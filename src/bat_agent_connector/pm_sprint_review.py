"""Bounded fixed Sprint Review sources; no runtime, acceptance or dispatch authority."""

from __future__ import annotations

import json
import time

from . import pm_access, pm_meetings, pm_snapshots, pm_sprints, pm_workspace
from .operations import OperationError
from .pm_release_baselines import _snapshot_read

MAX_SOURCES = 20
MAX_SOURCE_BYTES = 1_000_000
BASIS_FIELDS = {"version", "results", "feedback_items", "governance_revision_ids", "follow_up_ref"}


def mismatch():
    return OperationError(
        "PM_SPRINT_REVIEW_REFERENCE_MISMATCH", "Select the exact original Review source", 409
    )


def unavailable():
    return OperationError("PM_MEETING_SOURCE_UNAVAILABLE", "Original Review sources are unavailable", 404)


def exact(value, fields):
    if not isinstance(value, dict) or set(value) != set(fields):
        raise OperationError("INVALID_PARAMS", "Supply exact guided Review fields", 422)


def items(value, label):
    return pm_meetings._list(value, label, MAX_SOURCES)


def source_budget(value):
    if len(pm_snapshots._json(value).encode("utf-8")) > MAX_SOURCE_BYTES:
        raise OperationError("PM_SPRINT_REVIEW_SOURCE_LIMIT", "Select fewer original Review sources", 413)


def validate(basis):
    exact(basis, BASIS_FIELDS)
    if type(basis["version"]) is not int or basis["version"] != 1:
        raise OperationError("INVALID_PARAMS", "Unsupported guided Review version", 422)
    for field in ("results", "feedback_items", "governance_revision_ids"):
        items(basis[field], field)
    seen = set()
    for row in basis["results"]:
        exact(row, ("attempt_id", "result_id", "digest", "assessment_ref"))
        for key in ("attempt_id", "result_id", "digest"):
            pm_meetings._text(row[key], key, 200)
        if row["result_id"] in seen:
            raise mismatch()
        seen.add(row["result_id"])
        if row["assessment_ref"] is not None:
            from .pm_quality_projections import REF_FIELDS

            exact(row["assessment_ref"], REF_FIELDS)
    seen, sources = set(), set()
    for row in basis["feedback_items"]:
        exact(row, ("id", "source_ref"))
        pm_meetings._key(row["id"], seen, "feedback")
        ref = row["source_ref"]
        if not isinstance(ref, dict):
            raise mismatch()
        if ref.get("kind") == "meeting_note":
            exact(ref, ("kind", "note_id"))
        elif ref.get("kind") == "discussion_input":
            exact(ref, ("kind", "thread_id", "input_id", "operation_id"))
        else:
            raise mismatch()
        for value in ref.values():
            pm_meetings._text(value, "feedback source", 200)
        identity = pm_snapshots._json(ref)
        if identity in sources:
            raise mismatch()
        sources.add(identity)
    pm_sprints._ids(basis["governance_revision_ids"], "governance revisions", MAX_SOURCES)
    ref = basis["follow_up_ref"]
    if ref is not None:
        exact(ref, ("intake_id", "document_digest", "issue_ids"))
        pm_sprints._ids(ref["issue_ids"], "issues", MAX_SOURCES)
        for key in ("intake_id", "document_digest"):
            pm_meetings._text(ref[key], key, 200)


def feedback(ops, principal, wid, meeting, context_id, selection):
    from . import pm_discussions

    ref = selection["source_ref"]
    if ref["kind"] == "meeting_note":
        row = ops.db.execute(
            "SELECT * FROM pm_meeting_notes WHERE meeting_id=? AND context_revision_id=? AND note_id=?",
            (meeting["meeting_id"], context_id, ref["note_id"]),
        ).fetchone()
        if row is None:
            raise unavailable()
        if not pm_meetings.operation_visible(ops.db, principal, {"operation_id": row["operation_id"]}):
            raise unavailable()
        source = {**dict(row), "source_refs": json.loads(row["source_refs"])}
        content = row["content"]
    else:
        pm_discussions._read(ops, principal, wid)
        pm_discussions._thread(ops.db, wid, ref["thread_id"])
        row = ops.db.execute(
            "SELECT * FROM pm_inputs WHERE workspace_id=? AND thread_id=? AND input_id=?",
            (wid, ref["thread_id"], ref["input_id"]),
        ).fetchone()
        if row is None:
            raise unavailable()
        pm_access.require_operation_sources(ops.db, principal, [row["operation_id"]])
        if row["operation_id"] != ref["operation_id"]:
            raise mismatch()
        source = pm_discussions._input_out(row, db=ops.db)
        content = row["value"]
    pm_meetings._text(content, "exact feedback", 4000)
    return {"id": selection["id"], "reference": ref, "content": content, "original": source}


def resolve(ops, principal, meeting, context, basis, *, project=False):
    """Resolve exact sources once; projection is used only in the freeze transaction."""
    from . import pm_attempt_result_reads, pm_follow_up, pm_quality_projections, pm_quality_reviews

    validate(basis)
    from . import pm_source_lineage

    if not pm_source_lineage.installed(ops.db):
        raise unavailable()
    wid = meeting["workspace_id"]
    pm_workspace.require_member(ops.db, principal, wid)
    snapshot = pm_meetings._snapshot(ops, principal, wid, context["snapshot_ref"])
    if not snapshot["baseline"]["document"]:
        raise OperationError(
            "PM_ADOPTED_PLAN_REQUIRED", "Guided Review needs an adopted snapshot baseline", 409
        )
    selected = {v["work_item_id"]: v for v in context["selected_items"]}
    resolved = {"results": [], "feedback": [], "governance": [], "follow_up": None}

    def budget():
        source_budget({"context": context, "snapshot": snapshot, **resolved})

    budget()
    for source in basis["results"]:
        value = pm_attempt_result_reads.read_fixed_receipt(
            ops, principal, wid, source["attempt_id"], source["result_id"], source["digest"]
        )
        saved = None
        if source["assessment_ref"] is not None:
            saved = pm_quality_projections.original(ops, principal, wid, source["assessment_ref"])
        # Authorize the exact originals before exposing relation differences.
        if value["work_item_id"] not in selected:
            raise mismatch()
        item = selected[value["work_item_id"]]
        binding = pm_sprints._binding(ops, principal, wid, item["binding_id"])
        original = value["receipt"]
        package = value["package"]
        # Work identity selects the Sprint row, never the Result's relation.
        # The exact package reader has already authorized and proved this context.
        original_context = package["document"]["context"] if package else None
        if original_context and original_context["work_ref"]["work_item_id"] != value["work_item_id"]:
            raise mismatch()
        original_binding = original_context["work_ref"]["binding_id"] if original_context else None
        original_plan = (
            {
                key: original_context["plan_ref"][key]
                for key in ("revision_id", "document_digest", "scope_revision")
            }
            if original_context
            else None
        )
        requirements = [row["id"] for row in package["document"]["requirements"]] if package else []
        binding_matches = bool(
            original_context
            and original_binding == binding["binding_id"]
            and original_plan["revision_id"] == binding["plan_revision_id"]
            and set(requirements) <= set(binding["requirement_ids"])
        )
        if saved is not None and (
            saved["context"]["work_ref"]["work_item_id"] != value["work_item_id"]
            or saved["context"]["execution_operation_id"] != value["execution_operation_id"]
            or not package
            or saved["context"]["package_ref"]["package_id"] != package["package_id"]
            or saved["context"]["native_result"]["candidate_commit"] != original["head_commit"]
        ):
            raise mismatch()
        fact = {
            "reference": source,
            "receipt": original,
            "work_item_id": value["work_item_id"],
            "binding_id": original_binding,
            "plan_ref": original_plan,
            "package_ref": {k: package[k] for k in ("workspace_id", "package_id", "document_digest")}
            if package
            else None,
            "assessment": saved,
            "quality": None,
            "requirements": requirements,
        }
        if project:
            doc = original["document"]
            fact["reported"] = pm_attempt_result_reads._self_reported_facet(
                doc, doc.get("latest_assistant_summary"), doc.get("turn_done")
            )
            fact.update(
                verification="unassessed", acceptance="unassessed", dod="unassessed", qualified=False, gaps=[]
            )
            if not package:
                fact["gaps"].append("no_visible_explicit_requirement_relation")
            elif not binding_matches:
                fact["gaps"].append("original_binding_differs")
            if saved is None:
                fact["gaps"].extend(("acceptance_missing", "verification_missing", "dod_unassessed"))
            else:
                ref = source["assessment_ref"]
                review = pm_quality_reviews.evaluate_review(
                    ops,
                    principal,
                    wid,
                    ref["review_id"],
                    binding_id=binding["binding_id"],
                    scope_revision=fact["plan_ref"]["scope_revision"],
                    dod_revision_id=context["artifacts"]["dod"]["revision_id"],
                    candidate_commit=original["head_commit"],
                )["review"]
                latest = review["assessment"]
                matches = bool(
                    latest
                    and all(latest[k] == ref[k] for k in ("operation_id", "version", "document_digest"))
                    and saved["context"]["execution_operation_id"] == value["execution_operation_id"]
                    and package
                    and saved["context"]["package_ref"]["package_id"] == package["package_id"]
                )
                fact["quality"] = {
                    "original": saved,
                    "applicable_at_selection": bool(matches and review["applicable"]),
                    "applicability_reasons": review["applicability_reasons"],
                    "current_applicability": "not_evaluated",
                }
                if matches and review["applicable"]:
                    fact.update(
                        acceptance=review["acceptance"], dod=review["dod"], qualified=review["qualified"]
                    )
                else:
                    fact["gaps"].append("assessment_not_applicable")
                # Proof facts remain in the immutable assessment matrix; no CI-only qualification.
                fact["verification"] = "see_original_criterion_proofs"
                if fact["acceptance"] != "accepted":
                    fact["gaps"].append("acceptance_" + fact["acceptance"])
                if fact["dod"] != "accepted":
                    fact["gaps"].append("dod_" + fact["dod"])
                if saved["context"]["dod_ref"] is None:
                    fact["gaps"].append("dod_missing")
            if original["document"].get("coverage", {}).get("document_truncated") or not original[
                "document"
            ].get("coverage", {}).get("turn_slice_complete"):
                fact["qualified"] = False
                fact["gaps"].append("result_coverage_incomplete")
        resolved["results"].append(fact)
        budget()
    for value in basis["feedback_items"]:
        resolved["feedback"].append(
            feedback(ops, principal, wid, meeting, meeting["context_revision_id"], value)
        )
        budget()
    if basis["governance_revision_ids"]:
        resolved["governance"], _ = pm_follow_up._governance(
            ops, principal, wid, basis["governance_revision_ids"]
        )
        budget()
    if basis["follow_up_ref"] is not None:
        ref = basis["follow_up_ref"]
        intake = pm_follow_up.get_intake(ops, principal, wid, ref["intake_id"])["intake"]
        if intake["document_digest"] != ref["document_digest"] or set(ref["issue_ids"]) - {
            i["issue_id"] for i in intake["document"]["issues"]
        }:
            raise mismatch()
        resolved["follow_up"] = {
            "reference": ref,
            "original": intake,
            "issues": [
                issue for issue in intake["document"]["issues"] if issue["issue_id"] in ref["issue_ids"]
            ],
        }
        budget()
    return resolved


def freeze(ops, principal, meeting, context, basis):
    resolved = resolve(ops, principal, meeting, context, basis, project=True)
    requirements, gaps = {}, []
    for issue in (resolved["follow_up"] or {}).get("issues", []):
        gaps.append({"source_id": issue["issue_id"], "reason": "unresolved_follow_up"})
    for revision in resolved["governance"]:
        if revision["document"]["status"] != "reported_resolved":
            gaps.append({"source_id": revision["revision_id"], "reason": "unresolved_governance"})
    by_binding = {}
    selected_bindings = {item["binding_id"] for item in context["selected_items"]}
    for result in resolved["results"]:
        if result["binding_id"] and "original_binding_differs" not in result["gaps"]:
            key = result["binding_id"], result["plan_ref"]["revision_id"]
            by_binding.setdefault(key, []).append(result)
        gaps.extend(
            {"source_id": result["reference"]["result_id"], "reason": reason} for reason in result["gaps"]
        )
    for item in context["selected_items"]:
        binding = pm_sprints._binding(ops, principal, meeting["workspace_id"], item["binding_id"])
        contributions = by_binding.get((binding["binding_id"], binding["plan_revision_id"]), [])
        if not contributions:
            gaps.extend(
                {"source_id": item["binding_id"], "reason": reason}
                for reason in ("result_not_selected", "acceptance_missing", "dod_unassessed")
            )
        for rid in binding["requirement_ids"]:
            key = binding["plan_revision_id"], rid
            statuses = []
            represented = [result for result in contributions if rid in result["requirements"]]
            for result in represented:
                original = result["assessment"]
                rows = original["assessment"]["document"]["requirements"] if original else []
                status = next((r["status"] for r in rows if r["requirement_id"] == rid), "unassessed")
                if not (result.get("quality") or {}).get("applicable_at_selection"):
                    status = "unassessed"
                statuses.append(status)
            status = (
                "rejected"
                if "rejected" in statuses
                else "accepted"
                if statuses and all(s == "accepted" for s in statuses)
                else "unassessed"
            )
            requirements.setdefault(key, []).append(
                {
                    "work_item_id": item["work_item_id"],
                    "acceptance": status,
                    "qualified": bool(represented) and all(r["qualified"] for r in represented),
                }
            )
    rows = [
        {
            "plan_revision_id": plan,
            "requirement_id": rid,
            "contributions": values,
            "acceptance": "rejected"
            if any(v["acceptance"] == "rejected" for v in values)
            else "accepted"
            if all(v["acceptance"] == "accepted" for v in values)
            else "unassessed",
            "qualified": all(v["qualified"] for v in values),
        }
        for (plan, rid), values in requirements.items()
    ]
    for gap in gaps:
        gap["issue_id"] = "pmsrg_" + pm_snapshots._digest([meeting["context_revision_id"], gap])[:32]
    return {
        "version": 1,
        "basis": basis,
        "context_revision_id": meeting["context_revision_id"],
        "context_digest": pm_snapshots._digest(context),
        "frozen_at": time.time(),
        "journal_cursor": ops.journal.api_head(),
        **resolved,
        "requirements": rows,
        "gaps": gaps,
        "coverage": {
            "snapshot": context["snapshot_ref"],
            "selected_results": len(resolved["results"]),
            "selected_bindings": len({binding for binding, _ in by_binding} & selected_bindings),
            "sprint_bindings": len(context["selected_items"]),
        },
    }


@_snapshot_read
def access_draft(ops, principal, wid, revision_id):
    from .pm_source_reads import check

    def permitted():
        _access_draft(ops, principal, wid, revision_id)
        return True

    if not check(ops.db, principal, "sprint_review_draft", (wid, revision_id), permitted):
        raise unavailable()
    # Memoize authority only; each caller receives its own immutable row decoding.
    return pm_meetings._document(ops.db, "pm_meeting_drafts", "revision_id", revision_id)


def _access_draft(ops, principal, wid, revision_id):
    row = ops.db.execute(
        "SELECT d.meeting_id FROM pm_meeting_drafts d JOIN pm_meetings m USING(meeting_id) WHERE m.workspace_id=? AND d.revision_id=?",
        (wid, revision_id),
    ).fetchone()
    if row is None:
        raise unavailable()
    draft = pm_meetings._document(ops.db, "pm_meeting_drafts", "revision_id", revision_id)
    from . import pm_source_lineage

    if not pm_source_lineage.visible(ops.db, principal, "operation", draft["operation_id"]):
        raise unavailable()
    context = pm_meetings._document(
        ops.db, "pm_meeting_contexts", "context_revision_id", draft["context_revision_id"]
    )
    pm_meetings._snapshot(ops, principal, wid, context["document"]["snapshot_ref"])
    guided = draft["document"].get("guided_review")
    if guided is not None:
        meeting = {
            "workspace_id": wid,
            "meeting_id": row["meeting_id"],
            "context_revision_id": draft["context_revision_id"],
        }
        resolve(ops, principal, meeting, context["document"], guided["basis"])
    return draft


def operation_access(db, principal, op):
    from .pm_workload import _Reader

    binding = (op.get("external_refs") or {}).get("admission_binding") or {}
    reader, wid = _Reader(db), op["target"]["workspace_id"]
    if binding.get("sprint_review"):
        saved = binding["sprint_review"]
        context = pm_meetings._document(
            db, "pm_meeting_contexts", "context_revision_id", saved["context_revision_id"]
        )
        meeting = {**op["target"], "context_revision_id": saved["context_revision_id"]}
        resolve(reader, principal, meeting, context["document"], saved["basis"])
    if binding.get("sprint_review_draft_id"):
        draft = access_draft(reader, principal, wid, binding["sprint_review_draft_id"])
        if "sprint_review_decisions" in binding:
            decisions(
                reader,
                principal,
                wid,
                draft,
                op["params"]["decision_ids"],
                binding["sprint_review_decisions"],
            )


def proposal(ops, principal, wid, ref):
    exact(ref, ("meeting_id", "draft_revision_id", "draft_digest", "decision_id"))
    draft = access_draft(ops, principal, wid, ref["draft_revision_id"])
    if draft["meeting_id"] != ref["meeting_id"] or draft["document_digest"] != ref["draft_digest"]:
        raise mismatch()
    decision = next((d for d in draft["document"]["decisions"] if d["id"] == ref["decision_id"]), None)
    if not draft["document"].get("guided_review") or decision is None:
        raise mismatch()
    return draft, decision


def decision_required():
    return OperationError(
        "PM_SPRINT_REVIEW_DECISION_REQUIRED", "Record each selected direction through governance", 409
    )


def decisions(ops, principal, wid, draft, selected, references):
    from . import pm_governance

    pm_meetings._list(references, "governance decisions", 10)
    by_id = {}
    for ref in references:
        exact(ref, ("decision_id", "revision_id", "document_digest"))
        revision = pm_governance._revision(ops.db, ref["revision_id"])
        record = pm_governance._record(ops.db, wid, revision["record_id"])
        pm_governance._access(ops, principal, record)
        original = {
            "meeting_id": draft["meeting_id"],
            "draft_revision_id": draft["revision_id"],
            "draft_digest": draft["document_digest"],
            "decision_id": ref["decision_id"],
        }
        _, proposed = proposal(ops, principal, wid, original)
        doc = revision["document"]
        if (
            revision["document_digest"] != ref["document_digest"]
            or record["context"].get("review_proposal_ref") != original
            or doc["owner_actor"] != proposed["owner_actor"]
            or doc["description"] != proposed["text"]
        ):
            raise mismatch()
        if revision["action"] != "pm.governance.decide" or not doc["decision"]:
            raise decision_required()
        if ref["decision_id"] in by_id:
            raise mismatch()
        by_id[ref["decision_id"]] = {
            **ref,
            "record_id": record["record_id"],
            "recorded_by": doc["decision"]["recorded_by"],
            "disposition": doc["decision"]["disposition"],
            "owner_actor": doc["owner_actor"],
        }
    if set(by_id) != set(selected):
        raise decision_required()
    return by_id


def completion_access(ops, principal, wid, completion):
    doc = completion["document"]
    draft = access_draft(ops, principal, wid, doc["draft_revision_id"])
    if draft["document"].get("guided_review"):
        refs = doc.get("governance_decisions", [])
        decisions(
            ops,
            principal,
            wid,
            draft,
            [r["decision_id"] for r in refs],
            [{k: r[k] for k in ("decision_id", "revision_id", "document_digest")} for r in refs],
        )
    return completion


def capabilities(ops):
    from . import (
        pm_attempt_results,
        pm_discussions,
        pm_follow_up,
        pm_governance,
        pm_quality_reviews,
        pm_source_lineage,
    )

    required = (
        *pm_meetings.ACTIONS,
        *pm_governance.ACTIONS,
        *pm_discussions.ACTIONS,
        *pm_quality_reviews.ACTIONS,
        pm_follow_up.ACTION,
    )
    if (
        not all(action in ops.actions for action in required)
        or not pm_source_lineage.installed(ops.db)
        or not pm_attempt_results.installed(ops.db)
    ):
        return {}
    return {
        "guided_sprint_review": {
            "version": 1,
            "fixed_results": True,
            "sourced_feedback": True,
            "governance_decisions": True,
            "planning_source": True,
        }
    }


def source_operations(frozen):
    """Explicit original rows feed the existing bounded lineage collector."""
    sources = [row["receipt"]["operation_id"] for row in frozen["results"]]
    sources += [
        row["assessment"]["assessment"]["operation_id"] for row in frozen["results"] if row["assessment"]
    ]
    sources += [row["original"]["operation_id"] for row in frozen["feedback"]]
    sources += [row["operation_id"] for row in frozen["governance"]]
    if frozen["follow_up"]:
        sources.append(frozen["follow_up"]["original"]["operation_id"])
    return sorted(set(sources))
