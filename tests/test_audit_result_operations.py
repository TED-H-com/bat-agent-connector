"""Audit-result regression through real mock TLS/WebSocket, HTTP, RPC and MCP.

Baseline d62093b8: nine post-send cases fail; three intent cases already pass.
"""
import asyncio
import json
import os

import pytest

from bat_agent_connector import safety
from bat_agent_connector.errors import InvokeTimeout
from bat_agent_connector.mcp_server import build_server
from bat_agent_connector.task_daemon import TaskDaemon
from tests import test_api_v1 as api
from tests.conftest import adopt
from tests.operation_helpers import settle_operations, stop_workers
from tests.test_interrupt_operations import mcp_result

daemon, served = api.daemon, api.served


@pytest.mark.parametrize("door", ["http", "rpc", "mcp"])
@pytest.mark.parametrize("case", ["accept", "bat_error", "timeout", "intent"])
async def test_audit_after_transport(served, mock, monkeypatch, tmp_path, door, case):
    d, port = served
    monkeypatch.setenv("BAT_ERROR_LOG", str(tmp_path / "errors.jsonl"))
    adopt(api.MANUAL)
    mock.echo_sends = True
    tok = api.token(d, "operator-dashboard", "observe", "operate")
    monkeypatch.setenv("BATC_TASK_URL", f"http://127.0.0.1:{port}/rpc")
    monkeypatch.setenv("BATC_API_TOKEN", tok)
    server, fleet = build_server(d.fleet.config, principal_only=True)
    real_invoke = d.fleet.client("h1").invoke
    real_open = safety.open_private
    after_roundtrip = False
    failures = 0

    if case == "bat_error":
        def rejected(_):
            raise ValueError("synthetic private remote error body")
        mock.handlers["claude:send-message"] = rejected

    async def invoke(channel, *args, **kwargs):
        nonlocal after_roundtrip
        try:
            response = await real_invoke(channel, *args, **kwargs)
        finally:
            if channel == "claude:send-message":
                after_roundtrip = True
        if channel == "claude:send-message" and case == "timeout":
            # Deliberately lose a real mock round-trip reply above transport.
            raise InvokeTimeout("synthetic lost reply after mock acceptance")
        return response

    def audit_open(path, flags, *args, **kwargs):
        nonlocal failures
        if flags & os.O_APPEND and (after_roundtrip or case == "intent"):
            failures += 1
            raise PermissionError("synthetic result audit open fault")
        return real_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(d.fleet.client("h1"), "invoke", invoke)
    monkeypatch.setattr(safety, "open_private", audit_open)
    args = {"action": "session.send", "target": {"host": "h1", "session_id": api.MANUAL},
            "params": {"text": "synthetic private instruction"}, "idempotency_key": "audit-probe"}

    async def submit():
        if door == "http":
            status, value = await api.http(port, "POST", "/api/v1/operations", tok=tok, body=args)
            assert status in {200, 202}
        elif door == "rpc":
            status, envelope = await api.http(port, "POST", "/rpc", tok=tok,
                body={"method": "op_submit", "params": {**args, "wait_s": 0}})
            assert status == 200, envelope
            value = envelope["result"]
        else:
            value = await mcp_result(server, "operation_submit", {**args, "wait_s": 0, "confirm": True})
        return value["operation"]["operation_id"]

    async def read(op_id):
        if door == "http":
            status, value = await api.http(port, "GET", f"/api/v1/operations/{op_id}", tok=tok)
            assert status == 200
        elif door == "rpc":
            status, envelope = await api.http(port, "POST", "/rpc", tok=tok,
                body={"method": "op_get", "params": {"operation_id": op_id}})
            assert status == 200, envelope
            value = envelope["result"]
        else:
            value = await mcp_result(server, "operation_get", {"operation_id": op_id})
        return value["operation"]

    try:
        op_id = await submit()
        await settle_operations(d.ops)
        done = await read(op_id)
        assert await submit() == op_id
        await settle_operations(d.ops)
        again = await read(op_id)
        assert again == done
        sends = [f for f in mock.invokes if f["channel"] == "claude:send-message"]
        assert len(sends) == (0 if case == "intent" else 1)
        assert failures == 1
        print(json.dumps({"door": door, "case": case, "status": done["status"],
            "code": done["error_code"], "response": d.journal.db.execute("SELECT response FROM operation_steps WHERE operation_id=? AND name='send'", (op_id,)).fetchone()[0],
            "send_count": len(sends), "replay_identical": again == done,
            "dispatch": done["external_refs"]["direct_send_dispatch"]}))
        expected = {"accept": ("succeeded", None), "bat_error": ("failed", "BAT_ERROR"),
                    "timeout": ("uncertain", "UNCERTAIN"), "intent": ("failed", "REFUSED")}[case]
        assert (done["status"], done["error_code"]) == expected
        warnings = done["external_refs"].get("audit_results", {})
        events = [e for e in d.journal.api_events(0, 500)["events"]
                  if e["kind"] == "operation.audit_result_failed"]
        if case == "intent":
            assert not warnings and not events
        else:
            assert len(warnings) == len(events) == 1
            effect_id, warning = next(iter(warnings.items()))
            assert warning == {"state": "failed", "code": "AUDIT_RESULT_WRITE_FAILED", "phase": "result",
                               "failure_stage": "open", "effect_outcome": {
                                   "accept": "accepted", "bat_error": "error", "timeout": "unknown"}[case]}
            assert events[0]["body"] == {"effect_id": effect_id, **warning}
            for private in (args["params"]["text"], tok, "synthetic private remote error body", str(tmp_path)):
                assert private not in json.dumps([warnings, events])
        status, _ = await api.http(port, "POST", "/api/v1/operations", tok=tok,
            body={**args, "params": {"text": "changed instruction"}})
        assert status == 409

        # A new daemon and SQLite connection read the same durable receipt.
        # The original listener remains fixture-owned but receives no further calls.
        await stop_workers(d.ops)
        await d.fleet.close()
        await d.inventory.close()
        d.release_owner()
        d.journal.close()
        restarted = TaskDaemon(d.fleet.config, d._db_path)
        listener = await asyncio.start_server(restarted._handle, "127.0.0.1", 0)
        port = listener.sockets[0].getsockname()[1]
        monkeypatch.setenv("BATC_TASK_URL", f"http://127.0.0.1:{port}/rpc")
        try:
            assert await read(op_id) == done
            assert await submit() == op_id
            await settle_operations(restarted.ops)
            assert await read(op_id) == done
            status, _ = await api.http(port, "POST", "/api/v1/operations", tok=tok,
                body={**args, "params": {"text": "changed instruction after restart"}})
            assert status == 409
            assert len([e for e in restarted.journal.api_events(0, 500)["events"]
                        if e["kind"] == "operation.audit_result_failed"]) == len(events)
            assert len([f for f in mock.invokes if f["channel"] == "claude:send-message"]) == len(sends)
            if case == "timeout":
                api.due_now(restarted, op_id)
                await settle_operations(restarted.ops)
                recovered = await read(op_id)
                assert recovered["status"] == "succeeded"
                assert recovered["result"]["settled_by"] == "bat_transcript"
                assert recovered["external_refs"]["audit_results"] == warnings
                assert len([e for e in restarted.journal.api_events(0, 500)["events"]
                            if e["kind"] == "operation.audit_result_failed"]) == 1
                assert len([f for f in mock.invokes if f["channel"] == "claude:send-message"]) == 1
        finally:
            listener.close()
            await listener.wait_closed()
            await stop_workers(restarted.ops)
            await restarted.fleet.close()
            await restarted.inventory.close()
            restarted.release_owner()
            restarted.journal.close()
    finally:
        await stop_workers(d.ops)
        await fleet.close()
