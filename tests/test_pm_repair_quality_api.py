"""Real shared routes use production source hooks for repairs and quality records."""
import json

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from bat_agent_connector import mcp_server, pm_access
from bat_agent_connector import pm_maintenance_repairs as repairs
from bat_agent_connector import pm_quality_reviews as quality
from tests import test_pm_maintenance_repairs as repairtests
from tests import test_pm_quality_reviews as qualitytests
from tests.test_api_v1 import http, token
from tests.test_pm_plan_operations import OWNER
from tests.test_pm_run_api import unpack

world, pm, ready, fixed = repairtests.world, repairtests.pm, repairtests.ready, repairtests.fixed
reviewed = qualitytests.reviewed


@pytest.fixture
async def repair_ready(fixed, monkeypatch):
    # API initialization supplies the real hooks; exercise those without the
    # isolated domain fixture's stand-ins.
    monkeypatch.setattr(repairtests, 'shared_hooks', lambda _: None)
    return await repairtests.repair_ready.__wrapped__(fixed, monkeypatch)


async def read_parity(w, monkeypatch, auth, routes):
    d, wid = w['d'], w['workspace_id']
    monkeypatch.setenv('BATC_TASK_URL', f"http://127.0.0.1:{w['port']}/rpc")
    monkeypatch.setenv('BATC_API_TOKEN', auth)
    server, fleet = mcp_server.build_server(d.fleet.config, read_only=True, principal_only=True)
    before = d.ops.db.total_changes
    try:
        tools = {tool.name: tool for tool in await server.list_tools()}
        for method, params, path in routes:
            assert tools[method].annotations.read_only_hint
            status, data = await http(w['port'], 'GET', '/api/v1/pm/workspaces/'+wid+path, tok=auth)
            assert status == 200, data
            assert await d.call_api(method, {'workspace_id': wid, **params}, OWNER) == data
            assert unpack(await server.call_tool(method, {'workspace_id': wid, **params})) == data
        assert d.ops.db.total_changes == before
        d.ops.db.execute('DELETE FROM pm_workspace_members WHERE workspace_id=? AND actor=?', (wid, OWNER.actor))
        for method, params, path in routes:
            assert (await http(w['port'], 'GET', '/api/v1/pm/workspaces/'+wid+path, tok=auth))[0] == 404
            with pytest.raises(ToolError, match='NOT_FOUND'):
                await server.call_tool(method, {'workspace_id': wid, **params})
    finally:
        await fleet.close()


async def test_repair_real_source_roots_routes_scopes_and_original_child(repair_ready, monkeypatch):
    w, d = repair_ready, repair_ready['d']
    parent = d.ops.create(OWNER, **repairtests.request(w))[0]
    parent = await repairtests.advance(w, parent)
    rid, wid = 'pmrp_'+parent['operation_id'][3:], w['workspace_id']
    child = repairs._child(d.ops, parent)
    assert child is not None and child['status'] == 'succeeded'
    assert parent['operation_id'] in repairtests.pm_source_lineage.roots(d.ops.db, 'operation', child['operation_id'])
    auth = token(d, OWNER.actor, 'observe', 'manage', 'start')
    for scopes in [('observe', 'manage'), ('observe', 'start'), ('manage', 'start')]:
        status, caps = await http(w['port'], 'GET', '/api/v1/capabilities', tok=token(d, OWNER.actor, *scopes))
        if 'observe' not in scopes:
            assert status == 403
            continue
        assert status == 200
        assert not next(a['allowed'] for a in caps['actions'] if a['action'] == repairs.ACTION)
    for suffix in ['?after=9223372036854775808', '?limit=51', '?start=true', '/'+rid+'?limit=1']:
        assert (await http(w['port'], 'GET', f'/api/v1/pm/workspaces/{wid}/maintenance-repairs'+suffix, tok=auth))[0] == 422
    await read_parity(w, monkeypatch, auth, [
        ('pm_maintenance_repairs_list', {'after': 0, 'limit': 1}, '/maintenance-repairs?after=0&limit=1'),
        ('pm_maintenance_repair_current_get', {}, '/maintenance-repairs/current'),
        ('pm_maintenance_repair_get', {'repair_id': rid}, '/maintenance-repairs/'+rid)])
    assert not pm_access.visible(d.ops.db, OWNER, child)
    assert (await http(w['port'], 'GET', '/api/v1/operations/'+parent['operation_id'], tok=auth))[0] == 404


async def test_quality_actual_routes_and_event_source_withdrawal(reviewed, monkeypatch):
    w, d = reviewed, reviewed['d']
    row = await qualitytests.new_review(w)
    op = await qualitytests.execute(w, qualitytests.assessment(w, row), actor=qualitytests.REVIEWER)
    wid, rid = w['workspace_id'], row['review_id']
    assert quality.get_review(d.ops, OWNER, wid, rid)['review']['qualified']
    auth = token(d, OWNER.actor, 'observe', 'manage')
    status, caps = await http(w['port'], 'GET', '/api/v1/capabilities', tok=token(d, OWNER.actor, 'manage'))
    assert status == 403
    status, caps = await http(w['port'], 'GET', '/api/v1/capabilities', tok=token(d, OWNER.actor, 'observe'))
    assert status == 200 and not any(a['allowed'] for a in caps['actions'] if a['action'] in quality.ACTIONS)
    status, caps = await http(w['port'], 'GET', '/api/v1/capabilities', tok=auth)
    assert status == 200 and all(a['allowed'] for a in caps['actions'] if a['action'] in quality.ACTIONS)
    for suffix in ['?after=-1', '?after=9223372036854775808', '?limit=101', '?qualified=true', '/'+rid+'?limit=1']:
        assert (await http(w['port'], 'GET', f'/api/v1/pm/workspaces/{wid}/quality-reviews'+suffix, tok=auth))[0] == 422
    await read_parity(w, monkeypatch, auth, [
        ('pm_quality_reviews_list', {'after': 0, 'limit': 1}, '/quality-reviews?after=0&limit=1'),
        ('pm_quality_review_get', {'review_id': rid}, '/quality-reviews/'+rid),
        ('pm_work_quality_get', {'work_item_id': w['package']['work_item_id']}, '/work/'+w['package']['work_item_id']+'/quality')])
    assert not pm_access.visible(d.ops.db, OWNER, op)
    status, events = await http(w['port'], 'GET', '/api/v1/events?after=0&limit=500', tok=auth)
    assert status == 200 and rid not in json.dumps(events)
