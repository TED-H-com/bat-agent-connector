"""Original criterion policy and fixed verification facts for accountable reviews."""
from __future__ import annotations

import json
import re

from . import pm_package_handoff, pm_plans, pm_source_lineage, pm_spec_format, pm_sprints
from . import pm_work_batch_evidence as verification
from .operations import OperationError

MAX_CRITERIA = 200


def bad(message, code='PM_QUALITY_INVALID', status=422):
    return OperationError(code, message, status)


def exact(value, fields):
    if not isinstance(value, dict) or set(value) != set(fields):
        raise bad('Use the exact fixed quality review fields')


def text(value, label, maximum=4000):
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise bad(label + ' must be bounded nonempty text')
    return value


def key(ref):
    if not isinstance(ref, dict):
        raise bad('Select an original acceptance or DoD criterion')
    if ref.get('kind') == 'acceptance':
        exact(ref, ('kind', 'requirement_id', 'input_id'))
    elif ref.get('kind') == 'dod':
        exact(ref, ('kind', 'criterion_id'))
    else:
        raise bad('Select an original acceptance or DoD criterion')
    if any(not isinstance(value, str) or not value or len(value) > 200 for value in ref.values()):
        raise bad('Invalid original criterion identity')
    return verification.digest(ref)


def operation(ops, principal, oid):
    from .pm_access import require_visible
    if not isinstance(oid, str) or not re.fullmatch('op_[0-9a-f]{32}', oid):
        raise bad('Select an exact original execution operation')
    op = ops.get(oid)
    require_visible(ops.db, principal, op)
    return op


def subject(ops, principal, wid, work_item_id, params):
    ref = pm_package_handoff._ref(params['package_ref'])
    if ref['workspace_id'] != wid:
        raise bad('The package must belong to the original workspace')
    package = pm_package_handoff._package(ops, principal, ref)
    doc, original = package['document'], package['document']['context']
    if doc['work_item_id'] != work_item_id:
        raise bad('The package must describe the exact selected WorkItem')
    child = operation(ops, principal, params['execution_operation_id'])
    binding = pm_package_handoff.binding(child)
    if (child['action'] != 'repository.continue' or child['status'] != 'succeeded' or not binding
            or binding['contract']['package_ref'] != ref or binding['contract']['package'] != doc
            or verification.digest(binding['contract']) != binding['contract_digest']):
        raise bad('The original successful handoff must retain this exact immutable package')
    candidate = params['candidate_commit']
    if not isinstance(candidate, str) or not verification.SHA.fullmatch(candidate):
        raise bad('Select one full candidate commit')
    row = ops.db.execute("SELECT response FROM operation_steps WHERE operation_id=? AND name='source.resolve' AND status='succeeded'",
        (child['operation_id'],)).fetchone()
    if row is None:
        raise bad('The original published repository identity receipt is unavailable')
    receipt = json.loads(row[0])
    native = {'provider_origin': receipt['config']['provider_origin'], 'repository': receipt['config']['repository'],
        'repository_id': receipt['repository_id'], 'candidate_commit': candidate}
    dod, dod_ref = None, None
    selected = params['dod_revision_id']
    if original['dod_ref'] is not None and selected != original['dod_ref']['revision_id']:
        raise bad('The original package DoD cannot be replaced')
    if selected is not None:
        row = pm_sprints._artifact(ops.db, wid, selected, 'dod')
        dod = row['document']
        plan = pm_sprints._plan(ops, principal, wid, dod['plan_ref']['revision_id'])
        if plan['scope_revision'] != original['plan_ref']['scope_revision']:
            raise bad('The DoD must retain this original adopted scope')
        dod_ref = {'revision_id': selected, 'document_digest': verification.digest(dod),
            'basis': 'original_package' if original['dod_ref'] else 'explicit_review_selection'}
    criteria, gaps = [], []
    for requirement in doc['requirements']:
        if not requirement['acceptance']:
            gaps.append({'requirement_id': requirement['id'], 'reason': 'original_acceptance_missing'})
        for item in requirement['acceptance']:
            criteria.append({'criterion': {'kind': 'acceptance', 'requirement_id': requirement['id'], 'input_id': item['source_input_id']},
                'text': item['text'], 'original_confirmed': requirement['source_status'] == 'user_confirmed' and item['source_status'] == 'user_confirmed'})
    for item in dod['criteria'] if dod else ():
        criteria.append({'criterion': {'kind': 'dod', 'criterion_id': item['id']}, 'text': item['text'], 'original_confirmed': True})
    if len(criteria) > MAX_CRITERIA:
        raise bad('Select a smaller whole review; original criteria cannot be trimmed', 'PM_QUALITY_LIMIT', 413)
    policy = params['criteria']
    if not isinstance(policy, list) or len(policy) != len(criteria):
        raise bad('The evidence policy must cover every original acceptance and DoD criterion')
    policies = {}
    for value in policy:
        exact(value, ('criterion', 'condition'))
        identity = key(value['criterion'])
        if identity in policies:
            raise bad('An original criterion can appear only once')
        policies[identity] = verification.condition(value['condition'])
    if set(policies) != {key(value['criterion']) for value in criteria}:
        raise bad('Evidence policy may not invent or omit original criteria')
    criteria = [{**value, 'condition': policies[key(value['criterion'])]} for value in criteria]
    return {'package_ref': ref, 'plan_ref': original['plan_ref'], 'work_ref': original['work_ref'],
        'execution_operation_id': child['operation_id'], 'handoff_contract_digest': binding['contract_digest'],
        'native_result': native, 'source_receipt_digest': verification.digest(receipt), 'dod_ref': dod_ref,
        'criteria': criteria, 'gaps': gaps, 'requirement_ids': [value['id'] for value in doc['requirements']],
        'operation_ids': sorted({child['operation_id'], *(row['operation_id'] for row in pm_spec_format.history(ops.db, package))})}


def access(ops, principal, wid, context):
    package = pm_package_handoff._package(ops, principal, context['package_ref'])
    original = package['document']['context']
    if context['plan_ref'] != original['plan_ref'] or context['work_ref'] != original['work_ref']:
        raise bad('The original quality context changed', 'NOT_FOUND', 404)
    child = operation(ops, principal, context['execution_operation_id'])
    binding = pm_package_handoff.binding(child)
    if not binding or binding['contract_digest'] != context['handoff_contract_digest']:
        raise bad('The original engineering handoff is unavailable', 'NOT_FOUND', 404)
    row = ops.db.execute("SELECT response FROM operation_steps WHERE operation_id=? AND name='source.resolve' AND status='succeeded'",
        (child['operation_id'],)).fetchone()
    if not row or verification.digest(json.loads(row[0])) != context['source_receipt_digest']:
        raise bad('The original repository identity is unavailable', 'NOT_FOUND', 404)
    for oid in context['operation_ids']:
        pm_source_lineage.require(ops.db, principal, 'operation', oid)
    ref = context['dod_ref']
    if ref:
        dod = pm_sprints._artifact(ops.db, wid, ref['revision_id'], 'dod')
        if verification.digest(dod['document']) != ref['document_digest']:
            raise bad('The original DoD is unavailable', 'NOT_FOUND', 404)
        pm_plans.get_plan(ops, principal, wid, dod['document']['plan_ref']['revision_id'])
    return child


def _evaluate(ops, principal, wid, context, child, condition, reference):
    proof = verification.evaluate(ops, principal, wid, child, condition, reference)
    if condition['profile'] == 'github_reported_checks_v1':
        integration = operation(ops, principal, reference['integration_operation_id'])
        native = ((integration.get('external_refs') or {}).get('admission_binding') or {}).get('pm_delivery') or {}
        original = context['native_result']
        if any(native.get(field) != original[field] for field in ('provider_origin', 'repository_id')):
            proof = {**proof, 'status': 'blocked', 'reason': 'original_repository_identity_unproven'}
    return proof


def conclusions(ops, principal, wid, context, values):
    if not isinstance(values, list) or len(values) != len(context['criteria']):
        raise bad('Record the whole original acceptance and DoD matrix')
    expected, selected = {key(row['criterion']): row for row in context['criteria']}, {}
    child = access(ops, principal, wid, context)
    for value in values:
        exact(value, ('criterion', 'verdict', 'rationale', 'reference'))
        identity = key(value['criterion'])
        if identity not in expected or identity in selected:
            raise bad('Conclusions may not invent, duplicate or omit original criteria')
        if value['verdict'] not in ('accepted', 'rejected', 'unassessed'):
            raise bad('Record an attributed reviewer verdict, not a supplied quality status')
        text(value['rationale'], 'Criterion rationale', 2000)
        criterion = expected[identity]
        proof = None
        if value['reference'] is not None:
            proof = _evaluate(ops, principal, wid, context, child, criterion['condition'], value['reference'])
        proven = bool(proof and proof['status'] == 'passed'
            and proof['candidate_commit'] == context['native_result']['candidate_commit'])
        status = ('rejected' if value['verdict'] == 'rejected' else 'accepted'
            if value['verdict'] == 'accepted' and proven and criterion['original_confirmed'] else 'unassessed')
        selected[identity] = {**value, 'status': status, 'verification': proof,
            'reason': None if status == 'accepted' else 'reviewer_rejected' if status == 'rejected'
            else 'original_criterion_unconfirmed' if not criterion['original_confirmed']
            else 'reviewer_conclusion_missing' if value['verdict'] == 'unassessed'
            else 'fixed_verification_unproven'}
    matrix = [selected[key(row['criterion'])] for row in context['criteria']]
    def result(rows, *, missing=False):
        return 'rejected' if any(row['status'] == 'rejected' for row in rows) else 'accepted' if rows and not missing and all(row['status'] == 'accepted' for row in rows) else 'unassessed'
    requirements = [{'requirement_id': rid, 'status': result([row for row in matrix if row['criterion'].get('requirement_id') == rid],
        missing=any(gap['requirement_id'] == rid for gap in context['gaps']))} for rid in context['requirement_ids']]
    acceptance = result(requirements)
    dod = result([row for row in matrix if row['criterion']['kind'] == 'dod']) if context['dod_ref'] else 'not_defined'
    return {'criteria': matrix, 'requirements': requirements, 'acceptance': acceptance, 'dod': dod,
        'qualified': acceptance == dod == 'accepted', 'verification_is_semantic_acceptance': False,
        'coverage': 'original_declared_criteria_and_fixed_evidence_policy', 'gaps': context['gaps']}


def proof_access(ops, principal, wid, assessment):
    for item in assessment['criteria']:
        if item['verification']:
            verification.require_sources(ops, principal, wid, item['verification'])


def proof_current(ops, principal, wid, context, assessment):
    child = access(ops, principal, wid, context)
    for item in assessment['criteria']:
        proof = item['verification']
        if item['status'] != 'accepted' or not proof:
            continue
        current = _evaluate(ops, principal, wid, context, child, proof['condition'], proof['reference'])
        if current['status'] != 'passed' or current['receipt_digest'] != proof['receipt_digest']:
            return False
    return True
