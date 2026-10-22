"""The agent MCP profile cannot borrow the operator's direct Fleet/admin authority."""
from __future__ import annotations

import json

import pytest

from bat_agent_connector import __version__, task_scoped_mcp
from bat_agent_connector.mcp_server import OPERATION_TOOLS, build_server
from tests import test_api_v1 as api

daemon = api.daemon
served = api.served

LEGACY = {"hosts_list", "sessions_list", "session_send", "session_continue",
          "session_interrupt", "session_answer", "session_set_permissions",
          "session_cleanup", "worktree_remove"}
CENTRAL_ORCHESTRATION = {"worktree_merge", "session_relay", "session_failover", "fanout_plan_session", "fanout_from_plan",
                         "session_record_verification"}
TASK_WRITES = {"work_submit", "work_pause", "work_resume", "work_mark_stage"}


@pytest.mark.parametrize("entry", ["bat", "bat-task"])
async def test_identity_resource_reads_authorized_central_build_over_real_rpc(served, monkeypatch, entry):
    d, port = served
    tok = api.token(d, "identity-reader", "observe")
    monkeypatch.setenv("BATC_TASK_URL", f"http://127.0.0.1:{port}/rpc")
    monkeypatch.setenv("BATC_API_TOKEN", tok)
    status, caps = await api.http(port, "GET", "/api/v1/capabilities", tok=tok)
    assert status == 200
    if entry == "bat":
        server, fleet = build_server(d.fleet.config, principal_only=True, read_only=True)
    else:
        server, fleet = task_scoped_mcp.build("fixture-task"), None
    try:
        contents = list(await server.read_resource("batc://identity"))
        identity = json.loads(contents[0].content)
        assert identity["mcp"]["build"]["version"] == __version__
        assert identity["central"]["build"] == caps["build"]
        assert identity["central"]["status"] == "available"
        assert identity["central"]["reason"] is None
        assert identity["central"]["observed_at"] > 0
        assert not d.journal.db.execute("SELECT 1 FROM operations").fetchone()
    finally:
        if fleet:
            await fleet.close()


async def call(server, name, args):
    try:
        result = await server.call_tool(name, args)
    except Exception as exc:  # server versions may wrap errors in results or raise ToolError
        return str(exc)
    return json.dumps(result.model_dump() if hasattr(result, "model_dump") else result, default=str)


@pytest.mark.parametrize("read_only", [False, True])
async def test_agent_profile_omits_all_direct_fleet_tools_even_on_privileged_hosts(served, mock, read_only):
    d, _ = served
    server, fleet = build_server(d.fleet.config, principal_only=True, read_only=read_only)
    try:
        names = {t.name for t in await server.list_tools()}
        assert not names & LEGACY
        assert {"capabilities_get", "inventory_sessions", "work_status", "work_result", "work_events"} <= names
        assert ("session_start" in names) is (not read_only)
        assert {"workspaces_list", "session_read", "session_wait"} <= names
        if read_only:
            assert not names & (TASK_WRITES | set(OPERATION_TOOLS) | CENTRAL_ORCHESTRATION)
        else:
            assert TASK_WRITES | set(OPERATION_TOOLS) | CENTRAL_ORCHESTRATION <= names
        assert not mock.invokes
    finally:
        await fleet.close()


async def test_observe_only_agent_can_read_but_cannot_submit_or_bypass_operations(served, mock, monkeypatch):
    d, port = served
    tok = api.token(d, "scoped-reader", "observe")
    monkeypatch.setenv("BATC_TASK_URL", f"http://127.0.0.1:{port}/rpc")
    monkeypatch.setenv("BATC_API_TOKEN", tok)
    task = d.journal.submit(project="p", host="h1", workspace="ws1", original_words="saved", idempotency_key="saved")
    server, fleet = build_server(d.fleet.config, principal_only=True)
    try:
        assert "scoped-reader" in await call(server, "capabilities_get", {})
        assert task["task_id"] in await call(server, "work_status", {"task_id": task["task_id"]})
        assert task["task_id"] in await call(server, "work_result", {"task_id": task["task_id"]})
        assert "next_cursor" in await call(server, "work_events", {})
        assert "FORBIDDEN" in await call(server, "work_submit", {
            "project": "p", "host": "h1", "workspace": "ws1", "original_words": "refused", "idempotency_key": "refused"})
        for action, target, params in [
            ("session.send", {"host": "h1", "session_id": api.MANUAL}, {"text": "refused"}),
            ("project.create", {}, {"name": "refused"}),
        ]:
            assert "FORBIDDEN" in await call(server, "operation_submit", {
                "action": action, "target": target, "params": params, "idempotency_key": action, "confirm": True})
        assert "Unknown tool" in await call(server, "session_send", {})
        for name, args in [
            ("worktree_merge", {"host": "h1", "session_id": api.MANUAL}),
            ("session_relay", {"host": "h1", "session_id": api.MANUAL, "message": "refused"}),
            ("session_failover", {"host": "h1", "session_id": api.MANUAL}),
            ("fanout_plan_session", {"host": "h1", "workspace": "ws-1", "message": "refused"}),
            ("fanout_from_plan", {"host": "h1", "session_id": api.MANUAL}),
        ]:
            assert "FORBIDDEN" in await call(server, name, {**args, "confirm": True})
        assert "FORBIDDEN" in await call(server, "session_start", {"host": "h1", "workspace": "ws-1", "confirm": True})
        assert not d.journal.db.execute("SELECT 1 FROM operations").fetchone()
        assert not api.write_frames(mock)
    finally:
        await fleet.close()


async def test_profile_missing_token_never_reads_admin_file_or_uses_task_capability(served, monkeypatch, tmp_path):
    d, port = served
    admin = tmp_path / "admin.token"
    admin.write_text(d._admin_token)
    monkeypatch.setenv("BATC_TASK_ADMIN_TOKEN_FILE", str(admin))
    monkeypatch.setenv("BATC_TASK_CAPABILITY", d._admin_token)
    monkeypatch.setenv("BATC_TASK_URL", f"http://127.0.0.1:{port}/rpc")
    monkeypatch.delenv("BATC_API_TOKEN", raising=False)
    server, fleet = build_server(d.fleet.config, principal_only=True)
    try:
        for name, args in [
            ("capabilities_get", {}), ("inventory_sessions", {}), ("work_status", {"task_id": "missing"}),
            ("cleanup_preview", {"target": {"kind": "host", "host": "h1"}}),
            ("cleanup_retained", {}), ("cleanup_tombstones", {}), ("artifacts_list", {}),
            ("work_submit", {"project": "p", "host": "h1", "workspace": "ws1",
                             "original_words": "refused", "idempotency_key": "refused"}),
            ("operation_cancel", {"operation_id": "op_missing", "confirm": True}),
        ]:
            assert "BATC_API_TOKEN" in await call(server, name, args)
        assert not d.journal.db.execute("SELECT 1 FROM operations").fetchone()
    finally:
        await fleet.close()


async def test_permitted_operation_keeps_agent_identity_and_original_key(served, mock, monkeypatch):
    d, port = served
    monkeypatch.setenv("BATC_TASK_URL", f"http://127.0.0.1:{port}/rpc")
    monkeypatch.setenv("BATC_API_TOKEN", api.token(d, "scoped-writer", "observe", "manage"))
    server, fleet = build_server(d.fleet.config, principal_only=True)
    args = {"action": "project.create", "target": {}, "params": {"name": "Agent project"},
            "idempotency_key": "same-request", "confirm": True}
    try:
        first = await call(server, "operation_submit", args)
        assert "scoped-writer" in first
        assert "scoped-writer" in await call(server, "operation_submit", args)
        rows = d.journal.db.execute("SELECT actor,entry,idem_key FROM operations").fetchall()
        assert [tuple(row) for row in rows] == [("scoped-writer", "mcp", "same-request")]
        assert not api.write_frames(mock)
    finally:
        await fleet.close()


@pytest.mark.parametrize("method", ["work_status", "work_result", "work_events"])
async def test_task_reads_require_observe_scope_on_actual_rpc(served, method):
    d, port = served
    status, result = await api.http(port, "POST", "/rpc", tok=api.token(d, "write-without-observe", "operate"),
                                   body={"method": method, "params": {"task_id": "missing"}})
    assert status == 400 and result["error"] == "FORBIDDEN"
