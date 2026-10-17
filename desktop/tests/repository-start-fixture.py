"""Published-version UI with actual central receipts and temporary Git; no live provider."""
from __future__ import annotations

import asyncio
import contextlib
import copy
import json
import os
import re
import sys
import tempfile
from pathlib import Path

from bat_agent_connector import api_auth, integration, platform_files, registry
from bat_agent_connector.config import parse_config
from bat_agent_connector.errors import InvokeTimeout
from bat_agent_connector.task_daemon import TaskDaemon
from tests.fakegithub import TOKEN as GH_TOKEN
from tests.fakegithub import FakeGitHub
from tests.mockbat import TOKEN, MockBat
from tests.test_artifacts import LocalArtifactHost, action, upload
from tests.test_checkpoints import RealGitLog, bat_writes, git, snapshot
from tests.test_repository_sync import Runner


async def main():
    with tempfile.TemporaryDirectory(prefix='batc-published-ui-') as temporary:
        root = Path(temporary)
        platform_files.ensure_private_directory(root / "state")
        platform_files.ensure_private_directory(root / "config")
        os.environ.update(BATC_CONFIG_DIR=str(root / 'config'), BATC_STATE_DIR=str(root / 'state'),
                          BATC_TEST_TOKEN=TOKEN, REPO_FAKE_TOKEN=GH_TOKEN)
        os.environ.pop('BATC_DEVICE_ID', None)
        human, remote, managed = root / 'human', root / 'remote.git', root / 'managed'
        human.mkdir()
        managed.mkdir()
        git(human, 'init', '--template=', '-q', '-b', 'main')
        git(human, 'config', 'user.name', 'fixture')
        git(human, 'config', 'user.email', 'fixture@example.invalid')
        (human / 'hello.txt').write_text('published bytes\n')
        git(human, 'add', '.')
        git(human, 'commit', '-qm', 'published')
        sha = git(human, 'rev-parse', 'HEAD')
        git(root, 'clone', '--bare', '--no-hardlinks', str(human), str(remote))
        (human / 'hello.txt').write_text('unpublished human edit\n')
        before, remote_before = snapshot(human), git(remote, 'for-each-ref')
        mock, gh = MockBat(), FakeGitHub()
        await mock.start()
        gh.start()
        def provide(method, path, _):
            if method == 'GET' and path == '/repos/o/r/pulls/1' and 1 in gh.pulls:
                # GitHub updates this read-only ref after a normal push to the PR branch.
                git(remote, 'update-ref', 'refs/pull/1/head', git(remote, 'rev-parse', 'refs/heads/project-pr'))
            if method == 'GET' and path == '/repos/o/r/git/ref/heads/main':
                gh.script.append(('GET', re.escape(path) + '$', 200, {},
                                  {'ref': 'refs/heads/main', 'object': {'type': 'commit', 'sha': sha}}))
        gh.before_request = provide
        raw = {'hosts': {'h1': {'url': mock.url, 'fingerprint': mock.fingerprint, 'token_ref': 'env:BATC_TEST_TOKEN',
               'writes': True, 'orchestrate': True, 'orchestrate_register_tabs': False, 'orchestrate_max_sessions': 4,
               'managed_roots': [str(managed)]}}, 'safety': {'write_min_interval_s': 0},
               'github': {'token_ref': 'env:REPO_FAKE_TOKEN', 'api_url': gh.url, 'repos': [{'repository': 'o/r',
               'integrate': {'hosts': ['h1'], 'remote_url': str(remote)},
               'sync': {'remote_url': str(remote), 'bindings': [{'host': 'h1', 'workspace_id': 'ws-1'}]}}]}}
        mock.ws_doc['workspaces'][0]['folderPath'] = str(human)
        mock.git_logs = RealGitLog()
        mock.handlers['git:branch'] = lambda p: git(p['cwd'], 'branch', '--show-current')
        mock.echo_sends = True
        original_workspace = copy.deepcopy(mock.ws_doc)
        daemon = TaskDaemon(parse_config(raw), root / 'journal.db')
        daemon.ops.context['git_runner'] = Runner()
        scopes = ['observe', 'start', 'integrate']
        if os.environ.get('BATC_DISPATCH_CREATE_PROJECT') == '1':
            scopes.append('manage')
        token = api_auth.issue(daemon.journal.db, 'published-browser', scopes)
        server = await asyncio.start_server(daemon._handle, '127.0.0.1', 0)
        worker = asyncio.create_task(daemon.ops.loop(0.1))
        print(json.dumps({'port': server.sockets[0].getsockname()[1], 'token': token, 'sha': sha}), flush=True)
        try:
            while line := await asyncio.to_thread(sys.stdin.readline):
                command = json.loads(line)
                if command['action'] == 'stop':
                    break
                if command['action'] == 'writes':
                    daemon.fleet.config.host('h1').writes = command['enabled']
                elif command['action'] == 'prepare-delivery':
                    git(remote, 'update-ref', 'refs/heads/project-pr', sha)
                    git(remote, 'update-ref', 'refs/pull/1/head', sha)
                    gh.add_pr(1, sha, head_ref='project-pr')
                    gh.track_remote(1, str(remote))
                    results = []
                    for oid in command['operations']:
                        op = daemon.ops.get(oid)
                        path = Path(op['result']['worktree_path'])
                        name = 'result-' + oid[3:15] + '.txt'
                        (path / name).write_text('result of ' + oid + '\n')
                        git(path, 'add', name)
                        git(path, '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid', 'commit', '-qm', 'Managed result')
                        mock.metas[op['result']['session_id']]['isStreaming'] = False
                        results.append({'operation_id': oid, 'sha': git(path, 'rev-parse', 'HEAD')})
                    await daemon.inventory.refresh_host('h1')
                    print(json.dumps({'results': results}), flush=True)
                    continue
                elif command['action'] == 'verify-delivery':
                    for result in command['results']:
                        rows = integration.candidates(daemon.ops, 'h1', source_kind='execution', source_id=result['operation_id'])
                        selected = rows['selected']
                        assert selected['result']['status'] == 'unverified'
                        assert selected['delivered_to'][0]['pinned_sha'] == result['sha']
                        assert selected['delivered_to'][0]['pull_number'] == 1
                        assert git(remote, 'merge-base', '--is-ancestor', result['sha'], 'refs/heads/project-pr') == ''
                    assert snapshot(human) == before
                    assert not daemon.journal.db.execute('SELECT 1 FROM tasks').fetchone()
                    assert len([f for f in bat_writes(mock) if f['channel'] == 'claude:start-session']) == 2
                    assert all(method == 'GET' for method, _, _ in gh.requests)
                elif command['action'] == 'prepare-project':
                    project_id = command.get('project_id')
                    if project_id:
                        row = daemon.journal.db.execute('SELECT repositories,created_by FROM projects WHERE project_id=?',
                                                       (project_id,)).fetchone()
                        assert json.loads(row['repositories']) == ['o/r'] and row['created_by'] == 'published-browser'
                    else:
                        project = await action(daemon, 'project.create', params={'name': 'Dispatch fixture', 'repositories': ['o/r']})
                        project_id = project['result']['project_id']
                    artifact = await upload(daemon, b'fixed project input\n')
                    daemon.ops.context['artifact_host'] = LocalArtifactHost()
                    print(json.dumps({'project_id': project_id, 'artifact': artifact}), flush=True)
                    continue
                elif command['action'] == 'archive-project':
                    version = daemon.journal.db.execute('SELECT version FROM projects WHERE project_id=?',
                        (command['project_id'],)).fetchone()[0]
                    edit = await action(daemon, 'project.update', target={'project_id': command['project_id']},
                        params={'archived': command['archived']}, pre={'expected_version': version})
                    assert edit['status'] == 'succeeded', edit
                elif command['action'] == 'lose-send':
                    mock.echo_sends = False
                    client, original = daemon.fleet.client('h1'), daemon.fleet.client('h1').invoke
                    async def lose(channel, *args, _original=original, **kwargs):
                        result = await _original(channel, *args, **kwargs)
                        if channel == 'claude:send-message':
                            raise InvokeTimeout('fixture actual send ACK lost')
                        return result
                    client.invoke = lose
                elif command['action'] in {'verify', 'verify-project'}:
                    op = daemon.ops.get(command['operation_id'])
                    assert op['actor'] == 'published-browser' and op['action'] == 'repository.continue'
                    assert op['params']['source_sha'] == sha and op['params']['prompt'] == command['prompt']
                    assert op['status'] == command['status'], op
                    operations = daemon.ops.list()['operations']
                    if command['action'] == 'verify-project':
                        operations = [o for o in operations if o['action'] == 'repository.continue']
                        assert op['params']['project_id'] == command['project_id']
                        assert op['preconditions']['expected_project_version'] == command['version']
                        assert op['params']['artifacts'] == [command['artifact']]
                        assert op['params']['model'] == 'selected-model'
                        ref = command['artifact']
                        path = Path(op['external_refs']['worktree_path'], '.batc-inputs', f"{ref['artifact_id']}-r1", 'notes.txt')
                        assert path.read_bytes() == b'fixed project input\n'
                        frames = bat_writes(mock)
                        assert all(f['params']['options']['model'] == 'selected-model' for f in frames if f['channel'] == 'claude:start-session')
                        sends = [f['params']['prompt'] for f in frames if f['channel'] == 'claude:send-message']
                        assert any(command['prompt'] in p and ref['digest'] in p and '.batc-inputs/' in p for p in sends)
                        assert not daemon.journal.db.execute('SELECT 1 FROM work_items').fetchone()
                    assert len(operations) == command['operations']
                    sid = op['external_refs']['session_id']
                    assert registry.get('h1', sid)['start_operation_id'] == op['operation_id']
                    assert git(op['external_refs']['worktree_path'], 'rev-parse', 'HEAD') == sha
                    assert Path(op['external_refs']['worktree_path'], 'hello.txt').read_text() == 'published bytes\n'
                    frames = bat_writes(mock)
                    assert len(frames) == command['frames'], frames
                    assert snapshot(human) == before and git(remote, 'for-each-ref') == remote_before
                    assert mock.ws_doc == original_workspace
                    assert all(method == 'GET' for method, _, _ in gh.requests), gh.requests
                else:
                    raise ValueError('Unknown fixture action')
                print(json.dumps({'ok': True}), flush=True)
        finally:
            worker.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await worker
            server.close()
            await server.wait_closed()
            await daemon.artifact_store.close_reaper()
            await daemon.inventory.close()
            await daemon.fleet.close()
            daemon.journal.close()
            await mock.stop()
            gh.stop()


if __name__ == '__main__':
    asyncio.run(main())
