"""AHP HTTP/RPC/MCP parity, native-supported exact paths and source withdrawal."""
from dataclasses import replace

import pytest

from bat_agent_connector import api_auth, mcp_server
from bat_agent_connector import pm_ahp_observations as ahp
from tests.operation_helpers import settle_operations
from tests.test_ahp_observer import SESSION
from tests.test_ahp_observer import host as ahp_host
from tests.test_api_v1 import daemon as api_daemon
from tests.test_api_v1 import http, token, write_frames
from tests.test_api_v1 import served as api_served
from tests.test_pm_api import OWNER, seed
from tests.test_pm_run_api import unpack

daemon, served, host = api_daemon, api_served, ahp_host


@pytest.fixture
async def ready(served, host):
    d, port = served
    workspace, _ = await seed(d)
    wid = workspace['workspace_id']
    host.adapter = replace(host.adapter, workspace_ids=(wid,))
    d.fleet.config.ahp = {'local': host.adapter}
    ahp.sync_config(d.ops)
    auth = token(d, OWNER.actor, 'observe')
    principal = api_auth.authenticate(d.journal.db, auth, d._admin_token)
    return d, port, host, wid, auth, principal


async def test_actual_http_capture_and_all_read_entrypoints_share_original_receipt(ready, monkeypatch, mock):
    d, port, host, wid, auth, principal = ready
    body = {'action': ahp.ACTION, 'target': {'workspace_id': wid, 'adapter_id': 'local'},
            'params': {'session_uris': [SESSION], 'include_changesets': True}, 'preconditions': {}}
    status, accepted = await http(port, 'POST', '/api/v1/operations', tok=auth, body=body, headers={'Idempotency-Key': 'original-ahp'})
    assert status == 202, accepted
    await settle_operations(d.ops)
    done = d.ops.get(accepted['operation']['operation_id'])
    assert done['status'] == 'succeeded', done
    oid = done['result']['observation_id']
    monkeypatch.setenv('BATC_TASK_URL', f'http://127.0.0.1:{port}/rpc')
    monkeypatch.setenv('BATC_API_TOKEN', auth)
    server, fleet = mcp_server.build_server(d.fleet.config, read_only=True, principal_only=True)
    try:
        tools = {tool.name: tool for tool in await server.list_tools()}
        count, writes = len(host.requests), d.ops.db.total_changes
        for method, fields, suffix in [
            ('pm_ahp_adapters_list', {}, '/ahp-adapters'),
            ('pm_ahp_observations_list', {'after': 0, 'limit': 1}, '/ahp-observations?after=0&limit=1'),
            ('pm_ahp_observation_current_get', {}, '/ahp-observations/current'),
            ('pm_ahp_observation_get', {'observation_id': oid}, '/ahp-observations/' + oid),
        ]:
            status, expected = await http(port, 'GET', f'/api/v1/pm/workspaces/{wid}' + suffix, tok=auth)
            assert status == 200, expected
            params = {'workspace_id': wid, **fields}
            assert await d.call_api(method, params, principal) == expected
            assert unpack(await server.call_tool(method, params)) == expected
            assert tools[method].annotations.read_only_hint
        assert writes == d.ops.db.total_changes and count == len(host.requests)
        status, replay = await http(port, 'POST', '/api/v1/operations', tok=auth, body=body, headers={'Idempotency-Key': 'original-ahp'})
        assert status == 200 and replay['operation']['operation_id'] == done['operation_id']
        assert len(host.requests) == count and not write_frames(mock)
        status, caps = await http(port, 'GET', '/api/v1/capabilities', tok=auth)
        assert status == 200 and caps['features']['pm_workspaces']['ahp_observation'] == ahp.capabilities()
    finally:
        await fleet.close()


async def test_queries_are_exact_and_adapter_revocation_hides_global_operation(ready):
    d, port, host, wid, auth, principal = ready
    root = f'/api/v1/pm/workspaces/{wid}'
    for suffix in ['/ahp-adapters?endpoint=custom', '/ahp-adapters?adapter_id=local',
                   '/ahp-observations?after=', '/ahp-observations?after=0&after=1',
                   '/ahp-observations?after=9223372036854775808', '/ahp-observations?limit=51',
                   '/ahp-observations/current?refresh=true']:
        assert (await http(port, 'GET', root + suffix, tok=auth))[0] == 422, suffix
    assert (await http(port, 'GET', root + '/ahp-adapters', tok=token(d, 'outsider', 'observe')))[0] == 404
    assert (await http(port, 'GET', root + '/ahp-adapters', tok=token(d, OWNER.actor, 'manage')))[0] == 403
    op, _ = d.ops.create(principal, action=ahp.ACTION, target={'workspace_id': wid, 'adapter_id': 'local'}, params={}, preconditions={}, idempotency_key='ahp-catalog')
    await settle_operations(d.ops)
    assert d.ops.get(op['operation_id'])['status'] == 'succeeded'
    d.fleet.config.ahp = {}
    ahp.sync_config(d.ops)
    assert (await http(port, 'GET', '/api/v1/operations/' + op['operation_id'], tok=auth))[0] == 404
    assert (await http(port, 'GET', root + '/ahp-observations', tok=auth))[1]['observations'] == []
    assert (await http(port, 'GET', root + '/ahp-observations/current', tok=auth))[1]['visibility_filtered']
