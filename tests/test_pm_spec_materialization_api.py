"""Shared immutable specification readers and original-source event barriers."""
import asyncio
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
    store = d.artifact_store
    reap_started, release_reap = asyncio.Event(), asyncio.Event()
    original_reap = store.reap_best_effort

    async def held_reap(operation_id=None):
        if asyncio.current_task() is store._reap_task:
            reap_started.set()
            await release_reap.wait()
        await original_reap(operation_id)

    # Operation settlement does not include its independently scheduled staging
    # reaper. Hold that real task so this test always covers pending cleanup.
    monkeypatch.setattr(store, 'reap_best_effort', held_reap)
    try:
        done = await materialize(w, original)
        await asyncio.wait_for(reap_started.wait(), 5)
        assert store._reap_task is not None and not store._reap_task.done()
    finally:
        release_reap.set()
        if store._reap_task is not None:
            await store._reap_task
    upload = d.ops.db.execute('SELECT reserved_bytes,released_at FROM artifact_uploads WHERE operation_id=?',
                              (done['operation_id'],)).fetchone()
    assert upload['reserved_bytes'] == 0 and upload['released_at'] is not None
    # The unchanged counter below now covers only the advertised read paths.
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
        status, caps = await http(w['port'], 'GET', '/api/v1/capabilities', tok=token(d, OWNER.actor, 'observe', 'manage'))
        assert status == 200, caps
        assert caps['features']['pm_workspaces']['spec_materialization']['action'] == 'pm.spec.materialize'
        assert not next(a for a in caps['actions'] if a['action']=='pm.spec.materialize')['allowed']
        d.ops.db.execute("DELETE FROM pm_workspace_members WHERE workspace_id=? AND actor=?", (wid, OWNER.actor))
        status, hidden = await http(w['port'], 'GET', root, tok=auth)
        assert status == 404, hidden
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
