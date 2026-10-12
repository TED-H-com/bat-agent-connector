"""Continue the same ledger after transport loss and a real central reopen."""
from __future__ import annotations

import asyncio
import copy
import json

from bat_agent_connector import pm_bat_runtime as bat_runtime
from bat_agent_connector import pm_conversation_proposals as proposals
from bat_agent_connector import pm_plans, pm_runs, pm_snapshots, pm_sprints, registry
from bat_agent_connector.config import parse_config
from bat_agent_connector.errors import InvokeTimeout
from bat_agent_connector.task_daemon import TaskDaemon
from tests import test_pm_conversation_proposals as conversation
from tests.operation_helpers import settle_operations
from tests.pm_joined_lifecycle_helpers import OWNER, assert_manual_preserved
from tests.test_api_v1 import http, token
from tests.test_pm_plan_operations import act as thread_action


async def reconnect_and_replan(w, mock, monkeypatch):
    old, wid = w['d'], w['workspace_id']
    ops = old.ops
    original_plan = copy.deepcopy(pm_plans.accepted_plan(ops, OWNER, wid)['plan'])
    original_runs = [pm_runs.get_by_operation(ops.db, row[0]) for row in ops.db.execute(
        'SELECT operation_id FROM pm_runs WHERE workspace_id=?', (wid,))]
    original_bindings = [dict(row) for row in ops.db.execute('SELECT * FROM pm_work_bindings WHERE workspace_id=?', (wid,))]
    original_packages = [dict(row) for row in ops.db.execute('SELECT * FROM pm_work_packages WHERE workspace_id=?', (wid,))]
    before_registry = {run['session_binding']['session_id']: copy.deepcopy(registry.get('h1', run['session_binding']['session_id']))
        for run in original_runs if run['session_binding']}
    assert all(row['status'] == 'stopped' for row in before_registry.values())
    auth = token(old, OWNER.actor, *OWNER.scopes)
    second_auth = token(old, OWNER.actor, *OWNER.scopes)
    path = f"/api/v1/pm/workspaces/{wid}/snapshots/{w['joined_snapshot']['snapshot_id']}"
    clients = await asyncio.gather(http(w['port'], 'GET', path, tok=auth), http(w['port'], 'GET', path, tok=second_auth))
    assert clients[0][0] == clients[1][0] == 200 and clients[0][1] == clients[1][1]
    assert clients[0][1]['snapshot']['view_digest'] == w['joined_snapshot']['view_digest']

    next_world = {**w, 'thread_id': w['ledger'].references['next_idea_thread_id']}
    message = await thread_action(next_world, 'pm.message.append', {'content':
        '請保留原登入與重設需求，另把 Review 的復原操作說明整理成可審閱的下一版計畫；推薦碼仍未採用。'})
    doc = conversation.envelope('accepted_plan')
    source = 'input.' + w['ledger'].references['next_idea_input_id']
    doc['title'] = 'Member recovery guidance after the original release'
    doc['candidates'].extend([
        {'key': 'guidance', 'domain': 'scope', 'text': 'Provide accessible recovery instructions',
            'kind': 'proposal', 'source_refs': [source, 'message.' + message['message_id']]},
        {'key': 'guidance_test', 'domain': 'quality', 'text': 'Recovery instructions can be completed using keyboard navigation',
            'kind': 'proposal', 'source_refs': [source]},
    ])
    doc['requirements'].append({'id': 'member.guidance', 'source_key': 'guidance', 'acceptance_keys': ['guidance_test'],
        'priority': 'should', 'depends_on': ['member.recovery']})
    w['pm_settings']['raw_reply'] = json.dumps(doc, ensure_ascii=False)
    request = conversation.propose_request(next_world)
    request['params']['adapter'] = bat_runtime.ADAPTER
    client = old.fleet.client('h1')
    invoke, dropped = client.invoke, False

    async def lose_original_reply(channel, *args, **kwargs):
        nonlocal dropped
        value = await invoke(channel, *args, **kwargs)
        if channel == 'claude:send-message' and not dropped:
            dropped = True
            raise InvokeTimeout('Lost original PM send reply after the provider accepted it')
        return value

    with monkeypatch.context() as lost:
        lost.setattr(client, 'invoke', lose_original_reply)
        created = ops.create(OWNER, **request)[0]
        await settle_operations(ops)
    pending = ops.get(created['operation_id'])
    assert dropped and pending['status'] == 'uncertain', pending
    count = mock.channels().count('claude:send-message')
    database = old.journal.path
    await old.fleet.close()
    await old.inventory.close()
    old.journal.close()
    w['d'] = TaskDaemon(parse_config(w['raw']), database)
    w['d'].ops.context['git_runner'] = w['runner']
    next_world['d'] = w['d']
    ops = w['d'].ops
    server = await asyncio.start_server(w['d']._handle, '127.0.0.1', 0)
    w['port'] = server.sockets[0].getsockname()[1]
    try:
        # A newly connected client retries the original envelope/key after restart.
        status, reply = await http(w['port'], 'POST', '/api/v1/operations', tok=auth,
            body={key: value for key, value in request.items() if key != 'idempotency_key'},
            headers={'Idempotency-Key': request['idempotency_key']})
        assert status == 200 and reply['operation']['operation_id'] == created['operation_id'], reply
        ops.db.execute('UPDATE operations SET next_run_at=0 WHERE operation_id=?', (created['operation_id'],))
        await settle_operations(ops)
        recovered = ops.get(created['operation_id'])
        assert recovered['status'] == 'succeeded', recovered
        assert mock.channels().count('claude:send-message') == count
        run = pm_runs.get_by_operation(ops.db, created['operation_id'])
        assert run['generation'] > max(row['generation'] for row in original_runs)
        assert run['session_binding']['session_id'] not in before_registry
        assert run['context']['accepted_plan']['revision_id'] == original_plan['revision_id']
        assert source in run['context']['source_references']
        for prior in original_runs:
            assert pm_runs.get_by_operation(ops.db, prior['operation_id']) == prior
        assert all(registry.get('h1', sid) == row for sid, row in before_registry.items())
        proposal = proposals.get_proposal(ops, OWNER, wid, recovered['result']['outcome']['proposal_id'])['proposal']
        assert proposal['validation']['status'] == 'format_valid', proposal['validation']
        assert pm_plans.accepted_plan(ops, OWNER, wid)['plan'] == original_plan
        adopted = await conversation.complete(next_world, ops.create(OWNER, **conversation.adoption_request(next_world, proposal))[0])
        assert adopted['status'] == 'succeeded', adopted
        revised = pm_plans.accepted_plan(ops, OWNER, wid)['plan']
        assert revised['revision_id'] != original_plan['revision_id']
        assert {row['id'] for row in revised['document']['requirements']} == {'member.login', 'member.recovery', 'member.guidance'}
        assert pm_plans.get_plan(ops, OWNER, wid, original_plan['revision_id'])['plan'] == original_plan
        assert [dict(row) for row in ops.db.execute('SELECT * FROM pm_work_bindings WHERE workspace_id=?', (wid,))] == original_bindings
        assert [dict(row) for row in ops.db.execute('SELECT * FROM pm_work_packages WHERE workspace_id=?', (wid,))] == original_packages
        assert pm_sprints.get_sprint(ops, OWNER, **w['joined_sprint']['target']) == w['joined_sprint']['closed']
        assert pm_snapshots.get_snapshot(ops, OWNER, wid, w['joined_snapshot']['snapshot_id'])['snapshot'] == w['joined_snapshot']
        clients = await asyncio.gather(http(w['port'], 'GET', path, tok=auth), http(w['port'], 'GET', path, tok=second_auth))
        assert clients[0] == clients[1] and clients[0][0] == 200
        assert clients[0][1]['snapshot']['view_digest'] == w['joined_snapshot']['view_digest']
        w['ledger'].record('reconnected_pm_and_explicit_scope_change', resumed_pm_operation_id=created['operation_id'],
            next_plan_revision_id=revised['revision_id'], next_plan_adoption_operation_id=adopted['operation_id'])
        assert_manual_preserved(w, mock)
    finally:
        server.close()
        await server.wait_closed()
