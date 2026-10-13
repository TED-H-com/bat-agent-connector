"""Fixed deployment identities, environment generations and provider evidence (plan §17)."""
from __future__ import annotations

import base64
import json
import logging
import math
import re
import time
from dataclasses import asdict
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

from . import deployment_store as store
from .github import GitHubAmbiguous
from .iso_time import parse_datetime
from .operations import RERUN, TERMINAL, NeedsAttention, OperationError, Wait

HEX40 = re.compile(r"^[0-9a-f]{40}$")
HEX64 = re.compile(r"^[0-9a-f]{64}$")
ARTIFACT_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")


def unexpired(value):
    try:
        date = parse_datetime(value)
        return date.tzinfo is not None and date.timestamp() > time.time()
    except (AttributeError, TypeError, ValueError, OverflowError):
        return False


def recipe(ops, name):
    r = ops.context["github_config"].recipes.get(str(name or ""))
    if r is None:
        raise OperationError("RECIPE_NOT_CONFIGURED", f"no [[deploy.recipes]] named {name!r}", 403)
    return r


def ready(r):
    missing = [] if r.verification else ["verification"]
    return {"ready": not missing, "missing": missing, "code": "DEPLOY_VERIFICATION_REQUIRED" if missing else None}


def recipe_digest(ops, r, repository_id):
    return store.digest({**asdict(r), "provider_origin": ops.context["github_config"].api_url,
                         "repository_id": repository_id})


def bound_environment(ops, r):
    rows = ops.db.execute("SELECT environment_key FROM deployment_environments WHERE provider_origin=? AND "
                          "repository=? COLLATE NOCASE AND environment=?",
                          (ops.context["github_config"].api_url, r.repository, r.environment)).fetchall()
    if len(rows) != 1:
        raise preview_required()
    return store.environment(ops.db, rows[0]["environment_key"])


def preview_required():
    return OperationError("DEPLOY_PREVIEW_REQUIRED", "read deployment_preview (GET /api/v1/deployments/preview?recipe=NAME) "
                          "and pass expected_environment_generation and expected_recipe_digest", 409)


async def preview(ops, name):
    from .delivery import _gh, _read
    r, gh = recipe(ops, name), _gh(ops)
    repo = await _read(gh.repository(r.repository), "read deployment repository")
    if not isinstance(repo.get("id"), int) or repo["id"] <= 0:
        raise OperationError("REPOSITORY_ID_CHANGED", "repository omitted its identity", 409)
    origin = ops.context["github_config"].api_url
    key = store.environment_key(origin, repo["id"], r.environment)
    with store.tx(ops.journal):
        old = ops.db.execute("SELECT repository_id FROM deployment_environments WHERE provider_origin=? "
                             "AND repository=? COLLATE NOCASE AND environment=?", (origin, r.repository, r.environment)).fetchall()
        if any(row[0] != repo["id"] for row in old):
            raise OperationError("REPOSITORY_ID_CHANGED", "configured name now refers to another repository", 409)
        ops.db.execute("""INSERT INTO deployment_environments
            (environment_key,provider_origin,repository_id,repository,environment,updated_at) VALUES(?,?,?,?,?,?)
            ON CONFLICT(environment_key) DO UPDATE SET repository=excluded.repository""",
            (key, origin, repo["id"], r.repository, r.environment, time.time()))
    env = store.environment(ops.db, key)
    dig = recipe_digest(ops, r, repo["id"])
    return {"recipe": r.name, "repository": r.repository, "repository_id": repo["id"], "environment": r.environment,
            "recipe_digest": dig, "environment_generation": env["desired_generation"],
            "preconditions": {"expected_environment_generation": env["desired_generation"], "expected_recipe_digest": dig},
            "readiness": ready(r), "ordering": asdict(r.ordering), "rollback": asdict(r.rollback),
            "verification": {"kind": r.verification.kind, "version_required": r.verification.version_required,
                             "health_required": r.verification.health_required} if r.verification else None,
            **environment_view(ops, env)}


def get(ops, dep_id):
    dep = store.deployment(ops.db, deployment_id=str(dep_id))
    if dep is None:
        raise OperationError("DEPLOYMENT_NOT_FOUND", "deployment not found", 404)
    return dep


def rollback_reason(ops, dep, r=None, env=None):
    r = r or ops.context["github_config"].recipes.get(dep["recipe"])
    if r is None or not r.rollback.supported:
        return "ROLLBACK_UNSUPPORTED"
    if dep.get("legacy") or not dep.get("verified") or dep["state"] not in {"succeeded", "superseded"}:
        return "ROLLBACK_TARGET_INVALID"
    if (dep["recipe"] != r.name or dep["recipe_snapshot"]["environment"] != r.environment
            or (env and dep["environment_key"] != env["environment_key"])):
        return "ROLLBACK_TARGET_INVALID"
    if r.rollback.identity == "artifact":
        identity = dep["identity"]
        expiry = identity.get("artifact_expires_at")
        if (not identity.get("artifact_id") or not identity.get("artifact_digest") or not identity.get("artifact_run_id")
                or not ARTIFACT_DIGEST.fullmatch(str(identity.get("artifact_digest", ""))) or not unexpired(expiry)):
            return "ROLLBACK_ARTIFACT_UNAVAILABLE"
    return None


def admission(ops, principal, target, params, pre, *, combined=False, rollback=False):
    from .delivery import _gh
    _gh(ops)
    r = recipe(ops, target.get("recipe"))
    if not r.verification:
        raise OperationError("DEPLOY_VERIFICATION_REQUIRED", f"recipe {r.name} is not fully configured: add verification", 422)
    g, dig = pre.get("expected_environment_generation"), pre.get("expected_recipe_digest")
    if not isinstance(g, int) or isinstance(g, bool) or g < 0 or not HEX64.fullmatch(str(dig or "")):
        raise preview_required()
    env = bound_environment(ops, r)
    if combined and set(params) - {"preview_id", "method"}:
        raise OperationError("INVALID_PARAMS", "combined deploy binds the verified merged SHA only", 422)
    if dig != recipe_digest(ops, r, env["repository_id"]):
        raise OperationError("RECIPE_CHANGED", "recipe differs from the reviewed deployment preview", 409)
    if rollback:
        if set(params) != {"deployment_id"}:
            raise OperationError("INVALID_PARAMS", "rollback selects only a saved deployment_id", 422)
        if not r.rollback.supported:
            raise OperationError("ROLLBACK_UNSUPPORTED", f"recipe {r.name} does not support rollback", 422)
        why = rollback_reason(ops, get(ops, params["deployment_id"]), r, env)
        if why:
            raise OperationError(why, "select an available verified deployment of this recipe and environment", 409)
    elif not combined:
        if set(params) - {"source_sha", "retry_of"} or not HEX40.fullmatch(str(params.get("source_sha") or "")):
            raise OperationError("INVALID_PARAMS", "params.source_sha must be the full 40-hex commit to deploy", 422)
        if params.get("retry_of"):
            old = get(ops, params["retry_of"])
            if (old["recipe"] != r.name or old["environment_key"] != env["environment_key"]
                    or old["identity"].get("source_sha") != params["source_sha"]):
                raise OperationError("ROLLBACK_TARGET_INVALID", "retry must retain the saved identity", 409)
    no_recipe_in_flight(ops, r)


def no_recipe_in_flight(ops, r):
    legacy = legacy_occupant(ops, r.repository, r.environment)
    if legacy:
        raise OperationError("DEPLOY_IN_PROGRESS", f"{legacy['operation_id']} still owns this environment's legacy provider slot", 409)
    for row in ops.db.execute("SELECT operation_id,target,status FROM operations WHERE action IN "
                              "('deployment.start','deployment.rollback','delivery.merge_and_deploy')"):
        if json.loads(row["target"]).get("recipe") != r.name:
            continue
        dep = store.deployment(ops.db, operation_id=row["operation_id"])
        if (dep and not dep["provider_terminal"]) or (not dep and row["status"] not in TERMINAL):
            raise OperationError("DEPLOY_IN_PROGRESS", f"{row['operation_id']} still owns this recipe's deploy lock", 409)


def bind_legacy(ops, dep):
    """Fill absent legacy routing once, recording the configuration fallback rather than guessing from a merge."""
    s = dict(dep["recipe_snapshot"])
    r = ops.context["github_config"].recipes.get(dep["recipe"])
    sources = dict(dep.get("legacy_binding_sources") or {})
    if r:
        configured = asdict(r)
        for field in ("repository", "environment", "mode", "workflow", "ref"):
            if not s.get(field):
                s[field] = configured[field]
                sources[field] = "configured_recipe"
    store.update(ops.journal, dep["deployment_id"], recipe_snapshot=s,
                 facts={"legacy_binding_sources": sources})
    return get(ops, dep["deployment_id"])


def legacy_occupant(ops, repository, environment):
    for row in ops.db.execute("SELECT deployment_id FROM deployments WHERE provider_terminal=0 AND recipe_digest='legacy'").fetchall():
        dep = bind_legacy(ops, get(ops, row[0]))
        s = dep["recipe_snapshot"]
        if s.get("repository", "").lower() == repository.lower() and s.get("environment") == environment:
            return dep
    return None


async def select(ctx, *, combined=False, rollback=False):
    async def apply():
        saved = store.deployment(ctx.service.db, operation_id=ctx.operation_id)
        if saved:
            return {"deployment_id": saved["deployment_id"], "generation": saved["generation"]}
        r = recipe(ctx.service, ctx.target["recipe"])
        env = bound_environment(ctx.service, r)
        dig = recipe_digest(ctx.service, r, env["repository_id"])
        if dig != ctx.preconditions.get("expected_recipe_digest"):
            raise OperationError("RECIPE_CHANGED", "recipe changed after admission", 409)
        old_id = ctx.params.get("deployment_id") if rollback else ctx.params.get("retry_of")
        old = get(ctx.service, old_id) if old_id else None
        if rollback:
            why = rollback_reason(ctx.service, old, r, env)
            if why:
                raise OperationError(why, "saved rollback identity is no longer eligible", 409)
        identity = dict(old["identity"]) if old else ({"source_pending_merge": True} if combined else {"source_sha": ctx.params["source_sha"]})
        if old and not rollback and old.get("legacy"):
            identity = {"source_sha": old["identity"]["source_sha"]}
        if old and rollback and r.rollback.identity == "source_sha":
            identity = {"source_sha": old["identity"]["source_sha"]}
        dep_id = "dep_" + ctx.operation_id.removeprefix("op_")
        snap = {**asdict(r), "provider_origin": ctx.service.context["github_config"].api_url,
                "repository_id": env["repository_id"], "workflow_id": None}
        doc = {"verified": False, "evidence": None, "rollback_of": old_id if rollback else None,
               "retry_of": old_id if not rollback else None, "provider_kind": "merge" if combined else "none", "dispatch_sent": False}
        now = time.time()
        with store.tx(ctx.service.journal):
            changed = ctx.service.db.execute("""UPDATE deployment_environments SET desired_generation=desired_generation+1,
                desired_deployment_id=?,version=version+1,updated_at=? WHERE environment_key=? AND desired_generation=?""",
                (dep_id, now, env["environment_key"], ctx.preconditions["expected_environment_generation"]))
            if changed.rowcount != 1:
                raise OperationError("ENVIRONMENT_CHANGED", "desired environment generation changed; read deployment_preview", 409)
            g = ctx.preconditions["expected_environment_generation"] + 1
            ctx.service.db.execute("""INSERT INTO deployments (deployment_id,operation_id,recipe,environment_key,generation,
                identity,recipe_snapshot,recipe_digest,state,document,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,?, 'selected',?,?,?)""",
                (dep_id, ctx.operation_id, r.name, env["environment_key"], g, store.encode(identity), store.encode(snap), dig,
                 store.encode(doc), now, now))
            store.event(ctx.service.journal, dep_id, "deployment.selected", actor=ctx.op["actor"])
        return {"deployment_id": dep_id, "generation": g}
    async def reconcile(_):
        saved = store.deployment(ctx.service.db, operation_id=ctx.operation_id)
        return {"deployment_id": saved["deployment_id"], "generation": saved["generation"]} if saved else RERUN
    selected = await ctx.step("deploy.select", apply, request={"preconditions": ctx.preconditions}, reconcile=reconcile)
    ctx.set_refs(deployment_id=selected["deployment_id"], environment_generation=selected["generation"])
    return get(ctx.service, selected["deployment_id"])


async def read(ctx, coro, what):
    from .delivery import _read
    try:
        return await _read(coro, what, ctx)
    except OperationError as exc:
        dep = store.deployment(ctx.service.db, operation_id=ctx.operation_id)
        if dep and (dep.get("dispatch_sent") or dep.get("run_id")):
            raise NeedsAttention(exc.code, exc.message + "; provider evidence is still required") from None
        raise


def gh_for(ops, dep):
    from .delivery import _gh
    gh = _gh(ops)
    if gh.cfg.api_url != dep["recipe_snapshot"]["provider_origin"]:
        raise NeedsAttention("REPOSITORY_ID_CHANGED", "restore the original provider configuration to read this run")
    return gh


async def artifact_check(ctx, dep, identity):
    s = dep["recipe_snapshot"]
    try:
        artifact = await read(ctx, gh_for(ctx.service, dep).artifact(s["repository"], identity["artifact_id"]), "read saved artifact")
    except OperationError as exc:
        raise OperationError("ROLLBACK_ARTIFACT_UNAVAILABLE", "saved artifact is unavailable", 409) from exc
    if (artifact.get("id") != identity["artifact_id"] or artifact.get("expired")
            or artifact.get("digest") != identity["artifact_digest"]
            or (artifact.get("workflow_run") or {}).get("id") != identity["artifact_run_id"]
            or (artifact.get("workflow_run") or {}).get("repository_id") != s["repository_id"]
            or not unexpired(artifact.get("expires_at"))):
        raise OperationError("ROLLBACK_ARTIFACT_UNAVAILABLE", "saved artifact identity is unavailable or expired", 409)


async def source(ctx, dep, merged=None):
    gh, s = gh_for(ctx.service, dep), dep["recipe_snapshot"]
    repo = await read(ctx, gh.repository(s["repository"]), "read deployment repository identity")
    if repo.get("id") != s["repository_id"]:
        raise NeedsAttention("REPOSITORY_ID_CHANGED", "repository name now has another identity")
    if recipe_digest(ctx.service, recipe(ctx.service, dep["recipe"]), repo["id"]) != dep["recipe_digest"]:
        raise NeedsAttention("RECIPE_CHANGED", "recipe changed; do not dispatch a different recipe")
    identity = dict(dep["identity"])
    if merged:
        if not merged.get("verified"):
            raise NeedsAttention("MERGE_RESULT_UNVERIFIABLE", "deploy requires a verified actual merged SHA")
        identity = {"source_sha": merged["merged_sha"]}
    if identity.get("artifact_id"):
        await artifact_check(ctx, dep, identity)
    if not identity.get("artifact_id") or ctx.op["action"] != "deployment.rollback":
        try:
            comparison = await read(ctx, gh.compare(s["repository"], s["ref"], identity["source_sha"]), "check source on recipe ref")
        except OperationError as exc:
            if exc.code != "GITHUB_404":
                raise
            raise OperationError("DEPLOY_SOURCE_NOT_ON_REF", "source/ref comparison is unavailable", 422) from None
        if comparison.get("status") not in {"identical", "behind"}:
            raise OperationError("DEPLOY_SOURCE_NOT_ON_REF", "source SHA is not reachable from recipe ref", 422)
    workflow = await read(ctx, gh.workflow(s["repository"], s["workflow"]), "read workflow identity")
    if type(workflow.get("id")) is not int or workflow["id"] <= 0:
        raise NeedsAttention("DEPLOY_RUN_AMBIGUOUS", "workflow omitted its fixed identity")
    store.update(ctx.service.journal, dep["deployment_id"], identity=identity,
                 recipe_snapshot={**s, "workflow_id": workflow["id"]}, facts={"provider_kind": "none"})
    return {"identity": identity, "workflow_id": workflow["id"]}


async def order(ctx, dep):
    outcome = None
    with store.tx(ctx.service.journal):
        env = store.environment(ctx.service.db, dep["environment_key"])
        if env["desired_generation"] != dep["generation"] or env["desired_deployment_id"] != dep["deployment_id"]:
            current = store.deployment(ctx.service.db, deployment_id=dep["deployment_id"])
            if current["run_id"] is None:
                store.update(ctx.service.journal, dep["deployment_id"], state="superseded", provider_terminal=True)
            outcome = "superseded"
        elif (legacy_occupant(ctx.service, dep["recipe_snapshot"]["repository"], dep["recipe_snapshot"]["environment"])
              or (env["slot_deployment_id"] and env["slot_deployment_id"] != dep["deployment_id"])):
            outcome = "waiting"
        else:
            ctx.service.db.execute("UPDATE deployment_environments SET slot_deployment_id=?,version=version+1,updated_at=? "
                                   "WHERE environment_key=?", (dep["deployment_id"], time.time(), dep["environment_key"]))
    if outcome == "superseded":
        release_slot(ctx.service, dep)
        raise NeedsAttention("DEPLOY_SUPERSEDED", "a newer desired deployment replaced this selection")
    if outcome == "waiting":
        store.update(ctx.service.journal, dep["deployment_id"], state="waiting_order")
        raise Wait("waiting_external", "waiting_order: environment provider slot is occupied", 10)
    return {"claimed": True}


def operation_token(run, op_id):
    return bool(re.search(r"(?<![A-Za-z0-9_])" + re.escape(op_id) + r"(?![A-Za-z0-9_])",
                          str(run.get("display_title") or "") + " " + str(run.get("name") or "")))


def run_matches(run, dep, *, token=True):
    s = dep["recipe_snapshot"]
    return ((run.get("repository") or {}).get("id") == s["repository_id"]
            and run.get("workflow_id") == s["workflow_id"] and run.get("head_branch") == s["ref"]
            and run.get("event") == ("workflow_dispatch" if token else "push")
            and (operation_token(run, dep["operation_id"]) if token else run.get("head_sha") == dep["identity"]["source_sha"]))


async def background_read(coro):
    status, body = await coro
    if status != 200:
        raise NeedsAttention(f"GITHUB_{status}", "provider read was refused")
    return body


async def locate(ops, dep, ctx=None):
    gh, s = gh_for(ops, dep), dep["recipe_snapshot"]
    dispatch_mode = s["mode"] == "workflow_dispatch"
    sent_at = dep.get("dispatch_sent_at") if dispatch_mode else None
    if not sent_at:
        step = ops.db.execute("SELECT min(started_at) FROM operation_steps WHERE operation_id=? AND name=?",
                              (dep["operation_id"], "deploy.dispatch" if dispatch_mode else "merge.submit")).fetchone()
        sent_at = step[0]
    # Standalone on_merge may adopt an older push run: never filter it by admission time.
    filters = {"created": ">=" + datetime.fromtimestamp(max(0, int(sent_at) - 1), timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")} if sent_at else {}
    matches = []
    # GitHub caps a filtered workflow-run search at 1,000 results. Do not accept a truncated search.
    for page in range(1, 12):
        call = gh.runs(s["repository"], s["workflow"], branch=s["ref"],
                       head_sha=None if dispatch_mode else dep["identity"]["source_sha"], page=page, per_page=100,
                       **filters,
                       event="workflow_dispatch" if dispatch_mode else "push")
        body = await read(ctx, call, "locate deployment run") if ctx else await background_read(call)
        if body.get("total_count", 0) > 1000:
            raise NeedsAttention("DEPLOY_RUN_AMBIGUOUS", "filtered run search exceeds GitHub's 1,000-result limit")
        runs = body.get("workflow_runs") or []
        matches.extend(r for r in runs if run_matches(r, dep, token=dispatch_mode))
        if len(runs) < 100:
            break
    else:
        raise NeedsAttention("DEPLOY_RUN_AMBIGUOUS", "run pagination did not terminate")
    if len(matches) > 1:
        raise NeedsAttention("DEPLOY_RUN_AMBIGUOUS", "several runs match this fixed deployment identity")
    return matches[0] if matches else None


def retry_after(value):
    try:
        seconds = float(value)
        return max(1.0, seconds) if math.isfinite(seconds) else 30.0
    except (TypeError, ValueError):
        try:
            return max(1.0, parsedate_to_datetime(str(value)).timestamp() - time.time())
        except (TypeError, ValueError, OverflowError):
            return 30.0


def dispatch_wait(ctx, delay):
    from .delivery import _check_wait
    key = "deploy_dispatch_wait_started_at"
    _check_wait(ctx, key)
    started = (ctx.service.get(ctx.operation_id).get("external_refs") or {})[key]
    remaining = ctx.service.context["github_config"].wait_max_s - (time.time() - started)
    return Wait("waiting_external", "dispatch rate limited; honouring Retry-After", min(delay, max(1, remaining)))


async def dispatch(ctx, dep):
    gh = gh_for(ctx.service, dep)
    last = ctx.service.db.execute("SELECT name,response,seq FROM operation_steps WHERE operation_id=? "
                                  "AND (name='deploy.dispatch' OR name LIKE 'deploy.dispatch.retry.%') "
                                  "ORDER BY seq DESC LIMIT 1", (ctx.operation_id,)).fetchone()
    refused = last and json.loads(last["response"] or "{}").get("http_status") == 429
    if refused:
        from .delivery import _check_wait
        _check_wait(ctx, "deploy_dispatch_wait_started_at")
        run = await locate(ctx.service, dep, ctx)
        if run:
            return run["id"]
        if time.time() < dep.get("dispatch_retry_at", 0):
            raise dispatch_wait(ctx, dep["dispatch_retry_at"] - time.time())
        await order(ctx, dep)
        await source(ctx, dep)
    name = f"deploy.dispatch.retry.{last['seq']}" if refused else last["name"] if last else "deploy.dispatch"
    # An acknowledged/unknown dispatch replays its receipt; only a new POST checks the current recipe policy.
    if not last or refused:
        r = recipe(ctx.service, dep["recipe"])
        if recipe_digest(ctx.service, r, dep["recipe_snapshot"]["repository_id"]) != dep["recipe_digest"]:
            raise OperationError("RECIPE_CHANGED", "recipe changed before dispatch", 409)
    s = dep["recipe_snapshot"]
    values = {"source_sha": dep["identity"]["source_sha"], "operation_id": ctx.operation_id,
              "environment": s["environment"], "repository": s["repository"], "environment_generation": str(dep["generation"]),
              "artifact_id": str(dep["identity"].get("artifact_id", "")), "artifact_digest": dep["identity"].get("artifact_digest", "")}
    inputs = {k: values[v] for k, v in s["inputs"]}
    async def send():
        from .pm_source_lineage import require_frame
        require_frame()
        store.update(ctx.service.journal, dep["deployment_id"], state="uncertain",
                     facts={"dispatch_sent": True, "provider_kind": "run", "dispatch_sent_at": dep.get("dispatch_sent_at") or time.time()})
        status, body = await gh.dispatch(s["repository"], s["workflow"], s["ref"], inputs)
        if status == 429:
            delay = retry_after(body.get("retry_after", "30"))
            store.update(ctx.service.journal, dep["deployment_id"], state="queued",
                         facts={"dispatch_sent": False, "provider_kind": "none", "dispatch_retry_at": time.time() + delay})
            return {"http_status": 429, "retry_after": delay}
        if status not in {200, 204}:
            store.update(ctx.service.journal, dep["deployment_id"], provider_terminal=True, facts={"dispatch_refused": True})
            return {"http_status": status}
        run_id = body.get("workflow_run_id")
        run_id = run_id if type(run_id) is int and run_id > 0 else None
        store.update(ctx.service.journal, dep["deployment_id"], state="queued", run_id=run_id,
                     facts={"html_url": body.get("html_url"), "write_acknowledged": True})
        return {"http_status": status, "run_id": run_id}
    async def reconcile(_):
        run = await locate(ctx.service, get(ctx.service, dep["deployment_id"]), ctx)
        return {"http_status": 200, "run_id": run["id"], "observed_only": True} if run else None
    result = await ctx.step(name, send, request={"recipe": dep["recipe"], "inputs": inputs}, reconcile=reconcile)
    if result["http_status"] == 429:
        raise dispatch_wait(ctx, result["retry_after"])
    if result["http_status"] not in {200, 204}:
        raise OperationError(f"GITHUB_{result['http_status']}", "GitHub refused dispatch")
    return result.get("run_id")


async def observe(ops, dep, ctx=None, *, run=None):
    gh, s = gh_for(ops, dep), dep["recipe_snapshot"]
    async def fetch(coro, what):
        return await read(ctx, coro, what) if ctx else await background_read(coro)
    if run is None:
        run = await fetch(gh.run(s["repository"], dep["run_id"]), "read the workflow run")
    terminal = run.get("status") == "completed"
    if not run_matches(run, dep, token=s["mode"] == "workflow_dispatch"):
        code = "RUN_VERSION_MISMATCH" if s["mode"] == "on_merge" and run.get("head_sha") != dep["identity"]["source_sha"] else "DEPLOY_RUN_AMBIGUOUS"
        return {"error": code, "attention": True, "provider_terminal": False}
    attempt = run.get("run_attempt")
    facts = {"run_id": run["id"], "run_attempt": attempt, "workflow_id": run.get("workflow_id"),
             "workflow_sha": run.get("head_sha"), "html_url": run.get("html_url"),
             "provider_status": run.get("status"), "provider_conclusion": run.get("conclusion")}
    if not isinstance(attempt, int) or attempt < 1:
        return {**facts, "waiting": "waiting for run attempt identity", "state": "unverified"}
    if dep.get("run_attempt") and attempt != dep["run_attempt"]:
        return {**facts, "error": "DEPLOY_ATTEMPT_CHANGED", "attention": True, "provider_terminal": terminal}
    if not terminal:
        waiting = "waiting for environment approval" if run.get("status") == "waiting" else f"workflow run is {run.get('status')}"
        return {**facts, "waiting": waiting, "state": "waiting_environment" if run.get("status") == "waiting" else "building",
                "provider_terminal": False}
    if run.get("conclusion") != "success":
        return {**facts, "error": "DEPLOY_FAILED", "provider_terminal": True}
    jobs = []
    for page in range(1, 10001):
        body = await fetch(gh.attempt_jobs(s["repository"], run["id"], attempt, page=page), "read attempt deploy jobs")
        page_jobs = body.get("jobs") or []
        jobs.extend(page_jobs)
        if len(page_jobs) < 100:
            break
    else:
        return {**facts, "error": "DEPLOY_VERSION_UNPROVEN", "attention": True, "provider_terminal": True}
    selected = [j for j in jobs if j.get("name") == s["deploy_job"]]
    facts["job"] = {k: selected[0].get(k) for k in ("id", "name", "status", "conclusion")} if len(selected) == 1 else None
    if len(selected) != 1 or selected[0].get("status") != "completed" or selected[0].get("conclusion") != "success":
        return {**facts, "error": "DEPLOY_NOT_RUN", "provider_terminal": True}
    pending = await fetch(gh.pending_deployments(s["repository"], run["id"]), "read pending environments")
    facts["pending_environments"] = [(p.get("environment") or {}).get("name") for p in pending.get("items") or []]
    if s["environment"] in facts["pending_environments"]:
        return {**facts, "waiting": "waiting for environment approval", "state": "waiting_environment", "provider_terminal": False}
    return {**facts, "provider_terminal": True, "provider_proven": True}


async def read_step(ctx, prefix, fn):
    previous = ctx.service.db.execute("SELECT name,seq FROM operation_steps WHERE operation_id=? "
                                      "AND (name=? OR name LIKE ?) ORDER BY seq DESC LIMIT 1",
                                      (ctx.operation_id, prefix, prefix + ".retry.%")).fetchone()
    name = f"{prefix}.retry.{previous['seq']}" if previous else prefix
    async def safe():
        try:
            return await fn()
        except Wait as exc:
            return {"waiting": exc.reason, "delay": exc.delay_s}
        except (NeedsAttention, OperationError) as exc:
            return {"error": exc.code, "attention": isinstance(exc, NeedsAttention), "message": exc.message}
        except GitHubAmbiguous:
            return {"waiting": "GitHub did not answer; retrying", "delay": 30}
    async def reconcile(_):
        return RERUN
    from .pm_delivery_lineage import readback_step
    return await readback_step(ctx, name, safe, reconcile=reconcile)


def apply_receipt(ctx, dep, receipt, wait_key):
    from .delivery import _check_wait
    if receipt.get("waiting"):
        _check_wait(ctx, wait_key)
        raise Wait("waiting_external", receipt["waiting"], receipt.get("delay", 15))
    if receipt.get("error"):
        exception = NeedsAttention if receipt.get("attention") else OperationError
        raise exception(receipt["error"], receipt.get("message", receipt["error"]))


def release_slot(ops, dep):
    with store.tx(ops.journal):
        ops.db.execute("UPDATE deployment_environments SET slot_deployment_id=NULL,version=version+1,updated_at=? "
                       "WHERE environment_key=? AND slot_deployment_id=?", (time.time(), dep["environment_key"], dep["deployment_id"]))


def failure(ops, dep, code, *, attention=False):
    fresh = get(ops, dep["deployment_id"])
    sent = fresh.get("dispatch_sent") or fresh.get("run_id") or fresh.get("on_merge_pending") or merge_sent(ops, fresh)
    store.update(ops.journal, dep["deployment_id"], state="superseded" if code == "DEPLOY_SUPERSEDED" else "needs_attention" if attention else "failed",
                 provider_terminal=fresh["provider_terminal"] or not sent, facts={"error_code": code})
    if get(ops, dep["deployment_id"])["provider_terminal"]:
        release_slot(ops, dep)


async def finish(ctx, dep, merged=None):
    if not dep.get("dispatch_sent") and not dep.get("run_id"):
        receipt = await read_step(ctx, "deploy.source", lambda: source(ctx, dep, merged))
        apply_receipt(ctx, dep, receipt, "deploy_source_wait_started_at")
        dep = get(ctx.service, dep["deployment_id"])
        receipt = await read_step(ctx, "deploy.order", lambda: order(ctx, dep))
        apply_receipt(ctx, dep, receipt, "deploy_order_wait_started_at")
    if not dep.get("run_id"):
        if dep["recipe_snapshot"]["mode"] == "workflow_dispatch":
            run_id = await dispatch(ctx, dep)
        else:
            store.update(ctx.service.journal, dep["deployment_id"], facts={"on_merge_pending": True})
            run_id = None
        if not run_id:
            receipt = await read_step(ctx, "deploy.locate", lambda: locate_receipt(ctx, dep))
            apply_receipt(ctx, dep, receipt, "deploy_locate_wait_started_at")
            run_id = receipt["run_id"]
        store.update(ctx.service.journal, dep["deployment_id"], run_id=run_id, facts={"provider_kind": "run"})
        ctx.set_refs(deploy_run_id=run_id)
        dep = get(ctx.service, dep["deployment_id"])
    receipt = await read_step(ctx, "deploy.observe", lambda: observe(ctx.service, dep, ctx))
    attempt = dep.get("run_attempt") or receipt.get("run_attempt")
    store.update(ctx.service.journal, dep["deployment_id"], run_attempt=attempt,
                 provider_terminal=receipt.get("provider_terminal", dep["provider_terminal"]),
                 state=receipt.get("state", "verifying"), facts={"provider_evidence": receipt,
                        "html_url": receipt.get("html_url") or dep.get("html_url")})
    if receipt.get("html_url"):
        ctx.set_refs(deploy_run_url=receipt["html_url"])
    dep = get(ctx.service, dep["deployment_id"])
    apply_receipt(ctx, dep, receipt, "deploy_wait_started_at")
    observed_env = store.environment(ctx.service.db, dep["environment_key"])
    evidence = await read_step(ctx, "deploy.verify", lambda: runtime_check(ctx.service, dep))
    store.update(ctx.service.journal, dep["deployment_id"], facts={"runtime_evidence": evidence})
    env = store.environment(ctx.service.db, dep["environment_key"])
    if env["desired_deployment_id"] != dep["deployment_id"]:
        if evidence.get("observed"):
            drift(ctx.service, observed_env, evidence["observed"])
        if evidence.get("error") or evidence.get("waiting"):
            store.update(ctx.service.journal, dep["deployment_id"], state="superseded", facts={"error_code": "DEPLOY_SUPERSEDED"})
            release_slot(ctx.service, dep)
            raise NeedsAttention("DEPLOY_SUPERSEDED", "a newer desired deployment superseded this result")
    if evidence.get("waiting"):
        started = (ctx.op.get("external_refs") or {}).get("deploy_verification_wait_started_at")
        if started is not None and time.time() - started > ctx.service.context["github_config"].wait_max_s:
            raise NeedsAttention("DEPLOY_VERSION_UNPROVEN", "runtime verification is still unavailable")
    apply_receipt(ctx, dep, evidence, "deploy_verification_wait_started_at")
    async def save():
        return record(ctx.service, get(ctx.service, dep["deployment_id"]), {"provider": receipt, "runtime": evidence})
    async def reconcile(_):
        saved = get(ctx.service, dep["deployment_id"])
        return saved.get("recorded_result") or RERUN
    from .pm_delivery_lineage import sent_readback
    result = await ctx.step("deploy.record", save, reconcile=reconcile, receipt_only=sent_readback(ctx, "deploy.record"))
    if not result["is_current"]:
        raise NeedsAttention("DEPLOY_SUPERSEDED", "a newer desired deployment superseded this result")
    ctx.set_refs(deployment_id=dep["deployment_id"], deployed_source_sha=dep["identity"]["source_sha"])
    return result


async def locate_receipt(ctx, dep):
    run = await locate(ctx.service, get(ctx.service, dep["deployment_id"]), ctx)
    if not run:
        return {"waiting": "waiting for the deployment run to start"}
    return {"run_id": run["id"]}


async def run_start(ctx, *, rollback=False):
    old = store.deployment(ctx.service.db, operation_id=ctx.operation_id)
    if old and old.get("legacy"):
        return await legacy_readback(ctx, old)
    if "expected_environment_generation" not in ctx.preconditions:
        raise preview_required()
    dep = await select(ctx, rollback=rollback)
    try:
        return await finish(ctx, dep)
    except (OperationError, NeedsAttention) as exc:
        failure(ctx.service, dep, exc.code, attention=isinstance(exc, NeedsAttention))
        raise


async def run_combined(ctx, merge_fn):
    old = store.deployment(ctx.service.db, operation_id=ctx.operation_id)
    if old and old.get("legacy"):
        return await legacy_readback(ctx, old)
    if "expected_environment_generation" not in ctx.preconditions:
        raise preview_required()
    dep = old
    async def before_submit():
        nonlocal dep
        dep = await select(ctx, combined=True)
        await order(ctx, dep)
    try:
        merged = dep.get("merged_result") if dep else None
        if merged is None:
            merged = await merge_fn(before_submit)
            ctx.set_refs(merged_sha=merged["merged_sha"])
            store.update(ctx.service.journal, dep["deployment_id"], facts={"merged_result": merged})
        deployed = await finish(ctx, get(ctx.service, dep["deployment_id"]), merged)
        return {"merge": merged, "deploy": deployed}
    except (OperationError, NeedsAttention) as exc:
        if dep:
            failure(ctx.service, dep, exc.code, attention=isinstance(exc, NeedsAttention))
        raise


def merge_sent(ops, dep):
    return ops.db.execute("SELECT 1 FROM operation_steps WHERE operation_id=? AND name='merge.submit'",
                          (dep["operation_id"],)).fetchone() is not None


def status(ops, dep_id):
    dep = get(ops, dep_id)
    env = store.environment(ops.db, dep["environment_key"])
    r = ops.context["github_config"].recipes.get(dep["recipe"])
    why = rollback_reason(ops, dep, r, env)
    s = dep["recipe_snapshot"]
    return {k: dep.get(k) for k in ("deployment_id", "operation_id", "recipe", "generation", "state", "identity",
            "created_at", "updated_at", "run_id", "run_attempt", "verified", "provider_terminal", "evidence",
            "error_code", "rollback_of", "retry_of", "html_url", "legacy", "reconciliation_error", "runtime_evidence")} | {
        "repository": s["repository"], "environment": s["environment"], "workflow": s["workflow"],
        "source_sha": dep["identity"].get("source_sha"),
        "operation_url": f"/api/v1/operations/{dep['operation_id']}", "provider_url": dep.get("html_url"),
        "rollback_eligible": why is None, "rollback_reason": why,
        "is_current": bool(env and env["current_deployment_id"] == dep_id and not env["attention"]),
        "rollback": {"ready": why is None, "code": why, "identity": r.rollback.identity if r else None,
                     "not_undone": list(r.rollback.not_undone) if r else []}}


def history(ops, name, *, cursor=None, limit=50):
    return history_page(ops, "recipe=?", [str(name)], cursor=cursor, limit=limit)


def environment_history(ops, name, *, cursor=None, limit=50):
    """The existing history projection and cursor, grouped across recipes of one configured environment."""
    r = recipe(ops, name)
    try:
        env = bound_environment(ops, r)
        key = env["environment_key"]
    except OperationError:
        key = ""  # history stays readable before the first provider binding
    aliases = [a.name for a in ops.context["github_config"].recipes.values()
               if a.repository.lower() == r.repository.lower() and a.environment == r.environment]
    placeholders = ",".join("?" for _ in aliases)
    where = ("environment_key=? OR (recipe_digest='legacy' AND "
             "(json_extract(recipe_snapshot,'$.repository')=? COLLATE NOCASE OR "
             f"(json_extract(recipe_snapshot,'$.repository')='' AND recipe IN ({placeholders}))) AND "
             "(json_extract(recipe_snapshot,'$.environment')=? OR "
             f"(json_extract(recipe_snapshot,'$.environment') IS NULL AND recipe IN ({placeholders}))))")
    return history_page(ops, "(" + where + ")", [key, r.repository, *aliases, r.environment, *aliases], cursor=cursor, limit=limit)


def history_page(ops, where, args, *, cursor=None, limit=50):
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 200:
        raise OperationError("INVALID_PARAMS", "limit must be 1..200", 422)
    args = list(args)
    if cursor is not None and cursor != "":
        try:
            if not isinstance(cursor, str):
                raise ValueError()
            decoded = json.loads(base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)))
            if not isinstance(decoded, list) or len(decoded) != 2:
                raise ValueError()
            stamp, dep_id = decoded
            valid_stamp = ((type(stamp) is int and -(2**63) <= stamp <= 2**63 - 1)
                           or (type(stamp) is float and math.isfinite(stamp)))
            if (not valid_stamp or not isinstance(dep_id, str)
                    or not re.fullmatch(r"dep_[0-9a-f-]+", dep_id)):
                raise ValueError()
        except (ValueError, TypeError, UnicodeError):
            raise OperationError("INVALID_PARAMS", "invalid deployment history cursor", 422) from None
        where += " AND (created_at<? OR (created_at=? AND deployment_id<?))"
        args += [stamp, stamp, dep_id]
    rows = ops.db.execute(f"SELECT deployment_id,created_at FROM deployments WHERE {where} "  # noqa: S608
                          "ORDER BY created_at DESC,deployment_id DESC LIMIT ?", (*args, limit + 1)).fetchall()
    page = rows[:limit]
    next_cursor = (base64.urlsafe_b64encode(store.encode([page[-1]["created_at"], page[-1]["deployment_id"]]).encode())
                   .decode().rstrip("=")) if len(rows) > limit else None
    return {"items": [status(ops, row["deployment_id"]) for row in page], "next_cursor": next_cursor}


def environment_view(ops, env):
    def item(dep_id):
        return status(ops, dep_id) if dep_id and store.deployment(ops.db, deployment_id=dep_id) else None
    return {"environment_key": env["environment_key"], "repository_id": env["repository_id"],
            "environment": env["environment"], "desired_generation": env["desired_generation"],
            "desired": item(env["desired_deployment_id"]), "current": item(env["current_deployment_id"]),
            "last_verified": item(env["last_verified_deployment_id"]), "slot_deployment_id": env["slot_deployment_id"],
            "observed": env["observed"], "observed_at": (env["observed"] or {}).get("observed_at"),
            "attention": env["attention"]}


def retry_envelope(saved, preconditions):
    return {"action": "deployment.start", "target": {"recipe": saved["recipe"]},
            "params": {"source_sha": saved["identity"].get("source_sha"), "retry_of": saved["deployment_id"]},
            "preconditions": preconditions}


def environment_status(ops, name):
    r = ops.context["github_config"].recipes.get(str(name))
    if r:
        try:
            return environment_view(ops, bound_environment(ops, r))
        except OperationError:
            return {"recipe": name, "desired_generation": 0, "current": None, "desired": None,
                    "last_verified": None, "slot_deployment_id": None, "attention": None}
    row = ops.db.execute("SELECT environment_key FROM deployments WHERE recipe=? ORDER BY created_at DESC LIMIT 1",
                          (str(name),)).fetchone()
    if not row:
        raise OperationError("RECIPE_NOT_CONFIGURED", "no recipe or saved deployment", 404)
    return environment_view(ops, store.environment(ops.db, row[0]))


async def runtime_check(ops, dep):
    from .deployment_verifier import fetch
    s = dep["recipe_snapshot"]
    settings = s.get("verification")
    if not settings:
        return {"error": "DEPLOY_VERIFICATION_REQUIRED", "attention": True}
    observed = await ops.context.get("deployment_verifier", fetch)(settings)
    if observed.get("waiting"):
        return observed
    if "repository_id" not in observed or "environment" not in observed:
        return {"waiting": "runtime omitted repository/environment evidence", "observed": observed}
    if observed["repository_id"] != s["repository_id"] or observed["environment"] != s["environment"]:
        return {"error": "DEPLOY_VERSION_MISMATCH", "attention": True, "observed": observed}
    identity = dep["identity"]
    version, health = settings["version_required"], settings["health_required"]
    if version:
        fields = ["source_sha"] + (["artifact_id", "artifact_digest"] if identity.get("artifact_id") else [])
        if any(k not in observed for k in fields):
            return {"waiting": "runtime omitted required version evidence", "observed": observed}
        if any(observed[k] != identity[k] for k in fields):
            code = "DEPLOY_VERSION_MISMATCH" if observed["source_sha"] != identity["source_sha"] else "DEPLOY_ARTIFACT_MISMATCH"
            return {"error": code, "attention": True, "observed": observed}
    if health and "healthy" not in observed:
        return {"waiting": "runtime omitted required health evidence", "observed": observed}
    if health and observed["healthy"] is not True:
        return {"error": "DEPLOY_HEALTH_FAILED", "attention": True, "observed": observed}
    # Artifact metadata is obtained from its original producing run, never from the publish run's head SHA.
    if s["rollback"]["identity"] == "artifact" and observed.get("artifact_id") and not identity.get("artifact_id"):
        if not observed.get("artifact_digest"):
            return {"waiting": "runtime omitted artifact digest", "observed": observed}
        if observed["artifact_id"] <= 0 or not ARTIFACT_DIGEST.fullmatch(observed["artifact_digest"]):
            return {"error": "DEPLOY_ARTIFACT_MISMATCH", "attention": True, "observed": observed}
        a = await background_read(gh_for(ops, dep).artifact(s["repository"], observed["artifact_id"]))
        run = a.get("workflow_run") or {}
        if (a.get("expired") or a.get("digest") != observed["artifact_digest"] or run.get("repository_id") != s["repository_id"]
                or not run.get("id") or not unexpired(a.get("expires_at"))):
            return {"error": "ROLLBACK_ARTIFACT_UNAVAILABLE", "attention": True, "observed": observed}
        identity = {**identity, "artifact_id": a["id"], "artifact_digest": a["digest"],
                    "artifact_run_id": run["id"], "artifact_expires_at": a["expires_at"]}
        store.update(ops.journal, dep["deployment_id"], identity=identity)
    return {"observed": observed, "version_checked": version, "health_checked": health,
            "checked_at": time.time(),
            "summary": ("version passed" if version else "runtime version not checked by this recipe")
                       + "; " + ("health passed" if health else "health not checked by this recipe")}


def record(ops, dep, evidence):
    now = time.time()
    with store.tx(ops.journal):
        fresh = get(ops, dep["deployment_id"])
        if fresh.get("recorded_result"):
            return fresh["recorded_result"]
        env = store.environment(ops.db, dep["environment_key"])
        current = env["desired_generation"] == dep["generation"] and env["desired_deployment_id"] == dep["deployment_id"]
        result = {"deployment_id": dep["deployment_id"], "recipe": dep["recipe"], "environment": dep["recipe_snapshot"]["environment"],
                  "repository": dep["recipe_snapshot"]["repository"], "workflow": dep["recipe_snapshot"]["workflow"],
                  "source_sha": fresh["identity"]["source_sha"], "identity": fresh["identity"], "generation": dep["generation"],
                  "run_id": dep["run_id"], "run_attempt": dep["run_attempt"], "html_url": evidence["provider"].get("html_url"),
                  "deployed": current, "is_current": current, "evidence": evidence}
        store.update(ops.journal, dep["deployment_id"], state="succeeded" if current else "superseded", provider_terminal=True,
                     facts={"verified": True, "evidence": evidence, "recorded_result": result,
                            "html_url": evidence["provider"].get("html_url")})
        if current:
            ops.db.execute("""UPDATE deployment_environments SET current_deployment_id=?,last_verified_deployment_id=?,
                observed=?,attention=NULL,version=version+1,updated_at=? WHERE environment_key=? AND
                desired_generation=? AND desired_deployment_id=?""", (dep["deployment_id"], dep["deployment_id"],
                store.encode({**evidence["runtime"]["observed"], "observed_at": evidence["runtime"].get("checked_at", now)}),
                now, dep["environment_key"], dep["generation"], dep["deployment_id"]))
        release_slot(ops, dep)
        store.event(ops.journal, dep["deployment_id"], "deployment.verified" if current else "deployment.superseded",
                    extra={"is_current": current})
    return result


async def legacy_readback(ctx, dep):
    dep = bind_legacy(ctx.service, dep)
    ctx.set_refs(deployment_id=dep["deployment_id"])
    if not dep.get("dispatch_sent") and not dep.get("run_id"):
        if not dep.get("merge_sent"):
            store.update(ctx.service.journal, dep["deployment_id"], provider_terminal=True, state="failed",
                         facts={"error_code": "DEPLOY_PREVIEW_REQUIRED"})
            release_slot(ctx.service, dep)
        raise preview_required()
    s = dep["recipe_snapshot"]
    r = ctx.service.context["github_config"].recipes.get(dep["recipe"])
    repository = s.get("repository") or (r.repository if r else None)
    if not repository or not dep.get("run_id"):
        raise NeedsAttention("DEPLOY_VERSION_UNPROVEN", "legacy dispatch requires its original run identity; never dispatch again")
    from .delivery import _gh
    run = await read(ctx, _gh(ctx.service).run(repository, dep["run_id"]), "read original legacy run")
    if run.get("status") != "completed":
        raise Wait("waiting_external", "waiting for original legacy run", 15)
    settle_legacy_run(ctx.service, dep, run)
    raise NeedsAttention("DEPLOY_VERSION_UNPROVEN", "legacy run completed without runtime evidence; saved as unverified")


def settle_legacy_run(ops, dep, run):
    outcome = dep.get("legacy_operation_status")
    state = {"failed": "failed", "cancelled": "cancelled"}.get(outcome,
        "unverified" if run.get("conclusion") == "success" else "failed")
    facts = {"legacy_provider_evidence": {k: run.get(k) for k in ("id", "status", "conclusion", "run_attempt")}}
    if state == "failed" and not dep.get("error_code"):
        facts["error_code"] = "DEPLOY_FAILED"
    store.update(ops.journal, dep["deployment_id"], provider_terminal=True, state=state, facts=facts)
    release_slot(ops, dep)


def drift(ops, env, observed):
    if not env["current_deployment_id"]:
        return
    current = store.deployment(ops.db, deployment_id=env["current_deployment_id"])
    if not current:
        return
    settings = current["recipe_snapshot"].get("verification") or {}
    if observed.get("repository_id") != env["repository_id"] or observed.get("environment") != env["environment"]:
        return
    if not settings.get("version_required"):
        return
    fields = ["source_sha"] + (["artifact_id", "artifact_digest"] if current["identity"].get("artifact_id") else [])
    if not all(k in observed for k in fields):
        return
    if any(observed[k] != current["identity"][k] for k in fields):
        with store.tx(ops.journal):
            changed = ops.db.execute("""UPDATE deployment_environments SET current_deployment_id=NULL,observed=?,
                attention='ENVIRONMENT_VERSION_DRIFT',version=version+1,updated_at=? WHERE environment_key=? AND version=?
                AND current_deployment_id=?""", (store.encode({**observed, "observed_at": time.time()}), time.time(), env["environment_key"], env["version"],
                current["deployment_id"]))
            if changed.rowcount:
                ops.journal.api_event("deployment_environment", env["environment_key"], "deployment.drift",
                                      {"code": "ENVIRONMENT_VERSION_DRIFT", "last_verified": current["deployment_id"],
                                       "deployment_id": current["deployment_id"], "operation_id": current["operation_id"]})


async def stopped_merge(ops, dep, op):
    """A cancelled merge can still start an on_merge run. Bind its reviewed result using reads only."""
    from .delivery import _gh
    s = dep["recipe_snapshot"]
    if not s.get("repository") or not op["target"].get("pull_number"):
        return False
    gh = _gh(ops) if dep.get("legacy") else gh_for(ops, dep)
    pr = await background_read(gh.pull(s["repository"], op["target"]["pull_number"]))
    store.update(ops.journal, dep["deployment_id"], facts={"merge_observation": {
        "merged": pr.get("merged"), "state": pr.get("state"), "head_sha": (pr.get("head") or {}).get("sha"),
        "merge_commit_sha": pr.get("merge_commit_sha")}})
    terminal = pr.get("state") == "closed"
    uuid = (op.get("external_refs") or {}).get("merge_request_uuid")
    if not terminal and uuid:
        result = await background_read(gh.merge_async_result(s["repository"], op["target"]["pull_number"], uuid))
        terminal = result.get("status") == "failed"
    if pr.get("merged"):
        if not s.get("mode"):
            raise NeedsAttention("DEPLOY_VERSION_UNPROVEN", "restore the legacy recipe mode before settling this merge")
        if s["mode"] == "on_merge":
            step = ops.db.execute("SELECT request FROM operation_steps WHERE operation_id=? AND name='merge.submit'",
                                  (op["operation_id"],)).fetchone()
            request = json.loads(step[0]) if step else {}
            head = request.get("sha") or op["preconditions"].get("expected_head_sha")
            sha = pr.get("merge_commit_sha")
            if not HEX40.fullmatch(str(sha or "")) or not head or (pr.get("head") or {}).get("sha") != head:
                raise NeedsAttention("MERGE_RESULT_UNVERIFIABLE", "cancelled merge did not prove the reviewed head")
            if request.get("method") == "merge":
                commit = await background_read(gh.commit(s["repository"], sha))
                parents = commit.get("parents") or []
                if len(parents) != 2 or parents[1].get("sha") != head:
                    raise NeedsAttention("MERGE_RESULT_UNVERIFIABLE", "cancelled merge parents do not prove the reviewed head")
            comparison = await background_read(gh.compare(s["repository"], s["ref"], sha))
            if comparison.get("status") not in {"identical", "behind"}:
                raise NeedsAttention("DEPLOY_SOURCE_NOT_ON_REF", "cancelled merge result is not reachable from recipe ref")
            if not s.get("repository_id"):
                repository = await background_read(gh.repository(s["repository"]))
                s = {**s, "repository_id": repository["id"], "provider_origin": gh.cfg.api_url}
            if not s.get("workflow_id"):
                workflow = await background_read(gh.workflow(s["repository"], s["workflow"]))
                s = {**s, "workflow_id": workflow["id"]}
            store.update(ops.journal, dep["deployment_id"], identity={"source_sha": sha}, recipe_snapshot=s,
                         facts={"on_merge_pending": True, "merge_binding": {"reviewed_head_sha": head, "merged_sha": sha,
                                                                            "recipe_ref": s["ref"]}})
            return False
        terminal = True
    if terminal:
        store.update(ops.journal, dep["deployment_id"], provider_terminal=True,
                     facts={"merge_provider_terminal": True, "merge_commit_sha": pr.get("merge_commit_sha")})
        release_slot(ops, dep)
    return terminal


def reconcile_now():
    return time.time()


def poll_due(ops, key, *, adaptive=False):
    """Persist the read cadence separately from evidence, including unsuccessful reads and restarts."""
    now = reconcile_now()
    interval = ops.context["github_config"].deployment_reconcile_interval_s
    with store.tx(ops.journal):
        row = ops.db.execute("SELECT * FROM deployment_reconcile_reads WHERE read_key=?", (key,)).fetchone()
        if row and now - row["checked_at"] < (min(interval, row["interval_s"]) if adaptive else interval):
            return False
        ops.db.execute("INSERT INTO deployment_reconcile_reads(read_key,checked_at) VALUES(?,?) ON CONFLICT(read_key) "
                       "DO UPDATE SET checked_at=excluded.checked_at", (key, now))
    return True


def provider_cadence(ops, key, dep, error=None):
    state = store.digest({k: dep.get(k) for k in ("provider_evidence", "legacy_provider_evidence", "merge_observation",
                                                "run_id", "identity", "provider_terminal", "on_merge_pending")} | {"error": error})
    with store.tx(ops.journal):
        row = ops.db.execute("SELECT * FROM deployment_reconcile_reads WHERE read_key=?", (key,)).fetchone()
        interval = min(ops.context["github_config"].deployment_reconcile_interval_s, row["interval_s"] * 2) if row["provider_state"] == state else 15
        ops.db.execute("UPDATE deployment_reconcile_reads SET interval_s=?,provider_state=? WHERE read_key=?", (interval, state, key))


async def background_locate(ops, dep):
    if not poll_due(ops, "locate:" + dep["deployment_id"]):
        return False, None
    return True, await locate(ops, dep)


def attempt_attention(ops, dep, proof):
    """An external rerun invalidates current without replacing the original saved attempt."""
    with store.tx(ops.journal):
        ops.db.execute("""UPDATE deployment_environments SET current_deployment_id=NULL,
            attention=?,version=version+1,updated_at=? WHERE environment_key=? AND current_deployment_id=?""",
            (proof["error"], time.time(), dep["environment_key"], dep["deployment_id"]))
        if not proof.get("provider_terminal"):
            ops.db.execute("UPDATE deployment_environments SET slot_deployment_id=?,version=version+1,updated_at=? "
                           "WHERE environment_key=? AND slot_deployment_id IS NULL",
                           (dep["deployment_id"], time.time(), dep["environment_key"]))


def save_provider(ops, dep, proof):
    if not store.update(ops.journal, dep["deployment_id"], expected_version=dep["version"],
                        provider_terminal=proof.get("provider_terminal", dep["provider_terminal"]),
                        run_attempt=dep["run_attempt"] or proof.get("run_attempt"), facts={"provider_evidence": proof}):
        return None
    if proof.get("provider_terminal"):
        release_slot(ops, dep)
    if proof.get("error"):
        store.update(ops.journal, dep["deployment_id"], state="needs_attention" if proof.get("attention") else "failed",
                     facts={"error_code": proof["error"]})
        if proof["error"] == "DEPLOY_ATTEMPT_CHANGED":
            attempt_attention(ops, dep, proof)
    return get(ops, dep["deployment_id"])


async def check_current(ops, dep, env):
    if not poll_due(ops, "current:" + env["environment_key"]):
        return False
    s = dep["recipe_snapshot"]
    run = await background_read(gh_for(ops, dep).run(s["repository"], dep["run_id"]))
    if (not run_matches(run, dep, token=s["mode"] == "workflow_dispatch")
            or run.get("run_attempt") != dep["run_attempt"] or run.get("status") != "completed"
            or run.get("conclusion") != "success"):
        proof = await observe(ops, dep, run=run)
        if not proof.get("error"):
            proof = {**proof, "error": "DEPLOY_VERSION_UNPROVEN", "attention": True}
        if save_provider(ops, dep, proof):
            attempt_attention(ops, dep, proof)
        return True
    # The saved successful attempt's jobs and approvals are immutable evidence; do not page them again.
    runtime = await runtime_check(ops, dep)
    if runtime.get("observed"):
        drift(ops, env, runtime["observed"])
    prior = dep.get("runtime_evidence") or dep["evidence"]["runtime"]
    def comparable(r):
        return {k: v for k, v in r.items() if k != "checked_at"}
    if comparable(runtime) != comparable(prior):
        store.update(ops.journal, dep["deployment_id"], expected_version=dep["version"], facts={"runtime_evidence": runtime})
        # A read started before a newer current was recorded cannot change that environment's attention.
        with store.tx(ops.journal):
            ops.db.execute("UPDATE deployment_environments SET observed=?,attention=?,version=version+1,updated_at=? "
                           "WHERE environment_key=? AND version=? AND current_deployment_id=?",
                           (store.encode({**(runtime.get("observed") or {}), "observed_at": runtime.get("checked_at", time.time())}),
                            runtime.get("error") or ("DEPLOY_VERSION_UNPROVEN" if runtime.get("waiting") else None),
                            time.time(), env["environment_key"], env["version"], dep["deployment_id"]))
    return True


def settle_stopped_local(ops, dep, op):
    """Terminal provider evidence needs no more network reads, even if the operation stopped before recording."""
    env = store.environment(ops.db, dep["environment_key"])
    older = dep["generation"] is not None and env["desired_deployment_id"] != dep["deployment_id"]
    if older and dep["state"] != "superseded":
        store.update(ops.journal, dep["deployment_id"], state="superseded")
    elif not dep.get("recorded_result") and dep["state"] not in {"failed", "cancelled", "superseded", "unverified", "needs_attention"}:
        store.update(ops.journal, dep["deployment_id"], state=op["status"] if op["status"] != "succeeded" else "unverified")
    release_slot(ops, dep)


async def reconcile_stopped(ops, dep, op):
    merge_read = False
    if dep.get("legacy"):
        dep = bind_legacy(ops, dep)
        if dep.get("run_id"):
            from .delivery import _gh
            repository = dep["recipe_snapshot"].get("repository")
            if repository:
                run = await background_read(_gh(ops).run(repository, dep["run_id"]))
                store.update(ops.journal, dep["deployment_id"], facts={"legacy_provider_evidence": {
                    k: run.get(k) for k in ("id", "status", "conclusion", "run_attempt")}})
                if run.get("status") == "completed":
                    settle_legacy_run(ops, dep, run)
                return True
            return False
        if dep.get("merge_sent"):
            if not dep.get("on_merge_pending"):
                if await stopped_merge(ops, dep, op):
                    return True
                merge_read = True
                dep = get(ops, dep["deployment_id"])
            if dep.get("on_merge_pending"):
                read, run = await background_locate(ops, dep)
                if run:
                    store.update(ops.journal, dep["deployment_id"], run_id=run["id"])
                    if run.get("status") == "completed":
                        settle_legacy_run(ops, get(ops, dep["deployment_id"]), run)
                return read or merge_read
            return True
        return False
    if not dep.get("run_id"):
        if not dep.get("dispatch_sent") and not dep.get("on_merge_pending"):
            if merge_sent(ops, dep) and not dep.get("merged_result"):
                if await stopped_merge(ops, dep, op):
                    return True
                merge_read = True
                dep = get(ops, dep["deployment_id"])
            elif op["status"] == "cancelled":
                store.update(ops.journal, dep["deployment_id"], provider_terminal=True, state="cancelled")
                release_slot(ops, dep)
                return True
        if not dep.get("dispatch_sent") and not dep.get("on_merge_pending"):
            return True
        read, run = await background_locate(ops, dep)
        if not run:
            return read or merge_read
        store.update(ops.journal, dep["deployment_id"], run_id=run["id"])
        dep = get(ops, dep["deployment_id"])
    proof = await observe(ops, dep)
    dep = save_provider(ops, dep, proof)
    if not dep or proof.get("error") or not proof.get("provider_proven"):
        return True
    env = store.environment(ops.db, dep["environment_key"])
    runtime = await runtime_check(ops, dep)
    if runtime.get("observed"):
        drift(ops, env, runtime["observed"])
    if runtime.get("error") or runtime.get("waiting"):
        older = env["desired_deployment_id"] != dep["deployment_id"]
        store.update(ops.journal, dep["deployment_id"], state="superseded" if older else "needs_attention",
                     facts={"runtime_evidence": runtime})
        return True
    if not dep.get("recorded_result"):
        record(ops, dep, {"provider": proof, "runtime": runtime})
    return True


async def reconcile_deployments(ops):
    """Read stopped in-flight providers and each current environment only; never send or resume writes."""
    rows = ops.db.execute("""SELECT d.deployment_id FROM deployments d JOIN operations o USING(operation_id)
        WHERE o.status IN ('cancelled','needs_attention','failed','succeeded') AND
        (d.provider_terminal=0 OR EXISTS(SELECT 1 FROM deployment_environments e WHERE e.current_deployment_id=d.deployment_id)
         OR (d.provider_terminal=1 AND json_extract(d.document,'$.recorded_result') IS NULL AND
             (d.state NOT IN ('failed','cancelled','superseded','unverified','needs_attention') OR
              (d.generation IS NOT NULL AND d.state IN ('cancelled','needs_attention') AND
               EXISTS(SELECT 1 FROM deployment_environments e WHERE e.environment_key=d.environment_key
                      AND e.desired_deployment_id!=d.deployment_id)))))
        ORDER BY d.created_at""").fetchall()
    for row in rows:
        dep = None
        adaptive = False
        key = "provider:" + row[0]
        try:
            dep = get(ops, row[0])
            op = ops.get(dep["operation_id"])
            env = store.environment(ops.db, dep["environment_key"])
            adaptive = (not dep["provider_terminal"] and bool(dep.get("run_id") or
                (merge_sent(ops, dep) and not dep.get("on_merge_pending") and not dep.get("dispatch_sent"))))
            if adaptive and not poll_due(ops, key, adaptive=True):
                continue
            if env["current_deployment_id"] == dep["deployment_id"]:
                complete = await check_current(ops, dep, env)
            elif dep["provider_terminal"]:
                settle_stopped_local(ops, dep, op)
                complete = True
            else:
                complete = await reconcile_stopped(ops, dep, op)
            if complete:
                fresh = get(ops, dep["deployment_id"])
                if adaptive:
                    provider_cadence(ops, key, fresh)
                if fresh.get("reconciliation_error"):
                    store.update(ops.journal, dep["deployment_id"], facts={"reconciliation_error": None})
        except (OperationError, NeedsAttention) as exc:
            store.update(ops.journal, row[0], facts={"reconciliation_error": exc.code})
            if adaptive:
                provider_cadence(ops, key, get(ops, row[0]), exc.code)
        except (GitHubAmbiguous, Wait):
            if adaptive:
                provider_cadence(ops, key, get(ops, row[0]), "unanswered")
            continue
        except Exception as exc:  # noqa: BLE001 - one malformed row must not starve later deployments
            logging.warning("deployment %s reconciliation failed: %s", row[0], type(exc).__name__)
            try:
                store.update(ops.journal, row[0], facts={"reconciliation_error": "RECONCILE_FAILED"})
                if adaptive and dep:
                    provider_cadence(ops, key, dep, "RECONCILE_FAILED")
            except Exception as save_error:  # noqa: BLE001 - a corrupt receipt must not stop later rows either
                logging.warning("deployment %s reconciliation receipt failed: %s", row[0], type(save_error).__name__)
