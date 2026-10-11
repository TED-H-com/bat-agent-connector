"""Fixed Follow-up preparation across public entries and original source access."""
import json
import uuid

import pytest

from bat_agent_connector import controller_authority, mcp_server, pm_follow_up, pm_plans
from bat_agent_connector.operations import OperationError
from tests.operation_helpers import settle_operations
from tests.test_api_v1 import http, token, write_frames
from tests.test_pm_discussions import CONTRIBUTOR, OWNER, READER, member
from tests.test_pm_evidence import capture, link, task
from tests.test_pm_governance_api import send
from tests.test_pm_run_api import unpack
from tests.test_pm_snapshots import snapshot
from tests.test_pm_sprint_api import pm as pm_fixture
from tests.test_pm_sprint_api import prepared
from tests.test_pm_sprint_api import served as served_fixture
from tests.test_pm_sprint_api import sprint_clock as sprint_clock_fixture
from tests.test_pm_sprint_api import world as repository_world

pm, world, served, sprint_clock = pm_fixture, repository_world, served_fixture, sprint_clock_fixture


def request(d, wid, snap, **params):
    return {'action': 'pm.follow_up.prepare', 'target': {'workspace_id': wid},
            'params': {'snapshot_id': snap['snapshot_id'], **params},
            'preconditions': {'expected_version': pm_follow_up.head(d.ops.db, wid)['version'],
                              'expected_plan_version': pm_plans.head(d.ops.db, wid)['version']},
            'idempotency_key': uuid.uuid4().hex}


async def setup(d, selection):
    # Public plan adoption commits and reads back a real temporary Git repository.
    data = await prepared(d, selection)
    wid = data['workspace_id']
    return data, await snapshot(d.ops, wid)


async def test_follow_up_http_rpc_mcp_read_parity_and_no_read_effects(served, monkeypatch, mock):
    d, port, selection = served
    data, snap = await setup(d, selection)
    wid = data['workspace_id']
    assert pm_follow_up.latest_intake(d.ops, OWNER, wid)['intake'] is None
    op = await send(d, port, request(d, wid, snap), CONTRIBUTOR)
    iid = op['result']['intake_id']
    auth = token(d, READER.actor, 'observe')
    monkeypatch.setenv('BATC_TASK_URL', f'http://127.0.0.1:{port}/rpc')
    monkeypatch.setenv('BATC_API_TOKEN', auth)
    server, fleet = mcp_server.build_server(d.fleet.config, read_only=True, principal_only=True)
    before = d.ops.db.total_changes
    try:
        tools = {tool.name: tool for tool in await server.list_tools()}
        for method, fields, suffix in [
            ('pm_follow_up_list', {'after': 0, 'limit': 1}, '?after=0&limit=1'),
            ('pm_follow_up_get', {'intake_id': iid}, '/' + iid),
            ('pm_follow_up_latest_get', {}, '/latest'),
        ]:
            assert tools[method].annotations.read_only_hint
            status, expected = await http(port, 'GET', f'/api/v1/pm/workspaces/{wid}/follow-ups' + suffix, tok=auth)
            assert status == 200, expected
            params = {'workspace_id': wid, **fields}
            assert await d.call_api(method, params, READER) == expected
            assert unpack(await server.call_tool(method, params)) == expected
        assert d.ops.db.total_changes == before
        assert not write_frames(mock)
    finally:
        await fleet.close()


async def test_preparation_replay_and_cas_do_not_change_plan_or_runtime_mode(served, mock):
    d, port, selection = served
    data, snap = await setup(d, selection)
    wid = data['workspace_id']
    head = pm_plans.head(d.ops.db, wid)
    threads = [dict(row) for row in d.ops.db.execute('SELECT * FROM pm_threads WHERE workspace_id=?', (wid,))]
    first_request = request(d, wid, snap)
    first = await send(d, port, first_request, CONTRIBUTOR)
    stale = {**first_request, 'idempotency_key': uuid.uuid4().hex}
    body = {key: value for key, value in stale.items() if key != 'idempotency_key'}
    status, result = await http(port, 'POST', '/api/v1/operations', tok=token(d, CONTRIBUTOR.actor, *CONTRIBUTOR.scopes),
                                body=body, headers={'Idempotency-Key': stale['idempotency_key']})
    assert status == 409 and result['error']['code'] == 'VERSION_CONFLICT'
    second = await send(d, port, request(d, wid, snap, previous_intake_id=first['result']['intake_id']), CONTRIBUTOR)
    replay = await send(d, port, first_request, CONTRIBUTOR)
    assert replay['operation_id'] == first['operation_id'] and replay['result'] == first['result']
    assert second['result']['version'] == 2
    assert d.ops.db.execute('SELECT COUNT(*) FROM pm_follow_up_intakes WHERE workspace_id=?', (wid,)).fetchone()[0] == 2
    document = pm_follow_up.latest_intake(d.ops, OWNER, wid)['intake']['document']
    assert not document['runtime_invoked'] and not document['execution_authorized'] and not document['accepted_plan_changed']
    assert pm_plans.head(d.ops.db, wid) == head
    assert [dict(row) for row in d.ops.db.execute('SELECT * FROM pm_threads WHERE workspace_id=?', (wid,))] == threads
    auth = token(d, READER.actor, 'observe')
    assert (await http(port, 'GET', '/api/v1/capabilities', tok=auth))[1]['features']['pm_workspaces']['follow_up_prepare']
    profiles = (await http(port, 'GET', '/api/v1/pm/profiles', tok=auth))[1]
    assert 'follow_up' not in profiles['executable_modes'] and not profiles['runtime_available']
    assert not write_frames(mock)


async def test_exact_queries_types_and_current_membership(served):
    d, port, selection = served
    data, snap = await setup(d, selection)
    wid = data['workspace_id']
    op = await send(d, port, request(d, wid, snap), CONTRIBUTOR)
    iid, auth = op['result']['intake_id'], token(d, READER.actor, 'observe')
    prefix = f'/api/v1/pm/workspaces/{wid}/follow-ups'
    for suffix in ['?after=-1', '?after=', '?after=0&after=1', '?limit=0', '?limit=', '?limit=&limit=1',
                   '?actor=', '?actor=owner', '?thread_id=pmt_1', '/latest?after=0', '/latest?actor=', '/' + iid + '?limit=1']:
        assert (await http(port, 'GET', prefix + suffix, tok=auth))[0] == 422, suffix
    for params in [{'limit': True}, {'limit': None}, {'after': '0'}, {'after': None}, {'actor': OWNER.actor}]:
        with pytest.raises(OperationError, match='INVALID_PARAMS'):
            await d.call_api('pm_follow_up_list', {'workspace_id': wid, **params}, READER)
    for method in ['pm_follow_up_get', 'pm_follow_up_latest_get']:
        with pytest.raises(OperationError, match='INVALID_PARAMS'):
            await d.call_api(method, {'workspace_id': wid, 'after': 0}, READER)
    await member(d.ops, {'workspace_id': wid}, READER.actor, None)
    for suffix in ['', '/latest', '/' + iid]:
        assert (await http(port, 'GET', prefix + suffix, tok=auth))[0] == 404
    assert (await http(port, 'GET', '/api/v1/operations/' + op['operation_id'], tok=auth))[0] == 404
    assert (await http(port, 'GET', prefix, tok=token(d, OWNER.actor, 'manage')))[0] == 403


async def test_original_governance_source_withdrawal_hides_intakes_operations_and_events(served, monkeypatch):
    from tests.test_mcp_principal import call

    d, port, selection = served
    data, public_snapshot = await setup(d, selection)
    wid, work = data['workspace_id'], {'workspace_id': data['workspace_id'], 'work_item_id': data['work'][0]}
    with controller_authority.use(controller_authority.binding(d.ops.db, OWNER), d.journal):
        source = task(d.ops)
        await link(d.ops, work, 'task', source['task_id'])
        await capture(d.ops, work)
    private_snapshot = await snapshot(d.ops, wid)
    finding = await send(d, port, {'action': 'pm.governance.record', 'target': {'workspace_id': wid},
        'params': {'kind': 'risk', 'root_cause_key': 'follow-up-private-source', 'snapshot_id': private_snapshot['snapshot_id'],
                   'title': 'Private finding', 'description': 'PRIVATE FOLLOW-UP SOURCE QUOTE', 'owner_actor': CONTRIBUTOR.actor,
                   'source_refs': [{'kind': 'requirement', 'id': 'membership.login'}], 'rationale': 'Preserve this source boundary'},
        'preconditions': {'expected_plan_version': pm_plans.head(d.ops.db, wid)['version']}}, CONTRIBUTOR)
    original = request(d, wid, public_snapshot, governance_revision_ids=[finding['result']['revision_id']])
    op = await send(d, port, original, CONTRIBUTOR)
    iid, auth = op['result']['intake_id'], token(d, READER.actor, 'observe')
    monkeypatch.setenv('BATC_TASK_URL', f'http://127.0.0.1:{port}/rpc')
    monkeypatch.setenv('BATC_API_TOKEN', auth)
    server, fleet = mcp_server.build_server(d.fleet.config, read_only=True, principal_only=True)
    prefix = f'/api/v1/pm/workspaces/{wid}/follow-ups'
    try:
        assert 'PRIVATE FOLLOW-UP SOURCE QUOTE' in json.dumps((await http(port, 'GET', prefix + '/' + iid, tok=auth))[1])
        # Also protect an admitted operation before its effect has produced an intake.
        pending_request = request(d, wid, public_snapshot, governance_revision_ids=[finding['result']['revision_id']])
        pending, _ = d.ops.create(CONTRIBUTOR, **pending_request)
        d.ops.db.execute("UPDATE tasks SET controller_authority='{}' WHERE task_id=?", (source['task_id'],))
        for identifier in [op['operation_id'], pending['operation_id']]:
            assert (await http(port, 'GET', '/api/v1/operations/' + identifier, tok=auth))[0] == 404
            with pytest.raises(OperationError):
                await d.call_api('op_get', {'operation_id': identifier}, READER)
            assert 'NOT_FOUND' in await call(server, 'operation_get', {'operation_id': identifier})
        for method, params, suffix in [('pm_follow_up_get', {'intake_id': iid}, '/' + iid),
                                        ('pm_follow_up_latest_get', {}, '/latest')]:
            assert (await http(port, 'GET', prefix + suffix, tok=auth))[0] == 404
            with pytest.raises(OperationError, match='PM_FOLLOW_UP_SOURCE_UNAVAILABLE'):
                await d.call_api(method, {'workspace_id': wid, **params}, READER)
            assert 'PM_FOLLOW_UP_SOURCE_UNAVAILABLE' in await call(server, method, {'workspace_id': wid, **params})
        for method, params, path in [('pm_follow_up_list', {'workspace_id': wid}, prefix),
            ('operations_list', {'action': 'pm.follow_up.prepare'}, '/api/v1/operations?action=pm.follow_up.prepare'),
            ('events_list', {'after': 0, 'limit': 500}, '/api/v1/events?after=0&limit=500')]:
            status, via_http = await http(port, 'GET', path, tok=auth)
            assert status == 200
            rpc_method = {'operations_list': 'op_list', 'events_list': 'api_events'}.get(method, method)
            for encoded in [json.dumps(via_http), json.dumps(await d.call_api(rpc_method, params, READER)), await call(server, method, params)]:
                assert op['operation_id'] not in encoded and pending['operation_id'] not in encoded
                assert iid not in encoded and 'PRIVATE FOLLOW-UP SOURCE QUOTE' not in encoded
        body = {key: value for key, value in original.items() if key != 'idempotency_key'}
        assert (await http(port, 'POST', '/api/v1/operations', tok=token(d, CONTRIBUTOR.actor, *CONTRIBUTOR.scopes),
                           body=body, headers={'Idempotency-Key': original['idempotency_key']}))[0] == 404
        await settle_operations(d.ops)
        assert d.ops.get(op['operation_id'])['result']['intake_id'] == iid
        assert d.ops.db.execute('SELECT COUNT(*) FROM pm_follow_up_intakes WHERE workspace_id=?', (wid,)).fetchone()[0] == 1
    finally:
        await fleet.close()
