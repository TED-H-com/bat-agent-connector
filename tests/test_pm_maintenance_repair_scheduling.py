"""No new investment from revoked policy, unknown applicability or absent budget."""
import copy
import uuid

import pytest

from bat_agent_connector import api_auth, pm_lifecycle_gates
from bat_agent_connector import pm_maintenance_policy as policy
from bat_agent_connector import pm_maintenance_repairs as repairs
from bat_agent_connector.operations import OperationError
from tests.test_checkpoints import bat_writes
from tests.test_pm_lifecycle import decide
from tests.test_pm_maintenance_repairs import (  # noqa: F401
    OWNER,
    advance,
    execute,
    fixed,
    pm,
    ready,
    refresh,
    request,
    view,
    world,
)
from tests.test_pm_maintenance_repairs import repair_ready as repair_fixture

repair_ready = repair_fixture


async def adopt(w, document, *, who=OWNER):
    ops, wid = w['d'].ops, w['workspace_id']
    current = policy.head(ops.db, wid)
    active = policy.active_record(ops, OWNER, wid)
    return await execute(ops, {'action': policy.ACTION, 'target': {'workspace_id': wid},
        'params': {'policy': document, 'rationale': 'Owner selected explicit fixed repair rules'},
        'preconditions': {'expected_policy_revision': current['revision'], 'expected_previous_policy_id': current['policy_id'],
            'expected_baseline_id': active['baseline_id'], 'expected_lifecycle_revision': pm_lifecycle_gates.head(ops.db, wid)['revision']},
        'idempotency_key': uuid.uuid4().hex}, who=who)


async def test_tick_reserves_before_child_creation_and_duplicate_tick_uses_original(repair_ready, mock):
    w, ops = repair_ready, repair_ready['d'].ops
    ids = repairs.tick(ops)
    assert len(ids) == 1
    assert not bat_writes(mock)
    assert ops.db.execute('SELECT COUNT(*) FROM pm_maintenance_repairs').fetchone()[0] == 1
    assert ops.db.execute('SELECT COUNT(*) FROM pm_maintenance_repair_children').fetchone()[0] == 0
    assert repairs.tick(ops) == []
    parent = await advance(w, ops.get(ids[0]))
    assert view(w, parent)['child']['status'] == 'succeeded'
    assert mock.channels().count('claude:start-session') == 1


async def test_pause_before_child_releases_only_proven_unstarted_capacity(repair_ready, mock):
    w, ops = repair_ready, repair_ready['d'].ops
    oid = repairs.tick(ops)[0]
    await decide(ops, w['workspace_id'], who=OWNER)
    parent = await advance(w, ops.get(oid))
    assert repairs.tick(ops) == []
    current = view(w, parent)
    assert not current['capacity_held'] and current['child'] is None
    assert current['finished_at'] is not None
    assert not bat_writes(mock)


@pytest.mark.parametrize('change,expected', [('disabled', 'PM_MAINTENANCE_DISABLED'),
    ('cost', 'PM_REPAIR_COST_UNSUPPORTED'), ('manual', 'PM_REPAIR_WORKLOAD_UNAVAILABLE')])
async def test_policy_does_not_upgrade_unsupported_or_unadopted_repair_permission(repair_ready, mock, change, expected):
    w, ops = repair_ready, repair_ready['d'].ops
    document = copy.deepcopy(w['policy_document'])
    if change == 'disabled':
        document['enabled'] = False
    elif change == 'cost':
        document['limits']['cost_microusd'] = 1
    else:
        document['after_release_policy'] = 'manual'
    await adopt(w, document)
    await refresh(w)
    with pytest.raises(OperationError) as caught:
        ops.create(OWNER, **request(w))
    assert caught.value.code == expected
    assert repairs.tick(ops) == []
    assert not bat_writes(mock)


async def test_manage_only_original_owner_cannot_adopt_executable_rule(repair_ready):
    with pytest.raises(OperationError) as caught:
        await adopt(repair_ready, copy.deepcopy(repair_ready['policy_document']),
                    who=api_auth.Principal(OWNER.actor, frozenset({'observe', 'manage'})))
    assert caught.value.code == 'FORBIDDEN'


async def test_newest_observation_blocks_old_exact_finding(repair_ready, mock):
    w, ops = repair_ready, repair_ready['d'].ops
    old = request(w)
    w['issues'][0]['labels'] = [{'name': 'different'}]
    await refresh(w)
    with pytest.raises(OperationError) as caught:
        ops.create(OWNER, **old)
    assert caught.value.code == 'PM_REPAIR_FINDING_STALE'
    assert not bat_writes(mock)


async def test_current_owner_credential_rotation_preserves_same_controller(repair_ready, mock):
    w, ops = repair_ready, repair_ready['d'].ops
    parent = ops.create(OWNER, **request(w))[0]
    api_auth.revoke(ops.db, OWNER.actor)
    api_auth.issue(ops.db, OWNER.actor, sorted(repairs.REQUIRED))
    w['clock'][0] += 1
    parent = await advance(w, parent)
    assert view(w, parent)['child']['status'] == 'succeeded'
    assert mock.channels().count('claude:start-session') == 1


async def test_explicit_caller_cannot_borrow_other_live_start_credential(repair_ready):
    w = repair_ready
    caller = api_auth.Principal(OWNER.actor, frozenset({'observe', 'manage'}))
    with pytest.raises(OperationError) as caught:
        w['d'].ops.create(caller, **request(w))
    assert caught.value.code == 'FORBIDDEN'
