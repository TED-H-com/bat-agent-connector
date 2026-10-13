"""Original fixed verifier receipts do not become semantic or release acceptance."""
import json
import uuid

import pytest

from bat_agent_connector import pm_access, pm_read_cache, pm_source_lineage
from bat_agent_connector import pm_maintenance_repair_delivery as delivery
from bat_agent_connector import pm_maintenance_repair_evidence as evidence
from bat_agent_connector import pm_maintenance_repairs as repairs
from bat_agent_connector.operations import OpContext, OperationError
from bat_agent_connector.pm_release_baselines import _snapshot
from tests.test_pm_lifecycle import decide
from tests.test_pm_maintenance_repairs import (  # noqa: F401
    OWNER,
    advance,
    execute,
    fixed,
    pm,
    ready,
    request,
    view,
    world,
)
from tests.test_pm_maintenance_repairs import repair_ready as repair_fixture

repair_ready = repair_fixture


def observed(w, parent, *, own=True, passed=True):
    ops = w['d'].ops
    child = repairs._child(ops, parent)
    from tests.test_pm_work_batches import observed as original_observed
    return {'kind': 'verification', 'reference': original_observed(w, child,
        session_id=None if own else 'other-session', candidate='c' * 40, tree='d' * 40, exit_code=0 if passed else 1)}


def record(w, parent, selected, *, version=1):
    return {'action': repairs.RECORD, 'target': {'workspace_id': w['workspace_id'], 'repair_id': 'pmrp_' + parent['operation_id'][3:]},
        'params': {'evidence': selected}, 'preconditions': {'expected_repair_version': version}, 'idempotency_key': uuid.uuid4().hex}


async def test_exact_verifier_receipt_after_pause_is_visible_but_not_publication(repair_ready):
    w, ops = repair_ready, repair_ready['d'].ops
    parent = await advance(w, ops.create(OWNER, **request(w))[0])
    selected = observed(w, parent)
    await decide(ops, w['workspace_id'], who=OWNER)
    original = record(w, parent, selected)
    receipt = await execute(ops, original, who=OWNER)
    assert receipt['result']['evidence']['proof']['status'] == 'passed'
    current = view(w, parent)
    assert current['progress']['verification']['reference'] == selected['reference']
    assert current['progress']['required_ci'] == 'unproven'
    assert not current['repair_fixed'] and current['capacity_held']
    assert current['version'] == 2
    assert repairs.operation_visible(ops.db, OWNER, receipt)
    replay = await evidence.run(OpContext(ops, receipt))
    assert replay == receipt['result']
    assert ops.db.execute('SELECT COUNT(*) FROM pm_maintenance_repair_evidence').fetchone()[0] == 1
    with pytest.raises(OperationError) as caught:
        ops.create(OWNER, **record(w, parent, selected))
    assert caught.value.code == 'VERSION_CONFLICT'


async def test_foreign_verifier_cannot_become_original_repair_evidence(repair_ready):
    w, ops = repair_ready, repair_ready['d'].ops
    parent = await advance(w, ops.create(OWNER, **request(w))[0])
    selected = observed(w, parent, own=False)
    with pytest.raises(OperationError):
        ops.create(OWNER, **record(w, parent, selected))
    assert ops.db.execute('SELECT COUNT(*) FROM pm_maintenance_repair_evidence').fetchone()[0] == 0


async def test_full_saved_receipt_sources_gate_parent_child_and_operation_reads(repair_ready, monkeypatch):
    w, ops = repair_ready, repair_ready['d'].ops
    parent = await advance(w, ops.create(OWNER, **request(w))[0])
    selected = observed(w, parent)
    receipt = await execute(ops, record(w, parent, selected), who=OWNER)
    task_id = receipt['result']['evidence']['proof']['task_id']
    old = pm_source_lineage.require
    def denied(db, principal, kind, identity):
        if kind == 'execution' and identity == task_id:
            raise OperationError('NOT_FOUND', 'Original verifier source withdrawn', 404)
        return old(db, principal, kind, identity)
    monkeypatch.setattr(pm_source_lineage, 'require', denied)
    assert not repairs.operation_visible(ops.db, OWNER, parent)
    assert not repairs.operation_visible(ops.db, OWNER, receipt)
    assert not pm_access.visible(ops.db, OWNER, repairs._child(ops, parent))
    assert repairs.current_repair(ops, OWNER, w['workspace_id'])['source_visibility_filtered']
    assert repairs.list_repairs(ops, OWNER, w['workspace_id'])['source_visibility_filtered']
    assert await evidence.run(OpContext(ops, receipt)) == receipt['result']


@pytest.mark.parametrize('first', ['parent', 'child'])
async def test_scoped_read_memo_matches_unscoped_after_a_withdrawn_repair_source(repair_ready, monkeypatch, first):
    # The repair back-edge guard answers nested checks as if the repair were
    # visible. A later source withdrawal hides the repair; inside one read memo
    # scope the child and its execution must stay hidden as they do unscoped.
    w, ops = repair_ready, repair_ready['d'].ops
    parent = await advance(w, ops.create(OWNER, **request(w))[0])
    receipt = await execute(ops, record(w, parent, observed(w, parent)), who=OWNER)
    task_id = receipt['result']['evidence']['proof']['task_id']
    child = repairs._child(ops, parent)

    def withdrawn(ops_, principal, op):
        raise OperationError('NOT_FOUND', 'Original repair observation sources unavailable', 404)
    monkeypatch.setattr(delivery, 'require_sources', withdrawn)

    def decisions():
        return {'parent': pm_access.visible(ops.db, OWNER, parent),
                'child': pm_access.visible(ops.db, OWNER, child),
                'execution': pm_source_lineage.visible(ops.db, OWNER, 'execution', task_id)}

    unscoped = decisions()
    assert unscoped == {'parent': False, 'child': False, 'execution': False}
    with _snapshot(ops.db), pm_read_cache.scope(ops.db, OWNER):
        if first == 'parent':
            assert pm_access.visible(ops.db, OWNER, parent) is False
        else:
            assert pm_access.visible(ops.db, OWNER, child) is False
        assert decisions() == unscoped
        with pytest.raises(OperationError) as refused:
            pm_source_lineage.require(ops.db, OWNER, 'execution', task_id)
        assert refused.value.status == 404
        assert decisions() == unscoped


async def test_failed_verifier_is_preserved_without_authorizing_release(repair_ready):
    w, ops = repair_ready, repair_ready['d'].ops
    parent = await advance(w, ops.create(OWNER, **request(w))[0])
    selected = observed(w, parent, passed=False)
    receipt = await execute(ops, record(w, parent, selected), who=OWNER)
    assert receipt['result']['evidence']['proof']['status'] == 'blocked'
    assert view(w, parent)['progress']['blocked_reason'] == 'PM_REPAIR_VERIFICATION_REQUIRED'
    with pytest.raises(OperationError):
        evidence.require_delivery(ops, OWNER, parent, {'action': 'integration.apply'})


async def test_whole_record_digest_and_append_only_evidence(repair_ready):
    w, ops = repair_ready, repair_ready['d'].ops
    parent = await advance(w, ops.create(OWNER, **request(w))[0])
    await execute(ops, record(w, parent, observed(w, parent)), who=OWNER)
    row = ops.db.execute('SELECT document,document_digest FROM pm_maintenance_repair_evidence').fetchone()
    assert repairs.checks._digest(json.loads(row[0])) == row[1]
    assert repairs.capabilities(ops)['automatic_release'] is False
