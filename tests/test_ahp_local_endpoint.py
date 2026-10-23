"""Synthetic Linux endpoint fixtures; real disk-backed Unix sockets, never an editor.

Metadata/root/session/chat shapes: microsoft/vscode@2a59476c9bfcb90b3ddc372c36762471b7dfad1c,
src/vs/platform/agentHost/LOCAL_ENDPOINT.md and common/state/protocol/channels-*/state.ts.
Action envelopes come from the pinned synthetic owned-wire fixtures.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from dataclasses import replace
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest
from websockets.asyncio.server import unix_serve
from websockets.exceptions import InvalidURI

from bat_agent_connector import ahp_local_endpoint as local
from bat_agent_connector import ahp_observer, ahp_protocol
from bat_agent_connector.ahp_config import LocalAhpProfile, parse_ahp
from bat_agent_connector.config import ConfigError

pytestmark = pytest.mark.skipif(sys.platform != 'linux', reason='Linux Unix endpoint profile only')
TOKEN = 'synthetic-local-connection-token-abcdef123456'
ROWS = [json.loads(line) for line in (Path(__file__).parent / 'fixtures/ahp-owned/protocol-shapes.jsonl')
        .read_text(encoding='utf-8').splitlines()]
GOLDEN = {row['name']: row for row in ROWS if row['version'] == '0.10.0'}
SESSION = GOLDEN['create']['frame']['params']['channel']
CHAT = GOLDEN['turn']['frame']['params']['channel']


def publish(root, entry, *, legacy=False):
    folder = root / 'agent-host/local-endpoint'
    (folder / 'entries').mkdir(parents=True, exist_ok=True)
    root.chmod(0o700)
    folder.parent.chmod(0o700)
    folder.chmod(0o700)
    (folder / 'entries').chmod(0o700)
    path = folder / ('metadata.json' if legacy else 'entries/' + local.identity(entry) + '.json')
    path.write_text(json.dumps([entry] if legacy else entry), encoding='utf-8')
    path.chmod(0o600)
    return path


@pytest.fixture
def world(tmp_path):
    root = tmp_path / 'selected'
    app = tmp_path / 'installation'
    app.mkdir()
    app.chmod(0o755)
    (app / 'package.json').write_text(json.dumps({'version': '1.141.0'}))
    (app / 'product.json').write_text(json.dumps({'commit': ahp_protocol.PINNED_COMMITS['0.10.0']}))
    for file in app.iterdir():
        file.chmod(0o644)
    # A short basename in the wrapper's disk basetemp respects sun_path's size.
    path = str(tmp_path / 's')
    entry = {'schemaVersion': 2, 'type': 'editor', 'pid': os.getpid(), 'instanceId': 'synthetic-instance',
             'protocolVersion': '0.10.0', 'connectionToken': TOKEN, 'endpoint': {'type': 'socket', 'path': path}}
    metadata = publish(root, entry)
    profile = LocalAhpProfile('selected-editor', str(root), str(app), local.identity(entry), 'copilotcli', (SESSION,))
    return profile, entry, metadata


@pytest.fixture
async def host(world):
    profile, entry, _ = world
    state = {'requests': [], 'mode': 'normal', 'connections': 0}
    async def upgrade(connection, request):
        state['connections'] += 1
        if state['mode'] == '403' or parse_qs(urlsplit(request.path).query) != {'tkn': [TOKEN]}:
            return connection.respond(403, 'Synthetic token unavailable')
    async def handler(ws):
        async for raw in ws:
            request = json.loads(raw)
            state['requests'].append(request)
            method, channel = request['method'], request['params']['channel']
            assert method in {'initialize', 'listSessions', 'subscribe'}
            if state['mode'] == 'stall':
                await ws.wait_closed()
                return
            if method == 'initialize':
                # Pinned protocolServerHandler.ts omits the optional serverInfo.
                result = {'protocolVersion': '0.10.0', 'serverSeq': 3,
                          'snapshots': [{'resource': 'ahp-root://', 'fromSeq': 3, 'state': {'agents': [
                              {'provider': 'copilotcli', 'displayName': 'Synthetic provider', 'description': '',
                               'models': [], 'capabilities': {}}]}}]}
                if state['mode'] == 'version':
                    result['protocolVersion'] = '1.0.0'
                if state['mode'] == 'distribution':
                    result['serverInfo'] = {'name': 'synthetic-host', 'version': '9.0.0'}
                if state['mode'] == 'info_only':
                    result['serverInfo'] = {'name': 'synthetic-host', 'version': '1.141.0'}
            elif method == 'listSessions':
                summary = {'resource': SESSION, 'provider': 'copilotcli', 'title': 'Synthetic', 'status': 33,
                           'createdAt': '2026-01-01T00:00:00Z', 'modifiedAt': '2026-01-01T00:00:00Z'}
                result = {'items': [summary]}
                if state['mode'] == 'catalog_limit':
                    result['items'] *= 201
            elif channel == SESSION:
                result = {'snapshot': {'resource': SESSION, 'fromSeq': 3, 'state': {
                    'provider': 'copilotcli', 'title': 'Synthetic', 'status': 33, 'lifecycle': 'ready',
                    'activeClients': [], 'chats': [{'resource': CHAT, 'title': 'Synthetic chat', 'status': 1,
                        'modifiedAt': '2026-01-01T00:00:00Z'}], 'defaultChat': CHAT}}}
            else:
                assert channel == CHAT
                await ws.send(json.dumps(GOLDEN['turn_echo']['frame']))
                result = {'snapshot': {'resource': CHAT, 'fromSeq': 3, 'state': {'resource': CHAT,
                    'title': 'Synthetic chat', 'status': 1, 'modifiedAt': '2026-01-01T00:00:00Z', 'turns': []}}}
                if state['mode'] == 'chat_shape':
                    result['snapshot']['state']['status'] = 128
            if state['mode'] == 'rotate' and method == 'initialize':
                publish(Path(profile.user_data_root), {**entry, 'connectionToken': TOKEN + '-rotated'})
            await ws.send(json.dumps({'jsonrpc': '2.0', 'id': request['id'], 'result': result}))
    # Peer logging belongs to the synthetic editor, outside the Connector. Keep
    # the test's root DEBUG capture enabled to detect any Connector URL leakage.
    peer_logger = logging.Logger('synthetic.editor', level=logging.CRITICAL + 1)
    async with unix_serve(handler, entry['endpoint']['path'], process_request=upgrade, logger=peer_logger):
        yield state


async def test_fixed_instance_bounded_verification_and_private_receipt(world, host, caplog):
    profile, _, _ = world
    caplog.set_level(logging.DEBUG)
    verifier = local.LocalVerifier()
    result = await verifier.verify(profile, check=lambda: None)
    assert set(result) == {'profile_id', 'reason', 'validity_window'}
    assert result['reason'] == 'verified_read_only'
    assert result['validity_window']['expires_at'] - result['validity_window']['observed_at'] == 60
    receipt = verifier._receipts[profile.profile_id]
    assert receipt['installation']['version'] == '1.141.0'
    assert receipt['negotiated_version'] == '0.10.0' and receipt['decoder'] == 'decode_0_10'
    assert receipt['coverage']['sessions'] == 1 and receipt['coverage']['chats'] == 1
    assert receipt['coverage']['writes'] is False and receipt['coverage']['running_binary_attested'] is False
    assert host['requests'][0]['params']['protocolVersions'] == ['0.10.0']
    assert [r['method'] for r in host['requests']] == ['initialize', 'listSessions', 'subscribe', 'subscribe']
    assert host['connections'] == 1
    assert TOKEN not in repr(profile) + json.dumps(receipt) + json.dumps(result) + caplog.text
    assert ahp_observer.METHODS == {'initialize', 'reconnect', 'listSessions', 'subscribe'}
    with pytest.raises(ahp_protocol.WireRefusal, match='owned_writes_disabled'):
        ahp_protocol.require_owned_write()
    result['validity_window']['expires_at'] = 0
    assert verifier.public(profile)['reason'] == 'verified_read_only'


def test_discovery_never_searches_outside_selected_root(world):
    profile, entry, metadata = world
    metadata.unlink()
    publish(Path(profile.user_data_root).parent / 'outside', entry)
    assert local.discover(profile) == []


def test_legacy_dedup_unknown_schema_dead_and_unknown_pid(world, monkeypatch):
    profile, entry, _ = world
    old = {**entry, 'connectionToken': TOKEN + '-old'}
    publish(Path(profile.user_data_root), old, legacy=True)
    assert len(local.discover(profile)) == 1
    assert local.discover(profile)[0]['source'].startswith('entries/')
    monkeypatch.setattr(local, '_liveness', lambda pid: 'unknown')
    assert local.discover(profile)[0]['liveness'] == 'unknown'
    monkeypatch.setattr(local, '_liveness', lambda pid: 'dead')
    assert local.discover(profile) == []
    monkeypatch.setattr(local, '_liveness', lambda pid: 'alive')
    publish(Path(profile.user_data_root), {**entry, 'schemaVersion': 1})
    assert local.discover(profile)[0]['source'] == 'metadata.json'


@pytest.mark.parametrize('unsafe', ['symlink', 'fifo', 'mode', 'directory', 'wrong_name', 'oversize'])
def test_unsafe_metadata_is_not_a_candidate(world, unsafe):
    profile, _, metadata = world
    if unsafe == 'symlink':
        target = metadata.with_suffix('.saved')
        metadata.rename(target)
        metadata.symlink_to(target)
    elif unsafe == 'fifo':
        metadata.unlink()
        os.mkfifo(metadata, 0o600)
    elif unsafe == 'mode':
        metadata.chmod(0o644)
    elif unsafe == 'directory':
        metadata.unlink()
        metadata.mkdir()
    elif unsafe == 'wrong_name':
        metadata.rename(metadata.with_name('0' * 64 + '.json'))
    else:
        metadata.write_bytes(b' ' * 65537)
    assert local.discover(profile) == []


def test_owner_and_directory_mode_refused(world, monkeypatch):
    profile, _, metadata = world
    metadata.parent.chmod(0o755)
    with pytest.raises(local.LocalRefusal):
        local.discover(profile)
    metadata.parent.chmod(0o700)
    monkeypatch.setattr(local, '_uid', lambda: os.getuid() + 1)
    with pytest.raises(local.LocalRefusal):
        local.discover(profile)


def test_metadata_count_limit_and_explicit_instance_selection(world):
    profile, entry, _ = world
    for i in range(32):
        publish(Path(profile.user_data_root), {**entry, 'instanceId': 'synthetic-' + str(i)})
    with pytest.raises(local.LocalRefusal, match='entry_limit'):
        local.discover(profile)


@pytest.mark.parametrize('mode', ['403', 'version', 'distribution', 'catalog_limit', 'chat_shape', 'rotate'])
async def test_refusals_never_produce_a_receipt(world, host, mode, caplog):
    profile, _, _ = world
    host['mode'] = mode
    verifier = local.LocalVerifier()
    with pytest.raises(local.LocalRefusal) as error:
        await verifier.verify(profile, check=lambda: None)
    if mode == '403':
        assert str(error.value) == 'connection_token_unavailable'
    assert verifier._receipts == {}
    assert TOKEN not in str(error.value) + caplog.text


async def test_token_never_leaks_from_transport_exception(world, host, monkeypatch, caplog):
    async def fail(*args):
        raise InvalidURI('ws://localhost/?tkn=' + TOKEN, 'synthetic failed upgrade')
    monkeypatch.setattr(local, '_connect', fail)
    caplog.set_level(logging.DEBUG)
    with pytest.raises(local.LocalRefusal) as error:
        await local.LocalVerifier().verify(world[0], check=lambda: None)
    assert TOKEN not in str(error.value) + repr(error.value) + caplog.text


async def test_server_info_alone_cannot_prove_installed_version(world, host):
    profile, _, _ = world
    host['mode'] = 'info_only'
    (Path(profile.installation_root) / 'package.json').unlink()
    with pytest.raises(local.LocalRefusal):
        await local.LocalVerifier().verify(profile, check=lambda: None)


async def test_revoked_check_stops_before_next_frame(world, host):
    calls = 0
    def check():
        nonlocal calls
        calls += 1
        if calls == 3:
            raise PermissionError('Synthetic authority withdrawn')
    with pytest.raises(local.LocalRefusal):
        await local.LocalVerifier().verify(world[0], check=check)
    assert [request['method'] for request in host['requests']] == ['initialize']


async def test_deadline_closes_only_our_socket_and_releases_admission(world, host, monkeypatch):
    host['mode'] = 'stall'
    monkeypatch.setattr(ahp_observer, 'MAX_SECONDS', 0.05)
    with pytest.raises(local.LocalRefusal):
        await local.LocalVerifier().verify(world[0], check=lambda: None)
    assert local._active == set()
    assert Path(world[1]['endpoint']['path']).exists()
    assert world[2].exists()


async def test_cancellation_and_restart_invalidate_instance_without_reconnect(world, host):
    profile, entry, metadata = world
    host['mode'] = 'stall'
    task = asyncio.create_task(local.LocalVerifier().verify(profile, check=lambda: None))
    await asyncio.sleep(0)
    task.cancel()
    done, _ = await asyncio.wait({task}, timeout=1)
    try:
        assert task in done, 'Caller cancellation must settle even if socket connect just completed'
        with pytest.raises(asyncio.CancelledError):
            await task
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    assert local._active == set()
    host['mode'] = 'normal'
    verifier = local.LocalVerifier()
    await verifier.verify(profile, check=lambda: None)
    metadata.unlink()
    publish(Path(profile.user_data_root), {**entry, 'instanceId': 'synthetic-restarted'})
    assert verifier.public(profile)['reason'] == 'binding_changed'
    with pytest.raises(local.LocalRefusal, match='selected_endpoint_unavailable'):
        await verifier.verify(profile, check=lambda: None)


async def test_wrong_selected_instance_never_falls_back_to_first_window(world, host):
    with pytest.raises(local.LocalRefusal, match='selected_endpoint_unavailable'):
        await local.LocalVerifier().verify(replace(world[0], entry_id='0' * 64), check=lambda: None)
    assert host['connections'] == 0


async def test_socket_peer_pid_must_match_before_token_upgrade(world, host):
    profile, entry, _ = world
    changed = {**entry, 'pid': os.getppid()}
    publish(Path(profile.user_data_root), changed)
    with pytest.raises(local.LocalRefusal, match='peer_unverified'):
        await local.LocalVerifier().verify(replace(profile, entry_id=local.identity(changed)), check=lambda: None)
    assert host['connections'] == 0


@pytest.mark.parametrize('unsafe', ['mode', 'symlink', 'commit'])
async def test_installation_readback_must_be_trusted_and_pinned(world, host, unsafe):
    profile, _, _ = world
    path = Path(profile.installation_root) / 'product.json'
    if unsafe == 'mode':
        path.chmod(0o666)
    elif unsafe == 'symlink':
        target = path.with_suffix('.saved')
        path.rename(target)
        path.symlink_to(target)
    else:
        path.write_text(json.dumps({'commit': '0' * 40}))
    with pytest.raises(local.LocalRefusal):
        await local.LocalVerifier().verify(profile, check=lambda: None)


async def test_receipt_expires_and_token_source_change_invalidates_it(world, host, monkeypatch):
    profile, entry, _ = world
    verifier = local.LocalVerifier()
    result = await verifier.verify(profile, check=lambda: None)
    monkeypatch.setattr(local, '_now', lambda: result['validity_window']['expires_at'] + 1)
    assert verifier.public(profile)['reason'] == 'verification_expired'
    monkeypatch.undo()
    await verifier.verify(profile, check=lambda: None)
    publish(Path(profile.user_data_root), {**entry, 'connectionToken': TOKEN + '-changed'})
    assert verifier.public(profile)['reason'] == 'binding_changed'


async def test_metadata_security_change_cannot_revive_an_old_receipt(world, host):
    profile, _, metadata = world
    verifier = local.LocalVerifier()
    await verifier.verify(profile, check=lambda: None)
    metadata.chmod(0o644)
    metadata.chmod(0o600)
    assert verifier.public(profile)['reason'] == 'binding_changed'


@pytest.mark.parametrize('platform', ['darwin', 'win32'])
def test_other_platforms_refuse(world, monkeypatch, platform):
    monkeypatch.setattr(local, '_platform', lambda: platform)
    with pytest.raises(local.LocalRefusal, match='platform_unsupported'):
        local.discover(world[0])


def test_local_and_tcp_bindings_cannot_masquerade_as_each_other(world):
    profile, entry, _ = world
    for endpoint in ({'type': 'tcp', 'host': '127.0.0.1', 'port': 4567},
                     {'type': 'socket', 'path': 'ws://127.0.0.1:4567/'}):
        publish(Path(profile.user_data_root), {**entry, 'endpoint': endpoint})
        assert local.discover(profile) == []
    for endpoint in (entry['endpoint']['path'], 'unix://' + entry['endpoint']['path'], 'ws://192.0.2.1:4567/'):
        with pytest.raises(ConfigError):
            parse_ahp({'ahp': {'adapters': {'test': {'workspace_ids': ['ws_' + '1' * 32],
                'endpoint': endpoint, 'credential_ref': 'env:SYNTHETIC_TOKEN'}}}})
    with pytest.raises(ValueError):
        replace(profile, user_data_root='ws://127.0.0.1/')
