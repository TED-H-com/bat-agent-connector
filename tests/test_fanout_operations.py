"""Fixed fanout and planner retirement through real operation children and MockBat."""
from __future__ import annotations

import json

import pytest

from bat_agent_connector import api_auth, registry
from bat_agent_connector import fanout_operations as fanout
from bat_agent_connector.operations import NO_KEY_PREFIX, OperationError
from tests import test_api_v1 as api
from tests.conftest import adopt
from tests.operation_helpers import settle_operations

daemon, served = api.daemon, api.served
P = api_auth.Principal('operator-dashboard', frozenset({'observe', 'start', 'operate'}))
SID = api.MANUAL
ITEMS = [{'index': 1, 'title': 'one', 'prompt': '  unchanged\nfirst  ', 'area': 'one.py'},
         {'index': 2, 'title': 'two', 'prompt': 'second', 'area': 'two.py'}]


@pytest.fixture(autouse=True)
def git_identity(mock):
    mock.handlers['git:log'] = lambda p: [{'hash': 'a' * 40}]


def intent(**kwargs):
    return {'action': 'fanout.start', 'target': {'host': 'h1', 'workspace': 'ws-1'},
            'params': {'plan': ITEMS}, 'idempotency_key': 'fixed-fanout', **kwargs}


async def settle(d, oid):
    for _ in range(10):
        d.ops.db.execute('UPDATE operations SET next_run_at=0 WHERE operation_id=?', (oid,))
        await settle_operations(d.ops)
        out = d.ops.get(oid)
        if out['status'] in {'succeeded', 'failed', 'cancelled', 'needs_attention'}:
            return out
    return d.ops.get(oid)


async def create(d, **kwargs):
    op, _ = d.ops.create(P, **intent(**kwargs))
    return await settle(d, op['operation_id'])


def replies(mock, sid=SID, tasks=None):
    text = '```bat-fanout\n' + json.dumps(tasks or ITEMS) + '\n```'
    mock.states[sid]['messages'] = [{'role': 'assistant', 'content': text, 'id': 'selected-plan', 'timestamp': 1790000009999}]
    mock.states[sid]['isStreaming'] = False
    mock.metas[sid]['isStreaming'] = False
    return text


async def test_file_plan_exact_children_receipts_and_replay(daemon, mock):
    out = await create(daemon)
    assert out['status'] == 'succeeded', out
    assert len(out['result']['started']) == 2 and out['result']['count'] == 2
    assert [x['params']['prompt'] for x in mock.invokes if x['channel'] == 'claude:send-message'] == [i['prompt'] for i in ITEMS]
    children = [daemon.ops.get(i['operation_id']) for i in out['result']['started']]
    assert all(c['actor'] == P.actor and c['action'] == 'session.start' and c['idempotency_key'] is None for c in children)
    assert NO_KEY_PREFIX not in json.dumps(children)
    before = list(api.write_frames(mock))
    daemon.fleet.config.host('h1').writes = False
    again = await create(daemon)
    assert again['operation_id'] == out['operation_id'] and api.write_frames(mock) == before
    with pytest.raises(OperationError, match='TIER_DISABLED'):
        await create(daemon, idempotency_key='later')
    await daemon.fleet.close()


async def test_planner_is_confined_before_prompt_and_never_public_override(daemon, mock):
    out = await create(daemon, action='fanout.plan', params={'message': '  exact original  '})
    assert out['status'] == 'succeeded', out
    row = registry.get('h1', out['result']['session_id'])
    assert row['role'] == 'planner' and row['write_scope'] == 'confined'
    assert row['agent_params'] == {'sandboxMode': 'read-only', 'approvalPolicy': 'never'}
    start = next(x for x in mock.invokes if x['channel'] == 'claude:start-session')
    assert start['params']['options']['codexSandboxMode'] == 'read-only'
    assert start['params']['options']['codexApprovalPolicy'] == 'never'
    assert '  exact original  ' in out['result']['plan'][0]['prompt']
    with pytest.raises(OperationError):
        daemon.ops.create(P, action='session.start', target={'host': 'h1', 'workspace': 'ws-1'},
                          params={'role': 'planner'}, idempotency_key='injected')
    await daemon.fleet.close()


async def test_source_read_is_fixed_and_manual_never_stopped(daemon, mock, monkeypatch):
    original_text = replies(mock)
    original = fanout.create_child
    def changed(ctx, plan, item):
        result = original(ctx, plan, item)
        replies(mock, tasks=[{'title': 'new', 'prompt': 'NOT SELECTED'}])
        return result
    monkeypatch.setattr(fanout, 'create_child', changed)
    out = await create(daemon, target={'host': 'h1', 'session_id': 'sess-claude'}, params={})
    assert out['status'] == 'succeeded', out
    fixed = out['external_refs']['fanout_selection']
    assert fixed['source']['session_id'] == SID and fixed['source']['block'] == original_text
    assert out['result']['plan'] == ITEMS
    assert all(i['prompt'] in sent['params']['prompt'] and 'BAT-STATUS:' in sent['params']['prompt']
               for i, sent in zip(ITEMS, [x for x in mock.invokes if x['channel'] == 'claude:send-message']))
    assert not any(x['params'].get('sessionId') == SID for x in api.write_frames(mock))
    assert 'planner_cleanup' not in out['result']
    await daemon.fleet.close()


async def test_confirmed_all_success_stops_only_original_planner_and_keeps_carrier(daemon, mock):
    adopt(SID, role='planner', agent_preset='claude-code')
    daemon.fleet.config.host('h1').orchestrate_max_sessions = 3
    replies(mock)
    out = await create(daemon, target={'host': 'h1', 'session_id': SID}, params={})
    assert out['status'] == 'succeeded', out
    cleaned = out['result']['planner_cleanup']
    assert cleaned['stopped'] and cleaned['capacity_released'] and cleaned['worktree_kept'], cleaned
    assert registry.get('h1', SID)['status'] == 'stopped'
    channels = mock.channels()
    assert channels.count('claude:stop-session') == 1 and 'worktree:remove' not in channels
    assert channels.index('claude:stop-session') > max(i for i, v in enumerate(channels) if v == 'claude:send-message')
    await daemon.fleet.close()


@pytest.mark.parametrize('params', [{'plan': []}, {'plan': [None]}, {'plan': [dict(ITEMS[0], index=2)]},
    {'plan': [dict(ITEMS[0], force=True)]}, {'plan': ITEMS, 'agent': []}, {'plan': ITEMS, 'max_items': 0},
    {'plan': ITEMS, 'model': {}}])
async def test_invalid_plan_no_operation_or_frame(daemon, mock, params):
    with pytest.raises(OperationError):
        await create(daemon, params=params)
    assert not daemon.ops.list()['operations'] and not mock.invokes


@pytest.mark.parametrize('scope', ['start', 'operate', 'observe'])
async def test_source_plan_requires_start_and_operate(daemon, scope):
    with pytest.raises(OperationError, match='FORBIDDEN'):
        daemon.ops.create(api_auth.Principal(P.actor, frozenset({scope})),
            **intent(target={'host': 'h1', 'session_id': SID}, params={}))
    assert not daemon.ops.list()['operations']


async def test_partial_child_failure_never_replans_or_stops_planner(daemon, mock):
    adopt(SID, role='planner', agent_preset='claude-code')
    replies(mock)
    # Planner consumes one of the two configured slots; the second fixed child cannot start.
    out = await create(daemon, target={'host': 'h1', 'session_id': SID}, params={})
    assert out['status'] == 'failed', out
    rows = out['external_refs']['fanout_result']['started']
    assert len(rows) == 2 and rows[0]['prompt_sent'] is True and rows[1]['operation_status'] == 'failed'
    assert mock.channels().count('claude:start-session') == 1 and 'claude:stop-session' not in mock.channels()
    assert registry.get('h1', SID)['status'] == 'active'
    assert (await create(daemon, target={'host': 'h1', 'session_id': SID}, params={}))['operation_id'] == out['operation_id']
    assert len(daemon.ops.list()['operations']) == 3
    await daemon.fleet.close()


@pytest.mark.parametrize('phase', ['claude:start-session', 'claude:send-message'])
async def test_unknown_first_child_never_dispatches_later_item(daemon, mock, monkeypatch, phase):
    from bat_agent_connector.errors import InvokeTimeout
    client = daemon.fleet.client('h1')
    original = client.invoke
    async def lost(channel, *args, **kwargs):
        result = await original(channel, *args, **kwargs)
        if channel == phase:
            raise InvokeTimeout('reply lost after actual frame')
        return result
    monkeypatch.setattr(client, 'invoke', lost)
    out = await create(daemon)
    assert out['status'] == 'waiting_external', out
    rows = out['external_refs']['fanout_result']['started']
    assert len(rows) == 1 and rows[0]['operation_status'] == 'uncertain'
    daemon.ops.cancel(P, out['operation_id'])
    later = await settle(daemon, out['operation_id'])
    assert later['status'] in {'waiting_external', 'needs_attention', 'cancelled'}, later
    assert mock.channels().count(phase) == 1 and len(daemon.ops.list()['operations']) == 2
    assert len(registry.list_entries('h1')) == 1  # Never erase the original carrier.
    await daemon.fleet.close()


@pytest.mark.parametrize('change', ['paused_owner', 'successor', 'registry', 'active', 'runtime', 'malformed'])
async def test_final_planner_stop_refuses_changed_binding_and_readiness(daemon, mock, monkeypatch, change):
    adopt(SID, role='planner', agent_preset='claude-code')
    replies(mock, tasks=ITEMS[:1])
    client = daemon.fleet.client('h1')
    original = client.guard_read
    async def read(channel, *args, **kwargs):
        result = await original(channel, *args, **kwargs)
        # Only stop's final frame reads the original SID; child-start reads use another SID.
        if channel == 'claude:get-session-meta' and args[0]['sessionId'] == SID:
            if change == 'paused_owner':
                task = daemon.journal.submit(project='p', host='h1', workspace='demo-project', original_words='later', idempotency_key='later')
                daemon.journal.db.execute('UPDATE tasks SET session_id=?,paused=1 WHERE task_id=?', (SID, task['task_id']))
            elif change == 'successor':
                adopt('successor-session', failover_of=SID)
            elif change == 'registry':
                registry.update('h1', SID, agent_params={'changed': True})
            elif change == 'active':
                result = {**result, 'isStreaming': True}
            elif change == 'runtime':
                result = {**result, 'cwd': '/srv/replacement-runtime'}
            else:
                result = {'cwd': '/srv/demo'}  # Absence of streaming is not positive idle.
        return result
    monkeypatch.setattr(client, 'guard_read', read)
    out = await create(daemon, target={'host': 'h1', 'session_id': SID}, params={})
    assert out['status'] == 'succeeded', out
    assert not out['result']['planner_cleanup']['stopped']
    assert 'claude:stop-session' not in mock.channels() and 'worktree:remove' not in mock.channels()
    assert registry.get('h1', SID)['status'] == 'active'
    await daemon.fleet.close()


@pytest.mark.parametrize('cancel', [False, True])
async def test_unknown_stop_reconciles_original_without_resend_even_cancelled(daemon, mock, monkeypatch, cancel):
    from bat_agent_connector.errors import InvokeTimeout
    adopt(SID, role='planner', agent_preset='claude-code')
    replies(mock, tasks=ITEMS[:1])
    client = daemon.fleet.client('h1')
    original = client.invoke
    async def lost(channel, *args, **kwargs):
        result = await original(channel, *args, **kwargs)
        if channel == 'claude:stop-session':
            raise InvokeTimeout('stop reply lost')
        return result
    monkeypatch.setattr(client, 'invoke', lost)
    op, _ = daemon.ops.create(P, **intent(target={'host': 'h1', 'session_id': SID}, params={}))
    # Advance just far enough to observe the first unknown stop; do not force its retry.
    for _ in range(4):
        daemon.ops.db.execute('UPDATE operations SET next_run_at=0 WHERE operation_id=?', (op['operation_id'],))
        await settle_operations(daemon.ops)
        out = daemon.ops.get(op['operation_id'])
        if out['status'] == 'uncertain':
            break
    assert out['status'] == 'uncertain' and mock.channels().count('claude:stop-session') == 1, out
    if cancel:
        daemon.ops.cancel(P, op['operation_id'])
    later = await settle(daemon, op['operation_id'])
    assert later['status'] == ('cancelled' if cancel else 'succeeded'), later
    details = (later.get('result') or later['external_refs']['fanout_result'])['planner_cleanup']
    assert details['stopped'] is True and details['worktree_kept'] is True
    assert registry.get('h1', SID)['status'] == ('active' if cancel else 'stopped')
    assert mock.channels().count('claude:stop-session') == 1
    await daemon.fleet.close()


@pytest.mark.parametrize('change', ['role', 'task', 'cleanup'])
async def test_retirement_cas_preserves_newer_binding_after_stop_ack(daemon, mock, monkeypatch, change):
    adopt(SID, role='planner', agent_preset='claude-code')
    replies(mock, tasks=ITEMS[:1])
    original = registry.retire
    def race(*args, **kwargs):
        if change == 'role':
            registry.update('h1', SID, role='new-owner')
        elif change == 'task':
            registry.update('h1', SID, task_id='new-task')
        else:
            # New cleanup reservation uses the original registry guard authority.
            from bat_agent_connector import cleanup
            def guarded(*a, **k):
                from bat_agent_connector.errors import ResourceReadOnly
                raise ResourceReadOnly('CLEANUP_IN_PROGRESS', 'new reviewed cleanup owns this record')
            monkeypatch.setattr(cleanup, 'guard', guarded)
        return original(*args, **kwargs)
    monkeypatch.setattr(registry, 'retire', race)
    out = await create(daemon, target={'host': 'h1', 'session_id': SID}, params={})
    assert out['status'] in {'succeeded', 'failed'}
    assert registry.get('h1', SID)['status'] == 'active'
    assert mock.channels().count('claude:stop-session') == 1
    if out['status'] == 'succeeded':
        assert out['result']['planner_cleanup']['capacity_released'] is False
    await daemon.fleet.close()


async def test_cancel_unknown_unloaded_proof_missing_stays_uncertain_and_retains_capacity(daemon, mock):
    adopt(SID, role='planner', agent_preset='claude-code')
    replies(mock, tasks=ITEMS[:1])
    mock.handlers['claude:stop-session'] = lambda p: {'ok': False}
    op, _ = daemon.ops.create(P, **intent(target={'host': 'h1', 'session_id': SID}, params={}))
    first = await settle(daemon, op['operation_id'])
    assert first['status'] in {'uncertain', 'needs_attention'}, first
    assert mock.channels().count('claude:stop-session') == 1
    daemon.ops.cancel(P, op['operation_id'])
    later = await settle(daemon, op['operation_id'])
    assert later['status'] in {'uncertain', 'needs_attention'} and later['cancel_requested'], later
    assert registry.get('h1', SID)['status'] == 'active' and mock.channels().count('claude:stop-session') == 1
    await daemon.fleet.close()


async def test_legacy_no_key_independent_and_reduced_scope_cannot_steer_source(daemon, mock):
    request = {'host': 'h1', 'workspace': 'ws-1', 'plan': ITEMS[:1], 'confirm': True}
    outcomes = []
    for _ in range(2):
        out = await fanout.legacy(daemon.ops, P, 'fanout_start', request, entry='rpc')
        assert out['idempotency_key'] is None and out['idempotency_enabled'] is False
        outcomes.append(await settle(daemon, out['operation_id']))
    assert outcomes[0]['operation_id'] != outcomes[1]['operation_id']
    assert len(daemon.ops.list()['operations']) == 4
    assert all(o['idempotency_key'] is None for o in daemon.ops.list()['operations'])
    await daemon.fleet.close()


@pytest.mark.parametrize('first', ['http', 'rpc', 'mcp', 'cli'])
async def test_real_source_transports_replay_fixed_children_after_tier_change(served, mock, monkeypatch, capsys, first):
    import asyncio

    from bat_agent_connector import cli
    from bat_agent_connector.mcp_server import build_server
    from tests.test_interrupt_operations import mcp_result, rpc
    d, port = served
    replies(mock, tasks=ITEMS[:1])
    token = api.token(d, P.actor, 'start', 'operate', 'observe')
    monkeypatch.setenv('BATC_TASK_URL', f'http://127.0.0.1:{port}/rpc')
    monkeypatch.setenv('BATC_API_TOKEN', token)
    monkeypatch.setattr(cli, 'load_config', lambda _: d.fleet.config)
    server, fleet = build_server(d.fleet.config, principal_only=True)
    request = {'host': 'h1', 'session_id': SID, 'agent': 'codex', 'confirm': True, 'idempotency_key': 'source-once'}
    async def call(door):
        if door == 'http':
            code, result = await api.http(port, 'POST', '/api/v1/operations', tok=token,
                body=intent(target={'host': 'h1', 'session_id': SID}, params={'agent': 'codex'}, idempotency_key='source-once'))
            assert code in {200, 202}, result
            return result['operation']['operation_id']
        if door == 'rpc':
            code, result = await rpc(port, token, 'fanout_from_plan', request)
            assert code == 200, result
            result = result['result']
        elif door == 'mcp':
            result = await mcp_result(server, 'fanout_from_plan', request)
        else:
            assert await asyncio.to_thread(cli.main, ['--json', 'fanout-start', 'h1', SID, '--confirm', '--key', 'source-once']) == 0
            result = json.loads(capsys.readouterr().out)
        return result['operation_id']
    try:
        oid = await call(first)
        assert (await settle(d, oid))['status'] == 'succeeded'
        d.fleet.config.host('h1').orchestrate = False
        for door in ('http', 'rpc', 'mcp', 'cli'):
            assert await call(door) == oid
        assert mock.channels().count('claude:start-session') == 1
        reduced = api_auth.Principal(P.actor, frozenset({'start'}))
        for other_id in (oid, d.ops.get(oid)['result']['started'][0]['operation_id']):
            with pytest.raises(OperationError, match='FORBIDDEN'):
                d.ops.cancel(reduced, other_id)
    finally:
        await fleet.close()


async def test_real_cli_readonly_preview_and_fixed_file_apply(served, mock, tmp_path, monkeypatch, capsys):
    import asyncio

    from bat_agent_connector import cli
    d, port = served
    replies(mock, tasks=ITEMS[:1])
    monkeypatch.setenv('BATC_TASK_URL', f'http://127.0.0.1:{port}/rpc')
    monkeypatch.setenv('BATC_API_TOKEN', api.token(d, P.actor, 'observe'))
    monkeypatch.setattr(cli, 'load_config', lambda _: d.fleet.config)
    assert await asyncio.to_thread(cli.main, ['--json', '--read-only', 'fanout-start', 'h1', SID, '--dry-run']) == 0
    assert json.loads(capsys.readouterr().out)['dry_run']
    assert not d.ops.list()['operations'] and not api.write_frames(mock)
    plan = tmp_path / 'reviewed.md'
    plan.write_text('## First task\nKeep these exact requirements.\n')
    assert await asyncio.to_thread(cli.main, ['--json', 'fanout', str(plan)]) == 0
    expected = json.loads(capsys.readouterr().out)
    monkeypatch.setenv('BATC_API_TOKEN', api.token(d, 'file-starter', 'start'))
    args = ['--json', 'fanout', str(plan), '--start', '--host', 'h1', '--workspace', 'ws-1', '--confirm', '--key', 'reviewed-file']
    assert await asyncio.to_thread(cli.main, args) == 0
    accepted = json.loads(capsys.readouterr().out)
    out = await settle(d, accepted['operation_id'])
    assert out['status'] == 'succeeded', out
    sent = next(x for x in mock.invokes if x['channel'] == 'claude:send-message')
    assert sent['params']['prompt'] == expected['tasks'][0]['prompt']
    assert 'claude:stop-session' not in mock.channels()
    assert await asyncio.to_thread(cli.main, args) == 0
    assert json.loads(capsys.readouterr().out)['operation_id'] == accepted['operation_id']


async def test_real_planner_mcp_cli_and_failure_exit(served, mock, monkeypatch, capsys):
    import asyncio

    from bat_agent_connector import cli
    from bat_agent_connector.mcp_server import build_server
    from tests.test_interrupt_operations import mcp_result
    d, port = served
    monkeypatch.setenv('BATC_TASK_URL', f'http://127.0.0.1:{port}/rpc')
    monkeypatch.setenv('BATC_API_TOKEN', api.token(d, 'planner', 'start'))
    monkeypatch.setattr(cli, 'load_config', lambda _: d.fleet.config)
    server, fleet = build_server(d.fleet.config, principal_only=True)
    try:
        out = await mcp_result(server, 'fanout_plan_session', {'host': 'h1', 'workspace': 'ws-1',
            'message': 'exact plan request', 'confirm': True, 'idempotency_key': 'planner-once'})
        assert (await settle(d, out['operation_id']))['status'] == 'succeeded'
        assert await asyncio.to_thread(cli.main, ['--json', 'fanout-plan', 'h1', 'ws-1', '--message', 'exact plan request',
                                                  '--confirm', '--key', 'planner-once']) == 0
        assert json.loads(capsys.readouterr().out)['operation_id'] == out['operation_id']
        # A real central read-only source-resolution failure returns truthful nonzero.
        monkeypatch.setenv('BATC_API_TOKEN', api.token(d, 'source-caller', 'start', 'operate'))
        args = ['--json', 'fanout-start', 'h1', SID, '--confirm', '--key', 'no-block']
        assert await asyncio.to_thread(cli.main, args) == 1
        refused = json.loads(capsys.readouterr().out)
        assert refused['operation_status'] == 'failed' and refused['operation_status_reason']
        assert await asyncio.to_thread(cli.main, args) == 1
        assert json.loads(capsys.readouterr().out)['operation_id'] == refused['operation_id']
    finally:
        await fleet.close()


async def test_parent_receipt_completion_after_restart_does_not_recheck_moving_source(daemon, mock):
    from bat_agent_connector.operations import OperationService
    out, _ = daemon.ops.create(P, **intent(params={'plan': ITEMS[:1]}))
    await settle_operations(daemon.ops)
    child = [o for o in daemon.ops.list()['operations'] if o['action'] == 'session.start'][0]
    assert child['status'] == 'succeeded'
    daemon.fleet.config.host('h1').writes = False
    mock.handlers['git:log'] = lambda p: [{'hash': 'b' * 40}]
    reopened = OperationService(daemon.journal, actions=list(daemon.ops.actions.values()))
    reopened.context.update(daemon.ops.context)
    daemon.ops = reopened
    after = await settle(daemon, out['operation_id'])
    assert after['status'] == 'succeeded' and mock.channels().count('claude:start-session') == 1
    await daemon.fleet.close()


async def test_fixed_parent_source_commit_change_refuses_new_child_effect(daemon, mock, monkeypatch):
    original = fanout.create_child
    def rebind(ctx, plan, item):
        result = original(ctx, plan, item)
        mock.handlers['git:log'] = lambda p: [{'hash': 'b' * 40}]
        return result
    monkeypatch.setattr(fanout, 'create_child', rebind)
    out = await create(daemon)
    assert out['status'] == 'failed' and not api.write_frames(mock), out
    assert len(daemon.ops.list()['operations']) == 2
    await daemon.fleet.close()


async def test_retirement_checks_active_claim_under_flock(daemon, mock, monkeypatch):
    from bat_agent_connector.confinement import ConfinementRefused
    adopt(SID, role='planner', agent_preset='claude-code')
    replies(mock, tasks=ITEMS[:1])
    original = registry.refuse_start_claim
    def claimed(path, host, sid):
        if sid == SID:
            raise ConfinementRefused('CONFINEMENT_START_UNSETTLED', 'fixture active start claim')
        return original(path, host, sid)
    monkeypatch.setattr(registry, 'refuse_start_claim', claimed)
    out = await create(daemon, target={'host': 'h1', 'session_id': SID}, params={})
    assert out['status'] == 'failed' and out['error_code'] == 'CONFINEMENT_START_UNSETTLED', out
    assert mock.channels().count('claude:stop-session') == 1 and registry.get('h1', SID)['status'] == 'active'
    await daemon.fleet.close()


@pytest.mark.parametrize('initial', ['unloaded', 'missing_sdk', 'missing_cwd'])
@pytest.mark.parametrize('boundary', ['children', 'final_frame', 'reopen'])
async def test_new_runtime_identity_never_authorizes_planner_stop(daemon, mock, monkeypatch, initial, boundary):
    from bat_agent_connector.operations import OperationService
    from bat_agent_connector.task_journal import Journal
    adopt(SID, role='planner', agent_preset='claude-code')
    replies(mock, tasks=ITEMS[:1])
    mock.archives[SID] = list(mock.states[SID]['messages'])
    original_meta = dict(mock.metas[SID])
    original_meta['sdkSessionId'] = 'original-sdk'
    mock.metas[SID] = dict(original_meta)
    if initial == 'unloaded':
        mock.metas[SID] = None
    else:
        mock.metas[SID].pop('sdkSessionId' if initial == 'missing_sdk' else 'cwd', None)
    def replacement():
        sdk = original_meta['sdkSessionId'] if initial == 'missing_cwd' else 'replacement-sdk'
        mock.metas[SID] = {**original_meta, 'sdkSessionId': sdk, 'cwd': '/srv/demo', 'isStreaming': False}
    if boundary == 'children':
        original = fanout.create_child
        def create_then_replace(ctx, plan, item):
            saved = original(ctx, plan, item)
            replacement()
            return saved
        monkeypatch.setattr(fanout, 'create_child', create_then_replace)
    elif boundary == 'final_frame':
        # For unloaded plans there is no initial stop invocation. Make it loaded
        # on the pre-stop meta read; partial loaded plans change inside the final
        # callback, after the initial idle check and authorization have passed.
        client = daemon.fleet.client('h1')
        name = 'invoke' if initial == 'unloaded' else 'guard_read'
        original = getattr(client, name)
        async def read(channel, *args, **kwargs):
            if (channel == 'claude:get-session-meta' and args[0]['sessionId'] == SID
                    and fanout.relay.receipt(daemon.ops, oid, 'fanout.child.1')):
                replacement()
            return await original(channel, *args, **kwargs)
        monkeypatch.setattr(client, name, read)
    op, _ = daemon.ops.create(P, **intent(target={'host': 'h1', 'session_id': SID}, params={}))
    oid = op['operation_id']
    if boundary == 'reopen':
        # Stop at the durable child boundary, regardless of how long BAT takes.
        # Draining all due work can also run the parent's one-second retry and
        # retire the still-original planner before the replacement below exists.
        await daemon.ops._execute(oid)
        saved = fanout.relay.receipt(daemon.ops, oid, 'fanout.child.1')
        assert saved is not None
        await daemon.ops._execute(saved['operation_id'])
        child = daemon.ops.get(saved['operation_id'])
        assert child['status'] == 'succeeded'
        parent = daemon.ops.get(oid)
        assert parent['status'] == 'waiting_external'
        assert not any(step['name'] == 'planner.stop' for step in parent['steps'])
        replacement()
        daemon.journal.close()
        daemon.journal = Journal(daemon.journal.path)
        reopened = OperationService(daemon.journal, actions=list(daemon.ops.actions.values()))
        reopened.context.update(daemon.ops.context)
        daemon.ops = reopened
    out = await settle(daemon, oid)
    assert out['status'] == 'succeeded', out
    assert out['result']['started'][0]['prompt_sent'] is True
    assert out['result']['planner_cleanup']['stopped'] is False
    assert out['result']['planner_cleanup']['worktree_kept'] is True
    assert registry.get('h1', SID)['status'] == 'active'
    assert 'claude:stop-session' not in mock.channels() and 'worktree:remove' not in mock.channels()
    assert mock.channels().count('claude:start-session') == 1
    await daemon.fleet.close()
