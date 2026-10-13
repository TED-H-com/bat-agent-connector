"""Offline source fixtures: no CLI processes, credentials, live hosts or model calls."""
from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import threading
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from bat_agent_connector import managed_setup, mcp_server, pm_reads, pm_resources, usage_collector
from bat_agent_connector.errors import ConfigError
from bat_agent_connector.operations import OperationError
from bat_agent_connector.usage_config import UsageConfig, UsageSource, parse_usage
from tests.test_api_v1 import daemon, http, served, token  # noqa: F401
from tests.test_pm_api import OWNER, seed
from tests.test_pm_run_api import unpack

SESSION = "11111111-1111-4111-8111-111111111111"
POOL = "pool_" + "a" * 32


def clock():
    # Default reads have a millisecond, exclusive upper bound. These fixtures
    # represent already-observed usage even when collection and read finish in
    # the same millisecond; exact interval boundaries are checked separately.
    return (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(timespec="milliseconds")


def codex_usage(n=1, **extra):
    return {"timestamp": clock(), "type": "event_msg", "payload": {"type": "token_count",
        "info": {"total_token_usage": {"input_tokens": n * 100, "cached_input_tokens": n * 40,
            "output_tokens": n * 20, "reasoning_output_tokens": n * 10, "total_tokens": n * 120},
            "last_token_usage": {"input_tokens": 999999}}, **extra}}


def claude_usage(n=1, mid="msg_example", **extra):
    return {"type": "assistant", "timestamp": clock(), "sessionId": SESSION, "version": "2.1.0",
        "message": {"id": mid, "model": "example-claude", "content": [{"text": "PRIVATE TRANSCRIPT"}],
            "usage": {"input_tokens": n * 10, "cache_read_input_tokens": n * 30,
                      "cache_creation_input_tokens": n * 5, "output_tokens": n * 20, **extra}}}


def append(path, *records):
    with path.open("ab") as stream:
        for row in records:
            stream.write(json.dumps(row).encode() + b"\n")


@pytest.fixture
async def ready(daemon, tmp_path, monkeypatch):  # noqa: F811
    workspace, _ = await seed(daemon)
    wid = workspace["workspace_id"]
    source = UsageSource("coding", wid, "codex-rollout-v1", tmp_path / "coding.jsonl", SESSION,
                         "implementer", POOL)
    append(source.path, {"type": "session_meta", "payload": {"id": SESSION, "cli_version": "0.120.0",
        "cwd": "/private/source", "base_instructions": "PRIVATE INSTRUCTIONS"}},
        {"type": "turn_context", "payload": {"model": "requested-codex", "effort": "high"}})
    daemon.usage_collector.config = UsageConfig(10, (source,))
    monkeypatch.setattr(daemon.journal, "owner_valid", lambda: True)
    return daemon, wid, source


def read(d, wid, **params):
    return pm_reads.read(d.ops, OWNER, "pm_resources_get", {"workspace_id": wid, **params})


async def test_restart_replay_counter_inclusions_and_private_data(ready):
    d, wid, source = ready
    append(source.path, codex_usage(), codex_usage())
    await d.usage_collector.collect_once()
    first = read(d, wid)["rows"][0]
    assert first["observations"] == 1 and first["calls"] is None
    assert {k: first["metrics"][k]["value"] for k in ("input", "cache_read", "output", "reasoning", "total")} == {
        "input": 100, "cache_read": 40, "output": 20, "reasoning": 10, "total": 120}
    assert first["metrics"]["cache_write"]["value"] is None
    assert first["configurations"][0]["actual_model"] is None
    assert first["configurations"][0]["requested_model"] == "requested-codex"
    restarted = usage_collector.UsageCollector(d.journal, d.usage_collector.config)
    await restarted.collect_once()
    append(source.path, codex_usage(2))
    await restarted.collect_once()
    assert read(d, wid)["rows"][0]["metrics"]["total"]["value"] == 240
    assert read(d, wid)["rows"][0]["observations"] == 2
    dump = "\n".join(d.journal.db.iterdump())
    assert "PRIVATE" not in dump and "/private/source" not in dump and str(source.path) not in dump
    assert SESSION not in json.dumps(read(d, wid))


async def test_claude_message_dedup_refinement_and_model_history(ready):
    d, wid, source = ready
    source = replace(source, adapter="claude-transcript-v1", quota_pool=None)
    d.usage_collector.config = UsageConfig(10, (source,))
    source.path.write_text("")
    append(source.path, claude_usage(), claude_usage(), claude_usage(2))
    await d.usage_collector.collect_once()
    result = read(d, wid)["rows"][0]
    assert result["calls"] == 1 and result["metrics"]["total"]["value"] == 130
    next_message = claude_usage(mid="msg_next")
    next_message["message"]["model"] = "another-model"
    append(source.path, next_message)
    await d.usage_collector.collect_once()
    result = read(d, wid)["rows"][0]
    assert result["calls"] == 2 and len(result["configurations"]) == 2
    assert result["metrics"]["total"]["value"] == 195
    assert result["metrics"]["reasoning"]["value"] is None


async def test_partial_line_bounded_page_and_atomic_cursor(ready, monkeypatch):
    d, wid, source = ready
    await d.usage_collector.collect_once()
    raw = json.dumps(codex_usage()).encode()
    with source.path.open("ab") as stream:
        stream.write(raw[:30])
    await d.usage_collector.collect_once()
    assert read(d, wid)["rows"][0]["status"] == "catching_up"
    assert read(d, wid)["rows"][0]["observations"] == 0
    with source.path.open("ab") as stream:
        stream.write(raw[30:] + b"\n")
    append(source.path, codex_usage(2))
    monkeypatch.setattr(usage_collector, "PAGE_LINES", 1)
    await d.usage_collector.collect_once()
    assert read(d, wid)["rows"][0]["metrics"]["total"]["value"] == 120
    await d.usage_collector.collect_once()
    assert read(d, wid)["rows"][0]["metrics"]["total"]["value"] == 240


@pytest.mark.parametrize("problem", ["truncate", "rotate", "rewrite", "missing", "symlink", "regression", "malformed", "boolean", "synthetic", "identity"])
async def test_invalid_source_withdraws_counts_without_resetting_original_cursor(ready, problem):
    d, wid, source = ready
    append(source.path, codex_usage())
    await d.usage_collector.collect_once()
    old = dict(d.journal.db.execute("SELECT * FROM resource_usage_sources").fetchone())
    if problem == "truncate":
        source.path.write_text("")
    elif problem in {"rotate", "symlink"}:
        moved = source.path.with_suffix(".old")
        source.path.rename(moved)
        if problem == "symlink":
            source.path.symlink_to(moved)
        else:
            source.path.write_bytes(moved.read_bytes())
    elif problem == "rewrite":
        value = source.path.read_text().replace("0.120.0", "0.121.0")
        source.path.write_text(value)
    elif problem == "missing":
        source.path.unlink()
    elif problem == "malformed":
        with source.path.open("ab") as stream:
            stream.write(b"PRIVATE INVALID JSON\n")
    else:
        value = codex_usage(2)
        counters = value["payload"]["info"]["total_token_usage"]
        if problem == "regression":
            value = codex_usage(0)
        elif problem == "boolean":
            counters["input_tokens"] = True
        elif problem == "synthetic":
            counters["total_tokens"] = 100000
        else:
            value = {"type": "session_meta", "payload": {"id": "different"}}
        append(source.path, value)
    await d.usage_collector.collect_once()
    current = read(d, wid)["rows"][0]
    assert current["metrics"] is None and current["observations"] is None
    assert current["configurations"] == []
    assert d.journal.db.execute("SELECT cursor FROM resource_usage_sources").fetchone()[0] == old["cursor"]
    assert read(d, wid)["quota_pools"][0]["snapshot"] is None


async def test_withdraw_binding_membership_and_owner_loss(ready, monkeypatch):
    d, wid, source = ready
    append(source.path, codex_usage())
    monkeypatch.setattr(d.journal, "owner_valid", lambda: False)
    await d.usage_collector.collect_once()
    assert read(d, wid)["rows"][0]["status"] == "pending"
    monkeypatch.setattr(d.journal, "owner_valid", lambda: True)
    await d.usage_collector.collect_once()
    d.usage_collector.config = UsageConfig(10)
    assert read(d, wid)["rows"] == [] and read(d, wid)["quota_pools"] == []
    d.usage_collector.config = UsageConfig(10, (replace(source, role="reviewer"),))
    assert read(d, wid)["rows"][0]["status"] == "binding_changed"
    assert read(d, wid)["rows"][0]["metrics"] is None
    d.journal.db.execute("DELETE FROM pm_workspace_members WHERE workspace_id=?", (wid,))
    with pytest.raises(OperationError, match="NOT_FOUND"):
        read(d, wid)


async def test_loop_repeats_and_stops_without_reader_trigger(ready, monkeypatch):
    d, wid, source = ready
    reached = asyncio.Event()
    real = d.usage_collector.collect_once
    calls = 0

    async def poll():
        nonlocal calls
        append(source.path, codex_usage(calls + 1))
        await real()
        calls += 1
        if calls == 2:
            reached.set()

    monkeypatch.setattr(d.usage_collector, "collect_once", poll)
    d.usage_collector.config = UsageConfig(0.01, (source,))
    task = asyncio.create_task(d.usage_collector.loop())
    try:
        await asyncio.wait_for(reached.wait(), 2)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    assert calls == 2
    assert read(d, wid)["rows"][0]["metrics"]["total"]["value"] == 240


async def test_quota_pool_dedup_expiry_and_distinct_limit_identity(ready):
    d, wid, source = ready
    other = replace(source, source_id="review", path=source.path.with_name("review.jsonl"), session_id="22222222-2222-4222-8222-222222222222")
    append(other.path, {"type": "session_meta", "payload": {"id": other.session_id}})
    d.usage_collector.config = UsageConfig(10, (source, other))
    rate = {"limit_id": "codex", "primary": {"used_percent": 25, "window_minutes": 300,
            "resets_at": int(datetime.now(timezone.utc).timestamp()) + 1000}, "credits": {"balance": "PRIVATE ACCOUNT"}}
    event = codex_usage(rate_limits=rate)
    append(source.path, event)
    append(other.path, event)
    await d.usage_collector.collect_once()
    pools = read(d, wid)["quota_pools"]
    assert len(pools) == 1 and pools[0]["sources"] == ["coding", "review"]
    assert pools[0]["snapshot"]["windows"][0]["used_percent"] == 25
    assert "PRIVATE ACCOUNT" not in "\n".join(d.journal.db.iterdump())
    # Conflicting simultaneous reports must not be averaged or summed.
    conflicting = json.loads(json.dumps(pools[0]["snapshot"]))
    conflicting["windows"][0]["used_percent"] = 30
    d.journal.db.execute("UPDATE resource_usage_sources SET quota=? WHERE source_id='review'", (json.dumps(conflicting),))
    assert read(d, wid)["quota_pools"][0]["status"] == "conflicting"
    assert read(d, wid)["quota_pools"][0]["snapshot"] is None
    # A model-specific limit is not merged with the default limit.
    conflicting["limit_id"] = "codex-review"
    d.journal.db.execute("UPDATE resource_usage_sources SET quota=? WHERE source_id='review'", (json.dumps(conflicting),))
    assert len(read(d, wid)["quota_pools"]) == 2
    for row in d.journal.db.execute("SELECT source_id,quota FROM resource_usage_sources").fetchall():
        rate = json.loads(row["quota"])
        rate["observed_at"] = (datetime.now(timezone.utc) - timedelta(minutes=6)).isoformat()
        d.journal.db.execute("UPDATE resource_usage_sources SET quota=? WHERE source_id=?", (json.dumps(rate), row["source_id"]))
    assert all(p["snapshot"] is None for p in read(d, wid)["quota_pools"])


async def test_window_optional_fields_stale_and_schema_idempotence(ready):
    d, wid, source = ready
    version = d.journal.db.execute("PRAGMA user_version").fetchone()[0]
    usage_collector.schema(d.journal)
    assert d.journal.db.execute("PRAGMA user_version").fetchone()[0] == version
    first, second = codex_usage(), codex_usage(2)
    first["timestamp"], second["timestamp"] = "2026-10-01T10:00:00Z", "2026-10-02T10:00:00Z"
    append(source.path, first, second)
    await d.usage_collector.collect_once()
    result = read(d, wid, since="2026-10-02T00:00:00Z", until="2026-10-03T00:00:00Z")["rows"][0]
    assert result["metrics"]["total"]["value"] == 120 and result["observations"] == 1
    assert result["metrics"]["cache_write"]["covered"] == 0
    before_second = read(d, wid, since=first["timestamp"], until=second["timestamp"])["rows"][0]
    assert before_second["metrics"]["total"]["value"] == 120 and before_second["observations"] == 1
    at_second = read(d, wid, since=second["timestamp"], until="2026-10-02T10:00:00.001Z")["rows"][0]
    assert at_second["metrics"]["total"]["value"] == 120 and at_second["observations"] == 1
    assert read(d, wid, since="2026-10-03T00:00:00Z", until="2026-10-04T00:00:00Z")["rows"][0]["metrics"]["total"]["value"] is None
    d.journal.db.execute("UPDATE resource_usage_sources SET successful_at='2020-01-01T00:00:00+00:00'")
    assert read(d, wid)["rows"][0]["status"] == "stale"
    for params in [{"since": True}, {"since": "2026-01-01"}, {"since": "2027-01-01T00:00:00Z", "until": "2026-01-01T00:00:00Z"},
                   {"since": "2020-01-01T00:00:00Z", "until": "2026-01-01T00:00:00Z"}]:
        with pytest.raises(OperationError, match="INVALID_PARAMS"):
            read(d, wid, **params)


async def test_owner_lost_during_read_never_commits(ready, monkeypatch):
    d, wid, source = ready
    append(source.path, codex_usage())
    original = usage_collector.read_page

    def lose_owner(*args):
        result = original(*args)
        monkeypatch.setattr(d.journal, "owner_valid", lambda: False)
        return result

    monkeypatch.setattr(usage_collector, "read_page", lose_owner)
    await d.usage_collector.collect_once()
    assert read(d, wid)["rows"][0]["status"] == "pending"
    assert d.journal.db.execute("SELECT count(*) FROM resource_usage_samples").fetchone()[0] == 0


async def test_claude_conflicting_sample_withdraws_prior_projection(ready):
    d, wid, source = ready
    source = replace(source, adapter="claude-transcript-v1", quota_pool=None)
    source.path.write_text("")
    d.usage_collector.config = UsageConfig(10, (source,))
    append(source.path, claude_usage(2))
    await d.usage_collector.collect_once()
    append(source.path, claude_usage(1))
    await d.usage_collector.collect_once()
    assert read(d, wid)["rows"][0]["status"] == "conflicting_sample"
    assert read(d, wid)["rows"][0]["metrics"] is None


async def test_configuration_activation_revokes_pending_collector_read(ready, monkeypatch):
    d, wid, source = ready
    append(source.path, codex_usage())
    started, finish = threading.Event(), threading.Event()
    original = usage_collector.read_page

    def held(*args):
        result = original(*args)
        started.set()
        assert finish.wait(5)
        return result

    monkeypatch.setattr(usage_collector, "read_page", held)
    task = asyncio.create_task(d.usage_collector.collect_once())
    try:
        assert await asyncio.to_thread(started.wait, 2)
        managed_setup._activate(d, replace(d._config, usage=UsageConfig()), {}, "source-revocation")
        assert read(d, wid)["rows"] == []
    finally:
        finish.set()
        await task
    assert d.journal.db.execute("SELECT count(*) FROM resource_usage_samples").fetchone()[0] == 0


async def test_partial_database_write_rolls_back_samples_and_cursor_then_replays_once(ready):
    d, wid, source = ready
    append(source.path, codex_usage(), codex_usage(2))
    d.journal.db.execute("""CREATE TRIGGER fixture_interrupted_usage BEFORE INSERT ON resource_usage_samples
        WHEN (SELECT count(*) FROM resource_usage_samples)>0 BEGIN
        SELECT RAISE(ABORT, 'fixture interrupted transaction'); END""")
    with pytest.raises(sqlite3.IntegrityError, match="fixture interrupted"):
        await d.usage_collector.collect_once()
    assert d.journal.db.execute("SELECT count(*) FROM resource_usage_samples").fetchone()[0] == 0
    assert d.journal.db.execute("SELECT count(*) FROM resource_usage_sources").fetchone()[0] == 0
    assert read(d, wid)["rows"][0]["status"] == "pending"
    d.journal.db.execute("DROP TRIGGER fixture_interrupted_usage")
    await d.usage_collector.collect_once()
    await d.usage_collector.collect_once()
    result = read(d, wid)["rows"][0]
    assert result["observations"] == 2 and result["metrics"]["total"]["value"] == 240


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="POSIX file-type fixture")
async def test_non_regular_file_is_refused_before_open(ready):
    d, wid, source = ready
    source.path.unlink()
    os.mkfifo(source.path)
    await asyncio.wait_for(d.usage_collector.collect_once(), 2)
    assert read(d, wid)["rows"][0]["status"] == "unsafe_file"


async def test_read_entrypoint_parity_scope_and_no_collection(served, tmp_path, monkeypatch):  # noqa: F811
    d, port = served
    workspace, _ = await seed(d)
    wid = workspace["workspace_id"]
    auth = token(d, OWNER.actor, *OWNER.scopes)
    path = f"/api/v1/pm/workspaces/{wid}/resources"
    changes = d.journal.db.total_changes
    status, result = await http(port, "GET", path, tok=auth)
    assert status == 200 and result["rows"] == []
    assert result["capabilities"] == pm_resources.capabilities()
    rpc = await d.call_api("pm_resources_get", {"workspace_id": wid}, OWNER)
    assert rpc["rows"] == result["rows"] and d.journal.db.total_changes == changes
    assert (await http(port, "GET", path + "?path=/private", tok=auth))[0] == 422
    assert (await http(port, "GET", path + "?since=yesterday", tok=auth))[0] == 422
    assert (await http(port, "POST", path, tok=auth, body={}))[0] in (404, 405)
    assert (await http(port, "GET", path, tok=token(d, "other", "observe")))[0] == 404
    assert (await http(port, "GET", path, tok=token(d, OWNER.actor, "manage")))[0] == 403
    with pytest.raises(OperationError, match="INVALID_PARAMS"):
        pm_reads.read(d.ops, OWNER, "pm_resources_get", {"workspace_id": wid, "refresh": True})
    monkeypatch.setenv("BATC_TASK_URL", f"http://127.0.0.1:{port}/rpc")
    monkeypatch.setenv("BATC_API_TOKEN", auth)
    server, fleet = mcp_server.build_server(d.fleet.config, read_only=True, principal_only=True)
    try:
        assert "pm_resources_get" in {t.name for t in await server.list_tools()}
        payload = unpack(await server.call_tool("pm_resources_get", {"workspace_id": wid}))
        assert payload["rows"] == []
    finally:
        await fleet.close()


def test_config_defaults_duplicates_and_no_discovery(tmp_path):
    assert parse_usage({}).sources == ()
    entry = {"source_id": "test", "workspace_id": "ws_" + "1" * 32, "adapter": "codex-rollout-v1",
             "path": str(tmp_path / "session.jsonl"), "session_id": SESSION}
    assert parse_usage({"usage": {"sources": [entry]}}).sources[0].path == tmp_path / "session.jsonl"
    for data in [{"sources": [entry, entry]}, {"interval_s": True}, {"interval_s": float("nan")},
                 {"sources": [{**entry, "adapter": "any-cli"}]}, {"sources": [{**entry, "path": "/tmp/*.jsonl"}]},
                 {"sources": [{**entry, "quota_pool": "private@example.com"}]}, {"auto_discover": True}]:
        with pytest.raises(ConfigError):
            parse_usage({"usage": data})


def test_config_repr_hides_private_session_and_path(tmp_path):
    source = UsageSource("public-label", "ws_" + "1" * 32, "codex-rollout-v1", tmp_path / "secret.jsonl", SESSION)
    assert str(tmp_path) not in repr(source) and SESSION not in repr(source)
