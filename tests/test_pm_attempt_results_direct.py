"""Direct inputs use the original collector and the unchanged Work-input queue."""
import copy
import json
import time
import types

import pytest

from bat_agent_connector import operations, pm_attempt_results, pm_direct_inputs, pm_work_follow_up
from tests import test_pm_attempt_results as results
from tests.operation_helpers import settle_operations
from tests.test_pm_attempt_result_marker import send_direct, terminal
from tests.test_pm_work_start import STARTER_OWNER
from tests.test_pm_workspace_wip_lineage import closed_attempt

world = results.world
pm = results.pm
ready = results.ready
start_ready = results.start_ready


async def collect(w):
    w['d'].result_collector._memo.clear()
    return await w['d'].result_collector.collect_host('h1')


@pytest.mark.parametrize('variant', ['one', 'two', 'work_first', 'work_last'])
async def test_direct_markers_use_real_collector(start_ready, mock, variant):
    w = start_ready
    att, _ = await closed_attempt(w, mock)
    ops = w['d'].ops
    expected = []
    order = ['work', 'direct'] if variant == 'work_first' else (
        ['direct', 'work'] if variant == 'work_last' else ['direct'] * (2 if variant == 'two' else 1))
    for n, kind in enumerate(order):
        if kind == 'direct':
            op = await send_direct(w, att, f'collector-direct-{n}')
        else:
            op, _ = ops.create(STARTER_OWNER, action='session.send', target=w['target'],
                params={'text': 'Synthetic Work follow-up', 'schedule': 'now'},
                preconditions={}, idempotency_key='collector-work-input')
            await settle_operations(ops)
            op = ops.get(op['operation_id'])
            assert op['status'] == 'succeeded'
        marker = op['result']['message_id']
        expected.append((marker, op['operation_id']))
        terminal(mock, att, marker)
        # The next Work input still uses the existing predecessor receipt.
        if kind == 'work':
            assert await collect(w)
    for _ in range(len(expected) + 1):
        await collect(w)
    rows = pm_attempt_results.receipts(ops.db, w['workspace_id'], att['attempt_id'])['receipts']
    assert set(expected) <= {(r['turn_marker'], r['operation_id']) for r in rows}
    assert all(pm_work_follow_up._has_turn_boundary_receipt(ops.db, w['workspace_id'], att['attempt_id'], m)
               for m, _ in expected)


async def test_queued_direct_marker_records_original_attribution(start_ready, mock):
    w = start_ready
    att, _ = await closed_attempt(w, mock)
    sid = att['session_id']
    mock.metas[sid].update(isStreaming=True, numTurns=1)
    mock.states[sid]['isStreaming'] = True
    op = await send_direct(w, att, 'collector-queued-direct', queue=True)
    marker = op['result']['message_id']
    assert op['result']['queued'] is True
    terminal(mock, att, marker)
    mock.metas[sid]['numTurns'] = 2
    prior = await collect(w)  # Existing step still records/wakes at the prior boundary.
    assert not any(r['turn_marker'] == marker for r in prior)
    mock.states[sid]['messages'].append({'id': 'queued-terminal-answer', 'role': 'assistant',
        'content': 'Synthetic queued turn completed', 'timestamp': 1790001020000})
    mock.metas[sid]['numTurns'] = 3
    rows = await collect(w)
    rows = [r for r in rows if r['turn_marker'] == marker]
    assert len(rows) == 1
    assert rows[0]['operation_id'] == op['operation_id']
    assert rows[0]['document']['turn_attribution'] == 'correlated_after_prior_turn'
    assert rows[0]['document']['boundary'] == 'turn_done'


async def test_direct_waits_for_existing_marker_boundary(start_ready, mock):
    w = start_ready
    att = await results._start_managed_attempt(w, mock)
    op = await send_direct(w, att, 'collector-before-initial-receipt')
    terminal(mock, att, op['result']['message_id'])
    first = await collect(w)
    assert [r['turn_marker'] for r in first] == [att['turn_marker'], op['result']['message_id']]


async def test_no_direct_extension_preserves_receipts_and_checks(start_ready, mock, monkeypatch):
    from bat_agent_connector import pm_direct_inputs
    w = start_ready
    att = await results._start_managed_attempt(w, mock)
    results._set_turn_messages(mock, att['session_id'], att['turn_marker'])
    collector = w['d'].result_collector
    monkeypatch.setattr(pm_attempt_results, 'time', types.SimpleNamespace(
        time=lambda: 1900000000.0, monotonic=lambda: 1000.0))
    monkeypatch.setattr(pm_attempt_results, 'new_result_id', lambda: 'sr_' + 'a' * 32)
    db = w['d'].ops.db
    values = []
    for enabled in (True, False):
        db.execute('SAVEPOINT same_fixture')
        collector._memo.clear()
        collector._last_checked_at.clear()
        collector.last_check.clear()
        try:
            with monkeypatch.context() as patch:
                if not enabled:
                    patch.setattr(pm_direct_inputs, 'eligible', lambda *args, **kwargs: [])
                for _ in range(2):
                    await collector.collect_host('h1')
            values.append((pm_attempt_results.receipts(db, w['workspace_id'], att['attempt_id']),
                           copy.deepcopy(collector.last_check), copy.deepcopy(collector._memo),
                           copy.deepcopy(collector.direct_last_check)))
        finally:
            db.execute('ROLLBACK TO same_fixture')
            db.execute('RELEASE same_fixture')
    assert json.dumps(values[0], sort_keys=True) == json.dumps(values[1], sort_keys=True)


@pytest.mark.parametrize('enabled', [False, True])
async def test_direct_selection_preserves_queue_release_point(start_ready, mock, monkeypatch, enabled):
    from bat_agent_connector import pm_direct_inputs
    w = start_ready
    att = await results._start_managed_attempt(w, mock)
    sid = att['session_id']
    # Original marker has no receipt; a direct input exists and a Work input queues.
    direct = await send_direct(w, att, 'queue-direct')
    terminal(mock, att, direct['result']['message_id'])
    mock.metas[sid]['isStreaming'] = mock.states[sid]['isStreaming'] = True
    await w['d'].inventory.refresh_host('h1')
    queued, _ = w['d'].ops.create(STARTER_OWNER, action='session.send', target=w['target'],
        params={'text': 'Queued synthetic Work input', 'schedule': 'after_current'},
        preconditions={}, idempotency_key='queue-work-input')
    await settle_operations(w['d'].ops)
    assert w['d'].ops.get(queued['operation_id'])['status'] == 'waiting_external'
    if not enabled:
        monkeypatch.setattr(pm_direct_inputs, 'eligible', lambda *args, **kwargs: [])
    assert not await collect(w)
    assert not any(f.get('params', {}).get('clientMessageId') == 'batc-' + queued['operation_id']
                   for f in mock.invokes)
    mock.metas[sid]['isStreaming'] = mock.states[sid]['isStreaming'] = False
    await w['d'].inventory.refresh_host('h1')
    await collect(w)
    await settle_operations(w['d'].ops)
    assert w['d'].ops.get(queued['operation_id'])['status'] == 'succeeded'
    assert sum(f.get('params', {}).get('clientMessageId') == 'batc-' + queued['operation_id']
               for f in mock.invokes) == 1


@pytest.mark.parametrize('enabled',[False,True])
async def test_queue_wake_at_prior_boundary(start_ready,mock,monkeypatch,enabled):
    w = start_ready
    ops = w['d'].ops
    att,_=await closed_attempt(w,mock)
    sid=att['session_id']
    # A later BAT turn is active; the direct input is accepted into BAT's queue.
    mock.metas[sid].update(isStreaming=True,numTurns=1)
    mock.states[sid]['isStreaming']=True
    direct=await send_direct(w,att,'queue-boundary-direct',queue=True)
    assert direct['result']['queued'] is True
    await w['d'].inventory.refresh_host('h1')
    now=time.time()
    # Freeze only the operation scheduler's clock so the existing 5-second poll
    # cannot accidentally hide a missing collector wake during a slow fixture.
    monkeypatch.setattr(operations,'time',types.SimpleNamespace(time=lambda:now,monotonic=time.monotonic))
    follow,_=ops.create(STARTER_OWNER,action='session.send',target=w['target'],
        params={'text':'Synthetic queued Work input','schedule':'after_current'},
        preconditions={},idempotency_key='queue-boundary-work')
    await settle_operations(ops)
    assert ops.get(follow['operation_id'])['status']=='waiting_external'
    terminal(mock,att,direct['result']['message_id'])
    mock.metas[sid]['numTurns']=2
    await w['d'].inventory.refresh_host('h1')
    if not enabled:
        monkeypatch.setattr(pm_direct_inputs,'eligible',lambda *a,**kw:[])
    w['d'].result_collector._memo.clear()
    receipts=await w['d'].result_collector.collect_host('h1')
    await settle_operations(ops)
    result=ops.get(follow['operation_id'])
    frames=sum(f.get('params',{}).get('clientMessageId')=='batc-'+follow['operation_id'] for f in mock.invokes)
    print('QUEUE PRIOR BOUNDARY',enabled,result['status'],frames,
        w['d'].result_collector.last_check[att['attempt_id']],len(receipts),flush=True)
    assert result['status']=='succeeded' and frames==1


async def test_direct_step_never_wakes_or_changes_existing_memo(start_ready, mock, monkeypatch):
    w = start_ready
    att, _ = await closed_attempt(w, mock)
    op = await send_direct(w, att, 'isolated-direct-step')
    terminal(mock, att, op['result']['message_id'])
    collector = w['d'].result_collector
    wakes, reads = [], []
    wake = collector._wake_follow_up_if_idle
    collect_one = collector._collect_candidate
    async def traced(cand, **kwargs):
        direct = 'direct_marker' in cand
        memo = copy.deepcopy(collector._memo)
        before = len(wakes)
        reads.append(cand['turn_marker'])
        row = await collect_one(cand, **kwargs)
        if direct:
            assert collector._memo == memo
            assert len(wakes) == before
        return row
    def waking(*args, **kwargs):
        wakes.append(args)
        return wake(*args, **kwargs)
    monkeypatch.setattr(collector, '_wake_follow_up_if_idle', waking)
    monkeypatch.setattr(collector, '_collect_candidate', traced)
    await collect(w)
    assert len(wakes) == 1
    assert reads == [att['turn_marker'], op['result']['message_id']]
    print('ELIGIBLE PASS existing_reads=1 direct_reads=1 queue_wakes=1')
    assert collector.direct_last_check[att['attempt_id']]['outcome'] == 'recorded'
    # Same observation, within the memo interval: neither step runs again.
    await collector.collect_host('h1')
    assert len(wakes) == 1 and len(reads) == 2


async def test_direct_step_respects_remaining_pass_budget(start_ready, mock, monkeypatch):
    w = start_ready
    att, _ = await closed_attempt(w, mock)
    op = await send_direct(w, att, 'direct-after-budget')
    terminal(mock, att, op['result']['message_id'])
    collector = w['d'].result_collector
    original = collector._collect_candidate
    selected = []
    async def exhausted(cand, **kwargs):
        row = await original(cand, **kwargs)
        collector.pass_budget_s = 0
        return row
    def direct(cand):
        selected.append(cand)
        raise AssertionError('Direct eligibility must not run after the budget ends')
    monkeypatch.setattr(collector, '_collect_candidate', exhausted)
    monkeypatch.setattr(collector, '_direct_candidate', direct)
    rows = await collect(w)
    assert rows and rows[0]['turn_marker'] == att['turn_marker']
    assert not selected and not collector.direct_last_check
