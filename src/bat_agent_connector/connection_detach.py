"""Reviewed, principal-local reference effects; no service or source mutation."""
from __future__ import annotations

import json
import re

from . import connection_dependencies, connection_handoff, dashboard_sync, setup_connections
from .connection_contract import CONTRACT, valid_digest, validate_native_plan_v2
from .operations import ActionDef, NeedsAttention, OperationError
from .pm_release_baselines import _snapshot

ACTION = "connection.reference.detach"


def owned(ops, principal, target):
    if not principal.allows("observe"):
        raise OperationError("FORBIDDEN", "Connection references require observe / 連線關聯需要 observe 權限", 403)
    identity = dashboard_sync.identity(ops.journal, principal)
    row = setup_connections._row(ops, identity["principal_id"], target)
    if not row:
        raise OperationError("NOT_FOUND", "Connection not found in this principal and installation", 404)
    return identity, row


def available(ops, principal_id, target):
    try:
        setup_connections._require_connection_available(ops, principal_id, target)
    except OperationError as error:
        if error.code != "CONNECTION_BUSY":
            raise
        raise OperationError("CONNECTION_OPERATION_RUNNING", "Finish the original connection operation first / 請先完成原連線操作", 409) from None


def plan(ops, principal, target, params, pre):
    if (set(target) != {"installation_ref", "connection_id"} or not isinstance(target.get("connection_id"), str)
            or not re.fullmatch(r"conn_[0-9a-f]{32}", target["connection_id"])):
        raise OperationError("INVALID_TARGET", "Use installation_ref and connection_id / 請提供安裝與連線識別", 422)
    setup_connections.setup._text(target["installation_ref"], "installation reference", 128)
    identity, row = owned(ops, principal, target)
    if row["state"] != "active":
        raise OperationError("CONNECTION_ALREADY_DETACHED", "This reference is already detached / 此連線關聯已中斷", 409)
    if "native_plan" not in params:
        raise OperationError("NATIVE_REVIEW_REQUIRED", "Review the local reference effects first / 請先檢閱本機連線關聯效果", 409)
    if set(params) != {"native_plan"}:
        raise OperationError("INVALID_PARAMS", "Detach accepts only native_plan / 中斷連線關聯僅接受 native_plan", 422)
    kind = connection_handoff.effect_kind(ACTION)
    validate_native_plan_v2(params["native_plan"], action=ACTION, kind=kind)
    if (set(pre) != {"expected_revision", "dependency_digest", "review_digest"}
            or type(pre.get("expected_revision")) is not int or pre["expected_revision"] < 0
            or not all(valid_digest(pre.get(k)) for k in ("dependency_digest", "review_digest"))):
        raise OperationError("PRECONDITION_REQUIRED", "Supply revision, dependency and review digests / 請提供版本、相依性及檢閱摘要", 422)
    if pre["expected_revision"] != row["revision"]:
        setup_connections._changed()
    available(ops, identity["principal_id"], target)
    # Read all bounded pages at the same snapshot; a blocker beyond page one still refuses.
    with _snapshot(ops.db):
        dependencies = page = connection_dependencies.read(ops, principal, **target)
        while True:
            if not page["server_complete"]:
                raise OperationError("CONNECTION_DEPENDENCIES_UNPROVEN", "Connection dependencies could not be fully checked / 無法完整確認連線相依性", 409)
            if any(r["disposition"] == "blocking" for r in page["impacts"]):
                raise OperationError("CONNECTION_DEPENDENCY_ACTIVE", CONTRACT["v2"]["codes"]["CONNECTION_DEPENDENCY_ACTIVE"]["reason"], 409)
            if page["next_offset"] is None:
                break
            page = connection_dependencies.read(ops, principal, **target, offset=page["next_offset"])
            if page["dependency_digest"] != dependencies["dependency_digest"]:
                setup_connections._changed()
    preview = {"action": ACTION, "target": target, "expected_revision": row["revision"],
        "dependency_digest": dependencies["dependency_digest"], "dependencies": dependencies, "native_plan": params["native_plan"]}
    review = connection_handoff.digest({**identity, **preview})
    binding = {**identity, **target, "native_plan": params["native_plan"], "review_digest": review}
    return binding, {**preview, "review_digest": review}


def admit(ops, principal, target, params, pre):
    binding, preview = plan(ops, principal, target, params, pre)
    if pre["dependency_digest"] != preview["dependency_digest"] or pre["review_digest"] != preview["review_digest"]:
        setup_connections._changed()
    return binding


def save(ctx):
    binding = ctx.admission_binding
    before = setup_connections._row(ctx.service, binding["principal_id"], ctx.target)
    if (binding["server_id"] != dashboard_sync._metadata(ctx.service.journal)["server_id"] or not before
            or before["revision"] != ctx.preconditions["expected_revision"] or before["state"] != "active"):
        raise NeedsAttention("CONNECTION_PROJECTION_CHANGED", "Native result retained; original reference changed / 已保留本機結果；原連線關聯已變更")
    refs = list(before["receipt_refs"])
    native = ctx.service.db.execute("SELECT response FROM operation_steps WHERE operation_id=? AND name=?",
        (ctx.operation_id, connection_handoff.STEP)).fetchone()
    refs.extend(r["receipt_ref"] for r in connection_handoff.receipts(native) if r["receipt_ref"] not in refs)
    ctx.service.db.execute("""UPDATE connection_bindings SET state='detached',revision=revision+1,receipt_refs=?
        WHERE principal=? AND installation_ref=? AND connection_id=?""",
        (json.dumps(refs), binding["principal_id"], ctx.target["installation_ref"], ctx.target["connection_id"]))
    result = {**ctx.target, "revision": before["revision"] + 1, "state": "detached"}
    ctx.service.journal.api_event("operation", ctx.operation_id, "connection.binding.detached", result, actor=ctx.actor)
    return result


def install(ops):
    if ACTION not in ops.actions:
        ops.register(ActionDef(ACTION, "observe", "Detach a principal-local connection reference / 中斷個人連線關聯",
            setup_connections._run, admit, target_keys=("installation_ref", "connection_id"),
            authorize_existing=setup_connections._authorize))
