"""Real membership regressions across original acceptance and maintenance Review.

Git and configured verifier subprocesses run in pytest's disk directory. BAT and
GitHub are offline providers; the tiny account fixture is test data, not a product
authentication implementation. A prerequisite's semantic receipt is not a code
transfer: each child starts from its explicitly published original baseline.
"""
from __future__ import annotations

import copy
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from bat_agent_connector import (
    api_auth,
    pm_discussions,
    pm_lifecycle_gates,
    pm_plans,
    pm_snapshots,
    work_items,
)
from bat_agent_connector import pm_maintenance_review as maintenance_review
from bat_agent_connector import pm_requirement_acceptance as acceptance
from bat_agent_connector import pm_work_batches as batches
from bat_agent_connector.operations import OperationError
from bat_agent_connector.task_verifier import ObservedVerifier, VerificationSettings
from tests import test_pm_execution_verification as producer
from tests import test_pm_maintenance_acceptance as repair
from tests import test_pm_maintenance_repairs as repair_setup
from tests import test_pm_maintenance_review as meetings
from tests import test_pm_package_handoff as handoffs
from tests import test_pm_quality_reviews as reviews
from tests import test_pm_requirement_acceptance as accepted
from tests import test_pm_work_batches as batch
from tests import test_pm_work_packages as packages
from tests.test_checkpoints import MANUAL, bat_writes, git, snapshot
from tests.test_integration import commit
from tests.test_pm_plan_operations import act, adopt, draft
from tests.test_pm_plan_operations import target as thread_target

base_world = repair.base_world
released_world = repair.world
pm, ready, fixed = packages.pm, packages.ready, handoffs.fixed
repair_ready, original_repair = repair.repair_ready, repair.original_repair
original_authority = repair.original_authority
OWNER = repair.OWNER

RECOVERY_BROKEN = 'def reset_password(token, password):\n    return False\n'
RECOVERY_FIXED = '''import login
remaining_token = 'original-reset-token'
def reset_password(token, password):
    global remaining_token
    if remaining_token is None or token != remaining_token or not password:
        return False
    login.password = password
    remaining_token = None
    return True
'''
RESETTABLE_LOGIN = '''password = 'correct'
def login(candidate):
    return candidate == password
'''
MEMBER_REGRESSION = '''import sys
import login
assert login.login('correct') is True, 'valid credentials must allow access'
assert login.login('incorrect') is False, 'wrong password must be denied'
assert login.login('') is False, 'empty password must be denied'
if sys.argv[1] == 'recovery':
    from recovery import reset_password
    assert reset_password('invalid-token', 'replacement') is False
    assert login.login('correct') is True, 'invalid token cannot change credentials'
    assert reset_password('original-reset-token', '') is False
    assert reset_password('original-reset-token', 'replacement') is True
    assert login.login('correct') is False, 'old password must be revoked'
    assert login.login('replacement') is True, 'new password must allow access'
    assert reset_password('original-reset-token', 'second-replacement') is False
    assert login.login('replacement') is True, 'used token cannot reset twice'
print('PASS: fixed original member ' + sys.argv[1] + ' acceptance')
'''


@pytest.fixture
async def world(released_world):
    w = released_world
    # This is fixture creation before the human-checkout preservation boundary.
    for name, content in {'recovery.py': RECOVERY_BROKEN, 'member_regression.py': MEMBER_REGRESSION}.items():
        (w['human'] / name).write_text(content)
    git(w['human'], 'add', 'recovery.py', 'member_regression.py')
    git(w['human'], 'commit', '-qm', 'Fixed member login and password recovery regression scenarios')
    git(w['human'], 'push', str(w['remote']), 'main')
    w['state']['sha'] = w['sha'] = git(w['human'], 'rev-parse', 'HEAD')
    return w


@pytest.fixture
async def member_batch(ready):
    w, ops = ready, ready['d'].ops
    scope = await act(w, 'pm.input.record', {'slot': 'scope', 'value': 'Members can recover a forgotten password',
        'status': 'user_confirmed', 'source_ref': {'kind': 'statement', 'ref': 'Explicit member recovery direction'}})
    criterion = await act(w, 'pm.input.record', {'slot': 'quality',
        'value': 'Invalid reset tokens leave credentials unchanged; a valid token replaces the password exactly once',
        'status': 'user_confirmed', 'source_ref': {'kind': 'statement', 'ref': 'Explicit password recovery acceptance'}})
    original = w['requirements'][0]
    discussion = pm_discussions.get_thread(ops, OWNER, **thread_target(w))
    await act(w, 'pm.thread.plan.revise', {'selected_input_ids': [original['source_input_id'],
        *original['acceptance_input_ids'], scope['input_id'], criterion['input_id']],
        'rationale': 'Plan login first and recovery only after its original acceptance'},
        pre={'expected_version': discussion['thread']['version'], 'expected_draft_id': discussion['draft']['draft_id']})
    w['requirements'] = [original, {'id': 'member.recovery', 'source_input_id': scope['input_id'],
        'acceptance_input_ids': [criterion['input_id']], 'depends_on': ['member.login']}]
    w['revision'] = await draft(w, requirements=w['requirements'])
    assert (await adopt(w, w['revision']))['status'] == 'succeeded'
    await packages.associate(w)
    w['package'] = packages.read(w, await packages.create(w))
    handoffs.dispatch_runner(w)
    batches.install(ops)
    api_auth.issue(ops.db, OWNER.actor, sorted(OWNER.scopes))
    project = work_items._get_item(ops.db, w['target']['work_item_id'])['project_id']
    item = (await packages.action(w, 'work_item.create', {'project_id': project},
        {'title': 'Forgotten password recovery', 'goal': 'Restore member access',
         'acceptance': 'Reject invalid and consumed reset tokens; replace the old password once'}))['work_item_id']
    target = {'workspace_id': w['workspace_id'], 'work_item_id': item}
    await packages.action(w, 'pm.work.bind', target, {'rationale': 'Recovery requires accepted original login',
        'plan_revision_id': w['revision']['revision_id'], 'requirement_ids': ['member.recovery']},
        {'expected_binding_id': None, 'expected_work_version': work_items._get_item(ops.db, item)['version']})
    second = {**w, 'target': target, 'params': copy.deepcopy(w['params'])}
    second['params'].update(title='Password recovery engineering package', requirements=['member.recovery'])
    second['params']['design']['text'] = 'Validate the selected one-use reset token before replacing credentials.'
    second['params']['tasks'][0].update(key='identity.recovery', requirement_ids=['member.recovery'],
        description='Implement one-use password recovery and its negative cases')
    second['params']['verification'][0].update(key='verify.recovery', requirement_id='member.recovery',
        acceptance_input_ids=[criterion['input_id']], method='Exercise invalid, valid and consumed reset tokens',
        expected_evidence='Fixed candidate login and recovery regression results')
    second['package'] = packages.read(second, await packages.create(second))
    return w, second


async def configure_verifier(w, child, scenario):
    w['child'] = child
    argv = (sys.executable, '-B', 'member_regression.py', scenario)
    w['d'].adapter.verifier = ObservedVerifier(VerificationSettings(
        commands={child['params']['project_id']: argv},
        artifact_dir=str(w['human'].parent / ('member-' + scenario + '-logs'))))
    producer.verifier.install(w['d'].ops)
    w['config'] = await producer.verifier.configuration(w['d'].ops, OWNER, w['workspace_id'], child['operation_id'])
    assert w['config']['available'] and w['config']['candidate_reason'] is None
    return {'kind': 'fixed_revision_verification', 'profile': 'observed_runner_v1',
        'command_digest': w['config']['command_digest']}


async def record_quality(w, verification, condition):
    w['candidate'], w['tree'] = w['config']['candidate_commit'], w['config']['tree_hash']
    w['proof'] = verification['result']['reference']
    request = reviews.selection(w)
    request['params']['rationale'] = 'Assess every original recovery criterion and the login regression at this fixed candidate'
    for criterion in request['params']['criteria']:
        criterion['condition'] = condition
    created = await reviews.execute(w, request)
    review = reviews.read(w, {'review_id': created['result']['review_id']})
    assert not review['qualified']
    assessment = reviews.assessment(w, review)
    for conclusion in assessment['params']['conclusions']:
        conclusion['rationale'] = 'Original fixed tests cover rejected tokens, one-use reset and old/new password behavior.'
    await reviews.execute(w, assessment, actor=reviews.REVIEWER)
    review = reviews.read(w, review)
    assert review['qualified']
    return {'review_id': review['review_id'], 'candidate_commit': w['candidate'],
        **{key: review['assessment'][key] for key in ('operation_id', 'version', 'document_digest')}}


async def test_login_acceptance_releases_recovery_only_with_its_exact_original_review(member_batch, mock):
    w, second = member_batch
    ops = w['d'].ops
    human = snapshot(w['human'])
    plan = dict(pm_plans.head(ops.db, w['workspace_id']))
    task_count = ops.db.execute('SELECT COUNT(*) FROM tasks').fetchone()[0]
    conditions = [acceptance.freeze_condition(item['package'], item['params']['requirements']) for item in (w, second)]
    first = await batch.selected(w, 'first')
    first['result_condition'] = conditions[0]
    last = await batch.selected(second, 'second', [{'entry_key': 'first', 'condition': conditions[0]}])
    last['result_condition'] = conditions[1]
    parent = await batch.advance(w, ops.create(OWNER, **batch.envelope(w, [first, last], max_parallel=1))[0])
    entries = batch.view(w, parent)['entries']
    assert entries[0]['operation_id'] and entries[1]['operation_id'] is None
    child = ops.get(entries[0]['operation_id'])
    await configure_verifier(w, child, 'login')
    failed = await producer.execute(w)
    assert failed['result']['status'] == 'blocked'
    wt = Path(child['result']['worktree_path'])
    git(wt, 'config', 'user.name', 'fixture')
    git(wt, 'config', 'user.email', 'fixture@example.invalid')
    commit(wt, 'login.py', repair.FIXED, 'Deny invalid member credentials')
    await configure_verifier(w, child, 'login')
    passed = await producer.execute(w)
    assert passed['result']['status'] == 'passed'
    parent = await batch.advance(w, parent)
    assert batch.view(w, parent)['entries'][1]['operation_id'] is None
    assert mock.channels().count('claude:start-session') == 1
    review, selected, reference = await accepted.qualify(w)
    assert review['qualified'] and selected == conditions[0]
    # Even an actual accepted review cannot stand in for another fixed candidate.
    with pytest.raises(OperationError):
        acceptance.evaluate(ops, OWNER, w['workspace_id'], child, selected,
            {**reference, 'candidate_commit': failed['params']['candidate_commit']})
    assert batch.view(w, parent)['entries'][1]['operation_id'] is None
    await reviews.execute(w, batch.proof_request(w, parent, reference, condition=selected))
    parent = await batch.advance(w, parent)
    entries = batch.view(w, parent)['entries']
    assert entries[0]['state'] == 'requirements_accepted' and entries[1]['operation_id']
    second_child = ops.get(entries[1]['operation_id'])
    assert second_child['status'] == 'succeeded'
    stored = json.loads(ops.db.execute('SELECT request FROM pm_work_batch_children WHERE operation_id=?',
        (second_child['operation_id'],)).fetchone()[0])
    assert stored['_acceptance']['first']['reference'] == reference
    assert stored['params']['pm_requirement_quality_refs']['member.login']['reference'] == reference
    assert second_child['params']['source_sha'] == child['params']['source_sha'] == w['sha']
    assert mock.channels().count('claude:start-session') == 2
    # B implements its own fixed package in its own managed checkout. No Git
    # objects from A's unpublished candidate are transferred or silently adopted.
    second['dod'] = w['dod']
    await configure_verifier(second, second_child, 'recovery')
    failed_recovery = await producer.execute(second)
    assert failed_recovery['result']['status'] == 'blocked'
    wt = Path(second_child['result']['worktree_path'])
    git(wt, 'config', 'user.name', 'fixture')
    git(wt, 'config', 'user.email', 'fixture@example.invalid')
    commit(wt, 'login.py', RESETTABLE_LOGIN, 'Implement original login contract for resettable fixture account')
    await configure_verifier(second, second_child, 'recovery')
    still_broken = await producer.execute(second)
    assert still_broken['result']['status'] == 'blocked'  # Login now passes; reset itself still fails.
    commit(wt, 'recovery.py', RECOVERY_FIXED, 'Consume valid reset token once and replace credentials')
    verifier_condition = await configure_verifier(second, second_child, 'recovery')
    verified = await producer.execute(second)
    assert verified['result']['status'] == 'passed'
    second_reference = await record_quality(second, verified, verifier_condition)
    await reviews.execute(w, batch.proof_request(w, parent, second_reference, key='second', version=2, condition=conditions[1]))
    parent = ops.get(parent['operation_id'])
    assert parent['status'] == 'succeeded' and parent['result']['outcome'] == 'dispatch_settled'
    # Dispatch settles separately from later explicit result assessments. Read
    # each immutable proof rather than upgrading the original dispatch receipt.
    results = batch.view(w, parent)['entries']
    for entry, reference in zip(results, (reference, second_reference)):
        proof = entry['verification_evidence'][0]
        assert proof['status'] == 'passed' and proof['semantic_acceptance'] == 'accepted'
        assert proof['reference'] == reference
    assert pm_plans.head(ops.db, w['workspace_id']) == plan
    assert snapshot(w['human']) == human and git(w['remote'], 'rev-parse', 'refs/heads/main') == w['sha']
    assert ops.db.execute('SELECT COUNT(*) FROM tasks').fetchone()[0] == task_count
    assert not [call for call in bat_writes(mock) if call['params'].get('sessionId') == MANUAL]


async def test_real_login_repair_is_preserved_in_registered_discussion_and_review_snapshot(original_repair, mock, monkeypatch):
    w, ops = original_repair, original_repair['d'].ops
    publication = await repair.publish(w)
    assert publication['status'] == 'succeeded'
    original = repair.read(w)
    assert original['outcome'] == 'published_for_review' and original['capacity_held'] and not original['repair_fixed']
    assert w['baseline_verification']['result']['status'] == 'blocked' and w['verification']['result']['status'] == 'passed'
    maintenance_review.install(ops)
    now = w['clock'][0] + 2
    monkeypatch.setattr(maintenance_review, 'time', SimpleNamespace(time=lambda: now))
    base = repair_setup.shared_request(w, maintenance_review.PREFIX + 'prepare')
    request = {**base, 'params': {'title': '會員登入修復檢討 / Member login repair Review',
        'period_start': original['created_at'] - 60, 'period_end': now},
        'preconditions': {**base['preconditions'], 'expected_policy_id': base['params']['policy_id'],
            'expected_plan_version': pm_plans.head(ops.db, w['workspace_id'])['version'],
            'expected_review_revision': maintenance_review.head(ops.db, w['workspace_id'])['revision']}}
    plan = dict(pm_plans.head(ops.db, w['workspace_id']))
    lifecycle = dict(pm_lifecycle_gates.head(ops.db, w['workspace_id']))
    writes, provider_calls = list(bat_writes(mock)), list(w['gh'].requests)
    prepared = await repair_setup.execute(ops, request, who=OWNER)
    rid = prepared['result']['review_id']
    initial = maintenance_review.get_review(ops, OWNER, w['workspace_id'], rid)
    report = initial['snapshot']['maintenance']['report']
    assert initial['completion'] is None and report['meeting_completed'] is False
    observed = next(item for item in report['repairs'] if item['repair_id'] == original['repair_id'])
    assert observed['outcome'] == 'published_for_review' and observed['capacity_held'] and not observed['repair_fixed']
    children = {row['slot']: row for row in observed['children']}
    assert children['child']['operation_id'] == w['child']['operation_id']
    assert children['publication_child']['operation_id'] == publication['operation_id']
    assert report['changes']['whole_period_no_events_proven'] is False
    sid = observed['source_id']
    with pytest.raises(OperationError):
        ops.create(OWNER, **meetings.mutation(ops, w, rid, 'complete', meetings.completion([])))
    note = await repair_setup.execute(ops, meetings.mutation(ops, w, rid, 'note', {
        'content': '原登入回歸已重現並通過修復驗證；PR 等待審閱，尚無發布或執行環境終止證明。',
        'source_refs': [sid], 'rationale': 'Discuss original verification, PR and remaining runtime gap'}), who=reviews.REVIEWER)
    candidate = await repair_setup.execute(ops, meetings.mutation(ops, w, rid, 'candidate.record', {
        'title': 'Expand member recovery regression coverage', 'problem': 'This repair proves only the original login behavior',
        'goal_relation': 'Preserve member account access', 'expected_effect': 'Detect recovery regressions at their fixed candidate',
        'options': ['Review a separately scoped recovery package'], 'hypotheses': ['Reset-token negative cases may expose another gap'],
        'source_refs': [sid], 'rationale': 'Keep the sourced improvement as a proposal for later review'}), who=OWNER)
    completed = await repair_setup.execute(ops, meetings.mutation(ops, w, rid, 'complete', meetings.completion(
        [note['result']['event_id']], candidate_ids=[candidate['result']['event_id']],
        discussion_summary='Reviewed original passing login proof and draft PR; delivery and runtime completion remain unproven.',
        follow_ups=[{'id': 'review-pr', 'text': 'Review the original draft PR and outstanding runtime proof',
            'owner_actor': reviews.REVIEWER.actor, 'expected_effect': 'Resolve the explicit delivery and capacity uncertainty',
            'verification': 'Attach original provider and runtime receipts', 'source_refs': [sid]}])), who=OWNER)
    final = maintenance_review.get_review(ops, OWNER, w['workspace_id'], rid)
    assert completed['result']['meeting_completed'] and final['review']['version'] == 4
    assert final['snapshot'] == initial['snapshot']
    assert final['snapshot']['snapshot_id'] == prepared['result']['snapshot_id']
    assert final['snapshot']['maintenance']['report']['meeting_completed'] is False
    assert final['completion']['document']['acknowledged_by'] == OWNER.actor
    assert final['completion']['document']['scope_adopted'] is False
    assert final['events'][0]['actor'] == reviews.REVIEWER.actor
    assert final['events'][1]['document']['source_refs'] == [sid]
    assert pm_snapshots.get_snapshot(ops, OWNER, w['workspace_id'], prepared['result']['snapshot_id'])['snapshot'] == initial['snapshot']
    replay, fresh = ops.create(OWNER, **request)
    assert not fresh and replay['operation_id'] == prepared['operation_id'] and replay['result'] == prepared['result']
    assert pm_plans.head(ops.db, w['workspace_id']) == plan and dict(pm_lifecycle_gates.head(ops.db, w['workspace_id'])) == lifecycle
    assert repair.read(w)['capacity_held'] and not repair.read(w)['repair_fixed']
    assert list(bat_writes(mock)) == writes and w['gh'].requests == provider_calls
    repair.preserved(w, mock)
    # The immutable report and its discussion retain all original repair sources.
    handoffs.revoke(w)
    with pytest.raises(OperationError):
        maintenance_review.get_review(ops, OWNER, w['workspace_id'], rid)
    with pytest.raises(OperationError):
        pm_snapshots.get_snapshot(ops, OWNER, w['workspace_id'], prepared['result']['snapshot_id'])
