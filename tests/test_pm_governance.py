"""Sourced findings, explicit root causes, durable decisions and source withdrawal."""
from __future__ import annotations

import copy
import json
import sqlite3
import uuid

import pytest

from bat_agent_connector import pm_access, pm_plans, pm_sprints, pm_workspace
from bat_agent_connector import pm_governance as pm
from bat_agent_connector.api_auth import Principal
from bat_agent_connector.operations import OpContext, OperationError
from tests.test_pm_discussions import CONTRIBUTOR, OUTSIDER, OWNER, READER, member, settle, workspace
from tests.test_pm_evidence import capture, link, task
from tests.test_pm_plans import adopt, adoption, draft
from tests.test_pm_snapshots import accepted, snapshot, work_for
from tests.test_pm_snapshots import ops as snapshot_ops_fixture

snapshot_ops = snapshot_ops_fixture


@pytest.fixture
def ops(snapshot_ops):
    version = snapshot_ops.db.execute('PRAGMA user_version').fetchone()[0]
    pm.install(snapshot_ops)
    pm.install(snapshot_ops)
    assert snapshot_ops.db.execute('PRAGMA user_version').fetchone()[0] == version
    return snapshot_ops


async def prepared(ops, *, evidence=False):
    target, plan, confirmed, assumption = await accepted(ops)
    source = None
    if evidence:
        work = await work_for(ops, target)
        source = task(ops)
        await link(ops, work, 'task', source['task_id'])
        ops.db.execute("UPDATE tasks SET state='needs_ted' WHERE task_id=?", (source['task_id'],))
        await capture(ops, work)
    snap = await snapshot(ops, target['workspace_id'])
    return {'target': target, 'wid': target['workspace_id'], 'plan': plan, 'confirmed': confirmed,
            'assumption': assumption, 'snapshot': snap, 'task': source}


def fields(data, **changes):
    return {'title': 'Account recovery depends on an unverified assumption',
            'description': 'The selected quality criterion has not been verified.',
            'owner_actor': CONTRIBUTOR.actor, 'affected_requirement_ids': ['login'],
            'trigger': 'Before approving the first release', 'response': 'Arrange a focused verification',
            'review_at': None, 'source_refs': [{'kind': 'plan_input', 'id': data['assumption']}],
            'rationale': 'Keep the uncertainty explicit', **changes}


def request(ops, data, **changes):
    return {'action': 'pm.governance.record', 'target': {'workspace_id': data['wid']},
            'params': {'kind': 'risk', 'root_cause_key': 'unverified-recovery', 'snapshot_id': data['snapshot']['snapshot_id'], **fields(data), **changes},
            'preconditions': {'expected_plan_version': pm_plans.head(ops.db, data['wid'])['version']}, 'idempotency_key': uuid.uuid4().hex}


async def record(ops, data, *, who=CONTRIBUTOR, **changes):
    op, _ = ops.create(who, **request(ops, data, **changes))
    return (await settle(ops, op))['result']


def read(ops, result, who=OWNER):
    return pm.get_record(ops, who, result['workspace_id'], result['record_id'])


def mutation(ops, result, action, params):
    return {'action': 'pm.governance.' + action, 'target': {k: result[k] for k in ('workspace_id', 'record_id')},
            'params': params, 'preconditions': {'expected_version': read(ops, result)['record']['version']},
            'idempotency_key': uuid.uuid4().hex}


async def change(ops, result, action, *, who=OWNER, **params):
    op, _ = ops.create(who, **mutation(ops, result, action, params))
    return (await settle(ops, op))['result']


def execution_state(ops):
    return {table: [dict(r) for r in ops.db.execute('SELECT * FROM ' + table)]
            for table in ('tasks', 'commands', 'work_items', 'pm_plan_heads', 'pm_plan_adoptions')}


async def test_sourced_assumption_preserves_original_truth_and_no_execution(ops):
    data = await prepared(ops)
    before = execution_state(ops)
    result = await record(ops, data, kind='assumption')
    view = read(ops, result, READER)
    assert view['record']['context']['plan_revision_id'] == data['plan']['revision_id']
    document = view['revision']['document']
    assert document['sources'][0]['source']['status'] == 'agent_assumption'
    assert document['assessment_basis'] == 'attributed_assumption'
    assert document['verification_status'] == 'unassessed' and document['decision'] is None
    assert document['review_at'] is None and not document['execution_authorized'] and not document['scope_applied']
    assert execution_state(ops) == before
    with pytest.raises(sqlite3.IntegrityError, match='immutable'):
        ops.db.execute("UPDATE pm_governance_records SET context='{}'")
    with pytest.raises(sqlite3.IntegrityError, match='immutable'):
        ops.db.execute('DELETE FROM pm_governance_revisions')


async def test_root_cause_dedup_preserves_first_finding_even_new_snapshot_new_operation(ops):
    data = await prepared(ops)
    first = await record(ops, data)
    frozen = read(ops, first)
    data['snapshot'] = await snapshot(ops, data['wid'])
    repeat = await record(ops, data, title='Another analysis phrasing', response='A different suggestion')
    assert repeat['record_id'] == first['record_id'] and repeat['deduplicated'] and not repeat['input_applied']
    assert read(ops, first) == frozen
    assert len(pm.list_records(ops, OWNER, data['wid'])['records']) == 1
    assert len(pm.list_history(ops, OWNER, data['wid'], first['record_id'])['revisions']) == 1


async def test_same_evidence_can_support_different_risks_only_with_explicit_distinction(ops):
    data = await prepared(ops, evidence=True)
    evidence = next(e for e in data['snapshot']['evidence'] if e['source_kind'] == 'execution_state')
    refs = [{'kind': 'evidence', 'id': evidence['evidence_id']}]
    first = await record(ops, data, source_refs=refs)
    with pytest.raises(OperationError, match='PM_GOVERNANCE_DUPLICATE_CANDIDATE'):
        ops.create(CONTRIBUTOR, **request(ops, data, root_cause_key='a-different-cause', source_refs=refs))
    second = await record(ops, data, root_cause_key='a-different-cause', source_refs=refs,
                          disambiguation={'record_ids': [first['record_id']], 'rationale': 'The same execution also exposes a separate resource dependency'})
    assert second['record_id'] != first['record_id']
    assert read(ops, second)['revision']['document']['disambiguation']['record_ids'] == [first['record_id']]
    repeat = await record(ops, data, root_cause_key='a-different-cause', source_refs=refs)
    assert repeat['record_id'] == second['record_id'] and repeat['deduplicated']
    with pytest.raises(OperationError, match='PM_GOVERNANCE_DUPLICATE_CANDIDATE'):
        ops.create(CONTRIBUTOR, **request(ops, data, root_cause_key='third-cause', source_refs=refs,
                   disambiguation={'record_ids': [first['record_id']], 'rationale': 'Missing another source overlap'}))


async def test_revision_adds_same_cause_symptoms_and_retains_baseline_history(ops):
    data = await prepared(ops)
    first = await record(ops, data)
    original = read(ops, first)
    refs = [{'kind': 'plan_input', 'id': data['assumption']}, {'kind': 'requirement', 'id': 'login'}]
    await change(ops, first, 'revise', who=CONTRIBUTOR, **fields(data, source_refs=refs, status='reported_resolved',
                                                             response='The owner reports a mitigation was applied'))
    latest = read(ops, first)
    assert latest['record']['context'] == original['record']['context']
    assert latest['revision']['document']['status'] == 'reported_resolved'
    assert latest['revision']['document']['verification_status'] == 'unassessed'
    history = pm.list_history(ops, READER, data['wid'], first['record_id'])
    assert history['revisions'][0] == original['revision'] and len(history['revisions']) == 2
    with pytest.raises(OperationError, match='Original sources cannot be removed'):
        ops.create(OWNER, **mutation(ops, first, 'revise', fields(data)))
    newer = await draft(ops, data['target'], title='Later scope proposal')
    adopt(adoption(ops, data['target'], newer['revision_id'], previous=data['plan']['revision_id']))
    assert read(ops, first) == latest


async def test_concurrent_mutation_cas_and_original_key_lost_reply(ops):
    data = await prepared(ops)
    result = await record(ops, data)
    req = mutation(ops, result, 'revise', fields(data, title='Original accepted management edit'))
    one, _ = ops.create(OWNER, **req)
    two, _ = ops.create(OWNER, **{**req, 'idempotency_key': uuid.uuid4().hex,
                                'params': fields(data, title='Concurrent edit must fail')})
    first = await settle(ops, one)
    assert ops.get(two['operation_id'])['status'] == 'failed'
    assert ops.get(two['operation_id'])['error_code'] == 'VERSION_CONFLICT'
    replay, created = ops.create(OWNER, **req)
    assert not created and replay['operation_id'] == one['operation_id'] and replay['result'] == first['result']
    assert read(ops, result)['revision']['document']['title'] == req['params']['title']


async def test_committed_effect_recovery_does_not_reapply_after_revocation(ops):
    data = await prepared(ops)
    req = request(ops, data)
    op, _ = ops.create(CONTRIBUTOR, **req)
    result = await pm._run(OpContext(ops, op))
    await member(ops, {'workspace_id': data['wid']}, CONTRIBUTOR.actor, None)
    assert await pm._run(OpContext(ops, ops.get(op['operation_id']))) == result
    assert ops.db.execute('SELECT COUNT(*) FROM pm_governance_revisions').fetchone()[0] == 1
    with pytest.raises(OperationError):
        ops.create(CONTRIBUTOR, **req)


async def test_pending_effect_rechecks_requester_and_owner_membership(ops):
    data = await prepared(ops)
    op, _ = ops.create(CONTRIBUTOR, **request(ops, data))
    # Current membership is execution authority; admission is not a forever grant.
    ops.db.execute('DELETE FROM pm_workspace_members WHERE workspace_id=? AND actor=?', (data['wid'], CONTRIBUTOR.actor))
    await settle(ops, op, success=False)
    assert ops.get(op['operation_id'])['status'] == 'failed'
    assert ops.db.execute('SELECT COUNT(*) FROM pm_governance_records').fetchone()[0] == 0


@pytest.mark.parametrize('who', [READER, OUTSIDER, Principal('project-owner', frozenset({'manage'}))])
async def test_write_permissions_require_observe_manage_and_current_writer(ops, who):
    data = await prepared(ops)
    with pytest.raises(OperationError):
        ops.create(who, **request(ops, data))


@pytest.mark.parametrize('owner', [READER.actor, OUTSIDER.actor, 'unknown-agent'])
async def test_risk_owner_is_a_current_writer(ops, owner):
    data = await prepared(ops)
    with pytest.raises(OperationError, match='FORBIDDEN'):
        ops.create(OWNER, **request(ops, data, owner_actor=owner))


@pytest.mark.parametrize('value', ['2026-02-30T09:00:00Z', '2026-10-11', '2026-10-11T09:00:00',
                                   '2026-10-11T09:00:00+01:99', '2026-10-11T24:00:00Z', True])
async def test_review_time_rejects_invalid_or_ambiguous_dates(ops, value):
    data = await prepared(ops)
    with pytest.raises(OperationError, match='INVALID_PARAMS'):
        ops.create(OWNER, **request(ops, data, review_at=value))


@pytest.mark.parametrize('value', [None, '2028-02-29T09:30:00Z', '2026-10-11T09:30:00-04:00'])
async def test_review_time_preserves_explicit_timezone_or_unknown(ops, value):
    data = await prepared(ops)
    result = await record(ops, data, review_at=value)
    assert read(ops, result)['revision']['document']['review_at'] == value


async def test_drift_requires_plan_and_observation_but_does_not_validate_semantic_claim(ops):
    data = await prepared(ops, evidence=True)
    with pytest.raises(OperationError, match='reported drift needs'):
        ops.create(OWNER, **request(ops, data, kind='drift', dimension='scope'))
    evidence = data['snapshot']['evidence'][0]
    refs = [{'kind': 'evidence', 'id': evidence['evidence_id']}, {'kind': 'requirement', 'id': 'login'}]
    result = await record(ops, data, kind='drift', dimension='scope', source_refs=refs)
    document = read(ops, result)['revision']['document']
    assert document['dimension'] == 'scope' and document['assessment_basis'] == 'attributed_management_finding'
    assert document['verification_status'] == 'unassessed'


async def test_source_ids_are_scoped_to_exact_fixed_snapshot_and_requirement_revision(ops):
    data = await prepared(ops)
    other = await prepared(ops)
    for refs in ([{'kind': 'plan_input', 'id': other['confirmed']}], [{'kind': 'requirement', 'id': 'invented'}], [],
                 [{'kind': 'operation', 'id': data['plan']['revision_id']}], fields(data)['source_refs'] * 2):
        with pytest.raises(OperationError, match='INVALID_PARAMS'):
            ops.create(OWNER, **request(ops, data, source_refs=refs))
    with pytest.raises(OperationError, match='INVALID_PARAMS'):
        ops.create(OWNER, **request(ops, data, affected_requirement_ids=['invented']))
    with pytest.raises(OperationError):
        ops.create(OWNER, **request(ops, data, snapshot_id=other['snapshot']['snapshot_id']))


async def test_draft_only_workspace_does_not_supply_accepted_baseline(ops):
    ws = await workspace(ops)
    snap = await snapshot(ops, ws['workspace_id'], who=OWNER)
    data = {'wid': ws['workspace_id'], 'snapshot': snap, 'assumption': 'unknown'}
    with pytest.raises(OperationError, match='PM_ADOPTED_PLAN_REQUIRED'):
        ops.create(OWNER, **request(ops, data, owner_actor=OWNER.actor))


async def test_direction_decision_preserves_alternatives_rejections_and_cannot_adopt_scope(ops):
    data = await prepared(ops)
    proposal = await draft(ops, data['target'], title='A candidate recovery change')
    first = await record(ops, data, kind='change', proposed_revision_id=proposal['revision_id'], alternatives=['Defer until evidence arrives'])
    before = execution_state(ops)
    for who in (CONTRIBUTOR, READER, Principal('external-admin', frozenset(), admin=True)):
        with pytest.raises(OperationError, match='FORBIDDEN'):
            ops.create(who, **mutation(ops, first, 'decide', {'disposition': 'adopted_direction', 'rationale': 'A proposal is not a vote'}))
    await change(ops, first, 'decide', disposition='rejected', rationale='First investigate the uncertainty')
    await change(ops, first, 'decide', disposition='adopted_direction', rationale='Now choose the fixed proposal after discussion')
    view = read(ops, first)['revision']['document']
    assert view['decision']['disposition'] == 'adopted_direction' and view['decision']['recorded_by'] == OWNER.actor
    assert view['adoption'] is None and not view['scope_applied']
    assert view['alternatives'] == ['Defer until evidence arrives']
    assert execution_state(ops) == before
    history = pm.list_history(ops, OWNER, data['wid'], first['record_id'])['revisions']
    assert history[1]['document']['decision']['disposition'] == 'rejected'
    assert history[1]['document']['decision']['rationale'] == 'First investigate the uncertainty'


async def test_link_requires_actual_successful_original_adoption_and_exact_candidate_baseline(ops):
    data = await prepared(ops)
    candidate = await draft(ops, data['target'], title='Fixed next plan')
    first = await record(ops, data, kind='change', proposed_revision_id=candidate['revision_id'])
    await change(ops, first, 'decide', disposition='adopted_direction', rationale='Choose this exact direction')
    ctx = adoption(ops, data['target'], candidate['revision_id'], previous=data['plan']['revision_id'])
    params = {'adoption_operation_id': ctx.operation_id, 'rationale': 'Link the original adoption fact'}
    with pytest.raises(OperationError, match='ADOPTION_MISMATCH'):
        ops.create(OWNER, **mutation(ops, first, 'adoption.link', params))
    adopt(ctx)
    await settle(ops, ctx.op)
    before = execution_state(ops)
    linked = await change(ops, first, 'adoption.link', **params)
    document = read(ops, first)['revision']['document']
    assert document['scope_applied'] and document['adoption']['operation_id'] == ctx.operation_id
    assert document['adoption']['previous_revision_id'] == data['plan']['revision_id']
    assert not document['execution_authorized'] and document['verification_status'] == 'unassessed'
    assert execution_state(ops) == before
    original = copy.deepcopy(document['adoption'])
    next_plan = await draft(ops, data['target'], title='Another future revision')
    adopt(adoption(ops, data['target'], next_plan['revision_id'], previous=candidate['revision_id']))
    assert read(ops, linked)['revision']['document']['adoption'] == original


async def test_revising_proposal_requires_new_direction_decision_and_keeps_old(ops):
    data = await prepared(ops)
    first = await record(ops, data, kind='change')
    await change(ops, first, 'decide', disposition='adopted_direction', rationale='Choose an exploratory direction')
    await change(ops, first, 'revise', **fields(data, title='A refined alternative'))
    assert read(ops, first)['revision']['document']['decision'] is None
    history = pm.list_history(ops, OWNER, data['wid'], first['record_id'])['revisions']
    assert history[1]['document']['decision']['disposition'] == 'adopted_direction'


async def test_source_withdrawal_filters_records_history_operations_events_and_replay(ops, monkeypatch):
    data = await prepared(ops, evidence=True)
    req = request(ops, data)
    operation, _ = ops.create(CONTRIBUTOR, **req)
    result = (await settle(ops, operation))['result']
    assert pm.operation_visible(ops.db, READER, {'operation_id': operation['operation_id']})
    original = pm_workspace.operation_visible
    def integration_hook(db, principal, op):
        return original(db, principal, op) and (not op['action'].startswith('pm.governance.') or pm.operation_visible(db, principal, op))
    monkeypatch.setattr(pm_workspace, 'operation_visible', integration_hook)
    assert ops.get(operation['operation_id'], principal=READER)
    ops.db.execute("UPDATE tasks SET controller_authority='{}' WHERE task_id=?", (data['task']['task_id'],))
    assert not pm.operation_visible(ops.db, READER, {'operation_id': operation['operation_id']})
    event = {'body': {'operation_id': operation['operation_id']}}
    assert not pm.event_visible(ops.db, READER, event)
    assert not pm_access.visible(ops.db, READER, ops.get(operation['operation_id']))
    for call in (lambda: read(ops, result), lambda: pm.list_history(ops, OWNER, data['wid'], result['record_id']),
                 lambda: ops.get(operation['operation_id'], principal=READER), lambda: ops.create(CONTRIBUTOR, **req)):
        with pytest.raises(OperationError):
            call()
    page = pm.list_records(ops, OWNER, data['wid'])
    assert page['records'] == [] and page['coverage']['visibility_filtered'] and page['coverage']['scanned'] == 1


async def test_current_source_withdrawal_does_not_change_original_document_and_membership_is_current(ops):
    data = await prepared(ops, evidence=True)
    result = await record(ops, data)
    original = read(ops, result)
    await member(ops, {'workspace_id': data['wid']}, READER.actor, None)
    for call in (lambda: read(ops, result, READER), lambda: pm.list_records(ops, READER, data['wid'])):
        with pytest.raises(OperationError):
            call()
    assert read(ops, result) == original
    with pytest.raises(OperationError):
        read(ops, result, OUTSIDER)


async def test_duplicate_read_checks_both_new_analysis_and_existing_root_context(ops):
    data = await prepared(ops)
    first = await record(ops, data)
    work = await work_for(ops, data['target'])
    source = task(ops)
    await link(ops, work, 'task', source['task_id'])
    await capture(ops, work)
    data['snapshot'] = await snapshot(ops, data['wid'])
    op, _ = ops.create(CONTRIBUTOR, **request(ops, data, description='A new analysis may quote the newer source'))
    result = (await settle(ops, op))['result']
    assert result['record_id'] == first['record_id']
    ops.db.execute("UPDATE tasks SET controller_authority='{}' WHERE task_id=?", (source['task_id'],))
    assert not pm.operation_visible(ops.db, READER, {'operation_id': op['operation_id']})
    assert read(ops, first)  # Original finding has no withdrawn source.


async def test_pagination_whole_wire_bytes_no_skipping_and_clock_rollback(ops, monkeypatch):
    data = await prepared(ops)
    one = await record(ops, data, title='登入風險', description='說明' * 3000)
    two = await record(ops, data, kind='assumption', title='定價假設', description='資料' * 3000)
    page = pm.list_records(ops, OWNER, data['wid'])
    assert [r['record']['record_id'] for r in page['records']] == [one['record_id'], two['record_id']]
    single = pm.list_records(ops, OWNER, data['wid'], limit=1)
    monkeypatch.setattr(pm, 'MAX_READ_BYTES', pm_sprints._read_size(single) + 100)
    limited = pm.list_records(ops, OWNER, data['wid'])
    assert len(limited['records']) == 1 and limited['coverage']['byte_limited']
    assert pm_sprints._read_size(limited) <= pm.MAX_READ_BYTES
    tail = pm.list_records(ops, OWNER, data['wid'], after=limited['next_cursor'])
    assert tail['records'][0]['record']['record_id'] == two['record_id'] and tail['next_cursor'] is None
    monkeypatch.setattr(pm, 'MAX_READ_BYTES', 200)
    with pytest.raises(OperationError, match='READ_TOO_LARGE'):
        pm.list_records(ops, OWNER, data['wid'])
    with pytest.raises(OperationError, match='READ_TOO_LARGE'):
        read(ops, one)


async def test_history_pages_preserve_whole_revisions_and_exact_cursor(ops, monkeypatch):
    data = await prepared(ops)
    result = await record(ops, data, description='敘述' * 3000)
    for i in range(3):
        await change(ops, result, 'revise', **fields(data, description='敘述' * 3000, title=f'Review {i}'))
    single = pm.list_history(ops, OWNER, data['wid'], result['record_id'], limit=1)
    monkeypatch.setattr(pm, 'MAX_READ_BYTES', pm_sprints._read_size(single) + 100)
    after, revisions = 0, []
    while True:
        page = pm.list_history(ops, OWNER, data['wid'], result['record_id'], after=after)
        assert pm_sprints._read_size(page) <= pm.MAX_READ_BYTES and len(page['revisions']) == 1
        revisions.extend(r['revision_id'] for r in page['revisions'])
        after = page['next_cursor']
        if after is None:
            break
    assert len(set(revisions)) == 4


@pytest.mark.parametrize('kwargs', [{'limit': True}, {'limit': 0}, {'after': -1}, {'after': '0'}, {'kind': []}])
async def test_malformed_paging_is_rejected(ops, kwargs):
    data = await prepared(ops)
    with pytest.raises(OperationError, match='INVALID_PARAMS'):
        pm.list_records(ops, OWNER, data['wid'], **kwargs)


async def test_future_event_or_foreign_operation_cannot_be_linked_as_plan_adoption(ops):
    data = await prepared(ops)
    proposal = await draft(ops, data['target'])
    result = await record(ops, data, kind='change', proposed_revision_id=proposal['revision_id'])
    await change(ops, result, 'decide', disposition='adopted_direction', rationale='Keep this chosen proposal')
    opid = ops.db.execute('SELECT operation_id FROM pm_plan_revisions WHERE revision_id=?', (proposal['revision_id'],)).fetchone()[0]
    with pytest.raises(OperationError, match='ADOPTION_MISMATCH'):
        ops.create(OWNER, **mutation(ops, result, 'adoption.link', {'adoption_operation_id': opid, 'rationale': 'A draft operation is not adoption'}))


async def test_no_source_refresh_or_auto_date_or_quality_from_record_state(ops):
    data = await prepared(ops)
    result = await record(ops, data, status='reported_resolved', response='Someone says testing is done')
    out = read(ops, result)
    assert out['coverage']['source_refresh'] == 'not_performed'
    assert out['coverage']['semantic_verification'] == 'unassessed'
    assert out['revision']['document']['review_at'] is None
    assert out['revision']['document']['verification_status'] == 'unassessed'
    assert 'counts_as_done' not in json.dumps(out)
