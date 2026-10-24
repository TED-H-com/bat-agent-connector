"""Pure, source-gated relations from the Result's original engineering handoff."""
from __future__ import annotations

import json

from . import (
    controller_authority,
    pm_evidence,
    pm_package_handoff,
    pm_plans,
    pm_quality_reviews,
    pm_source_lineage,
    pm_work_packages,
    pm_work_start,
    work_items,
)
from .operations import OperationError
from .pm_access import require_visible
from .pm_release_baselines import _snapshot_read

MAX_BYTES = 262_144
LIMIT = 50
READINESS_LIMIT = 20
HANDOFF_LIMIT = 50


def _none():
    # Missing and inaccessible originals deliberately have the same public shape.
    return {'relation': 'none', 'reason': 'no_visible_explicit_binding', 'items': [], 'dependent_work': []}


def _link(read, **params):
    return {'read': read, 'params': params}


def _bounded(value):
    if len(json.dumps(value, ensure_ascii=False).encode('utf-8')) > MAX_BYTES:
        raise OperationError('PM_RESULT_REQUIREMENTS_LIMIT', 'Original requirement relations exceed the read budget', 413)
    return value


def _original(ops, principal, wid, child):
    """Resolve only immutable handoff evidence, including registered Work routes."""
    require_visible(ops.db, principal, child)
    frozen = pm_package_handoff.binding(child)
    if frozen:
        if not pm_package_handoff.source_visible(ops.db, principal, child):
            return None
        contract = frozen["contract"]
        ref, refs = contract["package_ref"], contract.get("pm_requirement_quality_refs")
    else:
        if not pm_work_start.installed(ops.db):
            return None
        row = ops.db.execute("SELECT * FROM pm_work_start_children WHERE operation_id=? AND slot='handoff'",
                             (child["operation_id"],)).fetchone()
        if row is None:
            return None
        parent = ops.get(row["parent_id"], steps=False)
        require_visible(ops.db, principal, parent)
        intent = pm_work_start._intent(parent)
        request = json.loads(row["request"])
        if (parent["action"] != pm_work_start.ACTION or row["ordinal"] != pm_work_start.SLOT_ORDINAL["handoff"]
                or pm_work_start._digest(request) != row["request_digest"]
                or any(child[key] != request[key] for key in ("action", "target", "params", "preconditions", "idempotency_key"))
                or child["actor"] != parent["actor"] or child["target"] != parent["target"]
                or parent["target"].get("workspace_id") != wid
                or child["external_refs"].get(pm_work_start.LINK) != {"operation_id": parent["operation_id"], "slot": "handoff"}
                or not controller_authority.same_controller(controller_authority.operation_binding(child), intent["controller"])):
            return None
        ref, refs = request["params"].get("package_ref"), None
    if not isinstance(ref, dict) or ref.get("workspace_id") != wid:
        return None
    package = pm_package_handoff._package(ops, principal, ref)
    if not frozen and package["work_item_id"] != parent["target"]["work_item_id"]:
        return None
    return package, ref, refs


def _assessments(ops, principal, wid, package, execution, candidate):
    doc = package['document']
    catalog = pm_quality_reviews.list_reviews(ops, principal, wid, work_item_id=doc['work_item_id'], limit=LIMIT)
    binding = pm_evidence._binding(ops.db, wid, doc['work_item_id'])
    plan = pm_plans.accepted_plan(ops, principal, wid)['plan']
    scope = plan['adoption']['scope_revision'] if plan and plan['adoption'] else None
    rows = []
    for review in catalog['reviews']:
        context = review['context']
        if context['execution_operation_id'] != execution or context['package_ref']['package_id'] != package['package_id']:
            continue
        current = pm_quality_reviews.evaluate_review(ops, principal, wid, review['review_id'],
            binding_id=binding['binding_id'] if binding else None, scope_revision=scope,
            dod_revision_id=context['dod_ref']['revision_id'] if context['dod_ref'] else None,
            candidate_commit=candidate)['review']
        for record in review['history']:
            if record['kind'] != 'assessment':
                continue
            ref = {key: record[key] for key in ('operation_id', 'version', 'document_digest')}
            saved = pm_quality_reviews.get_assessment(ops, principal, wid, review['review_id'], **ref)['assessment']
            ref.update(review_id=review['review_id'], candidate_commit=context['native_result']['candidate_commit'])
            reasons = list(current['applicability_reasons'])
            if not current['assessment'] or record['operation_id'] != current['assessment']['operation_id']:
                reasons.append('PM_QUALITY_VERSION_CONFLICT')
            rows.append({'reference': ref, 'plan_ref': context['plan_ref'], 'dod_ref': context['dod_ref'],
                'package_ref': context['package_ref'], 'work_ref': context['work_ref'],
                'execution_operation_id': execution, 'reviewer_actor': context['reviewer_actor'],
                'original': saved['document'], 'assessed_by': saved['actor'], 'assessed_at': saved['created_at'],
                'applicable': not reasons, 'applicability_reasons': reasons,
                'qualified': current['qualified'] and not reasons, 'revocation': current['revocation']})
            _bounded(rows)
    return rows, bool(catalog['next_cursor'] or catalog['visibility_filtered'])


def _handoffs(ops, wid, target, limit):
    direct = ops.db.execute("SELECT operation_id FROM operations WHERE "
        "json_extract(external_refs,'$.admission_binding.pm_handoff.contract.package_ref.workspace_id')=? "
        "AND json_extract(external_refs,'$.admission_binding.pm_handoff.contract.package_ref.package_id')=? "
        "ORDER BY operation_id LIMIT ?", (wid, target['package_id'], limit + 1)).fetchall()
    # Original routed children need no runtime installation or action allowlist.
    routed = ops.db.execute("""SELECT c.operation_id FROM work_attempts a
        JOIN pm_work_start_children c ON c.parent_id=a.start_operation_id AND c.slot='handoff'
        WHERE a.workspace_id=? AND a.work_item_id=? ORDER BY a.sequence LIMIT ?""",
        (wid, target['work_item_id'], limit + 1)).fetchall() if pm_work_start.installed(ops.db) else []
    return list({row['operation_id']: row for row in [*direct, *routed]}.values())


def _dependents(ops, principal, wid, package):
    original = package['document']['context']['plan_ref']
    ids = {row['id'] for row in package['document']['requirements']}
    catalog = pm_work_packages.list_packages(ops, principal, wid, limit=LIMIT)
    result = []
    readiness_left, handoffs_left = READINESS_LIMIT, HANDOFF_LIMIT
    partial = bool(catalog["next_cursor"] or catalog["coverage"]["visibility_filtered"])
    for target in catalog['packages']:
        doc = target['document']
        if doc['context']['plan_ref'] != original:
            continue
        needed = sorted(ids & {rid for row in doc['requirements'] for rid in row['depends_on']})
        if not needed:
            continue
        # Package source access and work visibility are the existing authorities.
        try:
            pm_source_lineage.require(ops.db, principal, 'work_item', target['work_item_id'])
        except OperationError as exc:
            if exc.status != 404:
                raise
            continue
        if readiness_left == 0:
            partial = True
            break
        default_readiness = pm_work_packages.readiness(ops, principal, wid, target['package_id'])
        readiness_left -= 1
        selections = []
        children = _handoffs(ops, wid, target, handoffs_left) if handoffs_left else []
        complete = bool(handoffs_left) and len(children) <= handoffs_left
        for stub in children[:handoffs_left]:
            handoffs_left -= 1
            try:
                child = ops.get(stub['operation_id'], steps=False)
                handoff = _original(ops, principal, wid, child)
                if handoff is None or handoff[1]['package_id'] != target['package_id']:
                    continue
                refs = handoff[2]
                if refs is None:
                    continue
                if readiness_left == 0:
                    complete = False
                    break
                readiness_left -= 1
                readiness = pm_work_packages.readiness(ops, principal, wid, target['package_id'], quality_refs=refs)
                selections.append({'execution_operation_id': child['operation_id'], 'quality_refs': refs, 'readiness': readiness})
            except OperationError as exc:
                if exc.status not in {404, 409}:
                    raise
        result.append({'work_item_id': target['work_item_id'], 'title': work_items._get_item(ops.db, target['work_item_id'])['title'],
            'package_id': target['package_id'], 'document_digest': target['document_digest'], 'prerequisite_ids': needed,
            'readiness': default_readiness,
            'selections': selections, 'selections_complete': complete,
            'link': _link('pm_work_package_readiness_get', workspace_id=wid, package_id=target['package_id'])})
        partial |= not complete
    return result, partial


def _related(ops, principal, wid, package, execution):
    from . import pm_maintenance_repairs, pm_roadmap
    result = []
    try:
        baseline = pm_roadmap.get_roadmap(ops, principal, wid)['baseline']
    except OperationError as exc:
        if exc.status != 404:
            raise
        baseline = None
    context = package['document']['context']
    for milestone in baseline['document']['milestones'] if baseline else ():
        if (milestone['plan_revision_id'] == context['plan_ref']['revision_id']
                and context['work_ref']['binding_id'] in milestone['work_binding_ids']):
            result.append({'kind': 'milestone', 'id': milestone['milestone_id'],
                **_link('pm_roadmap_revision_get', workspace_id=wid, revision_id=baseline['revision_id'])})
    for oid in sorted(pm_source_lineage.roots(ops.db, 'operation', execution)):
        parent = ops.get(oid, steps=False)
        if parent['action'] != 'pm.maintenance.repair':
            continue
        try:
            repair = pm_maintenance_repairs.get_repair(ops, principal, wid, 'pmrp_' + oid[3:])['repair']
        except OperationError as exc:
            if exc.status != 404:
                raise
            continue
        if (repair.get('child') or {}).get('operation_id') == execution:
            result.append({'kind': 'maintenance_repair', 'id': repair['repair_id'],
                **_link('pm_maintenance_repair_get', workspace_id=wid, repair_id=repair['repair_id'])})
    return result


@_snapshot_read
def read(ops, principal, workspace_id, execution_operation_id, candidate_commit, verification_status, *, verification_evidence=()):
    """Never select a newest assessment or create a prerequisite selection on read."""
    pm_work_packages._member(ops, principal, workspace_id)
    if execution_operation_id is None:
        return _none()
    try:
        child = ops.get(execution_operation_id, steps=False)
        original = _original(ops, principal, workspace_id, child)
        if original is None:
            return _none()
        package, package_ref, _refs = original
    except OperationError as exc:
        if exc.status != 404:
            raise
        return _none()
    doc = package['document']
    if not doc['requirements']:
        return _none()
    assessments, reviews_partial = _assessments(ops, principal, workspace_id, package, execution_operation_id, candidate_commit)
    items = []
    for requirement in doc['requirements']:
        rid = requirement['id']
        conclusions = []
        for assessment in assessments:
            original = next(row['status'] for row in assessment['original']['requirements'] if row['requirement_id'] == rid)
            conclusions.append({**assessment, 'conclusion': original,
                'state': original if assessment['applicable'] else 'no_longer_applies'})
        items.append({'requirement_id': rid, 'text': requirement['text'], 'source_input_id': requirement['source_input_id'],
            'acceptance_input_ids': [row['source_input_id'] for row in requirement['acceptance']],
            'state': 'assessments_recorded' if conclusions else 'unavailable' if reviews_partial
                else 'verified_no_conclusion' if verification_status == 'verified' else 'no_conclusion',
            'selection': 'undecided', 'assessments': conclusions,
            'link': _link('pm_plan_get', workspace_id=workspace_id, revision_id=doc['context']['plan_ref']['revision_id'])})
        _bounded(items)
    dependents, packages_partial = _dependents(ops, principal, workspace_id, package)
    result = {'relation': 'explicit', 'package_ref': package_ref,
        'plan_ref': doc['context']['plan_ref'], 'dod_ref': doc['context']['dod_ref'], 'candidate_commit': candidate_commit,
        'verification': verification_status, 'verification_evidence': list(verification_evidence), 'items': items, 'dependent_work': dependents,
        'related': _related(ops, principal, workspace_id, package, execution_operation_id),
        'coverage': {'reviews_partial': reviews_partial, 'packages_partial': packages_partial},
        'selection_rule': 'explicit_original_review_selection_required'}
    return _bounded(result)
