"""Current-authority resource projection over explicit local usage bindings."""
from __future__ import annotations

import bisect
import json
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError, available_timezones

from .operations import OperationError
from .pm_workspace import require_member, server_id
from .usage_adapters import MAX_COUNT, METRICS, UsageInvalid, timestamp
from .usage_config import ADAPTERS

# Opt-in chart rows. Every read, with or without buckets, still rejects a span over 366 days.
_BUCKET_DAYS = 92
_SETTLE_FLOOR = timedelta(minutes=15)
# TZif files ZoneInfo will open that are not calendar zones a client can name.
_NOT_IANA = frozenset({"localtime", "posixrules", "Factory"})
# Stored observed_at values are millisecond UTC strings, so this sorts after every one of them.
_AFTER_LAST_TIMESTAMP = "9999-12-31T99"


def capabilities():
    return {"version": 1, "adapters": list(ADAPTERS), "collection": "explicit_local_files",
            "recurring": True, "remote_collection": False, "forecast": False, "dispatch": False,
            "buckets": {"granularity": "day"}}


@lru_cache(maxsize=1)
def _iana_names():
    return frozenset(available_timezones()) - _NOT_IANA


def _zone(name):
    if name is None:
        name = "UTC"
    if type(name) is not str or name not in _iana_names():
        raise UsageInvalid("invalid_timezone")
    try:
        return name, ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError, OSError):
        raise UsageInvalid("invalid_timezone") from None


def _opt_in(buckets):
    if buckets is None:
        return False
    if buckets != "day":
        raise UsageInvalid("invalid_buckets")
    return True


def _settle(interval_s):
    interval = interval_s if isinstance(interval_s, (int, float)) and interval_s > 0 else 0
    return max(_SETTLE_FLOOR, timedelta(seconds=interval * 3))


def _local_midnight(day, zone):
    """UTC instant of local midnight. An ambiguous midnight uses the earlier fold."""
    try:
        # fold=0 is the first occurrence, so the local date starts at its earlier instant.
        return datetime(day.year, day.month, day.day, tzinfo=zone).astimezone(timezone.utc)
    except (OverflowError, ValueError, OSError):
        raise UsageInvalid("invalid_window") from None


def _local_days(start, end, zone):
    """Local dates overlapping [start, end). The end instant is exclusive.

    The last date is the zone's date of the instant 1µs before `end`, so a repeated
    or missing local midnight follows that instant instead of a wall-clock compare.
    """
    try:
        first = datetime.fromisoformat(start).astimezone(zone).date()
        last = (datetime.fromisoformat(end) - timedelta(microseconds=1)).astimezone(zone).date()
    except (OverflowError, OSError, ValueError):
        raise UsageInvalid("invalid_window") from None
    if last < first:
        raise UsageInvalid("invalid_window")
    # A calendar label still needs a representable local midnight before it can be a bucket.
    _local_midnight(first, zone)
    days, day = [], first
    while day <= last:
        if len(days) >= _BUCKET_DAYS:
            raise UsageInvalid("bucket_window")
        days.append(day)
        try:
            day += timedelta(days=1)
        except OverflowError:
            break
    return days


def _refuse(exc):
    reason = str(exc)
    if reason == "invalid_timezone":
        message = "tz must be an IANA time zone name"
    elif reason == "invalid_buckets":
        message = "buckets must be day"
    elif reason == "tz_without_buckets":
        message = "tz requires buckets=day"
    elif reason == "bucket_window":
        message = "Daily buckets cover at most 92 local dates"
    else:
        message = "Select an aware time window of at most 366 days"
    raise OperationError("INVALID_PARAMS", message, 422) from None


def _whole(value):
    if type(value) is int and 0 <= value <= MAX_COUNT:
        return value
    if type(value) is float and value.is_integer() and 0 <= value <= MAX_COUNT:
        return int(value)
    return None


def _complete(day_start, day_end, window_start, window_end, stored_status, coverage_at, settle):
    # `current` is committed only when that cycle's cursor reached EOF.
    # Coverage is the read's start, and the day must have settled after it ended.
    if stored_status != "current" or not isinstance(coverage_at, str):
        return False
    try:
        covered = datetime.fromisoformat(coverage_at)
    except ValueError:
        return False
    if covered.tzinfo is None:
        return False
    try:
        settled = day_end + settle
    except OverflowError:
        return False
    return window_start <= day_start and day_end <= window_end and covered >= settled


def _tokens(item, complete):
    if item["samples"] == 0:
        # A fully read empty day was observed. An unread gap is unknown, not zero.
        return {key: 0 if complete else None for key in METRICS}
    return {key: (item["values"][key] if item["covered"][key] == item["samples"] and item["values"][key] <= MAX_COUNT
                  else None) for key in METRICS}


def _coverage_at(stored, same):
    if not stored or not same:
        return None
    try:
        cursor = json.loads(stored["cursor"])
    except (TypeError, ValueError):
        return None
    if not isinstance(cursor, dict):
        return None
    value = cursor.get("coverage_at")
    return value if isinstance(value, str) else None


def _day_end(day, zone):
    """UTC start of the next local date, or None when that instant cannot be represented."""
    try:
        following = day + timedelta(days=1)
    except OverflowError:
        return None
    return _local_midnight(following, zone)


def _bounds(days, zone):
    # Day edges use the same millisecond UTC form as stored observed_at, compared as strings.
    edges = [_local_midnight(day, zone).isoformat(timespec="milliseconds") for day in days]
    end = _day_end(days[-1], zone)
    edges.append(end.isoformat(timespec="milliseconds") if end is not None else _AFTER_LAST_TIMESTAMP)
    return edges


def _series(db, source, start, end, zone, days, stored_status, coverage_at, settle):
    slots = {day.isoformat(): {"samples": 0, "values": dict.fromkeys(METRICS, 0), "covered": dict.fromkeys(METRICS, 0)}
             for day in days}
    edges = _bounds(days, zone)
    columns = ",".join("json_extract(value,'$.metrics." + key + "')" for key in METRICS)
    # Indexed (source_id, observed_at) range only; the log file is not opened.
    rows = db.execute("SELECT observed_at," + columns + " FROM resource_usage_samples "  # noqa: S608 - fixed METRICS constants only
                      "WHERE source_id=? AND observed_at>=? AND observed_at<?", (source.source_id, start, end))
    for row in rows:
        observed = row[0]
        if type(observed) is not str:
            continue
        index = bisect.bisect_right(edges, observed) - 1
        if not 0 <= index < len(days):
            continue
        item = slots[days[index].isoformat()]
        item["samples"] += 1
        for offset, key in enumerate(METRICS):
            number = _whole(row[offset + 1])
            if number is None:
                continue
            item["values"][key] += number
            item["covered"][key] += 1
    window_start, window_end = datetime.fromisoformat(start), datetime.fromisoformat(end)
    day_rows = []
    for day in days:
        day_start = _local_midnight(day, zone)
        day_end = _day_end(day, zone)
        # A day whose exclusive end is not representable cannot be shown as settled.
        complete = False if day_end is None else _complete(
            day_start, day_end, window_start, window_end, stored_status, coverage_at, settle)
        day_rows.append({"date": day.isoformat(), "tokens": _tokens(slots[day.isoformat()], complete),
                         "complete": complete})
    return {"source_id": source.source_id, "adapter": source.adapter, "cli": source.adapter.split("-", 1)[0],
            "days": day_rows}


def _metrics(db, source_id, start, end):
    fields = ["count(*) AS samples", "min(observed_at) AS first_at", "max(observed_at) AS last_at"]
    for key in METRICS:
        fields += [f"total(json_extract(value,'$.metrics.{key}')) AS {key}",
                   f"count(json_extract(value,'$.metrics.{key}')) AS {key}_covered"]
    row = dict(db.execute("SELECT " + ",".join(fields) + " FROM resource_usage_samples "  # noqa: S608 - fixed METRICS constants only
                          "WHERE source_id=? AND observed_at>=? AND observed_at<?", (source_id, start, end)).fetchone())
    values = {}
    for key in METRICS:
        total, covered = int(row[key]), row[key + "_covered"]
        # No observations is unknown, not proof of zero use. Totals are bounded for browser precision.
        values[key] = {"value": total if covered == row["samples"] and covered and total <= MAX_COUNT else None,
                       "observed": total if covered and total <= MAX_COUNT else None,
                       "covered": covered}
    models = db.execute("""SELECT json_extract(value,'$.actual_model') actual_model,
        json_extract(value,'$.requested_model') requested_model,json_extract(value,'$.effort') effort,
        json_extract(value,'$.cli_version') cli_version, count(*) observations
        FROM resource_usage_samples WHERE source_id=? AND observed_at>=? AND observed_at<?
        GROUP BY actual_model,requested_model,effort,cli_version ORDER BY observations DESC,actual_model,requested_model
        LIMIT 21""", (source_id, start, end)).fetchall()
    return {"metrics": values, "observations": row["samples"], "first_at": row["first_at"], "last_at": row["last_at"],
            "configurations": [dict(r) for r in models[:20]], "configurations_partial": len(models) > 20}


def read(ops, principal, workspace_id, *, since=None, until=None, tz=None, buckets=None):
    require_member(ops.db, principal, workspace_id)
    collector = ops.context["usage_collector"]
    at = datetime.now(timezone.utc)
    try:
        want = _opt_in(buckets)
        if tz is not None and not want:
            raise UsageInvalid("tz_without_buckets")
        zone_name, zone = _zone(tz) if want else (None, None)
        start = timestamp(since) if since is not None else (at - timedelta(days=7)).isoformat(timespec="milliseconds")
        end = timestamp(until) if until is not None else at.isoformat(timespec="milliseconds")
        if not start < end or datetime.fromisoformat(end) - datetime.fromisoformat(start) > timedelta(days=366):
            raise UsageInvalid("invalid_window")
        days = _local_days(start, end, zone) if want else None
        settle = _settle(collector.config.interval_s) if want else None
    except OverflowError:
        _refuse(UsageInvalid("invalid_window"))
    except UsageInvalid as exc:
        _refuse(exc)
    rows, pools, series = [], {}, []
    for source in collector.config.sources:
        if source.workspace_id != workspace_id:
            continue
        stored = ops.db.execute("SELECT * FROM resource_usage_sources WHERE source_id=?", (source.source_id,)).fetchone()
        stored_status = stored["status"] if stored else "pending"
        status = stored_status
        same = stored and stored["binding"] == source.binding
        if stored and not same:
            status = "binding_changed"
        last_poll = stored["successful_at"] if same else None
        if status in {"current", "catching_up"} and last_poll and (
                at - datetime.fromisoformat(last_poll)).total_seconds() > max(180, collector.config.interval_s * 3):
            status = "stale"
        usable = same and status in {"current", "catching_up", "stale"}
        row = {"source_id": source.source_id, "adapter": source.adapter, "role": source.role,
               "host_id": server_id(ops.db), "host_kind": "central_local", "status": status,
               "checked_at": stored["checked_at"] if same else None, "successful_at": last_poll,
               "quota_pool": source.quota_pool, "capacity": None, "forecast": None,
               "metrics": None, "observations": None, "configurations": [], "calls": None}
        if usable:
            row.update(_metrics(ops.db, source.source_id, start, end))
            if source.adapter == ADAPTERS[1]:
                row["calls"] = row["observations"]
            if want:
                # Same visibility rule as totals: withdrawn and binding-changed sources contribute no days.
                try:
                    series.append(_series(ops.db, source, start, end, zone, days, stored_status if same else None,
                                          _coverage_at(stored, same), settle))
                except UsageInvalid as exc:
                    _refuse(exc)
        rows.append(row)
        if source.quota_pool:
            rate = json.loads(stored["quota"]) if usable and stored["quota"] else None
            limit_id = rate["limit_id"] if rate else None
            pool = pools.setdefault((source.quota_pool, limit_id), {"pool_id": source.quota_pool, "limit_id": limit_id,
                "identity_basis": "operator_binding", "sources": [], "status": "unavailable", "snapshot": None})
            pool["sources"].append(source.source_id)
            if rate:
                age = (at - datetime.fromisoformat(rate["observed_at"])).total_seconds()
                rate["windows"] = [w for w in rate["windows"] if w["resets_at"] > at.timestamp()]
                if 0 <= age <= 300 and rate["windows"] and status == "current":
                    old = pool["snapshot"]
                    if old is None or rate["observed_at"] > old["observed_at"]:
                        pool.update(snapshot=rate, status="reported")
                    elif rate["observed_at"] == old["observed_at"] and rate != old:
                        pool["status"] = "conflicting"
    for pool in pools.values():
        if pool["status"] == "conflicting":
            pool["snapshot"] = None
    payload = {"workspace_id": workspace_id, "as_of": at.isoformat(timespec="milliseconds"),
               "window": {"since": start, "until": end}, "interval_s": collector.config.interval_s,
               "rows": rows, "quota_pools": list(pools.values()), "capabilities": capabilities(),
               "coverage": "configured_local_files_only", "forecast": {"status": "insufficient_evidence"}}
    if want:
        payload["buckets"] = {"granularity": "day", "tz": zone_name, "series": series}
    return payload
