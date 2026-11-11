"""Fixed saved conversations through the existing native/BAT PM operation loop."""
from __future__ import annotations

import copy
import json
import sqlite3
from dataclasses import replace

import pytest

from bat_agent_connector import pm_access, pm_continuation, pm_runs, session_archive
from bat_agent_connector.operations import OperationError
from tests import test_pm_continuation as continuation
from tests import test_pm_native_operations as native
from tests import test_pm_plan_operations as plans
from tests import test_session_archive as archive
from tests.operation_helpers import settle_operations
from tests.test_checkpoints import bat_writes, snapshot

world, pm, ready = plans.world, plans.pm, plans.ready
proposal_world = continuation.proposal_world
daemon, structured, bat_world = continuation.daemon, continuation.structured, continuation.bat_world
OWNER = plans.OWNER


async def save_source(w, mock, monkeypatch):
    d = w['d']
    monkeypatch.setattr(d.journal, 'owner_valid', lambda: True)
    await d.inventory.refresh_host('h1')
    mock.archives[archive.SID] = []
    mock.states[archive.SID]['messages'] = [
        archive.msg(1, 'Keep the original login goal; the decision is still open.', 'user'),
        archive.msg(2, 'The assistant claimed completion without verification.'),
    ]
    await d.session_archive.capture('h1', archive.SID)
    return d.session_archive.read(OWNER, 'h1', archive.SID)


def admitted(w, saved, **params):
    request = continuation.request(w, retained_session_ref=saved['retained_session_ref'], **params)
    op = w['d'].ops.create(OWNER, **request)[0]
    context = op['external_refs']['admission_binding']['continuation_brief']['context']
    key = next(key for key, ref in context['source_references'].items() if ref['kind'] == 'retained_session_message')
    w['settings']['raw_reply'] = json.dumps(continuation.envelope(key))
    return op, request, context


async def completed(w, op):
    await settle_operations(w['d'].ops)
    done = w['d'].ops.get(op['operation_id'])
    assert done['status'] == 'succeeded', done
    return pm_runs.read(w['d'].ops, OWNER, w['workspace_id'], done['result']['run_id'])


async def test_native_pins_source_across_capture_restart_and_key_replay(proposal_world, mock, monkeypatch):
    w, ops = proposal_world, proposal_world['d'].ops
    saved = await save_source(w, mock, monkeypatch)
    human = snapshot(w['human'])
    op, request, context = admitted(w, saved)
    mock.states[archive.SID]['messages'][0]['content'] = 'A later source revision must not replace the selected goal.'
    await w['d'].session_archive.capture('h1', archive.SID)
    assert w['d'].session_archive.read(OWNER, 'h1', archive.SID)['snapshot_id'] != saved['snapshot_id']
    first = await completed(w, op)
    value = pm_continuation.brief(first)
    assert value['validation']['status'] == 'format_valid'
    assert value['provenance']['actual_model']
    assert first['context'] == context
    assert context['retained_session']['ref'] == saved['retained_session_ref']
    assert context['coverage']['partial']
    refs = [ref for ref in context['source_references'].values() if ref['kind'] == 'retained_session_message']
    assert any('original login goal' in ref['quote'] for ref in refs)
    assert all('later source revision' not in ref['quote'] for ref in refs)
    assert all(ref['disposition'] == 'context' and 'operation_id' not in ref for ref in refs)
    assert value['document']['adopted_decisions'] == []
    assert not first['outcome']['execution_authorized']
    w['d'].session_archive = session_archive.SessionArchive(w['d'])
    assert pm_continuation.brief(pm_runs.read(ops, OWNER, w['workspace_id'], first['run_id'])) == value
    assert ops.create(OWNER, **request)[0]['operation_id'] == first['operation_id']
    assert pm_continuation._import(ops.journal, first['run_id'], first['generation'])['state'] == 'completed'
    assert sum(instance.sends for instance in w['instances']) == 1
    assert snapshot(w['human']) == human
    with pytest.raises(sqlite3.IntegrityError, match='immutable'):
        ops.db.execute("UPDATE pm_run_receipts SET data='{}' WHERE run_id=?", (first['run_id'],))


@pytest.mark.parametrize('change', ['host_removed', 'host_rebound', 'native_replaced'])
async def test_retained_source_change_before_send_refuses_model(proposal_world, mock, monkeypatch, change):
    w, ops = proposal_world, proposal_world['d'].ops
    saved = await save_source(w, mock, monkeypatch)
    op, _, _ = admitted(w, saved)
    def withdraw(_):
        if change == 'host_removed':
            w['d'].inventory.config.hosts.pop('h1')
        elif change == 'host_rebound':
            hosts = w['d'].inventory.config.hosts
            hosts['h1'] = replace(hosts['h1'], profile_id='another-profile')
        else:
            row = ops.db.execute('SELECT body FROM sessions_observed WHERE host=? AND session_id=?', ('h1', archive.SID)).fetchone()
            body = json.loads(row[0])
            body['provider_native_id'] = 'replacement-native-session'
            ops.db.execute('UPDATE sessions_observed SET body=? WHERE host=? AND session_id=?',
                (json.dumps(body), 'h1', archive.SID))
    w['settings']['before_intent'] = withdraw
    await settle_operations(ops)
    done = ops.get(op['operation_id'])
    assert done['status'] == 'failed', done
    assert sum(instance.sends for instance in w['instances']) == 0
    assert not pm_access.visible(ops.db, OWNER, done)


async def test_withdrawal_hides_saved_summary_and_derived_chain(proposal_world, mock, monkeypatch):
    w, ops = proposal_world, proposal_world['d'].ops
    saved = await save_source(w, mock, monkeypatch)
    op, _, _ = admitted(w, saved)
    first = await completed(w, op)
    value = pm_continuation.brief(first)
    w['settings']['raw_reply'] = json.dumps(continuation.envelope(value['document']['goal']['source_refs'][0]))
    second, _ = await continuation.prepare(w, previous_brief_ref={
        'run_id': first['run_id'], 'document_digest': value['document_digest']})
    assert pm_continuation.brief(second)['validation']['status'] == 'format_valid'
    ordinary = await native.finish(ops, native.request(ops, plans.target(w), who=OWNER, version=plans.version(w)))
    assert ordinary['status'] == 'succeeded', ordinary
    derived = pm_runs.get_by_operation(ops.db, ordinary['operation_id'])
    w['d'].inventory.config.hosts.pop('h1')
    for label, run in (('original', first), ('chained', second), ('ordinary', derived)):
        assert not pm_access.visible(ops.db, OWNER, ops.get(run['operation_id'])), label
        with pytest.raises(OperationError) as caught:
            pm_runs.read(ops, OWNER, w['workspace_id'], run['run_id'])
        assert caught.value.status == 404
    assert pm_continuation.latest(ops, OWNER, **plans.target(w)) == {'available': False}
    events = ops.journal.api_events(resource_type='pm_continuation')['events']
    assert events and all(not pm_access.event_visible(ops.db, OWNER, event) for event in events)
    assert sum(instance.sends for instance in w['instances']) == 3


async def test_partial_source_cannot_produce_verified_completion(proposal_world, mock, monkeypatch):
    w = proposal_world
    saved = await save_source(w, mock, monkeypatch)
    op, _, _ = admitted(w, saved)
    reply = json.loads(w['settings']['raw_reply'])
    reply.update(status='no_remaining_work', open_questions=[], completed_evidence=[copy.deepcopy(reply['goal'])])
    w['settings']['raw_reply'] = json.dumps(reply)
    run = await completed(w, op)
    value = pm_continuation.brief(run)
    assert value['document'] is None and value['validation']['status'] == 'needs_review'
    assert value['raw_reply'] == w['settings']['raw_reply']
    assert sum(instance.sends for instance in w['instances']) == 1


async def test_source_withdrawn_after_send_does_not_import_or_resend(proposal_world, mock, monkeypatch):
    w, ops = proposal_world, proposal_world['d'].ops
    saved = await save_source(w, mock, monkeypatch)
    op, _, _ = admitted(w, saved)
    w['settings']['after_intent'] = lambda _: w['d'].inventory.config.hosts.pop('h1')
    await settle_operations(ops)
    done = ops.get(op['operation_id'])
    assert done['status'] != 'succeeded', done
    run = pm_runs.get_by_operation(ops.db, op['operation_id'])
    assert not any(r['phase'] == 'import' and r['status'] == 'confirmed' for r in run['receipts'])
    assert not pm_access.visible(ops.db, OWNER, done)
    await settle_operations(ops)
    assert sum(instance.sends for instance in w['instances']) == 1


async def test_bat_adapter_uses_same_saved_context_and_original_key(structured, mock, monkeypatch):
    w, ops = structured, structured['d'].ops
    saved = await save_source(w, mock, monkeypatch)
    op, request, context = admitted(w, saved, adapter=continuation.bat_tests.runtime.ADAPTER)
    first = await completed(w, op)
    value = pm_continuation.brief(first)
    assert first['context'] == context
    assert value['validation']['status'] == 'format_valid'
    assert value['provenance']['adapter'] == continuation.bat_tests.runtime.ADAPTER
    assert value['provenance']['bat_runtime']['native_message_id'] == 'native-answer'
    assert ops.create(OWNER, **request)[0]['operation_id'] == first['operation_id']
    assert pm_continuation._import(ops.journal, first['run_id'], first['generation'])['state'] == 'completed'
    assert not bat_writes(mock, archive.SID)
