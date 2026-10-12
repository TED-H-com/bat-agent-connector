"""Original maintenance authority, bounded reservations and receipt recovery."""
import copy
import uuid

import pytest

from bat_agent_connector import api_auth, pm_access, pm_lifecycle_gates
from bat_agent_connector import pm_maintenance_checks as checks
from bat_agent_connector import pm_maintenance_policy as policy
from bat_agent_connector import pm_maintenance_repair_evidence as evidence
from bat_agent_connector import pm_maintenance_repair_rules as rules
from bat_agent_connector import pm_maintenance_repairs as repairs
from bat_agent_connector.errors import InvokeTimeout
from bat_agent_connector.operations import OperationError
from tests.operation_helpers import settle_operations
from tests.test_checkpoints import bat_writes
from tests.test_pm_lifecycle import decide
from tests.test_pm_maintenance_repairs import (  # noqa: F401
    OWNER,
    advance,
    execute,
    fixed,
    pm,
    ready,
    request,
    revoke,
    view,
    world,
)
from tests.test_pm_maintenance_repairs import repair_ready as repair_fixture

repair_ready = repair_fixture

@pytest.mark.parametrize('boundary', ['deadline', 'credential', 'owner', 'source'])
async def test_original_policy_stop_between_frames_never_sends_new_start(repair_ready, mock, boundary):
    w, ops = repair_ready, repair_ready['d'].ops
    def stop(_):
        if boundary == 'deadline':
            w['clock'][0] += w['policy_document']['limits']['run_seconds'] + 1
        elif boundary == 'credential':
            api_auth.revoke(ops.db, OWNER.actor)
        elif boundary == 'owner':
            ops.db.execute("UPDATE pm_workspace_members SET role='contributor' WHERE workspace_id=? AND actor=?", (w['workspace_id'], OWNER.actor))
        else:
            revoke(w)
    w['runner'].after['fetch'] = stop
    parent = await advance(w, ops.create(OWNER, **request(w))[0])
    child = repairs._child(ops, parent)
    assert child['status'] == 'failed', child
    assert child['error_code'] != 'INTERNAL'
    assert not bat_writes(mock)
    assert ops.db.execute('SELECT COUNT(*) FROM pm_maintenance_repair_children').fetchone()[0] == 1


@pytest.mark.parametrize('channel', ['claude:start-session', 'claude:send-message'])
async def test_lost_sent_ack_reconciles_original_after_deadline_without_resend(repair_ready, mock, monkeypatch, channel):
    w, ops = repair_ready, repair_ready['d'].ops
    client = w['d'].fleet.client('h1')
    invoke, lost = client.invoke, False
    async def lose(name, *args, **kwargs):
        nonlocal lost
        result = await invoke(name, *args, **kwargs)
        if name == channel and not lost:
            lost = True
            w['clock'][0] += w['policy_document']['limits']['run_seconds'] + 1
            raise InvokeTimeout('Original synthetic acknowledgement lost')
        return result
    monkeypatch.setattr(client, 'invoke', lose)
    parent = await advance(w, ops.create(OWNER, **request(w))[0], rounds=5)
    child = repairs._child(ops, parent)
    for _ in range(4):
        ops.db.execute('UPDATE operations SET next_run_at=0 WHERE operation_id=?', (child['operation_id'],))
        await settle_operations(ops)
    child = ops.get(child['operation_id'])
    assert mock.channels().count('claude:start-session') == 1
    assert mock.channels().count('claude:send-message') == (1 if channel.endswith('send-message') else 0)
    assert child['status'] == ('succeeded' if channel.endswith('send-message') else 'failed'), child
    assert view(w, parent)['capacity_held']


@pytest.mark.parametrize('change', ['revoked', 'expired', 'partial', 'stage', 'owner'])
async def test_live_authority_and_epoch_before_reservation(repair_ready, mock, change):
    w, ops = repair_ready, repair_ready['d'].ops
    if change == 'revoked':
        api_auth.revoke(ops.db, OWNER.actor)
    elif change == 'expired':
        ops.db.execute('UPDATE api_principals SET expires_at=?', (w['clock'][0] - 1,))
    elif change == 'partial':
        api_auth.revoke(ops.db, OWNER.actor)
        api_auth.issue(ops.db, OWNER.actor, ['observe', 'manage'])
        api_auth.issue(ops.db, OWNER.actor, ['start'])
        w['clock'][0] += 1
    elif change == 'stage':
        await decide(ops, w['workspace_id'], who=OWNER)
    else:
        ops.db.execute("UPDATE pm_workspace_members SET role='contributor' WHERE workspace_id=? AND actor=?", (w['workspace_id'], OWNER.actor))
    with pytest.raises(OperationError):
        ops.create(OWNER, **request(w))
    assert repairs.tick(ops) == []
    assert not bat_writes(mock)


async def test_duplicate_admitted_parents_reserve_once_and_share_check_capacity(repair_ready, mock):
    w, ops = repair_ready, repair_ready['d'].ops
    one = ops.create(OWNER, **request(w))[0]
    two = ops.create(OWNER, **request(w))[0]
    first = await advance(w, one)
    assert ops.get(two['operation_id'])['error_code'] == 'PM_REPAIR_ALREADY_RESERVED'
    assert mock.channels().count('claude:start-session') == 1
    assert len(repairs.list_repairs(ops, OWNER, w['workspace_id'])['repairs']) == 1
    with pytest.raises(OperationError) as caught:
        checks._capacity(ops, w['workspace_id'], repairs._intent(first)['check'], w['clock'][0])
    assert caught.value.code == 'PM_MAINTENANCE_BUDGET_EXHAUSTED'
    assert repairs.tick(ops) == []


async def test_private_relation_and_envelope_cannot_be_forged(repair_ready):
    w, ops = repair_ready, repair_ready['d'].ops
    parent = await advance(w, ops.create(OWNER, **request(w))[0])
    child = repairs._child(ops, parent)
    for changed in ({**child, 'operation_id': 'op_' + 'f' * 32}, {**child, 'params': {**child['params'], 'prompt': 'Different work'}},
                    {**child, 'external_refs': {**child['external_refs'], 'pm_maintenance_repair_parent': 'op_' + 'f' * 32}}):
        with pytest.raises(OperationError) as caught:
            repairs.require_child(ops, changed)
        assert caught.value.code == 'PM_REPAIR_CHILD_CHANGED'


async def test_source_withdrawal_hides_original_and_derived_records_without_losing_cursor(repair_ready):
    w, ops = repair_ready, repair_ready['d'].ops
    parent = await advance(w, ops.create(OWNER, **request(w))[0])
    child = repairs._child(ops, parent)
    revoke(w)
    assert not repairs.operation_visible(ops.db, OWNER, parent)
    assert not pm_access.visible(ops.db, OWNER, child)
    with pytest.raises(OperationError):
        view(w, parent)


async def test_no_foreign_pr_association_can_authorize_a_new_publication(repair_ready):
    w, ops = repair_ready, repair_ready['d'].ops
    parent = await advance(w, ops.create(OWNER, **request(w))[0])
    with pytest.raises(OperationError) as caught:
        evidence.require_delivery(ops, OWNER, parent, {'action': 'integration.apply'})
    assert caught.value.code == 'PM_REPAIR_OWNED_PR_UNPROVEN'
    with pytest.raises(OperationError) as caught:
        evidence.require_delivery(ops, OWNER, parent, {'action': 'github.pr.merge'})
    assert caught.value.code == 'PM_REPAIR_DELIVERY_CEILING'


@pytest.mark.parametrize('change', ['release', 'path', 'labels', 'profile', 'package', 'private', 'kind'])
async def test_rule_does_not_gain_unproven_version_paths_or_execution_fields(repair_ready, change):
    w, ops = repair_ready, repair_ready['d'].ops
    document = copy.deepcopy(w['policy_document'])
    rule = document['repair_rules'][0]
    if change == 'release':
        rule['release']['commit_sha'] = 'f' * 40
    elif change == 'path':
        rule['paths'] = ['../escape']
    elif change == 'labels':
        rule['labels_all'] = []
    elif change == 'profile':
        rule['verification'] = {'kind': 'fixed_revision_verification', 'profile': 'github_reported_checks_v1'}
    elif change == 'package':
        rule['handoff']['params']['pm_package_ref']['document_digest'] = 'f' * 64
    elif change == 'private':
        rule['handoff']['params']['pm_maintenance_repair_parent'] = 'caller claim'
    else:
        rule['work_kind'] = 'model_says_low_risk'
    current = policy.head(ops.db, w['workspace_id'])
    active = policy.active_record(ops, OWNER, w['workspace_id'])
    with pytest.raises(OperationError):
        ops.create(OWNER, action=policy.ACTION, target={'workspace_id': w['workspace_id']},
            params={'policy': document, 'rationale': 'Explicit changed rule'},
            preconditions={'expected_policy_revision': current['revision'], 'expected_previous_policy_id': current['policy_id'],
                'expected_baseline_id': active['baseline_id'], 'expected_lifecycle_revision': pm_lifecycle_gates.head(ops.db, w['workspace_id'])['revision']},
            idempotency_key=uuid.uuid4().hex)


def test_exact_paths_do_not_grant_siblings_or_rename_sources():
    assert rules.covered('src/a.py', ['src/'])
    assert not rules.covered('src2/a.py', ['src/'])
    for path in ('/absolute', '../parent', 'src/../parent', 'src/*.py', 'src\\a'):
        with pytest.raises(OperationError):
            rules.path(path)
