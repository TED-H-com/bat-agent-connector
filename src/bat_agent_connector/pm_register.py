"""Read-only issues, actions and changes register.

One request reads under a single deferred snapshot. ``as_of`` is when the
request started. Hidden records are omitted from rows, order, counts and
labels together. Relations come only from stored foreign keys.
"""
from __future__ import annotations

import base64
import json
import re
import sqlite3
from collections import deque
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
SOURCE_TYPES = {"governance": frozenset({"risk", "issue", "change"}), "finding": frozenset({"issue"}),
                "repair": frozenset({"action"}), "review": frozenset({"decision", "action", "change"}),
                "follow_up": frozenset({"action"}), "meeting": frozenset({"action", "decision"}),
                "proposal": frozenset({"change"}), "plan_revision": frozenset({"decision"}),
                "issue_mapping": frozenset({"issue"})}
_SELF_STEP = {"risk": "finding", "issue": "finding", "action": "action", "change": "proposal", "decision": "decision"}
MAX_SCAN = 200
DETAIL_CAP = 40
PAGE = 50
HISTORY_MEETING = 50
HISTORY_REVIEW = 20
_KINDS = "governance|finding|repair|follow_up|proposal|issue_mapping|meeting|plan_revision"
_ROW_ID = re.compile(rf"^(?:{_KINDS})\.[A-Za-z0-9._-]{{1,180}}$")
_STATUS = re.compile(r"[a-z0-9_]{1,64}$")
_ID = re.compile(r"[A-Za-z0-9._-]{1,180}$")
_CURSOR = re.compile(r"[A-Za-z0-9_-]{1,400}$")
_MEETING_ITEM = re.compile(r"^(pmmeet_[0-9a-f]{32})\.(follow_up|decision)\.([a-z][a-z0-9_.-]{0,79})$")
_REVIEW_FOLLOW = re.compile(r"^(pmrevw_[0-9a-f]{32})\.follow_up\.([A-Za-z0-9_-]{1,80})$")
_LINK_KINDS = frozenset({"governance", "finding", "repair", "follow_up", "proposal", "issue_mapping", "meeting", "plan_revision"})


class _Scan(list):
    """Partial source names, plus which of them stopped on a conflict or size cap."""

    def __init__(self):
        super().__init__()
        self.source_incomplete = set()

    def mark(self, source, *, bad=False):
        if source not in self:
            self.append(source)
        if bad:
            self.source_incomplete.add(source)


def capabilities():
    from .pm_source_lineage import MAX_ROOTS
    return {"version": 1, "read_only": True, "types": list(TYPES), "source_kinds": list(SOURCE_KINDS),
            "timeline_steps": list(STEPS), "dispatch": False, "writes": False,
            "summary": "complete_visible_scan_only", "time": "iso8601_utc",
            "row_id": "kind.ident", "cursor": "opaque_base64url_sequence", "max_roots_per_page": MAX_ROOTS}


def _bad(message):
    raise OperationError("INVALID_PARAMS", message, 422)


def _missing(exc):
    return isinstance(exc, sqlite3.OperationalError) and "no such table" in str(exc).lower()


def _hidden(exc):
    return isinstance(exc, OperationError) and exc.status in (403, 404)


def _conflict(exc):
    return isinstance(exc, OperationError) and exc.status in (409, 413)


def _next_sequence(ops, sql, params):
    """Sequence of the next source stub strictly after ``after``, from that source's own query."""

    def lookup(after):
        try:
            row = ops.db.execute(sql, (*params, after)).fetchone()
        except sqlite3.OperationalError as exc:
            if _missing(exc):
                return None
            raise
        if row is None or row[0] is None:
            return None
        return int(row[0])

    return lookup


def _workspace_stub(ops, table, workspace_id):
    sql = {
        "pm_governance_records": "SELECT sequence FROM pm_governance_records WHERE workspace_id=? AND sequence>? ORDER BY sequence LIMIT 1",
        "pm_maintenance_repairs": "SELECT sequence FROM pm_maintenance_repairs WHERE workspace_id=? AND sequence>? ORDER BY sequence LIMIT 1",
        "pm_meetings": "SELECT sequence FROM pm_meetings WHERE workspace_id=? AND sequence>? ORDER BY sequence LIMIT 1",
        "pm_maintenance_reviews": "SELECT sequence FROM pm_maintenance_reviews WHERE workspace_id=? AND sequence>? ORDER BY sequence LIMIT 1",
        "pm_follow_up_intakes": "SELECT sequence FROM pm_follow_up_intakes WHERE workspace_id=? AND sequence>? ORDER BY sequence LIMIT 1",
        "pm_conversation_proposals": "SELECT sequence FROM pm_conversation_proposals WHERE workspace_id=? AND sequence>? ORDER BY sequence LIMIT 1",
    }[table]
    return _next_sequence(ops, sql, (workspace_id,))


def _isolate(size, page_limit, after, seen, next_sequence):
    """Retry a conflicting page one record at a time, then skip that record's sequence."""
    if size != 1:
        return 1, after
    if next_sequence is None:
        return None
    nxt = next_sequence(after)
    if nxt is None or nxt <= after or nxt in seen:
        return None
    seen.add(nxt)
    return min(PAGE, page_limit), nxt


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


def _encode_cursor(sequence, row_id):
    raw = f"{int(sequence)}\n{row_id}".encode("ascii")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _decode_cursor(after):
    if not isinstance(after, str) or not _CURSOR.fullmatch(after):
        _bad("after must be a register cursor")
    padded = after + "=" * ((4 - len(after) % 4) % 4)
    try:
        text = base64.urlsafe_b64decode(padded.encode("ascii")).decode("ascii")
    except (ValueError, UnicodeDecodeError):
        _bad("after must be a register cursor")
    sequence, sep, row_id = text.partition("\n")
    if not sep or not sequence.isdecimal() or not _ROW_ID.fullmatch(row_id):
        _bad("after must be a register cursor")
    return int(sequence), row_id


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
    if after is None:
        return None
    return _decode_cursor(after)


def _ids(values):
    result = []
    for value in values:
        if isinstance(value, str) and value not in result:
            result.append(value)
    return result


def _base(*, kind, ident, row_type, basis, title, title_reason, status, status_reason, owner, owner_reason,
          due, due_reason, impact, impact_reason, priority, priority_reason, next_step, next_reason,
          created, updated, requirements, work_items, prs, actions, steps, refs, resolved_at, blocking,
          sequence=0):
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
    try:
        recorded = int(sequence)
    except (TypeError, ValueError):
        recorded = 0
    return {"row_id": kind + "." + ident, "type": row_type, "type_basis": basis, **public,
            "source": {"kind": kind, "id": ident}, "related": {"requirement_ids": _ids(requirements),
            "work_item_ids": _ids(work_items), "prs": clean_prs}, "available_actions": actions, "unknowns": unknowns,
            "_steps": steps, "_refs": refs, "_open": open_row, "_resolved_at": resolved_at if status in RESOLVED_STATUSES else None,
            "_blocking_reason": blocking, "_unclassified": unclassified, "_unknown_status": status is None,
            "_sequence": recorded, "_roots": set()}


def _action(ops, name, target, required=None):
    if name not in ops.actions:
        return None
    return {"action": name, "target": target, "required": required or {}}


def _text_reason(value):
    return value if isinstance(value, str) and value else None


def _recorded_reason(document):
    if not isinstance(document, dict):
        return None
    return _text_reason(document.get("blocking_reason")) or _text_reason(document.get("reason")) or _text_reason(document.get("result"))


def _event_reason(events, status):
    """Latest blocked follow-up stores its explanation in the event result."""
    if status != "blocked":
        return None
    chosen, when = None, None
    for event in events:
        document = event.get("document") if isinstance(event, dict) else None
        if not isinstance(document, dict) or document.get("status") != "blocked":
            continue
        reason = _recorded_reason(document)
        stamp = _iso(event.get("created_at"))
        if reason and (when is None or (stamp or "") >= (when or "")):
            chosen, when = reason, stamp
    return chosen


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
    blocking = _text_reason(document.get("blocking_reason")) or _text_reason(document.get("reason"))
    return _base(kind="governance", ident=record["record_id"], row_type=row_type, basis=basis, title=document.get("title"),
                 title_reason="not_recorded", status=document.get("status"), status_reason="not_recorded",
                 owner=document.get("owner_actor"), owner_reason="not_recorded", due=_iso(document.get("review_at")),
                 due_reason="not_recorded", impact=None, impact_reason="not_recorded", priority=None,
                 priority_reason="not_recorded", next_step=document.get("response"), next_reason="not_recorded",
                 created=created, updated=updated, requirements=document.get("affected_requirement_ids") or [],
                 work_items=[], prs=[], actions=actions, steps=steps, refs=refs, resolved_at=resolved, blocking=blocking,
                 sequence=record.get("sequence") or 0)


def _step_key(step):
    ref = step.get("ref") or {}
    if step.get("step") in {"decision", "revision"}:
        return ("decision", step.get("step"), ref.get("kind"), ref.get("id"), step.get("status"))
    return ("other", step.get("step"), ref.get("kind"), ref.get("id"), step.get("status"), step.get("at"))


def _add_step(steps, seen, step):
    key = _step_key(step)
    if key in seen:
        return
    seen.add(key)
    steps.append(step)


def _governance_history(ops, principal, workspace_id, record_id):
    """One record's own revisions. Decision and adoption steps collapse; a superseded proposal is omitted."""
    from .pm_governance import list_history
    cap = MAX_SCAN
    steps, seen, proposals, adopted = [], set(), [], set()
    after, truncated, bad = 0, False, False
    window, cursors = deque(), set()
    page_limit = min(PAGE, max(cap, 1))
    size = page_limit
    stub = _next_sequence(ops, "SELECT sequence FROM pm_governance_revisions WHERE record_id=? AND sequence>? "
                           "ORDER BY sequence LIMIT 1", (record_id,))
    while True:
        try:
            page = list_history(ops, principal, workspace_id, record_id, after=after, limit=size)
        except OperationError as exc:
            if not _conflict(exc):
                raise
            truncated, bad = True, True
            isolated = _isolate(size, page_limit, after, cursors, stub)
            if isolated is None:
                break
            size, after = isolated
            continue
        size = page_limit
        for revision in page["revisions"]:
            window.append(revision)
            if len(window) > cap:
                window.popleft()
                truncated = True
        if page["next_cursor"] is None:
            break
        if page["next_cursor"] == after or page["next_cursor"] in cursors:
            truncated = True
            break
        cursors.add(page["next_cursor"])
        after = page["next_cursor"]
    last_disposition = object()
    for revision in window:
        document = revision["document"]
        when = _iso(revision["created_at"])
        decision = document.get("decision") if isinstance(document.get("decision"), dict) else None
        disposition = decision.get("disposition") if decision and isinstance(decision.get("disposition"), str) else None
        if decision and (revision.get("action") == "pm.governance.decide" or disposition != last_disposition):
            _add_step(steps, seen, {"step": "decision", "at": when,
                                    "actor": decision.get("recorded_by") if isinstance(decision.get("recorded_by"), str) else None,
                                    "title": document.get("title"), "ref": {"kind": "governance", "id": record_id},
                                    "status": disposition})
        if disposition is not None:
            last_disposition = disposition
        adoption, proposed = document.get("adoption"), document.get("proposed_revision")
        adopted_id = adoption.get("revision_id") if isinstance(adoption, dict) else None
        proposed_id = proposed.get("revision_id") if isinstance(proposed, dict) else None
        if isinstance(adopted_id, str):
            adopted.add(adopted_id)
            _add_step(steps, seen, {"step": "revision", "at": _iso(adoption.get("adopted_at")) or when,
                                    "actor": adoption.get("adopted_by") if isinstance(adoption.get("adopted_by"), str) else None,
                                    "title": document.get("title"), "ref": {"kind": "plan_revision", "id": adopted_id},
                                    "status": "adopted"})
        elif isinstance(proposed_id, str):
            proposals.append({"step": "revision", "at": when, "actor": revision.get("actor"), "title": document.get("title"),
                              "ref": {"kind": "plan_revision", "id": proposed_id}, "status": document.get("status")})
    for step in proposals:
        if step["ref"]["id"] in adopted:
            continue
        _add_step(steps, seen, step)
    return steps, truncated, bad


def _newest(fetch, key, cap, *, cursors=("next_cursor", "next_after"), page_limit=100, next_sequence=None):
    """Keep the newest ``cap`` visible records. One 409/413 record is skipped; the source stays partial."""
    size = min(PAGE, page_limit)
    after, kept, truncated, bad, seen = 0, deque(), False, False, set()
    while True:
        try:
            page = fetch(after, size)
        except OperationError as exc:
            if not _conflict(exc):
                raise
            truncated, bad = True, True
            isolated = _isolate(size, page_limit, after, seen, next_sequence)
            if isolated is None:
                return list(kept), True, True
            size, after = isolated
            continue
        size = min(PAGE, page_limit)
        for item in list(page.get(key) or []):
            kept.append(item)
            if len(kept) > cap:
                kept.popleft()
                truncated = True
        cursor = next((page[name] for name in cursors if page.get(name) is not None), None)
        if cursor is None:
            return list(kept), truncated, bad
        if cursor == after or cursor in seen:
            return list(kept), True, bad
        seen.add(cursor)
        after = cursor


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
    blocking = _text_reason(facts.get("blocking_reason")) or _text_reason(facts.get("reason"))
    row = _base(kind="finding", ident=item["finding_id"], row_type="issue", basis="neutral", title=None,
                title_reason="not_recorded", status=item.get("state") if isinstance(item.get("state"), str) else None,
                status_reason="not_recorded", owner=None, owner_reason="not_recorded", due=None, due_reason="not_recorded",
                impact=impact, impact_reason="not_recorded", priority=priority, priority_reason="not_recorded",
                next_step=None, next_reason="not_recorded", created=None, updated=None, requirements=[], work_items=work,
                prs=prs, actions=_finding_actions(ops, workspace_id, item, rules, policy_id, ref, reserved), steps=[],
                refs=refs, resolved_at=None, blocking=blocking)
    if row and pull_unknown:
        row["unknowns"]["pull_requests"] = "repository_name_not_stored"
    if row:
        row["_source_name"] = source_kind
    return row


def _collect_findings(ops, principal, workspace_id, rules_state, reserved):
    if not _table(ops.db, "pm_maintenance_policies"):
        return [], False, False
    from .pm_maintenance_findings import current_findings
    from .pm_maintenance_policy import head
    if head(ops.db, workspace_id)["policy_id"] is None:
        return [], False, False
    try:
        view = current_findings(ops, principal, workspace_id)
    except OperationError as exc:
        if exc.status in (403, 404):
            return [], False, False
        if _conflict(exc):
            return [], True, True
        raise
    except sqlite3.OperationalError as exc:
        if _missing(exc):
            return [], False, False
        raise
    # A hidden current observation is unknown, the same as a missing one.
    if view.get("visibility_filtered"):
        return [], True, False
    policy_id, rules = rules_state if rules_state else (None, None)
    ref = view.get("observation_ref") if isinstance(view.get("observation_ref"), dict) else None
    finding_ref = None
    if ref and isinstance(ref.get("observation_id"), str) and isinstance(ref.get("document_digest"), str):
        finding_ref = {"observation_id": ref["observation_id"], "document_digest": ref["document_digest"]}
    rows, partial = [], False
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
    return rows, partial, False


def _repair_row(ops, workspace_id, repair):
    from .pm_maintenance_repairs import TERMINAL
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
    return _base(kind="repair", ident=repair["repair_id"], row_type="action", basis="explicit", title=None,
                 title_reason="not_recorded", status=status, status_reason="not_recorded", owner=None,
                 owner_reason="not_recorded", due=_iso(repair.get("deadline_at")), due_reason="not_recorded",
                 impact=None, impact_reason="not_recorded", priority=None, priority_reason="not_recorded",
                 next_step=None, next_reason="not_recorded", created=_iso(repair.get("created_at")),
                 updated=_iso(repair.get("finished_at")) or _iso(repair.get("created_at")), requirements=[],
                 work_items=work, prs=[], actions=actions, steps=steps, refs=refs, resolved_at=resolved, blocking=None,
                 sequence=repair.get("sequence") or 0)


def _collect_repairs(ops, principal, workspace_id):
    from .pm_maintenance_repairs import list_repairs
    if not _table(ops.db, "pm_maintenance_repairs"):
        return [], False, False
    records, truncated, bad = _newest(
        lambda after, limit: list_repairs(ops, principal, workspace_id, after=after, limit=limit),
        "repairs", MAX_SCAN, cursors=("next_after",), page_limit=50,
        next_sequence=_workspace_stub(ops, "pm_maintenance_repairs", workspace_id))
    rows = []
    for repair in records:
        row = _repair_row(ops, workspace_id, repair)
        if row:
            rows.append(row)
    return rows, truncated, bad


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
    sequence = meeting.get("sequence") or 0
    for item in document.get("decisions") or []:
        if not isinstance(item, dict) or item.get("disposition") != "recorded_discussion_decision" or not isinstance(item.get("id"), str):
            continue
        ident = meeting_id + ".decision." + item["id"]
        row = _base(kind="meeting", ident=ident, row_type="decision", basis="explicit", title=item.get("text") if isinstance(item.get("text"), str) else None,
                    title_reason="not_recorded", status=item.get("disposition"), status_reason="not_recorded",
                    owner=item.get("recorded_by") if isinstance(item.get("recorded_by"), str) else None, owner_reason="not_recorded",
                    due=None, due_reason="not_recorded", impact=None, impact_reason="not_recorded", priority=None,
                    priority_reason="not_recorded", next_step=None, next_reason="not_recorded",
                    created=_iso(completion.get("created_at")), updated=_iso(completion.get("created_at")), requirements=[],
                    work_items=[], prs=[], actions=[], steps=[], refs=set(), resolved_at=None, blocking=None, sequence=sequence)
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
        ident = meeting_id + ".follow_up." + item["id"]
        steps = [{"step": "action", "at": _iso(event.get("created_at")), "actor": event.get("actor") if isinstance(event.get("actor"), str) else None,
                  "title": item.get("text"), "ref": {"kind": "meeting", "id": ident},
                  "status": (event.get("document") or {}).get("status")} for event in related if isinstance(event.get("document"), dict)]
        resolved = event_at or _iso(completion.get("created_at")) if status in RESOLVED_STATUSES else None
        blocking = _event_reason(related, status) or (_recorded_reason(item) if status == "blocked" else None)
        row = _base(kind="meeting", ident=ident, row_type="action", basis="explicit",
                    title=item.get("text") if isinstance(item.get("text"), str) else None, title_reason="not_recorded",
                    status=status, status_reason="not_recorded", owner=item.get("owner_actor") if isinstance(item.get("owner_actor"), str) else None,
                    owner_reason="not_recorded", due=None, due_reason="not_recorded", impact=None, impact_reason="not_recorded",
                    priority=None, priority_reason="not_recorded", next_step=item.get("expected_effect") if isinstance(item.get("expected_effect"), str) else None,
                    next_reason="not_recorded", created=_iso(completion.get("created_at")), updated=event_at or _iso(completion.get("created_at")),
                    requirements=[], work_items=[], prs=[], actions=actions, steps=steps, refs=set(), resolved_at=resolved,
                    blocking=blocking, sequence=sequence)
        if row:
            rows.append(row)
    return rows, truncated


def _review_rows(ops, workspace_id, review, detail):
    completion = (detail or {}).get("completion")
    events = (detail or {}).get("events") or []
    truncated = detail.get("next_cursor") is not None if detail else False
    rows = []
    review_id = review["review_id"]
    sequence = review.get("sequence") or 0
    if completion and isinstance(completion.get("document"), dict):
        document = completion["document"]
        direction = document.get("direction") if isinstance(document.get("direction"), str) else None
        row = _base(kind="meeting", ident=review_id, row_type="decision", basis="explicit",
                    title=review.get("title") if isinstance(review.get("title"), str) else None, title_reason="not_recorded",
                    status=direction, status_reason="not_recorded", owner=completion.get("actor") if isinstance(completion.get("actor"), str) else None,
                    owner_reason="not_recorded", due=None, due_reason="not_recorded", impact=None, impact_reason="not_recorded",
                    priority=None, priority_reason="not_recorded", next_step=None, next_reason="not_recorded",
                    created=_iso(review.get("created_at")), updated=_iso(completion.get("created_at")), requirements=[],
                    work_items=[], prs=[], actions=[], steps=[], refs=set(), resolved_at=None, blocking=None, sequence=sequence)
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
            related = follow_events.get(item["id"], [])
            blocking = _event_reason(related, status) or (_recorded_reason(item) if status == "blocked" else None)
            follow = _base(kind="follow_up", ident=review_id + ".follow_up." + item["id"], row_type="action", basis="explicit",
                           title=item.get("text") if isinstance(item.get("text"), str) else None, title_reason="not_recorded",
                           status=status, status_reason="not_recorded", owner=item.get("owner_actor") if isinstance(item.get("owner_actor"), str) else None,
                           owner_reason="not_recorded", due=None, due_reason="not_recorded", impact=None, impact_reason="not_recorded",
                           priority=None, priority_reason="not_recorded", next_step=item.get("expected_effect") if isinstance(item.get("expected_effect"), str) else None,
                           next_reason="not_recorded", created=_iso(completion.get("created_at")), updated=event_at or _iso(completion.get("created_at")),
                           requirements=[], work_items=[], prs=[], actions=actions, steps=[], refs={("meeting", review_id)},
                           resolved_at=resolved, blocking=blocking, sequence=sequence)
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
                          work_items=[], prs=[], actions=[], steps=[], refs={("meeting", review_id)}, resolved_at=None,
                          blocking=None, sequence=event.get("sequence") or sequence)
        if candidate:
            rows.append(candidate)
    return rows, truncated


def _expand(stubs, load):
    """A listed stub that later disappears, or one conflict, marks that source partial."""
    cap = DETAIL_CAP
    chosen = list(stubs)
    truncated, bad = False, False
    if len(chosen) > cap:
        chosen = chosen[-cap:]
        truncated = True
    rows = []
    for stub in chosen:
        try:
            found, more = load(stub)
        except OperationError as exc:
            if _hidden(exc):
                truncated = True
                continue
            if _conflict(exc):
                truncated, bad = True, True
                continue
            raise
        except sqlite3.OperationalError as exc:
            if _missing(exc):
                return [], False, False
            raise
        rows.extend(found)
        truncated = truncated or more
    return rows, truncated, bad


def _follow_up_cutoff(ops, meeting_id):
    cap = HISTORY_MEETING
    if not _table(ops.db, "pm_meeting_follow_up_events"):
        return 0, False
    row = ops.db.execute("SELECT sequence FROM pm_meeting_follow_up_events WHERE meeting_id=? "
                         "ORDER BY sequence DESC LIMIT 1 OFFSET ?", (meeting_id, cap)).fetchone()
    if row is None:
        return 0, False
    return int(row["sequence"]), True


def _collect_meetings(ops, principal, workspace_id):
    from .pm_meetings import get_meeting, list_meetings
    if not _table(ops.db, "pm_meetings"):
        return [], False, False
    history = HISTORY_MEETING
    stubs, truncated, bad = _newest(
        lambda after, limit: list_meetings(ops, principal, workspace_id, after=after, limit=limit),
        "meetings", DETAIL_CAP, next_sequence=_workspace_stub(ops, "pm_meetings", workspace_id))

    def load(stub):
        after, older = _follow_up_cutoff(ops, stub["meeting_id"])
        detail = get_meeting(ops, principal, workspace_id, stub["meeting_id"], history_limit=history, follow_up_after=after)
        rows, more = _meeting_rows(ops, workspace_id, stub, detail)
        return rows, more or older
    found, more, expand_bad = _expand(stubs, load)
    return found, truncated or more, bad or expand_bad


def _review_events(ops, principal, workspace_id, review_id):
    from .pm_maintenance_review import list_events
    cap = HISTORY_REVIEW
    page_limit = min(100, max(cap, 1))
    after, kept, truncated, bad, seen = 0, deque(), False, False, set()
    size = page_limit
    stub = _next_sequence(ops, "SELECT sequence FROM pm_maintenance_review_events WHERE workspace_id=? "
                           "AND review_id=? AND sequence>? ORDER BY sequence LIMIT 1", (workspace_id, review_id))
    while True:
        try:
            page = list_events(ops, principal, workspace_id, review_id, after=after, limit=size)
        except OperationError as exc:
            if not _conflict(exc):
                raise
            truncated, bad = True, True
            isolated = _isolate(size, page_limit, after, seen, stub)
            if isolated is None:
                return list(kept), True, True
            size, after = isolated
            continue
        size = page_limit
        for event in page.get("events") or []:
            kept.append(event)
            if len(kept) > cap:
                kept.popleft()
                truncated = True
        cursor = page.get("next_cursor")
        if cursor is None:
            return list(kept), truncated, bad
        if cursor == after or cursor in seen:
            return list(kept), True, bad
        seen.add(cursor)
        after = cursor


def _collect_reviews(ops, principal, workspace_id):
    from .pm_maintenance_review import get_review, list_reviews
    if not _table(ops.db, "pm_maintenance_reviews"):
        return [], False, False
    stubs, truncated, bad = _newest(
        lambda after, limit: list_reviews(ops, principal, workspace_id, after=after, limit=limit),
        "reviews", DETAIL_CAP, next_sequence=_workspace_stub(ops, "pm_maintenance_reviews", workspace_id))
    skipped = []

    def load(stub):
        events, more, event_bad = _review_events(ops, principal, workspace_id, stub["review_id"])
        if event_bad:
            skipped.append(stub["review_id"])
        detail = get_review(ops, principal, workspace_id, stub["review_id"], history_limit=1)
        detail = {**detail, "events": events, "next_cursor": 1 if more else None}
        return _review_rows(ops, workspace_id, {**stub, **detail["review"]}, detail)
    found, more, expand_bad = _expand(stubs, load)
    return found, truncated or more or bool(skipped), bad or expand_bad or bool(skipped)


def _intake_row(ops, workspace_id, intake):
    document = intake.get("document") if isinstance(intake.get("document"), dict) else {}
    refs = set()
    for item in document.get("selected_governance_refs") or []:
        if isinstance(item, dict) and isinstance(item.get("record_id"), str):
            refs.add(("governance", item["record_id"]))
    for item in document.get("selected_meeting_follow_up_refs") or []:
        if isinstance(item, dict) and isinstance(item.get("meeting_id"), str) and isinstance(item.get("follow_up_id"), str):
            refs.add(("meeting", item["meeting_id"] + ".follow_up." + item["follow_up_id"]))
    created = _iso(intake.get("created_at"))
    return _base(kind="follow_up", ident=intake["intake_id"], row_type="action", basis="explicit", title=None,
                 title_reason="not_recorded", status=None, status_reason="not_recorded", owner=None, owner_reason="not_recorded",
                 due=None, due_reason="not_recorded", impact=None, impact_reason="not_recorded", priority=None,
                 priority_reason="not_recorded", next_step=None, next_reason="not_recorded", created=created, updated=created,
                 requirements=[], work_items=[], prs=[], actions=[], steps=[], refs=refs, resolved_at=None, blocking=None,
                 sequence=intake.get("sequence") or 0)


def _collect_intakes(ops, principal, workspace_id):
    from .pm_follow_up import get_intake, list_intakes
    if not _table(ops.db, "pm_follow_up_intakes"):
        return [], False, False
    stubs, truncated, bad = _newest(
        lambda after, limit: list_intakes(ops, principal, workspace_id, after=after, limit=limit),
        "intakes", DETAIL_CAP, next_sequence=_workspace_stub(ops, "pm_follow_up_intakes", workspace_id))

    def load(stub):
        intake = get_intake(ops, principal, workspace_id, stub["intake_id"])["intake"]
        if "sequence" not in intake:
            intake = {**intake, "sequence": stub.get("sequence") or 0}
        row = _intake_row(ops, workspace_id, intake)
        return ([row] if row else []), False
    found, more, expand_bad = _expand(stubs, load)
    return found, truncated or more, bad or expand_bad


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
                 resolved_at=None, blocking=None, sequence=proposal.get("sequence") or 0)


def _collect_proposals(ops, principal, workspace_id):
    from .pm_conversation_proposals import get_proposal, list_proposals
    if not _table(ops.db, "pm_conversation_proposals"):
        return [], False, False
    stubs, truncated, bad = _newest(
        lambda after, limit: list_proposals(ops, principal, workspace_id, after=after, limit=limit),
        "proposals", DETAIL_CAP, next_sequence=_workspace_stub(ops, "pm_conversation_proposals", workspace_id))

    def load(stub):
        proposal = get_proposal(ops, principal, workspace_id, stub["proposal_id"])["proposal"]
        if "sequence" not in proposal:
            proposal = {**proposal, "sequence": stub.get("sequence") or 0}
        row = _proposal_row(proposal)
        return ([row] if row else []), False
    found, more, expand_bad = _expand(stubs, load)
    return found, truncated or more, bad or expand_bad


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
    bad = False
    for proposal_id in candidates:
        try:
            get_proposal(ops, principal, workspace_id, proposal_id)
        except OperationError as exc:
            if _hidden(exc):
                continue
            if _conflict(exc):
                bad = True
                continue
            raise
        except sqlite3.OperationalError as exc:
            if _missing(exc):
                return None, False
            raise
        return proposal_id, bad
    return None, bad


def _collect_plans(ops, principal, workspace_id):
    from .pm_plans import get_plan
    if not _table(ops.db, "pm_plan_adoptions"):
        return [], False, False
    cap = DETAIL_CAP
    grouped = ops.db.execute("SELECT revision_id, MIN(rowid) AS sequence FROM pm_plan_adoptions WHERE workspace_id=? "
                             "GROUP BY revision_id ORDER BY sequence", (workspace_id,)).fetchall()
    # Walk oldest to newest and keep the tail, so a hidden adoption does not consume the cap.
    visible, truncated, bad = [], False, False
    window = deque()
    for item in grouped:
        try:
            plan = get_plan(ops, principal, workspace_id, item["revision_id"])["plan"]
        except OperationError as exc:
            if _hidden(exc):
                continue
            if _conflict(exc):
                bad = True
                continue
            raise
        except sqlite3.OperationalError as exc:
            if _missing(exc):
                return [], False, False
            raise
        window.append((item, plan))
        if len(window) > cap:
            window.popleft()
            truncated = True
    for item, plan in window:
        adoptions = ops.db.execute("SELECT operation_id,revision_id,adopted_by,created_at,result,rowid FROM pm_plan_adoptions "
                                   "WHERE workspace_id=? AND revision_id=? ORDER BY rowid", (workspace_id, item["revision_id"])).fetchall()
        document = plan.get("document") if isinstance(plan.get("document"), dict) else {}
        title = document.get("title") if isinstance(document.get("title"), str) else None
        requirements = [entry["id"] for entry in document.get("requirements") or [] if isinstance(entry, dict) and isinstance(entry.get("id"), str)]
        created = _iso(adoptions[0]["created_at"]) if adoptions else None
        updated = _iso(adoptions[-1]["created_at"]) if adoptions else created
        # The list never shows a timeline, so it does not read proposal ids.
        refs = set()
        actor = adoptions[-1]["adopted_by"] if adoptions else None
        steps = [{"step": "decision", "at": updated, "actor": actor, "title": title,
                  "ref": {"kind": "plan_revision", "id": item["revision_id"]}, "status": "adopted"}]
        row = _base(kind="plan_revision", ident=item["revision_id"], row_type="decision", basis="explicit", title=title,
                    title_reason="not_recorded", status="adopted", status_reason=None, owner=actor,
                    owner_reason="not_recorded", due=None, due_reason="not_recorded", impact=None, impact_reason="not_recorded",
                    priority=None, priority_reason="not_recorded", next_step=None, next_reason="not_recorded", created=created,
                    updated=updated, requirements=requirements, work_items=[], prs=[], actions=[], steps=steps, refs=refs,
                    resolved_at=created, blocking=None, sequence=item["sequence"])
        if row:
            visible.append(row)
    return visible, truncated, bad


def _mapping_row(ops, workspace_id, current, created_at, sequence):
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
    return _base(kind="issue_mapping", ident=current["mapping_id"], row_type="issue", basis="explicit", title=title,
                 title_reason="not_on_current_revision" if title is None else "not_recorded", status=state,
                 status_reason="not_recorded", owner=None, owner_reason="not_recorded", due=None, due_reason="not_recorded",
                 impact=None, impact_reason="not_recorded", priority=None, priority_reason="not_recorded", next_step=None,
                 next_reason="not_recorded", created=created_at or updated, updated=updated, requirements=requirements,
                 work_items=work, prs=[], actions=actions, steps=[], refs=refs,
                 resolved_at=updated if state in RESOLVED_STATUSES else None, blocking=None, sequence=sequence)


def _collect_mappings(ops, principal, workspace_id):
    from .pm_issue_mappings import list_mappings
    if not _table(ops.db, "pm_issue_revisions"):
        return [], False, False
    currents, truncated, bad = _newest(
        lambda after, limit: list_mappings(ops, principal, workspace_id, after=after, limit=limit), "mappings", MAX_SCAN,
        next_sequence=_next_sequence(ops, "SELECT MIN(sequence) FROM pm_issue_revisions WHERE workspace_id=? "
                                      "GROUP BY mapping_id HAVING MIN(sequence)>? ORDER BY MIN(sequence) LIMIT 1",
                                      (workspace_id,)))
    ids = [row["mapping_id"] for row in currents if isinstance(row.get("mapping_id"), str)]
    created, sequences = {}, {}
    if ids:
        marks = ",".join("?" for _ in ids)
        for row in ops.db.execute(f"SELECT mapping_id,MIN(sequence) AS sequence,MIN(created_at) AS created_at FROM pm_issue_revisions WHERE workspace_id=? AND mapping_id IN ({marks}) GROUP BY mapping_id",  # noqa: S608 - bound placeholders
                                  (workspace_id, *ids)):
            created[row["mapping_id"]] = _iso(row["created_at"])
            sequences[row["mapping_id"]] = row["sequence"]
    rows = []
    for current in currents:
        row = _mapping_row(ops, workspace_id, current, created.get(current.get("mapping_id")), sequences.get(current.get("mapping_id")) or 0)
        if row:
            rows.append(row)
    return rows, truncated, bad


def _collect_governance(ops, principal, workspace_id):
    from .pm_governance import list_records
    if not _table(ops.db, "pm_governance_records"):
        return [], False, False
    views, truncated, bad = _newest(
        lambda after, limit: list_records(ops, principal, workspace_id, after=after, limit=limit), "records", MAX_SCAN,
        next_sequence=_workspace_stub(ops, "pm_governance_records", workspace_id))
    return [row for row in (_governance_row(ops, workspace_id, view) for view in views) if row], truncated, bad


def _take(source, fn, rows, notes):
    try:
        found, truncated, bad = fn()
    except sqlite3.OperationalError as exc:
        if _missing(exc):
            return False, False
        raise
    except OperationError as exc:
        if _hidden(exc):
            return False, False
        if _conflict(exc):
            notes.mark(source, bad=True)
            return True, True
        raise
    rows.extend(row for row in found if row)
    if truncated or bad:
        notes.mark(source, bad=bad)
    return truncated, bad


def _collect(ops, principal, workspace_id):
    rows, notes = [], _Scan()
    repair_rows = []
    repair_truncated, repair_bad = _take("repair", lambda: _collect_repairs(ops, principal, workspace_id), repair_rows, notes)
    # A lineage-hidden repair is not an existing repair. Admit it before it can reserve a finding.
    repair_rows = _admit(ops, principal, repair_rows, notes)
    if "repair" in notes.source_incomplete:
        repair_bad = True
    reserved = None
    if not repair_truncated and not repair_bad:
        reserved = {ident for row in repair_rows for kind, ident in row["_refs"] if kind == "finding"}
    rules = None
    try:
        rules = _policy_rules(ops, principal, workspace_id)
    except sqlite3.OperationalError as exc:
        if not _missing(exc):
            raise
    _take("governance", lambda: _collect_governance(ops, principal, workspace_id), rows, notes)
    _take("finding", lambda: _collect_findings(ops, principal, workspace_id, rules, reserved), rows, notes)
    if repair_truncated or repair_bad:
        notes.mark("finding", bad=repair_bad)
    rows.extend(repair_rows)
    _take("review", lambda: _collect_reviews(ops, principal, workspace_id), rows, notes)
    _take("follow_up", lambda: _collect_intakes(ops, principal, workspace_id), rows, notes)
    _take("meeting", lambda: _collect_meetings(ops, principal, workspace_id), rows, notes)
    _take("proposal", lambda: _collect_proposals(ops, principal, workspace_id), rows, notes)
    _take("plan_revision", lambda: _collect_plans(ops, principal, workspace_id), rows, notes)
    _take("issue_mapping", lambda: _collect_mappings(ops, principal, workspace_id), rows, notes)
    return rows, notes


def _public(row):
    return {key: value for key, value in row.items() if not key.startswith("_")}


def _lineage(ops):
    from . import pm_source_lineage as lineage
    if not lineage.installed(ops.db):
        return None
    if ops.db.execute("SELECT 1 FROM pm_source_lineage LIMIT 1").fetchone() is None:
        return None
    return lineage


def _admit(ops, principal, rows, notes):
    """Drop a hidden row before totals. A row with too many roots is skipped and its source is partial."""
    lineage = _lineage(ops)
    if lineage is None:
        return rows
    kept = []
    for row in rows:
        try:
            roots = lineage.document_roots(ops.db, _public(row))
        except OperationError as exc:
            if _conflict(exc):
                notes.mark(row["source"]["kind"], bad=True)
                continue
            raise
        if not lineage.visible_roots(ops.db, principal, roots):
            continue
        row["_roots"] = roots
        kept.append(row)
    return kept


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


def _relevant(notes, row_type):
    names = [name for name in notes if row_type is None or row_type in SOURCE_TYPES.get(name, frozenset())]
    return names, bool(set(names) & set(getattr(notes, "source_incomplete", set())))


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
    if days < 91:
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


def _coverage(complete, notes, rows, as_of, *, source_incomplete=False):
    blocked = None
    if complete:
        blocked = sum(1 for row in rows if row.get("status") == "blocked" and not row.get("_blocking_reason"))
    return {"complete": complete, "partial_reason": None if complete else ("source_incomplete" if source_incomplete else "incomplete"),
            "sources": list(notes), "time": "iso8601_utc", "open_statuses": sorted(OPEN_STATUSES),
            "resolved_statuses": sorted(RESOLVED_STATUSES),
            "unclassified_status": None if not complete else sum(1 for row in rows if row["_unclassified"]),
            "unknown_status": None if not complete else sum(1 for row in rows if row["_unknown_status"]),
            "open_without_known_age": None if not complete else sum(1 for row in rows if row["_open"] and _age(_when(row["created_at"]), as_of) is None),
            "blocked_without_reason": blocked, "blocking_reasons": "recorded_reason_or_follow_up_result",
            "findings": "current_visible_observation", "scan_limit_per_source": MAX_SCAN}


def _page(rows, cursor, limit):
    from .pm_source_lineage import MAX_ROOTS
    ordered = sorted(rows, key=lambda row: (-int(row.get("_sequence") or 0), row["row_id"]))
    if cursor is not None:
        sequence, ident = cursor
        ordered = [row for row in ordered if int(row.get("_sequence") or 0) < sequence
                   or (int(row.get("_sequence") or 0) == sequence and row["row_id"] >= ident)]
    page, seen, excluded = [], set(), None
    for row in ordered:
        roots = row.get("_roots") or set()
        if len(page) >= limit or len(seen | roots) > MAX_ROOTS:
            excluded = row
            break
        seen |= set(roots)
        page.append(row)
    nxt = _encode_cursor(int(excluded.get("_sequence") or 0), excluded["row_id"]) if excluded is not None else None
    return page, nxt


def _self_step(row):
    return {"step": _SELF_STEP[row["type"]], "at": row["updated_at"] or row["created_at"], "actor": row["owner"],
            "title": row["title"], "ref": dict(row["source"]), "status": row["status"]}


def _plan_resolves(ops, principal, workspace_id, revision_id):
    from .pm_plans import get_plan
    try:
        get_plan(ops, principal, workspace_id, revision_id)
    except OperationError as exc:
        if _hidden(exc):
            return False
        if _conflict(exc):
            return None
        raise
    except sqlite3.OperationalError as exc:
        if _missing(exc):
            return False
        raise
    return True


def _link_source(kind, ident):
    """Collector name for a neighbour that cannot be read."""
    if kind == "meeting" and isinstance(ident, str) and ident.startswith("pmrevw_"):
        return "review"
    if kind == "follow_up" and isinstance(ident, str) and _REVIEW_FOLLOW.fullmatch(ident):
        return "review"
    if kind == "proposal" and isinstance(ident, str) and ident.startswith("pmrve_"):
        return "review"
    return kind


def _link_neighbor(ops, principal, workspace_id, row, steps, seen, kind, ident, notes):
    """Add one neighbour's own step. Hidden targets are omitted; a conflict marks that source."""
    if kind not in _LINK_KINDS or not isinstance(ident, str):
        return
    row_id = kind + "." + ident
    if row_id == row["row_id"] or not _ROW_ID.fullmatch(row_id):
        return
    try:
        loaded = _load_shallow(ops, principal, workspace_id, row_id)
    except OperationError as exc:
        if _hidden(exc):
            return
        if _conflict(exc):
            notes.mark(_link_source(kind, ident), bad=True)
            return
        raise
    except sqlite3.OperationalError as exc:
        if _missing(exc):
            return
        raise
    if loaded is not None:
        _add_step(steps, seen, _self_step(loaded))


def _matching_documents(ops, sql, params, predicate):
    try:
        rows = ops.db.execute(sql, params).fetchall()
    except sqlite3.OperationalError as exc:
        if _missing(exc):
            return []
        raise
    found = []
    for row in rows:
        try:
            document = json.loads(row["document"])
        except (TypeError, ValueError):
            continue
        if isinstance(document, dict) and predicate(document):
            found.append(row)
    return found


def _intakes_selecting(ops, workspace_id, predicate):
    if not _table(ops.db, "pm_follow_up_intakes"):
        return []
    return [row["intake_id"] for row in _matching_documents(
        ops, "SELECT intake_id, document FROM pm_follow_up_intakes WHERE workspace_id=?", (workspace_id,), predicate)]


def _records_disambiguating(ops, workspace_id, record_id):
    if not _table(ops.db, "pm_governance_heads"):
        return []
    sql = ("SELECT r.record_id, v.document FROM pm_governance_records r "
           "JOIN pm_governance_heads h ON h.record_id=r.record_id "
           "JOIN pm_governance_revisions v ON v.revision_id=h.revision_id "
           "WHERE r.workspace_id=? AND r.record_id!=?")

    def lists_this(document):
        ids = (document.get("disambiguation") or {}).get("record_ids") or []
        return record_id in ids

    return [row["record_id"] for row in _matching_documents(ops, sql, (workspace_id, record_id), lists_this)]


def _plans_for_proposal(ops, workspace_id, proposal_id):
    if not _table(ops.db, "pm_plan_adoptions"):
        return []
    found = []
    for row in _matching_documents(ops, "SELECT revision_id, result AS document FROM pm_plan_adoptions WHERE workspace_id=?",
                                   (workspace_id,), lambda document: document.get("proposal_id") == proposal_id):
        if row["revision_id"] not in found:
            found.append(row["revision_id"])
    if _table(ops.db, "pm_source_lineage") and _table(ops.db, "pm_conversation_proposals"):
        try:
            rows = ops.db.execute(
                "SELECT DISTINCT a.revision_id FROM pm_plan_adoptions a "
                "JOIN pm_source_lineage l ON l.resource_type='operation' AND l.resource_id=a.operation_id "
                "JOIN pm_conversation_proposals p ON p.workspace_id=a.workspace_id AND p.operation_id=l.original_operation_id "
                "WHERE a.workspace_id=? AND p.proposal_id=?", (workspace_id, proposal_id)).fetchall()
        except sqlite3.OperationalError as exc:
            if _missing(exc):
                return found
            raise
        for row in rows:
            if row["revision_id"] not in found:
                found.append(row["revision_id"])
    return found


def _review_siblings(ops, principal, workspace_id, review_id, notes):
    """Follow-ups and candidates stored on this review. A bad event keeps the readable siblings."""
    from .pm_maintenance_review import get_review
    try:
        events, _, event_bad = _review_events(ops, principal, workspace_id, review_id)
        if event_bad:
            notes.mark("review", bad=True)
        detail = get_review(ops, principal, workspace_id, review_id, history_limit=1)
    except OperationError as exc:
        if _hidden(exc):
            return []
        if _conflict(exc):
            notes.mark("review", bad=True)
            return []
        raise
    except sqlite3.OperationalError as exc:
        if _missing(exc):
            return []
        raise
    rows, _ = _review_rows(ops, workspace_id, detail["review"], {**detail, "events": events, "next_cursor": None})
    return [(row["source"]["kind"], row["source"]["id"]) for row in rows if row["source"]["id"] != review_id]


def _reverse_links(ops, principal, workspace_id, row, notes):
    """Rows whose stored foreign key names this one. One hop, then the caller loads each target."""
    kind, ident = row["source"]["kind"], row["source"]["id"]
    found = []
    if kind == "finding" and _table(ops.db, "pm_maintenance_repairs"):
        try:
            repairs = ops.db.execute("SELECT repair_id FROM pm_maintenance_repairs WHERE workspace_id=? AND finding_id=?",
                                     (workspace_id, ident)).fetchall()
        except sqlite3.OperationalError as exc:
            if not _missing(exc):
                raise
            repairs = []
        found.extend(("repair", item["repair_id"]) for item in repairs)
    if kind == "governance":
        found.extend(("follow_up", intake_id) for intake_id in _intakes_selecting(
            ops, workspace_id, lambda document: any(isinstance(item, dict) and item.get("record_id") == ident
                                                    for item in document.get("selected_governance_refs") or [])))
        found.extend(("governance", other) for other in _records_disambiguating(ops, workspace_id, ident))
    matched = _MEETING_ITEM.fullmatch(ident) if kind == "meeting" else None
    if matched and matched.group(2) == "follow_up":
        meeting_id, follow_up_id = matched.group(1), matched.group(3)
        found.extend(("follow_up", intake_id) for intake_id in _intakes_selecting(
            ops, workspace_id, lambda document: any(
                isinstance(item, dict) and item.get("meeting_id") == meeting_id and item.get("follow_up_id") == follow_up_id
                for item in document.get("selected_meeting_follow_up_refs") or [])))
    if kind == "meeting" and ident.startswith("pmrevw_"):
        found.extend(_review_siblings(ops, principal, workspace_id, ident, notes))
    if kind == "proposal" and ident.startswith("pmcp_"):
        found.extend(("plan_revision", revision_id) for revision_id in _plans_for_proposal(ops, workspace_id, ident))
    return found


def _timeline(ops, principal, workspace_id, row, notes):
    steps, seen = [], set()
    _add_step(steps, seen, _self_step(row))
    if row.get("_proposal_bad"):
        notes.mark("proposal", bad=True)
    if row["source"]["kind"] == "governance":
        for step in row["_steps"]:
            if step["step"] == "evidence":
                _add_step(steps, seen, step)
        try:
            history, truncated, bad = _governance_history(ops, principal, workspace_id, row["source"]["id"])
        except OperationError as exc:
            if _hidden(exc):
                return None
            if _conflict(exc):
                notes.mark("governance", bad=True)
                history = []
            else:
                raise
        else:
            if truncated or bad:
                notes.mark("governance", bad=bad)
        for step in history:
            ref = step.get("ref") or {}
            if ref.get("kind") == "plan_revision" and isinstance(ref.get("id"), str):
                visible = _plan_resolves(ops, principal, workspace_id, ref["id"])
                if visible is None:
                    notes.mark("plan_revision", bad=True)
                    continue
                if not visible:
                    continue
            _add_step(steps, seen, step)
    else:
        for step in row["_steps"]:
            _add_step(steps, seen, step)
    for kind, ident in row["_refs"]:
        _link_neighbor(ops, principal, workspace_id, row, steps, seen, kind, ident, notes)
    for kind, ident in _reverse_links(ops, principal, workspace_id, row, notes):
        _link_neighbor(ops, principal, workspace_id, row, steps, seen, kind, ident, notes)
    steps.sort(key=lambda item: (item["at"] or "", STEP_ORDER.get(item["step"], 99), item["title"] or ""))
    return steps


def _bound_timeline(ops, principal, row, steps, notes):
    lineage = _lineage(ops)
    if lineage is None:
        return steps
    from .pm_source_lineage import MAX_ROOTS
    roots = lineage.document_roots(ops.db, _public(row))
    if not lineage.visible_roots(ops.db, principal, roots):
        return None
    kept, stopped = [], False
    for step in steps:
        try:
            extra = lineage.document_roots(ops.db, step)
        except OperationError as exc:
            if _conflict(exc):
                notes.mark(row["source"]["kind"], bad=True)
                stopped = True
                continue
            raise
        if not lineage.visible_roots(ops.db, principal, extra):
            continue
        if len(roots | extra) > MAX_ROOTS:
            notes.mark(row["source"]["kind"])
            break
        roots |= extra
        kept.append(step)
    if stopped and row["source"]["kind"] not in notes:
        notes.mark(row["source"]["kind"], bad=True)
    return kept


def _load_shallow(ops, principal, workspace_id, row_id):
    kind, _, ident = row_id.partition(".")
    if kind == "governance":
        from .pm_governance import get_record
        return _governance_row(ops, workspace_id, get_record(ops, principal, workspace_id, ident))
    if kind == "repair":
        from .pm_maintenance_repairs import get_repair
        return _repair_row(ops, workspace_id, get_repair(ops, principal, workspace_id, ident)["repair"])
    if kind == "finding":
        return _finding_by_id(ops, principal, workspace_id, ident)
    if kind == "meeting":
        return _meeting_by_id(ops, principal, workspace_id, ident)
    if kind == "follow_up":
        return _follow_up_by_id(ops, principal, workspace_id, ident)
    if kind == "proposal":
        return _proposal_by_id(ops, principal, workspace_id, ident)
    if kind == "plan_revision":
        row = _plan_by_id(ops, principal, workspace_id, ident)
        return row
    if kind == "issue_mapping":
        from .pm_issue_mappings import get_mapping
        current = get_mapping(ops, principal, workspace_id, ident)["current"]
        created, sequence = None, 0
        if _table(ops.db, "pm_issue_revisions"):
            found = ops.db.execute("SELECT MIN(sequence) AS sequence, MIN(created_at) AS created_at FROM pm_issue_revisions "
                                   "WHERE workspace_id=? AND mapping_id=?", (workspace_id, ident)).fetchone()
            if found and found["sequence"] is not None:
                sequence, created = found["sequence"], _iso(found["created_at"])
        return _mapping_row(ops, workspace_id, current, created, sequence)
    return None


def _finding_by_id(ops, principal, workspace_id, finding_id):
    rules = _policy_rules(ops, principal, workspace_id)
    repairs, truncated, bad = _collect_repairs(ops, principal, workspace_id)
    notes = _Scan()
    if bad:
        notes.mark("repair", bad=True)
    repairs = _admit(ops, principal, repairs, notes)
    if truncated or bad or "repair" in notes.source_incomplete:
        reserved = None
    else:
        reserved = {ident for row in repairs for kind, ident in row["_refs"] if kind == "finding"}
    if not _table(ops.db, "pm_maintenance_policies"):
        return None
    from .pm_maintenance_findings import current_findings
    from .pm_maintenance_policy import head
    if head(ops.db, workspace_id)["policy_id"] is None:
        return None
    view = current_findings(ops, principal, workspace_id)
    if view.get("visibility_filtered"):
        return None
    policy_id, selected = rules if rules else (None, None)
    ref = view.get("observation_ref") if isinstance(view.get("observation_ref"), dict) else None
    for name, source in (view.get("sources") or {}).items():
        if not isinstance(source, dict) or source.get("status") != "complete":
            continue
        for item in source.get("findings") or []:
            if isinstance(item, dict) and item.get("finding_id") == finding_id:
                linked = {**ref, "finding_id": finding_id} if ref and isinstance(ref.get("observation_id"), str) and isinstance(ref.get("document_digest"), str) else None
                return _finding_row(ops, workspace_id, item, name, linked, selected, policy_id, reserved)
    return None


def _meeting_by_id(ops, principal, workspace_id, ident):
    matched = _MEETING_ITEM.fullmatch(ident)
    if matched:
        from .pm_meetings import get_meeting
        meeting_id = matched.group(1)
        after, _older = _follow_up_cutoff(ops, meeting_id)
        detail = get_meeting(ops, principal, workspace_id, meeting_id, history_limit=HISTORY_MEETING, follow_up_after=after)
        meeting = detail.get("meeting") or {"meeting_id": meeting_id}
        rows, _ = _meeting_rows(ops, workspace_id, meeting, detail)
        return next((row for row in rows if row["source"]["id"] == ident), None)
    if ident.startswith("pmrevw_"):
        from .pm_maintenance_review import get_review
        detail = get_review(ops, principal, workspace_id, ident, history_limit=1)
        rows, _ = _review_rows(ops, workspace_id, detail["review"], {**detail, "events": [], "next_cursor": None})
        return next((row for row in rows if row["source"]["id"] == ident), None)
    return None


def _follow_up_by_id(ops, principal, workspace_id, ident):
    matched = _REVIEW_FOLLOW.fullmatch(ident)
    if matched:
        from .pm_maintenance_review import get_review
        review_id = matched.group(1)
        events, _, _ = _review_events(ops, principal, workspace_id, review_id)
        detail = get_review(ops, principal, workspace_id, review_id, history_limit=1)
        rows, _ = _review_rows(ops, workspace_id, detail["review"], {**detail, "events": events, "next_cursor": None})
        return next((row for row in rows if row["source"]["id"] == ident), None)
    from .pm_follow_up import get_intake
    intake = get_intake(ops, principal, workspace_id, ident)["intake"]
    return _intake_row(ops, workspace_id, intake)


def _proposal_by_id(ops, principal, workspace_id, ident):
    if ident.startswith("pmcp_"):
        from .pm_conversation_proposals import get_proposal
        return _proposal_row(get_proposal(ops, principal, workspace_id, ident)["proposal"])
    if ident.startswith("pmrve_") and _table(ops.db, "pm_maintenance_review_events"):
        found = ops.db.execute("SELECT review_id FROM pm_maintenance_review_events WHERE event_id=?", (ident,)).fetchone()
        if found is None:
            return None
        from .pm_maintenance_review import get_review
        events, _, _ = _review_events(ops, principal, workspace_id, found["review_id"])
        detail = get_review(ops, principal, workspace_id, found["review_id"], history_limit=1)
        rows, _ = _review_rows(ops, workspace_id, detail["review"], {**detail, "events": events, "next_cursor": None})
        return next((row for row in rows if row["source"]["id"] == ident), None)
    return None


def _plan_by_id(ops, principal, workspace_id, revision_id):
    from .pm_plans import get_plan
    if not _table(ops.db, "pm_plan_adoptions"):
        return None
    item = ops.db.execute("SELECT MIN(rowid) AS sequence FROM pm_plan_adoptions WHERE workspace_id=? AND revision_id=?",
                          (workspace_id, revision_id)).fetchone()
    if item is None or item["sequence"] is None:
        return None
    plan = get_plan(ops, principal, workspace_id, revision_id)["plan"]
    adoptions = ops.db.execute("SELECT operation_id,adopted_by,created_at,result FROM pm_plan_adoptions "
                               "WHERE workspace_id=? AND revision_id=? ORDER BY rowid", (workspace_id, revision_id)).fetchall()
    document = plan.get("document") if isinstance(plan.get("document"), dict) else {}
    title = document.get("title") if isinstance(document.get("title"), str) else None
    requirements = [entry["id"] for entry in document.get("requirements") or [] if isinstance(entry, dict) and isinstance(entry.get("id"), str)]
    created = _iso(adoptions[0]["created_at"]) if adoptions else None
    updated = _iso(adoptions[-1]["created_at"]) if adoptions else created
    actor = adoptions[-1]["adopted_by"] if adoptions else None
    refs, proposal_bad = set(), False
    for adoption in adoptions:
        try:
            saved = json.loads(adoption["result"])
        except (TypeError, ValueError):
            saved = None
        proposal_id, bad_proposal = _visible_proposal(ops, principal, workspace_id, adoption["operation_id"], saved)
        proposal_bad = proposal_bad or bad_proposal
        if proposal_id:
            refs.add(("proposal", proposal_id))
    steps = [{"step": "decision", "at": updated, "actor": actor, "title": title,
              "ref": {"kind": "plan_revision", "id": revision_id}, "status": "adopted"}]
    row = _base(kind="plan_revision", ident=revision_id, row_type="decision", basis="explicit", title=title,
                title_reason="not_recorded", status="adopted", status_reason=None, owner=actor, owner_reason="not_recorded",
                due=None, due_reason="not_recorded", impact=None, impact_reason="not_recorded", priority=None,
                priority_reason="not_recorded", next_step=None, next_reason="not_recorded", created=created, updated=updated,
                requirements=requirements, work_items=[], prs=[], actions=[], steps=steps, refs=refs, resolved_at=created,
                blocking=None, sequence=item["sequence"])
    if row is not None and proposal_bad:
        row["_proposal_bad"] = True
    return row


def _direct(ops, principal, workspace_id, row_id, rows):
    """Load one governance or repair row by id. Other kinds use the same direct load."""
    found = next((row for row in rows if row["row_id"] == row_id), None)
    if found:
        return found, False
    try:
        row = _load_shallow(ops, principal, workspace_id, row_id)
    except OperationError as exc:
        if _hidden(exc):
            return None, True
        raise
    except sqlite3.OperationalError as exc:
        if _missing(exc):
            return None, True
        raise
    return row, row is None


def _read(ops):
    from .pm_release_baselines import _memoized_reads, _snapshot
    return _snapshot(ops.db), _memoized_reads()


def _view(workspace_id, as_of, page, nxt, selected, names, complete, bad):
    return {"workspace_id": workspace_id, "as_of": as_of.isoformat(timespec="milliseconds"), "rows": [_public(row) for row in page],
            "next_cursor": nxt, "total_by_type": {name: sum(1 for row in selected if row["type"] == name) for name in TYPES} if complete else None,
            "summary": _summary(selected, as_of) if complete else None,
            "coverage": _coverage(complete, names, selected, as_of, source_incomplete=bad)}


def list_register(ops, principal, workspace_id, row_type=None, status=None, owner=None, q=None, after=None, limit=50):
    cursor = _params(row_type, status, owner, q, after, limit)
    as_of = datetime.now(timezone.utc)
    snapshot, memo = _read(ops)
    with snapshot, memo:
        _check(workspace_id, principal, ops)
        rows, notes = _collect(ops, principal, workspace_id)
        visible = _admit(ops, principal, rows, notes)
        selected = _filter(visible, row_type, status, owner, q)
        names, bad = _relevant(notes, row_type)
        complete = not names
        page, nxt = _page(selected, cursor, limit)
        return _view(workspace_id, as_of, page, nxt, selected, names, complete, bad)


def get_register(ops, principal, workspace_id, row_id):
    if not isinstance(row_id, str) or not _ROW_ID.fullmatch(row_id):
        _bad("row_id must name a register source")
    as_of = datetime.now(timezone.utc)
    snapshot, memo = _read(ops)
    with snapshot, memo:
        _check(workspace_id, principal, ops)
        notes = _Scan()
        try:
            selected = _load_shallow(ops, principal, workspace_id, row_id)
        except OperationError as exc:
            if _hidden(exc):
                raise OperationError("NOT_FOUND", "Register row not found", 404) from None
            raise
        except sqlite3.OperationalError as exc:
            if _missing(exc):
                raise OperationError("NOT_FOUND", "Register row not found", 404) from None
            raise
        if selected is None:
            raise OperationError("NOT_FOUND", "Register row not found", 404)
        steps = _timeline(ops, principal, workspace_id, selected, notes)
        if steps is None:
            raise OperationError("NOT_FOUND", "Register row not found", 404)
        bounded = _bound_timeline(ops, principal, selected, steps, notes)
        if bounded is None:
            raise OperationError("NOT_FOUND", "Register row not found", 404)
        names, bad = _relevant(notes, None)
        complete = not names
        reason = None if complete else ("source_incomplete" if bad else "incomplete")
        return {"workspace_id": workspace_id, "as_of": as_of.isoformat(timespec="milliseconds"), "row": _public(selected),
                "timeline": bounded, "timeline_coverage": {"complete": complete, "partial_reason": reason, "sources": names},
                "coverage": _coverage(complete, names, [selected], as_of, source_incomplete=bad)}
