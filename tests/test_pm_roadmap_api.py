"""Roadmap scheduling uses the same bounded private reads across every entry point."""
from __future__ import annotations

import pytest

from bat_agent_connector import mcp_server, pm_roadmap
from bat_agent_connector.operations import OperationError
from tests.test_api_v1 import daemon as api_daemon
from tests.test_api_v1 import http, token, write_frames
from tests.test_api_v1 import served as api_served
from tests.test_pm_api import OTHER, OWNER, seed
from tests.test_pm_run_api import grant, unpack
from tests.test_work_items import act

daemon, served = api_daemon, api_served


async def baseline(d, wid):
    return await act(d, OWNER, 'pm.roadmap.baseline.set', {'workspace_id': wid},
                     {'title': 'Unscheduled roadmap', 'rationale': 'No delivery dates are committed yet', 'milestones': []},
                     {'expected_roadmap_version': pm_roadmap.head(d.ops.db, wid)['version'], 'expected_plan_version': 0})


async def test_roadmap_http_rpc_mcp_parity_and_original_baseline_remains_fixed(served, monkeypatch, mock):
    d, port = served
    workspace, _ = await seed(d)
    wid = workspace['workspace_id']
    op = await baseline(d, wid)
    rid = op['result']['revision_id']
    auth = token(d, OWNER.actor, *OWNER.scopes)
    prefix = f'/api/v1/pm/workspaces/{wid}/roadmap'
    monkeypatch.setenv('BATC_TASK_URL', f'http://127.0.0.1:{port}/rpc')
    monkeypatch.setenv('BATC_API_TOKEN', auth)
    server, fleet = mcp_server.build_server(d.fleet.config, read_only=True, principal_only=True)
    try:
        for method, fields, suffix in [('pm_roadmap_get', {}, ''),
                ('pm_roadmap_revisions_list', {'kind': 'baseline', 'after': 0, 'limit': 1}, '/revisions?kind=baseline&after=0&limit=1'),
                ('pm_roadmap_revision_get', {'revision_id': rid}, '/revisions/' + rid)]:
            status, expected = await http(port, 'GET', prefix + suffix, tok=auth)
            assert status == 200
            params = {'workspace_id': wid, **fields}
            assert await d.call_api(method, params.copy(), OWNER) == expected
            assert unpack(await server.call_tool(method, params)) == expected
        original = (await http(port, 'GET', prefix + '/revisions/' + rid, tok=auth))[1]
        await baseline(d, wid)
        assert (await http(port, 'GET', prefix + '/revisions/' + rid, tok=auth))[1] == original
        assert original['revision']['document']['milestones'] == []
        assert not original['revision']['document']['execution_authorized'] and not write_frames(mock)
    finally:
        await fleet.close()


async def test_roadmap_membership_is_current_and_reads_do_not_create_baselines(served):
    d, port = served
    workspace, _ = await seed(d)
    wid = workspace['workspace_id']
    auth = token(d, OWNER.actor, *OWNER.scopes)
    prefix = f'/api/v1/pm/workspaces/{wid}/roadmap'
    assert (await http(port, 'GET', prefix, tok=auth))[1]['baseline'] is None
    await baseline(d, wid)
    outsider = token(d, OTHER.actor, *OTHER.scopes)
    assert (await http(port, 'GET', prefix, tok=outsider))[0] == 404
    await grant(d, wid, OTHER.actor, 'reader')
    assert (await http(port, 'GET', prefix, tok=outsider))[0] == 200
    with pytest.raises(OperationError, match='FORBIDDEN'):
        d.ops.create(OTHER, action='pm.roadmap.baseline.set', target={'workspace_id': wid},
                     params={'milestones': [], 'rationale': 'Unauthorized change'},
                     preconditions={'expected_roadmap_version': 1, 'expected_plan_version': 0}, idempotency_key='reader-baseline')
    await grant(d, wid, OTHER.actor, None)
    assert (await http(port, 'GET', prefix, tok=outsider))[0] == 404


@pytest.mark.parametrize('suffix', ['?kind=baseline', '/revisions?kind=unknown', '/revisions?kind=baseline&kind=forecast',
                                    '/revisions?after=-1', '/revisions?after=0&after=1', '/revisions?limit=101', '/revisions?actor=owner'])
async def test_roadmap_queries_are_exact_and_bounded(served, suffix):
    d, port = served
    workspace, _ = await seed(d)
    auth = token(d, OWNER.actor, *OWNER.scopes)
    assert (await http(port, 'GET', f"/api/v1/pm/workspaces/{workspace['workspace_id']}/roadmap" + suffix, tok=auth))[0] == 422
