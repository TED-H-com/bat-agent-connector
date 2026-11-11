"""Completed Sprint Review decisions as explicit, unresolved Idea sources."""

from __future__ import annotations

from . import pm_meetings, pm_snapshots, pm_sprint_review
from .operations import OperationError

KIND = "sprint_review_decision"
FIELDS = {"kind", "meeting_id", "completion_id", "completion_digest", "decision_id"}


def validate(ref):
    pm_sprint_review.exact(ref, FIELDS)
    if ref["kind"] != KIND:
        raise pm_sprint_review.mismatch()
    for key in FIELDS:
        pm_meetings._text(ref[key], key, 200)
    return ref


def original(db, wid, ref):
    validate(ref)
    row = db.execute(
        "SELECT c.completion_id FROM pm_meeting_completions c JOIN pm_meetings m USING(meeting_id) WHERE m.workspace_id=? AND c.meeting_id=? AND c.completion_id=?",
        (wid, ref["meeting_id"], ref["completion_id"]),
    ).fetchone()
    if row is None:
        raise pm_sprint_review.unavailable()
    return pm_meetings._document(db, "pm_meeting_completions", "completion_id", ref["completion_id"])


def require(ops, principal, wid, ref):
    pm_meetings._member(ops, principal, wid)
    completion = original(ops.db, wid, ref)
    pm_sprint_review.completion_access(ops, principal, wid, completion)
    return project(completion, ref)


def project(completion, ref):
    doc = completion["document"]
    if pm_snapshots._digest(doc) != ref["completion_digest"] or not doc.get("guided_review"):
        raise pm_sprint_review.mismatch()
    selected = next((r for r in doc["governance_decisions"] if r["decision_id"] == ref["decision_id"]), None)
    if selected is None:
        raise pm_sprint_review.decision_required()
    decision = next(d for d in doc["decisions"] if d["id"] == ref["decision_id"])
    return {
        "reference": ref,
        "decision": decision,
        "governance": selected,
        "follow_ups": [f for f in doc["follow_ups"] if f["id"] in decision["follow_up_ids"]],
        "operation_id": completion["operation_id"],
        "scope_applied": False,
        "execution_authorized": False,
    }


def projection(db, wid, ref):
    return project(original(db, wid, ref), ref)


def operation_visible(db, principal, operation):
    from .pm_workload import _Reader

    try:
        binding = operation["external_refs"]["admission_binding"]
        ref = binding["pm_sprint_review_decision"]
        if (
            operation["action"] != "pm.input.record"
            or operation["params"]["source_ref"] != ref
            or binding["pm_lineage"]["workspace_id"] != operation["target"]["workspace_id"]
            or operation["params"]["status"] != "unresolved"
        ):
            return False
        require(_Reader(db), principal, operation["target"]["workspace_id"], ref)
        return True
    except (OperationError, KeyError, TypeError, ValueError):
        return False
