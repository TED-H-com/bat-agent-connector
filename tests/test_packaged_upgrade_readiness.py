"""A packaged service belongs to its spawned bootloader, not a stale pointer."""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import runpy
import subprocess
import sys
import urllib.error
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest

from bat_agent_connector import platform_files


@pytest.fixture
def upgrade(monkeypatch):
    script = Path(__file__).resolve().parents[1] / 'desktop/tests/managed-upgrade-fixture.py'
    monkeypatch.syspath_prepend(str(script.parent))
    return runpy.run_path(str(script))


def put_pointer(root, pid, endpoint='http://127.0.0.1:45123/rpc'):
    platform_files.ensure_private_directory(root / 'state')
    platform_files.atomic_write(root / 'state/task-service.json', json.dumps({'pid': pid, 'endpoint': endpoint}).encode())


@pytest.mark.parametrize('code', [5, 32])
def test_pointer_refusal_uses_production_reader_once_with_winerror(upgrade, tmp_path, monkeypatch, code):
    put_pointer(tmp_path, 42)
    calls = []
    error = PermissionError('native pointer refusal')
    error.winerror = code

    def read(path):
        calls.append(path)
        raise error

    monkeypatch.setattr(platform_files, 'read_private', read)
    monkeypatch.setitem(upgrade['owned_launch'].__globals__, 'request',
                        lambda *_a, **_kw: pytest.fail('Unreadable pointer must not contact a service'))
    with pytest.raises(OSError, match=f'winerror={code}') as caught:
        upgrade['wait_owned_launch'](tmp_path, SimpleNamespace(pid=42, poll=lambda: None), {}, timeout=45)
    assert caught.value.__cause__ is error
    assert calls == [tmp_path / 'state/task-service.json']


def test_owned_launch_accepts_the_bootloaders_python_child(upgrade, tmp_path, monkeypatch):
    program = """import subprocess, sys
child = subprocess.Popen([sys.executable, '-c', 'import sys; sys.stdin.buffer.read()'], stdin=subprocess.PIPE)
print(child.pid, flush=True)
try:
    sys.stdin.buffer.read()
finally:
    child.stdin.close()
    child.wait(timeout=10)
"""
    options = {'creationflags': subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == 'nt' else {'start_new_session': True}
    with subprocess.Popen([sys.executable, '-u', '-c', program], stdin=subprocess.PIPE,
                          stdout=subprocess.PIPE, text=True, **options) as child:
        try:
            inner_pid = int(child.stdout.readline())
            assert inner_pid != child.pid
            put_pointer(tmp_path, inner_pid)
            (tmp_path / 'installation.key').write_bytes(b'fixture-key')
            requests = []

            def proof(launch, path, **kwargs):
                requests.append(kwargs)
                challenge = parse_qs(urlsplit(path).query)['challenge'][0]
                payload = json.dumps([1, challenge, launch['endpoint'], 'fixture-server'], separators=(',', ':')).encode()
                return {'proof': hmac.new(b'fixture-key', payload, hashlib.sha256).hexdigest()}

            monkeypatch.setitem(upgrade['owned_launch'].__globals__, 'request', proof)
            original = {'server_id': 'fixture-server', 'token': 'fixture-token'}
            launch = upgrade['owned_launch'](tmp_path, child, original)
            assert launch == {**original, 'endpoint': 'http://127.0.0.1:45123'}
            assert requests == [{'authenticated': False}]
        finally:
            child.stdin.close()
            child.stdin = None
            child.wait(timeout=10)


@pytest.mark.parametrize('stale', ['live', 'stopped'])
def test_owned_launch_rejects_a_stale_pid_without_contact(upgrade, tmp_path, monkeypatch, stale):
    pid = os.getpid()
    if stale == 'stopped':
        with subprocess.Popen([sys.executable, '-c', 'pass']) as previous:
            previous.wait(timeout=10)
            pid = previous.pid
    put_pointer(tmp_path, pid)

    def unexpected(*args, **kwargs):
        pytest.fail('A stale pointer must not receive even an unauthenticated probe')

    monkeypatch.setitem(upgrade['owned_launch'].__globals__, 'request', unexpected)
    # A stopped previous service is outside the fresh child's private session/tree.
    options = {'creationflags': subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == 'nt' else {'start_new_session': True}
    with subprocess.Popen([sys.executable, '-c', 'import sys; sys.stdin.buffer.read()'],
                          stdin=subprocess.PIPE, **options) as child:
        try:
            assert child.pid != pid
            with pytest.raises(ValueError, match='not listening yet'):
                upgrade['owned_launch'](tmp_path, child, {})
        finally:
            child.stdin.close()
            child.stdin = None
            child.wait(timeout=10)


@pytest.mark.parametrize('endpoint', ['http://127.0.0.1:18796/rpc',
    'http://127.0.0.1:invalid/rpc', 'http://127.0.0.1:45123/wrong'])
def test_owned_launch_waits_for_usable_endpoint_without_contact(upgrade, tmp_path, monkeypatch, endpoint):
    put_pointer(tmp_path, os.getpid(), endpoint)

    def unexpected(*args, **kwargs):
        pytest.fail('The fixed operator port must never receive a fixture probe')

    monkeypatch.setitem(upgrade['owned_launch'].__globals__, 'request', unexpected)
    with pytest.raises(upgrade['OwnerNotReady'], match='Invalid owned fixture endpoint'):
        upgrade['owned_launch'](tmp_path, SimpleNamespace(pid=os.getpid()), {})


def test_wait_owned_launch_only_waits_for_pointer_readiness(upgrade, monkeypatch):
    attempts, pauses = [], []
    expected = {'endpoint': 'http://127.0.0.1:45123'}

    def launch(*args):
        attempts.append(1)
        if len(attempts) < 3:
            raise upgrade['OwnerNotReady']('not listening yet')
        return expected

    scope = upgrade['wait_owned_launch'].__globals__
    monkeypatch.setitem(scope, 'owned_launch', launch)
    monkeypatch.setitem(scope, 'time', SimpleNamespace(monotonic=lambda: 0, sleep=pauses.append))
    assert upgrade['wait_owned_launch'](None, SimpleNamespace(poll=lambda: None), {}, timeout=45) == expected
    assert len(attempts) == 3 and pauses == [0.1, 0.1]


@pytest.mark.parametrize('failure', [ConnectionRefusedError('fixture'),
    urllib.error.URLError('fixture'), ValueError('Owned fixture service identity could not be verified')])
def test_wait_owned_launch_does_not_retry_connection_or_proof_failure(upgrade, monkeypatch, failure):
    attempts = []

    def launch(*args):
        attempts.append(1)
        raise failure

    scope = upgrade['wait_owned_launch'].__globals__
    monkeypatch.setitem(scope, 'owned_launch', launch)
    monkeypatch.setitem(scope, 'time', SimpleNamespace(monotonic=lambda: 0, sleep=lambda _: pytest.fail('Unexpected retry')))
    with pytest.raises(type(failure), match='fixture|identity'):
        upgrade['wait_owned_launch'](None, SimpleNamespace(poll=lambda: None), {}, timeout=45)
    assert attempts == [1]


@pytest.mark.parametrize('parents,pid,expected', [({101: 100}, 101, True),
    ({102: 101, 101: 100}, 102, True), ({77: 1}, 77, False), ({}, 77, False),
    ({77: 78, 78: 77}, 77, False), ({}, True, False)])
def test_windows_ancestry_requires_the_spawned_parent_chain(upgrade, monkeypatch, parents, pid, expected):
    # Controlled Toolhelp parent map; the Win32 API itself remains native CI evidence.
    scope = upgrade['owned_pid'].__globals__
    monkeypatch.setitem(scope, 'os', SimpleNamespace(name='nt'))
    monkeypatch.setitem(scope, 'windows_process_parents', lambda: parents)
    assert upgrade['owned_pid'](SimpleNamespace(pid=100), pid) is expected
