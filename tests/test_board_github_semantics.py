"""Source observations must never impersonate local completion or ownership."""
import pytest

from bat_agent_connector import pm_kanban
from tests.test_board_github_sync import Provider, issue, sync
from tests.test_work_items import PERSON, act, get, item, project
from tests.test_work_items import daemon as daemon


@pytest.mark.parametrize(('pr', 'reason', 'merged', 'column'), [
    (True, None, False, 'github_closed'),
    (True, None, True, 'github_done'),
    (False, 'completed', False, 'github_completed'),
    (False, 'not_planned', False, 'github_closed'),
    (False, 'duplicate', False, 'github_closed'),
    (False, None, False, 'github_closed'),
])
async def test_closure_is_source_observation_without_human_completion(daemon, pr, reason, merged, column):
    d = daemon
    ws = (await act(d, PERSON, 'pm.workspace.create', params={'name': 'Source status'}))['result']['workspace']['workspace_id']
    value = issue(1, pr=pr, state='closed', merged=merged)
    value['state_reason'] = reason
    d.ops.context['board_github_provider'] = Provider([value])
    await act(d, PERSON, 'pm.github.import', {'workspace_id': ws},
        {'repository': 'example/board'}, {'expected_revision': 0})
    card = pm_kanban.get_board(d.ops, PERSON, ws)['cards'][0]
    saved = get(d, card['work_item_id'])
    assert saved['state'] != 'done'
    assert saved['done_by'] is saved['done_at'] is None
    assert not saved['completion']['pending'] and not saved['completion']['approved']
    assert card['column'] == column
    assert saved['external']['source'] == 'github'
    assert saved['external']['state_reason'] == reason


@pytest.mark.parametrize('state', ['doing', 'waiting', 'todo'])
async def test_first_adoption_of_closed_source_preserves_human_lane(daemon, state):
    d = daemon
    pid = await project(d)
    iid = await item(d, pid, 'Human work', state=state, goal='https://github.com/example/board/pull/1')
    d.ops.context['board_github_provider'] = Provider([issue(1, pr=True, state='closed', merged=True)])
    result = (await sync(d, pid))['result']
    assert result['created'] == 0 and result['adopted'] == 1
    saved = get(d, iid)
    assert saved['state'] == state
    assert saved['external']['board_lane'] is None
    assert saved['done_by'] is saved['done_at'] is None


async def test_draft_is_retained_as_source_status(daemon):
    d = daemon
    pid = await project(d)
    d.ops.context['board_github_provider'] = Provider([issue(1, pr=True, draft=True)])
    await sync(d, pid)
    iid = d.ops.db.execute('SELECT work_item_id FROM work_items').fetchone()[0]
    assert get(d, iid)['external']['draft'] is True
