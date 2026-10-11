"""Private bounded observation and delivery children of one original repair."""
from __future__ import annotations

import asyncio
import json
import time
from contextlib import contextmanager
from contextvars import ContextVar

from . import delivery, deployment, pm_delivery_lineage, pm_source_lineage, pr_delivery
from . import pm_github_observation as observations
from . import pm_maintenance_repair_evidence as evidence
from . import pm_maintenance_repairs as repairs
from . import pm_required_ci as required_ci
from .operations import NeedsAttention, OperationError, Wait

_SELECTING = ContextVar('pm_repair_delivery_selection', default=None)
_HTTP_BUDGET = ContextVar('pm_repair_delivery_http_budget', default=None)
DELIVERY_ACTIONS = frozenset({'github.pr.merge', 'delivery.merge_and_deploy'})
LINK = 'pm_maintenance_repair_delivery_parent'


def schema(journal):
    journal.db.execute('''CREATE TABLE IF NOT EXISTS pm_maintenance_repair_deliveries (
        parent_id TEXT NOT NULL,role TEXT NOT NULL,ordinal INTEGER NOT NULL,operation_id TEXT NOT NULL UNIQUE,
        request TEXT NOT NULL,request_digest TEXT NOT NULL,required_ci TEXT,created_at REAL NOT NULL,
        PRIMARY KEY(parent_id,role,ordinal))''')
    for verb in ('UPDATE', 'DELETE'):
        journal.db.execute(f'CREATE TRIGGER IF NOT EXISTS pm_maintenance_repair_deliveries_{verb.lower()}_immutable BEFORE {verb} '
            "ON pm_maintenance_repair_deliveries BEGIN SELECT RAISE(ABORT,'Repair delivery identity is immutable'); END")


def _rows(ops, parent, role=None):
    return ops.db.execute('SELECT * FROM pm_maintenance_repair_deliveries WHERE parent_id=? '
        'AND (? IS NULL OR role=?) ORDER BY created_at,role,ordinal LIMIT 13',
        (parent['operation_id'], role, role)).fetchall()


def _child(ops, parent, row):
    request = json.loads(row['request'])
    op = repairs._operation(ops, row['operation_id'])
    authority = ((op.get('external_refs') or {}).get('admission_binding') or {}).get('controller_authority') or {}
    allowed = {'observation': {observations.ACTION}, 'delivery': DELIVERY_ACTIONS}
    if (row['role'] not in allowed or op['action'] not in allowed[row['role']]
            or repairs.checks._digest(request) != row['request_digest']
            or any(op.get(key) != value for key, value in request.items())
            or op['actor'] != parent['actor'] or (op.get('external_refs') or {}).get(LINK) != parent['operation_id']
            or any(authority.get(key) != repairs._intent(parent)['check']['controller'][key]
                   for key in ('server_id', 'controller_id', 'actor'))
            or parent['operation_id'] not in pm_source_lineage.roots(ops.db, 'operation', op['operation_id'])):
        raise evidence._bad('The child lost its exact original repair relation', 'PM_REPAIR_DELIVERY_CHANGED')
    if row['role'] == 'delivery':
        selected = ((op.get('external_refs') or {}).get('admission_binding') or {}).get('pm_delivery') or {}
        if (selected.get('pm_repair_required_ci') or {}).get(parent['operation_id']) != json.loads(row['required_ci']):
            raise evidence._bad('The frozen required-CI selection changed', 'PM_REPAIR_DELIVERY_CHANGED')
    return op


def child(ops, parent):
    rows = _rows(ops, parent, 'delivery')
    return _child(ops, parent, rows[0]) if rows else None


def require_child(ops, operation):
    claim = (operation.get('external_refs') or {}).get(LINK)
    if not pm_source_lineage._table(ops.db, 'pm_maintenance_repair_deliveries'):
        if claim:
            raise evidence._bad('Unproven repair delivery relation', 'PM_REPAIR_DELIVERY_CHANGED')
        return None
    row = ops.db.execute('SELECT * FROM pm_maintenance_repair_deliveries WHERE operation_id=?',
        (operation['operation_id'],)).fetchone()
    if row is None and claim is None:
        return None
    if row is None or claim != row['parent_id']:
        raise evidence._bad('Unproven repair delivery relation', 'PM_REPAIR_DELIVERY_CHANGED')
    parent = repairs._operation(ops, row['parent_id'])
    original = _child(ops, parent, row)
    if any(original.get(key) != operation.get(key) for key in ('operation_id', 'action', 'target', 'params', 'preconditions', 'actor', 'idempotency_key')):
        raise evidence._bad('The repair delivery request changed', 'PM_REPAIR_DELIVERY_CHANGED')
    scopes = repairs.REQUIRED | ({'merge', 'deploy'} if operation['action'] == 'delivery.merge_and_deploy'
        else {'merge'} if row['role'] == 'delivery' else set())
    repairs._guard(ops, parent, scopes=scopes)
    return repairs._record_for(ops, parent)['deadline_at']


def child_timeout(ops, op, maximum):
    deadline = require_child(ops, op)
    return min(maximum, max(0.001, deadline - time.time())) if deadline is not None else maximum


def private_child(ops, operation):
    if not pm_source_lineage._table(ops.db, 'pm_maintenance_repair_deliveries'):
        return False
    row = ops.db.execute('SELECT * FROM pm_maintenance_repair_deliveries WHERE operation_id=?',
        (operation['operation_id'],)).fetchone()
    if row is None:
        return False
    _child(ops, repairs._operation(ops, row['parent_id']), row)
    return row['role'] == 'delivery'


def require_native_target(ctx, pr):
    """Recovery retains original native identity without new investment authority."""
    if not private_child(ctx.service, ctx.op):
        return
    from . import pm_maintenance_repair_publication as publication
    parent = repairs._operation(ctx.service, ctx.op['external_refs'][LINK])
    receipt = publication.published_receipt(ctx.service, parent)
    head, base = pr.get('head') or {}, pr.get('base') or {}
    if (pr.get('id') != receipt['native_pr_id'] or pr.get('node_id') != receipt['native_pr_node_id']
            or pr.get('number') != receipt['pull_number'] or head.get('sha') != receipt['candidate_sha']
            or base.get('ref') != receipt['base_ref'] or (base.get('repo') or {}).get('id') != receipt['repository_id']
            or (head.get('repo') or {}).get('id') != receipt['repository_id']):
        raise NeedsAttention('PM_REPAIR_DELIVERY_TARGET_CHANGED', 'Original native PR identity is unavailable for recovery')


@contextmanager
def selecting(ops, parent, reference):
    token = _SELECTING.set((id(ops.db), parent['operation_id'], reference))
    try:
        yield
    finally:
        _SELECTING.reset(token)


def selected(ops, parent):
    value = _SELECTING.get()
    return value[2] if value and value[:2] == (id(ops.db), parent['operation_id']) else None


def require_sources(ops, principal, parent):
    from . import pm_maintenance_repair_publication as publication
    for row in _rows(ops, parent, 'observation'):
        op = _child(ops, parent, row)
        if not observations.operation_visible(ops.db, principal, op):
            raise OperationError('NOT_FOUND', 'Original repair observation sources unavailable', 404)
    for row in _rows(ops, parent, 'delivery'):
        _child(ops, parent, row)
        reference = json.loads(row['required_ci'])
        if reference is not None:
            required_ci.historical(ops, principal, parent, publication.published_receipt(ops, parent), reference)


def projection(ops, parent):
    rows = _rows(ops, parent)
    observed = [_child(ops, parent, row) for row in rows if row['role'] == 'observation']
    selected = [json.loads(row['required_ci']) for row in rows if row['role'] == 'delivery']
    return {'observation_children': [{key: op[key] for key in ('operation_id', 'action', 'status', 'result', 'error_code')}
        for op in observed], 'required_ci_selection': selected[0] if selected else None}


def _selection(ops, principal, parent, receipt):
    policy = evidence.policies_for(ops, parent)
    if not policy['verification']['required_ci']:
        return None
    docs = [doc for doc in evidence._receipts(ops, parent) if doc['selection']['kind'] == 'required_ci']
    if docs:
        reference = docs[-1]['selection']['reference']
        required_ci.require(ops, principal, parent, receipt, reference)
        return reference
    if policy['observation'].get('required_ci_profile') != required_ci.PROFILE:
        raise evidence._bad('An explicit required-CI observation profile is needed', 'PM_REPAIR_REQUIRED_CI_UNPROVEN')
    row = ops.db.execute('SELECT observation_id,document_digest FROM pm_github_observations WHERE workspace_id=? '
        'ORDER BY sequence DESC LIMIT 1', (parent['target']['workspace_id'],)).fetchone()
    reference = {**dict(row), 'native_pr_id': receipt['native_pr_id']} if row else None
    required_ci.require(ops, principal, parent, receipt, reference)
    return reference


def _create(ctx, role, ordinal, request, reference, *, charge_retry=False):
    ops = ctx.service
    def create():
        parent = repairs._operation(ops, ctx.operation_id)
        scopes = repairs.REQUIRED | ({'merge', 'deploy'} if request['action'] == 'delivery.merge_and_deploy'
            else {'merge'} if role == 'delivery' else set())
        principal = repairs._guard(ops, parent, scopes=scopes)
        if charge_retry:
            used = ops.db.execute('SELECT COUNT(*) FROM pm_maintenance_repair_retries WHERE parent_id=?',
                (parent['operation_id'],)).fetchone()[0]
            if used >= repairs._intent(parent)['check']['limits']['retries']:
                raise evidence._bad('The original observation retry budget is exhausted', 'PM_REPAIR_RETRIES_EXHAUSTED')
        if role == 'delivery':
            receipt, _ = evidence.owned_creation(ops, principal, parent)
            if _selection(ops, principal, parent, receipt) != reference:
                raise evidence._bad('The original required-CI selection changed', 'PM_REPAIR_REQUIRED_CI_UNPROVEN')
        with selecting(ops, parent, reference):
            op, fresh = ops.create(principal, **request, entry='pm-maintenance-repair-' + role)
        if not fresh:
            raise evidence._bad('Child key exists without its private relation', 'PM_REPAIR_DELIVERY_CHANGED')
        roots = pm_source_lineage.roots(ops.db, 'operation', repairs._child(ops, parent)['operation_id']) | {parent['operation_id']}
        if len(roots | pm_source_lineage.roots(ops.db, 'operation', op['operation_id'])) > pm_source_lineage.MAX_ROOTS:
            raise evidence._bad('The original source chain exceeds the bound', 'PM_SOURCE_LINEAGE_LIMIT')
        for oid in roots:
            ops.db.execute('INSERT OR IGNORE INTO pm_source_lineage VALUES(?,?,?,?)',
                ('operation', op['operation_id'], oid, parent['operation_id']))
        ops.db.execute('INSERT INTO pm_maintenance_repair_deliveries VALUES(?,?,?,?,?,?,?,?)',
            (ctx.operation_id, role, ordinal, op['operation_id'], repairs.checks._json(request),
             repairs.checks._digest(request), repairs.checks._json(reference), time.time()))
        ops._merge_refs(op['operation_id'], {LINK: ctx.operation_id})
        if charge_retry:
            ops.db.execute('INSERT INTO pm_maintenance_repair_retries VALUES(?,?,?,?,?)',
                (ctx.operation_id, op['operation_id'], repairs.checks._digest(request), 'pm.repair.observation.refresh', time.time()))
        return {'operation_id': op['operation_id']}
    return ctx.effect('pm.repair.' + role + '.' + str(ordinal), create)


def _observe(ctx, parent):
    policy = evidence.policies_for(ctx.service, parent)
    if policy['observation'].get('required_ci_profile') != required_ci.PROFILE:
        raise evidence._bad('An explicit required-CI observation profile is needed', 'PM_REPAIR_REQUIRED_CI_UNPROVEN')
    rows = _rows(ctx.service, parent, 'observation')
    if rows:
        last = _child(ctx.service, parent, rows[-1])
        if repairs.checks._unsettled(ctx.service, last):
            raise Wait('waiting_external', 'Original required-CI observation remains ' + last['status'], 15)
        if time.time() < rows[-1]['created_at'] + policy['schedule']['check_interval_seconds']:
            raise Wait('waiting_external', 'Waiting for the adopted required-CI observation interval', 15)
    if len(rows) > repairs._intent(parent)['check']['limits']['retries']:
        raise evidence._bad('The original observation retry budget is exhausted', 'PM_REPAIR_RETRIES_EXHAUSTED')
    request = {'action': observations.ACTION, 'target': parent['target'],
        'params': {'policy_id': repairs._intent(parent)['check']['workload']['policy_id']},
        'preconditions': parent['preconditions'],
        'idempotency_key': 'pm-repair-observation:' + parent['operation_id'] + ':' + str(len(rows))}
    _create(ctx, 'observation', len(rows), request, None, charge_retry=bool(rows))
    raise Wait('waiting_external', 'Original private required-CI observation admitted', 1)


def _finish(ctx, parent, original):
    def record():
        op = child(ctx.service, parent)
        facts = pm_delivery_lineage._facts(ctx.service, op)
        merged = facts.get('merge') or {}
        deployed = facts.get('deployment') or {}
        ceiling = evidence.policies_for(ctx.service, parent)['delivery']['maximum']
        if (op['operation_id'] != original['operation_id'] or op['status'] != 'succeeded'
                or repairs.checks._unsettled(ctx.service, op) or merged.get('merged') is not True
                or merged.get('verified') is not True or ceiling == 'deploy' and
                (deployed.get('verified') is not True or deployed.get('state') != 'succeeded')):
            raise evidence._bad('Original verified delivery is incomplete', 'PM_REPAIR_DELIVERY_UNPROVEN')
        return {'repair_id': 'pmrp_' + ctx.operation_id[3:], 'outcome': 'deployed' if ceiling == 'deploy' else 'merged',
            'delivery_operation_id': op['operation_id'], 'delivery': facts, 'repair_fixed': False,
            'release_publication': 'not_inferred', 'capacity_held': True,
            'capacity_reason': 'PM_REPAIR_RUNTIME_TERMINATION_UNPROVEN'}
    return ctx.effect('pm.repair.delivered', record, receipt_only=True)


async def advance(ctx):
    parent, ops = repairs._operation(ctx.service, ctx.operation_id), ctx.service
    original = child(ops, parent)
    if original:
        if original['status'] == 'succeeded' and not repairs.checks._unsettled(ops, original):
            return _finish(ctx, parent, original)
        raise Wait('waiting_external', 'Original delivery remains ' + original['status'], 15)
    try:
        policy = evidence.policies_for(ops, parent)
        scopes = repairs.REQUIRED | {'merge'} | ({'deploy'} if policy['delivery']['maximum'] == 'deploy' else set())
        principal = repairs._guard(ops, parent, scopes=scopes)
        receipt, publication = evidence.owned_creation(ops, principal, parent)
        if publication['status'] != 'succeeded' or repairs.checks._unsettled(ops, publication):
            raise Wait('waiting_external', 'Original publication has unsettled effects', 15)
        try:
            reference = _selection(ops, principal, parent, receipt)
        except OperationError as exc:
            if exc.code != 'PM_REPAIR_REQUIRED_CI_UNPROVEN':
                raise
            _observe(ctx, parent)
        if delivery.open_operations(ops, tuple(DELIVERY_ACTIONS), receipt['repository'], receipt['pull_number']):
            raise evidence._bad('An existing delivery must settle first', 'PM_REPAIR_DELIVERY_EXISTS')
        remaining = repairs._record_for(ops, parent)['deadline_at'] - time.time()
        token = _HTTP_BUDGET.set(ctx)
        try:
            preview = await asyncio.wait_for(delivery.pr_preview(ops, receipt['repository'], receipt['pull_number']), min(30, remaining))
        finally:
            _HTTP_BUDGET.reset(token)
        repairs._guard(ops, parent, scopes=scopes)
        if preview['merge_preview']['blocking']:
            raise evidence._bad('The current complete merge preview is blocked', 'PM_REPAIR_DELIVERY_PREVIEW_BLOCKED')
        recipe, extra = None, {}
        if policy['delivery']['maximum'] == 'deploy':
            options = [r for r in ops.context['github_config'].recipes.values()
                if r.repository.lower() == receipt['repository'].lower() and r.environment == policy['delivery']['environment']
                and r.ref == receipt['base_ref']]
            if len(options) != 1:
                raise evidence._bad('Select one configured original repository/branch/environment recipe', 'PM_REPAIR_DEPLOYMENT_UNPROVEN')
            recipe = options[0].name
            token = _HTTP_BUDGET.set(ctx)
            try:
                dep = await asyncio.wait_for(deployment.preview(ops, recipe), min(30, repairs._record_for(ops, parent)['deadline_at'] - time.time()))
            finally:
                _HTTP_BUDGET.reset(token)
            if dep['repository_id'] != receipt['repository_id'] or not dep['readiness']['ready']:
                raise evidence._bad('The configured deployment identity is unavailable', 'PM_REPAIR_DEPLOYMENT_UNPROVEN')
            extra = dep['preconditions']
        request = pr_delivery.merge_envelope(preview['merge_preview'], recipe=recipe)
        request['preconditions'].update(extra)
        request['idempotency_key'] = 'pm-repair-delivery:' + parent['operation_id']
        _create(ctx, 'delivery', 0, request, reference)
    except OperationError as exc:
        raise Wait('waiting_external', exc.code + ': ' + exc.message, 15) from exc
    except asyncio.TimeoutError as exc:
        raise Wait('waiting_external', 'PM_REPAIR_DELIVERY_PREVIEW_TIMEOUT', 15) from exc
    raise Wait('waiting_external', 'Original private delivery admitted', 1)


async def bounded_run(ctx, run):
    """Bound the existing engine; cancellation leaves original steps unresolved."""
    if not private_child(ctx.service, ctx.op):
        return await run()
    selected = ctx.admission_binding['pm_delivery']
    if (ctx.service.context['github_config'].api_url != selected['provider_origin']
            or ctx.service.context['github'].cfg.api_url != selected['provider_origin']):
        raise NeedsAttention('PM_REPAIR_DELIVERY_TARGET_CHANGED', 'Restore the original provider for bounded receipt recovery')
    pending = delivery._sent_write(ctx)
    timeout = 20 if pending else child_timeout(ctx.service, ctx.op, 30)
    token = _HTTP_BUDGET.set(ctx)
    try:
        return await asyncio.wait_for(run(), timeout)
    except asyncio.TimeoutError as exc:
        raise NeedsAttention('PM_REPAIR_DELIVERY_TIMEOUT', 'Original delivery timed out; retain sent effects for readback') from exc
    finally:
        _HTTP_BUDGET.reset(token)


def http_timeout(method):
    """Called on the async owner thread, never from urllib's worker thread."""
    ctx = _HTTP_BUDGET.get()
    if ctx is None:
        return None
    op = repairs._operation(ctx.service, ctx.operation_id)
    if op['action'] == repairs.ACTION:
        repairs._guard(ctx.service, op)
        return min(30, max(0.001, repairs._record_for(ctx.service, op)['deadline_at'] - time.time()))
    if method == 'GET' and delivery._sent_write(ctx):
        return 20  # Recovery is read-only and independently bounded after stop.
    return child_timeout(ctx.service, op, 30)
