"""Host-owned catalogs/usage and personal display preferences.

The BAT host is the source of model candidates and subscription windows. A
missing or failed read never fabricates a model list, account, balance or zero
usage. Display preferences cannot change the host's allowed engines/models or
modify a running session.
"""

from __future__ import annotations

import asyncio
import json
import math
import re
import time
from datetime import datetime

from . import catalog_sources, dashboard_sync
from .operations import ActionDef, OperationError
from .orchestrate import PRESETS

FIELDS = frozenset({"initial_agent", "initial_model", "last_agent", "last_model", "hidden", "order"})
DEFAULTS = {"initial_agent": None, "initial_model": None, "last_agent": None, "last_model": None,
            "hidden": [], "order": []}
ENGINES = frozenset({"claude", "codex"})


def install(ops):
    with ops.journal.tx():
        ops.db.execute("""CREATE TABLE IF NOT EXISTS product_model_preferences (
            principal_id TEXT NOT NULL, host TEXT NOT NULL, revision INTEGER NOT NULL,
            document TEXT NOT NULL, updated_at REAL NOT NULL, PRIMARY KEY(principal_id,host))""")
        ops.db.execute("""CREATE TABLE IF NOT EXISTS product_host_observations (
            host TEXT NOT NULL, kind TEXT NOT NULL, scope TEXT NOT NULL, document TEXT NOT NULL,
            observed_at REAL NOT NULL, PRIMARY KEY(host,kind,scope))""")
    if "preferences.models.update" not in ops.actions:
        ops.register(ActionDef("preferences.models.update", "observe", "Save your model display preferences",
                               _run, _admit, ("host",), authorize_existing=_authorize))


def _host(ops, host):
    if not isinstance(host, str) or host not in ops.context["fleet"].config.hosts:
        raise OperationError("UNKNOWN_HOST", "host is not configured", 404)
    return host


def preferences(db, principal_id, host):
    row = db.execute("SELECT * FROM product_model_preferences WHERE principal_id=? AND host=?",
                     (principal_id, host)).fetchone()
    return {**DEFAULTS, **(json.loads(row["document"]) if row else {}),
            "revision": row["revision"] if row else 0, "updated_at": row["updated_at"] if row else None}


def _text(value, limit=256):
    return isinstance(value, str) and 0 < len(value) <= limit and not any(ord(char) < 32 for char in value)


def _check(ops, target, params, pre):
    if set(target) != {"host"}:
        raise OperationError("INVALID_TARGET", "preferences need an exact host", 422)
    host = _host(ops, target["host"])
    if set(params) - FIELDS or not params:
        raise OperationError("INVALID_PARAMS", "only model display preference fields are accepted", 422)
    for key in ("initial_agent", "last_agent"):
        if key in params and params[key] is not None and (not isinstance(params[key], str) or params[key] not in ENGINES):
            raise OperationError("INVALID_PARAMS", "unsupported central agent", 422)
    for key in ("initial_model", "last_model"):
        if key in params and params[key] is not None and not _text(params[key]):
            raise OperationError("INVALID_PARAMS", "model IDs must be bounded strings", 422)
    for key in ("hidden", "order"):
        if key not in params:
            continue
        rows = params[key]
        if (not isinstance(rows, list) or len(rows) > 200 or any(not _text(value, 264)
                or value.partition(":")[0] not in ENGINES or not value.partition(":")[2] for value in rows)
                or len(set(rows)) != len(rows)):
            raise OperationError("INVALID_PARAMS", "model ordering uses unique agent:model IDs", 422)
    if set(pre) != {"expected_revision"} or type(pre["expected_revision"]) is not int or pre["expected_revision"] < 0:
        raise OperationError("PRECONDITION_REQUIRED", "expected_revision must name the preferences you read", 422)
    return host


def _admit(ops, principal, target, params, pre):
    host = _check(ops, target, params, pre)
    identity = dashboard_sync.identity(ops.journal, principal)
    if preferences(ops.db, identity["principal_id"], host)["revision"] != pre["expected_revision"]:
        raise OperationError("VERSION_CONFLICT", "your model preferences changed; reload before saving", 409)
    return identity


def _authorize(ops, principal, operation, _verb):
    saved = (operation.get("external_refs") or {}).get("admission_binding") or {}
    if {k: v for k, v in saved.items() if k != "controller_authority"} != dashboard_sync.identity(ops.journal, principal):
        raise OperationError("FORBIDDEN", "model preferences belong to the original effective principal", 403)


async def _run(ctx):
    def save():
        host = _check(ctx.service, ctx.target, ctx.params, ctx.preconditions)
        binding = ctx.admission_binding or {}
        metadata = ctx.service.db.execute("SELECT server_id FROM api_sync_metadata WHERE singleton=1").fetchone()
        if not metadata or not binding.get("principal_id") or binding.get("server_id") != metadata[0]:
            raise OperationError("PREFERENCE_IDENTITY_CHANGED", "the original journal identity is unavailable", 409)
        previous = preferences(ctx.service.db, binding["principal_id"], host)
        if previous["revision"] != ctx.preconditions["expected_revision"]:
            raise OperationError("VERSION_CONFLICT", "your model preferences changed; reload before saving", 409)
        value = {key: ctx.params.get(key, previous[key]) for key in FIELDS}
        revision, now = previous["revision"] + 1, time.time()
        ctx.service.db.execute("""INSERT INTO product_model_preferences VALUES(?,?,?,?,?)
            ON CONFLICT(principal_id,host) DO UPDATE SET revision=excluded.revision,
                document=excluded.document,updated_at=excluded.updated_at""",
            (binding["principal_id"], host, revision, json.dumps(value, sort_keys=True), now))
        ctx.service.journal.api_event("preferences", host, "preferences.models.updated",
                                      {"operation_id": ctx.operation_id}, actor=ctx.actor)
        return {**value, "revision": revision, "updated_at": now}
    return ctx.effect("personal_model_preferences", save, request=ctx.params)


def _number(value):
    return value if type(value) in (int, float) and math.isfinite(value) else None


def _epoch(value):
    if _number(value) is not None and value >= 0:
        return value / 1000
    if isinstance(value, str) and len(value) <= 64:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return parsed.timestamp() if parsed.tzinfo else None
        except ValueError:
            pass
    return None


def _window(value):
    if not isinstance(value, dict):
        return None
    used = _number(value.get("utilization"))
    used = used if used is not None and 0 <= used <= 1 else None
    resets = _epoch(value.get("resetsAt"))
    return {"utilization": used, "resets_at": resets} if used is not None or resets is not None else None


def normalize_usage(raw, now):
    if not isinstance(raw, dict):
        raise ValueError("invalid usage response")
    providers = []
    for provider in sorted(ENGINES):
        value = raw.get(provider)
        if not isinstance(value, dict) or value.get("provider", provider) != provider:
            continue
        fetched = _epoch(value.get("fetchedAt"))
        five, seven = _window(value.get("fiveHour")), _window(value.get("sevenDay"))
        stale = value.get("stale")
        reason = stale.get("reason") if isinstance(stale, dict) else None
        if reason not in {"network", "unauthorized", "rate_limited"}:
            reason = "host_reported_stale" if stale else None
        if not fetched or fetched > now + 60:
            reason = "missing_observation_time"
        elif now - fetched > 300 and not reason:
            reason = "observation_expired"
        account = value.get("accountEmail")
        if not isinstance(account, str) or not re.fullmatch(r"[^\s@]{1,128}@[^\s@]{1,190}", account):
            account = None
        plan = value.get("planType")
        providers.append({"provider": provider, "kind": "subscription", "five_hour": five, "seven_day": seven,
                          "fetched_at": fetched, "stale": bool(reason), "reason": reason,
                          "plan_type": plan if _text(plan, 64) else None, "account_email": account,
                          "available": five is not None or seven is not None})
    return {"providers": providers, "status": "available" if any(row["available"] for row in providers) else "unavailable",
            "reason": None if any(row["available"] for row in providers) else "host_has_no_usage_snapshot"}


def _agents(raw):
    if not isinstance(raw, list) or len(raw) > 200:
        raise ValueError("invalid agent catalog")
    found = {}
    known = {preset: engine for (engine, _use), preset in PRESETS.items()}
    for item in raw:
        if not isinstance(item, dict) or item.get("id") not in known:
            continue
        agent = known[item["id"]]
        found.setdefault(agent, {"id": agent, "label": item.get("name") if _text(item.get("name"), 100) else agent})
    return {"status": "available" if found else "unavailable", "agents": list(found.values()),
            "reason": None if found else "host_has_no_supported_agent"}


def _models(raw, agent):
    if not isinstance(raw, list) or len(raw) > 200:
        raise ValueError("invalid model catalog")
    models, seen = [], set()
    for item in raw:
        if not isinstance(item, dict) or not _text(item.get("value")) or item["value"] in seen:
            continue
        seen.add(item["value"])
        models.append({"agent": agent, "id": item["value"], "label": item.get("displayName")
                       if _text(item.get("displayName"), 200) else item["value"], "available": True,
                       "source": item.get("source") if item.get("source") in {"builtin", "sdk", "api"} else "bat_host"})
    return {"status": "available" if models else "unavailable", "models": models,
            "reason": None if models else "host_has_no_model_catalog"}


async def _observe(ops, host, kind, scope, channel, params, normalize, *, refresh=False):
    now = time.time()
    binding = catalog_sources.host_binding(ops, host)
    row = ops.db.execute("SELECT * FROM product_host_observations WHERE host=? AND kind=? AND scope=?",
                         (host, kind, scope)).fetchone()
    cached = catalog_sources.cached_document(row, binding)
    if cached is None:
        row = None
    base = {"source": "bat_host", "observed_at": row["observed_at"] if row else None, "stale": False}
    if row and not refresh and now - row["observed_at"] < 30:
        return {**base, **cached}
    try:
        raw = await asyncio.wait_for(ops.context["fleet"].client(host).invoke(channel, params), 10)
        value = normalize(raw)
    except Exception:  # noqa: BLE001 - do not expose host errors or credentials in a read model
        if catalog_sources.host_binding(ops, host) != binding:
            return {"source": "bat_host", "observed_at": None, "status": "unavailable",
                    "stale": True, "reason": "host_binding_changed"}
        if row:
            return {**base, **cached, "stale": True, "reason": "host_unavailable"}
        return {**base, "status": "unavailable", "stale": True, "reason": "host_unavailable"}
    if catalog_sources.host_binding(ops, host) != binding:
        return {"source": "bat_host", "observed_at": None, "status": "unavailable",
                "stale": True, "reason": "host_binding_changed"}
    ops.db.execute("""INSERT INTO product_host_observations VALUES(?,?,?,?,?)
        ON CONFLICT(host,kind,scope) DO UPDATE SET document=excluded.document,observed_at=excluded.observed_at""",
        (host, kind, scope, json.dumps({**value, "_source_binding": binding}, sort_keys=True), now))
    return {**base, **value, "observed_at": now}


async def read(ops, principal, host, *, agent=None, session_id=None, refresh=False):
    if not principal.allows("observe"):
        raise OperationError("FORBIDDEN", "host preferences require observe", 403)
    _host(ops, host)
    if agent is not None and (not isinstance(agent, str) or agent not in ENGINES) or type(refresh) is not bool:
        raise OperationError("INVALID_PARAMS", "invalid agent or refresh flag", 422)
    identity = dashboard_sync.identity(ops.journal, principal)
    saved = preferences(ops.db, identity["principal_id"], host)
    agent = agent or saved["initial_agent"] or "claude"
    inventory = ops.context.get("inventory")
    session = None
    if session_id is not None:
        if not _text(session_id):
            raise OperationError("INVALID_PARAMS", "invalid session ID", 422)
        from .pm_source_lineage import require_session
        require_session(ops.db, principal, host, session_id)
        session = inventory.get_session(host, session_id) if inventory else None
        if not session or session.get("agent_kind") != agent:
            raise OperationError("SESSION_NOT_FOUND", "choose an observed session of this host and agent", 404)
    elif inventory:
        rows = inventory.list_sessions(host=host, provider=agent, limit=200, principal=principal)["sessions"]
        session = next((item for item in rows if not item.get("stale") and not item.get("gone_at")), None)
    model = {"status": "unavailable", "models": [], "reason": "no_observed_session", "source": "bat_host",
             "observed_at": None, "stale": False}
    tasks = [_observe(ops, host, "agents", "", "agent:list-presets", {}, _agents, refresh=refresh),
             _observe(ops, host, "usage", "", "agent:usage-snapshot", {},
                      lambda raw: normalize_usage(raw, time.time()), refresh=refresh)]
    if session:
        tasks.append(_observe(ops, host, "models", agent + ":" + session["session_id"],
                              "claude:get-supported-models", {"sessionId": session["session_id"]},
                              lambda raw: _models(raw, agent), refresh=refresh))
    results = await asyncio.gather(*tasks)
    agents, usage = results[:2]
    if session:
        from .pm_source_lineage import require_session
        require_session(ops.db, principal, host, session["session_id"])
        model = results[2]
    usage.setdefault("providers", [])
    agents.setdefault("agents", [])
    model.setdefault("models", [])
    for provider in usage["providers"]:
        if provider["fetched_at"] and time.time() - provider["fetched_at"] > 300:
            provider.update(stale=True, reason=provider["reason"] or "observation_expired")
    usage["stale"] = usage["stale"] or any(row["stale"] for row in usage["providers"])
    if session and session.get("stale"):
        model.update(stale=True, reason="session_inventory_stale")
    model.update(agent=agent, session_id=session["session_id"] if session else None,
                 current_model=session.get("model") if session else None)
    known = {item["id"] for item in model["models"]}
    unknown = [{"agent": saved[role + "_agent"], "id": saved[role + "_model"]} for role in ("initial", "last")
               if saved[role + "_model"] and (saved[role + "_agent"] != agent or saved[role + "_model"] not in known)]
    return {"version": 1, "host": host, "agent_catalog": agents, "model_catalog": model,
            "model_preferences": saved, "unknown_models": unknown, "usage": usage}
