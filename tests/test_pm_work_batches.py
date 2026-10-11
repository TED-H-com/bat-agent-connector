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
    return {'key': key, 'handoff': {k: original[k] for k in ('target', 'params', 'preconditions')},
        'depends_on': depends_on or [], 'result_condition': CONDITION}


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


def observed(w, child, *, session_id=None, exit_code=0, candidate='a' * 40, tree='b' * 40, command='pytest'):
    """Persist an existing trusted Task Service verifier with exact central relation."""
    from bat_agent_connector import controller_authority
    ops, result = w['d'].ops, child['result']
    with controller_authority.use(controller_authority.operation_binding(child), ops.journal):
        task = ops.journal.submit(project='fixed-batch-fixture', host=result['host'], workspace=child['target']['workspace_id'],
            original_words='Existing owned verifier fixture', idempotency_key=uuid.uuid4().hex)
    ops.journal.change(task['task_id'], 'dispatching', fields={'session_id': session_id or result['session_id']})
    ops.journal.change(task['task_id'], 'verifying', fields={'verification_commit': candidate, 'verification_tree': tree})
    receipt = ops.journal.record_observed_verification(task['task_id'], {'candidate_commit': candidate,
        'tree_hash': tree, 'command': command, 'exit_code': exit_code, 'log_ref': 'private://original-verifier',
        'output_sha256': 'c' * 64, 'source': 'observed_runner'})
    if exit_code == 0:
        ops.journal.change(task['task_id'], 'done')
    return {'verification_id': receipt['verification_id'], 'candidate_commit': candidate, 'tree_hash': tree}


def proof_request(w, parent, ref, *, version=1, key='first', condition=CONDITION):
    return {'action': batches.RECORD, 'target': {'workspace_id': w['workspace_id'], 'batch_id': 'pmbt_' + parent['operation_id'][3:]},
        'params': {'evidence': [{'entry_key': key, 'condition': condition, 'reference': ref}], 'rationale': 'Bind this original verifier receipt'},
        'preconditions': {'expected_version': version}, 'idempotency_key': uuid.uuid4().hex}


async def test_fixed_observed_verification_releases_only_selected_prerequisite(batch_ready, mock):
    w, ops = batch_ready, batch_ready['d'].ops
    second = await extra(w, 'Verified prerequisite child')
    original = envelope(w, [await selected(w, 'first'), await selected(second, 'second', [{'entry_key': 'first', 'condition': CONDITION}])], max_parallel=1)
    parent = await advance(w, ops.create(OWNER, **original)[0])
    child = ops.get(view(w, parent)['entries'][0]['operation_id'])
    ref = observed(w, child)
    proof = proof_request(w, parent, ref)
    receipt = ops.create(OWNER, **proof)[0]
    await settle_operations(ops)
    done = ops.get(receipt['operation_id'])
    assert done['status'] == 'succeeded', done
    assert done['result']['evidence'][0]['status'] == 'passed'
    assert done['result']['evidence'][0]['semantic_acceptance'] == 'not_evaluated'
    assert 'private://original-verifier' not in str(done['result'])
    replay, fresh = ops.create(OWNER, **proof)
    assert not fresh and replay['operation_id'] == receipt['operation_id']
    with pytest.raises(OperationError, match='revision changed'):
        ops.create(OWNER, **proof_request(w, parent, ref))
    parent = await advance(w, parent)
    entries = view(w, parent)['entries']
    assert entries[0]['state'] == 'fixed_revision_verified'
    assert entries[1]['state'] == 'dispatched_awaiting_result'
    assert mock.channels().count('claude:start-session') == 2
    assert view(w, parent)['requirement_acceptance'] == 'not_evaluated'


@pytest.mark.parametrize('problem', ['wrong_session', 'failed', 'wrong_command', 'wrong_commit', 'later_failure'])
async def test_unproven_verifier_never_releases_dependent(batch_ready, mock, problem):
    w, ops = batch_ready, batch_ready['d'].ops
    second = await extra(w, 'Unavailable prerequisite child')
    parent = await advance(w, ops.create(OWNER, **envelope(w, [await selected(w, 'first'),
        await selected(second, 'second', [{'entry_key': 'first', 'condition': CONDITION}])]))[0])
    child = ops.get(view(w, parent)['entries'][0]['operation_id'])
    ref = observed(w, child, session_id='unrelated-verifier-session' if problem == 'wrong_session' else None,
        exit_code=1 if problem == 'failed' else 0, command='echo accepted' if problem == 'wrong_command' else 'pytest')
    if problem == 'wrong_commit':
        ref['candidate_commit'] = 'd' * 40
    if problem == 'later_failure':
        old = ops.db.execute('SELECT * FROM observed_verifications WHERE verification_id=?', (ref['verification_id'],)).fetchone()
        ops.journal.record_observed_verification(old['task_id'], {**dict(old), 'source': 'observed_runner', 'exit_code': 1})
    receipt = ops.create(OWNER, **proof_request(w, parent, ref))[0]
    await settle_operations(ops)
    done = ops.get(receipt['operation_id'])
    assert done['status'] == 'succeeded', done
    assert done['result']['evidence'][0]['status'] == 'blocked'
    parent = await advance(w, parent)
    assert view(w, parent)['entries'][1]['operation_id'] is None
    assert mock.channels().count('claude:start-session') == 1


async def test_fixed_condition_cannot_be_weakened_and_private_verifier_is_hidden(batch_ready):
    w, ops = batch_ready, batch_ready['d'].ops
    parent = await advance(w, ops.create(OWNER, **envelope(w, [await selected(w, 'first')]))[0])
    child = ops.get(view(w, parent)['entries'][0]['operation_id'])
    ref = observed(w, child)
    with pytest.raises(OperationError, match='original selected result condition'):
        ops.create(OWNER, **proof_request(w, parent, ref, condition={**CONDITION, 'command_digest': evidence.digest('true')}))
    receipt = ops.create(OWNER, **proof_request(w, parent, ref))[0]
    await settle_operations(ops)
    assert ops.get(receipt['operation_id'])['status'] == 'succeeded'
    task = ops.db.execute('SELECT task_id FROM observed_verifications WHERE verification_id=?', (ref['verification_id'],)).fetchone()[0]
    # Current Task Service ownership changes must hide the saved derived proof.
    ops.db.execute("UPDATE tasks SET controller_authority=json_set(controller_authority,'$.pm_workspace_id',?) WHERE task_id=?",
        ('ws_' + 'e' * 32, task))
    with pytest.raises(OperationError) as hidden:
        view(w, parent)
    assert hidden.value.code == 'NOT_FOUND'
    assert not batches.operation_visible(ops.db, OWNER, ops.get(receipt['operation_id']))


async def admitted_child(w):
    from bat_agent_connector import controller_authority
    from bat_agent_connector.operations import OpContext, Wait
    ops = w['d'].ops
    parent = ops.create(OWNER, **envelope(w, [await selected(w, 'first')]))[0]
    with controller_authority.use(controller_authority.operation_binding(parent), ops.journal):
        with pytest.raises(Wait):
            await batches._run(OpContext(ops, parent))
    return ops.get(parent['operation_id']), ops.get(view(w, parent)['entries'][0]['operation_id'])


async def test_retry_reservations_are_atomic_idempotent_and_do_not_count_receipts(batch_ready):
    from bat_agent_connector import controller_authority
    w, ops = batch_ready, batch_ready['d'].ops
    parent, child = await admitted_child(w)
    oid, name = child['operation_id'], 'batch.fixture.original-unsent'
    with controller_authority.use(controller_authority.operation_binding(child), ops.journal):
        ops._step_start(oid, name, {'fixed': 'request'})
        ops._step_status(oid, name, 'uncertain')
        with pytest.raises(RuntimeError, match='rollback'):
            with ops.journal.tx():
                batches.admit_restart(ops, oid, name)
                raise RuntimeError('rollback original restart')
        assert ops.db.execute('SELECT COUNT(*) FROM pm_work_batch_retries').fetchone()[0] == 0
        with ops.journal.tx():
            batches.admit_restart(ops, oid, name)
            batches.admit_restart(ops, oid, name)
            ops._step_restart(oid, name, {'fixed': 'request'})
        assert ops.db.execute('SELECT COUNT(*) FROM pm_work_batch_retries').fetchone()[0] == 1
        ops._step_status(oid, name, 'uncertain')
        with pytest.raises(OperationError) as exhausted:
            ops._step_restart(oid, name, {'fixed': 'request'})
        assert exhausted.value.code == 'PM_BATCH_RETRIES_EXHAUSTED'
        assert ops.db.execute('SELECT status FROM operation_steps WHERE operation_id=? AND name=?', (oid, name)).fetchone()[0] == 'uncertain'
        ops._step_start(oid, 'batch.fixture.receipt', {}, local=True, bookkeeping=True)
        ops._step_status(oid, 'batch.fixture.receipt', 'uncertain')
        ops._step_restart(oid, 'batch.fixture.receipt', {}, bookkeeping=True)
        assert ops.db.execute('SELECT COUNT(*) FROM pm_work_batch_retries').fetchone()[0] == 1
    assert ops.get(oid)['attempts'] == 0


@pytest.mark.parametrize('stop', ['pause', 'cancel', 'scope_union'])
async def test_last_unsent_frame_rechecks_original_parent_stop_and_single_credential(batch_ready, mock, monkeypatch, stop):
    from tests.test_pm_lifecycle import decide
    w, ops = batch_ready, batch_ready['d'].ops
    original = envelope(w, [await selected(w, 'first')])
    parent = ops.create(OWNER, **original)[0]
    client, stopped = w['d'].fleet.client('h1'), False
    invoke = client.invoke
    async def stop_before_frame(name, *args, **kwargs):
        nonlocal stopped
        if name == 'claude:start-session' and not stopped:
            original_guard = kwargs['before_frame']
            async def guard():
                nonlocal stopped
                stopped = True
                if stop == 'pause':
                    await decide(ops, w['workspace_id'], who=OWNER)
                elif stop == 'cancel':
                    ops.cancel(OWNER, parent['operation_id'])
                else:
                    ops.db.execute('UPDATE api_principals SET revoked_at=?', (time.time(),))
                    api_auth.issue(ops.db, OWNER.actor, ['observe', 'manage'])
                    api_auth.issue(ops.db, OWNER.actor, ['start'])
                await original_guard()
            kwargs['before_frame'] = guard
        return await invoke(name, *args, **kwargs)
    monkeypatch.setattr(client, 'invoke', stop_before_frame)
    parent = await advance(w, parent)
    assert stopped
    assert not bat_writes(mock)
    child = ops.get(view(w, parent)['entries'][0]['operation_id'])
    assert child['error_code'] in {'PM_LIFECYCLE_STOPPED', 'PM_BATCH_STOPPED', 'PM_BATCH_AUTHORITY_UNAVAILABLE'}, child


async def test_verified_edge_cannot_bypass_original_package_readiness(batch_ready, mock):
    w, ops = batch_ready, batch_ready['d'].ops
    blocked = await extra(w, 'Fixed unresolved design')
    params = copy.deepcopy(blocked['params'])
    params['design']['status'] = 'unresolved'
    blocked['package'] = read(blocked, await create(blocked, **params, previous_package_id=blocked['package']['package_id']))
    parent = await advance(w, ops.create(OWNER, **envelope(w, [await selected(w, 'first'),
        await selected(blocked, 'second', [{'entry_key': 'first', 'condition': CONDITION}])]))[0])
    first = ops.get(view(w, parent)['entries'][0]['operation_id'])
    proof = ops.create(OWNER, **proof_request(w, parent, observed(w, first)))[0]
    await settle_operations(ops)
    assert ops.get(proof['operation_id'])['status'] == 'succeeded'
    parent = await advance(w, parent)
    current = view(w, parent)
    assert current['entries'][0]['state'] == 'fixed_revision_verified'
    assert current['entries'][1]['reason'] == 'PM_PACKAGE_NOT_READY'
    assert current['entries'][1]['operation_id'] is None
    assert mock.channels().count('claude:start-session') == 1


async def test_batch_list_has_whole_wire_budget_and_signed_cursor(batch_ready, monkeypatch):
    from bat_agent_connector import controller_authority
    from bat_agent_connector.operations import OpContext
    w, ops = batch_ready, batch_ready['d'].ops
    entry = await selected(w, 'first')
    for _ in range(2):
        parent = ops.create(OWNER, **envelope(w, [entry]))[0]
        with controller_authority.use(controller_authority.operation_binding(parent), ops.journal):
            batches._reserve(OpContext(ops, parent))
    all_rows = batches.list_batches(ops, OWNER, w['workspace_id'])
    assert len(all_rows['batches']) == 2
    limit = batches._size({**all_rows, 'batches': all_rows['batches'][:1], 'next_cursor': 2**63 - 1})
    monkeypatch.setattr(batches, 'MAX_READ_BYTES', limit)
    first = batches.list_batches(ops, OWNER, w['workspace_id'])
    assert len(first['batches']) == 1 and first['next_cursor'] == first['batches'][0]['sequence']
    last = batches.list_batches(ops, OWNER, w['workspace_id'], after=first['next_cursor'])
    assert len(last['batches']) == 1 and last['next_cursor'] is None
    assert first['batches'][0]['batch_id'] != last['batches'][0]['batch_id']
    assert batches._size(first) <= limit and batches._size(last) <= limit
    for after in (True, -1, 2**63):
        with pytest.raises(OperationError):
            batches.list_batches(ops, OWNER, w['workspace_id'], after=after)
    monkeypatch.setattr(batches, 'MAX_READ_BYTES', 1)
    with pytest.raises(OperationError) as small:
        batches.list_batches(ops, OWNER, w['workspace_id'])
    assert small.value.status == 413
