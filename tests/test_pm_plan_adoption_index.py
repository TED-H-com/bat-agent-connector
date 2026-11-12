"""Startup adoption lookup indexing preserves latest rows and timestamp ties."""
import ast
import inspect
import json
import sqlite3
import time

import pytest

from bat_agent_connector import pm_plans
from bat_agent_connector.operations import OperationError
from bat_agent_connector.task_journal import Journal
from tests.test_pm_plan_impact import mapped as mapped
from tests.test_pm_plan_impact import pm as pm
from tests.test_pm_plan_impact import read
from tests.test_pm_plan_impact import ready as ready
from tests.test_pm_plan_impact import selected as selected
from tests.test_pm_plan_impact import world as world
from tests.test_pm_plan_impact_discovery import measure
from tests.test_pm_plans import adopt, adoption, draft, planning
from tests.test_pm_plans import ops as ops

INDEX = 'pm_plan_adoption_revision'
DDL = f'CREATE INDEX IF NOT EXISTS {INDEX} ON pm_plan_adoptions(workspace_id,revision_id,created_at)'


def lookups():
    statements = [node.value for node in sorted(ast.walk(ast.parse(inspect.getsource(pm_plans))),
                                               key=lambda node: getattr(node, "lineno", 0))
                  if isinstance(node, ast.Constant) and isinstance(node.value, str)
                  and node.value.startswith('SELECT ') and 'FROM pm_plan_adoptions WHERE workspace_id=?' in node.value
                  and 'LIMIT 1' in node.value]
    assert len(statements) == 3
    return statements


def queries(db, wid, rid):
    out = []
    for sql in lookups():
        args = (wid, rid) * (sql.count('?') // 2)
        out.append({'sql': sql, 'plan': [tuple(row) for row in db.execute('EXPLAIN QUERY PLAN ' + sql, args)],
                    'row': tuple(db.execute(sql, args).fetchone())})
    return out


def adoption_noise(w, count=2000):
    ops = w['d'].ops
    original = dict(ops.db.execute('SELECT * FROM pm_plan_adoptions LIMIT 1').fetchone())
    keys = list(original)
    with ops.journal.tx():
        for i in range(count):
            value = original | {'operation_id': 'op_' + f'{700000+i:032x}',
                'decision_id': 'pmdec_' + f'{700000+i:032x}', 'workspace_id': 'ws_' + 'f' * 32}
            ops.db.execute(f"INSERT INTO pm_plan_adoptions({','.join(keys)}) VALUES({','.join('?' for _ in keys)})", [value[k] for k in keys])


async def test_impact_preview_adoption_noise_stays_within_instruction_budget(selected):
    w = selected
    before, small, _ = measure(w, w['impact_selector'])
    adoption_noise(w)
    after, large, _ = measure(w, w['impact_selector'])
    print('ADOPTION_NOISE_VM', small, large, 'ratio', large / small)
    assert before == after
    assert large <= small * 1.2


async def test_plan_adoption_index_upgrade_twice_preserves_results_and_version(selected, tmp_path, monkeypatch):
    w, ops = selected, selected['d'].ops
    adoption_noise(w)
    expected = read(w, w['impact_selector'])
    path = tmp_path / 'adoption-upgrade.sqlite3'
    old = sqlite3.connect(path, isolation_level=None)
    ops.db.backup(old)
    old.execute(f'DROP INDEX IF EXISTS {INDEX}')
    version = old.execute('PRAGMA user_version').fetchone()[0]
    inventory = {row[0]: row[1] for row in old.execute('SELECT name,sql FROM sqlite_master')}
    before = queries(old, w['workspace_id'], w['revision']['revision_id'])
    print('ADOPTION_PLANS_BEFORE', json.dumps(before))
    started = time.perf_counter()
    old.execute(DDL)
    seconds = time.perf_counter() - started
    size = old.execute('SELECT SUM(pgsize) FROM dbstat WHERE name=?', (INDEX,)).fetchone()[0]
    print('ADOPTION_INDEX_ONLY_BUILD', 'seconds', seconds, 'bytes', size)
    old.execute(f'DROP INDEX {INDEX}')
    old.close()
    for reopening in range(2):
        journal = Journal(path)
        try:
            started = time.perf_counter()
            pm_plans.schema(journal)
            seconds = time.perf_counter() - started
            after = queries(journal.db, w['workspace_id'], w['revision']['revision_id'])
            print('ADOPTION_PLANS_AFTER', reopening, json.dumps(after))
            assert [q['row'] for q in before] == [q['row'] for q in after]
            assert journal.db.execute('PRAGMA user_version').fetchone()[0] == version
            current = {row[0]: row[1] for row in journal.db.execute('SELECT name,sql FROM sqlite_master')}
            assert current.keys() - inventory.keys() == {INDEX}
            assert {k: v for k, v in current.items() if k != INDEX} == inventory
            assert current[INDEX] == DDL.replace(' IF NOT EXISTS', '')
            for query in after:
                plan = ' '.join(row[3] for row in query['plan'])
                assert f'INDEX {INDEX}' in plan
                assert 'SCAN ' not in plan and 'TEMP B-TREE' not in plan
            size = journal.db.execute('SELECT SUM(pgsize) FROM dbstat WHERE name=?', (INDEX,)).fetchone()[0]
            print('ADOPTION_INDEX_BUILD', reopening, 'seconds', seconds, 'bytes', size)
            with monkeypatch.context() as patch:
                patch.setattr(ops, 'db', journal.db)
                patch.setattr(ops.journal, 'db', journal.db)
                assert read(w, w['impact_selector']) == expected
        finally:
            journal.close()


async def test_plan_adoption_equal_timestamp_rows_keep_original_tie_result(selected):
    db = selected['d'].ops.db
    # Imported/synthetic history is permitted by the table even though the action
    # cannot readopt the same revision. Pin the pre-index rowid tie selection too.
    db.execute(f'DROP INDEX IF EXISTS {INDEX}')
    original = dict(db.execute('SELECT * FROM pm_plan_adoptions LIMIT 1').fetchone())
    keys = list(original)
    later = original | {'operation_id': 'op_' + 'e' * 32, 'decision_id': 'pmdec_' + 'e' * 32,
                        'scope_revision': 'pmscope_' + 'e' * 32, 'rationale': 'Later row, identical timestamp',
                        'result': '{}'}
    db.execute(f"INSERT INTO pm_plan_adoptions({','.join(keys)}) VALUES({','.join('?' for _ in keys)})", [later[k] for k in keys])
    before = queries(db, original['workspace_id'], original['revision_id'])
    # These are today's observable results without the authorized index.
    assert before[0]['row'] == (original['operation_id'],)
    assert before[1]['row'] == (original['scope_revision'],)
    assert before[2]['row'] == (original['result'], original['rationale'], original['created_at'])
    pm_plans.schema(selected['d'].ops.journal)
    after = queries(db, original['workspace_id'], original['revision_id'])
    assert [q['row'] for q in after] == [q['row'] for q in before]


async def test_adoption_action_replay_and_frozen_timestamp_do_not_readopt_revision(ops, monkeypatch):
    target, _, _, _ = await planning(ops)
    first = await draft(ops, target)
    ctx = adoption(ops, target, first['revision_id'])
    # Patch the module binding, not the shared stdlib time function.
    class Clock:
        @staticmethod
        def time():
            return 1_700_000_000.0
    monkeypatch.setattr(pm_plans, 'time', Clock)
    result = adopt(ctx)
    assert adopt(ctx) == result
    with pytest.raises(OperationError, match='PM_ACCEPTED_REVISION_CONFLICT'):
        adoption(ops, target, first['revision_id'], previous=first['revision_id'])
    second = await draft(ops, target, title='Next fixed revision')
    adopt(adoption(ops, target, second['revision_id'], previous=first['revision_id']))
    rows = ops.db.execute('SELECT revision_id,created_at FROM pm_plan_adoptions ORDER BY rowid').fetchall()
    assert [row[0] for row in rows] == [first['revision_id'], second['revision_id']]
    assert len({row[1] for row in rows}) == 1
