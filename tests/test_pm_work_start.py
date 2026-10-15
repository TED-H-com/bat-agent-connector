"""Tests for the work.start compound action and pm_work_start_preview (v1.3 E02, slice P3)."""
from __future__ import annotations

import sqlite3
import time

import pytest

from bat_agent_connector import controller_authority, pm_work_start, pm_workspace
from bat_agent_connector.api_auth import Principal
from bat_agent_connector.operations import ActionDef, NeedsAttention, OperationError, OperationService
from bat_agent_connector.task_journal import Journal

OWNER = Principal("alice", frozenset({"observe", "manage", "start", "operate"}))
OTHER = Principal("bob", frozenset({"observe", "manage", "start", "operate"}))


def _service(tmp_path) -> OperationService:
    journal = Journal(tmp_path / "tasks.sqlite3")
    pm_workspace.schema(journal)
    ops = OperationService(journal)
    srv = pm_workspace.server_id(ops.db)
    now = time.time()
    with journal.tx():
        for idx, ws in enumerate(("ws_" + "1" * 32, "ws_" + "2" * 32)):
            ops.db.execute(
                "INSERT INTO pm_workspaces VALUES(?,?,?,?,1,?,?,?,?)",
                (ws, srv, f"WS {idx}", '["o/r"]', "alice", now, now, f"op_ws_{idx}"),
            )
            for who in (OWNER, OTHER):
                ops.db.execute(
                    "INSERT INTO pm_workspace_members VALUES(?,?,?,?,?,?)",
                    (ws, who.actor, "owner", pm_workspace.controller_for(ops.db, who), now, now),
                )

    def admit(service, principal, target, params, pre):
        wid, wi = target["workspace_id"], target["work_item_id"]
        ctrl = controller_authority.binding(service.db, principal, pm_workspace_id=wid)
        if not params.get("_skip_pre_controller_check"):
            pm_work_start.require_work_controller(service.db, wid, wi, ctrl)
        intent = {"workspace_id": wid, "work_item_id": wi, "controller": ctrl, "params": params}
        return {
            "principal": pm_workspace.principal_binding(principal),
            "work_start": intent,
            "work_start_digest": pm_work_start._digest(intent),
        }

    async def run(ctx):
        if ctx.params.get("pause"):
            raise NeedsAttention("PAUSED_FOR_TEST", "Paused for controller test")
        return {"ok": True}

    ops.register(
        ActionDef(
            "work.start",
            "start",
            "Start work",
            run,
            admit,
            ("workspace_id", "work_item_id"),
        )
    )
    return ops


def test_schema_immutability_and_no_user_version(tmp_path):
    ops = _service(tmp_path)
    before_ver = ops.db.execute("PRAGMA user_version").fetchone()[0]
    pm_work_start.schema(ops.journal)
    assert ops.db.execute("PRAGMA user_version").fetchone()[0] == before_ver == 3
    ws = "ws_" + "1" * 32
    wi = "wi_" + "2" * 20
    op, created = ops.create(
        OWNER,
        action="work.start",
        target={"workspace_id": ws, "work_item_id": wi},
        params={"instruction": "Fix bug"},
        preconditions={},
        idempotency_key="k1",
    )
    assert created is True
    attempt_id = "att_" + op["operation_id"][3:]
    row = ops.db.execute("SELECT * FROM work_attempts WHERE attempt_id=?", (attempt_id,)).fetchone()
    assert row is not None
    assert row["origin_kind"] == "operation"
    assert row["origin_id"] == op["operation_id"]
    assert row["start_operation_id"] == op["operation_id"]
    assert row["predecessor_attempt_id"] is None

    with pytest.raises(sqlite3.IntegrityError, match="Work attempt history is immutable"):
        ops.db.execute("UPDATE work_attempts SET origin_id='op_other' WHERE attempt_id=?", (attempt_id,))
    with pytest.raises(sqlite3.IntegrityError, match="Work attempt history is immutable"):
        ops.db.execute("DELETE FROM work_attempts WHERE attempt_id=?", (attempt_id,))

    with ops.journal.tx():
        ops.db.execute(
            "INSERT INTO pm_work_start_children VALUES(?,?,?,?,?,?,?)",
            (op["operation_id"], "bind", 0, "op_" + "a" * 32, "{}", "d" * 64, time.time()),
        )
    with pytest.raises(sqlite3.IntegrityError, match="Work start child is immutable"):
        ops.db.execute("UPDATE pm_work_start_children SET slot='package' WHERE parent_id=?", (op["operation_id"],))
    with pytest.raises(sqlite3.IntegrityError, match="Work start child is immutable"):
        ops.db.execute("DELETE FROM pm_work_start_children WHERE parent_id=?", (op["operation_id"],))


def test_insert_hook_predecessor_chain_and_controller_rollback(tmp_path):
    ops = _service(tmp_path)
    ws = "ws_" + "1" * 32
    wi = "wi_" + "2" * 20
    first, _ = ops.create(
        OWNER,
        action="work.start",
        target={"workspace_id": ws, "work_item_id": wi},
        params={"instruction": "First attempt"},
        preconditions={},
        idempotency_key="k-first",
    )
    second, _ = ops.create(
        OWNER,
        action="work.start",
        target={"workspace_id": ws, "work_item_id": wi},
        params={"instruction": "Second attempt"},
        preconditions={},
        idempotency_key="k-second",
    )
    row2 = ops.db.execute(
        "SELECT * FROM work_attempts WHERE start_operation_id=?", (second["operation_id"],)
    ).fetchone()
    assert row2["predecessor_attempt_id"] == "att_" + first["operation_id"][3:]

    # Early check in _admit rejects a second controller.
    with pytest.raises(OperationError) as exc_info:
        ops.create(
            OTHER,
            action="work.start",
            target={"workspace_id": ws, "work_item_id": wi},
            params={"instruction": "Takeover"},
            preconditions={},
            idempotency_key="k-other",
        )
    assert exc_info.value.code == "PM_WORK_CONTROLLER_CONFLICT"

    # Even if a concurrent start bypassed _admit's pre-check before the winner committed,
    # the INSERT hook re-checks inside the transaction and rolls back the operation row.
    count_before = ops.db.execute("SELECT COUNT(*) FROM operations").fetchone()[0]
    with pytest.raises(OperationError) as exc_info2:
        ops.create(
            OTHER,
            action="work.start",
            target={"workspace_id": ws, "work_item_id": wi},
            params={"instruction": "Concurrent takeover", "_skip_pre_controller_check": True},
            preconditions={},
            idempotency_key="k-concurrent",
        )
    assert exc_info2.value.code == "PM_WORK_CONTROLLER_CONFLICT"
    assert ops.db.execute("SELECT COUNT(*) FROM operations").fetchone()[0] == count_before
    assert ops.db.execute("SELECT COUNT(*) FROM work_attempts").fetchone()[0] == 2


def test_workspace_key_namespacing_and_idempotency_conflict(tmp_path):
    ops = _service(tmp_path)
    ws1 = "ws_" + "1" * 32
    ws2 = "ws_" + "2" * 32
    wi = "wi_" + "3" * 20

    op1, fresh1 = ops.create(
        OWNER,
        action="work.start",
        target={"workspace_id": ws1, "work_item_id": wi},
        params={"instruction": "In ws1"},
        preconditions={},
        idempotency_key="shared-key",
    )
    assert fresh1 is True
    assert op1["idempotency_key"] == "shared-key"
    assert ops.has_idempotency_record(
        OWNER, action="work.start", target={"workspace_id": ws1, "work_item_id": wi}, idempotency_key="shared-key"
    )
    assert not ops.has_idempotency_record(
        OWNER, action="work.start", target={"workspace_id": ws2, "work_item_id": wi}, idempotency_key="shared-key"
    )

    # Same key in the same workspace with identical request replays.
    replay, fresh_replay = ops.create(
        OWNER,
        action="work.start",
        target={"workspace_id": ws1, "work_item_id": wi},
        params={"instruction": "In ws1"},
        preconditions={},
        idempotency_key="shared-key",
    )
    assert fresh_replay is False
    assert replay["operation_id"] == op1["operation_id"]
    assert replay["idempotency_key"] == "shared-key"

    # Same key in a different workspace creates a distinct operation.
    op2, fresh2 = ops.create(
        OWNER,
        action="work.start",
        target={"workspace_id": ws2, "work_item_id": wi},
        params={"instruction": "In ws2"},
        preconditions={},
        idempotency_key="shared-key",
    )
    assert fresh2 is True
    assert op2["operation_id"] != op1["operation_id"]
    assert op2["idempotency_key"] == "shared-key"

    # Same key in the same workspace with a different request raises IDEMPOTENCY_CONFLICT.
    with pytest.raises(OperationError) as exc_info:
        ops.create(
            OWNER,
            action="work.start",
            target={"workspace_id": ws1, "work_item_id": wi},
            params={"instruction": "Changed intent"},
            preconditions={},
            idempotency_key="shared-key",
        )
    assert exc_info.value.code == "IDEMPOTENCY_CONFLICT"
    assert exc_info.value.status == 409


@pytest.mark.asyncio
async def test_authorize_controller_blocks_different_actor_cancel_and_resume(tmp_path):
    ops = _service(tmp_path)
    ws = "ws_" + "1" * 32
    wi = "wi_" + "2" * 20
    op, _ = ops.create(
        OWNER,
        action="work.start",
        target={"workspace_id": ws, "work_item_id": wi},
        params={"instruction": "Pause me", "pause": True},
        preconditions={},
        idempotency_key="k-ctrl",
    )
    await ops._execute(op["operation_id"])
    assert ops.get(op["operation_id"])["status"] == "needs_attention"

    with pytest.raises(OperationError) as cancel_exc:
        ops.cancel(OTHER, op["operation_id"])
    assert cancel_exc.value.code == "CONTROLLER_MISMATCH"

    with pytest.raises(OperationError) as resume_exc:
        ops.resume(OTHER, op["operation_id"])
    assert resume_exc.value.code == "CONTROLLER_MISMATCH"

    # Original controller can resume and cancel.
    resumed = ops.resume(OWNER, op["operation_id"])
    assert resumed["status"] == "running"
    await ops._execute(op["operation_id"])
    cancelled = ops.cancel(OWNER, op["operation_id"])
    assert cancelled["status"] == "cancelled"


def test_child_settled_trigger_wakes_waiting_parent(tmp_path):
    ops = _service(tmp_path)
    ops.register(ActionDef("dummy.child", "start", "Dummy child", lambda ctx: None))
    ws = "ws_" + "1" * 32
    wi = "wi_" + "2" * 20
    parent, _ = ops.create(
        OWNER,
        action="work.start",
        target={"workspace_id": ws, "work_item_id": wi},
        params={"instruction": "Wait on child"},
        preconditions={},
        idempotency_key="k-wake",
    )
    child, _ = ops.create(
        OWNER,
        action="dummy.child",
        target={},
        params={},
        preconditions={},
        idempotency_key="k-child",
    )
    due = time.time() + 3600
    with ops.journal.tx():
        ops.db.execute(
            "INSERT INTO pm_work_start_children VALUES(?,?,?,?,?,?,?)",
            (parent["operation_id"], "bind", 0, child["operation_id"], "{}", "d" * 64, time.time()),
        )
        ops._transition(parent["operation_id"], "running")
        ops._transition(parent["operation_id"], "waiting_external", reason="Waiting for child", next_run_at=due)
    assert ops.get(parent["operation_id"])["next_run_at"] == due
    ops._transition(child["operation_id"], "running")
    assert ops.get(parent["operation_id"])["next_run_at"] == due
    ops._transition(child["operation_id"], "succeeded", result={"done": True})
    assert ops.get(parent["operation_id"])["next_run_at"] == 0
