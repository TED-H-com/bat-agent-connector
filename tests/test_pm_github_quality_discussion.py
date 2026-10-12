"""Real fixed GitHub quality receipts stay readable in sourced discussions."""
from __future__ import annotations

import copy
import json
import re

import pytest

from bat_agent_connector import (
    pm_access,
    pm_evidence,
    pm_follow_up,
    pm_meetings,
    pm_snapshots,
    pm_sprints,
    pm_work_batch_evidence,
    work_items,
)
from bat_agent_connector import pm_github_observation as github
from bat_agent_connector import pm_lifecycle_gates as lifecycle
from bat_agent_connector import pm_maintenance_policy as policy
from bat_agent_connector import pm_quality_reviews as quality
from bat_agent_connector.operations import OperationError
from tests import test_pm_delivery_lineage as delivery
from tests import test_pm_follow_up as follow
from tests import test_pm_quality_projections as projections
from tests import test_pm_quality_reviews as reviews
from tests.test_pm_maintenance_repairs import shared_request
from tests.test_pm_plan_operations import OWNER
from tests.test_pm_release_baselines import request as baseline_request
from tests.test_pm_work_packages import action, member
from tests.test_pm_workload import settings

world, pm, ready, fixed = delivery.world, delivery.pm, delivery.ready, delivery.fixed
delivery_ready = delivery.delivery_ready
CONDITION = {'kind': 'fixed_revision_verification', 'profile': 'github_reported_checks_v1'}


async def github_quality(w):
    """Use real registered delivery, bounded HTTP observation and review actions."""
    ops, wid, gh = w['d'].ops, w['workspace_id'], w['gh']
    delivered = await delivery.settle(w, delivery.apply(w, await delivery.preview(w)))
    assert delivered['status'] == 'succeeded' and delivered['result']['pushed'], delivered
    w['child'], w['candidate'] = w['original'], delivered['result']['new_head']
    receipt = ops.db.execute('SELECT seq FROM integration_receipts WHERE operation_id=? AND source_id=?',
        (delivered['operation_id'], w['child']['operation_id'])).fetchone()
    assert receipt is not None
    # The fake provider serves exact native IDs and the actual published Git head.
    gh.pulls[1]['id'] = 901
    previous = gh.before_request
    sha = w['candidate']
    check = {'id': 903, 'head_sha': sha, 'status': 'completed', 'conclusion': 'success',
        'check_suite': {'id': 902}}

    def provider(method, path, body):
        responses = {
            '/repos/o/r/releases/41': {'id': 41, 'tag_name': 'v1.4.0', 'draft': False, 'prerelease': False,
                'published_at': '2026-01-01T00:00:00Z', 'target_commitish': 'main'},
            '/repos/o/r/git/ref/tags/v1.4.0': {'ref': 'refs/tags/v1.4.0', 'object': {'type': 'commit', 'sha': w['sha']}},
            '/repos/o/r/git/commits/' + w['sha']: {'sha': w['sha']},
            '/repos/o/r/pulls': [gh.pulls[1]],
            '/repos/o/r/commits/' + sha + '/check-suites': {'total_count': 1, 'check_suites': [
                {'id': 902, 'head_sha': sha, 'status': 'completed', 'conclusion': 'success'}]},
            '/repos/o/r/commits/' + sha + '/check-runs': {'total_count': 1, 'check_runs': [check]},
            '/repos/o/r/check-suites/902/check-runs': {'total_count': 1, 'check_runs': [check]},
            '/repos/o/r/commits/' + sha + '/statuses': [],
            '/repos/o/r/actions/runs': {'total_count': 0, 'workflow_runs': []},
        }
        if method == 'GET' and path in responses:
            gh.script.append(('GET', re.escape(path) + '$', 200, {}, copy.deepcopy(responses[path])))
        elif previous:
            previous(method, path, body)

    gh.before_request = provider
    bound = ops.db.execute('SELECT binding_id,project_id FROM pm_project_bindings WHERE workspace_id=?', (wid,)).fetchone()
    env = baseline_request(ops, {'workspace_id': wid, 'repository': 'o/r'}, bound['binding_id'])
    env['preconditions']['expected_commit_sha'] = w['sha']
    baseline = await reviews.execute(w, env)
    document = settings()
    document['enabled'] = False  # An explicit manual observation grants no scheduling authority.
    document['observation']['github_profile'] = policy.GITHUB_PROFILE
    await reviews.execute(w, projections.envelope(policy.ACTION, {'workspace_id': wid},
        {'policy': document, 'rationale': 'Read fixed reported GitHub checks for the original quality review'},
        {'expected_policy_revision': 0, 'expected_previous_policy_id': None,
            'expected_baseline_id': baseline['result']['baseline_id'],
            'expected_lifecycle_revision': lifecycle.head(ops.db, wid)['revision']}))
    observed = await reviews.execute(w, shared_request(w, github.ACTION))
    record = github.get_observation(ops, OWNER, wid, observed['result']['observation_id'])['observation']
    assert record['document']['evidence']['status'] == 'complete'
    assert record['document']['evidence']['traversals_completed'] == 2
    w['proof'] = {'observation_id': record['observation_id'], 'document_digest': record['document_digest'],
        'integration_operation_id': delivered['operation_id'], 'source_seq': receipt['seq'], 'candidate_commit': sha}
    await member(w, reviews.REVIEWER.actor, 'contributor')
    w['dod'] = (await action(w, 'pm.dod.revise', {'workspace_id': wid},
        {'plan_revision_id': w['package']['document']['context']['plan_ref']['revision_id'],
            'criteria': [{'id': 'regression', 'text': 'Review the fixed original GitHub check result'}],
            'rationale': 'Separate reported checks from accountable criterion judgments'},
        {'expected_revision_id': None}))['revision_id']
    selected = reviews.selection(w)
    for criterion in selected['params']['criteria']:
        criterion['condition'] = CONDITION
    created = await reviews.execute(w, selected)
    return reviews.read(w, created['result']), observed, bound['project_id']


@pytest.mark.parametrize('verdict', ['accepted', 'rejected'])
async def test_original_github_quality_reaches_followup_and_withdrawal_hides_whole_source(delivery_ready, verdict):
    w, ops = delivery_ready, delivery_ready['d'].ops
    for module in (pm_sprints, quality, pm_snapshots, pm_follow_up):
        module.install(ops)
    review, observation, project_id = await github_quality(w)
    assessed = await reviews.execute(w, reviews.assessment(w, review, verdict=verdict), actor=reviews.REVIEWER)
    selected = reviews.read(w, review)
    assert selected['acceptance'] == selected['dod'] == verdict
    proof = selected['assessment']['document']['criteria'][0]['verification']
    assert proof['status'] == 'passed' and proof['semantic_acceptance'] == 'not_evaluated'
    assert observation['operation_id'] in proof['operation_ids']
    reference = {'review_id': review['review_id'], 'candidate_commit': w['candidate'],
        **{key: selected['assessment'][key] for key in ('operation_id', 'version', 'document_digest')}}
    await projections.capture(w, reference)
    frozen = await projections.snapshot(w)
    reader = pm_meetings._SourceReader(ops.db)
    before, provider_calls = ops.db.total_changes, len(w['gh'].requests)
    pm_work_batch_evidence.require_sources(reader, OWNER, w['workspace_id'], proof)
    assert reader.get(observation['operation_id'], principal=OWNER) == ops.get(observation['operation_id'], steps=False)
    assert pm_evidence._read_operation(reader, OWNER, w['workspace_id'], observation['operation_id']) is None
    saved = ops.db.execute('SELECT * FROM pm_document_snapshots WHERE snapshot_id=?', (frozen['snapshot_id'],)).fetchone()
    read = pm_snapshots._out(reader, OWNER, saved)['snapshot']
    assert read == frozen and not read['visibility']['filtered']
    assert ops.db.total_changes == before and len(w['gh'].requests) == provider_calls
    data = {'wid': w['workspace_id'], 'snapshot': frozen}
    intake = await reviews.execute(w, follow.request(ops, data))
    view = follow.read(ops, intake['result'], who=OWNER)
    assert view['document']['snapshot_ref']['snapshot_digest'] == frozen['snapshot_digest']
    # A PM assessment is not generic delivery evidence. The intake referring
    # back to this same snapshot must likewise stop before recursive reads.
    for forbidden in (assessed, intake):
        with pytest.raises(OperationError, match='NOT_FOUND'):
            reader.get(forbidden['operation_id'], principal=OWNER)
        assert pm_evidence._read_operation(reader, OWNER, w['workspace_id'], forbidden['operation_id']) is None
    with pytest.raises(OperationError, match='NOT_FOUND'):
        reader.get('op_' + 'f' * 32, principal=OWNER)
    project = work_items._get_project(ops.db, project_id)
    await action(w, 'project.update', {'project_id': project_id},
        {'repositories': ['other/product']}, {'expected_version': project['version']})
    before, provider_calls = ops.db.total_changes, len(w['gh'].requests)
    with pytest.raises(OperationError, match='NOT_FOUND'):
        pm_work_batch_evidence.require_sources(reader, OWNER, w['workspace_id'], proof)
    with pytest.raises(OperationError, match='NOT_FOUND'):
        reader.get(observation['operation_id'], principal=OWNER)
    filtered = pm_snapshots._out(reader, OWNER, saved)['snapshot']
    assert filtered['visibility']['filtered'] and filtered['snapshot_digest'] == frozen['snapshot_digest']
    assert reference['review_id'] not in json.dumps(filtered)
    assert not pm_access.visible(ops.db, OWNER, intake)
    with pytest.raises(OperationError) as hidden:
        follow.read(ops, intake['result'], who=OWNER)
    assert hidden.value.status == 404
    assert ops.db.total_changes == before and len(w['gh'].requests) == provider_calls
