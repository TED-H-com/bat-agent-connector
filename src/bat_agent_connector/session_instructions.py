"""Journal receipts for session instructions; not a live BAT queue or queue order."""

from __future__ import annotations

import base64
import json
import math
import re
import time

from .operations import OperationError

# A task's mutable current session is deliberately not joined into historical sends.
BINDING = """CASE
 WHEN json_type(external_refs,'$.resolved_target.host')='text'
  AND json_type(external_refs,'$.resolved_target.session_id')='text' THEN json_extract(external_refs,'$.resolved_target')
 WHEN json_type(target,'$.host')='text' AND json_type(target,'$.session_id')='text' THEN target
 WHEN json_type(external_refs,'$.host')='text' AND json_type(external_refs,'$.session_id')='text' THEN external_refs
 ELSE json_extract(external_refs,'$.admission_binding') END"""


def _text(value, max_chars=512):
    return value if isinstance(value, str) and len(value) <= max_chars and not any(ord(c) < 32 for c in value) else None


def _cursor(value, host, sid):
    try:
        if not isinstance(value, str) or len(value) > 2048:
            raise ValueError
        body = json.loads(base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True))
        if (not isinstance(body, list) or len(body) != 4 or body[:2] != [host, sid]
                or type(body[2]) not in (float, int) or not math.isfinite(body[2]) or body[2] < 0
                or not isinstance(body[3], str) or not re.fullmatch(r"op_[0-9a-f]{32}", body[3])):
            raise ValueError
        return body[2], body[3]
    except (ValueError, TypeError):
        raise OperationError("INVALID_CURSOR", "use the returned cursor for this exact host and session", 422) from None


def _item(ops, row, host, sid):
    op = ops._decode(row)
    params = {**op["params"], **((op.get("external_refs") or {}).get("resolved_params") or {})}
    step = ops.db.execute("SELECT * FROM operation_steps WHERE operation_id=? AND name='send'",
                          (op["operation_id"],)).fetchone()
    receipt = json.loads(step["response"]) if step and step["status"] == "succeeded" and step["response"] else op.get("result") or {}
    accepted = receipt.get("accepted") if type(receipt.get("accepted")) is bool else None
    refs = op.get("external_refs") or {}
    command = ops.db.execute("""SELECT c.* FROM commands c JOIN tasks t USING(task_id)
        WHERE c.command_id=? AND c.task_id=? AND t.host=? AND c.session_id=? AND c.kind='send'""",
        (refs.get("command_id"), refs.get("task_id"), host, sid)).fetchone()
    if command and command["status"] in {"accepted", "settled"}:
        accepted = True
    elif command and command["status"] == "rejected":
        accepted = False
    phase = ("accepted" if accepted is True else "not_accepted" if accepted is False
             else "unconfirmed" if op["status"] in {"uncertain", "needs_attention"} or step and step["status"] == "uncertain"
                 or command and command["status"] in {"uncertain", "needs_review"}
             else "operation_cancelled" if op["status"] == "cancelled"
             else "operation_failed" if op["status"] == "failed" else "pending")
    message_id = receipt.get("message_id") or (command["message_id"] if command else None)
    if not message_id:
        request = json.loads(step["request"]) if step and step["request"] else {}
        message_id = request.get("message_id") or params.get("message_id") or "batc-" + op["operation_id"]
    text = params.get("text") if isinstance(params.get("text"), str) else ""
    return {"operation_id": op["operation_id"], "host": host, "session_id": sid, "actor": op["actor"],
            "operation_status": op["status"], "error_code": _text(op.get("error_code"), 128),
            "created_at": op["created_at"], "updated_at": op["updated_at"], "phase": phase,
            "message_id": _text(message_id), "command_id": command["command_id"] if command else None,
            "text_excerpt": text[:1000], "text_truncated": len(text) > 1000,
            "queue_requested": params.get("queue") is True, "accepted": accepted,
            "was_queued": receipt.get("queued") if type(receipt.get("queued")) is bool else None,
            "receipt_recorded_at": step["finished_at"] if step and step["status"] == "succeeded" else None,
            "turn_attribution": _text(receipt.get("turn_attribution"), 80),
            "cancel_requested": op["cancel_requested"], "queue_position": None,
            "live_queue_status": "unavailable", "per_message_cancel": False,
            "operation_url": "/api/v1/operations/" + op["operation_id"]}


def read(ops, principal, host, session_id, *, limit=30, cursor=None):
    if not principal.allows("observe"):
        raise OperationError("FORBIDDEN", "instruction receipts need observe", 403)
    if not isinstance(host, str) or host not in ops.context["fleet"].config.hosts:
        raise OperationError("UNKNOWN_HOST", "host is not configured", 404)
    if not _text(session_id, 256):
        raise OperationError("INVALID_PARAMS", "an exact session ID is required", 422)
    from .pm_source_lineage import require_session
    require_session(ops.db, principal, host, session_id)
    if type(limit) is not int or not 1 <= limit <= 100:
        raise OperationError("INVALID_PARAMS", "limit must be 1-100", 422)
    before = _cursor(cursor, host, session_id) if cursor is not None else None
    sql = ("WITH scoped AS (SELECT *, " + BINDING + " AS instruction_binding FROM operations WHERE action='session.send') "  # noqa: S608 - fixed binding expression
           "SELECT * FROM scoped WHERE json_extract(instruction_binding,'$.host')=? "
           "AND json_extract(instruction_binding,'$.session_id')=?")
    args = [host, session_id]
    if before:
        sql += " AND (created_at<? OR (created_at=? AND operation_id<?))"
        args.extend((before[0], before[0], before[1]))
    rows = ops.db.execute(sql + " ORDER BY created_at DESC,operation_id DESC LIMIT ?", (*args, limit + 1)).fetchall()
    more, page = len(rows) > limit, rows[:limit]
    next_cursor = (base64.urlsafe_b64encode(json.dumps([host, session_id, page[-1]["created_at"],
                   page[-1]["operation_id"]], separators=(",", ":")).encode()).decode().rstrip("=") if more else None)
    return {"version": 1, "host": host, "session_id": session_id, "source": "central_operation_journal", "read_at": time.time(),
            "instructions": [_item(ops, row, host, session_id) for row in page], "next_cursor": next_cursor,
            "live_queue_available": False, "per_message_cancel": False,
            "interrupt_scope": "session", "queue_receipt_semantics": "queued_at_submission_not_current_position"}
