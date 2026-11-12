"""Receipt work grows with history once, not once per direct marker."""
import sqlite3
import time

from tests import test_pm_attempt_results as results
from tests.test_pm_attempt_result_marker import send_direct, terminal
from tests.test_pm_work_start import STARTER_OWNER
from tests.test_pm_workspace_wip import read
from tests.test_pm_workspace_wip_lineage import closed_attempt

world = results.world
pm = results.pm
ready = results.ready
start_ready = results.start_ready


async def test_five_hundred_terminal_markers_use_one_receipt_pass(start_ready, mock):
    w = start_ready
    ops = w['d'].ops
    att, _ = await closed_attempt(w, mock)
    w['d'].fleet.config.safety.max_writes_per_hour = 10000
    collector = w['d'].result_collector
    collector.pass_budget_s = 120
    collector._memo.clear()
    candidate = collector._eligible_candidates('h1')[0]
    # Real admissions and actual collector receipts: never synthetic release proof.
    for n in range(500):
        op = await send_direct(w, att, f'receipt-cost-{n}')
        terminal(mock, att, op['result']['message_id'])
        assert await collector._collect_candidate({**candidate,
            'turn_marker': op['result']['message_id'], 'receipt_operation_id': op['operation_id'],
            'direct_marker': {'queued': False}}, budget_s=20)
    db = ops.db
    async def measure(kind):
        ticks = rows = 0
        def progress():
            nonlocal ticks
            ticks += 1
        def row_factory(cursor, values):
            nonlocal rows
            columns = {c[0] for c in cursor.description}
            if {'source_position', 'document'} <= columns:
                rows += 1
            return sqlite3.Row(cursor, values)
        old = db.row_factory
        db.row_factory = row_factory
        db.set_progress_handler(progress, 1)
        start = time.perf_counter()
        try:
            if kind == 'wip':
                assert read(ops, w['target'], STARTER_OWNER)['used'] == 0
            else:
                collector._memo.clear()
                await collector.collect_host('h1')
        finally:
            elapsed = time.perf_counter() - start
            db.set_progress_handler(None, 0)
            db.row_factory = old
        print('TERMINAL500', kind, 'VM', ticks, 'receipt_rows', rows, 'callback_seconds', elapsed)
        return rows
    wip_rows = await measure('wip')
    collector_rows = await measure('collector')
    assert wip_rows <= 3 * 501
    assert collector_rows <= 3 * 502
