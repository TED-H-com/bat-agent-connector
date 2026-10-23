"""v1.3 P5 — Follow-up instructions to a started Work item (`session.send` with Work target).

Central authority rules:
- Same Work, same Controller: resolves `{workspace_id, work_item_id}` to the latest `started`
  attempt and its managed `(host, session_id)` session, requiring matching controller authority.
- Connector-owned queue (`after_current`), never steering an active turn: holds queued inputs in
  durable `work_inputs` until `session_results` records a terminal turn boundary (`turn_done` or
  `agent_error`) for the effective predecessor turn and the session is idle.
- Proven-unsent transport fence (`send_sent`): monotonic per operation. Pre-fence disconnects or
  busy checks wait with `uncertain_tries=0`; post-fence errors become `AmbiguousOutcome` and
  reconcile by the original `batc-<operation_id>` echo without ever resending.
"""

from __future__ import annotations

import json
from typing import Any

from . import (
    controller_authority,
    pm_lifecycle_gates,
    pm_source_lineage,
    pm_work_start,
    pm_workspace,
    registry,
    resource_policy,
    service,
    work_items,
)
from .api_auth import Principal
from .errors import ResourceReadOnly
from .operations import (
    AMBIGUOUS,
    AmbiguousOutcome,
    Cancelled,
    NeedsAttention,
    OpContext,
    OperationError,
    OperationService,
    StepFailed,
    Wait,
)
from .pm_release_baselines import _snapshot, _snapshot_read
from .redact import redact

ACTION = "session.send"
SCHEDULES = ("after_current", "now")
DEFAULT_SCHEDULE = "after_current"
STATES = ("queued", "sending", "sent", "unknown", "failed", "cancelled")

WORK_FOLLOW_UP_HEAD_POLL_S = 5.0
WORK_FOLLOW_UP_FOLLOWER_FALLBACK_S = 60.0
TEXT_EXCERPT_MAX_CHARS = 240

ENDED_RUNTIME_STATUSES = frozenset({"exited", "stopped", "ended", "dead"})
# Inventory predecessor check only, and only when that row has no explicit
# streaming flag. BAT documents runtimeStatus as "starting" or null. "running"
# and "active" are not protocol values and do not make a row busy. Dropping
# this set would treat a "starting" row with no stream flag as idle at
# prepare_send, which is a wider permission than the final-frame idle check.
BUSY_RUNTIME_STATUSES = frozenset({"streaming", "busy", "starting"})


class ReceiptState(str):
    """String state with `.cancellable` and dict-style access for table-driven & API callers."""

    cancellable: bool
    retryable_as_new: bool
    reconcilable: bool

    def __new__(cls, state: str, *, cancellable: bool = False) -> ReceiptState:
        obj = super().__new__(cls, state)
        obj.cancellable = cancellable
        obj.retryable_as_new = state in {"failed", "cancelled"}
        obj.reconcilable = state == "unknown"
        return obj

    @property
    def state(self) -> str:
        return str(self)

    def __getitem__(self, key: str | int | slice) -> Any:
        if isinstance(key, str):
            if key == "state":
                return str(self)
            if key == "cancellable":
                return self.cancellable
            if key == "retryable_as_new":
                return self.retryable_as_new
            if key == "reconcilable":
                return self.reconcilable
            raise KeyError(key)
        return super().__getitem__(key)


def _bad(code: str, message: str, status: int = 409) -> OperationError:
    return OperationError(code, message, status)


def _has_table(db, name: str) -> bool:
    return (
        db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (name,),
        ).fetchone()
        is not None
    )


def installed(db) -> bool:
    return _has_table(db, "work_inputs")


def schema_db(db) -> None:
    for ddl in (
        """CREATE TABLE IF NOT EXISTS work_inputs (
            sequence INTEGER PRIMARY KEY AUTOINCREMENT,
            operation_id TEXT NOT NULL UNIQUE,
            workspace_id TEXT NOT NULL,
            work_item_id TEXT NOT NULL,
            attempt_id TEXT NOT NULL,
            host TEXT NOT NULL,
            session_id TEXT NOT NULL,
            position INTEGER NOT NULL,
            schedule TEXT NOT NULL CHECK (schedule IN ('after_current', 'now')),
            message_id TEXT NOT NULL UNIQUE,
            created_at REAL NOT NULL,
            UNIQUE(host, session_id, position),
            UNIQUE(attempt_id, position)
        )""",
        """CREATE INDEX IF NOT EXISTS idx_work_inputs_work_seq
            ON work_inputs(workspace_id, work_item_id, sequence ASC)""",
        """CREATE INDEX IF NOT EXISTS idx_work_inputs_attempt_pos
            ON work_inputs(attempt_id, position ASC)""",
        """CREATE INDEX IF NOT EXISTS idx_work_inputs_session_pos
            ON work_inputs(host, session_id, position ASC)""",
        """CREATE TRIGGER IF NOT EXISTS work_inputs_no_update
            BEFORE UPDATE ON work_inputs
            BEGIN
                SELECT RAISE(ABORT, 'work_inputs rows are immutable');
            END""",
        """CREATE TRIGGER IF NOT EXISTS work_inputs_no_delete
            BEFORE DELETE ON work_inputs
            BEGIN
                SELECT RAISE(ABORT, 'work_inputs rows are immutable');
            END""",
    ):
        db.execute(ddl)


def schema(journal) -> None:
    with journal.tx():
        schema_db(journal.db)


def install(ops: OperationService) -> None:
    schema(ops.journal)


def capabilities(ops: OperationService | None = None) -> dict[str, Any]:
    avail = True if ops is None else ACTION in ops.actions
    return {
        "version": 1,
        "supported": avail,
        "available": avail,
        "action": ACTION,
        "target_keys": ["workspace_id", "work_item_id"],
        "schedules": list(SCHEDULES),
        "default_schedule": DEFAULT_SCHEDULE,
        "queue_param_accepted": False,
        "steer_supported": False,
        "codex_supported": False,
        "cancel_while_states": ["queued"],
        "reconcile_on_states": ["unknown"],
        "states": list(STATES),
        "reads": ["pm_work_inputs_list"],
        "mcp_tools": ["operation_submit", "pm_work_inputs_list"],
        "routes": {
            "send": "/api/v1/operations",
            "inputs_list": "/api/v1/pm/workspaces/{workspace_id}/work/{work_item_id}/inputs",
            "operation": "/api/v1/operations/{operation_id}",
            "cancel": "/api/v1/operations/{operation_id}/cancel",
            "resume": "/api/v1/operations/{operation_id}/resume",
        },
        "required_scopes": ["observe", "operate"],
        "requires_workspace_membership": True,
        "requires_writer_for_send": True,
        "requires_same_controller": True,
    }


def is_work_target(action: str, target: Any) -> bool:
    return (
        action == ACTION
        and isinstance(target, dict)
        and ("workspace_id" in target or "work_item_id" in target)
    )


def is_work_follow_up_binding(action: str, binding: Any) -> bool:
    return (
        action == ACTION
        and isinstance(binding, dict)
        and isinstance(binding.get("work_follow_up"), dict)
    )


def is_work_follow_up(op: Any) -> bool:
    if not isinstance(op, dict) or op.get("action") != ACTION:
        return False
    if is_work_target(ACTION, op.get("target")):
        return True
    refs = op.get("external_refs") if isinstance(op.get("external_refs"), dict) else {}
    if isinstance(refs.get("work_follow_up"), dict):
        return True
    adm = refs.get("admission_binding") if isinstance(refs.get("admission_binding"), dict) else {}
    return isinstance(adm.get("work_follow_up"), dict)


def _follow_up_meta(op: dict) -> dict[str, Any]:
    refs = op.get("external_refs") if isinstance(op.get("external_refs"), dict) else {}
    wf = refs.get("work_follow_up") if isinstance(refs.get("work_follow_up"), dict) else None
    if wf is None:
        adm = refs.get("admission_binding") if isinstance(refs.get("admission_binding"), dict) else {}
        wf = adm.get("work_follow_up") if isinstance(adm.get("work_follow_up"), dict) else {}
    return dict(wf or {})


def _observed_session(db, host: str, session_id: str) -> dict[str, Any]:
    if not _has_table(db, "sessions_observed"):
        return {}
    row = db.execute(
        "SELECT * FROM sessions_observed WHERE host=? AND session_id=?",
        (host, session_id),
    ).fetchone()
    if row is None:
        return {}
    row_dict = dict(row)
    body: dict[str, Any] = {}
    if row_dict.get("body"):
        try:
            parsed = json.loads(row_dict["body"])
            if isinstance(parsed, dict):
                body = parsed
        except (TypeError, ValueError):
            body = {}
    merged = {**body}
    for k, v in row_dict.items():
        if k != "body" and v is not None:
            merged[k] = v
    return merged


def _is_session_ended(obs: dict[str, Any]) -> bool:
    if not obs:
        return False
    if obs.get("gone_at") is not None or bool(obs.get("gone")):
        return True
    rt = obs.get("runtime_status")
    return isinstance(rt, str) and rt in ENDED_RUNTIME_STATUSES


def _has_pending_gate(obs: dict[str, Any]) -> bool:
    if not obs:
        return False
    pending = obs.get("pending")
    if pending is not None and pending != {} and pending != []:
        return True
    pending_json = obs.get("pending_json")
    if isinstance(pending_json, str) and pending_json.strip():
        try:
            parsed = json.loads(pending_json)
            return bool(parsed)
        except (TypeError, ValueError):
            return True
    return False


def _explicit_stream_flag(obs: dict[str, Any]) -> bool | None:
    """True/False when streaming was observed; None when the flag is absent."""
    saw_false = False
    for key in ("streaming", "isStreaming"):
        if key not in obs or obs[key] is None:
            continue
        value = obs[key]
        if value is True or value == 1:
            return True
        if value is False or value == 0:
            saw_false = True
    return False if saw_false else None


def _is_streaming_or_busy(obs: dict[str, Any]) -> bool:
    if not obs:
        return False
    flag = _explicit_stream_flag(obs)
    if flag is not None:
        return flag
    rt = obs.get("runtime_status")
    return isinstance(rt, str) and rt in BUSY_RUNTIME_STATUSES


def _frame_idle_block(meta: Any, state: Any) -> str | None:
    """Classify the final-frame meta and state reads.

    Idle requires both values to be dictionaries whose ``isStreaming`` is
    exactly ``False`` and no truthy ``service.SESSION_WAITING_FIELDS`` value.
    ``queuedMessageCount`` of 0 is not a signal. A waiting signal is more
    specific than an unreadable or streaming read. Anything else, including
    null, a missing field, or a non-boolean ``isStreaming``, is busy.
    """
    waiting = isinstance(state, dict) and any(state.get(key) for key in service.SESSION_WAITING_FIELDS)
    idle = (
        isinstance(meta, dict)
        and meta.get("isStreaming") is False
        and isinstance(state, dict)
        and state.get("isStreaming") is False
        and not waiting
    )
    if idle:
        return None
    if waiting:
        return "waiting"
    return "busy"


def _refuse_unidle_frame(kind: str, schedule: str) -> None:
    if kind == "waiting":
        if schedule == "after_current":
            raise Wait(
                status="waiting_external",
                reason="waiting_permission_or_question_gate",
                delay_s=WORK_FOLLOW_UP_HEAD_POLL_S,
            )
        raise _bad(
            "PM_WORK_SESSION_WAITING_INPUT",
            "session is waiting for permission or question input",
            409,
        )
    if schedule == "after_current":
        raise Wait(
            status="waiting_external",
            reason="session_busy_at_frame",
            delay_s=WORK_FOLLOW_UP_HEAD_POLL_S,
        )
    raise _bad("PM_WORK_SESSION_BUSY", "session is not idle", 409)


def _session_kind(
    db,
    host: str,
    session_id: str,
    reg: dict[str, Any] | None = None,
    ctx: dict[str, Any] | None = None,
) -> str | None:
    obs = _observed_session(db, host, session_id)
    for cand in (
        obs.get("kind"),
        obs.get("agent_kind"),
        service.agent_kind(obs.get("agent_preset") or obs.get("agentPreset")),
        (reg or {}).get("kind"),
        (reg or {}).get("agent_kind"),
        service.agent_kind((reg or {}).get("agent_preset") or (reg or {}).get("agentPreset")),
        ((ctx or {}).get("intent") or {}).get("agent"),
        ((ctx or {}).get("view") or {}).get("agent"),
    ):
        if isinstance(cand, str) and cand in {"claude", "codex"}:
            return cand
    return None


def _has_turn_boundary_receipt(
    db,
    workspace_id: str,
    attempt_id: str,
    turn_marker: str | None,
) -> bool:
    if not turn_marker or not _has_table(db, "session_results"):
        return False
    from . import pm_attempt_results

    rows = db.execute(
        """SELECT source_position, document FROM session_results
           WHERE workspace_id=? AND attempt_id=?
           ORDER BY sequence DESC""",
        (workspace_id, attempt_id),
    ).fetchall()
    for row in rows:
        parsed_pos = pm_attempt_results.parse_source_position(row["source_position"] or "")
        matched_marker = parsed_pos.get("turn_marker") == turn_marker
        try:
            doc = json.loads(row["document"] or "{}")
        except (TypeError, ValueError):
            doc = {}
        if not isinstance(doc, dict):
            doc = {}
        if not matched_marker and doc.get("turn_marker") == turn_marker:
            matched_marker = True
        if not matched_marker:
            continue
        turn_prog = doc.get("turn_progress") if isinstance(doc.get("turn_progress"), dict) else {}
        boundary = doc.get("boundary") or turn_prog.get("boundary")
        if boundary in {"turn_done", "agent_error"}:
            return True
    return False


def _initial_attempt_turn_marker(db, attempt_id: str, fallback: str | None = None) -> str | None:
    if isinstance(fallback, str) and fallback.strip():
        return fallback.strip()
    if not _has_table(db, "work_attempts"):
        return None
    row = db.execute(
        "SELECT start_operation_id, origin_id FROM work_attempts WHERE attempt_id=?",
        (attempt_id,),
    ).fetchone()
    if row is None:
        return None
    # work.start records its execution in the immutable handoff child relation;
    # execution_operation_id is a read projection, not a work_attempts column.
    start_op_id = row["start_operation_id"] or row["origin_id"]
    if not start_op_id or not _has_table(db, "pm_work_start_children"):
        return None
    child = db.execute(
        "SELECT operation_id FROM pm_work_start_children WHERE parent_id=? AND slot='handoff'",
        (start_op_id,),
    ).fetchone()
    if child is None:
        return None
    exec_op_id = child["operation_id"]
    op_row = db.execute(
        "SELECT result FROM operations WHERE operation_id=?",
        (exec_op_id,),
    ).fetchone()
    if op_row is not None and op_row["result"]:
        try:
            res = json.loads(op_row["result"])
            if isinstance(res, dict) and isinstance(res.get("message_id"), str) and res["message_id"].strip():
                return res["message_id"].strip()
        except (TypeError, ValueError):
            pass
    step_row = db.execute(
        "SELECT response, request FROM operation_steps WHERE operation_id=? AND name='send'",
        (exec_op_id,),
    ).fetchone()
    if step_row is not None:
        for col in ("response", "request"):
            if step_row[col]:
                try:
                    data = json.loads(step_row[col])
                    if isinstance(data, dict) and isinstance(data.get("message_id"), str) and data["message_id"].strip():
                        return data["message_id"].strip()
                except (TypeError, ValueError):
                    pass
    return None


def receipt_state(op: dict[str, Any], step: dict[str, Any] | str | None = None) -> ReceiptState:
    """Derive the P5 input receipt state and cancellability from SQLite fence, op status and send step (§8.2)."""
    refs = op.get("external_refs") if isinstance(op.get("external_refs"), dict) else {}
    send_sent = op["send_sent"] if "send_sent" in op else refs.get("send_sent")
    op_status = op.get("status")
    error_code = op.get("error_code")
    step_status = step.get("status") if isinstance(step, dict) else (step if isinstance(step, str) else None)

    # Order 1: cancelled (proven unsent before fence)
    if send_sent is not True and (
        op_status == "cancelled" or (op_status == "failed" and error_code == "CANCELLED")
    ):
        return ReceiptState("cancelled", cancellable=False)

    # Order 2: failed (proven unsent before fence, or BAT explicitly replied accepted: false)
    if (send_sent is not True and op_status == "failed" and error_code != "CANCELLED") or (
        send_sent is True and op_status == "failed" and error_code == "NOT_ACCEPTED"
    ):
        return ReceiptState("failed", cancellable=False)

    # Order 3: sent (crossed fence and operation/step succeeded)
    if send_sent is True and (
        op_status == "succeeded" or (op_status == "running" and step_status == "succeeded")
    ):
        return ReceiptState("sent", cancellable=False)

    # Order 4: unknown (crossed fence with unproven outcome, or defensive fallback)
    if (
        send_sent is True
        and (
            (op_status == "running" and step_status == "uncertain")
            or op_status
            in {"accepted", "waiting_checks", "waiting_external", "needs_attention", "uncertain", "cancelled"}
            or (op_status == "failed" and error_code != "NOT_ACCEPTED")
        )
    ) or (
        send_sent is not True
        and (
            op_status in {"uncertain", "needs_attention", "succeeded"}
            or (
                op_status in {"accepted", "running", "waiting_checks", "waiting_external"}
                and step_status in {"uncertain", "succeeded"}
            )
        )
    ):
        return ReceiptState("unknown", cancellable=False)

    # Order 5: sending (crossed fence and currently in flight)
    if (
        send_sent is True
        and op_status == "running"
        and (step_status is None or step_status in {"started", "failed"})
    ):
        return ReceiptState("sending", cancellable=False)

    # Order 6: queued (before fence, waiting for turn boundary or predecessor)
    if (
        send_sent is not True
        and op_status in {"accepted", "running", "waiting_checks", "waiting_external"}
        and (step_status is None or step_status in {"started", "failed"})
    ):
        return ReceiptState("queued", cancellable=True)

    return ReceiptState("unknown", cancellable=False)


def _fence_sent_from_row(op_row: dict[str, Any] | None) -> Any:
    if not isinstance(op_row, dict):
        return None
    refs = op_row.get("external_refs")
    if isinstance(refs, str):
        try:
            refs = json.loads(refs)
        except (TypeError, ValueError):
            refs = {}
    if not isinstance(refs, dict):
        return None
    return refs.get("send_sent")


def fence_sent(ctx: OpContext) -> Any:
    row = ctx.service._row(ctx.operation_id) or {}
    return _fence_sent_from_row(row)


def unsent(ctx: OpContext) -> bool:
    return fence_sent(ctx) is False


def latest_sent_turn(db, attempt_id: str) -> tuple[str | None, str | None]:
    """Return `(message_id, operation_id)` of the highest-position follow-up that may have started a turn.

    An operation that ended ``NOT_ACCEPTED`` crossed the fence and was explicitly
    rejected, so no turn started (§8.2). ``FOLLOW_UP_PROMPT_UNPROVEN`` still counts.
    """
    if not installed(db):
        return None, None
    rows = db.execute(
        """SELECT wi.operation_id, wi.message_id, wi.position,
                  op.status, op.error_code, op.external_refs,
                  st.status AS step_status
           FROM work_inputs wi
           JOIN operations op ON op.operation_id = wi.operation_id
           LEFT JOIN operation_steps st ON st.operation_id = wi.operation_id AND st.name = 'send'
           WHERE wi.attempt_id = ?
           ORDER BY wi.position DESC""",
        (attempt_id,),
    ).fetchall()
    for row in rows:
        if row["error_code"] == "NOT_ACCEPTED":
            continue
        sent = _fence_sent_from_row(dict(row))
        st = row["status"]
        # Pre-fence cancelled/failed rows do not count: the turn never started.
        # Unknown and unproven rows do, because that turn may have started.
        if sent is True or st in {"succeeded", "uncertain", "needs_attention"}:
            return str(row["message_id"]), str(row["operation_id"])
    return None, None


def _evaluate_queue_state(
    db,
    *,
    workspace_id: str,
    attempt_id: str,
    host: str,
    session_id: str,
    position: int,
    initial_turn_marker: str | None = None,
) -> dict[str, Any]:
    """Evaluate dynamic predecessor status and effective predecessor turn marker from local SQLite (§5.1)."""
    pred_rows = (
        db.execute(
            """SELECT wi.operation_id, wi.attempt_id, wi.position, wi.message_id,
                      op.status, op.error_code, op.external_refs,
                      st.status AS step_status
               FROM work_inputs wi
               JOIN operations op ON op.operation_id = wi.operation_id
               LEFT JOIN operation_steps st ON st.operation_id = wi.operation_id AND st.name = 'send'
               WHERE wi.host = ? AND wi.session_id = ? AND wi.position < ?
               ORDER BY wi.position ASC""",
            (host, session_id, position),
        ).fetchall()
        if installed(db)
        else []
    )

    blocked_uncertain_op_id: str | None = None
    waiting_pred_op_id: str | None = None
    effective_marker: str | None = None

    for r in pred_rows:
        r_dict = dict(r)
        if r_dict.get("external_refs") and isinstance(r_dict["external_refs"], str):
            try:
                r_dict["external_refs"] = json.loads(r_dict["external_refs"])
            except (TypeError, ValueError):
                r_dict["external_refs"] = {}
        r_state = receipt_state(r_dict, r_dict.get("step_status"))
        if str(r["attempt_id"]) == attempt_id and r["status"] == "succeeded":
            effective_marker = str(r["message_id"])
        if r["status"] not in {"succeeded", "failed", "cancelled"}:
            if r_state == "unknown" or r["status"] in {"uncertain", "needs_attention"} or r["step_status"] == "uncertain":
                if blocked_uncertain_op_id is None:
                    blocked_uncertain_op_id = str(r["operation_id"])
            else:
                if waiting_pred_op_id is None:
                    waiting_pred_op_id = str(r["operation_id"])

    if effective_marker is None:
        effective_marker = _initial_attempt_turn_marker(db, attempt_id, fallback=initial_turn_marker)

    if blocked_uncertain_op_id is not None:
        return {
            "is_head": False,
            "blocked_by_operation_id": blocked_uncertain_op_id,
            "wait_reason": "predecessor_outcome_uncertain",
            "delay_s": WORK_FOLLOW_UP_FOLLOWER_FALLBACK_S,
            "effective_predecessor_turn_marker": effective_marker,
        }
    if waiting_pred_op_id is not None:
        return {
            "is_head": False,
            "blocked_by_operation_id": waiting_pred_op_id,
            "wait_reason": "waiting_predecessor_input",
            "delay_s": WORK_FOLLOW_UP_FOLLOWER_FALLBACK_S,
            "effective_predecessor_turn_marker": effective_marker,
        }
    return {
        "is_head": True,
        "blocked_by_operation_id": None,
        "wait_reason": None,
        "delay_s": WORK_FOLLOW_UP_HEAD_POLL_S,
        "effective_predecessor_turn_marker": effective_marker,
    }


def admit_send(
    ops: OperationService,
    principal: Principal,
    target: dict[str, Any],
    params: dict[str, Any],
    pre: dict[str, Any],
) -> dict[str, Any]:
    """Validate and admit a Work-targeted `session.send` operation (§2, §3, §5.4)."""
    schema(ops.journal)
    if not isinstance(target, dict) or set(target) != {"workspace_id", "work_item_id"}:
        raise _bad("INVALID_TARGET", "Exact workspace_id and work_item_id are required", 422)
    wid, wi_id = target.get("workspace_id"), target.get("work_item_id")
    if not isinstance(wid, str) or not wid.strip() or not isinstance(wi_id, str) or not work_items.WORK_ITEM_ID.fullmatch(wi_id):
        raise _bad("INVALID_TARGET", "workspace_id and a valid work_item_id are required", 422)

    workspace = pm_workspace.require_member(ops.db, principal, wid, write=True)

    work = work_items._get_item(ops.db, wi_id, active=True)
    work_items._get_project(ops.db, work["project_id"], active=True)
    if not ops.db.execute(
        "SELECT 1 FROM pm_project_bindings WHERE workspace_id=? AND project_id=?",
        (wid, work["project_id"]),
    ).fetchone():
        raise _bad("PM_PROJECT_BINDING_REQUIRED", "Explicitly bind this work's project first", 409)
    pm_source_lineage.require(ops.db, principal, "work_item", wi_id)

    if not isinstance(params, dict):
        raise _bad("INVALID_PARAMS", "params must be an object", 422)
    if "queue" in params:
        raise _bad(
            "INVALID_PARAMS",
            "Work-targeted session.send uses schedule ('after_current' or 'now'), not queue",
            422,
        )
    if "message_id" in params:
        raise _bad(
            "INVALID_PARAMS",
            "Work-targeted session.send assigns message_id deterministically as batc-<operation_id>",
            422,
        )
    if set(params) - {"text", "schedule"}:
        raise _bad("INVALID_PARAMS", "Work-targeted session.send only accepts text and schedule", 422)
    text = params.get("text")
    if not isinstance(text, str) or not text.strip() or len(text) > service.MAX_PROMPT_CHARS:
        raise _bad("INVALID_PARAMS", f"text must be 1-{service.MAX_PROMPT_CHARS} characters", 422)

    schedule = params.get("schedule", DEFAULT_SCHEDULE)
    if schedule is None:
        schedule = DEFAULT_SCHEDULE
    if not isinstance(schedule, str) or schedule not in SCHEDULES:
        raise _bad("INVALID_PARAMS", "schedule must be 'after_current' or 'now'", 422)

    if not isinstance(pre, dict) or set(pre) - {"expected_attempt_id"}:
        raise _bad("INVALID_PRECONDITIONS", "preconditions only accepts expected_attempt_id", 422)
    has_expected_attempt = "expected_attempt_id" in pre and pre["expected_attempt_id"] is not None
    expected_attempt_id = pre.get("expected_attempt_id")
    if has_expected_attempt and (
        not isinstance(expected_attempt_id, str) or not pm_work_start.ATTEMPT_ID.fullmatch(expected_attempt_id)
    ):
        raise _bad("INVALID_PRECONDITIONS", "expected_attempt_id must be a valid attempt ID", 422)

    # Work ownership uses the workspace-scoped binding. The managed session row was
    # stamped by repository.continue under binding(principal) with no pm_workspace_id.
    # require_entry rejects a workspace-scoped actual authority in that case
    # (CONTROLLER_WORKSPACE_CHANGED), so the session check uses the unscoped binding.
    controller = controller_authority.binding(ops.db, principal, pm_workspace_id=wid)
    session_authority = controller_authority.binding(ops.db, principal)
    pm_work_start.require_work_controller(ops.db, wid, wi_id, controller)
    if has_expected_attempt:
        pm_work_start.require_expected_latest_attempt(ops.db, wid, wi_id, expected_attempt_id)
    pm_work_start.require_latest_attempt_settled(ops.db, wid, wi_id)

    attempt_id = pm_work_start.latest_attempt_id(ops.db, wid, wi_id)
    if not attempt_id:
        raise _bad("PM_WORK_NOT_STARTED", "Work item has no started attempt", 409)
    att_row = ops.db.execute(
        "SELECT * FROM work_attempts WHERE workspace_id=? AND work_item_id=? AND attempt_id=?",
        (wid, wi_id, attempt_id),
    ).fetchone()
    if att_row is None:
        raise _bad("PM_WORK_NOT_STARTED", "Work item has no started attempt", 409)
    view = pm_work_start._attempt_view(ops, att_row)
    if view.get("stage") != "started":
        raise _bad("PM_WORK_NOT_STARTED", "Latest work attempt is not in the started stage", 409)

    from . import pm_attempt_result_reads

    ctx = pm_attempt_result_reads._attempt_context(ops, principal, wid, attempt_id)
    sess_host = ctx["sess_host"]
    sess_id = ctx["sess_id"]
    if not sess_host or not sess_id:
        raise _bad("PM_WORK_NOT_STARTED", "Latest work attempt has no started session", 409)

    fleet = ops.context["fleet"]
    if sess_host not in fleet.config.hosts:
        raise _bad("UNKNOWN_HOST", f"unknown host {sess_host!r}", 404)
    if not fleet.writes_enabled(sess_host):
        raise _bad("TIER_DISABLED", f"the write tier is off for host {sess_host}", 403)

    inventory = ops.context.get("inventory")
    inv_row = inventory.get_session(sess_host, sess_id) if inventory else None
    obs = _observed_session(ops.db, sess_host, sess_id)
    has_tab = bool((inv_row and inv_row.get("has_tab")) or obs.get("has_tab"))
    reg_entry = registry.get(sess_host, sess_id)
    if inv_row is None and not obs and reg_entry is None:
        raise _bad("UNKNOWN_READ_ONLY", "the session is not in the inventory or the connector registry", 403)

    verdict = resource_policy.classify_row_for_read(
        fleet.config.host(sess_host),
        sess_id,
        has_tab=has_tab,
        entries=registry.list_entries(sess_host),
    )
    if verdict["api_access"] != "managed":
        raise _bad(
            verdict.get("read_only_code", "READ_ONLY"),
            f"session {sess_id[:8]} is {verdict['provenance']} and read-only through the API",
            403,
        )

    ok_reg, _reg_reason, reg = pm_attempt_result_reads._check_managed_session_registry(ops, ctx)
    provenance = reg.get("provenance") if isinstance(reg, dict) else None
    # Registry has start_operation_id, not created_by_operation_id.
    # _check_managed_session_registry already requires start_operation_id == handoff id.
    # Manual and unknown use resource_policy's names. A missing provenance is not
    # manual: registry rows do not carry that field, and classify_row_for_read
    # already refused a read-only session above.
    if (
        not ok_reg
        or reg is None
        or provenance in {resource_policy.MANUAL, resource_policy.UNKNOWN}
    ):
        raise _bad(
            "PM_WORK_SESSION_NOT_MANAGED",
            "The attempt session is not a managed session created by this work attempt",
            409,
        )

    with controller_authority.use(session_authority, ops.journal):
        try:
            owner = controller_authority.require_entry(reg)
        except ResourceReadOnly as exc:
            raise _bad(exc.code, str(exc), 403) from None

    pm_source_lineage.require_session(ops.db, principal, sess_host, sess_id)

    kind = _session_kind(ops.db, sess_host, sess_id, reg=reg, ctx=ctx)
    if kind == "codex":
        raise _bad(
            "PM_WORK_CODEX_FOLLOW_UP_UNSUPPORTED",
            "BAT Codex sessions do not echo clientMessageId or correlated turn boundaries; Work-targeted follow-up requires correlated turn completion",
            409,
        )

    if _is_session_ended(obs):
        raise _bad("PM_WORK_SESSION_ENDED", "The work session has already ended", 409)

    handoff = ctx.get("handoff") if isinstance(ctx.get("handoff"), dict) else {}
    handoff_res = handoff.get("result") if isinstance(handoff.get("result"), dict) else {}
    initial_turn_marker = str(handoff_res.get("message_id") or ctx.get("turn_marker") or "")

    if schedule == "now":
        _require_idle_for_now(
            ops.db,
            workspace_id=wid,
            attempt_id=attempt_id,
            host=sess_host,
            session_id=sess_id,
            initial_turn_marker=initial_turn_marker,
        )

    return {
        "principal": pm_workspace.principal_binding(principal),
        "pm_lineage": {"workspace_id": wid},
        "controller_authority": session_authority,
        "session_controller": {
            "host": sess_host,
            "session_id": sess_id,
            "controller_incarnation": owner["controller_incarnation"],
        },
        "resolved_target": {"host": sess_host, "session_id": sess_id},
        "work_follow_up": {
            "workspace_id": wid,
            "server_id": workspace["server_id"],
            "work_item_id": wi_id,
            "attempt_id": attempt_id,
            "attempt_sequence": int(att_row["sequence"]),
            "start_operation_id": str(ctx["start_op_id"]),
            "execution_operation_id": str(ctx["execution_op_id"]),
            "initial_turn_marker": initial_turn_marker,
            "host": sess_host,
            "session_id": sess_id,
            "schedule": schedule,
            "expected_attempt_id": expected_attempt_id if has_expected_attempt else attempt_id,
            "has_expected_attempt_id": has_expected_attempt,
        },
    }


def _require_idle_for_now(
    db,
    *,
    workspace_id: str,
    attempt_id: str,
    host: str,
    session_id: str,
    initial_turn_marker: str | None = None,
    exclude_operation_id: str | None = None,
) -> None:
    """Enforce the 3 local idle invariants required by `schedule='now'` (§3 point 4)."""
    obs = _observed_session(db, host, session_id)
    if _is_session_ended(obs):
        raise _bad("PM_WORK_SESSION_ENDED", "The work session has already ended", 409)
    if _has_pending_gate(obs):
        raise _bad(
            "PM_WORK_SESSION_WAITING_INPUT",
            "Session is waiting for permission or question input",
            409,
        )
    if installed(db):
        sql = """SELECT wi.operation_id FROM work_inputs wi
                 JOIN operations op ON op.operation_id = wi.operation_id
                 WHERE wi.host=? AND wi.session_id=?
                   AND op.status NOT IN ('succeeded', 'failed', 'cancelled')"""
        args: list[Any] = [host, session_id]
        if exclude_operation_id is not None:
            sql += " AND wi.operation_id != ?"
            args.append(exclude_operation_id)
        if db.execute(sql + " LIMIT 1", tuple(args)).fetchone() is not None:
            raise _bad(
                "PM_WORK_SESSION_BUSY",
                "Queued or active follow-up instructions exist for this session",
                409,
            )
    if _is_streaming_or_busy(obs):
        raise _bad("PM_WORK_SESSION_BUSY", "Session is currently streaming a turn", 409)

    # Effective predecessor turn marker must have a boundary receipt in session_results
    pred_marker: str | None = None
    if installed(db):
        row = db.execute(
            """SELECT wi.message_id FROM work_inputs wi
               JOIN operations op ON op.operation_id = wi.operation_id
               WHERE wi.attempt_id=? AND op.status='succeeded'
               ORDER BY wi.position DESC LIMIT 1""",
            (attempt_id,),
        ).fetchone()
        if row is not None:
            pred_marker = str(row["message_id"])
    if pred_marker is None:
        pred_marker = _initial_attempt_turn_marker(db, attempt_id, fallback=initial_turn_marker)
    if not _has_turn_boundary_receipt(db, workspace_id, attempt_id, pred_marker):
        raise _bad(
            "PM_WORK_SESSION_BUSY",
            "Current turn has not yet recorded a completion boundary in session_results",
            409,
        )


def admit_operation(
    db,
    operation_id: str,
    action: str,
    target: dict[str, Any],
    params: dict[str, Any],
    preconditions: dict[str, Any],
    binding: dict[str, Any] | None,
    now: float,
) -> None:
    """Re-verify invariants and atomically allocate `work_inputs.position` inside `OperationService.create` tx (§4.3)."""
    if not is_work_follow_up_binding(action, binding):
        return
    schema_db(db)
    assert binding is not None
    wf = dict(binding["work_follow_up"])
    wid = wf["workspace_id"]
    wi_id = wf["work_item_id"]
    attempt_id = wf["attempt_id"]
    host = wf["host"]
    sid = wf["session_id"]
    schedule = wf["schedule"]
    controller = binding["controller_authority"]

    pm_work_start.require_work_controller(db, wid, wi_id, controller)
    if wf.get("has_expected_attempt_id"):
        pm_work_start.require_expected_latest_attempt(db, wid, wi_id, wf.get("expected_attempt_id"))
    pm_work_start.require_latest_attempt_settled(db, wid, wi_id)

    current_latest = pm_work_start.latest_attempt_id(db, wid, wi_id)
    if current_latest != attempt_id:
        raise _bad(
            "PM_WORK_ATTEMPT_CHANGED" if wf.get("has_expected_attempt_id") else "PM_WORK_NOT_STARTED",
            "The latest work attempt changed before admission committed",
            409,
        )
    exec_row = db.execute(
        "SELECT status FROM operations WHERE operation_id=?",
        (wf["execution_operation_id"],),
    ).fetchone()
    if exec_row is None or exec_row["status"] != "succeeded":
        raise _bad("PM_WORK_NOT_STARTED", "Latest work attempt is not in the started stage", 409)

    if schedule == "now":
        _require_idle_for_now(
            db,
            workspace_id=wid,
            attempt_id=attempt_id,
            host=host,
            session_id=sid,
            initial_turn_marker=wf.get("initial_turn_marker"),
            exclude_operation_id=operation_id,
        )

    pos_row = db.execute(
        "SELECT COALESCE(MAX(position), 0) + 1 AS next_pos FROM work_inputs WHERE host=? AND session_id=?",
        (host, sid),
    ).fetchone()
    position = int(pos_row["next_pos"])
    message_id = f"batc-{operation_id}"

    db.execute(
        """INSERT INTO work_inputs (
            operation_id, workspace_id, work_item_id, attempt_id,
            host, session_id, position, schedule, message_id, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            operation_id,
            wid,
            wi_id,
            attempt_id,
            host,
            sid,
            position,
            schedule,
            message_id,
            float(now),
        ),
    )

    wf_persisted = {
        **wf,
        "position": position,
        "message_id": message_id,
    }
    op_row = db.execute(
        "SELECT external_refs FROM operations WHERE operation_id=?",
        (operation_id,),
    ).fetchone()
    refs = json.loads(op_row["external_refs"]) if (op_row and op_row["external_refs"]) else {}
    if isinstance(refs.get("admission_binding"), dict):
        refs["admission_binding"]["work_follow_up"] = wf_persisted
    refs["resolved_target"] = {"host": host, "session_id": sid}
    refs["work_follow_up"] = wf_persisted
    db.execute(
        "UPDATE operations SET external_refs=? WHERE operation_id=?",
        (json.dumps(refs, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str), operation_id),
    )


def prepare_send(ctx: OpContext) -> dict[str, Any]:
    """Run local SQLite predecessor and turn-boundary checks before entering `ctx.step('send', ...)` (§5.1).

    Zero BAT RPC calls occur here. If the operation already crossed the transport fence (`send_sent is True`)
    or has a `send` step in `succeeded`/`uncertain`, skip pre-send waiting so `OpContext.step` can replay or reconcile.
    """
    db = ctx.service.db
    fresh_op = ctx.service._row(ctx.operation_id) or ctx.op
    sent = _fence_sent_from_row(fresh_op)
    step_row = db.execute(
        "SELECT status FROM operation_steps WHERE operation_id=? AND name='send'",
        (ctx.operation_id,),
    ).fetchone()
    step_status = step_row["status"] if step_row is not None else None

    wf = _follow_up_meta(fresh_op)
    wi_row = db.execute(
        "SELECT * FROM work_inputs WHERE operation_id=?",
        (ctx.operation_id,),
    ).fetchone()
    if wi_row is not None:
        wid = str(wi_row["workspace_id"])
        wi_id = str(wi_row["work_item_id"])
        attempt_id = str(wi_row["attempt_id"])
        host = str(wi_row["host"])
        sid = str(wi_row["session_id"])
        position = int(wi_row["position"])
        schedule = str(wi_row["schedule"])
        message_id = str(wi_row["message_id"])
    else:
        wid = str(wf["workspace_id"])
        wi_id = str(wf["work_item_id"])
        attempt_id = str(wf["attempt_id"])
        host = str(wf["host"])
        sid = str(wf["session_id"])
        position = int(wf.get("position") or 1)
        schedule = str(wf.get("schedule") or DEFAULT_SCHEDULE)
        message_id = str(wf.get("message_id") or f"batc-{ctx.operation_id}")

    if sent is True or step_status in {"succeeded", "uncertain"}:
        return {
            "workspace_id": wid,
            "work_item_id": wi_id,
            "attempt_id": attempt_id,
            "host": host,
            "session_id": sid,
            "position": position,
            "schedule": schedule,
            "message_id": message_id,
        }

    qstate = _evaluate_queue_state(
        db,
        workspace_id=wid,
        attempt_id=attempt_id,
        host=host,
        session_id=sid,
        position=position,
        initial_turn_marker=wf.get("initial_turn_marker"),
    )
    if not qstate["is_head"]:
        if schedule == "now":
            raise _bad(
                "PM_WORK_SESSION_BUSY",
                "Another follow-up instruction is ahead in the session queue",
                409,
            )
        raise Wait(
            status="waiting_external",
            reason=qstate["wait_reason"],
            delay_s=qstate["delay_s"],
        )

    obs = _observed_session(db, host, sid)
    if _is_session_ended(obs):
        raise _bad(
            "PM_WORK_SESSION_ENDED",
            "session ended before queued follow-up could be sent",
            409,
        )

    has_pending = _has_pending_gate(obs)
    is_streaming = _is_streaming_or_busy(obs)
    has_boundary = _has_turn_boundary_receipt(
        db,
        wid,
        attempt_id,
        qstate["effective_predecessor_turn_marker"],
    )

    if has_pending or is_streaming or not has_boundary:
        if schedule == "now":
            if has_pending:
                raise _bad(
                    "PM_WORK_SESSION_WAITING_INPUT",
                    "session is waiting for permission or question input",
                    409,
                )
            raise _bad("PM_WORK_SESSION_BUSY", "session is currently busy or turn boundary is not recorded", 409)
        reason = "waiting_permission_or_question_gate" if has_pending else "waiting_turn_boundary"
        raise Wait(
            status="waiting_external",
            reason=reason,
            delay_s=WORK_FOLLOW_UP_HEAD_POLL_S,
        )

    # Check work item is still active before dispatching a new frame.
    # Writer membership is re-checked here, before ctx.step, so a removed member
    # fails with require_member's NOT_FOUND rather than the lineage effect guard
    # that runs when the step starts. The send gate checks again at the frame.
    work_items._get_item(db, wi_id, active=True)
    _require_current_writer(ctx)

    return {
        "workspace_id": wid,
        "work_item_id": wi_id,
        "attempt_id": attempt_id,
        "host": host,
        "session_id": sid,
        "position": position,
        "schedule": schedule,
        "message_id": message_id,
        "effective_predecessor_turn_marker": qstate["effective_predecessor_turn_marker"],
    }


async def execute_send_step(
    ctx: OpContext,
    *,
    fleet,
    host: str,
    sid: str,
    text: str,
    mid: str,
    schedule: str,
    target: dict[str, Any],
    check_controller_binding_fn,
) -> dict[str, Any]:
    """Execute `service.session_send` wrapped with the monotonic proven-unsent transport fence (§6.1, §6.2)."""
    if fence_sent(ctx) is True:
        raise AmbiguousOutcome("follow-up send fence is already set; refusing to resend")
    ctx.set_refs(send_sent=False)
    incarnation_holder: dict[str, str | None] = {"incarnation": None}

    def _before_send_gate() -> None:
        ctx.check_cancel()
        incarnation_holder["incarnation"] = check_controller_binding_fn(ctx, target)
        pm_lifecycle_gates.require_local(ctx.service.db, ctx.operation_id)
        _require_current_writer(ctx)

    def _on_streaming(meta: dict[str, Any]) -> None:
        if schedule == "after_current":
            raise Wait(
                status="waiting_external",
                reason="session_busy_at_frame",
                delay_s=WORK_FOLLOW_UP_HEAD_POLL_S,
            )
        raise _bad("PM_WORK_SESSION_BUSY", "session is currently streaming a turn", 409)

    async def _before_frame() -> None:
        # Both reads finish before the synchronous gate. before_send still runs
        # after this returns and immediately before the transport fence.
        client = fleet.client(host)
        fresh_meta = await client.guard_read("claude:get-session-meta", {"sessionId": sid})
        fresh_state = await client.guard_read("claude:get-session-state", {"sessionId": sid})
        if block := _frame_idle_block(fresh_meta, fresh_state):
            _refuse_unidle_frame(block, schedule)

    def _on_transport() -> None:
        ctx.set_refs(send_sent=True)

    try:
        incarnation_holder["incarnation"] = check_controller_binding_fn(ctx, target)
        r = await service.session_send(
            fleet,
            host,
            sid,
            text,
            confirm=True,
            message_id=mid,
            ensure_loaded=False,
            queue=False,
            tool="api:" + ctx.actor,
            retry_on_disconnect=False,
            before_invoke=_before_send_gate,
            control_version=ctx.effective_preconditions.get("control_version"),
            operation_id=ctx.operation_id,
            _exact_session_id=True,
            _before_frame=_before_frame,
            _controller_incarnation=incarnation_holder["incarnation"],
            _on_transport=_on_transport,
            _on_streaming=_on_streaming,
            _on_unloaded=lambda: _refuse_unidle_frame("busy", schedule),
        )
    except Exception as exc:  # noqa: BLE001 - classified strictly by the fresh SQLite transport fence
        sent = fence_sent(ctx)
        if sent is True:
            raise AmbiguousOutcome(redact(f"{type(exc).__name__}: {exc}")) from exc
        if isinstance(exc, Wait):
            raise
        if isinstance(exc, Cancelled):
            raise StepFailed("CANCELLED", "operation cancelled before session send") from exc
        if isinstance(exc, (*AMBIGUOUS, OSError)):
            if schedule == "after_current":
                raise Wait(
                    status="waiting_external",
                    reason="transport_unavailable_before_send",
                    delay_s=WORK_FOLLOW_UP_HEAD_POLL_S,
                ) from None
            raise StepFailed("BAT_ERROR", redact(f"{type(exc).__name__}: {exc}")) from exc
        raise

    accepted = r.get("accepted", r.get("ok")) if isinstance(r, dict) else None
    if type(accepted) is not bool:
        raise NeedsAttention("FOLLOW_UP_PROMPT_UNPROVEN", "follow-up prompt reply lacks acceptance evidence")
    if accepted is False:
        raise StepFailed("NOT_ACCEPTED", "BAT rejected the message")
    return r


def _require_current_writer(ctx: OpContext) -> None:
    """Refuse the frame unless the admitted principal is still a writer.

    The stored controller authority is unscoped, so ``require_binding`` does not
    call ``require_member``. Read visibility of the lineage is not write access.
    """
    principal = pm_workspace.admitted_principal(ctx)
    meta = _follow_up_meta(ctx.op)
    workspace_id = meta.get("workspace_id") or (ctx.op.get("target") or {}).get("workspace_id")
    pm_workspace.require_member(ctx.service.db, principal, workspace_id, write=True)


def operation_visible(db, principal: Principal, operation: dict[str, Any]) -> bool:
    """Workspace membership + source lineage visibility check for Work follow-up operations (§8.4)."""
    with _snapshot(db):
        try:
            op_id = operation.get("operation_id")
            if not isinstance(op_id, str) or not op_id:
                return False
            row = db.execute("SELECT * FROM operations WHERE operation_id=?", (op_id,)).fetchone()
            if row is None or row["action"] != ACTION:
                return False
            op = OperationService._decode(row)
            if not is_work_follow_up(op):
                return False
            if not principal.allows("observe"):
                return False
            wf = _follow_up_meta(op)
            wid = wf.get("workspace_id") or (op.get("target") or {}).get("workspace_id")
            wi_id = wf.get("work_item_id") or (op.get("target") or {}).get("work_item_id")
            if not isinstance(wid, str) or not wid:
                return False
            workspace = pm_workspace.require_member(db, principal, wid, write=False)
            if wf.get("server_id") is not None and workspace["server_id"] != wf.get("server_id"):
                return False
            if isinstance(wi_id, str) and wi_id:
                work = work_items._get_item(db, wi_id)
                if not db.execute(
                    "SELECT 1 FROM pm_project_bindings WHERE workspace_id=? AND project_id=?",
                    (wid, work["project_id"]),
                ).fetchone():
                    return False
                if not pm_source_lineage.visible(db, principal, "work_item", wi_id):
                    return False
            host = wf.get("host") or ((op.get("external_refs") or {}).get("resolved_target") or {}).get("host")
            sid = wf.get("session_id") or ((op.get("external_refs") or {}).get("resolved_target") or {}).get("session_id")
            if isinstance(host, str) and host and isinstance(sid, str) and sid:
                if not pm_source_lineage.visible(db, principal, "session", f"{host}/{sid}"):
                    return False
            return pm_source_lineage.visible(db, principal, "operation", op_id)
        except (OperationError, KeyError, TypeError, ValueError):
            return False


source_visible = operation_visible


def authorize_existing(ops: OperationService, principal: Principal, op: dict[str, Any], verb: str) -> None:
    """Enforce workspace writer membership, same controller, and original actor on replay/cancel/resume (§7.1, §9.1)."""
    if not is_work_follow_up(op):
        return
    if not operation_visible(ops.db, principal, op):
        raise _bad("NOT_FOUND", "operation not found", 404)
    wf = _follow_up_meta(op)
    wid = wf.get("workspace_id") or (op.get("target") or {}).get("workspace_id")
    wi_id = wf.get("work_item_id") or (op.get("target") or {}).get("work_item_id")
    if not isinstance(wid, str) or not isinstance(wi_id, str):
        raise _bad("NOT_FOUND", "operation not found", 404)
    pm_workspace.require_member(ops.db, principal, wid, write=True)
    controller = controller_authority.binding(ops.db, principal, pm_workspace_id=wid)
    pm_work_start.require_work_controller(ops.db, wid, wi_id, controller)
    if verb != "replay" and principal.actor != op.get("actor"):
        raise _bad("FORBIDDEN", f"{verb} requires the original operation actor", 403)


def check_cancel_allowed(ops: OperationService, principal: Principal, op: dict[str, Any]) -> None:
    """Refuse cancellation with `409 PM_WORK_CANCEL_NOT_ALLOWED_AFTER_SEND` once send has begun (§7.1)."""
    if not is_work_follow_up(op):
        return
    authorize_existing(ops, principal, op, "cancel")
    fresh = ops._row(op["operation_id"]) or op
    sent = _fence_sent_from_row(fresh)
    step_row = ops.db.execute(
        "SELECT status FROM operation_steps WHERE operation_id=? AND name='send'",
        (op["operation_id"],),
    ).fetchone()
    step_status = step_row["status"] if step_row is not None else None
    if (
        sent is True
        or fresh.get("status") in {"succeeded", "uncertain", "needs_attention"}
        or step_status in {"succeeded", "uncertain"}
    ):
        raise _bad(
            "PM_WORK_CANCEL_NOT_ALLOWED_AFTER_SEND",
            "Instruction has already entered sending; cancel cannot retract an instruction after send began.",
            409,
        )


cancel = check_cancel_allowed


def resume(ops: OperationService, principal: Principal, op: dict[str, Any]) -> None:
    """Authorize reconciling an unproven Work follow-up operation by its original ID (§6.3, §7.2)."""
    if not is_work_follow_up(op):
        return
    authorize_existing(ops, principal, op, "resume")


def wake_session_queue(ops: OperationService, host: str, session_id: str) -> str | None:
    """Wake the lowest-position `waiting_external` Work follow-up input for `(host, session_id)`."""
    if not installed(ops.db):
        return None
    rows = ops.db.execute(
        """SELECT wi.operation_id, wi.position, op.status, op.external_refs, st.status AS step_status
           FROM work_inputs wi
           JOIN operations op ON op.operation_id = wi.operation_id
           LEFT JOIN operation_steps st ON st.operation_id = wi.operation_id AND st.name = 'send'
           WHERE wi.host = ? AND wi.session_id = ?
             AND op.status NOT IN ('succeeded', 'failed', 'cancelled')
           ORDER BY wi.position ASC""",
        (host, session_id),
    ).fetchall()
    if not rows:
        return None
    head = rows[0]
    if head["status"] == "waiting_external":
        head_op_id = str(head["operation_id"])
        ops.wake(head_op_id)
        return head_op_id
    return None


def is_work_input_event(db, event: dict[str, Any]) -> bool:
    """True only for a Work-input event, never merely because a body carries workspace_id."""
    body = event.get("body") if isinstance(event.get("body"), dict) else {}
    action = str(body.get("action") or "")
    if body.get("work_follow_up") is True or is_work_target(action, body.get("target")):
        return True
    resource_id = event.get("resource_id")
    if not installed(db) or not isinstance(resource_id, str):
        return False
    return (
        db.execute(
            "SELECT 1 FROM work_inputs WHERE operation_id=?",
            (resource_id,),
        ).fetchone()
        is not None
    )


def missing_row_event_visible(db, principal: Principal, event: dict[str, Any]) -> bool:
    """Missing operation row: show a Work-input event only when this principal can see that Work."""
    if not principal.allows("observe") or not installed(db):
        return False
    resource_id = event.get("resource_id")
    if not isinstance(resource_id, str):
        return False
    row = db.execute("SELECT * FROM work_inputs WHERE operation_id=?", (resource_id,)).fetchone()
    if row is None:
        return False
    try:
        pm_workspace.require_member(db, principal, row["workspace_id"], write=False)
        if not pm_source_lineage.visible(db, principal, "work_item", row["work_item_id"]):
            return False
        return pm_source_lineage.visible(
            db, principal, "session", f"{row['host']}/{row['session_id']}",
        )
    except (OperationError, KeyError, TypeError, ValueError):
        return False


def transition_event_fields(db, operation_id: str, action: str) -> dict[str, Any]:
    """Stamp only Work follow-up transition events. Other pm_lineage actions stay unchanged."""
    if action != ACTION or not installed(db):
        return {}
    row = db.execute(
        "SELECT workspace_id FROM work_inputs WHERE operation_id=?",
        (operation_id,),
    ).fetchone()
    if row is None:
        return {}
    return {"workspace_id": str(row["workspace_id"]), "work_follow_up": True}


def on_transition(ops: OperationService, operation_id: str, new_status: str) -> None:
    """When a `work_inputs` operation settles (`succeeded`/`failed`/`cancelled`), wake the next waiting input."""
    if new_status not in {"succeeded", "failed", "cancelled"}:
        return
    if not installed(ops.db):
        return
    row = ops.db.execute(
        "SELECT host, session_id FROM work_inputs WHERE operation_id=?",
        (operation_id,),
    ).fetchone()
    if row is None:
        return
    wake_session_queue(ops, str(row["host"]), str(row["session_id"]))


def _receipt_labels(state: str, blocked_by: str | None, error_code: str | None = None) -> dict[str, dict[str, str]]:
    """English and zh-TW copy for the later UI. API error.message stays the English sentence."""
    if state == "queued" and blocked_by:
        detail_en = (
            "Queued behind an unconfirmed instruction. Open the unconfirmed instruction "
            "to reconcile with its original ID, or cancel this queued instruction."
        )
        detail_zh = "目前排在一筆結果未確認的指令之後。請開啟該筆未確認指令以原 ID 重新確認結果，或取消此筆排隊中的指令。"
    elif state == "queued":
        detail_en = (
            "Waiting for the current turn to end. Cancelling removes this instruction before it is sent to BAT."
        )
        detail_zh = "正在等待目前回合結束。此時取消會在送交 BAT 前撤回此指令。"
    elif state in {"sending", "sent"}:
        detail_en = (
            "This instruction has already been sent to the session and cannot be retracted by cancelling the operation."
        )
        detail_zh = "此指令已開始送出或已送達工作階段，無法透過取消操作撤回。"
    elif state == "unknown":
        detail_en = (
            "Delivery reply was lost after send began. Cancel cannot retract an instruction "
            "that may have reached BAT; reconcile with the original ID instead."
        )
        detail_zh = "送出後未收到回覆，無法透過取消撤回可能已送達 BAT 的指令；請以原 ID 重新確認結果。"
    elif state == "failed" and error_code == "FORBIDDEN":
        detail_en = "Your current permissions do not allow sending this instruction. It was not sent."
        detail_zh = "目前權限不允許送出此指令；指令尚未送出。"
    elif state == "failed":
        detail_en = "BAT rejected the message or the send failed before it left. Retry as a new instruction."
        detail_zh = "BAT 拒絕了此訊息，或送出前即失敗。請以新指令重試。"
    else:
        detail_en = "This queued instruction was withdrawn before it was sent. Edit it and send a new instruction."
        detail_zh = "此排隊指令在送出前已撤回。請編輯後以新指令送出。"
    titles = {
        "queued": ("Queued", "排隊中"),
        "sending": ("Sending", "送出中"),
        "sent": ("Sent", "已送出"),
        "unknown": ("Delivery unconfirmed", "結果未確認"),
        "failed": ("Failed", "失敗"),
        "cancelled": ("Cancelled", "已取消"),
    }
    title_en, title_zh = titles.get(state, ("Delivery unconfirmed", "結果未確認"))
    return {"state": {"en": title_en, "zh-TW": title_zh}, "detail": {"en": detail_en, "zh-TW": detail_zh}}


def build_input_receipt(
    db,
    op: dict[str, Any],
    wi_row: Any = None,
) -> dict[str, Any] | None:
    """Synthesize the canonical §8.1 Input Receipt from `work_inputs`, `operations` and `operation_steps(name='send')`."""
    if not is_work_follow_up(op):
        return None
    op_id = str(op["operation_id"])
    if wi_row is None and installed(db):
        wi_row = db.execute(
            "SELECT * FROM work_inputs WHERE operation_id=?",
            (op_id,),
        ).fetchone()
    wf = _follow_up_meta(op)
    if wi_row is not None:
        sequence = int(wi_row["sequence"])
        wid = str(wi_row["workspace_id"])
        wi_id = str(wi_row["work_item_id"])
        attempt_id = str(wi_row["attempt_id"])
        host = str(wi_row["host"])
        sid = str(wi_row["session_id"])
        position = int(wi_row["position"])
        schedule = str(wi_row["schedule"])
        message_id = str(wi_row["message_id"])
        created_at = float(wi_row["created_at"])
    else:
        sequence = int(wf.get("sequence") or 0)
        wid = str(wf.get("workspace_id") or (op.get("target") or {}).get("workspace_id") or "")
        wi_id = str(wf.get("work_item_id") or (op.get("target") or {}).get("work_item_id") or "")
        attempt_id = str(wf.get("attempt_id") or "")
        host = str(wf.get("host") or "")
        sid = str(wf.get("session_id") or "")
        position = int(wf.get("position") or 1)
        schedule = str(wf.get("schedule") or DEFAULT_SCHEDULE)
        message_id = str(wf.get("message_id") or f"batc-{op_id}")
        created_at = float(op.get("created_at") or 0.0)

    attempt_sequence = wf.get("attempt_sequence")
    if attempt_sequence is None and attempt_id and _has_table(db, "work_attempts"):
        att_r = db.execute(
            "SELECT sequence FROM work_attempts WHERE attempt_id=?",
            (attempt_id,),
        ).fetchone()
        if att_r is not None:
            attempt_sequence = int(att_r["sequence"])

    execution_op_id = wf.get("execution_operation_id")
    step_row = db.execute(
        "SELECT * FROM operation_steps WHERE operation_id=? AND name='send'",
        (op_id,),
    ).fetchone()
    step_dict = dict(step_row) if step_row is not None else None
    step_resp: dict[str, Any] = {}
    if step_dict and step_dict.get("response"):
        try:
            parsed_resp = json.loads(step_dict["response"])
            if isinstance(parsed_resp, dict):
                step_resp = parsed_resp
        except (TypeError, ValueError):
            step_resp = {}

    rstate = receipt_state(op, step_dict)
    state = str(rstate)
    cancellable = bool(rstate.cancellable)

    qstate = _evaluate_queue_state(
        db,
        workspace_id=wid,
        attempt_id=attempt_id,
        host=host,
        session_id=sid,
        position=position,
        initial_turn_marker=wf.get("initial_turn_marker"),
    )
    effective_pred_marker = qstate["effective_predecessor_turn_marker"]

    if state == "queued":
        blocked_by = qstate["blocked_by_operation_id"]
        if qstate["wait_reason"] is not None:
            status_reason = qstate["wait_reason"]
        else:
            status_reason = op.get("status_reason") or "waiting_turn_boundary"
    else:
        blocked_by = None
        status_reason = op.get("status_reason")

    raw_text = str((op.get("params") or {}).get("text") or "")
    text_truncated = len(raw_text) > TEXT_EXCERPT_MAX_CHARS
    text_excerpt = raw_text[:TEXT_EXCERPT_MAX_CHARS] if text_truncated else raw_text

    sent_at: float | None = None
    if state == "sent":
        if step_dict and (step_dict.get("finished_at") or step_dict.get("started_at")):
            sent_at = float(step_dict.get("finished_at") or step_dict.get("started_at"))
        elif op.get("updated_at") is not None:
            sent_at = float(op["updated_at"])

    reconciled = bool(step_resp.get("reconciled"))
    settled_by: str | None = step_resp.get("settled_by")
    if state == "sent" and not settled_by:
        settled_by = "bat_transcript" if reconciled else "direct_reply"

    next_actions: list[dict[str, Any]] = []
    if state == "queued" and cancellable:
        if not op.get('cancel_requested'):
            next_actions.append({'kind': 'action', 'action': 'pm.work_input.defer',
                'target': {'workspace_id': wid, 'work_item_id': wi_id, 'source_operation_id': op_id},
                'preconditions': {'expected_state': 'queued'},
                'required_scopes': ['observe', 'manage', 'operate'],
                'label': 'Keep for later', 'label_zh_TW': '留到稍後'})
        next_actions.append(
            {
                "kind": "operation_endpoint",
                "endpoint": "cancel",
                "operation_id": op_id,
                "http_method": "POST",
                "http_path": f"/api/v1/operations/{op_id}/cancel",
                "required_scopes": ["observe"],
                "label": "Cancel queued instruction",
                "label_zh_TW": "取消排隊中的指令",
            }
        )
        if blocked_by:
            next_actions.append(
                {
                    "kind": "operation_endpoint",
                    "endpoint": "resume",
                    "operation_id": blocked_by,
                    "http_method": "POST",
                    "http_path": f"/api/v1/operations/{blocked_by}/resume",
                    "required_scopes": ["observe"],
                    "label": "Reconcile with original ID",
                    "label_zh_TW": "以原 ID 重新確認",
                }
            )
    elif state == "unknown":
        next_actions.append(
            {
                "kind": "operation_endpoint",
                "endpoint": "resume",
                "operation_id": op_id,
                "http_method": "POST",
                "http_path": f"/api/v1/operations/{op_id}/resume",
                "required_scopes": ["observe"],
                "label": "Reconcile with original ID",
                "label_zh_TW": "以原 ID 重新確認",
            }
        )
    elif state == "failed":
        next_actions.append(
            {
                "kind": "action",
                "action": ACTION,
                "label": "Retry as new instruction",
                "label_zh_TW": "以新指令重試",
                "target": {"workspace_id": wid, "work_item_id": wi_id},
                "params": {"text": raw_text, "schedule": schedule},
                "http_method": "POST",
                "http_path": "/api/v1/operations",
                "required_scopes": ["observe", "operate"],
                "requires_new_idempotency_key": True,
            }
        )
    elif state == "cancelled":
        next_actions.append(
            {
                "kind": "action",
                "action": ACTION,
                "label": "Edit and send as new instruction",
                "label_zh_TW": "編輯並以新指令送出",
                "target": {"workspace_id": wid, "work_item_id": wi_id},
                "params": {"text": raw_text, "schedule": schedule},
                "http_method": "POST",
                "http_path": "/api/v1/operations",
                "required_scopes": ["observe", "operate"],
                "requires_new_idempotency_key": True,
            }
        )

    links: dict[str, Any] = {
        "operation": f"/api/v1/operations/{op_id}",
        "attempt_result": f"/api/v1/pm/workspaces/{wid}/work-attempts/{attempt_id}/result",
        "inputs": f"/api/v1/pm/workspaces/{wid}/work/{wi_id}/inputs",
        "operation_hash": f"#/op/{op_id}",
    }
    if blocked_by:
        links["blocked_by_operation"] = f"/api/v1/operations/{blocked_by}"
        links["blocked_by_operation_hash"] = f"#/op/{blocked_by}"

    from .pm_work_input_idea import receipt as idea_receipt
    deferred = idea_receipt(db, op_id)
    return {
        "sequence": sequence,
        **({'deferred_idea': deferred} if deferred is not None else {}),
        "operation_id": op_id,
        "workspace_id": wid,
        "work_item_id": wi_id,
        "attempt_id": attempt_id,
        "attempt_sequence": attempt_sequence,
        "execution_operation_id": execution_op_id,
        "host": host,
        "session_id": sid,
        "message_id": message_id,
        "schedule": schedule,
        "position": position,
        "state": state,
        "operation_status": op["status"],
        "status_reason": status_reason,
        "blocked_by_operation_id": blocked_by,
        "error_code": op.get("error_code"),
        "effective_predecessor_turn_marker": effective_pred_marker,
        "text_excerpt": text_excerpt,
        "text_truncated": text_truncated,
        "actor": op.get("actor"),
        "created_at": created_at,
        "updated_at": float(op.get("updated_at") or created_at),
        "sent_at": sent_at,
        "reconciled": reconciled,
        "settled_by": settled_by,
        "cancellable": cancellable,
        "next_allowed_actions": next_actions,
        "links": links,
        "labels": _receipt_labels(state, blocked_by, op.get("error_code")),
    }


def enrich_operation(db, op: dict[str, Any] | None) -> dict[str, Any] | None:
    """Attach the Input Receipt at ``operation.input_receipt`` only.

    The receipt is not merged into ``result`` and is not copied onto the
    operation. Those fields keep the operation's own values.
    """
    if op is None or not is_work_follow_up(op):
        return op
    receipt = build_input_receipt(db, op)
    if receipt is None:
        return op
    op["input_receipt"] = receipt
    return op


@_snapshot_read
def list_inputs(
    ops: OperationService,
    principal: Principal,
    workspace_id: str,
    work_item_id: str,
    *,
    attempt_id: str | None = None,
    after: int = 0,
    limit: int = 50,
) -> dict[str, Any]:
    """List Work follow-up inputs in `sequence ASC` (`position ASC`) order with visibility filtering (§8.3)."""
    if not principal.allows("observe"):
        raise _bad("FORBIDDEN", "Observing work inputs requires observe", 403)
    if type(after) is not int or not 0 <= after < 2**63:
        raise _bad("INVALID_PARAMS", "after must be a nonnegative integer", 422)
    if type(limit) is not int or not 1 <= limit <= 100:
        raise _bad("INVALID_PARAMS", "limit must be between 1 and 100", 422)
    if attempt_id is not None and (
        not isinstance(attempt_id, str) or not pm_work_start.ATTEMPT_ID.fullmatch(attempt_id)
    ):
        raise _bad("INVALID_PARAMS", "attempt_id must be a valid attempt ID", 422)

    pm_workspace.require_member(ops.db, principal, workspace_id, write=False)
    work = work_items._get_item(ops.db, work_item_id)
    if not ops.db.execute(
        "SELECT 1 FROM pm_project_bindings WHERE workspace_id=? AND project_id=?",
        (workspace_id, work["project_id"]),
    ).fetchone():
        raise _bad("WORK_ITEM_NOT_FOUND", "Work item is not bound to this workspace", 404)
    pm_source_lineage.require(ops.db, principal, "work_item", work_item_id)

    if attempt_id is not None:
        att_exists = ops.db.execute(
            "SELECT 1 FROM work_attempts WHERE workspace_id=? AND work_item_id=? AND attempt_id=?",
            (workspace_id, work_item_id, attempt_id),
        ).fetchone()
        if att_exists is None:
            raise _bad("NOT_FOUND", "Work attempt not found", 404)

    items: list[dict[str, Any]] = []
    filtered = False
    cursor = after
    has_more = False

    while len(items) <= limit:
        sql = """SELECT * FROM work_inputs
                 WHERE workspace_id=? AND work_item_id=? AND sequence>?"""
        args: list[Any] = [workspace_id, work_item_id, cursor]
        if attempt_id is not None:
            sql += " AND attempt_id=?"
            args.append(attempt_id)
        sql += " ORDER BY sequence ASC LIMIT ?"
        args.append(max(limit + 1, 32))
        rows = ops.db.execute(sql, tuple(args)).fetchall()
        if not rows:
            break
        for row in rows:
            cursor = int(row["sequence"])
            op_row = ops.db.execute(
                "SELECT * FROM operations WHERE operation_id=?",
                (row["operation_id"],),
            ).fetchone()
            if op_row is None:
                filtered = True
                continue
            op = OperationService._decode(op_row)
            if not operation_visible(ops.db, principal, op):
                filtered = True
                continue
            if len(items) == limit:
                has_more = True
                break
            receipt = build_input_receipt(ops.db, op, wi_row=row)
            if receipt is not None:
                items.append(receipt)
        if has_more or len(rows) < max(limit + 1, 32):
            break

    latest_att_id = pm_work_start.latest_attempt_id(ops.db, workspace_id, work_item_id)
    if latest_att_id:
        att_r = ops.db.execute(
            "SELECT * FROM work_attempts WHERE workspace_id=? AND work_item_id=? AND attempt_id=?",
            (workspace_id, work_item_id, latest_att_id),
        ).fetchone()
        start_id = None if att_r is None else (att_r["start_operation_id"] or att_r["origin_id"])
        if not start_id or not pm_work_start.operation_visible(
            ops.db, principal, {"operation_id": start_id},
        ):
            latest_att_id = None

    next_cursor = items[-1]["sequence"] if (has_more and items) else None
    return {
        "workspace_id": workspace_id,
        "work_item_id": work_item_id,
        "attempt_id": attempt_id,
        "latest_attempt_id": latest_att_id,
        "items": items,
        "next_cursor": next_cursor,
        "has_more": has_more,
        "visibility_filtered": filtered,
    }
