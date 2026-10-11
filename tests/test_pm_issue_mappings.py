"""Disk Git packages and loopback GitHub; never publish to a real repository."""
from __future__ import annotations

import copy
import json
import re
import sqlite3
import uuid

import pytest

from bat_agent_connector import pm_issue_mappings as issues
from bat_agent_connector import pm_workspace
from bat_agent_connector.operations import OpContext, OperationError, Uncertain
from tests.operation_helpers import settle_operations
from tests.test_pm_work_packages import OWNER, add_task_evidence, create, member, read
from tests.test_pm_work_packages import pm as pm_fixture
from tests.test_pm_work_packages import ready as ready_fixture
from tests.test_pm_work_packages import world as world_fixture
from tests.test_pm_worktree import resume

world = world_fixture
pm = pm_fixture
ready = ready_fixture


class IssueProvider:
    def __init__(self, w):
        self.gh, self.issues, self.comments = w["gh"], {}, []
        self.mode = "ok"
        self.before = self.gh.before_request
        self.gh.before_request = self.route

    def route(self, method, path, body):
        self.before(method, path, body)
        base = self.gh.url + "/repos/o/r"
        status, answer = 200, None
        if method == "POST" and path == "/repos/o/r/issues":
            number = len(self.issues) + 1
            value = {"id": 2000 + number, "number": number, "title": body["title"], "body": body["body"],
                     "repository_url": base, "url": base + f"/issues/{number}", "state": "open"}
            if self.mode != "lost_before":
                self.issues[number] = value
            status, answer = 201, value
        elif method == "GET" and path == "/repos/o/r/issues":
            answer = list(self.issues.values())
        elif match := re.fullmatch(r"/repos/o/r/issues/(\d+)(/comments)?", path):
            number = int(match[1])
            if not match[2] and method == "GET":
                status, answer = (200, self.issues[number]) if number in self.issues else (404, {})
            elif match[2] and method == "POST":
                identifier = 9000 + len(self.comments)
                value = {"id": identifier, "body": body["body"], "issue_url": base + f"/issues/{number}",
                         "url": base + f"/issues/comments/{identifier}"}
                if self.mode != "lost_before":
                    self.comments.append(value)
                status, answer = 201, value
            elif match[2] and method == "GET":
                answer = [c for c in self.comments if c["issue_url"].endswith(f"/issues/{number}")]
        if answer is not None:
            if method == "POST" and self.mode.startswith("lost"):
                status, answer, self.mode = 502, {"message": "Synthetic lost reply"}, "ok"
            elif method == "POST" and self.mode == "malformed":
                answer = {"body": body["body"]}
            elif method == "POST" and self.mode == "refused":
                # Definitive HTTP refusal has no mutation in this fixture.
                if path == "/repos/o/r/issues":
                    self.issues.pop(number, None)
                else:
                    self.comments.pop()
                status, answer = 403, {"message": "Synthetic refusal"}
            self.gh.script.append((method, re.escape(path) + "$", status, {}, copy.deepcopy(answer)))


@pytest.fixture
async def mapped(ready, monkeypatch):
    w = ready
    ops = w["d"].ops
    issues.install(ops)
    issues.install(ops)
    w["issue_provider"] = IssueProvider(w)
    original = pm_workspace.operation_visible

    def visibility(db, principal, operation):
        return original(db, principal, operation) and (operation["action"] not in issues.ACTIONS
            or issues.operation_visible(db, principal, operation))

    monkeypatch.setattr(pm_workspace, "operation_visible", visibility)
    w["package"] = read(w, await create(w))
    return w


def request(w, *, previous=None, package=None, **params):
    package = package or w["package"]
    return {"action": issues.PUBLISH, "target": w["target"],
            "params": {"package_id": package["package_id"], "public_title": "Verify member access",
                       "public_body": "Selected public description. 未驗證的工程工作。", **params},
            "preconditions": {"expected_package_digest": package["document_digest"], "expected_mapping_revision": previous},
            "idempotency_key": uuid.uuid4().hex}


async def run(w, req):
    ops = w["d"].ops
    op, _ = ops.create(OWNER, **req)
    await settle_operations(ops)
    return ops.get(op["operation_id"])


async def publish(w, **kwargs):
    done = await run(w, request(w, **kwargs))
    assert done["status"] == "succeeded", done
    return done


def cancellation(w, previous, **params):
    result = previous["result"]
    return {"action": issues.CANCEL, "target": {"workspace_id": w["workspace_id"], "mapping_id": result["mapping_id"]},
            "params": {"rationale": "Private management reason; retain the external collaboration", **params},
            "preconditions": {"expected_mapping_revision": result["revision_id"]}, "idempotency_key": uuid.uuid4().hex}


async def test_explicit_publication_is_one_real_issue_with_native_receipt_and_no_private_package(mapped):
    w, ops = mapped, mapped["d"].ops
    counts = {table: ops.db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] for table in ("tasks", "artifacts", "pm_runs")}
    envelope = request(w)
    done = await run(w, envelope)
    assert done["status"] == "succeeded", done
    result, provider = done["result"], w["issue_provider"]
    assert result["issue"]["id"] == 2001 and result["issue"]["number"] == 1 and result["publication"]["native_id"] == 2001
    assert not result["execution_authorized"] and not result["publication"]["counts_as_done"]
    body = provider.issues[1]["body"]
    assert body.startswith(envelope["params"]["public_body"] + "\n\n<!-- connector-pm-issue:")
    assert w["workspace_id"] not in body and w["package"]["package_id"] not in body and "openspec/" not in body
    assert "Valid credentials allow access" not in body and "Selected owner direction" not in body
    assert ops.create(OWNER, **envelope)[0]["operation_id"] == done["operation_id"]
    assert await issues._run(OpContext(ops, done)) == result
    assert w["gh"].count("POST", r"/issues$") == 1
    assert all(ops.db.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] == n for t, n in counts.items())
    view = issues.get_mapping(ops, OWNER, w["workspace_id"], result["mapping_id"])
    assert view["current"]["document"]["package_ref"]["document_digest"] == w["package"]["document_digest"]
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        ops.db.execute("UPDATE pm_issue_revisions SET document='{}'")
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        ops.db.execute("DELETE FROM pm_issue_revisions")


async def test_task_reordering_keeps_whole_work_mapping_and_preserves_external_edits(mapped):
    w = mapped
    one = await publish(w)
    provider = w["issue_provider"]
    provider.issues[1].update(title="Maintainer's edited title", body="Another author's new body", state="closed")
    external = copy.deepcopy(provider.issues[1])
    tasks = copy.deepcopy(w["params"]["tasks"])
    tasks.insert(0, {**tasks[0], "key": "identity.other", "description": "A separate engineering subtask"})
    package = read(w, await create(w, previous=w["package"]["package_id"], tasks=tasks))
    two = await publish(w, previous=one["result"]["revision_id"], package=package, public_title="New selected revision")
    assert two["result"]["mapping_id"] == one["result"]["mapping_id"]
    assert two["result"]["issue"] == one["result"]["issue"]
    assert provider.issues[1] == external and len(provider.issues) == len(provider.comments) == 1
    assert provider.comments[0]["body"].startswith("New selected revision\n\n")
    assert not any(method == "PATCH" for method, _, _ in w["gh"].requests)
    assert issues.list_mappings(w["d"].ops, OWNER, w["workspace_id"])["mappings"][0]["revision_id"] == two["result"]["revision_id"]


@pytest.mark.parametrize("public_reason", [None, "Selected public cancellation reason"])
async def test_stop_tracking_does_not_close_issue_or_publish_private_rationale(mapped, public_reason):
    w = mapped
    one = await publish(w)
    before = copy.deepcopy(w["issue_provider"].issues[1])
    req = cancellation(w, one, **({"public_reason": public_reason} if public_reason else {}))
    done = await run(w, req)
    assert done["status"] == "succeeded", done
    assert done["result"]["state"] == "cancelled" and done["result"]["external_issue_state"] == "not_changed"
    assert w["issue_provider"].issues[1] == before
    comments = w["issue_provider"].comments
    assert len(comments) == (1 if public_reason else 0)
    assert not comments or public_reason in comments[0]["body"] and req["params"]["rationale"] not in comments[0]["body"]
    with pytest.raises(OperationError, match="PM_ISSUE_CANCELLED"):
        w["d"].ops.create(OWNER, **request(w, previous=done["result"]["revision_id"]))


@pytest.mark.parametrize("kind", ["create", "revision", "cancel"])
async def test_lost_post_response_reconciles_original_native_object_without_resend(mapped, kind):
    w, ops = mapped, mapped["d"].ops
    one = await publish(w) if kind != "create" else None
    req = (cancellation(w, one, public_reason="Selected public reason") if kind == "cancel"
           else request(w, previous=one["result"]["revision_id"] if one else None))
    w["issue_provider"].mode = "lost_after"
    unknown = await run(w, req)
    assert unknown["status"] == "uncertain", unknown
    count = len([m for m, _, _ in w["gh"].requests if m == "POST"])
    ops.db.execute("DELETE FROM pm_workspace_members WHERE workspace_id=? AND actor=?", (w["workspace_id"], OWNER.actor))
    recovered = await resume(w, unknown)
    assert recovered["status"] == "succeeded", recovered
    assert len([m for m, _, _ in w["gh"].requests if m == "POST"]) == count
    assert not issues.operation_visible(ops.db, OWNER, recovered)
    with pytest.raises(OperationError):
        ops.get(recovered["operation_id"], principal=OWNER)


@pytest.mark.parametrize("mode", ["lost_before", "malformed"])
async def test_unknown_publication_never_retries_or_releases_mapping_to_new_key(mapped, mode):
    w, ops = mapped, mapped["d"].ops
    w["issue_provider"].mode = mode
    unknown = await run(w, request(w))
    assert unknown["status"] == "uncertain", unknown
    if mode == "malformed":
        w["issue_provider"].issues.clear()  # The original event cannot be read back.
    again = await resume(w, unknown)
    assert again["status"] == "uncertain", again
    competing = await run(w, request(w))
    assert competing["status"] == "failed" and competing["error_code"] == "PM_ISSUE_PENDING", competing
    assert w["gh"].count("POST", r"/issues$") == 1
    assert not ops.db.execute("SELECT 1 FROM pm_issue_revisions").fetchone()


async def test_two_different_keys_compete_for_one_mapping_not_two_issues(mapped):
    w, ops = mapped, mapped["d"].ops
    first, _ = ops.create(OWNER, **request(w))
    second, _ = ops.create(OWNER, **request(w))
    await settle_operations(ops)
    states = [ops.get(op["operation_id"]) for op in (first, second)]
    assert sorted(op["status"] for op in states) == ["failed", "succeeded"], states
    assert w["gh"].count("POST", r"/issues$") == 1


async def test_definitively_refused_issue_does_not_poison_future_explicit_request(mapped):
    w = mapped
    w["issue_provider"].mode = "refused"
    failed = await run(w, request(w))
    assert failed["status"] == "failed" and failed["error_code"] == "PM_ISSUE_PUBLICATION_REFUSED", failed
    w["issue_provider"].mode = "ok"
    succeeded = await publish(w)
    assert succeeded["result"]["issue"]["number"] == 1


async def test_duplicate_marker_or_changed_remote_identity_is_not_reconciliation_proof(mapped):
    w = mapped
    w["issue_provider"].mode = "lost_after"
    unknown = await run(w, request(w))
    assert unknown["status"] == "uncertain", unknown
    provider = w["issue_provider"]
    original = provider.issues[1]
    provider.issues[2] = {**original, "id": 2002, "number": 2, "url": original["url"].rsplit("/", 1)[0] + "/2"}
    duplicate = await resume(w, unknown)
    assert duplicate["status"] == "uncertain", duplicate
    provider.issues.pop(2)
    original["repository_url"] = w["gh"].url + "/repos/another/repo"
    moved = await resume(w, duplicate)
    assert moved["status"] == "uncertain", moved
    assert w["gh"].count("POST", r"/issues$") == 1


async def test_reconcile_does_not_substitute_new_github_service(mapped, monkeypatch):
    w, ops = mapped, mapped["d"].ops
    w["issue_provider"].mode = "lost_after"
    unknown = await run(w, request(w))
    original_url = ops.context["github"].cfg.api_url
    monkeypatch.setattr(ops.context["github"].cfg, "api_url", original_url + "/other-service")
    before = len(w["gh"].requests)
    still_unknown = await resume(w, unknown)
    assert still_unknown["status"] == "uncertain", still_unknown
    assert len(w["gh"].requests) == before


@pytest.mark.parametrize("stop", ["cancel", "revoke", "work_changed", "repository_changed"])
async def test_final_frame_rechecks_after_network_preflight(mapped, monkeypatch, stop):
    w, ops = mapped, mapped["d"].ops
    gh = ops.context["github"]
    original = gh.repository
    op, _ = ops.create(OWNER, **request(w))

    async def changed(repository):
        answer = await original(repository)
        if stop == "cancel":
            ops.cancel(OWNER, op["operation_id"])
        elif stop == "revoke":
            ops.db.execute("UPDATE pm_workspace_members SET role='reader' WHERE workspace_id=? AND actor=?", (w["workspace_id"], OWNER.actor))
        elif stop == "work_changed":
            ops.db.execute("UPDATE work_items SET request='new direction',version=version+1 WHERE work_item_id=?", (w["target"]["work_item_id"],))
        else:
            answer[1]["id"] += 1
        return answer

    monkeypatch.setattr(gh, "repository", changed)
    await settle_operations(ops)
    done = ops.get(op["operation_id"])
    assert done["status"] in {"failed", "cancelled"}, done
    assert not w["issue_provider"].issues and not w["gh"].count("POST", r"/issues$")


async def test_original_source_withdrawal_hides_history_pending_operation_and_same_key_replay(mapped):
    w, ops = mapped, mapped["d"].ops
    task, evidence = await add_task_evidence(w)
    w["package"] = read(w, await create(w, previous=w["package"]["package_id"], evidence_refs=[evidence]))
    one = await publish(w)
    package = read(w, await create(w, previous=w["package"]["package_id"], evidence_refs=[]))
    envelope = request(w, previous=one["result"]["revision_id"], package=package)
    pending, _ = ops.create(OWNER, **envelope)
    assert issues.operation_visible(ops.db, OWNER, pending)
    ops.db.execute("UPDATE tasks SET controller_authority='{}' WHERE task_id=?", (task["task_id"],))
    for op in (one, pending):
        assert not issues.operation_visible(ops.db, OWNER, op)
        with pytest.raises(OperationError):
            ops.get(op["operation_id"], principal=OWNER)
    with pytest.raises(OperationError):
        ops.create(OWNER, **envelope)
    assert ops.has_idempotency_record(OWNER, action=issues.PUBLISH, target=envelope["target"], idempotency_key=envelope["idempotency_key"])
    with pytest.raises(OperationError):
        issues.get_mapping(ops, OWNER, w["workspace_id"], one["result"]["mapping_id"])
    assert issues.list_mappings(ops, OWNER, w["workspace_id"])["mappings"] == []
    await settle_operations(ops)
    assert len(w["issue_provider"].comments) == 0
    assert await issues._run(OpContext(ops, one)) == one["result"]


async def test_local_receipt_loss_and_transaction_rollback_do_not_duplicate_post(mapped, monkeypatch):
    w, ops = mapped, mapped["d"].ops
    original = ops.journal.api_event
    armed = True

    def fail_once(kind, resource, event, payload, **kwargs):
        nonlocal armed
        if kind == "pm_issue_mapping" and armed:
            armed = False
            raise Uncertain(issues.RECORD_STEP, "Simulated journal failure after row insertion")
        return original(kind, resource, event, payload, **kwargs)

    monkeypatch.setattr(ops.journal, "api_event", fail_once)
    unknown = await run(w, request(w))
    assert unknown["status"] == "uncertain", unknown
    assert not ops.db.execute("SELECT 1 FROM pm_issue_revisions").fetchone()
    recovered = await resume(w, unknown)
    assert recovered["status"] == "succeeded", recovered
    assert w["gh"].count("POST", r"/issues$") == 1


async def test_injected_task_mapping_or_whole_package_export_rejected_and_read_budget_is_whole(mapped, monkeypatch):
    w = mapped
    for field, value in (("task_key", "identity.login"), ("auto_export", True), ("public_body", "<!-- connector-pm-issue:fake -->")):
        with pytest.raises(OperationError):
            w["d"].ops.create(OWNER, **request(w, **{field: value}))
    one = await publish(w)
    monkeypatch.setattr(issues, "MAX_READ_BYTES", 100)
    with pytest.raises(OperationError) as err:
        issues.get_mapping(w["d"].ops, OWNER, w["workspace_id"], one["result"]["mapping_id"])
    assert err.value.status == 413
    with pytest.raises(OperationError) as err:
        issues.list_mappings(w["d"].ops, OWNER, w["workspace_id"])
    assert err.value.status == 413


async def test_read_only_member_cannot_publish_and_schema_retains_user_version(mapped):
    w, ops = mapped, mapped["d"].ops
    version = ops.db.execute("PRAGMA user_version").fetchone()[0]
    issues.schema(ops.journal)
    assert ops.db.execute("PRAGMA user_version").fetchone()[0] == version
    from bat_agent_connector.api_auth import Principal
    reader = Principal("issue-reader", frozenset({"observe", "manage"}))
    await member(w, reader.actor, "reader")
    with pytest.raises(OperationError, match="FORBIDDEN"):
        ops.create(reader, **request(w))
    assert not w["issue_provider"].issues


async def test_recorded_revision_bytes_are_stable_and_no_completion_claims(mapped):
    one = await publish(mapped)
    view = issues.get_mapping(mapped["d"].ops, OWNER, mapped["workspace_id"], one["result"]["mapping_id"])
    document = view["current"]["document"]
    assert json.loads(json.dumps(document, ensure_ascii=False)) == document
    assert document["external_content_policy"] == "create_or_append_only"
    assert document["state"] == "tracking" and document["counts_as_done"] is False
