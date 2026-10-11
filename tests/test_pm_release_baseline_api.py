"""Release baseline HTTP/RPC/MCP parity and original-source operation privacy."""
import asyncio
import json
from urllib.parse import urlencode

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
from tests.test_pm_release_previews import ChoiceProvider
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


async def test_explicit_source_choices_preview_and_selected_envelope_http_rpc_mcp(ready, monkeypatch):
    d, port, target, binding, _ = ready
    d.ops.context["github"] = ChoiceProvider(d.ops.context["github_config"])
    wid = target["workspace_id"]
    prefix = f"/api/v1/pm/workspaces/{wid}"
    auth = token(d, READER.actor, "observe")
    monkeypatch.setenv("BATC_TASK_URL", f"http://127.0.0.1:{port}/rpc")
    monkeypatch.setenv("BATC_API_TOKEN", auth)
    server, fleet = mcp_server.build_server(d.fleet.config, read_only=True, principal_only=True)
    before = d.ops.db.total_changes
    chosen = {"project_binding_id": binding, "repository": target["repository"]}
    try:
        for method, fields, suffix in [
            ("pm_release_baseline_sources_list", {"after": 0, "limit": 20}, "/release-baseline-sources"),
            ("pm_release_baseline_releases_list", {**chosen, "page": 1, "limit": 20}, "/release-baseline-sources/github-releases"),
            ("pm_release_baseline_deployments_list", {**chosen, "after": 0, "limit": 20}, "/release-baseline-sources/deployments"),
        ]:
            status, expected = await http(port, "GET", prefix + suffix + "?" + urlencode(fields), tok=auth)
            assert status == 200, expected
            assert await d.call_api(method, {"workspace_id": wid, **fields}, READER) == expected
            assert unpack(await server.call_tool(method, {"workspace_id": wid, **fields})) == expected
            if method == "pm_release_baseline_sources_list":
                assert d.ops.context["github"].calls == []
        params = {**chosen, "kind": "github_release", "release_id": 41}
        status, http_preview = await http(port, "GET", prefix + "/release-baseline-preview?" + urlencode(params), tok=auth)
        assert status == 200, http_preview
        rpc_preview = await d.call_api("pm_release_baseline_preview", {"workspace_id": wid, **params}, READER)
        mcp_preview = unpack(await server.call_tool("pm_release_baseline_preview", {"workspace_id": wid, **params}))
        for value in (rpc_preview, mcp_preview):
            assert value["registration"] == http_preview["registration"]
            assert value["proof"]["source"] == http_preview["proof"]["source"]
            assert value["proof"]["selection"] == http_preview["proof"]["selection"]
        assert d.ops.db.total_changes == before
        fixed = http_preview["registration"]
        fixed["params"]["rationale"] = "Owner chooses this exact released version"
        done = await send(d, port, fixed)
        assert done["result"]["version"] == 1
    finally:
        await fleet.close()


async def test_source_choice_and_preview_queries_are_exact_and_bounded(ready):
    d, port, target, binding, _ = ready
    prefix = f'/api/v1/pm/workspaces/{target["workspace_id"]}'
    auth = token(d, OWNER.actor, "observe")
    chosen = urlencode({"project_binding_id": binding, "repository": target["repository"]})
    for suffix in [
        "/release-baseline-sources?limit=21", "/release-baseline-sources?after=",
        "/release-baseline-sources?repository=example/product", "/release-baseline-sources?after=0&after=1",
        "/release-baseline-sources/github-releases?" + chosen + "&page=0",
        "/release-baseline-sources/github-releases?" + chosen + "&after=0",
        "/release-baseline-sources/deployments?" + chosen + "&page=1",
        "/release-baseline-preview?" + chosen + "&kind=github_release&release_id=",
        "/release-baseline-preview?" + chosen + "&kind=github_release&release_id=1&release_id=2",
        "/release-baseline-preview?" + chosen + "&kind=github_release&release_id=9223372036854775808",
        "/release-baseline-preview?" + chosen + "&kind=package_registry",
        "/release-baseline-preview?" + chosen + "&kind=github_release&release_id=41&maintenance_branch=",
        "/release-baseline-preview?" + chosen + "&kind=github_release&release_id=41&channel=stable",
    ]:
        status, result = await http(port, "GET", prefix + suffix, tok=auth)
        assert status == 422, (suffix, result)
    assert d.ops.context["github"].calls == []
