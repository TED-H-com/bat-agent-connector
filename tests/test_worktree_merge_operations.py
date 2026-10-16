"""Real central scheduler and MockBat frames; checked Git reads use temporary local repositories."""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from bat_agent_connector import api_auth, checkpoints, registry, resource_policy, service
from bat_agent_connector import worktree_merge_host as helper
from bat_agent_connector import worktree_merge_operations as merge
from bat_agent_connector.errors import InvokeTimeout, ResourceReadOnly
from bat_agent_connector.operations import OperationError, OperationService
from bat_agent_connector.task_journal import Journal
from tests import test_api_v1 as api
from tests.conftest import adopt
from tests.operation_helpers import settle_operations

daemon, served = api.daemon, api.served
P = api_auth.Principal('operator-dashboard', frozenset({'observe', 'integrate'}))
SID = 'merge-managed-0001'


def git(path, *args):
    return subprocess.run(['git', '-C', str(path), *args], capture_output=True, text=True, check=True).stdout.strip()


class LocalRunner:
    def available(self, host):
        return host == 'h1'

    async def run(self, host, script, timeout_s):
        return await checkpoints._run(('sh', '-c', script), timeout_s)


@pytest.fixture
def host_git_context(tmp_path, monkeypatch):
    """A synthetic BAT/SSH account, with real Git and no CI/user config inheritance.

    Hosted runners install system Git LFS filters. Production must refuse those;
    positive fixtures instead need their own account config. Only Git's default
    config locations change here, never its argv, exit/stderr or sanitization.
    """
    real_git = shutil.which('git')
    assert real_git
    for key in list(os.environ):
        if key.startswith('GIT_'):
            monkeypatch.delenv(key)
    context = tmp_path / 'host-git-context'
    (context / 'git').mkdir(parents=True)
    system = context / 'system.config'
    global_config = context / 'git' / 'config'
    system.write_text('')
    global_config.write_text('')
    monkeypatch.setenv('XDG_CONFIG_HOME', str(context))
    # /dev/shm basetemps may be noexec. Only this disposable launcher uses /tmp;
    # config and repositories still live in the isolated pytest fixture directory.
    with tempfile.TemporaryDirectory(prefix='bac-merge-git-', dir='/tmp') as launchers:
        launcher = Path(launchers) / 'git'
        launcher.write_text(
            f'#!{sys.executable}\n'
            'import os, sys\n'
            f'os.environ.setdefault("GIT_CONFIG_SYSTEM", {str(system)!r})\n'
            'os.environ.setdefault("GIT_CONFIG_GLOBAL", os.path.join(os.environ["XDG_CONFIG_HOME"], "git", "config"))\n'
            f'os.execv({real_git!r}, [{real_git!r}, *sys.argv[1:]])\n')
        launcher.chmod(0o700)
        monkeypatch.setenv('PATH', launchers + os.pathsep + os.environ['PATH'])
        yield {'system': system, 'global': global_config}


@pytest.fixture
def carriers(daemon, mock, tmp_path, host_git_context):
    origin, source = tmp_path / 'clone', tmp_path / 'worktree'
    origin.mkdir()
    git(origin, 'init', '--template=', '-b', 'main')
    git(origin, 'config', 'user.name', 'Fixture')
    git(origin, 'config', 'user.email', 'fixture@example.invalid')
    (origin / 'base').write_text('base')
    git(origin, 'add', '.')
    git(origin, 'commit', '-m', 'base')
    git(origin, 'worktree', 'add', '-b', 'bat/feature', str(source))
    (source / 'feature').write_text('fixed result')
    git(source, 'add', '.')
    git(source, 'commit', '-m', 'feature')
    daemon.fleet.config.host('h1').managed_roots = [str(tmp_path)]
    daemon.ops.context['git_runner'] = LocalRunner()
    mock.ws_doc['workspaces'].append({'id': 'ws-merge', 'name': 'merge-fixture', 'folderPath': str(origin)})
    mock.ws_doc['terminals'].append({'id': SID, 'workspaceId': 'ws-merge', 'agentPreset': 'claude-code-worktree',
                                   'cwd': str(source), 'worktreePath': str(source), 'worktreeBranch': 'bat/feature'})
    mock.metas[SID] = {'cwd': str(source), 'isStreaming': False, 'sdkSessionId': 'fixed-runtime'}
    mock.states[SID] = {'isStreaming': False, 'messages': []}
    mock.worktrees[SID] = {'worktreePath': str(source), 'branchName': 'bat/feature', 'sourceBranch': 'main',
                           'mergedKind': 'ahead', 'merged': False}
    mock.handlers['git:getRoot'] = lambda p: git(p['cwd'], 'rev-parse', '--show-toplevel')
    mock.handlers['git:branch'] = lambda p: git(p['cwd'], 'branch', '--show-current')
    mock.handlers['git:log'] = lambda p: [{'hash': git(p['cwd'], 'rev-parse', 'HEAD')}]
    mock.handlers['git:status'] = lambda p: []  # deliberately cannot prove successful Git execution
    def perform(p):
        git(origin, 'merge', '--no-ff', '--no-edit', 'bat/feature')
        return {'success': True, 'strategy': 'merge', 'branchName': 'bat/feature', 'sourceBranch': 'main'}
    mock.handlers['worktree:merge'] = perform
    adopt(SID, cwd=str(source), origin_cwd=str(origin), worktree_path=str(source), branch='bat/feature',
          agent_preset='claude-code-worktree', workspace_id='ws-merge')
    return origin, source


def intent(**kw):
    return {'action': 'worktree.merge', 'target': {'host': 'h1', 'session_id': SID}, 'params': {}, 'idempotency_key': 'merge-key', **kw}


async def create(d, **kw):
    op, _ = d.ops.create(P, **intent(**kw))
    await settle_operations(d.ops)
    return d.ops.get(op['operation_id'])


def reopen(d):
    d.journal.close()
    d.journal = Journal(d.journal.path)
    ops = OperationService(d.journal, actions=list(d.ops.actions.values()))
    ops.context.update(d.ops.context)
    d.ops = ops
    d.coordinator.journal = d.adapter.journal = d.journal
    d.journal.on_close = d.release_owner
    d.journal.owner_valid = d._owns_fleet
    d.acquire_owner()


async def test_merge_exact_receipts_real_git_and_named_replay(daemon, mock, carriers):
    origin, source = carriers
    original_head = git(origin, 'rev-parse', 'HEAD')
    out = await create(daemon, target={'host': 'h1', 'session_id': 'merge-managed'})
    assert out['status'] == 'succeeded', out
    assert out['result']['merged_now'] is True and out['result']['result']['strategy'] == 'merge'
    assert git(origin, 'rev-parse', 'HEAD') != original_head
    assert (origin / 'feature').read_bytes() == (source / 'feature').read_bytes()
    assert not merge._document().get('carrier_writers')
    assert [s['name'] for s in out['steps']] == [
        'merge.prepare', 'source.identity', 'merge.reserve', 'merge.frame', 'merge.release']
    daemon.fleet.config.host('h1').orchestrate = False
    daemon.ops.context['git_runner'] = None
    replay = await create(daemon, target={'host': 'h1', 'session_id': 'merge-managed'})
    assert replay['operation_id'] == out['operation_id'] and mock.channels().count('worktree:merge') == 1
    with pytest.raises(OperationError, match='TIER_DISABLED'):
        await create(daemon, idempotency_key='new')
    await daemon.fleet.close()


@pytest.mark.parametrize('failure', ['exit', 'timeout', 'dirty', 'root', 'unavailable'])
async def test_failed_positive_git_proof_never_uses_bat_empty_status(daemon, mock, carriers, monkeypatch, failure):
    origin, source = carriers
    if failure == 'dirty':
        (source / 'uncommitted').write_text('do not merge')
    elif failure == 'root':
        git(source, 'config', 'core.worktree', str(origin / 'wrong'))
    elif failure == 'unavailable':
        daemon.ops.context['git_runner'] = None
    else:
        async def failed(*args, **kwargs):
            if failure == 'timeout':
                raise asyncio.TimeoutError()
            return json.dumps({'ok': False, 'code': 'MERGE_GIT_UNAVAILABLE'})
        monkeypatch.setattr(daemon.ops.context['git_runner'], 'run', failed)
    if failure == 'unavailable':
        with pytest.raises(OperationError, match='MERGE_GIT_UNAVAILABLE'):
            await create(daemon)
    else:
        out = await create(daemon)
        assert out['status'] == ('succeeded' if failure == 'dirty' else 'failed'), out
        if failure == 'dirty':
            assert out['result']['merged_now'] is False
    assert not api.write_frames(mock)
    assert not merge._document().get('carrier_writers')
    await daemon.fleet.close()


@pytest.mark.parametrize('change', ['manual', 'task', 'pending', 'streaming', 'unknown_idle', 'neighbor', 'operation'])
async def test_positive_managed_idle_and_all_consumers_required(daemon, mock, carriers, change):
    origin, source = carriers
    if change == 'manual':
        registry.registry_path().unlink()
    elif change == 'task':
        task = daemon.journal.submit(project='p', host='h1', workspace='merge-fixture', original_words='task', idempotency_key='task')
        daemon.journal.db.execute('UPDATE tasks SET session_id=?,paused=1 WHERE task_id=?', (SID, task['task_id']))
    elif change == 'pending':
        mock.states[SID]['pendingPermission'] = {'id': 'pending'}
    elif change == 'streaming':
        mock.metas[SID]['isStreaming'] = True
    elif change == 'unknown_idle':
        mock.metas[SID].pop('isStreaming')
    elif change == 'neighbor':
        mock.ws_doc['terminals'].append({'id': 'manual-neighbor', 'cwd': str(origin), 'agentPreset': 'claude-code'})
    else:
        other, _ = daemon.ops.create(api_auth.Principal(P.actor, frozenset({'operate'})), action='session.send',
                                    target={'host': 'h1', 'session_id': SID}, params={'text': 'pending'}, idempotency_key='pending')
        daemon.ops.db.execute("UPDATE operations SET status='uncertain',next_run_at=99999999999 WHERE operation_id=?", (other['operation_id'],))
    out = await create(daemon)
    assert out['status'] == 'failed', out
    assert not api.write_frames(mock)
    await daemon.fleet.close()


@pytest.mark.parametrize('change', ['task', 'registry', 'new_neighbor', 'head', 'dirty', 'owner', 'reader'])
async def test_final_awaited_reads_followed_by_synchronous_owner_gate(daemon, mock, carriers, monkeypatch, change):
    origin, source = carriers
    original = merge.proof
    count = 0
    async def alter(*args, **kwargs):
        nonlocal count
        p = await original(*args, **kwargs)
        count += 1
        if count == 5:  # final frame's last asynchronous proof, after BAT status
            if change == 'owner':
                monkeypatch.setattr(daemon.journal, 'owner_valid', lambda: False)
            elif change == 'reader':
                daemon.ops.context['git_runner'].aliases = {'h1': 'different-fixture-reader'}
            elif change == 'task':
                t = daemon.journal.submit(project='p', host='h1', workspace='merge-fixture', original_words='new', idempotency_key='new-task')
                daemon.journal.db.execute('UPDATE tasks SET session_id=?,paused=1 WHERE task_id=?', (SID, t['task_id']))
            elif change == 'registry':
                registry.update('h1', SID, role='reviewer')
            elif change == 'new_neighbor':
                # Simulate another existing process's newly durable consumer after observation.
                path = registry.registry_path()
                doc = json.loads(path.read_text())
                doc['sessions'].append({**registry.get('h1', SID), 'session_id': 'new-consumer'})
                registry._write_document(path, doc)
            elif change == 'head':
                p['source']['head'] = 'b' * 40
            else:
                p['destination']['clean'] = False
        return p
    monkeypatch.setattr(merge, 'proof', alter)
    out = await create(daemon)
    assert out['status'] in {'failed', 'needs_attention'}, out
    assert not api.write_frames(mock)
    await daemon.fleet.close()


async def test_cancel_during_final_proof_is_definitively_unsent_and_releases_carriers(daemon, mock, carriers, monkeypatch):
    original = merge.proof
    calls = 0
    async def cancel_after_proof(*a, **kw):
        nonlocal calls
        p = await original(*a, **kw)
        calls += 1
        if calls == 5:
            oid = daemon.ops.db.execute("SELECT operation_id FROM operations WHERE action='worktree.merge'").fetchone()[0]
            daemon.ops.cancel(P, oid)
        return p
    monkeypatch.setattr(merge, 'proof', cancel_after_proof)
    out = await create(daemon)
    assert out['status'] == 'cancelled', out
    assert not api.write_frames(mock) and not merge._document().get('carrier_writers')
    await daemon.fleet.close()


@pytest.mark.parametrize('lost', ['merge', 'rehydrate'])
async def test_lost_ack_cancel_reopen_retains_both_carriers_without_resend(daemon, mock, carriers, monkeypatch, lost):
    origin, source = carriers
    if lost == 'rehydrate':
        status = mock.worktrees.pop(SID)
        mock.handlers['claude:get-worktree-status'] = lambda p: status
        def rehydrate(p):
            mock.worktrees[SID] = status
            return {'success': True}
        mock.handlers['worktree:rehydrate'] = rehydrate
    client = daemon.fleet.client('h1')
    real = client.invoke
    async def lose(channel, *args, **kw):
        r = await real(channel, *args, **kw)
        if channel == 'worktree:' + lost:
            raise InvokeTimeout('fixture lost positive reply')
        return r
    monkeypatch.setattr(client, 'invoke', lose)
    out = await create(daemon)
    assert out['status'] == 'uncertain', out
    assert out['external_refs']['merge_result']['merged_now'] is None
    for path in (origin, source, origin / 'new-subdir'):
        with pytest.raises(ResourceReadOnly, match='MERGE_RESERVED'):
            merge.check_writer('h1', 'new-session', workdir=str(path))
    for binding in ({'cwd': str(origin)}, {'origin_cwd': str(origin)}):
        with pytest.raises(ResourceReadOnly, match='MERGE_RESERVED'):
            registry.reserve('h1', {'session_id': 'new-session', **binding}, 10)
    with pytest.raises(ResourceReadOnly, match='MERGE_RESERVED'):
        await service.session_send(daemon.fleet, 'h1', SID, 'later', confirm=True)
    tab = next(t for t in mock.ws_doc['terminals'] if t.get('id') == SID)
    stop = await resource_policy.authorize_session(daemon.fleet, 'h1', 'session.stop', tab)
    resource_policy.check_grant(stop, 'h1', 'claude:stop-session', {'sessionId': SID})
    writes = list(api.write_frames(mock))
    reopen(daemon)
    daemon.ops.db.execute("UPDATE operations SET status='needs_attention' WHERE operation_id=?", (out['operation_id'],))
    daemon.ops.cancel(P, out['operation_id'])
    await settle_operations(daemon.ops)
    assert daemon.ops.get(out['operation_id'])['status'] == 'uncertain'
    assert api.write_frames(mock) == writes and merge._document()['carrier_writers']
    await daemon.fleet.close()


async def test_rehydrate_and_merge_are_separate_frames_max_inflight_one(daemon, mock, carriers):
    status = mock.worktrees.pop(SID)
    mock.handlers['claude:get-worktree-status'] = lambda p: status
    def rehydrate(p):
        mock.worktrees[SID] = status
        return {'success': True}
    mock.handlers['worktree:rehydrate'] = rehydrate
    daemon.fleet.client('h1')._sem = asyncio.Semaphore(1)
    out = await create(daemon)
    assert out['status'] == 'succeeded', out
    assert out['result']['rehydrated'] is True
    assert [f['channel'] for f in api.write_frames(mock)] == ['worktree:rehydrate', 'worktree:merge']
    assert {s['name'] for s in out['steps']} >= set(merge.REMOTE)
    await daemon.fleet.close()


async def test_all_ack_cancel_restart_only_releases_original_marker(daemon, mock, carriers, monkeypatch):
    original = merge.release
    def fail(*a, **kw):
        raise OSError('local registry write unavailable')
    monkeypatch.setattr(merge, 'release', fail)
    out = await create(daemon)
    assert out['status'] == 'uncertain', out
    writes = list(api.write_frames(mock))
    monkeypatch.setattr(merge, 'release', original)
    daemon.fleet.config.host('h1').orchestrate = False
    reopen(daemon)
    daemon.ops.db.execute("UPDATE operations SET status='needs_attention' WHERE operation_id=?", (out['operation_id'],))
    daemon.ops.cancel(P, out['operation_id'])
    await settle_operations(daemon.ops)
    assert daemon.ops.get(out['operation_id'])['status'] == 'succeeded'
    assert api.write_frames(mock) == writes and not merge._document()['carrier_writers']
    await daemon.fleet.close()


@pytest.mark.parametrize('effect', ['rehydrate', 'merge'])
@pytest.mark.parametrize('error', [OSError, sqlite3.OperationalError])
async def test_post_ack_receipt_storage_failure_stays_resumable_without_resend(daemon, mock, carriers, monkeypatch, effect, error):
    if effect == 'rehydrate':
        status = mock.worktrees.pop(SID)
        mock.handlers['claude:get-worktree-status'] = lambda p: status
        def rehydrate(_):
            mock.worktrees[SID] = status
            return {'success': True}
        mock.handlers['worktree:rehydrate'] = rehydrate
    original = daemon.ops._step_done
    def unavailable(oid, name, response, **kw):
        if name == effect + '.frame':
            raise error('fixture receipt storage unavailable')
        return original(oid, name, response, **kw)
    monkeypatch.setattr(daemon.ops, '_step_done', unavailable)
    out = await create(daemon)
    assert mock.channels().count('worktree:' + effect) == 1
    assert merge._document()['carrier_writers']
    assert out['status'] == 'needs_attention', (out['status'], out['error_code'], out['status_reason'])
    assert out['error_code'] == 'MERGE_RECEIPT_UNAVAILABLE'
    step = next(s for s in out['steps'] if s['name'] == effect + '.frame')
    stored = daemon.ops.db.execute('SELECT request FROM operation_steps WHERE operation_id=? AND name=?',
                                   (out['operation_id'], effect + '.frame')).fetchone()
    assert step['status'] == 'started' and json.loads(stored['request'])['channel'] == 'worktree:' + effect
    writes = list(api.write_frames(mock))
    reopen(daemon)
    daemon.ops.resume(P, out['operation_id'])
    await settle_operations(daemon.ops)
    assert daemon.ops.get(out['operation_id'])['status'] == 'uncertain'
    daemon.ops.cancel(P, out['operation_id'])
    await settle_operations(daemon.ops)
    assert daemon.ops.get(out['operation_id'])['status'] == 'uncertain'
    assert api.write_frames(mock) == writes and merge._document()['carrier_writers']
    await daemon.fleet.close()


async def test_committed_ack_then_storage_error_cancel_reopens_for_local_release_only(daemon, mock, carriers, monkeypatch):
    original = daemon.ops._step_done
    def after_commit(oid, name, response, **kw):
        original(oid, name, response, **kw)
        if name == 'merge.frame':
            raise sqlite3.OperationalError('fixture failure after committed receipt')
    monkeypatch.setattr(daemon.ops, '_step_done', after_commit)
    out = await create(daemon)
    assert out['status'] == 'needs_attention' and out['error_code'] == 'MERGE_RECEIPT_UNAVAILABLE'
    assert next(s for s in out['steps'] if s['name'] == 'merge.frame')['status'] == 'succeeded'
    writes = list(api.write_frames(mock))
    reopen(daemon)
    daemon.fleet.config.host('h1').orchestrate = False
    reads = list(mock.invokes)
    daemon.ops.cancel(P, out['operation_id'])
    await settle_operations(daemon.ops)
    assert daemon.ops.get(out['operation_id'])['status'] == 'succeeded'
    assert api.write_frames(mock) == writes and mock.invokes == reads
    assert not merge._document().get('carrier_writers')
    await daemon.fleet.close()


async def test_remote_intent_storage_failure_cancel_never_starts_a_new_frame(daemon, mock, carriers, monkeypatch):
    original = daemon.ops._step_start
    def fail_before_intent(oid, name, request, **kwargs):
        if name == 'merge.frame':
            raise sqlite3.OperationalError('fixture intent storage unavailable')
        return original(oid, name, request, **kwargs)
    monkeypatch.setattr(daemon.ops, '_step_start', fail_before_intent)
    out = await create(daemon)
    assert out['status'] == 'needs_attention' and not api.write_frames(mock)
    assert not any(s['name'] == 'merge.frame' for s in out['steps'])
    reopen(daemon)
    daemon.ops.cancel(P, out['operation_id'])
    await settle_operations(daemon.ops)
    assert daemon.ops.get(out['operation_id'])['status'] == 'cancelled'
    assert not api.write_frames(mock) and not merge._document().get('carrier_writers')
    await daemon.fleet.close()


@pytest.mark.parametrize('params', [{'force': True}, {'strategy': 'cherry-pick'}])
async def test_http_scope_and_malformed_inputs_before_frames(served, mock, carriers, params):
    d, port = served
    token = api.token(d, P.actor, 'observe', 'integrate')
    status, body = await api.http(port, 'POST', '/api/v1/operations', tok=token, body=intent(params=params))
    assert status == 422 and body['error']['code'] == 'INVALID_PARAMS'
    status, _ = await api.http(port, 'POST', '/api/v1/operations', tok=api.token(d, 'observe-only', 'observe'), body=intent())
    assert status == 403 and not api.write_frames(mock)
    assert d.ops.db.execute('SELECT COUNT(*) FROM operations').fetchone()[0] == 0


def test_helper_checks_status_process_failure_and_timeout(tmp_path, monkeypatch):
    # Stub only this helper's subprocess dependency, never global subprocess/Popen.
    class Failing:
        returncode = 1
        def poll(self):
            return 1
        def wait(self):
            return 1
    class ProcessModule:
        @staticmethod
        def Popen(*args, **kwargs):
            assert args[0][:5] == ['git', '--no-optional-locks', '-c', 'core.hooksPath=/dev/null', '-c']
            assert kwargs['env']['GIT_OPTIONAL_LOCKS'] == '0'
            return Failing()
    monkeypatch.setattr(helper, 'subprocess', ProcessModule)
    with pytest.raises(ValueError, match='MERGE_GIT_UNAVAILABLE'):
        helper.git(str(tmp_path), 'status', '--porcelain=v1')
    class Hanging(Failing):
        def poll(self):
            return None
        def kill(self):
            self.killed = True
    process = Hanging()
    ProcessModule.Popen = lambda *a, **kw: process
    monkeypatch.setattr(helper, 'DEADLINE', 0)
    with pytest.raises(ValueError, match='MERGE_GIT_UNAVAILABLE'):
        helper.git(str(tmp_path), 'status', '--porcelain=v1')
    assert process.killed


async def test_default_headless_session_resolves_without_inventing_tabs(daemon, mock, carriers):
    mock.ws_doc['terminals'] = [t for t in mock.ws_doc['terminals'] if t.get('id') != SID]
    before = json.dumps(mock.ws_doc)
    out = await create(daemon, target={'host': 'h1', 'session_id': 'merge-managed'})
    assert out['status'] == 'succeeded', out
    assert out['result']['session_id'] == SID and out['result']['merged_now'] is True
    assert json.dumps(mock.ws_doc) == before
    assert [f['channel'] for f in api.write_frames(mock)] == ['worktree:merge']
    await daemon.fleet.close()


@pytest.mark.parametrize('bad', ['malformed_runtime', 'changed_runtime', 'missing_preset'])
async def test_headless_requires_positive_registry_and_runtime_binding(daemon, mock, carriers, bad):
    mock.ws_doc['terminals'] = [t for t in mock.ws_doc['terminals'] if t.get('id') != SID]
    if bad == 'malformed_runtime':
        mock.metas[SID] = {}
    elif bad == 'changed_runtime':
        mock.metas[SID]['cwd'] = str(carriers[0])
    else:
        registry.update('h1', SID, agent_preset=None)
    out = await create(daemon)
    assert out['status'] == 'failed' and not api.write_frames(mock), out
    if bad == 'malformed_runtime':
        assert 'claude:get-session-state' not in mock.channels()
    await daemon.fleet.close()


@pytest.mark.parametrize('headless', [False, True])
@pytest.mark.parametrize('status', ['active', 'stopped'])
async def test_positively_unloaded_managed_carrier_can_merge(daemon, mock, carriers, headless, status):
    if headless:
        mock.ws_doc['terminals'] = [t for t in mock.ws_doc['terminals'] if t.get('id') != SID]
    del mock.metas[SID]
    registry.update('h1', SID, status=status)
    out = await create(daemon)
    assert out['status'] == 'succeeded' and out['result']['merged_now'] is True, out
    assert 'claude:get-session-state' not in mock.channels()
    assert mock.channels().count('worktree:merge') == 1
    await daemon.fleet.close()


@pytest.mark.parametrize('reload', [False, True])
async def test_unloaded_binding_survives_reopen_and_refuses_replacement(daemon, mock, carriers, monkeypatch, reload):
    del mock.metas[SID]
    original = merge.reserve
    def fail(*a, **kw):
        raise OSError('fixture interruption before reservation')
    monkeypatch.setattr(merge, 'reserve', fail)
    out = await create(daemon)
    assert out['status'] == 'uncertain' and not api.write_frames(mock), out
    monkeypatch.setattr(merge, 'reserve', original)
    if reload:
        mock.metas[SID] = {'cwd': str(carriers[1]), 'sdkSessionId': 'replacement', 'isStreaming': False}
    reopen(daemon)
    daemon.journal.owner_valid = daemon._owns_fleet
    daemon.ops.db.execute('UPDATE operations SET next_run_at=0 WHERE operation_id=?', (out['operation_id'],))
    await settle_operations(daemon.ops)
    current = daemon.ops.get(out['operation_id'])
    assert current['status'] == ('failed' if reload else 'succeeded'), current
    assert mock.channels().count('worktree:merge') == (0 if reload else 1)
    await daemon.fleet.close()


@pytest.mark.parametrize('reply', [{'type': 'invoke-result'}, {'type': 'unexpected', 'result': None},
                                  {'type': 'invoke-error', 'error': 'metadata unavailable'}])
async def test_malformed_final_metadata_does_not_prove_unloaded(daemon, mock, carriers, monkeypatch, reply):
    del mock.metas[SID]
    client = daemon.fleet.client('h1')
    roundtrip, guard_read = client._roundtrip, client.guard_read
    final_read = False
    async def fake(frame, *args, **kwargs):
        if final_read and frame['channel'] == 'claude:get-session-meta':
            return reply
        return await roundtrip(frame, *args, **kwargs)
    async def guarded(*args, **kwargs):
        nonlocal final_read
        final_read = True
        try:
            return await guard_read(*args, **kwargs)
        finally:
            final_read = False
    monkeypatch.setattr(client, '_roundtrip', fake)
    monkeypatch.setattr(client, 'guard_read', guarded)
    out = await create(daemon)
    assert out['status'] == 'failed' and not api.write_frames(mock), out
    assert not merge._document().get('carrier_writers')
    await daemon.fleet.close()


async def test_outside_linked_worktree_shared_git_directory_is_refused(daemon, mock, carriers, tmp_path):
    git(carriers[0], 'worktree', 'add', '-b', 'other', str(tmp_path / 'outside-consumer'))
    out = await create(daemon)
    assert out['status'] == 'failed' and not api.write_frames(mock), out
    await daemon.fleet.close()


@pytest.mark.parametrize('program', ['clean', 'process', 'include', 'multiline', 'submodule'])
async def test_git_read_proof_refuses_repository_filter_programs_and_submodules(daemon, mock, carriers, tmp_path, program):
    import shlex
    origin, source = carriers
    marker = tmp_path / 'filter-must-not-run'
    if program == 'submodule':
        git(source, 'update-index', '--add', '--cacheinfo', '160000,' + git(origin, 'rev-parse', 'HEAD') + ',nested')
    else:
        (source / '.gitattributes').write_text('feature filter=proof\n')
        git(source, 'add', '.gitattributes')
        git(source, 'commit', '-m', 'fixture attributes')
        # Install after the commit; only the helper's status could execute it.
        command = 'touch ' + shlex.quote(str(marker)) + '; cat'
        if program == 'include':
            config = tmp_path / 'included-filter.conf'
            config.write_text('')
            git(source, 'config', '--file', str(config), 'filter.proof.clean', command)
            git(source, 'config', 'include.path', str(config))
        else:
            git(source, 'config', 'filter.proof.' + ('clean' if program == 'multiline' else program),
                '\n' + command if program == 'multiline' else command)
        (source / 'feature').write_text('other result')  # same size as "fixed result"
    out = await create(daemon)
    assert out['status'] == 'failed' and out['error_code'] == 'MERGE_GIT_UNAVAILABLE', out
    assert not marker.exists() and not api.write_frames(mock)
    await daemon.fleet.close()


@pytest.mark.parametrize('location', ['local', 'include', 'global', 'global_include', 'worktree'])
@pytest.mark.parametrize('program', ['external', 'command', 'textconv'])
async def test_effective_git_diff_programs_refuse_before_bat_status(daemon, mock, carriers, tmp_path, monkeypatch, location, program):
    import shlex
    origin, source = carriers
    marker = tmp_path / 'diff-must-not-run'
    script = tmp_path / 'fixture-diff.sh'
    script.write_text('touch ' + shlex.quote(str(marker)) + '\nprintf fixture\n')
    key = 'diff.external' if program == 'external' else 'diff.proof.' + program
    if program != 'external':
        (origin / '.git' / 'info' / 'attributes').write_text('feature diff=proof\n')
    args = []
    if location == 'include':
        config = tmp_path / 'included-diff.conf'
        git(origin, 'config', 'include.path', str(config))
        args = ['--file', str(config)]
    elif location in {'global', 'global_include'}:
        xdg = tmp_path / 'git-context'
        (xdg / 'git').mkdir(parents=True)
        monkeypatch.setenv('XDG_CONFIG_HOME', str(xdg))
        config = xdg / 'git' / 'config'
        if location == 'global_include':
            included = tmp_path / 'included-global-diff.conf'
            git(origin, 'config', '--file', str(config), 'include.path', str(included))
            config = included
        args = ['--file', str(config)]
    elif location == 'worktree':
        git(origin, 'config', 'extensions.worktreeConfig', 'true')
        args = ['--worktree']
    git(origin, 'config', *args, key, 'sh ' + shlex.quote(str(script)))
    # Prove the pinned BAT diff argv really would execute this temporary helper.
    git(origin, 'diff', 'main...bat/feature')
    assert marker.exists()
    marker.unlink()
    status = dict(mock.worktrees[SID])
    def bat_status(_):
        git(origin, 'diff', 'main...bat/feature')
        return status
    mock.handlers['worktree:status'] = bat_status
    out = await create(daemon)
    assert out['status'] == 'failed' and out['error_code'] == 'MERGE_GIT_UNAVAILABLE', out
    assert 'worktree:status' not in mock.channels()
    assert not marker.exists() and not api.write_frames(mock)
    await daemon.fleet.close()


@pytest.mark.parametrize('scope', ['system', 'global'])
async def test_effective_host_lfs_filters_refuse_even_without_attributes(daemon, mock, carriers, host_git_context, scope):
    config = host_git_context[scope]
    git(carriers[0], 'config', '--file', str(config), 'filter.lfs.clean', 'git-lfs clean -- %f')
    git(carriers[0], 'config', '--file', str(config), 'filter.lfs.process', 'git-lfs filter-process')
    pattern = r'^filter\.lfs\.(clean|process)$'
    # The fixture must expose account filters to the ambient-context inspection,
    # but respect the helper's explicit system/global disabling for later reads.
    assert helper.git(str(carriers[0]), 'config', '--name-only', '--get-regexp', pattern,
                      config_context=True)[1].splitlines() == ['filter.lfs.clean', 'filter.lfs.process']
    assert helper.git(str(carriers[0]), 'config', '--name-only', '--get-regexp', pattern, codes=(0, 1)) == (1, '')
    out = await create(daemon)
    assert out['status'] == 'failed' and out['error_code'] == 'MERGE_GIT_UNAVAILABLE', out
    assert 'worktree:status' not in mock.channels() and not api.write_frames(mock)
    await daemon.fleet.close()


@pytest.mark.parametrize('key,value', [('GIT_EXTERNAL_DIFF', 'fixture-command'), ('GIT_DIFF_OPTS', '-u'),
                                    ('GIT_CONFIG_PARAMETERS', "'diff.external=fixture-command'"),
                                    ('GIT_CONFIG_COUNT', '0'), ('GIT_CONFIG_GLOBAL', '/fixture-config')])
async def test_ambient_git_overrides_are_not_silently_sanitized(daemon, mock, carriers, monkeypatch, key, value):
    monkeypatch.setenv(key, value)
    out = await create(daemon)
    assert out['status'] == 'failed' and out['error_code'] == 'MERGE_GIT_UNAVAILABLE', out
    assert 'worktree:status' not in mock.channels() and not api.write_frames(mock)
    await daemon.fleet.close()


async def test_new_diff_program_before_final_frame_refuses_before_another_status(daemon, mock, carriers, tmp_path, monkeypatch):
    import shlex
    marker = tmp_path / 'late-diff-must-not-run'
    original = merge.proof
    calls, status_count = 0, None
    async def add_program(*a, **kw):
        nonlocal calls, status_count
        result = await original(*a, **kw)
        calls += 1
        if calls == 3:  # final preparation proof; the later frame must inspect again
            git(carriers[0], 'config', 'diff.external', 'touch ' + shlex.quote(str(marker)))
            status_count = mock.channels().count('worktree:status')
        return result
    monkeypatch.setattr(merge, 'proof', add_program)
    out = await create(daemon)
    assert out['status'] == 'failed' and not api.write_frames(mock), out
    assert status_count is not None and mock.channels().count('worktree:status') == status_count
    assert not marker.exists() and not merge._document().get('carrier_writers')
    await daemon.fleet.close()


@pytest.mark.skipif(os.geteuid() == 0, reason='root can read mode-000 fixture directories')
@pytest.mark.parametrize('carrier', [0, 1])
async def test_exit_zero_empty_status_with_unreadable_directory_is_not_clean(daemon, mock, carriers, carrier):
    blocked = carriers[carrier] / 'unreadable'
    blocked.mkdir()
    (blocked / 'private-file').write_text('fixture bytes')
    blocked.chmod(0)
    try:
        status = subprocess.run(['git', '-C', str(carriers[carrier]), 'status', '--porcelain=v1', '-z', '--untracked-files=all'],
                                capture_output=True, check=False)
        assert status.returncode == 0 and status.stdout == b'' and status.stderr
        out = await create(daemon)
        assert out['status'] == 'failed' and out['error_code'] == 'MERGE_GIT_UNAVAILABLE', out
        assert not api.write_frames(mock)
    finally:
        blocked.chmod(0o700)
        await daemon.fleet.close()


async def test_pending_task_ssh_creation_before_carrier_projection_blocks_merge(daemon, mock, carriers, monkeypatch):
    task = daemon.journal.submit(project='p', host='h1', workspace='merge-fixture', original_words='work', idempotency_key='task')
    daemon.journal.db.execute("UPDATE tasks SET state='dispatching',base_branch='main' WHERE task_id=?", (task['task_id'],))
    task = daemon.journal.get(task['task_id'])
    daemon.journal.command(task['task_id'], 'start_lead', 'pending-new-sid', {'agent': 'claude'}, 'start')
    entered, finish = asyncio.Event(), asyncio.Event()
    async def held_ssh(*args, **kwargs):
        entered.set()
        await finish.wait()
        raise ValueError('fixture stops before external Git effect')
    monkeypatch.setattr(daemon.adapter, '_ssh_script', held_ssh)
    pending = asyncio.create_task(daemon.adapter._ensure_external_worktree(task))
    try:
        await asyncio.wait_for(entered.wait(), 5)
        assert daemon.journal.get(task['task_id'])['external_worktree_path'] is None
        out = await create(daemon)
        assert out['status'] == 'failed' and out['error_code'] == 'MERGE_WRITER_UNPROVEN', out
        assert not api.write_frames(mock) and not merge._document().get('carrier_writers')
    finally:
        finish.set()
        with pytest.raises(ValueError, match='fixture stops'):
            await pending
        await daemon.fleet.close()


async def test_pending_normal_task_workspace_alias_is_not_missed(daemon, mock, carriers):
    task = daemon.journal.submit(project='p', host='h1', workspace='FiXtUrE', original_words='work', idempotency_key='task')
    daemon.journal.command(task['task_id'], 'start_lead', 'pending-new-sid', {'agent': 'claude'}, 'start')
    out = await create(daemon)
    assert out['status'] == 'failed' and out['error_code'] == 'MERGE_WRITER_UNPROVEN', out
    assert not api.write_frames(mock)
    await daemon.fleet.close()


async def test_precarrier_start_origin_is_an_unresolved_consumer(daemon, mock, carriers):
    registry.reserve('h1', {'session_id': 'pending-start', 'origin_cwd': str(carriers[0]),
                           'workspace_id': 'ws-merge', 'agent_preset': 'claude-code-worktree'}, 10)
    out = await create(daemon)
    assert out['status'] == 'failed' and not api.write_frames(mock), out
    await daemon.fleet.close()


@pytest.mark.parametrize('first', ['http', 'rpc', 'mcp', 'cli'])
async def test_actual_adapters_replay_original_key_after_tier_change(served, mock, carriers, monkeypatch, capsys, first):
    from bat_agent_connector import cli
    from bat_agent_connector.mcp_server import build_server
    from tests.test_interrupt_operations import mcp_result, rpc
    d, port = served
    token = api.token(d, P.actor, 'observe', 'integrate')
    monkeypatch.setenv('BATC_TASK_URL', f'http://127.0.0.1:{port}/rpc')
    monkeypatch.setenv('BATC_API_TOKEN', token)
    monkeypatch.setattr(cli, 'load_config', lambda _: d.fleet.config)
    server, local = build_server(d.fleet.config, principal_only=True)
    params = {'host': 'h1', 'session_id': SID, 'confirm': True, 'idempotency_key': 'merge-key'}
    async def call(door):
        if door == 'http':
            status, out = await api.http(port, 'POST', '/api/v1/operations', tok=token, body=intent())
            assert status in {200, 202}, out
            return out['operation']['operation_id']
        if door == 'rpc':
            status, out = await rpc(port, token, 'worktree_merge', params)
            assert status == 200, out
            out = out['result']
        elif door == 'mcp':
            out = await mcp_result(server, 'worktree_merge', params)
        else:
            rc = await asyncio.to_thread(cli.main, ['--json', 'merge', 'h1', SID, '--confirm', '--key', 'merge-key'])
            assert rc == 0
            out = json.loads(capsys.readouterr().out)
        assert out['idempotency_key'] == 'merge-key'
        return out['operation_id']
    try:
        oid = await call(first)
        await settle_operations(d.ops)
        assert d.ops.get(oid)['status'] == 'succeeded'
        d.fleet.config.host('h1').orchestrate = False
        d.ops.context['git_runner'] = None
        for door in ('http', 'rpc', 'mcp', 'cli'):
            assert await call(door) == oid
        assert mock.channels().count('worktree:merge') == 1
    finally:
        await local.close()


async def test_no_key_is_independent_and_scope_replay_controls_stay_original_actor(daemon, mock, carriers):
    request = {'host': 'h1', 'session_id': SID, 'confirm': True}
    first = await merge.legacy(daemon.ops, P, request, entry='rpc')
    second = await merge.legacy(daemon.ops, P, request, entry='rpc')
    assert first['operation_status'] == second['operation_status'] == 'succeeded'
    assert first['operation_id'] != second['operation_id']
    assert first['idempotency_key'] is second['idempotency_key'] is None
    assert not first['idempotency_enabled'] and not second['idempotency_enabled']
    assert mock.channels().count('worktree:merge') == 1  # second request truthfully observes already merged
    op, _ = daemon.ops.create(P, **intent())
    for caller in (api_auth.Principal(P.actor, frozenset({'observe'})), api_auth.Principal('different', P.scopes)):
        code = 'FORBIDDEN' if caller.actor == P.actor else 'CONTROLLER_MISMATCH'
        with pytest.raises(OperationError, match=code):
            daemon.ops.cancel(caller, op['operation_id'])
    with pytest.raises(OperationError, match='IDEMPOTENCY_KEY_REQUIRED'):
        daemon.ops.create(P, **intent(idempotency_key=None))
    await settle_operations(daemon.ops)
    await daemon.fleet.close()


async def test_cli_failed_refusal_exits_nonzero_and_readonly_never_contacts_central(served, mock, carriers, monkeypatch, capsys):
    from bat_agent_connector import cli
    d, port = served
    monkeypatch.setenv('BATC_TASK_URL', f'http://127.0.0.1:{port}/rpc')
    monkeypatch.setenv('BATC_API_TOKEN', api.token(d, P.actor, 'integrate'))
    monkeypatch.setattr(cli, 'load_config', lambda _: d.fleet.config)
    mock.metas[SID]['isStreaming'] = True
    args = ['--json', 'merge', 'h1', SID, '--confirm', '--key', 'cli-failed']
    assert await asyncio.to_thread(cli.main, args) == 1
    out = json.loads(capsys.readouterr().out)
    assert out['operation_error_code'] == 'MERGE_WRITER_UNPROVEN'
    assert await asyncio.to_thread(cli.main, args) == 1
    assert json.loads(capsys.readouterr().out)['operation_id'] == out['operation_id']
    count = d.ops.db.execute('SELECT COUNT(*) FROM operations').fetchone()[0]
    assert await asyncio.to_thread(cli.main, ['--read-only', *args]) == 1
    assert count == d.ops.db.execute('SELECT COUNT(*) FROM operations').fetchone()[0]
    assert not api.write_frames(mock)


@pytest.mark.parametrize('field,value', [('roots', []), ('host', None), ('actor', ''), ('journal_path', 'relative.db'),
                                       ('version', True), ('session_ids', []), ('plan_sha256', 'bad'), ('operation_id', 'other')])
async def test_present_malformed_reservation_fails_closed_before_stale_grant_frame(daemon, mock, carriers, field, value):
    tab = next(t for t in mock.ws_doc['terminals'] if t.get('id') == SID)
    grant = await resource_policy.authorize_session(daemon.fleet, 'h1', 'session.send', tab)
    path = registry.registry_path()
    doc = json.loads(path.read_text())
    op_id = 'op_' + 'a' * 32
    marker = {'version': 1, 'operation_id': op_id, 'actor': 'original', 'journal_path': str(daemon.journal.path.resolve()),
              'host': 'h1', 'roots': list(map(str, carriers)), 'session_ids': [SID], 'plan_sha256': 'b' * 64}
    marker[field] = value
    doc['carrier_writers'] = {op_id: marker}
    registry._write_document(path, doc)
    with pytest.raises(ResourceReadOnly, match='MERGE_RESERVED'):
        await daemon.fleet.client('h1').invoke('claude:send-message', {'sessionId': SID, 'prompt': 'must not send'}, grant=grant)
    assert not api.write_frames(mock)
    await daemon.fleet.close()
