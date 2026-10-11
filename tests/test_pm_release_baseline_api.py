"""Release baseline HTTP/RPC/MCP parity and original-source operation privacy."""
import asyncio
import json

import pytest

from bat_agent_connector import mcp_server, pm_lifecycle
from bat_agent_connector import pm_release_baselines as baselines
from bat_agent_connector.api_auth import Principal
from bat_agent_connector.config import GitHubConfig, GitHubRepo
from bat_agent_connector.operations import OperationError
from tests.operation_helpers import settle_operations
from tests.test_api_v1 import daemon as api_daemon
from tests.test_api_v1 import http, token, write_frames
from tests.test_api_v1 import served as api_served
from tests.test_mcp_principal import call
from tests.test_pm_governance_api import send
from tests.test_pm_release_baselines import OWNER, READER, Provider, member, remove_repo, request, setup
from tests.test_pm_release_deployments import deployments, selection
from tests.test_pm_release_deployments import setup as deployment_setup
from tests.test_pm_run_api import unpack

daemon, served = api_daemon, api_served
gh, make_daemon = deployments.gh, deployments.make_daemon


@pytest.fixture
async def ready(served):
    d, port = served
    cfg = GitHubConfig(repos={"example/product": GitHubRepo("example/product")})
    d.ops.context.update(github_config=cfg, github=Provider(cfg))
    pm_lifecycle.install(d.ops)
    target, binding, project_id = await setup(d.ops)
    return d, port, target, binding, project_id


async def test_real_reads_http_rpc_mcp_and_exact_original_action_replay(ready, monkeypatch, mock):
    d, port, target, binding, _ = ready
    original = request(d.ops, target, binding)
    only_manage = Principal(OWNER.actor, frozenset({"manage"}))
    done = await send(d, port, original, only_manage)
    bid, wid = done["result"]["baseline_id"], target["workspace_id"]
    auth = token(d, READER.actor, "observe")
    monkeypatch.setenv("BATC_TASK_URL", f"http://127.0.0.1:{port}/rpc")
    monkeypatch.setenv("BATC_API_TOKEN", auth)
    server, fleet = mcp_server.build_server(d.fleet.config, read_only=True, principal_only=True)
    before = d.ops.db.total_changes
    calls = len(d.ops.context["github"].calls)
    try:
        tools = {tool.name: tool for tool in await server.list_tools()}
        for method, fields, suffix in [
            ("pm_release_baselines_list", {"after": 0, "limit": 1}, "?after=0&limit=1"),
            ("pm_release_baseline_current_get", {}, "/current"),
            ("pm_release_baseline_get", {"baseline_id": bid}, "/" + bid),
        ]:
            assert tools[method].annotations.read_only_hint
            status, expected = await http(port, "GET", f"/api/v1/pm/workspaces/{wid}/release-baselines" + suffix, tok=auth)
            assert status == 200, expected
            params = {"workspace_id": wid, **fields}
            assert await d.call_api(method, params, READER) == expected
            assert unpack(await server.call_tool(method, params)) == expected
        assert d.ops.db.total_changes == before and len(d.ops.context["github"].calls) == calls
        replay = await send(d, port, original, only_manage)
        assert replay["operation_id"] == done["operation_id"] and replay["result"] == done["result"]
        assert all(replay[key] == original[key] for key in ("target", "params", "preconditions"))
        assert len(d.ops.context["github"].calls) == calls
        status, cap = await http(port, "GET", "/api/v1/capabilities", tok=auth)
        assert status == 200 and cap["features"]["pm_workspaces"]["release_baselines"] == baselines.capabilities()
        assert not write_frames(mock)
    finally:
        await fleet.close()


async def test_exact_queries_scope_membership_and_malformed_ids(ready):
    d, port, target, binding, _ = ready
    done = await send(d, port, request(d.ops, target, binding))
    bid, wid = done["result"]["baseline_id"], target["workspace_id"]
    prefix = f"/api/v1/pm/workspaces/{wid}/release-baselines"
    auth = token(d, READER.actor, "observe")
    for suffix in ["?after=", "?after=0&after=1", "?limit=", "?limit=0", "?limit=101", "?limit=true",
                   "?after=-1", "?actor=", "?publish=true", "/current?after=0", "/" + bid + "?limit=1"]:
        assert (await http(port, "GET", prefix + suffix, tok=auth))[0] == 422, suffix
    for suffix in ["/pmrb_bad", "/latest", "/" + bid + "/publish"]:
        assert (await http(port, "GET", prefix + suffix, tok=auth))[0] == 404
    for params in [{"limit": True}, {"after": None}, {"after": 2**80}, {"actor": OWNER.actor}]:
        with pytest.raises(OperationError, match="INVALID_PARAMS"):
            await d.call_api("pm_release_baselines_list", {"workspace_id": wid, **params}, READER)
    assert (await http(port, "GET", prefix, tok=token(d, OWNER.actor, "manage")))[0] == 403
    await member(d.ops, {"workspace_id": wid}, READER.actor, None)
    for suffix in ["", "/current", "/" + bid]:
        assert (await http(port, "GET", prefix + suffix, tok=auth))[0] == 404
    assert (await http(port, "GET", "/api/v1/operations/" + done["operation_id"], tok=auth))[0] == 404


async def test_original_source_withdrawal_hides_pending_complete_events_and_replay(ready, monkeypatch):
    d, port, target, binding, project_id = ready
    original = request(d.ops, target, binding)
    original["params"]["rationale"] = "PRIVATE MAINTENANCE SOURCE QUOTE"
    done = await send(d, port, original)
    pending = d.ops.create(OWNER, **request(d.ops, target, binding))[0]
    wid, bid = target["workspace_id"], done["result"]["baseline_id"]
    auth = token(d, READER.actor, "observe")
    monkeypatch.setenv("BATC_TASK_URL", f"http://127.0.0.1:{port}/rpc")
    monkeypatch.setenv("BATC_API_TOKEN", auth)
    server, fleet = mcp_server.build_server(d.fleet.config, read_only=True, principal_only=True)
    # Source revocation occurs before the accepted pending selection can observe.
    d.ops.db.execute("UPDATE projects SET repositories='[]' WHERE project_id=?", (project_id,))
    try:
        for op in (done, pending):
            oid = op["operation_id"]
            assert (await http(port, "GET", "/api/v1/operations/" + oid, tok=auth))[0] == 404
            with pytest.raises(OperationError):
                await d.call_api("op_get", {"operation_id": oid}, READER)
            assert "NOT_FOUND" in await call(server, "operation_get", {"operation_id": oid})
        prefix = f"/api/v1/pm/workspaces/{wid}/release-baselines"
        assert (await http(port, "GET", prefix + "/" + bid, tok=auth))[0] == 404
        for method, params, route, rpc_method in [
            ("pm_release_baselines_list", {"workspace_id": wid}, prefix, "pm_release_baselines_list"),
            ("pm_release_baseline_current_get", {"workspace_id": wid}, prefix + "/current", "pm_release_baseline_current_get"),
            ("operations_list", {"action": baselines.ACTION}, "/api/v1/operations?action=" + baselines.ACTION, "op_list"),
            ("events_list", {"after": 0, "limit": 500}, "/api/v1/events?after=0&limit=500", "api_events"),
        ]:
            status, view = await http(port, "GET", route, tok=auth)
            assert status == 200, view
            for value in [json.dumps(view), json.dumps(await d.call_api(rpc_method, params, READER)), await call(server, method, params)]:
                assert "PRIVATE MAINTENANCE SOURCE QUOTE" not in value
                assert all(op["operation_id"] not in value for op in (done, pending))
                assert bid not in value
        calls = len(d.ops.context["github"].calls)
        status, _ = await http(port, "POST", "/api/v1/operations", tok=token(d, OWNER.actor, "manage"),
            body={key: value for key, value in original.items() if key != "idempotency_key"},
            headers={"Idempotency-Key": original["idempotency_key"]})
        assert status == 404 and len(d.ops.context["github"].calls) == calls
        await settle_operations(d.ops)
    finally:
        await fleet.close()


async def test_waited_post_does_not_leak_source_revoked_during_provider_read(ready):
    d, port, target, binding, project_id = ready
    original = request(d.ops, target, binding)
    original["params"]["rationale"] = "PRIVATE SOURCE IN WAITED ACTION"

    async def revoke(path):
        if path.endswith("releases/41"):
            d.ops.db.execute("UPDATE projects SET repositories='[]' WHERE project_id=?", (project_id,))
    d.ops.context["github"].hook = revoke
    auth = token(d, OWNER.actor, "manage")
    status, result = await http(port, "POST", "/api/v1/operations?wait=3", tok=auth,
        body={key: value for key, value in original.items() if key != "idempotency_key"},
        headers={"Idempotency-Key": original["idempotency_key"]})
    assert status == 404 and "PRIVATE SOURCE IN WAITED ACTION" not in json.dumps(result)
    await settle_operations(d.ops)
    assert d.ops.db.execute("SELECT COUNT(*) FROM pm_release_baselines").fetchone()[0] == 0


async def test_deployment_selection_and_receipt_replay_need_manage_without_observe(make_daemon, gh):
    d = make_daemon()
    target, binding = await deployment_setup(d, gh)
    original_deploy = await deployments.deployed(d, gh)
    body = selection(d.ops, target, binding, original_deploy)
    server = await asyncio.start_server(d._handle, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    who = Principal(OWNER.actor, frozenset({"manage"}))
    try:
        done = await send(d, port, body, who)
        replay = await send(d, port, body, who)
        assert replay["operation_id"] == done["operation_id"]
        assert replay["result"] == done["result"]
        assert replay["target"] == body["target"] and replay["params"] == body["params"]
        doc = baselines.current_baseline(d.ops, OWNER, target["workspace_id"])["baseline"]["document"]
        assert doc["proof"]["source"]["operation_id"] == original_deploy["operation_id"]
    finally:
        server.close()
        await server.wait_closed()


async def test_source_project_update_invalidates_reads_without_rebinding_to_new_repository(ready):
    d, port, target, binding, project_id = ready
    done = await send(d, port, request(d.ops, target, binding))
    await remove_repo(d.ops, project_id)
    status, view = await http(port, "GET", f'/api/v1/pm/workspaces/{target["workspace_id"]}/release-baselines/current',
                              tok=token(d, OWNER.actor, "observe"))
    assert status == 200 and view["visibility_filtered"] and view["baseline"] is None
    assert view["head"] == {"baseline_id": None, "version": done["result"]["version"]}
