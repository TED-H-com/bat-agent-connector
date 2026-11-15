"""Portable handshake examples exercised over the mock's actual TLS wire."""
from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import ssl
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
import websockets.exceptions
from websockets.asyncio.client import connect

from tests.mockbat import TOKEN, MockBac, MockBat

CASES = json.loads((Path(__file__).parent / 'fixtures/bac/hello-cases.json').read_text())
HELLO = {'protocol': 'bac/1', 'minor': 0}


@asynccontextmanager
async def wire(mock):
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    async with connect(mock.url, ssl=ctx, compression=None, proxy=None) as ws:
        await ws.send(json.dumps({'type': 'auth', 'id': 'auth-1', 'token': TOKEN,
                                  'protocols': ['bat-remote/v2']}))
        auth = json.loads(await ws.recv())
        assert auth['result'] is True
        yield ws


async def call(ws, channel, params, mid='case', **extra):
    await ws.send(json.dumps({'type': 'invoke', 'id': mid, 'channel': channel, 'params': params, **extra}))
    reply = json.loads(await ws.recv())
    assert reply['type'] == 'invoke-result' and reply['id'] == mid
    return reply['result']


def resolve(value, bac):
    if isinstance(value, dict):
        return {key: resolve(item, bac) for key, item in value.items()}
    if isinstance(value, list):
        return [resolve(item, bac) for item in value]
    if value == '$HOST_ID':
        return bac.host_id
    if value == '$BUILD':
        return bac.build
    if value == '$STORAGE_REASON':
        return bac.storage_reason
    if value == '$FEATURES':
        return bac.features
    if value == '$LIMITS':
        return bac.limits
    return value


@pytest.fixture
async def bac_mock():
    mock = MockBat(bac=True)
    await mock.start()
    try:
        yield mock
    finally:
        await mock.stop()


@pytest.mark.parametrize('case', CASES['cases'], ids=lambda case: case['name'])
@pytest.mark.parametrize('features_enabled', [False, True], ids=['features-off', 'features-on'])
async def test_portable_hello_case(bac_mock, case, features_enabled):
    if features_enabled:
        bac_mock.bac.features = dict.fromkeys(bac_mock.bac.features, True)
        bac_mock.bac.limits['profiles'] *= 2
    for flag, value in case.get('appliesWhen', {}).get('features', {}).items():
        if bac_mock.bac.features.get(flag) is not value:
            pytest.skip('case applies only to the specified feature availability')
    situation = case['situation']
    async with wire(bac_mock) as ws:
        if situation['connection'] == 'negotiated':
            assert (await call(ws, 'bac:hello', HELLO))['ok'] is True
        storage = situation.get('identityStorage')
        if storage:
            bac_mock.bac.storage_error = 'STORAGE_' + storage.upper()
        reply = await call(ws, case.get('channel', 'bac:hello'), resolve(case['params'], bac_mock.bac),
                           case['name'], **situation.get('frame', {}))
        assert reply == resolve(case['result'], bac_mock.bac)
        if 'after' in case:
            probe = await call(ws, 'bac:workspace-read', {})
            negotiated = probe.get('error', {}).get('code') != 'NOT_OPTED_IN'
            assert negotiated == (case['after'] == 'negotiated')


async def test_pre_and_post_hello_refusals_are_connection_local(bac_mock):
    def forbidden(_):
        pytest.fail('BAC reached legacy dispatch')

    channels = [*bac_mock.bac.CHANNELS, 'bac:future']
    bac_mock.handlers.update(dict.fromkeys(channels, forbidden))
    async with wire(bac_mock) as first, wire(bac_mock) as second:
        for channel in channels:
            assert (await call(first, channel, None, contextId=None))['error']['code'] == 'NOT_OPTED_IN'
        assert (await call(first, 'bac:hello', HELLO))['ok'] is True
        for channel in channels:
            reply = await call(first, channel, {})
            code = 'UNKNOWN_CHANNEL' if channel == 'bac:future' else 'UNSUPPORTED_FEATURE'
            assert reply['error']['code'] == code
            assert (await call(second, channel, {}))['error']['code'] == 'NOT_OPTED_IN'
        assert (await call(first, 'bac:hello', HELLO))['ok'] is True
    async with wire(bac_mock) as third:
        assert (await call(third, 'bac:future', {}))['error']['code'] == 'NOT_OPTED_IN'
    assert len(bac_mock.bac.frames) == 3 * len(channels) + 3


async def test_frame_id_and_size_boundaries(bac_mock):
    async with wire(bac_mock) as ws:
        for mid in ('', 'x' * 129, '\0', 7, 'x' * 600_000):
            await ws.send(json.dumps({'type': 'invoke', 'id': mid, 'channel': 'bac:hello', 'params': HELLO}))
            raw = await ws.recv()
            reply = json.loads(raw)
            assert reply['id'] is None
            assert len(raw.encode('utf-8')) <= 524288
            assert reply['result']['error']['code'] == 'INVALID_REQUEST'
        assert (await call(ws, 'bac:hello', HELLO, 'x' * 128))['ok']
        assert (await call(ws, 'bac:future\0', {}))['error'] == {
            'code': 'INVALID_REQUEST', 'message': 'Invalid BAC request',
            'retryable': False, 'details': {'field': 'channel'}}
        assert (await call(ws, 'bac:' + 'x' * 125, {}))['error']['details'] == {'field': 'channel'}
        frame = {'type': 'invoke', 'id': 'case', 'channel': 'bac:hello',
                 'params': {'protocol': '', 'minor': 0}}
        overhead = len(json.dumps(frame, separators=(',', ':')).encode())
        for size, expected in ((65536, 'UNSUPPORTED_VERSION'), (65537, 'PAYLOAD_TOO_LARGE')):
            params = {'protocol': 'x' * (size - overhead), 'minor': 0}
            assert (await call(ws, 'bac:hello', params))['error']['code'] == expected


async def test_reply_size_boundary_is_inclusive(bac_mock):
    async with wire(bac_mock) as ws:
        frame = {'type': 'invoke-result', 'id': 'case', 'result': ''}
        overhead = len(json.dumps(frame, separators=(',', ':'), ensure_ascii=False).encode('utf-8'))
        for size in (524288, 524289):
            bac_mock.bac.hello_result = 'x' * (size - overhead)
            await ws.send(json.dumps({'type': 'invoke', 'id': 'case', 'channel': 'bac:hello', 'params': HELLO}))
            raw = await ws.recv()
            reply = json.loads(raw)
            assert reply['id'] == 'case'
            if size == 524288:
                assert len(raw.encode('utf-8')) == size
                assert reply['result'] == bac_mock.bac.hello_result
            else:
                assert reply['result'] == {'ok': False, 'error': {
                    'code': 'RESPONSE_TOO_LARGE', 'message': 'BAC result exceeds limit',
                    'retryable': False, 'details': {'maxBytes': 524288}}}


async def test_legacy_protocol_does_not_advertise_or_negotiate(bac_mock):
    bac_mock.bac.storage_error = 'STORAGE_UNAVAILABLE'
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    async with connect(bac_mock.url, ssl=ctx, compression=None, proxy=None) as ws:
        await ws.send(json.dumps({'type': 'auth', 'id': 'auth-1', 'token': TOKEN, 'protocols': []}))
        auth = json.loads(await ws.recv())
        assert auth['protocol'] == 'bat-remote/legacy-v1'
        assert 'bac' not in auth['capabilities']
        assert await call(ws, 'bac:hello', HELLO) == {'ok': False, 'error': {
            'code': 'UNSUPPORTED_VERSION', 'message': 'Unsupported BAC protocol version',
            'retryable': False, 'details': {'protocol': 'bac/1', 'minor': 0}}}
        assert (await call(ws, 'bac:future', {}))['error']['code'] == 'NOT_OPTED_IN'


@pytest.mark.parametrize(('changes', 'field'), [
    ({'type': 'event', 'args': []}, 'type'),
    ({'id': '', 'args': []}, 'id'),
    ({'channel': 'bac:' + 'x' * 125, 'args': []}, 'channel'),
    ({'args': []}, 'frame'),
])
def test_common_shape_validation_order(changes, field):
    frame = {'type': 'invoke', 'id': 'case', 'channel': 'bac:hello', 'params': HELLO, **changes}
    result, _ = MockBac().reply(frame, True, 'bat-remote/v2')
    assert result['error']['details'] == {'field': field}


@pytest.mark.parametrize('missing', ['id', 'channel'])
async def test_direct_handle_missing_required_key_returns_error(missing):
    class Socket:
        async def send(self, data):
            self.reply = json.loads(data)

    frame = {'type': 'invoke', 'id': 'case', 'channel': 'bac:hello', 'params': HELLO}
    del frame[missing]
    socket = Socket()
    assert await MockBac().handle(socket, frame, False, 'bat-remote/v2') is False
    assert socket.reply['result']['error']['details'] == {'field': missing}
    if missing == 'id':
        assert socket.reply['id'] is None


async def test_internal_storage_error_has_no_reason(bac_mock):
    bac_mock.bac.storage_error = 'INTERNAL_ERROR'
    async with wire(bac_mock) as ws:
        result = await call(ws, 'bac:hello', HELLO)
        assert result == {'ok': False, 'error': {'code': 'INTERNAL_ERROR',
                           'message': 'BAC request failed', 'retryable': False, 'details': {}}}
        assert (await call(ws, 'bac:future', {}))['error']['code'] == 'NOT_OPTED_IN'


@pytest.mark.parametrize('channel', [None, 7, ['bac:hello']])
async def test_non_string_channel_keeps_legacy_dispatch_path(bac_mock, channel):
    async with wire(bac_mock) as ws:
        await ws.send(json.dumps({'type': 'invoke', 'id': 'case', 'channel': channel, 'params': {}}))
        reply = json.loads(await ws.recv())
        assert reply['id'] == 'case'
        assert reply['type'] in {'invoke-result', 'invoke-error'}
        assert not bac_mock.bac.frames


def digest(document):
    encoded = json.dumps(document, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode('utf-8')
    return 'sha256:' + hashlib.sha256(encoded).hexdigest()


def rendered_document(ws_doc):
    return copy.deepcopy({
        'workspaces': ws_doc.get('workspaces', []),
        'activeWorkspaceId': ws_doc.get('activeWorkspaceId'),
        'activeGroup': ws_doc.get('activeGroup'),
        'terminals': ws_doc.get('terminals', []),
        'activeTerminalId': ws_doc.get('activeTerminalId'),
    })


@pytest.fixture
def ws_mock(bac_mock):
    bac_mock.bac.enable_workspace(bac_mock)
    return bac_mock


def test_revision_canonical_json_and_utf8():
    empty = {
        'workspaces': [],
        'activeWorkspaceId': None,
        'activeGroup': None,
        'terminals': [],
        'activeTerminalId': None,
    }
    expected_empty = 'sha256:66c1b7a04a05ee220e10341c9baec59bdf92885b193c2838d527a235d2ca5a9c'
    assert MockBac.revision(empty) == expected_empty
    assert digest(empty) == expected_empty

    reordered = {
        'activeTerminalId': None,
        'terminals': [{'b': 2, 'a': 1}],
        'activeGroup': None,
        'activeWorkspaceId': None,
        'workspaces': [{'z': 0, 'id': 'w1'}],
    }
    ordered = {
        'workspaces': [{'id': 'w1', 'z': 0}],
        'activeWorkspaceId': None,
        'activeGroup': None,
        'terminals': [{'a': 1, 'b': 2}],
        'activeTerminalId': None,
    }
    assert MockBac.revision(reordered) == MockBac.revision(ordered) == digest(ordered)

    non_ascii = {**empty, 'terminals': [{'id': 't1', 'title': '終端機 — café'}]}
    assert MockBac.revision(non_ascii) != expected_empty
    assert MockBac.revision(non_ascii) == digest(non_ascii)


async def test_feature_gates_and_enable_workspace(bac_mock):
    async with wire(bac_mock) as ws:
        for channel, params in (
            ('bac:workspace-read', {'profileId': 'default'}),
            ('bac:session-locate', {'sessionId': 'sess-claude-0001'}),
        ):
            assert await call(ws, channel, params) == {
                'ok': False,
                'error': {
                    'code': 'NOT_OPTED_IN',
                    'message': 'Call bac:hello first',
                    'retryable': False,
                    'details': {},
                },
            }
        assert (await call(ws, 'bac:hello', HELLO))['ok'] is True
        assert await call(ws, 'bac:workspace-read', {'profileId': 'default'}) == {
            'ok': False,
            'error': {
                'code': 'UNSUPPORTED_FEATURE',
                'message': 'BAC feature is unavailable',
                'retryable': False,
                'details': {'feature': 'workspaceRead'},
            },
        }
        assert await call(ws, 'bac:session-locate', {'sessionId': 'sess-claude-0001'}) == {
            'ok': False,
            'error': {
                'code': 'UNSUPPORTED_FEATURE',
                'message': 'BAC feature is unavailable',
                'retryable': False,
                'details': {'feature': 'sessionLocate'},
            },
        }
        bac_mock.bac.features = dict.fromkeys(bac_mock.bac.features, True)
        for channel, params in (
            ('bac:workspace-read', {'profileId': 'default'}),
            ('bac:session-locate', {'sessionId': 'sess-claude-0001'}),
        ):
            assert await call(ws, channel, params) == {
                'ok': False,
                'error': {
                    'code': 'INTERNAL_ERROR',
                    'message': 'BAC request failed',
                    'retryable': False,
                    'details': {},
                },
            }

    bac_mock.bac.features = dict.fromkeys(bac_mock.bac.CHANNELS.values(), False)
    bac_mock.bac.enable_workspace(bac_mock)
    async with wire(bac_mock) as ws:
        hello = await call(ws, 'bac:hello', HELLO)
        assert hello['ok'] is True
        assert hello['value']['features'] == {
            'workspaceRead': True,
            'sessionLocate': True,
            'workspaceAddTerminal': False,
            'gitObserve': False,
        }
        assert await call(ws, 'bac:workspace-add-terminal', {}) == {
            'ok': False,
            'error': {
                'code': 'UNSUPPORTED_FEATURE',
                'message': 'BAC feature is unavailable',
                'retryable': False,
                'details': {'feature': 'workspaceAddTerminal'},
            },
        }
        bac_mock.bac.features['workspaceAddTerminal'] = True
        assert await call(ws, 'bac:workspace-add-terminal', {}) == {
            'ok': False,
            'error': {
                'code': 'INVALID_REQUEST',
                'message': 'Invalid BAC request',
                'retryable': False,
                'details': {'field': 'params.profileId'},
            },
        }


async def test_read_and_locate_request_validation(ws_mock):
    def invalid(field):
        return {
            'ok': False,
            'error': {
                'code': 'INVALID_REQUEST',
                'message': 'Invalid BAC request',
                'retryable': False,
                'details': {'field': field},
            },
        }

    async with wire(ws_mock) as ws:
        assert (await call(ws, 'bac:hello', HELLO))['ok'] is True
        for channel, key, other_key in (
            ('bac:workspace-read', 'profileId', 'sessionId'),
            ('bac:session-locate', 'sessionId', 'profileId'),
        ):
            assert await call(ws, channel, {}) == invalid(f'params.{key}')
            assert await call(ws, channel, {key: 'default', 'extra': 1}) == invalid('params')
            assert await call(ws, channel, {key: '', 'extra': 1}) == invalid('params')
            assert await call(ws, channel, {other_key: 'default'}) == invalid('params')
            for bad_value in (7, None, ['default'], '', 'a' * 129, 'a b'):
                assert await call(ws, channel, {key: bad_value}) == invalid(f'params.{key}')

        for bad_profile in ('a:b', '設' * 43, 'é'):
            assert await call(ws, 'bac:workspace-read', {'profileId': bad_profile}) == invalid(
                'params.profileId'
            )
        assert await call(ws, 'bac:session-locate', {'sessionId': '設'}) == invalid(
            'params.sessionId'
        )

        assert await call(ws, 'bac:workspace-read', {'profileId': '設' * 42}) == {
            'ok': False,
            'error': {
                'code': 'PROFILE_NOT_FOUND',
                'message': 'Local profile not found',
                'retryable': False,
                'details': {},
            },
        }
        doc = rendered_document(ws_mock.ws_doc)
        rev = digest(doc)
        loc_rev = digest([['default', 'persisted', None, rev]])
        assert await call(ws, 'bac:session-locate', {'sessionId': 'a:b.c_d-9'}) == {
            'ok': True,
            'value': {
                'hostId': ws_mock.bac.host_id,
                'locationRevision': loc_rev,
                'disposition': 'unregistered',
                'consistency': 'sequential',
                'atomic': False,
                'locations': [],
            },
        }


async def test_workspace_read_default_and_copy_semantics(ws_mock):
    before = copy.deepcopy(ws_mock.ws_doc)
    expected_doc = rendered_document(before)
    assert expected_doc['terminals'][0]['id'] == 'sess-claude-0001'
    assert expected_doc['terminals'][0]['sdkSessionId'] == 'sdk-1'
    assert expected_doc['terminals'][0]['model'] == 'm1'
    assert expected_doc['activeGroup'] is None
    assert expected_doc['activeTerminalId'] is None
    expected_rev = digest(expected_doc)

    async with wire(ws_mock) as ws:
        assert (await call(ws, 'bac:hello', HELLO))['ok'] is True
        reply = await call(ws, 'bac:workspace-read', {'profileId': 'default'})
        assert reply == {
            'ok': True,
            'value': {
                'hostId': ws_mock.bac.host_id,
                'profileId': 'default',
                'source': 'persisted',
                'windowId': None,
                'revision': expected_rev,
                'document': expected_doc,
                'appendTargetEligible': True,
            },
        }
        assert ws_mock.ws_doc == before

        ws_mock.ws_doc['activeTerminalId'] = 'sess-claude-0001'
        ws_mock.ws_doc['extraTopLevel'] = 'ignored'
        ws_mock.ws_doc['workspaces'].append({'id': 'ws-3', 'name': 'third', 'folderPath': '/srv/3'})
        updated_doc = rendered_document(ws_mock.ws_doc)
        assert 'extraTopLevel' not in updated_doc
        updated_rev = digest(updated_doc)
        assert updated_rev != expected_rev
        second = await call(ws, 'bac:workspace-read', {'profileId': 'default'})
        assert second == {
            'ok': True,
            'value': {
                'hostId': ws_mock.bac.host_id,
                'profileId': 'default',
                'source': 'persisted',
                'windowId': None,
                'revision': updated_rev,
                'document': updated_doc,
                'appendTargetEligible': True,
            },
        }


@pytest.mark.parametrize(
    'bad_ws_doc',
    [
        {'workspaces': [{'id': 'ws-1'}]},
        {'workspaces': 'not-a-list', 'terminals': [{'id': 't-1'}]},
        {'workspaces': ['not-an-object'], 'terminals': [{'id': 't-1'}]},
        {'workspaces': [{'id': ''}], 'terminals': [{'id': 't-1'}]},
        {'workspaces': [{'id': 7}], 'terminals': [{'id': 't-1'}]},
        {'workspaces': [{'id': 'ws-1'}], 'terminals': [{'id': 't-1'}, {'id': 't-1'}]},
        {'workspaces': [{'id': 'ws-1'}, {'id': 'ws-1'}], 'terminals': [{'id': 't-1'}]},
    ],
    ids=[
        'missing-terminals',
        'workspaces-not-list',
        'entry-not-object',
        'empty-id',
        'non-string-id',
        'duplicate-terminal-id',
        'duplicate-workspace-id',
    ],
)
async def test_workspace_read_append_target_ineligible(ws_mock, bad_ws_doc):
    ws_mock.ws_doc = copy.deepcopy(bad_ws_doc)
    expected_doc = rendered_document(ws_mock.ws_doc)
    expected_rev = digest(expected_doc)
    async with wire(ws_mock) as ws:
        assert (await call(ws, 'bac:hello', HELLO))['ok'] is True
        assert await call(ws, 'bac:workspace-read', {'profileId': 'default'}) == {
            'ok': True,
            'value': {
                'hostId': ws_mock.bac.host_id,
                'profileId': 'default',
                'source': 'persisted',
                'windowId': None,
                'revision': expected_rev,
                'document': expected_doc,
                'appendTargetEligible': False,
            },
        }


async def test_session_locate_rows_dispositions_and_extra_locations(ws_mock):
    async with wire(ws_mock) as ws:
        assert (await call(ws, 'bac:hello', HELLO))['ok'] is True
        doc = rendered_document(ws_mock.ws_doc)
        rev = digest(doc)
        loc_rev = digest([['default', 'persisted', None, rev]])
        expected_row = {
            'profileId': 'default',
            'workspaceId': 'ws-1',
            'terminalId': 'sess-claude-0001',
            'windowId': None,
            'source': 'persisted',
            'profileRevision': rev,
        }
        assert await call(ws, 'bac:session-locate', {'sessionId': 'sess-claude-0001'}) == {
            'ok': True,
            'value': {
                'hostId': ws_mock.bac.host_id,
                'locationRevision': loc_rev,
                'disposition': 'located',
                'consistency': 'sequential',
                'atomic': False,
                'locations': [expected_row],
            },
        }
        assert await call(ws, 'bac:session-locate', {'sessionId': 'sess-unknown'}) == {
            'ok': True,
            'value': {
                'hostId': ws_mock.bac.host_id,
                'locationRevision': loc_rev,
                'disposition': 'unregistered',
                'consistency': 'sequential',
                'atomic': False,
                'locations': [],
            },
        }

        ws_mock.ws_doc['terminals'].extend([
            {'id': 'sess-orphan', 'workspaceId': 'ws-missing'},
            {'id': 'sess-non-str-ws', 'workspaceId': 99},
        ])
        rev_with_invalid = digest(rendered_document(ws_mock.ws_doc))
        loc_rev_with_invalid = digest([['default', 'persisted', None, rev_with_invalid]])
        for sid in ('sess-orphan', 'sess-non-str-ws'):
            assert await call(ws, 'bac:session-locate', {'sessionId': sid}) == {
                'ok': True,
                'value': {
                    'hostId': ws_mock.bac.host_id,
                    'locationRevision': loc_rev_with_invalid,
                    'disposition': 'unregistered',
                    'consistency': 'sequential',
                    'atomic': False,
                    'locations': [],
                },
            }

        ws_mock.ws_doc['terminals'].append({'id': 'sess-claude-0001', 'workspaceId': 'ws-2'})
        dup_rev = digest(rendered_document(ws_mock.ws_doc))
        dup_loc_rev = digest([['default', 'persisted', None, dup_rev]])
        assert await call(ws, 'bac:session-locate', {'sessionId': 'sess-claude-0001'}) == {
            'ok': True,
            'value': {
                'hostId': ws_mock.bac.host_id,
                'locationRevision': dup_loc_rev,
                'disposition': 'ambiguous',
                'consistency': 'sequential',
                'atomic': False,
                'locations': [
                    {**expected_row, 'profileRevision': dup_rev},
                    {**expected_row, 'workspaceId': 'ws-2', 'profileRevision': dup_rev},
                ],
            },
        }

        ws_mock.ws_doc['terminals'].pop()
        extra_row = {
            'profileId': 'secondary',
            'workspaceId': 'ws-9',
            'terminalId': 'sess-claude-0001',
            'windowId': 'win-1',
            'source': 'live',
            'profileRevision': 'sha256:' + 'f' * 64,
        }
        ws_mock.bac.extra_locations = [copy.deepcopy(extra_row)]
        extra_before = copy.deepcopy(ws_mock.bac.extra_locations)
        reply = await call(ws, 'bac:session-locate', {'sessionId': 'sess-claude-0001'})
        assert reply == {
            'ok': True,
            'value': {
                'hostId': ws_mock.bac.host_id,
                'locationRevision': loc_rev_with_invalid,
                'disposition': 'ambiguous',
                'consistency': 'sequential',
                'atomic': False,
                'locations': [
                    {**expected_row, 'profileRevision': rev_with_invalid},
                    extra_row,
                ],
            },
        }
        assert ws_mock.bac.extra_locations == extra_before

        ws_mock.bac.extra_locations = []
        ws_mock.ws_doc = {'workspaces': 'not-a-list', 'terminals': [{'id': 'sess-claude-0001', 'workspaceId': 'ws-1'}]}
        bad_rev = digest(rendered_document(ws_mock.ws_doc))
        bad_loc_rev = digest([['default', 'persisted', None, bad_rev]])
        assert await call(ws, 'bac:session-locate', {'sessionId': 'sess-claude-0001'}) == {
            'ok': True,
            'value': {
                'hostId': ws_mock.bac.host_id,
                'locationRevision': bad_loc_rev,
                'disposition': 'unregistered',
                'consistency': 'sequential',
                'atomic': False,
                'locations': [],
            },
        }


async def test_feature_controls_and_hello_immunity(ws_mock):
    sent_ids = []

    async def send_invoke(ws, mid, channel, params):
        sent_ids.append(mid)
        await ws.send(json.dumps({'type': 'invoke', 'id': mid, 'channel': channel, 'params': params}))

    async with wire(ws_mock) as ws:
        sent_ids.append('hello-1')
        assert (await call(ws, 'bac:hello', HELLO, mid='hello-1'))['ok'] is True

        ws_mock.bac.read_error = 'WORKSPACE_TOO_LARGE'
        sent_ids.append('read-invalid')
        assert await call(ws, 'bac:workspace-read', {}, mid='read-invalid') == {
            'ok': False,
            'error': {
                'code': 'INVALID_REQUEST',
                'message': 'Invalid BAC request',
                'retryable': False,
                'details': {'field': 'params.profileId'},
            },
        }
        sent_ids.append('read-too-large')
        assert await call(ws, 'bac:workspace-read', {'profileId': 'default'}, mid='read-too-large') == {
            'ok': False,
            'error': {
                'code': 'WORKSPACE_TOO_LARGE',
                'message': 'Canonical workspace exceeds limit',
                'retryable': False,
                'details': {'maxBytes': 520192},
            },
        }
        ws_mock.bac.read_error = 'WORKSPACE_UNAVAILABLE'
        sent_ids.append('read-unavail')
        assert await call(ws, 'bac:workspace-read', {'profileId': 'default'}, mid='read-unavail') == {
            'ok': False,
            'error': {
                'code': 'WORKSPACE_UNAVAILABLE',
                'message': 'Canonical workspace is unavailable',
                'retryable': False,
                'details': {},
            },
        }
        ws_mock.bac.read_error = None

        ws_mock.bac.locate_error = 'LOCATION_UNAVAILABLE'
        sent_ids.append('loc-invalid')
        assert await call(ws, 'bac:session-locate', {}, mid='loc-invalid') == {
            'ok': False,
            'error': {
                'code': 'INVALID_REQUEST',
                'message': 'Invalid BAC request',
                'retryable': False,
                'details': {'field': 'params.sessionId'},
            },
        }
        sent_ids.append('loc-unavail')
        assert await call(ws, 'bac:session-locate', {'sessionId': 'sess-claude-0001'}, mid='loc-unavail') == {
            'ok': False,
            'error': {
                'code': 'LOCATION_UNAVAILABLE',
                'message': 'Session locations are unavailable',
                'retryable': False,
                'details': {'profileIds': []},
            },
        }
        ws_mock.bac.locate_error = 'LOCATION_TOO_LARGE'
        sent_ids.append('loc-too-large')
        assert await call(ws, 'bac:session-locate', {'sessionId': 'sess-claude-0001'}, mid='loc-too-large') == {
            'ok': False,
            'error': {
                'code': 'LOCATION_TOO_LARGE',
                'message': 'Session locations exceed limit',
                'retryable': False,
                'details': {},
            },
        }
        ws_mock.bac.locate_error = None

        ws_mock.bac.result_override = {
            'bac:workspace-read': {'ok': True},
            'bac:hello': {'override': 'ignored'},
        }
        sent_ids.append('hello-override')
        hello_reply = await call(ws, 'bac:hello', HELLO, mid='hello-override')
        assert hello_reply['ok'] is True and 'value' in hello_reply
        sent_ids.append('read-override')
        assert await call(ws, 'bac:workspace-read', {'profileId': 'default'}, mid='read-override') == {
            'ok': True
        }
        sent_ids.append('loc-normal')
        loc_reply = await call(ws, 'bac:session-locate', {'sessionId': 'sess-claude-0001'}, mid='loc-normal')
        assert loc_reply['ok'] is True and loc_reply['value']['disposition'] == 'located'
        ws_mock.bac.result_override = {}

        ws_mock.bac.busy_replies = 2
        sent_ids.append('hello-busy')
        assert (await call(ws, 'bac:hello', HELLO, mid='hello-busy'))['ok'] is True
        assert ws_mock.bac.busy_replies == 2
        for busy_id in ('busy-1', 'busy-2'):
            await send_invoke(ws, busy_id, 'bac:workspace-read', {'profileId': 'default'})
            assert json.loads(await ws.recv()) == {
                'type': 'invoke-error',
                'id': busy_id,
                'error': 'Remote server is busy',
            }
        assert ws_mock.bac.busy_replies == 0
        sent_ids.append('after-busy')
        after_busy = await call(ws, 'bac:workspace-read', {'profileId': 'default'}, mid='after-busy')
        assert after_busy['ok'] is True and after_busy['value']['profileId'] == 'default'

        ws_mock.bac.drop_feature_replies = True
        sent_ids.append('hello-drop')
        assert (await call(ws, 'bac:hello', HELLO, mid='hello-drop'))['ok'] is True
        await send_invoke(ws, 'a', 'bac:workspace-read', {'profileId': 'default'})
        while not any(f.get('id') == 'a' for f in ws_mock.bac.frames):
            await asyncio.sleep(0.005)
        ws_mock.bac.drop_feature_replies = False
        await send_invoke(ws, 'b', 'bac:workspace-read', {'profileId': 'default'})
        first_received = json.loads(await ws.recv())
        assert first_received['id'] == 'b'
        assert first_received['type'] == 'invoke-result'
        assert first_received['result']['ok'] is True

    assert [f['id'] for f in ws_mock.bac.frames] == sent_ids


@pytest.fixture
def append_mock(bac_mock):
    bac_mock.bac.enable_append(bac_mock)
    return bac_mock


VALID = {
    'id': 'sess-append-0001',
    'workspaceId': 'ws-1',
    'title': 'Appended Claude Agent',
    'type': 'terminal',
    'cwd': '/srv/demo',
    'agentPreset': 'claude-code',
}
_DUMMY_REV = 'sha256:' + 'a' * 64


def _terminal_of_bytes(total_bytes):
    base = {**VALID, 'title': ''}
    overhead = len(json.dumps(base, ensure_ascii=False, separators=(',', ':')).encode('utf-8'))
    term = {**VALID, 'title': 'x' * (total_bytes - overhead)}
    assert len(json.dumps(term, ensure_ascii=False, separators=(',', ':')).encode('utf-8')) == total_bytes
    return term


def _append_frames_params(mock):
    return [
        f['params']
        for f in mock.bac.frames
        if f.get('channel') == 'bac:workspace-add-terminal'
    ]


async def test_enable_append_and_missing_workspace_host(bac_mock):
    bac_mock.bac.features = dict.fromkeys(bac_mock.bac.features, True)
    async with wire(bac_mock) as ws:
        assert (await call(ws, 'bac:hello', HELLO))['ok'] is True
        assert await call(
            ws,
            'bac:workspace-add-terminal',
            {'profileId': 'default', 'expectedRevision': _DUMMY_REV, 'terminal': VALID},
        ) == {
            'ok': False,
            'error': {
                'code': 'INTERNAL_ERROR',
                'message': 'BAC request failed',
                'retryable': False,
                'details': {},
            },
        }

    bac_mock.bac.features = dict.fromkeys(bac_mock.bac.CHANNELS.values(), False)
    bac_mock.bac.enable_append(bac_mock)
    async with wire(bac_mock) as ws:
        assert await call(ws, 'bac:hello', HELLO) == {
            'ok': True,
            'value': {
                'protocol': 'bac/1',
                'minor': 0,
                'hostId': bac_mock.bac.host_id,
                'build': bac_mock.bac.build,
                'features': {
                    'workspaceRead': True,
                    'sessionLocate': True,
                    'workspaceAddTerminal': True,
                    'gitObserve': False,
                },
                'limits': bac_mock.bac.limits,
            },
        }


@pytest.mark.parametrize(
    ('params', 'field'),
    [
        (
            {
                'profileId': 'default',
                'expectedRevision': _DUMMY_REV,
                'terminal': VALID,
                'extra': 1,
            },
            'params',
        ),
        ({}, 'params.profileId'),
        (
            {'profileId': 'a:b', 'expectedRevision': _DUMMY_REV, 'terminal': VALID},
            'params.profileId',
        ),
        (
            {'profileId': 7, 'expectedRevision': _DUMMY_REV, 'terminal': VALID},
            'params.profileId',
        ),
        ({'profileId': 'default', 'terminal': VALID}, 'params.expectedRevision'),
        (
            {
                'profileId': 'default',
                'expectedRevision': 'sha256:' + 'A' * 64,
                'terminal': VALID,
            },
            'params.expectedRevision',
        ),
        (
            {
                'profileId': 'default',
                'expectedRevision': 'sha256:' + 'a' * 63,
                'terminal': VALID,
            },
            'params.expectedRevision',
        ),
        (
            {'profileId': 'default', 'expectedRevision': 5, 'terminal': VALID},
            'params.expectedRevision',
        ),
        ({'profileId': 'default', 'expectedRevision': _DUMMY_REV}, 'params.terminal'),
        (
            {'profileId': 'default', 'expectedRevision': _DUMMY_REV, 'terminal': []},
            'params.terminal',
        ),
        (
            {'profileId': 'default', 'expectedRevision': _DUMMY_REV, 'terminal': 'x'},
            'params.terminal',
        ),
        (
            {'profileId': 'default', 'expectedRevision': _DUMMY_REV, 'terminal': None},
            'params.terminal',
        ),
        (
            {
                'profileId': 'default',
                'expectedRevision': _DUMMY_REV,
                'terminal': {**VALID, 'sdkSessionId': 'sdk-1'},
            },
            'params.terminal',
        ),
        (
            {
                'profileId': 'default',
                'expectedRevision': _DUMMY_REV,
                'terminal': {k: v for k, v in VALID.items() if k != 'id'},
            },
            'params.terminal.id',
        ),
        (
            {
                'profileId': 'default',
                'expectedRevision': _DUMMY_REV,
                'terminal': {**VALID, 'id': 'a b'},
            },
            'params.terminal.id',
        ),
        (
            {
                'profileId': 'default',
                'expectedRevision': _DUMMY_REV,
                'terminal': {**VALID, 'id': 'a' * 129},
            },
            'params.terminal.id',
        ),
        (
            {
                'profileId': 'default',
                'expectedRevision': _DUMMY_REV,
                'terminal': {**VALID, 'id': 5},
            },
            'params.terminal.id',
        ),
        (
            {
                'profileId': 'default',
                'expectedRevision': _DUMMY_REV,
                'terminal': {k: v for k, v in VALID.items() if k != 'workspaceId'},
            },
            'params.terminal.workspaceId',
        ),
        (
            {
                'profileId': 'default',
                'expectedRevision': _DUMMY_REV,
                'terminal': {**VALID, 'workspaceId': 'a/b'},
            },
            'params.terminal.workspaceId',
        ),
        (
            {
                'profileId': 'default',
                'expectedRevision': _DUMMY_REV,
                'terminal': {k: v for k, v in VALID.items() if k != 'title'},
            },
            'params.terminal.title',
        ),
        (
            {
                'profileId': 'default',
                'expectedRevision': _DUMMY_REV,
                'terminal': {**VALID, 'title': ''},
            },
            'params.terminal.title',
        ),
        (
            {
                'profileId': 'default',
                'expectedRevision': _DUMMY_REV,
                'terminal': {**VALID, 'title': f'bad{chr(0)}title'},
            },
            'params.terminal.title',
        ),
        (
            {
                'profileId': 'default',
                'expectedRevision': _DUMMY_REV,
                'terminal': {k: v for k, v in VALID.items() if k != 'type'},
            },
            'params.terminal.type',
        ),
        (
            {
                'profileId': 'default',
                'expectedRevision': _DUMMY_REV,
                'terminal': {**VALID, 'type': 'agent'},
            },
            'params.terminal.type',
        ),
        (
            {
                'profileId': 'default',
                'expectedRevision': _DUMMY_REV,
                'terminal': {**VALID, 'cwd': ''},
            },
            'params.terminal.cwd',
        ),
        (
            {
                'profileId': 'default',
                'expectedRevision': _DUMMY_REV,
                'terminal': {**VALID, 'agentPreset': 'shell'},
            },
            'params.terminal.agentPreset',
        ),
        (
            {
                'profileId': 'default',
                'expectedRevision': _DUMMY_REV,
                'terminal': {k: v for k, v in VALID.items() if k != 'agentPreset'},
            },
            'params.terminal.agentPreset',
        ),
        (
            {
                'profileId': 'default',
                'expectedRevision': _DUMMY_REV,
                'terminal': {**VALID, 'model': ''},
            },
            'params.terminal.model',
        ),
        (
            {
                'profileId': 'default',
                'expectedRevision': _DUMMY_REV,
                'terminal': {**VALID, 'model': 5},
            },
            'params.terminal.model',
        ),
        (
            {
                'profileId': 'default',
                'expectedRevision': _DUMMY_REV,
                'terminal': {**VALID, 'permissionMode': ''},
            },
            'params.terminal.permissionMode',
        ),
        (
            {
                'profileId': 'default',
                'expectedRevision': _DUMMY_REV,
                'terminal': {**VALID, 'agentParams': []},
            },
            'params.terminal.agentParams',
        ),
        (
            {
                'profileId': 'default',
                'expectedRevision': _DUMMY_REV,
                'terminal': {
                    **VALID,
                    'agentParams': {f'k{i}': 'v' for i in range(33)},
                },
            },
            'params.terminal.agentParams',
        ),
        (
            {
                'profileId': 'default',
                'expectedRevision': _DUMMY_REV,
                'terminal': {**VALID, 'agentParams': {'k': 5}},
            },
            'params.terminal.agentParams',
        ),
        (
            {
                'profileId': 'default',
                'expectedRevision': _DUMMY_REV,
                'terminal': {**VALID, 'agentParams': {'': 'v'}},
            },
            'params.terminal.agentParams',
        ),
        (
            {
                'profileId': 'default',
                'expectedRevision': _DUMMY_REV,
                'terminal': {**VALID, 'agentParams': {f'k{chr(0)}': 'v'}},
            },
            'params.terminal.agentParams',
        ),
        (
            {
                'profileId': 'default',
                'expectedRevision': _DUMMY_REV,
                'terminal': {**VALID, 'agentParams': {'k': f'v{chr(0)}'}},
            },
            'params.terminal.agentParams',
        ),
        (
            {
                'profileId': 'default',
                'expectedRevision': _DUMMY_REV,
                'terminal': {**VALID, 'worktreePath': ''},
            },
            'params.terminal.worktreePath',
        ),
        (
            {
                'profileId': 'default',
                'expectedRevision': _DUMMY_REV,
                'terminal': {**VALID, 'worktreeBranch': ''},
            },
            'params.terminal.worktreeBranch',
        ),
        (
            {
                'profileId': 'default',
                'expectedRevision': _DUMMY_REV,
                'terminal': {**VALID, 'worktreeBranch': 5},
            },
            'params.terminal.worktreeBranch',
        ),
        (
            {
                'profileId': 'default',
                'expectedRevision': _DUMMY_REV,
                'terminal': _terminal_of_bytes(16385),
            },
            'params.terminal',
        ),
    ],
    ids=[
        'extra-key',
        'empty-params',
        'profile-colon',
        'profile-int',
        'revision-missing',
        'revision-uppercase',
        'revision-short',
        'revision-int',
        'terminal-missing',
        'terminal-list',
        'terminal-str',
        'terminal-none',
        'terminal-sdkSessionId',
        'id-missing',
        'id-space',
        'id-129',
        'id-int',
        'workspaceId-missing',
        'workspaceId-slash',
        'title-missing',
        'title-empty',
        'title-nul',
        'type-missing',
        'type-agent',
        'cwd-empty',
        'agentPreset-shell',
        'agentPreset-missing',
        'model-empty',
        'model-int',
        'permissionMode-empty',
        'agentParams-list',
        'agentParams-33',
        'agentParams-val-int',
        'agentParams-key-empty',
        'agentParams-key-nul',
        'agentParams-val-nul',
        'worktreePath-empty',
        'worktreeBranch-empty',
        'worktreeBranch-int',
        'terminal-16385-bytes',
    ],
)
async def test_append_request_validation(append_mock, params, field):
    before = copy.deepcopy(append_mock.ws_doc)
    expected_params = copy.deepcopy(params)
    async with wire(append_mock) as ws:
        assert (await call(ws, 'bac:hello', HELLO))['ok'] is True
        assert await call(ws, 'bac:workspace-add-terminal', params) == {
            'ok': False,
            'error': {
                'code': 'INVALID_REQUEST',
                'message': 'Invalid BAC request',
                'retryable': False,
                'details': {'field': field},
            },
        }
    assert append_mock.ws_doc == before
    assert _append_frames_params(append_mock) == [expected_params]


async def test_append_validation_order(append_mock):
    before = copy.deepcopy(append_mock.ws_doc)
    sent_params = []

    def invalid(field):
        return {
            'ok': False,
            'error': {
                'code': 'INVALID_REQUEST',
                'message': 'Invalid BAC request',
                'retryable': False,
                'details': {'field': field},
            },
        }

    async with wire(append_mock) as ws:
        assert (await call(ws, 'bac:hello', HELLO))['ok'] is True
        for params, field in (
            (
                {'profileId': 'a:b', 'expectedRevision': 'bad', 'terminal': VALID},
                'params.profileId',
            ),
            (
                {'profileId': 'default', 'expectedRevision': 'bad', 'terminal': None},
                'params.expectedRevision',
            ),
            (
                {
                    'profileId': 'default',
                    'expectedRevision': _DUMMY_REV,
                    'terminal': {**VALID, 'id': 'a b', 'title': ''},
                },
                'params.terminal.id',
            ),
            (
                {
                    'profileId': 'default',
                    'expectedRevision': _DUMMY_REV,
                    'terminal': {**VALID, 'type': 'agent', 'cwd': ''},
                },
                'params.terminal.type',
            ),
            (
                {
                    'profileId': 'default',
                    'expectedRevision': _DUMMY_REV,
                    'terminal': {**_terminal_of_bytes(16385), 'worktreeBranch': ''},
                },
                'params.terminal.worktreeBranch',
            ),
        ):
            sent_params.append(copy.deepcopy(params))
            assert await call(ws, 'bac:workspace-add-terminal', params) == invalid(field)
            assert append_mock.ws_doc == before

        append_mock.bac.append_error = 'WORKSPACE_UNAVAILABLE'
        bad_with_err = {'profileId': 'a:b', 'expectedRevision': _DUMMY_REV, 'terminal': VALID}
        sent_params.append(copy.deepcopy(bad_with_err))
        assert await call(ws, 'bac:workspace-add-terminal', bad_with_err) == invalid(
            'params.profileId'
        )
        assert append_mock.ws_doc == before
    assert _append_frames_params(append_mock) == sent_params


@pytest.mark.parametrize(
    'terminal',
    [
        {
            **VALID,
            'model': None,
            'permissionMode': None,
            'agentParams': None,
            'worktreePath': None,
            'worktreeBranch': None,
        },
        {**VALID, 'agentParams': {}},
        {**VALID, 'agentParams': {'emptyVal': ''}},
        {**VALID, 'agentParams': {f'k{i}': f'v{i}' for i in range(32)}},
        _terminal_of_bytes(16384),
    ],
    ids=[
        'optional-none',
        'agentParams-empty-dict',
        'agentParams-empty-val',
        'agentParams-32-entries',
        'terminal-16384-bytes',
    ],
)
async def test_append_accepted_forms(append_mock, terminal):
    rev = digest(rendered_document(append_mock.ws_doc))
    params = {'profileId': 'default', 'expectedRevision': rev, 'terminal': terminal}
    expected_params = copy.deepcopy(params)
    async with wire(append_mock) as ws:
        assert (await call(ws, 'bac:hello', HELLO))['ok'] is True
        reply = await call(ws, 'bac:workspace-add-terminal', params)
    new_rev = digest(rendered_document(append_mock.ws_doc))
    assert reply == {
        'ok': True,
        'value': {
            'hostId': append_mock.bac.host_id,
            'profileId': 'default',
            'source': 'persisted',
            'windowId': None,
            'terminalId': terminal['id'],
            'appended': True,
            'revision': new_rev,
        },
    }
    assert _append_frames_params(append_mock) == [expected_params]


async def test_append_unknown_profile(append_mock):
    before = copy.deepcopy(append_mock.ws_doc)
    rev = digest(rendered_document(before))
    params = {'profileId': 'other', 'expectedRevision': rev, 'terminal': VALID}
    async with wire(append_mock) as ws:
        assert (await call(ws, 'bac:hello', HELLO))['ok'] is True
        assert await call(ws, 'bac:workspace-add-terminal', params) == {
            'ok': False,
            'error': {
                'code': 'PROFILE_NOT_FOUND',
                'message': 'Local profile not found',
                'retryable': False,
                'details': {},
            },
        }
    assert append_mock.ws_doc == before
    assert _append_frames_params(append_mock) == [params]


async def test_append_normalization_and_follow_up_reads(append_mock):
    before = copy.deepcopy(append_mock.ws_doc)
    rev = digest(rendered_document(before))
    req_terminal = {
        **VALID,
        'model': None,
        'agentParams': None,
        'worktreeBranch': None,
        'permissionMode': 'plan',
    }
    expected_stored = {
        **VALID,
        'permissionMode': 'plan',
        'worktreeBranch': None,
    }
    expected_ws_doc = copy.deepcopy(before)
    expected_ws_doc['terminals'].append(expected_stored)
    expected_doc = rendered_document(expected_ws_doc)
    new_rev = digest(expected_doc)
    params = {'profileId': 'default', 'expectedRevision': rev, 'terminal': req_terminal}
    expected_params = copy.deepcopy(params)

    async with wire(append_mock) as ws:
        assert (await call(ws, 'bac:hello', HELLO))['ok'] is True
        assert await call(ws, 'bac:workspace-add-terminal', params) == {
            'ok': True,
            'value': {
                'hostId': append_mock.bac.host_id,
                'profileId': 'default',
                'source': 'persisted',
                'windowId': None,
                'terminalId': VALID['id'],
                'appended': True,
                'revision': new_rev,
            },
        }
        assert append_mock.ws_doc == expected_ws_doc
        assert list(append_mock.ws_doc['terminals'][-1].keys()) == [
            'id',
            'workspaceId',
            'title',
            'type',
            'cwd',
            'agentPreset',
            'permissionMode',
            'worktreeBranch',
        ]
        assert await call(ws, 'bac:workspace-read', {'profileId': 'default'}) == {
            'ok': True,
            'value': {
                'hostId': append_mock.bac.host_id,
                'profileId': 'default',
                'source': 'persisted',
                'windowId': None,
                'revision': new_rev,
                'document': expected_doc,
                'appendTargetEligible': True,
            },
        }
        assert await call(ws, 'bac:session-locate', {'sessionId': VALID['id']}) == {
            'ok': True,
            'value': {
                'hostId': append_mock.bac.host_id,
                'locationRevision': digest([['default', 'persisted', None, new_rev]]),
                'disposition': 'located',
                'consistency': 'sequential',
                'atomic': False,
                'locations': [
                    {
                        'profileId': 'default',
                        'workspaceId': 'ws-1',
                        'terminalId': VALID['id'],
                        'windowId': None,
                        'source': 'persisted',
                        'profileRevision': new_rev,
                    }
                ],
            },
        }
        legacy_doc = json.loads(await call(ws, 'workspace:load', {}))
        assert legacy_doc == expected_ws_doc
        assert legacy_doc['terminals'][-1] == expected_stored
    assert _append_frames_params(append_mock) == [expected_params]


async def test_append_same_registration_and_conflicts(append_mock):
    sent_params = []
    old_rev = digest(rendered_document(append_mock.ws_doc))
    no_type_term = {k: v for k, v in VALID.items() if k != 'type'}
    no_type_term['id'] = 'sess-no-type'
    append_mock.ws_doc['terminals'].append(no_type_term)
    sandbox_term = {
        **VALID,
        'id': 'sess-sandbox',
        'agentParams': {'sandboxMode': 'read-only', 'effortLevel': 'high'},
    }
    append_mock.ws_doc['terminals'].append(copy.deepcopy(sandbox_term))
    rev_before_append = digest(rendered_document(append_mock.ws_doc))

    async with wire(append_mock) as ws:
        assert (await call(ws, 'bac:hello', HELLO))['ok'] is True
        first_params = {
            'profileId': 'default',
            'expectedRevision': rev_before_append,
            'terminal': VALID,
        }
        sent_params.append(copy.deepcopy(first_params))
        first_reply = await call(ws, 'bac:workspace-add-terminal', first_params)
        cur_rev = digest(rendered_document(append_mock.ws_doc))
        assert first_reply == {
            'ok': True,
            'value': {
                'hostId': append_mock.bac.host_id,
                'profileId': 'default',
                'source': 'persisted',
                'windowId': None,
                'terminalId': VALID['id'],
                'appended': True,
                'revision': cur_rev,
            },
        }
        before = copy.deepcopy(append_mock.ws_doc)

        for candidate, expected_id in (
            (VALID, VALID['id']),
            ({**VALID, 'model': None}, VALID['id']),
            ({**VALID, 'agentParams': {}}, VALID['id']),
            ({**VALID, 'id': 'sess-no-type'}, 'sess-no-type'),
        ):
            p = {'profileId': 'default', 'expectedRevision': old_rev, 'terminal': candidate}
            sent_params.append(copy.deepcopy(p))
            assert await call(ws, 'bac:workspace-add-terminal', p) == {
                'ok': True,
                'value': {
                    'hostId': append_mock.bac.host_id,
                    'profileId': 'default',
                    'source': 'persisted',
                    'windowId': None,
                    'terminalId': expected_id,
                    'appended': False,
                    'revision': cur_rev,
                },
            }
            assert append_mock.ws_doc == before

        for conflict_term in (
            {**VALID, 'title': 'Other Title'},
            {**VALID, 'worktreeBranch': None},
            {
                **VALID,
                'id': 'sess-sandbox',
                'agentParams': {'sandboxMode': 'workspace-write', 'effortLevel': 'high'},
            },
        ):
            p = {
                'profileId': 'default',
                'expectedRevision': cur_rev,
                'terminal': conflict_term,
            }
            sent_params.append(copy.deepcopy(p))
            assert await call(ws, 'bac:workspace-add-terminal', p) == {
                'ok': False,
                'error': {
                    'code': 'TERMINAL_EXISTS',
                    'message': 'Terminal ID has a different registration',
                    'retryable': False,
                    'details': {
                        'terminalId': conflict_term['id'],
                        'currentRevision': cur_rev,
                    },
                },
            }
            assert append_mock.ws_doc == before
    assert _append_frames_params(append_mock) == sent_params


async def test_append_overlay(append_mock):
    def apply_overlay(terminal):
        terminal.setdefault('agentParams', {})['sandboxMode'] = 'workspace-write'

    append_mock.bac.overlay = apply_overlay
    rev0 = digest(rendered_document(append_mock.ws_doc))
    params = {'profileId': 'default', 'expectedRevision': rev0, 'terminal': VALID}
    expected_params = copy.deepcopy(params)

    async with wire(append_mock) as ws:
        assert (await call(ws, 'bac:hello', HELLO))['ok'] is True
        reply1 = await call(ws, 'bac:workspace-add-terminal', params)
        assert append_mock.ws_doc['terminals'][-1] == {
            **VALID,
            'agentParams': {'sandboxMode': 'workspace-write'},
        }
        rev1 = digest(rendered_document(append_mock.ws_doc))
        assert reply1 == {
            'ok': True,
            'value': {
                'hostId': append_mock.bac.host_id,
                'profileId': 'default',
                'source': 'persisted',
                'windowId': None,
                'terminalId': VALID['id'],
                'appended': True,
                'revision': rev1,
            },
        }
        after_first = copy.deepcopy(append_mock.ws_doc)

        assert await call(ws, 'bac:workspace-add-terminal', params) == {
            'ok': True,
            'value': {
                'hostId': append_mock.bac.host_id,
                'profileId': 'default',
                'source': 'persisted',
                'windowId': None,
                'terminalId': VALID['id'],
                'appended': False,
                'revision': rev1,
            },
        }
        assert append_mock.ws_doc == after_first

        append_mock.bac.overlay = None
        assert await call(ws, 'bac:workspace-add-terminal', params) == {
            'ok': False,
            'error': {
                'code': 'TERMINAL_EXISTS',
                'message': 'Terminal ID has a different registration',
                'retryable': False,
                'details': {'terminalId': VALID['id'], 'currentRevision': rev1},
            },
        }
        assert append_mock.ws_doc == after_first
    assert _append_frames_params(append_mock) == [
        expected_params,
        expected_params,
        expected_params,
    ]


async def test_append_revision_and_before_append(append_mock):
    before = copy.deepcopy(append_mock.ws_doc)
    cur_rev = digest(rendered_document(before))
    sent_params = []

    async with wire(append_mock) as ws:
        assert (await call(ws, 'bac:hello', HELLO))['ok'] is True
        stale_params = {
            'profileId': 'default',
            'expectedRevision': 'sha256:' + '0' * 64,
            'terminal': VALID,
        }
        sent_params.append(copy.deepcopy(stale_params))
        assert await call(ws, 'bac:workspace-add-terminal', stale_params) == {
            'ok': False,
            'error': {
                'code': 'REVISION_MISMATCH',
                'message': 'Workspace revision changed',
                'retryable': True,
                'details': {'currentRevision': cur_rev},
            },
        }
        assert append_mock.ws_doc == before

        hook_calls = 0

        def other_editor():
            nonlocal hook_calls
            hook_calls += 1
            if hook_calls == 1:
                append_mock.ws_doc['terminals'].append(
                    {**VALID, 'id': 'sess-other-editor'}
                )

        append_mock.bac.before_append = other_editor
        read1 = await call(ws, 'bac:workspace-read', {'profileId': 'default'})
        assert read1['value']['revision'] == cur_rev

        race_params = {
            'profileId': 'default',
            'expectedRevision': read1['value']['revision'],
            'terminal': VALID,
        }
        sent_params.append(copy.deepcopy(race_params))
        race_reply = await call(ws, 'bac:workspace-add-terminal', race_params)
        after_other_doc = copy.deepcopy(append_mock.ws_doc)
        after_other_rev = digest(rendered_document(after_other_doc))
        assert race_reply == {
            'ok': False,
            'error': {
                'code': 'REVISION_MISMATCH',
                'message': 'Workspace revision changed',
                'retryable': True,
                'details': {'currentRevision': after_other_rev},
            },
        }
        read2 = await call(ws, 'bac:workspace-read', {'profileId': 'default'})
        assert read2['value']['revision'] == after_other_rev

        retry_params = {
            'profileId': 'default',
            'expectedRevision': read2['value']['revision'],
            'terminal': VALID,
        }
        sent_params.append(copy.deepcopy(retry_params))
        retry_reply = await call(ws, 'bac:workspace-add-terminal', retry_params)
        final_rev = digest(rendered_document(append_mock.ws_doc))
        assert retry_reply == {
            'ok': True,
            'value': {
                'hostId': append_mock.bac.host_id,
                'profileId': 'default',
                'source': 'persisted',
                'windowId': None,
                'terminalId': VALID['id'],
                'appended': True,
                'revision': final_rev,
            },
        }
        assert hook_calls == 2
    assert _append_frames_params(append_mock) == sent_params


async def test_append_unknown_workspace_and_precedence(append_mock):
    before = copy.deepcopy(append_mock.ws_doc)
    cur_rev = digest(rendered_document(before))
    stale_rev = 'sha256:' + '0' * 64
    sent_params = []

    async with wire(append_mock) as ws:
        assert (await call(ws, 'bac:hello', HELLO))['ok'] is True
        p1 = {
            'profileId': 'default',
            'expectedRevision': cur_rev,
            'terminal': {**VALID, 'workspaceId': 'ws-missing'},
        }
        sent_params.append(copy.deepcopy(p1))
        assert await call(ws, 'bac:workspace-add-terminal', p1) == {
            'ok': False,
            'error': {
                'code': 'WORKSPACE_NOT_FOUND',
                'message': 'Target workspace not found',
                'retryable': False,
                'details': {'workspaceId': 'ws-missing'},
            },
        }
        assert append_mock.ws_doc == before

        p2 = {
            'profileId': 'default',
            'expectedRevision': stale_rev,
            'terminal': {**VALID, 'id': 'sess-claude-0001', 'title': 'Different'},
        }
        sent_params.append(copy.deepcopy(p2))
        assert await call(ws, 'bac:workspace-add-terminal', p2) == {
            'ok': False,
            'error': {
                'code': 'TERMINAL_EXISTS',
                'message': 'Terminal ID has a different registration',
                'retryable': False,
                'details': {
                    'terminalId': 'sess-claude-0001',
                    'currentRevision': cur_rev,
                },
            },
        }
        assert append_mock.ws_doc == before

        p3 = {
            'profileId': 'default',
            'expectedRevision': stale_rev,
            'terminal': {**VALID, 'workspaceId': 'ws-missing'},
        }
        sent_params.append(copy.deepcopy(p3))
        assert await call(ws, 'bac:workspace-add-terminal', p3) == {
            'ok': False,
            'error': {
                'code': 'REVISION_MISMATCH',
                'message': 'Workspace revision changed',
                'retryable': True,
                'details': {'currentRevision': cur_rev},
            },
        }
        assert append_mock.ws_doc == before
    assert _append_frames_params(append_mock) == sent_params


@pytest.mark.parametrize(
    'bad_ws_doc',
    [
        {'workspaces': [{'id': 'ws-1'}], 'terminals': {}},
        {'workspaces': [{'id': 'ws-1'}], 'terminals': ['not-a-dict']},
        {'workspaces': [{'id': 'ws-1'}], 'terminals': [{'workspaceId': 'ws-1'}]},
        {'workspaces': [{'id': 'ws-1'}], 'terminals': [{'id': ''}]},
        {'workspaces': [{'id': 'ws-1'}], 'terminals': [VALID, VALID]},
        {'workspaces': {}, 'terminals': [VALID]},
        {'workspaces': ['not-a-dict'], 'terminals': [VALID]},
        {'workspaces': [{'name': 'ws'}], 'terminals': [VALID]},
        {'workspaces': [{'id': ''}], 'terminals': [VALID]},
        {'workspaces': [{'id': 'ws-1'}, {'id': 'ws-1'}], 'terminals': [VALID]},
    ],
    ids=[
        'terminals-dict',
        'terminal-entry-str',
        'terminal-without-id',
        'terminal-empty-id',
        'terminals-duplicate-id',
        'workspaces-dict',
        'workspace-entry-str',
        'workspace-without-id',
        'workspace-empty-id',
        'workspaces-duplicate-id',
    ],
)
async def test_append_invalid_structure(append_mock, bad_ws_doc):
    append_mock.ws_doc = copy.deepcopy(bad_ws_doc)
    before = copy.deepcopy(append_mock.ws_doc)
    cur_rev = digest(rendered_document(before))
    stale_rev = 'sha256:' + '0' * 64
    sent_params = []
    expected_err = {
        'ok': False,
        'error': {
            'code': 'WORKSPACE_UNAVAILABLE',
            'message': 'Canonical workspace is unavailable',
            'retryable': False,
            'details': {},
        },
    }

    async with wire(append_mock) as ws:
        assert (await call(ws, 'bac:hello', HELLO))['ok'] is True
        for rev, term in (
            (cur_rev, {**VALID, 'id': 'sess-new-id'}),
            (cur_rev, VALID),
            (stale_rev, {**VALID, 'id': 'sess-new-id'}),
            (stale_rev, VALID),
        ):
            p = {'profileId': 'default', 'expectedRevision': rev, 'terminal': term}
            sent_params.append(copy.deepcopy(p))
            assert await call(ws, 'bac:workspace-add-terminal', p) == expected_err
            assert append_mock.ws_doc == before
    assert _append_frames_params(append_mock) == sent_params


async def test_append_error_controls(append_mock):
    before = copy.deepcopy(append_mock.ws_doc)
    cur_rev = digest(rendered_document(before))
    params = {'profileId': 'default', 'expectedRevision': cur_rev, 'terminal': VALID}
    sent_params = []

    async with wire(append_mock) as ws:
        assert (await call(ws, 'bac:hello', HELLO))['ok'] is True
        for code, msg, details in (
            ('PROFILE_NOT_LOCAL', 'BAC requires a local profile', {}),
            (
                'WORKSPACE_TOO_LARGE',
                'Canonical workspace exceeds limit',
                {'maxBytes': 520192},
            ),
            ('WORKSPACE_UNAVAILABLE', 'Canonical workspace is unavailable', {}),
        ):
            append_mock.bac.append_error = code
            sent_params.append(copy.deepcopy(params))
            assert await call(ws, 'bac:workspace-add-terminal', params) == {
                'ok': False,
                'error': {
                    'code': code,
                    'message': msg,
                    'retryable': False,
                    'details': details,
                },
            }
            assert append_mock.ws_doc == before
    assert _append_frames_params(append_mock) == sent_params


async def test_append_faults(append_mock):
    sent_params = []
    before0 = copy.deepcopy(append_mock.ws_doc)
    rev0 = digest(rendered_document(before0))

    append_mock.bac.append_fault = 'lost_reply'
    async with wire(append_mock) as ws:
        assert (await call(ws, 'bac:hello', HELLO))['ok'] is True
        stale_params = {
            'profileId': 'default',
            'expectedRevision': 'sha256:' + '0' * 64,
            'terminal': VALID,
        }
        sent_params.append(copy.deepcopy(stale_params))
        assert await call(ws, 'bac:workspace-add-terminal', stale_params) == {
            'ok': False,
            'error': {
                'code': 'REVISION_MISMATCH',
                'message': 'Workspace revision changed',
                'retryable': True,
                'details': {'currentRevision': rev0},
            },
        }
        assert append_mock.ws_doc == before0
        assert append_mock.bac.append_fault == 'lost_reply'

        append_mock.bac.append_fault = 'lost_request'
        p_lost_req = {
            'profileId': 'default',
            'expectedRevision': rev0,
            'terminal': VALID,
        }
        sent_params.append(copy.deepcopy(p_lost_req))
        await ws.send(
            json.dumps({
                'type': 'invoke',
                'id': 'fault-lost-req',
                'channel': 'bac:workspace-add-terminal',
                'params': p_lost_req,
            })
        )
        with pytest.raises(websockets.exceptions.ConnectionClosed):
            await ws.recv()
    assert append_mock.bac.append_fault is None
    assert append_mock.ws_doc == before0

    append_mock.bac.append_fault = 'lost_reply'
    async with wire(append_mock) as ws:
        assert (await call(ws, 'bac:hello', HELLO))['ok'] is True
        p_lost_rep = {
            'profileId': 'default',
            'expectedRevision': rev0,
            'terminal': VALID,
        }
        sent_params.append(copy.deepcopy(p_lost_rep))
        await ws.send(
            json.dumps({
                'type': 'invoke',
                'id': 'fault-lost-rep',
                'channel': 'bac:workspace-add-terminal',
                'params': p_lost_rep,
            })
        )
        with pytest.raises(websockets.exceptions.ConnectionClosed):
            await ws.recv()
    assert append_mock.bac.append_fault is None
    expected_after_lost_rep = copy.deepcopy(before0)
    expected_after_lost_rep['terminals'].append(copy.deepcopy(VALID))
    assert append_mock.ws_doc == expected_after_lost_rep

    before1 = copy.deepcopy(append_mock.ws_doc)
    rev1 = digest(rendered_document(before1))
    term2 = {**VALID, 'id': 'sess-append-0002'}
    write_failed_reply = {
        'ok': False,
        'error': {
            'code': 'WRITE_FAILED',
            'message': 'Canonical workspace save failed; read back before retry',
            'retryable': False,
            'details': {'outcome': 'unknown'},
        },
    }

    async with wire(append_mock) as ws:
        assert (await call(ws, 'bac:hello', HELLO))['ok'] is True
        append_mock.bac.append_fault = 'write_failed'
        p_wf = {'profileId': 'default', 'expectedRevision': rev1, 'terminal': term2}
        sent_params.append(copy.deepcopy(p_wf))
        assert await call(ws, 'bac:workspace-add-terminal', p_wf) == write_failed_reply
        assert append_mock.bac.append_fault is None
        assert append_mock.ws_doc == before1

        append_mock.bac.append_fault = 'write_failed_applied'
        sent_params.append(copy.deepcopy(p_wf))
        assert await call(ws, 'bac:workspace-add-terminal', p_wf) == write_failed_reply
        assert append_mock.bac.append_fault is None
        expected_after_wfa = copy.deepcopy(before1)
        expected_after_wfa['terminals'].append(copy.deepcopy(term2))
        assert append_mock.ws_doc == expected_after_wfa

        before2 = copy.deepcopy(append_mock.ws_doc)
        rev2 = digest(rendered_document(before2))
        term3 = {**VALID, 'id': 'sess-append-0003'}
        append_mock.bac.append_fault = 'internal_error'
        p_ie = {'profileId': 'default', 'expectedRevision': rev2, 'terminal': term3}
        sent_params.append(copy.deepcopy(p_ie))
        assert await call(ws, 'bac:workspace-add-terminal', p_ie) == {
            'ok': False,
            'error': {
                'code': 'INTERNAL_ERROR',
                'message': 'BAC request failed',
                'retryable': False,
                'details': {},
            },
        }
        assert append_mock.bac.append_fault is None
        assert append_mock.ws_doc == before2

    assert _append_frames_params(append_mock) == sent_params


async def test_append_reply_controls_and_recording(append_mock):
    sent_params = []
    before0 = copy.deepcopy(append_mock.ws_doc)
    rev0 = digest(rendered_document(before0))
    p1 = {'profileId': 'default', 'expectedRevision': rev0, 'terminal': VALID}

    async with wire(append_mock) as ws:
        assert (await call(ws, 'bac:hello', HELLO))['ok'] is True

        append_mock.bac.busy_replies = 1
        sent_params.append(copy.deepcopy(p1))
        await ws.send(
            json.dumps({
                'type': 'invoke',
                'id': 'busy-append',
                'channel': 'bac:workspace-add-terminal',
                'params': p1,
            })
        )
        assert json.loads(await ws.recv()) == {
            'type': 'invoke-error',
            'id': 'busy-append',
            'error': 'Remote server is busy',
        }
        assert append_mock.bac.busy_replies == 0
        assert append_mock.ws_doc == before0

        sent_params.append(copy.deepcopy(p1))
        reply1 = await call(ws, 'bac:workspace-add-terminal', p1, mid='after-busy')
        expected_after_1 = copy.deepcopy(before0)
        expected_after_1['terminals'].append(copy.deepcopy(VALID))
        rev1 = digest(rendered_document(expected_after_1))
        assert reply1 == {
            'ok': True,
            'value': {
                'hostId': append_mock.bac.host_id,
                'profileId': 'default',
                'source': 'persisted',
                'windowId': None,
                'terminalId': VALID['id'],
                'appended': True,
                'revision': rev1,
            },
        }
        assert append_mock.ws_doc == expected_after_1

        term2 = {**VALID, 'id': 'sess-append-drop'}
        p2 = {'profileId': 'default', 'expectedRevision': rev1, 'terminal': term2}
        append_mock.bac.drop_feature_replies = True
        sent_params.append(copy.deepcopy(p2))
        await ws.send(
            json.dumps({
                'type': 'invoke',
                'id': 'drop-append',
                'channel': 'bac:workspace-add-terminal',
                'params': p2,
            })
        )
        while not any(f.get('id') == 'drop-append' for f in append_mock.bac.frames):
            await asyncio.sleep(0.005)
        expected_after_2 = copy.deepcopy(expected_after_1)
        expected_after_2['terminals'].append(copy.deepcopy(term2))
        assert append_mock.ws_doc == expected_after_2
        rev2 = digest(rendered_document(expected_after_2))

        append_mock.bac.drop_feature_replies = False
        await ws.send(
            json.dumps({
                'type': 'invoke',
                'id': 'read-after-drop',
                'channel': 'bac:workspace-read',
                'params': {'profileId': 'default'},
            })
        )
        assert json.loads(await ws.recv()) == {
            'type': 'invoke-result',
            'id': 'read-after-drop',
            'result': {
                'ok': True,
                'value': {
                    'hostId': append_mock.bac.host_id,
                    'profileId': 'default',
                    'source': 'persisted',
                    'windowId': None,
                    'revision': rev2,
                    'document': rendered_document(expected_after_2),
                    'appendTargetEligible': True,
                },
            },
        }

        term3 = {**VALID, 'id': 'sess-append-override'}
        p3 = {'profileId': 'default', 'expectedRevision': rev2, 'terminal': term3}
        append_mock.bac.result_override = {
            'bac:workspace-add-terminal': {'ok': True, 'value': 'bogus'}
        }
        sent_params.append(copy.deepcopy(p3))
        assert await call(ws, 'bac:workspace-add-terminal', p3, mid='override-append') == {
            'ok': True,
            'value': 'bogus',
        }
        expected_after_3 = copy.deepcopy(expected_after_2)
        expected_after_3['terminals'].append(copy.deepcopy(term3))
        assert append_mock.ws_doc == expected_after_3

    assert _append_frames_params(append_mock) == sent_params
