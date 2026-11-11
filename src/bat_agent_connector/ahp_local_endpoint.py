"""Bounded Linux reads of an explicitly selected editor endpoint; no writes or launch.

Source: microsoft/vscode@2a59476c9bfcb90b3ddc372c36762471b7dfad1c,
src/vs/platform/agentHost/LOCAL_ENDPOINT.md and common/state/protocol/channels-*/state.ts.
Private process-local receipts expire on restart; they are never execution grants.
"""
from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import os
import re
import socket
import stat
import struct
import sys
import time
import uuid
from pathlib import Path
from urllib.parse import urlencode

from websockets.exceptions import InvalidStatus

from . import ahp_observer as observer
from . import ahp_protocol as wire
from .ahp_config import LocalAhpProfile
from .client import _wait_with_timeout

MAX_ENTRIES, MAX_METADATA, PROPOSAL_TTL = 32, 65536, 60
_SAFE = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK if sys.platform == 'linux' else 0
_active = set()


class LocalRefusal(ValueError):
    """Only fixed local reasons may cross the private reader boundary."""


def _uid():
    return os.getuid()


def _platform():
    return sys.platform


def _now():
    return time.time()


def identity(entry):
    return hashlib.sha256(f"{entry['type']}\0{entry['pid']}\0{entry['instanceId']}".encode()).hexdigest()


@contextlib.contextmanager
def _fd(path, directory=False):
    # Walk descriptors without following any component symlink; never create a path.
    parts = Path(path).parts
    if not parts or parts[0] != '/' or '..' in parts:
        raise LocalRefusal('path_refused')
    fd = os.open('/', _SAFE | os.O_DIRECTORY)
    try:
        for index, part in enumerate(parts[1:]):
            flags = _SAFE | (os.O_DIRECTORY if directory or index < len(parts) - 2 else 0)
            child = os.open(part, flags, dir_fd=fd)
            os.close(fd)
            fd = child
        yield fd
    finally:
        os.close(fd)


def _trusted(info, *, directory=False, private=True):
    kind = stat.S_ISDIR if directory else stat.S_ISREG
    mode = stat.S_IMODE(info.st_mode)
    if (not kind(info.st_mode) or info.st_uid not in ({_uid()} if private else {0, _uid()})
            or (mode != (0o700 if directory else 0o600) if private else bool(mode & 0o022))):
        raise LocalRefusal('metadata_untrusted')


def _read(path, *, private=True):
    with _fd(path) as fd:
        before = os.fstat(fd)
        _trusted(before, private=private)
        if before.st_size > MAX_METADATA:
            raise LocalRefusal('metadata_limit_or_change')
        raw = os.read(fd, MAX_METADATA)
        after = os.fstat(fd)
        if len(raw) != before.st_size or any(getattr(before, key) != getattr(after, key)
                for key in ('st_dev', 'st_ino', 'st_size', 'st_mtime_ns', 'st_ctime_ns')):
            raise LocalRefusal('metadata_limit_or_change')
        return json.loads(raw), observer.digest([before.st_dev, before.st_ino, before.st_mtime_ns, before.st_ctime_ns,
                                                hashlib.sha256(raw).hexdigest()])


def _liveness(pid):
    try:
        os.kill(pid, 0)
        return 'alive'
    except ProcessLookupError:
        return 'dead'
    except OSError:
        return 'unknown'


def _entry(value, source, digest):
    if (not isinstance(value, dict) or value.get('schemaVersion') != 2 or value.get('type') != 'editor'
            or type(value.get('pid')) is not int or not 0 < value['pid'] <= 2**31 - 1
            or not isinstance(value.get('instanceId'), str)
            or not re.fullmatch(r'[A-Za-z0-9_-]{1,256}', value['instanceId'])
            or value.get('protocolVersion') != '0.10.0'):
        return None
    endpoint, token = value.get('endpoint'), value.get('connectionToken')
    if (not isinstance(endpoint, dict) or set(endpoint) != {'type', 'path'} or endpoint['type'] != 'socket'
            or not isinstance(endpoint['path'], str) or not endpoint['path'].startswith('/')
            or '\0' in endpoint['path'] or not isinstance(token, str) or not token
            or any(ord(c) < 33 or ord(c) == 127 for c in token)):
        return None
    key, live = identity(value), _liveness(value['pid'])
    if live == 'dead' or source != 'metadata.json' and source != 'entries/' + key + '.json':
        return None
    return {'identity': key, 'pid': value['pid'], 'instance_id': value['instanceId'], 'path': endpoint['path'],
            'source': source, 'source_digest': digest, 'liveness': live}


def discover(profile):
    """Private candidates only. No default selection and no stale-file cleanup."""
    if _platform() != 'linux':
        raise LocalRefusal('platform_unsupported')
    if type(profile) is not LocalAhpProfile:
        raise LocalRefusal('profile_refused')
    base = Path(profile.user_data_root) / 'agent-host/local-endpoint'
    try:
        for path, private in ((profile.user_data_root, False), (base.parent, False), (base, True)):
            with _fd(path, directory=True) as fd:
                _trusted(os.fstat(fd), directory=True, private=private)
        candidates, sources, count = {}, [], 0
        try:
            with _fd(base / 'entries', directory=True) as fd:
                _trusted(os.fstat(fd), directory=True)
                with os.scandir(fd) as entries:
                    for entry in entries:
                        count += 1
                        if count > MAX_ENTRIES:
                            raise LocalRefusal('entry_limit')
                        if re.fullmatch(r'[0-9a-f]{64}\.json', entry.name):
                            sources.append('entries/' + entry.name)
        except FileNotFoundError:
            pass
        for source in sorted(sources) + ['metadata.json']:
            try:
                value, digest = _read(base / source)
                values = value if source == 'metadata.json' else [value]
                if not isinstance(values, list):
                    continue
                if source == 'metadata.json' and len(values) + count > MAX_ENTRIES:
                    raise LocalRefusal('entry_limit')
                for value in values:
                    item = _entry(value, source, digest)
                    if item:
                        candidates.setdefault(item['identity'], item)
            except LocalRefusal as error:
                if str(error) == 'entry_limit':
                    raise
            except (OSError, ValueError, TypeError):
                continue
        return [candidates[key] for key in sorted(candidates)]
    except LocalRefusal:
        raise
    except (OSError, ValueError, TypeError):
        raise LocalRefusal('metadata_unavailable') from None


def _selected(profile):
    for entry in discover(profile):
        if entry['identity'] == profile.entry_id:
            return entry
    raise LocalRefusal('selected_endpoint_unavailable')


def _socket_stamp(entry):
    info = os.lstat(entry['path'])
    if (not stat.S_ISSOCK(info.st_mode) or info.st_uid != _uid()
            or os.path.realpath(entry['path']) != entry['path']):
        raise LocalRefusal('socket_untrusted')
    return [info.st_dev, info.st_ino, info.st_ctime_ns]


def _installation(profile, info):
    root = Path(profile.installation_root)
    with _fd(root, directory=True) as fd:
        _trusted(os.fstat(fd), directory=True, private=False)
    package, package_digest = _read(root / 'package.json', private=False)
    product, product_digest = _read(root / 'product.json', private=False)
    if (package.get('version') != '1.141.0' or product.get('commit') != wire.PINNED_COMMITS['0.10.0']
            or info.get('version', package['version']) != package['version']):
        raise LocalRefusal('installation_version_unverified')
    return {'version': package['version'], 'commit': product['commit'],
            'files_digest': observer.digest([package_digest, product_digest])}


async def _connect(entry, token):
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.setblocking(False)
    try:
        await _wait_with_timeout(asyncio.get_running_loop().sock_connect(sock, entry['path']), 5)
        pid, uid, _ = struct.unpack('3i', sock.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
        if uid != _uid() or pid != entry['pid']:
            raise LocalRefusal('peer_unverified')
        return await observer._NoRedirect('ws://localhost/?' + urlencode({'tkn': token}), unix=True, sock=sock,
            proxy=None, compression=None, open_timeout=5, close_timeout=0.2, max_size=observer.MAX_FRAME,
            max_queue=4, ping_interval=None, logger=observer._LOGGER)
    except BaseException:
        sock.close()
        raise


class _Reader(observer.Observer):
    def event(self, envelope):
        if envelope.get('channel') not in self.subscriptions:
            raise LocalRefusal('unselected_event')
        wire.decoder_for(self.negotiated_version)({'jsonrpc': '2.0', 'method': 'action', 'params': envelope})


class LocalVerifier:
    def __init__(self):
        self._receipts = {}

    def public(self, profile):
        receipt = self._receipts.get(profile.profile_id)
        reason, window = 'not_verified', None
        if receipt:
            window = receipt['validity_window']
            expired = not window['observed_at'] <= _now() < window['expires_at']
            reason = 'verification_expired' if expired or time.monotonic() >= receipt['_deadline'] else 'verified_read_only'
            try:
                if (receipt['binding_digest'] != observer.digest(vars(profile))
                        or _selected(profile) != receipt['_endpoint']
                        or _socket_stamp(receipt['_endpoint']) != receipt['endpoint_incarnation']
                        or _installation(profile, {}) != receipt['installation']):
                    reason = 'binding_changed'
            except Exception:
                reason = 'binding_changed'
            if reason != 'verified_read_only':
                self._receipts.pop(profile.profile_id, None)
        return {'profile_id': profile.profile_id, 'reason': reason,
                'validity_window': dict(window) if window else None}

    async def verify(self, profile, *, check):
        if type(profile) is not LocalAhpProfile:
            raise LocalRefusal('profile_refused')
        if profile.profile_id in _active or len(_active) >= 16:
            raise LocalRefusal('verification_busy')
        _active.add(profile.profile_id)
        self._receipts.pop(profile.profile_id, None)
        try:
            receipt = await _wait_with_timeout(self._verify(profile, check), observer.MAX_SECONDS)
            check()
            self._receipts[profile.profile_id] = receipt
            return self.public(profile)
        except LocalRefusal as error:
            raise LocalRefusal(str(error)) from None
        except InvalidStatus as error:
            reason = 'connection_token_unavailable' if error.response.status_code == 403 else 'upgrade_refused'
            raise LocalRefusal(reason) from None
        except Exception:
            raise LocalRefusal('verification_failed') from None
        finally:
            _active.discard(profile.profile_id)

    async def _verify(self, profile, check):
        check()
        endpoint = _selected(profile)
        stamp = _socket_stamp(endpoint)
        def current():
            check()
            if _selected(profile) != endpoint or _socket_stamp(endpoint) != stamp:
                raise LocalRefusal('endpoint_changed')
        base = Path(profile.user_data_root) / 'agent-host/local-endpoint'
        raw, digest = _read(base / endpoint['source'])
        if digest != endpoint['source_digest']:
            raise LocalRefusal('endpoint_changed')
        values = raw if isinstance(raw, list) else [raw]
        token = next(value['connectionToken'] for value in values if identity(value) == profile.entry_id)
        if token in json.dumps([endpoint, vars(profile)]):
            raise LocalRefusal('private_identity_refused')
        ws = await _connect(endpoint, token)
        del token, raw, values
        try:
            reader = _Reader(None, client_id=str(uuid.uuid4()), selected=profile.session_uris,
                             changesets=False, check=current)
            reader.ws = ws
            result = await reader.request('initialize', {'channel': observer.ROOT, 'clientId': reader.client_id,
                'protocolVersions': [profile.wire_version], 'initialSubscriptions': [observer.ROOT]})
            if result.get('protocolVersion') != profile.wire_version:
                raise LocalRefusal('protocol_version_changed')
            reader.negotiated_version = profile.wire_version
            decoder = wire.decoder_for(profile.wire_version)
            installation = _installation(profile, result.get('serverInfo', {}))
            observer.integer(result.get('serverSeq'))
            snapshots = result.get('snapshots')
            if not isinstance(snapshots, list) or len(snapshots) != 1 or snapshots[0]['resource'] != observer.ROOT:
                raise LocalRefusal('root_snapshot_required')
            root = snapshots[0]['state']
            agents = root['agents']
            if not isinstance(agents, list) or len(agents) > 32:
                raise LocalRefusal('provider_limit')
            matches = [item for item in agents if item.get('provider') == profile.provider]
            if len(matches) != 1:
                raise LocalRefusal('provider_identity_unverified')
            agent = matches[0]
            if (not isinstance(agent.get('models'), list) or len(agent['models']) > 200
                    or not isinstance(agent.get('capabilities', {}), dict)):
                raise LocalRefusal('provider_shape_refused')
            resources = await reader.catalog_read()
            for summary in reader.catalog:
                wire._session(summary['resource'], profile.wire_version, summary['provider'])
                wire.decode_status(summary['status'])
            chats = 0
            for session in profile.session_uris:
                wire._session(session, profile.wire_version, profile.provider)
                if session not in resources:
                    raise LocalRefusal('selected_session_unavailable')
                reader.subscriptions.append(session)
                state = await self._snapshot(reader, session)
                if state.get('provider') != profile.provider or state.get('lifecycle') not in {'creating', 'ready', 'error'}:
                    raise LocalRefusal('session_shape_refused')
                if not isinstance(state.get('chats'), list) or len(state['chats']) > observer.MAX_SELECTED:
                    raise LocalRefusal('chat_limit')
                for chat in state['chats']:
                    uri = chat['resource']
                    wire._chat(uri, profile.wire_version)
                    chats += 1
                    if chats > observer.MAX_SELECTED:
                        raise LocalRefusal('chat_limit')
                    reader.subscriptions.append(uri)
                    chat_state = await self._snapshot(reader, uri)
                    if chat_state.get('resource') != uri or not isinstance(chat_state.get('turns'), list):
                        raise LocalRefusal('chat_shape_refused')
            current()
            if reader.catalog_changed or _installation(profile, {}) != installation:
                raise LocalRefusal('verification_changed')
            observed = _now()
            return {'binding_digest': observer.digest(vars(profile)), '_endpoint': endpoint,
                'installation': installation, 'endpoint_incarnation': stamp,
                'negotiated_version': profile.wire_version, 'decoder': decoder.__name__,
                'provider_config_model_digest': observer.digest({'provider': agent, 'config': root.get('config')}),
                '_deadline': time.monotonic() + PROPOSAL_TTL,
                'validity_window': {'observed_at': observed, 'expires_at': observed + PROPOSAL_TTL},
                'coverage': {'catalog_complete': reader.catalog_complete, 'catalog_count': len(reader.catalog),
                    'sessions': len(profile.session_uris), 'chats': chats, 'writes': False,
                    'provider_details': 'opaque_digest_only', 'transcript_retained': False,
                    'running_binary_attested': False, 'instance_write_proof': False}}
        finally:
            await ws.close()

    @staticmethod
    async def _snapshot(reader, uri):
        result = await reader.request('subscribe', {'channel': uri})
        snapshot = result['snapshot']
        if snapshot['resource'] != uri:
            raise LocalRefusal('snapshot_identity_changed')
        observer.integer(snapshot['fromSeq'])
        state = snapshot['state']
        wire.decode_status(state['status'])
        return state
