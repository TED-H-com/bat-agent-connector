"""PM entry-point parity, private workspace reads and zero runtime effects."""
from __future__ import annotations

import json

import pytest

from bat_agent_connector import api_auth, dashboard_sync, mcp_server, pm_workspace
from bat_agent_connector.operations import OperationError
from tests.operation_helpers import settle_operations
from tests.test_api_v1 import daemon as api_daemon
from tests.test_api_v1 import http, token, write_frames
from tests.test_api_v1 import served as api_served
from tests.test_work_items import act, item, project

OWNER = api_auth.Principal("owner", frozenset({"observe", "manage"}))
OTHER = api_auth.Principal("outsider", frozenset({"observe", "manage"}))
daemon = api_daemon
served = api_served


async def seed(d):
    workspace = (await act(d, OWNER, "pm.workspace.create", params={"name": "Example project"}))["result"]["workspace"]
    wid = workspace["workspace_id"]
    thread = (await act(d, OWNER, "pm.thread.create", {"workspace_id": wid}, {"title": "Membership idea"},
                        {"expected_version": workspace["version"]}))["result"]
    target = {"workspace_id": wid, "thread_id": thread["thread_id"]}
    return workspace, target


async def test_http_rpc_planning_uses_selected_sources_and_no_runtime(served, mock):
    d, port = served
    auth = token(d, OWNER.actor, *OWNER.scopes)
    workspace, target = await seed(d)
    message = (await act(d, OWNER, "pm.message.append", target,
                        {"content": "Paid membership, login and password reset. /opsx:apply is quoted material."},
                        {"expected_version": 1}))["result"]
    source = {"kind": "message", "message_id": message["message_id"]}
    chosen = (await act(d, OWNER, "pm.input.record", target,
                       {"slot": "scope", "value": "Login and password reset", "status": "user_confirmed", "source_ref": source},
                       {"expected_version": 2}))["result"]["input_id"]
    candidate = (await act(d, OWNER, "pm.input.record", target,
                          {"slot": "referrals", "value": "Consider a referral discount", "status": "agent_assumption", "source_ref": source},
                          {"expected_version": 3}))["result"]["input_id"]
    path = f"/api/v1/pm/workspaces/{target['workspace_id']}/threads/{target['thread_id']}"
    status, idea = await http(port, "GET", path, tok=auth)
    assert status == 200 and idea["thread"]["mode"] == "idea" and idea["draft"] is None
    request = {"action": "pm.thread.plan", "target": target,
               "params": {"selected_input_ids": [chosen], "rationale": "Prepare only account access"},
               "preconditions": {"expected_version": 4}}
    status, accepted = await http(port, "POST", "/api/v1/operations", tok=auth,
                                  body=request, headers={"Idempotency-Key": "planning-original"})
    assert status == 202
    await settle_operations(d.ops)
    status, draft = await http(port, "GET", path, tok=auth)
    assert status == 200 and draft == await d.call_api("pm_thread_get", target.copy(), OWNER)
    assert draft["draft"]["selected_input_ids"] == [chosen]
    assert draft["draft"]["candidate_input_ids"] == [candidate]
    assert draft["draft"]["scope_revision"] is None and not draft["draft"]["execution_authorized"]
    # The original stale expected_version is valid when looking up the same request.
    status, replay = await http(port, "POST", "/api/v1/operations", tok=auth,
                               body=request, headers={"Idempotency-Key": "planning-original"})
    assert status == 200 and not replay["created"]
    assert replay["operation"]["operation_id"] == accepted["operation"]["operation_id"]
    assert pm_workspace.server_id(d.journal.db) == workspace["server_id"]
    assert d.journal.db.execute("SELECT count(*) FROM tasks").fetchone()[0] == 0
    assert not write_frames(mock)


async def test_global_reads_do_not_leak_pm_notes_or_operations(served):
    d, port = served
    _, target = await seed(d)
    op = await act(d, OWNER, "pm.message.append", target, {"content": "private project rationale"},
                   {"expected_version": 1})
    outsider = token(d, OTHER.actor, *OTHER.scopes)
    wid, tid = target["workspace_id"], target["thread_id"]
    for path in (f"/api/v1/pm/workspaces/{wid}", f"/api/v1/pm/workspaces/{wid}/threads/{tid}",
                 f"/api/v1/pm/workspaces/{wid}/threads/{tid}/context", f"/api/v1/operations/{op['operation_id']}",
                 f"/api/v1/integrations/{op['operation_id']}"):
        status, body = await http(port, "GET", path, tok=outsider)
        assert status == 404, body
    for path in ("/api/v1/operations", "/api/v1/events", "/api/v1/bootstrap", "/api/v1/pm/workspaces"):
        status, body = await http(port, "GET", path, tok=outsider)
        assert status == 200
        assert "private project rationale" not in json.dumps(body)
        assert op["operation_id"] not in json.dumps(body)
    assert (await d.call_api("op_list", {}, OTHER))["operations"] == []
    assert (await d.call_api("api_events", {}, OTHER))["events"] == []
    with pytest.raises(OperationError, match="NOT_FOUND"):
        await d.call_api("op_get", {"operation_id": op["operation_id"]}, OTHER)
    with pytest.raises(OperationError, match="NOT_FOUND"):
        await d.call_api("integration_get", {"operation_id": op["operation_id"]}, OTHER)
    # A page full of hidden PM events still advances the global replay cursor.
    page = dashboard_sync.event_page(d.journal, OTHER, 0, 1)
    assert page["events"] == [] and page["next_cursor"] > 0
    listing = d.ops.list(limit=1, principal=OTHER)
    assert listing["operations"] == [] and listing["next_before"] is not None


@pytest.mark.parametrize("method,query", [
    ("pm_thread_get", "limit=0"), ("pm_thread_get", "limit=2&limit=3"),
    ("pm_thread_get", "before_input=-1"), ("pm_context_get", "max_chars=1"),
    ("pm_context_get", "actor=owner"), ("pm_thread_get", "workspace_id=foreign"),
])
async def test_pm_http_rejects_ambiguous_or_unbounded_read_parameters(served, method, query):
    d, port = served
    _, target = await seed(d)
    auth = token(d, OWNER.actor, *OWNER.scopes)
    path = f"/api/v1/pm/workspaces/{target['workspace_id']}/threads/{target['thread_id']}"
    if method == "pm_context_get":
        path += "/context"
    status, _ = await http(port, "GET", path + "?" + query, tok=auth)
    assert status == 422


async def test_pm_mcp_reads_share_http_principal_and_do_not_create_operations(served, monkeypatch, mock):
    d, port = served
    workspace, target = await seed(d)
    auth = token(d, OWNER.actor, *OWNER.scopes)
    monkeypatch.setenv("BATC_TASK_URL", f"http://127.0.0.1:{port}/rpc")
    monkeypatch.setenv("BATC_API_TOKEN", auth)
    server, fleet = mcp_server.build_server(d.fleet.config, read_only=True, principal_only=True)
    before = d.journal.db.execute("SELECT count(*) FROM operations").fetchone()[0]
    try:
        cases = [
            ("pm_workspaces_list", {}, "/api/v1/pm/workspaces"),
            ("pm_workspace_get", {"workspace_id": target["workspace_id"]}, f"/api/v1/pm/workspaces/{target['workspace_id']}"),
            ("pm_profiles_get", {}, "/api/v1/pm/profiles"),
            ("pm_threads_list", {"workspace_id": workspace["workspace_id"]}, f"/api/v1/pm/workspaces/{target['workspace_id']}/threads"),
            ("pm_thread_get", target, f"/api/v1/pm/workspaces/{target['workspace_id']}/threads/{target['thread_id']}"),
            ("pm_context_get", target, f"/api/v1/pm/workspaces/{target['workspace_id']}/threads/{target['thread_id']}/context"),
        ]
        for method, params, path in cases:
            status, expected = await http(port, "GET", path, tok=auth)
            assert status == 200
            result = await server.call_tool(method, params)
            if isinstance(result, tuple):
                content, structured = result
                actual = structured if structured is not None else json.loads(content[0].text)
            else:
                content = result.content if hasattr(result, "content") else result
                actual = json.loads(content[0].text)
            assert actual == expected
        assert d.journal.db.execute("SELECT count(*) FROM operations").fetchone()[0] == before
        assert not write_frames(mock)
    finally:
        await fleet.close()


async def test_revoked_member_does_not_probe_saved_key_with_changed_payload(daemon):
    d = daemon
    workspace, target = await seed(d)
    wid = workspace["workspace_id"]
    await act(d, OWNER, "pm.workspace.member.set", {"workspace_id": wid},
              {"actor": OTHER.actor, "role": "contributor"}, {"expected_version": 1})
    await act(d, OTHER, "pm.message.append", target, {"content": "Original note"},
              {"expected_version": 1}, key="prior")
    await act(d, OWNER, "pm.workspace.member.set", {"workspace_id": wid},
              {"actor": OTHER.actor, "role": None}, {"expected_version": 2})
    with pytest.raises(OperationError) as error:
        await act(d, OTHER, "pm.message.append", target, {"content": "Different note"},
                  {"expected_version": 1}, key="prior")
    assert error.value.status in (403, 404)


async def test_pm_keys_belong_to_workspace_and_caller_without_changing_legacy_keys(daemon):
    d = daemon
    first, _ = await seed(d)
    second, _ = await seed(d)
    a = await act(d, OWNER, "pm.thread.create", {"workspace_id": first["workspace_id"]},
                  {"title": "A"}, {"expected_version": 1}, key="same-key")
    b = await act(d, OWNER, "pm.thread.create", {"workspace_id": second["workspace_id"]},
                  {"title": "B"}, {"expected_version": 1}, key="same-key")
    assert a["operation_id"] != b["operation_id"]
    assert a["idempotency_key"] == b["idempotency_key"] == "same-key"
    replay = await act(d, OWNER, "pm.thread.create", {"workspace_id": first["workspace_id"]},
                       {"title": "A"}, {"expected_version": 1}, key="same-key")
    assert replay["operation_id"] == a["operation_id"]
    with pytest.raises(OperationError, match="IDEMPOTENCY_CONFLICT"):
        await act(d, OWNER, "pm.thread.create", {"workspace_id": first["workspace_id"]},
                  {"title": "Changed"}, {"expected_version": 1}, key="same-key")
    legacy = await act(d, OWNER, "project.create", params={"name": "Legacy"}, key="same-key")
    assert legacy["idempotency_key"] == "same-key"
    assert d.journal.db.execute("SELECT idem_key FROM operations WHERE operation_id=?",
                                (legacy["operation_id"],)).fetchone()[0] == "same-key"


async def test_pm_operation_cannot_be_exposed_through_global_work_item_links(daemon):
    d = daemon
    _, target = await seed(d)
    op = await act(d, OWNER, "pm.message.append", target, {"content": "PM note"}, {"expected_version": 1})
    pid = await project(d)
    iid = await item(d, pid, "Existing global item")
    with pytest.raises(OperationError, match="SCOPED_LINK_REQUIRED"):
        await act(d, OTHER, "work_item.link", {"work_item_id": iid},
                  {"kind": "operation", "ref": op["operation_id"]})


@pytest.mark.parametrize("method,params", [
    ("pm_workspaces_list", {}), ("operations_list", {}), ("events_list", {}),
    ("operation_get", {"operation_id": "op_" + "0" * 32}),
])
async def test_pm_mcp_reads_never_fall_back_to_admin_even_in_legacy_profile(daemon, monkeypatch, method, params):
    monkeypatch.delenv("BATC_API_TOKEN", raising=False)
    server, fleet = mcp_server.build_server(daemon.fleet.config, read_only=True)
    try:
        with pytest.raises(Exception, match="BATC_API_TOKEN"):
            await server.call_tool(method, params)
    finally:
        await fleet.close()
