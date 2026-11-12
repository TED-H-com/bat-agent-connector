"""Rejected Work turns relinquish markers without changing Result ownership."""

import json

import pytest

from bat_agent_connector import pm_attempt_results as ar
from bat_agent_connector import pm_work_follow_up as follow
from bat_agent_connector.operations import OperationError
from tests import test_pm_attempt_results as results
from tests.operation_helpers import settle_operations
from tests.test_pm_terminal_unsent_reservation import collect, queued_collision
from tests.test_pm_work_start import STARTER_OWNER
from tests.test_pm_workspace_wip import read
from tests.test_pm_workspace_wip_lineage import closed_attempt

world = results.world
pm = results.pm
ready = results.ready
start_ready = results.start_ready


async def test_rejected_work_releases_direct_marker(start_ready, mock):
    # Adapted from the independent review's real-action NOT_ACCEPTED probe.
    w = start_ready
    ops = w['d'].ops
    att, _ = await closed_attempt(w, mock)
    before = len(mock.invokes)
    mock.handlers['claude:send-message'] = lambda _: {'accepted': False, 'ok': False}
    work, _ = ops.create(STARTER_OWNER, action='session.send', target=w['target'],
        params={'text': 'Synthetic rejected Work input', 'schedule': 'now'},
        preconditions={}, idempotency_key='rejected-work')
    await settle_operations(ops)
    mock.handlers.pop('claude:send-message')
    work = ops.get(work['operation_id'])
    assert work['status'] == 'failed' and work['error_code'] == 'NOT_ACCEPTED'
    assert work['external_refs']['send_sent'] is True
    assert follow.receipt_state(work, work['steps'][-1]) == 'failed'
    assert follow.latest_sent_turn(ops.db, att['attempt_id']) == (None, None)
    with pytest.raises(OperationError, match='NOT_RESUMABLE'):
        ops.resume(STARTER_OWNER, work['operation_id'])
    original_input = dict(ops.db.execute('SELECT * FROM work_inputs WHERE operation_id=?',
                                        (work['operation_id'],)).fetchone())
    marker = 'batc-' + work['operation_id']
    direct, _ = ops.create(STARTER_OWNER, action='session.send',
        target={'host': 'h1', 'session_id': att['session_id']},
        params={'text': 'Synthetic rejected Work input', 'message_id': marker},
        preconditions={}, idempotency_key='rejected-direct')
    await settle_operations(ops)
    assert ops.get(direct['operation_id'])['status'] == 'succeeded'
    assert read(ops, w['target'], STARTER_OWNER)['used'] == 1
    await collect(w, mock, att, marker)
    assert read(ops, w['target'], STARTER_OWNER)['used'] == 0
    rows = [r for r in ar.receipts(ops.db, w['workspace_id'], att['attempt_id'])['receipts']
            if r['turn_marker'] == marker]
    assert [r['operation_id'] for r in rows] == [direct['operation_id']]
    own = ar.receipts(ops.db, w['workspace_id'], att['attempt_id'],
                      own_operation_id=att['handoff_operation_id'])['receipts']
    assert all(r['turn_marker'] != marker for r in own)
    await collect(w, mock, att, marker)
    assert len([r for r in ar.receipts(ops.db, w['workspace_id'], att['attempt_id'])['receipts']
                if r['turn_marker'] == marker]) == 1
    assert sum(f['channel'] == 'claude:send-message' for f in mock.invokes[before:]) == 2
    assert dict(ops.db.execute('SELECT * FROM work_inputs WHERE operation_id=?',
                              (work['operation_id'],)).fetchone()) == original_input


async def test_latest_sent_turn_preserves_original_selection(start_ready, mock):
    """Synthetic state matrix compares the factored selector with its prior rule."""
    w = start_ready
    ops = w['d'].ops
    att, work, marker = await queued_collision(w, mock)
    for status in ['accepted', 'running', 'waiting_checks', 'waiting_external',
                   'needs_attention', 'uncertain', 'succeeded', 'failed', 'cancelled']:
        for fence in [None, False, True]:
            for error in [None, 'NOT_ACCEPTED', 'REFUSED', 'CANCELLED']:
                ops.db.execute('SAVEPOINT selection')
                try:
                    refs = dict(work['external_refs'])
                    if fence is None:
                        refs.pop('send_sent', None)
                    else:
                        refs['send_sent'] = fence
                    ops.db.execute('UPDATE operations SET status=?,error_code=?,external_refs=? WHERE operation_id=?',
                        (status, error, json.dumps(refs), work['operation_id']))
                    expected = (marker, work['operation_id']) if (
                        error != 'NOT_ACCEPTED' and
                        (fence is True or status in {'succeeded', 'uncertain', 'needs_attention'})
                    ) else (None, None)
                    assert follow.latest_sent_turn(ops.db, att['attempt_id']) == expected, (status, fence, error)
                finally:
                    ops.db.execute('ROLLBACK TO selection')
                    ops.db.execute('RELEASE selection')
