"""Observe a fixed owned PM execution with the existing configured verifier.

This produces no TaskService task and never changes session ownership. The
OperationService's original process receipt is the independent verifier source.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import re
import time
from dataclasses import replace
from urllib.parse import urlsplit

from . import (
    controller_authority,
    execution_sources,
    pm_package_handoff,
    pm_source_lineage,
    pm_workspace,
    work_items,
)
from . import pm_work_batch_evidence as evidence
from .operations import ActionDef, OperationError, OperationService
from .task_verifier import ObservedVerifier

ACTION = 'pm.execution.verify'
PROCESS = 'pm.execution.verifier.process'
RECEIPT = 'pm.execution.verifier.receipt'
REQUIRED = frozenset({'observe', 'operate'})
SHA = re.compile('[0-9a-f]{40}')


def _bad(code, message, status=409):
    return OperationError(code, message, status)


def command_identity(argv):
    return 'argv_sha256:' + hashlib.sha256('\0'.join(argv).encode()).hexdigest()


def install(ops):
    if ACTION not in ops.actions:
        ops.register(ActionDef(ACTION, 'operate', 'Verify the exact owned execution with its configured trusted runner',
            _run, _admit, ('workspace_id', 'execution_operation_id'), authorize_existing=_authorize_existing))


def capabilities():
    return {'version': 1, 'action': ACTION, 'profile': 'observed_runner_v1', 'required_scopes': sorted(REQUIRED),
        'caller_commands': False, 'new_task_or_session': False, 'unknown_process_resend': False,
        'reference_fields': ['verification_operation_id', 'candidate_commit', 'tree_hash']}


def _op(ops, oid):
    row = ops.db.execute('SELECT * FROM operations WHERE operation_id=?', (oid,)).fetchone()
    if row is None:
        raise _bad('NOT_FOUND', 'Original execution unavailable', 404)
    return OperationService._decode(row)


def _source(ops, principal, wid, oid, *, write=False):
    if not principal.allows('observe') or write and not principal.allows('operate'):
        raise _bad('FORBIDDEN', 'Verification needs observe and operate', 403)
    pm_workspace.require_member(ops.db, principal, wid, write=write)
    child = _op(ops, oid)
    binding = pm_package_handoff.binding(child)
    if (child['action'] != 'repository.continue' or child['status'] != 'succeeded' or not binding
            or binding['contract']['package_ref']['workspace_id'] != wid
            or not pm_package_handoff.source_visible(ops.db, principal, child)):
        raise _bad('NOT_FOUND', 'Original owned fixed engineering source unavailable', 404)
    from .pm_access import require_visible
    require_visible(ops.db, principal, child)
    if write:
        if not controller_authority.same_controller(controller_authority.binding(ops.db, principal), controller_authority.operation_binding(child)):
            raise _bad('PM_VERIFIER_FOREIGN_CONTROLLER', 'Only the original source controller may execute its verifier', 403)
    return child, binding


def _configured(ops, child):
    daemon = ops.context.get('daemon')
    verifier = getattr(getattr(daemon, 'adapter', None), 'verifier', None)
    if not isinstance(verifier, ObservedVerifier):
        raise _bad('PM_VERIFIER_UNAVAILABLE', 'The existing trusted observed verifier is unavailable')
    project_id = child['params'].get('project_id')
    project = work_items._get_project(ops.db, project_id, active=True)
    # Stable central IDs, or an explicitly stored TaskService project mapping.
    # A similar project name never selects an administrator command.
    key = project_id if project_id in verifier.settings.commands else project.get('task_project')
    argv = verifier.settings.commands.get(key) if key else None
    if not argv:
        raise _bad('PM_VERIFIER_NOT_CONFIGURED', 'No trusted command is configured for this original project')
    host = child['result']['host']
    alias = verifier.settings.ssh_hosts.get(host)
    hc = ops.context['fleet'].config.host(host)
    if alias is None and urlsplit(hc.url).hostname not in {'127.0.0.1', '::1', 'localhost'}:
        raise _bad('PM_VERIFIER_HOST_UNPROVEN', 'Remote execution requires the original host verification SSH mapping')
    return verifier, {'project_id': project_id, 'command_project_key': key, 'host': host,
        'command_digest': evidence.digest(command_identity(argv)), 'transport_digest': evidence.digest([host, hc.url, hc.fingerprint, alias]),
        'timeout_s': verifier.settings.timeout_s}


async def configuration(ops, principal, workspace_id, execution_operation_id):
    child, binding = _source(ops, principal, workspace_id, execution_operation_id)
    base = {'workspace_id': workspace_id, 'execution_operation_id': execution_operation_id,
        'handoff_contract_digest': binding['contract_digest'], 'profile': 'observed_runner_v1',
        'candidate_commit': None, 'tree_hash': None, 'candidate_reason': 'unavailable'}
    try:
        runner, selected = _configured(ops, child)
    except OperationError as exc:
        return {**base, 'available': False, 'reason': exc.code, 'command_digest': None}
    source = execution_sources.resolve(ops, 'execution', execution_operation_id)
    metadata = {'host': selected['host'], 'project': selected['command_project_key'], 'task_id': execution_operation_id}
    try:
        identity = await asyncio.wait_for(runner.identity(metadata, source['location']), 15)
    except asyncio.TimeoutError:
        identity = None
    fresh_child, _ = _source(ops, principal, workspace_id, execution_operation_id)
    if _configured(ops, fresh_child)[1] != selected or execution_sources.resolve(ops, 'execution', execution_operation_id) != source:
        raise _bad('PM_VERIFIER_CONTEXT_CHANGED', 'Original verification source changed during read')
    if identity and identity['clean']:
        base.update(candidate_commit=identity['candidate_commit'], tree_hash=identity['tree_hash'], candidate_reason=None)
    else:
        base['candidate_reason'] = 'candidate_not_clean_or_unavailable'
    return {**base, 'available': True, 'reason': None, 'command_digest': selected['command_digest']}


def _check(ops, principal, target, params, pre):
    if (set(target) != {'workspace_id', 'execution_operation_id'}
            or set(params) != {'candidate_commit', 'tree_hash', 'command_digest'}
            or set(pre) != {'handoff_contract_digest'}
            or any(not isinstance(params.get(key), str) or not SHA.fullmatch(params[key]) for key in ('candidate_commit', 'tree_hash'))):
        raise _bad('INVALID_PARAMS', 'Select an exact original child, full candidate/tree and configured command', 422)
    child, binding = _source(ops, principal, target['workspace_id'], target['execution_operation_id'], write=True)
    _, configured = _configured(ops, child)
    if params['command_digest'] != configured['command_digest'] or pre['handoff_contract_digest'] != binding['contract_digest']:
        raise _bad('PM_VERIFIER_CONTEXT_CHANGED', 'The original configured command or fixed package changed')
    source = execution_sources.resolve(ops, 'execution', child['operation_id'])
    return {'source': source, 'configured': configured, 'handoff_contract_digest': binding['contract_digest'],
        'operation_ids': [child['operation_id']]}


def _admit(ops, principal, target, params, pre):
    context = _check(ops, principal, target, params, pre)
    return {'principal': pm_workspace.principal_binding(principal), 'pm_execution_verification': context,
        'pm_execution_verification_digest': evidence.digest(context), 'pm_lineage': {'workspace_id': target['workspace_id']}}


def _intent(op):
    bound = (op.get('external_refs') or {}).get('admission_binding') or {}
    value = bound.get('pm_execution_verification')
    if not isinstance(value, dict) or evidence.digest(value) != bound.get('pm_execution_verification_digest'):
        raise _bad('PM_VERIFIER_CONTEXT_CHANGED', 'Original verification context is unavailable')
    return value


def _access(ops, principal, op):
    _intent(op)
    child, binding = _source(ops, principal, op['target']['workspace_id'], op['target']['execution_operation_id'])
    if binding['contract_digest'] != _intent(op)['handoff_contract_digest']:
        raise _bad('NOT_FOUND', 'Original verifier package unavailable', 404)
    return child


def operation_visible(db, principal, original):
    try:
        from .pm_quality_reviews import _Reader
        ops = _Reader(db)
        op = _op(ops, original['operation_id'])
        if op['action'] != ACTION:
            return False
        _access(ops, principal, op)
        return True
    except (OperationError, KeyError, TypeError, ValueError):
        return False


def _authorize_existing(ops, principal, op, verb):
    if not operation_visible(ops.db, principal, op):
        raise _bad('NOT_FOUND', 'Original verification sources unavailable', 404)
    if not principal.allows('operate'):
        raise _bad('FORBIDDEN', 'Verification operation control needs operate', 403)


def _current_principal(ops, op):
    from . import api_auth
    original = controller_authority.operation_binding(op)
    admitted = op['external_refs']['admission_binding']['principal']
    if admitted['actor'] != op['actor'] or not (admitted['admin'] or REQUIRED.issubset(admitted['scopes'])):
        raise _bad('PM_VERIFIER_AUTHORITY_UNAVAILABLE', 'Original verification authority is unavailable', 403)
    now = time.time()
    for row in ops.db.execute("""SELECT token_hash,scopes FROM api_principals WHERE actor=?
            AND revoked_at IS NULL AND created_at<=? AND (expires_at IS NULL OR expires_at>?)
            ORDER BY created_at DESC LIMIT 64""", (op['actor'], now, now)):
        scopes = json.loads(row['scopes'])
        if isinstance(scopes, list) and REQUIRED.issubset(scopes):
            principal = api_auth.Principal(op['actor'], REQUIRED, credential_id=row['token_hash'])
            break
    else:
        daemon = ops.context.get('daemon')
        token = vars(daemon).get('_admin_token') if daemon is not None else None
        principal = api_auth.authenticate(ops.db, token, token) if admitted['admin'] and token else None
        if principal is None or principal.actor != op['actor']:
            raise _bad('PM_VERIFIER_AUTHORITY_UNAVAILABLE', 'No current credential permits the original verification', 403)
    if not controller_authority.same_controller(controller_authority.binding(ops.db, principal), original):
        raise _bad('PM_VERIFIER_FOREIGN_CONTROLLER', 'Original verifier controller changed', 403)
    return principal


def _guard(ctx):
    ctx.check_cancel()
    from .pm_lifecycle_gates import require_local
    require_local(ctx.service.db, ctx.operation_id)
    principal = _current_principal(ctx.service, ctx.op)
    if _check(ctx.service, principal, ctx.target, ctx.params, ctx.preconditions) != _intent(ctx.op):
        raise _bad('PM_VERIFIER_CONTEXT_CHANGED', 'Original verifier source, command or host changed')
    pm_source_lineage.require_new_effect(ctx.service, ctx.operation_id)
    from . import pm_maintenance_repairs as repairs
    repairs.require_new_effect(ctx.service, ctx.op)
    remaining = float(_intent(ctx.op)['configured']['timeout_s'])
    for oid in pm_source_lineage.roots(ctx.service.db, 'operation', ctx.operation_id):
        parent = _op(ctx.service, oid)
        if parent['action'] == repairs.ACTION:
            remaining = min(remaining, repairs._record_for(ctx.service, parent)['deadline_at'] - repairs.time.time())
    if remaining <= 0:
        raise _bad('PM_VERIFIER_DEADLINE', 'The original verification investment deadline expired')
    return principal, remaining


def _saved(ops, op):
    row = ops.db.execute("SELECT response FROM operation_steps WHERE operation_id=? AND name=? AND status='succeeded'", (op['operation_id'], RECEIPT)).fetchone()
    if row is None:
        return None
    value = json.loads(row[0])
    if (value['context_digest'] != evidence.digest(_intent(op)) or value['reference'] != {
            'verification_operation_id': op['operation_id'], 'candidate_commit': op['params']['candidate_commit'], 'tree_hash': op['params']['tree_hash']}
            or value['receipt_digest'] != evidence.digest(value['observed'])):
        raise _bad('PM_VERIFIER_RECEIPT_CHANGED', 'Original verifier receipt changed')
    return value


async def _run(ctx):
    async def observe():
        _, remaining = _guard(ctx)
        child = _op(ctx.service, ctx.target['execution_operation_id'])
        original, selected = _configured(ctx.service, child)
        # Only the runner transport is reused. This metadata never creates or
        # adopts a task and never gives a task ownership over the source session.
        runner = ObservedVerifier(replace(original.settings, timeout_s=min(original.settings.timeout_s, remaining)))
        metadata = {'host': selected['host'], 'project': selected['command_project_key'], 'task_id': ctx.operation_id}
        cwd = _intent(ctx.op)['source']['location']
        def before_run():
            _guard(ctx)
        try:
            observed = await asyncio.wait_for(runner.observe(metadata, cwd, before_run=before_run,
                expected_identity={key: ctx.params[key] for key in ('candidate_commit', 'tree_hash')}), remaining)
        except asyncio.TimeoutError:
            observed = None
        if observed is not None and (observed['candidate_commit'] != ctx.params['candidate_commit']
                or observed['tree_hash'] != ctx.params['tree_hash']
                or evidence.digest(observed['command']) != ctx.params['command_digest']):
            observed = None
        value = {'operation_id': ctx.operation_id, 'workspace_id': ctx.target['workspace_id'],
            'execution_operation_id': ctx.target['execution_operation_id'], 'context_digest': evidence.digest(_intent(ctx.op)),
            'reference': {'verification_operation_id': ctx.operation_id, 'candidate_commit': ctx.params['candidate_commit'], 'tree_hash': ctx.params['tree_hash']},
            'observed': observed, 'receipt_digest': evidence.digest(observed), 'created_at': time.time()}
        # Original process outcome survives stop or crash before outer completion.
        return ctx.effect(RECEIPT, lambda: value, receipt_only=True)
    async def reconcile(_):
        return _saved(ctx.service, ctx.op)  # Never run an unknown original process again.
    result = await ctx.step(PROCESS, observe, request={'context_digest': evidence.digest(_intent(ctx.op)), **ctx.params}, reconcile=reconcile)
    return _public(result)


def _public(saved):
    observed = saved['observed']
    return {key: saved[key] for key in ('operation_id', 'workspace_id', 'execution_operation_id', 'reference', 'receipt_digest', 'created_at')} | {
        'profile': 'observed_runner_v1', 'status': 'passed' if observed and observed['exit_code'] == 0 else 'blocked',
        'exit_code': observed['exit_code'] if observed else None, 'semantic_acceptance': 'not_evaluated'}


def get_verification(ops, principal, workspace_id, verification_operation_id):
    op = _op(ops, verification_operation_id)
    if op['action'] != ACTION or op['target']['workspace_id'] != workspace_id:
        raise _bad('NOT_FOUND', 'Original verification unavailable', 404)
    _access(ops, principal, op)
    saved = _saved(ops, op)
    return {'workspace_id': workspace_id, 'operation_status': op['status'], 'verification': _public(saved) if saved else None}


def evaluate(ops, principal, wid, child, condition, reference):
    if (set(reference) != {'verification_operation_id', 'candidate_commit', 'tree_hash'}
            or not isinstance(reference['verification_operation_id'], str)
            or not re.fullmatch('op_[0-9a-f]{32}', reference['verification_operation_id'])):
        raise evidence.bad('Select an exact original execution verifier operation')
    op = _op(ops, reference['verification_operation_id'])
    _access(ops, principal, op)
    saved = _saved(ops, op)
    if (op['action'] != ACTION or op['target'] != {'workspace_id': wid, 'execution_operation_id': child['operation_id']}
            or saved is None or saved['reference'] != reference or op['params']['command_digest'] != condition['command_digest']):
        raise evidence.bad('The verifier must belong to this exact original execution and configured command')
    observed = saved['observed']
    return {'status': 'passed' if observed and observed['exit_code'] == 0 else 'blocked',
        'reason': None if observed and observed['exit_code'] == 0 else 'observed_verification_unproven',
        'reference': reference, 'candidate_commit': reference['candidate_commit'], 'tree_hash': reference['tree_hash'],
        'operation_ids': [child['operation_id'], op['operation_id']], 'receipt_digest': saved['receipt_digest'],
        'command_digest': op['params']['command_digest']}
