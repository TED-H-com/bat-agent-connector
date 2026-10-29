"""High-level operations shared by the MCP server and the CLI.

Every function returns JSON-serializable dicts. Token values never appear in
results; errors are redacted.
"""

from __future__ import annotations

import asyncio
import json
import statistics
import time
import uuid
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from . import confinement, registry, resource_policy, task_control
from .client import BatClient, event_session_id
from .errlog import log_error
from .errors import BatError, ConnectionLost, InvokeError, InvokeTimeout, TaskControlRefused, WriteRefused
from .fleet import Fleet
from .iso_time import parse_datetime
from .redact import redact
from .safety import Audit
from .summarize import clip, iso_local, summarize_message, summarize_pending, ts_to_ms

MAX_LAST_N = 100
MAX_READ_CHARS = 60_000
MAX_PROMPT_CHARS = 20_000
DIGEST_TEXT_MAX_CHARS = 20_000
TURN_SLICE_MAX_ITEMS = 2000
SESSION_WAITING_FIELDS = (
    "pendingPermission", "pendingQuestion", "pendingPermissions", "pendingQuestions", "queuedMessages",
    "pendingApproval", "pendingAskUser", "queuedMessageCount", "isWaitingForInput",
)
_write_locks: dict[tuple[int, str], asyncio.Lock] = {}


TASK_SERVICE_POINTER = "task-service.json"


def task_service_db() -> Path | None:
    """The running task daemon's journal, as recorded next to the session registry."""
    try:
        data = json.loads((registry.registry_path().parent / TASK_SERVICE_POINTER).read_text())
        db = Path(data["db_path"])
    except (OSError, ValueError, KeyError, TypeError):
        return None
    return db if db.is_absolute() else None


def _write_lock(host: str) -> asyncio.Lock:
    # Host-scoped: unrelated hosts need not wait for each other's RPCs. BAT
    # still has no cross-process/GUI ownership fence, so keep same-host writes
    # serialized within this event loop.
    key = (id(asyncio.get_running_loop()), host)
    lock = _write_locks.get(key)
    if lock is None:
        lock = _write_locks[key] = asyncio.Lock()
    return lock


def _err(e: BaseException) -> str:
    if isinstance(e, BatError):
        return redact(e)
    return redact(f"{type(e).__name__}: {e}")


def agent_kind(preset: str | None) -> str | None:
    if not preset:
        return None
    p = preset.lower()
    if p.startswith("codex"):
        return "codex"
    if p.startswith("claude"):
        return "claude"
    return p


def _age(ms: int | None) -> str | None:
    if not ms:
        return None
    s = max(0, time.time() - ms / 1000)
    if s < 90:
        return f"{int(s)}s"
    if s < 5400:
        return f"{int(s / 60)}m"
    if s < 172800:
        return f"{s / 3600:.1f}h"
    return f"{int(s / 86400)}d"


def _hosts(fleet: Fleet, host: str | None) -> list[str]:
    if host:
        fleet.config.host(host)
        return [host]
    return list(fleet.config.hosts)


async def _gather_limited(coros: list, limit: int = 8) -> list:
    sem = asyncio.Semaphore(limit)

    async def run(c):
        async with sem:
            try:
                return await c
            except Exception as e:  # noqa: BLE001
                return e

    return await asyncio.gather(*(run(c) for c in coros))


async def _workspace(c: BatClient) -> dict:
    raw = await c.invoke("workspace:load", {"profileId": c.host.profile_id})
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            raw = {}
    return raw if isinstance(raw, dict) else {}


def _agent_terminals(ws: dict) -> list[dict]:
    return [t for t in ws.get("terminals") or [] if isinstance(t, dict) and t.get("agentPreset")]


async def _meta(c: BatClient, sid: str) -> dict | None:
    m = await c.invoke("claude:get-session-meta", {"sessionId": sid})
    return m if isinstance(m, dict) else None


def _state_safe(kind: str | None, meta: dict | None) -> bool:
    """get-session-state on a Claude record without cwd makes the sidecar drop that record; avoid it."""
    if not meta:
        return False
    if kind == "claude":
        return bool(str(meta.get("cwd") or "").strip())
    return True


def registry_terminal(e: dict) -> dict:
    """Synthesize a workspace-like terminal record for a tab-less orchestrated session."""
    return {
        "id": e.get("session_id"),
        "agentPreset": e.get("agent_preset"),
        "cwd": e.get("cwd"),
        "workspaceId": e.get("workspace_id"),
        "title": e.get("title") or "orchestrated",
        "model": e.get("model"),
        "worktreePath": e.get("worktree_path"),
        "worktreeBranch": e.get("branch"),
        "permissionMode": e.get("permission_mode_claude"),
        "agentParams": e.get("agent_params"),
        "_orchestrated": True,
        "_origin_cwd": e.get("origin_cwd"),
    }


async def _resolve_session(c: BatClient, session_id: str, ws: dict | None = None) -> tuple[dict, dict]:
    ws = ws if ws is not None else await _workspace(c)
    sid = (session_id or "").strip()
    if len(sid) < 6:
        raise BatError("session_id must be the full id or a unique prefix of at least 6 characters")
    terms = ws.get("terminals") or []
    exact = [t for t in terms if t.get("id") == sid]
    matches = exact or [t for t in terms if str(t.get("id", "")).startswith(sid)]
    if not matches:
        # A retried start can leave several rows for one session; the newest row wins.
        regs = {e.get("session_id"): e for e in registry.find_prefix(c.host.name, sid)}
        if len(regs) == 1:
            return registry_terminal(next(iter(regs.values()))), ws
        raise BatError(f"session {sid!r} not found on host {c.host.name}")
    if len(matches) > 1:
        raise BatError(f"session prefix {sid!r} is ambiguous on host {c.host.name}")
    t = matches[0]
    if not t.get("agentPreset"):
        raise BatError(f"terminal {t.get('id')} is a plain shell, not an agent session")
    return t, ws


# --------------------------------------------------------------------------- read tools
async def hosts_list(fleet: Fleet, probe: bool = True) -> dict:
    async def one(name: str) -> dict:
        hc = fleet.config.host(name)
        d: dict[str, Any] = {
            "name": name,
            "url": hc.url,
            "writes_enabled": fleet.writes_enabled(name),
            "orchestrate_enabled": fleet.orchestrate_enabled(name),
            "token_ref_kind": hc.token_kind,
            "token_available": hc.token_available(),
            "confinement": confinement.host_capability(fleet, name),
        }
        if probe:
            c = fleet.client(name)
            try:
                rtt = await c.ping()
                d.update(
                    reachable=True,
                    server_version=c.auth_info.get("serverVersion"),
                    protocol=c.auth_info.get("protocol"),
                    ping_ms=round(rtt),
                )
            except Exception as e:  # noqa: BLE001
                d.update(reachable=False, error=_err(e))
        return d

    items = await asyncio.gather(*(one(n) for n in fleet.config.hosts))
    return {"hosts": list(items), "count": len(items), "read_only": not fleet.any_writes}


async def host_status(fleet: Fleet, host: str) -> dict:
    await confinement.check_account(fleet, host)
    c = fleet.client(host)
    was_connected = c.connected
    await c.connect()
    pings = [round(await c.ping()) for _ in range(3)]
    version = await c.invoke("app:get-version", {})
    profiles = await c.invoke("profile:list", {})
    ws = await _workspace(c)
    agents = _agent_terminals(ws)
    metas = await _gather_limited([_meta(c, t["id"]) for t in agents])
    loaded = [m for m in metas if isinstance(m, dict)]
    streaming = [m for m in loaded if m.get("isStreaming") is True]
    kinds: dict[str, int] = {}
    for t in agents:
        k = agent_kind(t.get("agentPreset")) or "?"
        kinds[k] = kinds.get(k, 0) + 1
    return {
        "host": host,
        "server_version": c.auth_info.get("serverVersion") or version,
        "app_version": version,
        "protocol": c.auth_info.get("protocol"),
        "capabilities": c.auth_info.get("capabilities"),
        "latency": {
            "connect_ms": None if was_connected or c.connect_ms is None else round(c.connect_ms),
            "auth_ms": None if was_connected or c.auth_ms is None else round(c.auth_ms),
            "ping_ms": pings,
            "ping_median_ms": statistics.median(pings),
        },
        "profiles": len((profiles or {}).get("profiles") or []) if isinstance(profiles, dict) else None,
        "workspaces": len(ws.get("workspaces") or []),
        "terminals": len(ws.get("terminals") or []),
        "agent_sessions": len(agents),
        "agent_sessions_by_kind": kinds,
        "loaded": len(loaded),
        "streaming": len(streaming),
        "meta_errors": sum(1 for m in metas if isinstance(m, Exception)),
        "writes_enabled": fleet.writes_enabled(host),
        "orchestrate_enabled": fleet.orchestrate_enabled(host),
        "confinement": confinement.host_capability(fleet, host),
    }


async def workspaces_list(fleet: Fleet, host: str | None = None) -> dict:
    async def one(name: str) -> tuple[str, Any]:
        try:
            ws = await _workspace(fleet.client(name))
        except Exception as e:  # noqa: BLE001
            return name, e
        terms = ws.get("terminals") or []
        out = []
        for w in ws.get("workspaces") or []:
            mine = [t for t in terms if t.get("workspaceId") == w.get("id")]
            out.append(
                {
                    "host": name,
                    "workspace_id": w.get("id"),
                    "name": w.get("name"),
                    "folder": w.get("folderPath"),
                    "terminals": len(mine),
                    "agent_sessions": sum(1 for t in mine if t.get("agentPreset")),
                    "active": w.get("id") == ws.get("activeWorkspaceId"),
                }
            )
        return name, out

    res = await asyncio.gather(*(one(n) for n in _hosts(fleet, host)))
    items, errors = [], {}
    for name, r in res:
        if isinstance(r, Exception):
            errors[name] = _err(r)
        else:
            items.extend(r)
    return {"workspaces": items, "count": len(items), "errors": errors}


async def _host_sessions(
    fleet: Fleet,
    name: str,
    workspace: str | None,
    agent: str | None,
    check_pending: str,
    activity_sources: bool,
    discovery: dict | None = None,
) -> list[dict]:
    c = fleet.client(name)
    methods = {source: {"status": "skipped", "attempted": 0, "succeeded": 0, "failed": 0} for source in
               ("workspace:load", "session_meta", "safe_state", "archive", "claude_transcripts", "registry", "journal")}
    if discovery is not None:
        discovery["methods"] = methods
    async def read_source(source, channel, params, **kw):
        method = methods[source]
        method["attempted"] += 1
        try:
            result = await c.invoke(channel, params, **kw)
        except Exception:
            method["failed"] += 1
            method["status"] = "failed" if not method["succeeded"] else "partial"
            raise
        method["succeeded"] += 1
        method["status"] = "partial" if method["failed"] else "succeeded"
        return result
    methods["workspace:load"]["attempted"] = 1
    ws = await _workspace(c)
    if discovery is not None and not isinstance(ws.get("terminals"), list):
        raise ValueError("workspace:load returned no workspace document")
    methods["workspace:load"].update(status="succeeded", succeeded=1)
    ws_by_id = {w.get("id"): w for w in ws.get("workspaces") or []}
    terms = _agent_terminals(ws)
    known = {t.get("id") for t in terms}
    entries = registry.list_entries(name)
    orchestrated_ids = {e.get("session_id") for e in entries}
    if discovery is not None:
        discovery.update(workspace_ids=sorted(str(k) for k in ws_by_id if k),
                         registry_entries=len(entries), workspace_document=True, enrichment_failures=0)
    for e in registry.list_entries(name, active_only=True):
        if e.get("session_id") not in known:
            terms.append(registry_terminal(e))
    if workspace:
        wl = workspace.lower()
        terms = [
            t
            for t in terms
            if str(t.get("workspaceId", "")).startswith(workspace)
            or wl in str((ws_by_id.get(t.get("workspaceId")) or {}).get("name", "")).lower()
        ]
    if agent:
        terms = [t for t in terms if agent_kind(t.get("agentPreset")) == agent.lower()]
    methods["registry"].update(status="succeeded", attempted=1, succeeded=1)
    methods["journal"].update(status="succeeded", attempted=1, succeeded=1)
    metas = await _gather_limited([_meta(c, t["id"]) for t in terms])
    failures = sum(isinstance(m, Exception) for m in metas)
    methods["session_meta"].update(status="partial" if failures and failures < len(terms) else "failed" if failures else "succeeded",
                                   attempted=len(terms), succeeded=len(terms) - failures, failed=failures)
    recent_attention = {
        e.get("sessionId")
        for e in c.recent_events
        if e.get("channel") in ("agent:ask-user", "agent:permission-request")
    }
    rows: list[dict] = []
    for t, m in zip(terms, metas, strict=True):
        meta = m if isinstance(m, dict) else None
        kind = agent_kind(t.get("agentPreset"))
        w = ws_by_id.get(t.get("workspaceId")) or {}
        last_ms = ts_to_ms((meta or {}).get("lastDataAt"))
        row: dict[str, Any] = {
            "host": name,
            "session_id": t.get("id"),
            "workspace": w.get("name"),
            "workspace_id": t.get("workspaceId"),
            "title": t.get("title"),
            "cwd": t.get("cwd") or (meta or {}).get("cwd"),
            "agent_preset": t.get("agentPreset"),
            "agent_kind": kind,
            "model": (meta or {}).get("model") or t.get("model"),
            "loaded": None if isinstance(m, Exception) else meta is not None,
            "streaming": meta.get("isStreaming") if meta else None,
            "provider_native_id": (meta or {}).get("sdkSessionId") or t.get("sdkSessionId"),
            # These proof fields come only from this authenticated metadata read;
            # never backfill native identity from a tab or previous observation.
            "sdk_session_id": (meta or {}).get("sdkSessionId"),
            "native_input_state": (meta or {}).get("inputState"),
            "registry_origin": next(({key: e.get(key) for key in
                ("controller_incarnation", "created_at", "start_operation_id")}
                for e in entries if e.get("session_id") == t.get("id")), None),
            "field_evidence": {"loaded": "meta_failed" if isinstance(m, Exception) else "session_meta",
                               "streaming": "session_meta" if meta else "not_observed",
                               "has_tab": "workspace_document",
                               "native_input": "session_meta" if meta else "not_observed"},
            "runtime_status": (meta or {}).get("runtimeStatus"),
            "num_turns": (meta or {}).get("numTurns"),
            "worktree_branch": t.get("worktreeBranch"),
            "orchestrated": t.get("id") in orchestrated_ids,
            "has_tab": not t.get("_orchestrated", False),
            **confinement.session_fields(name, t.get("id"), meta, account=confinement.account_status(fleet, name)),
            **resource_policy.classify_row_for_read(c.host, t.get("id"), has_tab=not t.get("_orchestrated", False),
                                                    entries=entries),
            "pending": None,
            "last_activity_ms": last_ms,
            "last_activity_source": "host" if last_ms else None,
        }
        if isinstance(m, Exception):
            row["meta_error"] = _err(m)
            if discovery is not None:
                discovery["enrichment_failures"] += 1
        rows.append(row)

    async def fill_archive(row: dict) -> None:
        r = await read_source("archive", "claude:load-archived", {"sessionId": row["session_id"], "offset": 0, "limit": 1})
        if isinstance(r, dict):
            row["archived_total"] = r.get("total")
            msgs = r.get("messages") or []
            if msgs and isinstance(msgs[-1], dict):
                ms = ts_to_ms(msgs[-1].get("timestamp") or msgs[-1].get("completedAt"))
                if ms and (not row["last_activity_ms"] or ms > row["last_activity_ms"]):
                    row["last_activity_ms"] = ms
                    row["last_activity_source"] = "archive"

    async def fill_pending(row: dict, meta: dict) -> None:
        st = await read_source("safe_state", "claude:get-session-state", {"sessionId": row["session_id"]})
        if isinstance(st, dict):
            p = summarize_pending(st.get("pendingAskUser"), "ask_user") or summarize_pending(
                st.get("pendingPermission"), "permission"
            )
            row["pending"] = p
            row["pending_checked"] = True  # "pending": None is a fact only when this is set
            msgs = st.get("messages") or []
            for mm in reversed(msgs):
                ms = ts_to_ms(mm.get("timestamp") or mm.get("completedAt")) if isinstance(mm, dict) else None
                if ms:
                    if not row["last_activity_ms"] or ms > row["last_activity_ms"]:
                        row["last_activity_ms"] = ms
                        row["last_activity_source"] = "state"
                    break

    async def fill_transcripts(cwd: str, group: list[dict]) -> None:
        # Claude transcript files on the host: timestamp = file mtime (cheap, parsed host-side).
        # Not used for Codex: the host scans every rollout file for that, which can take minutes.
        r = await read_source("claude_transcripts", "claude:list-sessions", {"cwd": cwd, "agentKind": "claude"}, timeout=20)
        by_sdk = (
            {e.get("sdkSessionId"): e for e in r or [] if isinstance(e, dict)} if isinstance(r, list) else {}
        )
        for row in group:
            e = by_sdk.get(row.pop("_sdk", None))
            ms = ts_to_ms(e.get("timestamp")) if e else None
            if ms and (not row["last_activity_ms"] or ms > row["last_activity_ms"]):
                row["last_activity_ms"] = ms
                row["last_activity_source"] = "transcript"

    if activity_sources:
        groups: dict[str, list[dict]] = {}
        for row, t in zip(rows, terms, strict=True):
            if row["agent_kind"] == "claude" and row["cwd"] and t.get("sdkSessionId"):
                row["_sdk"] = t.get("sdkSessionId")
                groups.setdefault(row["cwd"], []).append(row)
        transcript_results = await _gather_limited([fill_transcripts(cwd, g) for cwd, g in groups.items()], limit=3)
        if discovery is not None:
            discovery["enrichment_failures"] += sum(isinstance(r, Exception) for r in transcript_results)
    for row in rows:
        row.pop("_sdk", None)

    jobs = []
    for row, m in zip(rows, metas, strict=True):
        meta = m if isinstance(m, dict) else None
        if activity_sources and not row["last_activity_ms"]:
            jobs.append(fill_archive(row))
        want_pending = check_pending == "all" or (
            check_pending == "auto"
            and meta is not None
            and (row["streaming"] or row["runtime_status"] or row["session_id"] in recent_attention)
        )
        if want_pending and _state_safe(row["agent_kind"], meta):
            jobs.append(fill_pending(row, meta))
    results = await _gather_limited(jobs, limit=4)
    if discovery is not None:
        discovery["enrichment_failures"] += sum(isinstance(r, Exception) for r in results)
    return rows


async def sessions_list(
    fleet: Fleet,
    host: str | None = None,
    workspace: str | None = None,
    agent: str | None = None,
    only_loaded: bool = False,
    active_within_hours: float | None = None,
    check_pending: str = "auto",
    activity_sources: bool = True,
    limit: int = 50,
) -> dict:
    if check_pending not in ("auto", "all", "none"):
        raise BatError("check_pending must be auto, all or none")
    names = _hosts(fleet, host)
    res = await asyncio.gather(
        *(_host_sessions(fleet, n, workspace, agent, check_pending, activity_sources) for n in names),
        return_exceptions=True,
    )
    rows, errors = [], {}
    for n, r in zip(names, res, strict=True):
        if isinstance(r, BaseException):
            errors[n] = _err(r)
        else:
            rows.extend(r)
    if only_loaded:
        rows = [r for r in rows if r["loaded"]]
    if active_within_hours is not None:
        cutoff = (time.time() - active_within_hours * 3600) * 1000
        rows = [r for r in rows if (r["last_activity_ms"] or 0) >= cutoff]
    rows.sort(key=lambda r: r["last_activity_ms"] or 0, reverse=True)
    total = len(rows)
    limit = max(1, min(500, int(limit)))
    rows = rows[:limit]
    for r in rows:
        ms = r.pop("last_activity_ms")
        r["last_activity"] = iso_local(ms)
        r["last_activity_age"] = _age(ms)
    return {"sessions": rows, "count": len(rows), "total_matched": total, "errors": errors}



# --------------------------------------------------------------------------- turn markers
# ``turn_marker`` is the exact clientMessageId echoed by BAT, independent of
# ``after_ms`` (a host-clock cursor for older callers). Never infer a timestamp
# from a UUID-shaped message id.


def marker_ms(after: Any) -> int | None:
    """Parse an ``after`` marker: epoch ms (int/str), a message id ending in "-<ms>", or ISO-8601."""
    if after is None or after == "":
        return None
    if isinstance(after, bool):
        raise BatError("after must be a turn_marker, epoch ms or ISO-8601 time")
    if isinstance(after, (int, float)):
        return ts_to_ms(after)
    s = str(after).strip()
    if s.isdigit():
        return ts_to_ms(int(s))
    tail = s.rsplit("-", 1)[-1] if "-" in s else ""
    if tail.isdigit() and len(tail) >= 10:
        return ts_to_ms(int(tail))
    try:
        return int(parse_datetime(s).timestamp() * 1000)
    except ValueError:
        raise BatError(f"cannot parse after={s!r}: pass the turn_marker from session_relay/session_send") from None


def _msg_ms(m: dict) -> int:
    return ts_to_ms(m.get("timestamp") or m.get("completedAt")) or 0


def _cursor(after: Any, messages: list[dict], *, exact_id: bool = False) -> dict | None:
    if after is None or after == "":
        return None
    if isinstance(after, str) and (after.startswith("batc-") or exact_id):
        echo = next((m for m in messages if m.get("id") == after and _is_user(m)), None)
        ms = _msg_ms(echo) if echo else None
        return {"message_id": after, "after_ms": ms, "iso": iso_local(ms), "echo_found": echo is not None}
    ms = marker_ms(after)
    return {"message_id": None, "after_ms": ms, "iso": iso_local(ms), "echo_found": True}


def _messages_after(messages: list[dict], cursor: dict) -> list[dict]:
    if cursor["message_id"]:
        if not cursor["echo_found"]:
            return []
        pos = next(i for i, m in enumerate(messages) if m.get("id") == cursor["message_id"] and _is_user(m))
        return messages[pos + 1:]
    return [m for m in messages if _msg_ms(m) > cursor["after_ms"]]


def _is_user(m: dict) -> bool:
    return m.get("role") == "user" or m.get("type") == "user"


async def _live_state(c: BatClient, sid: str, kind: str | None, meta: dict | None) -> dict | None:
    if not _state_safe(kind, meta):
        return None
    st = await c.invoke("claude:get-session-state", {"sessionId": sid})
    return st if isinstance(st, dict) else None


def _progress_after(state: dict | None, meta: dict | None, cursor: dict, turn: dict | None = None) -> dict:
    """What the session has produced since the marker (live messages only)."""
    msgs = [m for m in (state or {}).get("messages") or [] if isinstance(m, dict)]
    new = _messages_after(msgs, cursor)
    replies = [m for m in new if not _is_user(m)]
    streaming = bool((state or {}).get("isStreaming") or (meta or {}).get("isStreaming"))
    if cursor["message_id"]:
        attribution = "correlated" if cursor["echo_found"] else "echo_not_visible"
    else:
        attribution = "timestamp_cursor"
    if turn and turn.get("queued"):
        baseline = turn.get("baseline_turns")
        turns = (meta or {}).get("numTurns")
        boundary = turn.get("boundary_ms")
        if isinstance(baseline, int) and isinstance(turns, int) and turns >= baseline + 1:
            if not boundary and turns == baseline + 1:
                boundary = max((_msg_ms(m) for m in new), default=cursor["after_ms"] or 0)
                registry.update_turn_boundary(turn["host"], turn["session_id"], turn["message_id"], boundary)
            if boundary and turns >= baseline + 2:
                replies = [m for m in replies if _msg_ms(m) > boundary]
                attribution = "correlated_after_prior_turn"
            elif boundary and turns == baseline + 1 and turn.get("boundary_ms"):
                replies = [m for m in replies if _msg_ms(m) > boundary]
                attribution = "correlated_after_prior_turn" if replies else "queued_unconfirmed"
            else:
                replies = []
                attribution = "queued_unconfirmed"
        else:
            replies = []
            attribution = "queued_unconfirmed"
    started = bool(replies)
    done = (not streaming) and started and attribution == "correlated_after_prior_turn" if turn and turn.get("queued") else (not streaming) and started
    return {
        "streaming": streaming,
        "new_items": len(new),
        # streaming alone proves nothing: a queued send waits behind the previous turn, which also streams
        "started": started,
        "done": done,
        "phase": "terminal" if done else "running" if started else "accepted",
        "attribution": attribution,
    }


async def _find_turn_marker(
    c: BatClient, sid: str, kind: str | None, message_id: str, baseline_ms: int | None, tries: int = 6
) -> dict:
    """Locate BAT's exact user echo; keep the message id even if the echo is delayed."""
    if kind == "codex":
        # BAT 5a61d43's Codex router ignores clientMessageId and emits user-<time>.
        # Preserve the old cursor contract, explicitly marked as weaker.
        return {"turn_marker": str(baseline_ms) if baseline_ms else None,
                "after_ms": baseline_ms, "after": iso_local(baseline_ms),
                "marker_source": "codex_timestamp_fallback"}
    for i in range(tries):
        try:
            meta = await _meta(c, sid)
            st = await _live_state(c, sid, kind, meta)
        except BatError:
            st = None
        for m in reversed([m for m in (st or {}).get("messages") or [] if isinstance(m, dict)]):
            if _is_user(m) and m.get("id") == message_id:
                ms = _msg_ms(m)
                return {"turn_marker": message_id, "after_ms": ms, "after": iso_local(ms),
                        "marker_source": "sent_message"}
        if i + 1 < tries:
            await asyncio.sleep(0.25)
    return {"turn_marker": message_id, "after_ms": baseline_ms, "after": iso_local(baseline_ms),
            "marker_source": "pending_echo"}


async def session_read(
    fleet: Fleet,
    host: str,
    session_id: str,
    last_n: int = 20,
    offset: int = 0,
    include_tools: bool = False,
    max_chars: int = 12_000,
    max_message_chars: int = 2_000,
    after: Any = None,
    index_reading: bool = False,
    *,
    turn_slice: bool = False,
) -> dict:
    last_n = max(1, min(MAX_LAST_N, int(last_n)))
    offset = max(0, int(offset))
    max_chars = max(500, min(MAX_READ_CHARS, int(max_chars)))
    max_message_chars = max(100, min(max_chars, int(max_message_chars)))
    c = fleet.client(host)
    t, ws = await _resolve_session(c, session_id)
    sid = t["id"]
    turn = registry.get_turn(host, sid, after) if isinstance(after, str) else None
    after_ms = None if isinstance(after, str) and (after.startswith("batc-") or turn) else marker_ms(after)
    kind = agent_kind(t.get("agentPreset"))
    meta = await _meta(c, sid)
    state = None
    if _state_safe(kind, meta):
        st = await c.invoke("claude:get-session-state", {"sessionId": sid})
        state = st if isinstance(st, dict) else None
    live = [m for m in (state or {}).get("messages") or [] if isinstance(m, dict)]
    live_ids = {m.get("id") for m in live}

    newest_first = list(reversed(live))
    want = offset + last_n
    archive_total = None
    arch_off = 0
    archive_complete = False
    archive_reason = None
    raw_budget = 3000
    def need_archive() -> bool:
        if index_reading:
            return not archive_complete
        if isinstance(after, str) and (after.startswith("batc-") or turn):
            return not any(m.get("id") == after and _is_user(m) for m in newest_first)
        visible = [m for m in newest_first if after_ms is None or _msg_ms(m) > after_ms]
        return (sum(summarize_message(m, include_tools=include_tools, max_chars=50) is not None
                    for m in visible) < want and
                not (after_ms is not None and newest_first and _msg_ms(newest_first[-1]) <= after_ms))

    while need_archive() and raw_budget > 0:
        chunk = min(200, raw_budget)
        r = await c.invoke("claude:load-archived", {"sessionId": sid, "offset": arch_off, "limit": chunk})
        if not isinstance(r, dict):
            archive_reason = "archive_unconfirmed"
            break
        archive_total = r.get("total", archive_total)
        msgs = [m for m in r.get("messages") or [] if isinstance(m, dict)]
        if not msgs:
            archive_complete = r.get("hasMore") is False
            archive_reason = None if archive_complete else "archive_unconfirmed"
            break
        for m in reversed(msgs):
            if m.get("id") not in live_ids:
                newest_first.append(m)
        arch_off += len(msgs)
        raw_budget -= len(msgs)
        if not r.get("hasMore"):
            archive_complete = r.get("hasMore") is False
            archive_reason = None if archive_complete else "archive_unconfirmed"
            break
    if not archive_complete and archive_reason is None and raw_budget <= 0:
        archive_reason = "archive_budget"

    chronological = list(reversed(newest_first))
    cursor = _cursor(after, chronological, exact_id=turn is not None)
    selected = _messages_after(chronological, cursor) if cursor else chronological
    turn = registry.get_turn(host, sid, after) if cursor and cursor["message_id"] else None
    if turn and turn.get("queued"):
        progress = _progress_after({"messages": chronological, "isStreaming": (state or {}).get("isStreaming")}, meta, cursor, turn)
        if progress["attribution"] != "correlated_after_prior_turn":
            selected = []
        else:
            selected = [m for m in selected if _msg_ms(m) > turn.get("boundary_ms", 0)]
    slice_info: dict[str, Any] = {}
    if turn_slice:
        slice_items = [
            s
            for m in selected
            if (s := summarize_message(m, include_tools=True, max_chars=DIGEST_TEXT_MAX_CHARS)) is not None
        ]
        slice_complete = len(slice_items) <= TURN_SLICE_MAX_ITEMS
        if not slice_complete:
            slice_items = slice_items[-TURN_SLICE_MAX_ITEMS:]
        slice_info["_turn_slice"] = {
            "items": slice_items,
            "complete": slice_complete,
            "reason": None if slice_complete else "turn_slice_bounded",
        }
    visible = [m for m in reversed(selected) if summarize_message(m, include_tools=include_tools, max_chars=50) is not None]
    page = visible[offset : offset + last_n]
    out_msgs: list[dict] = []
    used = 0
    truncated = False
    for m in page:  # newest first, so the size cap keeps the most recent messages
        s = summarize_message(m, include_tools=include_tools, max_chars=max_message_chars)
        if s is None:
            continue
        size = len(s.get("text") or "") + 80
        if used + size > max_chars and out_msgs:
            truncated = True
            break
        used += size
        out_msgs.append(s)
    out_msgs.reverse()
    has_more = len(visible) > offset + len(out_msgs) or bool(archive_total and arch_off < archive_total)
    pending = None
    if state:
        pending = summarize_pending(state.get("pendingAskUser"), "ask_user") or summarize_pending(
            state.get("pendingPermission"), "permission"
        )
    w = next((x for x in ws.get("workspaces") or [] if x.get("id") == t.get("workspaceId")), {})
    m = meta or {}
    since: dict[str, Any] = {}
    if cursor is not None:
        prog = _progress_after({"messages": chronological, "isStreaming": (state or {}).get("isStreaming")}, meta, cursor, turn)
        since = {
            "after": cursor,
            "turn_started": prog["started"],
            "turn_done": prog["done"],
            "turn_attribution": prog["attribution"],
            "turn_phase": prog["phase"],
            "note": (
                "only output newer than the marker is shown"
                if out_msgs
                else "no confirmed output for this turn yet; do NOT report older messages as its result"
            ),
        }
        if not prog["started"]:
            since["streaming_text_tail_hidden"] = True  # the live tail may still be the previous turn
            pending = None  # a blocked prompt may also belong to the previous turn
    reading_index = {}
    if index_reading:
        from .session_reading import index_messages
        reading_index["_reading_index"] = index_messages(
            chronological, complete=archive_complete, include_tools=include_tools, archive_reason=archive_reason
        )
    return {
        **reading_index,
        **slice_info,
        **since,
        "host": host,
        "session_id": sid,
        "workspace": w.get("name"),
        "title": t.get("title"),
        "agent_kind": kind,
        **confinement.session_fields(host, sid, meta, account=confinement.account_status(fleet, host)),
        "loaded": meta is not None,
        "streaming": bool((state or {}).get("isStreaming") or m.get("isStreaming")),
        "streaming_text_tail": None
        if since.get("streaming_text_tail_hidden")
        else clip((state or {}).get("streamingText") or "", 1000)[-1000:] or None,
        "pending": pending,
        "meta": {
            "model": m.get("model") or t.get("model"),
            "num_turns": m.get("numTurns"),
            "context_tokens": m.get("contextTokens"),
            "context_window": m.get("contextWindow"),
            "last_data_at": iso_local(ts_to_ms(m.get("lastDataAt"))),
            "cwd": m.get("cwd") or t.get("cwd"),
        },
        "messages": out_msgs,
        "offset": offset,
        "next_offset": offset + len(out_msgs) if has_more else None,
        "truncated_by_size": truncated,
        "live_items": len(live),
        "archived_total": archive_total,
        "include_tools": include_tools,
    }


WAIT_SETS = {
    "attention": {"agent:turn-end", "agent:ask-user", "agent:permission-request", "agent:error"},
    "turn-end": {"agent:turn-end"},
    "ask-user": {"agent:ask-user", "agent:permission-request"},
}


async def session_wait(
    fleet: Fleet,
    host: str,
    session_id: str,
    until: str = "attention",
    timeout_s: float = 120,
    require_new: bool = False,
    after: Any = None,
) -> dict:
    if until not in WAIT_SETS:
        raise BatError(f"until must be one of {', '.join(WAIT_SETS)}")
    timeout_s = max(1.0, min(1800.0, float(timeout_s)))
    c = fleet.client(host)
    t, _ = await _resolve_session(c, session_id)
    sid = t["id"]
    kind = agent_kind(t.get("agentPreset"))
    channels = WAIT_SETS[until]
    sub = c.subscribe(lambda ev: ev.get("channel") in channels and event_session_id(ev) == sid, maxsize=64)
    t0 = time.monotonic()
    try:
        meta = await _meta(c, sid)
        if after is not None and after != "":
            return await _wait_after(c, sid, kind, host, until, after, timeout_s, sub, t0)
        if not require_new:
            if until in ("attention", "turn-end") and not (meta or {}).get("isStreaming"):
                return {
                    "host": host,
                    "session_id": sid,
                    "status": "idle",
                    "event": None,
                    "elapsed_s": 0.0,
                    "loaded": meta is not None,
                }
            if until in ("attention", "ask-user") and _state_safe(kind, meta):
                st = await c.invoke("claude:get-session-state", {"sessionId": sid})
                if isinstance(st, dict):
                    p = summarize_pending(st.get("pendingAskUser"), "ask_user") or summarize_pending(
                        st.get("pendingPermission"), "permission"
                    )
                    if p:
                        return {
                            "host": host,
                            "session_id": sid,
                            "status": "pending",
                            "event": None,
                            "pending": p,
                            "elapsed_s": round(time.monotonic() - t0, 1),
                        }
        while True:
            remaining = timeout_s - (time.monotonic() - t0)
            if remaining <= 0:
                return {
                    "host": host,
                    "session_id": sid,
                    "status": "timeout",
                    "event": None,
                    "elapsed_s": round(time.monotonic() - t0, 1),
                }
            c.last_used = time.monotonic()  # keep the idle reaper away while waiting
            try:
                ev = await sub.get(timeout=min(10.0, remaining))
            except ConnectionLost:
                sub.reopen()
                ev = None
            if ev is not None:
                return {
                    "host": host,
                    "session_id": sid,
                    "status": "event",
                    "event": ev.get("channel"),
                    "elapsed_s": round(time.monotonic() - t0, 1),
                }
            if not c.connected:
                # reconnect; events during the gap are lost, so re-check the live state
                try:
                    await c.connect()
                except (BatError, OSError, asyncio.TimeoutError) as exc:
                    log_error("bat-session", "session.reconnect.failed", exc)
                    raise
                meta = await _meta(c, sid)
                if until in ("attention", "turn-end") and not (meta or {}).get("isStreaming"):
                    return {
                        "host": host,
                        "session_id": sid,
                        "status": "idle",
                        "event": None,
                        "note": "reconnected; session no longer streaming",
                        "elapsed_s": round(time.monotonic() - t0, 1),
                    }
    finally:
        c.unsubscribe(sub)


async def _wait_after(
    c: BatClient, sid: str, kind: str | None, host: str, until: str, after: Any, timeout_s: float, sub, t0: float
) -> dict:
    """Wait for output correlated with an exact echo or a legacy timestamp cursor."""
    turn = registry.get_turn(host, sid, after) if isinstance(after, str) else None
    marker: dict = {}

    async def check() -> dict:
        meta = await _meta(c, sid)
        state = await _live_state(c, sid, kind, meta)
        cursor = _cursor(after, [m for m in (state or {}).get("messages") or [] if isinstance(m, dict)],
                         exact_id=turn is not None)
        marker.update(cursor or {})
        return _progress_after(state, meta, cursor, registry.get_turn(host, sid, after) if turn else None)

    def out(status: str, prog: dict, event: str | None = None, **extra: Any) -> dict:
        return {"host": host, "session_id": sid, "status": status, "event": event, "after": marker,
                "turn_started": prog["started"], "turn_done": prog["done"],
                "turn_attribution": prog["attribution"],
                "turn_phase": prog["phase"],
                "elapsed_s": round(time.monotonic() - t0, 1), **extra}

    prog = await check()
    if until in ("attention", "turn-end") and prog["done"]:
        return out("done", prog)
    if until in ("attention", "ask-user"):
        st = await c.invoke("claude:get-session-state", {"sessionId": sid}) if _state_safe(kind, await _meta(c, sid)) else None
        if isinstance(st, dict):
            p = summarize_pending(st.get("pendingAskUser"), "ask_user") or summarize_pending(
                st.get("pendingPermission"), "permission"
            )
            if p and prog["started"]:
                return out("pending", prog, pending=p)
    while True:
        remaining = timeout_s - (time.monotonic() - t0)
        if remaining <= 0:
            prog = await check()
            if until in ("attention", "turn-end") and prog["done"]:
                return out("done", prog)
            note = None if prog["started"] else (
                "the session has not started a turn after the marker (queued behind another turn, or not delivered)"
            )
            return out("timeout", prog, **({"note": note} if note else {}))
        c.last_used = time.monotonic()
        try:
            ev = await sub.get(timeout=min(10.0, remaining))
        except ConnectionLost:
            sub.reopen()
            ev = None
        if ev is not None:
            ch = ev.get("channel")
            if ch in ("agent:ask-user", "agent:permission-request", "agent:error"):
                prog = await check()
                if prog["started"]:
                    return out("event", prog, ch)
                continue
            prog = await check()
            if prog["done"]:
                return out("event", prog, ch)
            continue  # turn-end of an older turn, or the reply is not visible yet
        if not c.connected:
            await c.connect()
        prog = await check()
        if until in ("attention", "turn-end") and prog["done"]:
            return out("done", prog, note="reply after the marker found while polling")


# --------------------------------------------------------------------------- write tools
def _audit(fleet: Fleet) -> Audit:
    return Audit(fleet.config.safety)


def _guard(fleet: Fleet, host: str, confirm: bool) -> None:
    if not fleet.writes_enabled(host):
        raise WriteRefused(
            f"writes are disabled for host {host!r} (set writes = true in its config to enable)"
        )
    if confirm is not True:
        raise WriteRefused("write tools require confirm=true")


def _resume_params(t: dict, ws: dict) -> dict:
    cwd = str(t.get("cwd") or "").strip()
    if not cwd:
        raise WriteRefused("session is not loaded and its workspace record has no cwd; open it in BAT first")
    w = next((x for x in ws.get("workspaces") or [] if x.get("id") == t.get("workspaceId")), {})
    opts = {
        "cwd": cwd,
        "agentPreset": t.get("agentPreset"),
        "model": t.get("model"),
        "permissionMode": t.get("permissionMode"),
        "effort": t.get("effort"),
        "workspaceId": t.get("workspaceId"),
        "workspaceName": w.get("name"),
    }
    ap = t.get("agentParams") if isinstance(t.get("agentParams"), dict) else {}
    if agent_kind(t.get("agentPreset")) == "codex":
        opts["codexSandboxMode"] = ap.get("sandboxMode")
        opts["codexApprovalPolicy"] = ap.get("approvalPolicy")
    return {
        "sessionId": t["id"],
        "sdkSessionId": t.get("sdkSessionId"),
        "options": {k: v for k, v in opts.items() if v not in (None, "")},
    }


@task_control.guarded("send")
async def session_send(
    fleet: Fleet,
    host: str,
    session_id: str,
    text: str,
    confirm: bool = False,
    message_id: str | None = None,
    ensure_loaded: bool = True,
    queue: bool = False,
    tool: str = "session_send",
    retry_on_disconnect: bool = True,
    before_invoke: Callable[[], None] | None = None,
    initial_task_send: bool = False,
    _task_guard: task_control.FrameGuard | None = None,
    control_version: int | None = None,
    operation_id: str | None = None,
    _exact_session_id: bool = False,
    _before_frame: Callable[[], Awaitable[None]] | None = None,
    _operation_context=None,
    _controller_incarnation: str | None = None,
    _on_transport: Callable[[], None] | None = None,
    _on_streaming: Callable[[dict[str, Any]], None] | None = None,
    _on_unloaded: Callable[[], None] | None = None,
) -> dict:
    from . import orchestration_operations
    from .operations import OpContext
    relay = False
    if _operation_context is not None:
        if (not isinstance(_operation_context, OpContext) or _operation_context.operation_id != operation_id
                or _operation_context.op['action'] != 'session.send'
                or _operation_context.service.context.get('fleet') is not fleet):
            raise WriteRefused('invalid internal send operation context')
        relay = bool(orchestration_operations.linked(_operation_context.op))
        if relay:
            orchestration_operations.check_child(_operation_context)
    _guard(fleet, host, confirm)
    if not isinstance(text, str) or not text.strip():
        raise WriteRefused("text must be a non-empty string")
    if len(text) > MAX_PROMPT_CHARS:
        raise WriteRefused(f"text is longer than {MAX_PROMPT_CHARS} characters")
    c = fleet.client(host)
    audit = _audit(fleet)
    async with _write_lock(host):
        t, ws = await _resolve_session(c, session_id)
        sid = t["id"]
        if _exact_session_id and sid != session_id:
            raise TaskControlRefused("TASK_BINDING_MISMATCH", "the operation's session identity no longer resolves exactly")
        grant = await resource_policy.authorize_session(fleet, host, "session.send", t,
                                                        expected_incarnation=_controller_incarnation)
        successor = next((e for e in registry.list_entries(host) if e.get("failover_of") == sid
                          and e.get("status") in ("starting", "active")
                          and e.get("handoff_status") == "sent"), None)
        if successor:
            raise WriteRefused(f"session was handed off to {successor['session_id']}; send there instead")
        kind = agent_kind(t.get("agentPreset"))
        mid = message_id or f"batc-{uuid.uuid4()}"
        if initial_task_send:
            owner = registry.get(host, sid)
            if (not owner or not owner.get("task_id")
                    or owner.get("role") not in {"lead", "reviewer"}):
                raise WriteRefused("initial task send requires a task-owned session")
        if not (kind == "claude" and message_id and audit.same_send_retry(host, sid, mid, text)):
            audit.check_rate(host, sid, initial_task_send=initial_task_send)
        meta = await _meta(c, sid)
        resumed = False
        if meta is None:
            if not ensure_loaded:
                if _on_unloaded is not None:
                    _on_unloaded()
                raise WriteRefused(
                    "session is not loaded on the host (pass ensure_loaded=true to client-resume it)"
                )
            params = _resume_params(t, ws)
            original = confinement.resume_options(host, sid, kind or "claude")
            if original:
                params["options"].update(original)
            audit.record(
                actor=fleet.actor,
                tool=tool,
                host=host,
                session_id=sid,
                channel="claude:client-resume",
                phase="attempt",
            )
            try:
                # Reattach checks the task binding without submitting its prompt.
                async def resume_frame():
                    if relay:
                        await orchestration_operations.before_send(_operation_context, c, resume=True)
                def resume_gate():
                    if relay:
                        orchestration_operations.check_child(_operation_context)
                    if _task_guard:
                        _task_guard.check()
                await c.invoke("claude:client-resume", params, grant=grant,
                               before_send=resume_gate if relay else (_task_guard.check if _task_guard else None),
                               **({"before_frame": resume_frame} if relay else {}),
                               frame_guard=lambda frame: confinement.guard_resume_frame(host, sid, kind or "claude", frame))
            except BatError as e:
                log_error("bat-session", "session.resume.failed", e)
                audit.record(
                    actor=fleet.actor,
                    tool=tool,
                    host=host,
                    session_id=sid,
                    channel="claude:client-resume",
                    phase="result",
                    ok=False,
                    error=_err(e),
                )
                raise
            resumed = True
            meta = await _meta(c, sid)
        confinement.guard_loaded(host, sid, meta)
        if (meta or {}).get("isStreaming") and not queue:
            if _on_streaming is not None:
                _on_streaming(meta)
            raise WriteRefused(
                "session is currently streaming a turn; pass queue=true to queue the message behind it"
            )
        baseline_ms: int | None = None
        try:
            st0 = await _live_state(c, sid, agent_kind(t.get("agentPreset")), meta)
            baseline_ms = max((_msg_ms(m) for m in (st0 or {}).get("messages") or [] if isinstance(m, dict)),
                              default=None)
        except BatError:
            baseline_ms = None
        audit.record(
            actor=fleet.actor,
            tool=tool,
            host=host,
            session_id=sid,
            channel="claude:send-message",
            message_id=mid,
            phase="attempt",
            text=text,
        )
        async def verify_at_frame() -> None:
            if relay:
                await orchestration_operations.before_send(_operation_context, c)
            if _before_frame:
                await _before_frame()
            entry = registry.get(host, sid) or {}
            if entry.get("write_scope") == "confined" and entry.get("confinement"):
                observed = await c.guard_read("claude:get-session-meta", {"sessionId": sid})
                confinement.guard_loaded(host, sid, observed)

        def send_gate():
            if relay:
                orchestration_operations.check_child(_operation_context)
            if _task_guard:
                _task_guard()
            elif before_invoke:
                before_invoke()
        def transported():
            if relay:
                _operation_context.relay_send_transported = True
            if _on_transport is not None:
                _on_transport()
        try:
            if before_invoke:
                before_invoke()
            r = await c.invoke(
                "claude:send-message", {"sessionId": sid, "prompt": text, "clientMessageId": mid},
                retry_on_disconnect=retry_on_disconnect and agent_kind(t.get("agentPreset")) == "claude",
                before_send=send_gate if relay else (_task_guard or before_invoke), before_frame=verify_at_frame, grant=grant,
                **({"on_transport": transported} if (relay or _on_transport is not None) else {}),
            )
        except BatError as e:
            audit.record(
                actor=fleet.actor,
                tool=tool,
                host=host,
                session_id=sid,
                channel="claude:send-message",
                message_id=mid,
                phase="result",
                ok=False,
                error=_err(e),
            )
            raise
        r = r if isinstance(r, dict) else {"result": r}
        if _task_guard and not isinstance(r.get("accepted", r.get("ok")), bool):
            raise ValueError("BAT send reply has no boolean acceptance result")
        audit.record(
            actor=fleet.actor,
            tool=tool,
            host=host,
            session_id=sid,
            channel="claude:send-message",
            message_id=mid,
            phase="result",
            ok=bool(r.get("ok", True)),
            queued=r.get("queued"),
        )
        accepted = bool(r.get("accepted", r.get("ok")))
        if accepted and agent_kind(t.get("agentPreset")) == "claude":
            registry.record_turn(host, sid, mid, queued=bool(r.get("queued")),
                                 baseline_turns=(meta or {}).get("numTurns"))
        turn_record = registry.get_turn(host, sid, mid) if accepted else None
        marker = await _find_turn_marker(c, sid, agent_kind(t.get("agentPreset")), mid, baseline_ms) if accepted else {
            "turn_marker": None, "after_ms": baseline_ms, "after": iso_local(baseline_ms),
            "marker_source": None,
        }
    return {
        "host": host,
        "session_id": sid,
        "message_id": mid,
        "accepted": r.get("accepted", r.get("ok")),
        "queued": r.get("queued") if r.get("queued") is not None else (turn_record or {}).get("queued"),
        "turn_phase": "accepted" if accepted else "rejected",
        "turn_attribution": "exact_echo" if marker["marker_source"] == "sent_message" else marker["marker_source"],
        "resumed": resumed,
        **marker,
        "note": (
            "BAT Codex ignores clientMessageId; this timestamp cursor is best effort and cannot prove turn ownership"
            if agent_kind(t.get("agentPreset")) == "codex" else
            "reuse message_id to retry safely; pass turn_marker as after= to session_wait/session_read"
        ),
    }


async def session_continue(
    fleet: Fleet,
    host: str,
    session_id: str,
    confirm: bool = False,
    text: str = "continue",
    queue: bool = False,
    message_id: str | None = None,
) -> dict:
    return await session_send(
        fleet,
        host,
        session_id,
        text,
        confirm=confirm,
        queue=queue,
        message_id=message_id,
        tool="session_continue",
    )


@task_control.guarded("interrupt")
async def session_interrupt(
    fleet: Fleet, host: str, session_id: str, mode: str = "soft", confirm: bool = False,
    _task_guard: task_control.FrameGuard | None = None, control_version: int | None = None,
    operation_id: str | None = None,
    _exact_session_id: bool = False,
    _controller_incarnation: str | None = None,
) -> dict:
    _guard(fleet, host, confirm)
    if mode not in ("soft", "hard"):
        raise WriteRefused("mode must be soft or hard")
    c = fleet.client(host)
    audit = _audit(fleet)
    async with _write_lock(host):
        t, _ = await _resolve_session(c, session_id)
        sid = t["id"]
        if _exact_session_id and sid != session_id:
            raise TaskControlRefused("TASK_BINDING_MISMATCH", "the operation's session identity no longer resolves exactly")
        grant = await resource_policy.authorize_session(fleet, host, "session.interrupt", t,
                                                        expected_incarnation=_controller_incarnation)
        kind = agent_kind(t.get("agentPreset"))
        audit.check_rate(host, sid + "#interrupt")
        channel = "claude:interrupt-turn" if (mode == "soft" and kind == "claude") else "claude:abort-session"
        audit.record(
            actor=fleet.actor,
            tool="session_interrupt",
            host=host,
            session_id=sid + "#interrupt",
            channel=channel,
            mode=mode,
            phase="attempt",
        )
        try:
            r = await c.invoke(channel, {"sessionId": sid}, grant=grant, before_send=_task_guard)
        except BatError as e:
            audit.record(
                actor=fleet.actor,
                tool="session_interrupt",
                host=host,
                session_id=sid + "#interrupt",
                channel=channel,
                phase="result",
                ok=False,
                error=_err(e),
            )
            raise
        audit.record(
            actor=fleet.actor,
            tool="session_interrupt",
            host=host,
            session_id=sid + "#interrupt",
            channel=channel,
            phase="result",
            ok=True,
        )
    note = None
    if mode == "soft" and kind != "claude":
        note = "Codex has no soft interrupt; used abort-session (turn interrupt, session stays)"
    return {"host": host, "session_id": sid, "mode": mode, "channel": channel, "result": r, "note": note}


@task_control.guarded("answer")
async def session_answer(
    fleet: Fleet,
    host: str,
    session_id: str,
    confirm: bool = False,
    answers: dict[str, str] | list[str] | None = None,
    permission: str | None = None,
    deny_message: str | None = None,
    tool_use_id: str | None = None,
    dont_ask_again: bool = False,
    _task_guard: task_control.FrameGuard | None = None,
    control_version: int | None = None,
    operation_id: str | None = None,
    _exact_session_id: bool = False,
    _operation_context=None,
    _controller_incarnation: str | None = None,
) -> dict:
    from . import bulk_approval
    from .operations import AmbiguousOutcome, OpContext, StepFailed
    bulk = False
    if _operation_context is not None:
        if (not isinstance(_operation_context, OpContext) or _operation_context.operation_id != operation_id
                or _operation_context.op["action"] != "session.answer"
                or _operation_context.service.context.get("fleet") is not fleet):
            raise WriteRefused("invalid internal answer operation context")
        bulk = bool(bulk_approval.linked(_operation_context.op))
        if bulk:
            bulk_approval.check_child(_operation_context)
    _guard(fleet, host, confirm)
    if (answers is None) == (permission is None):
        raise WriteRefused("pass exactly one of answers (for ask-user) or permission (allow|deny)")
    c = fleet.client(host)
    audit = _audit(fleet)
    async with _write_lock(host):
        t, _ = await _resolve_session(c, session_id)
        sid = t["id"]
        if _exact_session_id and sid != session_id:
            raise TaskControlRefused("TASK_BINDING_MISMATCH", "the operation's session identity no longer resolves exactly")
        grant = await resource_policy.authorize_session(fleet, host, "session.answer", t,
                                                        expected_incarnation=_controller_incarnation)
        kind = agent_kind(t.get("agentPreset"))
        if bulk and kind != bulk_approval.check_child(_operation_context)["agent_kind"]:
            raise TaskControlRefused("BULK_BINDING_CHANGED", "reviewed agent kind changed")
        meta = await _meta(c, sid)
        if not _state_safe(kind, meta):
            raise WriteRefused("session is not loaded on the host; nothing to answer")
        st = await c.invoke("claude:get-session-state", {"sessionId": sid})
        st = st if isinstance(st, dict) else {}
        audit.check_rate(host, sid + "#answer")
        if answers is not None:
            pend = st.get("pendingAskUser")
            if not isinstance(pend, dict):
                raise WriteRefused("session has no pending ask-user question")
            tuid = pend.get("toolUseId")
            if tool_use_id and tool_use_id != tuid:
                raise WriteRefused("tool_use_id does not match the pending question")
            questions = [
                str(q.get("question", "")) for q in pend.get("questions") or [] if isinstance(q, dict)
            ]
            if isinstance(answers, list):
                if len(answers) > len(questions):
                    raise WriteRefused(f"got {len(answers)} answers for {len(questions)} questions")
                amap = {questions[i] or str(i): str(a) for i, a in enumerate(answers)}
            elif isinstance(answers, dict):
                amap = {}
                for k, v in answers.items():
                    if k in questions:
                        amap[k] = str(v)
                    elif k.isdigit() and int(k) < len(questions):
                        amap[questions[int(k)] or k] = str(v)
                    else:
                        raise WriteRefused("answer keys must be question texts or 0-based question indexes")
            else:
                raise WriteRefused("answers must be a list or an object")
            channel, params = (
                "claude:resolve-ask-user",
                {"sessionId": sid, "toolUseId": tuid, "answers": amap},
            )
            detail = {"questions": len(questions), "answered": len(amap)}
        else:
            pend = st.get("pendingPermission")
            if not isinstance(pend, dict):
                raise WriteRefused("session has no pending permission request")
            tuid = pend.get("toolUseId")
            if tool_use_id and tool_use_id != tuid:
                raise WriteRefused("tool_use_id does not match the pending permission request")
            confinement.guard_answer(host, sid, pend.get("toolName"),
                                     dont_ask_again=dont_ask_again, allow=permission == "allow")
            if permission == "allow":
                result = {"behavior": "allow", "updatedInput": pend.get("input")}
                if dont_ask_again:
                    result["dontAskAgain"] = True  # Codex: accept for the rest of the session
            elif permission == "deny":
                result = {
                    "behavior": "deny",
                    "message": (
                        deny_message
                        or "The user doesn't want to proceed with this tool use. "
                        "Stop and wait for further instructions."
                    )[:2000],
                }
            else:
                raise WriteRefused("permission must be allow or deny")
            channel, params = (
                "claude:resolve-permission",
                {"sessionId": sid, "toolUseId": tuid, "result": result},
            )
            detail = {"permission": permission, "permission_tool": pend.get("toolName")}
        audit.record(
            actor=_operation_context.actor if bulk else fleet.actor,
            tool="session_answer",
            host=host,
            session_id=sid + "#answer",
            channel=channel,
            phase="attempt",
            **detail,
        )
        transported = False
        def sent():
            nonlocal transported
            transported = True
            _operation_context.bulk_answer_transported = True
            if _task_guard:
                _task_guard.frames.append(True)
        def final_guard():
            if _task_guard:
                _task_guard.check()
            bulk_approval.check_child(_operation_context)
        async def before_frame():
            await bulk_approval.before_answer(_operation_context, c)
            final_guard()
        try:
            r = await c.invoke(channel, params, grant=grant,
                               before_send=final_guard if bulk else _task_guard,
                               before_frame=before_frame if bulk else None,
                               on_transport=sent if bulk else None,
                               frame_guard=lambda _: confinement.guard_answer(
                                   host, sid, detail.get("permission_tool"),
                                   dont_ask_again=dont_ask_again, allow=permission == "allow"))
            if bulk and r is not True:
                audit.record(actor=_operation_context.actor, tool="session_answer", host=host,
                             session_id=sid + "#answer", outcome="uncertain", phase="result", **detail)
                raise AmbiguousOutcome("bulk answer did not return a literal true ACK")
        except (BatError, OSError, asyncio.TimeoutError) as e:
            if bulk:
                audit.record(actor=_operation_context.actor, tool="session_answer", host=host,
                             session_id=sid + "#answer", outcome="uncertain" if transported else "not_sent",
                             phase="result", **detail)
                if transported:
                    raise AmbiguousOutcome("bulk answer was sent without a proven ACK") from e
                if isinstance(e, (ConnectionLost, InvokeTimeout, OSError, asyncio.TimeoutError)):
                    raise StepFailed("BULK_NOT_SENT", "bulk answer was not sent; review connectivity before a new request") from e
            if not isinstance(e, InvokeError):
                raise
            audit.record(
                actor=_operation_context.actor if bulk else fleet.actor,
                tool="session_answer",
                host=host,
                session_id=sid + "#answer",
                channel=channel,
                phase="result",
                ok=False,
                error=_err(e),
            )
            raise
        audit.record(
            actor=_operation_context.actor if bulk else fleet.actor,
            tool="session_answer",
            host=host,
            session_id=sid + "#answer",
            channel=channel,
            phase="result",
            ok=True,
        )
    return {"host": host, "session_id": sid, "channel": channel, "tool_use_id": tuid, "result": r, **detail}
