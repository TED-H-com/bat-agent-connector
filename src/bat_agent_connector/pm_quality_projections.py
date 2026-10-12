"""Explicit original quality selectors shared by current and frozen PM views."""
from __future__ import annotations

from . import pm_quality_reviews as reviews
from .pm_quality_sources import bad, exact

REF_FIELDS = ('review_id', 'operation_id', 'version', 'document_digest', 'candidate_commit')


def original(ops, principal, wid, reference):
    exact(reference, REF_FIELDS)
    value = reviews.get_assessment(ops, principal, wid, reference['review_id'],
        **{key: reference[key] for key in ('operation_id', 'version', 'document_digest')})
    if value['context']['native_result']['candidate_commit'] != reference['candidate_commit']:
        raise bad('The selected assessment belongs to a different candidate', 'PM_QUALITY_CONTEXT_CHANGED', 409)
    return value


def select(ops, principal, wid, reference, *, work_item_id, binding_id, scope_revision, dod_revision_id):
    """Recheck one selected assessment at admission and at the recording frame."""
    saved = original(ops, principal, wid, reference)
    context = saved['context']
    if context['work_ref']['work_item_id'] != work_item_id:
        raise bad('Select the original assessment for this WorkItem', 'PM_QUALITY_CONTEXT_CHANGED', 409)
    current = reviews.evaluate_review(ops, principal, wid, reference['review_id'],
        binding_id=binding_id, scope_revision=scope_revision, dod_revision_id=dod_revision_id,
        candidate_commit=reference['candidate_commit'])['review']
    latest = current['assessment']
    if not latest or any(latest[key] != reference[key] for key in ('operation_id', 'version', 'document_digest')):
        raise bad('The selected original assessment is no longer the latest conclusion', 'PM_QUALITY_VERSION_CONFLICT', 409)
    # Historical conclusions remain visible. Only a currently applicable exact
    # binding/scope/DoD/candidate may count as qualified at this selection frame.
    document = saved['assessment']['document']
    return {'reference': reference, 'context_digest': saved['context_digest'],
        'work_item_id': work_item_id, 'binding_id': context['work_ref']['binding_id'],
        'plan_revision_id': context['plan_ref']['revision_id'], 'scope_revision': context['plan_ref']['scope_revision'],
        'dod_revision_id': context['dod_ref']['revision_id'] if context['dod_ref'] else None,
        'candidate_commit': context['native_result']['candidate_commit'],
        'requirements': document['requirements'], 'original_outcome': current['original_outcome'],
        'acceptance': current['acceptance'], 'dod': current['dod'], 'qualified': current['qualified'],
        'applicable_at_selection': current['applicable'], 'applicability_reasons': current['applicability_reasons'],
        'assessed_by': saved['assessment']['actor'], 'assessed_at': saved['assessment']['created_at'],
        'semantics': 'fixed_selection_of_original_assessment', 'current_applicability': 'not_evaluated'}


def access(ops, principal, wid, fact):
    """Authorize frozen bytes without replacing them with today's conclusion."""
    saved = original(ops, principal, wid, fact['reference'])
    if saved['context_digest'] != fact['context_digest']:
        raise bad('The original quality context is unavailable', 'NOT_FOUND', 404)
    return saved


def for_capture(ops, principal, wid, binding, reference):
    from . import pm_plans
    saved = original(ops, principal, wid, reference)
    plan = pm_plans.accepted_plan(ops, principal, wid)['plan']
    scope = plan['adoption']['scope_revision'] if plan and plan['adoption'] else None
    dod = saved['context']['dod_ref']
    return select(ops, principal, wid, reference, work_item_id=binding['work_item_id'],
        binding_id=binding['binding_id'], scope_revision=scope, dod_revision_id=dod['revision_id'] if dod else None)


def sprint_sources(ops, principal, wid, document):
    for item in document.get('items', []):
        if item.get('quality_assessment'):
            access(ops, principal, wid, item['quality_assessment'])


def card_quality(ops, principal, wid, binding, capture):
    from . import pm_evidence
    from .operations import OperationError
    result = {'dod': 'unassessed', 'acceptance_criteria': 'unassessed', 'verification': 'see_evidence',
        'deployment': 'see_evidence', 'management_approval_is_quality': False}
    for entry in capture.get('evidence', []) if capture else []:
        if entry['source_kind'] != 'quality_assessment':
            continue
        original_fact = pm_evidence.get_evidence(ops, principal, wid, entry['evidence_id'])['evidence']['document']['facts']
        result['selected_assessment'] = original_fact
        try:
            current = for_capture(ops, principal, wid, binding, original_fact['reference'])
        except OperationError as exc:
            if exc.status != 409:
                raise
            result.update(qualified=False, applicability_reasons=[exc.code])
        else:
            result.update(dod=current['dod'], acceptance_criteria=current['acceptance'], qualified=current['qualified'],
                applicability_reasons=current['applicability_reasons'])
        break
    return result


def operation_visible(db, principal, operation):
    """Close/capture envelopes retain membership and optional source privacy."""
    from .operations import OperationError
    from .pm_release_baselines import _snapshot
    from .pm_workspace import require_member
    with _snapshot(db):
        try:
            ops = reviews._Reader(db)
            op = reviews._op(ops, operation['operation_id'])
            wid, params = op['target']['workspace_id'], op['params']
            require_member(db, principal, wid)
            refs = params.get('quality_refs', {}).values() if op['action'].startswith('pm.sprint.') else [params['quality_ref']] if 'quality_ref' in params else []
            for reference in refs:
                original(ops, principal, wid, reference)
            return True
        except (OperationError, KeyError, TypeError, ValueError):
            return False
