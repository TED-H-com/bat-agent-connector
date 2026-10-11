"""Bounded PM runtime evidence, without dispatch or mutable PM state.

BAT contract: docs/design/pm-runtime.md, audited source b7419892. Callers enforce
PM membership/ownership before any read and journal replay pages atomically.
Neither a send ACK nor a quiet runtime establishes a completed PM turn.
"""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json

from . import confinement

BAT_SOURCE = confinement.BAT_SOURCE
MAX_REPLAY_BYTES = 1024 * 1024
MAX_OUTPUT_CHARS = 128_000
READONLY_OPTIONS = {"codexSandboxMode": "read-only", "codexApprovalPolicy": "never"}


class RuntimeEvidenceError(ValueError):
    """Invalid evidence cannot advance a cursor or establish completion."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def readonly_start_options() -> dict:
    return dict(READONLY_OPTIONS)


def confinement_record() -> dict:
    return confinement.snapshot("codex", readonly_start_options())


def check_readonly(meta: dict | None) -> dict:
    record = confinement_record()
    confinement.ensure_confirmed(record, meta)
    return confinement.verify(record, meta)


def _text(value, maximum=512) -> bool:
    return isinstance(value, str) and bool(value.strip()) and len(value) <= maximum


def _seq(value) -> bool:
    return type(value) is int and 0 <= value <= 2**63 - 1


def _cursor(value) -> dict:
    if not isinstance(value, dict) or not _text(value.get("epoch"), 256) or not _seq(value.get("seq")):
        raise RuntimeEvidenceError("PM_REPLAY_CURSOR_INVALID")
    return {"epoch": value["epoch"], "seq": value["seq"]}


def _events(events, session_id: str, epoch: str, after: int) -> list[dict]:
    if not isinstance(events, list):
        raise RuntimeEvidenceError("PM_REPLAY_EVENTS_INVALID")
    expected = after + 1
    for event in events:
        if not isinstance(event, dict) or not _seq(event.get("seq")) or event["seq"] != expected:
            raise RuntimeEvidenceError("PM_REPLAY_SEQUENCE_GAP")
        if event.get("epoch", epoch) != epoch:
            raise RuntimeEvidenceError("PM_REPLAY_EPOCH_CHANGED")
        params = event.get("params")
        channel = event.get("channel")
        if (not _text(channel) or not channel.startswith("claude:") or channel == "claude:sync-event"
                or not isinstance(params, dict) or params.get("sessionId") != session_id):
            raise RuntimeEvidenceError("PM_REPLAY_SESSION_MISMATCH")
        if "at" in event and not _seq(event["at"]):
            raise RuntimeEvidenceError("PM_REPLAY_EVENT_INVALID")
        expected += 1
    return copy.deepcopy(events)


def parse_replay(raw, session_id: str, cursor: dict | None = None) -> dict:
    """Validate one snapshot/delta; post-send snapshots are permanent evidence gaps.

    This function never substitutes a snapshot for lost native completion events.
    Returned event dictionaries preserve BAT's original channel/params/seq/at.
    """
    if not _text(session_id):
        raise RuntimeEvidenceError("PM_REPLAY_SESSION_INVALID")
    try:
        size = len(json.dumps(raw, ensure_ascii=False, allow_nan=False).encode("utf-8"))
    except (TypeError, ValueError, UnicodeError):
        raise RuntimeEvidenceError("PM_REPLAY_INVALID") from None
    if size > MAX_REPLAY_BYTES:
        raise RuntimeEvidenceError("PM_REPLAY_OVERSIZED")
    if not isinstance(raw, dict) or raw.get("sessionId") != session_id:
        raise RuntimeEvidenceError("PM_REPLAY_SESSION_MISMATCH")
    current = _cursor(raw.get("cursor"))
    previous = _cursor(cursor) if cursor is not None else None
    if raw.get("mode") == "snapshot":
        state = raw.get("state")
        if not isinstance(state, dict) or state.get("sessionId", session_id) != session_id:
            raise RuntimeEvidenceError("PM_REPLAY_SNAPSHOT_INVALID")
        return {"mode": "snapshot", "cursor": current, "events": [], "state": copy.deepcopy(state),
                "has_more": False, "gap": previous is not None,
                "reason": "replay_snapshot_after_send" if previous is not None else "initial_snapshot"}
    if raw.get("mode") != "delta" or previous is None:
        raise RuntimeEvidenceError("PM_REPLAY_MODE_INVALID")
    if current["epoch"] != previous["epoch"]:
        raise RuntimeEvidenceError("PM_REPLAY_EPOCH_CHANGED")
    events = _events(raw.get("events"), session_id, current["epoch"], previous["seq"])
    if current["seq"] != (events[-1]["seq"] if events else previous["seq"]):
        raise RuntimeEvidenceError("PM_REPLAY_CURSOR_GAP")
    has_more = raw.get("hasMore")
    if type(has_more) is not bool or has_more and not events:
        raise RuntimeEvidenceError("PM_REPLAY_PAGINATION_INVALID")
    return {"mode": "delta", "cursor": current, "events": events,
            "has_more": has_more, "gap": False, "reason": None}


async def sync_session(client, session_id: str, cursor: dict | None = None) -> dict:
    params = {"sessionId": session_id}
    if cursor is not None:
        params["cursor"] = _cursor(cursor)
    raw = await client.invoke("claude:sync-session", params)
    return parse_replay(raw, session_id, cursor)


def fresh_send_cursor(replay: dict, expected_sdk_session_id: str) -> dict:
    """The first turn must use a fresh own session, never a resumed native thread."""
    state = replay.get("state") or {}
    meta = state.get("meta") or {}
    if (replay.get("mode") != "snapshot" or replay.get("gap") or replay.get("events")
            or not _text(expected_sdk_session_id) or meta.get("sdkSessionId") != expected_sdk_session_id
            or type(meta.get("numTurns")) is not int or meta["numTurns"] != 0
            or state.get("isStreaming") is not False or meta.get("isStreaming") is not False
            or state.get("pendingPermission") or state.get("pendingAskUser")
            or not isinstance(state.get("messages"), list) or state["messages"]):
        raise RuntimeEvidenceError("PM_SESSION_NOT_FRESH")
    check_readonly(meta)
    return _cursor(replay.get("cursor"))


def prompt_digest(prompt: str) -> str:
    return hashlib.sha256(prompt.encode("utf-8")).hexdigest()


def _message_text(message: dict) -> str | None:
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list) and all(isinstance(item, dict) and item.get("type") in {"text", "output_text"}
                                         and isinstance(item.get("text"), str) for item in content):
        return "".join(item["text"] for item in content)
    return None


def assess_turn(session_id: str, send_cursor: dict, events: list[dict], current_cursor: dict,
                expected_sdk_session_id: str, *, gap=False, has_more=False, actual_model=None,
                expected_prompt_digest: str | None = None) -> dict:
    """Assess all durable events after one fresh session's original send cursor.

    Pass complete persisted events, not only the most recent page. Unknown is
    sticky in journal state; no returned state authorizes sending the input again.
    """
    result = {"status": "unknown", "complete": False, "reason": None, "content": None,
              "native_turn_id": None, "sdk_session_id": None, "native_message_id": None,
              "actual_model": actual_model, "source_event_seqs": [], "messages": []}

    def finish(status, reason):
        return {**result, "status": status, "complete": status == "completed", "reason": reason}

    if gap:
        return finish("unknown", "replay_gap")
    try:
        before, current = _cursor(send_cursor), _cursor(current_cursor)
        if before["epoch"] != current["epoch"]:
            return finish("unknown", "replay_epoch_changed")
        items = _events(events, session_id, before["epoch"], before["seq"])
        if current["seq"] != (items[-1]["seq"] if items else before["seq"]):
            return finish("unknown", "replay_cursor_gap")
    except RuntimeEvidenceError as exc:
        return finish("unknown", exc.code)
    if not _text(expected_sdk_session_id):
        return finish("unknown", "native_session_id_missing")
    terminals, messages, user_messages, model_values = [], [], [], []
    seen = {}
    for event in items:
        channel, params = event["channel"], event["params"]
        if channel == "claude:history-checkpoint":
            return finish("unknown", "history_checkpoint_gap")
        if channel == "claude:status":
            meta = params.get("meta")
            if not isinstance(meta, dict):
                return finish("unknown", "native_status_invalid")
            if meta.get("sdkSessionId", expected_sdk_session_id) != expected_sdk_session_id:
                return finish("unknown", "native_session_changed")
            if (any(key in meta and meta[key] != value for key, value in READONLY_OPTIONS.items())):
                return finish("unknown", "permission_options_changed")
            if _text(meta.get("model")):
                model_values.append(meta["model"])
        elif channel == "claude:message":
            message = params.get("message")
            if not isinstance(message, dict) or message.get("sessionId", session_id) != session_id:
                return finish("unknown", "native_message_invalid")
            role = message.get("role")
            if role not in {"user", "assistant"}:
                continue
            content = _message_text(message)
            if content is None or message.get("truncated") or message.get("isStreaming"):
                return finish("incomplete", "native_message_incomplete")
            if role == "user":
                user_messages.append(content)
                continue
            mid = message.get("id")
            if not _text(mid):
                return finish("unknown", "native_message_id_missing")
            if mid in seen:
                if seen[mid] != content:
                    return finish("unknown", "native_message_id_conflict")
                continue
            seen[mid] = content
            messages.append({"native_message_id": mid, "content": content, "source_event_seq": event["seq"]})
        elif channel == "claude:turn-end":
            payload = params.get("payload")
            if not isinstance(payload, dict):
                return finish("unknown", "native_terminal_invalid")
            terminals.append((event, payload))
    if len(user_messages) > 1:
        return finish("unknown", "multiple_inputs_in_fresh_session")
    if expected_prompt_digest is not None and (len(user_messages) != 1
            or prompt_digest(user_messages[0]) != expected_prompt_digest):
        return finish("unknown", "original_input_not_observed")
    if model_values:
        if len(set(model_values)) != 1 or actual_model is not None and actual_model != model_values[-1]:
            return finish("unknown", "native_model_changed")
        result["actual_model"] = model_values[-1]
    if has_more:
        return finish("pending", "replay_has_more")
    if not terminals:
        return finish("pending", "native_completion_not_observed")
    if len(terminals) != 1:
        return finish("unknown", "multiple_native_terminals")
    event, terminal = terminals[0]
    result.update(native_turn_id=terminal.get("turnId"), sdk_session_id=terminal.get("sdkSessionId"))
    if terminal.get("reason") != "completed":
        return finish("incomplete", "native_turn_" + str(terminal.get("reason", "unknown")))
    if (not _text(terminal.get("turnId")) or terminal.get("sdkSessionId") != expected_sdk_session_id):
        return finish("unknown", "native_terminal_identity_missing_or_changed")
    if any(item["source_event_seq"] > event["seq"] for item in messages):
        return finish("unknown", "message_after_native_terminal")
    content = messages[-1]["content"] if messages else terminal.get("result")
    if not isinstance(content, str) or not content.strip():
        return finish("incomplete", "native_reply_missing")
    if len(content) > MAX_OUTPUT_CHARS or terminal.get("truncated"):
        return finish("incomplete", "native_reply_oversized_or_truncated")
    result.update(content=content, messages=messages,
                  native_message_id=messages[-1]["native_message_id"] if messages else None,
                  source_event_seqs=[item["source_event_seq"] for item in messages] + [event["seq"]])
    return finish("completed", "native_turn_completed")


def capability_report(*, bat_version=None, runtime=None, meta=None, replay=None,
                      transport_authenticated=False) -> dict:
    """Independent observations, not a promise of an unrestricted PM writer.

    Fixed BAT has no per-session external MCP tool policy/readback. Never infer
    that boundary from filesystem read-only settings, installation, or a prompt.
    """
    codex = runtime.get("codex") if isinstance(runtime, dict) else None
    codex = codex if isinstance(codex, dict) else {}
    verification = confinement.verify(confinement_record(), meta)
    replay_supported = isinstance(replay, dict) and replay.get("mode") in {"snapshot", "delta"}
    return {"adapter": "bat_codex_readonly", "audited_bat_source": BAT_SOURCE,
            "bat_version": bat_version if _text(bat_version) else None,
            "runtime": {"status": codex.get("state", "unknown"), "version": codex.get("version")},
            "authentication": {"transport": "authenticated" if transport_authenticated else "unknown",
                               "model_provider": "unverified"},
            "actual_model": meta.get("model") if isinstance(meta, dict) and _text(meta.get("model")) else None,
            "session_options": verification,
            "filesystem": {"mode": "read_only", "status": verification["status"],
                           "enforcement": "sandbox_enforcement_unverified"},
            "external_tools": {"status": "unverified", "reason": "external_tool_policy_unverified"},
            "native_completion": {"status": "replay_available" if replay_supported else "unverified",
                                  "retention_seconds": 600, "durable": False},
            "headless": {"status": "session_observed" if isinstance(meta, dict) else "unverified"},
            "write_files": False, "automatic_pm_turn": False,
            "fallback": "observer_context_only", "blocked_reason": "external_tool_policy_unverified"}


async def probe(client, session_id: str | None = None) -> dict:
    """Read-only capability observation. Caller checks workspace visibility first."""
    calls = [client.invoke("app:get-version", {}), client.invoke("runtime:get-status", {})]
    if session_id is not None:
        calls.extend([client.invoke("claude:get-session-meta", {"sessionId": session_id}),
                      sync_session(client, session_id)])
    values = await asyncio.gather(*calls, return_exceptions=True)
    observed = [None if isinstance(value, BaseException) else value for value in values]
    return capability_report(bat_version=observed[0], runtime=observed[1],
                             meta=observed[2] if session_id is not None else None,
                             replay=observed[3] if session_id is not None else None,
                             transport_authenticated=bool(getattr(client, "auth_info", {})))
