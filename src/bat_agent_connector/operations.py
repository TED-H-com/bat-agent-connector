"""Durable operations: one record per Dashboard, MCP or CLI action, shared by every entry point.

An operation is persisted (intent, actor, idempotency key, preconditions) before anything reaches BAT, Git or a
provider. Each external call is a named step whose intent is committed first; a timeout or lost connection leaves
the step ``uncertain`` and is settled by reading state back, never by sending again. HTTP (/api/v1), MCP and CLI
all call ``OperationService.create`` and read the same rows, so they share one permission, precondition, execution
and record path. Design: docs/design/api-v1.md.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import logging
import re
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from .api_auth import Principal
from .errors import (
    BatError,
    ConnectionLost,
    InvokeTimeout,
    ResourceReadOnly,
    TaskControlRefused,
    WriteRefused,
)
from .redact import redact

STATES = ("accepted", "running", "waiting_checks", "waiting_external", "needs_attention", "uncertain",
          "succeeded", "failed", "cancelled")
TERMINAL = frozenset({"succeeded", "failed", "cancelled"})
# Rows the worker picks up again (``running`` after a restart replays its steps).
RUNNABLE = ("accepted", "running", "waiting_checks", "waiting_external", "uncertain")
ALLOWED = {
    "accepted": {"running", "failed", "cancelled"},
    "running": {"running", "waiting_checks", "waiting_external", "needs_attention", "uncertain", "succeeded",
                "failed", "cancelled"},
    "waiting_checks": {"running", "cancelled"},
    "waiting_external": {"running", "cancelled"},
    # No uncertain -> cancelled: a cancel waits for the read-back, so "cancelled" never hides a step that ran.
    "uncertain": {"running", "needs_attention"},
    "needs_attention": {"running", "cancelled", "failed"},
}
UNCERTAIN_RETRY_S = (30.0, 60.0, 120.0, 300.0, 600.0)
NO_KEY_PREFIX = "batc:nokey:"
PM_KEY_PREFIX = NO_KEY_PREFIX + "pm:"
LEGACY_SESSION_ACTIONS = frozenset({"worktree.merge", "session.record_verification", "session.failover", "fanout.plan", "fanout.start", "session.relay", "session.start", "session.send", "session.answer", "session.interrupt", "session.permissions", "session.approve_pending"})
LEGACY_TASK_ACTIONS = frozenset({"task.pause", "task.resume", "task.mark_stage",
                                 "task.verify", "task.request_ted", "task.command.reconcile"})


class AmbiguousOutcome(Exception):
    """An external call whose effect is unknown (timeout, lost connection, 5xx after sending)."""


AMBIGUOUS = (InvokeTimeout, ConnectionLost, asyncio.TimeoutError, AmbiguousOutcome)
# A reconcile function returns RERUN when it proved the earlier attempt had no effect and the call is safe to
# make again (for example a merge request deduplicated by the provider). Only use it with such proof.
RERUN: dict = {"__rerun__": True}


class OperationError(Exception):
    """A request the service refuses; ``status`` is the HTTP status the API returns."""

    def __init__(self, code: str, message: str, status: int = 400) -> None:
        self.code, self.message, self.status = code, message, status
        super().__init__(f"[{code}] {message}")


class NeedsAttention(Exception):
    def __init__(self, code: str, message: str) -> None:
        self.code, self.message = code, message
        super().__init__(message)


class Wait(Exception):
    """Pause the operation in ``waiting_checks``/``waiting_external`` and run it again after ``delay_s``."""

    def __init__(self, status: str, reason: str, delay_s: float = 30.0, refs: dict | None = None) -> None:
        if status not in {"waiting_checks", "waiting_external"}:
            raise ValueError("wait status must be waiting_checks or waiting_external")
        self.status, self.reason, self.delay_s, self.refs = status, reason, max(1.0, delay_s), refs or {}
        super().__init__(reason)


class Uncertain(Exception):
    def __init__(self, step: str, message: str) -> None:
        self.step, self.message = step, message
        super().__init__(message)


class StepFailed(Exception):
    def __init__(self, code: str, message: str) -> None:
        self.code, self.message = code, message
        super().__init__(message)


class Cancelled(Exception):
    pass


@dataclass(frozen=True)
class ActionDef:
    """One action: scope, admission (with an optional server binding), and handler."""

    name: str
    scope: str
    summary: str
    run: Callable[[OpContext], Awaitable[dict]]
    admit: Callable[[OperationService, Principal, dict, dict, dict], dict | None] | None = None
    target_keys: tuple[str, ...] = ()
    # Existing intent must not repeat admission (for example, an accepted preview may have expired),
    # but some actions bind replay and controls to authority narrower than their primary scope.
    authorize_existing: Callable[[OperationService, Principal, dict, str], None] | None = None


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str)


def _workspace_key(action: str, target: Any = None) -> bool:
    if action.startswith("pm.") or action in {"work.start", "work.adopt_and_start"}:
        return True
    return (
        action == "session.send"
        and isinstance(target, dict)
        and ("workspace_id" in target or "work_item_id" in target)
    )


def _pm_key_prefix(target: dict) -> str:
    # The journal is server-scoped and the unique index also includes the actor.
    # Keep legacy keys intact; new PM keys additionally belong to one workspace.
    namespace = hashlib.sha256(_canonical(target.get("workspace_id")).encode()).hexdigest()
    return PM_KEY_PREFIX + namespace + ":"


def _error_code(exc: BaseException) -> str:
    from .confinement import ConfinementRefused
    from .session_permissions import PermissionsRefused

    if isinstance(exc, ConfinementRefused | ResourceReadOnly | TaskControlRefused | PermissionsRefused):
        return exc.code
    if isinstance(exc, StepFailed | NeedsAttention | OperationError):
        return exc.code
    if isinstance(exc, WriteRefused):
        return "REFUSED"
    if isinstance(exc, BatError):
        return "BAT_ERROR"
    return "INTERNAL"


@dataclass
class OpContext:
    service: OperationService
    op: dict
    replayed: list[str] = field(default_factory=list)

    @property
    def operation_id(self) -> str:
        return self.op["operation_id"]

    @property
    def target(self) -> dict:
        return self.op["target"]

    @property
    def params(self) -> dict:
        return self.op["params"]

    @property
    def preconditions(self) -> dict:
        return self.op["preconditions"]

    @property
    def admission_binding(self) -> dict | None:
        return (self.op.get("external_refs") or {}).get("admission_binding")

    @property
    def effective_preconditions(self) -> dict:
        if self.admission_binding and "control_version" in self.admission_binding:
            return {**self.preconditions, "control_version": self.admission_binding["control_version"]}
        return self.preconditions

    @property
    def actor(self) -> str:
        return self.op["actor"]

    def check_cancel(self) -> None:
        row = self.service.db.execute("SELECT cancel_requested FROM operations WHERE operation_id=?",
                                      (self.operation_id,)).fetchone()
        if row and row["cancel_requested"]:
            raise Cancelled()

    def require_controller(self) -> dict:
        """Check the persisted creation authority immediately before a resource effect."""
        from . import controller_authority
        return controller_authority.require_binding(controller_authority.operation_binding(
            self.service.get(self.operation_id, steps=False)))

    def set_refs(self, **refs: Any) -> None:
        self.service._merge_refs(self.operation_id, refs)

    def effect(self, name: str, fn: Callable[[], dict], *, request: dict | None = None,
               refs: dict | Callable[[dict], dict] | None = None, receipt_only: bool = False,
               lifecycle_bookkeeping: bool = False) -> dict:
        """Commit a local task effect and its receipt in one journal transaction.

        A started receipt without a result proves the effect transaction rolled back. Replaying the
        original journal method is safe; no BAT/provider call may run inside this callback. Refs commit with
        the receipt; replay repairs their missing values without running the effect again.
        receipt_only permits bookkeeping after cancellation only when the caller has already
        proven every external effect; it must never authorize a new external action.
        lifecycle_bookkeeping allows proven original-result import after a workspace stop
        without bypassing the ordinary cancellation or current source/authority checks.
        """
        row = self.service.db.execute("SELECT * FROM operation_steps WHERE operation_id=? AND name=?",
                                      (self.operation_id, name)).fetchone()
        if row and row["status"] == "succeeded":
            self.replayed.append(name)
            result = json.loads(row["response"] or "{}")
            self._effect_refs(result, refs)
            return result
        if not receipt_only:
            self.check_cancel()
        if row is None:
            self.service._step_start(self.operation_id, name, request or {}, local=True,
                                     bookkeeping=receipt_only or lifecycle_bookkeeping)
        with self.service.journal.tx():
            if not receipt_only:
                from .host_git import require_execution
                require_execution()
                from .pm_source_lineage import require_new_effect
                require_new_effect(self.service, self.operation_id)
            from .pm_lifecycle_gates import require_local
            require_local(self.service.db, self.operation_id, bookkeeping=receipt_only or lifecycle_bookkeeping)
            result = fn()
            self.service._step_done(self.operation_id, name, result or {})
            self._effect_refs(result or {}, refs)
        return result or {}

    def _effect_refs(self, result: dict, refs: dict | Callable[[dict], dict] | None) -> None:
        if refs is None:
            return
        values = refs(result) if callable(refs) else refs
        row = self.service.db.execute("SELECT external_refs FROM operations WHERE operation_id=?",
                                      (self.operation_id,)).fetchone()
        current = json.loads(row["external_refs"] or "{}")
        if any(k not in current or current[k] != v for k, v in values.items()):
            self.set_refs(**values)

    async def step(self, name: str, fn: Callable[[], Awaitable[dict]], *, request: dict | None = None,
                   reconcile: Callable[[dict], Awaitable[dict | None]] | None = None,
                   receipt_only: bool = False) -> dict:
        """Run one external call exactly once from this record's point of view.

        A finished step returns its stored response. A step whose earlier run never recorded an outcome (process
        restart, timeout, lost connection) is ``uncertain``: ``reconcile`` reads the outside world back and returns
        the response, or None when the outcome still cannot be proven. The call is never repeated.
        Internal receipt_only permits proven local bookkeeping after cancellation,
        never a BAT/provider effect. Its caller must first prove every required ACK.
        """
        db = self.service.db
        row = db.execute("SELECT * FROM operation_steps WHERE operation_id=? AND name=?",
                         (self.operation_id, name)).fetchone()
        if row is not None:
            if row["status"] == "succeeded":
                self.replayed.append(name)
                return json.loads(row["response"] or "{}")
            if row["status"] == "failed":
                err = json.loads(row["error"] or "{}")
                raise StepFailed(err.get("code", "STEP_FAILED"), err.get("message", "step failed earlier"))
            try:
                recovered = await reconcile(json.loads(row["request"])) if reconcile else None
            except (*AMBIGUOUS, BatError, OSError):  # the read-back itself failed: still unproven, try later
                recovered = None
            if recovered is None:
                self.service._step_status(self.operation_id, name, "uncertain")
                raise Uncertain(name, f"outcome of step {name!r} is not proven; reading it back again later")
            if recovered is not RERUN:
                self.service._step_done(self.operation_id, name, recovered, reconciled=True)
                return recovered
            try:  # proven not to have happened: a cancel requested meanwhile stops here instead of sending it now
                if not receipt_only:
                    self.check_cancel()
            except Cancelled:
                self.service._step_status(self.operation_id, name, "failed",
                                          error={"code": "CANCELLED", "message": "proven not sent; cancelled"})
                raise
            try:
                self.service._step_restart(self.operation_id, name, request or {}, bookkeeping=receipt_only)
            except OperationError as exc:
                if exc.code in {'PM_LIFECYCLE_STOPPED', 'PM_LIFECYCLE_EPOCH_CHANGED'}:
                    self.service._step_status(self.operation_id, name, 'failed',
                        error={'code': exc.code, 'message': 'Proven not sent; lifecycle blocks a new attempt'})
                raise
        else:
            if not receipt_only:
                self.check_cancel()
            self.service._step_start(self.operation_id, name, request or {}, bookkeeping=receipt_only)
        try:
            response = await fn()
        except Wait as exc:
            fresh = self.service._row(self.operation_id) or {}
            sent = (fresh.get("external_refs") or {}).get(f"{name}_sent")
            if sent is False:
                raise
            if sent is True:
                self.service._step_status(
                    self.operation_id,
                    name,
                    "uncertain",
                    error={"code": "UNCERTAIN", "message": "wait requested after transport fence"},
                )
                raise Uncertain(name, f"step {name!r} requested wait after transport fence") from None
            code = _error_code(exc)
            message = type(exc).__name__
            self.service._step_status(self.operation_id, name, "failed", error={"code": code, "message": message})
            raise StepFailed(code, message) from exc
        except NeedsAttention:
            self.service._step_status(self.operation_id, name, "uncertain")
            raise
        except (*AMBIGUOUS, OSError) as exc:  # OSError: local bookkeeping may fail after the external call
            self.service._step_status(self.operation_id, name, "uncertain",
                                      error={"code": "UNCERTAIN", "message": redact(f"{type(exc).__name__}: {exc}")})
            raise Uncertain(name, f"step {name!r} may or may not have happened ({type(exc).__name__})") from None
        except Exception as exc:  # noqa: BLE001 - recorded, then re-raised as a definitive failure
            code = _error_code(exc)
            message = redact(str(exc)) if isinstance(exc, BatError | OperationError | StepFailed) else type(exc).__name__
            self.service._step_status(self.operation_id, name, "failed", error={"code": code, "message": message})
            raise StepFailed(code, message) from exc
        self.service._step_done(self.operation_id, name, response or {})
        return response or {}


class OperationService:
    def __init__(self, journal, *, actions: list[ActionDef] | None = None) -> None:
        self.journal = journal
        self.db = journal.db
        self.actions: dict[str, ActionDef] = {}
        self._active: dict[str, asyncio.Task] = {}
        self._wake: asyncio.Event | None = None
        self.context: dict[str, Any] = {}  # handlers' shared dependencies (fleet, inventory, ...)
        from .host_git import install as install_git_bindings
        install_git_bindings(journal)
        for a in actions or []:
            self.register(a)

    def register(self, action: ActionDef) -> None:
        if action.name in self.actions:
            raise ValueError(f"action {action.name} is already registered")
        self.actions[action.name] = action

    # ------------------------------------------------------------------ records
    def _row(self, operation_id: str) -> dict | None:
        row = self.db.execute("SELECT * FROM operations WHERE operation_id=?", (operation_id,)).fetchone()
        return self._decode(row) if row else None

    @staticmethod
    def _decode(row) -> dict:
        op = dict(row)
        for key in ("target", "params", "preconditions"):
            op[key] = json.loads(op[key])
        for key in ("result", "external_refs"):
            op[key] = json.loads(op[key]) if op[key] else None
        op["cancel_requested"] = bool(op["cancel_requested"])
        # Keep the old field for readers, but never expose a storage sentinel as a client key.
        if op["idem_key"] == NO_KEY_PREFIX + op["operation_id"]:
            op["idem_key"] = None
        elif _workspace_key(op["action"], op["target"]) and op["idem_key"].startswith(_pm_key_prefix(op["target"])):
            op["idem_key"] = op["idem_key"][len(_pm_key_prefix(op["target"])):]
        op["idempotency_key"] = op["idem_key"]
        op["idempotency_enabled"] = op["idem_key"] is not None
        op.pop("request_hash", None)
        return op

    def get(self, operation_id: str, *, steps: bool = True, principal: Principal | None = None) -> dict:
        op = self._row(operation_id)
        if op is None:
            raise OperationError("NOT_FOUND", "operation not found", 404)
        if principal is not None:
            from .pm_access import require_visible
            require_visible(self.db, principal, op)
        if steps:
            op["steps"] = [{"seq": r["seq"], "name": r["name"], "status": r["status"],
                            "external_ref": r["external_ref"],
                            "error": json.loads(r["error"]) if r["error"] else None,
                            "started_at": r["started_at"], "finished_at": r["finished_at"]}
                           for r in self.db.execute("""SELECT * FROM operation_steps WHERE operation_id=?
                               ORDER BY seq""", (operation_id,))]
        return op

    def list(self, *, statuses: list[str] | None = None, actor: str | None = None, action: str | None = None,
             before: float | None = None, before_id: str | None = None, limit: int = 50, principal: Principal | None = None) -> dict:
        limit = max(1, min(200, int(limit)))
        if before_id is not None and (before is None or not isinstance(before_id, str)
                or not re.fullmatch(r"op_[0-9a-f]{32}", before_id)):
            raise OperationError("INVALID_FILTER", "Use before and its bounded operation before_id together / "
                "請同時提供 before 與對應且符合格式的操作 before_id", 422)
        sql, args = "SELECT * FROM operations WHERE 1=1", []
        if statuses:
            bad = [s for s in statuses if s not in STATES]
            if bad:
                raise OperationError("INVALID_FILTER", f"unknown status {bad[0]!r}", 422)
            sql += f" AND status IN ({','.join('?' * len(statuses))})"  # noqa: S608 - placeholders only
            args += statuses
        if actor:
            sql, args = sql + " AND actor=?", [*args, actor]
        if action:
            sql, args = sql + " AND action=?", [*args, action]
        if before is not None:
            if before_id is not None:
                sql += " AND (created_at<? OR (created_at=? AND operation_id<?))"
                args += [float(before), float(before), before_id]
            else:
                sql, args = sql + " AND created_at<?", [*args, float(before)]
        rows = self.db.execute(sql + " ORDER BY created_at DESC, operation_id DESC LIMIT ?", (*args, limit + 1)).fetchall()
        ops = [self._decode(r) for r in rows[:limit]]
        next_before = ops[-1]["created_at"] if len(rows) > limit else None
        next_before_id = ops[-1]["operation_id"] if len(rows) > limit else None
        if principal is not None:
            from .pm_access import visible
            ops = [op for op in ops if visible(self.db, principal, op)]
        return {"operations": ops, "next_before": next_before, "next_before_id": next_before_id}

    def _transition(self, operation_id: str, status: str, *, reason: str | None = None,
                    error_code: str | None = None, result: dict | None = None, next_run_at: float = 0.0,
                    attempts: int | None = None, uncertain_tries: int | None = None,
                    actor: str | None = None) -> dict:
        with self.journal.tx():
            row = self.db.execute("SELECT * FROM operations WHERE operation_id=?", (operation_id,)).fetchone()
            if row is None:
                raise OperationError("NOT_FOUND", "operation not found", 404)
            old = row["status"]
            if old in TERMINAL:
                return self._decode(row)
            if status not in ALLOWED.get(old, set()):
                raise ValueError(f"operation transition {old} -> {status} is not allowed")
            now = time.time()
            self.db.execute("""UPDATE operations SET status=?,status_reason=?,error_code=?,
                result=COALESCE(?,result),next_run_at=?,attempts=COALESCE(?,attempts),
                uncertain_tries=COALESCE(?,uncertain_tries),version=version+1,updated_at=? WHERE operation_id=?""",
                            (status, (reason or "")[:500] or None, error_code,
                             _canonical(result) if result is not None else None, next_run_at, attempts,
                             uncertain_tries, now, operation_id))
            if status != old:
                body = {"action": row["action"], "from": old, "to": status,
                        "error_code": error_code, "reason": (reason or "")[:200] or None}
                from .pm_work_follow_up import transition_event_fields
                body.update(transition_event_fields(self.db, operation_id, row["action"]))
                self.journal.api_event("operation", operation_id, "operation." + status, body,
                                       actor=actor or row["actor"])
        if status != old and status in {"succeeded", "failed", "cancelled"}:
            from .pm_work_follow_up import on_transition
            on_transition(self, operation_id, status)
        if status != old and status in TERMINAL | {"needs_attention"}:
            # A compound action may have made its parent due in the same commit.
            self.kick()
        return self._row(operation_id)

    def _merge_refs(self, operation_id: str, refs: dict) -> None:
        with self.journal.tx():
            row = self.db.execute("SELECT external_refs FROM operations WHERE operation_id=?",
                                  (operation_id,)).fetchone()
            previous = json.loads(row["external_refs"]) if row and row["external_refs"] else {}
            merged = {**previous, **refs}
            self.db.execute("UPDATE operations SET external_refs=?,updated_at=? WHERE operation_id=?",
                            (_canonical(merged), time.time(), operation_id))
            if merged != previous:
                self._observation_event(operation_id, "resource.bound", {"refs": refs})

    def _observation_event(self, operation_id: str, kind: str, data: dict) -> None:
        from .observation import operation_summary
        row = self.db.execute("SELECT actor FROM operations WHERE operation_id=?", (operation_id,)).fetchone()
        if data.get("step"):
            step = self.db.execute("SELECT seq,status FROM operation_steps WHERE operation_id=? AND name=?", (operation_id, data["step"])).fetchone()
            if step:
                data = {"step_seq": step["seq"], "status": step["status"], **data}
        self.journal.api_event("operation", operation_id, kind, operation_summary(self.db, operation_id, data), actor=row["actor"] if row else None,
                               context={"entry_point": "daemon", "observer": "operation-service"})

    def _step_start(self, operation_id: str, name: str, request: dict, *, local=False, bookkeeping=False) -> None:
        with self.journal.tx():
            if not bookkeeping:
                from .host_git import require_execution
                require_execution()
                from .pm_source_lineage import require_new_effect
                require_new_effect(self, operation_id)
            from .pm_lifecycle_gates import admit_step
            admit_step(self.db, operation_id, name, local=local, bookkeeping=bookkeeping)
            seq = self.db.execute("SELECT COALESCE(MAX(seq),0)+1 FROM operation_steps WHERE operation_id=?",
                                  (operation_id,)).fetchone()[0]
            from .host_git import bind_step
            bind_step(self, operation_id, name)
            self.db.execute("""INSERT INTO operation_steps(operation_id,seq,name,status,request,started_at)
                VALUES(?,?,?,?,?,?)""", (operation_id, seq, name, "started", _canonical(request), time.time()))
            self._observation_event(operation_id, "operation.step.started", {"step": name, "step_seq": seq, "request": request})

    def _step_restart(self, operation_id: str, name: str, request: dict, *, bookkeeping=False) -> None:
        with self.journal.tx():
            if not bookkeeping:
                from .host_git import require_execution
                require_execution()
                from .pm_source_lineage import require_new_effect
                from .pm_work_batches import admit_restart
                require_new_effect(self, operation_id)
                admit_restart(self, operation_id, name)
                from .pm_work_start import admit_restart as admit_work_start_restart
                admit_work_start_restart(self, operation_id, name)
                from .pm_work_adopt_and_start import admit_restart as admit_adopt_start_restart
                admit_adopt_start_restart(self, operation_id, name)
                from .pm_maintenance_repairs import admit_restart as admit_repair_restart
                admit_repair_restart(self, operation_id, name, request)
            from .pm_lifecycle_gates import admit_step
            admit_step(self.db, operation_id, name, bookkeeping=bookkeeping, restart=True)
            from .host_git import bind_step
            bind_step(self, operation_id, name)
            self.db.execute("""UPDATE operation_steps SET status='started',request=?,response=NULL,error=NULL,
                started_at=?,finished_at=NULL WHERE operation_id=? AND name=?""",
                            (_canonical(request), time.time(), operation_id, name))
            self._observation_event(operation_id, "operation.step.restarted", {"step": name, "request": request})

    def _step_done(self, operation_id: str, name: str, response: dict, *, reconciled: bool = False) -> None:
        with self.journal.tx():
            self.db.execute("""UPDATE operation_steps SET status='succeeded',response=?,finished_at=?,
                external_ref=COALESCE(?,external_ref) WHERE operation_id=? AND name=?""",
                            (_canonical({**response, **({"reconciled": True} if reconciled else {})}),
                             time.time(), response.get("external_ref"), operation_id, name))
            self._observation_event(operation_id, "operation.step.succeeded", {"step": name, "response": response, "reconciled": reconciled})

    def _step_status(self, operation_id: str, name: str, status: str, *, error: dict | None = None) -> None:
        with self.journal.tx():
            self.db.execute("""UPDATE operation_steps SET status=?,error=COALESCE(?,error),
                finished_at=CASE WHEN ?='failed' THEN ? ELSE finished_at END WHERE operation_id=? AND name=?""",
                            (status, _canonical(error) if error else None, status, time.time(), operation_id,
                             name))
            self._observation_event(operation_id, "operation.step." + status, {"step": name, "status": status, "error_code": (error or {}).get("code")})

    def has_idempotency_record(self, principal: Principal, *, action, target, idempotency_key) -> bool:
        """Internal admission classification, not a public lookup or source read.

        A hidden original is still admitted. Only this actor's exact key namespace
        matters; a filtered get or another actor's key cannot establish absence.
        """
        if not isinstance(action, str) or not isinstance(idempotency_key, str):
            return False
        key = idempotency_key.strip()
        if _workspace_key(action, target):
            target = target or {}  # Match create(): omitted workspace-create target is {}.
            if not isinstance(target, dict):
                return False
            try:
                key = _pm_key_prefix(target) + key
            except UnicodeEncodeError:
                return False  # Such a target could not have been admitted.
        return self.db.execute("SELECT 1 FROM operations WHERE actor=? AND idem_key=?",
                               (principal.actor, key)).fetchone() is not None

    # ------------------------------------------------------------------ create / cancel
    def _prepare_create(self, principal, *, action, target=None, params=None, preconditions=None,
                        idempotency_key, _legacy_session=False, _legacy_task=False):
        """Validate caller intent and replay before any asynchronous compatibility resolution."""
        adef = self.actions.get(action)
        if adef is None:
            raise OperationError("UNKNOWN_ACTION", f"unknown action {action!r}", 422)
        if not principal.allows(adef.scope):
            raise OperationError("FORBIDDEN", f"{action} needs the {adef.scope!r} scope", 403)
        no_key = idempotency_key is None and (
            _legacy_session and action in LEGACY_SESSION_ACTIONS
            or _legacy_task and action in LEGACY_TASK_ACTIONS)
        if not no_key and (not isinstance(idempotency_key, str) or not 1 <= len(idempotency_key.strip()) <= 200):
            raise OperationError("IDEMPOTENCY_KEY_REQUIRED", "idempotency_key must be 1-200 characters", 422)
        if isinstance(idempotency_key, str) and idempotency_key.strip().startswith(NO_KEY_PREFIX):
            raise OperationError("INVALID_IDEMPOTENCY_KEY", "idempotency_key uses a reserved prefix", 422)
        target, params, preconditions = target or {}, params or {}, preconditions or {}
        if not all(isinstance(x, dict) for x in (target, params, preconditions)):
            raise OperationError("INVALID_REQUEST", "target, params and preconditions must be objects", 422)
        missing = [k for k in adef.target_keys if not isinstance(target.get(k), str) or not target[k]]
        if missing:
            raise OperationError("INVALID_TARGET", f"target needs {', '.join(missing)}", 422)
        try:
            request_hash = hashlib.sha256(_canonical({"action": action, "target": target, "params": params,
                                                      "preconditions": preconditions}).encode()).hexdigest()
        except UnicodeEncodeError:
            raise OperationError("INVALID_REQUEST", "request text must contain valid Unicode characters", 422) from None
        key = None if no_key else idempotency_key.strip()
        if _workspace_key(action, target):
            key = _pm_key_prefix(target) + key
        existing = self.db.execute("SELECT * FROM operations WHERE actor=? AND idem_key=?",
                                   (principal.actor, key)).fetchone()
        if existing and not _workspace_key(action, target):
            if existing["request_hash"] != request_hash:
                raise OperationError("IDEMPOTENCY_CONFLICT",
                                     "this idempotency_key was already used for a different request", 409)
        op = self._decode(existing) if existing else None
        if op:
            from .pm_access import require_visible
            require_visible(self.db, principal, op)
            self._authorize_controller(principal, op, "replay")
            from .bulk_approval import authorize_child
            authorize_child(self, principal, op, "replay")
            from .orchestration_operations import authorize_child as authorize_orchestration_child
            authorize_orchestration_child(self, principal, op, "replay")
        if op and adef.authorize_existing:
            adef.authorize_existing(self, principal, op, "replay")
        if existing and _workspace_key(action, target) and existing["request_hash"] != request_hash:
            raise OperationError("IDEMPOTENCY_CONFLICT",
                                 "this idempotency_key was already used for a different request", 409)
        return adef, target, params, preconditions, key, request_hash, op

    def create(self, principal: Principal, *, action: str, target: dict | None = None, params: dict | None = None,
               preconditions: dict | None = None, idempotency_key: str | None, entry: str = "http",
               _legacy_session: bool = False, _resolved_target: dict | None = None,
               _resolved_params: dict | None = None, _legacy_task: bool = False) -> tuple[dict, bool]:
        """Persist intent. Private compatibility arguments are never accepted from HTTP/RPC bodies."""
        adef, target, params, preconditions, key, request_hash, existing = self._prepare_create(
            principal, action=action, target=target, params=params, preconditions=preconditions,
            idempotency_key=idempotency_key, _legacy_session=_legacy_session, _legacy_task=_legacy_task)
        if existing:
            return existing, False
        if (_resolved_target is not None or _resolved_params is not None) and not (
                _legacy_session and action in LEGACY_SESSION_ACTIONS):
            raise ValueError("resolved inputs are limited to the legacy session adapter")
        if _resolved_params is not None and (action != "session.answer" or set(_resolved_params) != {"tool_use_id"}):
            raise ValueError("only the legacy answer adapter may bind an observed pending prompt")
        # Ordinary admission may enrich persisted params with server-only authority (cleanup).
        # Only a legacy answer needs a separate view: its observed prompt is stored in refs,
        # never added to the caller's literal params or request hash.
        admission_params = params if _resolved_params is None else {**params, **_resolved_params}
        from . import controller_authority
        authority = controller_authority.binding(self.db, principal)
        from .pm_source_lineage import require_document
        require_document(self.db, principal, {"target": _resolved_target or target, "params": admission_params})
        with controller_authority.use(authority, self.journal):
            binding = adef.admit(self, principal, _resolved_target or target,
                                admission_params, preconditions) if adef.admit else None
        from .pm_source_lineage import inherit
        binding = inherit(self, principal, action, _resolved_target or target, admission_params, binding)
        from .host_git import admission_bindings
        binding = admission_bindings(self, action, _resolved_target or target, admission_params, binding)
        # Admission is trusted service code. A task capability may delegate the
        # original task controller after validating that exact capability; an
        # HTTP/RPC caller cannot supply or replace this binding.
        binding = {"controller_authority": authority, **(binding or {})}
        operation_id = "op_" + uuid.uuid4().hex
        key = key if key is not None else NO_KEY_PREFIX + operation_id
        refs = {"admission_binding": binding} if binding else {}
        if _resolved_target is not None:
            refs["resolved_target"] = _resolved_target
        if _resolved_params is not None:
            refs["resolved_params"] = _resolved_params
        now = time.time()
        with self.journal.tx():
            if action in {"setup.connection.apply", "connection.reference.apply", "connection.reference.detach"}:
                from .setup_connections import _require_connection_available
                if action == "connection.reference.detach":
                    from .connection_detach import admit as admit_detach
                    admit_detach(self, principal, target, params, preconditions)
                else:
                    _require_connection_available(self, binding["principal_id"], target)
            from .pm_overview_assessments import ACTION as OVERVIEW_ACTION
            from .pm_overview_assessments import admit_operation as admit_overview
            if action == OVERVIEW_ACTION:
                # A caller may have waited behind another accepted request.
                # Original-key replay precedes the new-intent rate reservation.
                replay = self._prepare_create(principal, action=action, target=target, params=params,
                    preconditions=preconditions, idempotency_key=idempotency_key)[-1]
                if replay:
                    return replay, False
                now = admit_overview(self, principal, action, target, params, preconditions, binding)
            self.db.execute("""INSERT INTO operations(operation_id,actor,entry,idem_key,request_hash,action,target,
                params,preconditions,status,created_at,updated_at,external_refs) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                            (operation_id, principal.actor, entry[:20], key, request_hash, action,
                             _canonical(target), _canonical(params), _canonical(preconditions), "accepted", now,
                             now, _canonical(refs) if refs else None))
            from .pm_lifecycle_gates import admit_operation
            admit_operation(self.db, operation_id, action, target, binding)
            from .pm_source_lineage import bind_operation
            bind_operation(self.db, operation_id, _resolved_target or target, params, binding)
            from .pm_work_start import admit_operation as admit_work_start
            admit_work_start(self.db, operation_id, action, target, binding, now)
            from .pm_work_follow_up import admit_operation as admit_work_follow_up
            admit_work_follow_up(self.db, operation_id, action, target, params, preconditions, binding, now)
            self.journal.api_event("operation", operation_id, "operation.accepted",
                                   {"action": action, "target": target, "entry": entry}, actor=principal.actor)
        self.kick()
        return self._row(operation_id), True

    def _may_steer(self, principal: Principal, op: dict, verb: str) -> None:
        from .pm_access import require_visible
        require_visible(self.db, principal, op)
        from .pm_source_lineage import require_document
        # Saved receipts can name sources beyond the operation's admission roots.
        # Refuse before changing state or children if the public reply would be hidden.
        require_document(self.db, principal, op)
        self._authorize_controller(principal, op, verb)
        from .pm_work_adopt_and_start import authorize_child as authorize_adopt_start_child
        authorize_adopt_start_child(self, principal, op, verb)
        from .bulk_approval import authorize_child
        authorize_child(self, principal, op, verb)
        from .orchestration_operations import authorize_child as authorize_orchestration_child
        authorize_orchestration_child(self, principal, op, verb)
        adef = self.actions.get(op["action"])
        if adef and adef.authorize_existing:
            adef.authorize_existing(self, principal, op, verb)
        if not (op["actor"] == principal.actor or principal.admin or (adef and principal.allows(adef.scope))):
            raise OperationError("FORBIDDEN", f"{verb} needs the operation's own actor or its {op['action']} scope",
                                 403)

    def _authorize_controller(self, principal: Principal, op: dict, verb: str) -> None:
        """Resource control belongs to its controller, independently of admin/scopes.

        Read-only records and GitHub delivery retain their existing permissions.
        A legacy operation without creation authority may be read/reconciled by
        the worker, but cannot be claimed through resume or cancellation.
        """
        action = op["action"]
        if action in {"session.read", "session.position", "session.labels.set", "session.record_verification"}:
            return  # Connector metadata keeps its existing personal/manage authorization.
        if action == "cleanup.apply":
            from .cleanup import authorize_controller_existing
            if authorize_controller_existing(self, principal, op, verb):
                return
        if action.startswith("task.") or action == "session.send" and (op.get("target") or {}).get("task_id"):
            from .task_actions import authorize_controller_existing
            authorize_controller_existing(self, principal, op, verb)
            return
        if not (action.startswith(("session.", "worktree.", "fanout.", "cleanup."))
                or action in {"checkpoint.continue", "checkpoint.continue.revalidate",
                              "repository.continue", "integration.handoff", "work.start", "work.adopt_and_start"}):
            return
        from . import controller_authority
        saved = controller_authority.operation_binding(op)
        caller = controller_authority.binding(self.db, principal)
        if not saved or any(saved.get(k) != caller.get(k) for k in ("server_id", "controller_id")):
            raise OperationError("CONTROLLER_MISMATCH", f"{verb} requires the original resource controller", 403)

    def _running(self, operation_id: str) -> bool:
        task = self._active.get(operation_id)
        return task is not None and not task.done()

    def cancel(self, principal: Principal, operation_id: str) -> dict:
        """Stop an operation before its next step. A step that may already have run is read back first, so a
        cancelled operation never hides an action that happened."""
        op = self.get(operation_id, steps=False)
        self._may_steer(principal, op, "cancel")
        from .pm_work_follow_up import cancel as cancel_work_follow_up
        cancel_work_follow_up(self, principal, op)
        if op["status"] in TERMINAL:
            return op
        if op["action"] in {"setup.connection.apply", "connection.reference.detach"} and op["status"] == "needs_attention":
            if op["error_code"] == "CONNECTION_PROJECTION_CHANGED":
                from .setup_connections import cancel_projection_conflict
                return cancel_projection_conflict(self, principal, op)
            open_step = self.db.execute("SELECT name FROM operation_steps WHERE operation_id=? "
                "AND status IN ('started','uncertain') ORDER BY seq DESC LIMIT 1", (operation_id,)).fetchone()
            if open_step and open_step["name"] == "native_effects":
                from .setup_connections import abandon
                return abandon(self, principal, op)
        if op["action"] == "worktree.merge":
            from .worktree_merge_operations import cancel
            return cancel(self, principal, op)
        if op["action"] == "session.failover":
            from .failover_operations import cancel
            return cancel(self, principal, op)
        if op["action"] in {"session.relay", "fanout.plan", "fanout.start"}:
            from .orchestration_operations import cancel
            return cancel(self, principal, op)
        if op["action"] == "session.approve_pending":
            from .bulk_approval import cancel
            return cancel(self, principal, op)
        if op["action"] in {"pm.run.analyze", "pm.run.bat.analyze", "pm.follow_up.analyze", "pm.conversation.propose", "pm.continuation.prepare", "pm.overview.assess"}:
            from .pm_native_operations import cancel
            return cancel(self, principal, op)
        if op["action"] in {"pm.run.prepare", "pm.plan.adopt"}:
            from .pm_run_operations import cancel
            return cancel(self, principal, op)
        if op["action"] == "work.adopt_and_start":
            from .pm_work_adopt_and_start import cancel_children as cancel_adopt_start
            pending = cancel_adopt_start(self, principal, op)
            if pending is not None:
                return pending
        if op["action"] == "work.start":
            from .pm_work_start import cancel_children as cancel_work_start_children
            cancel_work_start_children(self, principal, op)
        self.db.execute("UPDATE operations SET cancel_requested=1,updated_at=? WHERE operation_id=?",
                        (time.time(), operation_id))
        if not self._running(operation_id):
            if op["status"] in {"accepted", "waiting_checks", "waiting_external"}:
                return self._transition(operation_id, "cancelled", actor=principal.actor,
                                        reason=f"cancelled by {principal.actor} before the next step")
            if op["status"] == "needs_attention":
                if op["action"] == "session.permissions":
                    from .session_permissions import complete_receipts
                    if complete_receipts(self, op):
                        # Every exact setter ACK is durable. Cancellation cannot erase those effects;
                        # schedule only the original handler's proven receipt-completion path.
                        self._transition(operation_id, "running", actor=principal.actor,
                                         reason="finishing accepted permission receipts after cancellation")
                        self.kick()
                        return self.get(operation_id, steps=False)
                open_steps = [r["name"] for r in self.db.execute(
                    "SELECT name FROM operation_steps WHERE operation_id=? AND status IN ('started','uncertain')",
                    (operation_id,))]
                note = f"; the outcome of {', '.join(open_steps)} was never proven" if open_steps else ""
                return self._transition(operation_id, "cancelled", actor=principal.actor,
                                        reason=f"cancelled by {principal.actor}{note}")
            if op["status"] == "uncertain":  # read the step back now; the run stops before any new step
                self.db.execute("UPDATE operations SET next_run_at=0 WHERE operation_id=?", (operation_id,))
        self.kick()
        return self.get(operation_id, steps=False)

    def resume(self, principal: Principal, operation_id: str) -> dict:
        """Run a needs_attention operation again: finished steps replay, an unproven step is read back (never
        re-sent), and the handler's checks run afresh."""
        op = self.get(operation_id, steps=False)
        self._may_steer(principal, op, "resume")
        from .pm_work_follow_up import resume as resume_work_follow_up
        resume_work_follow_up(self, principal, op)
        if op["action"] == "delivery.merge_and_deploy" and not (
                principal.allows("merge") and principal.allows("deploy")):
            raise OperationError("FORBIDDEN", "resuming merge-and-deploy needs both merge and deploy scopes", 403)
        if op["status"] != "needs_attention":
            raise OperationError("NOT_RESUMABLE", f"only needs_attention operations resume (this one is "
                                 f"{op['status']})", 409)
        if op["action"] in {"setup.connection.apply", "connection.reference.detach"}:
            from .connection_handoff import renew_unclaimed
            renew_unclaimed(self, op)
        op = self._transition(operation_id, "running", reason=f"resumed by {principal.actor}", uncertain_tries=0,
                              actor=principal.actor)
        if op["action"] == "session.approve_pending":
            from .bulk_approval import resume_children
            resume_children(self, principal, op)
        if op["action"] in {"session.relay", "fanout.plan", "fanout.start"}:
            from .orchestration_operations import resume_children
            resume_children(self, principal, op)
        if op["action"] == "work.adopt_and_start":
            from .pm_work_adopt_and_start import resume_children as resume_adopt_start
            resume_adopt_start(self, principal, op)
        if op["action"] == "work.start":
            from .pm_work_start import resume_children as resume_work_start_children
            resume_work_start_children(self, principal, op)
        self.kick()
        return op

    # ------------------------------------------------------------------ execution
    def kick(self) -> None:
        if self._wake is not None:
            self._wake.set()

    def wake(self, operation_id: str) -> None:
        """Make a waiting payload/provider operation due now without changing its state."""
        self.db.execute("UPDATE operations SET next_run_at=0 WHERE operation_id=? AND status='waiting_external'",
                        (operation_id,))
        self.kick()

    async def run_due(self) -> None:
        now = time.time()
        placeholders = ",".join("?" * len(RUNNABLE))
        rows = self.db.execute(f"""SELECT operation_id FROM operations WHERE status IN ({placeholders})
            AND next_run_at<=? ORDER BY created_at""", (*RUNNABLE, now)).fetchall()  # noqa: S608
        for row in rows:
            op_id = row["operation_id"]
            if not self._running(op_id):
                task = asyncio.create_task(self._execute(op_id), name=f"op-{op_id[:11]}")
                self._active[op_id] = task
                task.add_done_callback(
                    lambda t, k=op_id: self._active.pop(k, None) if self._active.get(k) is t else None)

    async def loop(self, interval_s: float = 1.0) -> None:
        self._wake = asyncio.Event()
        while True:
            try:
                await self.run_due()
            except Exception as exc:  # noqa: BLE001 - one bad row must not stop the loop
                logging.warning("operation loop error: %s", type(exc).__name__)
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(self._wake.wait(), interval_s)
            self._wake.clear()

    async def drain(self, timeout: float = 5.0) -> None:
        """Run due operations until nothing is runnable now (tests and one-shot CLI use)."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            await self.run_due()
            pending = [t for t in self._active.values() if not t.done()]
            if not pending:
                return
            await asyncio.wait(pending, timeout=max(0.01, deadline - time.monotonic()))

    async def wait(self, operation_id: str, timeout: float) -> dict:
        """Return once the operation leaves accepted/running, or after ``timeout`` seconds."""
        deadline = time.monotonic() + max(0.0, min(timeout, 60.0))
        while True:
            op = self.get(operation_id)
            if op["status"] not in {"accepted", "running"} or time.monotonic() >= deadline:
                return op
            await asyncio.sleep(0.1)

    async def _execute(self, operation_id: str) -> None:
        from .observation import event_context

        op = self._row(operation_id)
        if op is None or op["status"] in TERMINAL:
            return
        # run_due may be woken by an unrelated authenticated RPC. Task effects must use
        # their own durable admission identity, never the scheduler's inherited context.
        with event_context(actor=op["actor"], entry_point=op["entry"], operation_entry_point=op["entry"],
                           operation_id=operation_id, actor_basis="authenticated_principal",
                           actor_evidence={"source": "operations.actor", "operation_id": operation_id,
                                           "principal_ref": op["actor"]}):
            await self._execute_bound(op)

    async def _execute_bound(self, op: dict) -> None:
        operation_id = op["operation_id"]
        adef = self.actions.get(op["action"])
        if adef is None:
            self._transition(operation_id, "failed", error_code="UNKNOWN_ACTION",
                             reason=f"no handler for {op['action']} in this connector version")
            return
        if op["cancel_requested"] and op["status"] not in {"running", "uncertain"} and op["action"] not in {"worktree.merge", "session.failover", "session.approve_pending", "session.relay", "fanout.plan", "fanout.start"}:
            self._transition(operation_id, "cancelled", reason="cancelled before the next step")
            return
        if op["status"] != "running":
            op = self._transition(operation_id, "running", attempts=op["attempts"] + 1)
        ctx = OpContext(self, op)
        try:
            from . import controller_authority
            from .host_git import execution as git_execution
            from .pm_source_lineage import execution

            # Missing legacy authority is intentional: receipt recovery can
            # still read, while every new external effect fails closed.
            with (controller_authority.use(controller_authority.operation_binding(op), self.journal),
                  execution(self, operation_id), git_execution(self, operation_id)):
                result = await adef.run(ctx)
        except Cancelled:
            self._transition(operation_id, "cancelled", reason="cancelled between steps")
        except Wait as w:
            if w.refs:
                self._merge_refs(operation_id, w.refs)
            self._transition(operation_id, w.status, reason=w.reason, next_run_at=time.time() + w.delay_s,
                             uncertain_tries=0)
        except NeedsAttention as n:
            self._transition(operation_id, "needs_attention", error_code=n.code, reason=n.message)
        except Uncertain as u:
            # Its own budget: resumes from waiting_* do not use up the read-backs.
            tries = op["uncertain_tries"] + 1
            if tries > len(UNCERTAIN_RETRY_S):
                self._transition(operation_id, "uncertain", error_code="UNCERTAIN", reason=u.message,
                                 uncertain_tries=tries)
                self._transition(operation_id, "needs_attention", error_code="UNCERTAIN_UNRESOLVED",
                                 reason=f"{u.message}; read-back did not settle it after {tries} attempts")
            else:
                self._transition(operation_id, "uncertain", error_code="UNCERTAIN", reason=u.message,
                                 next_run_at=time.time() + UNCERTAIN_RETRY_S[tries - 1], uncertain_tries=tries)
        except StepFailed as f:
            self._transition(operation_id, "failed", error_code=f.code, reason=f.message)
        except (OperationError, ResourceReadOnly, WriteRefused, BatError) as e:
            self._transition(operation_id, "failed", error_code=_error_code(e), reason=redact(str(e)))
        except Exception as e:  # noqa: BLE001 - never leave a row running without an outcome
            logging.warning("operation %s handler error: %s", operation_id, type(e).__name__)
            self._transition(operation_id, "failed", error_code="INTERNAL", reason=type(e).__name__)
        else:
            self._transition(operation_id, "succeeded", result=result or {})
