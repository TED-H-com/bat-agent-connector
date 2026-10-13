"""Read-only issues, actions and changes register.

Each request is a best-effort point in time. Source modules open their own
journal transactions, so this is not one snapshot-isolation guarantee.
Hidden records are omitted from rows, order, counts and labels together.
Relations come only from stored foreign keys, never from names or time.
"""
from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime, timedelta, timezone

from .operations import OperationError
from .pm_source_lineage import _table
from .pm_workspace import require_member

TYPES = ("risk", "issue", "action", "change", "decision")
SOURCE_KINDS = ("governance", "finding", "repair", "follow_up", "proposal", "issue_mapping", "meeting", "plan_revision", "requirement", "work_item")
STEPS = ("finding", "action", "work", "evidence", "review", "proposal", "decision", "revision")
STEP_ORDER = {name: index for index, name in enumerate(STEPS)}
OPEN_STATUSES = frozenset({"open", "monitoring", "observed_open", "blocked", "tracking", "needs_review", "proposed", "in_progress", "draft"})
RESOLVED_STATUSES = frozenset({"reported_resolved", "reported_done", "adopted", "rejected", "cancelled", "succeeded"})
AGE_BUCKETS = ("0-7d", "8-30d", "31-90d", "90d+")
GOVERNANCE_TYPES = {"risk": "risk", "issue": "issue", "change": "change"}
NEUTRAL_GOVERNANCE = frozenset({"assumption", "dependency", "drift"})
MAX_SCAN = 200
DETAIL_CAP = 40
PAGE = 50
_ROW_ID = re.compile(r"^(?:governance|finding|repair|follow_up|proposal|issue_mapping|meeting|plan_revision):[A-Za-z0-9_.~-]{1,180}$")
_STATUS = re.compile(r"[a-z0-9_]{1,64}$")
_ID = re.compile(r"[A-Za-z0-9_.~-]{1,180}$")


def capabilities():
    return {"version": 1, "read_only": True, "types": list(TYPES), "source_kinds": list(SOURCE_KINDS),
            "timeline_steps": list(STEPS), "dispatch": False, "writes": False,
            "summary": "complete_visible_scan_only", "time": "iso8601_utc"}


def _bad(message):
    raise OperationError("INVALID_PARAMS", message, 422)


def _missing(exc):
    return isinstance(exc, sqlite3.OperationalError) and "no such table" in str(exc).lower()


def _hidden(exc):
    return isinstance(exc, OperationError) and exc.status in (403, 404)


def _iso(value):
    if isinstance(value, str):
        text = value[:-1] + "+00:00" if value.endswith("Z") else value
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            return None
        if parsed.tzinfo is None:
            return None
    elif isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    else:
        try:
            parsed = datetime.fromtimestamp(value, timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    return parsed.astimezone(timezone.utc).isoformat(timespec="milliseconds")


def _when(value):
    text = _iso(value)
    if text is None:
        return None
    return datetime.fromisoformat(text)


def _check(workspace_id, principal, ops):
    if not principal.allows("observe"):
        raise OperationError("FORBIDDEN", "PM reads need observe", 403)
    require_member(ops.db, principal, workspace_id)


def _params(row_type, status, owner, q, after, limit):
    if row_type is not None and row_type not in TYPES:
        _bad("type must be risk, issue, action, change or decision")
    if status is not None and (not isinstance(status, str) or not _STATUS.fullmatch(status)):
        _bad("status must be a canonical key")
    if owner is not None and (not isinstance(owner, str) or not owner or len(owner) > 200):
        _bad("owner must be an explicit actor")
    if q is not None and (not isinstance(q, str) or not q or len(q) > 200):
        _bad("q must be a title search of at most 200 characters")
    if type(limit) is not int or not 1 <= limit <= 100:
        _bad("limit must be 1-100")
    cursor = None
    if after is not None:
        if not isinstance(after, str) or "~" not in after or len(after) > 400:
            _bad("after must be a register cursor")
        stamp, ident = after.split("~", 1)
        if not _ROW_ID.fullmatch(ident) or (stamp and _iso(stamp) is None):
            _bad("after must be a register cursor")
        cursor = (_iso(stamp) or "", ident)
    return cursor


def _ids(values):
    result = []
    for value in values:
        if isinstance(value, str) and value not in result:
            result.append(value)
    return result


def _base(*, kind, ident, row_type, basis, title, title_reason, status, status_reason, owner, owner_reason,
          due, due_reason, impact, impact_reason, priority, priority_reason, next_step, next_reason,
          created, updated, requirements, work_items, prs, actions, steps, refs, resolved_at, blocking):
    if not _ID.fullmatch(ident):
        return None
    unknowns = {}
    fields = {"title": (title, title_reason), "status": (status, status_reason), "owner": (owner, owner_reason),
              "due": (due, due_reason), "impact": (impact, impact_reason), "priority": (priority, priority_reason),
              "next_step": (next_step, next_reason), "created_at": (created, "not_recorded"),
              "updated_at": (updated, "not_recorded")}
    public = {}
    for name, (value, reason) in fields.items():
        public[name] = value
        if value is None and reason:
            unknowns[name] = reason
    clean_prs = []
    seen = set()
    for item in prs:
        if not isinstance(item, dict):
            continue
        repository, number = item.get("repository"), item.get("number")
        if not isinstance(repository, str) or not isinstance(number, int) or isinstance(number, bool):
            continue
        key = (repository, number)
        if key in seen:
            continue
        seen.add(key)
        clean_prs.append({"repository": repository, "number": number})
    open_row = status in OPEN_STATUSES
    unclassified = isinstance(status, str) and status not in OPEN_STATUSES and status not in RESOLVED_STATUSES
    return {"row_id": kind + ":" + ident, "type": row_type, "type_basis": basis, **public,
            "source": {"kind": kind, "id": ident}, "related": {"requirement_ids": _ids(requirements),
            "work_item_ids": _ids(work_items), "prs": clean_prs}, "available_actions": actions, "unknowns": unknowns,
            "_steps": steps, "_refs": refs, "_open": open_row, "_resolved_at": resolved_at if status in RESOLVED_STATUSES else None,
            "_blocking_reason": blocking, "_unclassified": unclassified, "_unknown_status": status is None}


def _action(ops, name, target, required=None):
    if name not in ops.actions:
        return None
    return {"action": name, "target": target, "required": required or {}}


def _governance_row(ops, workspace_id, view):
    record, revision = view["record"], view["revision"]
    document = revision["document"]
    kind = record["kind"]
    if kind in GOVERNANCE_TYPES:
        row_type, basis = GOVERNANCE_TYPES[kind], "explicit"
    elif kind in NEUTRAL_GOVERNANCE:
        row_type, basis = "issue", "neutral"
    else:
        return None
    created, updated = _iso(record["created_at"]), _iso(revision["created_at"])
    decision, adoption = document.get("decision"), document.get("adoption")
    proposed = document.get("proposed_revision")
    steps, refs = [], set()
    if isinstance(decision, dict):
        steps.append({"step": "decision", "at": updated if revision.get("action") == "pm.governance.decide" else None,
                      "actor": decision.get("recorded_by") if isinstance(decision.get("recorded_by"), str) else None,
                      "title": document.get("title"), "ref": {"kind": "governance", "id": record["record_id"]},
                      "status": decision.get("disposition") if isinstance(decision.get("disposition"), str) else None})
    adopted_id = adoption.get("revision_id") if isinstance(adoption, dict) else None
    proposed_id = proposed.get("revision_id") if isinstance(proposed, dict) else None
    if isinstance(adopted_id, str):
        steps.append({"step": "revision", "at": _iso(adoption.get("adopted_at")), "actor": adoption.get("adopted_by")
                      if isinstance(adoption.get("adopted_by"), str) else None, "title": document.get("title"),
                      "ref": {"kind": "plan_revision", "id": adopted_id}, "status": "adopted"})
        refs.add(("plan_revision", adopted_id))
    elif isinstance(proposed_id, str):
        steps.append({"step": "revision", "at": updated, "actor": revision.get("actor"), "title": document.get("title"),
                      "ref": {"kind": "plan_revision", "id": proposed_id}, "status": document.get("status")})
        refs.add(("plan_revision", proposed_id))
    for source in document.get("sources") or []:
        if isinstance(source, dict) and source.get("kind") == "evidence" and isinstance(source.get("id"), str):
            steps.append({"step": "evidence", "at": updated, "actor": revision.get("actor"), "title": None,
                          "ref": {"kind": "evidence", "id": source["id"]}, "status": None})
    for other in (document.get("disambiguation") or {}).get("record_ids") or []:
        if isinstance(other, str):
            refs.add(("governance", other))
    actions = []
    target = {"workspace_id": workspace_id, "record_id": record["record_id"]}
    for name in ("pm.governance.revise",):
        item = _action(ops, name, target)
        if item:
            actions.append(item)
    if kind == "change" and decision is None:
        item = _action(ops, "pm.governance.decide", target)
        if item:
            actions.append(item)
    if (kind == "change" and isinstance(decision, dict) and decision.get("disposition") == "adopted_direction"
            and isinstance(proposed_id, str) and not adoption):
        item = _action(ops, "pm.governance.adoption.link", target)
        if item:
            actions.append(item)
    resolved = updated if document.get("status") in RESOLVED_STATUSES else None
    blocking = document.get("blocking_reason") if isinstance(document.get("blocking_reason"), str) else document.get("reason") if isinstance(document.get("reason"), str) else None
    return _base(kind="governance", ident=record["record_id"], row_type=row_type, basis=basis, title=document.get("title"),
                 title_reason="not_recorded", status=document.get("status"), status_reason="not_recorded",
                 owner=document.get("owner_actor"), owner_reason="not_recorded", due=_iso(document.get("review_at")),
                 due_reason="not_recorded", impact=None, impact_reason="not_recorded", priority=None,
                 priority_reason="not_recorded", next_step=document.get("response"), next_reason="not_recorded",
                 created=created, updated=updated, requirements=document.get("affected_requirement_ids") or [],
                 work_items=[], prs=[], actions=actions, steps=steps, refs=refs, resolved_at=resolved, blocking=blocking)


def _history_steps(ops, principal, workspace_id, record_id):
    from .pm_governance import list_history
    steps, after, seen = [], 0, 0
    while seen < MAX_SCAN:
        page = list_history(ops, principal, workspace_id, record_id, after=after, limit=min(PAGE, MAX_SCAN - seen))
        for revision in page["revisions"]:
            document = revision["document"]
            when = _iso(revision["created_at"])
            decision = document.get("decision")
            if isinstance(decision, dict):
                steps.append({"step": "decision", "at": when, "actor": decision.get("recorded_by")
                              if isinstance(decision.get("recorded_by"), str) else None, "title": document.get("title"),
                              "ref": {"kind": "governance", "id": revision["revision_id"]},
                              "status": decision.get("disposition") if isinstance(decision.get("disposition"), str) else None})
            adoption, proposed = document.get("adoption"), document.get("proposed_revision")
            adopted = adoption.get("revision_id") if isinstance(adoption, dict) else None
            proposed_id = proposed.get("revision_id") if isinstance(proposed, dict) else None
            if isinstance(adopted, str):
                steps.append({"step": "revision", "at": _iso(adoption.get("adopted_at")) or when,
                              "actor": adoption.get("adopted_by") if isinstance(adoption.get("adopted_by"), str) else None,
                              "title": document.get("title"), "ref": {"kind": "plan_revision", "id": adopted}, "status": "adopted"})
            elif isinstance(proposed_id, str):
                steps.append({"step": "revision", "at": when, "actor": revision.get("actor"), "title": document.get("title"),
                              "ref": {"kind": "plan_revision", "id": proposed_id}, "status": document.get("status")})
        seen += len(page["revisions"])
        if page["next_cursor"] is None:
            return steps, False
        if seen >= MAX_SCAN or page["next_cursor"] == after:
            return steps, True
        after = page["next_cursor"]
    return steps, True


def _bounded(fetch, key, cursors=("next_cursor", "next_after")):
    after, items = 0, []
    for _ in range(MAX_SCAN):
        room = MAX_SCAN - len(items)
        if room <= 0:
            return items, True
        page = fetch(after, min(PAGE, room))
        batch = list(page.get(key) or [])
        items.extend(batch[:room])
        cursor = next((page[name] for name in cursors if page.get(name) is not None), None)
        if cursor is None:
            return items, False
        if cursor == after or len(items) >= MAX_SCAN:
            return items[:MAX_SCAN], True
        after = cursor
    return items[:MAX_SCAN], True


def _collect_governance(ops, principal, workspace_id):
    from .pm_governance import list_records
    if not _table(ops.db, "pm_governance_records"):
        return [], False
    views, truncated = _bounded(lambda after, limit: list_records(ops, principal, workspace_id, after=after, limit=limit), "records")
    return [row for row in (_governance_row(ops, workspace_id, view) for view in views) if row], truncated


def _policy_rules(ops, principal, workspace_id):
    if "pm.maintenance.repair" not in ops.actions or not _table(ops.db, "pm_maintenance_policies"):
        return None
    from .pm_maintenance_policy import active_record, head
    if head(ops.db, workspace_id)["policy_id"] is None:
        return None
    try:
        record = active_record(ops, principal, workspace_id)
    except (OperationError, sqlite3.OperationalError):
        return None
    rules = (record.get("policy") or {}).get("repair_rules") or []
    if not isinstance(rules, list):
        return None
    selected = [rule for rule in rules if isinstance(rule, dict) and isinstance(rule.get("key"), str) and isinstance(rule.get("labels_all"), list) and rule["labels_all"]]
    return record.get("policy_id"), selected


def _reserved_findings(ops, workspace_id):
    if not _table(ops.db, "pm_maintenance_repairs"):
        return set()
    rows = ops.db.execute("SELECT finding_id FROM pm_maintenance_repairs WHERE workspace_id=? LIMIT ?", (workspace_id, MAX_SCAN + 1)).fetchall()
    if len(rows) > MAX_SCAN:
        return None
    return {row[0] for row in rows}


def _finding_actions(ops, workspace_id, item, rules, policy_id, ref, reserved):
    native = item.get("native") or {}
    facts = item.get("facts") or {}
    labels = facts.get("labels")
    if (rules is None or reserved is None or policy_id is None or ref is None or native.get("kind") != "issues"
            or item.get("existing_pull_request") or item.get("existing_work") or item.get("finding_id") in reserved
            or not isinstance(labels, list) or not all(isinstance(label, str) for label in labels)):
        return []
    have = set(labels)
    actions = []
    for rule in rules:
        if not set(rule["labels_all"]) <= have:
            continue
        entry = _action(ops, "pm.maintenance.repair", {"workspace_id": workspace_id},
                        {"policy_id": policy_id, "rule_key": rule["key"], "finding_ref": ref})
        if entry:
            actions.append(entry)
    return actions


def _finding_row(ops, workspace_id, item, source_kind, ref, rules, policy_id, reserved):
    facts = item.get("facts") if isinstance(item.get("facts"), dict) else {}
    priority = facts.get("priority") if isinstance(facts.get("priority"), str) else None
    impact = facts.get("severity") if isinstance(facts.get("severity"), str) else None
    prs, pull_unknown = [], False
    pull = item.get("existing_pull_request")
    if isinstance(pull, dict) and isinstance(pull.get("number"), int) and not isinstance(pull.get("number"), bool):
        if isinstance(pull.get("repository"), str):
            prs.append({"repository": pull["repository"], "number": pull["number"]})
        else:
            pull_unknown = True
    work, refs = [], set()
    for existing in item.get("existing_work") or []:
        if not isinstance(existing, dict):
            continue
        if isinstance(existing.get("work_item_id"), str):
            work.append(existing["work_item_id"])
            refs.add(("work_item", existing["work_item_id"]))
        if isinstance(existing.get("mapping_id"), str):
            refs.add(("issue_mapping", existing["mapping_id"]))
    row = _base(kind="finding", ident=item["finding_id"], row_type="issue", basis="neutral", title=None,
                title_reason="not_recorded", status=item.get("state") if isinstance(item.get("state"), str) else None,
                status_reason="not_recorded", owner=None, owner_reason="not_recorded", due=None, due_reason="not_recorded",
                impact=impact, impact_reason="not_recorded", priority=priority, priority_reason="not_recorded",
                next_step=None, next_reason="not_recorded", created=None, updated=None, requirements=[], work_items=work,
                prs=prs, actions=_finding_actions(ops, workspace_id, item, rules, policy_id, ref, reserved), steps=[],
                refs=refs, resolved_at=None, blocking=facts.get("blocking_reason") if isinstance(facts.get("blocking_reason"), str) else facts.get("reason") if isinstance(facts.get("reason"), str) else None)
    if row and pull_unknown:
        row["unknowns"]["pull_requests"] = "repository_name_not_stored"
    if row:
        row["_source_name"] = source_kind
    return row


def _collect_findings(ops, principal, workspace_id, rules_state, reserved):
    if not _table(ops.db, "pm_maintenance_policies"):
        return [], False
    from .pm_maintenance_findings import current_findings
    from .pm_maintenance_policy import head
    if head(ops.db, workspace_id)["policy_id"] is None:
        return [], False
    try:
        view = current_findings(ops, principal, workspace_id)
    except OperationError as exc:
        if exc.status in (403, 404):
            return [], False
        if exc.status == 409:
            return [], True
        raise
    except sqlite3.OperationalError as exc:
        if _missing(exc):
            return [], False
        raise
    policy_id, rules = rules_state if rules_state else (None, None)
    ref = view.get("observation_ref") if isinstance(view.get("observation_ref"), dict) else None
    finding_ref = None
    if ref and isinstance(ref.get("observation_id"), str) and isinstance(ref.get("document_digest"), str):
        finding_ref = {"observation_id": ref["observation_id"], "document_digest": ref["document_digest"]}
    rows, partial = [], False
    if view.get("visibility_filtered"):
        return [], False
    for name, source in (view.get("sources") or {}).items():
        if not isinstance(source, dict) or source.get("status") == "disabled":
            continue
        if source.get("status") != "complete":
            partial = True
            continue
        for item in source.get("findings") or []:
            if not isinstance(item, dict) or not isinstance(item.get("finding_id"), str):
                partial = True
                continue
            linked = {**finding_ref, "finding_id": item["finding_id"]} if finding_ref else None
            row = _finding_row(ops, workspace_id, item, name, linked, rules, policy_id, reserved)
            if row:
                rows.append(row)
    return rows, partial


def _collect_repairs(ops, principal, workspace_id):
    from .pm_maintenance_repairs import TERMINAL, list_repairs
    if not _table(ops.db, "pm_maintenance_repairs"):
        return [], False
    records, truncated = _bounded(lambda after, limit: list_repairs(ops, principal, workspace_id, after=after, limit=limit), "repairs", ("next_after",))
    rows = []
    for repair in records:
        status = repair.get("operation_status") if isinstance(repair.get("operation_status"), str) else None
        actions = []
        if status not in TERMINAL:
            item = _action(ops, "pm.maintenance.repair.evidence.record",
                           {"workspace_id": workspace_id, "repair_id": repair["repair_id"]})
            if item:
                actions.append(item)
        refs = set()
        if isinstance(repair.get("finding_id"), str):
            refs.add(("finding", repair["finding_id"]))
        work = [repair["work_item_id"]] if isinstance(repair.get("work_item_id"), str) else []
        for ident in work:
            refs.add(("work_item", ident))
        steps = [{"step": "work", "at": _iso(repair.get("created_at")), "actor": None, "title": None,
                  "ref": {"kind": "work_item", "id": ident}, "status": status} for ident in work]
        resolved = _iso(repair.get("finished_at")) if status in RESOLVED_STATUSES else None
        row = _base(kind="repair", ident=repair["repair_id"], row_type="action", basis="explicit", title=None,
                    title_reason="not_recorded", status=status, status_reason="not_recorded", owner=None,
                    owner_reason="not_recorded", due=_iso(repair.get("deadline_at")), due_reason="not_recorded",
                    impact=None, impact_reason="not_recorded", priority=None, priority_reason="not_recorded",
                    next_step=None, next_reason="not_recorded", created=_iso(repair.get("created_at")),
                    updated=_iso(repair.get("finished_at")) or _iso(repair.get("created_at")), requirements=[],
                    work_items=work, prs=[], actions=actions, steps=steps, refs=refs, resolved_at=resolved, blocking=None)
        if row:
            rows.append(row)
    return rows, truncated


def _text_status(events, fallback):
    chosen, when = fallback, None
    for event in events:
        document = event.get("document") if isinstance(event, dict) else None
        if not isinstance(document, dict) or not isinstance(document.get("status"), str):
            continue
        stamp = _iso(event.get("created_at"))
        if when is None or (stamp or "") >= (when or ""):
            chosen, when = document["status"], stamp
    return chosen, when


def _meeting_rows(ops, workspace_id, meeting, detail):
    completion = (detail or {}).get("completion")
    if not completion or not isinstance(completion.get("document"), dict):
        return [], False
    document = completion["document"]
    events = (detail or {}).get("follow_up_events") or []
    truncated = bool(((detail.get("coverage") or {}).get("histories") or {}).get("follow_ups", {}).get("has_more"))
    rows = []
    meeting_id = meeting["meeting_id"]
    for item in document.get("decisions") or []:
        if not isinstance(item, dict) or item.get("disposition") != "recorded_discussion_decision" or not isinstance(item.get("id"), str):
            continue
        ident = meeting_id + "~decision~" + item["id"]
        row = _base(kind="meeting", ident=ident, row_type="decision", basis="explicit", title=item.get("text") if isinstance(item.get("text"), str) else None,
                    title_reason="not_recorded", status=item.get("disposition"), status_reason="not_recorded",
                    owner=item.get("recorded_by") if isinstance(item.get("recorded_by"), str) else None, owner_reason="not_recorded",
                    due=None, due_reason="not_recorded", impact=None, impact_reason="not_recorded", priority=None,
                    priority_reason="not_recorded", next_step=None, next_reason="not_recorded",
                    created=_iso(completion.get("created_at")), updated=_iso(completion.get("created_at")), requirements=[],
                    work_items=[], prs=[], actions=[], steps=[], refs=set(), resolved_at=None, blocking=None)
        if row:
            rows.append(row)
    completed = bool(meeting.get("completion_id"))
    for item in document.get("follow_ups") or []:
        if not isinstance(item, dict) or not isinstance(item.get("id"), str):
            continue
        related = [event for event in events if isinstance(event, dict) and event.get("follow_up_id") == item["id"]]
        status, event_at = _text_status(related, item.get("status") if isinstance(item.get("status"), str) else None)
        actions = []
        if completed:
            entry = _action(ops, "pm.meeting.follow_up.record", {"workspace_id": workspace_id, "meeting_id": meeting_id},
                            {"follow_up_id": item["id"]})
            if entry:
                actions.append(entry)
        steps = [{"step": "action", "at": _iso(event.get("created_at")), "actor": event.get("actor") if isinstance(event.get("actor"), str) else None,
                  "title": item.get("text"), "ref": {"kind": "meeting", "id": meeting_id + "~follow_up~" + item["id"]},
                  "status": (event.get("document") or {}).get("status")} for event in related if isinstance(event.get("document"), dict)]
        resolved = event_at or _iso(completion.get("created_at")) if status in RESOLVED_STATUSES else None
        row = _base(kind="meeting", ident=meeting_id + "~follow_up~" + item["id"], row_type="action", basis="explicit",
                    title=item.get("text") if isinstance(item.get("text"), str) else None, title_reason="not_recorded",
                    status=status, status_reason="not_recorded", owner=item.get("owner_actor") if isinstance(item.get("owner_actor"), str) else None,
                    owner_reason="not_recorded", due=None, due_reason="not_recorded", impact=None, impact_reason="not_recorded",
                    priority=None, priority_reason="not_recorded", next_step=item.get("expected_effect") if isinstance(item.get("expected_effect"), str) else None,
                    next_reason="not_recorded", created=_iso(completion.get("created_at")), updated=event_at or _iso(completion.get("created_at")),
                    requirements=[], work_items=[], prs=[], actions=actions, steps=steps, refs=set(), resolved_at=resolved, blocking=None)
        if row:
            rows.append(row)
    return rows, truncated


def _review_rows(ops, workspace_id, review, detail):
    completion = (detail or {}).get("completion")
    events = (detail or {}).get("events") or []
    truncated = detail.get("next_cursor") is not None if detail else False
    rows = []
    review_id = review["review_id"]
    if completion and isinstance(completion.get("document"), dict):
        document = completion["document"]
        direction = document.get("direction") if isinstance(document.get("direction"), str) else None
        row = _base(kind="meeting", ident=review_id, row_type="decision", basis="explicit",
                    title=review.get("title") if isinstance(review.get("title"), str) else None, title_reason="not_recorded",
                    status=direction, status_reason="not_recorded", owner=completion.get("actor") if isinstance(completion.get("actor"), str) else None,
                    owner_reason="not_recorded", due=None, due_reason="not_recorded", impact=None, impact_reason="not_recorded",
                    priority=None, priority_reason="not_recorded", next_step=None, next_reason="not_recorded",
                    created=_iso(review.get("created_at")), updated=_iso(completion.get("created_at")), requirements=[],
                    work_items=[], prs=[], actions=[], steps=[], refs=set(), resolved_at=None, blocking=None)
        if row:
            rows.append(row)
        follow_events = {}
        for event in events:
            document_event = event.get("document") if isinstance(event, dict) else None
            if isinstance(document_event, dict) and event.get("kind") == "follow_up.record" and isinstance(document_event.get("follow_up_id"), str):
                follow_events.setdefault(document_event["follow_up_id"], []).append(event)
        for item in document.get("follow_ups") or []:
            if not isinstance(item, dict) or not isinstance(item.get("id"), str):
                continue
            status, event_at = _text_status(follow_events.get(item["id"], []), item.get("status") if isinstance(item.get("status"), str) else None)
            actions = []
            if review.get("status") == "completed":
                entry = _action(ops, "pm.maintenance_review.follow_up.record", {"workspace_id": workspace_id, "review_id": review_id},
                                {"follow_up_id": item["id"]})
                if entry:
                    actions.append(entry)
            resolved = event_at or _iso(completion.get("created_at")) if status in RESOLVED_STATUSES else None
            follow = _base(kind="follow_up", ident=review_id + "~follow_up~" + item["id"], row_type="action", basis="explicit",
                           title=item.get("text") if isinstance(item.get("text"), str) else None, title_reason="not_recorded",
                           status=status, status_reason="not_recorded", owner=item.get("owner_actor") if isinstance(item.get("owner_actor"), str) else None,
                           owner_reason="not_recorded", due=None, due_reason="not_recorded", impact=None, impact_reason="not_recorded",
                           priority=None, priority_reason="not_recorded", next_step=item.get("expected_effect") if isinstance(item.get("expected_effect"), str) else None,
                           next_reason="not_recorded", created=_iso(completion.get("created_at")), updated=event_at or _iso(completion.get("created_at")),
                           requirements=[], work_items=[], prs=[], actions=actions, steps=[], refs={("meeting", review_id)},
                           resolved_at=resolved, blocking=None)
            if follow:
                rows.append(follow)
    for event in events:
        document = event.get("document") if isinstance(event, dict) else None
        if event.get("kind") != "candidate.record" or not isinstance(document, dict) or document.get("disposition") != "proposal":
            continue
        if not isinstance(event.get("event_id"), str):
            continue
        candidate = _base(kind="proposal", ident=event["event_id"], row_type="change", basis="explicit",
                          title=document.get("title") if isinstance(document.get("title"), str) else None, title_reason="not_recorded",
                          status="proposed", status_reason=None, owner=None, owner_reason="not_recorded", due=None,
                          due_reason="not_recorded", impact=None, impact_reason="not_recorded", priority=None,
                          priority_reason="not_recorded", next_step=None, next_reason="not_recorded",
                          created=_iso(event.get("created_at")), updated=_iso(event.get("created_at")), requirements=[],
                          work_items=[], prs=[], actions=[], steps=[], refs={("meeting", review_id)}, resolved_at=None, blocking=None)
        if candidate:
            rows.append(candidate)
    return rows, truncated


def _expand(stubs, load, cap=DETAIL_CAP):
    rows, truncated = [], len(stubs) > cap
    for stub in stubs[:cap]:
        try:
            found, more = load(stub)
        except OperationError as exc:
            if _hidden(exc):
                continue
            raise
        except sqlite3.OperationalError as exc:
            if _missing(exc):
                return [], False
            raise
        rows.extend(found)
        truncated = truncated or more
    return rows, truncated


def _collect_meetings(ops, principal, workspace_id):
    from .pm_meetings import get_meeting, list_meetings
    if not _table(ops.db, "pm_meetings"):
        return [], False
    stubs, truncated = _bounded(lambda after, limit: list_meetings(ops, principal, workspace_id, after=after, limit=limit), "meetings")

    def load(stub):
        detail = get_meeting(ops, principal, workspace_id, stub["meeting_id"], history_limit=50)
        return _meeting_rows(ops, workspace_id, stub, detail)
    rows, more = _expand(stubs, load)
    return rows, truncated or more


def _collect_reviews(ops, principal, workspace_id):
    from .pm_maintenance_review import get_review, list_reviews
    if not _table(ops.db, "pm_maintenance_reviews"):
        return [], False
    stubs, truncated = _bounded(lambda after, limit: list_reviews(ops, principal, workspace_id, after=after, limit=limit), "reviews")

    def load(stub):
        detail = get_review(ops, principal, workspace_id, stub["review_id"], history_limit=20)
        return _review_rows(ops, workspace_id, {**stub, **detail["review"]}, detail)
    rows, more = _expand(stubs, load)
    return rows, truncated or more


def _intake_row(ops, workspace_id, intake):
    document = intake.get("document") if isinstance(intake.get("document"), dict) else {}
    refs = set()
    for item in document.get("selected_governance_refs") or []:
        if isinstance(item, dict) and isinstance(item.get("record_id"), str):
            refs.add(("governance", item["record_id"]))
    for item in document.get("selected_meeting_follow_up_refs") or []:
        if isinstance(item, dict) and isinstance(item.get("meeting_id"), str) and isinstance(item.get("follow_up_id"), str):
            refs.add(("meeting", item["meeting_id"] + "~follow_up~" + item["follow_up_id"]))
    created = _iso(intake.get("created_at"))
    return _base(kind="follow_up", ident=intake["intake_id"], row_type="action", basis="explicit", title=None,
                 title_reason="not_recorded", status=None, status_reason="not_recorded", owner=None, owner_reason="not_recorded",
                 due=None, due_reason="not_recorded", impact=None, impact_reason="not_recorded", priority=None,
                 priority_reason="not_recorded", next_step=None, next_reason="not_recorded", created=created, updated=created,
                 requirements=[], work_items=[], prs=[], actions=[], steps=[], refs=refs, resolved_at=None, blocking=None)


def _collect_intakes(ops, principal, workspace_id):
    from .pm_follow_up import get_intake, list_intakes
    if not _table(ops.db, "pm_follow_up_intakes"):
        return [], False
    stubs, truncated = _bounded(lambda after, limit: list_intakes(ops, principal, workspace_id, after=after, limit=limit), "intakes")

    def load(stub):
        intake = get_intake(ops, principal, workspace_id, stub["intake_id"])["intake"]
        row = _intake_row(ops, workspace_id, intake)
        return ([row] if row else []), False
    rows, more = _expand(stubs, load)
    return rows, truncated or more


def _proposal_row(proposal):
    document = proposal.get("document") if isinstance(proposal.get("document"), dict) else None
    validation = proposal.get("validation") if isinstance(proposal.get("validation"), dict) else {}
    title = document.get("title") if isinstance(document, dict) and isinstance(document.get("title"), str) else None
    requirements = []
    if isinstance(document, dict):
        for item in document.get("requirements") or []:
            if isinstance(item, dict) and isinstance(item.get("id"), str):
                requirements.append(item["id"])
    status = validation.get("status") if isinstance(validation.get("status"), str) else None
    created = _iso(proposal.get("created_at"))
    return _base(kind="proposal", ident=proposal["proposal_id"], row_type="change", basis="explicit", title=title,
                 title_reason="not_recorded", status=status, status_reason="not_recorded", owner=None, owner_reason="not_recorded",
                 due=None, due_reason="not_recorded", impact=None, impact_reason="not_recorded", priority=None,
                 priority_reason="not_recorded", next_step=None, next_reason="not_recorded", created=created, updated=created,
                 requirements=requirements, work_items=[], prs=[], actions=[], steps=[], refs=set(),
                 resolved_at=None, blocking=None)


def _collect_proposals(ops, principal, workspace_id):
    from .pm_conversation_proposals import get_proposal, list_proposals
    if not _table(ops.db, "pm_conversation_proposals"):
        return [], False
    stubs, truncated = _bounded(lambda after, limit: list_proposals(ops, principal, workspace_id, after=after, limit=limit), "proposals")

    def load(stub):
        proposal = get_proposal(ops, principal, workspace_id, stub["proposal_id"])["proposal"]
        row = _proposal_row(proposal)
        return ([row] if row else []), False
    rows, more = _expand(stubs, load)
    return rows, truncated or more


def _visible_proposal(ops, principal, workspace_id, operation_id, result):
    candidates = []
    if isinstance(result, dict) and isinstance(result.get("proposal_id"), str):
        candidates.append(result["proposal_id"])
    if _table(ops.db, "pm_source_lineage") and _table(ops.db, "pm_conversation_proposals"):
        originals = ops.db.execute("SELECT original_operation_id FROM pm_source_lineage WHERE resource_type=? AND resource_id=? LIMIT 8",
                                   ("operation", operation_id)).fetchall()
        for (original,) in originals:
            found = ops.db.execute("SELECT proposal_id FROM pm_conversation_proposals WHERE workspace_id=? AND operation_id=?",
                                   (workspace_id, original)).fetchone()
            if found:
                candidates.append(found[0])
    from .pm_conversation_proposals import get_proposal
    for proposal_id in candidates:
        try:
            get_proposal(ops, principal, workspace_id, proposal_id)
        except OperationError as exc:
            if _hidden(exc):
                continue
            raise
        except sqlite3.OperationalError as exc:
            if _missing(exc):
                return None
            raise
        return proposal_id
    return None


def _collect_plans(ops, principal, workspace_id):
    from .pm_plans import get_plan
    if not _table(ops.db, "pm_plan_adoptions"):
        return [], False
    rows = ops.db.execute("""SELECT operation_id,revision_id,previous_revision_id,adopted_by,created_at,result
        FROM pm_plan_adoptions WHERE workspace_id=? ORDER BY created_at,operation_id LIMIT ?""", (workspace_id, MAX_SCAN + 1)).fetchall()
    truncated = len(rows) > MAX_SCAN
    grouped = {}
    for row in rows[:MAX_SCAN]:
        grouped.setdefault(row["revision_id"], []).append(row)
    result = []
    for revision_id, adoptions in list(grouped.items())[:DETAIL_CAP]:
        if len(grouped) > DETAIL_CAP:
            truncated = True
        try:
            plan = get_plan(ops, principal, workspace_id, revision_id)["plan"]
        except OperationError as exc:
            if _hidden(exc):
                continue
            if exc.status == 409:
                truncated = True
                continue
            raise
        except sqlite3.OperationalError as exc:
            if _missing(exc):
                return [], False
            raise
        document = plan.get("document") if isinstance(plan.get("document"), dict) else {}
        title = document.get("title") if isinstance(document.get("title"), str) else None
        requirements = [item["id"] for item in document.get("requirements") or [] if isinstance(item, dict) and isinstance(item.get("id"), str)]
        created = _iso(adoptions[0]["created_at"])
        updated = _iso(adoptions[-1]["created_at"])
        refs, steps = set(), []
        previous = adoptions[-1]["previous_revision_id"]
        if isinstance(previous, str):
            refs.add(("plan_revision", previous))
        for adoption in adoptions:
            try:
                saved = json.loads(adoption["result"])
            except (TypeError, ValueError):
                saved = None
            proposal_id = _visible_proposal(ops, principal, workspace_id, adoption["operation_id"], saved)
            if proposal_id:
                refs.add(("proposal", proposal_id))
            steps.append({"step": "decision", "at": _iso(adoption["created_at"]), "actor": adoption["adopted_by"],
                          "title": title, "ref": {"kind": "plan_revision", "id": revision_id}, "status": "adopted"})
        row = _base(kind="plan_revision", ident=revision_id, row_type="decision", basis="explicit", title=title,
                    title_reason="not_recorded", status="adopted", status_reason=None, owner=adoptions[-1]["adopted_by"],
                    owner_reason="not_recorded", due=None, due_reason="not_recorded", impact=None, impact_reason="not_recorded",
                    priority=None, priority_reason="not_recorded", next_step=None, next_reason="not_recorded", created=created,
                    updated=updated, requirements=requirements, work_items=[], prs=[], actions=[], steps=steps, refs=refs,
                    resolved_at=created, blocking=None)
        if row:
            result.append(row)
    return result, truncated


def _mapping_row(ops, workspace_id, current, created_at):
    document = current.get("document") if isinstance(current.get("document"), dict) else {}
    selected = document.get("selected") if isinstance(document.get("selected"), dict) else {}
    title = selected.get("public_title") if isinstance(selected.get("public_title"), str) else None
    relation = document.get("relation") if isinstance(document.get("relation"), dict) else {}
    work = [relation["work_item_id"]] if isinstance(relation.get("work_item_id"), str) else []
    requirements = [item for item in relation.get("requirement_ids") or [] if isinstance(item, str)]
    state = document.get("state") if isinstance(document.get("state"), str) else None
    actions = []
    if state == "tracking":
        entry = _action(ops, "pm.issue.cancel", {"workspace_id": workspace_id, "mapping_id": current["mapping_id"]})
        if entry:
            actions.append(entry)
    if state != "cancelled" and work:
        entry = _action(ops, "pm.issue.publish", {"workspace_id": workspace_id, "work_item_id": work[0]})
        if entry:
            actions.append(entry)
    refs = {("work_item", ident) for ident in work}
    updated = _iso(current.get("created_at"))
    row = _base(kind="issue_mapping", ident=current["mapping_id"], row_type="issue", basis="explicit", title=title,
                title_reason="not_on_current_revision" if title is None else "not_recorded", status=state,
                status_reason="not_recorded", owner=None, owner_reason="not_recorded", due=None, due_reason="not_recorded",
                impact=None, impact_reason="not_recorded", priority=None, priority_reason="not_recorded", next_step=None,
                next_reason="not_recorded", created=created_at or updated, updated=updated, requirements=requirements,
                work_items=work, prs=[], actions=actions, steps=[], refs=refs,
                resolved_at=updated if state in RESOLVED_STATUSES else None, blocking=None)
    return row


def _collect_mappings(ops, principal, workspace_id):
    from .pm_issue_mappings import list_mappings
    if not _table(ops.db, "pm_issue_revisions"):
        return [], False
    currents, truncated = _bounded(lambda after, limit: list_mappings(ops, principal, workspace_id, after=after, limit=limit), "mappings")
    ids = [row["mapping_id"] for row in currents if isinstance(row.get("mapping_id"), str)]
    created = {}
    if ids:
        marks = ",".join("?" for _ in ids)
        for row in ops.db.execute(f"SELECT mapping_id,MIN(created_at) AS created_at FROM pm_issue_revisions WHERE workspace_id=? AND mapping_id IN ({marks}) GROUP BY mapping_id",  # noqa: S608 - bound placeholders
                                  (workspace_id, *ids)):
            created[row["mapping_id"]] = _iso(row["created_at"])
    rows = []
    for current in currents:
        row = _mapping_row(ops, workspace_id, current, created.get(current.get("mapping_id")))
        if row:
            rows.append(row)
    return rows, truncated


def _safe(source, fn, rows, notes):
    try:
        found, truncated = fn()
    except sqlite3.OperationalError as exc:
        if _missing(exc):
            return
        raise
    except OperationError as exc:
        if _hidden(exc):
            return
        raise
    rows.extend(row for row in found if row)
    if truncated:
        notes.append(source)


def _collect(ops, principal, workspace_id):
    rows, notes = [], []
    rules = None
    reserved = set()
    try:
        rules = _policy_rules(ops, principal, workspace_id)
        reserved = _reserved_findings(ops, workspace_id)
    except sqlite3.OperationalError as exc:
        if not _missing(exc):
            raise
    if reserved is None:
        notes.append("repair")
        reserved = set()
        rules = None
    _safe("governance", lambda: _collect_governance(ops, principal, workspace_id), rows, notes)
    _safe("finding", lambda: _collect_findings(ops, principal, workspace_id, rules, reserved), rows, notes)
    _safe("repair", lambda: _collect_repairs(ops, principal, workspace_id), rows, notes)
    _safe("review", lambda: _collect_reviews(ops, principal, workspace_id), rows, notes)
    _safe("follow_up", lambda: _collect_intakes(ops, principal, workspace_id), rows, notes)
    _safe("meeting", lambda: _collect_meetings(ops, principal, workspace_id), rows, notes)
    _safe("proposal", lambda: _collect_proposals(ops, principal, workspace_id), rows, notes)
    _safe("plan_revision", lambda: _collect_plans(ops, principal, workspace_id), rows, notes)
    _safe("issue_mapping", lambda: _collect_mappings(ops, principal, workspace_id), rows, notes)
    return rows, notes


def _public(row):
    return {key: value for key, value in row.items() if not key.startswith("_")}


def _filter(rows, row_type, status, owner, q):
    selected = []
    for row in rows:
        if row_type is not None and row["type"] != row_type:
            continue
        if status is not None and row["status"] != status:
            continue
        if owner is not None and row["owner"] != owner:
            continue
        if q is not None and (not isinstance(row["title"], str) or q.casefold() not in row["title"].casefold()):
            continue
        selected.append(row)
    return selected


def _monday(moment):
    day = moment.astimezone(timezone.utc).date()
    return day - timedelta(days=day.weekday())


def _age(created, as_of):
    if created is None:
        return None
    days = (as_of - created).total_seconds() / 86400
    if days < 0:
        return None
    if days < 8:
        return "0-7d"
    if days < 31:
        return "8-30d"
    if days < 90:
        return "31-90d"
    return "90d+"


def _summary(rows, as_of):
    counts = {bucket: 0 for bucket in AGE_BUCKETS}
    weeks = [_monday(as_of) - timedelta(days=7 * (11 - index)) for index in range(12)]
    weekly = {day: {"week_start": day.isoformat(), "opened": 0, "resolved": 0} for day in weeks}
    reasons = {}
    for row in rows:
        if row["_open"]:
            bucket = _age(_when(row["created_at"]), as_of)
            if bucket:
                counts[bucket] += 1
        opened = _when(row["created_at"])
        if opened is not None and _monday(opened) in weekly:
            weekly[_monday(opened)]["opened"] += 1
        resolved = _when(row["_resolved_at"])
        if resolved is not None and _monday(resolved) in weekly:
            weekly[_monday(resolved)]["resolved"] += 1
        reason = row.get("_blocking_reason")
        if isinstance(reason, str) and reason:
            reasons[reason] = reasons.get(reason, 0) + 1
    return {"open_by_age": [{"bucket": bucket, "count": counts[bucket]} for bucket in AGE_BUCKETS],
            "weekly": [weekly[day] for day in weeks],
            "blocking_reasons": [{"reason": reason, "count": reasons[reason]} for reason in sorted(reasons, key=lambda item: (-reasons[item], item))]}


def _coverage(complete, notes, rows, as_of):
    return {"complete": complete, "partial_reason": None if complete else "incomplete", "sources": notes,
            "time": "iso8601_utc", "open_statuses": sorted(OPEN_STATUSES), "resolved_statuses": sorted(RESOLVED_STATUSES),
            "unclassified_status": None if not complete else sum(1 for row in rows if row["_unclassified"]),
            "unknown_status": None if not complete else sum(1 for row in rows if row["_unknown_status"]),
            "open_without_known_age": None if not complete else sum(1 for row in rows if row["_open"] and _age(_when(row["created_at"]), as_of) is None),
            "blocking_reasons": "explicit_reason_field_only", "findings": "current_visible_observation",
            "scan_limit_per_source": MAX_SCAN}


def _page(rows, cursor, limit):
    ordered = sorted(rows, key=lambda row: row["row_id"])
    ordered.sort(key=lambda row: row["updated_at"] or "", reverse=True)
    if cursor is not None:
        stamp, ident = cursor
        ordered = [row for row in ordered if (row["updated_at"] or "") < stamp or ((row["updated_at"] or "") == stamp and row["row_id"] > ident)]
    page = ordered[:limit]
    nxt = None
    if len(ordered) > limit and page:
        last = page[-1]
        nxt = (last["updated_at"] or "") + "~" + last["row_id"]
    return page, nxt


def _direct(ops, principal, workspace_id, row_id, rows):
    """Return the row and whether a direct read proved it absent.

    Governance and repair can be loaded by id. A 404 from that read is absence,
    including when another source was truncated. Kinds that only exist inside the
    bounded scan stay unproved so a partial scan cannot become a false 404.
    """
    found = next((row for row in rows if row["row_id"] == row_id), None)
    if found:
        return found, False
    kind, _, ident = row_id.partition(":")
    try:
        if kind == "governance":
            from .pm_governance import get_record
            row = _governance_row(ops, workspace_id, get_record(ops, principal, workspace_id, ident))
            return row, row is None
        if kind == "repair":
            from .pm_maintenance_repairs import get_repair
            repair = get_repair(ops, principal, workspace_id, ident)["repair"]
            found = next((row for row in _collect_repairs(ops, principal, workspace_id)[0] if row["row_id"] == row_id), None)
            if found:
                return found, False
            row = _base(kind="repair", ident=repair["repair_id"], row_type="action", basis="explicit", title=None,
                        title_reason="not_recorded", status=repair.get("operation_status"), status_reason="not_recorded",
                        owner=None, owner_reason="not_recorded", due=_iso(repair.get("deadline_at")), due_reason="not_recorded",
                        impact=None, impact_reason="not_recorded", priority=None, priority_reason="not_recorded",
                        next_step=None, next_reason="not_recorded", created=_iso(repair.get("created_at")),
                        updated=_iso(repair.get("finished_at")) or _iso(repair.get("created_at")), requirements=[],
                        work_items=[repair["work_item_id"]] if isinstance(repair.get("work_item_id"), str) else [],
                        prs=[], actions=[], steps=[], refs=set(), resolved_at=None, blocking=None)
            return row, row is None
    except OperationError as exc:
        if _hidden(exc):
            return None, True
        raise
    except sqlite3.OperationalError as exc:
        if _missing(exc):
            return None, True
        raise
    return None, False


def _timeline(rows, selected):
    index = {(row["source"]["kind"], row["source"]["id"]): row for row in rows}
    key = (selected["source"]["kind"], selected["source"]["id"])
    index[key] = selected
    seen, stack = set(), [key]
    while stack:
        current = stack.pop()
        if current in seen:
            continue
        seen.add(current)
        row = index.get(current)
        if row is None:
            continue
        for ref in row["_refs"]:
            if ref not in seen:
                stack.append(ref)
        for other, candidate in index.items():
            if current in candidate["_refs"] and other not in seen:
                stack.append(other)
    steps = []
    for current in seen:
        row = index.get(current)
        if row is None:
            continue
        steps.append({"step": {"risk": "finding", "issue": "finding", "action": "action", "change": "proposal", "decision": "decision"}[row["type"]],
                      "at": row["updated_at"] or row["created_at"], "actor": row["owner"], "title": row["title"],
                      "ref": dict(row["source"]), "status": row["status"]})
        if row["row_id"] != selected["row_id"]:
            steps.extend(row["_steps"])
    steps.sort(key=lambda item: (item["at"] or "", STEP_ORDER.get(item["step"], 99), item["title"] or ""))
    return steps


def list_register(ops, principal, workspace_id, row_type=None, status=None, owner=None, q=None, after=None, limit=50):
    cursor = _params(row_type, status, owner, q, after, limit)
    _check(workspace_id, principal, ops)
    as_of = datetime.now(timezone.utc)
    rows, notes = _collect(ops, principal, workspace_id)
    complete = not notes
    selected = _filter(rows, row_type, status, owner, q)
    page, nxt = _page(selected, cursor, limit)
    coverage = _coverage(complete, notes, selected, as_of)
    return {"workspace_id": workspace_id, "as_of": as_of.isoformat(timespec="milliseconds"), "rows": [_public(row) for row in page],
            "next_cursor": nxt, "total_by_type": {name: sum(1 for row in selected if row["type"] == name) for name in TYPES} if complete else None,
            "summary": _summary(selected, as_of) if complete else None, "coverage": coverage}


def get_register(ops, principal, workspace_id, row_id):
    if not isinstance(row_id, str) or not _ROW_ID.fullmatch(row_id):
        _bad("row_id must name a register source")
    _check(workspace_id, principal, ops)
    as_of = datetime.now(timezone.utc)
    rows, notes = _collect(ops, principal, workspace_id)
    selected, proved_absent = _direct(ops, principal, workspace_id, row_id, rows)
    if selected is None:
        if proved_absent or not notes:
            raise OperationError("NOT_FOUND", "Register row not found", 404)
        raise OperationError("PM_REGISTER_INCOMPLETE", "The register scan cannot prove this row is absent", 409)
    extra, history_truncated = [], False
    if selected["source"]["kind"] == "governance" and "~" not in selected["source"]["id"]:
        try:
            extra, history_truncated = _history_steps(ops, principal, workspace_id, selected["source"]["id"])
        except OperationError as exc:
            if not _hidden(exc):
                raise
            raise OperationError("NOT_FOUND", "Register row not found", 404) from None
    steps = _timeline(rows, selected)
    steps.extend(item for item in selected["_steps"] if not extra or item["step"] not in {"decision", "revision"})
    steps.extend(extra)
    steps.sort(key=lambda item: (item["at"] or "", STEP_ORDER.get(item["step"], 99), item["title"] or ""))
    timeline_complete = not notes and not history_truncated
    return {"workspace_id": workspace_id, "as_of": as_of.isoformat(timespec="milliseconds"), "row": _public(selected),
            "timeline": steps, "timeline_coverage": {"complete": timeline_complete, "partial_reason": None if timeline_complete else "incomplete",
                                                     "sources": notes},
            "coverage": _coverage(not notes, notes, rows, as_of)}
