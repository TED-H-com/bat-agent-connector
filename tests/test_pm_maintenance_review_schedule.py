"""Weekly calendar windows and current delegated authority without a new engine."""
from __future__ import annotations

import datetime as dt
import sqlite3
from types import SimpleNamespace

import pytest

from bat_agent_connector import api_auth, pm_lifecycle_gates, pm_snapshots
from bat_agent_connector import pm_maintenance_checks as checks
from bat_agent_connector import pm_maintenance_review as review
from bat_agent_connector import pm_maintenance_review_schedule as weekly
from bat_agent_connector.operations import OpContext, OperationError
from tests import test_pm_maintenance_checks as ct
from tests.test_pm_evidence import OWNER, READER
from tests.test_pm_lifecycle import decide
from tests.test_pm_release_baselines import remove_repo

base_ops = ct.base_ops
release_ops = ct.release_ops
workload_ops = ct.workload_ops
github_ops = ct.github_ops
check_ops = ct.ops


@pytest.fixture
def ops(check_ops, monkeypatch):
    review.install(check_ops)
    clock = check_ops.context['check_test_clock']
    for module in (review, pm_snapshots):
        monkeypatch.setattr(module, 'time', SimpleNamespace(time=lambda: clock[0]))
    return check_ops


def at(text):
    return dt.datetime.fromisoformat(text.replace('Z', '+00:00')).timestamp()


def calendar(created, *, timezone='America/New_York', weekday=6, local_time='02:30'):
    return {'created_at': at(created), 'document': {'policy': {'schedule': {
        'timezone': timezone, 'review_weekday': weekday, 'review_local_time': local_time}}}}


def test_dst_gap_fold_and_first_boundary_use_one_original_instant():
    record = calendar('2026-03-01T09:00:00Z')
    gap = weekly.timing(record, at('2026-03-08T07:30:00Z'))
    assert gap['scheduled_at'] == at('2026-03-08T07:00:00Z')
    assert gap['advanced_local_minutes'] == 30 and gap['window'] == 1
    assert gap['next_due'] == at('2026-03-15T06:30:00Z')
    record = calendar('2026-10-25T08:00:00Z', local_time='01:30')
    before = weekly.timing(record, at('2026-11-01T05:00:00Z'))
    assert before['window'] == 0 and before['next_due'] == at('2026-11-01T05:30:00Z')
    first = weekly.timing(record, at('2026-11-01T05:45:00Z'))
    second = weekly.timing(record, at('2026-11-01T06:45:00Z'))
    assert first == second and first['scheduled_at'] == at('2026-11-01T05:30:00Z')
    same = calendar('2026-11-01T05:30:00Z', local_time='01:30')
    assert weekly.timing(same, same['created_at'])['window'] == 0
    with pytest.raises(OperationError):
        weekly.timing(record, record['created_at'] - 1)


async def due(ops, monkeypatch, *, weeks=1, document=None):
    data = await ct.prepared(ops, monkeypatch, document=document)
    record = checks.policies._record(ops.db, data['workspace_id'], data['policy_id'])
    clock = ops.context['check_test_clock']
    clock[0] = weekly.timing(record, clock[0])['next_due'] + (weeks - 1) * 7 * 86400 + 1
    return data


async def prepare_due(ops, monkeypatch, **kwargs):
    data = await due(ops, monkeypatch, **kwargs)
    tick = checks.tick(ops)
    assert tick['weekly_scheduled'] == 1 and tick['scheduled'] == 0
    saved = ops.db.execute('SELECT * FROM pm_maintenance_review_windows ORDER BY sequence DESC LIMIT 1').fetchone()
    op = ops.get(saved['operation_id'])
    return data, saved, op


async def test_one_atomic_window_report_and_receipt_no_model_or_provider_refresh(ops, monkeypatch):
    data, saved, op = await prepare_due(ops, monkeypatch)
    original = ops.db.execute('PRAGMA user_version').fetchone()[0]
    weekly.schema(ops.journal)
    assert checks.capabilities(ops)['weekly_review_execution']
    assert ops.db.execute('PRAGMA user_version').fetchone()[0] == original
    assert checks.tick(ops)['weekly_scheduled'] == 0
    assert checks.tick(ops)['scheduled'] == 0  # queued report holds the shared slot
    before = len(ops.context['github'].reads)
    await ops._execute(op['operation_id'])
    done = ops.get(op['operation_id'])
    assert done['status'] == 'succeeded', done
    view = review.get_review(ops, OWNER, data['workspace_id'], done['result']['review_id'])
    context = view['snapshot']['maintenance']['report']['schedule']
    assert context['window'] == 1 and context['scheduled_at'] == saved['scheduled_at']
    assert context['meeting_completed'] is False and context['missed_weeks_checked'] is False
    assert view['review']['status'] == 'prepared' and view['completion'] is None
    assert len(ops.context['github'].reads) == before
    final = weekly._row(ops, op['operation_id'])
    assert final['outcome'] == 'prepared' and final['review_id'] == done['result']['review_id']
    assert checks.schedule_status(ops, READER, data['workspace_id'])['schedule']['weekly_review']['pending_report'] == final['review_id']
    for query in ("DELETE FROM pm_maintenance_review_windows", "UPDATE pm_maintenance_review_windows SET outcome='other'",
                  'UPDATE pm_maintenance_review_windows SET deadline_at=0'):
        with pytest.raises(sqlite3.IntegrityError, match='immutable'):
            ops.db.execute(query)
    await remove_repo(ops, data['project_id'])
    ctx = OpContext(ops, done)
    assert await review._run(ctx) == done['result']
    assert ctx.replayed == ['pm.maintenance_review.record']


async def test_missed_weeks_coalesce_latest_week_and_prior_pending_discussion(ops, monkeypatch):
    data, saved, op = await prepare_due(ops, monkeypatch, weeks=20)
    assert saved['window'] == 20 and saved['coalesced_windows'] == 19
    assert 6 * 86400 < saved['scheduled_at'] - saved['period_start'] < 8 * 86400
    await ops._execute(op['operation_id'])
    first = ops.get(op['operation_id'])
    clock = ops.context['check_test_clock']
    original_time = clock[0]
    clock[0] -= 7 * 86400
    assert not weekly.maybe_schedule(ops, checks.policies._record(ops.db, data['workspace_id'], data['policy_id']))
    clock[0] = original_time + 7 * 86400
    assert checks.tick(ops)['weekly_scheduled'] == 1
    new = ops.db.execute('SELECT * FROM pm_maintenance_review_windows ORDER BY sequence DESC LIMIT 1').fetchone()
    await ops._execute(new['operation_id'])
    done = ops.get(new['operation_id'])
    assert done['status'] == 'succeeded', done
    view = review.get_review(ops, OWNER, data['workspace_id'], done['result']['review_id'])
    previous = view['snapshot']['maintenance']['report']['previous_review']
    assert previous['review_id'] == first['result']['review_id'] and previous['unattended_report_remains_pending']
    assert previous['status'] == view['review']['status'] == 'prepared'
    assert len(review.list_reviews(ops, OWNER, data['workspace_id'])['reviews']) == 2


@pytest.mark.parametrize('change', ['deadline', 'pause', 'pause_reopen', 'credential', 'owner', 'source'])
async def test_queued_original_schedule_rechecks_before_publication_and_releases_without_retry(ops, monkeypatch, change):
    data, saved, op = await prepare_due(ops, monkeypatch)
    if change == 'deadline':
        ops.context['check_test_clock'][0] = saved['deadline_at']
    elif change in ('pause', 'pause_reopen'):
        await decide(ops, data['workspace_id'], 'pause')
        if change == 'pause_reopen':
            await decide(ops, data['workspace_id'], 'reopen', params={'stage': 'development'})
    elif change == 'credential':
        ops.db.execute('UPDATE api_principals SET revoked_at=1')
    elif change == 'owner':
        ops.db.execute("UPDATE pm_workspace_members SET role='contributor' WHERE workspace_id=? AND actor=?", (data['workspace_id'], OWNER.actor))
    else:
        await remove_repo(ops, data['project_id'])
    await ops._execute(op['operation_id'])
    assert ops.get(op['operation_id'])['status'] == 'failed'
    assert ops.db.execute('SELECT COUNT(*) FROM pm_maintenance_reviews').fetchone()[0] == 0
    assert ops.db.execute('SELECT COUNT(*) FROM pm_document_snapshots').fetchone()[0] == 0
    result = checks.tick(ops)
    assert result['weekly_reconciled'] == 1 and result['weekly_scheduled'] == 0
    assert weekly._row(ops, op['operation_id'])['outcome'] == 'stopped'
    assert ops.db.execute('SELECT COUNT(*) FROM pm_maintenance_review_windows').fetchone()[0] == 1


async def test_deadline_during_saved_report_build_rolls_back_report_and_snapshot(ops, monkeypatch):
    _, saved, op = await prepare_due(ops, monkeypatch)
    original = pm_snapshots._build
    def expire(*args):
        result = original(*args)
        ops.context['check_test_clock'][0] = saved['deadline_at']
        return result
    monkeypatch.setattr(pm_snapshots, '_build', expire)
    await ops._execute(op['operation_id'])
    assert ops.get(op['operation_id'])['error_code'] == 'PM_REVIEW_SCHEDULE_DEADLINE'
    assert ops.db.execute('SELECT COUNT(*) FROM pm_document_snapshots').fetchone()[0] == 0


async def test_atomic_window_failure_rolls_back_prepare_operation_and_reservation(ops, monkeypatch):
    await due(ops, monkeypatch)
    before = ops.db.execute('SELECT COUNT(*) FROM operations').fetchone()[0]
    ops.db.execute("""CREATE TRIGGER fixture_window_failure BEFORE INSERT ON pm_maintenance_review_windows
        BEGIN SELECT RAISE(ABORT, 'fixture window'); END""")
    with pytest.raises(sqlite3.IntegrityError, match='fixture window'):
        checks.tick(ops)
    assert ops.db.execute('SELECT COUNT(*) FROM operations').fetchone()[0] == before
    assert ops.db.execute('SELECT COUNT(*) FROM pm_maintenance_review_windows').fetchone()[0] == 0
    ops.db.execute('DROP TRIGGER fixture_window_failure')
    assert checks.tick(ops)['weekly_scheduled'] == 1


@pytest.mark.parametrize('stage', ['idea', 'planning', 'paused', 'closed'])
async def test_no_automatic_report_in_stopped_or_unselected_stages(ops, monkeypatch, stage):
    data = await due(ops, monkeypatch)
    if stage in ('idea', 'planning'):
        # Current stage is an actual lifecycle decision, not a UI draft hint.
        await decide(ops, data['workspace_id'], 'set_stage', params={'stage': stage})
    else:
        await decide(ops, data['workspace_id'], 'pause' if stage == 'paused' else 'close')
    assert checks.tick(ops)['weekly_scheduled'] == 0
    assert ops.db.execute('SELECT COUNT(*) FROM pm_maintenance_review_windows').fetchone()[0] == 0


async def test_shared_period_budget_and_existing_check_reservation_bound_weekly_reports(ops, monkeypatch):
    document = ct.githubtests.worktests.settings()
    document['limits'].update(period_seconds=31 * 86400, period_runs=1)
    data = await due(ops, monkeypatch, document=document)
    # A check admitted before this due visit owns the same concurrency/budget.
    parent = ops.create(OWNER, **ct.request(ops, data))[0]
    checks._reserve(OpContext(ops, parent))
    assert checks.tick(ops)['weekly_scheduled'] == 0
    await ct.finish(ops, parent)
    assert checks.tick(ops)['weekly_scheduled'] == 0  # finishing releases concurrency, not period budget
    assert checks.schedule_status(ops, OWNER, data['workspace_id'])['schedule']['weekly_review']['blocked_reason'] == 'PM_MAINTENANCE_BUDGET_EXHAUSTED'


async def test_no_execution_claim_without_registered_review_action(check_ops, monkeypatch):
    data = await ct.prepared(check_ops, monkeypatch)
    assert checks.capabilities(check_ops)['weekly_review_execution'] is False
    result = checks.schedule_status(check_ops, READER, data['workspace_id'])
    assert result['schedule']['weekly_review']['execution_available'] is False


async def test_current_credential_rotation_supports_original_controller(ops, monkeypatch):
    data, _, op = await prepare_due(ops, monkeypatch)
    ops.db.execute('UPDATE api_principals SET revoked_at=1')
    api_auth.issue(ops.db, OWNER.actor, ['observe', 'manage'])
    await ops._execute(op['operation_id'])
    assert ops.get(op['operation_id'])['status'] == 'succeeded'
    assert pm_lifecycle_gates.head(ops.db, data['workspace_id'])['stage'] == 'development'


async def test_readable_source_limit_starts_independent_report_with_explicit_continuity_gap(ops, monkeypatch):
    import json

    from bat_agent_connector import pm_maintenance_review_sources as sources
    from tests import test_pm_maintenance_review as rt
    data = await ct.prepared(ops, monkeypatch)
    clock = ops.context['check_test_clock']
    clock[0] += 10
    await ct.githubtests.observe(ops, data)
    await ct.githubtests.observe(ops, data)
    _, initial = await rt.prepare(ops, data)
    old_id = initial['review']['review_id']
    record = checks.policies._record(ops.db, data['workspace_id'], data['policy_id'])
    clock[0] = weekly.timing(record, clock[0])['next_due'] + 7 * 86400 + 1
    # These saved observations precede the latest due boundary and are inside
    # its period; the old observations are outside that period.
    source_clock = SimpleNamespace(time=lambda: clock[0] - 2, monotonic=lambda: clock[0] - 2)
    monkeypatch.setattr(ct.githubtests.observation, 'time', source_clock)
    monkeypatch.setattr(ct.githubtests.provider, 'time', source_clock)
    await ct.githubtests.observe(ops, data)
    await ct.githubtests.observe(ops, data)
    monkeypatch.setattr(sources, 'MAX_REFS', 5)
    before = ops.db.execute("SELECT COUNT(*) FROM operations WHERE action='pm.maintenance_review.prepare'").fetchone()[0]
    assert checks.tick(ops)['weekly_scheduled'] == 1
    saved = ops.db.execute('SELECT operation_id FROM pm_maintenance_review_windows').fetchone()
    op = ops.get(saved[0])
    assert 'previous_review_id' not in op['params']
    assert old_id not in json.dumps(op)
    assert ops.db.execute("SELECT COUNT(*) FROM operations WHERE action='pm.maintenance_review.prepare'").fetchone()[0] == before + 1
    await ops._execute(op['operation_id'])
    done = ops.get(op['operation_id'])
    assert done['status'] == 'succeeded', done
    result = review.get_review(ops, OWNER, data['workspace_id'], done['result']['review_id'])
    report = result['snapshot']['maintenance']['report']
    assert report['previous_context'] == {'status': 'omitted_source_limit', 'comparison_available': False}
    assert report['previous_review'] is None and report['changes']['status'] == 'comparison_unavailable'
    assert report['coverage']['github']['samples'] == 2
    assert old_id not in json.dumps(result)
    assert review.get_review(ops, OWNER, data['workspace_id'], old_id)['review']['status'] == 'prepared'
    await remove_repo(ops, data['project_id'])
    with pytest.raises(OperationError):
        review.get_review(ops, OWNER, data['workspace_id'], done['result']['review_id'])


async def test_source_withdrawal_never_uses_source_limit_fallback(ops, monkeypatch):
    import json

    from bat_agent_connector import pm_maintenance_review_sources as sources
    from tests import test_pm_maintenance_review as rt
    from tests.test_pm_evidence import OUTSIDER, bind_work, capture, item, link, source_operation, workspace
    data = await ct.prepared(ops, monkeypatch)
    clock = ops.context['check_test_clock']
    clock[0] += 10
    work = await item(ops, data['project_id'])
    target = {'workspace_id': data['workspace_id'], 'work_item_id': work}
    await bind_work(ops, target)
    original = await source_operation(ops)
    await link(ops, target, 'operation', original['operation_id'])
    await capture(ops, target)
    await rt.prepare(ops, data)
    private = await workspace(ops, OUTSIDER)
    ops.db.execute("UPDATE operations SET action='pm.private',target=? WHERE operation_id=?",
        (json.dumps({'workspace_id': private['workspace_id']}), original['operation_id']))
    record = checks.policies._record(ops.db, data['workspace_id'], data['policy_id'])
    clock[0] = weekly.timing(record, clock[0])['next_due'] + 1
    monkeypatch.setattr(sources, 'MAX_REFS', 2)
    assert checks.tick(ops)['weekly_scheduled'] == 0
    assert ops.db.execute('SELECT COUNT(*) FROM pm_maintenance_review_windows').fetchone()[0] == 0
    assert ops.db.execute('SELECT COUNT(*) FROM pm_maintenance_reviews').fetchone()[0] == 1
