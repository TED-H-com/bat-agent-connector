"""No new effects across source stops; original spec/Git receipts remain recoverable."""
from __future__ import annotations

from pathlib import Path

import pytest

from bat_agent_connector import artifacts, pm_runs, pm_source_lineage
from bat_agent_connector import pm_spec_materialization as specs
from bat_agent_connector.api_auth import Principal
from bat_agent_connector.errors import InvokeTimeout
from bat_agent_connector.operations import OpContext, OperationError
from tests.operation_helpers import settle_operations
from tests.test_checkpoints import bat_writes, git, snapshot
from tests.test_pm_plan_operations import OWNER, interrupt_host
from tests.test_pm_spec_materialization import fixed as fixed_fixture
from tests.test_pm_spec_materialization import pm as pm_fixture
from tests.test_pm_spec_materialization import ready as ready_fixture
from tests.test_pm_spec_materialization import record, request
from tests.test_pm_spec_materialization import world as world_fixture
from tests.test_pm_worktree import resume

world = world_fixture
pm = pm_fixture
ready = ready_fixture
fixed = fixed_fixture


def revoke(w):
    w["d"].ops.db.execute("DELETE FROM pm_workspace_members WHERE workspace_id=? AND actor=?", (w["workspace_id"], OWNER.actor))


def lose(_):
    raise InvokeTimeout("Lost original Git response")


@pytest.mark.parametrize("lost", [False, True])
@pytest.mark.parametrize("stop", ["cancel", "revoke"])
async def test_completed_git_recovers_combined_original_fact_after_stop(fixed, lost, stop):
    w, ops = fixed, fixed["d"].ops
    envelope = request(w)
    op, _ = ops.create(OWNER, **envelope)
    def after(_):
        if stop == "cancel":
            ops.cancel(OWNER, op["operation_id"])
        else:
            revoke(w)
        if lost:
            lose(None)
    w["runner"].after["pm-revision"] = after
    await settle_operations(ops)
    original = ops.get(op["operation_id"])
    if lost:
        assert original["status"] == "uncertain", original
        original = await resume(w, original)
    assert original["status"] == "succeeded", original
    assert ops.db.execute("SELECT COUNT(*) FROM pm_spec_materializations").fetchone()[0] == 1
    assert pm_runs.get_by_operation(ops.db, original["operation_id"])["state"] == "completed_service"
    assert w["runner"].phases.count("pm-revision") == 1
    assert git(w["plan"]["worktree_path"], "rev-list", "--count", w["sha"] + "..HEAD") == "2"
    assert await specs._run(OpContext(ops, original)) == original["result"]
    if stop == "revoke":
        assert not specs.source_visible(ops.db, OWNER, original)
        with pytest.raises(OperationError):
            record(w, original)
        with pytest.raises(OperationError):
            artifacts.get(ops.db, original["result"]["artifact_ref"]["artifact_id"], 1, principal=OWNER)
        with pytest.raises(OperationError):
            pm_runs.read(ops, OWNER, w["workspace_id"], original["external_refs"]["run_id"])


@pytest.mark.parametrize("point", ["intent", "files", "branch", "index"])
async def test_partial_git_retains_writer_after_revoke_and_recovers_once_restored(fixed, monkeypatch, point):
    w, ops = fixed, fixed["d"].ops
    interrupt_host(w, monkeypatch, point)
    op, _ = ops.create(OWNER, **request(w))
    await settle_operations(ops)
    original = ops.get(op["operation_id"])
    assert original["status"] == "uncertain", original
    membership = dict(ops.db.execute("SELECT * FROM pm_workspace_members WHERE workspace_id=? AND actor=?", (w["workspace_id"], OWNER.actor)).fetchone())
    revoke(w)
    stopped = await resume(w, original)
    assert stopped["status"] == "uncertain", stopped
    assert not ops.db.execute("SELECT 1 FROM pm_spec_materializations").fetchone()
    run = pm_runs.get_by_operation(ops.db, op["operation_id"])
    assert ops.db.execute("SELECT 1 FROM pm_writer_slots WHERE run_id=?", (run["run_id"],)).fetchone()
    assert w["runner"].phases.count("pm-revision") == 1
    columns = ",".join(membership)
    ops.db.execute("INSERT INTO pm_workspace_members(" + columns + ") VALUES(" + ",".join("?" for _ in membership) + ")", tuple(membership.values()))
    recovered = await resume(w, original)
    assert recovered["status"] == "succeeded", recovered
    assert git(w["plan"]["worktree_path"], "rev-list", "--count", w["sha"] + "..HEAD") == "2"


@pytest.mark.parametrize("bad", ["digest", "scope", "extra", "scopes", "outsider"])
async def test_invalid_admission_has_no_writer_artifact_or_git_effect(fixed, mock, bad):
    w, ops = fixed, fixed["d"].ops
    env = request(w)
    who = OWNER
    if bad == "digest":
        env["preconditions"]["expected_package_digest"] = "a" * 64
    elif bad == "scope":
        env["preconditions"]["expected_scope_revision"] = "pmscope_" + "a" * 32
    elif bad == "extra":
        env["params"]["path"] = "/manual/upstream/tasks.md"
    elif bad == "scopes":
        who = Principal(OWNER.actor, frozenset({"observe", "manage"}))
    else:
        who = Principal("outsider", OWNER.scopes)
    previous = {table: ops.db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] for table in ("pm_runs", "artifacts")}
    with pytest.raises(OperationError):
        ops.create(who, **env)
    assert previous == {table: ops.db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] for table in previous}
    assert not bat_writes(mock) and not w["runner"].phases


@pytest.mark.parametrize("bad", ["foreign_file", "symlink", "index"])
async def test_foreign_owned_carrier_content_is_preserved_without_commit(fixed, bad):
    w, ops = fixed, fixed["d"].ops
    carrier = Path(w["plan"]["worktree_path"])
    head = git(carrier, "rev-parse", "HEAD")
    if bad == "foreign_file":
        (carrier / "manual.txt").write_text("A person's unregistered draft")
    elif bad == "symlink":
        owned = carrier / "openspec" / "changes" / ("pm-" + w["package"]["package_id"][5:])
        owned.symlink_to(w["human"], target_is_directory=True)
    else:
        (carrier / "hello.txt").write_text("A person's staged change")
        git(carrier, "add", "hello.txt")
    before = snapshot(w["human"])
    op, _ = ops.create(OWNER, **request(w))
    await settle_operations(ops)
    done = ops.get(op["operation_id"])
    assert done["status"] in {"failed", "needs_attention"}, done
    assert not ops.db.execute("SELECT 1 FROM pm_spec_materializations").fetchone()
    assert git(carrier, "rev-parse", "HEAD") == head and snapshot(w["human"]) == before
    # The carrier cannot be proved safe. Never clear its writer automatically.
    run = pm_runs.get_by_operation(ops.db, op["operation_id"])
    assert run["state"] not in pm_runs.TERMINAL
    assert ops.db.execute("SELECT 1 FROM pm_writer_slots WHERE run_id=?", (run["run_id"],)).fetchone()


async def test_snapshot_artifact_never_claims_completed_git_when_new_git_is_stopped(fixed):
    w, ops = fixed, fixed["d"].ops
    op, _ = ops.create(OWNER, **request(w))
    w["runner"].before["pm-revision"] = lambda _: revoke(w)
    await settle_operations(ops)
    done = ops.get(op["operation_id"])
    assert done["status"] != "succeeded", done
    assert not ops.db.execute("SELECT 1 FROM pm_spec_materializations").fetchone()
    assert not pm_source_lineage.visible(ops.db, OWNER, "artifact", "art_" + op["operation_id"][3:])
    assert pm_runs.get_by_operation(ops.db, op["operation_id"])["state"] == "completed_service"
