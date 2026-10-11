"""Bounded HTTP responses against a task-owned loopback fixture only."""
from __future__ import annotations

import hashlib
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from bat_agent_connector.config import GitHubConfig
from bat_agent_connector.errors import TokenUnavailable
from bat_agent_connector.github import GitHubAmbiguous, GitHubClient, GitHubReadLimit


@pytest.fixture
def http_provider(monkeypatch):
    state = {'status': 200, 'body': {'total_count': 0, 'workflow_runs': []}, 'headers': {}, 'requests': [], 'raw': None}
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            state['requests'].append((self.path, self.headers.get('Authorization')))
            raw = state['raw'] if state['raw'] is not None else json.dumps(state['body']).encode()
            self.send_response(state['status'])
            for key, value in state['headers'].items():
                self.send_header(key, value)
            if 'Content-Length' not in state['headers']:
                self.send_header('Content-Length', str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def log_message(self, *_args):
            pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setenv('FAKE_GH_BOUNDED_TOKEN', 'fixture-token')
    cfg = GitHubConfig(api_url=f'http://127.0.0.1:{server.server_port}', token_ref='env:FAKE_GH_BOUNDED_TOKEN')
    try:
        yield GitHubClient(cfg), state
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.mark.parametrize('payload', [[], {'total_count': 0, 'workflow_runs': []}])
async def test_object_and_list_headers_and_body_digest_preserved(http_provider, payload):
    client, state = http_provider
    state['body'] = payload
    state['headers']['Link'] = '<' + client.cfg.api_url + '/repos/example/product/actions/runs?page=2>; rel="next"'
    result = await client.bounded_get('/repos/example/product/actions/runs', query={'page': 1})
    assert result['payload'] == payload and result['metadata']['link'] == state['headers']['Link']
    raw = json.dumps(payload).encode()
    assert result['metadata']['bytes'] == len(raw)
    assert result['metadata']['body_sha256'] == hashlib.sha256(raw).hexdigest()
    assert state['requests'] == [('/repos/example/product/actions/runs?page=1', 'Bearer fixture-token')]


async def test_byte_limit_rejects_large_success_and_error_bodies(http_provider):
    client, state = http_provider
    state['body'] = {'message': 'x' * 1000}
    for status in (200, 403):
        state['status'] = status
        with pytest.raises(GitHubReadLimit):
            await client.bounded_get('/repos/example/product', max_bytes=20)


async def test_redirect_is_not_followed(http_provider):
    client, state = http_provider
    state['status'] = 302
    state['headers']['Location'] = client.cfg.api_url + '/repos/other/product'
    result = await client.bounded_get('/repos/example/product')
    assert result['status'] == 302 and len(state['requests']) == 1


@pytest.mark.parametrize('status,headers', [(429, {}), (500, {}), (403, {'X-RateLimit-Remaining': '0'})])
async def test_rate_limit_and_server_errors_never_empty_success(http_provider, status, headers):
    client, state = http_provider
    state.update(status=status, headers=headers)
    with pytest.raises(GitHubAmbiguous):
        await client.bounded_get('/repos/example/product')


async def test_truncated_body_and_unreadable_json_never_empty_success(http_provider):
    client, state = http_provider
    state.update(raw=b'{}', headers={'Content-Length': '100'})
    with pytest.raises((GitHubReadLimit, GitHubAmbiguous)):
        await client.bounded_get('/repos/example/product')
    state.update(raw=b'not json', headers={})
    with pytest.raises(GitHubAmbiguous):
        await client.bounded_get('/repos/example/product')


async def test_current_token_required_no_fallback_to_previous_read_credential(http_provider, monkeypatch):
    client, state = http_provider
    monkeypatch.delenv('FAKE_GH_BOUNDED_TOKEN')
    with pytest.raises(TokenUnavailable):
        await client.bounded_get('/repos/example/product')
    assert state['requests'] == []
    monkeypatch.setenv('FAKE_GH_BOUNDED_TOKEN', 'rotated-fixture-token')
    await client.bounded_get('/repos/example/product')
    assert state['requests'][0][1] == 'Bearer rotated-fixture-token'


async def test_slow_headers_have_outer_deadline_and_only_one_outstanding_worker(http_provider):
    import asyncio
    import time

    client, _ = http_provider
    entered, release = threading.Event(), threading.Event()
    original = client._read_opener.open
    def delayed_open(*args, **kwargs):
        entered.set()
        assert release.wait(timeout=5), 'test worker was not released'
        return original(*args, **kwargs)
    client._read_opener.open = delayed_open
    started = time.monotonic()
    try:
        with pytest.raises(GitHubReadLimit, match='total time'):
            await client.bounded_get('/repos/example/product', timeout_s=0.05)
        assert entered.is_set() and time.monotonic() - started < 1
        for _ in range(5):
            with pytest.raises(GitHubReadLimit, match='still finishing'):
                await client.bounded_get('/repos/example/product', timeout_s=0.05)
    finally:
        release.set()
    # The abandoned task can finish, but none of its data is returned to a caller.
    for _ in range(100):
        if client._bounded_read_slot.acquire(blocking=False):
            client._bounded_read_slot.release()
            break
        await asyncio.sleep(0.01)
    else:
        pytest.fail('read worker did not release its bounded slot')
    answer = await client.bounded_get('/repos/example/product')
    assert answer['status'] == 200
