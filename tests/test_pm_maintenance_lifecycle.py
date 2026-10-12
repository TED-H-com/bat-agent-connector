"""A real bounded observation admits one fixed, source-gated Maintain decision."""
from __future__ import annotations

import uuid

import pytest

from bat_agent_connector import pm_lifecycle as life
from bat_agent_connector import pm_lifecycle_gates as gates
from bat_agent_connector import pm_maintenance_lifecycle as maintenance
from bat_agent_connector import pm_workload as workload
from bat_agent_connector.operations import OperationError
from tests import test_pm_maintenance_checks as checktests
from tests.operation_helpers import settle_operations
from tests.test_pm_evidence import OWNER, READER
from tests.test_pm_lifecycle import decide, request
from tests.test_pm_release_baselines import remove_repo

base_ops, release_ops, workload_ops, github_ops, ops = (
    checktests.base_ops, checktests.release_ops, checktests.workload_ops, checktests.github_ops, checktests.ops)


async def ready(ops, monkeypatch):
    document = checktests.githubtests.worktests.settings()
    document['observation']['stable_for_seconds'] = 0
    data = await checktests.prepared(ops, monkeypatch, document=document)
    return data


async def fixed(ops, data):
    await checktests.githubtests.observe(ops, data)
    op, _ = await checktests.githubtests.worktests.capture(ops, data)
    row = workload._record(ops.db, data['workspace_id'], op['result']['snapshot_id'])
    return maintenance.reference(row)


async def test_policy_check_enters_maintain_once_without_dispatch_or_epoch_change(ops, monkeypatch):
    data = await ready(ops, monkeypatch)
    wid = data['workspace_id']
    old = gates.head(ops.db, wid)
    parent, _ = await checktests.check(ops, data)
    await settle_operations(ops)
    current = gates.head(ops.db, wid)
    assert current['stage'] == 'maintain' and current['revision'] == old['revision'] + 1
    assert current['execution_epoch'] == old['execution_epoch']
    transition = life.get_transition(ops, OWNER, wid, current['transition_id'])['transition']
    assert transition['snapshot']['maintenance']['snapshot_id'].startswith('pmwl_')
    assert ops.db.execute('SELECT COUNT(*) FROM tasks').fetchone()[0] == 0
    assert ops.db.execute('SELECT COUNT(*) FROM pm_runs').fetchone()[0] == 0
    await checktests.checks._run(checktests.OpContext(ops, parent))
    await settle_operations(ops)
    assert gates.head(ops.db, wid) == current
    # Its source identity remains fixed after a newer lifecycle head.
    assert life.operation_visible(ops.db, READER, ops.get(ops.db.execute(
        'SELECT operation_id FROM pm_lifecycle_transitions WHERE transition_id=?', (current['transition_id'],)).fetchone()[0]))


@pytest.mark.parametrize('change', ['pause', 'source', 'work'])
async def test_final_stage_frame_rechecks_stop_sources_and_actual_work(ops, monkeypatch, change):
    data = await ready(ops, monkeypatch)
    wid = data['workspace_id']
    ref = await fixed(ops, data)
    assert life.get_lifecycle(ops, OWNER, wid)['maintenance']['reference'] == ref
    op = ops.create(OWNER, **request(ops, wid, 'set_stage', params={'stage': 'maintain', 'maintenance_ref': ref}))[0]
    if change == 'pause':
        await decide(ops, wid, 'pause')
    elif change == 'source':
        await remove_repo(ops, data['project_id'])
    else:
        checktests.githubtests.worktests.inventory(ops, sessions=[{'streaming': True}])
    await ops._execute(op['operation_id'])
    assert ops.get(op['operation_id'])['status'] == 'failed'
    assert gates.head(ops.db, wid)['stage'] != 'maintain'


async def test_original_history_source_withdrawal_and_explicit_reopen(ops, monkeypatch):
    data = await ready(ops, monkeypatch)
    wid = data['workspace_id']
    ref = await fixed(ops, data)
    env = request(ops, wid, 'set_stage', params={'stage': 'maintain', 'maintenance_ref': ref})
    op = ops.create(OWNER, **env)[0]
    await ops._execute(op['operation_id'])
    assert ops.get(op['operation_id'])['status'] == 'succeeded'
    await decide(ops, wid, 'pause')
    # Old selected observation cannot be silently reused across the stop epoch.
    with pytest.raises(OperationError):
        ops.create(OWNER, **request(ops, wid, 'reopen', params={'stage': 'maintain', 'maintenance_ref': ref}))
    updated = await fixed(ops, data)
    reopened = await decide(ops, wid, 'reopen', params={'stage': 'maintain', 'maintenance_ref': updated})
    assert reopened['result']['stage'] == 'maintain'
    assert not ops.create(OWNER, **env)[1]
    await remove_repo(ops, data['project_id'])
    assert not life.operation_visible(ops.db, READER, op)
    with pytest.raises(OperationError):
        life.get_transition(ops, READER, wid, 'pmlt_'+op['operation_id'][3:])
    assert life.get_lifecycle(ops, READER, wid)['transition_visibility_filtered']


async def test_idea_never_automatically_enters_maintain(ops, monkeypatch):
    data = await ready(ops, monkeypatch)
    wid = data['workspace_id']
    await decide(ops, wid, 'set_stage', params={'stage': 'idea'})
    ref = await fixed(ops, data)
    with pytest.raises(OperationError, match='PM_LIFECYCLE_TRANSITION_INVALID'):
        ops.create(OWNER, **request(ops, wid, 'set_stage', params={'stage': 'maintain', 'maintenance_ref': ref}, key=uuid.uuid4().hex))
    assert gates.head(ops.db, wid)['stage'] == 'idea'


@pytest.mark.parametrize('change', ['credential', 'deadline'])
async def test_automatic_queued_transition_rechecks_original_authority_and_deadline(ops, monkeypatch, change):
    data = await ready(ops, monkeypatch)
    wid = data['workspace_id']
    parent = ops.create(OWNER, **checktests.request(ops, data))[0]
    # Settle only the original observer and snapshot, leaving the stage child queued.
    await ops._execute(parent['operation_id'])
    github = checktests.checks._child(ops, parent['operation_id'], 'github')
    await ops._execute(github['operation_id'])
    await ops._execute(parent['operation_id'])
    captured = checktests.checks._child(ops, parent['operation_id'], 'workload')
    await ops._execute(captured['operation_id'])
    await ops._execute(parent['operation_id'])
    row = ops.db.execute("SELECT operation_id FROM pm_maintenance_check_children WHERE parent_id=? AND slot='lifecycle'", (parent['operation_id'],)).fetchone()
    assert row is not None
    if change == 'credential':
        ops.db.execute('UPDATE api_principals SET revoked_at=1 WHERE actor=?', (OWNER.actor,))
    else:
        ops.context['check_test_clock'][0] += 121
    await ops._execute(row[0])
    failed = ops.get(row[0])
    assert failed['status'] == 'failed', failed
    assert failed['error_code'] == ('PM_MAINTENANCE_AUTHORITY_UNAVAILABLE' if change == 'credential' else 'PM_MAINTENANCE_DEADLINE')
    assert gates.head(ops.db, wid)['stage'] == 'development'


async def test_maintenance_reference_types_and_hidden_events_are_not_laundered(ops, monkeypatch):
    from bat_agent_connector import pm_access
    data = await ready(ops, monkeypatch)
    wid = data['workspace_id']
    ref = await fixed(ops, data)
    with pytest.raises(OperationError, match='INVALID_PARAMS'):
        ops.create(OWNER, **request(ops, wid, 'set_stage', params={'stage': 'maintain', 'maintenance_ref': {**ref, 'policy_version': True}}))
    done = await decide(ops, wid, 'set_stage', params={'stage': 'maintain', 'maintenance_ref': ref})
    event = {'resource_type': 'pm_lifecycle', 'resource_id': wid, 'kind': 'pm.lifecycle.set_stage', 'body': {'operation_id': done['operation_id']}}
    assert pm_access.event_visible(ops.db, READER, event)
    await remove_repo(ops, data['project_id'])
    assert not pm_access.event_visible(ops.db, READER, event)
    assert life.get_lifecycle(ops, READER, wid)['lifecycle']['transition_id'] is None
    assert life.list_inflight(ops, READER, wid)['transition_id'] is None
