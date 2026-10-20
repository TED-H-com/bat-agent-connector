"""Upgrade two distinct packaged cores using disposable, disk-backed state.

Both resource directories must contain the original build manifest/runtime.gz.
This fixture never installs an app or connects to BAT, GitHub or a model. Package
versions and digests are evidence; relabelling an equal-version binary is refused.
"""
from __future__ import annotations

import argparse
import asyncio
import concurrent.futures
import contextlib
import gzip
import hashlib
import json
import os
import re
import runpy
import signal
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

# macOS ff633ea3 progressed beyond old fixture setup after the 40s stack dump,
# but missed 45s readiness. Linux's body-to-ready was 1.59s: double it,
# round up to a 5s window, then add to 45s => 50s. Only seed readiness changes;
# the provider window and the whole case (including cleanup) still have 60s.
SEED_READINESS_TIMEOUT_S = 50


def package(resources, directory):
    manifest = json.loads((resources / 'manifest.json').read_text())
    assert manifest['protocol'] == 1
    assert re.fullmatch(r'\d{1,9}\.\d{1,9}\.\d{1,9}', manifest['runtime_version'])
    assert manifest['executable'] in {'batc-managed-runtime', 'batc-managed-runtime.exe'}
    payload = (resources / 'runtime.gz').read_bytes()
    assert hashlib.sha256(payload).hexdigest() == manifest['payload_sha256']
    binary = gzip.decompress(payload)
    assert len(binary) == manifest['size']
    assert hashlib.sha256(binary).hexdigest() == manifest['sha256']
    directory.mkdir(mode=0o700)
    executable = directory / manifest['executable']
    executable.write_bytes(binary)
    executable.chmod(0o700)
    return manifest, executable


def command(executable, data, env, *, action='ensure', refused=False):
    result = subprocess.run(  # noqa: S603 - digest-verified fixture executable, fixed argv
        [str(executable), action, '--data-dir', str(data)], env=env,
        stdin=subprocess.DEVNULL, capture_output=True, timeout=90)
    assert len(result.stdout) < 16384, 'Bounded runtime reply required'
    try:
        reply = json.loads(result.stdout)
    except ValueError:
        raise AssertionError('Invalid packaged runtime reply') from None
    if refused:
        # Native IPC deliberately returns no arbitrary exception text. Verify
        # refusal and its phase, then check owner/data preservation separately.
        assert result.returncode != 0 and reply.get('error') == 'MANAGED_RUNTIME_UNAVAILABLE'
        assert reply.get('location', {}).get('phase') == 'ensure'
    else:
        assert result.returncode == 0 and reply.get('protocol') == 1, 'Packaged runtime command failed'
    return reply


def request(launch, path, *, body=None, key=None):
    headers = {'Authorization': 'Bearer ' + launch['token']}
    if body is not None:
        headers['Content-Type'] = 'application/json'
    if key:
        headers['Idempotency-Key'] = key
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, hdrs, newurl):
            raise AssertionError('Fixture peer redirected')
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    req = urllib.request.Request(  # noqa: S310 - owned fixture loopback peer
        launch['endpoint'] + path, headers=headers,
        data=json.dumps(body).encode() if body is not None else None)
    with opener.open(req, timeout=5) as response:
        payload = response.read(1_048_577)
        assert len(payload) <= 1_048_576
        return json.loads(payload)


def identity(reply):
    return tuple(reply[key] for key in ('actor', 'server_id', 'principal_id', 'token'))


def wait_operation(launch, operation):
    until = time.monotonic() + 30
    while operation['status'] in {'accepted', 'running', 'waiting_checks', 'waiting_external'} and time.monotonic() < until:
        time.sleep(0.05)
        operation = request(launch, '/api/v1/operations/' + operation['operation_id'])['operation']
    assert operation['status'] == 'succeeded', 'Fixture local operation did not complete'
    return operation


def history(data, operation_id):
    with contextlib.closing(sqlite3.connect(data / 'state' / 'tasks.sqlite3')) as db:
        return {
            'operation': db.execute('SELECT * FROM operations WHERE operation_id=?', (operation_id,)).fetchone(),
            'steps': db.execute('SELECT * FROM operation_steps WHERE operation_id=? ORDER BY name', (operation_id,)).fetchall(),
            'workspace': db.execute('SELECT * FROM pm_workspaces WHERE operation_id=?', (operation_id,)).fetchall(),
            'events': db.execute("""SELECT seq,resource_type,resource_id,kind,body,actor FROM api_events
                WHERE (resource_type='operation' AND resource_id=?) OR body LIKE ? ORDER BY seq""",
                (operation_id, '%' + operation_id + '%')).fetchall(),
        }


def idle_upgrade(previous, current, old_manifest, new_manifest, root, env):
    data = root / 'idle-installation'
    stopped = False
    try:
        original = command(previous, data, env)
        assert original['runtime_version'] == old_manifest['runtime_version']
        body = {'action': 'pm.workspace.create', 'target': {}, 'params': {
            'name': 'Saved upgrade fixture', 'repositories': ['example/project']}, 'preconditions': {}}
        accepted = request(original, '/api/v1/operations', body=body, key='original-workspace')
        operation = wait_operation(original, accepted['operation'])
        wid = operation['result']['workspace']['workspace_id']
        saved = history(data, operation['operation_id'])
        assert saved['workspace'] and saved['events']
        assert {'operation.accepted', 'operation.succeeded'} <= {
            row[3] for row in saved['events'] if row[1] == 'operation' and row[2] == operation['operation_id']}
        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as workers:
            replies = list(workers.map(lambda _: command(current, data, env), range(3)))
        upgraded = replies[0]
        assert all(reply == upgraded for reply in replies)
        assert identity(upgraded) == identity(original)
        assert upgraded['runtime_version'] == new_manifest['runtime_version']
        assert request(upgraded, '/api/v1/version')['connector'] == new_manifest['runtime_version']
        assert request(upgraded, '/api/v1/pm/workspaces/' + wid)['workspace']['workspace_id'] == wid
        replay = request(upgraded, '/api/v1/operations', body=body, key='original-workspace')['operation']
        assert replay['operation_id'] == operation['operation_id'] and replay['result'] == operation['result']
        assert history(data, operation['operation_id']) == saved
        command(previous, data, env, refused=True)
        assert command(current, data, env) == upgraded
        assert command(current, data, env, action='stop')['stopped'] is True
        stopped = True
        restarted = command(current, data, env)
        stopped = False
        assert identity(restarted) == identity(original)
        assert restarted['runtime_version'] == new_manifest['runtime_version']
        assert history(data, operation['operation_id']) == saved
    finally:
        if not stopped and (data / 'installation.json').exists():
            assert command(current, data, env, action='stop')['stopped'] is True


def terminate_owned(child):
    # This handle is our explicit serve child, never a PID adopted from disk.
    # PyInstaller's inner process belongs to the same created group/tree.
    if child.poll() is not None:
        return
    if os.name == 'nt':
        taskkill = Path(os.environ['SystemRoot']) / 'System32' / 'taskkill.exe'
        subprocess.run([str(taskkill), '/PID', str(child.pid), '/T', '/F'],  # noqa: S603
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
    else:
        os.killpg(child.pid, signal.SIGTERM)
    try:
        child.wait(timeout=10)
    except subprocess.TimeoutExpired:
        if os.name != 'nt':
            os.killpg(child.pid, signal.SIGKILL)
        child.wait(timeout=10)


def busy_refusal(previous, current, old_manifest, root, env):
    data = root / 'busy-installation'
    original = command(previous, data, env)
    assert command(previous, data, env, action='stop')['stopped'] is True
    options = {'creationflags': subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == 'nt' else {'start_new_session': True}
    child = subprocess.Popen(  # noqa: S603 - explicit hash-verified task-owned fault fixture
        [str(previous), 'serve', '--data-dir', str(data)], env=env, stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **options)
    try:
        # Wait for this owned process without asking ensure to spawn another.
        until = time.monotonic() + 45
        while True:
            assert child.poll() is None, 'Owned fixture service exited'
            try:
                pointer = json.loads((data / 'state' / 'task-service.json').read_text())
                launch = {**original, 'endpoint': pointer['endpoint'].removesuffix('/rpc')}
                bootstrap = request(launch, '/api/v1/bootstrap')
                assert bootstrap['sync']['server_id'] == original['server_id']
                break
            except (OSError, ValueError, urllib.error.URLError):
                assert time.monotonic() < until, 'Owned fixture service did not become ready'
                time.sleep(0.1)
        assert identity(command(previous, data, env)) == identity(original)
        oid, now = 'op_' + '7' * 32, time.time()
        with contextlib.closing(sqlite3.connect(data / 'state' / 'tasks.sqlite3')) as db, db:
            # Synthetic uncertain effect: no real BAT request occurred. Its due
            # window stays in the future, so the fixture cannot send anything.
            db.execute('''INSERT INTO operations(operation_id,actor,entry,idem_key,request_hash,action,
                target,params,preconditions,status,next_run_at,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)''', (oid, original['actor'], 'fixture', 'original-unknown-key',
                'original-payload-hash', 'session.send', '{}', '{}', '{}', 'uncertain', now + 3600, now, now))
        saved = history(data, oid)
        command(current, data, env, refused=True)
        assert child.poll() is None
        retained = command(previous, data, env)
        assert identity(retained) == identity(original) and retained['runtime_version'] == old_manifest['runtime_version']
        assert history(data, oid) == saved
    finally:
        terminate_owned(child)
        assert command(previous, data, env, action='stop')['stopped'] is True


fixture_log_text = runpy.run_path(str(Path(__file__).with_name('managed_upgrade_observer.py')))['fixture_log_text']


def preserve_publication_logs(root):
    directory = Path(__file__).resolve().parents[1] / 'test-results/packaged-core-upgrade'
    directory.mkdir(parents=True, exist_ok=True)
    seed = 'Seed log was not written; failure occurred before producer output.'
    for name in ('publication-seed.log', 'publication-core.log'):
        source = root / name
        if not source.exists():
            continue
        # Bound both uploaded output and exception size, including a noisy crash.
        with source.open('rb') as stream:
            stream.seek(0, 2)
            stream.seek(max(0, stream.tell() - 512 * 1024))
            content = fixture_log_text(stream.read().decode(errors='replace'), root)
        (directory / name).write_text(content)
        if name == 'publication-seed.log':
            seed = '\n'.join(content.splitlines()[-300:])
    return seed


def publication_upgrade(previous, current, previous_source, root, env, *, pytest_prefix=None):
    try:
        result = _publication_upgrade(previous, current, previous_source, root, env, pytest_prefix=pytest_prefix)
    except Exception as error:
        tail = preserve_publication_logs(root)
        message = fixture_log_text(f'{type(error).__name__}: {error}', root)
        raise AssertionError(f'{message}\nPublication seed log (last 300 lines):\n{tail}') from None
    preserve_publication_logs(root)
    return result


def _publication_upgrade(previous, current, previous_source, root, env, *, pytest_prefix=None):
    """Real 201 producer -> distinct managed core, preserving the original journal.

    The producer loads the previous release's own Python source and test doubles.
    Only its outer completion is faulted; no receipt/operation rows are fabricated.
The selected BAT host fixture executes POSIX Git scripts. Windows retains the
existing packaged workspace/identity checks; its publication path remains open.
"""
    if os.name == 'nt':
        return {'status': 'not-run', 'reason': 'POSIX test BAT host required; Windows publication acceptance remains open'}
    started = time.monotonic()
    previous_source = previous_source.resolve()
    handlers = {}
    for name in ('pr_publication', 'pm_work_start', 'pm_attempt_results'):
        old = previous_source / 'src/bat_agent_connector' / (name + '.py')
        handlers[name] = hashlib.sha256(old.read_bytes()).hexdigest()
    data = root / 'publication-installation'
    original = command(previous, data, env)
    assert command(previous, data, env, action='stop')['stopped'] is True
    output = root / 'publication-seed.json'
    # Controller Python has dev dependencies; frozen children still have no
    # Python on PATH. The seeder writes through the real action/receipt code.
    seed_env = {**os.environ, 'PYTHONPATH': os.pathsep.join([str(previous_source / 'src'), str(previous_source),
        str(Path(__file__).resolve().parent)]), 'TMPDIR': str(root),
        'TMP': str(root), 'TEMP': str(root), 'BATC_UPGRADE_SEED_DATA': str(data),
        'BATC_UPGRADE_SEED_REPORT': str(output), 'BATC_UPGRADE_SEED_MODE': '1'}
    seed_command = [*(pytest_prefix or [sys.executable, '-m', 'pytest']), '-vv', '-s', '--setup-show',
        '-p', 'managed_upgrade_observer',
        str(Path(__file__).resolve()) + '::test_publication_seed', '--noconftest', '--import-mode=importlib',
        '--rootdir=' + str(previous_source), '--asyncio-mode=auto',
        '-o', 'faulthandler_timeout=40',
        '--basetemp=' + str(root / 'publication-pytest')]
    seed_log = root / 'publication-seed.log'
    with seed_log.open('wb') as log:
        seeded = subprocess.Popen(seed_command, cwd=previous_source, env=seed_env,  # noqa: S603 - fixed source producer
            stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
    try:
        until = time.monotonic() + SEED_READINESS_TIMEOUT_S
        while not output.exists():
            assert seeded.poll() is None, 'Original source producer exited before readiness'
            assert time.monotonic() < until, 'Original source producer did not become ready'
            time.sleep(0.05)
        info = json.loads(output.read_text())
        assert info['handlers'] == handlers and info['effects'] == {'create_pull': 1, 'push': 1}
        assert info['producer_version'] == original['runtime_version']
        assert Path(info['producer_root']) == (previous_source / 'src').resolve()
        assert info['rows']['work_attempts'] and info['rows']['session_results']
        options = {'start_new_session': True}
        with (root / 'publication-core.log').open('wb') as log:
            child = subprocess.Popen([str(current), 'serve', '--data-dir', str(data)], env={**env, **info['provider_environment']},  # noqa: S603 - digest-verified fixture
                stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, **options)
        try:
            # Same child command ensure would spawn, held by our Popen handle so a
            # fault fixture can always reap it without adopting a PID from disk.
            until = time.monotonic() + 15
            while True:
                assert child.poll() is None, 'Owned upgraded core exited'
                try:
                    pointer = json.loads((data / 'state/task-service.json').read_text())
                    upgraded = {**original, 'endpoint': pointer['endpoint'].removesuffix('/rpc')}
                    version = request(upgraded, '/api/v1/version')['connector']
                    if version != original['runtime_version']:
                        break
                except (OSError, ValueError, urllib.error.URLError):
                    pass
                assert time.monotonic() < until, 'Owned upgraded core did not become ready'
                time.sleep(0.05)
            verified = command(current, data, env)
            assert identity(verified) == identity(original)
            peer = {**verified, 'token': info['token']}
            done = wait_operation(peer, request(peer, '/api/v1/operations/' + info['operation_id'])['operation'])
            replay = request(peer, '/api/v1/operations?wait=0',
                body={key: value for key, value in info['request'].items() if key != 'idempotency_key'},
                key=info['request']['idempotency_key'])
            assert replay['created'] is False and replay['operation']['operation_id'] == done['operation_id']
            with contextlib.closing(sqlite3.connect((data / 'state/tasks.sqlite3').as_uri() + '?mode=ro', uri=True)) as db:
                queries = {'work_items': 'SELECT * FROM work_items ORDER BY rowid',
                    'work_attempts': 'SELECT * FROM work_attempts ORDER BY rowid',
                    'session_results': 'SELECT * FROM session_results ORDER BY rowid',
                    'pr_creation_receipts': 'SELECT * FROM pr_creation_receipts ORDER BY rowid'}
                for table, query in queries.items():
                    expected = info['receipt'] if table == 'pr_creation_receipts' else info['rows'][table]
                    actual = [list(row) for row in db.execute(query)]
                    assert actual == expected, table
                events = [list(row) for row in db.execute('SELECT * FROM api_events WHERE resource_id=? ORDER BY rowid',
                    (done['operation_id'],))]
                assert events[:len(info['operation_events'])] == info['operation_events']
            attempt = info['attempt']
            work = request(peer, '/api/v1/work-items/' + attempt['work_item_id'])
            result = request(peer, '/api/v1/pm/workspaces/' + attempt['workspace_id'] +
                '/work-attempts/' + attempt['attempt_id'] + '/result')
            assert work['work_item']['work_item_id'] == attempt['work_item_id']
            for key, value in info['work_item_view'].items():
                assert work['work_item'][key] == value, key
            assert result['attempt_id'] == attempt['attempt_id'] and result['results'] == info['result_results']
            actual_files = {path.relative_to(Path(info['manual_checkout'])).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
                for path in Path(info['manual_checkout']).rglob('*') if path.is_file()}
            assert actual_files == info['manual_files']
            result = {'status': 'passed', 'original_producer_handler_sha256': handlers,
                'producer_version': info['producer_version'],
                'real_work_attempt_result': True, 'original_201_and_key_recovered': True,
                'create_pull': 1, 'push': 1, 'manual_checkout_unchanged': True}
        finally:
            terminate_owned(child)
    finally:
        (root / 'publication-seed.release').write_text('finished\n')
        try:
            seeded.wait(timeout=10)
        except subprocess.TimeoutExpired:
            # Reap the owned process without replacing the original failure.
            terminate_owned(seeded)
        finally:
            terminate_owned(seeded)
    assert seeded.returncode == 0, 'Original source producer failed during cleanup'
    effects = json.loads((root / 'publication-effects.json').read_text())
    assert effects == {'create_pull': 1, 'push': 1}
    result['seconds'] = round(time.monotonic() - started, 3)
    assert result['seconds'] <= 60, 'Added packaged publication case exceeded its 60-second budget'
    return result


if os.environ.get('BATC_UPGRADE_SEED_MODE') == '1':
    # Explicit pytest node only. PYTHONPATH/rootdir select the original release;
    # --noconftest prevents this checkout's fixtures from replacing its producer.
    import pytest
    from managed_upgrade_observer import record

    from bat_agent_connector import platform_files
    from bat_agent_connector.task_daemon import TaskDaemon
    from tests import test_repository_sync
    from tests.conftest import isolated_dirs, mock
    from tests.test_pm_work_attempt_result import pm, ready, start_ready
    from tests.test_repository_sync import world as repository_world

    _ = isolated_dirs, mock, pm, ready, start_ready

    @pytest.fixture(name='world')
    async def publication_world(mock, monkeypatch):
        data = Path(os.environ['BATC_UPGRADE_SEED_DATA'])
        work = data.parent / 'publication-fixture'
        platform_files.ensure_private_directory(work)
        monkeypatch.setenv('BATC_STATE_DIR', str(data / 'state'))
        monkeypatch.setenv('BATC_CONFIG_DIR', str(data / 'config'))
        monkeypatch.setattr(test_repository_sync, 'TaskDaemon',
            lambda config, _: TaskDaemon(config, data / 'state/tasks.sqlite3'))
        async for value in repository_world.__wrapped__(mock, work, monkeypatch):
            yield value

    async def test_publication_seed(start_ready, mock, monkeypatch):
        record('seed.producer.start')
        print('[publication-seed] start_ready complete; entering Work/Result/publication producer', flush=True)
        helper = Path(__file__).resolve().parents[2] / 'tests/upgrade_continuity_helpers.py'
        producer = runpy.run_path(str(helper))['seed_publication']
        info = await producer(start_ready, mock, monkeypatch, Path(os.environ['BATC_UPGRADE_SEED_DATA']))
        print('[publication-seed] original 201 receipt committed; releasing journal', flush=True)
        start_ready['server'].close()
        await start_ready['server'].wait_closed()
        daemon = start_ready['d']
        await daemon.fleet.close()
        await daemon.inventory.close()
        daemon.release_owner()
        daemon.journal.close()
        info['provider_environment'] = {name: os.environ[name] for name in ('BATC_TEST_TOKEN', 'REPO_FAKE_TOKEN')}
        output = Path(os.environ['BATC_UPGRADE_SEED_REPORT'])
        platform_files.atomic_write(output, json.dumps(info).encode())
        record('seed.ready')
        print('[publication-seed] readiness written; providers remain alive', flush=True)
        # Keep the original FakeGitHub/MockBat alive through the new core's
        # recovery, so a duplicate REST call is observed across the switch.
        until = time.monotonic() + 60
        while not (output.parent / 'publication-seed.release').exists():
            assert time.monotonic() < until, 'Controller did not release owned source producer'
            await asyncio.sleep(0.05)
        effects = {'create_pull': len(start_ready['posts']), 'push': start_ready['runner'].ran['publication-push']}
        record('seed.providers.released')
        assert effects == {'create_pull': 1, 'push': 1}
        platform_files.atomic_write(output.parent / 'publication-effects.json', json.dumps(effects).encode())



def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--previous-resources', type=Path, required=True)
    parser.add_argument('--current-resources', type=Path, required=True)
    parser.add_argument('--receipt', type=Path)
    parser.add_argument('--previous-source', type=Path)
    args = parser.parse_args()
    temp_root = Path(os.environ.get('RUNNER_TEMP', Path.home() / 'agent-work/tmp/managed-runtime'))
    temp_root.mkdir(parents=True, exist_ok=True)
    root = Path(tempfile.mkdtemp(prefix='packaged-upgrade-', dir=temp_root))
    remove = runpy.run_path(str(Path(__file__).with_name('managed-runtime-fixture.py')))['remove_stopped_fixture']
    try:
        old, previous = package(args.previous_resources, root / 'previous')
        new, current = package(args.current_resources, root / 'current')
        assert (old['platform'], old['architecture']) == (new['platform'], new['architecture'])
        assert tuple(map(int, old['runtime_version'].split('.'))) < tuple(map(int, new['runtime_version'].split('.'))), 'Upgrade requires distinct increasing package versions'
        assert old['sha256'] != new['sha256'], 'Upgrade requires two actual runtime binaries'
        env = os.environ.copy()
        for key in ('PYTHONHOME', 'PYTHONPATH', 'VIRTUAL_ENV', 'BATC_DESKTOP_TOKEN'):
            env.pop(key, None)
        env.update(PATH='', PYINSTALLER_RESET_ENVIRONMENT='1', TMPDIR=str(root), TEMP=str(root), TMP=str(root))
        idle_upgrade(previous, current, old, new, root, env)
        busy_refusal(previous, current, old, root, env)
        publication = publication_upgrade(previous, current,
            args.previous_source or args.previous_resources.resolve().parents[1], root, env)
        receipt = {'status': 'passed', 'evidence_level': 'packaged-core-upgrade-fixture', 'live_accepted': False,
            'previous': {key: old[key] for key in ('runtime_version', 'sha256')},
            'current': {key: new[key] for key in ('runtime_version', 'sha256')},
            'platform': new['platform'], 'architecture': new['architecture'], 'no_python_on_child_path': True,
            'stable_identity_and_workspace': True, 'original_operation_key_and_history': True,
            'concurrent_upgrade_clients': 3, 'new_version_read_back': True, 'downgrade_refused': True,
            'uncertain_effect_blocks_upgrade': True, 'unknown_key_and_old_owner_retained': True,
            'publication_continuity': publication,
            'task_owned_processes_stopped': True, 'fixture_directory_removed': True}
    finally:
        # If a case fails, never remove data underneath a surviving service.
        # The explicit busy-fixture child is already reaped in its own finally.
        if 'current' in locals() and 'env' in locals():
            for name in ('idle-installation', 'busy-installation', 'publication-installation'):
                data = root / name
                if (data / 'installation.json').exists():
                    assert command(current, data, env, action='stop')['stopped'] is True
        remove(root)
    output = json.dumps(receipt)
    if args.receipt:
        args.receipt.parent.mkdir(parents=True, exist_ok=True)
        args.receipt.write_text(output + '\n')
    print(output)


if __name__ == '__main__':
    main()
