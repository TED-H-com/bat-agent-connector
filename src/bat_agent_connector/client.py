"""Core async client for BAT's remote protocol (bat-remote/v2).

* TLS with a pinned SHA-256 certificate fingerprint (CA/hostname checks are
  replaced by the pin; a mismatch aborts before any token is sent).
* v2 auth frame with a stable deviceId.
* Request/response correlation, per-channel timeouts, a background reader that
  always drains the socket (the server revokes clients with 256 queued frames),
  bounded per-subscriber event queues (drop-oldest), reconnect with backoff.
* Channel allowlist enforced here, before a frame is written.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import itertools
import json
import logging
import random
import ssl
import time
import zlib
from collections import OrderedDict, deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import ConnectionClosed, InvalidHandshake

from . import PROTOCOL, __version__
from .channels import check_allowed, is_write, timeout_for
from .config import HostConfig
from .errors import (
    AuthError,
    ChannelNotAllowed,
    ConfigError,
    ConnectionLost,
    FingerprintMismatch,
    InvokeError,
    InvokeTimeout,
)
from .redact import redact
from .resource_policy import WriteGrant, check_grant

log = logging.getLogger("bat_agent_connector.client")

GZIP_MAGIC = b"BATGZIP1\0"
DEFAULT_MAX_FRAME = 48 * 1024 * 1024
BUSY_TEXT = "Remote server is busy"


async def _wait_with_timeout(awaitable, timeout):
    """Keep caller cancellation even when a reply arrives in the same loop turn.

    Python 3.10's wait_for can return an already completed child after the caller
    was cancelled (CPython #86296). That can leave a disconnected observation
    running for its full wait period. asyncio.wait preserves that cancellation.
    """
    future = asyncio.ensure_future(awaitable)
    try:
        done, _ = await asyncio.wait({future}, timeout=timeout)
        if not done:
            raise asyncio.TimeoutError
        return future.result()
    finally:
        if not future.done():
            future.cancel()
        await asyncio.gather(future, return_exceptions=True)


def cert_fingerprint(der: bytes) -> str:
    return hashlib.sha256(der).hexdigest().upper()


@dataclass(eq=False)
class EventSubscription:
    """Bounded queue of events. The reader never blocks on it: when full the oldest event is dropped."""

    predicate: Callable[[dict], bool] | None = None
    maxsize: int = 512
    queue: deque = field(default_factory=deque)
    dropped: int = 0
    _signal: asyncio.Event = field(default_factory=asyncio.Event)

    recent_ids: OrderedDict = field(default_factory=OrderedDict)
    dedupe_limit: int = 2048
    _closed: ConnectionLost | None = None

    def close(self, error: ConnectionLost) -> None:
        self._closed = error
        self._signal.set()

    def reopen(self) -> None:
        """Resume events after the consumer has observed a connection closure."""
        self._closed = None

    def offer(self, ev: dict) -> None:
        if self.predicate and not self.predicate(ev):
            return
        payloads = [ev]
        if isinstance(ev.get("params"), dict):
            payloads.append(ev["params"])
        if isinstance(ev.get("args"), list):
            payloads.extend(arg for arg in ev["args"] if isinstance(arg, dict))
        identity = next(((key, payload[key]) for payload in payloads
                         for key in ("eventId", "messageId", "id")
                         if isinstance(payload.get(key), str) and payload[key]), None)
        if identity is not None:
            identity = (ev.get("channel"), event_session_id(ev), *identity)
            if identity in self.recent_ids:
                self.recent_ids.move_to_end(identity)
                return
            self.recent_ids[identity] = None
            while len(self.recent_ids) > self.dedupe_limit:
                self.recent_ids.popitem(last=False)
        if len(self.queue) >= self.maxsize:
            self.queue.popleft()
            self.dropped += 1
        self.queue.append(ev)
        self._signal.set()

    async def get(self, timeout: float | None = None) -> dict | None:
        deadline = None if timeout is None else time.monotonic() + timeout
        while not self.queue:
            if self._closed is not None:
                raise self._closed
            self._signal.clear()
            remaining = None if deadline is None else deadline - time.monotonic()
            if remaining is not None and remaining <= 0:
                return None
            try:
                await _wait_with_timeout(self._signal.wait(), remaining)
            except asyncio.TimeoutError:
                return None
        return self.queue.popleft()


def event_session_id(ev: dict) -> str | None:
    p = ev.get("params")
    if isinstance(p, dict) and isinstance(p.get("sessionId"), str):
        return p["sessionId"]
    args = ev.get("args")
    if isinstance(args, list) and args:
        a0 = args[0]
        if isinstance(a0, str):
            return a0
        if isinstance(a0, dict) and isinstance(a0.get("sessionId"), str):
            return a0["sessionId"]
    return None


# Ids the wire may carry. PROTOCOL §5 documents none of these on agent:error;
# they are kept only when that event's params actually include them.
_AGENT_ERROR_ID_KEYS = ("clientMessageId", "messageId", "turnId")


def _agent_error_record(host: str, obj: dict) -> dict:
    """Record an agent:error without inventing a turn id.

    The stored event is channel, sessionId, receive time, and the registry
    generation observed at that moment. clientMessageId, messageId, and turnId
    are copied only when the params contain a non-empty string. A missing
    session or generation is left missing so a later reader cannot treat the
    event as attributed.
    """
    record: dict[str, Any] = {
        "channel": "agent:error",
        "sessionId": event_session_id(obj),
        "at": time.time(),
    }
    params = obj.get("params") if isinstance(obj.get("params"), dict) else {}
    for key in _AGENT_ERROR_ID_KEYS:
        value = params.get(key)
        if isinstance(value, str) and value:
            record[key] = value
    session_id = record["sessionId"]
    if isinstance(session_id, str) and session_id:
        from . import registry as registry_mod

        entry = registry_mod.get(host, session_id)
        if isinstance(entry, dict):
            inc = entry.get("controller_incarnation")
            created_at = entry.get("created_at")
            if inc is not None and created_at is not None and str(inc) and str(created_at):
                record["source_generation"] = f"{inc}:{created_at}"
    return record


async def _retry_pause(delay_s: float) -> None:
    """Pause between this client's own retries. Tests replace only this function."""
    await asyncio.sleep(delay_s)


class ReconnectBackoff:
    """Full jitter; short successful connections do not erase failure history."""

    def __init__(self, *, rng=random.uniform, clock=time.monotonic, stable_s=60.0):
        self.rng, self.clock, self.stable_s = rng, clock, stable_s
        self.failures = 0
        self.connected_at = None

    def connected(self):
        self.connected_at = self.clock()

    def disconnected(self):
        if self.connected_at is not None and self.clock() - self.connected_at >= self.stable_s:
            self.failures = 0
        self.connected_at = None

    def next_delay(self):
        self.disconnected()
        ceiling = min(60.0, 2.0 ** min(self.failures, 6))
        self.failures += 1
        return self.rng(0.0, ceiling)


class BatClient:
    def __init__(
        self,
        host: HostConfig,
        *,
        device_id: str,
        client_label: str = "BAT Agent Connector",
        allow_writes: bool | None = None,
        allow_orchestrate: bool | None = None,
        max_frame: int = DEFAULT_MAX_FRAME,
        connect_timeout: float | None = None,
        max_concurrency: int = 16,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.host = host
        self.allow_writes = host.writes if allow_writes is None else (allow_writes and host.writes)
        orch = host.orchestrate if allow_orchestrate is None else (allow_orchestrate and host.orchestrate)
        self.allow_orchestrate = bool(orch and self.allow_writes)
        self._device_id = device_id
        self._label = client_label
        self._max_frame = max_frame
        self._connect_timeout = host.connect_timeout_s if connect_timeout is None else connect_timeout
        self._clock = clock
        self._backoff = ReconnectBackoff(clock=clock)
        self._recovery: asyncio.Task | None = None
        self._disconnected_at: float | None = None
        self._closing = False
        self._ws: ClientConnection | None = None
        self._reader: asyncio.Task | None = None
        self._pending: dict[str, asyncio.Future] = {}
        self._subs: set[EventSubscription] = set()
        self._ids = itertools.count(1)
        self._conn_lock = asyncio.Lock()
        self._sem = asyncio.Semaphore(max_concurrency)
        self.auth_info: dict[str, Any] = {}
        self.connect_ms: float | None = None
        self.auth_ms: float | None = None
        self.last_used = time.monotonic()
        self.events_seen = 0
        self.recent_events: deque = deque(maxlen=256)
        self.closed_reason: str | None = None

    # ------------------------------------------------------------------ connect
    @property
    def connected(self) -> bool:
        return self._ws is not None and self._reader is not None and not self._reader.done()

    def _ssl_context(self) -> ssl.SSLContext:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        # The BAT host uses a self-signed certificate; trust comes from the pin below.
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        return ctx

    async def connect(self) -> None:
        async with self._conn_lock:
            if self.connected:
                return
            self._closing = False
            until = self._clock() + self.host.reconnect_grace_s
            if self._disconnected_at is not None:
                until = min(until, self._disconnected_at + self.host.reconnect_grace_s)
                await _retry_pause(min(self._backoff.next_delay(), max(0.0, until - self._clock())))
            while True:
                remaining = until - self._clock()
                if remaining <= 0:
                    self._disconnected_at = None
                    raise ConnectionLost(f"{self.host.name}: reconnect grace expired")
                try:
                    await _wait_with_timeout(self._connect_locked(), remaining)
                    self._disconnected_at = None
                    self._backoff.connected()
                    for sub in self._subs:
                        sub.reopen()
                    return
                except (ConnectionLost, asyncio.TimeoutError) as error:
                    self._log_failure("reconnect", error)
                    remaining = until - self._clock()
                    if remaining <= 0:
                        self._disconnected_at = None
                        raise ConnectionLost(f"{self.host.name}: reconnect grace expired") from None
                    await _retry_pause(min(self._backoff.next_delay(), remaining))

    def _log_failure(self, action: str, error: Any) -> None:
        details = {"type": error.get("type", "RemoteError"),
                   "code": error.get("code"), "message": error.get("message"),
                   "details": error} if isinstance(error, dict) else {
            "type": type(error).__name__, "code": getattr(error, "code", getattr(error, "errno", None)),
            "message": str(error),
        }
        log.warning("host=%s action=%s error=%s", self.host.name, action,
                    redact(json.dumps(details, default=str, ensure_ascii=False)))

    async def _connect_locked(self) -> None:
        await self._close_transport()
        token = self.host.resolve_token()
        t0 = time.monotonic()
        try:
            ws = await connect(
                self.host.url,
                ssl=self._ssl_context(),
                open_timeout=self._connect_timeout,
                ping_interval=20,
                ping_timeout=20,
                close_timeout=3,
                max_size=self._max_frame,
                max_queue=64,
                compression=None,
                proxy=None,
                user_agent_header=f"bat-agent-connector/{__version__}",
            )
        except (OSError, InvalidHandshake, asyncio.TimeoutError) as e:
            self._log_failure("connect", e)
            raise ConnectionLost(
                f"{self.host.name}: connect failed: {redact(type(e).__name__)}: {redact(e)}"
            ) from None
        self._ws = ws
        try:
            self.connect_ms = (time.monotonic() - t0) * 1000
            sslobj = ws.transport.get_extra_info("ssl_object")
            der = sslobj.getpeercert(binary_form=True) if sslobj else None
            if not der or cert_fingerprint(der) != self.host.fingerprint:
                raise FingerprintMismatch(
                    f"{self.host.name}: TLS certificate fingerprint does not match the pinned value; refusing to authenticate"
                )
            t1 = time.monotonic()
            auth = {
                "type": "auth",
                "id": "auth-1",
                "token": token,
                "protocols": [PROTOCOL],
                "compression": ["none"],
                "clientInfo": {
                    "appName": "bat-agent-connector",
                    "appVersion": __version__,
                    "label": self._label,
                    "deviceId": self._device_id,
                    "platform": "linux",
                },
            }
            try:
                await _wait_with_timeout(ws.send(json.dumps(auth)), self._connect_timeout)
                deadline = time.monotonic() + self._connect_timeout
                reply = None
                while time.monotonic() < deadline:
                    raw = await _wait_with_timeout(ws.recv(), max(0.1, deadline - time.monotonic()))
                    obj = self._decode(raw)
                    if obj and obj.get("type") == "auth-result":
                        reply = obj
                        break
            except (ConnectionClosed, asyncio.TimeoutError) as e:
                self._log_failure("authenticate", e)
                raise ConnectionLost(f"{self.host.name}: no auth-result ({type(e).__name__})") from None
            if not reply:
                raise ConnectionLost(f"{self.host.name}: no auth-result")
            if reply.get("result") is not True:
                err = (reply or {}).get("error") or "no auth-result"
                self._log_failure("authenticate", err)
                raise AuthError(f"{self.host.name}: auth failed: {redact(err)}")
            if reply.get("protocol") != PROTOCOL:
                raise AuthError(f"{self.host.name}: server negotiated {reply.get('protocol')!r}, need {PROTOCOL}")
            self.auth_ms = (time.monotonic() - t1) * 1000
            self.auth_info = {
                k: reply.get(k) for k in ("protocol", "serverVersion", "capabilities", "compression")
            }
            self.closed_reason = None
            self._reader = asyncio.create_task(self._read_loop(ws), name=f"bat-reader-{self.host.name}")
        except BaseException:
            await self._close_transport()
            raise

    def _decode(self, raw: str | bytes) -> dict | None:
        try:
            if isinstance(raw, bytes):
                if raw.startswith(GZIP_MAGIC):
                    data = bytearray()
                    compressed = raw[len(GZIP_MAGIC) :]
                    while compressed:
                        decoder = zlib.decompressobj(16 + zlib.MAX_WBITS)
                        data.extend(decoder.decompress(compressed, self._max_frame + 1 - len(data)))
                        if len(data) > self._max_frame or not decoder.eof:
                            return None
                        compressed = decoder.unused_data.lstrip(b"\0")
                    raw = data
                raw = raw.decode("utf-8", "replace")
            obj = json.loads(raw)
            return obj if isinstance(obj, dict) else None
        except (ValueError, OSError, EOFError, zlib.error):
            return None

    async def _read_loop(self, ws: ClientConnection) -> None:
        reason = "closed"
        try:
            async for raw in ws:
                obj = self._decode(raw)
                if obj is None:
                    continue
                t = obj.get("type")
                if t == "event":
                    self.events_seen += 1
                    ch = obj.get("channel")
                    if ch == "agent:error":
                        self.recent_events.append(_agent_error_record(self.host.name, obj))
                    elif ch in (
                        "agent:turn-end",
                        "agent:ask-user",
                        "agent:permission-request",
                        "agent:result",
                    ):
                        self.recent_events.append(
                            {"channel": ch, "sessionId": event_session_id(obj), "at": time.time()}
                        )
                    for sub in list(self._subs):
                        sub.offer(obj)
                    continue
                mid = obj.get("id")
                fut = self._pending.pop(mid, None) if isinstance(mid, str) else None
                if fut and not fut.done():
                    fut.set_result(obj)
        except ConnectionClosed as e:
            self._log_failure("connection", e)
            reason = f"connection closed ({e.rcvd.code if e.rcvd else 'no close frame'})"
        except Exception as e:  # pragma: no cover - defensive
            self._log_failure("connection", e)
            reason = f"reader error: {type(e).__name__}"
        finally:
            self.closed_reason = reason
            err = ConnectionLost(f"{self.host.name}: {reason}")
            for fut in self._pending.values():
                if not fut.done():
                    fut.set_exception(err)
            self._pending.clear()
            self._backoff.disconnected()
            if not self._closing:
                self._disconnected_at = self._clock()
                if self._subs and (self._recovery is None or self._recovery.done()):
                    self._recovery = asyncio.create_task(self._recover_subscriptions(err))

    async def _recover_subscriptions(self, error: ConnectionLost) -> None:
        try:
            await self.connect()
        except (ConnectionLost, AuthError, FingerprintMismatch, ConfigError) as failure:
            self._log_failure("reconnect", failure)
            for sub in list(self._subs):
                sub.close(error)

    async def close(self) -> None:
        self._closing = True
        if self._recovery is not None:
            self._recovery.cancel()
            await asyncio.gather(self._recovery, return_exceptions=True)
            self._recovery = None
        await self._close_transport()
        for sub in list(self._subs):
            sub.close(ConnectionLost(f"{self.host.name}: client closed"))

    async def _close_transport(self) -> None:
        ws, self._ws = self._ws, None
        if ws is not None:
            try:
                await ws.close()
            except Exception:
                ws.transport.abort()
            except BaseException:
                ws.transport.abort()
                raise
        if self._reader is not None:
            with contextlib.suppress(Exception, asyncio.CancelledError):
                await _wait_with_timeout(self._reader, 3)
            self._reader = None

    async def __aenter__(self) -> BatClient:
        await self.connect()
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.close()

    # ------------------------------------------------------------------ requests
    async def _roundtrip(self, frame: dict, timeout: float,
                         on_transport: Callable[[], None] | None = None) -> dict:
        if not self.connected:
            raise ConnectionLost(f"{self.host.name}: not connected")
        mid = frame["id"]
        fut = asyncio.get_running_loop().create_future()
        self._pending[mid] = fut
        try:
            assert self._ws is not None
            encoded = json.dumps(frame)
            async def exchange():
                if on_transport:
                    on_transport()  # No await between the durable fence and websocket send.
                await self._ws.send(encoded)
                return await fut

            return await _wait_with_timeout(exchange(), timeout)
        except asyncio.TimeoutError:
            raise InvokeTimeout(
                f"{self.host.name}: {frame.get('channel', frame['type'])} timed out after {timeout:.0f}s"
            ) from None
        except ConnectionClosed:
            raise ConnectionLost(f"{self.host.name}: connection closed") from None
        finally:
            self._pending.pop(mid, None)

    async def ping(self, timeout: float = 10.0) -> float:
        await self.connect()
        t = time.monotonic()
        await self._roundtrip({"type": "ping", "id": f"p{next(self._ids)}"}, timeout)
        self.last_used = time.monotonic()
        return (time.monotonic() - t) * 1000

    async def guard_read(self, channel: str, params: dict) -> Any:
        """Read BAT identity while an outer write holds the client semaphore.

        This intentionally skips connect, retries, and semaphore acquisition;
        the outer guarded invoke already owns the connection and semaphore.
        """
        if channel not in {"claude:get-session-meta", "claude:get-session-state", "claude:get-worktree-status", "claude:get-pm-runtime", "claude:sync-session", "worktree:status", "git:getRoot", "git:log", "git:branch", "workspace:load"}:
            raise ChannelNotAllowed("guard read channel is not an identity read")
        canonical = check_allowed(channel, allow_writes=False, allow_orchestrate=False)
        frame = {"type": "invoke", "id": f"batc-{next(self._ids)}",
                 "channel": canonical, "params": params}
        reply = await self._roundtrip(frame, max(timeout_for(canonical), self.host.invoke_timeout_s))
        if reply.get("type") == "invoke-error":
            self._log_failure(canonical, reply.get("error"))
            raise InvokeError(f"{self.host.name}: {canonical}: {redact(reply.get('error'))}")
        if canonical == "claude:get-session-meta" and (reply.get("type") != "invoke-result" or "result" not in reply):
            raise InvokeError("session metadata reply does not prove presence or absence")
        return reply.get("result")

    async def invoke(self, channel: str, params: dict | None = None, *, timeout: float | None = None,
                     retry_on_disconnect: bool = False,
                     before_send: Callable[[], None] | None = None,
                     before_frame: Callable[[], Awaitable[None]] | None = None,
                     frame_guard: Callable[[dict], None] | None = None,
                     on_transport: Callable[[], None] | None = None,
                     grant: WriteGrant | None = None) -> Any:
        """Invoke an allow-listed channel. Raises ChannelNotAllowed before sending anything otherwise.

        Write channels also need a resource policy grant for this host, channel and session."""
        canonical = check_allowed(
            channel, allow_writes=self.allow_writes, allow_orchestrate=self.allow_orchestrate
        )
        if is_write(canonical):
            check_grant(grant, self.host.name, canonical, params)
        def final_gate():
            if before_send:
                before_send()
            if is_write(canonical):
                # A shared-carrier failover may reserve its writer fence while
                # this frame waits for connect, a permit or async identity reads.
                check_grant(grant, self.host.name, canonical, params)
                from .pm_source_lineage import require_frame
                require_frame()
                from .host_git import require_execution
                require_execution(self.host.name)
        return await self._invoke_checked(canonical, params, timeout,
                                          retry_on_disconnect=retry_on_disconnect,
                                          before_send=final_gate, before_frame=before_frame,
                                          frame_guard=frame_guard, on_transport=on_transport)

    async def _invoke_checked(self, canonical: str, params: dict | None, timeout: float | None,
                              *, retry_on_disconnect: bool = False,
                              before_send: Callable[[], None] | None = None,
                              before_frame: Callable[[], Awaitable[None]] | None = None,
                              frame_guard: Callable[[dict], None] | None = None,
                              on_transport: Callable[[], None] | None = None) -> Any:
        write = is_write(canonical)
        timeout = timeout if timeout is not None else max(timeout_for(canonical), self.host.invoke_timeout_s)
        attempts = 1 if write and (canonical != "claude:send-message" or not retry_on_disconnect) else 3
        last_exc: Exception | None = None
        for attempt in range(attempts):
            try:
                await self.connect()
                async with self._sem:
                    if before_frame:
                        await before_frame()
                    frame = {
                        "type": "invoke",
                        "id": f"batc-{next(self._ids)}",
                        "channel": canonical,
                        "params": params or {},
                    }
                    if before_send:
                        before_send()
                    if frame_guard:
                        frame_guard(frame)
                    reply = await self._roundtrip(frame, timeout, on_transport=on_transport)
                self.last_used = time.monotonic()
                if reply.get("type") == "invoke-error":
                    self._log_failure(canonical, reply.get("error"))
                    err = redact(reply.get("error"))
                    if BUSY_TEXT in err and attempt + 1 < attempts:
                        await _retry_pause(0.5 * (attempt + 1))
                        continue
                    raise InvokeError(f"{self.host.name}: {canonical}: {err}")
                if canonical == "claude:get-session-meta" and (reply.get("type") != "invoke-result" or "result" not in reply):
                    raise InvokeError("session metadata reply does not prove presence or absence")
                return reply.get("result")
            except (ConnectionLost, InvokeTimeout, AuthError, FingerprintMismatch, ConfigError) as e:
                self._log_failure(canonical, e)
                if not isinstance(e, ConnectionLost):
                    raise
                # Only Claude's send-message is idempotent by clientMessageId;
                # Codex ignores that field and callers disable this retry.
                last_exc = e
                if attempt + 1 < attempts:
                    await _retry_pause(self._backoff.next_delay())
                    continue
                raise
        raise last_exc or ConnectionLost(f"{self.host.name}: failed")  # pragma: no cover

    async def append_workspace_terminal(self, profile_id: str, terminal: dict, *, grant: WriteGrant | None,
                                        retries: int = 3, before_send: Callable[[], None] | None = None,
                                        before_frame: Callable[[], Awaitable[None]] | None = None) -> dict:
        """Append ONE terminal (tab) to the host's workspace document, append-only.

        BAT only offers a whole-document ``workspace:save``. To stay append-only we:
        load -> build new doc = old doc + one terminal (nothing else changed) ->
        re-load and require it to be unchanged (else retry) -> save -> re-load and
        verify every previous terminal/workspace id is still present. A small race
        window with a concurrent GUI save remains (documented in SECURITY.md).
        """
        from .errors import ChannelNotAllowed

        if not (self.allow_writes and self.allow_orchestrate and self.host.orchestrate_register_tabs):
            raise ChannelNotAllowed("tab registration needs writes + orchestrate + orchestrate_register_tabs")
        tid = terminal.get("id")
        if not isinstance(tid, str) or not tid:
            raise InvokeError("terminal needs an id")
        check_grant(grant, self.host.name, "workspace:save", {"sessionId": tid})

        async def load() -> tuple[str | None, dict]:
            raw = await self._invoke_checked("workspace:load", {"profileId": profile_id}, None)
            if raw is None:
                return None, {}
            doc = json.loads(raw) if isinstance(raw, str) else raw
            if not isinstance(doc, dict):
                raise InvokeError("workspace:load returned an unexpected document")
            return (raw if isinstance(raw, str) else json.dumps(raw, sort_keys=True)), doc

        for _attempt in range(retries):
            raw0, doc0 = await load()
            if (
                raw0 is None
                or not isinstance(doc0.get("terminals"), list)
                or not isinstance(doc0.get("workspaces"), list)
            ):
                raise InvokeError("refusing to save: host workspace document is empty or malformed")
            if any(isinstance(t, dict) and t.get("id") == tid for t in doc0["terminals"]):
                return {"appended": False, "reason": "already present"}
            if terminal.get("workspaceId") not in {
                w.get("id") for w in doc0["workspaces"] if isinstance(w, dict)
            }:
                raise InvokeError("refusing to save: target workspace does not exist on host")
            new_doc = json.loads(json.dumps(doc0))
            new_doc["terminals"].append(terminal)
            check = json.loads(json.dumps(new_doc))
            check["terminals"] = [
                t for t in check["terminals"] if not (isinstance(t, dict) and t.get("id") == tid)
            ]
            if check != doc0:
                raise InvokeError("internal: append-only invariant violated")
            raw1, _ = await load()
            if raw1 != raw0:
                await asyncio.sleep(0.5)
                continue  # someone saved in between; retry from scratch
            def save_gate():
                if before_send:
                    before_send()
                check_grant(grant, self.host.name, "workspace:save", {"sessionId": tid})
            saved = await self._invoke_checked(
                "workspace:save", {"profileId": profile_id, "data": json.dumps(new_doc)}, None,
                before_send=save_gate, before_frame=before_frame
            )
            _, after = await load()
            ids_after = {t.get("id") for t in after.get("terminals") or [] if isinstance(t, dict)}
            lost = [
                t.get("id") for t in doc0["terminals"] if isinstance(t, dict) and t.get("id") not in ids_after
            ]
            ws_after = {w.get("id") for w in after.get("workspaces") or [] if isinstance(w, dict)}
            lost_ws = [
                w.get("id") for w in doc0["workspaces"] if isinstance(w, dict) and w.get("id") not in ws_after
            ]
            return {
                "appended": bool(saved) and tid in ids_after,
                "saved": saved,
                "verified_previous_terminals_kept": not lost and not lost_ws,
                "lost_terminals": lost,
                "lost_workspaces": lost_ws,
            }
        return {"appended": False, "reason": "workspace kept changing; gave up to avoid clobbering"}

    def subscribe(
        self, predicate: Callable[[dict], bool] | None = None, maxsize: int = 512
    ) -> EventSubscription:
        sub = EventSubscription(predicate=predicate, maxsize=maxsize)
        self._subs.add(sub)
        return sub

    def unsubscribe(self, sub: EventSubscription) -> None:
        self._subs.discard(sub)
