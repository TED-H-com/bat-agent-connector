"""Real policy decisions and stale-worker/source boundaries; disk journal only."""
import json
import os
import sqlite3
import subprocess
import sys
import uuid

import pytest

from bat_agent_connector import pm_access, pm_lifecycle_gates, pm_workspace
from bat_agent_connector import pm_maintenance_policy as policies
from bat_agent_connector.api_auth import Principal
from bat_agent_connector.operations import OpContext, OperationError
from tests.test_pm_evidence import CONTRIBUTOR, OWNER, READER
from tests.test_pm_lifecycle import decide
from tests.test_pm_release_baselines import (
    base_ops,  # noqa: F401 - fixture dependency
    baselines,
    execute,
    member,
    remove_repo,
    setup,
)
from tests.test_pm_release_baselines import ops as release_ops
from tests.test_pm_release_baselines import (
    request as baseline_request,
)

baseline_ops = release_ops


@pytest.fixture
def ops(baseline_ops):
    policies.install(baseline_ops)
    return baseline_ops


def policy():
    return {
        "enabled": True, "after_release_policy": "maintain_when_idle",
        "sources": ["issues", "pull_requests", "dependencies"],
        "allowed_work_kinds": ["bug_fix", "documentation"], "allowed_paths": ["src/", "docs/"],
        "delivery": {"maximum": "pull_request", "environment": None},
        "observation": {"max_age_seconds": 1800, "stable_for_seconds": 300},
        "schedule": {"timezone": "America/New_York", "check_interval_seconds": 3600,
                     "review_weekday": 0, "review_local_time": "09:00"},
        "limits": {"max_parallel": 2, "run_seconds": 1200, "period_seconds": 86400,
                   "period_runs": 4, "cost_microusd": 10_000_000, "retries": 1},
        "verification": {"required_ci": True, "review_required": True,
                         "acceptance_evidence": ["Reproduce the reported failure on the maintained version"]},
    }


async def prepared(ops, *, branch="support/1.x"):
    target, binding, project_id = await setup(ops)
    await execute(ops, baseline_request(ops, target, binding, branch=branch))
    return target["workspace_id"], target, binding, project_id


def request(ops, wid, *, document=None):
    head = policies.head(ops.db, wid)
    return {"action": policies.ACTION, "target": {"workspace_id": wid},
            "params": {"policy": policy() if document is None else document,
                       "rationale": "Keep the published version usable within these limits"},
            "preconditions": {"expected_policy_revision": head["revision"],
                              "expected_previous_policy_id": head["policy_id"],
                              "expected_baseline_id": baselines._head(ops.db, wid)["baseline_id"],
                              "expected_lifecycle_revision": pm_lifecycle_gates.head(ops.db, wid)["revision"]},
            "idempotency_key": uuid.uuid4().hex}


async def test_adoption_has_fixed_source_but_does_not_start_work_or_change_stage(ops):
    wid, _, _, _ = await prepared(ops)
    version = ops.db.execute("PRAGMA user_version").fetchone()[0]
    policies.install(ops)
    assert ops.db.execute("PRAGMA user_version").fetchone()[0] == version
    provider_calls = len(ops.context["github"].calls)
    done = await execute(ops, request(ops, wid))
    record = policies.current_policy(ops, READER, wid)["policy"]
    doc = record["document"]
    assert doc["policy"] == policy()
    assert doc["scope"]["maintenance_branch"]["ref"] == "refs/heads/support/1.x"
    assert doc["scope"]["github_scope"] == "repository"
    assert doc["controller"]["actor"] == OWNER.actor
    assert set(doc["controller"]) == {"actor", "server_id", "controller_id"}
    assert doc["baseline_id"] == baselines._head(ops.db, wid)["baseline_id"]
    assert done["result"]["automatic_execution_started"] is False
    assert pm_lifecycle_gates.head(ops.db, wid)["stage"] == "idea"
    assert len(ops.context["github"].calls) == provider_calls
    assert ops.db.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 0
    assert policies.capabilities()["scheduler_available"] is False
    assert policies.capabilities()["repair_dispatch_available"] is False
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        ops.db.execute("DELETE FROM pm_maintenance_policies")


@pytest.mark.parametrize("who", [CONTRIBUTOR, READER, Principal("admin", frozenset(), admin=True)])
async def test_only_current_actual_owner_can_adopt(ops, who):
    wid, _, _, _ = await prepared(ops)
    with pytest.raises(OperationError):
        await execute(ops, request(ops, wid), who=who)
    assert policies.head(ops.db, wid)["revision"] == 0


async def test_cas_original_receipt_and_manage_only_replay_survive_new_head_and_stop(ops):
    wid, _, _, _ = await prepared(ops)
    first, stale = request(ops, wid), request(ops, wid)
    one, two = ops.create(OWNER, **first)[0], ops.create(OWNER, **stale)[0]
    await ops._execute(one["operation_id"])
    await ops._execute(two["operation_id"])
    done = ops.get(one["operation_id"])
    assert ops.get(two["operation_id"])["error_code"] == "PM_MAINTENANCE_POLICY_CHANGED"
    await execute(ops, request(ops, wid))
    await decide(ops, wid)
    count = len(ops.context["github"].calls)
    replay, existing = ops.create(Principal(OWNER.actor, frozenset({"manage"})), **first)
    assert existing and replay["operation_id"] == done["operation_id"]
    assert replay["result"] == done["result"]
    old = policies.fixed_record(ops, OWNER, wid, done["result"]["policy_id"])
    assert old["version"] == 1
    ops.db.execute("DELETE FROM pm_workspace_members WHERE workspace_id=? AND actor=?", (wid, OWNER.actor))
    assert await policies._run(OpContext(ops, done)) == done["result"]
    assert not policies.operation_visible(ops.db, OWNER, done)
    assert len(ops.context["github"].calls) == count
    assert policies.head(ops.db, wid)["revision"] == 2


async def test_owner_revocation_after_admission_does_not_record_policy(ops):
    wid, _, _, _ = await prepared(ops)
    await member(ops, {"workspace_id": wid}, CONTRIBUTOR.actor, "owner")
    pending = ops.create(OWNER, **request(ops, wid))[0]
    # Execute only revocation; a settle-all helper would accidentally run pending.
    revoke = ops.create(CONTRIBUTOR, action="pm.workspace.member.set", target={"workspace_id": wid},
                        params={"actor": OWNER.actor, "role": "reader"},
                        preconditions={"expected_version": pm_workspace.get_workspace(ops.db, CONTRIBUTOR, wid)["version"]},
                        idempotency_key=uuid.uuid4().hex)[0]
    await ops._execute(revoke["operation_id"])
    assert ops.get(revoke["operation_id"])["status"] == "succeeded"
    await ops._execute(pending["operation_id"])
    assert ops.get(pending["operation_id"])["status"] == "failed"
    assert policies.head(ops.db, wid)["revision"] == 0


async def test_baseline_change_and_old_execution_epoch_block_queued_policy(ops):
    wid, target, binding, _ = await prepared(ops)
    pending = ops.create(OWNER, **request(ops, wid))[0]
    await execute(ops, baseline_request(ops, target, binding, branch="support/1.x"))
    await ops._execute(pending["operation_id"])
    assert ops.get(pending["operation_id"])["error_code"] == "PM_BASELINE_VERSION_CONFLICT"
    pending = ops.create(OWNER, **request(ops, wid))[0]
    await decide(ops, wid)
    with pytest.raises(OperationError, match="PM_LIFECYCLE_STOPPED"):
        ops.create(OWNER, **request(ops, wid))
    await decide(ops, wid, "reopen", params={"stage": "planning"})
    await ops._execute(pending["operation_id"])
    assert ops.get(pending["operation_id"])["error_code"] == "PM_LIFECYCLE_EPOCH_CHANGED"
    assert policies.head(ops.db, wid)["revision"] == 0


async def test_verified_same_scope_release_can_advance_without_re_adopting_policy(ops):
    wid, target, binding, _ = await prepared(ops)
    first = await execute(ops, request(ops, wid))
    before = policies.active_record(ops, OWNER, wid)
    release = await execute(ops, baseline_request(ops, target, binding, branch="support/1.x"))
    after = policies.active_record(ops, OWNER, wid)
    assert after["policy_id"] == first["result"]["policy_id"]
    assert after["version"] == before["version"] == 1
    assert after["adopted_baseline_id"] == before["baseline_id"]
    assert after["baseline_id"] == release["result"]["baseline_id"] != before["baseline_id"]
    assert after["observation"] == {"max_age_seconds": 1800, "stability_seconds": 300}
    await execute(ops, baseline_request(ops, target, binding))  # Unconfigured branch cannot inherit repair authority.
    with pytest.raises(OperationError, match="PM_MAINTENANCE_SCOPE_CHANGED"):
        policies.active_record(ops, OWNER, wid)
    assert policies.fixed_record(ops, OWNER, wid, after["policy_id"]) == before


async def test_original_source_withdrawal_hides_records_pending_operations_and_events(ops):
    wid, _, _, project_id = await prepared(ops)
    body = request(ops, wid)
    body["params"]["rationale"] = "PRIVATE POLICY SOURCE"
    done = await execute(ops, body)
    pending = ops.create(OWNER, **request(ops, wid))[0]
    # Direct fixture revocation avoids executing the queued decision.
    ops.db.execute("UPDATE projects SET repositories='[]' WHERE project_id=?", (project_id,))
    for op in (done, pending):
        assert not pm_workspace.operation_visible(ops.db, OWNER, op)
    event = {"resource_type": "pm_maintenance_policy", "resource_id": done["result"]["policy_id"],
             "kind": "pm.maintenance_policy.adopted", "body": {"operation_id": done["operation_id"], "workspace_id": wid}}
    assert not pm_access.event_visible(ops.db, OWNER, event)
    current = policies.current_policy(ops, OWNER, wid)
    assert current["policy"] is None and current["visibility_filtered"] is True
    assert current["head"] == {"policy_id": None, "revision": 1}
    page = policies.list_policies(ops, OWNER, wid)
    assert page["policies"] == [] and page["visibility_filtered"]
    assert "PRIVATE POLICY SOURCE" not in json.dumps(page)
    with pytest.raises(OperationError):
        ops.create(OWNER, **body)


@pytest.mark.parametrize("field,value", [
    ("allowed_paths", ["../outside"]), ("allowed_paths", ["/absolute"]), ("allowed_paths", ["C:\\local"]),
    ("allowed_paths", ["src/*"]), ("allowed_paths", ["src//file"]), ("allowed_paths", ["x\nsecret"]),
    ("allowed_work_kinds", ["new_feature"]), ("allowed_work_kinds", [{}]), ("sources", []), ("sources", ["research", "research"]),
    ("after_release_policy", []), ("enabled", 1), ("controller", "caller-picked"),
    ("schedule.timezone", "../secret"), ("schedule.timezone", "Invented/Zone"), ("schedule.review_local_time", "25:90"),
    ("schedule.review_weekday", True), ("limits.max_parallel", 0), ("limits.cost_microusd", 0),
    ("limits.run_seconds", True), ("verification.required_ci", 1), ("observation.max_age_seconds", 0),
    ("verification.acceptance_evidence", []), ("delivery.maximum", "arbitrary_shell"),
    ("delivery.maximum", "report"), ("delivery.environment", "unapproved-production"),
])
async def test_invalid_or_unbounded_policies_are_rejected_without_record(ops, field, value):
    wid, _, _, _ = await prepared(ops)
    doc = policy()
    parent, _, key = field.rpartition(".")
    (doc[parent] if parent else doc)[key] = value
    with pytest.raises(OperationError, match="INVALID_PARAMS"):
        ops.create(OWNER, **request(ops, wid, document=doc))
    assert policies.head(ops.db, wid)["revision"] == 0


async def test_report_policy_without_repair_branch_is_explicit_and_cursors_are_bounded(ops):
    wid, _, _, project_id = await prepared(ops, branch=None)
    report = policy()
    report.update(allowed_work_kinds=[], allowed_paths=["."], after_release_policy="manual")
    report["delivery"]["maximum"] = "report"
    one = await execute(ops, request(ops, wid, document=report))
    report["enabled"] = False
    two = await execute(ops, request(ops, wid, document=report))
    page = policies.list_policies(ops, READER, wid, limit=1)
    assert page["policies"][0]["policy_id"] == one["result"]["policy_id"]
    assert policies.list_policies(ops, READER, wid, after=page["next_cursor"])["policies"][0]["policy_id"] == two["result"]["policy_id"]
    for args in ({"after": True}, {"after": -1}, {"after": 2**64}, {"limit": 0}, {"limit": 101}):
        with pytest.raises(OperationError, match="INVALID_PARAMS"):
            policies.list_policies(ops, READER, wid, **args)
    await remove_repo(ops, project_id)
    assert policies.current_policy(ops, READER, wid)["visibility_filtered"]


async def test_policy_page_does_not_truncate_documents_to_fit(ops, monkeypatch):
    wid, _, _, _ = await prepared(ops)
    await execute(ops, request(ops, wid))
    await execute(ops, request(ops, wid))
    first = policies.list_policies(ops, OWNER, wid, limit=1)
    cap = baselines._size(first) + 64
    monkeypatch.setattr(baselines, "MAX_READ_BYTES", cap)
    page = policies.list_policies(ops, OWNER, wid)
    assert len(page["policies"]) == 1 and page["next_cursor"]
    assert page["policies"][0] == first["policies"][0]
    monkeypatch.setattr(baselines, "MAX_READ_BYTES", 100)
    with pytest.raises(OperationError):
        policies.list_policies(ops, OWNER, wid)


def test_packaged_iana_database_works_without_system_zone_files():
    result = subprocess.run([sys.executable, "-c", "from zoneinfo import ZoneInfo; from datetime import datetime; "
        "z=ZoneInfo('America/New_York'); assert str(datetime(2026,1,1,tzinfo=z).utcoffset())=='-1 day, 19:00:00'; "
        "assert str(datetime(2026,7,1,tzinfo=z).utcoffset())=='-1 day, 20:00:00'"],
        env={**os.environ, "PYTHONTZPATH": ""}, capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
