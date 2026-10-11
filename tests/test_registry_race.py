"""The successor uniqueness check must survive independent processes."""

from __future__ import annotations

import multiprocessing as mp

import pytest

from bat_agent_connector import controller_authority, registry
from bat_agent_connector.errors import WriteRefused
from tests.conftest import adopt


def _reserve_successor(start, out, sid, authority):
    start.wait()
    with controller_authority.use(authority):
        existing = registry.reserve(
            "h1", {"session_id": sid, "failover_of": "old-claude", "worktree_path": "/srv/worktree"}, 10
        )
    out.put(existing.get("session_id") if existing else sid)


def test_simultaneous_successor_reservation(controller_context):
    adopt("old-claude", cwd="/srv/worktree")
    ctx = mp.get_context("spawn")
    start = ctx.Event()
    out = ctx.Queue()
    procs = [ctx.Process(target=_reserve_successor, args=(start, out, f"new-{i}", controller_context[1])) for i in range(2)]
    for proc in procs:
        proc.start()
    start.set()
    results = [out.get(timeout=10) for _ in procs]
    for proc in procs:
        proc.join(timeout=10)
        assert proc.exitcode == 0
    assert results[0] == results[1]
    successors = [
        e
        for e in registry.list_entries("h1")
        if e.get("failover_of") == "old-claude" and e.get("status") in ("starting", "active")
    ]
    assert len(successors) == 1


def test_second_source_cannot_fail_over_into_owned_worktree(controller_context):
    adopt("old-a", cwd="/srv/first")
    adopt("old-b", cwd="/srv/second")
    registry.reserve(
        "h1", {"session_id": "first", "failover_of": "old-a", "worktree_path": "/srv/worktree"}, 10
    )
    with pytest.raises(WriteRefused, match="owns this worktree"):
        registry.reserve(
            "h1", {"session_id": "second", "failover_of": "old-b", "worktree_path": "/srv/worktree"}, 10
        )
