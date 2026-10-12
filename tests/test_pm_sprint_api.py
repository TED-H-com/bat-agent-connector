"""The same fixed Sprint commitments and current authority at HTTP/RPC/MCP."""
import asyncio
import datetime as dt
import json
from types import SimpleNamespace

import pytest

from bat_agent_connector import api_auth, mcp_server, pm_sprints
from bat_agent_connector.operations import OperationError
from tests.operation_helpers import settle_operations
from tests.test_api_v1 import http, token, write_frames
from tests.test_pm_discussions import CONTRIBUTOR, OWNER, READER, member
from tests.test_pm_evidence import bind_project, bind_work, item, project
from tests.test_pm_plan_api import draft_body, select_planning
from tests.test_pm_run_api import unpack
from tests.test_pm_sprints import END, NOW, create, create_request, request, revise, selections
from tests.test_pm_worktree import pm as pm_fixture
from tests.test_pm_worktree import world as repository_world
from tests.test_work_items import act

pm, world = pm_fixture, repository_world


@pytest.fixture
async def served(pm):
    d = pm['d']
    server = await asyncio.start_server(d._handle, '127.0.0.1', 0)
    try:
        yield d, server.sockets[0].getsockname()[1], pm['selection']
    finally:
        server.close()
        await server.wait_closed()


async def prepared(d, selection):
    # Real disk Git adoption: the public service's adoption guards stay installed.
    owner = api_auth.Principal(OWNER.actor, frozenset({'observe', 'manage', 'start'}))
    ws = (await act(d, owner, 'pm.workspace.create', params={'name': 'Sprint API', 'repositories': ['o/r']}))['result']['workspace']
    ws = await member(d.ops, ws, CONTRIBUTOR.actor, 'contributor')
    ws = await member(d.ops, ws, READER.actor, 'reader')
    wid = ws['workspace_id']
    thread = await act(d, owner, 'pm.thread.create', {'workspace_id': wid}, {'title': 'Membership'}, {'expected_version': ws['version']})
    target = {'workspace_id': wid, 'thread_id': thread['result']['thread_id']}
    source, _ = await select_planning(d, target, owner)
    await act(d, owner, 'pm.run.prepare', target, {'purpose': 'Prepare fixed scope', 'repository_binding': selection}, {'expected_version': 4})
    body = draft_body(d, target)
    body['params']['requirements'] = [{'id': 'membership.login', 'source_input_id': source}]
    drafted = await act(d, owner, body['action'], target, body['params'], body['preconditions'])
    rid = drafted['result']['revision_id']
    await act(d, owner, 'pm.plan.adopt', {'workspace_id': wid, 'revision_id': rid}, {'rationale': 'Adopt this exact scope'}, {'expected_accepted_revision_id': None})
    pid = await project(d.ops, name='Sprint work ' + wid)
    await bind_project(d.ops, wid, pid)
    work = [await item(d.ops, pid, 'One deliverable')]
    binding = await bind_work(d.ops, {'workspace_id': wid, 'work_item_id': work[0]}, plan_revision_id=rid, requirement_ids=['membership.login'])
    goal = await revise(d.ops, wid, 'product_goal', plan_revision_id=rid, text='Restore member access')
    dod = await revise(d.ops, wid, 'dod', plan_revision_id=rid, criteria=[{'id': 'tested', 'text': 'Verify the fixed change'}])
    bindings = [binding['binding_id']]
    backlog = await revise(d.ops, wid, 'backlog', product_goal_revision_id=goal, binding_ids=bindings)
    return dict(workspace_id=wid, goal=goal, dod=dod, backlog=backlog, bindings=bindings, work=work)


@pytest.fixture(autouse=True)
def sprint_clock(monkeypatch):
    monkeypatch.setattr(pm_sprints, 'time', SimpleNamespace(time=lambda: NOW))


async def test_sprint_read_parity_fixed_artifacts_and_bounded_history(served, monkeypatch, mock):
    d, port, selection = served
    data = await prepared(d, selection)
    target = await create(d.ops, data)
    wid, sid = target['workspace_id'], target['sprint_id']
    prefix = f'/api/v1/pm/workspaces/{wid}'
    auth = token(d, OWNER.actor, 'observe')
    monkeypatch.setenv('BATC_TASK_URL', f'http://127.0.0.1:{port}/rpc')
    monkeypatch.setenv('BATC_API_TOKEN', auth)
    server, fleet = mcp_server.build_server(d.fleet.config, read_only=True, principal_only=True)
    before = d.ops.db.total_changes
    try:
        cases = [
            ('pm_sprint_artifacts_get', {}, '/sprint-artifacts'),
            ('pm_sprint_artifact_revisions_list', {'kind': 'dod', 'after': 0, 'limit': 1}, '/sprint-artifacts/dod?after=0&limit=1'),
            ('pm_sprint_artifact_get', {'kind': 'dod', 'revision_id': data['dod']}, '/sprint-artifacts/dod/' + data['dod']),
            ('pm_sprints_list', {'after': 0, 'limit': 1}, '/sprints?after=0&limit=1'),
            ('pm_sprint_get', {'sprint_id': sid, 'history_limit': 1}, '/sprints/' + sid + '?history_limit=1'),
            ('pm_sprint_history_list', {'sprint_id': sid, 'kind': 'events', 'after': 0, 'limit': 1}, '/sprints/' + sid + '/history/events?after=0&limit=1'),
        ]
        for method, fields, suffix in cases:
            status, expected = await http(port, 'GET', prefix + suffix, tok=auth)
            assert status == 200, expected
            params = {'workspace_id': wid, **fields}
            assert await d.call_api(method, params.copy(), OWNER) == expected
            assert unpack(await server.call_tool(method, params)) == expected
        assert d.ops.db.total_changes == before
        assert not write_frames(mock)
    finally:
        await fleet.close()


@pytest.mark.parametrize('end_action', ['cancel', 'close'])
async def test_http_sprint_end_replay_and_revoked_feed_privacy(served, mock, monkeypatch, end_action):
    d, port, selection = served
    data = await prepared(d, selection)
    auth = token(d, OWNER.actor, *OWNER.scopes)

    async def send(body):
        body = body.copy()
        key = body.pop('idempotency_key')
        status, response = await http(port, 'POST', '/api/v1/operations', tok=auth, body=body, headers={'Idempotency-Key': key})
        assert status in {200, 202}, response
        await settle_operations(d.ops)
        result = d.ops.get(response['operation']['operation_id'])
        assert result['status'] == 'succeeded', result
        return result

    created = await send(create_request(d.ops, data))
    target = {'workspace_id': data['workspace_id'], 'sprint_id': created['result']['sprint_id']}
    await send(request(d.ops, target, 'select', items=selections(data)))
    await send(request(d.ops, target, 'start'))
    original = pm_sprints.get_sprint(d.ops, OWNER, **target)
    if end_action == 'close':
        end_time = dt.datetime.fromisoformat(END.replace('Z', '+00:00')).timestamp()
        monkeypatch.setattr(pm_sprints, 'time', SimpleNamespace(time=lambda: end_time))
    finish = request(d.ops, target, end_action)
    ended = await send(finish)
    assert (await send(finish))['operation_id'] == ended['operation_id']
    after = pm_sprints.get_sprint(d.ops, OWNER, **target)
    assert after['sprint']['status'] == ('cancelled' if end_action == 'cancel' else 'closed')
    assert after['goal'] == original['goal'] and after['selected_items'] == original['selected_items']
    assert after['history'][-1]['document']['automatic_carryover'] is False
    assert after['history'][-1]['document']['items'][0]['counts_as_done'] is False
    assert not after['execution_authorized'] and after['quality'] == 'unassessed'
    assert d.ops.db.execute('SELECT count(*) FROM tasks').fetchone()[0] == 0
    assert not write_frames(mock)
    # No optional quality ref is needed to keep the original workspace private.
    reader = token(d, READER.actor, 'observe')
    for role in ('reader', None, 'reader'):
        await member(d.ops, {'workspace_id': target['workspace_id']}, READER.actor, role)
        status, events = await http(port, 'GET', '/api/v1/events?after=0&limit=500', tok=reader)
        assert status == 200
        assert (ended['operation_id'] in json.dumps(events)) == (role is not None)
        assert (any(event['kind'] == 'pm.sprint.' + end_action for event in events['events'])) == (role is not None)


async def test_sprint_membership_rechecked_and_foreign_sources_stay_private(served):
    d, port, selection = served
    data = await prepared(d, selection)
    target = await create(d.ops, data)
    other = await prepared(d, selection)
    wid, sid = target['workspace_id'], target['sprint_id']
    path = f'/api/v1/pm/workspaces/{wid}/sprints/{sid}'
    auth = token(d, READER.actor, 'observe')
    assert (await http(port, 'GET', path, tok=auth))[0] == 200
    await member(d.ops, {'workspace_id': wid}, READER.actor, None)
    assert (await http(port, 'GET', path, tok=auth))[0] == 404
    owner = token(d, OWNER.actor, *OWNER.scopes)
    assert (await http(port, 'GET', path.replace(wid, other['workspace_id']), tok=owner))[0] == 404
    assert (await http(port, 'GET', f"/api/v1/pm/workspaces/{wid}/sprint-artifacts/dod/{other['dod']}", tok=owner))[0] == 404
    assert (await http(port, 'GET', path, tok=token(d, OWNER.actor, 'manage')))[0] == 403


@pytest.mark.parametrize('suffix', [
    '/sprint-artifacts?limit=1', '/sprint-artifacts/dod?after=-1', '/sprint-artifacts/backlog?limit=101',
    '/sprint-artifacts/product_goal?after=0&after=1', '/sprints?history_limit=1', '/sprints?actor=other',
    '/sprints?limit=true', '/sprints/{sid}?limit=1', '/sprints/{sid}?history_limit=0',
    '/sprints/{sid}?history_limit=101', '/sprints/{sid}?history_limit=1&history_limit=2',
    '/sprints/{sid}/history/goals?before=1', '/sprints/{sid}/history/selections?after=1.5',
])
async def test_sprint_queries_reject_ambiguous_or_unbounded_fields(served, suffix):
    d, port, selection = served
    data = await prepared(d, selection)
    target = await create(d.ops, data)
    path = '/api/v1/pm/workspaces/' + target['workspace_id'] + suffix.format(sid=target['sprint_id'])
    assert (await http(port, 'GET', path, tok=token(d, OWNER.actor, 'observe')))[0] == 422


@pytest.mark.parametrize('extra', [{'history_limit': True}, {'history_limit': '1'}, {'history_limit': None}, {'actor': 'other'}])
async def test_sprint_rpc_preserves_types_and_identity(served, extra):
    d, _, selection = served
    data = await prepared(d, selection)
    target = await create(d.ops, data)
    with pytest.raises(OperationError, match='INVALID_PARAMS'):
        await d.call_api('pm_sprint_get', {**target, **extra}, OWNER)
