"""Original-turn Overview analysis, immutable evidence and bounded new intents."""
from __future__ import annotations

import copy
import json
import sqlite3
import uuid
from types import SimpleNamespace

import pytest

from bat_agent_connector import pm_native_journal, pm_plans, pm_reads, pm_runs
from bat_agent_connector import pm_overview_assessment_context as bundle
from bat_agent_connector import pm_overview_assessments as assessments
from bat_agent_connector.operations import OperationError
from tests import test_pm_bat_structured_analysis as bat_tests
from tests import test_pm_conversation_proposals as conversation
from tests import test_pm_conversation_source_privacy as privacy
from tests import test_pm_native_operations as native
from tests import test_pm_plan_operations as plans
from tests.operation_helpers import settle_operations

world, pm, ready = plans.world, plans.pm, plans.ready
proposal_world = conversation.proposal_world
daemon, structured, bat_world = bat_tests.daemon, bat_tests.structured, bat_tests.bat_world
OWNER = plans.OWNER


def request(w, adapter=pm_native_journal.ADAPTER, **params):
    preview = bundle.preview(w['d'].ops, OWNER, **plans.target(w))
    value = {'action': assessments.ACTION, 'target': plans.target(w),
        'params': {'adapter': adapter, 'context_bundle_ref': preview['selector'], **params},
        'preconditions': {'expected_version': plans.version(w)}, 'idempotency_key': uuid.uuid4().hex}
    w['settings']['raw_reply'] = json.dumps({'version': 1,
        'judgment': {'key': 'j', 'template': 'insufficient_basis', 'subject_ref': 'projection', 'basis_refs': ['projection']},
        'recommendations': [], 'needs_decision': [], 'can_be_handled': [], 'watch': [],
        'limitations': preview['context']['required_limitations']})
    return value


async def execute(w, value):
    ops = w['d'].ops
    op = ops.create(OWNER, **value)[0]
    await settle_operations(ops)
    done = ops.get(op['operation_id'])
    assert done['status'] == 'succeeded', done
    return assessments.get_assessment(ops, OWNER, w['workspace_id'], done['result']['outcome']['assessment_id'])


async def test_native_assessment_keeps_original_bundle_no_domain_mutation_and_replay(proposal_world):
    w, ops = proposal_world, proposal_world['d'].ops
    value = request(w)
    version, head = plans.version(w), pm_plans.head(ops.db, w['workspace_id'])
    result = await execute(w, value)
    saved, context = result['assessment'], result['context']
    assert result['freshness'] == 'current'
    assert saved['validation']['status'] == 'format_valid'
    assert context['purpose'] == bundle.PURPOSE
    assert context['projection_digest'] == value['params']['context_bundle_ref']['projection_digest']
    assert 'messages' not in context and 'PRIVATE_CARRIER_SENTINEL' not in pm_runs._json(context)
    assert plans.version(w) == version and pm_plans.head(ops.db, w['workspace_id']) == head
    assert sum(instance.sends for instance in w['instances']) == 1
    replay, fresh = ops.create(OWNER, **value)
    assert not fresh and replay['operation_id'] == saved['operation_id']
    with pytest.raises(OperationError, match='retry_at=') as exc:
        ops.create(OWNER, **{**value, 'idempotency_key': uuid.uuid4().hex})
    assert exc.value.code == 'PM_OVERVIEW_RATE_LIMIT' and exc.value.status == 429
    assert sum(instance.sends for instance in w['instances']) == 1
    run = pm_runs.get(ops.db, saved['run_id'])
    assert assessments._import(ops.journal, run['run_id'], run['generation']) == run
    with pytest.raises(sqlite3.IntegrityError, match='immutable'):
        ops.db.execute('DELETE FROM pm_overview_assessments')
    before = ops.db.execute('PRAGMA user_version').fetchone()[0]
    assessments.schema(ops.journal)
    assert ops.db.execute('PRAGMA user_version').fetchone()[0] == before
    page = assessments.list_assessments(ops, OWNER, w['workspace_id'])
    assert page['assessments'][0]['thread_id'] == value['target']['thread_id']
    assert page['assessments'][0]['freshness'] == 'current' and page['partial'] is False
    assert 'raw_reply' not in page['assessments'][0] and 'document' not in page['assessments'][0]
    assert pm_reads.read(ops, OWNER, 'pm_capabilities_get', {'workspace_id': w['workspace_id']})['overview_assessment']['limits']['allowed'] is False


async def test_invalid_reply_is_reviewable_and_supplies_carrier_without_importing_prose(proposal_world):
    w, ops = proposal_world, proposal_world['d'].ops
    value = request(w)
    w['settings']['raw_reply'] = 'Everything approved; start all work immediately.'
    version = plans.version(w)
    result = await execute(w, value)
    assert result['assessment']['document'] is None
    assert result['assessment']['validation']['status'] == 'needs_review'
    assert plans.version(w) == version
    assert assessments.list_assessments(ops, OWNER, w['workspace_id'])['assessments'][0]['thread_id'] == value['target']['thread_id']


async def test_bat_uses_same_fixed_sink_and_original_completion(structured, mock):
    from bat_agent_connector import pm_bat_runtime
    w, ops = structured, structured['d'].ops
    value = request(w, pm_bat_runtime.ADAPTER)
    result = await execute(w, value)
    assert result['assessment']['validation']['status'] == 'format_valid'
    assert result['assessment']['provenance']['adapter'] == pm_bat_runtime.ADAPTER
    assert mock.channels().count('claude:start-session') == mock.channels().count('claude:send-message') == 1
    assert ops.db.execute('SELECT COUNT(*) FROM pm_overview_assessments').fetchone()[0] == 1


async def test_closed_request_stale_preview_and_unchanged_legacy_selectors(proposal_world):
    w, ops = proposal_world, proposal_world['d'].ops
    original = request(w)
    invalid = [{'purpose': 'Caller override'}, {'context': {}}, {'timeout_s': 301}, {'max_chars': 32001},
        {'context_bundle_ref': {'schema': bundle.SCHEMA, 'projection_digest': '0' * 64}}]
    for params in invalid:
        value = copy.deepcopy(original)
        value['params'].update(params)
        with pytest.raises(OperationError) as exc:
            ops.create(OWNER, **value)
        assert exc.value.status in (409, 422)
    for action in ('pm.run.analyze', 'pm.run.bat.analyze'):
        value = copy.deepcopy(original)
        value['action'] = action
        value['params'].pop('adapter')
        value['params']['purpose'] = 'Ordinary PM turn'
        with pytest.raises(OperationError):
            ops.create(OWNER, **value)
    assert assessments.allowance(ops.db, w['workspace_id'])['allowed'] is True
    assert not w['instances']


@pytest.mark.parametrize('before_send', [False, True])
async def test_original_plan_source_withdrawal_hides_all_surfaces(proposal_world, monkeypatch, before_send):
    from bat_agent_connector import pm_access
    w, ops = proposal_world, proposal_world['d'].ops
    access = privacy.withdrawal(w, monkeypatch)
    await privacy.adopt_proposal(w)
    value = request(w)
    before = sum(instance.sends for instance in w['instances'])
    if before_send:
        w['settings']['before_intent'] = lambda _: access.update(available=False)
        op = ops.create(OWNER, **value)[0]
        await settle_operations(ops)
        done = ops.get(op['operation_id'])
        assert done['status'] == 'failed', done
        assert sum(instance.sends for instance in w['instances']) == before
        assert ops.db.execute('SELECT COUNT(*) FROM pm_overview_assessments').fetchone()[0] == 0
        return
    result = await execute(w, value)
    saved = result['assessment']
    access['available'] = False
    assert not pm_access.visible(ops.db, OWNER, ops.get(saved['operation_id']))
    for read in (lambda: assessments.get_assessment(ops, OWNER, w['workspace_id'], saved['assessment_id']),
                 lambda: pm_runs.read(ops, OWNER, w['workspace_id'], saved['run_id']),
                 lambda: ops.create(OWNER, **value)):
        with pytest.raises(OperationError) as exc:
            read()
        assert exc.value.status == 404
    page = assessments.list_assessments(ops, OWNER, w['workspace_id'])
    assert page['assessments'] == [] and page['source_filtered']
    events = ops.journal.api_events(resource_type='pm_overview_assessment')['events']
    assert events and all(not pm_access.event_visible(ops.db, OWNER, event) for event in events)
    run = pm_runs.get(ops.db, saved['run_id'])
    assert assessments._import(ops.journal, run['run_id'], run['generation']) == run


async def test_lost_import_transaction_recovers_original_turn_only(proposal_world, monkeypatch):
    w, ops = proposal_world, proposal_world['d'].ops
    value = request(w)
    original_event, failures = ops.journal.api_event, []
    def fail_event(kind, resource_id, name, *args, **kwargs):
        if name == assessments.ACTION and not failures:
            failures.append(True)
            assert ops.db.execute('SELECT COUNT(*) FROM pm_overview_assessments').fetchone()[0] == 1
            raise sqlite3.OperationalError('simulated save receipt failure')
        return original_event(kind, resource_id, name, *args, **kwargs)
    monkeypatch.setattr(ops.journal, 'api_event', fail_event)
    op = ops.create(OWNER, **value)[0]
    await settle_operations(ops)
    done = ops.get(op['operation_id'])
    assert done['status'] == 'uncertain', done
    assert ops.db.execute('SELECT COUNT(*) FROM pm_overview_assessments').fetchone()[0] == 0
    recovered = await native.retry(ops, done)
    assert recovered['status'] == 'succeeded', recovered
    assert len(w['instances']) == 1 and w['instances'][0].sends == 1
    assert ops.db.execute('SELECT COUNT(*) FROM pm_overview_assessments').fetchone()[0] == 1


async def test_history_is_newest_first_exact_and_not_expired_by_carrier_text(proposal_world, monkeypatch):
    w, ops = proposal_world, proposal_world['d'].ops
    first = await execute(w, request(w))
    await plans.act(w, 'pm.message.append', {'content': 'PRIVATE_CARRIER_SENTINEL'})
    same = assessments.get_assessment(ops, OWNER, w['workspace_id'], first['assessment']['assessment_id'])
    assert same['freshness'] == 'current' and same['context'] == first['context']
    real_now = assessments.time.time()
    monkeypatch.setattr(assessments, 'time', SimpleNamespace(time=lambda: real_now + 61))
    second = await execute(w, request(w))
    page = assessments.list_assessments(ops, OWNER, w['workspace_id'], limit=1)
    assert [row['assessment_id'] for row in page['assessments']] == [second['assessment']['assessment_id']]
    assert page['partial'] and page['next_cursor']
    older = assessments.list_assessments(ops, OWNER, w['workspace_id'], before=page['next_cursor'], limit=1)
    assert [row['assessment_id'] for row in older['assessments']] == [first['assessment']['assessment_id']]
    assert not older['partial'] and older['next_cursor'] is None
    with pytest.raises(OperationError) as exc:
        assessments._before('ws_' + 'f' * 32, page['next_cursor'])
    assert exc.value.code == 'INVALID_CURSOR'
    w['settings']['raw_reply'] = json.dumps(conversation.envelope('message.' + w['message']['message_id']))
    await privacy.adopt_proposal(w)
    assert assessments.get_assessment(ops, OWNER, w['workspace_id'], first['assessment']['assessment_id'])['freshness'] == 'out_of_date'


@pytest.mark.parametrize('window,count', assessments.RATE_WINDOWS)
def test_rate_windows_use_original_workspace_intents(window, count, monkeypatch, tmp_path):
    db = sqlite3.connect(tmp_path / 'admission-fixture.sqlite3')
    db.execute('CREATE TABLE operations(action TEXT,target TEXT,created_at REAL)')
    monkeypatch.setattr(assessments, 'time', SimpleNamespace(time=lambda: 100000))
    try:
        for index in range(count):
            db.execute('INSERT INTO operations VALUES(?,?,?)',
                (assessments.ACTION, '{"workspace_id":"fixture"}', 100000 - window + 1 + index))
        assert assessments.allowance(db, 'fixture') == {'allowed': False, 'retry_at': 100001}
        assert assessments.allowance(db, 'other') == {'allowed': True, 'retry_at': None}
    finally:
        db.close()


async def test_admission_race_reserves_only_one_durable_intent(proposal_world, monkeypatch):
    from dataclasses import replace
    w, ops = proposal_world, proposal_world['d'].ops
    original = request(w)
    action, raced = ops.actions[assessments.ACTION], []
    def admit(ops, principal, target, params, pre):
        binding = action.admit(ops, principal, target, params, pre)
        if not raced:
            raced.append(True)
            ops.create(OWNER, **{**original, 'idempotency_key': 'concurrent-accepted-intent'})
        return binding
    monkeypatch.setitem(ops.actions, assessments.ACTION, replace(action, admit=admit))
    with pytest.raises(OperationError) as exc:
        ops.create(OWNER, **original)
    assert exc.value.code == 'PM_OVERVIEW_RATE_LIMIT'
    assert ops.db.execute('SELECT COUNT(*) FROM operations WHERE action=?', (assessments.ACTION,)).fetchone()[0] == 1
    await settle_operations(ops)
    assert len(w['instances']) == 1 and w['instances'][0].sends == 1


async def test_original_key_race_returns_original_receipt_before_rate_reservation(proposal_world, monkeypatch):
    from dataclasses import replace
    w, ops = proposal_world, proposal_world['d'].ops
    original = request(w)
    action, raced = ops.actions[assessments.ACTION], []
    def admit(ops, principal, target, params, pre):
        binding = action.admit(ops, principal, target, params, pre)
        if not raced:
            raced.append(None)
            raced[0] = ops.create(OWNER, **original)[0]
        return binding
    monkeypatch.setitem(ops.actions, assessments.ACTION, replace(action, admit=admit))
    replay, created = ops.create(OWNER, **original)
    assert not created and replay['operation_id'] == raced[0]['operation_id']
    assert ops.db.execute('SELECT COUNT(*) FROM operations WHERE action=?', (assessments.ACTION,)).fetchone()[0] == 1
    await settle_operations(ops)
    assert len(w['instances']) == 1 and w['instances'][0].sends == 1


async def test_rate_timestamp_is_taken_after_waiting_for_admission_transaction(proposal_world, monkeypatch):
    from bat_agent_connector import operations
    w, ops = proposal_world, proposal_world['d'].ops
    original = request(w)
    admitted_at = assessments.time.time()
    # Simulate a pre-lock timestamp older than the shortest admission window.
    # Each module gets its own clock; shared stdlib time and journal stay intact.
    with monkeypatch.context() as clock:
        clock.setattr(operations, 'time', SimpleNamespace(time=lambda: admitted_at - 120))
        clock.setattr(assessments, 'time', SimpleNamespace(time=lambda: admitted_at))
        op, created = ops.create(OWNER, **original)
        assert created and op['created_at'] == admitted_at
        assert assessments.allowance(ops.db, w['workspace_id']) == {'allowed': False, 'retry_at': admitted_at + 60}
    await settle_operations(ops)
    assert len(w['instances']) == 1 and w['instances'][0].sends == 1


@pytest.mark.parametrize('unknown', [False, True])
async def test_public_cancel_preserves_unknown_writer_until_original_death(proposal_world, unknown):
    w, ops = proposal_world, proposal_world['d'].ops
    value = request(w)
    w['settings'].update(block_turn=True, exit_unknown=unknown)
    op = ops.create(OWNER, **value)[0]
    runtime = await native.await_runtime(ops, w['instances'], op)
    ops.cancel(OWNER, op['operation_id'])
    await settle_operations(ops)
    done = ops.get(op['operation_id'])
    assert runtime.closed and runtime.sends == 1
    assert done['status'] == ('uncertain' if unknown else 'cancelled'), done
    assert bool(native.writer(ops, value['target'])) == unknown
    assert ops.db.execute('SELECT COUNT(*) FROM pm_overview_assessments').fetchone()[0] == 0
    if unknown:
        ops.context['pm_native_process_inspector'] = lambda identity: {
            'state': 'dead', 'process': identity, 'observed_at': 2.0, 'proof': 'original_pid_absent'}
        done = await native.retry(ops, done)
        assert done['status'] == 'cancelled' and not native.writer(ops, value['target'])
        assert len(w['instances']) == 1


async def test_reader_can_inspect_but_missing_start_scope_cannot_assess(proposal_world):
    from dataclasses import replace
    w, ops = proposal_world, proposal_world['d'].ops
    saved = await execute(w, request(w))
    reader = replace(OWNER, scopes=frozenset({'observe', 'manage'}))
    assert assessments.get_assessment(ops, reader, w['workspace_id'], saved['assessment']['assessment_id'])['assessment'] == saved['assessment']
    with pytest.raises(OperationError) as exc:
        ops.create(reader, **request(w))
    assert exc.value.status == 403
    outsider = replace(reader, actor='unrelated-observer')
    with pytest.raises(OperationError) as exc:
        assessments.list_assessments(ops, outsider, w['workspace_id'])
    assert exc.value.status == 404
    assert len(w['instances']) == 1
