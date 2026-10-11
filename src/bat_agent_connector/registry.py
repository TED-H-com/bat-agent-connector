"""Local registry of sessions started by the orchestrate tier.

BAT only lists sessions that have a tab in the host's workspace document. Sessions
started headlessly (without tab registration) are tracked here so the connector
can still find, cap, review and clean them up.
"""

from __future__ import annotations

import asyncio
import contextlib
import contextvars
import functools
import hashlib
import json
import os
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from . import platform_files
from .config import state_dir

RETIRED = frozenset({"stopped", "absent_at_cleanup"})


def registry_path() -> Path:
    return state_dir() / "orchestrated.json"


def _caller():
    try:
        task = asyncio.current_task()
    except RuntimeError:
        task = None
    return task or threading.current_thread()


@dataclass
class _StartClaim:
    path: Path
    host: str
    session_id: str
    fd: int
    owner: object
    token: str = field(default_factory=lambda: str(uuid.uuid4()))
    reserved: bool = False


@dataclass
class _StartCall:
    owner: object
    claims: list[_StartClaim] = field(default_factory=list)


_start_call = contextvars.ContextVar("registry_start_call", default=None)
_start_claims: dict[tuple[Path, str, str], _StartClaim] = {}


def _call() -> _StartCall | None:
    call = _start_call.get()
    return call if call and call.owner == _caller() else None


def start_call(fn):
    """Keep reservation locks through the entire start's return or exception.

    A nested start may take its caller's preparation claim, never a reservation
    already in use. Child tasks cannot borrow inherited ContextVar claims.
    """
    @functools.wraps(fn)
    async def wrapped(*args, **kwargs):
        call = _StartCall(_caller())
        if parent := _call():
            call.claims = [claim for claim in parent.claims if not claim.reserved]
            parent.claims = [claim for claim in parent.claims if claim.reserved]
        context = _start_call.set(call)
        try:
            return await fn(*args, **kwargs)
        finally:
            try:
                for claim in call.claims:
                    try:
                        # The token is diagnostic. A cleanup write cannot undo
                        # an accepted start or replace the original cancellation.
                        with contextlib.suppress(OSError), _locked(claim.path):
                            items = _read(claim.path)
                            _clear_claim_token(claim, items)
                            _write(claim.path, items)
                    finally:
                        _close_claim(claim)
            finally:
                _start_call.reset(context)
    return wrapped


def _clear_claim_token(claim: _StartClaim, items: list[dict]) -> None:
    for entry in items:
        if (entry.get("host") == claim.host and entry.get("session_id") == claim.session_id
                and entry.get("start_claim_token") == claim.token):
            entry["start_claim_token"] = None


def _close_claim(claim: _StartClaim) -> None:
    _start_claims.pop((claim.path, claim.host, claim.session_id), None)
    os.close(claim.fd)  # close, not unlink: every contender must lock the same inode


def _own_claim(path: Path, host: str, session_id: str) -> _StartClaim | None:
    claim = _start_claims.get((path, host, session_id))
    call = _call()
    return claim if claim and claim.owner == _caller() and (
        claim in call.claims if call else True) else None


def _open_claim(path: Path, host: str, session_id: str) -> int:
    from .confinement import ConfinementRefused

    directory = path.parent / "start-claims"
    platform_files.ensure_private_directory(directory)
    digest = hashlib.sha256((host + "\0" + session_id).encode()).hexdigest()
    fd = platform_files.open_private_file(directory / (digest + ".lock"), os.O_RDWR | os.O_CREAT)
    try:
        platform_files.lock(fd, blocking=False)
    except BlockingIOError as exc:
        os.close(fd)
        raise ConfinementRefused("START_IN_PROGRESS", "another process or coroutine is starting this session; "
                                "read it back later, do not retry blindly", sent=False) from exc
    except BaseException:
        os.close(fd)
        raise
    return fd


def refuse_start_claim(path: Path, host: str, session_id: str) -> None:
    """Check a live start while holding the registry flock, before cleanup reserves it.

    A new descriptor must contend even with this coroutine's existing claim.
    Closing it releases only this probe; never unlink the persistent lock inode.
    """
    os.close(_open_claim(path, host, session_id))


def _take_claim(path: Path, host: str, session_id: str) -> _StartClaim:
    fd = _open_claim(path, host, session_id)
    claim = _StartClaim(path, host, session_id, fd, _caller())
    _start_claims[(path, host, session_id)] = claim
    if call := _call():
        call.claims.append(claim)
    return claim


def _claim_unsent_locked(path: Path, items: list[dict], host: str, session_id: str) -> str:
    entry = next((e for e in items if e.get("host") == host and e.get("session_id") == session_id), None)
    if not entry or entry.get("status") not in {"failed", "starting"} or entry.get("start_sent") is not False:
        return "not_unsent"
    claim = _own_claim(path, host, session_id)
    if not claim or claim.reserved:
        _take_claim(path, host, session_id)
    return "claimed"


def claim_unsent(host: str, session_id: str) -> str:
    """Claim an abandoned unsent row, or refuse its live owner without changes.

    Call from a start_call before preparation. reserve consumes this call's
    preclaim under the registry flock; another coroutine always opens a new fd.
    """
    p = registry_path()
    with _locked(p):
        items = _read(p)
        from . import controller_authority
        entry = next((e for e in items if e.get("host") == host and e.get("session_id") == session_id), {})
        # A sent/settled record needs no start claim: permit read-back even when
        # legacy authority is absent. Only new or positively unsent effects need authority.
        if not entry or (entry.get("status") in {"failed", "starting"} and entry.get("start_sent") is False):
            controller_authority.require()
            if entry:
                controller_authority.require_entry(entry)
        _cleanup_guard(host, {**entry, "session_id": session_id})
        return _claim_unsent_locked(p, items, host, session_id)


def _cleanup_guard(host: str, entry: dict) -> None:
    from .cleanup import guard

    guard(host, session_id=entry.get("session_id"), path=entry.get("worktree_path") or entry.get("cwd") or entry.get("origin_cwd"),
          branch=entry.get("branch"))


def _release_implicit(claim: _StartClaim | None, items: list[dict]) -> None:
    if claim and not _call():
        _clear_claim_token(claim, items)
        _close_claim(claim)


@contextlib.contextmanager
def _discard_unused_claim(path: Path, host: str, session_id: str):
    try:
        yield
    finally:
        claim = _own_claim(path, host, session_id)
        if claim and not claim.reserved and not _call():
            _close_claim(claim)


def _after_fork() -> None:
    # A forked helper must not keep its parent's claims alive. Unlocking the
    # shared open-file description here would also unlock the parent's claim.
    for claim in _start_claims.values():
        os.close(claim.fd)
    _start_claims.clear()
    _start_call.set(None)


if hasattr(os, "register_at_fork"):
    os.register_at_fork(after_in_child=_after_fork)


@contextlib.contextmanager
def _locked(path: Path):
    # A CLI registry write may be the first creator of central state. Its
    # directory must satisfy the service lease contract on every platform.
    platform_files.ensure_private_directory(path.parent)
    lock = path.with_suffix(".lock")
    fd = platform_files.open_private_file(lock, os.O_RDWR | os.O_CREAT)
    try:
        platform_files.lock(fd)
        yield
    finally:
        platform_files.unlock(fd)
        os.close(fd)


def _read(path: Path) -> list[dict]:
    try:
        data = json.loads(path.read_text())
        items = [e for e in data.get("sessions", []) if isinstance(e, dict)]
    except (OSError, ValueError):
        return []
    _validate_unique(items)
    return items


class RegistryInvariantError(RuntimeError):
    code = "REGISTRY_DUPLICATE_SESSION"


def _validate_unique(items: list[dict]) -> None:
    seen = set()
    for entry in items:
        key = (entry.get("host"), entry.get("session_id"))
        if key in seen:
            raise RegistryInvariantError("[REGISTRY_DUPLICATE_SESSION] multiple rows for the same host/session ID; "
                                         "repair the registry before continuing")
        seen.add(key)


def _write_document(path: Path, data: dict) -> None:
    _validate_unique(data.get("sessions", []))
    if platform_files.WINDOWS:
        platform_files.atomic_write(path, json.dumps(data, indent=1).encode())
        return
    tmp = path.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        json.dump(data, fh, indent=1)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)
    platform_files.sync_directory(path.parent)


def _write(path: Path, items: list[dict]) -> None:
    # All callers hold _locked. Preserve cross-process cleanup reservations and tombstones.
    try:
        data = json.loads(path.read_text())
    except FileNotFoundError:
        data = {}
    data["sessions"] = items
    _write_document(path, data)


def list_entries(host: str | None = None, active_only: bool = False) -> list[dict]:
    items = _read(registry_path())
    if host:
        items = [e for e in items if e.get("host") == host]
    if active_only:
        items = [e for e in items if e.get("status") == "active"]
    return items


def get(host: str, session_id: str) -> dict | None:
    for e in _read(registry_path()):
        if e.get("host") == host and e.get("session_id") == session_id:
            return e
    return None


def _preserve_controller(row, fields):
    from .errors import ResourceReadOnly
    for key in ("controller_authority", "controller_incarnation"):
        if key in fields and fields[key] != row.get(key):
            raise ResourceReadOnly("CONTROLLER_IMMUTABLE", "Registry projection cannot replace controller creation evidence")


def is_pending_worktree_source(entry):
    """A durable fresh-worktree start reads this cwd; it has no carrier there yet."""
    return (isinstance(entry.get("start_operation_id"), str) and bool(entry["start_operation_id"])
            and entry.get("creates_worktree") is True and entry.get("start_sent") is False
            and not entry.get("worktree_path")
            and not any(entry.get(key) for key in ("shares_worktree_with", "lead_session_id", "failover_of")))


def _require_owned_carriers(host, entry, items, *, replaces=None):
    from . import controller_authority
    related = {value for value in (replaces, entry.get("failover_of"), entry.get("shares_worktree_with"),
                                  entry.get("lead_session_id")) if value}
    for sid in related:
        row = next((e for e in items if e.get("host") == host and e.get("session_id") == sid), None)
        controller_authority.require_entry(row)
    paths = {entry.get("worktree_path"), None if is_pending_worktree_source(entry) else entry.get("cwd")} - {None, ""}
    for row in items:
        if row.get("host") == host and row.get("session_id") != entry.get("session_id") and paths & {
                None if is_pending_worktree_source(row) else row.get("cwd"), row.get("worktree_path")}:
            controller_authority.require_entry(row)


def ensure_existing(host: str, entry: dict) -> None:
    """Restore local lookup for a BAT session verified by task journal and host meta."""
    from . import controller_authority
    p = registry_path()
    with _locked(p), _discard_unused_claim(p, host, entry["session_id"]):
        items = _read(p)
        # Recovery must carry original durable creation evidence. Never infer a
        # controller from the current caller while adopting an old BAT session.
        controller_authority.require_entry(entry)
        _require_owned_carriers(host, entry, items)
        _cleanup_guard(host, entry)
        if any(e.get("host") == host and e.get("session_id") == entry.get("session_id") and
               e.get("status") in RETIRED for e in items):
            from .errors import ResourceReadOnly
            raise ResourceReadOnly("SESSION_RETIRED", "this session ID left the host cap; start a new session ID")
        claim = _own_claim(p, host, entry["session_id"])
        if not claim:
            _claim_unsent_locked(p, items, host, entry["session_id"])
            claim = _own_claim(p, host, entry["session_id"])
        for old in items:
            if old.get("host") == host and old.get("session_id") == entry.get("session_id"):
                controller_authority.require_entry(old, expected_incarnation=entry["controller_incarnation"])
                if (old.get("task_id") not in {None, entry.get("task_id")}
                        or old.get("role") not in {None, entry.get("role")}
                        or any(old.get(key) not in {None, entry.get(key)} for key in (
                            "workspace_id", "origin_cwd", "cwd", "worktree_path", "branch",
                            "agent_preset"))):
                    raise ValueError("local session entry changed during BAT identity verification")
                _preserve_controller(old, entry)
                old.update(entry)
                old["status"] = "active"
                old["updated_at"] = time.time()
                _release_implicit(claim, items)
                _write(p, items)
                return
        items.append({**entry, "host": host, "status": "active", "created_at": time.time(),
                      "recovered_from": "task_journal"})
        _write(p, items)


def find_prefix(host: str, prefix: str) -> list[dict]:
    return [
        e
        for e in _read(registry_path())
        if e.get("host") == host and str(e.get("session_id", "")).startswith(prefix)
    ]


def reserve(host: str, entry: dict, max_active: int, replaces: str | None = None, *,
            predecessor_expected: dict | None = None, failover_fence: dict | None = None) -> dict | None:
    """Atomically check the per-host cap and add an entry (status=starting).

    ``replaces``: session id of an active entry this one takes over (failover in the same
    worktree). It is marked ``superseded`` in the same transaction, so the pair counts once.
    """
    from . import controller_authority
    from .errors import WriteRefused

    p = registry_path()
    with _locked(p), _discard_unused_claim(p, host, entry["session_id"]):
        items = _read(p)
        controller_authority.require()
        _require_owned_carriers(host, entry, items, replaces=replaces)
        previous = next((e for e in items if e.get("host") == host and e.get("session_id") == entry["session_id"]), None)
        if previous:
            controller_authority.require_entry(previous)
        entry = controller_authority.stamp({**entry, "host": host})
        _cleanup_guard(host, entry)
        predecessor = None
        if predecessor_expected is not None or failover_fence is not None:
            from .errors import ResourceReadOnly
            predecessor = next((e for e in items if e.get('host') == host and
                                e.get('session_id') == entry.get('failover_of')), None)
            if (not predecessor or not isinstance(failover_fence, dict) or not predecessor_expected
                    or predecessor.get('failover_fence') is not None
                    or any(predecessor.get(k) != v for k, v in predecessor_expected.items())
                    or failover_fence.get('source_session_id') != predecessor['session_id']
                    or failover_fence.get('source_created_at') != predecessor.get('created_at')
                    or failover_fence.get('successor_session_id') != entry['session_id']
                    or failover_fence.get('operation_id') != entry.get('start_operation_id')):
                raise ResourceReadOnly('FAILOVER_BINDING_CHANGED', 'original failover incarnation or fence changed')
            _cleanup_guard(host, predecessor)
            refuse_start_claim(p, host, predecessor['session_id'])
        if any(e.get("host") == host and e.get("session_id") == entry.get("session_id") and
               e.get("status") in RETIRED for e in items):
            from .errors import ResourceReadOnly
            raise ResourceReadOnly("SESSION_RETIRED", "this session ID left the host cap; start a new session ID")
        sid = entry["session_id"]
        previous = next((e for e in items if e.get("host") == host and e.get("session_id") == sid), None)
        # False proves no frame, not abandonment. The OS lock proves whether
        # another start call still owns that unsent reservation.
        reclaim = _claim_unsent_locked(p, items, host, sid) == "claimed"
        if previous:
            from .confinement import guard_new_start
            guard_new_start(previous)
        # The earlier caller-side lookup is only a hint. This check and the reservation
        # must share the flock, including when two independent MCP processes race.
        old = entry.get("failover_of")
        if old:
            existing = next((e for e in items if e.get("host") == host and
                             e.get("failover_of") == old and e.get("status") in ("starting", "active")), None)
            if existing and not (reclaim and existing.get("session_id") == sid):
                return existing
            worktree = entry.get("worktree_path")
            if worktree and any(e.get("host") == host and e.get("worktree_path") == worktree and
                                e.get("failover_of") and e.get("status") in ("starting", "active")
                                and not (reclaim and e.get("session_id") == sid)
                                for e in items):
                raise WriteRefused("another active failover successor already owns this worktree")
        if previous and not reclaim:
            from .confinement import ConfinementRefused
            raise ConfinementRefused("CONFINEMENT_START_UNSETTLED",
                                     "session ID is already reserved; use read-back recovery")
        if replaces:
            for e in items:
                if e.get("host") == host and e.get("session_id") == replaces and e.get("status") == "active":
                    e.update(status="superseded", superseded_by=entry.get("session_id"), updated_at=time.time())
        # The preclaim (or fresh claim below) and replacement share this flock.
        items = [e for e in items if not (e.get("host") == host and e.get("session_id") == entry["session_id"]
                                        and e.get("status") in {"failed", "starting"} and e.get("start_sent") is False)]
        active = [e for e in items if e.get("host") == host and e.get("status") in ("active", "starting")]
        if len(active) >= max_active:
            raise WriteRefused(
                f"orchestrate cap reached on {host}: {len(active)} active orchestrated sessions "
                f"(orchestrate_max_sessions={max_active}); remove finished worktrees first"
            )
        claim = _own_claim(p, host, sid)
        if not claim or claim.reserved:
            claim = _take_claim(p, host, sid)
        entry = {**entry, "host": host, "status": "starting", "created_at": time.time(),
                 "start_claim_token": claim.token}
        if predecessor is not None:
            fence = {**failover_fence, 'successor_created_at': entry['created_at']}
            entry['failover_fence'] = fence
            predecessor['failover_fence'] = fence
        items.append(entry)
        _write(p, items)
        claim.reserved = True
    return None


def rollback_failover(host, session_id, *, fence, predecessor_expected, successor_expected):
    """Release only this call's positively unsent successor and predecessor fence."""
    p = registry_path()
    with _locked(p):
        items = _read(p)
        row = next((e for e in items if e.get('host') == host and e.get('session_id') == session_id), None)
        old = next((e for e in items if e.get('host') == host and e.get('session_id') == fence['source_session_id']), None)
        claim = _own_claim(p, host, session_id)
        if (not claim or not row or not old or row.get('start_sent') is not False
                or row.get('failover_fence') != fence or old.get('failover_fence') != fence
                or row.get('start_operation_id') != fence['operation_id']
                or row.get('created_at') != fence['successor_created_at']
                or old.get('created_at') != fence['source_created_at']
                or any(row.get(k) != v for k, v in successor_expected.items())
                or any(old.get(k) != v for k, v in predecessor_expected.items())):
            return False
        row.update(status='failed', updated_at=time.time(), failover_fence=None)
        old['failover_fence'] = None
        if old.get('status') == 'superseded' and old.get('superseded_by') == session_id:
            old.update(status='active', superseded_by=None, updated_at=time.time())
        _write(p, items)
        return True


def fail_reservation(host: str, session_id: str, replaces: str | None = None) -> None:
    """Fail a successor and undo only the predecessor handoff owned by it."""
    p = registry_path()
    with _locked(p):
        items = _read(p)
        claim = _own_claim(p, host, session_id)
        if not claim:
            _claim_unsent_locked(p, items, host, session_id)
            claim = _own_claim(p, host, session_id)
        for e in items:
            if e.get("host") == host and e.get("session_id") == session_id:
                e.update(status="failed", updated_at=time.time())
            if (replaces and e.get("host") == host and e.get("session_id") == replaces
                    and e.get("superseded_by") == session_id):
                e.update(status="active", superseded_by=None, updated_at=time.time())
        _release_implicit(claim, items)
        _write(p, items)


def update(host: str, session_id: str, **fields) -> None:
    p = registry_path()
    with _locked(p):
        items = _read(p)
        claim = _own_claim(p, host, session_id)
        if fields.get("status") and fields["status"] != "starting" and not claim:
            _claim_unsent_locked(p, items, host, session_id)
            claim = _own_claim(p, host, session_id)
        for e in items:
            if e.get("host") == host and e.get("session_id") == session_id:
                _preserve_controller(e, fields)
                e.update(fields)
                e["updated_at"] = time.time()
        if fields.get("status") and fields["status"] != "starting":
            _release_implicit(claim, items)
        _write(p, items)


def project_start(host, session_id, *, operation_id, created_at, expected, fields):
    """Project one start receipt without replacing a later incarnation or owner.

    Repeating an already matching projection is read-only. Otherwise the entire
    expected standalone binding must still match under the registry flock.
    """
    p = registry_path()
    with _locked(p):
        items = _read(p)
        row = next((e for e in items if e.get("host") == host and e.get("session_id") == session_id), None)
        if not row or row.get("start_operation_id") != operation_id or row.get("created_at") != created_at:
            return False
        if all(row.get(key) == value for key, value in fields.items()):
            return True
        if any(row.get(key) != value for key, value in expected.items()):
            return False
        _preserve_controller(row, fields)
        row.update(fields, updated_at=time.time())
        _write(p, items)
        return True


def project_permissions(host, session_id, expected, operation_id, fields):
    """Idempotent projection after all ACKs; never overwrite newer ownership or policy."""
    p = registry_path()
    with _locked(p):
        items = _read(p)
        entry = next((e for e in items if e.get("host") == host and e.get("session_id") == session_id), None)
        if entry is None:
            return {"updated": False, "reason": "registry_missing"}
        if entry.get("permission_operation_id") == operation_id and all(entry.get(k) == v for k, v in fields.items()):
            return {"updated": True, "replayed": True}
        if any(entry.get(k) != v for k, v in expected.items()):
            return {"updated": False, "reason": "registry_binding_changed"}
        _preserve_controller(entry, fields)
        entry.update(fields, permission_operation_id=operation_id, updated_at=time.time())
        _write(p, items)
        return {"updated": True}


def retire(host: str, session_id: str, status: str, *, created_at, actor: str, reason: str,
           operation_id: str | None = None, carrier_resource_id: str | None = None,
           expected: dict | None = None, controller_incarnation: str | None = None) -> dict:
    """Release capacity for a confirmed runtime generation without retiring its worktree/history."""
    if status not in RETIRED:
        raise ValueError("invalid session retirement status")
    p = registry_path()
    with _locked(p):
        try:
            document = json.loads(p.read_text())
        except FileNotFoundError:
            document = {"sessions": []}
        except ValueError:
            from .errors import ResourceReadOnly
            raise ResourceReadOnly("REGISTRY_READ_FAILED", "registry retirement data is invalid") from None
        if not isinstance(document, dict) or not isinstance(document.get("sessions", []), list):
            from .errors import ResourceReadOnly
            raise ResourceReadOnly("REGISTRY_READ_FAILED", "registry retirement data is invalid")
        items = [e for e in document.get("sessions", []) if isinstance(e, dict)]
        _validate_unique(items)
        for e in items:
            if e.get("host") != host or e.get("session_id") != session_id:
                continue
            result = {"capacity_released": False, "registry_status": e.get("status"), "capacity_reason": None}
            if e.get("created_at") != created_at:
                return {**result, "capacity_reason": "generation_changed"}
            # Check the original resource inside the same lock as the write and before
            # accepting an idempotent retirement receipt from a possibly replaced row.
            incarnation = controller_incarnation if controller_incarnation is not None else (expected or {}).get("controller_incarnation")
            if incarnation is not None:
                if e.get("controller_incarnation") != incarnation:
                    return {**result, "capacity_reason": "generation_changed"}
                from . import controller_authority
                controller_authority.require_entry(e, expected_incarnation=incarnation)
            if e.get("task_id"):
                return {**result, "capacity_reason": "task_owned"}
            if e.get("status") == "starting":
                return {**result, "capacity_reason": "start_unsettled"}
            retirement = {"actor": actor, "reason": reason, "operation_id": operation_id,
                          "carrier_resource_id": carrier_resource_id}
            if e.get("status") == status and e.get("retirement") == retirement:
                return {**result, "capacity_released": True}
            if expected is not None:
                if any(e.get(k) != v for k, v in expected.items()):
                    return {**result, "capacity_reason": "generation_changed"}
                refuse_start_claim(p, host, session_id)
                from .cleanup import guard
                guard(host, session_id=session_id, path=e.get("worktree_path") or e.get("cwd"), branch=e.get("branch"))
            if e.get("status") != "active":
                return {**result, "capacity_reason": "not_counted"}
            e.update(status=status, retired_at=time.time(), retirement=retirement, updated_at=time.time())
            if status == "stopped":
                e.update(stopped_at=e["retired_at"], stopped_by=actor)
            _write(p, items)
            return {**result, "capacity_released": True, "registry_status": status}
    return {"capacity_released": False, "registry_status": None, "capacity_reason": "generation_changed"}


def claim_warm(host: str, session_id: str, *, previous_task_id: str, task_id: str,
               workspace_id: str, cwd: str, branch: str) -> None:
    """Transfer one completed task's lead session under the registry flock."""
    from .errors import TaskIdentityMismatch

    p = registry_path()
    with _locked(p):
        from .cleanup import guard
        guard(host, session_id=session_id, path=cwd, branch=branch)
        items = _read(p)
        matches = [e for e in items if e.get("host") == host and e.get("session_id") == session_id]
        if (len(matches) != 1 or matches[0].get("task_id") != previous_task_id
                or matches[0].get("role") != "lead" or matches[0].get("status") != "active"
                or matches[0].get("workspace_id") != workspace_id
                or matches[0].get("cwd") != cwd or matches[0].get("worktree_path") != cwd
                or matches[0].get("branch") != branch
                or any(e.get("host") == host and e.get("failover_of") == session_id
                       and e.get("status") in {"active", "starting"} for e in items)):
            raise TaskIdentityMismatch("warm session ownership changed before claim")
        from . import controller_authority
        controller_authority.require_entry(matches[0])
        matches[0].update(task_id=task_id, title="task " + task_id[:8],
                          warm_from_task_id=previous_task_id, updated_at=time.time())
        _write(p, items)


def confirm_failover_start(host: str, session_id: str, *, message_id: str, **fields) -> dict:
    """Activate a read-back without overwriting a concurrently recorded handoff.

    Older start blocks could not attempt a handoff before settling their start.
    Backfill their null fence and message ID under the same registry flock.
    """
    from . import confinement
    from .errors import TaskIdentityMismatch

    p = registry_path()
    with _locked(p):
        items = _read(p)
        successor = next((e for e in items if e.get("host") == host and e.get("session_id") == session_id), None)
        if not successor or not successor.get("failover_of"):
            raise TaskIdentityMismatch("reserved successor disappeared during start read-back")
        confinement.guard_start_record(successor)
        if (successor.get("start_uncertain") or successor.get("status") == "starting") and successor.get("handoff_status") == "pending":
            successor.setdefault("handoff_frame_sha256", None)
            if not successor.get("handoff_message_id"):
                successor["handoff_message_id"] = message_id
        _preserve_controller(successor, fields)
        successor.update(fields, updated_at=time.time())
        _write(p, items)
        return successor


def claim_handoff_frame(host: str, successor_id: str, message_id: str, prompt_sha256: str) -> None:
    """Consume the durable proof of an unsent failover handoff before transport.

    Explicit null means this reservation uses the frame fence; a missing key in
    a legacy row is not proof. The flock also fences separate daemon processes.
    Task Service's ownership guard still runs on the actual frame afterwards.
    """
    from .errors import TaskIdentityMismatch

    p = registry_path()
    with _locked(p):
        items = _read(p)
        successor = next((e for e in items if e.get("host") == host and
                          e.get("session_id") == successor_id), None)
        if (not successor or not successor.get("failover_of") or successor.get("status") != "active"
                or successor.get("handoff_status") != "pending"
                or successor.get("handoff_message_id") != message_id
                or "handoff_frame_sha256" not in successor or successor["handoff_frame_sha256"] is not None):
            raise TaskIdentityMismatch("failover handoff may already have been attempted")
        successor.update(handoff_frame_sha256=prompt_sha256, updated_at=time.time())
        _write(p, items)


def record_handoff_frame(host: str, *, old_session_id: str, successor_id: str, task_id: str,
                         worktree_path: str, branch: str, command_id: str,
                         message_id: str, prompt_sha256: str) -> None:
    """Atomically bind an actual BAT frame to the still-owned successor registry row."""
    from .errors import TaskIdentityMismatch

    p = registry_path()
    with _locked(p):
        items = _read(p)
        old = next((e for e in items if e.get("host") == host and
                    e.get("session_id") == old_session_id), None)
        successor = next((e for e in items if e.get("host") == host and
                          e.get("session_id") == successor_id), None)
        if (not old or old.get("worktree_path") != worktree_path or old.get("branch") != branch
                or not successor or successor.get("session_id") != successor_id
                or successor.get("failover_of") != old_session_id
                or successor.get("shares_worktree_with") != old_session_id
                or successor.get("task_id") != task_id
                or successor.get("worktree_path") != worktree_path
                or successor.get("cwd") != worktree_path or successor.get("branch") != branch
                or successor.get("status") != "active" or successor.get("handoff_status") != "pending"
                or successor.get("handoff_command_id") != command_id
                or successor.get("handoff_message_id") != message_id):
            raise TaskIdentityMismatch("successor registry ownership changed before BAT handoff frame")
        successor["handoff_frame_sha256"] = prompt_sha256
        successor["updated_at"] = time.time()
        _write(p, items)


def turn_path() -> Path:
    return state_dir() / "turns.json"


def record_turn(host: str, session_id: str, message_id: str, *, queued: bool, baseline_turns: int | None) -> None:
    """Keep the accepted command identity across separate batc/MCP calls on this machine."""
    p = turn_path()
    with _locked(p):
        try:
            items = json.loads(p.read_text()).get("turns", [])
        except (OSError, ValueError):
            items = []
        if any(e.get("host") == host and e.get("session_id") == session_id and
               e.get("message_id") == message_id for e in items):
            return  # preserve the original queue fence on an idempotent retry
        items.append({"host": host, "session_id": session_id, "message_id": message_id,
                      "queued": queued, "baseline_turns": baseline_turns, "accepted_at": time.time()})
        fd = os.open(p.with_suffix(".tmp"), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as fh:
            json.dump({"turns": items[-1000:]}, fh)
        os.replace(p.with_suffix(".tmp"), p)


def get_turn(host: str, session_id: str, message_id: str) -> dict | None:
    try:
        items = json.loads(turn_path().read_text()).get("turns", [])
    except (OSError, ValueError):
        return None
    return next((e for e in reversed(items) if e.get("host") == host and
                 e.get("session_id") == session_id and e.get("message_id") == message_id), None)


def update_turn_boundary(host: str, session_id: str, message_id: str, boundary_ms: int) -> None:
    p = turn_path()
    with _locked(p):
        try:
            items = json.loads(p.read_text()).get("turns", [])
        except (OSError, ValueError):
            return
        for e in items:
            if e.get("host") == host and e.get("session_id") == session_id and e.get("message_id") == message_id:
                e.setdefault("boundary_ms", boundary_ms)
        fd = os.open(p.with_suffix(".tmp"), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as fh:
            json.dump({"turns": items}, fh)
        os.replace(p.with_suffix(".tmp"), p)
