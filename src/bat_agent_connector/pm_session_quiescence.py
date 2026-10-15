"""Explicit read-only evidence for one linked manual session's current input.

This never creates ownership, clears a registry reservation or accepts work.
Only the bounded single-input native profile is supported.
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


def _bad(message, code='PM_SESSION_QUIESCENCE_UNPROVEN', status=409):
    return OperationError(code, message, status)


def install(ops):
    if ACTION not in ops.actions:
        ops.register(ActionDef(ACTION, 'observe', 'Observe one explicitly linked manual input without changing the session',
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
            'manual_multiple_inputs_unsupported' if counter['received'] != 1 else
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
        if (initial['mode'] != 'snapshot' or initial['gap'] or not getattr(client, 'auth_info', None)
                or meta.get('sdkSessionId') != intent['sdk_session_id'] or counter != intent['input_state']
                or state.get('isStreaming') is not False or meta.get('isStreaming') is not False
                or state.get('pendingPermission') or state.get('pendingAskUser')
                or counter is None or counter['received'] != 1 or counter['pending'] != 0):
            raise NeedsAttention('PM_SESSION_QUIESCENCE_PROFILE_UNAVAILABLE', 'A current unqueued single input and original native identity are required')
        original = {'schema': 'pm_manual_single_input_v1', 'host': intent['host'], 'session_id': intent['session_id'],
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
    users = [item for item in events if item['channel'] == 'claude:message' and item['params'].get('message', {}).get('role') == 'user']
    terminals = [item for item in events if item['channel'] == 'claude:turn-end']
    # Empty expected_options observes a manual session without claiming its
    # permissions were created or controlled by Connector.
    assessed = pm_runtime._assess_turn(original['session_id'], original['send_cursor'], events, replay['cursor'],
        original['sdk_session_id'], has_more=replay['has_more'], expected_options={})
    if replay['has_more']:
        raise Wait('waiting_external', 'Read the remaining bounded original native history', 1)
    if (not assessed['complete'] or len(users) != 1 or len(terminals) != 1 or users[0]['seq'] >= terminals[0]['seq']
            or replay['cursor']['seq'] < original['initial_cursor']['seq']):
        raise NeedsAttention('PM_SESSION_QUIESCENCE_INCOMPLETE', 'The complete original single input has not been proven')
    final = await asyncio.wait_for(pm_runtime.sync_session(client, original['session_id'], replay['cursor']), 15)
    if final['gap'] or final['events'] or final['has_more']:
        raise Wait('waiting_external', 'Observe all current events before recording quiescence', 1)
    meta = await asyncio.wait_for(client.invoke('claude:get-session-meta', {'sessionId': original['session_id']}), 15)
    _meta(original, meta)
    await _scope(ops, op, client, intent)
    with ops.journal.tx():
        ctx.check_cancel()
        _live(ops, op)
        result = {'schema': 'pm_manual_single_input_quiescence_v1', 'workspace_id': op['target']['workspace_id'],
            'work_item_id': op['target']['work_item_id'], 'link_id': intent['link']['link_id'],
            'observation_operation_id': ctx.operation_id, 'host': original['host'], 'session_id': original['session_id'],
            'sdk_session_id': original['sdk_session_id'], 'input_state': original['input_state'],
            'origin_digest': evidence.digest(original), 'event_digest': evidence.digest(events), 'event_count': len(events),
            'native_turn_id': assessed['native_turn_id'], 'completion_cursor': replay['cursor'],
            'runtime_status': 'current_single_input_quiescent', 'process_death_proven': False, 'ownership': 'manual_read_only',
            'work_acceptance': 'not_evaluated', 'recorded_at': time.time()}
        saved._store(ctx, COMPLETE, result)
    return saved.public_receipt(result)
