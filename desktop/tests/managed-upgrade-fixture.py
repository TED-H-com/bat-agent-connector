"""Upgrade two distinct packaged cores using disposable, disk-backed state.

Both resource directories must contain the original build manifest/runtime.gz.
This fixture never installs an app or connects to BAT, GitHub or a model. Package
versions and digests are evidence; relabelling an equal-version binary is refused.
"""
from __future__ import annotations

import argparse
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
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path


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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--previous-resources', type=Path, required=True)
    parser.add_argument('--current-resources', type=Path, required=True)
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
        receipt = {'status': 'passed', 'evidence_level': 'packaged-core-upgrade-fixture', 'live_accepted': False,
            'previous': {key: old[key] for key in ('runtime_version', 'sha256')},
            'current': {key: new[key] for key in ('runtime_version', 'sha256')},
            'platform': new['platform'], 'architecture': new['architecture'], 'no_python_on_child_path': True,
            'stable_identity_and_workspace': True, 'original_operation_key_and_history': True,
            'concurrent_upgrade_clients': 3, 'new_version_read_back': True, 'downgrade_refused': True,
            'uncertain_effect_blocks_upgrade': True, 'unknown_key_and_old_owner_retained': True,
            'task_owned_processes_stopped': True, 'fixture_directory_removed': True}
    finally:
        # If a case fails, never remove data underneath a surviving service.
        # The explicit busy-fixture child is already reaped in its own finally.
        if 'current' in locals() and 'env' in locals():
            for name in ('idle-installation', 'busy-installation'):
                data = root / name
                if (data / 'installation.json').exists():
                    assert command(current, data, env, action='stop')['stopped'] is True
        remove(root)
    print(json.dumps(receipt))


if __name__ == '__main__':
    main()
