"""Fixed, source-authorized affected-work joins; no semantic inference or effects."""
from __future__ import annotations

import json

from . import (
    pm_evidence,
    pm_issue_mappings,
    pm_plans,
    pm_snapshots,
    pm_source_lineage,
    pm_work_packages,
    pm_work_start,
    pm_workspace,
)
from . import pm_plan_diff as diff
from .operations import OperationError
from .pm_release_baselines import _snapshot

MAX_BINDINGS = 50
MAX_SUBJECTS = 100
MAX_CANDIDATES = 1000
MAX_BYTES = 153600
READ = "pm_plan_impact_preview"


def capabilities():
    return {"version": 1, "read_only": True, "read": READ, "required_scopes": ["observe"],
            "max_bindings": MAX_BINDINGS, "max_subjects": MAX_SUBJECTS,
            "max_candidate_rows": MAX_CANDIDATES, "max_bytes": MAX_BYTES}


def _unavailable():
    return OperationError("PM_IMPACT_SOURCE_UNAVAILABLE", "The selected impact sources are unavailable", 404)


def _proof(condition):
    if not condition:
        raise OperationError("PM_IMPACT_EVIDENCE_INVALID", "Retained impact evidence is inconsistent or ambiguous", 409)


def _bound(condition):
    if not condition:
        raise OperationError("PM_IMPACT_TOO_LARGE", "Select a smaller binding subset; the complete impact matrix exceeds its bounds", 413)


def _source(reader, *args, **kwargs):
    try:
        return reader(*args, **kwargs)
    except OperationError:
        # Do not disclose even the kind/identity/count of a failed source closure.
        raise _unavailable() from None


def _eligible(ops, operation, bindings, cursor):
    """Private selection from original relations, before any source reader."""
    try:
        action = operation["action"]
        saved = operation["external_refs"].get("admission_binding", {})
        if action == "pm.work_package.create":
            bid = operation["preconditions"]["expected_binding_id"]
        elif action in {"pm.issue.publish", "pm.issue.cancel"}:
            bid = saved["issue_intent"]["relation"]["binding_id"]
        elif action == "repository.continue":
            bid = saved["pm_handoff"]["contract"]["package"]["context"]["work_ref"]["binding_id"]
        else:
            # Without a unique retained finish, an Attempt remains unassigned.
            row = ops.db.execute("SELECT attempt_id FROM work_attempts WHERE start_operation_id=?",
                                 (operation["operation_id"],)).fetchone()
            if row is None:
                return True
            events = ops.db.execute("""SELECT body FROM api_events WHERE resource_type='pm_work_attempt'
                AND resource_id=? AND seq<=? AND kind='work.start.succeeded'""", (row[0], cursor)).fetchall()
            if len(events) != 1:
                return True
            body = json.loads(events[0][0])
            link = ops.db.execute("SELECT operation_id FROM pm_work_start_children WHERE parent_id=? AND slot='handoff'",
                                  (operation["operation_id"],)).fetchone()
            child = ops._row(link[0]) if link else None
            if child is None or child["operation_id"] != body.get("execution_operation_id"):
                return True
            ref = child["external_refs"]["admission_binding"]["pm_handoff"]["contract"]
            if ref["package_ref"]["package_id"] != body.get("package_id"):
                return True
            bid = ref["package"]["context"]["work_ref"]["binding_id"]
        return bid in bindings
    except (KeyError, TypeError, ValueError):
        # Malformed evidence is checked after selected-source authorization.
        return True


def _candidates(ops, workspace_id, work_ids, cursor, bindings):
    # Discovery depends exclusively on retained events, not today's domain heads.
    # Original envelopes interpret admissions (including cancel and direct handoff).
    rows = ops.db.execute("""SELECT e.*,o.operation_id AS original_operation_id FROM api_events e
        LEFT JOIN operations o ON o.operation_id=CASE WHEN e.kind='operation.accepted' THEN e.resource_id
            ELSE json_extract(e.body,'$.operation_id') END
        WHERE e.seq<=? AND (
          (e.kind IN ('pm.work_package.created','work.start.succeeded','pm.issue.recorded')
            AND json_extract(e.body,'$.workspace_id')=? AND json_extract(e.body,'$.work_item_id') IN (SELECT value FROM json_each(?)))
          OR (e.kind='operation.accepted' AND e.resource_type='operation' AND (
            (o.action IN ('pm.work_package.create','work.start','pm.issue.publish','pm.issue.cancel')
              AND json_extract(o.target,'$.workspace_id')=?
              AND COALESCE(json_extract(o.target,'$.work_item_id'),
                json_extract(o.external_refs,'$.admission_binding.issue_intent.work_item_id')) IN (SELECT value FROM json_each(?)))
            OR (o.action='repository.continue'
              AND json_extract(o.external_refs,'$.admission_binding.pm_handoff.contract.package_ref.workspace_id')=?
              AND json_extract(o.external_refs,'$.admission_binding.pm_handoff.contract.package.work_item_id') IN (SELECT value FROM json_each(?)))
          ))) ORDER BY e.seq""",
        (cursor, workspace_id, json.dumps(work_ids), workspace_id, json.dumps(work_ids),
         workspace_id, json.dumps(work_ids)))
    groups = {}
    inspected = 0
    for row in rows:
        if row["original_operation_id"] is None:
            raise _unavailable()
        if not _eligible(ops, ops._row(row["original_operation_id"]), bindings, cursor):
            continue
        inspected += 1
        _bound(inspected <= MAX_CANDIDATES)
        group = groups.setdefault(row["original_operation_id"], {})
        _proof(row["kind"] not in group)
        group[row["kind"]] = {**dict(row), "body": json.loads(row["body"])}
    return groups


def _event(event, resource_type, resource_id, **fields):
    _proof(event["resource_type"] == resource_type and event["resource_id"] == resource_id
           and all(event["body"].get(key) == value for key, value in fields.items()))


def _package(ops, principal, wid, package_id):
    return _source(pm_work_packages.get_package, ops, principal, wid, package_id)["package"]


def _package_ref(package):
    return {key: package[key] for key in ("package_id", "document_digest")}


def _requirements(package):
    return sorted(r["id"] for r in package["document"]["requirements"])


def _handoff(ops, principal, wid, operation):
    saved = operation["external_refs"]["admission_binding"]["pm_handoff"]
    contract = saved["contract"]
    _proof(diff._digest(contract) == saved["contract_digest"])
    ref = contract["package_ref"]
    _proof(ref["workspace_id"] == wid)
    package = _package(ops, principal, wid, ref["package_id"])
    _proof(package["document_digest"] == ref["document_digest"] and package["document"] == contract["package"])
    return package, saved["contract_digest"]


def _subject(ops, principal, wid, bindings, operation, events):
    oid, action = operation["operation_id"], operation["action"]
    admission = events.get("operation.accepted")
    if admission:
        _event(admission, "operation", oid, action=action, target=operation["target"])
    base = {"operation_id": oid, "event_seqs": sorted(e["seq"] for e in events.values()),
            "semantic_impact": "unassessed"}
    target = operation["target"]
    saved = operation["external_refs"].get("admission_binding", {})
    if action == "pm.work_package.create":
        bid = operation["preconditions"]["expected_binding_id"]
        if bid not in bindings:
            return None
        base.update(kind="package", binding_id=bid, work_item_id=target["work_item_id"])
        event = events.get("pm.work_package.created")
        if not event:
            return {**base, "boundary": "creation_boundary_unproven", "package_ref": None}
        package = _package(ops, principal, wid, event["resource_id"])
        _event(event, "pm_work_package", package["package_id"], workspace_id=wid, operation_id=oid,
               work_item_id=package["work_item_id"], package_id=package["package_id"], document_digest=package["document_digest"])
        doc = package["document"]
        _proof(package["operation_id"] == oid and package["work_item_id"] == target["work_item_id"]
               and doc["context"]["work_ref"]["binding_id"] == bid)
        return {**base, "boundary": "proven", "package_ref": _package_ref(package),
                "plan_ref": doc["context"]["plan_ref"], "requirements": doc["requirements"],
                "requirement_ids": _requirements(package),
                "verification": doc["verification"], "acceptance_verification": "not_performed"}
    if action == "repository.continue":
        package, digest = _handoff(ops, principal, wid, operation)
        bid = package["document"]["context"]["work_ref"]["binding_id"]
        if bid not in bindings:
            return None
        _proof(admission is not None)
        return {**base, "kind": "execution", "boundary": "proven", "binding_id": bid,
                "work_item_id": package["work_item_id"], "package_ref": _package_ref(package),
                "requirement_ids": _requirements(package), "contract_digest": digest, "execution_state": "not_reconstructed"}
    if action in {"pm.issue.publish", "pm.issue.cancel"}:
        intent = saved["issue_intent"]
        _proof(diff._digest(intent) == saved["issue_intent_digest"])
        bid = intent["relation"]["binding_id"]
        if bid not in bindings:
            return None
        package = _package(ops, principal, wid, intent["package_ref"]["package_id"])
        _proof(_package_ref(package) == intent["package_ref"])
        base.update(kind="issue", binding_id=bid, work_item_id=intent["work_item_id"],
                    package_ref=intent["package_ref"], requirement_ids=_requirements(package), cross_scope_publication="unsupported")
        event = events.get("pm.issue.recorded")
        if not event:
            return {**base, "boundary": "creation_boundary_unproven", "publication": "publication_unknown"}
        mapping = _source(pm_issue_mappings.get_mapping, ops, principal, wid, intent["mapping_id"])
        matches = [r for r in mapping["revisions"] if r["revision_id"] == event["body"].get("revision_id")]
        _proof(len(matches) == 1)
        revision = matches[0]
        doc = revision["document"]
        _event(event, "pm_issue_mapping", intent["mapping_id"], workspace_id=wid, operation_id=oid,
               work_item_id=intent["work_item_id"], mapping_id=intent["mapping_id"], revision_id=revision["revision_id"])
        _proof(revision["operation_id"] == oid and doc["relation"] == intent["relation"]
               and doc["package_ref"] == intent["package_ref"])
        return {**base, "boundary": "proven", "mapping_id": intent["mapping_id"], "revision_id": revision["revision_id"],
                "document_digest": revision["document_digest"], "relation": doc["relation"], "issue": doc["issue"],
                "publication": "receipt_recorded" if doc.get("receipt") else "publication_unknown"}
    _proof(action == "work.start")
    intent = pm_work_start._intent(operation)
    row = ops.db.execute("SELECT * FROM work_attempts WHERE start_operation_id=?", (oid,)).fetchone()
    if row is None:
        raise _unavailable()
    _source(pm_work_start.get_attempt, ops, principal, wid, row["attempt_id"])
    _proof(row["workspace_id"] == wid and row["work_item_id"] == target["work_item_id"]
           and row["admission_digest"] == diff._digest(intent))
    base.update(kind="attempt", work_item_id=target["work_item_id"],
                boundary="proven" if admission else "creation_boundary_unproven",
                association="association_unknown", package_ref=None)
    # An admitted Attempt row is inserted in the same transaction as its event.
    if admission:
        base["attempt_id"] = row["attempt_id"]
    event = events.get("work.start.succeeded")
    if not event:
        return base
    body = event["body"]
    _event(event, "pm_work_attempt", row["attempt_id"], workspace_id=wid, operation_id=oid,
           work_item_id=row["work_item_id"], attempt_id=row["attempt_id"])
    children = pm_work_start._children(ops, operation)
    execution = children.get("handoff")
    _proof(execution is not None and execution["operation_id"] == body["execution_operation_id"])
    execution = _source(ops.get, execution["operation_id"], steps=False, principal=principal)
    package, _ = _handoff(ops, principal, wid, execution)
    _proof(package["package_id"] == body["package_id"] and package["work_item_id"] == row["work_item_id"])
    bid = package["document"]["context"]["work_ref"]["binding_id"]
    if bid not in bindings:
        return None
    return {**base, "attempt_id": row["attempt_id"], "association": "proven", "binding_id": bid,
            "package_ref": _package_ref(package), "requirement_ids": _requirements(package),
            "execution_operation_id": execution["operation_id"]}


def preview(ops, principal, workspace_id, snapshot_id, from_revision_id, to_revision_id, binding_ids):
    if not principal.allows("observe"):
        raise OperationError("FORBIDDEN", "PM reads need observe", 403)
    for name, value, pattern in (("workspace_id", workspace_id, pm_workspace._WORKSPACE),
                                  ("snapshot_id", snapshot_id, pm_snapshots.SNAPSHOT_ID),
                                  ("from_revision_id", from_revision_id, pm_plans.REVISION_ID),
                                  ("to_revision_id", to_revision_id, pm_plans.REVISION_ID)):
        if not isinstance(value, str) or not pattern.fullmatch(value):
            raise OperationError("INVALID_PARAMS", f"{name} must be a canonical identifier", 422)
    if (not isinstance(binding_ids, list) or not 1 <= len(binding_ids) <= MAX_BINDINGS
            or any(not isinstance(b, str) or not pm_evidence._BINDING.fullmatch(b) for b in binding_ids)
            or len(set(binding_ids)) != len(binding_ids)):
        raise OperationError("INVALID_PARAMS", "Select 1-50 distinct immutable work binding IDs", 422)
    with _snapshot(ops.db):
        snapshot = _source(pm_snapshots.get_snapshot, ops, principal, workspace_id, snapshot_id)["snapshot"]
        if snapshot["visibility"]["filtered"]:
            raise _unavailable()
        for rid in (from_revision_id, to_revision_id):
            _source(pm_plans._require_revision_sources, ops.db, principal, workspace_id, rid)
        if snapshot["baseline"]["document_revision"] != from_revision_id:
            raise OperationError("PM_IMPACT_BASELINE_MISMATCH", "Select the saved snapshot's adopted baseline", 409)
        selected = [w for w in snapshot["work"] if (w.get("binding") or {}).get("binding_id") in binding_ids]
        if {w["binding"]["binding_id"] for w in selected} != set(binding_ids):
            raise OperationError("PM_IMPACT_BINDING_MISMATCH", "Select bindings from this saved snapshot", 409)
        cursor = snapshot["journal_cursor"]
        try:
            comparison = diff.compare(ops, principal, workspace_id, from_revision_id, to_revision_id,
                                      binding_ids=binding_ids, journal_cursor=cursor)["comparison"]
        except OperationError as error:
            if error.code == "PM_DIFF_TOO_LARGE":
                _bound(False)  # The comparison alone already exceeds the smaller matrix bound.
            raise
        groups = _candidates(ops, workspace_id, sorted({w["work_item_id"] for w in selected}), cursor, set(binding_ids))
        candidates = {c["binding"]["binding_id"]: c for c in comparison["impact_candidates"]}
        subjects = []
        for oid, events in sorted(groups.items()):
            operation = _source(ops.get, oid, steps=False, principal=principal)
            try:
                subject = _subject(ops, principal, workspace_id, set(binding_ids), operation, events)
            except (KeyError, TypeError, ValueError):
                _proof(False)
            if subject is not None:
                candidate = candidates.get(subject.get("binding_id"))
                requirements = set(subject.get("requirement_ids", ()))
                for key in ("direct_requirement_ids", "dependent_requirement_ids"):
                    subject[key] = sorted(requirements.intersection(candidate[key])) if candidate else []
                subjects.append(subject)
                _bound(len(subjects) <= MAX_SUBJECTS)
        matrix = {"selector": {"workspace_id": workspace_id, "snapshot_id": snapshot_id,
                              "from_revision_id": from_revision_id, "to_revision_id": to_revision_id,
                              "binding_ids": sorted(binding_ids)},
                  "snapshot_digest": snapshot["snapshot_digest"], "journal_cursor": cursor,
                  "comparison": comparison, "work": sorted(selected, key=lambda w: w["binding"]["binding_id"]),
                  "subjects": subjects, "coverage": {"work": "selected_bindings_only", "project_complete": False,
                      "unselected_work": "not_scanned", "history": "retained_events_only",
                      "snapshot": snapshot["coverage"], "undiscoverable_history": "creation_boundary_unproven",
                      "semantic_impact": "unassessed", "acceptance_verification": "not_performed",
                      "delivery_verification": "not_performed", "network_checked": False,
                      "cross_scope_issue_publication": "unsupported"},
                  "execution_authorized": False, "work_update_authorized": False}
        result = {"version": 1, "workspace_id": workspace_id, "matrix": matrix, "impact_digest": diff._digest(matrix)}
        _source(pm_source_lineage.require_document, ops.db, principal, result)
        # Check the ordinary wire encoding too, not just compact digest bytes.
        _bound(len(json.dumps(result, ensure_ascii=False, allow_nan=False).encode("utf-8")) <= MAX_BYTES)
        return result
