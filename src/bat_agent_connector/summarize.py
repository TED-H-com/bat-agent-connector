"""Turn BAT session messages into compact, size-capped text records."""

from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from typing import Any

from .iso_time import parse_datetime


def ts_to_ms(v: Any) -> int | None:
    if v is None or v == "" or v == 0:
        return None
    if isinstance(v, (int, float)):
        x = float(v)
        if x < 1e11:  # seconds
            x *= 1000
        return int(x)
    if isinstance(v, str):
        s = v.strip()
        try:
            return ts_to_ms(float(s))
        except ValueError:
            pass
        try:
            dt = parse_datetime(s)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return int(dt.timestamp() * 1000)
        except ValueError:
            return None
    return None


def iso_local(ms: int | None) -> str | None:
    if not ms:
        return None
    return datetime.fromtimestamp(ms / 1000).astimezone().isoformat(timespec="seconds")


def clip(s: str, n: int) -> str:
    s = s or ""
    if len(s) <= n:
        return s
    return s[: max(0, n - 12)] + f" …[+{len(s) - n + 12} chars]"


def _content_text(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for b in content:
            if isinstance(b, str):
                parts.append(b)
            elif isinstance(b, dict):
                t = b.get("type")
                if t in ("text", "output_text", "input_text") or ("text" in b and t is None):
                    parts.append(str(b.get("text", "")))
                elif t == "image":
                    parts.append("[image]")
                elif t == "tool_use":
                    parts.append(f"[tool_use {b.get('name', '?')}]")
                elif t == "tool_result":
                    parts.append("[tool_result]")
                elif t in ("thinking", "redacted_thinking", "reasoning"):
                    continue
                elif "text" in b:
                    parts.append(str(b.get("text")))
        return "\n".join(p for p in parts if p)
    if isinstance(content, dict):
        if "text" in content:
            return str(content["text"])
        return json.dumps(content, ensure_ascii=False)[:400]
    return str(content)


def is_tool(m: dict) -> bool:
    return "toolName" in m


def _message_metadata(m: dict) -> dict:
    # Never borrow model/agent/cost metadata from the current session. Older BAT
    # archives usually have only a timestamp; missing message facts stay absent.
    result = {}
    for key in ("agent", "model", "status"):
        value = m.get(key)
        if isinstance(value, str) and 0 < len(value) <= 256 and not any(ord(c) < 32 for c in value):
            result[key] = value
    duration = m.get("durationMs")
    if type(duration) in (int, float) and math.isfinite(duration) and duration >= 0:
        result["duration_ms"] = duration
    return result


def summarize_message(m: dict, *, include_tools: bool, max_chars: int) -> dict | None:
    ts = ts_to_ms(m.get("timestamp") or m.get("completedAt"))
    base: dict[str, Any] = {"id": m.get("id"), "ts": iso_local(ts), **_message_metadata(m)}
    if is_tool(m):
        if not include_tools:
            return None
        inp = m.get("input")
        if isinstance(inp, dict):
            brief = inp.get("command") or inp.get("description") or inp.get("file_path") or inp.get("pattern")
            brief = brief if isinstance(brief, str) else json.dumps(inp, ensure_ascii=False)
        else:
            brief = str(inp or "")
        result = m.get("result")
        parts = [clip(str(brief), min(300, max_chars))]
        if isinstance(result, str) and result:
            parts.append(result)
        if isinstance(m.get("denyReason"), str) and m["denyReason"]:
            parts.append(m["denyReason"])
        completed = ts_to_ms(m.get("completedAt"))
        base.update(role="tool", tool=m.get("toolName"), status=m.get("status"),
                    text=clip("\n\n".join(parts), max_chars))
        for source, target in (("denied", "denied"), ("isDeferred", "deferred")):
            if type(m.get(source)) is bool:
                base[target] = m[source]
        if completed is not None:
            base["completed_at"] = iso_local(completed)
        started = ts_to_ms(m.get("timestamp"))
        if started is not None and completed is not None and completed >= started:
            base["duration_ms"] = completed - started
        return base
    role = m.get("role") or m.get("type") or "?"
    text = _content_text(m.get("content") if "content" in m else m.get("text"))
    if not text.strip():
        return None
    base.update(role=role, text=clip(text, max_chars))
    return base


def summarize_pending(p: Any, kind: str) -> dict | None:
    if not isinstance(p, dict):
        return None
    out: dict[str, Any] = {"kind": kind, "toolUseId": p.get("toolUseId")}
    if kind == "ask_user":
        qs = []
        for q in p.get("questions") or []:
            if isinstance(q, dict):
                qs.append(
                    {
                        "question": clip(str(q.get("question", "")), 400),
                        "header": q.get("header"),
                        "multiSelect": bool(q.get("multiSelect")),
                        "options": [
                            clip(str(o.get("label", o) if isinstance(o, dict) else o), 120)
                            for o in (q.get("options") or [])
                        ][:10],
                    }
                )
        out["questions"] = qs
    else:
        out["toolName"] = p.get("toolName")
        inp = p.get("input")
        out["input_preview"] = clip(json.dumps(inp, ensure_ascii=False) if inp is not None else "", 400)
    return out
