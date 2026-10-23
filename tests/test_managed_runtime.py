"""Real isolated service ownership, credential recovery and safe failure boundaries."""
import concurrent.futures
import copy
import json
import os
import socket
import sqlite3
import sys
import time
from pathlib import Path

import pytest

from bat_agent_connector import managed_runtime as runtime
from desktop.tests.managed_runtime_assertions import assert_stopped_reply, stable_launch


def test_stable_launch_ignores_observation_clocks_without_mutating_replies():
    reply = json.loads((Path(__file__).parent / "fixtures/managed-runtime-identity.json").read_text())["differs"]
    original = copy.deepcopy(reply)
    later = copy.deepcopy(reply)
    for field in ("bundled_runtime", "running_runtime"):
        later[field]["observed_at"] += 1
    expected_later = copy.deepcopy(later)
    assert reply != later
    assert stable_launch(reply) == stable_launch(later)
    assert reply == original and later == expected_later


def test_stable_launch_preserves_every_other_producer_field():
    golden = json.loads((Path(__file__).parent / "fixtures/managed-runtime-identity.json").read_text())

    def paths(value, prefix=()):
        for key, item in value.items():
            path = (*prefix, key)
            yield path
            if isinstance(item, dict):
                yield from paths(item, path)

    clocks = {("bundled_runtime", "observed_at"), ("running_runtime", "observed_at")}
    for name, reply in golden.items():
        for path in paths(reply):
            if path in clocks:
                continue
            changed = copy.deepcopy(reply)
            parent = changed
            for key in path[:-1]:
                parent = parent[key]
            del parent[path[-1]]
            assert stable_launch(changed) != stable_launch(reply), (name, path)


@pytest.fixture
def installation(tmp_path, monkeypatch):
    root = tmp_path / "installed"
    children = []
    original = runtime._spawn

    def spawn(path):
        child = original(path)
        children.append(child)
        return child

    monkeypatch.setattr(runtime, "_spawn", spawn)
    yield root, children
    for child in children:
        if child.poll() is None:
            child.terminate()
            try:
                child.wait(timeout=10)
            except Exception:
                child.kill()
                child.wait(timeout=10)


def test_installed_runtime_reuses_identity_and_recovers_after_restart(installation):
    root, children = installation
    first = runtime.ensure(root, timeout=15)
    second = runtime.ensure(root, timeout=5)
    assert stable_launch(first) == stable_launch(second) and len(children) == 1
    for field in ("bundled_runtime", "running_runtime"):
        assert second[field]["observed_at"] >= first[field]["observed_at"]
    assert first["token"].startswith("batc_")
    assert first["actor"].startswith("desktop-")
    # Service continues independently of launcher/client; its ordinary API is authoritative.
    bootstrap = runtime._request(first["endpoint"], "/api/v1/bootstrap", token=first["token"])
    assert bootstrap["sync"]["server_id"] == first["server_id"]
    assert bootstrap["sync"]["principal_id"] == first["principal_id"]
    stopped = runtime._request(first["endpoint"], "/api/v1/managed/stop", token=first["token"], body={})
    assert stopped == {"protocol": 1, "stopped": True}
    children[0].wait(timeout=10)
    third = runtime.ensure(root, timeout=15)
    assert {k: third[k] for k in ("actor", "token", "server_id", "principal_id")} == {
        k: first[k] for k in ("actor", "token", "server_id", "principal_id")}


def test_invalid_manifest_and_existing_unclaimed_data_never_spawn(installation):
    root, children = installation
    root.mkdir(mode=0o700)
    (root / "state").mkdir()
    with pytest.raises(ValueError, match="unclaimed"):
        runtime.ensure(root)
    assert not children and not (root / runtime.MANIFEST).exists()
    (root / "state").rmdir()
    runtime._write(root / runtime.MANIFEST, {"protocol": 1, "data_dir": str(root)})
    with pytest.raises(ValueError, match="manifest"):
        runtime.ensure(root)
    assert not children


def test_revoked_identity_never_gets_replaced(installation):
    root, children = installation
    first = runtime.ensure(root, timeout=15)
    runtime._request(first["endpoint"], "/api/v1/managed/stop", token=first["token"], body={})
    children[0].wait(timeout=10)
    import sqlite3
    with sqlite3.connect(root / "state" / "tasks.sqlite3") as db:
        db.execute("UPDATE api_principals SET revoked_at=?", (time.time(),))
    with pytest.raises(ValueError, match="initialization failed"):
        runtime.ensure(root, timeout=10)
    assert (root / "identity.token").read_text() == first["token"]
    assert json.loads((root / runtime.MANIFEST).read_text())["server_id"] == first["server_id"]


def test_occupied_default_port_is_not_adopted(installation):
    root, _ = installation
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        first = runtime.ensure(root, timeout=15)
        assert first["endpoint"] != f"http://127.0.0.1:{listener.getsockname()[1]}"


@pytest.mark.skipif(os.name == "nt", reason="native reparse coverage lives in Windows platform fixtures")
def test_symlinked_installation_is_not_adopted(tmp_path):
    target = tmp_path / "target"
    target.mkdir(mode=0o700)
    link = tmp_path / "alias"
    link.symlink_to(target, target_is_directory=True)
    with pytest.raises(ValueError, match="symbolic"):
        runtime.ensure(link)
    assert not list(target.iterdir())


@pytest.fixture
def versioned_installation(installation, monkeypatch):
    # A real child daemon, with the version a different installed package would
    # contain. No version override exists in the product's runtime protocol.
    def command(root):
        source = ("import asyncio, sys; from bat_agent_connector import managed_runtime as r; "
                  f"r.__version__ = {runtime.__version__!r}; asyncio.run(r.serve(sys.argv[1]))")
        return [sys.executable, "-c", source, str(root)]
    monkeypatch.setattr(runtime, "_service_command", command)
    monkeypatch.setattr(runtime, "__version__", "1.0.0")
    return installation


def test_new_package_upgrades_owned_idle_service_preserving_identity_and_history(versioned_installation, monkeypatch):
    root, children = versioned_installation
    original = runtime.ensure(root, timeout=15)
    # History already in the sole journal must survive the replacement process.
    with sqlite3.connect(root / "state" / "tasks.sqlite3") as db:
        db.execute("INSERT INTO api_events(resource_type,resource_id,kind,body,created_at) VALUES(?,?,?,?,?)",
                   ("fixture", "saved-event", "retained", "{}", time.time()))
        sequence = db.execute("SELECT seq FROM api_events WHERE resource_id='saved-event'").fetchone()[0]
    monkeypatch.setattr(runtime, "__version__", "1.1.0")
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as workers:
        replies = list(workers.map(lambda _: runtime.ensure(root, timeout=15), range(3)))
    upgraded = replies[0]
    assert all(stable_launch(reply) == stable_launch(upgraded) for reply in replies)
    assert upgraded["runtime_version"] == "1.1.0"
    assert len(children) == 2
    children[0].wait(timeout=10)
    assert {k: upgraded[k] for k in ("actor", "token", "server_id", "principal_id")} == {
        k: original[k] for k in ("actor", "token", "server_id", "principal_id")}
    with sqlite3.connect(root / "state" / "tasks.sqlite3") as db:
        assert db.execute("SELECT seq FROM api_events WHERE resource_id='saved-event'").fetchone()[0] == sequence
    assert stable_launch(runtime.ensure(root, timeout=5)) == stable_launch(upgraded) and len(children) == 2


def test_busy_upgrade_preserves_original_owner_and_uncertain_operation(versioned_installation, monkeypatch):
    root, children = versioned_installation
    original = runtime.ensure(root, timeout=15)
    # A saved uncertain effect cannot be retried or discarded to make upgrading
    # convenient. Keep it out of the scheduler's due window in this fixture.
    with sqlite3.connect(root / "state" / "tasks.sqlite3") as db:
        db.execute("""INSERT INTO operations(operation_id,actor,entry,idem_key,request_hash,action,
                   target,params,preconditions,status,next_run_at,created_at,updated_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                   ("uncertain-fixture", original["actor"], "fixture", "original-key", "fixed-hash",
                    "session.send", "{}", "{}", "{}", "uncertain", time.time() + 3600, time.time(), time.time()))
    monkeypatch.setattr(runtime, "__version__", "1.1.0")
    with pytest.raises(ValueError, match="busy"):
        runtime.ensure(root, timeout=5)
    assert len(children) == 1 and children[0].poll() is None
    assert stable_launch(runtime._verify(root, runtime._manifest(root))) == stable_launch(original)
    with sqlite3.connect(root / "state" / "tasks.sqlite3") as db:
        assert db.execute("SELECT status,idem_key FROM operations WHERE operation_id='uncertain-fixture'").fetchone() == (
            "uncertain", "original-key")


def test_older_package_never_downgrades_a_running_service(versioned_installation, monkeypatch):
    root, children = versioned_installation
    original = runtime.ensure(root, timeout=15)
    monkeypatch.setattr(runtime, "__version__", "0.9.0")
    with pytest.raises(ValueError, match="newer"):
        runtime.ensure(root, timeout=5)
    assert len(children) == 1 and children[0].poll() is None
    assert stable_launch(runtime._verify(root, runtime._manifest(root))) == stable_launch(original)


def test_older_package_never_downgrades_a_stopped_installation(versioned_installation, monkeypatch):
    root, children = versioned_installation
    runtime.ensure(root, timeout=15)
    launch = runtime._verify(root, runtime._manifest(root))
    runtime._request(launch["endpoint"], "/api/v1/managed/stop", token=launch["token"], body={})
    children[0].wait(timeout=10)
    manifest = (root / runtime.MANIFEST).read_bytes()
    monkeypatch.setattr(runtime, "__version__", "0.9.0")
    with pytest.raises(ValueError, match="newer"):
        runtime.ensure(root, timeout=5)
    assert len(children) == 1 and (root / runtime.MANIFEST).read_bytes() == manifest


def test_interrupted_upgrade_recovers_original_installation(versioned_installation, monkeypatch):
    root, children = versioned_installation
    original = runtime.ensure(root, timeout=15)
    monkeypatch.setattr(runtime, "__version__", "1.1.0")
    spawn = runtime._spawn
    def interrupted(_root):
        raise OSError("fixture failed to launch replacement")
    monkeypatch.setattr(runtime, "_spawn", interrupted)
    with pytest.raises(OSError, match="replacement"):
        runtime.ensure(root, timeout=15)
    children[0].wait(timeout=10)
    assert runtime._manifest(root)["state"] == "ready"
    assert runtime._manifest(root)["runtime_version"] == "1.0.0"
    monkeypatch.setattr(runtime, "_spawn", spawn)
    recovered = runtime.ensure(root, timeout=15)
    assert recovered["runtime_version"] == "1.1.0" and len(children) == 2
    assert {k: recovered[k] for k in ("actor", "token", "server_id", "principal_id")} == {
        k: original[k] for k in ("actor", "token", "server_id", "principal_id")}


def test_ensure_restarts_dead_service_with_new_endpoint_and_original_identity(installation):
    root, children = installation
    first = runtime.ensure(root, timeout=15)
    # Terminate only our fixture child, never a PID from the endpoint pointer.
    children[0].terminate()
    children[0].wait(timeout=10)
    # Occupy the old port so the OS cannot coincidentally reuse it for port=0.
    from urllib.parse import urlsplit
    with socket.socket() as occupied:
        occupied.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        occupied.bind(("127.0.0.1", urlsplit(first["endpoint"]).port))
        occupied.listen()
        recovered = runtime.ensure(root, timeout=15)
    assert len(children) == 2
    assert recovered["endpoint"] != first["endpoint"]
    for key in ("server_id", "principal_id", "actor", "token", "runtime_version"):
        assert recovered[key] == first[key]
    assert runtime._request(recovered["endpoint"], "/api/v1/bootstrap", token=recovered["token"])["sync"]["server_id"] == first["server_id"]


def hold(root):
    return runtime._json(root / 'state' / 'lifecycle.json')


def test_stop_hold_precedes_real_request_and_explicit_resume_starts_once(installation, monkeypatch):
    root, children = installation
    runtime.ensure(root, timeout=15)
    request = runtime._request
    def observed(endpoint, path, **kwargs):
        if path == '/api/v1/managed/stop':
            assert hold(root)['state'] == 'stop_pending'
            assert len(children) == 1
        return request(endpoint, path, **kwargs)
    monkeypatch.setattr(runtime, '_request', observed)
    result = runtime.stop(root)
    assert result['lifecycle']['state'] == 'stopped'
    assert hold(root)['stopped_at'] >= hold(root)['requested_at']
    children[0].wait(timeout=10)
    with pytest.raises(runtime.LifecycleRefusal) as refused:
        runtime.ensure(root)
    assert refused.value.code == 'SERVICE_STOPPED'
    assert len(children) == 1
    runtime.resume(root, timeout=15)
    assert not (root / 'state' / 'lifecycle.json').exists()
    assert len(children) == 2


def test_busy_stop_removes_hold_and_keeps_real_owner(installation):
    root, children = installation
    launch = runtime.ensure(root, timeout=15)
    with sqlite3.connect(root / 'state' / 'tasks.sqlite3') as db:
        db.execute('''INSERT INTO operations(operation_id,actor,entry,idem_key,request_hash,action,
            target,params,preconditions,status,next_run_at,created_at,updated_at)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)''', ('stop-busy', launch['actor'], 'fixture', 'stop-key', 'hash',
            'session.send', '{}', '{}', '{}', 'uncertain', time.time()+3600, time.time(), time.time()))
    with pytest.raises(runtime.LifecycleRefusal) as refused:
        runtime.stop(root)
    assert refused.value.code == 'SERVICE_BUSY'
    assert str(refused.value) == 'finish or reconcile current operations before stopping'
    assert not (root / 'state' / 'lifecycle.json').exists()
    assert len(children) == 1 and children[0].poll() is None
    runtime.ensure(root)
    assert len(children) == 1


@pytest.fixture
def slow_stopping_installation(installation, monkeypatch):
    # Real /managed/stop route and real kernel lease, but the fixture owner does
    # not act on its shutdown signal. No product busy gate or lease is mocked.
    def command(root):
        source = '''import asyncio, sys
from types import SimpleNamespace
from pathlib import Path
from bat_agent_connector import api_v1, task_daemon, managed_runtime as r
handle = api_v1.ApiV1.handle
async def counted(self, method, target, headers, reader, writer):
    if target == '/api/v1/managed/stop':
        with (Path(sys.argv[1]) / 'stop-requests').open('a') as log:
            log.write('stop\\n')
    await handle(self, method, target, headers, reader, writer)
api_v1.ApiV1.handle = counted
def signal():
    with (Path(sys.argv[1]) / 'stop-signals').open('a') as log:
        log.write('stop\\n')
original = task_daemon.TaskDaemon.serve
async def slow(self, *args, **kwargs):
    self.managed_stop = SimpleNamespace(set=signal)
    await original(self, *args, **kwargs)
task_daemon.TaskDaemon.serve = slow
asyncio.run(r.serve(sys.argv[1]))
'''
        return [sys.executable, '-c', source, str(root)]
    monkeypatch.setattr(runtime, '_service_command', command)
    return installation


def test_ack_without_exit_stays_pending_until_lease_frees(slow_stopping_installation):
    root, children = slow_stopping_installation
    runtime.ensure(root, timeout=15)
    result = runtime.stop(root, timeout=0.2)
    assert result['lifecycle']['state'] == 'stop_pending'
    before = (root / 'state' / 'lifecycle.json').read_bytes()
    assert runtime.lifecycle(root)['state'] == 'stop_pending'
    assert (root / 'state' / 'lifecycle.json').read_bytes() == before
    with pytest.raises(runtime.LifecycleRefusal) as refused:
        runtime.ensure(root)
    assert refused.value.code == 'SERVICE_STOPPED'
    with pytest.raises(runtime.LifecycleRefusal) as refused:
        runtime.resume(root)
    assert refused.value.code == 'SERVICE_STILL_STOPPING'
    assert len(children) == 1 and hold(root)['state'] == 'stop_pending'
    children[0].terminate()  # our fixture child only
    children[0].wait(timeout=10)
    assert runtime.lifecycle(root)['state'] == 'stop_pending'  # read never writes
    with pytest.raises(runtime.LifecycleRefusal):
        runtime.ensure(root)
    assert hold(root)['state'] == 'stopped'
    runtime.resume(root, timeout=15)
    assert len(children) == 2


def test_lost_stop_reply_keeps_pending(slow_stopping_installation, monkeypatch):
    root, children = slow_stopping_installation
    runtime.ensure(root, timeout=15)
    request = runtime._request
    def lost(endpoint, path, **kwargs):
        result = request(endpoint, path, **kwargs)
        if path == '/api/v1/managed/stop':
            raise OSError('fixture lost acknowledgement')
        return result
    monkeypatch.setattr(runtime, '_request', lost)
    assert runtime.stop(root)['lifecycle']['state'] == 'stop_pending'
    with pytest.raises(runtime.LifecycleRefusal):
        runtime.ensure(root)
    assert len(children) == 1


@pytest.mark.parametrize('command', ['ensure', 'browser'])
def test_cli_hold_never_starts(installation, monkeypatch, capsys, command):
    root, children = installation
    runtime.ensure(root, timeout=15)
    stopped = runtime.stop(root)
    children[0].wait(timeout=10)
    monkeypatch.setattr(sys, 'argv', ['runtime', command, '--data-dir', str(root)])
    with pytest.raises(SystemExit) as exited:
        runtime.main()
    failure = json.loads(capsys.readouterr().out)
    assert failure['error'] == 'SERVICE_STOPPED'
    assert failure['lifecycle']['state'] == 'stopped'
    assert_stopped_reply(exited.value.code, failure, stopped['lifecycle'])
    assert len(children) == 1


def test_ensure_and_stop_serialize_before_stop_request(installation, monkeypatch):
    import threading
    root, children = installation
    runtime.ensure(root, timeout=15)
    entered, release = threading.Event(), threading.Event()
    request = runtime._request
    def paused(endpoint, path, **kwargs):
        if path == '/api/v1/managed/stop':
            entered.set()
            assert release.wait(5)
        return request(endpoint, path, **kwargs)
    monkeypatch.setattr(runtime, '_request', paused)
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as workers:
        stopping = workers.submit(runtime.stop, root)
        assert entered.wait(5)
        ensuring = workers.submit(runtime.ensure, root)
        assert not ensuring.done()
        release.set()
        assert stopping.result()['lifecycle']['state'] == 'stopped'
        with pytest.raises(runtime.LifecycleRefusal):
            ensuring.result()
    assert len(children) == 1


@pytest.mark.parametrize('invalid', ['symlink', 'foreign', 'version', 'bytes', 'state'])
def test_untrusted_hold_refuses_start_and_resume(installation, monkeypatch, invalid):
    from bat_agent_connector import platform_files
    if os.name == 'nt' and invalid in ('foreign', 'symlink'):
        pytest.skip('POSIX owner/symlink injection; Windows private-handle fixtures cover ACL/reparse checks')
    root, children = installation
    runtime.ensure(root, timeout=15)
    runtime.stop(root)
    children[0].wait(timeout=10)
    path = root / 'state' / 'lifecycle.json'
    if invalid == 'symlink':
        path.unlink()
        path.symlink_to(root / 'missing')
    elif invalid == 'foreign':
        # Exercise actual private-file owner validation with a leaf-specific stat
        # result, without globally monkeypatching any stdlib class.
        original = platform_files._check_private_stat
        def foreign(info, **kwargs):
            if not kwargs.get('directory') and info.st_ino == path.stat().st_ino:
                info = os.stat_result((*info[:4], info.st_uid + 1, *info[5:]))
            return original(info, **kwargs)
        monkeypatch.setattr(platform_files, '_check_private_stat', foreign)
    elif invalid == 'version':
        runtime._write(path, {**hold(root), 'version': 999})
    elif invalid == 'state':
        runtime._write(path, {**hold(root), 'state': []})
    else:
        path.write_bytes(b'\xff')
    for action in (runtime.ensure, runtime.resume):
        with pytest.raises(runtime.LifecycleRefusal) as refused:
            action(root)
        assert refused.value.code == 'SERVICE_LIFECYCLE_UNPROVEN'
    assert len(children) == 1


@pytest.fixture
def counted_installation(installation, monkeypatch):
    def command(root):
        source = '''import asyncio, sys
from pathlib import Path
from bat_agent_connector import api_v1, managed_runtime as r
original = api_v1.ApiV1.handle
async def counted(self, method, target, headers, reader, writer):
    if target == '/api/v1/managed/stop':
        with (Path(sys.argv[1]) / 'stop-requests').open('a') as log:
            log.write('stop\\n')
    await original(self, method, target, headers, reader, writer)
api_v1.ApiV1.handle = counted
asyncio.run(r.serve(sys.argv[1]))
'''
        return [sys.executable, '-c', source, str(root)]
    monkeypatch.setattr(runtime, '_service_command', command)
    return installation


def test_explicit_stop_can_resend_after_connection_failure(counted_installation, monkeypatch):
    import urllib.error
    root, children = counted_installation
    runtime.ensure(root, timeout=15)
    request = runtime._request
    attempts = []
    def disconnect_once(endpoint, path, **kwargs):
        if path == '/api/v1/managed/stop':
            attempts.append(path)
            if len(attempts) == 1:
                raise urllib.error.URLError('fixture disconnected before delivery')
        return request(endpoint, path, **kwargs)
    monkeypatch.setattr(runtime, '_request', disconnect_once)
    assert runtime.stop(root)['lifecycle']['state'] == 'stop_pending'
    assert runtime._running(root) and children[0].poll() is None
    assert not (root / 'stop-requests').exists()
    assert runtime.stop(root)['lifecycle']['state'] == 'stopped'
    children[0].wait(timeout=10)
    assert (root / 'stop-requests').read_text() == 'stop\n'
    assert len(attempts) == 2 and len(children) == 1


@pytest.mark.parametrize('status', [401, 403, 405])
@pytest.mark.parametrize('pending', [False, True])
def test_definitive_stop_refusal_clears_hold(counted_installation, monkeypatch, status, pending):
    from bat_agent_connector import api_auth
    root, children = counted_installation
    runtime.ensure(root, timeout=15)
    other = 'unknown'
    if status == 403:
        with sqlite3.connect(root / 'state/tasks.sqlite3') as db:
            other = api_auth.issue(db, 'different-owner', api_auth.SCOPES)
    request = runtime._request
    def refused(endpoint, path, **kwargs):
        if path == '/api/v1/managed/stop':
            if status == 405:
                kwargs['body'] = None
            else:
                kwargs['token'] = other
        return request(endpoint, path, **kwargs)
    monkeypatch.setattr(runtime, '_request', refused)
    # A pending hold from a previous uncertain attempt is also cleared by refusal.
    if pending:
        runtime._write(root / 'state/lifecycle.json',
                       {'version': 1, 'state': 'stop_pending', 'requested_at': time.time(), 'stopped_at': None})
    with pytest.raises(runtime.LifecycleRefusal) as result:
        runtime.stop(root)
    assert result.value.code == 'SERVICE_STOP_REFUSED'
    assert result.value.lifecycle['state'] == 'running'
    assert not (root / 'state/lifecycle.json').exists()
    assert runtime._running(root) and children[0].poll() is None
    assert (root / 'stop-requests').read_text() == 'stop\n'
    runtime.ensure(root)
    assert len(children) == 1


def test_repeated_stop_route_acknowledges_without_busy_gate(slow_stopping_installation):
    root, children = slow_stopping_installation
    launch = runtime.ensure(root, timeout=15)
    assert runtime.stop(root, timeout=0.2)['lifecycle']['state'] == 'stop_pending'
    with sqlite3.connect(root / 'state/tasks.sqlite3') as db:
        db.execute('''INSERT INTO operations(operation_id,actor,entry,idem_key,request_hash,action,
            target,params,preconditions,status,next_run_at,created_at,updated_at)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)''', ('wind-down', launch['actor'], 'fixture', 'key', 'hash',
            'session.send', '{}', '{}', '{}', 'uncertain', time.time()+3600, time.time(), time.time()))
    assert runtime._request(launch['endpoint'], '/api/v1/managed/stop', token=launch['token'], body={}) == {
        'protocol': 1, 'stopped': True}
    import urllib.error
    # Idempotent acknowledgement still requires the same authenticated owner;
    # the existing fence on other POST effects remains active during shutdown.
    with pytest.raises(urllib.error.HTTPError) as refused:
        runtime._request(launch['endpoint'], '/api/v1/managed/stop', token='unknown', body={})
    assert refused.value.code == 401
    with pytest.raises(urllib.error.HTTPError) as fenced:
        runtime._request(launch['endpoint'], '/api/v1/managed/browser-file', token=launch['token'], body={})
    assert fenced.value.code == 503
    assert (root / 'stop-requests').read_text() == 'stop\nstop\nstop\n'
    assert (root / 'stop-signals').read_text() == 'stop\n'
    assert runtime._running(root) and len(children) == 1


def test_held_reads_and_resume_do_not_send_stop(slow_stopping_installation, monkeypatch, capsys):
    root, children = slow_stopping_installation
    runtime.ensure(root, timeout=15)
    runtime.stop(root, timeout=0.2)
    assert (root / 'stop-requests').read_text() == 'stop\n'
    for action in (runtime.ensure, runtime.resume):
        with pytest.raises(runtime.LifecycleRefusal):
            action(root)
    monkeypatch.setattr(sys, 'argv', ['runtime', 'status', '--data-dir', str(root)])
    runtime.main()
    assert json.loads(capsys.readouterr().out)['lifecycle']['state'] == 'stop_pending'
    assert (root / 'stop-requests').read_text() == 'stop\n'
    assert len(children) == 1


def test_unlisted_stop_busy_reason_uses_stopping_fallback(installation, monkeypatch):
    import io
    import urllib.error
    root, children = installation
    runtime.ensure(root, timeout=15)
    request = runtime._request
    def unknown_busy(endpoint, path, **kwargs):
        if path == '/api/v1/managed/stop':
            raise urllib.error.HTTPError(endpoint + path, 409, 'Conflict', {},
                io.BytesIO(b'{"error":{"code":"SERVICE_BUSY","message":"unlisted private text"}}'))
        return request(endpoint, path, **kwargs)
    monkeypatch.setattr(runtime, '_request', unknown_busy)
    with pytest.raises(runtime.LifecycleRefusal) as result:
        runtime.stop(root)
    assert result.value.code == 'SERVICE_BUSY'
    assert str(result.value) == 'owned service is busy; finish or reconcile current work before stopping'
    assert 'private' not in str(result.value)
    assert not (root / 'state/lifecycle.json').exists()
    assert runtime._running(root) and len(children) == 1
