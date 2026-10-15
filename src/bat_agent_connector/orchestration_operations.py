"""Fixed relay selection and canonical child operations, owned by the existing scheduler."""
from __future__ import annotations

import hashlib
import json
import time

from . import (
    api_actions,
    api_auth,
    controller_authority,
    lifecycle,
    registry,
    resource_policy,
    service,
    session_permissions,
    task_control,
)
from .errors import ResourceReadOnly, TaskControlRefused
from .operations import RERUN, ActionDef, Cancelled, NeedsAttention, OperationError, StepFailed, Wait
from .relay import build_relay

ACTION = 'session.relay'
PARENTS = frozenset({ACTION, 'fanout.plan', 'fanout.start'})
FIELDS = {'message', 'channel', 'thread', 'earlier', 'brief', 'request_fanout', 'max_items', 'queue', 'start_if_missing'}
BINDING_FIELDS = (*session_permissions.BINDING_FIELDS, 'workspace_id', 'origin_cwd', 'origin_root', 'branch',
                  'superseded_by', 'failover_of', 'controller_authority', 'controller_incarnation')


def install(ops):
    if ACTION not in ops.actions:
        ops.register(ActionDef(ACTION, 'operate', 'Relay exact original words to one fixed managed target',
                               run, admit, ('host',), authorize_existing=authorize_existing))


def validate(target, params, pre):
    if (set(target) - {'host', 'workspace', 'session_id'} or not target.get('host')
            or not (target.get('workspace') or target.get('session_id'))
            or any(not isinstance(v, str) or not v.strip() or len(v) > 512 for v in target.values())):
        raise OperationError('INVALID_TARGET', 'host and workspace or session_id are required', 422)
    if (set(params) - FIELDS or not isinstance(params.get('message'), str) or not params['message'].strip()
            or len(params['message']) > 12000):
        raise OperationError('INVALID_PARAMS', 'relay requires original message of 1-12000 characters', 422)
    for key in ('channel', 'thread'):
        if key in params and (not isinstance(params[key], str) or len(params[key]) > 512):
            raise OperationError('INVALID_PARAMS', key + ' must be a bounded string', 422)
    for key in ('request_fanout', 'queue', 'start_if_missing'):
        if key in params and type(params[key]) is not bool:
            raise OperationError('INVALID_PARAMS', key + ' must be boolean', 422)
    if 'max_items' in params and (type(params['max_items']) is not int or not 1 <= params['max_items'] <= 16):
        raise OperationError('INVALID_PARAMS', 'max_items must be 1-16', 422)
    earlier = params.get('earlier', [])
    if not isinstance(earlier, list) or len(earlier) > 40 or any(not isinstance(v, str) or len(v) > 12000 for v in earlier):
        raise OperationError('INVALID_PARAMS', 'earlier must be a bounded list of original messages', 422)
    brief = params.get('brief')
    if brief is not None and not isinstance(brief, (str, dict)):
        raise OperationError('INVALID_PARAMS', 'brief must be a string or an object', 422)
    if len(json.dumps(brief, ensure_ascii=False)) > 16000:
        raise OperationError('INVALID_PARAMS', 'brief is too large', 422)
    if (set(pre) - {'control_version'} or 'control_version' in pre and
            (type(pre['control_version']) is not int or pre['control_version'] < 0)):
        raise OperationError('INVALID_PARAMS', 'control_version must be a nonnegative integer', 422)


def scopes(params):
    return {'operate', *({'start'} if params.get('start_if_missing') else set())}


def require(principal, params):
    if not all(principal.allows(scope) for scope in scopes(params)):
        raise OperationError('FORBIDDEN', 'relay requires operate and start when start_if_missing is requested', 403)


def admit(ops, principal, target, params, pre):
    validate(target, params, pre)
    require(principal, params)
    fleet = ops.context['fleet']
    if target['host'] not in fleet.config.hosts:
        raise OperationError('UNKNOWN_HOST', 'unknown relay host', 404)
    if not fleet.writes_enabled(target['host']):
        raise OperationError('TIER_DISABLED', 'relay host write tier is disabled', 403)
    return {'orchestration': {'accepted_scopes': sorted(scopes(params))}, **admit_source(target)}


def authorize_existing(ops, principal, op, verb):
    require(principal, op['params'])
    if principal.actor != op['actor']:
        raise OperationError('FORBIDDEN', 'relay controls retain their original actor', 403)


def principal(ctx):
    bound = ctx.admission_binding['orchestration']
    return api_auth.Principal(ctx.actor, frozenset(bound['accepted_scopes']))


def record(host, sid):
    row = registry.get(host, sid) or {}
    return {k: row.get(k) for k in BINDING_FIELDS}


def require_record(host, sid, original):
    # Missing historical ownership is never repaired from a current row.
    controller_authority.require_entry(original)
    return controller_authority.require_entry(registry.get(host, sid),
        expected_incarnation=original['controller_incarnation'])


def source_snapshot(host, selector=None):
    """Pin registry identity before asynchronous source discovery, including absence.

    Prefix selectors remain supported; this evidence permits reading a foreign
    source, but never grants authority to mutate it.
    """
    selector = selector.strip() if selector else None
    entries = {}
    for row in registry.list_entries(host):
        sid = row.get('session_id')
        if not isinstance(sid, str) or selector and not sid.startswith(selector):
            continue
        if sid in entries:
            raise StepFailed('SESSION_SOURCE_CHANGED', 'source registry identity is ambiguous')
        entries[sid] = _source_identity(row)
    return {'host': host, 'selector': selector, 'entries': entries}


def _source_identity(row):
    return {'present': row is not None, **{key: (row or {}).get(key) for key in
            ('created_at', 'controller_authority', 'controller_incarnation')}}


def admit_source(target):
    return ({'session_source': source_snapshot(target['host'], target['session_id'])}
            if target.get('session_id') else {})


def source_pin(snapshot, host, sid):
    if (not isinstance(snapshot, dict) or snapshot.get('host') != host
            or not isinstance(snapshot.get('entries'), dict)
            or snapshot.get('selector') and not sid.startswith(snapshot['selector'])):
        raise StepFailed('SESSION_SOURCE_CHANGED', 'original source selection evidence is unavailable')
    pinned = {'host': host, 'session_id': sid,
              'identity': snapshot['entries'].get(sid, _source_identity(None))}
    check_source(pinned)
    return pinned


def check_source(pinned):
    if (not isinstance(pinned, dict) or not pinned.get('host') or not pinned.get('session_id')
            or pinned.get('identity') != _source_identity(registry.get(pinned['host'], pinned['session_id']))):
        raise StepFailed('SESSION_SOURCE_CHANGED', 'original source controller or incarnation changed')


async def resolve_source(ops, target, *, ctx=None, snapshot=None, step='source.identity'):
    """Resolve once and durably retain the concrete ID before any further reads."""
    host = target['host']
    if snapshot is None:
        snapshot = ((ctx.admission_binding or {}).get('session_source') if ctx else
                    source_snapshot(host, target['session_id']))
    saved = receipt(ops, ctx.operation_id, step) if ctx else None
    if saved:
        check_source(saved)
    tab, doc = await service._resolve_session(ops.context['fleet'].client(host),
                                            saved['session_id'] if saved else target['session_id'])
    pinned = source_pin(snapshot, host, tab['id'])
    if saved and saved != pinned:
        raise StepFailed('SESSION_SOURCE_CHANGED', 'resolved source differs from the original selection')
    pinned = ctx.effect(step, lambda: pinned) if ctx else pinned
    check_source(pinned)
    return tab, doc, pinned


def task_binding(value):
    """Task incarnation is independent of controller and Git execution metadata."""
    return {k: v for k, v in (value or {}).items()
            if k not in {'controller_authority', 'session_controller', 'git_execution'}} or None


def require_child_authority(ctx, parent):
    controller_authority.require_binding(controller_authority.operation_binding(parent))
    controller_authority.require_binding(controller_authority.operation_binding(ctx.op))


def receipt(ops, operation_id, name):
    row = ops.db.execute('SELECT response FROM operation_steps WHERE operation_id=? AND name=? AND status=\'succeeded\'',
                         (operation_id, name)).fetchone()
    return json.loads(row['response']) if row else None


async def select(ops, target, params, pre, *, ctx=None):
    """Only reads here. The successful step fixes every later branch and prompt."""
    fleet, host = ops.context['fleet'], target['host']
    hc, client = fleet.config.host(host), fleet.client(host)
    workspace = target.get('workspace')
    snapshot = None
    if target.get('session_id'):
        chosen = {'session_id': target['session_id']}
    else:
        snapshot = (ctx.effect('relay.candidate_sources', lambda: source_snapshot(host)) if ctx else source_snapshot(host))
        chosen = receipt(ops, ctx.operation_id, 'relay.source_choice') if ctx else None
        if chosen is None:
            chosen = await lifecycle.main_session(fleet, host, workspace) or {'session_id': None}
            chosen = ctx.effect('relay.source_choice', lambda: chosen) if ctx else chosen
    sid, ws_name = chosen['session_id'], workspace or chosen.get('workspace')
    tab, denied, meta = None, None, None
    if sid:
        tab, ws, pinned = await resolve_source(ops, {'host': host, 'session_id': sid}, ctx=ctx, snapshot=snapshot)
        sid = tab['id']
        # Journal ownership survives missing/drifted registry evidence. A task control
        # refusal can never select the independent-start compatibility fallback.
        owner = task_control.owner_task(fleet, host, sid)
        if owner:
            coordinator = ops.context.get('coordinator')
            if coordinator is None:
                raise TaskControlRefused('TASK_OWNER_UNAVAILABLE', 'relay task owner is unavailable')
            task_control.check(coordinator.journal, owner, host, sid, 'send', pre.get('control_version'))
        try:
            await resource_policy.authorize_session(fleet, host, 'session.send', tab)
        except ResourceReadOnly as exc:
            if owner or task_control.owner_task(fleet, host, sid):
                raise
            denied = {'read_only': True, 'read_only_code': exc.code, 'reason': str(exc)}
        check_source(pinned)
    else:
        ws = await service._workspace(client)
    n = min(fleet.config.safety.max_start_per_call, params.get('max_items') or fleet.config.safety.max_start_per_call)
    text = build_relay(params['message'], host=host, workspace=ws_name, channel=params.get('channel'), thread=params.get('thread'),
        earlier=params.get('earlier'), brief=params.get('brief'), human_name=fleet.config.human_name,
        relay_name=fleet.config.relay_name, request_fanout=params.get('request_fanout', False), max_items=n)
    api_actions._validate_send({'text': text})
    out = {'host': host, 'session_id': sid, 'workspace': ws_name, 'text': text,
           'text_sha256': hashlib.sha256(text.encode()).hexdigest(), 'request_fanout': params.get('request_fanout', False),
           'max_items': n, 'profile_id': hc.profile_id}
    if denied or not sid:
        out.update(denied or {'no_session': True})
        if not params.get('start_if_missing'):
            return {**out, 'route': 'none', 'sent': False,
                    'next': 'start_if_missing=true requires start scope and creates a new managed Codex worktree'}
        selector = workspace or (tab or {}).get('workspaceId')
        matches = [w for w in ws.get('workspaces', []) if selector in {w.get('id'), w.get('name')}]
        if len(matches) != 1:
            raise StepFailed('RELAY_WORKSPACE_UNPROVEN', 'new relay target requires one exact workspace')
        w = matches[0]
        root = await client.invoke('git:getRoot', {'cwd': w.get('folderPath')})
        branch = await client.invoke('git:branch', {'cwd': w.get('folderPath')})
        log = await client.invoke('git:log', {'cwd': w.get('folderPath'), 'count': 1})
        head = log[0].get('hash') if isinstance(log, list) and log and isinstance(log[0], dict) else None
        if not root or not isinstance(branch, str) or not head:
            raise StepFailed('RELAY_WORKSPACE_UNPROVEN', 'new relay source Git identity is unavailable')
        if sid and task_control.owner_task(fleet, host, sid):
            raise TaskControlRefused('TASK_OWNED_CONTROL_REQUIRED', 'task ownership appeared before relay fallback')
        return {**out, 'route': 'start', 'source_session_id': sid, 'workspace_id': w['id'], 'folder': w['folderPath'], 'origin_root': root,
                'source_branch': branch, 'base_commit': head}
    kind = service.agent_kind(tab.get('agentPreset'))
    if kind not in {'claude', 'codex'}:
        raise StepFailed('RELAY_AGENT_UNSUPPORTED', 'relay supports managed Claude or Codex only')
    bound_task = task_binding(api_actions._admit_session(ops, None, {'host': host, 'session_id': sid}, {}, pre))
    original = record(host, sid)
    check_source(pinned)
    meta = await service._meta(client, sid)
    if await lifecycle._quota_stopped(fleet, host, sid):
        return {**out, 'route': 'none', 'sent': False, 'quota_stopped': True,
                'next': 'inspect the original session before explicitly planning a successor'}
    if (meta or {}).get('isStreaming') is True and not params.get('queue'):
        return {**out, 'route': 'none', 'sent': False, 'busy': True, 'next': 'use queue=true explicitly'}
    if record(host, sid) != original:
        raise StepFailed('RELAY_BINDING_CHANGED', 'relay session changed while selecting it')
    return {**out, 'route': 'send', 'registry': original, 'task_binding': bound_task, 'agent_kind': kind,
            'terminal': {k: tab.get(k) for k in ('workspaceId', 'cwd', 'worktreePath', 'worktreeBranch', 'agentPreset')},
            'runtime': {k: (meta or {}).get(k) for k in ('sdkSessionId', 'cwd')}}


def linked(op):
    return (op.get('external_refs') or {}).get('orchestration_child')


def child_plan(ctx, *, allow_cancel=False):
    link = linked(ctx.op)
    if not link:
        return None
    parent = ctx.service._row(link.get('parent_id'))
    if parent and parent['action'] in {'fanout.plan', 'fanout.start'}:
        from .fanout_operations import child_plan as fanout_child
        return fanout_child(ctx, link, parent, allow_cancel=allow_cancel)
    plan = receipt(ctx.service, link.get('parent_id'), 'relay.resolve') if parent else None
    saved = receipt(ctx.service, link.get('parent_id'), 'relay.child') if parent else None
    if (not parent or parent['action'] not in PARENTS or parent['actor'] != ctx.actor or not plan or
            not saved or saved.get('operation_id') != ctx.operation_id or link.get('slot') != 'relay'
            or {k: ctx.op[k] for k in ('action', 'target', 'params', 'preconditions')} != saved.get('intent')):
        raise TaskControlRefused('RELAY_BINDING_CHANGED', 'child does not match the original relay receipt')
    require_child_authority(ctx, parent)
    if not allow_cancel and (parent['cancel_requested'] or parent['status'] in {'failed', 'cancelled'}):
        raise TaskControlRefused('RELAY_CANCEL_REQUESTED', 'relay cancelled before this frame')
    return plan


def check_child(ctx, *, start_plan=None):
    plan = child_plan(ctx)
    if not plan:
        return None
    fleet, host = ctx.service.context['fleet'], plan['host']
    if fleet.config.host(host).profile_id != plan['profile_id']:
        raise TaskControlRefused('RELAY_BINDING_CHANGED', 'relay host profile changed')
    if plan['route'] == 'send':
        sid = plan['session_id']
        require_record(host, sid, plan['registry'])
        if record(host, sid) != plan['registry'] or task_control.owner_task(fleet, host, sid) != (plan['task_binding'] or {}).get('task_id'):
            raise TaskControlRefused('RELAY_BINDING_CHANGED', 'relay session creation or owner changed')
        if task_binding(ctx.admission_binding) != task_binding(plan['task_binding']):
            raise TaskControlRefused('RELAY_BINDING_CHANGED', 'relay child task binding changed')
        task_control.check_binding(ctx)
        from .cleanup import guard
        guard(host, session_id=sid, path=plan['registry'].get('worktree_path') or plan['registry'].get('cwd'))
    else:
        if plan.get('source_session_id') and task_control.owner_task(fleet, host, plan['source_session_id']):
            raise TaskControlRefused('TASK_OWNED_CONTROL_REQUIRED', 'task-owned relay source cannot start an independent successor')
        if start_plan is not None and any(start_plan.get(k) != plan.get(k) for k in
                ('host', 'workspace_id', 'folder', 'origin_root', 'source_branch', 'base_commit')):
            raise TaskControlRefused('RELAY_BINDING_CHANGED', 'new relay workspace or source changed')
    return plan


async def before_send(ctx, client, *, resume=False):
    plan = check_child(ctx)
    if not plan:
        return
    raw = await client.guard_read('workspace:load', {'profileId': plan['profile_id']})
    try:
        ws = json.loads(raw) if isinstance(raw, str) else raw
    except (ValueError, TypeError):
        ws = None
    if not isinstance(ws, dict) or not isinstance(ws.get('terminals'), list):
        raise TaskControlRefused('RELAY_BINDING_CHANGED', 'relay workspace identity is unavailable')
    tab, _ = await service._resolve_session(client, plan['session_id'], ws)
    if tab['id'] != plan['session_id'] or any(tab.get(k) != v for k, v in plan['terminal'].items()):
        raise TaskControlRefused('RELAY_BINDING_CHANGED', 'relay terminal identity changed')
    meta = await client.guard_read('claude:get-session-meta', {'sessionId': plan['session_id']})
    if meta is not None and (not isinstance(meta, dict) or any(meta.get(k) != v for k, v in plan['runtime'].items() if v is not None)):
        raise TaskControlRefused('RELAY_BINDING_CHANGED', 'relay runtime identity changed')
    if not resume and not service._state_safe(plan['agent_kind'], meta):
        raise TaskControlRefused('RELAY_RUNTIME_UNPROVEN', 'relay runtime is no longer loaded')
    if not resume and meta.get('isStreaming') is True and not ctx.params.get('queue'):
        raise TaskControlRefused('RELAY_BUSY', 'relay target started another turn; queue=true was not requested')
    check_child(ctx)


def authorize_child(ops, caller, op, verb):
    link = linked(op)
    if not link:
        return
    parent = ops._row(link.get('parent_id'))
    if not parent or parent['action'] not in PARENTS or parent['actor'] != caller.actor:
        raise OperationError('FORBIDDEN', 'relay child controls retain the original actor', 403)
    if parent['action'] != ACTION:
        from .fanout_operations import authorize_existing
        authorize_existing(ops, caller, parent, verb)
    else:
        require(caller, parent['params'])
    if verb == 'resume' and parent['cancel_requested']:
        raise OperationError('RELAY_CANCEL_REQUESTED', 'cancelled relay cannot resume unsent child work', 409)


def create_child(ctx, plan):
    def create():
        if plan['route'] == 'send':
            require_record(plan['host'], plan['session_id'], plan['registry'])
            if record(plan['host'], plan['session_id']) != plan['registry']:
                raise StepFailed('RELAY_BINDING_CHANGED', 'fixed relay session changed before child admission')
            intent = {'action': 'session.send', 'target': {'host': plan['host'], 'session_id': plan['session_id']},
                      'params': {'text': plan['text'], **({'queue': True} if ctx.params.get('queue') else {})},
                      'preconditions': {'control_version': plan['task_binding']['control_version']} if plan['task_binding'] else ctx.preconditions}
        else:
            intent = {'action': 'session.start', 'target': {'host': plan['host'], 'workspace': plan['workspace_id']},
                      'params': {'agent': 'codex', 'prompt': plan['text'], 'use_worktree': True, 'title': 'relayed task'}, 'preconditions': {}}
        child, fresh = ctx.service.create(principal(ctx), **intent, idempotency_key=None, _legacy_session=True, entry=ctx.op['entry'])
        if not fresh or plan['route'] == 'send' and task_binding((child.get('external_refs') or {}).get('admission_binding')) != task_binding(plan['task_binding']):
            raise StepFailed('RELAY_BINDING_CHANGED', 'relay task binding changed before child admission')
        ctx.service._merge_refs(child['operation_id'], {'orchestration_child': {'parent_id': ctx.operation_id, 'slot': 'relay'}})
        ctx.set_refs(relay_child_id=child['operation_id'])
        return {'operation_id': child['operation_id'], 'intent': intent}
    return ctx.effect('relay.child', create)


async def run(ctx):
    async def reread(_):
        return RERUN
    plan = await ctx.step('relay.resolve', lambda: select(ctx.service, ctx.target, ctx.params, ctx.preconditions, ctx=ctx), reconcile=reread)
    ctx.set_refs(relay_selection=plan)
    if plan['route'] == 'none':
        return plan
    saved = receipt(ctx.service, ctx.operation_id, 'relay.child')
    cancelled = ctx.service._row(ctx.operation_id)['cancel_requested']
    if saved is None:
        if cancelled:
            raise Cancelled()
        saved = create_child(ctx, plan)
    if cancelled:
        ctx.service.cancel(principal(ctx), saved['operation_id'])
    child = ctx.service.get(saved['operation_id'])
    out = {'host': plan['host'], 'session_id': (child.get('result') or {}).get('session_id') or
           (child.get('external_refs') or {}).get('session_id') or plan['session_id'], 'workspace': plan['workspace'],
           'text': plan['text'], 'request_fanout': plan['request_fanout'], 'max_items': plan['max_items'], 'sent': None,
           'child_operation_id': child['operation_id'], 'child_operation_status': child['status'],
           'child_error_code': child['error_code'], 'child_status_reason': child['status_reason'], 'result': child.get('result')}
    if child['status'] == 'succeeded':
        result = child['result'] or {}
        out.update(sent=result.get('accepted') is True if plan['route'] == 'send' else result.get('prompt_sent') is True)
        if plan['route'] == 'start':
            out['started'] = result.get('started')
            if plan.get('read_only'):
                out['replaced'] = {k: plan[k] for k in ('session_id', 'read_only_code', 'reason')}
        out.update({k: result.get(k) for k in ('turn_marker', 'after_ms', 'after', 'marker_source', 'turn_phase', 'turn_attribution')})
        if plan['route'] == 'send':
            out['next'] = ('session_wait/session_read with after=turn_marker; check turn_attribution before reporting output'
                           if out.get('turn_marker') else 'session_wait(require_new=true); check message timestamps against the send time')
    ctx.set_refs(relay_result=out)
    unproven = any(s['status'] in {'started', 'uncertain'} for s in child['steps'])
    if child['status'] in {'accepted', 'running', 'uncertain', 'waiting_external', 'waiting_checks'}:
        due = child.get('next_run_at') if child['status'] not in {'accepted', 'running'} else None
        raise Wait('waiting_external', 'reading the original relay child', delay_s=max(1, min(30, (due - time.time()) if due else 1)))
    if child['status'] == 'needs_attention' or unproven:
        raise NeedsAttention('RELAY_CHILD_UNSETTLED', 'inspect original child receipts; no unknown frame will be resent')
    if cancelled:
        raise Cancelled()
    if child['status'] in {'failed', 'cancelled'}:
        raise StepFailed(child['error_code'] or 'RELAY_CHILD_REFUSED', child['status_reason'] or 'relay child did not complete')
    if out['sent'] is not True:
        raise NeedsAttention('RELAY_SEND_UNPROVEN', 'original child did not prove prompt acceptance')
    return out


def cancel(ops, caller, op):
    with ops.journal.tx():
        ops.db.execute('UPDATE operations SET cancel_requested=1 WHERE operation_id=?', (op['operation_id'],))
        ops._transition(op['operation_id'], 'running', reason='cancelling original orchestration children; retaining receipts', actor=caller.actor)
    ops.kick()
    return ops.get(op['operation_id'], steps=False)


def resume_children(ops, caller, op):
    if op['action'] != ACTION:
        from .fanout_operations import resume_children as fanout_resume
        return fanout_resume(ops, caller, op)
    saved = receipt(ops, op['operation_id'], 'relay.child')
    if not op['cancel_requested'] and saved and ops._row(saved['operation_id'])['status'] == 'needs_attention':
        ops.resume(caller, saved['operation_id'])


async def preview(ops, caller, request):
    if not caller.allows('observe'):
        raise OperationError('FORBIDDEN', 'relay dry-run requires observe', 403)
    target, params, pre = inputs(request)
    validate(target, params, pre)
    # Dry-run preserves the existing read-only render, including manual/no-session diagnostics.
    with controller_authority.use(controller_authority.binding(ops.db, caller), ops.journal):
        return await lifecycle.session_relay(ops.context['fleet'], **target, **params, dry_run=True)


def inputs(request):
    if set(request) - FIELDS - {'host', 'workspace', 'session_id', 'confirm', 'dry_run', 'idempotency_key', 'control_version'}:
        raise OperationError('INVALID_PARAMS', 'unknown relay argument', 422)
    if 'dry_run' in request and type(request['dry_run']) is not bool:
        raise OperationError('INVALID_PARAMS', 'dry_run must be boolean', 422)
    return ({k: request[k] for k in ('host', 'workspace', 'session_id') if request.get(k) is not None},
            {k: request[k] for k in FIELDS if request.get(k) is not None and not (k in {'queue', 'request_fanout', 'start_if_missing'} and request[k] is False)},
            {'control_version': request['control_version']} if request.get('control_version') is not None else {})


async def legacy(ops, caller, request, *, entry):
    if request.get('dry_run') is True:
        return await preview(ops, caller, request)
    if request.get('confirm') is not True:
        raise OperationError('CONFIRM_REQUIRED', 'relay requires confirm=true', 403)
    target, params, pre = inputs(request)
    op, _ = ops.create(caller, action=ACTION, target=target, params=params, preconditions=pre,
                        idempotency_key=request.get('idempotency_key'), entry=entry, _legacy_session=True)
    await ops.run_due()
    op = await ops.wait(op['operation_id'], 30)
    return {'host': target.get('host'), 'session_id': None, 'sent': None,
            **((op.get('external_refs') or {}).get('relay_result') or {}), **(op.get('result') or {}),
            'operation_id': op['operation_id'], 'operation_status': op['status'], 'operation_error_code': op['error_code'],
            'operation_status_reason': op['status_reason'], 'idempotency_key': op['idempotency_key'],
            'idempotency_enabled': op['idempotency_enabled']}
