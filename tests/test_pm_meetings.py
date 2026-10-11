"""Meeting outputs are sourced management records, never execution or quality gates."""
from __future__ import annotations

import copy
import json
import sqlite3
import uuid
from types import SimpleNamespace

import pytest

from bat_agent_connector import pm_meetings as pm
from bat_agent_connector import pm_snapshots, pm_sprints
from bat_agent_connector.api_auth import Principal
from bat_agent_connector.operations import OpContext, OperationError, OperationService
from bat_agent_connector.task_journal import Journal
from tests.test_pm_discussions import CONTRIBUTOR, OUTSIDER, OWNER, READER, settle, workspace
from tests.test_pm_evidence import capture, link, task
from tests.test_pm_snapshots import snapshot
from tests.test_pm_sprints import (
    ADMIN,
    active,
    change,
    create,
    execution_state,
    prepared,
    revise,
    selections,
)
from tests.test_pm_sprints import ops as sprint_ops_fixture

sprint_ops = sprint_ops_fixture


@pytest.fixture
def ops(sprint_ops):
    version = sprint_ops.db.execute('PRAGMA user_version').fetchone()[0]
    pm_snapshots.install(sprint_ops)
    pm.install(sprint_ops)
    pm.install(sprint_ops)
    assert sprint_ops.db.execute('PRAGMA user_version').fetchone()[0] == version
    return sprint_ops


def request(ops, target, action, **params):
    return {'action': 'pm.meeting.' + action, 'target': target,
            'params': {'rationale': 'An explicit meeting record', **params},
            'preconditions': {'expected_version': pm._meeting(ops.db, target['workspace_id'], target['meeting_id'])['version']},
            'idempotency_key': uuid.uuid4().hex}


async def apply(ops, target, action, *, who=OWNER, **params):
    operation, _ = ops.create(who, **request(ops, target, action, **params))
    return (await settle(ops, operation))['result']


def create_request(ops, target, snap, kind='planning'):
    return {'action': 'pm.meeting.create', 'target': target,
            'params': {'kind': kind, 'title': 'A useful ' + kind, 'snapshot_id': snap['snapshot_id'], 'rationale': 'Discuss a fixed view'},
            'preconditions': {'expected_sprint_version': pm_sprints.get_sprint(ops, OWNER, **target)['sprint']['version']},
            'idempotency_key': uuid.uuid4().hex}


async def meeting(ops, *, kind='planning', selected=True):
    data = await prepared(ops)
    sprint = await active(ops, data) if kind != 'planning' else await create(ops, data)
    if kind == 'planning' and selected:
        await change(ops, sprint, 'select', items=selections(data))
    snap = await snapshot(ops, data['workspace_id'])
    operation, _ = ops.create(CONTRIBUTOR, **create_request(ops, sprint, snap, kind))
    result = (await settle(ops, operation))['result']
    return data, sprint, snap, {'workspace_id': data['workspace_id'], 'meeting_id': result['meeting_id']}


def read(ops, target, who=OWNER, **kwargs):
    return pm.get_meeting(ops, who, **target, **kwargs)


def follow_up(owner=CONTRIBUTOR.actor):
    return {'id': 'check_quality', 'text': 'Review the first repair together', 'owner_actor': owner,
            'expected_effect': 'Fewer repeated review corrections', 'verification': 'Compare the next two review records', 'source_refs': []}


def draft_params(kind='planning', **extra):
    outputs = {'planning': {'why': 'A returning member can recover access', 'delivery_approach': 'One integrated slice',
                           'capacity_discussion': 'Use the bounded capacity already recorded', 'dependency_notes': ['Keep existing authentication behavior']},
               'review': {'goal_review': 'The work claim needs quality evidence',
                          'outcomes': [], 'feedback': ['The first action is clearer'], 'environment_changes': [],
                          'next_steps': ['Keep the alternative in Idea']},
               'retro': {'observations': ['Agree on review criteria before parallel changes'],
                         'previous_improvement_review': 'No previous effect has been verified yet'}}
    return {'output': outputs[kind], 'decisions': [{'id': 'retain_direction', 'text': 'Retain the current direction',
            'rationale': 'The alternative needs further evidence', 'alternatives': ['Explore the alternative later'], 'source_refs': []}],
            'follow_ups': [follow_up()] if kind == 'retro' else [], 'unresolved_questions': ['Quality evidence remains incomplete'], **extra}


async def draft(ops, target, kind='planning', **params):
    return await apply(ops, target, 'draft', who=CONTRIBUTOR, **draft_params(kind, **params))


async def note(ops, target, **params):
    return await apply(ops, target, 'note', who=CONTRIBUTOR, content='We discussed the evidence and retained the open questions.', **params)


async def complete(ops, target, **params):
    revision = read(ops, target)['draft']['revision_id']
    return await apply(ops, target, 'complete', draft_revision_id=revision,
                       discussion_summary='The owner records that the discussion took place; unknowns remain explicit.', **params)


async def test_planning_uses_fixed_why_what_how_and_never_rewrites_commitments_or_execution(ops):
    data, sprint, snap, target = await meeting(ops)
    original = copy.deepcopy(read(ops, target)['context'])
    state = execution_state(ops)
    await draft(ops, target)
    view = read(ops, target, READER)
    assert view['meeting']['status'] == 'draft' and view['completion'] is None
    assert view['context']['document']['snapshot_ref']['snapshot_digest'] == snap['snapshot_digest']
    output = view['draft']['document']['output']
    assert output['what'][0]['binding_id'] == data['bindings'][0]
    assert output['how'][0]['recorded_how'] == selections(data)[0]['how']
    assert not view['draft']['document']['meeting_completed'] and not view['execution_authorized']
    assert view['draft']['document']['decisions'][0]['disposition'] == 'proposal'
    assert view['snapshot']['baseline']['document']['selected_inputs'][1]['status'] == 'agent_assumption'
    await revise(ops, data['workspace_id'], 'dod', plan_revision_id=data['plan'], criteria=[{'id': 'new', 'text': 'A later criterion'}])
    assert read(ops, target)['context'] == original
    assert execution_state(ops) == state
    assert pm_sprints.get_sprint(ops, OWNER, **sprint)['sprint']['status'] == 'planned'


async def test_a_generated_draft_does_not_complete_discussion_and_only_designated_owner_can_record_it(ops):
    _, _, _, target = await meeting(ops)
    saved = await draft(ops, target)
    params = {'draft_revision_id': saved['draft_revision_id'], 'discussion_summary': 'Explicit discussion outcome'}
    with pytest.raises(OperationError, match='DISCUSSION_REQUIRED'):
        ops.create(OWNER, **request(ops, target, 'complete', **params))
    await note(ops, target)
    for who in (CONTRIBUTOR, ADMIN):
        with pytest.raises(OperationError, match='FORBIDDEN'):
            ops.create(who, **request(ops, target, 'complete', **params))
    result = await complete(ops, target, decision_ids=['retain_direction'])
    view = read(ops, target)
    assert result['status'] == 'completed' and view['completion']['document']['meeting_completed']
    assert view['completion']['document']['completion_basis'] == 'explicit_designated_owner_record'
    assert not view['completion']['document']['attendance_verified']
    assert view['completion']['document']['decisions'][0]['disposition'] == 'recorded_discussion_decision'
    assert not view['completion']['document']['decisions'][0]['scope_applied']
    assert view['draft']['document']['decisions'][0]['disposition'] == 'proposal'
    assert view['draft']['document']['meeting_completed'] is False
    assert not view['completion']['document']['counts_as_done']
    for action, values in [('draft', draft_params()), ('note', {'content': 'Rewrite the past'})]:
        with pytest.raises(OperationError, match='PM_MEETING_COMPLETED'):
            ops.create(OWNER, **request(ops, target, action, **values))


async def test_explicit_context_revision_preserves_history_and_requires_new_context_discussion(ops):
    data, sprint, snap, target = await meeting(ops, selected=False)
    first = await draft(ops, target)
    await note(ops, target)
    with pytest.raises(OperationError, match='PLANNING_INCOMPLETE'):
        await complete(ops, target)
    await change(ops, sprint, 'select', items=selections(data))
    with pytest.raises(OperationError, match='CONTEXT_CHANGED'):
        await complete(ops, target)
    req = request(ops, target, 'context.revise', snapshot_id=snap['snapshot_id'])
    req['preconditions']['expected_sprint_version'] = pm_sprints.get_sprint(ops, OWNER, **sprint)['sprint']['version']
    pending, _ = ops.create(CONTRIBUTOR, **req)
    await settle(ops, pending)
    view = read(ops, target)
    assert view['draft'] is None and view['meeting']['status'] == 'draft'
    previous = pm.list_history(ops, OWNER, **target, kind='drafts')['records'][0]
    assert previous['revision_id'] == first['draft_revision_id'] and previous['document']['output']['what'] == []
    await draft(ops, target)
    with pytest.raises(OperationError, match='DISCUSSION_REQUIRED'):
        await complete(ops, target)
    await note(ops, target)
    await complete(ops, target)
    assert len(pm.list_history(ops, OWNER, **target, kind='contexts')['records']) == 2


async def test_review_preserves_claims_feedback_environment_and_unknown_quality(ops):
    data, sprint, _, target = await meeting(ops, kind='review')
    original = execution_state(ops)
    params = draft_params('review')
    params['output']['outcomes'] = [{'binding_id': data['bindings'][0], 'observation': 'The implementer says everything is done'}]
    params['output']['environment_changes'] = ['A compatibility requirement may have changed']
    await apply(ops, target, 'draft', **params)
    await note(ops, target)
    await complete(ops, target)
    view = read(ops, target)
    output = view['draft']['document']['output']
    assert output['outcomes'][0]['source_status'] == 'discussion_claim'
    assert output['outcomes'][0]['dod'] == 'unassessed' and not output['outcomes'][0]['counts_as_done']
    assert output['qualified_increment'] is None
    assert output['feedback'] and output['next_steps'] and output['environment_changes']
    assert view['completion']['document']['decisions'][0]['disposition'] == 'proposal'
    assert execution_state(ops) == original
    assert pm_sprints.get_sprint(ops, OWNER, **sprint)['sprint']['status'] == 'active'


async def test_review_cannot_complete_as_presentation_without_feedback_and_next_direction(ops):
    _, _, _, target = await meeting(ops, kind='review')
    output = draft_params('review')['output']
    output['feedback'] = []
    await draft(ops, target, 'review', output=output)
    await note(ops, target)
    with pytest.raises(OperationError, match='REVIEW_INCOMPLETE'):
        await complete(ops, target)
    output['feedback'] = ['A concrete response from the discussion']
    output['next_steps'] = []
    await draft(ops, target, 'review', output=output)
    with pytest.raises(OperationError, match='REVIEW_INCOMPLETE'):
        await complete(ops, target)


async def test_retro_needs_owned_verifiable_improvement_and_records_bounded_results_without_work(ops):
    data, _, _, target = await meeting(ops, kind='retro')
    await draft(ops, target, 'retro', follow_ups=[])
    await note(ops, target)
    with pytest.raises(OperationError, match='RETRO_INCOMPLETE'):
        await complete(ops, target)
    await draft(ops, target, 'retro')
    original = execution_state(ops)
    await complete(ops, target)
    assigned = read(ops, target)['completion']['document']['follow_ups'][0]
    assert assigned['owner_actor'] == CONTRIBUTOR.actor and assigned['verification'] and assigned['expected_effect']
    assert assigned['status'] == 'open' and not assigned['work_item_created'] and not assigned['scheduler_registered']
    later = await snapshot(ops, data['workspace_id'])
    event = await apply(ops, target, 'follow_up.record', who=CONTRIBUTOR, follow_up_id='check_quality',
                        status='reported_done', result='We tried the new review practice; its effect still needs verification',
                        snapshot_id=later['snapshot_id'], source_refs=[{'kind': 'snapshot', 'id': later['snapshot_id']}])
    record = read(ops, target)['follow_up_events'][0]
    assert record['event_id'] == event['event_id'] and record['actor'] == CONTRIBUTOR.actor
    assert record['document']['snapshot_ref']['snapshot_digest'] == later['snapshot_digest']
    assert record['document']['status'] == 'reported_done' and record['document']['verification_status'] == 'unassessed'
    assert read(ops, target)['completion']['document']['follow_ups'][0] == assigned
    assert execution_state(ops) == original
    for table in ('pm_meeting_completions', 'pm_meeting_follow_up_events'):
        with pytest.raises(sqlite3.IntegrityError, match='immutable'):
            ops.db.execute(f'DELETE FROM {table}')


async def test_retro_can_explicitly_adopt_no_improvement_after_meaningful_reflection(ops):
    _, _, _, target = await meeting(ops, kind='retro')
    output = draft_params('retro')['output']
    output['observations'] = ['The last review practice is still being evaluated; no new intervention is justified']
    output['no_improvement_rationale'] = 'Wait for the original verification interval before adding more changes'
    await draft(ops, target, 'retro', output=output, follow_ups=[])
    await note(ops, target)
    await complete(ops, target)
    view = read(ops, target)
    assert view['completion']['document']['meeting_completed']
    assert view['completion']['document']['follow_ups'] == []
    assert view['draft']['document']['output']['no_improvement_rationale'] == output['no_improvement_rationale']
    assert not view['completion']['document']['execution_authorized']


@pytest.mark.parametrize('change', [
    {'owner_actor': OUTSIDER.actor}, {'verification': ''}, {'expected_effect': ''}, {'id': 'Bad ID'}, {'auto_dispatch': True},
])
async def test_retro_improvements_keep_real_owners_verification_and_no_hidden_execution_fields(ops, change):
    _, _, _, target = await meeting(ops, kind='retro')
    item = {**follow_up(), **change}
    with pytest.raises(OperationError, match='INVALID_PARAMS'):
        ops.create(CONTRIBUTOR, **request(ops, target, 'draft', **draft_params('retro', follow_ups=[item])))


async def test_draft_decision_and_followup_limits_do_not_silently_truncate_or_promote_proposals(ops):
    _, _, _, target = await meeting(ops, kind='retro')
    for extra in ({'follow_ups': [dict(follow_up(), id=f'check{i}') for i in range(6)]},
                  {'decisions': [dict(draft_params()['decisions'][0], disposition='adopted')]},
                  {'source_refs': [{'kind': 'evidence', 'id': 'pmev_' + 'a' * 32}]}):
        with pytest.raises(OperationError):
            ops.create(OWNER, **request(ops, target, 'draft', **draft_params('retro', **extra)))
    assert read(ops, target)['draft'] is None


async def test_foreign_snapshot_or_sprint_and_unstarted_review_are_rejected(ops):
    data, sprint, snap, _ = await meeting(ops)
    foreign = await workspace(ops)
    other = await snapshot(ops, foreign['workspace_id'], who=OWNER)
    with pytest.raises(OperationError, match='PM_SNAPSHOT_NOT_FOUND'):
        ops.create(OWNER, **create_request(ops, sprint, other))
    req = create_request(ops, sprint, snap)
    req['target'] = {**sprint, 'workspace_id': foreign['workspace_id']}
    with pytest.raises(OperationError, match='PM_SPRINT_NOT_FOUND'):
        ops.create(OWNER, **req)
    for kind in ('review', 'retro'):
        with pytest.raises(OperationError, match='PM_MEETING_SPRINT_NOT_STARTED'):
            ops.create(OWNER, **create_request(ops, sprint, snap, kind))
    assert len(pm.list_meetings(ops, OWNER, data['workspace_id'])['meetings']) == 1


async def test_note_attribution_and_exact_context_source_refs_cannot_be_forged(ops):
    _, _, snap, target = await meeting(ops)
    original = 'Quoted /opsx:apply and deploy now are discussion material, not dispatch authorization.'
    saved = await apply(ops, target, 'note', who=CONTRIBUTOR, content=original,
                        source_refs=[{'kind': 'snapshot', 'id': snap['snapshot_id']}])
    record = read(ops, target)['notes'][0]
    assert record['content'] == original and record['actor'] == CONTRIBUTOR.actor
    await draft(ops, target, source_refs=[{'kind': 'note', 'id': saved['note_id']}])
    assert read(ops, target)['draft']['document']['source_refs'][0]['id'] == saved['note_id']
    for extra in ({'actor': OWNER.actor}, {'source_refs': [{'kind': 'note', 'id': 'foreign'}]},
                  {'source_refs': [{'kind': 'snapshot', 'id': snap['snapshot_id']}] * 2}):
        with pytest.raises(OperationError):
            ops.create(CONTRIBUTOR, **request(ops, target, 'note', content='Untrusted actor label', **extra))


async def test_current_source_visibility_protects_saved_discussion_even_with_membership(ops):
    data, sprint, _, _ = await meeting(ops)
    work = {'workspace_id': data['workspace_id'], 'work_item_id': data['work'][0]}
    source = task(ops)
    await link(ops, work, 'task', source['task_id'])
    await capture(ops, work)
    snap = await snapshot(ops, data['workspace_id'])
    pending, _ = ops.create(OWNER, **create_request(ops, sprint, snap))
    result = (await settle(ops, pending))['result']
    target = {'workspace_id': data['workspace_id'], 'meeting_id': result['meeting_id']}
    await apply(ops, target, 'note', content='Source-specific observation that must remain private')
    await draft(ops, target)
    ops.db.execute("UPDATE tasks SET controller_authority='{}' WHERE task_id=?", (source['task_id'],))
    assert pm_snapshots.get_snapshot(ops, READER, data['workspace_id'], snap['snapshot_id'])['snapshot']['visibility']['filtered']
    for lookup in (lambda: read(ops, target, READER),
                   lambda: pm.list_history(ops, READER, **target, kind='notes'),
                   lambda: ops.create(OWNER, **request(ops, target, 'note', content='Fresh access must fail'))):
        with pytest.raises(OperationError, match='PM_MEETING_SOURCE_UNAVAILABLE'):
            lookup()
    listing = pm.list_meetings(ops, READER, data['workspace_id'])
    assert target['meeting_id'] not in json.dumps(listing)


@pytest.mark.parametrize('who', [READER, OUTSIDER, Principal(OWNER.actor, frozenset({'observe'})), Principal(OWNER.actor, frozenset({'manage'}))])
async def test_writes_need_current_membership_and_both_scopes(ops, who):
    _, _, _, target = await meeting(ops)
    with pytest.raises(OperationError):
        ops.create(who, **request(ops, target, 'note', content='A note'))


async def test_cas_and_permission_are_rechecked_at_effect_with_no_partial_discussion(ops):
    _, _, _, target = await meeting(ops)
    first, _ = ops.create(CONTRIBUTOR, **request(ops, target, 'note', content='First note'))
    second, _ = ops.create(CONTRIBUTOR, **request(ops, target, 'note', content='Stale note'))
    await settle(ops, first)
    assert (await settle(ops, second, success=False))['error_code'] == 'VERSION_CONFLICT'
    assert len(read(ops, target)['notes']) == 1
    queued, _ = ops.create(CONTRIBUTOR, **request(ops, target, 'draft', **draft_params()))
    ops.db.execute('DELETE FROM pm_workspace_members WHERE workspace_id=? AND actor=?', (target['workspace_id'], CONTRIBUTOR.actor))
    assert (await settle(ops, queued, success=False))['error_code'] == 'NOT_FOUND'
    assert read(ops, target)['draft'] is None


async def test_owner_revocation_blocks_completion_but_original_committed_receipt_still_recovers(ops):
    _, _, _, target = await meeting(ops)
    saved = await draft(ops, target)
    await note(ops, target)
    intent = request(ops, target, 'complete', draft_revision_id=saved['draft_revision_id'], discussion_summary='The discussion concluded')
    queued, _ = ops.create(OWNER, **intent)
    result = await pm._run(OpContext(ops, queued))
    ops.db.execute('DELETE FROM pm_workspace_members WHERE workspace_id=? AND actor=?', (target['workspace_id'], OWNER.actor))
    assert await pm._run(OpContext(ops, queued)) == result
    with pytest.raises(OperationError, match='NOT_FOUND'):
        ops.create(OWNER, **intent)
    with pytest.raises(OperationError, match='NOT_FOUND'):
        read(ops, target)
    assert read(ops, target, READER)['completion']['document']['meeting_completed']


async def test_queued_completion_rechecks_designated_owner_role_before_committing(ops):
    _, _, _, target = await meeting(ops)
    saved = await draft(ops, target)
    await note(ops, target)
    intent = request(ops, target, 'complete', draft_revision_id=saved['draft_revision_id'], discussion_summary='A proposed final summary')
    pending, _ = ops.create(OWNER, **intent)
    ops.db.execute("UPDATE pm_workspace_members SET role='contributor' WHERE workspace_id=? AND actor=?", (target['workspace_id'], OWNER.actor))
    failed = await settle(ops, pending, success=False)
    assert failed['status'] == 'failed' and failed['error_code'] == 'FORBIDDEN'
    view = read(ops, target)
    assert view['completion'] is None and view['meeting']['status'] == 'draft'


async def test_follow_up_results_recheck_their_own_later_fixed_snapshot_visibility(ops):
    data, _, _, target = await meeting(ops, kind='retro')
    await draft(ops, target, 'retro')
    await note(ops, target)
    await complete(ops, target)
    work = {'workspace_id': data['workspace_id'], 'work_item_id': data['work'][0]}
    source = task(ops)
    await link(ops, work, 'task', source['task_id'])
    await capture(ops, work)
    later = await snapshot(ops, data['workspace_id'])
    await apply(ops, target, 'follow_up.record', follow_up_id='check_quality', status='blocked',
                result='A later source is restricted', snapshot_id=later['snapshot_id'])
    assert read(ops, target)['follow_up_events'][0]['document']['status'] == 'blocked'
    ops.db.execute("UPDATE tasks SET controller_authority='{}' WHERE task_id=?", (source['task_id'],))
    for lookup in (lambda: read(ops, target, READER), lambda: pm.list_history(ops, READER, **target, kind='follow_ups')):
        with pytest.raises(OperationError, match='PM_MEETING_SOURCE_UNAVAILABLE'):
            lookup()


@pytest.mark.parametrize('action, params, table', [
    ('draft', draft_params(), 'pm_meeting_drafts'),
    ('complete', {'discussion_summary': 'Actual discussion is recorded'}, 'pm_meeting_completions'),
])
async def test_draft_and_completion_rollback_leave_the_previous_meeting_head_unchanged(ops, monkeypatch, action, params, table):
    _, _, _, target = await meeting(ops)
    saved = await draft(ops, target)
    await note(ops, target)
    values = copy.deepcopy(params)
    if action == 'complete':
        values['draft_revision_id'] = saved['draft_revision_id']
    before = read(ops, target)
    count = ops.db.execute(f'SELECT count(*) FROM {table}').fetchone()[0]
    pending, _ = ops.create(OWNER, **request(ops, target, action, **values))
    with monkeypatch.context() as broken:
        broken.setattr(ops.journal, 'api_event', lambda *a, **k: (_ for _ in ()).throw(RuntimeError('rollback after head write')))
        with pytest.raises(RuntimeError, match='rollback after head write'):
            await pm._run(OpContext(ops, pending))
    assert read(ops, target) == before
    assert ops.db.execute(f'SELECT count(*) FROM {table}').fetchone()[0] == count
    result = await pm._run(OpContext(ops, pending))
    assert await pm._run(OpContext(ops, pending)) == result
    assert ops.db.execute(f'SELECT count(*) FROM {table}').fetchone()[0] == count + 1


async def test_lost_reply_replay_restart_and_sqlite_rollback_preserve_one_meeting(ops, tmp_path, monkeypatch):
    data = await prepared(ops)
    sprint = await create(ops, data)
    snap = await snapshot(ops, data['workspace_id'])
    intent = create_request(ops, sprint, snap)
    queued, _ = ops.create(OWNER, **intent)
    with monkeypatch.context() as broken:
        broken.setattr(ops.journal, 'api_event', lambda *a, **k: (_ for _ in ()).throw(RuntimeError('rollback local meeting')))
        with pytest.raises(RuntimeError, match='rollback local meeting'):
            await pm._run(OpContext(ops, queued))
    assert ops.db.execute('SELECT count(*) FROM pm_meetings').fetchone()[0] == 0
    assert ops.db.execute('SELECT count(*) FROM pm_meeting_contexts').fetchone()[0] == 0
    result = await pm._run(OpContext(ops, queued))
    assert (await settle(ops, queued))['result'] == result
    repeated, created = ops.create(OWNER, **intent)
    assert not created and repeated['result'] == result
    reopened = Journal(tmp_path / 'sprints.db')
    try:
        recovered = OperationService(reopened)
        pm.install(recovered)
        repeated, created = recovered.create(OWNER, **intent)
        assert not created and repeated['result'] == result
        assert len(pm.list_meetings(recovered, OWNER, data['workspace_id'])['meetings']) == 1
    finally:
        reopened.close()


async def test_history_is_immutable_paged_and_read_only_with_clock_rollback(ops, monkeypatch):
    data, _, _, target = await meeting(ops)
    await note(ops, target)
    first = read(ops, target)['notes'][0]
    monkeypatch.setattr(pm, 'time', SimpleNamespace(time=lambda: first['created_at'] - 100))
    await note(ops, target)
    await draft(ops, target)
    await draft(ops, target, unresolved_questions=['A later explicit question'])
    original = ops.db.total_changes
    page = read(ops, target, history_limit=1)
    next_page = pm.list_history(ops, OWNER, **target, kind='notes', after=page['history_next_cursors']['notes'], limit=1)
    assert next_page['records'][0]['sequence'] > first['sequence']
    assert next_page['records'][0]['created_at'] < first['created_at']
    assert next_page['next_cursor'] is None
    assert len(pm.list_history(ops, READER, **target, kind='drafts')['records']) == 2
    assert len(pm.list_meetings(ops, READER, data['workspace_id'])['meetings']) == 1
    assert ops.db.total_changes == original
    for table in ('pm_meetings', 'pm_meeting_contexts', 'pm_meeting_notes', 'pm_meeting_drafts'):
        with pytest.raises(sqlite3.IntegrityError, match='immutable'):
            ops.db.execute(f'DELETE FROM {table}')


@pytest.mark.parametrize('pre', [{}, {'expected_version': True}, {'expected_version': -1}, {'expected_version': '1'}])
async def test_exact_cas_is_required(ops, pre):
    _, _, _, target = await meeting(ops)
    req = request(ops, target, 'note', content='No ambiguous version')
    req['preconditions'] = pre
    with pytest.raises(OperationError, match='VERSION_CONFLICT'):
        ops.create(OWNER, **req)
