"""Same-BAT continuation uses original disk context and a new owned session."""
from __future__ import annotations

import copy
import json
from dataclasses import replace

import pytest

from bat_agent_connector import pm_bat_operations as bat
from bat_agent_connector import pm_lifecycle_gates, pm_runs, pm_workspace, registry, repository_sync
from bat_agent_connector.api_auth import Principal
from bat_agent_connector.config import parse_config
from bat_agent_connector.operations import OperationError
from bat_agent_connector.task_daemon import TaskDaemon
from tests.operation_helpers import settle_operations
from tests.test_checkpoints import git, snapshot
from tests.test_pm_bat_operations import OWNER, bat_world  # noqa: F401
from tests.test_pm_discussions import act, read
from tests.test_pm_worktree import Runner
from tests.test_repository_sync import REQUEST
from tests.test_repository_sync import world as repository_world

world = repository_world
ORIGINAL_BINDING = bat._binding
ORIGINAL_CARRIER = bat.start_carrier
WRITER = Principal('continuing-pm-user', frozenset({'observe', 'manage', 'start'}))


@pytest.fixture
def daemon(world):
    return world['d']


async def run(ops, action, target=None, params=None, pre=None, *, who=OWNER, key=None):
    op, _ = ops.create(who, action=action, target=target or {}, params=params or {},
        preconditions=pre or {}, idempotency_key=key or action + ':' + str(len(ops.list()['operations'])))
    await settle_operations(ops)
    return ops.get(op['operation_id'])


@pytest.fixture
async def continuation(world, bat_world, mock, monkeypatch):  # noqa: F811
    w, (ops, settings, proofs, events) = world, bat_world
    # Use the production PM binding and a real PM Git carrier; only native
    # provider responses remain fake. No fixture-only writer/action is installed.
    monkeypatch.setattr(bat, '_binding', ORIGINAL_BINDING)
    monkeypatch.setattr(bat, 'start_carrier', ORIGINAL_CARRIER)
    mock.handlers.pop('git:log', None)
    w['raw']['hosts']['h1']['shared_clone_worktrees'] = False
    w['raw']['hosts']['h1']['orchestrate_max_sessions'] = 1
    w['d'].fleet.config.hosts['h1'] = replace(w['d'].fleet.config.host('h1'),
        shared_clone_worktrees=False, orchestrate_max_sessions=1)
    w['runner'] = Runner()
    ops.context['git_runner'] = w['runner']
    created = await run(ops, 'pm.workspace.create', params={'name': 'Durable PM', 'repositories': ['o/r']})
    assert created['status'] == 'succeeded', created
    wid = created['result']['workspace']['workspace_id']
    joined = await run(ops, 'pm.workspace.member.set', {'workspace_id': wid},
        {'actor': WRITER.actor, 'role': 'contributor'}, {'expected_version': 1})
    assert joined['status'] == 'succeeded', joined
    opened = await run(ops, 'pm.thread.create', {'workspace_id': wid}, {'title': 'Original discussion'},
        {'expected_version': joined['result']['workspace']['version']})
    target = {'workspace_id': wid, 'thread_id': opened['result']['thread_id']}
    await act(ops, target, 'pm.message.append', {'content': 'Keep the original password-reset requirement.'})
    pv = await repository_sync.source_preview(ops, REQUEST)
    selection = {**pv['target'], **{k: pv[k] for k in ('source_ref', 'source_sha', 'repository_id', 'binding_digest')}}
    prepared = await run(ops, 'pm.run.prepare', target,
        {'purpose': 'Persist original PM context', 'repository_binding': selection},
        {'expected_version': read(ops, target)['thread']['version']})
    assert prepared['status'] == 'succeeded', prepared
    w.update(target=target, settings=settings, proofs=proofs, events=events,
        binding=pm_runs.workspace_worktree(ops.db, wid))
    return w


async def analyze(w, key):
    ops, target = w['d'].ops, w['target']
    return await run(ops, bat.ACTION, target, {'purpose': 'Continue only the saved discussion'},
        {'expected_version': read(ops, target)['thread']['version']}, who=WRITER, key=key)


async def restart(w):
    old = w['d']
    path = old.journal.path
    await old.fleet.close()
    await old.inventory.close()
    old.journal.close()
    w['d'] = TaskDaemon(parse_config(w['raw']), path)
    w['d'].ops.context['git_runner'] = w['runner']


async def test_completed_disk_context_continues_in_new_owned_bat_session(continuation, mock):
    w = continuation
    human = snapshot(w['human'])
    first = await analyze(w, 'first-original')
    assert first['status'] == 'succeeded', json.dumps(first, indent=2)
    original = copy.deepcopy(pm_runs.get_by_operation(w['d'].ops.db, first['operation_id']))
    old_sid = original['session_binding']['session_id']
    old_registry = copy.deepcopy(registry.get('h1', old_sid))
    assert old_registry['status'] == 'stopped'
    assert old_registry['retirement']['operation_id'] == first['operation_id']
    old_proof = copy.deepcopy(w['proofs'][old_sid])
    await restart(w)
    await act(w['d'].ops, w['target'], 'pm.message.append', {'content': 'Discuss recovery without adding referral scope.'})
    second = await analyze(w, 'second-new-session')
    assert second['status'] == 'succeeded', json.dumps(second, indent=2)
    successor = pm_runs.get_by_operation(w['d'].ops.db, second['operation_id'])
    assert successor['generation'] == original['generation'] + 1
    assert successor['run_id'] != original['run_id']
    assert successor['session_binding']['session_id'] != old_sid
    assert successor['session_binding']['sdk_session_id'] != original['session_binding']['sdk_session_id']
    prompt = next(v['data']['prompt'] for v in successor['receipts'] if v['phase'] == 'prepare')
    assert 'Keep the original password-reset requirement.' in prompt
    assert 'Discuss the original evidence.' in prompt
    assert 'Discuss recovery without adding referral scope.' in prompt
    assert pm_runs.get_by_operation(w['d'].ops.db, first['operation_id']) == original
    assert registry.get('h1', old_sid) == old_registry and w['proofs'][old_sid] == old_proof
    assert pm_runs.workspace_worktree(w['d'].ops.db, w['target']['workspace_id']) == w['binding']
    assert snapshot(w['human']) == human
    assert 'worktree:create' not in mock.channels()
    assert all(item['params']['options']['cwd'] == w['binding']['worktree_path']
        for item in mock.invokes if item['channel'] == 'claude:start-session')
    assert mock.channels().count('claude:start-session') == mock.channels().count('claude:send-message') == 2
    for value in w['proofs'].values():
        assert value['pmRuntime']['attestation']['environmentAccess'] is False
        assert value['pmRuntime']['attestation']['externalToolsDisabled'] is True
    assert w['d'].ops.db.execute('SELECT COUNT(*) FROM tasks').fetchone()[0] == 0


async def test_unknown_original_death_blocks_second_writer_after_restart(continuation, mock):
    w = continuation
    w['settings']['missing_exit'] = True
    first = await analyze(w, 'unknown-first')
    assert first['status'] == 'uncertain', first
    await restart(w)
    blocked = await analyze(w, 'blocked-next')
    assert blocked['status'] == 'failed' and blocked['error_code'] == 'PM_WRITER_BUSY', blocked
    assert mock.channels().count('claude:start-session') == 1
    proof = next(iter(w['proofs'].values()))['pmRuntime']
    proof['exit'] = {'confirmedDead': True, 'exitCode': 0, 'signal': None, 'action': 'native_terminal', 'process': proof['process'].copy()}
    w['settings']['missing_exit'] = False
    ops = w['d'].ops
    ops.db.execute('UPDATE operations SET next_run_at=0 WHERE operation_id=?', (first['operation_id'],))
    await settle_operations(ops)
    assert ops.get(first['operation_id'])['status'] == 'succeeded'
    completed = await analyze(w, 'fresh-after-original-reconciled')
    assert completed['status'] == 'succeeded', completed
    assert mock.channels().count('claude:start-session') == mock.channels().count('claude:send-message') == 2


@pytest.mark.parametrize('changed', ['source', 'pause', 'membership'])
async def test_new_session_requires_current_original_source_lifecycle_and_member(continuation, mock, changed):
    w = continuation
    first = await analyze(w, 'original-completed')
    assert first['status'] == 'succeeded', json.dumps(first, indent=2)
    if changed == 'source':
        w['raw']['github']['repos'][0]['sync']['bindings'][0]['workspace_id'] = 'other-workspace'
    await restart(w)
    ops, target = w['d'].ops, w['target']
    if changed == 'pause':
        head = pm_lifecycle_gates.head(ops.db, target['workspace_id'])
        paused = await run(ops, 'pm.lifecycle.pause', {'workspace_id': target['workspace_id']},
            {'reason': 'Stop new PM investment'}, {'expected_lifecycle_revision': head['revision']})
        assert paused['status'] == 'succeeded', paused
    elif changed == 'membership':
        current = pm_workspace.get_workspace(ops.db, OWNER, target['workspace_id'])
        moved = await run(ops, 'pm.workspace.member.set', {'workspace_id': target['workspace_id']},
            {'actor': WRITER.actor, 'role': 'reader'}, {'expected_version': current['version']})
        assert moved['status'] == 'succeeded', moved
    try:
        result = await analyze(w, 'refused-continuation')
    except OperationError:
        pass
    else:
        assert result['status'] == 'failed', json.dumps(result, indent=2)
    assert mock.channels().count('claude:start-session') == mock.channels().count('claude:send-message') == 1


@pytest.mark.parametrize('change', ['head', 'workspace_folder'])
async def test_final_start_frame_rechecks_original_pm_carrier(continuation, mock, monkeypatch, change):
    w = continuation
    client = w['d'].fleet.client('h1')
    original = client.invoke
    altered = False
    async def race(channel, *args, **kwargs):
        nonlocal altered
        if channel == 'claude:start-session' and not altered:
            altered = True
            if change == 'head':
                git(w['binding']['worktree_path'], '-c', 'user.name=fixture', '-c', 'user.email=fixture@example.invalid',
                    'commit', '--allow-empty', '-qm', 'Unrelated carrier change')
            else:
                mock.ws_doc['workspaces'][0]['folderPath'] += '-rebound'
        return await original(channel, *args, **kwargs)
    monkeypatch.setattr(client, 'invoke', race)
    out = await analyze(w, 'final-frame-race')
    assert altered and out['status'] != 'succeeded', out
    assert 'claude:start-session' not in mock.channels()
    assert 'claude:send-message' not in mock.channels()
    assert 'worktree:create' not in mock.channels()
