"""Actual shared entrypoints retain original batch authority and read-only bounds."""
import json

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from bat_agent_connector import api_auth, mcp_server
from bat_agent_connector import pm_work_batches as batches
from tests import test_pm_package_handoff as fixtures
from tests import test_pm_work_batches as helpers
from tests.test_api_v1 import http, token
from tests.test_pm_plan_operations import OWNER
from tests.test_pm_run_api import unpack

world, pm, ready, fixed = fixtures.world, fixtures.pm, fixtures.ready, fixtures.fixed


async def test_shared_batch_original_operation_reads_and_source_withdrawal(fixed, monkeypatch, mock):
    w, d = fixed, fixed['d']
    api_auth.issue(d.ops.db, OWNER.actor, sorted(batches.REQUIRED))
    original = helpers.envelope(w, [await helpers.selected(w, 'first')])
    parent = d.ops.create(OWNER, **original)[0]
    parent = await helpers.advance(w, parent)
    bid, wid = 'pmbt_' + parent['operation_id'][3:], w['workspace_id']
    auth = token(d, OWNER.actor, 'observe', 'manage', 'start')
    root = f'/api/v1/pm/workspaces/{wid}/work-batches'
    monkeypatch.setenv('BATC_TASK_URL', f"http://127.0.0.1:{w['port']}/rpc")
    monkeypatch.setenv('BATC_API_TOKEN', auth)
    server, fleet = mcp_server.build_server(d.fleet.config, read_only=True, principal_only=True)
    changes, writes = d.ops.db.total_changes, list(mock.channels())
    try:
        discovered = {tool.name: tool for tool in await server.list_tools()}
        for method, params, suffix in [
            ('pm_work_batches_list', {'after': 0, 'limit': 1}, '?after=0&limit=1'),
            ('pm_work_batch_get', {'batch_id': bid}, '/'+bid),
        ]:
            assert discovered[method].annotations.read_only_hint
            status, data = await http(w['port'], 'GET', root+suffix, tok=auth)
            assert status == 200, data
            assert await d.call_api(method, {'workspace_id': wid, **params}, OWNER) == data
            assert unpack(await server.call_tool(method, {'workspace_id': wid, **params})) == data
        assert d.ops.db.total_changes == changes and mock.channels() == writes
        for suffix in ['?after=', '?after=-1', '?after=9223372036854775808', '?limit=101',
                       '?after=0&after=1', '?start=true', '/'+bid+'?limit=1']:
            assert (await http(w['port'], 'GET', root+suffix, tok=auth))[0] == 422, suffix
        assert (await http(w['port'], 'GET', root+'/current', tok=auth))[0] == 404
        assert (await http(w['port'], 'GET', root, tok=token(d, OWNER.actor, 'manage', 'start')))[0] == 403
        status, caps = await http(w['port'], 'GET', '/api/v1/capabilities', tok=token(d, OWNER.actor, 'observe', 'manage'))
        assert status == 200 and caps['features']['pm_workspaces']['work_batches'] == batches.capabilities(d.ops)
        assert not any(a['allowed'] for a in caps['actions'] if a['action'].startswith('pm.work_batch.'))
        project = w['package']['document']['context']['work_ref']['project_id']
        d.ops.db.execute("UPDATE projects SET repositories='[]' WHERE project_id=?", (project,))
        status, page = await http(w['port'], 'GET', root, tok=auth)
        assert status == 200 and page['batches'] == [] and page['visibility_filtered']
        assert (await http(w['port'], 'GET', root+'/'+bid, tok=auth))[0] == 404
        assert (await http(w['port'], 'GET', '/api/v1/operations/'+parent['operation_id'], tok=auth))[0] == 404
        status, events = await http(w['port'], 'GET', '/api/v1/events?after=0&limit=500', tok=auth)
        assert status == 200 and bid not in json.dumps(events)
        with pytest.raises(ToolError, match='NOT_FOUND'):
            await server.call_tool('pm_work_batch_get', {'workspace_id': wid, 'batch_id': bid})
    finally:
        await fleet.close()
