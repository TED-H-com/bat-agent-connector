"""Read-only project schedule for the Gantt view.

Dates come only from the current roadmap baseline and forecast revisions and from
explicit execution evidence. This module does not compute a forecast, a critical
path, phase rows, or token attribution, and it does not write.
"""
from __future__ import annotations

import base64
import json
import math
import re
from contextvars import ContextVar
from datetime import datetime, timezone

from . import pm_evidence, pm_roadmap, pm_source_lineage, work_items
from .operations import STATES as OPERATION_STATES
from .operations import TERMINAL as OPERATION_TERMINAL
from .operations import OperationError
from .task_journal import STATES as TASK_STATES

MAX_WORK = 200
MAX_LINKS_PER_WORK = 50
MAX_LINKS_TOTAL = 200
MAX_ISSUES = 200
MAX_BYTES = 2_000_000
_BINDING_CHUNK = 200
# Mappings examined, including ones list_mappings or work visibility will not emit.
_ISSUE_SCAN = 1000
# Links examined per schedule read, hidden ones included, before the 50 and 200 caps.
_LINK_SCAN = 1000
_LINK_PAGE = 200
# Rows examined per session, hidden ones included. Visibility is decided before any run is chosen.
_RUN_SCAN = 64
_RANGE_BOUND = {"start": "0000-00-00", "end": "0000-00-00"}
_PAGE_DEFAULT = 100
_RANK = {"milestone": 0, "requirement": 1, "issue": 2, "work": 3, "attempt": 4}
_CURSOR_TOKEN = re.compile(r"^[A-Za-z0-9_-]{1,4096}$")
_CURSOR_RAW_MAX = 3072
_DATE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")
_SHA = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
_MERGE_ACTIONS = frozenset({"github.pr.merge", "delivery.merge_and_deploy"})
_RUNNING = frozenset({"running", "streaming", "starting", "active"})
_TASK_WAIT = {"waiting_permission": "permission", "quota_limited": "quota", "human_owned": "human", "needs_ted": "human"}
_OPERATION_WAIT = {"waiting_checks": "checks", "waiting_external": "external"}
_SOURCE_FLAGS = ("attempts_truncated", "attempts_unavailable", "work_bindings_truncated", "issues_truncated")
_OPERATION_KINDS = tuple(f"operation.{name}" for name in OPERATION_STATES)
_CACHE = ContextVar("pm_schedule_cache", default=None)


def capabilities():
    return {"version": 1, "read_only": True, "forecast": "recorded_revision_only", "critical_path": False,
            "token_attribution": False, "phases": False, "drag_proposal": False}


def read(ops, principal, workspace_id, *, after=None, limit=None):
    """Project one workspace schedule. Membership is checked on every call."""
    limit = _PAGE_DEFAULT if limit is None else limit
    if type(limit) is not int or not 1 <= limit <= _PAGE_DEFAULT:
        raise OperationError("INVALID_PARAMS", "limit must be 1-100", 422)
    cursor = _cursor(after)
    token = _CACHE.set({})
    try:
        with pm_source_lineage.memoized_reads():
            with ops.journal.read_tx():
                pm_roadmap._member(ops, principal, workspace_id)
                loaded = _load(ops, principal, workspace_id)
                ordered = sorted(loaded["nodes"], key=lambda node: (_RANK[node["kind"]], node["id"]))
                if cursor is not None:
                    ordered = [node for node in ordered if (_RANK[node["kind"]], node["id"]) > cursor]
                return _fit(loaded, ordered, limit)
    finally:
        _CACHE.reset(token)


def _encode_cursor(row_id):
    return base64.urlsafe_b64encode(row_id.encode()).rstrip(b"=").decode("ascii")


def _cursor(after):
    if after is None:
        return None
    if not isinstance(after, str) or not _CURSOR_TOKEN.fullmatch(after):
        raise OperationError("INVALID_PARAMS", "after must be a schedule cursor", 422)
    padded = after + "=" * (-len(after) % 4)
    try:
        text = base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8")
    except (ValueError, UnicodeError):
        raise OperationError("INVALID_PARAMS", "after must be a schedule cursor", 422) from None
    kind, sep, rest = text.partition(":")
    if sep != ":" or kind not in _RANK or not rest or len(text) > _CURSOR_RAW_MAX:
        raise OperationError("INVALID_PARAMS", "after must be a schedule cursor", 422)
    if any(ord(char) < 32 or ord(char) == 127 for char in text):
        raise OperationError("INVALID_PARAMS", "after must be a schedule cursor", 422)
    return _RANK[kind], text


def _cached(key, factory):
    cache = _CACHE.get()
    if cache is None:
        return factory()
    if key not in cache:
        cache[key] = factory()
    return cache[key]


def _instant(value):
    if type(value) not in (int, float) or not math.isfinite(value):
        return None
    return datetime.fromtimestamp(value, timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _day(value):
    return value if isinstance(value, str) and _DATE.fullmatch(value) else None


def _label(value, limit):
    if not isinstance(value, str):
        return None
    text = value.strip()
    if (not text or len(text) > limit or text.startswith(("/", "\\"))
            or any(ord(char) < 32 or ord(char) == 127 for char in text)):
        return None
    return text


def _plain(value):
    """Non-blank planning or issue text. Newlines, tabs and a leading slash stay."""
    if not isinstance(value, str):
        return None
    text = value.strip()
    return text or None


def _span(value):
    day = _day(value)
    return {"start": day, "end": day} if day else None


def _table(db, name):
    return db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None


def _marks(ids):
    return ",".join("?" * len(ids))


def _note(bucket, field, reason):
    bucket.add((field, reason))


def _load(ops, principal, workspace_id):
    db = ops.db
    head = pm_roadmap.head(db, workspace_id)
    baseline = pm_roadmap._revision(db, workspace_id, head["baseline_revision_id"]) if head["baseline_revision_id"] else None
    forecast = pm_roadmap._revision(db, workspace_id, head["forecast_revision_id"]) if head["forecast_revision_id"] else None
    notes = set()
    coverage = {"basis": "explicit_roadmap_scope_and_links", "visibility_filtered": False, "attempts_truncated": False,
                "attempts_unavailable": False, "work_bindings_truncated": False, "issues_truncated": False,
                "network_checked": False,
                "forecast": "recorded_revision_only", "critical_path": False, "token_attribution": False}
    for field, reason in (("phase", "no_explicit_phase_records"), ("blocked", "no_explicit_blocked_record"),
                          ("ci_wait", "no_explicit_check_binding"), ("token_usage", "no_explicit_attribution")):
        _note(notes, field, reason)
    if baseline is None:
        _note(notes, "baseline", "no_adopted_schedule_baseline")
    else:
        _note(notes, "milestone.actual", "no_explicit_finish_evidence")
    if forecast is not None and (baseline is None or forecast["document"].get("baseline_revision_id") != baseline["revision_id"]):
        _note(notes, "forecast", "forecast_baseline_mismatch")
        forecast = None
    milestones = _milestones(baseline, forecast)
    requirements = _requirements(ops, principal, workspace_id, milestones, notes, coverage)
    works, attempts = _works(ops, principal, workspace_id, milestones, requirements, notes, coverage)
    issues = _issues(ops, principal, workspace_id, milestones, notes, coverage)
    if works:
        _note(notes, "work.depends_on", "no_work_dependency_contract")
    return {"workspace_id": workspace_id, "db": db, "nodes": [*milestones, *requirements.values(), *issues, *works, *attempts],
            "baseline": baseline, "forecast": forecast, "notes": notes, "coverage": coverage}


def _milestones(baseline, forecast):
    if baseline is None:
        return []
    dates = {}
    if forecast is not None:
        for item in forecast["document"].get("milestones") or []:
            if isinstance(item, dict) and isinstance(item.get("milestone_id"), str):
                dates[item["milestone_id"]] = item.get("forecast_date")
    known = {item.get("milestone_id") for item in baseline["document"].get("milestones") or [] if isinstance(item, dict)}
    rows = []
    for item in baseline["document"].get("milestones") or []:
        if not isinstance(item, dict) or not isinstance(item.get("milestone_id"), str):
            continue
        mid = item["milestone_id"]
        deps = [f"milestone:{dep}" for dep in item.get("depends_on") or []
                if isinstance(dep, str) and dep in known and dep != mid]
        refs = {"milestone_id": mid, "plan_revision_id": item.get("plan_revision_id"),
                "requirement_ids": [rid for rid in item.get("requirement_ids") or [] if isinstance(rid, str)]}
        title = item.get("title") if isinstance(item.get("title"), str) else mid
        node = _node(f"milestone:{mid}", None, "milestone", title, "unknown", _span(item.get("baseline_date")),
                     _span(dates.get(mid)), None, deps, refs, date_kind="milestone")
        node["_work_binding_ids"] = [bid for bid in item.get("work_binding_ids") or [] if isinstance(bid, str)]
        rows.append(node)
    return rows


def _requirements(ops, principal, workspace_id, milestones, notes, coverage):
    grouped = {}
    for milestone in milestones:
        revision = milestone["refs"].get("plan_revision_id")
        for rid in milestone["refs"].get("requirement_ids") or []:
            grouped.setdefault((revision, rid), []).append(milestone["id"])
    plans, rows, outside = {}, {}, False
    for (revision, rid), parents in grouped.items():
        if not isinstance(revision, str):
            _note(notes, "requirement", "requirement_text_unavailable")
            continue
        if revision not in plans:
            plans[revision] = _plan(ops, principal, workspace_id, revision, coverage)
        plan = plans[revision]
        if plan is None:
            continue
        source = next((item for item in plan["document"].get("requirements") or [] if item.get("id") == rid), None)
        # Planning input is kept verbatim. A missing text still emits the row so edges stay valid.
        title = _plain((source or {}).get("text")) if source else None
        if title is None:
            _note(notes, "requirement", "requirement_text_unavailable")
            title = rid
        parent_ids = list(dict.fromkeys(parents))
        parent = parent_ids[0] if len(parent_ids) == 1 else None
        if parent is None:
            _note(notes, "requirement.parent", "multiple_explicit_milestones")
        deps = []
        for dep in (source or {}).get("depends_on") or []:
            if (revision, dep) in grouped:
                deps.append(f"requirement:{revision}:{dep}")
            else:
                outside = True
        refs = {"requirement_id": rid, "plan_revision_id": revision}
        if len(parent_ids) == 1:
            refs["milestone_id"] = parent_ids[0].split(":", 1)[1]
        else:
            refs["milestone_ids"] = [item.split(":", 1)[1] for item in parent_ids]
        rows[(revision, rid)] = _node(f"requirement:{revision}:{rid}", parent, "requirement", title, "unknown",
                                      None, None, None, deps, refs)
    present = {node["id"] for node in rows.values()}
    for node in rows.values():
        node["depends_on"] = [dep for dep in node["depends_on"] if dep in present]
    if outside:
        _note(notes, "requirement.depends_on", "requirement_dependency_outside_baseline")
    return rows


def _plan(ops, principal, workspace_id, revision, coverage):
    key = ("plan", workspace_id, revision)

    def load():
        from . import pm_plans
        try:
            return pm_plans.get_plan(ops, principal, workspace_id, revision)["plan"]
        except OperationError as error:
            if error.status == 403:
                raise
            if error.status == 404:
                coverage["visibility_filtered"] = True
                return None
            raise

    cache = _CACHE.get()
    if cache is not None and key in cache:
        return cache[key]
    plan = load()
    if cache is not None:
        cache[key] = plan
    return plan


def _works(ops, principal, workspace_id, milestones, requirements, notes, coverage):
    db = ops.db
    selected, truncated = [], False
    for row in _current_bindings(db, workspace_id):
        binding = {**dict(row), "requirement_ids": json.loads(row["requirement_ids"])}
        if not _in_scope(binding, milestones):
            continue
        if not _work_visible(db, principal, binding["work_item_id"], coverage):
            continue
        if len(selected) >= MAX_WORK:
            truncated = True
            break
        selected.append(binding)
    if truncated:
        coverage["work_bindings_truncated"] = True
        _note(notes, "work", "binding_page_truncated")
    items = _work_items(db, [row["work_item_id"] for row in selected])
    links, truncated_works = _links(ops, principal, workspace_id, [row["work_item_id"] for row in selected], coverage, notes)
    evidence = _evidence(ops, principal, workspace_id, links, notes, coverage)
    works, attempts = [], []
    for binding in selected:
        item = items.get(binding["work_item_id"])
        if item is None:
            _note(notes, "work", "work_item_unavailable")
            continue
        own = links.get(binding["work_item_id"], [])
        unavailable = any((link["kind"] == "operation" and link["ref"] in evidence["missing_operations"])
                          or (link["kind"] == "task" and link["ref"] in evidence["missing_tasks"]) for link in own)
        attempt_rows = [row for row in (_attempt(binding["work_item_id"], link, evidence, notes) for link in own) if row]
        attempts.extend(attempt_rows)
        parent = _work_parent(binding, milestones, requirements, notes)
        withheld = unavailable or binding["work_item_id"] in truncated_works
        works.append(_work_node(db, binding, item, parent, attempt_rows, withheld, evidence, notes))
    return works, attempts


def _current_bindings(db, workspace_id):
    sequence = 0
    while True:
        rows = db.execute("""SELECT b.* FROM pm_work_bindings b WHERE b.workspace_id=? AND b.sequence>? AND NOT EXISTS(
            SELECT 1 FROM pm_work_bindings n WHERE n.workspace_id=b.workspace_id AND n.work_item_id=b.work_item_id
            AND n.sequence>b.sequence) ORDER BY b.sequence LIMIT ?""",
                          (workspace_id, sequence, _BINDING_CHUNK)).fetchall()
        if not rows:
            return
        yield from rows
        sequence = rows[-1]["sequence"]
        if len(rows) < _BINDING_CHUNK:
            return


def _work_visible(db, principal, work_id, coverage):
    def load():
        try:
            return pm_source_lineage.visible(db, principal, "work_item", work_id)
        except OperationError:
            return False

    allowed = _cached(("work", work_id), load)
    if not allowed:
        coverage["visibility_filtered"] = True
    return allowed


def _in_scope(binding, milestones):
    if any(binding["binding_id"] in milestone.get("_work_binding_ids", []) for milestone in milestones):
        return True
    selected = set(binding["requirement_ids"])
    return any(binding["plan_revision_id"] == milestone["refs"].get("plan_revision_id")
               and selected & set(milestone["refs"].get("requirement_ids") or []) for milestone in milestones)


def _work_items(db, ids):
    if not ids:
        return {}
    rows = db.execute(f"SELECT * FROM work_items WHERE work_item_id IN ({_marks(ids)})", tuple(ids)).fetchall()  # noqa: S608
    return {row["work_item_id"]: work_items._item(row) for row in rows}


def _link_verdict(ops, principal, workspace_id, row):
    """Classify one link before it can take a cap slot. Missing records still count."""
    kind, ref = row["kind"], row["ref"]
    try:
        if kind == "session":
            return "visible" if pm_evidence._session_visible(ops, principal, workspace_id, ref) else "hidden"
        if kind == "operation":
            if pm_evidence._read_operation(ops, principal, workspace_id, ref) is not None:
                return "visible"
            exists = ops.db.execute("SELECT 1 FROM operations WHERE operation_id=?", (ref,)).fetchone()
            return "hidden" if exists else "missing"
        if kind == "task":
            if ops.db.execute("SELECT 1 FROM tasks WHERE task_id=?", (ref,)).fetchone() is None:
                return "missing"
            return "visible" if pm_evidence._task_visible(ops, principal, workspace_id, ref) else "hidden"
    except OperationError:
        return "hidden"
    return "hidden"


def _links(ops, principal, workspace_id, ids, coverage, notes):
    """Count visible and missing links, in link_id order, before either cap."""
    if not ids:
        return {}, set()
    db = ops.db
    wanted = list(dict.fromkeys(ids))
    grouped, truncated = {}, set()
    examined, counted, last_id = 0, 0, 0
    while examined < _LINK_SCAN:
        room = min(_LINK_PAGE, _LINK_SCAN - examined)
        rows = db.execute(
            f"""SELECT work_item_id, link_id, kind, ref FROM work_item_links WHERE removed_at IS NULL
                AND kind IN ('session','operation','task') AND work_item_id IN ({_marks(wanted)}) AND link_id>?
                ORDER BY link_id LIMIT ?""", (*wanted, last_id, room + 1)).fetchall()  # noqa: S608
        if not rows:
            break
        extra = len(rows) > room
        for row in rows[:room]:
            examined += 1
            last_id = row["link_id"]
            if _link_verdict(ops, principal, workspace_id, row) == "hidden":
                coverage["visibility_filtered"] = True
                continue
            work_id = row["work_item_id"]
            have = grouped.setdefault(work_id, [])
            if len(have) >= MAX_LINKS_PER_WORK or counted >= MAX_LINKS_TOTAL:
                truncated.add(work_id)
                continue
            have.append(row)
            counted += 1
        if not extra:
            break
        if examined >= _LINK_SCAN:
            rest = db.execute(
                f"""SELECT DISTINCT work_item_id FROM work_item_links WHERE removed_at IS NULL
                    AND kind IN ('session','operation','task') AND work_item_id IN ({_marks(wanted)})
                    AND link_id>?""", (*wanted, last_id)).fetchall()  # noqa: S608
            truncated.update(row[0] for row in rest)
            break
    if truncated:
        coverage["attempts_truncated"] = True
        _note(notes, "work.actual", "attempt_links_truncated")
    return grouped, truncated


def _evidence(ops, principal, workspace_id, links, notes, coverage):
    sessions, operations, tasks = [], [], []
    for rows in links.values():
        for row in rows:
            if row["kind"] == "session":
                sessions.append(row["ref"])
            elif row["kind"] == "operation":
                operations.append(row["ref"])
            elif row["kind"] == "task":
                tasks.append(row["ref"])
    loaded_sessions, unobserved = _sessions(ops, principal, workspace_id, sessions, coverage)
    loaded_operations, ends, missing_operations = _operations(ops, principal, workspace_id, operations, notes, coverage)
    loaded_tasks, missing_tasks = _tasks(ops, principal, workspace_id, tasks, notes, coverage)
    return {"db": ops.db, "sessions": loaded_sessions, "unobserved_sessions": unobserved,
            "operations": loaded_operations, "ends": ends, "missing_operations": missing_operations,
            "tasks": loaded_tasks, "missing_tasks": missing_tasks,
            "operation_since": _operation_since(ops.db, loaded_operations),
            "runs": _runs(ops, principal, workspace_id, list(loaded_sessions), notes, coverage),
            "merges": _merges(ops.db, list(loaded_operations)),
            "deployments": _deployments(ops.db, list(loaded_operations))}


def _sessions(ops, principal, workspace_id, refs, coverage):
    pairs = []
    for ref in dict.fromkeys(refs):
        host, sep, sid = ref.partition("/")
        if sep and host and sid:
            pairs.append((host, sid, ref))
    if not pairs:
        return {}, set()
    rows = ops.db.execute(
        f"SELECT * FROM sessions_observed WHERE (host, session_id) IN ({','.join('(?,?)' for _ in pairs)})",  # noqa: S608
        tuple(part for host, sid, _ref in pairs for part in (host, sid))).fetchall()
    by_ref = {f"{row['host']}/{row['session_id']}": row for row in rows}
    found, unobserved = {}, set()
    for _host, _sid, ref in pairs:
        # Visibility does not need the observation row. A lineage error hides this one record.
        try:
            allowed = pm_evidence._session_visible(ops, principal, workspace_id, ref)
        except OperationError:
            allowed = False
        if not allowed:
            coverage["visibility_filtered"] = True
            continue
        row = by_ref.get(ref)
        if row is None:
            unobserved.add(ref)
            continue
        found[ref] = row
    return found, unobserved


def _operations(ops, principal, workspace_id, ids, notes, coverage):
    ids = list(dict.fromkeys(ids))
    found, missing = {}, set()
    if ids:
        existing = {row[0] for row in ops.db.execute(
            f"SELECT operation_id FROM operations WHERE operation_id IN ({_marks(ids)})", tuple(ids))}  # noqa: S608
    else:
        existing = set()
    for operation_id in ids:
        op = pm_evidence._read_operation(ops, principal, workspace_id, operation_id)
        if op is None:
            if operation_id in existing:
                coverage["visibility_filtered"] = True
            else:
                # No row, so visibility cannot be proved. Omit the id and mark the page partial.
                missing.add(operation_id)
                coverage["attempts_unavailable"] = True
                _note(notes, "attempt.operation", "operation_record_missing")
            continue
        found[operation_id] = op
    return found, _operation_ends(ops.db, found), missing


def _operation_ends(db, operations):
    """Terminal end is that status's own step time, not an earlier succeeded step."""
    terminal = {oid: op.get("status") for oid, op in operations.items() if op.get("status") in OPERATION_TERMINAL}
    if not terminal:
        return {}
    ids = list(terminal)
    rows = db.execute(
        f"""SELECT operation_id, status, MAX(finished_at) AS finished_at FROM operation_steps
            WHERE finished_at IS NOT NULL AND status IN ('succeeded','failed','cancelled')
            AND operation_id IN ({_marks(ids)}) GROUP BY operation_id, status""", tuple(ids)).fetchall()  # noqa: S608
    by_status = {}
    for row in rows:
        by_status.setdefault(row["operation_id"], {})[row["status"]] = row["finished_at"]
    ends = {}
    for operation_id, status in terminal.items():
        stamp = by_status.get(operation_id, {}).get(status)
        if stamp is not None:
            ends[operation_id] = _instant(stamp)
    return ends


def _operation_since(db, operations):
    wanted = {oid: op.get("status") for oid, op in operations.items() if op.get("status") in _OPERATION_WAIT}
    if not wanted:
        return {}
    ids, kinds = list(wanted), list(_OPERATION_KINDS)
    rows = db.execute(
        f"""SELECT resource_id, body, created_at FROM (
                SELECT resource_id, body, created_at,
                    ROW_NUMBER() OVER (PARTITION BY resource_id ORDER BY seq DESC) AS n
                FROM api_events WHERE resource_type='operation' AND resource_id IN ({_marks(ids)})
                AND kind IN ({_marks(kinds)})) WHERE n=1""", (*ids, *kinds)).fetchall()  # noqa: S608
    found = {}
    for row in rows:
        status = wanted.get(row["resource_id"])
        try:
            body = json.loads(row["body"])
        except (TypeError, ValueError):
            continue
        if isinstance(body, dict) and body.get("to") == status:
            found[row["resource_id"]] = _instant(row["created_at"])
    return found


def _tasks(ops, principal, workspace_id, ids, notes, coverage):
    ids = list(dict.fromkeys(ids))
    if not ids:
        return {}, set()
    rows = ops.db.execute(
        f"""SELECT task_id, state, submitted_at, delivered_at, lead_agent, host, session_id, verifying_started_at
            FROM tasks WHERE task_id IN ({_marks(ids)})""", tuple(ids)).fetchall()  # noqa: S608
    present = {row["task_id"]: row for row in rows}
    found, missing = {}, set()
    for task_id in ids:
        if task_id not in present:
            missing.add(task_id)
            coverage["attempts_unavailable"] = True
            _note(notes, "attempt.task", "task_record_missing")
            continue
        try:
            allowed = pm_evidence._task_visible(ops, principal, workspace_id, task_id)
        except OperationError:
            allowed = False
        if not allowed:
            coverage["visibility_filtered"] = True
            continue
        found[task_id] = present[task_id]
    terminal = {task_id: row["state"] for task_id, row in found.items() if row["state"] in {"done", "failed"}}
    ends = _task_transitions(ops.db, terminal)
    since = {task_id: _instant(stamp) for task_id, stamp in _task_transitions(ops.db, {
        task_id: row["state"] for task_id, row in found.items() if row["state"] in _TASK_WAIT}).items()}
    return ({task_id: {"row": row, "terminal_at": ends.get(task_id), "since": since.get(task_id)}
             for task_id, row in found.items()}, missing)


def _task_transitions(db, wanted):
    """Latest event_id whose body has task-state `from` and `to`, and whose `to` is the current state."""
    if not wanted:
        return {}
    states = tuple(sorted(TASK_STATES))
    ids = list(wanted)
    fetched = db.execute(
        f"""SELECT task_id, body, created_at FROM events WHERE task_id IN ({_marks(ids)})
            AND json_extract(body,'$.from') IN ({_marks(states)})
            AND json_extract(body,'$.to') IN ({_marks(states)})
            ORDER BY event_id DESC""", (*ids, *states, *states)).fetchall()  # noqa: S608
    found = {}
    for row in fetched:
        task_id = row["task_id"]
        if task_id in found:
            continue
        try:
            body = json.loads(row["body"])
        except (TypeError, ValueError):
            continue
        if isinstance(body, dict) and body.get("from") in TASK_STATES and body.get("to") == wanted[task_id]:
            found[task_id] = row["created_at"]
    return found


def _runs(ops, principal, workspace_id, refs, notes, coverage):
    """Attach refs.run_id only from visible runs bound to a linked observed session."""
    db = ops.db
    if not refs or not _table(db, "pm_runs") or not _table(db, "pm_run_state"):
        return {}
    pairs = []
    for ref in dict.fromkeys(refs):
        host, sep, sid = ref.partition("/")
        if sep and host and sid:
            pairs.append((host, sid, ref))
    if not pairs:
        return {}
    marks = ",".join("(?,?)" for _ in pairs)
    args = [workspace_id]
    for host, sid, _ref in pairs:
        args.extend((host, sid))
    rows = db.execute(
        f"""SELECT run_id, operation_id, context, host, session_id, created_at FROM (
                SELECT r.run_id AS run_id, r.operation_id AS operation_id, r.context AS context,
                    json_extract(s.session_binding,'$.host') AS host,
                    json_extract(s.session_binding,'$.session_id') AS session_id,
                    r.created_at AS created_at,
                    ROW_NUMBER() OVER (
                        PARTITION BY json_extract(s.session_binding,'$.host'),
                            json_extract(s.session_binding,'$.session_id')
                        ORDER BY r.created_at, r.run_id) AS n
                FROM pm_runs r JOIN pm_run_state s USING(run_id)
                WHERE r.workspace_id=? AND (json_extract(s.session_binding,'$.host'),
                    json_extract(s.session_binding,'$.session_id')) IN ({marks})
            ) WHERE n<=?""", (*args, _RUN_SCAN + 1)).fetchall()  # noqa: S608
    grouped = {}
    for row in rows:
        grouped.setdefault((row["host"], row["session_id"]), []).append(row)
    from . import pm_runs
    found, ambiguous, truncated = {}, False, False
    for host, sid, ref in pairs:
        matches = sorted(grouped.get((host, sid), []), key=lambda row: (row["created_at"], row["run_id"]))
        visible_ids, decided = [], False
        for match in matches[:_RUN_SCAN]:
            try:
                body = json.loads(match["context"]) if match["context"] else {}
            except (TypeError, ValueError):
                body = {}
            if not isinstance(body, dict):
                body = {}
            try:
                allowed = pm_runs.source_visible(db, principal, {
                    "operation_id": match["operation_id"], "context": body, "workspace_id": workspace_id})
            except OperationError:
                allowed = False
            if allowed:
                visible_ids.append(match["run_id"])
                if len(visible_ids) == 2:
                    ambiguous = True
                    decided = True
                    break
            else:
                coverage["visibility_filtered"] = True
        if decided:
            continue
        if len(matches) > _RUN_SCAN:
            truncated = True
            continue
        if len(visible_ids) == 1:
            found[ref] = visible_ids[0]
    if ambiguous:
        _note(notes, "attempt.run_id", "multiple_explicit_runs")
    if truncated:
        _note(notes, "attempt.run_id", "run_lookup_truncated")
    return found


def _merges(db, ids):
    if not ids:
        return {}
    rows = db.execute(
        f"""SELECT operation_id, finished_at, response FROM operation_steps WHERE status='succeeded'
            AND (name='merge.verify' OR name LIKE 'merge.verify.retry.%') AND operation_id IN ({_marks(ids)})
            ORDER BY finished_at""", tuple(ids)).fetchall()  # noqa: S608
    found = {}
    for row in rows:
        receipt = pm_evidence._body(row["response"])
        if receipt.get("verified") is not True or not _SHA.fullmatch(str(receipt.get("merged_sha", ""))):
            continue
        stamp = _instant(row["finished_at"])
        if stamp and row["operation_id"] not in found:
            found[row["operation_id"]] = stamp
    return found


def _deployments(db, ids):
    if not ids or not _table(db, "deployments"):
        return set()
    verified = set()
    for row in db.execute(
            f"SELECT operation_id, document FROM deployments WHERE operation_id IN ({_marks(ids)})", tuple(ids)):  # noqa: S608
        try:
            document = json.loads(row["document"])
        except (TypeError, ValueError):
            continue
        if isinstance(document, dict) and document.get("verified") is True:
            verified.add(row["operation_id"])
    return verified


def _attempt(work_id, link, evidence, notes):
    if link["kind"] == "session":
        return _session_attempt(work_id, link["ref"], evidence, notes)
    if link["kind"] == "operation":
        return _operation_attempt(work_id, link["ref"], evidence, notes)
    if link["kind"] == "task":
        return _task_attempt(work_id, link["ref"], evidence, notes)
    return None


def _session_attempt(work_id, ref, evidence, notes):
    host, _, sid = ref.partition("/")
    row = evidence["sessions"].get(ref)
    if row is None:
        if ref not in evidence["unobserved_sessions"]:
            return None
        # No observation means no provenance, so the attempt cannot be managed.
        _note(notes, "attempt.session", "session_not_observed")
        refs = {"session": {"host": host, "session_id": sid, "read_only": True}}
        return _node(f"attempt:session:{work_id}:{ref}", f"work:{work_id}", "attempt", sid, "unknown",
                     None, None, None, [], refs)
    body = pm_evidence._body(row["body"])
    runtime = body.get("runtime_status") if isinstance(body.get("runtime_status"), str) else None
    status = "in_progress" if body.get("streaming") is True or runtime in _RUNNING else "unknown"
    read_only = not (row["api_access"] == "managed" and row["provenance"] != "manual")
    refs = {"session": {"host": host, "session_id": sid, "read_only": read_only}}
    if ref in evidence["runs"]:
        refs["run_id"] = evidence["runs"][ref]
    model = _label(body.get("model"), 100)
    if model:
        refs["agent"] = {"model": model}
    if runtime and _label(runtime, 40):
        refs["recorded_state"] = runtime
    # last_seen_at, first_seen_at and gone_at are inventory facts, not an execution interval.
    title = _label(body.get("title"), 200) or sid
    return _node(f"attempt:session:{work_id}:{ref}", f"work:{work_id}", "attempt", title, status, None, None, None, [], refs)


def _operation_attempt(work_id, operation_id, evidence, notes):
    op = evidence["operations"].get(operation_id)
    if op is None:
        return None
    start = _instant(op.get("created_at"))
    status_name = op.get("status")
    terminal = status_name in OPERATION_TERMINAL
    end = evidence["ends"].get(operation_id) if terminal else None
    actual = None
    if start or end:
        actual = {"start": start, "end": end, "open": not terminal}
    elif terminal:
        actual = None
    if terminal and end is None:
        _note(notes, "actual.end", "terminal_time_unrecorded")
        if start:
            actual = {"start": start, "end": None, "open": False}
    status, waiting, recorded = _operation_status(status_name, evidence["operation_since"].get(operation_id))
    refs = {"operation_id": operation_id}
    if recorded:
        refs["recorded_state"] = recorded
    if op.get("actor") in {"codex", "claude"}:
        refs["agent"] = {"cli": op["actor"]}
    if waiting and waiting.get("since") is None:
        _note(notes, "waiting.since", "state_time_unrecorded")
    return _node(f"attempt:operation:{work_id}:{operation_id}", f"work:{work_id}", "attempt",
                 str(op.get("action") or operation_id), status, None, None, actual, [], refs, waiting=waiting)


def _operation_status(status_name, since):
    if status_name in _OPERATION_WAIT:
        return "waiting", {"reason": _OPERATION_WAIT[status_name], "since": since}, None
    if status_name in {"accepted", "running"}:
        return "in_progress", None, None
    if status_name == "succeeded":
        return "done", None, None
    if status_name in {"failed", "cancelled", "uncertain", "needs_attention"}:
        return "unknown", None, status_name
    return "unknown", None, status_name if isinstance(status_name, str) else None


def _task_attempt(work_id, task_id, evidence, notes):
    loaded = evidence["tasks"].get(task_id)
    if loaded is None:
        return None
    row, state = loaded["row"], loaded["row"]["state"]
    start = _instant(row["submitted_at"])
    terminal = state in {"done", "failed"}
    end = None
    if state == "done":
        end = _instant(row["delivered_at"]) or _instant(loaded["terminal_at"])
    elif state == "failed":
        end = _instant(loaded["terminal_at"])
    if terminal and end is None:
        _note(notes, "actual.end", "terminal_time_unrecorded")
    actual = None
    if start or end or (terminal and start):
        actual = {"start": start, "end": end, "open": False if terminal else end is None}
    status, waiting, recorded = _task_status(row, loaded.get("since"))
    refs = {"task_id": task_id}
    if row["lead_agent"] in {"codex", "claude"}:
        refs["agent"] = {"cli": row["lead_agent"]}
    if recorded:
        refs["recorded_state"] = recorded
    session_ref = f"{row['host']}/{row['session_id']}" if row["host"] and row["session_id"] else None
    observed = evidence["sessions"].get(session_ref) if session_ref else None
    if observed is not None:
        body_row = observed
        read_only = not (body_row["api_access"] == "managed" and body_row["provenance"] != "manual")
        refs["session"] = {"host": row["host"], "session_id": row["session_id"], "read_only": read_only}
    if waiting and waiting.get("since") is None:
        _note(notes, "waiting.since", "state_time_unrecorded")
    return _node(f"attempt:task:{work_id}:{task_id}", f"work:{work_id}", "attempt", task_id, status, None, None,
                 actual, [], refs, waiting=waiting)


def _task_status(row, since):
    state = row["state"]
    if state == "verifying":
        return "in_review", None, None
    if state in _TASK_WAIT:
        return "waiting", {"reason": _TASK_WAIT[state], "since": since}, None
    if state in {"queued", "dispatching", "accepted", "running"}:
        return "in_progress", None, None
    if state == "done":
        return "done", None, None
    if state in {"failed", "uncertain"}:
        return "unknown", None, state
    return "unknown", None, state


def _work_parent(binding, milestones, requirements, notes):
    in_scope = [requirements[(binding["plan_revision_id"], rid)] for rid in binding["requirement_ids"]
                if (binding["plan_revision_id"], rid) in requirements]
    if len(in_scope) == 1:
        return in_scope[0]["id"]
    listed = [milestone for milestone in milestones if binding["binding_id"] in milestone.get("_work_binding_ids", [])]
    if len(listed) == 1:
        return listed[0]["id"]
    parents = {row["parent_id"] for row in in_scope}
    if len(in_scope) > 1 and len(parents) == 1 and None not in parents:
        return next(iter(parents))
    if len(in_scope) > 1 or len(listed) > 1:
        _note(notes, "work.parent", "multiple_explicit_parents")
    return None


def _work_node(db, binding, item, parent, attempts, truncated, evidence, notes):
    completion = work_items.completion(item)
    merged = []
    for row in attempts:
        operation_id = row["refs"].get("operation_id")
        op = evidence["operations"].get(operation_id)
        if operation_id in evidence["merges"] and op and op.get("action") in _MERGE_ACTIONS and op.get("status") == "succeeded":
            merged.append(evidence["merges"][operation_id])
    deployed = any(row["refs"].get("operation_id") in evidence["deployments"] for row in attempts)
    open_attempt = any((row.get("actual") or {}).get("open") or row["status"] in {"in_progress", "in_review"} for row in attempts)
    verifying = any(row["status"] == "in_review" for row in attempts)
    if item["archived"]:
        status = "unknown"
    elif item["state"] == "waiting":
        status = "waiting"
    elif completion["approved"]:
        status = "accepted"
    elif deployed:
        status = "deployed"
    elif merged:
        status = "merged"
    elif completion["pending"] or verifying:
        status = "in_review"
    elif item["state"] == "doing" or open_attempt:
        status = "in_progress"
    elif item["state"] == "todo" and not attempts and not truncated:
        status = "not_started"
    else:
        status = "unknown"
        finished = any((row.get("actual") or {}).get("end") and not (row.get("actual") or {}).get("open") for row in attempts)
        if finished and item["state"] == "todo":
            _note(notes, "work.status", "attempt_finished_is_not_work_completion")
    waiting = None
    if status == "waiting":
        since = _state_since(db, item["work_item_id"], item["state"])
        waiting = {"reason": "work_state", "since": since}
        if since is None:
            _note(notes, "waiting.since", "state_time_unrecorded")
    actual = None if truncated else _work_actual(completion, merged, attempts)
    if actual and actual.get("start") and actual.get("end") and actual["end"] < actual["start"]:
        actual = {"start": actual["start"], "end": None, "open": True}
        _note(notes, "work.actual", "inconsistent_dates")
    if truncated and status == "not_started":
        status = "unknown"
    refs = {"work_item_id": item["work_item_id"], "binding_id": binding["binding_id"],
            "requirement_ids": list(binding["requirement_ids"])}
    if binding.get("plan_revision_id"):
        refs["plan_revision_id"] = binding["plan_revision_id"]
    return _node(f"work:{item['work_item_id']}", parent, "work", item["title"], status, None, None, actual, [], refs,
                 waiting=waiting)


def _state_since(db, work_id, state):
    """Latest work_item.state event, and only when its `to` is the current state."""
    row = db.execute("""SELECT body, created_at FROM api_events WHERE resource_type='work_item' AND resource_id=?
        AND kind='work_item.state' ORDER BY seq DESC LIMIT 1""", (work_id,)).fetchone()
    if row is None:
        return None
    try:
        body = json.loads(row["body"])
    except (TypeError, ValueError):
        return None
    if not isinstance(body, dict) or body.get("to") != state:
        return None
    return _instant(row["created_at"])


def _work_actual(completion, merged, attempts):
    starts = [row["actual"]["start"] for row in attempts if row.get("actual") and row["actual"].get("start")]
    ends = list(merged)
    if completion.get("approved"):
        approved = _instant(completion.get("approved_at"))
        if approved:
            ends.append(approved)
    start = min(starts) if starts else None
    end = min(ends) if ends else None
    if start is None and end is None:
        return None
    return {"start": start, "end": end, "open": end is None}


def _issues(ops, principal, workspace_id, milestones, notes, coverage):
    """Page mappings until 200 rows will be emitted, or the scan bound stops on more data."""
    if not _table(ops.db, "pm_issue_revisions"):
        return []
    from . import pm_issue_mappings
    emitted, after, scanned, truncated = [], 0, 0, False
    while scanned < _ISSUE_SCAN and len(emitted) < MAX_ISSUES:
        limit = min(50, _ISSUE_SCAN - scanned)
        page = pm_issue_mappings.list_mappings(ops, principal, workspace_id, after=after, limit=limit)
        if page["coverage"]["visibility_filtered"]:
            coverage["visibility_filtered"] = True
        scanned += page["coverage"].get("scanned", len(page["mappings"]))
        binding_ids, mapping_ids = [], []
        for mapping in page["mappings"]:
            document = mapping.get("document") if isinstance(mapping.get("document"), dict) else {}
            relation = document.get("relation") if isinstance(document.get("relation"), dict) else {}
            if isinstance(relation.get("binding_id"), str):
                binding_ids.append(relation["binding_id"])
            if isinstance(mapping.get("mapping_id"), str):
                mapping_ids.append(mapping["mapping_id"])
        plans = _binding_plans(ops.db, workspace_id, binding_ids)
        published = _published_titles(ops.db, workspace_id, mapping_ids)
        for mapping in page["mappings"]:
            row = _issue(ops, principal, mapping, milestones, notes, coverage, plans, published)
            if row is None:
                continue
            if len(emitted) >= MAX_ISSUES:
                truncated = True
                break
            emitted.append(row)
        if truncated:
            break
        more = page["coverage"]["has_more"] and page["next_cursor"] is not None and page["next_cursor"] > after
        if not more:
            break
        if scanned >= _ISSUE_SCAN:
            truncated = True
            break
        after = page["next_cursor"]
    if truncated:
        coverage["issues_truncated"] = True
        _note(notes, "issue", "mapping_page_truncated")
    return emitted


def _binding_plans(db, workspace_id, binding_ids):
    ids = list(dict.fromkeys(binding_ids))
    if not ids or not _table(db, "pm_work_bindings"):
        return {}
    rows = db.execute(
        f"""SELECT binding_id, plan_revision_id FROM pm_work_bindings WHERE workspace_id=?
            AND binding_id IN ({_marks(ids)})""", (workspace_id, *ids)).fetchall()  # noqa: S608
    return {row["binding_id"]: row["plan_revision_id"] for row in rows}


def _published_titles(db, workspace_id, mapping_ids):
    ids = list(dict.fromkeys(mapping_ids))
    if not ids:
        return {}
    rows = db.execute(
        f"""SELECT mapping_id, document FROM pm_issue_revisions WHERE workspace_id=?
            AND mapping_id IN ({_marks(ids)}) ORDER BY sequence""", (workspace_id, *ids)).fetchall()  # noqa: S608
    found = {}
    for row in rows:
        try:
            document = json.loads(row["document"])
        except (TypeError, ValueError):
            continue
        selected = document.get("selected") if isinstance(document.get("selected"), dict) else {}
        title = _plain(selected.get("public_title"))
        if title:
            found[row["mapping_id"]] = title
    return found


def _issue(ops, principal, mapping, milestones, notes, coverage, plans, published):
    document = mapping["document"]
    relation = document.get("relation") if isinstance(document.get("relation"), dict) else {}
    issue = document.get("issue") if isinstance(document.get("issue"), dict) else {}
    selected = document.get("selected") if isinstance(document.get("selected"), dict) else {}
    work_id = relation.get("work_item_id")
    if isinstance(work_id, str) and not _work_visible(ops.db, principal, work_id, coverage):
        return None
    title = _plain(selected.get("public_title")) or _plain(published.get(mapping["mapping_id"]))
    number, repository = issue.get("number"), issue.get("repository") if isinstance(issue.get("repository"), str) else relation.get("repository")
    if title is None and isinstance(repository, str) and repository.strip() and type(number) is int and number > 0:
        title = f"{repository.strip()}#{number}"
    if title is None:
        _note(notes, "issue.title", "issue_title_unavailable")
        return None
    requirement_ids = [rid for rid in relation.get("requirement_ids") or [] if isinstance(rid, str)]
    revision = plans.get(relation.get("binding_id")) if isinstance(relation.get("binding_id"), str) else None
    in_baseline = [rid for rid in requirement_ids if any(
        milestone["refs"].get("plan_revision_id") == revision and rid in (milestone["refs"].get("requirement_ids") or [])
        for milestone in milestones)]
    covered = [milestone for milestone in milestones if revision and in_baseline
               and milestone["refs"].get("plan_revision_id") == revision
               and set(in_baseline) <= set(milestone["refs"].get("requirement_ids") or [])]
    parent = covered[0]["id"] if len(covered) == 1 else None
    refs = {}
    if isinstance(repository, str) and type(number) is int and number > 0:
        refs["issue"] = {"repository": repository, "number": number}
    if isinstance(document.get("state"), str):
        refs["tracking_state"] = document["state"]
    if isinstance(work_id, str):
        refs["work_item_id"] = work_id
    if requirement_ids:
        refs["requirement_ids"] = requirement_ids
    return _node(f"issue:{mapping['mapping_id']}", parent, "issue", title, "unknown", None, None, None, [], refs)


def _node(row_id, parent, kind, title, status, baseline, forecast, actual, depends_on, refs, *, date_kind=None, waiting=None):
    node = {"id": row_id, "parent_id": parent, "kind": kind, "title": title, "status": status, "baseline": baseline,
            "forecast": forecast, "actual": actual, "blocked": None, "waiting": waiting,
            "refs": {key: value for key, value in refs.items() if value is not None}, "depends_on": list(depends_on)}
    if date_kind:
        node["date_kind"] = date_kind
    return node


def _dumps(payload):
    return json.dumps(payload, ensure_ascii=False).encode()


def _lineage_open(db):
    def load():
        if not pm_source_lineage.installed(db):
            return False
        return db.execute("SELECT 1 FROM pm_source_lineage LIMIT 1").fetchone() is not None

    return _cached("lineage_open", load)


def _row_roots(db, public):
    found = set()
    for kind, identity in pm_source_lineage.references(public):
        found.update(pm_source_lineage.roots(db, kind, identity))
    return found


def _fit(loaded, ordered, limit):
    """Keep a sort-order prefix that fits the byte cap and the 64-root lineage budget."""
    if not ordered:
        return _body(loaded, [], False)
    longest = max((_encode_cursor(node["id"]) for node in ordered), key=len)
    envelope = len(_dumps(_frame(loaded, [], [], partial=True, cursor=longest, range_value=_RANGE_BOUND)))
    lineage = _lineage_open(loaded["db"])
    kept, counts, used, union = [], [0, 0], 0, set()
    index = 0
    while index < len(ordered) and len(kept) < limit:
        node = ordered[index]
        public = _public(node)
        raw = _dumps(public)
        slot = 0 if _scheduled(node) else 1
        extra = len(raw) + (2 if counts[slot] else 0)
        row_roots = set()
        if lineage:
            try:
                row_roots = _row_roots(loaded["db"], public)
            except OperationError:
                loaded["coverage"]["visibility_filtered"] = True
                index += 1
                continue
            if len(row_roots) > pm_source_lineage.MAX_ROOTS:
                loaded["coverage"]["visibility_filtered"] = True
                index += 1
                continue
            if len(union | row_roots) > pm_source_lineage.MAX_ROOTS:
                break
        if envelope + used + extra > MAX_BYTES:
            if not kept:
                raise OperationError("PM_SCHEDULE_TOO_LARGE", "One schedule row exceeds the read limit", 413)
            break
        union |= row_roots
        kept.append(node)
        counts[slot] += 1
        used += extra
        index += 1
    return _body(loaded, kept, index < len(ordered))


def _body(loaded, selected, more):
    rows, unscheduled = [], []
    for node in selected:
        public = _public(node)
        (rows if _scheduled(node) else unscheduled).append(public)
    source_partial = any(loaded["coverage"][key] for key in _SOURCE_FLAGS)
    partial = bool(more or source_partial)
    cursor = _encode_cursor(selected[-1]["id"]) if more and selected else None
    return _frame(loaded, rows, unscheduled, partial=partial, cursor=cursor, range_value=_range(rows))


def _frame(loaded, rows, unscheduled, *, partial, cursor, range_value):
    return {"version": 1, "workspace_id": loaded["workspace_id"], "as_of": _instant(datetime.now(timezone.utc).timestamp()),
            "baseline": _head(loaded["baseline"], by=True), "forecast": _head(loaded["forecast"], by=False),
            "range": range_value, "range_scope": "returned_page" if partial else "complete",
            "rows": rows, "unscheduled": unscheduled,
            "unknown": [{"field": field, "reason": reason} for field, reason in sorted(loaded["notes"])],
            "truncated": partial, "partial": partial, "next_cursor": cursor,
            "capabilities": capabilities(), "coverage": dict(loaded["coverage"])}


def _public(node):
    refs = dict(node["refs"])
    if _scheduled(node):
        body = {"id": node["id"], "parent_id": node["parent_id"], "kind": node["kind"], "title": node["title"],
                "status": node["status"], "baseline": node["baseline"], "forecast": node["forecast"],
                "actual": node["actual"], "blocked": None, "waiting": node["waiting"], "refs": refs,
                "depends_on": list(node["depends_on"])}
        if node.get("date_kind"):
            body["date_kind"] = node["date_kind"]
        return body
    return {"id": node["id"], "parent_id": node["parent_id"], "kind": node["kind"], "title": node["title"],
            "status": node["status"], "blocked": None, "waiting": node["waiting"], "refs": refs,
            "depends_on": list(node["depends_on"])}


def _scheduled(node):
    return any(node.get(key) for key in ("baseline", "forecast", "actual"))


def _head(revision, *, by):
    if revision is None:
        return None
    head = {"revision_id": revision["revision_id"], "set_at": _instant(revision["created_at"])}
    if by:
        head["set_by"] = revision["created_by"]
    return head


def _range(rows):
    found = []
    for row in rows:
        for key in ("baseline", "forecast"):
            span = row.get(key)
            if span:
                found.extend([span["start"], span["end"]])
        actual = row.get("actual") or {}
        for key in ("start", "end"):
            if actual.get(key):
                found.append(actual[key][:10])
    found = [value for value in found if _day(value)]
    return {"start": min(found), "end": max(found)} if found else None
