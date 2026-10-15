"""Explicit manual read evidence never acquires managed execution ownership."""
import copy
import json
import uuid

import pytest

from bat_agent_connector import pm_access
from bat_agent_connector import pm_session_quiescence as manual
from bat_agent_connector.operations import OperationError
from tests import pm_joined_lifecycle_helpers as story
from tests.test_checkpoints import MANUAL
from tests.test_pm_joined_lifecycle import bat_world, daemon, joined, world  # noqa: F401


@pytest.fixture
async def linked(joined):  # noqa: F811
    await story.engineering(joined, 'member.login')
    return joined


def request(w):
    ops = w['d'].ops
    link = ops.db.execute("SELECT * FROM work_item_links WHERE kind='session' AND ref=?", ('h1/' + MANUAL,)).fetchone()
    return {'action': manual.ACTION, 'target': {'workspace_id': w['workspace_id'], 'work_item_id': link['work_item_id']},
        'params': {'link_id': link['link_id']},
        'preconditions': {'expected_link_operation_id': link['link_operation'], 'expected_project_binding_id': w['project_binding_id'],
            'expected_session_identity_digest': manual.sources(ops, story.OWNER, w['workspace_id'], link['work_item_id'])[0]['expected_session_identity_digest']},
        'idempotency_key': uuid.uuid4().hex}


async def run(w, envelope):
    ops = w['d'].ops
    operation = ops.create(story.OWNER, **envelope)[0]
    await ops._execute(operation['operation_id'])
    return ops.get(operation['operation_id'])


@pytest.mark.parametrize('failure', ['active', 'abort', 'multiple_inputs', 'pending', 'gap', 'missing_input_counter'])
async def test_active_incomplete_or_multiple_manual_inputs_remain_unknown(linked, mock, monkeypatch, failure):
    w, ops = linked, linked['d'].ops
    if failure != 'active':
        story.complete_native_turn(w, mock, MANUAL)
    if failure == 'abort':
        w['coding'][MANUAL]['reason'] = 'aborted'
    elif failure == 'multiple_inputs':
        mock.states[MANUAL]['messages'].append({'id': 'second-human-input', 'role': 'user', 'content': 'A second human instruction'})
    elif failure == 'pending':
        w['coding'][MANUAL]['pending'] = 1
    elif failure in {'gap', 'missing_input_counter'}:
        dispatch = mock.dispatch
        def changed(channel, params):
            value = dispatch(channel, params)
            if channel == 'claude:sync-session' and params.get('sessionId') == MANUAL:
                if failure == 'gap' and params.get('cursor'):
                    return dispatch(channel, {'sessionId': MANUAL})
                if failure == 'missing_input_counter' and value.get('state'):
                    value = copy.deepcopy(value)
                    value['state']['meta'].pop('inputState', None)
            return value
        monkeypatch.setattr(mock, 'dispatch', changed)
    result = await run(w, request(w))
    assert result['status'] != 'succeeded' and result['error_code'] != 'INTERNAL', result
    assert manual.proof(ops, story.OWNER, result) is None
    story.assert_manual_preserved(w, mock)


async def test_original_manual_fact_is_read_only_source_gated_and_not_work_acceptance(linked, mock):
    w, ops = linked, linked['d'].ops
    story.complete_native_turn(w, mock, MANUAL)
    envelope = request(w)
    result = await run(w, envelope)
    assert result['status'] == 'succeeded', result
    proof = manual.proof(ops, story.OWNER, result)
    assert proof['runtime_status'] == 'current_single_input_quiescent'
    assert proof['process_death_proven'] is False and proof['ownership'] == 'manual_read_only'
    assert proof['work_acceptance'] == 'not_evaluated'
    same = await run(w, envelope)
    assert same['operation_id'] == result['operation_id']
    assert manual.proof(ops, story.OWNER, same) == proof
    events = [dict(row) for row in ops.db.execute('SELECT * FROM api_events WHERE resource_type=\'operation\' AND resource_id=?', (result['operation_id'],))]
    assert events and all(pm_access.event_visible(ops.db, story.OWNER, {**row, 'body': json.loads(row['body'])}) for row in events)
    story.assert_manual_preserved(w, mock)
    # Removing the explicit old link revokes the complete contribution. No
    # other link or most-recent session observation substitutes for it.
    ops.db.execute('UPDATE work_item_links SET removed_at=1 WHERE link_id=?', (proof['link_id'],))
    assert not manual.operation_visible(ops.db, story.OWNER, result)
    with pytest.raises(OperationError):
        manual.proof(ops, story.OWNER, result)
    assert all(not pm_access.event_visible(ops.db, story.OWNER, {**row, 'body': json.loads(row['body'])}) for row in events)
