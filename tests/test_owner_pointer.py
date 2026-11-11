"""Ownership is discoverable before listening; an address is not readiness proof."""
from __future__ import annotations

import asyncio
import json
import os
import runpy
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlsplit

import pytest

from bat_agent_connector import (
    api_auth,
    dashboard_sync,
    managed_runtime,
    platform_files,
    registry,
    service,
    task_daemon,
)
from bat_agent_connector.browser_sessions import BrowserSessions
from bat_agent_connector.errors import OwnerConflict, TaskControlRefused
from bat_agent_connector.fleet import Fleet
from bat_agent_connector.operations import OperationError
from bat_agent_connector.task_daemon import TaskDaemon
from tests.conftest import make_config
from tests.test_operations_unification import SID, owned

_ = owned  # shared real owned-session fixture


def pointer():
    return registry.registry_path().parent / service.TASK_SERVICE_POINTER


def test_embedder_publishes_ownership_without_an_endpoint(mock, tmp_path):
    daemon = TaskDaemon(make_config(mock), tmp_path / 'tasks.db')
    other = TaskDaemon(make_config(mock), tmp_path / 'other.db')
    try:
        # The lazy journal path acquires ownership without ever creating a listener.
        assert daemon.journal.path == tmp_path / 'tasks.db'
        state = json.loads(pointer().read_text())
        assert state['pid'] == os.getpid() and state['owner_id'] == daemon._owner_id
        assert state['db_path'] == str(daemon.journal.path.resolve())
        assert state['lease_path'] == str(pointer().parent / 'task-daemon.lock')
        assert daemon._owns_fleet()
        with pytest.raises(OwnerConflict) as conflict:
            other.acquire_owner()
        assert conflict.value.owner == state and not other._db_path.exists()
        assert 'endpoint' not in state
    finally:
        daemon.journal.close()
    assert not daemon._owns_fleet()
    assert 'endpoint' not in json.loads(pointer().read_text())


@pytest.mark.parametrize('host', ['127.0.0.1', 'localhost'])
async def test_endpoint_is_published_only_after_bind_and_withdrawn_before_close(mock, tmp_path, monkeypatch, host):
    daemon = TaskDaemon(make_config(mock), tmp_path / 'tasks.db')
    browser = BrowserSessions(daemon, tmp_path, 'fixture')
    binding, proceed, published = asyncio.Event(), asyncio.Event(), asyncio.Event()
    servers, withdrawals = [], []
    real_write = daemon._write_owner_pointer

    async def bind(*args, **kwargs):
        assert daemon._owns_fleet()  # lease and ownership precede the bind
        binding.set()
        await proceed.wait()
        server = await asyncio.start_server(*args, **kwargs)
        servers.append(server)
        return server

    def write():
        real_write()
        if servers:
            state = json.loads(pointer().read_text())
            if state.get('endpoint'):
                published.set()
            else:
                withdrawals.append(servers[0].is_serving())
                with pytest.raises(OperationError, match='not listening') as error:
                    _ = browser.origin
                assert error.value.code == 'TASK_OWNER_UNAVAILABLE'

    # Replace only this module's API reference, never a global stdlib object.
    monkeypatch.setattr(task_daemon, 'asyncio', SimpleNamespace(
        **{name: getattr(asyncio, name) for name in dir(asyncio) if name != 'start_server'}, start_server=bind))
    monkeypatch.setattr(daemon, '_write_owner_pointer', write)
    serving = asyncio.create_task(daemon.serve(host=host, port=0))
    try:
        await binding.wait()
        assert 'endpoint' not in json.loads(pointer().read_text())
        proceed.set()
        await published.wait()
        state = json.loads(pointer().read_text())
        bound_host, port, *_ = servers[0].sockets[0].getsockname()
        endpoint = urlsplit(state['endpoint'])
        assert endpoint.hostname == bound_host and endpoint.port == port and endpoint.path == '/rpc'
        assert browser.origin == state['endpoint'].removesuffix('/rpc')
        _reader, writer = await asyncio.open_connection(bound_host, port)
        writer.close()
        await writer.wait_closed()
    finally:
        serving.cancel()
        await asyncio.gather(serving, return_exceptions=True)
    assert withdrawals and all(withdrawals)
    assert serving.cancelled()
    assert 'endpoint' not in json.loads(pointer().read_text())
    assert not daemon._owns_fleet() and not servers[0].is_serving()
    with pytest.raises(OSError):
        await asyncio.open_connection(bound_host, port)


@pytest.mark.parametrize('reader', ['origin', 'proof'])
async def test_unserved_browser_origin_and_managed_proof_refuse_explicitly(tmp_path, monkeypatch, reader):
    observed = []

    class BeforeBind(TaskDaemon):
        async def serve(self, **kwargs):
            assert self._endpoint is None
            with pytest.raises(OperationError) as error:
                if reader == 'origin':
                    _ = self.api.browser_sessions.origin
                else:
                    self.managed_proof('a' * 64, '127.0.0.1:45123')
            assert error.value.code == 'TASK_OWNER_UNAVAILABLE' and error.value.status == 503
            observed.append(error.value.code)

    runtime = managed_runtime
    root = runtime._root(tmp_path / 'managed')
    runtime._prepare(root)
    for name in ('BATC_STATE_DIR', 'BATC_CONFIG_DIR', 'BATC_CONFIG'):
        monkeypatch.setenv(name, os.environ.get(name, ''))
    monkeypatch.setattr(task_daemon, 'TaskDaemon', BeforeBind)
    await runtime.serve(root)
    assert observed == ['TASK_OWNER_UNAVAILABLE']


async def test_shutdown_closes_an_accepted_request_before_closing_its_journal(mock, tmp_path, monkeypatch):
    daemon = TaskDaemon(make_config(mock), tmp_path / 'tasks.db')
    accepted, published, ended = asyncio.Event(), asyncio.Event(), asyncio.Event()
    held = []
    real_write = daemon._write_owner_pointer

    async def pending(*args):
        held.append((asyncio.current_task(), args[-1]))
        accepted.set()
        try:
            await asyncio.Future()  # a controlled long poll, never a timed sleep
        finally:
            assert daemon.journal.db.execute('SELECT 1').fetchone()[0] == 1
            assert daemon._owns_fleet() and daemon._endpoint is None
            assert 'endpoint' not in json.loads(pointer().read_text())
            ended.set()

    def write():
        real_write()
        if daemon._endpoint:
            published.set()

    monkeypatch.setattr(daemon.api, 'handle', pending)
    monkeypatch.setattr(daemon, '_write_owner_pointer', write)
    serving = asyncio.create_task(daemon.serve(port=0))
    writer = None
    try:
        await published.wait()
        port = urlsplit(daemon._endpoint).port
        reader, writer = await asyncio.open_connection('127.0.0.1', port)
        writer.write(f'GET /api/v1/version HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\n\r\n'.encode())
        await writer.drain()
        await accepted.wait()
        serving.cancel()
        # Shutdown must not wait for an unbounded client request or close its DB underneath it.
        await asyncio.wait_for(asyncio.shield(asyncio.gather(serving, return_exceptions=True)), 5)
        assert held[0][0].done() and ended.is_set()
        assert await asyncio.wait_for(reader.read(), 1) == b''  # clean transport failure, no generic 500
        assert 'endpoint' not in json.loads(pointer().read_text())
    finally:
        for task, connection in held:
            task.cancel()
            connection.close()
            await connection.wait_closed()
        if writer:
            writer.close()
            await writer.wait_closed()
        await asyncio.gather(*(task for task, _ in held), return_exceptions=True)
        if not serving.done():
            await asyncio.gather(serving, return_exceptions=True)


@pytest.mark.parametrize('endpoint', [None, ''])
async def test_forwarding_refuses_unready_owner_without_using_a_default(owned, monkeypatch, endpoint):
    daemon, _tid = owned
    state = json.loads(pointer().read_text())
    state.pop('endpoint', None)
    if endpoint is not None:
        state['endpoint'] = endpoint
    pointer().write_text(json.dumps(state))
    monkeypatch.setenv('BATC_API_TOKEN', 'fixture-caller-token')
    requests = []

    def capture(*args, **kwargs):
        requests.append(kwargs)
        # Observe forwarding without allowing any network or default-port access.
        raise ValueError('fixture transport must not be reached')

    monkeypatch.setattr(task_daemon, 'request', capture)
    fleet = Fleet(daemon.fleet.config, actor='independent-client')
    try:
        with pytest.raises(TaskControlRefused, match='TASK_OWNER_UNAVAILABLE'):
            await service.session_send(fleet, 'h1', SID, 'one instruction', confirm=True)
        assert requests == []
    finally:
        await fleet.close()


async def test_managed_ensure_waits_for_endpoint_and_proves_it_before_sending_bearer(mock, tmp_path, monkeypatch):
    runtime = managed_runtime
    root = runtime._root(tmp_path / 'managed')
    doc = runtime._prepare(root)
    monkeypatch.setenv('BATC_STATE_DIR', str(root / 'state'))
    daemon = TaskDaemon(make_config(mock), root / 'state/tasks.sqlite3')
    daemon.acquire_owner()
    token = api_auth.issue(daemon.journal.db, doc['actor'], api_auth.SCOPES)
    principal = api_auth.authenticate(daemon.journal.db, token, daemon._admin_token)
    doc.update(dashboard_sync.identity(daemon.journal, principal), state='ready')
    runtime._write(root / runtime.MANIFEST, doc)
    runtime.atomic_write(root / 'identity.token', token.encode())
    key = b'fixture-installation-proof-key'
    runtime.atomic_write(root / 'installation.key', key)
    requests, pauses = [], []
    endpoint = 'http://127.0.0.1:45123'

    def request(address, path, *, token=None, body=None):
        requests.append((address, path, token))
        assert pauses and address == endpoint, 'unready owner must not contact any endpoint'
        if path.startswith('/api/v1/managed/identity?challenge='):
            assert token is None
            challenge = path.split('=', 1)[1]
            return {'protocol': runtime.PROTOCOL, 'runtime_version': runtime.__version__,
                    'proof': runtime._proof(key, challenge, endpoint, doc['server_id'])}
        assert path == '/api/v1/capabilities'
        return {'actor': doc['actor'], 'desktop_identity': {k: doc[k] for k in ('server_id', 'principal_id')}}

    def progress(_seconds):
        assert requests == []
        pauses.append('owner bound')
        # A controlled transport models the owner's later publication; no real port is contacted.
        daemon._endpoint = endpoint + '/rpc'
        daemon._write_owner_pointer()

    monkeypatch.setattr(runtime, '_request', request)
    monkeypatch.setattr(runtime, 'time', SimpleNamespace(monotonic=lambda: len(pauses), time=runtime.time.time, sleep=progress))
    try:
        result = runtime.ensure(root, timeout=2)
        assert result['endpoint'] == endpoint and result['token'] == token
        assert len(pauses) == 1 and len(requests) == 2
        assert requests[0][2] is None and requests[1][2] == token
    finally:
        daemon.journal.close()


def test_managed_verification_never_sends_bearer_to_an_unproven_service(tmp_path, monkeypatch):
    runtime = managed_runtime
    root = runtime._root(tmp_path / 'managed')
    runtime.ensure_private_directory(root / 'state')
    runtime._write(root / 'state/task-service.json', {
        'db_path': str(root / 'state/tasks.sqlite3'), 'endpoint': 'http://127.0.0.1:45123/rpc'})
    runtime.atomic_write(root / 'installation.key', b'fixture-proof-key')
    requests = []

    def unrelated(address, path, **kwargs):
        requests.append(kwargs)
        return {'proof': 'not-the-owned-service'}

    monkeypatch.setattr(runtime, '_request', unrelated)
    with pytest.raises(ValueError, match='identity could not be verified'):
        runtime._verify(root, {'server_id': 'fixture-server'})
    assert requests == [{}]


@pytest.mark.parametrize('state', [{}, {'pid': 42}, {'pid': 43, 'endpoint': 'http://127.0.0.1:45123/rpc'}])
def test_packaged_upgrade_probe_waits_for_its_child_endpoint(tmp_path, monkeypatch, state):
    script = Path(__file__).resolve().parents[1] / 'desktop/tests/managed-upgrade-fixture.py'
    monkeypatch.syspath_prepend(str(script.parent))
    fixture = runpy.run_path(str(script))
    platform_files.ensure_private_directory(tmp_path / 'state')
    platform_files.atomic_write(tmp_path / 'state/task-service.json', json.dumps(state).encode())
    with pytest.raises(ValueError, match='not listening yet'):
        fixture['owned_launch'](tmp_path, SimpleNamespace(pid=42), {})


def test_packaged_upgrade_probe_proves_identity_without_a_bearer(tmp_path, monkeypatch):
    script = Path(__file__).resolve().parents[1] / 'desktop/tests/managed-upgrade-fixture.py'
    monkeypatch.syspath_prepend(str(script.parent))
    fixture = runpy.run_path(str(script))
    platform_files.ensure_private_directory(tmp_path / 'state')
    platform_files.atomic_write(tmp_path / 'state/task-service.json', json.dumps({
        'pid': 42, 'endpoint': 'http://127.0.0.1:45123/rpc'}).encode())
    (tmp_path / 'installation.key').write_bytes(b'fixture-key')
    requests = []

    def unrelated(launch, path, **kwargs):
        requests.append(kwargs)
        return {'proof': 'unrelated-service'}

    monkeypatch.setitem(fixture['owned_launch'].__globals__, 'request', unrelated)
    with pytest.raises(ValueError, match='identity could not be verified'):
        fixture['owned_launch'](tmp_path, SimpleNamespace(pid=42), {'server_id': 'fixture-server', 'token': 'fixture-token'})
    assert requests == [{'authenticated': False}]


def test_packaged_upgrade_probe_never_contacts_the_default_port(tmp_path, monkeypatch):
    script = Path(__file__).resolve().parents[1] / 'desktop/tests/managed-upgrade-fixture.py'
    monkeypatch.syspath_prepend(str(script.parent))
    fixture = runpy.run_path(str(script))
    platform_files.ensure_private_directory(tmp_path / 'state')
    platform_files.atomic_write(tmp_path / 'state/task-service.json', json.dumps({
        'pid': 42, 'endpoint': 'http://127.0.0.1:18796/rpc'}).encode())
    with pytest.raises(ValueError, match='Invalid owned fixture endpoint'):
        fixture['owned_launch'](tmp_path, SimpleNamespace(pid=42), {})
