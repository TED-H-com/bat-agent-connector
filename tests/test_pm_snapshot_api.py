"""The Brief has one fixed, private snapshot contract across entry points."""
from __future__ import annotations

import pytest

from bat_agent_connector import mcp_server, pm_plans
from bat_agent_connector.operations import OperationError
from tests.test_api_v1 import daemon as api_daemon
from tests.test_api_v1 import http, token, write_frames
from tests.test_api_v1 import served as api_served
from tests.test_pm_api import OTHER, OWNER, seed
from tests.test_pm_evidence_api import capture, prepared
from tests.test_pm_run_api import grant, unpack
from tests.test_work_items import act

daemon = api_daemon
served = api_served


async def snapshot(d, wid):
    return await act(d, OWNER, 'pm.snapshot.create', {'workspace_id': wid}, {'title': 'A fixed project review'},
                     {'expected_plan_version': pm_plans.head(d.ops.db, wid)['version']})


async def test_snapshot_http_rpc_mcp_parity_preserves_fixed_capture_and_unknown_baseline(served, monkeypatch, mock):
    d, port = served
    target, _, binding = await prepared(d)
    first = await capture(d, target, binding)
    op = await snapshot(d, target['workspace_id'])
    wid, sid = target['workspace_id'], op['result']['snapshot_id']
    auth = token(d, OWNER.actor, *OWNER.scopes)
    prefix = f'/api/v1/pm/workspaces/{wid}/snapshots'
    monkeypatch.setenv('BATC_TASK_URL', f'http://127.0.0.1:{port}/rpc')
    monkeypatch.setenv('BATC_API_TOKEN', auth)
    server, fleet = mcp_server.build_server(d.fleet.config, read_only=True, principal_only=True)
    try:
        for method, fields, suffix in [('pm_snapshots_list', {'after': 0, 'limit': 1}, '?after=0&limit=1'),
                                       ('pm_snapshot_get', {'snapshot_id': sid}, '/' + sid),
                                       ('pm_snapshot_latest_get', {}, '/latest')]:
            status, expected = await http(port, 'GET', prefix + suffix, tok=auth)
            assert status == 200
            params = {'workspace_id': wid, **fields}
            assert await d.call_api(method, params.copy(), OWNER) == expected
            assert unpack(await server.call_tool(method, params)) == expected
        original = (await http(port, 'GET', prefix + '/' + sid, tok=auth))[1]['snapshot']
        assert original['brief']['goals']['status'] == 'unavailable'
        assert original['brief']['deviations']['status'] == 'unassessed'
        assert original['brief']['delivery']['status'] == 'quality_unassessed'
        assert first['result']['capture_id'] in str(original['work'])
        await capture(d, target, binding)
        assert (await http(port, 'GET', prefix + '/' + sid, tok=auth))[1]['snapshot'] == original
        assert not write_frames(mock)
    finally:
        await fleet.close()


async def test_snapshot_membership_is_rechecked_and_observing_does_not_create_snapshots(served):
    d, port = served
    workspace, _ = await seed(d)
    wid = workspace['workspace_id']
    auth = token(d, OWNER.actor, *OWNER.scopes)
    prefix = f'/api/v1/pm/workspaces/{wid}/snapshots'
    assert (await http(port, 'GET', prefix + '/latest', tok=auth))[1]['snapshot'] is None
    await snapshot(d, wid)
    outsider = token(d, OTHER.actor, *OTHER.scopes)
    assert (await http(port, 'GET', prefix + '/latest', tok=outsider))[0] == 404
    await grant(d, wid, OTHER.actor, 'reader')
    assert (await http(port, 'GET', prefix + '/latest', tok=outsider))[0] == 200
    with pytest.raises(OperationError, match='FORBIDDEN'):
        d.ops.create(OTHER, action='pm.snapshot.create', target={'workspace_id': wid}, params={},
                     preconditions={'expected_plan_version': 0}, idempotency_key='reader-snapshot')
    await grant(d, wid, OTHER.actor, None)
    assert (await http(port, 'GET', prefix + '/latest', tok=outsider))[0] == 404


@pytest.mark.parametrize('suffix', ['?after=-1', '?after=0&after=1', '?limit=101', '?actor=owner', '/latest?limit=1'])
async def test_snapshot_queries_are_exact_and_bounded(served, suffix):
    d, port = served
    workspace, _ = await seed(d)
    auth = token(d, OWNER.actor, *OWNER.scopes)
    assert (await http(port, 'GET', f"/api/v1/pm/workspaces/{workspace['workspace_id']}/snapshots" + suffix, tok=auth))[0] == 422
