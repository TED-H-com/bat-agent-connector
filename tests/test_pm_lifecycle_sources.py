"""Current source authority filters lifecycle histories, not just the original op."""
from __future__ import annotations

import json

import pytest

from bat_agent_connector import pm_access
from bat_agent_connector import pm_lifecycle as life
from bat_agent_connector.operations import OperationError
from tests.test_pm_follow_up_analysis import base_ops as follow_up_base
from tests.test_pm_follow_up_analysis import create, setup, withdraw
from tests.test_pm_follow_up_analysis import snapshot_ops as follow_up_snapshots
from tests.test_pm_follow_up_analysis import world as analysis_world
from tests.test_pm_lifecycle import decide
from tests.test_pm_native_operations import OWNER, await_runtime, finish, writer

snapshot_ops = follow_up_snapshots
base_ops = follow_up_base
base_world = analysis_world


@pytest.fixture
def world(base_world):
    life.install(base_world[0])
    return base_world


async def test_source_revocation_hides_inflight_ids_retention_reason_and_original_operation(world):
    ops, settings, instances = world
    data = await setup(ops, private=True)
    settings['block_turn'], settings['exit_unknown'] = True, True
    op = create(ops, data)
    runtime = await await_runtime(ops, instances, op)
    closed = await decide(ops, data['wid'], 'close')
    retained = await decide(ops, data['wid'], 'retain_inflight', params={
        'transition_id': closed['result']['transition_id'], 'operation_ids': [op['operation_id']],
        'reason': 'Private quoted problem: original source must remain restricted'})
    tid = retained['result']['transition_id']
    assert life.get_transition(ops, OWNER, data['wid'], tid)['transition']['reason'].startswith('Private quoted')
    withdraw(ops, data)
    visible = life.get_lifecycle(ops, OWNER, data['wid'])
    assert visible['lifecycle']['stage'] == 'closed' and visible['transition'] is None
    assert visible['transition_visibility_filtered'] is True
    assert visible['inflight_summary'] == {'visible_unresolved': 0, 'visible_retained': 0, 'visibility_filtered': True}
    page = life.list_inflight(ops, OWNER, data['wid'])
    assert page['inflight'] == [] and page['visibility_filtered']
    history = life.list_transitions(ops, OWNER, data['wid'])
    assert history['visibility_filtered'] and all(item['transition_id'] != tid for item in history['transitions'])
    public = json.dumps([visible, page, history])
    assert op['operation_id'] not in public and 'Private quoted' not in public
    with pytest.raises(OperationError, match='NOT_FOUND'):
        life.get_transition(ops, OWNER, data['wid'], tid)
    assert not pm_access.visible(ops.db, OWNER, retained)
    assert not pm_access.visible(ops.db, OWNER, op)
    runtime.release.set()
    result = await finish(ops, op)
    assert result['status'] == 'uncertain' and writer(ops, data['target']) == runtime.run_id
    assert runtime.sends == 1


async def test_hidden_history_pages_advance_without_skipping_next_visible_transition(world):
    ops, settings, instances = world
    data = await setup(ops, private=True)
    settings['block_turn'], settings['exit_unknown'] = True, True
    op = create(ops, data)
    runtime = await await_runtime(ops, instances, op)
    closed = await decide(ops, data['wid'], 'close')
    retained = await decide(ops, data['wid'], 'retain_inflight', params={
        'transition_id': closed['result']['transition_id'], 'operation_ids': [op['operation_id']], 'reason': 'Source-bound reason'})
    await decide(ops, data['wid'], 'reopen', params={'stage': 'idea'})
    withdraw(ops, data)
    first = life.list_transitions(ops, OWNER, data['wid'], limit=1)
    hidden = life.list_transitions(ops, OWNER, data['wid'], after=first['next_cursor'], limit=1)
    assert hidden['transitions'] == [] and hidden['next_cursor'] > first['next_cursor']
    last = life.list_transitions(ops, OWNER, data['wid'], after=hidden['next_cursor'], limit=1)
    assert last['transitions'][0]['action'] == 'pm.lifecycle.reopen' and last['next_cursor'] is None
    assert retained['result']['transition_id'] not in json.dumps(last)
    runtime.release.set()
    await finish(ops, op)
