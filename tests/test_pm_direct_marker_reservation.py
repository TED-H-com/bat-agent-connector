import pytest

from bat_agent_connector import pm_attempt_results as ar
from tests import test_pm_attempt_results as results
from tests.operation_helpers import settle_operations
from tests.test_pm_attempt_result_marker import envelope, terminal
from tests.test_pm_work_start import STARTER_OWNER
from tests.test_pm_workspace_wip_lineage import closed_attempt

world = results.world
pm = results.pm
ready = results.ready
start_ready = results.start_ready


@pytest.mark.parametrize("order", ["direct_first", "work_first"])
async def test_direct_and_work_marker_meeting_keeps_owned_result(start_ready, mock, order):
    w = start_ready
    ops = w["d"].ops
    att, _ = await closed_attempt(w, mock)
    sid = att["session_id"]
    mock.metas[sid].update(isStreaming=order == "direct_first", numTurns=1)
    mock.states[sid]["isStreaming"] = order == "direct_first"
    await w["d"].inventory.refresh_host("h1")
    text = "Synthetic same instruction"
    work, _ = ops.create(
        STARTER_OWNER,
        action="session.send",
        target=w["target"],
        params={"text": text, "schedule": "after_current" if order == "direct_first" else "now"},
        preconditions={},
        idempotency_key="collision-work",
    )
    await settle_operations(ops)
    work = ops.get(work["operation_id"])
    assert work["status"] == ("waiting_external" if order == "direct_first" else "succeeded")
    marker = "batc-" + work["operation_id"]
    if order == "work_first":
        terminal(mock, att, marker)
        c = w["d"].result_collector
        c._memo.clear()
        await c.collect_host("h1")
    mock.metas[sid]["isStreaming"] = False
    mock.states[sid]["isStreaming"] = False
    direct, _ = ops.create(
        STARTER_OWNER,
        action="session.send",
        target={"host": "h1", "session_id": sid},
        params={"text": text, "message_id": marker},
        preconditions={},
        idempotency_key="collision-direct",
    )
    await settle_operations(ops)
    direct = ops.get(direct["operation_id"])
    assert direct["status"] == "succeeded"
    terminal(mock, att, marker)
    mock.metas[sid]["numTurns"] = 3
    await w["d"].inventory.refresh_host("h1")
    c = w["d"].result_collector
    queries = []
    ops.db.set_trace_callback(queries.append)
    c._memo.clear()
    try:
        await c.collect_host("h1")
    finally:
        ops.db.set_trace_callback(None)
    if order == "direct_first":
        reservations = [q for q in queries if q.startswith(ar.RESERVED_WORK_MARKER_SQL.split("WHERE")[0])]
        assert reservations
        for query in reservations:
            plans = [r[3] for r in ops.db.execute("EXPLAIN QUERY PLAN " + query)]
            print("RESERVATION", query, plans)
            assert all("SCAN" not in p and "TEMP B-TREE" not in p for p in plans)
            assert len(ops.db.execute(query).fetchall()) == 1
    # At the prior boundary, the existing step wakes the Work input; direct collection
    # shares its exact client-supplied marker before the scheduler settles that input.
    await settle_operations(ops)
    work = ops.get(work["operation_id"])
    c._memo.clear()
    await c.collect_host("h1")
    c._memo.clear()
    await c.collect_host("h1")
    assert not direct["result"]["queued"]
    own = ar.receipts(
        ops.db, w["workspace_id"], att["attempt_id"], own_operation_id=att["handoff_operation_id"]
    )
    value = await envelope(w, att)
    assert work["status"] == "succeeded"
    assert any(r["turn_marker"] == marker for r in own["receipts"]), (
        "Dedup returned a direct-only receipt for a real Work-input turn"
    )
    assert value["execution"]["state"] == "turn-ended"
    from tests.test_pm_workspace_wip import read

    assert read(ops, w["target"], STARTER_OWNER)["used"] == 0
    frames = len(mock.invokes)
    c._memo.clear()
    await c.collect_host("h1")
    assert not any(f["channel"] == "claude:send-message" for f in mock.invokes[frames:])


async def test_five_hundred_reserved_direct_markers_use_bounded_seeks(start_ready, mock):
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
    w['d'].fleet.config.safety.max_writes_per_hour = 10000
    markers = []
    for n in range(500):
        op = await send_direct(w, att, f'reservation-cost-{n}', queue=True)
        markers.append(op['result']['message_id'])
    # Cost-only synthetic reservations, never release/admission evidence. The
    # 500 eligible direct operations above are real action/ACK records; cloning
    # the immutable queued row isolates the bounded membership lookup's cost.
    with ops.journal.tx():
        for n, marker in enumerate(markers):
            clone_row(ops.db, 'work_inputs', template, sequence=None,
                operation_id=f'op_reservation_cost_{n}', position=1000+n, message_id=marker)
    db = ops.db
    statements = []
    ticks = rows = 0
    original_factory = db.row_factory

    def progress():
        nonlocal ticks
        ticks += 1

    def counted(cursor, values):
        nonlocal rows
        if [c[0] for c in cursor.description] == ['operation_id', 'status', 'error_code', 'external_refs', 'step_status']:
            rows += 1
        return sqlite3.Row(cursor, values)

    db.set_trace_callback(statements.append)
    db.set_progress_handler(progress, 1)
    db.row_factory = counted
    started = time.perf_counter()
    try:
        assert collector._direct_candidate(candidate) is None
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
    assert collector._direct_candidate(candidate) is None
    without = time.perf_counter() - started
    print('RESERVED500 VM', ticks, 'reservation_rows', rows, 'queries', len(queries),
          'callback_seconds', elapsed, 'without_callback_seconds', without, 'PLAN', plan)
