"""Policy readers share source visibility across HTTP, RPC and MCP."""
import json

import pytest

from bat_agent_connector import mcp_server
from bat_agent_connector import pm_maintenance_policy as policies
from bat_agent_connector.api_auth import Principal
from bat_agent_connector.operations import OperationError
from tests.test_api_v1 import http, token, write_frames
from tests.test_mcp_principal import call
from tests.test_pm_governance_api import send
from tests.test_pm_maintenance_policy import request
from tests.test_pm_release_baseline_api import daemon, served  # noqa: F401 - fixture dependencies
from tests.test_pm_release_baseline_api import ready as baseline_ready
from tests.test_pm_release_baselines import OWNER, READER
from tests.test_pm_release_baselines import request as baseline_request
from tests.test_pm_run_api import unpack

release_ready = baseline_ready


@pytest.fixture
async def ready(release_ready):
    d, port, target, binding, project_id = release_ready
    await send(d, port, baseline_request(d.ops, target, binding, branch="support/1.x"))
    return d, port, target["workspace_id"], project_id


async def test_http_rpc_mcp_reads_and_manage_only_original_receipt(ready, monkeypatch, mock):
    d, port, wid, _ = ready
    original = request(d.ops, wid)
    manage = Principal(OWNER.actor, frozenset({"manage"}))
    done = await send(d, port, original, manage)
    pid = done["result"]["policy_id"]
    auth = token(d, READER.actor, "observe")
    monkeypatch.setenv("BATC_TASK_URL", f"http://127.0.0.1:{port}/rpc")
    monkeypatch.setenv("BATC_API_TOKEN", auth)
    server, fleet = mcp_server.build_server(d.fleet.config, read_only=True, principal_only=True)
    changes, calls = d.ops.db.total_changes, len(d.ops.context["github"].calls)
    try:
        tools = {tool.name: tool for tool in await server.list_tools()}
        for method, fields, suffix in [
            ("pm_maintenance_policies_list", {"after": 0, "limit": 1}, "?after=0&limit=1"),
            ("pm_maintenance_policy_current_get", {}, "/current"),
            ("pm_maintenance_policy_get", {"policy_id": pid}, "/" + pid),
        ]:
            assert tools[method].annotations.read_only_hint
            status, expected = await http(port, "GET", f"/api/v1/pm/workspaces/{wid}/maintenance-policies" + suffix, tok=auth)
            assert status == 200, expected
            params = {"workspace_id": wid, **fields}
            assert await d.call_api(method, params, READER) == expected
            assert unpack(await server.call_tool(method, params)) == expected
        assert d.ops.db.total_changes == changes
        replay = await send(d, port, original, manage)
        assert replay["operation_id"] == done["operation_id"] and replay["result"] == done["result"]
        assert all(replay[key] == original[key] for key in ("target", "params", "preconditions"))
        assert len(d.ops.context["github"].calls) == calls
        status, caps = await http(port, "GET", "/api/v1/capabilities", tok=auth)
        assert status == 200 and caps["features"]["pm_workspaces"]["maintenance_policy"] == policies.capabilities(d.ops)
        assert not write_frames(mock)
    finally:
        await fleet.close()


async def test_exact_read_queries_and_membership_scope(ready):
    d, port, wid, _ = ready
    done = await send(d, port, request(d.ops, wid))
    pid = done["result"]["policy_id"]
    prefix = f"/api/v1/pm/workspaces/{wid}/maintenance-policies"
    auth = token(d, READER.actor, "observe")
    for suffix in ["?after=", "?after=-1", "?after=0&after=1", "?limit=0", "?limit=101", "?actor=",
                   "?execute=true", "/current?limit=1", "/" + pid + "?limit=1"]:
        assert (await http(port, "GET", prefix + suffix, tok=auth))[0] == 422, suffix
    for suffix in ["/latest", "/pmmp_bad", "/" + pid + "/run"]:
        assert (await http(port, "GET", prefix + suffix, tok=auth))[0] == 404
    assert (await http(port, "GET", prefix, tok=token(d, OWNER.actor, "manage")))[0] == 403
    assert (await http(port, "GET", prefix, tok=token(d, "stranger", "observe")))[0] == 404
    for invalid in [{"after": True}, {"after": 2**80}, {"policy_id": pid}, {"controller_id": "injected"}]:
        with pytest.raises(OperationError, match="INVALID_PARAMS"):
            await d.call_api("pm_maintenance_policies_list", {"workspace_id": wid, **invalid}, READER)


async def test_source_withdrawal_filters_pending_complete_events_and_replay(ready, monkeypatch):
    d, port, wid, project_id = ready
    original = request(d.ops, wid)
    original["params"]["rationale"] = "PRIVATE POLICY SOURCE QUOTE"
    done = await send(d, port, original)
    pending = d.ops.create(OWNER, **request(d.ops, wid))[0]
    pid = done["result"]["policy_id"]
    auth = token(d, READER.actor, "observe")
    monkeypatch.setenv("BATC_TASK_URL", f"http://127.0.0.1:{port}/rpc")
    monkeypatch.setenv("BATC_API_TOKEN", auth)
    server, fleet = mcp_server.build_server(d.fleet.config, read_only=True, principal_only=True)
    d.ops.db.execute("UPDATE projects SET repositories='[]' WHERE project_id=?", (project_id,))
    try:
        for op in (done, pending):
            oid = op["operation_id"]
            assert (await http(port, "GET", "/api/v1/operations/" + oid, tok=auth))[0] == 404
            with pytest.raises(OperationError):
                await d.call_api("op_get", {"operation_id": oid}, READER)
            assert "NOT_FOUND" in await call(server, "operation_get", {"operation_id": oid})
        root = f"/api/v1/pm/workspaces/{wid}/maintenance-policies"
        assert (await http(port, "GET", root + "/" + pid, tok=auth))[0] == 404
        for method, params, route, rpc in [
            ("pm_maintenance_policies_list", {"workspace_id": wid}, root, "pm_maintenance_policies_list"),
            ("pm_maintenance_policy_current_get", {"workspace_id": wid}, root + "/current", "pm_maintenance_policy_current_get"),
            ("operations_list", {"action": policies.ACTION}, "/api/v1/operations?action=" + policies.ACTION, "op_list"),
            ("events_list", {"after": 0, "limit": 500}, "/api/v1/events?after=0&limit=500", "api_events"),
        ]:
            status, view = await http(port, "GET", route, tok=auth)
            assert status == 200, view
            for value in [json.dumps(view), json.dumps(await d.call_api(rpc, params, READER)), await call(server, method, params)]:
                assert "PRIVATE POLICY SOURCE QUOTE" not in value
                assert pid not in value and all(op["operation_id"] not in value for op in (done, pending))
        status, _ = await http(port, "POST", "/api/v1/operations", tok=token(d, OWNER.actor, "manage"),
            body={k: v for k, v in original.items() if k != "idempotency_key"},
            headers={"Idempotency-Key": original["idempotency_key"]})
        assert status == 404
    finally:
        await fleet.close()
