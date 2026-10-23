"""Persisted session inventory for /api/v1: what each host had, when it was seen, and whether that is stale.

A refresher polls each configured host with its own read-only Fleet (every write channel is refused in the client
core, so observation can never change BAT), upserts one row per (host, session_id), and appends an /api/v1 event
only when material values or field freshness changed. An unreachable host keeps its last rows, marked stale,
instead of dropping them; a row is only marked gone after two consecutive successful enumerations without it.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import hashlib
import json
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime

from . import confinement, registry, service
from .config import Config
from .fleet import Fleet
from .observation import Observation, _refs, body, dump, registry_bindings
from .redact import redact

# Values and non-timestamp field freshness produce session.updated; activity/observation times alone do not.
MATERIAL = ("workspace", "workspace_id", "title", "cwd", "agent_kind", "agent_preset", "model", "loaded",
            "streaming", "runtime_status", "pending", "worktree_branch", "orchestrated", "has_tab", "provenance",
            "api_access", "read_only_code", "isolation", "write_scope", "confinement", "current_verification",
            "fields_stale", "field_evidence", "sdk_session_id", "native_input_state", "registry_origin")
GONE_AFTER_MISSES = 2
MAX_PAGE = 200
COLLECT_PASS_BUDGET_S = 40.0
CURSOR_ERROR = "cursor does not match these filters; start again without it"


def _session_cursor(cursor: str, fhash: str, order: str) -> tuple[list, int]:
    """Validate the complete token before any inventory or resource query."""
    try:
        if not isinstance(cursor, str) or not cursor:
            raise ValueError
        decoded = json.loads(base64.b64decode(cursor.encode("ascii") + b"=" * (-len(cursor) % 4),
                                             altchars=b"-_", validate=True))
        if not isinstance(decoded, dict) or set(decoded) not in ({"f", "a", "k"}, {"v", "f", "a", "k"}):
            raise ValueError
        # Cursors issued before v1 had the same fields without a version marker.
        if "v" in decoded and (type(decoded["v"]) is not int or decoded["v"] != 1):
            raise ValueError
        key, as_of = decoded["k"], decoded["a"]
        if decoded["f"] != fhash or type(as_of) is not int or not 0 <= as_of <= 2**63 - 1:
            raise ValueError
        if not isinstance(key, list) or len(key) != (3 if order == "activity" else 2):
            raise ValueError
        if order == "activity" and (type(key[0]) is not int or not -(2**63) <= key[0] <= 2**63 - 1):
            raise ValueError
        for value in key[-2:]:
            if not isinstance(value, str):
                raise ValueError
            value.encode("utf-8")  # SQLite cannot bind unpaired JSON surrogate escapes.
        return key, as_of
    except (ValueError, TypeError):
        raise ValueError(CURSOR_ERROR) from None


@dataclass
class InventorySettings:
    interval_s: float = 60.0
    stale_after_s: float = 180.0
    activity_every: int = 5  # transcript/archive lookups every Nth refresh (they are the slow part)
    max_backoff_s: float = 600.0


@dataclass(frozen=True)
class CurrentSourceEvidence:
    observation: dict | None
    discovery_bodies: list[dict]
    reason: str | None = None


def _material(row: dict) -> dict:
    return {k: row.get(k) for k in MATERIAL}


def _digest(row: dict) -> str:
    return hashlib.sha256(json.dumps(_material(row), sort_keys=True, default=str).encode()).hexdigest()


def _iso(ts: float | None) -> str | None:
    if not ts:
        return None
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts))


def _valid_until(observed, stale_after_s):
    """Host observation validity, shared by Inventory and Work readiness."""
    if isinstance(observed, str):
        observed = datetime.fromisoformat(observed.replace("Z", "+00:00")).timestamp()
    return _iso(observed + stale_after_s) if observed is not None else None


def _observation_reason(row, now: float, stale_after_s: float) -> str | None:
    """The host fact's currentness, shared by Inventory and Work readiness."""
    if not row:
        return "never_observed"
    if not row.get("reachable"):
        return "host_unreachable"
    if now - (row.get("last_success_at") or 0) >= stale_after_s:
        return "host_not_refreshed"
    return None


class Inventory:
    def __init__(self, journal, config: Config, settings: InventorySettings | None = None,
                 fleet: Fleet | None = None) -> None:
        self.journal = journal
        self.db = journal.db
        self.config = config
        self.observation = Observation(journal, config)
        self.settings = settings or InventorySettings()
        # Observation must never be able to write, whatever the host tiers say.
        self.fleet = fleet or Fleet(config, read_only=True, idle_timeout=0, actor="inventory")
        self._scans: dict[str, dict] = {}
        self._runs: dict[str, int] = {}
        self._next_at: dict[str, float] = {}
        self.after_refresh: list[Callable[[str, dict], Awaitable[None]]] = []
        self.after_refresh_budget_s: float = COLLECT_PASS_BUDGET_S

    # ------------------------------------------------------------------ refresh
    async def refresh_host(self, host: str) -> dict:
        self.config.host(host)
        runs = self._runs.get(host, 0)
        self._runs[host] = runs + 1
        activity = runs % max(1, self.settings.activity_every) == 0
        started = time.time()
        self._scans[host] = {"host": host, "started_at": started, "activity": activity}
        binding = self._binding(host)
        scopes = self.db.execute("SELECT binding FROM discovery_latest WHERE host=?", (host,)).fetchall()
        if any(r[0] != binding for r in scopes):
            return self._record_failure(host, started, ValueError("DISCOVERY_SCOPE_CHANGED"))
        registry_bindings(self.journal, host, registry.list_entries(host))
        scan = self._scans[host]
        try:
            rows = await service._host_sessions(self.fleet, host, None, None, "auto", activity, discovery=scan)
            if not rows and self._has_live_rows(host):
                await self._confirm_empty(host)  # a null workspace document must not mark every session gone
            version = self.fleet.client(host).auth_info.get("serverVersion")
        except Exception as exc:  # noqa: BLE001 - recorded on the host row; rows stay and turn stale
            return self._record_failure(host, started, exc)
        return self._record_success(host, started, rows, version)

    def _has_live_rows(self, host: str) -> bool:
        return self.db.execute("SELECT 1 FROM sessions_observed WHERE host=? AND gone_at IS NULL LIMIT 1",
                               (host,)).fetchone() is not None

    async def _confirm_empty(self, host: str) -> None:
        c = self.fleet.client(host)
        raw = await c.invoke("workspace:load", {"profileId": c.host.profile_id})
        if isinstance(raw, str):
            with contextlib.suppress(ValueError):
                raw = json.loads(raw)
        if not (isinstance(raw, dict) and isinstance(raw.get("terminals"), list)):
            raise ValueError("workspace:load returned no workspace document")

    @staticmethod
    def _merge_previous(row: dict, prev_body: dict, *, meta_failed: bool) -> dict:
        """What one refresh did not observe stays as last observed; an unobserved field is not a new fact."""
        if meta_failed:  # the session's meta read failed: keep the last good row
            return {**prev_body, **{k: row[k] for k in ("provenance", "api_access", "read_only_code", "isolation", "has_tab", "workspace", "workspace_id", "title")
                                    if k in row}}
        if not row.pop("pending_checked", False) and row.get("loaded") and prev_body.get("pending"):
            row["pending"] = prev_body["pending"]  # not re-read this time (auto check): still pending
        if (row.get("last_activity_ms") or 0) < (prev_body.get("last_activity_ms") or 0):
            # Cheap refreshes see fewer activity sources; activity never moves backwards.
            row["last_activity_ms"] = prev_body["last_activity_ms"]
            row["last_activity_source"] = prev_body.get("last_activity_source")
        return row

    def _record_failure(self, host: str, at: float, exc: BaseException) -> dict:
        error = redact(f"{type(exc).__name__}: {exc}")[:300]
        with self.journal.tx():
            old = self.db.execute("SELECT * FROM hosts_observed WHERE host=?", (host,)).fetchone()
            failures = (old["consecutive_failures"] if old else 0) + 1
            self.db.execute("""INSERT INTO hosts_observed(host,reachable,error,last_attempt_at,consecutive_failures)
                VALUES(?,0,?,?,?) ON CONFLICT(host) DO UPDATE SET reachable=0,error=excluded.error,
                last_attempt_at=excluded.last_attempt_at,consecutive_failures=excluded.consecutive_failures""",
                            (host, error, at, failures))
            if old is None or old["reachable"]:
                self.journal.api_event("host", host, "host.unreachable", {"error": error}, actor="inventory", context=self._context(host))
            self._discovery(host, at, failed=error)
            if "DISCOVERY_SCOPE_CHANGED" in str(exc):
                for r in self.db.execute("SELECT * FROM sessions_observed WHERE host=?", (host,)).fetchall():
                    self._specific_stale(host, r["session_id"], "scope_changed", at)
        return {"host": host, "reachable": False, "error": error, "consecutive_failures": failures}

    def _record_success(self, host: str, at: float, rows: list[dict], version: str | None) -> dict:
        added = updated = gone = 0
        with self.journal.tx():
            old_host = self.db.execute("SELECT reachable FROM hosts_observed WHERE host=?", (host,)).fetchone()
            seen: set[str] = set()
            for raw in rows:
                sid = raw.get("session_id")
                if not isinstance(sid, str) or not sid:
                    continue
                seen.add(sid)
                prev = self.db.execute("""SELECT digest,gone_at,body FROM sessions_observed
                    WHERE host=? AND session_id=?""", (host, sid)).fetchone()
                row = {k: v for k, v in raw.items() if k != "meta_error"}
                if prev is not None:
                    row = self._merge_previous(row, json.loads(prev["body"]), meta_failed="meta_error" in raw)
                row.pop("pending_checked", None)
                evidence = body(row.get("field_evidence"))
                prior = body(prev["body"]) if prev else {}
                row["field_observed_at"] = {**prior.get("field_observed_at", {}), "tab": _iso(at)}
                if "meta_error" not in raw:
                    row["field_observed_at"].update(loading=_iso(at))
                    if raw.get("streaming") is not None:
                        row["field_observed_at"]["activity"] = _iso(at)
                elif prior:
                    evidence = {**prior.get("field_evidence", {}), "loaded": "previous_session_meta", "streaming": "previous_session_meta"}
                row.update(field_evidence=evidence, fields_stale="meta_error" in raw,
                           profile_id=self.config.host(host).profile_id, identity_evidence="enumerated")
                old_reason = prior.get("specific_stale_reason")
                row.pop("specific_stale_reason", None)
                activity = row.get("last_activity_ms") or 0
                digest = _digest(row)
                self.db.execute("""INSERT INTO sessions_observed(host,session_id,body,digest,provenance,api_access,
                    attention,sort_key,first_seen_at,last_seen_at,missing_count,gone_at)
                    VALUES(?,?,?,?,?,?,?,?,?,?,0,NULL) ON CONFLICT(host,session_id) DO UPDATE SET
                    body=excluded.body,digest=excluded.digest,provenance=excluded.provenance,
                    api_access=excluded.api_access,attention=excluded.attention,sort_key=excluded.sort_key,
                    last_seen_at=excluded.last_seen_at,missing_count=0,gone_at=NULL""",
                                (host, sid, json.dumps(row, default=str), digest, row.get("provenance") or "unknown",
                                 row.get("api_access") or "read_only", 1 if row.get("pending") else 0,
                                 -int(activity), at, at))
                if prev is None:
                    added += 1
                    self.journal.api_event("session", f"{host}/{sid}", "session.added", {**_material(row), "first_seen_at": _iso(at), "field_observed_at": row.get("field_observed_at")}, actor="inventory", context=self._context(host))
                # Recompute with today's keys so an older cached digest cannot make an unchanged poll noisy.
                elif _digest(prior) != digest or prev["gone_at"] is not None:
                    updated += 1
                    self.journal.api_event("session", f"{host}/{sid}", "session.reappeared" if prev["gone_at"] is not None else "session.updated",
                                           {**_material(row), "field_observed_at": row.get("field_observed_at"), "changed_fields": [k for k in MATERIAL if prior.get(k) != row.get(k)]},
                                           actor="inventory", context=self._context(host))
                if old_reason:
                    self.journal.api_event("session", f"{host}/{sid}", "session.fresh", {"previous_reason": old_reason}, actor="inventory", context=self._context(host))
            for missing in self.db.execute("""SELECT session_id,missing_count FROM sessions_observed
                    WHERE host=? AND gone_at IS NULL""", (host,)).fetchall():
                if missing["session_id"] in seen:
                    continue
                misses = missing["missing_count"] + 1
                if misses >= GONE_AFTER_MISSES:
                    gone += 1
                    self.db.execute("""UPDATE sessions_observed SET missing_count=?,gone_at=?
                        WHERE host=? AND session_id=?""", (misses, at, host, missing["session_id"]))
                    self.journal.api_event("session", f"{host}/{missing['session_id']}", "session.gone",
                                           {"misses": misses}, actor="inventory", context=self._context(host))
                    self._specific_stale(host, missing["session_id"], "gone", at)
                else:
                    self.db.execute("UPDATE sessions_observed SET missing_count=? WHERE host=? AND session_id=?",
                                    (misses, host, missing["session_id"]))
                    self._specific_stale(host, missing["session_id"], "not_enumerated", at)
            self.db.execute("""INSERT INTO hosts_observed(host,reachable,error,server_version,last_attempt_at,
                last_success_at,consecutive_failures,session_count) VALUES(?,1,NULL,?,?,?,0,?)
                ON CONFLICT(host) DO UPDATE SET reachable=1,error=NULL,server_version=excluded.server_version,
                last_attempt_at=excluded.last_attempt_at,last_success_at=excluded.last_success_at,
                consecutive_failures=0,session_count=excluded.session_count""",
                            (host, version, at, at, len(seen)))
            if old_host is None or not old_host["reachable"]:
                self.journal.api_event("host", host, "host.reachable", {"sessions": len(seen)}, actor="inventory", context=self._context(host))
            self._discovery(host, at, count=len(seen))
        return {"host": host, "reachable": True, "sessions": len(seen), "added": added, "updated": updated,
                "gone": gone}

    def _binding(self, host):
        hc = self.config.host(host)
        return hashlib.sha256(dump([hc.url, hc.fingerprint, hc.profile_id]).encode()).hexdigest()

    def current_source_evidence(self, host: str) -> CurrentSourceEvidence:
        """Read only evidence bound to this host's configured profile and source.

        hosts_observed has no source column; its discovery row supplies the binding.
        A scope refusal retains the old binding, so it cannot verify the new source.
        No refresh, probe, timestamp renewal or journal write happens here.
        """
        configured = self.config.hosts.get(host)
        if configured is None:
            return CurrentSourceEvidence(None, [])
        row = self.db.execute("""SELECT h.*, d.body AS discovery_body
            FROM discovery_latest d LEFT JOIN hosts_observed h ON h.host=d.host
            WHERE d.host=? AND d.profile_id=? AND d.binding=?""",
            (host, configured.profile_id, self._binding(host))).fetchone()
        if row is None:
            previous = self.db.execute("SELECT 1 FROM discovery_latest WHERE host=? LIMIT 1", (host,)).fetchone()
            return CurrentSourceEvidence(None, [], "DISCOVERY_SCOPE_CHANGED" if previous else None)
        observation = {key: row[key] for key in row.keys() if key != "discovery_body"} if row["host"] else None
        discovery = body(row["discovery_body"])
        return CurrentSourceEvidence(observation, [discovery] if discovery else [])

    def _context(self, host):
        scan = self._scans.get(host, {})
        return {"actor_basis": "service", "observer": "inventory", "entry_point": "daemon", "host": host,
                "profile_id": self.config.host(host).profile_id,
                "scan_id": f"scan_{int(scan['started_at'] * 1000000)}" if scan.get("started_at") else None,
                "evidence": [{"source": "workspace:load", "profile_id": self.config.host(host).profile_id}]}

    def _specific_stale(self, host, sid, reason, at):
        r = self.db.execute("SELECT body FROM sessions_observed WHERE host=? AND session_id=?", (host, sid)).fetchone()
        data = body(r[0])
        if data.get("specific_stale_reason") == reason:
            return
        data["specific_stale_reason"] = reason
        if reason in {"not_enumerated", "gone"}:
            data["has_tab"] = False  # complete workspace enumeration proves absence of the tab
            data["field_observed_at"] = {**data.get("field_observed_at", {}), "tab": _iso(at), "enumeration": _iso(at)}
        self.db.execute("UPDATE sessions_observed SET body=?,digest=? WHERE host=? AND session_id=?", (dump(data), _digest(data), host, sid))
        self.journal.api_event("session", f"{host}/{sid}", "session.stale", {"reason": reason, "observed_at": _iso(at), "has_tab": data.get("has_tab")},
                               actor="inventory", context=self._context(host))

    def _discovery(self, host, at, *, failed=None, count=None):
        hc = self.config.host(host)
        scan = self._scans.get(host, {})
        previous = self.db.execute("SELECT binding,body FROM discovery_latest WHERE host=? AND profile_id=?", (host, hc.profile_id)).fetchone()
        old = body(previous["body"]) if previous else {}
        info = self.fleet.client(host).auth_info
        errors = scan.get("enrichment_failures", 0)
        scope_changed = bool(failed and "DISCOVERY_SCOPE_CHANGED" in failed)
        binding = self._binding(host)
        stored = previous
        if scope_changed and stored is None:
            stored = self.db.execute("SELECT binding FROM discovery_latest WHERE host=? ORDER BY profile_id LIMIT 1", (host,)).fetchone()
        data = {"host": host, "profile_id": hc.profile_id, "binding_version": stored["binding"] if scope_changed and stored else binding,
                "scan_id": f"scan_{int(at * 1000000)}", "started_at": _iso(at), "finished_at": _iso(time.time()),
                "last_success_at": old.get("last_success_at") if failed else _iso(at),
                "observer": "inventory", "status": "failed" if failed else "partial" if errors else "succeeded",
                "complete_enumeration": not bool(failed),
                "error_code": "DISCOVERY_SCOPE_CHANGED" if scope_changed else "SOURCE_UNAVAILABLE" if failed or errors else None,
                "errors": [failed] if failed else ([{"source": "enrichment", "failures": errors}] if errors else []),
                "authority": {"kind": "bat_authenticated_read", "verified": not bool(failed),
                              "credential_ref": hashlib.sha256(dump([host, hc.token_kind]).encode()).hexdigest()[:16],
                              "server_version": info.get("serverVersion"), "capabilities": info.get("capabilities")},
                "methods": scan.get("methods") or {"workspace:load": {"status": "failed" if failed else "succeeded", "attempted": 1}},
                "coverage": old.get("coverage", {}) if failed else {"workspace_ids": scan.get("workspace_ids", []),
                    "profile_id": hc.profile_id, "registry_entries": scan.get("registry_entries", 0), "session_count": count,
                    "workspace_document": True},
                "outside_scan": [{"scope": name, "reason": reason} for name, reason in (
                    ("other_profiles_and_hosts", "not_configured"), ("manual_sessions_without_tabs_or_facts", "not_enumerable"),
                    ("arbitrary_transcripts", "only_known_claude_cwd"), ("codex_rollouts", "scan_cost"),
                    ("background_git_state", "no_background_git_probing"), ("earlier_history", "journal_facts_only"))]}
        if scope_changed:
            data["attempted_binding_version"] = binding
        if failed and "workspace:load" in data["methods"]:
            data["methods"]["workspace:load"]["status"] = "skipped" if scope_changed else "failed"
        self.db.execute("INSERT INTO discovery_latest VALUES(?,?,?,?) ON CONFLICT(host,profile_id) DO UPDATE SET body=excluded.body",
                        (host, hc.profile_id, data["binding_version"], dump(data)))
        if old.get("status") != data["status"] or old.get("coverage") != data["coverage"]:
            self.journal.api_event("host", host, "discovery.changed", data, actor="inventory", context=self._context(host))

    def discovery(self, host, *, after=0, limit=20):
        scopes = [body(r[0]) for r in self.db.execute("SELECT body FROM discovery_latest WHERE host=? ORDER BY profile_id", (host,))]
        if not scopes and host not in self.config.hosts:
            raise ValueError("unknown host")
        if not scopes:
            scopes = [{"host": host, "profile_id": self.config.host(host).profile_id, "status": "never_scanned", "authority": None}]
        return {"host": host, "scopes": scopes, "transitions": self.journal.api_events(after, limit, resource_type="host", resource_id=host, kind="discovery.changed")}

    def hosts_document(self, *, host=None, discovery=False, after=0, limit=20):
        if type(discovery) is not bool:
            raise ValueError("discovery must be boolean")
        out = {"hosts": [h for h in self.hosts() if host is None or h["host"] == host]}
        if discovery:
            if host:
                return self.discovery(host, after=after, limit=limit)
            out["discovery"] = [self.discovery(h, after=after, limit=limit) for h in self.config.hosts]
        return out

    async def refresh_all(self) -> list[dict]:
        return list(await asyncio.gather(*(self.refresh_host(h) for h in self.config.hosts)))

    async def _refresh_scheduled(self, host: str) -> dict:
        try:
            result = await self.refresh_host(host)
        except Exception:  # noqa: BLE001 - refresh_host records its own failures; this is a last resort
            result = {"consecutive_failures": 1}
        failures = result.get("consecutive_failures", 0)
        delay = (self.settings.interval_s if not failures
                 else min(self.settings.max_backoff_s, self.settings.interval_s * 2 ** min(failures, 6)))
        self._next_at[host] = time.time() + delay
        if result.get("reachable"):
            for hook in list(self.after_refresh):
                try:
                    await asyncio.wait_for(hook(host, result), timeout=self.after_refresh_budget_s)
                except Exception as exc:  # noqa: BLE001 - hooks must never change inventory result, schedule or backoff
                    logging.warning("inventory after_refresh hook for %s failed: %s", host, type(exc).__name__)
        with contextlib.suppress(Exception):
            await self.fleet.client(host).close()  # do not hold an idle socket between refreshes
        return result

    async def loop(self) -> None:
        """Refresh each host on its own schedule, independently: a slow host never delays the others.
        Failures back off up to max_backoff_s."""
        running: dict[str, asyncio.Task] = {}
        try:
            while True:
                now = time.time()
                for host in self.config.hosts:
                    task = running.get(host)
                    if (task is None or task.done()) and self._next_at.get(host, 0) <= now:
                        running[host] = asyncio.create_task(self._refresh_scheduled(host),
                                                            name=f"inventory-{host}")
                await asyncio.sleep(1.0)
        finally:
            for task in running.values():
                task.cancel()

    async def close(self) -> None:
        await self.fleet.close()

    # ------------------------------------------------------------------ reads
    def hosts(self, now: float | None = None) -> list[dict]:
        now = now or time.time()
        observed = {r["host"]: dict(r) for r in self.db.execute("SELECT * FROM hosts_observed")}
        out = []
        for name in self.config.hosts:
            r = observed.get(name) or {}
            stale, reason = self._host_stale(r, now)
            out.append({"host": name, "reachable": bool(r["reachable"]) if r else None, "error": r.get("error"),
                        "server_version": r.get("server_version"), "sessions": r.get("session_count"),
                        "last_attempt_at": _iso(r.get("last_attempt_at")),
                        "last_success_at": _iso(r.get("last_success_at")),
                        "valid_until": self._valid_until(r.get("last_success_at")),
                        "consecutive_failures": r.get("consecutive_failures", 0), "stale": stale,
                        "stale_reason": reason, "confinement": confinement.host_capability(self.fleet, name),
                        "discovery": [body(x[0]) for x in self.db.execute("SELECT body FROM discovery_latest WHERE host=?", (name,))]})
        return out

    def _valid_until(self, observed):
        return _valid_until(observed, self.settings.stale_after_s)

    def _host_stale(self, r: dict, now: float) -> tuple[bool, str | None]:
        reason = _observation_reason(r, now, self.settings.stale_after_s)
        return reason is not None, reason

    def _session_out(self, r, hosts: dict, now: float) -> dict:
        from .session_metadata import read as metadata
        body = json.loads(r["body"])
        if "confinement" not in body:
            body.update(confinement.session_fields(r["host"], r["session_id"],
                                                  account=confinement.account_status(self.fleet, r["host"])))
        stale, reason = self._host_stale(hosts.get(r["host"]) or {}, now)
        if r["gone_at"] is not None:
            stale, reason = True, "gone"
        elif not stale and now - r["last_seen_at"] >= self.settings.stale_after_s:
            stale, reason = True, "not_enumerated"
        if body.get("specific_stale_reason"):
            stale, reason = True, body["specific_stale_reason"]
        state = self._state(body, r["host"], hosts.get(r["host"]) or {}, r, stale, reason, now)
        activity = body.pop("last_activity_ms", None)
        body.pop("last_activity", None)
        body.pop("last_activity_age", None)
        current = body.get("current_verification") or {}
        if body["confinement"].get("level") == "host_account":
            account = confinement.account_status(self.fleet, r["host"])
            current["host_check"] = account
            if account["status"] != "verified":
                current.update(status="unknown", reason="host_account_unverified")
        if stale:
            current.update(status="unknown", reason="inventory_stale")
        body["current_verification"] = current
        return {**body, "host": r["host"], "session_id": r["session_id"], "last_activity_ms": activity,
                "connector_metadata": metadata(self.db, r["host"], r["session_id"]),
                "last_activity_at": _iso(activity / 1000) if activity else None,
                "first_seen_at": _iso(r["first_seen_at"]), "observed_at": _iso(r["last_seen_at"]),
                "gone_at": _iso(r["gone_at"]), "stale": stale, "stale_reason": reason, "resource_id": f"{r['host']}/{r['session_id']}",
                "state": state, "relations": self._relation_summary(f"{r['host']}/{r['session_id']}"),
                "scope_status": "current" if r["host"] in self.config.hosts else "outside_current_config"}

    def _state(self, data, host, host_row, row, stale, reason, now=None):
        loaded, streaming, tab = data.get("loaded"), data.get("streaming"), data.get("has_tab")
        runtime = data.get("runtime_status")
        values = {"connection": "unknown" if not host_row else "connected" if host_row.get("reachable") else "not_connected",
                  "loading": "unknown" if loaded is None else "loaded" if loaded else "not_loaded",
                  "tab": "unknown" if tab is None else "present" if tab else "no_tab",
                  "activity": "starting" if runtime == "starting" else "unknown" if streaming is None else "streaming" if streaming else "not_streaming",
                  "lifecycle": "active" if loaded and (streaming is True or runtime in {"running", "streaming", "starting", "active"}) else "unknown",
                  "enumeration": "unknown" if row is None else "gone" if row["gone_at"] else "missing" if row["missing_count"] else "present",
                  "freshness": "stale" if stale else "fresh"}
        times = data.get("field_observed_at", {})
        evidence = {name: {"observed_at": times.get({"loading": "loading", "activity": "activity", "tab": "tab"}.get(name), _iso(row["last_seen_at"]) if row else None),
                           "source_ref": f"hosts_observed:{host}" if name == "connection" else f"sessions_observed:{host}/{data['session_id']}" if row else "journal_identity",
                           "stale": stale or (data.get("fields_stale", False) and name in {"loading", "activity"})} for name in values}
        evidence["connection"]["observed_at"] = _iso(host_row.get("last_attempt_at"))
        host_stale = self._host_stale(host_row, now or time.time())[0]
        evidence["connection"]["stale"] = host_stale
        evidence["connection"]["valid_until"] = self._valid_until(host_row.get("last_success_at"))
        evidence["connection"]["stale_reason"] = self._host_stale(host_row, now or time.time())[1]
        evidence["tab"]["stale"] = host_stale or reason == "scope_changed"
        for axis in ("loading", "activity", "tab", "lifecycle", "enumeration"):
            if values[axis] == "unknown":
                evidence[axis]["observed_at"] = None
        if row and row["missing_count"]:
            evidence["enumeration"]["observed_at"] = times.get("enumeration")
        evidence["activity"]["valid_until"] = self._valid_until(evidence["activity"]["observed_at"])
        return {**values, "stale_reason": reason, "end_scope": None, "evidence": evidence}

    def _relation_summary(self, rid):
        return [body(r[0]) for r in self.db.execute("SELECT body FROM observation_relations WHERE session_resource_id=? ORDER BY rowid", (rid,))]

    def get_session(self, host: str, session_id: str, now: float | None = None) -> dict | None:
        from .session_metadata import read as metadata
        r = self.db.execute("SELECT * FROM sessions_observed WHERE host=? AND session_id=?",
                            (host, session_id)).fetchone()
        if r is None:
            identity = self.db.execute("SELECT body FROM observation_resources WHERE resource_type='session' AND resource_id=?", (f"{host}/{session_id}",)).fetchone()
            if not identity:
                return None
            data = {**body(identity[0]), "host": host, "session_id": session_id, "resource_id": f"{host}/{session_id}", "observation": "unknown", "has_tab": None,
                    "loaded": None, "streaming": None, "provenance": "unknown", "api_access": "read_only", "read_only_code": "UNKNOWN_READ_ONLY"}
            data["state"] = self._state(data, host, {}, None, True, "never_observed")
            data["connector_metadata"] = metadata(self.db, host, session_id)
            data["relations"] = self._relation_summary(data["resource_id"])
            data["scope_status"] = "current" if host in self.config.hosts else "outside_current_config"
            data.update(confinement.session_fields(host, session_id,
                                                   account=confinement.account_status(self.fleet, host)))
            return data
        hosts = {h["host"]: dict(h) for h in self.db.execute("SELECT * FROM hosts_observed")}
        return self._session_out(r, hosts, now or time.time())

    def session_document(self, host, session_id):
        from . import checkpoints, cleanup, session_metadata, work_items
        from .operations import OperationError
        row = self.get_session(host, session_id)
        cleaned = cleanup.lookup(self.db, f"{host}/{session_id}", host)
        if row is None and not cleaned:
            raise OperationError("NOT_FOUND", "session has no journal evidence", 404)
        discovery = [body(r[0]) for r in self.db.execute("SELECT body FROM discovery_latest WHERE host=?", (host,))]
        return {"session": row, "connector_metadata": session_metadata.read(self.db, host, session_id), "started_from": checkpoints.started_from(self.db, host, session_id),
                "work_items": work_items.work_items_for(self.db, "session", f"{host}/{session_id}"),
                "discovery": discovery, "relations_summary": row["relations"] if row else [],
                "history_available": True, "cleanup": cleaned}

    def list_sessions(self, *, host: str | None = None, provenance: str | None = None,
                      api_access: str | None = None, attention: bool | None = None, include_gone: bool = False,
                      order: str = "activity", cursor: str | None = None, limit: int = 50,
                      now: float | None = None, profile_id: str | None = None,
                      project_id: list[str] | str | None = None, work_item_id: str | None = None,
                      execution_id: str | None = None, provider: str | None = None, has_tab: bool | None = None,
                      loaded: bool | None = None, streaming: bool | None = None, lifecycle: str | None = None,
                      stale: bool | None = None, relation_scope: str = "history", principal=None) -> dict:
        """Keyset-paged inventory. Page 1 returns ``as_of`` (the event cursor at read time); follow
        /api/v1/events?after=as_of for changes instead of re-listing."""
        if order not in {"activity", "id"}:
            raise ValueError("order must be activity or id")
        if relation_scope not in {"current", "history"}:
            raise ValueError("relation_scope must be current or history")
        if lifecycle not in {None, "active", "ended", "unknown"}:
            raise ValueError("invalid lifecycle")
        for value in (has_tab, loaded, streaming, stale, attention, include_gone):
            if value is not None and type(value) is not bool:
                raise ValueError("state filters must be boolean")
        limit = max(1, min(MAX_PAGE, int(limit)))
        projects = [project_id] if isinstance(project_id, str) else project_id or []
        if not isinstance(projects, list) or not all(isinstance(p, str) for p in projects):
            raise ValueError("project_id must be a string or list")
        filters = {"host": host, "provenance": provenance, "api_access": api_access, "attention": attention,
                   "include_gone": include_gone, "order": order, "profile_id": profile_id, "project_id": sorted(projects),
                   "work_item_id": work_item_id, "execution_id": execution_id, "provider": provider,
                   "has_tab": has_tab, "loaded": loaded, "streaming": streaming, "lifecycle": lifecycle,
                   "stale": stale, "relation_scope": relation_scope}
        if principal is not None:
            from .controller_authority import binding
            filters["controller_id"] = binding(self.db, principal)["controller_id"]
        fhash = hashlib.sha256(json.dumps(filters, sort_keys=True).encode()).hexdigest()[:16]
        key, as_of = None, None
        if cursor is not None:
            key, as_of = _session_cursor(cursor, fhash, order)
        head = self.journal.api_head()
        if as_of is not None and as_of > head:
            raise ValueError(CURSOR_ERROR)
        as_of = as_of if as_of is not None else head
        configured = list(self.config.hosts) or [""]  # rows of hosts removed from the config are not listed
        sql = f"SELECT * FROM sessions_observed WHERE host IN ({','.join('?' * len(configured))})"  # noqa: S608
        args = list(configured)
        for column, value in (("host", host), ("provenance", provenance), ("api_access", api_access if principal is None else None)):
            if value:
                sql, args = sql + f" AND {column}=?", [*args, value]  # fixed identifiers only
        if attention is not None:
            sql, args = sql + " AND attention=?", [*args, 1 if attention else 0]
        if not include_gone:
            sql += " AND gone_at IS NULL"
        cols = ("sort_key", "host", "session_id") if order == "activity" else ("host", "session_id")
        if key is not None:
            sql += f" AND ({','.join(cols)}) > ({','.join('?' * len(cols))})"
            args += list(key)
        rows = [dict(r) for r in self.db.execute(sql + f" ORDER BY {','.join(cols)}", args)]
        now = now or time.time()
        hosts = {h["host"]: dict(h) for h in self.db.execute("SELECT * FROM hosts_observed")}
        # Journal-known identities are first-class even before the first successful host scan.
        observed_ids = {(r[0], r[1]) for r in self.db.execute("SELECT host,session_id FROM sessions_observed")}
        for identity in self.db.execute("SELECT resource_id FROM observation_resources WHERE resource_type='session'"):
            h, _, sid = identity[0].partition("/")
            if (h, sid) in observed_ids or h not in configured or host and h != host:
                continue
            candidate = {"host": h, "session_id": sid, "sort_key": 0, "journal_only": True}
            if key is None or tuple(candidate[c] for c in cols) > tuple(key):
                rows.append(candidate)
        rows.sort(key=lambda r: tuple(r[c] for c in cols))
        matches = []
        items = []
        membership = self._memberships(relation_scope) if projects or work_item_id else {}
        for r in rows:
            if principal is not None:
                from .pm_source_lineage import visible
                if not visible(self.db, principal, "session", r["host"] + "/" + r["session_id"]):
                    continue
            item = self.get_session(r["host"], r["session_id"], now) if r.get("journal_only") else self._session_out(r, hosts, now)
            if principal is not None:
                from .session_reading import access
                item = access(self.journal, principal, item)
            rid = item["resource_id"]
            wi_refs = membership.get(rid, [])
            relations = [rel for rel in item["relations"] if relation_scope == "history" or rel["status"] != "closed"]
            if execution_id and not any(rel["execution_id"] == execution_id for rel in relations):
                continue
            if projects and not any(wi["project_id"] in projects for wi in wi_refs):
                continue
            if work_item_id and not any(wi["work_item_id"] == work_item_id for wi in wi_refs):
                continue
            if any(value is not None and item.get(field) != value for field, value in (
                ("profile_id", profile_id), ("agent_kind", provider), ("has_tab", has_tab), ("loaded", loaded),
                ("streaming", streaming), ("stale", stale), ("provenance", provenance), ("api_access", api_access))):
                continue
            if lifecycle is not None and item["state"]["lifecycle"] != lifecycle:
                continue
            if attention is not None and bool(item.get("pending")) != attention:
                continue
            item["work_item_relations"] = wi_refs
            matches.append(r)
            items.append(item)
            if len(items) > limit:
                break
        rows, items = matches, items[:limit]
        next_cursor = None
        if len(rows) > limit:
            last = rows[limit - 1]
            payload = {"v": 1, "f": fhash, "a": as_of, "k": [last[c] for c in cols]}
            next_cursor = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
        return {"sessions": items, "count": len(items), "next_cursor": next_cursor, "as_of": as_of,
                "hosts": self.hosts(now), "coverage": {"source": "journal", "paging": "keyset", "relation_scope": relation_scope}}

    def _memberships(self, scope):
        out = {}
        sql = "SELECT l.*,w.project_id FROM work_item_links l JOIN work_items w USING(work_item_id)"
        if scope == "current":
            sql += " WHERE l.removed_at IS NULL"
        for link in self.db.execute(sql):
            refs = _refs(self.db, link["kind"], link["ref"], include_runs=True)
            for typ, rid in refs:
                if typ != "session":
                    continue
                if scope == "current" and link["kind"] == "task" and not any(
                    r["execution_id"] == link["ref"] and r["status"] != "closed" for r in self._relation_summary(rid)):
                    continue
                out.setdefault(rid, []).append({"work_item_id": link["work_item_id"], "project_id": link["project_id"],
                                               "via": {"kind": link["kind"], "ref": link["ref"]}})
        return out
