"""Stopped deployment recovery and bounded read budgets (D03/D05, §09/§17/§28)."""
from __future__ import annotations

import json
from dataclasses import replace

import pytest

from bat_agent_connector import delivery, deployment
from bat_agent_connector import deployment_store as store
from bat_agent_connector.operations import OperationError, OperationService
from tests import test_delivery as fixtures
from tests.legacy_deployment_fixture import legacy_deployment
from tests.test_delivery import HEAD, MERGED, OPERATOR, settle
from tests.test_deployments import completed, deployed, reconcile_due, source_on_main, start

gh = fixtures.gh
make_daemon = fixtures.make_daemon


def legacy_operation(d, *, status="cancelled", error=None, dispatch=False, merge=False, run=None, result=None):
    action = "delivery.merge_and_deploy" if merge else "deployment.start"
    old = legacy_deployment(d.journal, OPERATOR, action=action, target={"recipe": "prod", "repository": "o/r", "pull_number": 7},
        params={"source_sha": MERGED}, preconditions={"expected_head_sha": HEAD}, idempotency_key="legacy")
    d.journal.db.execute("UPDATE operations SET status=?,error_code=?,result=?,external_refs=? WHERE operation_id=?",
        (status, error, json.dumps(result or {}), json.dumps({"deploy_run_id": run} if run else {}), old["operation_id"]))
    for seq, name in enumerate((["merge.submit"] if merge else []) + (["deploy.dispatch"] if dispatch else []), 1):
        d.journal.db.execute("INSERT INTO operation_steps(operation_id,seq,name,status,request,started_at) "
            "VALUES(?,?,?,'succeeded',?,1)", (old["operation_id"], seq, name, json.dumps({"sha": HEAD})))
    d.journal.db.execute("PRAGMA user_version=2")
    store.backfill(d.journal)
    return d.ops.get(old["operation_id"]), store.deployment(d.ops.db, operation_id=old["operation_id"])


@pytest.mark.parametrize("status,error,dispatch", [
    ("failed", "GITHUB_403", False), ("cancelled", None, False), ("failed", "DEPLOY_FAILED", True),
])
async def test_legacy_terminal_states_release_unsent_or_definitively_failed_recipe(make_daemon, gh, status, error, dispatch):
    d = make_daemon()
    source_on_main(gh)
    old, dep = legacy_operation(d, status=status, error=error, dispatch=dispatch)
    assert dep["state"] == status and dep["error_code"] == error and dep["provider_terminal"]
    assert dep["legacy_operation_status"] == status
    assert d.ops.db.execute("PRAGMA user_version").fetchone()[0] == 3
    new = await start(d, key="new")
    assert new["status"] == "accepted"
    await reconcile_due(d.ops)
    assert gh.count("GET", "/runs/") == gh.count("POST", "dispatches") == 0
    assert d.ops.get(old["operation_id"])["status"] == status


async def test_legacy_run_blocks_alias_of_real_environment_until_provider_terminal(make_daemon, gh):
    d = make_daemon()
    source_on_main(gh)
    cfg = d.ops.context["github_config"]
    cfg.recipes["alias"] = replace(cfg.recipes["prod"], name="alias")
    run = gh.add_run(head_sha=MERGED)
    old, dep = legacy_operation(d, dispatch=True, run=run["id"])
    with pytest.raises(OperationError) as e:
        await start(d, name="alias", key="blocked")
    assert e.value.code == "DEPLOY_IN_PROGRESS"
    assert deployment.legacy_occupant(d.ops, "O/R", "production")["deployment_id"] == dep["deployment_id"]
    bound = deployment.get(d.ops, dep["deployment_id"])
    assert bound["recipe_snapshot"]["environment"] == "production"
    assert bound["recipe_snapshot"]["mode"] == "workflow_dispatch"
    assert bound["legacy_binding_sources"]["environment"] == "configured_recipe"
    # Restore an already admitted alias intent to exercise the local slot guard independently.
    preview = await deployment.preview(d.ops, "alias")
    action = next(a for a in delivery.ACTIONS if a.name == "deployment.start")
    unguarded = OperationService(d.journal, actions=[replace(action, admit=None)])
    unguarded.context.update(d.ops.context)
    pending = unguarded.create(OPERATOR, action="deployment.start", target={"recipe": "alias"},
        params={"source_sha": MERGED}, preconditions=preview["preconditions"], idempotency_key="previously-admitted")[0]
    waiting = await settle(d, pending["operation_id"], rounds=1)
    assert waiting["status"] == "waiting_external" and "waiting_order" in waiting["status_reason"]
    assert gh.count("POST", "dispatches") == 0
    await reconcile_due(d.ops)
    assert not deployment.get(d.ops, dep["deployment_id"])["provider_terminal"]
    completed(gh, run["id"])
    await reconcile_due(d.ops)
    assert deployment.get(d.ops, dep["deployment_id"])["provider_terminal"]
    assert d.ops.get(old["operation_id"])["status"] == "cancelled"
    await settle(d, pending["operation_id"], rounds=1)
    assert gh.count("POST", "dispatches") == 1


@pytest.mark.parametrize("legacy", [False, True], ids=["admitted", "legacy"])
async def test_cancelled_combined_on_merge_holds_slot_through_late_push_run(make_daemon, gh, legacy, monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(deployment, "reconcile_now", lambda: clock[0])
    from bat_agent_connector import pr_delivery

    d = make_daemon(mode="on_merge")
    gh.add_pr(7, HEAD)
    if legacy:
        op, dep = legacy_operation(d, merge=True)
        dep_id = dep["deployment_id"]
    else:
        gh.merge_mode = "enqueue"
        doc = (await delivery.pr_preview(d.ops, "o/r", 7))["merge_preview"]
        envelope = pr_delivery.merge_envelope(doc, recipe="prod")
        envelope["preconditions"].update((await deployment.preview(d.ops, "prod"))["preconditions"])
        op = d.ops.create(OPERATOR, **envelope, idempotency_key="combined")[0]
        waiting = await settle(d, op["operation_id"], rounds=1)
        dep_id = waiting["external_refs"]["deployment_id"]
        d.ops.cancel(OPERATOR, op["operation_id"])
    writes = gh.count("PUT", "merge-async")
    await reconcile_due(d.ops)
    assert not deployment.get(d.ops, dep_id)["provider_terminal"]
    gh.merge(7)
    await reconcile_due(d.ops)
    dep = deployment.get(d.ops, dep_id)
    assert not dep["provider_terminal"] and dep["identity"]["source_sha"] == MERGED
    assert dep["merge_binding"]["reviewed_head_sha"] == HEAD
    with pytest.raises(OperationError) as e:
        await start(d, key="blocked")
    assert e.value.code == "DEPLOY_IN_PROGRESS"
    run = gh.add_run(head_sha=MERGED, event="push")
    clock[0] += 300
    await reconcile_due(d.ops)
    assert deployment.get(d.ops, dep_id)["run_id"] == run["id"]
    assert not deployment.get(d.ops, dep_id)["provider_terminal"]
    completed(gh, run["id"])
    await reconcile_due(d.ops)
    assert deployment.get(d.ops, dep_id)["provider_terminal"]
    await start(d, key="unblocked")
    assert d.ops.get(op["operation_id"])["status"] == "cancelled"
    assert gh.count("PUT", "merge-async") == writes and gh.count("POST", "dispatches") == 0


async def test_legacy_cancelled_dispatch_merge_uses_config_mode_and_never_locates_push(make_daemon, gh):
    d = make_daemon()
    gh.add_pr(7, HEAD)
    op, dep = legacy_operation(d, merge=True)
    gh.merge(7)
    await reconcile_due(d.ops)
    assert deployment.get(d.ops, dep["deployment_id"])["provider_terminal"]
    assert deployment.get(d.ops, dep["deployment_id"])["recipe_snapshot"]["mode"] == "workflow_dispatch"
    assert gh.count("GET", "/runs") == gh.count("POST", "dispatches") == gh.count("PUT", "merge-async") == 0
    assert d.ops.get(op["operation_id"])["status"] == "cancelled"


@pytest.mark.parametrize("case", ["wrong_head", "off_ref", "closed"])
async def test_cancelled_legacy_on_merge_requires_reviewed_head_and_ref_or_unmerged_close(make_daemon, gh, case):
    d = make_daemon(mode="on_merge")
    pr = gh.add_pr(7, HEAD)
    _, dep = legacy_operation(d, merge=True)
    if case == "closed":
        pr["state"] = "closed"
    else:
        gh.merge(7)
        if case == "wrong_head":
            pr["head"]["sha"] = "1" * 40
        else:
            gh.branches["main"] = "b" * 40
    await reconcile_due(d.ops)
    assert deployment.get(d.ops, dep["deployment_id"])["provider_terminal"] == (case == "closed")
    assert gh.count("GET", "/runs") == gh.count("POST", "dispatches") == gh.count("PUT", "merge-async") == 0


async def test_reconcile_budget_skips_twenty_settled_rows_and_polls_current_once_per_cadence(make_daemon, gh, monkeypatch):
    d = make_daemon()
    source_on_main(gh)
    done = await deployed(d, gh)
    current = deployment.get(d.ops, done["result"]["deployment_id"])
    template = dict(d.ops.db.execute("SELECT * FROM deployments WHERE deployment_id=?", (current["deployment_id"],)).fetchone())

    for n in range(20):
        old = legacy_deployment(d.journal, OPERATOR, action="deployment.start", target={"recipe": "prod"},
                                idempotency_key=f"history-{n}")
        d.ops.db.execute("UPDATE operations SET status='succeeded' WHERE operation_id=?", (old["operation_id"],))
        row = {**template, "deployment_id": "dep_" + old["operation_id"][3:], "operation_id": old["operation_id"],
               "generation": -n, "run_id": 9000 + n}
        d.ops.db.execute("INSERT INTO deployments (" + ",".join(row) + ") VALUES(" + ",".join("?" for _ in row) + ")", tuple(row.values()))
    before = [tuple(r) for r in d.ops.db.execute("SELECT deployment_id,version,updated_at FROM deployments ORDER BY deployment_id")]
    env_before = dict(d.ops.db.execute("SELECT * FROM deployment_environments WHERE environment_key=?", (current["environment_key"],)).fetchone())
    verifier = d.ops.context["deployment_verifier"]
    calls = verifier.calls
    gh.requests.clear()
    clock = [1000.0]
    monkeypatch.setattr(deployment, "reconcile_now", lambda: clock[0])
    await reconcile_due(d.ops)
    clock[0] += 10
    fresh = OperationService(d.journal, actions=delivery.ACTIONS)
    fresh.context.update(d.ops.context)
    for _ in range(5):
        await delivery.reconcile_deployments(fresh)
    assert gh.count("GET", "/runs/") == gh.count("GET", f"/runs/{current['run_id']}$") == 1
    assert gh.count("GET", "jobs|pending_deployments|workflows") == 0
    assert verifier.calls - calls == 1
    clock[0] = 1300
    await delivery.reconcile_deployments(fresh)
    assert gh.count("GET", "/runs/") == 2 and verifier.calls - calls == 2
    assert [tuple(r) for r in d.ops.db.execute("SELECT deployment_id,version,updated_at FROM deployments ORDER BY deployment_id")] == before
    assert dict(d.ops.db.execute("SELECT * FROM deployment_environments WHERE environment_key=?", (current["environment_key"],)).fetchone()) == env_before


async def test_lost_dispatch_locate_is_throttled_across_restart_and_filters_saved_send_time(make_daemon, gh, monkeypatch):
    d = make_daemon()
    source_on_main(gh)
    gh.script.append(("POST", "/dispatches$", 500, {}, {}))
    op = await start(d)
    waiting = await settle(d, op["operation_id"])
    assert waiting["status"] == "needs_attention"
    d.ops.cancel(OPERATOR, op["operation_id"])
    dep = store.deployment(d.ops.db, operation_id=op["operation_id"])
    assert dep["dispatch_sent_at"] and not dep["run_id"]
    for _ in range(250):
        run = gh.add_run(title="older")
        run["created_at"] = "2020-01-01T00:00:00Z"
    gh.requests.clear()
    clock = [1000.0]
    monkeypatch.setattr(deployment, "reconcile_now", lambda: clock[0])
    await reconcile_due(d.ops)
    version = deployment.get(d.ops, dep["deployment_id"])["version"]
    clock[0] += 10
    fresh = OperationService(d.journal, actions=delivery.ACTIONS)
    fresh.context.update(d.ops.context)
    await delivery.reconcile_deployments(fresh)
    assert gh.count("GET", "workflows/.*/runs") == 1
    assert "created=%3E%3D" in gh.requests[0][1]
    assert deployment.get(d.ops, dep["deployment_id"])["version"] == version
    run = gh.add_run(head_sha=MERGED, title=op["operation_id"])
    clock[0] = 1300
    await delivery.reconcile_deployments(fresh)
    assert deployment.get(d.ops, dep["deployment_id"])["run_id"] == run["id"]
    assert gh.count("POST", "dispatches") == gh.count("PUT", "merge-async") == 0


async def test_locate_refuses_github_truncated_filtered_search(make_daemon, gh):
    from bat_agent_connector.operations import NeedsAttention

    d = make_daemon()
    source_on_main(gh)
    op = await start(d)
    waiting = await settle(d, op["operation_id"], rounds=1)
    dep = deployment.get(d.ops, waiting["external_refs"]["deployment_id"])
    for _ in range(1000):
        gh.add_run(title="another operation")
    gh.requests.clear()
    with pytest.raises(NeedsAttention) as e:
        await deployment.locate(d.ops, dep)
    assert e.value.code == "DEPLOY_RUN_AMBIGUOUS"
    assert gh.count("GET", "workflows/.*/runs") == 1
    assert gh.count("POST", "dispatches") == 0


async def test_current_reconcile_cadence_is_shared_when_current_changes(make_daemon, gh, monkeypatch):
    d = make_daemon()
    source_on_main(gh)
    await deployed(d, gh)
    clock = [1000.0]
    monkeypatch.setattr(deployment, "reconcile_now", lambda: clock[0])
    await reconcile_due(d.ops)
    second = await deployed(d, gh, key="second")
    clock[0] = 1010
    gh.requests.clear()
    verifier = d.ops.context["deployment_verifier"]
    calls = verifier.calls
    await reconcile_due(d.ops)
    assert not gh.requests and verifier.calls == calls
    clock[0] = 1300
    await reconcile_due(d.ops)
    assert gh.count("GET", f"/runs/{second['result']['run_id']}$") == 1
    assert verifier.calls == calls + 1


async def test_stopped_provider_cadence_bounds_hour_and_resets_on_state_change(make_daemon, gh, monkeypatch):
    d = make_daemon()
    source_on_main(gh)
    op = await start(d)
    waiting = await settle(d, op["operation_id"], rounds=1)
    dep_id, rid = waiting["external_refs"]["deployment_id"], waiting["external_refs"]["deploy_run_id"]
    completed(gh, rid)
    gh.pending_deployments[rid] = [{"environment": {"name": "production"}}]
    d.ops.cancel(OPERATOR, op["operation_id"])
    clock = [1000.0]
    monkeypatch.setattr(deployment, "reconcile_now", lambda: clock[0])
    gh.requests.clear()
    for second in range(0, 3601, 10):
        clock[0] = 1000 + second
        fresh = OperationService(d.journal, actions=delivery.ACTIONS)
        fresh.context.update(d.ops.context)
        await delivery.reconcile_deployments(fresh)
    assert 12 <= gh.count("GET", f"/runs/{rid}$") <= 18
    assert len(gh.requests) <= 54  # run + one jobs page + pending approvals, never every 10 seconds
    assert deployment.get(d.ops, dep_id)["provider_terminal"] is False
    row = d.ops.db.execute("SELECT * FROM deployment_reconcile_reads WHERE read_key=?", ("provider:" + dep_id,)).fetchone()
    assert row["interval_s"] == 300
    gh.runs[rid]["status"] = "in_progress"
    clock[0] = row["checked_at"] + row["interval_s"]
    await delivery.reconcile_deployments(d.ops)
    assert d.ops.db.execute("SELECT interval_s FROM deployment_reconcile_reads WHERE read_key=?", ("provider:" + dep_id,)).fetchone()[0] == 15
    completed(gh, rid)
    gh.pending_deployments[rid] = []
    clock[0] += 14
    await delivery.reconcile_deployments(d.ops)
    assert deployment.environment_status(d.ops, "prod")["slot_deployment_id"] == dep_id
    clock[0] += 1
    await delivery.reconcile_deployments(d.ops)
    assert deployment.environment_status(d.ops, "prod")["slot_deployment_id"] is None
    assert gh.count("POST", "dispatches") == 0


async def test_reconcile_success_clears_error_once_without_unchanged_version_writes(make_daemon, gh, monkeypatch):
    d = make_daemon()
    source_on_main(gh)
    op = await start(d)
    waiting = await settle(d, op["operation_id"], rounds=1)
    d.ops.cancel(OPERATOR, op["operation_id"])
    dep_id, rid = waiting["external_refs"]["deployment_id"], waiting["external_refs"]["deploy_run_id"]
    clock = [1000.0]
    monkeypatch.setattr(deployment, "reconcile_now", lambda: clock[0])
    gh.script.append(("GET", f"/runs/{rid}$", 403, {}, {}))
    await delivery.reconcile_deployments(d.ops)
    assert deployment.get(d.ops, dep_id)["reconciliation_error"] == "GITHUB_403"
    clock[0] += 15
    await delivery.reconcile_deployments(d.ops)
    assert deployment.get(d.ops, dep_id)["reconciliation_error"] is None
    version = deployment.get(d.ops, dep_id)["version"]
    clock[0] += 15
    await delivery.reconcile_deployments(d.ops)
    assert deployment.get(d.ops, dep_id)["version"] == version


async def test_reconcile_bad_row_logs_and_continues_without_repeated_version_writes(make_daemon, gh, monkeypatch, caplog):
    d = make_daemon()
    source_on_main(gh)
    cfg = d.ops.context["github_config"]
    cfg.recipes["staging"] = replace(cfg.recipes["prod"], name="staging", environment="staging")
    a, b = await start(d), await start(d, name="staging", key="staging")
    wa, wb = await settle(d, a["operation_id"], rounds=1), await settle(d, b["operation_id"], rounds=1)
    for op in (a, b):
        d.ops.cancel(OPERATOR, op["operation_id"])
    bad = wa["external_refs"]["deployment_id"]
    original = deployment.observe
    async def observe(ops, dep, *args, **kwargs):
        if dep["deployment_id"] == bad:
            raise RuntimeError("synthetic failure")
        return await original(ops, dep, *args, **kwargs)
    monkeypatch.setattr(deployment, "observe", observe)
    clock = [1000.0]
    monkeypatch.setattr(deployment, "reconcile_now", lambda: clock[0])
    gh.requests.clear()
    await delivery.reconcile_deployments(d.ops)
    assert deployment.get(d.ops, bad)["reconciliation_error"] == "RECONCILE_FAILED"
    assert "RuntimeError" in caplog.text
    assert gh.count("GET", f"/runs/{wb['external_refs']['deploy_run_id']}$") == 1
    version = deployment.get(d.ops, bad)["version"]
    clock[0] += 15
    await delivery.reconcile_deployments(d.ops)
    assert deployment.get(d.ops, bad)["version"] == version
