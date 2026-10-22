"""Scoped central adapters for existing transcript/turn observation; no write authority."""
from __future__ import annotations

import asyncio
import contextlib
import json
import math
import os
import time
from collections import Counter
from urllib.parse import urlsplit

from . import api_auth, registry, service
from .errors import BatError, WriteRefused
from .fleet import Fleet
from .operations import OperationError

METHODS = {"session_read", "session_wait"}
READ_DEADLINE = 30.0
TRANSPORT_MARGIN = 5.0
REAUTH_S = 10.0
MAX_ACTIVE = 32
MAX_PER_ACTOR = 4
MAX_PER_HOST = 8
_COMMON = {"host", "session_id", "after"}
_READ = {"last_n", "offset", "include_tools", "max_chars", "max_message_chars"}
_WAIT = {"until", "timeout_s", "require_new"}


def invalid(message):
    raise OperationError("INVALID_REQUEST", message, 422)


def validate(method: str, params: dict) -> dict:
    """Validate before clients/frames; preserve the legacy numeric clamp behavior."""
    if method not in METHODS or not isinstance(params, dict):
        invalid("expected a session observation request")
    allowed = _COMMON | (_READ if method == "session_read" else _WAIT)
    if set(params) - allowed:
        invalid("unknown session observation field")
    out = dict(params)
    for name in ("host", "session_id"):
        value = out.get(name)
        if (not isinstance(value, str) or not value.strip() or len(value) > 512
                or any(ord(c) < 32 or ord(c) == 127 for c in value)):
            invalid(f"{name} must be a non-empty bounded string")
    out["session_id"] = out["session_id"].strip()
    if len(out["session_id"]) < 6:
        invalid("session_id must be a full ID or unique prefix of at least six characters")
    after = out.get("after")
    if after is not None and (not isinstance(after, str) or len(after) > 512
                              or any(ord(c) < 32 or ord(c) == 127 for c in after)):
        invalid("after must be a bounded turn marker string")
    if method == "session_read":
        for name, default, low, high in (("last_n", 20, 1, service.MAX_LAST_N),
                                          ("max_chars", 12000, 500, service.MAX_READ_CHARS),
                                          ("max_message_chars", 2000, 100, service.MAX_READ_CHARS),
                                          ("offset", 0, 0, 1_000_000)):
            value = out.get(name, default)
            if type(value) is not int:
                invalid(f"{name} must be an integer")
            if name == "offset" and not low <= value <= high:
                invalid("offset must be between 0 and 1000000")
            out[name] = min(max(value, low), high)
        flag = "include_tools"
    else:
        until = out.get("until", "attention")
        if not isinstance(until, str) or until not in service.WAIT_SETS:
            invalid("until must be attention, turn-end or ask-user")
        value = out.get("timeout_s", 120)
        if type(value) not in (int, float) or not -1e9 <= value <= 1e9 or not math.isfinite(value):
            invalid("timeout_s must be a finite number")
        out.update(until=until, timeout_s=max(1.0, min(1800.0, value)))
        flag = "require_new"
    if type(out.get(flag, False)) is not bool:
        invalid(f"{flag} must be a boolean")
    out[flag] = out.get(flag, False)
    return out


def query_params(method: str, host: str, sid: str, query: dict) -> dict:
    allowed = {"after"} | (_READ if method == "session_read" else _WAIT)
    if set(query) - allowed or any(len(v) != 1 for v in query.values()):
        invalid("unknown or repeated observation query parameter")
    params = {"host": host, "session_id": sid}
    for key, values in query.items():
        value = values[0]
        try:
            if key in {"last_n", "offset", "max_chars", "max_message_chars"}:
                value = int(value)
            elif key == "timeout_s":
                value = float(value)
            elif key in {"include_tools", "require_new"}:
                if value.lower() not in {"true", "false", "1", "0", "yes", "no"}:
                    invalid(f"{key} must be a boolean")
                value = value.lower() in {"true", "1", "yes"}
        except (ValueError, OverflowError):
            invalid(f"invalid observation query parameter {key}")
        params[key] = value
    return validate(method, params)


def deadline(method: str, params: dict) -> float:
    return READ_DEADLINE + (params["timeout_s"] if method == "session_wait" else 0)


async def client_request(method: str, *, token: str | None, entry: str, **params) -> dict:
    """Explicit credential, deadline-aware transport. Never selects an admin fallback."""
    if not token:
        raise WriteRefused("session observation requires this client's BATC_API_TOKEN")
    if not isinstance(token, str) or len(token) > 4096 or any(c in token for c in "\r\n"):
        raise WriteRefused("invalid BATC_API_TOKEN")
    normalized = validate(method, params)
    # A cancellable async HTTP client is necessary here: cancelling to_thread(urllib) leaves its
    # socket and the central wait alive until the potentially 30-minute transport timeout.
    from .task_daemon import DEFAULT_URL
    url = urlsplit(os.environ.get("BATC_TASK_URL", DEFAULT_URL))
    if (url.scheme != "http" or url.hostname not in {"127.0.0.1", "localhost", "::1"}
            or url.username or url.password or url.path != "/rpc" or url.query or url.fragment):
        raise ValueError("task daemon URL must be loopback (reach a remote daemon through SSH forwarding)")
    port = url.port or 80

    async def exchange():
        reader, writer = await asyncio.open_connection(url.hostname, port)
        try:
            data = json.dumps({"method": method, "params": {"entry": entry, **normalized}}).encode()
            host = f"[{url.hostname}]" if url.hostname == "::1" else url.hostname
            writer.write((f"POST /rpc HTTP/1.1\r\nHost: {host}:{port}\r\n"
                          f"Authorization: Bearer {token}\r\nContent-Type: application/json\r\n"
                          f"Content-Length: {len(data)}\r\nConnection: close\r\n\r\n").encode() + data)
            await writer.drain()
            head = await reader.readuntil(b"\r\n\r\n")
            if len(head) > 16384:
                raise ValueError("invalid observation response headers")
            lines = head.decode("ascii").split("\r\n")
            lengths = [int(line.split(":", 1)[1]) for line in lines[1:]
                       if line.lower().startswith("content-length:")]
            if (not lines[0].startswith("HTTP/1.1 ") or len(lengths) != 1
                    or not 0 < lengths[0] <= 2 * 1024 * 1024
                    or any(line.lower().startswith("transfer-encoding:") for line in lines[1:])):
                raise ValueError("invalid observation response framing")
            result = json.loads(await reader.readexactly(lengths[0]))
            if not isinstance(result, dict):
                raise ValueError("invalid observation response")
            if "error" in result:
                raise ValueError(str(result["error"]) + ": " + str(result.get("message") or ""))
            if not isinstance(result.get("result"), dict):
                raise ValueError("invalid observation result")
            return result["result"]
        finally:
            writer.close()
            with contextlib.suppress(Exception):
                await asyncio.wait_for(writer.wait_closed(), 3)

    try:
        return await asyncio.wait_for(exchange(), deadline(method, normalized) + TRANSPORT_MARGIN)
    except (OSError, asyncio.TimeoutError, asyncio.IncompleteReadError) as exc:
        raise BatError(f"central session observation unavailable ({type(exc).__name__}); no local fallback") from None


def authorizer(daemon, token: str, principal, *, browser_session: bool = False):
    def check():
        current = api_auth.authenticate(daemon.journal.db, token, daemon._admin_token, browser_session=browser_session)
        return (current is not None and current.allows("observe") and current == principal
                and current.credential_id == principal.credential_id)
    return check


class SessionObservation:
    def __init__(self, daemon):
        self.daemon = daemon
        self.active = 0
        self.by_actor = Counter()
        self.by_host = Counter()
        self._tasks = set()
        self._closed = False

    async def request(self, principal, method, params, *, check_authorization=None, reader=None, writer=None):
        if not principal.allows("observe"):
            raise OperationError("FORBIDDEN", "session observation needs observe scope", 403)
        params = validate(method, params)
        host, actor = params["host"], principal.actor
        if host not in self.daemon.fleet.config.hosts:
            raise OperationError("UNKNOWN_HOST", "host is not configured", 404)
        # Custom message IDs are valid exact markers only when their original turn is recorded.
        after = params.get("after")
        if after and not after.startswith("batc-"):
            known = any(registry.get_turn(host, row["session_id"], after)
                        for row in registry.find_prefix(host, params["session_id"]))
            if not known:
                try:
                    service.marker_ms(after)
                except (BatError, ValueError, OverflowError):
                    invalid("after must be a recorded turn marker or timestamp")

        def authorize():
            from .pm_source_lineage import require_session
            require_session(self.daemon.journal.db, principal, host, params["session_id"])
            if check_authorization is not None and not check_authorization():
                raise OperationError("FORBIDDEN", "observation credential expired, changed or was revoked", 403)

        authorize()
        if self._closed:
            raise OperationError("OBSERVATION_CLOSED", "central observation is stopping", 503)
        if (self.active >= MAX_ACTIVE or self.by_actor[actor] >= MAX_PER_ACTOR
                or self.by_host[host] >= MAX_PER_HOST):
            raise OperationError("OBSERVATION_BUSY", "too many active session observations", 429)
        fleet = Fleet(self.daemon.fleet.config, read_only=True, idle_timeout=0, actor=actor)
        fleet.confinement_journal = self.daemon.journal
        self.active += 1
        self.by_actor[actor] += 1
        self.by_host[host] += 1
        current = asyncio.current_task()
        self._tasks.add(current)
        work = hangup = None
        try:
            fn = service.session_read if method == "session_read" else service.session_wait
            if method == "session_read":
                from .session_reading import begin_observation
                read_generation = begin_observation(self.daemon.journal)
            kwargs = {**params, "index_reading": True} if method == "session_read" else params
            started = time.monotonic()
            work = asyncio.create_task(fn(fleet, **kwargs))
            hangup = asyncio.create_task(reader.read(1)) if reader is not None else None
            pending = {work, hangup} if hangup else {work}
            while True:
                remaining = started + deadline(method, params) - time.monotonic()
                if remaining <= 0:
                    raise asyncio.TimeoutError
                done, _ = await asyncio.wait(
                    pending, timeout=min(REAUTH_S, remaining), return_when=asyncio.FIRST_COMPLETED
                )
                if hangup is not None and hangup in done:
                    if writer is not None:
                        writer.close()
                    raise asyncio.CancelledError
                authorize()  # Including the last read, before returning any body.
                if work in done:
                    result = await work
                    from .pm_source_lineage import require_document
                    require_document(self.daemon.journal.db, principal, result)
                    if method == "session_read":
                        from .session_reading import observe
                        result = observe(self.daemon.journal, principal, result, generation=read_generation)
                    return result
        except asyncio.CancelledError:
            if writer is not None:
                writer.close()
            raise
        except asyncio.TimeoutError:
            raise OperationError("OBSERVATION_TIMEOUT", "session observation exceeded its wall deadline", 504) from None
        finally:
            for task in (work, hangup):
                if task is not None:
                    task.cancel()
            try:
                await asyncio.gather(*(task for task in (work, hangup) if task is not None), return_exceptions=True)
            finally:
                try:
                    await fleet.close()
                finally:
                    self.active -= 1
                    for counter, key in ((self.by_actor, actor), (self.by_host, host)):
                        counter[key] -= 1
                        if not counter[key]:
                            del counter[key]
                    self._tasks.discard(current)

    async def close(self):
        self._closed = True
        tasks = [task for task in self._tasks if task is not asyncio.current_task()]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
