"""Adoption progresses from committed child outcomes without waiting out each poll."""
from __future__ import annotations

import asyncio
import sqlite3
import time
from contextlib import closing
from dataclasses import replace

import pytest

from bat_agent_connector.operations import NeedsAttention, OperationError, Uncertain, Wait
from tests import test_pm_conversation_proposals as conversation
from tests.operation_helpers import settle_operations
from tests.test_checkpoints import git

world = conversation.world
pm = conversation.pm
ready = conversation.ready
proposal_world = conversation.proposal_world
OWNER = conversation.OWNER


async def first_child(w):
    ops = w['d'].ops
    proposal = await conversation.proposed(w)
    parent = ops.create(OWNER, **conversation.adoption_request(w, proposal))[0]
    await ops._execute(parent['operation_id'])
    child_id = ops.db.execute('SELECT operation_id FROM pm_conversation_children WHERE parent_id=?',
        (parent['operation_id'],)).fetchone()[0]
    # Deliberately leave the poll in the future. A real child outcome must wake
    # the parent; slow fixture setup must not make a missing notification pass.
    due = time.time() + 3600
    ops.db.execute('UPDATE operations SET next_run_at=? WHERE operation_id=?', (due, parent['operation_id']))
    ops._wake = asyncio.Event()
    return parent['operation_id'], child_id, due


async def test_adoption_finishes_without_forcing_retry_deadlines(proposal_world):
    w, ops = proposal_world, proposal_world['d'].ops
    proposal = await conversation.proposed(w)
    request = conversation.adoption_request(w, proposal)
    parent = ops.create(OWNER, **request)[0]
    # Unlike conversation.complete, this never moves waiting retry deadlines.
    await settle_operations(ops)
    done = ops.get(parent['operation_id'])
    assert done['status'] == 'succeeded', done
    children = done['result']['child_operation_ids']
    assert len(children) == len(set(children)) == 8
    assert all(ops.get(child)['status'] == 'succeeded' for child in children)
    assert ops.create(OWNER, **request)[0]['operation_id'] == parent['operation_id']
    assert w['runner'].phases.count('pm-revision') == 1
    assert git(w['plan']['worktree_path'], 'rev-list', '--count', w['sha'] + '..HEAD') == '1'


@pytest.mark.parametrize('outcome', ['succeeded', 'failed', 'cancelled', 'needs_attention',
    'waiting_external', 'waiting_checks', 'uncertain'])
async def test_child_outcome_wakes_only_when_parent_can_progress(proposal_world, outcome):
    w, ops = proposal_world, proposal_world['d'].ops
    parent_id, child_id, due = await first_child(w)
    if outcome == 'cancelled':
        ops.cancel(OWNER, child_id)
    else:
        if outcome != 'succeeded':
            async def stop(ctx):
                if outcome == 'failed':
                    raise OperationError('FIXTURE_STOP', 'No child effect')
                if outcome == 'needs_attention':
                    raise NeedsAttention('FIXTURE_REVIEW', 'Review the original child')
                if outcome == 'uncertain':
                    raise Uncertain('original', 'Original effect needs reconciliation')
                raise Wait(outcome, 'Original child is still pending')
            action = ops.get(child_id)['action']
            ops.actions[action] = replace(ops.actions[action], run=stop)
        await ops._execute(child_id)
    assert ops.get(child_id)['status'] == outcome
    settled = outcome in {'succeeded', 'failed', 'cancelled', 'needs_attention'}
    # A fresh connection sees both the child's result and its parent's due time:
    # no in-memory subscription has to survive a process restart.
    with closing(sqlite3.connect(ops.journal.path)) as reopened:
        assert reopened.execute('SELECT status FROM operations WHERE operation_id=?', (child_id,)).fetchone()[0] == outcome
        assert reopened.execute('SELECT next_run_at FROM operations WHERE operation_id=?', (parent_id,)).fetchone()[0] == (0 if settled else due)
    assert ops._wake.is_set() is settled
    assert ops.get(parent_id)['status'] == 'waiting_external'
    if settled and outcome != 'succeeded':
        await settle_operations(ops)
        assert ops.get(parent_id)['status'] == 'needs_attention'
        assert ops.get(parent_id)['error_code'] == 'PM_CONVERSATION_CHILD_STOPPED'
        assert ops.db.execute('SELECT COUNT(*) FROM pm_conversation_children WHERE parent_id=?', (parent_id,)).fetchone()[0] == 1


@pytest.mark.parametrize('parent_status', ['cancelled', 'needs_attention'])
async def test_child_completion_does_not_resume_stopped_parent(proposal_world, parent_status):
    w, ops = proposal_world, proposal_world['d'].ops
    parent_id, child_id, _ = await first_child(w)
    if parent_status == 'cancelled':
        ops.cancel(OWNER, parent_id)
    else:
        ops._transition(parent_id, 'running')
        ops._transition(parent_id, 'needs_attention', reason='Owner review required')
    before = ops.get(parent_id)
    await ops._execute(child_id)
    assert ops.get(child_id)['status'] == 'failed'
    assert ops.get(parent_id) == before
    assert ops.get(parent_id)['status'] == parent_status
    assert ops.db.execute('SELECT COUNT(*) FROM pm_conversation_children WHERE parent_id=?', (parent_id,)).fetchone()[0] == 1


async def test_child_wakeup_is_atomic_and_uses_immutable_relation(proposal_world):
    w, ops = proposal_world, proposal_world['d'].ops
    parent_id, child_id, due = await first_child(w)
    unrelated = ops.create(OWNER, action='pm.message.append', target=conversation.plans.target(w),
        params={'content': 'Unrelated intent'}, preconditions={'expected_version': conversation.plans.version(w)},
        idempotency_key='unrelated')[0]
    # A reference alone cannot nominate an adoption parent for a wakeup.
    ops._merge_refs(unrelated['operation_id'], {'pm_conversation_parent': parent_id})
    ops.cancel(OWNER, unrelated['operation_id'])
    assert ops.get(parent_id)['next_run_at'] == due
    with pytest.raises(RuntimeError, match='roll back'):
        with ops.journal.tx():
            ops._transition(child_id, 'cancelled')
            assert ops.get(parent_id)['next_run_at'] == 0
            raise RuntimeError('roll back the child outcome')
    assert ops.get(child_id)['status'] == 'accepted'
    assert ops.get(parent_id)['next_run_at'] == due
    await ops._execute(child_id)
    assert ops.get(child_id)['status'] == 'succeeded'
    assert ops.get(parent_id)['next_run_at'] == 0
