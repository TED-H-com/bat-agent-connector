"""Fixed Follow-up context, explicit answers, bounded agenda and source authority."""
from __future__ import annotations

import json
import sqlite3
import uuid
from types import SimpleNamespace

import pytest

from bat_agent_connector import (
    pm_discussions,
    pm_governance,
    pm_meetings,
    pm_plans,
    pm_profiles,
    pm_snapshots,
    pm_sprints,
)
from bat_agent_connector import pm_follow_up as pm
from bat_agent_connector.api_auth import Principal
from bat_agent_connector.operations import OpContext, OperationError
from tests.test_pm_discussions import (
    CONTRIBUTOR,
    OUTSIDER,
    OWNER,
    READER,
    act,
    member,
    record,
    settle,
    workspace,
)
from tests.test_pm_evidence import bind_project, bind_work, capture, item, link, project, task
from tests.test_pm_governance import change as governance_change
from tests.test_pm_governance import execution_state
from tests.test_pm_governance import prepared as governance_prepared
from tests.test_pm_governance import record as governance_record
from tests.test_pm_meetings import apply as meeting_apply
from tests.test_pm_meetings import complete as meeting_complete
from tests.test_pm_meetings import draft as meeting_draft
from tests.test_pm_meetings import meeting
from tests.test_pm_meetings import note as meeting_note
from tests.test_pm_meetings import read as meeting_read
from tests.test_pm_plans import adopt, adoption, draft
from tests.test_pm_snapshots import accepted, snapshot
from tests.test_pm_snapshots import ops as snapshot_ops_fixture
from tests.test_pm_sprints import NOW

snapshot_ops = snapshot_ops_fixture


@pytest.fixture
def ops(snapshot_ops, monkeypatch):
    pm_sprints.install(snapshot_ops)
    pm_meetings.install(snapshot_ops)
    pm_governance.install(snapshot_ops)
    version = snapshot_ops.db.execute('PRAGMA user_version').fetchone()[0]
    pm.install(snapshot_ops)
    pm.install(snapshot_ops)
    monkeypatch.setattr(pm_sprints, 'time', SimpleNamespace(time=lambda: NOW))
    assert snapshot_ops.db.execute('PRAGMA user_version').fetchone()[0] == version
    return snapshot_ops


async def prepared(ops):
    target, plan, confirmed, assumption = await accepted(ops)
    snap = await snapshot(ops, target['workspace_id'])
    return {'target': target, 'wid': target['workspace_id'], 'plan': plan, 'confirmed': confirmed,
            'assumption': assumption, 'snapshot': snap}


async def work_for(ops, target):
    project_id = await project(ops, name='Explicit source ' + uuid.uuid4().hex)
    work_id = await item(ops, project_id)
    await bind_project(ops, target['workspace_id'], project_id)
    work = {'workspace_id': target['workspace_id'], 'work_item_id': work_id}
    await bind_work(ops, work)
    return work


def request(ops, data, **params):
    params = {'snapshot_id': data['snapshot']['snapshot_id'], **params}
    pre = {'expected_version': pm.head(ops.db, data['wid'])['version'],
           'expected_plan_version': pm_plans.head(ops.db, data['wid'])['version']}
    if 'thread_id' in params:
        pre['expected_thread_version'] = pm_discussions._thread(ops.db, data['wid'], params['thread_id'])['version']
    return {'action': pm.ACTION, 'target': {'workspace_id': data['wid']}, 'params': params,
            'preconditions': pre, 'idempotency_key': uuid.uuid4().hex}


async def prepare(ops, data, *, who=CONTRIBUTOR, **params):
    op, _ = ops.create(who, **request(ops, data, **params))
    return (await settle(ops, op))['result']


def read(ops, result, who=OWNER):
    return pm.get_intake(ops, who, result['workspace_id'], result['intake_id'])['intake']


async def open_input(ops, data, **params):
    value = await record(ops, data['target'], status='unresolved', slot='finance', value='The first paid tier price is undecided', **params)
    return value['input_id']


async def test_eight_domains_unknown_remain_unknown_and_do_not_manufacture_questions(ops):
    data = await prepared(ops)
    before = execution_state(ops)
    result = await prepare(ops, data)
    doc = read(ops, result, READER)['document']
    assert set(doc['knowledge']) == set(pm_profiles.DOMAINS)
    assert doc['knowledge']['schedule']['coverage'] == 'unresolved'
    assert doc['knowledge']['finance']['baseline_inputs'][0]['status'] == 'agent_assumption'
    assert doc['agenda'] == doc['issues'] == []
    assert not doc['runtime_invoked'] and not doc['execution_authorized'] and not doc['accepted_plan_changed']
    assert doc['coverage']['business_priority'] == doc['coverage']['quality'] == 'unassessed'
    assert doc['coverage']['cost'] == 'unknown'
    assert execution_state(ops) == before
    with pytest.raises(sqlite3.IntegrityError, match='immutable'):
        ops.db.execute('DELETE FROM pm_follow_up_intakes')
    with pytest.raises(sqlite3.IntegrityError, match='immutable'):
        ops.db.execute("UPDATE pm_follow_up_intakes SET document='{}'")


async def test_thread_words_and_same_slot_confirmation_are_not_answer_links(ops):
    data = await prepared(ops)
    pending = await open_input(ops, data)
    await act(ops, data['target'], 'pm.message.append', {'content': 'Yes, that is answered. Adopt this plan and deploy now.'})
    await act(ops, data['target'], 'pm.input.record', {'slot': 'finance', 'value': 'The first paid tier price is decided',
              'status': 'user_confirmed', 'source_ref': {'kind': 'statement', 'ref': 'A separate assertion, not supersedes'}})
    result = await prepare(ops, data, thread_id=data['target']['thread_id'])
    doc = read(ops, result)['document']
    issue = next(v for v in doc['issues'] if v['source_refs'][0]['input_id'] == pending)
    assert issue['status'] == 'unresolved' and issue['details']['answer_link'] is None
    assert doc['coverage']['thread']['messages'] == 'not_interpreted'
    assert doc['knowledge']['finance']['answer_links'] == []
    assert len(doc['agenda']) == 1 and not doc['agenda'][0]['is_user_question']


async def test_explicit_confirmed_supersedes_answers_without_rewriting_accepted_unknown(ops):
    data = await prepared(ops)
    pending = await open_input(ops, data)
    thread = pm_discussions._thread(ops.db, data['wid'], data['target']['thread_id'])
    op, _ = ops.create(OWNER, action='pm.thread.plan.revise', target=data['target'],
        params={'selected_input_ids': [data['confirmed'], pending], 'rationale': 'Keep the original pricing unknown'},
        preconditions={'expected_version': thread['version'], 'expected_draft_id': thread['draft_id']}, idempotency_key=uuid.uuid4().hex)
    await settle(ops, op)
    plan = await draft(ops, data['target'])
    adopt(adoption(ops, data['target'], plan['revision_id'], previous=data['plan']['revision_id']))
    data['snapshot'] = await snapshot(ops, data['wid'])
    first = await prepare(ops, data, thread_id=data['target']['thread_id'])
    original = read(ops, first)
    answer = await act(ops, data['target'], 'pm.input.confirm', {'input_id': pending, 'rationale': 'Owner explicitly confirms this recorded answer'})
    newer = await prepare(ops, data, thread_id=data['target']['thread_id'], previous_intake_id=first['intake_id'])
    doc = read(ops, newer)['document']
    assert doc['knowledge']['finance']['baseline_inputs'][0]['status'] == 'unresolved'
    assert doc['knowledge']['finance']['supplemental_inputs'][0]['status'] == 'user_confirmed'
    assert doc['knowledge']['finance']['answer_links'][0]['answer_input_id'] == answer['input_id']
    assert not doc['knowledge']['finance']['answer_links'][0]['accepted_plan_changed']
    assert doc['issues'][0]['issue_id'] == original['document']['issues'][0]['issue_id']
    assert doc['issues'][0]['status'] == 'answer_recorded' and not doc['agenda']
    assert read(ops, first) == original
    assert pm_plans.head(ops.db, data['wid'])['accepted_revision_id'] == plan['revision_id']


async def test_repeated_unresolved_event_is_one_issue_not_new_reminder_and_removed_is_not_resolved(ops):
    data = await prepared(ops)
    await open_input(ops, data)
    first = await prepare(ops, data, thread_id=data['target']['thread_id'])
    second = await prepare(ops, data, thread_id=data['target']['thread_id'], previous_intake_id=first['intake_id'])
    one, two = read(ops, first)['document'], read(ops, second)['document']
    assert one['issues'] == two['issues']
    assert two['changes']['added'] == two['changes']['changed'] == []
    assert two['changes']['unchanged'] == [one['issues'][0]['issue_id']]
    third = await prepare(ops, data, previous_intake_id=second['intake_id'])
    doc = read(ops, third)['document']
    assert doc['changes']['no_longer_selected'] == [one['issues'][0]['issue_id']]
    assert 'resolved' not in doc['changes'] and not doc['agenda']


async def test_zero_to_three_agenda_uses_explicit_reasons_not_business_priority(ops, monkeypatch):
    data = await governance_prepared(ops, evidence=True)
    monkeypatch.setattr(pm, 'time', SimpleNamespace(time=lambda: 1_900_000_000))
    first = await governance_record(ops, data, review_at='2026-10-11T01:00:00Z')
    for i in range(5):
        await record(ops, data['target'], status='conflicting' if i == 0 else 'unresolved', slot='risk', value=f'Recorded question {i}')
    result = await prepare(ops, data, thread_id=data['target']['thread_id'], governance_revision_ids=[first['revision_id']])
    doc = read(ops, result)['document']
    assert len(doc['agenda']) == 3 and doc['coverage']['agenda_candidates'] > 3
    assert [v['ordering_basis'] for v in doc['agenda']] == ['explicit_review_at_reached', 'observed_central_attention_state', 'explicit_conflicting_input']
    assert all(v['business_priority'] == 'unassessed' for v in doc['issues'])
    assert all(not v['is_user_question'] for v in doc['agenda'])


async def test_multiple_captured_states_of_one_execution_do_not_make_duplicate_current_claims(ops):
    data = await prepared(ops)
    source = task(ops)
    first_work = await work_for(ops, data['target'])
    await link(ops, first_work, 'task', source['task_id'])
    ops.db.execute("UPDATE tasks SET state='needs_ted' WHERE task_id=?", (source['task_id'],))
    await capture(ops, first_work)
    second_work = await work_for(ops, data['target'])
    await link(ops, second_work, 'task', source['task_id'])
    ops.db.execute("UPDATE tasks SET state='done' WHERE task_id=?", (source['task_id'],))
    await capture(ops, second_work)
    data['snapshot'] = await snapshot(ops, data['wid'])
    result = await prepare(ops, data)
    issues = [v for v in read(ops, result)['document']['issues'] if v['kind'] == 'evidence']
    assert len(issues) == 1 and issues[0]['status'] == 'multiple_captured_states'
    assert {v['status'] for v in issues[0]['details']['observations']} == {'needs_ted', 'done'}
    assert issues[0]['details']['current_state'] == 'not_checked'


async def test_new_snapshot_time_alone_is_not_a_changed_original_execution_issue(ops):
    data = await governance_prepared(ops, evidence=True)
    first = await prepare(ops, data)
    data['snapshot'] = await snapshot(ops, data['wid'])
    second = await prepare(ops, data, previous_intake_id=first['intake_id'])
    doc = read(ops, second)['document']
    assert doc['changes']['unchanged'] and not doc['changes']['changed'] and not doc['changes']['added']


async def test_fixed_governance_revision_and_original_decision_never_follow_current_head(ops):
    data = await prepared(ops)
    proposal = await draft(ops, data['target'], title='Alternative scope')
    original = await governance_record(ops, data, kind='change', proposed_revision_id=proposal['revision_id'])
    first = await prepare(ops, data, governance_revision_ids=[original['revision_id']])
    frozen = read(ops, first)
    decided = await governance_change(ops, original, 'decide', disposition='adopted_direction', rationale='Choose this direction; implementation remains separate')
    next_result = await prepare(ops, data, governance_revision_ids=[decided['revision_id']], previous_intake_id=first['intake_id'])
    doc = read(ops, next_result)['document']
    issue = doc['issues'][0]
    assert issue['issue_id'] == frozen['document']['issues'][0]['issue_id']
    assert issue['status'] == 'direction_recorded' and issue['details']['answer_link']['disposition'] == 'adopted_direction'
    assert issue['details']['adoption'] is None and not issue['details']['accepted_plan_changed']
    assert pm_plans.head(ops.db, data['wid'])['accepted_revision_id'] == data['plan']['revision_id']
    assert read(ops, first) == frozen
    with pytest.raises(OperationError, match='one explicit revision'):
        ops.create(OWNER, **request(ops, data, governance_revision_ids=[original['revision_id'], decided['revision_id']]))


async def test_different_root_causes_sharing_source_stay_distinct_follow_up_issues(ops):
    data = await prepared(ops)
    first = await governance_record(ops, data)
    second = await governance_record(ops, data, root_cause_key='another-real-cause',
        disambiguation={'record_ids': [first['record_id']], 'rationale': 'Same assumption, separately accountable resource issue'})
    result = await prepare(ops, data, governance_revision_ids=[first['revision_id'], second['revision_id']])
    assert len({v['issue_id'] for v in read(ops, result)['document']['issues']}) == 2


async def completed_meeting(ops, *, original_evidence=False):
    data, sprint, snap, target = await meeting(ops, kind='retro')
    source = None
    if original_evidence:
        work = await work_for(ops, data['discussion'])
        source = task(ops)
        await link(ops, work, 'task', source['task_id'])
        await capture(ops, work)
        private_snapshot = await snapshot(ops, data['workspace_id'])
        operation, _ = ops.create(CONTRIBUTOR, action='pm.meeting.context.revise', target=target,
            params={'snapshot_id': private_snapshot['snapshot_id'], 'rationale': 'Review this fixed source'},
            preconditions={'expected_version': pm_meetings._meeting(ops.db, data['workspace_id'], target['meeting_id'])['version'],
                           'expected_sprint_version': pm_sprints._sprint(ops.db, data['workspace_id'], sprint['sprint_id'])['version']},
            idempotency_key=uuid.uuid4().hex)
        await settle(ops, operation)
    await meeting_draft(ops, target, 'retro')
    await meeting_note(ops, target)
    await meeting_complete(ops, target)
    view = meeting_read(ops, target)
    return {'wid': data['workspace_id'], 'target': data['discussion'], 'snapshot': snap, 'task': source}, target, view['completion']['completion_id']


async def test_meeting_reported_done_is_unverified_fixed_result_not_completion_or_new_report(ops):
    data, target, completion_id = await completed_meeting(ops)
    first = await meeting_apply(ops, target, 'follow_up.record', follow_up_id='check_quality', status='reported_done', result='The reviewer says it is all fixed')
    ref = {'completion_id': completion_id, 'follow_up_id': 'check_quality', 'result_event_id': first['event_id']}
    result = await prepare(ops, data, meeting_follow_up_refs=[ref])
    frozen = read(ops, result)
    doc = frozen['document']
    assert doc['issues'][0]['status'] == 'reported_done' and doc['issues'][0]['verification_status'] == 'unassessed'
    assert doc['agenda'][0]['ordering_basis'] == 'reported_done_verification_unassessed'
    await meeting_apply(ops, target, 'follow_up.record', follow_up_id='check_quality', status='blocked', result='Another problem needs investigation')
    assert read(ops, result) == frozen
    original_only = await prepare(ops, data, meeting_follow_up_refs=[{'completion_id': completion_id, 'follow_up_id': 'check_quality'}])
    assert read(ops, original_only)['document']['issues'][0]['status'] == 'open'
    assert not read(ops, original_only)['document']['agenda']


@pytest.mark.parametrize('withdraw', ['original', 'result'])
async def test_meeting_completion_original_and_result_snapshot_both_rechecked(ops, withdraw):
    data, target, completion_id = await completed_meeting(ops, original_evidence=withdraw == 'original')
    # Original meeting data is one fixed, currently readable source. Add a distinct
    # later task so only the result snapshot references the withdrawn authority.
    work = await work_for(ops, data['target'])
    source = task(ops)
    await link(ops, work, 'task', source['task_id'])
    await capture(ops, work)
    later = await snapshot(ops, data['wid'])
    reported = await meeting_apply(ops, target, 'follow_up.record', follow_up_id='check_quality', status='blocked',
        result='Review the selected source', snapshot_id=(later if withdraw == 'result' else data['snapshot'])['snapshot_id'])
    ref = {'completion_id': completion_id, 'follow_up_id': 'check_quality', 'result_event_id': reported['event_id']}
    result = await prepare(ops, data, meeting_follow_up_refs=[ref])
    assert read(ops, result)
    withdrawn_source = source if withdraw == 'result' else data['task']
    ops.db.execute("UPDATE tasks SET controller_authority='{}' WHERE task_id=?", (withdrawn_source['task_id'],))
    assert not pm_snapshots.get_snapshot(ops, OWNER, data['wid'], data['snapshot']['snapshot_id'])['snapshot']['visibility']['filtered']
    with pytest.raises(OperationError, match='SOURCE_UNAVAILABLE'):
        read(ops, result)
    assert not pm.operation_visible(ops.db, OWNER, {'operation_id': read_operation_id(ops, result)})


def read_operation_id(ops, result):
    return ops.db.execute('SELECT operation_id FROM pm_follow_up_intakes WHERE intake_id=?', (result['intake_id'],)).fetchone()[0]


async def test_governance_original_source_and_previous_comparison_are_not_shared_by_author(ops):
    data = await prepared(ops)
    clean_snapshot = data['snapshot']
    work = await work_for(ops, data['target'])
    source = task(ops)
    await link(ops, work, 'task', source['task_id'])
    await capture(ops, work)
    data['snapshot'] = await snapshot(ops, data['wid'])
    finding = await governance_record(ops, data)
    data['snapshot'] = clean_snapshot
    first = await prepare(ops, data, governance_revision_ids=[finding['revision_id']])
    # A later packet only cites the previous comparison. Its source authority
    # remains transitive, without recursively following live heads.
    second = await prepare(ops, data, previous_intake_id=first['intake_id'])
    ops.db.execute("UPDATE tasks SET controller_authority='{}' WHERE task_id=?", (source['task_id'],))
    for result in (first, second):
        with pytest.raises(OperationError):
            read(ops, result)
        opid = read_operation_id(ops, result)
        assert not pm.operation_visible(ops.db, OWNER, {'operation_id': opid})
        assert not pm.event_visible(ops.db, OWNER, {'body': {'operation_id': opid}})
    page = pm.list_intakes(ops, OWNER, data['wid'], limit=1)
    assert page['intakes'] == [] and page['next_cursor'] is not None and page['coverage']['visibility_filtered']
    page = pm.list_intakes(ops, OWNER, data['wid'], after=page['next_cursor'])
    assert not page['intakes'] and page['next_cursor'] is None


async def test_lost_reply_original_key_returns_original_packet_after_new_context(ops):
    data = await prepared(ops)
    req = request(ops, data, thread_id=data['target']['thread_id'])
    op, _ = ops.create(CONTRIBUTOR, **req)
    result = (await settle(ops, op))['result']
    frozen = read(ops, result)
    await open_input(ops, data)
    await draft(ops, data['target'], title='A different candidate')
    replay, created = ops.create(CONTRIBUTOR, **req)
    assert not created and replay['operation_id'] == op['operation_id'] and replay['result'] == result
    assert read(ops, result) == frozen
    assert len(pm.list_intakes(ops, READER, data['wid'])['intakes']) == 1


async def test_head_cas_serializes_concurrent_preparation_and_rejects_changed_thread(ops):
    data = await prepared(ops)
    req = request(ops, data)
    one, _ = ops.create(CONTRIBUTOR, **req)
    two, _ = ops.create(CONTRIBUTOR, **{**req, 'idempotency_key': uuid.uuid4().hex})
    await settle(ops, one)
    assert ops.get(two['operation_id'])['error_code'] == 'VERSION_CONFLICT'
    stale = request(ops, data, thread_id=data['target']['thread_id'])
    await open_input(ops, data)
    with pytest.raises(OperationError, match='VERSION_CONFLICT'):
        ops.create(OWNER, **stale)


async def test_membership_is_checked_before_effect_and_recovery_keeps_original_receipt(ops):
    data = await prepared(ops)
    op, _ = ops.create(CONTRIBUTOR, **request(ops, data))
    # Remove only this task's explicit membership before the pending worker runs.
    ops.db.execute('DELETE FROM pm_workspace_members WHERE workspace_id=? AND actor=?', (data['wid'], CONTRIBUTOR.actor))
    failed = await settle(ops, op, success=False)
    assert failed['status'] == 'failed' and pm.head(ops.db, data['wid'])['version'] == 0
    op, _ = ops.create(OWNER, **request(ops, data))
    context = OpContext(ops, op)
    original = await pm._run(context)
    await settle(ops, op)
    await member(ops, {'workspace_id': data['wid']}, CONTRIBUTOR.actor, 'contributor')
    await open_input(ops, data)
    assert await pm._run(OpContext(ops, ops.get(op['operation_id']))) == original
    assert pm.head(ops.db, data['wid'])['version'] == 1


@pytest.mark.parametrize('who', [OUTSIDER, READER, Principal(OWNER.actor, frozenset({'manage'}))])
async def test_prepare_requires_current_writer_observe_and_manage(ops, who):
    data = await prepared(ops)
    with pytest.raises(OperationError):
        ops.create(who, **request(ops, data))


async def test_reads_revoke_current_membership_without_changing_saved_packet(ops):
    data = await prepared(ops)
    result = await prepare(ops, data)
    original = read(ops, result)
    await member(ops, {'workspace_id': data['wid']}, READER.actor, None)
    for call in (lambda: read(ops, result, READER), lambda: pm.list_intakes(ops, OUTSIDER, data['wid']),
                 lambda: pm.latest_intake(ops, READER, data['wid'])):
        with pytest.raises(OperationError):
            call()
    assert read(ops, result) == original


async def test_selected_foreign_ids_and_malformed_requests_are_rejected(ops):
    data, other = await prepared(ops), await prepared(ops)
    finding = await governance_record(ops, other)
    for params in ({'snapshot_id': other['snapshot']['snapshot_id']}, {'thread_id': other['target']['thread_id']},
                   {'governance_revision_ids': [finding['revision_id']]}, {'governance_revision_ids': 'not-a-list'},
                   {'meeting_follow_up_refs': [None]}, {'previous_intake_id': []}, {'thread_id': None}, {'run': True}):
        req = request(ops, data)
        req['params'].update(params)
        if 'thread_id' in params:
            req['preconditions']['expected_thread_version'] = 0
        with pytest.raises(OperationError):
            ops.create(OWNER, **req)
    for pre in ({}, {'expected_version': True, 'expected_plan_version': 0}, {'expected_version': 0, 'expected_plan_version': 1, 'extra': 1}):
        req = request(ops, data)
        req['preconditions'] = pre
        with pytest.raises(OperationError):
            ops.create(OWNER, **req)


async def test_draft_without_accepted_plan_is_not_follow_up_commitment(ops):
    ws = await workspace(ops)
    snap = await snapshot(ops, ws['workspace_id'], who=OWNER)
    with pytest.raises(OperationError, match='PM_ADOPTED_PLAN_REQUIRED'):
        ops.create(OWNER, **request(ops, {'wid': ws['workspace_id'], 'snapshot': snap}))


async def test_read_list_is_metadata_only_and_latest_uses_insertion_not_wallclock(ops, monkeypatch):
    data = await prepared(ops)
    assert pm.latest_intake(ops, OWNER, data['wid'])['intake'] is None
    monkeypatch.setattr(pm, 'time', SimpleNamespace(time=lambda: 5000))
    first = await prepare(ops, data)
    monkeypatch.setattr(pm, 'time', SimpleNamespace(time=lambda: 1000))
    second = await prepare(ops, data)
    latest = pm.latest_intake(ops, OWNER, data['wid'])
    assert latest['intake']['intake_id'] == second['intake_id'] and latest['head_version'] == 2
    page = pm.list_intakes(ops, OWNER, data['wid'], limit=1)
    assert page['intakes'][0]['intake_id'] == first['intake_id'] and 'document' not in page['intakes'][0]
    rest = pm.list_intakes(ops, OWNER, data['wid'], after=page['next_cursor'])
    assert rest['intakes'][0]['intake_id'] == second['intake_id'] and rest['next_cursor'] is None
    for kwargs in ({'after': True}, {'limit': 0}, {'limit': 101}, {'after': -1}):
        with pytest.raises(OperationError):
            pm.list_intakes(ops, OWNER, data['wid'], **kwargs)


async def test_whole_wire_utf8_limit_rejects_atomic_effect_without_partial_packet(ops, monkeypatch):
    data = await prepared(ops)
    finding = await governance_record(ops, data, description='中文內容' * 1000)
    first = await prepare(ops, data, governance_revision_ids=[finding['revision_id']])
    wire = pm.get_intake(ops, OWNER, data['wid'], first['intake_id'])
    encoded = len(json.dumps(wire, ensure_ascii=False, allow_nan=False).encode('utf-8'))
    assert encoded > len(json.dumps(wire, ensure_ascii=False, allow_nan=False))
    assert encoded == pm_sprints._read_size(wire)
    monkeypatch.setattr(pm, 'MAX_READ_BYTES', encoded - 1)
    op, _ = ops.create(OWNER, **request(ops, data, governance_revision_ids=[finding['revision_id']]))
    result = await settle(ops, op, success=False)
    assert result['status'] == 'failed' and result['error_code'] == 'PM_FOLLOW_UP_TOO_LARGE'
    assert pm.head(ops.db, data['wid'])['version'] == 1
    with pytest.raises(OperationError, match='TOO_LARGE'):
        pm.get_intake(ops, OWNER, data['wid'], first['intake_id'])


async def test_incomplete_thread_scan_is_explicit_without_synthetic_unasked_questions(ops, monkeypatch):
    data = await prepared(ops)
    for i in range(4):
        await record(ops, data['target'], slot='schedule', value=f'Candidate {i}')
    monkeypatch.setattr(pm, 'MAX_INPUTS', 2)
    result = await prepare(ops, data, thread_id=data['target']['thread_id'])
    doc = read(ops, result)['document']
    assert doc['coverage']['thread']['truncated'] and doc['coverage']['thread']['returned'] == 2
    assert doc['agenda'] == []
    assert doc['coverage']['unselected_sources'] == 'not_scanned'
