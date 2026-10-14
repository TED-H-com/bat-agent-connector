"""Central read-only observation: actual transport, turn identity and owned lifetimes."""
from __future__ import annotations

import asyncio
import json

import pytest

from bat_agent_connector import api_auth, cli, registry
from bat_agent_connector import session_observation as observation
from bat_agent_connector.errors import BatError
from bat_agent_connector.mcp_server import build_server
from tests import test_api_v1 as api
from tests.conftest import adopt, make_config
from tests.test_mcp_principal import call

daemon = api.daemon
served = api.served
SID = "sess-claude-0001"
BUSY = "sess-codex-0002"


async def eventually(predicate):
    async def run():
        while not predicate():
            await asyncio.sleep(.005)
    await asyncio.wait_for(run(), 10)


@pytest.fixture
def tracked(monkeypatch):
    fleets = []
    original = observation.Fleet

    class TrackingFleet(original):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            fleets.append(self)

    monkeypatch.setattr(observation, "Fleet", TrackingFleet)
    return fleets


async def rpc(port, tok, method="session_read", **params):
    return await api.http(port, "POST", "/rpc", tok=tok, body={"method": method, "params": params})


@pytest.mark.parametrize("surface", ["http", "rpc"])
async def test_manual_transcript_pagination_uses_central_readonly_pool(served, mock, tracked, surface):
    d, port = served
    tok = api.token(d, "reader", "observe")
    async def read(offset):
        if surface == "http":
            code, out = await api.http(port, "GET", f"/api/v1/sessions/h1/sess-claude/messages?last_n=5&offset={offset}", tok=tok)
        else:
            code, out = await rpc(port, tok, host="h1", session_id="sess-claude", last_n=5, offset=offset)
            out = out["result"]
        assert code == 200
        return out
    first = await read(0)
    second = await read(first["next_offset"])
    assert first["session_id"] == SID
    assert [m["text"] for m in first["messages"]] == [f"live message {i}" for i in range(5, 10)]
    assert not {m["id"] for m in first["messages"]} & {m["id"] for m in second["messages"]}
    assert not registry.get("h1", SID)
    assert all(f.read_only and f.actor == "reader" for f in tracked)
    assert all(not c.connected and not c._subs for f in tracked for c in f._clients.values())
    assert not api.write_frames(mock) and not d.journal.db.execute("SELECT 1 FROM operations").fetchone()
    assert d.api.session_observation.active == 0


@pytest.mark.parametrize("method", ["session_read", "session_wait"])
@pytest.mark.parametrize("bad", [
    {"host": []}, {"session_id": {}}, {"session_id": "short"}, {"after": []},
    {"after": "x" * 513}, {"after": "bad\nmarker"}, {"after": "invalid-marker"}, {"_reader": "forged"},
])
async def test_malformed_common_request_refuses_before_fleet_or_frames(served, mock, tracked, method, bad):
    d, port = served
    code, out = await rpc(port, api.token(d, "reader", "observe"), method,
                          **{"host": "h1", "session_id": SID, **bad})
    assert code == 400 and out["error"] == "INVALID_REQUEST"
    assert not tracked and not mock.frames


@pytest.mark.parametrize(("method", "bad"), [
    ("session_read", {"last_n": True}), ("session_read", {"offset": -1}),
    ("session_read", {"offset": 1000001}), ("session_read", {"include_tools": "false"}),
    ("session_read", {"max_chars": []}), ("session_wait", {"until": []}),
    ("session_wait", {"until": "done"}), ("session_wait", {"require_new": 1}),
    ("session_wait", {"timeout_s": True}), ("session_wait", {"timeout_s": float("nan")}),
    ("session_wait", {"timeout_s": float("inf")}), ("session_wait", {"timeout_s": 10 ** 400}),
])
async def test_malformed_specific_request_refuses_before_frames(served, mock, tracked, method, bad):
    d, port = served
    code, out = await rpc(port, api.token(d, "reader", "observe"), method, host="h1", session_id=SID, **bad)
    assert code == 400 and out["error"] == "INVALID_REQUEST"
    assert not tracked and not mock.frames


@pytest.mark.parametrize("query", ["timeout_s=nan", "until=bad", "require_new=perhaps", "require_new=", "timeout_s=1&timeout_s=2", "actor=admin"])
async def test_http_wait_rejects_invalid_query_before_frames(served, mock, tracked, query):
    d, port = served
    code, out = await api.http(port, "GET", f"/api/v1/sessions/h1/{SID}/wait?{query}", tok=api.token(d, "reader", "observe"))
    assert code == 422 and out["error"]["code"] == "INVALID_REQUEST"
    assert not tracked and not mock.frames


@pytest.mark.parametrize("surface", ["http", "rpc"])
async def test_observe_scope_required_even_with_operate_start(served, mock, surface):
    d, port = served
    tok = api.token(d, "writer", "operate", "start")
    if surface == "http":
        code, out = await api.http(port, "GET", f"/api/v1/sessions/h1/{SID}/messages", tok=tok)
        assert code == 403 and out["error"]["code"] == "FORBIDDEN"
    else:
        code, out = await rpc(port, tok, host="h1", session_id=SID)
        assert code == 400 and out["error"] == "FORBIDDEN"
    assert not mock.frames


async def test_prefix_ambiguity_stops_before_any_session_probe(served, mock):
    d, port = served
    code, out = await rpc(port, api.token(d, "reader", "observe"), host="h1", session_id="sess-c")
    assert code == 400 and "ambiguous" in out["message"]
    assert mock.channels() == ["workspace:load"]


async def test_missing_claude_cwd_does_not_issue_destructive_state_read(served, mock):
    d, port = served
    mock.metas[SID].pop("cwd")
    code, _ = await rpc(port, api.token(d, "reader", "observe"), host="h1", session_id=SID)
    assert code == 200 and "claude:get-session-state" not in mock.channels()


@pytest.mark.parametrize("marker", ["batc-fixed-original", "caller-custom-message"])
async def test_exact_after_hides_old_output_then_correlates_original_echo(served, mock, marker):
    d, port = served
    tok = api.token(d, "reader", "observe")
    adopt(SID)
    registry.record_turn("h1", SID, marker, queued=False, baseline_turns=3)
    code, out = await rpc(port, tok, host="h1", session_id="sess-claude", after=marker)
    assert code == 200 and out["result"]["messages"] == [] and not out["result"]["turn_done"]
    mock.states[SID]["messages"] += [
        {"id": marker, "role": "user", "content": "original", "timestamp": 1790000020000},
        {"id": "new-reply", "role": "assistant", "content": "new result", "timestamp": 1790000020001},
    ]
    _, out = await rpc(port, tok, host="h1", session_id=SID, after=marker)
    assert [m["text"] for m in out["result"]["messages"]] == ["new result"]
    assert out["result"]["turn_done"] and out["result"]["turn_attribution"] == "correlated"
    assert not api.write_frames(mock)


async def test_central_wait_ignores_previous_idle_and_unrelated_event(served, mock, tracked):
    d, port = served
    tok = api.token(d, "reader", "observe")
    task = asyncio.create_task(rpc(port, tok, "session_wait", host="h1", session_id=SID,
                                  timeout_s=5, after="batc-echo"))
    await eventually(lambda: tracked and tracked[0]._clients.get("h1") and tracked[0]._clients["h1"]._subs)
    await mock.broadcast("agent:turn-end", {"sessionId": SID})
    await mock.broadcast("agent:turn-end", {"sessionId": "someone-else"})
    await asyncio.sleep(.03)
    assert not task.done()
    mock.states[SID]["messages"] += [
        {"id": "batc-echo", "role": "user", "content": "original", "timestamp": 1790000020000},
        {"id": "new-reply", "role": "assistant", "content": "new result", "timestamp": 1790000020001},
    ]
    await mock.broadcast("agent:turn-end", {"sessionId": SID})
    code, out = await task
    assert code == 200 and out["result"]["turn_done"]
    assert out["result"]["turn_attribution"] == "correlated"
    assert not tracked[0]._clients["h1"]._subs


@pytest.mark.parametrize("surface", ["http", "rpc"])
@pytest.mark.parametrize("change", ["revoke", "scope", "expire"])
async def test_wait_revocation_cleans_subscription_before_return(served, mock, tracked, monkeypatch, surface, change):
    d, port = served
    monkeypatch.setattr(observation, "REAUTH_S", .02)
    tok = api.token(d, "reader", "observe")
    req = (api.http(port, "GET", f"/api/v1/sessions/h1/{BUSY}/wait?timeout_s=1800", tok=tok)
           if surface == "http" else rpc(port, tok, "session_wait", host="h1", session_id=BUSY, timeout_s=1800))
    task = asyncio.create_task(req)
    await eventually(lambda: tracked and tracked[0]._clients.get("h1") and tracked[0]._clients["h1"]._subs)
    if change == "revoke":
        api_auth.revoke(d.journal.db, "reader")
    elif change == "scope":
        d.journal.db.execute("UPDATE api_principals SET scopes='[\"observe\",\"manage\"]' WHERE actor='reader'")
    else:
        d.journal.db.execute("UPDATE api_principals SET expires_at=1 WHERE actor='reader'")
    code, out = await task
    assert (code == 403 and out["error"]["code"] == "FORBIDDEN") if surface == "http" else (code == 400 and out["error"] == "FORBIDDEN")
    assert not tracked[0]._clients["h1"]._subs and not tracked[0]._clients["h1"].connected
    assert d.api.session_observation.active == 0


async def test_client_cancellation_closes_only_its_own_read_client(served, mock, tracked, monkeypatch):
    d, port = served
    monkeypatch.setenv("BATC_TASK_URL", f"http://127.0.0.1:{port}/rpc")
    # Independent coordinator/inventory connections must remain usable.
    await d.fleet.client("h1").connect()
    await d.inventory.fleet.client("h1").connect()
    task = asyncio.create_task(observation.client_request("session_wait", token=api.token(d, "reader", "observe"),
        entry="mcp", host="h1", session_id=BUSY, timeout_s=1800))
    await eventually(lambda: tracked and tracked[0]._clients.get("h1") and tracked[0]._clients["h1"]._subs)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await eventually(lambda: d.api.session_observation.active == 0)
    assert not tracked[0]._clients["h1"]._subs and not tracked[0]._clients["h1"].connected
    assert d.fleet.client("h1").connected and d.inventory.fleet.client("h1").connected


async def test_inventory_close_does_not_break_wait(served, mock, tracked):
    d, port = served
    task = asyncio.create_task(rpc(port, api.token(d, "reader", "observe"), "session_wait",
                                  host="h1", session_id=BUSY, timeout_s=5))
    await eventually(lambda: tracked and tracked[0]._clients.get("h1") and tracked[0]._clients["h1"]._subs)
    await d.inventory.close()
    await mock.broadcast("agent:turn-end", {"sessionId": BUSY})
    code, out = await task
    assert code == 200 and out["result"]["status"] == "event"


@pytest.mark.parametrize("bound", ["MAX_ACTIVE", "MAX_PER_ACTOR", "MAX_PER_HOST"])
async def test_busy_limits_do_not_construct_more_clients(served, mock, tracked, monkeypatch, bound):
    d, port = served
    monkeypatch.setattr(observation, bound, 1)
    tok = api.token(d, "reader", "observe")
    task = asyncio.create_task(rpc(port, tok, "session_wait", host="h1", session_id=BUSY, timeout_s=5))
    await eventually(lambda: d.api.session_observation.active == 1)
    code, out = await rpc(port, tok, host="h1", session_id=SID)
    assert code == 400 and out["error"] == "OBSERVATION_BUSY" and len(tracked) == 1
    await d.api.session_observation.close()
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    assert d.api.session_observation.active == 0


async def test_wall_deadline_returns_explicit_error_and_releases_client(served, mock, tracked, monkeypatch):
    d, port = served
    monkeypatch.setattr(observation, "REAUTH_S", .02)
    entered = asyncio.Event()
    async def blocked(client):
        await client.connect()
        entered.set()
        monkeypatch.setattr(observation, "READ_DEADLINE", .02)
        await asyncio.Event().wait()
    monkeypatch.setattr(observation.service, "_workspace", blocked)
    code, out = await rpc(port, api.token(d, "reader", "observe"), host="h1", session_id=SID)
    assert entered.is_set() and code == 400 and out["error"] == "OBSERVATION_TIMEOUT"
    assert d.api.session_observation.active == 0 and not tracked[0]._clients["h1"].connected


async def test_wall_deadline_during_connect_closes_half_open_socket(served, mock, tracked, monkeypatch):
    from bat_agent_connector import client as client_mod
    d, port = served
    monkeypatch.setattr(observation, "REAUTH_S", .02)
    connecting = asyncio.Event()
    orig_connect = client_mod.connect
    async def stalled_connect(*args, **kwargs):
        ws = await orig_connect(*args, **kwargs)
        async def blocked_recv():
            await eventually(lambda: len(mock.conns) == 1)
            connecting.set()
            monkeypatch.setattr(observation, "READ_DEADLINE", .02)
            await asyncio.Event().wait()
        monkeypatch.setattr(ws, "recv", blocked_recv)
        return ws
    monkeypatch.setattr(client_mod, "connect", stalled_connect)
    code, out = await rpc(port, api.token(d, "reader", "observe"), host="h1", session_id=SID)
    assert connecting.is_set() and code == 400 and out["error"] == "OBSERVATION_TIMEOUT"
    assert d.api.session_observation.active == 0
    assert not tracked[0]._clients["h1"].connected and tracked[0]._clients["h1"]._ws is None
    await eventually(lambda: not mock.conns)


@pytest.mark.parametrize("principal_only", [False, True])
async def test_mcp_readonly_profile_uses_only_central_identity(served, mock, monkeypatch, principal_only):
    d, port = served
    monkeypatch.setenv("BATC_TASK_URL", f"http://127.0.0.1:{port}/rpc")
    monkeypatch.setenv("BATC_API_TOKEN", api.token(d, "agent", "observe"))
    # The adapter's own config points nowhere; only the daemon config may reach BAT.
    cfg = make_config(mock)
    cfg.hosts.clear()
    server, local = build_server(cfg, principal_only=principal_only, read_only=True)
    try:
        assert {"session_read", "session_wait"} <= {t.name for t in await server.list_tools()}
        result = await call(server, "session_read", {"host": "h1", "session_id": SID, "last_n": 1})
        assert "live message 9" in result
        result = await call(server, "session_wait", {"host": "h1", "session_id": SID, "timeout_s": 1800})
        assert "idle" in result and not local._clients
        monkeypatch.delenv("BATC_API_TOKEN")
        before = len(mock.frames)
        assert "BATC_API_TOKEN" in await call(server, "session_read", {"host": "h1", "session_id": SID})
        assert len(mock.frames) == before
    finally:
        await local.close()


async def test_real_cli_read_wait_use_token_without_loading_local_config(served, mock, monkeypatch, capsys):
    d, port = served
    monkeypatch.setenv("BATC_TASK_URL", f"http://127.0.0.1:{port}/rpc")
    monkeypatch.setenv("BATC_API_TOKEN", api.token(d, "cli-reader", "observe"))
    for args in (["read", "h1", SID, "-n", "1"], ["wait", "h1", SID, "--timeout", "1800"]):
        code = await asyncio.to_thread(cli.main, ["--config", "/no/such/config", "--json", "--read-only", *args])
        assert code == 0
        assert json.loads(capsys.readouterr().out)["session_id"] == SID
    monkeypatch.delenv("BATC_API_TOKEN")
    before = len(mock.frames)
    code = await asyncio.to_thread(cli.main, ["read", "h1", SID])
    assert code != 0 and "BATC_API_TOKEN" in capsys.readouterr().err
    assert len(mock.frames) == before


async def test_missing_daemon_does_not_fallback(served, monkeypatch):
    d, _ = served
    # Bind then close a temporary port: no daemon exists there, and none is started.
    server = await asyncio.start_server(lambda *_: None, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    server.close()
    await server.wait_closed()
    monkeypatch.setenv("BATC_TASK_URL", f"http://127.0.0.1:{port}/rpc")
    with pytest.raises(BatError, match="no local fallback"):
        await observation.client_request("session_read", token=api.token(d, "reader", "observe"),
                                         entry="cli", host="h1", session_id=SID)


def test_clamped_wait_transport_budget_preserves_long_supported_wait():
    params = observation.validate("session_wait", {"host": "h1", "session_id": SID, "timeout_s": 9999})
    assert params["timeout_s"] == 1800
    assert observation.deadline("session_wait", params) + observation.TRANSPORT_MARGIN == 1835


async def test_authorization_rechecked_after_successful_last_read(served, mock):
    d, port = served
    tok = api.token(d, "reader", "observe")
    def revoke_after_read(_):
        api_auth.revoke(d.journal.db, "reader")
        return mock.states[SID]
    mock.handlers["claude:get-session-state"] = revoke_after_read
    code, out = await rpc(port, tok, host="h1", session_id=SID, last_n=1)
    assert code == 400 and out["error"] == "FORBIDDEN"
    assert "messages" not in out and not api.write_frames(mock)


async def test_missing_echo_wait_timeout_does_not_claim_idle_or_done(served, mock):
    d, port = served
    code, out = await rpc(port, api.token(d, "reader", "observe"), "session_wait", host="h1",
                          session_id=SID, timeout_s=1, after="batc-original-without-echo")
    assert code == 200
    assert out["result"]["status"] == "timeout" and out["result"]["turn_started"] is False
    assert out["result"]["turn_done"] is False and out["result"]["turn_attribution"] == "echo_not_visible"
    assert not api.write_frames(mock)


@pytest.mark.parametrize("host", ["removed-host", "other-host"])
async def test_unknown_host_refuses_before_client_construction(served, mock, tracked, host):
    d, port = served
    code, out = await rpc(port, api.token(d, "reader", "observe"), host=host, session_id=SID)
    assert code == 400 and out["error"] == "UNKNOWN_HOST" and not tracked and not mock.frames


@pytest.mark.parametrize("url", ["https://127.0.0.1/rpc", "http://example.invalid/rpc", "http://127.0.0.1/rpc?other=1",
                                  "http://127.0.0.1/else", "http://user@127.0.0.1/rpc"])
async def test_read_client_refuses_noncentral_endpoint_without_network(monkeypatch, url):
    monkeypatch.setenv("BATC_TASK_URL", url)
    with pytest.raises(ValueError, match="loopback"):
        await observation.client_request("session_read", token="fixture", entry="mcp", host="h1", session_id=SID)


async def test_http_disconnect_releases_wait_subscription(served, tracked):
    d, port = served
    tok = api.token(d, "reader", "observe")
    _, writer = await asyncio.open_connection("127.0.0.1", port)
    writer.write((f"GET /api/v1/sessions/h1/{BUSY}/wait?timeout_s=1800 HTTP/1.1\r\n"
                  f"Host: 127.0.0.1:{port}\r\nAuthorization: Bearer {tok}\r\n\r\n").encode())
    await writer.drain()
    await eventually(lambda: tracked and tracked[0]._clients.get("h1") and tracked[0]._clients["h1"]._subs)
    writer.close()
    await writer.wait_closed()
    await eventually(lambda: d.api.session_observation.active == 0)
    assert not tracked[0]._clients["h1"]._subs and not tracked[0]._clients["h1"].connected
