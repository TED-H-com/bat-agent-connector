"""Bounded maintenance checks composed from the existing operation worker.

Scheduling reserves a policy/window in the journal. A check reserves capacity
before it admits any child; it never owns a timer, runtime, repair or merge loop.
"""
from __future__ import annotations

import json
import math
import re
import time
import uuid

from . import api_auth, controller_authority, pm_lifecycle_gates, pm_workspace
from . import pm_github_observation as github
from . import pm_maintenance_policy as policies
from . import pm_release_baselines as baselines
from . import pm_workload as workload
from .operations import ActionDef, NeedsAttention, OpContext, OperationError, OperationService, Wait

ACTION = 'pm.maintenance.check'
RUN = re.compile(r'pmck_[0-9a-f]{32}')
REQUIRED = frozenset({'observe', 'manage'})
CHILDREN = (('github', github.ACTION), ('workload', workload.ACTION))
MAX_TICK = 32
MAX_READ_BYTES = 2_000_000
TERMINAL = frozenset({'succeeded', 'failed', 'cancelled'})


def _bad(code, message, status=409):
    return OperationError(code, message, status)


def _json(value):
    return baselines._json(value)


def _digest(value):
    return baselines.sources.digest(value)


def schema(journal):
    policies.schema(journal)
    with journal.tx():
        journal.db.execute('''CREATE TABLE IF NOT EXISTS pm_maintenance_check_runs (
            sequence INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL UNIQUE,
            operation_id TEXT NOT NULL UNIQUE, workspace_id TEXT NOT NULL, policy_id TEXT NOT NULL,
            policy_version INTEGER NOT NULL, actor TEXT NOT NULL, context TEXT NOT NULL,
            context_digest TEXT NOT NULL, created_at REAL NOT NULL, deadline_at REAL NOT NULL,
            budget_period INTEGER NOT NULL, finished_at REAL, document TEXT, document_digest TEXT)''')
        journal.db.execute('CREATE INDEX IF NOT EXISTS pm_maintenance_check_active ON pm_maintenance_check_runs(workspace_id,finished_at)')
        journal.db.execute('CREATE INDEX IF NOT EXISTS pm_maintenance_check_budget ON pm_maintenance_check_runs(policy_id,budget_period)')
        journal.db.execute('''CREATE TABLE IF NOT EXISTS pm_maintenance_check_children (
            parent_id TEXT NOT NULL, slot TEXT NOT NULL, operation_id TEXT NOT NULL UNIQUE,
            request TEXT NOT NULL, request_digest TEXT NOT NULL, PRIMARY KEY(parent_id,slot))''')
        journal.db.execute('''CREATE TABLE IF NOT EXISTS pm_maintenance_check_windows (
            sequence INTEGER PRIMARY KEY AUTOINCREMENT, schedule_id TEXT NOT NULL,
            policy_id TEXT NOT NULL, policy_version INTEGER NOT NULL, workspace_id TEXT NOT NULL,
            window INTEGER NOT NULL, scheduled_at REAL NOT NULL, admitted_at REAL NOT NULL,
            coalesced_windows INTEGER NOT NULL, operation_id TEXT NOT NULL UNIQUE,
            UNIQUE(schedule_id,policy_version,window))''')
        journal.db.execute('''CREATE TABLE IF NOT EXISTS pm_maintenance_check_cursor (
            singleton INTEGER PRIMARY KEY CHECK(singleton=1), policy_sequence INTEGER NOT NULL,
            run_sequence INTEGER NOT NULL)''')
        journal.db.execute('INSERT OR IGNORE INTO pm_maintenance_check_cursor VALUES(1,0,0)')
        for table in ('pm_maintenance_check_children', 'pm_maintenance_check_windows'):
            for verb in ('UPDATE', 'DELETE'):
                journal.db.execute(f'CREATE TRIGGER IF NOT EXISTS {table}_{verb.lower()}_immutable '
                    f'BEFORE {verb} ON {table} '
                    "BEGIN SELECT RAISE(ABORT, 'Maintenance check identity is immutable'); END")
        journal.db.execute('''CREATE TRIGGER IF NOT EXISTS pm_maintenance_check_final_immutable
            BEFORE UPDATE ON pm_maintenance_check_runs WHEN OLD.finished_at IS NOT NULL
            BEGIN SELECT RAISE(ABORT, 'Completed maintenance checks are immutable'); END''')
        journal.db.execute('''CREATE TRIGGER IF NOT EXISTS pm_maintenance_check_identity_immutable
            BEFORE UPDATE OF run_id,operation_id,workspace_id,policy_id,policy_version,actor,context,
            context_digest,created_at,deadline_at,budget_period ON pm_maintenance_check_runs
            BEGIN SELECT RAISE(ABORT, 'Maintenance check identity is immutable'); END''')
        journal.db.execute('''CREATE TRIGGER IF NOT EXISTS pm_maintenance_check_delete_immutable
            BEFORE DELETE ON pm_maintenance_check_runs
            BEGIN SELECT RAISE(ABORT, 'Maintenance check history is immutable'); END''')


def install(ops):
    schema(ops.journal)
    if ACTION not in ops.actions:
        ops.register(ActionDef(ACTION, 'manage', 'Run one bounded policy-authorized maintenance observation',
            _run, _admit, ('workspace_id',), authorize_existing=_authorize_existing))


def capabilities(ops=None):
    from . import pm_maintenance_review_schedule as weekly
    return {'version': 1, 'action': ACTION, 'required_scopes': sorted(REQUIRED),
        'server_schedule': True, 'missed_windows': 'coalesce_latest', 'automatic_repairs': False,
        'automatic_lifecycle_transition': True, 'weekly_review_execution': bool(ops and weekly.available(ops)),
        'cost_coverage': {'model_requests': 'none', 'provider_billing': 'unmeasured'},
        'retry_behavior': 'original_child_receipts_only'}


def _owner(ops, principal, wid):
    if not all(principal.allows(scope) for scope in REQUIRED):
        raise _bad('FORBIDDEN', 'Maintenance checks require observe and manage', 403)
    member = pm_workspace.require_member(ops.db, principal, wid, decide=True)
    if member['membership']['role'] != 'owner':
        raise _bad('FORBIDDEN', 'Maintenance checks require an actual workspace owner', 403)


def _current_principal(ops, record, now, *, required=REQUIRED):
    """A policy delegates to a controller, never to an actor name with old scopes.

    At least one CURRENT credential must individually carry the required scopes.
    Rotation for the same controller is permitted; combining partial grants is not.
    The current daemon admin credential is authenticated, never inferred by name.
    """
    row = ops.db.execute('SELECT * FROM operations WHERE operation_id=?', (record['operation_id'],)).fetchone()
    op = OperationService._decode(row) if row else {}
    original = ((op.get('external_refs') or {}).get('admission_binding') or {}).get('principal') or {}
    controller = record['document']['controller']
    actor = controller['actor']
    if (op.get('actor') != actor or original.get('actor') != actor
            or original.get('admin') is not True and not required.issubset(original.get('scopes', []))):
        raise _bad('PM_MAINTENANCE_AUTHORITY_UNAVAILABLE', 'Original policy did not delegate the required authority', 403)
    rows = ops.db.execute('''SELECT token_hash,scopes FROM api_principals WHERE actor=?
        AND revoked_at IS NULL AND created_at<=? AND (expires_at IS NULL OR expires_at>?)
        ORDER BY created_at DESC LIMIT 64''', (actor, now, now)).fetchall()
    live = None
    for candidate in rows:
        try:
            scopes = json.loads(candidate['scopes'])
            if isinstance(scopes, list) and required.issubset(scopes):
                live = api_auth.Principal(actor, required, credential_id=candidate['token_hash'])
                break
        except (ValueError, TypeError):
            continue
    if live is None and original.get('admin') is True:
        daemon = ops.context.get('daemon')
        token = vars(daemon).get('_admin_token') if daemon is not None else None
        if isinstance(token, str) and token:
            authenticated = api_auth.authenticate(ops.db, token, token)
            if authenticated and authenticated.actor == actor:
                live = authenticated
    if live is None:
        raise _bad('PM_MAINTENANCE_AUTHORITY_UNAVAILABLE', 'No current credential permits this policy check', 403)
    _owner(ops, live, record['workspace_id'])
    authority = controller_authority.binding(ops.db, live, pm_workspace_id=record['workspace_id'])
    if any(authority[key] != controller[key] for key in ('server_id', 'controller_id', 'actor')):
        raise _bad('PM_MAINTENANCE_CONTROLLER_CHANGED', 'Original policy controller is unavailable', 403)
    return live


def _check(ops, principal, target, params, pre):
    original = workload._check(ops, principal, target, params, pre)
    wid = target['workspace_id']
    _owner(ops, principal, wid)
    record = policies._record(ops.db, wid, original['policy_id'])
    policy = record['document']['policy']
    live = _current_principal(ops, record, time.time())
    if principal.actor != live.actor:
        raise _bad('PM_MAINTENANCE_CONTROLLER_CHANGED', 'Only the original policy owner can start this check', 403)
    stage = pm_lifecycle_gates.head(ops.db, wid)['stage']
    if stage not in {'development', 'maintain'}:
        raise _bad('PM_MAINTENANCE_STAGE_STOPPED', 'Checks require current Development or Maintain intent')
    if policy['enabled'] is not True:
        raise _bad('PM_MAINTENANCE_DISABLED', 'The current maintenance policy is disabled')
    if original['scope']['github_scope'] != github.provider.PROFILE or any(action not in ops.actions for _, action in CHILDREN):
        raise _bad('PM_MAINTENANCE_SOURCES_UNAVAILABLE', 'Adopt the supported observation profile and available child actions')
    # Verify both original sources under CURRENT credential authority, not merely
    # the immutable principal that admitted the old policy.
    workload._intent_access(ops, live, wid, original)
    return {'workload': original, 'limits': policy['limits'], 'schedule': policy['schedule'],
        'controller': record['document']['controller'], 'policy_created_at': record['created_at']}


def _admit(ops, principal, target, params, pre):
    with ops.journal.tx():
        intent = _check(ops, principal, target, params, pre)
    return {'principal': pm_workspace.principal_binding(principal), 'maintenance_check_intent': intent,
        'maintenance_check_intent_digest': _digest(intent)}


def _intent(op):
    binding = (op.get('external_refs') or {}).get('admission_binding') or {}
    intent = binding.get('maintenance_check_intent')
    if not isinstance(intent, dict) or _digest(intent) != binding.get('maintenance_check_intent_digest'):
        raise _bad('PM_MAINTENANCE_CONTEXT_INVALID', 'Original maintenance check context is unavailable')
    return intent


def _row(ops, operation_id):
    row = ops.db.execute('SELECT * FROM pm_maintenance_check_runs WHERE operation_id=?', (operation_id,)).fetchone()
    if row is None:
        return None
    value = dict(row)
    try:
        value['context'] = json.loads(row['context'])
        value['document'] = json.loads(row['document']) if row['document'] else None
        if (_digest(value['context']) != row['context_digest'] or row['run_id'] != 'pmck_' + operation_id[3:]
                or value['document'] is not None and _digest(value['document']) != row['document_digest']):
            raise ValueError
    except (ValueError, KeyError, TypeError):
        raise _bad('PM_MAINTENANCE_RECORD_CORRUPT', 'Original maintenance check record cannot be verified') from None
    return value


def _guard(ops, op):
    if op['action'] != ACTION or op['cancel_requested'] or op['status'] in TERMINAL | {'needs_attention'}:
        raise _bad('PM_MAINTENANCE_PARENT_STOPPED', 'The original maintenance check stopped new steps')
    intent = _intent(op)
    record = policies._record(ops.db, op['target']['workspace_id'], intent['workload']['policy_id'])
    principal = _current_principal(ops, record, time.time())
    current = _check(ops, principal, op['target'], op['params'], op['preconditions'])
    if current != intent:
        raise _bad('PM_MAINTENANCE_CONTEXT_CHANGED', 'Policy, baseline or lifecycle changed before the next check step')
    row = _row(ops, op['operation_id'])
    if row and (row['finished_at'] is not None or time.time() < row['created_at'] or time.time() >= row['deadline_at']):
        raise _bad('PM_MAINTENANCE_DEADLINE', 'This check cannot admit another step after its bounded deadline')
    return principal, intent


def _request(parent, slot):
    return {'action': dict(CHILDREN)[slot], 'target': parent['target'], 'params': parent['params'],
        'preconditions': parent['preconditions'], 'idempotency_key': 'maintenance:' + parent['operation_id'] + ':' + slot}


def _child(ops, parent_id, slot):
    link = ops.db.execute('SELECT * FROM pm_maintenance_check_children WHERE parent_id=? AND slot=?', (parent_id, slot)).fetchone()
    if link is None:
        return None
    try:
        parent = ops.get(parent_id, steps=False)
        child = ops.get(link['operation_id'])
        request = json.loads(link['request'])
        if (_digest(request) != link['request_digest'] or request != _request(parent, slot)
                or any(child[key] != request[key] for key in request)
                or child['actor'] != _intent(parent)['controller']['actor']
                or (child.get('external_refs') or {}).get('pm_maintenance_parent') != {'operation_id': parent_id, 'slot': slot}):
            raise ValueError
        return child
    except (ValueError, KeyError, TypeError):
        raise _bad('PM_MAINTENANCE_CHILD_CHANGED', 'The child does not match its original maintenance receipt') from None


def require_child(ops, op):
    """Shared child handlers call before every UNSENT effect, never after a receipt replay.

    A missing link means an ordinary manual child action. A supplied link must
    correspond to the private immutable relation and exact original envelope.
    """
    link = (op.get('external_refs') or {}).get('pm_maintenance_parent')
    installed = ops.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='pm_maintenance_check_children'").fetchone()
    if installed is None:
        if link is None:
            return
        raise _bad('PM_MAINTENANCE_CHILD_CHANGED', 'Unproven maintenance parent link')
    persisted = ops.db.execute('SELECT parent_id,slot FROM pm_maintenance_check_children WHERE operation_id=?',
        (op.get('operation_id'),)).fetchone()
    if link is None and persisted is None:
        return
    if (not isinstance(link, dict) or set(link) != {'operation_id', 'slot'} or not persisted
            or (persisted['parent_id'], persisted['slot']) != (link['operation_id'], link['slot'])):
        raise _bad('PM_MAINTENANCE_CHILD_CHANGED', 'Unproven maintenance parent link')
    child = _child(ops, persisted['parent_id'], persisted['slot'])
    if any(child.get(key) != op.get(key) for key in ('operation_id', 'actor', 'action', 'target', 'params', 'preconditions', 'idempotency_key')):
        raise _bad('PM_MAINTENANCE_CHILD_CHANGED', 'Maintenance child identity changed')
    _guard(ops, ops.get(persisted['parent_id'], steps=False))
    row = _row(ops, persisted['parent_id'])
    if row is None:
        raise _bad('PM_MAINTENANCE_CHILD_CHANGED', 'Child lacks its original capacity reservation')
    return row['deadline_at']


def child_timeout(ops, op, timeout_s):
    """Clamp each UNSENT provider call to the verified original parent budget."""
    deadline = require_child(ops, op)
    if deadline is None:
        return timeout_s
    remaining = deadline - time.time()
    if remaining <= 0:
        raise _bad('PM_MAINTENANCE_DEADLINE', 'The original check deadline prevents another provider call')
    return min(timeout_s, remaining)


def _capacity(ops, wid, intent, now):
    period = math.floor((now - intent['policy_created_at']) / intent['limits']['period_seconds'])
    if period < 0:
        raise _bad('PM_MAINTENANCE_CLOCK_CHANGED', 'Policy time is later than the current clock')
    concurrent = ops.db.execute('SELECT COUNT(*) FROM pm_maintenance_check_runs WHERE workspace_id=? AND finished_at IS NULL', (wid,)).fetchone()[0]
    count = ops.db.execute('SELECT COUNT(*) FROM pm_maintenance_check_runs WHERE policy_id=? AND budget_period=?',
        (intent['workload']['policy_id'], period)).fetchone()[0]
    from . import pm_maintenance_review_schedule as weekly
    if weekly.installed(ops.db):
        concurrent += ops.db.execute('SELECT COUNT(*) FROM pm_maintenance_review_windows WHERE workspace_id=? AND finished_at IS NULL',
            (wid,)).fetchone()[0]
        count += ops.db.execute('SELECT COUNT(*) FROM pm_maintenance_review_windows WHERE policy_id=? AND budget_period=?',
            (intent['workload']['policy_id'], period)).fetchone()[0]
    from . import pm_maintenance_repairs as repairs
    repair_concurrent, repair_count = repairs.capacity_counts(ops, wid, intent['workload']['policy_id'], period)
    concurrent += repair_concurrent
    count += repair_count
    if concurrent >= intent['limits']['max_parallel'] or count >= intent['limits']['period_runs']:
        raise _bad('PM_MAINTENANCE_BUDGET_EXHAUSTED', 'Current concurrent or period run capacity is exhausted')
    return period


def _reserve(ctx):
    def reserve():
        principal, intent = _guard(ctx.service, ctx.service.get(ctx.operation_id, steps=False))
        now, wid = time.time(), ctx.target['workspace_id']
        period = _capacity(ctx.service, wid, intent, now)
        run_id = 'pmck_' + ctx.operation_id[3:]
        ctx.service.db.execute('''INSERT INTO pm_maintenance_check_runs(run_id,operation_id,workspace_id,policy_id,
            policy_version,actor,context,context_digest,created_at,deadline_at,budget_period)
            VALUES(?,?,?,?,?,?,?,?,?,?,?)''', (run_id, ctx.operation_id, wid, intent['workload']['policy_id'],
            intent['workload']['version'], principal.actor, _json(intent), _digest(intent), now,
            now + intent['limits']['run_seconds'], period))
        ctx.service.journal.api_event('pm_maintenance_check', run_id, 'pm.maintenance.check.reserved',
            {'workspace_id': wid, 'run_id': run_id, 'operation_id': ctx.operation_id}, actor=ctx.actor)
        return {'run_id': run_id}
    return ctx.effect('pm.maintenance.reserve', reserve)


def _admit_child(ctx, slot):
    def create():
        principal, _ = _guard(ctx.service, ctx.service.get(ctx.operation_id, steps=False))
        request = _request(ctx.op, slot)
        child, fresh = ctx.service.create(principal, **request, entry='maintenance')
        if not fresh:
            raise _bad('PM_MAINTENANCE_CHILD_CHANGED', 'A child key exists without its original local receipt')
        ctx.service.db.execute('INSERT INTO pm_maintenance_check_children VALUES(?,?,?,?,?)',
            (ctx.operation_id, slot, child['operation_id'], _json(request), _digest(request)))
        ctx.service._merge_refs(child['operation_id'], {'pm_maintenance_parent': {'operation_id': ctx.operation_id, 'slot': slot}})
        return {'operation_id': child['operation_id']}
    return ctx.effect('pm.maintenance.child.' + slot, create)


def _unsettled(ops, child):
    if child['status'] not in TERMINAL:
        return True
    # Match lifecycle reconciliation: a rolled-back local transaction did not
    # send an external effect. Unclassified legacy effects remain unresolved.
    return ops.db.execute('''SELECT 1 FROM operation_steps s
        LEFT JOIN pm_lifecycle_steps l ON l.operation_id=s.operation_id AND l.name=s.name
        WHERE s.operation_id=? AND s.status IN ('started','uncertain')
        AND (l.kind IS NULL OR l.kind='external') LIMIT 1''', (child['operation_id'],)).fetchone() is not None


def _finish(ctx, outcome, reason=None):
    children = [child for slot, _ in CHILDREN if (child := _child(ctx.service, ctx.operation_id, slot))]
    if any(_unsettled(ctx.service, child) for child in children):
        raise NeedsAttention('PM_MAINTENANCE_CHILD_UNSETTLED', 'The original child still needs reconciliation; capacity remains reserved')
    def record():
        row = _row(ctx.service, ctx.operation_id)
        document = {'schema_version': 1, 'workspace_id': row['workspace_id'], 'run_id': row['run_id'],
            'policy_id': row['policy_id'], 'policy_version': row['policy_version'],
            'baseline_id': row['context']['workload']['baseline_id'],
            'lifecycle_revision': row['context']['workload']['lifecycle_revision'],
            'execution_epoch': row['context']['workload']['execution_epoch'], 'outcome': outcome, 'reason': reason,
            'children': [{'operation_id': child['operation_id'], 'action': child['action'], 'status': child['status'],
                'result': child['result'], 'error_code': child['error_code']} for child in children],
            'started_at': row['created_at'], 'finished_at': time.time(), 'deadline_at': row['deadline_at'],
            'retries_used': 0, 'retry_limit': row['context']['limits']['retries'],
            'cost_coverage': {'model_requests': 'none', 'provider_billing': 'unmeasured',
                'cost_budget_microusd': row['context']['limits']['cost_microusd'], 'charged_microusd': None},
            'automatic_maintenance_authorized': False, 'repair_started': False}
        _bounded({'workspace_id': row['workspace_id'], 'run': {'document': document}})
        ctx.service.db.execute('UPDATE pm_maintenance_check_runs SET finished_at=?,document=?,document_digest=? WHERE operation_id=?',
            (document['finished_at'], _json(document), _digest(document), ctx.operation_id))
        result = {'workspace_id': row['workspace_id'], 'run_id': row['run_id'], 'outcome': outcome,
            'document_digest': _digest(document), 'automatic_maintenance_authorized': False}
        ctx.service.journal.api_event('pm_maintenance_check', row['run_id'], 'pm.maintenance.check.finished',
            {**result, 'operation_id': ctx.operation_id}, actor=ctx.actor)
        return result
    # Every child is proven terminal. This records receipts after a pause or
    # credential loss, but cannot admit another child or perform network IO.
    return ctx.effect('pm.maintenance.finish', record, receipt_only=True)


async def _run(ctx):
    row = _row(ctx.service, ctx.operation_id)
    if row and row['finished_at'] is not None:
        return ctx.effect('pm.maintenance.finish', lambda: {}, receipt_only=True)
    if row is None:
        _reserve(ctx)
    for slot, _ in CHILDREN:
        child = _child(ctx.service, ctx.operation_id, slot)
        if child is None:
            try:
                _admit_child(ctx, slot)
            except OperationError as exc:
                return _finish(ctx, 'stopped', exc.code)
            child = _child(ctx.service, ctx.operation_id, slot)
        if _unsettled(ctx.service, child):
            if child['status'] in TERMINAL | {'needs_attention'}:
                raise NeedsAttention('PM_MAINTENANCE_CHILD_UNSETTLED', 'Original child receipts are not settled; capacity is retained')
            raise Wait('waiting_external', 'Reading the original maintenance child', delay_s=1)
        if child['status'] != 'succeeded':
            return _finish(ctx, 'incomplete', child['error_code'] or 'CHILD_DID_NOT_COMPLETE')
    children = {slot: _child(ctx.service, ctx.operation_id, slot) for slot, _ in CHILDREN}
    snapshot = workload._record(ctx.service.db, ctx.target['workspace_id'], children['workload']['result']['snapshot_id'])
    complete = (children['github']['result'].get('coverage_status') == 'complete'
        and all(source['status'] == 'complete' for source in snapshot['document']['source_coverage']))
    if complete:
        from . import pm_maintenance_lifecycle
        try:
            pm_maintenance_lifecycle.admit_from_check(ctx, snapshot['snapshot_id'])
        except OperationError as exc:
            return _finish(ctx, 'stopped', exc.code)
    return _finish(ctx, 'complete' if complete else 'incomplete', None if complete else 'SOURCE_COVERAGE_INCOMPLETE')


def _access(ops, principal, op):
    workload._intent_access(ops, principal, op['target']['workspace_id'], _intent(op)['workload'])
    for slot, _ in CHILDREN:
        child = _child(ops, op['operation_id'], slot)
        if child and not (github if slot == 'github' else workload).operation_visible(ops.db, principal, child):
            raise _bad('NOT_FOUND', 'An original maintenance check source is unavailable', 404)


def operation_visible(db, principal, operation):
    with baselines._snapshot(db):
        row = db.execute('SELECT * FROM operations WHERE operation_id=?', (operation.get('operation_id'),)).fetchone()
        if row is None or row['action'] != ACTION:
            return False
        try:
            _access(workload._Reader(db), principal, OperationService._decode(row))
            return True
        except (OperationError, ValueError, TypeError, KeyError):
            return False


def _authorize_existing(ops, principal, op, verb):
    if not principal.allows('manage') or not operation_visible(ops.db, principal, op):
        raise _bad('NOT_FOUND', 'Original maintenance check unavailable', 404)
    if verb != 'replay':
        _owner(ops, principal, op['target']['workspace_id'])
        if principal.actor != op['actor']:
            raise _bad('FORBIDDEN', 'Original maintenance controller is required', 403)


def _bounded(value):
    if workload._size(value) > MAX_READ_BYTES:
        raise _bad('PM_MAINTENANCE_READ_TOO_LARGE', 'A whole maintenance record exceeds the read byte limit', 413)
    return value


def _view(ops, row):
    op = ops.get(row['operation_id'], steps=False)
    return {key: row[key] for key in ('sequence', 'run_id', 'workspace_id', 'policy_id', 'policy_version',
        'operation_id', 'actor', 'created_at', 'deadline_at', 'finished_at', 'document', 'document_digest')} | {
            'operation_status': op['status'], 'capacity_reserved': row['finished_at'] is None,
            'baseline_id': row['context']['workload']['baseline_id'],
            'lifecycle_revision': row['context']['workload']['lifecycle_revision'],
            'execution_epoch': row['context']['workload']['execution_epoch']}


@baselines._snapshot_read
def get_run(ops, principal, workspace_id, run_id):
    workload._member(ops, principal, workspace_id)
    if not isinstance(run_id, str) or not RUN.fullmatch(run_id):
        raise _bad('NOT_FOUND', 'Maintenance check not found', 404)
    row = _row(ops, 'op_' + run_id[5:])
    if row is None or row['workspace_id'] != workspace_id:
        raise _bad('NOT_FOUND', 'Maintenance check not found', 404)
    _access(ops, principal, ops.get(row['operation_id'], steps=False))
    return _bounded({'workspace_id': workspace_id, 'run': _view(ops, row)})


@baselines._snapshot_read
def current_run(ops, principal, workspace_id):
    workload._member(ops, principal, workspace_id)
    row = ops.db.execute('SELECT run_id FROM pm_maintenance_check_runs WHERE workspace_id=? ORDER BY sequence DESC LIMIT 1', (workspace_id,)).fetchone()
    if row:
        try:
            return _bounded({**get_run(ops, principal, workspace_id, row[0]), 'visibility_filtered': False})
        except OperationError as exc:
            if exc.status not in (403, 404):
                raise
            return {'workspace_id': workspace_id, 'run': None, 'visibility_filtered': True}
    return {'workspace_id': workspace_id, 'run': None, 'visibility_filtered': False}


@baselines._snapshot_read
def list_runs(ops, principal, workspace_id, *, after=0, limit=20):
    workload._member(ops, principal, workspace_id)
    if type(after) is not int or not 0 <= after < 2**63 or type(limit) is not int or not 1 <= limit <= 100:
        raise _bad('INVALID_PARAMS', 'Use a signed 64-bit cursor and limit 1–100', 422)
    rows = ops.db.execute('SELECT run_id,sequence FROM pm_maintenance_check_runs WHERE workspace_id=? AND sequence>? ORDER BY sequence LIMIT ?',
        (workspace_id, after, limit + 1)).fetchall()
    result = {'workspace_id': workspace_id, 'runs': [], 'next_cursor': None, 'visibility_filtered': False}
    consumed = after
    for stub in rows[:limit]:
        try:
            row = get_run(ops, principal, workspace_id, stub['run_id'])['run']
        except OperationError as exc:
            if exc.status not in (403, 404):
                raise
            result['visibility_filtered'], consumed = True, stub['sequence']
            continue
        candidate = {**result, 'runs': [*result['runs'], row], 'next_cursor': stub['sequence']}
        if workload._size(candidate) > MAX_READ_BYTES:
            if consumed == after:
                return _bounded(candidate)
            result['next_cursor'] = consumed
            return _bounded(result)
        result['runs'].append(row)
        consumed = stub['sequence']
    result['next_cursor'] = consumed if len(rows) > limit else None
    return _bounded(result)


def _schedule_id(db, wid, policy_id):
    return 'pmms_' + uuid.uuid5(uuid.UUID(pm_workspace.server_id(db)), wid + ':' + policy_id).hex


def _schedule(ops, principal, wid):
    active = policies.active_record(ops, principal, wid)
    record = policies._record(ops.db, wid, active['policy_id'])
    intent = {'action': ACTION, 'target': {'workspace_id': wid}, 'params': {'policy_id': active['policy_id']},
        'preconditions': {'expected_policy_version': active['version'], 'expected_baseline_id': active['baseline_id'],
            'expected_lifecycle_revision': pm_lifecycle_gates.head(ops.db, wid)['revision']}}
    return record, intent


@baselines._snapshot_read
def schedule_status(ops, principal, workspace_id):
    workload._member(ops, principal, workspace_id)
    current = policies.current_policy(ops, principal, workspace_id)
    if current['policy'] is None:
        return {'workspace_id': workspace_id, 'schedule': None, 'visibility_filtered': current['visibility_filtered']}
    record = current['policy']
    sid = _schedule_id(ops.db, workspace_id, record['policy_id'])
    schedule, now = record['document']['policy']['schedule'], time.time()
    latest = ops.db.execute('SELECT * FROM pm_maintenance_check_windows WHERE schedule_id=? ORDER BY window DESC LIMIT 1', (sid,)).fetchone()
    last = None
    if latest:
        op = ops.get(latest['operation_id'], steps=False)
        try:
            _access(ops, principal, op)
        except OperationError as exc:
            if exc.status not in (403, 404):
                raise
            return {'workspace_id': workspace_id, 'schedule': None, 'visibility_filtered': True}
        last = {key: latest[key] for key in ('operation_id', 'window', 'scheduled_at', 'admitted_at', 'coalesced_windows')}
        last['operation_status'] = op['status']
    blocked = None
    try:
        live = _current_principal(ops, record, now)
        _, request = _schedule(ops, live, workspace_id)
        intent = _check(ops, live, request['target'], request['params'], request['preconditions'])
        _capacity(ops, workspace_id, intent, now)
    except OperationError as exc:
        blocked = exc.code
    success = ops.db.execute("SELECT operation_id FROM pm_maintenance_check_runs WHERE policy_id=? AND json_extract(document,'$.outcome')='complete' ORDER BY sequence DESC LIMIT 1", (record['policy_id'],)).fetchone()
    last_success = None
    if success:
        original = ops.get(success['operation_id'], steps=False)
        try:
            _access(ops, principal, original)
            last_success = _row(ops, success['operation_id'])['finished_at']
        except OperationError as exc:
            if exc.status not in (403, 404):
                raise
            return {'workspace_id': workspace_id, 'schedule': None, 'visibility_filtered': True}
    from . import pm_maintenance_review_schedule as weekly
    try:
        weekly_status = weekly.status(ops, principal, record, now)
    except OperationError as exc:
        if exc.status not in (403, 404):
            raise
        return {'workspace_id': workspace_id, 'schedule': None, 'visibility_filtered': True}
    next_due = record['created_at'] + ((latest['window'] + 1) if latest else 1) * schedule['check_interval_seconds']
    return _bounded({'workspace_id': workspace_id, 'visibility_filtered': False, 'schedule': {
        'schedule_id': sid, 'policy_id': record['policy_id'], 'policy_version': record['revision'],
        'next_due': next_due, 'overdue': now >= next_due, 'blocked_reason': blocked, 'latest_window': last, 'last_success': last_success,
        'weekly_review': weekly_status,
        'missed_windows': 'coalesce_latest', 'automatic_repairs': False}})


def _reconcile(ops, row):
    op = ops.get(row['operation_id'], steps=False)
    if op['status'] not in TERMINAL:
        return False
    children = [_child(ops, op['operation_id'], slot) for slot, _ in CHILDREN]
    if any(child and _unsettled(ops, child) for child in children):
        return False
    _finish(OpContext(ops, op), 'stopped', op['error_code'] or 'PARENT_STOPPED')
    return True


def tick(ops):
    """A bounded synchronous TaskDaemon tick; no network, sleeps or new worker.

    Shared-database transactions atomically reserve the latest actual due window
    and its operation. A rolling cursor prevents a large installation starving.
    """
    if ACTION not in ops.actions:
        return {'scheduled': 0, 'reconciled': 0}
    from . import pm_maintenance_review_schedule as weekly
    scheduled = reconciled = weekly_scheduled = 0
    with ops.journal.tx():
        weekly_reconciled = weekly.reconcile(ops)
        state = ops.db.execute('SELECT * FROM pm_maintenance_check_cursor WHERE singleton=1').fetchone()
        runs = ops.db.execute('SELECT * FROM pm_maintenance_check_runs WHERE finished_at IS NULL AND sequence>? ORDER BY sequence LIMIT ?',
            (state['run_sequence'], MAX_TICK + 1)).fetchall()
        for row in runs[:MAX_TICK]:
            reconciled += _reconcile(ops, row)
        ops.db.execute('UPDATE pm_maintenance_check_cursor SET run_sequence=? WHERE singleton=1',
            (runs[MAX_TICK - 1]['sequence'] if len(runs) > MAX_TICK else 0,))
        rows = ops.db.execute('''SELECT p.* FROM pm_maintenance_policies p WHERE p.sequence>?
            AND p.revision=(SELECT MAX(h.revision) FROM pm_maintenance_policies h WHERE h.workspace_id=p.workspace_id)
            ORDER BY p.sequence LIMIT ?''', (state['policy_sequence'], MAX_TICK + 1)).fetchall()
        for raw in rows[:MAX_TICK]:
            try:
                # A due weekly report takes the shared slot before interval
                # checks, avoiding starvation when max_parallel is one.
                weekly_scheduled += weekly.maybe_schedule(ops, raw)
            except OperationError:
                pass
            try:
                with ops.journal.tx():
                    record = policies._record(ops.db, raw['workspace_id'], raw['policy_id'])
                    now = time.time()
                    principal = _current_principal(ops, record, now)
                    record, request = _schedule(ops, principal, raw['workspace_id'])
                    intent = _check(ops, principal, request['target'], request['params'], request['preconditions'])
                    _capacity(ops, raw['workspace_id'], intent, now)
                    interval = record['document']['policy']['schedule']['check_interval_seconds']
                    window = math.floor((now - record['created_at']) / interval)
                    if not 1 <= window < 2**63:
                        continue
                    sid = _schedule_id(ops.db, raw['workspace_id'], record['policy_id'])
                    previous = ops.db.execute('SELECT MAX(window) FROM pm_maintenance_check_windows WHERE schedule_id=?', (sid,)).fetchone()[0] or 0
                    if window <= previous:
                        continue
                    op, fresh = ops.create(principal, **request, idempotency_key=f'{sid}:{record["revision"]}:{window}', entry='maintenance-schedule')
                    if not fresh:
                        raise _bad('PM_MAINTENANCE_WINDOW_CHANGED', 'Schedule operation exists without its atomic window')
                    _reserve(OpContext(ops, op))
                    ops.db.execute('''INSERT INTO pm_maintenance_check_windows(schedule_id,policy_id,policy_version,workspace_id,
                        window,scheduled_at,admitted_at,coalesced_windows,operation_id) VALUES(?,?,?,?,?,?,?,?,?)''',
                        (sid, record['policy_id'], record['revision'], raw['workspace_id'], window,
                         record['created_at'] + window * interval, now, window - previous - 1, op['operation_id']))
                    scheduled += 1
            except OperationError:
                # State readers expose current blockers. No failed authorization
                # consumes a window or creates a replacement for an unknown child.
                continue
        ops.db.execute('UPDATE pm_maintenance_check_cursor SET policy_sequence=? WHERE singleton=1',
            (rows[MAX_TICK - 1]['sequence'] if len(rows) > MAX_TICK else 0,))
    return {'scheduled': scheduled, 'reconciled': reconciled,
        'weekly_scheduled': weekly_scheduled, 'weekly_reconciled': weekly_reconciled}
