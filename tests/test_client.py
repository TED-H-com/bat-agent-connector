"""Core client: auth v2, pinning, allowlist, backpressure, reconnect, redaction."""

from __future__ import annotations

import asyncio
import json

import pytest

from bat_agent_connector import channels, registry
from bat_agent_connector.client import BatClient, _wait_with_timeout
from bat_agent_connector.config import device_id
from bat_agent_connector.errors import (
    AuthError,
    ChannelNotAllowed,
    FingerprintMismatch,
    InvokeError,
    ResourceReadOnly,
    TaskDispatchCancelled,
)
from bat_agent_connector.resource_policy import BY_ACTION, WriteGrant
from tests.conftest import adopt, make_config
from tests.mockbat import TOKEN

SEND = BY_ACTION["session.send"].channels


@pytest.fixture
def client_send_grant(controller_context):
    adopt("sess-claude-0001")
    entry = registry.get("h1", "sess-claude-0001")
    return WriteGrant("h1", "session.send", "sess-claude-0001", SEND,
                      controller_authority=entry["controller_authority"],
                      controller_incarnation=entry["controller_incarnation"])


@pytest.mark.parametrize('method', ['invoke', 'guard_read'])
@pytest.mark.parametrize('reply', [{'type': 'invoke-result'}, {'type': 'other', 'result': None},
                                  {'type': 'invoke-error', 'error': 'fixture unavailable'},
                                  {'type': 'invoke-result', 'result': None}])
async def test_metadata_absence_requires_explicit_successful_null(mock, monkeypatch, method, reply):
    client = client_for(mock)
    original = client._roundtrip
    async def response(frame, *args, **kwargs):
        if frame.get('channel') == 'claude:get-session-meta':
            return reply
        return await original(frame, *args, **kwargs)
    monkeypatch.setattr(client, '_roundtrip', response)
    try:
        call = getattr(client, method)
        if reply == {'type': 'invoke-result', 'result': None}:
            assert await call('claude:get-session-meta', {'sessionId': 'fixture'}) is None
        else:
            with pytest.raises(InvokeError):
                await call('claude:get-session-meta', {'sessionId': 'fixture'})
    finally:
        await client.close()


def client_for(mock, **kw):
    cfg = make_config(
        mock, **{k: v for k, v in kw.items() if k in ("writes", "orchestrate", "tabs", "fingerprint")}
    )
    return BatClient(cfg.host("h1"), device_id="dev-test")


async def test_auth_v2_and_ping(mock):
    async with client_for(mock) as c:
        assert c.auth_info["protocol"] == "bat-remote/v2"
        assert c.auth_info["serverVersion"] == "9.9.9"
        assert await c.ping() >= 0
    auth = mock.auth_frames[0]
    assert auth["protocols"] == ["bat-remote/v2"]
    assert auth["clientInfo"]["deviceId"] == "dev-test"


async def test_reply_and_cancellation_in_same_loop_turn_preserve_cancellation(mock):
    client = client_for(mock)
    loop = asyncio.get_running_loop()
    client._reader = loop.create_future()

    class ReplySocket:
        async def send(self, encoded):
            frame = json.loads(encoded)
            reply = client._pending[frame["id"]]
            caller = asyncio.current_task()

            def complete_and_cancel():
                reply.set_result({"type": "invoke-result", "result": {"isStreaming": True}})
                caller.cancel()

            loop.call_soon(complete_and_cancel)

    client._ws = ReplySocket()
    request = asyncio.create_task(client._roundtrip(
        {"id": "cancel-race", "type": "invoke", "channel": "claude:get-session-meta"}, 30))
    try:
        with pytest.raises(asyncio.CancelledError):
            await request
        assert not client._pending
    finally:
        client._reader.cancel()
        client._reader = client._ws = None


async def test_transport_deadline_cancels_and_joins_its_owned_wait():
    entered, released = asyncio.Event(), asyncio.Event()

    async def wait():
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            released.set()

    with pytest.raises(asyncio.TimeoutError):
        await _wait_with_timeout(wait(), .01)
    assert entered.is_set() and released.is_set()


async def test_transport_guard_runs_after_awaited_connect_before_send_frame(mock, client_send_grant):
    c = client_for(mock, writes=True)
    original_connect = c.connect
    entered, release = asyncio.Event(), asyncio.Event()
    allowed = True

    async def delayed_connect():
        entered.set()
        await release.wait()
        await original_connect()

    def guard():
        if not allowed:
            raise TaskDispatchCancelled("paused before BAT frame")

    c.connect = delayed_connect
    try:
        send = asyncio.create_task(c.invoke("claude:send-message",
                                            {"sessionId": "sess-claude-0001", "prompt": "test"},
                                            before_send=guard,
                                            grant=client_send_grant))
        await asyncio.wait_for(entered.wait(), 2)
        allowed = False
        release.set()
        with pytest.raises(TaskDispatchCancelled):
            await asyncio.wait_for(send, 5)
        assert not any(i["channel"] == "claude:send-message" for i in mock.invokes)
    finally:
        release.set()
        await c.close()


async def test_stable_device_id(isolated_dirs):
    a = device_id()
    b = device_id()
    assert a == b and a.startswith("batc-")


async def test_bad_token_is_rejected_and_not_leaked(mock, monkeypatch):
    monkeypatch.setenv("BATC_TEST_TOKEN", "wrong-token-value-xyz")
    c = client_for(mock)
    with pytest.raises(AuthError) as ei:
        await c.connect()
    assert "wrong-token-value-xyz" not in str(ei.value)


async def test_pinning_mismatch_rejected_before_auth(mock):
    c = client_for(mock, fingerprint="00" * 32)
    with pytest.raises(FingerprintMismatch):
        await c.connect()
    await asyncio.sleep(0.1)
    assert mock.auth_frames == []  # token never sent


async def test_legacy_downgrade_refused(mock):
    mock.protocol = "bat-remote/legacy-v1"
    with pytest.raises(AuthError):
        await client_for(mock).connect()


@pytest.mark.parametrize(
    "channel",
    [
        "claude:rest-session",
        "claude:reset-session",
        "pty:kill",
        "pty:write",
        "fs:delete-path",
        "fs:readFile",
        "settings:save",
        "workspace:save",
        "runtime:install",
        "app:install-update",
        "claude:account-switch",
        "agent:fork-session",
        "claude:cleanup-worktree",
        "made:up",
    ],
)
async def test_never_exposed_channels_blocked_even_with_all_tiers(mock, channel):
    async with client_for(mock, writes=True, orchestrate=True, tabs=True) as c:
        with pytest.raises(ChannelNotAllowed):
            await c.invoke(channel, {})
    assert channel not in mock.channels()


@pytest.mark.parametrize("channel", sorted(channels.WRITE_CHANNELS) + ["agent:send-message"])
async def test_write_channels_blocked_when_writes_disabled(mock, channel):
    async with client_for(mock) as c:
        with pytest.raises(ChannelNotAllowed):
            await c.invoke(channel, {"sessionId": "x"})
    assert mock.invokes == []


@pytest.mark.parametrize("channel", sorted(channels.ORCHESTRATE_CHANNELS))
async def test_orchestrate_channels_need_orchestrate_tier(mock, channel):
    async with client_for(mock, writes=True) as c:
        with pytest.raises(ChannelNotAllowed):
            await c.invoke(channel, {"sessionId": "x"})
    assert mock.invokes == []


async def test_write_allowed_when_enabled(mock, client_send_grant):
    async with client_for(mock, writes=True) as c:
        r = await c.invoke(
            "agent:send-message", {"sessionId": "sess-claude-0001", "prompt": "hi", "clientMessageId": "m1"},
            grant=client_send_grant,
        )
    assert r["accepted"] is True
    assert mock.channels() == ["claude:send-message"]  # alias folded


@pytest.mark.parametrize("grant", [
    None,
    WriteGrant("h1", "session.send", "sess-codex-0002", SEND),  # another session
    WriteGrant("h2", "session.send", "sess-claude-0001", SEND),  # another host
    WriteGrant("h1", "session.interrupt", "sess-claude-0001", BY_ACTION["session.interrupt"].channels),
])
async def test_write_frame_needs_matching_policy_grant(mock, grant):
    async with client_for(mock, writes=True, orchestrate=True, tabs=True) as c:
        with pytest.raises(ResourceReadOnly):
            await c.invoke("claude:send-message", {"sessionId": "sess-claude-0001", "prompt": "hi"}, grant=grant)
        with pytest.raises(ResourceReadOnly):
            await c.append_workspace_terminal("default", {"id": "sess-claude-0001"}, grant=grant)
    assert mock.invokes == []


def test_never_exposed_examples_are_not_allowlisted():
    allowed = channels.READ_CHANNELS | channels.WRITE_CHANNELS | channels.ORCHESTRATE_CHANNELS
    assert not (channels.NEVER_EXPOSED_EXAMPLES & allowed)


async def test_backpressure_reader_keeps_draining(mock):
    mock.flood_before_reply = 3000
    async with client_for(mock) as c:
        sub = c.subscribe(maxsize=100)  # never consumed
        r = await c.invoke("app:get-version", {})
        assert r == "9.9.9"
        assert sub.dropped >= 2900 and len(sub.queue) == 100
        assert c.events_seen >= 3000
        assert c.connected


async def test_reconnect_after_drop(mock):
    async with client_for(mock) as c:
        await c.invoke("app:get-version", {})
        await mock.drop_all()
        await asyncio.sleep(0.2)
        assert await c.invoke("app:get-version", {}) == "9.9.9"
    assert len(mock.auth_frames) == 2


async def test_frames_never_contain_token_after_auth(mock):
    async with client_for(mock) as c:
        await c.invoke("workspace:load", {"profileId": "default"})
    non_auth = [f for f in mock.frames if f.get("type") != "auth"]
    assert TOKEN not in json.dumps(non_auth)
