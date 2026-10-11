"""A10 / §06, §12 and v2 A06: unsent starts keep carrier metadata consistent with rollback."""
from __future__ import annotations

import asyncio

import pytest

from bat_agent_connector import confinement, orchestrate, registry, task_bat
from bat_agent_connector.errors import ConnectionLost, InvokeError, WriteRefused
from bat_agent_connector.task_core import TaskCoordinator
from tests.test_confinement import MANAGED


def refuse_before_frame(monkeypatch, failure='refused'):
    enabled = [True]

    async def refuse(*args, **kwargs):
        if enabled[0]:
            if failure == 'refused':
                raise confinement.ConfinementRefused('HOST_ACCOUNT_UNVERIFIED', 'fixture preframe', sent=False)
            raise ConnectionLost('fixture before transport')

    monkeypatch.setattr(confinement, 'guard_start_frame', refuse)
    return enabled


def create_unique_carriers(mock):
    calls = []

    def create(params):
        mock.handlers.pop('worktree:create')
        try:
            result = mock.dispatch('worktree:create', params)
        finally:
            mock.handlers['worktree:create'] = create
        count = len(calls) + 1
        result.update(worktreePath=params['cwd'] + f'/.bat-worktrees/{count:08x}',
                      branchName=f'bat/worktree-{count:08x}')
        mock.worktrees[params['sessionId']].update(result)
        mock.git_branch[result['worktreePath']] = result['branchName']
        calls.append(result)
        return result

    mock.handlers['worktree:create'] = create
    return calls


@pytest.mark.parametrize('failure', ['refused', 'bat_error'])
async def test_a10_unsent_rollback_clears_carrier_and_same_id_creates_once(
        fleet_factory, mock, monkeypatch, failure):
    fleet = fleet_factory(writes=True, orchestrate=True, safety={'write_min_interval_s': 0}, **MANAGED)
    enabled = refuse_before_frame(monkeypatch, failure)
    creates = create_unique_carriers(mock)
    try:
        with pytest.raises((confinement.ConfinementRefused, ConnectionLost)):
            await orchestrate.session_start(fleet, 'h1', 'demo-project', confirm=True, session_id='retry-unsent')
        row = registry.get('h1', 'retry-unsent')
        assert row['status'] == 'failed' and row['start_sent'] is False
        assert row['cwd'] == row['origin_cwd'] == '/srv/demo'
        assert row['worktree_path'] is None and row['branch'] is None
        assert row['worktree_rolled_back'] is True and row['rolled_back_worktree_path'] == creates[0]['worktreePath']
        assert row['rolled_back_branch'] == creates[0]['branchName']
        assert mock.channels().count('worktree:remove') == 1 and 'claude:start-session' not in mock.channels()
        enabled[0] = False
        result = await orchestrate.session_start(fleet, 'h1', 'demo-project', confirm=True, session_id='retry-unsent')
        row = registry.get('h1', result['session_id'])
        assert row['status'] == 'active' and row['worktree_path'] == creates[1]['worktreePath']
        assert creates[0]['worktreePath'] != creates[1]['worktreePath']
        assert len(registry.list_entries()) == mock.channels().count('claude:start-session') == 1
        assert mock.channels().count('worktree:create') == 2 and 'worktree:status' not in mock.channels()
    finally:
        await fleet.close()


@pytest.mark.parametrize('removal', ['error', 'cancelled', 'unconfirmed'])
@pytest.mark.parametrize('identity', ['matching', 'missing'])
async def test_a10_unconfirmed_rollback_keeps_carrier_and_retry_proves_or_refuses(
        fleet_factory, mock, monkeypatch, removal, identity):
    fleet = fleet_factory(writes=True, orchestrate=True, safety={'write_min_interval_s': 0}, **MANAGED)
    enabled = refuse_before_frame(monkeypatch)
    creates = create_unique_carriers(mock)
    client = fleet.client('h1')
    invoke = client.invoke

    async def remove_failure(channel, params=None, **kwargs):
        if channel == 'worktree:remove':
            if removal == 'cancelled':
                raise asyncio.CancelledError
            if removal == 'unconfirmed':
                return {'success': False}
            raise InvokeError('fixture remove failed')
        return await invoke(channel, params, **kwargs)

    monkeypatch.setattr(client, 'invoke', remove_failure)
    try:
        with pytest.raises((InvokeError, asyncio.CancelledError, WriteRefused)):
            await orchestrate.session_start(fleet, 'h1', 'demo-project', confirm=True, session_id='retry-retained')
        row = registry.get('h1', 'retry-retained')
        assert row['status'] == 'failed' and row['start_sent'] is False
        assert row['cwd'] == row['worktree_path'] == creates[0]['worktreePath']
        assert row['branch'] == creates[0]['branchName'] and not row.get('worktree_rolled_back')
        enabled[0] = False
        if identity == 'missing':
            mock.worktrees.pop('retry-retained')
            # Repeated failed recovery must not lose the retained carrier and later create blindly.
            for _ in range(2):
                with pytest.raises(WriteRefused, match='worktree identity is unavailable'):
                    await orchestrate.session_start(fleet, 'h1', 'demo-project', confirm=True, session_id='retry-retained')
                assert registry.get('h1', 'retry-retained')['worktree_path'] == creates[0]['worktreePath']
            assert 'claude:start-session' not in mock.channels()
        else:
            await orchestrate.session_start(fleet, 'h1', 'demo-project', confirm=True, session_id='retry-retained')
            assert registry.get('h1', 'retry-retained')['status'] == 'active'
            assert mock.channels().count('claude:start-session') == 1
        assert len(creates) == len(registry.list_entries()) == 1
        assert 'worktree:status' in mock.channels()
    finally:
        await fleet.close()


@pytest.mark.parametrize('readback', ['present', 'error', 'cancelled'])
async def test_a10_successful_rollback_reply_needs_carrier_absence_readback(
        fleet_factory, mock, monkeypatch, readback):
    fleet = fleet_factory(writes=True, orchestrate=True, safety={'write_min_interval_s': 0}, **MANAGED)
    enabled = refuse_before_frame(monkeypatch)
    creates = create_unique_carriers(mock)
    client = fleet.client('h1')
    invoke = client.invoke
    removed = False

    async def no_op_remove(channel, params=None, **kwargs):
        nonlocal removed
        if channel == 'worktree:remove':
            # BAT lost its in-memory mapping across a restart. The real carrier
            # remains, but the untracked remove channel still returns success.
            mock.worktrees.pop(params['sessionId'])
            removed = True
            return {'success': True}
        if removed and channel == 'git:getRoot' and params['cwd'] == creates[0]['worktreePath']:
            if readback == 'error':
                raise ConnectionLost('fixture rollback read-back unavailable')
            if readback == 'cancelled':
                raise asyncio.CancelledError
        return await invoke(channel, params, **kwargs)

    monkeypatch.setattr(client, 'invoke', no_op_remove)
    try:
        with pytest.raises((WriteRefused, ConnectionLost, asyncio.CancelledError)):
            await orchestrate.session_start(fleet, 'h1', 'demo-project', confirm=True, session_id='rollback-noop')
        row = registry.get('h1', 'rollback-noop')
        assert row['status'] == 'failed' and row['start_sent'] is False
        assert row['worktree_path'] == row['cwd'] == creates[0]['worktreePath']
        assert row['branch'] == creates[0]['branchName'] and not row.get('worktree_rolled_back')
        enabled[0] = False
        for _ in range(2):
            with pytest.raises(WriteRefused, match='worktree identity is unavailable'):
                await orchestrate.session_start(fleet, 'h1', 'demo-project', confirm=True, session_id='rollback-noop')
            assert registry.get('h1', 'rollback-noop')['worktree_path'] == creates[0]['worktreePath']
        assert len(creates) == 1 and 'claude:start-session' not in mock.channels()
    finally:
        await fleet.close()


async def test_a10_task_lead_recover_start_recreates_a_confirmed_rollback_under_same_id(
        fleet_factory, mock, controller_context, monkeypatch):
    fleet = fleet_factory(writes=True, orchestrate=True, safety={'write_min_interval_s': 0}, **MANAGED)
    journal, _ = controller_context
    task = journal.submit(project='p', host='h1', workspace='demo-project', original_words='fixture task',
                          idempotency_key='rollback-task')
    adapter = task_bat.BatTaskAdapter(fleet, journal=journal)
    enabled = refuse_before_frame(monkeypatch)
    creates = create_unique_carriers(mock)
    start = orchestrate.session_start

    async def available(_task):
        return frozenset({'codex'})

    async def non_retained(*args, **kwargs):
        # The normal adapter retains its worktree. Exercise recovery of a journal-owned
        # same-ID row produced by a caller that used the supported rollback policy.
        return await start(*args, **{**kwargs, 'retain_on_error': False})

    monkeypatch.setattr(adapter, 'available_agents', available)
    monkeypatch.setattr(orchestrate, 'session_start', non_retained)
    try:
        result = await TaskCoordinator(journal, adapter)._start(task, role='lead')
        command = next(c for c in journal.commands(task['task_id']) if c['kind'] == 'start_lead')
        sid = command['session_id']
        assert result['state'] == 'needs_ted' and command['status'] == 'rejected'
        assert registry.get('h1', sid)['worktree_path'] is None and not mock.worktrees
        enabled[0] = False
        monkeypatch.setattr(orchestrate, 'session_start', start)
        # The refusal requires operator attention; resume through a valid task transition
        # before recovering the original start intent, without changing engine policy.
        task = journal.change(task['task_id'], 'accepted', fields={'result': None}, event='fixture_resume')
        assert await adapter.recover_start(task, role='lead', session_id=sid)
        row = registry.get('h1', sid)
        assert row['status'] == 'active' and row['start_sent'] is True
        assert row['worktree_path'] == creates[1]['worktreePath']
        assert len(registry.list_entries()) == mock.channels().count('claude:start-session') == 1
        assert len(creates) == 2
    finally:
        await fleet.close()
