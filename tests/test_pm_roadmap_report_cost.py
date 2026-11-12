"""Reproducible SQLite VM/elapsed costs over synthetic appended report ledgers."""
import copy
import json
import os
import time
from pathlib import Path

from bat_agent_connector import pm_access, pm_plans, pm_runs
from bat_agent_connector import pm_roadmap_report_materialization as reports
from tests.test_pm_plan_operations import OWNER
from tests.test_pm_roadmap_report_materialization import (  # noqa: F401
    fixed as fixed_fixture,
)
from tests.test_pm_roadmap_report_materialization import (
    materialize,
)
from tests.test_pm_roadmap_report_materialization import pm as pm_fixture
from tests.test_pm_roadmap_report_materialization import ready as ready_fixture
from tests.test_pm_roadmap_report_materialization import spec_fixed as spec_fixed_fixture
from tests.test_pm_roadmap_report_materialization import world as world_fixture
from tests.test_pm_roadmap_report_materialization_visibility import withdraw

fixed = fixed_fixture
pm = pm_fixture
ready = ready_fixture
spec_fixed = spec_fixed_fixture
world = world_fixture

def insert(db, table, value):
    db.execute('INSERT INTO ' + table + '(' + ','.join(value) + ') VALUES(' + ','.join('?' for _ in value) + ')', tuple(value.values()))


def clones(w, op, start, stop):
    db, wid = w['d'].ops.db, w['workspace_id']
    operation = dict(db.execute('SELECT * FROM operations WHERE operation_id=?', (op['operation_id'],)).fetchone())
    run = dict(db.execute('SELECT * FROM pm_runs WHERE operation_id=?', (op['operation_id'],)).fetchone())
    state = dict(db.execute('SELECT * FROM pm_run_state WHERE run_id=?', (run['run_id'],)).fetchone())
    record = dict(db.execute('SELECT * FROM pm_roadmap_report_materializations WHERE operation_id=?', (op['operation_id'],)).fetchone())
    step = dict(db.execute("SELECT * FROM operation_steps WHERE operation_id=? AND name='pm.revision.commit'", (op['operation_id'],)).fetchone())
    operation_events = [dict(row) for row in db.execute("SELECT * FROM api_events WHERE resource_type='operation' AND resource_id=?", (op['operation_id'],))]
    frozen_step = dict(db.execute("SELECT * FROM operation_steps WHERE operation_id=? AND name='pm.roadmap.report.freeze'", (op['operation_id'],)).fetchone())
    parent = pm_plans.latest_git_receipt(db, wid)['commit_sha']
    with w['d'].ops.journal.tx():
        for index in range(start, stop):
            oid, rid, mid = 'op_' + f'{index:032x}', 'pmr_' + f'{index:032x}', 'pmrpt_' + f'{index:032x}'
            replacements = [(op['operation_id'], oid), (run['run_id'], rid), (record['materialization_id'], mid)]
            def replace(value):
                value = copy.deepcopy(value)
                for key, text in value.items():
                    if isinstance(text, str):
                        for old, new in replacements:
                            text = text.replace(old, new)
                        value[key] = text
                return value
            o, r, s, rec, st = map(replace, (operation, run, state, record, step))
            o['idem_key'] = 'cost-' + oid
            r['generation'] += index
            rec.pop('sequence')
            receipt = json.loads(rec['git_receipt'])
            receipt['commit_sha'] = f'{index:040x}'
            rec['git_receipt'] = json.dumps(receipt)
            st['response'] = json.dumps(receipt)
            intent = json.loads(st['request'])
            intent['expected_parent'] = parent
            st['request'] = json.dumps(intent)
            parent = receipt['commit_sha']
            result = reports._result({**rec, 'git_receipt': receipt})
            o['result'], s['outcome'] = json.dumps(result), json.dumps(result)
            for table, value in [('operations', o), ('pm_runs', r), ('pm_run_state', s),
                                  ('operation_steps', st), ('operation_steps', replace(frozen_step)), ('pm_roadmap_report_materializations', rec)]:
                insert(db, table, value)
            db.execute('INSERT INTO pm_source_lineage VALUES(?,?,?,?)', ('operation', oid, oid, oid))
            for event in operation_events:
                event = replace(event)
                w['d'].ops.journal.api_event('operation', oid, event['kind'], json.loads(event['body']), actor=OWNER.actor)
            w['d'].ops.journal.api_event('pm_workspace', wid, 'pm.roadmap.report.materialized', result, actor=OWNER.actor)


def measure(db, fn):
    count = [0]
    def tick():
        count[0] += 1
        return 0
    db.set_progress_handler(tick, 1)
    start = time.perf_counter()
    try:
        result = fn()
    finally:
        elapsed = time.perf_counter() - start
        db.set_progress_handler(None, 0)
    return result, {'vm_instructions': count[0], 'seconds': round(elapsed, 6)}


def readers(ops, wid):
    report_ids = {row[0] for row in ops.db.execute('SELECT operation_id FROM operations WHERE action=?', (reports.ACTION,))}
    def operations():
        cursor, count, pages = {}, 0, 0
        while True:
            page = ops.list(principal=OWNER, action=reports.ACTION, limit=200, **cursor)
            count += len(page['operations'])
            pages += 1
            if page['next_before'] is None:
                return count, pages
            cursor = {'before': page['next_before'], 'before_id': page['next_before_id']}
    def events():
        cursor, seen, pages = 0, set(), 0
        while True:
            page = pm_access.filter_events(ops.db, OWNER, ops.journal.api_events(cursor, 500))
            for event in page['events']:
                identity = event['resource_id'] if event['resource_type'] == 'operation' else event['body'].get('operation_id')
                if identity in report_ids:
                    seen.add(identity)
            pages += 1
            if not page['has_more']:
                return len(seen), pages
            cursor = page['next_cursor']
    def runs():
        cursor, count, pages = None, 0, 0
        while True:
            page = pm_runs.list_runs(ops, OWNER, wid, after=cursor)
            count += len([row for row in page['runs'] if row['purpose'].startswith('Materialize roadmap report ')])
            pages += 1
            cursor = page['next_cursor']
            if cursor is None:
                return count, pages
    def receipts():
        cursor, count, pages = 0, 0, 0
        while True:
            page = reports.list_materializations(ops, OWNER, wid, after=cursor, limit=50)
            count += len(page['materializations'])
            pages += 1
            cursor = page['next_after']
            if cursor is None:
                return count, pages
    return {'operations': operations, 'events': events, 'runs': runs, 'receipts': receipts}


async def test_visibility_and_parent_costs_at_required_sizes(fixed, monkeypatch):
    w, ops = fixed, fixed['d'].ops
    db, wid = ops.db, w['workspace_id']
    original = reports.pm_roadmap_report_sources.load_fixed
    calls = [0]
    def counted(*args, **kwargs):
        calls[0] += 1
        return original(*args, **kwargs)
    monkeypatch.setattr(reports.pm_roadmap_report_sources, 'load_fixed', counted)
    authority = db.execute('SELECT controller_authority FROM tasks WHERE task_id=?', (w['source_task']['task_id'],)).fetchone()[0]
    rows, parents = [], []
    op = None
    for size in (0, 1, 100, 500):
        if size == 1:
            op = await materialize(w)
        elif size == 100:
            clones(w, op, 1, 100)
        elif size == 500:
            clones(w, op, 100, 500)
        _, cost = measure(db, lambda: pm_plans.latest_git_receipt(db, wid))
        parents.append({'report_receipts': size, **cost})
        if size == 1:
            continue
        for hidden in (False, True):
            if hidden:
                withdraw(w)
            for name, read in readers(ops, wid).items():
                calls[0] = 0
                (count, pages), cost = measure(db, read)
                rows.append({'report_operations': size, 'withdrawn_source': hidden, 'read': name,
                             'returned': count, 'pages': pages, 'closure_calls': calls[0], **cost})
                assert count == (0 if hidden else size), rows[-1]
                assert calls[0] <= pages, rows[-1]
            db.execute('UPDATE tasks SET controller_authority=? WHERE task_id=?', (authority, w['source_task']['task_id']))
    evidence = os.environ.get('BAC_EVIDENCE')
    if evidence:
        suffix = __import__('sys').version_info.minor
        Path(evidence, 'b2', f'cost-python3.{suffix}.json').write_text(json.dumps({'visibility': rows, 'latest_git_receipt': parents}, indent=2) + '\n')
