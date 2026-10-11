"""Task controls preserve caller keys without presenting storage IDs as retry keys."""
import asyncio
import json

import pytest

from bat_agent_connector import api_actions, api_auth, cli
from bat_agent_connector.mcp_server import build_server
from bat_agent_connector.operations import NO_KEY_PREFIX, OperationError, OperationService
from bat_agent_connector.task_journal import Journal
from tests import test_api_v1 as api
from tests import test_operations_unification as tasks
from tests.test_interrupt_operations import mcp_result, rpc

daemon, served, owned = api.daemon, api.served, tasks.owned


def unkeyed(result):
    assert result["idempotency_key"] is None
    assert result["idempotency_enabled"] is False
    assert NO_KEY_PREFIX not in json.dumps(result)
    assert "legacy-request:" not in json.dumps(result)


@pytest.mark.parametrize("method", ["work_pause", "work_resume"])
async def test_unkeyed_controls_have_distinct_operations_and_real_effects(owned, method):
    d, tid = owned
    one = await tasks.call_owned(d, method, {"task_id": tid})
    two = await tasks.call_owned(d, method, {"task_id": tid})
    assert one["operation_id"] != two["operation_id"]
    assert d.journal.get(tid)["control_version"] == 2
    for result in (one, two, d.ops.get(one["operation_id"]), *d.ops.list()["operations"]):
        unkeyed(result)
    rows = d.journal.db.execute("SELECT operation_id,idem_key FROM operations").fetchall()
    assert all(row["idem_key"] == NO_KEY_PREFIX + row["operation_id"] for row in rows)
    reopened = Journal(d.journal.path)
    try:
        ops = OperationService(reopened, actions=api_actions.ACTIONS)
        unkeyed(ops.get(one["operation_id"]))
    finally:
        reopened.close()


async def test_task_control_key_projection_survives_rpc_mcp_http_and_cli_reads(served, monkeypatch, capsys):
    d, port = served
    task = d.journal.submit(project="p", host="h1", workspace="demo-project", original_words="fixed task",
                            idempotency_key="fixture")
    tid = task["task_id"]
    token = api.token(d, "operator-dashboard", "observe", "operate")
    monkeypatch.setenv("BATC_TASK_URL", f"http://127.0.0.1:{port}/rpc")
    monkeypatch.setenv("BATC_API_TOKEN", token)
    server, fleet = build_server(d.fleet.config, principal_only=True)
    try:
        code, out = await rpc(port, token, "work_pause", {"task_id": tid})
        assert code == 200
        first = out["result"]
        second = await mcp_result(server, "work_resume", {"task_id": tid})
        assert first["operation_id"] != second["operation_id"]
        for result in (first, second):
            unkeyed(result)
            code, out = await api.http(port, "GET", "/api/v1/operations/" + result["operation_id"], tok=token)
            assert code == 200
            unkeyed(out["operation"])
            assert await asyncio.to_thread(cli.main, ["op", result["operation_id"]]) == 0
            unkeyed(json.loads(capsys.readouterr().out)["operation"])
        for result in (await mcp_result(server, "operations_list", {}))["operations"]:
            unkeyed(result)
        assert d.journal.get(tid)["control_version"] == 2
    finally:
        await fleet.close()


async def test_named_and_historical_keys_preserve_replay_and_conflict(owned):
    d, tid = owned
    params = {"task_id": tid, "idempotency_key": "original"}
    first = await tasks.call_owned(d, "work_pause", params)
    assert first["idempotency_key"] == "original" and first["idempotency_enabled"]
    assert await tasks.call_owned(d, "work_pause", params) == first
    with pytest.raises(OperationError, match="IDEMPOTENCY_CONFLICT"):
        await tasks.call_owned(d, "work_pause", {**params, "abort_current": True})
    assert d.journal.get(tid)["control_version"] == 1
    # Historical Part A rows retain their old keys. Do not rewrite IDs, receipts or history.
    historical = "legacy-request:" + "a" * 32
    d.journal.db.execute("UPDATE operations SET idem_key=? WHERE operation_id=?", (historical, first["operation_id"]))
    result = await tasks.call_owned(d, "work_pause", {**params, "idempotency_key": historical})
    assert result["operation_id"] == first["operation_id"]
    assert result["idempotency_key"] == historical and result["idempotency_enabled"]
    assert d.journal.get(tid)["control_version"] == 1


@pytest.mark.parametrize("key", [None, "", NO_KEY_PREFIX + "spoof"])
async def test_public_operation_cannot_enable_task_compatibility_mode(served, key):
    d, port = served
    token = api.token(d, "caller", "operate")
    body = {"action": "task.pause", "target": {"task_id": "unresolved"}, "idempotency_key": key,
            "_legacy_task": True, "_legacy_session": True}
    code, out = await api.http(port, "POST", "/api/v1/operations", tok=token, body=body)
    assert code == 422 and out["error"]["code"] in {"IDEMPOTENCY_KEY_REQUIRED", "INVALID_IDEMPOTENCY_KEY"}
    code, out = await rpc(port, token, "op_submit", body)
    assert code == 400 and out["error"] in {"IDEMPOTENCY_KEY_REQUIRED", "INVALID_IDEMPOTENCY_KEY"}
    assert not d.journal.db.execute("SELECT 1 FROM operations").fetchone()


async def test_submit_still_requires_its_original_explicit_key_and_task_scope(daemon):
    with pytest.raises(OperationError, match="IDEMPOTENCY_KEY_REQUIRED"):
        await daemon.call("work_submit", {"host": "h1", "workspace": "demo-project", "project": "p",
                                          "original_words": "fixed"})
    with pytest.raises(OperationError, match="FORBIDDEN"):
        await daemon.call("work_pause", {"task_id": "unresolved"},
                          principal=api_auth.Principal("reader", frozenset({"observe"})))
    with pytest.raises(OperationError, match="IDEMPOTENCY_KEY_REQUIRED"):
        daemon.ops.create(api_auth.Principal("admin", frozenset(), admin=True), action="session.interrupt",
                          target={"host": "h1", "session_id": api.MANUAL}, idempotency_key=None, _legacy_task=True)
    assert not daemon.journal.db.execute("SELECT 1 FROM operations").fetchone()


async def test_task_send_keeps_existing_step_identity_and_does_not_repeat(owned, mock):
    d, tid = owned
    params = {"task_id": tid, "text": "one fixed instruction", "step_id": "step-one"}
    first = await tasks.call_owned(d, "task_send", params)
    frames = list(tasks.writes(mock))
    assert first["idempotency_key"] == f"task-step:{tid}:step-one" and first["idempotency_enabled"]
    assert await tasks.call_owned(d, "task_send", params) == first
    assert tasks.writes(mock) == frames
    with pytest.raises(OperationError, match="IDEMPOTENCY_CONFLICT"):
        await tasks.call_owned(d, "task_send", {**params, "text": "different instruction"})
    assert tasks.writes(mock) == frames
