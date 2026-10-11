"""One BAT-owned PM turn through the existing durable session start engine."""
from __future__ import annotations

import asyncio
import time

from . import (
    controller_authority,
    pm_native_operations,
    pm_run_operations,
    pm_runs,
    pm_runtime,
    pm_workspace,
    registry,
    repository_sync,
)
from . import (
    pm_bat_runtime as runtime,
)
from . import (
    session_start_operations as starts,
)
from .errors import BatError, ResourceReadOnly
from .operations import RERUN, ActionDef, AmbiguousOutcome, Cancelled, OperationError, StepFailed, Uncertain

ACTION = "pm.run.bat.analyze"


def capabilities(ops, principal, workspace):
    """Selected configuration only; reading never launches or probes a model."""
    registered = ACTION in ops.actions
    authorized = (registered and (principal.admin or workspace['membership']['role'] in {'owner', 'contributor'})
        and all(principal.allows(scope) for scope in ('observe', 'manage', 'start')))
    selected, reason = None, None
    if registered:
        try:
            value = _binding(ops, workspace['workspace_id'])
            starts._tier(ops.context['fleet'], value['target']['host'])
            selected = {key: value['target'][key] for key in ('host', 'workspace_id', 'repository')}
        except OperationError as exc:
            reason = exc.code
    else:
        reason = 'action_unavailable'
    return {'adapter': runtime.ADAPTER, 'contract': runtime.PROFILE, 'action_registered': registered,
        'authorized': authorized, 'eligible_for_preflight': reason is None, 'reason': reason,
        'selected_binding': selected, 'execution_scope': 'frozen_context_only',
        'runtime_attestation_required': True, 'options_confirmed': False,
        'auth_validity': 'unverified', 'model_execution_verified': False}


def _binding(ops, wid):
    value = pm_runs.workspace_worktree(ops.db, wid)
    if not value:
        raise OperationError("PM_WORKTREE_REQUIRED", "Prepare the workspace's configured PM worktree first", 409)
    current = repository_sync.config_snapshot(ops, value["target"])
    if repository_sync.digest(current) != value["config_digest"]:
        raise OperationError("REPOSITORY_BINDING_CHANGED", "The original PM repository binding changed", 409)
    return value


def _admit(ops, principal, target, params, pre):
    pm_native_operations._values(ops, principal, target, params, pre)
    binding = _binding(ops, target["workspace_id"])
    starts._tier(ops.context["fleet"], binding["target"]["host"])
    return {"principal": pm_workspace.principal_binding(principal),
        "pm_bat_binding": binding, "pm_bat_binding_digest": pm_runs._digest(binding)}


def _check(ctx, run, *, sending=False):
    principal = pm_workspace.admitted_principal(ctx)
    pm_native_operations._scopes(principal)
    pm_run_operations.require_execution(ctx.service.journal, run["execution_authority"], run["workspace_id"])
    pm_runs.require_requester(ctx.service.db, run)
    binding = ctx.admission_binding["pm_bat_binding"]
    if (pm_runs._digest(binding) != ctx.admission_binding["pm_bat_binding_digest"]
            or _binding(ctx.service, run["workspace_id"]) != binding):
        raise OperationError("PM_BAT_BINDING_CHANGED", "The original PM workspace binding changed", 409)
    if sending:
        from .pm_lifecycle_gates import require_local
        ctx.check_cancel()
        require_local(ctx.service.db, ctx.operation_id)
        if time.time() >= run["created_at"] + ctx.params.get("timeout_s", 300):
            raise OperationError("PM_BAT_DEADLINE", "The original PM analysis deadline elapsed", 409)


class _StartContext:
    """A trusted view of the same operation, not another journal or dispatcher.

    Its source/target are server-frozen. All durable steps, cancellation, lineage
    and transport fences still belong to the original PM operation.
    """
    def __init__(self, ctx, run):
        self.parent, self.run = ctx, run
        binding = ctx.admission_binding["pm_bat_binding"]
        self.target = {"host": binding["target"]["host"], "workspace": binding["target"]["workspace_id"]}
        self.params = {"agent": "codex", "use_worktree": True, "title": "PM discussion",
            **({"model": ctx.params["model"]} if "model" in ctx.params else {})}

    def __getattr__(self, key):
        return getattr(self.parent, key)

    def require_controller(self):
        _check(self.parent, self.run, sending=True)
        return controller_authority.require_binding(self.run["execution_authority"])


def start_profile(ctx):
    if not isinstance(ctx, _StartContext):
        return None
    ctx.require_controller()
    return {"pmRuntimeProfile": runtime.PROFILE, "pmContextDigest": ctx.run["context_digest"],
        "pmTimeoutMs": ctx.parent.params.get("timeout_s", 300) * 1000}


def guard_start(ctx, plan):
    profile = plan.get("pm_runtime_profile")
    if profile is None and not isinstance(ctx, _StartContext):
        return
    if (not isinstance(ctx, _StartContext) or profile != start_profile(ctx)
            or plan["workspace_id"] != ctx.target["workspace"]
            or plan["permission_options"] != pm_runtime.readonly_start_options()):
        raise StepFailed("PM_BAT_BINDING_CHANGED", "The original dedicated PM start profile changed")


def confirm_start(ctx, plan, meta):
    if plan.get("pm_runtime_profile") is None:
        return
    if not isinstance(ctx, _StartContext):
        raise StepFailed("PM_BAT_BINDING_CHANGED", "The PM start has no original writer binding")
    value = runtime.record(ctx.service.journal, ctx.run,
        {"sessionId": plan["session_id"], "pmRuntime": meta.get("pmRuntime")}, session_id=plan["session_id"])
    if meta.get("sdkSessionId") != value["threadId"]:
        raise StepFailed("PM_BAT_BINDING_CHANGED", "BAT metadata names a different native thread")


async def _proof(ctx, run, read):
    binding = run["session_binding"]
    raw = await read("claude:get-pm-runtime", {"sessionId": binding["session_id"]})
    return runtime.record(ctx.service.journal, run, raw, session_id=binding["session_id"])


def _row(ctx, run):
    binding = run["session_binding"]
    row = controller_authority.require_entry(registry.get(binding["host"], binding["session_id"]),
        expected_incarnation=binding["incarnation"])
    if row.get("start_operation_id") != ctx.operation_id:
        raise OperationError("PM_BAT_BINDING_CHANGED", "The original BAT session creation identity changed", 409)
    return row


async def _observe(ctx, run, client):
    _check(ctx, run)
    _row(ctx, run)
    proof = await _proof(ctx, run, client.invoke)
    if run["send_cursor"]:
        # A bounded page loop yields to the ordinary operation scheduler. Stored
        # events and cursors are atomic; a later snapshot cannot repair a gap.
        for _ in range(8):
            replay = await pm_runtime.sync_session(client, run["session_binding"]["session_id"], run["cursor"])
            pm_runs.record_replay(ctx.service.journal, run["run_id"], run["generation"], replay)
            run = pm_runs.get(ctx.service.db, run["run_id"])
            if not replay["has_more"] or replay["gap"]:
                break
    _check(ctx, run)
    return run, proof


def _assessment(ctx, run):
    binding = run["session_binding"]
    launch = next(row["data"] for row in run["receipts"] if row["phase"] == "prepare" and row["status"] == "prepared")
    value = pm_runtime.assess_turn(binding["session_id"], run["send_cursor"], pm_runs.events(ctx.service.db, run["run_id"]),
        run["cursor"], binding["sdk_session_id"], gap=bool(run["gaps"]), has_more=run["has_more"],
        actual_model=binding["actual_model"], expected_prompt_digest=launch["prompt_digest"])
    return value, launch


async def _abort_evidence(ctx, run):
    plan = starts._receipt(ctx, 'source.resolve')
    started = ctx.service.db.execute("SELECT 1 FROM operation_steps WHERE operation_id=? AND name='session.start'",
                                    (ctx.operation_id,)).fetchone()
    if not plan or not started:
        return None
    try:
        raw = await ctx.service.context['fleet'].client(plan['host']).invoke('claude:get-pm-runtime', {'sessionId': plan['session_id']})
        return runtime.record_abort(ctx.service.journal, run, raw, session_id=plan['session_id'])
    except (BatError, OperationError, OSError, asyncio.TimeoutError):
        return None


def _abort(ctx, run, proof, code):
    with ctx.service.journal.tx():
        pm_runs._release(ctx.service.db, pm_runs._active(ctx.service.db, run['run_id'], run['generation']),
            'aborted', {'reason': 'Original dedicated BAT process ended without an imported reply',
                'code': code, 'process_proof': proof})


async def _send(ctx, run, client, plan, carrier):
    binding = run["session_binding"]
    launch = next(row["data"] for row in run["receipts"] if row["phase"] == "prepare" and row["status"] == "prepared")
    message_id = "batc-pm-" + run["run_id"]
    async def frame():
        _check(ctx, run, sending=True)
        _row(ctx, run)
        view = _StartContext(ctx, run)
        await starts._identity(view, plan, client.guard_read, cwd=carrier["cwd"], branch=carrier["branch"])
        value = await _proof(ctx, run, client.guard_read)
        if value.get("send") or value.get("terminal") or value.get("exit"):
            raise OperationError("PM_BAT_NOT_FRESH", "The original dedicated runtime already consumed its turn", 409)
        replay = pm_runtime.parse_replay(await client.guard_read("claude:sync-session", {"sessionId": binding["session_id"]}),
                                         binding["session_id"])
        cursor = pm_runtime.fresh_send_cursor(replay, binding["sdk_session_id"])
        pm_runs.set_send_cursor(ctx.service.journal, run["run_id"], run["generation"], cursor)
        pm_runs.receipt(ctx.service.journal, run["run_id"], run["generation"], "send", "prepared",
            {"prompt_digest": launch["prompt_digest"], "message_id": message_id, "runtime_id": value["runtimeId"]})
        _check(ctx, run, sending=True)
    def fence():
        ctx.set_refs(pm_bat_send_attempted=True)
        pm_runs.receipt(ctx.service.journal, run["run_id"], run["generation"], "send", "sent",
            {"prompt_digest": launch["prompt_digest"], "message_id": message_id})
    async def send():
        _check(ctx, run, sending=True)
        ctx.set_refs(pm_bat_send_attempted=False)
        grant = starts.resource_policy.authorize_new_session(ctx.service.context["fleet"].config.host(binding["host"]),
            binding["session_id"], folder=plan["folder"], use_worktree=True,
            git_roots={starts.resource_policy.norm(plan["folder"]): plan["origin_root"]})
        grant = starts.resource_policy.check_new_worktree(grant, ctx.service.context["fleet"].config.host(binding["host"]),
            plan["folder"], carrier["cwd"], plan["origin_root"])
        original = runtime.observations(ctx.service.db, run["run_id"])[0]
        try:
            reply = await client.invoke("claude:send-message", {"sessionId": binding["session_id"],
                "prompt": launch["prompt"], "clientMessageId": message_id, "pmRuntimeId": original["runtimeId"],
                "pmContextDigest": run["context_digest"]}, grant=grant, before_send=lambda: _check(ctx, run, sending=True),
                before_frame=frame, on_transport=fence, retry_on_disconnect=False)
        except (BatError, OSError, asyncio.TimeoutError) as exc:
            if (ctx.service.get(ctx.operation_id)["external_refs"]).get("pm_bat_send_attempted") is not False:
                raise AmbiguousOutcome("The original BAT PM input may have reached transport") from exc
            raise StepFailed("PM_BAT_UNSENT", "The original BAT PM input did not reach transport") from exc
        if not isinstance(reply, dict) or reply.get("accepted", reply.get("ok")) is not True:
            raise AmbiguousOutcome("The original BAT PM input has no confirmed send receipt")
        return {"message_id": message_id, "sent": True}
    async def recover(_):
        # Never use an empty transcript as permission to resend. Only this
        # exact operation's committed, positive pre-transport fence can do so.
        if (ctx.service.get(ctx.operation_id)["external_refs"]).get("pm_bat_send_attempted") is False:
            return RERUN
        observed = await _proof(ctx, run, client.invoke)
        sent = observed.get("send") or {}
        if (sent.get("attempted") is True and sent.get("clientMessageId") == message_id
                and sent.get("promptDigest") == launch["prompt_digest"]):
            return {"message_id": message_id, "sent": True}
        return None
    return await ctx.step("pm.bat.send", send, request={"message_id": message_id, "prompt_digest": launch["prompt_digest"]},
                          reconcile=recover)


async def _analyze(ctx):
    principal = pm_workspace.admitted_principal(ctx)
    authority = pm_run_operations.execution_authority(ctx.service.db, principal, ctx.target["workspace_id"], ctx.operation_id)
    def freeze():
        pm_native_operations._values(ctx.service, principal, ctx.target, ctx.params, ctx.preconditions)
        run = pm_runs.create(ctx.service, principal, operation_id=ctx.operation_id, **ctx.target,
            expected_version=ctx.preconditions["expected_version"], adapter=runtime.ADAPTER,
            purpose=ctx.params["purpose"], execution_authority=authority, max_chars=ctx.params.get("max_chars", 32000))
        prompt = pm_native_operations._prompt(run)
        pm_runs.receipt(ctx.service.journal, run["run_id"], run["generation"], "prepare", "prepared",
            {"prompt": prompt, "prompt_digest": pm_runtime.prompt_digest(prompt)})
        return {"run_id": run["run_id"]}
    saved = ctx.effect("pm.bat.freeze", freeze)
    run = pm_runs.get(ctx.service.db, saved["run_id"])
    ctx.set_refs(run_id=run["run_id"], workspace_id=run["workspace_id"])
    if run["state"] == "completed":
        return pm_native_operations._result(run)
    if run["state"] in {"aborted", "failed"}:
        raise OperationError("PM_BAT_STOPPED", "This original PM run ended without an imported reply", 409)
    with controller_authority.use(authority, ctx.service.journal):
        try:
            # The ordinary executor retains separate carrier/start/confirmation
            # receipts and never restarts a start with an uncertain outcome.
            view = _StartContext(ctx, run)
            carrier = await starts.run(view)
            plan = starts._receipt(view, "source.resolve")
            row = registry.get(plan["host"], plan["session_id"])
            proof = runtime.observations(ctx.service.db, run["run_id"])[0]
            pm_runs.bind_session(ctx.service.journal, run["run_id"], run["generation"], host=plan["host"],
                session_id=plan["session_id"], incarnation=row["controller_incarnation"],
                sdk_session_id=proof["threadId"], actual_model=proof["actualModel"])
            run = pm_runs.get(ctx.service.db, run["run_id"])
            client = ctx.service.context["fleet"].client(plan["host"])
            await _send(ctx, run, client, plan, carrier)
            # Read-back uses the original operation scheduler rather than a
            # second background engine or a replacement turn.
            run, _ = await _observe(ctx, pm_runs.get(ctx.service.db, run["run_id"]), client)
            value, launch = _assessment(ctx, run)
            runtime.require_completed(ctx.service.db, run, value, prompt_digest=launch["prompt_digest"],
                message_id="batc-pm-" + run["run_id"])
            _check(ctx, run)
            completed = ctx.effect("pm.bat.import", lambda: pm_runs.import_reply(ctx.service.journal,
                run["run_id"], run["generation"], content=value["content"], native_turn_id=value["native_turn_id"],
                sdk_session_id=value["sdk_session_id"], model=value["actual_model"], native_message_id=value["native_message_id"]),
                lifecycle_bookkeeping=True)
            return pm_native_operations._result(completed)
        except Uncertain:
            proof = await _abort_evidence(ctx, run)
            if proof:
                # A lost send ACK may already have a successful original native
                # terminal. Preserve its normal read/import recovery path.
                native = proof['runtime'].get('terminal') or {}
                if native.get('native') is not True or native.get('status') != 'completed':
                    _abort(ctx, run, proof, 'PM_BAT_PREFLIGHT_FAILED')
                    raise OperationError('PM_BAT_PREFLIGHT_FAILED', 'The original BAT process ended before verified analysis', 409) from None
            raise
        except (BatError, OSError, asyncio.TimeoutError, pm_runtime.RuntimeEvidenceError) as exc:
            raise Uncertain("pm.bat.observe", "Original BAT observations are unavailable; retain the writer") from exc
        except (Cancelled, OperationError, ResourceReadOnly, StepFailed) as exc:
            # An unconfirmed exit is not a failed/no-effect run. Leave the writer
            # and original operation available for read-only reconciliation.
            if run.get("session_binding"):
                try:
                    await _proof(ctx, run, ctx.service.context["fleet"].client(run["session_binding"]["host"]).invoke)
                except (BatError, OperationError, OSError, asyncio.TimeoutError):
                    pass
            if (isinstance(exc, OperationError) and exc.code == "PM_BAT_COMPLETION_UNPROVEN"
                    and not runtime.death_proof(ctx.service.db, run)):
                raise Uncertain("pm.bat.observe", "The original BAT PM turn or process exit still needs observation") from None
            proof = runtime.death_proof(ctx.service.db, run) or await _abort_evidence(ctx, run)
            if proof:
                _abort(ctx, run, proof, getattr(exc, 'code', 'CANCELLED'))
                raise
            start = ctx.service.db.execute("SELECT 1 FROM operation_steps WHERE operation_id=? AND name='session.start'",
                                           (ctx.operation_id,)).fetchone()
            if start is None:
                pm_runs.fail(ctx.service.journal, run["run_id"], run["generation"], "No native BAT process was started", before_send=True)
                raise
            raise Uncertain("pm.bat.observe", "The original dedicated BAT process exit is unproven") from None


def install(ops):
    pm_runs.schema(ops.journal)
    runtime.schema(ops.journal)
    if ACTION not in ops.actions:
        ops.register(ActionDef(ACTION, "manage", "Analyze a frozen PM discussion in one dedicated BAT turn", _analyze,
            _admit, ("workspace_id", "thread_id"), authorize_existing=pm_native_operations._authorize_existing))
