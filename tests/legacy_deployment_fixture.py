"""Explicit pre-admission deployment rows for migration/recovery fixtures only.

Calling today's OperationService.create() would require today's provider bindings
and write current provenance. These fixtures represent rows persisted before those
contracts existed; they deliberately carry no invented admission/source authority.
"""
from __future__ import annotations

import hashlib
import json
import time
import uuid


def legacy_deployment(journal, principal, *, action, target, idempotency_key, params=None, preconditions=None):
    assert action in {"deployment.start", "delivery.merge_and_deploy"}
    params, preconditions = params or {}, preconditions or {}
    def encode(value):
        return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    request = {"action": action, "target": target, "params": params, "preconditions": preconditions}
    operation_id, now = "op_" + uuid.uuid4().hex, time.time()
    with journal.tx():
        journal.db.execute("""INSERT INTO operations(operation_id,actor,entry,idem_key,request_hash,action,target,
            params,preconditions,status,created_at,updated_at,external_refs) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,NULL)""",
            (operation_id, principal.actor, "http", idempotency_key, hashlib.sha256(encode(request).encode()).hexdigest(),
             action, encode(target), encode(params), encode(preconditions), "accepted", now, now))
        journal.api_event("operation", operation_id, "operation.accepted",
            {"action": action, "target": target, "entry": "http"}, actor=principal.actor)
    return {"operation_id": operation_id}
