"""Current-authority resource projection over explicit local usage bindings."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from .operations import OperationError
from .pm_workspace import require_member, server_id
from .usage_adapters import MAX_COUNT, METRICS, UsageInvalid, timestamp
from .usage_config import ADAPTERS


def capabilities():
    return {"version": 1, "adapters": list(ADAPTERS), "collection": "explicit_local_files",
            "recurring": True, "remote_collection": False, "forecast": False, "dispatch": False}


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


def read(ops, principal, workspace_id, *, since=None, until=None):
    require_member(ops.db, principal, workspace_id)
    collector = ops.context["usage_collector"]
    at = datetime.now(timezone.utc)
    try:
        start = timestamp(since) if since is not None else (at - timedelta(days=7)).isoformat(timespec="milliseconds")
        end = timestamp(until) if until is not None else at.isoformat(timespec="milliseconds")
        if not start < end or datetime.fromisoformat(end) - datetime.fromisoformat(start) > timedelta(days=366):
            raise UsageInvalid("invalid_window")
    except UsageInvalid:
        raise OperationError("INVALID_PARAMS", "Select an aware time window of at most 366 days", 422) from None
    rows, pools = [], {}
    for source in collector.config.sources:
        if source.workspace_id != workspace_id:
            continue
        stored = ops.db.execute("SELECT * FROM resource_usage_sources WHERE source_id=?", (source.source_id,)).fetchone()
        status = stored["status"] if stored else "pending"
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
            "rows": rows, "quota_pools": list(pools.values()), "capabilities": capabilities(),
            "coverage": "configured_local_files_only", "forecast": {"status": "insufficient_evidence"}}
