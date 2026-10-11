"""Assign one immutable engineering contract to an existing published start.

The execution substrate remains repository.continue. The selected implementation
base is an engineering decision, not a claim that the code implements the spec.
Only original effect reconciliation may proceed after source/scope withdrawal.
"""
from __future__ import annotations

import hashlib
import json

from . import artifacts, pm_work_packages, pm_workspace, repository_sync, service
from .api_auth import Principal
from .operations import OperationError

CONTRACT = "pm-engineering-handoff/v1"
MODE = "implement_fixed_spec_from_selected_base"
MAX_CONTRACT_BYTES = 64_000


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def _error(code, message, status=409):
    return OperationError(code, message, status)


def _ref(value):
    if (not isinstance(value, dict) or set(value) != {"workspace_id", "package_id", "document_digest"}
            or not isinstance(value["workspace_id"], str)
            or not isinstance(value["package_id"], str) or not pm_work_packages.PACKAGE.fullmatch(value["package_id"])
            or not isinstance(value["document_digest"], str) or not repository_sync.DIGEST.fullmatch(value["document_digest"])):
        raise _error("INVALID_PARAMS", "Select an exact workspace, immutable package and document digest", 422)
    return dict(value)


def _basis(value):
    if (not isinstance(value, dict) or set(value) != {"mode", "rationale"} or value["mode"] != MODE
            or not isinstance(value["rationale"], str) or not value["rationale"].strip()
            or len(value["rationale"]) > 4000):
        raise _error("INVALID_PARAMS", "State why this fixed implementation base is selected", 422)
    return {**value, "compatibility": "unverified", "scope_change_authorized": False}


def _package(ops, principal, ref, *, write=False):
    pm_workspace.require_member(ops.db, principal, ref["workspace_id"], write=write)
    package = pm_work_packages.get_package(ops, principal, ref["workspace_id"], ref["package_id"])["package"]
    if package["document_digest"] != ref["document_digest"]:
        raise _error("PM_PACKAGE_VERSION_CONFLICT", "The selected engineering package digest changed")
    return package


def _ready(ops, principal, ref, quality_refs=None):
    readiness = pm_work_packages.readiness(ops, principal, ref["workspace_id"], ref["package_id"], quality_refs=quality_refs)
    # Alignment here asks whether the code already shares the spec's commit.
    # The explicit basis decision authorizes implementing the fixed spec from a
    # distinct base, without claiming semantic compatibility or verified quality.
    blocked = [row["check"] for row in readiness["checks"]
               if row["status"] != "passed" and row["check"] != "spec_code_alignment"]
    if blocked:
        raise _error("PM_PACKAGE_NOT_READY", "The fixed package has unresolved handoff checks: " + ", ".join(blocked))


def _matches(document, target, params, pre):
    code = document["code_source"]
    expected = {**target, "source_ref": params["source_ref"], "source_sha": params["source_sha"],
                "repository_id": pre["repository_id"], "binding_digest": pre["binding_digest"]}
    work = document["context"]["work_ref"]
    if (code != expected or params.get("work_item_id") != work["work_item_id"]
            or params.get("project_id") != work["project_id"]
            or pre.get("expected_work_item_fingerprint") != work["work_fingerprint"]):
        raise _error("PM_PACKAGE_TARGET_CHANGED", "Use this package's fixed code base and explicit work association")
    if any(ref["repository"].lower() != code["repository"].lower() for ref in document["source_refs"]):
        raise _error("PM_PACKAGE_SOURCE_MISMATCH", "Specifications and code must belong to the same bound repository")
    git = document["context"]["git_binding"]
    if git is not None and git["repository_id"] != code["repository_id"]:
        raise _error("PM_PACKAGE_SOURCE_MISMATCH", "The original numeric repository identities differ")


def prompt(user_prompt, contract):
    """Keep the full authoritative package; never truncate a requirement or source."""
    presentation = contract
    if "spec_materialization" in contract:
        presentation = {key: value for key, value in contract.items() if key != "package"}
        presentation = {**presentation, "package_location": contract["spec_materialization"]["input_path"],
            "instruction": "Read the entire JSON specification artifact before implementing. Its package field is the complete authoritative engineering package; tasks includes stable identities and removed-key history. Do not replace missing bytes with guesses or a live PM path."}
    text = (user_prompt + "\n\nFixed engineering work package (authoritative for this attempt):\n"
            + canonical(presentation) + "\n\nImplement only the selected adopted requirements. "
            "Unknowns remain unknown. Task checkboxes and implementation reports do not prove verification, "
            "merge or deployment. Do not replace these fixed sources with live PM files.\n")
    if len(text) > service.MAX_PROMPT_CHARS - 100 or len(text.encode("utf-8")) > MAX_CONTRACT_BYTES:
        raise _error("PM_HANDOFF_TOO_LARGE", "This complete engineering contract exceeds the bounded first prompt; materialize it as a fixed spec artifact or select a smaller package", 413)
    return text


def admit(ops, principal, target, params, pre):
    if "pm_package_ref" not in params:
        if "pm_code_basis" in params or "pm_spec_ref" in params or "pm_requirement_quality_refs" in params:
            raise _error("INVALID_PARAMS", "A code-basis decision requires a fixed engineering package", 422)
        return None
    if not principal.allows("observe") or not principal.allows("manage"):
        raise _error("FORBIDDEN", "Engineering handoff requires observe and the existing linked-work manage scope", 403)
    ref, basis = _ref(params["pm_package_ref"]), _basis(params.get("pm_code_basis"))
    package = _package(ops, principal, ref, write=True)
    document = package["document"]
    _matches(document, target, params, pre)
    _ready(ops, principal, ref, params.get('pm_requirement_quality_refs'))
    contract = {"contract": CONTRACT, "package_ref": ref, "package": document,
                "code_basis": basis, "execution_assignment": "this_original_operation_only",
                "verification": "unassessed", "delivery": "not_observed"}
    if 'pm_requirement_quality_refs' in params:
        contract['pm_requirement_quality_refs'] = params['pm_requirement_quality_refs']
    if "pm_spec_ref" in params:
        contract["spec_materialization"] = _materialized(ops, principal, params["pm_spec_ref"], ref, params.get("artifacts", []))
    prompt(params["prompt"], contract)
    return {"pm_handoff": {"contract": contract, "contract_digest": digest(contract),
                            "principal": pm_workspace.principal_binding(principal)},
            "pm_lineage": {"workspace_id": ref["workspace_id"]}}


def binding(operation):
    return ((operation.get("external_refs") or {}).get("admission_binding") or {}).get("pm_handoff")


def source_visible(db, principal, original):
    """Original sources only: a newer adopted plan does not rewrite old visibility."""
    saved = binding(original)
    if not saved:
        return False
    try:
        contract = saved["contract"]
        ref = _ref(contract["package_ref"])
        record = pm_work_packages._record(db, ref["workspace_id"], ref["package_id"])
        return (digest(contract) == saved["contract_digest"]
                and record["document_digest"] == ref["document_digest"]
                and contract["package"] == record["document"]
                and _dependencies_visible(db, principal, record, contract)
                and _spec_visible(db, principal, contract)
                and pm_work_packages.operation_visible(db, principal, {"operation_id": record["operation_id"]}))
    except (OperationError, TypeError, KeyError, ValueError):
        return False


def guard(ctx):
    saved = (ctx.admission_binding or {}).get("pm_handoff")
    if saved is None:
        return
    caller = saved["principal"]
    principal = Principal(caller["actor"], frozenset(caller["scopes"]), admin=caller["admin"])
    if not source_visible(ctx.service.db, principal, ctx.op):
        raise _error("PM_HANDOFF_SOURCE_UNAVAILABLE", "The original engineering sources are no longer available", 404)
    contract = saved["contract"]
    package = _package(ctx.service, principal, contract["package_ref"], write=True)
    _matches(package["document"], ctx.target, ctx.params, ctx.preconditions)
    _ready(ctx.service, principal, contract["package_ref"], contract.get('pm_requirement_quality_refs'))
    if "spec_materialization" in contract:
        current = _materialized(ctx.service, principal, ctx.params["pm_spec_ref"], contract["package_ref"], ctx.params.get("artifacts", []))
        if current != contract["spec_materialization"]:
            raise _error("PM_SPEC_CHANGED", "The original spec artifact binding changed")


def execution_prompt(ctx):
    saved = (ctx.admission_binding or {}).get("pm_handoff")
    return prompt(ctx.params["prompt"], saved["contract"]) if saved else ctx.params["prompt"]


def _materialized(ops, principal, selected, package_ref, refs):
    from . import pm_spec_materialization as specs
    if (not isinstance(selected, dict) or set(selected) != {"workspace_id", "materialization_id", "document_digest"}
            or selected["workspace_id"] != package_ref["workspace_id"]):
        raise _error("INVALID_PARAMS", "Select an exact materialization in this package workspace", 422)
    value = specs.get_materialization(ops, principal, selected["workspace_id"], selected["materialization_id"])["materialization"]
    if (value["document_digest"] != selected["document_digest"] or value["document"]["package_ref"] != package_ref):
        raise _error("PM_SPEC_CHANGED", "The selected spec does not match the fixed package and digest")
    artifact_ref = value["artifact_ref"]
    if artifact_ref not in refs:
        raise _error("PM_SPEC_ARTIFACT_REQUIRED", "Include the exact spec artifact revision in this handoff's attachments", 422)
    metadata = artifacts.get(ops.db, artifact_ref["artifact_id"], artifact_ref["revision"], principal=principal)
    if (metadata["state"] != "ready" or metadata["operation_id"] != value["operation_id"]
            or metadata["digest"] != value["document_digest"]
            or ops.context["artifact_store"].read_content(artifact_ref["artifact_id"], artifact_ref["revision"]) != canonical(value["document"]).encode()):
        raise _error("PM_SPEC_ARTIFACT_CHANGED", "The original frozen spec bytes cannot be verified")
    return {**selected, "artifact_ref": artifact_ref, "operation_id": value["operation_id"],
            "git_commit_sha": value["git_receipt"]["commit_sha"],
            "input_path": ".batc-inputs/" + artifact_ref["artifact_id"] + "-r" + str(artifact_ref["revision"]) + "/" + artifacts.safe_name(metadata["display_name"])}


def _spec_visible(db, principal, contract):
    if "spec_materialization" not in contract:
        return True
    from . import pm_spec_materialization as specs
    fixed = contract["spec_materialization"]
    value = specs._record(db, fixed["workspace_id"], fixed["materialization_id"])
    return (value["document_digest"] == fixed["document_digest"] and value["artifact_ref"] == fixed["artifact_ref"]
            and value["operation_id"] == fixed["operation_id"] and value["document"]["package_ref"] == contract["package_ref"]
            and specs.source_visible(db, principal, {"operation_id": value["operation_id"]}))


def _dependencies_visible(db, principal, package, contract):
    if 'pm_requirement_quality_refs' not in contract:
        return True
    from .pm_quality_reviews import _Reader
    from .pm_requirement_acceptance import access_dependencies
    access_dependencies(_Reader(db), principal, package, contract['pm_requirement_quality_refs'])
    return True
