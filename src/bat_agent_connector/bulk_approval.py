"""Reviewed permission batches, owned by the existing operations and their child receipts."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
import uuid

from . import (
    api_auth,
    confinement,
    controller_authority,
    dashboard_sync,
    registry,
    resource_policy,
    service,
    session_permissions,
    task_control,
)
from .errors import BatError, TaskControlRefused
from .operations import ActionDef, Cancelled, NeedsAttention, OperationError, Wait

ACTION = "session.approve_pending"
TTL = 600
MAX_ITEMS = 50
MAX_TOKEN = 98304
MAX_PREVIEW = 262144
MIN_PARENT_WAIT_S = 1.0
MAX_PARENT_WAIT_S = 30.0
REGISTRY_FIELDS = (*session_permissions.BINDING_FIELDS, 'controller_authority', 'controller_incarnation')


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def b64(value):
    return base64.urlsafe_b64encode(value).decode().rstrip("=")


def install(ops, admin_token):
    ops.context["bulk_approval_key"] = hmac.digest(admin_token.encode(), b"batc.bulk-approval.v1", "sha256")
    if ACTION not in ops.actions:
        ops.register(ActionDef(ACTION, "operate", "Approve a fixed reviewed set of managed permission prompts",
                               run, admit, ("host",), authorize_existing=authorize_existing))


def identity(ops, principal):
    if not principal.credential_id:
        raise OperationError("FORBIDDEN", "bulk approval needs an authenticated API credential", 403)
    proof = {"scope": dashboard_sync.identity(ops.journal, principal), "credential": principal.credential_id}
    return hmac.digest(ops.context["bulk_approval_key"], canonical(proof).encode(), "sha256").hex()


def require_scopes(principal):
    if not all(principal.allows(s) for s in ("observe", "operate")):
        raise OperationError("FORBIDDEN", "bulk approval requires observe and operate", 403)


def record(host, sid):
    row = registry.get(host, sid) or {}
    return {key: row.get(key) for key in REGISTRY_FIELDS}


def code(exc):
    return getattr(exc, "code", "BULK_ITEM_UNAVAILABLE")


def prompt(state):
    p = state.get("pendingPermission") if isinstance(state, dict) else None
    if (not isinstance(p, dict) or not isinstance(p.get("toolUseId"), str)
            or not 1 <= len(p["toolUseId"]) <= 256):
        raise TaskControlRefused("BULK_PROMPT_CHANGED", "the reviewed permission prompt is no longer pending")
    if state.get("pendingAskUser"):
        raise TaskControlRefused("BULK_PENDING_AMBIGUOUS", "inspect this session's pending prompts individually")
    if len(canonical(p).encode()) > 8192:
        raise TaskControlRefused("BULK_PROMPT_TOO_LARGE", "inspect this permission prompt individually")
    return p


def static(ops, item, *, mode=None, command_id=None):
    from .api_actions import _admit_session
    from .cleanup import guard as cleanup_guard
    from .orchestration_operations import require_record, task_binding
    fleet = ops.context["fleet"]
    host, sid = item["host"], item["session_id"]
    authority = controller_authority.require()
    require_record(host, sid, item["registry"])
    cleanup_guard(host, session_id=sid, path=item["registry"].get("worktree_path") or item["registry"].get("cwd"))
    if record(host, sid) != item["registry"]:
        raise TaskControlRefused("BULK_BINDING_CHANGED", "reviewed session creation, ownership or policy changed")
    owner = task_control.owner_task(fleet, host, sid)
    expected = item.get("task_binding")
    if owner != (expected or {}).get("task_id"):
        raise TaskControlRefused("TASK_BINDING_MISMATCH", "reviewed session task owner changed")
    pre = {"control_version": expected["control_version"]} if expected else {}
    p = api_auth.Principal(authority['actor'], frozenset(authority['scopes']), admin=authority['admin'])
    actual = _admit_session(ops, p, {"host": host, "session_id": sid}, {}, pre,
                            "permissions" if mode is not None else "answer", _command_id=command_id)
    if task_binding(actual) != task_binding(expected):
        raise TaskControlRefused("TASK_BINDING_MISMATCH", "reviewed task incarnation changed")
    confinement.guard_answer(host, sid, item["tool_name"], dont_ask_again=True, allow=True)
    if mode is not None:
        session_permissions._policy(fleet, host, sid, mode)


async def preview(ops, principal, request):
    with controller_authority.use(controller_authority.binding(ops.db, principal), ops.journal):
        return await _preview(ops, principal, request)


async def _preview(ops, principal, request):
    if not principal.allows("observe"):
        raise OperationError("FORBIDDEN", "approval preview requires observe", 403)
    who = identity(ops, principal)
    if (not isinstance(request, dict) or set(request) - {"host", "workspace"}
            or not isinstance(request.get("host"), str)
            or request["host"] not in ops.context["fleet"].config.hosts
            or (request.get("workspace") is not None and not isinstance(request["workspace"], str))):
        raise OperationError("INVALID_PARAMS", "approval preview needs configured host and optional workspace", 422)
    host, workspace = request["host"], request.get("workspace")
    fleet, hc = ops.context["fleet"], ops.context["fleet"].config.host(host)
    client = fleet.client(host)
    ws = await service._workspace(client)
    workspace_ids = {w.get("id") for w in ws.get("workspaces", []) if workspace in {w.get("id"), w.get("name")}}
    candidates = sorted((t for t in ws.get("terminals", []) if t.get("agentPreset")
                         and (workspace is None or t.get("workspaceId") in workspace_ids)), key=lambda t: t["id"])
    items, rows = [], []
    for terminal in candidates[:MAX_ITEMS]:
        sid = terminal["id"]
        item_id = "bapi_" + digest({"host": host, "sid": sid})[:24]
        row = {"item_id": item_id, "host": host, "session_id": sid, "eligible": False}
        try:
            controller_authority.require_entry(registry.get(host, sid))
            cls = resource_policy.classify(hc, sid, terminal=terminal, entries=registry.list_entries(host))
            if cls.code:
                raise TaskControlRefused(cls.code, cls.reason)
            meta = await service._meta(client, sid)
            kind = service.agent_kind(terminal.get("agentPreset"))
            if kind not in {"claude", "codex"} or not service._state_safe(kind, meta):
                raise TaskControlRefused("BULK_RUNTIME_UNPROVEN", "loaded Claude or Codex identity is required")
            state = await client.invoke("claude:get-session-state", {"sessionId": sid})
            if isinstance(state, dict) and state.get("pendingPermission") is None:
                continue
            pending = prompt(state)
            owner = task_control.owner_task(fleet, host, sid)
            binding = (task_control.admission_binding(ops.journal.get(owner), session=True) if owner else None)
            item = {"item_id": item_id, "host": host, "session_id": sid, "agent_kind": kind,
                    "registry": record(host, sid), "task_binding": binding,
                    "runtime": {k: meta.get(k) for k in ("sdkSessionId", "cwd")},
                    "tool_use_id": pending["toolUseId"], "tool_name": pending.get("toolName"),
                    "prompt_fingerprint": digest(pending)}
            static(ops, item)
            live = await resource_policy.live_check(client, cls, worktree=False, folder=True)
            if refusal := resource_policy._decide(cls, resource_policy.BY_ACTION["session.answer"], live):
                raise TaskControlRefused(*refusal)
            static(ops, item)
            modes, refusals = [None], {}
            for mode in ("default", "allow_all"):
                try:
                    static(ops, item, mode=mode)
                except (BatError, OperationError) as exc:
                    refusals[mode] = code(exc)
                else:
                    modes.append(mode)
            item["allowed_modes"] = modes
            row.update(eligible=True, prompt=pending, prompt_fingerprint=item["prompt_fingerprint"],
                       agent_kind=kind, task_binding=binding, allowed_modes=modes, mode_refusals=refusals)
            items.append(item)
        except (BatError, OperationError, OSError) as exc:
            row.update(code=code(exc), reason="This session is not eligible for this reviewed batch")
        rows.append(row)
    document = {"host": host, "workspace": workspace, "items": items,
                "answer": {"permission": "allow", "dont_ask_again": True}}
    fingerprint = digest(document)
    now = time.time()
    claims = {"document": document, "fingerprint": fingerprint, "principal": who, "actor": principal.actor,
              "iat": now, "exp": now + TTL}
    raw = canonical(claims).encode()
    token = "bap1." + b64(raw) + "." + b64(hmac.digest(ops.context["bulk_approval_key"], b"bap1." + raw, "sha256"))
    result = {"host": host, "workspace": workspace, "preview_id": "bapv_" + hashlib.sha256(raw).hexdigest()[:32],
              "preview_token": token, "fingerprint": fingerprint, "issued_at": now, "expires_at": now + TTL,
              "items": rows, "answer": document["answer"], "truncated": len(candidates) > MAX_ITEMS,
              "notice": "Each selected prompt is allowed with dont_ask_again=true; mode changes are optional and separately recorded"}
    if len(token) > MAX_TOKEN or len(canonical(result).encode()) > MAX_PREVIEW:
        raise OperationError("BULK_PREVIEW_TOO_LARGE", "narrow the workspace selection and preview again", 409)
    return result


def decode(ops, token):
    try:
        if not isinstance(token, str) or len(token) > MAX_TOKEN:
            raise ValueError()
        version, raw, signature = token.split(".")
        body = base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4))
        expected = b64(hmac.digest(ops.context["bulk_approval_key"], b"bap1." + body, "sha256"))
        claims = json.loads(body)
        if (version != "bap1" or raw != b64(body) or not hmac.compare_digest(signature, expected)
                or canonical(claims).encode() != body or claims["exp"] != claims["iat"] + TTL
                or digest(claims["document"]) != claims["fingerprint"]):
            raise ValueError()
    except (ValueError, KeyError, TypeError, AttributeError):
        raise OperationError("BULK_PREVIEW_INVALID", "use the original bounded approval preview", 409) from None
    if claims["exp"] <= time.time():
        raise OperationError("BULK_PREVIEW_EXPIRED", "approval preview expired; review again", 409)
    return claims


def admit(ops, principal, target, params, pre):
    require_scopes(principal)
    if (set(target) != {"host"} or set(params) != {"preview_token", "selection"}
            or set(pre) != {"expected_fingerprint"}):
        raise OperationError("BULK_PREVIEW_REQUIRED", "use approval-previews and submit an explicit reviewed selection", 422)
    claims = decode(ops, params["preview_token"])
    if (claims["actor"] != principal.actor or claims["principal"] != identity(ops, principal)
            or claims["document"]["host"] != target["host"] or claims["fingerprint"] != pre["expected_fingerprint"]):
        raise OperationError("BULK_PREVIEW_MISMATCH", "preview caller, host or fingerprint changed", 409)
    selection = params["selection"]
    if not isinstance(selection, list) or not 1 <= len(selection) <= MAX_ITEMS:
        raise OperationError("INVALID_PARAMS", "select 1-50 reviewed items explicitly", 422)
    available = {item["item_id"]: item for item in claims["document"]["items"]}
    chosen, seen = [], set()
    for value in selection:
        if (not isinstance(value, dict) or set(value) != {"item_id", "mode"}
                or not isinstance(value["item_id"], str) or value["item_id"] in seen
                or value["item_id"] not in available or value["mode"] not in (None, "default", "allow_all")):
            raise OperationError("INVALID_PARAMS", "selection needs unique reviewed item IDs and explicit modes", 422)
        seen.add(value["item_id"])
        item = available[value["item_id"]]
        if value["mode"] not in item["allowed_modes"]:
            raise OperationError("BULK_MODE_REFUSED", "the reviewed item does not permit this mode", 403)
        try:
            static(ops, item, mode=value["mode"])
        except (BatError, OperationError) as exc:
            raise OperationError(code(exc), "reviewed item is no longer eligible", 409) from exc
        chosen.append({**item, "mode": value["mode"]})
    return {"bulk_approval": {"items": chosen, "principal": claims["principal"],
                             "fingerprint": claims["fingerprint"], "accepted_scopes": ["observe", "operate"]}}


def binding(op):
    return ((op.get("external_refs") or {}).get("admission_binding") or {}).get("bulk_approval")


def authorize_existing(ops, principal, op, verb):
    require_scopes(principal)
    if op["actor"] != principal.actor or binding(op)["principal"] != identity(ops, principal):
        raise OperationError("FORBIDDEN", f"bulk {verb} requires the original authenticated credential", 403)


def linked(op):
    return (op.get("external_refs") or {}).get("bulk_approval_child")


def authorize_child(ops, principal, op, verb):
    link = linked(op)
    if not link:
        return
    require_scopes(principal)
    parent = ops._row(link["parent_id"])
    if not parent or parent["action"] != ACTION or principal.actor != parent["actor"]:
        raise OperationError("FORBIDDEN", "bulk children retain their original caller and combined scopes", 403)
    if verb == "resume" and parent["cancel_requested"]:
        raise OperationError("BULK_CANCEL_REQUESTED", "a cancelled batch cannot resume unsent child work", 409)


def step_name(item, phase):
    return "bulk." + item["item_id"] + "." + phase


def receipt(ops, parent_id, name):
    row = ops.db.execute("SELECT status,response FROM operation_steps WHERE operation_id=? AND name=?",
                         (parent_id, name)).fetchone()
    return json.loads(row["response"]) if row and row["status"] == "succeeded" else None


def child_binding(ctx, *, allow_cancel=False):
    from .orchestration_operations import require_child_authority, task_binding
    link = linked(ctx.op)
    if not link:
        return None
    parent = ctx.service._row(link["parent_id"])
    if not parent or parent["action"] != ACTION or parent["actor"] != ctx.actor:
        raise TaskControlRefused("BULK_BINDING_CHANGED", "bulk child has no original parent")
    item = next((i for i in binding(parent)["items"] if i["item_id"] == link["item_id"]), None)
    saved = receipt(ctx.service, parent["operation_id"], step_name(item, link["phase"])) if item else None
    phase = link["phase"]
    action = "session.answer" if phase == "answer" else "session.permissions"
    expected = ({"permission": "allow", "dont_ask_again": True, "tool_use_id": item["tool_use_id"]}
                if phase == "answer" and item else {"mode": (item or {}).get("mode")})
    if (phase not in {"answer", "permissions"} or not saved or saved.get("operation_id") != ctx.operation_id
            or ctx.op["action"] != action or ctx.target != {"host": item["host"], "session_id": item["session_id"]}
            or ctx.params != expected or task_binding(ctx.admission_binding) != task_binding(item["task_binding"])):
        raise TaskControlRefused("BULK_BINDING_CHANGED", "bulk child does not match its fixed reviewed receipt")
    require_child_authority(ctx, parent)
    if not allow_cancel and (parent["cancel_requested"] or parent["status"] in {"failed", "cancelled"}):
        raise TaskControlRefused("BULK_CANCEL_REQUESTED", "batch cancelled before this frame")
    return item


def check_child(ctx):
    item = child_binding(ctx)
    if item:
        refs = ctx.service._row(ctx.operation_id)["external_refs"] or {}
        static(ctx.service, item, mode=item["mode"] if linked(ctx.op)["phase"] == "permissions" else None,
               command_id=refs.get("command_id"))
    return item


def check_meta(item, meta):
    if (not service._state_safe(item["agent_kind"], meta) or any(meta.get(k) != v for k, v in item["runtime"].items() if v is not None)
            or (meta.get("agentPreset") and service.agent_kind(meta["agentPreset"]) != item["agent_kind"])):
        raise TaskControlRefused("BULK_BINDING_CHANGED", "reviewed runtime identity changed")


async def before_answer(ctx, client):
    item = check_child(ctx)
    if not item:
        return
    meta = await client.guard_read("claude:get-session-meta", {"sessionId": item["session_id"]})
    check_meta(item, meta)
    state = await client.guard_read("claude:get-session-state", {"sessionId": item["session_id"]})
    current = prompt(state)
    if digest(current) != item["prompt_fingerprint"]:
        raise TaskControlRefused("BULK_PROMPT_CHANGED", "permission prompt differs from the reviewed selection")
    check_child(ctx)


def _child(ctx, item, phase):
    name = step_name(item, phase)
    def create():
        try:
            static(ctx.service, item, mode=item["mode"] if phase == "permissions" else None)
            principal = api_auth.Principal(ctx.actor, frozenset(binding(ctx.op)["accepted_scopes"]))
            params = ({"permission": "allow", "dont_ask_again": True, "tool_use_id": item["tool_use_id"]}
                      if phase == "answer" else {"mode": item["mode"]})
            pre = {"control_version": item["task_binding"]["control_version"]} if item["task_binding"] else {}
            child, fresh = ctx.service.create(principal, action="session." + phase,
                target={"host": item["host"], "session_id": item["session_id"]}, params=params, preconditions=pre,
                idempotency_key="bulk-child-" + uuid.uuid4().hex, entry="bulk")
        except (BatError, OperationError) as exc:
            return {"status": "refused", "code": code(exc)}
        if not fresh:
            raise OperationError("BULK_CHILD_CONFLICT", "server child identity already exists", 409)
        # Linkage and public child identity must commit with the receipt. A bookkeeping
        # failure must roll the whole child insert back, never leave an unlinked writer.
        ctx.service._merge_refs(child["operation_id"], {"bulk_approval_child": {
            "parent_id": ctx.operation_id, "item_id": item["item_id"], "phase": phase}})
        prior = (ctx.service._row(ctx.operation_id)["external_refs"] or {}).get("bulk_children") or {}
        ctx.set_refs(bulk_children={**prior, name: child["operation_id"]})
        return {"operation_id": child["operation_id"]}
    return ctx.effect(name, create)


def child_view(ops, saved):
    if not saved or not saved.get("operation_id"):
        return saved
    op = ops.get(saved["operation_id"])
    return {"operation_id": op["operation_id"], "status": op["status"], "code": op["error_code"],
            "reason": op["status_reason"], "acknowledged": bool(op["status"] == "succeeded" and
                (op.get("result") or {}).get("result") is True) if op["action"] == "session.answer" else op["status"] == "succeeded",
            "unproven": any(s["status"] in {"started", "uncertain"} for s in op["steps"]),
            "permission_frames": (op.get("external_refs") or {}).get("permission_frames")}


async def run(ctx):
    cancelled = ctx.service._row(ctx.operation_id)["cancel_requested"]
    principal = api_auth.Principal(ctx.actor, frozenset(binding(ctx.op)["accepted_scopes"]))
    rows, delays, unresolved = [], [], False
    for item in binding(ctx.op)["items"]:
        row = {"item_id": item["item_id"], "host": item["host"], "session_id": item["session_id"],
               "tool_use_id": item["tool_use_id"], "mode": item["mode"]}
        answer = receipt(ctx.service, ctx.operation_id, step_name(item, "answer"))
        if answer is None and not cancelled:
            answer = _child(ctx, item, "answer")
        answered = child_view(ctx.service, answer)
        permissions = receipt(ctx.service, ctx.operation_id, step_name(item, "permissions"))
        if (not cancelled and permissions is None and item["mode"] is not None and answered
                and answered.get("acknowledged") is True):
            permissions = _child(ctx, item, "permissions")
        for phase, saved in (("answer", answer), ("permissions", permissions)):
            if cancelled and saved and saved.get("operation_id"):
                ctx.service.cancel(principal, saved["operation_id"])
            view = child_view(ctx.service, saved)
            row[phase] = view
            if view:
                status = view.get("status")
                if status in {"accepted", "running", "uncertain", "waiting_checks", "waiting_external"}:
                    child = ctx.service._row(view["operation_id"])
                    due = child["next_run_at"] if status not in {"accepted", "running"} else None
                    # Follow the earliest persisted child deadline, while keeping bounded
                    # visibility of manual child controls. Parent controls clear their due time.
                    delays.append(max(MIN_PARENT_WAIT_S, min(MAX_PARENT_WAIT_S,
                        due - time.time() if due is not None else MIN_PARENT_WAIT_S)))
                if status == "needs_attention" or (status in {"failed", "cancelled"} and view.get("unproven")):
                    unresolved = True
        row["approved"] = bool(row["answer"] and row["answer"].get("acknowledged") is True)
        row["complete"] = row["approved"] and (item["mode"] is None or bool(row["permissions"] and row["permissions"].get("acknowledged")))
        rows.append(row)
    ctx.set_refs(bulk_items=rows)
    if delays:
        raise Wait("waiting_external", "reading the original bulk child operations", delay_s=min(delays))
    if unresolved:
        raise NeedsAttention("BULK_CHILD_UNSETTLED", "inspect the original child receipts; no ambiguous frame will be resent")
    if cancelled:
        raise Cancelled()
    return {"host": ctx.target["host"], "items": rows, "count": len(rows),
            "approved_count": sum(r["approved"] for r in rows), "completed_count": sum(r["complete"] for r in rows),
            "all_succeeded": all(r["complete"] for r in rows), "deferred_raises": []}


def cancel(ops, principal, op):
    with ops.journal.tx():
        ops.db.execute("UPDATE operations SET cancel_requested=1 WHERE operation_id=?", (op["operation_id"],))
        ops._transition(op["operation_id"], "running", reason="cancelling original bulk children; retaining sent receipts", actor=principal.actor)
    ops.kick()
    return ops.get(op["operation_id"], steps=False)


def resume_children(ops, principal, op):
    if op["cancel_requested"]:
        return
    for item in binding(op)["items"]:
        for phase in ("answer", "permissions"):
            saved = receipt(ops, op["operation_id"], step_name(item, phase))
            if saved and saved.get("operation_id") and ops._row(saved["operation_id"])["status"] == "needs_attention":
                ops.resume(principal, saved["operation_id"])


async def legacy(ops, principal, request, *, entry):
    allowed = {"host", "workspace", "confirm", "dry_run", "preview_token", "selection", "expected_fingerprint", "idempotency_key"}
    if set(request) - allowed or type(request.get("dry_run", False)) is not bool:
        raise OperationError("INVALID_REQUEST", "unknown bulk approval arguments", 422)
    if request.get("dry_run"):
        return await preview(ops, principal, {k: request[k] for k in ("host", "workspace") if request.get(k) is not None})
    if request.get("confirm") is not True:
        raise OperationError("CONFIRM_REQUIRED", "approve_pending requires confirm=true", 403)
    if not all(request.get(k) is not None for k in ("preview_token", "selection", "expected_fingerprint")):
        raise OperationError("BULK_PREVIEW_REQUIRED", "first use dry_run=true, then provide preview_token, expected_fingerprint and explicit selection", 422)
    if request.get("workspace") is not None:
        raise OperationError("INVALID_PARAMS", "workspace filters belong to preview; apply uses the fixed preview selection", 422)
    op, fresh = ops.create(principal, action=ACTION, target={"host": request.get("host")},
        params={k: request[k] for k in ("preview_token", "selection")},
        preconditions={"expected_fingerprint": request["expected_fingerprint"]},
        idempotency_key=request.get("idempotency_key"), entry=entry, _legacy_session=True)
    return {"operation": op, "created": fresh, "operation_id": op["operation_id"], "operation_status": op["status"],
            "idempotency_key": op["idempotency_key"], "idempotency_enabled": op["idempotency_enabled"]}
