"""Fixed release/policy/workload evidence for the existing lifecycle decisions."""
from __future__ import annotations

from . import pm_lifecycle_gates as gates
from . import pm_maintenance_policy as policies
from . import pm_release_baselines as baselines
from . import pm_workload as workload
from .operations import OperationError

FIELDS = {'policy_id', 'policy_version', 'baseline_id', 'snapshot_id', 'snapshot_digest'}


def reference(row):
    intent = row['source_binding']['intent']
    return {'policy_id': intent['policy_id'], 'policy_version': intent['version'],
        'baseline_id': intent['baseline_id'], 'snapshot_id': row['snapshot_id'],
        'snapshot_digest': row['document_digest']}


def sources(ops, principal, wid, ref):
    if not isinstance(ref, dict) or set(ref) != FIELDS:
        raise OperationError('PM_MAINTENANCE_UNAVAILABLE', 'Select original release, policy and workload evidence', 409)
    row = workload._record(ops.db, wid, ref['snapshot_id'])
    workload._access(ops, principal, wid, row)
    if reference(row) != ref:
        raise OperationError('PM_MAINTENANCE_CONTEXT_CHANGED', 'Original maintenance evidence does not match', 409)
    return row


def require(ops, principal, wid, ref, *, reopening=False):
    if not principal.allows('observe'):
        raise OperationError('FORBIDDEN', 'Maintenance admission requires observe and manage', 403)
    if not workload.sources.has_table(ops.db, 'pm_workload_snapshots'):
        raise OperationError('PM_MAINTENANCE_UNAVAILABLE', 'A release baseline and adopted maintenance policy are required', 409)
    with baselines._snapshot(ops.db):
        sources(ops, principal, wid, ref)
        active = policies.active_record(ops, principal, wid)
        decision = workload.evaluate_for_maintenance(ops, principal, wid, ref['snapshot_id'], active, allow_stopped=reopening)
        if not decision['eligible']:
            raise OperationError('PM_MAINTENANCE_NOT_READY', 'Current fixed maintenance evidence is not eligible: '+', '.join(decision['reasons']), 409)
        return decision


def availability(ops, principal, wid):
    if not workload.sources.has_table(ops.db, 'pm_workload_snapshots'):
        return {'available': False, 'reason': 'release_baseline_and_policy_required'}
    current = gates.head(ops.db, wid)
    if current['stage'] not in {'development', 'maintain', 'paused', 'closed'}:
        return {'available': False, 'reason': 'development_or_explicit_reopen_required'}
    try:
        with baselines._snapshot(ops.db):
            row = workload.current_snapshot(ops, principal, wid)['snapshot']
            if row is None:
                return {'available': False, 'reason': 'current_workload_required'}
            fixed = workload._record(ops.db, wid, row['snapshot_id'])
            ref = reference(fixed)
            require(ops, principal, wid, ref, reopening=current['stage'] in gates.STOPPED)
            return {'available': True, 'reason': None, 'reference': ref}
    except OperationError as exc:
        return {'available': False, 'reason': exc.code}


def admit_from_check(ctx, snapshot_id):
    """One durable local child; the original adopted policy authorizes this stage.

    The ordinary registered lifecycle action owns final CAS, fresh source checks
    and history. Finishing a check does not itself mutate a workspace stage.
    """
    from . import pm_maintenance_checks as checks
    def create():
        ops, wid = ctx.service, ctx.target['workspace_id']
        principal, _ = checks._guard(ops, ops.get(ctx.operation_id, steps=False))
        current = gates.head(ops.db, wid)
        if current['stage'] != 'development':
            return {'operation_id': None, 'reason': 'no_development_transition_required'}
        row = workload._record(ops.db, wid, snapshot_id)
        ref = reference(row)
        try:
            require(ops, principal, wid, ref)
        except OperationError as exc:
            if exc.status not in {403, 404, 409}:
                raise
            return {'operation_id': None, 'reason': exc.code}
        operation, _ = ops.create(principal, action='pm.lifecycle.set_stage', target={'workspace_id': wid},
            params={'stage': 'maintain', 'reason': 'Enter maintenance under the adopted idle policy and original workload evidence',
                'maintenance_ref': ref}, preconditions={'expected_lifecycle_revision': current['revision']},
            idempotency_key='maintenance-lifecycle:'+ctx.operation_id, entry='maintenance')
        return {'operation_id': operation['operation_id'], 'reason': None}
    return ctx.effect('pm.maintenance.lifecycle', create)
