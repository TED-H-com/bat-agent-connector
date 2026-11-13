"""Selected-marker decisions are independent of the newest receipt display page."""
import json
import time
import types

import pytest

from bat_agent_connector import pm_attempt_result_reads as result_read
from bat_agent_connector import pm_attempt_results
from tests import test_pm_attempt_results as results
from tests.operation_helpers import settle_operations
from tests.test_pm_work_start import STARTER_OWNER
from tests.test_pm_workspace_wip_lineage import closed_attempt

world = results.world
pm = results.pm
ready = results.ready
start_ready = results.start_ready


async def envelope(w, att):
    return await result_read.read(w['d'].ops, STARTER_OWNER, result_read.RESULT_GET,
        {'workspace_id': w['workspace_id'], 'attempt_id': att['attempt_id']})


async def send_direct(w, att, key, *, queue=False):
    ops = w['d'].ops
    op, _ = ops.create(STARTER_OWNER, action='session.send',
        target={'host': 'h1', 'session_id': att['session_id']},
        params={'text': 'Synthetic direct instruction', **({'queue': True} if queue else {})},
        preconditions={}, idempotency_key=key)
    await settle_operations(ops)
    op = ops.get(op['operation_id'])
    assert op['status'] == 'succeeded', (op['error_code'], op['status_reason'])
    return op


def terminal(mock, att, marker, *, queued=False):
    original = list(mock.states[att['session_id']]['messages'])
    results._set_turn_messages(mock, att['session_id'], marker,
        streaming=False, pending_ask=None, assistant_id='answer-' + marker)
    # Preserve preceding turn evidence for queued attribution and original markers.
    mock.states[att['session_id']]['messages'] = original + mock.states[att['session_id']]['messages']


async def measured_envelope(w, att):
    ticks = 0
    def progress():
        nonlocal ticks
        ticks += 1
    db = w['d'].ops.db
    db.set_progress_handler(progress, 1)
    try:
        value = await envelope(w, att)
    finally:
        db.set_progress_handler(None, 0)
    return value, ticks


@pytest.mark.parametrize('count', [21, 500])
async def test_selected_marker_decisions_survive_direct_receipt_page(start_ready, mock, monkeypatch, count):
    w = start_ready
    att, _ = await closed_attempt(w, mock)
    w['d'].fleet.config.safety.max_writes_per_hour = 10000  # Synthetic bulk-input fixture only.
    collector = w['d'].result_collector
    async def fixed_read():
        with monkeypatch.context() as patch:
            patch.setattr(result_read, 'time', types.SimpleNamespace(time=lambda: 1900000000., monotonic=time.monotonic))
            patch.setattr(w['d'].journal, 'api_head', lambda: 999999)
            return await measured_envelope(w, att)
    before, base_ticks = await fixed_read()
    collector._memo.clear()
    candidate = collector._eligible_candidates('h1')[0]
    snapshots = {0: before}
    ticks = {0: base_ticks}
    for n in range(count):
        op = await send_direct(w, att, f'marker-page-{n}')
        marker = op['result']['message_id']
        terminal(mock, att, marker)
        # The proposed selection only; all receipt data comes from the existing
        # producer path. This regression precedes the selector-extension commit.
        # Different read-only heads expose accidental direct-row action anchoring.
        mock.handlers['git:log'] = lambda p, n=n: [{'hash': f'{n + 1:040x}'}]
        row = await collector._collect_candidate(
            {**candidate, 'turn_marker': marker, 'receipt_operation_id': op['operation_id'],
             'direct_marker': {'queued': False}}, budget_s=20)
        mock.handlers.pop('git:log')
        assert row is not None
        if n + 1 in {19, 20, 21, 500}:
            snapshots[n + 1], ticks[n + 1] = await fixed_read()
    print('RESULT READ VM', ticks)
    for n, value in snapshots.items():
        assert value['execution'] == before['execution'], n
        assert value['next_allowed_actions'] == before['next_allowed_actions'], n
        assert json.dumps(value, sort_keys=True) == json.dumps(before, sort_keys=True), n
    plans = [r[3] for r in w['d'].ops.db.execute('EXPLAIN QUERY PLAN ' + pm_attempt_results.OWN_RECEIPTS_SQL,
        (w['workspace_id'], att['attempt_id'], att['handoff_operation_id'], 21))]
    print('SELECTED MARKER PLAN', plans)
    assert any('SEARCH' in p and 'session_results_by_attempt' in p for p in plans)
    assert all('SCAN' not in p and 'TEMP B-TREE' not in p for p in plans)


async def test_selected_marker_page_fast_path_is_byte_identical(start_ready, mock, monkeypatch):
    w = start_ready
    att, _ = await closed_attempt(w, mock)
    monkeypatch.setattr(result_read.time, 'time', lambda: 1900000000.0)
    current = await envelope(w, att)
    def original_page_selector(ops, wid, aid, marker, page):
        return next((r for r in page if r['turn_marker'] == marker), None)
    monkeypatch.setattr(result_read, '_selected_receipt', original_page_selector)
    original = await envelope(w, att)
    assert json.dumps(current, sort_keys=True) == json.dumps(original, sort_keys=True)
