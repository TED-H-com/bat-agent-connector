"""Durable professional PM work packages and explicitly reported runtime limits.

Preparing a package freezes context; it does not start a model, adopt a plan or
dispatch coding work. BAT's current file sandbox does not establish an external
MCP tool boundary, so autonomous PhaseProfile execution remains unavailable.
"""
from __future__ import annotations

from . import controller_authority, pm_discussions, pm_runs, pm_workspace
from .api_auth import Principal
from .operations import RERUN, ActionDef, Cancelled, OperationError, StepFailed


def execution_authority(db, principal, workspace_id, operation_id):
    identity = pm_runs.execution_identity(db, workspace_id)
    authority = controller_authority.binding(db, Principal(identity["actor"], principal.scopes))
    return {**authority, "pm_workspace_id": workspace_id, "pm_operation_id": operation_id,
            "pm_requester": pm_workspace.principal_binding(principal)}


def require_execution(journal, authority, workspace_id):
    """A PM actor name alone is not a grant to the workspace writer."""
    operation_id = authority.get("pm_operation_id")
    requester = authority.get("pm_requester")
    if (not operation_id or not requester or authority.get("pm_workspace_id") != workspace_id
            or authority.get("actor") != "pm:" + workspace_id):
        raise OperationError("PM_WRITER_REQUIRED", "The active PM writer binding is required", 403)
    run = pm_runs.get_by_operation(journal.db, operation_id)
    if not run or run["workspace_id"] != workspace_id or run["execution_authority"] != authority:
        raise OperationError("PM_WRITER_CHANGED", "The PM execution binding changed", 409)
    pm_runs.require_writer(journal, run["run_id"], run["generation"])
    principal = Principal(requester["actor"], frozenset(requester["scopes"]), admin=requester["admin"])
    if not principal.allows("manage"):
        raise OperationError("FORBIDDEN", "Preparing PM work requires manage", 403)
    pm_workspace.require_member(journal.db, principal, workspace_id, write=True)
    return run


def _values(ops, principal, target, params, pre):
    if (set(target) != {"workspace_id", "thread_id"}
            or set(params) - {"purpose", "max_chars", "repository_binding"}
            or set(pre) != {"expected_version"}):
        raise OperationError("INVALID_PARAMS", "Supply a discussion, purpose and expected thread version", 422)
    workspace = pm_workspace.require_member(ops.db, principal, target["workspace_id"], write=True)
    thread = pm_discussions._thread(ops.db, target["workspace_id"], target["thread_id"])
    if type(pre["expected_version"]) is not int or pre["expected_version"] < 1:
        raise OperationError("INVALID_PARAMS", "expected_version must be a positive integer", 422)
    if thread["version"] != pre["expected_version"]:
        raise OperationError("VERSION_CONFLICT", "Read the current discussion before preparing another package", 409)
    purpose = params.get("purpose")
    if not isinstance(purpose, str) or not purpose.strip() or len(purpose) > 4000 or "\x00" in purpose:
        raise OperationError("INVALID_PARAMS", "purpose must contain 1-4000 characters", 422)
    budget = params.get("max_chars", 32000)
    if type(budget) is not int or not 2000 <= budget <= 64000:
        raise OperationError("INVALID_PARAMS", "max_chars must be 2000-64000", 422)
    if "repository_binding" in params:
        from . import repository_sync
        selection = params["repository_binding"]
        fields = {"repository", "host", "workspace_id", "source_ref", "source_sha", "repository_id", "binding_digest"}
        if not isinstance(selection, dict) or set(selection) != fields:
            raise OperationError("INVALID_PARAMS", "Supply the exact configured repository preview selection", 422)
        if not principal.allows("start"):
            raise OperationError("FORBIDDEN", "Creating the PM worktree also requires start", 403)
        repository_sync.configured(ops, {key: selection[key] for key in ("repository", "host", "workspace_id")})
        if selection["repository"].lower() not in {repo.lower() for repo in workspace["repositories"]}:
            raise OperationError("PM_REPOSITORY_NOT_BOUND", "The repository is not listed in this PM workspace", 409)
        repository_sync.check_ref(selection["source_ref"])
        if (not isinstance(selection["source_sha"], str) or not repository_sync.SHA.fullmatch(selection["source_sha"])
                or type(selection["repository_id"]) is not int or selection["repository_id"] < 1
                or not isinstance(selection["binding_digest"], str)
                or not repository_sync.DIGEST.fullmatch(selection["binding_digest"])):
            raise OperationError("INVALID_PARAMS", "A full commit, repository identity and binding digest are required", 422)
    return thread


def _admit(ops, principal, target, params, pre):
    _values(ops, principal, target, params, pre)
    return {"principal": pm_workspace.principal_binding(principal)}


def _authorize_existing(ops, principal, op, verb):
    pm_workspace.require_member(ops.db, principal, op["target"]["workspace_id"], write=True)
    if not principal.allows("manage") or ("repository_binding" in op["params"] and not principal.allows("start")):
        raise OperationError("FORBIDDEN", "The original package preparation scopes are required", 403)
    if verb != "replay" and principal.actor != op["actor"]:
        raise OperationError("FORBIDDEN", "Only the original requester may control package preparation", 403)


async def _prepare(ctx):
    try:
        return await _prepare_package(ctx)
    except (OperationError, StepFailed, Cancelled):
        run = pm_runs.get_by_operation(ctx.service.db, ctx.operation_id)
        uncertain = ctx.service.db.execute("""SELECT 1 FROM operation_steps WHERE operation_id=?
            AND status IN ('started','uncertain')""", (ctx.operation_id,)).fetchone()
        if run and run["state"] not in {"prepared_manual", "failed", "completed", "aborted"} and not uncertain:
            pm_runs.fail(ctx.service.journal, run["run_id"], run["generation"], "preparation_stopped", before_send=True)
        raise


async def _prepare_package(ctx):
    principal = pm_workspace.admitted_principal(ctx)
    authority = execution_authority(ctx.service.db, principal, ctx.target["workspace_id"], ctx.operation_id)

    def freeze():
        _values(ctx.service, principal, ctx.target, ctx.params, ctx.preconditions)
        return pm_runs.create(ctx.service, principal, operation_id=ctx.operation_id,
            **ctx.target, expected_version=ctx.preconditions["expected_version"], adapter="bat.codex",
            purpose=ctx.params["purpose"], execution_authority=authority, max_chars=ctx.params.get("max_chars", 32000))

    run = ctx.effect("pm.context.freeze", freeze)
    ctx.set_refs(run_id=run["run_id"], workspace_id=run["workspace_id"])
    existing = pm_runs.get(ctx.service.db, run["run_id"])
    if existing["state"] == "prepared_manual":
        return _result(existing)
    binding, snapshot = None, None
    if "repository_binding" in ctx.params:
        from . import pm_worktree
        async def check():
            ctx.check_cancel()
            require_execution(ctx.service.journal, authority, run["workspace_id"])
            if not principal.allows("start"):
                raise OperationError("FORBIDDEN", "PM worktree creation requires start", 403)
        with controller_authority.use(authority, ctx.service.journal):
            await check()
            async def select():
                prior = pm_runs.workspace_worktree(ctx.service.db, run["workspace_id"])
                if prior:
                    selection = ctx.params["repository_binding"]
                    pm_worktree.validate_selection(ctx.service, selection)
                    if (any(prior["target"][key] != selection[key] for key in ("repository", "host", "workspace_id"))
                            or any(prior[key] != selection[key] for key in ("source_ref", "source_sha", "repository_id", "binding_digest"))):
                        raise OperationError("PM_WORKTREE_BOUND", "Use the original PM worktree binding and revision", 409)
                    return prior
                return await pm_worktree.select(ctx.service, run["workspace_id"], authority,
                    ctx.params["repository_binding"], operation_id=ctx.operation_id)
            async def reread(_):
                return RERUN
            plan = await ctx.step("pm.worktree.select", select, reconcile=reread)
            ctx.effect("pm.worktree.bind", lambda: pm_runs.bind_worktree(
                ctx.service.journal, run["run_id"], run["generation"], plan))
            await pm_worktree.ensure(ctx, plan, check=check)
            binding = plan
            snapshot = await pm_worktree.write_snapshot(ctx, plan, run["run_id"], "context",
                {"context": run["context"], "context_digest": run["context_digest"], "purpose": run["purpose"]}, check=check)
    result = ctx.effect("pm.context.prepared", lambda: pm_runs.complete_preparation(
        ctx.service.journal, run["run_id"], run["generation"], worktree_binding=binding, snapshot=snapshot))
    return _result(result)


def _result(run):
    return {"run_id": run["run_id"], "workspace_id": run["workspace_id"], "thread_id": run["thread_id"],
            "state": run["state"], "context_digest": run["context_digest"], "runtime_started": False,
            "execution_authorized": False, "runtime_reason": "external_tool_policy_unverified"}


def install(ops):
    pm_runs.schema(ops.journal)
    if "pm.run.prepare" not in ops.actions:
        ops.register(ActionDef("pm.run.prepare", "manage", "Freeze a professional PM discussion work package",
            _prepare, _admit, ("workspace_id", "thread_id"), authorize_existing=_authorize_existing))
