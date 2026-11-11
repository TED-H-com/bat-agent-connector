"""Index plans and VM work for impact discovery in a real disk database."""
import json

import pytest

from tests.test_pm_plan_impact import mapped as mapped
from tests.test_pm_plan_impact import pm as pm
from tests.test_pm_plan_impact import read, selector
from tests.test_pm_plan_impact import ready as ready
from tests.test_pm_plan_impact import selected as selected
from tests.test_pm_plan_impact import start_ready as start_ready
from tests.test_pm_plan_impact import world as world


def measure(w, params):
    db = w['d'].ops.db
    ticks, statements = [0], []
    def progress():
        ticks[0] += 1
        return 0
    db.set_progress_handler(progress, 100)
    db.set_trace_callback(statements.append)
    try:
        value = read(w, params)
    finally:
        db.set_progress_handler(None, 0)
        db.set_trace_callback(None)
    discovery = [q for q in statements if '/* pm_impact:' in q or 'original_operation_id FROM api_events' in q]
    plans = [(q, [tuple(r) for r in db.execute('EXPLAIN QUERY PLAN ' + q)]) for q in discovery]
    print('DISCOVERY_PLANS', json.dumps(plans))
    return value, ticks[0] * 100, plans


def noise(w, start):
    ops, db = w['d'].ops, w['d'].ops.db
    original = dict(db.execute('SELECT * FROM operations WHERE operation_id=?', (w['package']['operation_id'],)).fetchone())
    keys = list(original)
    with ops.journal.tx():
        for i in range(2000):
            target = {'workspace_id': w['workspace_id'] if i % 2 else 'pmw_' + 'f' * 32,
                      'work_item_id': 'wi_' + 'f' * 32}
            value = original | {'operation_id': 'op_' + f'{100000+i:032x}', 'idem_key': f'impact-noise-{i}',
                                'target': json.dumps(target), 'next_run_at': 9999999999}
            db.execute(f"INSERT INTO operations({','.join(keys)}) VALUES({','.join('?' for _ in keys)})", [value[k] for k in keys])
        rows = []
        for i in range(20000):
            wid = w['workspace_id'] if i % 3 == 0 else 'pmw_' + 'f' * 32
            kind = ('pm.work_package.created', 'operation.accepted', 'unrelated.event')[i % 3]
            body = {'workspace_id': wid, 'work_item_id': 'wi_' + 'f' * 32,
                    'action': 'pm.work_package.create', 'target': {'workspace_id': wid, 'work_item_id': 'wi_' + 'f' * 32}}
            rows.append((start+i, 'operation', 'op_' + f'{100000+i%2000:032x}', kind, json.dumps(body), 0))
        db.executemany('INSERT INTO api_events(seq,resource_type,resource_id,kind,body,created_at) VALUES(?,?,?,?,?,?)', rows)


async def test_impact_preview_work_does_not_grow_with_unrelated_history(selected):
    w, ops = selected, selected['d'].ops
    start = ops.journal.api_head() + 1
    # Reserve a cursor above the later synthetic inserts, preserving the selector.
    ops.db.execute("INSERT INTO api_events(seq,resource_type,resource_id,kind,body,created_at) VALUES(?, 'fixture', 'sentinel', 'fixture.cursor', '{}', 0)", (start + 30000,))
    params = await selector(w)
    before, small, _ = measure(w, params)
    noise(w, start)
    after, large, plans = measure(w, params)
    print('WHOLE_PREVIEW_VM_STEPS', small, large)
    assert after == before
    assert large <= small * 1.2
    assert plans
    for _, plan in plans:
        detail = ' '.join(row[3] for row in plan)
        assert 'SCAN api_events' not in detail and 'SCAN operations' not in detail
        assert 'INTEGER PRIMARY KEY (rowid<' not in detail
        assert 'INDEX' in detail


@pytest.mark.parametrize('count', [1001, 1100])
async def test_impact_post_cursor_related_admissions_never_consume_discovery_budget(selected, count):
    from tests.test_pm_plan_impact_limits import clone_admissions
    w = selected
    params = w['impact_selector']
    before = read(w, params)
    clone_admissions(w['d'].ops, w['package']['operation_id'], count)
    assert read(w, params) == before


async def test_impact_large_comparison_stops_construction_before_subjects(selected, monkeypatch):
    from bat_agent_connector import pm_plan_impact as impact
    from bat_agent_connector.operations import OperationError
    from tests.test_pm_plan_operations import OWNER, draft
    from tests.test_pm_snapshots import snapshot
    w, ops = selected, selected['d'].ops
    later = await draft(w, requirements=w['requirements'], markdown='範' * 100000)
    snap = await snapshot(ops, w['workspace_id'], who=OWNER)
    params = w['impact_selector'] | {'to_revision_id': later['revision_id'], 'snapshot_id': snap['snapshot_id']}
    def forbidden(*args, **kwargs):
        pytest.fail('Oversized fixed comparison must stop construction before any subject')
    monkeypatch.setattr(impact, '_subject', forbidden)
    with pytest.raises(OperationError, match='PM_IMPACT_TOO_LARGE'):
        read(w, params)


def all_plans(db, w, cursor):
    from bat_agent_connector import pm_impact_discovery as discovery
    wid, work = w['workspace_id'], w['target']['work_item_id']
    statements = [(discovery.EVENTS, (wid, work, cursor)),
                  (discovery.EVENTS, (wid, 'pmim_' + '0' * 32, cursor)),
                  (discovery.MAPPINGS, (wid, work)),
                  (discovery.PACKAGES, (wid, work)),
                  (discovery.HANDOFFS, (wid, w['package']['package_id'])),
                  (discovery.ADMISSIONS, (w['package']['operation_id'], cursor))]
    return [(query, args, [tuple(r) for r in db.execute('EXPLAIN QUERY PLAN ' + query, args)]) for query, args in statements]


async def test_impact_discovery_indexes_upgrade_idempotently_with_exact_plans(selected, tmp_path, monkeypatch):
    import sqlite3
    import time

    from bat_agent_connector import pm_impact_discovery as discovery
    from bat_agent_connector import pm_issue_mappings
    from bat_agent_connector.task_journal import Journal
    w, ops = selected, selected['d'].ops
    params = w['impact_selector']
    expected = read(w, params)
    path = tmp_path / 'impact-upgrade.sqlite3'
    db = sqlite3.connect(path, isolation_level=None)
    db.row_factory = sqlite3.Row
    ops.db.backup(db)
    db.close()
    for reopening in range(2):
        journal = Journal(path)
        try:
            with monkeypatch.context() as patch:
                patch.setattr(ops, 'db', journal.db)
                patch.setattr(ops.journal, 'db', journal.db)
                db = journal.db
                version = db.execute('PRAGMA user_version').fetchone()[0]
                if reopening == 0:
                    for name in discovery.INDEXES:
                        db.execute(f'DROP INDEX IF EXISTS {name}')
                    noise(w, ops.journal.api_head() + 1)
                    print('INDEX_PLANS_BEFORE', json.dumps(all_plans(db, w, ops.journal.api_head())))
                    for name, statement in discovery.INDEXES.items():
                        page_size = db.execute('PRAGMA page_size').fetchone()[0]
                        def used_pages():
                            return db.execute('PRAGMA page_count').fetchone()[0] - db.execute('PRAGMA freelist_count').fetchone()[0]
                        before = used_pages()
                        start = time.perf_counter()
                        db.execute(statement)
                        print('INDEX_BUILD', name, 'seconds', time.perf_counter() - start,
                              'bytes', (used_pages() - before) * page_size)
                    # Exercise the actual startup path, not only the index helper.
                    for name in discovery.INDEXES:
                        db.execute(f'DROP INDEX {name}')
                pm_issue_mappings.schema(journal)
                plans = all_plans(db, w, ops.journal.api_head())
                print('INDEX_PLANS_AFTER', reopening, json.dumps(plans))
                expected_indexes = ['pm_impact_event_relation', 'pm_impact_event_relation', 'pm_impact_issue_work',
                                    'pm_package_work', 'pm_handoff_package', 'api_events_resource']
                for (_, _, plan), index in zip(plans, expected_indexes, strict=True):
                    text = ' '.join(row[3] for row in plan)
                    assert index in text, text
                    assert not any(term in text for term in ('SCAN api_events', 'SCAN operations', 'INTEGER PRIMARY KEY (rowid<'))
                assert db.execute('PRAGMA user_version').fetchone()[0] == version
                assert read(w, params) == expected
        finally:
            journal.close()


async def test_impact_direct_handoff_locator_keeps_post_cursor_history_out(selected):
    from tests import test_pm_package_handoff as handoff
    from tests import test_pm_work_start as starting
    from tests.test_pm_plan_impact_limits import clone_admissions
    from tests.test_pm_plan_operations import OWNER
    w, ops = selected, selected['d'].ops
    handoff.dispatch_runner(w)
    op, _ = ops.create(OWNER, **await handoff.request(w))
    starting._park_until_executed(ops, op['operation_id'])
    params = await selector(w)
    before = read(w, params)
    execution = next(s for s in before['matrix']['subjects'] if s['kind'] == 'execution')
    assert execution['operation_id'] == op['operation_id'] and execution['boundary'] == 'proven'
    clone_admissions(ops, op['operation_id'], 1001)
    assert read(w, params) == before


async def test_impact_cancel_locator_uses_immutable_mapping_identity(selected):
    from tests import test_pm_work_start as starting
    from tests.test_pm_issue_mappings import cancellation
    from tests.test_pm_plan_impact_limits import clone_admissions
    from tests.test_pm_plan_operations import OWNER
    w, ops = selected, selected['d'].ops
    op, _ = ops.create(OWNER, **cancellation(w, w['publication']))
    starting._park_until_executed(ops, op['operation_id'])
    # Retention can remove the mapping's original proving events without removing
    # its immutable identity. They cannot be prerequisites for locating cancel.
    ops.db.execute("DELETE FROM api_events WHERE kind='pm.issue.recorded' OR resource_id=?", (w['publication']['operation_id'],))
    params = await selector(w)
    before = read(w, params)
    cancellation_subject = next(s for s in before['matrix']['subjects'] if s['kind'] == 'issue')
    assert cancellation_subject['operation_id'] == op['operation_id']
    assert cancellation_subject['boundary'] == 'creation_boundary_unproven'
    clone_admissions(ops, op['operation_id'], 1001)
    assert read(w, params) == before


async def test_impact_every_attempt_discovery_statement_uses_an_index(start_ready):
    from bat_agent_connector import pm_impact_discovery as discovery
    from bat_agent_connector import pm_plan_impact as impact
    from tests import test_pm_work_start as starting
    w, ops = start_ready, start_ready['d'].ops
    op, _ = ops.create(starting.STARTER_OWNER, **await starting._start_envelope(w))
    done = await starting._advance_start(ops, op['operation_id'])
    assert done['status'] == 'succeeded', done
    params = await selector(w)
    for indexed in (False, True):
        if indexed:
            discovery.schema(ops.journal)
        else:
            for name in discovery.INDEXES:
                ops.db.execute(f'DROP INDEX {name}')
        queries = []
        ops.db.set_trace_callback(queries.append)
        try:
            groups = impact._candidates(ops, w['workspace_id'], [w['target']['work_item_id']],
                                        ops.journal.api_head(), set(params['binding_ids']))
        finally:
            ops.db.set_trace_callback(None)
        assert op['operation_id'] in groups
        for query in dict.fromkeys(queries):
            # The journal cursor lookup is not subject discovery.
            if 'sqlite_sequence' in query:
                continue
            plan = [tuple(row) for row in ops.db.execute('EXPLAIN QUERY PLAN ' + query)]
            print('ATTEMPT_DISCOVERY_PLAN', indexed, query, json.dumps(plan))
            if indexed:
                text = ' '.join(row[3] for row in plan)
                assert 'INDEX' in text
                assert not any(term in text for term in ('SCAN api_events', 'SCAN operations', 'INTEGER PRIMARY KEY (rowid<'))
