"""Exact Review candidate provenance for an explicit, unconfirmed Idea input."""
from __future__ import annotations

import json
import re

from .operations import OperationError

KIND = 'maintenance_review_candidate'
ACTION = 'pm.input.record'
FIELDS = {'kind', 'review_id', 'event_id', 'document_digest', 'operation_id'}


def invalid():
    return OperationError('PM_REVIEW_CANDIDATE_INVALID', 'Select one original maintenance Review candidate', 422)


def validate(ref):
    if (not isinstance(ref, dict) or set(ref) != FIELDS or ref.get('kind') != KIND
            or any(not isinstance(ref.get(key), str) or not re.fullmatch(pattern, ref[key]) for key, pattern in (
                ('review_id', r'pmrevw_[0-9a-f]{32}'), ('event_id', r'pmrve_[0-9a-f]{32}'),
                ('document_digest', r'[0-9a-f]{64}'), ('operation_id', r'op_[0-9a-f]{32}')))):
        raise invalid()
    return ref


def reference(event):
    return {'kind': KIND, **{key: event[key] for key in ('review_id', 'event_id', 'document_digest', 'operation_id')}}


def original(db, workspace_id, ref):
    from . import pm_maintenance_review as review
    validate(ref)
    event = review._event(db, workspace_id, ref['review_id'], ref['event_id'])
    if event['kind'] != 'candidate.record' or reference(event) != ref:
        raise invalid()
    row = db.execute('SELECT action,target FROM operations WHERE operation_id=?', (event['operation_id'],)).fetchone()
    if (row is None or row['action'] != 'pm.maintenance_review.candidate.record'
            or json.loads(row['target']) != {'workspace_id': workspace_id, 'review_id': ref['review_id']}):
        raise invalid()
    return event


def require(ops, principal, workspace_id, ref):
    from . import pm_maintenance_review as review
    event = original(ops.db, workspace_id, ref)
    # Includes the original complete snapshot, candidate source binding and current
    # membership/provider access; neither latest Review nor latest completion wins.
    if not review.operation_visible(ops.db, principal, {'operation_id': event['operation_id']}):
        raise OperationError('PM_SOURCE_UNAVAILABLE', 'Original maintenance candidate sources are unavailable', 404)
    return event


def operation_visible(db, principal, operation):
    from .pm_workload import _Reader
    try:
        binding = operation['external_refs']['admission_binding']
        ref = binding['pm_review_candidate']
        if (operation['action'] != ACTION or ref != operation['params']['source_ref']
                or binding['pm_lineage']['workspace_id'] != operation['target']['workspace_id']):
            return False
        event = require(_Reader(db), principal, operation['target']['workspace_id'], ref)
        return operation['params']['value'] == event['document']['title'] and operation['params']['status'] == 'unresolved'
    except (OperationError, KeyError, TypeError, ValueError):
        return False


def projection(db, workspace_id, ref):
    event = original(db, workspace_id, ref)
    return {key: event[key] for key in ('kind', 'document', 'document_digest', 'event_id', 'review_id', 'operation_id', 'actor')}
