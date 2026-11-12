import json
from pathlib import Path

import pytest

from bat_agent_connector import (
    api_auth,
    pm_delivery_lineage,
    pm_source_lineage,
    pm_wip_count,
    session_reading,
    work_items,
)
from bat_agent_connector import pm_workspace_wip as policy
from bat_agent_connector.api_auth import Principal
from tests import test_pm_attempt_results as results
from tests import test_pm_work_start as starting
from tests.operation_helpers import settle_operations
from tests.test_api_v1 import daemon as daemon_fixture
from tests.test_checkpoints import git
from tests.test_pm_workspace_wip import read
from tests.test_pm_workspace_wip_lineage import closed_attempt

daemon = daemon_fixture
world = results.world
pm = results.pm
ready = results.ready
start_ready = results.start_ready


async def build_action(w, mock, att, kind):
    ops = w["d"].ops
    target = {"host": "h1", "session_id": att["session_id"]}
    params, pre = {}, {}
    principal = Principal(starting.STARTER_OWNER.actor, starting.STARTER_OWNER.scopes | {"approve"})
    api_auth.issue(ops.db, principal.actor, sorted(principal.scopes))
    action = kind
    if kind in {"session.read", "session.position"}:
        index = session_reading.index_messages(mock.states[att["session_id"]]["messages"], complete=True)
        session_reading.observe(
            ops.journal,
            principal,
            {"host": "h1", "session_id": att["session_id"], "messages": [], "_reading_index": index},
        )
        message = index["messages"][0]
        if kind == "session.read":
            params = {"messages": [{"message_id": message["id"], "revision": message["revision"]}]}
        else:
            params = {"message_id": message["id"], "revision": message["revision"], "offset": 0}
            pre = {"expected_version": 0}
    elif kind == "session.labels.set":
        params = {"labels": ["Reviewed synthetic result"]}
        pre = {"expected_version": 0}
    elif kind.startswith("work_item."):
        wi = work_items._get_item(ops.db, w["target"]["work_item_id"])
        target = {"work_item_id": wi["work_item_id"]}
        if kind == "work_item.pin":
            params = {"pinned": True}
            pre = {"before": False}
        elif kind == "work_item.update":
            params = {"archived": False}
            pre = {"expected_version": wi["version"]}
        else:
            target = {"project_id": wi["project_id"]}
            order = [
                row["work_item_id"] for row in work_items.project_get(ops.db, wi["project_id"])["work_items"]
            ]
            params = {"order": order}
            pre = {"before": order}
    elif kind == "session.record_verification":
        handoff = ops.get(att["handoff_operation_id"])
        path = Path(handoff["result"]["worktree_path"])
        params = {
            "candidate_commit": git(path, "rev-parse", "HEAD"),
            "command": "pytest",
            "exit_code": 0,
            "environment": "Synthetic review fixture",
            "log_ref": "artifacts/test-run.log",
        }
    elif kind == "artifact.accept":
        token = api_auth.issue(ops.db, principal.actor, sorted(principal.scopes))
        principal = api_auth.authenticate(ops.db, token, "synthetic-unused-admin-token")
        from tests import test_artifact_managed as managed
        from tests.test_artifact_capture import LocalCaptureHost

        ops.context["artifact_host"] = LocalCaptureHost()
        handoff = ops.get(att["handoff_operation_id"])
        path = Path(handoff["result"]["worktree_path"])
        (path / "result.bin").write_bytes(b"synthetic review result\n")
        execution = (handoff, att["session_id"], path)
        pv = await managed.preview(w["d"], principal, execution)
        capture = managed.submit(w["d"], principal, pv, key="review-capture")
        await settle_operations(ops)
        capture = ops.get(capture["operation_id"])
        assert capture["status"] == "succeeded", capture
        assert read(ops, w["target"], principal)["used"] == 0
        return principal, managed.accept(w["d"], principal, capture, pv, key="review-accept")
    elif kind.startswith("session.answer"):
        action = "session.answer"
        if kind.endswith(".pending"):
            mock.states[att["session_id"]]["pendingAskUser"] = {
                "toolUseId": "review-question",
                "questions": [
                    {
                        "question": "Choose a fixture option",
                        "header": "Option",
                        "options": [{"label": "One", "description": "One"}],
                        "multiSelect": False,
                    }
                ],
            }
        params = {"tool_use_id": "review-question", "answers": {"Choose a fixture option": "One"}}
    elif kind.startswith("session.interrupt"):
        action = "session.interrupt"
        mock.metas[att["session_id"]]["isStreaming"] = kind.endswith(".active")
        mock.states[att["session_id"]]["isStreaming"] = kind.endswith(".active")
        params = {"mode": "soft"}
    op, _ = ops.create(
        principal,
        action=action,
        target=target,
        params=params,
        preconditions=pre,
        idempotency_key="review-" + kind,
    )
    return principal, op


@pytest.mark.parametrize(
    "kind",
    [
        "session.read",
        "session.labels.set",
        "session.position",
        "work_item.pin",
        "work_item.order",
        "work_item.update",
        "session.record_verification",
        "artifact.accept",
    ],
)
async def test_noninputs_never_hold(start_ready, mock, kind):
    w = start_ready
    att, _ = await closed_attempt(w, mock)
    principal, op = await build_action(w, mock, att, kind)
    pending = read(w["d"].ops, w["target"], principal)
    await settle_operations(w["d"].ops)
    assert w["d"].ops.get(op["operation_id"])["status"] == "succeeded"
    assert pending["used"] == read(w["d"].ops, w["target"], principal)["used"] == 0


def test_lineage_action_inventory(daemon):
    inventory = (
        pm_source_lineage.DERIVED_ACTIONS
        | pm_delivery_lineage.ACTIONS
        | {a for a in daemon.ops.actions if a.startswith("pm.")}
        | pm_source_lineage.HANDOFF_ACTIONS
    )
    print("LINEAGE_INVENTORY", json.dumps(sorted(inventory)))
    assert set(pm_wip_count.LINEAGE_ACTIONS) == inventory
    assert pm_wip_count.LINEAGE_ACTIONS["session.send"] == "input"
    assert {a for a, kind in pm_wip_count.LINEAGE_ACTIONS.items() if kind == "control"} == {
        "session.answer",
        "session.interrupt",
    }


async def test_settled_read_history_uses_record_budget(start_ready, mock, monkeypatch):
    w = start_ready
    ops = w["d"].ops
    att, _ = await closed_attempt(w, mock)
    principal = starting.STARTER_OWNER
    index = session_reading.index_messages(mock.states[att["session_id"]]["messages"], complete=True)
    session_reading.observe(
        ops.journal,
        principal,
        {"host": "h1", "session_id": att["session_id"], "messages": [], "_reading_index": index},
    )
    msg = index["messages"][0]
    for n in range(600):
        op, _ = ops.create(
            principal,
            action="session.read",
            target={"host": "h1", "session_id": att["session_id"]},
            params={"messages": [{"message_id": msg["id"], "revision": msg["revision"]}]},
            preconditions={},
            idempotency_key=f"noninput-history-{n}",
        )
        await settle_operations(ops)
        assert ops.get(op["operation_id"])["status"] == "succeeded"
        if n in {256, 599}:
            value = read(ops, w["target"], principal)
            assert value["coverage"]["status"] == "complete" and value["used"] == 0
    collector = pm_wip_count._Collector(ops, principal, w["workspace_id"], policy.time.time())
    collector.scan()
    monkeypatch.setattr(policy, "MAX_RECORDS", collector.used_records - 1)
    value = read(ops, w["target"], principal)
    assert value["used"] is None and value["coverage"]["reasons"] == ["coverage_unavailable"]


@pytest.mark.parametrize("case", ["future_action", "wrong_target"])
async def test_unknown_lineage_inclusion_is_generic(start_ready, mock, case):
    from tests.test_pm_workspace_wip import clone_row

    w = start_ready
    ops = w["d"].ops
    att, _ = await closed_attempt(w, mock)
    # Synthetic newer-version/corrupt source row, never a completion receipt.
    source = dict(
        ops.db.execute(
            "SELECT * FROM operations WHERE operation_id=?", (att["handoff_operation_id"],)
        ).fetchone()
    )
    oid = "op_" + "a" * 32
    clone_row(
        ops.db,
        "operations",
        source,
        operation_id=oid,
        idem_key="synthetic-lineage-kind",
        action="fixture.future_action" if case == "future_action" else "session.send",
        target=json.dumps({"host": "h1", "session_id": "different-synthetic-session"}),
    )
    ops.db.execute(
        "INSERT INTO pm_source_lineage VALUES(?,?,?,?)", ("operation", oid, att["handoff_operation_id"], oid)
    )
    value = read(ops, w["target"], starting.STARTER_OWNER)
    assert value["coverage"]["reasons"] == ["coverage_unavailable"] and value["used"] is None
    assert "breakdown" not in value and "known_used" not in value
