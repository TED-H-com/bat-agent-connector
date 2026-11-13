"""Portable handshake examples exercised over the mock's actual TLS wire."""
from __future__ import annotations

import json
import ssl
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from websockets.asyncio.client import connect

from tests.mockbat import TOKEN, MockBat

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
async def test_portable_hello_case(bac_mock, case):
    situation = case['situation']
    async with wire(bac_mock) as ws:
        if situation['connection'] == 'negotiated':
            assert (await call(ws, 'bac:hello', HELLO))['ok'] is True
        storage = situation.get('identityStorage')
        if storage:
            bac_mock.bac.storage_error = 'STORAGE_' + storage.upper()
        reply = await call(ws, 'bac:hello', resolve(case['params'], bac_mock.bac),
                           case['name'], **situation.get('frame', {}))
        assert reply == resolve(case['result'], bac_mock.bac)
        probe = await call(ws, 'bac:future', {})
        negotiated = situation['connection'] == 'negotiated' or reply['ok']
        assert probe['error']['code'] == ('UNKNOWN_CHANNEL' if negotiated else 'NOT_OPTED_IN')


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
        for mid in ('', 'x' * 129, '\0'):
            reply = await call(ws, 'bac:hello', HELLO, mid)
            assert reply['error']['code'] == 'INVALID_REQUEST'
        assert (await call(ws, 'bac:hello', HELLO, 'x' * 128))['ok']
        frame = {'type': 'invoke', 'id': 'case', 'channel': 'bac:hello',
                 'params': {'protocol': '', 'minor': 0}}
        overhead = len(json.dumps(frame, separators=(',', ':')).encode())
        for size, expected in ((65536, 'UNSUPPORTED_VERSION'), (65537, 'PAYLOAD_TOO_LARGE')):
            params = {'protocol': 'x' * (size - overhead), 'minor': 0}
            assert (await call(ws, 'bac:hello', params))['error']['code'] == expected
