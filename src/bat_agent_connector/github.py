"""Minimal GitHub REST client for delivery operations (PR merge, workflow runs), pinned to one API version.

Writes and reads return (status, body) for every HTTP answer the caller must interpret. A transport failure, a
timeout, 429, a rate-limited read (403 with rate-limit headers), a 5xx or a truncated or unreadable body raises
``GitHubAmbiguous``: for a write the effect is unknown and the operation settles it by reading GitHub back
(operations.py), never by sending blindly again. The token comes from ``[github] token_ref``, is resolved again for
every request so a rotated or expiring token (a GitHub App token lasts an hour) is picked up, and never appears in
results or errors.
"""

from __future__ import annotations

import asyncio
import hashlib
import http.client
import json
import threading
import time
import urllib.error
import urllib.request
from urllib.parse import quote, urlencode, urlsplit

from . import __version__
from .config import GitHubConfig
from .errors import TokenUnavailable
from .operations import AmbiguousOutcome
from .redact import redact


class GitHubAmbiguous(AmbiguousOutcome):
    pass


class GitHubReadLimit(Exception):
    """A bounded read cannot establish a complete provider response."""


class _NoReadRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Repository moves require a newly adopted binding, never token forwarding.
        return None


def _rate_limited(headers, payload) -> bool:
    """GitHub answers an exhausted primary or a secondary rate limit with 403 as well as 429."""
    return (headers.get("x-ratelimit-remaining") == "0" or headers.get("retry-after") is not None
            or "rate limit" in str(payload.get("message") if isinstance(payload, dict) else "").lower())


class GitHubClient:
    def __init__(self, cfg: GitHubConfig, token: str | None = None) -> None:
        self.cfg = cfg
        self._fixed_token = token
        # A missing token disables delivery at startup (TokenUnavailable). Later requests resolve it again.
        self._last_token = token if token is not None else cfg.token()
        host = urlsplit(cfg.api_url).hostname or ""
        # A loopback test server must not go through the egress proxy; real GitHub uses the environment's proxy.
        handlers = [urllib.request.ProxyHandler({})] if host in {"127.0.0.1", "localhost"} else []
        self._opener = urllib.request.build_opener(*handlers)
        self._read_opener = urllib.request.build_opener(*handlers, _NoReadRedirect())
        self._bounded_read_slot = threading.BoundedSemaphore(1)

    def _bounded_get(self, path, query, max_bytes, timeout_s):
        """Opt-in GET with byte/time bounds and metadata for object AND list pages.

        Existing delivery callers retain their response contract. This read path
        resolves current credentials without falling back to an earlier token.
        """
        if (not isinstance(path, str) or not path.startswith('/repos/') or '?' in path or '#' in path
                or type(max_bytes) is not int or not 1 <= max_bytes <= 2_000_000
                or type(timeout_s) not in (int, float) or not 0 < timeout_s <= 120):
            raise ValueError('Invalid bounded GitHub read')
        token = self._fixed_token if self._fixed_token is not None else self.cfg.token()
        url = self.cfg.api_url + path + ('?' + urlencode(query) if query else '')
        req = urllib.request.Request(url, method='GET', headers={  # noqa: S310 - trusted configured API
            'Accept': 'application/vnd.github+json', 'X-GitHub-Api-Version': self.cfg.api_version,
            'Authorization': 'Bearer ' + token, 'User-Agent': f'bat-agent-connector/{__version__}'})
        deadline = time.monotonic() + timeout_s
        try:
            try:
                response = self._read_opener.open(req, timeout=min(self.cfg.timeout_s, timeout_s))  # noqa: S310
            except urllib.error.HTTPError as exc:
                response = exc
            with response:
                status, chunks, size = response.code, [], 0
                link = response.headers.get('Link', '')
                if len(link.encode()) > 16_384:
                    raise GitHubReadLimit('GitHub pagination header exceeds the byte limit')
                while True:
                    if time.monotonic() >= deadline:
                        raise GitHubReadLimit('GitHub read exceeded its time limit')
                    sock = getattr(getattr(getattr(response, 'fp', None), 'raw', None), '_sock', None)
                    if sock is not None:
                        sock.settimeout(min(self.cfg.timeout_s, max(0.001, deadline - time.monotonic())))
                    # read1 returns available bytes: a slow stream cannot extend
                    # the read indefinitely by sending one byte per socket timeout.
                    read = getattr(response, 'read1', response.read)
                    chunk = read(min(65_536, max_bytes + 1 - size))
                    size += len(chunk)
                    if size > max_bytes:
                        raise GitHubReadLimit('GitHub response exceeds the byte limit')
                    if not chunk:
                        break
                    chunks.append(chunk)
                if time.monotonic() >= deadline:
                    raise GitHubReadLimit('GitHub read exceeded its time limit')
                raw = b''.join(chunks)
                length = response.headers.get('Content-Length')
                if length is not None and (not length.isdigit() or int(length) != len(raw)):
                    raise GitHubReadLimit('GitHub response body is incomplete')
                payload = json.loads(raw) if raw else None
                if status == 429 or status >= 500 or status == 403 and _rate_limited(response.headers, payload):
                    raise GitHubAmbiguous('GitHub bounded read is temporarily unavailable')
                return {'status': status, 'payload': payload, 'metadata': {'link': link,
                    'bytes': len(raw), 'body_sha256': hashlib.sha256(raw).hexdigest(), 'authenticated': True}}
        except (urllib.error.URLError, TimeoutError, OSError, http.client.HTTPException, ValueError):
            raise GitHubAmbiguous('GitHub bounded read did not establish a complete JSON response') from None

    async def bounded_get(self, path, *, query=None, max_bytes=512_000, timeout_s=30):
        # DNS and response headers precede the body deadline loop. Bound caller
        # completion too; at most ONE timed-out read worker may survive per client.
        # Late bytes can never become the response of a subsequent observation.
        if type(timeout_s) not in (int, float) or not 0 < timeout_s <= 120:
            raise ValueError('Invalid bounded GitHub read timeout')
        if not self._bounded_read_slot.acquire(blocking=False):
            raise GitHubReadLimit('An earlier bounded GitHub read is still finishing')
        abandoned = threading.Event()

        def read():
            try:
                if abandoned.is_set():
                    raise GitHubReadLimit('GitHub read was abandoned before starting')
                return self._bounded_get(path, query, max_bytes, timeout_s)
            finally:
                self._bounded_read_slot.release()

        task = asyncio.create_task(asyncio.to_thread(read))
        # Retrieve a late exception after the caller has already timed out.
        task.add_done_callback(lambda done: None if done.cancelled() else done.exception())
        try:
            return await asyncio.wait_for(asyncio.shield(task), timeout_s)
        except asyncio.TimeoutError:
            abandoned.set()
            raise GitHubReadLimit('GitHub read exceeded its total time limit') from None
        except asyncio.CancelledError:
            abandoned.set()
            raise

    def _request(self, method: str, path: str, body: dict | None = None, query: dict | None = None) -> tuple[int, dict]:
        url = self.cfg.api_url + path + (("?" + urlencode({k: v for k, v in query.items() if v is not None}))
                                         if query else "")
        data = json.dumps(body).encode() if body is not None else None
        token = self._fixed_token
        if token is None:
            try:
                token = self._last_token = self.cfg.token()
            except TokenUnavailable:
                # Mid-rotation: send with the last good token so GitHub's answer decides. Raising here would record
                # a write that was never sent as uncertain, and a dispatch read-back can never prove it unsent.
                token = self._last_token
        req = urllib.request.Request(url, data=data, method=method, headers={  # noqa: S310 - https or loopback
            "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": self.cfg.api_version,
            "Authorization": "Bearer " + token, "User-Agent": f"bat-agent-connector/{__version__}",
            **({"Content-Type": "application/json"} if data is not None else {})})
        try:
            with self._opener.open(req, timeout=self.cfg.timeout_s) as resp:  # noqa: S310
                status, raw = resp.status, resp.read()
        except urllib.error.HTTPError as e:
            try:
                raw = e.read()
            except (http.client.HTTPException, OSError):
                raw = b""
            try:
                payload = json.loads(raw) if raw else {}
            except ValueError:
                payload = {"message": raw[:200].decode(errors="replace")}
            # A rate-limited read is retried later; a write refused for its rate limit keeps its 403 (not sent).
            rate_limited_read = e.code == 403 and method == "GET" and _rate_limited(e.headers, payload)
            if e.code == 429 and method == "POST" and path.endswith("/dispatches"):
                return 429, {"retry_after": e.headers.get("Retry-After", "30")}
            if e.code == 429 or e.code >= 500 or rate_limited_read:
                raise GitHubAmbiguous(f"GitHub {method} {path} answered {e.code}") from None
            out = payload.copy() if isinstance(payload, dict) else {"items": payload}
            if isinstance(payload, list) and "Link" in e.headers:
                out["_link"] = e.headers["Link"]
            return e.code, out
        except (urllib.error.URLError, TimeoutError, OSError, http.client.HTTPException) as e:
            # HTTPException covers a body cut short (IncompleteRead) after GitHub may already have acted.
            raise GitHubAmbiguous(redact(f"GitHub {method} {path} failed: {type(e).__name__}")) from None
        try:
            payload = json.loads(raw) if raw else {}
            if isinstance(payload, list):
                out = {"items": payload}
            else:
                out = payload.copy() if isinstance(payload, dict) else {"items": payload}
            if isinstance(payload, list) and "Link" in getattr(resp, "headers", {}):
                out["_link"] = resp.headers["Link"]
            return status, out
        except ValueError:
            raise GitHubAmbiguous(f"GitHub {method} {path} answered {status} with an unreadable body") from None

    async def call(self, method: str, path: str, body: dict | None = None, query: dict | None = None):
        return await asyncio.to_thread(self._request, method, path, body, query)

    @staticmethod
    def _repo(repository: str) -> str:
        owner, name = repository.split("/", 1)
        return f"/repos/{quote(owner)}/{quote(name)}"

    # ------------------------------------------------------------------ pulls
    async def pull(self, repository: str, number: int):
        return await self.call("GET", f"{self._repo(repository)}/pulls/{int(number)}")

    async def repository(self, repository: str):
        return await self.call("GET", self._repo(repository))

    async def branch_ref(self, repository: str, branch: str):
        return await self.call("GET", f"{self._repo(repository)}/git/ref/heads/{quote(branch, safe='')}")

    async def update_pull(self, repository: str, number: int, fields: dict):
        return await self.call("PATCH", f"{self._repo(repository)}/pulls/{int(number)}", fields)

    async def pulls(self, repository: str, *, page: int = 1, state: str = "open",
                    sort: str | None = None, direction: str | None = None):
        return await self.call("GET", f"{self._repo(repository)}/pulls",
                               query={"per_page": 100, "page": page, "state": state,
                                      **({"sort": sort} if sort else {}), **({"direction": direction} if direction else {})})

    async def stacks(self, repository: str, number: int, *, page: int = 1):
        return await self.call("GET", f"{self._repo(repository)}/stacks",
                               query={"pull_request": number, "per_page": 100, "page": page})

    async def stack(self, repository: str, number: int):
        return await self.call("GET", f"{self._repo(repository)}/stacks/{int(number)}")

    async def compare(self, repository: str, base: str, head: str, *, page: int = 1):
        return await self.call("GET", f"{self._repo(repository)}/compare/{quote(base, safe='')}..."
                               f"{quote(head, safe='')}", query={"per_page": 100, "page": page})

    async def merge_async(self, repository: str, number: int, sha: str, method: str):
        return await self.call("PUT", f"{self._repo(repository)}/pulls/{int(number)}/merge-async",
                               {"sha": sha, "merge_method": method, "merge_action": "default"})

    async def merge_async_result(self, repository: str, number: int, uuid: str):
        return await self.call("GET", f"{self._repo(repository)}/pulls/{int(number)}/merge-async/{quote(uuid)}")

    async def commit(self, repository: str, sha: str):
        return await self.call("GET", f"{self._repo(repository)}/commits/{quote(sha, safe='')}")

    async def check_runs(self, repository: str, sha: str):
        return await self.call("GET", f"{self._repo(repository)}/commits/{quote(sha)}/check-runs",
                               query={"per_page": 100})

    # ------------------------------------------------------------------ actions
    async def dispatch(self, repository: str, workflow: str, ref: str, inputs: dict):
        return await self.call("POST", f"{self._repo(repository)}/actions/workflows/{quote(workflow)}/dispatches",
                               {"ref": ref, "inputs": inputs})

    async def runs(self, repository: str, workflow: str, **filters):
        return await self.call("GET", f"{self._repo(repository)}/actions/workflows/{quote(workflow)}/runs",
                               query={"per_page": 50, **filters})

    async def run(self, repository: str, run_id: int):
        return await self.call("GET", f"{self._repo(repository)}/actions/runs/{int(run_id)}")

    async def jobs(self, repository: str, run_id: int):
        return await self.call("GET", f"{self._repo(repository)}/actions/runs/{int(run_id)}/jobs",
                               query={"per_page": 100})

    async def workflow(self, repository: str, workflow: str):
        return await self.call("GET", f"{self._repo(repository)}/actions/workflows/{quote(workflow)}")

    async def attempt_jobs(self, repository: str, run_id: int, attempt: int, *, page: int = 1):
        return await self.call("GET", f"{self._repo(repository)}/actions/runs/{int(run_id)}/attempts/"
                               f"{int(attempt)}/jobs", query={"per_page": 100, "page": page})

    async def pending_deployments(self, repository: str, run_id: int):
        return await self.call("GET", f"{self._repo(repository)}/actions/runs/{int(run_id)}/pending_deployments")

    async def artifact(self, repository: str, artifact_id: int):
        return await self.call("GET", f"{self._repo(repository)}/actions/artifacts/{int(artifact_id)}")
