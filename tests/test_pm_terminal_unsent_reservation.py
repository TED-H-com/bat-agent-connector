"""A terminal-unsent Work input cannot own a direct input's terminal receipt."""

import pytest

from bat_agent_connector import pm_attempt_results as ar
from bat_agent_connector import pm_work_follow_up as follow
from bat_agent_connector.operations import OpContext
from tests import test_pm_attempt_results as results
from tests.operation_helpers import settle_operations
from tests.test_pm_attempt_result_marker import terminal
from tests.test_pm_work_start import STARTER_OWNER
from tests.test_pm_workspace_wip import read
from tests.test_pm_workspace_wip_lineage import closed_attempt

world = results.world
pm = results.pm
ready = results.ready
start_ready = results.start_ready


async def queued_collision(w, mock):
    ops = w['d'].ops
    att, _ = await closed_attempt(w, mock)
    sid = att['session_id']
    mock.metas[sid].update(isStreaming=True, numTurns=1)
    mock.states[sid]['isStreaming'] = True
    await w['d'].inventory.refresh_host('h1')
    work, _ = ops.create(STARTER_OWNER, action='session.send', target=w['target'],
        params={'text': 'Synthetic Work instruction', 'schedule': 'after_current'},
        preconditions={}, idempotency_key='reserved-work')
    await settle_operations(ops)
    work = ops.get(work['operation_id'])
    assert work['status'] == 'waiting_external'
    assert follow.receipt_state(work, None) == 'queued'
    marker = 'batc-' + work['operation_id']
    return att, work, marker


async def accepted_direct(w, mock, att, marker, *, different_text=False):
    ops = w['d'].ops
    sid = att['session_id']
    mock.metas[sid]['isStreaming'] = mock.states[sid]['isStreaming'] = False
    direct, _ = ops.create(STARTER_OWNER, action='session.send',
        target={'host': 'h1', 'session_id': sid},
        params={'text': 'Different synthetic instruction' if different_text else 'Synthetic Work instruction',
                'message_id': marker}, preconditions={}, idempotency_key='reserved-direct')
    await settle_operations(ops)
    direct = ops.get(direct['operation_id'])
    assert direct['status'] == 'succeeded'
    return direct


async def collect(w, mock, att, marker):
    terminal(mock, att, marker)
    mock.metas[att['session_id']]['numTurns'] = 3
    await w['d'].inventory.refresh_host('h1')
    c = w['d'].result_collector
    for _ in range(3):
        c._memo.clear()
        await c.collect_host('h1')
        await settle_operations(w['d'].ops)


@pytest.mark.parametrize('order', ['cancel_before', 'cancel_after', 'cancel_after_skipped', 'refuse_at_wake'])
async def test_terminal_unsent_reservation_releases_direct(start_ready, mock, order):
    w = start_ready
    ops = w['d'].ops
    att, work, marker = await queued_collision(w, mock)
    original_input = dict(ops.db.execute('SELECT * FROM work_inputs WHERE operation_id=?',
                                         (work['operation_id'],)).fetchone())
    if order == 'cancel_before':
        ops.cancel(STARTER_OWNER, work['operation_id'])
        await settle_operations(ops)
    direct = await accepted_direct(w, mock, att, marker, different_text=order == 'refuse_at_wake')
    if order == 'cancel_after_skipped':
        # The original marker already has a receipt. A pending Work input owns
        # the shared marker before its send step starts, even with a direct ACK.
        c = w['d'].result_collector
        mock.metas[att['session_id']]['isStreaming'] = True
        mock.states[att['session_id']]['isStreaming'] = True
        await w['d'].inventory.refresh_host('h1')
        c._memo.clear()
        await c.collect_host('h1')
        await settle_operations(ops)
        assert ops.get(work['operation_id'])['status'] == 'waiting_external'
        assert c.direct_last_check[att['attempt_id']]['outcome'] == 'unchanged'
        assert read(ops, w['target'], STARTER_OWNER)['used'] == 1
    if order in {'cancel_after', 'cancel_after_skipped'}:
        ops.cancel(STARTER_OWNER, work['operation_id'])
        await settle_operations(ops)
    await collect(w, mock, att, marker)
    final_work = ops.get(work['operation_id'])
    assert final_work['status'] == ('failed' if order == 'refuse_at_wake' else 'cancelled')
    step = next((s for s in final_work['steps'] if s['name'] == 'send'), None)
    assert step is None or follow.unsent(OpContext(ops, final_work))
    assert follow.receipt_state(final_work, step) in {'failed', 'cancelled'}
    assert follow.latest_sent_turn(ops.db, att['attempt_id']) == (None, None)
    assert dict(ops.db.execute('SELECT * FROM work_inputs WHERE operation_id=?',
                              (work['operation_id'],)).fetchone()) == original_input
    assert read(ops, w['target'], STARTER_OWNER)['used'] == 0
    raw = ar.receipts(ops.db, w['workspace_id'], att['attempt_id'])['receipts']
    assert any(r['turn_marker'] == marker and r['operation_id'] == direct['operation_id'] for r in raw)
    own = ar.receipts(ops.db, w['workspace_id'], att['attempt_id'],
                      own_operation_id=att['handoff_operation_id'])['receipts']
    assert all(r['turn_marker'] != marker for r in own)
    frames = len(mock.invokes)
    await collect(w, mock, att, marker)
    assert not any(f['channel'] == 'claude:send-message' for f in mock.invokes[frames:])


async def test_unsent_runnable_work_keeps_reservation_and_owns_result(start_ready, mock, monkeypatch):
    w = start_ready
    ops = w['d'].ops
    att, work, marker = await queued_collision(w, mock)
    # Enter the real send step, then wait before transport. This is the existing
    # unsent() -> RERUN path, not merely a never-started queued operation.
    from bat_agent_connector import service
    from bat_agent_connector.operations import Wait

    async def before_transport(*args, **kwargs):
        raise Wait(status='waiting_external', reason='synthetic-pre-frame', delay_s=60)

    terminal(mock, att, att['turn_marker'])
    await w['d'].inventory.refresh_host('h1')
    with monkeypatch.context() as patch:
        patch.setattr(service, 'session_send', before_transport)
        ops.wake(work['operation_id'])
        await settle_operations(ops)
    work = ops.get(work['operation_id'])
    assert work['status'] == 'waiting_external'
    assert follow.unsent(OpContext(ops, work))
    await accepted_direct(w, mock, att, marker)
    c = w['d'].result_collector
    c._memo.clear()
    candidate = c._eligible_candidates('h1')[0]
    assert follow.unsent(OpContext(ops, work))
    assert follow.receipt_state(work, work['steps'][-1]) == 'queued'
    assert c._direct_candidate(candidate) is None
    await collect(w, mock, att, marker)
    work = ops.get(work['operation_id'])
    assert work['status'] == 'succeeded'
    own = ar.receipts(ops.db, w['workspace_id'], att['attempt_id'],
                      own_operation_id=att['handoff_operation_id'])['receipts']
    assert any(r['turn_marker'] == marker and r['operation_id'] == work['operation_id'] for r in own)
    assert read(ops, w['target'], STARTER_OWNER)['used'] == 0


async def test_nonterminal_and_possibly_sent_owners_stay_reserved(start_ready, mock):
    """Synthetic state guards over an actual admitted Work input and direct ACK."""
    import json

    w = start_ready
    ops = w['d'].ops
    att, work, marker = await queued_collision(w, mock)
    await accepted_direct(w, mock, att, marker)
    c = w['d'].result_collector
    c._memo.clear()
    candidate = c._eligible_candidates('h1')[0]
    for status, sent, error in [
        ('accepted', False, None), ('running', False, None),
        ('waiting_checks', False, None), ('waiting_external', False, None),
        ('needs_attention', False, None), ('uncertain', False, None),
        ('uncertain', True, None), ('succeeded', True, None),
        ('failed', True, 'REFUSED'), ('failed', True, 'NOT_ACCEPTED'),
        ('cancelled', True, None),
    ]:
        ops.db.execute('SAVEPOINT owner_state')
        try:
            refs = {**work['external_refs'], 'send_sent': sent}
            ops.db.execute('UPDATE operations SET status=?,error_code=?,external_refs=? WHERE operation_id=?',
                           (status, error, json.dumps(refs), work['operation_id']))
            assert c._direct_candidate(candidate) is None, (status, sent, error)
        finally:
            ops.db.execute('ROLLBACK TO owner_state')
            ops.db.execute('RELEASE owner_state')
