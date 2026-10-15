"""Saved workload access reuses complete source decisions, never eligibility."""
import asyncio
import copy
from dataclasses import replace

import pytest

from bat_agent_connector import pm_access, pm_source_reads, pm_workspace
from bat_agent_connector import pm_maintenance_repairs as repairs
from bat_agent_connector import pm_workload as workload
from bat_agent_connector.operations import OperationError
from tests import test_pm_workload as fixtures
from tests.test_pm_evidence import OWNER, READER, item
from tests.test_pm_release_baselines import remove_repo

base_ops, release_ops, ops = fixtures.base_ops, fixtures.release_ops, fixtures.ops


async def saved(ops):
    data = await fixtures.prepared(ops)
    fixtures.inventory(ops)
    await item(ops, data['project_id'])
    operation, document = await fixtures.capture(ops, data)
    row = workload._record(ops.db, data['workspace_id'], document['snapshot_id'])
    return data, operation, row


async def test_canonical_workload_reuses_only_complete_same_credential_sources(ops, monkeypatch):  # noqa: F811
    data, _, row = await saved(ops)
    wid = data['workspace_id']
    source, member, calls = workload._source_access, pm_workspace.require_member, []
    denied = replace(READER, credential_id='different-source-credential')
    def counted(*args):
        calls.append(args[3]['snapshot_id'])
        return source(*args)
    def membership(db, principal, *args, **kwargs):
        if principal.credential_id == denied.credential_id:
            raise OperationError('NOT_FOUND', 'Original credential cannot read these sources', 404)
        return member(db, principal, *args, **kwargs)
    monkeypatch.setattr(workload, '_source_access', counted)
    monkeypatch.setattr(pm_workspace, 'require_member', membership)
    changes = ops.db.total_changes
    with pm_source_reads.scope(ops.db, READER):
        assert workload._access(ops, READER, wid, row) is None
        assert workload._access(ops, READER, wid, row) is None
        assert len(calls) == 1
        poisoned = copy.deepcopy(row)
        poisoned['source_binding']['source_refs'] = []
        assert poisoned != row
        with pytest.raises(OperationError) as changed:
            workload._access(ops, READER, wid, poisoned)
        assert changed.value.status == 404
        # Membership still precedes loading canonical hidden history.
        with monkeypatch.context() as hidden:
            hidden.setattr(workload, '_record', lambda *args: pytest.fail('Read hidden canonical history'))
            with pytest.raises(OperationError) as forbidden:
                workload._access(ops, denied, wid, row)
        assert forbidden.value.status == 404
        # Another credential's repair traversal neither grants authority nor
        # changes the original credential's cache boundary.
        token = repairs._READING.set({repairs._source_read_key(ops.db, denied, 'op_' + 'a' * 32)})
        try:
            assert not repairs.source_read_active(ops.db, READER)
            assert workload._access(ops, READER, wid, row) is None
            assert len(calls) == 1
        finally:
            repairs._READING.reset(token)
        token = repairs._READING.set({repairs._source_read_key(ops.db, READER, 'op_' + 'b' * 32)})
        try:
            assert repairs.source_read_active(ops.db, READER)
            assert workload._access(ops, READER, wid, row) is None
            assert workload._access(ops, READER, wid, row) is None
            assert len(calls) == 3  # No read or write of a partial-repair grant.
        finally:
            repairs._READING.reset(token)
    assert ops.db.total_changes == changes
    assert workload._access(ops, READER, wid, row) is None
    assert len(calls) == 4


async def test_original_workload_withdrawal_hides_whole_receipt_operation_and_events(ops):  # noqa: F811
    data, operation, row = await saved(ops)
    wid = data['workspace_id']
    raw = ops.db.execute('SELECT document,source_binding FROM pm_workload_snapshots WHERE snapshot_id=?',
        (row['snapshot_id'],)).fetchone()
    events = ops.journal.api_events(resource_id=row['snapshot_id'])['events']
    assert events and all(pm_access.event_visible(ops.db, READER, event) for event in events)
    assert workload._source_access(ops, READER, wid, row) is None
    with pm_source_reads.scope(ops.db, READER):
        assert workload._access(ops, READER, wid, row) is None
        assert workload.get_snapshot(ops, READER, wid, row['snapshot_id'])['snapshot']['document'] == row['document']
        assert workload.operation_visible(ops.db, READER, operation)
    await asyncio.sleep(0)
    await remove_repo(ops, data['project_id'])
    for access in (lambda: workload._source_access(ops, READER, wid, row),
                   lambda: workload._access(ops, READER, wid, row),
                   lambda: workload.get_snapshot(ops, READER, wid, row['snapshot_id'])):
        with pytest.raises(OperationError) as hidden:
            access()
        assert hidden.value.status in (403, 404)
    assert not workload.operation_visible(ops.db, READER, operation)
    assert not pm_access.visible(ops.db, READER, operation)
    assert not any(pm_access.event_visible(ops.db, READER, event) for event in events)
    assert tuple(ops.db.execute('SELECT document,source_binding FROM pm_workload_snapshots WHERE snapshot_id=?',
        (row['snapshot_id'],)).fetchone()) == tuple(raw)


async def test_source_reuse_does_not_reuse_freshness_or_final_eligibility(ops, monkeypatch):  # noqa: F811
    data, _, row = await saved(ops)
    wid = data['workspace_id']
    source, calls = workload._source_access, []
    def counted(*args):
        calls.append(args[3]['snapshot_id'])
        return source(*args)
    monkeypatch.setattr(workload, '_source_access', counted)
    policy = fixtures.policy.active_record(ops, OWNER, wid)
    with pm_source_reads.scope(ops.db, OWNER):
        before = workload.evaluate_for_maintenance(ops, OWNER, wid, row['snapshot_id'], policy, now=fixtures.NOW + 1)
        after = workload.evaluate_for_maintenance(ops, OWNER, wid, row['snapshot_id'], policy, now=fixtures.NOW + 1000)
        assert len(calls) == 1
        assert 'observation_expired_or_clock_changed' not in before['reasons']
        assert 'observation_expired_or_clock_changed' in after['reasons']
        assert not after['eligible']
