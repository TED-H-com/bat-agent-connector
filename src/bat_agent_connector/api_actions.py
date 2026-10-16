"""Operation handlers for /api/v1, MCP and CLI. Each wraps an existing connector function.

Session actions refuse sessions created in BAT twice: at admission (registry and inventory facts, so the caller gets
403 and no operation is stored) and again inside the call (the live resource policy check in service.py).
"""

from __future__ import annotations

import asyncio
import hashlib

from . import registry, resource_policy, service, session_permissions, task_control
from .api_auth import Principal
from .errors import ConnectionLost, InvokeTimeout, TaskControlRefused
from .operations import ActionDef, NeedsAttention, OpContext, OperationError, OperationService, StepFailed


def _fleet(ops: OperationService):
    return ops.context["fleet"]


def _admit_session(ops: OperationService, principal: Principal, target: dict, params: dict, pre: dict,
                   action: str = "send", *, _command_id: str | None = None) -> dict | None:
    if not all(isinstance(target.get(k), str) and target[k].strip() for k in ("host", "session_id")):
        raise OperationError("INVALID_TARGET", "host and session_id must be non-empty strings", 422)
    fleet = _fleet(ops)
    host, sid = target["host"], target["session_id"]
    if host not in fleet.config.hosts:
        raise OperationError("UNKNOWN_HOST", f"unknown host {host!r}", 404)
    if not fleet.writes_enabled(host):
        raise OperationError("TIER_DISABLED", f"the write tier is off for host {host}", 403)
    inventory = ops.context.get("inventory")
    row = inventory.get_session(host, sid) if inventory else None
    has_tab = bool(row and row.get("has_tab"))
    if row is None and registry.get(host, sid) is None:
        # Not observed yet and never created by the connector: nothing proves it is writable.
        raise OperationError("UNKNOWN_READ_ONLY", "the session is not in the inventory or the connector registry",
                             403)
    verdict = resource_policy.classify_row_for_read(fleet.config.host(host), sid, has_tab=has_tab,
                                                    entries=registry.list_entries(host))
    if verdict["api_access"] != "managed":
        raise OperationError(verdict.get("read_only_code", "READ_ONLY"),
                             f"session {sid[:8]} is {verdict['provenance']} and read-only through the API", 403)
    from . import controller_authority
    from .errors import ResourceReadOnly
    try:
        owner = controller_authority.require_entry(registry.get(host, sid))
    except ResourceReadOnly as exc:
        raise OperationError(exc.code, str(exc), 403) from None
    controller_binding = {"session_controller": {
        "host": host, "session_id": sid, "controller_incarnation": owner["controller_incarnation"]}}
    task_id = task_control.owner_task(fleet, host, sid)
    if task_id:
        coordinator = ops.context.get("coordinator")
        if coordinator is None:
            raise OperationError("TASK_OWNER_UNAVAILABLE", "task coordinator unavailable", 409)
        try:
            task = task_control.check(coordinator.journal, task_id, host, sid,
                                      action, pre.get("control_version"), command_id=_command_id)
        except TaskControlRefused as exc:
            raise OperationError(exc.code, str(exc), 409) from None
        return {**controller_binding, **task_control.admission_binding(task, session=True)}
    return controller_binding


def check_controller_binding(ctx, target):
    """An admitted session ID must still name the same creation incarnation."""
    from . import controller_authority
    expected = (ctx.admission_binding or {}).get("session_controller")
    if (not expected or expected.get("host") != target.get("host")
            or expected.get("session_id") != target.get("session_id")):
        raise OperationError("CONTROLLER_BINDING_UNPROVEN", "the operation has no fixed session creation authority", 403)
    ctx.require_controller()
    controller_authority.require_entry(registry.get(target["host"], target["session_id"]),
                                      expected_incarnation=expected["controller_incarnation"])
    return expected["controller_incarnation"]


def _authorize_send(ops: OperationService, principal: Principal, op: dict, verb: str) -> None:
    """Work-target sends authorize the workspace writer. Direct session sends are unchanged."""
    from . import pm_work_follow_up
    pm_work_follow_up.authorize_existing(ops, principal, op, verb)


def _admit_send(ops: OperationService, principal: Principal, target: dict, params: dict, pre: dict) -> dict | None:
    if "workspace_id" in target or "work_item_id" in target:
        from . import pm_work_follow_up
        return pm_work_follow_up.admit_send(ops, principal, target, params, pre)
    if target.get("task_id"):
        from .task_actions import admit_send
        return admit_send(ops, principal, target, params, pre)
    if not all(isinstance(target.get(k), str) and target[k] for k in SESSION_TARGET):
        raise OperationError("INVALID_TARGET", "host and session_id are required", 422)
    _validate_send(params)
    return _admit_session(ops, principal, target, params, pre)


def _validate_send(params):
    text = params.get("text")
    if not isinstance(text, str) or not text.strip() or len(text) > service.MAX_PROMPT_CHARS:
        raise OperationError("INVALID_PARAMS", f"text must be 1-{service.MAX_PROMPT_CHARS} characters", 422)
    if "queue" in params and type(params["queue"]) is not bool:
        raise OperationError("INVALID_PARAMS", "queue must be a boolean", 422)
    if "message_id" in params and (not isinstance(params["message_id"], str) or not params["message_id"].strip()):
        raise OperationError("INVALID_PARAMS", "message_id must be a non-empty string", 422)


def _bound_inputs(ctx):
    refs = ctx.op.get("external_refs") or {}
    return refs.get("resolved_target") or ctx.target, {**ctx.params, **(refs.get("resolved_params") or {})}


async def _send(ctx: OpContext) -> dict:
    task_control.replay_command_refs(ctx)
    if ctx.target.get("task_id"):
        from .task_actions import send
        return await send(ctx)
    fleet = _fleet(ctx.service)
    target, params = _bound_inputs(ctx)
    host, sid = target["host"], target["session_id"]
    text = params["text"]
    mid = params.get("message_id") or "batc-" + ctx.operation_id

    async def send() -> dict:
        task_control.check_binding(ctx)
        incarnation = check_controller_binding(ctx, target)
        from .orchestration_operations import linked
        ctx.relay_send_transported = False
        try:
            return await service.session_send(fleet, host, sid, text, confirm=True, message_id=mid,
                queue=params.get("queue", False), tool="api:" + ctx.actor,
                retry_on_disconnect=False, control_version=ctx.effective_preconditions.get("control_version"),
                operation_id=ctx.operation_id, _exact_session_id=True,
                _controller_incarnation=incarnation,
                **({"_operation_context": ctx} if linked(ctx.op) else {}))
        except (ConnectionLost, InvokeTimeout, OSError, asyncio.TimeoutError) as exc:
            if linked(ctx.op) and not ctx.relay_send_transported:
                raise StepFailed("RELAY_NOT_SENT", "relay prompt was not sent; inspect connectivity before a new request") from exc
            raise

    async def reconcile(_request: dict) -> dict | None:
        turn = registry.get_turn(host, sid, mid)
        if turn and "message_id" not in params:  # operation-generated IDs cannot name a different earlier prompt
            return {"host": host, "session_id": sid, "message_id": mid, "accepted": True,
                    "queued": turn.get("queued"), "turn_marker": mid, "settled_by": "accepted_turn_record"}
        # The reply may have been lost after BAT took the frame: BAT echoes a Claude prompt as a user message
        # whose id is the clientMessageId. Codex ignores that id, so a lost Codex reply stays unproven.
        c = fleet.client(host)
        kind = service.agent_kind((registry.get(host, sid) or {}).get("agent_preset"))
        if kind == "codex":
            return None
        state = await service._live_state(c, sid, kind, await service._meta(c, sid))
        if any(isinstance(m, dict) and m.get("id") == mid and m.get("role") == "user"
               and ("message_id" not in params or m.get("content") == text)
               for m in (state or {}).get("messages") or []):
            return {"host": host, "session_id": sid, "message_id": mid, "accepted": True,
                    "turn_marker": mid, "settled_by": "bat_transcript"}
        return None

    r = await ctx.step("send", send, request={"message_id": mid,
                                              "text_sha256": hashlib.sha256(text.encode()).hexdigest()},
                       reconcile=reconcile)
    if not r.get("accepted"):
        raise NeedsAttention("NOT_ACCEPTED", "BAT did not accept the message")
    return r


def _admit_answer(ops: OperationService, principal: Principal, target: dict, params: dict, pre: dict) -> dict | None:
    # The exact prompt is required: without it an answer could land on whatever prompt is pending next, and a
    # read-back could not tell this prompt from another one.
    _validate_answer(params, require_prompt=True)
    return _admit_session(ops, principal, target, params, pre, "answer")


def _validate_answer(params, *, require_prompt):
    if require_prompt and "tool_use_id" not in params:
        raise OperationError("INVALID_PARAMS", "tool_use_id (the pending prompt's toolUseId) is required", 422)
    if "tool_use_id" in params and (not isinstance(params["tool_use_id"], str) or not params["tool_use_id"].strip()):
        raise OperationError("INVALID_PARAMS", "tool_use_id must be a non-empty string", 422)
    if ("answers" in params) == ("permission" in params):
        raise OperationError("INVALID_PARAMS", "pass exactly one of answers or permission", 422)
    if "permission" in params and (not isinstance(params["permission"], str) or params["permission"] not in {"allow", "deny"}):
        raise OperationError("INVALID_PARAMS", "permission must be allow or deny", 422)
    if "answers" in params:
        answers = params["answers"]
        if not ((isinstance(answers, list) and all(isinstance(v, str) for v in answers)) or
                (isinstance(answers, dict) and all(isinstance(k, str) and isinstance(v, str) for k, v in answers.items()))):
            raise OperationError("INVALID_PARAMS", "answers must be a list of strings or a string-to-string object", 422)
    if "dont_ask_again" in params and type(params["dont_ask_again"]) is not bool:
        raise OperationError("INVALID_PARAMS", "dont_ask_again must be a boolean", 422)
    if "deny_message" in params and not isinstance(params["deny_message"], str):
        raise OperationError("INVALID_PARAMS", "deny_message must be a string", 422)


async def _answer(ctx: OpContext) -> dict:
    task_control.replay_command_refs(ctx)
    fleet = _fleet(ctx.service)
    target, p = _bound_inputs(ctx)
    host, sid = target["host"], target["session_id"]

    async def answer() -> dict:
        from .bulk_approval import linked
        task_control.check_binding(ctx)
        incarnation = check_controller_binding(ctx, target)
        ctx.bulk_answer_transported = False
        try:
            return await service.session_answer(fleet, host, sid, confirm=True, answers=p.get("answers"),
                                                permission=p.get("permission"), deny_message=p.get("deny_message"),
                                                tool_use_id=p.get("tool_use_id"),
                                                dont_ask_again=p.get("dont_ask_again", False),
                                                control_version=ctx.effective_preconditions.get("control_version"),
                                                operation_id=ctx.operation_id, _exact_session_id=True,
                                                _controller_incarnation=incarnation,
                                                _operation_context=ctx)
        except (ConnectionLost, InvokeTimeout, OSError, asyncio.TimeoutError) as exc:
            if linked(ctx.op) and not ctx.bulk_answer_transported:
                # Includes the legacy decorator's initial resolution/authorization reads.
                # A process interruption is deliberately excluded: its intent remains unknown.
                raise StepFailed("BULK_NOT_SENT", "bulk answer was not sent; review connectivity before a new request") from exc
            raise

    async def reconcile(request: dict) -> dict | None:
        from .bulk_approval import linked
        if linked(ctx.op):
            return None  # A cleared prompt cannot prove the reviewed allow was accepted.
        # The answer cleared the pending prompt if BAT no longer shows that tool use as pending.
        c = fleet.client(host)
        kind = service.agent_kind((registry.get(host, sid) or {}).get("agent_preset"))
        state = await service._live_state(c, sid, kind, await service._meta(c, sid))
        field = "pendingAskUser" if "answers" in p else "pendingPermission"
        if not isinstance(state, dict) or field not in state:
            return None
        pending = state[field]
        if pending is None or (isinstance(pending, dict) and isinstance(pending.get("toolUseId"), str)
                               and pending["toolUseId"] and pending["toolUseId"] != request.get("tool_use_id")):
            return {"host": host, "session_id": sid, "tool_use_id": request.get("tool_use_id"),
                    "result": None, "settled_by": "pending_prompt_cleared"}
        return None

    return await ctx.step("answer", answer, request={"tool_use_id": p.get("tool_use_id")}, reconcile=reconcile)


def _admit_interrupt(ops: OperationService, principal: Principal, target: dict, params: dict, pre: dict) -> dict | None:
    mode = params.get("mode", "soft")
    if not isinstance(mode, str) or mode not in {"soft", "hard"}:
        raise OperationError("INVALID_PARAMS", "mode must be soft or hard", 422)
    return _admit_session(ops, principal, target, params, pre, "interrupt")


async def _interrupt(ctx: OpContext) -> dict:
    task_control.replay_command_refs(ctx)
    fleet = _fleet(ctx.service)
    target = (ctx.op.get("external_refs") or {}).get("resolved_target") or ctx.target
    host, sid = target["host"], target["session_id"]

    async def interrupt() -> dict:
        task_control.check_binding(ctx)
        incarnation = check_controller_binding(ctx, target)
        r = await service.session_interrupt(fleet, host, sid, ctx.params.get("mode", "soft"), confirm=True,
                                            control_version=ctx.effective_preconditions.get("control_version"),
                                            operation_id=ctx.operation_id, _exact_session_id=True,
                                            _controller_incarnation=incarnation)
        return r

    async def reconcile(_request: dict) -> dict | None:
        meta = await fleet.client(host).invoke("claude:get-session-meta", {"sessionId": sid})
        if isinstance(meta, dict) and meta.get("isStreaming") is False:
            return {"host": host, "session_id": sid, "mode": ctx.params.get("mode", "soft"),
                    "channel": None, "result": None, "note": None, "settled_by": "session_not_streaming"}
        return None

    return await ctx.step("interrupt", interrupt, reconcile=reconcile)


LEGACY_SESSION_METHODS = {
    "session_send": "session.send", "session_continue": "session.send",
    "session_answer": "session.answer", "session_interrupt": "session.interrupt",
    "session_set_permissions": "session.permissions",
}


async def legacy_session_control(ops: OperationService, principal: Principal, method: str,
                                 request: dict, *, entry: str) -> dict:
    """Compatibility inputs share canonical actions; resolved facts never replace the caller's intent hash."""
    action = LEGACY_SESSION_METHODS[method]
    fields = {"session.send": {"text", "message_id", "queue"},
              "session.answer": {"answers", "permission", "deny_message", "tool_use_id", "dont_ask_again"},
              "session.interrupt": {"mode"}, "session.permissions": {"mode"}}[action]
    allowed = {"host", "session_id", "confirm", "idempotency_key", "control_version"} | fields
    if set(request) - allowed:
        raise OperationError("INVALID_REQUEST", "unknown session control arguments", 422)
    if request.get("confirm") is not True:
        raise OperationError("CONFIRM_REQUIRED", f"{method} requires confirm=true", 403)
    if not all(isinstance(request.get(k), str) and request[k].strip() for k in SESSION_TARGET):
        raise OperationError("INVALID_TARGET", "host and session_id must be non-empty strings", 422)
    if "control_version" in request and (type(request["control_version"]) is not int or request["control_version"] < 0):
        raise OperationError("INVALID_PARAMS", "control_version must be a non-negative integer", 422)
    params = {k: request[k] for k in fields if k in request and
              (request[k] is not None or k in {"text", "queue", "dont_ask_again", "mode"})}
    if method == "session_continue":
        params.setdefault("text", "continue")
    if action == "session.send":
        _validate_send(params)
    elif action == "session.answer":
        _validate_answer(params, require_prompt=False)
    elif action == "session.permissions":
        from .session_permissions import validate
        params.setdefault("mode", "allow_all")
        validate(params, {"control_version": request["control_version"]} if "control_version" in request else {})
    else:
        params.setdefault("mode", "soft")
        if not isinstance(params["mode"], str) or params["mode"] not in {"soft", "hard"}:
            raise OperationError("INVALID_PARAMS", "mode must be soft or hard", 422)
    # Legacy aliases omit optional false defaults; replay with a raw canonical request still requires
    # this exact envelope, not a differently spelled (even semantically equivalent) set of params.
    for key in ("queue", "dont_ask_again"):
        if params.get(key) is False:
            params.pop(key)
    intent = {"action": action, "target": {k: request[k] for k in SESSION_TARGET}, "params": params,
              "preconditions": ({"control_version": request["control_version"]}
                                if "control_version" in request else {}),
              "idempotency_key": request.get("idempotency_key")}
    *_, op = ops._prepare_create(principal, **intent, _legacy_session=True)
    if op is None:
        fleet = _fleet(ops)
        host = intent["target"]["host"]
        if host not in fleet.config.hosts:
            raise OperationError("UNKNOWN_HOST", f"unknown host {host!r}", 404)
        if not fleet.writes_enabled(host):
            raise OperationError("TIER_DISABLED", f"the write tier is off for host {host}", 403)
        client = fleet.client(host)
        terminal, _ = await service._resolve_session(client, intent["target"]["session_id"])
        from . import controller_authority
        with controller_authority.use(controller_authority.binding(ops.db, principal), ops.journal):
            await resource_policy.authorize_session(fleet, host, action, terminal)
        resolved_params = None
        if action == "session.answer" and "tool_use_id" not in params:
            sid = terminal["id"]
            meta = await service._meta(client, sid)
            if not service._state_safe(service.agent_kind(terminal.get("agentPreset")), meta):
                raise OperationError("PENDING_UNAVAILABLE", "loaded pending prompt evidence is required", 409)
            state = await client.invoke("claude:get-session-state", {"sessionId": sid})
            field = "pendingAskUser" if "answers" in params else "pendingPermission"
            other = "pendingPermission" if field == "pendingAskUser" else "pendingAskUser"
            prompt = state.get(field) if isinstance(state, dict) else None
            if (not isinstance(prompt, dict) or not isinstance(prompt.get("toolUseId"), str)
                    or not prompt["toolUseId"].strip() or state.get(other) is not None):
                raise OperationError("PENDING_UNAVAILABLE", "one unambiguous pending prompt of the requested kind is required", 409)
            resolved_params = {"tool_use_id": prompt["toolUseId"]}
        # After awaits, repeat replay and atomically save original intent plus exact resolved evidence.
        op, _ = ops.create(principal, **intent, entry=entry, _legacy_session=True,
                           _resolved_target={"host": host, "session_id": terminal["id"]},
                           _resolved_params=resolved_params)
    await ops.run_due()
    op = await ops.wait(op["operation_id"], 30)
    refs = op.get("external_refs") or {}
    target = refs.get("resolved_target") or op["target"]
    effective = {**op["params"], **(refs.get("resolved_params") or {})}
    if action == "session.send":
        result = {k: None for k in ("accepted", "queued", "turn_marker", "turn_phase", "turn_attribution",
                                    "marker_source", "after_ms", "after", "resumed", "note")}
        result["message_id"] = effective.get("message_id") or "batc-" + op["operation_id"]
    elif action == "session.answer":
        result = {"channel": None, "result": None, "tool_use_id": effective.get("tool_use_id"),
                  "questions": None, "answered": None, "permission": effective.get("permission"), "permission_tool": None}
    elif action == "session.permissions":
        result = {"mode": effective["mode"], "agent_kind": None, "calls": None, "note": None}
    else:
        result = {"mode": effective.get("mode", "soft"), "channel": None, "result": None, "note": None}
    return {"host": target["host"], "session_id": target["session_id"], **result, **(op.get("result") or {}),
            "operation_id": op["operation_id"], "operation_status": op["status"],
            "operation_error_code": op["error_code"], "operation_status_reason": op["status_reason"],
            "idempotency_key": op["idempotency_key"],
            "idempotency_enabled": op["idempotency_enabled"]}


async def legacy_interrupt(ops: OperationService, principal: Principal, request: dict, *, entry: str) -> dict:
    return await legacy_session_control(ops, principal, "session_interrupt", request, entry=entry)


SESSION_TARGET = ("host", "session_id")
ACTIONS = [
    ActionDef("session.permissions", "operate", "Set managed session permission configuration",
              session_permissions.run, session_permissions.admit, SESSION_TARGET,
              authorize_existing=session_permissions.authorize_existing),
    ActionDef("session.send", "operate", "Send a message to a connector-managed session",
              _send, _admit_send, authorize_existing=_authorize_send),
    ActionDef("session.answer", "operate", "Answer a managed session's pending question or permission prompt",
              _answer, _admit_answer, SESSION_TARGET),
    ActionDef("session.interrupt", "operate", "Interrupt a managed session's running turn",
              _interrupt, _admit_interrupt, SESSION_TARGET),
]
