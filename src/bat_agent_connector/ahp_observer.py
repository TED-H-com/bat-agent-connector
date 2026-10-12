"""Bounded, version-profiled AHP reads. No dispatch, authentication or writes.

Wire source: microsoft/agent-host-protocol spec/v1.0.0, 5f16d81bb7045b66.
Events are original metadata receipts, not a second reducer or completion proof.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import time
from urllib.parse import urlencode

from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed, WebSocketException

VERSION = '1.0.0'
PROFILES = {'1.0.0': 'ahp_1_0_observer_v1', '0.10.0': 'vscode_0_10_observer_v1'}
# These are wire profiles, never inferred from informational serverInfo strings.
_LEGACY_SESSION = re.compile(r'([a-z][a-z0-9+.-]*):/([^/?#\\]+)')
_RESERVED_SCHEMES = frozenset({'file', 'http', 'https', 'ws', 'wss', 'data', 'javascript',
    'ftp', 'ssh', 'mailto', 'ahp-root', 'ahp-changeset', 'ahp-terminal', 'ahp-chat', 'ahp-folder-changeset'})
ROOT = 'ahp-root://'
MAX_SECONDS = 30
MAX_BYTES = 4 * 1024 * 1024
MAX_FRAME = 1024 * 1024
MAX_FRAMES = 512
MAX_PAGES = 8
MAX_SESSIONS = 200
MAX_SELECTED = 8
MAX_CHANGESETS = 16
METHODS = frozenset({'initialize', 'reconnect', 'listSessions', 'subscribe'})
# No WebSocket debug/error record may expose a query credential or peer payload.
_LOGGER = logging.Logger('batc.ahp.private', level=logging.CRITICAL + 1)
_LOGGER.addHandler(logging.NullHandler())
_LOGGER.propagate = False


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
        separators=(',', ':'), allow_nan=False).encode()).hexdigest()


class ObserverError(Exception):
    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason


class _NoRedirect(connect):
    def process_redirect(self, exc):
        return exc


def text(value, limit=2048):
    if (not isinstance(value, str) or len(value) > limit
            or any(ord(c) < 32 or ord(c) == 127 for c in value)):
        raise ObserverError('invalid_peer_shape')
    return value


def uri(value, prefix):
    return isinstance(value, str) and value.startswith(prefix) and len(value) <= 2048 and not any(
        ord(c) < 33 or ord(c) == 127 or c in '{}' for c in value)


def integer(value):
    if type(value) is not int or not 0 <= value <= 2**53 - 1:
        raise ObserverError('invalid_peer_shape')
    return value


def session_uri(value, version=None, provider_id=None):
    """Syntax only; subscription still requires the original advertised identity."""
    if uri(value, 'ahp-session:'):
        return version != '0.10.0'
    if version not in (None, '0.10.0') or not uri(value, ''):
        return False
    match = _LEGACY_SESSION.fullmatch(value)
    return bool(match and match[1] not in _RESERVED_SCHEMES and not match[1].startswith('ahp-')
        and (provider_id is None or provider_id == match[1]))


def static_changeset(value, parent, version):
    if version == '1.0.0' and uri(value, 'ahp-changeset:'):
        return True
    # VS Code advertises nested session changesets in both wire versions. Read
    # only this exact static advertisement, never expand templates or read files.
    prefix = parent + '/changeset/'
    return (uri(value, prefix) and bool(value[len(prefix):])
        and not any(c in value[len(prefix):] for c in '/?#\\'))


def summary(value, version=VERSION):
    if not isinstance(value, dict) or not session_uri(value.get('resource'), version, value.get('provider')):
        raise ObserverError('invalid_session_summary')
    out = {key: text(value[key]) for key in ('resource', 'provider', 'title')}
    out['status'] = integer(value['status'])
    for key in ('createdAt', 'modifiedAt'):
        if key in value:
            out[key] = text(value[key], 128)
    if 'workingDirectories' in value:
        dirs = value['workingDirectories']
        if not isinstance(dirs, list) or len(dirs) > 32:
            raise ObserverError('invalid_peer_shape')
        out['workingDirectories'] = [text(item) for item in dirs]
    out.update(source_kind='session', ownership='foreign_or_manual', access='read_only', completion='not_inferred')
    return out


def project_snapshot(resource, state, kind, version):
    if not isinstance(state, dict):
        raise ObserverError('invalid_snapshot')
    if resource == ROOT:
        # Agent configuration, authentication challenges, customizations and tools
        # aren't needed for this observer and must not become a secret side channel.
        return {'kind': 'root', 'provider_configuration': 'not_retained'}
    if kind == 'session':
        out = summary({**state, 'resource': resource}, version)
        out['lifecycle'] = text(state.get('lifecycle', 'unknown'), 64)
        entries = state.get('changesets', [])
        if not isinstance(entries, list) or len(entries) > 64:
            raise ObserverError('changeset_catalog_limit')
        out['changesets'] = []
        for entry in entries:
            if not isinstance(entry, dict):
                raise ObserverError('invalid_peer_shape')
            template = text(entry.get('uriTemplate'))
            out['changesets'].append({'label': text(entry.get('label')), 'uriTemplate': template,
                'changeKind': text(entry.get('changeKind'), 128),
                'read_supported': static_changeset(template, resource, version)})
        return out
    if kind == 'changeset':
        files = state.get('files')
        if not isinstance(files, list) or len(files) > 500:
            raise ObserverError('changeset_file_limit')
        out = {'status': text(state.get('status'), 64), 'files': []}
        for file in files:
            if not isinstance(file, dict) or not isinstance(file.get('edit'), dict):
                raise ObserverError('invalid_peer_shape')
            row = {'id': text(file.get('id')), 'edit': {}}
            for side in ('before', 'after'):
                if side in file['edit']:
                    row['edit'][side] = {'uri': text(file['edit'][side]['uri'])}
            diff = file['edit'].get('diff')
            if diff is not None:
                row['edit']['diff'] = {key: integer(diff[key]) for key in ('added', 'removed')}
            out['files'].append(row)
        out['content'] = 'not_fetched'
        return out
    raise ObserverError('unselected_snapshot')


class Observer:
    def __init__(self, adapter, *, client_id, selected, changesets, check):
        self.adapter, self.client_id = adapter, client_id
        self.selected, self.changesets, self.check = selected, changesets, check
        self.ws = None
        self.sequence = self.request_id = self.bytes = self.frames = self.pages = 0
        self.subscriptions = [ROOT]
        self.events, self.event_digests, self.snapshots = [], {}, {}
        self.catalog, self.missing, self.gaps = [], [], []
        self.server_info, self.reconnect_mode = {}, 'not_needed'
        self.catalog_complete = False
        self.tokens, self.changeset_owners = [], {}
        self.session_sources = {}
        self.positive_catalog, self.positive_snapshots = [], []
        self.catalog_changed, self.notifications = False, []
        self.negotiated_version = self.decoder_profile = None

    def sources(self, resource):
        if resource == ROOT:
            return []
        parent = self.changeset_owners.get(resource, resource)
        original = self.session_sources.get(parent)
        if original is None:
            raise ObserverError('snapshot_source_unavailable')
        result = [{key: original[key] for key in ('resource', 'source_identity')} | {'source_kind': 'session'}]
        if parent != resource:
            result.append({'resource': resource, 'source_kind': 'changeset', 'source_identity': digest({
                'resource': resource, 'session_identity': original['source_identity']})})
        return result

    def withdraw(self, resource):
        if resource not in self.missing:
            self.missing.append(resource)
        self.snapshots.pop(resource, None)
        for channel, owner in list(self.changeset_owners.items()):
            if owner == resource:
                self.snapshots.pop(channel, None)
                if channel not in self.missing:
                    self.missing.append(channel)

    def event(self, envelope):
        if not isinstance(envelope, dict) or not isinstance(envelope.get('action'), dict):
            raise ObserverError('invalid_event')
        seq, channel = integer(envelope.get('serverSeq')), text(envelope.get('channel'))
        if channel not in self.subscriptions:
            raise ObserverError('unsubscribed_event')
        original = digest(envelope)
        if seq in self.event_digests:
            if self.event_digests[seq] != original:
                raise ObserverError('conflicting_event_identity')
            return
        if len(self.events) >= MAX_FRAMES:
            raise ObserverError('event_limit')
        self.event_digests[seq] = original
        self.events.append({'channel': channel, 'server_seq': seq,
            'action_type': text(envelope['action'].get('type'), 256), 'original_digest': original,
            'received_at': time.time(), 'payload': 'not_retained', 'original_sources': self.sources(channel)})
        self.sequence = max(self.sequence, seq)

    def snapshot(self, value):
        if not isinstance(value, dict) or value.get('resource') not in self.subscriptions:
            raise ObserverError('unselected_snapshot')
        resource, seq = value['resource'], integer(value.get('fromSeq'))
        if resource in self.missing:
            return  # A later response must not revive a disposed/withdrawn URI.
        raw_state = value.get('state')
        if resource in self.session_sources:
            original = self.session_sources[resource]
            # SessionState has provider but, unlike SessionSummary, need not
            # contain createdAt. Bind it to the catalog identity captured before
            # subscription; compare a timestamp if the host supplies one.
            if (not isinstance(raw_state, dict) or raw_state.get('provider') != original['provider']
                    or 'createdAt' in raw_state and raw_state['createdAt'] != original['createdAt']):
                raise ObserverError('session_identity_changed')
        kind = ('root' if resource == ROOT else 'session' if resource in self.session_sources
            else 'changeset' if resource in self.changeset_owners else 'unknown')
        state = project_snapshot(resource, raw_state, kind, self.negotiated_version)
        sources = self.sources(resource)
        self.snapshots[resource] = {'resource': resource, 'from_seq': seq,
            'source_kind': kind,
            'original_digest': digest(value), 'received_at': time.time(),
            'state': state, 'original_sources': sources}
        if resource not in self.positive_snapshots:
            self.positive_snapshots.append(resource)
        self.sequence = max(self.sequence, seq)

    async def request(self, method, params):
        if method not in METHODS:
            raise ObserverError('read_method_refused')
        self.check()  # Immediately before every original application frame.
        self.request_id += 1
        request_id = self.request_id
        await self.ws.send(json.dumps({'jsonrpc': '2.0', 'id': request_id, 'method': method, 'params': params}))
        while True:
            raw = await self.ws.recv()
            self.check()
            if not isinstance(raw, str):
                raise ObserverError('binary_frame_refused')
            self.bytes += len(raw.encode())
            self.frames += 1
            if self.bytes > MAX_BYTES or self.frames > MAX_FRAMES:
                raise ObserverError('read_limit')
            value = json.loads(raw, parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
            if not isinstance(value, dict) or value.get('jsonrpc') != '2.0':
                raise ObserverError('invalid_rpc')
            if 'method' in value:
                if 'id' in value:
                    raise ObserverError('server_request_unsupported')
                if value['method'] == 'action':
                    self.event(value.get('params'))
                elif value['method'] in {'root/sessionAdded', 'root/sessionRemoved', 'root/sessionSummaryChanged'}:
                    params = value.get('params')
                    if not isinstance(params, dict) or params.get('channel') != ROOT:
                        raise ObserverError('invalid_catalog_notification')
                    self.catalog_changed = True
                    self.notifications.append({'method': value['method'], 'original_digest': digest(value),
                        'received_at': time.time(), 'payload': 'not_retained'})
                    if value['method'] == 'root/sessionRemoved':
                        if not session_uri(params.get('session'), self.negotiated_version):
                            raise ObserverError('invalid_catalog_notification')
                        self.withdraw(params['session'])
                # Other server notifications aren't commands and are not retained.
                continue
            if type(value.get('id')) is not int or value.get('id') != request_id:
                raise ObserverError('unexpected_response')
            if 'error' in value:
                raise ObserverError('peer_read_refused')
            if not isinstance(value.get('result'), dict):
                raise ObserverError('invalid_response')
            return value['result']

    async def catalog_read(self):
        self.catalog, self.catalog_complete = [], False
        cursors, cursor, resources = set(), None, set()
        while True:
            if self.pages >= MAX_PAGES:
                raise ObserverError('catalog_page_limit')
            self.pages += 1
            params = {'channel': ROOT, 'limit': 50}
            if cursor is not None:
                params['cursor'] = cursor
            page = await self.request('listSessions', params)
            items = page.get('items')
            # 1.141.0 returns the whole catalog despite the limit hint. The
            # original global session/frame/byte bounds still apply.
            page_limit = MAX_SESSIONS if self.negotiated_version == '0.10.0' else 50
            if not isinstance(items, list) or len(items) > page_limit:
                raise ObserverError('invalid_catalog')
            for item in items:
                if len(self.catalog) >= MAX_SESSIONS:
                    raise ObserverError('catalog_session_limit')
                row = summary(item, self.negotiated_version)
                if 'createdAt' not in row or 'modifiedAt' not in row:
                    raise ObserverError('catalog_identity_unavailable')
                row['source_identity'] = digest({key: row[key] for key in ('resource', 'provider', 'createdAt')})
                if row['resource'] in resources:
                    raise ObserverError('catalog_changed_during_pagination')
                resources.add(row['resource'])
                self.catalog.append(row)
                self.positive_catalog.append({key: row[key] for key in ('resource', 'source_identity', 'source_kind')})
            cursor = page.get('nextCursor')
            if cursor is None:
                self.catalog_complete = True
                return resources
            text(cursor)
            if not cursor or cursor in cursors:
                raise ObserverError('catalog_cursor_cycle')
            cursors.add(cursor)

    async def collect_connection(self, reconnect):
        self.check()
        self.catalog_complete = False  # A prior connection cannot prove current absence.
        self.positive_catalog, self.positive_snapshots = [], []
        self.catalog_changed = False
        try:
            token = self.adapter.token()
        except Exception:
            raise ObserverError('credential_unavailable') from None
        self.tokens.append(token)
        endpoint, headers = self.adapter.endpoint, None
        if self.adapter.credential_transport == 'connection_token_query':
            endpoint += '?' + urlencode({'tkn': token})
        else:
            headers = {'Authorization': 'Bearer ' + token}
        async with _NoRedirect(endpoint, additional_headers=headers, proxy=None, compression=None,
                open_timeout=5, close_timeout=0.2, max_size=MAX_FRAME, max_queue=4,
                ping_interval=None, logger=_LOGGER) as ws:
            self.ws = ws
            if reconnect:
                if self.negotiated_version not in PROFILES or self.decoder_profile != PROFILES[self.negotiated_version]:
                    raise ObserverError('protocol_version_unsupported')
                result = await self.request('reconnect', {'channel': ROOT, 'clientId': self.client_id,
                    'lastSeenServerSeq': self.sequence, 'subscriptions': list(self.subscriptions)})
                # Reconnect has no renegotiation field in either contract. A peer
                # claiming a different version cannot change this original read.
                if result.get('protocolVersion', self.negotiated_version) != self.negotiated_version:
                    raise ObserverError('protocol_version_changed')
                self.reconnect_mode = result.get('type')
                if self.reconnect_mode == 'replay':
                    actions, missing = result.get('actions'), result.get('missing')
                    if not isinstance(actions, list) or not isinstance(missing, list) or len(actions) > MAX_FRAMES:
                        raise ObserverError('invalid_reconnect')
                    for envelope in actions:
                        self.event(envelope)
                    for resource in missing:
                        if resource not in self.subscriptions or resource == ROOT:
                            raise ObserverError('invalid_reconnect')
                        self.withdraw(resource)
                        self.subscriptions.remove(resource)
                elif self.reconnect_mode == 'snapshot':
                    self.gaps.append({'reason': 'snapshot_fallback', 'after_server_seq': self.sequence})
                    self.snapshots = {}
                    values = result.get('snapshots')
                    if not isinstance(values, list) or len(values) > 1 + MAX_SELECTED + MAX_CHANGESETS:
                        raise ObserverError('invalid_reconnect')
                    for value in values:
                        self.snapshot(value)
                else:
                    raise ObserverError('invalid_reconnect')
            else:
                result = await self.request('initialize', {'channel': ROOT, 'protocolVersions': list(PROFILES),
                    'clientId': self.client_id, 'clientInfo': {'name': 'bat-agent-connector'},
                    'initialSubscriptions': [ROOT]})
                version = result.get('protocolVersion')
                if not isinstance(version, str) or version not in PROFILES:
                    raise ObserverError('protocol_version_unsupported')
                self.negotiated_version, self.decoder_profile = version, PROFILES[version]
                self.sequence = integer(result.get('serverSeq'))
                info = result.get('serverInfo', {})
                if not isinstance(info, dict):
                    raise ObserverError('invalid_peer_shape')
                self.server_info = {key: text(info[key], 256) for key in ('name', 'version') if key in info}
                values = result.get('snapshots')
                if not isinstance(values, list) or len(values) != 1:
                    raise ObserverError('root_snapshot_required')
                self.snapshot(values[0])
            # Catalog cursors never survive a connection; root catalog events are
            # not replayed by AHP. Always read a new catalog after reconnect.
            resources = await self.catalog_read()
            for resource in self.selected:
                if resource not in resources or resource in self.missing:
                    self.withdraw(resource)
                    continue
                source = next(row for row in self.catalog if row['resource'] == resource)
                previous = self.session_sources.get(resource)
                if previous and previous['source_identity'] != source['source_identity']:
                    raise ObserverError('session_identity_changed')
                self.session_sources[resource] = {key: source[key] for key in (
                    'resource', 'provider', 'createdAt', 'source_identity')}
                if resource not in self.subscriptions:
                    self.subscriptions.append(resource)
                result = await self.request('subscribe', {'channel': resource})
                if 'snapshot' not in result:
                    # Reconnect may already have subscribed this channel. Original
                    # snapshot plus later event metadata remains explicitly dated.
                    if resource not in self.snapshots:
                        raise ObserverError('session_snapshot_unavailable')
                else:
                    self.snapshot(result['snapshot'])
                if resource in self.missing:
                    continue
                if self.changesets:
                    for entry in self.snapshots[resource]['state']['changesets']:
                        channel = entry['uriTemplate']
                        if not entry['read_supported']:
                            continue
                        if (channel in resources or channel == ROOT
                                or channel in self.changeset_owners and self.changeset_owners[channel] != resource):
                            raise ObserverError('changeset_source_ambiguous')
                        self.changeset_owners[channel] = resource
                        if channel not in self.subscriptions:
                            if sum(s in self.changeset_owners for s in self.subscriptions) >= MAX_CHANGESETS:
                                raise ObserverError('changeset_limit')
                            self.subscriptions.append(channel)
                        if channel in self.missing:
                            continue
                        result = await self.request('subscribe', {'channel': channel})
                        if 'snapshot' in result:
                            self.snapshot(result['snapshot'])
                        elif channel not in self.snapshots:
                            raise ObserverError('changeset_snapshot_unavailable')

    async def collect(self):
        reason = None
        try:
            async def bounded():
                for attempt in range(2):
                    try:
                        await self.collect_connection(bool(attempt))
                        return
                    except (ConnectionClosed, OSError):
                        if attempt or not self.sequence:
                            raise ObserverError('transport_unavailable') from None
            await asyncio.wait_for(bounded(), MAX_SECONDS)
        except asyncio.TimeoutError:
            reason = 'deadline_exceeded'
        except ObserverError as exc:
            reason = exc.reason
        except (WebSocketException, OSError, ValueError, TypeError, KeyError, RecursionError):
            # Never include transport exceptions: they may contain credential URLs.
            reason = 'invalid_or_unavailable_peer'
        self.check()
        for snapshot in self.snapshots.values():
            snapshot['later_event_observed'] = any(e['channel'] == snapshot['resource']
                and e['server_seq'] > snapshot['from_seq'] for e in self.events)
        if self.missing and reason is None:
            reason = 'selected_source_unavailable'
        if self.catalog_changed:
            self.catalog_complete = False
            reason = reason or 'catalog_changed_during_capture'
        result = {'protocol_version': self.negotiated_version, 'negotiated_version': self.negotiated_version,
            'decoder_profile': self.decoder_profile, 'server_info': self.server_info,
            'status': 'incomplete' if reason else 'observed', 'reason': reason,
            'catalog': self.catalog, 'catalog_complete': self.catalog_complete,
            'snapshots': list(self.snapshots.values()), 'events': self.events,
            'last_server_seq': self.sequence, 'reconnect': self.reconnect_mode,
            'gaps': self.gaps, 'missing': self.missing, 'bytes': self.bytes, 'frames': self.frames,
            'pages': self.pages, 'coverage': 'bounded_advertised_catalog_and_selected_point_snapshots',
            'extension_host_sessions': 'not_assumed_exposed', 'completion': 'not_inferred',
            'cost': 'not_inferred', 'create_own': 'unsupported', 'pm_writer': 'unsupported'}
        result['notifications'] = self.notifications
        result['positive_catalog'] = self.positive_catalog
        result['positive_snapshots'] = self.positive_snapshots
        result['changeset_owners'] = self.changeset_owners
        # A peer may echo the upgrade token in an otherwise allowed text field.
        # Keep raw envelope digests, but never persist the transport credential.
        def scrub(value):
            if isinstance(value, str):
                for token in self.tokens:
                    value = value.replace(token, '<redacted>')
                return value
            if isinstance(value, list):
                return [scrub(item) for item in value]
            if isinstance(value, dict):
                return {scrub(key): scrub(item) for key, item in value.items()}
            return value
        result['catalog'] = [row for row in result['catalog'] if row['resource'] not in self.missing]
        return scrub(result)
