"""Standalone start, with distinct durable carrier/start/tab/initial-prompt effects."""
from __future__ import annotations

import asyncio
import hashlib
import json
import re
import uuid

from . import confinement, registry, resource_policy, service, task_control
from .errlog import log_error
from .errors import BatError, ResourceReadOnly
from .operations import (
    RERUN,
    ActionDef,
    AmbiguousOutcome,
    Cancelled,
    NeedsAttention,
    OperationError,
    StepFailed,
)
from .orchestrate import PRESETS, registry_permission_fields
from .safety import Audit

FIELDS = frozenset({'agent', 'prompt', 'model', 'use_worktree', 'title'})
SHA = re.compile(r'[0-9a-f]{40}')
NAMESPACE = uuid.UUID('a8743f1c-2496-4590-9d02-f4325506e091')


def install(ops):
    if 'session.start' in ops.actions:
        return
    ops.register(ActionDef('session.start', 'start', 'Start one standalone managed session', run, admit,
                           ('host', 'workspace')))


def admit(ops, principal, target, params, pre):
    if set(target) != {'host', 'workspace'} or set(params) - FIELDS or pre:
        raise OperationError('INVALID_PARAMS', 'start accepts only host/workspace and standalone start options', 422)
    if not all(isinstance(target.get(k), str) and target[k].strip() and len(target[k]) <= 256
               for k in ('host', 'workspace')):
        raise OperationError('INVALID_TARGET', 'host and workspace must be non-empty strings', 422)
    agent = params.get('agent', 'claude')
    if not isinstance(agent, str) or agent not in {'claude', 'codex'}:
        raise OperationError('INVALID_PARAMS', 'agent must be claude or codex', 422)
    if 'use_worktree' in params and type(params['use_worktree']) is not bool:
        raise OperationError('INVALID_PARAMS', 'use_worktree must be boolean', 422)
    if params.get('use_worktree') is False:
        raise OperationError('START_WORKTREE_REQUIRED', 'standalone durable start requires a new worktree; shared-folder ownership is not proven', 403)
    for key, limit in (('prompt', 20_000), ('model', 256), ('title', 256)):
        if key in params and (not isinstance(params[key], str) or not params[key].strip() or len(params[key]) > limit):
            raise OperationError('INVALID_PARAMS', f'{key} must be 1-{limit} characters', 422)
    _tier(ops.context['fleet'], target['host'])


def _tier(fleet, host):
    if host not in fleet.config.hosts:
        raise OperationError('UNKNOWN_HOST', 'host is not configured', 404)
    if not fleet.orchestrate_enabled(host):
        raise OperationError('TIER_DISABLED', 'start requires writes and orchestrate', 403)


async def workspaces(ops, principal, request):
    if not principal.allows('observe'):
        raise OperationError('FORBIDDEN', 'workspace discovery requires observe', 403)
    if set(request) - {'host', 'limit'}:
        raise OperationError('INVALID_PARAMS', 'unknown workspace discovery argument', 422)
    host, limit = request.get('host'), request.get('limit', 100)
    fleet = ops.context['fleet']
    if host is not None and (not isinstance(host, str) or host not in fleet.config.hosts):
        raise OperationError('UNKNOWN_HOST', 'host is not configured', 404)
    if type(limit) is not int or not 1 <= limit <= 200:
        raise OperationError('INVALID_PARAMS', 'limit must be 1-200', 422)
    # Only configured hosts, with no probe or mutation and bounded returned rows.
    result = await service.workspaces_list(fleet, host)
    rows = result['workspaces']
    return {**result, 'workspaces': rows[:limit], 'count': min(len(rows), limit), 'has_more': len(rows) > limit}


def _receipt(ctx, name):
    row = ctx.service.db.execute('SELECT response FROM operation_steps WHERE operation_id=? AND name=? AND status=?',
                                 (ctx.operation_id, name, 'succeeded')).fetchone()
    return json.loads(row[0]) if row else None


async def _head(read, cwd):
    logs = await read('git:log', {'cwd': cwd, 'count': 1})
    head = logs[0].get('hash') if isinstance(logs, list) and logs and isinstance(logs[0], dict) else None
    if not isinstance(head, str) or not SHA.fullmatch(head):
        raise StepFailed('START_GIT_UNPROVEN', 'a full Git commit is required')
    return head


def _doc(raw):
    value = json.loads(raw) if isinstance(raw, str) else raw
    if not isinstance(value, dict) or not isinstance(value.get('workspaces'), list):
        raise StepFailed('START_WORKSPACE_UNPROVEN', 'workspace document is unavailable')
    return value


async def _plan(ctx, client):
    p, host = ctx.params, ctx.target['host']
    fleet, selector = ctx.service.context['fleet'], ctx.target['workspace']
    hc = fleet.config.host(host)
    doc = await service._workspace(client)
    rows = [w for w in doc.get('workspaces', []) if isinstance(w, dict)]
    matches = [w for w in rows if selector in (w.get('id'), w.get('name'))]
    if not matches:
        matches = [w for w in rows if selector.casefold() in str(w.get('name', '')).casefold()]
    if len(matches) != 1 or not isinstance(matches[0].get('id'), str):
        raise StepFailed('START_WORKSPACE_UNPROVEN', 'workspace must resolve to one exact identity')
    workspace = matches[0]
    folder = workspace.get('folderPath')
    from .pm_bat_operations import start_carrier
    pm_carrier = await start_carrier(ctx)
    if pm_carrier is not None:
        if folder != pm_carrier['workspace_folder']:
            raise StepFailed('PM_BAT_BINDING_CHANGED', 'The original workspace folder changed')
        folder = pm_carrier['worktree_path']
    root = await client.invoke('git:getRoot', {'cwd': folder}) if isinstance(folder, str) else None
    branch = await client.invoke('git:branch', {'cwd': folder}) if root else None
    if not isinstance(branch, str) or not branch.strip():
        raise StepFailed('START_GIT_UNPROVEN', 'workspace Git branch is unavailable')
    sid = str(uuid.uuid5(NAMESPACE, ctx.operation_id))
    use = p.get('use_worktree', True)
    grant = resource_policy.authorize_new_session(hc, sid, folder=folder, use_worktree=use, git_roots={resource_policy.norm(folder): root})
    agent = p.get('agent', 'claude')
    from .orchestration_operations import child_plan
    parent_plan = child_plan(ctx)
    role = (parent_plan or {}).get('role')
    from .pm_bat_operations import start_profile
    pm_profile = start_profile(ctx)
    if role not in (None, 'planner') or role == 'planner' and agent != 'codex':
        raise StepFailed('START_BINDING_CHANGED', 'invalid internal planner creation binding')
    permissions, scope, record = await confinement.start_decision(fleet, host, agent, planner=role == 'planner' or pm_profile is not None)
    return {'host': host, 'session_id': sid, 'workspace_id': workspace['id'], 'workspace_name': workspace.get('name'),
            'folder': folder, 'origin_root': root, 'source_branch': branch, 'base_commit': await _head(client.invoke, folder),
            'agent': agent, 'preset': PRESETS[(agent, use)], 'use_worktree': use,
            'model': p.get('model') or (hc.codex_model if agent == 'codex' else None), 'title': p.get('title'),
            'permission_policy': hc.default_permission_mode, 'permission_options': permissions, 'write_scope': scope,
            'confinement': record, 'isolation': grant.isolation, 'register_tab': hc.orchestrate_register_tabs,
            'profile_id': hc.profile_id, 'role': role, **({'pm_runtime_profile': pm_profile} if pm_profile else {}),
            **({'pm_carrier': pm_carrier, 'workspace_folder': pm_carrier['workspace_folder']} if pm_carrier else {})}


def _guard(ctx, plan, *, reserved=True):
    ctx.require_controller()
    from .pm_bat_operations import guard_start
    guard_start(ctx, plan)
    from .orchestration_operations import check_child
    check_child(ctx, start_plan=plan)
    ctx.check_cancel()
    fleet, host, sid = ctx.service.context['fleet'], plan['host'], plan['session_id']
    _tier(fleet, host)
    hc = fleet.config.host(host)
    if (hc.default_permission_mode != plan['permission_policy'] or hc.profile_id != plan['profile_id']
            or hc.orchestrate_register_tabs != plan['register_tab']):
        raise StepFailed('START_POLICY_CHANGED', 'the fixed start policy/profile changed')
    task_control.refuse_owned(fleet, host, sid)
    if reserved:
        row = registry.get(host, sid) or {}
        reserved_receipt = _receipt(ctx, 'session.reserve')
        if (row.get('start_operation_id') != ctx.operation_id or row.get('status') in registry.RETIRED
                or (reserved_receipt and row.get('created_at') != reserved_receipt['created_at'])
                or row.get('workspace_id') != plan['workspace_id'] or row.get('origin_cwd') != plan['folder']
                or row.get('role') != plan.get('role')
                or row.get('agent_preset') != plan['preset'] or row.get('write_scope') != plan['write_scope']
                or any(row.get(k) != v for k, v in registry_permission_fields(plan['permission_options']).items())):
            raise StepFailed('START_BINDING_CHANGED', 'the reserved standalone session changed')
        from .cleanup import guard
        guard(host, session_id=sid, path=row.get('worktree_path') or row.get('cwd'), branch=row.get('branch'))


async def _identity(ctx, plan, read, *, cwd=None, branch=None):
    _guard(ctx, plan)
    raw = await read('workspace:load', {'profileId': plan['profile_id']})
    matches = [w for w in _doc(raw)['workspaces'] if isinstance(w, dict) and w.get('id') == plan['workspace_id']]
    if len(matches) != 1 or matches[0].get('folderPath') != plan.get('workspace_folder', plan['folder']):
        raise StepFailed('START_BINDING_CHANGED', 'the fixed workspace folder changed')
    if 'pm_carrier' in plan:
        from .pm_bat_operations import guard_carrier
        await guard_carrier(ctx, plan)
    source_root = await read('git:getRoot', {'cwd': plan['folder']})
    if resource_policy.norm(source_root) != resource_policy.norm(plan['origin_root']):
        raise StepFailed('START_BINDING_CHANGED', 'fixed workspace repository root changed')
    folder = cwd or plan['folder']
    root = await read('git:getRoot', {'cwd': folder})
    hc = ctx.service.context['fleet'].config.host(plan['host'])
    if cwd and plan['use_worktree']:
        row = registry.get(plan['host'], plan['session_id']) or {}
        if (row.get('cwd') != cwd or row.get('worktree_path') != cwd or row.get('branch') != branch
                or resource_policy.norm(root) != resource_policy.norm(cwd)):
            raise StepFailed('START_BINDING_CHANGED', 'created worktree identity changed')
        grant = resource_policy.authorize_new_session(hc, plan['session_id'], folder=plan['folder'],
            use_worktree=True, git_roots={resource_policy.norm(plan['folder']): plan['origin_root']})
        resource_policy.check_new_worktree(grant, hc, plan['folder'], cwd, plan['origin_root'])
        if 'pm_carrier' not in plan:
            tracked = await read('worktree:status', {'sessionId': plan['session_id']})
            if not isinstance(tracked, dict) or tracked.get('worktreePath') != cwd or tracked.get('branchName') != branch:
                raise StepFailed('START_BINDING_CHANGED', 'BAT worktree binding changed')
    else:
        if resource_policy.norm(root) != resource_policy.norm(plan['origin_root']):
            raise StepFailed('START_BINDING_CHANGED', 'source Git root changed')
        resource_policy.authorize_new_session(hc, plan['session_id'], folder=plan['folder'],
            use_worktree=plan['use_worktree'], git_roots={resource_policy.norm(plan['folder']): root})
    if await _head(read, folder) != plan['base_commit'] or await read('git:branch', {'cwd': folder}) != (branch or plan['source_branch']):
        raise StepFailed('START_GIT_CHANGED', 'the fixed Git branch/commit changed')
    _guard(ctx, plan)


async def _invoke(ctx, plan, name, client, channel, params, grant, *, before_frame=None, start=False):
    # The committed false fence precedes every invoke. on_transport persists true
    # before the socket send, so crash recovery may rerun only proven unsent calls.
    ctx.set_refs(**{name + '_sent': False})
    sent = False
    audit = Audit(ctx.service.context['fleet'].config.safety)
    base = {'actor': ctx.actor, 'tool': 'api:session.start', 'host': plan['host'], 'session_id': plan['session_id']}
    audit.check_rate(plan['host'], plan['session_id'] + '#' + name)
    audit.record(**base, channel=channel, phase='attempt')
    def fence():
        nonlocal sent
        ctx.set_refs(**{name + '_sent': True})
        if start:
            registry.update(plan['host'], plan['session_id'], start_sent=True)
        sent = True
    try:
        result = await client.invoke(channel, params, grant=grant, before_send=lambda: _guard(ctx, plan),
                                     before_frame=before_frame, on_transport=fence)
        audit.record(**base, channel=channel, phase='result', ok=True)
        return result
    except Cancelled:
        raise StepFailed('CANCELLED', 'cancelled before the next transport frame') from None
    except (BatError, OSError, asyncio.TimeoutError) as exc:
        log_error('bat-session', 'session.start.failed', exc)
        audit.record(**base, channel=channel, phase='result', ok=False, error=type(exc).__name__)
        if sent:
            raise AmbiguousOutcome(f'{channel} reached transport; outcome requires read-back') from exc
        raise StepFailed(getattr(exc, 'code', None) or 'START_NOT_SENT', f'{channel} was not sent ({type(exc).__name__})') from exc


def _unsent(ctx, name):
    return (ctx.service.get(ctx.operation_id).get('external_refs') or {}).get(name + '_sent') is False


def _carrier(plan, result):
    path = result.get('worktreePath') if isinstance(result, dict) else None
    branch = result.get('branchName') if isinstance(result, dict) else None
    if (not isinstance(path, str) or not isinstance(branch, str) or not branch
            or result.get('sourceBranch') != plan['source_branch']):
        raise NeedsAttention('START_CARRIER_UNPROVEN', 'worktree identity is not proven')
    return {'cwd': path, 'worktree_path': path, 'branch': branch, 'source_branch': result['sourceBranch']}


@registry.start_call
async def run(ctx):
    try:
        return await _run(ctx)
    except (Cancelled, StepFailed, asyncio.CancelledError) as exc:
        # Only positive no-start evidence releases capacity. Unknown external
        # effects stay unresolved; cancellation cannot hide a sent frame. Retain
        # every proven carrier: creation ownership alone does not authorize
        # deleting later commits, uncommitted files or another client's workspace.
        sid, host = str(uuid.uuid5(NAMESPACE, ctx.operation_id)), ctx.target['host']
        reservation = _receipt(ctx, 'session.reserve')
        carrier = _receipt(ctx, 'worktree.create') or _receipt(ctx, 'pm.carrier.bind')
        row = registry.get(host, sid) or {}
        unresolved = ctx.service.db.execute("SELECT 1 FROM operation_steps WHERE operation_id=? AND status IN ('started','uncertain') "
            "AND name IN ('worktree.create','session.start','workspace.register','send')", (ctx.operation_id,)).fetchone()
        if reservation and row.get('start_sent') is False and not unresolved:
            plan = _receipt(ctx, 'source.resolve')
            fields = {'status': 'failed'}
            expected = {'task_id': None, 'role': plan.get('role'), 'status': 'starting', 'start_sent': False,
                        'cwd': plan['folder'], 'worktree_path': None, 'branch': None}
            if carrier:
                pointer = {k: carrier[k] for k in ('cwd', 'worktree_path', 'branch')}
                fields.update(pointer)
                if _receipt(ctx, 'carrier.record'):
                    expected.update(pointer)
            registry.project_start(host, sid, operation_id=ctx.operation_id, created_at=reservation['created_at'],
                expected=expected, fields=fields)
        if isinstance(exc, StepFailed) and exc.code == 'CANCELLED':
            raise Cancelled() from exc
        raise


async def _run(ctx):
    fleet, host = ctx.service.context['fleet'], ctx.target['host']
    client = fleet.client(host)
    async def plan_read():
        return await _plan(ctx, client)
    async def reread(_):
        return RERUN
    plan = await ctx.step('source.resolve', plan_read, reconcile=reread)
    sid, hc = plan['session_id'], fleet.config.host(host)
    ctx.set_refs(host=host, session_id=sid, workspace_id=plan['workspace_id'])
    def creation_grant(carrier=None):
        grant = resource_policy.authorize_new_session(hc, sid, folder=plan['folder'], use_worktree=plan['use_worktree'],
            git_roots={resource_policy.norm(plan['folder']): plan['origin_root']})
        return resource_policy.check_new_worktree(grant, hc, plan['folder'], carrier['cwd'], plan['origin_root']) if carrier and plan['use_worktree'] else grant

    def project(fields, *, carrier=None, status='starting'):
        reservation = _receipt(ctx, 'session.reserve')
        expected = {'status': status, 'start_sent': False if not carrier else True, 'task_id': None, 'role': plan.get('role'),
            'workspace_id': plan['workspace_id'], 'agent_preset': plan['preset'], 'write_scope': plan['write_scope'],
            'cwd': carrier['cwd'] if carrier else plan['folder'],
            **registry_permission_fields(plan['permission_options'])}
        if not registry.project_start(host, sid, operation_id=ctx.operation_id, created_at=reservation['created_at'],
                                      expected=expected, fields=fields):
            raise NeedsAttention('START_BINDING_CHANGED', 'local start projection no longer owns this binding')
        return {'projected': True}

    async with service._write_lock(host):
        async def reserve():
            _guard(ctx, plan, reserved=False)
            registry.reserve(host, {'session_id': sid, 'workspace_id': plan['workspace_id'],
                'workspace_name': plan['workspace_name'], 'agent_preset': plan['preset'],
                'origin_cwd': plan['folder'], 'origin_root': plan['origin_root'], 'cwd': plan['folder'],
                'model': plan['model'], 'title': plan['title'], 'isolation': plan['isolation'],
                'start_sent': False, 'start_operation_id': ctx.operation_id, 'role': plan.get('role'),
                'creates_worktree': plan['use_worktree'] and 'pm_carrier' not in plan,
                'confinement': plan['confinement'], 'write_scope': plan['write_scope'],
                **registry_permission_fields(plan['permission_options'])}, hc.orchestrate_max_sessions)
            return {'session_id': sid, 'created_at': registry.get(host, sid)['created_at']}
        async def reserved(_):
            row = registry.get(host, sid)
            if not row:
                return RERUN
            if row.get('start_operation_id') != ctx.operation_id:
                raise NeedsAttention('START_BINDING_CHANGED', 'reserved ID belongs to another start')
            return {'session_id': sid, 'created_at': row['created_at']}
        if _receipt(ctx, 'session.start') is None:
            registry.claim_unsent(host, sid)
        await ctx.step('session.reserve', reserve, request={'session_id': sid}, reconcile=reserved)
        carrier = {'cwd': plan['folder'], 'worktree_path': None, 'branch': None, 'source_branch': plan['source_branch']}
        if plan.get('pm_carrier'):
            async def bind_carrier():
                from .pm_bat_operations import guard_carrier
                await guard_carrier(ctx, plan)
                return {'cwd': plan['folder'], 'worktree_path': plan['folder'],
                    'branch': plan['source_branch'], 'source_branch': plan['source_branch']}
            carrier = await ctx.step('pm.carrier.bind', bind_carrier,
                request=plan['pm_carrier'], reconcile=reread)
            async def record_pm_carrier():
                return project({k: carrier[k] for k in ('cwd', 'worktree_path', 'branch')})
            await ctx.step('carrier.record', record_pm_carrier, reconcile=reread)
        elif plan['use_worktree']:
            async def create():
                grant = creation_grant()
                async def frame():
                    await _identity(ctx, plan, client.guard_read)
                result = await _invoke(ctx, plan, 'carrier', client, 'worktree:create',
                    {'sessionId': sid, 'cwd': plan['folder'], 'installPnpm': False, 'baseBranch': plan['source_branch']},
                    grant, before_frame=frame)
                if not isinstance(result, dict) or result.get('success') is not True:
                    raise NeedsAttention('START_CARRIER_UNPROVEN', 'worktree create reply did not prove success')
                carrier = _carrier(plan, result)
                try:
                    resource_policy.check_new_worktree(grant, hc, plan['folder'], carrier['cwd'], plan['origin_root'])
                except ResourceReadOnly as exc:
                    raise NeedsAttention('START_CARRIER_UNPROVEN', 'created carrier is outside the fixed destination policy') from exc
                return carrier
            async def carrier_read(_):
                if _unsent(ctx, 'carrier'):
                    return RERUN
                result = await client.invoke('worktree:status', {'sessionId': sid})
                if not result:
                    return None
                result = _carrier(plan, result)
                creation_grant(result)
                if await _head(client.invoke, result['cwd']) != plan['base_commit']:
                    raise NeedsAttention('START_GIT_CHANGED', 'created carrier differs from the fixed commit')
                return result
            carrier = await ctx.step('worktree.create', create, request={'session_id': sid, 'cwd': plan['folder'],
                'branch': plan['source_branch'], 'commit': plan['base_commit']}, reconcile=carrier_read)
            async def record_carrier():
                return project({k: carrier[k] for k in ('cwd', 'worktree_path', 'branch')})
            await ctx.step('carrier.record', record_carrier, reconcile=reread)
        options = {'cwd': carrier['cwd'], 'agentPreset': plan['preset'], 'workspaceId': plan['workspace_id'],
                   'workspaceName': plan['workspace_name'], **plan['permission_options']}
        if plan['model']:
            options['model'] = plan['model']
        if plan['use_worktree']:
            options.update(useWorktree=True, worktreePath=carrier['cwd'], worktreeBranch=carrier['branch'])
        if plan.get('pm_runtime_profile'):
            options.update(plan['pm_runtime_profile'])
        def verify_started(meta):
            fatal = (ctx.service.get(ctx.operation_id).get('external_refs') or {}).get('start_mismatch')
            if fatal:
                raise NeedsAttention(fatal, 'a prior start identity mismatch needs explicit review')
            try:
                confinement.guard_start_record(registry.get(host, sid) or {})
                confinement.guard_start_cwd({'cwd': carrier['cwd']}, meta)
                confinement.ensure_confirmed(plan['confinement'], meta, allow_unknown=plan['write_scope'] != 'confined')
                from .pm_bat_operations import confirm_start
                confirm_start(ctx, plan, meta)
            except confinement.ConfinementRefused as exc:
                if exc.code in confinement.START_IDENTITY_MISMATCH_CODES or confinement.verify(plan['confinement'], meta)['status'] == 'mismatch':
                    ctx.set_refs(start_mismatch=exc.code)
                    reservation = _receipt(ctx, 'session.reserve')
                    registry.project_start(host, sid, operation_id=ctx.operation_id, created_at=reservation['created_at'],
                        expected={'status': 'starting', 'start_sent': True, 'task_id': None, 'role': plan.get('role'), 'cwd': carrier['cwd']},
                        fields={'error_code': exc.code,
                                'confinement': confinement.confirm(plan['confinement'], meta)})
                raise NeedsAttention(exc.code, str(exc)) from exc

        async def start_frame():
            await _identity(ctx, plan, client.guard_read, cwd=carrier['cwd'], branch=carrier['branch'])
            await confinement.guard_start_frame(fleet, host, plan['confinement'])
            _guard(ctx, plan)
        async def start():
            result = await _invoke(ctx, plan, 'start', client, 'claude:start-session',
                {'sessionId': sid, 'options': options}, creation_grant(carrier), before_frame=start_frame, start=True)
            if not isinstance(result, dict) or result.get('ok') is not True or result.get('sessionId') != sid:
                raise NeedsAttention('START_UNPROVEN', 'start reply did not confirm the exact reserved session')
            return {'host': host, 'session_id': sid, 'cwd': carrier['cwd'], 'started': True}
        async def start_read(_):
            row = registry.get(host, sid) or {}
            if _unsent(ctx, 'start') and row.get('start_sent') is False:
                return RERUN
            meta = await service._meta(client, sid)
            if meta is None:
                return None
            verify_started(meta)
            return {'host': host, 'session_id': sid, 'cwd': carrier['cwd'], 'started': True}
        started = await ctx.step('session.start', start, request={'session_id': sid, 'cwd': carrier['cwd'],
                                 'options': options}, reconcile=start_read)
        ctx.set_refs(start_result={**started, **carrier, 'base_commit': plan['base_commit'],
                                  'agent_preset': plan['preset'], 'workspace': plan['workspace_name']})
        async def confirm():
            meta = await service._meta(client, sid)
            if meta is None:
                raise NeedsAttention('START_CONFIRMATION_UNPROVEN', 'started session identity is not readable')
            verify_started(meta)
            return {'confinement': confinement.confirm(plan['confinement'], meta)}
        confirmed = await ctx.step('session.confirm', confirm, reconcile=reread)
        async def record_start():
            return project({'status': 'active', 'start_sent': True, 'confinement': confirmed['confinement']}, carrier=carrier)
        await ctx.step('session.record', record_start, reconcile=reread)

        tab = None
        if plan['register_tab']:
            terminal = {'id': sid, 'workspaceId': plan['workspace_id'], 'title': plan['title'] or plan['agent'] + ' (orchestrated)',
                'type': 'terminal', 'cwd': carrier['cwd'], 'agentPreset': plan['preset']}
            permissions = registry_permission_fields(plan['permission_options'])
            if permissions['permission_mode_claude']:
                terminal['permissionMode'] = permissions['permission_mode_claude']
            if permissions['agent_params']:
                terminal['agentParams'] = permissions['agent_params']
            if plan['model']:
                terminal['model'] = plan['model']
            if plan['use_worktree']:
                terminal.update(worktreePath=carrier['cwd'], worktreeBranch=carrier['branch'])
            def tab_guard():
                _guard(ctx, plan)
                audit = Audit(fleet.config.safety)
                audit.check_rate(host, sid + '#register')
                audit.record(actor=ctx.actor, tool='api:session.start', host=host, session_id=sid, channel='workspace:save', phase='attempt')

            async def register():
                _guard(ctx, plan)
                try:
                    result = await client.append_workspace_terminal(plan['profile_id'], terminal,
                        grant=resource_policy.authorize_register_tab(host, sid),
                        before_send=tab_guard, before_frame=start_frame)
                except (BatError, OSError, asyncio.TimeoutError) as exc:
                    raise AmbiguousOutcome('tab registration requires identity read-back') from exc
                if result.get('verified_previous_terminals_kept') is False:
                    ctx.set_refs(tab_conflict=True)
                    raise NeedsAttention('START_TAB_CONFLICT', 'workspace read-back did not preserve previous identities')
                if result.get('appended') or result.get('reason') == 'already present':
                    verified = await tab_read({})
                    if verified is None:
                        raise NeedsAttention('START_TAB_UNPROVEN', 'registered terminal is no longer readable')
                    return {**result, **verified}
                return result
            async def tab_read(_):
                if (ctx.service.get(ctx.operation_id).get('external_refs') or {}).get('tab_conflict'):
                    raise NeedsAttention('START_TAB_CONFLICT', 'previous workspace identity loss needs explicit review')
                doc = await service._workspace(client)
                found = [t for t in doc.get('terminals', []) if isinstance(t, dict) and t.get('id') == sid]
                if not found:
                    return None
                if len(found) != 1 or any(found[0].get(k) != v for k, v in terminal.items()):
                    raise NeedsAttention('START_TAB_MISMATCH', 'registered terminal differs from the fixed session')
                return {'appended': True, 'settled_by': 'exact_terminal'}
            tab = await ctx.step('workspace.register', register, request={'terminal': terminal}, reconcile=tab_read)
            async def record_tab():
                return project({'tab_registered': bool(tab.get('appended'))}, carrier=carrier, status='active')
            await ctx.step('workspace.record', record_tab, reconcile=reread)

        prompt = ctx.params.get('prompt')
        mid = 'batc-' + ctx.operation_id
        sent = None
        if prompt is not None:
            async def send_frame():
                await _identity(ctx, plan, client.guard_read, cwd=carrier['cwd'], branch=carrier['branch'])
                meta = await client.guard_read('claude:get-session-meta', {'sessionId': sid})
                confinement.guard_start_cwd({'cwd': carrier['cwd']}, meta)
                confinement.guard_loaded(host, sid, meta)
                confinement.ensure_confirmed(confirmed['confinement'], meta, allow_unknown=False)
                if meta.get('isStreaming') is not False:
                    raise StepFailed('START_PROMPT_BUSY', 'initial prompt requires positively idle loaded session')
                _guard(ctx, plan)
            async def send():
                result = await _invoke(ctx, plan, 'prompt', client, 'claude:send-message',
                    {'sessionId': sid, 'prompt': prompt, 'clientMessageId': mid}, creation_grant(carrier), before_frame=send_frame)
                accepted = result.get('accepted', result.get('ok')) if isinstance(result, dict) else None
                if type(accepted) is not bool:
                    raise NeedsAttention('START_PROMPT_UNPROVEN', 'initial prompt reply lacks acceptance evidence')
                if accepted is False:
                    raise StepFailed('NOT_ACCEPTED', 'BAT rejected the initial prompt')
                if plan['agent'] == 'claude':
                    registry.record_turn(host, sid, mid, queued=False, baseline_turns=None)
                return {'host': host, 'session_id': sid, 'message_id': mid, 'accepted': True}
            async def send_read(_):
                if _unsent(ctx, 'prompt'):
                    return RERUN
                if plan['agent'] != 'claude':
                    return None
                if registry.get_turn(host, sid, mid):
                    return {'host': host, 'session_id': sid, 'message_id': mid, 'accepted': True, 'settled_by': 'accepted_turn_record'}
                state = await service._live_state(client, sid, 'claude', await service._meta(client, sid))
                if any(isinstance(m, dict) and m.get('role') == 'user' and m.get('id') == mid and m.get('content') == prompt
                       for m in (state or {}).get('messages', [])):
                    return {'host': host, 'session_id': sid, 'message_id': mid, 'accepted': True, 'settled_by': 'exact_echo'}
                return None
            sent = await ctx.step('send', send, request={'message_id': mid, 'text_sha256': hashlib.sha256(prompt.encode()).hexdigest()},
                                  reconcile=send_read)
        return {**started, **carrier, 'agent_preset': plan['preset'], 'workspace': plan['workspace_name'],
                'base_commit': plan['base_commit'], 'base_branch': plan['source_branch'],
                'permissions': plan['write_scope'] or plan['permission_policy'], 'tab': tab, 'prompt_sent': sent is not None,
                'message_id': mid if prompt is not None else None, 'write_scope': plan['write_scope'],
                'confinement': plan['confinement'], 'isolation': plan['isolation']}


async def legacy(ops, principal, request, *, entry):
    if set(request) - FIELDS - {'host', 'workspace', 'confirm', 'idempotency_key'}:
        raise OperationError('INVALID_PARAMS', 'unknown standalone start argument', 422)
    if request.get('confirm') is not True:
        raise OperationError('CONFIRM_REQUIRED', 'session_start requires confirm=true', 403)
    op, _ = ops.create(principal, action='session.start', target={k: request.get(k) for k in ('host', 'workspace')},
        params={k: request[k] for k in FIELDS if request.get(k) is not None}, preconditions={},
        idempotency_key=request.get('idempotency_key'), entry=entry, _legacy_session=True)
    await ops.run_due()
    op = await ops.wait(op['operation_id'], 30)
    refs = op.get('external_refs') or {}
    prompt_sent = False if 'prompt' not in op['params'] else None
    failed_send = next((s for s in op['steps'] if s['name'] == 'send' and s['status'] == 'failed'), None)
    if failed_send and (failed_send.get('error') or {}).get('code') in {'START_NOT_SENT', 'NOT_ACCEPTED'}:
        prompt_sent = False
    return {'host': request['host'], 'session_id': refs.get('session_id'), 'started': None, 'prompt_sent': prompt_sent,
            **(refs.get('start_result') or {}), **(op.get('result') or {}),
            'operation_id': op['operation_id'], 'operation_status': op['status'],
            'operation_error_code': op['error_code'], 'operation_status_reason': op['status_reason'],
            'idempotency_key': op['idempotency_key'], 'idempotency_enabled': op['idempotency_enabled']}
