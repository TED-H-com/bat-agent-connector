"""HTTP/RPC/MCP native PM integration uses a fake process, never a model request."""
from __future__ import annotations

import asyncio

from bat_agent_connector import api_auth, mcp_server, pm_discussions, pm_native_runtime, pm_reads
from tests.operation_helpers import settle_operations
from tests.test_api_v1 import daemon as api_daemon
from tests.test_api_v1 import http, token, write_frames
from tests.test_api_v1 import served as api_served
from tests.test_pm_api import OWNER, seed
from tests.test_pm_native_operations import FakeRuntime, await_runtime
from tests.test_pm_native_runtime import MODEL, THREAD, TURN
from tests.test_pm_run_api import unpack

daemon, served = api_daemon, api_served
REQUESTER = api_auth.Principal(OWNER.actor, frozenset({"observe", "manage", "start"}))


def native_fixture(d):
    settings, instances = {}, []
    d.ops.context["pm_native_runtime_factory"] = lambda *args, **kwargs: FakeRuntime(
        *args, **kwargs, settings=settings, instances=instances)
    d.ops.context["pm_native_process_inspector"] = lambda identity: {
        "state": "unknown", "process": identity, "proof": "identity_unavailable", "observed_at": 1.0}
    return settings, instances


def request(target):
    return {"action": "pm.run.analyze", "target": target,
            "params": {"purpose": "Compare confirmed inputs with unresolved choices", "model": MODEL},
            "preconditions": {"expected_version": 1}}


async def test_http_and_rpc_native_analysis_recover_original_reply_and_native_provenance(served, mock):
    d, port = served
    _, target = await seed(d)
    _, instances = native_fixture(d)
    auth = token(d, REQUESTER.actor, *REQUESTER.scopes)
    body = request(target)
    status, accepted = await http(port, "POST", "/api/v1/operations", tok=auth, body=body,
                                  headers={"Idempotency-Key": "native-original"})
    assert status == 202, accepted
    await settle_operations(d.ops)
    op = d.ops.get(accepted["operation"]["operation_id"], steps=False)
    assert op["status"] == "succeeded", op
    assert len(instances) == 1 and instances[0].sends == 1 and instances[0].closed
    status, replay = await http(port, "POST", "/api/v1/operations", tok=auth, body=body,
                                headers={"Idempotency-Key": "native-original"})
    assert status == 200 and not replay["created"]
    shown = dict(replay["operation"])
    loop = shown.pop("loop")
    assert shown == op
    # docs/design/v1.3-e10-parity.md, a succeeded discussion action (pm.run.analyze).
    assert loop == {
        "operation_id": op["operation_id"],  # :238
        "work_item_id": None,  # :225, :239
        "attempt_id": None,  # :240
        "state": {"operation_status": "succeeded", "updated_at": op["updated_at"]},  # :241-243
        "next_allowed_actions": [],  # :266-267, terminal omits cancel
        "blocked_actions": [],  # :266-267
        "links": None,  # :246
        "dashboard_hash": f"#/pm/{target['workspace_id']}/discussions/{target['thread_id']}",  # :247, :259
        "result_read": None,  # :248
        "completeness": {"status": "complete", "reason": None},  # :232, :292
    }
    path = f"/api/v1/pm/workspaces/{target['workspace_id']}/threads/{target['thread_id']}"
    status, thread = await http(port, "GET", path, tok=auth)
    assert status == 200 and thread == await d.call_api("pm_thread_get", target, REQUESTER)
    reply = thread["messages"][-1]
    assert reply["role"] == "assistant" and reply["actor"] != REQUESTER.actor
    assert reply["provenance"]["native_turn_id"] == TURN
    assert reply["provenance"]["model"] == MODEL
    assert reply["provenance"]["source_refs"]["native_thread_id"] == THREAD
    assert thread["thread"]["mode"] == "idea" and thread["draft"] is None
    assert not op["result"]["execution_authorized"] and not write_frames(mock)


async def test_registered_native_capability_is_separate_from_legacy_bat_and_never_spawns(served, monkeypatch):
    d, port = served
    _, target = await seed(d)
    _, instances = native_fixture(d)
    installation = {"adapter": "codex-native-linux", "eligible_for_preflight": True,
                    "options_confirmed": False, "auth_validity": "unverified", "model_execution_verified": False}
    monkeypatch.setattr(pm_native_runtime, "installation_eligibility", lambda: installation.copy())
    wid = target["workspace_id"]
    cap = pm_reads.read(d.ops, REQUESTER, "pm_capabilities_get", {"workspace_id": wid})
    assert cap["native_analysis"] == {**installation, "action_registered": True, "authorized": True}
    assert not pm_reads.read(d.ops, OWNER, "pm_capabilities_get", {"workspace_id": wid})["native_analysis"]["authorized"]
    thread = pm_discussions.get_thread(d.ops, REQUESTER, **target)
    listing = pm_discussions.list_threads(d.ops, REQUESTER, wid)
    assert thread["capabilities"]["native_analysis"] == cap["native_analysis"]
    assert listing["capabilities"]["native_analysis"] == cap["native_analysis"]
    assert listing["runtime"] == {"available": False, "adapter": "bat", "legacy": True,
                                  "reason": "external_tool_policy_unverified"}
    assert thread["capabilities"]["runtime"] is False and thread["capabilities"]["runtime_adapter"] == "bat"
    auth = token(d, REQUESTER.actor, *REQUESTER.scopes)
    status, global_caps = await http(port, "GET", "/api/v1/capabilities", tok=auth)
    assert status == 200 and global_caps["features"]["pm_workspaces"]["native_analysis"] is True
    assert global_caps["features"]["pm_workspaces"]["runner"] is False and not instances


async def test_mcp_native_action_and_capability_use_the_same_central_contract(served, monkeypatch, mock):
    d, port = served
    _, target = await seed(d)
    _, instances = native_fixture(d)
    auth = token(d, REQUESTER.actor, *REQUESTER.scopes)
    monkeypatch.setenv("BATC_TASK_URL", f"http://127.0.0.1:{port}/rpc")
    monkeypatch.setenv("BATC_API_TOKEN", auth)
    server, fleet = mcp_server.build_server(d.fleet.config, read_only=False, principal_only=True)
    try:
        accepted = unpack(await server.call_tool("operation_submit", {
            **request(target), "idempotency_key": "mcp-native-original", "confirm": True, "wait_s": 0}))
        await settle_operations(d.ops)
        operation = d.ops.get(accepted["operation"]["operation_id"])
        assert operation["status"] == "succeeded", operation
        run_id = operation["result"]["run_id"]
        for method, params, path in [
            ("pm_run_get", {"workspace_id": target["workspace_id"], "run_id": run_id},
             f"/api/v1/pm/workspaces/{target['workspace_id']}/runs/{run_id}"),
            ("pm_capabilities_get", {"workspace_id": target["workspace_id"]},
             f"/api/v1/pm/workspaces/{target['workspace_id']}/capabilities"),
        ]:
            status, expected = await http(port, "GET", path, tok=auth)
            assert status == 200 and unpack(await server.call_tool(method, params)) == expected
        assert len(instances) == 1 and instances[0].sends == 1 and not write_frames(mock)
    finally:
        await fleet.close()


async def test_http_cancel_routes_to_owned_process_reconciliation_and_no_assistant(served, mock):
    d, port = served
    _, target = await seed(d)
    settings, instances = native_fixture(d)
    settings["block_turn"] = True
    auth = token(d, REQUESTER.actor, *REQUESTER.scopes)
    status, accepted = await http(port, "POST", "/api/v1/operations", tok=auth, body=request(target),
                                  headers={"Idempotency-Key": "cancel-native"})
    assert status == 202
    operation = accepted["operation"]
    runtime = await await_runtime(d.ops, instances, operation)
    status, stopped = await http(port, "POST", f"/api/v1/operations/{operation['operation_id']}/cancel", tok=auth, body={})
    assert status == 200 and stopped["operation"]["cancel_requested"]
    await asyncio.wait_for(settle_operations(d.ops), 5)
    final = d.ops.get(operation["operation_id"])
    assert final["status"] == "cancelled", final
    assert runtime.closed and runtime.sends == 1
    assert pm_discussions.get_thread(d.ops, REQUESTER, **target)["messages"] == []
    assert d.ops.db.execute("SELECT COUNT(*) FROM pm_writer_slots WHERE run_id IS NOT NULL").fetchone()[0] == 0
    assert not write_frames(mock)


async def test_missing_start_scope_cannot_launch_native_analysis(served):
    d, port = served
    _, target = await seed(d)
    _, instances = native_fixture(d)
    auth = token(d, OWNER.actor, *OWNER.scopes)
    status, rejected = await http(port, "POST", "/api/v1/operations", tok=auth, body=request(target),
                                  headers={"Idempotency-Key": "no-start-native"})
    assert status == 403 and rejected["error"]["code"] == "FORBIDDEN"
    assert not instances
