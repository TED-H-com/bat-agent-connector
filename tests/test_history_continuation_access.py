"""Grant-first refusals over real HTTP, RPC and MCP with retained synthetic history."""
from __future__ import annotations

import copy
import json
from dataclasses import replace

import pytest

from bat_agent_connector import mcp_server
from bat_agent_connector import pm_external_history as refs
from bat_agent_connector.operations import OperationError
from bat_agent_connector.session_history_config import HistoryConfig, HistorySource
from tests.test_api_v1 import daemon, served, token  # noqa: F401
from tests.test_external_history import SESSION, append, message
from tests.test_pm_api import OWNER
from tests.test_rpc_scope_order import raw_post
from tests.test_work_items import act


async def retain(d, wid, principal, root, monkeypatch):
    source = HistorySource('grant-history', wid, 'codex-history-jsonl-v1', root / 'history.jsonl', SESSION, (principal.actor,))
    append(source.path, {'type': 'session_meta', 'payload': {'id': SESSION}}, message('Selected synthetic context.'))
    d.history_collector.config = HistoryConfig(10, (source,))
    monkeypatch.setattr(d.journal, 'owner_valid', lambda: True)
    await d.history_collector.collect_once()
    entry = d.ops.db.execute('SELECT entry_id,content_digest FROM session_history_entries WHERE source_id=?', (source.source_id,)).fetchone()
    fixed = refs.preview(d.ops.db, principal, wid, source.source_id, entry['entry_id'], entry['content_digest'], {'kind': 'entry'})
    return source, fixed['ref']


def request_body(ref, pid='prj_' + '1' * 20, sha='c' * 40):
    return {'context_ref': ref, 'project_id': pid,
        'code_target': {'repository': 'o/r', 'host': 'h1', 'workspace_id': 'ws-1', 'source_ref': 'refs/heads/main'},
        'expected_source_sha': sha, 'requirement_ids': ['member.login'],
        'new_work': {'title': 'Continue', 'goal': 'Account access', 'acceptance': 'Test'},
        'instruction': 'Implement selected requirements.', 'agent': 'claude', 'delivery': 'reviewable_changes'}


def db_snapshot(db):
    return {schema: {row[0]: [tuple(r) for r in db.execute(f'SELECT * FROM {schema}."{row[0]}"')]
        for row in db.execute(f"SELECT name FROM {schema}.sqlite_master WHERE type='table' ORDER BY name").fetchall()}
        for schema in ('main', 'temp')}


@pytest.fixture
async def access(served, tmp_path, monkeypatch):  # noqa: F811
    d, port = served
    workspace = (await act(d, OWNER, 'pm.workspace.create', params={'name': 'Access fixture'}))['result']['workspace']
    wid = workspace['workspace_id']
    source, ref = await retain(d, wid, OWNER, tmp_path, monkeypatch)
    await act(d, OWNER, 'pm.workspace.member.set', {'workspace_id': wid}, {'actor': 'local-admin', 'role': 'reader'},
        {'expected_version': workspace['version']})
    return d, port, wid, source, ref


SHAPES = ('valid', 'schema', 'missing-digest', 'unknown-key', 'entry-type', 'server',
    'selection', 'entry-size', 'reference-type', 'target-size')


def malformed(body, shape):
    body = copy.deepcopy(body)
    ref = body['context_ref']
    if shape == 'schema':
        ref['schema'] = 'other'
    elif shape == 'missing-digest':
        del ref['excerpt_digest']
    elif shape == 'unknown-key':
        ref['unexpected'] = 'synthetic'
    elif shape == 'entry-type':
        ref['entry_id'] = []
    elif shape == 'server':
        ref['server_id'] = 'another-server'
    elif shape == 'selection':
        ref['selection'] = None
    elif shape == 'entry-size':
        ref['entry_id'] = 'x' * 10000
    elif shape == 'reference-type':
        body['context_ref'] = []
    elif shape == 'target-size':
        body['code_target']['host'] = 'x' * 10000
    return body


@pytest.mark.parametrize('role', ['owner', 'admin', 'no-observe', 'outsider'])
@pytest.mark.parametrize('shape', SHAPES)
async def test_unavailable_matrix_has_identical_raw_refusals(access, monkeypatch, mock, role, shape):
    d, port, wid, source, ref = access
    auth = {'owner': lambda: token(d, OWNER.actor, *OWNER.scopes), 'admin': lambda: d._admin_token,
        'no-observe': lambda: token(d, OWNER.actor, 'manage'),
        'outsider': lambda: token(d, 'outsider', 'observe')}[role]()
    readers = (OWNER.actor,) if role == 'no-observe' else ('outsider',) if role == 'outsider' else ('another-reader',)
    d.history_collector.config = HistoryConfig(10, (replace(source, readers=readers),))
    monkeypatch.setenv('BATC_TASK_URL', f'http://127.0.0.1:{port}/rpc')
    monkeypatch.setenv('BATC_API_TOKEN', auth)
    server, fleet = mcp_server.build_server(d.fleet.config, principal_only=True)
    before = db_snapshot(d.ops.db)
    expected_code = 'FORBIDDEN' if role == 'no-observe' else 'PM_HISTORY_SOURCE_UNAVAILABLE'
    bodies = {transport: [] for transport in ('http', 'rpc', 'mcp')}
    try:
        for target in ('existing', 'missing-workspace', 'missing-source'):
            route_wid = 'ws_' + 'f' * 32 if target == 'missing-workspace' else wid
            body = malformed(request_body(ref), shape)
            if isinstance(body['context_ref'], dict):
                body['context_ref']['workspace_id'] = route_wid
                if target == 'missing-source':
                    body['context_ref']['source_id'] = 'missing-history'
            for transport in bodies:
                sql = []
                d.ops.db.set_trace_callback(sql.append)
                try:
                    if transport == 'http':
                        status, raw = await raw_post(port, f'/api/v1/pm/workspaces/{route_wid}/history-continuation-preview', auth, body)
                        assert status == (403 if role == 'no-observe' else 404)
                        error = json.loads(raw)['error']
                    elif transport == 'rpc':
                        status, raw = await raw_post(port, '/rpc', auth,
                            {'method': 'pm_history_continuation_preview', 'params': {'workspace_id': route_wid, 'body': body}})
                        assert status == 400
                        doc = json.loads(raw)
                        error = {'code': doc['error'], 'message': doc['message']}
                    else:
                        with pytest.raises(Exception) as exc:
                            await server.call_tool('pm_history_continuation_preview', {'workspace_id': route_wid, 'body': body})
                        raw = str(exc.value).encode()
                        assert expected_code in str(exc.value)
                        error = None
                finally:
                    d.ops.db.set_trace_callback(None)
                if error:
                    assert error['code'] == expected_code
                    assert ' / ' in error['message']
                bodies[transport].append(raw)
                assert not any('session_history_entries' in statement or 'pm_project' in statement for statement in sql)
                if role in ('owner', 'admin') and target != 'missing-workspace' and shape != 'reference-type':
                    assert any('FROM temp.session_history_current_grants' in statement for statement in sql)
                assert db_snapshot(d.ops.db) == before
        for values in bodies.values():
            assert values[0] == values[1] == values[2]
        assert json.loads(bodies['http'][0])['error']['message'] == json.loads(bodies['rpc'][0])['message']
        assert json.loads(bodies['rpc'][0])['message'] in bodies['mcp'][0].decode()
        assert not mock.invokes
    finally:
        await fleet.close()


@pytest.mark.parametrize('shape', ['schema', 'missing-digest', 'unknown-key'])
async def test_current_grant_keeps_reference_validation_422(access, shape):
    d, _, _, _, ref = access
    invalid = malformed(request_body(ref), shape)['context_ref']
    sql = []
    d.ops.db.set_trace_callback(sql.append)
    try:
        with pytest.raises(OperationError) as error:
            refs.resolve(d.ops.db, OWNER, invalid)
    finally:
        d.ops.db.set_trace_callback(None)
    assert error.value.code == 'PM_HISTORY_REFERENCE_INVALID' and error.value.status == 422
    assert any('FROM temp.session_history_current_grants' in statement for statement in sql)
    # Source readability uses retained-entry aggregates; it must not fetch entry bodies.
    assert not any('SELECT content,content_digest' in statement for statement in sql)


@pytest.mark.parametrize('source_id', [None, [], {}, True, 1, '', 'x' * 129])
async def test_unaddressable_source_is_unavailable(access, source_id):
    d, _, _, _, ref = access
    invalid = dict(ref, source_id=source_id)
    if source_id is None:
        del invalid['source_id']
    with pytest.raises(OperationError, match='PM_HISTORY_SOURCE_UNAVAILABLE'):
        refs.resolve(d.ops.db, OWNER, invalid)
