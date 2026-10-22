"""Frozen v1 public source descriptor; private snapshots contain only digests."""
from __future__ import annotations

import hashlib
import json
import re

from .connection_endpoints import setup_endpoint
from .connection_sources import BAT_SOURCE_POLICY


def source_digest(*, kind, profile_reference, association, availability_reason, source_state):
    """Bind source inputs, excluding discovered server lists and setup choices."""
    if kind not in {"bat_profile", "staged_secret"}:
        raise ValueError("Invalid source kind")
    if profile_reference is not None and (not isinstance(profile_reference, str) or not 1 <= len(profile_reference) <= 256
            or profile_reference != profile_reference.strip() or any(ord(c) < 32 for c in profile_reference)):
        raise ValueError("Invalid profile reference")
    if not isinstance(association, dict) or set(association) != {"url", "fingerprint"}:
        raise ValueError("Invalid endpoint association")
    url, pin = association["url"], association["fingerprint"]
    if setup_endpoint(url) != url:
        raise ValueError("Invalid endpoint")
    if not isinstance(pin, str) or not re.fullmatch(r"[0-9A-F]{64}", pin):
        raise ValueError("Invalid fingerprint")
    if not isinstance(availability_reason, str) or not re.fullmatch(r"[A-Z][A-Z0-9_]{0,63}", availability_reason):
        raise ValueError("Invalid availability reason")
    if not isinstance(source_state, dict) or not 1 <= len(source_state) <= 16 or any(
        not isinstance(k, str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", k)
        or not isinstance(v, str) or not re.fullmatch(r"[0-9a-f]{64}", v) for k, v in source_state.items()
    ):
        raise ValueError("Invalid source snapshot")
    value = {"policy_version": BAT_SOURCE_POLICY, "kind": kind, "profile_reference": profile_reference,
        "association": association, "availability_reason": availability_reason, "source_state": source_state}
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def build_source_descriptor(*, kind, profile_reference, association, availability_reason, source_state):
    digest = source_digest(kind=kind, profile_reference=profile_reference, association=association,
        availability_reason=availability_reason, source_state=source_state)
    return {"reference": "source_" + digest, "kind": kind, "policy_version": BAT_SOURCE_POLICY,
        "source_digest": digest, "profile_reference": profile_reference, "association": dict(association),
        "availability_reason": availability_reason}
