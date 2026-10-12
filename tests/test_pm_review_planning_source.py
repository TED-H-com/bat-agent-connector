"""Saved Review candidates enter Idea explicitly and retain original privacy."""
from __future__ import annotations

import copy
import uuid

import pytest

from bat_agent_connector import (
    pm_access,
    pm_conversation_proposals,
    pm_discussions,
    pm_follow_up,
    pm_lifecycle_gates,
    pm_plans,
    pm_source_lineage,
    pm_workspace,
)
from bat_agent_connector import pm_review_candidate_sources as sources
from bat_agent_connector.operations import OpContext, OperationError
from tests import test_pm_maintenance_review as reviews
from tests.test_pm_evidence import CONTRIBUTOR, OWNER, READER
from tests.test_pm_release_baselines import execute, remove_repo

base_ops, release_ops, workload_ops, github_ops, ops = (
    reviews.base_ops, reviews.release_ops, reviews.workload_ops, reviews.github_ops, reviews.ops)


async def prepared(ops, monkeypatch):
    pm_source_lineage.schema(ops.journal)
    data = await reviews.gh.prepared(ops, monkeypatch)
    _, report = await reviews.prepare(ops, data)
    rid = report['review']['review_id']
    candidate = await execute(ops, reviews.mutation(ops, data, rid, 'candidate.record', {
        'title': 'Investigate a bounded recovery improvement', 'problem': 'Recovery coverage is still unproven. ' * 70,
        'goal_relation': 'Preserve the selected membership release', 'expected_effect': 'Make the missing evidence visible',
        'options': ['Investigate', 'Defer'], 'hypotheses': ['A narrower check may help'],
        'source_refs': [report['snapshot']['maintenance']['report']['sources'][0]['source_id']],
        'rationale': 'Discussion proposal only, not accepted scope'}))
    event = reviews.review.get_review(ops, OWNER, data['workspace_id'], rid)['events'][0]
    assert event['operation_id'] == candidate['operation_id']
    workspace = pm_workspace.get_workspace(ops.db, OWNER, data['workspace_id'])
    created = await execute(ops, {'action': 'pm.thread.create', 'target': {'workspace_id': data['workspace_id']},
        'params': {'title': 'Consider recovery improvements'}, 'preconditions': {'expected_version': workspace['version']},
        'idempotency_key': uuid.uuid4().hex})
    data.update(thread_id=created['result']['thread_id'], event=event)
    return data


def request(ops, data, action='pm.input.record', params=None):
    return {'action': action, 'target': {key: data[key] for key in ('workspace_id', 'thread_id')},
        'params': params or {'slot': 'quality', 'value': data['event']['document']['title'], 'status': 'unresolved',
            'source_ref': data['event']['planning_source']},
        'preconditions': {'expected_version': pm_discussions._thread(ops.db, data['workspace_id'], data['thread_id'])['version']},
        'idempotency_key': uuid.uuid4().hex}


async def test_original_candidate_confirmation_planning_and_followup_preserve_source(ops, monkeypatch):
    data = await prepared(ops, monkeypatch)
    wid, tid = data['workspace_id'], data['thread_id']
    original = copy.deepcopy(data['event'])
    before = pm_plans.head(ops.db, wid).copy()
    imported = await execute(ops, request(ops, data), who=CONTRIBUTOR)
    thread = pm_discussions.get_thread(ops, OWNER, wid, tid)
    value = thread['inputs'][0]
    assert thread['thread']['mode'] == 'idea' and value['status'] == 'unresolved'
    assert value['source_candidate']['document'] == original['document']
    assert value['source_candidate']['kind'] == 'candidate.record'
    assert value['source_candidate']['document']['disposition'] == 'proposal'
    assert pm_plans.head(ops.db, wid) == before
    context = pm_discussions.context(ops, OWNER, wid, tid, max_chars=64000)
    assert context['inputs_by_status']['unresolved'][0]['source_candidate']['document'] == original['document']
    proposal_context = pm_conversation_proposals._context(ops, OWNER, {'workspace_id': wid, 'thread_id': tid},
        {'purpose': 'Consider only this fixed candidate', 'max_chars': 64000}, {'expected_version': thread['thread']['version']}, before)
    assert proposal_context['inputs'][0]['source_candidate']['document'] == original['document']
    with pytest.raises(OperationError) as bounded:
        pm_discussions.context(ops, OWNER, wid, tid, max_chars=2000)
    assert bounded.value.status == 413
    assert pm_source_lineage.roots(ops.db, 'operation', imported['operation_id']) == {imported['operation_id']}
    confirmed = await execute(ops, request(ops, data, 'pm.input.confirm', {
        'input_id': value['input_id'], 'rationale': 'Owner explicitly selects investigation, without adopting work'}))
    planned = await execute(ops, request(ops, data, 'pm.thread.plan', {
        'selected_input_ids': [confirmed['result']['input_id']], 'rationale': 'Prepare one selected Planning direction'}))
    head = pm_plans.head(ops.db, wid)
    draft = await execute(ops, {'action': 'pm.plan.draft', 'target': {'workspace_id': wid, 'thread_id': tid},
        'params': {'previous_revision_id': head['latest_revision_id']}, 'preconditions': {
            'expected_version': pm_discussions._thread(ops.db, wid, tid)['version'], 'expected_plan_version': head['version']},
        'idempotency_key': uuid.uuid4().hex})
    plan = pm_plans.get_plan(ops, OWNER, wid, draft['result']['revision_id'])['plan']
    assert plan['document']['selected_inputs'][0]['source_candidate']['document'] == original['document']
    pm_follow_up.install(ops)
    snapshot_id = reviews.review.get_review(ops, OWNER, wid, original['review_id'])['snapshot']['snapshot_id']
    intake = await execute(ops, {'action': 'pm.follow_up.prepare', 'target': {'workspace_id': wid},
        'params': {'snapshot_id': snapshot_id, 'thread_id': tid}, 'preconditions': {
            'expected_plan_version': pm_plans.head(ops.db, wid)['version'], 'expected_version': 0,
            'expected_thread_version': pm_discussions._thread(ops.db, wid, tid)['version']},
        'idempotency_key': uuid.uuid4().hex})
    saved = pm_follow_up._row(ops.db, wid, intake['result']['intake_id'])
    assert any(ref['kind'] == 'input_operation' for ref in pm_follow_up._original_access(ops.db, saved))
    assert ops.db.execute('SELECT COUNT(*) FROM tasks').fetchone()[0] == 0
    await remove_repo(ops, data['project_id'])
    for op in (imported, confirmed, planned, draft, intake):
        assert not pm_access.visible(ops.db, READER, op)
    for read in (lambda: pm_discussions.get_thread(ops, READER, wid, tid),
                 lambda: pm_plans.get_plan(ops, READER, wid, draft['result']['revision_id']),
                 lambda: pm_follow_up.get_intake(ops, READER, wid, intake['result']['intake_id'])):
        with pytest.raises(OperationError) as error:
            read()
        assert error.value.status == 404
    assert reviews.review._event(ops.db, wid, original['review_id'], original['event_id'])['document'] == original['document']


@pytest.mark.parametrize('change', ['digest', 'operation', 'noncandidate', 'edited_quote', 'confirmed', 'foreign_workspace', 'planning', 'reader'])
async def test_exact_candidate_and_original_idea_authority_are_required(ops, monkeypatch, change):
    data = await prepared(ops, monkeypatch)
    body = copy.deepcopy(request(ops, data))
    who = OWNER
    if change == 'digest':
        body['params']['source_ref']['document_digest'] = '0' * 64
    elif change == 'operation':
        body['params']['source_ref']['operation_id'] = 'op_' + '0' * 32
    elif change == 'noncandidate':
        note = await reviews.note(ops, data, data['event']['review_id'])
        event = reviews.review._event(ops.db, data['workspace_id'], data['event']['review_id'], note['result']['event_id'])
        body['params']['source_ref'] = sources.reference(event)
    elif change == 'edited_quote':
        body['params']['value'] = 'A different and already accepted requirement'
    elif change == 'confirmed':
        body['params']['status'] = 'user_confirmed'
    elif change == 'foreign_workspace':
        created = await execute(ops, {'action': 'pm.workspace.create', 'params': {'name': 'Other workspace'}, 'idempotency_key': uuid.uuid4().hex})
        wid = created['result']['workspace']['workspace_id']
        thread = await execute(ops, {'action': 'pm.thread.create', 'target': {'workspace_id': wid},
            'params': {'title': 'Unrelated Idea'}, 'preconditions': {'expected_version': 1}, 'idempotency_key': uuid.uuid4().hex})
        body['target'] = {'workspace_id': wid, 'thread_id': thread['result']['thread_id']}
    elif change == 'planning':
        imported = await execute(ops, request(ops, data))
        await execute(ops, request(ops, data, 'pm.thread.plan', {'selected_input_ids': [imported['result']['input_id']], 'rationale': 'Explicit Planning'}))
        body['preconditions'] = request(ops, data)['preconditions']
    else:
        who = READER
    with pytest.raises(OperationError):
        ops.create(who, **body)


async def test_withdrawal_before_local_commit_does_not_create_input(ops, monkeypatch):
    data = await prepared(ops, monkeypatch)
    op = ops.create(OWNER, **request(ops, data))[0]
    await remove_repo(ops, data['project_id'])
    with pytest.raises(OperationError):
        await pm_discussions._run(OpContext(ops, op))
    assert not ops.db.execute('SELECT 1 FROM pm_inputs WHERE operation_id=?', (op['operation_id'],)).fetchone()
    assert not pm_access.visible(ops.db, OWNER, op)


async def test_original_local_receipt_precedes_new_phase_version_and_source_checks(ops, monkeypatch):
    data = await prepared(ops, monkeypatch)
    body = request(ops, data)
    op = await execute(ops, body)
    await execute(ops, request(ops, data, 'pm.thread.plan', {'selected_input_ids': [op['result']['input_id']], 'rationale': 'Move to Planning explicitly'}))
    recovered, created = ops.create(OWNER, **body)
    assert not created and recovered['result'] == op['result']
    await remove_repo(ops, data['project_id'])
    assert await pm_discussions._run(OpContext(ops, op)) == op['result']
    assert ops.db.execute('SELECT COUNT(*) FROM pm_inputs WHERE operation_id=?', (op['operation_id'],)).fetchone()[0] == 1
    assert not pm_access.visible(ops.db, OWNER, op)


async def test_pause_still_allows_unconfirmed_record_but_current_writer_is_required(ops, monkeypatch):
    from tests.test_pm_lifecycle import decide
    data = await prepared(ops, monkeypatch)
    await decide(ops, data['workspace_id'], 'pause')
    before = dict(pm_lifecycle_gates.head(ops.db, data['workspace_id']))
    imported = await execute(ops, request(ops, data), who=CONTRIBUTOR)
    assert imported['result']['mode'] == 'idea'
    assert dict(pm_lifecycle_gates.head(ops.db, data['workspace_id'])) == before
    op = ops.create(CONTRIBUTOR, **request(ops, data))[0]
    workspace = pm_workspace.get_workspace(ops.db, OWNER, data['workspace_id'])
    await execute(ops, {'action': 'pm.workspace.member.set', 'target': {'workspace_id': data['workspace_id']},
        'params': {'actor': CONTRIBUTOR.actor, 'role': 'reader'}, 'preconditions': {'expected_version': workspace['version']},
        'idempotency_key': uuid.uuid4().hex})
    with pytest.raises(OperationError):
        await pm_discussions._run(OpContext(ops, op))
    assert not ops.db.execute('SELECT 1 FROM pm_inputs WHERE operation_id=?', (op['operation_id'],)).fetchone()
