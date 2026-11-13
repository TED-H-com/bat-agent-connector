"""Conflict evidence belongs to the requested Attempt, not a candidate row's owner."""

from tests import test_pm_attempt_results as results
from tests.operation_helpers import settle_operations
from tests.test_pm_attempt_result_marker import envelope, terminal
from tests.test_pm_work_start import STARTER_OWNER
from tests.test_pm_workspace_wip import clone_row
from tests.test_pm_workspace_wip_lineage import closed_attempt

world = results.world
pm = results.pm
ready = results.ready
start_ready = results.start_ready


async def test_conflict_query_uses_requested_attempt(start_ready, mock):
    w = start_ready
    ops = w["d"].ops
    att, rec = await closed_attempt(w, mock)
    # Real Work input belongs to this Attempt; a negative stored row will describe
    # a foreign Attempt with the same source position. Page isolation and conflict
    # scope must use the caller's Attempt, not the foreign row's own membership.
    op, _ = ops.create(
        STARTER_OWNER,
        action="session.send",
        target=w["target"],
        params={"text": "Synthetic own Work input", "schedule": "now"},
        preconditions={},
        idempotency_key="conflict-own-input",
    )
    await settle_operations(ops)
    op = ops.get(op["operation_id"])
    terminal(mock, att, op["result"]["message_id"])
    c = w["d"].result_collector
    c._memo.clear()
    await c.collect_host("h1")
    latest = dict(
        ops.db.execute("SELECT * FROM session_results WHERE operation_id=?", (op["operation_id"],)).fetchone()
    )
    wi = dict(
        ops.db.execute("SELECT * FROM work_inputs WHERE operation_id=?", (op["operation_id"],)).fetchone()
    )
    clone_row(
        ops.db,
        "work_inputs",
        wi,
        sequence=None,
        operation_id="op_foreign_conflict",
        attempt_id="att_foreign",
        position=999,
        message_id="marker_foreign",
    )
    clone_row(
        ops.db,
        "session_results",
        latest,
        sequence=None,
        result_id="res_foreign_conflict",
        attempt_id="att_foreign",
        operation_id="op_foreign_conflict",
        digest="sha256:" + "d" * 64,
    )
    statements = []
    ops.db.set_trace_callback(statements.append)
    try:
        value = await envelope(w, att)
    finally:
        ops.db.set_trace_callback(None)
    conflicts = [
        q for q in statements if q.lstrip().startswith("SELECT digest, document FROM session_results")
    ]
    assert len(conflicts) == 1
    for query in conflicts:
        plan = [r[3] for r in ops.db.execute("EXPLAIN QUERY PLAN " + query)]
        print("CONFLICT QUERY", query, "PLAN", plan)
        assert all("SCAN" not in p and "TEMP B-TREE" not in p for p in plan)
        assert not ops.db.execute(query).fetchall()

    assert "source_digest_conflict" not in value["completeness"]["reasons"]


async def test_daemon_registers_one_collector_owner(start_ready):
    daemon = start_ready["d"]
    collector = daemon.result_collector
    assert daemon.ops.context["attempt_result_collector"] is collector
    callbacks = [
        fn
        for fn in daemon.inventory.after_refresh
        if isinstance(getattr(fn, "__self__", None), type(collector))
    ]
    assert callbacks == [collector.after_refresh]
