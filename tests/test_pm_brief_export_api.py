"""The same fixed, authorized report is returned by HTTP/RPC/MCP as JSON."""
from __future__ import annotations

import hashlib
import json

import pytest

from bat_agent_connector import controller_authority, mcp_server, pm_brief_exports
from bat_agent_connector.operations import OperationError
from tests.test_api_v1 import daemon as daemon_fixture
from tests.test_api_v1 import http, token, write_frames
from tests.test_api_v1 import served as served_fixture
from tests.test_mcp_principal import call
from tests.test_pm_api import OTHER, OWNER
from tests.test_pm_evidence import task
from tests.test_pm_evidence_api import capture, prepared
from tests.test_pm_run_api import grant, unpack
from tests.test_pm_snapshot_api import snapshot
from tests.test_work_items import act

daemon, served = daemon_fixture, served_fixture


async def test_brief_export_http_rpc_mcp_parity_fixed_identity_and_no_write(served, monkeypatch, mock):
    d, port = served
    target, _, binding = await prepared(d)
    await capture(d, target, binding)
    saved = await snapshot(d, target['workspace_id'])
    wid, sid = target['workspace_id'], saved['result']['snapshot_id']
    fields = {'workspace_id': wid, 'snapshot_id': sid}
    path = f'/api/v1/pm/workspaces/{wid}/snapshots/{sid}/brief-export'
    auth = token(d, OWNER.actor, 'observe')
    monkeypatch.setenv('BATC_TASK_URL', f'http://127.0.0.1:{port}/rpc')
    monkeypatch.setenv('BATC_API_TOKEN', auth)
    server, fleet = mcp_server.build_server(d.fleet.config, read_only=True, principal_only=True)
    try:
        tools = {tool.name: tool for tool in await server.list_tools()}
        assert tools['pm_brief_export_get'].annotations.read_only_hint
        assert set(tools['pm_brief_export_get'].input_schema['properties']) == {*fields, 'format', 'locale'}
        before = d.ops.db.total_changes
        results = {}
        for format in ['markdown', 'html', 'svg']:
            for locale in ['en', 'zh-TW']:
                params = {**fields, 'format': format, 'locale': locale}
                status, result = await http(port, 'GET', f'{path}?format={format}&locale={locale}', tok=auth)
                assert status == 200, result
                assert await d.call_api('pm_brief_export_get', params.copy(), OWNER) == result
                assert unpack(await server.call_tool('pm_brief_export_get', params)) == result
                assert result['workspace_id'] == wid and result['snapshot_id'] == sid
                assert result['format'] == format and result['locale'] == locale
                raw = result['content'].encode('utf-8')
                assert result['byte_length'] == len(raw) and result['output_digest'] == hashlib.sha256(raw).hexdigest()
                assert result['coverage']['projection'] == 'project_brief_only' and not result['coverage']['content_truncated']
                assert not result['published'] and not result['review_completed'] and not result['execution_authorized']
                results[format, locale] = result
        assert d.ops.db.total_changes == before
        assert (await http(port, 'GET', path, tok=auth))[1] == results['markdown', 'en']
        assert await d.call_api('pm_brief_export_get', fields.copy(), OWNER) == results['markdown', 'en']
        assert unpack(await server.call_tool('pm_brief_export_get', fields)) == results['markdown', 'en']
        capabilities = (await http(port, 'GET', '/api/v1/capabilities', tok=auth))[1]['features']['pm_workspaces']
        assert capabilities['brief_export'] == {'formats': ['markdown', 'html', 'svg'], 'locales': ['en', 'zh-TW'], 'renderer_version': 2}
        # New snapshots/captures cannot silently replace the selected version.
        await capture(d, target, binding)
        await snapshot(d, wid)
        assert (await http(port, 'GET', path, tok=auth))[1] == results['markdown', 'en']
        assert d.ops.db.execute('SELECT COUNT(*) FROM artifacts').fetchone()[0] == 0
        assert not write_frames(mock)
    finally:
        await fleet.close()


async def test_brief_export_exact_queries_types_and_bounded_json_errors(served, monkeypatch):
    d, port = served
    target, _, _ = await prepared(d)
    saved = await snapshot(d, target['workspace_id'])
    fields = {'workspace_id': target['workspace_id'], 'snapshot_id': saved['result']['snapshot_id']}
    root = f"/api/v1/pm/workspaces/{fields['workspace_id']}/snapshots"
    path = f"{root}/{fields['snapshot_id']}/brief-export"
    auth = token(d, OWNER.actor, 'observe')
    for suffix in ['?format=', '?locale=', '?format=pdf', '?locale=zh', '?format=HTML', '?actor=',
                   '?format=&format=html', '?locale=en&locale=zh-TW', '?limit=1', '?path=report.html',
                   '?url=https://example.invalid', '?content=untrusted']:
        assert (await http(port, 'GET', path + suffix, tok=auth))[0] == 422, suffix
    for other in [root + '/latest/brief-export', root + '/brief-export', path + '/content']:
        assert (await http(port, 'GET', other, tok=auth))[0] == 404
    for params in [{'format': None}, {'locale': False}, {'format': []}, {'format': 'svg', 'path': 'report.svg'}]:
        with pytest.raises(OperationError, match='INVALID_PARAMS'):
            await d.call_api('pm_brief_export_get', {**fields, **params}, OWNER)
    monkeypatch.setenv('BATC_TASK_URL', f'http://127.0.0.1:{port}/rpc')
    monkeypatch.setenv('BATC_API_TOKEN', auth)
    server, fleet = mcp_server.build_server(d.fleet.config, read_only=True, principal_only=True)
    try:
        monkeypatch.setattr(pm_brief_exports, 'MAX_CONTENT_BYTES', 1)
        status, result = await http(port, 'GET', path, tok=auth)
        assert status == 413 and result['error']['code'] == 'PM_EXPORT_TOO_LARGE'
        assert 'content' not in result
        with pytest.raises(OperationError, match='PM_EXPORT_TOO_LARGE'):
            await d.call_api('pm_brief_export_get', fields, OWNER)
        assert 'PM_EXPORT_TOO_LARGE' in await call(server, 'pm_brief_export_get', fields)
    finally:
        await fleet.close()


async def test_brief_export_rechecks_original_source_and_current_member_all_surfaces(served, monkeypatch):
    d, port = served
    target, _, binding = await prepared(d)
    with controller_authority.use(controller_authority.binding(d.ops.db, OWNER), d.journal):
        source = task(d.ops)
    await act(d, OWNER, 'work_item.link', {'work_item_id': target['work_item_id']},
              {'kind': 'task', 'ref': source['task_id']})
    d.ops.db.execute("UPDATE tasks SET state='needs_ted' WHERE task_id=?", (source['task_id'],))
    await capture(d, target, binding)
    saved = await snapshot(d, target['workspace_id'])
    wid, sid = target['workspace_id'], saved['result']['snapshot_id']
    fields = {'workspace_id': wid, 'snapshot_id': sid, 'format': 'html'}
    path = f'/api/v1/pm/workspaces/{wid}/snapshots/{sid}/brief-export?format=html'
    auth = token(d, OWNER.actor, 'observe')
    before = (await http(port, 'GET', path, tok=auth))[1]
    assert source['task_id'] in before['content']
    d.ops.db.execute("UPDATE tasks SET controller_authority='{}' WHERE task_id=?", (source['task_id'],))
    monkeypatch.setenv('BATC_TASK_URL', f'http://127.0.0.1:{port}/rpc')
    monkeypatch.setenv('BATC_API_TOKEN', auth)
    server, fleet = mcp_server.build_server(d.fleet.config, read_only=True, principal_only=True)
    try:
        status, after = await http(port, 'GET', path, tok=auth)
        assert status == 200 and after['coverage']['visibility']['filtered']
        assert after == await d.call_api('pm_brief_export_get', fields.copy(), OWNER)
        assert after == unpack(await server.call_tool('pm_brief_export_get', fields))
        assert source['task_id'] not in json.dumps(after) and 'needs_ted' not in after['content']
        assert before['snapshot_digest'] == after['snapshot_digest']
        assert before['view_digest'] != after['view_digest'] and before['output_digest'] != after['output_digest']
        outsider = token(d, OTHER.actor, 'observe')
        assert (await http(port, 'GET', path, tok=outsider))[0] == 404
        await grant(d, wid, OTHER.actor, 'reader')
        assert (await http(port, 'GET', path, tok=outsider))[0] == 200
        monkeypatch.setenv('BATC_API_TOKEN', outsider)
        await grant(d, wid, OTHER.actor, None)
        assert (await http(port, 'GET', path, tok=outsider))[0] == 404
        with pytest.raises(OperationError, match='NOT_FOUND'):
            await d.call_api('pm_brief_export_get', fields.copy(), OTHER)
        assert 'NOT_FOUND' in await call(server, 'pm_brief_export_get', fields)
        assert (await http(port, 'GET', path, tok=token(d, OWNER.actor, 'manage')))[0] == 403
    finally:
        await fleet.close()
