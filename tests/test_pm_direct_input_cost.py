"""Bounded direct eligibility, original query plans and collector pass cost."""
import time

from bat_agent_connector import pm_attempt_results, pm_direct_inputs
from tests import test_pm_attempt_results as results
from tests.test_pm_attempt_result_marker import send_direct, terminal
from tests.test_pm_attempt_results_direct import collect
from tests.test_pm_workspace_wip import clone_row
from tests.test_pm_workspace_wip_lineage import closed_attempt

world = results.world
pm = results.pm
ready = results.ready
start_ready = results.start_ready


def plans(db, att):
    rows = [r[3] for r in db.execute('EXPLAIN QUERY PLAN ' + pm_direct_inputs.ELIGIBLE_SQL,
        (att['handoff_operation_id'], 'h1', att['session_id'], 501))]
    print('DIRECT INPUT PLAN', rows)
    assert any('pm_source_original_lookup' in p for p in rows)
    assert all('SCAN' not in p and 'TEMP B-TREE' not in p for p in rows)


async def test_direct_eligibility_bound_and_admission_order(start_ready, mock):
    w = start_ready
    att, _ = await closed_attempt(w, mock)
    w['d'].fleet.config.safety.max_writes_per_hour = 10000
    sent = []
    for n in range(500):
        sent.append(await send_direct(w, att, f'direct-bound-{n}'))
    ops = w['d'].ops
    plans(ops.db, att)
    values = pm_direct_inputs.eligible(ops.db, att['handoff_operation_id'],
        {'host': 'h1', 'session_id': att['session_id']})
    assert len(values) == 500
    assert [v['operation_id'] for v in values] == [op['operation_id'] for op in sent]
    terminal(mock, att, sent[0]['result']['message_id'])
    rows = await collect(w)
    assert sum(r['operation_id'] == sent[0]['operation_id'] for r in rows) == 1
    await send_direct(w, att, 'direct-bound-overflow')
    assert len(pm_direct_inputs.eligible(ops.db, att['handoff_operation_id'],
        {'host': 'h1', 'session_id': att['session_id']})) == 501
    rows = await collect(w)
    assert not any(r['operation_id'] in {v['operation_id'] for v in sent} for r in rows)
    assert w['d'].result_collector.direct_last_check[att['attempt_id']]['outcome'] == 'direct_input_limit'


async def test_thousand_attempt_pass_cost_with_unrelated_lineage(start_ready, mock, monkeypatch):
    w = start_ready
    att, _ = await closed_attempt(w, mock)
    db = w['d'].ops.db
    collector = w['d'].result_collector
    original = dict(db.execute('SELECT * FROM work_attempts WHERE attempt_id=?', (att['attempt_id'],)).fetchone())
    receipt = dict(db.execute('SELECT * FROM session_results WHERE attempt_id=?', (att['attempt_id'],)).fetchone())
    # Cost-only synthetic Attempt rows share a valid managed execution. Every pass
    # still performs its actual per-Attempt child/registry/receipt queries; no SQL
    # or provider predicate is mocked. These are not admission/release evidence.
    with w['d'].journal.tx():
        for n in range(999):
            aid = f'att_cost_{n}'
            clone_row(db, 'work_attempts', original, sequence=None, attempt_id=aid,
                      origin_kind=f'fixture_cost_{n}', origin_id=att['start_operation_id'], start_operation_id=None)
            clone_row(db, 'session_results', receipt, sequence=None, result_id=f'res_cost_{n}', attempt_id=aid,
                      source_position=pm_attempt_results.format_source_position(att['turn_marker'], f'cost_answer_{n}'))
    obs = collector._observed_session_row('h1', att['session_id'])
    for row in db.execute('SELECT attempt_id FROM work_attempts'):
        collector._memo[row[0]] = {'obs_tuple': (obs['digest'], obs['sort_key'], obs['gone_at']),
                                   'checked_at': time.monotonic()}
    collector.memo_recheck_s = 10**9
    async def measure(callback=True):
        ticks = 0
        def progress():
            nonlocal ticks
            ticks += 1
        if callback:
            db.set_progress_handler(progress, 1)
        start = time.perf_counter()
        try:
            assert not await collector.collect_host('h1')
        finally:
            duration = time.perf_counter() - start
            db.set_progress_handler(None, 0)
        return ticks if callback else None, duration
    with monkeypatch.context() as old:
        old.setattr(pm_direct_inputs, 'eligible', lambda *args, **kwargs: [])
        baseline = await measure()
    current = await measure()
    with w['d'].journal.tx():
        db.executemany('INSERT INTO pm_source_lineage VALUES(?,?,?,?)',
            [('operation', f'fixture-unrelated-{n}', f'fixture-other-root-{n}', f'fixture-unrelated-{n}')
             for n in range(20000)])
    noisy = await measure()
    without_callback = await measure(False)
    plans(db, att)
    print('COLLECTOR PASS VM/time baseline=', baseline, 'extension=', current,
          'unrelated20000=', noisy, 'without_callback=', without_callback)
    assert len(collector.last_check) == 1000
    assert current[0] <= baseline[0] * 1.2
    assert noisy[0] <= baseline[0] * 1.2
