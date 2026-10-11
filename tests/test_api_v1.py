"""/api/v1 foundation: event log, API principals, OperationService, persisted inventory, HTTP surface."""

from __future__ import annotations

import asyncio
import json
import time

import pytest

from bat_agent_connector import api_auth, confinement, controller_authority, registry, service
from bat_agent_connector.channels import GUARDED_CHANNELS, ORCHESTRATE_CHANNELS, WRITE_CHANNELS
from bat_agent_connector.errors import InvokeTimeout
from bat_agent_connector.inventory import Inventory, InventorySettings
from bat_agent_connector.operations import OperationError
from bat_agent_connector.task_daemon import TaskDaemon
from bat_agent_connector.task_journal import Journal
from tests.conftest import adopt, make_config
from tests.operation_helpers import settle_operations

MANUAL = "sess-claude-0001"
BAT_WRITES = WRITE_CHANNELS | ORCHESTRATE_CHANNELS | GUARDED_CHANNELS


def write_frames(mock):
    return [i for i in mock.invokes if i["channel"] in BAT_WRITES]


@pytest.fixture
def daemon(mock, tmp_path):
    d = TaskDaemon(make_config(mock, writes=True, orchestrate=True, managed_roots=["/srv"],
                               safety={"write_min_interval_s": 0}), tmp_path / "tasks.db")
    with controller_authority.use(controller_authority.binding(d.journal.db, dashboard_principal()), d.journal):
        yield d
    d.journal.close()


def token(d, actor, *scopes):
    with d.journal.tx():
        return api_auth.issue(d.journal.db, actor, list(scopes))


async def http(port, method, path, *, tok=None, body=None, headers=None, raw_headers=None):
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    data = json.dumps(body).encode() if body is not None else b""
    lines = [f"{method} {path} HTTP/1.1", f"Host: 127.0.0.1:{port}"]
    if tok:
        lines.append("Authorization: Bearer " + tok)
    for k, v in (headers or {}).items():
        lines.append(f"{k}: {v}")
    lines += raw_headers or []
    if data or method == "POST":
        lines.append(f"Content-Length: {len(data)}")
    writer.write(("\r\n".join(lines) + "\r\n\r\n").encode() + data)
    await writer.drain()
    raw = await asyncio.wait_for(reader.read(), 10)
    writer.close()
    head, _, payload = raw.partition(b"\r\n\r\n")
    status = int(head.split(b" ")[1])
    return status, json.loads(payload) if payload else None


@pytest.fixture
async def served(daemon):
    server = await asyncio.start_server(daemon._handle, "127.0.0.1", 0)
    yield daemon, server.sockets[0].getsockname()[1]
    server.close()
    await server.wait_closed()
    await daemon.fleet.close()
    await daemon.inventory.close()


# --------------------------------------------------------------------------- event log
def test_task_events_project_into_the_api_cursor_and_old_journals_backfill(tmp_path):
    path = tmp_path / "j.db"
    j = Journal(path)
    t = j.submit(project="p", host="h1", workspace="w", original_words="do it", idempotency_key="k1")
    page = j.api_events(0, 10)
    assert page["events"] and all(e["resource_type"] == "task" and e["resource_id"] == t["task_id"]
                                  for e in page["events"])
    assert page["events"][0]["kind"].startswith("task.")
    # an older journal (no projection yet) gets a one-time backfill on open
    j.db.execute("DELETE FROM api_events")
    j.db.execute("PRAGMA user_version=0")
    j.close()
    j = Journal(path)
    again = j.api_events(0, 10)
    assert [e["body"] for e in again["events"]] == [e["body"] for e in page["events"]]
    assert j.api_events(again["next_cursor"], 10) == {"events": [], "next_cursor": again["next_cursor"],
                                                      "head_cursor": again["head_cursor"], "has_more": False}
    with pytest.raises(ValueError):
        j.api_events(-1, 10)
    j.close()


def test_api_tokens_are_hashed_scoped_and_revocable(tmp_path):
    j = Journal(tmp_path / "j.db")
    with j.tx():
        tok = api_auth.issue(j.db, "operator-dashboard", ["observe", "operate"], label="laptop")
    assert tok not in json.dumps([dict(r) for r in j.db.execute("SELECT * FROM api_principals")])
    p = api_auth.authenticate(j.db, tok, "admin-token-" + "x" * 30)
    assert p.actor == "operator-dashboard" and p.allows("operate") and not p.allows("merge")
    assert api_auth.authenticate(j.db, "admin-token-" + "x" * 30, "admin-token-" + "x" * 30).admin
    assert api_auth.authenticate(j.db, "nope", "admin-token-" + "x" * 30) is None
    for bad in (("local-admin", ["observe"]), ("x", ["root"]), ("x", []), ("bad actor!", ["observe"])):
        with pytest.raises(ValueError):
            api_auth.issue(j.db, *bad)
    with j.tx():
        assert api_auth.revoke(j.db, "operator-dashboard") == 1
    assert api_auth.authenticate(j.db, tok, "admin-token-" + "x" * 30) is None
    with j.tx():
        short = api_auth.issue(j.db, "temp", ["observe"], ttl_s=60)
    j.db.execute("UPDATE api_principals SET expires_at=? WHERE actor='temp'", (time.time() - 1,))
    assert api_auth.authenticate(j.db, short, "admin-token-" + "x" * 30) is None
    j.close()


# --------------------------------------------------------------------------- operations
def dashboard_principal(*scopes):
    return api_auth.Principal("operator-dashboard", frozenset(scopes or ("observe", "operate")))


def send_op(daemon, key="k1", sid=MANUAL, text="hello", principal=None):
    return daemon.ops.create(principal or dashboard_principal(), action="session.send",
                             target={"host": "h1", "session_id": sid}, params={"text": text},
                             idempotency_key=key)


async def test_operation_idempotency_scope_and_bat_sessions(daemon, mock):
    with pytest.raises(OperationError) as e:
        send_op(daemon)  # never observed and never created by the connector
    assert e.value.status == 403 and e.value.code == "UNKNOWN_READ_ONLY"
    await daemon.inventory.refresh_host("h1")
    with pytest.raises(OperationError) as e:
        send_op(daemon)  # the user's BAT session: refused before anything is stored
    assert e.value.status == 403 and e.value.code == "MANUAL_READ_ONLY"
    assert daemon.journal.db.execute("SELECT COUNT(*) FROM operations").fetchone()[0] == 0
    adopt(MANUAL)  # now a connector-created session in a managed root
    with pytest.raises(OperationError) as e:
        send_op(daemon, principal=dashboard_principal("observe"))
    assert e.value.status == 403 and e.value.code == "FORBIDDEN"
    op, created = send_op(daemon)
    again, created_again = send_op(daemon)
    assert created and not created_again and again["operation_id"] == op["operation_id"]
    with pytest.raises(OperationError) as e:
        send_op(daemon, text="different words")
    assert e.value.status == 409 and e.value.code == "IDEMPOTENCY_CONFLICT"
    with pytest.raises(OperationError) as foreign:
        send_op(daemon, principal=api_auth.Principal("hermes", frozenset({"operate"})))
    assert foreign.value.code == "FOREIGN_CONTROLLER"
    with pytest.raises(OperationError) as e:
        daemon.ops.create(dashboard_principal(), action="session.delete", target={}, idempotency_key="x")
    assert e.value.status == 422
    assert write_frames(mock) == []  # nothing ran yet


@pytest.mark.parametrize("kind", ["send", "answer", "interrupt"])
async def test_standalone_operation_records_no_task_refs(daemon, mock, kind):
    adopt(MANUAL)
    params = {"text": "hello"} if kind == "send" else {}
    if kind == "answer":
        mock.states[MANUAL]["pendingAskUser"] = {"toolUseId": "ask-1", "questions": [{"question": "Choice?"}]}
        params = {"answers": ["yes"], "tool_use_id": "ask-1"}
    op, _ = daemon.ops.create(dashboard_principal(), action="session." + kind, target={"host": "h1", "session_id": MANUAL},
                              params=params, idempotency_key="standalone")
    try:
        await settle_operations(daemon.ops)
        done = daemon.ops.get(op["operation_id"])
        assert done["status"] == "succeeded"
        binding = done["external_refs"]["admission_binding"]
        assert binding["controller_authority"]["actor"] == "operator-dashboard"
        assert binding["session_controller"]["session_id"] == MANUAL and "task_id" not in binding
        assert len(write_frames(mock)) == 1
        assert not daemon.journal.db.execute("SELECT 1 FROM commands").fetchone()
    finally:
        await daemon.fleet.close()
        await daemon.inventory.close()


async def test_send_operation_runs_once_and_records_steps_and_events(daemon, mock):
    adopt(MANUAL)
    mock.echo_sends = True
    op, _ = send_op(daemon)
    await settle_operations(daemon.ops)
    done = daemon.ops.get(op["operation_id"])
    assert done["status"] == "succeeded" and done["result"]["accepted"] is True
    assert done["result"]["message_id"] == "batc-" + op["operation_id"]
    assert [s["name"] for s in done["steps"]] == ["send"] and done["steps"][0]["status"] == "succeeded"
    sends = [i for i in mock.invokes if i["channel"] == "claude:send-message"]
    assert len(sends) == 1 and sends[0]["params"]["clientMessageId"] == "batc-" + op["operation_id"]
    kinds = [e["kind"] for e in daemon.journal.api_events(0, 100, resource_id=op["operation_id"])["events"]]
    assert kinds == ["operation.accepted", "operation.running", "operation.step.started",
                     "operation.step.succeeded", "operation.succeeded"]
    await settle_operations(daemon.ops)  # a finished operation never runs again
    assert len([i for i in mock.invokes if i["channel"] == "claude:send-message"]) == 1
    await daemon.fleet.close()


async def test_ambiguous_send_becomes_uncertain_and_settles_by_read_back(daemon, mock, monkeypatch):
    adopt(MANUAL)
    calls = []

    async def lost(*a, **k):
        calls.append(k["message_id"])
        raise InvokeTimeout("h1: claude:send-message timed out after 120s")

    monkeypatch.setattr(service, "session_send", lost)
    op, _ = send_op(daemon)
    await settle_operations(daemon.ops)
    row = daemon.ops.get(op["operation_id"])
    assert row["status"] == "uncertain" and row["steps"][0]["status"] == "uncertain"
    # Re-running without proof keeps it uncertain and never sends again.
    daemon.journal.db.execute("UPDATE operations SET next_run_at=0 WHERE operation_id=?", (op["operation_id"],))
    await settle_operations(daemon.ops)
    assert daemon.ops.get(op["operation_id"])["status"] == "uncertain" and len(calls) == 1
    # BAT did accept it: the turn record proves it, and the read-back settles the operation.
    registry.record_turn("h1", MANUAL, "batc-" + op["operation_id"], queued=False, baseline_turns=3)
    daemon.journal.db.execute("UPDATE operations SET next_run_at=0 WHERE operation_id=?", (op["operation_id"],))
    await settle_operations(daemon.ops)
    settled = daemon.ops.get(op["operation_id"])
    assert settled["status"] == "succeeded" and settled["result"]["accepted"] and len(calls) == 1


async def test_operation_settling_waits_for_retry_status_instead_of_drain(daemon, mock, monkeypatch):
    """A05: an old uncertain status is not completion of the newly scheduled read-back."""
    adopt(MANUAL)

    async def lost(*args, **kwargs):
        raise InvokeTimeout("send reply lost")

    monkeypatch.setattr(service, "session_send", lost)
    op, _ = send_op(daemon)
    await settle_operations(daemon.ops)
    assert daemon.ops.get(op["operation_id"])["status"] == "uncertain"
    daemon.journal.db.execute("UPDATE operations SET next_run_at=0 WHERE operation_id=?", (op["operation_id"],))
    entered, release = asyncio.Event(), asyncio.Event()

    async def slow_readback(*args, **kwargs):
        entered.set()
        await release.wait()
        return {"messages": [{"id": "batc-" + op["operation_id"], "role": "user"}]}

    async def unused_drain(*args, **kwargs):
        pytest.fail("status assertions must not rely on drain returning by its deadline")

    monkeypatch.setattr(service, "_live_state", slow_readback)
    monkeypatch.setattr(daemon.ops, "drain", unused_drain)
    worker = asyncio.create_task(settle_operations(daemon.ops))
    try:
        await asyncio.wait_for(entered.wait(), 60)
        assert daemon.ops.get(op["operation_id"])["status"] == "running"
        assert not worker.done()
    finally:
        release.set()
        await worker
    assert daemon.ops.get(op["operation_id"])["status"] == "succeeded"
    assert not daemon.ops._running(op["operation_id"])
    assert not write_frames(mock)


async def test_restart_replays_finished_steps_and_reconciles_unfinished_ones(daemon, mock, tmp_path):
    adopt(MANUAL)
    op, _ = send_op(daemon)
    # Simulate a crash after the step intent was committed but before any outcome was recorded.
    daemon.ops._transition(op["operation_id"], "running", attempts=1)
    daemon.ops._step_start(op["operation_id"], "send", {"message_id": "batc-" + op["operation_id"]})
    daemon.journal.close()
    d2 = TaskDaemon(make_config(mock, writes=True, orchestrate=True, managed_roots=["/srv"]), tmp_path / "tasks.db")
    await settle_operations(d2.ops)
    assert d2.ops.get(op["operation_id"])["status"] == "uncertain"
    assert not any(i["channel"] == "claude:send-message" for i in mock.invokes)  # never re-sent
    d2.journal.close()


async def test_cancel_before_start_never_runs(daemon, mock):
    adopt(MANUAL)
    op, _ = send_op(daemon)
    cancelled = daemon.ops.cancel(dashboard_principal(), op["operation_id"])
    assert cancelled["status"] == "cancelled"
    await settle_operations(daemon.ops)
    assert write_frames(mock) == []
    with pytest.raises(OperationError) as e:  # cancelling needs the action's scope (or being its actor)
        daemon.ops.cancel(api_auth.Principal("someone-else", frozenset({"observe"})), op["operation_id"])
    assert e.value.status == 403
    other, _ = send_op(daemon, key="k-other")
    with pytest.raises(OperationError, match="original resource controller"):
        daemon.ops.cancel(api_auth.Principal("teammate", frozenset({"operate"})), other["operation_id"])
    daemon.ops.cancel(dashboard_principal(), other["operation_id"])
    events = daemon.journal.api_events(0, 100, resource_id=other["operation_id"])["events"]
    assert events[-1]["kind"] == "operation.cancelled" and events[-1]["actor"] == "operator-dashboard"


# --------------------------------------------------------------------------- inventory
async def test_inventory_observes_read_only_and_pages_every_row_once(mock, tmp_path):
    cfg = make_config(mock, writes=True, orchestrate=True)  # write tiers on, yet observation cannot write
    j = Journal(tmp_path / "j.db")
    inv = Inventory(j, cfg)
    assert not inv.fleet.writes_enabled("h1")
    r = await inv.refresh_host("h1")
    assert r["reachable"] and r["sessions"] == 3 and r["added"] == 3
    assert write_frames(mock) == []
    seen, cursor = [], None
    while True:
        page = inv.list_sessions(limit=1, cursor=cursor)
        seen += [s["session_id"] for s in page["sessions"]]
        cursor = page["next_cursor"]
        if not cursor:
            break
    assert sorted(seen) == sorted(["sess-claude-0001", "sess-codex-0002", "sess-unload-0003"])
    row = inv.get_session("h1", MANUAL)
    assert row["provenance"] == "manual" and row["api_access"] == "read_only" and row["stale"] is False
    assert inv.list_sessions(api_access="managed")["sessions"] == []
    with pytest.raises(ValueError):
        inv.list_sessions(api_access="managed", cursor=inv.list_sessions(limit=1)["next_cursor"])
    kinds = [e["kind"] for e in j.api_events(0, 50)["events"]]
    assert kinds.count("session.added") == 3 and "host.reachable" in kinds
    again = await inv.refresh_host("h1")  # nothing material changed: no new events
    assert again["added"] == again["updated"] == 0
    await inv.close()
    j.close()


async def test_inventory_keeps_offline_hosts_stale_and_marks_gone_after_two_misses(mock, tmp_path):
    j = Journal(tmp_path / "j.db")
    inv = Inventory(j, make_config(mock), InventorySettings(stale_after_s=60))
    await inv.refresh_host("h1")
    mock.ws_doc["terminals"] = [t for t in mock.ws_doc["terminals"] if t["id"] != "sess-codex-0002"]
    await inv.refresh_host("h1")
    assert inv.get_session("h1", "sess-codex-0002")["gone_at"] is None  # one miss is not proof
    await inv.refresh_host("h1")
    gone = inv.get_session("h1", "sess-codex-0002")
    assert gone["gone_at"] and gone["stale_reason"] == "gone"
    assert "sess-codex-0002" not in [s["session_id"] for s in inv.list_sessions()["sessions"]]
    assert "sess-codex-0002" in [s["session_id"] for s in inv.list_sessions(include_gone=True)["sessions"]]
    await inv.close()
    await mock.stop()
    r = await inv.refresh_host("h1")
    assert r["reachable"] is False
    rows = inv.list_sessions()["sessions"]
    assert rows and all(s["stale"] and s["stale_reason"] == "host_unreachable" for s in rows)
    assert inv.hosts()[0]["stale"] and "host.unreachable" in [e["kind"] for e in j.api_events(0, 100)["events"]]
    await inv.close()
    j.close()


# --------------------------------------------------------------------------- HTTP
@pytest.mark.parametrize("case,status,reason,effect", [
    ("unchecked", "unknown", "unchecked_or_stale", "recheck"),
    ("stale", "unknown", "unchecked_or_stale", "recheck"),
    ("fresh", "unknown", "check_executable_untrusted", "fallback_default"),
    ("fresh", "unknown", "login_environment_writable", "fallback_default"),
    ("fresh", "unknown", "login_shell_unsupported", "fallback_default"),
    ("fresh", "unknown", "check_channel_untrusted", "fallback_default"),
    ("fresh", "mismatch", "protected_root_writable", "refused"),
    ("fresh", "unknown", "ssh_alias_unavailable", "refused"),
    ("fresh", "verified", "read_only_account_check", "verified"),
    ("undeclared", "unknown", "unchecked_or_stale", "fallback_default"),
])
async def test_a10_capabilities_account_start_effect_is_cached_read_only(served, mock, case, status, reason, effect):
    from tests.test_confinement import ACCOUNT, AccountRunner, account_observation

    class ReadOnlyRunner(AccountRunner):
        def __init__(self):
            super().__init__()
            self.calls = []

        def available(self, host):
            self.calls.append("available")
            return True

        async def run_account_check(self, *args, **kwargs):
            self.calls.append("run_account_check")
            return await super().run_account_check(*args, **kwargs)

    d, port = served
    d.fleet.config.host("h1").confinement = {**ACCOUNT, "check_max_age_s": 45} if case != "undeclared" else {}
    d.fleet.confinement_runner = ReadOnlyRunner()
    if case in {"fresh", "stale"}:
        cached = confinement.account_status(d.fleet, "h1")
        cached.update(account_observation("verified" if case == "stale" else status,
                                          "read_only_account_check" if case == "stale" else reason),
                      checked_at=time.time() - (46 if case == "stale" else 0))
        d.fleet._confinement_checks = {"h1": cached}
    tok = token(d, "test-observer", "observe")
    try:
        code, caps = await http(port, "GET", "/api/v1/capabilities", tok=tok)
        assert code == 200
        account = caps["hosts"][0]["confinement"]["host_account"]
        assert (account["status"], account["reason"], account["start_effect"]) == (status, reason, effect)
        assert account["declared"] is (case != "undeclared")
        # Daemon MCP capabilities and the CLI/MCP host read use the same projection.
        code, rpc = await http(port, "POST", "/rpc", tok=tok,
                               body={"method": "api_capabilities", "params": {}})
        assert code == 200 and rpc["result"]["hosts"][0]["confinement"]["host_account"] == account
        hosts = await service.hosts_list(d.fleet, probe=False)
        assert hosts["hosts"][0]["confinement"]["host_account"] == account
        assert not d.fleet.confinement_runner.calls and not mock.invokes
    finally:
        await d.fleet.close()


async def test_http_auth_host_origin_and_reads(served, mock):
    d, port = served
    assert (await http(port, "GET", "/api/v1/version"))[0] == 200
    assert (await http(port, "GET", "/api/v1/sessions"))[0] == 401
    viewer = token(d, "viewer", "observe")
    await d.inventory.refresh_host("h1")
    status, body = await http(port, "GET", "/api/v1/sessions?limit=2", tok=viewer)
    assert status == 200 and body["count"] == 2 and body["next_cursor"]
    status, body = await http(port, "GET", "/api/v1/sessions/h1/" + MANUAL, tok=viewer)
    assert status == 200 and body["session"]["provenance"] == "manual"
    assert (await http(port, "GET", "/api/v1/sessions/nohost/x", tok=viewer))[0] == 404
    status, caps = await http(port, "GET", "/api/v1/capabilities", tok=viewer)
    assert status == 200 and caps["actor"] == "viewer"
    assert {a["action"]: a["allowed"] for a in caps["actions"]}["session.send"] is False
    status, body = await http(port, "GET", "/api/v1/sessions", tok=viewer,
                              raw_headers=["Host: attacker.example"])  # a second Host header
    assert status == 400
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    writer.write(b"GET /api/v1/version HTTP/1.1\r\nHost: evil.example:80\r\n\r\n")
    await writer.drain()
    assert b" 400 " in (await reader.read())[:20]
    writer.close()
    status, body = await http(port, "GET", "/api/v1/hosts", tok=viewer, headers={"Origin": "https://evil.example"})
    assert status == 403 and body["error"]["code"] == "BAD_ORIGIN"
    status, body = await http(port, "GET", "/api/v1/hosts", tok=viewer, headers={"Origin": "http://127.0.0.1:9"})
    assert status == 200 and body["hosts"][0]["reachable"]
    assert (await http(port, "DELETE", "/api/v1/hosts", tok=viewer))[0] == 405
    assert write_frames(mock) == []


async def test_http_operations_end_to_end(served, mock):
    d, port = served
    operator = token(d, "operator-dashboard", "observe", "operate")
    viewer = token(d, "viewer", "observe")
    await d.inventory.refresh_host("h1")
    req = {"action": "session.send", "target": {"host": "h1", "session_id": MANUAL}, "params": {"text": "hi"}}
    status, body = await http(port, "POST", "/api/v1/operations", tok=viewer, body=req,
                              headers={"Idempotency-Key": "a1"})
    assert status == 403 and body["error"]["code"] == "FORBIDDEN"
    status, body = await http(port, "POST", "/api/v1/operations", tok=operator, body=req,
                              headers={"Idempotency-Key": "a1"})
    assert status == 403 and body["error"]["code"] == "MANUAL_READ_ONLY"
    assert (await http(port, "POST", "/api/v1/operations", tok=operator, body=req))[0] == 422  # no key
    adopt(MANUAL)
    mock.echo_sends = True
    ops_task = asyncio.create_task(d.ops.loop(interval_s=0.05))
    try:
        status, body = await http(port, "POST", "/api/v1/operations?wait=5", tok=operator, body=req,
                                  headers={"Idempotency-Key": "a1"})
        assert status == 202 and body["created"] and body["operation"]["status"] == "succeeded"
        op_id = body["operation"]["operation_id"]
        status, again = await http(port, "POST", "/api/v1/operations", tok=operator, body=req,
                                   headers={"Idempotency-Key": "a1"})
        assert status == 200 and again["created"] is False and again["operation"]["operation_id"] == op_id
        status, got = await http(port, "GET", f"/api/v1/operations/{op_id}", tok=viewer)
        assert status == 200 and got["operation"]["steps"][0]["name"] == "send"
        status, listing = await http(port, "GET", "/api/v1/operations?status=succeeded", tok=viewer)
        assert [o["operation_id"] for o in listing["operations"]] == [op_id]
        status, events = await http(port, "GET", "/api/v1/events?limit=500", tok=viewer)
        assert "operation.succeeded" in [e["kind"] for e in events["events"]]
    finally:
        ops_task.cancel()
        await asyncio.gather(ops_task, return_exceptions=True)
    assert len([i for i in mock.invokes if i["channel"] == "claude:send-message"]) == 1


async def test_sse_stream_resumes_from_last_event_id(served):
    d, port = served
    viewer = token(d, "viewer", "observe")
    with d.journal.tx():
        first = d.journal.api_event("host", "h1", "host.reachable", {})
        d.journal.api_event("host", "h1", "host.unreachable", {"error": "x"})
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    writer.write((f"GET /api/v1/events/stream HTTP/1.1\r\nHost: localhost:{port}\r\n"
                  f"Authorization: Bearer {viewer}\r\nLast-Event-ID: {first}\r\n\r\n").encode())
    await writer.drain()
    data = b""
    while b"host.unreachable" not in data:
        data += await asyncio.wait_for(reader.read(4096), 5)
    assert b"text/event-stream" in data and b"event: host.reachable" not in data
    assert f"id: {first + 1}".encode() in data
    writer.close()
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    writer.write(f"GET /api/v1/events/stream HTTP/1.1\r\nHost: localhost:{port}\r\n\r\n".encode())
    await writer.drain()
    assert b" 401 " in (await asyncio.wait_for(reader.read(), 5))[:20]
    writer.close()


async def test_rpc_doors_share_the_operation_service(served, mock):
    d, port = served

    async def rpc(tok, method, params):
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        body = json.dumps({"method": method, "params": params}).encode()
        writer.write(b"POST /rpc HTTP/1.1\r\nHost: localhost\r\nAuthorization: Bearer " + tok.encode()
                     + b"\r\nContent-Length: " + str(len(body)).encode() + b"\r\n\r\n" + body)
        await writer.drain()
        raw = await reader.read()
        writer.close()
        return json.loads(raw.split(b"\r\n\r\n", 1)[1])

    hermes = token(d, "hermes", "observe", "operate")
    out = await rpc(hermes, "op_submit", {"action": "session.send", "idempotency_key": "h1",
                                          "target": {"host": "h1", "session_id": MANUAL},
                                          "params": {"text": "x"}, "entry": "mcp"})
    assert out["error"] == "UNKNOWN_READ_ONLY" or out["error"] == "MANUAL_READ_ONLY"
    assert (await rpc(hermes, "api_token_issue", {"actor": "x", "scopes": ["observe"]}))["error"] == "ValueError"
    issued = await rpc(d._admin_token, "api_token_issue", {"actor": "grokbot", "scopes": ["observe"]})
    assert issued["result"]["token"].startswith("batc_")
    listed = await rpc(d._admin_token, "api_token_list", {})
    assert {p["actor"] for p in listed["result"]["principals"]} == {"hermes", "grokbot"}
    assert "token" not in json.dumps(listed)
    caps = await rpc(issued["result"]["token"], "api_capabilities", {})
    assert caps["result"]["actor"] == "grokbot"
    assert (await rpc(issued["result"]["token"], "op_submit", {"action": "session.send"}))["error"] == "FORBIDDEN"


async def raw_get(port, path, *, method="GET", host=None):
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    writer.write(f"{method} {path} HTTP/1.1\r\nHost: {host or f'127.0.0.1:{port}'}\r\n\r\n".encode())
    await writer.drain()
    raw = await asyncio.wait_for(reader.read(), 10)
    writer.close()
    head, _, body = raw.partition(b"\r\n\r\n")
    lines = head.decode().split("\r\n")
    hdrs = {k.lower(): v.strip() for k, _, v in (line.partition(":") for line in lines[1:])}
    return int(lines[0].split(" ")[1]), hdrs, body


async def test_dashboard_serves_only_its_static_files_with_strict_headers(served):
    _, port = served
    status, hdrs, _ = await raw_get(port, "/")
    assert status == 302 and hdrs["location"] == "/dashboard/"
    for path, ctype in [("/dashboard/", "text/html"), ("/dashboard/app.js", "text/javascript"),
                        ("/dashboard/i18n.js", "text/javascript"), ("/dashboard/app.css", "text/css")]:
        status, hdrs, body = await raw_get(port, path)
        assert status == 200 and hdrs["content-type"].startswith(ctype) and body
        assert int(hdrs["content-length"]) == len(body)
        assert "script-src 'self'" in hdrs["content-security-policy"]
        assert "frame-ancestors 'none'" in hdrs["content-security-policy"]
        assert hdrs["x-frame-options"] == "DENY" and hdrs["x-content-type-options"] == "nosniff"
    status, hdrs, body = await raw_get(port, "/dashboard/app.js", method="HEAD")
    assert status == 200 and body == b"" and int(hdrs["content-length"]) > 0
    for path in ["/dashboard/../task_daemon.py", "/dashboard/%2e%2e/api_v1.py", "/dashboard/x.js", "/favicon.ico"]:
        assert (await raw_get(port, path))[0] in {400, 404}
    assert (await raw_get(port, "/dashboard/", method="POST"))[0] == 405
    assert (await raw_get(port, "/dashboard/", host="evil.example"))[0] == 400
    # The page itself is static: no token, no data, and no inline script for the CSP to allow.
    _, _, page = await raw_get(port, "/dashboard/")
    assert b"<script type=\"module\" src=\"/dashboard/app.js\"></script>" in page
    assert b"<script>" not in page and b"batc_" not in page


# --------------------------------------------------------------------------- review follow-ups
def answer_op(daemon, key="a1", **params):
    return daemon.ops.create(dashboard_principal(), action="session.answer", target={"host": "h1", "session_id": MANUAL},
                             params={"permission": "allow", "tool_use_id": "tu1", **params}, idempotency_key=key)


def due_now(daemon, op_id):
    daemon.journal.db.execute("UPDATE operations SET next_run_at=0 WHERE operation_id=?", (op_id,))


async def test_a_failed_read_back_keeps_the_operation_uncertain(daemon, mock, monkeypatch):
    from bat_agent_connector.errors import ConnectionLost

    adopt(MANUAL)
    mock.states[MANUAL]["pendingPermission"] = {"toolUseId": "tu1", "toolName": "Bash", "input": {}}

    async def delivered_then_dropped(*a, **k):
        raise ConnectionLost("h1: connection closed")

    monkeypatch.setattr(service, "session_answer", delivered_then_dropped)
    op, _ = answer_op(daemon)
    await settle_operations(daemon.ops)
    assert daemon.ops.get(op["operation_id"])["status"] == "uncertain"

    async def host_down(*a, **k):
        raise ConnectionLost("h1: connect failed")

    monkeypatch.setattr(service, "_meta", host_down)
    due_now(daemon, op["operation_id"])
    await settle_operations(daemon.ops)
    after = daemon.ops.get(op["operation_id"])
    assert after["status"] == "uncertain" and after["steps"][0]["status"] == "uncertain"
    assert after["uncertain_tries"] == 2


async def test_answers_name_the_exact_prompt(daemon, mock):
    adopt(MANUAL)
    with pytest.raises(OperationError) as e:
        daemon.ops.create(dashboard_principal(), action="session.answer", target={"host": "h1", "session_id": MANUAL},
                          params={"permission": "allow"}, idempotency_key="no-tuid")
    assert e.value.code == "INVALID_PARAMS" and "tool_use_id" in e.value.message


async def test_a_lost_send_reply_is_settled_from_the_bat_transcript(daemon, mock, monkeypatch):
    adopt(MANUAL, agent_preset="claude-code")
    mock.echo_sends = True
    real_send = service.session_send

    async def reply_lost(*a, **k):
        await real_send(*a, **k)  # BAT took the frame and echoed the prompt
        raise InvokeTimeout("h1: claude:send-message timed out")

    monkeypatch.setattr(service, "session_send", reply_lost)
    monkeypatch.setattr(registry, "get_turn", lambda *a, **k: None)  # the local turn record never happened
    op, _ = send_op(daemon)
    await settle_operations(daemon.ops)
    assert daemon.ops.get(op["operation_id"])["status"] == "uncertain"
    # A cancel arriving now must not hide the delivered message: the read-back runs first.
    assert daemon.ops.cancel(dashboard_principal(), op["operation_id"])["status"] == "uncertain"
    await settle_operations(daemon.ops)
    done = daemon.ops.get(op["operation_id"])
    assert done["status"] == "succeeded" and done["result"]["settled_by"] == "bat_transcript"
    assert len([i for i in mock.invokes if i["channel"] == "claude:send-message"]) == 1


async def test_needs_attention_can_be_cancelled_or_resumed(daemon, mock, monkeypatch):
    adopt(MANUAL)

    async def lost(*a, **k):
        raise InvokeTimeout("h1: timed out")

    monkeypatch.setattr(service, "session_send", lost)
    monkeypatch.setattr(registry, "get_turn", lambda *a, **k: None)
    ops = [send_op(daemon, key=k)[0] for k in ("n1", "n2")]
    for op in ops:
        await settle_operations(daemon.ops)
        daemon.journal.db.execute("UPDATE operations SET uncertain_tries=5 WHERE operation_id=?",
                                  (op["operation_id"],))
        due_now(daemon, op["operation_id"])
        await settle_operations(daemon.ops)
        assert daemon.ops.get(op["operation_id"])["status"] == "needs_attention"
    cancelled = daemon.ops.cancel(dashboard_principal(), ops[0]["operation_id"])  # its worker already finished
    assert cancelled["status"] == "cancelled" and "never proven" in cancelled["status_reason"]
    resumed = daemon.ops.resume(dashboard_principal(), ops[1]["operation_id"])
    assert resumed["status"] == "running" and resumed["uncertain_tries"] == 0
    await settle_operations(daemon.ops)
    assert daemon.ops.get(ops[1]["operation_id"])["status"] == "uncertain"  # read back again, never re-sent
    with pytest.raises(OperationError) as e:
        daemon.ops.resume(dashboard_principal(), ops[0]["operation_id"])
    assert e.value.code == "NOT_RESUMABLE"


async def test_waiting_does_not_use_up_the_read_back_budget(daemon, monkeypatch):
    from bat_agent_connector.operations import ActionDef, Wait

    # One forced poll per drain, even when journal I/O outlasts the synthetic one-second Wait.
    monkeypatch.setattr("bat_agent_connector.operations.time.time", lambda: 1000.0)
    calls = {"n": 0}

    async def run(ctx):
        calls["n"] += 1
        if calls["n"] <= 6:
            raise Wait("waiting_external", "polling", delay_s=1)

        async def lost():
            raise InvokeTimeout("timed out")

        return await ctx.step("call", lost)

    daemon.ops.register(ActionDef("test.poll", "operate", "test", run))
    op, _ = daemon.ops.create(dashboard_principal(), action="test.poll", idempotency_key="poll")
    for _ in range(7):
        due_now(daemon, op["operation_id"])
        await settle_operations(daemon.ops)
    after = daemon.ops.get(op["operation_id"])
    assert after["status"] == "uncertain" and after["uncertain_tries"] == 1 and after["attempts"] == 7


async def test_http_edges(served, mock, monkeypatch):
    d, port = served
    operator_tok = token(d, "operator-dashboard", "observe", "operate")
    adopt(MANUAL)
    await d.inventory.refresh_host("h1")
    status, body = await http(port, "POST", "/api/v1/operations?wait=soon", tok=operator_tok,
                              body={"action": "session.send", "target": {"host": "h1", "session_id": MANUAL},
                                    "params": {"text": "x"}}, headers={"Idempotency-Key": "w1"})
    assert status == 422 and d.ops.list()["operations"] == []  # refused before anything was stored
    status, _ = await http(port, "POST", "/api/v1/operations", tok=operator_tok,
                           raw_headers=["Content-Length: -5"])
    assert status == 400
    writer_only = token(d, "writer", "operate")
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    writer.write((f"GET /api/v1/events/stream HTTP/1.1\r\nHost: localhost\r\n"
                  f"Authorization: Bearer {writer_only}\r\n\r\n").encode())
    await writer.drain()
    assert b" 403 " in (await asyncio.wait_for(reader.read(), 5))[:20]
    writer.close()


async def test_event_streams_stop_on_revocation_and_are_capped_per_actor(served, monkeypatch):
    from bat_agent_connector import api_v1

    d, port = served
    monkeypatch.setattr(api_v1, "REAUTH_S", 0.2)
    monkeypatch.setattr(api_v1, "MAX_STREAMS_PER_ACTOR", 1)
    viewer = token(d, "viewer", "observe")

    async def open_stream():
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        writer.write((f"GET /api/v1/events/stream HTTP/1.1\r\nHost: localhost\r\n"
                      f"Authorization: Bearer {viewer}\r\n\r\n").encode())
        await writer.drain()
        return reader, writer

    r1, w1 = await open_stream()
    assert b" 200 " in (await asyncio.wait_for(r1.read(64), 5))
    r2, w2 = await open_stream()
    assert b" 429 " in (await asyncio.wait_for(r2.read(), 5))[:20]
    w2.close()
    with d.journal.tx():
        api_auth.revoke(d.journal.db, "viewer")
    assert await asyncio.wait_for(r1.read(), 5) is not None  # the server ends the stream
    assert r1.at_eof()
    w1.close()


async def test_cors_only_for_listed_origins(mock, tmp_path):
    cfg = make_config(mock, writes=True, orchestrate=True)
    d = TaskDaemon(cfg, tmp_path / "t.db")
    d.api.allowed_origins = ("https://hub.example",)
    server = await asyncio.start_server(d._handle, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    try:
        async def raw(method, origin):
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            writer.write(f"{method} /api/v1/version HTTP/1.1\r\nHost: localhost\r\nOrigin: {origin}\r\n\r\n".encode())
            await writer.drain()
            head = (await asyncio.wait_for(reader.read(), 5)).split(b"\r\n\r\n")[0].decode()
            writer.close()
            return head

        pre = await raw("OPTIONS", "https://hub.example")
        assert " 204 " in pre and "Access-Control-Allow-Origin: https://hub.example" in pre
        assert "Access-Control-Allow-Headers: Authorization" in pre
        assert "Access-Control-Allow-Origin: https://hub.example" in await raw("GET", "https://hub.example")
        local = await raw("GET", "http://127.0.0.1:9")
        assert " 200 " in local and "Access-Control-Allow-Origin" not in local
        assert " 403 " in await raw("OPTIONS", "http://127.0.0.1:9")
    finally:
        server.close()
        await server.wait_closed()
        await d.fleet.close()
        await d.inventory.close()
        d.journal.close()


async def test_inventory_keeps_what_one_refresh_did_not_observe(mock, tmp_path):
    cfg = make_config(mock, writes=True)
    journal = Journal(tmp_path / "inv.db")
    inv = Inventory(journal, cfg, InventorySettings(activity_every=5))
    try:
        await inv.refresh_host("h1")  # run 0 reads every activity source
        first = inv.get_session("h1", MANUAL)
        assert first["last_activity_ms"]
        mock.states[MANUAL]["pendingAskUser"] = {"toolUseId": "tu9", "questions": [{"question": "Q?"}]}
        mock.metas[MANUAL]["isStreaming"] = True  # makes the auto check read state once
        await inv.refresh_host("h1")
        assert inv.get_session("h1", MANUAL)["pending"]["toolUseId"] == "tu9"
        mock.metas[MANUAL]["isStreaming"] = False  # the next cheap refresh does not re-read state
        await inv.refresh_host("h1")
        again = inv.get_session("h1", MANUAL)
        assert again["pending"]["toolUseId"] == "tu9"  # unobserved is not "answered"
        assert again["last_activity_ms"] >= first["last_activity_ms"]  # activity never moves backwards
        mock.handlers["workspace:load"] = lambda p: None  # a null workspace document
        for _ in range(3):
            r = await inv.refresh_host("h1")
        assert r["reachable"] is False and inv.get_session("h1", MANUAL)["gone_at"] is None
        with journal.tx():
            journal.db.execute("""INSERT INTO sessions_observed(host,session_id,body,digest,provenance,api_access,
                first_seen_at,last_seen_at) VALUES('old-host','s1','{}','d','manual','read_only',0,0)""")
        assert "old-host" not in {s["host"] for s in inv.list_sessions(include_gone=True)["sessions"]}
    finally:
        await inv.close()
        journal.close()


async def test_a10_session_capabilities_inventory_and_triage_share_evidence(served, mock):
    from bat_agent_connector import orchestrate, triage
    d, port = served
    r = await orchestrate.session_start(d.fleet, "h1", "demo-project", "codex", confirm=True, write_scope="confined")
    sid = r["session_id"]
    await d.inventory.refresh_host("h1")
    tok = token(d, "test-observer", "observe")
    status, caps = await http(port, "GET", "/api/v1/capabilities", tok=tok)
    assert status == 200 and caps["hosts"][0]["confinement"]["agents"]["codex"]["gap"] == "sandbox_enforcement_unverified"
    assert caps["hosts"][0]["confinement"]["network_configurable"] is False
    status, detail = await http(port, "GET", f"/api/v1/sessions/h1/{sid}", tok=tok)
    assert status == 200 and detail["session"]["confinement"] == r["confinement"]
    status, listing = await http(port, "GET", "/api/v1/sessions", tok=tok)
    row = next(s for s in listing["sessions"] if s["session_id"] == sid)
    assert row["confinement"] == r["confinement"] and row["write_scope"] == "confined"
    rows = (await triage.sessions_triage(d.fleet, use_jev="never"))["sessions"]
    assert next(s for s in rows if s["session_id"] == sid)["confinement"] == r["confinement"]
    manual = next(s for s in listing["sessions"] if s["session_id"] == MANUAL)
    assert manual["confinement"]["level"] == "none" and manual["api_access"] == "read_only"
    # The observation detail wrapper must enrich the row it actually returns.
    mock.metas[sid]["codexSandboxMode"] = "danger-full-access"
    status, live = await http(port, "GET", f"/api/v1/sessions/h1/{sid}?live=true", tok=tok)
    assert status == 200 and live["history_available"] is True
    assert live["session"]["current_verification"]["status"] == "mismatch"
    assert live["session"]["confinement"] == r["confinement"]


async def test_a10_cached_legacy_inventory_exposes_unknown_evidence_without_rewriting(served):
    d, port = served
    await d.inventory.refresh_host("h1")
    row = d.journal.db.execute("SELECT body FROM sessions_observed WHERE host=? AND session_id=?",
                               ("h1", MANUAL)).fetchone()
    body = json.loads(row[0])
    for field in ("confinement", "current_verification", "write_scope"):
        body.pop(field, None)
    original = json.dumps(body)
    d.journal.db.execute("UPDATE sessions_observed SET body=? WHERE host=? AND session_id=?", (original, "h1", MANUAL))
    tok = token(d, "test-observer", "observe")
    status, out = await http(port, "GET", "/api/v1/sessions", tok=tok)
    legacy = next(s for s in out["sessions"] if s["session_id"] == MANUAL)
    assert status == 200 and legacy["confinement"]["level"] == "none"
    assert legacy["confinement"]["verification"]["status"] == "unknown"
    assert d.journal.db.execute("SELECT body FROM sessions_observed WHERE host=? AND session_id=?",
                                ("h1", MANUAL)).fetchone()[0] == original
    status, hosts = await http(port, "GET", "/api/v1/hosts", tok=tok)
    assert status == 200 and hosts["hosts"][0]["confinement"]["host_account"]["declared"] is False
    d.journal.db.execute("UPDATE hosts_observed SET reachable=0 WHERE host=?", ("h1",))
    assert d.inventory.get_session("h1", MANUAL)["current_verification"]["reason"] == "inventory_stale"
