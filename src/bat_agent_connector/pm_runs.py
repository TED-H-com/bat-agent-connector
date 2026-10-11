"""Durable, bounded PM turns in the existing central journal.

The writer slot has no lease expiry. A lost reply, disconnected adapter or old
timestamp cannot authorize another writer. Public actions belong to the operation
service; these helpers only persist its admitted intent and original evidence.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
import uuid

from . import pm_discussions, pm_workspace
from .api_auth import Principal
from .operations import OperationError

RUN_ID = re.compile(r"pmr_[0-9a-f]{32}")
PHASES = {"prepare", "start", "send", "observe", "import"}
RECEIPT_STATUSES = {"prepared", "sent", "confirmed", "unknown", "failed"}
TERMINAL = {"completed", "failed", "aborted", "prepared_manual", "completed_service"}


def _error(message, code="PM_RUN_CONFLICT", status=409):
    return OperationError(code, message, status)


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _digest(value):
    return hashlib.sha256(_json(value).encode()).hexdigest()


def schema(journal):
    """Add tables without changing the journal's data migration version."""
    with journal.tx():
        for statement in (
            """CREATE TABLE IF NOT EXISTS pm_writer_slots (
                workspace_id TEXT PRIMARY KEY REFERENCES pm_workspaces(workspace_id),
                generation INTEGER NOT NULL, run_id TEXT, updated_at REAL NOT NULL)""",
            """CREATE TABLE IF NOT EXISTS pm_runs (
                run_id TEXT PRIMARY KEY, operation_id TEXT NOT NULL UNIQUE,
                workspace_id TEXT NOT NULL REFERENCES pm_workspaces(workspace_id),
                thread_id TEXT NOT NULL REFERENCES pm_threads(thread_id), generation INTEGER NOT NULL,
                requester TEXT NOT NULL, request_authority TEXT NOT NULL,
                execution_authority TEXT NOT NULL, adapter TEXT NOT NULL, purpose TEXT NOT NULL,
                thread_version INTEGER NOT NULL, context TEXT NOT NULL, context_digest TEXT NOT NULL,
                created_at REAL NOT NULL, UNIQUE(workspace_id,generation))""",
            """CREATE TABLE IF NOT EXISTS pm_run_state (
                run_id TEXT PRIMARY KEY REFERENCES pm_runs(run_id), status TEXT NOT NULL,
                worktree_binding TEXT, session_binding TEXT, send_cursor TEXT, cursor TEXT,
                gaps TEXT NOT NULL, has_more INTEGER NOT NULL DEFAULT 0,
                outcome TEXT, updated_at REAL NOT NULL)""",
            """CREATE TABLE IF NOT EXISTS pm_workspace_worktrees (
                workspace_id TEXT PRIMARY KEY REFERENCES pm_workspaces(workspace_id),
                run_id TEXT NOT NULL REFERENCES pm_runs(run_id), binding TEXT NOT NULL,
                created_at REAL NOT NULL)""",
            """CREATE TABLE IF NOT EXISTS pm_run_receipts (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL REFERENCES pm_runs(run_id), phase TEXT NOT NULL,
                status TEXT NOT NULL, receipt_key TEXT NOT NULL, data TEXT NOT NULL,
                created_at REAL NOT NULL, UNIQUE(run_id,phase,status))""",
            """CREATE TABLE IF NOT EXISTS pm_run_native_events (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL REFERENCES pm_runs(run_id), epoch TEXT NOT NULL,
                native_seq INTEGER NOT NULL, event TEXT NOT NULL, digest TEXT NOT NULL,
                created_at REAL NOT NULL, UNIQUE(run_id,epoch,native_seq))""",
            """CREATE TABLE IF NOT EXISTS pm_assistant_provenance (
                message_id TEXT PRIMARY KEY REFERENCES pm_messages(message_id),
                run_id TEXT NOT NULL UNIQUE REFERENCES pm_runs(run_id),
                native_turn_id TEXT NOT NULL, native_message_id TEXT, sdk_session_id TEXT NOT NULL,
                model TEXT, completeness TEXT NOT NULL, source_refs TEXT NOT NULL,
                context_digest TEXT NOT NULL, created_at REAL NOT NULL)""",
            "CREATE INDEX IF NOT EXISTS pm_runs_workspace ON pm_runs(workspace_id,created_at,run_id)",
        ):
            journal.db.execute(statement)
        for table in ("pm_runs", "pm_workspace_worktrees", "pm_run_receipts", "pm_run_native_events", "pm_assistant_provenance"):
            for verb in ("UPDATE", "DELETE"):
                journal.db.execute(f"CREATE TRIGGER IF NOT EXISTS {table}_{verb.lower()}_immutable BEFORE {verb} ON {table} "
                                   "BEGIN SELECT RAISE(ABORT, 'PM run evidence is immutable'); END")


def execution_identity(db, workspace_id):
    """A workspace PM is never the human who asked it to perform this turn."""
    pm_workspace._workspace(db, workspace_id)
    actor = "pm:" + workspace_id
    return {"actor": actor, "controller_id": pm_workspace.controller_for(db, Principal(actor, frozenset()))}


def get(db, run_id):
    """Internal readback; callers exposing it must use read/list_runs."""
    if not isinstance(run_id, str) or not RUN_ID.fullmatch(run_id):
        raise _error("PM run not found", "PM_RUN_NOT_FOUND", 404)
    row = db.execute("SELECT * FROM pm_runs JOIN pm_run_state USING(run_id) WHERE run_id=?", (run_id,)).fetchone()
    if row is None:
        raise _error("PM run not found", "PM_RUN_NOT_FOUND", 404)
    result = dict(row)
    for field in ("request_authority", "execution_authority", "context", "worktree_binding", "session_binding",
                  "send_cursor", "cursor", "gaps", "outcome"):
        result[field] = json.loads(result[field]) if result[field] is not None else None
    result["has_more"] = bool(result["has_more"])
    result["state"] = result["status"]
    result["receipts"] = [{**dict(row), "data": json.loads(row["data"])} for row in db.execute(
        "SELECT * FROM pm_run_receipts WHERE run_id=? ORDER BY sequence", (run_id,))]
    return result


def read(ops, principal, workspace_id, run_id):
    with ops.journal.tx():
        pm_discussions._read(ops, principal, workspace_id)
        run = get(ops.db, run_id)
        if run["workspace_id"] != workspace_id or not source_visible(ops.db, principal, run):
            raise _error("PM run not found", "PM_RUN_NOT_FOUND", 404)
        _read_bound({"run": run})
        return run


def get_by_operation(db, operation_id):
    row = db.execute("SELECT run_id FROM pm_runs WHERE operation_id=?", (operation_id,)).fetchone()
    return get(db, row[0]) if row else None


def list_runs(ops, principal, workspace_id, *, thread_id=None, limit=50, after=None):
    with ops.journal.tx():
        pm_discussions._read(ops, principal, workspace_id)
        pm_discussions._limit(limit)
        if thread_id is not None:
            pm_discussions._thread(ops.db, workspace_id, thread_id)
        if after is not None and get(ops.db, after)["workspace_id"] != workspace_id:
            raise _error("PM run not found", "PM_RUN_NOT_FOUND", 404)
        rows = ops.db.execute("""SELECT run_id FROM pm_runs WHERE workspace_id=?
            AND (? IS NULL OR thread_id=?) AND (? IS NULL OR run_id>?) ORDER BY run_id LIMIT ?""",
            (workspace_id, thread_id, thread_id, after, after, limit + 1)).fetchall()
        result = {"workspace_id": workspace_id, "runs": [], "next_cursor": None,
                  "coverage": {"visibility_filtered": False, "scanned": 0}}
        cursor = after
        for row in rows[:limit]:
            run = get(ops.db, row[0])
            visible = source_visible(ops.db, principal, run)
            candidate = {**result, "runs": result["runs"] + ([_summary(run)] if visible else []),
                "next_cursor": row[0],  # Budget continuation and filtered-row metadata too.
                "coverage": {"visibility_filtered": result["coverage"]["visibility_filtered"] or not visible,
                             "scanned": result["coverage"]["scanned"] + 1}}
            try:
                _read_bound(candidate)
            except OperationError:
                if cursor == after:
                    raise
                result["next_cursor"] = cursor
                return _read_bound(result)
            result = candidate
            cursor = row[0]
        result["next_cursor"] = cursor if len(rows) > limit else None
        return _read_bound(result)


MAX_READ_BYTES = 2_000_000


def _read_bound(value):
    from .pm_sprints import _read_size
    if _read_size(value) > MAX_READ_BYTES:
        raise _error("The complete PM run response exceeds 2,000,000 UTF-8 bytes", "PM_RUN_TOO_LARGE", 413)
    return value


def source_visible(db, principal, run):
    """Follow-up derives access from its original admission, including raw output."""
    row = db.execute("SELECT action FROM operations WHERE operation_id=?", (run["operation_id"],)).fetchone()
    if run["context"].get("output_destination") == "follow_up_assessment" or row and row[0] == "pm.follow_up.analyze":
        from .pm_follow_up_analysis import operation_visible
        return operation_visible(db, principal, {"operation_id": run["operation_id"]})
    return True


def _summary(run):
    result = {field: run[field] for field in ("run_id", "workspace_id", "thread_id", "operation_id", "generation",
              "requester", "adapter", "purpose", "thread_version", "context_digest", "created_at", "updated_at", "state", "status")}
    result["profile"] = {field: run["context"]["profile"][field] for field in ("profile_id", "version", "mode")}
    return result


def require_requester(db, run):
    """Before a new effect, recheck the original caller's current membership."""
    auth = run["request_authority"]
    principal = Principal(auth["actor"], frozenset(auth["scopes"]), admin=auth["admin"])
    if not principal.allows("manage"):
        raise _error("PM runs require manage scope", "FORBIDDEN", 403)
    pm_workspace.require_member(db, principal, run["workspace_id"], write=True)
    if not source_visible(db, principal, run):
        raise _error("Original PM analysis sources are unavailable", "PM_FOLLOW_UP_SOURCE_UNAVAILABLE", 404)
    return principal


def create(ops, principal, *, operation_id, workspace_id, thread_id, expected_version,
           adapter, purpose, execution_authority, max_chars=32000, _context_factory=None):
    """Freeze the context and reserve the only writer in one journal transaction.

    Idempotent internal recovery reads a saved run before checking later membership
    or versions. Public operation replays still require current authorization.
    """
    with ops.journal.tx():
        existing = ops.db.execute("SELECT run_id FROM pm_runs WHERE operation_id=?", (operation_id,)).fetchone()
        if existing:
            run = get(ops.db, existing[0])
            if (run["workspace_id"], run["thread_id"], run["requester"], run["adapter"], run["purpose"],
                    run["thread_version"], run["execution_authority"], run["context"]["coverage"]["max_chars"]) != (
                    workspace_id, thread_id, principal.actor, adapter, purpose, expected_version, execution_authority, max_chars):
                raise _error("Original PM run intent differs")
            return run
        from .pm_lifecycle_gates import require_local
        require_local(ops.db, operation_id)
        if not principal.allows("manage"):
            raise _error("PM runs require manage scope", "FORBIDDEN", 403)
        pm_workspace.require_member(ops.db, principal, workspace_id, write=True)
        thread = pm_discussions._thread(ops.db, workspace_id, thread_id)
        if type(expected_version) is not int or expected_version != thread["version"]:
            raise _error("Read the current discussion before starting a PM turn", "VERSION_CONFLICT")
        if thread["mode"] not in {"idea", "planning"}:
            raise _error("PM runtime does not support this discussion mode", "PM_MODE_REFUSED")
        for value, label, size in ((operation_id, "operation_id", 80), (adapter, "adapter", 120), (purpose, "purpose", 4000)):
            pm_discussions._text(value, label, size)
        identity = execution_identity(ops.db, workspace_id)
        if (not isinstance(execution_authority, dict) or execution_authority.get("actor") != identity["actor"]
                or execution_authority.get("controller_id") != identity["controller_id"]
                or execution_authority.get("server_id") != pm_workspace.server_id(ops.db)
                or execution_authority.get("pm_workspace_id") != workspace_id
                or execution_authority.get("pm_operation_id") != operation_id
                or execution_authority.get("pm_requester") != pm_workspace.principal_binding(principal)
                or execution_authority.get("admin") is not False):
            raise _error("PM execution must use its own workspace controller", "PM_AUTHORITY_INVALID", 403)
        slot = ops.db.execute("SELECT * FROM pm_writer_slots WHERE workspace_id=?", (workspace_id,)).fetchone()
        if slot and slot["run_id"]:
            raise _error("A PM turn still owns this workspace writer", "PM_WRITER_BUSY")
        generation = slot["generation"] + 1 if slot else 1
        context = (_context_factory() if _context_factory else
                   pm_discussions.context(ops, principal, workspace_id, thread_id, max_chars=max_chars))
        if _context_factory:
            row = ops.db.execute("SELECT action FROM operations WHERE operation_id=?", (operation_id,)).fetchone()
            if (not row or row[0] != "pm.follow_up.analyze" or adapter != "codex-native-linux"
                    or context.get("output_destination") != "follow_up_assessment"
                    or context.get("workspace_id") != workspace_id or context.get("thread_id") != thread_id
                    or context.get("thread_version") != expected_version or context.get("coverage", {}).get("max_chars") != max_chars):
                raise _error("A fixed context requires its original Follow-up admission", "PM_CONTEXT_REFUSED")
        context["purpose"] = purpose
        # Keep the profile, sources, omitted coverage, and exact words, not a mutable pointer.
        digest = _digest(context)
        run_id = "pmr_" + uuid.uuid5(uuid.UUID(pm_workspace.server_id(ops.db)), operation_id).hex
        now = time.time()
        ops.db.execute("INSERT INTO pm_runs VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (run_id, operation_id, workspace_id, thread_id, generation, principal.actor,
             _json(pm_workspace.principal_binding(principal)), _json(execution_authority), adapter, purpose,
             thread["version"], _json(context), digest, now))
        ops.db.execute("INSERT INTO pm_run_state(run_id,status,gaps,updated_at) VALUES(?,?,?,?)",
                       (run_id, "prepared", "[]", now))
        ops.db.execute("""INSERT INTO pm_writer_slots VALUES(?,?,?,?) ON CONFLICT(workspace_id)
            DO UPDATE SET generation=excluded.generation,run_id=excluded.run_id,updated_at=excluded.updated_at""",
            (workspace_id, generation, run_id, now))
        return get(ops.db, run_id)


def _active(db, run_id, generation):
    run = get(db, run_id)
    slot = db.execute("SELECT generation,run_id FROM pm_writer_slots WHERE workspace_id=?", (run["workspace_id"],)).fetchone()
    if (type(generation) is not int or run["generation"] != generation or not slot
            or slot["generation"] != generation or slot["run_id"] != run_id):
        raise _error("PM reply belongs to a writer that is no longer active", "PM_WRITER_CHANGED")
    return run


def require_writer(journal, run_id, generation):
    run = _active(journal.db, run_id, generation)
    require_requester(journal.db, run)
    return run


def _bind(journal, run_id, generation, field, value):
    with journal.tx():
        run = _active(journal.db, run_id, generation)
        if run[field] is not None:
            if run[field] != value:
                raise _error("Original PM resource binding cannot change")
            return run
        require_requester(journal.db, run)
        journal.db.execute(f"UPDATE pm_run_state SET {field}=?,updated_at=? WHERE run_id=?",  # noqa: S608 - internal fixed columns
                           (_json(value), time.time(), run_id))
        return get(journal.db, run_id)


def bind_worktree(journal, run_id, generation, plan):
    if not isinstance(plan, dict) or not plan:
        raise _error("PM worktree needs an original bootstrap binding")
    with journal.tx():
        run = _active(journal.db, run_id, generation)
        old = workspace_worktree(journal.db, run["workspace_id"])
        if old is not None and old != plan:
            raise _error("The workspace already has a different PM worktree binding")
        run = _bind(journal, run_id, generation, "worktree_binding", plan)
        if old is None:
            journal.db.execute("INSERT INTO pm_workspace_worktrees VALUES(?,?,?,?)",
                               (run["workspace_id"], run_id, _json(plan), time.time()))
        return run


def workspace_worktree(db, workspace_id):
    row = db.execute("SELECT binding FROM pm_workspace_worktrees WHERE workspace_id=?", (workspace_id,)).fetchone()
    return json.loads(row[0]) if row else None


def bind_session(journal, run_id, generation, *, host, session_id, incarnation, worktree_binding=None,
                 sdk_session_id=None, actual_model=None):
    for value, label in ((host, "host"), (session_id, "session_id")):
        pm_discussions._text(value, label, 300)
    if not isinstance(incarnation, str) or not re.fullmatch(r"[0-9a-f]{32}", incarnation):
        raise _error("PM session requires its original controller incarnation")
    for value, label in ((sdk_session_id, "sdk_session_id"), (actual_model, "actual_model")):
        if value is not None:
            pm_discussions._text(value, label, 300)
    with journal.tx():
        if worktree_binding is not None:
            bind_worktree(journal, run_id, generation, worktree_binding)
        return _bind(journal, run_id, generation, "session_binding",
                     {"host": host, "session_id": session_id, "incarnation": incarnation,
                      "sdk_session_id": sdk_session_id, "actual_model": actual_model})


def receipt(journal, run_id, generation, phase, status, data):
    """Save each original phase receipt. Unknown never releases the writer."""
    if phase not in PHASES or status not in RECEIPT_STATUSES or not isinstance(data, dict):
        raise _error("Invalid PM phase receipt")
    encoded = _json(data)
    with journal.tx():
        saved = journal.db.execute("SELECT * FROM pm_run_receipts WHERE run_id=? AND phase=? AND status=?",
                                   (run_id, phase, status)).fetchone()
        if saved:
            if saved["data"] != encoded or get(journal.db, run_id)["generation"] != generation:
                raise _error("Original PM receipt differs")
            return {**dict(saved), "data": json.loads(saved["data"])}
        run = _active(journal.db, run_id, generation)
        if status in {"prepared", "sent"}:
            require_requester(journal.db, run)
        if phase == "send" and status in {"prepared", "sent"} and not run["send_cursor"]:
            raise _error("PM send needs its original replay cursor")
        key = f"{run_id}:{phase}"
        now = time.time()
        journal.db.execute("INSERT INTO pm_run_receipts(run_id,phase,status,receipt_key,data,created_at) VALUES(?,?,?,?,?,?)",
                           (run_id, phase, status, key, encoded, now))
        state = "unknown" if status == "unknown" else "running" if phase in {"send", "observe"} else "prepared"
        journal.db.execute("UPDATE pm_run_state SET status=?,updated_at=? WHERE run_id=?", (state, now, run_id))
        saved = journal.db.execute("SELECT * FROM pm_run_receipts WHERE run_id=? AND phase=? AND status=?",
                                   (run_id, phase, status)).fetchone()
        return {**dict(saved), "data": json.loads(saved["data"])}


def _cursor(value):
    if (not isinstance(value, dict) or set(value) != {"epoch", "seq"}
            or not isinstance(value["epoch"], str) or not value["epoch"] or len(value["epoch"]) > 300
            or type(value["seq"]) is not int or value["seq"] < 0):
        raise _error("Invalid native replay cursor", "PM_REPLAY_INVALID")
    return value


def set_send_cursor(journal, run_id, generation, cursor):
    cursor = _cursor(cursor)
    with journal.tx():
        run = _active(journal.db, run_id, generation)
        if not run["session_binding"]:
            raise _error("PM send requires its bound session")
        if run["send_cursor"] is not None:
            if run["send_cursor"] != cursor:
                raise _error("Original PM send cursor cannot change")
            return run
        if any(r["phase"] == "send" for r in run["receipts"]):
            raise _error("Cannot establish a PM cursor after sending")
        require_requester(journal.db, run)
        journal.db.execute("UPDATE pm_run_state SET send_cursor=?,cursor=?,updated_at=? WHERE run_id=?",
                           (_json(cursor), _json(cursor), time.time(), run_id))
        return get(journal.db, run_id)


def events(db, run_id):
    get(db, run_id)
    return [json.loads(row[0]) for row in db.execute(
        "SELECT event FROM pm_run_native_events WHERE run_id=? ORDER BY sequence", (run_id,))]


def record_replay(journal, run_id, generation, replay):
    """Persist the original events and cursor atomically; gaps are sticky evidence."""
    cursor = _cursor(replay.get("cursor"))
    values = replay.get("events", [])
    if not isinstance(values, list) or len(values) > 10000:
        raise _error("Invalid PM replay events", "PM_REPLAY_INVALID")
    with journal.tx():
        run = _active(journal.db, run_id, generation)
        if not run["send_cursor"] or not run["session_binding"]:
            raise _error("PM replay requires the original session and send cursor")
        session_id = run["session_binding"]["session_id"]
        if replay.get("sessionId", replay.get("session_id", session_id)) != session_id:
            raise _error("Native replay names another session", "PM_REPLAY_INVALID")
        gaps = list(run["gaps"])
        previous = run["cursor"]
        def gap(reason):
            detail = {"reason": reason, "from": previous, "to": cursor}
            if detail not in gaps:
                gaps.append(detail)
        if replay.get("gap") or replay.get("mode") != "delta":
            gap(str(replay.get("reason") or "snapshot_after_send"))
        if cursor["epoch"] != previous["epoch"]:
            gap("epoch_changed")
        next_seq = previous["seq"] + 1 if cursor["epoch"] == previous["epoch"] else None
        last_seq = None
        for event in values:
            if (not isinstance(event, dict) or type(event.get("seq")) is not int or event["seq"] < 1
                    or not isinstance(event.get("channel"), str) or not isinstance(event.get("params"), dict)):
                raise _error("Invalid PM native event", "PM_REPLAY_INVALID")
            seq = event["seq"]
            if event.get("epoch", cursor["epoch"]) != cursor["epoch"]:
                raise _error("Native event epoch differs from replay cursor", "PM_REPLAY_INVALID")
            if event["params"].get("sessionId") != session_id:
                raise _error("Native event names another session", "PM_REPLAY_INVALID")
            if seq > cursor["seq"]:
                raise _error("Native event lies beyond replay cursor", "PM_REPLAY_INVALID")
            encoded, digest = _json(event), _digest(event)
            old = journal.db.execute("SELECT digest FROM pm_run_native_events WHERE run_id=? AND epoch=? AND native_seq=?",
                                     (run_id, cursor["epoch"], seq)).fetchone()
            if old:
                if old[0] != digest:
                    raise _error("Native source ID was replayed with different content", "PM_REPLAY_CONFLICT")
                continue
            if last_seq is not None and seq <= last_seq:
                raise _error("Native replay events are out of order", "PM_REPLAY_INVALID")
            if next_seq is not None and seq != next_seq:
                gap("missing_native_events")
            last_seq, next_seq = seq, seq + 1
            journal.db.execute("INSERT INTO pm_run_native_events(run_id,epoch,native_seq,event,digest,created_at) VALUES(?,?,?,?,?,?)",
                               (run_id, cursor["epoch"], seq, encoded, digest, time.time()))
        if cursor["epoch"] == previous["epoch"]:
            if cursor["seq"] < previous["seq"]:
                # An older duplicate page must not roll back the durable cursor.
                cursor = previous
            elif cursor["seq"] > previous["seq"] and next_seq != cursor["seq"] + 1:
                gap("cursor_advanced_without_events")
        journal.db.execute("UPDATE pm_run_state SET cursor=?,gaps=?,has_more=?,updated_at=? WHERE run_id=?",
                           (_json(cursor), _json(gaps), int(bool(replay.get("has_more", replay.get("hasMore", False)))),
                            time.time(), run_id))
        if gaps:
            journal.db.execute("UPDATE pm_run_state SET status='unknown' WHERE run_id=?", (run_id,))
        return get(journal.db, run_id)


def _terminal(db, run, *, turn_id=None, sdk_session_id=None):
    if run["gaps"] or run["has_more"] or not run["send_cursor"]:
        raise _error("PM native completion is incomplete or unknown", "PM_COMPLETION_UNPROVEN")
    if not any(r["phase"] == "send" and r["status"] in {"sent", "confirmed", "unknown"} for r in run["receipts"]):
        raise _error("PM run has no original send evidence", "PM_COMPLETION_UNPROVEN")
    terminals = []
    for row in db.execute("SELECT * FROM pm_run_native_events WHERE run_id=? ORDER BY sequence", (run["run_id"],)):
        event = json.loads(row["event"])
        if row["epoch"] != run["send_cursor"]["epoch"] or row["native_seq"] <= run["send_cursor"]["seq"]:
            continue
        if event["channel"] == "claude:turn-end":
            payload = event["params"].get("payload")
            if not isinstance(payload, dict):
                raise _error("PM native terminal payload is invalid", "PM_COMPLETION_UNPROVEN")
            terminals.append((row, payload))
    if len(terminals) != 1:
        raise _error("PM run needs one unambiguous native turn completion", "PM_COMPLETION_UNPROVEN")
    row, payload = terminals[0]
    if (not isinstance(payload.get("turnId"), str) or not payload["turnId"]
            or not isinstance(payload.get("sdkSessionId"), str) or not payload["sdkSessionId"]
            or turn_id is not None and payload["turnId"] != turn_id
            or sdk_session_id is not None and payload["sdkSessionId"] != sdk_session_id):
        raise _error("PM native turn identity differs", "PM_COMPLETION_UNPROVEN")
    return row, payload


def _release(db, run, status, outcome):
    now = time.time()
    db.execute("UPDATE pm_run_state SET status=?,outcome=?,updated_at=? WHERE run_id=?",
               (status, _json(outcome), now, run["run_id"]))
    changed = db.execute("UPDATE pm_writer_slots SET run_id=NULL,updated_at=? WHERE workspace_id=? AND generation=? AND run_id=?",
                         (now, run["workspace_id"], run["generation"], run["run_id"]))
    if changed.rowcount != 1:
        raise _error("PM writer changed during completion", "PM_WRITER_CHANGED")


def import_reply(journal, run_id, generation, *, content, native_turn_id, sdk_session_id,
                 model=None, native_message_id=None):
    """Internal assistant import; it never confirms inputs or changes a phase."""
    pm_discussions._text(content, "assistant content", 128000)
    for value, label in ((native_turn_id, "native_turn_id"), (sdk_session_id, "sdk_session_id")):
        pm_discussions._text(value, label, 300)
    for value, label in ((model, "model"), (native_message_id, "native_message_id")):
        if value is not None:
            pm_discussions._text(value, label, 300)
    with journal.tx():
        if get(journal.db, run_id)["context"].get("output_destination") == "follow_up_assessment":
            raise _error("Follow-up output cannot enter ordinary discussion history", "PM_OUTPUT_DESTINATION_REFUSED")
        saved = journal.db.execute("""SELECT p.*,m.content FROM pm_assistant_provenance p
            JOIN pm_messages m USING(message_id) WHERE p.run_id=?""", (run_id,)).fetchone()
        if saved:
            if (get(journal.db, run_id)["generation"] != generation
                    or (saved["content"], saved["native_turn_id"], saved["sdk_session_id"], saved["model"], saved["native_message_id"])
                    != (content, native_turn_id, sdk_session_id, model, native_message_id)):
                raise _error("Original assistant import differs", "PM_IMPORT_CONFLICT")
            return get(journal.db, run_id)
        run = _active(journal.db, run_id, generation)
        require_requester(journal.db, run)
        native, payload = _terminal(journal.db, run, turn_id=native_turn_id, sdk_session_id=sdk_session_id)
        if payload.get("reason") != "completed":
            raise _error("The native turn did not complete successfully", "PM_COMPLETION_UNPROVEN")
        if run["session_binding"].get("sdk_session_id") != sdk_session_id:
            raise _error("The reply differs from the originally observed native session", "PM_COMPLETION_UNPROVEN")
        from .pm_runtime import assess_turn
        assessment = assess_turn(run["session_binding"]["session_id"], run["send_cursor"], events(journal.db, run_id),
                                 run["cursor"], expected_sdk_session_id=sdk_session_id,
                                 gap=bool(run["gaps"]), has_more=run["has_more"], actual_model=run["session_binding"]["actual_model"])
        if (not assessment.get("complete") or assessment.get("content") != content
                or assessment.get("native_turn_id") != native_turn_id
                or assessment.get("sdk_session_id") != sdk_session_id
                or assessment.get("actual_model") != model
                or assessment.get("native_message_id") != native_message_id):
            raise _error("Assistant content or identity differs from native evidence", "PM_IMPORT_CONFLICT")
        message_id = "pmm_" + uuid.uuid5(uuid.UUID(pm_workspace.server_id(journal.db)), run_id + ":assistant").hex
        now = time.time()
        journal.db.execute("INSERT INTO pm_messages(message_id,workspace_id,thread_id,actor,content,created_at,operation_id) VALUES(?,?,?,?,?,?,?)",
                           (message_id, run["workspace_id"], run["thread_id"], run["execution_authority"]["actor"],
                            content, now, run["operation_id"]))
        refs = {"context_digest": run["context_digest"], "run_id": run_id,
                "terminal": {"epoch": native["epoch"], "seq": native["native_seq"], "digest": native["digest"]},
                "send_cursor": run["send_cursor"], "observed_cursor": run["cursor"],
                "native_event_seqs": assessment["source_event_seqs"],
                "input_ids": [item["input_id"] for group in run["context"]["inputs_by_status"].values() for item in group],
                "message_ids": [item["message_id"] for item in run["context"]["messages"]],
                "coverage": run["context"]["coverage"]}
        journal.db.execute("INSERT INTO pm_assistant_provenance VALUES(?,?,?,?,?,?,?,?,?,?)",
                           (message_id, run_id, native_turn_id, native_message_id, sdk_session_id, model,
                            "complete", _json(refs), run["context_digest"], now))
        journal.db.execute("UPDATE pm_threads SET version=version+1,updated_at=? WHERE thread_id=?", (now, run["thread_id"]))
        outcome = {"message_id": message_id, "native_turn_id": native_turn_id, "completeness": "complete"}
        receipt(journal, run_id, generation, "import", "confirmed", outcome)
        _release(journal.db, run, "completed", outcome)
        journal.api_event("pm_thread", run["thread_id"], "pm.run.imported",
                          {"workspace_id": run["workspace_id"], "thread_id": run["thread_id"], "run_id": run_id,
                           "message_id": message_id, "operation_id": run["operation_id"]}, actor=run["execution_authority"]["actor"])
        return get(journal.db, run_id)


def fail(journal, run_id, generation, reason, *, before_send=False):
    """Release only before any possible native start/send, or after native error/abort.

    A sent start may itself have uncertain effects; its writer is retained even
    when no send receipt exists. Session reconciliation belongs to the runner.
    """
    pm_discussions._text(reason, "reason", 4000)
    with journal.tx():
        run = get(journal.db, run_id)
        if run["generation"] != generation:
            raise _error("PM writer changed", "PM_WRITER_CHANGED")
        if run["status"] in TERMINAL:
            return run
        run = _active(journal.db, run_id, generation)
        if before_send:
            uncertain = any(r["phase"] in {"start", "send"} for r in run["receipts"])
            if uncertain:
                raise _error("PM may have started or sent; preserve its writer", "PM_EFFECT_UNKNOWN")
            status = "failed"
            outcome = {"reason": reason, "effect": "not_started"}
        else:
            native, payload = _terminal(journal.db, run)
            if payload.get("reason") not in {"error", "aborted"}:
                raise _error("PM failure needs a native error or abort", "PM_COMPLETION_UNPROVEN")
            status = "aborted" if payload["reason"] == "aborted" else "failed"
            outcome = {"reason": reason, "native_reason": payload["reason"], "native_turn_id": payload["turnId"],
                       "terminal": {"epoch": native["epoch"], "seq": native["native_seq"]}}
        _release(journal.db, run, status, outcome)
        return get(journal.db, run_id)


def complete_preparation(journal, run_id, generation, *, worktree_binding, snapshot):
    """Finish a service-written context draft, with no claimed runtime output.

    This is the honest observer/manual fallback when the adapter cannot prove
    the required tool boundary. A native turn has not been started or sent.
    """
    if (worktree_binding is not None and (not isinstance(snapshot, dict) or not snapshot)
            or worktree_binding is None and snapshot is not None):
        raise _error("PM preparation requires its saved snapshot receipt")
    outcome = {"mode": "manual", "runtime_available": False, "execution_authorized": False,
               "worktree_binding": worktree_binding, "snapshot": snapshot}
    with journal.tx():
        run = get(journal.db, run_id)
        if run["generation"] != generation:
            raise _error("PM writer changed", "PM_WRITER_CHANGED")
        if run["status"] == "prepared_manual":
            if run["outcome"] != outcome:
                raise _error("Original PM preparation differs")
            return run
        run = _active(journal.db, run_id, generation)
        require_requester(journal.db, run)
        if any(r["phase"] in {"start", "send"} for r in run["receipts"]):
            raise _error("PM native execution has been prepared; preserve its writer", "PM_EFFECT_UNKNOWN")
        if worktree_binding is not None:
            bind_worktree(journal, run_id, generation, worktree_binding)
        receipt(journal, run_id, generation, "prepare", "confirmed", outcome)
        _release(journal.db, run, "prepared_manual", outcome)
        return get(journal.db, run_id)


def complete_service(journal, run_id, generation, outcome):
    """Internal Git/journal completion, with no native assistant output claim.

    The adoption service calls this within the transaction recording its decision,
    or after proving every host effect when no decision can be made. Receipt
    reconciliation remains possible after requester membership is revoked.
    """
    with journal.tx():
        run = get(journal.db, run_id)
        operation = journal.db.execute("SELECT action FROM operations WHERE operation_id=?", (run["operation_id"],)).fetchone()
        if not operation or operation[0] != "pm.plan.adopt" or run["adapter"] != "connector.git":
            raise _error("Service completion requires the original plan adoption", "PM_SERVICE_REQUIRED")
        if run["generation"] != generation:
            raise _error("PM writer changed", "PM_WRITER_CHANGED")
        if run["status"] == "completed_service":
            if run["outcome"] != outcome:
                raise _error("Original service completion differs")
            return run
        run = _active(journal.db, run_id, generation)
        if any(r["phase"] in {"start", "send"} for r in run["receipts"]):
            raise _error("Native effects need their own completion proof", "PM_EFFECT_UNKNOWN")
        _release(journal.db, run, "completed_service", outcome)
        return get(journal.db, run_id)


def message_out(db, row):
    """Decorate shared discussion messages without changing sequence/source IDs."""
    result = dict(row)
    # Older journals can read local discussions before the runtime is installed.
    exists = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='pm_assistant_provenance'").fetchone()
    provenance = db.execute("SELECT * FROM pm_assistant_provenance WHERE message_id=?", (row["message_id"],)).fetchone() if exists else None
    result["role"] = "assistant" if provenance else "user"
    if provenance:
        result["provenance"] = {**dict(provenance), "source_refs": json.loads(provenance["source_refs"])}
    return result
