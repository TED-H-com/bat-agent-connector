"""Bounded selections use the real fixed handoff engine and disk Git fixtures."""
from __future__ import annotations

import copy
import sqlite3
import time
import uuid

import pytest

from bat_agent_connector import api_auth, pm_lifecycle_gates, pm_workspace, work_items
from bat_agent_connector import pm_work_batch_evidence as evidence
from bat_agent_connector import pm_work_batches as batches
from bat_agent_connector.errors import InvokeTimeout
from bat_agent_connector.operations import OperationError
from tests.operation_helpers import settle_operations
from tests.test_checkpoints import bat_writes, snapshot
from tests.test_pm_package_handoff import fixed as fixed_fixture
from tests.test_pm_package_handoff import pm, ready, request, revoke, world  # noqa: F401
from tests.test_pm_plan_operations import OWNER
from tests.test_pm_work_packages import action, associate, create, read

fixed = fixed_fixture

CONDITION = {'kind': 'fixed_revision_verification', 'profile': 'observed_runner_v1', 'command_digest': evidence.digest('pytest')}


@pytest.fixture
async def batch_ready(fixed, monkeypatch):
    w = fixed
    ops = w['d'].ops
    batches.install(ops)
    api_auth.issue(ops.db, OWNER.actor, sorted(batches.REQUIRED))
    # Root owns this exact shared lifecycle classification and restart hook.
    monkeypatch.setattr(pm_lifecycle_gates, 'RECORD_ACTIONS', pm_lifecycle_gates.RECORD_ACTIONS | {batches.RECORD})
    restart = ops._step_restart
    def guarded(oid, name, selected, *, bookkeeping=False):
        with ops.journal.tx():
            if not bookkeeping:
                batches.admit_restart(ops, oid, name)
            return restart(oid, name, selected, bookkeeping=bookkeeping)
    monkeypatch.setattr(ops, '_step_restart', guarded)
    return w


async def extra(w, title):
    clone = {**w, 'params': copy.deepcopy(w['params'])}
    work = work_items._get_item(w['d'].ops.db, w['target']['work_item_id'])
    wid = (await action(w, 'work_item.create', {'project_id': work['project_id']}, {'title': title,
        'goal': 'Selected independent engineering result', 'acceptance': 'Fixed revision verification'}))['work_item_id']
    clone['target'] = {'workspace_id': w['workspace_id'], 'work_item_id': wid}
    await associate(clone)
    clone['package'] = read(clone, await create(clone))
    return clone


async def selected(w, key, depends_on=None):
    original = await request(w)
    return {'key': key, 'handoff': {k: original[k] for k in ('target', 'params', 'preconditions')}, 'depends_on': depends_on or []}


def envelope(w, entries, **limits):
    ops, wid = w['d'].ops, w['workspace_id']
    return {'action': batches.START, 'target': {'workspace_id': wid},
        'params': {'entries': entries, 'limits': {'max_starts': len(entries), 'max_parallel': min(8, len(entries)),
            'deadline_at': time.time() + 600, 'max_retries': 1, **limits}, 'rationale': 'Start only this fixed selection'},
        'preconditions': {'expected_workspace_version': pm_workspace.get_workspace(ops.db, OWNER, wid)['version'],
            'expected_lifecycle_revision': pm_lifecycle_gates.head(ops.db, wid)['revision']}, 'idempotency_key': uuid.uuid4().hex}


async def advance(w, parent, rounds=3):
    ops = w['d'].ops
    for _ in range(rounds):
        ops.db.execute('UPDATE operations SET next_run_at=0 WHERE operation_id=?', (parent['operation_id'],))
        await settle_operations(ops)
    parent = ops.get(parent['operation_id'])
    assert parent['error_code'] != 'INTERNAL', parent
    return parent


def view(w, parent):
    return batches.get_batch(w['d'].ops, OWNER, w['workspace_id'], 'pmbt_' + parent['operation_id'][3:])['batch']


async def test_mixed_selected_dependencies_do_not_block_independent_entries(batch_ready, mock):
    w = batch_ready
    dependent, independent = await extra(w, 'Dependent selection'), await extra(w, 'Independent selection')
    entries = [await selected(w, 'base'), await selected(dependent, 'dependent', [{'entry_key': 'base', 'condition': CONDITION}]),
        await selected(independent, 'independent')]
    human = snapshot(w['human'])
    original = envelope(w, entries)
    parent = w['d'].ops.create(OWNER, **original)[0]
    parent = await advance(w, parent)
    current = view(w, parent)
    assert parent['status'] == 'waiting_external', parent
    states = {entry['key']: entry for entry in current['entries']}
    assert states['dependent']['reason'] == 'PM_BATCH_DEPENDENCY_EVIDENCE_UNAVAILABLE'
    assert states['dependent']['operation_id'] is None
    assert states['base']['state'] == states['independent']['state'] == 'dispatched_awaiting_result'
    assert mock.channels().count('claude:start-session') == mock.channels().count('claude:send-message') == 2
    assert snapshot(w['human']) == human
    replay, fresh = w['d'].ops.create(OWNER, **original)
    assert not fresh and replay['operation_id'] == parent['operation_id']
    assert batches.list_batches(w['d'].ops, OWNER, w['workspace_id'])['batches'][0]['batch_id'] == current['batch_id']
    with pytest.raises(sqlite3.IntegrityError, match='immutable'):
        w['d'].ops.db.execute('DELETE FROM pm_work_batch_children')


async def test_start_success_does_not_release_parallel_result_capacity(batch_ready, mock):
    w = batch_ready
    second = await extra(w, 'Second fixed work')
    parent = w['d'].ops.create(OWNER, **envelope(w, [await selected(w, 'first'), await selected(second, 'second')], max_parallel=1))[0]
    parent = await advance(w, parent)
    current = view(w, parent)
    assert current['entries'][0]['operation_status'] == 'succeeded'
    assert current['entries'][1]['reason'] == 'PM_BATCH_PARALLEL_LIMIT'
    assert current['entries'][1]['operation_id'] is None
    assert mock.channels().count('claude:start-session') == 1


@pytest.mark.parametrize('change', ['duplicate', 'cycle', 'scope', 'private_parent'])
async def test_invalid_selection_never_dispatches(batch_ready, mock, change):
    w = batch_ready
    entry = await selected(w, 'one')
    entries = [entry]
    if change == 'duplicate':
        entries.append({**entry, 'key': 'two'})
    elif change == 'cycle':
        entry['depends_on'] = [{'entry_key': 'one', 'condition': CONDITION}]
    elif change == 'scope':
        entry['handoff']['params']['pm_package_ref']['workspace_id'] = 'ws_' + 'f' * 32
    else:
        entry['handoff']['params']['pm_work_batch_parent'] = {'operation_id': 'op_' + 'a' * 32}
    with pytest.raises(OperationError):
        w['d'].ops.create(OWNER, **envelope(w, entries))
    assert not bat_writes(mock)


@pytest.mark.parametrize('boundary', ['deadline', 'credential'])
async def test_late_parent_guard_blocks_next_new_frame(batch_ready, mock, monkeypatch, boundary):
    w = batch_ready
    original = envelope(w, [await selected(w, 'one')])
    def stop(_):
        if boundary == 'deadline':
            monkeypatch.setattr(batches, '_now', lambda: original['params']['limits']['deadline_at'] + 1)
        else:
            w['d'].ops.db.execute('UPDATE api_principals SET revoked_at=?', (time.time(),))
    w['runner'].after['fetch'] = stop
    parent = await advance(w, w['d'].ops.create(OWNER, **original)[0])
    child = view(w, parent)['entries'][0]['operation_id']
    done = w['d'].ops.get(child)
    assert done['error_code'] == ('PM_BATCH_DEADLINE' if boundary == 'deadline' else 'PM_BATCH_AUTHORITY_UNAVAILABLE'), done
    assert not bat_writes(mock)


@pytest.mark.parametrize('channel', ['claude:start-session', 'claude:send-message'])
async def test_original_sent_ack_recovery_never_admits_next_frame_after_deadline(batch_ready, mock, monkeypatch, channel):
    w = batch_ready
    original = envelope(w, [await selected(w, 'one')])
    client = w['d'].fleet.client('h1')
    invoke, lost = client.invoke, False
    async def lose(name, *args, **kwargs):
        nonlocal lost
        value = await invoke(name, *args, **kwargs)
        if name == channel and not lost:
            lost = True
            monkeypatch.setattr(batches, '_now', lambda: original['params']['limits']['deadline_at'] + 1)
            raise InvokeTimeout('Original synthetic acknowledgement was lost')
        return value
    monkeypatch.setattr(client, 'invoke', lose)
    parent = await advance(w, w['d'].ops.create(OWNER, **original)[0], rounds=5)
    child_id = view(w, parent)['entries'][0]['operation_id']
    for _ in range(4):
        w['d'].ops.db.execute('UPDATE operations SET next_run_at=0 WHERE operation_id=?', (child_id,))
        await settle_operations(w['d'].ops)
    child = w['d'].ops.get(child_id)
    assert mock.channels().count('claude:start-session') == 1
    assert mock.channels().count('claude:send-message') == (1 if channel.endswith('send-message') else 0)
    assert child['status'] == ('succeeded' if channel.endswith('send-message') else 'failed'), child
    assert child['error_code'] in (None, 'PM_BATCH_STOPPED', 'PM_BATCH_DEADLINE')


async def test_source_withdrawal_hides_original_batch_and_preserves_scan_cursor(batch_ready):
    w = batch_ready
    parent = await advance(w, w['d'].ops.create(OWNER, **envelope(w, [await selected(w, 'one')]))[0])
    revoke(w)
    assert not batches.operation_visible(w['d'].ops.db, OWNER, parent)
    with pytest.raises(OperationError):
        view(w, parent)
