"""BAC negotiation never changes or authorizes existing guarded work."""
from __future__ import annotations

import asyncio
import copy
import json
from dataclasses import FrozenInstanceError

import pytest

from bat_agent_connector import bac, channels, registry
from bat_agent_connector.client import BatClient
from bat_agent_connector.errors import BacReplyError, ChannelNotAllowed, ConnectionLost
from bat_agent_connector.redact import register_secret
from bat_agent_connector.resource_policy import BY_ACTION, WriteGrant
from tests.conftest import adopt, make_config
from tests.mockbat import TOKEN, MockBac, MockBat


@pytest.fixture
async def host():
    mock = MockBat(bac=True)
    await mock.start()
    try:
        yield mock
    finally:
        await mock.stop()


@pytest.fixture
def grant(controller_context):
    adopt('sess-claude-0001')
    entry = registry.get('h1', 'sess-claude-0001')
    return WriteGrant('h1', 'session.send', 'sess-claude-0001', BY_ACTION['session.send'].channels,
                      controller_authority=entry['controller_authority'],
                      controller_incarnation=entry['controller_incarnation'])


def client_for(host, **kwargs):
    cfg = make_config(host, writes=True, orchestrate=True, tabs=True)
    return BatClient(cfg.host('h1'), device_id='bac-test', **kwargs)


async def ordinary_paths(client, grant):
    assert await client.invoke('app:get-version') == '9.9.9'
    gates = []

    async def before_frame():
        meta = await client.guard_read('claude:get-session-meta', {'sessionId': 'sess-claude-0001'})
        assert meta['cwd'] == '/srv/demo'
        gates.append('read')

    reply = await client.invoke('claude:send-message',
                                {'sessionId': 'sess-claude-0001', 'prompt': 'synthetic fixture'},
                                grant=grant, before_frame=before_frame,
                                on_transport=lambda: gates.append('transport'))
    assert reply['accepted'] is True
    assert gates == ['read', 'transport']


@pytest.mark.parametrize('capabilities,status', [
    (None, 'absent'), ([], 'absent'), ('bac', 'absent'), ({}, 'absent'),
    ({'bac': {'protocol': 'bac/1', 'minor': 0, 'future': True}}, 'supported'),
])
def test_advertisement_container(capabilities, status):
    assert bac.advertisement_status(capabilities) == status


BAD_ADVERTISEMENTS = [None, 'bac/1', [], True, 0, {}, {'protocol': 'bac/1'}, {'minor': 0},
                      {'protocol': 'bac/2', 'minor': 0}, {'protocol': 'bac/1', 'minor': 1},
                      {'protocol': 'bac/1', 'minor': True}, {'protocol': 'bac/1', 'minor': '0'},
                      {'protocol': 'bac/1', 'minor': 0.0}, {'protocol': [], 'minor': 0}]


@pytest.mark.parametrize('advertisement', BAD_ADVERTISEMENTS)
async def test_unsupported_advertisement_sends_nothing(host, grant, advertisement):
    host.bac.advertisement = advertisement
    async with client_for(host) as client:
        assert client.bac.status == 'unsupported'
        assert client.bac.reason == 'unsupported_advertisement'
        await ordinary_paths(client, grant)
        await client.connect()
        assert host.bac.frames == []


@pytest.mark.parametrize('enabled', [False, True])
async def test_absent_advertisement_never_probes(mock, grant, enabled):
    if enabled:
        mock.bac = MockBac()
        mock.bac.advertisement = MockBac.MISSING
    async with client_for(mock) as client:
        assert client.bac.status == 'absent' and client.bac.reason == 'not_advertised'
        await ordinary_paths(client, grant)
        assert not any(frame.get('channel', '').startswith('bac:') for frame in mock.frames)


async def test_one_hello_before_work_concurrent_connect_and_single_permit(host, grant):
    client = client_for(host, max_concurrency=1)
    try:
        await asyncio.wait_for(asyncio.gather(client.connect(), client.connect()), 3)
        assert len(host.auth_frames) == len(host.bac.frames) == 1
        assert host.frames[1] == bac.hello_request()
        assert client.bac.status == 'negotiated'
        assert client.bac.host_id == host.bac.host_id
        assert client.bac.build.product == host.bac.build['product']
        assert client.bac.build.revision == host.bac.build['revision']
        assert client.bac.build.release == host.bac.build['release']
        assert client.bac.build.dirty == host.bac.build['dirty']
        assert client.bac.features == host.bac.features
        assert client.bac.limits == host.bac.limits
        await asyncio.wait_for(ordinary_paths(client, grant), 3)
        assert host.invokes[1]['id'] == 'batc-1'
        learned = client.bac.host_id
        await client.close()
        assert client.bac is None and not client.connected
        await client.connect()
        assert host.bac.frames[-1] == bac.hello_request(learned)
        assert len(host.auth_frames) == len(host.bac.frames) == 2
        auth_positions = [i for i, frame in enumerate(host.frames) if frame['type'] == 'auth']
        assert host.frames[auth_positions[-1] + 1]['channel'] == 'bac:hello'
    finally:
        await client.close()


async def test_cancelled_hello_closes_connection_and_can_negotiate_again(host):
    host.bac.drop_hello = True
    client = client_for(host)
    pending = asyncio.create_task(client.connect())
    try:
        await asyncio.wait_for(host.bac.hello_received.wait(), 3)
        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pending
        assert not client.connected and client.bac is None
        assert client._ws is None and client._reader is None and not client._pending
        host.bac.drop_hello = False
        await client.connect()
        assert client.bac.status == 'negotiated'
        assert len(host.bac.frames) == 2
        assert 'expectedHostId' not in host.bac.frames[-1]['params']
    finally:
        pending.cancel()
        await asyncio.gather(pending, return_exceptions=True)
        await client.close()


@pytest.mark.parametrize('failure', ['STORAGE_UNAVAILABLE', 'hello_timeout'])
async def test_failed_first_hello_does_not_pin_identity_on_reconnect(host, failure):
    host.bac.storage_error = 'STORAGE_UNAVAILABLE' if failure == 'STORAGE_UNAVAILABLE' else None
    host.bac.drop_hello = failure == 'hello_timeout'
    client = client_for(host, connect_timeout=.5)
    try:
        await client.connect()
        assert client.bac.status == 'unavailable' and client.bac.reason == failure
        assert client._bac_host_id is None
        assert await client.invoke('app:get-version') == '9.9.9'
        await client.close()
        host.bac.storage_error = None
        host.bac.drop_hello = False
        await client.connect()
        assert client.bac.status == 'negotiated'
        assert client._bac_host_id == host.bac.host_id
        assert len(host.bac.frames) == 2
        assert all('expectedHostId' not in frame['params'] for frame in host.bac.frames)
    finally:
        await client.close()


async def test_cancelled_concurrent_connect_allows_waiter_to_negotiate(host):
    host.bac.drop_hello = True
    client = client_for(host)
    first = asyncio.create_task(client.connect())
    second = None
    try:
        await asyncio.wait_for(host.bac.hello_received.wait(), 3)
        second = asyncio.create_task(client.connect())
        await asyncio.sleep(0)
        assert not second.done()
        host.bac.drop_hello = False
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        await asyncio.wait_for(second, 3)
        assert client.connected and client.bac.status == 'negotiated'
        assert len(host.bac.frames) == 2
        assert 'expectedHostId' not in host.bac.frames[-1]['params']
    finally:
        first.cancel()
        if second is not None:
            second.cancel()
        await asyncio.gather(*(task for task in (first, second) if task is not None), return_exceptions=True)
        await client.close()


async def test_close_immediately_after_hello_reply_does_not_pin_identity(host, monkeypatch):
    client = client_for(host)
    roundtrip = client._roundtrip
    close_once = True

    async def close_after_reply(frame, timeout, *args, **kwargs):
        nonlocal close_once
        reply = await roundtrip(frame, timeout, *args, **kwargs)
        if frame.get('channel') == bac.HELLO_CHANNEL and close_once:
            close_once = False
            await client.close()
        return reply

    monkeypatch.setattr(client, '_roundtrip', close_after_reply)
    try:
        with pytest.raises(ConnectionLost):
            await client.connect()
        assert not client.connected and client.bac is None and client._bac_host_id is None
        await client.connect()
        assert client.bac.status == 'negotiated'
        assert 'expectedHostId' not in host.bac.frames[-1]['params']
    finally:
        await client.close()


async def test_reader_drains_event_flood_before_hello(host):
    host.flood_before_reply = 1024
    client = client_for(host, max_concurrency=1)
    sub = client.subscribe(maxsize=3)
    try:
        await client.connect()
        assert client.bac.status == 'negotiated'
        assert client.events_seen == 1024 and sub.dropped == 1021
        assert len(host.bac.frames) == 1
    finally:
        client.unsubscribe(sub)
        await client.close()


ERROR_CODES = ['INVALID_REQUEST', 'PAYLOAD_TOO_LARGE', 'RESPONSE_TOO_LARGE', 'CONTEXT_NOT_SUPPORTED',
               'UNSUPPORTED_VERSION', 'HOST_MISMATCH', 'STORAGE_UNAVAILABLE',
               'STORAGE_CORRUPT', 'INTERNAL_ERROR']


@pytest.mark.parametrize('code', ERROR_CODES)
async def test_error_envelope_keeps_ordinary_paths(host, grant, code):
    host.bac.hello_result = host.bac.error(code)
    async with client_for(host) as client:
        assert client.bac.status == 'unavailable' and client.bac.reason == code
        await ordinary_paths(client, grant)
        await client.connect()
        assert len(host.bac.frames) == 1


def invalid_results():
    good = {'ok': True, 'value': MockBac().hello_value()}
    cases = [('non-object', None), ('array', []), ('no-ok', {'value': good['value']}),
             ('numeric-ok', {'ok': 1, 'value': good['value']}), ('no-value', {'ok': True}),
             ('null-value', {'ok': True, 'value': None}), ('error-null', {'ok': False, 'error': None})]
    for key, value in [('hostId', 'h_BAD'), ('hostId', 'h_' + 'A' * 32), ('hostId', 'h_' + '0' * 33),
                       ('protocol', 'bac/2'), ('minor', 1), ('minor', True), ('minor', '0'),
                       ('features', None), ('features', {'gitObserve': 1}),
                       ('features', {'workspaceRead': None}), ('limits', {'x': -1}),
                       ('limits', {'x': 1.5}), ('limits', {'x': True}), ('limits', {'x': '1'}),
                       ('limits', []), ('build', {}), ('build', None)]:
        result = copy.deepcopy(good)
        result['value'][key] = value
        cases.append((f'invalid-{key}-{value}', result))
    for key, value in [('product', 'other'), ('revision', None), ('release', 1), ('dirty', 1)]:
        result = copy.deepcopy(good)
        result['value']['build'][key] = value
        cases.append((f'build-{key}', result))
    for key in ('hostId', 'protocol', 'minor', 'features', 'limits', 'build'):
        result = copy.deepcopy(good)
        del result['value'][key]
        cases.append((f'missing-{key}', result))
    for key, value in [('code', 'bad'), ('code', 'X' * 65), ('code', 'X1'), ('code', 'A\n'),
                       ('code', None), ('message', None), ('retryable', 0), ('details', [])]:
        result = MockBac.error('INTERNAL_ERROR')
        result['error'][key] = value
        cases.append((f'error-{key}-{value}', result))
    for key in ('code', 'message', 'retryable', 'details'):
        result = MockBac.error('INTERNAL_ERROR')
        del result['error'][key]
        cases.append((f'error-missing-{key}', result))
    return cases


@pytest.mark.parametrize('name,result', invalid_results(), ids=lambda value: value if isinstance(value, str) else None)
async def test_invalid_result_keeps_ordinary_paths(host, grant, name, result):
    host.bac.hello_result = result
    async with client_for(host) as client:
        assert client.bac.status == 'unavailable' and client.bac.reason == 'invalid_reply', name
        await ordinary_paths(client, grant)
        assert len(host.bac.frames) == 1


async def test_invoke_error_is_diagnostic_only(host, grant):
    host.bac.invoke_error = 'HOST_MISMATCH ' + TOKEN
    async with client_for(host) as client:
        assert client.bac.reason == 'invoke_error'
        await ordinary_paths(client, grant)
        assert len(host.bac.frames) == 1


async def test_timeout_drops_late_reply_and_never_retries(host, grant):
    host.bac.drop_hello = True
    client = client_for(host, connect_timeout=.1)
    try:
        await client.connect()
        state = client.bac
        assert state.status == 'unavailable' and state.reason == 'hello_timeout'
        assert not client._pending
        for ws in list(host.conns):
            await ws.send(json.dumps({'type': 'invoke-result', 'id': bac.HELLO_ID,
                                      'result': {'ok': True, 'value': host.bac.hello_value()}}))
        await ordinary_paths(client, grant)
        await client.connect()
        assert client.bac is state and len(host.bac.frames) == 1
    finally:
        await client.close()


async def test_closed_hello_fails_connect_and_resets_state(host):
    host.bac.close_hello = True
    client = client_for(host)
    try:
        with pytest.raises(ConnectionLost):
            await client.connect()
        assert not client.connected and client.bac is None
        assert client._ws is None and client._reader is None and not client._pending
        host.bac.close_hello = False
        await client.connect()
        assert client.bac.status == 'negotiated'
        assert len(host.bac.frames) == 2
    finally:
        await client.close()


async def test_remembered_identity_survives_mismatch_and_absent_connection(host, grant):
    client = client_for(host)
    try:
        await client.connect()
        learned = client.bac.host_id
        await client.close()
        host.bac.host_id = 'h_' + 'f' * 32
        for _ in range(2):
            await client.connect()
            assert client.bac.reason == 'HOST_MISMATCH'
            assert host.bac.frames[-1]['params']['expectedHostId'] == learned
            count = len(host.bac.frames)
            await ordinary_paths(client, grant)
            assert len(host.bac.frames) == count
            await client.close()
        host.bac.advertisement = MockBac.MISSING
        await client.connect()
        assert client.bac.status == 'absent'
        await client.close()
        host.bac.advertisement = {'protocol': 'bac/1', 'minor': 0}
        await client.connect()
        assert host.bac.frames[-1]['params']['expectedHostId'] == learned
        assert client.bac.reason == 'HOST_MISMATCH'
    finally:
        await client.close()
    async with client_for(host) as new_client:
        assert new_client.bac.status == 'negotiated'
        assert 'expectedHostId' not in host.bac.frames[-1]['params']


async def test_remote_close_clears_negotiated_state(host):
    client = client_for(host)
    try:
        await client.connect()
        learned = client.bac.host_id
        await host.drop_all()
        await asyncio.wait_for(client._reader, 3)
        assert client.bac is None and not client.connected
        await client.connect()
        assert host.bac.frames[-1]['params']['expectedHostId'] == learned
    finally:
        await client.close()


@pytest.mark.parametrize('writes', [False, True])
@pytest.mark.parametrize('orchestrate', [False, True])
@pytest.mark.parametrize('tabs', [False, True])
@pytest.mark.parametrize('channel', ['bac:hello', *MockBac.CHANNELS, 'bac:future'])
async def test_bac_blocked_in_all_public_paths(mock, writes, orchestrate, tabs, channel):
    assert channels.HANDSHAKE_CHANNELS == {bac.HELLO_CHANNEL}
    with pytest.raises(ChannelNotAllowed):
        channels.check_allowed(channel, allow_writes=writes, allow_orchestrate=orchestrate)
    # Config itself refuses orchestrate without writes; constructor overrides
    # exercise each requested combination through the normal effective-tier gates.
    client = BatClient(make_config(mock, writes=True, orchestrate=True, tabs=tabs).host('h1'),
                       device_id='bac-test', allow_writes=writes, allow_orchestrate=orchestrate)
    try:
        for method in (client.invoke, client.guard_read):
            with pytest.raises(ChannelNotAllowed):
                await method(channel, {})
        assert mock.frames == []
    finally:
        await client.close()


def test_immutable_known_values_and_unknown_members():
    value = MockBac().hello_value()
    value['features'] = {'gitObserve': True, 'future': {'unknown': True}}
    value['build']['future'] = []
    value['future'] = {}
    state = bac.validate_hello(value)
    assert state.features == dict.fromkeys(bac.FEATURES, False) | {'gitObserve': True}
    assert 'future' not in state.features
    with pytest.raises(FrozenInstanceError):
        state.status = 'unavailable'
    with pytest.raises(FrozenInstanceError):
        state.build.dirty = True
    with pytest.raises(TypeError):
        state.features['gitObserve'] = False
    with pytest.raises(TypeError):
        state.limits['requestBytes'] = 1
    value['features']['gitObserve'] = False
    value['limits']['requestBytes'] = 1
    assert state.features['gitObserve'] and state.limits['requestBytes'] == 65536
    assert bac.parse_envelope({'ok': True, 'value': 4, 'future': True}) == 4
    assert bac.hello_state({'type': 'unexpected', 'result': {'ok': True, 'value': value}}).reason == 'invalid_reply'


@pytest.mark.parametrize('key', ['product', 'revision', 'release', 'dirty'])
def test_missing_build_fields_are_invalid(key):
    value = MockBac().hello_value()
    del value['build'][key]
    state = bac.hello_state({'type': 'invoke-result', 'result': {'ok': True, 'value': value}})
    assert state.status == 'unavailable' and state.reason == 'invalid_reply'


@pytest.mark.parametrize('dirty', [True, False])
def test_dirty_boolean_and_zero_limit_are_valid(dirty):
    value = MockBac().hello_value()
    value['build']['dirty'] = dirty
    value['limits'] = {'x': 0}
    state = bac.validate_hello(value)
    assert state.build.dirty is dirty and state.limits == {'x': 0}


def test_envelope_error_carries_diagnostics_with_redacted_exception_text():
    register_secret(TOKEN)
    result = MockBac.error('INTERNAL_ERROR', diagnostic='synthetic')
    result['error']['message'] = f'synthetic message {TOKEN} {MockBac().host_id}'
    with pytest.raises(BacReplyError) as caught:
        bac.parse_envelope(result)
    error = caught.value
    assert error.code == 'INTERNAL_ERROR' and error.retryable is False
    assert error.details == {'diagnostic': 'synthetic'}
    assert TOKEN not in str(error) and MockBac().host_id not in str(error)
    assert TOKEN not in error.message


@pytest.mark.parametrize(('revision', 'release'), [
    ('unknown', 'dev'), ('0' * 40, ' '), ('abcdef0123' * 4, '~' * 64),
])
def test_valid_build_text_boundaries(revision, release):
    value = MockBac().hello_value()
    value['build'].update(revision=revision, release=release)
    state = bac.validate_hello(value)
    assert (state.build.revision, state.build.release) == (revision, release)


@pytest.mark.parametrize(('key', 'text'), [
    ('revision', 'a' * 41), ('revision', 'A' * 40), ('revision', 'a' * 39),
    ('release', ''), ('release', 'a' * 65), ('release', 'x\ny'), ('release', 'é'),
])
def test_invalid_build_text_is_unavailable(key, text):
    value = MockBac().hello_value()
    value['build'][key] = text
    state = bac.hello_state({'type': 'invoke-result', 'result': {'ok': True, 'value': value}})
    assert state.status == 'unavailable' and state.reason == 'invalid_reply'


def test_error_details_are_deep_copied_and_message_is_bounded_after_redaction():
    register_secret(TOKEN)
    details = {'nested': [{'value': 'original'}]}
    message = TOKEN + MockBac().host_id + 'x' * 400
    error = BacReplyError('INTERNAL_ERROR', message, False, details)
    details['nested'][0]['value'] = 'changed'
    assert error.details == {'nested': [{'value': 'original'}]}
    assert len(error.message) == 300
    assert error.message.startswith('<redacted><host-id>')
    assert TOKEN not in str(error) and MockBac().host_id not in str(error)


@pytest.mark.parametrize('code', ['A', 'A' * 64, 'FUTURE_ERROR'])
def test_unknown_valid_error_codes_are_preserved(code):
    result = MockBac.error('INTERNAL_ERROR')
    result['error']['code'] = code
    result['error']['message'] = 'HOST_MISMATCH is only diagnostic text here'
    assert bac.hello_state({'type': 'invoke-result', 'result': result}).reason == code


async def test_first_invoke_negotiates_without_semaphore_recursion(host, monkeypatch):
    client = client_for(host, max_concurrency=1, connect_timeout=60)
    roundtrip = client._roundtrip
    timeouts = []

    async def capture(frame, timeout, *args, **kwargs):
        if frame.get('channel') == bac.HELLO_CHANNEL:
            timeouts.append(timeout)
        return await roundtrip(frame, timeout, *args, **kwargs)

    monkeypatch.setattr(client, '_roundtrip', capture)
    try:
        assert await asyncio.wait_for(client.invoke('app:get-version'), 3) == '9.9.9'
        assert timeouts == [10.0]
        assert [frame['id'] for frame in host.invokes] == ['bac-hello-1', 'batc-1']
    finally:
        await client.close()


@pytest.mark.parametrize('mismatch', [False, True])
async def test_logs_have_stable_codes_without_host_diagnostics(host, tmp_path, monkeypatch, mismatch):
    path = tmp_path / 'errors.jsonl'
    monkeypatch.setenv('BAT_ERROR_LOG', str(path))
    client = client_for(host)
    try:
        if mismatch:
            await client.connect()
            await client.close()
            host.bac.host_id = 'h_' + 'f' * 32
        else:
            host.bac.hello_result = host.bac.error('INTERNAL_ERROR', token=TOKEN, hostId=host.bac.host_id)
            host.bac.hello_result['error']['message'] = f'raw host message {TOKEN} {host.bac.host_id}'
        await client.connect()
        await client.connect()
        records = [json.loads(line) for line in path.read_text().splitlines()]
        code = 'connector.bac.host_mismatch' if mismatch else 'connector.bac.hello_unavailable'
        assert len(records) == 1 and records[0]['code'] == code
        assert records[0]['level'] == 'warn' and records[0]['count'] == 1
        text = path.read_text()
        assert TOKEN not in text and 'raw host message' not in text
        assert host.bac.host_id not in text and MockBac().host_id not in text
    finally:
        await client.close()


async def test_client_rejects_success_with_a_different_remembered_identity(host, grant, tmp_path, monkeypatch):
    path = tmp_path / 'errors.jsonl'
    monkeypatch.setenv('BAT_ERROR_LOG', str(path))
    client = client_for(host)
    try:
        await client.connect()
        learned = client.bac.host_id
        await client.close()
        replacement = host.bac.hello_value()
        replacement['hostId'] = 'h_' + 'f' * 32
        host.bac.hello_result = {'ok': True, 'value': replacement}
        await client.connect()
        assert client.bac.status == 'unavailable' and client.bac.reason == 'HOST_MISMATCH'
        assert client.bac.host_id is None and not client.bac.features
        assert client._bac_host_id == learned
        assert host.bac.frames[-1]['params']['expectedHostId'] == learned
        await ordinary_paths(client, grant)
        assert len(host.bac.frames) == 2
        records = [json.loads(line) for line in path.read_text().splitlines()]
        assert len(records) == 1 and records[0]['count'] == 1
        assert records[0]['code'] == 'connector.bac.host_mismatch' and records[0]['level'] == 'warn'
        assert learned not in path.read_text() and replacement['hostId'] not in path.read_text()
        await client.close()
        host.bac.hello_result = {'ok': True, 'value': host.bac.hello_value()}
        await client.connect()
        assert client.bac.status == 'negotiated' and client.bac.host_id == learned
        assert host.bac.frames[-1]['params']['expectedHostId'] == learned
    finally:
        await client.close()


@pytest.mark.parametrize('code', ['STORAGE_UNAVAILABLE', 'STORAGE_CORRUPT'])
@pytest.mark.parametrize('diagnostic,accepted', [
    ('unspecified', True), ('future_reason_7', True), ('a' * 64, True),
    ('/fixture/private/identity.json', False), ('x' * 65, False), (None, False),
    (17, False), (True, False), ({'path': '/fixture/private'}, False), ([], False),
    ('bad\n', False), ('UPPER', False), (TOKEN, False), ('h_' + 'f' * 32, False),
])
async def test_storage_diagnostics_are_bounded_and_do_not_select_behavior(
    host, grant, tmp_path, monkeypatch, code, diagnostic, accepted,
):
    path = tmp_path / 'errors.jsonl'
    monkeypatch.setenv('BAT_ERROR_LOG', str(path))
    host.bac.storage_error = code
    host.bac.storage_reason = diagnostic
    async with client_for(host) as client:
        assert client.bac.status == 'unavailable' and client.bac.reason == code
        assert client.bac.storage_reason == (diagnostic if accepted else None)
        await ordinary_paths(client, grant)
        assert len(host.bac.frames) == 1
    records = [json.loads(line) for line in path.read_text().splitlines()]
    assert len(records) == 1 and records[0]['code'] == 'connector.bac.hello_unavailable'
    expected = {'reason': code}
    if accepted:
        expected['storage_reason'] = diagnostic
    assert records[0]['context'] == expected
    text = path.read_text()
    assert host.bac.host_id not in text and TOKEN not in text
    if isinstance(diagnostic, str) and not accepted:
        assert diagnostic not in text


def test_storage_diagnostic_is_not_interpreted_for_other_codes():
    result = MockBac.error('INTERNAL_ERROR', reason='future_reason_7')
    state = bac.hello_state({'type': 'invoke-result', 'result': result})
    assert state.reason == 'INTERNAL_ERROR' and state.storage_reason is None
