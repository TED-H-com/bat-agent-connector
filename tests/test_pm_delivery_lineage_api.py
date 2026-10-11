"""Shared read parity, source withdrawal and complete delivery response bounds."""
import json

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from bat_agent_connector import mcp_server, pm_delivery_lineage
from bat_agent_connector.operations import OperationError
from tests import test_pm_delivery_lineage as fixtures
from tests.test_api_v1 import http, token
from tests.test_pm_delivery_lineage import DELIVERER, apply, preview, revoke, settle
from tests.test_pm_run_api import unpack

world, pm, ready, fixed = fixtures.world, fixtures.pm, fixtures.ready, fixtures.fixed
delivery_ready = fixtures.delivery_ready


async def test_delivery_http_rpc_mcp_preserve_original_sources_without_new_effects(delivery_ready, monkeypatch):
    w, d = delivery_ready, delivery_ready['d']
    pv = await preview(w)
    done = await settle(w, apply(w, pv))
    assert done['status'] == 'succeeded', done
    wid, work = w['workspace_id'], w['target']['work_item_id']
    root = f'/api/v1/pm/workspaces/{wid}/work/{work}/deliveries'
    auth = token(d, DELIVERER.actor, 'observe', 'manage', 'integrate', 'merge', 'deploy')
    monkeypatch.setenv('BATC_TASK_URL', f"http://127.0.0.1:{w['port']}/rpc")
    monkeypatch.setenv('BATC_API_TOKEN', auth)
    server, fleet = mcp_server.build_server(d.fleet.config, read_only=True, principal_only=True)
    before = d.ops.db.total_changes, len(w['gh'].requests), len(w['runner'].scripts)
    try:
        discovered = {tool.name: tool for tool in await server.list_tools()}
        assert discovered['pm_work_deliveries_list'].annotations.read_only_hint
        status, data = await http(w['port'], 'GET', root+'?after=0&limit=1', tok=auth)
        assert status == 200, data
        params = dict(workspace_id=wid, work_item_id=work, after=0, limit=1)
        assert await d.call_api('pm_work_deliveries_list', params, DELIVERER) == data
        assert unpack(await server.call_tool('pm_work_deliveries_list', params)) == data
        assert data['original_packages'][0]['operation_id'] == w['original']['operation_id']
        assert data['requirement_acceptance'] == 'not_evaluated_by_delivery'
        status, second = await http(w['port'], 'GET', root+f"?after={data['next_after']}&limit=1", tok=auth)
        assert status == 200 and second['deliveries'][0]['integration']['pushed'] is True
        assert second['next_after'] is None
        assert before == (d.ops.db.total_changes, len(w['gh'].requests), len(w['runner'].scripts))
        for query in ('?after=', '?after=-1', '?after=9223372036854775808', '?limit=0', '?limit=101',
                      '?refresh=true', '?after=0&after=1', '?work_item_id='+work):
            assert (await http(w['port'], 'GET', root+query, tok=auth))[0] == 422, query
        assert (await http(w['port'], 'GET', root+'?after=9223372036854775807', tok=auth))[1]['deliveries'] == []
        assert (await http(w['port'], 'GET', root, tok=token(d, 'outsider', 'observe')))[0] == 404
        assert (await http(w['port'], 'GET', root, tok=token(d, DELIVERER.actor, 'manage')))[0] == 403
        revoke(w)
        status, hidden = await http(w['port'], 'GET', root, tok=auth)
        assert status == 404 and done['operation_id'] not in json.dumps(hidden)
        with pytest.raises(OperationError):
            await d.call_api('pm_work_deliveries_list', params, DELIVERER)
        with pytest.raises(ToolError, match='NOT_FOUND') as denied:
            await server.call_tool('pm_work_deliveries_list', params)
        assert done['operation_id'] not in str(denied.value)
    finally:
        await fleet.close()


async def test_delivery_budget_counts_actual_wire_spaces_and_never_truncates_a_receipt(delivery_ready, monkeypatch):
    w = delivery_ready
    await preview(w)
    # Compact JSON is within the limit, while the real wire form exceeds it.
    facts = {'sources': ['x'] * 40000}
    assert len(json.dumps(facts, separators=(',', ':')).encode()) < 192_000
    assert len(json.dumps(facts).encode()) > 192_000
    monkeypatch.setattr(pm_delivery_lineage, '_facts', lambda *_: facts)
    with pytest.raises(OperationError) as caught:
        pm_delivery_lineage.for_work(w['d'].ops, DELIVERER, w['workspace_id'], w['target']['work_item_id'])
    assert caught.value.code == 'PM_DELIVERY_READ_TOO_LARGE'
    assert not w['d'].ops.db.in_transaction
