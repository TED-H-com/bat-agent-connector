"""Registered public observation transport and private observer discoverability."""
import uuid

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from bat_agent_connector import mcp_server, pm_workspace
from bat_agent_connector import pm_execution_runtime as managed
from bat_agent_connector import pm_maintenance_repair_runtime as repair
from bat_agent_connector import pm_session_quiescence as manual
from tests import pm_joined_lifecycle_helpers as story
from tests.operation_helpers import settle_operations
from tests.test_api_v1 import http, token
from tests.test_checkpoints import MANUAL, bat_writes
from tests.test_pm_joined_lifecycle import bat_world, daemon, joined, world  # noqa: F401
from tests.test_pm_run_api import unpack


async def test_catalog_never_advertises_private_runtime_observers(world, monkeypatch):  # noqa: F811
    d, port = world['d'], world['port']
    for auth in (token(d, 'observer-catalog', 'observe'), d._admin_token):
        monkeypatch.setenv('BATC_TASK_URL', f'http://127.0.0.1:{port}/rpc')
        monkeypatch.setenv('BATC_API_TOKEN', auth)
        server, fleet = mcp_server.build_server(d.fleet.config, read_only=True, principal_only=True)
        try:
            status, caps = await http(port, 'GET', '/api/v1/capabilities', tok=auth)
            assert status == 200
            assert unpack(await server.call_tool('capabilities_get', {}))['actions'] == caps['actions']
            advertised = {row['action']: row for row in caps['actions']}
            for action in (managed.ACTION, repair.ACTION):
                assert advertised[action]['scope'] == 'observe'
                assert advertised[action]['allowed'] is False
            # This scope hint does not promise workspace writer membership.
            assert advertised[manual.ACTION]['allowed'] is True
        finally:
            await fleet.close()


async def test_fixed_manual_source_http_mcp_receipt_and_reader_recovery(joined, mock, monkeypatch):  # noqa: F811
    w, d = joined, joined['d']
    await story.engineering(w, 'member.login')
    story.complete_native_turn(w, mock, MANUAL)
    await d.inventory.refresh_host('h1')
    auth = token(d, story.OWNER.actor, 'observe')
    monkeypatch.setenv('BATC_TASK_URL', f"http://127.0.0.1:{w['port']}/rpc")
    monkeypatch.setenv('BATC_API_TOKEN', auth)
    server, fleet = mcp_server.build_server(d.fleet.config, principal_only=True)
    try:
        link = d.ops.db.execute("SELECT * FROM work_item_links WHERE kind='session' AND ref=? AND removed_at IS NULL", ('h1/'+MANUAL,)).fetchone()
        target = {'workspace_id': w['workspace_id'], 'work_item_id': link['work_item_id']}
        path = f"/api/v1/pm/workspaces/{w['workspace_id']}/kanban/{link['work_item_id']}"
        status, card = await http(w['port'], 'GET', path, tok=auth)
        assert status == 200, card
        source, = card['card']['execution']['manual_observation_sources']
        assert source['available'] is True and source['reason'] is None
        assert unpack(await server.call_tool('pm_kanban_card_get', target))['card']['execution']['manual_observation_sources'] == [source]
        envelope = {'action': manual.ACTION, 'target': target, 'params': {'link_id': source['link_id']},
            'preconditions': {key: source[key] for key in ('expected_link_operation_id',
                'expected_project_binding_id', 'expected_session_identity_digest')}}
        key = uuid.uuid4().hex
        original_writes = bat_writes(mock)
        status, admitted = await http(w['port'], 'POST', '/api/v1/operations?wait=0', tok=auth,
            body=envelope, headers={'Idempotency-Key': key})
        assert status == 202 and admitted['created'] is True, admitted
        operation_id = admitted['operation']['operation_id']
        await settle_operations(d.ops)
        status, read = await http(w['port'], 'GET', '/api/v1/operations/'+operation_id, tok=auth)
        assert status == 200 and read['operation']['status'] == 'succeeded', read
        original = read['operation']['result']
        assert original['runtime_status'] == 'current_single_input_quiescent'
        assert original['process_death_proven'] is False and original['ownership'] == 'manual_read_only'
        assert original['work_acceptance'] == 'not_evaluated'
        replay = unpack(await server.call_tool('operation_submit', {**envelope, 'idempotency_key': key, 'wait_s': 0, 'confirm': True}))
        assert replay['operation']['operation_id'] == operation_id and replay['operation']['result'] == original
        # A separate explicit MCP observation is admitted through the same real
        # action; it must never replace the earlier receipt selected below.
        second = unpack(await server.call_tool('operation_submit', {**envelope, 'idempotency_key': uuid.uuid4().hex,
            'wait_s': 0, 'confirm': True}))
        assert second['created'] is True and second['operation']['operation_id'] != operation_id
        await settle_operations(d.ops)
        status, second_read = await http(w['port'], 'GET', '/api/v1/operations/'+second['operation']['operation_id'], tok=auth)
        assert status == 200 and second_read['operation']['status'] == 'succeeded', second_read
        assert second_read['operation']['result']['observation_operation_id'] == second['operation']['operation_id']
        assert bat_writes(mock) == original_writes

        # Use the real registered membership actions to preserve governance.
        for actor, role in [('transport-other-owner', 'owner'), (story.OWNER.actor, 'reader')]:
            version = pm_workspace.require_member(d.ops.db, story.OWNER, w['workspace_id'])['version']
            await story.execute(w, 'pm.workspace.member.set', {'workspace_id': w['workspace_id']},
                {'actor': actor, 'role': role}, {'expected_version': version})
        reads_before = len(mock.invokes)
        status, recovered = await http(w['port'], 'GET', '/api/v1/operations/'+operation_id, tok=auth)
        assert status == 200 and recovered['operation']['result'] == original
        assert unpack(await server.call_tool('operation_get', {'operation_id': operation_id}))['operation']['result'] == original
        replay = unpack(await server.call_tool('operation_submit', {**envelope, 'idempotency_key': key, 'wait_s': 0, 'confirm': True}))
        assert replay['operation']['operation_id'] == operation_id and replay['operation']['result'] == original
        count = d.ops.db.execute('SELECT COUNT(*) FROM operations').fetchone()[0]
        status, refused = await http(w['port'], 'POST', '/api/v1/operations?wait=0', tok=auth,
            body=envelope, headers={'Idempotency-Key': uuid.uuid4().hex})
        assert status == 403 and refused['error']['admission_refused'] is True
        with pytest.raises(ToolError, match='FORBIDDEN'):
            await server.call_tool('operation_submit', {**envelope, 'idempotency_key': uuid.uuid4().hex, 'wait_s': 0, 'confirm': True})
        assert d.ops.db.execute('SELECT COUNT(*) FROM operations').fetchone()[0] == count
        assert len(mock.invokes) == reads_before

        # A reader's original operation and replay both lose access with the source.
        await story.execute(w, 'work_item.link', {'work_item_id': link['work_item_id']},
            {'kind': 'session', 'ref': 'h1/'+MANUAL, 'remove': True})
        assert (await http(w['port'], 'GET', '/api/v1/operations/'+operation_id, tok=auth))[0] == 404
        with pytest.raises(ToolError, match='NOT_FOUND'):
            await server.call_tool('operation_get', {'operation_id': operation_id})
        status, hidden = await http(w['port'], 'POST', '/api/v1/operations?wait=0', tok=auth,
            body=envelope, headers={'Idempotency-Key': key})
        assert status == 404 and 'admission_refused' not in hidden['error']
        assert bat_writes(mock) == original_writes
        story.assert_manual_preserved(w, mock)
    finally:
        await fleet.close()
