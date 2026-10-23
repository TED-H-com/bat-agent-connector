"""Start at an explicitly published GitHub head in new managed resources."""
from __future__ import annotations

import base64
import hashlib
import json
import re
import shlex
import uuid
from dataclasses import asdict
from importlib import resources

from . import (
    artifacts,
    checkpoints,
    native_carrier,
    registry,
    resource_policy,
    service,
    task_control,
    work_items,
)
from .operations import RERUN, ActionDef, AmbiguousOutcome, NeedsAttention, OperationError, StepFailed

SHA = re.compile(r"[0-9a-f]{40}")
DIGEST = re.compile(r"[0-9a-f]{64}")
FIELDS = {"source_ref", "source_sha", "agent", "prompt", "title", "model", "artifacts", "project_id", "work_item_id",
          "pm_package_ref", "pm_code_basis", "pm_spec_ref", "pm_requirement_quality_refs"}


def project_context(ops, target, params, pre, *, operation_id=None):
    pid = params.get("project_id")
    if pid is None:
        if "expected_project_version" in pre or "work_item_id" in params:
            raise OperationError("INVALID_PARAMS", "project version requires project_id", 422)
        return
    if not isinstance(pid, str) or not work_items.PROJECT_ID.fullmatch(pid):
        raise OperationError("INVALID_PARAMS", "an exact project_id is required", 422)
    project = work_items._get_project(ops.db, pid, active=True)
    if type(pre.get("expected_project_version")) is not int or pre["expected_project_version"] != project["version"]:
        raise OperationError("PROJECT_CHANGED", "review the current project before starting", 409)
    if target["repository"].lower() not in {r.lower() for r in project["repositories"]}:
        raise OperationError("PROJECT_REPOSITORY_CHANGED", "selected repository does not belong to this project", 409)
    if "work_item_id" in params:
        wid = params["work_item_id"]
        if not isinstance(wid, str) or not work_items.WORK_ITEM_ID.fullmatch(wid):
            raise OperationError("INVALID_PARAMS", "exact work item ID required", 422)
        item = work_items._get_item(ops.db, wid, active=True)
        if item["project_id"] != pid or work_items.fingerprint(item) != pre.get("expected_work_item_fingerprint"):
            raise OperationError("WORK_ITEM_CHANGED", "review the current project work item before starting", 409)
        from . import managed_repairs
        repair = managed_repairs.dispatch_record(ops, wid)
        if repair:
            if work_items.completion(item)["approved"]:
                raise OperationError("REPAIR_COMPLETED", "completed repair work cannot start another dispatch", 409)
            if params.get("prompt") != repair["request"] or item["request"] != repair["request"]:
                raise OperationError("REPAIR_CHANGED", "repair dispatch must preserve its fixed evidence request", 409)
            launch = ops.db.execute("SELECT operation_id FROM managed_repair_launches WHERE work_item_id=?", (wid,)).fetchone()
            if launch and launch[0] != operation_id:
                raise OperationError("REPAIR_ALREADY_DISPATCHED", "read the existing repair operation instead of starting again", 409)


def install(ops):
    from .pm_source_lineage import schema
    schema(ops.journal)
    if "repository.continue" not in ops.actions:
        ops.register(ActionDef("repository.continue", "start", "Start from a published version", run, admit,
                               ("repository", "host", "workspace_id"), authorize_existing=_authorize_existing))


def _authorize_existing(ops, principal, op, control):
    from .pm_access import require_visible
    require_visible(ops.db, principal, op)
    if op["params"].get("work_item_id") and not principal.allows("manage"):
        raise OperationError("FORBIDDEN", "dispatch linked work needs manage", 403)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def configured(ops, target):
    if (set(target) != {"repository", "host", "workspace_id"} or any(not isinstance(v, str) or
            not 1 <= len(v) <= 256 for v in target.values())):
        raise OperationError("INVALID_TARGET", "exact repository, host and workspace_id are required", 422)
    fleet = ops.context["fleet"]
    repo = fleet.config.github.repos.get(target["repository"].lower())
    pair = target["host"], target["workspace_id"]
    if not repo or not repo.sync or pair not in repo.sync.bindings:
        raise OperationError("REPOSITORY_NOT_BOUND", "no explicit repository/host/workspace binding", 403)
    hc = fleet.config.hosts.get(pair[0])
    runner = ops.context.get("git_runner")
    if not hc or not fleet.orchestrate_enabled(pair[0]) or not hc.managed_roots:
        raise OperationError("REPOSITORY_HOST_UNAVAILABLE", "target needs write/orchestrate and a managed root", 403)
    if not runner or not runner.available(pair[0]) or ops.context.get("github") is None:
        raise OperationError("REPOSITORY_HOST_UNAVAILABLE", "GitHub read client and SSH Git runner are required", 409)
    return repo, hc


def config_snapshot(ops, target):
    repo, hc = configured(ops, target)
    return {"provider_origin": ops.context["github_config"].api_url, "repository": repo.repository,
            "host": hc.name, "workspace_id": target["workspace_id"], "profile_id": hc.profile_id,
            "managed_root": resource_policy.norm(hc.managed_roots[0]), "remote_url": repo.sync.remote_url,
            "sync": asdict(repo.sync), "permission_policy": hc.default_permission_mode,
            "register_tabs": hc.orchestrate_register_tabs, "confinement_policy": hc.confinement}


def check_ref(ref):
    branch = ref[len("refs/heads/"):] if isinstance(ref, str) and ref.startswith("refs/heads/") else ""
    if not resource_policy.HEAD_REF.fullmatch(branch) or any(p.endswith(".lock") or p.startswith(".") for p in branch.split("/")):
        raise OperationError("INVALID_REF", "source_ref must be an explicit refs/heads/ branch", 422)
    return branch


def admit(ops, principal, target, params, pre):
    configured(ops, target)
    expected = {"repository_id", "binding_digest"} | ({"expected_project_version"} if "project_id" in params else set())
    if "work_item_id" in params:
        expected.add("expected_work_item_fingerprint")
        if not principal.allows("manage"):
            raise OperationError("FORBIDDEN", "dispatch linked work needs manage", 403)
    if set(params) - FIELDS or set(pre) != expected:
        raise OperationError("INVALID_PARAMS", "published start requires exact preview preconditions", 422)
    check_ref(params.get("source_ref"))
    if (not isinstance(params.get("source_sha"), str) or not SHA.fullmatch(params["source_sha"])
            or type(pre["repository_id"]) is not int or pre["repository_id"] <= 0
            or not isinstance(pre["binding_digest"], str) or not DIGEST.fullmatch(pre["binding_digest"])):
        raise OperationError("INVALID_PARAMS", "full source SHA, repository ID and binding digest required", 422)
    if not isinstance(params.get("agent", "claude"), str) or params.get("agent", "claude") not in {"claude", "codex"}:
        raise OperationError("INVALID_PARAMS", "agent must be claude or codex", 422)
    for key, limit in (("prompt", 12000), ("title", 256), ("model", 256)):
        if key == "prompt" or key in params:
            if not isinstance(params.get(key), str) or not params[key].strip() or len(params[key]) > limit:
                raise OperationError("INVALID_PARAMS", f"{key} must contain 1-{limit} characters", 422)
    project_context(ops, target, params, pre)
    refs = artifacts.normalize_refs(ops.db, params.get("artifacts", []), ops.context["artifact_store"].settings)
    if len(checkpoints._input_instructions(ops.db, params["prompt"], refs)) > service.MAX_PROMPT_CHARS - 100:
        raise OperationError("INVALID_PARAMS", "instructions and input manifest exceed the prompt limit", 422)
    from . import pm_package_handoff
    binding = pm_package_handoff.admit(ops, principal, target, params, pre)
    if binding:
        text = pm_package_handoff.prompt(params["prompt"], binding["pm_handoff"]["contract"])
        if len(checkpoints._input_instructions(ops.db, text, refs)) > service.MAX_PROMPT_CHARS - 100:
            raise OperationError("PM_HANDOFF_TOO_LARGE", "The complete engineering contract and input manifest exceed the prompt limit", 413)
    return binding


async def gh_read(call):
    status, value = await call
    if status != 200 or not isinstance(value, dict):
        raise OperationError("REPOSITORY_SOURCE_UNAVAILABLE", "published GitHub identity/ref unavailable", 409)
    return value


async def published(ops, repository, ref, *, expected_id=None, expected_sha=None):
    gh = ops.context["github"]
    repo = await gh_read(gh.repository(repository))
    if (type(repo.get("id")) is not int or repo["id"] <= 0 or
            str(repo.get("full_name", "")).lower() != repository.lower() or
            expected_id is not None and repo["id"] != expected_id):
        raise OperationError("REPOSITORY_ID_CHANGED", "configured repository has a different identity", 409)
    observed = await gh_read(gh.branch_ref(repository, check_ref(ref)))
    obj = observed.get("object") or {}
    sha = obj.get("sha")
    if (observed.get("ref") != ref or obj.get("type") != "commit" or not isinstance(sha, str) or not SHA.fullmatch(sha)):
        raise OperationError("REPOSITORY_SOURCE_UNAVAILABLE", "GitHub did not prove the exact published branch head", 409)
    if expected_sha is not None and sha != expected_sha:
        raise OperationError("REPOSITORY_REF_CHANGED", "published branch head changed; keep original selection and review again", 409)
    return repo["id"], sha


async def workspace(ops, target, read=None):
    _, hc = configured(ops, target)
    if read:
        value = await read("workspace:load", {"profileId": hc.profile_id})
        doc = json.loads(value) if isinstance(value, str) else value
    else:
        doc = await service._workspace(checkpoints._read_fleet(ops).client(target["host"]))
    rows = [r for r in (doc or {}).get("workspaces", []) if isinstance(r, dict) and r.get("id") == target["workspace_id"]]
    if len(rows) != 1 or not resource_policy.norm(rows[0].get("folderPath")):
        raise OperationError("REPOSITORY_WORKSPACE_CHANGED", "exact bound workspace is unavailable", 409)
    return {"workspace_id": rows[0]["id"], "folder": rows[0]["folderPath"], "name": rows[0].get("name")}


async def preview(ops, principal, request):
    if not principal.allows("observe"):
        raise OperationError("FORBIDDEN", "published version preview requires observe", 403)
    return await source_preview(ops, request)


async def source_preview(ops, request):
    if not isinstance(request, dict) or set(request) != {"repository", "host", "workspace_id", "source_ref"}:
        raise OperationError("INVALID_PARAMS", "preview requires repository/host/workspace_id/source_ref", 422)
    target = {k: request[k] for k in ("repository", "host", "workspace_id")}
    config = config_snapshot(ops, target)
    ws = await workspace(ops, target)
    rid, sha = await published(ops, config["repository"], request["source_ref"])
    binding = digest({"config": config, "workspace": {k: ws[k] for k in ("workspace_id", "folder")}, "repository_id": rid})
    return {"target": target, "workspace": ws, "source_ref": request["source_ref"], "source_sha": sha,
            "repository_id": rid, "binding_digest": binding, "exact_ref_head_only": True,
            "preconditions": {"repository_id": rid, "binding_digest": binding}}


def capabilities(ops):
    return [{"repository": r.repository, "host": h, "workspace_id": w, "exact_ref_head_only": True}
            for r in ops.context["fleet"].config.github.repos.values() if r.sync for h, w in r.sync.bindings]


def receipt(ctx, name):
    row = ctx.service.db.execute("SELECT response FROM operation_steps WHERE operation_id=? AND name=? AND status='succeeded'",
                                 (ctx.operation_id, name)).fetchone()
    return json.loads(row[0]) if row else None


async def resolve(ctx):
    from .pm_package_handoff import guard as pm_guard
    pm_guard(ctx)
    project_context(ctx.service, ctx.target, ctx.params, ctx.preconditions, operation_id=ctx.operation_id)
    selected = await source_preview(ctx.service, {**ctx.target, "source_ref": ctx.params["source_ref"]})
    if selected["source_sha"] != ctx.params["source_sha"]:
        raise StepFailed("REPOSITORY_REF_CHANGED", "selected published head changed")
    if selected["preconditions"] != {k: ctx.preconditions[k] for k in ("repository_id", "binding_digest")}:
        raise StepFailed("REPOSITORY_BINDING_CHANGED", "reviewed repository/workspace configuration changed")
    config = config_snapshot(ctx.service, ctx.target)
    suffix = ctx.operation_id[3:15]
    clone = config["managed_root"] + "/batc-published-" + ctx.operation_id[3:]
    return {**selected, "config": config, "config_digest": digest(config), "operation_id": ctx.operation_id,
            "session_id": str(uuid.uuid5(checkpoints._SESSION_NS, ctx.operation_id)), "clone_path": clone,
            "worktree_path": clone + "/.bat-worktrees/batc-published-" + suffix, "branch": "batc/published-" + suffix,
            "markers": {"managed-clone": "true", "role": "published", "repository": config["repository"],
                "repository-id": str(selected["repository_id"]), "host": ctx.target["host"], "remote-url": config["remote_url"],
                "operation": ctx.operation_id, "binding": selected["binding_digest"]}}


def guard(ctx, plan):
    from .pm_package_handoff import guard as pm_guard
    pm_guard(ctx)
    ctx.require_controller()
    ctx.check_cancel()
    project_context(ctx.service, ctx.target, ctx.params, ctx.preconditions, operation_id=ctx.operation_id)
    if digest(config_snapshot(ctx.service, ctx.target)) != plan["config_digest"]:
        raise StepFailed("REPOSITORY_BINDING_CHANGED", "fixed repository target configuration changed")
    from .cleanup import guard as cleanup_guard
    cleanup_guard(ctx.target["host"], session_id=plan["session_id"], path=plan["worktree_path"], branch=plan["branch"])
    task_control.refuse_owned(ctx.service.context["fleet"], ctx.target["host"], plan["session_id"])
    row = registry.get(ctx.target["host"], plan["session_id"])
    if row and (row.get("start_operation_id") != ctx.operation_id or row.get("status") in registry.RETIRED
                or row.get("repository_binding") != plan["binding_digest"]):
        raise StepFailed("REPOSITORY_BINDING_CHANGED", "published start reservation changed")
    reserved = (ctx.service.get(ctx.operation_id).get("external_refs") or {}).get("repository_reservation")
    if reserved and (not row or row.get("created_at") != reserved.get("created_at")):
        raise StepFailed("REPOSITORY_BINDING_CHANGED", "published start incarnation changed")


async def frame(ctx, plan, *, at_frame=False):
    guard(ctx, plan)
    c = ctx.service.context["fleet"].client(ctx.target["host"])
    current = await workspace(ctx.service, ctx.target, c.guard_read if at_frame else None)
    if current["folder"] != plan["workspace"]["folder"]:
        raise StepFailed("REPOSITORY_WORKSPACE_CHANGED", "fixed workspace folder changed")
    if at_frame:
        root = await c.guard_read("git:getRoot", {"cwd": plan["worktree_path"]})
        log = await c.guard_read("git:log", {"cwd": plan["worktree_path"], "count": 1})
        branch = await c.guard_read("git:branch", {"cwd": plan["worktree_path"]})
        if root != plan["worktree_path"] or not log or log[0].get("hash") != plan["source_sha"] or branch != plan["branch"]:
            raise StepFailed("REPOSITORY_CARRIER_CHANGED", "fixed managed carrier HEAD/root changed")
    guard(ctx, plan)


async def host_call(ctx, plan, phase):
    runner = ctx.service.context["git_runner"]
    req = {**plan, **{k: plan["config"][k] for k in ("managed_root", "remote_url")}, "phase": phase}
    source = (resources.files(__package__) / "repository_sync_host.py").read_text()
    arg = base64.b64encode(json.dumps(req).encode()).decode()
    async def check():
        await frame(ctx, plan)
        if phase == "fetch":
            await published(ctx.service, plan["config"]["repository"], plan["source_ref"],
                            expected_id=plan["repository_id"], expected_sha=plan["source_sha"])
        ctx.require_controller()
    if not phase.startswith("read."):
        ctx.require_controller()
    token = checkpoints._LOCKED_CHECK.set(None if phase.startswith("read.") else check)
    try:
        result = json.loads(await runner.run(ctx.target["host"], "python3 -c " + shlex.quote(source) + " " + shlex.quote(arg),
                                            timeout_s=plan["config"]["sync"]["fetch_timeout_s"]))
    finally:
        checkpoints._LOCKED_CHECK.reset(token)
    if "error" in result:
        if result["error"] == "REPOSITORY_HOST_UNCERTAIN":
            raise AmbiguousOutcome("published repository host effect is unproven")
        raise NeedsAttention(result["error"], "published repository carrier requires review")
    return result["result"]


async def run(ctx):
    async def reread(_):
        return RERUN
    plan = await ctx.step("source.resolve", lambda: resolve(ctx), reconcile=reread)
    refs = artifacts.normalize_refs(ctx.service.db, ctx.params.get("artifacts", []), ctx.service.context["artifact_store"].settings)
    if refs or ctx.params.get("project_id"):
        def bind_inputs():
            project_context(ctx.service, ctx.target, ctx.params, ctx.preconditions, operation_id=ctx.operation_id)
            artifacts.reference(ctx.service.db, "operation", ctx.operation_id, refs, ctx.operation_id)
            wid = ctx.params.get("work_item_id")
            if wid:
                from . import managed_repairs
                if managed_repairs.dispatch_record(ctx.service, wid):
                    ctx.service.db.execute("INSERT OR IGNORE INTO managed_repair_launches VALUES(?,?,?)",
                                           (wid, ctx.operation_id, ctx.op["created_at"]))
                ctx.service.db.execute("""INSERT OR IGNORE INTO work_item_links
                    (work_item_id,kind,ref,linked_by,linked_at,link_operation) VALUES(?,'operation',?,?,?,?)""",
                    (wid, ctx.operation_id, ctx.actor, ctx.op["created_at"], ctx.operation_id))
                ctx.service.journal.api_event("work_item", wid, "work_item.linked",
                    {"kind": "operation", "ref": ctx.operation_id, "operation_id": ctx.operation_id}, actor=ctx.actor)
            return {"project_id": ctx.params.get("project_id"), "artifacts": refs}
        ctx.effect("published_inputs", bind_inputs, request={"artifacts": refs, "project_id": ctx.params.get("project_id")})
        ctx.set_refs(project_id=ctx.params.get("project_id"), input_manifest_digest=digest({
            "target": ctx.target, "params": ctx.params, "preconditions": ctx.preconditions}))
    ctx.set_refs(host=ctx.target["host"], session_id=plan["session_id"], repository=plan["config"]["repository"],
                 repository_id=plan["repository_id"], clone_path=plan["clone_path"], worktree_path=plan["worktree_path"],
                 branch=plan["branch"], source_sha=plan["source_sha"], source_ref=plan["source_ref"],
                 repository_binding=plan["binding_digest"])
    await native_carrier.prepare_published(ctx, plan, lambda phase: host_call(ctx, plan, phase))
    binding = {"binding_digest": plan["binding_digest"], "source_sha": plan["source_sha"]}
    context = {"host": ctx.target["host"]}
    if refs and not ctx.service.db.execute("SELECT 1 FROM operation_steps WHERE operation_id=? AND name='send'",
                                          (ctx.operation_id,)).fetchone():
        await artifacts.materialize(ctx, context, plan["clone_path"], plan["worktree_path"], plan["branch"], refs,
                                    published_binding=binding, before_transfer=lambda: frame(ctx, plan))

    from . import pm_execution_runtime
    from . import pm_maintenance_repair_runtime as repair_runtime

    async def input_frame():
        await frame(ctx, plan, at_frame=True)
        if refs:
            await artifacts.verify_materializations(ctx, context, plan["clone_path"], plan["worktree_path"],
                                                   plan["branch"], published_binding=binding)
        guard(ctx, plan)
        await repair_runtime.before_send_frame(ctx)
        await pm_execution_runtime.before_send_frame(ctx)

    async def capture_runtime(sid, text):
        await repair_runtime.capture(ctx, sid, text)
        await pm_execution_runtime.capture(ctx, sid, text)
    marker = "[batc:" + ctx.operation_id + "]"
    fields = {"start_operation_id": ctx.operation_id, "repository_binding": plan["binding_digest"],
              "repository": plan["config"]["repository"], "repository_id": plan["repository_id"],
              "published_sha": plan["source_sha"], "origin_root": plan["clone_path"], "worktree_made_by": "connector"}
    result = await checkpoints.start_in_worktree(ctx, host=ctx.target["host"], workspace=ctx.target["workspace_id"],
        agent=ctx.params.get("agent", "claude"), worktree=plan["worktree_path"], branch=plan["branch"], head=plan["source_sha"],
        title=ctx.params.get("title") or "Published " + plan["source_sha"][:12], model=ctx.params.get("model"),
        text=marker + "\n" + checkpoints._input_instructions(ctx.service.db, _execution_prompt(ctx), refs),
        marker=marker, registry_fields={}, creation_fields=fields,
        frame_check=input_frame, final_check=lambda: guard(ctx, plan),
        before_first_send=capture_runtime)
    return {**result, "host": ctx.target["host"], "workspace_id": ctx.target["workspace_id"],
            "repository": plan["config"]["repository"], "repository_id": plan["repository_id"],
            "source_ref": plan["source_ref"], "source_sha": plan["source_sha"], "worktree_path": plan["worktree_path"],
            "branch": plan["branch"], "binding_digest": plan["binding_digest"]}


def _execution_prompt(ctx):
    from .pm_package_handoff import execution_prompt
    return execution_prompt(ctx)


def carriers(ops, operations):
    for op in operations:
        if op["action"] != "repository.continue":
            continue
        rows = {r["name"]: json.loads(r["response"]) for r in ops.db.execute("SELECT name,response FROM operation_steps "
                "WHERE operation_id=? AND status='succeeded' AND name IN ('source.resolve','repository.fetch','worktree.prepare')",
                (op["operation_id"],))}
        plan = rows.get("source.resolve")
        fetched, carrier = rows.get("repository.fetch"), rows.get("worktree.prepare")
        if not plan:
            continue
        if (plan.get("operation_id") != op["operation_id"] or plan.get("target") != op["target"] or
                fetched is not None and fetched != {"clone_path": plan["clone_path"], "source_sha": plan["source_sha"], "pinned": True}):
            continue
        if fetched is None and not ops.db.execute("SELECT 1 FROM operation_steps WHERE operation_id=? AND name='repository.fetch'",
                                                  (op["operation_id"],)).fetchone():
            continue
        if carrier and carrier != {"clone_path": plan["clone_path"], "worktree_path": plan["worktree_path"],
                                   "branch": plan["branch"], "head": plan["source_sha"]}:
            continue
        yield {**plan, "carrier_proven": bool(fetched and carrier)}
