"""Read-only, bounded GitHub issue collection for board synchronization.

No redirects, provider error bodies, credential output, or caller-selected URLs.
The issue endpoint includes PRs (including drafts) and has stable issue node IDs.
"""
from __future__ import annotations

import asyncio
import http.client
import json
import re
import shutil
import subprocess
import threading
import time
import urllib.error
import urllib.request
from urllib.parse import urlencode, urlsplit

from . import __version__
from .errors import TokenUnavailable
from .github import _NoReadRedirect, _rate_limited
from .redact import register_secret

MAX_PAGES = 100
INTERVAL = 300


class BoardGitHubError(Exception):
    def __init__(self, code, retry_after=INTERVAL):
        self.code, self.retry_after = code, retry_after
        super().__init__(code)


def access_token(config):
    try:
        return config.token()
    except TokenUnavailable:
        pass
    # A configured API endpoint must not implicitly borrow github.com's CLI
    # credential. Custom endpoints need their explicitly configured token store.
    try:
        endpoint = urlsplit(config.api_url)
        if (endpoint.scheme != 'https' or endpoint.hostname != 'api.github.com'
                or endpoint.port not in (None, 443) or endpoint.username or endpoint.password):
            return None
    except ValueError:
        return None
    try:
        executable = shutil.which('gh')
        if executable is None:
            return None
        result = subprocess.run([executable, 'auth', 'token', '--hostname', 'github.com'],  # noqa: S603 - resolved gh, fixed argv
            capture_output=True, timeout=5, check=False)
        if result.returncode == 0 and 0 < len(result.stdout) < 16_384:
            token = result.stdout.decode().strip()
            if token and not any(c.isspace() for c in token):
                register_secret(token)
                return token
    except (OSError, subprocess.TimeoutExpired, UnicodeError):
        pass
    return None


def normalize(repository, value):
    """Validate identity before any external data can become a board item."""
    number, node = value.get('number'), value.get('node_id')
    pr = isinstance(value.get('pull_request'), dict)
    kind = 'pull' if pr else 'issues'
    url = f'https://github.com/{repository}/{kind}/{number}'
    if (type(number) is not int or not 0 < number < 1_000_000_000 or not isinstance(node, str)
            or not 1 <= len(node) <= 256 or not all(33 <= ord(c) <= 126 for c in node)
            or not isinstance(value.get('html_url'), str) or value['html_url'].lower() != url.lower()
            or value.get('state') not in ('open', 'closed') or not isinstance(value.get('title'), str)):
        raise BoardGitHubError('unreachable')
    if any(ord(c) < 32 and c not in '\r\n\t' or ord(c) == 127 for c in value['title']):
        raise BoardGitHubError('invalid_source')
    title = ' '.join(value['title'].split())[:120] or f'#{number}'
    merged = bool(pr and value['pull_request'].get('merged_at'))
    return {'number': number, 'node_id': node, 'url': url, 'title': title,
        'kind': 'pull_request' if pr else 'issue', 'state': 'merged' if merged else value['state'],
        'state_reason': value.get('state_reason'), 'draft': bool(pr and value.get('draft')),
        'lane': ('github_done' if merged else 'github_completed' if not pr and value.get('state_reason') == 'completed'
            else 'github_closed') if value['state'] == 'closed' or merged else 'doing' if pr else 'todo'}


class BoardGitHubProvider:
    def __init__(self, config):
        self.config = config
        self.slot = threading.BoundedSemaphore(1)

    async def collect(self, repository, cached):
        if not self.slot.acquire(blocking=False):
            raise BoardGitHubError('unreachable')
        def read():
            try:
                return self._collect(repository, cached)
            finally:
                self.slot.release()
        task = asyncio.create_task(asyncio.to_thread(read))
        task.add_done_callback(lambda done: None if done.cancelled() else done.exception())
        try:
            return await asyncio.wait_for(asyncio.shield(task), 120)
        except asyncio.TimeoutError:
            raise BoardGitHubError('unreachable') from None

    def _collect(self, repository, cached):
        config = self.config() if callable(self.config) else self.config
        token = access_token(config)
        headers = {'Accept': 'application/vnd.github+json', 'X-GitHub-Api-Version': config.api_version,
            'User-Agent': f'bat-agent-connector/{__version__}'}
        if token:
            headers['Authorization'] = 'Bearer ' + token
        handlers = [_NoReadRedirect()]
        if urlsplit(config.api_url).hostname in ('127.0.0.1', 'localhost'):
            handlers.append(urllib.request.ProxyHandler({}))
        opener = urllib.request.build_opener(*handlers)
        pages, items, deadline = {}, {}, time.monotonic() + 110

        def request(path, etag=None, *, repository_redirect=False):
            if time.monotonic() >= deadline:
                raise BoardGitHubError('unreachable')
            request_headers = dict(headers)
            if etag:
                request_headers['If-None-Match'] = etag
            req = urllib.request.Request(config.api_url + path, headers=request_headers)  # noqa: S310 - fixed paths on configured API
            try:
                response = opener.open(req, timeout=min(config.timeout_s, max(0.1, deadline - time.monotonic())))  # noqa: S310
            except urllib.error.HTTPError as exc:
                response = exc
            with response:
                if response.code == 301 and repository_redirect:
                    # Resolve only the documented numeric repository identity, never
                    # follow a provider-selected URL or send a credential elsewhere.
                    location, endpoint = urlsplit(response.headers.get('Location', '')), urlsplit(config.api_url)
                    prefix = endpoint.path.rstrip('/')
                    match = re.fullmatch(re.escape(prefix) + r'/repositories/([1-9][0-9]{0,18})', location.path)
                    if (location.scheme, location.netloc) != (endpoint.scheme, endpoint.netloc) or not match or location.query or location.fragment:
                        raise BoardGitHubError('unreachable')
                    return 'repository_id', int(match[1]), None
                if response.code == 304:
                    return 304, response.headers, None
                if response.code == 429 or response.code == 403 and _rate_limited(response.headers, {}):
                    retry = INTERVAL
                    try:
                        retry = max(retry, int(response.headers.get('Retry-After', '0')),
                            int(response.headers.get('X-RateLimit-Reset', '0')) - int(time.time()))
                    except ValueError:
                        pass
                    raise BoardGitHubError('rate_limited', min(retry, 86400))
                if response.code not in (200, 403):
                    raise BoardGitHubError('auth_required' if response.code in (401, 403, 404) else 'unreachable')
                raw = bytearray()
                while True:
                    if time.monotonic() >= deadline:
                        raise BoardGitHubError('unreachable')
                    chunk = getattr(response, 'read1', response.read)(min(65_536, 2_000_001 - len(raw)))
                    raw.extend(chunk)
                    if len(raw) > 2_000_000:
                        raise BoardGitHubError('too_large')
                    if not chunk:
                        break
                length = response.headers.get('Content-Length')
                if length is not None and (not length.isdigit() or int(length) != len(raw)):
                    raise BoardGitHubError('unreachable')
                value = json.loads(raw)
                if response.code == 403:
                    raise BoardGitHubError('rate_limited' if _rate_limited(response.headers, value) else 'auth_required')
                return response.code, response.headers, value

        try:
            previous_repository = cached.get('_repository', {})
            known = previous_repository.get('id') if repository == previous_repository.get('full_name') else None
            code, response_headers, metadata = request(f'/repositories/{known}' if known else f'/repos/{repository}', repository_redirect=not known)
            if code == 'repository_id':
                code, response_headers, metadata = request(f'/repositories/{response_headers}')
            if (code != 200 or not isinstance(metadata, dict) or type(metadata.get('id')) is not int or metadata['id'] < 1
                    or not isinstance(metadata.get('node_id'), str) or not 1 <= len(metadata['node_id']) <= 256
                    or not all(33 <= ord(c) <= 126 for c in metadata['node_id'])
                    or not isinstance(metadata.get('full_name'), str)
                    or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9-]{0,38}/[A-Za-z0-9._-]{1,100}', metadata['full_name']) or metadata['full_name'].endswith(('/.', '/..'))):
                raise BoardGitHubError('unreachable')
            if known and metadata['id'] != known:
                raise BoardGitHubError('identity_conflict')
            repository = metadata['full_name'].lower()
            identity = {'id': metadata['id'], 'node_id': metadata['node_id'], 'full_name': repository}
            # Old normalized pages cannot provide newly added source fields; never
            # revalidate them as though they had been normalized by this contract.
            if previous_repository != identity:
                cached = {}
            pages['_repository'] = identity
            for page in range(1, MAX_PAGES + 1):
                previous = cached.get(str(page), {})
                query = urlencode({'state': 'all', 'sort': 'created', 'direction': 'asc', 'per_page': 100, 'page': page})
                code, response_headers, values = request(f'/repos/{repository}/issues?{query}', previous.get('etag'))
                if code == 304:
                    if not previous:
                        raise BoardGitHubError('unreachable')
                    saved = previous
                else:
                    if not isinstance(values, list) or len(values) > 100:
                        raise BoardGitHubError('unreachable')
                    etag = response_headers.get('ETag', '')
                    if len(etag) > 512 or '\r' in etag or '\n' in etag:
                        raise BoardGitHubError('unreachable')
                    saved = {'etag': etag, 'more': 'rel="next"' in response_headers.get('Link', ''),
                        'items': [normalize(repository, value) for value in values]}
                pages[str(page)] = saved
                for value in saved['items']:
                    old = items.get(value['number'])
                    if old is not None and old != value:
                        raise BoardGitHubError('unreachable')
                    items[value['number']] = value
                probe_successor = code == 304 and len(saved['items']) == 100
                if not saved['more'] and not probe_successor:
                    return {'pages': pages, 'items': list(items.values()), 'authenticated': bool(token),
                        'repository': repository, 'repository_id': metadata['id']}
            raise BoardGitHubError('too_large')
        except (OSError, ValueError, TypeError, AttributeError, urllib.error.URLError, http.client.HTTPException):
            raise BoardGitHubError('unreachable') from None
