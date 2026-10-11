"""Advance a deployment baseline through its existing receipt-selection action.

This never creates a GitHub Release, invents a package version or replaces a
stable/prerelease baseline with an unrelated deployment environment.
"""
from __future__ import annotations

import asyncio

from . import pm_delivery_lineage
from . import pm_maintenance_repair_delivery as delivery
from . import pm_maintenance_repair_evidence as evidence
from . import pm_maintenance_repairs as repairs
from . import pm_release_baselines as baselines
from .operations import OperationError, Wait


def child(ops, parent):
    rows = delivery._rows(ops, parent, 'baseline')
    return delivery._child(ops, parent, rows[0]) if rows else None


def request(ops, parent, completed):
    """Derive only from the original adopted channel and verified delivery."""
    intent = repairs._intent(parent)
    original = baselines._record(ops.db, parent['target']['workspace_id'], intent['check']['workload']['baseline_id'])
    doc = original['document']
    policy = evidence.policies_for(ops, parent)
    if doc['intent']['source']['kind'] != 'deployment' or policy['delivery']['maximum'] != 'deploy':
        return None
    facts = pm_delivery_lineage._facts(ops, completed)
    merged, deployed = facts.get('merge') or {}, facts.get('deployment') or {}
    if (completed['status'] != 'succeeded' or repairs.checks._unsettled(ops, completed)
            or merged.get('merged') is not True or merged.get('verified') is not True
            or deployed.get('verified') is not True or deployed.get('state') != 'succeeded'
            or deployed.get('source_sha') != merged.get('merged_sha')
            or deployed.get('environment') != doc['channel'] or doc['channel'] != policy['delivery']['environment']):
        raise evidence._bad('Original deployment does not prove the adopted baseline channel', 'PM_REPAIR_BASELINE_UNPROVEN')
    source = doc['intent']
    return {'action': baselines.ACTION, 'target': {'workspace_id': parent['target']['workspace_id'], 'repository': doc['repository']},
        'params': {'project_binding_id': source['project_binding']['binding_id'],
            'source': {'kind': 'deployment', 'deployment_id': deployed['deployment_id']}, 'channel': doc['channel'],
            'maintenance_branch': source['maintenance_branch'], 'known_limitations': source['known_limitations'],
            'rationale': 'Original adopted maintenance repair verified in the same deployment channel: ' + parent['operation_id']},
        'preconditions': {'expected_baseline_version': original['version'], 'expected_previous_baseline_id': original['baseline_id'],
            'expected_repository_id': doc['repository_id'], 'expected_commit_sha': deployed['source_sha']},
        'idempotency_key': 'pm-repair-baseline:' + parent['operation_id']}


def advance(ctx, parent, completed):
    """At most one original child; stopped/failed selection never republishes."""
    ops = ctx.service
    original = child(ops, parent)
    if original:
        if original['status'] not in repairs.TERMINAL or repairs.checks._unsettled(ops, original):
            raise Wait('waiting_external', 'Original release baseline selection remains ' + original['status'], 5)
        if original['status'] == 'succeeded':
            saved = original['result']
            record = baselines._record(ops.db, parent['target']['workspace_id'], saved['baseline_id'])
            if (record['operation_id'] != original['operation_id'] or record['document_digest'] != saved['document_digest']
                    or record['previous_baseline_id'] != original['preconditions']['expected_previous_baseline_id']):
                raise evidence._bad('Original release baseline receipt changed', 'PM_REPAIR_BASELINE_UNPROVEN')
            return {'status': 'updated', 'operation_id': original['operation_id'], **saved}
        return {'status': 'not_updated', 'operation_id': original['operation_id'], 'reason': original['error_code'] or original['status']}
    try:
        selected = request(ops, parent, completed)
        if selected is None:
            return {'status': 'not_applicable', 'reason': 'original_release_source_or_delivery_scope'}
        delivery._create(ctx, 'baseline', 0, selected, None)
    except OperationError as exc:
        # Delivery has already been verified. A later stop/version conflict may
        # prevent the new local selection, but cannot erase that delivery fact.
        return {'status': 'not_updated', 'reason': exc.code}
    raise Wait('waiting_external', 'Original verified-deployment baseline selection admitted', 1)


async def bounded_read(ctx, read):
    claim = ctx.op.get('external_refs', {}).get(delivery.LINK)
    if not claim:
        return await read()
    rows = delivery._rows(ctx.service, {'operation_id': claim}, 'baseline')
    if not any(row['operation_id'] == ctx.operation_id for row in rows):
        return await read()
    timeout = delivery.child_timeout(ctx.service, ctx.op, 30)
    try:
        return await asyncio.wait_for(read(), timeout)
    except asyncio.TimeoutError:
        raise OperationError('PM_REPAIR_BASELINE_READ_TIMEOUT', 'Original baseline observation exceeded its bounded deadline', 409) from None
