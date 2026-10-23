"""Result relations use original Connector producers, disk Git and mock providers."""
import copy
import json
import uuid

import pytest

from bat_agent_connector import (
    pm_attempt_result_reads,
    pm_quality_reviews,
    pm_result_requirements,
    pm_work_packages,
    work_items,
)
from bat_agent_connector.api_auth import Principal
from bat_agent_connector.operations import OperationError
from tests import test_pm_execution_verification as verifier
from tests import test_pm_package_handoff as handoffs
from tests import test_pm_quality_reviews as reviews
from tests import test_pm_requirement_acceptance as acceptance
from tests import test_pm_work_attempt_result as results
from tests import test_pm_work_packages as packages
from tests.test_api_v1 import http, token
from tests.test_checkpoints import git
from tests.test_integration import commit
from tests.test_pm_plan_operations import adopt, draft
from tests.test_pm_work_start import STARTER_OWNER as OWNER

world, pm, ready, start_ready = results.world, results.pm, results.ready, results.start_ready


@pytest.fixture
async def two_results(start_ready, mock):
    w, ops = start_ready, start_ready['d'].ops
    requirements = copy.deepcopy(w['requirements'])
    requirements.append({**copy.deepcopy(requirements[0]), 'id': 'member.recovery'})
    requirements.append({**copy.deepcopy(requirements[0]), 'id': 'member.followup', 'depends_on': ['member.login']})
    w['revision'] = await draft(w, requirements=requirements)
    assert (await adopt(w, w['revision']))['status'] == 'succeeded'
    await packages.associate(w)
    w['source_task'], evidence = await packages.add_task_evidence(w)
    w['package'] = packages.read(w, await packages.create(w, evidence_refs=[evidence]))
    first = await results._start_managed_attempt(w, mock, key='result-login')
    project = work_items._get_item(ops.db, w['target']['work_item_id'])['project_id']
    item = await packages.action(w, 'work_item.create', {'project_id': project},
        {'title': 'Independent recovery', 'goal': 'Recovery behavior', 'acceptance': 'Original recovery'})
    target = {'workspace_id': w['workspace_id'], 'work_item_id': item['work_item_id']}
    await packages.action(w, 'pm.work.bind', target,
        {'rationale': 'Explicit recovery', 'plan_revision_id': w['revision']['revision_id'], 'requirement_ids': ['member.recovery']},
        {'expected_binding_id': None, 'expected_work_version': work_items._get_item(ops.db, target['work_item_id'])['version']})
    second = await results._start_managed_attempt(w, mock, key='result-recovery', work_item_id=target['work_item_id'])
    for attempt in (first, second):
        results._set_turn_messages(mock, attempt['session_id'], attempt['turn_marker'])
    await w['d'].inventory._refresh_scheduled('h1')
    await w['d'].result_collector.collect_host('h1')
    return w, first, second


async def test_two_results_retain_only_their_explicit_original_requirements(two_results):
    w, first, second = two_results
    for attempt, rid in ((first, 'member.login'), (second, 'member.recovery')):
        result = await results._result(w, attempt)
        section = result['requirements']
        assert section['relation'] == 'explicit'
        assert [row['requirement_id'] for row in section['items']] == [rid]
        package = pm_work_packages.get_package(w['d'].ops, OWNER, w['workspace_id'], result['identity']['package_id'])['package']
        assert section['package_ref']['document_digest'] == package['document_digest']
        assert section['plan_ref'] == package['document']['context']['plan_ref']
        assert section['items'][0]['assessments'] == []
        assert section['items'][0]['state'] == 'no_conclusion'


async def verified(w, attempt):
    ops = w['d'].ops
    result = await results._result(w, attempt)
    package = pm_work_packages.get_package(ops, OWNER, w['workspace_id'], result['identity']['package_id'])['package']
    local = {**w, 'package': package, 'target': {'workspace_id': w['workspace_id'], 'work_item_id': attempt['work_item_id']}}
    await acceptance.configure_batch_child(local, ops.get(attempt['handoff_operation_id']))
    proof = await reviews.execute(local, verifier.envelope(local), actor=OWNER)
    assert proof['result']['status'] == 'passed'
    local.update(candidate=local['config']['candidate_commit'], tree=local['config']['tree_hash'], proof=proof['result']['reference'])
    return local


async def conclude(w, verdict='accepted', previous=None):
    if 'dod' not in w:
        await packages.member(w, reviews.REVIEWER.actor, 'contributor')
        w['dod'] = (await packages.action(w, 'pm.dod.revise', {'workspace_id': w['workspace_id']},
            {'plan_revision_id': w['package']['document']['context']['plan_ref']['revision_id'],
             'criteria': [{'id': 'regression', 'text': 'Original fixed verifier'}], 'rationale': 'Original DoD'},
            {'expected_revision_id': None}))['revision_id']
    env = reviews.selection(w, previous=previous)
    for row in env['params']['criteria']:
        row['condition'] = {'kind': 'fixed_revision_verification', 'profile': 'observed_runner_v1', 'command_digest': w['config']['command_digest']}
    created = await reviews.execute(w, env)
    row = pm_quality_reviews.get_review(w['d'].ops, OWNER, w['workspace_id'], created['result']['review_id'])['review']
    await reviews.execute(w, reviews.assessment(w, row, verdict=verdict), actor=reviews.REVIEWER)
    return reviews.read(w, row)


def reference(review):
    return {key: review['assessment'][key] for key in ('operation_id', 'version', 'document_digest')} | {
        'review_id': review['review_id'], 'candidate_commit': review['context']['native_result']['candidate_commit']}


async def test_verification_conclusions_and_every_original_assessment_stay_distinct(two_results):
    w, first, second = two_results
    local = await verified(w, first)
    row = (await results._result(w, first))['requirements']['items'][0]
    assert row['state'] == 'verified_no_conclusion' and row['assessments'] == []
    accepted = await conclude(local)
    before = (await results._result(w, first))['requirements']['items'][0]
    assert before['assessments'][0]['reference'] == reference(accepted)
    assert before['assessments'][0]['state'] == 'accepted' and before['assessments'][0]['qualified']
    await reviews.execute(local, reviews.assessment(local, accepted, verdict='rejected'), actor=reviews.REVIEWER)
    after = (await results._result(w, first))['requirements']['items'][0]
    assert after['selection'] == 'undecided'
    assert [row['conclusion'] for row in after['assessments']] == ['accepted', 'rejected']
    assert after['assessments'][0]['original'] == before['assessments'][0]['original']
    assert after['assessments'][0]['state'] == 'no_longer_applies'
    assert after['assessments'][0]['applicability_reasons'] == ['PM_QUALITY_VERSION_CONFLICT']
    assert after['assessments'][1]['state'] == 'rejected'
    assert (await results._result(w, second))['requirements']['items'][0]['assessments'] == []


async def downstream(w):
    ops = w['d'].ops
    project = w['child']['params']['project_id']
    item = await packages.action(w, 'work_item.create', {'project_id': project},
        {'title': 'Private dependent title', 'goal': 'Declared followup', 'acceptance': 'Original followup'})
    target = {'workspace_id': w['workspace_id'], 'work_item_id': item['work_item_id']}
    await packages.action(w, 'pm.work.bind', target,
        {'rationale': 'Explicit prerequisite', 'plan_revision_id': w['revision']['revision_id'], 'requirement_ids': ['member.followup']},
        {'expected_binding_id': None, 'expected_work_version': work_items._get_item(ops.db, target['work_item_id'])['version']})
    dw = {**w, 'target': target, 'params': copy.deepcopy(w['params'])}
    dw['params']['requirements'] = ['member.followup']
    dw['params']['tasks'][0]['requirement_ids'] = ['member.followup']
    dw['params']['verification'][0]['requirement_id'] = 'member.followup'
    dw['source_task'], evidence = await packages.add_task_evidence(dw)
    dw['package'] = packages.read(dw, await packages.create(dw, evidence_refs=[evidence]))
    return dw


async def test_dependencies_reuse_readiness_and_frozen_selection_then_revocation(two_results):
    w, first, _ = two_results
    local = await verified(w, first)
    review = await conclude(local)
    dw = await downstream(local)
    ops, wid, pid = w['d'].ops, w['workspace_id'], dw['package']['package_id']
    refs = {'member.login': {'condition': acceptance.acceptance.freeze_condition(local['package'], ['member.login']),
        'reference': reference(review), 'execution_operation_id': local['child']['operation_id']}}
    empty = (await results._result(w, first))['requirements']['dependent_work'][0]
    assert empty['readiness'] == pm_work_packages.readiness(ops, OWNER, wid, pid)
    assert empty['readiness']['status'] == 'blocked' and empty['selections'] == []
    child = await handoffs.run(dw, await handoffs.request(dw, pm_requirement_quality_refs=refs))
    assert child['status'] == 'succeeded'
    selected = (await results._result(w, first))['requirements']['dependent_work'][0]['selections'][0]
    assert selected['quality_refs'] == refs
    assert selected['readiness'] == pm_work_packages.readiness(ops, OWNER, wid, pid, quality_refs=refs)
    assert selected['readiness']['status'] == 'ready'
    await reviews.execute(local, {'action': 'pm.quality.review.revoke',
        'target': {'workspace_id': wid, 'review_id': review['review_id']}, 'params': {'reason': 'Original counterexample'},
        'preconditions': {'expected_version': review['version']}, 'idempotency_key': uuid.uuid4().hex})
    section = (await results._result(w, first))['requirements']
    assert section['items'][0]['assessments'][0]['conclusion'] == 'accepted'
    assert section['items'][0]['assessments'][0]['state'] == 'no_longer_applies'
    assert 'review_revoked' in section['items'][0]['assessments'][0]['applicability_reasons']
    assert section['dependent_work'][0]['selections'][0]['readiness'] == pm_work_packages.readiness(ops, OWNER, wid, pid, quality_refs=refs)
    assert section['dependent_work'][0]['selections'][0]['readiness']['status'] == 'blocked'


@pytest.mark.parametrize('change', ['scope', 'candidate', 'source'])
async def test_changed_context_does_not_rewrite_history(two_results, change):
    w, first, _ = two_results
    local = await verified(w, first)
    review = await conclude(local)
    original = copy.deepcopy(review['assessment'])
    if change == 'scope':
        revision = await draft(w, requirements=w['requirements'])
        assert (await adopt(w, revision))['status'] == 'succeeded'
    elif change == 'candidate':
        wt = first['worktree_path']
        git(wt, 'config', 'user.name', 'fixture')
        git(wt, 'config', 'user.email', 'fixture@example.invalid')
        first['new_candidate'] = commit(wt, 'new.txt', 'A different candidate', 'New candidate')
    else:
        # Withdraw the original verifier's source; the review must disappear,
        # without returning its title, conclusion or a source path in an error.
        w['d'].ops.db.execute("UPDATE tasks SET controller_authority='{}' WHERE task_id=?", (w['source_task']['task_id'],))
    candidate = first.get('new_candidate', local['candidate'])
    section = pm_result_requirements.read(w['d'].ops, OWNER, w['workspace_id'], local['child']['operation_id'], candidate, 'verified')
    if change == 'source':
        assert section == pm_result_requirements.read(w['d'].ops, OWNER, w['workspace_id'], None, candidate, 'verified')
        assert section['relation'] == 'none'
    else:
        saved = section['items'][0]['assessments'][0]
        assert saved['state'] == 'no_longer_applies' and not saved['qualified']
        assert ('scope_revision_changed' if change == 'scope' else 'candidate_commit_changed') in saved['applicability_reasons']
        assert saved['original'] == original['document']
    stored = w['d'].ops.db.execute('SELECT document FROM pm_quality_review_records WHERE operation_id=?', (original['operation_id'],)).fetchone()[0]
    assert json.loads(stored) == original['document']


async def test_no_binding_is_explicit_and_never_inferred(two_results):
    w, first, _ = two_results
    ops, wid = w['d'].ops, w['workspace_id']
    missing = pm_result_requirements.read(ops, OWNER, wid, None, first['base_sha'], 'verified')
    assert missing == {'relation': 'none', 'reason': 'no_visible_explicit_binding', 'items': [], 'dependent_work': []}
    # A real operation contains the same title/text but has no handoff contract.
    operation = ops.db.execute("SELECT operation_id FROM operations WHERE action='work_item.create' LIMIT 1").fetchone()[0]
    assert pm_result_requirements.read(ops, OWNER, wid, operation, first['base_sha'], 'verified') == missing
    assert pm_result_requirements.read(ops, OWNER, wid, 'op_' + '0' * 32, first['base_sha'], 'verified') == missing


async def test_observe_membership_transport_parity_and_no_writes(two_results, monkeypatch, mock):
    w, first, _ = two_results
    local = await verified(w, first)
    await conclude(local)
    d, wid, aid = w['d'], w['workspace_id'], first['attempt_id']
    for role in ('owner', 'contributor', 'reader'):
        actor = 'relation-' + role
        await packages.member(w, actor, role)
        auth = token(d, actor, 'observe')
        principal = Principal(actor, frozenset({'observe'}))
        before = (d.ops.db.total_changes, d.ops.journal.api_head(), len(mock.invokes))
        status, actual = await http(w['port'], 'GET', f'/api/v1/pm/workspaces/{wid}/work-attempts/{aid}/result', tok=auth)
        assert status == 200, actual
        params = {'workspace_id': wid, 'attempt_id': aid}
        assert (await d.call_api('pm_work_attempt_result_get', dict(params), principal))['requirements'] == actual['requirements']
        assert (await results._call_mcp_tool(w, monkeypatch, auth, 'pm_work_attempt_result_get', params))['requirements'] == actual['requirements']
        assert before == (d.ops.db.total_changes, d.ops.journal.api_head(), len(mock.invokes))
    from tests.test_pm_workspace import create
    await create(d.ops, who=Principal('outsider', frozenset({'observe', 'manage'})))
    for who in (Principal('outsider', frozenset({'observe'})), Principal(actor, frozenset())):
        with pytest.raises(OperationError) as denied:
            await pm_attempt_result_reads.read(d.ops, who, 'pm_work_attempt_result_get', {'workspace_id': wid, 'attempt_id': aid})
        assert denied.value.status in {403, 404}


async def test_hidden_requirement_and_work_titles_are_not_sent(two_results):
    w, first, _ = two_results
    local = await verified(w, first)
    dw = await downstream(local)
    ops = w['d'].ops
    await packages.member(w, 'relation-reader', 'reader')
    reader = Principal('relation-reader', frozenset({'observe'}))
    def read():
        return pm_result_requirements.read(ops, reader, w['workspace_id'], local['child']['operation_id'], local['candidate'], 'verified')
    assert 'Private dependent title' in json.dumps(read())
    ops.db.execute("UPDATE tasks SET controller_authority='{}' WHERE task_id=?", (dw['source_task']['task_id'],))
    assert 'Private dependent title' not in json.dumps(read())
    ops.db.execute("UPDATE tasks SET controller_authority='{}' WHERE task_id=?", (w['source_task']['task_id'],))
    assert read() == pm_result_requirements.read(ops, reader, w['workspace_id'], None, local['candidate'], 'verified')
    assert 'Members can log in' not in json.dumps(read())


async def test_partial_coverage_and_whole_document_byte_limit(two_results, monkeypatch):
    w, first, _ = two_results
    local = await verified(w, first)
    await conclude(local)
    def read():
        return pm_result_requirements.read(w['d'].ops, OWNER, w['workspace_id'], local['child']['operation_id'], local['candidate'], 'verified')
    original = read()
    size = len(json.dumps(original, ensure_ascii=False).encode('utf-8'))
    monkeypatch.setattr(pm_result_requirements, 'MAX_BYTES', size)
    assert read() == original
    monkeypatch.setattr(pm_result_requirements, 'MAX_BYTES', size - 1)
    with pytest.raises(OperationError) as limited:
        read()
    assert limited.value.code == 'PM_RESULT_REQUIREMENTS_LIMIT' and limited.value.status == 413
    monkeypatch.setattr(pm_result_requirements, 'MAX_BYTES', size)
    monkeypatch.setattr(pm_result_requirements, 'LIMIT', 1)
    assert read()['coverage']['packages_partial']
