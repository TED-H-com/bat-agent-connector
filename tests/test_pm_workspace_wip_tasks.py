"""Terminal task state alone cannot settle a linked send."""
from tests.test_pm_discussions import workspace
from tests.test_pm_evidence import bind_project, bind_work, item, link, project, task
from tests.test_pm_evidence import ops as ops
from tests.test_pm_kanban import decide, update
from tests.test_pm_workspace_wip import install, read


def measured(ops, target, case):
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
    print('TASK SEND READ', case, ticks, value['used'])
    for sql in sorted(set(statements)):
        if sql.startswith('SELECT') and ('FROM events ' in sql or 'FROM command_reconciliations ' in sql):
            plans = [row[3] for row in ops.db.execute('EXPLAIN QUERY PLAN ' + sql)]
            print('TASK PLAN', sql, plans)
            assert all('SCAN ' not in row and 'TEMP B-TREE' not in row for row in plans)
    return value


async def test_terminal_task_releases_only_proven_unsent_commands(ops):
    for case in ('cancelled', 'accepted', 'uncertain', 'cancelled_after_ack', 'not_delivered', 'delivered'):
        ws = await workspace(ops)
        pid = await project(ops, 'Synthetic task case ' + case)
        wi = await item(ops, pid)
        await bind_project(ops, ws['workspace_id'], pid)
        target = {'workspace_id': ws['workspace_id'], 'work_item_id': wi}
        await bind_work(ops, target)
        install(ops)
        source = task(ops)
        tid = source['task_id']
        ops.journal.change(tid, 'dispatching', fields={'session_id': 'fixture-send'})
        command, _ = ops.journal.command(tid, 'send', 'fixture-send', {'prompt': 'Synthetic task input'}, 'wip-input-' + case)
        cid = command['command_id']
        if case in {'not_delivered', 'delivered'}:
            ops.journal.command_status(cid, 'uncertain')
            ops.journal.change(tid, 'uncertain')
            token = ops.journal.issue_reconcile_capability(tid, cid)
            ops.journal.resolve_send(tid, cid, token=token, outcome=case, actor='operator',
                                     source='synthetic-review', evidence='Reviewed original dispatch evidence')
        elif case == 'cancelled_after_ack':
            # Contradictory later cancellation cannot erase an original ACK, even without a marker.
            ops.journal.command_status(cid, 'accepted')
            ops.journal.command_status(cid, 'cancelled')
        else:
            ops.journal.command_status(cid, case)
        ops.journal.change(tid, 'failed')
        await link(ops, target, 'task', tid)
        await update(ops, target, state='done')
        await decide(ops, target)
        value = measured(ops, target, case)
        held = int(case not in {'cancelled', 'not_delivered'})
        assert value['used'] == value['breakdown']['unresolved_execution'] == held
