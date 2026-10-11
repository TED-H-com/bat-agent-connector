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
import http.client
import json
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

    # Issue publication only creates objects; it never overwrites another
    # author's mutable title/body or treats an ETag GET as a PATCH precondition.
    async def issue(self, repository: str, number: int):
        return await self.call("GET", f"{self._repo(repository)}/issues/{int(number)}")

    async def issues(self, repository: str, *, page: int = 1):
        return await self.call("GET", f"{self._repo(repository)}/issues",
                               query={"state": "all", "sort": "created", "direction": "asc", "per_page": 100, "page": page})

    async def create_issue(self, repository: str, *, title: str, body: str):
        return await self.call("POST", f"{self._repo(repository)}/issues", {"title": title, "body": body})

    async def issue_comments(self, repository: str, number: int, *, page: int = 1):
        return await self.call("GET", f"{self._repo(repository)}/issues/{int(number)}/comments",
                               query={"per_page": 100, "page": page})

    async def create_issue_comment(self, repository: str, number: int, *, body: str):
        return await self.call("POST", f"{self._repo(repository)}/issues/{int(number)}/comments", {"body": body})

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
