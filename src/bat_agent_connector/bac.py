"""Pure bac/1 handshake values and validation; no feature calls or policy grants.

The handshake contract is documented in docs/design/host-contracts.md. Unknown
response members are ignored; unknown request members are never generated.
"""
from __future__ import annotations

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
_HOST_ID = re.compile(r"h_[0-9a-f]{32}")
_ERROR_CODE = re.compile(r"[A-Z][A-Z_]{0,63}")
_STORAGE_REASON = re.compile(r"[a-z][a-z0-9_]{0,63}")


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
            or not isinstance(build.get("release"), str) or "dirty" not in build
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
