"""Independent review's 50 x 43 roots, plus ordinary-only gated pages."""
import copy
import json

import pytest

from bat_agent_connector import pm_access, pm_package_handoff, pm_runs
from bat_agent_connector import pm_roadmap_report_materialization as reports
from bat_agent_connector.api_auth import Principal
from tests.test_pm_plan_operations import OWNER
from tests.test_pm_roadmap_report_cost import clones, insert
from tests.test_pm_roadmap_report_materialization import fixed as fixed_fixture
from tests.test_pm_roadmap_report_materialization import materialize
from tests.test_pm_roadmap_report_materialization import pm as pm_fixture
from tests.test_pm_roadmap_report_materialization import ready as ready_fixture
from tests.test_pm_roadmap_report_materialization import spec_fixed as spec_fixed_fixture
from tests.test_pm_roadmap_report_materialization import world as world_fixture

fixed, pm, ready, spec_fixed, world = fixed_fixture, pm_fixture, ready_fixture, spec_fixed_fixture, world_fixture


@pytest.mark.parametrize('admin', [False, True])
@pytest.mark.parametrize('report', [False, True])
async def test_independent_page_budget(fixed, report, admin):
    w = fixed
    ops, db, wid = w['d'].ops, w['d'].ops.db, w['workspace_id']
    principal = Principal('page-admin', OWNER.scopes, admin=True) if admin else OWNER
    action = reports.ACTION if report else 'repository.continue'
    if report:
        op = await materialize(w)
        clones(w, op, 1, 50)
    else:
        from tests.test_pm_package_handoff import request as handoff_request
        envelope = await handoff_request(w)
        binding = pm_package_handoff.admit(ops, OWNER, envelope['target'], envelope['params'], envelope['preconditions'])
        original = dict(db.execute('SELECT * FROM operations WHERE action=? LIMIT 1', ('pm.plan.draft',)).fetchone())
        run = dict(db.execute('SELECT * FROM pm_runs LIMIT 1').fetchone())
        state = dict(db.execute('SELECT * FROM pm_run_state WHERE run_id=?', (run['run_id'],)).fetchone())
        with ops.journal.tx():
            for i in range(50):
                oid, rid = 'op_' + f'{i:032x}', 'pmr_' + f'{i:032x}'
                value = {**original, 'operation_id': oid, 'idem_key': 'page-' + oid,
                         'action': action, 'status': 'succeeded', 'target': json.dumps(envelope['target']),
                         'params': json.dumps(envelope['params']), 'external_refs': json.dumps({'admission_binding': binding})}
                insert(db, 'operations', value)
                insert(db, 'pm_runs', {**run, 'operation_id': oid, 'run_id': rid,
                    'generation': run['generation'] + 1000 + i, 'purpose': 'Page budget fixture'})
                insert(db, 'pm_run_state', {**state, 'run_id': rid})
                db.execute('INSERT INTO pm_source_lineage VALUES(?,?,?,?)', ('operation', oid, oid, oid))
        assert not db.execute('SELECT 1 FROM operations WHERE action=?', (reports.ACTION,)).fetchone()
        assert not db.execute('SELECT 1 FROM pm_roadmap_report_materializations').fetchone()
    originals = [dict(r) for r in db.execute('SELECT * FROM operations WHERE action=? AND status=?', (action, 'succeeded'))]
    assert len(originals) == 50
    with ops.journal.tx():
        for i, row in enumerate(originals):
            for k in range(42):
                root = copy.deepcopy(row)
                rid = 'op_' + f'{100000 + i*42 + k:032x}'
                root.update(operation_id=rid, idem_key='root-' + rid, status='accepted', result=None)
                # Canonical admitted report shape before freeze, or ordinary workspace operation.
                insert(db, 'operations', root)
                db.execute('INSERT INTO pm_source_lineage VALUES(?,?,?,?)', ('operation', row['operation_id'], rid, row['operation_id']))
    ids = {r['operation_id'] for r in originals}
    alone = [ops.get(r['operation_id']) for r in originals]
    assert all(pm_access.visible(db, principal, row) for row in alone)
    cursor = ops.journal.api_head()
    for row in originals:
        ops.journal.api_event('operation', row['operation_id'], 'operation.succeeded', {'action': action}, actor=OWNER.actor)
    events = ops.journal.api_events(cursor, 500)
    expected_events = [e for e in events['events'] if pm_access.event_visible(db, principal, e)]
    assert len(expected_events) == 50
    runs = [pm_runs.get_by_operation(db, oid) for oid in ids]
    assert all(pm_runs.source_visible(db, principal, row) for row in runs)
    # These standalone predicates are the pre-page-scope semantics: full serialized rows,
    # not a count of grants that could accidentally refer to different records.
    actual_ops = ops.list(principal=principal, action=action, statuses=['succeeded'], limit=200)['operations']
    expected_ops = [ops._decode(row) for row in sorted(originals, key=lambda r: (r['created_at'], r['operation_id']), reverse=True)]
    assert json.dumps(actual_ops, sort_keys=True) == json.dumps(expected_ops, sort_keys=True)
    assert pm_access.filter_events(db, principal, events)['events'] == expected_events
    actual_runs = [r for r in pm_runs.list_runs(ops, principal, wid, limit=100)['runs'] if r['operation_id'] in ids]
    assert actual_runs == [pm_runs._summary(r) for r in sorted(runs, key=lambda r: r['run_id'])]
    if report:
        receipts = reports.list_materializations(ops, principal, wid, limit=50)['materializations']
        assert len(receipts) == 50
