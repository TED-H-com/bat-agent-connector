"""Whole-read cost and query plans include every original contributing source."""
from __future__ import annotations

import json
import re
import time

from bat_agent_connector import pm_execution_runtime as runtime
from bat_agent_connector import pm_runs, work_items
from bat_agent_connector.api_auth import Principal
from tests import test_pm_execution_runtime as executions
from tests import test_pm_work_batches as batches
from tests.operation_helpers import settle_operations
from tests.test_pm_discussions import prepared as discussion
from tests.test_pm_evidence import session, work_request
from tests.test_pm_kanban import commands
from tests.test_pm_runs import create as create_run
from tests.test_pm_workspace_wip import clone_row, install, read

world = executions.world
daemon = executions.daemon
bat_world = executions.bat_world
joined = executions.joined
uncompleted = executions.uncompleted


def measured(db, call, *, callback=True, trace=None):
    ticks = 0
    def progress():
        nonlocal ticks
        ticks += 1
        return 0
    if callback:
        db.set_progress_handler(progress, 1)
    if trace is not None:
        db.set_trace_callback(trace.append)
    start = time.perf_counter()
    try:
        value = call()
    finally:
        elapsed = time.perf_counter() - start
        db.set_progress_handler(None, 0)
        db.set_trace_callback(None)
    return value, ticks if callback else None, elapsed


def assert_plans(db, statements):
    protected = {'operations', 'api_events', 'pm_run_state', 'work_item_links', 'management_applied', 'commands'}
    searched = set()
    for sql in sorted(set(statements)):
        if not sql.lstrip().upper().startswith(('SELECT', 'WITH')):
            assert sql.strip().split()[0].upper() in {'BEGIN', 'COMMIT', 'ROLLBACK', 'SAVEPOINT', 'RELEASE'}, sql
            continue
        plan = [row[3] for row in db.execute('EXPLAIN QUERY PLAN ' + sql)]
        print('PLAN', sql.replace('\n', ' '), plan)
        aliases = set(protected)
        for table in protected:
            for match in re.finditer(r'\b(?:FROM|JOIN)\s+' + table + r'\s+(?:AS\s+)?(\w+)', sql, re.I):
                aliases.add(match[1])
        for step in plan:
            if any(re.search(r'\bSCAN\s+' + re.escape(name) + r'\b', step, re.I) for name in aliases):
                raise AssertionError((sql, plan))
            if 'SEARCH' in step:
                searched.add(step)
    assert any('pm_run_state_session' in step for step in searched)
    assert any('pm_wip_operations_workspace' in step for step in searched)
    assert any('pm_wip_management_work' in step for step in searched)
    assert any('work_item_links_active' in step for step in searched)
    assert any('pm_wip_commands_task' in step for step in searched)


async def test_whole_read_and_observer_cost_independent_of_every_unrelated_source(uncompleted):
    w, ops = uncompleted, uncompleted['d'].ops
    install(ops)
    child = w['original_children'][0]
    from bat_agent_connector import pm_package_handoff
    wi = pm_package_handoff.binding(child)['contract']['package']['work_item_id']
    w = {**w, 'target': {'workspace_id': w['workspace_id'], 'work_item_id': wi}}
    assert child['status'] == 'succeeded'
    assert runtime.observer(ops, child) is not None
    # Close management so the read also evaluates the original runtime observer.
    wi = w['target']['work_item_id']
    item = work_items._get_item(ops.db, wi)
    await batches.action(w, 'work_item.update', {'work_item_id': wi}, {'state': 'done'}, {'expected_version': item['version']})
    item = work_items._get_item(ops.db, wi)
    await batches.action(w, 'work_item.approve', {'work_item_id': wi}, {'note': 'Reviewed synthetic scope'},
        {'expected_fingerprint': work_items.fingerprint(item)}, who=Principal(batches.OWNER.actor, batches.OWNER.scopes | {'approve'}))
    linked_work = await batches.action(w, 'work_item.create', {'project_id': item['project_id']}, {'title': 'Legacy explicit activity'})
    linked_target = {'workspace_id': w['workspace_id'], 'work_item_id': linked_work['work_item_id']}
    ops.create(batches.OWNER, **work_request(ops, linked_target))
    await settle_operations(ops)
    source, sent = commands(ops)
    session(ops)
    ops.db.execute("UPDATE sessions_observed SET host='h1' WHERE host='mock-host' AND session_id='mock-session'")
    for kind, ref in [('task', source['task_id']), ('session', 'h1/mock-session')]:
        await batches.action(w, 'work_item.link', {'work_item_id': linked_target['work_item_id']}, {'kind': kind, 'ref': ref})
    # A real second PM workspace/thread supplies FK-valid unrelated run rows.
    other = await discussion(ops)
    pm_runs.schema(ops.journal)
    run = create_run(ops, other)
    db = ops.db
    def row(table, where='', args=()):
        return dict(db.execute('SELECT * FROM ' + table + ' ' + where, args).fetchone())
    run_row = row('pm_runs', 'WHERE run_id=?', (run['run_id'],))
    state = row('pm_run_state', 'WHERE run_id=?', (run['run_id'],))
    operation = row('operations', 'WHERE operation_id=?', (runtime.observer(ops, child)['operation_id'],))
    task = row('tasks', 'WHERE task_id=?', (source['task_id'],))
    command = row('commands', 'WHERE command_id=?', (sent[0]['command_id'],))
    observed = row('sessions_observed', "WHERE host='h1' AND session_id='mock-session'")
    linked = row('work_item_links', 'WHERE work_item_id=?', (linked_target['work_item_id'],))
    work = row('work_items', 'WHERE work_item_id=?', (wi,))
    management = row('management_applied', 'WHERE operation_id=?', (work['operation_id'],))
    clone_row(db, 'work_items', work, work_item_id='wi_unrelated', operation_id='op_unrelated_work')
    binding = row('pm_work_bindings', 'WHERE workspace_id=? AND work_item_id=?',
                  (w['workspace_id'], linked_target['work_item_id']))
    clone_row(db, 'pm_work_bindings', binding, sequence=None, binding_id='pmb_unrelated',
              workspace_id=other['workspace_id'], work_item_id='wi_unrelated', operation_id='op_unrelated_work')
    def read_call():
        return read(ops, w['target'], batches.OWNER)
    statements = []
    baseline, before, _ = measured(db, read_call, trace=statements)
    assert baseline['used'] >= 1 and baseline['coverage']['status'] == 'complete', baseline
    _, observer_before, _ = measured(db, lambda: runtime.observer(ops, child))
    with ops.journal.tx():
        db.executemany('INSERT INTO api_events(resource_type,resource_id,kind,body,created_at) VALUES(?,?,?,?,?)',
            [('work_item', 'wi_unrelated', 'work_item.updated', '{}', 1)] * 20_000)
        for n in range(2000):
            rid, oid, tid, sid = f'run_unrelated_{n}', f'op_unrelated_{n}', f'task_unrelated_{n}', f'session_unrelated_{n}'
            clone_row(db, 'operations', operation, operation_id=oid, idem_key=oid,
                action=runtime.ACTION if n % 2 else 'pm.work_batch.start',
                target=json.dumps({'workspace_id': other['workspace_id'],
                    **({'execution_operation_id': f'execution_unrelated_{n}'} if n % 2 else {})}))
            clone_row(db, 'management_applied', management, operation_id=oid,
                result=json.dumps({'work_item_id': 'wi_unrelated', 'changed': True}))
            clone_row(db, 'pm_runs', run_row, run_id=rid, operation_id=oid, generation=n + 2)
            clone_row(db, 'pm_run_state', state, run_id=rid, session_binding=json.dumps({'host': 'h1', 'session_id': sid}))
            clone_row(db, 'sessions_observed', observed, session_id=sid)
            clone_row(db, 'tasks', task, task_id=tid, idem_key=tid, workspace=other['workspace_id'], host='h1', session_id=sid)
            clone_row(db, 'commands', command, command_id=f'command_unrelated_{n}', task_id=tid, idem_key=tid, session_id=sid)
            clone_row(db, 'work_item_links', linked, link_id=None, work_item_id='wi_unrelated', ref=tid)
    actual, after, _ = measured(db, read_call, trace=statements)
    _, observer_after, _ = measured(db, lambda: runtime.observer(ops, child))
    assert actual['used'] == baseline['used'] and actual['coverage'] == baseline['coverage']
    print(f'whole read baseline={before} unrelated={after} ratio={after / before:.6f}')
    print(f'observer baseline={observer_before} unrelated={observer_after} ratio={observer_after / observer_before:.6f}')
    assert_plans(db, statements)
    assert after <= before * 1.2
    assert observer_after <= observer_before * 1.2
