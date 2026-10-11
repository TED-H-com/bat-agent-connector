"""Bounded engineering descriptions; declarations never become delivery evidence."""
from __future__ import annotations

import copy
import re

from . import pm_plans, pm_spec_source_format, pm_worktree
from .operations import OperationError

MAX_ITEMS = 100
KEY = re.compile(r"[a-z][a-z0-9_.-]{0,79}")
SOURCE = re.compile(r"pmssrc_[0-9a-f]{64}")
EVIDENCE = re.compile(r"pme_[0-9a-f]{32}")
FIELDS = {"title", "requirements", "source_refs", "code_source", "design", "tasks", "verification", "delivery", "rationale"}
OPTIONAL = {"previous_package_id", "evidence_refs", "dod_revision_id", "unresolved"}


def bad(message):
    return OperationError("INVALID_PARAMS", message, 422)


def exact(value, required, optional=()):
    if not isinstance(value, dict) or not set(required) <= set(value) or set(value) - set(required) - set(optional):
        raise bad("Supply the exact work package fields")


def text(value, label, maximum=4000):
    return pm_plans._text(value, label, maximum)


def rows(value, label, *, maximum=MAX_ITEMS, minimum=0):
    if not isinstance(value, list) or not minimum <= len(value) <= maximum:
        raise bad(f"{label} must contain {minimum}–{maximum} records")
    return value


def ids(value, label, *, pattern=KEY, maximum=MAX_ITEMS, minimum=0):
    rows(value, label, maximum=maximum, minimum=minimum)
    if any(not isinstance(v, str) or not pattern.fullmatch(v) for v in value) or len(set(value)) != len(value):
        raise bad(f"{label} must contain distinct fixed identifiers")
    return list(value)


def sources(values):
    result, paths = [], set()
    for value in rows(values, "source_refs", maximum=20):
        exact(value, ("repository", "commit_sha", "path", "source_id", "sha256"))
        if (pm_spec_source_format.describe_path(value["path"]) is None
                or not isinstance(value["repository"], str) or not 1 <= len(value["repository"]) <= 256
                or not isinstance(value["commit_sha"], str) or not re.fullmatch(r"[0-9a-f]{40}", value["commit_sha"])
                or not isinstance(value["source_id"], str) or not SOURCE.fullmatch(value["source_id"])
                or not isinstance(value["sha256"], str) or not pm_plans.DIGEST.fullmatch(value["sha256"])
                or value["path"] in paths):
            raise bad("Select distinct fixed OpenSpec source IDs, paths and byte digests")
        paths.add(value["path"])
        result.append(copy.deepcopy(value))
    return result


def evidence_refs(values):
    result, seen = [], set()
    for value in rows(values, "evidence_refs", maximum=30):
        exact(value, ("evidence_id", "fact_digest"))
        if (not isinstance(value["evidence_id"], str) or not EVIDENCE.fullmatch(value["evidence_id"])
                or value["evidence_id"] in seen or not isinstance(value["fact_digest"], str)
                or not pm_plans.DIGEST.fullmatch(value["fact_digest"])):
            raise bad("Select distinct original evidence IDs and fact digests")
        result.append(copy.deepcopy(value))
        seen.add(value["evidence_id"])
    return result


def _source_ids(value, known):
    selected = ids(value, "source_ids", pattern=SOURCE, maximum=20)
    if set(selected) - known:
        raise bad("Engineering references must name selected fixed source documents")
    return selected


def _requirements(value, selected):
    result = ids(value, "requirement_ids", minimum=1)
    if set(result) - selected:
        raise bad("Engineering work must refer to the selected adopted requirements")
    return result


def design(value, known):
    exact(value, ("status", "text", "source_ids"))
    if value["status"] not in ("described", "not_applicable", "unresolved"):
        raise bad("Design is described, explicitly not_applicable, or unresolved")
    return {"status": value["status"], "text": text(value["text"], "design / tailoring rationale", 128_000),
            "source_ids": _source_ids(value["source_ids"], known), "basis": "caller_engineering_description"}


def tasks(values, requirements, known):
    result = []
    for value in rows(values, "tasks", minimum=1):
        exact(value, ("key", "description", "requirement_ids", "depends_on", "source_ids"))
        key = ids([value["key"]], "technical task key")[0]
        result.append({"key": key, "description": text(value["description"], "technical task"),
            "requirement_ids": _requirements(value["requirement_ids"], requirements),
            "depends_on": ids(value["depends_on"], "task dependencies"),
            "source_ids": _source_ids(value["source_ids"], known),
            "status": "planned", "verification": "unassessed", "counts_as_done": False})
    keys = {v["key"] for v in result}
    if len(keys) != len(result) or any(set(v["depends_on"]) - keys for v in result):
        raise bad("Technical task keys must be unique and dependencies must name this package's tasks")
    edges = {v["key"]: v["depends_on"] for v in result}
    active, done = set(), set()

    def visit(key):
        if key in active:
            raise bad("Technical task dependency cycle")
        if key in done:
            return
        active.add(key)
        for dependency in edges[key]:
            visit(dependency)
        active.remove(key)
        done.add(key)

    for key in keys:
        visit(key)
    return result


def verification(values, requirements, known):
    result, seen = [], set()
    for value in rows(values, "verification", minimum=1):
        exact(value, ("key", "requirement_id", "acceptance_input_ids", "method", "expected_evidence", "source_ids"))
        key = ids([value["key"]], "verification key")[0]
        rid = value["requirement_id"]
        if not isinstance(rid, str) or rid not in requirements or key in seen:
            raise bad("Verification keys must be distinct and map to selected requirements")
        seen.add(key)
        acceptance = {item["source_input_id"] for item in requirements[rid]["acceptance"]}
        refs = ids(value["acceptance_input_ids"], "acceptance_input_ids", pattern=re.compile(r"pmi_[0-9a-f]{32}"))
        if set(refs) - acceptance:
            raise bad("Acceptance references must belong to this exact adopted requirement")
        result.append({"key": key, "requirement_id": rid, "acceptance_input_ids": refs,
            "method": text(value["method"], "verification method"),
            "expected_evidence": text(value["expected_evidence"], "expected verification evidence"),
            "source_ids": _source_ids(value["source_ids"], known), "status": "planned", "verification": "not_run"})
    return result


def delivery(value, code):
    aspects = ("migration", "rollout", "rollback", "observation")
    exact(value, ("repository", "repository_id", "base_ref", "goal", *aspects), ("pull_number",))
    if value["repository"] != code["repository"] or type(value["repository_id"]) is not int or value["repository_id"] != code["repository_id"]:
        raise bad("The delivery target must name the exact selected code repository")
    from . import repository_sync
    repository_sync.check_ref(value["base_ref"])
    if value["goal"] not in ("reviewable_changes", "pull_request", "integration_pr"):
        raise bad("Choose reviewable_changes, pull_request or a fixed integration_pr delivery goal")
    if (value["goal"] == "integration_pr") != ("pull_number" in value):
        raise bad("Only an integration_pr goal requires an existing pull_number")
    if "pull_number" in value and (type(value["pull_number"]) is not int or value["pull_number"] <= 0):
        raise bad("pull_number must be a positive integer")
    plans = {}
    for aspect in aspects:
        item = value[aspect]
        exact(item, ("status", "text"))
        if item["status"] not in ("described", "not_applicable", "unresolved"):
            raise bad("Delivery aspects need an explicit description, tailoring rationale or unresolved state")
        plans[aspect] = {"status": item["status"], "text": text(item["text"], aspect)}
    return {**copy.deepcopy(value), **plans, "status": "intent_only", "target_verified": False,
            "merge_authorized": False, "deploy_authorized": False}


def descriptions(ops, params, selected_requirements):
    exact(params, FIELDS, OPTIONAL)
    code = copy.deepcopy(params["code_source"])
    pm_worktree.validate_selection(ops, code)
    selected = ids(params["requirements"], "requirements", minimum=1)
    if set(selected) - set(selected_requirements):
        raise bad("Select requirements from this work's fixed adopted plan association")
    requirements = {rid: selected_requirements[rid] for rid in selected}
    refs = sources(params["source_refs"])
    if any(ref["repository"] != code["repository"] for ref in refs):
        raise bad("Specification and code sources must name the same repository")
    known = {v["source_id"] for v in refs}
    unresolved = [text(value, "unresolved engineering question") for value in rows(params.get("unresolved", []), "unresolved", maximum=30)]
    return {"title": text(params["title"], "title", 200), "requirements": list(requirements.values()),
            "source_refs": refs, "code_source": code, "design": design(params["design"], known),
            "tasks": tasks(params["tasks"], set(selected), known), "verification": verification(params["verification"], requirements, known),
            "delivery": delivery(params["delivery"], code), "unresolved": unresolved,
            "rationale": text(params["rationale"], "rationale"), "evidence_refs": evidence_refs(params.get("evidence_refs", []))}
