"""Engineering package preparation and original source access across shared entries."""
import asyncio
import json
import uuid

import pytest

from bat_agent_connector import controller_authority, mcp_server, pm_plans, work_items
from bat_agent_connector import pm_work_packages as packages
from bat_agent_connector.operations import OperationError
from tests.operation_helpers import settle_operations
from tests.test_api_v1 import http, token, write_frames
from tests.test_pm_governance_api import send
from tests.test_pm_run_api import unpack
from tests.test_pm_work_packages import CONTRIBUTOR, OWNER, READER, add_task_evidence, member, request
from tests.test_pm_work_packages import pm as pm_fixture
from tests.test_pm_work_packages import ready as ready_fixture
from tests.test_pm_work_packages import world as world_fixture

pm, ready, world = pm_fixture, ready_fixture, world_fixture


@pytest.fixture
async def served(ready):
    w = ready
    await member(w, READER.actor, 'reader')
    await member(w, CONTRIBUTOR.actor, 'contributor')
    server = await asyncio.start_server(w['d']._handle, '127.0.0.1', 0)
    try:
        yield w, server.sockets[0].getsockname()[1]
    finally:
        server.close()
        await server.wait_closed()


async def test_fixed_package_http_rpc_mcp_parity_without_read_effects(served, monkeypatch, mock):
    w, port = served
    d, wid = w['d'], w['workspace_id']
    op = await send(d, port, request(w), CONTRIBUTOR)
    pid = op['result']['package_id']
    auth = token(d, READER.actor, 'observe')
    monkeypatch.setenv('BATC_TASK_URL', f'http://127.0.0.1:{port}/rpc')
    monkeypatch.setenv('BATC_API_TOKEN', auth)
    server, fleet = mcp_server.build_server(d.fleet.config, read_only=True, principal_only=True)
    before, calls = d.ops.db.total_changes, len(w['runner'].phases)
    try:
        tools = {tool.name: tool for tool in await server.list_tools()}
        for method, fields, suffix in [
            ('pm_work_packages_list', {'work_item_id': w['target']['work_item_id'], 'after': 0, 'limit': 1},
             '?work_item_id=' + w['target']['work_item_id'] + '&after=0&limit=1'),
            ('pm_work_package_get', {'package_id': pid}, '/' + pid),
            ('pm_work_package_readiness_get', {'package_id': pid}, '/' + pid + '/readiness'),
        ]:
            assert tools[method].annotations.read_only_hint
            status, expected = await http(port, 'GET', f'/api/v1/pm/workspaces/{wid}/work-packages' + suffix, tok=auth)
            assert status == 200, expected
            params = {'workspace_id': wid, **fields}
            assert await d.call_api(method, params, READER) == expected
            assert unpack(await server.call_tool(method, params)) == expected
            if method == 'pm_work_package_readiness_get':
                assert expected['status'] == 'ready' and expected['ready_for'] == 'engineering_handoff_review'
                assert not expected['execution_authorized'] and not expected['execution_available']
                assert expected['dispatch_readiness'] == 'separate_fixed_handoff_admission'
                assert expected['coverage']['quality'] == 'unassessed'
                assert expected['handoff'] == {
                    'action': 'repository.continue', 'contract': 'pm-engineering-handoff/v1',
                    'code_basis_required': True, 'compatibility': 'unverified',
                    'source_materialization': 'bounded_full_prompt'}
        assert d.ops.db.total_changes == before and len(w['runner'].phases) == calls
        assert not write_frames(mock)
    finally:
        await fleet.close()


async def test_public_package_replay_cas_and_readiness_are_not_execution(served, mock):
    w, port = served
    d, wid = w['d'], w['workspace_id']
    head = pm_plans.head(d.ops.db, wid)
    work = work_items.work_item_get(d.ops.db, w['target']['work_item_id'])
    counts = {table: d.ops.db.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]
              for table in ('tasks', 'artifacts', 'pm_runs', 'pm_messages')}
    original = request(w)
    one = await send(d, port, original, CONTRIBUTOR)
    body = {key: value for key, value in original.items() if key != 'idempotency_key'}
    status, conflict = await http(port, 'POST', '/api/v1/operations', tok=token(d, CONTRIBUTOR.actor, *CONTRIBUTOR.scopes),
                                   body=body, headers={'Idempotency-Key': uuid.uuid4().hex})
    assert status == 409 and conflict['error']['code'] == 'PM_PACKAGE_VERSION_CONFLICT'
    two = await send(d, port, request(w, previous=one['result']['package_id'], unresolved=['Review a remaining design choice']), CONTRIBUTOR)
    calls = len(w['runner'].phases)
    replay = await send(d, port, original, CONTRIBUTOR)
    assert replay['operation_id'] == one['operation_id'] and replay['result'] == one['result']
    assert len(w['runner'].phases) == calls
    assert one['params'] == original['params'] and one['preconditions'] == original['preconditions']
    assert d.ops.db.execute('SELECT COUNT(*) FROM pm_work_packages').fetchone()[0] == 2
    auth = token(d, READER.actor, 'observe')
    for op, blocked in [(one, 'package_revision'), (two, 'unresolved')]:
        status, check = await http(port, 'GET', f'/api/v1/pm/workspaces/{wid}/work-packages/{op["result"]["package_id"]}/readiness', tok=auth)
        assert status == 200 and check['status'] == 'blocked'
        assert any(row['check'] == blocked and row['status'] == 'blocked' for row in check['checks'])
        assert not check['execution_authorized']
    assert pm_plans.head(d.ops.db, wid) == head
    assert work_items.work_item_get(d.ops.db, w['target']['work_item_id']) == work
    assert all(d.ops.db.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0] == count for table, count in counts.items())
    assert (await http(port, 'GET', '/api/v1/capabilities', tok=auth))[1]['features']['pm_workspaces']['work_package_prepare']
    assert not write_frames(mock)


async def test_exact_package_queries_and_current_membership(served):
    w, port = served
    d, wid = w['d'], w['workspace_id']
    op = await send(d, port, request(w), CONTRIBUTOR)
    pid, auth = op['result']['package_id'], token(d, READER.actor, 'observe')
    prefix = f'/api/v1/pm/workspaces/{wid}/work-packages'
    for suffix in ['?after=-1', '?after=', '?after=0&after=1', '?limit=0', '?limit=', '?limit=&limit=1',
                   '?actor=', '?execute=true', '?work_item_id=', '?work_item_id=wi_bad',
                   '?work_item_id=' + w['target']['work_item_id'] + '&work_item_id=' + w['target']['work_item_id'],
                   '/' + pid + '?limit=1', '/' + pid + '?actor=', '/' + pid + '/readiness?after=0',
                   '/' + pid + '/readiness?execute=']:
        assert (await http(port, 'GET', prefix + suffix, tok=auth))[0] == 422, suffix
    for params in [{'limit': True}, {'limit': None}, {'after': '0'}, {'after': None}, {'actor': OWNER.actor}, {'work_item_id': False}]:
        with pytest.raises(OperationError, match='INVALID_PARAMS'):
            await d.call_api('pm_work_packages_list', {'workspace_id': wid, **params}, READER)
    for method in ['pm_work_package_get', 'pm_work_package_readiness_get']:
        with pytest.raises(OperationError, match='INVALID_PARAMS'):
            await d.call_api(method, {'workspace_id': wid, 'package_id': pid, 'after': 0}, READER)
    denied = request(w, previous=pid)
    status, _ = await http(port, 'POST', '/api/v1/operations', tok=token(d, READER.actor, *READER.scopes),
                          body={k: v for k, v in denied.items() if k != 'idempotency_key'}, headers={'Idempotency-Key': denied['idempotency_key']})
    assert status == 403  # Observe/manage scopes do not turn a workspace reader into a writer.
    await member(w, READER.actor, None)
    for suffix in ['', '/' + pid, '/' + pid + '/readiness']:
        assert (await http(port, 'GET', prefix + suffix, tok=auth))[0] == 404
    assert (await http(port, 'GET', '/api/v1/operations/' + op['operation_id'], tok=auth))[0] == 404
    assert (await http(port, 'GET', prefix, tok=token(d, OWNER.actor, 'manage')))[0] == 403


async def test_original_and_predecessor_source_withdrawal_hides_every_entry(served, monkeypatch):
    from tests.test_mcp_principal import call

    w, port = served
    d, wid = w['d'], w['workspace_id']
    with controller_authority.use(controller_authority.binding(d.ops.db, OWNER), d.journal):
        source, evidence = await add_task_evidence(w)
    original = request(w, evidence_refs=[evidence], rationale='PRIVATE ENGINEERING SOURCE QUOTE')
    one = await send(d, port, original, CONTRIBUTOR)
    two = await send(d, port, request(w, previous=one['result']['package_id'], evidence_refs=[],
                                     rationale='PRIVATE ENGINEERING SOURCE QUOTE retained in revision'), CONTRIBUTOR)
    auth = token(d, READER.actor, 'observe')
    prefix = f'/api/v1/pm/workspaces/{wid}/work-packages'
    monkeypatch.setenv('BATC_TASK_URL', f'http://127.0.0.1:{port}/rpc')
    monkeypatch.setenv('BATC_API_TOKEN', auth)
    server, fleet = mcp_server.build_server(d.fleet.config, read_only=True, principal_only=True)
    try:
        assert (await http(port, 'GET', prefix + '/' + two['result']['package_id'], tok=auth))[0] == 200
        pending, _ = d.ops.create(CONTRIBUTOR, **request(w, previous=two['result']['package_id']))
        d.ops.db.execute("UPDATE tasks SET controller_authority='{}' WHERE task_id=?", (source['task_id'],))
        operations = [one, two, pending]
        for op in operations:
            oid = op['operation_id']
            assert (await http(port, 'GET', '/api/v1/operations/' + oid, tok=auth))[0] == 404
            with pytest.raises(OperationError):
                await d.call_api('op_get', {'operation_id': oid}, READER)
            assert 'NOT_FOUND' in await call(server, 'operation_get', {'operation_id': oid})
        for op in (one, two):
            pid = op['result']['package_id']
            for method, suffix in [('pm_work_package_get', '/' + pid), ('pm_work_package_readiness_get', '/' + pid + '/readiness')]:
                assert (await http(port, 'GET', prefix + suffix, tok=auth))[0] == 404
                with pytest.raises(OperationError) as error:
                    await d.call_api(method, {'workspace_id': wid, 'package_id': pid}, READER)
                assert error.value.status == 404
                assert error.value.code in await call(server, method, {'workspace_id': wid, 'package_id': pid})
        for method, params, path in [('pm_work_packages_list', {'workspace_id': wid}, prefix),
            ('operations_list', {'action': packages.ACTION}, '/api/v1/operations?action=' + packages.ACTION),
            ('events_list', {'after': 0, 'limit': 500}, '/api/v1/events?after=0&limit=500')]:
            status, via_http = await http(port, 'GET', path, tok=auth)
            assert status == 200
            rpc_method = {'operations_list': 'op_list', 'events_list': 'api_events'}.get(method, method)
            for encoded in [json.dumps(via_http), json.dumps(await d.call_api(rpc_method, params, READER)), await call(server, method, params)]:
                assert all(op['operation_id'] not in encoded for op in operations)
                assert all(op['result']['package_id'] not in encoded for op in (one, two))
                assert 'PRIVATE ENGINEERING SOURCE QUOTE' not in encoded
        page = (await http(port, 'GET', prefix + '?limit=1', tok=auth))[1]
        assert not page['packages'] and page['next_cursor'] and page['coverage']['visibility_filtered']
        tail = (await http(port, 'GET', prefix + '?after=' + str(page['next_cursor']), tok=auth))[1]
        assert not tail['packages'] and tail['next_cursor'] is None
        assert (await http(port, 'POST', '/api/v1/operations', tok=token(d, CONTRIBUTOR.actor, *CONTRIBUTOR.scopes),
                          body={k: v for k, v in original.items() if k != 'idempotency_key'},
                          headers={'Idempotency-Key': original['idempotency_key']}))[0] == 404
        await settle_operations(d.ops)
        assert d.ops.get(one['operation_id'])['result'] == one['result']
        assert d.ops.db.execute('SELECT COUNT(*) FROM pm_work_packages').fetchone()[0] == 2
    finally:
        await fleet.close()
