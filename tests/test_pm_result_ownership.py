from bat_agent_connector import pm_attempt_result_reads as rr
from bat_agent_connector import pm_attempt_results as ar
from bat_agent_connector import pm_kanban, pm_work_packages, work_items
from bat_agent_connector import pm_work_follow_up as follow
from bat_agent_connector.api_auth import Principal
from tests import test_pm_attempt_results as results
from tests.test_pm_attempt_result_marker import send_direct, terminal
from tests.test_pm_work_start import STARTER_OWNER
from tests.test_pm_workspace_wip_lineage import closed_attempt

world = results.world
pm = results.pm
ready = results.ready
start_ready = results.start_ready


async def test_extra_direct_receipt_does_not_change_decisions(start_ready, mock, monkeypatch):
    w = start_ready
    ops = w["d"].ops
    att, _ = await closed_attempt(w, mock)
    person = Principal(STARTER_OWNER.actor, STARTER_OWNER.scopes | {"integrate"})
    op = await send_direct(w, att, "consumer-direct")
    terminal(mock, att, op["result"]["message_id"])
    snapshots = []
    for enabled in [False, True]:
        collector = ar.AttemptResultCollector(ops, fleet=w["d"].result_collector.fleet)
        if not enabled:
            monkeypatch.setattr(collector, "_direct_candidate", lambda cand: None)
        # Simulate a read-only BAT Git observation failing between the unchanged
        # existing step and its new direct step. No local/remote mutation occurs.
        collect_one = collector._collect_candidate

        async def traced(cand, **kw):
            if "direct_marker" not in cand:
                return await collect_one(cand, **kw)
            mock.handlers["git:log"] = lambda p: []
            try:
                return await collect_one(cand, **kw)
            finally:
                mock.handlers.pop("git:log", None)

        monkeypatch.setattr(collector, "_collect_candidate", traced)
        ops.db.execute("SAVEPOINT consumers")
        try:
            monkeypatch.setitem(ops.context, "attempt_result_collector", collector)
            rows = await collector.collect_host("h1")
            result = await rr.read(
                ops,
                person,
                rr.RESULT_GET,
                {"workspace_id": w["workspace_id"], "attempt_id": att["attempt_id"]},
            )
            package = ops.get(att["handoff_operation_id"])["params"]["pm_package_ref"]["package_id"]
            facts = {
                "actions": result["next_allowed_actions"],
                "blocked": result["blocked_actions"],
                "execution": result["execution"],
                "changes": result["changes"],
                "readiness": pm_work_packages.readiness(ops, person, w["workspace_id"], package),
                "approval": work_items.completion(work_items._get_item(ops.db, w["target"]["work_item_id"])),
                "flow": pm_kanban.get_card(ops, person, **w["target"])["card"]["flow"],
                "selected_boundary": follow._has_turn_boundary_receipt(
                    ops.db, w["workspace_id"], att["attempt_id"], att["turn_marker"]
                ),
                "receipt_count": len(rows),
            }
            snapshots.append(facts)
        finally:
            ops.db.execute("ROLLBACK TO consumers")
            ops.db.execute("RELEASE consumers")
    for n, x in enumerate(snapshots):
        print(
            "CONSUMER",
            n,
            "allowed",
            [a.get("action") for a in x["actions"]],
            "blocked",
            x["blocked"],
            "receipts",
            x["receipt_count"],
        )
    assert snapshots[0]["readiness"] == snapshots[1]["readiness"]
    assert snapshots[0]["approval"] == snapshots[1]["approval"]
    assert snapshots[0]["flow"] == snapshots[1]["flow"]
    assert snapshots[0]["selected_boundary"] == snapshots[1]["selected_boundary"]
    assert (
        snapshots[0]["actions"] == snapshots[1]["actions"]
        and snapshots[0]["blocked"] == snapshots[1]["blocked"]
    ), "Direct receipt changes E04 action decision"


async def test_absent_selected_marker_does_not_walk_old_history(start_ready, mock):
    from tests.operation_helpers import settle_operations
    from tests.test_pm_attempt_result_marker import envelope

    w = start_ready
    ops = w["d"].ops
    att, _ = await closed_attempt(w, mock)
    collector = w["d"].result_collector
    collector._memo.clear()
    cand = collector._eligible_candidates("h1")[0]

    async def snapshots(start, end):
        for n in range(start, end):
            results._set_turn_messages(mock, att["session_id"], att["turn_marker"], assistant_id=f"old-{n}")
            assert await collector._collect_candidate(cand, budget_s=20)

    await snapshots(0, 510)
    op, _ = ops.create(
        STARTER_OWNER,
        action="session.send",
        target=w["target"],
        params={"text": "Synthetic new Work turn", "schedule": "now"},
        preconditions={},
        idempotency_key="missing-selected-marker",
    )
    await settle_operations(ops)
    assert ops.get(op["operation_id"])["status"] == "succeeded"
    sql = []
    ops.db.set_trace_callback(sql.append)
    try:
        await envelope(w, att)
    finally:
        ops.db.set_trace_callback(None)
    assert not any("json_extract(source_position" in q for q in sql)
    page = next(q for q in sql if "SELECT * FROM session_results" in q and "LIMIT 21" in q)

    def measured_page():
        ticks = 0

        def progress():
            nonlocal ticks
            ticks += 1

        ops.db.set_progress_handler(progress, 1)
        try:
            rows = ops.db.execute(page).fetchall()
        finally:
            ops.db.set_progress_handler(None, 0)
        return len(rows), ticks

    first = measured_page()
    await snapshots(510, 1020)
    second = measured_page()
    plan = [r[3] for r in ops.db.execute("EXPLAIN QUERY PLAN " + page)]
    print("OWN PAGE absent marker rows/VM at 511/1021 history", first, second, plan)
    assert first[0] == second[0] == 21
    assert second[1] <= first[1] * 1.2
    assert all("SCAN" not in p and "TEMP B-TREE" not in p for p in plan)


async def test_process_ended_direct_observations_add_no_receipt_history(start_ready, mock):
    from tests.test_pm_attempt_results_direct import collect
    from tests.test_pm_workspace_wip import read

    w = start_ready
    att, _ = await closed_attempt(w, mock)
    op = await send_direct(w, att, "ended-direct-bound")
    for n in range(3):
        terminal(mock, att, op["result"]["message_id"])
        mock.states[att["session_id"]]["messages"][-1]["id"] = f"ended-{n}"
        mock.metas[att["session_id"]]["runtimeStatus"] = "exited"
        await w["d"].inventory.refresh_host("h1")
        await collect(w)
    assert (
        w["d"]
        .ops.db.execute("SELECT count(*) FROM session_results WHERE operation_id=?", (op["operation_id"],))
        .fetchone()[0]
        == 0
    )
    assert read(w["d"].ops, w["target"], STARTER_OWNER)["used"] == 1
    assert (
        w["d"].result_collector.direct_last_check[att["attempt_id"]]["outcome"]
        == "direct_boundary_not_terminal"
    )
