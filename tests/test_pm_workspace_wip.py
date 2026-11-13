"""Workspace-wide advisory facts from original records, without admission changes."""
from __future__ import annotations

import copy
import json
import sqlite3
import time
import uuid

import pytest

from bat_agent_connector import (
    pm_attempt_results,
    pm_kanban,
    pm_lifecycle,
    pm_wip_count,
    pm_work_adopt_and_start,
    pm_work_batches,
    pm_work_follow_up,
    pm_work_start,
    work_items,
)
from bat_agent_connector import (
    pm_workspace_wip as wip,
)
from bat_agent_connector.api_auth import Principal
from bat_agent_connector.operations import OperationError
from tests.test_pm_discussions import CONTRIBUTOR, OUTSIDER, OWNER, READER, settle
from tests.test_pm_evidence import bind_work, item, prepared
from tests.test_pm_evidence import ops as ops
from tests.test_pm_kanban import decide, update


def install(ops):
    pm_lifecycle.schema(ops.journal)
    pm_work_start.schema(ops.journal)
    pm_work_batches.schema(ops.journal)
    pm_work_adopt_and_start.install(ops)
    pm_work_follow_up.schema(ops.journal)
    pm_attempt_results.schema(ops.journal)
    wip.install(ops)


def request(wid, *, mode="advisory", limit=5, revision=0, digest=None):
    return {"action": wip.ACTION, "target": {"workspace_id": wid},
            "params": {"policy": {"schema_version": 1, "mode": mode, "limit": limit,
                "counting_basis": wip.BASIS, "basis": {"assumptions": "One implementation and review team", "review_on": None}},
                "rationale": "Record this period's reviewed capacity"},
            "preconditions": {"expected_policy_revision": revision, "expected_policy_digest": digest},
            "idempotency_key": uuid.uuid4().hex}


def read(ops, target, who=OWNER, **kwargs):
    return wip.get_wip(ops, who, target["workspace_id"], **kwargs)


async def save(ops, env, who=OWNER):
    op, _ = ops.create(who, **env)
    return await settle(ops, op)


async def test_workspace_total_ignores_page_filter_and_preserves_page_metric(ops):
    target = await prepared(ops)
    install(ops)
    await update(ops, target, state="doing")
    wi = await item(ops, work_items._get_item(ops.db, target["work_item_id"])["project_id"])
    second = {**target, "work_item_id": wi}
    await bind_work(ops, second)
    await update(ops, second, state="waiting")
    value = read(ops, target)
    assert value["used"] == 2
    assert value["breakdown"] == {"management_wip": 2, "unresolved_execution": 0, "accepted_selection": 0}
    page = pm_kanban.get_board(ops, OWNER, target["workspace_id"], limit=1)
    assert page["wip"]["count"] == 1 and page["wip"]["limit"] is None
    assert page["capabilities"]["wip_limit"] == {"status": "not_configured", "value": None}
    empty = pm_kanban.get_board(ops, OWNER, target["workspace_id"], project_id="prj_" + "f" * 20)
    assert empty["cards"] == []
    assert read(ops, target)["used"] == 2
    await save(ops, request(target["workspace_id"], limit=1))
    value = read(ops, target)
    assert value["used"] == 2 and value["remaining"] == 0 and value["over_limit"] is True
    assert pm_kanban._capabilities(ops, target["workspace_id"])["wip_limit"]["read"] == wip.READ


async def test_policy_cas_history_replay_conflict_and_disabled(ops):
    target = await prepared(ops)
    install(ops)
    env = request(target["workspace_id"])
    first = await save(ops, env)
    assert first["status"] == "succeeded", first
    original = read(ops, target)["policy"]
    second = await save(ops, request(target["workspace_id"], mode="disabled", limit=None,
                                   revision=1, digest=original["document_digest"]))
    assert second["status"] == "succeeded"
    value = read(ops, target)
    assert value["remaining"] is None and value["over_limit"] is None
    assert value["effective_policy_revision"] == value["selected_policy_revision"] == 2
    old = read(ops, target, policy_revision=1)
    assert old["policy"] == original and old["effective_policy_revision"] == 2
    replay, created = ops.create(OWNER, **env)
    assert not created and replay["operation_id"] == first["operation_id"]
    changed = copy.deepcopy(env)
    changed["params"]["policy"]["limit"] = 2
    with pytest.raises(OperationError, match="different request"):
        ops.create(OWNER, **changed)
    with pytest.raises(OperationError) as exc:
        ops.create(OWNER, **request(target["workspace_id"]))
    assert exc.value.code == "PM_WIP_POLICY_CHANGED"


@pytest.mark.parametrize("mode", ["enforced", "unsupported"])
async def test_enforced_mode_is_unsupported(ops, mode):
    target = await prepared(ops)
    install(ops)
    with pytest.raises(OperationError) as exc:
        ops.create(OWNER, **request(target["workspace_id"], mode=mode))
    assert exc.value.code == "PM_WIP_MODE_UNSUPPORTED"


@pytest.mark.parametrize("who,allowed", [(OWNER, True), (CONTRIBUTOR, False), (READER, False),
    (Principal("fixture-admin", frozenset(), admin=True), True)])
async def test_policy_owner_administrator_contributor_reader_authority(ops, who, allowed):
    target = await prepared(ops)
    install(ops)
    assert read(ops, target, who)["used"] == 0
    if allowed:
        result = await save(ops, request(target["workspace_id"]), who)
        assert result["status"] == "succeeded", result
    else:
        with pytest.raises(OperationError) as exc:
            ops.create(who, **request(target["workspace_id"]))
        assert exc.value.status == 403
    with pytest.raises(OperationError) as exc:
        read(ops, target, OUTSIDER)
    assert exc.value.status == 404


async def test_schema_twice_preserves_version_ids_and_history(ops):
    target = await prepared(ops)
    before_version = ops.db.execute("PRAGMA user_version").fetchone()[0]
    install(ops)
    saved = await save(ops, request(target["workspace_id"]))
    before = [tuple(row) for row in ops.db.execute("SELECT * FROM pm_workspace_wip_policies")]
    wip.schema(ops.journal)
    assert before == [tuple(row) for row in ops.db.execute("SELECT * FROM pm_workspace_wip_policies")]
    assert ops.db.execute("PRAGMA user_version").fetchone()[0] == before_version
    assert before[0][-1] == saved["operation_id"]
    for statement in ("UPDATE pm_workspace_wip_policies SET revision=9", "DELETE FROM pm_workspace_wip_policies"):
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            ops.db.execute(statement)


@pytest.mark.parametrize("bound", ["MAX_WORKS", "MAX_RECORDS"])
async def test_bound_overflow_matches_hidden_source_without_counts(ops, monkeypatch, bound):
    target = await prepared(ops)
    install(ops)
    monkeypatch.setattr(wip, bound, 0)
    limited = read(ops, target)
    assert limited["coverage"]["reasons"] == ["coverage_unavailable"]
    assert limited["used"] is limited["remaining"] is limited["over_limit"] is None
    assert "breakdown" not in limited and "known_used" not in limited
    monkeypatch.undo()
    operation = ops.db.execute("SELECT operation_id FROM pm_work_bindings WHERE workspace_id=?", (target["workspace_id"],)).fetchone()[0]
    original = ops.get
    def hidden(oid, **kwargs):
        if oid == operation and kwargs.get("principal"):
            raise OperationError("NOT_FOUND", "Source unavailable", 404)
        return original(oid, **kwargs)
    monkeypatch.setattr(ops, "get", hidden)
    invisible = read(ops, target)
    for key in ("coverage", "used", "remaining", "over_limit"):
        assert invisible[key] == limited[key]
    assert "breakdown" not in invisible and "known_used" not in invisible


async def test_missing_visible_history_is_unknown_not_zero(ops):
    target = await prepared(ops)
    install(ops)
    ops.db.execute("DELETE FROM api_events WHERE resource_type='work_item' AND resource_id=?", (target["work_item_id"],))
    value = read(ops, target)
    assert value["used"] is None and value["coverage"]["reasons"] == ["missing_history"]


async def test_management_closure_and_reopening_follow_existing_flow(ops):
    target = await prepared(ops)
    install(ops)
    await update(ops, target, state="doing")
    await update(ops, target, state="todo")
    assert read(ops, target)["used"] == 1
    await update(ops, target, state="done")
    assert read(ops, target)["used"] == 1
    await decide(ops, target)
    assert read(ops, target)["used"] == 0
    await decide(ops, target, "continue")
    assert read(ops, target)["used"] == 1


async def test_reads_write_nothing_and_probe_nothing(ops, monkeypatch):
    target = await prepared(ops)
    install(ops)
    def forbidden(*args, **kwargs):
        pytest.fail("Read must not create schema, an operation or probe a provider")
    monkeypatch.setattr(wip, "schema", forbidden)
    monkeypatch.setattr(ops, "create", forbidden)
    tables = [row[0] for row in ops.db.execute("SELECT name FROM sqlite_master WHERE type='table'")]
    def counts():
        return [ops.db.execute('SELECT COUNT(*) FROM "' + name.replace('"', '""') + '"').fetchone()[0] for name in tables]
    before, changes = counts(), ops.db.total_changes
    for _ in range(3):
        assert read(ops, target)["used"] == 0
    assert counts() == before and ops.db.total_changes == changes


async def test_workspace_indexes_have_search_plans(ops):
    target = await prepared(ops)
    install(ops)
    for sql, args, index in (
        (pm_wip_count.ROOT_SQL, (target["workspace_id"], 50001), "pm_wip_operations_workspace"),
        (pm_wip_count.HISTORY_SQL, (target["work_item_id"], 50001), "api_events_resource"),
        (pm_wip_count.MANAGEMENT_SQL, (target["work_item_id"], 50001), "pm_wip_management_work"),
        (pm_wip_count.LINKS_SQL, (target["work_item_id"], 50001), "work_item_links_active"),
    ):
        plan = [row[3] for row in ops.db.execute("EXPLAIN QUERY PLAN " + sql, args)]
        print(index, plan)
        assert any("SEARCH" in step and index in step for step in plan)
        assert not any("SCAN " in step for step in plan)


def measured(ops, target, *, callback=True):
    ticks = 0
    def progress():
        nonlocal ticks
        ticks += 1
        return 0
    if callback:
        ops.db.set_progress_handler(progress, 1)
    start = time.perf_counter()
    try:
        value = read(ops, target)
    finally:
        elapsed = time.perf_counter() - start
        ops.db.set_progress_handler(None, 0)
    return value, ticks if callback else None, elapsed


async def test_unrelated_workspace_history_does_not_increase_work(ops):
    target = await prepared(ops)
    install(ops)
    await update(ops, target, state="doing")
    base, before, _ = measured(ops, target)
    template = dict(ops.db.execute("SELECT * FROM operations LIMIT 1").fetchone())
    with ops.journal.tx():
        ops.db.executemany("INSERT INTO api_events(resource_type,resource_id,kind,body,created_at) VALUES(?,?,?,?,?)",
            [("work_item", "wi_unrelated", "work_item.updated", "{}", 1)] * 20_000)
        for n in range(2000):
            row = {**template, "operation_id": f"op_unrelated_{n}", "idem_key": f"unrelated-{n}",
                   "action": "pm.work_batch.start", "target": json.dumps({"workspace_id": "ws_" + "f" * 32})}
            ops.db.execute("INSERT INTO operations(" + ",".join(row) + ") VALUES(" + ",".join("?" * len(row)) + ")", tuple(row.values()))
    value, after, _ = measured(ops, target)
    assert value["used"] == base["used"] == 1
    print(f"instruction baseline={before} unrelated={after} ratio={after / before:.4f}")
    assert after <= 1.2 * before


def clone_row(db, table, template, **updates):
    row = {**template, **updates}
    db.execute('INSERT INTO "' + table + '" (' + ','.join(row) + ') VALUES(' + ','.join('?' * len(row)) + ')', tuple(row.values()))


async def test_cost_for_thousand_works_and_five_thousand_source_records(ops):
    target = await prepared(ops)
    install(ops)
    wi = target["work_item_id"]
    work = dict(ops.db.execute("SELECT * FROM work_items WHERE work_item_id=?", (wi,)).fetchone())
    binding = dict(ops.db.execute("SELECT * FROM pm_work_bindings WHERE work_item_id=?", (wi,)).fetchone())
    event = dict(ops.db.execute("SELECT * FROM api_events WHERE resource_type='work_item' AND resource_id=?", (wi,)).fetchone())
    create = dict(ops.db.execute("SELECT * FROM operations WHERE operation_id=?", (work["operation_id"],)).fetchone())
    bind = dict(ops.db.execute("SELECT * FROM operations WHERE operation_id=?", (binding["operation_id"],)).fetchone())
    receipt = dict(ops.db.execute("SELECT * FROM management_applied WHERE operation_id=?", (work["operation_id"],)).fetchone())
    with ops.journal.tx():
        for i in range(999):
            new_wi, cid, bid = f"wi_{i:020x}", f"op_{i:032x}", f"op_{i + 10000:032x}"
            clone_row(ops.db, "work_items", work, work_item_id=new_wi, operation_id=cid)
            clone_row(ops.db, "operations", create, operation_id=cid, idem_key=f"cost-create-{i}",
                      result=create["result"].replace(wi, new_wi))
            clone_row(ops.db, "operations", bind, operation_id=bid, idem_key=f"cost-bind-{i}",
                      target=bind["target"].replace(wi, new_wi), result=bind["result"].replace(wi, new_wi))
            clone_row(ops.db, "management_applied", receipt, operation_id=cid,
                      result=receipt["result"].replace(wi, new_wi))
            clone_row(ops.db, "api_events", event, seq=None, resource_id=new_wi,
                      body=event["body"].replace(work["operation_id"], cid))
            clone_row(ops.db, "pm_work_bindings", binding, sequence=None, binding_id=f"pmb_{i:032x}",
                      work_item_id=new_wi, operation_id=bid)
    value, instructions, elapsed = measured(ops, target)
    assert value["used"] == 0 and value["coverage"]["status"] == "complete", value
    print(f"cost Works=1000 source_records=5000 callback=yes instructions={instructions} seconds={elapsed:.6f}")
    plain, instructions, elapsed = measured(ops, target, callback=False)
    assert plain["used"] == value["used"] and plain["coverage"] == value["coverage"]
    print(f"cost Works=1000 source_records=5000 callback=no instructions=unmeasured seconds={elapsed:.6f}")


async def test_removed_link_activity_is_outside_scope_and_active_sources_deduplicate(ops):
    from tests.test_pm_evidence import link, session
    from tests.test_pm_kanban import commands
    target = await prepared(ops)
    install(ops)
    source, _ = commands(ops)
    links = [('task', source['task_id']), ('session', session(ops))]
    for kind, ref in links:
        await link(ops, target, kind, ref)
    value = read(ops, target)
    assert value['used'] == value['breakdown']['unresolved_execution'] == 1
    for kind, ref in links:
        await link(ops, target, kind, ref, remove=True)
    value = read(ops, target)
    assert value['used'] == 0 and 'removed_link_activity' in value['coverage']['outside_scope']


async def test_session_visibility_index_searches_existing_predicate(ops):
    from bat_agent_connector import pm_evidence, pm_runs
    from tests.test_pm_evidence import link, session
    target = await prepared(ops)
    install(ops)
    pm_runs.schema(ops.journal)
    ref = session(ops)
    await link(ops, target, 'session', ref)
    statements = []
    ops.db.set_trace_callback(statements.append)
    try:
        assert pm_evidence._session_visible(ops, OWNER, target['workspace_id'], ref)
    finally:
        ops.db.set_trace_callback(None)
    sql = next(sql for sql in statements if 'FROM pm_runs r JOIN pm_run_state s' in sql)
    plan = [row[3] for row in ops.db.execute('EXPLAIN QUERY PLAN ' + sql)]
    print('session visibility', plan)
    assert any('SEARCH s USING INDEX pm_run_state_session' in row for row in plan)
    assert not any('SCAN' in row for row in plan)
