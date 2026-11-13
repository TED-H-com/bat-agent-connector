"""Every RPC scope gate precedes lineage, including malformed ID-bearing inputs."""
from __future__ import annotations

import asyncio
import json

import pytest

from bat_agent_connector import api_auth, mcp_server, pm_source_lineage
from bat_agent_connector.operations import OperationError
from bat_agent_connector.task_daemon import API_RPC
from tests.test_api_v1 import daemon, served, token  # noqa: F401
from tests.test_pm_package_handoff import request as handoff_request
from tests.test_pm_work_packages import OWNER, create, pm, read, ready, world  # noqa: F401


async def raw_post(port, path, auth, body):
    payload = json.dumps(body).encode()
    reader, writer = await asyncio.open_connection('127.0.0.1', port)
    writer.write((f'POST {path} HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\n'
        f'Authorization: Bearer {auth}\r\nContent-Length: {len(payload)}\r\n\r\n').encode() + payload)
    try:
        await writer.drain()
        response = await asyncio.wait_for(reader.read(), 10)
    finally:
        writer.close()
        await writer.wait_closed()
    head, _, raw = response.partition(b'\r\n\r\n')
    return int(head.split(b' ')[1]), raw


@pytest.fixture
async def protected(ready):  # noqa: F811
    d, port = ready['d'], ready['port']
    ready['package'] = read(ready, await create(ready))
    # Real registered admission records lineage; cancel before any execution.
    op, _ = d.ops.create(OWNER, **await handoff_request(ready))
    d.ops.cancel(OWNER, op['operation_id'])
    oid = op['operation_id']
    principal = api_auth.Principal(OWNER.actor, frozenset({'manage'}))
    with pytest.raises(OperationError, match='NOT_FOUND'):
        pm_source_lineage.read_request(d.ops.db, principal, {'operation_id': oid})
    assert pm_source_lineage.read_request(d.ops.db, OWNER, {'operation_id': oid}) is None
    return d, port, ready['workspace_id'], oid, principal, token(d, principal.actor, 'manage')


def authentication_sql(d, auth):
    sql = []
    d.ops.db.set_trace_callback(sql.append)
    try:
        api_auth.authenticate(d.ops.db, auth, d._admin_token)
    finally:
        d.ops.db.set_trace_callback(None)
    return sql


@pytest.mark.parametrize('method', sorted(k for k, scope in API_RPC.items() if scope == 'observe'))
async def test_every_observe_rpc_refuses_before_any_lineage_read(protected, method, mock):
    d, port, _, oid, principal, auth = protected
    frame_at = len(mock.invokes)
    sql = []
    d.ops.db.set_trace_callback(sql.append)
    try:
        with pytest.raises(OperationError) as original:
            await d._call_api_bound(method, {}, principal)
    finally:
        d.ops.db.set_trace_callback(None)
    assert sql == []
    assert original.value.status == 403 and original.value.code == 'FORBIDDEN'
    expected = json.dumps({'error': original.value.code, 'message': original.value.message}, ensure_ascii=False).encode()
    auth_sql = authentication_sql(d, auth)
    for params in ({'operation_id': oid}, {'operation_id': 'op_' + 'f' * 32},
            {'body': {'operation_id': oid, 'unexpected': {'original_operation_id': oid}}, 'unexpected_id': oid}):
        sql = []
        before = d.ops.db.total_changes
        d.ops.db.set_trace_callback(sql.append)
        try:
            status, raw = await raw_post(port, '/rpc', auth, {'method': method, 'params': params})
        finally:
            d.ops.db.set_trace_callback(None)
        assert (status, raw) == (400, expected)
        # The gate is even before controller binding: only authentication SQL.
        assert sql == auth_sql
        assert d.ops.db.total_changes == before
    assert len(mock.invokes) == frame_at


async def test_mcp_scope_refusal_uses_the_real_rpc_path(protected, monkeypatch):
    d, port, wid, oid, principal, auth = protected
    monkeypatch.setenv('BATC_TASK_URL', f'http://127.0.0.1:{port}/rpc')
    monkeypatch.setenv('BATC_API_TOKEN', auth)
    server, fleet = mcp_server.build_server(d.fleet.config, principal_only=True)
    replies = []
    try:
        for key in (oid, 'op_' + 'f' * 32):
            sql = []
            d.ops.db.set_trace_callback(sql.append)
            try:
                with pytest.raises(Exception) as error:
                    await server.call_tool('pm_history_continuation_preview',
                        {'workspace_id': wid, 'body': {'operation_id': key}})
            finally:
                d.ops.db.set_trace_callback(None)
            replies.append(str(error.value))
            assert sql == authentication_sql(d, auth)
        assert replies[0] == replies[1]
        assert 'FORBIDDEN' in replies[0] and '預覽需要' in replies[0]
    finally:
        await fleet.close()
