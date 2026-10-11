"""Bounded fixed engineering selections composed on the existing operation worker."""
from __future__ import annotations

import json
import math
import re
import time

from . import (
    api_auth,
    controller_authority,
    pm_lifecycle_gates,
    pm_package_handoff,
    pm_source_lineage,
    pm_spec_format,
    pm_workspace,
)
from . import pm_release_baselines as snapshots
from . import pm_work_batch_evidence as evidence
from .operations import ActionDef, OperationError, OperationService, Wait

START = 'pm.work_batch.start'
RECORD = 'pm.work_batch.evidence.record'
BATCH = re.compile('pmbt_[0-9a-f]{32}')
KEY = re.compile('[a-z][a-z0-9_]{0,47}')
REQUIRED = frozenset({'observe', 'manage', 'start'})
MAX_ENTRIES = 16
MAX_READ_BYTES = 256_000
MAX_REQUEST_BYTES = 384_000
TERMINAL = frozenset({'succeeded', 'failed', 'cancelled'})
DEFAULT_CONDITION = {'kind': 'fixed_revision_verification', 'profile': 'github_reported_checks_v1'}


def _now():
    return time.time()


def _bad(code, message, status=409):
    return OperationError(code, message, status)


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def _size(value):
    return len(json.dumps(value, ensure_ascii=False, allow_nan=False).encode())


def _bounded(value):
    if _size(value) > MAX_READ_BYTES:
        raise _bad('PM_BATCH_READ_TOO_LARGE', 'One whole batch exceeds the public read budget', 413)
    return value


def installed(db):
    return pm_source_lineage._table(db, 'pm_work_batches')


def schema(journal):
    with journal.tx():
        journal.db.execute('''CREATE TABLE IF NOT EXISTS pm_work_batches (
            sequence INTEGER PRIMARY KEY AUTOINCREMENT,batch_id TEXT NOT NULL UNIQUE,
            operation_id TEXT NOT NULL UNIQUE,workspace_id TEXT NOT NULL,context TEXT NOT NULL,
            context_digest TEXT NOT NULL,created_at REAL NOT NULL,version INTEGER NOT NULL DEFAULT 1)''')
        journal.db.execute('''CREATE TABLE IF NOT EXISTS pm_work_batch_children (
            parent_id TEXT NOT NULL,entry_key TEXT NOT NULL,operation_id TEXT NOT NULL UNIQUE,
            request TEXT NOT NULL,request_digest TEXT NOT NULL,created_at REAL NOT NULL,
            PRIMARY KEY(parent_id,entry_key))''')
        journal.db.execute('''CREATE TABLE IF NOT EXISTS pm_work_batch_states (
            sequence INTEGER PRIMARY KEY AUTOINCREMENT,parent_id TEXT NOT NULL,entry_key TEXT NOT NULL,
            document TEXT NOT NULL,document_digest TEXT NOT NULL,created_at REAL NOT NULL)''')
        journal.db.execute('''CREATE TABLE IF NOT EXISTS pm_work_batch_evidence (
            sequence INTEGER PRIMARY KEY AUTOINCREMENT,parent_id TEXT NOT NULL,entry_key TEXT NOT NULL,
            operation_id TEXT NOT NULL,version INTEGER NOT NULL,document TEXT NOT NULL,
            document_digest TEXT NOT NULL,created_at REAL NOT NULL,UNIQUE(operation_id,entry_key))''')
        journal.db.execute('''CREATE TABLE IF NOT EXISTS pm_work_batch_retries (
            operation_id TEXT NOT NULL,transition_digest TEXT NOT NULL,step TEXT NOT NULL,
            created_at REAL NOT NULL,PRIMARY KEY(operation_id,transition_digest))''')
        for table in ('pm_work_batch_states', 'pm_work_batch_evidence'):
            journal.db.execute(f'CREATE INDEX IF NOT EXISTS {table}_parent ON {table}(parent_id,entry_key,sequence)')
        for table in ('pm_work_batch_children', 'pm_work_batch_states', 'pm_work_batch_evidence', 'pm_work_batch_retries'):
            for verb in ('UPDATE', 'DELETE'):
                journal.db.execute(f'CREATE TRIGGER IF NOT EXISTS {table}_{verb.lower()}_immutable '
                    f"BEFORE {verb} ON {table} BEGIN SELECT RAISE(ABORT,'Batch history is immutable'); END")
        journal.db.execute('''CREATE TRIGGER IF NOT EXISTS pm_work_batches_identity_immutable
            BEFORE UPDATE OF batch_id,operation_id,workspace_id,context,context_digest,created_at ON pm_work_batches
            BEGIN SELECT RAISE(ABORT,'Batch selection is immutable'); END''')
        journal.db.execute('''CREATE TRIGGER IF NOT EXISTS pm_work_batches_delete_immutable
            BEFORE DELETE ON pm_work_batches BEGIN SELECT RAISE(ABORT,'Batch selection is immutable'); END''')


def install(ops):
    schema(ops.journal)
    for name, run, admit, targets in ((START, _run, _admit, ('workspace_id',)),
            (RECORD, _record_evidence, _admit_evidence, ('workspace_id', 'batch_id'))):
        if name not in ops.actions:
            ops.register(ActionDef(name, 'manage', 'Bounded selected engineering batch', run, admit, targets,
                authorize_existing=_authorize_existing))


def capabilities(ops=None):
    return {'version': 1, 'actions': [START, RECORD], 'profiles': sorted(evidence.PROFILES),
        'max_entries': MAX_ENTRIES, 'max_parallel': 8, 'max_retries': 3, 'max_deadline_seconds': 86400,
        'semantic_acceptance': False, 'intrinsic_requirement_dependencies': 'remain_unassessed',
        'execution_engine': 'repository.continue', 'cost_coverage': 'start_count_only_model_provider_cost_unmeasured'}


def _member(ops, principal, wid, *, write=False):
    scopes = REQUIRED if write else {'observe'}
    if not all(principal.allows(scope) for scope in scopes):
        raise _bad('FORBIDDEN', 'Batch dispatch requires observe, manage and start; reads require observe', 403)
    return pm_workspace.require_member(ops.db, principal, wid, write=write)


def _limits(value, now):
    if not isinstance(value, dict) or set(value) != {'max_starts', 'max_parallel', 'deadline_at', 'max_retries'}:
        raise _bad('INVALID_PARAMS', 'Use exact batch start, concurrency, deadline and retry limits', 422)
    for key, low, high in (('max_starts', 1, MAX_ENTRIES), ('max_parallel', 1, 8), ('max_retries', 0, 3)):
        if type(value[key]) is not int or not low <= value[key] <= high:
            raise _bad('INVALID_PARAMS', 'Invalid batch limit: ' + key, 422)
    end = value['deadline_at']
    if type(end) not in (int, float) or not math.isfinite(end) or not now < end <= now + 86400 or value['max_parallel'] > value['max_starts']:
        raise _bad('INVALID_PARAMS', 'Batch deadline or parallel limit is outside the supported bound', 422)
    return value


def _rationale(value):
    if not isinstance(value, str) or not value.strip() or len(value) > 4000:
        raise _bad('INVALID_PARAMS', 'A bounded nonempty rationale is required', 422)


def _selection(ops, principal, wid, params):
    if set(params) != {'entries', 'limits', 'rationale'} or not isinstance(params['entries'], list) or not 1 <= len(params['entries']) <= MAX_ENTRIES:
        raise _bad('INVALID_PARAMS', 'Select 1–16 explicit engineering entries', 422)
    _rationale(params['rationale'])
    _limits(params['limits'], _now())
    if _size(params) > MAX_REQUEST_BYTES:
        raise _bad('PM_BATCH_REQUEST_TOO_LARGE', 'Select a smaller whole batch', 413)
    keys, works, operation_ids, normalized = set(), set(), set(), []
    for entry in params['entries']:
        if (not isinstance(entry, dict) or set(entry) - {'key', 'handoff', 'depends_on', 'result_condition'}
                or not {'key', 'handoff', 'depends_on'} <= set(entry) or not KEY.fullmatch(str(entry['key'])) or entry['key'] in keys):
            raise _bad('INVALID_PARAMS', 'Distinct stable entry keys are required', 422)
        normalized.append({**entry, 'result_condition': evidence.condition(entry.get('result_condition', dict(DEFAULT_CONDITION)))})
        keys.add(entry['key'])
        handoff = entry['handoff']
        if not isinstance(handoff, dict) or set(handoff) != {'target', 'params', 'preconditions'} or any(not isinstance(handoff[key], dict) for key in handoff):
            raise _bad('INVALID_PARAMS', 'Each entry selects the exact existing published handoff envelope', 422)
        ref = pm_package_handoff._ref(handoff['params'].get('pm_package_ref'))
        if ref['workspace_id'] != wid:
            raise _bad('PM_BATCH_WORKSPACE_CHANGED', 'Every fixed package must belong to the selected workspace')
        package = pm_package_handoff._package(ops, principal, ref, write=True)
        pm_package_handoff._basis(handoff['params'].get('pm_code_basis'))
        pm_package_handoff._matches(package['document'], handoff['target'], handoff['params'], handoff['preconditions'])
        from . import repository_sync
        try:
            repository_sync.admit(ops, principal, handoff['target'], handoff['params'], handoff['preconditions'])
        except OperationError as exc:
            if exc.code != 'PM_PACKAGE_NOT_READY':
                raise
        work = package['document']['work_item_id']
        if work in works:
            raise _bad('INVALID_PARAMS', 'One explicit batch cannot dispatch a WorkItem twice', 422)
        works.add(work)
        chain = pm_spec_format.history(ops.db, package)
        operation_ids.update(row['operation_id'] for row in chain)
        if len(operation_ids) > 256:
            raise _bad('PM_SOURCE_LINEAGE_LIMIT', 'The selected original package histories exceed the batch bound')
        if not isinstance(entry['depends_on'], list) or len(entry['depends_on']) > MAX_ENTRIES:
            raise _bad('INVALID_PARAMS', 'Batch prerequisites must be a bounded list', 422)
        seen = set()
        for edge in entry['depends_on']:
            if (not isinstance(edge, dict) or set(edge) != {'entry_key', 'condition'}
                    or not isinstance(edge['entry_key'], str) or not KEY.fullmatch(edge['entry_key']) or edge['entry_key'] in seen):
                raise _bad('INVALID_PARAMS', 'Each prerequisite selects one distinct original entry', 422)
            seen.add(edge['entry_key'])
            evidence.condition(edge['condition'])
    graph = {entry['key']: {edge['entry_key'] for edge in entry['depends_on']} for entry in params['entries']}
    selected = {entry['key']: entry for entry in normalized}
    for entry in normalized:
        for edge in entry['depends_on']:
            if edge['entry_key'] in selected and edge['condition'] != selected[edge['entry_key']]['result_condition']:
                raise _bad('INVALID_PARAMS', 'A prerequisite must match its upstream fixed result condition', 422)
    visited, walking = set(), set()
    def visit(key):
        if key not in graph or key in walking:
            raise _bad('INVALID_PARAMS', 'Batch dependencies must be selected and acyclic', 422)
        if key in visited:
            return
        walking.add(key)
        for previous in graph[key]:
            visit(previous)
        walking.remove(key)
        visited.add(key)
    for key in keys:
        visit(key)
    return sorted(operation_ids), normalized


def _admit(ops, principal, target, params, pre):
    if set(target) != {'workspace_id'}:
        raise _bad('INVALID_TARGET', 'Select one exact PM workspace', 422)
    wid = target['workspace_id']
    with ops.journal.tx():
        workspace = _member(ops, principal, wid, write=True)
        lifecycle = pm_lifecycle_gates.head(ops.db, wid)
        if set(pre) != {'expected_workspace_version', 'expected_lifecycle_revision'} or any(type(pre[k]) is not int for k in pre):
            raise _bad('INVALID_PRECONDITIONS', 'Exact workspace and lifecycle revisions are required', 422)
        if pre['expected_workspace_version'] != workspace['version'] or pre['expected_lifecycle_revision'] != lifecycle['revision']:
            raise _bad('PM_BATCH_CONTEXT_CHANGED', 'Workspace or lifecycle revision changed')
        original_ids, entries = _selection(ops, principal, wid, params)
        intent = {'entries': entries, 'limits': params['limits'], 'operation_ids': original_ids,
            'workspace_version': workspace['version'], 'execution_epoch': lifecycle['execution_epoch'],
            'lifecycle_revision': lifecycle['revision'], 'controller': controller_authority.binding(ops.db, principal, pm_workspace_id=wid)}
    return {'principal': pm_workspace.principal_binding(principal), 'work_batch_intent': intent,
        'work_batch_intent_digest': evidence.digest(intent), 'pm_lineage': {'workspace_id': wid}}


def _op(ops, oid):
    row = ops.db.execute('SELECT * FROM operations WHERE operation_id=?', (oid,)).fetchone()
    if not row:
        raise _bad('NOT_FOUND', 'Original batch operation is unavailable', 404)
    return OperationService._decode(row)


def _intent(op):
    saved = (op.get('external_refs') or {}).get('admission_binding') or {}
    intent = saved.get('work_batch_intent')
    if not isinstance(intent, dict) or evidence.digest(intent) != saved.get('work_batch_intent_digest'):
        raise _bad('PM_BATCH_CONTEXT_INVALID', 'Original batch selection cannot be verified')
    return intent


def _batch(ops, wid, bid):
    if not isinstance(bid, str) or not BATCH.fullmatch(bid):
        raise _bad('NOT_FOUND', 'Batch is unavailable', 404)
    row = ops.db.execute('SELECT * FROM pm_work_batches WHERE workspace_id=? AND batch_id=?', (wid, bid)).fetchone()
    if not row:
        raise _bad('NOT_FOUND', 'Batch is unavailable', 404)
    value = dict(row)
    value['context'] = json.loads(value['context'])
    if evidence.digest(value['context']) != value['context_digest'] or value['context'] != _intent(_op(ops, value['operation_id'])):
        raise _bad('PM_BATCH_CONTEXT_INVALID', 'Original batch record cannot be verified')
    return value


def _access(ops, principal, op):
    wid, intent = op['target']['workspace_id'], _intent(op)
    _member(ops, principal, wid)
    for entry in intent['entries']:
        pm_package_handoff._package(ops, principal, entry['handoff']['params']['pm_package_ref'])
        for ref in entry['handoff']['params'].get('artifacts', []):
            pm_source_lineage.require(ops.db, principal, 'artifact', ref.get('artifact_id'))
    for oid in intent['operation_ids']:
        pm_source_lineage.require(ops.db, principal, 'operation', oid)
    for row in ops.db.execute('SELECT document FROM pm_work_batch_evidence WHERE parent_id=?', (op['operation_id'],)):
        document = json.loads(row[0])
        evidence.require_sources(ops, principal, wid, document)


def _current_principal(ops, op):
    saved = op['external_refs']['admission_binding']['principal']
    now, actor = _now(), op['actor']
    if saved['actor'] != actor or not (saved['admin'] or REQUIRED <= set(saved['scopes'])):
        raise _bad('PM_BATCH_AUTHORITY_UNAVAILABLE', 'Original batch authority is unavailable', 403)
    principal = None
    for row in ops.db.execute('SELECT token_hash,scopes FROM api_principals WHERE actor=? AND revoked_at IS NULL '
            'AND created_at<=? AND (expires_at IS NULL OR expires_at>?) ORDER BY created_at DESC LIMIT 64', (actor, now, now)):
        scopes = json.loads(row['scopes'])
        if isinstance(scopes, list) and REQUIRED <= set(scopes):
            principal = api_auth.Principal(actor, REQUIRED, credential_id=row['token_hash'])
            break
    if principal is None and saved['admin']:
        daemon = ops.context.get('daemon')
        token = vars(daemon).get('_admin_token') if daemon else None
        principal = api_auth.authenticate(ops.db, token, token) if isinstance(token, str) and token else None
        if principal and principal.actor != actor:
            principal = None
    if principal is None:
        raise _bad('PM_BATCH_AUTHORITY_UNAVAILABLE', 'No single current credential authorizes the original batch', 403)
    _member(ops, principal, op['target']['workspace_id'], write=True)
    current = controller_authority.binding(ops.db, principal, pm_workspace_id=op['target']['workspace_id'])
    if not controller_authority.same_controller(current, _intent(op)['controller']):
        raise _bad('PM_BATCH_CONTROLLER_CHANGED', 'The original batch controller changed', 403)
    return principal


def _guard(ops, op):
    if op['action'] != START or op['cancel_requested'] or op['status'] in TERMINAL:
        raise _bad('PM_BATCH_STOPPED', 'The original batch has stopped new admission')
    intent, now = _intent(op), _now()
    if now < op['created_at'] or now >= intent['limits']['deadline_at']:
        raise _bad('PM_BATCH_DEADLINE', 'The original absolute batch deadline has elapsed')
    current = pm_lifecycle_gates.head(ops.db, op['target']['workspace_id'])
    pm_lifecycle_gates._require(current, intent['execution_epoch'])
    principal = _current_principal(ops, op)
    _access(ops, principal, op)
    return principal


def _request(parent, entry):
    return {'action': 'repository.continue', **entry['handoff'],
        'idempotency_key': 'pm-batch:' + parent['operation_id'] + ':' + entry['key']}


def _child(ops, parent, entry):
    link = ops.db.execute('SELECT * FROM pm_work_batch_children WHERE parent_id=? AND entry_key=?',
        (parent['operation_id'], entry['key'])).fetchone()
    if link is None:
        return None
    child, request = _op(ops, link['operation_id']), json.loads(link['request'])
    if (evidence.digest(request) != link['request_digest'] or request != _request(parent, entry)
            or any(child[key] != value for key, value in request.items()) or child['actor'] != parent['actor']
            or (child.get('external_refs') or {}).get('pm_work_batch_parent') != {'operation_id': parent['operation_id'], 'entry_key': entry['key']}
            or not controller_authority.same_controller(controller_authority.operation_binding(child), _intent(parent)['controller'])):
        raise _bad('PM_BATCH_CHILD_CHANGED', 'The original child receipt does not match this fixed batch')
    return child


def _uncertain(ops, child):
    return ops.db.execute("SELECT 1 FROM operation_steps s LEFT JOIN pm_lifecycle_steps l "
        "ON l.operation_id=s.operation_id AND l.name=s.name WHERE s.operation_id=? "
        "AND s.status IN ('started','uncertain') AND (l.kind IS NULL OR l.kind='external') LIMIT 1",
        (child['operation_id'],)).fetchone() is not None


def _proofs(ops, parent, key):
    rows = ops.db.execute('SELECT document,document_digest FROM pm_work_batch_evidence WHERE parent_id=? AND entry_key=? ORDER BY sequence DESC LIMIT 64',
        (parent['operation_id'], key)).fetchall()
    result = []
    for row in rows:
        document = json.loads(row['document'])
        if evidence.digest(document) != row['document_digest']:
            raise _bad('PM_BATCH_EVIDENCE_CHANGED', 'An original fixed verification receipt changed')
        result.append(document)
    return result


def _verified(ops, principal, parent, entry, condition=None):
    child = _child(ops, parent, entry)
    if child is None:
        return False
    required = condition or entry['result_condition']
    for proof in _proofs(ops, parent, entry['key']):
        if proof['status'] != 'passed' or proof['condition'] != required:
            continue
        current = evidence.evaluate(ops, principal, parent['target']['workspace_id'], child,
            proof['condition'], proof['reference'])
        if current['status'] == 'passed' and current['receipt_digest'] == proof['receipt_digest']:
            return True
    return False


def _active(ops, principal, parent):
    count = 0
    for entry in _intent(parent)['entries']:
        child = _child(ops, parent, entry)
        if child is None:
            continue
        if child['status'] in {'failed', 'cancelled'} and not _uncertain(ops, child):
            continue
        if child['status'] == 'succeeded' and _verified(ops, principal, parent, entry):
            continue
        count += 1
    return count


def require_child(ops, operation):
    """Registered unsent-step and final-frame guard; never a new authority source."""
    link = (operation.get('external_refs') or {}).get('pm_work_batch_parent')
    if not installed(ops.db):
        if link is None:
            return
        raise _bad('PM_BATCH_CHILD_CHANGED', 'Unproven batch parent')
    row = ops.db.execute('SELECT parent_id,entry_key FROM pm_work_batch_children WHERE operation_id=?',
        (operation['operation_id'],)).fetchone()
    if link is None and row is None:
        return
    if not row or not isinstance(link, dict) or link != {'operation_id': row['parent_id'], 'entry_key': row['entry_key']}:
        raise _bad('PM_BATCH_CHILD_CHANGED', 'Unproven private batch parent link')
    parent = _op(ops, row['parent_id'])
    entry = next((entry for entry in _intent(parent)['entries'] if entry['key'] == row['entry_key']), None)
    if entry is None or _child(ops, parent, entry)['operation_id'] != operation['operation_id']:
        raise _bad('PM_BATCH_CHILD_CHANGED', 'The child is outside the original explicit selection')
    principal = _guard(ops, parent)
    limits = _intent(parent)['limits']
    if _active(ops, principal, parent) > limits['max_parallel']:
        raise _bad('PM_BATCH_PARALLEL_LIMIT', 'Original batch parallel capacity is unavailable')
    return limits['deadline_at']


def admit_restart(ops, operation_id, step_name):
    """Reserve one proven-unsent transition inside the original restart transaction."""
    operation = _op(ops, operation_id)
    if require_child(ops, operation) is None:
        return
    if not ops.db.in_transaction:
        raise _bad('PM_BATCH_RETRY_TRANSACTION', 'Retry reservation requires the existing journal transaction')
    link = operation['external_refs']['pm_work_batch_parent']
    limit = _intent(_op(ops, link['operation_id']))['limits']['max_retries']
    step = ops.db.execute('SELECT * FROM operation_steps WHERE operation_id=? AND name=?', (operation_id, step_name)).fetchone()
    if not step or step['status'] not in {'started', 'uncertain'}:
        raise _bad('PM_BATCH_RETRY_UNPROVEN', 'Only an original unresolved step can reserve a proven-unsent retry')
    # A repeated hook for this same pre-transition row is idempotent. The
    # actual restart emits a new step event in the SAME transaction, making a
    # later proved-unsent retry a different bounded transition even if clocks tie.
    previous = ops.db.execute("SELECT MAX(seq) FROM api_events WHERE resource_type='operation' AND resource_id=? "
        "AND kind LIKE 'operation.step.%' AND json_extract(body,'$.step')=?", (operation_id, step_name)).fetchone()[0]
    transition = evidence.digest({'step': dict(step), 'original_step_event': previous})
    if ops.db.execute('SELECT 1 FROM pm_work_batch_retries WHERE operation_id=? AND transition_digest=?', (operation_id, transition)).fetchone():
        return
    if ops.db.execute('SELECT COUNT(*) FROM pm_work_batch_retries WHERE operation_id=?', (operation_id,)).fetchone()[0] >= limit:
        raise _bad('PM_BATCH_RETRIES_EXHAUSTED', 'The original child proved-unsent retry budget is exhausted')
    ops.db.execute('INSERT INTO pm_work_batch_retries VALUES(?,?,?,?)', (operation_id, transition, step_name, _now()))


def _state(ctx, key, state, reason=None):
    document = {'state': state, 'reason': reason}
    raw = _json(document)
    with ctx.service.journal.tx():
        row = ctx.service.db.execute('SELECT document FROM pm_work_batch_states WHERE parent_id=? AND entry_key=? ORDER BY sequence DESC LIMIT 1',
            (ctx.operation_id, key)).fetchone()
        if row and row[0] == raw:
            return
        ctx.service.db.execute('INSERT INTO pm_work_batch_states(parent_id,entry_key,document,document_digest,created_at) VALUES(?,?,?,?,?)',
            (ctx.operation_id, key, raw, evidence.digest(document), _now()))
        ctx.service.journal.api_event('pm_work_batch', 'pmbt_' + ctx.operation_id[3:], 'pm.work_batch.entry.observed',
            {'workspace_id': ctx.target['workspace_id'], 'operation_id': ctx.operation_id, 'entry_key': key, **document}, actor=ctx.actor)


def _reserve(ctx):
    def reserve():
        intent = _intent(ctx.op)
        ctx.service.db.execute('INSERT INTO pm_work_batches(batch_id,operation_id,workspace_id,context,context_digest,created_at) VALUES(?,?,?,?,?,?)',
            ('pmbt_' + ctx.operation_id[3:], ctx.operation_id, ctx.target['workspace_id'], _json(intent), evidence.digest(intent), ctx.op['created_at']))
        return {'batch_id': 'pmbt_' + ctx.operation_id[3:]}
    # Preserve the already admitted immutable selection even when it stopped
    # before its first worker turn. This local receipt admits no child/effect.
    return ctx.effect('pm.batch.reserve', reserve, receipt_only=True)


def _admit_child(ctx, entry):
    def admit():
        parent = _op(ctx.service, ctx.operation_id)
        principal = _guard(ctx.service, parent)
        limits = _intent(parent)['limits']
        count = ctx.service.db.execute('SELECT COUNT(*) FROM pm_work_batch_children WHERE parent_id=?', (ctx.operation_id,)).fetchone()[0]
        if count >= limits['max_starts']:
            raise _bad('PM_BATCH_START_BUDGET', 'The original batch start budget is exhausted')
        if _active(ctx.service, principal, parent) >= limits['max_parallel']:
            raise _bad('PM_BATCH_PARALLEL_LIMIT', 'Original child result capacity is still reserved')
        selected = {entry['key']: entry for entry in _intent(parent)['entries']}
        for edge in entry['depends_on']:
            if not _verified(ctx.service, principal, parent, selected[edge['entry_key']], edge['condition']):
                raise _bad('PM_BATCH_DEPENDENCY_EVIDENCE_UNAVAILABLE', 'The original prerequisite result is not verified')
        request = _request(parent, entry)
        child, fresh = ctx.service.create(principal, **request, entry='pm-batch')
        if not fresh:
            raise _bad('PM_BATCH_CHILD_CHANGED', 'A child key already exists without its exact original relation')
        ctx.service.db.execute('INSERT INTO pm_work_batch_children VALUES(?,?,?,?,?,?)',
            (ctx.operation_id, entry['key'], child['operation_id'], _json(request), evidence.digest(request), _now()))
        ctx.service._merge_refs(child['operation_id'], {'pm_work_batch_parent': {'operation_id': ctx.operation_id, 'entry_key': entry['key']}})
        return {'operation_id': child['operation_id']}
    return ctx.effect('pm.batch.child.' + entry['key'], admit)


def _finish(ctx, outcome, reason=None):
    def record():
        result = {'workspace_id': ctx.target['workspace_id'], 'batch_id': 'pmbt_' + ctx.operation_id[3:],
            'outcome': outcome, 'reason': reason, 'semantic_acceptance': 'not_evaluated',
            'existing_sessions': 'continue_under_original_authority'}
        ctx.service.journal.api_event('pm_work_batch', result['batch_id'], 'pm.work_batch.finished',
            {**result, 'operation_id': ctx.operation_id}, actor=ctx.actor)
        return result
    return ctx.effect('pm.batch.finish', record, receipt_only=True)


async def _run(ctx):
    if ctx.service.db.execute("SELECT 1 FROM operation_steps WHERE operation_id=? AND name='pm.batch.finish' AND status='succeeded'", (ctx.operation_id,)).fetchone():
        return _finish(ctx, 'replayed')
    try:
        _reserve(ctx)
        principal = _guard(ctx.service, _op(ctx.service, ctx.operation_id))
    except OperationError as exc:
        return _finish(ctx, 'stopped', exc.code)
    pending, unsettled = False, False
    for entry in _intent(ctx.op)['entries']:
        child = _child(ctx.service, ctx.op, entry)
        if child is None:
            try:
                _admit_child(ctx, entry)
            except OperationError as exc:
                _state(ctx, entry['key'], 'blocked', exc.code)
                pending = True
                continue
            child = _child(ctx.service, ctx.op, entry)
        if child['status'] in {'failed', 'cancelled'} and not _uncertain(ctx.service, child):
            _state(ctx, entry['key'], 'dispatch_failed', child['error_code'] or child['status'])
        elif child['status'] == 'succeeded':
            _state(ctx, entry['key'], 'fixed_revision_verified' if _verified(ctx.service, principal, ctx.op, entry) else 'dispatched_awaiting_result')
        else:
            unsettled = True
            _state(ctx, entry['key'], 'original_dispatch_unsettled', child['error_code'])
    if not pending and not unsettled:
        return _finish(ctx, 'dispatch_settled')
    remaining = _intent(ctx.op)['limits']['deadline_at'] - _now()
    if remaining <= 0:
        return _finish(ctx, 'stopped', 'PM_BATCH_DEADLINE')
    raise Wait('waiting_external', 'Original selected entries are waiting for capacity, fixed prerequisite evidence or child receipts',
        delay_s=min(30, max(.1, remaining)))


def _evidence_input(ops, principal, target, params, pre):
    if set(target) != {'workspace_id', 'batch_id'}:
        raise _bad('INVALID_TARGET', 'Select one exact original batch', 422)
    if set(params) != {'evidence', 'rationale'} or set(pre) != {'expected_version'} or type(pre['expected_version']) is not int:
        raise _bad('INVALID_PARAMS', 'Select immutable evidence with the exact batch version', 422)
    _rationale(params['rationale'])
    if not isinstance(params['evidence'], list) or not 1 <= len(params['evidence']) <= MAX_ENTRIES:
        raise _bad('INVALID_PARAMS', 'Select 1–16 whole evidence references', 422)
    row = _batch(ops, target['workspace_id'], target['batch_id'])
    if ops.db.execute('SELECT COUNT(*) FROM pm_work_batch_evidence WHERE parent_id=?', (row['operation_id'],)).fetchone()[0] + len(params['evidence']) > 64:
        raise _bad('PM_BATCH_EVIDENCE_LIMIT', 'The original batch proof history is full')
    parent = _op(ops, row['operation_id'])
    _member(ops, principal, target['workspace_id'], write=True)
    _access(ops, principal, parent)
    if row['version'] != pre['expected_version']:
        raise _bad('PM_BATCH_VERSION_CONFLICT', 'Batch evidence revision changed')
    entries, seen, results = {entry['key']: entry for entry in _intent(parent)['entries']}, set(), []
    for value in params['evidence']:
        if not isinstance(value, dict) or set(value) != {'entry_key', 'condition', 'reference'} or value['entry_key'] not in entries or value['entry_key'] in seen:
            raise _bad('INVALID_PARAMS', 'Select one distinct original entry per evidence receipt', 422)
        seen.add(value['entry_key'])
        if value['condition'] != entries[value['entry_key']]['result_condition']:
            raise _bad('PM_BATCH_EVIDENCE_CONDITION_CHANGED', 'Evidence must retain the original selected result condition')
        result = evidence.evaluate(ops, principal, target['workspace_id'], _child(ops, parent, entries[value['entry_key']]), value['condition'], value['reference'])
        results.append({'entry_key': value['entry_key'], **result})
    _bounded({'evidence': results})
    return row, results


def _admit_evidence(ops, principal, target, params, pre):
    with ops.journal.tx():
        row, results = _evidence_input(ops, principal, target, params, pre)
    intent = {'batch_id': row['batch_id'], 'parent_operation_id': row['operation_id'], 'version': row['version'], 'evidence': results}
    return {'principal': pm_workspace.principal_binding(principal), 'batch_evidence_intent': intent,
        'batch_evidence_digest': evidence.digest(intent), 'pm_lineage': {'workspace_id': target['workspace_id']}}


async def _record_evidence(ctx):
    def record():
        principal = pm_workspace.admitted_principal(ctx)
        row, results = _evidence_input(ctx.service, principal, ctx.target, ctx.params, ctx.preconditions)
        saved = ctx.admission_binding['batch_evidence_intent']
        if evidence.digest(saved) != ctx.admission_binding['batch_evidence_digest'] or results != saved['evidence']:
            raise _bad('PM_BATCH_EVIDENCE_CHANGED', 'Original selected verifier receipts changed')
        cur = ctx.service.db.execute('UPDATE pm_work_batches SET version=version+1 WHERE batch_id=? AND version=?', (row['batch_id'], row['version']))
        if cur.rowcount != 1:
            raise _bad('PM_BATCH_VERSION_CONFLICT', 'Batch evidence revision changed')
        for result in results:
            ctx.service.db.execute('INSERT INTO pm_work_batch_evidence(parent_id,entry_key,operation_id,version,document,document_digest,created_at) VALUES(?,?,?,?,?,?,?)',
                (row['operation_id'], result['entry_key'], ctx.operation_id, row['version'] + 1, _json(result), evidence.digest(result), _now()))
        ctx.service.journal.api_event('pm_work_batch', row['batch_id'], 'pm.work_batch.evidence.recorded',
            {'workspace_id': row['workspace_id'], 'batch_id': row['batch_id'], 'operation_id': ctx.operation_id, 'version': row['version'] + 1}, actor=ctx.actor)
        return {'workspace_id': row['workspace_id'], 'batch_id': row['batch_id'], 'version': row['version'] + 1, 'evidence': results}
    return ctx.effect('pm.batch.evidence.record', record)


class _Reader:
    def __init__(self, db):
        self.db = db

    def get(self, operation_id, **kwargs):
        return _op(self, operation_id)


def operation_visible(db, principal, operation):
    with snapshots._snapshot(db):
        try:
            ops = _Reader(db)
            op = _op(ops, operation['operation_id'])
            if op['action'] == START:
                _access(ops, principal, op)
            elif op['action'] == RECORD:
                binding = op['external_refs']['admission_binding']
                intent = binding['batch_evidence_intent']
                if evidence.digest(intent) != binding['batch_evidence_digest']:
                    return False
                _access(ops, principal, _op(ops, intent['parent_operation_id']))
                for proof in intent['evidence']:
                    evidence.require_sources(ops, principal, op['target']['workspace_id'], proof)
            else:
                return False
            return True
        except (OperationError, KeyError, ValueError, TypeError):
            return False


def _authorize_existing(ops, principal, op, verb):
    if not operation_visible(ops.db, principal, op):
        raise _bad('NOT_FOUND', 'Original batch sources are unavailable', 404)
    _member(ops, principal, op['target']['workspace_id'], write=True)
    if verb != 'replay' and principal.actor != op['actor']:
        raise _bad('FORBIDDEN', 'Steering requires the original batch actor', 403)


def _view(ops, row):
    parent = _op(ops, row['operation_id'])
    entries = []
    for entry in row['context']['entries']:
        child = _child(ops, parent, entry)
        state = ops.db.execute('SELECT document FROM pm_work_batch_states WHERE parent_id=? AND entry_key=? ORDER BY sequence DESC LIMIT 1',
            (parent['operation_id'], entry['key'])).fetchone()
        entries.append({'key': entry['key'], 'package_ref': entry['handoff']['params']['pm_package_ref'],
            'work_item_id': entry['handoff']['params']['work_item_id'], 'depends_on': entry['depends_on'],
            'result_condition': entry['result_condition'],
            'source_sha': entry['handoff']['params']['source_sha'],
            'operation_id': child['operation_id'] if child else None, 'operation_status': child['status'] if child else None,
            **(json.loads(state[0]) if state else {'state': 'selected', 'reason': None}),
            'verification_evidence': _proofs(ops, parent, entry['key'])})
    return {key: row[key] for key in ('sequence', 'batch_id', 'operation_id', 'workspace_id', 'created_at', 'version')} | {
        'operation_status': parent['status'], 'limits': row['context']['limits'], 'entries': entries,
        'result': parent['result'], 'requirement_acceptance': 'not_evaluated', 'model_provider_cost': 'unmeasured'}


@snapshots._snapshot_read
def get_batch(ops, principal, workspace_id, batch_id):
    _member(ops, principal, workspace_id)
    row = _batch(ops, workspace_id, batch_id)
    _access(ops, principal, _op(ops, row['operation_id']))
    return _bounded({'workspace_id': workspace_id, 'batch': _view(ops, row)})


@snapshots._snapshot_read
def list_batches(ops, principal, workspace_id, *, after=0, limit=50):
    _member(ops, principal, workspace_id)
    if type(after) is not int or not 0 <= after < 2**63 or type(limit) is not int or not 1 <= limit <= 100:
        raise _bad('INVALID_PARAMS', 'Use a signed 64-bit cursor and limit 1–100', 422)
    result = {'workspace_id': workspace_id, 'batches': [], 'next_cursor': None, 'visibility_filtered': False}
    rows = ops.db.execute('SELECT batch_id,sequence FROM pm_work_batches WHERE workspace_id=? AND sequence>? ORDER BY sequence LIMIT ?',
        (workspace_id, after, limit + 1)).fetchall()
    scanned = after
    for stub in rows[:limit]:
        row = _batch(ops, workspace_id, stub['batch_id'])
        if not operation_visible(ops.db, principal, {'operation_id': row['operation_id']}):
            result['visibility_filtered'] = True
            scanned = row['sequence']
            continue
        item = _view(ops, row)
        candidate = {**result, 'batches': [*result['batches'], item], 'next_cursor': 2**63 - 1}
        if _size(candidate) > MAX_READ_BYTES:
            if scanned == after:
                raise _bad('PM_BATCH_READ_TOO_LARGE', 'One whole batch exceeds the public read budget', 413)
            result['next_cursor'] = scanned
            return _bounded(result)
        result['batches'].append(item)
        scanned = row['sequence']
    result['next_cursor'] = scanned if len(rows) > limit else None
    return _bounded(result)
