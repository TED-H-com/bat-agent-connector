"""PM delivery uses real disk Git, fixed original packages and fake providers."""
from __future__ import annotations

import copy
import json
import sqlite3
import uuid
from dataclasses import replace

import pytest

from bat_agent_connector import delivery, deployment, integration, pm_delivery_lineage, pr_delivery
from bat_agent_connector import pm_source_lineage as lineage
from bat_agent_connector.api_auth import Principal
from bat_agent_connector.config import parse_config
from bat_agent_connector.github import GitHubAmbiguous
from bat_agent_connector.operations import OperationError
from tests.fakeverifier import FakeVerifier
from tests.operation_helpers import settle_operations
from tests.test_checkpoints import git, snapshot
from tests.test_integration import RaceRunner, commit
from tests.test_pm_lifecycle import decide
from tests.test_pm_package_handoff import fixed as fixed_fixture
from tests.test_pm_package_handoff import pm, ready, request, revoke, run, world  # noqa: F401
from tests.test_pm_plan_operations import OWNER
from tests.test_pm_work_packages import member

DELIVERER = Principal(OWNER.actor, OWNER.scopes | {"integrate", "merge", "deploy"})
TARGET = {"repository": "o/r", "host": "h1", "pull_number": 1}
fixed = fixed_fixture


def sync_provider_graph(w):
    for line in git(w["remote"], "rev-list", "--all", "--parents").splitlines():
        sha, *parents = line.split()
        w["gh"].commits[sha] = {"sha": sha, "parents": [{"sha": parent} for parent in parents],
            "commit": {"message": "Synthetic recorded Git commit"}}


@pytest.fixture
async def delivery_ready(fixed):
    w = fixed
    original = await run(w, await request(w))
    assert original["status"] == "succeeded", original
    w["original"] = original
    wt = original["result"]["worktree_path"]
    git(wt, "config", "user.name", "fixture")
    git(wt, "config", "user.email", "fixture@example.invalid")
    w["result_sha"] = commit(wt, "implementation.txt", "fixed selected implementation\n", "Implement selected package")
    git(w["remote"], "update-ref", "refs/heads/feature-1", w["state"]["sha"])
    git(w["remote"], "update-ref", "refs/pull/1/head", w["state"]["sha"])
    w["gh"].add_pr(1, w["state"]["sha"], head_ref="feature-1")
    w["gh"].pulls[1]["base"]["sha"] = w["state"]["sha"]
    w["gh"].branches["main"] = w["state"]["sha"]
    sync_provider_graph(w)
    w["gh"].track_remote(1, str(w["remote"]))
    raw = copy.deepcopy(w["raw"])
    raw["github"]["repos"][0]["integrate"] = {"hosts": ["h1"], "remote_url": str(w["remote"]), "workspace": "ws-1"}
    raw["deploy"] = {"recipes": [{"name": "prod", "repository": "o/r", "environment": "production",
        "mode": "workflow_dispatch", "workflow": "deploy.yml", "deploy_job": "deploy", "ref": "main",
        "inputs": {"source_sha": "source_sha", "operation_id": "operation_id"}, "run_name_contains": "operation_id",
        "verification": {"kind": "http_json", "url": "https://deployment.example/status",
            "version_required": True, "health_required": True}}]}
    w["d"].ops.context["github_config"] = parse_config(raw).github
    w["d"].ops.context["deployment_verifier"] = FakeVerifier(w["gh"])
    w["runner"] = RaceRunner()
    w["d"].ops.context["git_runner"] = w["runner"]
    return w


async def settle(w, op, rounds=8):
    for _ in range(rounds):
        w["d"].ops.db.execute("UPDATE operations SET next_run_at=0 WHERE operation_id=?", (op["operation_id"],))
        await settle_operations(w["d"].ops)
        op = w["d"].ops.get(op["operation_id"])
        if op["status"] in {"succeeded", "failed", "cancelled", "needs_attention"}:
            break
    assert op["error_code"] != "INTERNAL", op
    if op["action"] == "integration.apply" and op["status"] == "succeeded":
        sync_provider_graph(w)
    return op


def submit(w, action, target, params=None, pre=None, key=None):
    return w["d"].ops.create(DELIVERER, action=action, target=target, params=params or {},
        preconditions=pre or {}, idempotency_key=key or uuid.uuid4().hex)[0]


async def preview(w):
    op = await settle(w, submit(w, "integration.preview", TARGET,
        {"sources": [{"kind": "execution", "id": w["original"]["operation_id"]}]}))
    assert op["status"] == "succeeded", op
    assert not op["result"]["blocking"], op["result"]
    return op["result"]


def apply(w, pv):
    envelope = integration.apply_request(pv)
    return w["d"].ops.create(DELIVERER, **envelope)[0]


async def test_fixed_package_preview_apply_and_existing_reads_keep_original_source(delivery_ready):
    w = delivery_ready
    human_before = snapshot(w["human"])
    pv = await preview(w)
    op = await settle(w, apply(w, pv))
    assert op["status"] == "succeeded" and op["result"]["pushed"], op
    assert git(w["remote"], "rev-parse", "refs/heads/feature-1") == op["result"]["new_head"]
    root = w["original"]["operation_id"]
    assert lineage.roots(w["d"].ops.db, "delivery_preview", pv["preview_id"]) == {root}
    assert lineage.roots(w["d"].ops.db, "operation", op["operation_id"]) == {root}
    assert op["external_refs"]["admission_binding"]["pm_delivery"]["contract"] == pm_delivery_lineage.CONTRACT
    assert snapshot(w["human"]) == human_before
    current = await w["d"].call_api("integration_get", {"operation_id": op["operation_id"]}, DELIVERER)
    assert current["operation"]["operation_id"] == op["operation_id"]
    requests = len(w["gh"].requests), len(w["runner"].scripts), w["d"].ops.db.execute("SELECT COUNT(*) FROM operations").fetchone()[0]
    history = pm_delivery_lineage.for_work(w["d"].ops, DELIVERER, w["workspace_id"], w["target"]["work_item_id"], limit=1)
    assert history["original_packages"] == [{"operation_id": root, **w["original"]["external_refs"]["admission_binding"]["pm_handoff"]["contract"]["package_ref"]}]
    assert history["deliveries"][0]["action"] == "integration.preview" and type(history["next_after"]) is int
    second = pm_delivery_lineage.for_work(w["d"].ops, DELIVERER, w["workspace_id"], w["target"]["work_item_id"], after=history["next_after"], limit=1)
    assert second["next_after"] is None and second["deliveries"][0]["operation_id"] == op["operation_id"]
    assert second["deliveries"][0]["integration"]["pushed"] is True
    assert second["deliveries"][0]["requirement_acceptance"] == "not_evaluated_by_delivery"
    assert pm_delivery_lineage.for_work(w["d"].ops, DELIVERER, w["workspace_id"], w["target"]["work_item_id"], after=2**63 - 1)["deliveries"] == []
    assert requests == (len(w["gh"].requests), len(w["runner"].scripts), w["d"].ops.db.execute("SELECT COUNT(*) FROM operations").fetchone()[0])
    revoke(w)
    with pytest.raises(OperationError):
        pm_delivery_lineage.for_work(w["d"].ops, DELIVERER, w["workspace_id"], w["target"]["work_item_id"])
    with pytest.raises(OperationError) as caught:
        await w["d"].call_api("integration_get", {"operation_id": op["operation_id"]}, DELIVERER)
    assert caught.value.code == "NOT_FOUND"
    with pytest.raises(OperationError):
        lineage.read_response(w["d"].ops.db, DELIVERER, integration.preview_document(w["d"].ops.db, pv["preview_id"]))
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        w["d"].ops.db.execute("DELETE FROM pm_delivery_links")


async def test_withdrawal_after_apply_admission_stops_new_host_effects(delivery_ready):
    w = delivery_ready
    pv = await preview(w)
    op = apply(w, pv)
    before = len(w["runner"].scripts)
    revoke(w)
    done = await settle(w, op)
    assert done["status"] == "failed" and done["error_code"] == "PM_HANDOFF_SOURCE_UNAVAILABLE", done
    assert len(w["runner"].scripts) == before


async def test_lost_push_reply_reconciles_original_after_withdrawal_without_resend(delivery_ready):
    w = delivery_ready
    pv = await preview(w)
    w["runner"].lose_after.add("push")
    w["runner"].after["push"] = lambda: revoke(w)
    done = await settle(w, apply(w, pv))
    assert done["status"] == "succeeded" and done["result"]["pushed"], done
    assert w["runner"].ran["push"] == 1
    assert all(row["effective_status"] == "delivered" for row in integration.receipts(w["d"].ops.db, done["operation_id"]))
    with pytest.raises(OperationError):
        w["d"].ops.create(DELIVERER, **integration.apply_request(pv))


async def test_merge_uses_original_preview_and_preserves_lost_reply_after_withdrawal(delivery_ready, monkeypatch):
    w = delivery_ready
    pushed = await settle(w, apply(w, await preview(w)))
    assert pushed["status"] == "succeeded", pushed
    card = await delivery.pr_preview(w["d"].ops, "o/r", 1)
    pv = card["merge_preview"]
    env = pr_delivery.merge_envelope(pv)
    env["idempotency_key"] = "fixed-pm-merge"
    op, _ = w["d"].ops.create(DELIVERER, **env)
    assert lineage.roots(w["d"].ops.db, "operation", op["operation_id"]) == {w["original"]["operation_id"]}
    client = w["d"].ops.context["github"]
    merge = client.merge_async
    async def lose(*args):
        await merge(*args)
        w["gh"].merge(1)
        revoke(w)
        raise GitHubAmbiguous("Original acknowledged merge reply was lost")
    monkeypatch.setattr(client, "merge_async", lose)
    done = await settle(w, op)
    assert done["status"] == "succeeded" and done["result"]["verified"], (done["error_code"], done["status_reason"], done["steps"])
    assert w["gh"].count("PUT", "merge-async") == 1
    key = pm_delivery_lineage.identity(w["gh"].url, w["gh"].repository_id, done["result"]["merged_sha"])
    assert lineage.roots(w["d"].ops.db, "git_revision", key) == {w["original"]["operation_id"]}
    with pytest.raises(OperationError):
        lineage.read_response(w["d"].ops.db, DELIVERER, card)
    # Cached merge previews may expire, but their source policy must not.
    w["d"].ops.db.execute("DELETE FROM pr_merge_previews WHERE preview_id=?", (pv["preview_id"],))
    assert lineage.roots(w["d"].ops.db, "pr_merge_preview", pv["preview_id"]) == {w["original"]["operation_id"]}
    assert not lineage.visible_roots(w["d"].ops.db, DELIVERER, {w["original"]["operation_id"]})


async def test_wrong_native_repository_is_refused_before_host_mutation(delivery_ready):
    w = delivery_ready
    w["gh"].pulls[1]["base"]["repo"]["id"] += 1
    op = await settle(w, submit(w, "integration.preview", TARGET,
        {"sources": [{"kind": "execution", "id": w["original"]["operation_id"]}]}))
    assert op["error_code"] == "PM_DELIVERY_TARGET_CHANGED", op
    assert not w["runner"].scripts
    assert "PM_DELIVERY_TARGET_CHANGED" in json.dumps(op)


async def test_verified_merge_revision_binds_deployment_and_recovers_original_dispatch(delivery_ready, monkeypatch):
    w = delivery_ready
    assert (await settle(w, apply(w, await preview(w))))["status"] == "succeeded"
    pv = (await delivery.pr_preview(w["d"].ops, "o/r", 1))["merge_preview"]
    env = {**pr_delivery.merge_envelope(pv), "idempotency_key": "merge-for-deploy"}
    merged = await settle(w, w["d"].ops.create(DELIVERER, **env)[0])
    assert merged["status"] == "succeeded", (merged["error_code"], merged["status_reason"])
    deployment_preview = await deployment.preview(w["d"].ops, "prod")
    dep = submit(w, "deployment.start", {"recipe": "prod"}, {"source_sha": merged["result"]["merged_sha"]},
        deployment_preview["preconditions"])
    root = w["original"]["operation_id"]
    assert lineage.roots(w["d"].ops.db, "operation", dep["operation_id"]) == {root}
    client = w["d"].ops.context["github"]
    dispatch = client.dispatch
    async def lose(*args):
        await dispatch(*args)
        revoke(w)
        raise GitHubAmbiguous("Original dispatched workflow reply was lost")
    monkeypatch.setattr(client, "dispatch", lose)
    pending = await settle(w, dep, rounds=2)
    assert pending["status"] == "waiting_external", pending
    run_id = pending["external_refs"]["deploy_run_id"]
    w["gh"].runs[run_id].update(status="completed", conclusion="success")
    w["gh"].jobs[run_id][1]["conclusion"] = "success"
    done = await settle(w, dep)
    assert done["status"] == "succeeded", (done["error_code"], done["status_reason"], done["steps"])
    assert w["gh"].count("POST", "dispatches") == 1
    did = done["result"]["deployment_id"]
    assert lineage.roots(w["d"].ops.db, "deployment", did) == {root}
    with pytest.raises(OperationError):
        lineage.read_response(w["d"].ops.db, DELIVERER, deployment.status(w["d"].ops, did))
    history = lineage.read_response(w["d"].ops.db, DELIVERER, deployment.history(w["d"].ops, "prod"))
    assert history["items"] == [] and history["source_visibility_filtered"]


async def test_pending_async_merge_stays_uncertain_after_withdrawal_then_recovers(delivery_ready, monkeypatch):
    w = delivery_ready
    await settle(w, apply(w, await preview(w)))
    pv = (await delivery.pr_preview(w["d"].ops, "o/r", 1))["merge_preview"]
    op, _ = w["d"].ops.create(DELIVERER, **pr_delivery.merge_envelope(pv), idempotency_key="pending-merge")
    client = w["d"].ops.context["github"]
    original = client.merge_async
    async def lose(*args):
        await original(*args)
        revoke(w)
        raise GitHubAmbiguous("Accepted asynchronous request, reply lost")
    monkeypatch.setattr(client, "merge_async", lose)
    pending = await settle(w, op, rounds=2)
    assert pending["status"] == "uncertain", (pending["error_code"], pending["status_reason"])
    assert w["gh"].count("PUT", "merge-async") == 1
    w["gh"].merge(1)
    done = await settle(w, op)
    assert done["status"] == "succeeded" and done["result"]["verified"]
    assert w["gh"].count("PUT", "merge-async") == 1


async def test_lifecycle_stop_after_merge_preserves_receipt_but_blocks_next_deploy(delivery_ready, monkeypatch):
    w = delivery_ready
    await settle(w, apply(w, await preview(w)))
    pv = (await delivery.pr_preview(w["d"].ops, "o/r", 1))["merge_preview"]
    env = pr_delivery.merge_envelope(pv, recipe="prod")
    env["preconditions"].update((await deployment.preview(w["d"].ops, "prod"))["preconditions"])
    op, _ = w["d"].ops.create(DELIVERER, **env, idempotency_key="merge-then-stop")
    client = w["d"].ops.context["github"]
    original = client.merge_async
    async def stop(*args):
        result = await original(*args)
        w["gh"].merge(1)
        await decide(w["d"].ops, w["workspace_id"], who=OWNER)
        return result
    monkeypatch.setattr(client, "merge_async", stop)
    done = await settle(w, op)
    assert done["error_code"] == "PM_LIFECYCLE_STOPPED", (done["status"], done["error_code"], done["status_reason"])
    assert done["external_refs"]["merge_receipt"]["verified"]
    assert w["gh"].count("PUT", "merge-async") == 1 and w["gh"].count("POST", "dispatches") == 0
    history = pm_delivery_lineage.for_work(w["d"].ops, DELIVERER, w["workspace_id"], w["target"]["work_item_id"])
    result = next(item for item in history["deliveries"] if item["operation_id"] == op["operation_id"])
    assert result["merge"]["merged"] is True and result["merge"]["verified"] is True
    assert result["deployment"]["verified"] is False
    assert result["requirement_acceptance"] == "not_evaluated_by_delivery"


async def test_contributor_delivery_keeps_scopes_and_current_membership(delivery_ready):
    w = delivery_ready
    contributor = Principal("delivery-contributor", frozenset({"observe", "integrate"}))
    await member(w, contributor.actor, "contributor")
    env = {"action": "integration.preview", "target": TARGET,
        "params": {"sources": [{"kind": "execution", "id": w["original"]["operation_id"]}]},
        "idempotency_key": "contributor-preview"}
    op, _ = w["d"].ops.create(contributor, **env)
    done = await settle(w, op)
    assert done["status"] == "succeeded", (done["error_code"], done["status_reason"])
    env = integration.apply_request(done["result"])
    op, _ = w["d"].ops.create(contributor, **env)
    w["d"].ops.db.execute("UPDATE pm_workspace_members SET role='reader' WHERE workspace_id=? AND actor=?",
        (w["workspace_id"], contributor.actor))
    before = len(w["runner"].scripts)
    done = await settle(w, op)
    assert done["status"] == "failed" and done["error_code"] == "FORBIDDEN", done
    assert len(w["runner"].scripts) == before


async def test_queued_ordinary_preview_cannot_gain_pm_target_authority(delivery_ready):
    w = delivery_ready
    ordinary = submit(w, "integration.preview", TARGET, {"sources": [{"kind": "branch", "id": "feature-1"}]})
    assert not lineage.roots(w["d"].ops.db, "operation", ordinary["operation_id"])
    # Binding the explicit PM selection is synchronous with its operation INSERT,
    # before either worker starts or any native target lookup returns.
    submit(w, "integration.preview", TARGET, {"sources": [{"kind": "execution", "id": w["original"]["operation_id"]}]})
    done = await settle(w, ordinary)
    assert done["error_code"] == "PM_DELIVERY_SOURCE_CHANGED", done
    assert "pr" not in done["external_refs"]
    assert not any(ordinary["operation_id"][3:15] in script for script in w["runner"].scripts)


async def test_queued_ordinary_apply_keeps_original_binding_after_target_becomes_pm(delivery_ready):
    w = delivery_ready
    external = w["human"].parent / "ordinary-external-source"
    git(external.parent, "clone", "-q", str(w["remote"]), str(external))
    git(external, "config", "user.name", "fixture")
    git(external, "config", "user.email", "fixture@example.invalid")
    git(external, "checkout", "-qb", "ordinary-source")
    commit(external, "external.txt", "Ordinary external change\n", "Independent external work")
    git(external, "push", "-q", "origin", "ordinary-source")
    ordinary = await settle(w, submit(w, "integration.preview", TARGET,
        {"sources": [{"kind": "branch", "id": "ordinary-source"}]}))
    assert ordinary["status"] == "succeeded", ordinary
    pv = ordinary["result"]
    operation = apply(w, pv)
    assert not lineage.roots(w["d"].ops.db, "operation", operation["operation_id"])
    submit(w, "integration.preview", TARGET, {"sources": [{"kind": "execution", "id": w["original"]["operation_id"]}]})
    done = await settle(w, operation)
    assert done["error_code"] == "PM_DELIVERY_SOURCE_CHANGED", done
    assert not any(operation["operation_id"][3:15] in script for script in w["runner"].scripts)
    # An old preview is still a saved native target, so its read policy follows
    # the current original sources even though its own creation was ordinary.
    assert lineage.roots(w["d"].ops.db, "delivery_preview", pv["preview_id"]) == {w["original"]["operation_id"]}
    revoke(w)
    with pytest.raises(OperationError):
        lineage.read_response(w["d"].ops.db, DELIVERER, {"preview": pv})


async def test_queued_ordinary_merge_cannot_send_after_target_becomes_pm(delivery_ready):
    w = delivery_ready
    pv = (await delivery.pr_preview(w["d"].ops, "o/r", 1))["merge_preview"]
    operation, _ = w["d"].ops.create(DELIVERER, **pr_delivery.merge_envelope(pv), idempotency_key="old-merge")
    submit(w, "integration.preview", TARGET, {"sources": [{"kind": "execution", "id": w["original"]["operation_id"]}]})
    done = await settle(w, operation)
    assert done["error_code"] == "PM_DELIVERY_SOURCE_CHANGED", done
    assert w["gh"].count("PUT", "merge-async") == 0


async def test_same_native_pr_cannot_bypass_sources_using_another_repository_name(delivery_ready, monkeypatch):
    w = delivery_ready
    await preview(w)
    cfg = w["d"].ops.context["github_config"]
    cfg.repos["o/renamed"] = replace(cfg.repos["o/r"], repository="o/renamed")
    client = w["d"].ops.context["github"]
    pull = client.pull
    async def alias(repository, number):
        return await pull("o/r" if repository == "o/renamed" else repository, number)
    monkeypatch.setattr(client, "pull", alias)
    op = submit(w, "integration.preview", {**TARGET, "repository": "o/renamed"},
        {"sources": [{"kind": "branch", "id": "feature-1"}]})
    assert not lineage.roots(w["d"].ops.db, "operation", op["operation_id"])
    before = len(w["runner"].scripts)
    done = await settle(w, op)
    assert done["error_code"] == "PM_DELIVERY_SOURCE_CHANGED", done
    assert "pr" not in done["external_refs"] and len(w["runner"].scripts) == before


async def test_pr_source_withdrawal_during_provider_read_discards_shared_response(delivery_ready, monkeypatch):
    w = delivery_ready
    await settle(w, apply(w, await preview(w)))
    client = w["d"].ops.context["github"]
    pull = client.pull
    withdrew = False
    async def withdraw(*args):
        nonlocal withdrew
        result = await pull(*args)
        if not withdrew:
            withdrew = True
            revoke(w)
        return result
    monkeypatch.setattr(client, "pull", withdraw)
    with pytest.raises(OperationError) as caught:
        await w["d"].call_api("github_pr_preview", {"repository": "o/r", "pull_number": 1}, DELIVERER)
    assert caught.value.code == "NOT_FOUND"
