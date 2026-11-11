"""Actual HTTP, RPC and MCP preserve cleanup policy and their existing envelopes."""
from __future__ import annotations

import asyncio
import json
from urllib.parse import urlencode

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from bat_agent_connector import mcp_server, pm_source_lineage, work_items
from bat_agent_connector.api_auth import Principal
from bat_agent_connector.operations import OperationError
from bat_agent_connector.session_history_config import HistoryConfig
from bat_agent_connector.task_daemon import API_RPC
from tests.operation_helpers import settle_operations
from tests.test_api_v1 import http, served, token  # noqa: F401
from tests.test_external_history_cleanup import (
    METHOD,
    MISSING_LINK,
    MISSING_WORK,
    MISSING_WORKSPACE,
    PEER,
    add_rows,
    cleanup_case,  # noqa: F401
    daemon,  # noqa: F401
    linked_work,  # noqa: F401
    member,
    project,
    ready,  # noqa: F401
    retained,  # noqa: F401
    withdraw_rows,
)
from tests.test_pm_api import OWNER
from tests.test_pm_run_api import unpack
from tests.test_work_items import act


def cleanup_path(params):
    path = f"/api/v1/pm/workspaces/{params['workspace_id']}/work/{params['work_item_id']}/external-links/cleanup"
    query = {key: value for key, value in params.items() if key in {'after', 'limit'} and value is not None}
    return path + ('?' + urlencode(query) if query else '')


async def raw_get(port, path, auth=None):
    """Keep actual response body bytes for the uniform-refusal comparison."""
    reader, writer = await asyncio.open_connection('127.0.0.1', port)
    lines = [f'GET {path} HTTP/1.1', f'Host: 127.0.0.1:{port}']
    if auth:
        lines.append('Authorization: Bearer ' + auth)
    try:
        writer.write(('\r\n'.join(lines) + '\r\n\r\n').encode())
        await writer.drain()
        raw = await asyncio.wait_for(reader.read(), 10)
    finally:
        writer.close()
        await writer.wait_closed()
    head, _, body = raw.partition(b'\r\n\r\n')
    return int(head.split(b' ')[1]), body


async def tool_error(server, method, params):
    with pytest.raises(ToolError) as error:
        await server.call_tool(method, params)
    # The SDK names the invoked tool in its wrapper. Verify that wrapper, then
    # compare the complete, unchanged error text produced by the two PM tools.
    prefix = f'Error executing tool {method}: '
    assert str(error.value).startswith(prefix)
    return str(error.value).removeprefix(prefix)


@pytest.fixture
async def adapters(cleanup_case, served, monkeypatch):  # noqa: F811
    d, target, source, lid = cleanup_case
    _, port = served
    auth = token(d, OWNER.actor, *OWNER.scopes)
    monkeypatch.setenv('BATC_TASK_URL', f'http://127.0.0.1:{port}/rpc')
    monkeypatch.setenv('BATC_API_TOKEN', auth)
    server, fleet = mcp_server.build_server(d.fleet.config, read_only=True, principal_only=True)
    try:
        yield d, target, source, lid, port, auth, server
    finally:
        await fleet.close()


async def baseline_refusals(d, target, lid, port, auth, server):
    params = {'workspace_id': target['workspace_id'], 'link_id': lid}
    method = 'pm_history_link_get'
    path = f"/api/v1/pm/workspaces/{target['workspace_id']}/external-links/{lid}"
    web = await raw_get(port, path, auth)
    rpc = await http(port, 'POST', '/rpc', tok=auth, body={'method': method, 'params': params})
    tool = await tool_error(server, method, params)
    assert web[0] == 404 and rpc[0] == 400
    body = json.loads(web[1])['error']
    assert rpc[1] == {'error': body['code'], 'message': body['message']}
    assert body['code'] in tool and body['message'] in tool
    return web, rpc, tool


@pytest.mark.parametrize('who', [OWNER, PEER], ids=['owner', 'creator'])
async def test_cleanup_read_parity_exact_projection_and_scoped_inventory(adapters, monkeypatch, who):
    d, target, _, lid, port, auth, server = adapters
    if who == PEER:
        await member(d, target['workspace_id'], who.actor, 'contributor')
        withdraw_rows(d, [lid])
        lid, *more = add_rows(d, target, lid, 3, actor=who.actor)
        auth = token(d, who.actor, *who.scopes)
        monkeypatch.setenv('BATC_API_TOKEN', auth)
    else:
        more = add_rows(d, target, lid, 2)
    assert API_RPC[METHOD] == 'observe' and METHOD in mcp_server.READ_TOOLS
    schemas = {tool.name: tool.input_schema for tool in await server.list_tools()}
    assert set(schemas[METHOD]['properties']) == {'workspace_id', 'work_item_id', 'after', 'limit'}
    assert set(schemas[METHOD]['required']) == {'workspace_id', 'work_item_id'}
    assert schemas[METHOD]['properties']['after']['anyOf'] == [{'type': 'string'}, {'type': 'null'}]
    assert schemas[METHOD]['properties']['limit']['type'] == 'integer'
    for params in (target | {'limit': 1}, target | {'limit': 50, 'after': lid}):
        before = d.ops.db.total_changes
        expected = await d.call_api(METHOD, params.copy(), who)
        web = await http(port, 'GET', cleanup_path(params), tok=auth)
        rpc = await http(port, 'POST', '/rpc', tok=auth, body={'method': METHOD, 'params': params})
        tool = unpack(await server.call_tool(METHOD, params))
        assert web == (200, expected) and rpc == (200, {'result': expected}) and tool == expected
        for response in (expected, web[1], rpc[1]['result'], tool):
            assert project(response, target['work_item_id']) == ([lid] if params['limit'] == 1 else more)
        assert d.ops.db.total_changes == before
    capabilities = (await http(port, 'GET', '/api/v1/capabilities', tok=auth))[1]['features']['external_history']
    assert capabilities['relationships']['cleanup_discovery'] == {'version': 1}
    assert capabilities['relationships']['derive_ready'] is False
    assert capabilities['start_owned'] is capabilities['send_owned'] is False


async def test_cleanup_preserves_all_five_grant_checks_and_original_unlink_recovery(adapters):
    d, target, source, lid, port, auth, server = adapters
    ref = json.loads(d.ops.db.execute('SELECT canonical_ref FROM pm_external_excerpts').fetchone()[0])
    excerpt_id = d.ops.db.execute('SELECT excerpt_id FROM pm_external_links WHERE link_id=?', (lid,)).fetchone()[0]
    base = f"/api/v1/pm/workspaces/{target['workspace_id']}"
    preview = {key: ref[key] for key in ('source_id', 'entry_id', 'selection')}
    preview['expected_content_digest'] = ref['content_digest']
    cases = [
        ('pm_history_excerpt_preview', 'POST', base + '/history-excerpt-preview', preview),
        ('pm_history_excerpt_get', 'GET', base + f'/history-excerpts/{excerpt_id}', {'excerpt_id': excerpt_id}),
        ('pm_history_links_list', 'GET', base + f"/work/{target['work_item_id']}/external-links", {'work_item_id': target['work_item_id']}),
        ('pm_history_link_get', 'GET', base + f'/external-links/{lid}', {'link_id': lid}),
        ('pm_history_associations_list', 'GET', base + f'/history/{source.source_id}/associations?entry_id={ref["entry_id"]}',
            {'source_id': source.source_id, 'entry_id': ref['entry_id']}),
    ]
    d.history_collector.config = HistoryConfig(10, ())
    expected_web, expected_rpc, expected_tool = await baseline_refusals(d, target, lid, port, auth, server)
    for method, verb, path, selected in cases:
        params = {'workspace_id': target['workspace_id'], **selected}
        status, body = await http(port, verb, path, tok=auth, body=selected if verb == 'POST' else None)
        rpc = await http(port, 'POST', '/rpc', tok=auth, body={'method': method, 'params': params})
        if method == 'pm_history_links_list':
            assert (status, body) == (200, {'links': [], 'next_after': None})
            assert rpc == (200, {'result': body})
            assert unpack(await server.call_tool(method, params)) == body
            assert await d.call_api(method, params, OWNER) == body
        else:
            assert status == expected_web[0] and body == json.loads(expected_web[1])
            assert rpc == expected_rpc and await tool_error(server, method, params) == expected_tool
            with pytest.raises(OperationError) as error:
                await d.call_api(method, params, OWNER)
            assert (error.value.code, error.value.message) == (expected_rpc[1]['error'], expected_rpc[1]['message'])
    page = (await http(port, 'GET', cleanup_path(target), tok=auth))[1]
    assert project(page, target['work_item_id']) == [lid]
    assert unpack(await server.call_tool(METHOD, target)) == page
    assert await d.call_api(METHOD, target, OWNER) == page
    request = {'action': 'pm.history.unlink', 'target': target | {'link_id': lid}, 'params': {},
        'preconditions': {'expected_link_version': page['links'][0]['version']}}
    headers = {'Idempotency-Key': 'cleanup-lost-reply'}
    status, first = await http(port, 'POST', '/api/v1/operations?wait=3', tok=auth, body=request, headers=headers)
    assert status in (200, 202)
    await settle_operations(d.ops)
    status, completed = await http(port, 'GET', '/api/v1/operations/' + first['operation']['operation_id'], tok=auth)
    assert status == 200 and completed['operation']['status'] == 'succeeded'
    assert completed['operation']['result'] == {'link_id': lid, 'version': 2, 'state': 'withdrawn'}
    assert await d.call_api(METHOD, target, OWNER) == {'links': [], 'next_after': None}
    tables = ('operations', 'pm_external_links', 'pm_external_link_events')
    before = [d.ops.db.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0] for table in tables]
    _, recovered = await http(port, 'POST', '/api/v1/operations?wait=3', tok=auth, body=request, headers=headers)
    assert recovered['operation']['operation_id'] == first['operation']['operation_id']
    assert [d.ops.db.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0] for table in tables] == before


@pytest.mark.parametrize('kind', ['workspace', 'missing_work', 'nonmember', 'removed', 'admin', 'hidden', 'unbound',
    'foreign_workspace_work', 'unknown_cursor', 'other_work_cursor', 'other_workspace_cursor', 'other_creator_cursor'])
async def test_cleanup_uniform_semantic_refusals_within_each_adapter(adapters, monkeypatch, kind):
    d, target, source, lid, port, auth, server = adapters
    who = OWNER
    params = target.copy()
    if kind in {'nonmember', 'removed', 'other_creator_cursor'}:
        who = PEER
        if kind != 'nonmember':
            await member(d, target['workspace_id'], who.actor, 'contributor')
        if kind == 'removed':
            await member(d, target['workspace_id'], who.actor, None)
        auth = token(d, who.actor, *who.scopes)
    elif kind == 'admin':
        who = Principal('local-admin', frozenset(), admin=True)
        auth = d._admin_token
    monkeypatch.setenv('BATC_API_TOKEN', auth)
    d.history_collector.config = HistoryConfig(10, ())
    baseline = await baseline_refusals(d, target, lid, port, auth, server)
    if kind == 'workspace':
        params['workspace_id'] = MISSING_WORKSPACE
    elif kind == 'missing_work':
        params['work_item_id'] = MISSING_WORK
    elif kind == 'hidden':
        d.ops.db.execute('INSERT INTO pm_source_lineage VALUES(?,?,?,?)',
            ('work_item', target['work_item_id'], 'op_' + 'f' * 32, 'fixture'))
    elif kind in {'unbound', 'other_work_cursor'}:
        pid = work_items._get_item(d.ops.db, target['work_item_id'])['project_id']
        wi = (await act(d, OWNER, 'work_item.create', {'project_id': pid}, {'title': 'Other Work'}))['result']['work_item_id']
        other = target | {'work_item_id': wi}
        if kind == 'unbound':
            params = other
        else:
            await act(d, OWNER, 'pm.work.bind', other, {'rationale': 'Fixture'},
                {'expected_work_version': 1, 'expected_binding_id': None})
            params['after'] = add_rows(d, other, lid, 1)[0]
    elif kind == 'foreign_workspace_work':
        workspace = (await act(d, OWNER, 'pm.workspace.create', params={'name': 'Other workspace'}))['result']['workspace']
        params['workspace_id'] = workspace['workspace_id']
    elif kind == 'other_workspace_cursor':
        params['after'] = add_rows(d, target | {'workspace_id': MISSING_WORKSPACE}, lid, 1)[0]
    elif kind == 'unknown_cursor':
        params['after'] = MISSING_LINK
    elif kind == 'other_creator_cursor':
        assert await d.call_api(METHOD, target, who) == {'links': [], 'next_after': None}
        params['after'] = lid
    assert await raw_get(port, cleanup_path(params), auth) == baseline[0]
    assert await http(port, 'POST', '/rpc', tok=auth, body={'method': METHOD, 'params': params}) == baseline[1]
    assert await tool_error(server, METHOD, params) == baseline[2]
    with pytest.raises(OperationError) as error:
        await d.call_api(METHOD, params, who)
    assert (error.value.status, error.value.code, error.value.message) == (
        baseline[0][0], baseline[1][1]['error'], baseline[1][1]['message'])


@pytest.mark.parametrize('scope', ['observe', 'manage'])
async def test_cleanup_scope_and_missing_credentials_precede_source_lookups(adapters, monkeypatch, scope):
    d, target, _, lid, port, _, server = adapters
    auth = token(d, OWNER.actor, scope)
    monkeypatch.setenv('BATC_API_TOKEN', auth)
    def unexpected(*args, **kwargs):
        pytest.fail('Authorization must fail before source-dependent lookup')
    monkeypatch.setattr(pm_source_lineage, 'read_request', unexpected)
    principal = Principal(OWNER.actor, frozenset({scope}))
    with pytest.raises(OperationError) as policy:
        await d.call_api(METHOD, target, principal)
    assert policy.value.status == 403 and policy.value.code == 'FORBIDDEN'
    for selected in (target | {'after': lid}, target | {'work_item_id': MISSING_WORK, 'after': MISSING_LINK}):
        status, body = await http(port, 'GET', cleanup_path(selected), tok=auth)
        assert status == 403 and body['error'] == {'code': policy.value.code, 'message': policy.value.message}
        assert await http(port, 'POST', '/rpc', tok=auth, body={'method': METHOD, 'params': selected}) == (
            400, {'error': policy.value.code, 'message': policy.value.message})
        text = await tool_error(server, METHOD, selected)
        assert policy.value.code in text and policy.value.message in text
    old_path = f"/api/v1/pm/workspaces/{target['workspace_id']}/external-links/{lid}"
    if scope == 'manage':
        assert (await http(port, 'GET', old_path, tok=auth))[1]['error'] == {
            'code': 'FORBIDDEN', 'message': "this route needs the 'observe' scope"}
    assert await raw_get(port, cleanup_path(target)) == await raw_get(port, old_path)
    assert (await raw_get(port, cleanup_path(target)))[0] == 401
    missing_rpc = await http(port, 'POST', '/rpc', body={'method': METHOD, 'params': target})
    assert missing_rpc == await http(port, 'POST', '/rpc', body={
        'method': 'pm_history_link_get', 'params': {'workspace_id': target['workspace_id'], 'link_id': lid}})
    monkeypatch.delenv('BATC_API_TOKEN')
    monkeypatch.setattr(mcp_server, 'task_request', unexpected)
    assert await tool_error(server, METHOD, target) == await tool_error(server, 'pm_history_link_get',
        {'workspace_id': target['workspace_id'], 'link_id': lid})


async def test_cleanup_http_exact_query(adapters):
    _, target, _, _, port, auth, _ = adapters
    for query in ['limit=0', 'limit=51', 'limit=-1', 'limit=true', 'limit=1.0', 'limit=',
        'limit=1&limit=2', 'after=', 'after=0', 'after=null', 'after=hxl_short',
        'after=' + MISSING_LINK + '&after=' + MISSING_LINK, 'actor=other', 'source_id=example', 'entry_id=example',
        'workspace_id=other', 'work_item_id=other', 'path=/private', 'refresh=1', 'include_withdrawn=true']:
        assert (await http(port, 'GET', cleanup_path(target) + '?' + query, tok=auth))[0] == 422, query


async def test_cleanup_http_body_method_and_mcp_types(adapters):
    _, target, _, lid, port, auth, server = adapters
    path = cleanup_path(target)
    for body in ({}, {'after': lid}):
        assert (await http(port, 'GET', path, tok=auth, body=body))[0] == 422
    for headers in ({'Content-Length': '1'}, {'Transfer-Encoding': 'chunked'}):
        assert (await http(port, 'GET', path, tok=auth, headers=headers))[0] == 422
    assert (await http(port, 'GET', path, tok=auth, headers={'Content-Length': '0'}))[0] == 200
    for verb in ('POST', 'PUT', 'DELETE'):
        assert (await http(port, verb, path, tok=auth))[0] == 405
    for params in ({'limit': True}, {'limit': 1.0}, {'limit': '1'}, {'after': 1}, {'after': ''}, {'after': []}):
        await tool_error(server, METHOD, target | params)
    assert unpack(await server.call_tool(METHOD, target | {'after': None, 'limit': 1}))['links'][0]['link_id'] == lid
    # The existing GET's historical body handling is unchanged by this route's guard.
    assert (await http(port, 'GET', path.removesuffix('/cleanup'), tok=auth, body={}))[0] == 200


async def test_cleanup_mcp_preserves_cursor_strings_and_refuses_unknown_arguments(adapters):
    d, target, _, lid, port, auth, server = adapters
    schema = next(tool.input_schema for tool in await server.list_tools() if tool.name == METHOD)
    assert schema['additionalProperties'] is False
    assert 'extra_forbidden' in await tool_error(server, METHOD, target | {'actor': 'ignored-actor'})
    for cursor in ('null', '[]', '{}', 'true', 'false', '"null"'):
        params = target | {'after': cursor}
        web = await http(port, 'GET', cleanup_path(params), tok=auth)
        rpc = await http(port, 'POST', '/rpc', tok=auth, body={'method': METHOD, 'params': params})
        assert web[0] == 422 and rpc[0] == 400
        assert rpc[1] == {'error': web[1]['error']['code'], 'message': web[1]['error']['message']}
        assert rpc[1]['error'] == 'PM_HISTORY_REFERENCE_INVALID'
        text = await tool_error(server, METHOD, params)
        assert rpc[1]['error'] in text and rpc[1]['message'] in text
    first = unpack(await server.call_tool(METHOD, target))
    assert first['links'][0]['link_id'] == lid
    assert unpack(await server.call_tool(METHOD, target | {'after': None})) == first
    later = add_rows(d, target, lid, 1)
    assert project(unpack(await server.call_tool(METHOD, target | {'after': lid})), target['work_item_id']) == later
    # Other tools retain their established JSON-in-a-string argument handling.
    ref = json.loads(d.ops.db.execute('SELECT canonical_ref FROM pm_external_excerpts').fetchone()[0])
    params = {key: ref[key] for key in ('workspace_id', 'source_id', 'entry_id')}
    params.update(expected_content_digest=ref['content_digest'], selection=json.dumps(ref['selection']))
    assert unpack(await server.call_tool('pm_history_excerpt_preview', params))['ref'] == ref


@pytest.mark.parametrize('transport', ['http', 'rpc', 'mcp'])
@pytest.mark.parametrize('barrier', ['request', 'response'])
async def test_cleanup_normalizes_generic_lineage_barrier_refusal(adapters, monkeypatch, transport, barrier):
    d, target, _, lid, port, auth, server = adapters
    d.history_collector.config = HistoryConfig(10, ())
    baseline = await baseline_refusals(d, target, lid, port, auth, server)
    original = getattr(pm_source_lineage, 'read_' + barrier)
    def hide(db, principal, value):
        db.execute('INSERT INTO pm_source_lineage VALUES(?,?,?,?)',
            ('work_item', target['work_item_id'], 'op_' + 'f' * 32, 'fixture'))
        return original(db, principal, value)
    monkeypatch.setattr(pm_source_lineage, 'read_' + barrier, hide)
    if transport == 'http':
        assert await raw_get(port, cleanup_path(target), auth) == baseline[0]
    elif transport == 'rpc':
        assert await http(port, 'POST', '/rpc', tok=auth, body={'method': METHOD, 'params': target}) == baseline[1]
    else:
        assert await tool_error(server, METHOD, target) == baseline[2]


async def test_cleanup_empty_page_cursor_is_identical_across_adapters(adapters, monkeypatch):
    from bat_agent_connector import pm_external_history_links as links
    d, target, _, original, port, auth, server = adapters
    identities = add_rows(d, target, original, 4)
    withdraw_rows(d, [original])
    real = links._cleanup
    for transport, anchor in [('http', None), ('rpc', identities[0]), ('mcp', identities[1])]:
        selected = target | {'limit': 1, **({'after': anchor} if anchor else {})}
        next_id = identities[0 if anchor is None else identities.index(anchor) + 1]
        def withdraw(db, principal, wid, identity):
            if identity == next_id:
                row = real(db, principal, wid, identity)
                if row['state'] == 'active':
                    withdraw_rows(d, [identity])
            return real(db, principal, wid, identity)
        with monkeypatch.context() as patch:
            patch.setattr(links, '_cleanup', withdraw)
            if transport == 'http':
                status, page = await http(port, 'GET', cleanup_path(selected), tok=auth)
                assert status == 200
            elif transport == 'rpc':
                status, response = await http(port, 'POST', '/rpc', tok=auth, body={'method': METHOD, 'params': selected})
                assert status == 200
                page = response['result']
            else:
                page = unpack(await server.call_tool(METHOD, selected))
        assert page == {'links': [], 'next_after': next_id}
