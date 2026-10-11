"""The same fixed source bytes and current authority through HTTP, RPC and MCP."""
import asyncio
from urllib.parse import urlencode

import pytest

from bat_agent_connector import mcp_server
from bat_agent_connector.api_auth import Principal
from bat_agent_connector.operations import OperationError
from tests.test_api_v1 import http, token, write_frames
from tests.test_pm_run_api import unpack
from tests.test_pm_spec_sources import FILES, prepare
from tests.test_pm_worktree import OWNER
from tests.test_pm_worktree import pm as pm_fixture
from tests.test_pm_worktree import world as world_fixture

pm, world = pm_fixture, world_fixture


@pytest.fixture
async def served(pm):
    d = pm["d"]
    server = await asyncio.start_server(d._handle, "127.0.0.1", 0)
    try:
        yield pm, server.sockets[0].getsockname()[1]
    finally:
        server.close()
        await server.wait_closed()


async def test_fixed_spec_sources_http_rpc_mcp_parity_and_observe_only(served, monkeypatch, mock):
    w, port = served
    await prepare(w)
    d, wid = w["d"], w["workspace_id"]
    auth = token(d, OWNER.actor, "observe")
    observer = Principal(OWNER.actor, frozenset({"observe"}))
    monkeypatch.setenv("BATC_TASK_URL", f"http://127.0.0.1:{port}/rpc")
    monkeypatch.setenv("BATC_API_TOKEN", auth)
    server, fleet = mcp_server.build_server(d.fleet.config, read_only=True, principal_only=True)
    before = d.ops.db.total_changes
    try:
        tools = {tool.name: tool for tool in await server.list_tools()}
        for name in ("pm_spec_sources_get", "pm_spec_source_capabilities_get"):
            assert name in tools and tools[name].annotations.readOnlyHint
        params = {"workspace_id": wid}
        prefix = f"/api/v1/pm/workspaces/{wid}/spec-sources"
        count = len(w["runner"].phases)
        status, caps = await http(port, "GET", prefix + "/capabilities", tok=auth)
        assert status == 200 and caps["eligible"] and not caps["git_verified"]
        assert await d.call_api("pm_spec_source_capabilities_get", params.copy(), observer) == caps
        assert unpack(await server.call_tool("pm_spec_source_capabilities_get", params)) == caps
        assert len(w["runner"].phases) == count
        path = "openspec/changes/recovery/tasks.md"
        fields = {"repository": "o/r", "commit_sha": w["sha"], "paths": [path]}
        status, expected = await http(port, "GET", prefix + "?" + urlencode({**fields, "paths": path}), tok=auth)
        assert status == 200, expected
        assert expected["documents"][0]["content"] == FILES[path]
        assert await d.call_api("pm_spec_sources_get", {**params, **fields}, observer) == expected
        assert unpack(await server.call_tool("pm_spec_sources_get", {**params, **fields})) == expected
        assert d.ops.db.total_changes == before and not write_frames(mock)
        status, capabilities = await http(port, "GET", "/api/v1/capabilities", tok=auth)
        assert status == 200 and capabilities["features"]["pm_workspaces"]["spec_sources"] is True
    finally:
        await fleet.close()


async def test_exact_spec_source_queries_and_rpc_types_never_start_git(served):
    w, port = served
    await prepare(w)
    d, wid = w["d"], w["workspace_id"]
    auth = token(d, OWNER.actor, "observe")
    base = f"/api/v1/pm/workspaces/{wid}/spec-sources"
    valid = "repository=o/r&commit_sha=" + w["sha"]
    count = len(w["runner"].phases)
    for suffix in ["", "?repository=o/r", "?commit_sha=" + w["sha"], "?" + valid + "&actor=other",
                   "?" + valid + "&repository=o/r", "?" + valid + "&paths=openspec/config.yaml&paths=openspec/config.yaml",
                   "?" + valid + "&paths=", "?" + valid + "&paths=openspec/config.yaml,openspec/config.yaml",
                   "?" + valid + "&paths=../secret", "/capabilities?limit=1"]:
        status, value = await http(port, "GET", base + suffix, tok=auth)
        assert status == 422, (suffix, value)
    for extra in [{"paths": []}, {"paths": "openspec/config.yaml"}, {"paths": [None]}, {"actor": "other"},
                  {"commit_sha": 123}, {"repository": None}, {"paths": ["openspec/config.yaml"] * 101}]:
        with pytest.raises(OperationError) as caught:
            await d.call_api("pm_spec_sources_get", {"workspace_id": wid, "repository": "o/r", "commit_sha": w["sha"], **extra}, OWNER)
        assert caught.value.code == "INVALID_PARAMS"
    assert len(w["runner"].phases) == count


async def test_capabilities_do_not_prepare_carrier_and_membership_is_current(served):
    w, port = served
    d, wid = w["d"], w["workspace_id"]
    prefix = f"/api/v1/pm/workspaces/{wid}/spec-sources"
    auth = token(d, OWNER.actor, "observe")
    status, caps = await http(port, "GET", prefix + "/capabilities", tok=auth)
    assert status == 200 and caps["reason"] == "PM_SPEC_BINDING_UNAVAILABLE" and not w["runner"].phases
    assert (await http(port, "GET", prefix + "/capabilities", tok=token(d, OWNER.actor, "manage")))[0] == 403
    assert (await http(port, "GET", prefix + "/capabilities", tok=token(d, "outsider", "observe")))[0] == 404
    await prepare(w)
    def revoke(_):
        d.ops.db.execute("DELETE FROM pm_workspace_members WHERE workspace_id=? AND actor=?", (wid, OWNER.actor))
    w["runner"].after["read.spec-sources"] = revoke
    status, result = await http(port, "GET", prefix + "?repository=o/r&commit_sha=" + w["sha"], tok=auth)
    assert status == 404 and "documents" not in result
    assert (await http(port, "GET", prefix + "/capabilities", tok=auth))[0] == 404
