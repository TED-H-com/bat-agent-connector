"""Durable standalone failover against real central operations and MockBat transport."""
from __future__ import annotations

from dataclasses import replace

import pytest

from bat_agent_connector import api_auth, registry, resource_policy, service
from bat_agent_connector import failover_operations as failover
from bat_agent_connector.errors import ResourceReadOnly
from bat_agent_connector.operations import OperationError, OperationService
from bat_agent_connector.task_journal import Journal
from tests import test_api_v1 as api
from tests.operation_helpers import settle_operations
from tests.test_lifecycle import _add_wt_claude

daemon, served = api.daemon, api.served
P = api_auth.Principal('operator-dashboard', frozenset({'observe', 'start', 'operate'}))
SID = 'wt-claude-0007'


@pytest.fixture(autouse=True)
def source(mock, daemon):
    _add_wt_claude(mock, SID)
    mock.handlers['git:log'] = lambda p: [{'hash': 'a' * 40}]
    return SID


def intent(**kwargs):
    return {'action': 'session.failover', 'target': {'host': 'h1', 'session_id': SID}, 'params': {},
            'idempotency_key': 'failover-once', **kwargs}


async def settle(d, oid):
    await settle_operations(d.ops)
    return d.ops.get(oid)


async def create(d, **kwargs):
    op, _ = d.ops.create(P, **intent(**kwargs))
    return await settle(d, op['operation_id'])


async def test_shared_carrier_fixed_handoff_receipts_and_replay(daemon, mock):
    out = await create(daemon)
    assert out['status'] == 'succeeded', out
    result = out['result']
    successor = registry.get('h1', result['new_session_id'])
    assert result['same_worktree'] and result['prompt_sent']
    assert result['cwd'] == '/srv/demo/.bat-worktrees/abc' and result['branch'] == 'bat/worktree-abc'
    assert registry.get('h1', SID)['status'] == 'superseded'
    assert successor['handoff_status'] == 'sent'
    assert len([e for e in registry.list_entries('h1') if e['status'] in {'active', 'starting'}]) == 1
    assert mock.channels().count('claude:start-session') == mock.channels().count('claude:send-message') == 1
    assert 'claude:stop-session' not in mock.channels() and 'worktree:create' not in mock.channels()
    sent = next(i for i in mock.invokes if i['channel'] == 'claude:send-message')
    assert 'ORIGINAL TASK' in sent['params']['prompt'] and 'also add tests' in sent['params']['prompt']
    assert sent['params']['clientMessageId'] == result['message_id']
    before = list(api.write_frames(mock))
    daemon.fleet.config.host('h1').orchestrate = False
    assert (await create(daemon))['operation_id'] == out['operation_id']
    assert api.write_frames(mock) == before
    with pytest.raises(OperationError, match='TIER_DISABLED'):
        await create(daemon, idempotency_key='fresh')
    await daemon.fleet.close()


@pytest.mark.parametrize('change', ['streaming', 'unknown_idle', 'pending', 'task', 'manual', 'head', 'other_manual'])
async def test_no_new_start_without_stopped_writer_and_original_ownership(daemon, mock, monkeypatch, change):
    if change == 'streaming':
        mock.states[SID]['isStreaming'] = mock.metas[SID]['isStreaming'] = True
    elif change == 'unknown_idle':
        mock.metas[SID].pop('isStreaming')
    elif change == 'pending':
        mock.states[SID]['pendingPermission'] = {'id': 'new'}
    elif change == 'task':
        task = daemon.journal.submit(project='p', host='h1', workspace='demo-project', original_words='task', idempotency_key='task')
        daemon.journal.db.execute('UPDATE tasks SET session_id=?,paused=1 WHERE task_id=?', (SID, task['task_id']))
    elif change == 'manual':
        registry.registry_path().unlink()
    elif change == 'head':
        original = failover._invoke
        async def rebind(*args, **kwargs):
            mock.handlers['git:log'] = lambda p: [{'hash': 'b' * 40}]
            return await original(*args, **kwargs)
        monkeypatch.setattr(failover, '_invoke', rebind)
    else:
        mock.ws_doc['terminals'].append({'id': 'manual-neighbor', 'cwd': '/srv/demo/.bat-worktrees/abc', 'agentPreset': 'claude-code'})
    out = await create(daemon, params={'force': True})
    assert out['status'] == 'failed', out
    assert not api.write_frames(mock)
    if change == 'head':
        assert registry.get('h1', SID)['failover_fence'] is None
        assert registry.get('h1', SID)['status'] == 'active'
    await daemon.fleet.close()


def reopen(d):
    d.journal.close()
    d.journal = Journal(d.journal.path)
    ops = OperationService(d.journal, actions=list(d.ops.actions.values()))
    ops.context.update(d.ops.context)
    d.ops = ops
    d.coordinator.journal = d.journal
    d.adapter.journal = d.journal


@pytest.mark.parametrize('lost', ['claude:start-session', 'claude:send-message'])
async def test_sent_unknown_recovery_never_resends_and_preserves_writer_fence(daemon, mock, monkeypatch, lost):
    from bat_agent_connector.errors import InvokeTimeout
    client = daemon.fleet.client('h1')
    original = client.invoke
    async def lose(channel, *args, **kwargs):
        result = await original(channel, *args, **kwargs)
        if channel == lost:
            raise InvokeTimeout('positive frame with lost reply')
        return result
    monkeypatch.setattr(client, 'invoke', lose)
    out = await create(daemon)
    assert out['status'] == 'uncertain', out
    replacement = out['external_refs']['failover_result']['pending_successor']
    source_tab = next(t for t in mock.ws_doc['terminals'] if t.get('id') == SID)
    grant = await resource_policy.authorize_session(daemon.fleet, 'h1', 'session.send', source_tab)
    with pytest.raises(ResourceReadOnly, match='FAILOVER_FENCED'):
        await original('claude:send-message', {'sessionId': SID, 'prompt': 'later', 'clientMessageId': 'later'}, grant=grant)
    if lost == 'claude:send-message':
        with pytest.raises(ResourceReadOnly, match='FAILOVER_HANDOFF_PENDING'):
            await service.session_send(daemon.fleet, 'h1', replacement, 'bypass', confirm=True)
    reopen(daemon)
    daemon.ops.db.execute('UPDATE operations SET next_run_at=0 WHERE operation_id=?', (out['operation_id'],))
    after = await settle(daemon, out['operation_id'])
    assert after['status'] == ('succeeded' if lost == 'claude:start-session' else 'uncertain'), (after['status_reason'], after['steps'])
    assert mock.channels().count(lost) == 1
    with pytest.raises(ResourceReadOnly, match='FAILOVER_FENCED'):
        resource_policy.check_grant(grant, 'h1', 'claude:send-message', {'sessionId': SID})
    if lost == 'claude:send-message':
        daemon.ops.db.execute("UPDATE operations SET status='needs_attention' WHERE operation_id=?", (out['operation_id'],))
        daemon.ops.cancel(P, out['operation_id'])
        after = await settle(daemon, out['operation_id'])
        assert after['status'] == 'uncertain' and after['cancel_requested']
        assert registry.get('h1', replacement)['handoff_status'] == 'pending'
        assert mock.channels().count('claude:send-message') == 1
    await daemon.fleet.close()


@pytest.mark.parametrize('boundary', ['connect', 'semaphore'])
async def test_waiting_predecessor_frame_observes_new_final_fence(daemon, mock, monkeypatch, boundary):
    import asyncio
    client = daemon.fleet.client('h1')
    tab = next(t for t in mock.ws_doc['terminals'] if t.get('id') == SID)
    grant = await resource_policy.authorize_session(daemon.fleet, 'h1', 'session.send', tab)
    entered, release = asyncio.Event(), asyncio.Event()
    current_task = None
    original = client.connect
    async def connect():
        if asyncio.current_task() is current_task:
            entered.set()
            await release.wait()
        return await original()
    if boundary == 'connect':
        monkeypatch.setattr(client, 'connect', connect)
    else:
        # Pause the direct frame at its real semaphore without blocking the
        # independent central client used by the durable failover operation.
        from bat_agent_connector.service import Fleet
        extra = Fleet(daemon.fleet.config)
        client = extra.client('h1')
        await client.connect()
        await client._sem.acquire()
        # Fixture defaults allow multiple inflight calls; consume every permit.
        permits = 1
        while client._sem._value:
            await client._sem.acquire()
            permits += 1
    called = []
    current_task = asyncio.create_task(client.invoke('claude:send-message', {'sessionId': SID, 'prompt': 'STALE', 'clientMessageId': 'stale'},
                                                     grant=grant, before_send=lambda: called.append('task-gate')))
    if boundary == 'connect':
        await asyncio.wait_for(entered.wait(), 5)
    else:
        await asyncio.sleep(0)
    out = await create(daemon)
    assert out['status'] == 'succeeded', out
    release.set()
    if boundary == 'semaphore':
        for _ in range(permits):
            client._sem.release()
    with pytest.raises(ResourceReadOnly, match='FAILOVER_FENCED'):
        await current_task
    assert called == ['task-gate']
    assert not any(i['channel'] == 'claude:send-message' and i['params'].get('prompt') == 'STALE' for i in mock.invokes)
    if boundary == 'semaphore':
        await extra.close()
    await daemon.fleet.close()


@pytest.mark.parametrize('change', ['none', 'owner'])
async def test_cancel_after_positive_handoff_ack_reopen_only_finishes_original_projection(daemon, mock, monkeypatch, change):
    original = registry.project_start
    def fail_projection(*args, **kwargs):
        if kwargs['fields'].get('handoff_status') == 'sent':
            raise OSError('local registry temporarily unavailable after ACK')
        return original(*args, **kwargs)
    monkeypatch.setattr(registry, 'project_start', fail_projection)
    out = await create(daemon)
    assert out['status'] == 'uncertain', out
    replacement = out['external_refs']['failover_result']['pending_successor']
    assert registry.get('h1', replacement)['handoff_status'] == 'pending'
    writes = list(api.write_frames(mock))
    monkeypatch.setattr(registry, 'project_start', original)
    if change == 'owner':
        registry.update('h1', replacement, task_id='new-owner')
    reopen(daemon)
    daemon.ops.cancel(P, out['operation_id'])
    after = await settle(daemon, out['operation_id'])
    assert after['status'] == ('cancelled' if change == 'none' else 'needs_attention'), after
    assert api.write_frames(mock) == writes
    assert registry.get('h1', replacement)['handoff_status'] == ('sent' if change == 'none' else 'pending')
    assert registry.get('h1', SID)['failover_fence']
    await daemon.fleet.close()


@pytest.mark.parametrize('loss', ['journal', 'ack', 'marker', 'registry'])
async def test_completed_successor_requires_readable_exact_owner_receipts(daemon, mock, loss):
    out = await create(daemon)
    assert out['status'] == 'succeeded', out
    sid = out['result']['new_session_id']
    tab, _ = await service._resolve_session(daemon.fleet.client('h1'), sid)
    grant = await resource_policy.authorize_session(daemon.fleet, 'h1', 'session.send', tab)
    resource_policy.check_grant(grant, 'h1', 'claude:send-message', {'sessionId': sid})
    if loss == 'journal':
        marker = registry.get('h1', SID)['failover_fence']
        marker['journal_path'] += '.missing'
        registry.update('h1', SID, failover_fence=marker)
        registry.update('h1', sid, failover_fence=marker)
    elif loss == 'ack':
        daemon.ops.db.execute("UPDATE operation_steps SET response='{}' WHERE operation_id=? AND name='failover.1.handoff'", (out['operation_id'],))
    elif loss == 'marker':
        registry.update('h1', SID, failover_fence={'broken': True})
    else:
        registry.registry_path().write_text('unreadable')
    with pytest.raises(ResourceReadOnly):
        resource_policy.check_grant(grant, 'h1', 'claude:send-message', {'sessionId': sid})
    # Broken failover receipts do not block stop while original controller evidence survives.
    stop_grant = replace(grant, action='session.stop', channels=frozenset({'claude:stop-session'}))
    if loss == 'registry':
        with pytest.raises(ResourceReadOnly):
            resource_policy.check_grant(stop_grant, 'h1', 'claude:stop-session', {'sessionId': sid})
    else:
        resource_policy.check_grant(stop_grant, 'h1', 'claude:stop-session', {'sessionId': sid})
    await daemon.fleet.close()


@pytest.mark.parametrize('params,target', [({'force': []}, {'host': 'h1', 'session_id': SID}),
    ({'model': {}}, {'host': 'h1', 'session_id': SID}), ({'tail_messages': 0}, {'host': 'h1', 'session_id': SID}),
    ({'task_authority': {}}, {'host': 'h1', 'session_id': SID}), ({}, {'host': [], 'session_id': SID}),
    ({'all_exhausted': True, 'instructions': 'do this'}, {'host': 'h1'}),
    ({'all_exhausted': True}, {'host': 'h1', 'session_id': SID})])
async def test_invalid_fixed_intents_do_not_create_operation_or_frame(daemon, mock, params, target):
    with pytest.raises(OperationError):
        await create(daemon, params=params, target=target)
    assert not daemon.ops.list()['operations'] and not api.write_frames(mock)


@pytest.mark.parametrize('scopes', [{'start'}, {'operate'}, {'observe'}])
async def test_current_caller_scopes_apply_to_admission_replay_and_controls(daemon, mock, scopes):
    out = await create(daemon)
    weaker = api_auth.Principal(P.actor, frozenset(scopes))
    for verb in ('create', 'cancel', 'resume'):
        with pytest.raises(OperationError, match='FORBIDDEN'):
            if verb == 'create':
                daemon.ops.create(weaker, **intent())
            else:
                getattr(daemon.ops, verb)(weaker, out['operation_id'])
    stranger = api_auth.Principal('different-actor', P.scopes)
    with pytest.raises(OperationError, match='CONTROLLER_MISMATCH'):
        daemon.ops.cancel(stranger, out['operation_id'])
    assert mock.channels().count('claude:start-session') == 1
    await daemon.fleet.close()


async def test_no_key_is_independent_and_existing_successor_never_adopted(daemon, mock):
    request = {'host': 'h1', 'session_id': SID, 'confirm': True}
    first = await failover.legacy(daemon.ops, P, request, entry='mcp')
    second = await failover.legacy(daemon.ops, P, request, entry='mcp')
    assert first['operation_id'] != second['operation_id']
    for row in (first, second):
        assert row['idempotency_key'] is None and row['idempotency_enabled'] is False
    assert second['skipped'].startswith('existing successor')
    assert second['existing'][0]['operation_id'] == first['operation_id']
    assert mock.channels().count('claude:start-session') == mock.channels().count('claude:send-message') == 1
    await daemon.fleet.close()


@pytest.mark.parametrize('first', ['http', 'rpc', 'mcp', 'cli'])
async def test_real_transports_replay_same_intent_after_local_and_central_tier_change(served, mock, monkeypatch, capsys, first):
    import asyncio
    import json

    from bat_agent_connector import cli
    from bat_agent_connector.mcp_server import build_server
    from tests.test_interrupt_operations import mcp_result, rpc
    d, port = served
    token = api.token(d, P.actor, 'observe', 'start', 'operate')
    monkeypatch.setenv('BATC_TASK_URL', f'http://127.0.0.1:{port}/rpc')
    monkeypatch.setenv('BATC_API_TOKEN', token)
    monkeypatch.setattr(cli, 'load_config', lambda _: d.fleet.config)
    request = {'host': 'h1', 'session_id': 'wt-claude', 'confirm': True, 'idempotency_key': 'doors',
               'all_exhausted': False, 'force': False, 'archive_only': False, 'tail_messages': 12}
    params = {k: request[k] for k in failover.FIELDS if k in request}
    async def call(door):
        if door == 'http':
            code, result = await api.http(port, 'POST', '/api/v1/operations', tok=token,
                body=intent(target={'host': 'h1', 'session_id': 'wt-claude'}, params=params, idempotency_key='doors'))
            assert code in {200, 202}, result
            return result['operation']['operation_id']
        if door == 'rpc':
            code, result = await rpc(port, token, 'session_failover', request)
            assert code == 200, result
            result = result['result']
        elif door == 'mcp':
            server, fleet = build_server(d.fleet.config, principal_only=True)
            try:
                result = await mcp_result(server, 'session_failover', request)
            finally:
                await fleet.close()
        else:
            assert await asyncio.to_thread(cli.main, ['--json', 'failover', 'h1', 'wt-claude', '--confirm', '--key', 'doors']) == 0
            result = json.loads(capsys.readouterr().out)
        return result['operation_id']
    oid = await call(first)
    assert (await settle(d, oid))['status'] == 'succeeded'
    d.fleet.config.host('h1').orchestrate = False
    for door in ('http', 'rpc', 'mcp', 'cli'):
        assert await call(door) == oid
    assert mock.channels().count('claude:start-session') == 1
    assert d.ops.get(oid)['actor'] == P.actor


async def test_real_cli_observe_preview_and_failure_exit(served, mock, monkeypatch, capsys):
    import asyncio
    import json

    from bat_agent_connector import cli
    d, port = served
    monkeypatch.setenv('BATC_TASK_URL', f'http://127.0.0.1:{port}/rpc')
    monkeypatch.setenv('BATC_API_TOKEN', api.token(d, P.actor, 'observe'))
    monkeypatch.setattr(cli, 'load_config', lambda _: d.fleet.config)
    before = registry.registry_path().read_bytes()
    assert await asyncio.to_thread(cli.main, ['--json', '--read-only', 'failover', 'h1', SID, '--dry-run']) == 0
    preview = json.loads(capsys.readouterr().out)
    assert preview['dry_run'] and 'ORIGINAL TASK' in preview['handoff_preview']
    assert registry.registry_path().read_bytes() == before and not d.ops.list()['operations']
    assert not api.write_frames(mock)
    monkeypatch.setenv('BATC_API_TOKEN', api.token(d, P.actor, 'start', 'operate'))
    mock.metas[SID]['isStreaming'] = True
    args = ['--json', 'failover', 'h1', SID, '--confirm', '--key', 'refused', '--force']
    assert await asyncio.to_thread(cli.main, args) == 1
    refused = json.loads(capsys.readouterr().out)
    assert refused['operation_status'] == 'failed' and refused['operation_status_reason']
    assert await asyncio.to_thread(cli.main, args) == 1
    assert json.loads(capsys.readouterr().out)['operation_id'] == refused['operation_id']


async def test_fixed_batch_and_partial_unknown_never_reselects_or_starts_later_item(daemon, mock, monkeypatch):
    from bat_agent_connector.errors import InvokeTimeout
    other = _add_wt_claude(mock, 'wt-claude-0008')
    # Give the second source its own proven carrier; same-carrier sources block.
    tab = next(t for t in mock.ws_doc['terminals'] if t['id'] == other)
    tab.update(cwd='/srv/demo/.bat-worktrees/def', worktreePath='/srv/demo/.bat-worktrees/def', worktreeBranch='bat/worktree-def')
    mock.metas[other]['cwd'] = tab['cwd']
    mock.worktrees[other].update(worktreePath=tab['cwd'], branchName=tab['worktreeBranch'])
    mock.git_branch[tab['cwd']] = tab['worktreeBranch']
    registry.update('h1', other, cwd=tab['cwd'], worktree_path=tab['cwd'], branch=tab['worktreeBranch'])
    daemon.fleet.config.host('h1').orchestrate_max_sessions = 4
    client = daemon.fleet.client('h1')
    original = client.invoke
    async def lose(channel, *args, **kwargs):
        result = await original(channel, *args, **kwargs)
        if channel == 'claude:send-message':
            raise InvokeTimeout('handoff unknown')
        return result
    monkeypatch.setattr(client, 'invoke', lose)
    out = await create(daemon, target={'host': 'h1'}, params={'all_exhausted': True})
    assert out['status'] == 'uncertain', out
    selected = failover.relay.receipt(daemon.ops, out['operation_id'], 'failover.selection')['session_ids']
    assert set(selected) == {SID, other}
    assert mock.channels().count('claude:start-session') == 1
    _add_wt_claude(mock, 'wt-claude-new')
    daemon.ops.db.execute('UPDATE operations SET next_run_at=0 WHERE operation_id=?', (out['operation_id'],))
    after = await settle(daemon, out['operation_id'])
    assert after['status'] == 'uncertain'
    assert mock.channels().count('claude:start-session') == 1
    assert failover.relay.receipt(daemon.ops, out['operation_id'], 'failover.selection')['session_ids'] == selected
    await daemon.fleet.close()


@pytest.mark.parametrize('change', ['source_busy', 'task_owner', 'shared_writer'])
async def test_confinement_await_precedes_final_source_and_writer_checks(daemon, mock, monkeypatch, change):
    from bat_agent_connector import confinement
    original = confinement.guard_start_frame
    async def changed(*args, **kwargs):
        await original(*args, **kwargs)
        if change == 'source_busy':
            mock.metas[SID]['isStreaming'] = True
        elif change == 'task_owner':
            task = daemon.journal.submit(project='p', host='h1', workspace='demo-project', original_words='new owner', idempotency_key='new-owner')
            daemon.journal.db.execute('UPDATE tasks SET session_id=?,paused=1 WHERE task_id=?', (SID, task['task_id']))
        else:
            mock.ws_doc['terminals'].append({'id': 'new-manual-consumer', 'cwd': '/srv/demo/.bat-worktrees/abc', 'agentPreset': 'claude-code'})
    monkeypatch.setattr(confinement, 'guard_start_frame', changed)
    out = await create(daemon)
    assert out['status'] == 'failed' and not api.write_frames(mock), out
    assert registry.get('h1', SID)['status'] == 'active' and registry.get('h1', SID)['failover_fence'] is None
    await daemon.fleet.close()


async def test_known_unsent_handoff_explicit_resume_rechecks_gates_without_restarting(daemon, mock, monkeypatch):
    from bat_agent_connector.errors import ConnectionLost
    client = daemon.fleet.client('h1')
    original = client.guard_read
    sid = None
    async def unavailable(channel, params):
        if channel == 'claude:get-session-meta' and params['sessionId'] != SID:
            raise ConnectionLost('failed identity read before handoff transport')
        return await original(channel, params)
    monkeypatch.setattr(client, 'guard_read', unavailable)
    out = await create(daemon)
    assert out['status'] == 'needs_attention', out
    sid = out['external_refs']['failover_result']['pending_successor']
    assert registry.get('h1', sid)['handoff_frame_sha256'] is None
    assert mock.channels().count('claude:start-session') == 1 and 'claude:send-message' not in mock.channels()
    monkeypatch.setattr(client, 'guard_read', original)
    daemon.ops.resume(P, out['operation_id'])
    after = await settle(daemon, out['operation_id'])
    assert after['status'] == 'succeeded', after
    assert mock.channels().count('claude:start-session') == mock.channels().count('claude:send-message') == 1
    await daemon.fleet.close()


async def test_default_rate_interval_allows_initial_handoff_but_hourly_budget_remains(daemon, mock):
    from bat_agent_connector.safety import Audit
    daemon.fleet.config.safety.write_min_interval_s = 300
    daemon.fleet.config.safety.max_writes_per_hour = 1
    out = await create(daemon)
    assert out['status'] == 'needs_attention', out
    assert 'claude:send-message' not in mock.channels()
    attempts = [r for r in Audit(daemon.fleet.config.safety)._tail() if r['phase'] == 'attempt']
    assert len(attempts) == 1 and attempts[0]['channel'] == 'claude:start-session'
    daemon.fleet.config.safety.max_writes_per_hour = 10
    daemon.ops.resume(P, out['operation_id'])
    assert (await settle(daemon, out['operation_id']))['status'] == 'succeeded'
    attempts = [r for r in Audit(daemon.fleet.config.safety)._tail() if r['phase'] == 'attempt']
    assert len(attempts) == 2 and all(r['actor'] == P.actor for r in attempts)
    assert (await create(daemon))['operation_id'] == out['operation_id']
    assert len([r for r in Audit(daemon.fleet.config.safety)._tail() if r['phase'] == 'attempt']) == 2
    await daemon.fleet.close()


async def test_source_start_claim_arbitrates_under_registry_flock(daemon, mock, monkeypatch):
    from bat_agent_connector.confinement import ConfinementRefused
    original = registry.refuse_start_claim
    def busy(path, host, sid):
        if sid == SID:
            raise ConfinementRefused('START_IN_PROGRESS', 'source claim remains live', sent=False)
        return original(path, host, sid)
    monkeypatch.setattr(registry, 'refuse_start_claim', busy)
    before = registry.registry_path().read_bytes()
    out = await create(daemon)
    assert out['status'] == 'failed' and out['error_code'] == 'START_IN_PROGRESS', out
    assert not api.write_frames(mock) and registry.registry_path().read_bytes() == before
    await daemon.fleet.close()


async def test_other_managed_carrier_writes_are_fenced_but_new_worktree_starts_are_not(daemon, mock):
    from tests.conftest import adopt
    other = 'managed-shared-carrier'
    adopt(other, cwd='/srv/demo/.bat-worktrees/abc', worktree_path='/srv/demo/.bat-worktrees/abc', origin_cwd='/srv/demo', branch='bat/worktree-abc')
    mock.metas[other] = None
    out = await create(daemon)
    assert out['status'] == 'succeeded', out
    grant = resource_policy.WriteGrant('h1', 'session.send', other, frozenset({'claude:send-message'}),
        '/srv/demo/.bat-worktrees/abc', controller_authority=registry.get('h1', other)['controller_authority'],
        controller_incarnation=registry.get('h1', other)['controller_incarnation'])
    with pytest.raises(ResourceReadOnly, match='FAILOVER_SHARED_CARRIER'):
        resource_policy.check_grant(grant, 'h1', 'claude:send-message', {'sessionId': other})
    own = resource_policy.authorize_new_session(daemon.fleet.config.host('h1'), 'independent', folder='/srv/demo', use_worktree=True)
    registry.reserve('h1', {'session_id': 'independent', 'cwd': '/srv/demo'}, 32)
    resource_policy.check_grant(own, 'h1', 'worktree:create', {'sessionId': 'independent'})
    await daemon.fleet.close()


@pytest.mark.parametrize('receipt_only', [False, True])
async def test_receipt_only_local_step_does_not_change_default_cancel_semantics(daemon, receipt_only):
    from bat_agent_connector.operations import Cancelled, OpContext
    op, _ = daemon.ops.create(P, **intent())
    daemon.ops.db.execute('UPDATE operations SET cancel_requested=1 WHERE operation_id=?', (op['operation_id'],))
    ctx = OpContext(daemon.ops, daemon.ops.get(op['operation_id']))
    calls = []
    async def local():
        calls.append('local-only')
        return {'recorded': True}
    if receipt_only:
        assert await ctx.step('local-only', local, receipt_only=True) == {'recorded': True}
        assert calls == ['local-only']
    else:
        with pytest.raises(Cancelled):
            await ctx.step('local-only', local)
        assert not calls


async def test_managed_root_failover_does_not_block_a_fresh_independent_worktree(daemon, mock):
    from tests.conftest import adopt
    sid = api.MANUAL
    daemon.fleet.config.host('h1').orchestrate_max_sessions = 3
    adopt(sid, cwd='/srv/demo', origin_cwd='/srv/demo', workspace_id='ws-1', agent_preset='claude-code')
    mock.ws_doc['terminals'] = [t for t in mock.ws_doc['terminals'] if t['id'] == sid]
    mock.metas[sid]['isStreaming'] = False
    mock.states[sid]['isStreaming'] = False
    out = await create(daemon, target={'host': 'h1', 'session_id': sid}, params={'force': True})
    assert out['status'] == 'succeeded' and out['result']['same_worktree'] is False, (out['status_reason'], out['steps'])
    grant = resource_policy.authorize_new_session(daemon.fleet.config.host('h1'), 'new-worktree', folder='/srv/demo', use_worktree=True)
    registry.reserve('h1', {'session_id': 'new-worktree', 'cwd': '/srv/demo'}, 32)
    resource_policy.check_grant(grant, 'h1', 'worktree:create', {'sessionId': 'new-worktree'})
    shared = resource_policy.authorize_new_session(daemon.fleet.config.host('h1'), 'new-shared', folder='/srv/demo', use_worktree=False)
    registry.reserve('h1', {'session_id': 'new-shared', 'cwd': '/srv/demo'}, 32)
    with pytest.raises(ResourceReadOnly, match='FAILOVER_SHARED_CARRIER'):
        resource_policy.check_grant(shared, 'h1', 'claude:start-session', {'sessionId': 'new-shared'})
    await daemon.fleet.close()


@pytest.mark.parametrize('changed', ['successor_owner', 'successor_permissions', 'source_owner'])
async def test_proven_unsent_rollback_never_overwrites_newer_registry_binding(daemon, mock, monkeypatch, changed):
    from bat_agent_connector import confinement
    original = confinement.guard_start_frame
    newer = None
    async def moved(*args, **kwargs):
        nonlocal newer
        await original(*args, **kwargs)
        successor = next(r for r in registry.list_entries('h1') if r.get('failover_of') == SID)
        if changed == 'successor_owner':
            registry.update('h1', successor['session_id'], task_id='new-task', role='lead')
        elif changed == 'successor_permissions':
            registry.update('h1', successor['session_id'], agent_params={'sandboxMode': 'workspace-write', 'approvalPolicy': 'on-request'})
        else:
            registry.update('h1', SID, task_id='new-owner', role='lead')
        newer = {'source': registry.get('h1', SID), 'successor': registry.get('h1', successor['session_id'])}
    monkeypatch.setattr(confinement, 'guard_start_frame', moved)
    out = await create(daemon)
    assert out['status'] == 'failed' and not api.write_frames(mock), out
    successor = registry.get('h1', newer['successor']['session_id'])
    assert successor['status'] == 'starting' and successor['start_sent'] is False
    assert successor['failover_fence'] == newer['successor']['failover_fence']
    assert successor.get('task_id') == newer['successor'].get('task_id')
    for key in ('status', 'task_id', 'role', 'failover_fence', 'superseded_by'):
        assert registry.get('h1', SID).get(key) == newer['source'].get(key)
    for key in ('role', 'agent_params', 'cwd'):
        assert successor.get(key) == newer['successor'].get(key)
    await daemon.fleet.close()


@pytest.mark.parametrize('change', ['task_owner', 'journal_owner', 'path_owner', 'registry_binding', 'new_consumer'])
@pytest.mark.parametrize('boundary', ['last_read', 'before_send'])
async def test_final_frame_rechecks_every_shared_consumer_and_detects_new_consumers(daemon, mock, monkeypatch, change, boundary):
    from tests.conftest import adopt
    other = 'managed-shared-carrier'
    adopt(other, cwd='/srv/demo/.bat-worktrees/abc', worktree_path='/srv/demo/.bat-worktrees/abc', origin_cwd='/srv/demo', branch='bat/worktree-abc')
    mock.metas[other] = None
    daemon.fleet.config.host('h1').orchestrate_max_sessions = 3
    client = daemon.fleet.client('h1')
    changed = False
    def mutate():
        nonlocal changed
        if changed:
            return
        changed = True
        if change in {'task_owner', 'journal_owner', 'path_owner'}:
            task = daemon.journal.submit(project='p', host='h1', workspace='demo-project', original_words='new owner', idempotency_key='last-read-owner')
            if change == 'path_owner':
                daemon.journal.db.execute('UPDATE tasks SET external_worktree_path=?,paused=1 WHERE task_id=?', ('/srv/demo/.bat-worktrees/abc', task['task_id']))
            else:
                daemon.journal.db.execute('UPDATE tasks SET session_id=?,paused=1 WHERE task_id=?', (other, task['task_id']))
            if change == 'task_owner':
                registry.update('h1', other, task_id=task['task_id'], role='lead')
        elif change == 'registry_binding':
            registry.update('h1', other, role='changed')
        else:
            adopt('new-carrier-consumer', cwd='/srv/demo/.bat-worktrees/abc', worktree_path='/srv/demo/.bat-worktrees/abc', origin_cwd='/srv/demo', branch='bat/worktree-abc')
            mock.metas['new-carrier-consumer'] = None
    if boundary == 'last_read':
        original = client.guard_read
        async def read(channel, params):
            result = await original(channel, params)
            if channel == 'claude:get-session-state' and params['sessionId'] == SID:
                mutate()
            return result
        monkeypatch.setattr(client, 'guard_read', read)
    else:
        original = client.invoke
        async def invoke(channel, *args, **kwargs):
            if channel == 'claude:start-session':
                prior = kwargs['before_send']
                def gate():
                    mutate()
                    prior()
                kwargs['before_send'] = gate
            return await original(channel, *args, **kwargs)
        monkeypatch.setattr(client, 'invoke', invoke)
    out = await create(daemon)
    assert changed and out['status'] == 'failed', out
    assert not api.write_frames(mock)
    assert registry.get('h1', SID)['status'] == 'active'
    await daemon.fleet.close()
