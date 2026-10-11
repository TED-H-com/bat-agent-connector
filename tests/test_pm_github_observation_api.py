"""Shared provider observation reads never refresh or weaken original authority."""
import json

import pytest

from bat_agent_connector import mcp_server
from bat_agent_connector import pm_github_observation as observation
from bat_agent_connector import pm_github_sources as provider
from bat_agent_connector.api_auth import Principal
from bat_agent_connector.operations import OperationError
from tests.test_api_v1 import http, token, write_frames
from tests.test_mcp_principal import call
from tests.test_pm_github_observation import ReadProvider, request
from tests.test_pm_governance_api import send
from tests.test_pm_maintenance_policy import policy
from tests.test_pm_maintenance_policy import request as policy_request
from tests.test_pm_maintenance_policy_api import (
    daemon,  # noqa: F401 - fixture dependencies
    release_ready,  # noqa: F401 - fixture dependencies
    served,  # noqa: F401 - fixture dependencies
)
from tests.test_pm_maintenance_policy_api import ready as policy_ready
from tests.test_pm_release_baselines import OWNER, READER
from tests.test_pm_run_api import unpack

baseline_ready = policy_ready


@pytest.fixture
async def ready(baseline_ready):
    d, port, wid, project_id = baseline_ready
    d.ops.context["github"] = ReadProvider(d.ops.context["github_config"])
    settings = policy()
    settings["observation"]["github_profile"] = provider.PROFILE
    await send(d, port, policy_request(d.ops, wid, document=settings))
    return d, port, wid, project_id


async def test_http_rpc_mcp_read_parity_and_original_key_recovery_never_repeat_provider_gets(ready, monkeypatch, mock):
    d, port, wid, _ = ready
    original = request(d.ops, {"workspace_id": wid})
    manage = Principal(OWNER.actor, frozenset({"manage"}))
    done = await send(d, port, original, manage)
    oid = done["result"]["observation_id"]
    auth = token(d, READER.actor, "observe")
    monkeypatch.setenv("BATC_TASK_URL", f"http://127.0.0.1:{port}/rpc")
    monkeypatch.setenv("BATC_API_TOKEN", auth)
    server, fleet = mcp_server.build_server(d.fleet.config, read_only=True, principal_only=True)
    changes, reads = d.ops.db.total_changes, list(d.ops.context["github"].reads)
    assert len(reads) == 16
    try:
        discovered = {tool.name: tool for tool in await server.list_tools()}
        for method, fields, suffix in [
            ("pm_github_observations_list", {"after": 0, "limit": 1}, "?after=0&limit=1"),
            ("pm_github_observation_current_get", {}, "/current"),
            ("pm_github_observation_get", {"observation_id": oid}, "/" + oid),
        ]:
            assert discovered[method].annotations.read_only_hint
            status, expected = await http(port, "GET", f"/api/v1/pm/workspaces/{wid}/github-observations" + suffix, tok=auth)
            assert status == 200, expected
            params = {"workspace_id": wid, **fields}
            assert await d.call_api(method, params, READER) == expected
            assert unpack(await server.call_tool(method, params)) == expected
        assert d.ops.db.total_changes == changes
        replay = await send(d, port, original, manage)
        assert replay["operation_id"] == done["operation_id"] and replay["result"] == done["result"]
        assert all(replay[key] == original[key] for key in ("target", "params", "preconditions"))
        assert d.ops.context["github"].reads == reads
        assert d.ops.db.execute("SELECT COUNT(*) FROM pm_github_observations").fetchone()[0] == 1
        status, caps = await http(port, "GET", "/api/v1/capabilities", tok=auth)
        assert status == 200 and caps["features"]["pm_github_observation"] == observation.capabilities()
        assert any(action["action"] == observation.ACTION and action["scope"] == "manage" for action in caps["actions"])
        assert not write_frames(mock)
    finally:
        await fleet.close()


async def test_exact_queries_scopes_membership_and_signed_64_bit_cursors(ready):
    d, port, wid, _ = ready
    done = await send(d, port, request(d.ops, {"workspace_id": wid}))
    oid = done["result"]["observation_id"]
    root = f"/api/v1/pm/workspaces/{wid}/github-observations"
    auth = token(d, READER.actor, "observe")
    for suffix in ["?after=", "?after=-1", "?after=0&after=1", "?limit=0", "?limit=101", "?actor=",
                   "?after=9223372036854775808", "?refresh=true", "/current?limit=1", "/" + oid + "?limit=1"]:
        assert (await http(port, "GET", root + suffix, tok=auth))[0] == 422, suffix
    for cursor in (10**9, 2**63 - 1):
        status, page = await http(port, "GET", root + f"?after={cursor}", tok=auth)
        assert status == 200 and page["observations"] == []
        assert page == await d.call_api("pm_github_observations_list", {"workspace_id": wid, "after": cursor}, READER)
    for suffix in ["/latest", "/pmgh_bad", "/" + oid + "/refresh"]:
        assert (await http(port, "GET", root + suffix, tok=auth))[0] == 404
    assert (await http(port, "GET", root, tok=token(d, OWNER.actor, "manage")))[0] == 403
    assert (await http(port, "GET", root, tok=token(d, "stranger", "observe")))[0] == 404
    for invalid in [{"after": True}, {"after": 2**63}, {"observation_id": oid}, {"refresh": True}]:
        with pytest.raises(OperationError, match="INVALID_PARAMS"):
            await d.call_api("pm_github_observations_list", {"workspace_id": wid, **invalid}, READER)


async def test_source_withdrawal_filters_pending_complete_events_and_original_replay(ready, monkeypatch):
    d, port, wid, project_id = ready
    original = request(d.ops, {"workspace_id": wid})
    done = await send(d, port, original)
    pending = d.ops.create(OWNER, **request(d.ops, {"workspace_id": wid}))[0]
    oid = done["result"]["observation_id"]
    auth = token(d, READER.actor, "observe")
    monkeypatch.setenv("BATC_TASK_URL", f"http://127.0.0.1:{port}/rpc")
    monkeypatch.setenv("BATC_API_TOKEN", auth)
    server, fleet = mcp_server.build_server(d.fleet.config, read_only=True, principal_only=True)
    reads = list(d.ops.context["github"].reads)
    d.ops.db.execute("UPDATE projects SET repositories='[]' WHERE project_id=?", (project_id,))
    try:
        for op in (done, pending):
            opid = op["operation_id"]
            assert (await http(port, "GET", "/api/v1/operations/" + opid, tok=auth))[0] == 404
            with pytest.raises(OperationError):
                await d.call_api("op_get", {"operation_id": opid}, READER)
            assert "NOT_FOUND" in await call(server, "operation_get", {"operation_id": opid})
        root = f"/api/v1/pm/workspaces/{wid}/github-observations"
        assert (await http(port, "GET", root + "/" + oid, tok=auth))[0] == 404
        for method, params, route, rpc in [
            ("pm_github_observations_list", {"workspace_id": wid}, root, "pm_github_observations_list"),
            ("pm_github_observation_current_get", {"workspace_id": wid}, root + "/current", "pm_github_observation_current_get"),
            ("operations_list", {"action": observation.ACTION}, "/api/v1/operations?action=" + observation.ACTION, "op_list"),
            ("events_list", {"after": 0, "limit": 500}, "/api/v1/events?after=0&limit=500", "api_events"),
        ]:
            status, view = await http(port, "GET", route, tok=auth)
            assert status == 200, view
            if "visibility_filtered" in view:
                assert view["visibility_filtered"] is True
            for value in [json.dumps(view), json.dumps(await d.call_api(rpc, params, READER)), await call(server, method, params)]:
                assert oid not in value and all(op["operation_id"] not in value for op in (done, pending))
        status, _ = await http(port, "POST", "/api/v1/operations", tok=token(d, OWNER.actor, "manage"),
            body={k: v for k, v in original.items() if k != "idempotency_key"},
            headers={"Idempotency-Key": original["idempotency_key"]})
        assert status == 404 and d.ops.context["github"].reads == reads
    finally:
        await fleet.close()
