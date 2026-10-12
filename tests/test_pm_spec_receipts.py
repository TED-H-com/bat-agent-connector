"""A malformed or lost transport ACK never turns into a completed spec claim."""
from __future__ import annotations

import pytest

from bat_agent_connector import pm_runs, pm_worktree
from bat_agent_connector.operations import AmbiguousOutcome
from tests.operation_helpers import settle_operations
from tests.test_checkpoints import git
from tests.test_pm_plan_operations import OWNER
from tests.test_pm_spec_materialization import fixed as fixed_fixture
from tests.test_pm_spec_materialization import pm as pm_fixture
from tests.test_pm_spec_materialization import ready as ready_fixture
from tests.test_pm_spec_materialization import request
from tests.test_pm_spec_materialization import world as world_fixture
from tests.test_pm_spec_materialization_recovery import revoke
from tests.test_pm_worktree import resume

world = world_fixture
pm = pm_fixture
ready = ready_fixture
fixed = fixed_fixture


@pytest.mark.parametrize("field", ["operation_id", "document_digest", "artifact_kind", "file_digests"])
async def test_malformed_git_ack_keeps_writer_until_exact_original_readback(fixed, monkeypatch, field):
    w, ops = fixed, fixed["d"].ops
    original = pm_worktree._host_call
    armed = True
    async def wrong(ctx, plan, phase, **kwargs):
        nonlocal armed
        value = await original(ctx, plan, phase, **kwargs)
        if phase == "pm-revision" and armed:
            armed = False
            value[field] = {} if field == "file_digests" else "wrong-original-identity"
        return value
    monkeypatch.setattr(pm_worktree, "_host_call", wrong)
    op, _ = ops.create(OWNER, **request(w))
    await settle_operations(ops)
    done = ops.get(op["operation_id"])
    assert done["status"] == "needs_attention" and done["error_code"] == "PM_GIT_RECEIPT_INVALID", done
    assert not ops.db.execute("SELECT 1 FROM pm_spec_materializations").fetchone()
    assert pm_runs.get_by_operation(ops.db, op["operation_id"])["state"] not in pm_runs.TERMINAL
    ops.resume(OWNER, op["operation_id"])
    recovered = await resume(w, op)
    assert recovered["status"] == "succeeded", recovered
    assert w["runner"].phases.count("pm-revision") == 1
    assert git(w["plan"]["worktree_path"], "rev-list", "--count", w["sha"] + "..HEAD") == "2"


async def test_lost_artifact_publish_recovers_only_original_snapshot_then_stops_git(fixed, monkeypatch):
    w, ops = fixed, fixed["d"].ops
    store = ops.context["artifact_store"]
    publish = store.publish
    armed = True
    def lost(row):
        nonlocal armed
        value = publish(row)
        if armed:
            armed = False
            revoke(w)
            raise AmbiguousOutcome("Original local snapshot ACK lost")
        return value
    monkeypatch.setattr(store, "publish", lost)
    op, _ = ops.create(OWNER, **request(w))
    await settle_operations(ops)
    first = ops.get(op["operation_id"])
    assert first["status"] == "uncertain", first
    done = await resume(w, first)
    assert done["status"] == "failed", done
    assert "pm-revision" not in w["runner"].phases
    assert not ops.db.execute("SELECT 1 FROM pm_spec_materializations").fetchone()
    assert ops.db.execute("SELECT state FROM artifact_revisions WHERE operation_id=?", (op["operation_id"],)).fetchone()[0] == "ready"
    assert pm_runs.get_by_operation(ops.db, op["operation_id"])["state"] == "completed_service"


async def test_materialization_inherits_existing_package_execution_sources(fixed):
    from bat_agent_connector import pm_source_lineage
    from tests.test_pm_package_handoff import request as handoff_request
    from tests.test_pm_package_handoff import run as dispatch
    from tests.test_pm_spec_materialization import materialize
    from tests.test_pm_work_packages import create, read

    w, ops = fixed, fixed["d"].ops
    original = await dispatch(w, await handoff_request(w))
    assert original["status"] == "succeeded", original
    w["package"] = read(w, await create(w, previous=w["package"]["package_id"], title="Revised owned spec"))
    assert pm_source_lineage.roots(ops.db, "operation", w["package"]["operation_id"]) == {original["operation_id"]}
    result = await materialize(w)
    expected = {original["operation_id"], result["operation_id"]}
    assert pm_source_lineage.roots(ops.db, "operation", result["operation_id"]) == expected
    assert pm_source_lineage.roots(ops.db, "artifact", result["result"]["artifact_ref"]["artifact_id"]) == expected
