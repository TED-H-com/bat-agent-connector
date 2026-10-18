"""Real journal and owned Git; bounded mocked model turns only."""
from __future__ import annotations

import copy
import json
import sqlite3
import uuid

import pytest

from bat_agent_connector import (
    pm_access,
    pm_continuation,
    pm_discussions,
    pm_native_journal,
    pm_operation_loop,
    pm_plans,
    pm_runs,
)
from bat_agent_connector import pm_continuation_format as fmt
from bat_agent_connector.operations import OperationError
from tests import test_pm_bat_structured_analysis as bat_tests
from tests import test_pm_conversation_proposals as conversation
from tests import test_pm_conversation_source_privacy as privacy
from tests import test_pm_native_operations as native
from tests import test_pm_plan_operations as plans
from tests.operation_helpers import settle_operations

world, pm, ready = plans.world, plans.pm, plans.ready
proposal_world = conversation.proposal_world
OWNER = plans.OWNER
daemon, structured, bat_world = bat_tests.daemon, bat_tests.structured, bat_tests.bat_world


def envelope(ref):
    return {'version': 1, 'goal': {'text': '保留登入與忘記密碼目標', 'source_refs': [ref]},
        'status': 'needs_decision', 'candidates': [], 'completed_evidence': [],
        'open_questions': [{'text': '先驗證哪個使用者流程？', 'source_refs': [ref]}],
        'remaining_work': [], 'next_steps': []}


def request(w, **params):
    ops = w['d'].ops
    pm_continuation.install(ops)
    head = pm_plans.head(ops.db, w['workspace_id'])
    return {'action': pm_continuation.ACTION, 'target': plans.target(w),
        'params': {'purpose': '整理並繼續', 'max_chars': 64000, **params},
        'preconditions': {'expected_version': plans.version(w), 'expected_plan_version': head['version'],
            'expected_accepted_revision_id': head['accepted_revision_id']}, 'idempotency_key': uuid.uuid4().hex}


async def prepare(w, **params):
    ops = w['d'].ops
    value = request(w, **params)
    op = ops.create(OWNER, **value)[0]
    await settle_operations(ops)
    done = ops.get(op['operation_id'])
    assert done['status'] == 'succeeded', done
    return pm_runs.read(ops, OWNER, w['workspace_id'], done['result']['run_id']), value


async def test_incremental_brief_preserves_receipt_and_original_text(proposal_world):
    w, ops = proposal_world, proposal_world['d'].ops
    w['settings']['raw_reply'] = json.dumps(envelope('message.' + w['message']['message_id']))
    head, version = pm_plans.head(ops.db, w['workspace_id']), plans.version(w)
    first, original = await prepare(w)
    saved = pm_continuation.brief(first)
    assert saved['validation']['status'] == 'format_valid', saved
    assert saved['document']['adopted_decisions'] == []
    assert first['outcome']['accepted_plan_changed'] is False
    public = await pm_operation_loop.attach(ops, OWNER, ops.get(first['operation_id']))
    assert public['loop']['dashboard_hash'] == f"#/pm/{w['workspace_id']}/discussions/{original['target']['thread_id']}"
    assert public['loop']['completeness'] == {'status': 'complete', 'reason': None}
    assert plans.version(w) == version and pm_plans.head(ops.db, w['workspace_id']) == head
    assert ops.create(OWNER, **original)[0]['operation_id'] == first['operation_id']
    assert pm_continuation._import(ops.journal, first['run_id'], first['generation']) == pm_runs.get(ops.db, first['run_id'])
    with pytest.raises(sqlite3.IntegrityError, match='immutable'):
        ops.db.execute("UPDATE pm_run_receipts SET data='{}' WHERE run_id=?", (first['run_id'],))
    new = await plans.act(w, 'pm.message.append', {'content': '現在先確認錯誤密碼。'})
    w['settings']['raw_reply'] = json.dumps(envelope('message.' + new['message_id']))
    second, _ = await prepare(w, previous_brief_ref={'run_id': first['run_id'], 'document_digest': saved['document_digest']})
    context = second['context']
    assert context['previous_brief']['document'] == saved['document']
    assert context['coverage']['included_message_ids'] == [new['message_id']]
    assert 'quote' not in context['source_references']['message.' + w['message']['message_id']]
    assert context['coverage']['partial'] is False
    assert sum(instance.sends for instance in w['instances']) == 2
    page = pm_discussions.get_thread(ops, OWNER, **plans.target(w))
    assert page['continuation_brief']['run_id'] == second['run_id']
    assert len(page['messages']) == 2 and all(row['role'] == 'user' for row in page['messages'])
    assert not ops.db.execute('SELECT 1 FROM tasks').fetchone()
    with pytest.raises(OperationError, match='ordinary discussion history'):
        pm_native_journal.import_reply(ops.journal, second['run_id'], second['generation'])
    following = await native.finish(ops, native.request(ops, plans.target(w), who=OWNER, version=plans.version(w)))
    assert following['status'] == 'succeeded', following
    frozen = pm_runs.get_by_operation(ops.db, following['operation_id'])['context']
    assert frozen['continuation_brief']['run_id'] == second['run_id']
    assert frozen['messages'] == []
    assert frozen['continuation_role'] == 'prior_model_interpretation_not_decision_authority'
    assert second['operation_id'] in frozen['source_operation_ids']


async def test_owner_exclusion_cannot_be_promoted_by_summary(proposal_world):
    w, ops = proposal_world, proposal_world['d'].ops
    await privacy.adopt_proposal(w)
    w['settings']['raw_reply'] = json.dumps(envelope('message.' + w['message']['message_id']))
    first, _ = await prepare(w)
    doc = pm_continuation.brief(first)['document']
    assert doc['adopted_decisions'] and all('推薦碼' not in row['text'] for row in doc['adopted_decisions'])
    rejected = next(row for row in doc['rejected_directions'] if '推薦碼' in row['text'])
    bad = envelope(rejected['source_refs'][0])
    bad['adopted_decisions'] = [{'text': '採用推薦碼', 'source_refs': rejected['source_refs']}]
    assert fmt.validate(json.dumps(bad), first['context'])[0] is None
    del bad['adopted_decisions']
    w['settings']['raw_reply'] = json.dumps(bad)
    second, _ = await prepare(w)
    assert pm_continuation.brief(second)['validation']['status'] == 'needs_review'
    assert pm_continuation.brief(second)['document'] is None
    assert pm_continuation.latest(ops, OWNER, **plans.target(w))['run_id'] == second['run_id']
    assert first['context']['plan_head'] == pm_plans.head(ops.db, w['workspace_id'])


async def test_withdrawal_hides_incremental_chain_and_global_surfaces(proposal_world, monkeypatch):
    w, ops = proposal_world, proposal_world['d'].ops
    access = privacy.withdrawal(w, monkeypatch)
    w['settings']['raw_reply'] = json.dumps(envelope('message.' + w['message']['message_id']))
    first, _ = await prepare(w)
    ref = {'run_id': first['run_id'], 'document_digest': pm_continuation.brief(first)['document_digest']}
    second, _ = await prepare(w, previous_brief_ref=ref)
    ordinary = await native.finish(ops, native.request(ops, plans.target(w), who=OWNER, version=plans.version(w)))
    assert ordinary['status'] == 'succeeded', ordinary
    derived = pm_runs.get_by_operation(ops.db, ordinary['operation_id'])
    access['available'] = False
    for run in (first, second, derived):
        assert not pm_access.visible(ops.db, OWNER, ops.get(run['operation_id']))
        with pytest.raises(OperationError) as caught:
            pm_runs.read(ops, OWNER, w['workspace_id'], run['run_id'])
        assert caught.value.status == 404
    assert pm_continuation.latest(ops, OWNER, **plans.target(w)) == {'available': False}
    events = ops.journal.api_events(resource_type='pm_continuation')['events']
    assert events and all(not pm_access.event_visible(ops.db, OWNER, event) for event in events)
    listing = pm_runs.list_runs(ops, OWNER, w['workspace_id'])
    assert not {first['run_id'], second['run_id']} & {row['run_id'] for row in listing['runs']}
    assert sum(instance.sends for instance in w['instances']) == 3


@pytest.mark.parametrize('change', ['digest', 'thread', 'plan_version'])
async def test_invalid_previous_identity_or_plan_refuses_before_send(proposal_world, change):
    w, ops = proposal_world, proposal_world['d'].ops
    w['settings']['raw_reply'] = json.dumps(envelope('message.' + w['message']['message_id']))
    first, _ = await prepare(w)
    value = request(w, previous_brief_ref={'run_id': first['run_id'], 'document_digest': pm_continuation.brief(first)['document_digest']})
    if change == 'digest':
        value['params']['previous_brief_ref']['document_digest'] = '0' * 64
    elif change == 'thread':
        value['target']['thread_id'] = 'pmt_' + '0' * 32
    else:
        value['preconditions']['expected_plan_version'] += 1
    with pytest.raises(OperationError):
        ops.create(OWNER, **value)
    assert len(w['instances']) == 1


@pytest.mark.parametrize('case', ['partial', 'invented', 'no_evidence', 'too_many_steps', 'extra_authority'])
def test_invalid_semantic_envelope_is_retained_for_review(case):
    context = {'source_references': {'message.x': {'disposition': 'context'}},
        'coverage': {'partial': False}, 'decisions': {'adopted': [], 'rejected': []}}
    doc = envelope('message.x')
    if case == 'partial':
        context['coverage']['partial'] = True
        doc.update(status='no_remaining_work', open_questions=[])
    elif case == 'invented':
        doc['goal']['source_refs'] = ['unavailable']
    elif case == 'no_evidence':
        doc['completed_evidence'] = [copy.deepcopy(doc['goal'])]
    elif case == 'too_many_steps':
        doc['next_steps'] = [copy.deepcopy(doc['goal'])] * 3
    else:
        doc['execution_authorized'] = True
    value, validation = fmt.validate(json.dumps(doc), context)
    assert value is None and validation['status'] == 'needs_review'


async def test_long_discussion_has_explicit_omissions_and_never_retransmits_history(proposal_world):
    w = proposal_world
    for n in range(102):
        latest = await plans.act(w, 'pm.message.append', {'content': f'new note {n}: ' + 'bounded original evidence ' * 80})
    w['settings']['raw_reply'] = json.dumps(envelope('message.' + latest['message_id']))
    first, _ = await prepare(w)
    coverage = first['context']['coverage']
    assert coverage['partial'] and coverage['older_messages_omitted'] and coverage['omitted_message_ids']
    assert len(pm_runs._json(first['context'])) <= 64000
    assert latest['message_id'] in coverage['included_message_ids']
    saved = pm_continuation.brief(first)
    assert saved['document'] is not None
    second, _ = await prepare(w, previous_brief_ref={'run_id': first['run_id'], 'document_digest': saved['document_digest']})
    assert second['context']['coverage']['included_message_ids'] == []
    assert second['context']['coverage']['partial']
    assert len(pm_runs._json(second['context'])) < len(pm_runs._json(first['context']))


async def test_source_withdrawn_before_send_cannot_start_another_turn(proposal_world, monkeypatch):
    w, ops = proposal_world, proposal_world['d'].ops
    access = privacy.withdrawal(w, monkeypatch)
    w['settings']['raw_reply'] = json.dumps(envelope('message.' + w['message']['message_id']))
    w['settings']['before_intent'] = lambda _: access.update(available=False)
    op = ops.create(OWNER, **request(w))[0]
    await settle_operations(ops)
    done = ops.get(op['operation_id'])
    assert done['status'] == 'failed', done
    assert sum(instance.sends for instance in w['instances']) == 0
    run = pm_runs.get_by_operation(ops.db, op['operation_id'])
    assert run['state'] == 'aborted' and pm_continuation.brief(run) is None


async def test_bat_uses_original_wire_proof_and_no_ordinary_import(structured, mock):
    w, ops = structured, structured['d'].ops
    w['settings']['raw_reply'] = json.dumps(envelope('message.' + w['message']['message_id']))
    first, original = await prepare(w, adapter=bat_tests.runtime.ADAPTER)
    saved = pm_continuation.brief(first)
    assert saved['validation']['status'] == 'format_valid'
    assert saved['provenance']['adapter'] == bat_tests.runtime.ADAPTER
    assert saved['provenance']['bat_runtime']['native_message_id'] == 'native-answer'
    assert ops.create(OWNER, **original)[0]['operation_id'] == first['operation_id']
    assert pm_continuation._import(ops.journal, first['run_id'], first['generation']) == pm_runs.get(ops.db, first['run_id'])
    assert mock.channels().count('claude:start-session') == mock.channels().count('claude:send-message') == 1
    assert not ops.db.execute('SELECT 1 FROM pm_assistant_provenance WHERE run_id=?', (first['run_id'],)).fetchone()
