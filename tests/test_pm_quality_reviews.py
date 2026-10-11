"""Fixed real Git handoffs, original verifier receipts and accountable review."""
from __future__ import annotations

import copy
import sqlite3
import uuid

import pytest

from bat_agent_connector import pm_lifecycle_gates, pm_sprints
from bat_agent_connector import pm_quality_reviews as quality
from bat_agent_connector.api_auth import Principal
from bat_agent_connector.operations import OperationError
from tests.operation_helpers import settle_operations
from tests.test_checkpoints import bat_writes, git, snapshot
from tests.test_pm_package_handoff import fixed as fixed_fixture
from tests.test_pm_package_handoff import pm, ready, request, run, world  # noqa: F401
from tests.test_pm_plan_operations import OWNER
from tests.test_pm_work_batches import CONDITION, observed
from tests.test_pm_work_packages import action, member

fixed = fixed_fixture
REVIEWER = Principal('quality-reviewer', frozenset({'observe', 'manage'}))


@pytest.fixture
async def reviewed(fixed, monkeypatch):
    w, ops = fixed, fixed['d'].ops
    pm_sprints.install(ops)
    version = ops.db.execute('PRAGMA user_version').fetchone()[0]
    quality.install(ops)
    quality.install(ops)
    assert ops.db.execute('PRAGMA user_version').fetchone()[0] == version
    monkeypatch.setattr(pm_lifecycle_gates, 'RECORD_ACTIONS', pm_lifecycle_gates.RECORD_ACTIONS | set(quality.ACTIONS))
    await member(w, REVIEWER.actor, 'contributor')
    w['dod'] = (await action(w, 'pm.dod.revise', {'workspace_id': w['workspace_id']},
        {'plan_revision_id': w['package']['document']['context']['plan_ref']['revision_id'],
            'criteria': [{'id': 'regression', 'text': 'Fixed revision regression succeeds'}], 'rationale': 'Original shared quality commitment'},
        {'expected_revision_id': None}))['revision_id']
    w['child'] = await run(w, await request(w))
    assert w['child']['status'] == 'succeeded'
    wt = w['child']['result']['worktree_path']
    w['candidate'], w['tree'] = git(wt, 'rev-parse', 'HEAD'), git(wt, 'rev-parse', 'HEAD^{tree}')
    w['proof'] = observed(w, w['child'], candidate=w['candidate'], tree=w['tree'])
    return w


def selection(w, *, previous=None, reviewer=REVIEWER.actor, dod=True):
    criteria = []
    for req in w['package']['document']['requirements']:
        criteria += [{'criterion': {'kind': 'acceptance', 'requirement_id': req['id'], 'input_id': item['source_input_id']},
            'condition': CONDITION} for item in req['acceptance']]
    if dod:
        criteria.append({'criterion': {'kind': 'dod', 'criterion_id': 'regression'}, 'condition': CONDITION})
    return {'action': quality.CREATE, 'target': w['target'], 'params': {
        'package_ref': {key: w['package'][key] for key in ('workspace_id', 'package_id', 'document_digest')},
        'execution_operation_id': w['child']['operation_id'], 'candidate_commit': w['candidate'],
        'dod_revision_id': w['dod'] if dod else None, 'reviewer_actor': reviewer, 'criteria': criteria,
        'rationale': 'Review all original login acceptance and regression criteria at this exact implementation'},
        'preconditions': {'expected_previous_review_id': previous}, 'idempotency_key': uuid.uuid4().hex}


async def execute(w, env, *, actor=OWNER, success=True):
    ops = w['d'].ops
    original = ops.create(actor, **env)[0]
    await settle_operations(ops)
    done = ops.get(original['operation_id'])
    assert done['error_code'] != 'INTERNAL', done
    if success:
        assert done['status'] == 'succeeded', done
    return done


async def new_review(w, **kwargs):
    op = await execute(w, selection(w, **kwargs))
    return quality.get_review(w['d'].ops, OWNER, w['workspace_id'], op['result']['review_id'])['review']


def assessment(w, review, *, verdict='accepted', proof=True, version=None):
    return {'action': quality.RECORD, 'target': {'workspace_id': w['workspace_id'], 'review_id': review['review_id']},
        'params': {'conclusions': [{'criterion': item['criterion'], 'verdict': verdict,
            'rationale': 'The fixed login regression verifies the stated behavior and its failure case; the original criterion is covered.',
            'reference': w['proof'] if proof else None} for item in review['context']['criteria']],
            'rationale': 'Compared every original criterion with the fixed verification result'},
        'preconditions': {'expected_version': version or review['version']}, 'idempotency_key': uuid.uuid4().hex}


def read(w, review):
    return quality.get_review(w['d'].ops, OWNER, w['workspace_id'], review['review_id'])['review']


async def test_original_verification_and_responsible_review_qualify_separate_acceptance_and_dod(reviewed, mock):
    w = reviewed
    before, foreign = len(bat_writes(mock)), snapshot(w['human'])
    original = selection(w)
    create = await execute(w, original)
    review = quality.get_review(w['d'].ops, OWNER, w['workspace_id'], create['result']['review_id'])['review']
    assert review['qualified'] is False and review['acceptance'] == 'unassessed'
    assert not review['context']['self_review']
    env = assessment(w, review)
    done = await execute(w, env, actor=REVIEWER)
    current = read(w, review)
    assert current['acceptance'] == current['dod'] == 'accepted' and current['qualified']
    assert current['assessment']['actor'] == REVIEWER.actor
    assert current['assessment']['document']['verification_is_semantic_acceptance'] is False
    assert current['context']['plan_ref'] == w['package']['document']['context']['plan_ref']
    for who, request_env, oid in ((OWNER, original, create['operation_id']), (REVIEWER, env, done['operation_id'])):
        replay, fresh = w['d'].ops.create(who, **request_env)
        assert not fresh and replay['operation_id'] == oid
    assert len(bat_writes(mock)) == before and snapshot(w['human']) == foreign
    with pytest.raises(sqlite3.IntegrityError, match='immutable'):
        w['d'].ops.db.execute('UPDATE pm_quality_review_records SET actor=?', ('invented',))
    with pytest.raises(sqlite3.IntegrityError, match='immutable'):
        w['d'].ops.db.execute('DELETE FROM pm_quality_reviews')


@pytest.mark.parametrize('case', ['ci_only', 'review_only', 'failed', 'wrong_candidate', 'dod_rejected', 'no_dod'])
async def test_incomplete_quality_is_never_qualified(reviewed, case):
    w = reviewed
    review = await new_review(w, dod=case != 'no_dod')
    env = assessment(w, review, verdict='unassessed' if case == 'ci_only' else 'accepted', proof=case != 'review_only')
    if case in {'failed', 'wrong_candidate'}:
        ref = observed(w, w['child'], candidate='f' * 40 if case == 'wrong_candidate' else w['candidate'], tree=w['tree'],
            exit_code=1 if case == 'failed' else 0)
        for value in env['params']['conclusions']:
            value['reference'] = ref
    if case == 'dod_rejected':
        env['params']['conclusions'][-1]['verdict'] = 'rejected'
    await execute(w, env, actor=REVIEWER)
    value = read(w, review)
    assert not value['qualified']
    if case == 'dod_rejected':
        assert value['acceptance'] == 'accepted' and value['dod'] == 'rejected'
    if case == 'no_dod':
        assert value['acceptance'] == 'accepted' and value['dod'] == 'not_defined'


@pytest.mark.parametrize('change', ['missing', 'duplicate', 'unknown', 'text', 'weak_condition'])
async def test_owner_policy_requires_exact_complete_original_criteria(reviewed, change):
    w = reviewed
    env = selection(w)
    rows = env['params']['criteria']
    if change == 'missing':
        rows.pop()
    elif change == 'duplicate':
        rows[-1] = copy.deepcopy(rows[0])
    elif change == 'unknown':
        rows[0]['criterion']['input_id'] = 'pmi_' + 'f' * 32
    elif change == 'text':
        rows[0]['text'] = 'Replace original acceptance'
    else:
        rows[0]['condition'] = {'kind': 'checkbox', 'passed': True}
    with pytest.raises(OperationError):
        w['d'].ops.create(OWNER, **env)
    assert not w['d'].ops.db.execute('SELECT 1 FROM pm_quality_reviews').fetchone()


async def test_original_reviewer_and_final_transaction_authority_are_required(reviewed):
    w, ops = reviewed, reviewed['d'].ops
    with pytest.raises(OperationError) as owner_required:
        ops.create(REVIEWER, **selection(w))
    assert owner_required.value.code == 'FORBIDDEN'
    review = await new_review(w)
    with pytest.raises(OperationError) as wrong:
        ops.create(OWNER, **assessment(w, review))
    assert wrong.value.code == 'FORBIDDEN'
    op = ops.create(REVIEWER, **assessment(w, review))[0]
    ops.db.execute('DELETE FROM pm_workspace_members WHERE workspace_id=? AND actor=?', (w['workspace_id'], REVIEWER.actor))
    await settle_operations(ops)
    assert ops.get(op['operation_id'])['status'] == 'failed'
    assert not ops.db.execute('SELECT 1 FROM pm_quality_review_records').fetchone()
    assert 'review_responsibility_unavailable' in read(w, review)['applicability_reasons']


async def test_record_and_policy_cas_preserve_original_history_and_replay(reviewed):
    w, ops = reviewed, reviewed['d'].ops
    one, two = selection(w), selection(w)
    op1, op2 = ops.create(OWNER, **one)[0], ops.create(OWNER, **two)[0]
    await ops._execute(op1['operation_id'])
    await ops._execute(op2['operation_id'])
    assert ops.get(op1['operation_id'])['status'] == 'succeeded'
    assert ops.get(op2['operation_id'])['error_code'] == 'PM_QUALITY_VERSION_CONFLICT'
    review = quality.get_review(ops, OWNER, w['workspace_id'], 'pmqr_' + op1['operation_id'][3:])['review']
    first, second = assessment(w, review), assessment(w, review, verdict='rejected')
    a, b = ops.create(REVIEWER, **first)[0], ops.create(REVIEWER, **second)[0]
    await ops._execute(a['operation_id'])
    await ops._execute(b['operation_id'])
    assert ops.get(a['operation_id'])['status'] == 'succeeded'
    assert ops.get(b['operation_id'])['error_code'] == 'PM_QUALITY_VERSION_CONFLICT'
    newer = await new_review(w, previous=review['review_id'])
    old = read(w, review)
    assert old['original_outcome']['qualified'] and not old['qualified']
    assert old['applicability_reasons'] == ['review_superseded']
    assert newer['context']['previous_review_id'] == review['review_id'] and not newer['qualified']


async def test_revocation_keeps_original_conclusions_and_source_withdrawal_hides_whole_history(reviewed):
    w, ops = reviewed, reviewed['d'].ops
    review = await new_review(w)
    accepted = await execute(w, assessment(w, review), actor=REVIEWER)
    revocation = {'action': quality.REVOKE, 'target': {'workspace_id': w['workspace_id'], 'review_id': review['review_id']},
        'params': {'reason': 'A newly discovered counterexample needs a new responsible review'},
        'preconditions': {'expected_version': 2}, 'idempotency_key': uuid.uuid4().hex}
    await execute(w, revocation)
    revoked = read(w, review)
    assert not revoked['qualified'] and revoked['original_outcome']['qualified']
    assert revoked['applicability_reasons'] == ['review_revoked']
    with pytest.raises(OperationError, match='revoked'):
        ops.create(REVIEWER, **assessment(w, review, version=3))
    task = ops.db.execute('SELECT task_id FROM observed_verifications WHERE verification_id=?', (w['proof']['verification_id'],)).fetchone()[0]
    ops.db.execute("UPDATE tasks SET controller_authority=json_set(controller_authority,'$.pm_workspace_id',?) WHERE task_id=?", ('ws_' + 'f' * 32, task))
    with pytest.raises(OperationError) as hidden:
        read(w, review)
    assert hidden.value.status == 404
    assert not quality.operation_visible(ops.db, OWNER, accepted)
    history = quality.list_reviews(ops, OWNER, w['workspace_id'])
    assert history['reviews'] == [] and history['visibility_filtered']


async def test_projection_requires_exact_original_scope_binding_dod_and_candidate(reviewed):
    w, ops = reviewed, reviewed['d'].ops
    review = await new_review(w)
    await execute(w, assessment(w, review), actor=REVIEWER)
    expected = {'binding_id': review['context']['work_ref']['binding_id'], 'scope_revision': review['context']['plan_ref']['scope_revision'],
        'dod_revision_id': w['dod'], 'candidate_commit': w['candidate']}
    assert quality.evaluate_review(ops, OWNER, w['workspace_id'], review['review_id'], **expected)['review']['qualified']
    for field in expected:
        changed = {**expected, field: 'different_original'}
        result = quality.evaluate_review(ops, OWNER, w['workspace_id'], review['review_id'], **changed)['review']
        assert not result['qualified'] and result['original_outcome']['qualified']
        assert field + '_changed' in result['applicability_reasons']
    visible = quality.for_work(ops, OWNER, w['workspace_id'], w['target']['work_item_id'])
    assert len(visible['reviews']) == 1 and visible['reviews'][0]['qualified']
    assert visible['aggregation'] == 'explicit_original_review_selection_required'
