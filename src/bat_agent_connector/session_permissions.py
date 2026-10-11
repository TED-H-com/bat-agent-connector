"""Fixed permission configuration, one durable step per BAT setter, no inferred ACKs."""
from __future__ import annotations

import asyncio
import json
import sqlite3
from types import SimpleNamespace

from . import confinement, registry, resource_policy, service, task_control
from .errors import BatError, ConnectionLost, InvokeTimeout, TaskControlRefused, WriteRefused
from .operations import AmbiguousOutcome, NeedsAttention, OperationError, StepFailed, Uncertain
from .safety import Audit

PLAN = "permissions.plan"
BINDING_FIELDS = ("created_at", "agent_preset", "task_id", "role", "cwd", "worktree_path", "write_scope",
                  "execution_options", "permission_mode_claude", "agent_params", "permission_operation_id", "status",
                  "controller_authority", "controller_incarnation")


class PermissionsRefused(WriteRefused):
    def __init__(self, code, message):
        self.code = code
        super().__init__(f"{code}: {message}")


def validate(params, pre):
    if set(params) != {"mode"} or not isinstance(params.get("mode"), str) or params["mode"] not in {"default", "allow_all"}:
        raise OperationError("INVALID_PARAMS", "permissions requires only mode=default|allow_all", 422)
    if set(pre) - {"control_version"} or ("control_version" in pre and
            (type(pre["control_version"]) is not int or pre["control_version"] < 0)):
        raise OperationError("INVALID_PARAMS", "control_version must be a non-negative integer", 422)


def authorize_existing(ops, principal, op, verb):
    if not principal.allows("operate"):
        raise OperationError("FORBIDDEN", f"permissions {verb} requires operate", 403)


def admit(ops, principal, target, params, pre):
    from .api_actions import _admit_session
    validate(params, pre)
    if set(target) != {"host", "session_id"}:
        raise OperationError("INVALID_TARGET", "permissions target requires host and full session_id only", 422)
    binding = _admit_session(ops, principal, target, params, pre, "permissions")
    host, sid = target["host"], target["session_id"]
    try:
        _policy(ops.context["fleet"], host, sid, params["mode"])
    except (PermissionsRefused, confinement.ConfinementRefused) as exc:
        raise OperationError(exc.code, str(exc), 403) from exc
    for row in ops.db.execute("SELECT operation_id,target,external_refs,status FROM operations WHERE action='session.permissions'"):
        refs = json.loads(row["external_refs"] or "{}")
        bound = refs.get("resolved_target") or json.loads(row["target"])
        if bound == target and row["status"] not in {"succeeded", "failed", "cancelled"}:
            raise OperationError("PERMISSIONS_PENDING", "read the existing permission operation before another change", 409)
    return binding


def _policy(fleet, host, sid, mode, options=None):
    service._guard(fleet, host, True)
    if mode == "allow_all":
        if fleet.config.host(host).default_permission_mode != "allow_all":
            raise PermissionsRefused("PERMISSIONS_HOST_POLICY", "host policy does not allow allow_all")
        confinement.guard_raise(host, sid)
    if options is not None:
        confinement.guard_permissions(host, sid, options)


def _snapshot(row):
    return {k: (row or {}).get(k) for k in BINDING_FIELDS}


def _loaded(meta, kind, *, force=False):
    if kind not in {"claude", "codex"}:
        raise PermissionsRefused("PERMISSIONS_AGENT_UNSUPPORTED", "only Claude and Codex permission configurations are supported")
    if not isinstance(meta, dict):
        raise PermissionsRefused("PERMISSIONS_UNLOADED", "session is not loaded; no permission frame was sent")
    if kind == "claude" and not force:
        if meta.get("isStreaming") is True or meta.get("streaming") is True:
            raise PermissionsRefused("PERMISSIONS_STREAMING", "Claude is streaming; wait for idle and submit a new operation key")
        if meta.get("isStreaming") is not False:
            raise PermissionsRefused("PERMISSIONS_IDLE_UNPROVEN", "positive Claude idle evidence is required")


def _step_row(ctx, name):
    return ctx.service.db.execute("SELECT * FROM operation_steps WHERE operation_id=? AND name=?",
                                  (ctx.operation_id, name)).fetchone()


def saved_plan(ctx):
    row = _step_row(ctx, PLAN)
    return json.loads(row["response"]) if row and row["status"] == "succeeded" else None


def _project_receipts(ctx, plan):
    frames = []
    for call in plan["calls"]:
        row = _step_row(ctx, call["step"])
        ack = bool(row and row["status"] == "succeeded" and json.loads(row["response"]).get("result") is True)
        frames.append({"step": call["step"], "channel": call["channel"],
                       "status": row["status"] if row else "not_started", "acknowledged": True if ack else None})
    ctx.set_refs(permission_frames=frames)


def has_dispatched_receipt(ctx):
    plan = saved_plan(ctx)
    return bool(plan and any((r := _step_row(ctx, c["step"])) is not None
                            and r["status"] in {"started", "uncertain", "succeeded"} for c in plan["calls"]))


def mark_task_uncertain(ctx, command):
    """Record exactly which unchanged task state this command made uncertain."""
    journal = ctx.service.journal
    with journal.tx():
        journal.command_status(command["command_id"], "uncertain")
        try:
            task = task_control.check_binding(ctx)
        except TaskControlRefused:
            return
        if not task or task["paused"] or task["state"] not in {"accepted", "running", "waiting_permission"}:
            return
        old_state = task["state"]
        changed = journal.change(task["task_id"], "uncertain", event="permissions_uncertain")
        ctx.set_refs(permission_task_uncertainty={"command_id": command["command_id"], "task_id": task["task_id"],
            "control_version": task["control_version"], "previous_state": old_state, "updated_at": changed["updated_at"]})


def _restore_own_uncertainty(ctx, command):
    refs = ctx.service._row(ctx.operation_id)["external_refs"] or {}
    proof = refs.get("permission_task_uncertainty") or {}
    try:
        task = task_control.check_binding(ctx)
    except TaskControlRefused:
        return
    owner = registry.get((ctx.admission_binding or {}).get("host"), command["session_id"]) or {}
    if (task and owner.get("task_id") == task["task_id"] and owner.get("role") == "lead"
            and not task["paused"] and task["state"] == "uncertain"
            and proof.get("command_id") == command["command_id"] and proof.get("task_id") == task["task_id"]
            and proof.get("control_version") == task["control_version"] and proof.get("updated_at") == task["updated_at"]
            and proof.get("previous_state") in {"accepted", "running", "waiting_permission"}
            and not any(c["command_id"] != command["command_id"] and c["status"] in {"intent", "needs_review", "uncertain"}
                        for c in ctx.service.journal.commands(task["task_id"]))):
        ctx.service.journal.change(task["task_id"], proof["previous_state"], event="permissions_receipts_recovered")


def recover_unsent_command(ctx, plan):
    command = _command(ctx, plan)
    if not command:
        return
    # Every existing frame has a positive ACK; missing step intents prove later frames were not dispatched.
    if any((row := _step_row(ctx, c["step"])) is not None and row["status"] != "succeeded" for c in plan["calls"]):
        return
    with ctx.service.journal.tx():
        _restore_own_uncertainty(ctx, command)
        task = task_control.check_binding(ctx)
        if task and task["state"] != "uncertain" and command["status"] == "uncertain":
            ctx.service.journal.command_status(command["command_id"], "intent")


def _calls_proven(ctx, plan):
    return all((r := _step_row(ctx, c["step"])) is not None and r["status"] == "succeeded"
               and json.loads(r["request"]) == {"channel": c["channel"], "params": c["params"]}
               and json.loads(r["response"]).get("result") is True for c in plan["calls"])


def complete_receipts(ops, op):
    """A cancelled needs_attention operation may only finish already-proven local bookkeeping."""
    from .operations import OpContext
    ctx = OpContext(ops, op)
    plan = saved_plan(ctx)
    if not plan or not _calls_proven(ctx, plan):
        return False
    try:
        _command(ctx, plan)
    except NeedsAttention:
        return False
    return True


def _check_owner(ctx, fleet, host, sid):
    expected = (ctx.admission_binding or {}).get("task_id")
    if task_control.owner_task(fleet, host, sid) != expected:
        raise TaskControlRefused("TASK_BINDING_MISMATCH", "permission task owner changed since admission")


def _command(ctx, plan):
    refs = ctx.service._row(ctx.operation_id)["external_refs"] or {}
    cid = refs.get("command_id")
    if not cid:
        if (ctx.admission_binding or {}).get("task_id"):
            raise NeedsAttention("TASK_COMMAND_MISSING", "permission operation lacks its original command receipt")
        return None
    command = ctx.service.journal.command_get(cid)
    payload = json.loads(command["payload"])
    binding = ctx.admission_binding or {}
    if (command["kind"] != "permissions" or command["session_id"] != plan["session_id"]
            or command["task_id"] != binding.get("task_id") or payload.get("operation_id") != ctx.operation_id
            or payload.get("mode") != plan["mode"] or payload.get("control_version") != binding.get("control_version")):
        raise NeedsAttention("TASK_BINDING_MISMATCH", "permission receipts do not bind the original task command")
    return command


def _finish(ctx, plan):
    from .orchestrate import registry_permission_fields
    if not _calls_proven(ctx, plan):
        raise NeedsAttention("PERMISSIONS_ACK_UNPROVEN", "every fixed permission frame needs its own true ACK")
    command = _command(ctx, plan)
    if command:
        def settle():
            # Restore only this command's exact uncertainty marker; preserve later state/incarnation.
            _restore_own_uncertainty(ctx, command)
            ctx.service.journal.command_status(command["command_id"], "settled")
            return ctx.service.journal.command_get(command["command_id"])
        ctx.effect("permissions.command_result", settle, refs=task_control.command_refs, receipt_only=True)

    def project():
        binding = ctx.admission_binding
        if binding:
            try:
                task_control.check_binding(ctx)
            except TaskControlRefused:
                return {"updated": False, "reason": "task_binding_changed"}
        fields = {"permission_raise_pending": None, "execution_options": plan["options"],
                  **registry_permission_fields(plan["options"])}
        return registry.project_permissions(plan["host"], plan["session_id"], plan["registry_before"],
                                            ctx.operation_id, fields)
    projection = ctx.effect("permissions.registry", project, receipt_only=True)
    return {"host": plan["host"], "session_id": plan["session_id"], "agent_kind": plan["agent_kind"],
            "mode": plan["mode"], "calls": [{"channel": c["channel"], "result": True} for c in plan["calls"]],
            "registry_projection": projection, "note": "BAT accepted the permission configuration; live SDK/OS enforcement is not proven"}


async def run(ctx):
    try:
        return await _run(ctx)
    except (OSError, sqlite3.Error) as exc:
        raise Uncertain("permissions.local", "permission receipts need local bookkeeping recovery") from exc


async def _run(ctx):
    from . import lifecycle
    from .api_actions import _bound_inputs, check_controller_binding
    task_control.replay_command_refs(ctx)
    plan = saved_plan(ctx)
    if plan:
        _project_receipts(ctx, plan)
        # Restore recorded outcomes before consulting live policy. Never send from this recovery loop.
        async def no_send():
            raise AssertionError("existing permission step must not dispatch")
        for call in plan["calls"]:
            if _step_row(ctx, call["step"]) is None:
                break
            await ctx.step(call["step"], no_send, request={"channel": call["channel"], "params": call["params"]})
        if _calls_proven(ctx, plan):
            return _finish(ctx, plan)
        recover_unsent_command(ctx, plan)
    target, params = _bound_inputs(ctx)
    if not plan and (ctx.service._row(ctx.operation_id)["external_refs"] or {}).get("command_id"):
        # The fixed plan is committed before any setter intent; no plan proves no setter was dispatched.
        recover_unsent_command(ctx, {"session_id": target["session_id"], "mode": params["mode"], "calls": []})
    task_control.check_binding(ctx)
    check_controller_binding(ctx, target)
    _check_owner(ctx, ctx.service.context["fleet"], target["host"], target["session_id"])
    await lifecycle.session_set_permissions(ctx.service.context["fleet"], target["host"], target["session_id"],
        params["mode"], confirm=True, control_version=ctx.effective_preconditions.get("control_version"),
        operation_id=ctx.operation_id, _exact_session_id=True, _operation_context=ctx)
    return _finish(ctx, saved_plan(ctx))


async def execute(ctx, fleet, host, sid, mode, guard):
    """Called inside the original TaskCoordinator command and host write lock boundary."""
    from . import lifecycle
    async with service._write_lock(host):
        terminal, _ = await service._resolve_session(fleet.client(host), sid)
        if terminal["id"] != sid:
            raise TaskControlRefused("TASK_BINDING_MISMATCH", "permission session no longer resolves exactly")
        client = fleet.client(host)
        grant = await resource_policy.authorize_session(fleet, host, "session.permissions", terminal)
        _check_owner(ctx, fleet, host, sid)
        plan = saved_plan(ctx)
        from .bulk_approval import check_child
        if bulk_item := check_child(ctx):
            if service.agent_kind(terminal.get("agentPreset")) != bulk_item["agent_kind"]:
                raise TaskControlRefused("BULK_BINDING_CHANGED", "reviewed permission agent kind changed")
        if plan is None:
            kind = service.agent_kind(terminal.get("agentPreset"))
            meta = await service._meta(client, sid)
            _loaded(meta, kind)
            _policy(fleet, host, sid, mode)
            options, calls = lifecycle.permission_configuration(host, sid, kind, mode)
            plan = {"host": host, "session_id": sid, "mode": mode, "agent_kind": kind, "options": options,
                    "registry_before": _snapshot(registry.get(host, sid)),
                    "runtime_identity": {k: meta.get(k) for k in ("sdkSessionId", "cwd")},
                    "calls": [{"step": "permissions." + name, "channel": channel, "params": params}
                              for name, channel, params in calls]}
            plan = ctx.effect(PLAN, lambda: plan)
        if plan["agent_kind"] != service.agent_kind(terminal.get("agentPreset")):
            raise TaskControlRefused("TASK_BINDING_MISMATCH", "permission agent kind changed")
        audit = Audit(fleet.config.safety)
        rate_checked = False
        audit_base = {"actor": ctx.op["actor"], "tool": "session_set_permissions", "host": host,
                      "session_id": sid + "#perm", "operation_id": ctx.operation_id}
        for call in plan["calls"]:
            async def send(call=call):
                nonlocal rate_checked
                live = None
                sent = False

                def check():
                    from .bulk_approval import check_child
                    check_child(ctx)
                    _check_owner(ctx, fleet, host, sid)
                    if guard:
                        guard.check()
                    _policy(fleet, host, sid, mode, plan["options"])
                    if _snapshot(registry.get(host, sid)) != plan["registry_before"]:
                        raise TaskControlRefused("TASK_BINDING_MISMATCH", "permission registry identity or policy changed")
                    from .cleanup import guard as cleanup_guard
                    cleanup_guard(host, session_id=sid, path=terminal.get("cwd") or terminal.get("worktreePath"))
                    cls = resource_policy.classify(fleet.config.host(host), sid, terminal=terminal,
                                                   entries=registry.list_entries(host))
                    refusal = resource_policy._decide(cls, resource_policy.BY_ACTION["session.permissions"], live)
                    if refusal:
                        raise PermissionsRefused(*refusal)

                async def before_frame():
                    nonlocal live
                    check()
                    async def read(channel, params):
                        result = await client.guard_read(channel, params)
                        if channel == "claude:get-session-meta":
                            _loaded(result, plan["agent_kind"])
                            from .bulk_approval import check_child, check_meta
                            if bulk_item := check_child(ctx):
                                check_meta(bulk_item, result)
                            if any(result.get(k) != value for k, value in plan["runtime_identity"].items() if value is not None):
                                raise TaskControlRefused("TASK_BINDING_MISMATCH", "permission runtime identity changed")
                            if result.get("agentPreset") and service.agent_kind(result["agentPreset"]) != plan["agent_kind"]:
                                raise TaskControlRefused("TASK_BINDING_MISMATCH", "permission runtime agent kind changed")
                        return result
                    cls = resource_policy.classify(fleet.config.host(host), sid, terminal=terminal,
                                                   entries=registry.list_entries(host))
                    reader = SimpleNamespace(host=client.host, invoke=read)
                    live = await resource_policy.live_check(reader, cls, worktree=False, folder=True)
                    check()

                def transported():
                    nonlocal sent, rate_checked
                    if not rate_checked:
                        audit.check_rate(host, sid + "#perm", permission_operation_id=ctx.operation_id)
                        rate_checked = True
                    audit.record(**audit_base, channel=call["channel"], phase="attempt", mode=mode)
                    sent = True
                    if guard:
                        guard.frames.append(True)
                try:
                    result = await client.invoke(call["channel"], call["params"], grant=grant,
                        before_frame=before_frame, before_send=check, on_transport=transported)
                except (BatError, OSError, asyncio.TimeoutError) as exc:
                    if sent:
                        audit.record(**audit_base, channel=call["channel"], phase="result", ok=False,
                                     error="permission ACK unproven")
                        raise AmbiguousOutcome("permission frame was sent without a proven ACK") from exc
                    if isinstance(exc, ConnectionLost | InvokeTimeout | OSError | asyncio.TimeoutError):
                        raise StepFailed("PERMISSIONS_NOT_SENT", "permission frame was not sent; submit a new operation key after connectivity recovers") from exc
                    raise
                audit.record(**audit_base, channel=call["channel"], phase="result", ok=result is True)
                if result is not True:
                    raise AmbiguousOutcome("permission frame did not return a literal true ACK")
                return {"channel": call["channel"], "result": True}
            try:
                await ctx.step(call["step"], send, request={"channel": call["channel"], "params": call["params"]})
            finally:
                _project_receipts(ctx, plan)
    return {"host": host, "session_id": sid, "mode": mode}
