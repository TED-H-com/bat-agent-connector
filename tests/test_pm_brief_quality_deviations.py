"""Original criterion judgments reach a fixed Brief without semantic inference."""
from __future__ import annotations

import copy
import json
import xml.etree.ElementTree as ET

from bat_agent_connector import pm_brief_exports, pm_evidence, pm_quality_reviews, pm_snapshots
from tests import test_pm_quality_projections as shared
from tests import test_pm_quality_reviews as base
from tests.test_pm_plan_operations import OWNER

world, pm, ready, fixed, reviewed = shared.world, shared.pm, shared.ready, shared.fixed, shared.reviewed


async def rejected(w, *, proof=True):
    row = await base.new_review(w)
    op = await base.execute(w, base.assessment(w, row, verdict='rejected', proof=proof), actor=base.REVIEWER)
    return {key: op['result'][key] for key in ('review_id', 'version', 'document_digest')} | {
        'operation_id': op['operation_id'], 'candidate_commit': w['candidate']}


async def test_rejected_criteria_freeze_original_sources_and_survive_later_good_assessment(reviewed):
    w, ops = reviewed, reviewed['d'].ops
    ref = await rejected(w)
    await shared.capture(w, ref)
    frozen = await shared.snapshot(w)
    view = frozen['brief']['deviations']
    original = pm_quality_reviews.get_assessment(ops, OWNER, w['workspace_id'], ref['review_id'],
        **{key: ref[key] for key in ('operation_id', 'version', 'document_digest')})
    assert view['status'] == 'fixed_quality_deviations'
    assert len(view['items']) == len(original['assessment']['document']['criteria']) == 2
    assert {row['details']['criterion']['criterion']['kind'] for row in view['items']} == {'acceptance', 'dod'}
    for item, conclusion in zip(view['items'], original['assessment']['document']['criteria']):
        details = item['details']
        assert details['reference'] == ref
        assert details['plan_ref'] == original['context']['plan_ref']
        assert details['baseline']['document_revision'] == frozen['baseline']['document_revision']
        assert details['assessment'] == conclusion
        assert details['assessed_by'] == base.REVIEWER.actor
        assert not details['independent_semantic_failure_proven']
        assert details['current_applicability'] == 'not_evaluated'
    outputs = {}
    for locale in ('en', 'zh-TW'):
        for format in ('markdown', 'html', 'svg'):
            report = pm_brief_exports.render_brief(ops, OWNER, w['workspace_id'], frozen['snapshot_id'], format=format, locale=locale)
            assert report['renderer_version'] == 4
            content = ''.join(ET.fromstring(report['content']).itertext()) if format == 'svg' else report['content']
            assert ('Reviewer rejected' if locale == 'en' else '審查者否決') in content
            assert ref['document_digest'] in ''.join(content.split())
            assert 'verification_id' in content and base.REVIEWER.actor in content
            outputs[locale, format] = report
    row = pm_quality_reviews.get_review(ops, OWNER, w['workspace_id'], ref['review_id'])['review']
    good = await base.execute(w, base.assessment(w, row), actor=base.REVIEWER)
    next_ref = {**ref, **{key: good['result'][key] for key in ('version', 'document_digest')}, 'operation_id': good['operation_id']}
    await shared.capture(w, next_ref)
    later = await shared.snapshot(w)
    assert later['brief']['deviations']['items'] == []
    assert later['brief']['deviations']['coverage']['selections'][0]['acceptance'] == 'accepted'
    assert pm_snapshots.get_snapshot(ops, OWNER, w['workspace_id'], frozen['snapshot_id'])['snapshot'] == frozen
    for (locale, format), report in outputs.items():
        assert pm_brief_exports.render_brief(ops, OWNER, w['workspace_id'], frozen['snapshot_id'], format=format, locale=locale) == report
    shared.withdraw_proof(w)
    hidden = pm_snapshots.get_snapshot(ops, OWNER, w['workspace_id'], frozen['snapshot_id'])['snapshot']
    assert hidden['brief']['deviations']['items'] == []
    assert ref['review_id'] not in json.dumps(hidden)
    assert hidden['visibility']['filtered'] and hidden['snapshot_digest'] == frozen['snapshot_digest']


async def test_no_proof_rejection_is_attributed_and_unassessed_has_no_finding(reviewed):
    w = reviewed
    ref = await rejected(w, proof=False)
    await shared.capture(w, ref)
    frozen = await shared.snapshot(w)
    assert frozen['brief']['deviations']['items']
    for row in frozen['brief']['deviations']['items']:
        assert row['details']['assessment']['verification'] is None
        assert row['details']['assessment']['reference'] is None
        assert not row['details']['independent_semantic_failure_proven']
    review = pm_quality_reviews.get_review(w['d'].ops, OWNER, w['workspace_id'], ref['review_id'])['review']
    op = await base.execute(w, base.assessment(w, review, verdict='unassessed'), actor=base.REVIEWER)
    ref.update(operation_id=op['operation_id'], **{key: op['result'][key] for key in ('version', 'document_digest')})
    await shared.capture(w, ref)
    later = await shared.snapshot(w)
    assert not later['brief']['deviations']['items']
    assert later['brief']['deviations']['coverage']['selections'][0]['acceptance'] == 'unassessed'


async def test_mismatched_partial_and_legacy_sources_do_not_infer_deviation(reviewed):
    w = reviewed
    await shared.capture(w, await rejected(w))
    frozen = await shared.snapshot(w)
    for field, value, reason in (('scope_revision', 'other-scope', 'scope_mismatch'),
            ('binding_id', 'other-binding', 'binding_mismatch'), ('applicable_at_selection', False, 'not_applicable_at_selection')):
        doc = copy.deepcopy(frozen)
        fact = next(row['document']['facts'] for row in doc['evidence'] if row['source_kind'] == 'quality_assessment')
        fact[field] = value
        view = pm_snapshots._brief(doc)['deviations']
        assert not view['items'] and view['coverage']['selections'][0]['reason'] == reason
    for key in ('work_truncated', 'evidence_truncated', 'bytes_truncated'):
        doc = copy.deepcopy(frozen)
        doc['coverage'][key] = True
        view = pm_snapshots._brief(doc)['deviations']
        assert view['items'] and not view['coverage']['selected_source_coverage_complete']
        assert not view['coverage']['absence_is_resolution']
    doc = copy.deepcopy(frozen)
    for row in doc['evidence']:
        if row['source_kind'] == 'quality_assessment':
            del row['document']['facts']['criterion_assessments']
    doc['brief'] = pm_snapshots._brief(doc)
    assert doc['brief']['deviations'] == {'status': 'unassessed', 'items': [],
        'coverage': {'baseline_result_assessment': 'not_performed', 'old_binding_is_not_drift': True}}
    assert pm_brief_exports._renderer_version(doc) == 3


async def test_whole_criterion_fact_rejects_over_budget_without_partial_capture(reviewed, monkeypatch):
    w = reviewed
    ref = await rejected(w)
    monkeypatch.setattr(pm_evidence, 'MAX_FACT_BYTES', 500)
    binding = pm_evidence._binding(w['d'].ops.db, w['workspace_id'], w['target']['work_item_id'])
    result = await base.execute(w, shared.envelope('pm.evidence.capture', w['target'], {'quality_ref': ref},
        {'expected_binding_id': binding['binding_id']}), success=False)
    assert result['error_code'] == 'PM_QUALITY_LIMIT'
    assert not w['d'].ops.db.execute('SELECT 1 FROM pm_evidence WHERE source_kind=?', ('quality_assessment',)).fetchone()
