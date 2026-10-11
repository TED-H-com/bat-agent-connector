"""Serialize PM investment admission with lifecycle stops in the existing journal.

These hooks receive server-owned admission bindings, never HTTP lineage. A future
coding handoff can bind ``pm_lineage.workspace_id`` without acquiring another queue.
Original external step reconciliation never calls a new-admission hook.
"""
from __future__ import annotations

import hashlib
import json
import time

from .operations import OperationError

# Explicit local contributions/results remain possible while investment is stopped.
# None of these actions launches a runtime, changes accepted product scope, or
# selects new development work. Their existing scope/source/owner checks still run.
RECORD_ACTIONS = frozenset({
    'pm.workspace.member.set', 'pm.message.append', 'pm.input.record',
    'pm.evidence.capture', 'pm.snapshot.create', 'pm.roadmap.observe', 'pm.workload.capture',
    'pm.meeting.note', 'pm.meeting.complete', 'pm.meeting.follow_up.record',
    'pm.governance.adoption.link', 'pm.sprint.close', 'pm.sprint.cancel',
})
STOPPED = frozenset({'paused', 'closed'})
# UI draft hints only; backend classification uses the original trusted binding.
RECORD_VARIANTS = {'pm.issue.cancel': {'absent_params': ['public_reason']}}


def installed(db):
    return db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='pm_lifecycle_heads'").fetchone() is not None


def head(db, wid):
    row = db.execute('SELECT * FROM pm_lifecycle_heads WHERE workspace_id=?', (wid,)).fetchone() if installed(db) else None
    return dict(row) if row else {'workspace_id': wid, 'stage': None, 'revision': 0, 'execution_epoch': 0,
                                  'transition_id': None, 'updated_at': None, 'coverage': 'legacy_unclassified'}


def lineage(action, target, binding):
    """Only the service's registered admit function can provide extra lineage."""
    fixed = (binding or {}).get('pm_lineage')
    wid = target.get('workspace_id') if action.startswith('pm.') else None
    if fixed is not None:
        if not isinstance(fixed, dict) or set(fixed) != {'workspace_id'} or not isinstance(fixed['workspace_id'], str):
            raise OperationError('PM_LIFECYCLE_BINDING_INVALID', 'Invalid server-bound PM lineage', 409)
        if wid is not None and wid != fixed['workspace_id']:
            raise OperationError('PM_LIFECYCLE_BINDING_INVALID', 'Conflicting PM workspace lineage', 409)
        wid = fixed['workspace_id']
    return wid


def kind(action, binding=None):
    if action.startswith('pm.lifecycle.'):
        return 'lifecycle'
    if action == 'pm.issue.cancel':
        intent = (binding or {}).get('issue_intent')
        if (isinstance(intent, dict) and intent.get('kind') == 'cancel'
                and isinstance(intent.get('selected'), dict) and set(intent['selected']) == {'rationale'}):
            try:
                digest = hashlib.sha256(json.dumps(intent, ensure_ascii=False, sort_keys=True,
                    separators=(',', ':'), allow_nan=False).encode('utf-8')).hexdigest()
            except (TypeError, ValueError, UnicodeError):
                digest = None
            if digest is not None and digest == binding.get('issue_intent_digest'):
                return 'record'
    return 'record' if action in RECORD_ACTIONS else 'investment'


def _require(current, epoch):
    if current['stage'] in STOPPED:
        raise OperationError('PM_LIFECYCLE_STOPPED', 'This workspace has stopped new investment; original effects may still be reconciled', 409)
    if epoch != current['execution_epoch']:
        raise OperationError('PM_LIFECYCLE_EPOCH_CHANGED', 'This intent belongs to an earlier execution epoch; explicitly submit new work after reopening', 409)


def admit_operation(db, operation_id, action, target, binding):
    """Called inside the same INSERT transaction as the operation's original intent."""
    wid = lineage(action, target, binding)
    if not wid or not installed(db):
        return
    current, mode = head(db, wid), kind(action, binding)
    if mode == 'investment':
        _require(current, current['execution_epoch'])
    db.execute('INSERT INTO pm_lifecycle_operations VALUES(?,?,?,?,?)',
               (operation_id, wid, current['execution_epoch'], mode, time.time()))


def operation_binding(db, operation_id):
    if not installed(db):
        return None
    saved = db.execute('SELECT * FROM pm_lifecycle_operations WHERE operation_id=?', (operation_id,)).fetchone()
    if saved:
        return dict(saved)
    # Older queued PM operations carry epoch 0, never the newly reopened epoch.
    row = db.execute('SELECT action,target,external_refs FROM operations WHERE operation_id=?', (operation_id,)).fetchone()
    if row is None:
        return None
    refs = json.loads(row['external_refs'] or '{}')
    wid = lineage(row['action'], json.loads(row['target']), refs.get('admission_binding'))
    return {'workspace_id': wid, 'execution_epoch': 0, 'kind': kind(row['action'], refs.get('admission_binding'))} if wid else None


def require_local(db, operation_id, *, bookkeeping=False):
    """Invoke after receipt lookup, INSIDE the local mutation transaction."""
    binding = operation_binding(db, operation_id)
    if binding and binding['kind'] == 'investment' and not bookkeeping:
        _require(head(db, binding['workspace_id']), binding['execution_epoch'])


def admit_step(db, operation_id, name, *, local=False, bookkeeping=False, restart=False):
    """An admitted external step may finish after stop; a proven-unsent retry may not."""
    binding = operation_binding(db, operation_id)
    if binding is None:
        return
    # Even record/lifecycle actions may not smuggle a new external action through
    # their exception. Today they have no external steps. Read-back bypasses here.
    current = head(db, binding['workspace_id'])
    if not bookkeeping and (not local or binding['kind'] == 'investment'):
        _require(current, binding['execution_epoch'])
    db.execute('''INSERT INTO pm_lifecycle_steps(operation_id,name,workspace_id,execution_epoch,kind,admitted_at)
        VALUES(?,?,?,?,?,?) ON CONFLICT(operation_id,name) DO UPDATE SET
        execution_epoch=excluded.execution_epoch,admitted_at=excluded.admitted_at''',
        (operation_id, name, binding['workspace_id'], binding['execution_epoch'],
         'bookkeeping' if bookkeeping else 'local' if local else 'external', time.time()))
