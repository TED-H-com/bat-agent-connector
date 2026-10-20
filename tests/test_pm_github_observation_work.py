"""Count original-source work in real observer frames, independently of clocks."""
import copy
import sqlite3
import time
from types import SimpleNamespace

import pytest

from bat_agent_connector import (
    operations,
    pm_lifecycle_gates,
    pm_read_cache,
    pm_source_lineage,
    pm_source_reads,
    task_journal,
)
from bat_agent_connector import pm_execution_runtime as runtime
from bat_agent_connector import pm_maintenance_policy as policies
from bat_agent_connector import pm_release_baselines as baselines
from bat_agent_connector import pm_requirement_acceptance as acceptance
from bat_agent_connector import pm_work_batches as batches
from bat_agent_connector import pm_work_packages as packages
from bat_agent_connector.operations import OpContext
from tests import test_pm_github_observation as github
from tests import test_pm_release_baselines as releases
from tests.test_pm_github_observation import base_ops, ops, release_ops, workload_ops  # noqa: F401


class PagesChecked(Exception):
    """Stop the focused prefix before publishing a fabricated traversal."""


async def test_observer_reuses_completed_history_only_while_durable_sources_are_unchanged(ops, monkeypatch):  # noqa: F811
    data = await baseline_history(ops, monkeypatch, 2)
    source, expanded, checked = baselines._project, [], []

    def project(*args, **kwargs):
        expanded.append(1)
        return source(*args, **kwargs)

    async def prefix(traversal):
        check = traversal.check
        def counted():
            checked.append(1)
            return check()
        traversal.check = counted
        for _ in range(3):
            await traversal.read(1, traversal.root, None, 'repository')
        raise PagesChecked

    monkeypatch.setattr(baselines, '_project', project)
    monkeypatch.setattr(github.provider.Traversal, 'collect', prefix)
    op = ops.create(github.OWNER, **github.request(ops, data))[0]
    with pytest.raises(PagesChecked):
        await github.observation._run(OpContext(ops, op))
    assert len(checked) == 12  # Every pre/post GET and receipt guard still executes.
    # One initial proof plus the three journal gates per page, whose caller's
    # transaction is deliberately ineligible for reuse across frames. Fresh page
    # snapshots can prove that their completed historical sources are unchanged.
    assert len(expanded) <= 10, len(expanded)
    assert len(ops.context['github'].reads) == 3
    assert ops.db.execute('SELECT COUNT(*) FROM pm_github_observations').fetchone()[0] == 0


async def baseline_history(ops, monkeypatch, count):  # noqa: F811
    data = await github.prepared(ops, monkeypatch)
    active = policies.active_record(ops, github.OWNER, data['workspace_id'])
    first = baselines._record(ops.db, data['workspace_id'], active['baseline_id'])
    original = ops.get(first['operation_id'])
    for _ in range(count - 1):
        await releases.execute(ops, releases.request(ops, original['target'],
            original['params']['project_binding_id'], branch='support/1.x'))
    head = baselines._head(ops.db, data['workspace_id'])
    await releases.execute(ops, {'action': policies.ACTION, 'target': {'workspace_id': data['workspace_id']},
        'params': {'policy': copy.deepcopy(active['policy']), 'rationale': 'Maintain the newest original release'},
        'preconditions': {'expected_policy_revision': active['version'],
            'expected_previous_policy_id': active['policy_id'], 'expected_baseline_id': head['baseline_id'],
            'expected_lifecycle_revision': 0}, 'idempotency_key': 'adopt-history'})
    return data


async def test_observer_bounds_repeated_baseline_sources_per_fresh_page_frame(ops, monkeypatch):  # noqa: F811
    count = 8
    data = await baseline_history(ops, monkeypatch, count)
    project = baselines._project
    expanded, frames = [], []

    def source(*args, **kwargs):
        expanded.append(args[2])
        return project(*args, **kwargs)

    async def prefix(traversal):
        check = traversal.check

        def measured():
            expanded.clear()
            result = check()
            frames.append(len(expanded))
            # These baselines all refer to the same published source and
            # project binding. Their chain metadata still needs verification,
            # but a versioned fresh snapshot may reuse the completed context.
            # Existing caller transactions still prove it afresh.
            return result

        traversal.check = measured
        for _ in range(3):
            await traversal.read(1, traversal.root, None, 'repository')
        raise PagesChecked

    monkeypatch.setattr(baselines, '_project', source)
    monkeypatch.setattr(github.provider.Traversal, 'collect', prefix)
    for history in (0, 64):
        # Admitted operations are real durable requests, unrelated to the
        # historical baseline proof. They must not multiply each page's work.
        for _ in range(history):
            ops.create(github.OWNER, **github.request(ops, data))
        op = ops.create(github.OWNER, **github.request(ops, data))[0]
        with pytest.raises(PagesChecked):
            await github.observation._run(OpContext(ops, op))
    assert len(frames) == 24
    assert frames == [0, 0, 0, 1] * 6, frames
    assert len(ops.context['github'].reads) == 6
    assert ops.db.execute('SELECT COUNT(*) FROM pm_github_observations').fetchone()[0] == 0


async def test_completed_baseline_context_joins_later_private_guard(ops, monkeypatch):  # noqa: F811
    data = await baseline_history(ops, monkeypatch, 2)
    record = baselines._record(ops.db, data['workspace_id'], baselines._head(ops.db, data['workspace_id'])['baseline_id'])
    project, expanded = baselines._project, []

    def source(*args, **kwargs):
        expanded.append(args[2])
        return project(*args, **kwargs)

    monkeypatch.setattr(baselines, '_project', source)
    for _ in range(2):
        with pm_source_reads.scope(ops.db, github.OWNER):
            baselines._access_context(ops, github.OWNER, data['workspace_id'], record['document']['intent'])
            with pm_read_cache.guarded(), pm_read_cache.guarded_scope(ops.db, github.OWNER):
                baselines._access_context(ops, github.OWNER, data['workspace_id'], record['document']['intent'])
    assert len(expanded) == 2  # One complete proof per fresh durable snapshot.


async def test_completed_context_lookup_does_not_expand_its_frozen_identity(ops, monkeypatch):  # noqa: F811
    data = await baseline_history(ops, monkeypatch, 2)
    wid = data['workspace_id']
    context = baselines._record(ops.db, wid, baselines._head(ops.db, wid)['baseline_id'])['document']['intent']
    key, expanded = pm_read_cache._key, []

    def counted(value):
        expanded.append(1)
        return key(value)

    with pm_source_reads.scope(ops.db, github.OWNER):
        baselines._access_context(ops, github.OWNER, wid, context)
        with monkeypatch.context() as lookup:
            lookup.setattr(pm_read_cache, '_key', counted)
            baselines._access_context(ops, github.OWNER, wid, context)
    # Context bytes still require a digest; the generic memo must not then
    # recursively expand that already verified identity on every lookup.
    assert len(expanded) <= 8, len(expanded)


@pytest.mark.parametrize('withdraw', [False, True])
async def test_local_effect_and_callback_share_only_unchanged_source_work(ops, monkeypatch, withdraw):  # noqa: F811
    data = await github.prepared(ops, monkeypatch)
    operation = ops.create(github.OWNER, **github.request(ops, data))[0]
    context = OpContext(ops, operation)
    guard, proofs, checks = pm_source_lineage.require_new_effect, [], []

    def source():
        proofs.append(1)
        return ops.db.execute('SELECT 1 FROM pm_workspace_members WHERE workspace_id=? AND actor=?',
            (data['workspace_id'], github.OWNER.actor)).fetchone() is not None

    def check():
        checks.append(1)
        return pm_source_reads.check(ops.db, github.OWNER, 'test.local_sources', data['workspace_id'], source)

    def required(service, oid):
        guard(service, oid)
        assert check()

    def local():
        if withdraw:
            ops.db.execute('DELETE FROM pm_workspace_members WHERE workspace_id=? AND actor=?',
                (data['workspace_id'], github.OWNER.actor))
        return {'visible': check()}

    monkeypatch.setattr(pm_source_lineage, 'require_new_effect', required)
    assert context.effect('test.local_sources', local) == {'visible': not withdraw}
    assert len(checks) == 3  # Admission, local transaction guard and callback all run.
    assert len(proofs) == (3 if withdraw else 2)
    assert context.effect('test.local_sources', lambda: pytest.fail('Receipt replay must not repeat the callback')) == {'visible': not withdraw}


async def test_observer_source_reuse_preserves_every_durable_byte(ops, monkeypatch, tmp_path):  # noqa: F811
    data = await baseline_history(ops, monkeypatch, 8)
    github.populate(ops.context['github'])
    op = ops.create(github.OWNER, **github.request(ops, data))[0]
    # Only module-local clocks are fixed. Restore the same admitted journal to
    # compare identical IDs and timestamps, including all step/event envelopes.
    clock = SimpleNamespace(time=lambda: github.NOW, monotonic=time.monotonic)
    monkeypatch.setattr(operations, 'time', clock)
    monkeypatch.setattr(task_journal, 'time', clock)
    monkeypatch.setattr(pm_lifecycle_gates, 'time', clock)
    legacy = getattr(baselines, '_access_context_sources', baselines._access_context)

    def batch_sources(reader, principal, operation):
        intent = batches._intent(operation)
        batches._member(reader, principal, operation['target']['workspace_id'])
        return batches._access_sources(reader, principal, operation, intent)

    snapshot = sqlite3.connect(tmp_path / 'admitted.sqlite')
    try:
        ops.db.backup(snapshot)
        with monkeypatch.context() as reference:
            reference.setattr(baselines, '_access_context', legacy)
            reference.setattr(packages, '_access_context', packages._access_context_sources)
            reference.setattr(packages, '_record', packages._record.__wrapped__)
            reference.setattr(batches, '_access', batch_sources)
            reference.setattr(runtime, 'proof', runtime._proof_sources)
            reference.setattr(acceptance, '_dependencies', acceptance._dependency_sources)
            await ops._execute(op['operation_id'])
        before = '\n'.join(ops.db.iterdump()).encode()
        result = ops.get(op['operation_id'])
        assert result['status'] == 'succeeded' and result['result']['coverage_status'] == 'complete'
        reads = len(ops.context['github'].reads)
        snapshot.backup(ops.db)
        await ops._execute(op['operation_id'])
        after = '\n'.join(ops.db.iterdump()).encode()
        different = next((i for i, (left, right) in enumerate(zip(before, after)) if left != right), 0)
        assert after == before, (before[max(0, different - 100):different + 100],
            after[max(0, different - 100):different + 100])
        assert ops.get(op['operation_id']) == result
        assert len(ops.context['github'].reads) == 2 * reads
    finally:
        snapshot.close()
