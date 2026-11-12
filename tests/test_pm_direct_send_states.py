"""Original direct-send outcomes, without a second reconciliation authority."""
import pytest

from bat_agent_connector import pm_direct_inputs
from bat_agent_connector.errors import ConnectionLost, InvokeError
from tests import test_pm_attempt_results as results
from tests.operation_helpers import settle_operations
from tests.test_pm_attempt_result_marker import terminal
from tests.test_pm_attempt_results_direct import collect
from tests.test_pm_work_follow_up import _patch_roundtrip
from tests.test_pm_work_start import STARTER_OWNER
from tests.test_pm_workspace_wip import read
from tests.test_pm_workspace_wip_lineage import closed_attempt

world = results.world
pm = results.pm
ready = results.ready
start_ready = results.start_ready


@pytest.mark.parametrize('case', ['busy_refusal', 'not_accepted', 'cancel_before',
    'accepted', 'cancel_after_ack', 'lost_ack', 'ambiguous_failure'])
async def test_original_direct_outcome_has_defined_release(start_ready, mock, monkeypatch, case):
    w = start_ready
    ops = w['d'].ops
    att, _ = await closed_attempt(w, mock)
    sid = att['session_id']
    if case == 'busy_refusal':
        mock.metas[sid]['isStreaming'] = True
        mock.states[sid]['isStreaming'] = True
    elif case == 'not_accepted':
        mock.handlers['claude:send-message'] = lambda p: {'accepted': False, 'ok': False}
    elif case in {'lost_ack', 'ambiguous_failure'}:
        async def lose_reply(client, frame, timeout, on_transport, original):
            value = await original(client, frame, timeout, on_transport=on_transport)
            if frame.get('channel') == 'claude:send-message':
                if case == 'lost_ack':
                    raise ConnectionLost('Synthetic lost ACK')
                raise InvokeError('Synthetic unproven failure after transport')
            return value
        _patch_roundtrip(monkeypatch, lose_reply)
    start = len(mock.invokes)
    op, _ = ops.create(STARTER_OWNER, action='session.send', target={'host': 'h1', 'session_id': sid},
        params={'text': 'Synthetic direct state'}, preconditions={}, idempotency_key=f'direct-state-{case}')
    oid = op['operation_id']
    assert read(ops, w['target'], STARTER_OWNER)['used'] == 1
    if case == 'cancel_before':
        ops.cancel(STARTER_OWNER, oid)
    await settle_operations(ops)
    op = ops.get(oid)
    if case in {'not_accepted', 'cancel_after_ack'}:
        ops.cancel(STARTER_OWNER, oid)
        await settle_operations(ops)
        op = ops.get(oid)
    value = read(ops, w['target'], STARTER_OWNER)
    frames = sum(f['channel'] == 'claude:send-message' for f in mock.invokes[start:])
    print('DIRECT STATE', case, op['status'], op['error_code'], frames, value['used'])
    if case in {'busy_refusal', 'not_accepted', 'cancel_before'}:
        assert op['status'] in {'failed', 'cancelled'}
        assert value['used'] == 0
        assert frames == (1 if case == 'not_accepted' else 0)
    elif case == 'ambiguous_failure':
        assert op['status'] == 'failed' and frames == 1
        assert value['used'] is None and value['coverage']['reasons'] == ['coverage_unavailable']
    elif case == 'lost_ack':
        assert op['status'] == 'uncertain' and frames == 1 and value['used'] == 1
        # The ordinary original read-back can settle Claude's exact echoed input.
        terminal(mock, att, 'batc-' + oid)
        with ops.journal.tx():
            ops.db.execute('UPDATE operations SET next_run_at=0 WHERE operation_id=?', (oid,))
        await settle_operations(ops)
        assert ops.get(oid)['status'] == 'succeeded'
        await collect(w)
        assert read(ops, w['target'], STARTER_OWNER)['used'] == 0
        assert sum(f['channel'] == 'claude:send-message' for f in mock.invokes[start:]) == 1
    else:
        assert op['status'] == 'succeeded' and frames == 1 and value['used'] == 1
        assert len(pm_direct_inputs.eligible(ops.db, att['handoff_operation_id'],
            {'host': 'h1', 'session_id': sid})) == 1
        terminal(mock, att, op['result']['message_id'])
        await collect(w)
        assert read(ops, w['target'], STARTER_OWNER)['used'] == 0


@pytest.mark.parametrize('status', ['failed', 'cancelled'])
async def test_terminal_certain_ack_has_a_collectable_marker(start_ready, mock, status):
    from tests.test_pm_attempt_result_marker import send_direct

    w = start_ready
    ops = w['d'].ops
    att, _ = await closed_attempt(w, mock)
    op = await send_direct(w, att, 'terminal-certain-ack')
    # Synthetic terminal bookkeeping fault after a real ACK. Ordinary cancel
    # after ACK is a no-op (covered above); it does not manufacture this state.
    with ops.journal.tx():
        ops.db.execute('UPDATE operations SET status=? WHERE operation_id=?', (status, op['operation_id']))
    assert len(pm_direct_inputs.eligible(ops.db, att['handoff_operation_id'],
        {'host': 'h1', 'session_id': att['session_id']})) == 1
    assert read(ops, w['target'], STARTER_OWNER)['used'] == 1
    terminal(mock, att, op['result']['message_id'])
    await collect(w)
    assert read(ops, w['target'], STARTER_OWNER)['used'] == 0
