"""Configured findings share saved HTTP/RPC/MCP reads without provider refresh."""
import pytest

from bat_agent_connector import mcp_server
from tests import test_pm_github_observation_api as fixtures
from tests.test_api_v1 import http, token
from tests.test_pm_github_observation import request
from tests.test_pm_governance_api import send
from tests.test_pm_maintenance_findings import Provider
from tests.test_pm_maintenance_policy import policy
from tests.test_pm_maintenance_policy import request as policy_request
from tests.test_pm_release_baselines import READER
from tests.test_pm_run_api import unpack

daemon, served, release_ready = fixtures.daemon, fixtures.served, fixtures.release_ready
baseline_ready = fixtures.baseline_ready


@pytest.fixture
async def ready(baseline_ready):
    d, port, wid, project = baseline_ready
    gh = Provider(d.ops.context['github_config'])
    d.ops.context['github'] = gh
    settings = policy()
    settings['observation'].update(github_profile='github_open_prs_and_actions_v1',
        maintenance_sources={'issues': {'labels_all': ['bug']}, 'dependencies': None})
    await send(d, port, policy_request(d.ops, wid, document=settings))
    gh.issues = [{'id': 501, 'number': 50, 'state': 'open', 'labels': [{'name': 'bug'}]}]
    await send(d, port, request(d.ops, {'workspace_id': wid}))
    return d, port, wid, project


async def test_exact_saved_finding_projection_and_source_withdrawal(ready, monkeypatch):
    d, port, wid, project = ready
    auth = token(d, READER.actor, 'observe')
    monkeypatch.setenv('BATC_TASK_URL', f'http://127.0.0.1:{port}/rpc')
    monkeypatch.setenv('BATC_API_TOKEN', auth)
    server, fleet = mcp_server.build_server(d.fleet.config, read_only=True, principal_only=True)
    path = f'/api/v1/pm/workspaces/{wid}/maintenance-findings/current'
    changes, reads = d.ops.db.total_changes, list(d.ops.context['github'].reads)
    try:
        status, expected = await http(port, 'GET', path, tok=auth)
        assert status == 200, expected
        assert not expected['automatic_repair_authorized']
        issue = expected['sources']['issues']['findings'][0]
        assert issue['native']['id'] == 501 and issue['native']['number'] == 50
        assert expected['sources']['dependabot_alerts']['status'] == 'disabled'
        method = 'pm_maintenance_findings_current_get'
        tools = {tool.name: tool for tool in await server.list_tools()}
        assert tools[method].annotations.read_only_hint
        assert await d.call_api(method, {'workspace_id': wid}, READER) == expected
        assert unpack(await server.call_tool(method, {'workspace_id': wid})) == expected
        assert d.ops.db.total_changes == changes and d.ops.context['github'].reads == reads
        for query in ['?refresh=true', '?limit=1', '?observation_id=x']:
            assert (await http(port, 'GET', path+query, tok=auth))[0] == 422
        assert (await http(port, 'GET', path, tok=token(d, READER.actor, 'manage')))[0] == 403
        assert (await http(port, 'GET', path, tok=token(d, 'stranger', 'observe')))[0] == 404
        d.ops.db.execute("UPDATE projects SET repositories='[]' WHERE project_id=?", (project,))
        status, hidden = await http(port, 'GET', path, tok=auth)
        assert status == 404, hidden
    finally:
        await fleet.close()
