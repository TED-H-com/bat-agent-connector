"""Private delivery transport cannot outlive its bounded caller silently."""
import asyncio
import threading
import time

import pytest

from bat_agent_connector import pm_maintenance_repair_delivery as chain
from bat_agent_connector.errors import TokenUnavailable
from bat_agent_connector.github import GitHubAmbiguous
from tests.test_github_bounded_read import http_provider as provider_fixture

http_provider = provider_fixture


async def test_private_delivery_deadline_retains_one_late_thread_and_no_repeat(http_provider, monkeypatch):
    client, state = http_provider
    monkeypatch.setattr(chain, 'http_timeout', lambda method: 0.05)
    entered, release = threading.Event(), threading.Event()
    original = client._read_opener.open
    observed = []
    def slow(*args, **kwargs):
        observed.append(kwargs['timeout'])
        entered.set()
        assert release.wait(5)
        return original(*args, **kwargs)
    monkeypatch.setattr(client._read_opener, 'open', slow)
    started = time.monotonic()
    try:
        with pytest.raises(GitHubAmbiguous, match='deadline'):
            await client.call('GET', '/repos/example/product')
        assert entered.is_set() and time.monotonic() - started < 1
        assert observed == [0.05]
        for _ in range(3):
            with pytest.raises(GitHubAmbiguous, match='still finishing'):
                await client.call('GET', '/repos/example/product')
    finally:
        release.set()
    for _ in range(100):
        if client._bounded_delivery_slot.acquire(blocking=False):
            client._bounded_delivery_slot.release()
            break
        await asyncio.sleep(0.01)
    else:
        pytest.fail('Original worker did not finish')
    assert len(state['requests']) == 1
    assert len(observed) == 1


async def test_private_delivery_does_not_use_previous_provider_token(http_provider, monkeypatch):
    client, state = http_provider
    monkeypatch.setattr(chain, 'http_timeout', lambda method: 1)
    monkeypatch.delenv('FAKE_GH_BOUNDED_TOKEN')
    with pytest.raises(TokenUnavailable):
        await client.call('GET', '/repos/example/product')
    assert state['requests'] == []


async def test_private_delivery_truncated_original_response_is_uncertain(http_provider, monkeypatch):
    client, state = http_provider
    monkeypatch.setattr(chain, 'http_timeout', lambda method: 1)
    state.update(raw=b'{}', headers={'Content-Length': '100'})
    with pytest.raises(GitHubAmbiguous):
        await client.call('GET', '/repos/example/product')
    assert len(state['requests']) == 1
