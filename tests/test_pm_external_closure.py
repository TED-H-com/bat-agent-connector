"""External closure is source state, never a substitute for fixed PM acceptance."""
from __future__ import annotations

import copy
import json
import re
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from bat_agent_connector import (
    delivery,
    pm_evidence,
    pm_github_observation,
    pm_github_sources,
    pm_issue_mappings,
    pm_lifecycle_gates,
    pm_maintenance_policy,
    pm_plans,
    pm_quality_reviews,
    pm_sprints,
    pm_workspace,
    pr_delivery,
)
from tests import test_pm_delivery_lineage as deliveries
from tests import test_pm_lifecycle as lifecycle
from tests import test_pm_maintenance_policy as policies
from tests import test_pm_quality_reviews as reviews
from tests import test_pm_release_baselines as releases
from tests import test_pm_requirement_acceptance as acceptance
from tests import test_pm_roadmap as roadmap
from tests import test_pm_snapshots as snapshots
from tests.test_api_v1 import http, token
from tests.test_checkpoints import bat_writes, snapshot
from tests.test_pm_work_packages import action

world, pm, ready = deliveries.world, deliveries.pm, deliveries.ready
fixed, delivery_ready = deliveries.fixed, deliveries.delivery_ready
OWNER = deliveries.DELIVERER


def provider_reads(w):
    """Only the external HTTP boundary supplies release/empty CI responses."""
    gh, previous, sha = w['gh'], w['gh'].before_request, w['state']['sha']
    responses = {
        '/repos/o/r/releases/41': {'id': 41, 'tag_name': 'v1.4.0', 'draft': False, 'prerelease': False,
            'published_at': '2026-01-01T00:00:00Z', 'target_commitish': 'main'},
        '/repos/o/r/git/ref/tags/v1.4.0': {'ref': 'refs/tags/v1.4.0', 'object': {'type': 'commit', 'sha': sha}},
        '/repos/o/r/git/commits/' + sha: {'sha': sha},
        '/repos/o/r/actions/runs': {'total_count': 0, 'workflow_runs': []},
    }

    def serve(method, path, body):
        answer = responses.get(path)
        if path.endswith('/check-suites'):
            answer = {'total_count': 0, 'check_suites': []}
        elif path.endswith('/statuses'):
            answer = []
        if method == 'GET' and answer is not None:
            gh.script.append(('GET', re.escape(path) + '$', 200, {}, copy.deepcopy(answer)))
        elif previous:
            previous(method, path, body)

    gh.before_request = serve
    gh.pulls[1]['id'] = 1001


@pytest.fixture
async def source_world(delivery_ready):
    w, ops = delivery_ready, delivery_ready['d'].ops
    pushed = await deliveries.settle(w, deliveries.apply(w, await deliveries.preview(w)))
    assert pushed['status'] == 'succeeded' and pushed['result']['pushed']
    await acceptance.configure_batch_child(w, w['original'])
    w['review'], _, _ = await acceptance.qualify(w)
    assert w['review']['qualified'] is True
    w['accepted_assessment'] = w['review']['assessment']
    await reviews.execute(w, reviews.assessment(w, w['review'], verdict='unassessed'), actor=reviews.REVIEWER)
    w['review'] = reviews.read(w, w['review'])
    w['quality_ref'] = {key: w['review']['assessment'][key] for key in ('operation_id', 'version', 'document_digest')}
    w['quality_ref'].update(review_id=w['review']['review_id'], candidate_commit=w['candidate'])
    assert not w['review']['qualified'] and w['review']['assessment']
    await lifecycle.decide(ops, w['workspace_id'], 'set_stage', who=OWNER, params={'stage': 'development'})
    provider_reads(w)
    mapping = await action(w, pm_issue_mappings.PUBLISH, w['target'],
        {'package_id': w['package']['package_id'], 'public_title': 'Verify login implementation',
         'public_body': 'Selected public login behavior.'},
        {'expected_package_digest': w['package']['document_digest'], 'expected_mapping_revision': None}, who=OWNER)
    assert mapping['issue']['number'] == 2 and not mapping['publication']['counts_as_done']
    w['mapping_id'] = mapping['mapping_id']
    w['gh'].issues[2]['labels'] = [{'name': 'bug'}]
    project = pm_evidence.list_projects(ops, OWNER, w['workspace_id'])['bindings'][0]
    baseline = releases.request(ops, {'workspace_id': w['workspace_id'], 'repository': 'o/r'}, project['binding_id'])
    baseline['preconditions']['expected_commit_sha'] = w['state']['sha']
    await releases.execute(ops, baseline, who=OWNER)
    policy = policies.policy()
    policy.update(allowed_work_kinds=[], delivery={'maximum': 'report', 'environment': None})
    policy['observation'].update(github_profile=pm_github_sources.PROFILE,
        maintenance_sources={'issues': {'labels_all': ['bug']}, 'dependencies': None})
    await releases.execute(ops, policies.request(ops, w['workspace_id'], document=policy), who=OWNER)
    binding = pm_evidence._binding(ops.db, **w['target'])
    plan = pm_plans.accepted_plan(ops, OWNER, w['workspace_id'])['plan']
    await roadmap.revision(ops, w['workspace_id'], who=OWNER, milestones=[roadmap.milestone(plan,
        requirement_ids=['member.login'], work_binding_ids=[binding['binding_id']])])
    await action(w, 'pm.evidence.capture', w['target'], {'quality_ref': w['quality_ref']},
        {'expected_binding_id': binding['binding_id']}, who=OWNER)
    await active_sprint(w, binding, plan)
    w['frozen'] = await snapshots.snapshot(ops, w['workspace_id'], who=OWNER)
    card = await delivery.pr_preview(ops, 'o/r', 1)
    envelope = pr_delivery.merge_envelope(card['merge_preview'])
    envelope['idempotency_key'] = 'reviewed-external-closure'
    w['merge'] = ops.create(OWNER, **envelope)[0]
    w['method'] = card['merge_preview']['method']
    w['auth'] = token(w['d'], OWNER.actor, *OWNER.scopes)
    return w


async def active_sprint(w, binding, plan):
    ops, wid = w['d'].ops, w['workspace_id']
    goal = await action(w, 'pm.product_goal.revise', {'workspace_id': wid},
        {'plan_revision_id': plan['revision_id'], 'text': 'Deliver the original login requirement', 'rationale': 'Selected scope'},
        {'expected_revision_id': None}, who=OWNER)
    backlog = await action(w, 'pm.backlog.revise', {'workspace_id': wid},
        {'product_goal_revision_id': goal['revision_id'], 'binding_ids': [binding['binding_id']], 'rationale': 'Selected Work'},
        {'expected_revision_id': None}, who=OWNER)
    now = datetime.now(timezone.utc)
    sprint = await action(w, 'pm.sprint.create', {'workspace_id': wid},
        {'title': 'Login delivery', 'mode': 'personal_iteration', 'owner_actor': OWNER.actor, 'developers': [OWNER.actor],
         'backlog_revision_id': backlog['revision_id'], 'dod_revision_id': w['dod'], 'goal': 'Preserve the original quality bar',
         'starts_at': (now - timedelta(days=1)).isoformat(), 'ends_at': (now + timedelta(days=1)).isoformat(),
         'capacity_note': 'Fixture iteration', 'rationale': 'Keep progress separate from external closure'},
        {'expected_workspace_version': pm_workspace.get_workspace(ops.db, OWNER, wid)['version']}, who=OWNER)
    w['sprint_target'] = {'workspace_id': wid, 'sprint_id': sprint['sprint_id']}
    for name, params in [('select', {'items': [{'binding_id': binding['binding_id'], 'assignee': OWNER.actor,
                                            'how': 'Implement the fixed selected package'}]}), ('start', {})]:
        current = pm_sprints.get_sprint(ops, OWNER, **w['sprint_target'])['sprint']
        await action(w, 'pm.sprint.' + name, w['sprint_target'], {'rationale': 'Explicit iteration decision', **params},
            {'expected_version': current['version']}, who=OWNER)


async def read(w, suffix):
    status, body = await http(w['port'], 'GET', f"/api/v1/pm/workspaces/{w['workspace_id']}/" + suffix, tok=w['auth'])
    assert status == 200, body
    return body


async def views(w):
    result = {name: await read(w, name) for name in ('overview', 'kanban', 'schedule', 'roadmap', 'register')}
    result['sprint'] = await read(w, 'sprints/' + w['sprint_target']['sprint_id'])
    result['mapping'] = await read(w, 'issue-mappings/' + w['mapping_id'])
    return result


async def observe(w):
    ops, wid = w['d'].ops, w['workspace_id']
    policy = pm_maintenance_policy.active_record(ops, OWNER, wid)
    done = await releases.execute(ops, {'action': pm_github_observation.ACTION, 'target': {'workspace_id': wid},
        'params': {'policy_id': policy['policy_id']}, 'preconditions': {
            'expected_policy_version': policy['version'], 'expected_baseline_id': policy['baseline_id'],
            'expected_lifecycle_revision': pm_lifecycle_gates.head(ops.db, wid)['revision']},
        'idempotency_key': uuid.uuid4().hex}, who=OWNER)
    return (await read(w, 'github-observations/' + done['result']['observation_id']))['observation']['document']


def fixed_rows(w):
    db = w['d'].ops.db
    sprint_tables = [row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'pm_sprint_%'")]
    return {table: [tuple(row) for row in db.execute(f'SELECT * FROM {table} ORDER BY rowid')]
        for table in ['pm_quality_reviews', 'pm_quality_review_records', 'pm_quality_review_heads', 'pm_plan_adoptions', 'pm_issue_revisions', *sprint_tables]}


def counts(view):
    """Visible PM counts, not journal cursors, elapsed time or observation history."""
    overview, board = view['overview'], view['kanban']
    return {'progress': overview['progress'], 'attention': overview['attention_counts'],
        'decisions': overview['recent_decisions_count'], 'deliveries': len(overview['recent_deliveries']),
        'milestones': overview['milestones'], 'wip': board['wip'],
        'columns': {column: sum(card['column'] == column for card in board['cards'])
            for column in ('backlog', 'in_progress', 'waiting', 'review', 'accepted', 'archived')},
        'accepted_sends': [card['execution']['accepted_attempts'] for card in board['cards']],
        'register': view['register']['summary'], 'register_types': view['register']['total_by_type'],
        'register_rows': len(view['register']['rows']), 'sprint_items': len(view['sprint']['selected_items'])}


@pytest.mark.parametrize('merged', [False, True])
@pytest.mark.parametrize('reason', ['not_planned', 'completed'])
async def test_external_closure_preserves_fixed_acceptance_and_only_verified_merge_counts(source_world, mock, merged, reason):
    w, ops = source_world, source_world['d'].ops
    original = await observe(w)
    assert original['evidence']['status'] == 'complete'
    assert original['evidence']['observed_open_pulls'] is True
    issues = original['evidence']['maintenance_sources']['issues']
    assert issues['status'] == 'complete' and len(issues['findings']) == 1
    assert issues['findings'][0]['existing_work'][0]['work_item_id'] == w['target']['work_item_id']
    assert issues['findings'][0]['existing_work'][0]['mapping_id'] == w['mapping_id']
    before = await views(w)
    assert before['overview']['progress']['complete']
    assert before['overview']['progress']['requirements'] == 1
    assert before['overview']['progress']['accepted'] == 0
    assert before['overview']['recent_deliveries'] == []
    assert len(before['sprint']['selected_items']) == 1 and before['sprint']['sprint']['status'] == 'active'
    assert before['register']['coverage']['complete']
    fixed_before, manual, frames = fixed_rows(w), snapshot(w['human']), bat_writes(mock)
    assert fixed_before['pm_quality_review_records'] and fixed_before['pm_plan_adoptions']

    # GitHub REST source fields, with a separate positive merge contrast:
    # https://docs.github.com/en/rest/pulls/pulls#get-a-pull-request
    # https://docs.github.com/en/rest/issues/issues#update-an-issue
    if merged:
        w['gh'].methods[1] = w['method']
        w['gh'].merge(1)
    else:
        now = datetime.now(timezone.utc).isoformat()
        w['gh'].pulls[1].update(state='closed', merged=False, merged_at=None, closed_at=now, updated_at=now)
    now = datetime.now(timezone.utc).isoformat()
    w['gh'].issues[2].update(state='closed', state_reason=reason, closed_at=now, updated_at=now)
    gh = ops.context['github']
    status, source = await gh.pull('o/r', 1)
    assert status == 200 and source['state'] == 'closed' and source['merged'] is merged
    assert (source['merged_at'] is not None) is merged
    status, issue = await gh.call('GET', '/repos/o/r/issues/2')
    assert status == 200 and issue['state'] == 'closed' and issue['state_reason'] == reason

    await ops._execute(w['merge']['operation_id'])
    result = ops.get(w['merge']['operation_id'])
    if merged:
        assert result['status'] == 'succeeded' and result['result']['verified'] is True, result
    else:
        assert result['status'] == 'failed' and result['error_code'] == 'PR_CLOSED', result
    assert w['gh'].count('PUT', 'merge-async') == 0
    observed = await observe(w)
    assert observed['evidence']['status'] == 'complete'
    assert observed['evidence']['facts']['pulls'] == []
    assert observed['evidence']['observed_open_pulls'] is False
    issues = observed['evidence']['maintenance_sources']['issues']
    assert issues['status'] == 'complete' and issues['findings'] == []
    # An absent finding is not a retrospective resolved/accepted record.
    old = await read(w, 'github-observations/' + original['observation_id'])
    assert old['observation']['document'] == original

    after = await views(w)
    assert after['overview']['progress'] == before['overview']['progress']
    assert after['kanban']['cards'][0]['work'] == before['kanban']['cards'][0]['work']
    assert after['kanban']['cards'][0]['completion'] == before['kanban']['cards'][0]['completion']
    assert after['kanban']['cards'][0]['column'] == before['kanban']['cards'][0]['column']
    assert after['kanban']['cards'][0]['quality'] == before['kanban']['cards'][0]['quality']
    assert after['sprint']['sprint'] == before['sprint']['sprint']
    assert after['sprint']['selected_items'] == before['sprint']['selected_items']
    assert after['sprint']['quality'] == before['sprint']['quality']
    assert after['roadmap'] == before['roadmap']
    assert after['mapping'] == before['mapping']
    assert fixed_rows(w) == fixed_before
    original_assessment = pm_quality_reviews.get_assessment(ops, OWNER, w['workspace_id'], w['review']['review_id'],
        **{key: w['accepted_assessment'][key] for key in ('operation_id', 'version', 'document_digest')})
    assert original_assessment['assessment'] == w['accepted_assessment']
    assert (await read(w, 'snapshots/' + w['frozen']['snapshot_id']))['snapshot'] == w['frozen']
    assert (await read(w, 'quality-reviews/' + w['review']['review_id']))['review']['assessment'] == w['review']['assessment']
    binding = pm_evidence._binding(ops.db, **w['target'])
    await action(w, 'pm.evidence.capture', w['target'], {'quality_ref': w['quality_ref']},
        {'expected_binding_id': binding['binding_id']}, who=OWNER)
    current = await snapshots.snapshot(ops, w['workspace_id'], who=OWNER)
    lineage = await read(w, 'work/' + w['target']['work_item_id'] + '/deliveries')
    recorded = next(row for row in lineage['deliveries'] if row['operation_id'] == result['operation_id'])
    assert recorded['requirement_acceptance'] == 'not_evaluated_by_delivery'
    assert (recorded['merge']['merged'] is True) is merged
    assert (recorded['merge']['verified'] is True) is merged
    assert current['brief']['delivery']['coverage']['qualified_deliverables'] == 0
    assert w['frozen']['brief']['delivery']['coverage']['qualified_deliverables'] == 0
    assert current['brief']['delivery']['quality'] == w['frozen']['brief']['delivery']['quality']
    if merged:
        assert len(after['overview']['recent_deliveries']) == 1
        assert after['overview']['recent_deliveries'][0]['state'] == 'merged'
        assert after['overview']['progress']['accepted'] == 0
    else:
        before_counts, after_counts = counts(before), counts(after)
        assert after_counts['progress'] == before_counts['progress']
        assert after_counts['deliveries'] == before_counts['deliveries'] == 0
        assert after_counts['wip'] == before_counts['wip']
        assert after_counts['columns'] == before_counts['columns']
        assert after_counts['accepted_sends'] == before_counts['accepted_sends']
        assert after_counts['decisions'] == before_counts['decisions']
        assert after_counts['milestones'] == before_counts['milestones']
        assert after_counts['sprint_items'] == before_counts['sprint_items'] == 1
        assert after_counts['register_rows'] <= before_counts['register_rows']
        for kind, count in after_counts['register_types'].items():
            assert count <= before_counts['register_types'][kind], kind
        assert {row['id']: row['status'] for row in after['schedule']['rows'] + after['schedule']['unscheduled']} == {
            row['id']: row['status'] for row in before['schedule']['rows'] + before['schedule']['unscheduled']}
        for kind, count in after_counts['attention'].items():
            assert count <= before_counts['attention'][kind], kind
        assert sum(row['resolved'] for row in after_counts['register']['weekly']) == sum(
            row['resolved'] for row in before_counts['register']['weekly'])
        assert after_counts['register']['weekly'] == before_counts['register']['weekly']
        assert after_counts['register']['open_by_age'] == before_counts['register']['open_by_age']
        assert after_counts['register']['blocking_reasons'] == before_counts['register']['blocking_reasons']
    assert bat_writes(mock) == frames and snapshot(w['human']) == manual
    assert w['gh'].count('POST', r'/issues$') == 1
    print(json.dumps({'closure': reason, 'merged': merged, 'accepted': after['overview']['progress']['accepted'],
        'recent_deliveries': len(after['overview']['recent_deliveries']), 'fixed_records_unchanged': True,
        'qualified_deliverables': current['brief']['delivery']['coverage']['qualified_deliverables']}, sort_keys=True))
