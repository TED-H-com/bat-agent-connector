"""Deployment evidence and fixed identities (D01-D06, plan §17, issue #32)."""

from __future__ import annotations

import json
from dataclasses import replace

import pytest

from bat_agent_connector import delivery, deployment, pr_delivery
from bat_agent_connector import deployment_store as store
from bat_agent_connector.operations import OperationError, OperationService
from tests import test_delivery as fixtures
from tests.test_delivery import HEAD, MERGED, OPERATOR, settle

gh = fixtures.gh
make_daemon = fixtures.make_daemon

NEW = "1" * 40


async def reconcile_due(ops):
    """Existing recovery scenarios advance to the next provider read, retaining every negative assertion."""
    ops.db.execute("UPDATE deployment_reconcile_reads SET checked_at=checked_at-300 WHERE read_key LIKE 'provider:%'")
    await delivery.reconcile_deployments(ops)


async def start(
    d, *, name="prod", sha=MERGED, key="deploy", action="deployment.start", params=None, pre=None
):
    p = await deployment.preview(d.ops, name)
    return d.ops.create(
        OPERATOR,
        action=action,
        target={"recipe": name},
        params=params or {"source_sha": sha},
        preconditions=pre or p["preconditions"],
        idempotency_key=key,
    )[0]


def completed(gh, rid, *, source=MERGED):
    gh.runs[rid].update(status="completed", conclusion="success")
    gh.jobs[rid][1]["conclusion"] = "success"
    gh.deployed_source = source


def source_on_main(gh):
    gh.commits[MERGED] = {"sha": MERGED, "parents": [{"sha": "b" * 40}]}
    gh.branches["main"] = MERGED


async def deployed(d, gh, *, key="first", sha=MERGED):
    op = await start(d, sha=sha, key=key)
    waiting = await settle(d, op["operation_id"], rounds=1)
    completed(gh, waiting["external_refs"]["deploy_run_id"], source=sha)
    done = await settle(d, op["operation_id"])
    assert done["status"] == "succeeded", json.dumps(done)
    return done


@pytest.mark.parametrize(
    "case,code,status",
    [
        ("missing_job", "DEPLOY_NOT_RUN", "failed"),
        ("skipped", "DEPLOY_NOT_RUN", "failed"),
        ("job_running", "DEPLOY_NOT_RUN", "failed"),
        ("pending_environment", None, "waiting_external"),
        ("run_waiting", None, "waiting_external"),
        ("wrong_sha", "DEPLOY_VERSION_MISMATCH", "needs_attention"),
        ("wrong_repository", "DEPLOY_VERSION_MISMATCH", "needs_attention"),
        ("wrong_environment", "DEPLOY_VERSION_MISMATCH", "needs_attention"),
        ("unhealthy", "DEPLOY_HEALTH_FAILED", "needs_attention"),
        ("missing_health", None, "waiting_external"),
        ("missing_version", None, "waiting_external"),
        ("outage", None, "waiting_external"),
    ],
)
async def test_d03_success_requires_job_environment_version_and_health(make_daemon, gh, case, code, status):
    d = make_daemon()
    source_on_main(gh)
    op = await start(d)
    waiting = await settle(d, op["operation_id"], rounds=1)
    rid = waiting["external_refs"]["deploy_run_id"]
    completed(gh, rid)
    v = d.ops.context["deployment_verifier"]
    v.response = {"repository_id": 4242, "environment": "production", "source_sha": MERGED, "healthy": True}
    if case == "missing_job":
        gh.jobs[rid] = []
    elif case == "skipped":
        gh.jobs[rid][1]["conclusion"] = "skipped"
    elif case == "job_running":
        gh.jobs[rid][1]["status"] = "in_progress"
    elif case == "pending_environment":
        gh.pending_deployments[rid] = [{"environment": {"name": "production"}}]
    elif case == "run_waiting":
        gh.runs[rid]["status"] = "waiting"
    elif case == "wrong_sha":
        v.response["source_sha"] = NEW
    elif case == "wrong_repository":
        v.response["repository_id"] = 999
    elif case == "wrong_environment":
        v.response["environment"] = "staging"
    elif case == "unhealthy":
        v.response["healthy"] = False
    elif case == "missing_health":
        v.response.pop("healthy")
    elif case == "missing_version":
        v.response.pop("source_sha")
    elif case == "outage":
        v.response = {"waiting": "runtime check unavailable"}
    done = await settle(d, op["operation_id"], rounds=1)
    assert done["status"] == status, done
    assert done["error_code"] == code
    assert deployment.environment_status(d.ops, "prod")["current"] is None
    assert not deployment.status(d.ops, done["external_refs"]["deployment_id"])["verified"]
    assert gh.count("POST", "dispatches") == 1


async def test_d03_workflow_sha_is_not_product_sha(make_daemon, gh):
    d = make_daemon()
    source_on_main(gh)
    done = await deployed(d, gh)
    assert gh.runs[done["result"]["run_id"]]["head_sha"] != MERGED
    assert done["result"]["source_sha"] == MERGED
    assert done["result"]["evidence"]["runtime"]["version_checked"] is True


async def test_d03_run_attempt_cannot_borrow_other_jobs(make_daemon, gh):
    d = make_daemon()
    source_on_main(gh)
    op = await start(d)
    w = await settle(d, op["operation_id"], rounds=1)
    rid = w["external_refs"]["deploy_run_id"]
    completed(gh, rid)
    gh.runs[rid]["run_attempt"] = 2
    gh.attempt_jobs[(rid, 2)] = [{"name": "deploy", "status": "completed", "conclusion": "success"}]
    done = await settle(d, op["operation_id"])
    assert done["error_code"] == "DEPLOY_ATTEMPT_CHANGED"
    assert gh.count("GET", "/attempts/2/jobs") == 0
    assert deployment.environment_status(d.ops, "prod")["current"] is None


async def test_d03_attempt_jobs_are_paginated(make_daemon, gh):
    d = make_daemon()
    source_on_main(gh)
    op = await start(d)
    w = await settle(d, op["operation_id"], rounds=1)
    rid = w["external_refs"]["deploy_run_id"]
    completed(gh, rid)
    gh.jobs[rid] = [{"name": f"build-{i}", "conclusion": "success"} for i in range(100)] + [
        {"name": "deploy", "conclusion": "success"}
    ]
    assert (await settle(d, op["operation_id"]))["status"] == "succeeded"
    assert gh.count("GET", "/attempts/1/jobs") == 2


async def test_d03_health_only_recipe_states_exactly_what_was_proven(make_daemon, gh):
    d = make_daemon()
    source_on_main(gh)
    cfg = d.ops.context["github_config"]
    r = cfg.recipes["prod"]
    cfg.recipes["prod"] = replace(r, verification=replace(r.verification, version_required=False))
    d.ops.context["deployment_verifier"].response = {
        "repository_id": 4242,
        "environment": "production",
        "healthy": True,
    }
    done = await deployed(d, gh)
    evidence = done["result"]["evidence"]["runtime"]
    assert evidence["health_checked"] and not evidence["version_checked"]
    assert "runtime version not checked" in evidence["summary"]


async def test_d03_runtime_unavailable_expires_as_unproven(make_daemon, gh):
    d = make_daemon()
    source_on_main(gh)
    op = await start(d)
    w = await settle(d, op["operation_id"], rounds=1)
    completed(gh, w["external_refs"]["deploy_run_id"])
    d.ops.context["deployment_verifier"].response = {"waiting": "unavailable"}
    await settle(d, op["operation_id"], rounds=1)
    refs = d.ops.get(op["operation_id"])["external_refs"]
    refs["deploy_verification_wait_started_at"] = 0
    d.journal.db.execute(
        "UPDATE operations SET external_refs=? WHERE operation_id=?", (json.dumps(refs), op["operation_id"])
    )
    done = await settle(d, op["operation_id"])
    assert done["status"] == "needs_attention" and done["error_code"] == "DEPLOY_VERSION_UNPROVEN"
    assert gh.count("POST", "dispatches") == 1


async def test_issue32_on_merge_locate_requires_recipe_branch_repository_and_workflow(make_daemon, gh):
    d = make_daemon(mode="on_merge")
    source_on_main(gh)
    gh.add_run(
        head_sha=MERGED,
        branch="staging",
        event="push",
        status="completed",
        conclusion="success",
        job_conclusion="success",
    )
    gh.add_run(
        head_sha=MERGED,
        workflow_id=456,
        event="push",
        status="completed",
        conclusion="success",
        job_conclusion="success",
    )
    gh.add_run(
        head_sha=MERGED,
        repository_id=111,
        event="push",
        status="completed",
        conclusion="success",
        job_conclusion="success",
    )
    op = await start(d)
    w = await settle(d, op["operation_id"], rounds=1)
    assert w["status"] == "waiting_external" and "deploy_run_id" not in w["external_refs"]
    right = gh.add_run(
        head_sha=MERGED, event="push", status="completed", conclusion="success", job_conclusion="success"
    )
    done = await settle(d, op["operation_id"])
    assert done["status"] == "succeeded" and done["result"]["run_id"] == right["id"]
    assert gh.count("POST", "dispatches") == 0


@pytest.mark.parametrize("kind", ["start", "retry", "rollback"])
async def test_issue32_source_must_be_reachable_from_recipe_ref(make_daemon, gh, kind):
    d = make_daemon()
    source_on_main(gh)
    cfg = d.ops.context["github_config"]
    cfg.recipes["prod"] = replace(
        cfg.recipes["prod"], rollback=replace(cfg.recipes["prod"].rollback, supported=True)
    )
    if kind == "start":
        gh.commits[NEW] = {"sha": NEW, "parents": [{"sha": "b" * 40}]}
        sha, params, action = NEW, None, "deployment.start"
    else:
        old = await deployed(d, gh)
        dep_id = old["result"]["deployment_id"]
        gh.branches["main"] = "b" * 40
        sha = MERGED
        params = {"deployment_id": dep_id} if kind == "rollback" else {"source_sha": sha, "retry_of": dep_id}
        action = "deployment.rollback" if kind == "rollback" else "deployment.start"
    before = gh.count("POST", "dispatches")
    op = await start(d, sha=sha, params=params, action=action, key="off-ref")
    done = await settle(d, op["operation_id"])
    assert done["error_code"] == "DEPLOY_SOURCE_NOT_ON_REF", done
    assert gh.count("POST", "dispatches") == before


async def test_issue32_combined_admission_requires_preview_base_on_recipe_ref(make_daemon, gh):
    d = make_daemon()
    gh.add_pr(7, HEAD)
    cfg = d.ops.context["github_config"]
    cfg.recipes["prod"] = replace(cfg.recipes["prod"], ref="staging")
    doc = (await delivery.pr_preview(d.ops, "o/r", 7))["merge_preview"]
    envelope = pr_delivery.merge_envelope(doc, recipe="prod")
    envelope["preconditions"].update((await deployment.preview(d.ops, "prod"))["preconditions"])
    with pytest.raises(OperationError) as e:
        d.ops.create(OPERATOR, **envelope, idempotency_key="wrong-base")
    assert e.value.code == "DEPLOY_SOURCE_NOT_ON_REF" and e.value.status == 422
    assert gh.count("PUT", "merge-async") == gh.count("POST", "dispatches") == 0


@pytest.mark.parametrize("combined", [False, True])
async def test_issue32_cancel_keeps_provider_slot_and_recipe_lock_until_terminal(make_daemon, gh, combined):
    d = make_daemon()
    if combined:
        gh.add_pr(7, HEAD)
        gh.merge_mode = "enqueue"
        doc = (await delivery.pr_preview(d.ops, "o/r", 7))["merge_preview"]
        env = pr_delivery.merge_envelope(doc, recipe="prod")
        env["preconditions"].update((await deployment.preview(d.ops, "prod"))["preconditions"])
        op = d.ops.create(OPERATOR, **env, idempotency_key="combined")[0]
    else:
        source_on_main(gh)
        op = await start(d)
    w = await settle(d, op["operation_id"], rounds=1)
    assert w["status"] == "waiting_external"
    dep_id = w["external_refs"]["deployment_id"]
    d.ops.cancel(OPERATOR, op["operation_id"])
    assert deployment.environment_status(d.ops, "prod")["slot_deployment_id"] == dep_id
    with pytest.raises(OperationError) as e:
        await start(d, key="blocked")
    assert e.value.code == "DEPLOY_IN_PROGRESS"
    await reconcile_due(d.ops)
    assert deployment.environment_status(d.ops, "prod")["slot_deployment_id"] == dep_id
    if combined:
        gh.merge(7)
    else:
        completed(gh, w["external_refs"]["deploy_run_id"])
    writes = gh.count("POST", "dispatches") + gh.count("PUT", "merge-async")
    await reconcile_due(d.ops)
    assert deployment.environment_status(d.ops, "prod")["slot_deployment_id"] is None
    assert d.ops.get(op["operation_id"])["status"] == "cancelled"
    assert gh.count("POST", "dispatches") + gh.count("PUT", "merge-async") == writes
    await start(d, key="admitted")


async def test_issue32_dispatch_429_waits_retry_after_and_adopts_before_resend(make_daemon, gh):
    d = make_daemon()
    source_on_main(gh)
    gh.script.append(("POST", "/dispatches$", 429, {"Retry-After": "2"}, {"message": "rate limited"}))
    op = await start(d)
    w = await settle(d, op["operation_id"], rounds=1)
    assert w["status"] == "waiting_external" and "Retry-After" in w["status_reason"]
    assert gh.count("POST", "dispatches") == 1 and not gh.runs
    await settle(d, op["operation_id"], rounds=1)
    assert gh.count("POST", "dispatches") == 1
    # The provider supplied the exact token while we were waiting: adopt it, never POST again.
    run = gh.add_run(
        title="deploy " + op["operation_id"],
        status="completed",
        conclusion="success",
        job_conclusion="success",
    )
    done = await settle(d, op["operation_id"])
    assert done["status"] == "succeeded" and done["result"]["run_id"] == run["id"]
    assert len(gh.runs) == gh.count("POST", "dispatches") == 1


async def test_issue32_dispatch_429_retries_only_after_backoff(make_daemon, gh):
    d = make_daemon()
    source_on_main(gh)
    gh.script.append(("POST", "/dispatches$", 429, {"Retry-After": "1"}, {}))
    op = await start(d)
    w = await settle(d, op["operation_id"], rounds=1)
    store.update(d.journal, w["external_refs"]["deployment_id"], facts={"dispatch_retry_at": 0})
    next_run = await settle(d, op["operation_id"], rounds=1)
    assert gh.count("POST", "dispatches") == 2 and len(gh.runs) == 1
    completed(gh, next_run["external_refs"]["deploy_run_id"])
    assert (await settle(d, op["operation_id"]))["status"] == "succeeded"


async def test_d05_environment_generation_is_atomic_across_recipes(make_daemon, gh):
    d = make_daemon()
    source_on_main(gh)
    cfg = d.ops.context["github_config"]
    cfg.recipes["alias"] = replace(cfg.recipes["prod"], name="alias", workflow="release.yml")
    gh.workflow_ids["release.yml"] = 456
    p = await deployment.preview(d.ops, "prod")
    a = await start(d, pre=p["preconditions"])
    alias_pre = {
        **p["preconditions"],
        "expected_recipe_digest": deployment.recipe_digest(d.ops, cfg.recipes["alias"], 4242),
    }
    b = await start(d, name="alias", pre=alias_pre, key="alias")
    wa = await settle(d, a["operation_id"], rounds=1)
    wb = await settle(d, b["operation_id"])
    assert wa["external_refs"]["environment_generation"] == 1
    assert wb["status"] == "failed" and wb["error_code"] == "ENVIRONMENT_CHANGED"
    assert deployment.environment_status(d.ops, "alias")["desired_generation"] == 1
    assert gh.count("POST", "dispatches") == 1


async def test_d05_late_old_run_is_superseded_never_current(make_daemon, gh):
    d = make_daemon()
    source_on_main(gh)
    cfg = d.ops.context["github_config"]
    cfg.recipes["alias"] = replace(cfg.recipes["prod"], name="alias")
    old = await start(d)
    wo = await settle(d, old["operation_id"], rounds=1)
    newer = await start(d, name="alias", key="new")
    wn = await settle(d, newer["operation_id"], rounds=1)
    assert wn["status"] == "waiting_external" and "waiting_order" in wn["status_reason"]
    completed(gh, wo["external_refs"]["deploy_run_id"])
    late = await settle(d, old["operation_id"])
    assert late["error_code"] == "DEPLOY_SUPERSEDED"
    assert deployment.environment_status(d.ops, "prod")["current"] is None
    wn = await settle(d, newer["operation_id"], rounds=1)
    completed(gh, wn["external_refs"]["deploy_run_id"])
    done = await settle(d, newer["operation_id"])
    assert done["status"] == "succeeded"
    await reconcile_due(d.ops)
    assert (
        deployment.environment_status(d.ops, "prod")["current"]["deployment_id"]
        == done["result"]["deployment_id"]
    )
    assert deployment.status(d.ops, wo["external_refs"]["deployment_id"])["state"] == "superseded"
    assert gh.count("POST", "dispatches") == 2


async def test_d05_runtime_drift_invalidates_current_without_redispatch(make_daemon, gh):
    d = make_daemon()
    source_on_main(gh)
    done = await deployed(d, gh)
    gh.deployed_source = NEW
    await reconcile_due(d.ops)
    env = deployment.environment_status(d.ops, "prod")
    assert env["current"] is None and env["attention"] == "ENVIRONMENT_VERSION_DRIFT"
    assert env["last_verified"]["deployment_id"] == done["result"]["deployment_id"]
    before = d.journal.db.execute("SELECT count(*) FROM api_events WHERE kind='deployment.drift'").fetchone()[
        0
    ]
    await reconcile_due(d.ops)
    assert (
        d.journal.db.execute("SELECT count(*) FROM api_events WHERE kind='deployment.drift'").fetchone()[0]
        == before
        == 1
    )
    assert gh.count("POST", "dispatches") == 1


async def test_d05_restart_and_cancel_keep_provider_slot_and_desired(make_daemon, gh):
    d = make_daemon()
    source_on_main(gh)
    op = await start(d)
    w = await settle(d, op["operation_id"], rounds=1)
    d.ops.cancel(OPERATOR, op["operation_id"])
    fresh = OperationService(d.journal, actions=delivery.ACTIONS)
    fresh.context.update(d.ops.context)
    d.ops = fresh
    await reconcile_due(d.ops)
    env = deployment.environment_status(d.ops, "prod")
    assert env["desired_generation"] == 1 and env["slot_deployment_id"] == w["external_refs"]["deployment_id"]
    completed(gh, w["external_refs"]["deploy_run_id"])
    await reconcile_due(d.ops)
    env = deployment.environment_status(d.ops, "prod")
    assert env["slot_deployment_id"] is None and env["desired_generation"] == 1
    assert d.ops.get(op["operation_id"])["status"] == "cancelled"
    assert gh.count("POST", "dispatches") == 1


async def test_d05_combined_selects_generation_then_binds_real_merge_sha(make_daemon, gh):
    d = make_daemon()
    gh.add_pr(7, HEAD)
    gh.merge_mode = "enqueue"
    doc = (await delivery.pr_preview(d.ops, "o/r", 7))["merge_preview"]
    e = pr_delivery.merge_envelope(doc, recipe="prod")
    e["preconditions"].update((await deployment.preview(d.ops, "prod"))["preconditions"])
    op = d.ops.create(OPERATOR, **e, idempotency_key="combined")[0]
    w = await settle(d, op["operation_id"], rounds=1)
    dep_id = w["external_refs"]["deployment_id"]
    assert deployment.status(d.ops, dep_id)["identity"] == {"source_pending_merge": True}
    assert deployment.environment_status(d.ops, "prod")["desired_generation"] == 1
    gh.merge(7)
    w = await settle(d, op["operation_id"], rounds=1)
    assert deployment.status(d.ops, dep_id)["source_sha"] == MERGED
    completed(gh, w["external_refs"]["deploy_run_id"])
    assert (await settle(d, op["operation_id"]))["status"] == "succeeded"
    assert gh.count("PUT", "merge-async") == gh.count("POST", "dispatches") == 1


async def test_deploy_preview_required_and_missing_verifier_disable_writes_but_not_history(make_daemon):
    d = make_daemon()
    with pytest.raises(OperationError) as e:
        d.ops.create(
            OPERATOR,
            action="deployment.start",
            target={"recipe": "prod"},
            params={"source_sha": MERGED},
            idempotency_key="old",
        )
    assert e.value.code == "DEPLOY_PREVIEW_REQUIRED" and "deployment_preview" in e.value.message
    cfg = d.ops.context["github_config"]
    cfg.recipes["prod"] = replace(cfg.recipes["prod"], verification=None)
    p = await deployment.preview(d.ops, "prod")
    assert p["readiness"] == {
        "ready": False,
        "missing": ["verification"],
        "code": "DEPLOY_VERIFICATION_REQUIRED",
    }
    with pytest.raises(OperationError) as e:
        await start(d)
    assert e.value.code == "DEPLOY_VERIFICATION_REQUIRED"
    assert deployment.history(d.ops, "prod")["items"] == []


async def test_repository_identity_and_recipe_are_rechecked_before_dispatch(make_daemon, gh):
    d = make_daemon()
    source_on_main(gh)
    op = await start(d)
    gh.repository_id += 1
    done = await settle(d, op["operation_id"])
    assert done["error_code"] == "REPOSITORY_ID_CHANGED"
    assert gh.count("POST", "dispatches") == 0


async def test_d05_undispatched_selection_superseded_has_zero_post(make_daemon, gh):
    d = make_daemon()
    source_on_main(gh)
    cfg = d.ops.context["github_config"]
    cfg.recipes["alias"] = replace(cfg.recipes["prod"], name="alias")
    old = await start(d)
    # A transient source read pauses before any dispatch, after selection.
    gh.script.append(("GET", "/compare/main", 502, {}, {}))
    w = await settle(d, old["operation_id"], rounds=1)
    assert w["status"] == "waiting_external" and gh.count("POST", "dispatches") == 0
    new = await start(d, name="alias", key="new")
    await settle(d, new["operation_id"], rounds=1)
    done = await settle(d, old["operation_id"])
    assert done["error_code"] == "DEPLOY_SUPERSEDED"
    transitions = [e for e in d.journal.api_events(limit=200)["events"]
                   if e["kind"] == "deployment.updated" and e["body"]["operation_id"] == old["operation_id"]
                   and e["body"]["state"] == "superseded" and not e["body"].get("error_code")]
    assert len(transitions) == 1 and transitions[0]["body"]["provider_terminal"]
    assert gh.count("POST", "dispatches") == 1
    assert not any(
        b["inputs"]["operation_id"] == old["operation_id"] for m, p, b in gh.requests if m == "POST"
    )


async def test_d05_current_survives_old_verification_that_arrives_after_new_record(make_daemon, gh):
    d = make_daemon()
    source_on_main(gh)
    cfg = d.ops.context["github_config"]
    cfg.recipes["alias"] = replace(cfg.recipes["prod"], name="alias")
    old = await start(d)
    w = await settle(d, old["operation_id"], rounds=1)
    completed(gh, w["external_refs"]["deploy_run_id"])
    d.ops.context["deployment_verifier"].response = {"waiting": "temporarily unavailable"}
    # Provider is terminal, but the old operation has no runtime proof yet.
    await settle(d, old["operation_id"], rounds=1)
    d.ops.cancel(OPERATOR, old["operation_id"])
    await reconcile_due(d.ops)
    gh.commits[NEW] = {"sha": NEW, "parents": [{"sha": MERGED}]}
    gh.branches["main"] = NEW
    d.ops.context["deployment_verifier"].response = None
    newer = await start(d, name="alias", sha=NEW, key="new")
    n = await settle(d, newer["operation_id"], rounds=1)
    completed(gh, n["external_refs"]["deploy_run_id"], source=NEW)
    new = await settle(d, newer["operation_id"])
    assert new["status"] == "succeeded"
    await reconcile_due(d.ops)
    env = deployment.environment_status(d.ops, "prod")
    assert env["current"]["deployment_id"] == new["result"]["deployment_id"]
    assert deployment.status(d.ops, w["external_refs"]["deployment_id"])["state"] == "superseded"
    assert gh.count("POST", "dispatches") == 2


async def test_d05_independent_environments_have_independent_generations(make_daemon, gh):
    d = make_daemon()
    source_on_main(gh)
    cfg = d.ops.context["github_config"]
    cfg.recipes["staging"] = replace(cfg.recipes["prod"], name="staging", environment="staging")
    a = await start(d)
    b = await start(d, name="staging", key="staging")
    await settle(d, a["operation_id"], rounds=1)
    await settle(d, b["operation_id"], rounds=1)
    assert deployment.environment_status(d.ops, "prod")["desired_generation"] == 1
    assert deployment.environment_status(d.ops, "staging")["desired_generation"] == 1
    assert gh.count("POST", "dispatches") == 2


async def test_d03_external_rerun_invalidates_current_and_keeps_original_attempt(make_daemon, gh):
    d = make_daemon()
    source_on_main(gh)
    done = await deployed(d, gh)
    dep_id, rid = done["result"]["deployment_id"], done["result"]["run_id"]
    gh.runs[rid].update(run_attempt=2, status="in_progress", conclusion=None)
    await reconcile_due(d.ops)
    env = deployment.environment_status(d.ops, "prod")
    assert env["current"] is None and env["attention"] == "DEPLOY_ATTEMPT_CHANGED"
    assert env["slot_deployment_id"] == dep_id
    assert deployment.status(d.ops, dep_id)["run_attempt"] == 1
    assert d.ops.get(done["operation_id"])["status"] == "succeeded"
    assert gh.count("POST", "dispatches") == 1
    gh.runs[rid].update(status="completed", conclusion="success")
    await reconcile_due(d.ops)
    assert deployment.environment_status(d.ops, "prod")["slot_deployment_id"] is None


@pytest.mark.parametrize("answer", [401, 403, 404])
async def test_deploy_refused_read_after_dispatch_keeps_lock_and_resumes(make_daemon, gh, answer):
    d = make_daemon()
    source_on_main(gh)
    op = await start(d)
    w = await settle(d, op["operation_id"], rounds=1)
    rid = w["external_refs"]["deploy_run_id"]
    gh.script.append(("GET", f"/runs/{rid}$", answer, {}, {}))
    held = await settle(d, op["operation_id"])
    assert held["status"] == "needs_attention" and held["error_code"] == f"GITHUB_{answer}"
    with pytest.raises(OperationError) as e:
        await start(d, key="blocked")
    assert e.value.code == "DEPLOY_IN_PROGRESS"
    completed(gh, rid)
    d.ops.resume(OPERATOR, op["operation_id"])
    assert (await settle(d, op["operation_id"]))["status"] == "succeeded"
    assert gh.count("POST", "dispatches") == 1


async def test_d01_d02_204_requires_exact_unique_operation_token_and_never_redispatches(make_daemon, gh):
    d = make_daemon()
    source_on_main(gh)
    gh.dispatch_mode = "no_content"
    op = await start(d)
    w = await settle(d, op["operation_id"], rounds=1)
    rid = w["external_refs"]["deploy_run_id"]
    # Reopen association from its saved 204 receipt: near tokens cannot stand in for the operation.
    dep_id = w["external_refs"]["deployment_id"]
    store.update(d.journal, dep_id, run_id=None)
    gh.runs[rid]["display_title"] += "_suffix"
    not_found = await settle(d, op["operation_id"], rounds=1)
    assert not_found["status"] == "waiting_external"
    gh.runs[rid]["display_title"] = "deploy " + op["operation_id"]
    gh.add_run(title="deploy " + op["operation_id"])
    ambiguous = await settle(d, op["operation_id"])
    assert ambiguous["error_code"] == "DEPLOY_RUN_AMBIGUOUS"
    assert gh.count("POST", "dispatches") == 1


async def test_delivery_actions_never_mutate_manual_or_unknown_resources(make_daemon, gh, mock):
    d = make_daemon()
    source_on_main(gh)
    await deployed(d, gh)
    assert not mock.frames and not mock.invokes
    assert all("/repos/o/r" in p for _, p, _ in gh.requests)
    # Deployment actions neither invoke a host runner nor acquire a local Git writer.
    assert not d.journal.db.execute(
        "SELECT 1 FROM operations WHERE action LIKE 'session.%' OR action LIKE 'worktree.%'"
    ).fetchone()


async def test_legacy_dispatched_operation_reads_original_run_and_never_dispatches(make_daemon, gh):
    from tests.legacy_deployment_fixture import legacy_deployment

    d = make_daemon()

    old = legacy_deployment(
        d.journal, OPERATOR,
        action="deployment.start",
        target={"recipe": "prod"},
        params={"source_sha": MERGED},
        idempotency_key="old",
    )
    run = gh.add_run(status="completed", conclusion="success", job_conclusion="success")
    d.journal.db.execute(
        "UPDATE operations SET status='waiting_external',external_refs=? WHERE operation_id=?",
        (json.dumps({"deploy_run_id": run["id"]}), old["operation_id"]),
    )
    d.journal.db.execute(
        "INSERT INTO operation_steps(operation_id,seq,name,status,request,started_at) VALUES(?,1,'deploy.dispatch','succeeded','{}',1)",
        (old["operation_id"],),
    )
    d.journal.db.execute("PRAGMA user_version=2")
    store.backfill(d.journal)
    done = await settle(d, old["operation_id"])
    assert done["status"] == "needs_attention" and done["error_code"] == "DEPLOY_VERSION_UNPROVEN"
    dep = deployment.status(
        d.ops, done["external_refs"].get("deployment_id") or "dep_" + old["operation_id"][3:]
    )
    assert dep["state"] == "unverified" and not dep["is_current"] and not dep["rollback_eligible"]
    assert gh.count("POST", "dispatches") == 0 and gh.count("GET", f"/runs/{run['id']}") == 1


async def test_legacy_undispatched_operation_stops_for_deployment_preview(make_daemon, gh):
    from tests.legacy_deployment_fixture import legacy_deployment

    d = make_daemon()

    old = legacy_deployment(
        d.journal, OPERATOR,
        action="deployment.start",
        target={"recipe": "prod"},
        params={"source_sha": MERGED},
        idempotency_key="old",
    )
    d.journal.db.execute("PRAGMA user_version=2")
    store.backfill(d.journal)
    done = await settle(d, old["operation_id"])
    assert done["error_code"] == "DEPLOY_PREVIEW_REQUIRED" and gh.count("POST", "dispatches") == 0


async def test_issue32_cancelled_on_merge_wait_keeps_slot_until_the_exact_run_finishes(make_daemon, gh, monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(deployment, "reconcile_now", lambda: clock[0])
    d = make_daemon(mode="on_merge")
    source_on_main(gh)
    op = await start(d)
    w = await settle(d, op["operation_id"], rounds=1)
    d.ops.cancel(OPERATOR, op["operation_id"])
    await reconcile_due(d.ops)
    assert (
        deployment.environment_status(d.ops, "prod")["slot_deployment_id"]
        == w["external_refs"]["deployment_id"]
    )
    run = gh.add_run(head_sha=MERGED, event="push")
    clock[0] += 300
    await reconcile_due(d.ops)
    assert deployment.environment_status(d.ops, "prod")["slot_deployment_id"]
    completed(gh, run["id"])
    await reconcile_due(d.ops)
    assert deployment.environment_status(d.ops, "prod")["slot_deployment_id"] is None
    assert d.ops.get(op["operation_id"])["status"] == "cancelled" and gh.count("POST", "dispatches") == 0


async def test_issue32_dispatch_429_wait_is_bounded_without_early_resend(make_daemon, gh):
    d = make_daemon()
    source_on_main(gh)
    gh.script.append(("POST", "/dispatches$", 429, {"Retry-After": "3600"}, {}))
    op = await start(d)
    w = await settle(d, op["operation_id"], rounds=1)
    assert w["status"] == "waiting_external"
    assert w["next_run_at"] - w["updated_at"] <= 601
    refs = {**w["external_refs"], "deploy_dispatch_wait_started_at": 0}
    d.journal.db.execute(
        "UPDATE operations SET external_refs=? WHERE operation_id=?", (json.dumps(refs), op["operation_id"])
    )
    done = await settle(d, op["operation_id"])
    assert done["error_code"] == "WAIT_TIMEOUT" and gh.count("POST", "dispatches") == 1 and not gh.runs


async def test_recipe_change_blocks_new_dispatch_but_removed_recipe_cannot_stop_original_readback(
    make_daemon, gh
):
    d = make_daemon()
    source_on_main(gh)
    op = await start(d)
    cfg = d.ops.context["github_config"]
    r = cfg.recipes["prod"]
    cfg.recipes["prod"] = replace(r, deploy_job="different")
    refused = await settle(d, op["operation_id"])
    assert refused["error_code"] == "RECIPE_CHANGED" and gh.count("POST", "dispatches") == 0
    cfg.recipes["prod"] = r
    op = await start(d, key="sent")
    w = await settle(d, op["operation_id"], rounds=1)
    cfg.recipes.clear()
    completed(gh, w["external_refs"]["deploy_run_id"])
    assert (await settle(d, op["operation_id"]))["status"] == "succeeded"
    assert gh.count("POST", "dispatches") == 1


async def test_issue32_unknown_source_comparison_refuses_before_dispatch(make_daemon, gh):
    d = make_daemon()
    source_on_main(gh)
    op = await start(d, sha=NEW)
    done = await settle(d, op["operation_id"])
    assert done["error_code"] == "DEPLOY_SOURCE_NOT_ON_REF" and gh.count("POST", "dispatches") == 0
