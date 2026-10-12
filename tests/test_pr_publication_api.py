"""Original publication proof has one source-gated read contract in all clients."""
import json

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from bat_agent_connector import mcp_server, pm_access, pm_reads, pm_source_lineage
from bat_agent_connector.operations import OperationError
from tests import test_pr_publication as base
from tests.test_api_v1 import http, token
from tests.test_pm_run_api import unpack

world, pm, ready, fixed, delivery_ready = base.world, base.pm, base.ready, base.fixed, base.delivery_ready
pub = base.pub


async def test_publication_original_receipt_routes_capabilities_and_withdrawal(pub, monkeypatch):
    w, d = pub, pub['d']
    who = base.DELIVERER
    assert base.publication.ACTION in pm_source_lineage.DERIVED_ACTIONS
    done = await base.settle(w, base.create(w))
    assert done['status'] == 'succeeded', done
    oid, wid = done['operation_id'], w['workspace_id']
    auth = token(d, who.actor, *who.scopes)
    path = f'/api/v1/pm/workspaces/{wid}/pr-publications/{oid}'
    params = {'workspace_id': wid, 'publication_operation_id': oid}
    monkeypatch.setenv('BATC_TASK_URL', f"http://127.0.0.1:{w['port']}/rpc")
    monkeypatch.setenv('BATC_API_TOKEN', auth)
    server, fleet = mcp_server.build_server(d.fleet.config, read_only=True, principal_only=True)
    before = len(w['posts']), len(w['runner'].scripts), d.ops.db.total_changes
    try:
        tools = {tool.name: tool for tool in await server.list_tools()}
        assert tools['pm_pr_publication_get'].annotations.read_only_hint
        status, data = await http(w['port'], 'GET', path, tok=auth)
        assert status == 200 and data['creation_receipt']['native_pr_id'] == 801
        assert await d.call_api('pm_pr_publication_get', params, who) == data
        assert unpack(await server.call_tool('pm_pr_publication_get', params)) == data
        assert (len(w['posts']), len(w['runner'].scripts), d.ops.db.total_changes) == before
        assert (await http(w['port'], 'GET', path+'?refresh=true', tok=auth))[0] == 422
        for bad in (None, [], 1, 'op_invalid'):
            with pytest.raises(OperationError) as malformed:
                pm_reads.read(d.ops, who, 'pm_pr_publication_get', {**params, 'publication_operation_id': bad})
            assert malformed.value.status == 422
        for scopes, allowed in [(('observe',), False), (('observe', 'integrate'), False), (('observe', 'manage'), False),
                (('observe', 'manage', 'integrate'), True)]:
            status, caps = await http(w['port'], 'GET', '/api/v1/capabilities', tok=token(d, who.actor, *scopes))
            assert status == 200
            assert next(a['allowed'] for a in caps['actions'] if a['action'] == base.publication.ACTION) is allowed
        base.revoke(w)
        assert not pm_access.visible(d.ops.db, who, done)
        assert (await http(w['port'], 'GET', path, tok=auth))[0] == 404
        assert (await http(w['port'], 'GET', '/api/v1/operations/'+oid, tok=auth))[0] == 404
        with pytest.raises(ToolError, match='NOT_FOUND'):
            await server.call_tool('pm_pr_publication_get', params)
        status, events = await http(w['port'], 'GET', '/api/v1/events?after=0&limit=500', tok=auth)
        assert status == 200 and oid not in json.dumps(events)
    finally:
        await fleet.close()
