"""Same-workspace history work must stop before filtering or sorting beyond limits."""
from bat_agent_connector import pm_wip_count
from tests.test_pm_evidence import link, prepared, session, task
from tests.test_pm_evidence import ops as ops
from tests.test_pm_kanban import update
from tests.test_pm_workspace_wip import clone_row, install, read


def measure(ops, target):
    ticks = 0
    statements = []
    def progress():
        nonlocal ticks
        ticks += 1
    ops.db.set_progress_handler(progress, 1)
    ops.db.set_trace_callback(statements.append)
    try:
        value = read(ops, target)
    finally:
        ops.db.set_progress_handler(None, 0)
        ops.db.set_trace_callback(None)
    for sql in sorted(set(statements)):
        if sql.lstrip().upper().startswith('SELECT') and ('pm_work_bindings' in sql or 'work_item_links' in sql):
            plan = [row[3] for row in ops.db.execute('EXPLAIN QUERY PLAN ' + sql)]
            print('WIP PLAN', sql, plan)
    return ticks, value


async def test_current_binding_seek_skips_obsolete_history(ops):
    target = await prepared(ops)
    install(ops)
    before, original = measure(ops, target)
    binding = dict(ops.db.execute('SELECT * FROM pm_work_bindings').fetchone())
    with ops.journal.tx():
        for n in range(50001):
            clone_row(ops.db, 'pm_work_bindings', binding, sequence=None,
                      binding_id=f'pmb_obsolete_{n}', operation_id=f'op_obsolete_{n}')
        # A current valid original; history rows are query-cost data only.
        operation = dict(ops.db.execute('SELECT * FROM operations WHERE operation_id=?', (binding['operation_id'],)).fetchone())
        clone_row(ops.db, 'operations', operation, operation_id='op_current_binding', idem_key='current-binding')
        clone_row(ops.db, 'pm_work_bindings', binding, sequence=None,
                  binding_id='pmb_current', operation_id='op_current_binding')
    after, value = measure(ops, target)
    print('BINDING INSTRUCTIONS', before, after, after / before)
    assert value['coverage']['status'] == 'complete'
    assert (value['used'], value['breakdown']) == (original['used'], original['breakdown'])
    assert after <= 1.2 * before


async def test_active_link_limit_stops_index_range_without_sort(ops):
    target = await prepared(ops)
    install(ops)
    await link(ops, target, 'session', session(ops))
    row = dict(ops.db.execute('SELECT * FROM work_item_links').fetchone())
    counts = []
    for start in (0, 50000):
        with ops.journal.tx():
            for n in range(start, start + 50000):
                clone_row(ops.db, 'work_item_links', row, link_id=None, ref=f'mock-host/overflow-{n}')
        ticks, value = measure(ops, target)
        counts.append(ticks)
        assert value['coverage']['reasons'] == ['coverage_unavailable']
        assert value['used'] is None and 'breakdown' not in value
    plan = [row[3] for row in ops.db.execute('EXPLAIN QUERY PLAN ' + pm_wip_count.LINKS_SQL,
                                            (target['work_item_id'], 50001))]
    print('LINK INSTRUCTIONS', counts, counts[1] / counts[0], plan)
    assert counts[1] <= 1.2 * counts[0]
    assert all('USE TEMP B-TREE' not in entry for entry in plan)


async def test_link_order_preserves_complete_union(ops, monkeypatch):
    target = await prepared(ops)
    install(ops)
    await update(ops, target, state='doing')
    await link(ops, target, 'session', session(ops))
    await link(ops, target, 'task', task(ops)['task_id'])
    before = read(ops, target)
    monkeypatch.setattr(pm_wip_count, 'LINKS_SQL',
        'SELECT * FROM work_item_links WHERE work_item_id=? AND removed_at IS NULL ORDER BY kind DESC,ref DESC LIMIT ?')
    after = read(ops, target)
    assert before['coverage'] == after['coverage']
    assert before['used'] == after['used'] == 1
    assert before['breakdown'] == after['breakdown']


async def test_per_work_history_bound_is_advertised_and_stops_materialization(ops):
    from bat_agent_connector import pm_workspace_wip
    target = await prepared(ops)
    install(ops)
    row = dict(ops.db.execute("SELECT * FROM api_events WHERE resource_type='work_item' AND resource_id=?",
                              (target['work_item_id'],)).fetchone())
    with ops.journal.tx():
        for _ in range(500):
            clone_row(ops.db, 'api_events', row, seq=None)
    sql = []
    ops.db.set_trace_callback(sql.append)
    try:
        value = read(ops, target)
    finally:
        ops.db.set_trace_callback(None)
    assert value['coverage']['reasons'] == ['coverage_unavailable']
    queries = [query for query in sql if "SELECT * FROM api_events WHERE resource_type='work_item'" in query]
    assert len(queries) == 1 and queries[0].endswith('LIMIT 501')
    assert pm_workspace_wip.capabilities(ops)['bounds']['per_work_history'] == 500
