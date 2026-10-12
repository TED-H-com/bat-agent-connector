"""One fixed quality selection reaches Brief and milestone observations."""
from __future__ import annotations

import copy
import json

import pytest

from bat_agent_connector import pm_evidence, pm_roadmap
from tests import test_pm_quality_projections as shared
from tests.test_pm_plan_operations import OWNER
from tests.test_pm_roadmap import milestone, request

world, pm, ready, fixed, reviewed = shared.world, shared.pm, shared.ready, shared.fixed, shared.reviewed


async def observe(w, snapshot):
    ops, wid = w['d'].ops, w['workspace_id']
    binding = pm_evidence._binding(ops.db, wid, w['target']['work_item_id'])
    await shared.base.execute(w, request(ops, wid, milestones=[milestone(
        {'revision_id': binding['plan_revision_id']}, requirement_ids=binding['requirement_ids'],
        work_binding_ids=[binding['binding_id']])]))
    result = await shared.base.execute(w, request(ops, wid, 'observation', snapshot_id=snapshot['snapshot_id']))
    return pm_roadmap.get_revision(ops, OWNER, wid, result['result']['revision_id'])['revision']


async def assessed(w):
    reference = await shared.qualified(w)
    await shared.capture(w, reference)
    frozen = await shared.snapshot(w)
    return reference, frozen, await observe(w, frozen)


async def test_original_quality_selection_reaches_roadmap_and_survives_later_revocation(reviewed, monkeypatch):
    w = reviewed
    reference, frozen, observation = await assessed(w)
    actual = observation['document']['milestones'][0]['actual']
    quality = actual['quality']
    assert actual['completion'] == 'qualified' and actual['dod'] == 'accepted'
    assert actual['actual_finish_date'] is None
    assert quality['qualified_requirements'] == quality['total_requirements'] == 1
    assert quality['requirements'] == frozen['brief']['delivery']['quality']['requirements']
    assert quality['assessments'][0]['reference'] == reference
    assert quality['current_applicability'] == 'not_evaluated'
    assert observation['document']['milestones'][0]['coverage']['scope_assessment'] == 'unassessed'
    # Include the new quality references in the existing complete UTF-8 budget.
    stored = pm_roadmap._revision(w['d'].ops.db, w['workspace_id'], observation['revision_id'])['document']
    with monkeypatch.context() as limits:
        limits.setattr(pm_roadmap, 'MAX_OBSERVATION_BYTES', len(pm_roadmap._json(observation['document']).encode('utf-8')) - 1)
        bounded = pm_roadmap._bound_observation(stored, frozen)
        visible = pm_roadmap._project_observation(bounded, frozen)
        assert len(pm_roadmap._json(visible).encode('utf-8')) <= pm_roadmap.MAX_OBSERVATION_BYTES
        assert visible['coverage']['bytes_truncated']
        assert visible['milestones'][0]['actual']['completion'] != 'qualified'
    await shared.revoke(w, reference)
    again = pm_roadmap.get_revision(w['d'].ops, OWNER, w['workspace_id'], observation['revision_id'])['revision']
    assert again == observation
    await shared.capture(w, reference)
    later = await observe(w, await shared.snapshot(w))
    assert later['document']['milestones'][0]['actual']['completion'] == 'unassessed'


async def test_withdrawal_hides_original_quality_and_does_not_replace_it(reviewed):
    w = reviewed
    reference, _, observation = await assessed(w)
    shared.withdraw_proof(w)
    after = pm_roadmap.get_revision(w['d'].ops, OWNER, w['workspace_id'], observation['revision_id'])['revision']
    assert after['document_digest'] == observation['document_digest']
    assert after['view_digest'] != observation['view_digest']
    assert after['document']['milestones'][0]['actual']['completion'] == 'unassessed'
    assert after['document']['milestones'][0]['coverage']['inaccessible_sources']
    assert reference['review_id'] not in json.dumps(after)
    assert reference['document_digest'] not in json.dumps(after)


async def test_passing_verification_without_selected_review_remains_unknown(reviewed):
    await shared.capture(reviewed)
    observed = await observe(reviewed, await shared.snapshot(reviewed))
    actual = observed['document']['milestones'][0]['actual']
    assert actual['completion'] == actual['dod'] == 'unassessed'
    assert 'quality' not in actual


async def test_rejected_original_criterion_is_not_rewritten_as_missing(reviewed):
    w = reviewed
    row = await shared.base.new_review(w)
    op = await shared.base.execute(w, shared.base.assessment(w, row, verdict='rejected'), actor=shared.base.REVIEWER)
    reference = {key: op['result'][key] for key in ('review_id', 'version', 'document_digest')} | {
        'operation_id': op['operation_id'], 'candidate_commit': w['candidate']}
    await shared.capture(w, reference)
    observed = await observe(w, await shared.snapshot(w))
    actual = observed['document']['milestones'][0]['actual']
    assert actual['completion'] == 'not_qualified' and actual['dod'] == 'rejected'
    assert actual['quality']['qualified_requirements'] == 0


@pytest.mark.parametrize('gap', ['wrong_plan', 'wrong_binding', 'wrong_capture', 'work_limit',
    'evidence_limit', 'byte_limit', 'hidden_source', 'missing_work', 'unmapped_requirement', 'legacy'])
async def test_missing_or_different_original_scope_never_qualifies_milestone(reviewed, gap):
    _, frozen, observed = await assessed(reviewed)
    document = copy.deepcopy(observed['document'])
    frozen = copy.deepcopy(frozen)
    selected = document['milestones'][0]
    if gap == 'wrong_plan':
        selected['quality_context']['plan_revision_id'] = 'pmrev_' + 'f' * 32
    elif gap == 'wrong_binding':
        frozen['work'][0]['binding']['binding_id'] = 'pmwb_' + 'f' * 32
    elif gap == 'wrong_capture':
        frozen['work'][0]['capture']['capture_id'] = 'other-capture'
    elif gap == 'work_limit':
        frozen['coverage']['work_truncated'] = True
    elif gap == 'evidence_limit':
        selected['coverage']['evidence_truncated'] = True
    elif gap == 'byte_limit':
        frozen['coverage']['bytes_truncated'] = True
    elif gap == 'hidden_source':
        frozen['work'][0]['coverage']['inaccessible_sources'] = True
    elif gap == 'missing_work':
        frozen['work'] = []
    elif gap == 'unmapped_requirement':
        selected['quality_context']['requirement_ids'].append('unmapped')
    else:
        del selected['quality_context']
        for match in selected['work']:
            match.pop('requirement_ids')
    result = pm_roadmap._project_observation(document, frozen)['milestones'][0]['actual']
    assert result['completion'] == 'unassessed'
    assert result['actual_finish_date'] is None
    if gap == 'legacy':
        assert 'quality' not in result
