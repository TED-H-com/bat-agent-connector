"""One observed execution receipt across the three public read transports."""
import json

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from bat_agent_connector import mcp_server, pm_access
from bat_agent_connector import pm_execution_reads as reads
from bat_agent_connector.operations import OperationError
from tests import test_pm_execution_verification as base
from tests.test_api_v1 import http, token
from tests.test_pm_run_api import unpack

world, pm, ready, fixed, owned = base.world, base.pm, base.ready, base.fixed, base.owned


async def test_verifier_sources_reads_scope_and_original_receipt(owned, monkeypatch):
    w, d, who = owned, owned['d'], base.OWNER
    wid, child = w['workspace_id'], w['child']['operation_id']
    auth = token(d, who.actor, *who.scopes)
    monkeypatch.setenv('BATC_TASK_URL', f"http://127.0.0.1:{w['port']}/rpc")
    monkeypatch.setenv('BATC_API_TOKEN', auth)
    server, fleet = mcp_server.build_server(d.fleet.config, read_only=True, principal_only=True)
    try:
        tools = {tool.name: tool for tool in await server.list_tools()}
        method = 'pm_execution_verification_configuration'
        params = {'workspace_id': wid, 'execution_operation_id': child}
        path = f'/api/v1/pm/workspaces/{wid}/executions/{child}/verification-config'
        assert tools[method].annotations.read_only_hint
        count = d.ops.db.total_changes
        status, config = await http(w['port'], 'GET', path, tok=auth)
        assert status == 200 and config['candidate_commit'] == w['config']['candidate_commit']
        assert await d.call_api(method, dict(params), who) == config
        assert unpack(await server.call_tool(method, params)) == config
        assert d.ops.db.total_changes == count and not w['marker'].exists()
        assert (await http(w['port'], 'GET', path+'?run=true', tok=auth))[0] == 422
        for field in params:
            for bad in (None, [], 1, 'invalid'):
                with pytest.raises(OperationError) as malformed:
                    await reads.read(d.ops, who, method, {**params, field: bad})
                assert malformed.value.status == 422
        for scopes, allowed in [(('observe',), False), (('operate',), False), (('observe', 'manage'), False),
                (('observe', 'operate'), True)]:
            status, caps = await http(w['port'], 'GET', '/api/v1/capabilities', tok=token(d, who.actor, *scopes))
            if 'observe' not in scopes:
                assert status == 403
                continue
            assert status == 200
            assert next(a['allowed'] for a in caps['actions'] if a['action'] == base.verifier.ACTION) is allowed
            assert caps['features']['pm_workspaces']['execution_verification']['version'] == 1
        done = await base.execute(w)
        assert done['status'] == 'succeeded' and done['result']['status'] == 'passed', done
        oid = done['operation_id']
        method = 'pm_execution_verification_get'
        params = {'workspace_id': wid, 'verification_operation_id': oid}
        path = f'/api/v1/pm/workspaces/{wid}/execution-verifications/{oid}'
        count = d.ops.db.total_changes
        status, data = await http(w['port'], 'GET', path, tok=auth)
        assert status == 200 and data['verification'] == done['result']
        assert await d.call_api(method, dict(params), who) == data
        assert unpack(await server.call_tool(method, params)) == data
        assert d.ops.db.total_changes == count and w['marker'].read_text() == 'x'
        base.revoke(w)
        assert not pm_access.visible(d.ops.db, who, done)
        assert (await http(w['port'], 'GET', path, tok=auth))[0] == 404
        assert (await http(w['port'], 'GET', '/api/v1/operations/'+oid, tok=auth))[0] == 404
        with pytest.raises(ToolError, match='NOT_FOUND'):
            await server.call_tool(method, params)
        status, events = await http(w['port'], 'GET', '/api/v1/events?after=0&limit=500', tok=auth)
        assert status == 200 and oid not in json.dumps(events)
        assert w['marker'].read_text() == 'x'
    finally:
        await fleet.close()
