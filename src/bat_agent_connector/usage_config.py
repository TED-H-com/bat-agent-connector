"""Explicit, host-local usage sources. Never discover conversations by directory/name."""
from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass, field
from pathlib import Path

from .errors import ConfigError

ADAPTERS = ("codex-rollout-v1", "claude-transcript-v1")


@dataclass(frozen=True)
class UsageSource:
    source_id: str
    workspace_id: str
    adapter: str
    path: Path = field(repr=False)
    session_id: str = field(repr=False)
    role: str = "unassigned"
    quota_pool: str | None = None

    host: str | None = None
    host_identity: str | None = field(default=None, repr=False)

    @property
    def binding(self):
        # Private paths/session identifiers never appear in the API or diagnostics.
        value = [self.source_id, self.workspace_id, self.adapter, str(self.path), self.session_id,
                 self.role, self.quota_pool]
        if self.host is not None:
            value += [self.host, self.host_identity]
        return hashlib.sha256(json.dumps(value).encode()).hexdigest()


@dataclass(frozen=True)
class UsageConfig:
    interval_s: float = 60
    sources: tuple[UsageSource, ...] = ()


def parse_usage(data, hosts=None):
    raw = data.get("usage", {})
    if not isinstance(raw, dict) or set(raw) - {"interval_s", "sources"}:
        raise ConfigError("[usage] supports interval_s and explicit sources only")
    interval = raw.get("interval_s", 60)
    if type(interval) not in (int, float) or not math.isfinite(interval) or not 10 <= interval <= 3600:
        raise ConfigError("usage.interval_s must be 10–3600 seconds")
    entries = raw.get("sources", [])
    if not isinstance(entries, list) or len(entries) > 64:
        raise ConfigError("usage.sources must contain at most 64 explicit local files")
    result, ids, sessions, paths = [], set(), set(), set()
    for entry in entries:
        fields = {"source_id", "workspace_id", "adapter", "path", "session_id", "role", "quota_pool", "host"}
        if not isinstance(entry, dict) or set(entry) - fields:
            raise ConfigError("Unknown usage source fields")
        for key in ("source_id", "session_id", "role"):
            value = entry.get(key, "unassigned" if key == "role" else None)
            if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}", value):
                raise ConfigError("Usage source needs bounded opaque IDs and role")
        if not re.fullmatch(r"ws_[0-9a-f]{32}", str(entry.get("workspace_id", ""))):
            raise ConfigError("Usage source needs an explicit PM workspace ID")
        if entry.get("adapter") not in ADAPTERS:
            raise ConfigError("Unsupported usage adapter")
        pool = entry.get("quota_pool")
        if pool is not None and (entry["adapter"] != ADAPTERS[0] or not isinstance(pool, str)
                                 or not re.fullmatch(r"pool_[0-9a-f]{32}", pool)):
            raise ConfigError("quota_pool must be an opaque pool ID for a Codex source")
        host = entry.get("host")
        if "host" in entry and (not isinstance(host, str) or host not in (hosts or {})):
            raise ConfigError("Usage source host must name a configured fleet host")
        path = entry.get("path")
        if not isinstance(path, str) or "\0" in path or ".." in Path(path).parts:
            raise ConfigError("Usage source path must name one absolute local JSONL file")
        path = Path(path) if host else Path(path).expanduser()
        if not path.is_absolute() or path.suffix != ".jsonl" or any(c in str(path) for c in "*?[]"):
            raise ConfigError("Usage source path must name one absolute local JSONL file")
        identity = (host, entry["adapter"], entry["session_id"])
        if entry["source_id"] in ids or identity in sessions or (host, path) in paths:
            raise ConfigError("Duplicate usage source, session or path; bind each session once")
        ids.add(entry["source_id"])
        sessions.add(identity)
        paths.add((host, path))
        host_identity = None
        if host:
            configured = hosts[host]
            host_identity = hashlib.sha256(json.dumps([configured.name, configured.url,
                configured.fingerprint, configured.profile_id]).encode()).hexdigest()
        result.append(UsageSource(**{**entry, "path": path, "host_identity": host_identity}))
    return UsageConfig(float(interval), tuple(result))
