"""Original coding-turn evidence; never infer completion from ACK, idle or absence.

Only a new repository.continue first-send callback can establish the replay
origin. One private read operation persists bounded replay pages while the
original repair runs and releases capacity only after its exact delivery settles.
"""
from __future__ import annotations

import asyncio
import json
import re
import time
from contextvars import ContextVar

from . import (
    api_auth,
    confinement,
    controller_authority,
    pm_runtime,
    pm_source_lineage,
    pm_workspace,
    registry,
)
from . import pm_maintenance_repairs as repairs
from .operations import ActionDef, NeedsAttention, OperationError, Wait

ACTION = 'pm.maintenance.repair.runtime.observe'
INTENT = 'pm.repair.runtime.origin'
LINK = 'pm_maintenance_repair_runtime_parent'
MAX_BYTES = 2 * 1024 * 1024
MAX_EVENTS = 4096
MAX_PAGES = 256
_ADMITTING = ContextVar('pm_repair_runtime_admission', default=None)


def _bad(message, code='PM_REPAIR_RUNTIME_UNPROVEN', status=409):
    return OperationError(code, message, status)


def schema(journal):
    with journal.tx():
        for table, fields in (
            ('origins', 'parent_id TEXT PRIMARY KEY,execution_id TEXT NOT NULL UNIQUE,document TEXT NOT NULL,document_digest TEXT NOT NULL'),
            ('observers', 'parent_id TEXT PRIMARY KEY,operation_id TEXT NOT NULL UNIQUE,request TEXT NOT NULL,request_digest TEXT NOT NULL'),
            ('pages', 'parent_id TEXT NOT NULL,ordinal INTEGER NOT NULL,document TEXT NOT NULL,document_digest TEXT NOT NULL,PRIMARY KEY(parent_id,ordinal)'),
            ('proofs', 'parent_id TEXT PRIMARY KEY,operation_id TEXT NOT NULL,document TEXT NOT NULL,document_digest TEXT NOT NULL')):
            name = 'pm_repair_runtime_' + table
            journal.db.execute(f'CREATE TABLE IF NOT EXISTS {name} ({fields})')
            for verb in ('UPDATE', 'DELETE'):
                journal.db.execute(f'CREATE TRIGGER IF NOT EXISTS {name}_{verb.lower()}_immutable BEFORE {verb} ON {name} '
                    "BEGIN SELECT RAISE(ABORT,'Original runtime evidence is immutable'); END")


def install(ops):
    schema(ops.journal)
    if ACTION not in ops.actions:
        ops.register(ActionDef(ACTION, 'observe', 'Read the original owned repair turn; no new input',
            _run, _admit, ('workspace_id', 'repair_id'), authorize_existing=_authorize_existing))


def _decode(row):
    if row is None:
        return None
    doc = json.loads(row['document'])
    if repairs.checks._digest(doc) != row['document_digest']:
        raise _bad('Original runtime evidence digest changed')
    return doc


def origin(ops, parent):
    if not pm_source_lineage._table(ops.db, 'pm_repair_runtime_origins'):
        return None
    row = ops.db.execute('SELECT * FROM pm_repair_runtime_origins WHERE parent_id=?', (parent['operation_id'],)).fetchone()
    value = _decode(row)
    if value is not None:
        child = repairs._child(ops, parent)
        receipt = ops.db.execute("SELECT response FROM operation_steps WHERE operation_id=? AND name=? AND status='succeeded'",
            (child['operation_id'], INTENT)).fetchone()
        if (not receipt or json.loads(receipt[0]) != {'document_digest': row['document_digest'], 'status': value['status']}
                or child['operation_id'] != row['execution_id'] or value['execution_operation_id'] != child['operation_id']
                or value['parent_operation_id'] != parent['operation_id']):
            raise _bad('Runtime origin lost its original first-send receipt')
    return value


def _parent_for_execution(ops, child):
    claim = (child.get('external_refs') or {}).get('pm_maintenance_repair_parent')
    if not repairs.installed(ops.db):
        if claim is not None:
            raise _bad('Unproven repair execution')
        return None
    row = ops.db.execute('SELECT parent_id FROM pm_maintenance_repair_children WHERE operation_id=?', (child['operation_id'],)).fetchone()
    if row is None and claim is None:
        return None
    if row is None or claim != row[0]:
        raise _bad('Unproven original repair child')
    parent = repairs._operation(ops, row[0])
    if repairs._child(ops, parent)['operation_id'] != child['operation_id']:
        raise _bad('Original repair child changed')
    return parent


def _host_binding(ops, host):
    hc = ops.context['fleet'].config.host(host)
    return repairs.checks._digest([hc.url, hc.fingerprint, hc.profile_id])


def _entry(ops, child, sid):
    host = child['target']['host']
    entry = registry.get(host, sid)
    reservation = (child.get('external_refs') or {}).get('repository_reservation')
    if (not entry or reservation != {'session_id': sid, 'created_at': entry.get('created_at')}
            or entry.get('start_operation_id') != child['operation_id'] or entry.get('task_id')
            or not re.fullmatch('[0-9a-f]{32}', str(entry.get('controller_incarnation', '')))
            or not controller_authority.same_controller(entry.get('controller_authority'), controller_authority.operation_binding(child))):
        raise _bad('Original managed session incarnation is unavailable')
    confinement.guard_start_record(entry)
    return entry


def _registry(ops, parent, doc):
    child = repairs._child(ops, parent)
    entry = _entry(ops, child, doc['session_id'])
    if (entry['controller_incarnation'] != doc['registry_incarnation'] or entry['created_at'] != doc['registry_created_at']
            or _host_binding(ops, doc['host']) != doc['host_binding_digest']
            or entry['confinement']['options'] != doc['confinement']['options']
            or (entry['confinement'].get('evidence') or {}).get('agent') != doc['confinement']['evidence']['agent']):
        raise _bad('Original host, incarnation or confinement changed')
    return child


async def capture(ctx, sid, text):
    """Called INSIDE the new first-send run, never its reconcile branch."""
    parent = _parent_for_execution(ctx.service, ctx.op)
    if parent is None:
        return
    repairs.require_child(ctx.service, ctx.op)
    child = repairs._operation(ctx.service, ctx.operation_id)
    entry = _entry(ctx.service, child, sid)
    host = child['target']['host']
    doc = {'schema': 'pm_repair_native_turn_v1', 'parent_operation_id': parent['operation_id'],
        'execution_operation_id': child['operation_id'], 'host': host, 'session_id': sid,
        'host_binding_digest': _host_binding(ctx.service, host), 'registry_incarnation': entry['controller_incarnation'],
        'registry_created_at': entry['created_at'], 'controller': controller_authority.operation_binding(child),
        'prompt_digest': pm_runtime.prompt_digest(text), 'confinement': entry['confinement'], 'status': 'unavailable',
        'reason': 'native_replay_unavailable', 'captured_at': time.time()}
    client = ctx.service.context['fleet'].client(host)
    try:
        remaining = repairs._record_for(ctx.service, parent)['deadline_at'] - time.time()
        replay = await asyncio.wait_for(pm_runtime.sync_session(client, sid), min(10, max(0.001, remaining)))
        if not getattr(client, 'auth_info', None):
            raise _bad('Original BAT transport was not authenticated')
        meta = (replay.get('state') or {}).get('meta', {})
        sdk = meta.get('sdkSessionId')
        cursor = pm_runtime.fresh_owned_send_cursor(replay, sdk, doc['confinement'])
        doc.update(status='captured', reason=None, send_cursor=cursor, sdk_session_id=sdk)
        doc['input_state'] = input_state(meta)
        if doc['input_state'] is not None and not same_original_input(doc, doc['input_state'], fresh=True):
            raise NeedsAttention('PM_REPAIR_RUNTIME_INPUT_NOT_FRESH', 'Native input history is not a fresh original session')
    except (pm_runtime.RuntimeEvidenceError, confinement.ConfinementRefused) as exc:
        if getattr(exc, 'code', None) in {'PM_SESSION_NOT_FRESH', 'CONFINEMENT_MISMATCH'}:
            raise NeedsAttention(exc.code, 'The original new coding session is not fresh and confined') from exc
        # An unsupported producer remains explicit and cannot later be upgraded
        # from an idle snapshot. Existing first-send behavior is retained.
        doc['reason'] = getattr(exc, 'code', 'native_replay_unavailable')
    except (OperationError, NeedsAttention):
        raise
    except Exception:  # noqa: BLE001 - read-only capability unavailable; no completion authority
        doc['reason'] = 'native_replay_unavailable'
    repairs.require_child(ctx.service, repairs._operation(ctx.service, ctx.operation_id))
    _registry(ctx.service, parent, doc)
    def save():
        repairs.require_child(ctx.service, repairs._operation(ctx.service, ctx.operation_id))
        ctx.service.db.execute('INSERT INTO pm_repair_runtime_origins VALUES(?,?,?,?)',
            (parent['operation_id'], child['operation_id'], repairs.checks._json(doc), repairs.checks._digest(doc)))
        return {'document_digest': repairs.checks._digest(doc), 'status': doc['status']}
    ctx.effect(INTENT, save)
    ensure_observer(ctx.service, parent)


async def before_send_frame(ctx):
    parent = _parent_for_execution(ctx.service, ctx.op)
    if parent is None:
        return
    doc = origin(ctx.service, parent)
    if doc is None or doc['status'] != 'captured':
        return
    repairs.require_child(ctx.service, ctx.op)
    _registry(ctx.service, parent, doc)
    remaining = repairs._record_for(ctx.service, parent)['deadline_at'] - time.time()
    client = ctx.service.context['fleet'].client(doc['host'])
    meta = await asyncio.wait_for(client.guard_read('claude:get-session-meta', {'sessionId': doc['session_id']}),
        min(10, max(0.001, remaining)))
    _meta(doc, meta, fresh=True)
    repairs.require_child(ctx.service, ctx.op)
    remaining = repairs._record_for(ctx.service, parent)['deadline_at'] - time.time()
    raw = await asyncio.wait_for(client.guard_read('claude:sync-session',
        {'sessionId': doc['session_id'], 'cursor': doc['send_cursor']}), min(10, max(0.001, remaining)))
    replay = pm_runtime.parse_replay(raw, doc['session_id'], doc['send_cursor'])
    if replay['gap'] or replay['events'] or replay['has_more'] or replay['cursor'] != doc['send_cursor']:
        raise _bad('Native replay changed before the original unsent input')
    repairs.require_child(ctx.service, ctx.op)
    _registry(ctx.service, parent, doc)



INPUT_SCHEMA = 'bat_input_state_v1'


def input_state(meta):
    """Authoritative runtime counter, never the SDK's usage/assistant turn count."""
    value = meta.get('inputState') if isinstance(meta, dict) else None
    if (not isinstance(value, dict) or set(value) != {'schema', 'epoch', 'received', 'pending'}
            or value.get('schema') != INPUT_SCHEMA or not isinstance(value.get('epoch'), str)
            or not re.fullmatch('[0-9a-f]{32}', value['epoch'])
            or type(value.get('received')) is not int or not 0 <= value['received'] <= 2**53 - 1
            or type(value.get('pending')) is not int or not 0 <= value['pending'] <= value['received']):
        return None
    return dict(value)


def same_original_input(doc, value, *, fresh=False):
    original = doc.get('input_state')
    return (isinstance(original, dict) and original['received'] == original['pending'] == 0
        and value is not None and value['epoch'] == original['epoch']
        and value['received'] == (0 if fresh else 1) and value['pending'] == 0)


def _meta(doc, meta, *, fresh=False):
    if (not isinstance(meta, dict) or meta.get('sdkSessionId') != doc['sdk_session_id']
            or meta.get('isStreaming') is not False
            or fresh and (type(meta.get('numTurns')) is not int or meta['numTurns'] != 0)):
        raise _bad('Original native session or sole input is no longer proven')
    # Older BATs can retain their original read receipts, but cannot release a
    # reservation from SDK usage counters or retrospectively acquired identity.
    if (not fresh or doc.get('input_state') is not None) and not same_original_input(doc, input_state(meta), fresh=fresh):
        raise _bad('Original native input/queue identity is unavailable or changed', 'PM_REPAIR_RUNTIME_INPUT_UNPROVEN')
    confinement.ensure_confirmed(doc['confinement'], meta)

def _access(ops, principal, parent):
    if not principal.allows('observe'):
        raise _bad('Current observe authority is required', 'FORBIDDEN', 403)
    pm_workspace.require_member(ops.db, principal, parent['target']['workspace_id'])
    repairs._full_access(ops, principal, parent)
    child = repairs._child(ops, parent)
    pm_source_lineage.require(ops.db, principal, 'operation', child['operation_id'])


def _live(ops, parent):
    """Current single credential + original controller; never old admission scopes."""
    original = ((parent.get('external_refs') or {}).get('admission_binding') or {}).get('principal') or {}
    if original.get('actor') != parent['actor'] or not (original.get('admin') or 'observe' in original.get('scopes', [])):
        raise _bad('Original read delegation is unavailable', 'FORBIDDEN', 403)
    now = time.time()
    candidates = ops.db.execute('''SELECT token_hash,scopes FROM api_principals WHERE actor=? AND revoked_at IS NULL
        AND created_at<=? AND (expires_at IS NULL OR expires_at>?) ORDER BY created_at DESC LIMIT 64''',
        (parent['actor'], now, now)).fetchall()
    live = next((api_auth.Principal(parent['actor'], frozenset({'observe'}), credential_id=row['token_hash'])
        for row in candidates if 'observe' in json.loads(row['scopes'])), None)
    if live is None and original.get('admin') is True:
        daemon = ops.context.get('daemon')
        token = vars(daemon).get('_admin_token') if daemon is not None else None
        if isinstance(token, str) and token:
            value = api_auth.authenticate(ops.db, token, token)
            if value and value.actor == parent['actor']:
                live = value
    if live is None or not controller_authority.same_controller(controller_authority.binding(ops.db, live),
            controller_authority.operation_binding(parent)):
        raise _bad('Original controller lacks a current read credential', 'FORBIDDEN', 403)
    _access(ops, live, parent)
    return live


def _request(parent, doc):
    return {'action': ACTION, 'target': {'workspace_id': parent['target']['workspace_id'],
        'repair_id': 'pmrp_' + parent['operation_id'][3:]}, 'params': {},
        'preconditions': {'runtime_origin_digest': repairs.checks._digest(doc)},
        'idempotency_key': 'pm-repair-runtime:' + parent['operation_id']}


def _admit(ops, principal, target, params, pre):
    row = repairs._row(ops, target.get('workspace_id'), target.get('repair_id'))
    parent = repairs._operation(ops, row['operation_id'])
    doc = origin(ops, parent)
    if (doc is None or doc['status'] != 'captured' or _ADMITTING.get() != (id(ops.db), parent['operation_id'])
            or target != _request(parent, doc)['target'] or params or pre != _request(parent, doc)['preconditions']):
        raise _bad('Only the original service-owned observer may be admitted')
    _access(ops, principal, parent)
    live = _live(ops, parent)
    if principal.actor != live.actor:
        raise _bad('The original controller must own its observer', 'FORBIDDEN', 403)
    return {'principal': pm_workspace.principal_binding(principal), 'pm_repair_runtime': {'parent_id': parent['operation_id'],
        'origin_digest': repairs.checks._digest(doc), 'operation_ids': [parent['operation_id'], doc['execution_operation_id']]},
        'pm_lineage': {'workspace_id': target['workspace_id']}}


def observer(ops, parent):
    row = ops.db.execute('SELECT * FROM pm_repair_runtime_observers WHERE parent_id=?', (parent['operation_id'],)).fetchone()
    if row is None:
        return None
    op, doc = repairs._operation(ops, row['operation_id']), origin(ops, parent)
    request = json.loads(row['request'])
    bound = ((op.get('external_refs') or {}).get('admission_binding') or {}).get('pm_repair_runtime') or {}
    if (request != _request(parent, doc) or repairs.checks._digest(request) != row['request_digest']
            or any(op.get(key) != value for key, value in request.items()) or op['actor'] != parent['actor']
            or bound.get('parent_id') != parent['operation_id'] or bound.get('origin_digest') != repairs.checks._digest(doc)
            or op['external_refs'].get(LINK) != parent['operation_id']
            or not controller_authority.same_controller(controller_authority.operation_binding(op), controller_authority.operation_binding(parent))):
        raise _bad('The observer lost its exact original private request')
    return op


def ensure_observer(ops, parent):
    doc = origin(ops, parent)
    if doc is None or doc['status'] != 'captured' or observer(ops, parent) is not None:
        return
    live = _live(ops, parent)
    with ops.journal.tx():
        token = _ADMITTING.set((id(ops.db), parent['operation_id']))
        try:
            request = _request(parent, doc)
            op, fresh = ops.create(live, **request, entry='pm-repair-runtime')
            if not fresh:
                raise _bad('An observer key exists without its private original relation')
            ops.db.execute('INSERT INTO pm_repair_runtime_observers VALUES(?,?,?,?)',
                (parent['operation_id'], op['operation_id'], repairs.checks._json(request), repairs.checks._digest(request)))
            ops._merge_refs(op['operation_id'], {LINK: parent['operation_id']})
        finally:
            _ADMITTING.reset(token)


def _original(ops, op):
    bound = ((op.get('external_refs') or {}).get('admission_binding') or {}).get('pm_repair_runtime') or {}
    parent = repairs._operation(ops, bound.get('parent_id'))
    if observer(ops, parent)['operation_id'] != op['operation_id']:
        raise _bad('Original observer relation unavailable')
    return parent


def operation_visible(db, principal, original):
    try:
        ops = repairs.workload._Reader(db)
        op = repairs._operation(ops, original['operation_id'])
        if op['action'] != ACTION:
            return False
        _access(ops, principal, _original(ops, op))
        return True
    except (OperationError, ValueError, TypeError, KeyError):
        return False


def _authorize_existing(ops, principal, op, _verb):
    parent = _original(ops, op)
    _access(ops, principal, parent)
    if not controller_authority.same_controller(controller_authority.binding(ops.db, principal), controller_authority.operation_binding(parent)):
        raise _bad('Only the original controller may steer this reader', 'FORBIDDEN', 403)


def _pages(ops, parent, doc):
    rows = ops.db.execute('SELECT * FROM pm_repair_runtime_pages WHERE parent_id=? ORDER BY ordinal LIMIT ?',
        (parent['operation_id'], MAX_PAGES + 1)).fetchall()
    cursor, events, size = doc['send_cursor'], [], 0
    for index, row in enumerate(rows):
        page = _decode(row)
        if row['ordinal'] != index or page['previous_cursor'] != cursor or page['gap']:
            raise _bad('Original replay contains a gap')
        cursor = page['cursor']
        events.extend(page['events'])
        size += len(row['document'].encode())
    if len(rows) > MAX_PAGES or len(events) > MAX_EVENTS or size > MAX_BYTES:
        raise _bad('Original bounded replay coverage exceeded')
    return rows, cursor, events, size


def _settled(ops, parent, reader_id, principal):
    from . import pm_maintenance_repair_evidence as evidence
    from . import pm_maintenance_repair_publication as publication
    if parent['status'] != 'succeeded' or (parent.get('result') or {}).get('outcome') not in {'published_for_review', 'merged', 'deployed'}:
        return None
    child, pub = repairs._child(ops, parent), publication.child(ops, parent)
    if child['status'] != 'succeeded' or pub is None or pub['status'] != 'succeeded':
        return None
    receipt = publication.published_receipt(ops, parent)
    if receipt is None:
        return None
    original = evidence.quality_assessment(ops, principal, parent, pub['params']['quality_ref'])
    assessment = original['assessment']['document']
    condition = repairs._intent(parent)['rule']['verification']
    verified = [item['verification'] for item in assessment['criteria'] if item.get('verification')
        and item['verification']['condition'] == condition and item['verification']['status'] == 'passed'
        and item['verification']['candidate_commit'] == receipt['candidate_sha']
        and item['verification']['tree_hash'] == receipt['candidate_tree']]
    if not assessment['qualified'] or not verified:
        return None
    # Any same-original derived active/uncertain effect keeps the reservation,
    # even when its public operation status was subsequently cancelled.
    rows = ops.db.execute("SELECT DISTINCT resource_id FROM pm_source_lineage WHERE resource_type='operation' AND original_operation_id=? LIMIT 257",
        (parent['operation_id'],)).fetchall()
    if len(rows) > 256:
        return None
    for row in rows:
        if row[0] in {parent['operation_id'], reader_id}:
            continue
        operation = repairs._operation(ops, row[0])
        if operation['status'] not in repairs.TERMINAL or repairs.checks._unsettled(ops, operation):
            return None
    if repairs.checks._unsettled(ops, parent):
        return None
    return {'outcome': parent['result']['outcome'], 'publication_operation_id': pub['operation_id'],
        'candidate_commit': receipt['candidate_sha'], 'tree_hash': receipt['candidate_tree'],
        'quality_ref': pub['params']['quality_ref'], 'verification': verified}


async def _run(ctx):
    try:
        return await _observe(ctx)
    except (OperationError, confinement.ConfinementRefused) as exc:
        # The original read operation remains recoverable after credential
        # rotation or restored source visibility, without a second key.
        raise Wait('waiting_external', getattr(exc, 'code', 'PM_REPAIR_RUNTIME_UNAVAILABLE'), 15) from exc


async def _observe(ctx):
    ops, parent = ctx.service, _original(ctx.service, ctx.op)
    principal = _live(ops, parent)
    doc = origin(ops, parent)
    saved = proof(ops, principal, parent)
    if saved is not None:
        return saved
    _registry(ops, parent, doc)
    rows, cursor, events, size = _pages(ops, parent, doc)
    try:
        replay = await asyncio.wait_for(pm_runtime.sync_session(ops.context['fleet'].client(doc['host']),
            doc['session_id'], cursor), 15)
    except pm_runtime.RuntimeEvidenceError as exc:
        raise NeedsAttention(exc.code, 'Original native replay cannot prove completion') from exc
    except Exception as exc:  # noqa: BLE001 - read retry only; original input is never sent here
        raise Wait('waiting_external', 'Original native replay is temporarily unavailable', 15) from exc
    principal = _live(ops, parent)
    _registry(ops, parent, doc)
    if replay['gap']:
        # Persist the permanent loss, so a later snapshot cannot heal it.
        def lost():
            page = {**replay, 'previous_cursor': cursor}
            ops.db.execute('INSERT INTO pm_repair_runtime_pages VALUES(?,?,?,?)',
                (parent['operation_id'], len(rows), repairs.checks._json(page), repairs.checks._digest(page)))
            return {'gap': True}
        ctx.effect('pm.repair.runtime.gap', lost)
        raise NeedsAttention('PM_REPAIR_RUNTIME_REPLAY_GAP', 'The original native replay is no longer contiguous')
    if replay['events']:
        page = {**replay, 'previous_cursor': cursor}
        if len(rows) >= MAX_PAGES or size + len(repairs.checks._json(page).encode()) > MAX_BYTES or len(events) + len(replay['events']) > MAX_EVENTS:
            raise NeedsAttention('PM_REPAIR_RUNTIME_REPLAY_LIMIT', 'Original bounded native replay coverage exceeded')
        def save_page():
            _live(ops, parent)
            _registry(ops, parent, doc)
            ops.db.execute('INSERT INTO pm_repair_runtime_pages VALUES(?,?,?,?)',
                (parent['operation_id'], len(rows), repairs.checks._json(page), repairs.checks._digest(page)))
            return {'document_digest': repairs.checks._digest(page), 'cursor': page['cursor']}
        ctx.effect('pm.repair.runtime.page.' + str(len(rows)), save_page)
        events += replay['events']
    assessed = pm_runtime.assess_owned_turn(doc['session_id'], doc['send_cursor'], events, replay['cursor'],
        doc['sdk_session_id'], original_confinement=doc['confinement'], has_more=replay['has_more'],
        expected_prompt_digest=doc['prompt_digest'])
    if (assessed['reason'] == 'original_input_not_observed' and not any(
            event['channel'] == 'claude:turn-end' or event['channel'] == 'claude:message'
            and event['params'].get('message', {}).get('role') == 'user' for event in events)):
        raise Wait('waiting_external', 'Original first input is not yet present in native replay', 5)
    if assessed['status'] in {'unknown', 'incomplete'}:
        raise NeedsAttention('PM_REPAIR_RUNTIME_INCOMPLETE', assessed['reason'])
    parent = repairs._operation(ops, parent['operation_id'])
    settled = _settled(ops, parent, ctx.operation_id, principal) if assessed['complete'] else None
    if not assessed['complete'] or settled is None:
        raise Wait('waiting_external', 'Awaiting original native completion and settled delivery', 1 if replay['has_more'] else 5)
    # A model may have changed files after an earlier verifier/publication.
    # Require the actual clean original carrier to retain that exact candidate.
    from . import pm_execution_verification
    current_candidate = await pm_execution_verification.configuration(ops, principal, parent['target']['workspace_id'],
        doc['execution_operation_id'])
    if any(current_candidate.get(key) != settled[key] for key in ('candidate_commit', 'tree_hash')):
        raise NeedsAttention('PM_REPAIR_RUNTIME_CANDIDATE_CHANGED', 'Original carrier no longer has the verified published candidate')
    _live(ops, parent)
    _registry(ops, parent, doc)
    client = ops.context['fleet'].client(doc['host'])
    final_replay = await asyncio.wait_for(pm_runtime.sync_session(client, doc['session_id'], replay['cursor']), 15)
    if final_replay['gap'] or final_replay['events'] or final_replay['has_more']:
        raise Wait('waiting_external', 'Read all original events after candidate verification before release', 1)
    meta = await asyncio.wait_for(client.invoke('claude:get-session-meta', {'sessionId': doc['session_id']}), 15)
    _meta(doc, meta)
    def finish():
        current = repairs._operation(ops, parent['operation_id'])
        principal = _live(ops, current)
        _registry(ops, current, doc)
        if _settled(ops, current, ctx.operation_id, principal) != settled:
            raise _bad('Original delivery changed before completion was recorded')
        result = {'schema': 'pm_repair_runtime_completion_v1', 'workspace_id': parent['target']['workspace_id'],
            'repair_id': 'pmrp_' + parent['operation_id'][3:], 'parent_operation_id': parent['operation_id'],
            'execution_operation_id': doc['execution_operation_id'], 'observation_operation_id': ctx.operation_id,
            'host': doc['host'], 'session_id': doc['session_id'], 'registry_incarnation': doc['registry_incarnation'],
            'origin_digest': repairs.checks._digest(doc), 'sdk_session_id': doc['sdk_session_id'],
            'input_state': input_state(meta),
            'native_turn_id': assessed['native_turn_id'], 'send_cursor': doc['send_cursor'], 'completion_cursor': replay['cursor'],
            'event_digest': repairs.checks._digest(events), 'event_count': len(events), 'delivery': settled,
            'capacity_held': False, 'runtime_status': 'original_turn_completed', 'process_death_proven': False,
            'repair_fixed': False, 'release_publication': 'not_inferred', 'recorded_at': time.time()}
        digest = repairs.checks._digest(result)
        ops.db.execute('INSERT INTO pm_repair_runtime_proofs VALUES(?,?,?,?)',
            (parent['operation_id'], ctx.operation_id, repairs.checks._json(result), digest))
        ops.db.execute('UPDATE pm_maintenance_repairs SET finished_at=? WHERE operation_id=? AND finished_at IS NULL',
            (result['recorded_at'], parent['operation_id']))
        return {**result, 'document_digest': digest}
    return ctx.effect('pm.repair.runtime.complete', finish)


def proof(ops, principal, parent):
    _access(ops, principal, parent)
    row = ops.db.execute('SELECT * FROM pm_repair_runtime_proofs WHERE parent_id=?', (parent['operation_id'],)).fetchone()
    result = _decode(row)
    if result is not None:
        doc = origin(ops, parent)
        if result['origin_digest'] != repairs.checks._digest(doc) or observer(ops, parent)['operation_id'] != row['operation_id']:
            raise _bad('Original runtime completion receipt changed')
        return {**result, 'document_digest': row['document_digest']}
    return None


def projection(ops, principal, parent):
    doc = origin(ops, parent)
    if doc is None:
        return {'runtime_completion': None, 'runtime_observer': None, 'runtime_origin_status': 'not_captured'}
    observed = observer(ops, parent)
    return {'runtime_origin_status': doc['status'], 'runtime_origin_reason': doc['reason'],
        'runtime_completion': proof(ops, principal, parent), 'runtime_observer':
        {key: observed[key] for key in ('operation_id', 'status', 'error_code')} if observed else None}
