"""A real private observation frame reuses sources, never current authority."""
import asyncio
from types import SimpleNamespace

import pytest

from bat_agent_connector import pm_access, pm_workspace
from bat_agent_connector import pm_maintenance_repair_delivery as delivery
from bat_agent_connector import pm_maintenance_repairs as repairs
from bat_agent_connector import pm_workload as workload
from bat_agent_connector.operations import OperationError
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
    await asyncio.sleep(0)
    assert frame() == new

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
        assert denied.value.status == 404
        assert not pm_access.visible(ops.db, OWNER, parent)
        assert not any(pm_access.event_visible(ops.db, OWNER, event) for event in events)
    assert frame() == new
    assert not ops.db.execute('SELECT 1 FROM operation_steps WHERE operation_id=?', (observer['operation_id'],)).fetchone()


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
