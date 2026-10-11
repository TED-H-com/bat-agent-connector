"""Fixed governance sources and actual Git adoption at all shared entry points."""
import uuid

import pytest

from bat_agent_connector import api_auth, mcp_server, pm_governance, pm_plans
from bat_agent_connector.operations import OperationError
from tests.operation_helpers import settle_operations
from tests.test_api_v1 import http, token, write_frames
from tests.test_pm_discussions import CONTRIBUTOR, OWNER, READER, member
from tests.test_pm_plan_api import draft_body
from tests.test_pm_run_api import unpack
from tests.test_pm_snapshots import snapshot
from tests.test_pm_sprint_api import pm as pm_fixture
from tests.test_pm_sprint_api import prepared
from tests.test_pm_sprint_api import served as served_fixture
from tests.test_pm_sprint_api import sprint_clock as sprint_clock_fixture
from tests.test_pm_sprint_api import world as repository_world

pm, world, served, sprint_clock = pm_fixture, repository_world, served_fixture, sprint_clock_fixture


async def send(d, port, body, principal=OWNER):
    body = body.copy()
    key = body.pop('idempotency_key', uuid.uuid4().hex)
    status, payload = await http(port, 'POST', '/api/v1/operations', tok=token(d, principal.actor, *principal.scopes),
                                 body=body, headers={'Idempotency-Key': key})
    assert status in {200, 202}, payload
    await settle_operations(d.ops)
    op = d.ops.get(payload['operation']['operation_id'])
    assert op['status'] == 'succeeded', op
    return op


async def finding(d, port, selection, **changes):
    data = await prepared(d, selection)
    wid = data['workspace_id']
    snap = await snapshot(d.ops, wid)
    body = {'action': 'pm.governance.record', 'target': {'workspace_id': wid},
            'params': {'kind': 'risk', 'root_cause_key': 'login-quality', 'snapshot_id': snap['snapshot_id'],
                       'title': 'Login verification needs review', 'description': 'A committed requirement still needs quality evidence.',
                       'owner_actor': CONTRIBUTOR.actor, 'source_refs': [{'kind': 'requirement', 'id': 'membership.login'}],
                       'affected_requirement_ids': ['membership.login'], 'rationale': 'Keep the unresolved quality question visible', **changes},
            'preconditions': {'expected_plan_version': pm_plans.head(d.ops.db, wid)['version']}, 'idempotency_key': uuid.uuid4().hex}
    return data, body, await send(d, port, body, CONTRIBUTOR)


async def test_read_parity_and_observation_has_no_effect(served, monkeypatch, mock):
    d, port, selection = served
    data, _, op = await finding(d, port, selection)
    wid, rid = data['workspace_id'], op['result']['record_id']
    auth = token(d, READER.actor, 'observe')
    monkeypatch.setenv('BATC_TASK_URL', f'http://127.0.0.1:{port}/rpc')
    monkeypatch.setenv('BATC_API_TOKEN', auth)
    server, fleet = mcp_server.build_server(d.fleet.config, read_only=True, principal_only=True)
    before = d.ops.db.total_changes
    try:
        for method, fields, suffix in [
            ('pm_governance_list', {'kind': 'risk', 'limit': 1}, '/governance?kind=risk&limit=1'),
            ('pm_governance_get', {'record_id': rid}, '/governance/' + rid),
            ('pm_governance_history_list', {'record_id': rid, 'after': 0, 'limit': 1}, '/governance/' + rid + '/history?after=0&limit=1'),
        ]:
            status, expected = await http(port, 'GET', f'/api/v1/pm/workspaces/{wid}' + suffix, tok=auth)
            assert status == 200, expected
            params = {'workspace_id': wid, **fields}
            assert await d.call_api(method, params, READER) == expected
            assert unpack(await server.call_tool(method, params)) == expected
        assert d.ops.db.total_changes == before
        assert not write_frames(mock)
    finally:
        await fleet.close()


async def test_current_membership_query_types_and_exact_original_key(served):
    d, port, selection = served
    data, original, op = await finding(d, port, selection)
    assert (await send(d, port, original, CONTRIBUTOR))['operation_id'] == op['operation_id']
    wid, rid = data['workspace_id'], op['result']['record_id']
    prefix = f'/api/v1/pm/workspaces/{wid}/governance'
    auth = token(d, READER.actor, 'observe')
    for suffix in ['?after=-1', '?after=', '?limit=0', '?limit=', '?limit=true', '?limit=1&limit=2',
                   '?kind=unverified', '?kind=', '?kind=&kind=risk', '?actor=owner', '?actor=',
                   '/' + rid + '?history_limit=1', '/' + rid + '?history_limit=',
                   '/' + rid + '/history?kind=risk', '/' + rid + '/history?kind=']:
        assert (await http(port, 'GET', prefix + suffix, tok=auth))[0] == 422
    for params in [{'limit': True}, {'after': '0'}, {'after': None}, {'actor': OWNER.actor}]:
        with pytest.raises(OperationError, match='INVALID_PARAMS'):
            await d.call_api('pm_governance_list', {'workspace_id': wid, **params}, READER)
    await member(d.ops, {'workspace_id': wid}, READER.actor, None)
    for suffix in ['', '/' + rid, '/' + rid + '/history']:
        assert (await http(port, 'GET', prefix + suffix, tok=auth))[0] == 404
    assert (await http(port, 'GET', '/api/v1/operations/' + op['operation_id'], tok=auth))[0] == 404
    assert (await http(port, 'GET', prefix, tok=token(d, OWNER.actor, 'manage')))[0] == 403


async def test_direction_then_real_git_adoption_are_distinct_public_actions(served, mock):
    d, port, selection = served
    data = await prepared(d, selection)
    wid = data['workspace_id']
    original = pm_plans.accepted_plan(d.ops, OWNER, wid)['plan']
    snap = await snapshot(d.ops, wid)
    target = {'workspace_id': wid, 'thread_id': original['thread_id']}
    candidate = await send(d, port, draft_body(d, target, markdown='Keep the adopted behavior and inspect its quality.'))
    candidate_id = candidate['result']['revision_id']
    record = await send(d, port, {'action': 'pm.governance.record', 'target': {'workspace_id': wid},
        'params': {'kind': 'change', 'root_cause_key': 'review-direction', 'snapshot_id': snap['snapshot_id'],
                   'title': 'Review the direction explicitly', 'description': 'A new plan document is a proposal.',
                   'owner_actor': CONTRIBUTOR.actor, 'source_refs': [{'kind': 'requirement', 'id': 'membership.login'}],
                   'rationale': 'Keep the proposal separate from adoption', 'proposed_revision_id': candidate_id},
        'preconditions': {'expected_plan_version': pm_plans.head(d.ops.db, wid)['version']}}, CONTRIBUTOR)
    result = record['result']
    target = {k: result[k] for k in ('workspace_id', 'record_id')}
    direction = {'action': 'pm.governance.decide', 'target': target,
                 'params': {'disposition': 'adopted_direction', 'rationale': 'Choose this exact proposal'},
                 'preconditions': {'expected_version': 1}}
    status, _ = await http(port, 'POST', '/api/v1/operations', tok=token(d, CONTRIBUTOR.actor, *CONTRIBUTOR.scopes),
                           body=direction, headers={'Idempotency-Key': uuid.uuid4().hex})
    assert status == 403
    await send(d, port, direction)
    view = pm_governance.get_record(d.ops, OWNER, **target)
    assert view['revision']['document']['scope_applied'] is False
    assert pm_plans.head(d.ops.db, wid)['accepted_revision_id'] == original['revision_id']
    adopter = api_auth.Principal(OWNER.actor, frozenset({'observe', 'manage', 'start'}))
    adoption = await send(d, port, {'action': 'pm.plan.adopt', 'target': {'workspace_id': wid, 'revision_id': candidate_id},
        'params': {'rationale': 'Adopt the selected fixed plan'},
        'preconditions': {'expected_accepted_revision_id': original['revision_id']}}, adopter)
    before = pm_plans.head(d.ops.db, wid)
    linked = await send(d, port, {'action': 'pm.governance.adoption.link', 'target': target,
        'params': {'adoption_operation_id': adoption['operation_id'], 'rationale': 'Link the original successful Git adoption'},
        'preconditions': {'expected_version': view['record']['version']}})
    document = pm_governance.get_record(d.ops, OWNER, **target)['revision']['document']
    assert document['scope_applied'] and document['adoption']['operation_id'] == adoption['operation_id']
    assert document['adoption']['previous_revision_id'] == original['revision_id']
    assert document['verification_status'] == 'unassessed' and not document['execution_authorized']
    assert pm_plans.head(d.ops.db, wid) == before
    assert linked['status'] == 'succeeded' and not write_frames(mock)


async def test_withdrawn_source_hides_all_shared_governance_surfaces(served, monkeypatch):
    from bat_agent_connector import controller_authority
    from tests.test_mcp_principal import call
    from tests.test_pm_evidence import capture, link, task

    d, port, selection = served
    data = await prepared(d, selection)
    wid = data['workspace_id']
    work = {'workspace_id': wid, 'work_item_id': data['work'][0]}
    with controller_authority.use(controller_authority.binding(d.ops.db, OWNER), d.journal):
        source = task(d.ops)
        await link(d.ops, work, 'task', source['task_id'])
        await capture(d.ops, work)
    snap = await snapshot(d.ops, wid)
    request = {'action': 'pm.governance.record', 'target': {'workspace_id': wid},
        'params': {'kind': 'risk', 'root_cause_key': 'private-source', 'snapshot_id': snap['snapshot_id'],
                   'title': 'Private source finding', 'description': 'PRIVATE DERIVED GOVERNANCE QUOTE',
                   'owner_actor': CONTRIBUTOR.actor, 'source_refs': [{'kind': 'requirement', 'id': 'membership.login'}],
                   'rationale': 'Preserve original source permissions'},
        'preconditions': {'expected_plan_version': pm_plans.head(d.ops.db, wid)['version']}, 'idempotency_key': uuid.uuid4().hex}
    op = await send(d, port, request, CONTRIBUTOR)
    rid = op['result']['record_id']
    auth = token(d, READER.actor, 'observe')
    monkeypatch.setenv('BATC_TASK_URL', f'http://127.0.0.1:{port}/rpc')
    monkeypatch.setenv('BATC_API_TOKEN', auth)
    server, fleet = mcp_server.build_server(d.fleet.config, read_only=True, principal_only=True)
    path = f'/api/v1/pm/workspaces/{wid}/governance/{rid}'
    try:
        assert (await http(port, 'GET', path, tok=auth))[0] == 200
        assert 'PRIVATE DERIVED GOVERNANCE QUOTE' in await call(server, 'operation_get', {'operation_id': op['operation_id']})
        d.ops.db.execute("UPDATE tasks SET controller_authority='{}' WHERE task_id=?", (source['task_id'],))
        for endpoint in [path, path + '/history', '/api/v1/operations/' + op['operation_id']]:
            assert (await http(port, 'GET', endpoint, tok=auth))[0] == 404
        for method, params in [('pm_governance_list', {'workspace_id': wid}),
                               ('operations_list', {'action': 'pm.governance.record'}), ('events_list', {'after': 0, 'limit': 500})]:
            result = await call(server, method, params)
            assert 'PRIVATE DERIVED GOVERNANCE QUOTE' not in result
            assert op['operation_id'] not in result
        assert 'NOT_FOUND' in await call(server, 'operation_get', {'operation_id': op['operation_id']})
        # Replaying a hidden operation must not disclose its private source type.
        with pytest.raises(OperationError, match='NOT_FOUND') as replay_error:
            d.ops.create(CONTRIBUTOR, **request)
        assert replay_error.value.status == 404
        assert d.ops.get(op['operation_id'])['result']['record_id'] == rid
    finally:
        await fleet.close()
