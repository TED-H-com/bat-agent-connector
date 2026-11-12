"""The independent read and policy action use existing central transport authority."""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from bat_agent_connector import mcp_server
from bat_agent_connector import pm_workspace_wip as wip
from bat_agent_connector.operations import OperationError
from tests.test_api_v1 import daemon as daemon
from tests.test_api_v1 import http, token
from tests.test_api_v1 import served as served
from tests.test_mcp_principal import call
from tests.test_pm_api import OTHER, OWNER, seed
from tests.test_pm_run_api import unpack
from tests.test_pm_workspace_wip import request


async def test_http_rpc_mcp_success_refusal_and_no_probe_parity(served, monkeypatch, mock):
    d, port = served
    _, target = await seed(d)
    wid = target["workspace_id"]
    auth = token(d, OWNER.actor, *OWNER.scopes)
    monkeypatch.setenv("BATC_TASK_URL", f"http://127.0.0.1:{port}/rpc")
    monkeypatch.setenv("BATC_API_TOKEN", auth)
    server, fleet = mcp_server.build_server(d.fleet.config, read_only=False, principal_only=True)
    try:
        env = request(wid)
        body = {k: v for k, v in env.items() if k != "idempotency_key"}
        status, response = await http(port, "POST", "/api/v1/operations", tok=auth, body=body,
                                      headers={"Idempotency-Key": env["idempotency_key"]})
        assert status == 202, response
        from tests.operation_helpers import settle_operations
        await settle_operations(d.ops)
        assert d.ops.get(response["operation"]["operation_id"])["status"] == "succeeded"
        monkeypatch.setattr(wip, "time", SimpleNamespace(time=lambda: 1_800_000_000))
        before, probes = d.ops.db.total_changes, len(mock.invokes)
        for query, suffix in (({}, ""), ({"policy_revision": 1}, "?policy_revision=1")):
            fields = {"workspace_id": wid, **query}
            status, value = await http(port, "GET", f"/api/v1/pm/workspaces/{wid}/wip{suffix}", tok=auth)
            assert status == 200, value
            assert value["used"] == 0 and value["admission_checked"] is False
            assert await d.call_api(wip.READ, fields.copy(), OWNER) == value
            assert unpack(await server.call_tool(wip.READ, fields)) == value
        assert d.ops.db.total_changes == before and len(mock.invokes) == probes
        status, error = await http(port, "GET", f"/api/v1/pm/workspaces/{wid}/wip?policy_revision=9", tok=auth)
        assert status == 404 and error["error"]["code"] == "NOT_FOUND"
        with pytest.raises(OperationError) as exc:
            await d.call_api(wip.READ, {"workspace_id": wid, "policy_revision": 9}, OWNER)
        assert exc.value.code == "NOT_FOUND"
        assert "NOT_FOUND" in await call(server, wip.READ, {"workspace_id": wid, "policy_revision": 9})
        current = wip.get_wip(d.ops, OWNER, wid)["policy"]
        invalid = request(wid, mode="enforced", revision=1, digest=current["document_digest"])
        status, error = await http(port, "POST", "/api/v1/operations", tok=auth,
                                  body={k: v for k, v in invalid.items() if k != "idempotency_key"},
                                  headers={"Idempotency-Key": invalid["idempotency_key"]})
        assert status == 422 and error["error"]["code"] == "PM_WIP_MODE_UNSUPPORTED", error
        with pytest.raises(OperationError) as exc:
            await d.call_api("op_submit", invalid.copy(), OWNER)
        assert exc.value.code == "PM_WIP_MODE_UNSUPPORTED"
        assert "PM_WIP_MODE_UNSUPPORTED" in await call(server, "operation_submit", {**invalid, "confirm": True})
    finally:
        await fleet.close()


@pytest.mark.parametrize("query", ["?policy_revision=", "?policy_revision=0", "?policy_revision=-1", "?policy_revision=1.5",
    "?policy_revision=true", "?policy_revision=9223372036854775808", "?policy_revision=1&policy_revision=2", "?after=0", "?probe=true"])
async def test_strict_http_query(served, query):
    d, port = served
    _, target = await seed(d)
    status, _ = await http(port, "GET", f"/api/v1/pm/workspaces/{target['workspace_id']}/wip{query}",
                           tok=token(d, OWNER.actor, "observe"))
    assert status == 422


async def test_readonly_client_cannot_save_and_installed_capability_is_explicit(served, monkeypatch):
    d, port = served
    _, target = await seed(d)
    wid = target["workspace_id"]
    auth = token(d, OWNER.actor, "observe")
    monkeypatch.setenv("BATC_TASK_URL", f"http://127.0.0.1:{port}/rpc")
    monkeypatch.setenv("BATC_API_TOKEN", auth)
    server, fleet = mcp_server.build_server(d.fleet.config, read_only=True, principal_only=True)
    try:
        names = {tool.name for tool in await server.list_tools()}
        assert wip.READ in names and "operation_submit" not in names
        assert unpack(await server.call_tool(wip.READ, {"workspace_id": wid}))["used"] == 0
        metadata = server._tool_manager.get_tool(wip.READ).fn_metadata
        for bad in ({"policy_revision": True}, {"policy_revision": "1"}, {"unexpected": True}, {"workspace_id": 1}):
            with pytest.raises(ValidationError):
                metadata.validate_arguments({"workspace_id": wid, **bad})
        status, _ = await http(port, "GET", f"/api/v1/pm/workspaces/{wid}/wip", tok=auth, body={})
        assert status == 422
        status, _ = await http(port, "GET", f"/api/v1/pm/workspaces/{wid}/wip", tok=token(d, OTHER.actor, "observe"))
        assert status == 404
        env = request(wid)
        status, _ = await http(port, "POST", "/api/v1/operations", tok=auth, body=env)
        assert status == 403
        expected = wip.capabilities(d.ops)
        _, global_caps = await http(port, "GET", "/api/v1/capabilities", tok=auth)
        caps = await d.call_api("pm_capabilities_get", {"workspace_id": wid}, OWNER)
        assert caps["workspace_wip"] == global_caps["features"]["pm_workspaces"]["workspace_wip"] == expected
        assert expected["enforcement"] is False and expected["modes"] == ["advisory", "disabled"]
        d.ops.actions.pop(wip.ACTION)
        _, global_caps = await http(port, "GET", "/api/v1/capabilities", tok=auth)
        assert "workspace_wip" not in global_caps["features"]["pm_workspaces"]
        assert "workspace_wip" not in await d.call_api("pm_capabilities_get", {"workspace_id": wid}, OWNER)
    finally:
        await fleet.close()
