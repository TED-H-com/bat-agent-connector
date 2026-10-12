"""Real local WebSocket fixtures exercise the pinned read subset, never a model."""
from __future__ import annotations

import asyncio
import copy
import json
from urllib.parse import parse_qs, urlsplit

import pytest
from websockets.asyncio.server import serve

from bat_agent_connector import ahp_observer as ahp
from bat_agent_connector.ahp_config import AhpAdapter, parse_ahp
from bat_agent_connector.config import ConfigError, parse_config

WID = 'ws_' + '1' * 32
SESSION = 'ahp-session:/manual-one'
CHANGESET = 'ahp-changeset:/manual-one/session'
TOKEN = 'private-ahp-transport-token-for-offline-fixture'
SUMMARY = {'resource': SESSION, 'provider': 'fixture-provider', 'title': 'Human session', 'status': 1,
           'createdAt': '2026-10-11T00:00:00Z', 'modifiedAt': '2026-10-11T00:00:00Z',
           'workingDirectories': ['file:///human/checkout']}
STATE = {**SUMMARY, 'lifecycle': 'ready', 'customizations': [{'secret': TOKEN}],
         'changesets': [{'label': 'Session changes', 'uriTemplate': CHANGESET, 'changeKind': 'session'},
                       {'label': 'Per turn', 'uriTemplate': CHANGESET + '/{turnId}', 'changeKind': 'turn'}]}


def snapshot(resource, state, seq=10):
    return {'resource': resource, 'state': state, 'fromSeq': seq}


class Host:
    def __init__(self):
        self.requests, self.connections = [], 0
        self.on_request = None
        self.reconnect = {'type': 'replay', 'actions': [], 'missing': []}
        self.session_state = copy.deepcopy(STATE)
        self.catalog = [copy.deepcopy(SUMMARY)]
        self.closed = 0
        self.closed_event = asyncio.Event()

    async def handler(self, ws):
        self.connections += 1
        connection = self.connections
        assert parse_qs(urlsplit(ws.request.path).query) == {'tkn': [TOKEN]}
        try:
            async for raw in ws:
                request = json.loads(raw)
                self.requests.append((connection, request))
                if self.on_request and await self.on_request(ws, request, connection):
                    continue
                method, params = request['method'], request['params']
                assert method in ahp.METHODS
                if method == 'initialize':
                    result = {'protocolVersion': '1.0.0', 'serverSeq': 10,
                              'serverInfo': {'name': 'offline-host', 'version': 'fixture-only'},
                              'snapshots': [snapshot(ahp.ROOT, {'agents': [{'config': TOKEN}]})]}
                elif method == 'listSessions':
                    result = {'items': self.catalog}
                elif method == 'reconnect':
                    result = self.reconnect
                elif params['channel'] == SESSION:
                    result = {'snapshot': snapshot(SESSION, self.session_state)}
                elif params['channel'] == CHANGESET:
                    result = {'snapshot': snapshot(CHANGESET, {'status': 'ready', 'files': [
                        {'id': 'file-1', 'edit': {'before': {'uri': 'file:///human/checkout/a.py',
                            'content': {'uri': 'data:secret-code'}}, 'after': {'uri': 'file:///human/checkout/b.py'},
                            'diff': {'added': 2, 'removed': 1}}}], 'operations': [{'id': 'apply'}]})}
                else:
                    pytest.fail('unexpected subscription')
                await ws.send(json.dumps({'jsonrpc': '2.0', 'id': request['id'], 'result': result}))
        finally:
            self.closed += 1
            self.closed_event.set()


@pytest.fixture
async def host(monkeypatch):
    monkeypatch.setenv('AHP_FIXTURE_TOKEN', TOKEN)
    host = Host()
    async with serve(host.handler, '127.0.0.1', 0) as server:
        host.adapter = AhpAdapter('local', (WID,), f'ws://127.0.0.1:{server.sockets[0].getsockname()[1]}/ahp', 'env:AHP_FIXTURE_TOKEN')
        yield host


def observer(host, *, check=lambda: None, selected=None):
    return ahp.Observer(host.adapter, client_id='original-observer-id', selected=[SESSION] if selected is None else selected,
                        changesets=True, check=check)


async def test_actual_catalog_selected_static_changesets_and_secret_minimization(host):
    value = await observer(host).collect()
    assert value['status'] == 'observed' and value['catalog_complete']
    assert [r['method'] for _, r in host.requests] == ['initialize', 'listSessions', 'subscribe', 'subscribe']
    assert value['catalog'][0]['ownership'] == 'foreign_or_manual'
    assert value['completion'] == value['cost'] == 'not_inferred'
    assert value['create_own'] == value['pm_writer'] == 'unsupported'
    assert value['snapshots'][2]['state']['files'][0]['edit']['diff'] == {'added': 2, 'removed': 1}
    assert not value['snapshots'][1]['state']['changesets'][1]['read_supported']
    encoded = json.dumps(value)
    assert TOKEN not in encoded and 'data:secret-code' not in encoded and '"apply"' not in encoded
    await asyncio.wait_for(host.closed_event.wait(), 1)
    assert 'customizations' not in encoded and host.connections == 1 and host.closed == 1


async def test_reconnect_same_identity_refreshes_catalog_and_deduplicates_exact_events(host):
    event = {'channel': SESSION, 'serverSeq': 13, 'action': {'type': 'session/titleChanged', 'title': 'Later title'}}
    host.reconnect = {'type': 'replay', 'actions': [event, event], 'missing': []}
    async def drop(ws, req, connection):
        if connection == 1 and req['method'] == 'subscribe' and req['params']['channel'] == CHANGESET:
            await ws.close()
            return True
        return False
    host.on_request = drop
    result = await observer(host).collect()
    assert result['status'] == 'observed' and result['reconnect'] == 'replay'
    assert len(result['events']) == 1 and result['last_server_seq'] == 13
    assert result['events'][0]['original_digest'] == ahp.digest(event)
    assert result['snapshots'][1]['later_event_observed']
    rec = next(r for _, r in host.requests if r['method'] == 'reconnect')
    assert rec['params']['clientId'] == 'original-observer-id' and rec['params']['lastSeenServerSeq'] == 10
    assert [c for c, r in host.requests if r['method'] == 'listSessions'] == [1, 2]
    assert all('cursor' not in r['params'] for _, r in host.requests if r['method'] == 'listSessions')


@pytest.mark.parametrize('kind', ['snapshot', 'missing', 'conflict'])
async def test_reconnect_gap_withdrawal_and_conflicting_sequence_are_honest(host, kind):
    event = {'channel': SESSION, 'serverSeq': 13, 'action': {'type': 'session/titleChanged', 'title': 'Original'}}
    if kind == 'snapshot':
        host.reconnect = {'type': 'snapshot', 'snapshots': [snapshot(ahp.ROOT, {}, 40), snapshot(SESSION, STATE, 40)]}
    elif kind == 'missing':
        host.reconnect = {'type': 'replay', 'actions': [], 'missing': [SESSION]}
    else:
        host.reconnect = {'type': 'replay', 'actions': [event, {**event, 'action': {'type': 'different'}}], 'missing': []}
    async def drop(ws, req, connection):
        if connection == 1 and req['method'] == 'subscribe' and req['params']['channel'] == CHANGESET:
            await ws.close()
            return True
        return False
    host.on_request = drop
    result = await observer(host).collect()
    if kind == 'snapshot':
        assert result['gaps'] == [{'reason': 'snapshot_fallback', 'after_server_seq': 10}]
    elif kind == 'missing':
        assert result['status'] == 'incomplete' and result['catalog'] == []
        assert [s['resource'] for s in result['snapshots']] == [ahp.ROOT]
        assert not any(c == 2 and r['method'] == 'subscribe' for c, r in host.requests)
    else:
        assert result['reason'] == 'conflicting_event_identity'


async def test_current_authority_checked_between_frames_and_before_result(host):
    allowed = True
    def check():
        if not allowed:
            raise PermissionError('withdrawn')
    async def revoke(ws, req, connection):
        nonlocal allowed
        if req['method'] == 'listSessions':
            allowed = False
        return False
    host.on_request = revoke
    with pytest.raises(PermissionError):
        await observer(host, check=check).collect()
    assert [r['method'] for _, r in host.requests] == ['initialize', 'listSessions']
    await asyncio.wait_for(host.closed_event.wait(), 1)
    assert host.closed == 1


async def test_original_total_deadline_closes_stalled_socket(host, monkeypatch):
    async def stall(ws, req, connection):
        await ws.wait_closed()
        return True
    host.on_request = stall
    monkeypatch.setattr(ahp, 'MAX_SECONDS', .08)
    start = asyncio.get_running_loop().time()
    result = await observer(host).collect()
    assert result['reason'] == 'deadline_exceeded'
    assert asyncio.get_running_loop().time() - start < 1
    assert host.connections == 1


@pytest.mark.parametrize('failure', ['cursor', 'pages', 'binary', 'version', 'frame', 'server-request', 'secret-echo'])
async def test_peer_limits_and_unsupported_frames_never_issue_write_methods(host, monkeypatch, failure):
    async def change(ws, req, connection):
        if failure in {'cursor', 'pages'} and req['method'] == 'listSessions':
            result = {'items': [], 'nextCursor': 'again' if failure == 'cursor' else str(len(host.requests))}
        elif failure == 'version' and req['method'] == 'initialize':
            result = {'protocolVersion': '0.9.0'}
        elif failure == 'binary':
            await ws.send(b'{}')
            return True
        elif failure == 'frame':
            await ws.send('x' * (ahp.MAX_FRAME + 1))
            return True
        elif failure == 'server-request':
            await ws.send(json.dumps({'jsonrpc': '2.0', 'id': 999, 'method': 'tools/call', 'params': {}}))
            return True
        else:
            return False
        await ws.send(json.dumps({'jsonrpc': '2.0', 'id': req['id'], 'result': result}))
        return True
    host.on_request = change
    if failure == 'secret-echo':
        host.catalog[0]['title'] = TOKEN
    value = await observer(host).collect()
    assert all(r['method'] in ahp.METHODS for _, r in host.requests)
    if failure == 'secret-echo':
        assert TOKEN not in json.dumps(value) and value['catalog'][0]['title'] == '<redacted>'
    else:
        assert value['status'] == 'incomplete'
        assert value['reason'] in {'catalog_cursor_cycle', 'catalog_page_limit', 'binary_frame_refused',
            'protocol_version_unsupported', 'transport_unavailable', 'server_request_unsupported'}


def test_admin_config_is_explicit_and_does_not_resolve_secrets(monkeypatch):
    monkeypatch.delenv('MISSING_AHP_TOKEN', raising=False)
    value = {'workspace_ids': [WID], 'endpoint': 'ws://127.0.0.1:4567/', 'credential_ref': 'env:MISSING_AHP_TOKEN'}
    cfg = parse_config({'ahp': {'adapters': {'local': value}}})
    assert cfg.ahp['local'].workspace_ids == (WID,)
    assert len(cfg.ahp['local'].binding_digest()) == 64
    assert parse_config({}).ahp == {}
    for delta in [{'token': 'inline'}, {'workspace_ids': ['*']}, {'workspace_ids': []},
                  {'endpoint': 'ws://example.test/'}, {'endpoint': 'wss://user:secret@example.test/'},
                  {'endpoint': 'wss://example.test/?tkn=secret'}, {'endpoint': 'wss://example.test/#fragment'},
                  {'endpoint': 'file:///tmp/host'}, {'credential_ref': 'literal:secret'},
                  {'credential_transport': 'authenticate'}, {'enabled': 'yes'}]:
        with pytest.raises(ConfigError):
            parse_ahp({'ahp': {'adapters': {'local': {**value, **delta}}}})


async def test_root_disposal_notification_cannot_be_revived_by_later_snapshot_response(host):
    async def disposal(ws, req, connection):
        if req['method'] == 'subscribe' and req['params']['channel'] == SESSION:
            await ws.send(json.dumps({'jsonrpc': '2.0', 'method': 'root/sessionRemoved',
                                      'params': {'channel': ahp.ROOT, 'session': SESSION}}))
        return False
    host.on_request = disposal
    result = await observer(host).collect()
    assert result['status'] == 'incomplete' and not result['catalog_complete']
    assert result['catalog'] == [] and result['missing'] == [SESSION]
    assert [s['resource'] for s in result['snapshots']] == [ahp.ROOT]
    assert result['notifications'][0]['method'] == 'root/sessionRemoved'
    assert not any(r['params'].get('channel') == CHANGESET for _, r in host.requests)


async def test_upgrade_redirect_never_transmits_credential_to_second_endpoint(monkeypatch):
    from websockets.datastructures import Headers
    from websockets.http11 import Response
    monkeypatch.setenv('AHP_FIXTURE_TOKEN', TOKEN)
    received = []
    async def sink(ws):
        received.append(ws.request.path)
    async with serve(sink, '127.0.0.1', 0) as second:
        endpoint = f'ws://127.0.0.1:{second.sockets[0].getsockname()[1]}/'
        def redirect(connection, request):
            return Response(302, 'Found', Headers({'Location': endpoint}), b'')
        async with serve(sink, '127.0.0.1', 0, process_request=redirect) as first:
            adapter = AhpAdapter('local', (WID,), f'ws://127.0.0.1:{first.sockets[0].getsockname()[1]}/', 'env:AHP_FIXTURE_TOKEN')
            result = await ahp.Observer(adapter, client_id='redirect-fixture', selected=[], changesets=False, check=lambda: None).collect()
    assert result['status'] == 'incomplete' and not received
    assert TOKEN not in json.dumps(result)
