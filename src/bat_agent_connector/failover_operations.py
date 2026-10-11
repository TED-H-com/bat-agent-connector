"""Standalone shared-carrier failover with immutable per-frame receipts and writer fences."""
from __future__ import annotations

import asyncio
import contextlib
import contextvars
import hashlib
import json
import sqlite3
import time
import uuid
from pathlib import Path
from urllib.parse import quote

from . import confinement, controller_authority, lifecycle, registry, resource_policy, service, task_control
from . import orchestration_operations as relay
from .errors import ResourceReadOnly
from .operations import (
    RERUN,
    ActionDef,
    AmbiguousOutcome,
    Cancelled,
    NeedsAttention,
    OperationError,
    StepFailed,
)
from .orchestrate import registry_permission_fields
from .safety import Audit
from .session_start_operations import NAMESPACE, _head, _tier

ACTION = 'session.failover'
FIELDS = {'all_exhausted', 'model', 'force', 'tail_messages', 'instructions', 'archive_only'}
_OWNER = contextvars.ContextVar('durable_failover_owner', default=None)
SAFE_CHANNELS = {'claude:stop-session', 'claude:interrupt-turn', 'claude:abort-session'}


def install(ops):
    if ACTION not in ops.actions:
        ops.register(ActionDef(ACTION, 'start', 'Continue one fixed standalone Claude source with Codex',
                               run, admit, ('host',), authorize_existing=authorize_existing))


def require(caller):
    if not caller.allows('start') or not caller.allows('operate'):
        raise OperationError('FORBIDDEN', 'failover requires start and operate', 403)


def validate(target, params, pre):
    if (pre or set(target) - {'host', 'session_id', 'workspace'} or not target.get('host')
            or any(not isinstance(v, str) or not v.strip() or len(v) > 256 for v in target.values())):
        raise OperationError('INVALID_TARGET', 'failover requires bounded host/session/workspace strings', 422)
    if set(params) - FIELDS:
        raise OperationError('INVALID_PARAMS', 'unsupported failover argument', 422)
    for key in ('all_exhausted', 'force', 'archive_only'):
        if key in params and type(params[key]) is not bool:
            raise OperationError('INVALID_PARAMS', key + ' must be boolean', 422)
    if bool(target.get('session_id')) == bool(params.get('all_exhausted')):
        raise OperationError('INVALID_PARAMS', 'choose one session_id or all_exhausted=true', 422)
    if not target.get('session_id') and (params.get('instructions') or params.get('archive_only')):
        raise OperationError('INVALID_PARAMS', 'instructions/archive_only require one source', 422)
    for key, limit in (('model', 256), ('instructions', 4000)):
        if key in params and (not isinstance(params[key], str) or not params[key].strip() or len(params[key]) > limit):
            raise OperationError('INVALID_PARAMS', key + ' must be a bounded nonempty string', 422)
    if 'tail_messages' in params and (type(params['tail_messages']) is not int or not 1 <= params['tail_messages'] <= 40):
        raise OperationError('INVALID_PARAMS', 'tail_messages must be 1-40', 422)


def admit(ops, caller, target, params, pre):
    validate(target, params, pre)
    require(caller)
    _tier(ops.context['fleet'], target['host'])


def authorize_existing(ops, caller, op, verb):
    require(caller)
    if caller.actor != op['actor']:
        raise OperationError('FORBIDDEN', 'failover controls retain the original actor', 403)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()).hexdigest()


def _receipt(ctx, name):
    return relay.receipt(ctx.service, ctx.operation_id, name)


def _rows():
    # This parser refuses malformed/unreadable documents instead of losing a fence.
    from .cleanup import _registry_document
    return _registry_document()['sessions']


def _owned(marker):
    """A private context must still prove its immutable journal receipt and registry marker."""
    owner = _OWNER.get()
    if not owner or owner[0] != registry._caller() or not isinstance(marker, dict):
        return False
    _, ctx, plan = owner
    try:
        saved = _receipt(ctx, plan['step'])
        op = ctx.service._row(ctx.operation_id)
        return (saved == plan and op and op['action'] == ACTION and op['actor'] == ctx.actor
                and marker['operation_id'] == ctx.operation_id
                and marker['source_session_id'] == plan['source_session_id']
                and marker['successor_session_id'] == plan['session_id']
                and marker['source_created_at'] == plan['registry']['created_at']
                and marker.get('cwd') == plan['cwd'] and marker.get('actor') == ctx.actor
                and marker.get('journal_path') == str(ctx.service.journal.path.resolve())
                and marker['plan_sha256'] == digest(plan))
    except Exception:  # noqa: BLE001 - unreadable local authority never grants a frame
        return False


def _completed_owner(marker):
    """Cross-process writers require the exact immutable plan and positive handoff ACK."""
    try:
        path = Path(marker['journal_path'])
        if not path.is_absolute():
            return False
        with contextlib.closing(sqlite3.connect('file:' + quote(str(path), safe='/') + '?mode=ro', uri=True, timeout=0.2)) as db:
            op = db.execute('SELECT action,actor FROM operations WHERE operation_id=?', (marker['operation_id'],)).fetchone()
            if not op or op != (ACTION, marker['actor']):
                return False
            row = db.execute("SELECT response FROM operation_steps WHERE operation_id=? AND name=? AND status='succeeded'",
                             (marker['operation_id'], marker['plan_step'])).fetchone()
            plan = json.loads(row[0]) if row else None
            if (not plan or digest(plan) != marker['plan_sha256'] or plan['session_id'] != marker['successor_session_id']
                    or plan['source_session_id'] != marker['source_session_id'] or plan['cwd'] != marker['cwd']
                    or plan['registry']['created_at'] != marker['source_created_at']):
                return False
            ack = db.execute("SELECT response FROM operation_steps WHERE operation_id=? AND name=? AND status='succeeded'",
                             (marker['operation_id'], marker['handoff_step'])).fetchone()
            value = json.loads(ack[0]) if ack else {}
            return (value.get('accepted') is True and value.get('message_id') == plan['message_id']
                    and value.get('text_sha256') == plan['prompt_sha256'])
    except (OSError, sqlite3.Error, ValueError, TypeError, KeyError):
        return False


def check_writer(host, sid, channel=None, *, shared=False, workdir=None):
    """Last shared policy gate; no actor string, public parameter or inherited task grants a bypass."""
    if channel in SAFE_CHANNELS:
        return
    rows = _rows()
    for row in rows:
        if row.get('host') != host or row.get('failover_fence') is None:
            continue
        marker = row['failover_fence']
        if not isinstance(marker, dict):
            if row.get('session_id') == sid:
                raise ResourceReadOnly('FAILOVER_FENCED', 'failover owner evidence is unreadable')
            continue
        source, successor = marker.get('source_session_id'), marker.get('successor_session_id')
        current = next((e for e in rows if e.get('host') == host and e.get('session_id') == sid), {})
        # Creating a separately owned worktree reads this origin but does not
        # grant a session the right to write in the fenced carrier itself.
        same_carrier = (channel != 'worktree:create' and marker.get('cwd')
                        and resource_policy.norm(workdir or current.get('worktree_path') or current.get('cwd')) == resource_policy.norm(marker['cwd']))
        if sid not in {source, successor} and not same_carrier:
            continue
        original = next((e for e in rows if e.get('host') == host and e.get('session_id') == source), {})
        replacement = next((e for e in rows if e.get('host') == host and e.get('session_id') == successor), {})
        valid = (original.get('failover_fence') == marker == replacement.get('failover_fence')
                 and original.get('created_at') == marker.get('source_created_at')
                 and replacement.get('created_at') == marker.get('successor_created_at')
                 and replacement.get('start_operation_id') == marker.get('operation_id')
                 and replacement.get('failover_of') == source)
        if not valid:
            raise ResourceReadOnly('FAILOVER_FENCED', 'failover identity is incomplete; retain the original operation')
        if sid not in {source, successor}:
            raise ResourceReadOnly('FAILOVER_SHARED_CARRIER', 'this carrier has an exclusive durable successor')
        if sid == source:
            if shared and _owned(marker):
                continue
            raise ResourceReadOnly('FAILOVER_FENCED', 'a durable successor owns this source; read, stop or interrupt remain available')
        if not _owned(marker) and (replacement.get('handoff_status') != 'sent' or not _completed_owner(marker)):
            raise ResourceReadOnly('FAILOVER_HANDOFF_PENDING', 'only the original operation may settle the fixed handoff')


@contextlib.contextmanager
def owning(ctx, plan):
    token = _OWNER.set((registry._caller(), ctx, plan))
    try:
        yield
    finally:
        _OWNER.reset(token)


async def selection(ops, target, params):
    fleet, host = ops.context['fleet'], target['host']
    if target.get('session_id'):
        tab, _ = await service._resolve_session(fleet.client(host), target['session_id'])
        return {'session_ids': [tab['id']], 'exhausted_found': 1, 'skipped_read_only': [], 'truncated_by_max_start_per_call': False}
    from .triage import sessions_triage
    tri = await sessions_triage(fleet, host, target.get('workspace'), agent='claude', states=['quota_exhausted'],
                               use_jev='never', include_unloaded=False)
    managed, skipped = [], []
    for row in tri['sessions']:
        code = row.get('read_only_code')
        if row.get('api_access') == 'managed':
            try:
                controller_authority.require_entry(registry.get(host, row['session_id']))
            except ResourceReadOnly as error:
                code = error.code
            else:
                managed.append(row)
                continue
        skipped.append({'session_id': row['session_id'], 'skipped': 'read_only', 'code': code})
    cap = fleet.config.safety.max_start_per_call
    return {'session_ids': [r['session_id'] for r in managed[:cap]], 'exhausted_found': len(tri['sessions']),
            'skipped_read_only': skipped,
            'truncated_by_max_start_per_call': len(managed) > cap}


def _source_record(host, sid):
    return {**relay.record(host, sid), 'failover_fence': (registry.get(host, sid) or {}).get('failover_fence')}


async def resolve(ops, target, params, sid, *, operation_id, index):
    fleet, host = ops.context['fleet'], target['host']
    client, hc = fleet.client(host), fleet.config.host(host)
    tab, ws = await service._resolve_session(client, sid)
    controller_authority.require_entry(registry.get(host, sid))
    task_control.refuse_owned(fleet, host, sid)
    if service.agent_kind(tab.get('agentPreset')) != 'claude':
        raise StepFailed('FAILOVER_AGENT', 'only standalone Claude sources support failover')
    before = _source_record(host, sid)
    if before['status'] in registry.RETIRED:
        raise ResourceReadOnly('SESSION_RETIRED', 'the original source is retired; choose a new managed start')
    classification = resource_policy.classify(hc, sid, terminal=tab, entries=registry.list_entries(host))
    if classification.code:
        raise ResourceReadOnly(classification.code, classification.reason)
    existing = [r for r in registry.list_entries(host) if r.get('failover_of') == sid and
                (r.get('status') in {'active', 'starting', 'uncertain'} or r.get('start_sent') is True)]
    if existing:
        return {'source_session_id': sid, 'existing': [{'session_id': r['session_id'], 'operation_id': r.get('start_operation_id'),
                'status': r.get('status'), 'handoff_status': r.get('handoff_status')} for r in existing]}
    if before.get('failover_fence') is not None:
        raise StepFailed('FAILOVER_FENCED', 'original source has unresolved failover evidence')
    meta = await service._meta(client, sid)
    snap = await lifecycle.session_snapshot(client, tab, meta, tail=80)
    cls = lifecycle.classify_messages(snap['messages'], streaming=snap['streaming'], pending=snap['pending'])
    if cls['state'] != 'quota_exhausted' and not params.get('force'):
        raise StepFailed('FAILOVER_NOT_EXHAUSTED', 'source is not quota exhausted; force only overrides this classification')
    matches = [w for w in ws.get('workspaces', []) if w.get('id') == tab.get('workspaceId')]
    if len(matches) != 1:
        raise StepFailed('FAILOVER_SOURCE_UNPROVEN', 'source workspace must be exact')
    w = matches[0]
    cwd = tab.get('worktreePath') or tab.get('cwd') or (meta or {}).get('cwd')
    origin = lifecycle._origin_cwd(tab, ws)
    root = await client.invoke('git:getRoot', {'cwd': cwd})
    branch = await client.invoke('git:branch', {'cwd': cwd})
    if not cwd or resource_policy.norm(root) != resource_policy.norm(cwd) or not isinstance(branch, str) or not branch:
        raise StepFailed('FAILOVER_SOURCE_UNPROVEN', 'original carrier/root/branch must remain readable')
    git = await lifecycle._git_state(client, cwd)
    first = await lifecycle._first_user_prompt(client, sid, snap['messages'])
    texts = [(r, t) for r, t in lifecycle._texts(snap['messages']) if not lifecycle.match_any(lifecycle.QUOTA_PATTERNS, t)]
    prompt = lifecycle.build_handoff_prompt(old_sid=sid, workspace=w.get('name'), cwd=cwd,
        same_worktree=bool(tab.get('worktreePath')), branch=branch, first_prompt=first,
        last_prompt=next((t for r, t in reversed(texts) if r == 'user'), None),
        recent=texts[-params.get('tail_messages', 12):], git=git, evidence=cls.get('evidence'), note=None,
        forced=cls['state'] != 'quota_exhausted', instructions=params.get('instructions'))
    new_sid = str(uuid.uuid5(NAMESPACE, operation_id + ':' + sid))
    grant = await resource_policy.authorize_shared_session(fleet, host, new_sid, tab)
    permissions, scope, confined = await confinement.start_decision(fleet, host, 'codex',
        confined=before.get('write_scope') == 'confined', predecessor=registry.get(host, sid) if before.get('write_scope') == 'confined' else None)
    plan = {'step': 'source.resolve.' + str(index), 'host': host, 'source_session_id': sid, 'session_id': new_sid,
        'registry': before, 'terminal': {k: tab.get(k) for k in ('id', 'workspaceId', 'cwd', 'worktreePath', 'worktreeBranch', 'agentPreset', 'sdkSessionId')},
        'runtime_loaded': isinstance(meta, dict), 'runtime': {k: (meta or {}).get(k) for k in ('cwd', 'sdkSessionId')},
        'workspace_id': w['id'], 'workspace_name': w.get('name'), 'folder': w.get('folderPath'), 'origin': origin,
        'cwd': cwd, 'root': root, 'branch': branch, 'head': await _head(client.invoke, cwd),
        'same_worktree': bool(tab.get('worktreePath')), 'profile_id': hc.profile_id,
        'permission_policy': hc.default_permission_mode, 'permission_options': permissions,
        'write_scope': scope, 'confinement': confined, 'isolation': grant.isolation,
        'model': params.get('model') or hc.codex_model, 'archive_only': params.get('archive_only', False),
        'preset': 'codex-agent-worktree' if tab.get('worktreePath') else 'codex-agent',
        'prompt': prompt, 'prompt_sha256': hashlib.sha256(prompt.encode()).hexdigest(),
        'message_id': 'batc-' + str(uuid.uuid5(NAMESPACE, operation_id + ':handoff:' + sid)),
        'evidence': cls.get('evidence'), 'resets': cls.get('resets'),
        'git': {k: git.get(k) for k in ('dirty_count', 'uncommitted_diff_stats')},
        'replaces': sid if before['status'] == 'active' and tab.get('worktreePath') else None}
    await identity(ops, plan, client.invoke, reserved=False)
    return plan


def _carrier_registry_ids(plan):
    return {r['session_id'] for r in _rows() if r.get('host') == plan['host']
            and r.get('session_id') != plan['session_id']
            and resource_policy.norm(r.get('worktree_path') or r.get('cwd')) == resource_policy.norm(plan['cwd'])}


def guard(ops, plan, *, reserved, ctx=None, consumers=None):
    fleet, host, sid = ops.context['fleet'], plan['host'], plan['source_session_id']
    if ctx:
        ctx.check_cancel()
        ctx.require_controller()
    relay.require_record(host, sid, plan['registry'])
    _tier(fleet, host)
    hc = fleet.config.host(host)
    if hc.profile_id != plan['profile_id'] or hc.default_permission_mode != plan['permission_policy']:
        raise StepFailed('FAILOVER_POLICY_CHANGED', 'fixed host profile/permission policy changed')
    task_control.refuse_owned(fleet, host, sid)
    task_control.refuse_owned(fleet, host, plan['session_id'])
    from .task_cleanup import owners
    task_ids, _, _ = owners(ops, {'host': host, 'path': plan['cwd'], 'session_id': sid}, entries=_rows())
    if task_ids:
        raise StepFailed('TASK_OWNED_CONTROL_REQUIRED', 'a durable task owns this carrier; use the Task Service')
    expected = dict(plan['registry'])
    if reserved:
        row = registry.get(host, plan['session_id']) or {}
        controller_authority.require_entry(row)
        fence = row.get('failover_fence')
        if (not ctx or not _owned(fence) or row.get('task_id') or row.get('role') or row.get('status') in registry.RETIRED
                or row.get('cwd') != plan['cwd'] or row.get('agent_preset') != plan['preset']
                or row.get('write_scope') != plan['write_scope']
                or any(row.get(k) != v for k, v in registry_permission_fields(plan['permission_options']).items())):
            raise StepFailed('FAILOVER_BINDING_CHANGED', 'successor reservation no longer has its original owner')
        expected['failover_fence'] = fence
        if plan['replaces']:
            expected.update(status='superseded', superseded_by=plan['session_id'])
    if _source_record(host, sid) != expected:
        raise StepFailed('FAILOVER_BINDING_CHANGED', 'original source incarnation changed')
    if consumers is not None:
        if _carrier_registry_ids(plan) - consumers.keys():
            raise StepFailed('FAILOVER_SHARED_CARRIER', 'a new carrier consumer has no stopped-writer proof')
        for other, binding in consumers.items():
            relay.require_record(host, other, binding)
            task_control.refuse_owned(fleet, host, other)
            if _source_record(host, other) != binding:
                raise StepFailed('FAILOVER_SHARED_CARRIER', 'a shared carrier consumer changed after its idle read')
    from .cleanup import guard as cleanup_guard
    cleanup_guard(host, session_id=sid, path=plan['cwd'], branch=plan['branch'])
    cleanup_guard(host, session_id=plan['session_id'], path=plan['cwd'], branch=plan['branch'])


async def _idle(read, sid, *, saved=None):
    meta = await read('claude:get-session-meta', {'sessionId': sid})
    if meta is None:
        return None
    if not isinstance(meta, dict) or not isinstance(meta.get('cwd'), str) or not meta['cwd']:
        raise StepFailed('FAILOVER_WRITER_UNPROVEN', 'shared carrier runtime is unreadable')
    if saved and (saved['runtime_loaded'] is not True or any(meta.get(k) != v for k, v in saved['runtime'].items())):
        raise StepFailed('FAILOVER_RUNTIME_CHANGED', 'original source runtime identity changed')
    state = await read('claude:get-session-state', {'sessionId': sid})
    if (meta.get('isStreaming') is not False or not isinstance(state, dict) or state.get('isStreaming') is not False
            or any(state.get(k) for k in service.SESSION_WAITING_FIELDS)):
        raise StepFailed('FAILOVER_WRITER_UNPROVEN', 'shared carrier writer is active, waiting or unproven')
    return meta


async def identity(ops, plan, read, *, reserved, ctx=None):
    guard(ops, plan, reserved=reserved, ctx=ctx)
    raw = await read('workspace:load', {'profileId': plan['profile_id']})
    doc = json.loads(raw) if isinstance(raw, str) else raw
    if not isinstance(doc, dict) or not isinstance(doc.get('terminals'), list):
        raise StepFailed('FAILOVER_SOURCE_UNPROVEN', 'workspace document is unavailable')
    found = [t for t in doc['terminals'] if t.get('id') == plan['source_session_id']]
    workspaces = [w for w in doc.get('workspaces', []) if w.get('id') == plan['workspace_id']]
    if (len(found) != 1 or any(found[0].get(k) != v for k, v in plan['terminal'].items())
            or len(workspaces) != 1 or workspaces[0].get('folderPath') != plan['folder']):
        raise StepFailed('FAILOVER_SOURCE_CHANGED', 'fixed terminal/workspace identity changed')
    if (await read('git:getRoot', {'cwd': plan['cwd']}) != plan['root']
            or await read('git:branch', {'cwd': plan['cwd']}) != plan['branch']
            or await _head(read, plan['cwd']) != plan['head']):
        raise StepFailed('FAILOVER_GIT_CHANGED', 'original carrier root/branch/HEAD changed')
    if plan['same_worktree']:
        wt = await read('worktree:status', {'sessionId': plan['source_session_id']})
        if not isinstance(wt, dict) or wt.get('worktreePath') != plan['cwd'] or wt.get('branchName') != plan['branch']:
            raise StepFailed('FAILOVER_SOURCE_CHANGED', 'original BAT worktree binding changed')
    consumers = {t['id'] for t in doc['terminals'] if t.get('id') and
                 resource_policy.norm(t.get('worktreePath') or t.get('cwd')) == resource_policy.norm(plan['cwd'])}
    consumers.update(_carrier_registry_ids(plan))
    consumers.discard(plan['session_id'])
    consumers.add(plan['source_session_id'])
    bindings = {sid: _source_record(plan['host'], sid) for sid in consumers}
    for sid in sorted(consumers):
        relay.require_record(plan['host'], sid, bindings[sid])
        task_control.refuse_owned(ops.context['fleet'], plan['host'], sid)
        if sid != plan['source_session_id']:
            row = registry.get(plan['host'], sid) or {}
            terminal = next((t for t in doc['terminals'] if t.get('id') == sid), None)
            cls = resource_policy.classify(ops.context['fleet'].config.host(plan['host']), sid,
                                           terminal=terminal, entries=registry.list_entries(plan['host']))
            if (cls.code or row.get('status') in {'starting', 'uncertain'}
                    or row.get('start_uncertain') or row.get('handoff_status') in {'pending', 'uncertain'}):
                raise StepFailed('FAILOVER_SHARED_CARRIER', 'another manual, unknown or unsettled consumer shares this carrier')
        await _idle(read, sid, saved=plan if sid == plan['source_session_id'] else None)
    guard(ops, plan, reserved=reserved, ctx=ctx, consumers=bindings)
    return bindings


async def _invoke(ctx, plan, name, channel, params, *, handoff=False):
    client, fleet, host, sid = ctx.service.context['fleet'].client(plan['host']), ctx.service.context['fleet'], plan['host'], plan['session_id']
    audit = Audit(fleet.config.safety)
    base = {'actor': ctx.actor, 'tool': 'api:session.failover', 'host': host, 'session_id': sid}
    ctx.set_refs(**{name + '_sent': False})
    sent = False
    bindings = None
    def fence():
        nonlocal sent
        # Mark conservatively before either durable write. A partial local failure
        # never turns a potentially consumed handoff claim back into unsent proof.
        sent = True
        if handoff:
            registry.claim_handoff_frame(host, sid, plan['message_id'], plan['prompt_sha256'])
        else:
            registry.update(host, sid, start_sent=True)
        ctx.set_refs(**{name + '_sent': True})
    async def final():
        nonlocal bindings
        if not handoff:
            # Trust verification can await host I/O. Read the final carrier and
            # stopped-writer evidence after it, then finish with synchronous gates.
            await confinement.guard_start_frame(fleet, host, plan['confinement'])
        bindings = await identity(ctx.service, plan, client.guard_read, reserved=True, ctx=ctx)
        if handoff:
            meta = await _idle(client.guard_read, sid)
            confirmed = _receipt(ctx, name.removesuffix('handoff') + 'confirm')
            if not confirmed or not isinstance(meta, dict) or any(meta.get(k) != v for k, v in confirmed['runtime'].items()):
                raise StepFailed('FAILOVER_RUNTIME_CHANGED', 'successor runtime differs from its original confirmation')
            confinement.guard_start_cwd({'cwd': plan['cwd']}, meta)
            confinement.ensure_confirmed(plan['confinement'], meta, allow_unknown=False)
        gate()
    def gate():
        if bindings is None:
            raise StepFailed('FAILOVER_WRITER_UNPROVEN', 'final carrier consumer evidence is unavailable')
        guard(ctx.service, plan, reserved=True, ctx=ctx, consumers=bindings)
    try:
        audit.check_rate(host, sid, initial_task_send=handoff)
        audit.record(**base, channel=channel, phase='attempt', operation_id=ctx.operation_id)
        grant = await resource_policy.authorize_shared_session(fleet, host, sid, plan['terminal'])
        result = await client.invoke(channel, params, grant=grant, retry_on_disconnect=False,
            before_frame=final, before_send=gate, on_transport=fence)
        audit.record(**base, channel=channel, phase='result', ok=True)
        return result
    except (Exception, asyncio.CancelledError) as exc:
        audit.record(**base, channel=channel, phase='result', ok=False, error=type(exc).__name__)
        if sent:
            raise AmbiguousOutcome('failover frame crossed its durable fence; retain and read the original receipt') from exc
        if isinstance(exc, Cancelled):
            raise StepFailed('CANCELLED', 'cancelled before transport') from exc
        if handoff:
            raise NeedsAttention(getattr(exc, 'code', None) or 'FAILOVER_HANDOFF_NOT_SENT',
                                 'handoff was not sent; original operation may resume only after its gates pass') from exc
        raise StepFailed(getattr(exc, 'code', None) or 'FAILOVER_NOT_SENT', 'failover frame was not sent: ' + str(exc)) from exc


def _unsent(ctx, plan, name, *, handoff=False):
    row = registry.get(plan['host'], plan['session_id']) or {}
    field = 'handoff_frame_sha256' if handoff else 'start_sent'
    value = None if handoff else False
    return ((ctx.service._row(ctx.operation_id).get('external_refs') or {}).get(name + '_sent') is False
            and field in row and row[field] is value)


async def item(ctx, plan, index):
    if plan.get('existing'):
        return {'old_session_id': plan['source_session_id'], 'skipped': 'existing successor retained; inspect its original operation',
                'existing': plan['existing']}
    host, sid = plan['host'], plan['session_id']
    fleet, client = ctx.service.context['fleet'], ctx.service.context['fleet'].client(host)
    prefix = 'failover.' + str(index) + '.'
    reservation_name = prefix + 'reserve'
    async def reread(_):
        return RERUN
    async def reserve():
        await identity(ctx.service, plan, client.invoke, reserved=False, ctx=ctx)
        marker = {'version': 1, 'operation_id': ctx.operation_id, 'source_session_id': plan['source_session_id'],
                  'successor_session_id': sid, 'source_created_at': plan['registry']['created_at'],
                  'cwd': plan['cwd'], 'plan_sha256': digest(plan), 'plan_step': plan['step'],
                  'handoff_step': prefix + 'handoff', 'journal_path': str(ctx.service.journal.path.resolve()), 'actor': ctx.actor}
        previous = registry.reserve(host, {'session_id': sid, 'start_operation_id': ctx.operation_id,
            'workspace_id': plan['workspace_id'], 'workspace_name': plan['workspace_name'], 'agent_preset': plan['preset'],
            'origin_cwd': plan['origin'], 'cwd': plan['cwd'], 'worktree_path': plan['cwd'] if plan['same_worktree'] else None,
            'branch': plan['branch'] if plan['same_worktree'] else None, 'model': plan['model'],
            'failover_of': plan['source_session_id'], 'shares_worktree_with': plan['source_session_id'] if plan['same_worktree'] else None,
            'isolation': plan['isolation'], 'start_sent': False, 'confinement': plan['confinement'], 'write_scope': plan['write_scope'],
            'cleanup_policy': 'archive' if plan['archive_only'] else None, 'handoff_status': 'pending',
            'handoff_message_id': plan['message_id'], 'handoff_frame_sha256': None,
            **registry_permission_fields(plan['permission_options'])}, fleet.config.host(host).orchestrate_max_sessions,
            replaces=plan['replaces'], predecessor_expected=plan['registry'], failover_fence=marker)
        if previous:
            raise StepFailed('FAILOVER_SUCCESSOR_EXISTS', 'another successor won reservation; inspect its original operation')
        row = registry.get(host, sid)
        return {'created_at': row['created_at'], 'fence': row['failover_fence']}
    async def reserved(_):
        row = registry.get(host, sid)
        if not row:
            return RERUN
        if row.get('start_operation_id') != ctx.operation_id or not _owned(row.get('failover_fence')):
            raise NeedsAttention('FAILOVER_BINDING_CHANGED', 'reserved successor differs from the fixed operation')
        return {'created_at': row['created_at'], 'fence': row['failover_fence']}
    with owning(ctx, plan):
        async with service._write_lock(host):
            if not _receipt(ctx, prefix + 'start'):
                registry.claim_unsent(host, sid)
            reservation = await ctx.step(reservation_name, reserve, reconcile=reserved)
            def project(fields, expected):
                if not registry.project_start(host, sid, operation_id=ctx.operation_id, created_at=reservation['created_at'],
                    expected={**expected, 'failover_fence': reservation['fence'], 'task_id': None, 'role': None,
                              'cwd': plan['cwd'], 'agent_preset': plan['preset'], 'write_scope': plan['write_scope'],
                              **registry_permission_fields(plan['permission_options'])}, fields=fields):
                    raise NeedsAttention('FAILOVER_BINDING_CHANGED', 'successor local receipt projection lost its incarnation')
                return {'projected': True}
            options = {'cwd': plan['origin'] if plan['same_worktree'] else plan['cwd'], 'agentPreset': plan['preset'],
                       'workspaceId': plan['workspace_id'], 'workspaceName': plan['workspace_name'], **plan['permission_options']}
            if plan['model']:
                options['model'] = plan['model']
            if plan['same_worktree']:
                options.update(useWorktree=True, worktreePath=plan['cwd'], worktreeBranch=plan['branch'])
            async def start():
                result = await _invoke(ctx, plan, prefix + 'start', 'claude:start-session', {'sessionId': sid, 'options': options})
                if not isinstance(result, dict) or result.get('ok') is not True or result.get('sessionId') != sid:
                    raise AmbiguousOutcome('failover start did not positively acknowledge its exact reserved ID')
                return {'started': True, 'session_id': sid}
            def verified(meta):
                row = registry.get(host, sid) or {}
                if not _owned(row.get('failover_fence')) or row.get('start_operation_id') != ctx.operation_id:
                    raise NeedsAttention('FAILOVER_BINDING_CHANGED', 'successor recovery does not own this incarnation')
                confinement.guard_start_cwd({'cwd': plan['cwd']}, meta)
                confinement.ensure_confirmed(plan['confinement'], meta, allow_unknown=False)
                return {'confinement': confinement.confirm(plan['confinement'], meta),
                        'runtime': {k: meta.get(k) for k in ('cwd', 'sdkSessionId')}}
            async def start_read(_):
                if _unsent(ctx, plan, prefix + 'start'):
                    return RERUN
                meta = await service._meta(client, sid)
                if meta is None:
                    return None
                verified(meta)
                return {'started': True, 'session_id': sid, 'settled_by': 'reserved_identity_readback'}
            await ctx.step(prefix + 'start', start, request={'session_id': sid, 'options': options}, reconcile=start_read)
            async def confirm():
                meta = await service._meta(client, sid)
                if not isinstance(meta, dict):
                    raise NeedsAttention('FAILOVER_CONFIRMATION_UNPROVEN', 'successor metadata is not available')
                return verified(meta)
            confirmed = await ctx.step(prefix + 'confirm', confirm, reconcile=reread)
            async def activate():
                return project({'status': 'active', 'confinement': confirmed['confinement']}, {'status': 'starting', 'start_sent': True})
            await ctx.step(prefix + 'activate', activate, reconcile=reread)
            async def send():
                result = await _invoke(ctx, plan, prefix + 'handoff', 'claude:send-message',
                    {'sessionId': sid, 'prompt': plan['prompt'], 'clientMessageId': plan['message_id']}, handoff=True)
                if not isinstance(result, dict) or result.get('accepted', result.get('ok')) is not True:
                    raise AmbiguousOutcome('Codex handoff acceptance is not proven')
                return {'accepted': True, 'message_id': plan['message_id'], 'text_sha256': plan['prompt_sha256']}
            async def sent(_):
                return RERUN if _unsent(ctx, plan, prefix + 'handoff', handoff=True) else None
            await ctx.step(prefix + 'handoff', send, request={'session_id': sid, 'message_id': plan['message_id'],
                'text_sha256': plan['prompt_sha256']}, reconcile=sent)
            async def finish():
                return project({'handoff_status': 'sent'}, {'status': 'active', 'start_sent': True,
                    'handoff_message_id': plan['message_id'], 'handoff_frame_sha256': plan['prompt_sha256']})
            await ctx.step(prefix + 'record', finish, reconcile=reread, receipt_only=True)
    return {'host': host, 'old_session_id': plan['source_session_id'], 'new_session_id': sid, 'cwd': plan['cwd'],
            'branch': plan['branch'], 'same_worktree': plan['same_worktree'], 'prompt_sent': True,
            'message_id': plan['message_id'], 'archive_only': plan['archive_only'], 'write_scope': plan['write_scope'],
            'workspace': plan['workspace_name'], 'agent_preset': plan['preset'], 'model': plan['model'] or '(BAT default)',
            'permissions': plan['write_scope'] or plan['permission_policy'], 'confinement': plan['confinement'],
            'isolation': plan['isolation'], 'handoff_chars': len(plan['prompt']), 'error': None,
            'evidence': plan['evidence'], 'resets': plan['resets'], 'git': plan['git'],
            'old_session': 'left as is; durable writer fence retained; use reviewed cleanup', 'counts_toward_cap': plan['replaces'] is None}


@registry.start_call
async def run(ctx):
    async def reread(_):
        return RERUN
    chosen = await ctx.step('failover.selection', lambda: selection(ctx.service, ctx.target, ctx.params), reconcile=reread)
    plans = []
    for index, sid in enumerate(chosen['session_ids'], 1):
        plans.append(await ctx.step('source.resolve.' + str(index),
            lambda sid=sid, index=index: resolve(ctx.service, ctx.target, ctx.params, sid, operation_id=ctx.operation_id, index=index), reconcile=reread))
    results = []
    try:
        for index, plan in enumerate(plans, 1):
            ctx.set_refs(failover_result={'host': ctx.target['host'], **chosen, 'failovers': results,
                'pending_source': plan['source_session_id'], 'pending_successor': plan.get('session_id'), 'count': len(results)})
            result = await item(ctx, plan, index)
            results.append(result)
    except (Cancelled, StepFailed, asyncio.CancelledError):
        # Never clear a sent or indeterminate start. Only the original live claim
        # and committed false boundary can release its own reservation/fence.
        for plan in plans:
            row = registry.get(ctx.target['host'], plan.get('session_id')) or {}
            if row.get('start_sent') is False and isinstance(row.get('failover_fence'), dict):
                old = {**plan['registry'], 'failover_fence': row['failover_fence']}
                if plan['replaces']:
                    old.update(status='superseded', superseded_by=plan['session_id'])
                successor = {'status': 'starting', 'task_id': None, 'role': None, 'cwd': plan['cwd'],
                    'workspace_id': plan['workspace_id'], 'agent_preset': plan['preset'], 'write_scope': plan['write_scope'],
                    'worktree_path': plan['cwd'] if plan['same_worktree'] else None,
                    'branch': plan['branch'] if plan['same_worktree'] else None,
                    **registry_permission_fields(plan['permission_options'])}
                registry.rollback_failover(ctx.target['host'], row['session_id'], fence=row['failover_fence'],
                                           predecessor_expected=old, successor_expected=successor)
        raise
    out = {'host': ctx.target['host'], **chosen, 'failovers': results, 'count': len(results), 'dry_run': False}
    if ctx.target.get('session_id') and results:
        out.update(results[0])
    ctx.set_refs(failover_result=out)
    ctx.check_cancel()
    return out


def cancel(ops, caller, op):
    # An exhausted retry budget is not permission to hide a sent start/handoff.
    ops.db.execute('UPDATE operations SET cancel_requested=1,next_run_at=0,updated_at=? WHERE operation_id=?',
                   (time.time(), op['operation_id']))
    pending = ops.db.execute("SELECT 1 FROM operation_steps WHERE operation_id=? AND status IN ('started','uncertain')",
                             (op['operation_id'],)).fetchone()
    if op['status'] == 'needs_attention' and pending:
        ops._transition(op['operation_id'], 'running', actor=caller.actor, reason='reading original failover receipts after cancellation')
    elif op['status'] in {'accepted', 'waiting_checks', 'waiting_external'} and not pending:
        ops._transition(op['operation_id'], 'cancelled', actor=caller.actor, reason='cancelled before the next failover step')
    ops.kick()
    return ops.get(op['operation_id'], steps=False)


async def legacy(ops, caller, request, *, entry):
    if set(request) - FIELDS - {'host', 'session_id', 'workspace', 'confirm', 'dry_run', 'idempotency_key'}:
        raise OperationError('INVALID_PARAMS', 'unknown standalone failover argument', 422)
    if 'dry_run' in request and type(request['dry_run']) is not bool:
        raise OperationError('INVALID_PARAMS', 'dry_run must be boolean', 422)
    target = {k: request[k] for k in ('host', 'session_id', 'workspace') if request.get(k) is not None}
    params = {k: request[k] for k in FIELDS if request.get(k) is not None}
    if request.get('dry_run') is True:
        if not caller.allows('observe'):
            raise OperationError('FORBIDDEN', 'failover preview requires observe', 403)
        validate(target, params, {})
        chosen = await selection(ops, target, params)
        plans = [await resolve(ops, target, params, sid, operation_id='preview', index=i)
                 for i, sid in enumerate(chosen['session_ids'], 1)]
        rows = [{'old_session_id': p['source_session_id'], 'cwd': p.get('cwd'), 'branch': p.get('branch'),
                 'same_worktree': p.get('same_worktree'), 'handoff_preview': p.get('prompt', '')[:1500],
                 'existing': p.get('existing'), 'dry_run': True} for p in plans]
        return {'host': target['host'], **chosen, 'failovers': rows, 'count': len(rows), 'dry_run': True,
                **(rows[0] if target.get('session_id') and rows else {})}
    if request.get('confirm') is not True:
        raise OperationError('CONFIRM_REQUIRED', 'failover requires confirm=true', 403)
    op, _ = ops.create(caller, action=ACTION, target=target, params=params, idempotency_key=request.get('idempotency_key'),
                       entry=entry, _legacy_session=True)
    await ops.run_due()
    op = await ops.wait(op['operation_id'], 30)
    return {**((op.get('external_refs') or {}).get('failover_result') or {}), **(op.get('result') or {}),
            'operation_id': op['operation_id'], 'operation_status': op['status'], 'operation_error_code': op['error_code'],
            'operation_status_reason': op['status_reason'], 'idempotency_key': op['idempotency_key'], 'idempotency_enabled': op['idempotency_enabled']}
