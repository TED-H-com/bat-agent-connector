"""Disk-journal scheduling and original child evidence; no live provider or model."""
from __future__ import annotations

import copy
import json
import sqlite3
from dataclasses import replace
from types import SimpleNamespace

import pytest

from bat_agent_connector import api_auth, pm_lifecycle_gates
from bat_agent_connector import pm_maintenance_checks as checks
from bat_agent_connector import pm_maintenance_policy as policies
from bat_agent_connector.operations import OpContext, OperationError
from tests import test_pm_github_observation as githubtests
from tests.operation_helpers import settle_operations
from tests.test_pm_evidence import CONTRIBUTOR, OWNER, READER
from tests.test_pm_lifecycle import decide
from tests.test_pm_release_baselines import remove_repo

base_ops = githubtests.base_ops
release_ops = githubtests.release_ops
workload_ops = githubtests.workload_ops
github_ops = githubtests.ops


@pytest.fixture
def ops(github_ops, monkeypatch):
    checks.install(github_ops)
    clock = [githubtests.NOW]
    github_ops.context['check_test_clock'] = clock
    monkeypatch.setattr(checks, 'time', SimpleNamespace(time=lambda: clock[0]))
    monkeypatch.setattr(policies, 'time', SimpleNamespace(time=lambda: clock[0]))
    # The integrator adds the same guard inside every unsent child effect. This
    # entry wrapper exercises composed real children while shared files stay owned
    # by the integrator; per-page guard placement has separate integration coverage.
    for _, name in checks.CHILDREN:
        action = github_ops.actions[name]
        async def guarded(ctx, original=action.run):
            checks.require_child(ctx.service, ctx.op)
            return await original(ctx)
        github_ops.actions[name] = replace(action, run=guarded)
    return github_ops


async def prepared(ops, monkeypatch, *, stage='development', document=None):
    if document is not None:
        monkeypatch.setattr(githubtests.worktests, 'settings', lambda: copy.deepcopy(document))
    data = await githubtests.prepared(ops, monkeypatch)
    if stage != 'idea':
        await decide(ops, data['workspace_id'], 'set_stage', params={'stage': stage})
    api_auth.issue(ops.db, OWNER.actor, ['observe', 'manage'])
    githubtests.worktests.inventory(ops)
    return data


def request(ops, data):
    value = githubtests.request(ops, data)
    value['action'] = checks.ACTION
    return value


async def finish(ops, parent):
    for _ in range(8):
        await settle_operations(ops)
        current = ops.get(parent['operation_id'])
        if current['status'] in checks.TERMINAL | {'needs_attention'}:
            return current
        ops.db.execute('UPDATE operations SET next_run_at=0 WHERE operation_id=?', (parent['operation_id'],))
    raise AssertionError(ops.get(parent['operation_id']))


async def check(ops, data):
    parent = ops.create(OWNER, **request(ops, data))[0]
    done = await finish(ops, parent)
    assert done['status'] == 'succeeded', done
    return done, checks.get_run(ops, OWNER, data['workspace_id'], done['result']['run_id'])['run']


async def test_composes_original_children_and_preserves_coverage_cost_and_schema_evidence(ops, monkeypatch):
    data = await prepared(ops, monkeypatch)
    before = ops.db.execute('PRAGMA user_version').fetchone()[0]
    checks.install(ops)
    parent, run = await check(ops, data)
    assert ops.db.execute('PRAGMA user_version').fetchone()[0] == before
    assert parent['result']['outcome'] == 'complete'
    doc = run['document']
    assert [c['action'] for c in doc['children']] == ['pm.github.observe', 'pm.workload.capture']
    assert doc['children'][0]['result']['coverage_status'] == 'complete'
    assert doc['children'][1]['result']['classification'] == 'idle_candidate'
    assert doc['cost_coverage'] == {'model_requests': 'none', 'provider_billing': 'unmeasured',
        'cost_budget_microusd': None, 'charged_microusd': None}
    assert doc['retries_used'] == 0 and not doc['automatic_maintenance_authorized'] and not doc['repair_started']
    assert pm_lifecycle_gates.head(ops.db, data['workspace_id'])['stage'] == 'development'
    assert ops.db.execute('SELECT COUNT(*) FROM tasks').fetchone()[0] == 0
    assert len(ops.context['github'].reads) == 16
    count = len(ops.context['github'].reads)
    assert await checks._run(OpContext(ops, parent)) == parent['result']
    assert len(ops.context['github'].reads) == count
    for query in ('DELETE FROM pm_maintenance_check_runs', 'UPDATE pm_maintenance_check_runs SET finished_at=0',
                  'DELETE FROM pm_maintenance_check_children'):
        with pytest.raises(sqlite3.IntegrityError, match='immutable'):
            ops.db.execute(query)
    assert checks.operation_visible(ops.db, READER, parent)


async def test_incomplete_provider_or_inventory_is_not_reported_as_complete(ops, monkeypatch):
    data = await prepared(ops, monkeypatch)
    ops.context['github'].total_override = 1
    _, run = await check(ops, data)
    assert run['document']['outcome'] == 'incomplete'
    assert run['document']['reason'] == 'SOURCE_COVERAGE_INCOMPLETE'
    assert checks.schedule_status(ops, OWNER, data['workspace_id'])['schedule']['last_success'] is None


async def test_tick_atomic_duplicate_window_and_bounded_missed_window_coalescing(ops, monkeypatch):
    data = await prepared(ops, monkeypatch)
    clock = ops.context['check_test_clock']
    assert checks.tick(ops)['scheduled'] == 0
    clock[0] += 120 * 50 + 5
    assert checks.tick(ops)['scheduled'] == 1
    assert checks.tick(ops)['scheduled'] == 0
    windows = ops.db.execute('SELECT * FROM pm_maintenance_check_windows').fetchall()
    assert len(windows) == 1 and windows[0]['window'] == 50 and windows[0]['coalesced_windows'] == 49
    assert windows[0]['scheduled_at'] == githubtests.NOW + 120 * 50
    parent = ops.get(windows[0]['operation_id'])
    # Provider fixtures have their own fixed clock; only scheduler cadence moves.
    done = await finish(ops, parent)
    assert done['status'] == 'succeeded'
    status = checks.schedule_status(ops, OWNER, data['workspace_id'])['schedule']
    assert status['latest_window']['operation_id'] == parent['operation_id']
    assert status['next_due'] == githubtests.NOW + 120 * 51
    assert status['weekly_review']['execution_available'] is False
    assert checks.tick(ops)['scheduled'] == 0


@pytest.mark.parametrize('stage', ['idea', 'planning', 'paused', 'closed'])
async def test_never_schedules_outside_development_or_maintain(ops, monkeypatch, stage):
    data = await prepared(ops, monkeypatch, stage='idea' if stage in {'idea', 'planning'} else 'development')
    if stage == 'planning':
        await decide(ops, data['workspace_id'], 'set_stage', params={'stage': stage})
    elif stage in {'paused', 'closed'}:
        await decide(ops, data['workspace_id'], 'pause' if stage == 'paused' else 'close')
    ops.context['check_test_clock'][0] += 240
    assert checks.tick(ops)['scheduled'] == 0
    with pytest.raises(OperationError):
        ops.create(OWNER, **request(ops, data))
    assert ops.context['github'].reads == []


@pytest.mark.parametrize('change', ['revoked', 'expired', 'partial_scopes', 'owner_removed'])
async def test_current_credential_and_owner_are_required_for_new_admission(ops, monkeypatch, change):
    data = await prepared(ops, monkeypatch)
    if change == 'revoked':
        api_auth.revoke(ops.db, OWNER.actor)
    elif change == 'expired':
        ops.db.execute('UPDATE api_principals SET expires_at=?', (githubtests.NOW - 1,))
    elif change == 'partial_scopes':
        api_auth.revoke(ops.db, OWNER.actor)
        api_auth.issue(ops.db, OWNER.actor, ['observe'])
        api_auth.issue(ops.db, OWNER.actor, ['manage'])
    else:
        ops.db.execute("UPDATE pm_workspace_members SET role='contributor' WHERE workspace_id=? AND actor=?", (data['workspace_id'], OWNER.actor))
    ops.context['check_test_clock'][0] += 240
    assert checks.tick(ops)['scheduled'] == 0
    with pytest.raises(OperationError):
        ops.create(OWNER, **request(ops, data))
    assert checks.schedule_status(ops, READER, data['workspace_id'])['schedule']['blocked_reason'] is not None
    assert ops.context['github'].reads == []


async def test_contributor_and_nonowner_admin_cannot_borrow_original_policy_controller(ops, monkeypatch):
    data = await prepared(ops, monkeypatch)
    for caller in (CONTRIBUTOR, api_auth.Principal('local-admin', frozenset(), admin=True)):
        with pytest.raises(OperationError):
            ops.create(caller, **request(ops, data))


async def test_live_credential_rotation_for_same_controller_is_supported(ops, monkeypatch):
    data = await prepared(ops, monkeypatch)
    parent = ops.create(OWNER, **request(ops, data))[0]
    api_auth.revoke(ops.db, OWNER.actor)
    api_auth.issue(ops.db, OWNER.actor, ['observe', 'manage'])
    assert (await finish(ops, parent))['result']['outcome'] == 'complete'


async def test_budget_reservation_precedes_children_and_is_not_refunded_after_completion(ops, monkeypatch):
    settings = githubtests.worktests.settings()
    settings['limits']['period_runs'] = 1
    data = await prepared(ops, monkeypatch, document=settings)
    one = ops.create(OWNER, **request(ops, data))[0]
    two = ops.create(OWNER, **request(ops, data))[0]
    await ops._execute(one['operation_id'])
    await ops._execute(two['operation_id'])
    assert ops.get(two['operation_id'])['error_code'] == 'PM_MAINTENANCE_BUDGET_EXHAUSTED'
    assert ops.db.execute('SELECT COUNT(*) FROM pm_maintenance_check_runs').fetchone()[0] == 1
    assert ops.db.execute('SELECT COUNT(*) FROM pm_maintenance_check_children').fetchone()[0] == 1
    assert (await finish(ops, one))['status'] == 'succeeded'
    three = ops.create(OWNER, **request(ops, data))[0]
    assert (await finish(ops, three))['error_code'] == 'PM_MAINTENANCE_BUDGET_EXHAUSTED'
    ops.context['check_test_clock'][0] += 240
    assert checks.tick(ops)['scheduled'] == 0
    assert checks.schedule_status(ops, OWNER, data['workspace_id'])['schedule']['blocked_reason'] == 'PM_MAINTENANCE_BUDGET_EXHAUSTED'
    ops.context['check_test_clock'][0] += 3600
    assert checks.tick(ops)['scheduled'] == 1


@pytest.mark.parametrize('change', ['pause', 'reopen', 'revoked', 'expired', 'deadline', 'source'])
async def test_late_stop_between_children_prevents_new_capture_and_preserves_first_receipt(ops, monkeypatch, change):
    data = await prepared(ops, monkeypatch)
    parent = ops.create(OWNER, **request(ops, data))[0]
    await ops._execute(parent['operation_id'])
    first = checks._child(ops, parent['operation_id'], 'github')
    await ops._execute(first['operation_id'])
    assert ops.get(first['operation_id'])['status'] == 'succeeded'
    calls = len(ops.context['github'].reads)
    if change in {'pause', 'reopen'}:
        await decide(ops, data['workspace_id'])
        if change == 'reopen':
            await decide(ops, data['workspace_id'], 'reopen', params={'stage': 'development'})
    elif change == 'revoked':
        api_auth.revoke(ops.db, OWNER.actor)
    elif change == 'expired':
        ops.db.execute('UPDATE api_principals SET expires_at=?', (githubtests.NOW - 1,))
    elif change == 'deadline':
        ops.context['check_test_clock'][0] += 121
    else:
        await remove_repo(ops, data['project_id'])
    await ops._execute(parent['operation_id'])
    completed = ops.get(parent['operation_id'])
    assert completed['status'] == 'succeeded', completed
    assert completed['result']['outcome'] == 'stopped'
    assert checks._child(ops, parent['operation_id'], 'workload') is None
    assert len(ops.context['github'].reads) == calls
    row = checks._row(ops, parent['operation_id'])
    assert row['finished_at'] is not None
    assert row['document']['children'][0]['operation_id'] == first['operation_id']


async def test_child_guard_rejects_forgery_changed_envelope_deadline_and_cancelled_parent(ops, monkeypatch):
    data = await prepared(ops, monkeypatch)
    parent = ops.create(OWNER, **request(ops, data))[0]
    await ops._execute(parent['operation_id'])
    child = checks._child(ops, parent['operation_id'], 'github')
    checks.require_child(ops, child)
    fake = copy.deepcopy(child)
    fake['operation_id'] = 'op_' + 'f' * 32
    with pytest.raises(OperationError, match='parent link'):
        checks.require_child(ops, fake)
    changed = copy.deepcopy(child)
    changed['preconditions']['expected_policy_version'] += 1
    with pytest.raises(OperationError, match='identity changed'):
        checks.require_child(ops, changed)
    ops.context['check_test_clock'][0] += 121
    with pytest.raises(OperationError, match='deadline'):
        checks.require_child(ops, child)
    ops.context['check_test_clock'][0] -= 121
    ops.cancel(OWNER, parent['operation_id'])
    with pytest.raises(OperationError, match='stopped new steps'):
        checks.require_child(ops, child)
    assert checks.tick(ops)['reconciled'] == 0
    await ops._execute(child['operation_id'])
    assert ops.get(child['operation_id'])['status'] == 'failed'
    assert checks.tick(ops)['reconciled'] == 1
    assert checks._row(ops, parent['operation_id'])['finished_at'] is not None
    assert ops.context['github'].reads == []


async def test_original_key_recovers_after_new_policy_or_credential_revocation_without_refetch(ops, monkeypatch):
    data = await prepared(ops, monkeypatch)
    original = request(ops, data)
    parent = ops.create(OWNER, **original)[0]
    done = await finish(ops, parent)
    calls = len(ops.context['github'].reads)
    api_auth.revoke(ops.db, OWNER.actor)
    replay, created = ops.create(api_auth.Principal(OWNER.actor, frozenset({'manage'})), **original)
    assert not created and replay['result'] == done['result']
    assert await checks._run(OpContext(ops, done)) == done['result']
    assert len(ops.context['github'].reads) == calls


async def test_original_source_withdrawal_hides_run_current_history_operation_and_schedule(ops, monkeypatch):
    data = await prepared(ops, monkeypatch)
    ops.context['check_test_clock'][0] += 120
    assert checks.tick(ops)['scheduled'] == 1
    parent = ops.get(ops.db.execute('SELECT operation_id FROM pm_maintenance_check_windows').fetchone()[0])
    done = await finish(ops, parent)
    run_id = done['result']['run_id']
    await remove_repo(ops, data['project_id'])
    assert not checks.operation_visible(ops.db, OWNER, done)
    assert checks.current_run(ops, OWNER, data['workspace_id']) == {'workspace_id': data['workspace_id'], 'run': None, 'visibility_filtered': True}
    assert checks.list_runs(ops, OWNER, data['workspace_id'])['runs'] == []
    assert checks.schedule_status(ops, OWNER, data['workspace_id'])['visibility_filtered']
    with pytest.raises(OperationError):
        checks.get_run(ops, OWNER, data['workspace_id'], run_id)


async def test_whole_record_pagination_cursor_bounds_and_filtered_page_progress(ops, monkeypatch):
    data = await prepared(ops, monkeypatch)
    one, _ = await check(ops, data)
    await check(ops, data)
    page = checks.list_runs(ops, OWNER, data['workspace_id'], limit=1)
    assert page['runs'][0]['run_id'] == one['result']['run_id'] and page['next_cursor']
    assert len(checks.list_runs(ops, OWNER, data['workspace_id'], after=page['next_cursor'])['runs']) == 1
    for invalid in ({'after': True}, {'after': -1}, {'after': 2**63}, {'limit': 0}, {'limit': 101}):
        with pytest.raises(OperationError):
            checks.list_runs(ops, OWNER, data['workspace_id'], **invalid)
    monkeypatch.setattr(checks, 'MAX_READ_BYTES', checks.workload._size(page) + 64)
    assert len(checks.list_runs(ops, OWNER, data['workspace_id'])['runs']) == 1
    await remove_repo(ops, data['project_id'])
    hidden = checks.list_runs(ops, OWNER, data['workspace_id'], limit=1)
    assert hidden['runs'] == [] and hidden['next_cursor'] and hidden['visibility_filtered']


async def test_atomic_tick_rolls_back_parent_when_window_insert_fails(ops, monkeypatch):
    await prepared(ops, monkeypatch)
    ops.context['check_test_clock'][0] += 120
    ops.db.execute("CREATE TRIGGER fixture_window_failure BEFORE INSERT ON pm_maintenance_check_windows BEGIN SELECT RAISE(ABORT,'fixture rollback'); END")
    with pytest.raises(sqlite3.IntegrityError, match='fixture rollback'):
        checks.tick(ops)
    assert ops.db.execute('SELECT COUNT(*) FROM operations WHERE action=?', (checks.ACTION,)).fetchone()[0] == 0
    assert ops.db.execute('SELECT COUNT(*) FROM pm_maintenance_check_windows').fetchone()[0] == 0


async def test_pending_unknown_child_retains_capacity_after_parent_cancellation(ops, monkeypatch):
    data = await prepared(ops, monkeypatch)
    parent = ops.create(OWNER, **request(ops, data))[0]
    await ops._execute(parent['operation_id'])
    child = checks._child(ops, parent['operation_id'], 'github')
    ops._step_start(child['operation_id'], 'fixture.uncertain', {})
    ops._step_status(child['operation_id'], 'fixture.uncertain', 'uncertain')
    ops._transition(child['operation_id'], 'running')
    ops._transition(child['operation_id'], 'needs_attention', error_code='FIXTURE_UNCERTAIN')
    ops.cancel(OWNER, parent['operation_id'])
    assert checks.tick(ops)['reconciled'] == 0
    assert checks._row(ops, parent['operation_id'])['finished_at'] is None
    assert checks.schedule_status(ops, OWNER, data['workspace_id'])['schedule']['blocked_reason'] == 'PM_MAINTENANCE_BUDGET_EXHAUSTED'


async def test_secret_credential_and_provider_settings_never_enter_public_run_documents(ops, monkeypatch):
    data = await prepared(ops, monkeypatch)
    _, run = await check(ops, data)
    public = json.dumps(run)
    token_hash = ops.db.execute('SELECT token_hash FROM api_principals LIMIT 1').fetchone()[0]
    assert token_hash not in public
    assert 'token_ref' not in public and 'api_url' not in public
    assert 'fingerprint' not in public and 'wss://' not in public


async def test_tick_reserves_accepted_parent_before_later_windows_or_worker_execution(ops, monkeypatch):
    data = await prepared(ops, monkeypatch)
    clock = ops.context['check_test_clock']
    clock[0] += 120
    assert checks.tick(ops)['scheduled'] == 1
    assert ops.db.execute('SELECT COUNT(*) FROM pm_maintenance_check_runs').fetchone()[0] == 1
    assert ops.db.execute('SELECT COUNT(*) FROM pm_maintenance_check_children').fetchone()[0] == 0
    clock[0] += 120
    assert checks.tick(ops)['scheduled'] == 0
    assert checks.schedule_status(ops, OWNER, data['workspace_id'])['schedule']['blocked_reason'] == 'PM_MAINTENANCE_BUDGET_EXHAUSTED'
    assert ops.context['github'].reads == []
    parent = ops.get(ops.db.execute('SELECT operation_id FROM pm_maintenance_check_windows').fetchone()[0])
    # The accepted parent expired before the worker resumed; it never admits IO.
    done = await finish(ops, parent)
    assert done['result']['outcome'] == 'stopped'
    assert checks.tick(ops)['scheduled'] == 1


async def test_one_second_provider_budget_is_clamped_to_persisted_parent_deadline(ops, monkeypatch):
    settings = githubtests.worktests.settings()
    settings['limits']['run_seconds'] = 1
    data = await prepared(ops, monkeypatch, document=settings)
    parent = ops.create(OWNER, **request(ops, data))[0]
    await ops._execute(parent['operation_id'])
    child = checks._child(ops, parent['operation_id'], 'github')
    assert checks.child_timeout(ops, child, 30) == 1
    ops.context['check_test_clock'][0] += 0.75
    assert checks.child_timeout(ops, child, 30) == 0.25
    assert checks.child_timeout(ops, child, 0.1) == 0.1
    ops.context['check_test_clock'][0] += 0.25
    with pytest.raises(OperationError, match='deadline'):
        checks.child_timeout(ops, child, 30)
    manual = copy.deepcopy(child)
    manual['operation_id'] = 'op_' + 'e' * 32
    manual['external_refs'].pop('pm_maintenance_parent')
    assert checks.child_timeout(ops, manual, 30) == 30
    assert ops.context['github'].reads == []
