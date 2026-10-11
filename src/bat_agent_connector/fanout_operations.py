"""Fixed fanout plans and canonical start children; never re-plan on recovery."""
from __future__ import annotations

import asyncio
import hashlib
import json
import time

from . import api_auth, lifecycle, registry, resource_policy, service, task_control
from . import orchestration_operations as relay
from .errors import BatError, TaskControlRefused
from .operations import (
    RERUN,
    ActionDef,
    AmbiguousOutcome,
    Cancelled,
    NeedsAttention,
    OperationError,
    StepFailed,
    Wait,
)
from .relay import build_relay, parse_fanout, status_footer
from .safety import Audit

ACTIONS = frozenset({'fanout.plan', 'fanout.start'})
PLANNER_FIELDS = {'message', 'channel', 'thread', 'earlier', 'brief', 'max_items'}
START_FIELDS = {'agent', 'model', 'max_items', 'plan'}


def install(ops):
    for name in sorted(ACTIONS):
        if name not in ops.actions:
            ops.register(ActionDef(name, 'start', 'Execute a fixed fanout plan with individual start receipts',
                                   run, lambda o, p, t, v, c, n=name: admit(o, p, t, v, c, n), ('host',),
                                   authorize_existing=authorize_existing))


def scopes(action, target):
    return {'start', *({'operate'} if action == 'fanout.start' and target.get('session_id') else set())}


def require(caller, action, target):
    if not all(caller.allows(s) for s in scopes(action, target)):
        raise OperationError('FORBIDDEN', 'fanout needs start; a source session also needs operate for planner retirement', 403)


def validate(action, target, params, pre):
    if (pre or set(target) - {'host', 'workspace', 'session_id'} or not target.get('host')
            or any(not isinstance(v, str) or not v.strip() or len(v) > 256 for v in target.values())):
        raise OperationError('INVALID_TARGET', 'fanout requires bounded host/workspace or source session strings', 422)
    if action == 'fanout.plan':
        if set(target) != {'host', 'workspace'} or set(params) - PLANNER_FIELDS:
            raise OperationError('INVALID_PARAMS', 'planner accepts workspace and original message fields only', 422)
        relay.validate(target, params, {})
    else:
        if set(params) - START_FIELDS or not (target.get('session_id') or target.get('workspace')):
            raise OperationError('INVALID_PARAMS', 'fanout needs a source session or fixed plan and workspace', 422)
        if bool(target.get('session_id')) == ('plan' in params):
            raise OperationError('INVALID_PARAMS', 'choose one source session or an explicit fixed plan', 422)
        if 'plan' in params:
            items(params['plan'])
        if not isinstance(params.get('agent', 'codex'), str) or params.get('agent', 'codex') not in {'claude', 'codex'}:
            raise OperationError('INVALID_PARAMS', 'agent must be claude or codex', 422)
        if 'model' in params and (not isinstance(params['model'], str) or not params['model'].strip() or len(params['model']) > 256):
            raise OperationError('INVALID_PARAMS', 'model must be a bounded string', 422)
    if 'max_items' in params and (type(params['max_items']) is not int or not 1 <= params['max_items'] <= 16):
        raise OperationError('INVALID_PARAMS', 'max_items must be 1-16', 422)


def items(value):
    if not isinstance(value, list) or not 1 <= len(value) <= 16:
        raise OperationError('INVALID_PARAMS', 'fixed plan must contain 1-16 tasks', 422)
    for i, item in enumerate(value, 1):
        if (not isinstance(item, dict) or set(item) - {'index', 'title', 'prompt', 'area'}
                or type(item.get('index')) is not int or item['index'] != i
                or not isinstance(item.get('title'), str) or not 1 <= len(item['title']) <= 200
                or not isinstance(item.get('prompt'), str) or not item['prompt'].strip() or len(item['prompt']) > 19000
                or 'area' in item and (not isinstance(item['area'], str) or len(item['area']) > 500)):
            raise OperationError('INVALID_PARAMS', 'fixed items require sequential index, bounded title/prompt/area', 422)
    return value


def admit(ops, caller, target, params, pre, action):
    validate(action, target, params, pre)
    require(caller, action, target)
    from .session_start_operations import _tier
    _tier(ops.context['fleet'], target['host'])
    if 'plan' in params and len(params['plan']) > min(ops.context['fleet'].config.safety.max_start_per_call, params.get('max_items', 16)):
        raise OperationError('FANOUT_CAP', 'fixed plan exceeds the configured per-call start cap', 422)
    return {'fanout': {'accepted_scopes': sorted(scopes(action, target))}}


def authorize_existing(ops, caller, op, verb):
    require(caller, op['action'], op['target'])
    if caller.actor != op['actor']:
        raise OperationError('FORBIDDEN', 'fanout controls retain their original actor', 403)


def principal(ctx):
    return api_auth.Principal(ctx.actor, frozenset(ctx.admission_binding['fanout']['accepted_scopes']))


async def resolve(ops, action, target, params):
    fleet, host = ops.context['fleet'], target['host']
    client, hc = fleet.client(host), fleet.config.host(host)
    cap = min(fleet.config.safety.max_start_per_call, params.get('max_items') or 16)
    source = None
    selector = target.get('workspace')
    if action == 'fanout.plan':
        text = lifecycle.PLANNER_PREFACE + build_relay(params['message'], host=host, workspace=selector,
            **{k: params[k] for k in ('channel', 'thread', 'earlier', 'brief') if k in params},
            human_name=fleet.config.human_name, relay_name=fleet.config.relay_name, request_fanout=True, max_items=cap)
        plan = [{'index': 1, 'title': 'fan-out planner', 'prompt': text}]
    elif target.get('session_id'):
        tab, _ = await service._resolve_session(client, target['session_id'])
        sid = tab['id']
        before = relay.record(host, sid)
        read = await service.session_read(fleet, host, sid, last_n=6, max_chars=60000, max_message_chars=40000)
        replies = [m.get('text') or '' for m in reversed(read['messages']) if m.get('role') not in ('user', 'system')]
        block = next((t for t in replies if 'bat-fanout' in t.lower()), None)
        parsed = parse_fanout(block, cap)
        selector = selector or tab.get('workspaceId')
        meta = await service._meta(client, sid)
        source = {'session_id': sid, 'registry': before, 'terminal': {k: tab.get(k) for k in
            ('workspaceId', 'cwd', 'worktreePath', 'worktreeBranch', 'agentPreset', 'sdkSessionId')},
            'runtime': {k: (meta or {}).get(k) for k in ('sdkSessionId', 'cwd')},
            'runtime_loaded': isinstance(meta, dict), 'block': block, 'tasks': parsed['tasks'],
            'block_sha256': hashlib.sha256(block.encode()).hexdigest()}
        if relay.record(host, sid) != before:
            raise StepFailed('FANOUT_SOURCE_CHANGED', 'source incarnation changed while reading its plan')
        plan = [{**i, 'prompt': i['prompt'] + '\n\n' + status_footer(fleet.config.human_name)} for i in parsed['tasks']]
    else:
        plan = items(params['plan'])
    if len(plan) > cap:
        raise StepFailed('FANOUT_CAP', 'fixed plan exceeds the current per-call start cap')
    ws = await service._workspace(client)
    matches = [w for w in ws.get('workspaces', []) if isinstance(w, dict) and selector in (w.get('id'), w.get('name'))]
    if len(matches) != 1 or not isinstance(matches[0].get('folderPath'), str):
        raise StepFailed('FANOUT_WORKSPACE_UNPROVEN', 'fanout destination must resolve to one exact workspace')
    w = matches[0]
    root = await client.invoke('git:getRoot', {'cwd': w['folderPath']})
    branch = await client.invoke('git:branch', {'cwd': w['folderPath']})
    from .session_start_operations import _head
    head = await _head(client.invoke, w['folderPath'])
    if not root or not isinstance(branch, str) or not branch:
        raise StepFailed('FANOUT_WORKSPACE_UNPROVEN', 'workspace Git identity is unavailable')
    for i in plan:
        service_text = i['prompt']
        if not 1 <= len(service_text) <= service.MAX_PROMPT_CHARS:
            raise StepFailed('FANOUT_PROMPT_LIMIT', 'complete fanout prompt exceeds the supported bound')
    return {'route': 'start', 'host': host, 'profile_id': hc.profile_id, 'workspace_id': w['id'], 'workspace': w.get('name'),
        'folder': w['folderPath'], 'origin_root': root, 'source_branch': branch, 'base_commit': head, 'source': source,
        'role': 'planner' if action == 'fanout.plan' else None, 'max_items': cap, 'items': plan,
        'plan_sha256': hashlib.sha256(json.dumps(plan, sort_keys=True, ensure_ascii=False).encode()).hexdigest()}


def child_plan(ctx, link, parent, *, allow_cancel=False):
    plan = relay.receipt(ctx.service, parent['operation_id'], 'fanout.resolve')
    slot = link.get('slot')
    saved = relay.receipt(ctx.service, parent['operation_id'], 'fanout.child.' + str(slot))
    if (not plan or type(slot) is not int or not 1 <= slot <= len(plan['items']) or not saved
            or parent['actor'] != ctx.actor or saved.get('operation_id') != ctx.operation_id
            or {k: ctx.op[k] for k in ('action', 'target', 'params', 'preconditions')} != saved.get('intent')):
        raise TaskControlRefused('FANOUT_BINDING_CHANGED', 'child differs from the fixed fanout receipt')
    relay.require_child_authority(ctx, parent)
    if not allow_cancel and (parent['cancel_requested'] or parent['status'] in {'failed', 'cancelled'}):
        raise TaskControlRefused('FANOUT_CANCEL_REQUESTED', 'fanout cancelled before this frame')
    return plan


def create_child(ctx, plan, item):
    name = 'fanout.child.' + str(item['index'])
    def create():
        intent = {'action': 'session.start', 'target': {'host': plan['host'], 'workspace': plan['workspace_id']},
                  'params': {'agent': 'codex' if plan['role'] else ctx.params.get('agent', 'codex'),
                             'prompt': item['prompt'], 'use_worktree': True,
                             'title': 'fan-out planner' if plan['role'] else f"fanout {item['index']}: {item['title'][:40]}"},
                  'preconditions': {}}
        if ctx.params.get('model'):
            intent['params']['model'] = ctx.params['model']
        child, _ = ctx.service.create(principal(ctx), **intent, idempotency_key=None, _legacy_session=True, entry=ctx.op['entry'])
        ctx.service._merge_refs(child['operation_id'], {'orchestration_child': {'parent_id': ctx.operation_id, 'slot': item['index']}})
        return {'operation_id': child['operation_id'], 'intent': intent}
    return ctx.effect(name, create)


def _stop_guard(ctx, plan):
    ctx.check_cancel()
    ctx.require_controller()
    fleet, source, host = ctx.service.context['fleet'], plan['source'], plan['host']
    from .session_start_operations import _tier
    _tier(fleet, host)
    sid = source['session_id']
    relay.require_record(host, sid, source['registry'])
    if (fleet.config.host(host).profile_id != plan['profile_id'] or relay.record(host, sid) != source['registry']
            or source['registry'].get('role') != 'planner' or source['registry'].get('task_id')
            or task_control.owner_task(fleet, host, sid)):
        raise TaskControlRefused('FANOUT_PLANNER_CHANGED', 'original standalone planner identity changed; retained')
    if any(e.get('host') == host and e.get('session_id') != sid and e.get('status') in {'active', 'starting'}
           and (e.get('failover_of') == sid or e.get('shares_worktree_with') == sid) for e in registry.list_entries(host)):
        raise TaskControlRefused('FANOUT_SUCCESSOR', 'planner has a live successor; retained')
    from .cleanup import guard
    guard(host, session_id=sid, path=source['registry'].get('worktree_path') or source['registry'].get('cwd'))


async def _idle(ctx, plan, read):
    _stop_guard(ctx, plan)
    source, sid = plan['source'], plan['source']['session_id']
    raw = await read('workspace:load', {'profileId': plan['profile_id']})
    try:
        ws = json.loads(raw) if isinstance(raw, str) else raw
    except (ValueError, TypeError):
        ws = None
    if not isinstance(ws, dict) or not isinstance(ws.get('terminals'), list):
        raise TaskControlRefused('FANOUT_PLANNER_CHANGED', 'planner workspace read is unavailable')
    tab, _ = await service._resolve_session(ctx.service.context['fleet'].client(plan['host']), sid, ws)
    if any(tab.get(k) != v for k, v in source['terminal'].items()):
        raise TaskControlRefused('FANOUT_PLANNER_CHANGED', 'planner terminal changed; retained')
    meta = await read('claude:get-session-meta', {'sessionId': sid})
    if meta is not None:
        # Absence is part of the fixed identity. An unloaded source (or a
        # historical receipt without positive loaded evidence) grants no right
        # to stop a runtime which appeared while its children were starting.
        if (source.get('runtime_loaded') is not True or not isinstance(meta, dict)
                or any(meta.get(k) != v for k, v in source['runtime'].items())):
            raise TaskControlRefused('FANOUT_PLANNER_CHANGED', 'planner runtime identity changed; retained')
        state = await read('claude:get-session-state', {'sessionId': sid})
        if (not isinstance(meta, dict) or meta.get('isStreaming') is not False or not isinstance(state, dict)
                or state.get('isStreaming') is not False or any(state.get(k) for k in service.SESSION_WAITING_FIELDS)):
            raise TaskControlRefused('FANOUT_PLANNER_ACTIVE', 'planner is active, waiting, or unreadable; retained')
    _stop_guard(ctx, plan)
    return tab, meta


async def stop_planner(ctx, plan):
    source = plan.get('source')
    kept = {'stopped': False, 'worktree_kept': True, 'next_action': 'batc resource-cleanup'}
    if not source or source['registry'].get('role') != 'planner':
        return None
    host, sid = plan['host'], source['session_id']
    fleet, client = ctx.service.context['fleet'], ctx.service.context['fleet'].client(host)
    async def send():
        async with service._write_lock(host):
            tab, meta = await _idle(ctx, plan, client.invoke)
            if meta is None:
                return {'stopped': True, 'settled_by': 'already_unloaded'}
            grant = await resource_policy.authorize_session(fleet, host, 'session.stop', tab)
            audit = Audit(fleet.config.safety)
            audit.check_rate(host, sid + '#stop')
            base = {'actor': ctx.actor, 'tool': 'api:fanout.start', 'host': host, 'session_id': sid + '#stop'}
            audit.record(**base, channel='claude:stop-session', phase='attempt')
            sent = False
            ctx.set_refs(fanout_stop_sent=False)
            def fence():
                nonlocal sent
                ctx.set_refs(fanout_stop_sent=True)
                sent = True
            try:
                result = await client.invoke('claude:stop-session', {'sessionId': sid}, grant=grant, retry_on_disconnect=False,
                    before_frame=lambda: _idle(ctx, plan, client.guard_read), before_send=lambda: _stop_guard(ctx, plan), on_transport=fence)
                if not isinstance(result, dict) or result.get('ok') is not True:
                    raise AmbiguousOutcome('planner stop reply did not prove acceptance')
            except (BatError, OSError, asyncio.TimeoutError) as exc:
                audit.record(**base, channel='claude:stop-session', phase='result', ok=False)
                if sent:
                    raise AmbiguousOutcome('planner stop was sent without a proven reply') from exc
                raise StepFailed('FANOUT_STOP_NOT_SENT', 'planner stop was not sent; retained for review') from exc
            audit.record(**base, channel='claude:stop-session', phase='result', ok=True)
            return {'stopped': True, 'result': result}
    async def recover(_):
        # Only read. No unknown stop intent ever authorizes a resend.
        if relay.record(host, sid) != source['registry']:
            return None
        meta = await client.invoke('claude:get-session-meta', {'sessionId': sid})
        return {'stopped': True, 'settled_by': 'same_incarnation_unloaded'} if meta is None else None
    try:
        stopped = await ctx.step('planner.stop', send, reconcile=recover)
    except StepFailed as exc:
        return {**kept, 'reason': str(exc), 'code': exc.code}
    if ctx.service._row(ctx.operation_id)['cancel_requested']:
        # Any unknown stop was read back above. A confirmed stop is retained as
        # evidence; cancellation skips fresh capacity bookkeeping.
        return {**kept, **stopped, 'capacity_released': False, 'reason': 'cancelled; capacity retained for reviewed cleanup'}
    async def unloaded():
        if relay.record(host, sid) != source['registry']:
            return {'unloaded': False, 'reason': 'planner incarnation changed; newer record retained'}
        return {'unloaded': await client.invoke('claude:get-session-meta', {'sessionId': sid}) is None}
    async def reread(_):
        return RERUN
    observed = await ctx.step('planner.unloaded', unloaded, reconcile=reread)
    if not observed['unloaded']:
        return {**kept, 'stop_accepted': True, 'capacity_released': False,
                'reason': observed.get('reason', 'runtime is still loaded; capacity retained')}
    async def retire():
        row = registry.get(host, sid) or {}
        try:
            relay.require_record(host, sid, source['registry'])
        except BatError as error:
            return {'capacity_released': False, 'capacity_reason': error.code}
        if relay.record(host, sid) != source['registry'] and (row.get('retirement') or {}).get('operation_id') != ctx.operation_id:
            return {'capacity_released': False, 'capacity_reason': 'generation_changed'}
        if task_control.owner_task(fleet, host, sid):
            return {'capacity_released': False, 'capacity_reason': 'task_owned'}
        if any(e.get('session_id') != sid and e.get('status') in {'active', 'starting'}
               and (e.get('failover_of') == sid or e.get('shares_worktree_with') == sid) for e in registry.list_entries(host)):
            return {'capacity_released': False, 'capacity_reason': 'live_successor'}
        return registry.retire(host, sid, 'stopped', created_at=source['registry']['created_at'], actor=ctx.actor,
                               reason='confirmed fixed fanout planner stop', operation_id=ctx.operation_id, expected=source['registry'])
    return {**kept, **stopped, **await ctx.step('planner.retire', retire, reconcile=reread)}


async def run(ctx):
    async def reread(_):
        return RERUN
    plan = await ctx.step('fanout.resolve', lambda: resolve(ctx.service, ctx.op['action'], ctx.target, ctx.params), reconcile=reread)
    ctx.set_refs(fanout_selection=plan)
    started = []
    stop_exists = ctx.service.db.execute('SELECT 1 FROM operation_steps WHERE operation_id=? AND name=?',
                                        (ctx.operation_id, 'planner.stop')).fetchone()
    for item in plan['items']:
        cancelled = ctx.service._row(ctx.operation_id)['cancel_requested']
        saved = relay.receipt(ctx.service, ctx.operation_id, 'fanout.child.' + str(item['index']))
        if not saved and cancelled:
            raise Cancelled()
        saved = saved or create_child(ctx, plan, item)
        if cancelled:
            ctx.service.cancel(principal(ctx), saved['operation_id'])
        child = ctx.service.get(saved['operation_id'])
        result = child.get('result') or (child.get('external_refs') or {}).get('start_result') or {}
        row = {'task': item['index'], 'title': item['title'], **result, 'operation_id': child['operation_id'],
               'operation_status': child['status'], 'operation_error_code': child['error_code'], 'operation_status_reason': child['status_reason']}
        started.append(row)
        out = {'host': plan['host'], 'workspace': plan['workspace'], 'source_session': (plan.get('source') or {}).get('session_id'),
               'plan': (plan.get('source') or {}).get('tasks', plan['items']), 'started': started, 'count': len(started), 'max_items': plan['max_items']}
        ctx.set_refs(fanout_result=out)
        if child['status'] in {'accepted', 'running', 'uncertain', 'waiting_external', 'waiting_checks'}:
            due = child.get('next_run_at') if child['status'] not in {'accepted', 'running'} else None
            raise Wait('waiting_external', 'reading the fixed fanout child', delay_s=max(1, min(30, (due - time.time()) if due else 1)))
        if child['status'] == 'needs_attention' or any(s['status'] in {'started', 'uncertain'} for s in child['steps']):
            raise NeedsAttention('FANOUT_CHILD_UNSETTLED', 'inspect original child receipts; no unknown frame will be resent')
        if cancelled and not stop_exists:
            raise Cancelled()
        if child['status'] in {'failed', 'cancelled'}:
            raise StepFailed(child['error_code'] or 'FANOUT_CHILD_REFUSED', child['status_reason'] or 'fanout child did not complete')
        if result.get('prompt_sent') is not True:
            raise NeedsAttention('FANOUT_PROMPT_UNPROVEN', 'child did not prove prompt acceptance')
    if plan['role']:
        return {**out, **started[0], 'role': 'planner', 'next': 'session_wait then fanout_from_plan with an explicit key'}
    cleanup = await stop_planner(ctx, plan)
    if cleanup is not None:
        out['planner_cleanup'] = cleanup
    ctx.set_refs(fanout_result=out)
    ctx.check_cancel()
    return out


def resume_children(ops, caller, op):
    plan = relay.receipt(ops, op['operation_id'], 'fanout.resolve')
    if plan and not op['cancel_requested']:
        for item in plan['items']:
            saved = relay.receipt(ops, op['operation_id'], 'fanout.child.' + str(item['index']))
            if saved and ops._row(saved['operation_id'])['status'] == 'needs_attention':
                ops.resume(caller, saved['operation_id'])


def inputs(method, request):
    action = 'fanout.plan' if method == 'fanout_plan_session' else 'fanout.start'
    fields = PLANNER_FIELDS if action == 'fanout.plan' else START_FIELDS
    if set(request) - fields - {'host', 'workspace', 'session_id', 'confirm', 'dry_run', 'idempotency_key'}:
        raise OperationError('INVALID_PARAMS', 'unknown fanout argument', 422)
    if 'dry_run' in request and type(request['dry_run']) is not bool:
        raise OperationError('INVALID_PARAMS', 'dry_run must be boolean', 422)
    return action, {k: request[k] for k in ('host', 'workspace', 'session_id') if request.get(k) is not None}, {
        k: request[k] for k in fields if request.get(k) is not None}


async def legacy(ops, caller, method, request, *, entry):
    action, target, params = inputs(method, request)
    if request.get('dry_run') is True:
        if not caller.allows('observe'):
            raise OperationError('FORBIDDEN', 'fanout preview requires observe', 403)
        validate(action, target, params, {})
        plan = await resolve(ops, action, target, params)
        return {'host': plan['host'], 'workspace': plan['workspace'], 'source_session': (plan['source'] or {}).get('session_id'),
                'plan': (plan['source'] or {}).get('tasks', plan['items']), 'dry_run': True}
    if request.get('confirm') is not True:
        raise OperationError('CONFIRM_REQUIRED', 'fanout apply requires confirm=true', 403)
    op, _ = ops.create(caller, action=action, target=target, params=params, idempotency_key=request.get('idempotency_key'),
                       entry=entry, _legacy_session=True)
    await ops.run_due()
    op = await ops.wait(op['operation_id'], 30)
    return {**((op.get('external_refs') or {}).get('fanout_result') or {}), **(op.get('result') or {}),
        'operation_id': op['operation_id'], 'operation_status': op['status'], 'operation_error_code': op['error_code'],
        'operation_status_reason': op['status_reason'], 'idempotency_key': op['idempotency_key'], 'idempotency_enabled': op['idempotency_enabled']}
