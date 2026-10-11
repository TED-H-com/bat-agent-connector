"""Shared immutable specification readers and original-source event barriers."""
import json

import pytest

from bat_agent_connector import mcp_server
from bat_agent_connector.operations import OperationError
from tests import test_pm_spec_materialization as fixtures
from tests.test_api_v1 import http, token
from tests.test_pm_plan_operations import OWNER
from tests.test_pm_run_api import unpack
from tests.test_pm_spec_materialization import materialize, request

world, pm, ready, fixed = fixtures.world, fixtures.pm, fixtures.ready, fixtures.fixed


async def test_fixed_spec_read_parity_routes_and_original_source_withdrawal(fixed, monkeypatch):
    w, d = fixed, fixed['d']
    original = request(w)
    done = await materialize(w, original)
    wid, ident = w['workspace_id'], done['result']['materialization_id']
    auth = token(d, OWNER.actor, 'observe', 'manage', 'start')
    root = f'/api/v1/pm/workspaces/{wid}/spec-materializations'
    monkeypatch.setenv('BATC_TASK_URL', f"http://127.0.0.1:{w['port']}/rpc")
    monkeypatch.setenv('BATC_API_TOKEN', auth)
    server, fleet = mcp_server.build_server(d.fleet.config, read_only=True, principal_only=True)
    changes = d.ops.db.total_changes
    try:
        discovered = {tool.name: tool for tool in await server.list_tools()}
        for method, params, suffix in [
            ('pm_spec_materializations_list', {'package_id': w['package']['package_id'], 'after': 0, 'limit': 1}, '?package_id='+w['package']['package_id']+'&after=0&limit=1'),
            ('pm_spec_materialization_get', {'materialization_id': ident}, '/'+ident),
        ]:
            assert discovered[method].annotations.read_only_hint
            status, data = await http(w['port'], 'GET', root+suffix, tok=auth)
            assert status == 200, data
            assert await d.call_api(method, {'workspace_id': wid, **params}, OWNER) == data
            assert unpack(await server.call_tool(method, {'workspace_id': wid, **params})) == data
        assert d.ops.db.total_changes == changes
        for suffix in ['?after=', '?after=-1', '?after=9223372036854775808', '?limit=51', '?limit=0',
                       '?package_id=bad', '?package_id=', '?after=0&after=1', '?materialize=true', '/'+ident+'?limit=1']:
            assert (await http(w['port'], 'GET', root+suffix, tok=auth))[0] == 422, suffix
        for suffix in ['/current', '/pmsp_bad', '/'+ident+'/start']:
            assert (await http(w['port'], 'GET', root+suffix, tok=auth))[0] == 404, suffix
        assert (await http(w['port'], 'GET', root+'?after=9223372036854775807', tok=auth))[1]['materializations'] == []
        for invalid in ({'after': 2**63}, {'after': True}, {'package_id': []}, {'limit': 51}):
            with pytest.raises(OperationError) as caught:
                await d.call_api('pm_spec_materializations_list', {'workspace_id': wid, **invalid}, OWNER)
            assert caught.value.code == 'INVALID_PARAMS'
        assert (await http(w['port'], 'GET', root, tok=token(d, 'outsider', 'observe')))[0] == 404
        manage = token(d, OWNER.actor, 'manage')
        assert (await http(w['port'], 'GET', root, tok=manage))[0] == 403
        caps = (await http(w['port'], 'GET', '/api/v1/capabilities', tok=manage))[1]
        assert caps['features']['pm_workspaces']['spec_materialization']['action'] == 'pm.spec.materialize'
        assert not next(a for a in caps['actions'] if a['action']=='pm.spec.materialize')['allowed']
        project_id = w['package']['document']['context']['work_ref']['project_id']
        d.ops.db.execute("UPDATE projects SET repositories='[]' WHERE project_id=?", (project_id,))
        status, hidden = await http(w['port'], 'GET', root, tok=auth)
        assert status == 200 and hidden['materializations'] == [] and hidden['source_visibility_filtered']
        assert ident not in json.dumps(hidden)
        for path in [root+'/'+ident, '/api/v1/operations/'+done['operation_id'],
                     '/api/v1/artifacts/'+done['result']['artifact_ref']['artifact_id']+'/revisions/1']:
            status, data = await http(w['port'], 'GET', path, tok=auth)
            assert status == 404, (path, data)
        status, events = await http(w['port'], 'GET', '/api/v1/events?after=0&limit=500', tok=auth)
        assert status == 200 and ident not in json.dumps(events) and done['operation_id'] not in json.dumps(events)
        with pytest.raises(OperationError):
            d.ops.create(OWNER, **original)
    finally:
        await fleet.close()
