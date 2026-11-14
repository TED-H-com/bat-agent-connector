"""Registered report action, owned Git chains and immutable facts."""
import copy
import json
import sqlite3
import uuid
from pathlib import Path

import pytest

from bat_agent_connector import pm_plans, pm_roadmap, pm_roadmap_reports, pm_runs, pm_snapshots
from bat_agent_connector import pm_roadmap_report_materialization as reports
from bat_agent_connector.operations import OperationError
from tests.operation_helpers import settle_operations
from tests.test_checkpoints import git
from tests.test_checkpoints import snapshot as tree_snapshot
from tests.test_pm_evidence import capture, task
from tests.test_pm_plan_operations import OWNER, adopt, draft
from tests.test_pm_roadmap import revision
from tests.test_pm_snapshots import snapshot
from tests.test_pm_spec_materialization import fixed as spec_fixed_fixture
from tests.test_pm_spec_materialization import materialize as materialize_spec
from tests.test_pm_spec_materialization import pm, ready, world  # noqa: F401
from tests.test_pm_work_packages import action

spec_fixed = spec_fixed_fixture


@pytest.fixture
async def fixed(spec_fixed):
    w = spec_fixed
    ops, wid = w['d'].ops, w['workspace_id']
    assert reports.ACTION in ops.actions
    version = ops.db.execute('PRAGMA user_version').fetchone()[0]
    reports.install(ops)
    reports.install(ops)
    assert ops.db.execute('PRAGMA user_version').fetchone()[0] == version
    pm_roadmap.install(ops)
    pm_snapshots.install(ops)
    await revision(ops, wid, who=OWNER, title='Synthetic report schedule', milestones=[])
    w['source_task'] = task(ops)
    await action(w, 'work_item.link', {'work_item_id': w['target']['work_item_id']},
        {'kind': 'task', 'ref': w['source_task']['task_id']})
    await capture(ops, w['target'], who=OWNER)
    snap = await snapshot(ops, wid, who=OWNER)
    w['observation'] = await revision(ops, wid, 'observation', who=OWNER, snapshot_id=snap['snapshot_id'])
    return w


def request(w, format='markdown', locale='en'):
    ops, wid = w['d'].ops, w['workspace_id']
    target = {'workspace_id': wid, 'observation_revision_id': w['observation']['revision_id']}
    params = {'format': format, 'locale': locale, 'renderer_version': 1}
    report = pm_roadmap_reports.get_report(ops, OWNER, **target, **params)
    return {'action': reports.ACTION, 'target': target, 'params': params,
        'preconditions': {'expected_' + key: report[key] for key in ('source_set_digest', 'view_digest', 'output_digest')},
        'idempotency_key': uuid.uuid4().hex}


async def materialize(w, envelope=None):
    ops = w['d'].ops
    op, _ = ops.create(OWNER, **(envelope or request(w)))
    await settle_operations(ops)
    result = ops.get(op['operation_id'])
    assert result['status'] == 'succeeded', result
    return result


def record(w, op):
    return reports.get_materialization(w['d'].ops, OWNER, w['workspace_id'], op['result']['materialization_id'])['materialization']


@pytest.mark.parametrize('format,locale', [('markdown', 'en'), ('html', 'zh-TW'), ('svg', 'en')])
async def test_frozen_bytes_original_thread_and_same_key(fixed, format, locale):
    w, ops = fixed, fixed['d'].ops
    envelope = request(w, format, locale)
    expected = pm_roadmap_reports.get_report(ops, OWNER, **envelope['target'], **envelope['params'])
    human = tree_snapshot(w['human'])
    accepted = pm_plans.accepted_plan(ops, OWNER, w['workspace_id'])
    roadmap = pm_roadmap.head(ops.db, w['workspace_id'])
    op = await materialize(w, envelope)
    saved = record(w, op)
    assert saved['kind'] == 'roadmap_report_materialization'
    step = ops.db.execute("SELECT request,response FROM operation_steps WHERE operation_id=? AND name='pm.roadmap.report.freeze'", (op['operation_id'],)).fetchone()
    frozen = json.loads(step['response'])
    assert json.loads(step['request']) == {}
    carrier = Path(w['plan']['worktree_path'])
    for path, content in frozen['revision']['files'].items():
        assert (carrier / path).read_bytes() == content.encode()
        if '/report.' in path:
            assert content == expected['content']
    original = ops.db.execute('SELECT run_id FROM pm_workspace_worktrees WHERE workspace_id=?', (w['workspace_id'],)).fetchone()[0]
    run = pm_runs.get_by_operation(ops.db, op['operation_id'])
    assert run['thread_id'] == pm_runs.get(ops.db, original)['thread_id'] == frozen['thread_id']
    assert run['state'] == 'completed_service'
    assert pm_plans.latest_git_receipt(ops.db, w['workspace_id']) == saved['git_receipt']
    replay, created = ops.create(OWNER, **envelope)
    assert not created and replay['operation_id'] == op['operation_id']
    assert git(carrier, 'rev-parse', 'HEAD') == saved['git_receipt']['commit_sha']
    assert pm_plans.accepted_plan(ops, OWNER, w['workspace_id']) == accepted
    assert pm_roadmap.head(ops.db, w['workspace_id']) == roadmap
    assert tree_snapshot(w['human']) == human
    for sql in ('UPDATE pm_roadmap_report_materializations SET actor=actor', 'DELETE FROM pm_roadmap_report_materializations'):
        with pytest.raises(sqlite3.IntegrityError, match='immutable'):
            ops.db.execute(sql)
    altered = copy.deepcopy(envelope)
    altered['params']['locale'] = 'zh-TW' if locale == 'en' else 'en'
    with pytest.raises(OperationError, match='IDEMPOTENCY'):
        ops.create(OWNER, **altered)


async def test_plan_report_spec_plan_chain(fixed):
    w, ops = fixed, fixed['d'].ops
    before = pm_plans.latest_git_receipt(ops.db, w['workspace_id'])
    op = await materialize(w)
    spec = await materialize_spec(w)
    revision = await draft(w, requirements=w['requirements'])
    plan = await adopt(w, revision)
    await settle_operations(ops)
    assert ops.get(plan['operation_id'])['status'] == 'succeeded'
    tip = pm_plans.latest_git_receipt(ops.db, w['workspace_id'])
    assert tip['operation_id'] == plan['operation_id']
    carrier = Path(w['plan']['worktree_path'])
    assert git(carrier, 'rev-parse', 'HEAD~3') == before['commit_sha']
    assert git(carrier, 'rev-parse', 'HEAD~2') == op['result']['git_receipt']['commit_sha']
    assert git(carrier, 'rev-parse', 'HEAD~1') == spec['result']['git_receipt']['commit_sha']


async def test_no_report_preserves_legacy_parent(fixed):
    ops, wid = fixed['d'].ops, fixed['workspace_id']
    before = pm_plans.latest_git_receipt(ops.db, wid)
    assert before == pm_plans.original_git_receipt(ops.db, wid)
    assert ops.db.execute('SELECT COUNT(*) FROM pm_roadmap_report_materializations').fetchone()[0] == 0
    ops.db.execute('DROP TABLE pm_spec_materializations')
    assert pm_plans.latest_git_receipt(ops.db, wid) == before
    reports.schema(ops.journal)
    assert pm_plans.latest_git_receipt(ops.db, wid) == before


async def test_refusal_order_exact_intent_and_no_effects(fixed):
    from bat_agent_connector.api_auth import Principal
    from tests.test_pm_roadmap_report_materialization_visibility import withdraw
    w, ops = fixed, fixed['d'].ops
    envelope = request(w)
    counts = {table: ops.db.execute('SELECT COUNT(*) FROM ' + table).fetchone()[0] for table in ('operations', 'pm_runs', 'artifacts')}
    for mutation, expected in [
        (lambda e: e['params'].update(path='docs/pm/roadmap.yaml'), 'INVALID_PARAMS'),
        (lambda e: e['params'].update(renderer_version=True), 'INVALID_PARAMS'),
        (lambda e: e['params'].update(renderer_version=2), 'PM_EXPORT_TEMPLATE_UNAVAILABLE'),
        (lambda e: e['preconditions'].update(expected_output_digest='0' * 64), 'PM_REPORT_VIEW_CHANGED'),
        (lambda e: e['target'].update(observation_revision_id='invalid'), 'INVALID_PARAMS'),
    ]:
        value = copy.deepcopy(envelope)
        mutation(value)
        with pytest.raises(OperationError) as error:
            ops.create(OWNER, **value)
        assert error.value.code == expected
    for missing in ('observe', 'manage', 'start'):
        with pytest.raises(OperationError) as error:
            ops.create(Principal(OWNER.actor, OWNER.scopes - {missing}), **envelope)
        assert error.value.code == 'FORBIDDEN'
    assert counts == {table: ops.db.execute('SELECT COUNT(*) FROM ' + table).fetchone()[0] for table in counts}
    assert not w['runner'].phases
    withdraw(w)
    value = copy.deepcopy(envelope)
    value['preconditions']['expected_output_digest'] = '0' * 64
    with pytest.raises(OperationError) as error:
        ops.create(OWNER, **value)
    assert error.value.status == 404  # Source concealment wins over digest conflict.


async def test_failed_preflight_rolls_back_writer_and_freeze(fixed, monkeypatch):
    w, ops = fixed, fixed['d'].ops
    envelope = request(w)
    def oversized(*args, **kwargs):
        raise reports.pm_roadmap_report_files.too_large()
    monkeypatch.setattr(reports, '_preflight', oversized)
    op, _ = ops.create(OWNER, **envelope)
    await settle_operations(ops)
    done = ops.get(op['operation_id'])
    assert done['error_code'] == 'PM_REPORT_MATERIALIZATION_TOO_LARGE'
    assert pm_runs.get_by_operation(ops.db, op['operation_id']) is None
    assert ops.db.execute("SELECT response FROM operation_steps WHERE operation_id=? AND name='pm.roadmap.report.freeze'", (op['operation_id'],)).fetchone()[0] is None
    assert not w['runner'].phases
