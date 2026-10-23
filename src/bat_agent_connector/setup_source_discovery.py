"""Explicit source snapshots and discovery; no staging or durable effects."""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import re
import secrets
import stat

from . import platform_files
from .config import HostConfig, normalize_fingerprint
from .connection_source_descriptor import build_source_descriptor
from .connection_sources import SourceUnavailable
from .errors import ConfigError
from .operations import OperationError

DISCOVERY_TIMEOUT_S = 25


def _sha(value):
    return hashlib.sha256(value).hexdigest()


def _secret_digest(daemon, value):
    # Already persisted by Dashboard sync installation; this read creates no key
    # or row. Domain separation keeps source commitments apart from sync MACs.
    row = daemon.journal.db.execute("SELECT signing_key FROM api_sync_metadata WHERE singleton=1").fetchone()
    key = bytes.fromhex(row["signing_key"])
    return hmac.digest(key, b"bat-setup-source-state/v1\0" + value, "sha256").hex()


def _profile_snapshot(daemon, directory):
    # BAT's index is public metadata, unlike the private token store.
    path = directory / "index.json"
    if path.is_symlink():
        raise ValueError
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
    with os.fdopen(fd, "rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError
        index = stream.read(1024 * 1024 + 1)
    if len(index) > 1024 * 1024:
        raise ValueError
    store_bytes = platform_files.read_private(directory / "remote-tokens.enc.json")  # Default bound: 1 MiB.
    return {"directory": _sha(json.dumps([str(directory.absolute()), str(directory.resolve())]).encode()), "index": _sha(index),
        "credential": _secret_digest(daemon, store_bytes)}


def resolve_source(daemon, document, params):
    """Re-select D3 each time and bind the complete source, including expiry."""
    from . import managed_setup as setup
    selected = params.get("import_profile_id")
    if selected is not None:
        if set(params) & {"url", "fingerprint", "secret_ref"}:
            raise setup._error("INVALID_PARAMS", "Choose exactly one connection source")
        setup._text(selected, "profile reference")
        try:
            directory = setup._profile_directory(document)
            snapshot = _profile_snapshot(daemon, directory)
            profiles, _ = setup._profiles(document)
            profile = next((p for p in profiles if p["id"] == selected and p["token_available"]), None)
            if not profile:
                raise ValueError
            # Refuse an inconsistent read instead of blessing a newer snapshot.
            if setup._profile_directory(document) != directory or _profile_snapshot(daemon, directory) != snapshot:
                raise ValueError
        except (OSError, ValueError, SourceUnavailable):
            raise setup._error("PROFILE_UNAVAILABLE", "Select a readable BAT profile", 409) from None
        url, fingerprint = profile["url"], profile["fingerprint"]
        token_ref = "bat-profile:" + selected
        profile_id = profile["profile_id"]
        kind = "bat_profile"
    else:
        if not {"url", "fingerprint", "secret_ref"} <= params.keys():
            raise setup._error("INVALID_PARAMS", "Choose a staged credential, endpoint and fingerprint or a BAT profile")
        token_ref = setup._secret(daemon, params["secret_ref"], "bat")
        path = setup._installation(daemon) / "config/setup-secrets"
        ref = params["secret_ref"]
        try:
            snapshot = {"reference": _sha(ref.encode()),
                "metadata": _secret_digest(daemon, platform_files.read_private(path / (ref + ".json"), max_bytes=4096)),
                "credential": _secret_digest(daemon, platform_files.read_private(path / (ref + ".token"), max_bytes=4096))}
        except (OSError, ValueError):
            raise setup._error("INVALID_SECRET", "Staged credential is unavailable", 409) from None
        url = setup._url(params["url"])
        fingerprint = normalize_fingerprint(setup._text(params["fingerprint"], "fingerprint", 200))
        directory, profile_id, kind = "", "default", "staged_secret"
    descriptor = build_source_descriptor(kind=kind, profile_reference=selected,
        association={"url": url, "fingerprint": fingerprint}, availability_reason="SOURCE_AVAILABLE", source_state=snapshot)
    host = HostConfig(name="discovery", url=url, fingerprint=fingerprint, token_ref=token_ref,
        profile_id=setup._text(params.get("profile_id", profile_id), "profile ID"), bat_profiles_dir=str(directory),
        writes=False, orchestrate=False)
    return descriptor, host


def validate_source(daemon, document, params, *, return_source=False):
    """The same refusal at preview, admission, execution and final publication."""
    from . import managed_setup as setup
    digest = params.get("source_digest")
    if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise setup._error("INVALID_PARAMS", "Supply a source digest from discovery")
    if "profile_id" in params:
        setup._text(params["profile_id"], "profile ID")
    if "import_profile_id" in params:
        setup._text(params["import_profile_id"], "profile reference")
        if set(params) & {"url", "fingerprint", "secret_ref"}:
            raise setup._error("INVALID_PARAMS", "Choose exactly one connection source")
    else:
        if not {"url", "fingerprint", "secret_ref"} <= params.keys():
            raise setup._error("INVALID_PARAMS", "Supply the complete discovered source")
        if not isinstance(params["secret_ref"], str) or not setup.SECRET_REF.fullmatch(params["secret_ref"]):
            raise setup._error("INVALID_PARAMS", "Supply a staged credential reference")
        setup._url(params["url"])
        try:
            normalize_fingerprint(setup._text(params["fingerprint"], "fingerprint", 200))
        except ConfigError:
            raise setup._error("INVALID_PARAMS", "Supply a valid fingerprint") from None
    try:
        current, _ = resolve_source(daemon, document, params)
        if not isinstance(digest, str) or not secrets.compare_digest(digest, current["source_digest"]):
            raise ValueError
    except (OperationError, ConfigError, OSError, ValueError, TypeError):
        raise setup._error("SETUP_REVIEW_CHANGED", "Settings changed; review again", 409) from None

    return current if return_source else None


async def discover(daemon, principal, body):
    from . import managed_setup as setup
    setup._installation(daemon, principal)
    if not isinstance(body, dict) or set(body) - {"revision", "import_profile_id", "url", "fingerprint", "secret_ref", "profile_id"}:
        raise setup._error("INVALID_PARAMS", "Discovery accepts only a source, revision and optional profile ID")
    _, _, document, revision = setup._configuration(daemon)
    if body.get("revision") != revision:
        raise setup._error("CONFIGURATION_CHANGED", "Configuration changed; review setup again", 409)
    try:
        source, host = resolve_source(daemon, document, body)
    except ConfigError:
        raise setup._error("INVALID_CONFIGURATION", "Check the selected host trust and connection settings") from None
    try:
        deadline = asyncio.get_running_loop().time() + DISCOVERY_TIMEOUT_S
        result = await setup._read_host(host, discovery=True, selected=body.get("profile_id"), deadline=deadline)
    except asyncio.TimeoutError:
        raise setup._error("SETUP_DISCOVERY_TIMEOUT", "Discovery timed out; find profiles and workspaces again", 409) from None
    except OperationError:
        raise
    except Exception:
        raise setup._error("SETUP_PROBE_FAILED", "Connection verification failed; check trust, sign-in and reachability", 409) from None
    if setup._configuration(daemon)[3] != revision:
        raise setup._error("CONFIGURATION_CHANGED", "Configuration changed; review setup again", 409)
    validate_source(daemon, document, {**body, "source_digest": source["source_digest"]})
    return {"source": source, **result}
