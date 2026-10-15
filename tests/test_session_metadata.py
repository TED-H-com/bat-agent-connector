from __future__ import annotations

import json

import pytest

from bat_agent_connector import api_auth, session_metadata
from bat_agent_connector.operations import OpContext, OperationError, OperationService
from bat_agent_connector.task_daemon import TaskDaemon
from bat_agent_connector.task_journal import LATEST_DATA_STEP, Journal
from tests.conftest import make_config
from tests.operation_helpers import settle_operations
from tests.test_api_v1 import http, token

PERSON = api_auth.Principal("labels-person", frozenset({"observe", "manage"}))
TARGET = {"host": "h1", "session_id": "manual-full-session"}


@pytest.fixture
def journal(tmp_path):
    j = Journal(tmp_path / "labels.db")
    with j.tx():
        j.api_event("session", "h1/manual-full-session", "session.observed", {**TARGET, "provenance": "manual"})
    yield j
    j.close()


def create(ops, values=None, *, target=None, version=0, key="original", principal=PERSON):
    return ops.create(principal, action="session.labels.set", target=target or TARGET,
        params={"labels": ["待確認", "UI"] if values is None else values}, preconditions={"expected_version": version},
        idempotency_key=key)


async def test_local_effect_exact_replay_cas_and_history(journal):
    ops = OperationService(journal, actions=session_metadata.ACTIONS)
    first, _ = create(ops)
    second, _ = create(ops, ["other"], key="competing")  # admitted against the same version
    await settle_operations(ops)
    original = ops.get(first["operation_id"])
    assert original["status"] == "succeeded"
    assert ops.get(second["operation_id"])["error_code"] == "METADATA_VERSION_CONFLICT"
    latest, _ = create(ops, ["new"], version=1, key="later")
    await settle_operations(ops)
    replay, reused = create(ops)
    assert not reused and replay["operation_id"] == original["operation_id"]
    assert replay["result"] == original["result"]
    assert replay["result"]["connector_metadata"]["version"] == 1
    assert ops.get(latest["operation_id"])["result"]["connector_metadata"]["version"] == 2
    with pytest.raises(OperationError, match="different request"):
        create(ops, ["changed same key"])
    events = journal.db.execute("SELECT * FROM api_events WHERE kind='session.labels_updated'").fetchall()
    assert len(events) == 2 and all(e["actor"] == PERSON.actor for e in events)
    assert json.loads(events[0]["body"])["operation_id"] == original["operation_id"]
    from bat_agent_connector.observation import Observation
    history = Observation(journal).history("session", "h1/manual-full-session")["events"]
    assert len([e for e in history if e["kind"] == "session.labels_updated"]) == 2
    assert original["result"]["previous"]["labels"] == []


async def test_receipt_commits_with_metadata_replays_after_reopen_and_does_not_allocate_data_step(journal, tmp_path):
    ops = OperationService(journal, actions=session_metadata.ACTIONS)
    op, _ = create(ops)
    result = await session_metadata._run(OpContext(ops, op))  # crash before operation success transition
    reopened = Journal(tmp_path / "labels.db")
    try:
        restarted = OperationService(reopened, actions=session_metadata.ACTIONS)
        assert await session_metadata._run(OpContext(restarted, op)) == result
        assert reopened.db.execute("PRAGMA user_version").fetchone()[0] == LATEST_DATA_STEP == 3
        assert reopened.db.execute("SELECT COUNT(*) FROM session_metadata").fetchone()[0] == 1
        assert reopened.db.execute("SELECT COUNT(*) FROM api_events WHERE kind='session.labels_updated'").fetchone()[0] == 1
    finally:
        reopened.close()


async def test_effect_failure_rolls_back_labels_event_and_receipt(journal, monkeypatch):
    ops = OperationService(journal, actions=session_metadata.ACTIONS)
    op, _ = create(ops)
    original = ops._step_done
    def fail(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError("simulated crash before commit")
    monkeypatch.setattr(ops, "_step_done", fail)
    with pytest.raises(RuntimeError):
        await session_metadata._run(OpContext(ops, op))
    assert session_metadata.read(journal.db, **TARGET)["version"] == 0
    assert not journal.db.execute("SELECT 1 FROM api_events WHERE kind='session.labels_updated'").fetchone()
    monkeypatch.setattr(ops, "_step_done", original)
    assert (await session_metadata._run(OpContext(ops, op)))["connector_metadata"]["version"] == 1


@pytest.mark.parametrize("values", [["x"]*2, [""], ["x"*41], [str(n) for n in range(9)], [1], ["a\nb"], ["a\u202eb"], ["\ud800"], "x"])
def test_bounded_plain_labels(journal, values):
    with pytest.raises(OperationError) as exc:
        create(OperationService(journal, actions=session_metadata.ACTIONS), values)
    assert exc.value.code in {"INVALID_PARAMS", "INVALID_REQUEST"}
    assert not journal.db.execute("SELECT 1 FROM operations").fetchone()


@pytest.mark.parametrize("target", [{"host":"h1", "session_id":"manual"}, {"host":"other", "session_id":TARGET["session_id"]},
    {**TARGET, "force":True}, {**TARGET, "session_id":"h1/other"}])
def test_only_exact_identity_no_prefix_resolution(journal, target):
    with pytest.raises(OperationError):
        create(OperationService(journal, actions=session_metadata.ACTIONS), target=target)
    assert not journal.db.execute("SELECT 1 FROM operations").fetchone()


@pytest.mark.parametrize("version", [None, True, -1, "0", 1])
def test_version_required_before_admission(journal, version):
    with pytest.raises(OperationError):
        create(OperationService(journal, actions=session_metadata.ACTIONS), version=version)


def test_manage_required_including_existing_replay(journal):
    ops = OperationService(journal, actions=session_metadata.ACTIONS)
    create(ops)
    for scopes in [[], ["observe"], ["operate"]]:
        with pytest.raises(OperationError) as exc:
            create(ops, principal=api_auth.Principal(PERSON.actor, frozenset(scopes)))
        assert exc.value.code == "FORBIDDEN"


async def test_manual_unknown_removed_host_overlay_refresh_and_api(mock, tmp_path):
    import asyncio
    d = TaskDaemon(make_config(mock, writes=False, orchestrate=False), tmp_path / "api.db")
    server = await asyncio.start_server(d._handle, "127.0.0.1", 0)
    try:
        await d.inventory.refresh_all()
        sid = "sess-claude-0001"
        before = d.inventory.get_session("h1", sid)
        frames = len(mock.invokes)
        assert before["provenance"] == "manual"
        op, _ = create(d.ops, target={"host":"h1", "session_id":sid})
        assert "git_execution" not in (op["external_refs"].get("admission_binding") or {})
        await settle_operations(d.ops)
        assert d.ops.get(op["operation_id"])["status"] == "succeeded"
        assert len(mock.invokes) == frames  # not even a policy/meta read
        after = d.inventory.get_session("h1", sid)
        assert {k:v for k,v in before.items() if k != "connector_metadata"} == {k:v for k,v in after.items() if k != "connector_metadata"}
        await d.inventory.refresh_all()
        assert d.inventory.get_session("h1",sid)["connector_metadata"]["labels"] == ["待確認","UI"]
        with d.journal.tx():
            d.journal.api_event("session", "removed/unknown-full", "session.observed", {"host":"removed","session_id":"unknown-full"})
        frames = len(mock.invokes)
        op, _ = create(d.ops, target={"host":"removed","session_id":"unknown-full"}, key="removed")
        assert "git_execution" not in (op["external_refs"].get("admission_binding") or {})
        await settle_operations(d.ops)
        assert d.ops.get(op["operation_id"])["status"] == "succeeded" and len(mock.invokes) == frames
        port = server.sockets[0].getsockname()[1]
        tok = token(d, "reader", "observe")
        code, data = await http(port, "GET", "/api/v1/sessions/removed/unknown-full", tok=tok)
        assert code == 200 and data["connector_metadata"]["version"] == 1
        assert data["session"]["provenance"] == "unknown" and data["session"]["api_access"] == "read_only"
        code, data = await http(port, "GET", "/api/v1/sessions", tok=tok)
        assert code == 200 and next(s for s in data["sessions"] if s["session_id"]==sid)["connector_metadata"]["version"] == 1
        code, data = await http(port, "GET", "/api/v1/capabilities", tok=tok)
        assert next(a for a in data["actions"] if a["action"]=="session.labels.set")["allowed"] is False
    finally:
        server.close()
        await server.wait_closed()
        await d.inventory.close()
        await d.fleet.close()
        d.journal.close()


async def test_tombstone_metadata_survives_without_inventory_and_corrupt_read_fails(journal):
    target = {"host": "retired", "session_id": "retained-session-full"}
    from bat_agent_connector.cleanup import _resource
    item = _resource("retired", "session", "creation-fixture", target["session_id"], session_id=target["session_id"],
                     original_ids=[target["session_id"], "retired/retained-session-full"])
    with journal.tx():
        journal.db.execute("INSERT INTO resource_tombstones VALUES(?,?,?,?,?,?)",
            (item["resource_id"], "generation-1", "retired", "session", json.dumps(item), 1))
        journal.db.execute("INSERT INTO cleanup_aliases VALUES(?,?,?,?)",
            ("original", "retired/retained-session-full", "retired", item["resource_id"]))
    ops = OperationService(journal, actions=session_metadata.ACTIONS)
    op, _ = create(ops, [], target=target)
    await settle_operations(ops)
    assert ops.get(op["operation_id"])["status"] == "succeeded"
    assert session_metadata.read(journal.db, **target)["version"] == 1
    journal.db.execute("UPDATE session_metadata SET labels=?", ('{"bad":"shape"}',))
    with pytest.raises(OperationError) as exc:
        session_metadata.read(journal.db, **target)
    assert exc.value.code == "METADATA_UNREADABLE"


async def test_http_and_cli_mcp_rpc_share_original_intent_and_authority(mock, tmp_path):
    import asyncio
    d = TaskDaemon(make_config(mock), tmp_path / "doors.db")
    with d.journal.tx():
        d.journal.api_event("session", "h1/manual-full-session", "session.observed", TARGET)
    server = await asyncio.start_server(d._handle, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    writer = token(d, PERSON.actor, "manage", "observe")
    body = {"action": "session.labels.set", "target": TARGET, "params": {"labels": ["Across entries"]},
            "preconditions": {"expected_version": 0}}
    try:
        code, initial = await http(port, "POST", "/api/v1/operations", tok=writer, body=body,
                                  headers={"Idempotency-Key":"same-key"})
        assert code == 202
        await settle_operations(d.ops)
        for entry in ("cli", "mcp"):
            code, out = await http(port, "POST", "/rpc", tok=writer, body={"method":"op_submit",
                "params":{**body,"idempotency_key":"same-key","entry":entry}})
            assert out["result"]["operation"]["operation_id"] == initial["operation"]["operation_id"]
            assert out["result"]["operation"]["actor"] == PERSON.actor
        observer = token(d, PERSON.actor, "observe")
        code, out = await http(port, "POST", "/rpc", tok=observer, body={"method":"op_submit",
            "params":{**body,"idempotency_key":"same-key","entry":"mcp"}})
        assert out["error"] == "FORBIDDEN"
        assert mock.invokes == []
    finally:
        server.close()
        await server.wait_closed()
        await d.inventory.close()
        await d.fleet.close()
        d.journal.close()
