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


def original_assessment(w, review, saved):
    return quality.get_assessment(w['d'].ops, OWNER, w['workspace_id'], review['review_id'],
        **{key: saved[key] for key in ('operation_id', 'version', 'document_digest')})


def contradict_verifier(w):
    ops = w['d'].ops
    row = dict(ops.db.execute('SELECT * FROM observed_verifications WHERE verification_id=?', (w['proof']['verification_id'],)).fetchone())
    fields = ('candidate_commit', 'tree_hash', 'command', 'log_ref', 'output_sha256')
    return ops.journal.record_observed_verification(row['task_id'],
        {**{key: row[key] for key in fields}, 'exit_code': 1, 'source': 'observed_runner'})


def withdraw_verifier(w):
    ops = w['d'].ops
    task = ops.db.execute('SELECT task_id FROM observed_verifications WHERE verification_id=?', (w['proof']['verification_id'],)).fetchone()[0]
    ops.db.execute("UPDATE tasks SET controller_authority=json_set(controller_authority,'$.pm_workspace_id',?) WHERE task_id=?", ('ws_' + 'f' * 32, task))


async def test_new_contradiction_changes_current_qualification_without_rewriting_original_receipt(reviewed):
    w = reviewed
    review = await new_review(w)
    await execute(w, assessment(w, review), actor=REVIEWER)
    saved = read(w, review)['assessment']
    frozen = original_assessment(w, review, saved)
    assert frozen['assessment']['document']['qualified']
    contradict_verifier(w)
    current = read(w, review)
    assert current['original_outcome']['qualified'] and not current['qualified']
    assert current['applicability_reasons'] == ['original_verification_no_longer_proven']
    assert original_assessment(w, review, saved) == frozen


async def test_original_assessment_is_exact_and_survives_later_rejection_revocation_and_policy(reviewed):
    w = reviewed
    review = await new_review(w)
    await execute(w, assessment(w, review), actor=REVIEWER)
    saved = read(w, review)['assessment']
    frozen = original_assessment(w, review, saved)
    await execute(w, assessment(w, review, verdict='rejected', version=2), actor=REVIEWER)
    assert not read(w, review)['qualified']
    await execute(w, {'action': quality.REVOKE, 'target': {'workspace_id': w['workspace_id'], 'review_id': review['review_id']},
        'params': {'reason': 'Retain this historical conclusion; close current review'},
        'preconditions': {'expected_version': 3}, 'idempotency_key': uuid.uuid4().hex})
    newer = await new_review(w, previous=review['review_id'])
    assert not read(w, newer)['qualified'] and original_assessment(w, review, saved) == frozen
    for field, value in [('operation_id', 'op_' + 'f' * 32), ('version', 3), ('document_digest', 'f' * 64)]:
        with pytest.raises(OperationError) as missing:
            original_assessment(w, review, {**saved, field: value})
        assert missing.value.status == 404
    withdraw_verifier(w)
    with pytest.raises(OperationError) as hidden:
        original_assessment(w, review, saved)
    assert hidden.value.status == 404
    with pytest.raises(OperationError) as hidden_successor:
        read(w, newer)
    assert hidden_successor.value.status == 404


@pytest.mark.parametrize('change', ['contradiction', 'withdrawal'])
async def test_changed_original_proof_between_admission_and_record_never_commits_success(reviewed, change):
    w, ops = reviewed, reviewed['d'].ops
    review = await new_review(w)
    op = ops.create(REVIEWER, **assessment(w, review))[0]
    if change == 'contradiction':
        contradict_verifier(w)
    else:
        withdraw_verifier(w)
    await settle_operations(ops)
    done = ops.get(op['operation_id'])
    assert done['status'] == 'failed'
    assert done['error_code'] == ('PM_QUALITY_EVIDENCE_CHANGED' if change == 'contradiction' else 'NOT_FOUND')
    assert not ops.db.execute('SELECT 1 FROM pm_quality_review_records').fetchone()
    assert read(w, review)['version'] == 1
    if change == 'withdrawal':
        assert not quality.operation_visible(ops.db, REVIEWER, done)


@pytest.mark.parametrize('change', ['binding', 'scope'])
async def test_current_work_does_not_inherit_original_qualification_after_context_changes(reviewed, change):
    from tests.test_pm_plan_operations import adopt, draft
    from tests.test_pm_work_packages import associate
    w, ops = reviewed, reviewed['d'].ops
    review = await new_review(w)
    await execute(w, assessment(w, review), actor=REVIEWER)
    original = read(w, review)
    if change == 'binding':
        await associate(w)
    else:
        plan = await draft(w, requirements=w['requirements'], markdown='# New adopted scope\nThe original scope remains historical.\n')
        assert (await adopt(w, plan))['status'] == 'succeeded'
    current = quality.for_work(ops, OWNER, w['workspace_id'], w['target']['work_item_id'])['reviews'][0]
    assert not current['qualified'] and current['original_outcome']['qualified']
    assert ('work_binding_changed' if change == 'binding' else 'scope_revision_changed') in current['applicability_reasons']
    assert read(w, review) == original


@pytest.mark.parametrize('state', ['pause', 'close'])
async def test_original_quality_records_preserve_results_after_workspace_stop(reviewed, mock, state):
    from tests.test_pm_lifecycle import decide
    w = reviewed
    await decide(w['d'].ops, w['workspace_id'], state, who=OWNER)
    before = len(bat_writes(mock))
    review = await new_review(w)
    await execute(w, assessment(w, review), actor=REVIEWER)
    assert read(w, review)['qualified'] and len(bat_writes(mock)) == before


@pytest.mark.parametrize('work', [False, True])
async def test_whole_utf8_quality_page_reserves_actual_int64_cursor_and_final_null(reviewed, monkeypatch, work):
    w, ops = reviewed, reviewed['d'].ops
    ops.db.execute("INSERT INTO sqlite_sequence(name,seq) VALUES('pm_quality_reviews',?)", (2**63 - 2,))
    env = selection(w)
    env['params']['rationale'] = '原始驗收與責任的完整證據' * 30
    await execute(w, env)
    fetch = (lambda: quality.for_work(ops, OWNER, w['workspace_id'], w['target']['work_item_id'])) if work else (
        lambda: quality.list_reviews(ops, OWNER, w['workspace_id']))
    original = fetch()
    assert original['reviews'][0]['sequence'] == 2**63 - 1 and original['next_cursor'] is None
    monkeypatch.setattr(quality, 'MAX_BYTES', quality._size(original))
    assert fetch() == original
    monkeypatch.setattr(quality, 'MAX_BYTES', quality._size(original) - 1)
    with pytest.raises(OperationError) as oversized:
        fetch()
    assert oversized.value.status == 413


async def test_quality_work_page_includes_projection_fields_before_prefix_budget(reviewed, monkeypatch):
    w, ops = reviewed, reviewed['d'].ops
    first = await new_review(w)
    await new_review(w, previous=first['review_id'])
    original = quality.for_work(ops, OWNER, w['workspace_id'], w['target']['work_item_id'])
    budget = quality._size(original) - 1
    monkeypatch.setattr(quality, 'MAX_BYTES', budget)
    prefix = quality.for_work(ops, OWNER, w['workspace_id'], w['target']['work_item_id'])
    assert len(prefix['reviews']) == 1 and prefix['next_cursor'] == original['reviews'][0]['sequence']
    assert quality._size(prefix) <= budget
    suffix = quality.for_work(ops, OWNER, w['workspace_id'], w['target']['work_item_id'], after=prefix['next_cursor'])
    assert len(suffix['reviews']) == 1 and suffix['next_cursor'] is None and quality._size(suffix) <= budget


async def test_publication_metadata_budget_failure_rolls_back_original_record_and_version(reviewed, monkeypatch):
    w, ops = reviewed, reviewed['d'].ops
    review = await new_review(w)
    original = ops.create(REVIEWER, **assessment(w, review))[0]
    saved = original['external_refs']['admission_binding']['quality_record']['document']
    budget = quality._size({'review_id': review['review_id'], 'context': review['context'], 'assessment': saved})
    monkeypatch.setattr(quality, 'MAX_BYTES', budget)
    await settle_operations(ops)
    done = ops.get(original['operation_id'])
    assert done['status'] == 'failed' and done['error_code'] == 'PM_QUALITY_LIMIT'
    assert not ops.db.execute('SELECT 1 FROM pm_quality_review_records').fetchone()
    assert ops.db.execute('SELECT version FROM pm_quality_reviews').fetchone()[0] == 1


@pytest.mark.parametrize('reviewer', [None, True, {}, [], 'x' * 201])
async def test_invalid_reviewer_types_never_reach_sql_or_create_an_operation(reviewed, reviewer):
    with pytest.raises(OperationError) as invalid:
        reviewed['d'].ops.create(OWNER, **selection(reviewed, reviewer=reviewer))
    assert invalid.value.code == 'PM_QUALITY_INVALID'


async def test_full_assessment_history_keeps_one_reserved_terminal_revocation(reviewed):
    w, ops = reviewed, reviewed['d'].ops
    review = await new_review(w)
    for version in range(1, quality.MAX_HISTORY + 1):
        await execute(w, assessment(w, review, version=version), actor=REVIEWER)
    saved = read(w, review)
    assert saved['qualified'] and len(saved['history']) == quality.MAX_HISTORY
    with pytest.raises(OperationError) as full:
        ops.create(REVIEWER, **assessment(w, review, version=quality.MAX_HISTORY + 1))
    assert full.value.code == 'PM_QUALITY_LIMIT'
    await execute(w, {'action': quality.REVOKE, 'target': {'workspace_id': w['workspace_id'], 'review_id': review['review_id']},
        'params': {'reason': 'Explicit withdrawal is available even at the original history ceiling'},
        'preconditions': {'expected_version': quality.MAX_HISTORY + 1}, 'idempotency_key': uuid.uuid4().hex})
    final = read(w, review)
    assert not final['qualified'] and final['original_outcome']['qualified']
    assert len(final['history']) == quality.MAX_HISTORY + 1
    assert original_assessment(w, review, saved['assessment'])['assessment']['document']['qualified']


async def test_revocation_projection_retains_the_original_responsible_reason_and_receipt(reviewed):
    w, ops = reviewed, reviewed['d'].ops
    review = await new_review(w)
    await execute(w, assessment(w, review), actor=REVIEWER)
    assert read(w, review)['revocation'] is None
    reason = '原始登入驗證出現反例；保留原結論並撤回本次驗收。'
    envelope = {'action': quality.REVOKE, 'target': {'workspace_id': w['workspace_id'], 'review_id': review['review_id']},
        'params': {'reason': reason}, 'preconditions': {'expected_version': 2}, 'idempotency_key': uuid.uuid4().hex}
    done = await execute(w, envelope)
    current = read(w, review)
    revoked = current['revocation']
    assert revoked['document'] == {'reason': reason, 'qualified': False}
    assert revoked['operation_id'] == done['operation_id'] and revoked['actor'] == OWNER.actor
    assert current['assessment']['document']['qualified'] and not current['qualified']
    assert quality.for_work(ops, OWNER, w['workspace_id'], w['target']['work_item_id'])['reviews'][0]['revocation'] == revoked
    replay, fresh = ops.create(OWNER, **envelope)
    assert not fresh and replay['operation_id'] == done['operation_id']
    assert read(w, review) == current


@pytest.mark.parametrize('field', ['policy', 'criterion', 'assessment', 'revoke'])
async def test_unpaired_unicode_is_refused_before_original_quality_admission(reviewed, field):
    w, ops = reviewed, reviewed['d'].ops
    if field in {'policy', 'criterion'}:
        envelope, who = selection(w), OWNER
        if field == 'policy':
            envelope['params']['rationale'] = '\ud800'
        else:
            envelope['params']['criteria'][0]['criterion']['input_id'] = '\ud800'
    else:
        review = await new_review(w)
        envelope, who = assessment(w, review), REVIEWER
        if field == 'assessment':
            envelope['params']['conclusions'][0]['rationale'] = '\ud800'
        else:
            envelope.update(action=quality.REVOKE, params={'reason': '\ud800'})
    with pytest.raises(OperationError) as invalid:
        ops.create(who, **envelope)
    assert invalid.value.code == 'PM_QUALITY_INVALID' and invalid.value.status == 422
