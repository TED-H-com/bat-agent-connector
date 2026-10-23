"""Personal installation onboarding through the central operation journal.

Tokens are staged in private files; only opaque references enter durable intents.
Configuration changes are revision-bound, atomic, and recovered by digest readback.
BAT probes are read-only and retain TLS pins. GitHub onboarding never writes upstream.
"""
from __future__ import annotations

import asyncio
import copy
import dataclasses
import datetime
import hashlib
import json
import math
import os
import re
import secrets
import sys
import time
import urllib.request
from pathlib import Path

from . import dashboard_sync, platform_files, service
from .artifact_host import ArtifactHost
from .client import BatClient
from .config import git_transport, normalize_fingerprint, parse_config, tomllib
from .connection_contract import VERSION as CONNECTION_VERSION
from .connection_endpoints import setup_endpoint
from .connection_sources import (
    SourceUnavailable,
    TokenMode,
    read_profile_index,
    read_token_store,
    select_profiles_directory,
)
from .errors import ConfigError, TokenUnavailable
from .github import GitHubClient
from .inventory import _observation_reason
from .operations import RERUN, TERMINAL, ActionDef, NeedsAttention, OperationError, Uncertain
from .redact import redact, redact_secrets, register_secret
from .task_verifier import parse_settings

MAX_CONFIG = 1024 * 1024
SECRET_REF = re.compile(r"setup_[0-9a-f]{32}")
HOST = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}")
REPOSITORY = re.compile(r"[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}")


def _error(code, message, status=422):
    return OperationError(code, message, status)


def _installation(daemon, principal=None):
    installation = getattr(daemon, "managed_installation", None)
    if not installation:
        raise _error("NOT_MANAGED", "Setup is available only for this managed installation", 403)
    if principal is not None and (not principal.allows("manage")
            or principal.actor != installation["actor"]
            or dashboard_sync.identity(daemon.journal, principal)["principal_id"] != installation["principal_id"]):
        raise _error("FORBIDDEN", "Setup belongs to the installation's personal identity", 403)
    root = Path(installation["data_dir"])
    platform_files.check_private(root, directory=True)
    return root


def _configuration(daemon):
    path = _installation(daemon) / "config" / "hosts.toml"
    raw = platform_files.read_private(path, max_bytes=MAX_CONFIG)
    return path, raw, tomllib.loads(raw.decode()), hashlib.sha256(raw).hexdigest()


def _toml(value):
    """Encode the complete parsed document, including unknown operator sections.

    Nested inline tables avoid a lossy hand-maintained list of supported sections.
    The roundtrip assertion in _candidate also refuses unsupported TOML types.
    """
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if type(value) is bool:
        return "true" if value else "false"
    if type(value) in (int, float):
        if isinstance(value, float) and math.isnan(value):
            return "nan"
        return str(value)
    if isinstance(value, (datetime.datetime, datetime.date, datetime.time)):
        return value.isoformat()
    if isinstance(value, list):
        return "[" + ", ".join(_toml(item) for item in value) + "]"
    if isinstance(value, dict):
        return "{ " + ", ".join(f"{_toml(key)} = {_toml(item)}" for key, item in value.items()) + " }"
    raise _error("INVALID_CONFIGURATION", "Existing configuration cannot be safely rewritten")


def _encode(document):
    return ("\n".join(f"{_toml(key)} = {_toml(value)}" for key, value in document.items()) + "\n").encode()


def _profile_directory(document):
    return select_profiles_directory(document.get("bat", {}).get("profiles_dir"),
        platform=sys.platform, environment=os.environ, home=Path.home())


def _url(value):
    try:
        return setup_endpoint(value)
    except ValueError:
        raise _error("INVALID_HOST", "A pinned BAT wss origin without credentials or query is required") from None


def _text(value, field, maximum=256):
    if not isinstance(value, str) or not value or value != value.strip() or len(value) > maximum or any(ord(c) < 32 for c in value):
        raise _error("INVALID_PARAMS", f"{field} must be a bounded nonempty string")
    return value


def _profiles(document):
    try:
        directory = _profile_directory(document)
    except SourceUnavailable as error:
        return [], str(error)
    listing = read_profile_index(directory)
    if listing.reason in {"PROFILE_INDEX_INVALID", "PROFILE_INDEX_UNAVAILABLE"}:
        return [], "BAT profiles are unavailable; enter the host and its trusted fingerprint"
    store = read_token_store(directory, mode=TokenMode.ADMISSION)
    result = []
    for profile in listing.profiles:
        token = store.lookup(profile["id"])
        result.append({"id": profile["id"], "name": profile["name"],
            "url": profile["endpoint"]["url"], "fingerprint": profile["fingerprint"],
            "profile_id": profile["remote_profile_id"],
            "token_available": token.state == "available", "token_state": token.state})
    return result, (None if result else "No complete remote BAT profiles were found")


def _busy(daemon, operation_id=""):
    marks = ",".join("?" for _ in TERMINAL)
    if daemon.journal.db.execute(f"SELECT 1 FROM operations WHERE operation_id<>? AND status NOT IN ({marks}) LIMIT 1",  # noqa: S608 - placeholders only
                                (operation_id, *TERMINAL)).fetchone():
        return True
    return daemon.journal.db.execute("SELECT 1 FROM tasks WHERE state NOT IN ('done','failed') LIMIT 1").fetchone() is not None


def _connection_state(daemon, name, host):
    """A missing/closed write socket is not a failed connectivity check.

    Inventory owns the read-only observations used by sessions, and deliberately
    closes its sockets between scans. Only current, recent evidence can claim a
    connection result. Setup GET stays read-only and never starts a second probe.
    """
    client = daemon.fleet._clients.get(name)
    if client and client.connected and (client.host.url, client.host.fingerprint, client.host.profile_id) == (host.url, host.fingerprint, host.profile_id):
        return {"connected": True, "connection_status": "connected", "connection_checked_at": None}
    inventory = daemon.inventory
    observed_host = inventory.config.hosts.get(name)
    same_source = observed_host and (observed_host.url, observed_host.fingerprint, observed_host.profile_id) == (host.url, host.fingerprint, host.profile_id)
    row = (
        inventory.current_source_evidence(name).observation if same_source else None
    )
    checked = row["last_attempt_at"] if row else None
    recent = checked is not None and 0 <= time.time() - checked <= inventory.settings.stale_after_s
    status = ("connected" if row["reachable"] else "unreachable") if recent else "checking"
    return {"connected": None if status == "checking" else status == "connected",
            "connection_status": status, "connection_checked_at": checked}


def _connection_observation(daemon, name, host):
    """Read Inventory's facts, keeping a live writer's transport fact separate.

    The three legacy fields above deliberately retain their historical mapping.
    They are not inputs to this versioned display contract or any action gate.
    """
    inventory = daemon.inventory
    current = inventory.current_source_evidence(name)
    row = current.observation
    now = time.time()
    reason = current.reason or _observation_reason(row, now, inventory.settings.stale_after_s)
    observed = row.get("last_attempt_at") if row else None
    deadline = inventory._valid_until(row.get("last_success_at") if row else None)
    source = {**(current.source or {}), "kind": "hosts_observed", "ref": f"hosts_observed:{name}"}

    def fact(value, code):
        return {"value": value, "observed_at": observed, "valid_until": deadline, "reason": code, "source": source}

    value = "ready" if reason is None else "unknown" if reason in {"never_observed", "DISCOVERY_SCOPE_CHANGED"} else "unavailable"
    facets = {"connection": fact(value, reason)}
    failure = row.get("failure_reason") or "unknown" if row and not row.get("reachable") else None
    for name_ in ("transport", "service", "trust_sign_in"):
        facets[name_] = fact("ready" if reason is None else "unknown", reason)
    if failure:
        axis = {"transport": "transport", "tls_trust": "trust_sign_in", "auth_or_protocol": "trust_sign_in",
                "credential_missing": "trust_sign_in", "service_refused": "service", "service_timeout": "service"}.get(failure)
        if axis:
            facets[axis] = fact("missing" if failure == "credential_missing" else "unavailable", failure)
        else:
            facets["service"] = fact("unknown", "unknown")
    client = daemon.fleet._clients.get(name)
    if client and client.connected and client.host == host:
        # connected means a reader started AFTER authentication and negotiation.
        # Observing it now proves transport liveness, never workspace readiness.
        facets["transport"] = {"value": "ready", "observed_at": now, "valid_until": inventory._valid_until(now),
            "reason": "authenticated_socket", "source": {**source, "kind": "fleet_client", "ref": f"fleet_client:{name}"}}
    return {"version": 1, "facets": facets}


def state(daemon, principal):
    _installation(daemon, principal)
    _, _, document, revision = _configuration(daemon)
    profiles, profiles_error = _profiles(document)
    hosts = []
    for name, host in daemon.fleet.config.hosts.items():
        hosts.append({"name": name, "url": host.url, "fingerprint": host.fingerprint,
            "git": host.git, "git_status": daemon.ops.context["git_runner"].status(name),
            "profile_id": host.profile_id, "writes": host.writes, "orchestrate": host.orchestrate,
            "shared_clone_worktrees": host.shared_clone_worktrees,
            "managed_roots": list(host.managed_roots), **_connection_state(daemon, name, host),
            "connection_observation": _connection_observation(daemon, name, host),
            "token_available": host.token_available(),
            "ssh_alias": document.get("managed_setup", {}).get("ssh_hosts", {}).get(name)})
    repos = [{"repository": repo.repository, "bindings": [{"host": host, "workspace_id": workspace}
        for host, workspace in (repo.sync.bindings if repo.sync else ())], "allow_merge": repo.allow_merge,
        "allow_pr_update": repo.allow_pr_update, "allow_integrate": repo.integrate is not None}
        for repo in daemon.fleet.config.github.repos.values()]
    verification = _verification(document)
    return {"revision": revision, "preview_contract_version": 1, "discovery_contract_version": 1, "connection_contract_version": CONNECTION_VERSION, "hosts": hosts, "repositories": repos, "profiles": profiles,
            "verification": {"commands": {name: list(argv) for name, argv in verification.commands.items()},
                             "timeout_s": verification.timeout_s},
            "profiles_error": profiles_error, "busy": _busy(daemon)}


def stage_secret(daemon, principal, body):
    root = _installation(daemon, principal)
    if (not isinstance(body, dict) or set(body) != {"kind", "value"}
            or not isinstance(body["kind"], str) or body["kind"] not in {"bat", "github"}):
        raise _error("INVALID_SECRET", "Secret kind must be bat or github")
    value = body["value"]
    if not isinstance(value, str) or not 8 <= len(value) <= 4096 or any(ord(c) < 33 or ord(c) > 126 for c in value):
        raise _error("INVALID_SECRET", "Enter a bounded credential without whitespace")
    register_secret(value)
    directory = root / "config" / "setup-secrets"
    platform_files.ensure_private_directory(directory)
    configuration = _configuration(daemon)[1].decode()
    pending = " ".join(row[0] for row in daemon.journal.db.execute(
        "SELECT params FROM operations WHERE status NOT IN ('succeeded','failed','cancelled')"))
    for metadata_path in directory.glob("setup_*.json"):
        ref = metadata_path.stem
        if not SECRET_REF.fullmatch(ref) or ref in configuration or ref in pending:
            continue
        try:
            metadata = json.loads(platform_files.read_private(metadata_path, max_bytes=4096))
            if metadata["expires_at"] >= time.time():
                continue
            token_path = directory / (ref + ".token")
            platform_files.check_private(token_path)
            token_path.unlink()
            metadata_path.unlink()
        except (OSError, ValueError, TypeError, KeyError):
            continue  # Never erase an unrecognized or linked resource.
    # Bound abandoned staging; referenced credentials are retained by the configuration.
    if len(list(directory.glob("setup_*.json"))) >= 200:
        raise _error("SECRET_STAGING_FULL", "Too many staged credentials; finish or review setup before adding more", 409)
    ref = "setup_" + secrets.token_hex(16)
    platform_files.atomic_write(directory / (ref + ".token"), value.encode())
    platform_files.atomic_write(directory / (ref + ".json"), json.dumps({
        "principal_id": daemon.managed_installation["principal_id"], "kind": body["kind"],
        "expires_at": time.time() + 3600, "sha256": hashlib.sha256(value.encode()).hexdigest()}).encode())
    return {"secret_ref": ref, "kind": body["kind"]}


def _secret(daemon, ref, kind, *, expired=False):
    if not isinstance(ref, str) or not SECRET_REF.fullmatch(ref):
        raise _error("INVALID_SECRET", "Select a staged credential reference")
    directory = _installation(daemon) / "config" / "setup-secrets"
    try:
        metadata = json.loads(platform_files.read_private(directory / (ref + ".json"), max_bytes=4096))
        token = platform_files.read_private(directory / (ref + ".token"), max_bytes=4096)
        if (metadata.get("kind") != kind or metadata.get("principal_id") != daemon.managed_installation["principal_id"]
                or not expired and metadata["expires_at"] < time.time()
                or hashlib.sha256(token).hexdigest() != metadata["sha256"]):
            raise ValueError
    except (OSError, ValueError, TypeError, KeyError):
        raise _error("INVALID_SECRET", "Staged credential is missing, expired or belongs to another setup", 409) from None
    return "file:" + str(directory / (ref + ".token"))


def _candidate(daemon, action, target, params, *, expired=False):
    path, _, source, revision = _configuration(daemon)
    document = copy.deepcopy(source)
    if action == "setup.host":
        name = target.get("host")
        if set(target) != {"host"} or not isinstance(name, str) or not HOST.fullmatch(name):
            raise _error("INVALID_TARGET", "Choose a stable logical host name")
        allowed = {"url", "fingerprint", "profile_id", "secret_ref", "import_profile_id", "writes", "orchestrate", "managed_roots", "ssh_alias", "shared_clone_worktrees", "git", "local_confirmation", "source_digest"}
        if set(params) - allowed:
            raise _error("INVALID_PARAMS", "Only reviewed host connection and permission fields are accepted")
        if "source_digest" in params:
            from .setup_source_discovery import validate_source
            validate_source(daemon, source, params)
        existing = document.setdefault("hosts", {}).get(name, {})
        host = copy.deepcopy(existing)
        try:
            mode = git_transport(params.get("git", host.get("git", "ssh")))
        except ConfigError:
            raise _error("HOST_GIT_INVALID", "Host Git transport must be exactly ssh or local") from None
        if "local_confirmation" in params and type(params["local_confirmation"]) is not bool:
            raise _error("LOCAL_GIT_CONFIRMATION_REQUIRED", "Local Git requires an explicit same-computer and same-filesystem assertion")
        if existing and mode != existing.get("git", "ssh"):
            raise _error("HOST_IDENTITY_CHANGED", "Use a new host name when changing its Git transport", 409)
        if mode == "local" and not existing and params.get("local_confirmation") is not True:
            raise _error("LOCAL_GIT_CONFIRMATION_REQUIRED", "Confirm BAT and central share this computer and filesystem")
        # Preserve old documents byte-for-byte on a no-op when mode was omitted.
        if "git" in params or mode == "local":
            host["git"] = mode
        selected = params.get("import_profile_id")
        if selected is not None:
            profiles, _ = _profiles(document)
            profile = next((item for item in profiles if item["id"] == selected), None)
            if not profile or not profile["token_available"] or set(params) & {"url", "fingerprint", "secret_ref"} or "profile_id" in params and "source_digest" not in params:
                raise _error("PROFILE_UNAVAILABLE", "Select a readable BAT profile or enter its connection and credential", 409)
            host.update({key: profile[key] for key in ("url", "fingerprint", "profile_id")})
            if "profile_id" in params:
                host["profile_id"] = _text(params["profile_id"], "profile ID")
            host.update(token_ref="bat-profile:" + selected, bat_profiles_dir=str(_profile_directory(document)))
        else:
            host.update(url=_url(params.get("url", host.get("url"))),
                fingerprint=normalize_fingerprint(_text(params.get("fingerprint", host.get("fingerprint", "")), "fingerprint", 200)),
                profile_id=_text(params.get("profile_id", host.get("profile_id", "default")), "profile ID"))
            if "secret_ref" in params:
                host["token_ref"] = _secret(daemon, params["secret_ref"], "bat", expired=expired)
            if not host.get("token_ref"):
                raise _error("CREDENTIAL_REQUIRED", "Add a BAT credential before connecting")
        if existing and (_url(existing["url"]) != host["url"]
                         or normalize_fingerprint(existing["fingerprint"]) != host["fingerprint"]
                         or existing.get("profile_id", "default") != host["profile_id"]):
            raise _error("HOST_IDENTITY_CHANGED", "Use a new host name when changing its endpoint, trust or profile", 409)
        for key in ("writes", "orchestrate", "shared_clone_worktrees"):
            value = params.get(key, host.get(key, False))
            if type(value) is not bool:
                raise _error("INVALID_PARAMS", "Host permission choices must be booleans")
            host[key] = value
        host["managed_roots"] = params.get("managed_roots", host.get("managed_roots", []))
        host.setdefault("shared_clone_worktrees", False)
        if host["orchestrate"] and not host["managed_roots"]:
            raise _error("MANAGED_ROOT_REQUIRED", "Choose the dedicated remote root for new managed work")
        if "ssh_alias" in params:
            alias = params["ssh_alias"]
            if not isinstance(alias, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", alias):
                raise _error("INVALID_PARAMS", "SSH alias must name an existing trusted SSH configuration entry")
            document.setdefault("managed_setup", {}).setdefault("ssh_hosts", {})[name] = alias
        document["hosts"][name] = host
    elif action == "setup.repository":
        repository = target.get("repository")
        if set(target) != {"repository"} or not isinstance(repository, str) or not REPOSITORY.fullmatch(repository):
            raise _error("INVALID_TARGET", "Repository must be an exact owner/name")
        if set(params) - {"host", "workspace_id", "secret_ref", "remote_url", "allow_merge", "allow_pr_update", "allow_integrate"}:
            raise _error("INVALID_PARAMS", "Only reviewed repository binding and permission fields are accepted")
        host, workspace = _text(params.get("host"), "host"), _text(params.get("workspace_id"), "workspace ID")
        if host not in document.get("hosts", {}):
            raise _error("UNKNOWN_HOST", "Select a configured BAT host", 404)
        github = document.setdefault("github", {})
        if github.get("api_url", "https://api.github.com").rstrip("/") != "https://api.github.com":
            raise _error("GITHUB_ENDPOINT_UNSUPPORTED", "Guided setup uses github.com only; preserve the existing provider configuration", 409)
        if "secret_ref" in params:
            github["token_ref"] = _secret(daemon, params["secret_ref"], "github", expired=expired)
        if not github.get("token_ref"):
            raise _error("CREDENTIAL_REQUIRED", "Add a GitHub credential before binding")
        repos = github.setdefault("repos", [])
        repo = next((item for item in repos if item["repository"].lower() == repository.lower()), None)
        if repo is None:
            repo = {"repository": repository, "allow_merge": False, "allow_pr_update": False}
            repos.append(repo)
        remote = _text(params.get("remote_url"), "Git remote URL", 512)
        sync = repo.setdefault("sync", {"remote_url": remote, "bindings": []})
        if sync["remote_url"] != remote:
            raise _error("REPOSITORY_BINDING_CHANGED", "Existing repository remote differs from the reviewed selection", 409)
        binding = {"host": host, "workspace_id": workspace}
        if binding not in sync["bindings"]:
            sync["bindings"].append(binding)
        for key in ("allow_merge", "allow_pr_update", "allow_integrate"):
            if key in params and type(params[key]) is not bool:
                raise _error("INVALID_PARAMS", "Repository permission choices must be booleans")
        for key in ("allow_merge", "allow_pr_update"):
            if key in params:
                repo[key] = params[key]
        if params.get("allow_integrate"):
            integrate = repo.setdefault("integrate", {"hosts": [], "remote_url": remote, "workspace": workspace})
            if integrate["remote_url"] != remote or integrate.get("workspace") != workspace:
                raise _error("REPOSITORY_BINDING_CHANGED", "Integration remote or workspace differs from the selected binding", 409)
            if host not in integrate["hosts"]:
                integrate["hosts"].append(host)
        elif params.get("allow_integrate") is False and repo.get("integrate"):
            raise _error("EXISTING_INTEGRATION", "Guided binding cannot remove an existing integration policy", 409)
    elif action == "setup.verification":
        if target or set(params) != {"commands", "timeout_s"}:
            raise _error("INVALID_PARAMS", "Verification setup accepts reviewed commands and timeout_s")
        document.setdefault("managed_setup", {})["verification"] = copy.deepcopy(params)
        _verification(document)
    else:
        raise _error("INVALID_PARAMS", "Unsupported managed setup action")
    try:
        _aliases(document)
        _verification(document)
        config = parse_config(document, path)
        encoded = _encode(document)
        if len(encoded) > MAX_CONFIG or _encode(tomllib.loads(encoded.decode())) != encoded:
            raise ValueError
    except (ValueError, TypeError, KeyError, ConfigError) as error:
        raise _error("INVALID_CONFIGURATION", "The reviewed configuration is invalid: " + type(error).__name__) from None
    if action == "setup.host" and "source_digest" in params:
        validate_source(daemon, source, params)
    return config, document, encoded, revision, hashlib.sha256(encoded).hexdigest()


def _review_candidate(daemon, principal, action, target, params, pre, *, reviewed=False):
    """Read and validate one candidate without admitting work or probing a host."""
    _installation(daemon, principal)
    keys = {"config_revision"}
    if reviewed and action == "setup.host" and isinstance(pre, dict) and "review_digest" in pre:
        keys.add("review_digest")
    if (not isinstance(pre, dict) or set(pre) != keys
            or any(not isinstance(pre[key], str) or not re.fullmatch(r"[0-9a-f]{64}", pre[key]) for key in keys)):
        raise _error("PRECONDITION_REQUIRED", "Read setup and supply its config_revision and optional host review_digest")
    if not isinstance(target, dict) or not isinstance(params, dict):
        raise _error("INVALID_PARAMS", "Setup target and params must be objects")
    if _busy(daemon):
        raise _error("SETUP_BUSY", "Wait for Connector operations and tasks to settle before changing connections", 409)
    try:
        candidate = _candidate(daemon, action, target, params)
    except ConfigError:
        raise _error("INVALID_CONFIGURATION", "Check the selected host trust, roots and connection settings") from None
    if candidate[3] != pre["config_revision"]:
        raise _error("CONFIGURATION_CHANGED", "Configuration changed; review setup again", 409)
    return candidate


def _review_digest(daemon, principal, action, target, params, before, after):
    value = {"contract_version": 1, **dashboard_sync.identity(daemon.journal, principal),
             "actor": principal.actor, "action": action, "target": target, "params": params,
             "config_revision": before, "after_revision": after}
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()


def preview(daemon, principal, body):
    """Return only the semantic host projection, never resolved credential paths."""
    _installation(daemon, principal)
    if (not isinstance(body, dict) or set(body) != {"action", "target", "params", "preconditions"}
            or body["action"] != "setup.host"):
        raise _error("INVALID_PARAMS", "Preview accepts only a typed setup.host request")
    action, target, params = body["action"], body["target"], body["params"]
    config, document, _, before, after = _review_candidate(daemon, principal, action, target, params, body["preconditions"])
    _, _, original, revision = _configuration(daemon)
    if revision != before:
        raise _error("CONFIGURATION_CHANGED", "Configuration changed; review setup again", 409)
    name = target["host"]
    fields = ("url", "fingerprint", "profile_id", "git", "writes", "orchestrate", "shared_clone_worktrees", "managed_roots", "ssh_alias")
    def projection(doc):
        host = doc.get("hosts", {}).get(name)
        if host is None:
            return dict.fromkeys(fields)
        return {**{key: host.get(key, {"git": "ssh", "profile_id": "default", "managed_roots": []}.get(key, False))
                   for key in fields if key != "ssh_alias"},
                "ssh_alias": doc.get("managed_setup", {}).get("ssh_hosts", {}).get(name)}
    old, new = projection(original), projection(document)
    if params.get("import_profile_id"):
        source = {"kind": "bat_profile", "reference": params["import_profile_id"], "available": True}
    elif params.get("secret_ref"):
        source = {"kind": "staged_secret", "reference": params["secret_ref"], "available": True}
    else:
        source = {"kind": "existing_connection", "reference": name,
                  "available": config.host(name).token_available()}
    return {"preview": {"contract_version": 1, "action": action, "target": target,
        "config_revision": before, "after_revision": after,
        "review_digest": _review_digest(daemon, principal, action, target, params, before, after),
        "changes": [{"field": key, "before": old[key], "after": new[key]} for key in fields if old[key] != new[key]],
        "source": source, "warnings": ["READ_ONLY_VERIFICATION_ON_APPLY"],
        "effects": {"configuration_written": False, "operation_created": False, "probe_started": False,
                    "credential_staged": source["kind"] == "staged_secret",
                    "on_apply": ["read_only_verification", "save_configuration"]}}}


def _admit(action):
    def admit(ops, principal, target, params, pre):
        daemon = ops.context["daemon"]
        config, document, _, before, after = _review_candidate(daemon, principal, action, target, params, pre, reviewed=True)
        binding = {**dashboard_sync.identity(ops.journal, principal), "before_revision": before, "after_revision": after}
        if "review_digest" in pre:
            digest = _review_digest(daemon, principal, action, target, params, before, after)
            if not secrets.compare_digest(digest, pre["review_digest"]):
                raise _error("SETUP_REVIEW_CHANGED", "Settings changed; review again", 409)
            binding["review_digest"] = digest
        if action == "setup.host":
            runner = daemon._git_runner(config, {**daemon.adapter.verifier.settings.ssh_hosts, **_aliases(document)})
            binding["git_execution"] = {target["host"]: runner.execution_binding(target["host"])}
            binding["local_confirmation"] = params.get("local_confirmation") is True
        return binding
    return admit


def _authorize_existing(ops, principal, op, _verb):
    _installation(ops.context["daemon"], principal)
    binding = (op.get("external_refs") or {}).get("admission_binding", {})
    if binding.get("principal_id") != dashboard_sync.identity(ops.journal, principal)["principal_id"]:
        raise _error("FORBIDDEN", "Setup belongs to the original personal identity", 403)


async def _read_host(host, *, discovery=False, selected=None, deadline=None):
    """The only setup BAT reader, shared by explicit discovery and Apply."""
    client = BatClient(host, device_id="managed-setup-probe", client_label="Better Agent Dashboard setup",
                       allow_writes=False, allow_orchestrate=False)
    async def read_step(awaitable):
        if deadline is None:
            return await awaitable
        return await asyncio.wait_for(awaitable, max(0, deadline - asyncio.get_running_loop().time()))
    try:
        await read_step(client.ping(timeout=10))
        raw = await read_step(client.invoke("profile:list", {}))
        rows = raw.get("profiles", []) if isinstance(raw, dict) else []
        # Preserve Apply's refusals, including TypeError for a null collection
        # or an unhashable ID. Discovery must not silently sanitize these away.
        reported = {p.get("id") for p in rows if isinstance(p, dict)}
        profiles = []
        for item in rows if isinstance(rows, list) else []:
            if not isinstance(item, dict):
                continue
            try:
                ident = _text(item.get("id"), "profile ID")
                name = item.get("name")
                try:
                    name = _text(name, "profile name")
                except OperationError:
                    name = ident
            except OperationError:
                continue
            if not any(p["id"] == ident for p in profiles):
                profiles.append({"id": ident, "name": name})
            if len(profiles) == 200:
                break
        profile_id = (selected or (profiles[0]["id"] if len(profiles) == 1 else None)) if discovery else host.profile_id
        if discovery and profile_id is None:
            return {"profiles": profiles, "profile_id": None, "workspaces": []}
        if profile_id not in ({p["id"] for p in profiles} if discovery else reported):
            raise _error("PROFILE_NOT_FOUND", "The selected BAT server did not report that profile", 409)
        client.host = dataclasses.replace(host, profile_id=profile_id)
        document = await read_step(service._workspace(client))
        workspaces = [{"workspace_id": item["id"], "name": item.get("name", item["id"])}
                      for item in document.get("workspaces", []) if isinstance(item, dict) and isinstance(item.get("id"), str)]
        return {"profiles": profiles, "profile_id": profile_id, "workspaces": workspaces[:200]}
    finally:
        if deadline is None:
            await client.close()
        else:
            # Budget cleanup too. Cancellation aborts BatClient's transport.
            await asyncio.wait_for(client.close(), max(0.01, min(2, deadline - asyncio.get_running_loop().time())))


async def _probe_host(config, name):
    result = await _read_host(config.host(name))
    return {"host": name, "connected": True, "workspaces": result["workspaces"]}


async def _probe_ssh(alias):
    # Use the existing known_hosts decision; never accept a new host key or execute caller commands.
    process = await asyncio.create_subprocess_exec("ssh", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes",
        "-o", "ConnectTimeout=10", alias, "true", stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
    try:
        if await asyncio.wait_for(process.wait(), 15) != 0:
            raise _error("SSH_NOT_VERIFIED", "Authorize the selected SSH alias and host key before connecting", 409)
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()


async def _probe_repository(config, repository, host, workspace):
    evidence = await _probe_host(config, host)
    if workspace not in {item["workspace_id"] for item in evidence["workspaces"]}:
        raise _error("WORKSPACE_NOT_FOUND", "The selected BAT profile did not report that workspace", 409)
    client = GitHubClient(config.github)
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            return None
    client._opener = urllib.request.build_opener(NoRedirect())
    status, response = await client.repository(repository)
    if status != 200 or str(response.get("full_name", "")).lower() != repository.lower():
        raise _error("REPOSITORY_NOT_VERIFIED", "GitHub did not verify access to the selected repository", 409)
    return {"repository": repository, "host": host, "workspace_id": workspace, "provider_verified": True}


def _aliases(document):
    metadata = document.get("managed_setup", {})
    aliases = metadata.get("ssh_hosts", {}) if isinstance(metadata, dict) else None
    if (not isinstance(aliases, dict) or any(not isinstance(host, str) or not HOST.fullmatch(host)
            or not isinstance(alias, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", alias)
            for host, alias in aliases.items())):
        raise _error("INVALID_CONFIGURATION", "Managed SSH mappings require logical host names and trusted aliases")
    return aliases


def _verification(document):
    metadata = document.get("managed_setup", {})
    raw = metadata.get("verification", {}) if isinstance(metadata, dict) else None
    if not isinstance(raw, dict) or set(raw) - {"commands", "timeout_s"}:
        raise _error("INVALID_CONFIGURATION", "Invalid managed verification settings")
    commands, timeout = raw.get("commands", {}), raw.get("timeout_s", 600)
    if (not isinstance(commands, dict) or len(commands) > 200 or type(timeout) is not int or not 1 <= timeout <= 3600):
        raise _error("INVALID_PARAMS", "Verification requires at most 200 named commands and a 1–3600 second timeout")
    for project, argv in commands.items():
        _text(project, "task project", 256)
        if (not isinstance(argv, list) or not 1 <= len(argv) <= 64
                or any(not isinstance(arg, str) or not arg or len(arg) > 4096
                       or any(ord(char) < 32 for char in arg) for arg in argv)
                or sum(map(len, argv)) > 16000 or argv[0].startswith("-")
                or redact_secrets(redact(json.dumps(argv))) != json.dumps(argv)):
            raise _error("INVALID_PARAMS", "Commands must be bounded explicit argv arrays without credentials or control characters")
    return parse_settings({"verification": {"commands": commands, "timeout_s": timeout}})


def _activate(daemon, config, document, revision=None, *, runner=None):
    if revision and daemon.ops.context.get("managed_setup_active_revision") == revision:
        return []
    # Prepare all adapters before changing live references; installation publication
    # and activation have no intervening await and use this already validated state.
    settings = daemon.adapter.verifier.settings
    aliases = {**settings.ssh_hosts, **_aliases(document)}
    settings = dataclasses.replace(settings, ssh_hosts=aliases)
    if "verification" in document.get("managed_setup", {}):
        verification = _verification(document)
        settings = dataclasses.replace(settings, commands=verification.commands, timeout_s=verification.timeout_s)
    runner = runner or daemon._git_runner(config, aliases)
    artifact = ArtifactHost(aliases, daemon.ops.context["artifact_host"].timeout_s)
    try:
        github = GitHubClient(config.github) if config.github.token_ref else None
    except TokenUnavailable:
        github = None
    old = []
    for fleet in (daemon.fleet, daemon.inventory.fleet):
        old.extend(fleet._clients.values())
        fleet._clients = {}
        fleet.config = config
    daemon._config = config
    daemon.usage_collector.config = config.usage
    daemon.history_collector.config = config.session_history
    daemon.inventory.config = config
    daemon.inventory.observation.config = config
    daemon.ops.context["github_config"] = config.github
    daemon.ops.context["github"] = github
    daemon.adapter.verifier.settings = settings
    daemon.verification_timeout_default_s = max(1, settings.timeout_s)
    daemon.verification_timeout_s = daemon.verification_timeout_default_s
    daemon.ops.context["git_runner"] = runner
    daemon.ops.context["artifact_host"] = artifact
    for fleet in (daemon.fleet, daemon.inventory.fleet):
        fleet.confinement_runner = runner
    if revision:
        daemon.ops.context["managed_setup_active_revision"] = revision
    return old


def _require_setup_identity(daemon, binding):
    if (binding.get("principal_id") != daemon.managed_installation["principal_id"]
            or binding.get("server_id") != dashboard_sync._metadata(daemon.journal)["server_id"]):
        raise NeedsAttention("SETUP_IDENTITY_CHANGED", "Original setup identity is unavailable")
    locations = binding.get("git_execution") or {}
    if any(location["installation_id"] != daemon.managed_installation.get("installation_id")
           for location in locations.values()):
        raise NeedsAttention("SETUP_IDENTITY_CHANGED", "Original setup installation is unavailable")


async def _run(ctx, *, action=None, target=None, params=None, source_guard=None):
    action = action or ctx.op["action"]
    target = target if target is not None else ctx.target
    params = params if params is not None else ctx.params
    daemon = ctx.service.context["daemon"]
    root = _installation(daemon)
    binding = ctx.admission_binding or {}
    _require_setup_identity(daemon, binding)
    lock = ctx.service.context.setdefault("managed_setup_lock", asyncio.Lock())
    async with lock:
        path = root / "config" / "hosts.toml"
        async def reconcile(_request):
            _require_setup_identity(daemon, binding)
            _, _, document, revision = _configuration(daemon)
            if revision == binding["after_revision"]:
                old = _activate(daemon, parse_config(document, path), document, revision)
                await asyncio.gather(*(client.close() for client in old), return_exceptions=True)
                return {"configured": True, "revision": revision, **target}
            if revision == binding["before_revision"]:
                return RERUN
            return None
        async def apply():
            if _busy(daemon, ctx.operation_id):
                raise _error("SETUP_BUSY", "Central work began; review setup again after it settles", 409)
            if source_guard:
                source_guard()
            config, document, encoded, before, after = _candidate(daemon, action, target, params, expired=True)
            if before != binding["before_revision"] or after != binding["after_revision"]:
                raise _error("CONFIGURATION_CHANGED", "Setup inputs or configuration changed; review again", 409)
            runner = daemon._git_runner(config,
                {**daemon.adapter.verifier.settings.ssh_hosts, **_aliases(document)})
            if action == "setup.host" and binding.get("git_execution") is not None:
                if binding["git_execution"] != {target["host"]: runner.execution_binding(target["host"])}:
                    raise _error("GIT_EXECUTION_LOCATION_CHANGED", "Original setup execution location changed", 409)
            try:
                if action == "setup.host":
                    proof = await _probe_host(config, target["host"])
                    if config.host(target["host"]).git == "ssh" and params.get("ssh_alias"):
                        await _probe_ssh(params["ssh_alias"])
                elif action == "setup.repository":
                    proof = await _probe_repository(config, target["repository"], params["host"], params["workspace_id"])
                else:
                    # Saving a command never executes it; task ownership and the
                    # existing observed-verification gates govern its later run.
                    proof = {"verification_configured": True}
            except OperationError:
                raise
            except Exception:
                raise _error("SETUP_PROBE_FAILED", "Connection or account verification failed; check trust, sign-in and reachability", 409) from None
            ctx.check_cancel()
            # No await between the final guards, publication and activating the parsed config.
            _require_setup_identity(daemon, binding)
            if source_guard:
                source_guard()
            if _busy(daemon, ctx.operation_id) or _configuration(daemon)[3] != before:
                raise _error("CONFIGURATION_CHANGED", "Work or configuration changed during verification; review again", 409)
            if "source_digest" in ctx.params:
                from .setup_source_discovery import validate_source
                validate_source(daemon, document, ctx.params)
            # Equal revisions mean these exact bytes are already on disk.
            if before != after:
                platform_files.atomic_write(path, encoded)
            old = _activate(daemon, config, document, after, runner=runner)
            await asyncio.gather(*(client.close() for client in old), return_exceptions=True)
            return {"configured": True, "revision": after, **proof}
        try:
            return await ctx.step("managed_configuration", apply,
                request={"before_revision": binding["before_revision"], "after_revision": binding["after_revision"],
                         **({"git_execution": binding["git_execution"], "local_confirmation": binding["local_confirmation"]}
                            if "git_execution" in binding else {})},
                reconcile=reconcile)
        except OSError:
            # Even a failure writing the final journal receipt must retain the original
            # operation for digest reconciliation, never turn the published config into
            # a misleading definitive failure/new intent.
            raise Uncertain("managed_configuration", "Configuration receipt needs digest reconciliation") from None


ACTIONS = [ActionDef("setup.host", "manage", "Configure a verified BAT connection for this installation", _run,
                    _admit("setup.host"), target_keys=("host",), authorize_existing=_authorize_existing),
           ActionDef("setup.repository", "manage", "Bind a verified GitHub repository and BAT workspace", _run,
                    _admit("setup.repository"), target_keys=("repository",), authorize_existing=_authorize_existing),
           ActionDef("setup.verification", "manage", "Configure explicit verification commands for task projects", _run,
                    _admit("setup.verification"), target_keys=(), authorize_existing=_authorize_existing)]


def install(daemon):
    """Call after managed_installation is set, before the service starts accepting requests."""
    for action in ACTIONS:
        if action.name not in daemon.ops.actions:
            daemon.ops.register(action)
    path, _, document, revision = _configuration(daemon)
    _activate(daemon, parse_config(document, path), document, revision)
