"""Bounded original POST transport; only task-owned loopback HTTP is used."""
from __future__ import annotations

import asyncio
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from bat_agent_connector.config import GitHubConfig
from bat_agent_connector.github import GitHubAmbiguous, GitHubClient


@pytest.fixture
def provider():
    state = {"status": 201, "raw": b'{"id":801}', "requests": [], "headers": {}}
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = self.rfile.read(int(self.headers["Content-Length"]))
            state["requests"].append((self.path, json.loads(body)))
            self.send_response(state["status"])
            for key, value in state["headers"].items():
                self.send_header(key, value)
            if "Content-Length" not in state["headers"]:
                self.send_header("Content-Length", str(len(state["raw"])))
            self.end_headers()
            self.wfile.write(state["raw"])

        def log_message(self, *_args):
            pass
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    client = GitHubClient(GitHubConfig(api_url=f"http://127.0.0.1:{server.server_port}"), token="fixture-only")
    try:
        yield client, state
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


async def send(client, **kwargs):
    return await client.create_pull("example/project", title="Selected title", body="Selected body",
        head="batc/pr-" + "a" * 32, base="main", draft=True, **kwargs)


async def test_original_201_posts_only_selected_fields(provider):
    client, state = provider
    assert await send(client) == (201, {"id": 801})
    assert state["requests"] == [("/repos/example/project/pulls", {"title": "Selected title", "body": "Selected body",
        "head": "batc/pr-" + "a" * 32, "base": "main", "draft": True, "maintainer_can_modify": False})]


@pytest.mark.parametrize("raw,status", [(b"x" * 256_001, 201), (b'{"id":', 201), (b'{}', 503), (b'{}', 429)])
async def test_unreadable_large_or_ambiguous_answer_is_not_retried(provider, raw, status):
    client, state = provider
    state.update(raw=raw, status=status)
    with pytest.raises(GitHubAmbiguous):
        await send(client)
    assert len(state["requests"]) == 1


async def test_redirect_is_not_followed_and_truncated_body_is_unknown(provider):
    client, state = provider
    state.update(status=307, headers={"Location": client.cfg.api_url + "/redirected"})
    assert (await send(client))[0] == 307
    assert len(state["requests"]) == 1
    state.update(status=201, headers={"Content-Length": "500"})
    with pytest.raises(GitHubAmbiguous):
        await send(client)
    assert len(state["requests"]) == 2


async def test_delayed_headers_bound_completion_and_outstanding_workers(provider, monkeypatch):
    client, state = provider
    release, started = threading.Event(), threading.Event()
    original = client._read_opener.open
    calls = []
    def delayed(*args, **kwargs):
        calls.append(True)
        started.set()
        release.wait(2)
        return original(*args, **kwargs)
    monkeypatch.setattr(client._read_opener, "open", delayed)
    began = time.monotonic()
    try:
        with pytest.raises(GitHubAmbiguous, match="total time"):
            await send(client, timeout_s=0.05)
        assert started.is_set() and time.monotonic() - began < 0.5
        for _ in range(3):
            with pytest.raises(GitHubAmbiguous, match="still finishing"):
                await send(client, timeout_s=0.05)
        assert len(calls) == 1
    finally:
        release.set()
        for _ in range(100):
            if client._bounded_create_slot.acquire(blocking=False):
                client._bounded_create_slot.release()
                break
            await asyncio.sleep(0.01)
    # The original request might have reached the provider late. No caller
    # receives its 201 and no automatic retry was submitted.
    assert len(calls) == 1 and len(state["requests"]) <= 1
