"""Explicit read-only evidence for one linked manual session's current input.

This never creates ownership, clears a registry reservation or accepts work.
Single-input receipts remain unchanged; a separate profile proves serial inputs.
"""
from __future__ import annotations

import asyncio
import json
import time

from . import api_auth, controller_authority, pm_evidence, pm_runtime, pm_workspace, registry
from . import pm_execution_runtime as saved
from . import pm_maintenance_repair_runtime as native
from . import pm_work_batch_evidence as evidence
from .operations import ActionDef, NeedsAttention, OperationError, Wait

ACTION = 'pm.session.quiescence.observe'
ORIGIN = 'pm.session.quiescence.origin'
COMPLETE = 'pm.session.quiescence.complete'
SERIAL_ORIGIN = 'pm_manual_serial_inputs_v1'
SERIAL_RECEIPT = 'pm_manual_serial_inputs_quiescence_v1'
MAX_SERIAL_INPUTS = 64


def _bad(message, code='PM_SESSION_QUIESCENCE_UNPROVEN', status=409):
    return OperationError(code, message, status)


def install(ops):
    if ACTION not in ops.actions:
        ops.register(ActionDef(ACTION, 'observe', 'Observe bounded explicitly linked manual inputs without changing the session',
            _run, _admit, ('workspace_id', 'work_item_id'), authorize_existing=_authorize_existing))


def _link(ops, wid, work, ident):
    row = ops.db.execute('SELECT * FROM work_item_links WHERE link_id=? AND work_item_id=? AND kind=\'session\' AND removed_at IS NULL',
        (ident, work)).fetchone()
    item = ops.db.execute('SELECT project_id FROM work_items WHERE work_item_id=?', (work,)).fetchone()
    project = ops.db.execute('SELECT * FROM pm_project_bindings WHERE workspace_id=? AND project_id=?',
        (wid, item['project_id'] if item else '')).fetchone()
    if row is None or not project or not row['link_operation']:
        raise _bad('Original explicit work/session link unavailable', 'NOT_FOUND', 404)
    return dict(row), dict(project)


def _access(ops, principal, op):
    from .pm_access import require_visible
    wid = op['target']['workspace_id']
    if not principal.allows('observe'):
        raise _bad('Current observe authority required', 'FORBIDDEN', 403)
    pm_workspace.require_member(ops.db, principal, wid)
    intent = op['external_refs']['admission_binding']['pm_session_quiescence']
    link, project = _link(ops, wid, op['target']['work_item_id'], intent['link']['link_id'])
    if link != intent['link'] or project != intent['project']:
        raise _bad('Original linked source changed', 'NOT_FOUND', 404)
    for oid in intent['operation_ids']:
        require_visible(ops.db, principal, ops.get(oid))
    if not pm_evidence._session_visible(ops, principal, wid, link['ref']):
        raise _bad('Original session source unavailable', 'NOT_FOUND', 404)
    return intent



def identity_digest(ops, host, sid, data):
    return evidence.digest({'host': host, 'session_id': sid, 'host_binding_digest': native._host_binding(ops, host),
        'workspace_id': data.get('workspace_id'), 'profile_id': data.get('profile_id'),
        'sdk_session_id': data.get('sdk_session_id'), 'native_input_state': data.get('native_input_state')})

def _admit(ops, principal, target, params, pre):
    if (set(target) != {'workspace_id', 'work_item_id'} or set(params) != {'link_id'}
            or type(params['link_id']) is not int or params['link_id'] <= 0
            or set(pre) != {'expected_link_operation_id', 'expected_project_binding_id', 'expected_session_identity_digest'}):
        raise _bad('Select an exact original work/session link and project binding', 'INVALID_PARAMS', 422)
    if not principal.allows('observe'):
        raise _bad('Current observe authority required', 'FORBIDDEN', 403)
    pm_workspace.require_member(ops.db, principal, target['workspace_id'], write=True)
    link, project = _link(ops, target['workspace_id'], target['work_item_id'], params['link_id'])
    if (pre['expected_link_operation_id'] != link['link_operation']
            or pre['expected_project_binding_id'] != project['binding_id']):
        raise _bad('Original work/session selection changed')
    host, separator, sid = link['ref'].partition('/')
    session = ops.db.execute('SELECT body FROM sessions_observed WHERE host=? AND session_id=?', (host, sid)).fetchone()
    data = json.loads(session[0]) if session else {}
    if (not separator or not sid or data.get('provenance') != 'manual' or not data.get('workspace_id')
            or not data.get('profile_id') or registry.get(host, sid)):
        raise _bad('A positively observed manual session is required')
    counter = native.input_state({'inputState': data.get('native_input_state')})
    if not pm_runtime._text(data.get('sdk_session_id')) or counter is None:
        raise _bad('Select an authenticated native identity from the original inventory')
    if pre['expected_session_identity_digest'] != identity_digest(ops, host, sid, data):
        raise _bad('Original observed native identity changed')
    intent = {'link': link, 'project': project, 'host': host, 'session_id': sid,
        'sdk_session_id': data['sdk_session_id'], 'input_state': counter,
        'bat_workspace_id': data['workspace_id'], 'profile_id': data['profile_id'],
        'host_binding_digest': native._host_binding(ops, host),
        'operation_ids': [link['link_operation'], project['operation_id']]}
    if 2 <= counter['received'] <= MAX_SERIAL_INPUTS:
        # A pre-upgrade pending operation never acquires a new proof profile.
        intent['replay_profile'] = SERIAL_ORIGIN
    bound = {'principal': pm_workspace.principal_binding(principal), 'pm_session_quiescence': intent,
        'pm_lineage': {'workspace_id': target['workspace_id']}}
    _access(ops, principal, {'target': target, 'external_refs': {'admission_binding': bound}})
    return bound



def sources(ops, principal, wid, work):
    """Bounded exact original link selectors for the existing Kanban detail."""
    pm_workspace.require_member(ops.db, principal, wid)
    rows = ops.db.execute("SELECT * FROM work_item_links WHERE work_item_id=? AND kind='session' AND removed_at IS NULL ORDER BY link_id LIMIT 65",
        (work,)).fetchall()
    if len(rows) > 64:
        return []  # Never advertise a misleading partial original selection.
    result = []
    for row in rows:
        host, separator, sid = row['ref'].partition('/')
        if not separator or not pm_evidence._session_visible(ops, principal, wid, row['ref']):
            continue
        observed = ops.db.execute('SELECT body FROM sessions_observed WHERE host=? AND session_id=?', (host, sid)).fetchone()
        data = json.loads(observed[0]) if observed else {}
        if data.get('provenance') != 'manual' or registry.get(host, sid):
            continue
        try:
            link, project = _link(ops, wid, work, row['link_id'])
            from .pm_access import require_visible
            for oid in (link['link_operation'], project['operation_id']):
                require_visible(ops.db, principal, ops.get(oid))
        except OperationError:
            continue
        counter = native.input_state({'inputState': data.get('native_input_state')})
        reason = ('manual_native_identity_unavailable' if counter is None or not pm_runtime._text(data.get('sdk_session_id'))
            or data.get('fields_stale') or data.get('specific_stale_reason') else
            'manual_multiple_inputs_unsupported' if not 1 <= counter['received'] <= MAX_SERIAL_INPUTS else
            'manual_native_turn_active' if counter['pending'] or data.get('streaming') is not False or data.get('pending') else None)
        result.append({'link_id': link['link_id'], 'host': host, 'session_id': sid,
            'expected_link_operation_id': link['link_operation'], 'expected_project_binding_id': project['binding_id'],
            'expected_session_identity_digest': identity_digest(ops, host, sid, data),
            'available': reason is None, 'reason': reason})
    return result

def _live(ops, op):
    original = op['external_refs']['admission_binding']['principal']
    now = time.time()
    rows = ops.db.execute('''SELECT token_hash,scopes FROM api_principals WHERE actor=? AND revoked_at IS NULL
        AND created_at<=? AND (expires_at IS NULL OR expires_at>?) ORDER BY created_at DESC LIMIT 64''',
        (op['actor'], now, now)).fetchall()
    live = next((api_auth.Principal(op['actor'], frozenset({'observe'}), credential_id=row['token_hash'])
        for row in rows if 'observe' in json.loads(row['scopes'])), None)
    if live is None and original['admin']:
        daemon = ops.context.get('daemon')
        token = vars(daemon).get('_admin_token') if daemon else None
        if isinstance(token, str) and token:
            value = api_auth.authenticate(ops.db, token, token)
            if value and value.actor == op['actor']:
                live = value
    if (original['actor'] != op['actor'] or not (original['admin'] or 'observe' in original['scopes'])
            or live is None or not controller_authority.same_controller(controller_authority.binding(ops.db, live),
                controller_authority.operation_binding(op))):
        raise _bad('Original controller lacks current read authority', 'FORBIDDEN', 403)
    intent = _access(ops, live, op)
    pm_workspace.require_member(ops.db, live, op['target']['workspace_id'], write=True)
    if registry.get(intent['host'], intent['session_id']) or native._host_binding(ops, intent['host']) != intent['host_binding_digest']:
        raise _bad('Original manual source identity changed')
    return live, intent



async def _scope(ops, op, client, intent):
    """Read the original manual tab relation, without claiming tab ownership."""
    from .service import _workspace
    _live(ops, op)
    catalogue = await asyncio.wait_for(_workspace(client), 15)
    if not isinstance(catalogue, dict) or not isinstance(catalogue.get('terminals'), list):
        raise _bad('Original manual tab scope is unavailable')
    matches = [row for row in catalogue['terminals'] if isinstance(row, dict) and row.get('id') == intent['session_id']]
    if (len(matches) != 1 or matches[0].get('workspaceId') != intent['bat_workspace_id']
            or ops.context['fleet'].config.host(intent['host']).profile_id != intent['profile_id']
            or not getattr(client, 'auth_info', None)):
        raise _bad('Original manual session moved outside its fixed scope')
    _live(ops, op)

def operation_visible(db, principal, operation):
    from .pm_workload import _Reader
    try:
        ops = _Reader(db)
        original = ops.get(operation['operation_id'])
        _access(ops, principal, original)
        return True
    except (OperationError, ValueError, TypeError, KeyError):
        return False


def _authorize_existing(ops, principal, op, _verb):
    _access(ops, principal, op)
    if not controller_authority.same_controller(controller_authority.binding(ops.db, principal), controller_authority.operation_binding(op)):
        raise _bad('Original observer controller required', 'FORBIDDEN', 403)


def _meta(document, meta):
    value = native.input_state(meta)
    if (not isinstance(meta, dict) or meta.get('sdkSessionId') != document['sdk_session_id']
            or meta.get('isStreaming') is not False or value != document['input_state']):
        raise _bad('Original current native input changed')
    if document['schema'] == SERIAL_ORIGIN and (meta.get('pendingPermission') or meta.get('pendingAskUser')):
        raise _bad('Original manual interaction is still pending')


def assess_serial_inputs(original, events, cursor, *, has_more=False):
    """Strict complete-history proof, never a last-turn or count-only heuristic.

    Counter entries include refused/queued/injected calls. Only native evidence
    of one serial user input per unique completed turn accounts for an entry.
    Ordinary provider echoes have no input-to-turn ID, so overlapping inputs or
    absent per-turn authenticated counter status cannot be paired here.
    """
    def unknown(reason):
        return {'complete': False, 'reason': reason}

    counter = native.input_state({'inputState': original.get('input_state')})
    if (original.get('schema') != SERIAL_ORIGIN or counter is None or counter['pending']
            or not 2 <= counter['received'] <= MAX_SERIAL_INPUTS):
        return unknown('serial_input_profile_unavailable')
    try:
        start, end = pm_runtime._cursor(original['send_cursor']), pm_runtime._cursor(cursor)
        if start['seq'] != 0 or start['epoch'] != end['epoch']:
            return unknown('serial_replay_origin_changed')
        items = pm_runtime._events(events, original['session_id'], start['epoch'], 0)
        if end['seq'] != (items[-1]['seq'] if items else 0):
            return unknown('serial_replay_cursor_gap')
    except pm_runtime.RuntimeEvidenceError as exc:
        return unknown(exc.code)
    turns, seen_turns, seen_users = [], set(), set()
    active, status_seen, segment_start, segment_cursor = None, False, 0, start
    for index, item in enumerate(items):
        channel, params = item['channel'], item['params']
        if channel == 'claude:history-checkpoint':
            return unknown('history_checkpoint_gap')
        if channel == 'claude:error':
            return unknown('native_error_observed')
        if channel == 'claude:status':
            meta = params.get('meta')
            observed = native.input_state(meta)
            ordinal = len(turns) + int(active is not None)
            if (observed is None or observed['epoch'] != counter['epoch']
                    or observed['received'] != ordinal or observed['pending'] > 1
                    or meta.get('sdkSessionId') != original['sdk_session_id']):
                return unknown('serial_native_input_identity_changed')
            status_seen = status_seen or active is not None
        elif channel == 'claude:message':
            message = params.get('message')
            if not isinstance(message, dict) or message.get('sessionId', original['session_id']) != original['session_id']:
                return unknown('native_message_invalid')
            # BAT emits a system announcement before the first ordinary input.
            # It is neither an input nor a native completion/ownership proof.
            if message.get('role') == 'system':
                if pm_runtime._message_text(message) is None or message.get('truncated') or message.get('isStreaming'):
                    return unknown('native_message_incomplete')
                continue
            if message.get('role') == 'user':
                ident = message.get('id')
                if active is not None or not pm_runtime._text(ident) or ident in seen_users:
                    return unknown('serial_input_ambiguous')
                seen_users.add(ident)
                active, status_seen = item, False
                if len(turns) >= counter['received']:
                    return unknown('serial_input_count_changed')
            elif message.get('role') != 'assistant' or active is None:
                return unknown('message_outside_serial_turn')
        elif channel == 'claude:turn-end':
            if active is None or not status_seen:
                return unknown('serial_input_identity_missing')
            terminal_cursor = {'epoch': start['epoch'], 'seq': item['seq']}
            assessed = pm_runtime._assess_turn(original['session_id'], segment_cursor,
                items[segment_start:index + 1], terminal_cursor, original['sdk_session_id'], expected_options={})
            if not assessed['complete']:
                return unknown(assessed['reason'])
            turn_id = assessed['native_turn_id']
            if turn_id.startswith('local-' + original['session_id'] + '-'):
                return unknown('native_turn_identity_unproven')
            if turn_id in seen_turns:
                return unknown('duplicate_native_turn')
            seen_turns.add(turn_id)
            turns.append({'input_event_seq': active['seq'], 'terminal_event_seq': item['seq'],
                'native_turn_id': turn_id, 'sdk_session_id': original['sdk_session_id']})
            active, status_seen = None, False
            segment_start, segment_cursor = index + 1, terminal_cursor
        elif active is None and channel != 'claude:worktree-info':
            return unknown('activity_outside_serial_turn')
    if has_more or active is not None or len(turns) != counter['received']:
        return unknown('serial_history_incomplete')
    return {'complete': True, 'reason': 'all_serial_inputs_completed', 'turns': turns}


def receipt_applies(proof, original, counter):
    """A current inventory can apply only the exact saved manual proof profile."""
    if counter is None or counter != original['input_state'] or counter['pending']:
        return False
    if original['schema'] == 'pm_manual_single_input_v1':
        return proof['schema'] == 'pm_manual_single_input_quiescence_v1' and counter['received'] == 1
    return (original['schema'] == SERIAL_ORIGIN and proof['schema'] == SERIAL_RECEIPT
        and 2 <= counter['received'] <= MAX_SERIAL_INPUTS and proof['completed_turn_count'] == counter['received'])


def proof(ops, principal, operation):
    _access(ops, principal, operation)
    document = saved._saved(ops, operation['operation_id'], COMPLETE)
    if document is not None:
        original = saved._saved(ops, operation['operation_id'], ORIGIN)
        if document['origin_digest'] != evidence.digest(original) or document['observation_operation_id'] != operation['operation_id']:
            raise _bad('Original manual observation receipt changed')
        return saved.public_receipt(document)
    return None


async def _run(ctx):
    try:
        return await _observe(ctx)
    except OperationError as exc:
        raise Wait('waiting_external', exc.code, 15) from exc


async def _observe(ctx):
    ops, op = ctx.service, ctx.op
    completed = saved._saved(ops, ctx.operation_id, COMPLETE)
    if completed is not None:
        return saved.public_receipt(completed)
    principal, intent = _live(ops, op)
    client = ops.context['fleet'].client(intent['host'])
    await _scope(ops, op, client, intent)
    original = saved._saved(ops, ctx.operation_id, ORIGIN)
    if original is None:
        initial = await asyncio.wait_for(pm_runtime.sync_session(client, intent['session_id']), 15)
        state, meta = initial.get('state') or {}, (initial.get('state') or {}).get('meta') or {}
        counter = native.input_state(meta)
        serial = intent.get('replay_profile') == SERIAL_ORIGIN
        if (initial['mode'] != 'snapshot' or initial['gap'] or not getattr(client, 'auth_info', None)
                or meta.get('sdkSessionId') != intent['sdk_session_id'] or counter != intent['input_state']
                or state.get('isStreaming') is not False or meta.get('isStreaming') is not False
                or state.get('pendingPermission') or state.get('pendingAskUser')
                or counter is None or counter['pending'] != 0
                or (not 2 <= counter['received'] <= MAX_SERIAL_INPUTS if serial else counter['received'] != 1)):
            raise NeedsAttention('PM_SESSION_QUIESCENCE_PROFILE_UNAVAILABLE', 'Current unqueued inputs and the original native identity are required')
        original = {'schema': SERIAL_ORIGIN if serial else 'pm_manual_single_input_v1', 'host': intent['host'], 'session_id': intent['session_id'],
            'bat_workspace_id': intent['bat_workspace_id'], 'profile_id': intent['profile_id'],
            'host_binding_digest': intent['host_binding_digest'], 'sdk_session_id': meta['sdkSessionId'],
            'input_state': counter, 'send_cursor': {'epoch': initial['cursor']['epoch'], 'seq': 0},
            'initial_cursor': initial['cursor'], 'captured_at': time.time()}
        _live(ops, op)
        saved._store(ctx, ORIGIN, original)
    count, cursor, events, size = saved._pages(ops, ctx.operation_id, original)
    replay = await asyncio.wait_for(pm_runtime.sync_session(client, original['session_id'], cursor), 15)
    _live(ops, op)
    if replay['gap'] or replay['events']:
        page = {**replay, 'previous_cursor': cursor}
        if count >= native.MAX_PAGES or size + len(saved.batches._json(page).encode()) > native.MAX_BYTES or len(events) + len(replay['events']) > native.MAX_EVENTS:
            raise NeedsAttention('PM_SESSION_QUIESCENCE_LIMIT', 'Original complete replay exceeds the supported bound')
        saved._store(ctx, saved.PREFIX + str(count), page)
        if replay['gap']:
            raise NeedsAttention('PM_SESSION_QUIESCENCE_GAP', 'Original complete single-input replay is unavailable')
        events += replay['events']
    if replay['has_more']:
        raise Wait('waiting_external', 'Read the remaining bounded original native history', 1)
    serial = original['schema'] == SERIAL_ORIGIN
    if serial:
        assessed = assess_serial_inputs(original, events, replay['cursor'])
        complete = assessed['complete']
    else:
        users = [item for item in events if item['channel'] == 'claude:message' and item['params'].get('message', {}).get('role') == 'user']
        terminals = [item for item in events if item['channel'] == 'claude:turn-end']
        # Empty options observe manual input without claiming its permissions.
        assessed = pm_runtime._assess_turn(original['session_id'], original['send_cursor'], events, replay['cursor'],
            original['sdk_session_id'], expected_options={})
        complete = assessed['complete'] and len(users) == len(terminals) == 1 and users[0]['seq'] < terminals[0]['seq']
    if not complete or replay['cursor']['seq'] < original['initial_cursor']['seq']:
        raise NeedsAttention('PM_SESSION_QUIESCENCE_INCOMPLETE', 'The complete original native inputs have not been proven')
    _live(ops, op)
    final = await asyncio.wait_for(pm_runtime.sync_session(client, original['session_id'], replay['cursor']), 15)
    if final['gap'] or final['events'] or final['has_more']:
        raise Wait('waiting_external', 'Observe all current events before recording quiescence', 1)
    await _scope(ops, op, client, intent)
    meta = await asyncio.wait_for(client.invoke('claude:get-session-meta', {'sessionId': original['session_id']}), 15)
    _meta(original, meta)
    # No provider await after the last native identity read. A scope/catalogue
    # read may itself overlap a new human input and must precede this read.
    with ops.journal.tx():
        ctx.check_cancel()
        _live(ops, op)
        result = {'schema': 'pm_manual_single_input_quiescence_v1', 'workspace_id': op['target']['workspace_id'],
            'work_item_id': op['target']['work_item_id'], 'link_id': intent['link']['link_id'],
            'observation_operation_id': ctx.operation_id, 'host': original['host'], 'session_id': original['session_id'],
            'sdk_session_id': original['sdk_session_id'], 'input_state': original['input_state'],
            'origin_digest': evidence.digest(original), 'event_digest': evidence.digest(events), 'event_count': len(events),
            'native_turn_id': assessed.get('native_turn_id'), 'completion_cursor': replay['cursor'],
            'runtime_status': 'current_single_input_quiescent', 'process_death_proven': False, 'ownership': 'manual_read_only',
            'work_acceptance': 'not_evaluated', 'recorded_at': time.time()}
        if serial:
            result.pop('native_turn_id')
            result.update(schema=SERIAL_RECEIPT, runtime_status='current_serial_inputs_quiescent',
                completed_turn_count=len(assessed['turns']), turns_digest=evidence.digest(assessed['turns']),
                turns=assessed['turns'])
        saved._store(ctx, COMPLETE, result)
    return saved.public_receipt(result)
