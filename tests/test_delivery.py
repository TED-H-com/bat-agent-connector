"""GitHub delivery operations against a fake GitHub (plan acceptance C04-C07, D01-D04)."""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace

import pytest

from bat_agent_connector import api_auth, delivery, deployment, deployment_store, pr_delivery
from bat_agent_connector.config import parse_config
from bat_agent_connector.github import GitHubClient
from bat_agent_connector.operations import OperationError, OperationService
from bat_agent_connector.task_daemon import TaskDaemon
from tests.fakegithub import TOKEN, FakeGitHub
from tests.fakeverifier import FakeVerifier
from tests.operation_helpers import settle_operations
from tests.test_journal_compatibility import snapshot as journal_snapshot

HEAD = "a" * 40
MERGED = "9" * 40
OPERATOR = api_auth.Principal("operator-dashboard", frozenset({"observe", "merge", "deploy", "integrate"}))


@pytest.fixture
def gh():
    fake = FakeGitHub()
    fake.start()
    yield fake
    fake.stop()


def config(mock, gh, *, mode="workflow_dispatch"):
    host = {"url": mock.url, "fingerprint": mock.fingerprint, "token_ref": "env:BATC_TEST_TOKEN"}
    return parse_config({
        "hosts": {"h1": host},
        "github": {"token_ref": "env:FAKE_GH_TOKEN", "api_url": gh.url, "wait_max_s": 600,
                   "repos": [{"repository": "o/r", "merge_methods": ["squash", "merge", "rebase"], "allow_pr_update": True}]},
        "deploy": {"recipes": [
            {"name": "prod", "repository": "o/r", "environment": "production", "mode": mode,
             "workflow": "deploy.yml", "deploy_job": "deploy", "ref": "main",
             "inputs": {"source_sha": "source_sha", "operation_id": "operation_id"},
             "verification": {"kind": "http_json", "url": "https://deployment.example/status",
                              "version_required": True, "health_required": True},
             "run_name_contains": "operation_id"}]},
    })


@pytest.fixture
def make_daemon(mock, gh, tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_GH_TOKEN", TOKEN)
    made = []

    def make(**kw):
        d = TaskDaemon(config(mock, gh, **kw), tmp_path / f"tasks{len(made)}.db")
        d.ops.context["deployment_verifier"] = FakeVerifier(gh)
        gh.commits.setdefault(MERGED, {"sha": MERGED, "parents": [{"sha": "b" * 40}]})
        d.ops.context["deployment_test_github"] = gh
        # Deploy-only tests previously assumed this fixed source was already on main.
        key = deployment_store.environment_key(gh.url, gh.repository_id, "production")
        d.journal.db.execute("INSERT INTO deployment_environments "
                            "(environment_key,provider_origin,repository_id,repository,environment,updated_at) "
                            "VALUES(?,?,?,'o/r','production',0)", (key, gh.url, gh.repository_id))
        made.append(d)
        return d

    yield make
    for d in made:
        d.journal.close()


async def merge_op(d, key="m1", sha=HEAD, number=7, principal=OPERATOR, action="github.pr.merge", **target):
    if (len(sha) != 40 or not principal.allows("merge") or not d.ops.context.get("github")
            or (action == "delivery.merge_and_deploy" and not principal.allows("deploy"))):
        return d.ops.create(principal, action=action, target={"repository": "o/r", "pull_number": number, **target},
                            params={"method": "squash"}, preconditions={"expected_head_sha": sha}, idempotency_key=key)
    preview = (await delivery.pr_preview(d.ops, "o/r", number))["merge_preview"]
    envelope = pr_delivery.merge_envelope(preview, recipe=target.get("recipe"))
    envelope["action"] = action
    envelope["target"].update(target)
    if action == "delivery.merge_and_deploy":
        envelope["preconditions"].update((await deployment.preview(d.ops, target["recipe"]))["preconditions"])
    return d.ops.create(principal, **envelope, idempotency_key=key)


async def settle(d, op_id, rounds=10):
    """Run the operation, fast-forwarding its scheduled waits, until it stops changing."""
    for _ in range(rounds):
        d.journal.db.execute("UPDATE operations SET next_run_at=0 WHERE operation_id=?", (op_id,))
        # Wait for the persisted status and worker completion before sampling.
        await settle_operations(d.ops)
        op = d.ops.get(op_id)
        if op["status"] in {"succeeded", "failed", "cancelled", "needs_attention"}:
            return op
    return d.ops.get(op_id)


# --------------------------------------------------------------------------- merge
async def test_merge_records_the_real_merged_sha(make_daemon, gh):
    d = make_daemon()
    gh.add_pr(7, HEAD)
    op, _ = await merge_op(d)
    done = await settle(d, op["operation_id"])
    assert done["status"] == "succeeded", done
    assert done["result"]["merged_sha"] == MERGED and done["result"]["via"] == "direct"
    put = [b for m, p, b in gh.requests if m == "PUT"]
    assert put == [{"sha": HEAD, "merge_method": "squash", "merge_action": "default"}]
    assert done["external_refs"]["merge_request_uuid"]
    assert TOKEN not in json.dumps(done)


async def test_moved_head_is_refused_before_any_merge_request(make_daemon, gh):
    d = make_daemon()
    gh.add_pr(7, HEAD)
    op, _ = await merge_op(d)
    gh.pulls[7]["head"]["sha"] = "e" * 40
    done = await settle(d, op["operation_id"])
    assert done["status"] == "failed" and done["error_code"] == "TARGET_HEAD_CHANGED"
    assert gh.count("PUT", "merge-async") == 0


async def test_merge_queue_waits_until_github_shows_the_merge(make_daemon, gh):
    d = make_daemon()
    gh.add_pr(7, HEAD)
    gh.merge_mode = "enqueue"
    op, _ = await merge_op(d)
    waiting = await settle(d, op["operation_id"], rounds=2)
    assert waiting["status"] == "waiting_external" and "merge queue" in waiting["status_reason"]
    assert waiting["external_refs"]["merge_queue"] is True
    gh.merge(7)  # the queue merges it later
    done = await settle(d, op["operation_id"])
    assert done["status"] == "succeeded" and done["result"]["via"] == "merge_queue"
    assert gh.count("PUT", "merge-async") == 1  # enqueued is never re-submitted


async def test_existing_merge_request_is_adopted_only_when_it_matches(make_daemon, gh):
    d = make_daemon()
    gh.add_pr(7, HEAD)
    gh.merge_mode = "conflict_409_same"
    op, _ = await merge_op(d)
    done = await settle(d, op["operation_id"])
    assert done["status"] == "succeeded"
    gh.add_pr(8, HEAD)
    gh.merge_mode = "conflict_409_other"
    op, _ = await merge_op(d, key="m2", number=8)
    other = await settle(d, op["operation_id"])
    assert other["status"] == "needs_attention" and other["error_code"] == "EXISTING_MERGE_REQUEST"
    assert gh.pulls[8]["merged"] is False


async def test_lost_merge_reply_is_read_back_not_blindly_resent(make_daemon, gh):
    d = make_daemon()
    gh.add_pr(7, HEAD)
    gh.merge_mode = "fail_500_but_merged"  # GitHub merged it, the reply was lost
    op, _ = await merge_op(d)
    first = await settle(d, op["operation_id"], rounds=1)
    assert first["status"] == "uncertain"
    done = await settle(d, op["operation_id"])
    assert done["status"] == "succeeded" and done["result"]["merged_sha"] == MERGED
    assert gh.count("PUT", "merge-async") == 1
    gh.add_pr(8, HEAD)
    gh.merge_mode = "fail_500"  # nothing happened: the read-back proves it, so asking again is safe
    op, _ = await merge_op(d, key="m2", number=8)
    done = await settle(d, op["operation_id"])
    assert done["status"] == "succeeded" and gh.count("PUT", "pulls/8/merge-async") == 2


async def test_pending_required_checks_wait_without_merging(make_daemon, gh):
    d = make_daemon()
    gh.add_pr(7, HEAD, mergeable_state="blocked")
    gh.check_runs[HEAD] = [{"name": "ci", "status": "in_progress", "conclusion": None}]
    op, _ = await merge_op(d)
    waiting = await settle(d, op["operation_id"], rounds=2)
    assert waiting["status"] == "waiting_checks" and gh.count("PUT", "merge-async") == 0
    gh.check_runs[HEAD] = [{"name": "ci", "status": "completed", "conclusion": "success"}]
    gh.result_status = "failed"  # rules are evaluated when the merge runs
    done = await settle(d, op["operation_id"])
    assert done["status"] == "needs_attention" and done["error_code"] == "MERGE_FAILED"


async def test_merge_admission(make_daemon, gh, monkeypatch):
    d = make_daemon()
    for kwargs, code in (({"sha": "short"}, "PRECONDITION_REQUIRED"),
                         ({"principal": api_auth.Principal("bot", frozenset({"operate"}))}, "FORBIDDEN"),
                         ({"action": "delivery.merge_and_deploy", "recipe": "prod",
                           "principal": api_auth.Principal("bot", frozenset({"merge"}))}, "FORBIDDEN")):
        with pytest.raises(OperationError) as e:
            await merge_op(d, **kwargs)
        assert e.value.code == code
    with pytest.raises(OperationError) as e:
        d.ops.create(OPERATOR, action="github.pr.merge", target={"repository": "x/y", "pull_number": 1},
                     preconditions={"expected_head_sha": HEAD}, idempotency_key="k")
    assert e.value.code == "REPO_NOT_CONFIGURED"
    # A09: configuration variants run sequentially; they cannot both own this fleet.
    await d.fleet.close()
    d.journal.close()
    monkeypatch.delenv("FAKE_GH_TOKEN")
    nogh = make_daemon()
    with pytest.raises(OperationError) as e:
        await merge_op(nogh)
    assert e.value.status == 503


# --------------------------------------------------------------------------- deploy
def deploy_op(d, sha=MERGED, key="d1"):
    fake = d.ops.context["deployment_test_github"]
    if not fake.pulls:
        fake.branches["main"] = MERGED
    r = d.ops.context["github_config"].recipes["prod"]
    env = deployment.bound_environment(d.ops, r)
    return d.ops.create(OPERATOR, action="deployment.start", target={"recipe": "prod"}, params={"source_sha": sha},
                        preconditions={"expected_environment_generation": env["desired_generation"],
                                       "expected_recipe_digest": deployment.recipe_digest(d.ops, r, env["repository_id"])},
                        idempotency_key=key)


async def test_dispatch_deploy_succeeds_only_when_the_deploy_job_succeeds(make_daemon, gh):
    d = make_daemon()
    op, _ = deploy_op(d)
    waiting = await settle(d, op["operation_id"], rounds=2)
    assert waiting["status"] == "waiting_external"
    dispatched = [b for m, p, b in gh.requests if m == "POST"]
    assert dispatched == [{"ref": "main", "inputs": {"operation_id": op["operation_id"], "source_sha": MERGED}}]
    run_id = waiting["external_refs"]["deploy_run_id"]
    gh.runs[run_id].update(status="completed", conclusion="success")
    gh.jobs[run_id][1]["conclusion"] = "skipped"
    skipped = await settle(d, op["operation_id"])
    assert skipped["status"] == "failed" and skipped["error_code"] == "DEPLOY_NOT_RUN"
    op, _ = deploy_op(d, key="d2")
    await settle(d, op["operation_id"], rounds=2)
    run_id = d.ops.get(op["operation_id"])["external_refs"]["deploy_run_id"]
    gh.runs[run_id].update(status="completed", conclusion="success")
    gh.jobs[run_id][1]["conclusion"] = "success"
    done = await settle(d, op["operation_id"])
    assert done["status"] == "succeeded" and done["result"]["environment"] == "production"
    assert done["result"]["source_sha"] == MERGED and done["result"]["run_id"] == run_id


async def test_lost_dispatch_reply_is_matched_by_run_name_not_redispatched(make_daemon, gh):
    d = make_daemon()
    gh.dispatch_mode = "fail_500_but_started"
    op, _ = deploy_op(d)
    first = await settle(d, op["operation_id"], rounds=1)
    assert first["status"] == "uncertain"
    found = await settle(d, op["operation_id"], rounds=2)
    assert found["status"] == "waiting_external" and found["external_refs"]["deploy_run_id"]
    assert gh.count("POST", "dispatches") == 1


async def test_on_merge_recipe_tracks_the_existing_run_and_serializes_the_environment(make_daemon, gh):
    d = make_daemon(mode="on_merge")
    op, _ = deploy_op(d)
    waiting = await settle(d, op["operation_id"], rounds=2)
    assert waiting["status"] == "waiting_external" and "start" in waiting["status_reason"]
    with pytest.raises(OperationError) as e:
        deploy_op(d, key="d2")  # one deploy per environment at a time
    assert e.value.status == 409
    gh.add_run(head_sha=MERGED, event="push", status="completed", conclusion="success", job_conclusion="success")
    done = await settle(d, op["operation_id"])
    assert done["status"] == "succeeded" and gh.count("POST", "dispatches") == 0


async def test_merge_and_deploy_keeps_the_merge_when_the_deploy_fails(make_daemon, gh):
    d = make_daemon()
    gh.add_pr(7, HEAD)
    op, _ = await merge_op(d, action="delivery.merge_and_deploy", recipe="prod")
    waiting = await settle(d, op["operation_id"], rounds=3)
    assert waiting["status"] == "waiting_external" and waiting["external_refs"]["merged_sha"] == MERGED
    run_id = waiting["external_refs"]["deploy_run_id"]
    assert [b for m, p, b in gh.requests if m == "POST"][0]["inputs"]["source_sha"] == MERGED
    gh.runs[run_id].update(status="completed", conclusion="failure")
    failed = await settle(d, op["operation_id"])
    assert failed["status"] == "failed" and failed["error_code"] == "DEPLOY_FAILED"
    # Retry deploys the same merged version; nothing is merged again.
    retry, _ = deploy_op(d, sha=failed["external_refs"]["merged_sha"], key="retry")
    await settle(d, retry["operation_id"], rounds=2)
    rid = d.ops.get(retry["operation_id"])["external_refs"]["deploy_run_id"]
    gh.runs[rid].update(status="completed", conclusion="success")
    gh.jobs[rid][1]["conclusion"] = "success"
    assert (await settle(d, retry["operation_id"]))["status"] == "succeeded"
    assert gh.count("PUT", "merge-async") == 1


# --------------------------------------------------------------------------- GitHub answers that are not outcomes
async def test_token_is_read_per_request_and_a_refused_read_after_the_merge_request_waits(make_daemon, gh,
                                                                                          monkeypatch):
    d = make_daemon()
    gh.add_pr(7, HEAD)
    gh.merge_mode = "enqueue"
    op, _ = await merge_op(d)
    assert (await settle(d, op["operation_id"], rounds=2))["status"] == "waiting_external"
    gh.token = "rotated-token-1"
    monkeypatch.setenv("FAKE_GH_TOKEN", gh.token)  # a refresher rewrote the token; the daemon did not restart
    assert (await settle(d, op["operation_id"], rounds=2))["status"] == "waiting_external"
    gh.token = "rotated-token-2"  # this time nothing refreshed the connector's copy
    held = await settle(d, op["operation_id"])
    assert held["status"] == "needs_attention" and held["error_code"] == "GITHUB_401", held
    monkeypatch.setenv("FAKE_GH_TOKEN", gh.token)
    gh.merge(7)  # the queue merged it meanwhile
    d.ops.resume(OPERATOR, op["operation_id"])
    done = await settle(d, op["operation_id"])
    assert done["status"] == "succeeded" and done["result"]["via"] == "merge_queue"
    assert gh.count("PUT", "merge-async") == 1


async def test_a_token_missing_mid_rotation_still_sends_the_dispatch_with_the_last_good_one(make_daemon, gh,
                                                                                            monkeypatch):
    d = make_daemon()
    d.acquire_owner()
    monkeypatch.delenv("FAKE_GH_TOKEN")  # the refresher is rewriting it
    op, _ = deploy_op(d)
    sent = await settle(d, op["operation_id"], rounds=1)
    assert sent["status"] == "waiting_external" and sent["external_refs"]["deploy_run_id"], sent
    assert gh.count("POST", "dispatches") == 1


async def test_a_refused_read_before_any_write_still_fails_fast(make_daemon, gh):
    d = make_daemon()
    gh.add_pr(8, HEAD)
    op, _ = await merge_op(d, number=8)
    gh.pulls.pop(8)  # reviewed preview was admitted; PR is no longer readable before the first write
    done = await settle(d, op["operation_id"])
    assert done["status"] == "failed" and done["error_code"] == "GITHUB_404"
    assert gh.count("PUT", "merge-async") == 0


async def test_a_rate_limited_poll_keeps_the_deploy_running_and_the_environment_locked(make_daemon, gh):
    d = make_daemon()
    op, _ = deploy_op(d)
    run_id = (await settle(d, op["operation_id"], rounds=2))["external_refs"]["deploy_run_id"]
    gh.script.append(("GET", rf"/actions/runs/{run_id}$", 403, {"x-ratelimit-remaining": "0"},
                      {"message": "API rate limit exceeded for installation"}))
    limited = await settle(d, op["operation_id"], rounds=1)
    assert limited["status"] == "waiting_external" and "did not answer" in limited["status_reason"], limited
    with pytest.raises(OperationError) as e:
        deploy_op(d, key="d2")
    assert e.value.code == "DEPLOY_IN_PROGRESS"
    gh.runs[run_id].update(status="completed", conclusion="success")
    gh.jobs[run_id][1]["conclusion"] = "success"
    assert (await settle(d, op["operation_id"]))["status"] == "succeeded"


async def test_an_unanswered_run_lookup_after_a_204_dispatch_keeps_looking(make_daemon, gh):
    d = make_daemon()
    gh.dispatch_mode = "no_content"
    gh.script.append(("GET", r"/workflows/deploy\.yml/runs$", 502, {}, {"message": "Bad Gateway"}))
    op, _ = deploy_op(d)
    first = await settle(d, op["operation_id"], rounds=1)
    assert first["status"] == "waiting_external", first
    found = await settle(d, op["operation_id"], rounds=1)
    assert found["status"] == "waiting_external" and found["external_refs"]["deploy_run_id"]
    assert gh.count("POST", "dispatches") == 1


async def test_a_dispatch_reply_cut_short_is_read_back_not_failed(make_daemon, gh):
    d = make_daemon()
    gh.truncate.append(("POST", r"/dispatches$"))
    op, _ = deploy_op(d)
    first = await settle(d, op["operation_id"], rounds=1)
    assert first["status"] == "uncertain", first
    found = await settle(d, op["operation_id"], rounds=2)
    assert found["status"] == "waiting_external" and found["external_refs"]["deploy_run_id"]
    assert gh.count("POST", "dispatches") == 1


async def test_pr_preview_route(make_daemon, gh):
    d = make_daemon()
    gh.add_pr(7, HEAD)
    gh.check_runs[HEAD] = [{"name": "ci", "status": "completed", "conclusion": "success"},
                           {"name": "lint", "status": "queued", "conclusion": None}]
    server = await asyncio.start_server(d._handle, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    with d.journal.tx():
        viewer = api_auth.issue(d.journal.db, "viewer", ["observe"])
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    writer.write((f"GET /api/v1/repositories/o/r/pulls/7 HTTP/1.1\r\nHost: localhost\r\n"
                  f"Authorization: Bearer {viewer}\r\n\r\n").encode())
    await writer.drain()
    raw = await reader.read()
    writer.close()
    body = json.loads(raw.split(b"\r\n\r\n", 1)[1])
    pr = body["pull_request"]
    assert pr["head_sha"] == HEAD and pr["checks"] == {"total": 2, "pending": 1, "failed": 0}
    assert pr["recipes"] == [{"name": "prod", "environment": "production", "mode": "workflow_dispatch"}]
    server.close()
    await server.wait_closed()


async def preview_op(d, *, method="squash", key="preview-merge", action="github.pr.merge", recipe=None):
    doc = (await delivery.pr_preview(d.ops, "o/r", 7, method))["merge_preview"]
    envelope = pr_delivery.merge_envelope(doc, recipe=recipe)
    envelope["action"] = action
    if recipe:
        envelope["preconditions"].update((await deployment.preview(d.ops, recipe))["preconditions"])
    return doc, d.ops.create(OPERATOR, **envelope, idempotency_key=key)[0]


async def test_c05_base_changed_before_submit_has_diff_and_zero_put(make_daemon, gh):
    d = make_daemon()
    gh.add_pr(7, HEAD)
    doc, op = await preview_op(d)
    gh.pulls[7]["base"]["sha"] = "c" * 40
    done = await settle(d, op["operation_id"])
    assert done["error_code"] == "TARGET_BASE_CHANGED" and done["status"] == "failed"
    assert done["external_refs"]["scope_difference"] == {
        "field": "base_sha", "reviewed": doc["target"]["base_sha"], "observed": "c" * 40}
    assert gh.count("PUT", "merge-async") == 0


@pytest.mark.parametrize("method", ["squash", "merge"])
async def test_c05_queue_newer_base_verifies_actual_merge(make_daemon, gh, method):
    d = make_daemon()
    gh.add_pr(7, HEAD)
    gh.merge_mode = "enqueue"
    doc, op = await preview_op(d, method=method)
    waiting = await settle(d, op["operation_id"], 2)
    assert waiting["status"] == "waiting_external"
    newer = "c" * 40
    gh.commits[newer] = {"sha": newer, "parents": [{"sha": "b" * 40}], "commit": {"message": "another PR"}}
    gh.branches["main"] = newer
    gh.merge(7)
    done = await settle(d, op["operation_id"])
    assert done["status"] == "succeeded", done
    assert done["result"]["merged_sha"] == MERGED and done["result"]["verified"]
    assert done["result"]["merged_onto_base_sha"] == newer and done["result"]["base_moved"]
    assert done["result"]["other_commits_count"] == 1
    assert gh.count("PUT", "merge-async") == 1
    assert pr_delivery.get_preview(d.journal.db, doc["preview_id"]) == doc


async def test_c05_newer_base_combined_deploy_uses_actual_sha(make_daemon, gh):
    d = make_daemon()
    gh.add_pr(7, HEAD)
    gh.merge_mode = "enqueue"
    _, op = await preview_op(d, action="delivery.merge_and_deploy", recipe="prod")
    await settle(d, op["operation_id"], 1)
    gh.commits["c" * 40] = {"sha": "c" * 40, "parents": [{"sha": "b" * 40}], "commit": {}}
    gh.branches["main"] = "c" * 40
    gh.merge(7)
    waiting = await settle(d, op["operation_id"], 1)
    assert waiting["external_refs"]["merged_sha"] == MERGED
    payload = [b for m, _, b in gh.requests if m == "POST"]
    assert payload[0]["inputs"]["source_sha"] == MERGED
    assert gh.count("PUT", "merge-async") == 1


@pytest.mark.parametrize("native", [True, False])
async def test_c04_native_stack_and_branch_chain_are_listed_and_refused(make_daemon, gh, native):
    d = make_daemon()
    lower = gh.add_pr(6, "c" * 40, head_ref="lower")
    upper = gh.add_pr(7, HEAD)
    upper["base"].update(ref="lower", sha="c" * 40)
    gh.commits[HEAD]["parents"] = [{"sha": "c" * 40}]
    if native:
        gh.stacks[1] = {"number": 1, "pull_requests": [lower, upper]}
    doc, op = await preview_op(d)
    assert doc["affected_prs"][0]["number"] == 6
    assert doc["affected_prs"][0]["reason"] == ("native_stack" if native else "branch_chain")
    done = await settle(d, op["operation_id"])
    assert done["error_code"] == "STACKED_PR_UNSUPPORTED" and done["status"] == "needs_attention"
    assert gh.count("PUT", "merge-async") == 0


async def test_c04_indirect_merge_differs_by_method_and_shared_ancestor(make_daemon, gh):
    d = make_daemon()
    gh.add_pr(6, "c" * 40)
    gh.add_pr(7, HEAD)
    gh.commits[HEAD]["parents"] = [{"sha": "c" * 40}]
    merge = (await delivery.pr_preview(d.ops, "o/r", 7, "merge"))["merge_preview"]
    squash = (await delivery.pr_preview(d.ops, "o/r", 7, "squash"))["merge_preview"]
    assert merge["blocking"][0]["code"] == "MERGE_SCOPE_EXPANDED"
    assert not squash["blocking"] and squash["affected_prs"][0]["would_merge"] is False
    gh.pulls[6]["head"]["sha"] = "b" * 40
    shared = (await delivery.pr_preview(d.ops, "o/r", 7, "merge"))["merge_preview"]
    assert not shared["affected_prs"] and not shared["blocking"]


async def test_c05_swept_downstack_fails_verification_and_never_dispatches(make_daemon, gh):
    d = make_daemon()
    gh.add_pr(6, "c" * 40)
    gh.add_pr(7, HEAD)
    gh.commits[HEAD]["parents"] = [{"sha": "c" * 40}]
    gh.merge_mode = "enqueue"
    _, op = await preview_op(d, action="delivery.merge_and_deploy", recipe="prod")
    await settle(d, op["operation_id"], 1)
    gh.merge(7)
    gh.pulls[6].update(merged=True, state="closed", merge_commit_sha=MERGED)
    done = await settle(d, op["operation_id"])
    assert done["status"] == "needs_attention" and done["error_code"] == "MERGE_RESULT_SCOPE_CHANGED"
    assert done["external_refs"]["merged_sha"] == MERGED
    assert gh.count("POST", "dispatches") == 0 and gh.count("PUT", "merge-async") == 1


async def test_c04_complete_compare_pagination_over_250_commits(make_daemon, gh):
    d = make_daemon()
    last = "b" * 40
    for i in range(301):
        sha = f"{i + 1000:040x}"
        gh.commits[sha] = {"sha": sha, "parents": [{"sha": last}], "commit": {"message": str(i)}}
        last = sha
    gh.add_pr(7, last)
    doc = (await delivery.pr_preview(d.ops, "o/r", 7))["merge_preview"]
    assert len(doc["commits"]) == 301 and doc["commits"][-1]["sha"] == last
    assert not doc["blocking"]
    assert gh.count("GET", "compare/.*page=4") == 1


async def test_c04_old_merge_client_requires_saved_preview(make_daemon, gh):
    d = make_daemon()
    with pytest.raises(OperationError) as e:
        d.ops.create(OPERATOR, action="github.pr.merge", target={"repository": "o/r", "pull_number": 7},
                     preconditions={"expected_head_sha": HEAD}, idempotency_key="old")
    assert e.value.code == "PRECONDITION_REQUIRED" and "github_pr_preview" in e.value.message
    assert not gh.requests


async def update_op(d, *, params=None, key="metadata", principal=OPERATOR, expected=None):
    pr = (await delivery.pr_preview(d.ops, "o/r", 7))
    return d.ops.create(principal, action="github.pr.update", target={"repository": "o/r", "pull_number": 7},
                        params=params or {"title": "Reviewed title"}, idempotency_key=key,
                        preconditions={"expected_metadata_digest": expected or pr["metadata_digest"]})[0]


async def test_metadata_human_only_pr_preserves_body_and_clears_explicitly(make_daemon, gh):
    d = make_daemon()
    gh.add_pr(7, HEAD, body="Human work\n\n[notes](https://example.com)\n")
    op = await update_op(d)
    done = await settle(d, op["operation_id"])
    assert done["status"] == "succeeded" and done["result"]["verified"]
    assert gh.pulls[7]["body"].endswith("\n") and gh.pulls[7]["title"] == "Reviewed title"
    second = await update_op(d, params={"body": ""}, key="clear")
    assert (await settle(d, second["operation_id"]))["status"] == "succeeded"
    assert gh.pulls[7]["body"] == "" and gh.pulls[7]["title"] == "Reviewed title"
    assert [b for m, _, b in gh.requests if m == "PATCH"] == [{"title": "Reviewed title"}, {"body": ""}]
    assert not gh.count("PUT", ".") and not gh.count("POST", ".")
    assert "Human work" not in json.dumps(d.journal.api_events(0, 200))


async def test_metadata_stale_digest_and_last_read_race_send_zero_patch(make_daemon, gh):
    d = make_daemon()
    gh.add_pr(7, HEAD)
    op = await update_op(d)
    gh.pulls[7]["body"] = "other person's edit"
    done = await settle(d, op["operation_id"])
    assert done["error_code"] == "PR_METADATA_CHANGED" and gh.count("PATCH", ".") == 0
    op = await update_op(d, key="second")
    reads = 0
    def race(method, path, body):
        nonlocal reads
        if method == "GET" and path.endswith("pulls/7"):
            reads += 1
            if reads == 2:
                gh.pulls[7]["title"] = "another edit before PATCH"
    gh.before_request = race
    done = await settle(d, op["operation_id"])
    assert done["error_code"] == "PR_METADATA_CHANGED" and gh.count("PATCH", ".") == 0


@pytest.mark.parametrize("lost", [False, True])
async def test_metadata_write_readback_conflict_never_overwrites(make_daemon, gh, lost):
    d = make_daemon()
    gh.add_pr(7, HEAD)
    op = await update_op(d)
    gh.patch_after = lambda pr: pr.update(body="concurrent editor")
    gh.patch_mode = "lost_after" if lost else "ok"
    done = await settle(d, op["operation_id"])
    assert done["status"] == "needs_attention" and done["error_code"] == "PR_METADATA_CONFLICT"
    assert done["external_refs"]["metadata_difference"]["observed"]["body"] == "concurrent editor"
    assert gh.count("PATCH", ".") == 1
    d.ops.resume(OPERATOR, op["operation_id"])
    await settle(d, op["operation_id"])
    assert gh.count("PATCH", ".") == 1 and gh.pulls[7]["body"] == "concurrent editor"


@pytest.mark.parametrize("refused_status", [None, 401, 403, 404])
async def test_metadata_acknowledged_write_conflict_settles_and_releases_pr(make_daemon, gh, refused_status):
    """C07, plan §09/§10/§15: a completed ACK readback releases admission while retaining the conflict audit."""
    d = make_daemon()
    gh.add_pr(7, HEAD, body="Original body")
    op = await update_op(d)
    def concurrent_edit(pr):
        pr.update(body="Concurrent editor's body")
        if refused_status:
            gh.script.append(("GET", r"/pulls/7$", refused_status, {}, {"message": "read permission unavailable"}))
    gh.patch_after = concurrent_edit
    started = pr_delivery.time.time()
    done = await settle(d, op["operation_id"])
    if refused_status:
        assert done["status"] == "needs_attention" and done["error_code"] == f"GITHUB_{refused_status}"
        assert done["external_refs"]["write_acknowledged"] and done["external_refs"]["verification_pending"]
        assert pr_delivery.metadata_settlement(d.ops, op["operation_id"]) is None
        with pytest.raises(OperationError) as exc:
            await update_op(d, key="readback-still-pending")
        assert exc.value.code == "PR_UPDATE_IN_PROGRESS" and gh.count("PATCH", ".") == 1
        d.ops.resume(OPERATOR, op["operation_id"])
        done = await settle(d, op["operation_id"])
    assert done["status"] == "needs_attention" and done["error_code"] == "PR_METADATA_CONFLICT"
    observed = {"title": "Reviewed title", "body": "Concurrent editor's body"}
    receipt = pr_delivery.metadata_settlement(d.ops, op["operation_id"])
    assert receipt["status"] == "conflict" and receipt["code"] == "PR_METADATA_CONFLICT"
    assert receipt["observed"] == observed and started <= receipt["settled_at"] <= pr_delivery.time.time()
    refs = done["external_refs"]
    assert refs["write_acknowledged"] and refs["verification_pending"] is False
    assert refs["metadata_reconciliation"] == "PR_METADATA_CONFLICT"
    assert refs["metadata_difference"]["before"] == {"title": "PR 7", "body": "Original body"}
    assert refs["metadata_difference"]["after"] == {"title": "Reviewed title", "body": "Original body"}
    assert refs["metadata_difference"]["observed"] == observed
    assert all(s["status"] == "succeeded" for s in done["steps"])
    assert gh.count("PATCH", ".") == 1
    requests = len(gh.requests)
    await pr_delivery.reconcile_metadata(d.ops)
    assert len(gh.requests) == requests

    gh.patch_after = None
    fresh = await update_op(d, key="fresh-after-ack-conflict", params={"body": "Freshly reviewed body"})
    assert fresh["status"] == "accepted"
    assert fresh["preconditions"]["expected_metadata_digest"] == pr_delivery.digest(observed)
    assert (await settle(d, fresh["operation_id"]))["status"] == "succeeded"
    assert d.ops.get(op["operation_id"])["status"] == "needs_attention"
    assert [b for m, _, b in gh.requests if m == "PATCH"] == [
        {"title": "Reviewed title"}, {"body": "Freshly reviewed body"}]

    restart_delivery_service(d)
    requests = len(gh.requests)
    d.ops.resume(OPERATOR, op["operation_id"])
    held = await settle(d, op["operation_id"])
    assert held["status"] == "needs_attention" and held["error_code"] == "PR_METADATA_CONFLICT"
    assert held["external_refs"] == refs and held["steps"] == done["steps"]
    await pr_delivery.reconcile_metadata(d.ops)
    assert len(gh.requests) == requests
    d.ops.cancel(OPERATOR, op["operation_id"])
    await pr_delivery.reconcile_metadata(d.ops)
    assert (await settle(d, op["operation_id"]))["status"] == "cancelled"
    assert len(gh.requests) == requests and pr_delivery.metadata_settlement(d.ops, op["operation_id"]) == receipt
    assert pr_delivery.metadata(gh.pulls[7]) == {"title": "Reviewed title", "body": "Freshly reviewed body"}


async def test_metadata_lost_reply_restart_and_cancel_never_resend(make_daemon, gh):
    d = make_daemon()
    gh.add_pr(7, HEAD)
    gh.patch_mode = "lost_after"
    op = await update_op(d)
    first = await settle(d, op["operation_id"], 1)
    assert first["status"] == "uncertain"
    # A new service on the same journal really replays saved step intent.
    from bat_agent_connector.operations import OperationService
    fresh = OperationService(d.journal, actions=delivery.ACTIONS)
    fresh.context.update(d.ops.context)
    d.ops = fresh
    done = await settle(d, op["operation_id"])
    assert done["status"] == "succeeded" and done["result"]["observed_intent"]
    assert gh.count("PATCH", ".") == 1
    gh.patch_mode = "lost_before"
    op = await update_op(d, params={"body": "late patch"}, key="late")
    assert (await settle(d, op["operation_id"], 2))["status"] == "uncertain"
    d.ops.cancel(OPERATOR, op["operation_id"])
    with pytest.raises(OperationError) as e:
        await update_op(d, key="cannot-overtake")
    assert e.value.code == "PR_UPDATE_IN_PROGRESS"
    await pr_delivery.reconcile_metadata(d.ops)
    assert gh.count("PATCH", ".") == 2  # before is not proof of no side effect
    gh.pulls[7]["body"] = "late patch"  # positive readback of the cancelled request
    await pr_delivery.reconcile_metadata(d.ops)
    assert d.ops.get(op["operation_id"])["external_refs"]["observed_intent"]
    assert (await settle(d, op["operation_id"]))["status"] == "cancelled"
    assert (await update_op(d, key="after-proof"))["status"] == "accepted"


async def test_c07_metadata_integrate_scope_opt_in_and_fields(make_daemon, gh):
    d = make_daemon()
    gh.add_pr(7, HEAD)
    for principal in [api_auth.Principal("observer", frozenset({"observe"})),
                      api_auth.Principal("merger", frozenset({"merge"}))]:
        with pytest.raises(OperationError) as e:
            await update_op(d, principal=principal)
        assert e.value.code == "FORBIDDEN"
    for params in [{"title": ""}, {"body": None}, {"links": []}, {"base": "other"}]:
        with pytest.raises(OperationError) as e:
            await update_op(d, params=params)
        assert e.value.code == "INVALID_PARAMS"
    from dataclasses import replace
    cfg = d.ops.context["github_config"]
    cfg.repos["o/r"] = replace(cfg.repos["o/r"], allow_pr_update=False)
    with pytest.raises(OperationError) as e:
        await update_op(d)
    assert e.value.code == "PR_UPDATE_DISABLED" and gh.count("PATCH", ".") == 0


async def test_c04_expired_request_uuid_reads_pr_without_resubmit(make_daemon, gh):
    d = make_daemon()
    gh.add_pr(7, HEAD)
    gh.result_status = "pending"
    _, op = await preview_op(d)
    assert (await settle(d, op["operation_id"], 1))["status"] == "waiting_external"
    gh.merge_requests.clear()
    assert (await settle(d, op["operation_id"], 1))["status"] == "waiting_external"
    gh.merge(7)
    assert (await settle(d, op["operation_id"]))["status"] == "succeeded"
    assert gh.count("PUT", "merge-async") == 1


async def test_c05_merge_restart_preserves_actual_sha_without_attribution(make_daemon, gh):
    from bat_agent_connector.operations import OperationService
    d = make_daemon()
    gh.add_pr(7, HEAD)
    gh.merge_mode = "fail_500_but_merged"
    _, op = await preview_op(d)
    assert (await settle(d, op["operation_id"], 1))["status"] == "uncertain"
    fresh = OperationService(d.journal, actions=delivery.ACTIONS)
    fresh.context.update(d.ops.context)
    d.ops = fresh
    done = await settle(d, op["operation_id"])
    assert done["status"] == "succeeded" and done["result"]["merged_sha"] == MERGED
    assert done["result"]["merged_by_this_operation"] is False
    assert all(s["status"] == "succeeded" for s in done["steps"])
    assert gh.count("PUT", "merge-async") == 1


def restart_delivery_service(d, **policy):
    cfg = d.ops.context["github_config"]
    cfg = replace(cfg, repos={**cfg.repos, "o/r": replace(cfg.repos["o/r"], **policy)})
    fresh = OperationService(d.journal, actions=delivery.ACTIONS)
    fresh.context.update(d.ops.context)
    fresh.context.update(github_config=cfg, github=GitHubClient(cfg))
    d.ops = fresh


async def default_merge_op(d, *, combined=False):
    cfg = d.ops.context["github_config"]
    cfg.repos["o/r"] = replace(cfg.repos["o/r"], default_merge_method="merge")
    doc = (await delivery.pr_preview(d.ops, "o/r", 7))["merge_preview"]
    envelope = pr_delivery.merge_envelope(doc, recipe="prod" if combined else None)
    if combined:
        envelope["preconditions"].update((await deployment.preview(d.ops, "prod"))["preconditions"])
    envelope["params"].pop("method")
    op = d.ops.create(OPERATOR, **envelope, idempotency_key="default-method")[0]
    assert doc["method"] == "merge" and "method" not in op["params"]
    return doc, op


@pytest.mark.parametrize("combined", [False, True])
async def test_c05_merge_method_pinned_to_preview_across_default_change(make_daemon, gh, combined):
    d = make_daemon()
    gh.add_pr(7, HEAD, mergeable_state="blocked")
    gh.check_runs[HEAD] = [{"status": "in_progress", "conclusion": None}]
    doc, op = await default_merge_op(d, combined=combined)
    assert (await settle(d, op["operation_id"], 1))["status"] == "waiting_checks"
    assert gh.count("PUT", ".") == 0
    restart_delivery_service(d, default_merge_method="squash")
    gh.check_runs[HEAD] = [{"status": "completed", "conclusion": "success"}]
    done = await settle(d, op["operation_id"], 1)
    if combined:
        assert done["status"] == "waiting_external"
        run_id = done["external_refs"]["deploy_run_id"]
        gh.runs[run_id].update(status="completed", conclusion="success")
        gh.jobs[run_id][1]["conclusion"] = "success"
        done = await settle(d, op["operation_id"])
    assert done["status"] == "succeeded", done
    result = done["result"]["merge"] if combined else done["result"]
    assert result["method"] == "merge" and result["verified"] and result["merged_sha"] == MERGED
    step = d.journal.db.execute("SELECT request FROM operation_steps WHERE operation_id=? AND name='merge.submit'",
                                (op["operation_id"],)).fetchone()
    assert json.loads(step["request"])["method"] == "merge"
    assert [b for m, _, b in gh.requests if m == "PUT"] == [
        {"sha": HEAD, "merge_method": "merge", "merge_action": "default"}]
    assert pr_delivery.get_preview(d.journal.db, doc["preview_id"]) == doc


@pytest.mark.parametrize("combined", [False, True])
@pytest.mark.parametrize("policy,code", [({"merge_methods": ("squash",)}, "INVALID_PARAMS"),
                                      ({"allow_merge": False}, "MERGE_DISABLED")])
async def test_c05_merge_method_removed_from_policy_stops_before_put(make_daemon, gh, combined, policy, code):
    d = make_daemon()
    gh.add_pr(7, HEAD, mergeable_state="blocked")
    gh.check_runs[HEAD] = [{"status": "in_progress", "conclusion": None}]
    _, op = await default_merge_op(d, combined=combined)
    assert (await settle(d, op["operation_id"], 1))["status"] == "waiting_checks"
    restart_delivery_service(d, default_merge_method="squash", **policy)
    gh.check_runs[HEAD] = [{"status": "completed", "conclusion": "success"}]
    done = await settle(d, op["operation_id"])
    assert done["status"] == "failed" and done["error_code"] == code
    assert not done["steps"]  # Policy refusal precedes the write intent, so it cannot become uncertain.
    assert gh.count("PUT", ".") == 0 and gh.count("POST", ".") == 0
    assert not gh.pulls[7]["merged"]


@pytest.mark.parametrize("combined", [False, True])
async def test_c05_explicit_merge_method_must_match_preview_at_admission(make_daemon, gh, combined):
    d = make_daemon()
    gh.add_pr(7, HEAD)
    doc = (await delivery.pr_preview(d.ops, "o/r", 7, "merge"))["merge_preview"]
    envelope = pr_delivery.merge_envelope(doc, recipe="prod" if combined else None)
    envelope["params"]["method"] = "squash"
    with pytest.raises(OperationError) as exc:
        d.ops.create(OPERATOR, **envelope, idempotency_key="wrong-method")
    assert exc.value.code == "PREVIEW_MISMATCH"
    assert gh.count("PUT", ".") == 0 and gh.count("POST", ".") == 0


@pytest.mark.parametrize("policy,code", [({"merge_methods": ("squash",)}, "INVALID_PARAMS"),
                                      ({"allow_merge": False}, "MERGE_DISABLED")])
async def test_c05_merge_policy_revoked_before_reconcile_never_resubmits(make_daemon, gh, policy, code):
    d = make_daemon()
    gh.add_pr(7, HEAD)
    gh.merge_mode = "fail_500"
    _, op = await default_merge_op(d)
    assert (await settle(d, op["operation_id"], 1))["status"] == "uncertain"
    restart_delivery_service(d, default_merge_method="squash", **policy)
    done = await settle(d, op["operation_id"])
    assert done["status"] == "needs_attention" and done["error_code"] == code
    assert gh.count("PUT", "merge-async") == 1 and not gh.pulls[7]["merged"]
    step = d.journal.db.execute("SELECT request FROM operation_steps WHERE operation_id=? AND name='merge.submit'",
                                (op["operation_id"],)).fetchone()
    assert json.loads(step["request"])["method"] == "merge"


@pytest.mark.parametrize("policy", [{"merge_methods": ("squash",)}, {"allow_merge": False}])
async def test_c05_merge_policy_change_after_acceptance_keeps_verifying(make_daemon, gh, policy):
    d = make_daemon()
    gh.add_pr(7, HEAD)
    gh.merge_mode = "enqueue"
    _, op = await default_merge_op(d)
    assert (await settle(d, op["operation_id"], 1))["status"] == "waiting_external"
    restart_delivery_service(d, default_merge_method="squash", **policy)
    gh.merge(7)
    done = await settle(d, op["operation_id"])
    assert done["status"] == "succeeded" and done["result"]["method"] == "merge"
    assert done["result"]["verified"] and gh.count("PUT", "merge-async") == 1


async def test_c04_native_bottom_lists_upper_rebase_and_refuses(make_daemon, gh):
    d = make_daemon()
    lower = gh.add_pr(7, HEAD)
    upper = gh.add_pr(8, "c" * 40)
    upper["base"].update(ref=lower["head"]["ref"], sha=HEAD)
    gh.stacks[1] = {"number": 1, "pull_requests": [lower, upper]}
    doc, op = await preview_op(d)
    assert doc["affected_prs"][0]["effect"] == "branch_rebase"
    assert doc["affected_prs"][0]["would_merge"] is False
    assert (await settle(d, op["operation_id"]))["error_code"] == "STACKED_PR_UNSUPPORTED"
    assert gh.count("PUT", ".") == 0


@pytest.mark.parametrize("rest_list_shape", [False, True])
async def test_c05_new_stack_during_acceptance_never_dispatches(make_daemon, gh, monkeypatch, rest_list_shape):
    d = make_daemon()
    gh.add_pr(7, HEAD)
    gh.merge_mode = "enqueue"
    _, op = await preview_op(d, action="delivery.merge_and_deploy", recipe="prod")
    await settle(d, op["operation_id"], 1)
    gh.merge(7)
    # A newly created downstack PR is no longer in the stack list after it was swept in.
    lower = gh.add_pr(6, HEAD)
    lower.update(merged=True, state="closed", merge_commit_sha=MERGED)
    if rest_list_shape:
        from datetime import datetime, timezone
        client = d.ops.context["github"]
        original = client.pulls
        async def rest_list(*args, **kw):
            status, body = await original(*args, **kw)
            # List pulls has merged_at, not the single-PR response's merged boolean; second precision.
            return status, {"items": [{k: v for k, v in p.items() if k != "merged"} | {
                "merged_at": datetime.fromtimestamp(int(op["created_at"]), timezone.utc).isoformat()}
                for p in body["items"]]}
        monkeypatch.setattr(client, "pulls", rest_list)
    done = await settle(d, op["operation_id"])
    assert done["error_code"] == "MERGE_RESULT_SCOPE_CHANGED"
    assert gh.count("POST", ".") == 0 and gh.count("PUT", ".") == 1


@pytest.mark.parametrize("merged_member", [False, True])
async def test_c05_stack_created_after_final_check_needs_attention_and_never_dispatches(make_daemon, gh,
                                                                                     merged_member):
    """C05, plan §16: new native membership also changes scope when the upper PR remains open."""
    d = make_daemon()
    gh.add_pr(7, HEAD)
    upper = gh.add_pr(8, "c" * 40)
    doc, op = await preview_op(d, action="delivery.merge_and_deploy", recipe="prod")
    assert not doc["stacks"] and not doc["blocking"]
    def create_stack(method, path, _):
        if method == "PUT" and path.endswith("/merge-async"):
            if merged_member:
                gh.merge(8, "8" * 40)  # independent merge on the newer base; native membership still matters
            else:
                upper["head"]["sha"] = "d" * 40
                upper["base"]["ref"] = "feature7"
            gh.stacks[1] = {"number": 1, "pull_requests": [
                {"number": 7, "state": "open"}, {"number": 8, "state": upper["state"]}]}
    gh.before_request = create_stack
    done = await settle(d, op["operation_id"])
    assert done["status"] == "needs_attention" and done["error_code"] == "MERGE_RESULT_SCOPE_CHANGED", done
    receipt = done["external_refs"]["merge_receipt"]
    members = [{"number": p["number"], "state": p["state"], "head_sha": p["head"]["sha"],
                "base_ref": p["base"]["ref"]} for p in (gh.pulls[7], upper)]
    assert done["external_refs"]["merged_sha"] == MERGED and receipt["merged_sha"] == MERGED
    assert receipt["preview_id"] == doc["preview_id"] and receipt["verified"] is False
    assert receipt["stacks"] == [{"number": 1, "members": members}]
    assert receipt["affected_prs"] == [members[1]]
    assert all(s["status"] != "failed" for s in done["steps"])
    assert gh.count("PUT", "merge-async") == 1 and gh.count("POST", ".") == 0


async def test_c04_scope_changes_or_missing_pages_never_submit(make_daemon, gh, monkeypatch):
    d = make_daemon()
    gh.add_pr(7, HEAD)
    _, op = await preview_op(d, method="merge")
    gh.add_pr(6, HEAD)
    done = await settle(d, op["operation_id"])
    assert done["error_code"] in {"MERGE_SCOPE_EXPANDED", "MERGE_SCOPE_CHANGED"} and gh.count("PUT", ".") == 0
    gh.pulls.pop(6)
    client = d.ops.context["github"]
    original = client.compare
    async def missing(repo, base, head, *, page=1):
        status, body = await original(repo, base, head, page=page)
        body["total_commits"] = 301
        if page > 1:
            body["commits"] = []
        return status, body
    monkeypatch.setattr(client, "compare", missing)
    doc = (await delivery.pr_preview(d.ops, "o/r", 7))["merge_preview"]
    assert doc["blocking"][0]["code"] == "MERGE_SCOPE_UNPROVEN"
    assert gh.count("PUT", ".") == 0


@pytest.mark.parametrize("status", [403, 404])
async def test_c04_stack_read_errors_are_not_empty_membership(make_daemon, gh, monkeypatch, status):
    d = make_daemon()
    gh.add_pr(7, HEAD)
    async def unavailable(*args, **kw):
        return status, {"message": "cannot read stacks"}
    monkeypatch.setattr(d.ops.context["github"], "stacks", unavailable)
    doc, op = await preview_op(d)
    assert doc["blocking"][0]["code"] == "MERGE_SCOPE_UNPROVEN"
    assert (await settle(d, op["operation_id"]))["error_code"] == "MERGE_SCOPE_UNPROVEN"
    assert gh.count("PUT", ".") == 0


async def test_c05_wrong_merge_parent_keeps_receipt_and_stops_deploy(make_daemon, gh):
    d = make_daemon()
    gh.add_pr(7, HEAD)
    gh.merge_mode = "enqueue"
    _, op = await preview_op(d, method="merge", action="delivery.merge_and_deploy", recipe="prod")
    await settle(d, op["operation_id"], 1)
    gh.merge(7)
    gh.commits[MERGED]["parents"][1]["sha"] = "f" * 40
    done = await settle(d, op["operation_id"])
    assert done["status"] == "needs_attention" and done["error_code"] == "MERGE_RESULT_UNVERIFIABLE"
    assert done["external_refs"]["merged_sha"] == MERGED and gh.count("POST", ".") == 0


async def test_merge_preview_expiry_only_before_first_submit(make_daemon, gh, monkeypatch):
    d = make_daemon()
    gh.add_pr(7, HEAD)
    doc, op = await preview_op(d)
    monkeypatch.setattr(pr_delivery.time, "time", lambda: doc["expires_at"] + 1)
    assert (await settle(d, op["operation_id"]))["error_code"] == "PREVIEW_EXPIRED"
    assert gh.count("PUT", ".") == 0
    monkeypatch.undo()
    gh.merge_mode = "enqueue"
    doc, op = await preview_op(d, key="queued")
    await settle(d, op["operation_id"], 1)
    gh.merge(7)
    monkeypatch.setattr(pr_delivery.time, "time", lambda: doc["expires_at"] + 1)
    assert (await settle(d, op["operation_id"]))["status"] == "succeeded"
    assert gh.count("PUT", ".") == 1


async def test_metadata_cancelled_unknown_write_stays_blocking_until_proven(make_daemon, gh):
    d = make_daemon()
    gh.add_pr(7, HEAD)
    gh.patch_mode = "lost_before"
    op = await update_op(d)
    unknown = await settle(d, op["operation_id"])
    assert unknown["status"] == "needs_attention" and unknown["error_code"] == "UNCERTAIN_UNRESOLVED"
    assert d.ops.cancel(OPERATOR, op["operation_id"])["status"] == "cancelled"
    await pr_delivery.reconcile_metadata(d.ops)
    with pytest.raises(OperationError) as e:
        await update_op(d, key="new")
    assert e.value.code == "PR_UPDATE_IN_PROGRESS"
    gh.pulls[7]["title"] = "Reviewed title"
    await pr_delivery.reconcile_metadata(d.ops)
    assert (await update_op(d, key="after-proof"))["status"] == "accepted"
    assert gh.count("PATCH", ".") == 1


@pytest.mark.parametrize("version", [1, 8])
def test_part_a_preview_migration_reopens_without_data_change(tmp_path, version):
    """Plan §09/§28: delivery DDL fills missing schema without taking a data-migration version."""
    from bat_agent_connector.task_journal import LATEST_DATA_STEP, Journal
    path = tmp_path / "old.db"
    j = Journal(path)
    j.db.execute("DROP TABLE pr_merge_previews")
    j.db.execute("DROP TABLE pr_metadata_settlements")
    j.db.execute("DROP TABLE pr_merge_scope_reads")
    j.db.execute("PRAGMA user_version=1" if version == 1 else "PRAGMA user_version=8")
    expected = {"pr_merge_previews": "table", "pr_merge_previews_pr": "index",
                "pr_metadata_settlements": "table", "pr_merge_scope_reads": "table"}
    schema_query = "SELECT name,type,sql FROM sqlite_master WHERE name IN (?,?,?,?) ORDER BY name"
    assert not j.db.execute(schema_query, tuple(expected)).fetchall()
    j.close()
    if version > LATEST_DATA_STEP:
        before = journal_snapshot(path)
        with pytest.raises(ValueError, match="JOURNAL_NEWER_DATA_STEP"):
            Journal(path)
        assert journal_snapshot(path) == before
        return
    j = Journal(path)
    assert j.db.execute("PRAGMA user_version").fetchone()[0] == max(version, LATEST_DATA_STEP)
    schema = j.db.execute(schema_query, tuple(expected)).fetchall()
    assert {r["name"]: r["type"] for r in schema} == expected
    doc = {"fixed": "scope"}
    preview = ("mpv_" + "0" * 32, "o/r", 7, json.dumps(doc), "digest", 1, 2)
    j.db.execute("INSERT INTO pr_merge_previews VALUES (?,?,?,?,?,?,?)", preview)
    j.close()
    j = Journal(path)
    assert j.db.execute("PRAGMA user_version").fetchone()[0] == max(version, LATEST_DATA_STEP)
    assert j.db.execute(schema_query, tuple(expected)).fetchall() == schema
    assert tuple(j.db.execute("SELECT * FROM pr_merge_previews").fetchone()) == preview
    assert pr_delivery.get_preview(j.db, preview[0]) == doc
    j.close()


async def tool_text(server, name, args):
    try:
        return str(await server.call_tool(name, args))
    except Exception as exc:  # direct MCP calls raise anticipated tool errors
        return str(exc)


async def test_c07_delivery_transports_share_actions_and_principals(make_daemon, gh, monkeypatch, tmp_path, capsys):
    from bat_agent_connector import cli
    from bat_agent_connector.mcp_server import build_server
    from tests.test_api_v1 import http, token
    d = make_daemon()
    gh.add_pr(7, HEAD)
    server = await asyncio.start_server(d._handle, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    own = token(d, "client", "observe", "integrate", "merge", "deploy")
    viewer = token(d, "viewer", "observe")
    monkeypatch.setenv("BATC_TASK_URL", f"http://127.0.0.1:{port}/rpc")
    monkeypatch.setenv("BATC_API_TOKEN", own)
    mcp, fleet = build_server(d.fleet.config)
    try:
        _, body = await http(port, "GET", "/api/v1/repositories/o/r/pulls/7?method=squash", tok=own)
        pr = body["pull_request"]
        doc = pr["merge_preview"]
        _, saved = await http(port, "GET", f"/api/v1/delivery/previews/{doc['preview_id']}", tok=own)
        assert saved["preview"] == doc
        envelope = {"action": "github.pr.update", "target": {"repository": "o/r", "pull_number": 7},
                    "params": {"title": "Across transports", "body": "Markdown\n"},
                    "preconditions": {"expected_metadata_digest": pr["metadata_digest"]}, "idempotency_key": "shared"}
        assert (await http(port, "POST", "/api/v1/operations", tok=viewer, body=envelope))[0] == 403
        _, out = await http(port, "POST", "/api/v1/operations", tok=own, body=envelope)
        op_id = out["operation"]["operation_id"]
        assert (await settle(d, op_id))["status"] == "succeeded"
        response = await tool_text(mcp, "github_pr_update", {"repository": "o/r", "pull_number": 7,
            "expected_metadata_digest": pr["metadata_digest"], "title": "Across transports", "body": "Markdown\n",
            "idempotency_key": "shared", "wait_s": 0, "confirm": True})
        assert op_id in str(response)
        body_file = tmp_path / "body.md"
        body_file.write_text("Markdown\n")
        assert await asyncio.to_thread(cli.main, ["delivery", "update-pr", "o/r", "7", "--metadata-digest",
            pr["metadata_digest"], "--title", "Across transports", "--body-file", str(body_file), "--key", "shared"]) == 0
        assert op_id in capsys.readouterr().out
        assert gh.count("PATCH", ".") == 1
        # New merge preview after the title change, then identical HTTP/MCP/CLI intent.
        _, body = await http(port, "GET", "/api/v1/repositories/o/r/pulls/7", tok=own)
        doc = body["pull_request"]["merge_preview"]
        envelope = {**pr_delivery.merge_envelope(doc), "idempotency_key": "merge-shared"}
        _, out = await http(port, "POST", "/api/v1/operations", tok=own, body=envelope)
        merge_id = out["operation"]["operation_id"]
        assert (await settle(d, merge_id))["status"] == "succeeded"
        response = await tool_text(mcp, "github_pr_merge", {"preview_id": doc["preview_id"],
            "idempotency_key": "merge-shared", "wait_s": 0, "confirm": True})
        assert merge_id in str(response)
        assert await asyncio.to_thread(cli.main, ["delivery", "merge", "--preview", doc["preview_id"], "--key", "merge-shared"]) == 0
        assert merge_id in capsys.readouterr().out
        assert gh.count("PUT", ".") == 1
        assert {o["actor"] for o in d.ops.list()["operations"]} == {"client"}
        assert (await http(port, "GET", f"/api/v1/delivery/previews/{doc['preview_id']}", tok=viewer))[0] == 200
        # An agent token cannot borrow the Dashboard's integrate/merge grants.
        monkeypatch.setenv("BATC_API_TOKEN", viewer)
        response = await tool_text(mcp, "github_pr_merge", {"preview_id": doc["preview_id"], "idempotency_key": "agent", "confirm": True})
        assert "FORBIDDEN" in str(response)
        assert await asyncio.to_thread(cli.main, ["delivery", "merge", "--preview", doc["preview_id"], "--key", "agent"]) == 1
        assert "FORBIDDEN" in capsys.readouterr().err
        _, caps = await http(port, "GET", "/api/v1/capabilities", tok=viewer)
        assert caps["contract_version"] == "2026-10-08"
        assert caps["features"]["merge_scope_preview"] and caps["features"]["metadata_update"]
        merge_only = token(d, "merge-only", "observe", "merge")
        _, caps = await http(port, "GET", "/api/v1/capabilities", tok=merge_only)
        assert not next(a["allowed"] for a in caps["actions"] if a["action"] == "delivery.merge_and_deploy")
    finally:
        await fleet.close()
        server.close()
        await server.wait_closed()


async def test_c07_delivery_wrappers_require_confirmation_and_token(make_daemon, monkeypatch):
    from bat_agent_connector.mcp_server import build_server
    d = make_daemon()
    mcp, fleet = build_server(d.fleet.config)
    monkeypatch.delenv("BATC_API_TOKEN", raising=False)
    try:
        for name, args in [("github_pr_merge", {"preview_id": "mpv_" + "0" * 32, "idempotency_key": "x"}),
                           ("github_pr_update", {"repository": "o/r", "pull_number": 7,
                            "expected_metadata_digest": "0" * 64, "idempotency_key": "x", "title": "title"})]:
            for confirm, message in [(False, "confirm=true"), (True, "BATC_API_TOKEN")]:
                result = await tool_text(mcp, name, {**args, "confirm": confirm})
                assert message in str(result)
        readonly, ro_fleet = build_server(d.fleet.config, read_only=True)
        assert not {"github_pr_merge", "github_pr_update"} & {t.name for t in await readonly.list_tools()}
        await ro_fleet.close()
    finally:
        await fleet.close()


@pytest.mark.parametrize("options", [{"merge_action": "bypass"}, {"merge_method": "default"},
                                    {"expected_head_sha": None}, {"merge_method": "merge"}])
async def test_c04_existing_request_requires_exact_options(make_daemon, gh, monkeypatch, options):
    d = make_daemon()
    gh.add_pr(7, HEAD)
    gh.merge_mode = "conflict_409_same"
    doc, op = await preview_op(d)
    client = d.ops.context["github"]
    original = client.merge_async
    async def different(*args):
        status, body = await original(*args)
        body["details"].update(options)
        return status, body
    monkeypatch.setattr(client, "merge_async", different)
    done = await settle(d, op["operation_id"])
    assert done["status"] == "needs_attention" and done["error_code"] == "EXISTING_MERGE_REQUEST"
    assert not gh.pulls[7]["merged"] and gh.count("PUT", ".") == 1
    assert doc["method"] == "squash"


async def test_c05_independently_merged_candidate_on_new_base_is_allowed(make_daemon, gh):
    d = make_daemon()
    gh.add_pr(6, "c" * 40)
    gh.add_pr(7, HEAD)
    gh.commits[HEAD]["parents"] = [{"sha": "c" * 40}]
    gh.merge_mode = "enqueue"
    _, op = await preview_op(d)
    await settle(d, op["operation_id"], 1)
    gh.merge(6, sha="d" * 40)
    gh.merge(7)
    done = await settle(d, op["operation_id"])
    assert done["status"] == "succeeded" and done["result"]["base_moved"]
    assert done["result"]["affected_prs"][0]["independent"]
    assert gh.count("PUT", ".") == 1


async def test_metadata_cancel_after_acknowledgement_retains_verified_effect(make_daemon, gh, monkeypatch):
    from bat_agent_connector.github import GitHubAmbiguous
    d = make_daemon()
    gh.add_pr(7, HEAD)
    op = await update_op(d)
    client = d.ops.context["github"]
    original = client.pull
    async def unavailable_after_write(*args):
        if gh.count("PATCH", "."):
            raise GitHubAmbiguous("readback unavailable")
        return await original(*args)
    monkeypatch.setattr(client, "pull", unavailable_after_write)
    waiting = await settle(d, op["operation_id"], 1)
    assert waiting["status"] == "uncertain" and waiting["external_refs"]["write_acknowledged"]
    assert waiting["external_refs"]["verification_pending"]
    d.ops.cancel(OPERATOR, op["operation_id"])
    monkeypatch.setattr(client, "pull", original)
    await pr_delivery.reconcile_metadata(d.ops)
    assert d.ops.get(op["operation_id"])["external_refs"]["observed_intent"]
    assert gh.count("PATCH", ".") == 1


async def test_c05_rebase_verifies_reviewed_head_and_actual_destination(make_daemon, gh):
    d = make_daemon()
    gh.add_pr(7, HEAD)
    source = "c" * 40
    gh.commits[source] = {"sha": source, "parents": [{"sha": "b" * 40}], "commit": {"message": "source one"}}
    gh.commits[HEAD]["parents"] = [{"sha": source}]
    gh.merge_mode = "enqueue"
    doc, op = await preview_op(d, method="rebase")
    await settle(d, op["operation_id"], 1)
    onto = "d" * 40
    gh.commits[onto] = {"sha": onto, "parents": [{"sha": "b" * 40}], "commit": {"message": "another merge"}}
    gh.branches["main"] = onto
    gh.merge(7)
    done = await settle(d, op["operation_id"])
    assert len(doc["commits"]) == 2
    assert done["status"] == "succeeded" and done["result"]["merged_onto_base_sha"] == onto
    assert done["result"]["base_moved"] and done["result"]["other_commits_count"] == 1
    assert gh.count("PUT", ".") == 1


async def test_c05_merge_verification_intent_precedes_reads_and_resume_is_read_only(make_daemon, gh, monkeypatch):
    d = make_daemon()
    gh.add_pr(7, HEAD)
    gh.merge_mode = "enqueue"
    _, op = await preview_op(d, method="merge")
    await settle(d, op["operation_id"], 1)
    gh.merge(7)
    gh.commits[MERGED]["parents"][1]["sha"] = "f" * 40
    client = d.ops.context["github"]
    original = client.commit
    async def check_intent(repo, sha):
        if sha == MERGED:
            row = d.journal.db.execute("SELECT status FROM operation_steps WHERE operation_id=? "
                                      "AND name LIKE 'merge.verify%' ORDER BY seq DESC LIMIT 1",
                                      (op["operation_id"],)).fetchone()
            assert row and row["status"] == "started"
        return await original(repo, sha)
    monkeypatch.setattr(client, "commit", check_intent)
    assert (await settle(d, op["operation_id"]))["error_code"] == "MERGE_RESULT_UNVERIFIABLE"
    gh.commits[MERGED]["parents"][1]["sha"] = HEAD
    d.ops.resume(OPERATOR, op["operation_id"])
    done = await settle(d, op["operation_id"])
    assert done["status"] == "succeeded" and done["result"]["verified"]
    assert any(s["name"].startswith("merge.verify.retry.") for s in done["steps"])
    assert gh.count("PUT", ".") == 1


async def test_c04_paginated_pr_list_finds_late_indirect_candidate(make_daemon, gh):
    d = make_daemon()
    for number in range(1, 110):
        if number != 7:
            gh.add_pr(number, "b" * 40)
    gh.add_pr(7, HEAD)
    gh.add_pr(111, "c" * 40)
    gh.commits[HEAD]["parents"] = [{"sha": "c" * 40}]
    doc = (await delivery.pr_preview(d.ops, "o/r", 7, "merge"))["merge_preview"]
    assert doc["affected_prs"][0]["number"] == 111
    assert doc["blocking"][0]["code"] == "MERGE_SCOPE_EXPANDED"
    assert gh.count("GET", r"pulls\?.*page=2") == 1 and gh.count("PUT", ".") == 0


@pytest.mark.parametrize("cancelled", [True, False])
async def test_metadata_lost_before_write_settles_not_applied_after_window(make_daemon, gh, cancelled):
    d = make_daemon()
    gh.add_pr(7, HEAD)
    gh.patch_mode = "lost_before"
    op = await update_op(d)
    unknown = await settle(d, op["operation_id"])
    assert unknown["status"] == "needs_attention" and unknown["error_code"] == "UNCERTAIN_UNRESOLVED"
    if cancelled:
        d.ops.cancel(OPERATOR, op["operation_id"])
    await pr_delivery.reconcile_metadata(d.ops)
    assert pr_delivery.metadata_settlement(d.ops, op["operation_id"]) is None
    with pytest.raises(OperationError) as e:
        await update_op(d, key="before-window")
    assert e.value.code == "PR_UPDATE_IN_PROGRESS"
    d.ops.db.execute("UPDATE operation_steps SET started_at=? WHERE operation_id=? AND name='pr.metadata.write'",
                     (pr_delivery.time.time() - pr_delivery.METADATA_SETTLE_S - 1, op["operation_id"]))
    await pr_delivery.reconcile_metadata(d.ops)
    receipt = pr_delivery.metadata_settlement(d.ops, op["operation_id"])
    assert receipt["status"] == "not_applied" and receipt["code"] == "PR_METADATA_NOT_APPLIED"
    reads = gh.count("GET", r"/pulls/7(?:\?|$)")
    await pr_delivery.reconcile_metadata(d.ops)
    await pr_delivery.reconcile_metadata(d.ops)
    assert gh.count("GET", r"/pulls/7(?:\?|$)") == reads
    # The delivery receipt settles the lock without changing another operation's step or refs.
    assert d.ops.get(op["operation_id"])["steps"][-1]["status"] == "uncertain"
    new = await update_op(d, key="after-window")
    assert new["status"] == "accepted" and gh.count("PATCH", ".") == 1
    d.ops.cancel(OPERATOR, new["operation_id"])
    if not cancelled:
        d.ops.resume(OPERATOR, op["operation_id"])
        done = await settle(d, op["operation_id"])
        assert done["status"] == "failed" and done["error_code"] == "PR_METADATA_NOT_APPLIED"
        assert done["external_refs"]["metadata_settlement"] == receipt
    else:
        assert d.ops.get(op["operation_id"])["status"] == "cancelled"
    assert gh.count("PATCH", ".") == 1


@pytest.mark.parametrize("cancelled", [False, True])
async def test_unresolved_metadata_write_third_value_settles_as_conflict_after_window(make_daemon, gh, cancelled):
    """C07, plan §09/§10/§15: third-value settlement releases admission without another PATCH or undo."""
    d = make_daemon()
    gh.add_pr(7, HEAD)
    gh.patch_mode = "lost_before"
    op = await update_op(d)
    unknown = await settle(d, op["operation_id"])
    assert unknown["status"] == "needs_attention" and unknown["error_code"] == "UNCERTAIN_UNRESOLVED"
    if cancelled:
        d.ops.cancel(OPERATOR, op["operation_id"])
    third = {"title": "Another editor's title", "body": "Another editor's body"}
    gh.pulls[7].update(third)
    await pr_delivery.reconcile_metadata(d.ops)
    assert pr_delivery.metadata_settlement(d.ops, op["operation_id"]) is None
    with pytest.raises(OperationError) as e:
        await update_op(d, key="before-conflict-window")
    assert e.value.code == "PR_UPDATE_IN_PROGRESS"
    d.ops.db.execute("UPDATE operation_steps SET started_at=? WHERE operation_id=? AND name='pr.metadata.write'",
                     (pr_delivery.time.time() - pr_delivery.METADATA_SETTLE_S - 1, op["operation_id"]))
    settling_at = pr_delivery.time.time()
    await pr_delivery.reconcile_metadata(d.ops)
    receipt = pr_delivery.metadata_settlement(d.ops, op["operation_id"])
    assert receipt["status"] == "conflict" and receipt["code"] == "PR_METADATA_CONFLICT"
    assert receipt["observed"] == third
    assert settling_at <= receipt["settled_at"] <= pr_delivery.time.time()
    saved = d.ops.get(op["operation_id"])
    refs = saved["external_refs"]
    assert refs["metadata_reconciliation"] == "PR_METADATA_CONFLICT" and refs["verification_pending"] is False
    assert refs["metadata_difference"]["observed"] == third
    assert refs["metadata_difference"]["before"] == {"title": "PR 7", "body": ""}
    assert refs["metadata_difference"]["after"] == {"title": "Reviewed title", "body": ""}
    assert saved["steps"][-1]["status"] == "uncertain"
    reads = gh.count("GET", r"/pulls/7(?:\?|$)")
    await pr_delivery.reconcile_metadata(d.ops)
    await pr_delivery.reconcile_metadata(d.ops)
    assert gh.count("GET", r"/pulls/7(?:\?|$)") == reads
    fresh = await update_op(d, key="after-conflict-window")
    assert fresh["status"] == "accepted"
    assert fresh["preconditions"]["expected_metadata_digest"] == pr_delivery.digest(third)
    d.ops.cancel(OPERATOR, fresh["operation_id"])
    if not cancelled:
        reads = gh.count("GET", r"/pulls/7(?:\?|$)")
        d.ops.resume(OPERATOR, op["operation_id"])
        held = await settle(d, op["operation_id"])
        assert held["status"] == "needs_attention" and held["error_code"] == "PR_METADATA_CONFLICT"
        assert held["external_refs"]["metadata_reconciliation"] == "PR_METADATA_CONFLICT"
        assert gh.count("GET", r"/pulls/7(?:\?|$)") == reads
    else:
        assert d.ops.get(op["operation_id"])["status"] == "cancelled"
    assert pr_delivery.metadata(gh.pulls[7]) == third and gh.count("PATCH", ".") == 1


async def test_metadata_late_landing_after_settlement_is_caught_by_digest(make_daemon, gh):
    d = make_daemon()
    gh.add_pr(7, HEAD)
    gh.patch_mode = "lost_before"
    op = await update_op(d, params={"body": "late old write"})
    await settle(d, op["operation_id"])
    d.ops.cancel(OPERATOR, op["operation_id"])
    d.ops.db.execute("UPDATE operation_steps SET started_at=? WHERE operation_id=? AND name='pr.metadata.write'",
                     (pr_delivery.time.time() - pr_delivery.METADATA_SETTLE_S - 1, op["operation_id"]))
    await pr_delivery.reconcile_metadata(d.ops)
    new = await update_op(d, key="fresh", params={"title": "new title"})
    gh.pulls[7]["body"] = "late old write"  # lands after the new operation's digest was reviewed
    done = await settle(d, new["operation_id"])
    assert done["status"] == "failed" and done["error_code"] == "PR_METADATA_CHANGED"
    assert gh.pulls[7]["title"] == "PR 7" and gh.pulls[7]["body"] == "late old write"
    assert gh.count("PATCH", ".") == 1


async def test_metadata_positive_cancel_reconciliation_pins_private_service_calls(make_daemon, gh, monkeypatch):
    d = make_daemon()
    gh.add_pr(7, HEAD)
    gh.patch_mode = "lost_before"
    op = await update_op(d)
    await settle(d, op["operation_id"])
    d.ops.cancel(OPERATOR, op["operation_id"])
    calls = []
    step_done, merge_refs = d.ops._step_done, d.ops._merge_refs
    def done(*args, **kwargs):
        calls.append(("step", args, kwargs))
        return step_done(*args, **kwargs)
    def refs(*args, **kwargs):
        calls.append(("refs", args, kwargs))
        return merge_refs(*args, **kwargs)
    monkeypatch.setattr(d.ops, "_step_done", done)
    monkeypatch.setattr(d.ops, "_merge_refs", refs)
    gh.pulls[7]["title"] = "Reviewed title"
    await pr_delivery.reconcile_metadata(d.ops)
    assert calls[0] == ("step", (op["operation_id"], "pr.metadata.write",
                               {"http_status": 200, "observed_intent": True, "write_acknowledged": False}),
                        {"reconciled": True})
    assert calls[1][0] == "refs" and calls[1][1][0] == op["operation_id"] and calls[1][2] == {}
    saved = d.ops.get(op["operation_id"])
    assert saved["status"] == "cancelled" and saved["steps"][-1]["status"] == "succeeded"
    assert saved["external_refs"]["observed_intent"] and not saved["external_refs"]["verification_pending"]
    assert gh.count("PATCH", ".") == 1


async def test_pr_card_reuses_identical_preview_and_prunes_expired(make_daemon, gh, monkeypatch):
    d = make_daemon()
    gh.add_pr(7, HEAD)
    client = d.ops.context["github"]
    original = client.compare
    summary = {"filename": "new.txt", "status": "renamed", "additions": 1, "deletions": 2, "changes": 3,
               "previous_filename": "old.txt"}
    async def files(*args, **kwargs):
        status, body = await original(*args, **kwargs)
        body["files"] = [{**summary, "patch": "large patch", "blob_url": "https://example/blob",
                          "contents_url": "https://example/contents", "raw_url": "https://example/raw", "sha": HEAD}]
        return status, body
    monkeypatch.setattr(client, "compare", files)
    first = (await delivery.pr_preview(d.ops, "o/r", 7))["merge_preview"]
    second = (await delivery.pr_preview(d.ops, "o/r", 7))["merge_preview"]
    assert first == second and first["files"] == [summary]
    assert d.ops.db.execute("SELECT count(*) FROM pr_merge_previews").fetchone()[0] == 1
    # Keep a queued merge's expired row while pruning an unreferenced preview at the same age.
    op, _ = d.ops.create(OPERATOR, **pr_delivery.merge_envelope(first), idempotency_key="queued")
    gh.merge_mode = "enqueue"
    assert (await settle(d, op["operation_id"], 1))["status"] == "waiting_external"
    orphan = {**first, "preview_id": "mpv_" + "0" * 32}
    d.ops.db.execute("INSERT INTO pr_merge_previews VALUES (?,?,?,?,?,?,?)",
                     (orphan["preview_id"], "o/r", 7, json.dumps(orphan), orphan["digest"],
                      orphan["created_at"], orphan["expires_at"]))
    monkeypatch.setattr(pr_delivery.time, "time", lambda: first["expires_at"] + pr_delivery.PREVIEW_RETENTION_S + 1)
    third = (await delivery.pr_preview(d.ops, "o/r", 7))["merge_preview"]
    assert third["preview_id"] != first["preview_id"]
    assert pr_delivery.get_preview(d.ops.db, first["preview_id"]) == first
    with pytest.raises(OperationError) as e:
        pr_delivery.get_preview(d.ops.db, orphan["preview_id"])
    assert e.value.code == "PREVIEW_NOT_FOUND"
    gh.merge(7)
    assert (await settle(d, op["operation_id"]))["status"] == "succeeded"
    await delivery.pr_preview(d.ops, "o/r", 7)
    with pytest.raises(OperationError):
        pr_delivery.get_preview(d.ops.db, first["preview_id"])


async def test_event_reloads_do_not_recompute_scope_within_window(make_daemon, gh):
    from bat_agent_connector import integration
    d = make_daemon()
    gh.add_pr(7, HEAD)
    for n in range(8, 18):
        gh.add_pr(n, f"{n:040x}")
    first = (await integration.pr_card(d.ops, "o/r", 7))["merge_preview"]
    before = len(gh.requests)
    for _ in range(8):
        card = await integration.pr_card(d.ops, "o/r", 7, from_event=True)
        assert card["merge_preview"] == first
    requests = gh.requests[before:]
    assert len(requests) == 16  # only one PR read and its checks per event
    assert all("/pulls/7" in p or "check-runs" in p for _, p, _ in requests)
    d.ops.db.execute("UPDATE pr_merge_scope_reads SET checked_at=checked_at-61")
    before_compares = gh.count("GET", "/compare/")
    card = await integration.pr_card(d.ops, "o/r", 7, from_event=True)
    assert gh.count("GET", "/compare/") > before_compares and card["merge_preview"] == first
    before_compares = gh.count("GET", "/compare/")
    await integration.pr_card(d.ops, "o/r", 7, from_event=True)
    assert gh.count("GET", "/compare/") == before_compares  # identical-row reuse refreshes the throttle clock
    gh.pulls[7]["title"] = "fresh cheap metadata"
    card = await integration.pr_card(d.ops, "o/r", 7, from_event=True)
    assert card["title"] == "fresh cheap metadata" and card["merge_preview"] == first
    gh.pulls[7]["head"]["sha"] = "e" * 40
    gh.commits["e" * 40] = {"sha": "e" * 40, "parents": [{"sha": "b" * 40}], "commit": {"message": "new head"}}
    card = await integration.pr_card(d.ops, "o/r", 7, from_event=True)
    assert card["merge_preview"]["target"]["head_sha"] == "e" * 40
    assert gh.count("GET", "/compare/") > before_compares
    before_compares = gh.count("GET", "/compare/")
    gh.pulls[7]["base"]["sha"] = "e" * 40
    card = await integration.pr_card(d.ops, "o/r", 7, from_event=True)
    assert card["merge_preview"]["target"]["base_sha"] == "e" * 40
    assert gh.count("GET", "/compare/") > before_compares


async def test_event_reload_throttle_is_shared_by_http_and_rpc(make_daemon, gh):
    from tests.test_api_v1 import http, token
    d = make_daemon()
    gh.add_pr(7, HEAD)
    gh.add_pr(8, "c" * 40)
    server = await asyncio.start_server(d._handle, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    observe = token(d, "viewer", "observe")
    try:
        status, body = await http(port, "GET", "/api/v1/repositories/o/r/pulls/7", tok=observe)
        assert status == 200
        preview = body["pull_request"]["merge_preview"]
        before = len(gh.requests)
        for _ in range(3):
            status, body = await http(port, "GET", "/api/v1/repositories/o/r/pulls/7?from_event=true", tok=observe)
            assert status == 200 and body["pull_request"]["merge_preview"] == preview
            status, body = await http(port, "POST", "/rpc", tok=observe, body={"method": "github_pr_preview",
                "params": {"repository": "o/r", "pull_number": 7, "from_event": True}})
            assert status == 200 and body["result"]["pull_request"]["merge_preview"] == preview
        assert len(gh.requests) - before == 12
    finally:
        server.close()
        await server.wait_closed()


async def test_transient_scope_read_error_before_submit_is_resumable(make_daemon, gh, monkeypatch):
    from bat_agent_connector.github import GitHubAmbiguous
    d = make_daemon()
    gh.add_pr(7, HEAD)
    _, op = await preview_op(d)
    original = d.ops.context["github"].repository
    async def once(*args):
        monkeypatch.setattr(d.ops.context["github"], "repository", original)
        raise GitHubAmbiguous("transient scope read")
    monkeypatch.setattr(d.ops.context["github"], "repository", once)
    stopped = await settle(d, op["operation_id"])
    assert stopped["status"] == "needs_attention" and stopped["error_code"] == "MERGE_SCOPE_UNPROVEN"
    assert not stopped["steps"] and gh.count("PUT", ".") == 0
    d.ops.resume(OPERATOR, op["operation_id"])
    done = await settle(d, op["operation_id"])
    assert done["status"] == "succeeded" and gh.count("PUT", ".") == 1


async def test_checks_wait_only_rereads_head_and_base_before_final_scope(make_daemon, gh):
    d = make_daemon()
    pr = gh.add_pr(7, HEAD, mergeable_state="blocked")
    gh.check_runs[HEAD] = [{"status": "in_progress", "conclusion": None}]
    _, op = await preview_op(d)
    compares = gh.count("GET", "/compare/")
    lists = gh.count("GET", r"/pulls\?")
    assert (await settle(d, op["operation_id"], 5))["status"] == "waiting_checks"
    assert gh.count("GET", "/compare/") == compares and gh.count("GET", r"/pulls\?") == lists
    pr["base"]["sha"] = "e" * 40
    stopped = await settle(d, op["operation_id"])
    assert stopped["error_code"] == "TARGET_BASE_CHANGED" and gh.count("PUT", ".") == 0


async def test_verify_accepts_affected_pr_merged_after_this_merge(make_daemon, gh):
    d = make_daemon()
    gh.add_pr(7, HEAD)
    gh.add_pr(6, HEAD)
    gh.merge_mode = "enqueue"
    doc, op = await preview_op(d)
    assert doc["affected_prs"][0]["number"] == 6
    await settle(d, op["operation_id"], 1)
    gh.merge(7)
    gh.merge(6, "8" * 40)
    done = await settle(d, op["operation_id"])
    assert done["status"] == "succeeded" and done["result"]["merged_sha"] == MERGED
    outcome = done["result"]["affected_prs"][0]
    assert outcome["number"] == 6 and outcome["merged_after"] and outcome["independent"]
    assert gh.count("PUT", ".") == 1


async def test_verify_stops_updated_pr_pagination_at_admission(make_daemon, gh):
    from datetime import datetime, timezone
    d = make_daemon()
    gh.add_pr(7, HEAD)
    # More than two pages of history, with recent PRs deliberately inserted among old ones.
    for n in range(100, 350):
        gh.add_pr(n, HEAD, state="closed", updated_at="2020-01-01T00:00:00+00:00")
    for n in range(350, 450):
        gh.add_pr(n, HEAD, state="closed", updated_at=datetime.now(timezone.utc).isoformat())
    _, op = await preview_op(d)
    # Pin these updates after admission; preview reads may take us past their creation second.
    updated_at = datetime.fromtimestamp(op["created_at"] + 1, timezone.utc).isoformat()
    for n in range(350, 450):
        gh.pulls[n]["updated_at"] = updated_at
    done = await settle(d, op["operation_id"])
    assert done["status"] == "succeeded"
    paths = [p for m, p, _ in gh.requests if m == "GET" and "/pulls?" in p and "state=all" in p]
    assert len(paths) == 2 and all("sort=updated" in p and "direction=desc" in p for p in paths)
    assert "page=1&" in paths[0] and "page=2&" in paths[1]


@pytest.mark.parametrize("status", [401, 403, 404])
@pytest.mark.parametrize("endpoint", ["pull", "commit", "compare", "recent_prs", "stacks", "affected_pull"])
async def test_refused_read_after_merge_submit_needs_attention_and_resumes(make_daemon, gh, status, endpoint):
    """C04/C05, plan §09/§16: refused evidence reads retain the sent merge and resume without another PUT."""
    d = make_daemon()
    gh.add_pr(7, HEAD)
    gh.add_pr(6, HEAD)
    gh.merge_mode = "enqueue"
    _, op = await preview_op(d)
    assert (await settle(d, op["operation_id"], 1))["status"] == "waiting_external"
    gh.merge(7)
    pattern = {"pull": r"/pulls/7$", "commit": rf"/commits/{MERGED}$", "compare": r"/compare/",
               "recent_prs": r"/pulls$", "stacks": r"/stacks$", "affected_pull": r"/pulls/6$"}[endpoint]
    for _ in range(2):
        gh.script.append(("GET", pattern, status, {}, {"message": "read permission unavailable"}))
        held = await settle(d, op["operation_id"])
        assert held["status"] == "needs_attention" and held["error_code"] == f"GITHUB_{status}", held
        assert all(s["status"] != "failed" for s in held["steps"])
        assert gh.count("PUT", "merge-async") == 1 and gh.count("POST", ".") == 0
        d.ops.resume(OPERATOR, op["operation_id"])
    done = await settle(d, op["operation_id"])
    assert done["status"] == "succeeded" and done["result"]["verified"] and done["result"]["merged_sha"] == MERGED
    assert gh.count("PUT", "merge-async") == 1


@pytest.mark.parametrize("status", [401, 403, 404])
@pytest.mark.parametrize("lost_reply", [False, True])
async def test_refused_read_after_metadata_write_needs_attention_and_resumes(make_daemon, gh, status, lost_reply):
    """C07, plan §10/§15: refused ACK readback or uncertain PATCH reconcile never fails or re-PATCHes."""
    d = make_daemon()
    gh.add_pr(7, HEAD)
    op = await update_op(d)
    if lost_reply:
        gh.patch_mode = "lost_after"
    refusal = ("GET", r"/pulls/7$", status, {}, {"message": "read permission unavailable"})
    gh.patch_after = lambda _: gh.script.append(refusal)
    held = await settle(d, op["operation_id"])
    assert held["status"] == "needs_attention" and held["error_code"] == f"GITHUB_{status}", held
    assert all(s["status"] != "failed" for s in held["steps"])
    assert gh.pulls[7]["title"] == "Reviewed title" and gh.count("PATCH", ".") == 1
    # Resuming before permission is repaired must remain resumable, with a fresh read each time.
    gh.script.append(refusal)
    d.ops.resume(OPERATOR, op["operation_id"])
    again = await settle(d, op["operation_id"])
    assert again["status"] == "needs_attention" and again["error_code"] == f"GITHUB_{status}", again
    d.ops.resume(OPERATOR, op["operation_id"])
    done = await settle(d, op["operation_id"])
    assert done["status"] == "succeeded" and done["result"]["verified"]
    assert gh.count("PATCH", ".") == 1


@pytest.mark.parametrize("status", [401, 403, 404])
@pytest.mark.parametrize("phase", ["plan", "pre_patch"])
async def test_metadata_refused_read_before_patch_fails_fast(make_daemon, gh, status, phase):
    """C07, plan §10/§15: a recorded readonly plan or write intent alone cannot hide a pre-PATCH refusal."""
    d = make_daemon()
    gh.add_pr(7, HEAD)
    op = await update_op(d)
    reads = 0
    def refuse(method, path, _):
        nonlocal reads
        if method == "GET" and path == "/repos/o/r/pulls/7":
            reads += 1
            if reads == (1 if phase == "plan" else 2):
                gh.script.append(("GET", r"/pulls/7$", status, {}, {"message": "no permission before PATCH"}))
    gh.before_request = refuse
    done = await settle(d, op["operation_id"])
    assert done["status"] == "failed" and done["error_code"] == f"GITHUB_{status}", done
    assert gh.count("PATCH", ".") == 0


@pytest.mark.parametrize("status", [401, 403, 404])
async def test_readonly_merge_verification_refusal_before_write_fails_fast(make_daemon, gh, status):
    """C04/C05, plan §16: an observed merge by another person is not this operation's recorded write."""
    d = make_daemon()
    gh.add_pr(7, HEAD)
    _, op = await preview_op(d)
    gh.merge(7)  # another person merged it before this operation sent anything
    gh.script.append(("GET", rf"/commits/{MERGED}$", status, {}, {"message": "no permission before our write"}))
    done = await settle(d, op["operation_id"])
    assert done["status"] == "failed" and done["error_code"] == f"GITHUB_{status}", done
    assert gh.count("PUT", ".") == 0 and gh.count("POST", ".") == 0


@pytest.mark.parametrize("outcome", ["open", "closed", "merged_reviewed", "merged_other_head"])
async def test_merge_async_400_reads_pr_before_failing(make_daemon, gh, outcome):
    """C04/C05, plan §16, #32: a definitive 400 still requires reading whether the reviewed PR already merged."""
    d = make_daemon()
    pr = gh.add_pr(7, HEAD)
    _, op = await preview_op(d)
    gh.script.append(("PUT", r"/merge-async$", 400, {}, {"message": "PR cannot be merged"}))
    def concurrently_changed(method, path, _):
        if method == "PUT" and path.endswith("/merge-async"):
            if outcome == "closed":
                pr["state"] = "closed"
            elif outcome.startswith("merged"):
                if outcome == "merged_other_head":
                    pr["head"]["sha"] = "e" * 40
                gh.merge(7)
    gh.before_request = concurrently_changed
    done = await settle(d, op["operation_id"])
    if outcome == "merged_reviewed":
        assert done["status"] == "succeeded" and done["result"]["verified"], done
        assert done["result"]["merged_sha"] == MERGED and not done["result"]["merged_by_this_operation"]
        assert not done["external_refs"]["merge_write_acknowledged"]
        assert any(s["name"] == "merge.verify" for s in done["steps"])
    else:
        assert done["status"] == "failed" and done["error_code"] == "PR_NOT_MERGEABLE", done
        assert not any(s["name"].startswith("merge.verify") for s in done["steps"])
    put_index = next(i for i, (m, _, _) in enumerate(gh.requests) if m == "PUT")
    assert gh.requests[put_index + 1][:2] == ("GET", "/repos/o/r/pulls/7")
    assert gh.count("PUT", ".") == 1 and gh.count("POST", ".") == 0


@pytest.mark.parametrize("status", [401, 403, 404])
async def test_merge_async_400_readback_refusal_remains_resumable(make_daemon, gh, status):
    """C04/C05, plan §16: a refused read after the 400 is not a proven merge outcome."""
    d = make_daemon()
    gh.add_pr(7, HEAD)
    _, op = await preview_op(d)
    gh.script.append(("PUT", r"/merge-async$", 400, {}, {"message": "PR cannot be merged"}))
    def refuse_readback(method, path, _):
        if method == "PUT" and path.endswith("/merge-async"):
            gh.script.append(("GET", r"/pulls/7$", status, {}, {"message": "read permission unavailable"}))
    gh.before_request = refuse_readback
    held = await settle(d, op["operation_id"])
    assert held["status"] == "needs_attention" and held["error_code"] == f"GITHUB_{status}", held
    assert gh.count("PUT", ".") == 1
    gh.merge(7)  # GitHub is readable again and another person has merged the reviewed head
    d.ops.resume(OPERATOR, op["operation_id"])
    done = await settle(d, op["operation_id"])
    assert done["status"] == "succeeded" and done["result"]["verified"]
    assert not done["result"]["merged_by_this_operation"]
    assert gh.count("PUT", ".") == 1
