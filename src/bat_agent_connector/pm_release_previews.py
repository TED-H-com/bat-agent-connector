"""Explicitly requested read-only source choices; no background scan or authority token."""
from __future__ import annotations

import re
from urllib.parse import quote

from . import deployment_store, work_items
from . import pm_release_baselines as baselines
from . import pm_release_sources as sources
from .github import GitHubAmbiguous
from .operations import OperationError

FIELDS = {
    "pm_release_baseline_sources_list": {"workspace_id", "after", "limit"},
    "pm_release_baseline_releases_list": {"workspace_id", "project_binding_id", "repository", "page", "limit"},
    "pm_release_baseline_deployments_list": {"workspace_id", "project_binding_id", "repository", "after", "limit"},
    "pm_release_baseline_preview": {"workspace_id", "project_binding_id", "repository", "kind", "release_id", "deployment_id", "maintenance_branch"},
}


def _page(value, limit, *, minimum=0, maximum=2**63 - 1):
    if type(value) is not int or not minimum <= value <= maximum or type(limit) is not int or not 1 <= limit <= 20:
        raise baselines._bad("Use a bounded cursor/page and limit 1–20")


def _selection(ops, principal, wid, binding_id, repository):
    if (not isinstance(binding_id, str) or not re.fullmatch(r"pmpb_[0-9a-f]{32}", binding_id)
            or not isinstance(repository, str) or not 1 <= len(repository) <= 256
            or not sources.REPOSITORY.fullmatch(repository) or any(part in {".", ".."} for part in repository.split("/"))):
        raise baselines._bad("Select an exact configured repository and project association")
    with baselines._snapshot(ops.db):
        ws = baselines._member(ops, principal, wid)
        config = sources.configured(ops, repository)
        if repository != config["repository"]:
            raise baselines._bad("Use the configured repository spelling")
        project = baselines._project(ops, wid, binding_id, repository, active=True)
        return {"server_id": ws["server_id"], "project_binding": project, "config": config, "repository": repository}


def list_sources(ops, principal, workspace_id, *, after=0, limit=20):
    _page(after, limit)
    with baselines._snapshot(ops.db):
        baselines._member(ops, principal, workspace_id)
        result = {"workspace_id": workspace_id, "bindings": [], "next_cursor": None}
        rows = ops.db.execute("SELECT * FROM pm_project_bindings WHERE workspace_id=? AND sequence>? ORDER BY sequence LIMIT ?",
                              (workspace_id, after, limit + 1)).fetchall()
        cfg = ops.context.get("github_config")
        consumed = after
        for row in rows[:limit]:
            project = work_items._get_project(ops.db, row["project_id"])
            repositories = []
            for name in project["repositories"] if not project["archived"] else []:
                configured = cfg.repos.get(name.lower()) if cfg else None
                if configured is not None:
                    try:
                        sources.configured(ops, configured.repository)
                    except OperationError:
                        continue
                    repositories.append(configured.repository)
            if repositories:
                value = {"sequence": row["sequence"], "project_binding_id": row["binding_id"], "project_id": row["project_id"],
                         "project_name": project["name"], "repositories": sorted(set(repositories))}
                candidate = {**result, "bindings": [*result["bindings"], value], "next_cursor": row["sequence"]}
                if baselines._size(candidate) > baselines.MAX_READ_BYTES:
                    if consumed == after:
                        return baselines._bounded(candidate)
                    result["next_cursor"] = consumed
                    return baselines._bounded(result)
                result["bindings"].append(value)
            consumed = row["sequence"]
        result["next_cursor"] = consumed if len(rows) > limit else None
        return baselines._bounded(result)


async def _get(ops, path, check, *, query=None):
    check()
    try:
        status, result = await ops.context["github"].call("GET", path, query=query)
    except GitHubAmbiguous:
        raise sources.bad("Publication source read is temporarily unavailable; no selection was saved", "PM_BASELINE_READ_UNAVAILABLE", 503) from None
    check()
    if status != 200 or not isinstance(result, dict):
        raise sources.bad("Publication source could not be read")
    return result


def _checker(ops, principal, wid, binding_id, repository, selection, head=None):
    def check():
        if _selection(ops, principal, wid, binding_id, repository) != selection:
            raise sources.bad("The selected original repository association changed", "PM_BASELINE_CONTEXT_CHANGED")
        if head is not None:
            with baselines._snapshot(ops.db):
                if baselines._head(ops.db, wid) != head:
                    raise sources.bad("The primary baseline changed; preview the new current selection", "PM_BASELINE_VERSION_CONFLICT")
                if head["baseline_id"]:
                    baselines._access(ops, principal, wid, baselines._record(ops.db, wid, head["baseline_id"]))
    return check


async def list_releases(ops, principal, workspace_id, *, project_binding_id, repository, page=1, limit=20):
    _page(page, limit, minimum=1, maximum=10000)
    selected = _selection(ops, principal, workspace_id, project_binding_id, repository)
    check = _checker(ops, principal, workspace_id, project_binding_id, repository, selected)
    path = "/repos/" + "/".join(quote(part, safe="") for part in repository.split("/"))
    repo = await _get(ops, path, check)
    if (type(repo.get("id")) is not int or not 0 < repo["id"] < 2**63
            or str(repo.get("full_name", "")).lower() != repository.lower()):
        raise sources.bad("The configured repository identity is unavailable", "PM_BASELINE_REPOSITORY_CHANGED")
    response = await _get(ops, path + "/releases", check, query={"page": page, "per_page": limit})
    rows = response.get("items")
    if not isinstance(rows, list) or len(rows) > limit:
        raise sources.bad("The provider did not return a bounded release page")
    result = {"workspace_id": workspace_id, "project_binding_id": project_binding_id,
              "repository": repository, "repository_id": repo["id"], "releases": [],
              "next_page": page + 1 if len(rows) == limit and page < 10000 else None,
              "coverage": {"publication": "github_release_only", "package_registry": "not_assessed",
                           "page_bound_reached": page == 10000 and len(rows) == limit}}
    for value in rows:
        if not isinstance(value, dict) or value.get("draft") is not False:
            continue
        channel = "prerelease" if value.get("prerelease") is True else "stable"
        try:
            release_id = value.get("id")
            if type(release_id) is not int or not 0 < release_id < 2**63:
                continue
            metadata = sources._release(value, release_id, channel)
        except OperationError:
            continue
        result["releases"].append({key: metadata[key] for key in ("release_id", "tag", "published_at")} | {"channel": channel})
    after = await _get(ops, path, check)
    if after.get("id") != repo["id"] or after.get("full_name") != repo.get("full_name"):
        raise sources.bad("The configured repository changed during discovery", "PM_BASELINE_REPOSITORY_CHANGED")
    return baselines._bounded(result)


def list_deployments(ops, principal, workspace_id, *, project_binding_id, repository, after=0, limit=20):
    _page(after, limit)
    with baselines._snapshot(ops.db):
        selected = _selection(ops, principal, workspace_id, project_binding_id, repository)
        rows = ops.db.execute("""SELECT rowid AS sequence,deployment_id FROM deployments
            WHERE rowid>? AND lower(json_extract(recipe_snapshot,'$.repository'))=? ORDER BY rowid LIMIT ?""",
            (after, repository.lower(), limit + 1)).fetchall()
        result = {"workspace_id": workspace_id, "project_binding_id": project_binding_id, "repository": repository,
                  "deployments": [], "next_cursor": None, "visibility_filtered": False, "eligibility_filtered": False}
        consumed = after
        for row in rows[:limit]:
            dep = deployment_store.deployment(ops.db, deployment_id=row["deployment_id"])
            snap, identity = dep["recipe_snapshot"], dep["identity"]
            try:
                proof = sources.deployment_receipt(ops, principal, workspace_id, row["deployment_id"], repository=repository,
                    repository_id=snap.get("repository_id"), commit_sha=identity.get("source_sha"), channel=snap.get("environment"))
                if (proof["provider_digest"] != selected["config"]["provider_digest"]
                        or type(proof["repository_id"]) is not int or not 0 < proof["repository_id"] < 2**63
                        or not isinstance(proof["commit_sha"], str) or not sources.SHA.fullmatch(proof["commit_sha"])
                        or not isinstance(proof["channel"], str) or not 1 <= len(proof["channel"]) <= 256):
                    raise sources.bad("Original deployment source is not selectable")
            except OperationError as exc:
                result["visibility_filtered" if exc.status in {403, 404} else "eligibility_filtered"] = True
                consumed = row["sequence"]
                continue
            value = {key: proof[key] for key in ("deployment_id", "operation_id", "repository_id", "channel", "commit_sha", "checked_at")}
            value.update(sequence=row["sequence"], version={"kind": "commit", "value": proof["commit_sha"]})
            candidate = {**result, "deployments": [*result["deployments"], value], "next_cursor": row["sequence"]}
            if baselines._size(candidate) > baselines.MAX_READ_BYTES:
                if consumed == after:
                    return baselines._bounded(candidate)
                result["next_cursor"] = consumed
                return baselines._bounded(result)
            result["deployments"].append(value)
            consumed = row["sequence"]
        result["next_cursor"] = consumed if len(rows) > limit else None
        return baselines._bounded(result)


async def preview(ops, principal, workspace_id, *, project_binding_id, repository, kind,
                  release_id=None, deployment_id=None, maintenance_branch=None):
    if not isinstance(kind, str) or kind not in {"github_release", "deployment"}:
        raise baselines._bad("Select a supported publication source", "PM_BASELINE_SOURCE_UNSUPPORTED")
    if maintenance_branch is not None:
        sources.ref_name(maintenance_branch)
    if kind == "github_release":
        if type(release_id) is not int or not 0 < release_id < 2**63 or deployment_id is not None:
            raise baselines._bad("Select only the original native release ID")
        source = {"kind": kind, "release_id": release_id}
    else:
        if not isinstance(deployment_id, str) or not re.fullmatch(r"dep_[0-9a-f]{32}", deployment_id) or release_id is not None:
            raise baselines._bad("Select only the original deployment ID")
        source = {"kind": kind, "deployment_id": deployment_id}
    with baselines._snapshot(ops.db):
        selected = _selection(ops, principal, workspace_id, project_binding_id, repository)
        head = baselines._head(ops.db, workspace_id)
        if head["baseline_id"]:
            baselines._access(ops, principal, workspace_id, baselines._record(ops.db, workspace_id, head["baseline_id"]))
        intent = {**selected, "source": source, "repository_id": None, "commit_sha": None, "channel": None,
                  "maintenance_branch": maintenance_branch, "deployment_receipt": None,
                  "previous_baseline_id": head["baseline_id"], "previous_version": head["version"]}
        if kind == "deployment":
            dep = deployment_store.deployment(ops.db, deployment_id=deployment_id)
            if dep is None:
                raise sources.bad("Original deployment is unavailable", "PM_BASELINE_SOURCE_UNAVAILABLE", 404)
            proof = sources.deployment_receipt(ops, principal, workspace_id, deployment_id, repository=repository,
                repository_id=dep["recipe_snapshot"].get("repository_id"), commit_sha=dep["identity"].get("source_sha"),
                channel=dep["recipe_snapshot"].get("environment"))
            if proof["provider_digest"] != selected["config"]["provider_digest"]:
                raise sources.bad("The original deployment provider differs from this selection", "PM_BASELINE_REPOSITORY_CHANGED")
            intent.update({key: proof[key] for key in ("repository_id", "commit_sha", "channel")})
            intent["deployment_receipt"] = proof
    check_selection = _checker(ops, principal, workspace_id, project_binding_id, repository, selected, head)

    def check():
        check_selection()
        if kind == "deployment":
            baselines._access_context(ops, principal, workspace_id, intent)
    try:
        proof = await sources.observe(ops, intent, check, discover=kind == "github_release")
    except GitHubAmbiguous:
        raise sources.bad("Publication preview is temporarily unavailable; no selection was saved", "PM_BASELINE_READ_UNAVAILABLE", 503) from None
    fixed = proof["selection"]
    registration = {"action": baselines.ACTION, "target": {"workspace_id": workspace_id, "repository": repository},
        "params": {"project_binding_id": project_binding_id, "source": source, "channel": fixed["channel"],
                   "maintenance_branch": maintenance_branch, "known_limitations": [], "rationale": ""},
        "preconditions": {"expected_baseline_version": head["version"], "expected_previous_baseline_id": head["baseline_id"],
                          "expected_repository_id": fixed["repository_id"], "expected_commit_sha": fixed["commit_sha"]}}
    return baselines._bounded({"workspace_id": workspace_id, "proof": proof, "registration": registration,
                              "requires_input": ["rationale"], "maintenance_authorized": False})


async def read(ops, principal, method, params):
    if method not in FIELDS or not isinstance(params, dict) or set(params) - FIELDS[method]:
        raise baselines._bad("Use only the exact release source read parameters")
    if not principal.allows("observe"):
        raise baselines._bad("Publication source reads need observe", "FORBIDDEN", 403)
    wid = params.get("workspace_id")
    if method == "pm_release_baseline_sources_list":
        return list_sources(ops, principal, wid, after=params.get("after", 0), limit=params.get("limit", 20))
    selected = {key: params.get(key) for key in ("project_binding_id", "repository")}
    if method == "pm_release_baseline_releases_list":
        return await list_releases(ops, principal, wid, **selected, page=params.get("page", 1), limit=params.get("limit", 20))
    if method == "pm_release_baseline_deployments_list":
        return list_deployments(ops, principal, wid, **selected, after=params.get("after", 0), limit=params.get("limit", 20))
    return await preview(ops, principal, wid, **selected,
        **{key: params.get(key) for key in ("kind", "release_id", "deployment_id", "maintenance_branch")})
