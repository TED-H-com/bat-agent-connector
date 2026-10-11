"""Fixed relay parent + real canonical children, using only MockBat and central transports."""
from __future__ import annotations

import json

import pytest

from bat_agent_connector import api_auth, registry, service
from bat_agent_connector import orchestration_operations as relay
from bat_agent_connector.operations import NO_KEY_PREFIX, OperationError
from tests import test_api_v1 as api
from tests import test_operations_unification as tasks
from tests.conftest import adopt
from tests.operation_helpers import settle_operations

daemon, served, owned = api.daemon, api.served, tasks.owned
SID = api.MANUAL
P = api_auth.Principal('operator-dashboard', frozenset({'operate', 'observe', 'start'}))
WORDS = '  原始說明\nDo this exactly.  '


def intent(**extra):
    return {'action': relay.ACTION, 'target': {'host': 'h1', 'session_id': SID},
            'params': {'message': WORDS}, 'idempotency_key': 'relay-key', **extra}


async def settle(d, op_id):
    for _ in range(6):
        d.ops.db.execute('UPDATE operations SET next_run_at=0 WHERE operation_id=?', (op_id,))
        await settle_operations(d.ops)
        out = d.ops.get(op_id)
        if out['status'] in {'succeeded', 'failed', 'cancelled', 'needs_attention'}:
            return out
    return d.ops.get(op_id)


async def create(d, **extra):
    op, _ = d.ops.create(P, **intent(**extra))
    return await settle(d, op['operation_id'])


async def test_exact_text_canonical_child_and_positive_replay(daemon, mock, monkeypatch):
    adopt(SID, agent_preset='claude-code')
    out = await create(daemon, target={'host': 'h1', 'session_id': 'sess-claude'})
    assert out['status'] == 'succeeded', out
    assert out['result']['sent'] is True and WORDS in out['result']['text']
    child = daemon.ops.get(out['result']['child_operation_id'])
    assert child['action'] == 'session.send' and child['actor'] == P.actor
    assert child['target']['session_id'] == SID and child['idempotency_key'] is None
    assert not child['idempotency_enabled'] and NO_KEY_PREFIX not in json.dumps(child)
    frames = api.write_frames(mock)
    assert len(frames) == 1 and frames[0]['params']['prompt'] == child['params']['text'] == out['result']['text']
    assert frames[0]['params']['clientMessageId'] == 'batc-' + child['operation_id']
    async def forbidden(*a, **k):
        pytest.fail('positive relay receipt cannot resolve a new target')
    monkeypatch.setattr(service, '_resolve_session', forbidden)
    daemon.fleet.config.host('h1').writes = False
    replay = await create(daemon, target={'host': 'h1', 'session_id': 'sess-claude'})
    assert replay['operation_id'] == out['operation_id'] and api.write_frames(mock) == frames
    with pytest.raises(OperationError, match='TIER_DISABLED'):
        await create(daemon, idempotency_key='later')
    await daemon.fleet.close()


async def test_no_key_parent_and_child_are_independent_and_private(daemon, mock):
    adopt(SID)
    request = {'host': 'h1', 'session_id': SID, 'message': WORDS, 'confirm': True}
    ids = []
    for _ in range(2):
        accepted = await relay.legacy(daemon.ops, P, request, entry='rpc')
        assert accepted['idempotency_key'] is None and accepted['idempotency_enabled'] is False
        ids.append(accepted['operation_id'])
        assert (await settle(daemon, ids[-1]))['status'] == 'succeeded'
    rows = daemon.ops.list()['operations']
    assert len(set(ids)) == 2 and len(rows) == 4 and len(api.write_frames(mock)) == 2
    assert all(o['idempotency_key'] is None and not o['idempotency_enabled'] for o in rows)
    assert NO_KEY_PREFIX not in json.dumps(rows)
    await daemon.fleet.close()


@pytest.mark.parametrize('fallback', [False, True])
async def test_manual_never_written_and_fallback_is_new_fixed_start(daemon, mock, fallback):
    mock.handlers['git:log'] = lambda p: [{'hash': 'a' * 40}]
    before = json.dumps(mock.ws_doc, sort_keys=True)
    out = await create(daemon, params={'message': WORDS, 'start_if_missing': fallback})
    assert out['status'] == 'succeeded', out
    frames = api.write_frames(mock)
    assert all(i['params'].get('sessionId') != SID for i in frames)
    if fallback:
        assert out['result']['sent'] is True and out['result']['started'] is True
        child = daemon.ops.get(out['result']['child_operation_id'])
        assert child['action'] == 'session.start' and child['params']['agent'] == 'codex'
        assert child['params']['use_worktree'] is True and child['target']['workspace'] == 'ws-1'
        row = registry.get('h1', out['result']['session_id'])
        assert row['worktree_path'] != '/srv/demo' and row['origin_cwd'] == '/srv/demo'
    else:
        assert out['result']['sent'] is False and out['result']['read_only'] is True and not frames
    assert json.dumps(mock.ws_doc, sort_keys=True) == before
    await daemon.fleet.close()


@pytest.mark.parametrize('params', [{'message': []}, {'message': ' '}, {'message': 'x', 'queue': 1},
    {'message': 'x', 'earlier': 'wrong'}, {'message': 'x', 'brief': []}, {'message': 'x', 'max_items': 0},
    {'message': 'x', 'start_if_missing': 'true'}, {'message': 'x', 'force': True}])
async def test_invalid_relay_has_no_operation_or_bat_frame(daemon, mock, params):
    with pytest.raises(OperationError):
        await create(daemon, params=params)
    assert not daemon.ops.list()['operations'] and not mock.invokes


@pytest.mark.parametrize('scope', ['observe', 'operate', 'start'])
async def test_fallback_requires_both_original_scopes_before_insert(daemon, scope):
    with pytest.raises(OperationError, match='FORBIDDEN'):
        daemon.ops.create(api_auth.Principal(P.actor, frozenset({scope})),
                          **intent(params={'message': WORDS, 'start_if_missing': True}))
    assert not daemon.ops.list()['operations']


async def test_busy_and_preview_never_create_hidden_send(daemon, mock):
    adopt(SID)
    mock.metas[SID]['isStreaming'] = True
    request = {'host': 'h1', 'session_id': SID, 'message': WORDS, 'dry_run': True}
    doc = await relay.legacy(daemon.ops, P, request, entry='rpc')
    assert WORDS in doc['text'] and not daemon.ops.list()['operations'] and not api.write_frames(mock)
    out = await create(daemon)
    assert out['status'] == 'succeeded' and out['result']['busy'] and out['result']['sent'] is False
    assert len(daemon.ops.list()['operations']) == 1 and not api.write_frames(mock)
    await daemon.fleet.close()


@pytest.mark.parametrize('change', ['registry', 'terminal', 'runtime', 'workspace_invalid', 'read_lost'])
async def test_final_frame_rechecks_fixed_identity_with_one_inflight(daemon, mock, monkeypatch, change):
    from bat_agent_connector.errors import ConnectionLost
    adopt(SID)
    client = daemon.fleet.client('h1')
    client._sem = __import__('asyncio').Semaphore(1)
    original = client.guard_read
    changed = False
    async def inspect(channel, params, **kwargs):
        nonlocal changed
        result = await original(channel, params, **kwargs)
        if channel == 'workspace:load' and not changed:
            changed = True
            if change == 'registry':
                registry.update('h1', SID, created_at=123)
            elif change == 'terminal':
                result = json.loads(result) if isinstance(result, str) else result
                result['terminals'][0]['cwd'] = '/srv/rebound'
            elif change == 'runtime':
                mock.metas[SID]['cwd'] = '/srv/rebound'
            elif change == 'workspace_invalid':
                result = None
            else:
                raise ConnectionLost('read ended before any prompt frame')
        return result
    monkeypatch.setattr(client, 'guard_read', inspect)
    out = await create(daemon)
    assert out['status'] == 'failed', out
    child = daemon.ops.get(out['external_refs']['relay_child_id'])
    assert child['status'] == 'failed' and not api.write_frames(mock)
    assert child['error_code'] == ('RELAY_NOT_SENT' if change == 'read_lost' else 'RELAY_BINDING_CHANGED'), child
    assert len(daemon.ops.list()['operations']) == 2
    await daemon.fleet.close()


@pytest.mark.parametrize('change', ['pause', 'binding', 'version', 'read_lost'])
async def test_task_frame_gate_keeps_original_command_and_refuses_race(owned, mock, monkeypatch, change):
    from bat_agent_connector.errors import ConnectionLost
    d, tid = owned
    before = d.journal.get(tid)
    client = d.fleet.client('h1')
    original = client.guard_read
    changed = False
    async def inspect(channel, params, **kwargs):
        nonlocal changed
        result = await original(channel, params, **kwargs)
        if channel == 'workspace:load' and not changed:
            changed = True
            if change == 'pause':
                d.journal.pause(tid)
            elif change == 'binding':
                d.journal.db.execute('UPDATE tasks SET session_id=? WHERE task_id=?', ('replacement-session', tid))
            elif change == 'version':
                d.journal.db.execute('UPDATE tasks SET control_version=control_version+1 WHERE task_id=?', (tid,))
            else:
                raise ConnectionLost('guard read before prompt')
        return result
    monkeypatch.setattr(client, 'guard_read', inspect)
    out = await create(d, preconditions={'control_version': before['control_version']})
    assert out['status'] == 'failed' and not api.write_frames(mock), out
    commands = d.journal.commands(tid)
    assert len(commands) == 1 and commands[0]['status'] == 'rejected', commands
    child = d.ops.get(out['external_refs']['relay_child_id'])
    assert child['external_refs']['command_id'] == commands[0]['command_id']
    assert json.loads(commands[0]['payload'])['operation_id'] == child['operation_id']
    assert d.journal.get(tid)['state'] != 'uncertain'
    if change == 'pause':
        assert d.journal.get(tid)['paused']


@pytest.mark.parametrize('kind', ['claude', 'codex'])
async def test_lost_child_reply_never_resends_or_repicks(daemon, mock, monkeypatch, kind):
    from bat_agent_connector.errors import InvokeTimeout
    sid = SID if kind == 'claude' else 'sess-codex-0002'
    adopt(sid, agent_preset=kind)
    mock.metas[sid]['isStreaming'] = False
    mock.echo_sends = True
    client = daemon.fleet.client('h1')
    original = client.invoke
    async def lose(channel, *args, **kwargs):
        result = await original(channel, *args, **kwargs)
        if channel == 'claude:send-message':
            raise InvokeTimeout('prompt reply lost')
        return result
    monkeypatch.setattr(client, 'invoke', lose)
    out = await create(daemon, target={'host': 'h1', 'session_id': sid})
    assert out['status'] == 'waiting_external', out
    child_id = out['external_refs']['relay_child_id']
    assert daemon.ops.get(child_id)['status'] == 'uncertain'
    daemon.ops.db.execute('UPDATE operations SET next_run_at=0 WHERE operation_id=?', (child_id,))
    after = await settle(daemon, out['operation_id'])
    assert after['status'] == ('succeeded' if kind == 'claude' else 'waiting_external'), after
    assert len(api.write_frames(mock)) == 1 and len(daemon.ops.list()['operations']) == 2
    if kind == 'codex':
        cancelled = daemon.ops.cancel(P, out['operation_id'])
        assert cancelled['cancel_requested']
        later = await settle(daemon, out['operation_id'])
        assert later['status'] in {'waiting_external', 'needs_attention'}
        assert len(api.write_frames(mock)) == 1
    await daemon.fleet.close()


async def test_cancel_before_child_and_between_admission_and_frame(daemon, mock, monkeypatch):
    adopt(SID)
    op, _ = daemon.ops.create(P, **intent())
    daemon.ops.cancel(P, op['operation_id'])
    assert (await settle(daemon, op['operation_id']))['status'] == 'cancelled'
    assert len(daemon.ops.list()['operations']) == 1 and not mock.invokes
    original = relay.before_send
    async def cancel_before_frame(ctx, client, **kwargs):
        parent_id = relay.linked(ctx.op)['parent_id']
        daemon.ops.cancel(P, parent_id)
        await original(ctx, client, **kwargs)
    monkeypatch.setattr(relay, 'before_send', cancel_before_frame)
    out = await create(daemon, idempotency_key='second')
    assert out['status'] == 'cancelled' and not api.write_frames(mock), out
    assert len(daemon.ops.list()['operations']) == 3
    await daemon.fleet.close()


async def test_child_control_retains_parent_actor_and_scopes(daemon, mock):
    adopt(SID)
    out = await create(daemon, params={'message': WORDS, 'start_if_missing': True})
    child_id = out['external_refs']['relay_child_id']
    reduced = api_auth.Principal(P.actor, frozenset({'operate'}))
    other = api_auth.Principal('other', P.scopes)
    for caller in (reduced, other):
        for oid in (out['operation_id'], child_id):
            with pytest.raises(OperationError, match='FORBIDDEN' if caller is reduced else 'CONTROLLER_MISMATCH'):
                daemon.ops.cancel(caller, oid)
    with pytest.raises(OperationError, match='FORBIDDEN'):
        daemon.ops.create(reduced, **intent(params={'message': WORDS, 'start_if_missing': True}))
    await daemon.fleet.close()


@pytest.mark.parametrize('first', ['http', 'rpc', 'mcp', 'cli'])
async def test_real_adapters_share_key_and_receipts(served, mock, monkeypatch, capsys, first):
    import asyncio

    from bat_agent_connector import cli
    from bat_agent_connector.mcp_server import build_server
    from tests.test_interrupt_operations import mcp_result, rpc
    d, port = served
    adopt(SID)
    tok = api.token(d, P.actor, 'operate', 'observe', 'start')
    monkeypatch.setenv('BATC_TASK_URL', f'http://127.0.0.1:{port}/rpc')
    monkeypatch.setenv('BATC_API_TOKEN', tok)
    monkeypatch.setattr(cli, 'load_config', lambda _: d.fleet.config)
    server, local = build_server(d.fleet.config, principal_only=True)
    params = {'host': 'h1', 'session_id': SID, 'message': WORDS, 'confirm': True, 'idempotency_key': 'relay-key'}
    async def call(door):
        if door == 'http':
            status, out = await api.http(port, 'POST', '/api/v1/operations', tok=tok, body=intent())
            assert status in {200, 202}, out
            return out['operation']['operation_id']
        if door == 'rpc':
            status, out = await rpc(port, tok, 'session_relay', params)
            assert status == 200, out
            out = out['result']
        elif door == 'mcp':
            out = await mcp_result(server, 'session_relay', params)
        else:
            rc = await asyncio.to_thread(cli.main, ['--json', 'relay', 'h1', '--session', SID, '--message', WORDS,
                                                    '--confirm', '--key', 'relay-key'])
            assert rc == 0
            out = json.loads(capsys.readouterr().out)
        assert out['idempotency_key'] == 'relay-key' and out['operation_id']
        return out['operation_id']
    try:
        oid = await call(first)
        assert (await settle(d, oid))['status'] == 'succeeded'
        d.fleet.config.host('h1').writes = False
        for door in ('http', 'rpc', 'mcp', 'cli'):
            assert await call(door) == oid
        assert len(api.write_frames(mock)) == 1
    finally:
        await local.close()


async def test_receipt_only_completion_after_owner_change_and_restart(daemon, mock):
    from bat_agent_connector.operations import OperationService
    adopt(SID)
    op, _ = daemon.ops.create(P, **intent())
    await settle_operations(daemon.ops)  # parent waits; its independently scheduled child completes
    child_id = daemon.ops.get(op['operation_id'])['external_refs']['relay_child_id']
    assert daemon.ops.get(child_id)['status'] == 'succeeded'
    registry.update('h1', SID, created_at=123)
    daemon.fleet.config.host('h1').writes = False
    reopened = OperationService(daemon.journal, actions=list(daemon.ops.actions.values()))
    reopened.context.update(daemon.ops.context)
    daemon.ops = reopened
    out = await settle(daemon, op['operation_id'])
    assert out['status'] == 'succeeded' and out['result']['sent'] is True
    assert len(api.write_frames(mock)) == 1
    await daemon.fleet.close()


@pytest.mark.parametrize('queue', [False, True])
async def test_busy_change_after_initial_read_requires_reviewed_queue(daemon, mock, monkeypatch, queue):
    adopt(SID)
    client = daemon.fleet.client('h1')
    original = client.guard_read
    async def read(channel, *args, **kwargs):
        if channel == 'claude:get-session-meta':
            mock.metas[SID]['isStreaming'] = True
        return await original(channel, *args, **kwargs)
    monkeypatch.setattr(client, 'guard_read', read)
    out = await create(daemon, params={'message': WORDS, 'queue': queue})
    assert out['status'] == ('succeeded' if queue else 'failed'), out
    assert len(api.write_frames(mock)) == int(queue)
    await daemon.fleet.close()


async def test_start_fallback_never_uses_changed_source_head(daemon, mock, monkeypatch):
    mock.handlers['git:log'] = lambda p: [{'hash': 'a' * 40}]
    original = relay.create_child
    def change(ctx, plan):
        result = original(ctx, plan)
        mock.handlers['git:log'] = lambda p: [{'hash': 'b' * 40}]
        return result
    monkeypatch.setattr(relay, 'create_child', change)
    out = await create(daemon, params={'message': WORDS, 'start_if_missing': True})
    assert out['status'] == 'failed' and not api.write_frames(mock), out
    child = daemon.ops.get(out['external_refs']['relay_child_id'])
    assert child['error_code'] == 'RELAY_BINDING_CHANGED'
    await daemon.fleet.close()


async def test_lost_selection_receipt_is_read_only_and_does_not_create_duplicate_children(daemon, mock, monkeypatch):
    from bat_agent_connector.errors import ConnectionLost
    adopt(SID)
    original = relay.select
    dropped = False
    async def read(*args, **kwargs):
        nonlocal dropped
        result = await original(*args, **kwargs)
        if not dropped:
            dropped = True
            raise ConnectionLost('lost read-only selection')
        return result
    monkeypatch.setattr(relay, 'select', read)
    op, _ = daemon.ops.create(P, **intent())
    await settle_operations(daemon.ops)
    assert daemon.ops.get(op['operation_id'])['status'] == 'uncertain' and not api.write_frames(mock)
    out = await settle(daemon, op['operation_id'])
    assert out['status'] == 'succeeded' and len(api.write_frames(mock)) == 1
    assert len(daemon.ops.list()['operations']) == 2
    await daemon.fleet.close()


async def test_actual_cli_dry_run_reads_through_central_without_operation(served, mock, monkeypatch, capsys):
    import asyncio

    from bat_agent_connector import cli
    d, port = served
    monkeypatch.setenv('BATC_TASK_URL', f'http://127.0.0.1:{port}/rpc')
    monkeypatch.setenv('BATC_API_TOKEN', api.token(d, 'preview-only', 'observe'))
    monkeypatch.setattr(cli, 'load_config', lambda _: d.fleet.config)
    rc = await asyncio.to_thread(cli.main, ['--json', '--read-only', 'relay', 'h1', '--session', SID,
                                          '--message', WORDS, '--dry-run'])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out['dry_run'] is True and out['sent'] is False and WORDS in out['text']
    assert 'operation_status' not in out and not d.ops.list()['operations'] and not api.write_frames(mock)


@pytest.mark.parametrize('state', ['paused', 'done', 'failed', 'uncertain'])
@pytest.mark.parametrize('drift', ['cwd', 'lost_registry_owner'])
async def test_task_ownership_before_readonly_fallback_never_creates_successor(owned, mock, state, drift):
    d, tid = owned
    mock.handlers['git:log'] = lambda p: [{'hash': 'a' * 40}]
    if state == 'paused':
        d.journal.pause(tid)
    else:
        d.journal.db.execute('UPDATE tasks SET state=? WHERE task_id=?', (state, tid))
    if drift == 'cwd':
        registry.update('h1', SID, cwd='/srv/changed-binding')
    else:
        registry.update('h1', SID, task_id=None, role=None)
    out = await create(d, params={'message': WORDS, 'start_if_missing': True})
    assert out['status'] == 'failed', out
    assert len(d.ops.list()['operations']) == 1 and not api.write_frames(mock)
    assert not d.journal.commands(tid)
    assert (d.journal.get(tid)['paused'] if state == 'paused' else d.journal.get(tid)['state'] == state)


async def test_new_task_ownership_after_manual_selection_refuses_fallback_frame(owned, mock, monkeypatch):
    d, tid = owned
    source = 'sess-codex-0002'  # Manual source at selection time, not the task's current SID.
    mock.handlers['git:log'] = lambda p: [{'hash': 'a' * 40}]
    original = relay.create_child
    def rebind(ctx, plan):
        result = original(ctx, plan)
        d.journal.db.execute('UPDATE tasks SET session_id=? WHERE task_id=?', (source, tid))
        return result
    monkeypatch.setattr(relay, 'create_child', rebind)
    out = await create(d, target={'host': 'h1', 'session_id': source}, params={'message': WORDS, 'start_if_missing': True})
    assert out['status'] == 'failed' and not api.write_frames(mock), out
    assert out['error_code'] == 'TASK_OWNED_CONTROL_REQUIRED'
