"""Explicit original qualified assessments for declared requirement prerequisites.

This adapter consumes central quality records. It does not execute work, discover
latest reviews, or reinterpret passing verification as semantic acceptance.
"""
from __future__ import annotations

import json
import re

from . import pm_evidence, pm_package_handoff, pm_plans, pm_quality_projections
from . import pm_work_batch_evidence as evidence
from .operations import OperationError

KIND = 'requirement_acceptance'
PROFILE = 'accepted_requirements_v1'
PARAM = 'pm_requirement_quality_refs'
MAX_REQUIREMENTS = 100
MAX_CRITERIA = 200
MAX_REFERENCE_BYTES = 16 * 1024
PLAN_FIELDS = ('revision_id', 'document_digest', 'scope_revision')


def bad(message):
    return OperationError('PM_REQUIREMENT_ACCEPTANCE_INVALID', message, 422)


def condition(value):
    if (not isinstance(value, dict) or set(value) != {'kind', 'profile', 'package_ref', 'plan_ref', 'requirements'}
            or value['kind'] != KIND or value['profile'] != PROFILE):
        raise bad('Select the registered original requirement acceptance condition')
    pm_package_handoff._ref(value['package_ref'])
    plan = value['plan_ref']
    if (not isinstance(plan, dict) or set(plan) != set(PLAN_FIELDS)
            or not isinstance(plan['revision_id'], str) or not re.fullmatch('pmrev_[0-9a-f]{32}', plan['revision_id'])
            or not isinstance(plan['document_digest'], str) or not evidence.DIGEST.fullmatch(plan['document_digest'])
            or not isinstance(plan['scope_revision'], str) or not re.fullmatch('pmscope_[0-9a-f]{32}', plan['scope_revision'])):
        raise bad('Retain the exact original adopted plan and scope')
    rows, seen, count = value['requirements'], set(), 0
    if not isinstance(rows, list) or not 1 <= len(rows) <= MAX_REQUIREMENTS:
        raise bad('Select a bounded nonempty original requirement set')
    for row in rows:
        if (not isinstance(row, dict) or set(row) != {'requirement_id', 'acceptance_input_ids'}
                or not isinstance(row['requirement_id'], str) or not 1 <= len(row['requirement_id']) <= 200
                or row['requirement_id'] in seen):
            raise bad('Every original requirement must appear exactly once')
        seen.add(row['requirement_id'])
        ids = row['acceptance_input_ids']
        if (not isinstance(ids, list) or not ids or any(not isinstance(v, str) or not 1 <= len(v) <= 200 for v in ids)
                or len(set(ids)) != len(ids)):
            raise bad('Retain all distinct original acceptance input identities')
        count += len(ids)
    if count > MAX_CRITERIA:
        raise bad('The original prerequisite criterion set exceeds its bound')
    return value


def freeze_condition(package, requirement_ids):
    """Read projection/admission helper; selects identities, never an outcome."""
    if (not isinstance(requirement_ids, list) or not requirement_ids or len(requirement_ids) > MAX_REQUIREMENTS
            or any(not isinstance(v, str) for v in requirement_ids) or len(set(requirement_ids)) != len(requirement_ids)):
        raise bad('Select distinct original requirements from the fixed package')
    doc = package['document']
    rows = [{'requirement_id': row['id'], 'acceptance_input_ids': sorted(item['source_input_id'] for item in row['acceptance'])}
        for row in doc['requirements'] if row['id'] in requirement_ids]
    if {row['requirement_id'] for row in rows} != set(requirement_ids):
        raise bad('The fixed package does not contain every selected requirement')
    return condition({'kind': KIND, 'profile': PROFILE,
        'package_ref': {key: package[key] for key in ('workspace_id', 'package_id', 'document_digest')},
        'plan_ref': {key: doc['context']['plan_ref'][key] for key in PLAN_FIELDS}, 'requirements': rows})


def require_package(package, selected):
    condition(selected)
    if freeze_condition(package, [row['requirement_id'] for row in selected['requirements']]) != selected:
        raise bad('The result condition must retain this exact original package and all selected criterion identities')


def _original(ops, principal, wid, child, selected, reference):
    condition(selected)
    if selected['package_ref']['workspace_id'] != wid:
        raise bad('The original acceptance belongs to a different workspace')
    package = pm_package_handoff._package(ops, principal, selected['package_ref'])
    require_package(package, selected)
    binding = pm_package_handoff.binding(child)
    if not binding or binding['contract']['package_ref'] != selected['package_ref']:
        raise bad('The assessment must belong to this original upstream child and package')
    saved = pm_quality_projections.original(ops, principal, wid, reference)
    context = saved['context']
    if (context['execution_operation_id'] != child['operation_id'] or context['package_ref'] != selected['package_ref']
            or {key: context['plan_ref'][key] for key in PLAN_FIELDS} != selected['plan_ref']):
        raise bad('The selected assessment has different original execution, package or plan identities')
    return saved


def evaluate(ops, principal, wid, child, selected, reference):
    saved = _original(ops, principal, wid, child, selected, reference)
    context = saved['context']
    work = pm_evidence._binding(ops.db, wid, context['work_ref']['work_item_id'])
    plan = pm_plans.accepted_plan(ops, principal, wid)['plan']
    scope = plan['adoption']['scope_revision'] if plan and plan['adoption'] else None
    qualified, reasons = False, []
    if not work:
        reasons.append('original_work_binding_unavailable')
    else:
        try:
            current = pm_quality_projections.select(ops, principal, wid, reference,
                work_item_id=work['work_item_id'], binding_id=work['binding_id'], scope_revision=scope,
                dod_revision_id=context['dod_ref']['revision_id'] if context['dod_ref'] else None)
        except OperationError as exc:
            if exc.status != 409:
                raise
            reasons.append(exc.code)
        else:
            qualified = current['qualified']
            reasons.extend(current['applicability_reasons'])
    matrix = saved['assessment']['document']['criteria']
    covered = all(any(item['criterion'] == {'kind': 'acceptance', 'requirement_id': row['requirement_id'], 'input_id': iid}
        and item['status'] == 'accepted' for item in matrix) for row in selected['requirements'] for iid in row['acceptance_input_ids'])
    passed = qualified and covered
    return {'condition': selected, 'reference': reference, 'source_operation_id': child['operation_id'],
        'status': 'passed' if passed else 'blocked', 'reason': None if passed else 'original_requirement_acceptance_unproven',
        'applicability_reasons': reasons, 'candidate_commit': reference['candidate_commit'],
        'requirement_ids': [row['requirement_id'] for row in selected['requirements']],
        'semantic_acceptance': 'accepted' if passed else 'unassessed',
        'receipt_digest': evidence.digest({'context_digest': saved['context_digest'], 'reference': reference, 'condition': selected}),
        'operation_ids': sorted({child['operation_id'], saved['assessment']['operation_id'], *context['operation_ids']}),
        'quality_context_digest': saved['context_digest'], 'coverage': 'original_complete_qualified_review_and_selected_requirements'}


def require_sources(ops, principal, wid, proof):
    saved = pm_quality_projections.original(ops, principal, wid, proof['reference'])
    if saved['context_digest'] != proof['quality_context_digest']:
        raise OperationError('NOT_FOUND', 'Original requirement acceptance sources unavailable', 404)


def _dependencies(ops, principal, downstream_package, references, *, current):
    if current:
        return _dependency_sources(ops, principal, downstream_package, references, current=True)
    from .pm_source_reads import check, scope
    try:
        doc = downstream_package['document']
        key = evidence.digest([downstream_package['workspace_id'], doc['requirements'],
            doc['context']['plan_ref'], references])
    except (KeyError, TypeError, ValueError, RecursionError):
        # Preserve the original validation and errors for malformed identities.
        return _dependency_sources(ops, principal, downstream_package, references, current=False)
    with scope(ops.db, principal) as state:
        def visible():
            _dependency_sources(ops, principal, downstream_package, references, current=False)
            return True
        if ('requirement_sources', key) in state['active']:
            visible()
        elif not check(ops.db, principal, 'requirement_sources', key, visible):
            raise OperationError('NOT_FOUND', 'Original prerequisite sources unavailable', 404)
    # Historical access never infers current applicability or acceptance.
    return []


def _dependency_sources(ops, principal, downstream_package, references, *, current):
    """Return proven prerequisite IDs; absent identities remain unassessed.

    Readiness calls this only for explicitly supplied identity references. The
    same map must be frozen into the handoff and rechecked at the final frame.
    """
    doc = downstream_package['document']
    needed = {rid for row in doc['requirements'] for rid in row['depends_on']}
    if references is None:
        return []
    try:
        size = len(json.dumps(references, ensure_ascii=False, allow_nan=False).encode('utf-8'))
    except (TypeError, ValueError, UnicodeError, RecursionError) as exc:
        raise bad('Prerequisite references must be bounded JSON identities') from exc
    if size > MAX_REFERENCE_BYTES:
        raise OperationError('PM_REQUIREMENT_ACCEPTANCE_LIMIT', 'Original prerequisite references exceed 16 KiB', 413)
    if (not isinstance(references, dict) or len(references) > MAX_REQUIREMENTS or set(references) - needed):
        raise bad('Select only declared original prerequisite requirements')
    passed, cached = [], {}
    original_plan = {key: doc['context']['plan_ref'][key] for key in PLAN_FIELDS}
    from .pm_access import require_visible
    for rid, value in references.items():
        if not isinstance(value, dict) or set(value) != {'condition', 'reference', 'execution_operation_id'}:
            raise bad('Prerequisites select original identities, never supplied quality flags')
        selected = condition(value['condition'])
        if selected['plan_ref'] != original_plan or rid not in {row['requirement_id'] for row in selected['requirements']}:
            raise bad('Prerequisite acceptance must cover this original plan requirement')
        oid = value['execution_operation_id']
        if not isinstance(oid, str) or not re.fullmatch('op_[0-9a-f]{32}', oid):
            raise bad('Select an exact original upstream execution')
        key = evidence.digest(value)
        if key not in cached:
            child = ops.get(oid)
            require_visible(ops.db, principal, child)
            if current:
                cached[key] = evaluate(ops, principal, downstream_package['workspace_id'], child, selected, value['reference'])
            else:
                _original(ops, principal, downstream_package['workspace_id'], child, selected, value['reference'])
                cached[key] = {'status': 'historical'}
        if cached[key]['status'] == 'passed':
            passed.append(rid)
    return sorted(passed)


def require_dependencies(ops, principal, downstream_package, references):
    return _dependencies(ops, principal, downstream_package, references, current=True)


def access_dependencies(ops, principal, downstream_package, references):
    """Original-source privacy only; never replace or reevaluate historical facts."""
    _dependencies(ops, principal, downstream_package, references, current=False)
