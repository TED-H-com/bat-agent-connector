"""Workspace lifecycle reads and decisions share authority across public entries."""
from __future__ import annotations

import json
import uuid

import pytest

from bat_agent_connector import api_auth, controller_authority, mcp_server, pm_lifecycle, pm_workspace
from bat_agent_connector.operations import OperationError
from tests.operation_helpers import settle_operations
from tests.test_api_v1 import daemon as api_daemon
from tests.test_api_v1 import http, token, write_frames
from tests.test_api_v1 import served as api_served
from tests.test_mcp_principal import call
from tests.test_pm_api import OTHER, OWNER, seed
from tests.test_pm_discussions import CONTRIBUTOR as SOURCE_CONTRIBUTOR
from tests.test_pm_discussions import OWNER as SOURCE_OWNER
from tests.test_pm_discussions import READER as SOURCE_READER
from tests.test_pm_evidence import capture, link, task
from tests.test_pm_follow_up_api import request as intake_request
from tests.test_pm_run_api import unpack
from tests.test_pm_snapshots import snapshot
from tests.test_pm_sprint_api import pm as pm_fixture
from tests.test_pm_sprint_api import prepared
from tests.test_pm_sprint_api import served as source_served_fixture
from tests.test_pm_sprint_api import world as repository_world
from tests.test_work_items import act

daemon, served = api_daemon, api_served
pm, world, source_served = pm_fixture, repository_world, source_served_fixture
READER = api_auth.Principal("lifecycle-reader", frozenset({"observe", "manage"}))
CONTRIBUTOR = api_auth.Principal("lifecycle-contributor", frozenset({"observe", "manage"}))


def request(wid, action, revision, **params):
    return {"action": "pm.lifecycle." + action, "target": {"workspace_id": wid},
            "params": {"reason": "Keep this direction explicit", **params},
            "preconditions": {"expected_lifecycle_revision": revision}}


async def submit(d, port, body, *, principal=OWNER, key=None):
    status, response = await http(port, "POST", "/api/v1/operations",
        tok=token(d, principal.actor, *principal.scopes), body=body,
        headers={"Idempotency-Key": key or uuid.uuid4().hex})
    assert status in (200, 202), response
    await settle_operations(d.ops)
    op = d.ops.get(response["operation"]["operation_id"])
    assert op["status"] == "succeeded", op
    return op


async def grant(d, wid, principal, role):
    version = pm_workspace.get_workspace(d.ops.db, OWNER, wid)["version"]
    return await act(d, OWNER, "pm.workspace.member.set", {"workspace_id": wid},
                     {"actor": principal.actor, "role": role}, {"expected_version": version})


async def test_lifecycle_http_rpc_mcp_parity_and_read_only_discovery(served, monkeypatch, mock):
    d, port = served
    workspace, _ = await seed(d)
    wid = workspace["workspace_id"]
    await grant(d, wid, READER, "reader")
    initial = pm_lifecycle.get_lifecycle(d.ops, READER, wid)
    assert initial["lifecycle"]["stage"] == "idea" and initial["lifecycle"]["revision"] == 0
    op = await submit(d, port, request(wid, "set_stage", 0, stage="development"))
    tid = op["result"]["transition_id"]
    auth = token(d, READER.actor, "observe")
    monkeypatch.setenv("BATC_TASK_URL", f"http://127.0.0.1:{port}/rpc")
    monkeypatch.setenv("BATC_API_TOKEN", auth)
    server, fleet = mcp_server.build_server(d.fleet.config, read_only=True, principal_only=True)
    prefix = f"/api/v1/pm/workspaces/{wid}/lifecycle"
    before = d.ops.db.total_changes
    try:
        tools = {tool.name: tool for tool in await server.list_tools()}
        assert "operation_submit" not in tools
        for method, fields, suffix in [
            ("pm_lifecycle_get", {}, ""),
            ("pm_lifecycle_transitions_list", {"after": 0, "limit": 1}, "/transitions?after=0&limit=1"),
            ("pm_lifecycle_transition_get", {"transition_id": tid}, "/transitions/" + tid),
            ("pm_lifecycle_inflight_list", {"transition_id": tid, "after": 0, "limit": 1},
             "/inflight?transition_id=" + tid + "&after=0&limit=1"),
        ]:
            assert tools[method].annotations.read_only_hint
            status, expected = await http(port, "GET", prefix + suffix, tok=auth)
            assert status == 200, expected
            params = {"workspace_id": wid, **fields}
            assert await d.call_api(method, params, READER) == expected
            assert unpack(await server.call_tool(method, params)) == expected
        for method in ("pm_lifecycle_transitions_list", "pm_lifecycle_inflight_list"):
            for invalid in ({"limit": True}, {"after": "0"}):
                rejected = await call(server, method, {"workspace_id": wid, **invalid})
                assert "validation" in rejected.lower() and "next_cursor" not in rejected
        assert d.ops.db.total_changes == before
        assert not write_frames(mock)
    finally:
        await fleet.close()


async def test_lifecycle_decision_original_key_survives_later_revisions_across_entries(served, monkeypatch, mock):
    d, port = served
    workspace, target = await seed(d)
    wid = workspace["workspace_id"]
    original = request(wid, "pause", 0)
    paused = await submit(d, port, original, key="original-pause")
    assert paused["result"]["stage"] == "paused"
    closed = await submit(d, port, request(wid, "close", 1))
    assert closed["result"]["stage"] == "closed"
    auth = token(d, OWNER.actor, *OWNER.scopes)
    status, replay = await http(port, "POST", "/api/v1/operations", tok=auth,
        body=original, headers={"Idempotency-Key": "original-pause"})
    assert status == 200 and not replay["created"]
    assert replay["operation"]["operation_id"] == paused["operation_id"]
    rpc = await d.call_api("op_submit", {**original, "idempotency_key": "original-pause"}, OWNER)
    assert not rpc["created"] and rpc["operation"]["result"] == paused["result"]
    monkeypatch.setenv("BATC_TASK_URL", f"http://127.0.0.1:{port}/rpc")
    monkeypatch.setenv("BATC_API_TOKEN", auth)
    server, fleet = mcp_server.build_server(d.fleet.config, principal_only=True)
    try:
        mcp = unpack(await server.call_tool("operation_submit", {**original,
            "idempotency_key": "original-pause", "confirm": True, "wait_s": 0}))
        assert not mcp["created"] and mcp["operation"]["result"] == paused["result"]
    finally:
        await fleet.close()
    status, stale = await http(port, "POST", "/api/v1/operations", tok=auth,
        body=request(wid, "reopen", 0, stage="planning"), headers={"Idempotency-Key": "stale-new-direction"})
    assert status == 409 and stale["error"]["code"] == "VERSION_CONFLICT"
    reopened = await submit(d, port, request(wid, "reopen", 2, stage="planning"))
    assert reopened["result"]["revision"] == 3
    assert pm_lifecycle.get_lifecycle(d.ops, OWNER, wid)["lifecycle"]["stage"] == "planning"
    thread = await d.call_api("pm_thread_get", target, OWNER)
    assert thread["thread"]["mode"] == "idea"
    assert not write_frames(mock)


async def test_manage_only_owner_can_recover_original_lifecycle_request_but_not_read(served):
    d, port = served
    workspace, _ = await seed(d)
    wid = workspace["workspace_id"]
    manager = api_auth.Principal(OWNER.actor, frozenset({"manage"}))
    body = request(wid, "pause", 0)
    original = await submit(d, port, body, principal=manager, key="manage-only-stop")
    replay = await submit(d, port, body, principal=manager, key="manage-only-stop")
    assert replay["operation_id"] == original["operation_id"]
    auth = token(d, manager.actor, "manage")
    assert (await http(port, "GET", f"/api/v1/pm/workspaces/{wid}/lifecycle", tok=auth))[0] == 403
    with pytest.raises(OperationError, match="FORBIDDEN"):
        await d.call_api("pm_lifecycle_get", {"workspace_id": wid}, manager)


async def test_only_current_owner_can_decide_without_scope_or_admin_bypass(served):
    d, port = served
    workspace, _ = await seed(d)
    wid = workspace["workspace_id"]
    for principal, role in [(READER, "reader"), (CONTRIBUTOR, "contributor")]:
        await grant(d, wid, principal, role)
        for suffix, params in [("set_stage", {"stage": "planning"}), ("pause", {}), ("close", {}),
                               ("reopen", {"stage": "idea"}),
                               ("retain_inflight", {"transition_id": "pmlt_" + "a" * 32,
                                                    "operation_ids": ["op_" + "a" * 32]})]:
            status, _ = await http(port, "POST", "/api/v1/operations", tok=token(d, principal.actor, *principal.scopes),
                body=request(wid, suffix, 0, **params), headers={"Idempotency-Key": uuid.uuid4().hex})
            assert status == 403
    status, _ = await http(port, "POST", "/api/v1/operations", tok=d._admin_token,
        body=request(wid, "pause", 0), headers={"Idempotency-Key": "admin-not-owner"})
    assert status == 403
    assert (await http(port, "POST", "/api/v1/operations", tok=token(d, OWNER.actor, "observe"),
        body=request(wid, "pause", 0), headers={"Idempotency-Key": "owner-without-manage"}))[0] == 403
    assert pm_lifecycle.get_lifecycle(d.ops, OWNER, wid)["lifecycle"]["revision"] == 0


async def test_lifecycle_strict_selectors_and_full_width_cursors(served):
    d, port = served
    workspace, _ = await seed(d)
    wid, auth = workspace["workspace_id"], token(d, OWNER.actor, *OWNER.scopes)
    prefix = f"/api/v1/pm/workspaces/{wid}/lifecycle"
    for suffix in ["?after=0", "?actor=", "/transitions?after=", "/transitions?after=0&after=1",
        "/transitions?after=-1", "/transitions?after=1.0", "/transitions?after=9223372036854775808",
        "/transitions?limit=", "/transitions?limit=0", "/transitions?limit=101", "/transitions?limit=1&limit=2",
        "/transitions?transition_id=pmlt_" + "a" * 32, "/inflight?actor=owner", "/inflight?workspace_id=foreign"]:
        assert (await http(port, "GET", prefix + suffix, tok=auth))[0] == 422, suffix
    for suffix in ["/inflight?transition_id=", "/inflight?transition_id=0", "/inflight?transition_id=not-a-transition"]:
        assert (await http(port, "GET", prefix + suffix, tok=auth))[0] in (404, 422), suffix
    for method, suffix in [("pm_lifecycle_transitions_list", "/transitions"), ("pm_lifecycle_inflight_list", "/inflight")]:
        params = {"workspace_id": wid, "after": 2**63 - 1, "limit": 100}
        status, empty = await http(port, "GET", prefix + suffix + "?after=9223372036854775807&limit=100", tok=auth)
        assert status == 200 and empty["next_cursor"] is None
        assert empty == await d.call_api(method, params, OWNER)
        for bad in [{"limit": True}, {"limit": None}, {"after": "0"}, {"after": None}, {"after": False},
                    {"after": 2**63}, {"actor": OWNER.actor}]:
            with pytest.raises(OperationError, match="INVALID_PARAMS"):
                await d.call_api(method, {"workspace_id": wid, **bad}, OWNER)
    for bad in [False, 0, [], ""]:
        with pytest.raises(OperationError):
            await d.call_api("pm_lifecycle_inflight_list", {"workspace_id": wid, "transition_id": bad}, OWNER)


async def test_lifecycle_history_really_paginates_and_keeps_original_transition(served):
    d, port = served
    workspace, _ = await seed(d)
    wid, auth = workspace["workspace_id"], token(d, OWNER.actor, *OWNER.scopes)
    ops = []
    for revision, stage in enumerate(["planning", "development", "idea"]):
        ops.append(await submit(d, port, request(wid, "set_stage", revision, stage=stage)))
    prefix = f"/api/v1/pm/workspaces/{wid}/lifecycle/transitions"
    cursor, received = 0, []
    for index in range(3):
        status, page = await http(port, "GET", prefix + f"?after={cursor}&limit=1", tok=auth)
        assert status == 200 and len(page["transitions"]) == 1
        received += page["transitions"]
        assert (page["next_cursor"] is None) is (index == 2)
        if page["next_cursor"] is not None:
            assert page["next_cursor"] > cursor
            cursor = page["next_cursor"]
    assert [entry["transition_id"] for entry in received] == [op["result"]["transition_id"] for op in ops]
    fixed = (await http(port, "GET", prefix + "/" + received[0]["transition_id"], tok=auth))[1]
    assert fixed["transition"]["to_stage"] == "planning" and fixed["transition"]["revision"] == 1
    assert fixed["transition"]["snapshot"]["coverage"]["delivery"] == "unassessed"


async def test_global_lifecycle_feature_policy_matches_stopped_workspace_and_recording_survives(served, mock):
    d, port = served
    workspace, target = await seed(d)
    wid, auth = workspace["workspace_id"], token(d, OWNER.actor, *OWNER.scopes)
    feature = (await http(port, "GET", "/api/v1/capabilities", tok=auth))[1]["features"]["pm_workspaces"]["lifecycle"]
    assert feature["version"] == 1
    assert "pm.run.analyze" in feature["gated_actions"]
    assert "pm.workspace.create" not in feature["gated_actions"]
    assert "pm.message.append" in feature["record_actions"]
    await submit(d, port, request(wid, "pause", 0))
    lifecycle = (await http(port, "GET", f"/api/v1/pm/workspaces/{wid}/lifecycle", tok=auth))[1]
    assert lifecycle["blocked_actions"] == feature["gated_actions"]
    assert not lifecycle["maintenance"]["available"]
    assert "maintain" not in lifecycle["available_stages"]
    status, denied = await http(port, "POST", "/api/v1/operations", tok=auth,
        body={"action": "pm.thread.create", "target": {"workspace_id": wid}, "params": {"title": "New investment"},
              "preconditions": {"expected_version": workspace["version"]}}, headers={"Idempotency-Key": "new-after-stop"})
    assert status == 409 and denied["error"]["code"] == "PM_LIFECYCLE_STOPPED"
    note = await act(d, OWNER, "pm.message.append", target, {"content": "Save the already known outcome"}, {"expected_version": 1})
    assert note["status"] == "succeeded"
    assert not write_frames(mock)


async def test_current_membership_hides_lifecycle_history_global_operations_and_events(served, monkeypatch):
    d, port = served
    workspace, _ = await seed(d)
    wid = workspace["workspace_id"]
    await grant(d, wid, READER, "reader")
    op = await submit(d, port, request(wid, "pause", 0, reason="Private strategic rationale"))
    tid, oid = op["result"]["transition_id"], op["operation_id"]
    auth = token(d, READER.actor, "observe")
    monkeypatch.setenv("BATC_TASK_URL", f"http://127.0.0.1:{port}/rpc")
    monkeypatch.setenv("BATC_API_TOKEN", auth)
    server, fleet = mcp_server.build_server(d.fleet.config, read_only=True, principal_only=True)
    prefix = f"/api/v1/pm/workspaces/{wid}/lifecycle"
    await grant(d, wid, READER, None)
    try:
        for method, params, suffix in [("pm_lifecycle_get", {}, ""),
            ("pm_lifecycle_transitions_list", {}, "/transitions"),
            ("pm_lifecycle_transition_get", {"transition_id": tid}, "/transitions/" + tid),
            ("pm_lifecycle_inflight_list", {"transition_id": tid}, "/inflight?transition_id=" + tid)]:
            assert (await http(port, "GET", prefix + suffix, tok=auth))[0] == 404
            with pytest.raises(OperationError, match="NOT_FOUND"):
                await d.call_api(method, {"workspace_id": wid, **params}, READER)
            assert "NOT_FOUND" in await call(server, method, {"workspace_id": wid, **params})
        for path in ["/api/v1/operations", "/api/v1/events", "/api/v1/bootstrap"]:
            status, page = await http(port, "GET", path, tok=auth)
            assert status == 200
            encoded = json.dumps(page)
            assert oid not in encoded and tid not in encoded and "Private strategic rationale" not in encoded
        assert (await http(port, "GET", "/api/v1/operations/" + oid, tok=auth))[0] == 404
        assert tid not in await call(server, "events_list", {"after": 0, "limit": 500})
        assert oid not in json.dumps(await d.call_api("op_list", {}, READER))
        assert tid not in json.dumps(await d.call_api("api_events", {"after": 0}, OTHER))
    finally:
        await fleet.close()


async def test_retained_private_source_cannot_leak_through_lifecycle_history_or_global_reads(source_served, monkeypatch):
    d, port, selection = source_served
    data = await prepared(d, selection)
    wid, work = data["workspace_id"], {"workspace_id": data["workspace_id"], "work_item_id": data["work"][0]}
    public_snapshot = await snapshot(d.ops, wid)
    with controller_authority.use(controller_authority.binding(d.ops.db, SOURCE_OWNER), d.journal):
        source = task(d.ops)
        await link(d.ops, work, "task", source["task_id"])
        await capture(d.ops, work)
    private_snapshot = await snapshot(d.ops, wid)
    pending = []
    for fixed in [private_snapshot, public_snapshot]:
        operation, _ = d.ops.create(SOURCE_CONTRIBUTOR, **intake_request(d, wid, fixed))
        # A persisted legacy unknown step has no proof that resending is safe.
        # Seed the original journal receipt; no external process is launched.
        d.ops._step_start(operation["operation_id"], "legacy.external.unknown", {})
        d.ops.db.execute("UPDATE operation_steps SET status='uncertain' WHERE operation_id=?", (operation["operation_id"],))
        d.ops.db.execute("UPDATE operations SET status='needs_attention' WHERE operation_id=?", (operation["operation_id"],))
        pending.append(operation["operation_id"])
    pause = await submit(d, port, request(wid, "pause", 0), principal=SOURCE_OWNER)
    assert pause["result"]["status"] == "pausing"
    retained = await submit(d, port, request(wid, "retain_inflight", 1,
        transition_id=pause["result"]["transition_id"], operation_ids=[pending[0]],
        reason="PRIVATE ORIGINAL SOURCE disposition"), principal=SOURCE_OWNER, key="original-retention")
    tid = retained["result"]["transition_id"]
    auth = token(d, SOURCE_READER.actor, "observe")
    monkeypatch.setenv("BATC_TASK_URL", f"http://127.0.0.1:{port}/rpc")
    monkeypatch.setenv("BATC_API_TOKEN", auth)
    server, fleet = mcp_server.build_server(d.fleet.config, read_only=True, principal_only=True)
    root = f"/api/v1/pm/workspaces/{wid}/lifecycle"
    try:
        before = (await http(port, "GET", root + "/inflight?transition_id=" + tid + "&limit=1", tok=auth))[1]
        assert before["inflight"][0]["operation_id"] == pending[0]
        assert before["inflight"][0]["disposition"] == "retained"
        assert before["inflight"][0]["steps"][0]["status"] == "uncertain"
        d.ops.db.execute("UPDATE tasks SET controller_authority='{}' WHERE task_id=?", (source["task_id"],))
        status, filtered = await http(port, "GET", root + "/inflight?transition_id=" + tid + "&limit=1", tok=auth)
        assert status == 200 and filtered["inflight"] == [] and filtered["visibility_filtered"]
        assert filtered["next_cursor"] is not None
        second = (await http(port, "GET", root + f"/inflight?transition_id={tid}&limit=1&after={filtered['next_cursor']}", tok=auth))[1]
        assert second["inflight"][0]["operation_id"] == pending[1] and second["next_cursor"] is None
        state = (await http(port, "GET", root, tok=auth))[1]
        assert state["lifecycle"]["stage"] == "paused" and state["transition"] is None
        assert state["transition_visibility_filtered"]
        assert (await http(port, "GET", root + "/transitions/" + tid, tok=auth))[0] == 404
        for oid in [pending[0], retained["operation_id"]]:
            assert (await http(port, "GET", "/api/v1/operations/" + oid, tok=auth))[0] == 404
            assert "NOT_FOUND" in await call(server, "operation_get", {"operation_id": oid})
        for method, fields, path in [("pm_lifecycle_get", {}, root),
            ("pm_lifecycle_transitions_list", {}, root + "/transitions"),
            ("pm_lifecycle_inflight_list", {"transition_id": tid}, root + "/inflight?transition_id=" + tid)]:
            params = {"workspace_id": wid, **fields}
            status, value = await http(port, "GET", path, tok=auth)
            assert status == 200
            assert value == await d.call_api(method, params, SOURCE_READER)
            assert value == unpack(await server.call_tool(method, params))
            assert "PRIVATE ORIGINAL SOURCE" not in json.dumps(value) and pending[0] not in json.dumps(value)
        for path in ["/api/v1/operations", "/api/v1/events", "/api/v1/bootstrap"]:
            encoded = json.dumps((await http(port, "GET", path, tok=auth))[1])
            assert "PRIVATE ORIGINAL SOURCE" not in encoded and retained["operation_id"] not in encoded
        assert "PRIVATE ORIGINAL SOURCE" not in await call(server, "events_list", {"after": 0, "limit": 500})
        assert d.ops.get(pending[0])["status"] == "needs_attention"
    finally:
        await fleet.close()
