"""Versioned, read-only BAT source policy for the current authorized account.

Admission is the common Python/native subset. Existing Python references retain
their historical decoding rules until supported BAT writers can be audited.
No migration, key rewriting, alternate-account search or encrypted-store fallback.
"""
from __future__ import annotations

import ipaddress
import json
import math
import os
import stat
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Literal

from ._private_files import read_private

BAT_SOURCE_POLICY = 1
DEFAULT_BAT_PROFILES_DIR = "~/.local/share/org.tonyq.better-agent-terminal/profiles"
STORE_BOUND = 1024 * 1024
TOKEN_BOUND = 64 * 1024
PROFILE_BOUND = 200
TokenState = Literal["available", "encrypted", "unavailable", "unsupported"]


class TokenMode(Enum):
    ADMISSION = "admission"
    LEGACY_RUNTIME = "legacy_python_runtime"


class SourceUnavailable(ValueError):
    """Only a fixed reason code; never a path or exception containing secrets."""


def _directory(path: Path) -> bool:
    try:
        metadata = path.stat()
        if not stat.S_ISDIR(metadata.st_mode):
            raise SourceUnavailable("DATA_DIRECTORY_INVALID")
        # stat alone can succeed for a directory whose contents cannot be read.
        with os.scandir(path):
            pass
        return True
    except FileNotFoundError:
        return False
    except NotADirectoryError:
        raise SourceUnavailable("DATA_DIRECTORY_INVALID") from None
    except OSError:
        raise SourceUnavailable("DATA_DIRECTORY_UNREADABLE") from None


def select_profiles_directory(
    profiles_dir: str | Path | None = None, *, platform: str,
    environment: Mapping[str, str], home: Path,
) -> Path:
    """Re-evaluate each call, then let the caller bind the selected source.

    Only an absent Windows data root allows the old-name fallback. A selected
    root with absent profiles/store never redirects to another source. Missing
    paths remain selected (as in Fleet); readers report the missing file later.
    """
    if profiles_dir:
        text = str(profiles_dir)
        path = home / text[2:] if text.startswith("~/") or text.startswith("~\\") else (
            home if text == "~" else Path(text).expanduser())
    elif platform == "win32":
        roaming = environment.get("APPDATA")
        if not roaming:
            raise SourceUnavailable("DATA_DIRECTORY_UNAVAILABLE")
        root = Path(roaming) / "BetterAgentTerminal"
        if not _directory(root):
            legacy = Path(roaming) / "org.tonyq.better-agent-terminal"
            if _directory(legacy):
                root = legacy
        path = root / "profiles"
    elif platform == "darwin":
        path = home / "Library/Application Support/org.tonyq.better-agent-terminal/profiles"
    else:
        path = home / DEFAULT_BAT_PROFILES_DIR[2:]
    _directory(path)
    return path


@dataclass(frozen=True)
class TokenResult:
    state: TokenState
    reason: str
    token: str | None = field(default=None, repr=False)


@dataclass(frozen=True)
class TokenStore:
    state: TokenState
    reason: str
    mode: TokenMode
    _tokens: dict = field(default_factory=dict, repr=False)

    def lookup(self, profile_id: str) -> TokenResult:
        if self.state != "available":
            return TokenResult(self.state, self.reason)
        token = self._tokens.get(profile_id)
        if not isinstance(token, str) or not token:
            reason = "TOKEN_MISSING"
            if self.mode is TokenMode.ADMISSION and any(
                key.lower() == profile_id.lower() for key in self._tokens
            ):
                reason = "TOKEN_KEY_MISMATCH" if profile_id not in self._tokens else "TOKEN_INVALID"
            return TokenResult("unavailable", reason)
        if self.mode is TokenMode.ADMISSION:
            try:
                valid = len(token.encode("utf-8")) <= TOKEN_BOUND and "\0" not in token
            except UnicodeError:
                valid = False
            if not valid:
                return TokenResult("unsupported", "TOKEN_INVALID")
        return TokenResult("available", "TOKEN_AVAILABLE", token)


class _AmbiguousStore(ValueError):
    pass


def _unique_object(pairs):
    # Match native strict_json's decoded, lowercase collision check at every
    # level. Lookup itself remains exact, including for non-ASCII profile IDs.
    result, seen = {}, set()
    for key, value in pairs:
        folded = key.lower()
        if folded in seen:
            raise _AmbiguousStore
        seen.add(folded)
        result[key] = value
    return result


def _invalid_constant(_value):
    raise ValueError


def _native_json_values(value, depth=0):
    """Admission must also fit serde_json's Unicode, number and nesting limits."""
    if isinstance(value, (dict, list)):
        # serde_json's default recursion budget refuses the 128th container.
        if depth >= 127:
            raise ValueError
        if isinstance(value, dict):
            for key in value:
                key.encode("utf-8")
            values = value.values()
        else:
            values = value
        for child in values:
            _native_json_values(child, depth + 1)
    elif isinstance(value, str):
        value.encode("utf-8")
    elif isinstance(value, (int, float)) and not math.isfinite(value):
        raise ValueError


def parse_token_store(contents: str, *, mode: TokenMode) -> TokenStore:
    """Decode once; all states/reasons/reprs are free of credential material."""
    strict = mode is TokenMode.ADMISSION

    def decode(value):
        if strict:
            decoded = json.loads(value, object_pairs_hook=_unique_object, parse_constant=_invalid_constant)
            _native_json_values(decoded)
            return decoded
        return json.loads(value)

    try:
        if len(contents.encode("utf-8")) > STORE_BOUND:
            return TokenStore("unavailable", "TOKEN_STORE_UNREADABLE", mode)
        raw = decode(contents)
        if not isinstance(raw, dict):
            return TokenStore("unsupported", "TOKEN_STORE_FORMAT", mode)
        enc = raw.get("enc")
        encrypted = enc is not False if strict else enc not in (False, None)
        if encrypted:
            return TokenStore("encrypted", "TOKEN_STORE_ENCRYPTED", mode)
        data = raw.get("data")
        if strict and not isinstance(data, str):
            return TokenStore("unsupported", "TOKEN_DATA_NOT_STRING", mode)
        if isinstance(data, str):
            data = decode(data)
        if data is not None and not isinstance(data, dict):
            return TokenStore("unsupported", "TOKEN_STORE_FORMAT", mode)
        tokens = (data or {}).get("tokens")
        if tokens is not None and not isinstance(tokens, dict):
            return TokenStore("unsupported", "TOKEN_STORE_FORMAT", mode)
        return TokenStore("available", "TOKEN_STORE_AVAILABLE", mode, tokens or {})
    except _AmbiguousStore:
        return TokenStore("unsupported", "TOKEN_STORE_AMBIGUOUS", mode)
    except (ValueError, OverflowError, RecursionError):
        return TokenStore("unsupported", "TOKEN_STORE_FORMAT", mode)


def read_token_store(directory: Path, *, mode: TokenMode) -> TokenStore:
    try:
        contents = read_private(directory / "remote-tokens.enc.json", STORE_BOUND)
    except (OSError, ValueError):
        return TokenStore("unavailable", "TOKEN_STORE_UNREADABLE", mode)
    return parse_token_store(contents, mode=mode)


@dataclass(frozen=True)
class ProfileListing:
    profiles: list[dict]
    reason: str
    skipped: list[dict]


def _profile_text(value, maximum=256):
    if (not isinstance(value, str) or not value or value != value.strip()
            or len(value) > maximum or any(ord(c) < 32 or 127 <= ord(c) <= 159 for c in value)):
        raise ValueError
    return value


def _remote_profile(profile):
    # Import at use time: config itself imports the token policy from this module.
    from .config import normalize_fingerprint

    ident = _profile_text(profile.get("id"))
    host = _profile_text(profile.get("remoteHost"))
    if host.startswith("[") and host.endswith("]"):
        host = host[1:-1]
        if ":" not in host:
            raise ValueError
    if not host or "%" in host:
        raise ValueError
    if ":" in host:
        ipaddress.IPv6Address(host)
        url_host = f"[{host}]"
    else:
        if any(c.isspace() or c in "/\\@?#[]" for c in host):
            raise ValueError
        url_host = host
    port = profile.get("remotePort")
    # Retain bounded decimal-string inputs from the existing listing. Never
    # coerce bools/floats or depend on an interpreter's integer digit limit.
    if isinstance(port, str):
        _profile_text(port)
        if port.isascii() and port.isdecimal():
            port = int(port)
    if type(port) is not int or not 1 <= port <= 65535:
        raise ValueError
    return {"id": ident, "name": _profile_text(profile.get("name") or ident),
            "endpoint": {"host": host, "port": port, "url": f"wss://{url_host}:{port}/"},
            "fingerprint": normalize_fingerprint(_profile_text(profile.get("remoteFingerprint"), 200)),
            "remote_profile_id": _profile_text(profile.get("remoteProfileId") or "default")}


def parse_profile_index(contents: str) -> ProfileListing:
    """Listing choices, not the Kit's strict reviewed-copy drift validation.

    Additive BAT fields are ignored. Invalid/non-remote entries are skipped with
    fixed diagnostics; all competing IDs are excluded, so order cannot pick one.
    """
    from .errors import ConfigError

    try:
        if len(contents.encode("utf-8")) > STORE_BOUND:
            raise ValueError
        index = json.loads(contents, object_pairs_hook=_unique_object, parse_constant=_invalid_constant)
        _native_json_values(index)
        profiles = index.get("profiles", [])
        if not isinstance(profiles, list) or len(profiles) > PROFILE_BOUND:
            raise ValueError
    except (ValueError, AttributeError, OverflowError, RecursionError):
        return ProfileListing([], "PROFILE_INDEX_INVALID", [])
    ids: dict[str, int] = {}
    for profile in profiles:
        if isinstance(profile, dict) and isinstance(profile.get("id"), str):
            key = profile["id"].lower()
            ids[key] = ids.get(key, 0) + 1
    result, skipped = [], []
    for entry, profile in enumerate(profiles):
        reason = None
        if not isinstance(profile, dict) or profile.get("type") != "remote":
            reason = "PROFILE_NOT_REMOTE"
        elif isinstance(profile.get("id"), str) and ids[profile["id"].lower()] > 1:
            reason = "PROFILE_ID_AMBIGUOUS"
        else:
            try:
                result.append(_remote_profile(profile))
            except (ValueError, TypeError, ConfigError):
                reason = "PROFILE_INCOMPLETE"
        if reason:
            skipped.append({"entry": entry, "reason": reason})
    reason = ("PROFILE_INDEX_PARTIAL" if skipped else "PROFILE_CATALOG_AVAILABLE") if result else "NO_COMPLETE_PROFILES"
    return ProfileListing(result, reason, skipped)
