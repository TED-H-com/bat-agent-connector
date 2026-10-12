"""Private original batch-turn reader on the existing operation journal.

No completion is inferred from dispatch, verifier or quality status. First-send
origin and contiguous native replay are separate immutable evidence.
"""
from __future__ import annotations

import asyncio
import json
import time
from contextvars import ContextVar

from . import api_auth, confinement, controller_authority, pm_runtime, pm_workspace
from . import pm_maintenance_repair_runtime as native
from . import pm_work_batch_evidence as evidence
from . import pm_work_batches as batches
from .operations import ActionDef, NeedsAttention, OperationError, Wait

ACTION = 'pm.execution.runtime.observe'
ORIGIN = 'pm.execution.runtime.origin'
COMPLETE = 'pm.execution.runtime.complete'
PREFIX = 'pm.execution.runtime.page.'
_ADMITTING = ContextVar('pm_execution_runtime_admission', default=None)


def _bad(message, code='PM_EXECUTION_RUNTIME_UNPROVEN', status=409):
    return OperationError(code, message, status)



def public_receipt(document):
    """Project original private receipt bytes explicitly, including nested shapes."""
    common = ('schema', 'workspace_id', 'observation_operation_id', 'host', 'session_id',
        'origin_digest', 'sdk_session_id', 'native_turn_id', 'event_digest', 'event_count',
        'runtime_status', 'process_death_proven', 'recorded_at')
    profiles = {
        'pm_execution_runtime_completion_v1': ('execution_operation_id', 'registry_incarnation', 'result_digest',
            'candidate_commit', 'tree_hash'),
        'pm_manual_single_input_quiescence_v1': ('work_item_id', 'link_id', 'ownership', 'work_acceptance'),
    }
    if document.get('schema') not in profiles:
        raise _bad('Unsupported original runtime receipt schema')
    projected = {key: document[key] for key in (*common, *profiles[document['schema']])}
    counters = document.get('input_state')
    if not isinstance(counters, dict):
        raise _bad('Original native counter shape unavailable')
    projected['input_state'] = native.input_state({
        'inputState': {key: counters.get(key) for key in ('schema', 'epoch', 'received', 'pending')}})
    if projected['input_state'] is None:
        raise _bad('Original native counters unavailable')
    projected['completion_cursor'] = pm_runtime._cursor(document.get('completion_cursor'))
    for key, value in projected.items():
        if key not in {'input_state', 'completion_cursor'} and isinstance(value, (dict, list)):
            raise _bad('Original runtime scalar shape changed')
    return {**projected, 'document_digest': evidence.digest(document)}


def summary(ops, principal, child):
    if origin(ops, child) is None:
        return None
    document = proof(ops, principal, child)
    if document is None:
        return None
    return {key: document[key] for key in ('observation_operation_id', 'execution_operation_id', 'document_digest',
        'recorded_at', 'runtime_status', 'process_death_proven')}

def install(ops):
    if ACTION not in ops.actions:
        ops.register(ActionDef(ACTION, 'observe', 'Read the original selected batch turn without sending input',
            _run, _admit, ('workspace_id', 'execution_operation_id'), authorize_existing=_authorize_existing))


def relation(ops, child):
    claim = (child.get('external_refs') or {}).get('pm_work_batch_parent')
    if not batches.installed(ops.db):
        if claim is not None:
            raise _bad('Unproven original batch relation')
        return None
    row = ops.db.execute('SELECT parent_id,entry_key FROM pm_work_batch_children WHERE operation_id=?',
        (child['operation_id'],)).fetchone()
    if row is None and claim is None:
        return None
    if not row or claim != {'operation_id': row['parent_id'], 'entry_key': row['entry_key']}:
        raise _bad('Unproven original batch relation')
    parent = batches._op(ops, row['parent_id'])
    entry = next((item for item in batches._intent(parent)['entries'] if item['key'] == row['entry_key']), None)
    if entry is None or batches._child(ops, parent, entry)['operation_id'] != child['operation_id']:
        raise _bad('Original fixed child changed')
    return parent, entry


def _saved(ops, oid, name):
    row = ops.db.execute("SELECT response FROM operation_steps WHERE operation_id=? AND name=? AND status='succeeded'", (oid, name)).fetchone()
    if row is None:
        return None
    response = json.loads(row[0])
    document = response.get('private_document')
    if not isinstance(document, dict) or evidence.digest(document) != response.get('document_digest'):
        raise _bad('Original private runtime receipt changed')
    return document


def _store(ctx, name, document):
    # The unknown wrapper is deliberately absent from public event-summary keys.
    # Operation reads never return step response bytes.
    return ctx.effect(name, lambda: {'private_document': document, 'document_digest': evidence.digest(document)})


def origin(ops, child):
    document = _saved(ops, child['operation_id'], ORIGIN)
    if document is not None:
        bound = relation(ops, child)
        if (bound is None or document['execution_operation_id'] != child['operation_id']
                or document['parent_operation_id'] != bound[0]['operation_id'] or document['entry_key'] != bound[1]['key']):
            raise _bad('Original first-send batch source changed')
    return document


def _access(ops, principal, child):
    from .pm_access import require_visible
    ops = batches._Reader(ops.db)
    bound = relation(ops, child)
    if not bound or not principal.allows('observe'):
        raise _bad('Original batch source unavailable', 'NOT_FOUND', 404)
    batches._access(ops, principal, bound[0])
    require_visible(ops.db, principal, child)


def _live(ops, child):
    parent, _ = relation(ops, child)
    original = parent['external_refs']['admission_binding']['principal']
    if original['actor'] != child['actor'] or not (original['admin'] or 'observe' in original['scopes']):
        raise _bad('Original observe delegation unavailable', 'FORBIDDEN', 403)
    now = time.time()
    rows = ops.db.execute('''SELECT token_hash,scopes FROM api_principals WHERE actor=? AND revoked_at IS NULL
        AND created_at<=? AND (expires_at IS NULL OR expires_at>?) ORDER BY created_at DESC LIMIT 64''',
        (child['actor'], now, now)).fetchall()
    live = next((api_auth.Principal(child['actor'], frozenset({'observe'}), credential_id=row['token_hash'])
        for row in rows if 'observe' in json.loads(row['scopes'])), None)
    if live is None and original['admin']:
        daemon = ops.context.get('daemon')
        token = vars(daemon).get('_admin_token') if daemon else None
        if isinstance(token, str) and token:
            candidate = api_auth.authenticate(ops.db, token, token)
            if candidate and candidate.actor == child['actor']:
                live = candidate
    if live is None or not controller_authority.same_controller(controller_authority.binding(ops.db, live),
            controller_authority.operation_binding(child)):
        raise _bad('Original controller lacks current observe authority', 'FORBIDDEN', 403)
    _access(ops, live, child)
    return live


def _registry(ops, child, doc):
    entry = native._entry(ops, child, doc['session_id'])
    if (entry['controller_incarnation'] != doc['registry_incarnation'] or entry['created_at'] != doc['registry_created_at']
            or native._host_binding(ops, doc['host']) != doc['host_binding_digest']
            or entry['confinement']['options'] != doc['confinement']['options']
            or (entry['confinement'].get('evidence') or {}).get('agent') != doc['confinement']['evidence']['agent']):
        raise _bad('Original host, session incarnation or confinement changed')


async def capture(ctx, sid, text):
    child = batches._op(ctx.service, ctx.operation_id)
    bound = relation(ctx.service, child)
    if bound is None:
        return
    parent, selected = bound
    deadline = batches.require_child(ctx.service, child)
    entry = native._entry(ctx.service, child, sid)
    agent = entry['confinement']['evidence']['agent']
    document = {'schema': 'pm_execution_native_turn_v1', 'workspace_id': parent['target']['workspace_id'],
        'parent_operation_id': parent['operation_id'], 'entry_key': selected['key'],
        'execution_operation_id': child['operation_id'], 'host': child['target']['host'], 'session_id': sid,
        'host_binding_digest': native._host_binding(ctx.service, child['target']['host']),
        'registry_incarnation': entry['controller_incarnation'], 'registry_created_at': entry['created_at'],
        'controller': controller_authority.operation_binding(child), 'confinement': entry['confinement'],
        'prompt_digest': pm_runtime.prompt_digest(text),
        'native_prompt_digest': pm_runtime.prompt_digest(native.native_prompt_text(text, agent)),
        'prompt_normalization': 'rust_codex_trim_v1' if agent == 'codex' else 'exact_text_v1',
        'status': 'unavailable', 'reason': 'native_replay_unavailable', 'captured_at': time.time()}
    client = ctx.service.context['fleet'].client(document['host'])
    try:
        replay = await asyncio.wait_for(pm_runtime.sync_session(client, sid), min(10, max(0.001, deadline - time.time())))
        if not getattr(client, 'auth_info', None):
            raise _bad('Original native transport was not authenticated')
        meta = (replay.get('state') or {}).get('meta', {})
        cursor = pm_runtime.fresh_owned_send_cursor(replay, meta.get('sdkSessionId'), document['confinement'])
        document.update(status='captured', reason=None, send_cursor=cursor, sdk_session_id=meta['sdkSessionId'],
            input_state=native.input_state(meta))
        if document['input_state'] is not None and not native.same_original_input(document, document['input_state'], fresh=True):
            raise NeedsAttention('PM_EXECUTION_RUNTIME_INPUT_NOT_FRESH', 'Original session has pre-existing native input')
    except (pm_runtime.RuntimeEvidenceError, confinement.ConfinementRefused) as exc:
        if getattr(exc, 'code', None) in {'PM_SESSION_NOT_FRESH', 'CONFINEMENT_MISMATCH'}:
            raise NeedsAttention(exc.code, 'The original coding session is not fresh and confined') from exc
        document['reason'] = getattr(exc, 'code', 'native_replay_unavailable')
    except (OperationError, NeedsAttention):
        raise
    except Exception:  # noqa: BLE001 - unavailable read capability never proves completion
        document['reason'] = 'native_replay_unavailable'
    batches.require_child(ctx.service, child)
    _registry(ctx.service, child, document)
    _store(ctx, ORIGIN, document)
    ensure_observer(ctx.service, child)


async def before_send_frame(ctx):
    child = batches._op(ctx.service, ctx.operation_id)
    if relation(ctx.service, child) is None:
        return
    document = origin(ctx.service, child)
    if document is None or document['status'] != 'captured':
        return
    deadline = batches.require_child(ctx.service, child)
    _registry(ctx.service, child, document)
    client = ctx.service.context['fleet'].client(document['host'])
    meta = await asyncio.wait_for(client.guard_read('claude:get-session-meta', {'sessionId': document['session_id']}),
        min(10, max(0.001, deadline - time.time())))
    native._meta(document, meta, fresh=True)
    batches.require_child(ctx.service, child)
    raw = await asyncio.wait_for(client.guard_read('claude:sync-session',
        {'sessionId': document['session_id'], 'cursor': document['send_cursor']}), min(10, max(0.001, deadline - time.time())))
    replay = pm_runtime.parse_replay(raw, document['session_id'], document['send_cursor'])
    if replay['gap'] or replay['events'] or replay['has_more'] or replay['cursor'] != document['send_cursor']:
        raise _bad('Native replay changed before the original unsent input')
    batches.require_child(ctx.service, child)
    _registry(ctx.service, child, document)


def _request(child, doc):
    return {'action': ACTION, 'target': {'workspace_id': doc['workspace_id'], 'execution_operation_id': child['operation_id']},
        'params': {}, 'preconditions': {'runtime_origin_digest': evidence.digest(doc)},
        'idempotency_key': 'pm-execution-runtime:' + child['operation_id']}


def _admit(ops, principal, target, params, pre):
    child = batches._op(ops, target.get('execution_operation_id'))
    document = origin(ops, child)
    if document is None or document['status'] != 'captured' or _ADMITTING.get() != (id(ops.db), child['operation_id']):
        raise _bad('Only the original service-created observer may be admitted')
    request = _request(child, document)
    if target != request['target'] or params or pre != request['preconditions']:
        raise _bad('Original observer request changed')
    _access(ops, principal, child)
    if principal.actor != _live(ops, child).actor:
        raise _bad('Original controller required', 'FORBIDDEN', 403)
    return {'principal': pm_workspace.principal_binding(principal),
        'pm_execution_runtime': {'execution_operation_id': child['operation_id'], 'origin_digest': evidence.digest(document),
            'operation_ids': [document['parent_operation_id'], child['operation_id']]},
        'pm_lineage': {'workspace_id': document['workspace_id']}}


def observer(ops, child):
    rows = ops.db.execute("SELECT * FROM operations WHERE action=? AND json_extract(target,'$.execution_operation_id')=? AND actor=? LIMIT 2",
        (ACTION, child['operation_id'], child['actor'])).fetchall()
    if not rows:
        return None
    if len(rows) != 1:
        raise _bad('Ambiguous original runtime reader')
    operation = batches.OperationService._decode(rows[0])
    document = origin(ops, child)
    binding = operation['external_refs'].get('admission_binding', {}).get('pm_execution_runtime', {})
    if (document is None or any(operation.get(key) != value for key, value in _request(child, document).items())
            or binding.get('execution_operation_id') != child['operation_id'] or binding.get('origin_digest') != evidence.digest(document)
            or not controller_authority.same_controller(controller_authority.operation_binding(operation), controller_authority.operation_binding(child))):
        raise _bad('Original service-created reader relation changed')
    return operation


def ensure_observer(ops, child):
    document = origin(ops, child)
    if document is None or document['status'] != 'captured' or observer(ops, child) is not None:
        return
    token = _ADMITTING.set((id(ops.db), child['operation_id']))
    try:
        _operation, fresh = ops.create(_live(ops, child), **_request(child, document), entry='pm-execution-runtime')
        if not fresh:
            raise _bad('Original runtime reader key was already reserved')
    finally:
        _ADMITTING.reset(token)


def _original(ops, op):
    child = batches._op(ops, op['target']['execution_operation_id'])
    reader = observer(ops, child)
    if reader is None or reader['operation_id'] != op['operation_id']:
        raise _bad('Original runtime reader unavailable')
    return child


def operation_visible(db, principal, original):
    try:
        ops = batches._Reader(db)
        child = _original(ops, original)
        _access(ops, principal, child)
        selected = _saved(ops, original['operation_id'], 'pm.execution.runtime.result')
        if selected is not None:
            evidence.require_sources(ops, principal, original['target']['workspace_id'], selected)
        return True
    except (OperationError, KeyError, ValueError, TypeError):
        return False


def _authorize_existing(ops, principal, op, _verb):
    child = _original(ops, op)
    _access(ops, principal, child)
    if not controller_authority.same_controller(controller_authority.binding(ops.db, principal), controller_authority.operation_binding(child)):
        raise _bad('Original controller required', 'FORBIDDEN', 403)


def _pages(ops, oid, doc):
    rows = ops.db.execute("SELECT name FROM operation_steps WHERE operation_id=? AND name LIKE ? AND status='succeeded' ORDER BY seq LIMIT ?",
        (oid, PREFIX + '%', native.MAX_PAGES + 1)).fetchall()
    cursor, events, size = doc['send_cursor'], [], 0
    for index, row in enumerate(rows):
        page = _saved(ops, oid, row['name'])
        if row['name'] != PREFIX + str(index) or page['previous_cursor'] != cursor or page['gap']:
            raise _bad('Original native replay contains a permanent gap')
        cursor = page['cursor']
        events.extend(page['events'])
        size += len(batches._json(page).encode())
    if len(rows) > native.MAX_PAGES or len(events) > native.MAX_EVENTS or size > native.MAX_BYTES:
        raise _bad('Original native replay bound exceeded')
    return len(rows), cursor, events, size


def proof(ops, principal, child):
    ops = batches._Reader(ops.db)
    _access(ops, principal, child)
    reader = observer(ops, child)
    if reader is None:
        return None
    document = _saved(ops, reader['operation_id'], COMPLETE)
    if document is not None:
        original = origin(ops, child)
        selected = _saved(ops, reader['operation_id'], 'pm.execution.runtime.result')
        if selected is None:
            raise _bad('Original selected result receipt is unavailable')
        evidence.require_sources(ops, principal, original['workspace_id'], selected)
        if (document['origin_digest'] != evidence.digest(original) or document['result_digest'] != evidence.digest(selected)
                or document['execution_operation_id'] != child['operation_id'] or document['observation_operation_id'] != reader['operation_id']):
            raise _bad('Original completion source changed')
        return public_receipt(document)
    return None


def _settled(ops, child, reader_id):
    if child['status'] != 'succeeded' or batches._uncertain(ops, child):
        return False
    rows = ops.db.execute("SELECT DISTINCT resource_id FROM pm_source_lineage WHERE resource_type='operation' AND original_operation_id=? LIMIT 257",
        (child['operation_id'],)).fetchall()
    if len(rows) > 256:
        return False
    for row in rows:
        if row[0] in {child['operation_id'], reader_id}:
            continue
        op = batches._op(ops, row[0])
        if op['action'] == ACTION:
            continue  # Other private readers cannot create unsettled coding effects.
        if op['status'] not in batches.TERMINAL or batches._uncertain(ops, op):
            return False
    return True


async def _run(ctx):
    try:
        return await _observe(ctx)
    except (OperationError, confinement.ConfinementRefused) as exc:
        raise Wait('waiting_external', getattr(exc, 'code', 'PM_EXECUTION_RUNTIME_UNAVAILABLE'), 15) from exc


async def _observe(ctx):
    ops, child = ctx.service, _original(ctx.service, ctx.op)
    # Original completed effect receipt is returned before new network authority.
    completed = _saved(ops, ctx.operation_id, COMPLETE)
    if completed is not None:
        return public_receipt(completed)
    principal = _live(ops, child)
    document = origin(ops, child)
    _registry(ops, child, document)
    count, cursor, events, size = _pages(ops, ctx.operation_id, document)
    client = ops.context['fleet'].client(document['host'])
    try:
        replay = await asyncio.wait_for(pm_runtime.sync_session(client, document['session_id'], cursor), 15)
    except pm_runtime.RuntimeEvidenceError as exc:
        raise NeedsAttention(exc.code, 'Original native replay cannot prove completion') from exc
    except Exception as exc:  # noqa: BLE001 - only the original read is retried
        raise Wait('waiting_external', 'Original native replay unavailable', 15) from exc
    principal = _live(ops, child)
    _registry(ops, child, document)
    if replay['gap'] or replay['events']:
        page = {**replay, 'previous_cursor': cursor}
        if count >= native.MAX_PAGES or size + len(batches._json(page).encode()) > native.MAX_BYTES or len(events) + len(replay['events']) > native.MAX_EVENTS:
            raise NeedsAttention('PM_EXECUTION_RUNTIME_LIMIT', 'Original replay bound exceeded')
        _store(ctx, PREFIX + str(count), page)
        if replay['gap']:
            raise NeedsAttention('PM_EXECUTION_RUNTIME_GAP', 'Original replay has a permanent gap')
        events += replay['events']
    assessed = pm_runtime.assess_owned_turn(document['session_id'], document['send_cursor'], events, replay['cursor'],
        document['sdk_session_id'], original_confinement=document['confinement'], has_more=replay['has_more'],
        expected_prompt_digest=document['native_prompt_digest'])
    if assessed['reason'] == 'original_input_not_observed' and not any(
            event['channel'] == 'claude:turn-end' or event['channel'] == 'claude:message'
            and event['params'].get('message', {}).get('role') == 'user' for event in events):
        raise Wait('waiting_external', 'Original input is not yet present', 5)
    if assessed['status'] in {'unknown', 'incomplete'}:
        raise NeedsAttention('PM_EXECUTION_RUNTIME_INCOMPLETE', assessed['reason'])
    child = batches._op(ops, child['operation_id'])
    parent, entry = relation(ops, child)
    if not assessed['complete'] or not _settled(ops, child, ctx.operation_id):
        raise Wait('waiting_external', 'Awaiting original native completion and settled original effects', 5)
    selected = _saved(ops, ctx.operation_id, 'pm.execution.runtime.result')
    if selected is None:
        selected = batches._verified_proof(ops, principal, parent, entry)
        if not selected:
            raise Wait('waiting_external', 'Awaiting explicitly recorded original batch result evidence', 5)
        _store(ctx, 'pm.execution.runtime.result', selected)
    current = evidence.evaluate(ops, principal, document['workspace_id'], child, selected['condition'], selected['reference'])
    if current['status'] != 'passed' or current['receipt_digest'] != selected['receipt_digest']:
        raise _bad('The original selected result evidence no longer qualifies')
    from . import pm_execution_verification
    candidate = await pm_execution_verification.configuration(ops, principal, document['workspace_id'], child['operation_id'])
    if (not candidate.get('available') or candidate.get('candidate_commit') != selected['candidate_commit']
            or not evidence.SHA.fullmatch(str(candidate.get('tree_hash', '')))
            or selected.get('tree_hash', candidate['tree_hash']) != candidate['tree_hash']):
        raise NeedsAttention('PM_EXECUTION_RUNTIME_CANDIDATE_CHANGED', 'The original clean candidate changed')
    _live(ops, child)
    _registry(ops, child, document)
    final = await asyncio.wait_for(pm_runtime.sync_session(client, document['session_id'], replay['cursor']), 15)
    if final['gap'] or final['events'] or final['has_more']:
        raise Wait('waiting_external', 'Read all events after candidate verification', 1)
    meta = await asyncio.wait_for(client.invoke('claude:get-session-meta', {'sessionId': document['session_id']}), 15)
    native._meta(document, meta)
    with ops.journal.tx():
        ctx.check_cancel()
        principal = _live(ops, child)
        _registry(ops, child, document)
        checked = evidence.evaluate(ops, principal, document['workspace_id'], child, selected['condition'], selected['reference'])
        if checked['status'] != 'passed' or checked['receipt_digest'] != selected['receipt_digest'] or not _settled(ops, child, ctx.operation_id):
            raise _bad('Original result changed before completion recording')
        result = {'schema': 'pm_execution_runtime_completion_v1', 'workspace_id': document['workspace_id'],
            'execution_operation_id': child['operation_id'], 'observation_operation_id': ctx.operation_id,
            'host': document['host'], 'session_id': document['session_id'], 'registry_incarnation': document['registry_incarnation'],
            'origin_digest': evidence.digest(document), 'result_digest': evidence.digest(selected),
            'sdk_session_id': document['sdk_session_id'], 'input_state': native.input_state(meta),
            'native_turn_id': assessed['native_turn_id'], 'completion_cursor': replay['cursor'],
            'event_digest': evidence.digest(events), 'event_count': len(events),
            'candidate_commit': candidate['candidate_commit'], 'tree_hash': candidate['tree_hash'],
            'runtime_status': 'original_turn_completed', 'process_death_proven': False, 'recorded_at': time.time()}
        _store(ctx, COMPLETE, result)
    return public_receipt(result)
