"""One read authority and identical fixed bytes across HTTP, RPC and strict MCP."""
from __future__ import annotations

import hashlib

import pytest

from bat_agent_connector import controller_authority, mcp_server, pm_roadmap, pm_roadmap_reports
from bat_agent_connector.api_auth import Principal
from bat_agent_connector.operations import OperationError
from tests.test_api_v1 import daemon as daemon_fixture
from tests.test_api_v1 import http, token, write_frames
from tests.test_api_v1 import served as served_fixture
from tests.test_mcp_principal import call
from tests.test_pm_api import OTHER, OWNER, seed
from tests.test_pm_evidence import task
from tests.test_pm_evidence_api import capture, prepared
from tests.test_pm_roadmap_api import baseline
from tests.test_pm_run_api import grant, unpack
from tests.test_pm_snapshot_api import snapshot
from tests.test_work_items import act

daemon, served = daemon_fixture, served_fixture
READ = 'pm_roadmap_report_get'


async def anchor(d, wid):
    selected = await baseline(d, wid)
    saved = await snapshot(d, wid)
    observed = await act(d, OWNER, 'pm.roadmap.observe', {'workspace_id': wid},
        {'baseline_revision_id': selected['result']['revision_id'], 'snapshot_id': saved['result']['snapshot_id']},
        {'expected_roadmap_version': pm_roadmap.head(d.ops.db, wid)['version']})
    fields = {'workspace_id': wid, 'observation_revision_id': observed['result']['revision_id']}
    return fields, f"/api/v1/pm/workspaces/{wid}/roadmap/revisions/{fields['observation_revision_id']}/report"


def server_for(d, port, monkeypatch, auth):
    monkeypatch.setenv('BATC_TASK_URL', f'http://127.0.0.1:{port}/rpc')
    monkeypatch.setenv('BATC_API_TOKEN', auth)
    return mcp_server.build_server(d.fleet.config, read_only=True, principal_only=True)


async def test_all_bytes_default_selectors_capability_and_no_effects(served, monkeypatch, mock):
    d, port = served
    workspace, _ = await seed(d)
    fields, path = await anchor(d, workspace['workspace_id'])
    auth = token(d, OWNER.actor, 'observe')
    server, fleet = server_for(d, port, monkeypatch, auth)
    try:
        tool = next(t for t in await server.list_tools() if t.name == READ)
        assert tool.annotations.read_only_hint
        assert tool.input_schema['additionalProperties'] is False
        assert set(tool.input_schema['properties']) == {*fields, 'format', 'locale', 'renderer_version'}
        before = d.ops.db.total_changes
        first = None
        for format in ['markdown', 'html', 'svg']:
            for locale in ['en', 'zh-TW']:
                params = fields | {'format': format, 'locale': locale, 'renderer_version': 1}
                status, result = await http(port, 'GET', path + f'?format={format}&locale={locale}&renderer_version=1', tok=auth)
                assert status == 200, result
                assert result == await d.call_api(READ, params.copy(), OWNER)
                assert result == unpack(await server.call_tool(READ, params))
                assert result['output_digest'] == hashlib.sha256(result['content'].encode()).hexdigest()
                assert result['coverage']['projection'] == 'roadmap_report'
                assert not result['coverage']['content_truncated']
                first = first or result
        assert (await http(port, 'GET', path, tok=auth))[1] == first
        assert await d.call_api(READ, fields.copy(), OWNER) == first
        assert unpack(await server.call_tool(READ, fields)) == first
        caps = (await http(port, 'GET', '/api/v1/capabilities', tok=auth))[1]['features']['pm_workspaces']
        assert caps['roadmap_report'] == pm_roadmap_reports.capabilities()
        assert 'roadmap_report_materialization' not in caps
        assert not caps['roadmap_report']['materialization']
        assert d.ops.db.total_changes == before
        await anchor(d, workspace['workspace_id'])
        assert (await http(port, 'GET', path, tok=auth))[1] == first
        assert d.ops.db.execute('SELECT COUNT(*) FROM artifacts').fetchone()[0] == 0
        assert not write_frames(mock)
    finally:
        await fleet.close()


async def test_selectors_are_exact_and_strict_across_transports(served, monkeypatch):
    d, port = served
    workspace, _ = await seed(d)
    fields, path = await anchor(d, workspace['workspace_id'])
    auth = token(d, OWNER.actor, 'observe')
    server, fleet = server_for(d, port, monkeypatch, auth)
    try:
        cases = [{'format': ''}, {'format': 'pdf'}, {'locale': ''}, {'locale': 'zh'},
            {'renderer_version': 0}, {'renderer_version': -1}, {'renderer_version': 1_000_000_000},
            {'content': 'body'}, {'path': 'report.svg'}, {'actor': 'owner'}]
        for changes in cases:
            query = '&'.join(f'{key}={value}' for key, value in changes.items())
            assert (await http(port, 'GET', path + '?' + query, tok=auth))[0] == 422, changes
            with pytest.raises(OperationError, match='INVALID_PARAMS'):
                await d.call_api(READ, fields | changes, OWNER)
            refusal = await call(server, READ, fields | changes)
            assert 'INVALID_PARAMS' in refusal or 'validation error' in refusal
        for suffix in ['?renderer_version=', '?renderer_version=true', '?renderer_version=1.0',
                       '?format=html&format=html', '?locale=en&locale=', '?renderer_version=1&renderer_version=2']:
            assert (await http(port, 'GET', path + suffix, tok=auth))[0] == 422
        for changes in [{'format': None}, {'locale': False}, {'format': []}, {'renderer_version': True},
                        {'renderer_version': '1'}, {'renderer_version': 1.0}, {'observation_revision_id': 'null'}]:
            with pytest.raises(OperationError, match='INVALID_PARAMS'):
                await d.call_api(READ, fields | changes, OWNER)
            refusal = await call(server, READ, fields | changes)
            assert 'INVALID_PARAMS' in refusal or 'validation error' in refusal
        # Unsupported numeric versions have identical post-authorization refusal.
        assert (await http(port, 'GET', path + '?renderer_version=2', tok=auth))[0] == 409
        with pytest.raises(OperationError, match='PM_EXPORT_TEMPLATE_UNAVAILABLE'):
            await d.call_api(READ, fields | {'renderer_version': 2}, OWNER)
        assert 'PM_EXPORT_TEMPLATE_UNAVAILABLE' in await call(server, READ, fields | {'renderer_version': 2})
    finally:
        await fleet.close()


async def test_scope_membership_and_reader_authority(served, monkeypatch):
    d, port = served
    workspace, _ = await seed(d)
    fields, path = await anchor(d, workspace['workspace_id'])
    missing_scope = token(d, OWNER.actor, 'manage')
    server, fleet = server_for(d, port, monkeypatch, missing_scope)
    try:
        assert (await http(port, 'GET', path, tok=missing_scope))[0] == 403
        with pytest.raises(OperationError, match='FORBIDDEN'):
            await d.call_api(READ, fields.copy(), Principal(OWNER.actor, frozenset({'manage'})))
        assert 'FORBIDDEN' in await call(server, READ, fields)
        outsider = token(d, OTHER.actor, 'observe')
        monkeypatch.setenv('BATC_API_TOKEN', outsider)
        for index, granted in enumerate([False, True, False]):
            if index:
                await grant(d, workspace['workspace_id'], OTHER.actor, 'reader' if granted else None)
            status, value = await http(port, 'GET', path, tok=outsider)
            if granted:
                assert status == 200
                assert value == await d.call_api(READ, fields.copy(), OTHER)
                assert value == unpack(await server.call_tool(READ, fields))
            else:
                assert status == 404
                with pytest.raises(OperationError, match='NOT_FOUND'):
                    await d.call_api(READ, fields.copy(), OTHER)
                assert 'NOT_FOUND' in await call(server, READ, fields)
    finally:
        await fleet.close()


async def test_withdrawal_including_unexpanded_evidence_precedes_size_diagnostics(served, monkeypatch):
    d, port = served
    target, _, binding = await prepared(d)
    with controller_authority.use(controller_authority.binding(d.ops.db, OWNER), d.journal):
        source = task(d.ops)
    await act(d, OWNER, 'work_item.link', {'work_item_id': target['work_item_id']},
              {'kind': 'task', 'ref': source['task_id']})
    await capture(d, target, binding)
    fields, path = await anchor(d, target['workspace_id'])
    auth = token(d, OWNER.actor, 'observe')
    server, fleet = server_for(d, port, monkeypatch, auth)
    try:
        assert (await http(port, 'GET', path, tok=auth))[0] == 200
        monkeypatch.setattr(pm_roadmap_reports, 'MAX_CONTENT_BYTES', 1)
        status, large = await http(port, 'GET', path, tok=auth)
        assert status == 413 and large['error']['code'] == 'PM_EXPORT_TOO_LARGE'
        with pytest.raises(OperationError, match='PM_EXPORT_TOO_LARGE'):
            await d.call_api(READ, fields.copy(), OWNER)
        assert 'PM_EXPORT_TOO_LARGE' in await call(server, READ, fields)
        d.ops.db.execute("UPDATE tasks SET controller_authority='{}' WHERE task_id=?", (source['task_id'],))
        hidden = (await http(port, 'GET', path, tok=auth))
        assert hidden[0] == 404 and hidden[1]['error']['code'] == 'PM_REPORT_SOURCE_UNAVAILABLE'
        with pytest.raises(OperationError, match='PM_REPORT_SOURCE_UNAVAILABLE'):
            await d.call_api(READ, fields.copy(), OWNER)
        assert 'PM_REPORT_SOURCE_UNAVAILABLE' in await call(server, READ, fields)
        monkeypatch.setattr(pm_roadmap_reports, 'MAX_CONTENT_BYTES', 1_500_000)
        assert await http(port, 'GET', path, tok=auth) == hidden
    finally:
        await fleet.close()
