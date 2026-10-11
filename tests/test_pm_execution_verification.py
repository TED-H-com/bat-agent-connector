"""Actual configured local runner, fixed owned Git source and durable receipts."""
from __future__ import annotations

import asyncio
import copy
import json
import sys
import uuid
from dataclasses import replace

import pytest

from bat_agent_connector import api_auth, execution_sources, pm_package_handoff, registry
from bat_agent_connector import pm_execution_verification as verifier
from bat_agent_connector import pm_work_batch_evidence as evidence
from bat_agent_connector.api_auth import Principal
from bat_agent_connector.operations import OperationError
from bat_agent_connector.task_verifier import ObservedVerifier, VerificationSettings
from tests.test_checkpoints import git, snapshot
from tests.test_pm_lifecycle import decide
from tests.test_pm_package_handoff import fixed, pm, ready, request, revoke, run, world  # noqa: F401
from tests.test_pm_work_packages import member

OWNER = Principal('pm-owner', frozenset({'observe', 'manage', 'start', 'operate'}))


@pytest.fixture
async def owned(fixed):
    w, ops = fixed, fixed['d'].ops
    w['child'] = await run(w, await request(w))
    assert w['child']['status'] == 'succeeded'
    w['marker'] = w['human'].parent / 'command-count'
    argv = (sys.executable, '-c', 'from pathlib import Path; p=Path(' + repr(str(w['marker'])) + '); p.write_text(p.read_text()+"x" if p.exists() else "x")')
    w['d'].adapter.verifier = ObservedVerifier(VerificationSettings(commands={w['child']['params']['project_id']: argv},
        artifact_dir=str(w['human'].parent / 'private-verifier-logs')))
    verifier.install(ops)
    w['token'] = api_auth.issue(ops.db, OWNER.actor, sorted(OWNER.scopes))
    w['config'] = await verifier.configuration(ops, OWNER, w['workspace_id'], w['child']['operation_id'])
    assert w['config']['available'] and w['config']['candidate_reason'] is None
    return w


def envelope(w):
    return {'action': verifier.ACTION, 'target': {'workspace_id': w['workspace_id'], 'execution_operation_id': w['child']['operation_id']},
        'params': {key: w['config'][key] for key in ('candidate_commit', 'tree_hash', 'command_digest')},
        'preconditions': {'handoff_contract_digest': w['config']['handoff_contract_digest']}, 'idempotency_key': uuid.uuid4().hex}


async def execute(w, env=None):
    ops = w['d'].ops
    op = ops.create(OWNER, **(env or envelope(w)))[0]
    await ops._execute(op['operation_id'])
    return ops.get(op['operation_id'])


async def test_actual_original_receipt_preserves_owned_execution_and_task_boundaries(owned):
    w, ops = owned, owned['d'].ops
    before = registry.get(w['child']['result']['host'], w['child']['result']['session_id'])
    counts = [ops.db.execute('SELECT COUNT(*) FROM ' + table).fetchone()[0] for table in ('tasks', 'commands', 'observed_verifications')]
    human = snapshot(w['human'])
    env = envelope(w)
    done = await execute(w, env)
    assert done['status'] == 'succeeded' and done['result']['status'] == 'passed', done
    assert w['marker'].read_text() == 'x'
    condition = {'kind': 'fixed_revision_verification', 'profile': 'observed_runner_v1', 'command_digest': w['config']['command_digest']}
    proof = evidence.evaluate(ops, OWNER, w['workspace_id'], w['child'], condition, done['result']['reference'])
    assert proof['status'] == 'passed' and proof['semantic_acceptance'] == 'not_evaluated'
    assert proof['operation_ids'] == [w['child']['operation_id'], done['operation_id']]
    assert verifier.get_verification(ops, OWNER, w['workspace_id'], done['operation_id'])['verification'] == done['result']
    assert verifier.operation_visible(ops.db, OWNER, done)
    replay, fresh = ops.create(OWNER, **env)
    assert not fresh and replay['operation_id'] == done['operation_id']
    assert counts == [ops.db.execute('SELECT COUNT(*) FROM ' + table).fetchone()[0] for table in ('tasks', 'commands', 'observed_verifications')]
    assert registry.get(w['child']['result']['host'], w['child']['result']['session_id']) == before
    assert execution_sources.resolve(ops, 'execution', w['child']['operation_id'])['location'] == before['worktree_path']
    assert snapshot(w['human']) == human
    assert 'argv' not in json.dumps(done['result']) and 'log_ref' not in json.dumps(done['result'])


@pytest.mark.parametrize('field', ['candidate_commit', 'tree_hash'])
async def test_wrong_fixed_revision_never_executes_command(owned, field):
    w = owned
    env = envelope(w)
    env['params'][field] = 'a' * 40
    done = await execute(w, env)
    assert done['status'] == 'succeeded' and done['result']['status'] == 'blocked', done
    assert not w['marker'].exists()


@pytest.mark.parametrize('field', ['command_digest', 'handoff_contract_digest'])
async def test_stale_config_or_package_fails_before_admission(owned, field):
    w = owned
    env = envelope(w)
    env['params' if field == 'command_digest' else 'preconditions'][field] = 'a' * 64
    with pytest.raises(OperationError, match='PM_VERIFIER_CONTEXT_CHANGED'):
        w['d'].ops.create(OWNER, **env)
    assert not w['marker'].exists()


async def test_command_change_after_admission_does_not_run_replacement(owned):
    w, ops = owned, owned['d'].ops
    op = ops.create(OWNER, **envelope(w))[0]
    runner = w['d'].adapter.verifier
    runner.settings = replace(runner.settings, commands={w['child']['params']['project_id']: (sys.executable, '-c', 'print("changed")')})
    await ops._execute(op['operation_id'])
    assert ops.get(op['operation_id'])['error_code'] == 'PM_VERIFIER_CONTEXT_CHANGED'
    assert not w['marker'].exists()


@pytest.mark.parametrize('change', ['revoked', 'expired', 'membership', 'paused'])
async def test_current_authority_and_lifecycle_before_command(owned, change):
    w, ops = owned, owned['d'].ops
    op = ops.create(OWNER, **envelope(w))[0]
    if change == 'revoked':
        ops.db.execute('UPDATE api_principals SET revoked_at=1 WHERE actor=?', (OWNER.actor,))
    elif change == 'expired':
        ops.db.execute('UPDATE api_principals SET expires_at=1 WHERE actor=?', (OWNER.actor,))
    elif change == 'membership':
        revoke(w)
    else:
        await decide(ops, w['workspace_id'], who=OWNER)
    await ops._execute(op['operation_id'])
    done = ops.get(op['operation_id'])
    assert done['status'] == 'failed' and done['error_code'] != 'INTERNAL', done
    assert not w['marker'].exists()


async def test_other_workspace_writer_cannot_claim_original_controller(owned):
    w, ops = owned, owned['d'].ops
    await member(w, 'other-writer', 'contributor')
    other = Principal('other-writer', OWNER.scopes)
    with pytest.raises(OperationError, match='PM_VERIFIER_FOREIGN_CONTROLLER'):
        ops.create(other, **envelope(w))
    assert not w['marker'].exists()


async def test_process_outcome_can_reconcile_after_stop_without_second_command(owned, monkeypatch):
    w, ops = owned, owned['d'].ops
    original, lost = ops._step_done, []
    def finish(oid, name, response, **kwargs):
        if name == verifier.PROCESS and not lost:
            lost.append(True)
            raise asyncio.CancelledError('lost outer process completion')
        return original(oid, name, response, **kwargs)
    monkeypatch.setattr(ops, '_step_done', finish)
    op = ops.create(OWNER, **envelope(w))[0]
    with pytest.raises(asyncio.CancelledError):
        await ops._execute(op['operation_id'])
    await decide(ops, w['workspace_id'], who=OWNER)
    ops.db.execute('UPDATE api_principals SET revoked_at=1 WHERE actor=?', (OWNER.actor,))
    await ops._execute(op['operation_id'])
    done = ops.get(op['operation_id'])
    assert done['status'] == 'succeeded' and done['result']['status'] == 'passed', done
    assert w['marker'].read_text() == 'x'
    assert next(step for step in done['steps'] if step['name'] == verifier.PROCESS)['status'] == 'succeeded'


async def test_unknown_process_never_reexecutes_or_invents_receipt(owned, monkeypatch):
    w, ops = owned, owned['d'].ops
    original = ObservedVerifier._group_run
    async def lost(self, *args, **kwargs):
        await original(self, *args, **kwargs)
        raise asyncio.CancelledError('process receipt lost')
    monkeypatch.setattr(ObservedVerifier, '_group_run', lost)
    op = ops.create(OWNER, **envelope(w))[0]
    with pytest.raises(asyncio.CancelledError):
        await ops._execute(op['operation_id'])
    for _ in range(3):
        await ops._execute(op['operation_id'])
    done = ops.get(op['operation_id'])
    assert done['status'] in {'uncertain', 'needs_attention'}
    assert w['marker'].read_text() == 'x'
    assert verifier.get_verification(ops, OWNER, w['workspace_id'], op['operation_id'])['verification'] is None


async def test_nonzero_process_is_original_blocked_receipt(owned):
    w = owned
    runner = w['d'].adapter.verifier
    runner.settings = replace(runner.settings, commands={w['child']['params']['project_id']: (sys.executable, '-c', 'raise SystemExit(7)')})
    w['config'] = await verifier.configuration(w['d'].ops, OWNER, w['workspace_id'], w['child']['operation_id'])
    done = await execute(w)
    assert done['status'] == 'succeeded' and done['result']['status'] == 'blocked' and done['result']['exit_code'] == 7


async def test_source_withdrawal_hides_original_saved_receipt(owned):
    w, ops = owned, owned['d'].ops
    done = await execute(w)
    revoke(w)
    assert not verifier.operation_visible(ops.db, OWNER, done)
    with pytest.raises(OperationError):
        verifier.get_verification(ops, OWNER, w['workspace_id'], done['operation_id'])


async def test_unavailable_and_dirty_source_never_fake_candidate(owned):
    w, ops = owned, owned['d'].ops
    path = w['child']['result']['worktree_path']
    from pathlib import Path
    Path(path, 'dirty.txt').write_text('uncommitted')
    config = await verifier.configuration(ops, OWNER, w['workspace_id'], w['child']['operation_id'])
    assert config['available'] and config['candidate_commit'] is None and config['tree_hash'] is None
    assert config['candidate_reason'] == 'candidate_not_clean_or_unavailable'
    w['d'].adapter.verifier = None
    config = await verifier.configuration(ops, OWNER, w['workspace_id'], w['child']['operation_id'])
    assert not config['available'] and config['reason'] == 'PM_VERIFIER_UNAVAILABLE'


async def test_exact_original_receipt_cannot_verify_another_child(owned):
    w, ops = owned, owned['d'].ops
    done = await execute(w)
    child = copy.deepcopy(w['child'])
    child['operation_id'] = 'op_' + 'a' * 32
    condition = {'kind': 'fixed_revision_verification', 'profile': 'observed_runner_v1', 'command_digest': w['config']['command_digest']}
    with pytest.raises(OperationError):
        verifier.evaluate(ops, OWNER, w['workspace_id'], child, condition, done['result']['reference'])
    assert pm_package_handoff.binding(w['child'])['contract_digest'] == w['config']['handoff_contract_digest']
    assert git(w['child']['result']['worktree_path'], 'rev-parse', 'HEAD') == w['config']['candidate_commit']
