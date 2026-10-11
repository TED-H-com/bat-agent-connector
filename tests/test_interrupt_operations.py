"""R01 Part B first slice: durable interrupt across the real legacy transport doors."""
from __future__ import annotations

import asyncio
import json

import pytest

from bat_agent_connector import api_actions, api_auth, cli, registry, service, task_daemon
from bat_agent_connector.errors import ConnectionLost, WriteRefused
from bat_agent_connector.mcp_server import build_server
from bat_agent_connector.operations import NO_KEY_PREFIX, OperationError
from tests import test_api_v1 as api
from tests import test_operations_unification as task_tests
from tests.conftest import adopt, controller_scope
from tests.operation_helpers import settle_operations

daemon, served, owned = api.daemon, api.served, task_tests.owned
SID = api.MANUAL
PRINCIPAL = api_auth.Principal("operator-dashboard", frozenset({"operate", "observe"}))


def arguments(**extra):
    return {"host": "h1", "session_id": SID, "mode": "soft", "confirm": True, **extra}


async def legacy(d, **extra):
    return await d.call_api("session_interrupt", arguments(**extra), PRINCIPAL)


async def rpc(port, token, method, params):
    return await api.http(port, "POST", "/rpc", tok=token, body={"method": method, "params": params})


async def mcp_result(server, name, params):
    result = await server.call_tool(name, params)
    if hasattr(result, "structuredContent") and result.structuredContent is not None:
        return result.structuredContent
    # MCP SDK versions expose either content blocks or (blocks, structured_result).
    if isinstance(result, tuple):
        return result[1]
    blocks = result.content if hasattr(result, "content") else result
    return json.loads(blocks[0].text)


@pytest.mark.parametrize("first", ["http", "mcp", "cli"])
async def test_a05_interrupt_named_key_replays_across_real_transports(served, mock, monkeypatch, capsys, first):
    d, port = served
    adopt(SID)
    tok = api.token(d, "operator-dashboard", "operate", "observe")
    monkeypatch.setenv("BATC_TASK_URL", f"http://127.0.0.1:{port}/rpc")
    monkeypatch.setenv("BATC_API_TOKEN", tok)
    monkeypatch.setattr(cli, "load_config", lambda _: d.fleet.config)
    server, fleet = build_server(d.fleet.config)
    intent = {"action": "session.interrupt", "target": {"host": "h1", "session_id": SID},
              "params": {"mode": "soft"}, "idempotency_key": "one-interrupt"}

    async def invoke(door):
        if door == "http":
            code, out = await api.http(port, "POST", "/api/v1/operations", tok=tok, body=intent)
            assert code in {200, 202}
            await settle_operations(d.ops)
            return out["operation"]["operation_id"]
        if door == "mcp":
            out = await mcp_result(server, "session_interrupt", arguments(idempotency_key="one-interrupt"))
        else:
            rc = await asyncio.to_thread(cli.main, ["--json", "interrupt", "h1", SID, "--confirm", "--key", "one-interrupt"])
            assert rc == 0
            out = json.loads(capsys.readouterr().out)
        assert out["operation_status"] == "succeeded" and out["result"] is True
        assert {"host", "session_id", "mode", "channel", "result", "note", "operation_error_code"} <= out.keys()
        assert out["idempotency_key"] == "one-interrupt" and out["idempotency_enabled"]
        return out["operation_id"]

    try:
        first_id = await invoke(first)
        for door in ("http", "mcp", "cli"):
            assert await invoke(door) == first_id
        assert len(api.write_frames(mock)) == 1
        assert not d.journal.db.execute("SELECT 1 FROM commands").fetchone()
        code, out = await rpc(port, tok, "session_interrupt", arguments(idempotency_key="one-interrupt", mode="hard"))
        assert code == 400 and out["error"] == "IDEMPOTENCY_CONFLICT"
    finally:
        await fleet.close()


async def test_a05_no_key_is_unique_storage_only_across_reads_and_reopen(served, mock, monkeypatch, capsys):
    d, port = served
    adopt(SID)
    tok = api.token(d, "operator-dashboard", "observe", "operate")
    monkeypatch.setenv("BATC_TASK_URL", f"http://127.0.0.1:{port}/rpc")
    monkeypatch.setenv("BATC_API_TOKEN", tok)
    one = await legacy(d)
    two = await legacy(d)
    assert one["operation_id"] != two["operation_id"]
    assert len(api.write_frames(mock)) == 2
    stored = d.journal.db.execute("SELECT operation_id,idem_key FROM operations").fetchall()
    assert all(row["idem_key"] == NO_KEY_PREFIX + row["operation_id"] for row in stored)
    server, fleet = build_server(d.fleet.config, principal_only=True)
    try:
        for response in [one, two, d.ops.get(one["operation_id"]), *d.ops.list()["operations"]]:
            assert response["idempotency_key"] is None and response["idempotency_enabled"] is False
            assert NO_KEY_PREFIX not in json.dumps(response)
        for path in ("/api/v1/operations", "/api/v1/operations/" + one["operation_id"]):
            code, out = await api.http(port, "GET", path, tok=tok)
            assert code == 200 and NO_KEY_PREFIX not in json.dumps(out)
        for method, params in (("op_list", {}), ("op_get", {"operation_id": one["operation_id"]})):
            _, out = await rpc(port, tok, method, params)
            assert NO_KEY_PREFIX not in json.dumps(out)
        for tool, params in (("operations_list", {}), ("operation_get", {"operation_id": one["operation_id"]})):
            assert NO_KEY_PREFIX not in json.dumps(await mcp_result(server, tool, params))
        assert await asyncio.to_thread(cli.main, ["op", one["operation_id"]]) == 0
        out = json.loads(capsys.readouterr().out)
        assert out["operation"]["idempotency_key"] is None and NO_KEY_PREFIX not in json.dumps(out)
        from bat_agent_connector.operations import OperationService
        from bat_agent_connector.task_journal import Journal
        reopened = Journal(d.journal.path)
        try:
            ops = OperationService(reopened, actions=api_actions.ACTIONS)
            assert ops.get(one["operation_id"])["idem_key"] is None
        finally:
            reopened.close()
    finally:
        await fleet.close()


@pytest.mark.parametrize("key", [None, "", "batc:nokey:caller", "  batc:nokey:caller  "])
async def test_a05_raw_operation_door_cannot_opt_into_legacy_key_mode(served, key):
    d, port = served
    tok = api.token(d, "operator-dashboard", "operate")
    body = {"action": "session.interrupt", "target": {"host": "h1", "session_id": SID},
            "idempotency_key": key, "_legacy_interrupt": True, "_resolved_target": {"session_id": SID}}
    code, out = await api.http(port, "POST", "/api/v1/operations", tok=tok, body=body)
    assert code == 422 and out["error"]["code"] in {"IDEMPOTENCY_KEY_REQUIRED", "INVALID_IDEMPOTENCY_KEY"}
    code, out = await rpc(port, tok, "op_submit", body)
    assert code == 400 and out["error"] in {"IDEMPOTENCY_KEY_REQUIRED", "INVALID_IDEMPOTENCY_KEY"}
    if key:
        code, out = await rpc(port, tok, "session_interrupt", arguments(idempotency_key=key))
        assert code == 400 and out["error"] == "INVALID_IDEMPOTENCY_KEY"
    assert not d.journal.db.execute("SELECT 1 FROM operations").fetchone()


@pytest.mark.parametrize("size", [40, 220])
async def test_a05_reserved_key_prefix_cannot_hide_in_legacy_task_hash(daemon, size):
    with pytest.raises(OperationError, match="INVALID_IDEMPOTENCY_KEY"):
        await daemon.call("work_submit", {"host": "h1", "workspace": "demo-project", "project": "p",
                                          "original_words": "saved", "idempotency_key": NO_KEY_PREFIX + "x" * size},
                          principal=PRINCIPAL)
    assert not daemon.journal.db.execute("SELECT 1 FROM operations").fetchone()
    assert not daemon.journal.db.execute("SELECT 1 FROM tasks").fetchone()


async def test_a05_prefix_resolution_is_fixed_and_replay_precedes_lookup(daemon, mock, monkeypatch):
    adopt(SID)
    out = await legacy(daemon, session_id="sess-claude", idempotency_key="prefix")
    op = daemon.ops.get(out["operation_id"])
    assert op["target"]["session_id"] == "sess-claude"
    assert op["external_refs"]["resolved_target"] == {"host": "h1", "session_id": SID}

    async def forbidden(*a, **k):
        pytest.fail("named-key replay must not resolve the selector again")
    monkeypatch.setattr(service, "_resolve_session", forbidden)
    assert (await legacy(daemon, session_id="sess-claude", idempotency_key="prefix"))["operation_id"] == out["operation_id"]
    with pytest.raises(OperationError, match="IDEMPOTENCY_CONFLICT"):
        await legacy(daemon, session_id=SID, idempotency_key="prefix")
    assert len(api.write_frames(mock)) == 1
    await daemon.fleet.close()


@pytest.mark.parametrize("replay", [False, True])
async def test_prefix_interrupt_history_uses_bound_session_even_before_bat_effect(daemon, mock, monkeypatch, replay):
    from bat_agent_connector.observation import Observation

    adopt(SID)

    async def refused(*args, **kwargs):
        raise WriteRefused("FIXTURE_REFUSED", "refused before a BAT effect")

    monkeypatch.setattr(service, "session_interrupt", refused)
    out = await legacy(daemon, session_id="sess-claude", idempotency_key="prefix-history")
    assert out["operation_status"] == "failed"
    op = daemon.ops.get(out["operation_id"])
    assert op["target"]["session_id"] == "sess-claude"
    if replay:
        seqs = [r[0] for r in daemon.journal.db.execute("SELECT seq FROM api_events ORDER BY seq")]
        with daemon.journal.tx():
            daemon.journal.db.execute("DELETE FROM api_event_context")
            daemon.journal.db.execute("DELETE FROM api_event_resources")
            for seq in seqs:
                daemon.journal._project_event(seq, None, legacy=True)
    rid = f"h1/{SID}"
    events = daemon.journal.api_events(related_resource_type="session", related_resource_id=rid)["events"]
    assert {"operation.accepted", "operation.running", "operation.failed"} <= {e["kind"] for e in events}
    assert all(e["context"]["session_resource_ids"] == [rid] for e in events)
    assert not daemon.journal.api_events(related_resource_type="session", related_resource_id="h1/sess-claude")["events"]
    assert {e["seq"] for e in Observation(daemon.journal).history("session", rid)["events"]} == {e["seq"] for e in events}
    assert not api.write_frames(mock)
    await daemon.fleet.close()


async def test_a05_legacy_keys_remain_actor_scoped(daemon, mock):
    adopt(SID)
    first = await legacy(daemon, idempotency_key="shared")
    principal = api_auth.Principal("other", frozenset({"operate"}))
    mock.metas["sess-codex-0002"]["cwd"] = "/srv/other"
    next(t for t in mock.ws_doc["terminals"] if t["id"] == "sess-codex-0002")["cwd"] = "/srv/other"
    with controller_scope(daemon.journal, principal):
        adopt("sess-codex-0002", agent_preset="codex-agent", cwd="/srv/other")
    other = await daemon.call_api("session_interrupt", arguments(session_id="sess-codex-0002", idempotency_key="shared"),
                                  principal)
    assert first["operation_id"] != other["operation_id"] and len(api.write_frames(mock)) == 2
    await daemon.fleet.close()


async def test_a05_interrupt_admission_binding_and_row_commit_together(daemon, monkeypatch):
    adopt(SID)
    original = daemon.journal.api_event
    def crash(resource_type, resource_id, kind, *a, **k):
        if kind == "operation.accepted":
            raise RuntimeError("crash before commit")
        return original(resource_type, resource_id, kind, *a, **k)
    monkeypatch.setattr(daemon.journal, "api_event", crash)
    with pytest.raises(RuntimeError, match="crash before commit"):
        await legacy(daemon, session_id="sess-claude", idempotency_key="atomic")
    assert not daemon.journal.db.execute("SELECT 1 FROM operations").fetchone()
    await daemon.fleet.close()


async def test_a01_legacy_mcp_needs_own_token_without_direct_or_admin_fallback(served, mock, monkeypatch):
    d, port = served
    adopt(SID)
    monkeypatch.delenv("BATC_API_TOKEN", raising=False)
    monkeypatch.setenv("BATC_TASK_ADMIN_TOKEN_FILE", str(d.admin_token_path))
    monkeypatch.setenv("BATC_TASK_URL", f"http://127.0.0.1:{port}/rpc")
    server, fleet = build_server(d.fleet.config)
    try:
        from tests.test_mcp_principal import call
        assert "BATC_API_TOKEN" in await call(server, "session_interrupt", arguments())
        assert not d.journal.db.execute("SELECT 1 FROM operations").fetchone() and not api.write_frames(mock)
    finally:
        await fleet.close()


async def test_a08_legacy_failed_operation_keeps_id_in_cli_and_mcp_error(served, mock, monkeypatch, capsys):
    d, port = served
    adopt(SID)
    tok = api.token(d, "operator-dashboard", "operate", "observe")
    monkeypatch.setenv("BATC_TASK_URL", f"http://127.0.0.1:{port}/rpc")
    monkeypatch.setenv("BATC_API_TOKEN", tok)
    monkeypatch.setattr(cli, "load_config", lambda _: d.fleet.config)
    async def refuse(*a, **k):
        raise WriteRefused("changed before execution")
    monkeypatch.setattr(service, "session_interrupt", refuse)
    assert await asyncio.to_thread(cli.main, ["interrupt", "h1", SID, "--confirm", "--key", "failed"]) == 1
    out = json.loads(capsys.readouterr().out)
    assert out["operation_status"] == "failed" and out["operation_error_code"] == "REFUSED"
    assert out["result"] is None
    server, fleet = build_server(d.fleet.config)
    try:
        from tests.test_mcp_principal import call
        error = await call(server, "session_interrupt", arguments(idempotency_key="failed"))
        assert out["operation_id"] in error and "REFUSED" in error
        assert not api.write_frames(mock)
    finally:
        await fleet.close()


async def test_a08_bounded_cli_wait_returns_pending_receipt_without_repeating_handler(served, mock, monkeypatch, capsys):
    d, port = served
    adopt(SID)
    monkeypatch.setenv("BATC_TASK_URL", f"http://127.0.0.1:{port}/rpc")
    monkeypatch.setenv("BATC_API_TOKEN", api.token(d, "operator-dashboard", "operate"))
    monkeypatch.setattr(cli, "load_config", lambda _: d.fleet.config)
    original_wait, original_interrupt = d.ops.wait, service.session_interrupt
    entered, release = asyncio.Event(), asyncio.Event()
    calls = []
    async def brief_wait(operation_id, timeout):
        assert timeout == 30
        return await original_wait(operation_id, 0.01)
    async def pending(*a, **k):
        calls.append(k["operation_id"])
        entered.set()
        await release.wait()
        return await original_interrupt(*a, **k)
    monkeypatch.setattr(d.ops, "wait", brief_wait)
    monkeypatch.setattr(service, "session_interrupt", pending)
    try:
        for _ in range(2):
            assert await asyncio.to_thread(cli.main, ["interrupt", "h1", SID, "--confirm", "--key", "pending"]) == 0
            out = json.loads(capsys.readouterr().out)
            assert out["operation_status"] in {"accepted", "running"} and out["result"] is None
        assert entered.is_set() and calls == [out["operation_id"]] and not api.write_frames(mock)
    finally:
        release.set()
        monkeypatch.setattr(d.ops, "wait", original_wait)
        await settle_operations(d.ops)
    assert d.ops.get(out["operation_id"])["status"] == "succeeded" and len(api.write_frames(mock)) == 1


async def test_a01_unavailable_owner_has_no_cli_or_mcp_direct_fallback(served, mock, monkeypatch, capsys):
    from bat_agent_connector import mcp_server
    from tests.test_mcp_principal import call
    d, _ = served
    adopt(SID)
    monkeypatch.setenv("BATC_API_TOKEN", api.token(d, "operator-dashboard", "operate"))
    monkeypatch.setattr(cli, "load_config", lambda _: d.fleet.config)
    def unavailable(*a, **k):
        raise ConnectionRefusedError("owner unavailable")
    monkeypatch.setattr(task_daemon, "request", unavailable)
    monkeypatch.setattr(mcp_server, "task_request", unavailable)
    assert await asyncio.to_thread(cli.main, ["interrupt", "h1", SID, "--confirm"]) == 1
    assert "outcome may be unknown" in capsys.readouterr().err
    server, fleet = build_server(d.fleet.config)
    try:
        assert "owner unavailable" in await call(server, "session_interrupt", arguments())
        assert not d.journal.db.execute("SELECT 1 FROM operations").fetchone()
        assert not api.write_frames(mock)
    finally:
        await fleet.close()


async def test_a05_bound_session_never_retargets_a_longer_prefix(daemon, mock, monkeypatch):
    adopt(SID)
    original = daemon.ops.create
    def replace_after_admission(*a, **k):
        op = original(*a, **k)
        terminal = mock.ws_doc["terminals"][0]
        terminal["id"] += "-replacement"
        adopt(terminal["id"])
        return op
    monkeypatch.setattr(daemon.ops, "create", replace_after_admission)
    result = await legacy(daemon, idempotency_key="bound")
    assert result["operation_status"] == "failed" and result["operation_error_code"] == "TASK_BINDING_MISMATCH"
    assert result["result"] is None and not api.write_frames(mock)
    await daemon.fleet.close()


@pytest.mark.parametrize("sid,mode,channel", [(SID, "soft", "claude:interrupt-turn"),
    (SID, "hard", "claude:abort-session"), ("sess-codex-0002", "soft", "claude:abort-session")])
async def test_a01_interrupt_preserves_provider_semantics(daemon, mock, sid, mode, channel):
    adopt(sid)
    out = await legacy(daemon, session_id=sid, mode=mode)
    assert out["channel"] == channel and out["mode"] == mode and out["result"] is True
    assert out["session_id"] == sid and out["host"] == "h1"
    assert (out["note"] is not None) == (sid == "sess-codex-0002")
    assert [f["channel"] for f in api.write_frames(mock)] == [channel]
    await daemon.fleet.close()


async def test_a08_lost_ack_reopens_same_step_and_only_positive_idle_settles(daemon, mock, monkeypatch):
    adopt(SID)
    mock.metas[SID]["isStreaming"] = True
    client = daemon.fleet.client("h1")
    original = client._roundtrip
    async def lost(frame, *a, **k):
        result = await original(frame, *a, **k)
        if frame["channel"] == "claude:interrupt-turn":
            raise ConnectionLost("reply lost after frame")
        return result
    monkeypatch.setattr(client, "_roundtrip", lost)
    first = await legacy(daemon, idempotency_key="lost")
    assert first["operation_status"] == "uncertain" and first["result"] is None
    await daemon.fleet.close()
    await daemon.inventory.close()
    daemon.journal.close()
    restarted = task_daemon.TaskDaemon(daemon._config, daemon._db_path)
    try:
        for meta in (None, {}, {"cwd": "/srv/demo", "isStreaming": True}, {"cwd": "/srv/demo", "isStreaming": False}):
            mock.metas[SID] = meta
            restarted.journal.db.execute("UPDATE operations SET next_run_at=0")
            out = await legacy(restarted, idempotency_key="lost")
            await settle_operations(restarted.ops)
            op = restarted.ops.get(out["operation_id"])
            assert out["operation_id"] == first["operation_id"] and out["result"] is None
            assert op["status"] == ("succeeded" if meta and meta.get("isStreaming") is False else "uncertain")
            assert len(api.write_frames(mock)) == 1
    finally:
        await restarted.ops.drain()
        await restarted.fleet.close()
        await restarted.inventory.close()
        restarted.journal.close()


@pytest.mark.parametrize("change,code", [("pause", "TASK_PAUSED"), ("version", "CONTROL_VERSION_CONFLICT"),
                                        ("owner", "TASK_BINDING_MISMATCH")])
async def test_a07_legacy_interrupt_keeps_the_final_frame_task_guard(owned, mock, monkeypatch, change, code):
    d, tid = owned
    client = d.fleet.client("h1")
    original = client._invoke_checked
    async def late(channel, *a, **k):
        if channel == "claude:interrupt-turn":
            if change == "pause":
                d.journal.db.execute("UPDATE tasks SET paused=1 WHERE task_id=?", (tid,))
            elif change == "version":
                d.journal.pause(tid)
                d.journal.resume(tid)
            else:
                registry.update("h1", SID, task_id="replacement-task")
        return await original(channel, *a, **k)
    monkeypatch.setattr(client, "_invoke_checked", late)
    out = await legacy(d, idempotency_key="guard")
    assert out["operation_status"] == "failed" and out["operation_error_code"] == code
    assert out["result"] is None and not api.write_frames(mock)
    assert d.journal.commands(tid)[0]["status"] == "rejected"
    assert (await legacy(d, idempotency_key="guard"))["operation_id"] == out["operation_id"]
    assert len(d.journal.commands(tid)) == 1


@pytest.mark.parametrize("case,code", [("manual", "MANUAL_READ_ONLY"), ("unknown", "UNKNOWN_READ_ONLY"),
                                      ("scope", "FORBIDDEN"), ("tier", "TIER_DISABLED"), ("confirm", "CONFIRM_REQUIRED")])
async def test_a01_legacy_interrupt_refused_before_operation_or_frame(served, mock, case, code):
    d, port = served
    principal_scope = "observe" if case == "scope" else "operate"
    tok = api.token(d, "operator-dashboard", principal_scope)
    params = arguments()
    if case not in {"manual", "unknown"}:
        adopt(SID)
    if case == "unknown":
        # Registry presence without creation evidence never grants control.
        registry.reserve("h1", {"session_id": SID, "status": "active"}, 10)
    if case == "tier":
        d.fleet.read_only = True
    if case == "confirm":
        params["confirm"] = False
    status, out = await rpc(port, tok, "session_interrupt", params)
    assert status == 400 and out["error"] == code
    assert not d.journal.db.execute("SELECT 1 FROM operations").fetchone()
    assert not api.write_frames(mock)


@pytest.mark.parametrize("mode", [[], {}, None, True, 0, "invalid"])
@pytest.mark.parametrize("door", ["http", "rpc"])
async def test_interrupt_rejects_malformed_mode_before_admission(served, mock, mode, door):
    d, port = served
    adopt(SID)
    tok = api.token(d, "operator-dashboard", "operate")
    if door == "http":
        status, out = await api.http(port, "POST", "/api/v1/operations", tok=tok, body={
            "action": "session.interrupt", "target": {"host": "h1", "session_id": SID},
            "params": {"mode": mode}, "idempotency_key": "invalid-interrupt-mode"})
        assert status == 422 and out["error"]["code"] == "INVALID_PARAMS"
    else:
        status, out = await rpc(port, tok, "session_interrupt", arguments(mode=mode))
        assert status == 400 and out["error"] == "INVALID_PARAMS"
    assert not d.journal.db.execute("SELECT 1 FROM operations").fetchone()
    assert not api.write_frames(mock)


@pytest.mark.parametrize("argv", [
    ["interrupt", "h1", SID, "--confirm"], ["op", "op_saved", "--cancel"],
    ["project", "create", "new"], ["item", "create", "p", "new"],
    ["checkpoint", "create", "h1", SID], ["integrate", "preview", "--host", "h1", "--repo", "o/r", "--pr", "1", "--source", "checkpoint:cp_saved"],
    ["artifact", "upload", "missing-file", "--key", "key"],
    ["delivery", "update-pr", "o/r", "1", "--metadata-digest", "d", "--body-file", "missing-file", "--key", "key"],
    ["resource-cleanup", "apply", "--preview-file", "missing-preview", "--key", "key"],
    ["api-token", "issue", "--actor", "new", "--scope", "operate"],
])
def test_a01_cli_read_only_blocks_all_mutation_doors_before_io(monkeypatch, capsys, argv):
    def forbidden(*a, **k):
        pytest.fail("read-only CLI reached daemon/config I/O")
    monkeypatch.setattr(task_daemon, "request", forbidden)
    monkeypatch.setattr(cli, "load_config", forbidden)
    assert cli.main(["--read-only", *argv]) == 1
    assert "--read-only" in capsys.readouterr().err


@pytest.mark.parametrize("argv,handler", [(["resource-cleanup", "preview", "--host", "h1"], "cmd_resource_cleanup"),
    (["artifact", "list"], "cmd_artifact"), (["integrate", "candidates", "--host", "h1"], "cmd_integrate"),
    (["project", "list"], "cmd_project")])
def test_a01_cli_read_only_preserves_pure_reads_and_previews(monkeypatch, argv, handler):
    calls = []
    monkeypatch.setattr(cli, handler, lambda args: calls.append(args.cmd) or 0)
    assert cli.main(["--read-only", *argv]) == 0 and len(calls) == 1
