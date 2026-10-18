"""Separate explicit grants for local conversation content, never inferred from usage."""
from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass, field
from pathlib import Path

from .errors import ConfigError

ADAPTERS = ("codex-history-jsonl-v1", "claude-history-jsonl-v1")


@dataclass(frozen=True)
class HistorySource:
    source_id: str
    workspace_id: str
    adapter: str
    path: Path = field(repr=False)
    session_id: str = field(repr=False)
    readers: tuple[str, ...] = ()

    @property
    def binding(self):
        # Reader withdrawal changes current authorization, not the original
        # content identity. Rebinding content cannot transfer saved history.
        return hashlib.sha256(json.dumps([self.source_id, self.workspace_id, self.adapter,
            str(self.path), self.session_id]).encode()).hexdigest()


@dataclass(frozen=True)
class HistoryConfig:
    interval_s: float = 60
    sources: tuple[HistorySource, ...] = ()


def parse_history(data):
    raw = data.get("session_history", {})
    if not isinstance(raw, dict) or set(raw) - {"interval_s", "sources"}:
        raise ConfigError("[session_history] supports interval_s and explicit sources only")
    interval = raw.get("interval_s", 60)
    if type(interval) not in (int, float) or not math.isfinite(interval) or not 10 <= interval <= 3600:
        raise ConfigError("session_history.interval_s must be 10–3600 seconds")
    entries = raw.get("sources", [])
    if not isinstance(entries, list) or len(entries) > 16:
        raise ConfigError("session_history.sources supports at most 16 explicit files")
    sources, ids, sessions, paths = [], set(), set(), set()
    for entry in entries:
        fields = {"source_id", "workspace_id", "adapter", "path", "session_id", "readers"}
        if not isinstance(entry, dict) or set(entry) != fields:
            raise ConfigError("History sources require exact identity, file and reader fields")
        for name in ("source_id", "session_id"):
            if not isinstance(entry[name], str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}", entry[name]):
                raise ConfigError("History source and session IDs must be bounded opaque identifiers")
        if not isinstance(entry["workspace_id"], str) or not re.fullmatch(r"ws_[0-9a-f]{32}", entry["workspace_id"]):
            raise ConfigError("History source requires an explicit PM workspace")
        if not isinstance(entry["adapter"], str) or entry["adapter"] not in ADAPTERS:
            raise ConfigError("Unsupported history adapter")
        readers = entry["readers"]
        if (not isinstance(readers, list) or len(readers) > 64
                or any(not isinstance(a, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}", a) for a in readers)
                or len(set(readers)) != len(readers)):
            raise ConfigError("History readers must be distinct explicit actor IDs; an empty list withdraws access")
        path = entry["path"]
        if not isinstance(path, str) or "\0" in path or ".." in Path(path).parts:
            raise ConfigError("History source must name one absolute local JSONL file")
        path = Path(path).expanduser()
        if not path.is_absolute() or path.suffix != ".jsonl" or any(c in str(path) for c in "*?[]"):
            raise ConfigError("History source must name one absolute local JSONL file")
        identity = (entry["adapter"], entry["session_id"])
        if entry["source_id"] in ids or identity in sessions or path in paths:
            raise ConfigError("Bind each history source, session and file only once")
        ids.add(entry["source_id"])
        sessions.add(identity)
        paths.add(path)
        sources.append(HistorySource(**{**entry, "path": path, "readers": tuple(readers)}))
    return HistoryConfig(float(interval), tuple(sources))
