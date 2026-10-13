"""Allowlisted usage metadata only; no prompts, paths, account data or tool bodies.

See docs/design/agent-resource-observation.md for the versioned input contracts.
"""
from __future__ import annotations

import re
from datetime import timezone

from .iso_time import parse_datetime

MAX_COUNT = 10**15
METRICS = ("input", "cache_read", "cache_write", "output", "reasoning", "total")


class UsageInvalid(ValueError):
    """Stable public code, never include raw input in the exception."""


def identifier(value):
    return value if isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:/-]{0,127}", value) else None


def timestamp(value):
    if not isinstance(value, str) or len(value) > 40:
        raise UsageInvalid("invalid_timestamp")
    try:
        dt = parse_datetime(value)
        if dt.tzinfo is None:
            raise ValueError
        return dt.astimezone(timezone.utc).isoformat(timespec="milliseconds")
    except (ValueError, OverflowError):
        raise UsageInvalid("invalid_timestamp") from None


def count(value):
    if value is None:
        return None
    if type(value) is not int or not 0 <= value <= MAX_COUNT:
        raise UsageInvalid("invalid_counter")
    return value


def counters(raw, claude=False):
    if not isinstance(raw, dict):
        raise UsageInvalid("invalid_counter")
    keys = ("input_tokens", "cache_read_input_tokens" if claude else "cached_input_tokens",
            "cache_creation_input_tokens" if claude else "cache_write_input_tokens", "output_tokens",
            "reasoning_output_tokens", "total_tokens")
    values = dict(zip(METRICS, (count(raw.get(k)) for k in keys), strict=True))
    if values["input"] is None or values["output"] is None:
        raise UsageInvalid("missing_counter")
    if claude:
        # Claude input excludes cache. Missing cache fields remain unknown, never zero.
        values["reasoning"] = None
        values["total"] = (sum(values[k] for k in ("input", "cache_read", "cache_write", "output"))
                           if values["cache_read"] is not None and values["cache_write"] is not None else None)
    else:
        expected = values["input"] + values["output"]
        if values["total"] is not None and values["total"] != expected:
            # Codex can emit synthetic context-window fill counters. They are not consumption.
            raise UsageInvalid("non_usage_counter")
        values["total"] = expected
        for child, parent in (("cache_read", "input"), ("cache_write", "input"), ("reasoning", "output")):
            if values[child] is not None and values[child] > values[parent]:
                raise UsageInvalid("invalid_inclusion")
    if values["total"] is not None and values["total"] > MAX_COUNT:
        raise UsageInvalid("invalid_counter")
    return values


def quota(raw, at):
    if not isinstance(raw, dict) or not identifier(raw.get("limit_id")):
        return None
    windows = []
    for name in ("primary", "secondary"):
        value = raw.get(name)
        if not isinstance(value, dict):
            continue
        used, duration, resets = value.get("used_percent"), value.get("window_minutes"), value.get("resets_at")
        if (type(used) not in (int, float) or not 0 <= used <= 100 or type(duration) is not int
                or not 0 < duration <= 525600 or type(resets) is not int or not 0 < resets < 253402300800):
            continue
        windows.append({"window": name, "used_percent": used, "window_minutes": duration,
                        "resets_at": resets})
    return {"observed_at": at, "limit_id": raw["limit_id"], "windows": windows} if windows else None


def parse(record, source, state):
    """Return one deduplicated observation candidate and optional rate-limit snapshot."""
    if not isinstance(record, dict):
        raise UsageInvalid("invalid_record")
    kind = record.get("type")
    if source.adapter == "claude-transcript-v1":
        if kind != "assistant":
            return None, None
        message = record.get("message")
        if not isinstance(message, dict) or not isinstance(message.get("usage"), dict):
            return None, None
        if record.get("sessionId") != source.session_id:
            raise UsageInvalid("session_mismatch")
        mid = identifier(message.get("id"))
        if mid is None:
            raise UsageInvalid("missing_identity")
        return {"key": mid, "at": timestamp(record.get("timestamp")), "metrics": counters(message["usage"], True),
                "actual_model": identifier(message.get("model")), "requested_model": None,
                "effort": None, "cli_version": identifier(record.get("version"))}, None
    payload = record.get("payload")
    if not isinstance(payload, dict):
        return None, None
    if kind == "session_meta":
        if payload.get("id") != source.session_id or payload.get("forked_from_id"):
            # A fork can include inherited usage; no parent-cost attribution without a boundary.
            raise UsageInvalid("session_mismatch")
        state["identified"] = True
        state["cli_version"] = identifier(payload.get("cli_version"))
    elif kind == "turn_context":
        state["requested_model"] = identifier(payload.get("model"))
        state["effort"] = identifier(payload.get("effort"))
    elif kind == "event_msg" and payload.get("type") == "token_count":
        if not state.get("identified"):
            raise UsageInvalid("missing_identity")
        at = timestamp(record.get("timestamp"))
        rate = quota(payload.get("rate_limits"), at)
        info = payload.get("info")
        if not isinstance(info, dict) or not isinstance(info.get("total_token_usage"), dict):
            return None, rate
        current = counters(info["total_token_usage"])
        previous = state.get("counters")
        if previous and any(current[k] is not None and previous[k] is not None and current[k] < previous[k]
                            for k in METRICS):
            raise UsageInvalid("counter_regression")
        state["counters"] = current
        if previous == current:
            return None, rate
        delta = {k: (current[k] - previous[k] if previous and previous[k] is not None else
                     current[k] if not previous else None) if current[k] is not None else None for k in METRICS}
        return {"key": str(current["input"]) + ":" + str(current["output"]), "at": at, "metrics": delta,
                "actual_model": None, "requested_model": state.get("requested_model"),
                "effort": state.get("effort"), "cli_version": state.get("cli_version")}, rate
    return None, None
