"""Project explicitly selected snapshot assessments onto a fixed milestone."""
from __future__ import annotations

from . import pm_snapshots

PROFILE = 'fixed_milestone_assessments_v1'


def context(milestone):
    return {'profile': PROFILE, **{key: milestone[key] for key in
        ('plan_revision_id', 'plan_document_digest', 'scope_revision', 'requirement_ids')}}


def project(milestone, snapshot):
    """Reuse the Brief's criterion aggregation, with the original milestone scope.

    Old observations have no selected quality profile and retain their original
    unassessed contract. No current plan, review, binding or provider is read.
    """
    selected = milestone.get('quality_context')
    if not selected or selected.get('profile') != PROFILE:
        return None
    available = {item['work_item_id']: item for item in snapshot['work']}
    work, complete = [], bool(milestone['work'])
    for match in milestone['work']:
        found = available.get(match['work_item_id'])
        exact = bool(match['match_status'] == 'exact' and found
            and found['binding']['binding_id'] == match['work_binding_id']
            and found['capture'] and found['capture']['capture_id'] == match['capture_id']
            and found['capture']['binding_id'] == match['work_binding_id'])
        complete &= exact
        if exact:
            complete &= not any(found['coverage'].get(key, False) for key in
                ('evidence_truncated', 'inaccessible_sources'))
        work.append({'work_item_id': match['work_item_id'],
            'binding': {'binding_id': match['work_binding_id'], 'requirement_ids': match['requirement_ids']},
            'evidence_ids': found['evidence_ids'] if exact else []})
    ids = set(milestone['evidence_ids'])
    evidence = []
    for entry in snapshot['evidence']:
        if entry['evidence_id'] not in ids or entry['source_kind'] != 'quality_assessment':
            continue
        fact = entry['document']['facts']
        if fact['plan_revision_id'] == selected['plan_revision_id'] and fact['scope_revision'] == selected['scope_revision']:
            evidence.append(entry)
    gaps = snapshot['coverage']
    summary = pm_snapshots._quality_summary({
        'baseline': {'scope_revision': selected['scope_revision'],
            'document': {'requirements': [{'id': rid} for rid in selected['requirement_ids']]}},
        'work': work, 'evidence': evidence,
        'coverage': {'work_truncated': not complete or gaps.get('work_truncated', False),
            'evidence_truncated': milestone['coverage']['evidence_truncated'] or gaps.get('evidence_truncated', False),
            'bytes_truncated': gaps.get('bytes_truncated', False)}})
    if summary is None:
        return None
    summary.update(profile=PROFILE, plan_revision_id=selected['plan_revision_id'],
        plan_document_digest=selected['plan_document_digest'],
        assessments=[{'evidence_id': entry['evidence_id'], **{key: entry['document']['facts'][key]
            for key in ('reference', 'work_item_id', 'candidate_commit', 'dod_revision_id')}} for entry in evidence])
    return summary


def outcome(summary):
    requirements = summary['requirements']
    if summary['total_requirements'] and summary['qualified_requirements'] == summary['total_requirements']:
        completion = 'qualified'
    elif any(row['acceptance'] == 'rejected' or row['dod'] == 'rejected' for row in requirements):
        completion = 'not_qualified'
    else:
        completion = 'unassessed'
    dod = ('rejected' if any(row['dod'] == 'rejected' for row in requirements) else
        'accepted' if requirements and summary['contribution_coverage_complete']
        and all(row['dod'] == 'accepted' for row in requirements) else 'unassessed')
    return {'completion': completion, 'dod': dod, 'quality': summary}
