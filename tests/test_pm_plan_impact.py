"""Retained-event joins use real temporary Git and mock providers, never current heads."""
from __future__ import annotations

import json

import pytest

from bat_agent_connector import pm_evidence, pm_plans, work_items
from bat_agent_connector import pm_plan_impact as impact
from bat_agent_connector.operations import OperationError
from tests import test_pm_work_start as starting
from tests.test_pm_issue_mappings import mapped as mapped
from tests.test_pm_issue_mappings import publish
from tests.test_pm_plan_operations import OWNER, adopt, draft
from tests.test_pm_quality_assessment_transport import table_counts
from tests.test_pm_snapshots import snapshot
from tests.test_pm_work_packages import action, associate
from tests.test_pm_work_packages import pm as pm
from tests.test_pm_work_packages import ready as ready
from tests.test_pm_work_packages import world as world

start_ready = starting.start_ready


async def selector(w):
    ops, wid = w['d'].ops, w['workspace_id']
    later = await draft(w, requirements=[{**w['requirements'][0], 'acceptance_input_ids': []}])
    snap = await snapshot(ops, wid, who=OWNER)
    return {'workspace_id': wid, 'snapshot_id': snap['snapshot_id'],
        'from_revision_id': w['revision']['revision_id'], 'to_revision_id': later['revision_id'],
        'binding_ids': [pm_evidence._binding(ops.db, **w['target'])['binding_id']]}


def read(w, params):
    return impact.preview(w['d'].ops, OWNER, **params)


@pytest.fixture
async def selected(mapped):
    w = mapped
    w['publication'] = await publish(w)
    w['impact_selector'] = await selector(w)
    return w


async def test_impact_fixed_package_acceptance_and_issue_edges_are_read_only(selected, mock, monkeypatch):
    w, ops = selected, selected['d'].ops
    def forbidden(*args, **kwargs):
        pytest.fail('Impact reads must never contact a provider or collector')
    monkeypatch.setattr(w['d'].fleet, 'client', forbidden)
    monkeypatch.setattr(w['d'].result_collector, 'collect_host', forbidden)
    monkeypatch.setitem(ops.context, 'pm_native_runtime_factory', forbidden)
    monkeypatch.setattr(w['gh'], 'before_request', forbidden)
    before = (table_counts(ops.db), ops.db.total_changes, len(mock.invokes), len(w['gh'].requests))
    queries = []
    ops.db.set_trace_callback(queries.append)
    try:
        out = read(w, w['impact_selector'])
    finally:
        ops.db.set_trace_callback(None)
    assert before == (table_counts(ops.db), ops.db.total_changes, len(mock.invokes), len(w['gh'].requests))
    assert [q for q in queries if q.startswith('BEGIN')] == ['BEGIN']
    assert not any(q.lstrip().split()[0].upper() in {'INSERT', 'UPDATE', 'DELETE', 'CREATE', 'ALTER'} for q in queries)
    matrix = out['matrix']
    assert out['impact_digest'] == impact.diff._digest(matrix)
    assert not matrix['coverage']['project_complete'] and not matrix['work_update_authorized']
    assert matrix['coverage']['undiscoverable_history'] == 'creation_boundary_unproven'
    package = next(s for s in matrix['subjects'] if s['kind'] == 'package')
    issue = next(s for s in matrix['subjects'] if s['kind'] == 'issue')
    assert package['boundary'] == issue['boundary'] == 'proven'
    assert package['requirements'][0]['acceptance']
    assert package['verification'][0]['acceptance_input_ids']
    assert issue['issue']['id'] == w['publication']['result']['issue']['id']
    assert issue['publication'] == 'receipt_recorded'
    assert issue['cross_scope_publication'] == 'unsupported'
    assert 'Selected public description' not in json.dumps(out)


async def test_impact_remains_fixed_after_titles_bindings_issue_heads_and_plan_supersession(selected):
    w, ops = selected, selected['d'].ops
    params = w['impact_selector']
    before = read(w, params)
    await publish(w, previous=w['publication']['result']['revision_id'], public_body='Later append')
    item = work_items._get_item(ops.db, w['target']['work_item_id'])
    await action(w, 'work_item.update', {'work_item_id': item['work_item_id']}, {'title': 'Later title'},
                 {'expected_version': item['version']})
    adopted = await adopt(w, {'revision_id': params['to_revision_id']})
    assert adopted['status'] == 'succeeded', adopted
    await associate(w, params['to_revision_id'])
    await draft(w, requirements=w['requirements'], title='A later proposal supersedes the comparison head')
    assert read(w, params) == before


@pytest.mark.parametrize('kind', ['pm.work_package.created', 'pm.issue.recorded'])
async def test_impact_missing_creation_event_keeps_original_intent_unknown(selected, kind):
    w, ops = selected, selected['d'].ops
    ops.db.execute('DELETE FROM api_events WHERE kind=?', (kind,))
    value = read(w, w['impact_selector'])
    subject = next(s for s in value['matrix']['subjects'] if s['kind'] == ('package' if 'package' in kind else 'issue'))
    assert subject['boundary'] == 'creation_boundary_unproven'
    assert 'mapping_id' not in subject
    if kind == 'pm.work_package.created':
        assert subject['package_ref'] is None
    # The surviving admission remains the stable subject identity, never a resend key.
    assert subject['operation_id'].startswith('op_')


async def test_impact_package_created_after_cursor_never_fills_an_unknown_admission(ready):
    w, ops = ready, ready['d'].ops
    from tests.test_pm_work_packages import request
    op, _ = ops.create(OWNER, **request(w))
    starting._park_until_executed(ops, op['operation_id'])
    params = await selector(w)
    before = read(w, params)
    assert before['matrix']['subjects'][0]['boundary'] == 'creation_boundary_unproven'
    await ops._execute(op['operation_id'])
    assert ops.get(op['operation_id'])['status'] == 'succeeded'
    assert read(w, params) == before


async def test_impact_attempt_and_execution_edges_require_the_retained_finish(start_ready):
    w, ops = start_ready, start_ready['d'].ops
    env = await starting._start_envelope(w)
    op, _ = ops.create(starting.STARTER_OWNER, **env)
    done = await starting._advance_start(ops, op['operation_id'])
    assert done['status'] == 'succeeded', done
    params = await selector(w)
    out = read(w, params)
    attempt = next(s for s in out['matrix']['subjects'] if s['kind'] == 'attempt')
    execution = next(s for s in out['matrix']['subjects'] if s['kind'] == 'execution')
    assert attempt['association'] == attempt['boundary'] == 'proven'
    assert attempt['execution_operation_id'] == execution['operation_id']
    assert attempt['package_ref'] == execution['package_ref']
    ops.db.execute("DELETE FROM api_events WHERE kind='work.start.succeeded'")
    unknown = read(w, params)
    attempt = next(s for s in unknown['matrix']['subjects'] if s['kind'] == 'attempt')
    assert attempt['association'] == 'association_unknown' and attempt['package_ref'] is None
    ops.db.execute("UPDATE operations SET status_reason='Later progress' WHERE operation_id=?", (op['operation_id'],))
    assert read(w, params) == unknown


@pytest.mark.parametrize('source', ['snapshot', 'from', 'to', 'package', 'issue'])
async def test_impact_withdraw_each_original_operation_source_fails_whole_matrix(selected, source):
    w, ops = selected, selected['d'].ops
    params = w['impact_selector']
    read(w, params)
    ids = {
        'snapshot': ops.db.execute('SELECT operation_id FROM pm_document_snapshots WHERE snapshot_id=?', (params['snapshot_id'],)).fetchone()[0],
        'from': pm_plans._revision(ops.db, w['workspace_id'], params['from_revision_id'])['operation_id'],
        'to': pm_plans._revision(ops.db, w['workspace_id'], params['to_revision_id'])['operation_id'],
        'package': w['package']['operation_id'], 'issue': w['publication']['operation_id']}
    await attach_source_and_withdraw(w, ids[source], params)
    with pytest.raises(OperationError) as error:
        read(w, params)
    assert error.value.code == 'PM_IMPACT_SOURCE_UNAVAILABLE'
    assert error.value.status == 404
    assert not any(value in error.value.message for value in (params['snapshot_id'], w['package']['package_id'], ids[source]))


@pytest.mark.parametrize('bound', ['MAX_SUBJECTS', 'MAX_CANDIDATES', 'MAX_BYTES', 'DIFF_BYTES'])
async def test_impact_bounds_fail_explicitly_never_truncate(selected, monkeypatch, bound):
    if bound == 'DIFF_BYTES':
        monkeypatch.setattr(impact.diff, 'MAX_RESPONSE_BYTES', 1)
    else:
        monkeypatch.setattr(impact, bound, 1)
    with pytest.raises(OperationError) as error:
        read(selected, selected['impact_selector'])
    assert error.value.code == 'PM_IMPACT_TOO_LARGE' and error.value.status == 413


async def test_impact_binding_selection_and_baseline_are_exact(selected):
    w, ops = selected, selected['d'].ops
    params = w['impact_selector']
    for change, code in [({'binding_ids': []}, 'INVALID_PARAMS'),
                         ({'binding_ids': params['binding_ids'] * 2}, 'INVALID_PARAMS'),
                         ({'binding_ids': ['pmwb_' + '0' * 32]}, 'PM_IMPACT_BINDING_MISMATCH'),
                         ({'from_revision_id': params['to_revision_id']}, 'PM_IMPACT_BASELINE_MISMATCH')]:
        with pytest.raises(OperationError) as error:
            read(w, params | change)
        assert error.value.code == code
    ops.db.execute("DELETE FROM api_events WHERE kind='pm.work.bind'")
    with pytest.raises(OperationError, match='PM_DIFF_CURSOR_UNPROVEN'):
        read(w, params)

async def attach_source_and_withdraw(w, operation_id, params):
    from bat_agent_connector import pm_workspace_adapter
    from tests.test_work_items import act
    d = w['d']
    original_workspace = (await act(d, OWNER, 'pm.workspace.create', params={'name': 'Original private source'}))['result']['workspace']['workspace_id']
    source = await act(d, OWNER, pm_workspace_adapter.ACTION, {'workspace_id': original_workspace},
        {'adapter': pm_workspace_adapter.ADAPTERS[0], 'rationale': 'Original synthetic authority'},
        pm_workspace_adapter.read(d.ops, OWNER, original_workspace)['preconditions'])
    # Model one retained original provenance edge. Both endpoints and the source
    # access policy are real; no visibility reader is replaced by the fixture.
    with d.journal.tx():
        d.ops.db.execute('INSERT INTO pm_source_lineage VALUES(?,?,?,?)',
            ('operation', operation_id, source['operation_id'], source['operation_id']))
    read(w, params)
    d.ops.db.execute('DELETE FROM pm_workspace_members WHERE workspace_id=? AND actor=?',
                     (original_workspace, OWNER.actor))


@pytest.mark.parametrize('source', ['attempt', 'execution'])
async def test_impact_withdraw_attempt_or_execution_original_source(start_ready, source):
    w, ops = start_ready, start_ready['d'].ops
    op, _ = ops.create(starting.STARTER_OWNER, **await starting._start_envelope(w))
    done = await starting._advance_start(ops, op['operation_id'])
    assert done['status'] == 'succeeded', done
    params = await selector(w)
    matrix = read(w, params)['matrix']
    oid = next(s['operation_id'] for s in matrix['subjects'] if s['kind'] == source)
    await attach_source_and_withdraw(w, oid, params)
    with pytest.raises(OperationError, match='PM_IMPACT_SOURCE_UNAVAILABLE'):
        read(w, params)


async def test_impact_late_attempt_finish_does_not_restore_an_open_unknown(start_ready):
    w, ops = start_ready, start_ready['d'].ops
    oid, _ = await starting._hold_before_handoff(w)
    params = await selector(w)
    before = read(w, params)
    attempt = next(s for s in before['matrix']['subjects'] if s['kind'] == 'attempt')
    assert attempt['association'] == 'association_unknown'
    done = await starting._advance_start(ops, oid)
    assert done['status'] == 'succeeded', done
    assert read(w, params) == before
    await attach_source_and_withdraw(w, oid, params)
    with pytest.raises(OperationError, match='PM_IMPACT_SOURCE_UNAVAILABLE'):
        read(w, params)


async def test_impact_filtered_snapshot_never_returns_a_partial_matrix(ready):
    from bat_agent_connector import pm_snapshots
    from tests.test_pm_work_packages import add_task_evidence
    w, ops = ready, ready['d'].ops
    task, _ = await add_task_evidence(w)
    params = await selector(w)
    assert read(w, params)
    ops.db.execute("UPDATE tasks SET controller_authority='{}' WHERE task_id=?", (task['task_id'],))
    assert pm_snapshots.get_snapshot(ops, OWNER, w['workspace_id'], params['snapshot_id'])['snapshot']['visibility']['filtered']
    with pytest.raises(OperationError, match='PM_IMPACT_SOURCE_UNAVAILABLE'):
        read(w, params)


async def test_impact_removed_events_never_backfill_from_rows_or_timestamps(selected):
    w, ops = selected, selected['d'].ops
    params = w['impact_selector']
    before = read(w, params)
    ops.db.execute('UPDATE api_events SET created_at=0')
    assert read(w, params) == before
    for oid in (w['package']['operation_id'], w['publication']['operation_id']):
        ops.db.execute("DELETE FROM api_events WHERE resource_id=? OR json_extract(body,'$.operation_id')=?", (oid, oid))
    missing = read(w, params)
    assert missing['matrix']['subjects'] == []
    assert missing['matrix']['coverage']['undiscoverable_history'] == 'creation_boundary_unproven'


async def test_impact_duplicate_creation_proof_fails_closed(selected):
    w, ops = selected, selected['d'].ops
    row = ops.db.execute("SELECT * FROM api_events WHERE kind='pm.work_package.created'").fetchone()
    ops.journal.api_event(row['resource_type'], row['resource_id'], row['kind'], json.loads(row['body']), actor=OWNER.actor)
    params = await selector(w)
    with pytest.raises(OperationError, match='PM_IMPACT_EVIDENCE_INVALID'):
        read(w, params)


async def test_impact_excluded_binding_never_reads_withdrawn_package(ready, monkeypatch):
    from tests.test_pm_work_packages import create
    w, ops = ready, ready['d'].ops
    old = await create(w)
    old_params = await selector(w)
    await associate(w)
    params = await selector(w)
    before = read(w, params)
    assert before['matrix']['subjects'] == []
    await attach_source_and_withdraw(w, old['operation_id'], params)
    assert read(w, params) == before
    with pytest.raises(OperationError, match='PM_IMPACT_SOURCE_UNAVAILABLE'):
        read(w, old_params)
    original = ops.get
    def get(oid, **kwargs):
        assert oid != old['operation_id'], 'Excluded subjects must not invoke a reader'
        return original(oid, **kwargs)
    monkeypatch.setattr(ops, 'get', get)
    assert read(w, params) == before


async def test_impact_mixed_selected_bindings_require_each_source(ready):
    from tests.test_pm_work_packages import create
    w, ops = ready, ready['d'].ops
    first = await create(w)
    first_target = w['target']
    first_binding = pm_evidence._binding(ops.db, **first_target)['binding_id']
    project = work_items._get_item(ops.db, first_target['work_item_id'])['project_id']
    second = await action(w, 'work_item.create', {'project_id': project}, {'title': 'Second selected work'})
    w['target'] = first_target | {'work_item_id': second['work_item_id']}
    await associate(w)
    await create(w)
    params = await selector(w)
    mixed = params | {'binding_ids': params['binding_ids'] + [first_binding]}
    assert len(read(w, mixed)['matrix']['subjects']) == 2
    await attach_source_and_withdraw(w, first['operation_id'], mixed)
    assert len(read(w, params)['matrix']['subjects']) == 1
    with pytest.raises(OperationError, match='PM_IMPACT_SOURCE_UNAVAILABLE'):
        read(w, mixed)
