"""Bound, read-only release observations; no publication or mutable checkout access."""
from __future__ import annotations

import copy
import hashlib
import json
import re
import time
from datetime import datetime
from urllib.parse import quote

from . import deployment_store, pm_evidence
from .operations import OperationError

SHA = re.compile(r"[0-9a-f]{40}")
REPOSITORY = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")


def bad(message, code="PM_BASELINE_SOURCE_UNPROVEN", status=409):
    return OperationError(code, message, status)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def configured(ops, repository):
    cfg = ops.context.get("github_config")
    gh = ops.context.get("github")
    repo = cfg.repos.get(repository.lower()) if cfg else None
    if (not repo or repo.repository.lower() != repository.lower() or gh is None
            or gh.cfg.api_url != cfg.api_url):
        raise bad("Select an explicitly configured repository and read provider", "PM_BASELINE_REPOSITORY_UNAVAILABLE", 403)
    # No tokens, transport roots, provider URL, or credential references in public provenance.
    return {"repository": repo.repository, "provider_digest": digest({"api_url": cfg.api_url}),
            "config_digest": digest({"api_url": cfg.api_url, "repository": repo.repository})}


def ref_name(value):
    if (not isinstance(value, str) or not 1 <= len(value) <= 256 or value.startswith(("-", "/"))
            or value.endswith(("/", ".")) or any(c.isspace() or ord(c) < 32 or c in '~^:?*[\\' for c in value)
            or any(part in {"", ".", "..", "@"} or part.startswith(".") or part.endswith(".lock") for part in value.split("/"))
            or ".." in value or "@{" in value):
        raise bad("Select an exact valid Git reference name", "INVALID_PARAMS", 422)
    return value


def _sha(value):
    if not isinstance(value, str) or not SHA.fullmatch(value):
        raise bad("The provider did not prove a full fixed commit identity")
    return value


def _release(value, release_id, channel):
    published = value.get("published_at")
    try:
        if not isinstance(published, str) or not datetime.fromisoformat(published.replace("Z", "+00:00")).tzinfo:
            raise ValueError
    except ValueError:
        raise bad("A release must have an explicit publication timestamp") from None
    if (type(value.get("id")) is not int or value["id"] != release_id or value.get("draft") is not False
            or type(value.get("prerelease")) is not bool or value["prerelease"] != (channel == "prerelease")):
        raise bad("The actual published release and selected stable/prerelease channel do not match")
    return {"release_id": value["id"], "tag": ref_name(value.get("tag_name")),
            "draft": False, "prerelease": value["prerelease"], "published_at": published}


def deployment_receipt(ops, principal, wid, deployment_id, *, repository, repository_id, commit_sha, channel):
    if (type(repository_id) is not int or not 0 < repository_id < 2**63
            or not isinstance(commit_sha, str) or not SHA.fullmatch(commit_sha)
            or not isinstance(channel, str) or not 1 <= len(channel) <= 256):
        raise bad("The original deployment identity is incomplete")
    dep = deployment_store.deployment(ops.db, deployment_id=deployment_id)
    if dep is None or not pm_evidence._read_operation(ops, principal, wid, dep["operation_id"]):
        raise bad("The original deployment source is unavailable", "PM_BASELINE_SOURCE_UNAVAILABLE", 404)
    result = dep.get("recorded_result")
    snap = dep["recipe_snapshot"]
    evidence = (result or {}).get("evidence") or {}
    runtime = evidence.get("runtime") or {}
    observed = runtime.get("observed") or {}
    identity = (result or {}).get("identity") or {}
    # The original recorded_result is committed with the deployment's verified fact
    # by deployment.record. Merely completed Actions/jobs or health-only recipes do
    # not prove which product version reached the runtime.
    if (dep.get("verified") is not True or dep["provider_terminal"] is not True or not isinstance(result, dict)
            or snap.get("repository", "").lower() != repository.lower() or snap.get("repository_id") != repository_id
            or result.get("repository", "").lower() != repository.lower()
            or result.get("deployment_id") != deployment_id or result.get("generation") != dep["generation"]
            or snap.get("environment") != channel or result.get("environment") != channel
            or result.get("source_sha") != commit_sha or identity.get("source_sha") != commit_sha
            or dep["identity"] != identity or runtime.get("version_checked") is not True
            or observed.get("source_sha") != commit_sha or observed.get("repository_id") != repository_id
            or observed.get("environment") != channel or not evidence.get("provider")
            or type(runtime.get("health_checked")) is not bool
            or runtime["health_checked"] and observed.get("healthy") is not True):
        raise bad("The original receipt does not prove this repository, environment and deployed commit")
    return {"operation_id": dep["operation_id"], "deployment_id": deployment_id,
            "receipt_digest": digest(result), "provider_digest": digest({"api_url": snap.get("provider_origin")}),
            "repository_id": repository_id, "commit_sha": commit_sha, "channel": channel,
            "generation": dep["generation"], "run_id": result.get("run_id"), "run_attempt": result.get("run_attempt"),
            "version_checked": True, "health_checked": runtime["health_checked"],
            "checked_at": runtime.get("checked_at"), "artifact_id": identity.get("artifact_id"),
            "artifact_digest": identity.get("artifact_digest"), "current_environment_status": "not_rechecked"}


async def observe(ops, intent, check, *, discover=False):
    """One bounded source-read step. The callback rechecks original authority per GET."""
    if discover:
        # Only the read-only preview calls this with unspecified native identities.
        # Registration always supplies the original required SHA/ID/channel.
        if intent["source"]["kind"] != "github_release" or any(intent[key] is not None for key in ("repository_id", "commit_sha", "channel")):
            raise bad("Discovery requires an unselected GitHub release preview")
        intent = copy.deepcopy(intent)
    gh = ops.context["github"]
    repository = intent["repository"]
    base = "/repos/" + "/".join(quote(part, safe="") for part in repository.split("/"))

    async def read(path):
        check()
        status, value = await gh.call("GET", base + path)
        check()
        if status != 200 or not isinstance(value, dict):
            raise bad("The fixed provider source could not be read")
        return value

    async def repository_identity():
        value = await read("")
        if (type(value.get("id")) is not int or value["id"] <= 0
                or intent["repository_id"] is not None and value["id"] != intent["repository_id"]
                or str(value.get("full_name", "")).lower() != repository.lower()):
            raise bad("The configured repository identity changed", "PM_BASELINE_REPOSITORY_CHANGED")
        if discover and intent["repository_id"] is None:
            intent["repository_id"] = value["id"]

    await repository_identity()
    source = intent["source"]
    proof = None
    if source["kind"] == "github_release":
        path = "/releases/" + str(source["release_id"])
        raw_release = await read(path)
        if discover:
            intent["channel"] = "prerelease" if raw_release.get("prerelease") is True else "stable"
        release = _release(raw_release, source["release_id"], intent["channel"])
        refpath = "/git/ref/tags/" + quote(release["tag"], safe="")
        ref = await read(refpath)
        if ref.get("ref") != "refs/tags/" + release["tag"]:
            raise bad("The release's exact tag reference is unavailable")
        obj = ref.get("object") or {}
        chain, seen = [], set()
        for _ in range(16):
            sha = _sha(obj.get("sha"))
            kind = obj.get("type")
            if sha in seen or kind not in {"tag", "commit"}:
                raise bad("The original tag chain cannot be resolved to a unique commit")
            seen.add(sha)
            chain.append({"type": kind, "sha": sha})
            if kind == "commit":
                break
            tag = await read("/git/tags/" + sha)
            if tag.get("sha") != sha:
                raise bad("The original annotated tag object changed")
            obj = tag.get("object") or {}
        else:
            raise bad("The release tag chain exceeds the supported bound")
        if discover:
            intent["commit_sha"] = sha
        if sha != intent["commit_sha"]:
            raise bad("The release tag resolves to a different commit", "PM_BASELINE_TAG_CHANGED")
        commit = await read("/git/commits/" + sha)
        if commit.get("sha") != sha:
            raise bad("The resolved tag does not identify the selected Git commit")
        proof = {"kind": "github_release", **release, "resolved_commit_sha": sha, "tag_chain": chain,
                 "target_commitish_used": False, "publication_status": "published"}
    else:
        proof = {"kind": "deployment", **intent["deployment_receipt"], "publication_status": "deployed"}

    branch = intent["maintenance_branch"]
    maintenance = {"status": "unconfigured"}
    if branch is not None:
        refpath_branch = "/git/ref/heads/" + quote(branch, safe="")
        before = await read(refpath_branch)
        obj = before.get("object") or {}
        if before.get("ref") != "refs/heads/" + branch or obj.get("type") != "commit":
            raise bad("The selected maintenance branch is unavailable")
        branch_sha = _sha(obj.get("sha"))
        relation = await read("/compare/" + intent["commit_sha"] + "..." + branch_sha)
        if ((relation.get("merge_base_commit") or {}).get("sha") != intent["commit_sha"]
                or (relation.get("base_commit") or {}).get("sha") != intent["commit_sha"]
                or relation.get("status") not in {"ahead", "identical"}
                or branch_sha == intent["commit_sha"] and relation["status"] != "identical"
                or branch_sha != intent["commit_sha"] and relation["status"] != "ahead"):
            raise bad("The branch does not contain the selected release commit", "PM_BASELINE_BRANCH_UNRELATED")
        after = await read(refpath_branch)
        if after.get("ref") != before["ref"] or after.get("object") != before["object"]:
            raise bad("The maintenance branch changed during verification", "PM_BASELINE_BRANCH_CHANGED")
        maintenance = {"status": "verified_at_observation", "ref": before["ref"], "head_sha": branch_sha,
                       "relation": relation["status"], "baseline_commit_sha": intent["commit_sha"],
                       "execution_authorized": False}
    if source["kind"] == "github_release":
        after_ref = await read(refpath)
        after_release = _release(await read(path), source["release_id"], intent["channel"])
        if after_ref.get("ref") != ref["ref"] or after_ref.get("object") != ref["object"] or after_release != release:
            raise bad("The published release/tag changed during verification", "PM_BASELINE_TAG_CHANGED")
    await repository_identity()
    check()
    return {"source": proof, "maintenance_branch": maintenance, "observed_at": time.time(),
            "intent_digest": digest(intent), "observation_scope": "fixed_original_source_at_observation",
            "selection": {key: intent[key] for key in ("repository_id", "commit_sha", "channel")}}
