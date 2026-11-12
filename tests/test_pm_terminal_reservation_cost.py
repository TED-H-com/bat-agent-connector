"""Indexed original-owner checks at the existing 500 direct-marker bound."""
from bat_agent_connector import pm_attempt_results as ar
from tests import test_pm_attempt_results as results
from tests.operation_helpers import settle_operations
from tests.test_pm_work_start import STARTER_OWNER
from tests.test_pm_workspace_wip_lineage import closed_attempt

world = results.world
pm = results.pm
ready = results.ready
start_ready = results.start_ready


async def test_five_hundred_terminal_reservations_are_bounded(start_ready, mock):
    import json
    import sqlite3
    import time

    from tests.test_pm_attempt_result_marker import send_direct
    from tests.test_pm_workspace_wip import clone_row

    w = start_ready
    ops = w['d'].ops
    att, _ = await closed_attempt(w, mock)
    collector = w['d'].result_collector
    collector._memo.clear()
    candidate = collector._eligible_candidates('h1')[0]
    sid = att['session_id']
    mock.metas[sid]['isStreaming'] = mock.states[sid]['isStreaming'] = True
    await w['d'].inventory.refresh_host('h1')
    work, _ = ops.create(STARTER_OWNER, action='session.send', target=w['target'],
        params={'text': 'Synthetic reservation template', 'schedule': 'after_current'},
        preconditions={}, idempotency_key='reservation-template')
    await settle_operations(ops)
    template = dict(ops.db.execute('SELECT * FROM work_inputs WHERE operation_id=?',
                                 (work['operation_id'],)).fetchone())
    operation_template = dict(ops.db.execute('SELECT * FROM operations WHERE operation_id=?',
                                            (work['operation_id'],)).fetchone())
    w['d'].fleet.config.safety.max_writes_per_hour = 10000
    markers = []
    for n in range(500):
        op = await send_direct(w, att, f'reservation-cost-{n}', queue=True)
        markers.append(op['result']['message_id'])
    step_template = dict(ops.db.execute("SELECT * FROM operation_steps WHERE operation_id=? AND name='send'",
                                       (op['operation_id'],)).fetchone())
    # Synthetic cost-only original rows. They exercise 500 owner/step lookups;
    # no result receipt or release action is produced from these synthetic rows.
    with ops.journal.tx():
        for n, marker in enumerate(markers):
            oid = f'op_terminal_cost_{n}'
            refs = json.loads(operation_template['external_refs'])
            refs['send_sent'] = n != 499
            clone_row(ops.db, 'operations', operation_template, operation_id=oid,
                      idem_key=oid, status='failed', error_code='REFUSED',
                      external_refs=json.dumps(refs))
            clone_row(ops.db, 'operation_steps', step_template, operation_id=oid,
                      status='failed', response=None, error=json.dumps({'code': 'REFUSED'}))
            clone_row(ops.db, 'work_inputs', template, sequence=None,
                      operation_id=oid, position=1000+n, message_id=marker)
    db = ops.db
    statements = []
    ticks = rows = 0
    original_factory = db.row_factory

    def progress():
        nonlocal ticks
        ticks += 1

    def counted(cursor, values):
        nonlocal rows
        if [c[0] for c in cursor.description] in (['1'],
                ['operation_id', 'status', 'error_code', 'external_refs', 'step_status']):
            rows += 1
        return sqlite3.Row(cursor, values)

    db.set_trace_callback(statements.append)
    db.set_progress_handler(progress, 1)
    db.row_factory = counted
    started = time.perf_counter()
    try:
        selected = collector._direct_candidate(candidate)
    finally:
        elapsed = time.perf_counter() - started
        db.set_progress_handler(None, 0)
        db.set_trace_callback(None)
        db.row_factory = original_factory
    queries = [q for q in statements if q.startswith(ar.RESERVED_WORK_MARKER_SQL.split("WHERE")[0])]
    assert len(queries) == rows == 500
    plan = [r[3] for r in db.execute('EXPLAIN QUERY PLAN '+ar.RESERVED_WORK_MARKER_SQL,
        (markers[0], w['workspace_id'], att['attempt_id']))]
    assert all('SCAN' not in p and 'TEMP B-TREE' not in p for p in plan)
    started = time.perf_counter()
    second = collector._direct_candidate(candidate)
    without = time.perf_counter() - started
    print('TERMINAL_RESERVED500 VM', ticks, 'reservation_rows', rows, 'queries', len(queries),
          'callback_seconds', elapsed, 'without_callback_seconds', without, 'PLAN', plan)
    assert selected is not None and selected['turn_marker'] == markers[-1]
    assert second is not None and second['turn_marker'] == markers[-1]
