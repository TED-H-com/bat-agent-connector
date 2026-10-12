"""Fixed release/policy/workload evidence for the existing lifecycle decisions."""
from __future__ import annotations

import json
import re

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
    if (type(ref['policy_version']) is not int or not 1 <= ref['policy_version'] < 2**63
            or any(not isinstance(ref[key], str) or not re.fullmatch(pattern, ref[key]) for key, pattern in (
                ('policy_id', r'pmmp_[0-9a-f]{32}'), ('baseline_id', r'pmrb_[0-9a-f]{32}'),
                ('snapshot_id', r'pmwl_[0-9a-f]{32}'), ('snapshot_digest', r'[0-9a-f]{64}')))):
        raise OperationError('INVALID_PARAMS', 'Use exact bounded maintenance evidence identities', 422)
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
        request = _request(ctx.op, ref)
        operation, fresh = ops.create(principal, **request, entry='maintenance')
        if not fresh:
            raise OperationError('PM_MAINTENANCE_CHILD_CHANGED', 'A lifecycle child exists without its original local receipt', 409)
        ops.db.execute('INSERT INTO pm_maintenance_check_children VALUES(?,?,?,?,?)',
            (ctx.operation_id, 'lifecycle', operation['operation_id'], checks._json(request), checks._digest(request)))
        ops._merge_refs(operation['operation_id'], {'pm_maintenance_lifecycle_parent': ctx.operation_id})
        return {'operation_id': operation['operation_id'], 'reason': None}
    return ctx.effect('pm.maintenance.lifecycle', create)


def _request(parent, ref):
    return {'action': 'pm.lifecycle.set_stage', 'target': parent['target'],
        'params': {'stage': 'maintain', 'reason': 'Enter maintenance under the adopted idle policy and original workload evidence',
            'maintenance_ref': ref}, 'preconditions': {'expected_lifecycle_revision': parent['preconditions']['expected_lifecycle_revision']},
        'idempotency_key': 'maintenance-lifecycle:'+parent['operation_id']}


def require_automatic_parent(ops, op):
    """Final local decision checks the original delegated owner and deadline.

    Completed checks may finish this reserved transition; arbitrary terminal
    requests, cancelled parents and expired/revoked authority cannot do so.
    """
    from . import pm_maintenance_checks as checks
    parent_id = (op.get('external_refs') or {}).get('pm_maintenance_lifecycle_parent')
    installed = workload.sources.has_table(ops.db, 'pm_maintenance_check_children')
    link = ops.db.execute('SELECT * FROM pm_maintenance_check_children WHERE operation_id=? AND slot=?',
        (op['operation_id'], 'lifecycle')).fetchone() if installed else None
    if parent_id is None and link is None:
        return
    try:
        if not link or parent_id != link['parent_id']:
            raise ValueError
        parent = ops.get(parent_id, steps=False)
        row = checks._row(ops, parent_id)
        child = checks._child(ops, parent_id, 'workload')
        if row is None or child is None or child['status'] != 'succeeded':
            raise ValueError
        original = workload._record(ops.db, parent['target']['workspace_id'], child['result']['snapshot_id'])
        request = json.loads(link['request'])
        if (request != _request(parent, reference(original)) or checks._digest(request) != link['request_digest']
                or any(op[key] != request[key] for key in request) or op['actor'] != parent['actor']):
            raise ValueError
    except (ValueError, KeyError, TypeError):
        raise OperationError('PM_MAINTENANCE_CHILD_CHANGED', 'Original lifecycle child identity is unproven', 409) from None
    now = checks.time.time()
    if parent['cancel_requested'] or parent['status'] in {'failed', 'cancelled', 'needs_attention'}:
        raise OperationError('PM_MAINTENANCE_PARENT_STOPPED', 'The original check cannot authorize a new transition', 409)
    if now < row['created_at'] or now >= row['deadline_at']:
        raise OperationError('PM_MAINTENANCE_DEADLINE', 'The original check deadline prevents this transition', 409)
    intent = checks._intent(parent)
    policy = policies._record(ops.db, parent['target']['workspace_id'], intent['workload']['policy_id'])
    principal = checks._current_principal(ops, policy, now)
    if checks._check(ops, principal, parent['target'], parent['params'], parent['preconditions']) != intent:
        raise OperationError('PM_MAINTENANCE_CONTEXT_CHANGED', 'Original check context changed before the lifecycle decision', 409)
