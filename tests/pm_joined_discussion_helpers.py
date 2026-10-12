"""Original public work mapping and sourced PM discussion in the same ledger."""
from __future__ import annotations

import copy
import json
import xml.etree.ElementTree as ET

from bat_agent_connector import pm_bat_runtime as bat_runtime
from bat_agent_connector import (
    pm_brief_exports,
    pm_follow_up,
    pm_follow_up_analysis,
    pm_governance,
    pm_issue_mappings,
    pm_plans,
    pm_runs,
)
from tests import test_pm_follow_up as follow
from tests import test_pm_follow_up_analysis as analysis
from tests import test_pm_issue_mappings as issues
from tests import test_pm_quality_projections as projections
from tests import test_pm_quality_reviews as quality
from tests.pm_joined_lifecycle_helpers import OWNER, assert_manual_preserved, execute


async def publish_original_packages(w, selections):
    """Selected public text follows each fixed package, without starting work."""
    provider = w['issue_provider'] = issues.IssueProvider(w)
    original = []
    for item in selections:
        request = issues.request(item, public_title=item['package']['document']['title'],
            public_body='Implement the selected member requirement and verify its original acceptance criteria.')
        op = await quality.execute(w, request)
        replay, created = w['d'].ops.create(OWNER, **request)
        assert not created and replay['operation_id'] == op['operation_id']
        view = pm_issue_mappings.get_mapping(w['d'].ops, OWNER, w['workspace_id'], op['result']['mapping_id'])
        assert view['current']['document']['package_ref']['document_digest'] == item['package']['document_digest']
        assert not op['result']['publication']['counts_as_done']
        original.append(op)
    # An explicit revision appends to the same issue after another author's edit.
    first = original[0]
    number = first['result']['issue']['number']
    provider.issues[number]['body'] = 'A collaborator keeps this original issue description.'
    external = copy.deepcopy(provider.issues[number])
    updated = await quality.execute(w, issues.request(selections[0], previous=first['result']['revision_id'],
        public_title='Original login acceptance selected', public_body='The selected batch will preserve the fixed login requirement.'))
    assert updated['result']['issue'] == first['result']['issue']
    assert provider.issues[number] == external and len(provider.issues) == 2 and len(provider.comments) == 1
    assert not any(method == 'PATCH' for method, _, _ in w['gh'].requests)
    w['ledger'].record('original_package_issue_publication',
        issue_mapping_ids=[row['result']['mapping_id'] for row in original],
        issue_publication_operation_ids=[row['operation_id'] for row in original],
        issue_revision_operation_id=updated['operation_id'])


async def discuss_original_failure(w, item, verification, condition, mock):
    """A real failed verifier plus attributed rejection precedes PM analysis."""
    ops, wid = w['d'].ops, w['workspace_id']
    item['candidate'], item['tree'] = item['config']['candidate_commit'], item['config']['tree_hash']
    item['proof'] = verification['result']['reference']
    selection = quality.selection(item)
    for criterion in selection['params']['criteria']:
        criterion['condition'] = condition
    created = await quality.execute(item, selection)
    review = quality.read(item, {'review_id': created['result']['review_id']})
    request = quality.assessment(item, review, verdict='rejected')
    for conclusion in request['params']['conclusions']:
        conclusion['rationale'] = 'The actual fixed candidate fails the original valid and invalid credential regression.'
    await quality.execute(item, request, actor=quality.REVIEWER)
    review = quality.read(item, review)
    assert review['acceptance'] == review['dod'] == 'rejected' and not review['qualified']
    reference = {'review_id': review['review_id'], 'candidate_commit': item['candidate'],
        **{key: review['assessment'][key] for key in ('operation_id', 'version', 'document_digest')}}
    await projections.capture(item, reference)
    snapshot = await projections.snapshot(w)
    fact = next(row for row in snapshot['evidence'] if row['source_kind'] == 'quality_assessment')
    assert fact['document']['facts']['reference']['document_digest'] == reference['document_digest']
    deviations = snapshot['brief']['deviations']
    assert deviations['version'] == 1 and deviations['status'] == 'fixed_quality_deviations'
    assert len(deviations['items']) == len(review['context']['criteria'])
    assert all(row['source']['evidence_id'] == fact['evidence_id']
        and row['details']['reference'] == reference and row['status'] == 'rejected'
        for row in deviations['items'])
    assert snapshot['brief']['delivery']['coverage']['current_environment'] == 'not_assessed'
    for locale in ('en', 'zh-TW'):
        for format in ('markdown', 'html', 'svg'):
            report = pm_brief_exports.render_brief(ops, OWNER, wid, snapshot['snapshot_id'], format=format, locale=locale)
            text = ''.join(ET.fromstring(report['content']).itertext()) if format == 'svg' else report['content']
            assert report['view_digest'] == snapshot['view_digest'] and report['renderer_version'] == 4
            assert reference['document_digest'] in ''.join(text.split())
    drift = await execute(w, 'pm.governance.record', {'workspace_id': wid},
        {'kind': 'drift', 'root_cause_key': 'original-login-regression', 'snapshot_id': snapshot['snapshot_id'],
            'title': 'Original login candidate does not satisfy its selected acceptance',
            'description': 'The original fixed verifier failed and the responsible reviewer rejected the same candidate.',
            'dimension': 'quality', 'owner_actor': OWNER.actor, 'affected_requirement_ids': ['member.login'],
            'source_refs': [{'kind': 'requirement', 'id': 'member.login'}, {'kind': 'evidence', 'id': fact['evidence_id']}],
            'response': 'Fix the original owned candidate and repeat the selected acceptance review.',
            'rationale': 'Compare this exact observed result with the unchanged accepted requirement.'},
        {'expected_plan_version': pm_plans.head(ops.db, wid)['version']})
    governance = pm_governance.get_record(ops, OWNER, wid, drift['result']['record_id'])
    data = {'wid': wid, 'snapshot': snapshot, 'target': {'workspace_id': wid, 'thread_id': w['thread_id']}}
    intake_op = await quality.execute(w, follow.request(ops, data, thread_id=w['thread_id'],
        governance_revision_ids=[governance['revision']['revision_id']]))
    intake = pm_follow_up.get_intake(ops, OWNER, wid, intake_op['result']['intake_id'])['intake']
    selected = intake['document']['selected_governance_refs'][0]
    assert selected['document_digest'] == governance['revision']['document_digest']
    assert intake['document']['knowledge']['finance']['coverage'] == 'unresolved'
    document = {'version': 1,
        'summary': {'text': 'The original login result needs correction; the first release scope is unchanged.',
            'source_refs': ['selected_governance_refs.0']},
        'findings': [
            {'kind': 'deviation', 'text': 'The original login candidate was explicitly rejected against its original requirement.',
                'impact': 'The dependent recovery work must remain blocked until its prerequisite is accepted.',
                'source_refs': ['selected_governance_refs.0']},
            {'kind': 'unknown', 'text': 'No cost baseline or actual expenditure is available.',
                'impact': 'Cost deviation cannot be assessed.', 'source_refs': ['knowledge.finance']}],
        'tradeoffs': [], 'questions': [], 'recommendations': [
            {'text': 'Repair and reassess the same original login requirement.', 'proposed_owner': OWNER.actor,
                'source_refs': ['selected_governance_refs.0']}]}
    w['pm_settings']['raw_reply'] = json.dumps(document)
    plan_before = copy.deepcopy(pm_plans.head(ops.db, wid))
    coding_inputs = {sid: [row['content'] for row in mock.states[sid]['messages'] if row.get('role') == 'user']
        for sid in w['coding']}
    evaluated = await quality.execute(w, analysis.request(ops, {**data, 'intake': intake},
        params={'adapter': bat_runtime.ADAPTER}))
    result = analysis.read(ops, evaluated)
    assert result['document'] == document and result['validation']['status'] == 'format_valid'
    assert not result['conclusions_verified'] and not result['accepted_plan_changed']
    run = pm_runs.get_by_operation(ops.db, evaluated['operation_id'])
    assert run['context']['intake'] == intake['document']
    assert reference['document_digest'] in json.dumps(run['context'])
    assert verification['result']['reference']['verification_operation_id'] in json.dumps(run['context'])
    assert pm_plans.head(ops.db, wid) == plan_before
    assert coding_inputs == {sid: [row['content'] for row in mock.states[sid]['messages'] if row.get('role') == 'user']
        for sid in w['coding']}
    w['initial_quality_discussion'] = {'snapshot': copy.deepcopy(snapshot), 'intake': copy.deepcopy(intake),
        'assessment': copy.deepcopy(result)}
    w['ledger'].record('original_quality_deviation_discussion', failed_quality_ref=reference,
        failed_quality_snapshot_id=snapshot['snapshot_id'], quality_drift_record_id=drift['result']['record_id'],
        quality_drift_revision_id=governance['revision']['revision_id'],
        quality_follow_up_intake_id=intake['intake_id'], quality_follow_up_assessment_id=result['assessment_id'])
    assert_manual_preserved(w, mock)


def assert_original_discussion(w):
    from bat_agent_connector import pm_snapshots
    ops, wid = w['d'].ops, w['workspace_id']
    saved = w['initial_quality_discussion']
    assert pm_snapshots.get_snapshot(ops, OWNER, wid, saved['snapshot']['snapshot_id'])['snapshot'] == saved['snapshot']
    assert pm_follow_up.get_intake(ops, OWNER, wid, saved['intake']['intake_id'])['intake'] == saved['intake']
    assert pm_follow_up_analysis.get_assessment(ops, OWNER, wid,
        saved['assessment']['assessment_id'])['assessment'] == saved['assessment']
