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
        reservations = [q for q in queries if q.startswith("SELECT 1 FROM work_inputs")]
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
