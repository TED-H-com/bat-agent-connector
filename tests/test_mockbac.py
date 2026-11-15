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
                'code': 'INTERNAL_ERROR',
                'message': 'BAC request failed',
                'retryable': False,
                'details': {},
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
