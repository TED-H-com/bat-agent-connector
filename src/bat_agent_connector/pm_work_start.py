"""Direct work start compound action (v1.3 E02, slice P3): work.start and start preview."""
from __future__ import annotations

import hashlib
import json
import re
import time

from . import controller_authority, pm_source_lineage
from .operations import OperationError, OperationService

ACTION = "work.start"
PREVIEW = "pm_work_start_preview"
ATTEMPTS_LIST = "pm_work_attempts_list"
ATTEMPT_GET = "pm_work_attempt_get"
ATTEMPT_ID = re.compile(r"att_[0-9a-f]{32}")
COMPOSITION_PROFILE = "work_start_thin_v1"
REQUIRED_SCOPES = frozenset({"observe", "manage", "start"})
SLOTS = ("bind", "package", "handoff")


def _bad(code: str, message: str, status: int = 409) -> OperationError:
    return OperationError(code, message, status)


def _json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _digest(value) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def installed(db) -> bool:
    return pm_source_lineage._table(db, "work_attempts")


def schema(journal) -> None:
    """Idempotent additive DDL; does not allocate user_version or a data step."""
    with journal.tx():
        for ddl in (
            """CREATE TABLE IF NOT EXISTS pm_work_start_children (
                parent_id TEXT NOT NULL,
                slot TEXT NOT NULL,
                ordinal INTEGER NOT NULL,
                operation_id TEXT NOT NULL UNIQUE,
                request TEXT NOT NULL,
                request_digest TEXT NOT NULL,
                created_at REAL NOT NULL,
                PRIMARY KEY(parent_id, slot),
                UNIQUE(parent_id, ordinal))""",
            """CREATE TABLE IF NOT EXISTS work_attempts (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                attempt_id TEXT NOT NULL UNIQUE,
                workspace_id TEXT NOT NULL,
                work_item_id TEXT NOT NULL,
                origin_kind TEXT NOT NULL,
                origin_id TEXT NOT NULL,
                start_operation_id TEXT UNIQUE,
                predecessor_attempt_id TEXT,
                admission_digest TEXT NOT NULL,
                created_at REAL NOT NULL,
                UNIQUE(origin_kind, origin_id))""",
            """CREATE INDEX IF NOT EXISTS work_attempts_by_work
                ON work_attempts(workspace_id, work_item_id, sequence DESC)""",
        ):
            journal.db.execute(ddl)
        for table, label in (
            ("pm_work_start_children", "Work start child is immutable"),
            ("work_attempts", "Work attempt history is immutable"),
        ):
            for verb in ("UPDATE", "DELETE"):
                journal.db.execute(
                    f"CREATE TRIGGER IF NOT EXISTS {table}_{verb.lower()}_immutable "
                    f"BEFORE {verb} ON {table} BEGIN SELECT RAISE(ABORT, '{label}'); END"
                )
        journal.db.execute("""CREATE TRIGGER IF NOT EXISTS pm_work_start_child_settled
            AFTER UPDATE OF status ON operations
            WHEN NEW.status != OLD.status AND NEW.status IN ('succeeded','failed','cancelled','needs_attention')
            BEGIN
                UPDATE operations SET next_run_at=0
                WHERE action='work.start' AND status='waiting_external'
                    AND operation_id=(SELECT parent_id FROM pm_work_start_children
                        WHERE operation_id=NEW.operation_id);
            END""")


def _op_controller(db, operation_id: str) -> dict | None:
    row = db.execute("SELECT * FROM operations WHERE operation_id=?", (operation_id,)).fetchone()
    if row is None:
        return None
    op = OperationService._decode(row)
    saved = ((op.get("external_refs") or {}).get("admission_binding") or {})
    intent = saved.get("work_start")
    if isinstance(intent, dict) and isinstance(intent.get("controller"), dict):
        return intent["controller"]
    return controller_authority.operation_binding(op)


def work_controller(db, workspace_id: str, work_item_id: str) -> dict | None:
    """Return the controller anchored by the Work's earliest attempt, or None if never started."""
    if not installed(db):
        return None
    row = db.execute(
        """SELECT start_operation_id, origin_id FROM work_attempts
        WHERE workspace_id=? AND work_item_id=? ORDER BY sequence ASC LIMIT 1""",
        (workspace_id, work_item_id),
    ).fetchone()
    if row is None:
        return None
    op_id = row["start_operation_id"] or row["origin_id"]
    controller = _op_controller(db, op_id)
    if controller is None:
        raise _bad("PM_WORK_CONTROLLER_CONFLICT", "Original work attempt controller authority cannot be verified", 409)
    return controller


def require_work_controller(db, workspace_id: str, work_item_id: str, candidate: dict) -> None:
    """Reject a different controller from taking over a Work that already has an attempt anchor."""
    anchored = work_controller(db, workspace_id, work_item_id)
    if anchored is not None and not controller_authority.same_controller(candidate, anchored):
        raise _bad(
            "PM_WORK_CONTROLLER_CONFLICT",
            "This work item is already anchored to another controller",
            409,
        )


def latest_attempt_id(db, workspace_id: str, work_item_id: str) -> str | None:
    if not installed(db):
        return None
    row = db.execute(
        """SELECT attempt_id FROM work_attempts
        WHERE workspace_id=? AND work_item_id=? ORDER BY sequence DESC LIMIT 1""",
        (workspace_id, work_item_id),
    ).fetchone()
    return row["attempt_id"] if row else None


def admit_operation(db, operation_id: str, action: str, target: dict, binding: dict | None, now: float | None = None) -> None:
    """INSERT-transaction hook: re-verify controller anchor and record the immutable work_attempts row."""
    if action != ACTION:
        return
    if not installed(db):
        raise _bad("PM_WORK_START_UNINITIALIZED", "work_attempts schema is not initialized", 409)
    saved = binding or {}
    intent = saved.get("work_start")
    digest = saved.get("work_start_digest")
    if not isinstance(intent, dict) or not isinstance(digest, str) or _digest(intent) != digest:
        raise _bad("PM_WORK_START_CONTEXT_INVALID", "Work start admission binding is missing or corrupt", 409)
    wid = target["workspace_id"]
    work_item_id = target["work_item_id"]
    controller = intent.get("controller") or saved.get("controller_authority")
    if not isinstance(controller, dict):
        raise _bad("PM_WORK_START_CONTEXT_INVALID", "Work start controller authority is missing", 409)
    require_work_controller(db, wid, work_item_id, controller)
    predecessor = latest_attempt_id(db, wid, work_item_id)
    created_at = time.time() if now is None else now
    db.execute(
        """INSERT INTO work_attempts(
            attempt_id, workspace_id, work_item_id, origin_kind, origin_id,
            start_operation_id, predecessor_attempt_id, admission_digest, created_at
        ) VALUES(?,?,?,?,?,?,?,?,?)""",
        (
            "att_" + operation_id[3:],
            wid,
            work_item_id,
            "operation",
            operation_id,
            operation_id,
            predecessor,
            digest,
            created_at,
        ),
    )
