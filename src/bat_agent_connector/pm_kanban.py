"""Read-only workspace flow projection over existing work and execution journals.

No progress is stored here. A recorded content approval is a management boundary,
not a quality assessment, and accepted sends are not completed deliverables.
"""
from __future__ import annotations

import json
import time

from . import artifact_managed, pm_evidence, work_items
from .operations import OperationError

MAX_HISTORY = 500
MAX_ATTEMPTS = 100
MAX_BOARD_BYTES = 2_000_000
_FLOW_KINDS = ("work_item.created", "work_item.state", "work_item.updated", "work_item.approved", "work_item.continued")
_BINDING_FIELDS = ("binding_id", "sequence", "workspace_id", "work_item_id", "project_id", "project_binding_id",
                   "work_version", "previous_binding_id", "plan_revision_id", "requirement_ids", "created_at", "operation_id")
_WORK_FIELDS = ("work_item_id", "project_id", "title", "state", "version", "pinned", "archived", "created_at", "updated_at")


def _source(row):
    return {"kind": "work_event", "seq": row["seq"], "operation_id": pm_evidence._body(row["body"]).get("operation_id")}


def _unknown_flow(item, completion, reason):
    return {"basis": "recorded_work_content_approval", "history": reason,
            "wip": False if completion["approved"] else True if item["state"] != "todo" or completion["pending"] else None,
            "started_at": None, "state_since": None, "closed_at": None, "age_seconds": None,
            "cycle_seconds": None, "waiting_since": None, "waiting_seconds": None,
            "total_waiting_seconds": None, "reopened": None, "sources": [],
            "blocked": "unknown", "blocked_reason": None, "cancelled": "unknown"}


def _flow(ops, principal, wid, item, completion, now):
    """Replay bounded central state/approval events; never use updated_at as age."""
    rows = ops.db.execute("""SELECT * FROM api_events WHERE resource_type='work_item' AND resource_id=?
        AND kind IN (?,?,?,?,?) ORDER BY seq LIMIT ?""", (item["work_item_id"], *_FLOW_KINDS, MAX_HISTORY + 1)).fetchall()
    def unknown(reason):
        return _unknown_flow(item, completion, reason)
    if not rows or rows[0]["kind"] != "work_item.created":
        return unknown("missing_history")
    if len(rows) > MAX_HISTORY:
        return unknown("truncated_history")
    # Retention can remove a complete intermediate cycle without breaking a
    # remaining from/to chain. Its original atomic management receipt survives.
    expected = ops.db.execute("""SELECT o.operation_id FROM management_applied m JOIN operations o USING(operation_id)
        WHERE json_extract(m.result,'$.work_item_id')=? AND COALESCE(json_extract(m.result,'$.changed'),1)=1
        AND (o.action IN ('work_item.create','work_item.approve','work_item.continue')
             OR o.action='work_item.update' AND json_type(o.params,'$.archived') IS NULL)
        ORDER BY m.rowid LIMIT ?""", (item["work_item_id"], MAX_HISTORY + 1)).fetchall()
    seen = {pm_evidence._body(r["body"]).get("operation_id") for r in rows}
    if len(expected) > MAX_HISTORY or any(r[0] not in seen for r in expected):
        return unknown("missing_history")
    state, approved, started, closed, since = None, False, None, None, None
    waiting, reopened, last_time, ever_started = 0.0, set(), None, False
    sources = []
    for row in rows:
        body, at = pm_evidence._body(row["body"]), pm_evidence._time(row["created_at"])
        if at is None or at > now or last_time is not None and at < last_time:
            return unknown("clock_uncertain")
        op = pm_evidence._read_operation(ops, principal, wid, body.get("operation_id"))
        receipt = ops.db.execute("SELECT result FROM management_applied WHERE operation_id=?", (body.get("operation_id"),)).fetchone()
        result = pm_evidence._body(receipt[0]) if receipt else {}
        if op is None or result.get("work_item_id") != item["work_item_id"]:
            return unknown("unavailable_history")
        previous_state, previous_approval = state, approved
        kind = row["kind"]
        if kind == "work_item.created":
            if state is not None or op["action"] != "work_item.create":
                return unknown("inconsistent_history")
            state = op["params"].get("state", "todo")
        elif kind == "work_item.state":
            if op["action"] != "work_item.update" or body.get("from") != state:
                return unknown("inconsistent_history")
            state = body.get("to")
            approved = pm_evidence._body(result.get("completion")).get("approved")
        elif kind == "work_item.updated":
            if op["action"] != "work_item.update":
                return unknown("inconsistent_history")
            approved = pm_evidence._body(result.get("completion")).get("approved")
        elif kind == "work_item.approved":
            if op["action"] != "work_item.approve":
                return unknown("inconsistent_history")
            state, approved = "done", True
        elif kind == "work_item.continued":
            if op["action"] != "work_item.continue" or body.get("from") != state:
                return unknown("inconsistent_history")
            state, approved = body.get("to"), False
        if state not in work_items.STATES or type(approved) is not bool:
            return unknown("inconsistent_history")
        if previous_state == "waiting" and last_time is not None:
            waiting += at - last_time
        if previous_approval and not approved:
            reopened.add(op["operation_id"])
            started, closed, ever_started = at, None, True
        elif previous_state == "done" and state != "done":
            # Rejecting a completion claim reopens the display without resetting
            # the age of work that was never accepted in the first place.
            reopened.add(op["operation_id"])
        if state in {"doing", "waiting"} and not ever_started:
            started, ever_started = at, True
        if approved and not previous_approval:
            closed = at
        if state != previous_state:
            since = at
        last_time = at
        # Each event is a source identity, not a copy of its potentially private note.
        sources.append(_source(row))
    if state != item["state"] or approved != completion["approved"]:
        return unknown("inconsistent_history")
    if state == "waiting":
        waiting += now - last_time
    wip = not approved and (ever_started or state != "todo" or completion["pending"])
    return {"basis": "recorded_work_content_approval", "history": "available_central_events", "wip": wip,
            "started_at": started, "state_since": since, "closed_at": closed,
            "age_seconds": now - started if wip and started is not None else None,
            "cycle_seconds": closed - started if approved and started is not None else None,
            "waiting_since": since if state == "waiting" else None,
            "waiting_seconds": now - since if state == "waiting" else None,
            "total_waiting_seconds": waiting, "reopened": len(reopened), "sources": sources,
            "blocked": "unknown", "blocked_reason": None, "cancelled": "unknown"}


class _Execution:
    """Canonical sends are deduplicated by command ID, including operation wrappers."""
    def __init__(self, ops, principal, wid):
        self.ops, self.principal, self.wid = ops, principal, wid
        self.attempts, self.tasks, self.operations, self.sessions = {}, {}, {}, {}
        self.coverage = {"basis": "currently_linked_central_sources", "network_checked": False,
                         "unavailable_sources": False, "truncated": False, "unsupported_sources": False,
                         "removed_links": "not_scanned", "unlinked_activity": "not_scanned"}

    def command(self, cid, *, task_id=None):
        if cid in self.attempts and (task_id is None or self.attempts[cid].get("task_id") == task_id):
            return
        row = self.ops.db.execute("SELECT * FROM commands WHERE command_id=?", (cid,)).fetchone()
        if (row is None or not pm_evidence._command_visible(self.ops, self.principal, self.wid, cid)
                or task_id is not None and row["task_id"] != task_id):
            self.coverage["unavailable_sources"] = True
            return
        if row["kind"] != "send":
            return
        if len(self.attempts) >= MAX_ATTEMPTS:
            self.coverage["truncated"] = True
            return
        accepted = self.ops.db.execute("""SELECT event_id,created_at FROM events WHERE task_id=?
            AND kind IN ('command_accepted','command_settled') AND json_extract(body,'$.command_id')=?
            ORDER BY event_id LIMIT 1""", (row["task_id"], cid)).fetchone()
        self.attempts[cid] = {"attempt_id": cid, "kind": "task_command", "task_id": row["task_id"],
                             "command_id": cid, "session_id": row["session_id"], "message_id": row["message_id"],
                             "status": row["status"], "accepted": True if accepted else None,
                             "accepted_at": pm_evidence._time(accepted["created_at"]) if accepted else None,
                             "source": {"kind": "command", "command_id": cid,
                                        "accepted_event_id": accepted["event_id"] if accepted else None},
                             "completion": "unassessed", "observed_at": pm_evidence._time(row["updated_at"])}

    def task(self, tid):
        if tid in self.tasks:
            return
        if not pm_evidence._task_visible(self.ops, self.principal, self.wid, tid):
            self.coverage["unavailable_sources"] = True
            return
        task = self.ops.db.execute("SELECT * FROM tasks WHERE task_id=?", (tid,)).fetchone()
        self.tasks[tid] = {**pm_evidence._fields(dict(task), ("task_id", "state", "paused", "control_version", "session_id")),
                           "observed_at": pm_evidence._time(task["updated_at"]), "state_since": None,
                           "timing": "unknown", "completion": "unassessed"}
        rows = self.ops.db.execute("SELECT command_id FROM commands WHERE task_id=? AND kind='send' ORDER BY rowid LIMIT ?",
                                   (tid, MAX_ATTEMPTS + 1)).fetchall()
        self.coverage["truncated"] |= len(rows) > MAX_ATTEMPTS
        for row in rows[:MAX_ATTEMPTS]:
            self.command(row[0], task_id=tid)

    def operation(self, oid):
        if oid in self.operations:
            return
        op = pm_evidence._read_operation(self.ops, self.principal, self.wid, oid)
        if op is None:
            self.coverage["unavailable_sources"] = True
            return
        self.operations[oid] = {**pm_evidence._fields(op, ("operation_id", "action", "status", "version", "cancel_requested")),
                                "observed_at": pm_evidence._time(op["updated_at"])}
        if op["action"] not in artifact_managed.EXECUTIONS:
            return
        refs = op.get("external_refs") or {}
        if refs.get("command_id"):
            if not refs.get("task_id"):
                self.coverage["unavailable_sources"] = True
                return
            self.command(refs["command_id"], task_id=refs["task_id"])
            return
        if refs.get("task_id"):
            self.coverage["unavailable_sources"] = True
            return
        if len(self.attempts) >= MAX_ATTEMPTS:
            self.coverage["truncated"] = True
            return
        step = self.ops.db.execute("SELECT * FROM operation_steps WHERE operation_id=? AND name='send'", (oid,)).fetchone()
        request, response = (pm_evidence._body(step[key]) if step else {} for key in ("request", "response"))
        accepted = bool(step and step["status"] == "succeeded" and response.get("accepted") is True
                        and isinstance(request.get("message_id"), str) and request["message_id"]
                        and request["message_id"] == response.get("message_id"))
        self.attempts[oid] = {"attempt_id": oid, "kind": "execution_operation", "operation_id": oid,
                             "status": op["status"], "accepted": True if accepted else None,
                             "accepted_at": pm_evidence._time(step["finished_at"]) if accepted else None,
                             "source": {"kind": "operation_send", "operation_id": oid, "step_seq": step["seq"] if step else None},
                             "completion": "unassessed", "observed_at": pm_evidence._time(op["updated_at"])}
        from . import pm_execution_runtime
        try:
            self.attempts[oid]['runtime_completion'] = pm_execution_runtime.summary(self.ops, self.principal, op)
        except OperationError:
            # An inaccessible original receipt cannot leak even its presence.
            self.attempts[oid]['runtime_completion'] = None
            self.coverage['unavailable_sources'] = True


    def session(self, ref):
        if not pm_evidence._session_visible(self.ops, self.principal, self.wid, ref):
            self.coverage["unavailable_sources"] = True
            return
        host, _, sid = ref.partition("/")
        row = self.ops.db.execute("SELECT * FROM sessions_observed WHERE host=? AND session_id=?", (host, sid)).fetchone()
        if row is None:
            self.coverage["unavailable_sources"] = True
            return
        body = pm_evidence._body(row["body"])
        self.sessions[ref] = {"resource_id": ref, "session_id": sid,
                              **pm_evidence._fields(body, ("streaming", "runtime_status", "stale")),
                              "observed_at": pm_evidence._time(row["last_seen_at"]), "freshness": "saved_observation"}

    def collect(self, work_id):
        rows = self.ops.db.execute("""SELECT kind,ref FROM work_item_links WHERE work_item_id=? AND removed_at IS NULL
            ORDER BY link_id LIMIT ?""", (work_id, pm_evidence.MAX_LINKS + 1)).fetchall()
        self.coverage["truncated"] |= len(rows) > pm_evidence.MAX_LINKS
        for row in rows[:pm_evidence.MAX_LINKS]:
            handler = {"operation": self.operation, "task": self.task, "session": self.session}.get(row["kind"])
            if handler:
                handler(row["ref"])
            else:
                self.coverage["unsupported_sources"] = True
        return {"attempts": list(self.attempts.values()), "accepted_attempts": sum(a["accepted"] is True for a in self.attempts.values()),
                "attempts_basis": "visible_send_identities_not_operation_retries_or_sessions",
                "tasks": list(self.tasks.values()), "operations": list(self.operations.values()), "sessions": list(self.sessions.values()),
                "coverage": self.coverage}


def _card(ops, principal, wid, binding, now):
    from .pm_quality_projections import card_quality
    from .pm_session_quiescence import sources as manual_sources
    item = work_items._get_item(ops.db, binding["work_item_id"])
    completion = work_items.completion(item)
    project = work_items._get_project(ops.db, item["project_id"])
    archived = bool(item["archived"] or project["archived"])
    column = ("archived" if archived else "accepted" if completion["approved"] else "review" if completion["pending"]
              else {"todo": "backlog", "doing": "in_progress", "waiting": "waiting", "done": "review"}[item["state"]])
    capture = pm_evidence.latest_capture(ops, principal, wid, item["work_item_id"])["capture"]
    return {"work_item_id": item["work_item_id"], "binding": {k: binding[k] for k in _BINDING_FIELDS},
            "work": {k: item[k] for k in _WORK_FIELDS}, "column": column, "completion": completion,
            "project_archived": bool(project["archived"]), "flow": _flow(ops, principal, wid, item, completion, now),
            "execution": {**_Execution(ops, principal, wid).collect(item["work_item_id"]),
                "manual_observation_sources": manual_sources(ops, principal, wid, item["work_item_id"])},
            "quality": card_quality(ops, principal, wid, binding, capture),
            "latest_capture": capture,
            "capture_matches_binding": capture["binding_id"] == binding["binding_id"] if capture else None,
            "capture_matches_work_version": capture["source_work_version"] == item["version"] if capture else None,
            "dependencies": {"status": "unknown", "items": [], "reason": "no_work_dependency_contract"},
            "assignment": {"status": "unknown", "actor": None, "reason": "creator_is_not_assignee"}}


def _capabilities():
    return {"read_only": True, "dispatch": False, "session_control": False, "quality_evaluation": False,
            "ready_gate": False, "work_dependencies": False, "assignment": False,
            "wip_limit": {"status": "not_configured", "value": None},
            "resource_capacity": {"status": "unknown", "value": None}, "cost": "unknown"}


def _wip(cards):
    active = [c for c in cards if c["column"] != "archived"]
    return {"scope": "returned_page", "basis": "started_not_content_approved", "count": sum(c["flow"]["wip"] is True for c in active),
            "unknown": sum(c["flow"]["wip"] is None for c in active), "cards": len(active),
            "archived_excluded": len(cards) - len(active), "limit": None}


def _read_size(value):
    # Match the UTF-8 HTTP payload, including JSON separators and escapes.
    return len(json.dumps(value, ensure_ascii=False, allow_nan=False).encode('utf-8'))


def get_board(ops, principal, workspace_id, *, project_id=None, after=0, limit=50):
    with ops.journal.tx():
        # list_work checks current membership, observe, IDs and bounded pagination.
        page = pm_evidence.list_work(ops, principal, workspace_id, project_id=project_id, after=after, limit=limit)
        now, cursor, cards = time.time(), ops.journal.api_head(), []
        out = {"version": 1, "workspace_id": workspace_id, "project_id": project_id, "observed_at": now,
               "journal_cursor": cursor, "cards": cards, "next_cursor": page["next_cursor"], "capabilities": _capabilities(),
               "coverage": {"basis": "current_explicit_work_bindings", "network_checked": False,
                            "scope": "returned_page", "pagination_snapshot": False, "byte_limited": False}}
        size = _read_size(out)
        for binding in page["bindings"]:
            card = _card(ops, principal, workspace_id, binding, now)
            size += _read_size(card) + 2
            if size > MAX_BOARD_BYTES:
                if not cards:
                    raise OperationError("PM_KANBAN_TOO_LARGE", "This work projection exceeds the bounded board response", 413)
                out["next_cursor"] = cards[-1]["binding"]["sequence"]
                out["coverage"]["byte_limited"] = True
                break
            cards.append(card)
        out["wip"] = _wip(cards)
        while _read_size(out) > MAX_BOARD_BYTES:
            # Include the actual UTF-8 counter/envelope size without rereading or
            # advancing past a card omitted from the response.
            if len(cards) < 2:
                raise OperationError("PM_KANBAN_TOO_LARGE", "This work projection exceeds the bounded board response", 413)
            cards.pop()
            out["next_cursor"] = cards[-1]["binding"]["sequence"]
            out["coverage"]["byte_limited"] = True
            out["wip"] = _wip(cards)
        return out


def get_card(ops, principal, workspace_id, work_item_id):
    with ops.journal.tx():
        pm_evidence._member(ops, principal, workspace_id)
        if not isinstance(work_item_id, str) or not work_items.WORK_ITEM_ID.fullmatch(work_item_id):
            raise OperationError("PM_WORK_NOT_FOUND", "Work not found in this workspace", 404)
        binding = pm_evidence._binding(ops.db, workspace_id, work_item_id)
        if binding is None:
            raise OperationError("PM_WORK_NOT_FOUND", "Work not found in this workspace", 404)
        now = time.time()
        result = {"version": 1, "workspace_id": workspace_id, "observed_at": now, "journal_cursor": ops.journal.api_head(),
                  "card": _card(ops, principal, workspace_id, binding, now)}
        if _read_size(result) > MAX_BOARD_BYTES:
            raise OperationError("PM_KANBAN_TOO_LARGE", "This work projection exceeds the bounded card response", 413)
        return result
