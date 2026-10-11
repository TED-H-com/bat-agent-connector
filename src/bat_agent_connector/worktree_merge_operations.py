"""Central, fixed two-carrier merge with fail-closed writer reservations and ACK receipts."""
from __future__ import annotations

import asyncio
import base64
import contextlib
import contextvars
import hashlib
import importlib.resources
import json
import re
import shlex
import sqlite3
import time

from . import orchestration_operations as relay
from . import registry, resource_policy, service, task_control
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
from .orchestrate import MERGED_KINDS, _summ
from .orchestration_operations import receipt, record
from .safety import Audit
from .session_start_operations import _head, _tier

ACTION = 'worktree.merge'
PLAN = 'merge.prepare'
REMOTE = ('rehydrate.frame', 'merge.frame')
SAFE_CHANNELS = {'claude:stop-session', 'claude:interrupt-turn', 'claude:abort-session'}
_OWNER = contextvars.ContextVar('worktree_merge_owner', default=None)
TERMINAL_FIELDS = ('id', 'workspaceId', 'cwd', 'worktreePath', 'worktreeBranch', 'agentPreset')
_UNSET = object()


def install(ops):
    if ACTION not in ops.actions:
        ops.register(ActionDef(ACTION, 'integrate', 'Merge one fixed managed worktree into its managed source checkout',
                               run, admit, ('host', 'session_id'), authorize_existing=authorize_existing))


def available(ops, host):
    runner = ops.context.get('git_runner')
    return bool(runner and runner.available(host))


def _reader_binding(ops, host):
    runner = ops.context.get('git_runner')
    return _digest({'implementation': type(runner).__module__ + '.' + type(runner).__qualname__,
                    'alias': getattr(runner, 'aliases', {}).get(host)})


def capabilities(ops):
    return {'strategy': 'merge', 'requires_verifier_ssh': True, 'requires_bat_git_context': True,
            'hosts': [{'host': h, 'available': available(ops, h),
                       'reasons': [] if available(ops, h) else ['MERGE_GIT_UNAVAILABLE']}
                      for h in ops.context['fleet'].config.hosts]}


def admit(ops, caller, target, params, pre):
    if (set(target) != {'host', 'session_id'} or any(not isinstance(v, str) or not v.strip() or len(v) > 256 for v in target.values())
            or params or pre):
        raise OperationError('INVALID_PARAMS', 'merge requires host/session_id strings, empty params and preconditions', 422)
    _tier(ops.context['fleet'], target['host'])
    if not available(ops, target['host']):
        raise OperationError('MERGE_GIT_UNAVAILABLE', 'merge requires verifier SSH mapped to the BAT Git account/configuration context', 409)


def authorize_existing(ops, caller, op, verb):
    if not caller.allows('integrate') or caller.actor != op['actor']:
        raise OperationError('FORBIDDEN', 'merge controls require integrate and the original actor', 403)


def _document():
    from .cleanup import _registry_document
    doc = _registry_document()
    if not isinstance(doc.get('carrier_writers', {}), dict):
        raise ResourceReadOnly('MERGE_RESERVED', 'carrier writer evidence is unreadable')
    return doc


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def _inside(path, roots):
    path = resource_policy.norm(path)
    return bool(path and any(path == p or path.startswith(p.rstrip('/') + '/') for p in roots))


def _owned(marker):
    owner = _OWNER.get()
    if not owner or owner[0] != registry._caller():
        return False
    _, ctx, plan = owner
    return (marker == _marker(ctx, plan) and receipt(ctx.service, ctx.operation_id, PLAN) == plan
            and ctx.service._row(ctx.operation_id)['action'] == ACTION)


def check_writer(host=None, sid=None, channel=None, *, workdir=None):
    if channel in SAFE_CHANNELS:
        return
    doc = _document()
    current = next((r for r in doc['sessions'] if r.get('host') == host and r.get('session_id') == sid), {})
    paths = [workdir, current.get('worktree_path'), current.get('cwd'), current.get('origin_cwd')]
    for key, marker in doc.get('carrier_writers', {}).items():
        if not _valid_marker(key, marker):
            raise ResourceReadOnly('MERGE_RESERVED', 'carrier reservation is invalid; inspect its original operation')
        if host is not None and marker.get('host') != host:
            continue
        if sid in marker['session_ids'] or any(_inside(p, marker['roots']) for p in paths):
            if not _owned(marker):
                raise ResourceReadOnly('MERGE_RESERVED', 'carrier is reserved by operation ' + marker['operation_id'])


def _valid_marker(key, marker):
    if not isinstance(marker, dict) or set(marker) != {'version', 'operation_id', 'actor', 'journal_path', 'host',
                                                       'roots', 'session_ids', 'plan_sha256'}:
        return False
    if (type(marker['version']) is not int or marker['version'] != 1
            or not isinstance(key, str) or not re.fullmatch(r'op_[0-9a-f]{32}', key) or marker['operation_id'] != key
            or any(not isinstance(marker[k], str) or not marker[k].strip() or len(marker[k]) > 256 for k in ('host', 'actor'))
            or not isinstance(marker['plan_sha256'], str) or not re.fullmatch(r'[0-9a-f]{64}', marker['plan_sha256'])):
        return False
    def path(p):
        return isinstance(p, str) and len(p) <= 4096 and '\0' not in p and resource_policy.norm(p) == p and p != '/'
    return (path(marker['journal_path']) and isinstance(marker['roots'], list) and len(marker['roots']) == 2
            and all(path(p) for p in marker['roots']) and len(set(marker['roots'])) == 2
            and isinstance(marker['session_ids'], list) and bool(marker['session_ids'])
            and all(isinstance(s, str) and s.strip() == s and 1 <= len(s) <= 256 for s in marker['session_ids'])
            and len(set(marker['session_ids'])) == len(marker['session_ids']))


@contextlib.contextmanager
def owning(ctx, plan):
    token = _OWNER.set((registry._caller(), ctx, plan))
    try:
        yield
    finally:
        _OWNER.reset(token)


def _binding(host, sid):
    row = registry.get(host, sid) or {}
    return {**record(host, sid), 'failover_fence': row.get('failover_fence'), 'handoff_status': row.get('handoff_status')}


def _marker(ctx, plan):
    return {'version': 1, 'operation_id': ctx.operation_id, 'actor': ctx.actor,
            'journal_path': str(ctx.service.journal.path.resolve()), 'host': plan['host'],
            'roots': plan['roots'], 'session_ids': sorted(plan['consumers']), 'plan_sha256': _digest(plan)}


async def proof(ops, host, source, destination, roots):
    if not available(ops, host):
        raise StepFailed('MERGE_GIT_UNAVAILABLE', 'configured verifier SSH mapping is required')
    req = {'source': source, 'destination': destination, 'roots': roots}
    code = importlib.resources.files('bat_agent_connector').joinpath('worktree_merge_host.py').read_text()
    arg = base64.b64encode(json.dumps(req).encode()).decode()
    try:
        raw = await ops.context['git_runner'].run(host, 'python3 -c ' + shlex.quote(code) + ' ' + shlex.quote(arg), timeout_s=25)
        if not isinstance(raw, str) or len(raw.encode()) > 32768:
            raise ValueError()
        out = json.loads(raw)
        p = out.get('proof') if out.get('ok') is True else None
        if (not isinstance(p, dict) or p.get('version') != 1 or p.get('kind') not in {'ahead', 'merged', 'diverged'}
                or any(not isinstance(p.get(k), dict) or type(p[k].get('clean')) is not bool for k in ('source', 'destination'))):
            raise ValueError()
        return p
    except (Exception, asyncio.CancelledError) as exc:
        if isinstance(exc, asyncio.CancelledError):
            raise
        raise StepFailed('MERGE_GIT_UNAVAILABLE', 'checked Git root/HEAD/branch/status proof failed; nothing may be merged') from exc


def _registry_consumers(host, roots):
    return {r['session_id'] for r in _document()['sessions'] if r.get('host') == host
            and any(_inside(r.get(k), roots) for k in ('cwd', 'worktree_path', 'origin_cwd', 'origin_root'))}


def _activity(ops, plan, own_op):
    from .cleanup import _consumers
    from .task_cleanup import command_unresolved
    rows = [ops._decode(r) for r in ops.db.execute('SELECT * FROM operations')]
    # base_branch task creation can be in SSH before its carrier path is projected.
    # The durable start command and fixed workspace still precede that effect.
    for row in ops.db.execute('SELECT * FROM tasks WHERE host=?', (plan['host'],)):
        task = dict(row)
        selector = task['workspace']
        # Ordinary task starts support case-insensitive workspace-name substrings.
        # Treat even an ambiguous overlapping alias conservatively while its start is unresolved.
        overlap = (selector in {plan['workspace_id'], plan['workspace_name']}
                   or isinstance(selector, str) and bool(selector) and selector.lower() in (plan['workspace_name'] or '').lower())
        if not overlap:
            continue
        if any(command_unresolved(dict(c), task) for c in ops.db.execute(
                "SELECT * FROM commands WHERE task_id=? AND kind IN ('start_lead','start_reviewer')", (task['task_id'],))):
            raise StepFailed('MERGE_WRITER_UNPROVEN', 'an unresolved task start may already be creating a shared-repository worktree')
    for path in plan['roots']:
        item = {'host': plan['host'], 'path': path, 'kind': 'worktree', 'original_ids': list(plan['consumers']),
                'consumers': [], 'reasons': [], 'registry': {}}
        _consumers(ops, item, rows, {}, own_op=own_op)
        if any(r['code'] in {'COMMAND_UNRESOLVED', 'ACTIVE_EXECUTION'} for r in item['reasons']):
            raise StepFailed('MERGE_WRITER_UNPROVEN', 'a carrier command or operation is unresolved')


def guard(ops, plan, *, ctx=None, reserved=False):
    if ctx:
        ctx.check_cancel()
        ctx.require_controller()
    if not getattr(ops.journal, 'owner_valid', lambda: False)():
        raise StepFailed('MERGE_OWNER_LOST', 'central owner lease is no longer valid')
    fleet, host = ops.context['fleet'], plan['host']
    _tier(fleet, host)
    hc = fleet.config.host(host)
    if hc.profile_id != plan['profile_id'] or list(hc.managed_roots) != plan['managed_roots']:
        raise StepFailed('MERGE_BINDING_CHANGED', 'fixed host profile or managed roots changed')
    if not available(ops, host) or _reader_binding(ops, host) != plan['git_reader_binding']:
        raise StepFailed('MERGE_BINDING_CHANGED', 'configured Git reader binding changed')
    if _registry_consumers(host, plan['roots']) != set(plan['consumers']):
        raise StepFailed('MERGE_CONSUMER_CHANGED', 'carrier consumers changed after preparation')
    from .cleanup import guard as cleanup_guard
    from .task_cleanup import owners
    for task in ops.db.execute('SELECT external_worktree_path FROM tasks WHERE host=?', (host,)):
        if _inside(task['external_worktree_path'], plan['roots']):
            raise StepFailed('TASK_OWNED_CONTROL_REQUIRED', 'a task owns a descendant carrier; use the Task Service')
    for path in plan['roots']:
        owned, _, _ = owners(ops, {'host': host, 'path': path, 'session_id': plan['session_id']}, entries=_document()['sessions'])
        if owned:
            raise StepFailed('TASK_OWNED_CONTROL_REQUIRED', 'a task owns a carrier; use the Task Service')
        cleanup_guard(host, path=path)
    for sid, binding in plan['consumers'].items():
        relay.require_record(host, sid, binding)
        task_control.refuse_owned(fleet, host, sid)
        if _binding(host, sid) != binding:
            raise StepFailed('MERGE_CONSUMER_CHANGED', 'a carrier consumer changed its original owner')
        registry.refuse_start_claim(registry.registry_path(), host, sid)
        row = registry.get(host, sid) or {}
        if (row.get('status') not in {'active', *registry.RETIRED}
                or row.get('status') in registry.RETIRED and plan['runtime'][sid]['loaded'] is not False
                or row.get('start_uncertain') or row.get('handoff_status') in {'pending', 'uncertain'}
                or row.get('failover_fence')):
            raise StepFailed('MERGE_WRITER_UNPROVEN', 'an unsettled or shared successor uses this carrier')
    if reserved:
        marker = _document().get('carrier_writers', {}).get(ctx.operation_id)
        if not _owned(marker):
            raise StepFailed('MERGE_RESERVED', 'original carrier reservation is unavailable')
    _activity(ops, plan, ctx.operation_id if ctx else plan['operation_id'])


async def _idle(read, sid, expected=_UNSET):
    meta = await read('claude:get-session-meta', {'sessionId': sid})
    # Client validation distinguishes explicit result:null from a missing result/error.
    # Never ask get-session-state about a record without cwd: BAT may drop that record.
    if meta is None:
        runtime = {'loaded': False}
        if expected is not _UNSET and runtime != expected:
            raise StepFailed('MERGE_RUNTIME_CHANGED', 'fixed runtime presence changed')
        return runtime
    if not isinstance(meta, dict) or not isinstance(meta.get('cwd'), str) or not meta['cwd'].strip():
        raise StepFailed('MERGE_WRITER_UNPROVEN', 'runtime metadata is neither positively absent nor readable')
    state = await read('claude:get-session-state', {'sessionId': sid})
    if (meta.get('isStreaming') is not False or not isinstance(state, dict) or state.get('isStreaming') is not False
            or any(state.get(k) for k in service.SESSION_WAITING_FIELDS)):
        raise StepFailed('MERGE_WRITER_UNPROVEN', 'every carrier consumer must be positively idle without pending prompts')
    runtime = {'loaded': True, **{k: meta.get(k) for k in ('cwd', 'sdkSessionId')}}
    if expected is not _UNSET and runtime != expected:
        raise StepFailed('MERGE_RUNTIME_CHANGED', 'fixed carrier runtime identity changed')
    return runtime


async def identity(ops, plan, read, *, ctx=None, reserved=False, rehydrated=False):
    guard(ops, plan, ctx=ctx, reserved=reserved)
    raw = await read('workspace:load', {'profileId': plan['profile_id']})
    doc = json.loads(raw) if isinstance(raw, str) else raw
    if not isinstance(doc, dict) or not isinstance(doc.get('terminals'), list):
        raise StepFailed('MERGE_BINDING_CHANGED', 'workspace inventory unavailable')
    terminals = {t['id']: {k: t.get(k) for k in TERMINAL_FIELDS} for t in doc['terminals']
                 if t.get('id') and any(_inside(t.get(k), plan['roots']) for k in ('cwd', 'worktreePath'))}
    if terminals != plan['terminals'] or len([t for t in doc['terminals'] if t.get('id') in terminals]) != len(terminals):
        raise StepFailed('MERGE_CONSUMER_CHANGED', 'terminal inventory changed')
    workspaces = [w for w in doc.get('workspaces', []) if w.get('id') == plan['workspace_id']]
    if len(workspaces) != 1 or workspaces[0].get('folderPath') != plan['destination']:
        raise StepFailed('MERGE_BINDING_CHANGED', 'destination workspace changed')
    for sid in sorted(plan['consumers']):
        await _idle(read, sid, plan['runtime'][sid])
    current = await proof(ops, plan['host'], plan['source'], plan['destination'], plan['managed_roots'])
    if current != plan['git']:
        raise StepFailed('MERGE_GIT_BINDING_CHANGED', 'real Git identity or cleanliness changed before BAT status')
    for key in ('source', 'destination'):
        p = plan['git'][key]
        if (await read('git:getRoot', {'cwd': p['root']}) != p['root']
                or await read('git:branch', {'cwd': p['root']}) != p['branch']
                or await _head(read, p['root']) != p['head']):
            raise StepFailed('MERGE_GIT_BINDING_CHANGED', 'BAT Git identity changed')
    wt = await read('worktree:status', {'sessionId': plan['session_id']})
    if wt is None and plan['rehydrate'] and not rehydrated:
        wt = await read('claude:get-worktree-status', {'sessionId': plan['session_id']})
    if not isinstance(wt, dict) or any(wt.get(k) != v for k, v in plan['worktree'].items()):
        raise StepFailed('MERGE_BINDING_CHANGED', 'fixed BAT worktree branch/source binding changed')
    current = await proof(ops, plan['host'], plan['source'], plan['destination'], plan['managed_roots'])
    if current != plan['git']:
        raise StepFailed('MERGE_GIT_BINDING_CHANGED', 'real Git root, branch, HEAD or cleanliness changed')
    guard(ops, plan, ctx=ctx, reserved=reserved)


async def authorize(fleet, host, tab, *, live=True):
    # Generic worktree authorization asks BAT for a diff. This action establishes
    # the fixed branch/path separately, only after checking the Git config context.
    cls = resource_policy.classify(fleet.config.host(host), tab['id'], terminal=tab, entries=_document()['sessions'])
    observed = await resource_policy.live_check(fleet.client(host), cls, worktree=False) if live and cls.writable else None
    return await resource_policy.authorize_session(fleet, host, ACTION, tab, cls=cls, live=observed)


async def prepare(ctx):
    ops, host = ctx.service, ctx.target['host']
    fleet, client = ops.context['fleet'], ops.context['fleet'].client(host)
    hc = fleet.config.host(host)
    tab, doc = await service._resolve_session(client, ctx.target['session_id'])
    sid = tab['id']
    task_control.refuse_owned(fleet, host, sid)
    await authorize(fleet, host, tab, live=False)
    destination = resource_policy.merge_origin(hc, sid, tab, doc)
    resource_policy.check_merge_destination(hc, destination)
    source = tab.get('worktreePath')
    if not source or not resource_policy.in_managed_root(hc, source):
        raise StepFailed('MERGE_SOURCE_UNPROVEN', 'merge requires a worktree inside a managed root')
    evidence = await proof(ops, host, source, destination, list(hc.managed_roots))
    await authorize(fleet, host, tab)
    wt = await client.invoke('worktree:status', {'sessionId': sid})
    rehydrate = wt is None
    if rehydrate:
        wt = await client.invoke('claude:get-worktree-status', {'sessionId': sid})
    if (not isinstance(wt, dict) or wt.get('worktreePath') != source or wt.get('branchName') != tab.get('worktreeBranch')
            or not wt.get('sourceBranch')):
        raise StepFailed('MERGE_SOURCE_UNPROVEN', 'original worktree/source branch binding is unavailable; no rehydrate guess')
    report = _summ(host, tab, wt, False, 0)
    report['rehydrated'] = False
    plan = {'operation_id': ctx.operation_id, 'host': host, 'session_id': sid, 'source': source, 'destination': destination,
            'roots': [source, destination], 'profile_id': hc.profile_id, 'managed_roots': list(hc.managed_roots),
            'git_reader_binding': _reader_binding(ops, host),
            'workspace_id': tab['workspaceId'],
            'workspace_name': next((w.get('name') for w in doc['workspaces'] if w.get('id') == tab['workspaceId']), None),
            'terminal': {k: tab.get(k) for k in TERMINAL_FIELDS},
            'worktree': {k: wt.get(k) for k in ('worktreePath', 'branchName', 'sourceBranch')},
            'git': evidence, 'rehydrate': rehydrate, 'report': report}
    if evidence['source']['branch'] != wt['branchName'] or evidence['destination']['branch'] != wt['sourceBranch']:
        return {**plan, 'noop': 'main checkout is not on the fixed source branch; BAT would switch branches, so refusing'}
    if wt.get('mergedKind') in MERGED_KINDS or evidence['kind'] == 'merged':
        return {**plan, 'noop': 'already merged'}
    if wt.get('mergedKind') == 'unknown':
        return {**plan, 'noop': 'no new commits on the worktree branch (or unknown state)'}
    if wt.get('mergedKind') != 'ahead' or evidence['kind'] != 'ahead':
        return {**plan, 'noop': 'branch is not strictly ahead; merging could conflict; never forced'}
    if not evidence['source']['clean'] or not evidence['destination']['clean']:
        return {**plan, 'noop': 'worktree or main checkout has uncommitted changes; refusing'}
    plan['terminals'] = {t['id']: {k: t.get(k) for k in TERMINAL_FIELDS} for t in doc['terminals']
                         if t.get('id') and any(_inside(t.get(k), plan['roots']) for k in ('cwd', 'worktreePath'))}
    consumers = set(plan['terminals']) | _registry_consumers(host, plan['roots'])
    plan['consumers'] = {other: _binding(host, other) for other in consumers}
    plan['runtime'] = {}
    for other in sorted(consumers):
        relay.require_record(host, other, plan['consumers'][other])
        terminal = plan['terminals'].get(other) or service.registry_terminal(registry.get(host, other) or {})
        cls = resource_policy.classify(hc, other, terminal=terminal, entries=_document()['sessions'])
        if cls.code or service.agent_kind(terminal.get('agentPreset')) not in {'claude', 'codex'}:
            raise StepFailed('MERGE_CONSUMER_UNPROVEN', 'every carrier consumer needs a managed creation and runtime binding')
        plan['runtime'][other] = await _idle(client.invoke, other)
        if plan['runtime'][other]['loaded'] and plan['runtime'][other]['cwd'] != (terminal.get('worktreePath') or terminal.get('cwd')):
            raise StepFailed('MERGE_RUNTIME_CHANGED', 'consumer runtime is not in its registered carrier')
    await identity(ops, plan, client.invoke, ctx=ctx)
    return plan


def reserve(ctx, plan):
    with registry._locked(registry.registry_path()):
        doc = _document()
        previous = doc.get('carrier_writers', {}).get(ctx.operation_id)
        if previous == _marker(ctx, plan):
            return {'reserved': True}
        if previous:
            raise StepFailed('MERGE_RESERVED', 'existing marker differs from original plan')
        guard(ctx.service, plan, ctx=ctx)
        doc.setdefault('carrier_writers', {})[ctx.operation_id] = _marker(ctx, plan)
        registry._write_document(registry.registry_path(), doc)
    return {'reserved': True}


def release(ctx, plan):
    # No current policy/live Git rereads: this is local ACK/positive-no-send bookkeeping only.
    pending = ctx.service.db.execute("SELECT 1 FROM operation_steps WHERE operation_id=? AND name IN (?,?) AND status IN ('started','uncertain')",
                                    (ctx.operation_id, *REMOTE)).fetchone()
    if pending:
        raise NeedsAttention('MERGE_RESERVED', 'unknown merge effects retain both reservations')
    with registry._locked(registry.registry_path()):
        doc = _document()
        marker = doc.get('carrier_writers', {}).get(ctx.operation_id)
        if marker is None:
            return {'released': True}
        if marker != _marker(ctx, plan) or any(_binding(plan['host'], sid) != binding for sid, binding in plan['consumers'].items()):
            raise NeedsAttention('MERGE_CONSUMER_CHANGED', 'local release lost its original carrier incarnation')
        del doc['carrier_writers'][ctx.operation_id]
        registry._write_document(registry.registry_path(), doc)
    return {'released': True}


async def invoke(ctx, plan, name, channel, params):
    host, sid = plan['host'], plan['session_id']
    fleet, client = ctx.service.context['fleet'], ctx.service.context['fleet'].client(host)
    sent, checked = False, False
    audit = Audit(fleet.config.safety)
    base = {'actor': ctx.actor, 'tool': 'api:' + ACTION, 'host': host, 'session_id': sid + '#merge'}
    async def before_frame():
        nonlocal checked
        await identity(ctx.service, plan, client.guard_read, ctx=ctx, reserved=True,
                       rehydrated=bool(receipt(ctx.service, ctx.operation_id, 'rehydrate.frame')))
        checked = True
    def before_send():
        if not checked:
            raise StepFailed('MERGE_WRITER_UNPROVEN', 'final frame proof is absent')
        guard(ctx.service, plan, ctx=ctx, reserved=True)
    def on_transport():
        nonlocal sent
        sent = True
    try:
        audit.check_rate(host, sid + '#merge', initial_task_send=name == 'merge.frame' and plan['rehydrate'])
        grant = await authorize(fleet, host, plan['terminal'])
        audit.record(**base, channel=channel, phase='attempt', operation_id=ctx.operation_id)
        result = await client.invoke(channel, params, grant=grant, retry_on_disconnect=False,
            before_frame=before_frame, before_send=before_send, on_transport=on_transport)
        if (not isinstance(result, dict) or result.get('success') is not True
                or channel == 'worktree:merge' and any(result.get(k) != v for k, v in
                    {'strategy': 'merge', 'branchName': plan['worktree']['branchName'], 'sourceBranch': plan['worktree']['sourceBranch']}.items())):
            raise AmbiguousOutcome('BAT did not positively acknowledge the fixed effect')
        audit.record(**base, channel=channel, phase='result', ok=True, operation_id=ctx.operation_id)
        return {'channel': channel, 'params': params, 'ack': result}
    except (Exception, asyncio.CancelledError) as exc:
        audit.record(**base, channel=channel, phase='result', ok=False, error=type(exc).__name__)
        if sent:
            raise AmbiguousOutcome('sent merge effect is unproven; retain original receipts and both carriers') from exc
        if isinstance(exc, Cancelled):
            raise StepFailed('CANCELLED', 'cancelled before merge transport') from exc
        raise StepFailed(getattr(exc, 'code', None) or 'MERGE_NOT_SENT', 'merge frame was not sent: ' + str(exc)) from exc


async def remote_step(ctx, plan, name, channel, params):
    try:
        return await ctx.step(name, lambda: invoke(ctx, plan, name, channel, params),
                              request={'channel': channel, 'params': params})
    except (OSError, sqlite3.Error) as exc:
        # step's response write follows its callback exception boundary. Its
        # persisted intent may already have reached BAT; preserve it and the
        # reservation even if the local ACK transaction could not be recorded.
        raise NeedsAttention('MERGE_RECEIPT_UNAVAILABLE',
                             'merge receipt storage is unavailable; retain original steps and carrier reservations, never resend') from exc


async def run(ctx):
    async def reread(_):
        return RERUN
    try:
        plan = await ctx.step(PLAN, lambda: prepare(ctx), reconcile=reread)
    except StepFailed:
        ctx.check_cancel()  # preparation contains no mutation; report requested cancellation truthfully
        raise
    if plan.get('noop'):
        return {**plan['report'], 'merged_now': False, 'reason': plan['noop']}
    async def local_release():
        return release(ctx, plan)
    with owning(ctx, plan):
        async with service._write_lock(plan['host']):
            ack = receipt(ctx.service, ctx.operation_id, 'merge.frame')
            try:
                if not ack:
                    async def acquire():
                        return reserve(ctx, plan)
                    await ctx.step('merge.reserve', acquire, reconcile=reread)
                    ctx.set_refs(host=plan['host'], session_id=plan['session_id'], merge_result={**plan['report'], 'merged_now': None},
                                 carrier_paths=plan['roots'])
                    if plan['rehydrate']:
                        params = {'sessionId': plan['session_id'], 'cwd': plan['destination'], 'worktreePath': plan['source'],
                                  'branchName': plan['worktree']['branchName']}
                        await remote_step(ctx, plan, 'rehydrate.frame', 'worktree:rehydrate', params)
                    params = {'sessionId': plan['session_id'], 'strategy': 'merge'}
                    ack = await remote_step(ctx, plan, 'merge.frame', 'worktree:merge', params)
                await ctx.step('merge.release', local_release, reconcile=reread, receipt_only=True)
            except (StepFailed, Cancelled):
                release(ctx, plan)
                ctx.check_cancel()
                raise
    result = {**plan['report'], 'rehydrated': plan['rehydrate'], 'merged_now': True,
              'result': ack['ack'], 'main_checkout_clean_after': None,
              'note': 'positive BAT merge ACK; no merge commit or post-merge cleanliness is inferred'}
    ctx.set_refs(merge_result=result)
    return result


def cancel(ops, caller, op):
    ops.db.execute('UPDATE operations SET cancel_requested=1,next_run_at=0,updated_at=? WHERE operation_id=?', (time.time(), op['operation_id']))
    pending = ops.db.execute("SELECT 1 FROM operation_steps WHERE operation_id=? AND status IN ('started','uncertain')", (op['operation_id'],)).fetchone()
    if op['status'] == 'needs_attention':
        # Even a completed ACK can precede a local storage exception. Re-enter
        # original receipts; cancellation still forbids every new remote step.
        ops._transition(op['operation_id'], 'running', actor=caller.actor, reason='reading original merge receipts after cancellation')
    elif op['status'] in {'accepted', 'waiting_checks', 'waiting_external'} and not pending:
        ops._transition(op['operation_id'], 'cancelled', actor=caller.actor, reason='cancelled before merge effect')
    ops.kick()
    return ops.get(op['operation_id'], steps=False)


async def legacy(ops, caller, request, *, entry):
    if set(request) - {'host', 'session_id', 'confirm', 'idempotency_key'}:
        raise OperationError('INVALID_PARAMS', 'unsupported merge argument', 422)
    if request.get('confirm') is not True:
        raise OperationError('CONFIRM_REQUIRED', 'merge requires confirm=true', 403)
    op, _ = ops.create(caller, action=ACTION, target={k: request.get(k) for k in ('host', 'session_id')}, params={},
                       idempotency_key=request.get('idempotency_key'), entry=entry, _legacy_session=True)
    await ops.run_due()
    op = await ops.wait(op['operation_id'], 30)
    return {**((op.get('external_refs') or {}).get('merge_result') or {}), **(op.get('result') or {}),
            'operation_id': op['operation_id'], 'operation_status': op['status'], 'operation_error_code': op['error_code'],
            'operation_status_reason': op['status_reason'], 'idempotency_key': op['idempotency_key'], 'idempotency_enabled': op['idempotency_enabled']}
