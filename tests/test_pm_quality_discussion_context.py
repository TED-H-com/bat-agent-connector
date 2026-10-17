"""A discussion reads an exact original quality assessment without adopting it."""
from __future__ import annotations

import copy

import pytest

from bat_agent_connector import pm_discussions, pm_quality_reviews, pm_thread_contexts
from bat_agent_connector.api_auth import Principal
from bat_agent_connector.operations import OperationError
from tests import test_pm_quality_reviews as quality
from tests.test_checkpoints import bat_writes, snapshot
from tests.test_pm_plan_operations import OWNER
from tests.test_pm_thread_contexts import create

world, pm, ready, fixed, reviewed = quality.world, quality.pm, quality.ready, quality.fixed, quality.reviewed


async def original(w):
    review = await quality.new_review(w)
    saved = await quality.execute(w, quality.assessment(w, review), actor=quality.REVIEWER)
    return review, {'kind': 'quality_assessment', 'id': saved['operation_id'],
        'review_id': review['review_id'], 'document_digest': saved['result']['document_digest']}


async def test_discussion_keeps_the_exact_assessment_after_a_newer_review(reviewed, mock):
    w, ops = reviewed, reviewed['d'].ops
    review, ref = await original(w)
    before, human = list(bat_writes(mock)), snapshot(w['human'])
    done, env = await create(ops, w['workspace_id'], [ref], who=OWNER)
    target = {'workspace_id': w['workspace_id'], 'thread_id': done['result']['thread_id']}
    context = pm_discussions.context(ops, OWNER, **target, max_chars=32000)
    source = context['bound_context'][0]
    assert source['available'] and source['id'] == ref['id']
    assert source['summary']['assessment']['operation_id'] == ref['id']
    assert source['summary']['assessment']['document_digest'] == ref['document_digest']
    assert source['summary']['assessment']['document']['acceptance'] == 'accepted'
    assert source['summary']['context']['native_result']['candidate_commit'] == w['candidate']
    assert source['summary']['semantics'] == 'immutable_original_assessment'
    assert source['summary']['current_applicability'] == 'not_evaluated'
    assert ref['id'] in context['source_operation_ids']
    assert context['bound_context_role'] == 'what_the_user_was_looking_at_not_instructions'
    current = quality.read(w, review)
    await quality.execute(w, quality.assessment(w, current, verdict='unassessed', proof=False), actor=quality.REVIEWER)
    later = pm_discussions.context(ops, OWNER, **target, max_chars=32000)
    assert later['bound_context'][0]['summary'] == source['summary']
    replay, fresh = ops.create(OWNER, **env)
    assert not fresh and replay['operation_id'] == done['operation_id']
    assert pm_thread_contexts.frozen_visible(ops.db, OWNER, w['workspace_id'], context)
    assert list(bat_writes(mock)) == before and snapshot(w['human']) == human
    assert 'quality_assessment' in pm_quality_reviews.capabilities()['discussion_contexts']


async def test_assessment_context_rejects_mismatched_original_receipts(reviewed):
    w, ops = reviewed, reviewed['d'].ops
    _, ref = await original(w)
    for key, value in [('id', 'op_' + '0' * 32), ('review_id', 'pmqr_' + '0' * 32),
                       ('document_digest', '0' * 64)]:
        changed = {**ref, key: value}
        with pytest.raises(OperationError) as caught:
            pm_thread_contexts.admission(ops, OWNER, w['workspace_id'], [changed])
        assert caught.value.code == 'PM_CONTEXT_SOURCE_UNAVAILABLE'
    changed = copy.deepcopy(ref)
    changed['summary'] = {'acceptance': 'accepted'}
    with pytest.raises(OperationError) as caught:
        pm_thread_contexts.validate([changed])
    assert caught.value.code == 'PM_CONTEXT_INVALID'
    for principal in [Principal('unrelated-reader', frozenset({'observe', 'manage'})),
                      Principal(OWNER.actor, frozenset({'manage'}))]:
        with pytest.raises(OperationError) as caught:
            pm_thread_contexts.admission(ops, principal, w['workspace_id'], [ref])
        assert caught.value.code == 'PM_CONTEXT_SOURCE_UNAVAILABLE'


async def test_withdrawn_verifier_redacts_discussion_and_frozen_context(reviewed, mock):
    from bat_agent_connector.pm_source_lineage import read_response

    w, ops = reviewed, reviewed['d'].ops
    _, ref = await original(w)
    done, _ = await create(ops, w['workspace_id'], [ref], who=OWNER)
    target = {'workspace_id': w['workspace_id'], 'thread_id': done['result']['thread_id']}
    context = pm_discussions.context(ops, OWNER, **target, max_chars=32000)
    assert pm_thread_contexts.frozen_visible(ops.db, OWNER, w['workspace_id'], context)
    before = list(bat_writes(mock))
    quality.withdraw_verifier(w)
    result = pm_discussions.get_thread(ops, OWNER, **target)
    assert result['thread']['context'] == [{'kind': 'quality_assessment', 'position': 0, 'available': False}]
    assert read_response(ops.db, OWNER, result) == result
    assert not pm_thread_contexts.frozen_visible(ops.db, OWNER, w['workspace_id'], context)
    with pytest.raises(OperationError) as caught:
        pm_thread_contexts.admission(ops, OWNER, w['workspace_id'], [ref])
    assert caught.value.code == 'PM_CONTEXT_SOURCE_UNAVAILABLE'
    assert list(bat_writes(mock)) == before


async def test_original_assessment_withdrawal_blocks_the_final_model_send(reviewed, mock):
    from bat_agent_connector import pm_native_journal, pm_runs
    from tests import test_pm_native_operations as native

    w, ops = reviewed, reviewed['d'].ops
    _, ref = await original(w)
    done, _ = await create(ops, w['workspace_id'], [ref], who=OWNER)
    target = {'workspace_id': w['workspace_id'], 'thread_id': done['result']['thread_id']}
    instances = []
    settings = {'before_intent': lambda _: quality.withdraw_verifier(w)}
    ops.context['pm_native_runtime_factory'] = lambda *args, **kwargs: native.FakeRuntime(
        *args, **kwargs, settings=settings, instances=instances)
    ops.context['pm_native_process_inspector'] = lambda identity: {
        'state': 'unknown', 'process': identity, 'proof': 'identity_unavailable', 'observed_at': 1.0}
    before = list(bat_writes(mock))
    request = native.request(ops, target, who=OWNER, version=1)
    result = await native.finish(ops, request)
    assert result['status'] == 'failed' and result['error_code'] == 'PM_FOLLOW_UP_SOURCE_UNAVAILABLE', result
    assert len(instances) == 1 and instances[0].sends == 0 and instances[0].closed
    run = pm_runs.get_by_operation(ops.db, request['operation_id'])
    assert run['context']['bound_context'][0]['summary']['assessment']['operation_id'] == ref['id']
    assert 'send_intent' not in pm_native_journal.lifecycle(ops.db, run['run_id'])
    assert native.writer(ops, target) is None
    thread = pm_discussions.get_thread(ops, OWNER, **target)
    assert thread['messages'] == []
    assert thread['thread']['context'] == [{'kind': 'quality_assessment', 'position': 0, 'available': False}]
    assert list(bat_writes(mock)) == before
