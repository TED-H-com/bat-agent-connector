"""Audit-result regression through real mock TLS/WebSocket, HTTP, RPC and MCP.

Baseline d62093b8: nine post-send cases fail; three intent cases already pass.
"""
import asyncio
import json
import os

import pytest

from bat_agent_connector import safety, service
from bat_agent_connector.errors import InvokeTimeout, WriteRefused
from bat_agent_connector.mcp_server import build_server
from bat_agent_connector.task_daemon import TaskDaemon
from tests import test_api_v1 as api
from tests.conftest import adopt
from tests.operation_helpers import settle_operations, stop_workers
from tests.test_interrupt_operations import mcp_result

daemon, served = api.daemon, api.served


@pytest.mark.parametrize('siblings', [False, True])
async def test_child_audit_within_open_registered_scope(daemon, mock, monkeypatch, siblings):
    """Already passes on 70de7942; expiring a sink must preserve live children."""
    from tests.test_audit_result_warnings import inject_append_fault
    d = daemon
    adopt(api.MANUAL)
    invoke = d.fleet.client('h1').invoke
    send = service.session_send
    active = False
    arrived = []
    both = asyncio.Event()
    inject_append_fault(monkeypatch, 'open', True, active=lambda: active)

    async def overlapping(*args, **kw):
        arrived.append(True)
        if len(arrived) == 2:
            both.set()
        if siblings:
            await asyncio.wait_for(both.wait(), 10)
        return await send(*args, **kw)

    async def after(channel, params=None, **kw):
        nonlocal active
        result = await invoke(channel, params, **kw)
        if channel == 'claude:send-message':
            async def child():
                nonlocal active
                active = True
                try:
                    safety.Audit(d.fleet.config.safety).record_after_effect(
                        effect_sent=True, effect_outcome='accepted', phase='result',
                        tool='synthetic-child', channel=channel, message_id=params['clientMessageId'])
                finally:
                    active = False
            await asyncio.create_task(child())
        return result

    monkeypatch.setattr(service, 'session_send', overlapping)
    monkeypatch.setattr(d.fleet.client('h1'), 'invoke', after)
    try:
        ids = [d.ops.create(api.dashboard_principal(), action='session.send',
               target={'host': 'h1', 'session_id': api.MANUAL}, params={'text': 'synthetic live child'},
               idempotency_key='live-child-' + str(i))[0]['operation_id'] for i in range(2 if siblings else 1)]
        await settle_operations(d.ops)
        warning_ids = []
        for oid in ids:
            done = d.ops.get(oid)
            assert done['status'] == 'succeeded'
            warnings = done['external_refs']['audit_results']
            assert len(warnings) == 1
            warning_ids.extend(warnings)
            events = [e for e in d.journal.api_events(0, 500, resource_id=oid)['events']
                      if e['kind'] == 'operation.audit_result_failed']
            assert len(events) == 1 and events[0]['body']['effect_id'] in warnings
        assert len(set(warning_ids)) == len(ids)
        assert len(api.write_frames(mock)) == len(ids)
        assert safety._result_sink.get() is None
    finally:
        await stop_workers(d.ops)
        await d.fleet.close()
        await d.inventory.close()


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


# R1-F1 / R1-S1: these seven regressions fail on 70de7942.
@pytest.mark.parametrize('channel,action,params', [
    ('claude:send-message', 'session.send', {'text': 'synthetic probe'}),
    ('claude:interrupt-turn', 'session.interrupt', {'mode': 'soft'}),
    ('claude:client-resume', 'session.send', {'text': 'synthetic probe'}),
])
@pytest.mark.parametrize('failure', ['refusal', 'timeout'])
async def test_preframe_fault_retains_baseline(daemon, mock, monkeypatch, channel, action, params, failure):
    d = daemon
    adopt(api.MANUAL)
    if channel == 'claude:client-resume':
        mock.metas[api.MANUAL] = None
    client = d.fleet.client('h1')
    real_checked, real_open = client._invoke_checked, safety.open_private
    armed = False
    async def checked(canonical, *a, **kw):
        if canonical == channel:
            async def refuse():
                nonlocal armed
                armed = True
                raise (WriteRefused if failure == 'refusal' else InvokeTimeout)('synthetic pre-frame guard')
            kw['before_frame'] = refuse
        return await real_checked(canonical, *a, **kw)
    def opened(path, flags, *a, **kw):
        if armed and flags & os.O_APPEND:
            raise OSError('synthetic private audit fault')
        return real_open(path, flags, *a, **kw)
    monkeypatch.setattr(client, '_invoke_checked', checked)
    monkeypatch.setattr(safety, 'open_private', opened)
    try:
        request = dict(action=action, target={'host': 'h1', 'session_id': api.MANUAL}, params=params, idempotency_key='preframe')
        op, _ = d.ops.create(api.dashboard_principal(), **request)
        await settle_operations(d.ops)
        out = d.ops.get(op['operation_id'])
        print(json.dumps({'channel': channel, 'failure': failure, 'status': out['status'], 'code': out['error_code'], 'frames': len(api.write_frames(mock)), 'refs': out['external_refs']}))
        assert not api.write_frames(mock)
        assert (out['status'], out['error_code']) == ('failed', 'REFUSED')
        assert not out['external_refs'].get('audit_results')
        assert not [e for e in d.journal.api_events(0, 500)['events']
                    if e['kind'] == 'operation.audit_result_failed']
    finally:
        await stop_workers(d.ops)
        await d.fleet.close()
        await d.inventory.close()


async def test_spawned_task_does_not_keep_finished_sink(daemon, mock, monkeypatch):
    """A deferred legacy call must be strict after the originating step has ended."""
    d = daemon
    adopt(api.MANUAL)
    client = d.fleet.client('h1')
    invoke, real_open = client.invoke, safety.open_private
    release = asyncio.Event()
    children = []
    fail_audit = False
    async def deferred():
        await release.wait()
        return await service.session_send(d.fleet, 'h1', api.MANUAL, 'synthetic deferred call', confirm=True, message_id='deferred')
    async def after(channel, *a, **kw):
        result = await invoke(channel, *a, **kw)
        if channel == 'claude:send-message' and not children:
            children.append(asyncio.create_task(deferred()))
        return result
    def opened(path, flags, *a, **kw):
        if fail_audit and flags & os.O_APPEND:
            # Keep intent strict and successful; fail only the deferred result.
            import inspect
            if any(f.function == 'record_after_effect' for f in inspect.stack()):
                raise OSError('synthetic deferred result append failure')
        return real_open(path, flags, *a, **kw)
    monkeypatch.setattr(client, 'invoke', after)
    monkeypatch.setattr(safety, 'open_private', opened)
    try:
        op, _ = d.ops.create(api.dashboard_principal(), action='session.send', target={'host':'h1','session_id':api.MANUAL}, params={'text':'synthetic parent'}, idempotency_key='parent')
        await settle_operations(d.ops)
        assert d.ops.get(op['operation_id'])['status'] == 'succeeded'
        fail_audit = True
        release.set()
        with pytest.raises(WriteRefused, match="cannot open audit file"):
            await children[0]
        assert len(api.write_frames(mock)) == 2
        assert not d.ops.get(op['operation_id'])['external_refs'].get('audit_results')
        assert not [e for e in d.journal.api_events(0, 500)['events']
                    if e['kind'] == 'operation.audit_result_failed']
    finally:
        release.set()
        await asyncio.gather(*children, return_exceptions=True)
        await stop_workers(d.ops)
        await d.fleet.close()
        await d.inventory.close()
