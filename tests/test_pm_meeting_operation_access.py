"""Shared operation feeds retain meeting sources' current visibility."""
import copy

import pytest

from bat_agent_connector import controller_authority, mcp_server, pm_access, pm_meetings, pm_sprints
from bat_agent_connector.operations import OpContext, OperationError
from tests.test_api_v1 import daemon as daemon  # noqa: F401
from tests.test_api_v1 import http, token
from tests.test_api_v1 import served as served  # noqa: F401
from tests.test_mcp_principal import call
from tests.test_pm_meeting_read_budget import restricted_meeting
from tests.test_pm_meetings import OWNER, READER, apply, complete, draft, meeting, note, read, request, settle
from tests.test_pm_meetings import ops as ops  # noqa: F401
from tests.test_pm_meetings import sprint_ops as sprint_ops  # noqa: F401


async def sourced_note(ops):
    data, sprint, public_snap, _ = await meeting(ops)
    target, source = await restricted_meeting(ops, data, sprint)
    intent = request(ops, target, 'note', content='PRIVATE SOURCE QUOTE')
    pending, _ = ops.create(OWNER, **intent)
    result = await settle(ops, pending)
    return data, sprint, public_snap, target, source, intent, result


def revoke(ops, source):
    ops.db.execute("UPDATE tasks SET controller_authority='{}' WHERE task_id=?", (source['task_id'],))


def denied(ops, operation):
    with pytest.raises(OperationError) as error:
        ops.get(operation['operation_id'], principal=READER)
    assert error.value.status == 404
    assert operation['operation_id'] not in str(ops.list(action=operation['action'], principal=READER))


async def test_get_list_operation_and_domain_events_hide_original_source_after_revocation(ops):
    _, _, _, target, source, intent, operation = await sourced_note(ops)
    for key in ('target', 'params', 'preconditions'):
        assert operation[key] == intent[key]
    assert operation['external_refs']['admission_binding']['meeting_context_revision_id']
    assert ops.get(operation['operation_id'], principal=READER)['params']['content'] == 'PRIVATE SOURCE QUOTE'
    events = [event for event in ops.journal.api_events(0, 500)['events']
              if event['resource_id'] == operation['operation_id'] or event['body'].get('operation_id') == operation['operation_id']]
    assert events and all(pm_access.event_visible(ops.db, READER, event) for event in events)
    revoke(ops, source)
    with pytest.raises(OperationError, match='SOURCE_UNAVAILABLE'):
        read(ops, target, READER)
    denied(ops, operation)
    assert not any(pm_access.event_visible(ops.db, READER, event) for event in events)
    with pytest.raises(OperationError, match='NOT_FOUND'):
        ops.create(OWNER, **intent)
    # Internal durable fact recovery neither exposes public data nor reruns a note.
    before = ops.db.total_changes
    assert await pm_meetings._run(OpContext(ops, operation)) == operation['result']
    assert ops.db.total_changes == before


async def test_new_public_context_does_not_reauthorize_old_operation_quotes(ops):
    _, sprint, public_snap, target, source, _, operation = await sourced_note(ops)
    refreshed = request(ops, target, 'context.revise', snapshot_id=public_snap['snapshot_id'])
    refreshed['preconditions']['expected_sprint_version'] = pm_sprints._sprint(ops.db, sprint['workspace_id'], sprint['sprint_id'])['version']
    pending, _ = ops.create(OWNER, **refreshed)
    revision = await settle(ops, pending)
    await draft(ops, target)
    new_draft_id = read(ops, target)['draft']['operation_id']
    revoke(ops, source)
    assert pm_meetings._access(ops, READER, pm_meetings._meeting(ops.db, target['workspace_id'], target['meeting_id']))[0]['document']['snapshot_ref']['snapshot_id'] == public_snap['snapshot_id']
    denied(ops, operation)
    denied(ops, revision)  # refresh rationale could quote its original private context
    assert ops.get(new_draft_id, principal=READER)['action'] == 'pm.meeting.draft'


async def test_legacy_effect_derives_original_context_without_admission_metadata(ops):
    _, _, _, _, source, _, operation = await sourced_note(ops)
    refs = copy.deepcopy(operation['external_refs'])
    refs['admission_binding'].pop('meeting_context_revision_id')
    ops.db.execute('UPDATE operations SET external_refs=? WHERE operation_id=?', (pm_meetings._json(refs), operation['operation_id']))
    assert ops.get(operation['operation_id'], principal=READER)['result'] == operation['result']
    revoke(ops, source)
    denied(ops, operation)


async def test_pre_effect_binding_does_not_follow_a_changed_current_head(ops):
    _, sprint, public_snap, target, source, _, _ = await sourced_note(ops)
    pending, _ = ops.create(OWNER, **request(ops, target, 'note', content='Original private source'))
    # Accepted operations preserve their admitted context independently of effects.
    assert ops.get(pending['operation_id'], principal=READER)['params']['content']
    revoke(ops, source)
    denied(ops, pending)
    result = await settle(ops, pending, success=False)
    assert result['status'] == 'failed' and result['error_code'] == 'PM_MEETING_SOURCE_UNAVAILABLE'
    assert ops.get(pending['operation_id'])['params']['content'] == 'Original private source'


async def test_http_rpc_and_mcp_shared_operation_reads_obey_same_source_filter(ops, served, monkeypatch):
    d, port = served
    with controller_authority.use(controller_authority.binding(ops.db, OWNER), ops.journal):
        _, _, _, _, source, _, operation = await sourced_note(ops)
    original_ops, original_journal = d.ops, d.journal
    d.ops, d.journal = ops, ops.journal
    fleet = None
    try:
        auth = token(d, READER.actor, 'observe')
        monkeypatch.setenv('BATC_TASK_URL', f'http://127.0.0.1:{port}/rpc')
        monkeypatch.setenv('BATC_API_TOKEN', auth)
        server, fleet = mcp_server.build_server(d.fleet.config, read_only=True, principal_only=True)
        path = '/api/v1/operations/' + operation['operation_id']
        status, payload = await http(port, 'GET', path, tok=auth)
        assert status == 200 and payload['operation']['params']['content'] == 'PRIVATE SOURCE QUOTE'
        assert 'PRIVATE SOURCE QUOTE' in await call(server, 'operation_get', {'operation_id': operation['operation_id']})
        revoke(ops, source)
        assert (await http(port, 'GET', path, tok=auth))[0] == 404
        status, listed = await http(port, 'GET', '/api/v1/operations?action=pm.meeting.note', tok=auth)
        assert status == 200 and operation['operation_id'] not in str(listed)
        assert 'NOT_FOUND' in await call(server, 'operation_get', {'operation_id': operation['operation_id']})
        assert operation['operation_id'] not in await call(server, 'operations_list', {'action': 'pm.meeting.note'})
        events = await call(server, 'events_list', {'after': 0, 'limit': 500})
        assert 'PRIVATE SOURCE QUOTE' not in events and operation['operation_id'] not in events
    finally:
        if fleet:
            await fleet.close()
        d.ops, d.journal = original_ops, original_journal


async def test_follow_up_operation_checks_its_later_snapshot_as_well_as_original_context(ops):
    data, sprint, _, target = await meeting(ops, kind='retro')
    await draft(ops, target, 'retro')
    await note(ops, target)
    await complete(ops, target)
    private_target, source = await restricted_meeting(ops, data, sprint)
    private_snapshot = read(ops, private_target)['snapshot']['snapshot_id']
    result = await apply(ops, target, 'follow_up.record', follow_up_id='check_quality', status='blocked',
                         result='PRIVATE LATER SOURCE', snapshot_id=private_snapshot)
    operation_id = ops.db.execute('SELECT operation_id FROM pm_meeting_follow_up_events WHERE event_id=?', (result['event_id'],)).fetchone()[0]
    operation = ops.get(operation_id, principal=READER)
    revoke(ops, source)
    denied(ops, operation)
    pm_meetings._access(ops, READER, pm_meetings._meeting(ops.db, target['workspace_id'], target['meeting_id']))


async def test_legacy_pending_without_original_context_is_hidden_instead_of_using_current_context(ops):
    _, _, _, target = await meeting(ops)
    intent = request(ops, target, 'note', content='Unverifiable original legacy context')
    pending, _ = ops.create(OWNER, **intent)
    refs = copy.deepcopy(pending['external_refs'])
    refs['admission_binding'].pop('meeting_context_revision_id')
    ops.db.execute('UPDATE operations SET external_refs=? WHERE operation_id=?', (pm_meetings._json(refs), pending['operation_id']))
    denied(ops, pending)
    # Internal execution can still settle normally; its immutable fact then
    # supplies the original source association for subsequent public reads.
    result = await settle(ops, pending)
    assert ops.get(pending['operation_id'], principal=READER)['result'] == result['result']
