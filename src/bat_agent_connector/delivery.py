"""GitHub delivery operations: merge a PR at an expected head, deploy a fixed version, or both.

These are deterministic backend actions (plan §15-§18): the Dashboard button and an agent with the right scopes
run the same handler, no LLM is involved, and a task id is not required. Every write is a recorded step; a lost
reply is settled by reading GitHub back. Merges use the asynchronous merge API, so a merge queue is waited on as
``waiting_external`` and never reported as merged until the PR itself is merged. A deploy is only ``succeeded``
when its fixed run attempt, deploy job, environment and runtime check all provide evidence.
"""

from __future__ import annotations

import json
import re
import time

from . import deployment, pr_delivery
from .api_auth import Principal
from .config import GitHubConfig
from .github import GitHubAmbiguous, GitHubClient
from .operations import (
    RERUN,
    TERMINAL,
    ActionDef,
    NeedsAttention,
    OpContext,
    OperationError,
    OperationService,
    Wait,
)

HEX40 = re.compile(r"^[0-9a-f]{40}$")
PENDING_RUN = {"queued", "in_progress", "waiting", "requested", "pending"}


def _gh(ops: OperationService) -> GitHubClient:
    client = ops.context.get("github")
    if client is None:
        raise OperationError("GITHUB_NOT_CONFIGURED", "set [github] token_ref to use delivery actions", 503)
    return client


def _cfg(ops: OperationService) -> GitHubConfig:
    return ops.context["github_config"]


async def _read(coro, what: str, ctx: OpContext | None = None):
    """An unanswered read waits; a refused read is classified against this operation's sent writes.

    A refused read (401 expired token, 403, 404) fails the operation only while it has sent nothing. Once a merge
    request or dispatch is out, GitHub may still be merging or deploying, so the operation waits for a person
    instead: ``failed`` would hide that and release the recipe's deploy lock.
    """
    try:
        status, body = await coro
    except GitHubAmbiguous:
        raise Wait("waiting_external", f"GitHub did not answer ({what}); retrying", 30) from None
    return _read_result(status, body, what, ctx)


def _sent_write(ctx: OpContext) -> bool:
    # Part A also records read-only plan/verify steps; their presence does not mean a write was sent.
    return ctx.service.db.execute("SELECT 1 FROM operation_steps WHERE operation_id=? "
                                  "AND (name LIKE '%.submit' OR name LIKE '%.dispatch' OR name='pr.metadata.write')",
                                  (ctx.operation_id,)).fetchone() is not None


def _read_result(status: int, body: dict, what: str, ctx: OpContext | None = None):
    """Share refused-read classification with named read steps without swallowing ambiguous replies."""
    if status != 200:
        message = f"{what}: {str(body.get('message') or status)[:200]}"
        if ctx is not None and _sent_write(ctx):
            raise NeedsAttention(f"GITHUB_{status}", message + "; a write was already sent, so check GitHub, fix "
                                 "the token or permission, then resume")
        raise OperationError(f"GITHUB_{status}", message)
    return body


def _check_wait(ctx: OpContext, key: str) -> None:
    started = (ctx.op.get("external_refs") or {}).get(key)
    if started is None:
        ctx.set_refs(**{key: time.time()})
        ctx.op["external_refs"] = {**(ctx.op.get("external_refs") or {}), key: time.time()}
    elif time.time() - float(started) > _cfg(ctx.service).wait_max_s:
        raise NeedsAttention("WAIT_TIMEOUT", f"still waiting after {int(_cfg(ctx.service).wait_max_s)} s ({key})")


def open_operations(ops: OperationService, actions: tuple[str, ...], repository: str, number: int) -> list[str]:
    """Non-terminal operations of these actions on one PR (merges and integrations exclude each other)."""
    marks, states = ",".join("?" * len(actions)), ",".join("?" * len(TERMINAL))
    rows = ops.db.execute(f"""SELECT operation_id, target FROM operations WHERE action IN ({marks})
        AND status NOT IN ({states}) ORDER BY created_at""", (*actions, *TERMINAL)).fetchall()  # noqa: S608
    out = []
    for r in rows:
        t = json.loads(r["target"])
        if str(t.get("repository") or "").lower() == repository.lower() and t.get("pull_number") == number:
            out.append(r["operation_id"])
    return out


def _has_step(ctx: OpContext, name: str) -> bool:
    return ctx.service.db.execute("SELECT 1 FROM operation_steps WHERE operation_id=? AND name=?",
                                  (ctx.operation_id, name)).fetchone() is not None


# --------------------------------------------------------------------------- merge
def _repo_or_403(ops: OperationService, repository) -> str:
    repo = _cfg(ops).repos.get(str(repository or "").lower())
    if repo is None:
        raise OperationError("REPO_NOT_CONFIGURED", f"{repository!r} has no [[github.repos]] entry", 403)
    return repo.repository


def _admit_merge(ops: OperationService, principal: Principal, target: dict, params: dict, pre: dict) -> None:
    _gh(ops)
    repository = _repo_or_403(ops, target.get("repository"))
    repo = _cfg(ops).repos[repository.lower()]
    if not repo.allow_merge:
        raise OperationError("MERGE_DISABLED", f"merging is disabled for {repository}", 403)
    number = target.get("pull_number")
    if not isinstance(number, int) or isinstance(number, bool) or number <= 0:
        raise OperationError("INVALID_TARGET", "target.pull_number must be a positive integer", 422)
    updating = open_operations(ops, ("integration.apply",), repository, number)
    if updating:
        raise OperationError("INTEGRATION_IN_PROGRESS", f"{updating[0]} is updating this PR's head; merge after it "
                             "finishes, at the new head", 409)
    if (not HEX40.match(str(pre.get("expected_head_sha") or ""))
            or not HEX40.match(str(pre.get("expected_base_sha") or ""))
            or not pre.get("preview_digest") or not params.get("preview_id")):
        raise OperationError("PRECONDITION_REQUIRED", "read github_pr_preview (GET /api/v1/repositories/"
                             "{owner}/{repo}/pulls/{number}) and pass its saved merge_preview id, digest, "
                             "reviewed head and base SHA", 422)
    preview = pr_delivery.get_preview(ops.db, params["preview_id"])
    if (preview["repository"].lower() != repository.lower() or preview["target"]["number"] != number
            or preview["digest"] != pre["preview_digest"]
            or preview["target"]["head_sha"] != pre["expected_head_sha"]
            or preview["target"]["base_sha"] != pre["expected_base_sha"]
            or preview["method"] != params.get("method", repo.default_merge_method)):
        raise OperationError("PREVIEW_MISMATCH", "merge parameters differ from the saved preview", 409)
    method = params.get("method", repo.default_merge_method)
    if method not in repo.merge_methods:
        raise OperationError("INVALID_PARAMS", f"method must be one of {', '.join(repo.merge_methods)}", 422)


def _check_merge_policy(ops: OperationService, repository: str, method: str) -> None:
    repo = _cfg(ops).repos[_repo_or_403(ops, repository).lower()]
    if not repo.allow_merge:
        raise OperationError("MERGE_DISABLED", f"merging is disabled for {repository}", 403)
    if method not in repo.merge_methods:
        raise OperationError("INVALID_PARAMS", f"method must be one of {', '.join(repo.merge_methods)}", 422)


async def _merge(ctx: OpContext, repository: str, number: int, sha: str, prefix: str = "merge", *, before_submit=None) -> dict:
    gh = _gh(ctx.service)
    pr = await _read(gh.pull(repository, number), "read the pull request", ctx)
    from .pm_maintenance_repair_delivery import require_native_target
    require_native_target(ctx, pr)
    submitted = _has_step(ctx, f"{prefix}.submit")
    preview = pr_delivery.get_preview(ctx.service.db, ctx.params["preview_id"])
    method = preview["method"]
    if not submitted:
        from .pm_delivery_lineage import require_target
        require_target(ctx, provider=gh.cfg.api_url, repository_id=(pr.get("base", {}).get("repo") or {}).get("id"), pull_number=number)
    if pr.get("merged"):
        if (pr.get("head") or {}).get("sha") != sha:
            raise NeedsAttention("MERGED_DIFFERENT_HEAD",
                                 f"PR #{number} was merged at head {str((pr.get('head') or {}).get('sha'))[:12]}, "
                                 f"not the reviewed {sha[:12]}")
        if before_submit and not submitted:
            await before_submit()
        acknowledged = False
        if submitted:
            async def observed():
                return {"http_status": 200, "status": "merged", "observed_only": True,
                        "details": {"sha": pr.get("merge_commit_sha")}}
            saved = await ctx.step(f"{prefix}.submit", observed, reconcile=lambda _: observed())
            acknowledged = saved.get("http_status") in {200, 202} and not saved.get("observed_only")
        return await _merged_result(ctx, pr, preview, acknowledged, repository, number, method)
    if pr.get("state") != "open":
        raise OperationError("PR_CLOSED", f"PR #{number} is {pr.get('state')} and not merged")
    if not submitted:
        for field, section, code in (("head_sha", "head", "TARGET_HEAD_CHANGED"),
                                     ("base_sha", "base", "TARGET_BASE_CHANGED")):
            observed = (pr.get(section) or {}).get("sha")
            if observed != preview["target"][field]:
                diff = {"field": field, "reviewed": preview["target"][field], "observed": observed}
                ctx.set_refs(scope_difference=diff)
                raise OperationError(code, json.dumps(diff), 409)
        if pr.get("draft"):
            raise OperationError("PR_DRAFT", f"PR #{number} is a draft")
        if pr.get("mergeable_state") == "dirty":
            raise NeedsAttention("MERGE_CONFLICT", f"PR #{number} has merge conflicts with its base")
        if pr.get("mergeable_state") == "blocked":
            checks = await _read(gh.check_runs(repository, sha), "read check runs", ctx)
            if any(c.get("status") != "completed" for c in checks.get("check_runs") or []):
                _check_wait(ctx, "checks_wait_started_at")
                raise Wait("waiting_checks", "required checks are still running", 30)

    async def submit() -> dict:
        from .pm_source_lineage import require_frame
        require_frame()
        status, body = await gh.merge_async(repository, number, sha, method)
        return {"http_status": status, "status": body.get("status"), "details": body.get("details") or {},
                "message": str(body.get("message") or "")[:300]}

    async def reconcile(_request: dict):
        try:
            status, again = await gh.pull(repository, number)
        except GitHubAmbiguous:
            return None
        _read_result(status, again, "read back the merge request", ctx)
        require_native_target(ctx, again)
        if status == 200 and again.get("merged"):
            return {"http_status": 200, "status": "merged", "details": {"sha": again.get("merge_commit_sha")},
                    "observed_only": True}
        # Still open: asking again is safe; a pending request for this PR comes back as 409 with its UUID.
        if status == 200 and again.get("state") == "open":
            from . import pm_maintenance_repair_delivery
            if pm_maintenance_repair_delivery.private_child(ctx.service, ctx.op):
                return None  # The original private repair never resends an unknown merge.
            try:
                await pr_delivery.check_scope(ctx, preview, expiry=False)
            except NeedsAttention as exc:
                if exc.code.startswith("GITHUB_"):
                    raise
                return None
            except (OperationError, GitHubAmbiguous):
                return None
            try:
                _check_merge_policy(ctx.service, repository, method)
            except OperationError as exc:
                # The earlier request still has an unknown outcome; block a resend without hiding it.
                raise NeedsAttention(exc.code, exc.message) from None
            if (ctx.admission_binding or {}).get("pm_delivery"):
                # An open PR is not evidence that its original async merge was
                # never accepted. Keep reading that request when current PM
                # authority no longer permits a resend.
                from .pm_lifecycle_gates import require_local
                from .pm_source_lineage import require_new_effect
                try:
                    require_new_effect(ctx.service, ctx.operation_id)
                    require_local(ctx.service.db, ctx.operation_id)
                except OperationError:
                    return None
            return RERUN
        return None

    if not submitted:
        _check_merge_policy(ctx.service, repository, method)
        if before_submit:
            await before_submit()
        await pr_delivery.check_scope(ctx, preview)
        _check_merge_policy(ctx.service, repository, method)
    r = await ctx.step(f"{prefix}.submit", submit, request={"repository": repository, "pull_number": number,
                                                           "sha": sha, "method": method}, reconcile=reconcile)
    status, result, details = r["http_status"], r.get("status"), r.get("details") or {}
    if status == 409:
        if (details.get("expected_head_sha") != sha or details.get("merge_method") != method
                or details.get("merge_action") != "default"):
            raise NeedsAttention("EXISTING_MERGE_REQUEST",
                                 "another merge request for this PR uses a different head or method")
        await pr_delivery.check_scope(ctx, preview, expiry=False)
        result = "pending"
    elif status not in {200, 202}:
        if status == 400:
            again = await _read(gh.pull(repository, number), "read PR after the merge request was refused", ctx)
            if again.get("merged") and (again.get("head") or {}).get("sha") == sha:
                ctx.set_refs(merge_write_acknowledged=False)
                return await _merged_result(ctx, again, preview, False, repository, number, method)
        code = "PR_NOT_MERGEABLE" if status == 400 else f"GITHUB_{status}"
        raise OperationError(code, (details.get("message") or r.get("message") or "merge refused")[:300])
    ctx.set_refs(merge_write_acknowledged=status in {200, 202} and not r.get("observed_only", False))
    if details.get("uuid"):
        ctx.set_refs(merge_request_uuid=details["uuid"])
    uuid = details.get("uuid") or (ctx.op.get("external_refs") or {}).get("merge_request_uuid")
    if result == "pending" and uuid:
        try:
            res = await _read(gh.merge_async_result(repository, number, uuid), "read the merge request", ctx)
        except (OperationError, NeedsAttention) as exc:
            if exc.code != "GITHUB_404":
                raise
            res = {"status": "enqueued"}  # expired UUID: observe the PR; never submit again
        result, details = res.get("status"), res.get("details") or {}
        if result == "pending":
            _check_wait(ctx, "merge_wait_started_at")
            raise Wait("waiting_external", "GitHub is running the merge", 5, {"merge_request_uuid": uuid})
    if result == "failed":
        raise NeedsAttention("MERGE_FAILED", str(details.get("message") or "GitHub refused the merge")[:300])
    if result == "enqueued":
        ctx.set_refs(merge_queue=True)
    pr = await _read(gh.pull(repository, number), "read the merged pull request", ctx)
    if not pr.get("merged"):
        _check_wait(ctx, "merge_wait_started_at")
        reason = "in the merge queue" if result == "enqueued" else "waiting for GitHub to show the merge"
        raise Wait("waiting_external", reason, 30 if result == "enqueued" else 5)
    if (pr.get("head") or {}).get("sha") != sha:
        raise NeedsAttention("MERGED_DIFFERENT_HEAD", f"PR #{number} merged at a different head")
    return await _merged_result(ctx, pr, preview, status in {200, 202} and not r.get("observed_only", False),
                                repository, number, method)


async def _merged_result(ctx, pr, preview, submitted, repository, number, method):
    receipt = await pr_delivery.verify_merge(ctx, pr, preview)
    if receipt.get("verified"):
        from .pm_delivery_lineage import record
        record(ctx, receipt["merged_sha"], "merge_verified")
    return {"merged": True, "repository": repository, "pull_number": number, "method": method,
            "html_url": pr.get("html_url"), **receipt,
            "via": "merge_queue" if (ctx.op.get("external_refs") or {}).get("merge_queue") else "direct",
            "merged_by_this_operation": submitted}


async def _run_merge(ctx: OpContext) -> dict:
    repository = _repo_or_403(ctx.service, ctx.target["repository"])
    from .pm_maintenance_repair_delivery import bounded_run
    return await bounded_run(ctx, lambda: _merge(ctx, repository, ctx.target["pull_number"], ctx.preconditions["expected_head_sha"]))


# --------------------------------------------------------------------------- deploy
async def _run_deploy(ctx: OpContext) -> dict:
    return await deployment.run_start(ctx)


async def _run_rollback(ctx: OpContext) -> dict:
    return await deployment.run_start(ctx, rollback=True)


def _admit_rollback(ops, principal, target, params, pre):
    deployment.admission(ops, principal, target, params, pre, rollback=True)


# --------------------------------------------------------------------------- merge + deploy
def _admit_merge_and_deploy(ops: OperationService, principal: Principal, target: dict, params: dict,
                            pre: dict) -> None:
    if not principal.allows("deploy"):
        raise OperationError("FORBIDDEN", "delivery.merge_and_deploy also needs the 'deploy' scope", 403)
    _admit_merge(ops, principal, target, params, pre)
    recipe = deployment.recipe(ops, target.get("recipe"))
    if recipe.repository.lower() != str(target.get("repository")).lower():
        raise OperationError("INVALID_TARGET", "the recipe deploys a different repository", 422)
    preview = pr_delivery.get_preview(ops.db, params["preview_id"])
    if preview["target"]["base_ref"] != recipe.ref:
        raise OperationError("DEPLOY_SOURCE_NOT_ON_REF", "reviewed PR base is not the recipe ref", 422)
    deployment.admission(ops, principal, target, params, pre, combined=True)


async def _run_merge_and_deploy(ctx: OpContext) -> dict:
    repository = _repo_or_403(ctx.service, ctx.target["repository"])
    from .pm_maintenance_repair_delivery import bounded_run
    return await bounded_run(ctx, lambda: deployment.run_combined(ctx, lambda before: _merge(
        ctx, repository, ctx.target["pull_number"], ctx.preconditions["expected_head_sha"], before_submit=before)))


async def reconcile_deployments(ops):
    await deployment.reconcile_deployments(ops)


# --------------------------------------------------------------------------- reads
async def pr_preview(ops: OperationService, repository: str, number: int, method: str | None = None,
                     *, from_event: bool = False) -> dict:
    """What the Dashboard shows before the one-click button: head, base, mergeability, checks, recipes."""
    gh = _gh(ops)
    repository = _repo_or_403(ops, repository)
    status, pr = await gh.pull(repository, number)
    if status != 200:
        raise OperationError(f"GITHUB_{status}", str(pr.get("message") or "cannot read the pull request")[:200],
                             404 if status == 404 else 502)
    head = (pr.get("head") or {}).get("sha")
    checks = {"total": 0, "pending": 0, "failed": 0}
    if head:
        cstatus, runs = await gh.check_runs(repository, head)
        for c in (runs.get("check_runs") or []) if cstatus == 200 else []:
            checks["total"] += 1
            if c.get("status") != "completed":
                checks["pending"] += 1
            elif c.get("conclusion") not in {"success", "neutral", "skipped"}:
                checks["failed"] += 1
    repo = _cfg(ops).repos[repository.lower()]
    method = method or repo.default_merge_method
    if method not in repo.merge_methods:
        raise OperationError("INVALID_PARAMS", "method is not enabled for this repository", 422)
    preview = await pr_delivery.card_preview(ops, repository, number, method, pr, from_event=from_event)
    return {"repository": repository, "pull_number": number, "title": pr.get("title"),
            "provider_origin": gh.cfg.api_url, "repository_id": (pr.get("base", {}).get("repo") or {}).get("id"),
            "body": pr.get("body") or "", "metadata_digest": pr_delivery.digest(pr_delivery.metadata(pr)),
            "metadata_update": {"allowed": repo.allow_pr_update}, "merge_preview": preview, "state": pr.get("state"),
            "draft": pr.get("draft"), "merged": pr.get("merged"), "merge_commit_sha": pr.get("merge_commit_sha"),
            "head_sha": head, "head_ref": (pr.get("head") or {}).get("ref"),
            "base_ref": (pr.get("base") or {}).get("ref"), "base_sha": (pr.get("base") or {}).get("sha"),
            "mergeable": pr.get("mergeable"), "mergeable_state": pr.get("mergeable_state"),
            "html_url": pr.get("html_url"), "checks": checks,
            "merge": {"allowed": repo.allow_merge, "methods": list(repo.merge_methods),
                      "default_method": repo.default_merge_method},
            "recipes": [{"name": r.name, "environment": r.environment, "mode": r.mode}
                        for r in _cfg(ops).recipes.values() if r.repository == repository]}


ACTIONS = [
    ActionDef("github.pr.update", "integrate", "Update PR title/body at a reviewed metadata digest",
              pr_delivery.run_update, pr_delivery.admit_update, ("repository",)),
    ActionDef("github.pr.merge", "merge", "Merge a pull request at the reviewed head SHA",
              _run_merge, _admit_merge, ("repository",)),
    ActionDef("deployment.start", "deploy", "Deploy a fixed commit through a configured recipe",
              _run_deploy, deployment.admission, ("recipe",)),
    ActionDef("deployment.rollback", "deploy", "Redeploy a saved verified identity through its recipe",
              _run_rollback, _admit_rollback, ("recipe",)),
    ActionDef("delivery.merge_and_deploy", "merge", "Merge a pull request, then deploy the merged commit",
              _run_merge_and_deploy, _admit_merge_and_deploy, ("repository", "recipe")),
]
