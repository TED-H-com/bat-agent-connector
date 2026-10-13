"""Daily resource buckets: stored samples only, no log reads or live providers."""
from __future__ import annotations

import json
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from bat_agent_connector import mcp_server, pm_reads, pm_resources, usage_collector
from bat_agent_connector.operations import OperationError
from bat_agent_connector.usage_adapters import UsageInvalid
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


def daily(d, wid, **params):
    return read(d, wid, buckets="day", **params)


def cover(db, source_id, at):
    cursor = json.loads(db.execute("SELECT cursor FROM resource_usage_sources WHERE source_id=?",
                                   (source_id,)).fetchone()[0])
    cursor["coverage_at"] = at
    db.execute("UPDATE resource_usage_sources SET cursor=?, status='current', successful_at=? WHERE source_id=?",
               (json.dumps(cursor), at, source_id))


def days_of(result, source_id):
    series = result["buckets"]["series"]
    assert [item["source_id"] for item in series] == [row["source_id"] for row in result["rows"]
                                                      if row["metrics"] is not None]
    return {day["date"]: day for day in next(item["days"] for item in series if item["source_id"] == source_id)}


def test_daily_bucket_query_uses_the_source_time_index(ready):  # noqa: F811
    d, _wid, source = ready
    captured = []
    d.journal.db.set_trace_callback(captured.append)
    pm_resources._series(d.journal.db, source, "2026-10-01T00:00:00.000+00:00", "2026-10-04T00:00:00.000+00:00",
                         ZoneInfo("UTC"), [date(2026, 10, 1), date(2026, 10, 2), date(2026, 10, 3)],
                         "current", "2026-10-13T00:00:00.000+00:00", timedelta(minutes=15))
    d.journal.db.set_trace_callback(None)
    sql = next(item for item in captured if item.startswith("SELECT observed_at,"))
    args = (source.source_id, "2026-10-01T00:00:00.000+00:00", "2026-10-04T00:00:00.000+00:00")
    explained = d.journal.db.execute("EXPLAIN QUERY PLAN " + sql, args if "?" in sql else ())
    assert "resource_usage_window" in " ".join(str(tuple(row)) for row in explained)


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
    result = daily(d, wid, **WINDOW)
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
    taipei = daily(d, wid, **WINDOW, tz="Asia/Taipei")
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
    assert daily(d, wid, **WINDOW)["buckets"] == result["buckets"]


async def test_dst_day_is_one_bucket_and_partial_windows_stay_incomplete(ready):  # noqa: F811
    d, wid, source = ready
    append(source.path, stamp(codex_usage(1), "2026-03-08T06:30:00Z"), stamp(codex_usage(2), "2026-03-08T07:30:00Z"))
    await d.usage_collector.collect_once()
    result = daily(d, wid, since="2026-03-08T05:00:00Z", until="2026-03-09T04:00:00Z", tz="America/New_York")
    days = days_of(result, "coding")
    assert list(days) == ["2026-03-08"]
    assert days["2026-03-08"]["complete"] is True
    assert days["2026-03-08"]["tokens"]["input"] == 200
    assert days["2026-03-08"]["tokens"]["cache_write"] is None
    clipped = daily(d, wid, since="2026-03-08T06:00:00Z", until="2026-03-08T08:00:00Z", tz="America/New_York")
    edge = days_of(clipped, "coding")["2026-03-08"]
    assert edge["complete"] is False and edge["tokens"]["input"] == 200


async def test_catching_up_and_mid_day_reads_do_not_invent_zero_days(ready, monkeypatch):  # noqa: F811
    d, wid, source = ready
    append(source.path, stamp(codex_usage(1), "2026-10-01T15:00:00Z"), stamp(codex_usage(2), "2026-10-02T15:00:00Z"))
    monkeypatch.setattr(usage_collector, "PAGE_LINES", 1)
    for _ in range(3):
        await d.usage_collector.collect_once()
    early = daily(d, wid, **WINDOW)
    assert early["rows"][0]["status"] == "catching_up"
    early_days = days_of(early, "coding")
    assert early_days["2026-10-01"]["complete"] is False
    assert early_days["2026-10-01"]["tokens"]["input"] == 100
    assert early_days["2026-10-01"]["tokens"]["cache_read"] == 40
    assert early_days["2026-10-02"]["tokens"]["total"] is None
    assert early_days["2026-10-03"]["complete"] is False and early_days["2026-10-03"]["tokens"]["input"] is None
    await d.usage_collector.collect_once()
    done = daily(d, wid, **WINDOW)
    assert done["rows"][0]["status"] == "current"
    done_days = days_of(done, "coding")
    assert done_days["2026-10-01"]["tokens"]["input"] == 100
    assert done_days["2026-10-02"]["tokens"]["input"] == 100 and done_days["2026-10-02"]["complete"] is True
    assert done_days["2026-10-03"]["complete"] is True and done_days["2026-10-03"]["tokens"]["output"] == 0
    # A full read that started during Oct 1 has observed tokens but not a finished day.
    cover(d.journal.db, "coding", "2026-10-01T18:00:00.000+00:00")
    mid = days_of(daily(d, wid, **WINDOW), "coding")
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
    assert {item["source_id"] for item in daily(d, wid, **WINDOW)["buckets"]["series"]} == {"coding", "review"}
    d.usage_collector.config = UsageConfig(10, (source, replace(other, role="reviewer")))
    changed = daily(d, wid, **WINDOW)
    assert changed["rows"][1]["status"] == "binding_changed" and changed["rows"][1]["metrics"] is None
    assert [item["source_id"] for item in changed["buckets"]["series"]] == ["coding"]
    assert "review" not in {item["source_id"] for item in changed["buckets"]["series"]}
    d.journal.db.execute("UPDATE resource_usage_sources SET status='source_unavailable' WHERE source_id='coding'")
    unavailable = daily(d, wid, **WINDOW)
    assert unavailable["rows"][0]["metrics"] is None and unavailable["buckets"]["series"] == []
    d.usage_collector.config = UsageConfig(10)
    removed = daily(d, wid, **WINDOW)
    assert removed["rows"] == [] and removed["buckets"]["series"] == [] and removed["quota_pools"] == []
    d.journal.db.execute("DELETE FROM pm_workspace_members WHERE workspace_id=?", (wid,))
    with pytest.raises(OperationError, match="NOT_FOUND"):
        daily(d, wid)


def test_empty_workspace_invalid_zone_and_day_bound(ready):  # noqa: F811
    d, wid, _source = ready
    d.usage_collector.config = UsageConfig(10)
    plain = read(d, wid)
    assert plain["rows"] == [] and "buckets" not in plain
    empty = daily(d, wid)
    assert empty["buckets"] == {"granularity": "day", "tz": "UTC", "series": []}
    # A 366-day duration is accepted even when it touches 367 local dates.
    assert "buckets" not in read(d, wid, since="2026-01-01T00:30:00Z", until="2027-01-02T00:30:00Z")
    assert "buckets" not in read(d, wid, since="2026-01-01T00:00:00Z", until="2027-01-02T00:00:00Z")
    with pytest.raises(OperationError, match="92 local dates") as too_many:
        daily(d, wid, since="2026-01-01T00:00:00Z", until="2027-01-02T00:00:00Z")
    assert too_many.value.status == 422 and too_many.value.code == "INVALID_PARAMS"
    with pytest.raises(OperationError, match="366 days") as too_long:
        read(d, wid, since="2026-01-01T00:00:00Z", until="2027-01-03T00:00:00Z")
    assert too_long.value.code == "INVALID_PARAMS"
    for tz in ("Not/AZone", "UTC+8", "../UTC", "", 1, "America/New_York/Nope", "localtime", "posixrules",
               "Factory", "asia/taipei"):
        with pytest.raises(OperationError, match="IANA") as caught:
            daily(d, wid, tz=tz)
        assert caught.value.status == 422 and caught.value.code == "INVALID_PARAMS"
    with pytest.raises(OperationError, match="tz requires buckets=day") as bare_tz:
        read(d, wid, tz="UTC")
    assert bare_tz.value.code == "INVALID_PARAMS"
    with pytest.raises(OperationError, match="buckets must be day") as bad_buckets:
        read(d, wid, buckets="week")
    assert bad_buckets.value.code == "INVALID_PARAMS"
    with pytest.raises(OperationError, match="366 days"):
        daily(d, wid, tz="UTC", since="yesterday")


@pytest.mark.parametrize('span,message', [(93, '92 local dates'), (366, '92 local dates'),
                                         (367, '366 days'), (400, '366 days')])
def test_daily_bucket_duration_error_precedes_local_date_error(ready, span, message):  # noqa: F811
    d, wid, _source = ready
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    changes = d.journal.db.total_changes
    with pytest.raises(OperationError) as refused:
        daily(d, wid, since=start.isoformat(), until=(start + timedelta(days=span)).isoformat())
    assert (refused.value.status, refused.value.code) == (422, 'INVALID_PARAMS')
    assert message in refused.value.message
    assert d.journal.db.total_changes == changes


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
    assert status == 200 and "buckets" not in empty
    status, opted = await http(port, "GET", path + "?buckets=day", tok=auth)
    assert status == 200 and opted["buckets"]["series"] == [] and opted["buckets"]["tz"] == "UTC"
    source_path = tmp_path / "coding.jsonl"
    source = UsageSource("coding", wid, "codex-rollout-v1", source_path, SESSION, "implementer", POOL)
    append(source_path, {"type": "session_meta", "payload": {"id": SESSION, "cli_version": "0.120.0"}},
           {"type": "turn_context", "payload": {"model": "requested-codex", "effort": "high"}},
           stamp(codex_usage(1), "2026-10-01T15:00:00Z"))
    d.usage_collector.config = UsageConfig(10, (source,))
    monkeypatch.setattr(d.journal, "owner_valid", lambda: True)
    await d.usage_collector.collect_once()
    changes = d.journal.db.total_changes
    query = "?since=2026-10-01T00:00:00Z&until=2026-10-03T00:00:00Z&buckets=day&tz=Asia%2FTaipei"
    status, body = await http(port, "GET", path + query, tok=auth)
    assert status == 200 and d.journal.db.total_changes == changes
    assert body["buckets"]["tz"] == "Asia/Taipei"
    assert body["buckets"]["series"][0]["cli"] == "codex"
    assert body["buckets"]["series"][0]["days"][0]["tokens"]["input"] == 100
    rpc = await d.call_api("pm_resources_get", {"workspace_id": wid, "since": "2026-10-01T00:00:00Z",
                                                "until": "2026-10-03T00:00:00Z", "buckets": "day",
                                                "tz": "Asia/Taipei"}, OWNER)
    assert rpc["buckets"] == body["buckets"] and rpc["rows"][0]["metrics"]["input"]["value"] == 100
    # Empty and repeated selectors are rejected before the zone check. A real name reaches it.
    status, blank = await http(port, "GET", path + "?tz=", tok=auth)
    assert status == 422 and blank["error"]["code"] == "INVALID_REQUEST"
    status, blank_with_buckets = await http(port, "GET", path + "?buckets=day&tz=", tok=auth)
    assert status == 422 and blank_with_buckets["error"]["code"] == "INVALID_REQUEST"
    status, repeated = await http(port, "GET", path + "?tz=UTC&tz=UTC", tok=auth)
    assert status == 422 and repeated["error"]["code"] == "INVALID_REQUEST"
    status, shape = await http(port, "GET", path + "?buckets=", tok=auth)
    assert status == 422 and shape["error"]["code"] == "INVALID_REQUEST"
    status, unknown = await http(port, "GET", path + "?refresh=1", tok=auth)
    assert status == 422 and unknown["error"]["code"] == "INVALID_REQUEST"
    status, zone = await http(port, "GET", path + "?buckets=day&tz=Not/AZone", tok=auth)
    assert status == 422 and zone["error"] == {"code": "INVALID_PARAMS", "message": "tz must be an IANA time zone name"}
    status, week = await http(port, "GET", path + "?buckets=week", tok=auth)
    assert status == 422 and week["error"] == {"code": "INVALID_PARAMS", "message": "buckets must be day"}
    status, bare = await http(port, "GET", path + "?tz=UTC", tok=auth)
    assert status == 422 and bare["error"] == {"code": "INVALID_PARAMS", "message": "tz requires buckets=day"}
    with pytest.raises(OperationError, match="IANA") as rpc_zone:
        await d.call_api("pm_resources_get", {"workspace_id": wid, "buckets": "day", "tz": ""}, OWNER)
    assert rpc_zone.value.status == 422 and rpc_zone.value.code == "INVALID_PARAMS"
    with pytest.raises(OperationError, match="Unknown PM read parameters") as rpc_unknown:
        await d.call_api("pm_resources_get", {"workspace_id": wid, "refresh": "1"}, OWNER)
    assert rpc_unknown.value.code == "INVALID_PARAMS"
    # A representable UTC year is 200. A zone that leaves the datetime range is 422, never 500.
    status, year = await http(port, "GET", path + "?since=9999-12-30T00:00:00Z&until=9999-12-31T12:00:00Z", tok=auth)
    assert status == 200 and "buckets" not in year
    status, zoned = await http(
        port, "GET", path + "?buckets=day&since=9999-12-31T00:00:00Z&until=9999-12-31T20:00:00Z&tz=Asia%2FTaipei",
        tok=auth)
    assert status == 422 and zoned["error"]["code"] == "INVALID_PARAMS" and "366 days" in zoned["error"]["message"]
    status, bounded = await http(port, "GET", path + "?buckets=day&since=9999-12-30T00:00:00Z&until=9999-12-31T12:00:00Z",
                                  tok=auth)
    assert status == 200 and bounded["buckets"]["tz"] == "UTC"
    denied, leaked = await http(port, "GET", path, tok=token(d, "other", "observe"))
    assert denied == 404 and "100" not in json.dumps(leaked) and "buckets" not in json.dumps(leaked)
    forbidden, hidden = await http(port, "GET", path, tok=token(d, OWNER.actor, "manage"))
    assert forbidden == 403 and "series" not in json.dumps(hidden)
    monkeypatch.setenv("BATC_TASK_URL", f"http://127.0.0.1:{port}/rpc")
    monkeypatch.setenv("BATC_API_TOKEN", auth)
    server, fleet = mcp_server.build_server(d.fleet.config, read_only=True, principal_only=True)
    try:
        tools = {tool.name: tool.input_schema for tool in await server.list_tools()}
        assert {"workspace_id", "since", "until", "buckets", "tz"} <= tools["pm_resources_get"]["properties"].keys()
        seen = []
        real_request = mcp_server.task_request

        def spy(method, **params):
            if method == "pm_resources_get":
                seen.append(params)
            return real_request(method, **params)

        monkeypatch.setattr(mcp_server, "task_request", spy)
        plain = unpack(await server.call_tool("pm_resources_get", {"workspace_id": wid}))
        assert "buckets" not in plain and "buckets" not in seen[-1] and "tz" not in seen[-1]
        assert seen[-1]["since"] is None and seen[-1]["until"] is None
        payload = unpack(await server.call_tool("pm_resources_get", {
            "workspace_id": wid, "since": "2026-10-01T00:00:00Z", "until": "2026-10-03T00:00:00Z",
            "buckets": "day", "tz": "UTC"}))
        assert seen[-1]["buckets"] == "day" and seen[-1]["tz"] == "UTC"
        assert payload["buckets"]["series"][0]["days"][0]["tokens"]["total"] == 120
        assert payload["buckets"]["series"][0]["days"][0]["complete"] is True
        with pytest.raises(Exception, match="IANA"):
            unpack(await server.call_tool("pm_resources_get", {"workspace_id": wid, "buckets": "day", "tz": "Not/AZone"}))
    finally:
        await fleet.close()
    # A non-member direct read still cannot see the collected total.
    with pytest.raises(OperationError, match="FORBIDDEN"):
        pm_reads.read(d.ops, type(OWNER)("owner", frozenset({"manage"})), "pm_resources_get", {"workspace_id": wid})


async def test_claude_refinement_across_midnight_waits_out_the_settle_margin(ready, monkeypatch):  # noqa: F811
    """A later Claude fragment keeps the first timestamp, so the day is not final at midnight."""
    d, wid, source = ready
    claude = replace(source, source_id="review", adapter="claude-transcript-v1", quota_pool=None,
                     path=source.path.with_name("review.jsonl"))
    d.usage_collector.config = UsageConfig(10, (claude,))
    append(claude.path, stamp(claude_usage(1, mid="msg_span"), "2026-10-01T23:59:30Z"))
    monkeypatch.setattr(usage_collector, "now", lambda: "2026-10-02T00:00:10.000+00:00")
    await d.usage_collector.collect_once()
    first = days_of(daily(d, wid, **WINDOW), "review")
    assert first["2026-10-01"]["complete"] is False
    assert first["2026-10-01"]["tokens"]["output"] == 20
    append(claude.path, stamp(claude_usage(50, mid="msg_span"), "2026-10-02T00:00:40Z"))
    monkeypatch.setattr(usage_collector, "now", lambda: "2026-10-02T00:01:10.000+00:00")
    await d.usage_collector.collect_once()
    second = days_of(daily(d, wid, **WINDOW), "review")
    assert second["2026-10-01"]["complete"] is False
    assert second["2026-10-01"]["tokens"]["output"] == 1000
    assert second["2026-10-02"]["tokens"]["output"] is None
    # Exactly day end plus the 15-minute floor is the first settled read.
    cover(d.journal.db, "review", "2026-10-02T00:15:00.000+00:00")
    settled = days_of(daily(d, wid, **WINDOW), "review")["2026-10-01"]
    assert settled["complete"] is True and settled["tokens"]["output"] == 1000
    # A refinement that arrives after that margin still lands on the first fragment's day.
    append(claude.path, stamp(claude_usage(60, mid="msg_span"), "2026-10-02T00:20:00Z"))
    monkeypatch.setattr(usage_collector, "now", lambda: "2026-10-02T00:30:00.000+00:00")
    await d.usage_collector.collect_once()
    later = days_of(daily(d, wid, **WINDOW), "review")["2026-10-01"]
    assert later["complete"] is True and later["tokens"]["output"] == 1200


async def test_commit_time_race_does_not_call_an_unread_day_complete(ready, monkeypatch):  # noqa: F811
    d, wid, source = ready
    real = usage_collector.read_page

    def racing(src, cursor, parser):
        page = real(src, cursor, parser)
        append(src.path, stamp(codex_usage(1), "2026-10-01T23:59:59.990Z"))
        return page

    monkeypatch.setattr(usage_collector, "read_page", racing)
    monkeypatch.setattr(usage_collector, "now", lambda: "2026-10-02T00:00:00.050+00:00")
    await d.usage_collector.collect_once()
    stored = d.journal.db.execute("SELECT status, cursor FROM resource_usage_sources").fetchone()
    assert stored["status"] == "current"
    assert json.loads(stored["cursor"])["coverage_at"] == "2026-10-02T00:00:00.050+00:00"
    first = days_of(daily(d, wid, **WINDOW), "coding")["2026-10-01"]
    assert first["complete"] is False and first["tokens"]["input"] is None
    monkeypatch.setattr(usage_collector, "read_page", real)
    monkeypatch.setattr(usage_collector, "now", lambda: "2026-10-02T00:01:00.000+00:00")
    await d.usage_collector.collect_once()
    second = days_of(daily(d, wid, **WINDOW), "coding")["2026-10-01"]
    assert second["complete"] is False and second["tokens"]["input"] == 100


async def test_stale_projection_keeps_days_the_read_start_already_covered(ready):  # noqa: F811
    d, wid, source = ready
    append(source.path, stamp(codex_usage(1), "2026-10-01T10:00:00Z"))
    await d.usage_collector.collect_once()
    d.journal.db.execute("UPDATE resource_usage_sources SET successful_at=? WHERE source_id=?",
                         ("2026-10-01T00:00:00.000+00:00", "coding"))
    result = daily(d, wid, **WINDOW)
    assert result["rows"][0]["status"] == "stale"
    days = days_of(result, "coding")
    assert days["2026-10-01"]["complete"] is True and days["2026-10-01"]["tokens"]["input"] == 100
    assert days["2026-10-02"]["complete"] is True and days["2026-10-02"]["tokens"]["input"] == 0
    assert days["2026-10-03"]["complete"] is True and days["2026-10-03"]["tokens"]["input"] == 0
    cursor = json.loads(d.journal.db.execute("SELECT cursor FROM resource_usage_sources").fetchone()[0])
    del cursor["coverage_at"]
    d.journal.db.execute("UPDATE resource_usage_sources SET cursor=?, successful_at=?, status='current' WHERE source_id=?",
                         (json.dumps(cursor), usage_collector.now(), "coding"))
    uncovered = days_of(daily(d, wid, **WINDOW), "coding")
    assert uncovered["2026-10-01"]["complete"] is False and uncovered["2026-10-01"]["tokens"]["input"] == 100
    assert uncovered["2026-10-02"]["tokens"]["input"] is None


async def test_settle_margin_uses_the_larger_of_fifteen_minutes_and_three_intervals(ready):  # noqa: F811
    d, wid, source = ready
    await d.usage_collector.collect_once()
    assert pm_resources._settle(10) == timedelta(minutes=15)
    assert pm_resources._settle(1200) == timedelta(hours=1)
    d.usage_collector.config = UsageConfig(1200, (source,))
    cover(d.journal.db, "coding", "2026-10-02T00:59:00.000+00:00")
    early = days_of(daily(d, wid, **WINDOW), "coding")["2026-10-01"]
    assert early["complete"] is False and early["tokens"]["input"] is None
    cover(d.journal.db, "coding", "2026-10-02T01:00:00.000+00:00")
    settled = days_of(daily(d, wid, **WINDOW), "coding")["2026-10-01"]
    assert settled["complete"] is True and settled["tokens"]["input"] == 0


async def test_codex_cumulative_delta_across_midnight_is_not_double_counted(ready):  # noqa: F811
    d, wid, source = ready
    append(source.path, stamp(codex_usage(1), "2026-10-01T23:59:00Z"), stamp(codex_usage(3), "2026-10-02T00:01:00Z"),
           stamp(codex_usage(3), "2026-10-02T00:02:00Z"))
    await d.usage_collector.collect_once()
    await d.usage_collector.collect_once()
    result = daily(d, wid, **WINDOW)
    days = days_of(result, "coding")
    assert days["2026-10-01"]["tokens"]["input"] == 100
    assert days["2026-10-02"]["tokens"]["input"] == 200
    assert result["rows"][0]["metrics"]["input"]["value"] == 300


async def test_fall_back_day_is_one_bucket_and_midnight_is_start_inclusive(ready, monkeypatch):  # noqa: F811
    d, wid, source = ready
    monkeypatch.setattr(usage_collector, "now", lambda: "2026-11-03T00:00:00.000+00:00")
    append(source.path,
           stamp(codex_usage(1), "2026-11-01T04:00:00Z"),
           stamp(codex_usage(2), "2026-11-01T05:30:00Z"),
           stamp(codex_usage(3), "2026-11-01T06:30:00Z"),
           stamp(codex_usage(4), "2026-11-02T04:30:00Z"),
           stamp(codex_usage(5), "2026-11-02T05:00:00Z"))
    await d.usage_collector.collect_once()
    window = {"since": "2026-11-01T04:00:00Z", "until": "2026-11-02T05:00:00Z"}
    days = days_of(daily(d, wid, **window, tz="America/New_York"), "coding")
    assert list(days) == ["2026-11-01"]
    assert days["2026-11-01"]["complete"] is True and days["2026-11-01"]["tokens"]["input"] == 400
    taipei = days_of(daily(d, wid, **window, tz="Asia/Taipei"), "coding")
    assert list(taipei) == ["2026-11-01", "2026-11-02"]
    assert taipei["2026-11-01"]["tokens"]["input"] + taipei["2026-11-02"]["tokens"]["input"] == 400


async def test_repeated_azores_midnight_keeps_the_in_window_sample(ready, monkeypatch):  # noqa: F811
    d, wid, source = ready
    monkeypatch.setattr(usage_collector, "now", lambda: "2026-10-26T00:00:00.000+00:00")
    append(source.path, stamp(codex_usage(1), "2026-10-24T12:00:00Z"), stamp(codex_usage(2), "2026-10-25T00:30:00Z"))
    await d.usage_collector.collect_once()
    result = daily(d, wid, since="2026-10-24T00:00:00Z", until="2026-10-25T01:00:00Z", tz="Atlantic/Azores")
    days = days_of(result, "coding")
    local = datetime(2026, 10, 25, 0, 30, tzinfo=timezone.utc).astimezone(ZoneInfo("Atlantic/Azores"))
    assert local.date() == date(2026, 10, 25)
    assert list(days) == ["2026-10-24", "2026-10-25"]
    assert result["rows"][0]["metrics"]["input"]["value"] == 200
    assert days["2026-10-24"]["complete"] is True and days["2026-10-24"]["tokens"]["input"] == 100
    assert days["2026-10-25"]["complete"] is False and days["2026-10-25"]["tokens"]["input"] == 100


async def test_missing_santiago_midnight_does_not_add_a_trailing_day(ready):  # noqa: F811
    d, wid, source = ready
    append(source.path, stamp(codex_usage(1), "2026-09-05T12:00:00Z"))
    await d.usage_collector.collect_once()
    start_of_day = datetime(2026, 9, 6, tzinfo=ZoneInfo("America/Santiago")).astimezone(timezone.utc)
    result = daily(d, wid, since="2026-09-05T04:00:00Z", until=start_of_day.isoformat(), tz="America/Santiago")
    days = days_of(result, "coding")
    assert list(days) == ["2026-09-05"]
    assert days["2026-09-05"]["complete"] is True and days["2026-09-05"]["tokens"]["input"] == 100


def test_samples_follow_precomputed_timestamp_strings(ready):  # noqa: F811
    d, wid, _source = ready
    db = d.journal.db
    db.execute("INSERT INTO resource_usage_sources VALUES(?,?,?,?,?,?,?,?)",
               ("coding", d.usage_collector.config.sources[0].binding, json.dumps({"coverage_at": "2026-10-13T00:00:00.000+00:00"}),
                "{}", None, "2026-10-13T00:00:00.000+00:00", "2026-10-13T00:00:00.000+00:00", "current"))

    def put(sample_id, at, value):
        db.execute("INSERT INTO resource_usage_samples VALUES(?,?,?,?)",
                   ("coding", sample_id, at, json.dumps({"metrics": {"input": value, "output": 1, "total": 1}})))

    put("edge", "2026-10-01T23:59:59.999+00:00", 7)
    put("next", "2026-10-02T00:00:00.000+00:00", 11)
    put("unparsed", "2026-10-01T15:00:00.000+00:00/extra", 9)
    days = days_of(daily(d, wid, **WINDOW), "coding")
    assert days["2026-10-01"]["tokens"]["input"] == 16
    assert days["2026-10-02"]["tokens"]["input"] == 11
    plain_sql = []
    db.set_trace_callback(plain_sql.append)
    plain = read(d, wid, **WINDOW)
    db.set_trace_callback(None)
    assert "buckets" not in plain and plain["rows"][0]["metrics"]["input"]["value"] == 27
    assert not any(item.startswith("SELECT observed_at,") for item in plain_sql)


async def test_plain_read_keeps_a_366_day_window_that_touches_367_dates(ready):  # noqa: F811
    d, wid, source = ready
    append(source.path, stamp(codex_usage(1), "2026-01-01T15:00:00Z"), stamp(codex_usage(2), "2026-04-02T15:00:00Z"))
    await d.usage_collector.collect_once()
    long = {"since": "2026-01-01T12:00:00Z", "until": "2027-01-02T12:00:00Z"}
    traced = []
    d.journal.db.set_trace_callback(traced.append)
    plain = read(d, wid, **long)
    d.journal.db.set_trace_callback(None)
    assert "buckets" not in plain and plain["rows"][0]["metrics"]["input"]["value"] == 200
    assert not any(item.startswith("SELECT observed_at,") for item in traced)
    now = datetime.now(timezone.utc)
    assert "buckets" not in read(d, wid, since=(now - timedelta(days=366)).isoformat(), until=now.isoformat())
    with pytest.raises(OperationError, match="92 local dates") as many_dates:
        daily(d, wid, **long)
    assert many_dates.value.code == "INVALID_PARAMS" and "366" not in many_dates.value.message
    aligned = daily(d, wid, since="2026-01-01T00:00:00Z", until="2026-04-03T00:00:00Z")
    days = days_of(aligned, "coding")
    assert list(days)[0] == "2026-01-01" and list(days)[-1] == "2026-04-02" and len(days) == 92
    assert days["2026-01-01"]["tokens"]["input"] == 100 and days["2026-04-02"]["tokens"]["input"] == 100
    assert aligned["rows"][0]["metrics"]["input"]["value"] == 200
    with pytest.raises(OperationError, match="92 local dates"):
        daily(d, wid, since="2026-01-01T00:00:00Z", until="2026-04-04T00:00:00Z")


async def test_year_edge_overflow_is_a_window_error(ready):  # noqa: F811
    d, wid, source = ready
    plain = read(d, wid, since="0001-01-01T00:00:00Z", until="0001-01-02T00:00:00Z")
    assert "buckets" not in plain
    assert "buckets" not in read(d, wid, since="9999-12-30T00:00:00Z", until="9999-12-31T12:00:00Z")
    await d.usage_collector.collect_once()
    cover(d.journal.db, "coding", "9999-12-31T00:15:00.000+00:00")
    last = days_of(daily(d, wid, since="9999-12-30T00:00:00Z", until="9999-12-31T12:00:00Z"), "coding")
    assert list(last) == ["9999-12-30", "9999-12-31"]
    assert last["9999-12-30"]["complete"] is True and last["9999-12-30"]["tokens"]["input"] == 0
    assert last["9999-12-31"]["complete"] is False and last["9999-12-31"]["tokens"]["input"] is None
    for params in (
        {"since": "9999-12-31T00:00:00Z", "until": "9999-12-31T20:00:00Z", "tz": "Asia/Taipei"},
        {"since": "0001-01-01T00:00:00Z", "until": "0001-01-02T00:00:00Z", "tz": "America/New_York"},
        {"since": "0001-01-01T00:00:00Z", "until": "0001-01-02T00:00:00Z", "tz": "Asia/Taipei"},
    ):
        with pytest.raises(OperationError, match="366 days") as caught:
            daily(d, wid, **params)
        assert caught.value.status == 422 and caught.value.code == "INVALID_PARAMS"


@pytest.mark.parametrize("name", ["America", "Etc", "leapseconds", "zone", "zones", "__pycache__", "tzdata",
                                  "Asia/Taipei/", "America/Argentina", "asia/taipei", "UTC/UTC"])
def test_zone_names_must_be_exact_cached_iana_keys(name):
    with pytest.raises(UsageInvalid, match="invalid_timezone"):
        pm_resources._zone(name)


def test_non_calendar_tzif_names_are_not_accepted():
    names = pm_resources._iana_names()
    for name in ("localtime", "posixrules", "Factory", "asia/taipei"):
        assert name not in names
        with pytest.raises(UsageInvalid, match="invalid_timezone"):
            pm_resources._zone(name)
    for name in ("UTC", "Etc/UTC", "Asia/Kolkata"):
        assert name in names and pm_resources._zone(name)[0] == name
    # A legacy link is accepted only when this tzdata still publishes that exact key.
    for name in ("US/Eastern", "Asia/Calcutta"):
        if name in names:
            assert pm_resources._zone(name)[0] == name
        else:
            with pytest.raises(UsageInvalid, match="invalid_timezone"):
                pm_resources._zone(name)
    longest = max(names, key=len)
    assert len(longest) >= 30 and "." not in longest
    assert pm_resources._zone(longest)[0] == longest
