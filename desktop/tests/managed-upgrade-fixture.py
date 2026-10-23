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
import hmac
import json
import os
import re
import runpy
import secrets
import signal
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from managed_runtime_assertions import (
    assert_stopped_reply,
    service_files,
    service_stays_stopped,
    stable_launch,
)

from bat_agent_connector import platform_files

continuity = runpy.run_path(str(Path(__file__).resolve().parents[2] / 'tests/upgrade_continuity_helpers.py'))
snapshot_rows = continuity['snapshot_rows']
assert_rows_preserved = continuity['assert_rows_preserved']

# Intel's longest observed progress gap is 10.903s before fixture setup:
# round 1.5 * 10.903 up to 5s => 20s of inactivity. Stack samples are not progress.
# Round the slowest whole case (50.545s) + 20s up to 5s => a 75s seed ceiling.
# Retain the former 60 - 45 = 15s allowance for recovery and cleanup: 90s total.
SEED_INACTIVITY_TIMEOUT_S = 20
SEED_READINESS_CEILING_S = 75
PUBLICATION_CASE_BUDGET_S = 90


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


def command(executable, data, env, *, action='ensure', refused=False, stopped=None):
    result = subprocess.run(  # noqa: S603 - digest-verified fixture executable, fixed argv
        [str(executable), action, '--data-dir', str(data)], env=env,
        stdin=subprocess.DEVNULL, capture_output=True, timeout=90)
    assert len(result.stdout) < 16384, 'Bounded runtime reply required'
    try:
        reply = json.loads(result.stdout)
    except ValueError:
        raise AssertionError('Invalid packaged runtime reply') from None
    if stopped is not None:
        assert_stopped_reply(result.returncode, reply, stopped)
    elif refused:
        # Native IPC deliberately returns no arbitrary exception text. Verify
        # refusal and its phase, then check owner/data preservation separately.
        assert result.returncode != 0 and reply.get('error') == 'MANAGED_RUNTIME_UNAVAILABLE'
        assert reply.get('location', {}).get('phase') == 'ensure'
    else:
        assert result.returncode == 0 and reply.get('protocol') == 1, 'Packaged runtime command failed'
    return reply


def request(launch, path, *, body=None, key=None, authenticated=True):
    headers = {'Authorization': 'Bearer ' + launch['token']} if authenticated else {}
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


class OwnerNotReady(ValueError):
    """Only pointer discovery may be retried, never connection or identity failure."""


def windows_process_parents():
    # Toolhelp's documented PROCESSENTRY32W layout, including pointer-sized heap ID.
    # https://learn.microsoft.com/en-us/windows/win32/api/tlhelp32/ns-tlhelp32-processentry32w
    import ctypes
    from ctypes import wintypes

    class Entry(ctypes.Structure):
        _fields_ = [('dwSize', wintypes.DWORD), ('cntUsage', wintypes.DWORD),
            ('th32ProcessID', wintypes.DWORD), ('th32DefaultHeapID', ctypes.c_size_t),
            ('th32ModuleID', wintypes.DWORD), ('cntThreads', wintypes.DWORD),
            ('th32ParentProcessID', wintypes.DWORD), ('pcPriClassBase', wintypes.LONG),
            ('dwFlags', wintypes.DWORD), ('szExeFile', wintypes.WCHAR * 260)]

    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    snapshot = kernel.CreateToolhelp32Snapshot
    snapshot.argtypes, snapshot.restype = [wintypes.DWORD, wintypes.DWORD], wintypes.HANDLE
    first, following, close = kernel.Process32FirstW, kernel.Process32NextW, kernel.CloseHandle
    for read in (first, following):
        read.argtypes, read.restype = [wintypes.HANDLE, ctypes.POINTER(Entry)], wintypes.BOOL
    close.argtypes, close.restype = [wintypes.HANDLE], wintypes.BOOL
    handle = snapshot(2, 0)  # TH32CS_SNAPPROCESS
    if handle == wintypes.HANDLE(-1).value:
        raise ctypes.WinError()
    try:
        entry = Entry()
        entry.dwSize = ctypes.sizeof(entry)
        parents = {}
        more = first(handle, ctypes.byref(entry))
        while more:
            parents[entry.th32ProcessID] = entry.th32ParentProcessID
            more = following(handle, ctypes.byref(entry))
        if ctypes.get_last_error() != 18:  # ERROR_NO_MORE_FILES
            raise ctypes.WinError()
        return parents
    finally:
        if not close(handle):
            raise ctypes.WinError()


def owned_pid(child, pid):
    if type(pid) is not int or pid <= 0:
        return False
    if pid == child.pid:
        return True
    if os.name == 'nt':
        # CREATE_NEW_PROCESS_GROUP is not a POSIX session; prove ancestry instead.
        parents, seen = windows_process_parents(), set()
        while pid in parents and pid not in seen:
            seen.add(pid)
            pid = parents[pid]
            if pid == child.pid:
                return True
        return False
    try:
        # Both call sites create a fresh session. One-file Python children inherit
        # its session/group; a previous stopped service belongs to neither.
        return os.getsid(pid) == child.pid and os.getpgid(pid) == child.pid
    except ProcessLookupError:
        return False


def owned_launch(data, child, original):
    try:
        pointer = json.loads(platform_files.read_private(data / 'state/task-service.json'))
    except FileNotFoundError:
        raise OwnerNotReady('Owned fixture service is not listening yet') from None
    except OSError as error:
        raise OSError(f"Owned fixture pointer read failed (winerror={getattr(error, 'winerror', None)})") from error
    endpoint = pointer.get('endpoint')
    if not owned_pid(child, pointer.get('pid')) or not endpoint:
        raise OwnerNotReady('Owned fixture service is not listening yet')
    try:
        parsed = urllib.parse.urlsplit(endpoint)
        port = parsed.port
    except ValueError:
        raise OwnerNotReady('Invalid owned fixture endpoint') from None
    # Both fixture cores serve on port zero, never an operator's fixed port.
    if (parsed.scheme != 'http' or parsed.hostname != '127.0.0.1' or not port or port == 18796
            or parsed.path != '/rpc' or parsed.username or parsed.password or parsed.query or parsed.fragment):
        raise OwnerNotReady('Invalid owned fixture endpoint')
    launch = {**original, 'endpoint': endpoint.removesuffix('/rpc')}
    challenge = secrets.token_hex(32)
    proof = request(launch, '/api/v1/managed/identity?challenge=' + challenge, authenticated=False)
    payload = json.dumps([1, challenge, launch['endpoint'], original['server_id']], separators=(',', ':')).encode()
    expected = hmac.new((data / 'installation.key').read_bytes(), payload, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(str(proof.get('proof', '')), expected):
        raise ValueError('Owned fixture service identity could not be verified')
    return launch


def wait_owned_launch(data, child, original, *, timeout, interval=0.1, name='Owned fixture service'):
    until = time.monotonic() + timeout
    while True:
        assert child.poll() is None, name + ' exited'
        try:
            return owned_launch(data, child, original)
        except OwnerNotReady:
            assert time.monotonic() < until, name + ' did not become ready'
            time.sleep(interval)


def identity(reply):
    return tuple(reply[key] for key in ('actor', 'server_id', 'principal_id', 'token'))


def wait_operation(launch, operation):
    until = time.monotonic() + 30
    while operation['status'] in {'accepted', 'running', 'waiting_checks', 'waiting_external'} and time.monotonic() < until:
        time.sleep(0.05)
        operation = request(launch, '/api/v1/operations/' + operation['operation_id'])['operation']
    assert operation['status'] == 'succeeded', 'Fixture local operation did not complete'
    return operation


def history_queries(operation_id):
    return {table: ('WHERE operation_id=?', (operation_id,))
            for table in ('operations', 'operation_steps', 'pm_workspaces')} | {
        'api_events': ("WHERE (resource_type='operation' AND resource_id=?) OR body LIKE ?",
                       (operation_id, '%' + operation_id + '%'))}


def history(data, operation_id):
    with contextlib.closing(sqlite3.connect(data / 'state' / 'tasks.sqlite3')) as db:
        return {table: snapshot_rows(db, table, where=where, args=args)
                for table, (where, args) in history_queries(operation_id).items()}


def assert_history(data, operation_id, saved):
    with contextlib.closing(sqlite3.connect(data / 'state' / 'tasks.sqlite3')) as db:
        for table, (where, args) in history_queries(operation_id).items():
            assert_rows_preserved(db, table, saved[table], where=where, args=args)


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
        assert saved['pm_workspaces']['rows'] and saved['api_events']['rows']
        assert {'operation.accepted', 'operation.succeeded'} <= {
            row['kind'] for row in (dict(zip(saved['api_events']['columns'], values, strict=True))
                for values in saved['api_events']['rows'])
            if row['resource_type'] == 'operation' and row['resource_id'] == operation['operation_id']}
        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as workers:
            replies = list(workers.map(lambda _: command(current, data, env), range(3)))
        upgraded = replies[0]
        assert all(stable_launch(reply) == stable_launch(upgraded) for reply in replies)
        assert identity(upgraded) == identity(original)
        assert upgraded['runtime_version'] == new_manifest['runtime_version']
        assert request(upgraded, '/api/v1/version')['connector'] == new_manifest['runtime_version']
        assert request(upgraded, '/api/v1/pm/workspaces/' + wid)['workspace']['workspace_id'] == wid
        replay = request(upgraded, '/api/v1/operations', body=body, key='original-workspace')['operation']
        assert replay['operation_id'] == operation['operation_id'] and replay['result'] == operation['result']
        assert_history(data, operation['operation_id'], saved)
        command(previous, data, env, refused=True)
        assert stable_launch(command(current, data, env)) == stable_launch(upgraded)
        stop_reply = command(current, data, env, action='stop')
        assert stop_reply['stopped'] is True
        stopped = True
        with service_stays_stopped(data):
            command(current, data, env, stopped=stop_reply['lifecycle'])
            assert command(current, data, env, action='status')['lifecycle'] == stop_reply['lifecycle']
        # The old fixture assumed stop -> ensure restarted. The product's durable
        # hold is intentional; only an explicit resume can clear it.
        stopped = False  # cleanup must also cover a partly completed resume
        restarted = command(current, data, env, action='resume')
        assert not (data / 'state/lifecycle.json').exists()
        resumed_owner = service_files(data)
        assert stable_launch(command(current, data, env)) == stable_launch(restarted)
        assert service_files(data) == resumed_owner, 'Ensure must reuse the one resumed service'
        assert identity(restarted) == identity(original)
        assert restarted['runtime_version'] == new_manifest['runtime_version']
        assert_history(data, operation['operation_id'], saved)
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
        launch = wait_owned_launch(data, child, original, timeout=45)
        bootstrap = request(launch, '/api/v1/bootstrap')
        assert bootstrap['sync']['server_id'] == original['server_id']
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
        assert_history(data, oid, saved)
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


def wait_for_seed(child, output, seed_log, *, monotonic=None, sleep=None):
    """Bound readiness by useful timeline events and an unconditional ceiling.

    The sampling thread can keep printing while the producer is stuck. Only
    collection/fixture transitions, child starts and host-call boundaries extend its wait.
    Consume complete records incrementally; arbitrary output is not progress.
    Clock/sleep injection lets tests exercise the real loop without long sleeps.
    """
    clock, pause = monotonic or time.monotonic, sleep or time.sleep
    started = last_progress = clock()
    events = {'observer.start', 'seed.collection.start', 'seed.collection.end',
              'fixture.setup.start', 'fixture.setup.end',
              'subprocess.Popen', 'os.posix_spawn', 'os.exec',
              'host.call.start', 'host.call.end', 'seed.producer.start', 'seed.ready'}
    with seed_log.open() as log:
        while True:
            now = clock()
            assert now - started < SEED_READINESS_CEILING_S, (
                f'Original source producer did not become ready: {SEED_READINESS_CEILING_S}s ceiling')
            if output.exists():
                return
            assert child.poll() is None, 'Original source producer exited before readiness'
            while True:
                offset = log.tell()
                line = log.readline()
                if not line.endswith('\n'):
                    log.seek(offset)
                    break
                _, marker, payload = line.partition('[seed-timeline] ')
                if marker:
                    with contextlib.suppress(ValueError, AttributeError):
                        if json.loads(payload).get('event') in events:
                            last_progress = now
            assert now - last_progress < SEED_INACTIVITY_TIMEOUT_S, (
                'Original source producer did not become ready: '
                f'no progress for {SEED_INACTIVITY_TIMEOUT_S}s')
            pause(0.05)


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
        wait_for_seed(seeded, output, seed_log)
        info = json.loads(output.read_text())
        assert info['handlers'] == handlers and info['effects'] == {'create_pull': 1, 'push': 1}
        assert info['producer_version'] == original['runtime_version']
        assert Path(info['producer_root']) == (previous_source / 'src').resolve()
        assert info['rows']['work_attempts']['rows'] and info['rows']['session_results']['rows']
        options = {'start_new_session': True}
        with (root / 'publication-core.log').open('wb') as log:
            child = subprocess.Popen([str(current), 'serve', '--data-dir', str(data)], env={**env, **info['provider_environment']},  # noqa: S603 - digest-verified fixture
                stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, **options)
        try:
            # Same child command ensure would spawn, held by our Popen handle so a
            # fault fixture can always reap it without adopting a PID from disk.
            upgraded = wait_owned_launch(data, child, original, timeout=15, interval=0.05, name='Owned upgraded core')
            version = request(upgraded, '/api/v1/version')['connector']
            assert version != original['runtime_version']
            verified = command(current, data, env)
            assert identity(verified) == identity(original)
            peer = {**verified, 'token': info['token']}
            done = wait_operation(peer, request(peer, '/api/v1/operations/' + info['operation_id'])['operation'])
            replay = request(peer, '/api/v1/operations?wait=0',
                body={key: value for key, value in info['request'].items() if key != 'idempotency_key'},
                key=info['request']['idempotency_key'])
            assert replay['created'] is False and replay['operation']['operation_id'] == done['operation_id']
            with contextlib.closing(sqlite3.connect((data / 'state/tasks.sqlite3').as_uri() + '?mode=ro', uri=True)) as db:
                for table in ('work_items', 'work_attempts', 'session_results', 'pr_creation_receipts'):
                    expected = info['receipt'] if table == 'pr_creation_receipts' else info['rows'][table]
                    assert_rows_preserved(db, table, expected)
                assert_rows_preserved(db, 'api_events', info['operation_events'],
                    where='WHERE resource_id=?', args=(done['operation_id'],), allow_append=True)
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
    assert result['seconds'] <= PUBLICATION_CASE_BUDGET_S, (
        f'Added packaged publication case exceeded its {PUBLICATION_CASE_BUDGET_S}-second budget')
    return result


if os.environ.get('BATC_UPGRADE_SEED_MODE') == '1':
    # Explicit pytest node only. PYTHONPATH selects the original product source;
    # pytest imports the supporting tests package from this checkout. The audit
    # plugin records both origins; --noconftest disables automatic fixture loading.
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
        producer = continuity['seed_publication']
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
            'stop_hold_refuses_ensure_without_spawn_or_lease': True, 'explicit_resume_reuses_one_owner': True,
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
