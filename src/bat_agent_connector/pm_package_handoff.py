"""Assign one immutable engineering contract to an existing published start.

The execution substrate remains repository.continue. The selected implementation
base is an engineering decision, not a claim that the code implements the spec.
Only original effect reconciliation may proceed after source/scope withdrawal.
"""
from __future__ import annotations

import hashlib
import json

from . import pm_work_packages, pm_workspace, repository_sync, service
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


def _ready(ops, principal, ref):
    readiness = pm_work_packages.readiness(ops, principal, ref["workspace_id"], ref["package_id"])
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
    text = (user_prompt + "\n\nFixed engineering work package (authoritative for this attempt):\n"
            + canonical(contract) + "\n\nImplement only the selected adopted requirements. "
            "Unknowns remain unknown. Task checkboxes and implementation reports do not prove verification, "
            "merge or deployment. Do not replace these fixed sources with live PM files.\n")
    if len(text) > service.MAX_PROMPT_CHARS - 100 or len(text.encode("utf-8")) > MAX_CONTRACT_BYTES:
        raise _error("PM_HANDOFF_TOO_LARGE", "This complete engineering contract exceeds the bounded first prompt; select a smaller package", 413)
    return text


def admit(ops, principal, target, params, pre):
    if "pm_package_ref" not in params:
        if "pm_code_basis" in params:
            raise _error("INVALID_PARAMS", "A code-basis decision requires a fixed engineering package", 422)
        return None
    if not principal.allows("observe") or not principal.allows("manage"):
        raise _error("FORBIDDEN", "Engineering handoff requires observe and the existing linked-work manage scope", 403)
    ref, basis = _ref(params["pm_package_ref"]), _basis(params.get("pm_code_basis"))
    package = _package(ops, principal, ref, write=True)
    document = package["document"]
    _matches(document, target, params, pre)
    _ready(ops, principal, ref)
    contract = {"contract": CONTRACT, "package_ref": ref, "package": document,
                "code_basis": basis, "execution_assignment": "this_original_operation_only",
                "verification": "unassessed", "delivery": "not_observed"}
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
    _ready(ctx.service, principal, contract["package_ref"])


def execution_prompt(ctx):
    saved = (ctx.admission_binding or {}).get("pm_handoff")
    return prompt(ctx.params["prompt"], saved["contract"]) if saved else ctx.params["prompt"]
