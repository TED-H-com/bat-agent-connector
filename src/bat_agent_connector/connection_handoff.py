"""Version-1 native participant wire contract; operations remain the authority.

No native effect runs here. A dispatched challenge is a transport fence, never
proof that a file was written or that BAT is reachable.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import re
import time

from .connection_contract import BOUNDS, CONTRACT, VERSION, canonical, require_version, valid_digest
from .operations import OperationError

STEP = "native_effects"
TTL_S = BOUNDS["challenge_ttl_s"]
ROLES = tuple(CONTRACT["roles"])
ACTIONS = frozenset({"connection.reference.apply", "setup.connection.apply"})
CLAIMS = ("contract_version", "server_id", "principal_id", "installation_ref", "operation_id", "step",
          "review_digest", "expected_revision", "epoch", "effect_id", "expires_at", "native_plan")
RECEIPT_FIELDS = frozenset({"contract_version", "installation_ref", "operation_id", "step", "review_digest",
                          "expected_revision", "epoch", "effect_id", "challenge", "outcome", "roles", "code", "native_review_digest", "phase"})
RECEIPT_LIMIT = BOUNDS["receipt_bytes"]


def now():
    return time.time()


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def _refuse(message="Native receipt does not match the accepted handoff"):
    raise OperationError("CONNECTION_HANDOFF_CONFLICT", message, 409)


def visible(db, principal, operation):
    row = db.execute("SELECT external_refs FROM operations WHERE operation_id=?",
                     (operation.get("operation_id"),)).fetchone()
    binding = (json.loads(row[0] or "{}") if row else {}).get("admission_binding", {})
    metadata = db.execute("SELECT server_id FROM api_sync_metadata WHERE singleton=1").fetchone()
    # dashboard_sync identity includes effective scopes and admin status, not a token ID.
    scope = {"actor": principal.actor, "scopes": sorted(principal.scopes), "admin": principal.admin}
    return bool(metadata and binding.get("principal_id") == digest(scope) and binding.get("server_id") == metadata[0])


def _challenge(ops, claims):
    key = ops.context["daemon"]._admin_token.encode()
    return hmac.new(key, b"connection-native-handoff-v1\0" + canonical(claims), hashlib.sha256).hexdigest()


def prepare(ctx):
    """Persist only a challenge hash alongside its bounded, purpose-bound claims."""
    binding = ctx.admission_binding
    claims = {"contract_version": VERSION, "server_id": binding["server_id"],
        "principal_id": binding["principal_id"], "installation_ref": ctx.target["installation_ref"],
        "operation_id": ctx.operation_id, "step": STEP, "review_digest": binding["review_digest"],
        "expected_revision": ctx.preconditions["expected_revision"], "epoch": 1,
        "effect_id": "effect_" + digest([ctx.operation_id, STEP]), "expires_at": now() + TTL_S,
        "native_plan": binding["native_plan"]}
    with ctx.service.journal.tx():
        ctx.service._step_start(ctx.operation_id, STEP,
            {**claims, "challenge_hash": digest(_challenge(ctx.service, claims))})
        ctx.set_refs(native_effects_sent=False)


def _renew(ops, operation_id, request, *, claiming=False):
    """Only an undispatched challenge may change; its effect identity never does."""
    claims = {key: request[key] for key in CLAIMS}
    expired = now() >= claims["expires_at"]
    if expired or claiming:
        claims.update(epoch=claims["epoch"] + int(expired), expires_at=now() + TTL_S)
        request = {**claims, "challenge_hash": digest(_challenge(ops, claims))}
        ops.db.execute("UPDATE operation_steps SET request=?,status='started',error=NULL WHERE operation_id=? AND name=?",
            (canonical(request).decode(), operation_id, STEP))
        ops.journal.api_event("operation", operation_id, "connection.handoff.issued",
            {"step": STEP, "epoch": claims["epoch"]}, actor=ops.get(operation_id, steps=False)["actor"])
    return request


def renew_unclaimed(ops, operation):
    """Explicit resume may renew; background receipt polling never issues challenges."""
    if not (operation.get("external_refs") or {}).get("native_effects_sent"):
        with ops.journal.tx():
            row = ops.db.execute("SELECT request FROM operation_steps WHERE operation_id=? AND name=?",
                (operation["operation_id"], STEP)).fetchone()
            if row:
                _renew(ops, operation["operation_id"], json.loads(row["request"]))


def receipts(row):
    value = json.loads(row["response"]) if row["response"] else None
    return value.get("receipts", []) if isinstance(value, dict) and "receipts" in value else ([value] if value else [])


def _envelope(ops, request, history):
    claims = {key: request[key] for key in CLAIMS}
    challenge = _challenge(ops, claims)
    if not hmac.compare_digest(digest(challenge), request["challenge_hash"]):
        raise OperationError("CONNECTION_HANDOFF_KEY_CHANGED", "Original challenge cannot be reconstructed; no write instruction is served / "
            "無法重建原 challenge；不提供寫入指示", 409)
    return {**claims, "challenge": challenge, "receipt": history[-1] if history else None}


def claim(ops, principal, body):
    from .managed_setup import _installation, _text
    _installation(ops.context["daemon"], principal)
    require_version(body)
    if (set(body) != {"contract_version", "installation_ref", "operation_id", "mode"}
            or len(canonical(body)) > BOUNDS["handoff_bytes"]
            or not isinstance(body["mode"], str) or body["mode"] not in CONTRACT["modes"]
            or not isinstance(body["operation_id"], str) or not re.fullmatch(r"op_[0-9a-f]{32}", body["operation_id"])):
        raise OperationError("INVALID_CONNECTION_HANDOFF", "Use the exact version, operation, installation and mode / 請使用完整的版本、操作、安裝識別與模式", 422)
    _text(body["installation_ref"], "installation reference", BOUNDS["installation_ref_chars"])
    with ops.journal.tx():
        operation = ops.get(body["operation_id"], principal=principal)
        if (operation["action"] != "setup.connection.apply"
                or body["installation_ref"] != operation["target"]["installation_ref"]):
            raise OperationError("FORBIDDEN", "Handoff belongs to the original principal and installation", 403)
        if body["mode"] == "claim" and (operation["status"] == "cancelled" or operation["cancel_requested"]):
            _refuse("A terminal operation cannot claim a native effect")
        row = ops.db.execute("SELECT request,response FROM operation_steps WHERE operation_id=? AND name=?",
            (operation["operation_id"], STEP)).fetchone()
        if not row:
            _refuse("The native step is not ready")
        request = json.loads(row["request"])
        sent = (operation.get("external_refs") or {}).get("native_effects_sent")
        history = receipts(row)
        if body["mode"] == "readback":
            if not sent:
                _refuse("Readback requires the original sent fence / 查回需要原已派送記錄")
            return {"native_handoff": _envelope(ops, request, history), "read_only": True}
        if operation["status"] in {"succeeded", "failed"} and (not sent or not history):
            _refuse("A terminal operation cannot claim a new native effect")
        if sent and now() >= request["expires_at"]:
            raise OperationError("CONNECTION_HANDOFF_EXPIRED", "A claimed effect cannot be claimed again after expiry", 409)
        from .setup_connections import revision_matches, validate_operation_source
        refusal = None
        if not sent:
            try:
                if not revision_matches(ops, operation):
                    raise OperationError("SETUP_REVIEW_CHANGED",
                        "Connection revision changed before dispatch; review again / 派送前連線版本已變更；請重新檢閱", 409)
                validate_operation_source(ops, operation)
            except OperationError as error:
                refusal = error
        if refusal:
            # Commit this honest refusal before replying; the native effect was never sent.
            if operation["status"] not in {"running", "needs_attention"}:
                ops._transition(operation["operation_id"], "running")
            ops._transition(operation["operation_id"], "failed", error_code=refusal.code, reason=refusal.message)
        else:
            if not sent:
                request = _renew(ops, operation["operation_id"], request, claiming=True)
                ops._merge_refs(operation["operation_id"], {"native_effects_sent": True})
            return {"native_handoff": _envelope(ops, request, history), "read_only": False}
    raise refusal


def _invalid_receipt():
    raise OperationError("INVALID_CONNECTION_RECEIPT", "Use the bounded native receipt and its allowed outcome, phase and code / "
        "請使用符合限制的本機回執，以及允許的結果、階段與代碼", 422)


def validate_receipt(body):
    require_version(body)
    if set(body) != RECEIPT_FIELDS or len(canonical(body)) > RECEIPT_LIMIT:
        _invalid_receipt()
    if (not isinstance(body["operation_id"], str) or not re.fullmatch(r"op_[0-9a-f]{32}", body["operation_id"])
            or not valid_digest(body["challenge"]) or not valid_digest(body["review_digest"])
            or not valid_digest(body["native_review_digest"])
            or not isinstance(body["installation_ref"], str) or not 1 <= len(body["installation_ref"]) <= BOUNDS["installation_ref_chars"]
            or not isinstance(body["step"], str) or not 1 <= len(body["step"]) <= 128
            or not isinstance(body["effect_id"], str) or not re.fullmatch(r"effect_[0-9a-f]{64}", body["effect_id"])
            or type(body["expected_revision"]) is not int or body["expected_revision"] < 0
            or type(body["epoch"]) is not int or body["epoch"] < 1
            or not isinstance(body["outcome"], str) or not isinstance(body["phase"], str)
            or body["code"] is not None and not isinstance(body["code"], str)
            or body["code"] not in CONTRACT["matrix"].get(body["outcome"], {}).get(body["phase"], [])):
        _invalid_receipt()
    roles = body["roles"]
    if not isinstance(roles, list) or len(roles) != len(ROLES):
        _invalid_receipt()
    for expected, role in zip(ROLES, roles, strict=True):
        if (not isinstance(role, dict) or set(role) != {"role", "before_digest", "after_digest", "observation"}
                or role["role"] != expected or not valid_digest(role["before_digest"], nullable=True)
                or not valid_digest(role["after_digest"], nullable=True)
                or not isinstance(role["observation"], str) or role["observation"] not in CONTRACT["observations"]
                or (role["observation"] == "present") != (role["after_digest"] is not None)):
            _invalid_receipt()
    return {key: value for key, value in body.items() if key != "challenge"}


def _validate_review(body, request):
    plan = request["native_plan"]
    matches = hmac.compare_digest(body["native_review_digest"], plan["review_digest"])
    changed = any(a["before_digest"] != a["after_digest"] for a in plan["artifacts"])
    for role, artifact in zip(body["roles"], plan["artifacts"], strict=True):
        matches = matches and role["before_digest"] == artifact["before_digest"]
        if body["outcome"] in {"applied", "unchanged"}:
            matches = matches and role["observation"] == "present" and role["after_digest"] == artifact["after_digest"]
    if body["outcome"] in {"applied", "unchanged"}:
        matches = matches and changed == (body["outcome"] == "applied")
    if not matches:
        raise OperationError("SETUP_REVIEW_CHANGED", "Native evidence does not match the accepted native review / "
            "本機證據不符合已接受的本機檢閱", 409)


def receive(ops, principal, body):
    value = validate_receipt(body)
    operation = ops.get(body["operation_id"], principal=principal)
    if operation["action"] != "setup.connection.apply" or not visible(ops.db, principal, operation):
        _refuse()
    # Keep the original personal setup boundary for every participant write.
    from .managed_setup import _installation
    _installation(ops.context["daemon"], principal)
    with ops.journal.tx():
        row = ops.db.execute("SELECT request,response FROM operation_steps WHERE operation_id=? AND name=?",
            (operation["operation_id"], STEP)).fetchone()
        receipt = {**value, "receipt_ref": "receipt_" + digest(value)}
        history = receipts(row) if row else []
        request = json.loads(row["request"]) if row else {}
        matches = bool(row) and all(body[key] == request[key]
            for key in RECEIPT_FIELDS - {"challenge", "outcome", "roles", "code", "native_review_digest", "phase"}) and hmac.compare_digest(
                digest(body["challenge"]), request["challenge_hash"])
        sent = (operation.get("external_refs") or {}).get("native_effects_sent")
        if receipt in history and matches and sent:
            return {"receipt": receipt, "replayed": True}
        if operation["status"] in {"failed", "succeeded", "cancelled"}:
            raise OperationError("CONNECTION_OPERATION_TERMINAL", "This operation is terminal", 409)
        if not matches:
            _refuse()
        if not sent:
            _refuse("Handoff has not been dispatched")
        _validate_review(body, request)
        if history and (history[-1]["outcome"] != "uncertain" or receipt["outcome"] == "uncertain"):
            _refuse("Only one definitive readback may supersede uncertain evidence")
        # Expiry prohibits a new claim/write, not late evidence of the original claimed effect.
        ops.db.execute("UPDATE operation_steps SET response=? WHERE operation_id=? AND name=?",
            (canonical({"receipts": [*history, receipt]}).decode(), operation["operation_id"], STEP))
        ops._merge_refs(operation["operation_id"], {
            "connection_id": operation["target"].get("connection_id") or "conn_" + operation["operation_id"][3:],
            "receipt_refs": [item["receipt_ref"] for item in [*history, receipt]]})
        ops.db.execute("UPDATE operations SET next_run_at=0 WHERE operation_id=?", (operation["operation_id"],))
        ops.journal.api_event("operation", operation["operation_id"], "connection.native.receipt",
            {"step": STEP, "receipt_ref": receipt["receipt_ref"], "outcome": body["outcome"],
                "phase": body["phase"], "code": body["code"]}, actor=principal.actor)
        if body["outcome"] == "abandoned":
            ops.journal.api_event("operation", operation["operation_id"], "connection.native.abandoned",
                {"receipt_ref": receipt["receipt_ref"], "code": body["code"], "phase": body["phase"]}, actor=principal.actor)
        if operation["status"] == "needs_attention" and receipt["outcome"] != "uncertain":
            ops.resume(principal, operation["operation_id"])
    ops.kick()
    return {"receipt": receipt, "replayed": False}
