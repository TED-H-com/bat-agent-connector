"""Whole original pagination and explicit public projections on real batch sources."""
import copy
import json

from bat_agent_connector import pm_access, pm_kanban
from bat_agent_connector import pm_execution_runtime as runtime
from tests import pm_joined_lifecycle_helpers as story
from tests.test_pm_execution_runtime import drive, finish, uncompleted  # noqa: F401
from tests.test_pm_joined_lifecycle import bat_world, daemon, joined, world  # noqa: F401


async def test_original_one_event_pages_resume_without_latest_page_substitution(joined, mock, monkeypatch):  # noqa: F811
    w = joined
    dispatch = mock.dispatch
    def one_event(channel, params):
        result = dispatch(channel, params)
        if channel == 'claude:sync-session' and params.get('sessionId') in w['coding'] and result.get('mode') == 'delta':
            events = result['events']
            if len(events) > 1:
                result = {**result, 'events': events[:1], 'hasMore': True,
                    'cursor': {**result['cursor'], 'seq': events[0]['seq']}}
        return result
    monkeypatch.setattr(mock, 'dispatch', one_event)
    with monkeypatch.context() as held:
        held.setattr(story, 'complete_native_turn', lambda *_: None)
        await story.develop_original_requirements(w, mock)
    ops = w['d'].ops
    child = ops.get(w['ledger'].references['execution_ids'][-1])
    # Read any original nonterminal pages without manufacturing native end.
    for _ in range(3):
        pending = await drive(w, child)
        assert pending['status'] == 'waiting_external', pending
        assert runtime.proof(ops, story.OWNER, child) is None
    story.complete_native_turn(w, mock, child['result']['session_id'])
    first = await drive(w, child)
    assert first['status'] == 'waiting_external', first
    assert runtime.proof(ops, story.OWNER, child) is None
    second = await drive(w, child)
    assert second['status'] == 'succeeded', second
    reader = runtime.observer(ops, child)
    count, cursor, events, _ = runtime._pages(ops, reader['operation_id'], runtime.origin(ops, child))
    assert count == len(events) == cursor['seq'] == 4
    assert [event['seq'] for event in events] == [1, 2, 3, 4]
    assert runtime.proof(ops, story.OWNER, child)['event_count'] == 4


async def test_unknown_private_receipt_fields_never_flow_through_public_proof(uncompleted, mock, monkeypatch):  # noqa: F811
    w, ops = uncompleted, uncompleted['d'].ops
    child = w['original_children'][-1]
    finish(w, mock)
    sentinel = 'PRIVATE-FUTURE-PRODUCER-DIAGNOSTIC-keep-in-original-journal'
    store = runtime._store
    def private_extra(ctx, name, document):
        if name == runtime.COMPLETE:
            document['privateDiagnostic'] = {'path': sentinel}
            document['input_state']['privateDiagnostic'] = {'environment': sentinel}
            document['completion_cursor']['privateDiagnostic'] = {'token': sentinel}
        return store(ctx, name, document)
    monkeypatch.setattr(runtime, '_store', private_extra)
    reader = await drive(w, child)
    assert reader['status'] == 'succeeded', reader
    private = runtime._saved(ops, reader['operation_id'], runtime.COMPLETE)
    assert sentinel in json.dumps(private)
    original = copy.deepcopy(private)
    proof = runtime.proof(ops, story.OWNER, child)
    assert sentinel not in json.dumps(proof)
    assert set(proof['input_state']) == {'schema', 'epoch', 'received', 'pending'}
    assert set(proof['completion_cursor']) == {'epoch', 'seq'}
    assert sentinel not in json.dumps(ops.get(reader['operation_id'], principal=story.OWNER))
    summary = runtime.summary(ops, story.OWNER, child)
    assert set(summary) == {'observation_operation_id', 'execution_operation_id', 'document_digest',
        'recorded_at', 'runtime_status', 'process_death_proven'}
    fixed = child['params']['work_item_id']
    binding = pm_kanban.pm_evidence._binding(ops.db, w['workspace_id'], fixed)
    card = pm_kanban._card(ops, story.OWNER, w['workspace_id'], binding, private['recorded_at'])
    assert sentinel not in json.dumps(card)
    assert any(attempt.get('runtime_completion') == summary for attempt in card['execution']['attempts'])
    events = [dict(row) for row in ops.db.execute("SELECT * FROM api_events WHERE resource_type='operation' AND resource_id=?", (reader['operation_id'],))]
    assert sentinel not in json.dumps(events)
    assert all(pm_access.event_visible(ops.db, story.OWNER, {**row, 'body': json.loads(row['body'])}) for row in events)
    assert runtime._saved(ops, reader['operation_id'], runtime.COMPLETE) == original
