"""Proven-unsent restart ceilings use the original step transaction, never resend."""
import copy
import json
import uuid

import pytest

from bat_agent_connector import pm_lifecycle_gates, pm_workspace
from bat_agent_connector import pm_maintenance_repairs as repairs
from bat_agent_connector.operations import RERUN, ActionDef, OpContext, OperationError
from tests.test_pm_lifecycle import decide
from tests.test_pm_maintenance_repair_scheduling import adopt
from tests.test_pm_maintenance_repairs import (  # noqa: F401
    OWNER,
    advance,
    execute,
    fixed,
    pm,
    ready,
    refresh,
    request,
    revoke,
    view,
    world,
)
from tests.test_pm_maintenance_repairs import repair_ready as repair_fixture

repair_ready = repair_fixture


async def prepared(w, *, retries=1):
    ops = w['d'].ops
    document = copy.deepcopy(w['policy_document'])
    document['limits']['retries'] = retries
    await adopt(w, document)
    await refresh(w)
    parent = await advance(w, ops.create(OWNER, **request(w))[0])
    child = repairs._child(ops, parent)
    # Represent interruption after the original safe source GET and before its
    # journal response. Original durable request/actor/child remain unchanged.
    ops.db.execute("UPDATE operation_steps SET status='uncertain',response=NULL WHERE operation_id=? AND name='source.resolve'", (child['operation_id'],))
    return parent, child


async def test_same_transition_is_idempotent_and_later_restart_consumes_next_slot(repair_ready):
    w, ops = repair_ready, repair_ready['d'].ops
    parent, child = await prepared(w)
    with ops.journal.tx():
        repairs.admit_restart(ops, child['operation_id'], 'source.resolve', {})
        repairs.admit_restart(ops, child['operation_id'], 'source.resolve', {})
        ops._step_restart(child['operation_id'], 'source.resolve', {})
    assert view(w, parent)['retries_used'] == 1
    with pytest.raises(OperationError) as caught, ops.journal.tx():
        repairs.admit_restart(ops, child['operation_id'], 'source.resolve', {})
    assert caught.value.code == 'PM_REPAIR_RETRIES_EXHAUSTED'


async def test_retry_reservation_rollback_does_not_consume_budget(repair_ready):
    w, ops = repair_ready, repair_ready['d'].ops
    parent, child = await prepared(w)
    with pytest.raises(RuntimeError), ops.journal.tx():
        repairs.admit_restart(ops, child['operation_id'], 'source.resolve', {})
        raise RuntimeError('synthetic journal rollback')
    assert view(w, parent)['retries_used'] == 0
    with ops.journal.tx():
        repairs.admit_restart(ops, child['operation_id'], 'source.resolve', {})
    assert view(w, parent)['retries_used'] == 1


async def test_retry_requires_same_request_and_atomic_step_transaction(repair_ready):
    w, ops = repair_ready, repair_ready['d'].ops
    parent, child = await prepared(w)
    with pytest.raises(OperationError) as caught:
        repairs.admit_restart(ops, child['operation_id'], 'source.resolve', {})
    assert caught.value.code == 'PM_REPAIR_RETRY_TRANSACTION'
    with pytest.raises(OperationError) as caught, ops.journal.tx():
        repairs.admit_restart(ops, child['operation_id'], 'source.resolve', {'source_sha': 'f' * 40})
    assert caught.value.code == 'PM_REPAIR_RETRY_UNPROVEN'
    assert view(w, parent)['retries_used'] == 0


async def test_zero_retry_budget_prevents_original_safe_rerun_before_effect(repair_ready, mock):
    w, ops = repair_ready, repair_ready['d'].ops
    parent, child = await prepared(w, retries=0)
    before = list(mock.invokes)
    called = False
    async def effect():
        nonlocal called
        called = True
        return {}
    async def read_back(_):
        return RERUN
    with pytest.raises(OperationError) as caught:
        await OpContext(ops, child).step('source.resolve', effect, reconcile=read_back)
    assert caught.value.code == 'PM_REPAIR_RETRIES_EXHAUSTED'
    assert not called and mock.invokes == before
    assert view(w, parent)['retries_used'] == 0


async def test_original_sent_receipt_reconciliation_is_free_after_stop(repair_ready, mock):
    w, ops = repair_ready, repair_ready['d'].ops
    parent, child = await prepared(w, retries=0)
    original = json.loads(ops.db.execute("SELECT response FROM operation_steps WHERE operation_id=? AND name='send'", (child['operation_id'],)).fetchone()[0])
    ops.db.execute("UPDATE operation_steps SET status='uncertain',response=NULL WHERE operation_id=? AND name='send'", (child['operation_id'],))
    await decide(ops, w['workspace_id'], who=OWNER)
    before = list(mock.invokes)
    async def effect():
        raise AssertionError('Never resend an original sent effect')
    async def read_back(_):
        return original
    assert await OpContext(ops, child).step('send', effect, reconcile=read_back) == original
    assert view(w, parent)['retries_used'] == 0 and mock.invokes == before


async def test_registered_local_record_after_stop_preserves_current_source_gate(repair_ready, monkeypatch):
    w, ops = repair_ready, repair_ready['d'].ops
    parent = await advance(w, ops.create(OWNER, **request(w))[0])
    child = repairs._child(ops, parent)
    name = 'pm.synthetic.repair.record'
    def admit(service, principal, target, params, pre):
        pm_workspace.require_member(service.db, principal, target['workspace_id'], write=True)
        return {'principal': pm_workspace.principal_binding(principal), 'source_operation_id': child['operation_id']}
    async def record(ctx):
        return ctx.effect('local.record', lambda: {'source_operation_id': child['operation_id']})
    ops.register(ActionDef(name, 'manage', 'Synthetic trusted local evidence record', record, admit, ('workspace_id',)))
    monkeypatch.setattr(pm_lifecycle_gates, 'RECORD_ACTIONS', pm_lifecycle_gates.RECORD_ACTIONS | {name})
    await decide(ops, w['workspace_id'], who=OWNER)
    env = {'action': name, 'target': {'workspace_id': w['workspace_id']}, 'params': {}, 'preconditions': {}, 'idempotency_key': uuid.uuid4().hex}
    assert (await execute(ops, env, who=OWNER))['status'] == 'succeeded'
    pending = ops.create(OWNER, **{**env, 'idempotency_key': uuid.uuid4().hex})[0]
    with ops.journal.tx():
        repairs.admit_restart(ops, pending['operation_id'], 'local.record', {})
    assert view(w, parent)['retries_used'] == 0
    revoke(w)
    await ops._execute(pending['operation_id'])
    assert ops.get(pending['operation_id'])['error_code'] == 'PM_HANDOFF_SOURCE_UNAVAILABLE'
