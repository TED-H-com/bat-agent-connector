"""Durable external testimony; never a Task Service observed-verifier authority."""
from __future__ import annotations

import time

from . import lifecycle, registry, service, task_control, verification
from .errors import BatError, TaskControlRefused, WriteRefused
from .operations import RERUN, ActionDef, OperationError, StepFailed
from .safety import Audit

ACTION = "session.record_verification"
BINDING = ("created_at", "task_id", "role", "status", "cwd", "worktree_path", "branch",
           "origin_cwd", "agent_preset")
TAB = ("id", "workspaceId", "cwd", "worktreePath", "worktreeBranch", "agentPreset")


def install(ops):
    if ACTION not in ops.actions:
        ops.register(ActionDef(ACTION, "operate", "Record external clean-candidate testimony",
                               run, admit, ("host", "session_id"), authorize_existing))


def validate(target, params, pre):
    if (set(target) != {"host", "session_id"}
            or any(not isinstance(target.get(k), str) or not target[k].strip() for k in target)):
        raise OperationError("INVALID_TARGET", "host and full session_id are required", 422)
    if set(params) != verification.FIELDS or pre:
        raise OperationError("INVALID_PARAMS", "exact verification fields and no preconditions are required", 422)
    try:
        verification.validate(**params)
    except WriteRefused as exc:
        raise OperationError("INVALID_PARAMS", str(exc), 422) from None


def authorize_existing(ops, principal, op, verb):
    if not principal.allows("operate"):
        raise OperationError("FORBIDDEN", f"verification {verb} requires operate", 403)


def _binding(host, sid):
    row = registry.get(host, sid)
    return {k: row.get(k) for k in BINDING} if row is not None else None


def _policy(ops, target):
    fleet, host, sid = ops.context["fleet"], target["host"], target["session_id"]
    if host not in fleet.config.hosts:
        raise OperationError("UNKNOWN_HOST", "host is not configured", 404)
    if not fleet.writes_enabled(host) or not fleet.orchestrate_enabled(host):
        raise OperationError("TIER_DISABLED", "verification recording requires writes and orchestrate", 403)
    task_control.refuse_owned(fleet, host, sid)
    if getattr(ops.journal, "owner_valid", None) and not ops.journal.owner_valid():
        raise TaskControlRefused("TASK_OWNER_UNAVAILABLE", "central owner lease is no longer held")


def admit(ops, principal, target, params, pre):
    validate(target, params, pre)
    _policy(ops, target)
    verification._document(verification.path())
    return {"verification_registry": _binding(target["host"], target["session_id"])}


def _target(ctx):
    return (ctx.op.get("external_refs") or {}).get("resolved_target") or ctx.target


def _guard(ctx):
    ctx.check_cancel()
    target = _target(ctx)
    _policy(ctx.service, target)
    if _binding(target["host"], target["session_id"]) != ctx.admission_binding["verification_registry"]:
        raise StepFailed("VERIFICATION_BINDING_CHANGED", "session incarnation changed since admission")


async def _source(ctx):
    _guard(ctx)
    target = _target(ctx)
    client = ctx.service.context["fleet"].client(target["host"])
    terminal, _ = await service._resolve_session(client, target["session_id"])
    if terminal.get("id") != target["session_id"]:
        raise StepFailed("VERIFICATION_BINDING_CHANGED", "full exact session identity is required")
    cwd = terminal.get("worktreePath") or terminal.get("cwd")
    if not isinstance(cwd, str) or not cwd or "\0" in cwd:
        raise StepFailed("VERIFICATION_SOURCE_UNAVAILABLE", "source cwd is unavailable")
    head = await lifecycle._candidate_head(client, cwd)
    if not head or head != ctx.params["candidate_commit"].lower():
        raise StepFailed("VERIFICATION_HEAD_CHANGED", "candidate_commit is not the source's current HEAD")
    root = await client.invoke("git:getRoot", {"cwd": cwd})
    if not isinstance(root, str) or not root:
        raise StepFailed("VERIFICATION_SOURCE_UNAVAILABLE", "source Git root is unavailable")
    dirty = await client.invoke("git:status", {"cwd": cwd})
    # BAT maps some Git failures to []; this preserves its reported-clean legacy
    # testimony check, not independently observed verifier/process evidence.
    if dirty != []:
        raise StepFailed("VERIFICATION_NOT_CLEAN", "BAT must report an empty candidate status")
    if await lifecycle._candidate_head(client, cwd) != head:
        raise StepFailed("VERIFICATION_HEAD_CHANGED", "candidate changed during clean-state observation")
    _guard(ctx)
    return {"terminal": {k: terminal.get(k) for k in TAB}, "cwd": cwd, "root": root, "head": head}


async def run(ctx):
    async def observe():
        source = await _source(ctx)
        target = _target(ctx)
        row = {**target, **ctx.params, "candidate_commit": source["head"],
               "actor": ctx.actor, "recorded_at": time.time()}
        return {"source": source, "record": row}

    async def reread(_):
        return RERUN  # Only source reads; no testimony has been recorded by this step.

    plan = await ctx.step("verification.observe", observe, reconcile=reread)

    async def record():
        # The first run also permits recovery of an already durable exact receipt.
        saved = verification.operation_record(ctx.operation_id, plan["record"])
        if saved is not None:
            return saved
        try:
            current = await _source(ctx)
        except TaskControlRefused:
            raise
        except (BatError, OSError) as exc:
            raise StepFailed("VERIFICATION_SOURCE_UNAVAILABLE", "source read failed before the local record") from exc
        if current != plan["source"]:
            raise StepFailed("VERIFICATION_BINDING_CHANGED", "source binding changed since original observation")
        # Registry adoption cannot race the final local write. No await inside this fence.
        with registry._locked(registry.registry_path()):
            _guard(ctx)
            row = verification.record(**plan["record"], operation_id=ctx.operation_id)
        fleet = ctx.service.context["fleet"]
        Audit(fleet.config.safety).record_after_effect(effect_outcome="accepted", actor=ctx.actor, tool=ACTION, host=row["host"],
            session_id=row["session_id"], phase="record", operation_id=ctx.operation_id,
            candidate_commit=row["candidate_commit"], exit_code=row["exit_code"])
        return row

    async def reconcile(_):
        # An absent/malformed receipt is not permission to rewrite the current latest testimony.
        return verification.operation_record(ctx.operation_id, plan["record"])

    row = await ctx.step("verification.record", record, request={"record": plan["record"]}, reconcile=reconcile)
    return {**row, "verified_candidate": row["exit_code"] == 0}


async def legacy(ops, principal, request, *, entry):
    if set(request) - (verification.FIELDS | {"host", "session_id", "confirm", "idempotency_key"}):
        raise OperationError("INVALID_REQUEST", "unknown verification argument", 422)
    if request.get("confirm") is not True:
        raise OperationError("CONFIRM_REQUIRED", "verification requires confirm=true", 403)
    target = {k: request.get(k) for k in ("host", "session_id")}
    params = {k: request[k] for k in verification.FIELDS if k in request}
    validate(target, params, {})
    intent = {"action": ACTION, "target": target, "params": params,
              "idempotency_key": request.get("idempotency_key")}
    *_, op = ops._prepare_create(principal, **intent, _legacy_session=True)
    if op is None:
        _policy(ops, target)
        terminal, _ = await service._resolve_session(ops.context["fleet"].client(target["host"]), target["session_id"])
        op, _ = ops.create(principal, **intent, _legacy_session=True, entry=entry,
                           _resolved_target={"host": target["host"], "session_id": terminal["id"]})
    await ops.run_due()
    op = await ops.wait(op["operation_id"], 30)
    target = (op.get("external_refs") or {}).get("resolved_target") or op["target"]
    return {**target, "verified_candidate": None, **(op.get("result") or {}),
            "operation_id": op["operation_id"], "operation_status": op["status"],
            "operation_error_code": op["error_code"], "operation_status_reason": op["status_reason"],
            "idempotency_key": op["idempotency_key"], "idempotency_enabled": op["idempotency_enabled"]}
