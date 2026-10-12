"""Read-only, fixed-revision plan comparisons and explicit work-impact candidates.

Neither a textual difference nor an association proves semantic drift. No reads
of mutable work state, provider calls, plan adoption or work mutation occur here.
"""
from __future__ import annotations

import hashlib
import json

from . import pm_evidence, pm_plans, pm_profiles, pm_workspace
from .operations import OperationError

VERSION = 1
MAX_BINDINGS = 100
MAX_RESPONSE_BYTES = 2_000_000


def _json(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def _digest(value):
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _bad(code, message, status=422):
    return OperationError(code, message, status)


def _keyed(items, key):
    if not isinstance(items, list) or any(not isinstance(item, dict) or not isinstance(item.get(key), str)
                                         or not item[key] for item in items):
        raise _bad("PM_PLAN_CORRUPT", "The fixed plan contains invalid structured items", 409)
    result = {item[key]: item for item in items}
    if len(result) != len(items):
        raise _bad("PM_PLAN_CORRUPT", "The fixed plan contains duplicate structured IDs", 409)
    return result


def _collection(before, after, key):
    old, new = _keyed(before, key), _keyed(after, key)
    shared = sorted(old.keys() & new.keys())
    changed = [{key: value, "before": old[value], "after": new[value],
                "fields": sorted(field for field in old[value].keys() | new[value].keys()
                                 if old[value].get(field) != new[value].get(field))}
               for value in shared if old[value] != new[value]]
    return {"added": [new[value] for value in sorted(new.keys() - old.keys())],
            "removed": [old[value] for value in sorted(old.keys() - new.keys())],
            "changed": changed, "unchanged_ids": [value for value in shared if old[value] == new[value]],
            "order_changed": list(old) != list(new), "before_order": list(old), "after_order": list(new)}


def _creation_seq(db, *, resource_type, resource_id, kind, workspace_id, operation_id, key, value, cursor):
    rows = db.execute("""SELECT seq FROM api_events WHERE resource_type=? AND resource_id=? AND kind=?
        AND CASE WHEN json_valid(body) THEN json_extract(body,'$.operation_id') END=?
        AND CASE WHEN json_valid(body) THEN json_extract(body,'$.workspace_id') END=?
        AND CASE WHEN json_valid(body) THEN json_extract(body,?) END=? ORDER BY seq LIMIT 2""",
        (resource_type, resource_id, kind, operation_id, workspace_id, f"$.{key}", value)).fetchall()
    if len(rows) != 1:
        raise _bad("PM_DIFF_CURSOR_UNPROVEN", "A retained, unambiguous source creation event is required", 409)
    seq = rows[0][0]
    if seq > cursor:
        raise _bad("PM_DIFF_SOURCE_AFTER_CURSOR", "A selected source was created after this journal cursor", 409)
    return seq


def _fixed_revision(ops, workspace_id, revision_id, cursor):
    revision = pm_plans._revision(ops.db, workspace_id, revision_id)
    pm_plans.revision_files(ops.db, workspace_id, revision_id)
    document = revision["document"]
    if document.get("schema_version") != 1:
        raise _bad("PM_DIFF_SCHEMA_UNSUPPORTED", "This plan document version needs a matching comparison contract", 409)
    try:
        sources = _keyed(document["selected_inputs"], "input_id")
        _keyed(document["requirements"], "id")
        if set(document["sections"]) != set(pm_profiles.DOMAINS):
            raise ValueError
        for section in document["sections"].values():
            if section["statements"] != [sources[key] for key in section["source_input_ids"]]:
                raise ValueError
        for requirement in document["requirements"]:
            if requirement["source_input_id"] not in sources:
                raise ValueError
            _keyed(requirement["acceptance"], "source_input_id")
            if any(item["source_input_id"] not in sources for item in requirement["acceptance"]):
                raise ValueError
        scope = {"sections": {key: document["sections"][key] for key in ("governance", "scope", "quality")},
                 "requirements": document["requirements"]}
        if _digest(scope) != revision["scope_digest"]:
            raise ValueError
    except (KeyError, TypeError, ValueError):
        raise _bad("PM_PLAN_CORRUPT", "The fixed plan's references or structured digest are invalid", 409) from None
    seq = _creation_seq(ops.db, resource_type="pm_plan", resource_id=revision_id, kind="pm.plan.drafted",
        workspace_id=workspace_id, operation_id=revision["operation_id"], key="revision_id", value=revision_id, cursor=cursor)
    identity = {key: revision[key] for key in ("revision_id", "document_digest", "thread_id", "planning_draft_id",
        "previous_revision_id", "base_accepted_revision_id", "created_by", "created_at", "operation_id")}
    identity.update(structured_scope_digest=revision["scope_digest"], creation_event_seq=seq)
    return identity, document


def _text_change(before, after):
    """An exact linear-time replacement span; no Markdown or semantic inference."""
    changed = before != after
    out = {"changed": changed, "before_digest": _digest(before), "after_digest": _digest(after),
           "classification": "unclassified" if changed else "unchanged", "replacement": None}
    if changed:
        prefix, end = 0, 0
        bound = min(len(before), len(after))
        while prefix < bound and before[prefix] == after[prefix]:
            prefix += 1
        while end < bound - prefix and before[len(before) - end - 1] == after[len(after) - end - 1]:
            end += 1
        out["replacement"] = {"prefix_chars": prefix, "suffix_chars": end,
            "removed": before[prefix:len(before) - end], "added": after[prefix:len(after) - end]}
    return out


def _changes(before, after):
    requirements = _collection(before["requirements"], after["requirements"], "id")
    for item in requirements["changed"]:
        item["acceptance"] = _collection(item["before"]["acceptance"], item["after"]["acceptance"], "source_input_id")
    domains = {domain: {"changed": before["sections"][domain] != after["sections"][domain],
                       "before": before["sections"][domain], "after": after["sections"][domain]}
               for domain in pm_profiles.DOMAINS}
    sources = _collection(before["selected_inputs"], after["selected_inputs"], "input_id")
    old_sources = _keyed(before["selected_inputs"], "input_id")
    # Acceptance changes can reference an unchanged or unmapped source. Keep its
    # complete original attribution, not only a source ID requiring a later live read.
    sources["unchanged"] = [old_sources[key] for key in sources["unchanged_ids"]]
    return {"requirements": requirements, "sources": sources,
            "domains": domains,
            "title": {"changed": before["title"] != after["title"], "before": before["title"], "after": after["title"]},
            "markdown": _text_change(before["markdown"], after["markdown"]),
            "unclassified_sources": {"changed": before["unclassified_source_input_ids"] != after["unclassified_source_input_ids"],
                "before": before["unclassified_source_input_ids"], "after": after["unclassified_source_input_ids"]}}


def _requirement_impact(before, after, changes):
    direct = {item["id"] for field in ("added", "removed", "changed") for item in changes["requirements"][field]}
    # Explicit dependency IDs support candidates, not estimates of semantic or execution impact.
    dependents = {}
    for requirement in [*before["requirements"], *after["requirements"]]:
        for dependency in requirement["depends_on"]:
            dependents.setdefault(dependency, set()).add(requirement["id"])
    affected, pending = set(direct), list(direct)
    while pending:
        for rid in dependents.get(pending.pop(), ()):
            if rid not in affected:
                affected.add(rid)
                pending.append(rid)
    return direct, affected - direct


def _binding(ops, workspace_id, binding_id, cursor):
    row = ops.db.execute("SELECT * FROM pm_work_bindings WHERE workspace_id=? AND binding_id=?",
                         (workspace_id, binding_id)).fetchone()
    if row is None:
        raise _bad("PM_WORK_BINDING_NOT_FOUND", "Selected work binding not found in this workspace", 404)
    value = dict(row)
    try:
        requirements = json.loads(value["requirement_ids"])
        if (not isinstance(requirements, list) or not all(isinstance(item, str) for item in requirements)
                or len(requirements) != len(set(requirements))):
            raise ValueError
    except (TypeError, ValueError):
        raise _bad("PM_DIFF_BINDING_CORRUPT", "The saved work binding cannot be compared", 409) from None
    seq = _creation_seq(ops.db, resource_type="pm_evidence", resource_id=workspace_id, kind="pm.work.bind",
        workspace_id=workspace_id, operation_id=value["operation_id"], key="binding_id", value=binding_id, cursor=cursor)
    return {**value, "requirement_ids": requirements, "creation_event_seq": seq,
            "source_work_version": value["work_version"], "title": None, "title_status": "not_saved_in_binding",
            "work_state": "unknown", "association_basis": "explicit_owner_association", "execution_spec": "not_assigned"}


def _impact(binding, from_id, to_id, direct, dependent, changes, recorded_changes):
    selected, baseline = set(binding["requirement_ids"]), binding["plan_revision_id"]
    relation = "both_revisions" if baseline == from_id == to_id else "from_revision" if baseline == from_id else (
        "to_revision" if baseline == to_id else "other_revision" if baseline else "unassigned")
    reasons = []
    exact = relation in {"both_revisions", "from_revision", "to_revision"}
    matched = sorted(selected & direct) if exact else []
    indirect = sorted(selected & dependent) if exact else []
    if matched:
        reasons.append("associated_requirement_changed")
    if indirect:
        reasons.append("associated_requirement_dependency_changed")
    domain_changes = [key for key, value in changes["domains"].items() if value["changed"]]
    source_changes = any(changes["sources"][key] for key in ("added", "removed", "changed", "order_changed"))
    if exact and (domain_changes or changes["title"]["changed"] or changes["markdown"]["changed"]
                  or source_changes or changes["unclassified_sources"]["changed"]):
        reasons.append("plan_context_changed_impact_unclassified")
    if exact and changes["requirements"]["order_changed"]:
        reasons.append("requirement_order_or_membership_changed")
    if recorded_changes and (not exact or not selected):
        reasons.append("association_coverage_insufficient")
    return {"binding": binding, "baseline_relation": relation,
            "assessment": "review_candidate" if reasons else "no_identified_change",
            "reasons": reasons, "direct_requirement_ids": matched, "dependent_requirement_ids": indirect,
            "changed_domains": domain_changes if exact else [], "semantic_impact": "unassessed",
            "action": "review_existing_work", "work_update_authorized": False}


def compare(ops, principal, workspace_id, from_revision_id, to_revision_id, *, binding_ids=(), journal_cursor=None):
    """Compare explicit immutable revisions. Omitting cursor captures the current head once."""
    with ops.journal.tx():
        if not principal.allows("observe"):
            raise _bad("FORBIDDEN", "Plan comparison requires observe scope", 403)
        pm_workspace.require_member(ops.db, principal, workspace_id)
        for revision_id in (from_revision_id, to_revision_id):
            pm_plans._require_revision_sources(ops.db, principal, workspace_id, revision_id)
        if (not isinstance(binding_ids, (list, tuple)) or len(binding_ids) > MAX_BINDINGS
                or any(not isinstance(value, str) or not pm_evidence._BINDING.fullmatch(value) for value in binding_ids)
                or len(set(binding_ids)) != len(binding_ids)):
            raise _bad("INVALID_PARAMS", "Select at most 100 distinct immutable work binding IDs")
        head = ops.journal.api_head()
        cursor = head if journal_cursor is None else journal_cursor
        if type(cursor) is not int or not 0 <= cursor <= head:
            raise _bad("INVALID_PARAMS", "journal_cursor must be a nonnegative observed central event cursor")
        old, before = _fixed_revision(ops, workspace_id, from_revision_id, cursor)
        new, after = _fixed_revision(ops, workspace_id, to_revision_id, cursor)
        changes = _changes(before, after)
        direct, dependent = _requirement_impact(before, after, changes)
        recorded = any((before[key] != after[key]) for key in (
            "title", "markdown", "selected_inputs", "sections", "requirements", "unclassified_source_input_ids"))
        bindings = [_binding(ops, workspace_id, bid, cursor) for bid in sorted(binding_ids)]
        scope = {"structured_digest_changed": old["structured_scope_digest"] != new["structured_scope_digest"],
                 "document_identity_changed": old["document_digest"] != new["document_digest"],
                 "recorded_content_changed": recorded,
                 "semantic_impact": "unassessed" if recorded else "no_recorded_change", "coverage": "partial",
                 "prose_scope_impact": "unknown" if changes["markdown"]["changed"] or changes["title"]["changed"] else "unchanged"}
        fixed = {"from_revision": old, "to_revision": new, "changes": changes, "scope": scope}
        impact = [_impact(binding, from_revision_id, to_revision_id, direct, dependent, changes, recorded) for binding in bindings]
        comparison = {**fixed, "comparison_digest": _digest(fixed), "impact_candidates": impact,
            "context": {"server_id": pm_workspace.server_id(ops.db), "journal_cursor": cursor,
                        "binding_ids": sorted(binding_ids), "basis": "explicit_immutable_bindings"},
            "coverage": {"requirements": "structured_ids_only", "source_statuses_preserved": True,
                         "work": "selected_bindings_only", "unselected_work": "not_scanned",
                         "historical_mutable_work": "not_reconstructed", "latest_work_state": "not_read",
                         "source_creation": "verified_at_or_before_cursor", "semantic_impact": "unassessed",
                         "acceptance_verification": "not_performed", "delivery_evidence": "not_evaluated",
                         "cost": "unknown", "network_checked": False}, "execution_authorized": False}
        comparison["context_digest"] = _digest(comparison)
        result = {"version": VERSION, "workspace_id": workspace_id, "comparison": comparison}
        if len(_json(result).encode("utf-8")) > MAX_RESPONSE_BYTES:
            raise _bad("PM_DIFF_TOO_LARGE", "The complete comparison exceeds 2,000,000 UTF-8 bytes; no partial diff is returned")
        return result
