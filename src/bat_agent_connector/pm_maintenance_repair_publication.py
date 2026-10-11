"""One immutable owned publication child per original maintenance repair."""
from __future__ import annotations

import json

from . import pm_maintenance_repair_evidence as evidence
from . import pm_maintenance_repairs as repairs
from .operations import OperationError, Wait

ACTION = 'github.pr.publish'


def schema(journal):
    journal.db.execute('''CREATE TABLE IF NOT EXISTS pm_maintenance_repair_publications (
        parent_id TEXT PRIMARY KEY,operation_id TEXT NOT NULL UNIQUE,request TEXT NOT NULL,
        request_digest TEXT NOT NULL,quality_ref TEXT NOT NULL,quality_digest TEXT NOT NULL)''')
    for verb in ('UPDATE', 'DELETE'):
        journal.db.execute(f'CREATE TRIGGER IF NOT EXISTS pm_maintenance_repair_publications_{verb.lower()}_immutable BEFORE {verb} '
            "ON pm_maintenance_repair_publications BEGIN SELECT RAISE(ABORT,'Repair publication identity is immutable'); END")


def _request(ops, parent, quality_ref):
    source = repairs._child(ops, parent)
    original = evidence.quality_assessment(ops, repairs._guard(ops, parent, scopes=repairs.REQUIRED | {'integrate'}), parent, quality_ref)
    return _frozen_request(parent, source, quality_ref, original['context']['native_result'])


def _frozen_request(parent, source, quality_ref, native):
    intent = repairs._intent(parent)
    number = intent['finding']['native']['number']
    if type(number) is not int or number <= 0:
        raise evidence._bad('An exact positive original Issue number is required')
    return {'action': ACTION, 'target': {'workspace_id': parent['target']['workspace_id'],
        'repository': native['repository'], 'host': source['result']['host'], 'source_operation_id': source['operation_id']},
        'params': {'candidate_sha': native['candidate_commit'],
            'base_ref': intent['rule']['handoff']['params']['source_ref'].removeprefix('refs/heads/'),
            'public_title': 'Maintenance repair for #' + str(number), 'public_body': 'Fixes #' + str(number),
            'draft': True, 'quality_ref': quality_ref},
        'preconditions': {'repository_id': native['repository_id'], 'expected_base_sha': intent['rule']['release']['commit_sha']},
        'idempotency_key': 'pm-repair-publication:' + parent['operation_id']}


def child(ops, parent):
    row = ops.db.execute('SELECT * FROM pm_maintenance_repair_publications WHERE parent_id=?', (parent['operation_id'],)).fetchone()
    if row is None:
        return None
    op, source = repairs._operation(ops, row['operation_id']), repairs._child(ops, parent)
    request, ref = json.loads(row['request']), json.loads(row['quality_ref'])
    # Recover exact original bytes without requiring current investment authority.
    native = {'repository': request['target']['repository'], 'repository_id': request['preconditions']['repository_id'],
              'candidate_commit': request['params']['candidate_sha']}
    bound = ((op.get('external_refs') or {}).get('admission_binding') or {}).get('controller_authority') or {}
    if (request != _frozen_request(parent, source, ref, native)
            or repairs.checks._digest(request) != row['request_digest'] or repairs.checks._digest(ref) != row['quality_digest']
            or any(op.get(key) != value for key, value in request.items()) or op['actor'] != parent['actor']
            or any(bound.get(key) != repairs._intent(parent)['check']['controller'][key] for key in ('server_id', 'controller_id', 'actor'))
            or (op.get('external_refs') or {}).get('pm_maintenance_repair_publication_parent') != parent['operation_id']):
        raise evidence._bad('The publication lost its exact original private repair relation', 'PM_REPAIR_PUBLICATION_CHANGED')
    return op


def require_child(ops, op):
    claim = (op.get('external_refs') or {}).get('pm_maintenance_repair_publication_parent')
    if op['action'] != ACTION:
        if claim is not None:
            raise evidence._bad('An unrelated action claims a repair publication', 'PM_REPAIR_PUBLICATION_CHANGED')
        return
    source_id = op['target'].get('source_operation_id')
    source = ops.db.execute('SELECT parent_id FROM pm_maintenance_repair_children WHERE operation_id=?', (source_id,)).fetchone()
    row = ops.db.execute('SELECT parent_id FROM pm_maintenance_repair_publications WHERE operation_id=?', (op['operation_id'],)).fetchone()
    if source is None and row is None and claim is None:
        return
    if source is None or row is None or claim != row[0] or row[0] != source[0]:
        raise evidence._bad('Repair publication requires its server-composed original child', 'PM_REPAIR_PUBLICATION_CHANGED')
    parent = repairs._operation(ops, row[0])
    saved = child(ops, parent)
    if any(saved.get(key) != op.get(key) for key in ('operation_id', 'action', 'target', 'params', 'preconditions', 'actor', 'idempotency_key')):
        raise evidence._bad('Repair publication request changed', 'PM_REPAIR_PUBLICATION_CHANGED')
    return repairs._guard(ops, parent, scopes=repairs.REQUIRED | {'integrate'})


def published_receipt(ops, parent):
    """Private original receipt validation; public callers still gate all sources."""
    from . import pr_publication
    original = child(ops, parent)
    if original is None:
        return None
    receipt = pr_publication._raw_receipt(ops, original['operation_id'])
    if receipt is None:
        return None
    source = repairs._child(ops, parent)
    if (receipt.get('target_verified') is not True or receipt.get('http_status') != 201
            or receipt.get('repair_parent_id') != parent['operation_id']
            or receipt.get('child_operation_id') != source['operation_id']
            or receipt.get('child_request_digest') != pr_publication._child_digest(source)
            or receipt.get('candidate_sha') != original['params']['candidate_sha']
            or receipt.get('repository_id') != original['preconditions']['repository_id']):
        raise evidence._bad('The original owned publication receipt is unproven', 'PM_REPAIR_OWNED_PR_UNPROVEN')
    return receipt


def finish_for_review(ctx):
    """The policy publication goal is distinct from runtime capacity settlement."""
    def finish():
        parent = repairs._operation(ctx.service, ctx.operation_id)
        original = child(ctx.service, parent)
        receipt = published_receipt(ctx.service, parent)
        if (evidence.policies_for(ctx.service, parent)['delivery']['maximum'] != 'pull_request'
                or original is None or original['status'] != 'succeeded'
                or repairs.checks._unsettled(ctx.service, original) or receipt is None):
            raise evidence._bad('The original publication is not yet settled', 'PM_REPAIR_PUBLICATION_UNSETTLED')
        # repository.continue success acknowledges session start. Neither it nor
        # an interrupt ACK proves the runtime stopped; keep the reservation.
        return {'repair_id': 'pmrp_' + ctx.operation_id[3:], 'outcome': 'published_for_review',
            'publication_operation_id': original['operation_id'], 'native_pr_id': receipt['native_pr_id'],
            'pull_number': receipt['pull_number'], 'creation_receipt_digest': repairs.checks._digest(receipt),
            'repair_fixed': False, 'release_publication': 'not_inferred', 'capacity_held': True,
            'capacity_reason': 'PM_REPAIR_RUNTIME_TERMINATION_UNPROVEN'}
    return ctx.effect('pm.repair.published_for_review', finish, receipt_only=True)


def advance(ctx):
    parent = repairs._operation(ctx.service, ctx.operation_id)
    original = child(ctx.service, parent)
    if original:
        if (evidence.policies_for(ctx.service, parent)['delivery']['maximum'] == 'pull_request'
                and original['status'] == 'succeeded' and not repairs.checks._unsettled(ctx.service, original)):
            return finish_for_review(ctx)
        raise Wait('waiting_external', 'Original PR publication ' + original['status'] + '; awaiting delivery receipts', 15)
    try:
        principal = repairs._guard(ctx.service, parent, scopes=repairs.REQUIRED | {'integrate'})
        evidence._ceiling(evidence.policies_for(ctx.service, parent), 1)
        ref = evidence.selected_quality(ctx.service, parent)
        request = _request(ctx.service, parent, ref)
        evidence.require_quality(ctx.service, principal, parent, ref, candidate_sha=request['params']['candidate_sha'])
        if ACTION not in ctx.service.actions:
            raise evidence._bad('Owned PR publication capability is unavailable', 'PM_REPAIR_PUBLICATION_UNAVAILABLE')
    except OperationError as exc:
        raise Wait('waiting_external', exc.code + ': ' + exc.message, 15) from exc
    def create():
        current = repairs._operation(ctx.service, ctx.operation_id)
        live = repairs._guard(ctx.service, current, scopes=repairs.REQUIRED | {'integrate'})
        if evidence.selected_quality(ctx.service, current) != ref or _request(ctx.service, current, ref) != request:
            raise evidence._bad('The exact original quality selection changed', 'PM_REPAIR_QUALITY_UNPROVEN')
        evidence.require_quality(ctx.service, live, current, ref, candidate_sha=request['params']['candidate_sha'])
        created, fresh = ctx.service.create(live, **request, entry='pm-maintenance-repair-publication')
        if not fresh:
            raise evidence._bad('Publication key exists without its original private relation', 'PM_REPAIR_PUBLICATION_CHANGED')
        ctx.service.db.execute('INSERT INTO pm_maintenance_repair_publications VALUES(?,?,?,?,?,?)',
            (ctx.operation_id, created['operation_id'], repairs.checks._json(request), repairs.checks._digest(request),
             repairs.checks._json(ref), repairs.checks._digest(ref)))
        ctx.service._merge_refs(created['operation_id'], {'pm_maintenance_repair_publication_parent': ctx.operation_id})
        return {'operation_id': created['operation_id']}
    ctx.effect('pm.repair.publication', create)
    raise Wait('waiting_external', 'Original owned PR publication admitted', 1)
