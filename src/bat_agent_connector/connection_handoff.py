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

from . import dashboard_sync
from .operations import OperationError

VERSION = 1
STEP = "native_effects"
TTL_S = 300
ROLES = frozenset({"inventory", "backend", "startup", "central"})
ACTIONS = frozenset({"connection.reference.apply", "setup.connection.apply"})
DIGEST = re.compile(r"[0-9a-f]{64}")
CLAIMS = ("contract_version", "server_id", "principal_id", "installation_ref", "operation_id", "step",
          "review_digest", "expected_revision", "epoch", "effect_id", "expires_at")
RECEIPT_FIELDS = frozenset({"contract_version", "installation_ref", "operation_id", "step", "review_digest",
                          "expected_revision", "epoch", "effect_id", "challenge", "outcome", "roles"})


def now():
    return time.time()


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()


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
    key = bytes.fromhex(dashboard_sync._metadata(ops.journal)["signing_key"])
    return hmac.new(key, b"connection-native-handoff-v1\0" + canonical(claims), hashlib.sha256).hexdigest()


def prepare(ctx):
    """Persist only a challenge hash alongside its bounded, purpose-bound claims."""
    binding = ctx.admission_binding
    claims = {"contract_version": VERSION, "server_id": binding["server_id"],
        "principal_id": binding["principal_id"], "installation_ref": ctx.target["installation_ref"],
        "operation_id": ctx.operation_id, "step": STEP, "review_digest": binding["review_digest"],
        "expected_revision": ctx.preconditions["expected_revision"], "epoch": 1,
        "effect_id": "effect_" + digest([ctx.operation_id, STEP]), "expires_at": now() + TTL_S}
    with ctx.service.journal.tx():
        ctx.service._step_start(ctx.operation_id, STEP,
            {**claims, "challenge_hash": digest(_challenge(ctx.service, claims))})
        ctx.set_refs(native_effects_sent=False)


def read(ops, principal, operation, installation_ref):
    """Explicit installation-scoped operation read dispatches the original effect."""
    if operation["action"] != "setup.connection.apply" or installation_ref is None:
        return operation
    if not visible(ops.db, principal, operation) or installation_ref != operation["target"]["installation_ref"]:
        raise OperationError("FORBIDDEN", "Handoff belongs to the original principal and installation", 403)
    if operation["status"] in {"cancelled", "failed"} or operation["cancel_requested"]:
        _refuse("A cancelled or failed operation cannot dispatch a native effect")
    row = ops.db.execute("SELECT request,response FROM operation_steps WHERE operation_id=? AND name=?",
                         (operation["operation_id"], STEP)).fetchone()
    if not row:
        return operation
    request = json.loads(row["request"])
    claims = {key: request[key] for key in CLAIMS}
    # Expiry prevents a new dispatch. Existing effect readback retains the same challenge.
    sent = (operation.get("external_refs") or {}).get("native_effects_sent")
    if not sent and now() > claims["expires_at"]:
        _refuse("Handoff expired before dispatch; review the original operation")
    ops._merge_refs(operation["operation_id"], {"native_effects_sent": True})
    return {**operation, "external_refs": {**(operation.get("external_refs") or {}), "native_effects_sent": True},
        "native_handoff": {**claims, "challenge": _challenge(ops, claims),
        "receipt": json.loads(row["response"]) if row["response"] else None}}


def validate_receipt(body):
    if not isinstance(body, dict) or set(body) != RECEIPT_FIELDS or len(canonical(body)) > 8192:
        raise OperationError("INVALID_CONNECTION_RECEIPT", "Receipt must use the bounded v1 shape", 422)
    if (type(body["contract_version"]) is not int or body["contract_version"] != VERSION
            or not isinstance(body["operation_id"], str) or not re.fullmatch(r"op_[0-9a-f]{32}", body["operation_id"])
            or not isinstance(body["challenge"], str) or not DIGEST.fullmatch(body["challenge"])
            or type(body["expected_revision"]) is not int or body["expected_revision"] < 0
            or type(body["epoch"]) is not int or body["epoch"] < 1
            or not isinstance(body["outcome"], str) or body["outcome"] not in {"applied", "unchanged", "refused", "uncertain"}):
        raise OperationError("INVALID_CONNECTION_RECEIPT", "Invalid receipt identity or outcome", 422)
    roles = body["roles"]
    if not isinstance(roles, list) or not 1 <= len(roles) <= len(ROLES):
        raise OperationError("INVALID_CONNECTION_RECEIPT", "Receipt needs bounded role digests", 422)
    seen = set()
    for role in roles:
        if (not isinstance(role, dict) or set(role) != {"role", "before_digest", "after_digest"}
                or not isinstance(role["role"], str) or role["role"] not in ROLES or role["role"] in seen
                or any(not isinstance(role[k], str) or not DIGEST.fullmatch(role[k]) for k in ("before_digest", "after_digest"))
                or body["outcome"] in {"unchanged", "refused"} and role["before_digest"] != role["after_digest"]):
            raise OperationError("INVALID_CONNECTION_RECEIPT", "Only unique roles and SHA-256 digests are accepted", 422)
        seen.add(role["role"])
    return {key: value for key, value in body.items() if key != "challenge"}


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
        if not row:
            _refuse()
        request = json.loads(row["request"])
        if any(body[key] != request[key] for key in RECEIPT_FIELDS - {"challenge", "outcome", "roles"}):
            _refuse()
        if not hmac.compare_digest(digest(body["challenge"]), request["challenge_hash"]):
            _refuse()
        if not (operation.get("external_refs") or {}).get("native_effects_sent"):
            _refuse("Handoff has not been dispatched")
        receipt = {**value, "receipt_ref": "receipt_" + digest(value)}
        if row["response"]:
            if json.loads(row["response"]) != receipt:
                _refuse("The challenge already recorded a different receipt")
            return {"receipt": receipt, "replayed": True}
        if now() > request["expires_at"]:
            _refuse("An expired challenge cannot report a new effect")
        ops.db.execute("UPDATE operation_steps SET response=? WHERE operation_id=? AND name=?",
            (canonical(receipt).decode(), operation["operation_id"], STEP))
        ops.db.execute("UPDATE operations SET next_run_at=0 WHERE operation_id=?", (operation["operation_id"],))
        ops.journal.api_event("operation", operation["operation_id"], "connection.native.receipt",
            {"step": STEP, "receipt_ref": receipt["receipt_ref"], "outcome": body["outcome"]}, actor=principal.actor)
    ops.kick()
    return {"receipt": receipt, "replayed": False}
