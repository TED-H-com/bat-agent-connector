"""Serialize PM investment admission with lifecycle stops in the existing journal.

These hooks receive server-owned admission bindings, never HTTP lineage. A future
coding handoff can bind ``pm_lineage.workspace_id`` without acquiring another queue.
Original external step reconciliation never calls a new-admission hook.
"""
from __future__ import annotations

import hashlib
import json
import re
import time

from .operations import OperationError

# Explicit contributions/results and the registered bounded GitHub GET remain
# possible while investment is stopped.
# None of these actions launches a runtime, changes accepted product scope, or
# selects new development work. Their existing scope/source/owner checks still run.
RECORD_ACTIONS = frozenset({
    'pm.history.link', 'pm.history.unlink',
    'pm.workspace.adapter.select',
    'pm.work_batch.evidence.record',
    'pm.maintenance.repair.evidence.record',
    'pm.quality.review.create', 'pm.quality.review.record', 'pm.quality.review.revoke',
    'pm.maintenance.repair.runtime.observe', 'pm.execution.runtime.observe', 'pm.session.quiescence.observe',
    'pm.workspace.member.set', 'pm.message.append', 'pm.thread.rename', 'pm.input.record',
    'pm.ahp.observe', 'pm.evidence.capture', 'pm.snapshot.create', 'pm.roadmap.observe', 'pm.workload.capture', 'pm.github.observe',
    'pm.meeting.note', 'pm.meeting.complete', 'pm.meeting.follow_up.record',
    'pm.governance.adoption.link', 'pm.sprint.close', 'pm.sprint.cancel',
    'pm.maintenance_review.prepare', 'pm.maintenance_review.note', 'pm.maintenance_review.candidate.record',
    'pm.maintenance_review.complete', 'pm.maintenance_review.follow_up.record',
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
    # Original PM session observation remains usable while investment is paused.
    # The lineage is service-admitted; it is never accepted from caller params.
    if action in {'session.read', 'session.position'} and (binding or {}).get('pm_source_roots'):
        return 'record'
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
    if not bookkeeping:
        from .pm_conversation_adoption import require_child
        require_child(db, operation_id)
        from .pm_work_start import require_child as require_work_start_child
        require_work_start_child(db, operation_id)
        from .pm_work_adopt_and_start import require_child as require_adopt_start_child
        require_adopt_start_child(db, operation_id)
    binding = operation_binding(db, operation_id)
    if binding and binding['kind'] == 'investment' and not bookkeeping:
        _require(head(db, binding['workspace_id']), binding['execution_epoch'])


def _github_observation_step(db, operation_id, name, binding, current):
    """Only the registered observer's fixed GET pages may run while stopped.

    Neither a caller-selected step name nor another record action grants this
    exception. The same original lifecycle revision and epoch must still hold;
    a pause/reopen during traversal invalidates all remaining page requests.
    """
    if not re.fullmatch(r'pm\.github\.page\.[12]\.[0-9]{3}', name):
        return False
    row = db.execute('SELECT action,external_refs FROM operations WHERE operation_id=?', (operation_id,)).fetchone()
    if not row or row['action'] != 'pm.github.observe' or binding['kind'] != 'record':
        return False
    admitted = json.loads(row['external_refs'] or '{}').get('admission_binding') or {}
    intent = admitted.get('github_observation_intent')
    if not isinstance(intent, dict):
        raise OperationError('PM_LIFECYCLE_BINDING_INVALID', 'Original observation lifecycle binding is unavailable', 409)
    digest = hashlib.sha256(json.dumps(intent, ensure_ascii=False, sort_keys=True,
        separators=(',', ':'), allow_nan=False).encode('utf-8')).hexdigest()
    if digest != admitted.get('github_observation_intent_digest'):
        raise OperationError('PM_LIFECYCLE_BINDING_INVALID', 'Original observation lifecycle binding changed', 409)
    if (binding['execution_epoch'] != current['execution_epoch']
            or intent.get('execution_epoch') != current['execution_epoch']
            or intent.get('lifecycle_revision') != current['revision']):
        raise OperationError('PM_LIFECYCLE_EPOCH_CHANGED', 'Explicitly request a new observation of the current lifecycle', 409)
    return True


def admit_step(db, operation_id, name, *, local=False, bookkeeping=False, restart=False):
    """An admitted external step may finish after stop; a proven-unsent retry may not."""
    binding = operation_binding(db, operation_id)
    if binding is None:
        return
    # Record/lifecycle actions cannot smuggle new external effects through their
    # exception. Only the registered observer's bounded GET pages qualify.
    current = head(db, binding['workspace_id'])
    if not bookkeeping and (not local or binding['kind'] == 'investment'):
        if local or not _github_observation_step(db, operation_id, name, binding, current):
            _require(current, binding['execution_epoch'])
    db.execute('''INSERT INTO pm_lifecycle_steps(operation_id,name,workspace_id,execution_epoch,kind,admitted_at)
        VALUES(?,?,?,?,?,?) ON CONFLICT(operation_id,name) DO UPDATE SET
        execution_epoch=excluded.execution_epoch,admitted_at=excluded.admitted_at''',
        (operation_id, name, binding['workspace_id'], binding['execution_epoch'],
         'bookkeeping' if bookkeeping else 'local' if local else 'external', time.time()))
