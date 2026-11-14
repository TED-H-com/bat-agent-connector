"""Source grants fence preview lineage reads on every transport."""
from __future__ import annotations

import copy
import json
from dataclasses import replace

import pytest

from bat_agent_connector import api_auth, mcp_server, pm_history_continuation, pm_source_lineage
from bat_agent_connector.operations import OperationError
from bat_agent_connector.session_history_config import HistoryConfig
from tests.test_api_v1 import token
from tests.test_history_continuation_access import db_snapshot, malformed, request_body, retain
from tests.test_rpc_scope_order import daemon, pm, protected, raw_post, ready, served, world  # noqa: F401
from tests.test_work_items import act


@pytest.fixture
async def lineage_context(protected, tmp_path, monkeypatch, mock):  # noqa: F811
    d, port, _, oid, _, _ = protected
    principal = api_auth.Principal('synthetic-other', frozenset({'observe', 'manage'}))
    with pytest.raises(OperationError, match='NOT_FOUND'):
        pm_source_lineage.read_request(d.ops.db, principal, {'operation_id': oid})
    workspace = (await act(d, principal, 'pm.workspace.create', params={'name': 'Synthetic other'}))['result']['workspace']
    wid = workspace['workspace_id']
    source, ref = await retain(d, wid, principal, tmp_path, monkeypatch)
    auth = token(d, principal.actor, *principal.scopes)
    monkeypatch.setenv('BATC_TASK_URL', f'http://127.0.0.1:{port}/rpc')
    monkeypatch.setenv('BATC_API_TOKEN', auth)
    server, fleet = mcp_server.build_server(d.fleet.config, principal_only=True)
    try:
        yield d, port, oid, wid, source, ref, auth, server, mock
    finally:
        await fleet.close()


async def invoke(transport, port, wid, auth, server, body):
    if transport == 'http':
        status, raw = await raw_post(port, f'/api/v1/pm/workspaces/{wid}/history-continuation-preview', auth, body)
        return status, raw
    if transport == 'rpc':
        status, raw = await raw_post(port, '/rpc', auth,
            {'method': 'pm_history_continuation_preview', 'params': {'workspace_id': wid, 'body': body}})
        return status, raw
    with pytest.raises(Exception) as exc:
        await server.call_tool('pm_history_continuation_preview', {'workspace_id': wid, 'body': body})
    return None, str(exc.value).encode()


async def test_revoked_grant_fences_nested_lineage_ids(lineage_context):
    d, port, oid, wid, source, ref, auth, server, mock = lineage_context
    d.history_collector.config = HistoryConfig(10, (replace(source, readers=('different-reader',)),))
    before, frame_at = db_snapshot(d.ops.db), len(mock.invokes)
    message = ' / '.join(pm_history_continuation.REASONS['PM_HISTORY_SOURCE_UNAVAILABLE'])
    expected = {'code': 'PM_HISTORY_SOURCE_UNAVAILABLE', 'message': message}
    for transport in ('http', 'rpc', 'mcp'):
        replies = []
        for identity in (oid, 'op_' + 'f' * 32):
            body = request_body(copy.deepcopy(ref))
            body['context_ref']['operation_id'] = identity
            sql = []
            d.ops.db.set_trace_callback(sql.append)
            try:
                status, raw = await invoke(transport, port, wid, auth, server, body)
            finally:
                d.ops.db.set_trace_callback(None)
            assert any('FROM temp.session_history_current_grants' in statement for statement in sql)
            assert not any('pm_source_lineage' in statement.lower() for statement in sql)
            assert not any('session_history_entries' in statement.lower() for statement in sql)
            if transport == 'http':
                assert status == 404 and json.loads(raw) == {'error': expected}
            elif transport == 'rpc':
                assert status == 400 and json.loads(raw) == {'error': expected['code'], 'message': message}
            else:
                assert expected['code'] in raw.decode() and message in raw.decode()
            replies.append(raw)
            assert db_snapshot(d.ops.db) == before
        assert replies[0] == replies[1]
    assert len(mock.invokes) == frame_at


@pytest.mark.parametrize('shape', ['schema', 'missing-digest', 'unknown-key'])
async def test_current_grant_preserves_reference_validation(lineage_context, shape):
    d, port, _, wid, _, ref, auth, server, mock = lineage_context
    body = malformed(request_body(ref), shape)
    before, frame_at = db_snapshot(d.ops.db), len(mock.invokes)
    message = ' / '.join(pm_history_continuation.REASONS['PM_HISTORY_REFERENCE_INVALID'])
    for transport in ('http', 'rpc', 'mcp'):
        status, raw = await invoke(transport, port, wid, auth, server, body)
        if transport == 'http':
            assert status == 422 and json.loads(raw) == {'error': {'code': 'PM_HISTORY_REFERENCE_INVALID', 'message': message}}
        elif transport == 'rpc':
            assert status == 400 and json.loads(raw) == {'error': 'PM_HISTORY_REFERENCE_INVALID', 'message': message}
        else:
            assert 'PM_HISTORY_REFERENCE_INVALID' in raw.decode() and message in raw.decode()
        assert db_snapshot(d.ops.db) == before
    assert len(mock.invokes) == frame_at


async def test_current_grant_retains_existing_lineage_protection(lineage_context):
    d, port, oid, wid, _, ref, auth, server, mock = lineage_context
    body = request_body(copy.deepcopy(ref))
    body['context_ref']['operation_id'] = oid
    before, frame_at = db_snapshot(d.ops.db), len(mock.invokes)
    for transport in ('rpc', 'mcp'):
        status, raw = await invoke(transport, port, wid, auth, server, body)
        if transport == 'rpc':
            assert status == 400 and json.loads(raw)['error'] == 'NOT_FOUND'
        else:
            assert 'NOT_FOUND' in raw.decode()
        assert db_snapshot(d.ops.db) == before
    assert len(mock.invokes) == frame_at
