"""Continue the original joined ledger through policy-owned maintenance.

Only the synthetic providers and their original repository evolve here. Product
actions, source gates, ownership, verifier and delivery engines remain real.
"""
from __future__ import annotations

import copy
import re
import sys
import time

from bat_agent_connector import pm_execution_verification, pm_plans, repository_sync
from bat_agent_connector import pm_github_observation as github
from bat_agent_connector import pm_lifecycle_gates as lifecycle
from bat_agent_connector import pm_maintenance_checks as checks
from bat_agent_connector import pm_maintenance_policy as policies
from bat_agent_connector import pm_maintenance_repair_delivery as delivery
from bat_agent_connector import pm_maintenance_repair_rules as rules
from bat_agent_connector import pm_maintenance_repair_runtime as runtime
from bat_agent_connector import pm_maintenance_repairs as repairs
from bat_agent_connector import pm_release_baselines as baselines
from bat_agent_connector import pm_required_ci as required_ci
from bat_agent_connector import pm_work_batch_evidence as evidence
from bat_agent_connector import pm_workload as workload
from bat_agent_connector.operations import OperationError
from bat_agent_connector.task_verifier import ObservedVerifier, VerificationSettings
from tests import test_pm_execution_verification as verifier
from tests import test_pm_member_lifecycle_acceptance as member
from tests.pm_joined_delivery_helpers import settle
from tests.pm_joined_lifecycle_helpers import OWNER, assert_manual_preserved, engineering, execute
from tests.test_checkpoints import git
from tests.test_integration import commit
from tests.test_pm_delivery_lineage import sync_provider_graph
from tests.test_pm_maintenance_repair_receipts import record
from tests.test_pm_maintenance_repairs import advance, shared_request, view
from tests.test_pm_package_handoff import request as handoff_request
from tests.test_repository_sync import REQUEST

REPAIR_COMMAND = (sys.executable, '-B', '-c',
    "import recovery; assert recovery.reset_password('original-reset-token', 17) is False; "
    "import runpy,sys; sys.argv=['member_regression.py','recovery']; runpy.run_path('member_regression.py')")


async def enter_maintenance(w, mock):
    """The original adopted policy, not a direct stage mutation, decides idle."""
    ops, wid = w['d'].ops, w['workspace_id']
    before = copy.deepcopy(lifecycle.head(ops.db, wid))
    original = shared_request(w, checks.ACTION)
    checked = await settle(w, original)
    assert checked['status'] == 'succeeded', checked
    after = lifecycle.head(ops.db, wid)
    assert after['stage'] == 'maintain' and after['execution_epoch'] == before['execution_epoch'], after
    replay, fresh = ops.create(OWNER, **original)
    assert not fresh and replay['operation_id'] == checked['operation_id']
    assert lifecycle.head(ops.db, wid) == after
    w['ledger'].record('policy_enters_maintenance', maintenance_check_id=checked['operation_id'],
        maintenance_transition_id=after['transition_id'])
    assert_manual_preserved(w, mock)


def maintenance_provider(w):
    """Extend the same HTTP provider with an issue and native required checks."""
    gh, previous = w['gh'], w['gh'].before_request
    w['maintenance_issues'] = [{'id': 501, 'number': 50, 'state': 'open',
        'labels': [{'name': 'bug'}, {'name': 'member-release'}]}]
    previous_comparison = gh.comparison

    def comparison(base, head, page):
        value = previous_comparison(base, head, page)
        if value is not None:
            value['files'] = [{'filename': name, 'status': 'modified'}
                for name in git(w['remote'], 'diff', '--name-only', base, head).splitlines()]
        return value

    def provider(method, path, body):
        payload = None
        selected_sha = next((pr['head']['sha'] for pr in gh.pulls.values()
            if '/commits/' + pr['head']['sha'] + '/' in path
            or f"/check-suites/{20000 + pr['number']}/" in path), None)
        selected_pr = next((pr for pr in gh.pulls.values() if pr['head']['sha'] == selected_sha), None)
        check = ({'id': 10000 + selected_pr['number'], 'name': 'ci', 'head_sha': selected_sha,
            'status': 'completed', 'conclusion': 'success', 'app': {'id': 10},
            'check_suite': {'id': 20000 + selected_pr['number']}, 'started_at': '2026-10-12T00:00:00Z',
            'completed_at': '2026-10-12T00:01:00Z'} if selected_pr else None)
        if method == 'GET' and path == '/repos/o/r/issues':
            payload = w['maintenance_issues']
        elif method == 'GET' and selected_pr and path.endswith('/check-suites'):
            payload = {'total_count': 1, 'check_suites': [{'id': check['check_suite']['id'],
                'head_sha': selected_sha, 'status': 'completed', 'conclusion': 'success'}]}
        elif method == 'GET' and check and path.endswith('/check-runs'):
            payload = {'total_count': 1, 'check_runs': [check]}
        elif method == 'GET' and selected_pr and path.endswith('/statuses'):
            payload = []
        elif method == 'GET' and path == '/repos/o/r/rules/branches/main':
            payload = [{'ruleset_id': 91, 'ruleset_source_type': 'Repository', 'type': 'required_status_checks',
                'parameters': {'strict_required_status_checks_policy': True,
                    'required_status_checks': [{'context': 'ci', 'integration_id': 10}]}}]
        elif method == 'GET' and path == '/repos/o/r/branches/main/protection':
            payload = {'required_status_checks': {'strict': True, 'contexts': ['ci'],
                'checks': [{'context': 'ci', 'app_id': 10}]}}
        elif method == 'GET' and path == '/repos/o/r/branches/main':
            payload = {'name': 'main', 'commit': {'sha': gh.branches['main']}, 'protected': True}
        if payload is not None:
            gh.script.append(('GET', re.escape(path) + '$', 200, {}, copy.deepcopy(payload)))
        elif previous:
            previous(method, path, body)
        if method == 'POST' and path == '/repos/o/r/pulls':
            sync_provider_graph(w)

    gh.before_request, gh.comparison = provider, comparison


async def adopt_original_repair(w):
    ops, wid = w['d'].ops, w['workspace_id']
    selection = await repository_sync.source_preview(ops, REQUEST)
    selected = {**w, 'selection': {**selection['target'], **{key: selection[key]
        for key in ('source_ref', 'source_sha', 'repository_id', 'binding_digest')}}}
    repair = await engineering(selected, 'member.recovery')
    dependency = copy.deepcopy(w['implementations'][1]['child']['params']['pm_requirement_quality_refs'])
    handoff = await handoff_request(repair, agent='codex', pm_requirement_quality_refs=dependency)
    active = policies.active_record(ops, OWNER, wid)
    baseline = baselines.get_baseline(ops, OWNER, wid, active['baseline_id'])['baseline']
    document = copy.deepcopy(active['policy'])
    document.update(allowed_work_kinds=['bug_fix'], allowed_paths=['recovery.py'],
        delivery={'maximum': 'deploy', 'environment': 'production'})
    document['limits']['run_seconds'] = 600
    document['observation'].update(required_ci_profile=required_ci.PROFILE,
        maintenance_sources={'issues': {'labels_all': ['bug']}, 'dependencies': None})
    document['repair_rules'] = [{'key': 'member_password_type', 'work_kind': 'bug_fix',
        'labels_all': ['bug', 'member-release'], 'release': rules.release_predicate(baseline),
        'paths': ['recovery.py'], 'handoff': {key: handoff[key] for key in ('target', 'params', 'preconditions')},
        'verification': {'kind': 'fixed_revision_verification', 'profile': 'observed_runner_v1',
            'command_digest': evidence.digest(pm_execution_verification.command_identity(REPAIR_COMMAND))}}]
    await execute(w, policies.ACTION, {'workspace_id': wid},
        {'policy': document, 'rationale': 'Fix only non-text password rejection in the original released recovery behavior'},
        {'expected_policy_revision': policies.head(ops.db, wid)['revision'], 'expected_previous_policy_id': active['policy_id'],
            'expected_baseline_id': active['baseline_id'], 'expected_lifecycle_revision': lifecycle.head(ops.db, wid)['revision']})
    w['maintenance_repair_world'] = repair
    return repair


async def verify_repair(w, child):
    w['child'] = child
    w['d'].adapter.verifier = ObservedVerifier(VerificationSettings(
        commands={child['params']['project_id']: REPAIR_COMMAND},
        artifact_dir=str(w['human'].parent / 'joined-maintenance-verifier')))
    w['config'] = await pm_execution_verification.configuration(w['d'].ops, OWNER, w['workspace_id'], child['operation_id'])
    assert w['config']['available'], w['config']
    return await verifier.execute(w)


async def repair_original_release(w, mock):
    """Exercise the declared failure, fixed candidate and original delivery chain."""
    maintenance_provider(w)
    repair = await adopt_original_repair(w)
    ops, wid = w['d'].ops, w['workspace_id']
    await w['d'].inventory.refresh_host('h1')
    observed = await settle(w, shared_request(w, github.ACTION))
    assert observed['status'] == 'succeeded', observed
    observation = github.get_observation(ops, OWNER, wid, observed['result']['observation_id'])['observation']
    assert observation['document']['evidence']['status'] == 'complete', observation
    captured = await settle(w, shared_request(w, workload.ACTION))
    assert captured['status'] == 'succeeded', captured
    finding = observation['document']['evidence']['maintenance_sources']['issues']['findings'][0]
    request = shared_request(w, repairs.ACTION)
    request['params'].update(rule_key='member_password_type', finding_ref={
        'observation_id': observation['observation_id'], 'document_digest': observation['document_digest'],
        'finding_id': finding['finding_id']})
    parent = await advance(w, ops.create(OWNER, **request)[0])
    child = repairs._child(ops, parent)
    assert child and child['status'] == 'succeeded', {'parent': parent, 'child': child}
    original = ops.create(OWNER, **request)
    assert not original[1] and original[0]['operation_id'] == parent['operation_id']
    duplicate = copy.deepcopy(request)
    duplicate['idempotency_key'] += '-duplicate-finding'
    try:
        ops.create(OWNER, **duplicate)
    except OperationError as exc:
        assert exc.status == 409
    else:
        raise AssertionError('An already reserved finding cannot start another repair')
    failed = await verify_repair(repair, child)
    assert failed['result']['status'] == 'blocked', failed
    wt = child['result']['worktree_path']
    git(wt, 'config', 'user.name', 'fixture')
    git(wt, 'config', 'user.email', 'fixture@example.invalid')
    fixed = member.RECOVERY_FIXED.replace('or not password:', 'or not isinstance(password, str) or not password:')
    commit(wt, 'recovery.py', fixed, 'Reject non-text replacement passwords within the original recovery contract')
    passed = await verify_repair(repair, child)
    assert passed['result']['status'] == 'passed', passed
    condition = repairs._intent(parent)['rule']['verification']
    reference = await member.record_quality(repair, passed, condition)
    await settle(w, record(w, parent, {'kind': 'quality', 'reference': {key: value
        for key, value in reference.items() if key != 'candidate_commit'}}))
    parent = await advance(w, parent, rounds=10)
    delegated = delivery.child(ops, parent)
    assert delegated and delegated['status'] == 'waiting_external', (parent, view(w, parent))
    for run_id, run in w['gh'].runs.items():
        if run['status'] != 'completed':
            run.update(status='completed', conclusion='success')
            w['gh'].jobs[run_id][1]['conclusion'] = 'success'
    await ops._execute(delegated['operation_id'])
    parent = await advance(w, parent, rounds=10)
    assert parent['status'] == 'succeeded' and parent['result']['outcome'] == 'deployed', parent
    assert parent['result']['baseline_update']['status'] == 'updated', parent
    from tests.pm_joined_lifecycle_helpers import complete_native_turn
    complete_native_turn(w, mock, child['result']['session_id'])
    observer = runtime.observer(ops, parent)
    assert observer is not None
    await ops._execute(observer['operation_id'])
    proof = runtime.proof(ops, OWNER, parent)
    assert proof and not proof['capacity_held'], ops.get(observer['operation_id'])
    final = view(w, parent)
    assert final['outcome'] == 'deployed' and not final['repair_fixed'] and not final['capacity_held'], final
    w['ledger'].record('original_maintenance_repair', repair_operation_id=parent['operation_id'],
        repair_failed_verification_id=failed['operation_id'], repair_passed_verification_id=passed['operation_id'],
        repair_quality_ref=reference, repair_baseline_id=parent['result']['baseline_update']['baseline_id'],
        repair_runtime_observation_id=observer['operation_id'], review_period_end=time.time())
    assert pm_plans.head(ops.db, wid)['accepted_revision_id'] == w['ledger'].references['plan_revision_id']
    assert_manual_preserved(w, mock)
    return final


async def review_and_reopen(w, mock):
    from bat_agent_connector import pm_discussions, pm_workspace
    from bat_agent_connector import pm_maintenance_review as reviews
    from tests.test_pm_lifecycle import decide
    from tests.test_pm_maintenance_review import completion, mutation

    ops, wid = w['d'].ops, w['workspace_id']
    original_plan = copy.deepcopy(pm_plans.head(ops.db, wid))
    base = shared_request(w, reviews.PREFIX + 'prepare')
    prepared = await settle(w, {**base, 'params': {'title': 'Member release and repair Review',
        'period_start': ops.get(w['ledger'].references['adoption_operation_id'])['created_at'] - 1,
        'period_end': time.time()}, 'preconditions': {**base['preconditions'],
            'expected_policy_id': base['params']['policy_id'], 'expected_plan_version': original_plan['version'],
            'expected_review_revision': reviews.head(ops.db, wid)['revision']}})
    assert prepared['status'] == 'succeeded', prepared
    rid = prepared['result']['review_id']
    saved = reviews.get_review(ops, OWNER, wid, rid)
    report = saved['snapshot']['maintenance']['report']
    repair = next(row for row in report['repairs'] if row['operation_id'] == w['ledger'].references['repair_operation_id'])
    assert repair['outcome'] == 'deployed' and repair['runtime_completion'] and not repair['capacity_held'], repair
    snapshot_digest = saved['snapshot']['snapshot_digest']
    source = repair['source_id']
    note = await settle(w, mutation(ops, w, rid, 'note', {'content':
        'The original recovery regression now rejects non-text passwords; the deployed revision and runtime evidence are recorded.',
        'source_refs': [source],
        'rationale': 'Review the fixed result and its declared offline evidence limits'}))
    candidate = await settle(w, mutation(ops, w, rid, 'candidate.record', {
        'title': 'Discuss accessible recovery instructions', 'problem': 'The original scope did not evaluate recovery guidance accessibility.',
        'goal_relation': 'Help members restore access', 'expected_effect': 'Identify whether another scope decision is useful',
        'options': ['Investigate', 'Defer'], 'hypotheses': ['Clearer instructions may reduce failed attempts'],
        'source_refs': [source], 'rationale': 'A proposal for discussion, with no automatic scope adoption'}))
    assert note['status'] == candidate['status'] == 'succeeded'
    done = await settle(w, mutation(ops, w, rid, 'complete', completion([note['result']['event_id']],
        candidate_ids=[candidate['result']['event_id']], discussion_summary='Reviewed original repair, deployment and runtime evidence.',
        follow_ups=[{'id': 'repair-evidence', 'text': 'Capture the original repaired requirement assessment for the next Review.',
            'owner_actor': OWNER.actor, 'expected_effect': 'Make the original quality result available to the next discussion.',
            'verification': 'Select the original quality reference in a new fixed Project Brief snapshot.',
            'source_refs': [source]}],
        direction_rationale='Keep the adopted release under maintenance; discuss the separate candidate in Idea.')))
    assert done['status'] == 'succeeded' and done['result']['meeting_completed'], done
    reviewed = reviews.get_review(ops, OWNER, wid, rid)
    assert reviewed['snapshot']['snapshot_digest'] == snapshot_digest
    proposed = next(row for row in reviewed['events'] if row['operation_id'] == candidate['operation_id'])
    created = await execute(w, 'pm.thread.create', {'workspace_id': wid},
        {'title': 'Recovery guidance after the original repair'},
        {'expected_version': pm_workspace.get_workspace(ops.db, OWNER, wid)['version']})
    tid = created['result']['thread_id']
    imported = await execute(w, 'pm.input.record', {'workspace_id': wid, 'thread_id': tid},
        {'slot': 'quality', 'value': proposed['document']['title'], 'status': 'unresolved',
            'source_ref': proposed['planning_source']},
        {'expected_version': pm_discussions._thread(ops.db, wid, tid)['version']})
    read = pm_discussions.get_thread(ops, OWNER, wid, tid)
    assert read['thread']['mode'] == 'idea' and read['inputs'][0]['status'] == 'unresolved'
    assert read['inputs'][0]['source_candidate']['document'] == proposed['document']
    assert pm_plans.head(ops.db, wid) == original_plan
    w['ledger'].record('maintenance_review_to_idea', maintenance_review_id=rid,
        maintenance_review_snapshot_digest=snapshot_digest, next_idea_thread_id=tid,
        next_idea_input_id=imported['result']['input_id'])
    from tests import test_pm_quality_projections as projections
    await projections.capture(w['maintenance_repair_world'], w['ledger'].references['repair_quality_ref'])
    later_snapshot = await projections.snapshot(w)
    updated = await settle(w, mutation(ops, w, rid, 'follow_up.record', {
        'follow_up_id': 'repair-evidence', 'status': 'reported_done',
        'result': 'The original repair quality assessment is selected in this later snapshot.',
        'snapshot_id': later_snapshot['snapshot_id'], 'rationale': 'Accountable report linked to the exact original evidence.'}))
    assert updated['status'] == 'succeeded', updated
    prior_review = reviews.get_review(ops, OWNER, wid, rid)
    next_base = shared_request(w, reviews.PREFIX + 'prepare')
    next_review = await settle(w, {**next_base, 'params': {'title': 'Follow-up on the original repair Review',
        'period_start': prepared['created_at'], 'period_end': time.time(), 'previous_review_id': rid},
        'preconditions': {**next_base['preconditions'], 'expected_policy_id': next_base['params']['policy_id'],
            'expected_plan_version': original_plan['version'], 'expected_review_revision': reviews.head(ops.db, wid)['revision']}})
    assert next_review['status'] == 'succeeded', next_review
    following = reviews.get_review(ops, OWNER, wid, next_review['result']['review_id'])
    previous = following['snapshot']['maintenance']['report']['previous_review']
    assert previous['completion'] == prior_review['completion']
    result = next(row for row in previous['follow_up_results'] if row['operation_id'] == updated['operation_id'])
    assert result['document']['snapshot_ref']['snapshot_digest'] == later_snapshot['snapshot_digest']
    assert result['document']['verification_status'] == 'unassessed'
    assert reviews.get_review(ops, OWNER, wid, rid)['snapshot']['snapshot_digest'] == snapshot_digest
    assert pm_plans.head(ops.db, wid) == original_plan
    w['ledger'].record('next_review_preserves_original_decision',
        maintenance_follow_up_operation_id=updated['operation_id'],
        maintenance_follow_up_snapshot_id=later_snapshot['snapshot_id'], next_maintenance_review_id=next_review['result']['review_id'])
    # A queued old-epoch observation cannot come back to life after reopening.
    queued = ops.create(OWNER, **shared_request(w, checks.ACTION))[0]
    before_calls = len(w['gh'].requests)
    paused = await decide(ops, wid, 'pause', who=OWNER)
    await ops._execute(queued['operation_id'])
    stopped = ops.get(queued['operation_id'])
    assert stopped['status'] != 'succeeded' and stopped['error_code'] != 'INTERNAL', stopped
    assert len(w['gh'].requests) == before_calls
    closed = await decide(ops, wid, 'close', who=OWNER)
    reopened = await decide(ops, wid, 'reopen', params={'stage': 'development'}, who=OWNER)
    await ops._execute(queued['operation_id'])
    assert len(w['gh'].requests) == before_calls
    assert ops.get(queued['operation_id'])['status'] != 'succeeded'
    assert reviews.get_review(ops, OWNER, wid, rid)['snapshot']['snapshot_digest'] == snapshot_digest
    w['ledger'].record('pause_close_reopen', pause_operation_id=paused['operation_id'],
        close_operation_id=closed['operation_id'], reopen_operation_id=reopened['operation_id'],
        stopped_check_id=queued['operation_id'])
    assert_manual_preserved(w, mock)
