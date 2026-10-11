"""Real owned Git/verifier receipts and reasoned original dependency reviews."""
from __future__ import annotations

import copy
import json
import sys
import uuid

import pytest

from bat_agent_connector import pm_quality_reviews, pm_sprints
from bat_agent_connector import pm_requirement_acceptance as acceptance
from bat_agent_connector import pm_work_batch_evidence as evidence
from bat_agent_connector import pm_work_batches as batches
from bat_agent_connector.operations import OperationError
from bat_agent_connector.task_verifier import ObservedVerifier, VerificationSettings
from tests import test_pm_execution_verification as producer
from tests import test_pm_quality_reviews as reviews
from tests import test_pm_work_batches as batch
from tests.operation_helpers import settle_operations
from tests.test_checkpoints import bat_writes, snapshot
from tests.test_pm_execution_verification import OWNER, fixed, pm, ready, world  # noqa: F401
from tests.test_pm_work_packages import action, member

owned = producer.owned
batch_ready = batch.batch_ready


async def qualify(w, *, verdict='accepted'):
    ops = w['d'].ops
    pm_sprints.install(ops)
    pm_quality_reviews.install(ops)
    await member(w, reviews.REVIEWER.actor, 'contributor')
    w['dod'] = (await action(w, 'pm.dod.revise', {'workspace_id': w['workspace_id']},
        {'plan_revision_id': w['package']['document']['context']['plan_ref']['revision_id'],
         'criteria': [{'id': 'regression', 'text': 'Fixed original verification passes'}], 'rationale': 'Original complete DoD'},
        {'expected_revision_id': None}))['revision_id']
    w['candidate'], w['tree'] = w['config']['candidate_commit'], w['config']['tree_hash']
    proof = await producer.execute(w)
    assert proof['result']['status'] == 'passed', proof
    w['proof'] = proof['result']['reference']
    env = reviews.selection(w)
    for row in env['params']['criteria']:
        row['condition'] = {'kind': 'fixed_revision_verification', 'profile': 'observed_runner_v1', 'command_digest': w['config']['command_digest']}
    created = await reviews.execute(w, env)
    review = pm_quality_reviews.get_review(ops, OWNER, w['workspace_id'], created['result']['review_id'])['review']
    await reviews.execute(w, reviews.assessment(w, review, verdict=verdict), actor=reviews.REVIEWER)
    current = reviews.read(w, review)
    ref = {key: current['assessment'][key] for key in ('operation_id', 'version', 'document_digest')}
    ref.update(review_id=current['review_id'], candidate_commit=w['candidate'])
    selected = acceptance.freeze_condition(w['package'], [row['id'] for row in w['package']['document']['requirements']])
    return current, selected, ref


@pytest.fixture
async def qualified(owned):
    w = owned
    w['review'], w['condition'], w['quality_ref'] = await qualify(w)
    return w


def dependent(w):
    package = copy.deepcopy(w['package'])
    rid = w['condition']['requirements'][0]['requirement_id']
    package['document']['requirements'] = [{'id': 'downstream', 'depends_on': [rid]}]
    refs = {rid: {'condition': w['condition'], 'reference': w['quality_ref'], 'execution_operation_id': w['child']['operation_id']}}
    return package, refs


async def test_real_verification_and_complete_responsible_review_prove_only_fixed_original_requirements(qualified):
    w, ops = qualified, qualified['d'].ops
    before = snapshot(w['human'])
    proof = evidence.evaluate(ops, OWNER, w['workspace_id'], w['child'], w['condition'], w['quality_ref'])
    assert proof['status'] == 'passed' and proof['semantic_acceptance'] == 'accepted'
    assert proof['receipt_digest'] and proof['reference'] == w['quality_ref']
    package, refs = dependent(w)
    assert acceptance.require_dependencies(ops, OWNER, package, refs) == list(refs)
    assert acceptance.access_dependencies(ops, OWNER, package, refs) is None
    assert acceptance.require_dependencies(ops, OWNER, package, None) == []
    assert snapshot(w['human']) == before and w['marker'].read_text() == 'x'
    # A quality review cannot recursively treat semantic acceptance as a verifier.
    with pytest.raises(OperationError):
        evidence.condition(w['condition'])


async def test_ci_without_responsible_acceptance_cannot_prove_requirements(owned):
    w, ops = owned, owned['d'].ops
    w['review'], w['condition'], w['quality_ref'] = await qualify(w, verdict='unassessed')
    assert not w['review']['qualified']
    proof = evidence.evaluate(ops, OWNER, w['workspace_id'], w['child'], w['condition'], w['quality_ref'])
    assert proof['status'] == 'blocked' and proof['semantic_acceptance'] == 'unassessed'


@pytest.mark.parametrize('change', ['revoked', 'new_assessment', 'reviewer_removed'])
async def test_later_changes_block_new_admission_without_rewriting_original_history(qualified, change):
    w, ops = qualified, qualified['d'].ops
    before = evidence.evaluate(ops, OWNER, w['workspace_id'], w['child'], w['condition'], w['quality_ref'])
    if change == 'revoked':
        await reviews.execute(w, {'action': pm_quality_reviews.REVOKE,
            'target': {'workspace_id': w['workspace_id'], 'review_id': w['review']['review_id']},
            'params': {'reason': 'Original result no longer approved'}, 'preconditions': {'expected_version': w['review']['version']},
            'idempotency_key': uuid.uuid4().hex})
    elif change == 'new_assessment':
        await reviews.execute(w, reviews.assessment(w, w['review']), actor=reviews.REVIEWER)
    else:
        ops.db.execute('DELETE FROM pm_workspace_members WHERE workspace_id=? AND actor=?', (w['workspace_id'], reviews.REVIEWER.actor))
    current = evidence.evaluate(ops, OWNER, w['workspace_id'], w['child'], w['condition'], w['quality_ref'])
    assert current['status'] == 'blocked' and current['receipt_digest'] == before['receipt_digest']
    package, refs = dependent(w)
    assert acceptance.require_dependencies(ops, OWNER, package, refs) == []
    assert acceptance.access_dependencies(ops, OWNER, package, refs) is None
    evidence.require_sources(ops, OWNER, w['workspace_id'], before)


@pytest.mark.parametrize('change', ['trim_criteria', 'plan_digest', 'scope', 'other_child', 'candidate', 'unselected_requirement'])
async def test_identity_substitution_is_refused(qualified, change):
    w, ops = qualified, qualified['d'].ops
    selected, reference, child = copy.deepcopy(w['condition']), copy.deepcopy(w['quality_ref']), copy.deepcopy(w['child'])
    if change == 'trim_criteria':
        selected['requirements'][0]['acceptance_input_ids'] = ['pmi_' + 'e' * 32]
    elif change == 'plan_digest':
        selected['plan_ref']['document_digest'] = 'e' * 64
    elif change == 'scope':
        selected['plan_ref']['scope_revision'] = 'pmscope_' + 'e' * 32
    elif change == 'other_child':
        child['operation_id'] = 'op_' + 'e' * 32
    elif change == 'candidate':
        reference['candidate_commit'] = 'e' * 40
    else:
        selected['requirements'][0]['requirement_id'] = 'invented'
    with pytest.raises(OperationError):
        acceptance.evaluate(ops, OWNER, w['workspace_id'], child, selected, reference)


async def test_actual_source_withdrawal_hides_original_dependency_proof(qualified):
    w, ops = qualified, qualified['d'].ops
    package, refs = dependent(w)
    producer.revoke(w)
    for check in (acceptance.require_dependencies, acceptance.access_dependencies):
        with pytest.raises(OperationError):
            check(ops, OWNER, package, refs)


async def configure_batch_child(w, child):
    w['child'] = child
    w['d'].adapter.verifier = ObservedVerifier(VerificationSettings(commands={child['params']['project_id']: (sys.executable, '-c', 'print("fixed original proof")')},
        artifact_dir=str(w['human'].parent / 'batch-verification-logs')))
    producer.verifier.install(w['d'].ops)
    w['config'] = await producer.verifier.configuration(w['d'].ops, OWNER, w['workspace_id'], child['operation_id'])


async def test_semantic_batch_waits_for_exact_qualified_result_and_preserves_independent_progress(batch_ready, mock):
    w, ops = batch_ready, batch_ready['d'].ops
    downstream, independent = await batch.extra(w, 'Semantic dependent'), await batch.extra(w, 'Independent result')
    selected = acceptance.freeze_condition(w['package'], [row['id'] for row in w['package']['document']['requirements']])
    first = await batch.selected(w, 'first')
    first['result_condition'] = selected
    entries = [first, await batch.selected(downstream, 'second', [{'entry_key': 'first', 'condition': selected}]), await batch.selected(independent, 'independent')]
    parent = await batch.advance(w, ops.create(OWNER, **batch.envelope(w, entries))[0])
    states = {row['key']: row for row in batch.view(w, parent)['entries']}
    assert states['second']['operation_id'] is None and states['independent']['operation_id']
    assert mock.channels().count('claude:start-session') == 2
    await configure_batch_child(w, ops.get(states['first']['operation_id']))
    review, condition, ref = await qualify(w)
    assert review['qualified'] and condition == selected
    proof = ops.create(OWNER, **batch.proof_request(w, parent, ref, condition=selected))[0]
    await settle_operations(ops)
    assert ops.get(proof['operation_id'])['status'] == 'succeeded'
    parent = await batch.advance(w, parent)
    rows = {row['key']: row for row in batch.view(w, parent)['entries']}
    assert rows['first']['state'] == 'requirements_accepted'
    assert rows['second']['operation_id'] and mock.channels().count('claude:start-session') == 3
    stored = ops.db.execute('SELECT request FROM pm_work_batch_children WHERE operation_id=?', (rows['second']['operation_id'],)).fetchone()[0]
    assert ref['operation_id'] in stored and '_acceptance' in stored
    assert len(bat_writes(mock)) > 0


async def test_reference_limit_counts_exact_whole_public_wire_spacing(qualified, monkeypatch):
    w, ops = qualified, qualified['d'].ops
    package, refs = dependent(w)
    size = len(json.dumps(refs, ensure_ascii=False).encode())
    assert len(json.dumps(refs, ensure_ascii=False, separators=(',', ':')).encode()) < size
    monkeypatch.setattr(acceptance, 'MAX_REFERENCE_BYTES', size)
    assert acceptance.require_dependencies(ops, OWNER, package, refs) == list(refs)
    monkeypatch.setattr(acceptance, 'MAX_REFERENCE_BYTES', size - 1)
    for check in (acceptance.require_dependencies, acceptance.access_dependencies):
        with pytest.raises(OperationError) as exc:
            check(ops, OWNER, package, refs)
        assert exc.value.code == 'PM_REQUIREMENT_ACCEPTANCE_LIMIT' and exc.value.status == 413


@pytest.mark.parametrize('value', [{'x': '\ud800'}, {'x': float('nan')}, {'x': object()}])
def test_invalid_reference_json_fails_before_source_lookup(value):
    package = {'document': {'requirements': [{'depends_on': ['x']}]}}
    with pytest.raises(OperationError) as exc:
        acceptance.require_dependencies(None, None, package, value)
    assert exc.value.code == 'PM_REQUIREMENT_ACCEPTANCE_INVALID'


async def test_later_explicit_assessment_cannot_replace_childs_frozen_prerequisite(batch_ready, mock):
    w, ops = batch_ready, batch_ready['d'].ops
    downstream = await batch.extra(w, 'Original semantic dependent')
    condition = acceptance.freeze_condition(w['package'], [row['id'] for row in w['package']['document']['requirements']])
    first = await batch.selected(w, 'first')
    first['result_condition'] = condition
    parent = ops.create(OWNER, **batch.envelope(w, [first, await batch.selected(downstream, 'second', [{'entry_key': 'first', 'condition': condition}])]))[0]
    await ops._execute(parent['operation_id'])
    first_id = batch.view(w, parent)['entries'][0]['operation_id']
    await ops._execute(first_id)
    await configure_batch_child(w, ops.get(first_id))
    review, _, original_ref = await qualify(w)
    proof = ops.create(OWNER, **batch.proof_request(w, parent, original_ref, condition=condition))[0]
    await ops._execute(proof['operation_id'])
    assert ops.get(proof['operation_id'])['status'] == 'succeeded'
    ops.db.execute('UPDATE operations SET next_run_at=0 WHERE operation_id=?', (parent['operation_id'],))
    await ops._execute(parent['operation_id'])
    child_id = batch.view(w, parent)['entries'][1]['operation_id']
    child = ops.get(child_id)
    assert child['status'] == 'accepted' and batches.require_child(ops, child)
    before = list(bat_writes(mock))
    # Append another reasoned complete assessment, then explicitly record it in
    # the parent. The already reserved child must keep its original assessment.
    changed = ops.create(reviews.REVIEWER, **reviews.assessment(w, review))[0]
    await ops._execute(changed['operation_id'])
    current = reviews.read(w, review)
    later_ref = {key: current['assessment'][key] for key in ('operation_id', 'version', 'document_digest')}
    later_ref.update(review_id=review['review_id'], candidate_commit=w['candidate'])
    later = ops.create(OWNER, **batch.proof_request(w, parent, later_ref, condition=condition, version=2))[0]
    await ops._execute(later['operation_id'])
    assert ops.get(later['operation_id'])['status'] == 'succeeded'
    with pytest.raises(OperationError, match='PM_BATCH_DEPENDENCY_EVIDENCE_UNAVAILABLE'):
        batches.require_child(ops, ops.get(child_id))
    await ops._execute(child_id)
    failed = ops.get(child_id)
    assert failed['status'] == 'failed' and failed['error_code'] == 'PM_BATCH_DEPENDENCY_EVIDENCE_UNAVAILABLE', failed
    assert bat_writes(mock) == before
    stored = json.loads(ops.db.execute('SELECT request FROM pm_work_batch_children WHERE operation_id=?', (child_id,)).fetchone()[0])
    assert stored['_acceptance']['first']['reference'] == original_ref
