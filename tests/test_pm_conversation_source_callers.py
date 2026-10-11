"""Public raw-input/revision consumers cannot launder a withdrawn proposal."""
from __future__ import annotations

import pytest

from bat_agent_connector import (
    pm_access,
    pm_follow_up,
    pm_follow_up_analysis,
    pm_plan_diff,
    pm_plans,
    pm_reads,
    pm_snapshots,
    pm_work_packages,
    pm_workspace,
    work_items,
)
from bat_agent_connector.operations import OpContext
from tests import test_pm_conversation_source_privacy as privacy
from tests import test_pm_follow_up as followup
from tests import test_pm_plan_operations as plans
from tests import test_pm_work_packages as packages
from tests.operation_helpers import settle_operations

world = privacy.world
pm = privacy.pm
ready = privacy.ready
proposal_world = privacy.proposal_world
OWNER = privacy.OWNER


@pytest.mark.parametrize('legacy_refs', [False, True])
async def test_saved_followup_and_analysis_retain_exact_supplemental_input_sources(proposal_world, monkeypatch, legacy_refs):
    w, ops = proposal_world, proposal_world['d'].ops
    assert (await plans.adopt(w))['status'] == 'succeeded'
    original_snapshot = await privacy.snapshot(w)
    pm_follow_up.install(ops)
    data = {'wid': w['workspace_id'], 'target': plans.target(w), 'snapshot': original_snapshot['result']}
    before = await followup.prepare(ops, data, who=OWNER, thread_id=data['target']['thread_id'])
    original = pm_follow_up.get_intake(ops, OWNER, data['wid'], before['intake_id'])['intake']
    access = privacy.withdrawal(w, monkeypatch)
    await privacy.adopt_proposal(w)
    request = followup.request(ops, data, thread_id=data['target']['thread_id'])
    with monkeypatch.context() as previous_version:
        if legacy_refs:
            # Retain an old producer's exact immutable bytes, which already
            # contain operation IDs but predate explicit input access_refs.
            selected = pm_follow_up._thread_inputs
            previous_version.setattr(pm_follow_up, '_thread_inputs', lambda *args:
                (*selected(*args)[:3], []))
        op = ops.create(OWNER, **request)[0]
        await settle_operations(ops)
    op = ops.get(op['operation_id'])
    assert op['status'] == 'succeeded', op
    intake = pm_follow_up.get_intake(ops, OWNER, data['wid'], op['result']['intake_id'])['intake']
    supplemental = [*intake['document']['unmapped_inputs'],
        *[row for domain in intake['document']['knowledge'].values() for row in domain['supplemental_inputs']]]
    excluded = next(row for row in supplemental if row.get('review_disposition', {}).get('candidate_key') == 'referral')
    assert excluded['review_disposition']['included'] is False
    assert excluded['review_disposition']['original_kind'] == 'excluded'
    assert any(ref['kind'] == 'input_operation' for ref in intake['document']['access_refs']) is not legacy_refs
    pm_follow_up_analysis.install(ops)
    analysis = ops.create(OWNER, action=pm_follow_up_analysis.ACTION, target=data['target'],
        params={'intake_id': intake['intake_id'], 'purpose': 'Review this fixed original intake'},
        preconditions={'expected_version': plans.version(w), 'expected_intake_digest': intake['document_digest']},
        idempotency_key='privacy-original-intake-analysis')[0]
    assert pm_follow_up_analysis.operation_visible(ops.db, OWNER, analysis)
    next_request = followup.request(ops, data, thread_id=data['target']['thread_id'])
    sends = sum(instance.sends for instance in w['instances'])
    access['available'] = False
    privacy.unavailable(lambda: pm_follow_up.get_intake(ops, OWNER, data['wid'], intake['intake_id']))
    privacy.unavailable(lambda: pm_follow_up.latest_intake(ops, OWNER, data['wid']))
    privacy.unavailable(lambda: ops.create(OWNER, **next_request))
    assert not pm_access.visible(ops.db, OWNER, op)
    assert not pm_follow_up_analysis.operation_visible(ops.db, OWNER, analysis)
    events = ops.journal.api_events(resource_type='pm_follow_up')['events']
    event = next(row for row in events if row['resource_id'] == intake['intake_id'])
    assert not pm_access.event_visible(ops.db, OWNER, event)
    old = pm_follow_up.get_intake(ops, OWNER, data['wid'], original['intake_id'])
    assert old['intake'] == original and old['head_version'] is None and old['head_unavailable']
    page = pm_follow_up.list_intakes(ops, OWNER, data['wid'], after=original['sequence'], limit=1)
    assert page['intakes'] == [] and page['coverage']['visibility_filtered']
    assert page['head_version'] is None and page['head_unavailable']
    assert pm_snapshots.get_snapshot(ops, OWNER, data['wid'], data['snapshot']['snapshot_id'])['snapshot']
    assert await pm_follow_up._run(OpContext(ops, op)) == op['result']
    await settle_operations(ops)
    assert ops.get(analysis['operation_id'])['status'] == 'failed'
    assert sum(instance.sends for instance in w['instances']) == sends


async def engineering_package(w, revision):
    ops, wid = w['d'].ops, w['workspace_id']
    pm_work_packages.install(ops)
    project = (await packages.action(w, 'project.create', params={'name': 'Private sourced plan', 'repositories': ['o/r']}))['project_id']
    work = (await packages.action(w, 'work_item.create', {'project_id': project},
        {'title': 'Login', 'goal': 'Implement reviewed account access', 'acceptance': 'Valid login; invalid password rejected'}))['work_item_id']
    await packages.action(w, 'pm.project.bind', {'workspace_id': wid, 'project_id': project}, {'rationale': 'Use this original plan'},
        {'expected_workspace_version': pm_workspace.get_workspace(ops.db, OWNER, wid)['version'],
         'expected_project_version': work_items._get_project(ops.db, project)['version']})
    w['target'] = {'workspace_id': wid, 'work_item_id': work}
    w['revision'] = revision
    await packages.associate(w)
    requirement = next(row for row in revision['document']['requirements'] if row['id'] == 'member.login')
    w['params'] = {'title': 'Original conversation engineering package', 'requirements': ['member.login'], 'source_refs': [],
        'code_source': w['selection'], 'rationale': 'Preserve this exact reviewed requirement',
        'design': {'status': 'unresolved', 'text': 'Detailed design remains open', 'source_ids': []},
        'tasks': [{'key': 'member.login', 'description': 'Implement the fixed criterion', 'requirement_ids': ['member.login'], 'depends_on': [], 'source_ids': []}],
        'verification': [{'key': 'verify.login', 'requirement_id': 'member.login',
            'acceptance_input_ids': [row['source_input_id'] for row in requirement['acceptance']],
            'method': 'Run the login regression', 'expected_evidence': 'Exact candidate result', 'source_ids': []}],
        'delivery': {'repository': 'o/r', 'repository_id': w['selection']['repository_id'], 'base_ref': 'refs/heads/main', 'goal': 'pull_request',
            **{key: {'status': 'not_applicable', 'text': 'Requires a separate delivery decision'} for key in ('migration', 'rollout', 'rollback', 'observation')}}}
    return await packages.create(w)


async def test_exact_plan_diff_package_and_shared_head_deny_withdrawn_sources(proposal_world, monkeypatch):
    w, ops = proposal_world, proposal_world['d'].ops
    access = privacy.withdrawal(w, monkeypatch)
    old_revision = w['revision']['revision_id']
    _, adoption = await privacy.adopt_proposal(w)
    current = pm_plans.get_plan(ops, OWNER, w['workspace_id'], adoption['result']['revision_id'])['plan']
    package_op = await engineering_package(w, current)
    package = packages.read(w, package_op)
    assert package['document']['context']['plan_ref']['revision_id'] == current['revision_id']
    assert pm_plan_diff.compare(ops, OWNER, w['workspace_id'], old_revision, current['revision_id'])
    assert pm_reads.read(ops, OWNER, 'pm_plan_head_get', {'workspace_id': w['workspace_id']})
    access['available'] = False
    for first, second in ((old_revision, current['revision_id']), (current['revision_id'], old_revision)):
        privacy.unavailable(lambda: pm_plan_diff.compare(ops, OWNER, w['workspace_id'], first, second))
    privacy.unavailable(lambda: packages.read(w, package_op))
    privacy.unavailable(lambda: pm_reads.read(ops, OWNER, 'pm_plan_head_get', {'workspace_id': w['workspace_id']}))
    assert not pm_work_packages.operation_visible(ops.db, OWNER, package_op)
    assert not pm_access.visible(ops.db, OWNER, package_op)
    page = pm_work_packages.list_packages(ops, OWNER, w['workspace_id'], work_item_id=w['target']['work_item_id'])
    assert page['packages'] == [] and page['coverage']['visibility_filtered']
    assert pm_plans.get_plan(ops, OWNER, w['workspace_id'], old_revision)['plan']['revision_id'] == old_revision
    assert await pm_work_packages._run(OpContext(ops, package_op)) == package_op['result']
