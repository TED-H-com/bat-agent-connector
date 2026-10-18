"""Explicit unsent-input deferral keeps the original fence and sourced Idea."""
from __future__ import annotations

import json

import pytest

from bat_agent_connector import pm_discussions, pm_thread_contexts
from bat_agent_connector.api_auth import Principal
from bat_agent_connector.operations import OperationError
from tests import test_pm_work_follow_up as follow
from tests.operation_helpers import settle_operations
from tests.test_api_v1 import http
from tests.test_pm_work_start import STARTER_OWNER

world, pm, ready, start_ready = follow.world, follow.pm, follow.ready, follow.start_ready


async def defer(w, att, source, key='defer-original-input'):
    return await http(w['port'], 'POST', '/api/v1/operations', tok=w['owner_token'],
        headers={'Idempotency-Key': key}, body={'action': 'pm.work_input.defer',
            'target': {'workspace_id': att['workspace_id'], 'work_item_id': att['work_item_id'],
                'source_operation_id': source},
            'params': {'reason': '留到下一輪討論，先完成原來的工作。'},
            'preconditions': {'expected_state': 'queued'}})


async def test_defer_cancels_before_saving_sourced_idea_and_replays_original_key(start_ready, mock):
    w, ops = start_ready, start_ready['d'].ops
    att = await follow._start_managed_attempt(w, mock)
    await follow._refresh(w, mock, att, att['turn_marker'], streaming=True)
    words = '保留這份原始想法，尚未採用。\n' * 1000
    source = await follow._queue(w, mock, att, words, 'defer-source-input')
    before = len(mock.invokes)
    status, body = await defer(w, att, source)
    assert status == 202, body
    op_id = body['operation']['operation_id']
    await settle_operations(ops)
    result = ops.get(op_id)
    assert result['status'] == 'succeeded', result
    # Cancellation and Idea persistence only touch the central journal, even
    # though their source points to a Work that originally ran on a BAT host.
    assert 'git_execution' not in result['external_refs']['admission_binding']
    original = await follow._get_op(w, source)
    assert original['input_receipt']['state'] == 'cancelled'
    assert original['params']['text'] == words
    assert follow._frames(mock, f'batc-{source}') == []

    assert len(mock.invokes) == before
    assert result['result']['execution_authorized'] is False
    thread_id = result['result']['thread_id']
    page = pm_discussions.get_thread(ops, STARTER_OWNER, att['workspace_id'], thread_id)
    assert page['thread']['mode'] == 'idea'
    original = await follow._get_op(w, source)
    assert original['input_receipt']['deferred_idea']['thread_id'] == thread_id
    context = pm_thread_contexts.read(ops, STARTER_OWNER, att['workspace_id'], thread_id, summaries=True)
    selected = next(item for item in context if item['kind'] == 'work_input')
    assert selected['id'] == source and selected['summary']['text'] == words
    status, replay = await defer(w, att, source)
    assert status == 200 and replay['operation']['operation_id'] == op_id
    assert ops.db.execute('SELECT COUNT(*) FROM pm_threads WHERE operation_id=?', (op_id,)).fetchone()[0] == 1
    assert follow._frames(mock, f'batc-{source}') == []


def envelope(att, source, key):
    return {'action': 'pm.work_input.defer', 'target': {
        'workspace_id': att['workspace_id'], 'work_item_id': att['work_item_id'],
        'source_operation_id': source}, 'params': {'reason': 'Save this for later'},
        'preconditions': {'expected_state': 'queued'}, 'idempotency_key': key}


async def queued(w, mock, text='Original unsent words'):
    att = await follow._start_managed_attempt(w, mock)
    await follow._refresh(w, mock, att, att['turn_marker'], streaming=True)
    source = await follow._queue(w, mock, att, text, 'idea-source')
    return att, source


async def test_partial_save_resumes_after_restart_without_cancelling_or_sending_again(start_ready, mock, monkeypatch):
    w, ops = start_ready, start_ready['d'].ops
    att, source = await queued(w, mock)
    original_save = pm_discussions.create_idea

    def failed_save(*args, **kwargs):
        # Fail after insertion, proving that draft and step roll back together.
        original_save(*args, **kwargs)
        raise OSError('simulated interrupted journal save')

    monkeypatch.setattr(pm_discussions, 'create_idea', failed_save)
    status, body = await defer(w, att, source)
    assert status == 202, body
    parent = body['operation']['operation_id']
    await settle_operations(ops)
    assert ops.get(parent)['status'] == 'needs_attention'
    assert (await follow._get_op(w, source))['input_receipt']['state'] == 'cancelled'
    assert ops.db.execute('SELECT COUNT(*) FROM pm_threads WHERE operation_id=?', (parent,)).fetchone()[0] == 0
    assert ops.get(parent)['steps'][0]['status'] == 'succeeded'
    monkeypatch.setattr(pm_discussions, 'create_idea', original_save)
    async with follow._restarted(w['d']) as restarted:
        def unexpected_cancel(*args, **kwargs):
            pytest.fail('Resume attempted to cancel the original input again')
        monkeypatch.setattr(restarted.ops, 'cancel', unexpected_cancel)
        restarted.ops.resume(STARTER_OWNER, parent)
        await settle_operations(restarted.ops)
        recovered = restarted.ops.get(parent)
        assert recovered['status'] == 'succeeded', recovered
        assert recovered['result']['source_operation_id'] == source
        assert restarted.ops.db.execute('SELECT COUNT(*) FROM pm_threads WHERE operation_id=?', (parent,)).fetchone()[0] == 1
        request = envelope(att, source, 'defer-original-input')
        request['params'] = body['operation']['params']
        replay, fresh = restarted.ops.create(STARTER_OWNER, **request)
        assert not fresh and replay['operation_id'] == parent
    assert follow._frames(mock, f'batc-{source}') == []


async def test_two_admitted_requests_cannot_both_claim_one_input(start_ready, mock):
    w, ops = start_ready, start_ready['d'].ops
    att, source = await queued(w, mock)
    first, _ = ops.create(STARTER_OWNER, **envelope(att, source, 'idea-first'))
    second, _ = ops.create(STARTER_OWNER, **envelope(att, source, 'idea-second'))
    await settle_operations(ops)
    results = [ops.get(item['operation_id']) for item in (first, second)]
    assert sorted(item['status'] for item in results) == ['failed', 'succeeded'], results
    assert next(item for item in results if item['status'] == 'failed')['error_code'] == 'PM_WORK_INPUT_NOT_QUEUED'
    assert ops.db.execute('SELECT COUNT(*) FROM pm_threads WHERE operation_id IN (?,?)',
        (first['operation_id'], second['operation_id'])).fetchone()[0] == 1
    assert follow._frames(mock, f'batc-{source}') == []


async def test_sent_input_wins_race_and_is_never_cancelled_or_saved(start_ready, mock):
    w, ops = start_ready, start_ready['d'].ops
    att, source = await queued(w, mock)
    parent, _ = ops.create(STARTER_OWNER, **envelope(att, source, 'idea-send-race'))
    await follow._refresh(w, mock, att, att['turn_marker'], streaming=False)
    await ops._execute(source)
    assert (await follow._get_op(w, source))['input_receipt']['state'] == 'sent'
    await settle_operations(ops)
    refused = ops.get(parent['operation_id'])
    assert refused['status'] == 'failed' and refused['error_code'] == 'PM_WORK_INPUT_NOT_QUEUED', refused
    assert not ops.get(source)['cancel_requested']
    assert len(follow._frames(mock, f'batc-{source}')) == 1
    assert ops.db.execute('SELECT COUNT(*) FROM pm_threads WHERE operation_id=?', (parent['operation_id'],)).fetchone()[0] == 0


async def test_scope_controller_and_exact_work_are_required(start_ready, mock):
    w, ops = start_ready, start_ready['d'].ops
    att, source = await queued(w, mock)
    for scope in ('observe', 'manage', 'operate'):
        limited = Principal(STARTER_OWNER.actor, STARTER_OWNER.scopes - {scope})
        with pytest.raises(OperationError):
            ops.create(limited, **envelope(att, source, 'idea-no-' + scope))
    other = Principal('idea-other-owner', STARTER_OWNER.scopes)
    current = follow.pm_workspace.get_workspace(ops.db, STARTER_OWNER, att['workspace_id'])
    await follow.pkg_action(w, 'pm.workspace.member.set', {'workspace_id': att['workspace_id']},
        {'actor': other.actor, 'role': 'owner'}, {'expected_version': current['version']}, who=STARTER_OWNER)
    with pytest.raises(OperationError) as denied:
        ops.create(other, **envelope(att, source, 'idea-other-controller'))
    assert denied.value.code == 'PM_WORK_CONTROLLER_CONFLICT'
    wrong = envelope(att, source, 'idea-wrong-work')
    wrong['target']['work_item_id'] = 'wi_' + 'f' * 20
    with pytest.raises(OperationError):
        ops.create(STARTER_OWNER, **wrong)
    assert (await follow._get_op(w, source))['input_receipt']['state'] == 'queued'


async def test_source_withdrawal_hides_idea_operation_and_original_words(start_ready, mock):
    w, ops = start_ready, start_ready['d'].ops
    task, evidence = await follow.add_task_evidence(w)
    await follow.create_package(w, evidence_refs=[evidence])
    att, source = await queued(w, mock, 'Withdraw this original private idea')
    status, body = await defer(w, att, source)
    assert status == 202, body
    parent = body['operation']['operation_id']
    await settle_operations(ops)
    result = ops.get(parent)
    assert result['status'] == 'succeeded', result
    thread_id = result['result']['thread_id']
    with ops.journal.tx():
        ops.db.execute("UPDATE tasks SET controller_authority='{}' WHERE task_id=?", (task['task_id'],))
    for path in (f'/api/v1/operations/{parent}',
            f"/api/v1/pm/workspaces/{att['workspace_id']}/threads/{thread_id}"):
        status, body = await http(w['port'], 'GET', path, tok=w['owner_token'])
        assert status == 404, (path, body)
        assert 'Withdraw this original' not in json.dumps(body)

    for path in ('/api/v1/operations', '/api/v1/events?limit=100', '/api/v1/bootstrap'):
        status, body = await http(w['port'], 'GET', path, tok=w['owner_token'])
        assert status == 200, body
        assert 'Withdraw this original' not in json.dumps(body)


async def test_mcp_submit_and_http_read_share_the_exact_idea_and_loop(start_ready, mock, monkeypatch):
    from tests import test_pm_loop_parity as parity

    w, ops = start_ready, start_ready['d'].ops
    att, source = await queued(w, mock)
    server, fleet = parity._open_mcp(w['d'], monkeypatch, w['port'], w['owner_token'])
    try:
        request = envelope(att, source, 'defer-mcp-original')
        reply = parity.unpack(await server.call_tool('operation_submit', {**request, 'wait_s': 0, 'confirm': True}))
        parent = reply['operation']['operation_id']
        await settle_operations(ops)
        result = ops.get(parent)
        assert result['status'] == 'succeeded', result
        loop = await parity._loops(server, w['port'], w['owner_token'], parent)
        assert loop['dashboard_hash'] == f"#/pm/{att['workspace_id']}/discussions/{result['result']['thread_id']}"
        assert loop['completeness'] == {'status': 'complete', 'reason': None}
        replay = parity.unpack(await server.call_tool('operation_submit', {**request, 'wait_s': 0, 'confirm': True}))
        assert replay['operation']['operation_id'] == parent
        assert follow._frames(mock, f'batc-{source}') == []
    finally:
        await fleet.close()
