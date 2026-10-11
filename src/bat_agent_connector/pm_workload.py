"""W45 immutable workload observations; a snapshot never starts maintenance.

The policy supplies trusted scope and freshness. Capture reads the existing
journal only; unknown/uncovered sources never become an idle assertion.
"""
from __future__ import annotations

import json
import math
import re
import time

from . import pm_lifecycle_gates, pm_workspace
from . import pm_release_baselines as baselines
from . import pm_workload_sources as sources
from .operations import ActionDef, OperationError, OperationService

ACTION = 'pm.workload.capture'
SNAPSHOT = re.compile(r'pmwl_[0-9a-f]{32}')
MAX_READ_BYTES = 2_000_000
MAX_STABILITY_RECORDS = 512


def _bad(code, message, status=422):
    return OperationError(code, message, status)


def _policy():
    from . import pm_maintenance_policy
    return pm_maintenance_policy


def _size(value):
    return len(json.dumps(value, ensure_ascii=False, allow_nan=False).encode('utf-8'))


def _bounded(value):
    if _size(value) > MAX_READ_BYTES:
        raise _bad('PM_WORKLOAD_READ_TOO_LARGE', 'A whole workload record exceeds the read byte limit', 413)
    return value


def schema(journal):
    baselines.schema(journal)
    with journal.tx():
        journal.db.execute('''CREATE TABLE IF NOT EXISTS pm_workload_snapshots (
            sequence INTEGER PRIMARY KEY AUTOINCREMENT, snapshot_id TEXT NOT NULL UNIQUE,
            workspace_id TEXT NOT NULL, policy_id TEXT NOT NULL, baseline_id TEXT NOT NULL,
            document TEXT NOT NULL, document_digest TEXT NOT NULL, source_binding TEXT NOT NULL,
            actor TEXT NOT NULL, created_at REAL NOT NULL, operation_id TEXT NOT NULL UNIQUE)''')
        for verb in ('UPDATE', 'DELETE'):
            journal.db.execute(f'CREATE TRIGGER IF NOT EXISTS pm_workload_{verb.lower()}_immutable '
                f'BEFORE {verb} ON pm_workload_snapshots '
                "BEGIN SELECT RAISE(ABORT, 'Workload observations are immutable'); END")


def install(ops):
    schema(ops.journal)
    if ACTION not in ops.actions:
        ops.register(ActionDef(ACTION, 'manage', 'Capture current bounded workload evidence without executing work',
            _run, _admit, ('workspace_id',), authorize_existing=_authorize_existing))


def capabilities():
    return {'version': 1, 'capture': 'saved_central_sources', 'network_refresh': False,
        'automatic_maintenance': False, 'full_github_repository_coverage': False,
        'classification': ['development', 'unknown', 'idle_candidate']}


def _member(ops, principal, wid, *, write=False):
    if not principal.allows('manage' if write else 'observe'):
        raise _bad('FORBIDDEN', 'Workload capture needs manage; reads need observe', 403)
    return pm_workspace.require_member(ops.db, principal, wid, write=write)


def _intent(policy, lifecycle):
    return {key: policy[key] for key in ('policy_id', 'version', 'document_digest', 'baseline_id',
        'baseline_digest', 'scope', 'observation')} | {
        'lifecycle_revision': lifecycle['revision'], 'execution_epoch': lifecycle['execution_epoch']}


def _check(ops, principal, target, params, pre):
    if (not isinstance(target, dict) or set(target) != {'workspace_id'} or not isinstance(params, dict)
            or set(params) != {'policy_id'} or not isinstance(pre, dict) or set(pre) != {
                'expected_policy_version', 'expected_baseline_id', 'expected_lifecycle_revision'}):
        raise _bad('INVALID_PARAMS', 'Use exact workload policy, baseline and lifecycle fields')
    wid = target['workspace_id']
    _member(ops, principal, wid, write=True)
    if (not isinstance(params['policy_id'], str) or not re.fullmatch(r'pmmp_[0-9a-f]{32}', params['policy_id'])
            or type(pre['expected_policy_version']) is not int or not 1 <= pre['expected_policy_version'] < 2**63
            or type(pre['expected_lifecycle_revision']) is not int or not 0 <= pre['expected_lifecycle_revision'] < 2**63
            or not isinstance(pre['expected_baseline_id'], str) or not baselines.BASELINE.fullmatch(pre['expected_baseline_id'])):
        raise _bad('INVALID_PARAMS', 'Select exact current policy and lifecycle revisions and a fixed baseline ID')
    policy = _policy().active_record(ops, principal, wid)
    lifecycle = pm_lifecycle_gates.head(ops.db, wid)
    if (policy['policy_id'] != params['policy_id'] or policy['version'] != pre['expected_policy_version']
            or policy['baseline_id'] != pre['expected_baseline_id'] or lifecycle['revision'] != pre['expected_lifecycle_revision']):
        raise _bad('PM_WORKLOAD_CONTEXT_CHANGED', 'Read the current policy, baseline and lifecycle before capturing', 409)
    return _intent(policy, lifecycle)


def _admit(ops, principal, target, params, pre):
    with ops.journal.tx():
        intent = _check(ops, principal, target, params, pre)
    return {'principal': pm_workspace.principal_binding(principal), 'workload_intent': intent,
        'workload_intent_digest': sources.digest(intent)}


def _record(db, wid, sid):
    if not isinstance(sid, str) or not SNAPSHOT.fullmatch(sid):
        raise _bad('PM_WORKLOAD_NOT_FOUND', 'Workload observation not found', 404)
    row = db.execute('SELECT * FROM pm_workload_snapshots WHERE workspace_id=? AND snapshot_id=?', (wid, sid)).fetchone()
    if not row:
        raise _bad('PM_WORKLOAD_NOT_FOUND', 'Workload observation not found', 404)
    result = dict(row)
    try:
        result['document'] = json.loads(row['document'])
        result['source_binding'] = json.loads(row['source_binding'])
        if (sources.digest(result['document']) != row['document_digest']
                or result['document']['snapshot_id'] != row['snapshot_id']
                or result['document']['workspace_id'] != wid
                or result['document']['source_binding_digest'] != sources.digest(result['source_binding'])):
            raise ValueError
    except (ValueError, KeyError, TypeError):
        raise _bad('PM_WORKLOAD_CORRUPT', 'The fixed observation cannot be verified', 409) from None
    return result


class _Reader:
    def __init__(self, db):
        self.db = db

    def get(self, oid, *, steps=False, principal=None):
        from . import pm_access
        row = self.db.execute('SELECT * FROM operations WHERE operation_id=?', (oid,)).fetchone()
        if not row:
            raise _bad('NOT_FOUND', 'Original workload source unavailable', 404)
        operation = OperationService._decode(row)
        if principal is not None:
            pm_access.require_visible(self.db, principal, operation)
        return operation


def _intent_access(ops, principal, wid, intent):
    pm_workspace.require_member(ops.db, principal, wid)
    original = _policy().fixed_record(ops, principal, wid, intent['policy_id'])
    if (original['document_digest'] != intent['document_digest'] or original['scope'] != intent['scope']
            or original['version'] != intent['version'] or original['observation'] != intent['observation']):
        raise _bad('NOT_FOUND', 'Original workload policy unavailable', 404)
    baseline = baselines._record(ops.db, wid, intent['baseline_id'])
    if baseline['document_digest'] != intent['baseline_digest']:
        raise _bad('NOT_FOUND', 'Original workload baseline unavailable', 404)
    baselines._access(ops, principal, wid, baseline)


def _access(ops, principal, wid, row):
    binding = row['source_binding']
    _intent_access(ops, principal, wid, binding['intent'])
    for ref in binding['source_refs']:
        if not sources.visible(ops, principal, wid, ref):
            raise _bad('NOT_FOUND', 'Original workload source unavailable', 404)


def operation_visible(db, principal, operation):
    with baselines._snapshot(db):
        row = db.execute('SELECT * FROM operations WHERE operation_id=?', (operation.get('operation_id'),)).fetchone()
        if not row or row['action'] != ACTION:
            return False
        op = OperationService._decode(row)
        binding = (op.get('external_refs') or {}).get('admission_binding') or {}
        intent = binding.get('workload_intent')
        try:
            if not isinstance(intent, dict) or sources.digest(intent) != binding.get('workload_intent_digest'):
                return False
            wid, reader = op['target']['workspace_id'], _Reader(db)
            _intent_access(reader, principal, wid, intent)
            saved = db.execute('SELECT snapshot_id FROM pm_workload_snapshots WHERE operation_id=?', (op['operation_id'],)).fetchone()
            if saved:
                _access(reader, principal, wid, _record(db, wid, saved[0]))
            return True
        except (OperationError, ValueError, TypeError, KeyError):
            return False


def _authorize_existing(ops, principal, operation, verb):
    if not principal.allows('manage') or not operation_visible(ops.db, principal, operation):
        raise _bad('NOT_FOUND', 'Original workload operation unavailable', 404)
    if verb != 'replay':
        _member(ops, principal, operation['target']['workspace_id'], write=True)


def _stability(db, wid, intent, current, now):
    required = intent['observation']['stability_seconds']
    result = {'required_seconds': required, 'observed_since': None, 'satisfied': False,
        'basis': 'overlapping_complete_observations', 'history_limited': False}
    if current['classification'] != 'idle_candidate' or current['expires_at'] <= now:
        return result
    since, next_time = now, now
    rows = db.execute('''SELECT document,source_binding FROM pm_workload_snapshots
        WHERE workspace_id=? ORDER BY sequence DESC LIMIT ?''', (wid, MAX_STABILITY_RECORDS + 1)).fetchall()
    for row in rows[:MAX_STABILITY_RECORDS]:
        previous, binding = json.loads(row[0]), json.loads(row[1])
        if (binding['intent'] != intent or previous['classification'] != 'idle_candidate'
                or previous['activity_digest'] != current['activity_digest']
                or not previous['as_of'] <= next_time < previous['expires_at']):
            break
        since, next_time = previous['as_of'], previous['as_of']
        if now - since >= required:
            break
    else:
        result['history_limited'] = len(rows) > MAX_STABILITY_RECORDS
    result.update(observed_since=since, satisfied=now - since >= required)
    return result


async def _run(ctx):
    def capture():
        ctx.check_cancel()
        principal = pm_workspace.admitted_principal(ctx)
        intent = _check(ctx.service, principal, ctx.target, ctx.params, ctx.preconditions)
        binding = ctx.admission_binding or {}
        if intent != binding.get('workload_intent') or sources.digest(intent) != binding.get('workload_intent_digest'):
            raise _bad('PM_WORKLOAD_CONTEXT_CHANGED', 'Original workload context changed', 409)
        wid, now = ctx.target['workspace_id'], time.time()
        facts = sources.Collector(ctx.service, principal, wid, intent, now).collect()
        access = {'intent': intent, 'source_refs': facts.pop('source_refs')}
        stability = _stability(ctx.service.db, wid, intent, facts, now)
        sid = 'pmwl_' + ctx.operation_id[3:]
        document = {'schema_version': 1, 'snapshot_id': sid, 'workspace_id': wid,
            'policy_id': intent['policy_id'], 'policy_version': intent['version'], 'baseline_id': intent['baseline_id'],
            'operation_id': ctx.operation_id, 'as_of': now, 'scope': intent['scope'],
            'lifecycle_revision': intent['lifecycle_revision'], 'execution_epoch': intent['execution_epoch'],
            **facts, 'stability': stability, 'next_check_at': min(facts['expires_at'], now + intent['observation']['max_age_seconds']),
            'source_binding_digest': sources.digest(access), 'automatic_maintenance_authorized': False,
            'quality': 'unassessed', 'network_checked': False}
        document_digest = sources.digest(document)
        # Reserve the same complete metadata envelope public reads return.
        _bounded({'workspace_id': wid, 'snapshot': {'sequence': 2**63 - 1, 'snapshot_id': sid,
            'workspace_id': wid, 'policy_id': intent['policy_id'], 'baseline_id': intent['baseline_id'],
            'document': document, 'document_digest': document_digest, 'actor': ctx.actor,
            'created_at': now, 'operation_id': ctx.operation_id}, 'visibility_filtered': False})
        ctx.service.db.execute('''INSERT INTO pm_workload_snapshots(snapshot_id,workspace_id,policy_id,baseline_id,
            document,document_digest,source_binding,actor,created_at,operation_id) VALUES(?,?,?,?,?,?,?,?,?,?)''',
            (sid, wid, intent['policy_id'], intent['baseline_id'], baselines._json(document), document_digest,
             baselines._json(access), ctx.actor, now, ctx.operation_id))
        result = {'workspace_id': wid, 'snapshot_id': sid, 'document_digest': document_digest,
            'classification': facts['classification'], 'automatic_maintenance_authorized': False}
        ctx.service.journal.api_event('pm_workload', sid, 'pm.workload.captured',
            {**result, 'operation_id': ctx.operation_id}, actor=ctx.actor)
        return result
    # The original local receipt is returned before cancellation, policy or
    # lifecycle checks. Public visibility is independently re-evaluated.
    return ctx.effect('pm.workload.capture', capture)


def _view(row):
    return {key: row[key] for key in ('sequence', 'snapshot_id', 'workspace_id', 'policy_id', 'baseline_id',
        'document', 'document_digest', 'actor', 'created_at', 'operation_id')}


@baselines._snapshot_read
def get_snapshot(ops, principal, workspace_id, snapshot_id):
    _member(ops, principal, workspace_id)
    row = _record(ops.db, workspace_id, snapshot_id)
    _access(ops, principal, workspace_id, row)
    return _bounded({'workspace_id': workspace_id, 'snapshot': _view(row)})


@baselines._snapshot_read
def current_snapshot(ops, principal, workspace_id):
    _member(ops, principal, workspace_id)
    row = ops.db.execute('SELECT snapshot_id FROM pm_workload_snapshots WHERE workspace_id=? ORDER BY sequence DESC LIMIT 1',
        (workspace_id,)).fetchone()
    if not row:
        return {'workspace_id': workspace_id, 'snapshot': None, 'visibility_filtered': False}
    try:
        return _bounded({**get_snapshot(ops, principal, workspace_id, row[0]), 'visibility_filtered': False})
    except OperationError as exc:
        if exc.status not in (403, 404):
            raise
        return {'workspace_id': workspace_id, 'snapshot': None, 'visibility_filtered': True}


@baselines._snapshot_read
def list_snapshots(ops, principal, workspace_id, *, after=0, limit=20):
    _member(ops, principal, workspace_id)
    if type(after) is not int or not 0 <= after < 2**63 or type(limit) is not int or not 1 <= limit <= 100:
        raise _bad('INVALID_PARAMS', 'Use a nonnegative signed 64-bit cursor and limit 1–100')
    rows = ops.db.execute('''SELECT snapshot_id,sequence FROM pm_workload_snapshots
        WHERE workspace_id=? AND sequence>? ORDER BY sequence LIMIT ?''', (workspace_id, after, limit + 1)).fetchall()
    result = {'workspace_id': workspace_id, 'snapshots': [], 'next_cursor': None, 'visibility_filtered': False}
    cursor = after
    for stub in rows[:limit]:
        row = _record(ops.db, workspace_id, stub['snapshot_id'])
        try:
            _access(ops, principal, workspace_id, row)
        except OperationError as exc:
            if exc.status not in (403, 404):
                raise
            result['visibility_filtered'], cursor = True, stub['sequence']
            continue
        result['snapshots'].append(_view(row))
        result['next_cursor'] = stub['sequence']
        # The terminal null cursor can be wider than a small sequence ID.
        # Reserve its width before accepting a whole record into the page.
        if _size(result) + max(0, 4 - len(str(stub['sequence']))) > MAX_READ_BYTES:
            result['snapshots'].pop()
            if cursor == after:
                raise _bad('PM_WORKLOAD_READ_TOO_LARGE', 'One whole workload observation exceeds the read byte limit', 413)
            result['next_cursor'] = cursor
            return _bounded(result)
        cursor = stub['sequence']
    result['next_cursor'] = cursor if len(rows) > limit else None
    return _bounded(result)


def evaluate_for_maintenance(ops, principal, workspace_id, snapshot_id, policy, *, now=None):
    """Call inside the final lifecycle/scheduler transaction; never an authority token.

    Current source changes, missing coverage, expired observations and stop epochs
    all refuse admission. No public API accepts the supplied internal policy dict.
    """
    if not ops.db.in_transaction:
        raise _bad('PM_WORKLOAD_TRANSACTION_REQUIRED', 'Evaluate workload in the final admission transaction', 409)
    at = time.time() if now is None else now
    if type(at) not in (int, float) or not math.isfinite(at):
        raise _bad('INVALID_PARAMS', 'Use a finite observation time')
    current = _policy().active_record(ops, principal, workspace_id)
    lifecycle = pm_lifecycle_gates.head(ops.db, workspace_id)
    row = _record(ops.db, workspace_id, snapshot_id)
    _access(ops, principal, workspace_id, row)
    document, intent = row['document'], row['source_binding']['intent']
    reasons = []
    if current != policy or _intent(current, lifecycle) != intent:
        reasons.append('context_changed')
    if lifecycle['stage'] in {'paused', 'closed'}:
        reasons.append('lifecycle_stopped')
    if (current['policy']['enabled'] is not True
            or current['policy']['after_release_policy'] != 'maintain_when_idle'):
        reasons.append('automatic_maintenance_policy_not_adopted')
    if not document['as_of'] <= at < document['expires_at']:
        reasons.append('observation_expired_or_clock_changed')
    facts = sources.Collector(ops, principal, workspace_id, _intent(current, lifecycle), at).collect()
    if facts['source_digest'] != document['source_digest']:
        reasons.append('source_changed')
    if facts['classification'] != 'idle_candidate' or document['classification'] != 'idle_candidate':
        reasons.append('workload_not_idle')
    if not document['stability']['satisfied']:
        reasons.append('stability_not_proven')
    return {'eligible': not reasons, 'reasons': reasons, 'snapshot_id': snapshot_id,
        'source_digest': document['source_digest'], 'automatic_maintenance_authorized': False}
