"""Public exits for the E10 operation loop projection.

HTTP, RPC and the principal MCP server build the states and read ``loop``.
"""
from __future__ import annotations

import asyncio
import json
import time
import uuid

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from bat_agent_connector import (
    pm_discussions,
    pm_evidence,
    pm_work_start,
    pm_workspace,
)
from bat_agent_connector.api_auth import Principal
from bat_agent_connector.mcp_server import build_server
from bat_agent_connector.pm_delivery_lineage import ACTIONS as DELIVERY_ACTIONS
from bat_agent_connector.pm_operation_loop import LOOP_ACTIONS
from tests.operation_helpers import settle_operations
from tests.test_api_v1 import MANUAL, http, token
from tests.test_api_v1 import daemon as daemon_fixture
from tests.test_api_v1 import served as served_fixture
from tests.test_operations_unification import capability_caller
from tests.test_operations_unification import owned as task_owned_fixture
from tests.test_pm_plan_operations import OWNER as PACKAGE_OWNER
from tests.test_pm_work_packages import action as pkg_action
from tests.test_pm_work_packages import member as add_member
from tests.test_pm_work_packages import ready as ready_fixture
from tests.test_pm_work_packages import request as package_request
from tests.test_pm_work_start import STARTER_OWNER, _advance_start, _start_envelope
from tests.test_pm_worktree import pm as pm_fixture
from tests.test_repository_sync import world as world_fixture

daemon, served = daemon_fixture, served_fixture
ready, pm, world = ready_fixture, pm_fixture, world_fixture
task_owned = task_owned_fixture


async def test_allowlist_is_the_section_32_list():
    assert LOOP_ACTIONS == frozenset(pm_discussions.ACTIONS) | {
        "pm.run.analyze", "pm.run.bat.analyze",
        "pm.conversation.propose", "pm.conversation.adopt", "pm.plan.draft", "pm.plan.adopt",
        "pm.work_package.create", "work.start", "session.send",
        "github.pr.publish", "github.pr.update", "pm.maintenance.repair",
    } | DELIVERY_ACTIONS
    assert "work.adopt_and_start" not in LOOP_ACTIONS
    assert "repository.continue" not in LOOP_ACTIONS


def test_session_send_completeness_stays_partial_for_unknown_targets():
    """A Work id on session.send is not the registered target and is not complete."""
    from bat_agent_connector.pm_operation_loop import _completeness

    kwargs = dict(
        dropped=False, delivery_unlinked=False, expects_work=False, work_visible=False,
        expects_attempt=False, attempt_present=False, attempt_unreadable=False)
    formed = "wi_" + "ab" * 10
    targets = (
        None, "not-a-dict", [], {},
        {"host": "h1", "session_id": "s1"},
        {"host": "h1", "session_id": "s1", "work_item_id": None},
        {"host": "h1", "session_id": "s1", "work_item_id": ""},
        {"host": "h1", "session_id": "s1", "work_item_id": "not-a-work"},
        {"host": "h1", "session_id": "s1", "work_item_id": formed},
        {"host": "h1", "session_id": "s1", "work_item_id": {"bad": True}},
        {"host": "h1", "session_id": "s1", "work_item_id": 1},
        {"work_item_id": formed},
    )
    partial = {"status": "partial", "reason": "session_target"}
    for target in targets:
        got = _completeness({"action": "session.send", "target": target}, **kwargs)
        assert got == partial, target
        assert got["status"] != "complete"
    withdrawn = _completeness(
        {"action": "session.send", "target": {"work_item_id": formed}}, **{**kwargs, "dropped": True})
    assert withdrawn == {"status": "unavailable", "reason": "source_withdrawn"}


def test_malformed_work_id_cannot_become_complete():
    """A non-id is not a visible Work, so the loop stays partial."""
    from bat_agent_connector.pm_operation_loop import _completeness, _work_id

    assert _work_id("not-a-work") is False
    assert _work_id("") is False
    assert _work_id(None) is False
    assert _work_id({"bad": True}) is False
    assert _work_id(1) is False
    got = _completeness(
        {"action": "work.start", "target": {"work_item_id": "not-a-work"}},
        dropped=False, delivery_unlinked=False, expects_work=True, work_visible=False,
        expects_attempt=True, attempt_present=True, attempt_unreadable=False)
    assert got == {"status": "partial", "reason": "no_work_item"}
    assert got["status"] != "complete"


def _open_mcp(daemon, monkeypatch, port, tok):
    monkeypatch.setenv("BATC_TASK_URL", f"http://127.0.0.1:{port}/rpc")
    monkeypatch.setenv("BATC_API_TOKEN", tok)
    return build_server(daemon.fleet.config, principal_only=True)


async def _loops(server, port, tok, operation_id):
    status, body = await http(port, "GET", "/api/v1/operations/" + operation_id, tok=tok)
    assert status == 200, body
    via_mcp = unpack(await server.call_tool("operation_get", {"operation_id": operation_id}))
    assert body["operation"]["loop"] == via_mcp["operation"]["loop"]
    return body["operation"]["loop"]


def _endpoint_rows(rows):
    return [row for row in rows if row.get("kind") == "operation_endpoint"]


def _steering_blocks(rows):
    return [row for row in rows if row.get("action") in {"cancel", "resume", "work.start"}]


async def test_discussion_hash_matches_across_http_and_mcp(served, monkeypatch):
    """A discussion names its thread. A workspace operation is not on the allowlist."""
    daemon, port = served
    actor = Principal("loop-discuss", frozenset({"observe", "manage"}))
    tok = token(daemon, actor.actor, "observe", "manage")
    server, fleet = _open_mcp(daemon, monkeypatch, port, tok)
    try:
        status, workspace = await _submit(
            port, tok, {"action": "pm.workspace.create", "params": {"name": "A useful product"}},
            "loop-hash-workspace")
        assert status == 202 and workspace["created"] is True, workspace
        assert "loop" not in workspace["operation"]
        await settle_operations(daemon.ops)
        stored = daemon.ops.get(workspace["operation"]["operation_id"])
        assert stored["status"] == "succeeded" and "loop" not in stored
        wid = stored["result"]["workspace"]["workspace_id"]
        version = stored["result"]["workspace"]["version"]
        status, created = await _submit(port, tok, {
            "action": "pm.thread.create", "target": {"workspace_id": wid},
            "params": {"title": "Paid membership"}, "preconditions": {"expected_version": version},
        }, "loop-hash-thread")
        assert status == 202 and created["created"] is True, created
        await settle_operations(daemon.ops)
        thread = daemon.ops.get(created["operation"]["operation_id"])
        assert thread["status"] == "succeeded", thread
        thread_id = thread["result"]["thread_id"]
        loop = await _loops(server, port, tok, thread["operation_id"])
        assert loop["dashboard_hash"] == f"#/pm/{wid}/discussions/{thread_id}"
        assert loop["work_item_id"] is None and loop["attempt_id"] is None
        assert loop["links"] is None and loop["result_read"] is None
        assert loop["completeness"] == {"status": "complete", "reason": None}
        assert loop["next_allowed_actions"] == [] and loop["blocked_actions"] == []

        current = pm_discussions.get_thread(daemon.ops, actor, wid, thread_id)["thread"]["version"]
        status, message = await _submit(port, tok, {
            "action": "pm.message.append", "target": {"workspace_id": wid, "thread_id": thread_id},
            "params": {"content": "A paid tier may be useful"},
            "preconditions": {"expected_version": current},
        }, "loop-hash-message")
        assert status == 202 and message["operation"]["loop"]["dashboard_hash"] == loop["dashboard_hash"]
        await settle_operations(daemon.ops)
        message_loop = await _loops(server, port, tok, message["operation"]["operation_id"])
        assert message_loop["dashboard_hash"] == loop["dashboard_hash"]
        assert message_loop["work_item_id"] is None
        assert message_loop["completeness"] == {"status": "complete", "reason": None}
    finally:
        await fleet.close()


async def test_package_loop_names_its_work(ready, monkeypatch):
    """A package operation carries the Work it names, on HTTP and MCP."""
    w = ready
    daemon, port = w["d"], w["port"]
    tok = token(daemon, PACKAGE_OWNER.actor, *sorted(PACKAGE_OWNER.scopes))
    server, fleet = _open_mcp(daemon, monkeypatch, port, tok)
    try:
        env = package_request(w)
        body = {key: env[key] for key in ("action", "target", "params", "preconditions")}
        status, created = await _submit(port, tok, body, env["idempotency_key"])
        assert status == 202 and created["created"] is True, created
        work_id = w["target"]["work_item_id"]
        loop = await _loops(server, port, tok, created["operation"]["operation_id"])
        assert loop == created["operation"]["loop"]
        assert loop["work_item_id"] == work_id and loop["attempt_id"] is None
        assert loop["dashboard_hash"] == f"#/pm/{w['workspace_id']}/board?select={work_id}"
        assert loop["links"] is None and loop["result_read"] is None
        assert loop["completeness"] == {"status": "complete", "reason": None}
    finally:
        await fleet.close()


async def test_work_start_actions_match_the_result_envelope(loop_start, monkeypatch):
    """An accepted work.start copies this principal's Result steering rows."""
    w = loop_start
    daemon, ops, port = w["d"], w["d"].ops, w["port"]
    wid, work_id = w["workspace_id"], w["target"]["work_item_id"]
    tok = token(daemon, STARTER_OWNER.actor, *sorted(STARTER_OWNER.scopes))
    server, fleet = _open_mcp(daemon, monkeypatch, port, tok)
    try:
        env = await _start_envelope(w, key="loop-result-copy")
        body = {key: env[key] for key in ("action", "target", "params", "preconditions")}
        status, created = await _submit(port, tok, body, env["idempotency_key"])
        assert status == 202 and created["created"] is True, created
        operation_id = created["operation"]["operation_id"]
        assert ops.get(operation_id)["status"] == "accepted"
        attempt_id = ops.db.execute(
            "SELECT attempt_id FROM work_attempts WHERE start_operation_id=?",
            (operation_id,)).fetchone()["attempt_id"]
        loop = await _loops(server, port, tok, operation_id)
        assert loop["attempt_id"] == attempt_id and loop["work_item_id"] == work_id
        assert loop["links"]["start_operation_hash"] == f"#/op/{operation_id}"
        assert loop["links"]["work_item_hash"] == f"#/pm/{wid}/board?select={work_id}"
        assert loop["result_read"]["result"]["name"] == "pm_work_attempt_result_get"
        assert loop["result_read"]["result"]["http"].endswith("/result")
        assert isinstance(loop["state"].get("attempt_stage"), str)
        assert loop["completeness"] == {"status": "complete", "reason": None}
        status, envelope = await http(
            port, "GET", f"/api/v1/pm/workspaces/{wid}/work-attempts/{attempt_id}/result", tok=tok)
        assert status == 200, envelope
        assert loop["next_allowed_actions"] == _endpoint_rows(envelope["next_allowed_actions"])
        assert loop["blocked_actions"] == _steering_blocks(envelope["blocked_actions"])
        assert any(row.get("endpoint") == "cancel" for row in loop["next_allowed_actions"])
    finally:
        await fleet.close()


async def test_disagreeing_child_stores_have_no_loop(loop_start, monkeypatch):
    """HTTP and MCP stay 200 when the child stores disagree, and neither adds loop."""
    w = loop_start
    daemon, ops, port = w["d"], w["d"].ops, w["port"]
    tok = token(daemon, STARTER_OWNER.actor, *sorted(STARTER_OWNER.scopes))
    server, fleet = _open_mcp(daemon, monkeypatch, port, tok)
    try:
        env = await _start_envelope(w, key="loop-disagree-parent")
        body = {key: env[key] for key in ("action", "target", "params", "preconditions")}
        status, parent = await _submit(port, tok, body, env["idempotency_key"])
        assert status == 202 and parent["created"] is True, parent
        package = package_request(w)
        package_body = {key: package[key] for key in ("action", "target", "params", "preconditions")}
        status, created = await _submit(port, tok, package_body, package["idempotency_key"])
        assert status == 202 and "loop" in created["operation"], created
        package_id = created["operation"]["operation_id"]
        assert (await _loops(server, port, tok, package_id))["work_item_id"] == w["target"]["work_item_id"]
        ops.db.execute(
            "INSERT INTO pm_work_start_children VALUES(?,?,?,?,?,?,?)",
            (parent["operation"]["operation_id"], "package", 1, package_id, "{}", "digest", time.time()))
        status, after = await http(port, "GET", "/api/v1/operations/" + package_id, tok=tok)
        assert status == 200 and "loop" not in after["operation"], after
        assert "PM_WORK_START_CHILD_CHANGED" not in json.dumps(after)
        via_mcp = unpack(await server.call_tool("operation_get", {"operation_id": package_id}))
        assert "loop" not in via_mcp["operation"]
        assert "PM_WORK_START_CHILD_CHANGED" not in json.dumps(via_mcp)
    finally:
        await fleet.close()


def _loop_of(operation):
    assert "loop" in operation
    assert operation["loop"]["operation_id"] == operation["operation_id"]
    return operation["loop"]


async def test_single_operation_exits_attach_loop_and_lists_do_not(served):
    daemon, port = served
    auth = token(daemon, "loop-exit", "observe", "manage")
    outsider = token(daemon, "loop-outsider", "observe", "manage")

    async def post(body, key, *, wait=None):
        path = "/api/v1/operations" if wait is None else f"/api/v1/operations?wait={wait}"
        return await http(port, "POST", path, tok=auth, body=body, headers={"Idempotency-Key": key})

    async def rpc(method, params):
        return await http(port, "POST", "/rpc", tok=auth, body={"method": method, "params": params})

    status, workspace = await post(
        {"action": "pm.workspace.create", "params": {"name": "A useful product"}}, "loop-workspace")
    assert status == 202 and workspace["created"] is True
    assert "loop" not in workspace["operation"]
    await settle_operations(daemon.ops)
    workspace_id = workspace["operation"]["operation_id"]
    stored = daemon.ops.get(workspace_id)
    assert "loop" not in stored and stored["status"] == "succeeded"
    wid = stored["result"]["workspace"]["workspace_id"]
    version = stored["result"]["workspace"]["version"]
    status, got = await http(port, "GET", "/api/v1/operations/" + workspace_id, tok=auth)
    assert status == 200
    assert set(got) == {"operation", "work_items", "cleanup_receipts"}
    assert "loop" not in got["operation"]

    def thread_body(title):
        return {"action": "pm.thread.create", "target": {"workspace_id": wid}, "params": {"title": title},
                "preconditions": {"expected_version": version}}

    status, created = await post(thread_body("Cancel on HTTP"), "loop-http-cancel")
    assert status == 202 and created["created"] is True
    http_id = created["operation"]["operation_id"]
    _loop_of(created["operation"])
    assert "loop" not in daemon.ops.get(http_id)
    status, cancelled = await http(port, "POST", f"/api/v1/operations/{http_id}/cancel", tok=auth)
    assert status == 200 and cancelled["operation"]["status"] == "cancelled"
    _loop_of(cancelled["operation"])
    before = daemon.ops.db.total_changes
    status, again = await http(port, "GET", "/api/v1/operations/" + http_id, tok=auth)
    assert status == 200 and daemon.ops.db.total_changes == before
    assert set(again) == {"operation", "work_items", "cleanup_receipts"}
    assert _loop_of(again["operation"])["state"]["operation_status"] == "cancelled"
    status, via_rpc = await rpc("op_get", {"operation_id": http_id})
    assert status == 200
    assert _loop_of(via_rpc["result"]["operation"]) == again["operation"]["loop"]
    assert "loop" not in daemon.ops.get(http_id)

    status, submitted = await rpc("op_submit", {**thread_body("Cancel on RPC"), "idempotency_key": "loop-rpc-cancel"})
    assert status == 200 and submitted["result"]["created"] is True
    rpc_id = submitted["result"]["operation"]["operation_id"]
    _loop_of(submitted["result"]["operation"])
    status, rpc_cancelled = await rpc("op_cancel", {"operation_id": rpc_id})
    assert status == 200 and rpc_cancelled["result"]["operation"]["status"] == "cancelled"
    _loop_of(rpc_cancelled["result"]["operation"])

    replay = thread_body("Replay after wait")
    status, first = await post(replay, "loop-replay")
    assert status == 202 and _loop_of(first["operation"])
    await settle_operations(daemon.ops)
    assert daemon.ops.get(first["operation"]["operation_id"])["status"] == "succeeded"
    status, waited = await post(replay, "loop-replay", wait=1)
    assert status == 200 and waited["created"] is False
    assert waited["operation"]["status"] == "succeeded"
    _loop_of(waited["operation"])
    assert "loop" not in daemon.ops.get(waited["operation"]["operation_id"])
    status, rpc_waited = await rpc("op_submit", {**replay, "idempotency_key": "loop-replay", "wait_s": 1})
    assert status == 200 and rpc_waited["result"]["created"] is False
    assert _loop_of(rpc_waited["result"]["operation"]) == waited["operation"]["loop"]

    status, attention = await post(thread_body("Resume on HTTP"), "loop-http-resume")
    attention_id = attention["operation"]["operation_id"]
    daemon.ops.db.execute("UPDATE operations SET status='needs_attention' WHERE operation_id=?", (attention_id,))
    status, resumed = await http(port, "POST", f"/api/v1/operations/{attention_id}/resume", tok=auth)
    assert status == 200 and resumed["operation"]["status"] == "running"
    assert _loop_of(resumed["operation"])["state"]["operation_status"] == "running"
    assert "loop" not in daemon.ops.get(attention_id)

    status, rpc_attention = await rpc(
        "op_submit", {**thread_body("Resume on RPC"), "idempotency_key": "loop-rpc-resume"})
    rpc_attention_id = rpc_attention["result"]["operation"]["operation_id"]
    daemon.ops.db.execute(
        "UPDATE operations SET status='needs_attention' WHERE operation_id=?", (rpc_attention_id,))
    status, rpc_resumed = await rpc("op_resume", {"operation_id": rpc_attention_id})
    assert status == 200 and rpc_resumed["result"]["operation"]["status"] == "running"
    _loop_of(rpc_resumed["result"]["operation"])

    status, listed = await http(port, "GET", "/api/v1/operations", tok=auth)
    assert status == 200 and listed["operations"]
    assert all("loop" not in item for item in listed["operations"])
    status, rpc_listed = await rpc("op_list", {})
    assert status == 200 and rpc_listed["result"]["operations"]
    assert all("loop" not in item for item in rpc_listed["result"]["operations"])

    status, hidden = await http(port, "GET", "/api/v1/operations/" + http_id, tok=outsider)
    assert status == 404 and hidden["error"]["code"] == "NOT_FOUND" and "operation" not in hidden
    status, rpc_hidden = await http(port, "POST", "/rpc", tok=outsider,
                                    body={"method": "op_get", "params": {"operation_id": http_id}})
    assert status == 400 and rpc_hidden["error"] == "NOT_FOUND" and "operation" not in rpc_hidden
    missing = "op_" + "ab" * 16
    status, absent = await http(port, "GET", "/api/v1/operations/" + missing, tok=auth)
    assert status == 404 and absent["error"]["code"] == "NOT_FOUND" and "operation" not in absent

    status, refused = await post(
        {"action": "pm.thread.create", "target": {}, "params": {"title": "No workspace"},
         "preconditions": {"expected_version": version}}, "loop-refused")
    assert "operation" not in refused and refused["error"]["admission_refused"] is True
    status, rpc_refused = await rpc("op_submit", {
        "action": "pm.thread.create", "target": {}, "params": {"title": "No workspace"},
        "preconditions": {"expected_version": version}, "idempotency_key": "loop-rpc-refused"})
    assert status == 400 and "operation" not in rpc_refused and isinstance(rpc_refused["error"], str)


_MEMBERSHIP = "Workspace membership does not permit this action"


def _hold(ops, operation_id):
    ops.db.execute(
        "UPDATE operations SET status='needs_attention' WHERE operation_id=?", (operation_id,))
    return operation_id


def _controller(verb):
    return f"{verb} requires the original resource controller"


def _actor_scope(verb, action):
    return f"{verb} needs the operation's own actor or its {action} scope"


async def _expect(port, tok, operation_id, verb, reason, message):
    """The case names the §3.2 outcome. The endpoint's own refusal is the other half."""
    offered, blocked, status, result = await _http_verb(port, tok, operation_id, verb)
    if reason is None:
        assert blocked == [] and offered and status < 400, (verb, offered, blocked, status, result)
        return
    assert offered == [] and status >= 400, (verb, offered, status, result)
    assert result["error"]["message"] == message, result
    if reason == "omit":
        assert blocked == [], (blocked, result)
    else:
        assert blocked == [{"action": verb, "reason": reason}], (blocked, result)


async def _workspace(ops, principal):
    created, _ = ops.create(principal, action="pm.workspace.create", params={"name": "A useful product"},
                            idempotency_key=uuid.uuid4().hex)
    await settle_operations(ops)
    return ops.get(created["operation_id"])["result"]["workspace"]


async def _add_member(ops, principal, workspace_id, actor, role):
    current = pm_workspace.get_workspace(ops.db, principal, workspace_id)
    created, _ = ops.create(
        principal, action="pm.workspace.member.set", target={"workspace_id": workspace_id},
        params={"actor": actor, "role": role}, preconditions={"expected_version": current["version"]},
        idempotency_key=uuid.uuid4().hex)
    await settle_operations(ops)
    done = ops.get(created["operation_id"])
    assert done["status"] == "succeeded", done


async def _check_verbs(port, ops, tokens, make, rows):
    for principal, cancel_reason, resume_reason, cancel_message, resume_message in rows:
        await _expect(port, tokens[principal], make(), "cancel", cancel_reason, cancel_message)
        await _expect(
            port, tokens[principal], _hold(ops, make()), "resume", resume_reason, resume_message)


async def test_session_send_work_target_stays_partial_on_the_public_read(served, mock, monkeypatch):
    """HTTP and MCP keep session.send partial after a Work id is stored on the target."""
    daemon, port = served
    actor = "loop-send-partial"
    tok = token(daemon, actor, "observe", "manage", "start", "operate")
    server, fleet = _open_mcp(daemon, monkeypatch, port, tok)
    try:
        mock.handlers["git:log"] = lambda _payload: [{"hash": "a" * 40}]
        status, started = await _submit(
            port, tok, {"action": "session.start", "target": {"host": "h1", "workspace": "ws-1"}},
            "loop-send-partial-start")
        assert status == 202, started
        await settle_operations(daemon.ops)
        started = daemon.ops.get(started["operation"]["operation_id"])
        assert started["status"] == "succeeded", started
        session_id = started["result"]["session_id"]
        await daemon.inventory.refresh_host("h1")
        status, created = await _submit(
            port, tok,
            {"action": "session.send", "target": {"host": "h1", "session_id": session_id},
             "params": {"text": "Discuss the current goal"}},
            "loop-send-partial")
        assert status == 202 and created["created"] is True, created
        operation_id = created["operation"]["operation_id"]
        partial = {"status": "partial", "reason": "session_target"}
        assert created["operation"]["loop"]["completeness"] == partial
        assert created["operation"]["loop"]["work_item_id"] is None

        stored = json.loads(daemon.ops.db.execute(
            "SELECT target FROM operations WHERE operation_id=?", (operation_id,)).fetchone()[0])
        assert set(stored) == {"host", "session_id"}
        formed = "wi_" + "cd" * 10
        for stuffed in ("not-a-work", formed, {"bad": True}, 1, ""):
            daemon.ops.db.execute(
                "UPDATE operations SET target=? WHERE operation_id=?",
                (json.dumps({**stored, "work_item_id": stuffed}), operation_id))
            loop = await _loops(server, port, tok, operation_id)
            assert loop["completeness"] == partial, stuffed
            assert loop["work_item_id"] is None
            assert loop["completeness"]["status"] != "complete"
    finally:
        await fleet.close()


async def test_discussion_session_and_delivery_cancel_and_resume_match_http(served, mock):
    daemon, port = served
    ops = daemon.ops
    actor = Principal("loop-actor", frozenset({"observe", "manage", "start", "operate", "merge", "deploy"}))
    writer = Principal("loop-writer", frozenset({"observe", "manage", "operate", "merge", "deploy"}))
    reader = Principal("loop-reader", frozenset({"observe", "manage", "operate"}))
    narrow = Principal(actor.actor, frozenset({"observe"}))
    other = Principal("loop-other", writer.scopes)
    workspace = await _workspace(ops, actor)
    wid = workspace["workspace_id"]
    await _add_member(ops, actor, wid, writer.actor, "contributor")
    await _add_member(ops, actor, wid, reader.actor, "reader")
    await _add_member(ops, actor, wid, other.actor, "contributor")
    version = pm_workspace.get_workspace(ops.db, actor, wid)["version"]
    thread, _ = ops.create(
        actor, action="pm.thread.create", target={"workspace_id": wid}, params={"title": "Paid membership"},
        preconditions={"expected_version": version}, idempotency_key=uuid.uuid4().hex)
    await settle_operations(ops)
    thread = ops.get(thread["operation_id"])
    assert thread["status"] == "succeeded", thread
    thread_id = thread["result"]["thread_id"]
    message_version = pm_discussions.get_thread(ops, actor, wid, thread_id)["thread"]["version"]
    mock.handlers["git:log"] = lambda _payload: [{"hash": "a" * 40}]
    started, _ = ops.create(actor, action="session.start", target={"host": "h1", "workspace": "ws-1"},
                            idempotency_key=uuid.uuid4().hex)
    await settle_operations(ops)
    started = ops.get(started["operation_id"])
    assert started["status"] == "succeeded", started
    session_id = started["result"]["session_id"]
    await daemon.inventory.refresh_host("h1")
    tokens = {
        actor: token(daemon, actor.actor, "observe", "manage", "start", "operate", "merge", "deploy"),
        writer: token(daemon, writer.actor, *sorted(writer.scopes)),
        reader: token(daemon, reader.actor, *sorted(reader.scopes)),
        narrow: token(daemon, narrow.actor, "observe"),
        other: token(daemon, other.actor, *sorted(other.scopes)),
    }

    def message():
        created, _ = ops.create(
            actor, action="pm.message.append", target={"workspace_id": wid, "thread_id": thread_id},
            params={"content": "A paid tier may be useful"}, preconditions={"expected_version": message_version},
            idempotency_key=uuid.uuid4().hex)
        return created["operation_id"]

    def send():
        created, _ = ops.create(
            actor, action="session.send", target={"host": "h1", "session_id": session_id},
            params={"text": "Discuss the current goal"}, idempotency_key=uuid.uuid4().hex)
        return created["operation_id"]

    def delivery():
        operation_id = "op_" + uuid.uuid4().hex
        now = time.time()
        ops.db.execute(
            """INSERT INTO operations(operation_id,actor,entry,idem_key,request_hash,action,target,params,
               preconditions,status,external_refs,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (operation_id, actor.actor, "test", operation_id, "hash", "delivery.merge_and_deploy",
             json.dumps({"repository": "o/r", "recipe": "prod"}), "{}", "{}", "accepted", None, now, now))
        return operation_id

    manage = "PM operation controls require manage scope"
    message_rows = [
        (actor, None, None, None, None),
        (writer, None, None, None, None),
        (reader, "not_writer_member", "not_writer_member", _MEMBERSHIP, _MEMBERSHIP),
        (narrow, "missing_scope", "missing_scope", manage, manage),
        (other, None, None, None, None),
    ]
    session_rows = [
        (actor, None, None, None, None),
        (writer, "not_controller", "not_controller", _controller("cancel"), _controller("resume")),
        (reader, "not_controller", "not_controller", _controller("cancel"), _controller("resume")),
        (narrow, None, None, None, None),
        (other, "not_controller", "not_controller", _controller("cancel"), _controller("resume")),
    ]
    action = "delivery.merge_and_deploy"
    delivery_rows = [
        (actor, None, None, None, None),
        (writer, None, None, None, None),
        (reader, "omit", "omit", _actor_scope("cancel", action), _actor_scope("resume", action)),
        (narrow, None, "missing_scope", None,
         "resuming merge-and-deploy needs both merge and deploy scopes"),
        (other, None, None, None, None),
    ]
    await _check_verbs(port, ops, tokens, message, message_rows)
    await _check_verbs(port, ops, tokens, send, session_rows)
    await _check_verbs(port, ops, tokens, delivery, delivery_rows)


async def test_package_cancel_and_resume_match_http(ready):
    w = ready
    daemon, ops, port = w["d"], w["d"].ops, w["port"]
    await add_member(w, "pkg-writer", "contributor")
    await add_member(w, "pkg-reader", "reader")
    await add_member(w, "pkg-other", "contributor")
    writer = Principal("pkg-writer", frozenset({"observe", "manage"}))
    reader = Principal("pkg-reader", frozenset({"observe", "manage"}))
    narrow = Principal(PACKAGE_OWNER.actor, frozenset({"observe"}))
    other = Principal("pkg-other", frozenset({"observe", "manage"}))
    tokens = {
        PACKAGE_OWNER: token(daemon, PACKAGE_OWNER.actor, *sorted(PACKAGE_OWNER.scopes)),
        writer: token(daemon, writer.actor, "observe", "manage"),
        reader: token(daemon, reader.actor, "observe", "manage"),
        narrow: token(daemon, narrow.actor, "observe"),
        other: token(daemon, other.actor, "observe", "manage"),
    }

    def package():
        created, fresh = ops.create(PACKAGE_OWNER, **package_request(w))
        assert fresh is True and ops.get(created["operation_id"])["status"] == "accepted"
        return created["operation_id"]

    scope = "Package reads need observe; creation also needs manage"
    rows = [
        (PACKAGE_OWNER, None, None, None, None),
        (writer, None, None, None, None),
        (reader, "not_writer_member", "not_writer_member", _MEMBERSHIP, _MEMBERSHIP),
        (narrow, "missing_scope", "missing_scope", scope, scope),
        (other, None, None, None, None),
    ]
    await _check_verbs(port, ops, tokens, package, rows)


def unpack(result):
    if isinstance(result, tuple):
        content, structured = result
        return structured if structured is not None else json.loads(content[0].text)
    if getattr(result, "structuredContent", None) is not None:
        return result.structuredContent
    content = result.content if hasattr(result, "content") else result
    return json.loads(content[0].text)


def _operation_count(db):
    return db.execute("SELECT COUNT(*) FROM operations").fetchone()[0]


def _attempt_count(db):
    return db.execute("SELECT COUNT(*) FROM work_attempts").fetchone()[0]


def _attempts_for(db, operation_id):
    return db.execute(
        "SELECT COUNT(*) FROM work_attempts WHERE start_operation_id=?", (operation_id,)).fetchone()[0]


def _has_key(value, key):
    if isinstance(value, dict):
        return key in value or any(_has_key(item, key) for item in value.values())
    if isinstance(value, list):
        return any(_has_key(item, key) for item in value)
    return False


def _scrub_loop(operation):
    """One placeholder for the operation id, then the two facts that are not that id.

    ``state.updated_at`` is this row's own timestamp. A ``work.start`` attempt id is
    ``att_`` plus the operation id's hex, so replacing the operation id leaves it.
    """
    loop = operation["loop"]
    assert loop["operation_id"] == operation["operation_id"]
    assert loop["state"]["updated_at"] == operation["updated_at"]
    scrubbed = json.loads(json.dumps(loop).replace(operation["operation_id"], "op_PLACEHOLDER"))
    assert scrubbed["operation_id"] == "op_PLACEHOLDER"
    scrubbed["state"].pop("updated_at")
    scrubbed.pop("attempt_id")
    return scrubbed


async def _submit(port, tok, body, key):
    return await http(port, "POST", "/api/v1/operations", tok=tok, body=body, headers={"Idempotency-Key": key})


async def _mcp_submit(server, body, key):
    return unpack(await server.call_tool(
        "operation_submit", {**body, "idempotency_key": key, "wait_s": 0, "confirm": True}))


async def _same_operation(server, port, tok, operation_id):
    """HTTP and MCP reads of one operation, then its cancel and its resume refusal."""
    status, body = await http(port, "GET", "/api/v1/operations/" + operation_id, tok=tok)
    assert status == 200, body
    via_mcp = unpack(await server.call_tool("operation_get", {"operation_id": operation_id}))
    assert body["operation"]["loop"] == via_mcp["operation"]["loop"]
    status, cancelled = await http(port, "POST", f"/api/v1/operations/{operation_id}/cancel", tok=tok)
    assert status == 200, cancelled
    via_cancel = unpack(await server.call_tool(
        "operation_cancel", {"operation_id": operation_id, "confirm": True}))
    assert cancelled["operation"]["loop"] == via_cancel["operation"]["loop"]
    status, resumed = await http(port, "POST", f"/api/v1/operations/{operation_id}/resume", tok=tok)
    assert status == 409 and resumed["error"]["code"] == "NOT_RESUMABLE" and "operation" not in resumed
    with pytest.raises(ToolError, match="NOT_RESUMABLE"):
        await server.call_tool("operation_resume", {"operation_id": operation_id, "confirm": True})
    return body["operation"]["loop"]


def _hide_source(db, kind, identity):
    db.execute(
        "INSERT INTO pm_source_lineage VALUES(?, ?, 'op_missing_loop', 'op_missing_loop')",
        (kind, identity),
    )


async def _refuse(server, port, tok, body, key, code, status_code, *, admission_refused, db):
    before = _operation_count(db)
    status, refused = await _submit(port, tok, body, key)
    assert status == status_code, refused
    assert refused["error"]["code"] == code and "operation" not in refused
    if admission_refused:
        assert refused["error"]["admission_refused"] is True
    else:
        assert "admission_refused" not in refused["error"]
    with pytest.raises(ToolError, match=code):
        await server.call_tool("operation_submit", {
            **body, "idempotency_key": key + "-mcp", "wait_s": 0, "confirm": True})
    assert _operation_count(db) == before


@pytest.fixture
async def loop_start(ready):
    from tests.test_pm_package_handoff import dispatch_runner

    dispatch_runner(ready)
    pm_work_start.install(ready["d"].ops)
    return ready


async def test_http_and_mcp_share_one_loop_and_the_same_refusals(served, mock, monkeypatch):
    daemon, port = served
    actor = "loop-parity"
    tok = token(daemon, actor, "observe", "manage", "start", "operate")
    monkeypatch.setenv("BATC_TASK_URL", f"http://127.0.0.1:{port}/rpc")
    monkeypatch.setenv("BATC_API_TOKEN", tok)
    server, fleet = build_server(daemon.fleet.config, principal_only=True)
    try:
        status, http_cap = await http(port, "GET", "/api/v1/capabilities", tok=tok)
        assert status == 200, http_cap
        assert http_cap["features"]["pm_workspaces"]["operation_loop"] == {"version": 1}
        mcp_cap = unpack(await server.call_tool("capabilities_get", {}))
        assert mcp_cap["features"]["pm_workspaces"]["operation_loop"] == {"version": 1}

        mock.handlers["git:log"] = lambda _payload: [{"hash": "a" * 40}]
        status, started = await _submit(
            port, tok, {"action": "session.start", "target": {"host": "h1", "workspace": "ws-1"}}, "loop-session")
        assert status == 202, started
        await settle_operations(daemon.ops)
        status, started_read = await http(
            port, "GET", "/api/v1/operations/" + started["operation"]["operation_id"], tok=tok)
        assert status == 200 and started_read["operation"]["status"] == "succeeded", started_read
        sid = started_read["operation"]["result"]["session_id"]
        await daemon.inventory.refresh_host("h1")

        http_body = {"action": "session.send", "target": {"host": "h1", "session_id": sid},
                     "params": {"text": "Discuss the current goal"}}
        mcp_body = {"action": "session.send", "target": {"host": "h1", "session_id": sid},
                    "params": {"text": "Discuss the current goal over MCP"}}
        status, http_created = await _submit(port, tok, http_body, "loop-http-send")
        assert status == 202 and http_created["created"] is True, http_created
        mcp_created = await _mcp_submit(server, mcp_body, "loop-mcp-send")
        assert mcp_created["created"] is True
        http_id = http_created["operation"]["operation_id"]
        mcp_id = mcp_created["operation"]["operation_id"]
        assert http_id != mcp_id
        assert http_created["operation"]["loop"]["attempt_id"] is None
        assert _scrub_loop(http_created["operation"]) == _scrub_loop(mcp_created["operation"])
        created_rows = _operation_count(daemon.ops.db)
        await settle_operations(daemon.ops)
        assert daemon.ops.get(http_id)["status"] == "succeeded"
        assert daemon.ops.get(mcp_id)["status"] == "succeeded"

        status, replayed = await _submit(port, tok, http_body, "loop-http-send")
        assert status == 200 and replayed["created"] is False
        assert replayed["operation"]["operation_id"] == http_id
        assert replayed["operation"]["loop"]["operation_id"] == http_id
        mcp_replay = await _mcp_submit(server, mcp_body, "loop-mcp-send")
        assert mcp_replay["created"] is False
        assert mcp_replay["operation"]["operation_id"] == mcp_id
        assert mcp_replay["operation"]["loop"]["operation_id"] == mcp_id
        assert _operation_count(daemon.ops.db) == created_rows

        kept = await _same_operation(server, port, tok, http_id)
        await _same_operation(server, port, tok, mcp_id)
        assert kept["dashboard_hash"] == f"#/session/h1/{sid}"

        # A successful resume leaves needs_attention, so each transport resumes its own row.
        http_attention = {"action": "session.send", "target": {"host": "h1", "session_id": sid},
                          "params": {"text": "Resume this send on HTTP"}}
        mcp_attention = {"action": "session.send", "target": {"host": "h1", "session_id": sid},
                         "params": {"text": "Resume this send on MCP"}}
        status, http_hold = await _submit(port, tok, http_attention, "loop-http-hold")
        assert status == 202, http_hold
        mcp_hold = await _mcp_submit(server, mcp_attention, "loop-mcp-hold")
        http_hold_id = http_hold["operation"]["operation_id"]
        mcp_hold_id = mcp_hold["operation"]["operation_id"]
        daemon.ops.db.execute(
            "UPDATE operations SET status='needs_attention' WHERE operation_id IN (?, ?)",
            (http_hold_id, mcp_hold_id))
        status, http_held = await http(port, "GET", "/api/v1/operations/" + http_hold_id, tok=tok)
        assert status == 200, http_held
        mcp_held = unpack(await server.call_tool("operation_get", {"operation_id": http_hold_id}))
        assert http_held["operation"]["loop"] == mcp_held["operation"]["loop"]
        assert any(row.get("endpoint") == "resume" for row in http_held["operation"]["loop"]["next_allowed_actions"])
        status, http_resumed = await http(port, "POST", f"/api/v1/operations/{http_hold_id}/resume", tok=tok)
        assert status == 200 and http_resumed["operation"]["status"] == "running", http_resumed
        mcp_resumed = unpack(await server.call_tool(
            "operation_resume", {"operation_id": mcp_hold_id, "confirm": True}))
        assert mcp_resumed["operation"]["status"] == "running"
        assert _scrub_loop(http_resumed["operation"]) == _scrub_loop(mcp_resumed["operation"])

        _hide_source(daemon.ops.db, "session", f"h1/{sid}")
        status, hidden = await http(port, "GET", "/api/v1/operations/" + http_id, tok=tok)
        assert status == 404, hidden
        assert hidden["error"] == {
            "code": "NOT_FOUND", "message": "Original resource sources are unavailable"}
        assert "loop" not in hidden and "operation" not in hidden
        assert http_id not in json.dumps(hidden) and sid not in json.dumps(hidden)
        with pytest.raises(ToolError, match="NOT_FOUND") as refused:
            await server.call_tool("operation_get", {"operation_id": http_id})
        mcp_text = str(refused.value)
        assert "Original resource sources are unavailable" in mcp_text
        assert "loop" not in mcp_text and http_id not in mcp_text and sid not in mcp_text
        assert sid in json.dumps(kept)

        status, workspace = await _submit(
            port, tok, {"action": "pm.workspace.create", "params": {"name": "A useful product"}}, "loop-parity-ws")
        assert status == 202, workspace
        await settle_operations(daemon.ops)
        created = daemon.ops.get(workspace["operation"]["operation_id"])
        assert created["status"] == "succeeded", created
        wid = created["result"]["workspace"]["workspace_id"]
        version = created["result"]["workspace"]["version"]
        thread = {"action": "pm.thread.create", "target": {"workspace_id": wid}, "params": {"title": "Hidden"},
                  "preconditions": {"expected_version": version}}
        narrow = token(daemon, actor, "observe")
        monkeypatch.setenv("BATC_API_TOKEN", narrow)
        await _refuse(server, port, narrow, thread, "loop-no-manage", "FORBIDDEN", 403,
                      admission_refused=True, db=daemon.ops.db)
        outsider = token(daemon, "loop-parity-outsider", "observe", "manage")
        monkeypatch.setenv("BATC_API_TOKEN", outsider)
        await _refuse(server, port, outsider, thread, "loop-non-member", "NOT_FOUND", 404,
                      admission_refused=True, db=daemon.ops.db)
        manual = {"action": "session.send", "target": {"host": "h1", "session_id": MANUAL},
                  "params": {"text": "Leave the manual session alone"}}
        monkeypatch.setenv("BATC_API_TOKEN", tok)
        await _refuse(server, port, tok, manual, "loop-manual", "MANUAL_READ_ONLY", 403,
                      admission_refused=False, db=daemon.ops.db)
        before = _operation_count(daemon.ops.db)
        changed = {**http_body, "params": {"text": "Different words for the same key"}}
        status, conflict = await _submit(port, tok, changed, "loop-http-send")
        assert status == 409 and conflict["error"]["code"] == "IDEMPOTENCY_CONFLICT"
        assert "operation" not in conflict and "admission_refused" not in conflict["error"]
        with pytest.raises(ToolError, match="IDEMPOTENCY_CONFLICT"):
            await _mcp_submit(server, {**mcp_body, "params": {"text": "Different MCP words"}}, "loop-mcp-send")
        assert _operation_count(daemon.ops.db) == before
    finally:
        await fleet.close()


async def test_work_start_loop_matches_across_http_and_mcp(loop_start, monkeypatch):
    w = loop_start
    daemon, ops, port = w["d"], w["d"].ops, w["port"]
    wid, work_id = w["workspace_id"], w["target"]["work_item_id"]
    tok = token(daemon, STARTER_OWNER.actor, *sorted(STARTER_OWNER.scopes))
    monkeypatch.setenv("BATC_TASK_URL", f"http://127.0.0.1:{port}/rpc")
    monkeypatch.setenv("BATC_API_TOKEN", tok)
    server, fleet = build_server(daemon.fleet.config, principal_only=True)
    try:
        http_env = await _start_envelope(w, key="loop-http-start")
        http_body = {key: http_env[key] for key in ("action", "target", "params", "preconditions")}
        status, http_created = await _submit(port, tok, http_body, http_env["idempotency_key"])
        assert status == 202 and http_created["created"] is True, http_created
        http_id = http_created["operation"]["operation_id"]
        http_attempt = ops.db.execute(
            "SELECT attempt_id FROM work_attempts WHERE start_operation_id=?", (http_id,)).fetchone()["attempt_id"]
        assert http_created["operation"]["loop"]["attempt_id"] == http_attempt
        status, http_cancelled = await http(port, "POST", f"/api/v1/operations/{http_id}/cancel", tok=tok)
        assert status == 200 and http_cancelled["operation"]["status"] == "cancelled", http_cancelled

        mcp_env = await _start_envelope(w, key="loop-mcp-start")
        mcp_created = await _mcp_submit(server, {
            key: mcp_env[key] for key in ("action", "target", "params", "preconditions")}, mcp_env["idempotency_key"])
        assert mcp_created["created"] is True
        mcp_id = mcp_created["operation"]["operation_id"]
        mcp_attempt = ops.db.execute(
            "SELECT attempt_id FROM work_attempts WHERE start_operation_id=?", (mcp_id,)).fetchone()["attempt_id"]
        assert mcp_created["operation"]["loop"]["attempt_id"] == mcp_attempt
        assert http_id != mcp_id
        assert _scrub_loop(http_created["operation"]) == _scrub_loop(mcp_created["operation"])
        mcp_cancelled = unpack(await server.call_tool(
            "operation_cancel", {"operation_id": mcp_id, "confirm": True}))
        assert mcp_cancelled["operation"]["status"] == "cancelled"
        await settle_operations(ops)
        assert ops.get(http_id)["status"] == "cancelled" and ops.get(mcp_id)["status"] == "cancelled"

        kept = await _same_operation(server, port, tok, http_id)
        await _same_operation(server, port, tok, mcp_id)
        assert kept["work_item_id"] == work_id and work_id in kept["dashboard_hash"]
        rows_after_start = _operation_count(ops.db)
        status, replayed = await _submit(port, tok, http_body, http_env["idempotency_key"])
        assert status == 200 and replayed["created"] is False
        assert replayed["operation"]["loop"]["operation_id"] == http_id
        mcp_replay = await _mcp_submit(server, {
            key: mcp_env[key] for key in ("action", "target", "params", "preconditions")}, mcp_env["idempotency_key"])
        assert mcp_replay["created"] is False and mcp_replay["operation"]["loop"]["operation_id"] == mcp_id
        assert _operation_count(ops.db) == rows_after_start
        assert _attempts_for(ops.db, http_id) == 1 and _attempts_for(ops.db, mcp_id) == 1

        for path in (
            f"/api/v1/pm/workspaces/{wid}/work/{work_id}/start-preview",
            f"/api/v1/pm/workspaces/{wid}/work-attempts/{http_attempt}",
            f"/api/v1/pm/workspaces/{wid}/work-attempts/{http_attempt}/result",
        ):
            method = "POST" if path.endswith("start-preview") else "GET"
            body = {"instruction": "Previewed via HTTP."} if method == "POST" else None
            status, document = await http(port, method, path, tok=tok, body=body)
            assert status == 200, (path, document)
            assert not _has_key(document, "loop")

        live_env = await _start_envelope(w, key="loop-live-start")
        status, live = await _submit(port, tok, {
            key: live_env[key] for key in ("action", "target", "params", "preconditions")}, live_env["idempotency_key"])
        assert status == 202 and live["created"] is True, live
        assert ops.get(live["operation"]["operation_id"])["status"] == "accepted"
        before_ops, before_attempts = _operation_count(ops.db), _attempt_count(ops.db)
        for key_name in ("loop-blocked-http", "loop-blocked-mcp"):
            blocked = await _start_envelope(w, key=key_name)
            body = {key: blocked[key] for key in ("action", "target", "params", "preconditions")}
            if key_name.endswith("http"):
                status, refused = await _submit(port, tok, body, blocked["idempotency_key"])
                assert status == 409 and refused["error"]["code"] == "PM_WORK_START_IN_PROGRESS", refused
                assert refused["error"]["admission_refused"] is True and "operation" not in refused
            else:
                with pytest.raises(ToolError, match="PM_WORK_START_IN_PROGRESS"):
                    await _mcp_submit(server, body, blocked["idempotency_key"])
        assert _operation_count(ops.db) == before_ops and _attempt_count(ops.db) == before_attempts

        _hide_source(ops.db, "work_item", work_id)
        status, hidden = await http(port, "GET", "/api/v1/operations/" + http_id, tok=tok)
        assert status == 404, hidden
        assert hidden["error"] == {
            "code": "NOT_FOUND", "message": "Original resource sources are unavailable"}
        assert "loop" not in hidden and "operation" not in hidden
        assert http_id not in json.dumps(hidden) and work_id not in json.dumps(hidden)
        with pytest.raises(ToolError, match="NOT_FOUND") as refused:
            await server.call_tool("operation_get", {"operation_id": http_id})
        mcp_text = str(refused.value)
        assert "Original resource sources are unavailable" in mcp_text
        assert "loop" not in mcp_text and http_id not in mcp_text and work_id not in mcp_text
        assert kept["work_item_id"] == work_id
    finally:
        await fleet.close()


async def test_child_session_withdrawn_from_another_row_stays_readable(loop_start, monkeypatch):
    """A proven package child copies the managed session from the handoff, not from its own fields."""
    w = loop_start
    daemon, ops, port = w["d"], w["d"].ops, w["port"]
    tok = token(daemon, STARTER_OWNER.actor, *sorted(STARTER_OWNER.scopes))
    monkeypatch.setenv("BATC_TASK_URL", f"http://127.0.0.1:{port}/rpc")
    monkeypatch.setenv("BATC_API_TOKEN", tok)
    server, fleet = build_server(daemon.fleet.config, principal_only=True)
    try:
        env = await _start_envelope(w, key="loop-child-session")
        body = {key: env[key] for key in ("action", "target", "params", "preconditions")}
        status, created = await _submit(port, tok, body, env["idempotency_key"])
        assert status == 202 and created["created"] is True, created
        parent_id = created["operation"]["operation_id"]
        done = await _advance_start(ops, parent_id)
        assert done["status"] == "succeeded", done
        child_id = done["result"]["children"]["package"]
        host, sid = done["result"]["session"]["host"], done["result"]["session"]["session_id"]
        assert isinstance(child_id, str) and child_id != parent_id
        assert sid not in json.dumps(ops.get(child_id))

        status, before = await http(port, "GET", "/api/v1/operations/" + child_id, tok=tok)
        assert status == 200, before
        before_mcp = unpack(await server.call_tool("operation_get", {"operation_id": child_id}))
        assert before["operation"]["loop"] == before_mcp["operation"]["loop"]
        kept = before["operation"]["loop"]
        assert kept["links"]["session_hash"] == f"#/session/{host}/{sid}"
        assert sid in json.dumps(kept)

        _hide_source(ops.db, "session", f"{host}/{sid}")
        status, hidden = await http(port, "GET", "/api/v1/operations/" + child_id, tok=tok)
        assert status == 200, hidden
        hidden_mcp = unpack(await server.call_tool("operation_get", {"operation_id": child_id}))
        assert hidden["operation"]["loop"] == hidden_mcp["operation"]["loop"]
        http_text, mcp_text = json.dumps(hidden), json.dumps(hidden_mcp)
        session_hash = f"#/session/{host}/{sid}"
        assert sid not in http_text and sid not in mcp_text
        assert session_hash not in http_text and session_hash not in mcp_text
        withdrawn = {"status": "unavailable", "reason": "source_withdrawn"}
        assert hidden["operation"]["loop"]["completeness"] == withdrawn
        assert hidden["operation"]["loop"]["links"]["session_hash"] is None
        assert sid in json.dumps(kept)
        assert ops.get(child_id)["status"] == "succeeded"
    finally:
        await fleet.close()


async def test_hidden_handoff_child_omits_the_view_and_stays_readable(loop_start, monkeypatch):
    """Hiding the handoff is case 2 on the package child and case 1 on the parent."""
    w = loop_start
    daemon, ops, port = w["d"], w["d"].ops, w["port"]
    tok = token(daemon, STARTER_OWNER.actor, *sorted(STARTER_OWNER.scopes))
    server, fleet = _open_mcp(daemon, monkeypatch, port, tok)
    try:
        env = await _start_envelope(w, key="loop-hidden-handoff")
        body = {key: env[key] for key in ("action", "target", "params", "preconditions")}
        status, created = await _submit(port, tok, body, env["idempotency_key"])
        assert status == 202 and created["created"] is True, created
        parent_id = created["operation"]["operation_id"]
        done = await _advance_start(ops, parent_id)
        assert done["status"] == "succeeded", done
        child_id = done["result"]["children"]["package"]
        handoff_id = done["result"]["execution_operation_id"]
        assert isinstance(child_id, str) and isinstance(handoff_id, str)
        assert handoff_id == done["result"]["children"]["handoff"]
        assert handoff_id not in json.dumps(ops.get(child_id))

        before = await _loops(server, port, tok, child_id)
        work_id = before["work_item_id"]
        assert isinstance(work_id, str)
        assert isinstance(before["state"].get("attempt_stage"), str)
        assert before["links"]["session_hash"] and before["links"]["execution_operation_hash"]

        _hide_source(ops.db, "operation", handoff_id)
        status, hidden = await http(port, "GET", "/api/v1/operations/" + child_id, tok=tok)
        assert status == 200, hidden
        hidden_mcp = unpack(await server.call_tool("operation_get", {"operation_id": child_id}))
        assert hidden["operation"]["loop"] == hidden_mcp["operation"]["loop"]
        loop = hidden["operation"]["loop"]
        assert loop["completeness"] == {"status": "unavailable", "reason": "source_withdrawn"}
        assert "attempt_stage" not in loop["state"]
        assert loop["links"]["session_hash"] is None
        assert loop["links"]["execution_operation_hash"] is None
        assert loop["work_item_id"] == work_id
        assert loop["result_read"]["result"]["name"] == "pm_work_attempt_result_get"

        status, parent_hidden = await http(port, "GET", "/api/v1/operations/" + parent_id, tok=tok)
        assert status == 404, parent_hidden
        assert parent_hidden["error"] == {
            "code": "NOT_FOUND", "message": "Original resource sources are unavailable"}
        assert "loop" not in parent_hidden
        with pytest.raises(ToolError, match="NOT_FOUND") as refused:
            await server.call_tool("operation_get", {"operation_id": parent_id})
        assert "Original resource sources are unavailable" in str(refused.value)
    finally:
        await fleet.close()


async def test_changed_child_receipt_stays_readable_as_attempt_unreadable(loop_start, monkeypatch):
    """A work.start whose child receipt no longer matches still returns the operation."""
    w = loop_start
    daemon, ops, port = w["d"], w["d"].ops, w["port"]
    tok = token(daemon, STARTER_OWNER.actor, *sorted(STARTER_OWNER.scopes))
    monkeypatch.setenv("BATC_TASK_URL", f"http://127.0.0.1:{port}/rpc")
    monkeypatch.setenv("BATC_API_TOKEN", tok)
    server, fleet = build_server(daemon.fleet.config, principal_only=True)
    try:
        env = await _start_envelope(w, key="loop-child-changed")
        body = {key: env[key] for key in ("action", "target", "params", "preconditions")}
        status, created = await _submit(port, tok, body, env["idempotency_key"])
        assert status == 202 and created["created"] is True, created
        parent_id = created["operation"]["operation_id"]
        done = await _advance_start(ops, parent_id)
        assert done["status"] == "succeeded", done
        child_id = done["result"]["children"]["package"]
        # The child link row is immutable. _child compares this operation's params
        # with the stored request (pm_work_start.py:1124-1127).
        child = ops.get(child_id)
        changed = dict(child["params"])
        changed["title"] = str(changed.get("title") or "package") + " receipt changed"
        ops.db.execute(
            "UPDATE operations SET params=? WHERE operation_id=?",
            (json.dumps(changed, ensure_ascii=False), child_id))

        status, got = await http(port, "GET", "/api/v1/operations/" + parent_id, tok=tok)
        assert status == 200, got
        via_mcp = unpack(await server.call_tool("operation_get", {"operation_id": parent_id}))
        status, cancelled = await http(port, "POST", f"/api/v1/operations/{parent_id}/cancel", tok=tok)
        assert status == 200, cancelled
        assert got["operation"]["loop"] == via_mcp["operation"]["loop"] == cancelled["operation"]["loop"]
        loop = got["operation"]["loop"]
        assert loop["completeness"] == {"status": "partial", "reason": "attempt_unreadable"}
        assert "attempt_stage" not in loop["state"]
        assert loop["result_read"]["result"]["name"]
        assert loop["next_allowed_actions"] == [] and loop["blocked_actions"] == []
        assert loop["links"]["session_hash"] is None
        assert loop["links"]["execution_operation_hash"] is None
        assert "PM_WORK_START_CHILD_CHANGED" not in json.dumps(loop)
    finally:
        await fleet.close()


async def test_first_start_of_an_unbound_work_keeps_the_work_hash(loop_start, monkeypatch):
    """A Work with no binding yet is visible on the create, the MCP submit, and the GET."""
    from bat_agent_connector import work_items

    w = loop_start
    daemon, ops, port = w["d"], w["d"].ops, w["port"]
    wid = w["workspace_id"]
    tok = token(daemon, STARTER_OWNER.actor, *sorted(STARTER_OWNER.scopes))
    monkeypatch.setenv("BATC_TASK_URL", f"http://127.0.0.1:{port}/rpc")
    monkeypatch.setenv("BATC_API_TOKEN", tok)
    server, fleet = build_server(daemon.fleet.config, principal_only=True)
    project_id = work_items._get_item(ops.db, w["target"]["work_item_id"])["project_id"]

    async def unbound_work(title):
        work_id = (await pkg_action(
            w, "work_item.create", {"project_id": project_id},
            {"title": title, "goal": "Authenticate members",
             "acceptance": "Valid credentials pass, invalid fail"},
        ))["work_item_id"]
        assert pm_evidence._binding(ops.db, wid, work_id) is None
        return work_id

    async def hold_due():
        return None

    try:
        http_work = await unbound_work("Unbound start over HTTP")
        mcp_work = await unbound_work("Unbound start over MCP")
        # Admission records the attempt. Hold the worker so this read is before the bind child.
        monkeypatch.setattr(ops, "run_due", hold_due)
        http_env = await _start_envelope(w, work_item_id=http_work, key="loop-unbound-http")
        mcp_env = await _start_envelope(w, work_item_id=mcp_work, key="loop-unbound-mcp")
        assert http_env["preconditions"]["expected_binding_id"] is None
        assert http_env["params"]["requirement_ids"]
        http_body = {key: http_env[key] for key in ("action", "target", "params", "preconditions")}
        mcp_body = {key: mcp_env[key] for key in ("action", "target", "params", "preconditions")}
        status, created = await _submit(port, tok, http_body, http_env["idempotency_key"])
        assert status == 202 and created["created"] is True, created
        parent_id = created["operation"]["operation_id"]
        submitted = await _mcp_submit(server, mcp_body, mcp_env["idempotency_key"])
        assert submitted["created"] is True
        status, got = await http(port, "GET", "/api/v1/operations/" + parent_id, tok=tok)
        assert status == 200, got
        assert ops.get(parent_id)["status"] == "accepted"
        assert pm_evidence._binding(ops.db, wid, http_work) is None
        assert pm_evidence._binding(ops.db, wid, mcp_work) is None
        attempt_id = ops.db.execute(
            "SELECT attempt_id FROM work_attempts WHERE start_operation_id=?", (parent_id,)).fetchone()["attempt_id"]
        status, result = await http(
            port, "GET",
            f"/api/v1/pm/workspaces/{wid}/work-attempts/{attempt_id}/result?wait_ms=0", tok=tok)
        assert status == 200, result
        work_hash = result["links"]["work_item_hash"]
        mcp_id = submitted["operation"]["operation_id"]
        mcp_attempt = ops.db.execute(
            "SELECT attempt_id FROM work_attempts WHERE start_operation_id=?", (mcp_id,)).fetchone()["attempt_id"]
        status, mcp_result = await http(
            port, "GET",
            f"/api/v1/pm/workspaces/{wid}/work-attempts/{mcp_attempt}/result?wait_ms=0", tok=tok)
        assert status == 200, mcp_result
        for reply, work_id, shown in (
            (created["operation"]["loop"], http_work, work_hash),
            (got["operation"]["loop"], http_work, work_hash),
            (submitted["operation"]["loop"], mcp_work, mcp_result["links"]["work_item_hash"]),
        ):
            assert reply["work_item_id"] == work_id, reply
            assert reply["links"]["work_item_hash"] == shown == f"#/pm/{wid}/board?select={work_id}"
    finally:
        await fleet.close()


def _verb(loop, verb):
    offered = [row for row in loop["next_allowed_actions"]
               if row.get("kind") == "operation_endpoint" and row.get("endpoint") == verb]
    blocked = [row for row in loop["blocked_actions"] if row.get("action") == verb]
    return offered, blocked


async def _http_verb(port, tok, operation_id, verb):
    status, body = await http(port, "GET", "/api/v1/operations/" + operation_id, tok=tok)
    assert status == 200, body
    offered, blocked = _verb(body["operation"]["loop"], verb)
    status, result = await http(port, "POST", f"/api/v1/operations/{operation_id}/{verb}", tok=tok)
    return offered, blocked, status, result


async def test_proven_bind_child_eligibility_matches_the_endpoint(loop_start, monkeypatch):
    """A proven bind child omits a reader, names a missing scope, and lists its scopes."""
    from dataclasses import replace

    from bat_agent_connector import work_items
    from bat_agent_connector.operations import Wait

    w = loop_start
    daemon, ops, port = w["d"], w["d"].ops, w["port"]
    wid = w["workspace_id"]
    project_id = work_items._get_item(ops.db, w["target"]["work_item_id"])["project_id"]
    work_id = (await pkg_action(
        w, "work_item.create", {"project_id": project_id},
        {"title": "Bind child eligibility", "goal": "Authenticate members",
         "acceptance": "Valid credentials pass, invalid fail"},
    ))["work_item_id"]
    await _add_member(ops, STARTER_OWNER, wid, "loop-bind-reader", "reader")

    async def hold_bind(_ctx):
        raise Wait("waiting_external", "hold the bind child", delay_s=30)

    monkeypatch.setitem(ops.actions, "pm.work.bind", replace(ops.actions["pm.work.bind"], run=hold_bind))
    owner_tok = token(daemon, STARTER_OWNER.actor, *sorted(STARTER_OWNER.scopes))
    env = await _start_envelope(w, work_item_id=work_id, key="loop-bind-child")
    assert env["preconditions"]["expected_binding_id"] is None
    created, fresh = ops.create(STARTER_OWNER, **env)
    assert fresh is True
    await settle_operations(ops)
    parent = ops.get(created["operation_id"])
    assert parent["status"] == "waiting_external", (
        parent["status"], parent.get("error_code"), parent.get("status_reason"), parent.get("result"))
    child_id = parent["result"]["children"]["bind"]
    assert isinstance(child_id, str), parent["result"]
    child = ops.get(child_id)
    assert child["action"] == "pm.work.bind" and child["status"] == "waiting_external", child

    reader_tok = token(daemon, "loop-bind-reader", "observe", "manage")
    narrow_tok = token(daemon, STARTER_OWNER.actor, "observe")
    offered, blocked, status, result = await _http_verb(port, reader_tok, child_id, "cancel")
    assert offered == [] and blocked == [], (offered, blocked, result)
    assert status >= 400 and result["error"]["message"] == "Workspace membership does not permit this action"
    offered, blocked, status, result = await _http_verb(port, narrow_tok, child_id, "cancel")
    assert blocked == [{"action": "cancel", "reason": "missing_scope"}], (blocked, result)
    assert status >= 400
    assert result["error"]["message"] == "Evidence reads require observe; changes also require manage"
    offered, blocked, status, result = await _http_verb(port, owner_tok, child_id, "cancel")
    assert blocked == [] and status < 400, (blocked, status, result)
    assert offered[0]["required_scopes"] == ["observe", "manage"]


async def test_task_bound_send_scope_matches_the_endpoint(task_owned):
    """A task-bound session.send without operate is missing_scope."""
    daemon, tid = task_owned
    caller = capability_caller(daemon, tid)
    created, fresh = daemon.ops.create(
        caller, action="session.send", target={"task_id": tid},
        params={"text": "one fixed instruction", "step_id": "loop-step"},
        idempotency_key="loop-task-send")
    assert fresh is True and daemon.ops.get(created["operation_id"])["status"] == "accepted"
    server = await asyncio.start_server(daemon._handle, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    try:
        tok = token(daemon, "loop-task-reader", "observe", "manage")
        offered, blocked, status, result = await _http_verb(port, tok, created["operation_id"], "cancel")
        assert offered == []
        assert blocked == [{"action": "cancel", "reason": "missing_scope"}], (blocked, result)
        assert status >= 400
        assert result["error"]["message"] == "task operation control requires its current action scope"
    finally:
        server.close()
        await server.wait_closed()
