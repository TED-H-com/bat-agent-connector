"""Real adopted Git scope reaches flow and meeting records through every entry point."""
from types import SimpleNamespace

import pytest

from bat_agent_connector import mcp_server, pm_kanban, pm_meetings, pm_sprints
from bat_agent_connector.operations import OperationError
from tests.operation_helpers import settle_operations
from tests.test_api_v1 import http, token, write_frames
from tests.test_pm_discussions import OWNER, READER, member
from tests.test_pm_meetings import create_request, draft_params, follow_up, request
from tests.test_pm_run_api import unpack
from tests.test_pm_snapshots import snapshot
from tests.test_pm_sprint_api import pm as pm_fixture
from tests.test_pm_sprint_api import prepared
from tests.test_pm_sprint_api import served as served_fixture
from tests.test_pm_sprint_api import sprint_clock as sprint_clock_fixture
from tests.test_pm_sprint_api import world as repository_world
from tests.test_pm_sprints import NOW, change, create, selections

pm, world, served, sprint_clock = pm_fixture, repository_world, served_fixture, sprint_clock_fixture


async def prepare_meeting(d, selection):
    data = await prepared(d, selection)
    sprint = await create(d.ops, data)
    await change(d.ops, sprint, 'select', items=selections(data))
    snap = await snapshot(d.ops, data['workspace_id'])
    op, _ = d.ops.create(OWNER, **create_request(d.ops, sprint, snap))
    await settle_operations(d.ops)
    result = d.ops.get(op['operation_id'])
    assert result['status'] == 'succeeded', result
    return data, sprint, snap, {'workspace_id': data['workspace_id'], 'meeting_id': result['result']['meeting_id']}


async def test_management_read_parity_and_no_observation_effects(served, monkeypatch, mock):
    d, port, selection = served
    data, sprint, _, target = await prepare_meeting(d, selection)
    monkeypatch.setattr(pm_kanban, 'time', SimpleNamespace(time=lambda: NOW))
    wid, mid = target['workspace_id'], target['meeting_id']
    auth = token(d, OWNER.actor, 'observe')
    monkeypatch.setenv('BATC_TASK_URL', f'http://127.0.0.1:{port}/rpc')
    monkeypatch.setenv('BATC_API_TOKEN', auth)
    server, fleet = mcp_server.build_server(d.fleet.config, read_only=True, principal_only=True)
    before = d.ops.db.total_changes
    try:
        for method, fields, suffix in [
            ('pm_kanban_get', {'after': 0, 'limit': 1}, '/kanban?after=0&limit=1'),
            ('pm_kanban_card_get', {'work_item_id': data['work'][0]}, '/kanban/' + data['work'][0]),
            ('pm_meetings_list', {'sprint_id': sprint['sprint_id'], 'limit': 1}, '/meetings?sprint_id=' + sprint['sprint_id'] + '&limit=1'),
            ('pm_meeting_get', {'meeting_id': mid, 'history_limit': 1}, '/meetings/' + mid + '?history_limit=1'),
            ('pm_meeting_history_list', {'meeting_id': mid, 'kind': 'contexts', 'after': 0, 'limit': 1}, '/meetings/' + mid + '/history/contexts?after=0&limit=1'),
        ]:
            status, expected = await http(port, 'GET', f'/api/v1/pm/workspaces/{wid}' + suffix, tok=auth)
            assert status == 200, expected
            params = {'workspace_id': wid, **fields}
            assert await d.call_api(method, params.copy(), OWNER) == expected
            assert unpack(await server.call_tool(method, params)) == expected
        assert d.ops.db.total_changes == before
        assert not write_frames(mock)
    finally:
        await fleet.close()


async def test_http_discussion_completion_and_follow_up_preserve_original_result(served, mock):
    d, port, selection = served
    _, sprint, _, target = await prepare_meeting(d, selection)
    auth = token(d, OWNER.actor, *OWNER.scopes)

    async def send(body):
        body = body.copy()
        key = body.pop('idempotency_key')
        status, response = await http(port, 'POST', '/api/v1/operations', tok=auth, body=body, headers={'Idempotency-Key': key})
        assert status in {200, 202}, response
        await settle_operations(d.ops)
        operation = d.ops.get(response['operation']['operation_id'])
        assert operation['status'] == 'succeeded', operation
        return operation

    await send(request(d.ops, target, 'note', content='Discuss the fixed Why, What and How.'))
    drafted = await send(request(d.ops, target, 'draft', **draft_params(follow_ups=[follow_up(OWNER.actor)])))
    before = pm_meetings.get_meeting(d.ops, OWNER, **target)
    assert before['completion'] is None and before['meeting']['status'] == 'draft'
    body = request(d.ops, target, 'complete', draft_revision_id=drafted['result']['draft_revision_id'],
                   discussion_summary='The owner records the discussed direction.', decision_ids=['retain_direction'])
    completed = await send(body)
    assert (await send(body))['operation_id'] == completed['operation_id']
    await send(request(d.ops, target, 'follow_up.record', follow_up_id='check_quality', status='reported_done',
                       result='Review was held; its quality effect still needs evidence.'))
    view = pm_meetings.get_meeting(d.ops, OWNER, **target)
    assert view['context'] == before['context'] and view['draft'] == before['draft']
    assert view['completion']['document']['decisions'][0]['scope_applied'] is False
    assert view['follow_up_events'][0]['document']['verification_status'] == 'unassessed'
    assert view['quality'] == 'unassessed' and not view['execution_authorized']
    assert pm_sprints.get_sprint(d.ops, OWNER, **sprint)['sprint']['status'] == 'planned'
    assert d.ops.db.execute('SELECT count(*) FROM tasks').fetchone()[0] == 0
    assert not write_frames(mock)


async def test_current_membership_and_fixed_foreign_ids_are_enforced(served):
    d, port, selection = served
    data, _, _, target = await prepare_meeting(d, selection)
    other = await prepared(d, selection)
    auth = token(d, READER.actor, 'observe')
    prefix = '/api/v1/pm/workspaces/' + target['workspace_id']
    suffixes = ['/kanban', '/kanban/' + data['work'][0], '/meetings', '/meetings/' + target['meeting_id'],
                '/meetings/' + target['meeting_id'] + '/history/contexts']
    for suffix in suffixes:
        assert (await http(port, 'GET', prefix + suffix, tok=auth))[0] == 200
    await member(d.ops, {'workspace_id': target['workspace_id']}, READER.actor, None)
    for suffix in suffixes:
        assert (await http(port, 'GET', prefix + suffix, tok=auth))[0] == 404
    owner = token(d, OWNER.actor, 'observe')
    for suffix in [suffixes[1], suffixes[3]]:
        assert (await http(port, 'GET', '/api/v1/pm/workspaces/' + other['workspace_id'] + suffix, tok=owner))[0] == 404
    assert (await http(port, 'GET', prefix + '/kanban', tok=token(d, OWNER.actor, 'manage')))[0] == 403


async def test_review_availability_tracks_started_sprint_and_current_writer_scope(served):
    d, port, selection = served
    _, sprint, _, _ = await prepare_meeting(d, selection)
    assert not pm_sprints.get_sprint(d.ops, OWNER, **sprint)['review_available']
    await change(d.ops, sprint, 'start')
    active = pm_sprints.get_sprint(d.ops, OWNER, **sprint)
    assert active['review_available'] and active['retro_available']
    assert active['quality'] == 'unassessed' and not active['execution_authorized']
    assert not pm_sprints.get_sprint(d.ops, READER, **sprint)['review_available']
    path = f"/api/v1/pm/workspaces/{sprint['workspace_id']}/sprints/{sprint['sprint_id']}"
    _, scoped = await http(port, 'GET', path, tok=token(d, OWNER.actor, 'observe'))
    assert not scoped['review_available']
    await change(d.ops, sprint, 'cancel')
    assert pm_sprints.get_sprint(d.ops, OWNER, **sprint)['review_available']


@pytest.mark.parametrize('suffix', [
    '/kanban?limit=0', '/kanban?after=-1', '/kanban?actor=other', '/kanban?after=0&after=1',
    '/kanban/{work}?limit=1', '/meetings?kind=review', '/meetings?sprint_id=unknown',
    '/meetings?limit=true', '/meetings/{mid}?history_limit=101', '/meetings/{mid}?history_limit=true',
    '/meetings/{mid}/history/notes?after=1.5', '/meetings/{mid}/history/drafts?limit=1&limit=2',
])
async def test_exact_management_queries(served, suffix):
    d, port, selection = served
    data, _, _, target = await prepare_meeting(d, selection)
    status, _ = await http(port, 'GET', '/api/v1/pm/workspaces/' + target['workspace_id'] + suffix.format(work=data['work'][0], mid=target['meeting_id']),
                           tok=token(d, OWNER.actor, 'observe'))
    assert status in {404, 422}


@pytest.mark.parametrize('extra', [{'history_limit': True}, {'history_limit': '1'}, {'history_limit': None}, {'actor': 'other'}])
async def test_rpc_meeting_read_types_and_identity(served, extra):
    d, _, selection = served
    _, _, _, target = await prepare_meeting(d, selection)
    with pytest.raises(OperationError, match='INVALID_PARAMS'):
        await d.call_api('pm_meeting_get', {**target, **extra}, OWNER)
