"""Immutable, policy-bound read-only GitHub observations with original-key recovery."""
from __future__ import annotations

import json
import re
import time

from . import pm_github_sources as provider
from . import pm_release_baselines as baselines
from . import pm_workload as workload
from . import pm_workload_sources as sources
from . import pm_workspace
from .operations import ActionDef, OperationError, OperationService

ACTION = 'pm.github.observe'
OBSERVATION = re.compile(r'pmgh_[0-9a-f]{32}')
MAX_READ_BYTES = 2_000_000


def _bad(code, message, status=409):
    return OperationError(code, message, status)


def schema(journal):
    baselines.schema(journal)
    with journal.tx():
        journal.db.execute('''CREATE TABLE IF NOT EXISTS pm_github_observations (
            sequence INTEGER PRIMARY KEY AUTOINCREMENT, observation_id TEXT NOT NULL UNIQUE,
            workspace_id TEXT NOT NULL, policy_id TEXT NOT NULL, baseline_id TEXT NOT NULL,
            document TEXT NOT NULL, document_digest TEXT NOT NULL, source_binding TEXT NOT NULL,
            actor TEXT NOT NULL, created_at REAL NOT NULL, operation_id TEXT NOT NULL UNIQUE)''')
        journal.db.execute('''CREATE TABLE IF NOT EXISTS pm_github_observation_pages (
            operation_id TEXT NOT NULL, step TEXT NOT NULL, receipt TEXT NOT NULL,
            receipt_digest TEXT NOT NULL, PRIMARY KEY(operation_id,step))''')
        for table in ('pm_github_observations', 'pm_github_observation_pages'):
            for verb in ('UPDATE', 'DELETE'):
                journal.db.execute(f'CREATE TRIGGER IF NOT EXISTS {table}_{verb.lower()}_immutable '
                    f'BEFORE {verb} ON {table} '
                    "BEGIN SELECT RAISE(ABORT, 'GitHub observations are immutable'); END")


def install(ops):
    schema(ops.journal)
    if ACTION not in ops.actions:
        ops.register(ActionDef(ACTION, 'manage', 'Observe the explicitly adopted read-only GitHub workload profile',
            _run, _admit, ('workspace_id',), authorize_existing=_authorize_existing))


def capabilities():
    return {'version': 1, 'profiles': [provider.PROFILE], 'read_only': True, 'traversals': 2,
        'automatic_maintenance': False, 'full_github_repository_coverage': False,
        'outside_scope': provider.OUTSIDE_SCOPE,
        'limits': {'pages': provider.MAX_PAGES, 'rows': provider.MAX_ROWS,
            'bytes': provider.MAX_BYTES, 'seconds': provider.MAX_SECONDS}}


def _check(ops, principal, target, params, pre):
    intent = workload._check(ops, principal, target, params, pre)
    if intent['scope']['github_scope'] != provider.PROFILE:
        raise _bad('PM_GITHUB_PROFILE_NOT_ADOPTED', 'Adopt the explicit GitHub observation profile first')
    if not callable(getattr(ops.context.get('github'), 'bounded_get', None)):
        raise _bad('PM_GITHUB_PROVIDER_UNAVAILABLE', 'A bounded read provider is unavailable')
    return intent


def _admit(ops, principal, target, params, pre):
    with ops.journal.tx():
        intent = _check(ops, principal, target, params, pre)
    return {'principal': pm_workspace.principal_binding(principal), 'github_observation_intent': intent,
        'github_observation_intent_digest': sources.digest(intent)}


def _record(db, wid, oid):
    if not isinstance(oid, str) or not OBSERVATION.fullmatch(oid):
        raise _bad('NOT_FOUND', 'GitHub observation unavailable', 404)
    row = db.execute('SELECT * FROM pm_github_observations WHERE workspace_id=? AND observation_id=?', (wid, oid)).fetchone()
    if not row:
        raise _bad('NOT_FOUND', 'GitHub observation unavailable', 404)
    record = dict(row)
    try:
        record['document'] = json.loads(row['document'])
        record['source_binding'] = json.loads(row['source_binding'])
        doc = record['document']
        if (sources.digest(doc) != row['document_digest'] or doc['observation_id'] != row['observation_id']
                or doc['workspace_id'] != wid or doc['source_binding_digest'] != sources.digest(record['source_binding'])):
            raise ValueError
    except (ValueError, TypeError, KeyError):
        raise _bad('PM_GITHUB_OBSERVATION_CORRUPT', 'Original GitHub evidence cannot be verified') from None
    return record


def _access(ops, principal, wid, record):
    workload._intent_access(ops, principal, wid, record['source_binding'])


def operation_visible(db, principal, operation):
    with baselines._snapshot(db):
        row = db.execute('SELECT * FROM operations WHERE operation_id=?', (operation.get('operation_id'),)).fetchone()
        if not row or row['action'] != ACTION:
            return False
        op = OperationService._decode(row)
        binding = (op.get('external_refs') or {}).get('admission_binding') or {}
        intent = binding.get('github_observation_intent')
        try:
            if not isinstance(intent, dict) or sources.digest(intent) != binding.get('github_observation_intent_digest'):
                return False
            wid, reader = op['target']['workspace_id'], workload._Reader(db)
            workload._intent_access(reader, principal, wid, intent)
            saved = db.execute('SELECT observation_id FROM pm_github_observations WHERE operation_id=?', (op['operation_id'],)).fetchone()
            if saved:
                _access(reader, principal, wid, _record(db, wid, saved[0]))
            return True
        except (OperationError, KeyError, TypeError, ValueError):
            return False


def _authorize_existing(ops, principal, operation, verb):
    if not principal.allows('manage') or not operation_visible(ops.db, principal, operation):
        raise _bad('NOT_FOUND', 'Original GitHub observation operation unavailable', 404)
    if verb != 'replay':
        workload._member(ops, principal, operation['target']['workspace_id'], write=True)


def _size(value):
    return len(json.dumps(value, ensure_ascii=False, allow_nan=False).encode())


def _bounded(value):
    if _size(value) > MAX_READ_BYTES:
        raise _bad('PM_GITHUB_READ_TOO_LARGE', 'A whole GitHub observation exceeds the read byte limit', 413)
    return value


async def _run(ctx):
    name = 'pm.github.record'
    saved = ctx.service.db.execute('SELECT status FROM operation_steps WHERE operation_id=? AND name=?',
        (ctx.operation_id, name)).fetchone()
    if saved and saved[0] == 'succeeded':
        return ctx.effect(name, lambda: {})
    principal = pm_workspace.admitted_principal(ctx)
    binding = ctx.admission_binding or {}
    intent = binding.get('github_observation_intent')

    def check():
        ctx.check_cancel()
        current = _check(ctx.service, principal, ctx.target, ctx.params, ctx.preconditions)
        if current != intent or sources.digest(current) != binding.get('github_observation_intent_digest'):
            raise _bad('PM_GITHUB_CONTEXT_CHANGED', 'Original GitHub observation scope changed')

    check()
    start = ctx.effect('pm.github.started', lambda: {'started_at': time.time()})
    observer = provider.Traversal(ctx, intent, start['started_at'], check)
    evidence = await observer.collect()

    def record():
        check()
        now, oid = time.time(), 'pmgh_' + ctx.operation_id[3:]
        document = {'schema_version': 1, 'observation_id': oid, 'workspace_id': ctx.target['workspace_id'],
            'profile': provider.PROFILE, 'scope': intent['scope'], 'as_of': start['started_at'], 'finished_at': now,
            'expires_at': start['started_at'] + intent['observation']['max_age_seconds'],
            'source_binding_digest': sources.digest(intent), 'evidence': evidence,
            'network_checked': evidence['pages'] > 0, 'automatic_maintenance_authorized': False}
        if now < start['started_at'] or now >= document['expires_at']:
            evidence.update(status='incomplete', reason='observation_expired_or_clock_changed')
        doc_digest = sources.digest(document)
        row = {'sequence': 2**63 - 1, 'observation_id': oid, 'workspace_id': ctx.target['workspace_id'],
            'policy_id': intent['policy_id'], 'baseline_id': intent['baseline_id'], 'document': document,
            'document_digest': doc_digest, 'actor': ctx.actor, 'created_at': now, 'operation_id': ctx.operation_id}
        _bounded({'workspace_id': row['workspace_id'], 'observation': row, 'visibility_filtered': False})
        ctx.service.db.execute('''INSERT INTO pm_github_observations(observation_id,workspace_id,policy_id,baseline_id,
            document,document_digest,source_binding,actor,created_at,operation_id) VALUES(?,?,?,?,?,?,?,?,?,?)''',
            (oid, row['workspace_id'], intent['policy_id'], intent['baseline_id'], baselines._json(document),
             doc_digest, baselines._json(intent), ctx.actor, now, ctx.operation_id))
        result = {'workspace_id': row['workspace_id'], 'observation_id': oid, 'document_digest': doc_digest,
            'coverage_status': evidence['status'], 'automatic_maintenance_authorized': False}
        ctx.service.journal.api_event('pm_github_observation', oid, 'pm.github.observed',
            {**result, 'operation_id': ctx.operation_id}, actor=ctx.actor)
        return result
    return ctx.effect(name, record)


def _view(record):
    return {key: value for key, value in record.items() if key != 'source_binding'}


@baselines._snapshot_read
def get_observation(ops, principal, workspace_id, observation_id):
    workload._member(ops, principal, workspace_id)
    row = _record(ops.db, workspace_id, observation_id)
    _access(ops, principal, workspace_id, row)
    return _bounded({'workspace_id': workspace_id, 'observation': _view(row)})


@baselines._snapshot_read
def current_observation(ops, principal, workspace_id):
    workload._member(ops, principal, workspace_id)
    row = ops.db.execute('SELECT observation_id FROM pm_github_observations WHERE workspace_id=? ORDER BY sequence DESC LIMIT 1',
        (workspace_id,)).fetchone()
    if not row:
        return {'workspace_id': workspace_id, 'observation': None, 'visibility_filtered': False}
    try:
        return _bounded({**get_observation(ops, principal, workspace_id, row[0]), 'visibility_filtered': False})
    except OperationError as exc:
        if exc.status not in (403, 404):
            raise
        return {'workspace_id': workspace_id, 'observation': None, 'visibility_filtered': True}


@baselines._snapshot_read
def list_observations(ops, principal, workspace_id, *, after=0, limit=20):
    workload._member(ops, principal, workspace_id)
    if type(after) is not int or not 0 <= after < 2**63 or type(limit) is not int or not 1 <= limit <= 100:
        raise _bad('INVALID_PARAMS', 'Use a signed 64-bit cursor and limit 1–100', 422)
    rows = ops.db.execute('''SELECT observation_id,sequence FROM pm_github_observations
        WHERE workspace_id=? AND sequence>? ORDER BY sequence LIMIT ?''', (workspace_id, after, limit + 1)).fetchall()
    result = {'workspace_id': workspace_id, 'observations': [], 'next_cursor': None, 'visibility_filtered': False}
    cursor = after
    for stub in rows[:limit]:
        row = _record(ops.db, workspace_id, stub['observation_id'])
        try:
            _access(ops, principal, workspace_id, row)
        except OperationError as exc:
            if exc.status not in (403, 404):
                raise
            result['visibility_filtered'], cursor = True, stub['sequence']
            continue
        result['observations'].append(_view(row))
        result['next_cursor'] = stub['sequence']
        # The terminal null cursor can be wider than a small sequence ID.
        # Reserve its width before accepting a whole record into the page.
        if _size(result) + max(0, 4 - len(str(stub['sequence']))) > MAX_READ_BYTES:
            result['observations'].pop()
            if cursor == after:
                raise _bad('PM_GITHUB_READ_TOO_LARGE', 'One whole GitHub observation exceeds the read byte limit', 413)
            result['next_cursor'] = cursor
            return _bounded(result)
        cursor = stub['sequence']
    result['next_cursor'] = cursor if len(rows) > limit else None
    return _bounded(result)


def workload_evidence(collector):
    """Use the newest original receipt only for its explicitly adopted profile."""
    db, wid = collector.db, collector.wid
    coverage = {'kind': 'github', 'repository': collector.scope['repository'], 'requested_scope': provider.PROFILE,
        'basis': 'original_provider_page_receipts', 'status': 'incomplete', 'network_checked': False,
        'outside_scope': provider.OUTSIDE_SCOPE}
    collector.coverage.append(coverage)
    row = db.execute('''SELECT observation_id FROM pm_github_observations
        WHERE workspace_id=? ORDER BY sequence DESC LIMIT 1''', (wid,)).fetchone() if sources.has_table(db, 'pm_github_observations') else None
    if not row:
        collector.reason('github_observation_missing')
        return
    record = _record(db, wid, row[0])
    if not collector.ref('github_observation', row[0]):
        return
    doc = record['document']
    collector.record('github_observation', row[0], record['document_digest'])
    if record['source_binding'] != collector.policy:
        collector.reason('github_observation_context_changed')
        return
    if not collector.fresh(doc['as_of'], {'kind': 'github_observation', 'id': row[0]}):
        return
    coverage.update(observation_id=row[0], network_checked=doc['network_checked'])
    if doc['evidence']['status'] != 'complete':
        collector.reason('github_observation_incomplete')
    else:
        coverage['status'] = 'complete'
    if doc['evidence']['observed_open_pulls']:
        collector.reason('github_open_pull_requests', 'development')
    if doc['evidence']['observed_nonterminal_actions']:
        collector.reason('github_nonterminal_actions', 'development')
