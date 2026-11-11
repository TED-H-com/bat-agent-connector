"""HTTP, daemon and MCP share the retained producer payload and operation key."""
import json

import pytest

from bat_agent_connector import mcp_server
from tests.operation_helpers import settle_operations
from tests.test_api_v1 import http, served, token  # noqa: F401
from tests.test_external_history_links import daemon, linked_work, ready, retained  # noqa: F401
from tests.test_pm_api import OWNER
from tests.test_pm_run_api import unpack


async def test_read_and_operation_parity(linked_work, served, monkeypatch):  # noqa: F811
    d, req, source = linked_work
    _, port = served
    auth = token(d, OWNER.actor, *OWNER.scopes)
    monkeypatch.setenv('BATC_TASK_URL', f'http://127.0.0.1:{port}/rpc')
    monkeypatch.setenv('BATC_API_TOKEN', auth)
    server, fleet = mcp_server.build_server(d.fleet.config, read_only=False, principal_only=True)
    try:
        status, accepted = await http(port, 'POST', '/api/v1/operations', tok=auth, body=req,
            headers={'Idempotency-Key': req['idempotency_key']})
        assert status == 202, accepted
        await settle_operations(d.ops)
        op = d.ops.get(accepted['operation']['operation_id'])
        assert op['status'] == 'succeeded', op
        replay = unpack(await server.call_tool('operation_submit', req | {'confirm': True}))
        assert replay['operation']['operation_id'] == op['operation_id'], replay
        wid, wi = req['target'].values()
        ref = req['params']['fixed_ref']
        base = f'/api/v1/pm/workspaces/{wid}'
        lid = op['result']['link_id']
        row = d.ops.db.execute('SELECT excerpt_id FROM pm_external_links WHERE link_id=?', (lid,)).fetchone()
        preview = {k: ref[k] for k in ('source_id', 'entry_id', 'selection')}
        preview['expected_content_digest'] = ref['content_digest']
        cases = [
            ('pm_history_excerpt_preview', 'POST', '/history-excerpt-preview', preview),
            ('pm_history_excerpt_get', 'GET', f'/history-excerpts/{row[0]}', {'excerpt_id': row[0]}),
            ('pm_history_links_list', 'GET', f'/work/{wi}/external-links', {'work_item_id': wi}),
            ('pm_history_link_get', 'GET', f'/external-links/{lid}', {'link_id': lid}),
            ('pm_history_associations_list', 'GET', f'/history/{source.source_id}/associations?entry_id={ref["entry_id"]}',
                {'source_id': source.source_id, 'entry_id': ref['entry_id']}),
        ]
        before = d.ops.db.total_changes
        for method, verb, path, params in cases:
            status, result = await http(port, verb, base + path, tok=auth, body=params if verb == 'POST' else None)
            assert status == 200, result
            assert result == await d.call_api(method, {'workspace_id': wid, **params}, OWNER)
            assert result == unpack(await server.call_tool(method, {'workspace_id': wid, **params}))
            bad, _ = await http(port, verb, base + path + ('&' if '?' in path else '?') + 'actor=other',
                tok=auth, body=params if verb == 'POST' else None)
            assert bad == 422
        bad, _ = await http(port, 'POST', base + '/history-excerpt-preview', tok=auth,
            body={**preview, 'method': 'pm_workspaces_list'})
        assert bad == 422
        assert d.ops.db.total_changes == before
        caps = (await http(port, 'GET', '/api/v1/capabilities', tok=auth))[1]['features']['external_history']
        assert caps['relationships']['derive_ready'] is False
        assert caps['start_owned'] is caps['send_owned'] is False
        d.history_collector.config = type(d.history_collector.config)(10, ())
        for method, verb, path, params in cases:
            status, result = await http(port, verb, base + path, tok=auth, body=params if verb == 'POST' else None)
            assert status == (200 if method == 'pm_history_links_list' else 404), result
            assert ref['excerpt_digest'] not in json.dumps(result)
            if method == 'pm_history_links_list':
                assert unpack(await server.call_tool(method, {'workspace_id': wid, **params})) == result
            else:
                with pytest.raises(Exception, match='PM_HISTORY_SOURCE_UNAVAILABLE'):
                    await server.call_tool(method, {'workspace_id': wid, **params})
    finally:
        await fleet.close()
