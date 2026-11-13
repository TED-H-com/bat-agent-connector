"""Write-side safety: audit log (append-only JSONL) and rate limiting derived from it."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import time
from contextlib import contextmanager
from contextvars import ContextVar
from os import fdopen
from pathlib import Path
from threading import current_thread
from typing import Any

from ._private_files import mkdir_private, open_private
from .config import SafetyConfig, state_dir
from .errors import ConnectionLost, InvokeTimeout, WriteRefused

POST_EFFECT_PHASES = frozenset({"result", "append-terminal", "rollback", "record"})
AUDIT_FAILURE_STAGES = frozenset({"mkdir", "open", "write", "flush", "close"})
AUDIT_EFFECT_OUTCOMES = frozenset({"accepted", "error", "unknown"})
_result_sink = ContextVar("audit_result_sink", default=None)


class _ResultAuditSink:
    def __init__(self, collect):
        self.collect = collect
        self.active = True
        self.thread = current_thread()


@contextmanager
def result_audit_sink(sink):
    """Bind an execution-local sink; it buffers warnings, never performs an external effect."""
    binding = _ResultAuditSink(sink)
    token = _result_sink.set(binding)
    try:
        yield
    finally:
        binding.active = False
        _result_sink.reset(token)


def audit_error_outcome(error: BaseException) -> str:
    """A transport/storage exception does not establish a remote outcome."""
    from .operations import AmbiguousOutcome

    return "unknown" if isinstance(error, (ConnectionLost, InvokeTimeout, OSError, asyncio.TimeoutError,
                                           asyncio.CancelledError, AmbiguousOutcome)) else "error"


def audit_reply_outcome(reply: Any, *, key: str | None = None) -> str:
    """Classify explicit remote acceptance, without treating a malformed ACK as success."""
    if key is not None:
        reply = reply.get(key) if isinstance(reply, dict) else None
    elif isinstance(reply, dict):
        reply = next((reply[key] for key in ("accepted", "ok", "success") if key in reply), None)
    return "accepted" if reply is True else "error" if reply is False else "unknown"


def audit_path() -> Path:
    return state_dir() / "audit.jsonl"


def text_fingerprint(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


class Audit:
    def __init__(self, safety: SafetyConfig, path: Path | None = None) -> None:
        self.safety = safety
        self.path = path or audit_path()

    def _tail(self, max_lines: int = 2000) -> list[dict]:
        try:
            with os.fdopen(open_private(self.path, os.O_RDONLY), "rb") as fh:
                fh.seek(0, os.SEEK_END)
                size = fh.tell()
                fh.seek(max(0, size - 512 * 1024))
                lines = fh.read().decode("utf-8", "replace").splitlines()[-max_lines:]
        except FileNotFoundError:
            return []
        except (OSError, ValueError):
            raise WriteRefused("cannot read audit file: validate private file permissions and type") from None
        out = []
        for ln in lines:
            try:
                out.append(json.loads(ln))
            except ValueError:
                continue
        return out

    def check_rate(self, host: str, session_id: str, now: float | None = None,
                   *, initial_task_send: bool = False, permission_operation_id: str | None = None) -> None:
        now = now or time.time()
        entries = [e for e in self._tail() if e.get("phase") == "attempt"]
        hour = [e for e in entries if now - float(e.get("at", 0)) < 3600]
        if len(hour) >= self.safety.max_writes_per_hour:
            raise WriteRefused(
                f"rate limit: {len(hour)} writes in the last hour (max_writes_per_hour={self.safety.max_writes_per_hour})"
            )
        same = [e for e in hour if e.get("host") == host and e.get("session_id") == session_id]
        if permission_operation_id:
            # A resumed fixed permission batch may finish its unsent frames without treating its
            # own proven earlier frame as another request. All attempts still count toward the hour.
            permission_channels = {"claude:set-permission-mode", "claude:set-codex-sandbox-mode",
                                   "claude:set-codex-approval-policy"}
            same = [e for e in same if not (e.get("operation_id") == permission_operation_id
                    and e.get("tool") == "session_set_permissions" and e.get("channel") in permission_channels)]
        if same:
            # A task's first prompt follows its reserved start on the same
            # session. Start attempts still consume the hourly budget, but
            # are not an earlier prompt for the per-session send interval.
            start_channels = {"worktree:create", "claude:start-session", "workspace:save"}
            if initial_task_send and all(e.get("channel") in start_channels for e in same):
                return
            age = now - max(float(e.get("at", 0)) for e in same)
            if age < self.safety.write_min_interval_s:
                raise WriteRefused(
                    f"rate limit: last write to this session was {age:.0f}s ago "
                    f"(write_min_interval_s={self.safety.write_min_interval_s:.0f})"
                )

    def same_send_retry(self, host: str, session_id: str, message_id: str, text: str) -> bool:
        """A Claude retry may bypass the interval only for the same recorded payload."""
        attempts = [e for e in self._tail() if e.get("phase") == "attempt" and
                    e.get("channel") == "claude:send-message" and e.get("host") == host and
                    e.get("session_id") == session_id and e.get("message_id") == message_id]
        if not attempts:
            return False
        if any(e.get("text_sha256_16") != text_fingerprint(text) or e.get("text_len") != len(text)
               for e in attempts):
            raise WriteRefused("message_id was previously used with different text")
        return True

    def record(self, **fields: Any) -> None:
        self._record(fields)

    def record_after_effect(self, *, effect_outcome: str, effect_sent: bool, **fields: Any) -> None:
        """Account for an append failure without replacing an already sent effect's result.

        Only explicitly reviewed post-effect sites use this entry point. Without
        an operation sink the original strict record behavior is retained.
        Sink/journal failures are not audit append failures and are not swallowed.
        """
        if fields.get("phase") not in POST_EFFECT_PHASES or effect_outcome not in AUDIT_EFFECT_OUTCOMES:
            raise ValueError("invalid post-effect audit classification")
        binding = _result_sink.get()
        sink = (binding.collect if effect_sent and binding is not None and binding.active
                and binding.thread is current_thread() else None)
        self._record(fields, sink=sink, effect_outcome=effect_outcome)

    def _record(self, fields: dict, *, sink=None, effect_outcome=None) -> None:
        text = fields.pop("text", None)
        if text is not None:
            fields["text_sha256_16"] = text_fingerprint(text)
            fields["text_len"] = len(text)
            n = self.safety.audit_preview_chars
            if n > 0:
                fields["text_preview"] = _redact_preview(text)[:n]
        fields.setdefault("at", time.time())
        fields["at_iso"] = time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(fields["at"]))
        line = json.dumps(fields, ensure_ascii=False, default=str)
        stage = "mkdir"
        try:
            mkdir_private(self.path.parent)
            stage = "open"
            try:
                fd = open_private(self.path, os.O_WRONLY | os.O_CREAT | os.O_APPEND)
            except (OSError, ValueError):
                raise WriteRefused("cannot open audit file: validate private file permissions and type") from None
            with fdopen(fd, "a") as fh:
                stage = "write"
                fh.write(line + "\n")
                stage = "flush"
                fh.flush()
                stage = "close"
        except (OSError, ValueError, WriteRefused):
            if sink is None:
                raise
            # Hash only stable effect selectors. Neither payload nor error text
            # nor audit paths enter the warning, its identity, or an event.
            identity = {key: fields.get(key) for key in
                        ("tool", "host", "session_id", "channel", "message_id", "phase")}
            sink(identity, {"state": "failed", "code": "AUDIT_RESULT_WRITE_FAILED",
                            "phase": fields["phase"], "failure_stage": stage,
                            "effect_outcome": effect_outcome})


def _redact_preview(text: str) -> str:
    text = re.sub(r"(?i)(authorization\s*:\s*bearer\s+|[?&]token=|\btoken\s*[:=]\s*[\"']?)[^\s&\"'<>]+", r"\1[redacted]", text)
    return re.sub(r"[A-Za-z0-9_+/=\-]{32,}", "[redacted]", text)
