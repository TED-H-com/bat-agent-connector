"""Every receipt statement stays on an existing index, without a temporary sort."""
from bat_agent_connector import pm_attempt_result_reads as result_read
from tests import test_pm_attempt_results as results
from tests.test_pm_attempt_result_marker import envelope, send_direct, terminal
from tests.test_pm_attempt_results_direct import collect
from tests.test_pm_work_start import STARTER_OWNER
from tests.test_pm_workspace_wip import read
from tests.test_pm_workspace_wip_lineage import closed_attempt

world = results.world
pm = results.pm
ready = results.ready
start_ready = results.start_ready


async def test_result_and_direct_receipt_statements_use_existing_indexes(start_ready, mock):
    w = start_ready
    att, _ = await closed_attempt(w, mock)
    ops = w['d'].ops
    op = await send_direct(w, att, 'query-plan-direct')
    terminal(mock, att, op['result']['message_id'])
    statements = []
    ops.db.set_trace_callback(statements.append)
    try:
        await collect(w)
        await envelope(w, att)
        await result_read.read(ops, STARTER_OWNER, result_read.CHANGES_GET,
            {'workspace_id': w['workspace_id'], 'attempt_id': att['attempt_id']})
        assert read(ops, w['target'], STARTER_OWNER)['used'] == 0
    finally:
        ops.db.set_trace_callback(None)
    queries = sorted({s for s in statements if s.lstrip().upper().startswith('SELECT')
                      and 'FROM session_results' in s})
    assert any('work_inputs i' in q for q in queries)
    for query in queries:
        plans = [r[3] for r in ops.db.execute('EXPLAIN QUERY PLAN ' + query)]
        print('RECEIPT SQL', query, 'PLAN', plans)
        assert all('SCAN' not in p and 'TEMP B-TREE' not in p for p in plans)
