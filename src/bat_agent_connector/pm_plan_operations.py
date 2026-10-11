"""Adopt an explicit PM revision through the original owned Git transaction."""
from __future__ import annotations

import json

from . import (
    controller_authority,
    pm_discussions,
    pm_plans,
    pm_revision_git,
    pm_run_operations,
    pm_runs,
    pm_workspace,
    pm_worktree,
)
from .errors import ResourceReadOnly
from .operations import ActionDef, Cancelled, OperationError, StepFailed, Uncertain


def _binding(ops, workspace_id):
    plan = pm_runs.workspace_worktree(ops.db, workspace_id)
    if plan is None:
        raise OperationError("PM_WORKTREE_REQUIRED", "Prepare the workspace's owned PM worktree before adopting a plan", 409)
    return plan


def _scopes(principal):
    if any(not principal.allows(scope) for scope in ("observe", "manage", "start")):
        raise OperationError("FORBIDDEN", "Plan adoption requires observe, manage and start scopes", 403)


def _admit(ops, principal, target, params, pre):
    _scopes(principal)
    admitted = pm_plans.prepare_adoption(ops, principal, target, params, pre)
    plan = _binding(ops, target["workspace_id"])
    return {**admitted, "git_binding_id": pm_revision_git.binding_id(plan)}


def _authorize_existing(ops, principal, op, verb):
    pm_plans.authorize_existing(ops, principal, op, verb)
    _scopes(principal)
    if verb != "replay" and principal.actor != op["actor"]:
        raise OperationError("FORBIDDEN", "Only the original requester may control this adoption", 403)


def _git_receipt(ctx):
    saved = ctx.service.db.execute("SELECT receipt FROM pm_plan_git_receipts WHERE operation_id=?", (ctx.operation_id,)).fetchone()
    return json.loads(saved[0]) if saved else None


async def _adopt(ctx):
    try:
        return await _adopt_plan(ctx)
    except (OperationError, StepFailed, Cancelled, ResourceReadOnly) as error:
        run = pm_runs.get_by_operation(ctx.service.db, ctx.operation_id)
        uncertain = ctx.service.db.execute("""SELECT 1 FROM operation_steps WHERE operation_id=?
            AND status IN ('started','uncertain')
            AND name NOT IN ('pm.plan.freeze','pm.plan.git-receipt','pm.plan.accepted')""", (ctx.operation_id,)).fetchone()
        if isinstance(error, Cancelled) and uncertain:
            raise Uncertain("pm.plan.adopt", "Cancellation is waiting for the original Git result") from None
        if run and run["state"] not in pm_runs.TERMINAL and not uncertain:
            pm_runs.complete_service(ctx.service.journal, run["run_id"], run["generation"],
                {"status": "not_adopted", "reason": "cancelled" if isinstance(error, Cancelled) else "adoption_stopped",
                 "git_receipt": _git_receipt(ctx), "execution_authorized": False})
        raise


async def _adopt_plan(ctx):
    principal = pm_workspace.admitted_principal(ctx)
    workspace_id = ctx.target["workspace_id"]
    authority = pm_run_operations.execution_authority(ctx.service.db, principal, workspace_id, ctx.operation_id)

    def freeze():
        _admit(ctx.service, principal, ctx.target, ctx.params, ctx.preconditions)
        plan = _binding(ctx.service, workspace_id)
        if pm_revision_git.binding_id(plan) != ctx.admission_binding["git_binding_id"]:
            raise OperationError("PM_GIT_BINDING_CHANGED", "The original PM worktree binding changed", 409)
        revision = pm_plans._revision(ctx.service.db, workspace_id, ctx.target["revision_id"])
        thread = pm_discussions._thread(ctx.service.db, workspace_id, revision["thread_id"])
        run = pm_runs.create(ctx.service, principal, operation_id=ctx.operation_id,
            workspace_id=workspace_id, thread_id=revision["thread_id"], expected_version=thread["version"],
            adapter="connector.git", purpose="Adopt plan " + revision["revision_id"], execution_authority=authority)
        pm_runs.bind_worktree(ctx.service.journal, run["run_id"], run["generation"], plan)
        previous = pm_plans.latest_git_receipt(ctx.service.db, workspace_id)
        return {"run_id": run["run_id"], "generation": run["generation"], "plan": plan,
                "expected_parent": previous["commit_sha"] if previous else plan["source_sha"]}

    fixed = ctx.effect("pm.plan.freeze", freeze)
    ctx.set_refs(run_id=fixed["run_id"], workspace_id=workspace_id, revision_id=ctx.target["revision_id"])

    # Replay the already committed journal decision before any current permission
    # or cancellation check. No host read is needed for that immutable fact.
    saved = ctx.service.db.execute("SELECT result FROM pm_plan_adoptions WHERE operation_id=?", (ctx.operation_id,)).fetchone()
    if saved:
        return json.loads(saved[0])

    async def check():
        ctx.check_cancel()
        _scopes(principal)
        pm_run_operations.require_execution(ctx.service.journal, authority, workspace_id)
        pm_workspace.require_member(ctx.service.db, principal, workspace_id, decide=True)

    receipt = _git_receipt(ctx)
    if receipt is None:
        with controller_authority.use(authority, ctx.service.journal):
            started = ctx.service.db.execute("SELECT 1 FROM operation_steps WHERE operation_id=? AND name='pm.revision.commit'",
                                            (ctx.operation_id,)).fetchone()
            # Once Git may have run, go straight to its original readback. A new
            # carrier step must not block reconciliation after cancellation.
            if not started:
                await pm_worktree.ensure(ctx, fixed["plan"], check=check)
            receipt = await pm_worktree.commit_revision(ctx, fixed["plan"],
                pm_plans.revision_files(ctx.service.db, workspace_id, ctx.target["revision_id"]),
                expected_parent=fixed["expected_parent"], check=check)
        ctx.effect("pm.plan.git-receipt", lambda: pm_plans.record_git_receipt(ctx, receipt), receipt_only=True)

    def accept():
        _scopes(principal)
        pm_run_operations.require_execution(ctx.service.journal, authority, workspace_id)
        result = pm_plans.adopt_revision(ctx)
        pm_runs.complete_service(ctx.service.journal, fixed["run_id"], fixed["generation"], result)
        return result

    # This is a new decision, not merely bookkeeping. A known Git commit remains
    # recorded even when cancellation or loss of owner authority prevents adoption.
    return ctx.effect("pm.plan.accepted", accept)


def install(ops):
    pm_plans.install(ops)
    pm_runs.schema(ops.journal)
    if "pm.plan.adopt" not in ops.actions:
        ops.register(ActionDef("pm.plan.adopt", "manage", "Adopt an exact project plan with verified Git evidence",
            _adopt, _admit, ("workspace_id", "revision_id"), authorize_existing=_authorize_existing))
