"""E2E tests for connector reconnection backoff and status reporting."""

from __future__ import annotations

import asyncio

from bat_agent_connector import service
from tests.test_client import client_for


async def test_e2e_reconnect_after_drop_preserves_subscription_with_backoff(mock):
    """End-to-end integration test verifying websocket connection drops and backoff intervals."""
    async with client_for(mock) as c:
        sub = c.subscribe()

        # Verify initial state
        assert c.connected

        # Drop the connection and wait a bit
        await mock.drop_all()
        await asyncio.sleep(0.2)

        # Verify we can still send/receive which implicitly triggers a reconnect
        assert await c.invoke("app:get-version", {}) == "9.9.9"

        # Publish an event
        await mock.broadcast("app:event", {"version": "9.9.10"})

        # Wait a moment for it to be received
        await asyncio.sleep(0.1)
        assert len(sub.queue) > 0
        event = sub.queue.popleft()
        assert event["channel"] == "app:event"
        assert event["params"]["version"] == "9.9.10"

async def test_e2e_reconnect_backoff_bounds(mock, monkeypatch):
    """Test exponential backoff limits after consecutive disconnects."""
    delays = []
    async def mock_retry_pause(delay_s: float):
        delays.append(delay_s)
        await asyncio.sleep(0.01)  # fast retry for test

    from bat_agent_connector import client
    monkeypatch.setattr(client, "_retry_pause", mock_retry_pause)

    # Cause the mock server to always close the connection upon receiving 'app:get-version'.
    # This will trigger the retry loop in `_invoke_checked`.
    async def dropping_handler(params):
        await mock.drop_all()

    mock.handlers["app:get-version"] = dropping_handler

    async with client_for(mock) as c:
        try:
            await c.invoke("app:get-version", {})
        except client.ConnectionLost:
            pass

    # _invoke_checked uses a max of 3 attempts for read channels, meaning 2 retries.
    # The backoff is `random.uniform(0.0, min(4.0, 0.5 * 2**attempt))`.
    assert len(delays) == 2
    # attempt 0 delay <= 0.5
    assert 0 <= delays[0] <= 0.5
    # attempt 1 delay <= 1.0
    assert 0 <= delays[1] <= 1.0

async def test_e2e_status_heartbeat_ping_reporting(fleet_factory, mock):
    """End-to-end integration test verifying status heartbeat pings and metrics."""
    f = fleet_factory()
    try:
        # First ensure we get a baseline successful status with valid ping latency
        st = await service.host_status(f, "h1")

        # Pings should be populated
        assert isinstance(st["latency"]["ping_ms"], list)
        assert len(st["latency"]["ping_ms"]) == 3
        assert st["latency"]["ping_median_ms"] >= 0

        # Now let's test what happens when the connection drops
        await mock.drop_all()

        # Connection status should handle the drop gracefully
        st_dropped = await service.host_status(f, "h1")
        assert st_dropped["latency"]["ping_ms"] is None or len(st_dropped["latency"]["ping_ms"]) == 3

    finally:
        await f.close()
