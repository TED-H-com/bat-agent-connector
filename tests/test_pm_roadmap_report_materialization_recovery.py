"""Report recovery keeps the original intent and uncertain writer."""
import json
from pathlib import Path

import pytest

from bat_agent_connector import pm_roadmap_report_materialization as reports
from bat_agent_connector import pm_runs, pm_worktree
from bat_agent_connector.operations import OpContext, OperationError, Uncertain
from tests.operation_helpers import settle_operations
from tests.test_checkpoints import git, snapshot
from tests.test_pm_plan_operations import OWNER, interrupt_host
from tests.test_pm_roadmap_report_materialization import (  # noqa: F401
    fixed as fixed_fixture,
)
from tests.test_pm_roadmap_report_materialization import pm as pm_fixture
from tests.test_pm_roadmap_report_materialization import ready as ready_fixture
from tests.test_pm_roadmap_report_materialization import (
    record,
    request,
)
from tests.test_pm_roadmap_report_materialization import spec_fixed as spec_fixed_fixture
from tests.test_pm_roadmap_report_materialization import world as world_fixture
from tests.test_pm_spec_materialization_recovery import lose, revoke
from tests.test_pm_worktree import resume

fixed = fixed_fixture
pm = pm_fixture
ready = ready_fixture
spec_fixed = spec_fixed_fixture
world = world_fixture


@pytest.mark.parametrize('stop', ['cancel', 'revoke', 'source'])
async def test_complete_git_readback_after_stop(fixed, stop):
    w, ops = fixed, fixed['d'].ops
    envelope = request(w)
    op, _ = ops.create(OWNER, **envelope)
    def after(_):
        if stop == 'source':
            from tests.test_pm_roadmap_report_materialization_visibility import withdraw
            withdraw(w)
        else:
            ops.cancel(OWNER, op['operation_id']) if stop == 'cancel' else revoke(w)
        lose(None)
    w['runner'].after['pm-revision'] = after
    await settle_operations(ops)
    original = ops.get(op['operation_id'])
    assert original['status'] == 'uncertain', original
    frozen = ops.db.execute("SELECT response FROM operation_steps WHERE operation_id=? AND name='pm.roadmap.report.freeze'", (op['operation_id'],)).fetchone()[0]
    done = await resume(w, original)
    assert done['status'] == 'succeeded', done
    assert w['runner'].phases.count('pm-revision') == 1
    assert ops.db.execute('SELECT COUNT(*) FROM pm_roadmap_report_materializations').fetchone()[0] == 1
    assert pm_runs.get_by_operation(ops.db, op['operation_id'])['state'] == 'completed_service'
    assert await reports._run(OpContext(ops, done)) == done['result']
    assert frozen == ops.db.execute("SELECT response FROM operation_steps WHERE operation_id=? AND name='pm.roadmap.report.freeze'", (op['operation_id'],)).fetchone()[0]
    if stop in {'revoke', 'source'}:
        with pytest.raises(OperationError):
            record(w, done)


def fault(w, monkeypatch, point):
    if point in {'intent', 'objects', 'files', 'branch', 'index'}:
        interrupt_host(w, monkeypatch, point)
        return
    original = pm_worktree._host_source
    armed = [True]
    anchors = {
        'before_intent': '    if not existing:\n        rows.append(proposed)\n',
        'first_object': '        if actual != sha:\n',
        'first_file': '                pm_write(fd, relative.rsplit("/", 1)[1], content)\n',
        'private_ref': '    current = rev_pending_guard(req, proposed, files)\n',
        'read_tree': '    git(req["worktree_path"], "update-index", "--refresh")\n',
        'complete': '    pm_carrier(req)\n    return receipt\n',
    }
    def source():
        text = original()
        if armed[0]:
            anchor = anchors[point]
            assert anchor in text
            if point == 'first_object':
                text = text.replace(anchor, '        raise ValueError("PM_HOST_UNCERTAIN")\n' + anchor)
            elif point == 'first_file':
                text = text.replace(anchor, anchor + '                raise ValueError("PM_HOST_UNCERTAIN")\n')
            else:
                text = text.replace(anchor, '    raise ValueError("PM_HOST_UNCERTAIN")\n' + anchor)
        return text
    monkeypatch.setattr(pm_worktree, '_host_source', source)
    w['runner'].before['pm-revision'] = lambda _: armed.__setitem__(0, False)


@pytest.mark.parametrize('point', ['before_intent', 'intent', 'first_object', 'objects', 'first_file', 'files', 'private_ref', 'branch', 'read_tree', 'index', 'complete'])
async def test_each_host_boundary_recovers_original_bytes(fixed, monkeypatch, point):
    w, ops = fixed, fixed['d'].ops
    fault(w, monkeypatch, point)
    envelope = request(w)
    op, _ = ops.create(OWNER, **envelope)
    await settle_operations(ops)
    original = ops.get(op['operation_id'])
    assert original['status'] == 'uncertain', original
    assert not ops.db.execute('SELECT 1 FROM pm_roadmap_report_materializations').fetchone()
    run = pm_runs.get_by_operation(ops.db, op['operation_id'])
    assert pm_runs.require_writer(ops.journal, run['run_id'], run['generation'])
    assert ops.create(OWNER, **envelope)[0]['operation_id'] == op['operation_id']
    if point not in {'before_intent', 'complete'}:
        member = dict(ops.db.execute('SELECT * FROM pm_workspace_members WHERE workspace_id=? AND actor=?', (w['workspace_id'], OWNER.actor)).fetchone())
        revoke(w)
        stopped = await resume(w, original)
        assert stopped['status'] == 'uncertain', stopped
        assert w['runner'].phases.count('pm-revision') == 1
        ops.db.execute('INSERT INTO pm_workspace_members(' + ','.join(member) + ') VALUES(' + ','.join('?' for _ in member) + ')', tuple(member.values()))
    done = await resume(w, original)
    assert done['status'] == 'succeeded', done
    assert git(w['plan']['worktree_path'], 'rev-list', '--count', w['sha'] + '..HEAD') == '2'
    assert record(w, done)['operation_id'] == original['operation_id']


@pytest.mark.parametrize('point', ['before_freeze', 'after_freeze', 'after_git', 'record_transaction', 'after_record'])
async def test_local_boundaries_replay_and_transaction_rollback(fixed, monkeypatch, point):
    w, ops = fixed, fixed['d'].ops
    original = OpContext.effect
    once = [True]
    def effect(ctx, name, fn, **kwargs):
        if ctx.op['action'] != reports.ACTION or not once[0]:
            return original(ctx, name, fn, **kwargs)
        if point == 'before_freeze' and name.endswith('.freeze') or point == 'after_git' and name.endswith('.record'):
            once[0] = False
            raise Uncertain(name, 'Synthetic local interruption')
        if point == 'record_transaction' and name.endswith('.record'):
            def failed():
                fn()
                once[0] = False
                raise Uncertain(name, 'Synthetic local interruption')
            return original(ctx, name, failed, **kwargs)
        result = original(ctx, name, fn, **kwargs)
        if (point == 'after_freeze' and name.endswith('.freeze') or point == 'after_record' and name.endswith('.record')):
            once[0] = False
            raise Uncertain(name, 'Synthetic local interruption')
        return result
    monkeypatch.setattr(OpContext, 'effect', effect)
    op, _ = ops.create(OWNER, **request(w))
    await settle_operations(ops)
    original_op = ops.get(op['operation_id'])
    assert original_op['status'] != 'succeeded'
    assert bool(ops.db.execute('SELECT 1 FROM pm_roadmap_report_materializations').fetchone()) == (point == 'after_record')
    # Reopen durable state in a new service, without preserving a worker/cache.
    from bat_agent_connector.operations import OperationService
    from bat_agent_connector.task_journal import Journal
    restarted = Journal(ops.db.execute('PRAGMA database_list').fetchone()['file'])
    service = OperationService(restarted, actions=list(ops.actions.values()))
    service.context.update(ops.context)
    try:
        await service._execute(op['operation_id'])
        done = service.get(op['operation_id'])
    finally:
        restarted.close()
    assert done['status'] == 'succeeded', done
    assert w['runner'].phases.count('pm-revision') == 1


@pytest.mark.parametrize('bad', ['foreign_file', 'index', 'symlink'])
async def test_foreign_carrier_state_remains_untouched(fixed, bad):
    w, ops = fixed, fixed['d'].ops
    carrier = Path(w['plan']['worktree_path'])
    before = git(carrier, 'rev-parse', 'HEAD')
    envelope = request(w)
    op, _ = ops.create(OWNER, **envelope)
    if bad == 'foreign_file':
        (carrier / 'manual.txt').write_text('Synthetic unregistered draft')
    elif bad == 'index':
        (carrier / 'hello.txt').write_text('Synthetic staged edit')
        git(carrier, 'add', 'hello.txt')
    else:
        parent = carrier / 'docs/pm/reports'
        parent.mkdir(exist_ok=True)
        (parent / ('pmrpt_' + op['operation_id'][3:])).symlink_to(w['human'], target_is_directory=True)
    human = snapshot(w['human'])
    await settle_operations(ops)
    done = ops.get(op['operation_id'])
    assert done['status'] in {'failed', 'needs_attention'}, done
    assert not ops.db.execute('SELECT 1 FROM pm_roadmap_report_materializations').fetchone()
    assert git(carrier, 'rev-parse', 'HEAD') == before and snapshot(w['human']) == human
    assert pm_runs.get_by_operation(ops.db, op['operation_id'])['state'] not in pm_runs.TERMINAL


async def test_tampered_successful_step_cannot_record_report(fixed, monkeypatch):
    w, ops = fixed, fixed['d'].ops
    original = OpContext.effect
    def effect(ctx, name, fn, **kwargs):
        if name == 'pm.roadmap.report.record':
            raise Uncertain(name, 'Synthetic local interruption')
        return original(ctx, name, fn, **kwargs)
    monkeypatch.setattr(OpContext, 'effect', effect)
    op, _ = ops.create(OWNER, **request(w))
    await settle_operations(ops)
    step = ops.db.execute("SELECT response FROM operation_steps WHERE operation_id=? AND name='pm.revision.commit'", (op['operation_id'],)).fetchone()
    value = json.loads(step[0])
    value['commit_sha'] = 'short'
    ops.db.execute("UPDATE operation_steps SET response=? WHERE operation_id=? AND name='pm.revision.commit'", (json.dumps(value), op['operation_id']))
    monkeypatch.setattr(OpContext, 'effect', original)
    done = await resume(w, op)
    assert done['status'] == 'needs_attention', done
    assert not ops.db.execute('SELECT 1 FROM pm_roadmap_report_materializations').fetchone()


@pytest.mark.parametrize('complete', [False, True])
async def test_lifecycle_stop_reconciles_only_complete_original_effect(fixed, monkeypatch, complete):
    from bat_agent_connector import pm_lifecycle
    from tests.test_pm_lifecycle import decide
    w, ops = fixed, fixed['d'].ops
    pm_lifecycle.install(ops)
    if complete:
        w['runner'].after['pm-revision'] = lose
    else:
        fault(w, monkeypatch, 'branch')
    envelope = request(w)
    op, _ = ops.create(OWNER, **envelope)
    await settle_operations(ops)
    original = ops.get(op['operation_id'])
    assert original['status'] == 'uncertain'
    await decide(ops, w['workspace_id'], who=OWNER)
    done = await resume(w, original)
    assert done['status'] == ('succeeded' if complete else 'uncertain'), done
    assert w['runner'].phases.count('pm-revision') == 1
    if not complete:
        run = pm_runs.get_by_operation(ops.db, op['operation_id'])
        assert pm_runs.require_writer(ops.journal, run['run_id'], run['generation'])
        await decide(ops, w['workspace_id'], 'reopen', who=OWNER, params={'stage': 'planning'})
        assert (await resume(w, original))['status'] == 'uncertain'
        second, _ = ops.create(OWNER, **request(w))
        await settle_operations(ops)
        assert ops.get(second['operation_id'])['error_code'] == 'PM_WRITER_BUSY'


async def test_source_withdrawal_at_locked_frame_stops_new_effect_retains_freeze(fixed):
    from tests.test_pm_roadmap_report_materialization_visibility import withdraw
    w, ops = fixed, fixed['d'].ops
    envelope = request(w)
    op, _ = ops.create(OWNER, **envelope)
    w['runner'].before['pm-revision'] = lambda _: withdraw(w)
    before = git(w['plan']['worktree_path'], 'rev-parse', 'HEAD')
    await settle_operations(ops)
    done = ops.get(op['operation_id'])
    assert done['status'] != 'succeeded', done
    assert not ops.db.execute('SELECT 1 FROM pm_roadmap_report_materializations').fetchone()
    assert ops.db.execute("SELECT response FROM operation_steps WHERE operation_id=? AND name='pm.roadmap.report.freeze'", (op['operation_id'],)).fetchone()[0]
    assert git(w['plan']['worktree_path'], 'rev-parse', 'HEAD') == before
    assert pm_runs.get_by_operation(ops.db, op['operation_id'])['state'] == 'completed_service'
    envelope['preconditions']['expected_output_digest'] = '0' * 64
    with pytest.raises(OperationError) as error:
        ops.create(OWNER, **envelope)
    assert error.value.code == 'NOT_FOUND'


@pytest.mark.parametrize('reply', ['unavailable', 'unsafe', 'missing', 'malformed'])
async def test_unproven_readback_keeps_original_writer(fixed, monkeypatch, reply):
    w, ops = fixed, fixed['d'].ops
    w['runner'].after['pm-revision'] = lose
    op, _ = ops.create(OWNER, **request(w))
    await settle_operations(ops)
    original_call = pm_worktree._host_call
    async def call(ctx, plan, phase, **kwargs):
        if phase == 'read.pm-revision':
            if reply == 'unavailable':
                lose(None)
            if reply == 'unsafe':
                return {'resume_safe': False, 'effect_started': True}
            if reply == 'missing':
                return {'resume_safe': True}
            return {'receipt': {'status': 'verified'}}
        return await original_call(ctx, plan, phase, **kwargs)
    monkeypatch.setattr(pm_worktree, '_host_call', call)
    done = await resume(w, op)
    assert done['status'] in {'uncertain', 'needs_attention'}, done
    assert not ops.db.execute('SELECT 1 FROM pm_roadmap_report_materializations').fetchone()
    run = pm_runs.get_by_operation(ops.db, op['operation_id'])
    assert pm_runs.require_writer(ops.journal, run['run_id'], run['generation'])
    assert w['runner'].phases.count('pm-revision') == 1


async def test_after_host_proof_before_step_commit_recovers_without_resend(fixed, monkeypatch):
    w, ops = fixed, fixed['d'].ops
    original, once = ops._step_done, [True]
    def done(identity, name, response, **kwargs):
        if name == 'pm.revision.commit' and once[0]:
            once[0] = False
            raise Uncertain(name, 'Synthetic interruption before journal step commit')
        return original(identity, name, response, **kwargs)
    monkeypatch.setattr(ops, '_step_done', done)
    op, _ = ops.create(OWNER, **request(w))
    await settle_operations(ops)
    assert ops.get(op['operation_id'])['status'] == 'uncertain'
    assert ops.db.execute("SELECT status FROM operation_steps WHERE operation_id=? AND name='pm.revision.commit'", (op['operation_id'],)).fetchone()[0] == 'started'
    assert (await resume(w, op))['status'] == 'succeeded'
    assert w['runner'].phases.count('pm-revision') == 1
