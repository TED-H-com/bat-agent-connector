"""Workspace work/evidence read parity and privacy across HTTP, RPC and MCP."""
from __future__ import annotations

import json

import pytest

from bat_agent_connector import mcp_server, pm_evidence, pm_workspace, work_items
from bat_agent_connector.operations import OperationError
from tests.operation_helpers import settle_operations
from tests.test_api_v1 import daemon as api_daemon
from tests.test_api_v1 import http, token, write_frames
from tests.test_api_v1 import served as api_served
from tests.test_pm_api import OTHER, OWNER, seed
from tests.test_pm_run_api import grant, unpack
from tests.test_work_items import act, item, project

daemon = api_daemon
served = api_served


async def prepared(d):
    workspace, _ = await seed(d)
    wid = workspace['workspace_id']
    pid = await project(d)
    work = await item(d, pid, 'Deliver a useful feature')
    await act(d, OWNER, 'pm.project.bind', {'workspace_id': wid, 'project_id': pid},
              {'rationale': 'Explicit project association'},
              {'expected_workspace_version': pm_workspace.get_workspace(d.ops.db, OWNER, wid)['version'],
               'expected_project_version': work_items._get_project(d.ops.db, pid)['version']})
    target = {'workspace_id': wid, 'work_item_id': work}
    binding = await act(d, OWNER, 'pm.work.bind', target, {'rationale': 'Track this deliverable'},
                        {'expected_binding_id': None, 'expected_work_version': work_items._get_item(d.ops.db, work)['version']})
    return target, pid, binding['result']['binding_id']


async def capture(d, target, binding):
    return await act(d, OWNER, 'pm.evidence.capture', target, {}, {'expected_binding_id': binding})


async def test_shared_evidence_reads_are_equal_and_capture_is_not_delivery(served, monkeypatch, mock):
    d, port = served
    target, pid, binding = await prepared(d)
    op = await capture(d, target, binding)
    wid, work = target['workspace_id'], target['work_item_id']
    cid = op['result']['capture_id']
    eid = pm_evidence.latest_capture(d.ops, OWNER, wid, work)['capture']['evidence_ids'][0]
    auth = token(d, OWNER.actor, *OWNER.scopes)
    monkeypatch.setenv('BATC_TASK_URL', f'http://127.0.0.1:{port}/rpc')
    monkeypatch.setenv('BATC_API_TOKEN', auth)
    server, fleet = mcp_server.build_server(d.fleet.config, read_only=True, principal_only=True)
    prefix = f'/api/v1/pm/workspaces/{wid}'
    cases = [
        ('pm_projects_list', {'after': 0, 'limit': 1}, '/projects?after=0&limit=1'),
        ('pm_work_list', {'project_id': pid, 'after': 0, 'limit': 1}, f'/work?project_id={pid}&after=0&limit=1'),
        ('pm_evidence_list', {'work_item_id': work, 'after': 0, 'limit': 1}, f'/evidence?work_item_id={work}&after=0&limit=1'),
        ('pm_evidence_get', {'evidence_id': eid}, f'/evidence/{eid}'),
        ('pm_capture_get', {'capture_id': cid}, f'/captures/{cid}'),
        ('pm_capture_latest_get', {'work_item_id': work}, f'/work/{work}/capture'),
    ]
    try:
        for method, fields, path in cases:
            status, expected = await http(port, 'GET', prefix + path, tok=auth)
            assert status == 200
            params = {'workspace_id': wid, **fields}
            assert await d.call_api(method, params.copy(), OWNER) == expected
            assert unpack(await server.call_tool(method, params)) == expected
        assert op['result']['dod'] == 'unassessed' and not op['result']['execution_authorized']
        assert d.ops.db.execute('SELECT COUNT(*) FROM tasks').fetchone()[0] == 0
        assert not write_frames(mock)
    finally:
        await fleet.close()


async def test_capture_replay_and_private_global_feed(served):
    d, port = served
    target, _, binding = await prepared(d)
    auth = token(d, OWNER.actor, *OWNER.scopes)
    request = {'action': 'pm.evidence.capture', 'target': target, 'params': {},
               'preconditions': {'expected_binding_id': binding}}
    status, first = await http(port, 'POST', '/api/v1/operations', tok=auth, body=request,
                               headers={'Idempotency-Key': 'capture-once'})
    assert status == 202
    await settle_operations(d.ops)
    status, second = await http(port, 'POST', '/api/v1/operations', tok=auth, body=request,
                                headers={'Idempotency-Key': 'capture-once'})
    assert status == 200 and first['operation']['operation_id'] == second['operation']['operation_id']
    assert d.ops.db.execute('SELECT COUNT(*) FROM pm_evidence_captures').fetchone()[0] == 1
    outsider = token(d, OTHER.actor, *OTHER.scopes)
    cid = second['operation']['result']['capture_id']
    prefix = f"/api/v1/pm/workspaces/{target['workspace_id']}"
    for suffix in ('/projects', '/work', '/evidence', '/captures/' + cid, f"/work/{target['work_item_id']}/capture"):
        assert (await http(port, 'GET', prefix + suffix, tok=outsider))[0] == 404
    for path in ('/api/v1/operations', '/api/v1/events', '/api/v1/bootstrap'):
        status, body = await http(port, 'GET', path, tok=outsider)
        assert status == 200
        assert cid not in json.dumps(body) and binding not in json.dumps(body)
    await grant(d, target['workspace_id'], OTHER.actor, 'reader')
    assert (await http(port, 'GET', prefix + '/evidence', tok=outsider))[0] == 200
    with pytest.raises(OperationError, match='FORBIDDEN'):
        d.ops.create(OTHER, **request, idempotency_key='reader-capture')
    await grant(d, target['workspace_id'], OTHER.actor, None)
    assert (await http(port, 'GET', prefix + '/evidence', tok=outsider))[0] == 404


@pytest.mark.parametrize('suffix', ['/projects?after=-1', '/work?project_id=unknown', '/work?after=0&after=1',
                                    '/evidence?work_item_id=unknown', '/evidence?limit=101', '/evidence?actor=owner'])
async def test_evidence_http_rejects_ambiguous_or_unbounded_queries(served, suffix):
    d, port = served
    workspace, _ = await seed(d)
    auth = token(d, OWNER.actor, *OWNER.scopes)
    assert (await http(port, 'GET', f"/api/v1/pm/workspaces/{workspace['workspace_id']}" + suffix, tok=auth))[0] == 422


@pytest.mark.parametrize('params', [{'after': True}, {'after': '0'}, {'limit': False}, {'actor': 'owner'}])
async def test_evidence_rpc_preserves_strict_types(daemon, params):
    workspace, _ = await seed(daemon)
    with pytest.raises(OperationError, match='INVALID_PARAMS'):
        await daemon.call_api('pm_evidence_list', {'workspace_id': workspace['workspace_id'], **params}, OWNER)
