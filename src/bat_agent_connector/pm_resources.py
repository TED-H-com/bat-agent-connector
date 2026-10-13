"""Current-authority resource projection over explicit local usage bindings."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .operations import OperationError
from .pm_workspace import require_member, server_id
from .usage_adapters import MAX_COUNT, METRICS, UsageInvalid, timestamp
from .usage_config import ADAPTERS

# Adding `buckets` does not change the meaning of existing fields.
_MAX_DAYS = 366
_TZ_CHARS = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_/+-")


def capabilities():
    return {"version": 1, "adapters": list(ADAPTERS), "collection": "explicit_local_files",
            "recurring": True, "remote_collection": False, "forecast": False, "dispatch": False,
            "buckets": {"granularity": "day"}}


def _zone(name):
    if name is None:
        name = "UTC"
    if (type(name) is not str or not 1 <= len(name) <= 64 or any(char not in _TZ_CHARS for char in name)
            or name.startswith("/") or name.endswith("/") or "//" in name or ".." in name):
        raise UsageInvalid("invalid_timezone")
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError, OSError):
        raise UsageInvalid("invalid_timezone") from None


def _local_midnight(day, zone):
    try:
        return datetime(day.year, day.month, day.day, tzinfo=zone)
    except (OverflowError, ValueError):
        raise UsageInvalid("invalid_window") from None


def _local_days(start, end, zone):
    """Local dates that overlap [start, end). The end instant is exclusive."""
    start_dt = datetime.fromisoformat(start).astimezone(zone)
    end_dt = datetime.fromisoformat(end).astimezone(zone)
    first = start_dt.date()
    last = end_dt.date() - timedelta(days=1) if end_dt == _local_midnight(end_dt.date(), zone) else end_dt.date()
    if last < first:
        raise UsageInvalid("invalid_window")
    days, day = [], first
    while day <= last:
        days.append(day)
        if len(days) > _MAX_DAYS:
            raise UsageInvalid("invalid_window")
        day += timedelta(days=1)
    return days


def _whole(value):
    if type(value) is int and 0 <= value <= MAX_COUNT:
        return value
    if type(value) is float and value.is_integer() and 0 <= value <= MAX_COUNT:
        return int(value)
    return None


def _complete(day_start, day_end, window_start, window_end, stored_status, successful_at):
    # `current` is committed only when that cycle's cursor reached EOF.
    # A later unread suffix can still hold this day's events, so catching up is not proof.
    if stored_status != "current" or not isinstance(successful_at, str):
        return False
    try:
        covered_until = datetime.fromisoformat(successful_at)
    except ValueError:
        return False
    if covered_until.tzinfo is None:
        return False
    return window_start <= day_start and day_end <= window_end and covered_until >= day_end


def _tokens(item, complete):
    if item["samples"] == 0:
        # A fully read empty day was observed. An unread gap is unknown, not zero.
        return {key: 0 if complete else None for key in METRICS}
    return {key: (item["values"][key] if item["covered"][key] == item["samples"] and item["values"][key] <= MAX_COUNT
                  else None) for key in METRICS}


def _series(db, source, start, end, zone, days, stored_status, successful_at):
    slots = {day.isoformat(): {"samples": 0, "values": dict.fromkeys(METRICS, 0), "covered": dict.fromkeys(METRICS, 0)}
             for day in days}
    columns = ",".join("json_extract(value,'$.metrics." + key + "')" for key in METRICS)
    # Indexed (source_id, observed_at) range only; the log file is not opened.
    rows = db.execute("SELECT observed_at," + columns + " FROM resource_usage_samples "  # noqa: S608 - fixed METRICS constants only
                      "WHERE source_id=? AND observed_at>=? AND observed_at<?", (source.source_id, start, end))
    for row in rows:
        item = slots.get(datetime.fromisoformat(row[0]).astimezone(zone).date().isoformat())
        if item is None:
            continue
        item["samples"] += 1
        for index, key in enumerate(METRICS):
            number = _whole(row[index + 1])
            if number is None:
                continue
            item["values"][key] += number
            item["covered"][key] += 1
    window_start, window_end = datetime.fromisoformat(start), datetime.fromisoformat(end)
    day_rows = []
    for day in days:
        nxt = day + timedelta(days=1)
        day_start = _local_midnight(day, zone).astimezone(timezone.utc)
        day_end = _local_midnight(nxt, zone).astimezone(timezone.utc)
        complete = _complete(day_start, day_end, window_start, window_end, stored_status, successful_at)
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


def read(ops, principal, workspace_id, *, since=None, until=None, tz=None):
    require_member(ops.db, principal, workspace_id)
    collector = ops.context["usage_collector"]
    at = datetime.now(timezone.utc)
    try:
        zone = _zone(tz)
        start = timestamp(since) if since is not None else (at - timedelta(days=7)).isoformat(timespec="milliseconds")
        end = timestamp(until) if until is not None else at.isoformat(timespec="milliseconds")
        if not start < end or datetime.fromisoformat(end) - datetime.fromisoformat(start) > timedelta(days=366):
            raise UsageInvalid("invalid_window")
        days = _local_days(start, end, zone)
    except UsageInvalid as exc:
        if str(exc) == "invalid_timezone":
            raise OperationError("INVALID_PARAMS", "tz must be an IANA time zone name", 422) from None
        raise OperationError("INVALID_PARAMS", "Select an aware time window of at most 366 days", 422) from None
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
            # Same visibility rule as totals: withdrawn and binding-changed sources contribute no days.
            series.append(_series(ops.db, source, start, end, zone, days, stored_status if same else None, last_poll))
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
    return {"workspace_id": workspace_id, "as_of": at.isoformat(timespec="milliseconds"),
            "window": {"since": start, "until": end}, "interval_s": collector.config.interval_s,
            "rows": rows, "buckets": {"granularity": "day", "tz": zone.key, "series": series},
            "quota_pools": list(pools.values()), "capabilities": capabilities(),
            "coverage": "configured_local_files_only", "forecast": {"status": "insufficient_evidence"}}
