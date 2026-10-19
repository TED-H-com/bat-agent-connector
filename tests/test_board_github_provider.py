"""Real HTTP fixtures; credentials and provider text never enter errors or logs."""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

import pytest

from bat_agent_connector import board_github_provider as provider
from bat_agent_connector.config import GitHubConfig
from tests.test_board_github_sync import issue


@pytest.fixture
def github(monkeypatch):
    state = {'requests': [], 'status': 200, 'headers': {}, 'next': True, 'pages': {1: [issue(1)], 2: [issue(2, pr=True, draft=True)]}}
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            query = parse_qs(urlsplit(self.path).query)
            page = int(query['page'][0])
            state['requests'].append({'path': self.path, 'headers': self.headers})
            payload = state['pages'][page]
            status = 304 if self.headers.get('If-None-Match') == f'"page-{page}"' else state['status']
            self.send_response(status)
            for key, value in state['headers'].items():
                self.send_header(key, value)
            self.send_header('ETag', f'"page-{page}"')
            if page == 1 and state['next']:
                self.send_header('Link', '<https://attacker.invalid/never-follow>; rel="next"')
            self.end_headers()
            if status != 304:
                self.wfile.write(json.dumps(payload).encode())
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setattr(provider, 'access_token', lambda cfg: None)
    cfg = GitHubConfig(api_url=f'http://127.0.0.1:{server.server_port}', timeout_s=1)
    try:
        yield provider.BoardGitHubProvider(cfg), state
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


async def test_public_pagination_then_etag_304_reuses_all_pages(github):
    client, state = github
    first = await client.collect('example/board', {})
    assert len(first['items']) == 2 and not first['authenticated']
    assert [v['lane'] for v in first['items']] == ['todo', 'doing']
    assert all('Authorization' not in req['headers'] for req in state['requests'])
    second = await client.collect('example/board', first['pages'])
    assert second == first
    assert len(state['requests']) == 4
    for req in state['requests'][2:]:
        assert req['headers']['If-None-Match'] in ('"page-1"', '"page-2"')
        assert req['path'].startswith('/repos/example/board/issues?')


@pytest.mark.parametrize(('status', 'headers', 'code'), [
    (429, {'Retry-After': '1200'}, 'rate_limited'),
    (403, {'X-RateLimit-Remaining': '0'}, 'rate_limited'),
    (403, {'Retry-After': '600'}, 'rate_limited'), (503, {}, 'unreachable'),
    (401, {}, 'auth_required'), (302, {'Location': 'https://attacker.invalid'}, 'unreachable'),
])
async def test_failures_are_sanitized_and_back_off(github, status, headers, code, caplog):
    client, state = github
    state.update(status=status, headers=headers)
    state['pages'][1] = {'message': 'sensitive-provider-error'}
    with pytest.raises(provider.BoardGitHubError) as exc:
        await client.collect('example/board', {})
    assert exc.value.code == code
    assert 'sensitive-provider-error' not in str(exc.value) + caplog.text
    if headers.get('Retry-After'):
        assert exc.value.retry_after >= int(headers['Retry-After'])
    assert len(state['requests']) == 1


async def test_second_page_failure_never_returns_partial_snapshot(github):
    client, state = github
    state['pages'][2] = {'message': 'bad page'}
    with pytest.raises(provider.BoardGitHubError):
        await client.collect('example/board', {})
    assert len(state['requests']) == 2


def test_existing_token_store_wins_and_gh_fallback_never_prints(monkeypatch, caplog):
    calls = []
    class Config:
        def token(self):
            return 'fixture-token'
    monkeypatch.setattr(provider.subprocess, 'run', lambda *a, **kw: calls.append(a))
    assert provider.access_token(Config()) == 'fixture-token' and not calls
    monkeypatch.setattr(provider.shutil, 'which', lambda command: '/example/bin/gh')
    class Result:
        returncode = 0
        stdout = b'fixture-cli-token\n'
    def run(argv, **kwargs):
        assert argv == ['/example/bin/gh', 'auth', 'token', '--hostname', 'github.com']
        assert kwargs['capture_output'] is True and kwargs['timeout'] == 5
        return Result()
    monkeypatch.setattr(provider.subprocess, 'run', run)
    assert provider.access_token(GitHubConfig()) == 'fixture-cli-token'
    assert 'fixture-cli-token' not in caplog.text


@pytest.mark.parametrize('url', ['http://github.com/example/board/issues/1', 'https://evil.invalid/example/board/issues/1', 'https://github.com/other/repo/issues/1'])
def test_identity_rejects_provider_url_mismatch(url):
    value = issue(1)
    value['html_url'] = url
    with pytest.raises(provider.BoardGitHubError):
        provider.normalize('example/board', value)


async def test_unchanged_full_last_page_discovers_new_successor(github):
    client, state = github
    state['next'] = False
    state['pages'] = {1: [issue(n) for n in range(1, 101)], 2: [issue(101)]}
    first = await client.collect('example/board', {})
    assert len(first['items']) == 100 and len(state['requests']) == 1
    # An appended issue need not change the original page's ETag or a 304's Link.
    second = await client.collect('example/board', first['pages'])
    assert len(second['items']) == 101 and len(state['requests']) == 3
    assert 'page=2' in state['requests'][-1]['path']
