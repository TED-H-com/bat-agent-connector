"""Portable handshake examples exercised over the mock's actual TLS wire."""
from __future__ import annotations

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
