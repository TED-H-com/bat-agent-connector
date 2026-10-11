"""Fixed policy/baseline workload observations, using disk journal and fake sources."""
from __future__ import annotations

import datetime as dt
import json
import sqlite3
import uuid
from types import SimpleNamespace

import pytest

from bat_agent_connector import pm_lifecycle_gates, pm_runs, pm_sprints, pm_workspace, work_items
from bat_agent_connector import pm_maintenance_policy as policy
from bat_agent_connector import pm_workload as workload
from bat_agent_connector import pm_workload_sources as sources
from bat_agent_connector.api_auth import Principal
from bat_agent_connector.config import GitHubRepo, HostConfig, RepositorySyncConfig
from bat_agent_connector.operations import OpContext, OperationError
from tests.test_pm_evidence import OWNER, READER, act, item, link, source_operation, task
from tests.test_pm_evidence import ops as evidence_ops
from tests.test_pm_lifecycle import decide
from tests.test_pm_release_baselines import execute, remove_repo, request, setup
from tests.test_pm_release_baselines import ops as baseline_ops
from tests.test_pm_runs import create as create_run
from tests.test_pm_sprints import change as sprint_change
from tests.test_pm_sprints import create as create_sprint
from tests.test_pm_sprints import prepared as sprint_prepared
from tests.test_pm_sprints import selections

base_ops = evidence_ops
release_ops = baseline_ops
NOW = 2_000_000_000.0


@pytest.fixture
def ops(release_ops, monkeypatch):
    base_ops = release_ops
    hc = HostConfig('mock-host', 'wss://bat.example.invalid', 'a' * 64, 'env:FAKE_TOKEN')
    cfg = base_ops.context['github_config']
    cfg.repos['example/product'] = GitHubRepo('example/product', sync=RepositorySyncConfig(
        'https://github.com/example/product.git', (('mock-host', 'mock-workspace'),)))
    base_ops.context['fleet'] = SimpleNamespace(config=SimpleNamespace(hosts={'mock-host': hc}, github=cfg))
    pm_sprints.install(base_ops)
    pm_runs.schema(base_ops.journal)
    policy.install(base_ops)
    workload.install(base_ops)
    monkeypatch.setattr(workload, 'time', SimpleNamespace(time=lambda: NOW))
    return base_ops


def settings():
    return {'enabled': True, 'after_release_policy': 'maintain_when_idle', 'sources': ['issues', 'pull_requests'],
        'allowed_work_kinds': [], 'allowed_paths': ['.'], 'delivery': {'maximum': 'report', 'environment': None},
        'observation': {'max_age_seconds': 120, 'stable_for_seconds': 60},
        'schedule': {'timezone': 'UTC', 'check_interval_seconds': 120, 'review_weekday': 1, 'review_local_time': '09:00'},
        'limits': {'max_parallel': 1, 'run_seconds': 120, 'period_seconds': 3600, 'period_runs': 10,
            'cost_microusd': None, 'retries': 0},
        'verification': {'required_ci': True, 'review_required': True, 'acceptance_evidence': ['Fixed version tests']}}


async def prepared(ops):
    target, binding, pid = await setup(ops)
    return await policy_for(ops, target, binding, pid)


async def policy_for(ops, target, binding, pid):
    release = await execute(ops, request(ops, target, binding, branch='support/1.x'))
    wid, baseline_id = target['workspace_id'], release['result']['baseline_id']
    adopted = await execute(ops, {'action': policy.ACTION, 'target': {'workspace_id': wid},
        'params': {'policy': settings(), 'rationale': 'Observe the explicitly bound repository and workspace'},
        'preconditions': {'expected_policy_revision': 0, 'expected_previous_policy_id': None,
            'expected_baseline_id': baseline_id, 'expected_lifecycle_revision': 0}, 'idempotency_key': uuid.uuid4().hex})
    return {'workspace_id': wid, 'project_id': pid, 'policy_id': adopted['result']['policy_id']}


def capture_request(ops, data):
    record = policy.active_record(ops, OWNER, data['workspace_id'])
    return {'action': workload.ACTION, 'target': {'workspace_id': data['workspace_id']},
        'params': {'policy_id': record['policy_id']}, 'preconditions': {
            'expected_policy_version': record['version'], 'expected_baseline_id': record['baseline_id'],
            'expected_lifecycle_revision': pm_lifecycle_gates.head(ops.db, data['workspace_id'])['revision']},
        'idempotency_key': uuid.uuid4().hex}


async def capture(ops, data, *, who=OWNER):
    operation = await execute(ops, capture_request(ops, data), who=who)
    return operation, workload.get_snapshot(ops, OWNER, data['workspace_id'], operation['result']['snapshot_id'])['snapshot']['document']


def inventory(ops, *, at=NOW - 1, reachable=True, complete=True, status='succeeded', sessions=()):
    hc = ops.context['fleet'].config.hosts['mock-host']
    binding = sources.digest([hc.url, hc.fingerprint, hc.profile_id])
    iso = dt.datetime.fromtimestamp(at, dt.timezone.utc).isoformat()
    discovery = {'binding_version': binding, 'finished_at': iso, 'status': status,
        'complete_enumeration': complete, 'authority': {'verified': True},
        'coverage': {'workspace_ids': ['mock-workspace']}}
    with ops.journal.tx():
        ops.db.execute('''INSERT INTO hosts_observed(host,reachable,last_attempt_at,last_success_at)
            VALUES('mock-host',?,?,?) ON CONFLICT(host) DO UPDATE SET reachable=excluded.reachable,
            last_attempt_at=excluded.last_attempt_at,last_success_at=excluded.last_success_at''', (reachable, at, at))
        ops.db.execute('INSERT OR REPLACE INTO discovery_latest VALUES(?,?,?,?)',
            ('mock-host', hc.profile_id, binding, json.dumps(discovery)))
        for values in sessions:
            data = {'session_id': 'manual-1', 'workspace_id': 'mock-workspace', 'loaded': True,
                'streaming': False, 'runtime_status': 'idle', 'profile_id': hc.profile_id, **values}
            gone = data.pop('gone_at', None)
            missing = data.pop('missing_count', 0)
            ops.db.execute('''INSERT OR REPLACE INTO sessions_observed(host,session_id,body,digest,provenance,
                api_access,first_seen_at,last_seen_at,gone_at,missing_count) VALUES(?,?,?,?,?,?,?,?,?,?)''',
                ('mock-host', data['session_id'], json.dumps(data), sources.digest(data), 'manual', 'read_only', at, at, gone, missing))


def codes(doc):
    return {r['code'] for r in doc['blocking_reasons']}


async def test_fixed_snapshot_complete_bat_does_not_invent_complete_repository_or_start_maintenance(ops):
    data = await prepared(ops)
    inventory(ops)
    old_version = ops.db.execute('PRAGMA user_version').fetchone()[0]
    workload.install(ops)
    op, doc = await capture(ops, data)
    assert ops.db.execute('PRAGMA user_version').fetchone()[0] == old_version
    assert doc['classification'] == 'unknown'
    assert codes(doc) == {'github_repository_coverage_unavailable'}
    assert doc['source_coverage'][1]['status'] == 'complete'
    assert doc['source_coverage'][2]['outside_scope'] == ['unlinked_remote_work', 'current_remote_pr_ci_state']
    assert doc['quality'] == 'unassessed' and doc['automatic_maintenance_authorized'] is False
    assert doc['network_checked'] is False and doc['stability']['satisfied'] is False
    assert doc['expires_at'] == NOW + 119
    assert ops.db.execute('SELECT COUNT(*) FROM tasks').fetchone()[0] == 0
    assert ops.db.execute('SELECT COUNT(*) FROM pm_runs').fetchone()[0] == 0
    assert pm_lifecycle_gates.head(ops.db, data['workspace_id'])['stage'] == 'idea'
    assert workload.operation_visible(ops.db, READER, op)
    public = json.dumps(doc)
    assert 'wss://' not in public and 'FAKE_TOKEN' not in public
    with pytest.raises(sqlite3.IntegrityError, match='immutable'):
        ops.db.execute("UPDATE pm_workload_snapshots SET document='{}'")


@pytest.mark.parametrize('changes,code', [
    ({'reachable': False}, 'adapter_disconnected'), ({'at': NOW - 500}, 'source_expired'),
    ({'at': NOW + 5}, 'source_time_unproven'), ({'complete': False}, 'adapter_coverage_incomplete'),
    ({'status': 'partial'}, 'adapter_coverage_incomplete'),
])
async def test_disconnected_stale_partial_and_future_observations_never_idle(ops, changes, code):
    data = await prepared(ops)
    inventory(ops, **changes)
    _, doc = await capture(ops, data)
    assert code in codes(doc) and doc['classification'] == 'unknown'


@pytest.mark.parametrize('session,code,classification', [
    ({'streaming': True}, 'coding_active', 'development'),
    ({'pending': [{'request': 'PRIVATE DETAIL'}]}, 'session_waiting', 'development'),
    ({'loaded': False}, 'session_end_unproven', 'unknown'),
    ({'fields_stale': True}, 'session_state_unknown', 'unknown'),
    ({'workspace_id': None}, 'session_workspace_unproven', 'unknown'),
    ({'gone_at': NOW - 1, 'missing_count': 2}, 'session_state_unknown', 'unknown'),
])
async def test_human_session_never_inherits_completion_from_idle_or_gone(ops, session, code, classification):
    data = await prepared(ops)
    inventory(ops, sessions=[session])
    _, doc = await capture(ops, data)
    assert code in codes(doc) and doc['classification'] == classification
    assert 'PRIVATE DETAIL' not in json.dumps(doc)
    row = ops.db.execute("SELECT provenance,api_access FROM sessions_observed WHERE session_id='manual-1'").fetchone()
    assert tuple(row) == ('manual', 'read_only')


async def test_backlog_excluded_but_waiting_and_done_claims_do_not_count_as_delivered(ops):
    data = await prepared(ops)
    inventory(ops)
    work = await item(ops, data['project_id'])
    _, doc = await capture(ops, data)
    assert {'kind': 'work', 'id': work, 'reason': 'unselected_backlog'} in doc['exclusions']
    for state, expected in [('waiting', 'work_waiting'), ('done', 'work_done_claim_unverified')]:
        await act(ops, 'work_item.update', {'work_item_id': work}, {'state': state},
            {'expected_version': work_items._get_item(ops.db, work)['version']})
        _, doc = await capture(ops, data)
        assert expected in codes(doc) and doc['classification'] == 'development'


@pytest.mark.parametrize('state', ['queued', 'waiting_permission', 'quota_limited', 'needs_ted', 'uncertain', 'failed', 'done'])
async def test_task_commitments_and_done_without_verification_remain_blockers(ops, state):
    data = await prepared(ops)
    inventory(ops)
    work = await item(ops, data['project_id'])
    t = task(ops)
    ops.db.execute('UPDATE tasks SET state=? WHERE task_id=?', (state, t['task_id']))
    await link(ops, {'work_item_id': work}, 'task', t['task_id'])
    _, doc = await capture(ops, data)
    assert doc['classification'] == 'development'
    assert any(r.get('source') == {'kind': 'task', 'id': t['task_id']} for r in doc['blocking_reasons'])


async def test_verified_ended_task_session_may_be_retained_without_cleanup(ops):
    data = await prepared(ops)
    project = work_items._get_project(ops.db, data['project_id'])
    await act(ops, 'project.update', {'project_id': data['project_id']}, {'task_project': 'example'}, {'expected_version': project['version']})
    t = task(ops)
    sha, tree = 'a' * 40, 'b' * 40
    with ops.journal.tx():
        ops.db.execute('''UPDATE tasks SET state='done',session_id='ended',review_passed=1,
            verification_commit=?,review_commit=?,verification_tree=?,review_tree=? WHERE task_id=?''', (sha, sha, tree, tree, t['task_id']))
        ops.db.execute('''INSERT INTO observed_verifications(task_id,candidate_commit,tree_hash,command,exit_code,
            log_ref,output_sha256,created_at) VALUES(?,?,?,?,0,?,?,?)''',
            (t['task_id'], sha, tree, 'fixture-only-test', 'private-log-reference', 'c' * 64, NOW - 10))
    inventory(ops, sessions=[{'session_id': 'ended', 'loaded': False}])
    _, doc = await capture(ops, data)
    assert {'kind': 'session', 'id': 'mock-host/ended', 'reason': 'verified_ended_session_retained'} in doc['exclusions']
    assert codes(doc) == {'github_repository_coverage_unavailable'}
    assert ops.db.execute("SELECT COUNT(*) FROM sessions_observed WHERE session_id='ended'").fetchone()[0] == 1
    ops.db.execute('''INSERT INTO commands(command_id,task_id,idem_key,kind,status,payload,created_at,updated_at)
        VALUES('uncertain-command',?,'unknown-send','send','uncertain','{}',?,?)''', (t['task_id'], NOW - 5, NOW - 5))
    _, later = await capture(ops, data)
    assert 'command_waiting_or_unknown' in codes(later)
    assert {'kind': 'session', 'id': 'mock-host/ended', 'reason': 'verified_ended_session_retained'} not in later['exclusions']


async def test_explicit_defer_records_failed_attempt_history_without_turning_it_done(ops):
    data = await prepared(ops)
    work = await item(ops, data['project_id'])
    t = task(ops)
    ops.db.execute("UPDATE tasks SET state='failed' WHERE task_id=?", (t['task_id'],))
    await link(ops, {'work_item_id': work}, 'task', t['task_id'])
    _, before = await capture(ops, data)
    assert 'task_failed_unresolved' in codes(before)  # initial backlog is not deferral
    for state in ('waiting', 'todo'):
        await act(ops, 'work_item.update', {'work_item_id': work}, {'state': state},
            {'expected_version': work_items._get_item(ops.db, work)['version']})
    _, after = await capture(ops, data)
    assert {'kind': 'task', 'id': t['task_id'], 'reason': 'failed_attempt_explicitly_deferred'} in after['exclusions']
    assert ops.journal.get(t['task_id'])['state'] == 'failed'
    assert work_items._get_item(ops.db, work)['state'] == 'todo'


async def test_pm_owned_session_is_excluded_by_real_run_binding_not_name(ops):
    data = await prepared(ops)
    wid = data['workspace_id']
    thread = await act(ops, 'pm.thread.create', {'workspace_id': wid}, {'title': 'Long-running idea'},
        {'expected_version': pm_workspace.get_workspace(ops.db, OWNER, wid)['version']})
    run = create_run(ops, {'workspace_id': wid, 'thread_id': thread['thread_id']})
    pm_runs.bind_session(ops.journal, run['run_id'], run['generation'], host='mock-host', session_id='pm-owned',
        incarnation='a' * 32, worktree_binding={'repository': 'example/product', 'commit': 'b' * 40},
        sdk_session_id='native-pm', actual_model='observed-model')
    inventory(ops, sessions=[{'session_id': 'pm-owned', 'streaming': True},
        {'session_id': 'pm-by-name-only', 'streaming': True}])
    _, doc = await capture(ops, data)
    assert {'kind': 'session', 'id': 'mock-host/pm-owned', 'reason': 'pm_owned_session'} in doc['exclusions']
    active = [r['source']['id'] for r in doc['blocking_reasons'] if r['code'] == 'coding_active']
    assert active == ['mock-host/pm-by-name-only']
    assert pm_runs.get(ops.db, run['run_id'])['generation'] == run['generation']


async def test_planned_selected_sprint_blocks_without_dispatch_or_changing_original_attempts(ops):
    management = await sprint_prepared(ops)
    wid = management['workspace_id']
    pid = work_items._get_item(ops.db, management['work'][0])['project_id']
    binding = ops.db.execute('SELECT binding_id FROM pm_project_bindings WHERE workspace_id=? AND project_id=?', (wid, pid)).fetchone()[0]
    data = await policy_for(ops, {'workspace_id': wid, 'repository': 'example/product'}, binding, pid)
    sprint = await create_sprint(ops, management)
    await sprint_change(ops, sprint, 'select', items=selections(management))
    _, doc = await capture(ops, data)
    assert 'sprint_commitment' in codes(doc) and doc['classification'] == 'development'
    assert ops.db.execute('SELECT COUNT(*) FROM tasks').fetchone()[0] == 0
    await sprint_change(ops, sprint, 'cancel')
    _, later = await capture(ops, data)
    assert 'sprint_commitment' not in codes(later)
    assert all(work_items._get_item(ops.db, wid)['state'] == 'todo' for wid in management['work'])


async def test_pending_operation_and_unknown_step_on_terminal_operation_still_block(ops):
    data = await prepared(ops)
    inventory(ops)
    source = await source_operation(ops, target={'repository': 'example/product'})
    with ops.journal.tx():
        ops.db.execute("UPDATE operations SET status='failed' WHERE operation_id=?", (source['operation_id'],))
        ops.db.execute('''INSERT INTO operation_steps(operation_id,seq,name,status,request,started_at)
            VALUES(?,1,'original_delivery','uncertain','{}',?)''', (source['operation_id'], NOW - 1000))
    _, doc = await capture(ops, data)
    assert 'operation_unknown' in codes(doc) and doc['classification'] == 'development'


async def test_fixed_original_source_withdrawal_hides_snapshot_operation_and_history(ops):
    data = await prepared(ops)
    inventory(ops)
    op, doc = await capture(ops, data)
    await remove_repo(ops, data['project_id'])
    assert not workload.operation_visible(ops.db, READER, op)
    with pytest.raises(OperationError) as error:
        workload.get_snapshot(ops, READER, data['workspace_id'], doc['snapshot_id'])
    assert error.value.status == 404
    assert workload.current_snapshot(ops, READER, data['workspace_id']) == {
        'workspace_id': data['workspace_id'], 'snapshot': None, 'visibility_filtered': True}
    page = workload.list_snapshots(ops, READER, data['workspace_id'])
    assert page['snapshots'] == [] and page['visibility_filtered']
    # Original local effect recovery is bookkeeping, never another observation.
    recovered = await workload._run(OpContext(ops, ops.get(op['operation_id'])))
    assert recovered == op['result']
    assert ops.db.execute('SELECT COUNT(*) FROM pm_workload_snapshots').fetchone()[0] == 1


async def test_queued_capture_context_changed_before_effect_is_not_recorded(ops):
    data = await prepared(ops)
    queued = ops.create(OWNER, **capture_request(ops, data))[0]
    await decide(ops, data['workspace_id'], 'pause')
    await ops._execute(queued['operation_id'])
    assert ops.get(queued['operation_id'])['status'] == 'failed'
    assert ops.db.execute('SELECT COUNT(*) FROM pm_workload_snapshots').fetchone()[0] == 0
    # A NEW explicit observation of the paused state is still record-only.
    fresh, doc = await capture(ops, data)
    assert fresh['status'] == 'succeeded' and doc['execution_epoch'] == 1


async def test_manage_only_member_capture_and_original_key_replay_no_observe_escalation(ops):
    data = await prepared(ops)
    principal = Principal(OWNER.actor, frozenset({'manage'}))
    req = capture_request(ops, data)
    done = await execute(ops, req, who=principal)
    same, reused = ops.create(principal, **req)
    assert reused and same['operation_id'] == done['operation_id']
    with pytest.raises(OperationError, match='FORBIDDEN'):
        workload.current_snapshot(ops, principal, data['workspace_id'])


async def test_extra_idle_or_complete_fields_and_wrong_cursor_are_rejected(ops):
    data = await prepared(ops)
    req = capture_request(ops, data)
    req['params']['complete'] = True
    with pytest.raises(OperationError, match='INVALID_PARAMS'):
        ops.create(OWNER, **req)
    for after in [True, -1, 2**63, '1']:
        with pytest.raises(OperationError, match='INVALID_PARAMS'):
            workload.list_snapshots(ops, READER, data['workspace_id'], after=after)


async def test_wire_byte_page_prefix_never_skips_next_snapshot(ops, monkeypatch):
    # Match inventory and policy digests with a genuine multibyte profile name;
    # wire accounting uses UTF-8 bytes, not JSON character count.
    ops.context['fleet'].config.hosts['mock-host'].profile_id = '開發'
    data = await prepared(ops)
    inventory(ops)
    await capture(ops, data)
    await capture(ops, data)
    full = workload.list_snapshots(ops, READER, data['workspace_id'])
    assert workload._size(full) > len(json.dumps(full, ensure_ascii=False))
    size = workload._size({**full, 'snapshots': full['snapshots'][:1], 'next_cursor': full['snapshots'][0]['sequence']})
    monkeypatch.setattr(workload, 'MAX_READ_BYTES', size + 10)
    first = workload.list_snapshots(ops, READER, data['workspace_id'])
    second = workload.list_snapshots(ops, READER, data['workspace_id'], after=first['next_cursor'])
    assert [r['snapshot_id'] for r in first['snapshots'] + second['snapshots']] == [r['snapshot_id'] for r in full['snapshots']]
    assert workload._size(first) <= size + 10
    monkeypatch.setattr(workload, 'MAX_READ_BYTES', 100)
    with pytest.raises(OperationError) as error:
        workload.list_snapshots(ops, READER, data['workspace_id'])
    assert error.value.status == 413


async def test_final_admission_rechecks_original_source_digest_and_needs_same_transaction(ops):
    data = await prepared(ops)
    inventory(ops)
    _, doc = await capture(ops, data)
    current = policy.active_record(ops, OWNER, data['workspace_id'])
    with pytest.raises(OperationError, match='TRANSACTION_REQUIRED'):
        workload.evaluate_for_maintenance(ops, OWNER, data['workspace_id'], doc['snapshot_id'], current, now=NOW)
    await item(ops, data['project_id'])
    with ops.journal.tx():
        result = workload.evaluate_for_maintenance(ops, OWNER, data['workspace_id'], doc['snapshot_id'], current, now=NOW)
    assert not result['eligible'] and 'source_changed' in result['reasons'] and 'workload_not_idle' in result['reasons']


def test_stability_does_not_bridge_gaps_clock_rollback_or_changed_activity(tmp_path):
    # Pure reducer inputs represent already authenticated complete observations;
    # no production endpoint can submit this fixture's completeness assertion.
    db = sqlite3.connect(tmp_path / 'stability.db')
    db.execute('CREATE TABLE pm_workload_snapshots(sequence INTEGER,workspace_id TEXT,document TEXT,source_binding TEXT)')
    intent = {'observation': {'stability_seconds': 60}}
    current = {'classification': 'idle_candidate', 'activity_digest': 'same', 'expires_at': 250}
    previous = {'classification': 'idle_candidate', 'activity_digest': 'same', 'as_of': 100, 'expires_at': 230}
    db.execute('INSERT INTO pm_workload_snapshots VALUES(1,?,?,?)', ('ws', json.dumps(previous), json.dumps({'intent': intent})))
    assert workload._stability(db, 'ws', intent, current, 200)['satisfied']
    assert not workload._stability(db, 'ws', intent, current, 240)['satisfied']  # uncovered gap
    assert not workload._stability(db, 'ws', intent, current, 90)['satisfied']  # backwards clock
    assert not workload._stability(db, 'ws', intent, {**current, 'activity_digest': 'changed'}, 200)['satisfied']
    db.close()
