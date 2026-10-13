"""Read-only project schedule for the Gantt view.

Dates come only from the current roadmap baseline and forecast revisions and from
explicit execution evidence. This module does not compute a forecast, a critical
path, phase rows, or token attribution, and it does not write.
"""
from __future__ import annotations

import json
import math
import re
from datetime import datetime, timezone

from . import pm_evidence, pm_roadmap, work_items
from .operations import TERMINAL as OPERATION_TERMINAL
from .operations import OperationError

MAX_WORK = 200
MAX_LINKS_PER_WORK = 50
MAX_LINKS_TOTAL = 200
MAX_ISSUES = 200
MAX_RUNS = 200
MAX_BYTES = 2_000_000
_PAGE_DEFAULT = 100
_RANK = {"milestone": 0, "requirement": 1, "issue": 2, "work": 3, "attempt": 4}
_CURSOR = re.compile(r"^(milestone|requirement|issue|work|attempt):[A-Za-z0-9_.:/+-]{1,360}$")
_DATE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")
_SHA = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
_MERGE_ACTIONS = frozenset({"github.pr.merge", "delivery.merge_and_deploy"})
_RUNNING = frozenset({"running", "streaming", "starting", "active"})
_TASK_WAIT = {"waiting_permission": "permission", "quota_limited": "quota", "human_owned": "human", "needs_ted": "human"}
_OPERATION_WAIT = {"waiting_checks": "checks", "waiting_external": "external"}
_SOURCE_FLAGS = ("attempts_truncated", "work_bindings_truncated", "issues_truncated")


def capabilities():
    return {"version": 1, "read_only": True, "forecast": "recorded_revision_only", "critical_path": False,
            "token_attribution": False, "phases": False, "drag_proposal": False}


def read(ops, principal, workspace_id, *, after=None, limit=None):
    """Project one workspace schedule. Membership is checked on every call."""
    limit = _PAGE_DEFAULT if limit is None else limit
    if type(limit) is not int or not 1 <= limit <= _PAGE_DEFAULT:
        raise OperationError("INVALID_PARAMS", "limit must be 1-100", 422)
    cursor = _cursor(after)
    with ops.journal.tx():
        pm_roadmap._member(ops, principal, workspace_id)
        loaded = _load(ops, principal, workspace_id)
        page, more = _slice(loaded["nodes"], cursor, limit)
        return _project(loaded, page, more)


def _cursor(after):
    if after is None:
        return None
    if not isinstance(after, str) or not _CURSOR.fullmatch(after):
        raise OperationError("INVALID_PARAMS", "after must be a schedule row id", 422)
    kind, _rest = after.split(":", 1)
    return _RANK[kind], after


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
                "work_bindings_truncated": False, "issues_truncated": False, "network_checked": False,
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
    return {"workspace_id": workspace_id, "nodes": [*milestones, *requirements.values(), *issues, *works, *attempts],
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
        source = None if plan is None else next((item for item in plan["document"].get("requirements") or []
                                                  if item.get("id") == rid), None)
        title = _label((source or {}).get("text"), 4000)
        if source is None or title is None:
            if plan is not None:
                _note(notes, "requirement", "requirement_text_unavailable")
            continue
        parent_ids = list(dict.fromkeys(parents))
        parent = parent_ids[0] if len(parent_ids) == 1 else None
        if parent is None:
            _note(notes, "requirement.parent", "multiple_explicit_milestones")
        deps = []
        for dep in source.get("depends_on") or []:
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
    if outside:
        _note(notes, "requirement.depends_on", "requirement_dependency_outside_baseline")
    return rows


def _plan(ops, principal, workspace_id, revision, coverage):
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


def _works(ops, principal, workspace_id, milestones, requirements, notes, coverage):
    db = ops.db
    bindings = db.execute("""SELECT b.* FROM pm_work_bindings b WHERE workspace_id=? AND NOT EXISTS(
        SELECT 1 FROM pm_work_bindings n WHERE n.workspace_id=b.workspace_id AND n.work_item_id=b.work_item_id
        AND n.sequence>b.sequence) ORDER BY sequence LIMIT ?""", (workspace_id, MAX_WORK + 1)).fetchall()
    if len(bindings) > MAX_WORK:
        coverage["work_bindings_truncated"] = True
        _note(notes, "work", "binding_page_truncated")
        bindings = bindings[:MAX_WORK]
    selected = []
    for row in bindings:
        binding = {**dict(row), "requirement_ids": json.loads(row["requirement_ids"])}
        if _in_scope(binding, milestones):
            selected.append(binding)
    items = _work_items(db, [row["work_item_id"] for row in selected])
    links, truncated_works = _links(db, [row["work_item_id"] for row in selected], coverage, notes)
    evidence = _evidence(ops, principal, workspace_id, links, notes, coverage)
    works, attempts = [], []
    for binding in selected:
        item = items.get(binding["work_item_id"])
        if item is None:
            _note(notes, "work", "work_item_unavailable")
            continue
        own = links.get(binding["work_item_id"], [])
        attempt_rows = [row for row in (_attempt(binding["work_item_id"], link, evidence, notes) for link in own) if row]
        attempts.extend(attempt_rows)
        parent = _work_parent(binding, milestones, requirements, notes)
        works.append(_work_node(db, binding, item, parent, attempt_rows, binding["work_item_id"] in truncated_works,
                                evidence, notes))
    return works, attempts


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


def _links(db, ids, coverage, notes):
    if not ids:
        return {}, set()
    counts = dict(db.execute(
        f"""SELECT work_item_id, COUNT(*) FROM work_item_links WHERE removed_at IS NULL
            AND kind IN ('session','operation','task') AND work_item_id IN ({_marks(ids)}) GROUP BY work_item_id""",  # noqa: S608
        tuple(ids)).fetchall())
    fetched = db.execute(
        f"""SELECT work_item_id, link_id, kind, ref FROM work_item_links WHERE removed_at IS NULL
            AND kind IN ('session','operation','task') AND work_item_id IN ({_marks(ids)})
            ORDER BY link_id LIMIT ?""",  # noqa: S608
        (*ids, MAX_LINKS_TOTAL + 1)).fetchall()
    overflow = len(fetched) > MAX_LINKS_TOTAL
    grouped = {}
    for row in fetched[:MAX_LINKS_TOTAL]:
        grouped.setdefault(row["work_item_id"], []).append(row)
    truncated = set()
    cut_id = fetched[MAX_LINKS_TOTAL - 1]["work_item_id"] if overflow else None
    for work_id, count in counts.items():
        have = grouped.get(work_id, [])
        if count > len(have) or len(have) > MAX_LINKS_PER_WORK or work_id == cut_id:
            truncated.add(work_id)
            grouped[work_id] = have[:MAX_LINKS_PER_WORK]
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
    loaded_sessions = _sessions(ops, principal, workspace_id, sessions, coverage)
    loaded_operations, ends = _operations(ops, principal, workspace_id, operations, coverage)
    loaded_tasks = _tasks(ops, principal, workspace_id, tasks, coverage)
    return {"db": ops.db, "sessions": loaded_sessions, "operations": loaded_operations, "ends": ends,
            "tasks": loaded_tasks, "runs": _runs(ops, principal, workspace_id, list(loaded_sessions), notes, coverage),
            "merges": _merges(ops.db, list(loaded_operations)),
            "deployments": _deployments(ops.db, list(loaded_operations))}


def _sessions(ops, principal, workspace_id, refs, coverage):
    pairs = []
    for ref in dict.fromkeys(refs):
        host, sep, sid = ref.partition("/")
        if sep and host and sid:
            pairs.append((host, sid, ref))
    if not pairs:
        return {}
    rows = ops.db.execute(
        f"SELECT * FROM sessions_observed WHERE (host, session_id) IN ({','.join('(?,?)' for _ in pairs)})",  # noqa: S608
        tuple(part for host, sid, _ref in pairs for part in (host, sid))).fetchall()
    by_ref = {f"{row['host']}/{row['session_id']}": row for row in rows}
    found = {}
    for _host, _sid, ref in pairs:
        row = by_ref.get(ref)
        if row is None:
            continue
        if not pm_evidence._session_visible(ops, principal, workspace_id, ref):
            coverage["visibility_filtered"] = True
            continue
        found[ref] = row
    return found


def _operations(ops, principal, workspace_id, ids, coverage):
    ids = list(dict.fromkeys(ids))
    found, terminal = {}, []
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
            continue
        found[operation_id] = op
        if op.get("status") in OPERATION_TERMINAL:
            terminal.append(operation_id)
    ends = {}
    if terminal:
        for row in ops.db.execute(
                f"""SELECT operation_id, MAX(finished_at) AS finished_at FROM operation_steps
                    WHERE status='succeeded' AND finished_at IS NOT NULL AND operation_id IN ({_marks(terminal)})
                    GROUP BY operation_id""", tuple(terminal)):  # noqa: S608
            ends[row["operation_id"]] = _instant(row["finished_at"])
    return found, ends


def _tasks(ops, principal, workspace_id, ids, coverage):
    ids = list(dict.fromkeys(ids))
    if not ids:
        return {}
    rows = ops.db.execute(
        f"""SELECT task_id, state, submitted_at, delivered_at, lead_agent, host, session_id, verifying_started_at
            FROM tasks WHERE task_id IN ({_marks(ids)})""", tuple(ids)).fetchall()  # noqa: S608
    present = {row["task_id"]: row for row in rows}
    found = {}
    for task_id in ids:
        if task_id not in present:
            continue
        if not pm_evidence._task_visible(ops, principal, workspace_id, task_id):
            coverage["visibility_filtered"] = True
            continue
        found[task_id] = present[task_id]
    ends = {}
    if found:
        for row in ops.db.execute(
                f"""SELECT task_id, MAX(created_at) AS created_at FROM events WHERE kind='state'
                    AND json_extract(body,'$.to') IN ('done','failed') AND task_id IN ({_marks(list(found))})
                    GROUP BY task_id""", tuple(found)):  # noqa: S608
            ends[row["task_id"]] = row["created_at"]
    return {task_id: {"row": row, "terminal_at": ends.get(task_id)} for task_id, row in found.items()}


def _runs(ops, principal, workspace_id, refs, notes, coverage):
    db = ops.db
    if not refs or not _table(db, "pm_runs") or not _table(db, "pm_run_state"):
        return {}
    rows = db.execute("""SELECT r.run_id, r.operation_id, json_extract(s.session_binding,'$.host') AS host,
        json_extract(s.session_binding,'$.session_id') AS session_id FROM pm_runs r JOIN pm_run_state s USING(run_id)
        WHERE r.workspace_id=? LIMIT ?""", (workspace_id, MAX_RUNS + 1)).fetchall()
    if len(rows) > MAX_RUNS:
        _note(notes, "attempt.run_id", "run_index_truncated")
        return {}
    grouped = {}
    for row in rows:
        grouped.setdefault((row["host"], row["session_id"]), []).append(row)
    from . import pm_runs
    found, ambiguous = {}, False
    for ref in refs:
        host, _, sid = ref.partition("/")
        matches = grouped.get((host, sid), [])
        if len(matches) > 1:
            ambiguous = True
            continue
        if len(matches) != 1:
            continue
        match = matches[0]
        raw = db.execute("SELECT context FROM pm_runs WHERE run_id=?", (match["run_id"],)).fetchone()
        try:
            body = json.loads(raw[0]) if raw else {}
        except (TypeError, ValueError):
            body = {}
        if not isinstance(body, dict):
            body = {}
        if not pm_runs.source_visible(db, principal, {"operation_id": match["operation_id"], "context": body}):
            coverage["visibility_filtered"] = True
            continue
        found[ref] = match["run_id"]
    if ambiguous:
        _note(notes, "attempt.run_id", "multiple_explicit_runs")
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
        return _session_attempt(work_id, link["ref"], evidence)
    if link["kind"] == "operation":
        return _operation_attempt(work_id, link["ref"], evidence, notes)
    if link["kind"] == "task":
        return _task_attempt(work_id, link["ref"], evidence, notes)
    return None


def _session_attempt(work_id, ref, evidence):
    row = evidence["sessions"].get(ref)
    if row is None:
        return None
    body = pm_evidence._body(row["body"])
    host, _, sid = ref.partition("/")
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
    status, waiting, recorded = _operation_status(status_name)
    refs = {"operation_id": operation_id}
    if recorded:
        refs["recorded_state"] = recorded
    if op.get("actor") in {"codex", "claude"}:
        refs["agent"] = {"cli": op["actor"]}
    if waiting and waiting.get("since") is None:
        _note(notes, "waiting.since", "state_time_unrecorded")
    return _node(f"attempt:operation:{work_id}:{operation_id}", f"work:{work_id}", "attempt",
                 str(op.get("action") or operation_id), status, None, None, actual, [], refs, waiting=waiting)


def _operation_status(status_name):
    if status_name in _OPERATION_WAIT:
        return "waiting", {"reason": _OPERATION_WAIT[status_name], "since": None}, None
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
    status, waiting, recorded = _task_status(row)
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


def _task_status(row):
    state = row["state"]
    if state == "verifying":
        return "in_review", {"reason": "review", "since": _instant(row["verifying_started_at"])}, None
    if state in _TASK_WAIT:
        return "waiting", {"reason": _TASK_WAIT[state], "since": None}, None
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
    elif item["state"] == "todo" and not attempts:
        status = "not_started"
    else:
        status = "unknown"
        if attempts and item["state"] == "todo":
            _note(notes, "work.status", "attempt_finished_is_not_work_completion")
    waiting = None
    if status == "waiting":
        since = _state_since(db, item["work_item_id"], item["state"])
        waiting = {"reason": "work_state", "since": since}
        if since is None:
            _note(notes, "waiting.since", "state_time_unrecorded")
    actual = None if truncated else _work_actual(completion, merged, attempts)
    if truncated and status == "not_started":
        status = "unknown"
    refs = {"work_item_id": item["work_item_id"], "binding_id": binding["binding_id"],
            "requirement_ids": list(binding["requirement_ids"])}
    if binding.get("plan_revision_id"):
        refs["plan_revision_id"] = binding["plan_revision_id"]
    return _node(f"work:{item['work_item_id']}", parent, "work", item["title"], status, None, None, actual, [], refs,
                 waiting=waiting)


def _state_since(db, work_id, state):
    row = db.execute("""SELECT created_at FROM api_events WHERE resource_type='work_item' AND resource_id=?
        AND kind='work_item.state' AND json_extract(body,'$.to')=? ORDER BY seq DESC LIMIT 1""", (work_id, state)).fetchone()
    return _instant(row[0]) if row else None


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
    if not _table(ops.db, "pm_issue_revisions"):
        return []
    from . import pm_issue_mappings
    collected, after, pages = [], 0, 0
    while pages < 4 and len(collected) < MAX_ISSUES:
        page = pm_issue_mappings.list_mappings(ops, principal, workspace_id, after=after, limit=50)
        if page["coverage"]["visibility_filtered"]:
            coverage["visibility_filtered"] = True
        collected.extend(page["mappings"])
        pages += 1
        if not page["coverage"]["has_more"] or page["next_cursor"] is None:
            break
        after = page["next_cursor"]
    else:
        coverage["issues_truncated"] = True
        _note(notes, "issue", "mapping_page_truncated")
    if len(collected) > MAX_ISSUES:
        collected = collected[:MAX_ISSUES]
        coverage["issues_truncated"] = True
        _note(notes, "issue", "mapping_page_truncated")
    return [row for row in (_issue(mapping, milestones, notes) for mapping in collected) if row]


def _issue(mapping, milestones, notes):
    document = mapping["document"]
    relation = document.get("relation") if isinstance(document.get("relation"), dict) else {}
    issue = document.get("issue") if isinstance(document.get("issue"), dict) else {}
    selected = document.get("selected") if isinstance(document.get("selected"), dict) else {}
    title = _label(issue.get("title"), 200) or _label(selected.get("public_title"), 200)
    if title is None:
        _note(notes, "issue.title", "issue_title_unavailable")
        return None
    requirement_ids = [rid for rid in relation.get("requirement_ids") or [] if isinstance(rid, str)]
    in_baseline = [rid for rid in requirement_ids if any(rid in (milestone["refs"].get("requirement_ids") or [])
                                                         for milestone in milestones)]
    covered = [milestone for milestone in milestones if in_baseline and set(in_baseline) <= set(
        milestone["refs"].get("requirement_ids") or [])]
    parent = covered[0]["id"] if len(covered) == 1 else None
    number, repository = issue.get("number"), relation.get("repository")
    refs = {}
    if isinstance(repository, str) and type(number) is int and number > 0:
        refs["issue"] = {"repository": repository, "number": number}
    if isinstance(document.get("state"), str):
        refs["tracking_state"] = document["state"]
    if isinstance(relation.get("work_item_id"), str):
        refs["work_item_id"] = relation["work_item_id"]
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


def _slice(nodes, cursor, limit):
    ordered = sorted(nodes, key=lambda node: (_RANK[node["kind"]], node["id"]))
    if cursor is not None:
        ordered = [node for node in ordered if (_RANK[node["kind"]], node["id"]) > cursor]
    return ordered[:limit], len(ordered) > limit


def _project(loaded, page, more):
    selected = list(page)
    while selected and _too_big(_body(loaded, selected, True)):
        if len(selected) == 1:
            raise OperationError("PM_SCHEDULE_TOO_LARGE", "One schedule row exceeds the read limit", 413)
        selected = selected[:-1]
        more = True
    return _body(loaded, selected, more)


def _too_big(payload):
    return len(json.dumps(payload, ensure_ascii=False).encode()) > MAX_BYTES


def _body(loaded, selected, more):
    rows, unscheduled = [], []
    for node in selected:
        public = _public(node)
        (rows if _scheduled(node) else unscheduled).append(public)
    source_partial = any(loaded["coverage"][key] for key in _SOURCE_FLAGS)
    partial = bool(more or source_partial)
    return {"version": 1, "workspace_id": loaded["workspace_id"], "as_of": _instant(datetime.now(timezone.utc).timestamp()),
            "baseline": _head(loaded["baseline"], by=True), "forecast": _head(loaded["forecast"], by=False),
            "range": _range(rows), "range_scope": "returned_page" if partial else "complete",
            "rows": rows, "unscheduled": unscheduled,
            "unknown": [{"field": field, "reason": reason} for field, reason in sorted(loaded["notes"])],
            "truncated": partial, "partial": partial,
            "next_cursor": selected[-1]["id"] if more and selected else None,
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
