"""Narrow, versioned plaintext profiles. No reasoning, instructions or inferred status."""
from __future__ import annotations

import hashlib

from .usage_adapters import UsageInvalid, identifier, timestamp

MAX_TEXT = 16000


def _text(value):
    if not isinstance(value, str):
        raise UsageInvalid("invalid_history_content")
    try:
        value.encode("utf-8")
    except UnicodeError:
        raise UsageInvalid("invalid_history_content") from None
    return value[:MAX_TEXT], len(value) > MAX_TEXT


def _content(raw, text_types):
    if isinstance(raw, str):
        text, clipped = _text(raw)
        return [{"kind": "text", "text": text}], clipped, 0
    if not isinstance(raw, list):
        raise UsageInvalid("invalid_history_content")
    parts, omitted, clipped, remaining = [], 0, False, MAX_TEXT
    for block in raw:
        if not isinstance(block, dict):
            raise UsageInvalid("invalid_history_content")
        kind = block.get("type")
        if kind in text_types:
            text, cut = _text(block.get("text"))
            clipped = clipped or cut or len(text) > remaining
            if remaining:
                parts.append({"kind": "text", "text": text[:remaining]})
                remaining -= min(remaining, len(text))
        elif kind == "tool_use":
            # A named request is not proof of execution; arguments stay omitted.
            parts.append({"kind": "tool_request", "name": identifier(block.get("name"))})
            omitted += 1
        elif kind == "tool_result":
            # This profile exposes the receipt marker; full tool bodies need a
            # separately reviewed format, not a stringified arbitrary object.
            parts.append({"kind": "tool_result", "name": None})
            omitted += 1
        else:
            omitted += 1
    return parts[:128], clipped or len(parts) > 128, omitted


def decode(record, source, state, *, offset, digest):
    if not isinstance(record, dict):
        raise UsageInvalid("invalid_record")
    state["records"] = state.get("records", 0) + 1
    parts, native_id, role, at, clipped, omitted = [], None, None, None, False, 0
    if source.adapter == "codex-history-jsonl-v1":
        kind, payload = record.get("type"), record.get("payload")
        if not isinstance(payload, dict):
            state["omitted_records"] = state.get("omitted_records", 0) + 1
            return None, None
        if kind == "session_meta":
            if (payload.get("id") != source.session_id or any(payload.get(k) is not None for k in
                    ("forked_from_id", "parent_thread_id", "history_base", "subagent_history_start_ordinal"))):
                raise UsageInvalid("history_identity_unproven")
            state["identified"] = True
            state["cli_version"] = identifier(payload.get("cli_version"))
            return None, None
        if not state.get("identified"):
            raise UsageInvalid("history_identity_unproven")
        if kind != "response_item":
            state["omitted_records"] = state.get("omitted_records", 0) + 1
            return None, None
        item_type = payload.get("type")
        if item_type == "message" and payload.get("role") in ("user", "assistant"):
            role = payload["role"]
            parts, clipped, omitted = _content(payload.get("content"), ("input_text", "output_text"))
        elif item_type in ("function_call", "custom_tool_call"):
            role = "tool"
            parts = [{"kind": "tool_request", "name": identifier(payload.get("name"))}]
            omitted = 1
        elif item_type in ("function_call_output", "custom_tool_call_output"):
            role = "tool"
            parts = [{"kind": "tool_result", "name": identifier(payload.get("name"))}]
            omitted = 1
        else:
            state["omitted_records"] = state.get("omitted_records", 0) + 1
            return None, None
        native_id = identifier(payload.get("id"))
    elif source.adapter == "claude-history-jsonl-v1":
        if record.get("type") not in ("user", "assistant"):
            state["omitted_records"] = state.get("omitted_records", 0) + 1
            return None, None
        if record.get("sessionId") != source.session_id or record.get("isSidechain") is True:
            raise UsageInvalid("history_identity_unproven")
        message = record.get("message")
        role = record["type"]
        if not isinstance(message, dict) or message.get("role") != role:
            raise UsageInvalid("invalid_history_content")
        native_id = identifier(record.get("uuid"))
        if native_id is None:
            raise UsageInvalid("history_identity_unproven")
        state["identified"] = True
        state["cli_version"] = identifier(record.get("version"))
        parts, clipped, omitted = _content(message.get("content"), ("text",))
    else:
        raise UsageInvalid("unsupported_history_adapter")
    at = timestamp(record.get("timestamp"))
    if not parts:
        state["omitted_records"] = state.get("omitted_records", 0) + 1
        return None, None
    state["clipped_records"] = state.get("clipped_records", 0) + int(clipped)
    state["omitted_parts"] = state.get("omitted_parts", 0) + omitted
    key = "native:" + native_id if native_id else "offset:" + str(offset)
    return {"entry_id": "shi_" + hashlib.sha256((source.binding + key).encode()).hexdigest()[:32],
            "role": role, "observed_at": at, "parts": parts, "clipped": clipped,
            "omitted_parts": omitted, "source_offset": offset, "source_digest": digest,
            "identity_basis": "native_record" if native_id else "file_offset"}, None
