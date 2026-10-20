"""A real private observation frame reuses sources, never current authority."""
import asyncio
import copy
from types import SimpleNamespace

import pytest

from bat_agent_connector import pm_access, pm_read_cache, pm_source_reads, pm_workspace
from bat_agent_connector import pm_execution_runtime as runtime
from bat_agent_connector import pm_github_observation as observations
from bat_agent_connector import pm_maintenance_repair_delivery as delivery
from bat_agent_connector import pm_maintenance_repairs as repairs
from bat_agent_connector import pm_workload as workload
from bat_agent_connector.operations import OpContext, OperationError, StepFailed
from tests import pm_joined_maintenance_helpers as maintenance
from tests.pm_joined_lifecycle_helpers import OWNER
from tests.test_pm_joined_lifecycle import bat_world, daemon, joined, world  # noqa: F401
from tests.test_pm_joined_lifecycle import test_same_ledger_development_and_semantic_dependency as run_story
from tests.test_pm_publication_source_reads import measured
from tests.test_pm_repair_reservation_sources import journal_change


class FrameChecked(Exception):
    """This regression ends before the separately tested full observer/delivery."""


def prior_intent_access(ops, principal, wid, intent):
    """The original source-only predicate, before its whole-read scope."""
    pm_workspace.require_member(ops.db, principal, wid)
    original = workload._policy().fixed_record(ops, principal, wid, intent['policy_id'])
    if (original['document_digest'] != intent['document_digest'] or original['scope'] != intent['scope']
            or original['version'] != intent['version'] or original['observation'] != intent['observation']):
        raise workload._bad('NOT_FOUND', 'Original workload policy unavailable', 404)
    baseline = workload.baselines._record(ops.db, wid, intent['baseline_id'])
    if baseline['document_digest'] != intent['baseline_digest']:
        raise workload._bad('NOT_FOUND', 'Original workload baseline unavailable', 404)
    workload.baselines._access(ops, principal, wid, baseline)


def prior_guard(ops, parent, *, scopes=repairs.REQUIRED):
    """Original checks and ordering; only the new synchronous scope is absent."""
    if parent['action'] != repairs.ACTION or parent['cancel_requested'] or parent['status'] in repairs.TERMINAL | {'needs_attention'}:
        raise repairs._bad('PM_REPAIR_STOPPED', 'The original repair stopped new investment')
    intent, wid = repairs._intent(parent), parent['target']['workspace_id']
    record = repairs.policies._record(ops.db, wid, intent['check']['workload']['policy_id'])
    live = repairs._principal(ops, record, scopes=scopes)
    current = repairs.checks._check(ops, live, parent['target'], {'policy_id': record['policy_id']}, parent['preconditions'])
    if current != intent['check']:
        raise repairs._bad('PM_REPAIR_CONTEXT_CHANGED', 'The original policy, baseline or lifecycle epoch changed')
    repairs._full_access(ops, live, parent)
    baseline = repairs.baselines._record(ops.db, wid, intent['check']['workload']['baseline_id'])
    repairs.rules.sources(ops, live, wid, intent['rule'], baseline)
    row = repairs._record_for(ops, parent)
    if row['finished_at'] is not None or not row['created_at'] <= repairs.time.time() < row['deadline_at']:
        raise repairs._bad('PM_REPAIR_DEADLINE', 'This repair cannot admit another effect after its original deadline')
    return live


async def check_frame(w, parent, observer, monkeypatch, record_property):
    ops, wid = w['d'].ops, w['workspace_id']
    def frame():
        return delivery.require_child(ops, observer)
    with monkeypatch.context() as prior:
        prior.setattr(repairs, '_guard', prior_guard)
        prior.setattr(workload, '_intent_access', prior_intent_access)
        old, old_queries = measured(ops.db, frame)
    changes = ops.db.total_changes
    new, new_queries = measured(ops.db, frame)
    assert new == old == repairs._record_for(ops, parent)['deadline_at']
    assert new_queries * 3 < old_queries * 2
    assert ops.db.total_changes == changes
    record_property('repair_observer_prior_frame_selects', old_queries)
    record_property('repair_observer_scoped_frame_selects', new_queries)

    # The actual page handler also reads the current policy under its admitted
    # principal. Before this fix that separate read expanded the same original
    # baseline/dependency history repeatedly, even though the repair guard was
    # already scoped. Compare identical intents on the joined graph, not time.
    ctx = OpContext(ops, observer)
    principal = pm_workspace.admitted_principal(ctx)
    def current_policy():
        return observations._check(ops, principal, ctx.target, ctx.params, ctx.preconditions)
    old_intent, old_policy_queries = measured(ops.db, lambda:
        workload._check(ops, principal, ctx.target, ctx.params, ctx.preconditions))
    new_intent, new_policy_queries = measured(ops.db, current_policy)
    assert new_intent == old_intent == ctx.admission_binding['github_observation_intent']
    assert new_policy_queries * 2 < old_policy_queries
    assert ops.db.total_changes == changes
    record_property('repair_observer_prior_policy_selects', old_policy_queries)
    record_property('repair_observer_scoped_policy_selects', new_policy_queries)
    await asyncio.sleep(0)
    assert frame() == new
    assert current_policy() == new_intent

    # Each subsequent frame reloads live credentials and the original parent.
    for mutation, args, code in (
        ('UPDATE api_principals SET revoked_at=1 WHERE actor=?', (OWNER.actor,), 'PM_MAINTENANCE_AUTHORITY_UNAVAILABLE'),
        ('UPDATE pm_workspace_members SET role=\'reader\' WHERE workspace_id=? AND actor=?', (wid, OWNER.actor), 'FORBIDDEN'),
        ('UPDATE operations SET cancel_requested=1 WHERE operation_id=?', (parent['operation_id'],), 'PM_REPAIR_STOPPED'),
    ):
        with journal_change(ops.db):
            ops.db.execute(mutation, args)
            with pytest.raises(OperationError) as denied:
                frame()
            assert denied.value.code == code
        assert frame() == new
    with monkeypatch.context() as expired:
        expired.setattr(repairs, 'time', SimpleNamespace(time=lambda: new + 1))
        with pytest.raises(OperationError) as denied:
            frame()
        assert denied.value.code == 'PM_REPAIR_DEADLINE'

    first = ops.db.execute("SELECT operation_id FROM pm_messages WHERE workspace_id=? ORDER BY sequence LIMIT 1", (wid,)).fetchone()[0]
    visible = pm_access.visible
    events = ops.journal.api_events(resource_id=parent['operation_id'], limit=500)['events']
    assert events and all(pm_access.event_visible(ops.db, OWNER, event) for event in events)
    with monkeypatch.context() as withdrawn:
        withdrawn.setattr(pm_access, 'visible', lambda db, principal, op:
            op['operation_id'] != first and visible(db, principal, op))
        await asyncio.sleep(0)
        with pytest.raises(OperationError) as denied:
            frame()
        with pytest.raises(OperationError) as policy_denied:
            current_policy()
        assert policy_denied.value.status == 404
        assert denied.value.status == 404
        assert not pm_access.visible(ops.db, OWNER, parent)
        assert not any(pm_access.event_visible(ops.db, OWNER, event) for event in events)
    assert frame() == new
    assert not ops.db.execute('SELECT 1 FROM operation_steps WHERE operation_id=?', (observer['operation_id'],)).fetchone()
    check_shared_batch_receipts(w, monkeypatch)
    await check_next_page(w, parent, observer, monkeypatch)


def check_shared_batch_receipts(w, monkeypatch):
    """Sibling runtime proofs share the same original batch and fixed receipts."""
    ops = w['d'].ops
    child = ops.get(w['ledger'].references['execution_ids'][0])
    parent, _ = runtime.relation(ops, child)
    package, reader = runtime.batches.pm_package_handoff._package, runtime.observer
    batch_source = runtime.batches._access_sources
    packages, receipts, expanded = [], [], []

    def package_read(*args, **kwargs):
        packages.append(args[2]['package_id'])
        return package(*args, **kwargs)

    def receipt_read(service, selected):
        if selected['operation_id'] == child['operation_id']:
            receipts.append(selected['operation_id'])
        return reader(service, selected)

    def batch_sources(*args, **kwargs):
        expanded.append(args[2]['operation_id'])
        return batch_source(*args, **kwargs)

    with monkeypatch.context() as counted:
        counted.setattr(runtime.batches.pm_package_handoff, '_package', package_read)
        counted.setattr(runtime, 'observer', receipt_read)
        counted.setattr(runtime.batches, '_access_sources', batch_sources)
        with pm_source_reads.scope(ops.db, OWNER):
            runtime.batches._access(ops, OWNER, parent)
            packages.clear()
            for _ in range(2):
                runtime.batches._access(ops, OWNER, parent)
            batch_reads = len(packages)
            first = runtime.proof(ops, OWNER, child)
            receipts.clear()
            proofs = [first, *(runtime.proof(ops, OWNER, child) for _ in range(2))]
            repeated_receipts = len(receipts)
            expanded.clear()
            # A completed proof obtained outside a traversal guard is still
            # complete inside it. Partial/back-edge proofs retain their exact
            # guard through the existing source-read interface.
            with pm_read_cache.guarded(), pm_read_cache.guarded_scope(ops.db, OWNER):
                runtime.batches._access(ops, OWNER, parent)
                assert runtime.proof(ops, OWNER, child) == first
    assert proofs[0] is not None and proofs[0] == proofs[1] == proofs[2]
    assert (batch_reads, repeated_receipts) == (0, 0)
    assert expanded == []


async def check_next_page(w, parent, observer, monkeypatch):
    """The actual next-page handler must refuse before another provider GET."""
    ops, wid = w['d'].ops, w['workspace_id']
    active = repairs.policies.active_record(ops, OWNER, wid)
    deadline = repairs._record_for(ops, parent)['deadline_at']
    provider = ops.context['github']
    bounded_get = provider.bounded_get
    reads = []

    async def counted(*args, **kwargs):
        reads.append(args[0])
        return await bounded_get(*args, **kwargs)

    async def scope_change():
        policy = copy.deepcopy(active['policy'])
        policy['allowed_paths'].append('docs')
        changed = ops.create(OWNER, action=repairs.policies.ACTION, target={'workspace_id': wid},
            params={'policy': policy, 'rationale': 'Change the currently adopted repository scope'},
            preconditions={'expected_policy_revision': active['version'], 'expected_previous_policy_id': active['policy_id'],
                'expected_baseline_id': active['baseline_id'],
                'expected_lifecycle_revision': observer['preconditions']['expected_lifecycle_revision']},
            idempotency_key='scope-change-between-pages')[0]
        await ops._execute(changed['operation_id'])
        assert ops.get(changed['operation_id'])['status'] == 'succeeded'

    for variant, code in (
        ('scope', 'PM_WORKLOAD_CONTEXT_CHANGED'),
        ('authority', 'PM_MAINTENANCE_AUTHORITY_UNAVAILABLE'),
        ('cancel', 'PM_REPAIR_STOPPED'),
        ('deadline', 'PM_REPAIR_DEADLINE'),
    ):
        reads.clear()

        async def prefix(traversal):
            await traversal.read(1, traversal.root, None, 'repository')
            assert len(reads) == 1 and traversal.pages == 1
            await asyncio.sleep(0)
            if variant == 'scope':
                await scope_change()
            elif variant == 'authority':
                ops.db.execute('UPDATE api_principals SET revoked_at=1 WHERE actor=?', (OWNER.actor,))
            elif variant == 'cancel':
                ops.db.execute('UPDATE operations SET cancel_requested=1 WHERE operation_id=?',
                    (parent['operation_id'],))
            else:
                page.setattr(repairs, 'time', SimpleNamespace(time=lambda: deadline + 1))
            with pytest.raises((OperationError, StepFailed)) as denied:
                await traversal.read(1, traversal.root, None, 'repository')
            assert denied.value.code == code
            assert len(reads) == 1 and traversal.pages == 1
            raise FrameChecked

        with journal_change(ops.db), monkeypatch.context() as page:
            page.setattr(provider, 'bounded_get', counted)
            page.setattr(observations.provider.Traversal, 'collect', prefix)
            # The same admitted child and its first original receipt are used
            # for each isolated durable-state mutation; none may publish.
            with pytest.raises(FrameChecked):
                await observations._run(OpContext(ops, observer))
        assert not ops.db.execute('SELECT 1 FROM pm_github_observations WHERE operation_id=?',
            (observer['operation_id'],)).fetchone()


async def test_original_observer_frame_bounds_repeated_sources_and_rechecks_authority(joined, mock, monkeypatch, record_property):  # noqa: F811
    advance, checked = maintenance.advance, []
    async def stop_at_observer(w, parent, rounds=3):
        for _ in range(rounds):
            parent = await advance(w, parent, rounds=1)
            rows = delivery._rows(w['d'].ops, parent, 'observation')
            if rows:
                observer = delivery._child(w['d'].ops, parent, rows[-1])
                assert observer['status'] == 'accepted'
                await check_frame(w, parent, observer, monkeypatch, record_property)
                checked.append(observer['operation_id'])
                raise FrameChecked
        return parent
    monkeypatch.setattr(maintenance, 'advance', stop_at_observer)
    with pytest.raises(FrameChecked):
        await run_story(joined, mock, monkeypatch, record_property)
    assert len(checked) == 1
