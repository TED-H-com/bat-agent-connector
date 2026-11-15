"""Pure bac/1 handshake and feature-channel grammar and reply validation; no I/O or policy grants.

The host contract is documented in docs/design/host-contracts.md. Unknown
response members are ignored; unknown request members are never generated.
"""
from __future__ import annotations

import copy
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Literal

from .errors import BacReplyError

PROTOCOL = "bac/1"
MINOR = 0
HELLO_CHANNEL = "bac:hello"
HELLO_ID = "bac-hello-1"
FEATURES = ("workspaceRead", "workspaceAddTerminal", "sessionLocate", "gitObserve")
WORKSPACE_READ_CHANNEL = "bac:workspace-read"
SESSION_LOCATE_CHANNEL = "bac:session-locate"
WORKSPACE_ADD_TERMINAL_CHANNEL = "bac:workspace-add-terminal"
CHANNEL_FEATURES = MappingProxyType({
    WORKSPACE_READ_CHANNEL: "workspaceRead",
    SESSION_LOCATE_CHANNEL: "sessionLocate",
    WORKSPACE_ADD_TERMINAL_CHANNEL: "workspaceAddTerminal",
})
REGISTRATION_KEYS = ("id", "workspaceId", "title", "type", "cwd", "agentPreset", "model",
                     "permissionMode", "agentParams", "worktreePath", "worktreeBranch")
AGENT_PRESETS = ("claude-code", "claude-code-worktree", "codex-agent", "codex-agent-worktree")
REGISTRATION_BYTES = 16384
AGENT_PARAMS_LIMIT = 32
LOCATION_MATCHES = 100
RUNTIME_AGENT_PARAMS = frozenset({"sandboxMode", "approvalPolicy", "effortLevel"})
_HOST_ID = re.compile(r"h_[0-9a-f]{32}")
_BUILD_REVISION = re.compile(r"(?:[0-9a-f]{40}|unknown)")
_BUILD_RELEASE = re.compile(r"[ -~]{1,64}")
_ERROR_CODE = re.compile(r"[A-Z][A-Z_]{0,63}")
_STORAGE_REASON = re.compile(r"[a-z][a-z0-9_]{0,63}")
_PROFILE_ID = re.compile(r"[A-Za-z0-9_.\-\u4e00-\u9fff]+")
_SESSION_ID = re.compile(r"[A-Za-z0-9_.:\-]{1,128}")
_REVISION = re.compile(r"sha256:[0-9a-f]{64}")
_REGISTRATION_KEY_SET = frozenset(REGISTRATION_KEYS)
_WORKSPACE_READ_KEYS = (
    "hostId",
    "profileId",
    "source",
    "windowId",
    "revision",
    "document",
    "appendTargetEligible",
)
_SESSION_LOCATE_KEYS = (
    "hostId",
    "locationRevision",
    "disposition",
    "consistency",
    "atomic",
    "locations",
)
_LOCATION_ROW_KEYS = (
    "profileId",
    "workspaceId",
    "terminalId",
    "windowId",
    "source",
    "profileRevision",
)
_APPEND_RESULT_KEYS = (
    "hostId",
    "profileId",
    "source",
    "windowId",
    "terminalId",
    "appended",
    "revision",
)
_ABSENT = object()


@dataclass(frozen=True)
class BacBuild:
    product: str
    revision: str
    release: str
    dirty: bool | None


@dataclass(frozen=True)
class BacState:
    """Immutable snapshot. Only negotiated has identity, build, features or limits."""

    status: Literal["absent", "unsupported", "negotiated", "unavailable"]
    reason: str | None = None
    storage_reason: str | None = None  # Diagnostic only; never selects behavior.
    host_id: str | None = None
    build: BacBuild | None = None
    features: Mapping[str, bool] = field(default_factory=lambda: MappingProxyType({}))
    limits: Mapping[str, int] = field(default_factory=lambda: MappingProxyType({}))


def advertisement_status(capabilities: Any) -> Literal["absent", "unsupported", "supported"]:
    if not isinstance(capabilities, dict) or "bac" not in capabilities:
        return "absent"
    value = capabilities["bac"]
    if (isinstance(value, dict) and value.get("protocol") == PROTOCOL
            and type(value.get("minor")) is int and value["minor"] == MINOR):
        return "supported"
    return "unsupported"


def hello_request(expected_host_id: str | None = None) -> dict:
    params: dict[str, Any] = {"protocol": PROTOCOL, "minor": MINOR}
    if expected_host_id is not None:
        params["expectedHostId"] = expected_host_id
    return {"type": "invoke", "id": HELLO_ID, "channel": HELLO_CHANNEL, "params": params}


def parse_envelope(result: Any) -> Any:
    """Return a success value, raise BacReplyError, or ValueError for invalid wire."""
    if not isinstance(result, dict):
        raise ValueError("invalid_reply")
    if result.get("ok") is True and "value" in result:
        return result["value"]
    if result.get("ok") is False:
        error = result.get("error")
        if (isinstance(error, dict) and isinstance(error.get("code"), str)
                and _ERROR_CODE.fullmatch(error["code"])
                and isinstance(error.get("message"), str)
                and type(error.get("retryable")) is bool
                and isinstance(error.get("details"), dict)):
            raise BacReplyError(error["code"], error["message"], error["retryable"], error["details"])
    raise ValueError("invalid_reply")


def validate_hello(value: Any) -> BacState:
    """Validate and copy known members so later input mutations cannot change state."""
    if not isinstance(value, dict):
        raise ValueError("invalid_reply")
    host_id = value.get("hostId")
    build, features, limits = (value.get(key) for key in ("build", "features", "limits"))
    if (value.get("protocol") != PROTOCOL or type(value.get("minor")) is not int
            or value["minor"] != MINOR or not isinstance(host_id, str)
            or not _HOST_ID.fullmatch(host_id) or not isinstance(build, dict)
            or build.get("product") != "bac" or not isinstance(build.get("revision"), str)
            or not _BUILD_REVISION.fullmatch(build["revision"])
            or not isinstance(build.get("release"), str) or not _BUILD_RELEASE.fullmatch(build["release"])
            or "dirty" not in build
            or (build["dirty"] is not None and type(build["dirty"]) is not bool)
            or not isinstance(features, dict) or not isinstance(limits, dict)):
        raise ValueError("invalid_reply")
    if any(type(features[key]) is not bool for key in FEATURES if key in features):
        raise ValueError("invalid_reply")
    if any(not isinstance(key, str) or type(limit) is not int or limit < 0
           for key, limit in limits.items()):
        raise ValueError("invalid_reply")
    return BacState(
        "negotiated", host_id=host_id,
        build=BacBuild(*(build[key] for key in ("product", "revision", "release", "dirty"))),
        features=MappingProxyType({key: features.get(key, False) for key in FEATURES}),
        limits=MappingProxyType(dict(limits)),
    )


def hello_state(reply: dict, expected_host_id: str | None = None) -> BacState:
    if reply.get("type") == "invoke-error":
        return BacState("unavailable", reason="invoke_error")
    if reply.get("type") != "invoke-result":
        return BacState("unavailable", reason="invalid_reply")
    try:
        state = validate_hello(parse_envelope(reply.get("result")))
        if expected_host_id is not None and state.host_id != expected_host_id:
            return BacState("unavailable", reason="HOST_MISMATCH")
        return state
    except BacReplyError as error:
        diagnostic = error.details.get("reason")
        storage_reason = None
        if (error.code in {"STORAGE_UNAVAILABLE", "STORAGE_CORRUPT"}
                and isinstance(diagnostic, str) and _STORAGE_REASON.fullmatch(diagnostic)
                and not _HOST_ID.fullmatch(diagnostic)):
            storage_reason = diagnostic
        return BacState("unavailable", reason=error.code, storage_reason=storage_reason)
    except ValueError:
        return BacState("unavailable", reason="invalid_reply")


def valid_profile_id(value: Any) -> bool:
    return (
        isinstance(value, str)
        and _PROFILE_ID.fullmatch(value) is not None
        and 1 <= len(value.encode("utf-8")) <= 128
    )


def valid_session_id(value: Any) -> bool:
    return isinstance(value, str) and _SESSION_ID.fullmatch(value) is not None


def valid_revision(value: Any) -> bool:
    return isinstance(value, str) and _REVISION.fullmatch(value) is not None


def parse_workspace_read(value: Any, *, host_id: str, profile_id: str) -> dict[str, Any]:
    if not isinstance(value, dict) or any(key not in value for key in _WORKSPACE_READ_KEYS):
        raise ValueError("invalid_reply")
    host = value["hostId"]
    profile = value["profileId"]
    source = value["source"]
    window_id = value["windowId"]
    revision = value["revision"]
    document = value["document"]
    eligible = value["appendTargetEligible"]
    if (
        not isinstance(host_id, str)
        or not isinstance(host, str)
        or host != host_id
        or not isinstance(profile_id, str)
        or not isinstance(profile, str)
        or profile != profile_id
        or not isinstance(source, str)
        or source not in ("live", "persisted")
        or (window_id is not None and not isinstance(window_id, str))
        or not valid_revision(revision)
        or not isinstance(document, dict)
        or type(eligible) is not bool
    ):
        raise ValueError("invalid_reply")
    return {
        "hostId": host,
        "profileId": profile,
        "source": source,
        "windowId": window_id,
        "revision": revision,
        "document": copy.deepcopy(document),
        "appendTargetEligible": eligible,
    }


def parse_session_locate(
    value: Any, *, host_id: str, session_id: str, max_rows: int = LOCATION_MATCHES
) -> dict[str, Any]:
    if not isinstance(value, dict) or any(key not in value for key in _SESSION_LOCATE_KEYS):
        raise ValueError("invalid_reply")
    host = value["hostId"]
    location_revision = value["locationRevision"]
    disposition = value["disposition"]
    consistency = value["consistency"]
    atomic = value["atomic"]
    locations = value["locations"]
    if (
        not isinstance(host_id, str)
        or not isinstance(host, str)
        or host != host_id
        or not isinstance(session_id, str)
        or not valid_revision(location_revision)
        or not isinstance(disposition, str)
        or disposition not in ("unregistered", "located", "ambiguous")
        or not isinstance(consistency, str)
        or consistency != "sequential"
        or atomic is not False
        or not isinstance(locations, list)
        or type(max_rows) is not int
        or max_rows < 0
        or len(locations) > max_rows
    ):
        raise ValueError("invalid_reply")
    expected_disposition = (
        "unregistered" if not locations else ("located" if len(locations) == 1 else "ambiguous")
    )
    if disposition != expected_disposition:
        raise ValueError("invalid_reply")
    copied_rows: list[dict[str, Any]] = []
    for row in locations:
        if not isinstance(row, dict) or any(key not in row for key in _LOCATION_ROW_KEYS):
            raise ValueError("invalid_reply")
        row_profile = row["profileId"]
        row_workspace = row["workspaceId"]
        row_terminal = row["terminalId"]
        row_window = row["windowId"]
        row_source = row["source"]
        row_revision = row["profileRevision"]
        if (
            not isinstance(row_profile, str)
            or not isinstance(row_workspace, str)
            or not isinstance(row_terminal, str)
            or row_terminal != session_id
            or (row_window is not None and not isinstance(row_window, str))
            or not isinstance(row_source, str)
            or row_source not in ("live", "persisted")
            or not valid_revision(row_revision)
        ):
            raise ValueError("invalid_reply")
        copied_rows.append({
            "profileId": row_profile,
            "workspaceId": row_workspace,
            "terminalId": row_terminal,
            "windowId": row_window,
            "source": row_source,
            "profileRevision": row_revision,
        })
    return {
        "hostId": host,
        "locationRevision": location_revision,
        "disposition": disposition,
        "consistency": consistency,
        "atomic": False,
        "locations": copied_rows,
    }


def _non_empty_no_nul(value: Any) -> bool:
    return isinstance(value, str) and bool(value) and "\0" not in value


def _registration_shape_error(terminal: Any) -> str | None:
    if not isinstance(terminal, dict) or any(
        not isinstance(key, str) or key not in _REGISTRATION_KEY_SET for key in terminal
    ):
        return "params.terminal"
    if not valid_session_id(terminal.get("id")):
        return "params.terminal.id"
    if not valid_session_id(terminal.get("workspaceId")):
        return "params.terminal.workspaceId"
    if not _non_empty_no_nul(terminal.get("title")):
        return "params.terminal.title"
    if not isinstance(terminal.get("type"), str) or terminal.get("type") != "terminal":
        return "params.terminal.type"
    if not _non_empty_no_nul(terminal.get("cwd")):
        return "params.terminal.cwd"
    if not isinstance(terminal.get("agentPreset"), str) or terminal.get("agentPreset") not in AGENT_PRESETS:
        return "params.terminal.agentPreset"
    for key in ("model", "permissionMode"):
        val = terminal.get(key)
        if val is not None and not _non_empty_no_nul(val):
            return f"params.terminal.{key}"
    agent_params = terminal.get("agentParams")
    if agent_params is not None:
        if (
            not isinstance(agent_params, dict)
            or len(agent_params) > AGENT_PARAMS_LIMIT
            or any(
                not _non_empty_no_nul(k) or not (isinstance(v, str) and "\0" not in v)
                for k, v in agent_params.items()
            )
        ):
            return "params.terminal.agentParams"
    worktree_path = terminal.get("worktreePath")
    if worktree_path is not None and not _non_empty_no_nul(worktree_path):
        return "params.terminal.worktreePath"
    worktree_branch = terminal.get("worktreeBranch")
    if worktree_branch is not None and not _non_empty_no_nul(worktree_branch):
        return "params.terminal.worktreeBranch"
    return None


def registration_error(terminal: Any, *, max_bytes: int = REGISTRATION_BYTES) -> str | None:
    error = _registration_shape_error(terminal)
    if error is not None:
        return error
    try:
        encoded = json.dumps(terminal, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    except UnicodeEncodeError:
        return "params.terminal"
    if len(encoded) > max_bytes:
        return "params.terminal"
    return None


def _typed_equal(left: Any, right: Any) -> bool:
    return type(left) is type(right) and left == right


def registration_effectively_equal(stored: Any, candidate: Any) -> bool:
    if not isinstance(stored, dict) or _registration_shape_error(candidate) is not None:
        return False
    if not _typed_equal(stored.get("type", "terminal"), "terminal"):
        return False
    if any(
        not _typed_equal(stored.get(key), candidate[key])
        for key in ("id", "workspaceId", "title", "cwd", "agentPreset")
    ):
        return False
    if any(
        not _typed_equal(stored.get(key), candidate.get(key))
        for key in ("model", "permissionMode", "worktreePath")
    ):
        return False
    if not _typed_equal(
        stored.get("worktreeBranch", _ABSENT), candidate.get("worktreeBranch", _ABSENT)
    ):
        return False
    stored_params = stored.get("agentParams")
    if stored_params is not None and not isinstance(stored_params, dict):
        return False
    candidate_params = candidate.get("agentParams")
    stored_filtered = {
        k: v for k, v in (stored_params or {}).items() if k not in RUNTIME_AGENT_PARAMS
    }
    candidate_filtered = {
        k: v for k, v in (candidate_params or {}).items() if k not in RUNTIME_AGENT_PARAMS
    }
    if len(stored_filtered) != len(candidate_filtered) or any(
        type(k) is not str for k in stored_filtered
    ):
        return False
    return all(
        k in stored_filtered and _typed_equal(stored_filtered[k], v)
        for k, v in candidate_filtered.items()
    )


def parse_append_result(
    value: Any, *, host_id: str, profile_id: str, terminal_id: str
) -> dict[str, Any]:
    if not isinstance(value, dict) or any(key not in value for key in _APPEND_RESULT_KEYS):
        raise ValueError("invalid_reply")
    host = value["hostId"]
    profile = value["profileId"]
    source = value["source"]
    window_id = value["windowId"]
    terminal = value["terminalId"]
    appended = value["appended"]
    revision = value["revision"]
    if (
        not isinstance(host_id, str)
        or not isinstance(host, str)
        or host != host_id
        or not isinstance(profile_id, str)
        or not isinstance(profile, str)
        or profile != profile_id
        or not isinstance(source, str)
        or source not in ("live", "persisted")
        or (window_id is not None and not isinstance(window_id, str))
        or not isinstance(terminal_id, str)
        or not isinstance(terminal, str)
        or terminal != terminal_id
        or type(appended) is not bool
        or not valid_revision(revision)
    ):
        raise ValueError("invalid_reply")
    return {
        "hostId": host,
        "profileId": profile,
        "source": source,
        "windowId": window_id,
        "terminalId": terminal,
        "appended": appended,
        "revision": revision,
    }
