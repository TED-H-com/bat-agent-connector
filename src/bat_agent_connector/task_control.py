"""Task authority at the shared service layer, including the last BAT-frame boundary."""

from __future__ import annotations

import asyncio
import contextlib
import functools
import hashlib
import inspect
import json
import sqlite3
from dataclasses import dataclass, field

from . import controller_authority, registry, resource_policy
from .errors import TaskControlRefused, WriteRefused

RUNTIME_KINDS = {"send", "answer", "interrupt", "permissions", "failover"}

FAILOVER_CALLBACKS = ("before_handoff_send", "verify_handoff_successor", "verify_handoff_at_frame",
                      "before_handoff_invoke", "handoff_frame_guard")


@dataclass(frozen=True, init=False, eq=False)
class TaskFailoverAuthority:
    """An internal capability issued by the coordinator for its reserved handoff."""

    task_id: str
    host: str
    session_id: str
    successor_session_id: str
    failover_command_id: str
    handoff_command_id: str
    handoff_message_id: str
    control_version: int
    before_handoff_send: object
    verify_handoff_successor: object
    verify_handoff_at_frame: object
    before_handoff_invoke: object
    handoff_frame_guard: object
    _issuer: object = field(repr=False)

    def __init__(self, *args, **kwargs):
        raise TypeError("only the TaskCoordinator can issue failover authority")

    def valid_for(self, fleet, host, sid, owner_id):
        issuer = getattr(self, "_issuer", None)
        return (issuer is not None and issuer is getattr(fleet, "task_coordinator", None)
                and self in issuer._failover_authorities
                and self.task_id == owner_id and self.host == host and self.session_id == sid
                and all(callable(getattr(self, name, None)) for name in FAILOVER_CALLBACKS)
                and issuer._valid_failover_authority(self))

    def check(self):
        task = self._issuer.journal.get(self.task_id)
        bound = {**task, "host": self.host, "session_id": self.session_id,
                 "control_version": self.control_version}
        check_incarnation(self._issuer.journal, bound)


def command_refs(command):
    return {"task_id": command["task_id"], "command_id": command["command_id"],
            "control_version": json.loads(command["payload"]).get("control_version")}


def replay_command_refs(ctx):
    """Repair old command receipts before an outer read-back or an early result/refusal replay."""
    for name in ("task_command", "task_send_command"):
        row = ctx.service.db.execute(
            "SELECT response FROM operation_steps WHERE operation_id=? AND name=? AND status='succeeded'",
            (ctx.operation_id, name)).fetchone()
        if row:
            ctx.effect(name, lambda row=row: json.loads(row["response"]), refs=command_refs)


def authority(task):
    """The original accepted task authority, including raw SQL rows used by cleanup."""
    value = task.get("controller_authority")
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return None
    return value if isinstance(value, dict) else None


def require_authority(task):
    return controller_authority.require_binding(authority(task))


def admission_binding(task, *, session=False, role="lead"):
    binding = {"task_id": task["task_id"], "control_version": task["control_version"],
               "controller_authority": authority(task)}
    if session:
        field = "reviewer_session_id" if role == "reviewer" else "session_id"
        binding.update(host=task["host"], session_id=task.get(field), role=role)
        entry = registry.get(task["host"], task.get(field)) if task.get(field) else None
        if entry and entry.get("controller_incarnation"):
            binding["session_controller"] = {"host": task["host"], "session_id": task[field],
                                              "controller_incarnation": entry["controller_incarnation"]}
    return binding


def check_binding(ctx):
    binding = ctx.admission_binding
    if not binding or "task_id" not in binding:
        return None
    task = ctx.service.journal.get(binding["task_id"])
    require_authority(task)
    if task["control_version"] != binding["control_version"]:
        raise TaskControlRefused("CONTROL_VERSION_CONFLICT", "task control_version changed since admission")
    if "session_id" in binding:
        sid = binding["session_id"]
        role = binding["role"]
        field = "reviewer_session_id" if role == "reviewer" else "session_id"
        if task["host"] != binding["host"] or task.get(field) != sid:
            raise TaskControlRefused("TASK_BINDING_MISMATCH", "task session changed since admission")
        if sid:
            expected = binding.get("session_controller") or {}
            if (expected.get("host") != task["host"] or expected.get("session_id") != sid
                    or not expected.get("controller_incarnation")):
                raise TaskControlRefused("CONTROLLER_BINDING_UNPROVEN", "task operation has no fixed session creation")
            controller_authority.require_entry(registry.get(task["host"], sid),
                                               expected_incarnation=expected["controller_incarnation"])
    return task


def check_incarnation(journal, task):
    """Fence a coordinator tick's awaited work before its local or external effects."""
    if getattr(journal, "owner_valid", None) and not journal.owner_valid():
        raise TaskControlRefused("TASK_OWNER_UNAVAILABLE", "fleet owner lease is no longer held")
    current = journal.get(task["task_id"])
    if any(current.get(key) != task.get(key) for key in ("host", "session_id", "reviewer_session_id")):
        raise TaskControlRefused("TASK_BINDING_MISMATCH", "task session changed during tick")
    if current["control_version"] != task["control_version"]:
        raise TaskControlRefused("CONTROL_VERSION_CONFLICT", "task control changed during tick")
    if current["paused"]:
        raise TaskControlRefused("TASK_PAUSED", "task paused during tick")
    return current


def start_guard(journal, task, session_id, role):
    """Bind a pre-ACK start to its original incarnation and durable command, when present.

    The task's current session binding changes only after ACK, so runtime FrameGuard
    cannot guard starts. Older internal adapter callers may lack a command; the
    coordinator always supplies one, and recovery must keep that original intent.
    """
    command = next((c for c in journal.commands(task["task_id"])
                    if c["kind"] == "start_" + role and c["session_id"] == session_id), None)

    def guard():
        require_authority(task)
        current = check_incarnation(journal, task)
        if command:
            row = journal.command_get(command["command_id"])
            controller_authority.require_binding(authority(row))
            payload = json.loads(row["payload"])
            if (row["kind"] != "start_" + role or row["task_id"] != task["task_id"]
                    or row["session_id"] != session_id
                    or row["status"] not in {"intent", "needs_review", "uncertain", "rejected"}):
                raise TaskControlRefused("TASK_COMMAND_PENDING", "start intent is no longer dispatchable")
            if payload.get("control_version", task["control_version"]) != current["control_version"]:
                raise TaskControlRefused("CONTROL_VERSION_CONFLICT", "start belongs to an earlier task version")
        owner = registry.get(task["host"], session_id)
        if owner and (owner.get("task_id") != task["task_id"] or owner.get("role") != role):
            raise TaskControlRefused("TASK_BINDING_MISMATCH", "reserved start ownership changed")

    return guard


def owner_task(fleet, host, sid):
    owner = registry.get(host, sid) or {}
    if owner.get("task_id"):
        return owner["task_id"]
    coordinator = getattr(fleet, "task_coordinator", None)
    def find(db):
        row = db.execute("SELECT task_id FROM tasks WHERE host=? AND (session_id=? OR reviewer_session_id=?) "
                         "ORDER BY submitted_at DESC LIMIT 1", (host, sid, sid)).fetchone()
        if not row:
            row = db.execute("SELECT c.task_id FROM commands c JOIN tasks t ON t.task_id=c.task_id "
                             "WHERE t.host=? AND c.session_id=? AND c.kind LIKE 'start_%' "
                             "UNION SELECT b.task_id FROM branches b JOIN tasks t ON t.task_id=b.task_id "
                             "WHERE t.host=? AND b.session_id=? LIMIT 1", (host, sid, host, sid)).fetchone()
        return row[0] if row else None
    if coordinator:
        return find(coordinator.journal.db)
    from .service import task_service_db
    path = task_service_db()
    if path:
        try:
            with contextlib.closing(sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=0.2)) as db:
                return find(db)
        except sqlite3.Error:
            pass
    return None


def check(journal, task_id: str, host: str, sid: str, action: str, version=None, *, command_id=None):
    try:
        task = journal.get(task_id)
    except (ValueError, KeyError):
        raise TaskControlRefused("TASK_OWNER_UNAVAILABLE", "task owner row is unavailable") from None
    require_authority(task)
    owner = registry.get(host, sid)
    controller_authority.require_entry(owner)
    if task["host"] != host or not owner or owner.get("task_id") != task_id:
        raise TaskControlRefused("TASK_BINDING_MISMATCH", "task session ownership does not match")
    if task.get("session_id") != sid or owner.get("role") != "lead":
        raise TaskControlRefused("TASK_STATE_BLOCKED", "only the task's current lead accepts external control")
    if version is not None and (type(version) is not int or version != task["control_version"]):
        raise TaskControlRefused("CONTROL_VERSION_CONFLICT", "task control_version changed")
    if task["paused"]:
        raise TaskControlRefused("TASK_PAUSED", "task is paused")
    if task["state"] == "verifying":
        raise TaskControlRefused("TASK_VERIFYING", "task is verifying")
    pending = [c for c in journal.commands(task_id) if c["command_id"] != command_id
               and c["status"] in {"intent", "needs_review", "uncertain"}
               and (c["kind"] in RUNTIME_KINDS or c["kind"].startswith("start_"))]
    if pending:
        raise TaskControlRefused("TASK_COMMAND_PENDING", "task has a command requiring reconciliation")
    if task["state"] == "uncertain":
        raise TaskControlRefused("TASK_RECONCILIATION_REQUIRED", "task needs reconciliation")
    if (task["state"] not in {"accepted", "running", "waiting_permission"}
            or task["state"] == "waiting_permission" and action == "send"):
        raise TaskControlRefused("TASK_STATE_BLOCKED", "task state does not allow this control")
    return task


@dataclass(frozen=True)
class FrameGuard:
    journal: object
    task_id: str
    host: str
    session_id: str
    version: int
    command_id: str | None = None
    action: str = "send"
    internal: bool = False
    abort: bool = False
    prompt_sha256: str | None = None
    frames: list = field(default_factory=list, compare=False)
    controller_incarnation: str | None = None

    def __post_init__(self):
        owner = registry.get(self.host, self.session_id) or {}
        object.__setattr__(self, "controller_incarnation", owner.get("controller_incarnation"))

    def __call__(self):
        # Only the command's effect frames count; preliminary writes use check() directly.
        self.check()
        self.frames.append(True)

    def check(self):
        if getattr(self.journal, "owner_valid", None) and not self.journal.owner_valid():
            raise TaskControlRefused("TASK_OWNER_UNAVAILABLE", "fleet owner lease is no longer held")
        task = self.journal.get(self.task_id)
        require_authority(task)
        owner = registry.get(self.host, self.session_id)
        if not self.controller_incarnation:
            raise TaskControlRefused("CONTROLLER_BINDING_UNPROVEN", "task frame has no original session creation")
        controller_authority.require_entry(owner, expected_incarnation=self.controller_incarnation)
        if (task["host"] != self.host or self.session_id not in
                {task.get("session_id"), task.get("reviewer_session_id")}
                or not owner or owner.get("task_id") != self.task_id
                or owner.get("role") != ("reviewer" if self.session_id == task.get("reviewer_session_id") else "lead")):
            raise TaskControlRefused("TASK_BINDING_MISMATCH", "task session binding changed before BAT frame")
        if task["control_version"] != self.version:
            raise TaskControlRefused("CONTROL_VERSION_CONFLICT", "task control_version changed before BAT frame")
        if self.abort:
            if not task["paused"]:
                raise TaskControlRefused("CONTROL_VERSION_CONFLICT", "pause no longer owns the abort")
            return
        if task["paused"]:
            raise TaskControlRefused("TASK_PAUSED", "task paused before BAT frame")
        if self.internal and self.action == "verify":
            if task["state"] not in {"accepted", "running", "verifying"}:
                raise TaskControlRefused("TASK_STATE_BLOCKED", "task no longer allows trusted verification")
            return
        command = self.journal.command_get(self.command_id) if self.command_id else None
        if command:
            controller_authority.require_binding(authority(command))
            original = json.loads(command["payload"]).get("controller_incarnation")
            if not original:
                raise TaskControlRefused("CONTROLLER_BINDING_UNPROVEN", "task command has no original session creation")
            controller_authority.require_entry(owner, expected_incarnation=original)
        if (not command or command["kind"] != self.action or command["task_id"] != self.task_id or command["session_id"] != self.session_id
                or command["status"] not in {"intent", "needs_review"}):
            raise TaskControlRefused("TASK_COMMAND_PENDING", "frame has no dispatchable task command")
        if self.action == "send" and (not self.prompt_sha256 or
                json.loads(command["payload"]).get("prompt_sha256") != self.prompt_sha256):
            raise TaskControlRefused("TASK_BINDING_MISMATCH", "task command does not bind this prompt")
        if self.internal:
            # Reviewer/rework sends are allowed only by the original coordinator's journaled intent.
            if command["kind"] != "send" or task["state"] not in {"accepted", "running", "verifying", "dispatching"}:
                raise TaskControlRefused("TASK_STATE_BLOCKED", "internal task command is no longer dispatchable")
        else:
            check(self.journal, self.task_id, self.host, self.session_id, self.action,
                  self.version, command_id=self.command_id)


def guarded(action):
    """Route task-owned legacy calls to the owner before taking the host write lock."""
    def decorate(fn):
        signature = inspect.signature(fn)

        @functools.wraps(fn)
        async def wrapper(*args, **kwargs):
            from . import service
            bound = signature.bind(*args, **kwargs)
            bound.apply_defaults()
            values = bound.arguments
            fleet, host, sid = values["fleet"], values["host"], values["session_id"]
            guard = values.get("_task_guard")
            if guard is not None:
                if (not isinstance(guard, FrameGuard) or guard.host != host or guard.session_id != sid
                        or guard.action != action):
                    raise TaskControlRefused("TASK_BINDING_MISMATCH", "invalid task frame authority")
                if action == "send" and hashlib.sha256(str(values.get("text", "")).encode()).hexdigest() != guard.prompt_sha256:
                    raise TaskControlRefused("TASK_BINDING_MISMATCH", "task frame authority binds a different prompt")
                guard.check()
                return await fn(*args, **kwargs)
            service._guard(fleet, host, values["confirm"])
            if action == "permissions" and values["mode"] == "allow_all" and fleet.config.host(host).default_permission_mode != "allow_all":
                raise WriteRefused(f"host {host!r} does not allow raising sessions to allow-all "
                                   '(set default_permission_mode = "allow_all" in its config)')
            tab, _ = await service._resolve_session(fleet.client(host), sid)
            if values.get("_exact_session_id") and tab["id"] != sid:
                raise TaskControlRefused("TASK_BINDING_MISMATCH", "the operation's session identity no longer resolves exactly")
            sid = tab["id"]
            await resource_policy.authorize_session(fleet, host, "session." + action, tab)
            task_id = owner_task(fleet, host, sid)
            if task_id and values.get("_before_frame") is not None:
                raise TaskControlRefused("TASK_OWNED", "standalone operation cannot adopt a task-owned session")
            if not task_id:
                return await fn(*args, **kwargs)
            coordinator = getattr(fleet, "task_coordinator", None)
            params = {k: v for k, v in values.items() if k not in
                      {"fleet", "host", "session_id", "_task_guard", "before_invoke", "initial_task_send"}}
            if coordinator:
                return await coordinator.session_control(task_id, host, sid, action, params, fn)
            # A second client observes the canonical journal, then enters the existing owner over RPC.
            db = service.task_service_db()
            if db is None:
                raise TaskControlRefused("TASK_OWNER_UNAVAILABLE", "task-owned session state is unavailable (owner pointer missing)")
            try:
                with contextlib.closing(sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=0.2)) as conn:
                    conn.row_factory = sqlite3.Row
                    class Reader:
                        def get(self, tid):
                            row = conn.execute("SELECT * FROM tasks WHERE task_id=?", (tid,)).fetchone()
                            if row is None:
                                raise ValueError("missing task")
                            return dict(row)

                        def commands(self, tid):
                            return [dict(r) for r in conn.execute("SELECT * FROM commands WHERE task_id=?", (tid,))]
                    task = check(Reader(), task_id, host, sid, action, params.get("control_version"))
                pointer = json.loads((registry.registry_path().parent / service.TASK_SERVICE_POINTER).read_text())
                import os

                from .task_daemon import request
                token = os.environ.get("BATC_API_TOKEN")
                if not token:
                    raise TaskControlRefused("CONTROLLER_AUTHORITY_REQUIRED", "forwarding requires the original caller API token")
                fields = {"send": {"text", "message_id", "queue"},
                          "answer": {"answers", "permission", "deny_message", "tool_use_id", "dont_ask_again"},
                          "interrupt": {"mode"}, "permissions": {"mode"}}[action]
                forwarded = {k: params[k] for k in fields if k in params}
                method = "session_set_permissions" if action == "permissions" else "session_" + action
                return await asyncio.to_thread(request, method, timeout=60,
                                               _url=pointer.get("endpoint"), _auth_token=token,
                                               host=host, session_id=sid, confirm=True,
                                               control_version=task["control_version"], **forwarded)
            except TaskControlRefused:
                raise
            except (OSError, ValueError, sqlite3.Error) as exc:
                code = str(exc).split(":", 1)[0]
                if code.startswith(("TASK_", "CONTROL_VERSION_", "CONTROLLER_", "FOREIGN_CONTROLLER")):
                    raise TaskControlRefused(code, "task owner refused the control") from None
                raise TaskControlRefused("TASK_OWNER_UNAVAILABLE", "task owner cannot be reached") from None
        return wrapper
    return decorate


def refuse_owned(fleet, host, sid):
    if owner_task(fleet, host, sid):
        raise TaskControlRefused("TASK_OWNED_CONTROL_REQUIRED", "use the Task Service for this owned session")
