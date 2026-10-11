"""Authenticated controller context shared by operation, task and adapter paths.

Only server admission constructs bindings. They contain no bearer/hash and are not
accepted from public request fields. Legacy missing evidence grants no mutation;
read-back may run without authority, while every new effect calls require helpers.
"""
from __future__ import annotations

import copy
import re
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field

from .api_auth import Principal
from .errors import ResourceReadOnly

_ID = re.compile(r"ctr_[0-9a-f]{32}")
_INCARNATION = re.compile(r"[0-9a-f]{32}")
_WORKSPACE = re.compile(r"ws_[0-9a-f]{32}")


@dataclass
class _State:
    authority: dict | None
    journal: object | None = None
    reservations: dict = field(default_factory=dict)


_active: ContextVar[_State | None] = ContextVar("controller_authority", default=None)


def _validated(value) -> dict:
    if not isinstance(value, dict):
        raise ResourceReadOnly("CONTROLLER_AUTHORITY_REQUIRED", "Authenticated controller authority is required")
    try:
        valid = (str(uuid.UUID(value["server_id"])) == value["server_id"]
                 and isinstance(value["controller_id"], str) and _ID.fullmatch(value["controller_id"])
                 and isinstance(value["actor"], str) and bool(value["actor"])
                 and value["controller_id"] == "ctr_" + uuid.uuid5(uuid.UUID(value["server_id"]), value["actor"]).hex
                 and type(value["admin"]) is bool and isinstance(value["scopes"], list)
                 and all(isinstance(scope, str) for scope in value["scopes"])
                 and (value.get("pm_workspace_id") is None or isinstance(value["pm_workspace_id"], str)
                      and _WORKSPACE.fullmatch(value["pm_workspace_id"])))
        if "pm_operation_id" in value or "pm_requester" in value:
            requester = value.get("pm_requester")
            valid = (valid and value.get("actor") == "pm:" + str(value.get("pm_workspace_id"))
                     and isinstance(value.get("pm_operation_id"), str)
                     and re.fullmatch(r"op_[0-9a-f]{32}", value["pm_operation_id"])
                     and isinstance(requester, dict) and isinstance(requester.get("actor"), str)
                     and bool(requester["actor"]) and type(requester.get("admin")) is bool
                     and isinstance(requester.get("scopes"), list)
                     and all(isinstance(scope, str) for scope in requester["scopes"]))
    except (KeyError, ValueError, TypeError, AttributeError):
        valid = False
    if not valid:
        raise ResourceReadOnly("CONTROLLER_AUTHORITY_INVALID", "Controller authority is incomplete or invalid")
    return {key: copy.deepcopy(value[key]) for key in
            ("server_id", "controller_id", "actor", "scopes", "admin", "pm_workspace_id",
             "pm_operation_id", "pm_requester") if key in value}


def binding(db, principal: Principal, *, pm_workspace_id=None) -> dict:
    from . import pm_workspace
    if pm_workspace_id is not None:
        pm_workspace.require_member(db, principal, pm_workspace_id, write=True)
    value = {"server_id": pm_workspace.server_id(db), "controller_id": pm_workspace.controller_for(db, principal),
             "actor": principal.actor, "scopes": sorted(principal.scopes), "admin": principal.admin}
    if pm_workspace_id is not None:
        value["pm_workspace_id"] = pm_workspace_id
    return _validated(value)


@contextmanager
def use(authority, journal=None):
    """None deliberately permits only read-back; it never inherits a parent's authority."""
    state = _State(_validated(authority) if authority is not None else None, journal)
    if state.authority is not None and journal is not None:
        from .pm_workspace import server_id
        if state.authority["server_id"] != server_id(journal.db):
            raise ResourceReadOnly("CONTROLLER_SERVER_CHANGED", "Authority belongs to another central server")
    token = _active.set(state)
    try:
        yield
    finally:
        _active.reset(token)


def current() -> dict | None:
    state = _active.get()
    return copy.deepcopy(state.authority) if state else None


def require() -> dict:
    return _validated(current())


def operation_binding(op) -> dict | None:
    value = ((op.get("external_refs") or {}).get("admission_binding") or {}).get("controller_authority")
    return _validated(value) if value is not None else None


def resource_binding() -> dict:
    return require()


def same_controller(left, right) -> bool:
    try:
        left, right = _validated(left), _validated(right)
    except ResourceReadOnly:
        return False
    return (left["server_id"], left["controller_id"]) == (right["server_id"], right["controller_id"])


def require_binding(expected) -> dict:
    actual, expected = require(), _validated(expected)
    if not same_controller(actual, expected):
        raise ResourceReadOnly("FOREIGN_CONTROLLER", "This resource belongs to another controller")
    workspace = expected.get("pm_workspace_id")
    if actual.get("pm_workspace_id") is not None and actual["pm_workspace_id"] != workspace:
        raise ResourceReadOnly("CONTROLLER_WORKSPACE_CHANGED", "Controller authority names another PM workspace")
    if workspace:
        from . import pm_workspace
        from .operations import OperationError
        state = _active.get()
        if state.journal is None:
            raise ResourceReadOnly("CONTROLLER_WORKSPACE_UNAVAILABLE", "Current PM membership cannot be verified")
        try:
            if expected["actor"] == "pm:" + workspace:
                from .pm_run_operations import require_execution
                require_execution(state.journal, actual, workspace)
                return expected
            pm_workspace.require_member(state.journal.db,
                Principal(actual["actor"], frozenset(actual["scopes"]), admin=actual["admin"]), workspace, write=True)
        except OperationError:
            raise ResourceReadOnly("CONTROLLER_MEMBERSHIP_CHANGED", "Current membership does not permit this resource mutation") from None
    return expected


def require_entry(row, *, expected_incarnation=None) -> dict:
    if not isinstance(row, dict):
        raise ResourceReadOnly("CONTROLLER_OWNER_UNPROVEN", "The resource has no controller creation evidence")
    require_binding(row.get("controller_authority"))
    incarnation = row.get("controller_incarnation")
    if not isinstance(incarnation, str) or not _INCARNATION.fullmatch(incarnation):
        raise ResourceReadOnly("CONTROLLER_OWNER_UNPROVEN", "The resource has no controller incarnation evidence")
    if expected_incarnation is not None and incarnation != expected_incarnation:
        raise ResourceReadOnly("CONTROLLER_INCARNATION_CHANGED", "The resource incarnation changed before mutation")
    return row


def reservation_identity(host: str, session_id: str) -> str:
    """Bind a pre-reservation creation grant to the row minted in this scope."""
    from . import registry
    require()
    state = _active.get()
    key = (host, session_id)
    row = registry.get(host, session_id)
    if row is not None:
        require_entry(row)
        nonce = row["controller_incarnation"]
        if key in state.reservations and state.reservations[key] != nonce:
            raise ResourceReadOnly("CONTROLLER_INCARNATION_CHANGED", "Reservation changed before mutation")
        state.reservations[key] = nonce
    return state.reservations.setdefault(key, uuid.uuid4().hex)


def stamp(entry: dict) -> dict:
    """Stamp a new reservation, never upgrade an existing unowned row."""
    from . import registry
    authority = require()
    original = registry.get(entry["host"], entry["session_id"])
    if original is not None:
        require_entry(original)
        authority = copy.deepcopy(original["controller_authority"])
    incarnation = reservation_identity(entry["host"], entry["session_id"])
    if "controller_authority" in entry or "controller_incarnation" in entry:
        require_entry(entry, expected_incarnation=incarnation)
        if entry["controller_authority"] != authority:
            raise ResourceReadOnly("CONTROLLER_IMMUTABLE", "Reservation cannot replace original controller authority")
        return copy.deepcopy(entry)
    return {**entry, "controller_authority": authority, "controller_incarnation": incarnation}
