"""Workspace investment lifecycle, independent of threads, Sprints and attempts.

A stop is a serialized admission barrier, not a claim that every external effect
has stopped. Original step receipts and unknown writers remain in their journal.
"""
from __future__ import annotations

import json
import re
import time

from . import pm_lifecycle_gates as gates
from . import pm_release_baselines, pm_workspace
from .operations import TERMINAL, ActionDef, OperationError

STAGES = ('idea', 'planning', 'development')
ACTIONS = tuple('pm.lifecycle.' + name for name in ('set_stage', 'pause', 'close', 'reopen', 'retain_inflight'))
TRANSITION_ID = re.compile(r'pmlt_[0-9a-f]{32}')
OPERATION_ID = re.compile(r'op_[0-9a-f]{32}')
MAX_READ_BYTES = 2_000_000


def _bad(code, message, status=422):
    return OperationError(code, message, status)


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def _bytes(value):
    # Match HTTP's actual uncompressed JSON spaces and ASCII escaping.
    return len(json.dumps(value, ensure_ascii=True, allow_nan=False).encode('utf-8'))


def _exists(db, table):
    return db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone() is not None


def schema(journal):
    """Additive DDL only; workspace.schema calls this without recursive setup."""
    with journal.tx():
        for ddl in (
            '''CREATE TABLE IF NOT EXISTS pm_lifecycle_heads (
                workspace_id TEXT PRIMARY KEY, stage TEXT NOT NULL, revision INTEGER NOT NULL,
                execution_epoch INTEGER NOT NULL, transition_id TEXT, updated_at REAL NOT NULL,
                coverage TEXT NOT NULL)''',
            '''CREATE TABLE IF NOT EXISTS pm_lifecycle_transitions (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT, transition_id TEXT NOT NULL UNIQUE,
                workspace_id TEXT NOT NULL, revision INTEGER NOT NULL, action TEXT NOT NULL,
                from_stage TEXT, to_stage TEXT NOT NULL, execution_epoch INTEGER NOT NULL,
                reason TEXT NOT NULL, actor TEXT NOT NULL, created_at REAL NOT NULL,
                effective_sequence INTEGER NOT NULL, snapshot TEXT NOT NULL,
                operation_id TEXT NOT NULL UNIQUE)''',
            '''CREATE TABLE IF NOT EXISTS pm_lifecycle_operations (
                operation_id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL,
                execution_epoch INTEGER NOT NULL, kind TEXT NOT NULL, admitted_at REAL NOT NULL)''',
            '''CREATE TABLE IF NOT EXISTS pm_lifecycle_steps (
                operation_id TEXT NOT NULL, name TEXT NOT NULL, workspace_id TEXT NOT NULL,
                execution_epoch INTEGER NOT NULL, kind TEXT NOT NULL, admitted_at REAL NOT NULL,
                PRIMARY KEY(operation_id,name))''',
            '''CREATE TABLE IF NOT EXISTS pm_lifecycle_inflight (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT, transition_id TEXT NOT NULL,
                operation_id TEXT NOT NULL, captured_at REAL NOT NULL,
                UNIQUE(transition_id,operation_id))''',
            '''CREATE TABLE IF NOT EXISTS pm_lifecycle_retained (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT, workspace_id TEXT NOT NULL,
                original_operation_id TEXT NOT NULL, transition_id TEXT NOT NULL,
                reason TEXT NOT NULL, actor TEXT NOT NULL, created_at REAL NOT NULL,
                operation_id TEXT NOT NULL)''',
            '''CREATE TABLE IF NOT EXISTS pm_lifecycle_applied (
                operation_id TEXT PRIMARY KEY, result TEXT NOT NULL)''',
            'CREATE INDEX IF NOT EXISTS pm_lifecycle_history ON pm_lifecycle_transitions(workspace_id,sequence)',
            'CREATE INDEX IF NOT EXISTS pm_lifecycle_workspace_ops ON pm_lifecycle_operations(workspace_id,operation_id)',
        ):
            journal.db.execute(ddl)
        for table in ('pm_lifecycle_transitions', 'pm_lifecycle_inflight', 'pm_lifecycle_retained', 'pm_lifecycle_applied', 'pm_lifecycle_operations'):
            for verb in ('UPDATE', 'DELETE'):
                journal.db.execute(f'CREATE TRIGGER IF NOT EXISTS {table}_{verb.lower()}_immutable BEFORE {verb} ON {table} '
                                   "BEGIN SELECT RAISE(ABORT, 'Lifecycle history is immutable'); END")


def initialize(db, workspace_id, now):
    """Only workspace creation calls this; never infer a legacy phase during reads."""
    db.execute("INSERT INTO pm_lifecycle_heads VALUES(?,'idea',0,0,NULL,?,'explicit_creation')", (workspace_id, now))


def install(ops):
    pm_workspace.schema(ops.journal)
    for action in ACTIONS:
        if action not in ops.actions:
            ops.register(ActionDef(action, 'manage', 'Record an explicit workspace lifecycle decision',
                                   _run, _admitter(action), ('workspace_id',), _authorize_existing))


def _member(ops, principal, wid, *, decide=False):
    if not principal.allows('manage' if decide else 'observe'):
        raise _bad('FORBIDDEN', 'The required PM scope is missing', 403)
    workspace = pm_workspace.require_member(ops.db, principal, wid)
    if decide:
        role = ops.db.execute('SELECT role FROM pm_workspace_members WHERE workspace_id=? AND actor=?', (wid, principal.actor)).fetchone()
        if not role or role['role'] != 'owner':
            raise _bad('FORBIDDEN', 'A current workspace owner must make this direction decision', 403)
    return workspace


def _transition(db, wid, tid):
    if not isinstance(tid, str) or not TRANSITION_ID.fullmatch(tid):
        raise _bad('NOT_FOUND', 'Lifecycle transition not found', 404)
    row = db.execute('SELECT * FROM pm_lifecycle_transitions WHERE workspace_id=? AND transition_id=?', (wid, tid)).fetchone()
    if row is None:
        raise _bad('NOT_FOUND', 'Lifecycle transition not found', 404)
    return row


def _operation(db, oid):
    row = db.execute('SELECT * FROM operations WHERE operation_id=?', (oid,)).fetchone()
    if row is None:
        return None
    from .operations import OperationService
    return OperationService._decode(row)


def _visible(db, principal, oid):
    operation = _operation(db, oid)
    return bool(operation and pm_workspace.operation_visible(db, principal, operation))


def operation_visible(db, principal, operation):
    """Retaining private effects does not launder their IDs through operation reads."""
    row = db.execute('SELECT action,target,params FROM operations WHERE operation_id=?', (operation.get('operation_id'),)).fetchone()
    if row is None:
        return False
    try:
        pm_workspace.require_member(db, principal, json.loads(row['target'])['workspace_id'])
    except (OperationError, KeyError):
        return False
    params = json.loads(row['params'])
    if 'maintenance_ref' in params:
        from . import pm_maintenance_lifecycle as maintenance
        from .pm_workload import _Reader
        try:
            maintenance.sources(_Reader(db), principal, json.loads(row['target'])['workspace_id'], params['maintenance_ref'])
        except (OperationError, KeyError, TypeError, ValueError):
            return False
    if row['action'] != 'pm.lifecycle.retain_inflight':
        return True
    ids = json.loads(row['params']).get('operation_ids', [])
    return bool(ids) and all(_visible(db, principal, oid) for oid in ids)


def _writer(db, oid):
    if not _exists(db, 'pm_writer_slots') or not _exists(db, 'pm_run_state'):
        return None
    row = db.execute('''SELECT r.run_id,r.generation,s.status FROM pm_runs r
        JOIN pm_writer_slots w ON w.run_id=r.run_id AND w.generation=r.generation
        JOIN pm_run_state s ON s.run_id=r.run_id WHERE r.operation_id=?''', (oid,)).fetchone()
    return dict(row) if row else None


def _retention(db, oid):
    row = db.execute('''SELECT reason,actor,created_at,transition_id,operation_id FROM pm_lifecycle_retained
        WHERE original_operation_id=? ORDER BY sequence DESC LIMIT 1''', (oid,)).fetchone()
    return dict(row) if row else None


def _inflight(db, captured):
    oid = captured['operation_id']
    op = _operation(db, oid)
    if op is None:
        return None
    # Local rolled-back receipts are not uncertain external effects. Legacy
    # steps without classification stay conservatively unresolved.
    steps = [dict(row) for row in db.execute('''SELECT s.name,s.status FROM operation_steps s
        LEFT JOIN pm_lifecycle_steps l ON l.operation_id=s.operation_id AND l.name=s.name
        WHERE s.operation_id=? AND s.status IN ('started','uncertain')
        AND (l.kind IS NULL OR l.kind='external') ORDER BY s.seq''', (oid,))]
    writer = _writer(db, oid)
    retained = _retention(db, oid)
    unresolved = bool(steps or writer)
    disposition = ('retained' if retained else 'unresolved') if unresolved else ('settled' if op['status'] in TERMINAL else 'held')
    return {'sequence': captured['sequence'], 'operation_id': oid, 'action': op['action'], 'actor': op['actor'],
            'operation_status': op['status'], 'captured_at': captured['captured_at'], 'disposition': disposition,
            'steps': steps, 'writer': writer, 'retention': retained}


def _summary(ops, principal, tid):
    result = {'visible_unresolved': 0, 'visible_retained': 0, 'visibility_filtered': False}
    if not tid:
        return result
    for row in ops.db.execute('SELECT * FROM pm_lifecycle_inflight WHERE transition_id=? ORDER BY sequence', (tid,)):
        if not _visible(ops.db, principal, row['operation_id']):
            result['visibility_filtered'] = True
            continue
        item = _inflight(ops.db, row)
        if item and item['disposition'] in ('unresolved', 'retained'):
            result['visible_' + item['disposition']] += 1
    return result


def _status(db, current):
    if current['stage'] not in gates.STOPPED:
        return 'unclassified' if current['stage'] is None else 'active'
    for row in db.execute('SELECT * FROM pm_lifecycle_inflight WHERE transition_id=?', (current['transition_id'],)):
        item = _inflight(db, row)
        if item is None or item['disposition'] == 'unresolved':
            return 'pausing' if current['stage'] == 'paused' else 'closing'
    return current['stage']


def _metadata(row):
    return {key: row[key] for key in ('sequence', 'transition_id', 'workspace_id', 'revision', 'action',
        'from_stage', 'to_stage', 'execution_epoch', 'reason', 'actor', 'created_at', 'effective_sequence')} | {'snapshot': json.loads(row['snapshot'])}


def capabilities(ops):
    return {'version': 1, 'gated_actions': sorted(action for action, definition in ops.actions.items()
            if action.startswith('pm.') and 'workspace_id' in definition.target_keys and gates.kind(action) == 'investment'),
            'record_actions': sorted(gates.RECORD_ACTIONS),
            'record_variants': {action: value for action, value in gates.RECORD_VARIANTS.items() if action in ops.actions}}


def _transition_visible(db, principal, row):
    return operation_visible(db, principal, {'operation_id': row['operation_id']})


@pm_release_baselines._snapshot_read
def get_lifecycle(ops, principal, workspace_id):
    _member(ops, principal, workspace_id)
    current = gates.head(ops.db, workspace_id)
    tid = current['transition_id']
    transition = _transition(ops.db, workspace_id, tid) if tid else None
    shown = transition is None or _transition_visible(ops.db, principal, transition)
    from . import pm_maintenance_lifecycle
    maintenance = pm_maintenance_lifecycle.availability(ops, principal, workspace_id)
    if not shown and transition['to_stage'] == 'maintain':
        current = {**current, 'transition_id': None}
    return _bounded({'workspace_id': workspace_id, 'lifecycle': {**current, 'status': _status(ops.db, current)},
            'transition': _metadata(transition) if transition is not None and shown else None,
            'transition_visibility_filtered': not shown,
            'blocked_actions': capabilities(ops)['gated_actions'] if current['stage'] in gates.STOPPED else [],
            'record_actions': sorted(gates.RECORD_ACTIONS), 'record_variants': capabilities(ops)['record_variants'],
            'available_stages': [*STAGES, *(['maintain'] if maintenance['available'] else [])],
            'maintenance': maintenance,
            'inflight_summary': _summary(ops, principal, tid)})


def _page(after, limit):
    if type(after) is not int or not 0 <= after <= 2**63 - 1 or type(limit) is not int or not 1 <= limit <= 100:
        raise _bad('INVALID_PARAMS', 'Use a nonnegative 64-bit cursor and a limit of 1–100')


def _bounded(result):
    if _bytes(result) > MAX_READ_BYTES:
        raise _bad('PM_LIFECYCLE_TOO_LARGE', 'A complete lifecycle record exceeds the 2 MB response budget', 413)
    return result


@pm_release_baselines._snapshot_read
def list_transitions(ops, principal, workspace_id, *, after=0, limit=20):
    _member(ops, principal, workspace_id)
    _page(after, limit)
    rows = ops.db.execute('SELECT * FROM pm_lifecycle_transitions WHERE workspace_id=? AND sequence>? ORDER BY sequence LIMIT ?',
                          (workspace_id, after, limit + 1)).fetchall()
    result = {'workspace_id': workspace_id, 'transitions': [], 'next_cursor': None, 'visibility_filtered': False}
    consumed = 0
    for row in rows[:limit]:
        if not _transition_visible(ops.db, principal, row):
            result['visibility_filtered'], result['next_cursor'] = True, row['sequence']
            consumed += 1
            continue
        candidate = {**result, 'transitions': [*result['transitions'], _metadata(row)], 'next_cursor': row['sequence']}
        if _bytes(candidate) > MAX_READ_BYTES:
            if not consumed:
                return _bounded(candidate)
            break
        result, consumed = candidate, consumed + 1
    if consumed == len(rows):
        result['next_cursor'] = None
    return _bounded(result)


@pm_release_baselines._snapshot_read
def get_transition(ops, principal, workspace_id, transition_id):
    _member(ops, principal, workspace_id)
    row = _transition(ops.db, workspace_id, transition_id)
    if not _transition_visible(ops.db, principal, row):
        raise _bad('NOT_FOUND', 'Lifecycle transition not found', 404)
    return _bounded({'workspace_id': workspace_id, 'transition': _metadata(row),
                     'inflight_summary': _summary(ops, principal, transition_id)})


@pm_release_baselines._snapshot_read
def list_inflight(ops, principal, workspace_id, *, transition_id=None, after=0, limit=20):
    _member(ops, principal, workspace_id)
    _page(after, limit)
    tid = gates.head(ops.db, workspace_id)['transition_id'] if transition_id is None else transition_id
    if tid is not None:
        transition = _transition(ops.db, workspace_id, tid)
        if transition['to_stage'] == 'maintain' and not _transition_visible(ops.db, principal, transition):
            if transition_id is not None:
                raise _bad('NOT_FOUND', 'Lifecycle transition not found', 404)
            return {'workspace_id': workspace_id, 'transition_id': None, 'inflight': [], 'next_cursor': None, 'visibility_filtered': True, 'omitted': None}
    rows = ops.db.execute('SELECT * FROM pm_lifecycle_inflight WHERE transition_id=? AND sequence>? ORDER BY sequence LIMIT ?',
                          (tid, after, limit + 1)).fetchall()
    result = {'workspace_id': workspace_id, 'transition_id': tid, 'inflight': [], 'next_cursor': None, 'visibility_filtered': False, 'omitted': 0}
    consumed = 0
    for row in rows[:limit]:
        if not _visible(ops.db, principal, row['operation_id']):
            result['visibility_filtered'], result['next_cursor'] = True, row['sequence']
            result['omitted'] += 1
            consumed += 1
            continue
        item = _inflight(ops.db, row)
        if item and item['retention']:
            retained_by = item['retention'].pop('operation_id')
            if not operation_visible(ops.db, principal, {'operation_id': retained_by}):
                item['retention'] = None
                item['retention_visibility_filtered'] = True
        candidate = {**result, 'inflight': [*result['inflight'], item], 'next_cursor': row['sequence']}
        if _bytes(candidate) > MAX_READ_BYTES:
            if not consumed:
                return _bounded(candidate)
            break
        result, consumed = candidate, consumed + 1
    if consumed == len(rows):
        result['next_cursor'] = None
    return _bounded(result)


def _check(ops, principal, action, target, params, pre):
    if set(target) != {'workspace_id'} or set(pre) != {'expected_lifecycle_revision'}:
        raise _bad('INVALID_PARAMS', 'Select a workspace and its exact lifecycle revision')
    wid = target['workspace_id']
    _member(ops, principal, wid, decide=True)
    expected = pre['expected_lifecycle_revision']
    if type(expected) is not int or expected < 0:
        raise _bad('PRECONDITION_REQUIRED', 'Supply the nonnegative lifecycle revision')
    current = gates.head(ops.db, wid)
    if current['revision'] != expected:
        raise _bad('VERSION_CONFLICT', 'Read the current lifecycle before deciding', 409)
    suffix = action.removeprefix('pm.lifecycle.')
    fields = {'stage', 'reason'} if suffix in ('set_stage', 'reopen') else {'transition_id', 'operation_ids', 'reason'} if suffix == 'retain_inflight' else {'reason'}
    if suffix in ('set_stage', 'reopen') and params.get('stage') == 'maintain' and 'maintenance_ref' in params:
        fields = fields | {'maintenance_ref'}
    if set(params) != fields:
        raise _bad('INVALID_PARAMS', 'Supply exactly the lifecycle decision fields')
    reason = params['reason']
    if not isinstance(reason, str) or not reason.strip() or len(reason) > 4000 or '\x00' in reason:
        raise _bad('INVALID_PARAMS', 'A lifecycle decision needs a reason of 1–4000 characters')
    try:
        reason.encode('utf-8')
    except UnicodeError:
        raise _bad('INVALID_PARAMS', 'The decision reason must be valid UTF-8') from None
    if suffix in ('set_stage', 'reopen'):
        if params['stage'] == 'maintain':
            from . import pm_maintenance_lifecycle
            pm_maintenance_lifecycle.require(ops, principal, wid, params.get('maintenance_ref'), reopening=suffix == 'reopen')
            if suffix == 'set_stage' and current['stage'] != 'development':
                raise _bad('PM_LIFECYCLE_TRANSITION_INVALID', 'Enter maintenance from Development or explicitly reopen', 409)
        elif params['stage'] not in STAGES:
            raise _bad('INVALID_PARAMS', 'Select Idea, Planning or Development')
        if (suffix == 'reopen') != (current['stage'] in gates.STOPPED):
            raise _bad('PM_LIFECYCLE_TRANSITION_INVALID', 'Use explicit reopen for a stopped workspace, or set_stage for active work', 409)
        if suffix == 'set_stage' and params['stage'] == current['stage']:
            raise _bad('PM_LIFECYCLE_UNCHANGED', 'The workspace already has this stage', 409)
        stage = params['stage']
    elif suffix in ('pause', 'close'):
        stage = 'paused' if suffix == 'pause' else 'closed'
        if current['stage'] == stage or current['stage'] == 'closed':
            raise _bad('PM_LIFECYCLE_TRANSITION_INVALID', 'Explicitly reopen before changing a closed workspace', 409)
    else:
        if current['stage'] not in gates.STOPPED or params['transition_id'] != current['transition_id']:
            raise _bad('PM_LIFECYCLE_TRANSITION_INVALID', 'Retain exact effects from the current stop transition', 409)
        ids = params['operation_ids']
        if (not isinstance(ids, list) or not 1 <= len(ids) <= 100 or any(not isinstance(oid, str) or not OPERATION_ID.fullmatch(oid) for oid in ids)
                or len(set(ids)) != len(ids)):
            raise _bad('INVALID_PARAMS', 'Select 1–100 distinct original operation IDs')
        for oid in ids:
            row = ops.db.execute('SELECT * FROM pm_lifecycle_inflight WHERE transition_id=? AND operation_id=?', (current['transition_id'], oid)).fetchone()
            if not row or not _visible(ops.db, principal, oid):
                raise _bad('NOT_FOUND', 'Original inflight operation not found', 404)
            if _inflight(ops.db, row)['disposition'] != 'unresolved':
                raise _bad('PM_LIFECYCLE_NOT_UNRESOLVED', 'Only an unresolved original effect can be explicitly retained', 409)
        stage = current['stage']
    return current, stage


def _admitter(action):
    def admit(ops, principal, target, params, pre):
        _check(ops, principal, action, target, params, pre)
        return {'principal': pm_workspace.principal_binding(principal)}
    return admit


def _authorize_existing(ops, principal, op, verb):
    if verb == 'replay':
        pm_workspace.require_member(ops.db, principal, op['target']['workspace_id'])
    else:
        _member(ops, principal, op['target']['workspace_id'], decide=True)
    if not principal.allows('manage') or not operation_visible(ops.db, principal, op):
        raise _bad('NOT_FOUND', 'Lifecycle operation not found', 404)
    if verb != 'replay' and op['actor'] != principal.actor:
        raise _bad('FORBIDDEN', 'Only the original caller may control this decision', 403)


def _snapshot(db, wid, now):
    """Bounded local management heads, no invented remote observation or release."""
    workspace = db.execute('SELECT version FROM pm_workspaces WHERE workspace_id=?', (wid,)).fetchone()
    plan = db.execute('SELECT version,accepted_revision_id FROM pm_plan_heads WHERE workspace_id=?', (wid,)).fetchone() if _exists(db, 'pm_plan_heads') else None
    return {'as_of': now, 'workspace_version': workspace['version'],
            'journal_sequence': db.execute('SELECT COALESCE(MAX(seq),0) FROM api_events').fetchone()[0],
            'accepted_plan': dict(plan) if plan else None,
            'release_baseline': None, 'maintenance_policy': None,
            'coverage': {'source': 'current_local_journal', 'external_observation': 'not_requested',
                         'delivery': 'unassessed', 'unfinished_work': 'unchanged', 'automatic_resumption': False}}


def _capture(ops, wid, tid, now, own_operation):
    # Include pending old intents and unresolved terminal operations/writers. No
    # names/paths are inferred; only explicit PM targets or trusted lineage count.
    rows = ops.db.execute('''SELECT o.operation_id FROM operations o LEFT JOIN pm_lifecycle_operations l USING(operation_id)
        WHERE (l.workspace_id=? OR (o.action LIKE 'pm.%' AND json_extract(o.target,'$.workspace_id')=?))
        AND o.operation_id!=? ORDER BY o.rowid''', (wid, wid, own_operation)).fetchall()
    for row in rows:
        oid = row['operation_id']
        binding = gates.operation_binding(ops.db, oid)
        if binding is None or binding['kind'] != 'investment':
            continue
        item = _inflight(ops.db, {'operation_id': oid, 'sequence': 0, 'captured_at': now})
        if item and (item['operation_status'] not in TERMINAL or item['disposition'] in ('unresolved', 'retained')):
            ops.db.execute('INSERT INTO pm_lifecycle_inflight(transition_id,operation_id,captured_at) VALUES(?,?,?)', (tid, oid, now))


async def _run(ctx):
    ops = ctx.service
    with ops.journal.tx():
        saved = ops.db.execute('SELECT result FROM pm_lifecycle_applied WHERE operation_id=?', (ctx.operation_id,)).fetchone()
        if saved:
            return json.loads(saved[0])
        ctx.check_cancel()
        principal = pm_workspace.admitted_principal(ctx)
        current, stage = _check(ops, principal, ctx.op['action'], ctx.target, ctx.params, ctx.preconditions)
        from . import pm_maintenance_lifecycle
        pm_maintenance_lifecycle.require_automatic_parent(ops, ctx.op)
        wid, now = current['workspace_id'], time.time()
        tid = 'pmlt_' + ctx.operation_id[3:]
        action = ctx.op['action']
        epoch = current['execution_epoch'] + int(action in ('pm.lifecycle.pause', 'pm.lifecycle.close', 'pm.lifecycle.reopen'))
        revision = current['revision'] + 1
        snapshot = _snapshot(ops.db, wid, now)
        if 'maintenance_ref' in ctx.params:
            snapshot['maintenance'] = ctx.params['maintenance_ref']
        if action == 'pm.lifecycle.retain_inflight':
            for oid in ctx.params['operation_ids']:
                ops.db.execute('''INSERT INTO pm_lifecycle_retained(workspace_id,original_operation_id,transition_id,reason,actor,created_at,operation_id)
                    VALUES(?,?,?,?,?,?,?)''', (wid, oid, tid, ctx.params['reason'], ctx.actor, now, ctx.operation_id))
        _capture(ops, wid, tid, now, ctx.operation_id)
        seq = ops.journal.api_event('pm_lifecycle', wid, action,
            {'workspace_id': wid, 'transition_id': tid, 'revision': revision, 'stage': stage,
             'execution_epoch': epoch, 'operation_id': ctx.operation_id}, actor=ctx.actor)
        ops.db.execute('''INSERT INTO pm_lifecycle_transitions(transition_id,workspace_id,revision,action,from_stage,to_stage,
            execution_epoch,reason,actor,created_at,effective_sequence,snapshot,operation_id) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)''',
            (tid, wid, revision, action, current['stage'], stage, epoch, ctx.params['reason'], ctx.actor, now, seq, _json(snapshot), ctx.operation_id))
        ops.db.execute('''INSERT INTO pm_lifecycle_heads VALUES(?,?,?,?,?,?,'explicit_decision') ON CONFLICT(workspace_id)
            DO UPDATE SET stage=excluded.stage,revision=excluded.revision,execution_epoch=excluded.execution_epoch,
            transition_id=excluded.transition_id,updated_at=excluded.updated_at,coverage=excluded.coverage''',
            (wid, stage, revision, epoch, tid, now))
        result = {'workspace_id': wid, 'transition_id': tid, 'revision': revision, 'stage': stage,
                  'execution_epoch': epoch, 'status': _status(ops.db, gates.head(ops.db, wid))}
        if action == 'pm.lifecycle.retain_inflight':
            result['retained_count'] = len(ctx.params['operation_ids'])
        ops.db.execute('INSERT INTO pm_lifecycle_applied VALUES(?,?)', (ctx.operation_id, _json(result)))
        return result
