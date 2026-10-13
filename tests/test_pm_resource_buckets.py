"""Daily resource buckets: stored samples only, no log reads or live providers."""
from __future__ import annotations

import json
from dataclasses import replace

import pytest

from bat_agent_connector import mcp_server, pm_reads, usage_collector
from bat_agent_connector.operations import OperationError
from bat_agent_connector.usage_config import UsageConfig, UsageSource
from tests.test_api_v1 import daemon, http, served, token  # noqa: F401
from tests.test_pm_api import OWNER, seed
from tests.test_pm_run_api import unpack
from tests.test_resource_usage import (
    POOL,
    SESSION,
    append,
    claude_usage,
    codex_usage,
    read,
    ready,  # noqa: F401
)

WINDOW = {"since": "2026-10-01T00:00:00Z", "until": "2026-10-04T00:00:00Z"}
HIDDEN = 777777


def stamp(row, at):
    row["timestamp"] = at
    return row


def days_of(result, source_id):
    series = result["buckets"]["series"]
    assert [item["source_id"] for item in series] == [row["source_id"] for row in result["rows"]
                                                      if row["metrics"] is not None]
    return {day["date"]: day for day in next(item["days"] for item in series if item["source_id"] == source_id)}


def test_daily_bucket_query_uses_the_source_time_index(ready):  # noqa: F811
    d, _wid, _source = ready
    plan = " ".join(str(tuple(row)) for row in d.journal.db.execute(
        "EXPLAIN QUERY PLAN SELECT observed_at FROM resource_usage_samples "
        "WHERE source_id=? AND observed_at>=? AND observed_at<?",
        ("coding", "2026-10-01T00:00:00.000+00:00", "2026-10-04T00:00:00.000+00:00")))
    assert "resource_usage_window" in plan


async def test_local_days_keep_inclusion_coverage_and_unknown_fields(ready):  # noqa: F811
    d, wid, source = ready
    claude = replace(source, source_id="review", adapter="claude-transcript-v1", quota_pool=None,
                     path=source.path.with_name("review.jsonl"))
    partial = stamp(claude_usage(1, mid="msg_partial"), "2026-10-02T12:00:00Z")
    del partial["message"]["usage"]["cache_creation_input_tokens"]
    append(source.path, stamp(codex_usage(1), "2026-10-01T15:00:00Z"), stamp(codex_usage(2), "2026-10-01T16:30:00Z"))
    append(claude.path, partial)
    d.usage_collector.config = UsageConfig(10, (source, claude))
    await d.usage_collector.collect_once()
    for source_id in ("coding", "review"):
        assert d.journal.db.execute("SELECT status FROM resource_usage_sources WHERE source_id=?",
                                    (source_id,)).fetchone()[0] == "current"
    d.journal.db.execute(
        "INSERT INTO resource_usage_samples VALUES(?,?,?,?)",
        ("elsewhere", "hidden", "2026-10-01T12:00:00.000+00:00", json.dumps({"metrics": {"input": HIDDEN}})))
    other = replace(source, source_id="elsewhere", workspace_id="ws_" + "2" * 32,
                    path=source.path.with_name("missing.jsonl"))
    d.usage_collector.config = UsageConfig(10, (source, claude, other))
    changes = d.journal.db.total_changes
    result = read(d, wid, **WINDOW)
    assert d.journal.db.total_changes == changes
    assert result["buckets"]["granularity"] == "day" and result["buckets"]["tz"] == "UTC"
    assert result["capabilities"]["version"] == 1
    assert result["capabilities"]["buckets"] == {"granularity": "day"}
    assert [item["cli"] for item in result["buckets"]["series"]] == ["codex", "claude"]
    assert str(source.path) not in json.dumps(result) and SESSION not in json.dumps(result)
    assert str(HIDDEN) not in json.dumps(result)
    codex = days_of(result, "coding")
    assert [day["complete"] for day in codex.values()] == [True, True, True]
    assert codex["2026-10-01"]["tokens"] == {
        "input": 200, "cache_read": 80, "cache_write": None, "output": 40, "reasoning": 20, "total": 240}
    assert codex["2026-10-02"]["tokens"] == dict.fromkeys(
        ("input", "cache_read", "cache_write", "output", "reasoning", "total"), 0)
    assert codex["2026-10-03"]["tokens"] == codex["2026-10-02"]["tokens"]
    row = next(item for item in result["rows"] if item["source_id"] == "coding")
    assert row["metrics"]["input"]["value"] == 200 and row["metrics"]["total"]["value"] == 240
    assert row["metrics"]["cache_write"]["value"] is None and row["calls"] is None
    claude_days = days_of(result, "review")
    assert claude_days["2026-10-02"]["complete"] is True
    assert claude_days["2026-10-02"]["tokens"] == {
        "input": 10, "cache_read": 30, "cache_write": None, "output": 20, "reasoning": None, "total": None}
    assert result["rows"][1]["calls"] == 1
    # The same instants cross a local midnight in Taipei; clipped edge days stay unknown.
    taipei = read(d, wid, **WINDOW, tz="Asia/Taipei")
    assert taipei["buckets"]["tz"] == "Asia/Taipei"
    local = days_of(taipei, "coding")
    assert list(local) == ["2026-10-01", "2026-10-02", "2026-10-03", "2026-10-04"]
    assert local["2026-10-01"]["complete"] is False and local["2026-10-01"]["tokens"]["input"] == 100
    assert local["2026-10-02"]["complete"] is True and local["2026-10-02"]["tokens"]["total"] == 120
    assert local["2026-10-03"]["complete"] is True and local["2026-10-03"]["tokens"]["input"] == 0
    assert local["2026-10-04"]["complete"] is False and local["2026-10-04"]["tokens"]["input"] is None
    # Deleting the log cannot change a projection that only reads stored samples.
    source.path.unlink()
    claude.path.unlink()
    assert read(d, wid, **WINDOW)["buckets"] == result["buckets"]


async def test_dst_day_is_one_bucket_and_partial_windows_stay_incomplete(ready):  # noqa: F811
    d, wid, source = ready
    append(source.path, stamp(codex_usage(1), "2026-03-08T06:30:00Z"), stamp(codex_usage(2), "2026-03-08T07:30:00Z"))
    await d.usage_collector.collect_once()
    result = read(d, wid, since="2026-03-08T05:00:00Z", until="2026-03-09T04:00:00Z", tz="America/New_York")
    days = days_of(result, "coding")
    assert list(days) == ["2026-03-08"]
    assert days["2026-03-08"]["complete"] is True
    assert days["2026-03-08"]["tokens"]["input"] == 200
    assert days["2026-03-08"]["tokens"]["cache_write"] is None
    clipped = read(d, wid, since="2026-03-08T06:00:00Z", until="2026-03-08T08:00:00Z", tz="America/New_York")
    edge = days_of(clipped, "coding")["2026-03-08"]
    assert edge["complete"] is False and edge["tokens"]["input"] == 200


async def test_catching_up_and_mid_day_reads_do_not_invent_zero_days(ready, monkeypatch):  # noqa: F811
    d, wid, source = ready
    append(source.path, stamp(codex_usage(1), "2026-10-01T15:00:00Z"), stamp(codex_usage(2), "2026-10-02T15:00:00Z"))
    monkeypatch.setattr(usage_collector, "PAGE_LINES", 1)
    for _ in range(3):
        await d.usage_collector.collect_once()
    early = read(d, wid, **WINDOW)
    assert early["rows"][0]["status"] == "catching_up"
    early_days = days_of(early, "coding")
    assert early_days["2026-10-01"]["complete"] is False
    assert early_days["2026-10-01"]["tokens"]["input"] == 100
    assert early_days["2026-10-01"]["tokens"]["cache_read"] == 40
    assert early_days["2026-10-02"]["tokens"]["total"] is None
    assert early_days["2026-10-03"]["complete"] is False and early_days["2026-10-03"]["tokens"]["input"] is None
    await d.usage_collector.collect_once()
    done = read(d, wid, **WINDOW)
    assert done["rows"][0]["status"] == "current"
    done_days = days_of(done, "coding")
    assert done_days["2026-10-01"]["tokens"]["input"] == 100
    assert done_days["2026-10-02"]["tokens"]["input"] == 100 and done_days["2026-10-02"]["complete"] is True
    assert done_days["2026-10-03"]["complete"] is True and done_days["2026-10-03"]["tokens"]["output"] == 0
    # A full read that stopped during Oct 1 has observed tokens but not a finished day.
    d.journal.db.execute("UPDATE resource_usage_sources SET status='current', successful_at=? WHERE source_id=?",
                         ("2026-10-01T18:00:00.000+00:00", "coding"))
    mid = days_of(read(d, wid, **WINDOW), "coding")
    assert mid["2026-10-01"]["complete"] is False and mid["2026-10-01"]["tokens"]["input"] == 100
    assert mid["2026-10-02"]["tokens"]["input"] == 100 and mid["2026-10-02"]["complete"] is False
    assert mid["2026-10-03"]["tokens"]["input"] is None


async def test_withdrawn_sources_drop_buckets_with_totals(ready):  # noqa: F811
    d, wid, source = ready
    other = replace(source, source_id="review", path=source.path.with_name("review.jsonl"),
                    session_id="22222222-2222-4222-8222-222222222222")
    append(source.path, stamp(codex_usage(1), "2026-10-01T12:00:00Z"))
    append(other.path, {"type": "session_meta", "payload": {"id": other.session_id, "cli_version": "0.120.0"}},
           {"type": "turn_context", "payload": {"model": "requested-codex", "effort": "low"}},
           stamp(codex_usage(1), "2026-10-02T12:00:00Z"))
    d.usage_collector.config = UsageConfig(10, (source, other))
    await d.usage_collector.collect_once()
    assert {item["source_id"] for item in read(d, wid, **WINDOW)["buckets"]["series"]} == {"coding", "review"}
    d.usage_collector.config = UsageConfig(10, (source, replace(other, role="reviewer")))
    changed = read(d, wid, **WINDOW)
    assert changed["rows"][1]["status"] == "binding_changed" and changed["rows"][1]["metrics"] is None
    assert [item["source_id"] for item in changed["buckets"]["series"]] == ["coding"]
    assert "review" not in {item["source_id"] for item in changed["buckets"]["series"]}
    d.journal.db.execute("UPDATE resource_usage_sources SET status='source_unavailable' WHERE source_id='coding'")
    unavailable = read(d, wid, **WINDOW)
    assert unavailable["rows"][0]["metrics"] is None and unavailable["buckets"]["series"] == []
    d.usage_collector.config = UsageConfig(10)
    removed = read(d, wid, **WINDOW)
    assert removed["rows"] == [] and removed["buckets"]["series"] == [] and removed["quota_pools"] == []
    d.journal.db.execute("DELETE FROM pm_workspace_members WHERE workspace_id=?", (wid,))
    with pytest.raises(OperationError, match="NOT_FOUND"):
        read(d, wid)


def test_empty_workspace_invalid_zone_and_day_bound(ready):  # noqa: F811
    d, wid, _source = ready
    d.usage_collector.config = UsageConfig(10)
    empty = read(d, wid)
    assert empty["rows"] == [] and empty["buckets"] == {"granularity": "day", "tz": "UTC", "series": []}
    allowed = read(d, wid, since="2026-01-01T00:00:00Z", until="2027-01-02T00:00:00Z")
    assert len(allowed["buckets"]["series"]) == 0
    # No configured source, so the bound is the refused window rather than a day list.
    with pytest.raises(OperationError, match="INVALID_PARAMS"):
        read(d, wid, since="2026-01-01T00:30:00Z", until="2027-01-02T00:30:00Z")
    for tz in ("Not/AZone", "UTC+8", "../UTC", "", 1, "America/New_York/Nope"):
        with pytest.raises(OperationError, match="IANA") as caught:
            read(d, wid, tz=tz)
        assert caught.value.status == 422 and caught.value.code == "INVALID_PARAMS"
    with pytest.raises(OperationError, match="INVALID_PARAMS"):
        read(d, wid, tz="UTC", since="yesterday")


async def test_http_and_mcp_accept_tz_and_hide_withdrawn_counts(served, tmp_path, monkeypatch):  # noqa: F811
    d, port = served
    workspace, _target = await seed(d)
    wid = workspace["workspace_id"]
    auth = token(d, OWNER.actor, *OWNER.scopes)
    path = f"/api/v1/pm/workspaces/{wid}/resources"
    status, caps = await http(port, "GET", "/api/v1/capabilities", tok=auth)
    assert status == 200 and caps["features"]["pm_resources"]["version"] == 1
    assert caps["features"]["pm_resources"]["buckets"] == {"granularity": "day"}
    status, empty = await http(port, "GET", path, tok=auth)
    assert status == 200 and empty["buckets"]["series"] == [] and empty["buckets"]["tz"] == "UTC"
    source_path = tmp_path / "coding.jsonl"
    source = UsageSource("coding", wid, "codex-rollout-v1", source_path, SESSION, "implementer", POOL)
    append(source_path, {"type": "session_meta", "payload": {"id": SESSION, "cli_version": "0.120.0"}},
           {"type": "turn_context", "payload": {"model": "requested-codex", "effort": "high"}},
           stamp(codex_usage(1), "2026-10-01T15:00:00Z"))
    d.usage_collector.config = UsageConfig(10, (source,))
    monkeypatch.setattr(d.journal, "owner_valid", lambda: True)
    await d.usage_collector.collect_once()
    changes = d.journal.db.total_changes
    status, body = await http(port, "GET", path + "?since=2026-10-01T00:00:00Z&until=2026-10-03T00:00:00Z&tz=Asia%2FTaipei", tok=auth)
    assert status == 200 and d.journal.db.total_changes == changes
    assert body["buckets"]["tz"] == "Asia/Taipei"
    assert body["buckets"]["series"][0]["cli"] == "codex"
    assert body["buckets"]["series"][0]["days"][0]["tokens"]["input"] == 100
    rpc = await d.call_api("pm_resources_get", {"workspace_id": wid, "since": "2026-10-01T00:00:00Z",
                                                "until": "2026-10-03T00:00:00Z", "tz": "Asia/Taipei"}, OWNER)
    assert rpc["buckets"] == body["buckets"] and rpc["rows"][0]["metrics"]["input"]["value"] == 100
    assert (await http(port, "GET", path + "?tz=Not/AZone", tok=auth))[0] == 422
    assert (await http(port, "GET", path + "?tz=", tok=auth))[0] == 422
    assert (await http(port, "GET", path + "?tz=UTC&tz=UTC", tok=auth))[0] == 422
    denied, leaked = await http(port, "GET", path, tok=token(d, "other", "observe"))
    assert denied == 404 and "100" not in json.dumps(leaked) and "buckets" not in json.dumps(leaked)
    forbidden, hidden = await http(port, "GET", path, tok=token(d, OWNER.actor, "manage"))
    assert forbidden == 403 and "series" not in json.dumps(hidden)
    monkeypatch.setenv("BATC_TASK_URL", f"http://127.0.0.1:{port}/rpc")
    monkeypatch.setenv("BATC_API_TOKEN", auth)
    server, fleet = mcp_server.build_server(d.fleet.config, read_only=True, principal_only=True)
    try:
        tools = {tool.name: tool.input_schema for tool in await server.list_tools()}
        assert {"workspace_id", "since", "until", "tz"} <= tools["pm_resources_get"]["properties"].keys()
        payload = unpack(await server.call_tool("pm_resources_get", {
            "workspace_id": wid, "since": "2026-10-01T00:00:00Z", "until": "2026-10-03T00:00:00Z", "tz": "UTC"}))
        assert payload["buckets"]["series"][0]["days"][0]["tokens"]["total"] == 120
        assert payload["buckets"]["series"][0]["days"][0]["complete"] is True
        with pytest.raises(Exception, match="IANA"):
            unpack(await server.call_tool("pm_resources_get", {"workspace_id": wid, "tz": "Not/AZone"}))
    finally:
        await fleet.close()
    # A non-member direct read still cannot see the collected total.
    with pytest.raises(OperationError, match="FORBIDDEN"):
        pm_reads.read(d.ops, type(OWNER)("owner", frozenset({"manage"})), "pm_resources_get", {"workspace_id": wid})
