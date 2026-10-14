"""Materialize a fixed engineering package in the original owned PM Git chain."""
from __future__ import annotations

import asyncio
import json
import time
from contextvars import ContextVar

from . import (
    artifacts,
    controller_authority,
    pm_discussions,
    pm_package_handoff,
    pm_plans,
    pm_read_cache,
    pm_revision_git,
    pm_run_operations,
    pm_runs,
    pm_source_lineage,
    pm_spec_format,
    pm_work_packages,
    pm_workspace,
    pm_worktree,
)
from .errors import ResourceReadOnly
from .operations import RERUN, ActionDef, Cancelled, OperationError, OperationService, StepFailed, Uncertain
from .pm_release_baselines import _snapshot_read

ACTION = "pm.spec.materialize"
_SOURCE_READ = ContextVar("pm_spec_source_read", default=None)


def _error(code, message, status=409):
    return OperationError(code, message, status)


def schema(journal):
    with journal.tx():
        journal.db.execute("""CREATE TABLE IF NOT EXISTS pm_spec_materializations (
            sequence INTEGER PRIMARY KEY AUTOINCREMENT, materialization_id TEXT NOT NULL UNIQUE,
            workspace_id TEXT NOT NULL, package_id TEXT NOT NULL UNIQUE,
            document_digest TEXT NOT NULL, document TEXT NOT NULL,
            artifact_ref TEXT NOT NULL, git_receipt TEXT NOT NULL,
            operation_id TEXT NOT NULL UNIQUE, actor TEXT NOT NULL, created_at REAL NOT NULL)""")
        for verb in ("UPDATE", "DELETE"):
            journal.db.execute(f"CREATE TRIGGER IF NOT EXISTS pm_spec_materializations_{verb.lower()}_immutable "
                f"BEFORE {verb} ON pm_spec_materializations BEGIN SELECT RAISE(ABORT, 'PM spec materialization is immutable'); END")


def capabilities():
    return {"version": 1, "action": ACTION, "immutable": True, "owned_git_chain": True,
            "max_generated_bytes": 512_000, "openspec_cli_compatibility": "not_claimed", "execution_authorized": False}


def _scopes(principal):
    if any(not principal.allows(scope) for scope in ("observe", "manage", "start")):
        raise _error("FORBIDDEN", "Spec materialization requires observe, manage and start scopes", 403)


def _selected(ops, principal, target, params, pre):
    _scopes(principal)
    if (set(target) != {"workspace_id", "package_id"} or params
            or set(pre) != {"expected_package_digest", "expected_scope_revision"}
            or not isinstance(pre["expected_package_digest"], str)
            or not isinstance(pre["expected_scope_revision"], str)):
        raise _error("INVALID_PARAMS", "Select the exact package digest and adopted scope revision", 422)
    ref = {**target, "document_digest": pre["expected_package_digest"]}
    package = pm_package_handoff._package(ops, principal, pm_package_handoff._ref(ref), write=True)
    pm_package_handoff._ready(ops, principal, ref)
    accepted = pm_plans.accepted_plan(ops, principal, target["workspace_id"])["plan"]
    if accepted["adoption"]["scope_revision"] != pre["expected_scope_revision"]:
        raise _error("PM_SCOPE_CHANGED", "Read the currently adopted scope before materialization")
    plan = pm_runs.workspace_worktree(ops.db, target["workspace_id"])
    if plan is None:
        raise _error("PM_WORKTREE_REQUIRED", "Prepare the workspace's owned PM carrier first")
    return package, plan


def _admit(ops, principal, target, params, pre):
    package, plan = _selected(ops, principal, target, params, pre)
    if ops.db.execute("SELECT 1 FROM pm_spec_materializations WHERE package_id=?", (package["package_id"],)).fetchone():
        raise _error("PM_SPEC_ALREADY_MATERIALIZED", "Use the original immutable materialization for this package")
    document = pm_spec_format.document(ops.db, package)
    # Validate the complete file bound before reserving a writer or artifact.
    pm_spec_format.files(document, "pmsp_" + "0" * 32, {"artifact_id": "art_" + "0" * 32, "revision": 1,
                                                       "digest": pm_spec_format.digest(document)})
    return {"principal": pm_workspace.principal_binding(principal), "git_binding_id": pm_revision_git.binding_id(plan),
            "pm_spec_materialization": {"package_ref": document["package_ref"], "document_digest": pm_spec_format.digest(document),
                "operation_ids": [row["operation_id"] for row in document["source_chain"]]},
            "pm_lineage": {"workspace_id": target["workspace_id"]}}


def _source_visible(db, principal, operation):
    try:
        # Read surfaces can pass only the operation identity/action/target.
        # Admission evidence always comes from the canonical record, never a
        # caller's projection, even when its action already matches.
        row = pm_read_cache.operation_row(db, operation.get("operation_id"))
        operation = OperationService._decode(row) if row else {}
        if operation.get("action") != ACTION:
            return False
        fixed = operation["external_refs"]["admission_binding"]["pm_spec_materialization"]
        ref = pm_package_handoff._ref(fixed["package_ref"])
        package = pm_work_packages._record(db, ref["workspace_id"], ref["package_id"])
        document = pm_spec_format.document(db, package)
        operations = [row["operation_id"] for row in document["source_chain"]]
        return (package["document_digest"] == ref["document_digest"]
                and operations == fixed["operation_ids"]
                and pm_work_packages.operation_visible(db, principal, {"operation_id": package["operation_id"]})
                and all(pm_source_lineage.visible(db, principal, "operation", identity) for identity in operations)
                and pm_spec_format.digest(document) == fixed["document_digest"])
    except (OperationError, KeyError, TypeError, ValueError):
        return False


def source_visible(db, principal, operation):
    # Immutable temporal ancestry is a DAG. Memoize only this synchronous read,
    # so shared ancestors are not expanded exponentially and corrupt cycles fail
    # closed. No permission result survives into a later request or frame.
    state = _SOURCE_READ.get()
    token = None
    if state is None:
        state = ({}, set())
        token = _SOURCE_READ.set(state)
    results, active = state
    key = (id(db), principal.actor, tuple(sorted(principal.scopes)), principal.admin,
           principal.credential_id, operation.get("operation_id"))
    try:
        if key in active:
            pm_read_cache.refuse()
            return False
        if key in results:
            return results[key]
        if len(results) + len(active) >= pm_source_lineage.MAX_ROOTS:
            pm_read_cache.refuse()
            return False
        active.add(key)
        try:
            # Cycle and root-budget refusals must not reach the request read memo either.
            with pm_read_cache.guarded():
                results[key] = _source_visible(db, principal, operation)
        finally:
            active.remove(key)
        return results[key]
    finally:
        if token is not None:
            _SOURCE_READ.reset(token)


operation_visible = source_visible


def _authorize_existing(ops, principal, operation, verb):
    _scopes(principal)
    pm_workspace.require_member(ops.db, principal, operation["target"]["workspace_id"], write=True)
    if not source_visible(ops.db, principal, operation):
        raise _error("NOT_FOUND", "Original spec sources are unavailable", 404)
    if verb != "replay" and principal.actor != operation["actor"]:
        raise _error("FORBIDDEN", "Only the original requester may control this materialization", 403)


def _record(db, wid, identity):
    if not isinstance(identity, str) or not pm_spec_format.MATERIALIZATION.fullmatch(identity):
        raise _error("NOT_FOUND", "Spec materialization not found", 404)
    row = db.execute("SELECT * FROM pm_spec_materializations WHERE workspace_id=? AND materialization_id=?", (wid, identity)).fetchone()
    if row is None:
        raise _error("NOT_FOUND", "Spec materialization not found", 404)
    value = dict(row)
    for key in ("document", "artifact_ref", "git_receipt"):
        value[key] = json.loads(value[key])
    if pm_spec_format.digest(value["document"]) != value["document_digest"]:
        raise _error("PM_SPEC_CORRUPT", "The immutable spec digest does not match")
    return value


def _read_size(value):
    return len(json.dumps(value, ensure_ascii=False, allow_nan=False).encode())


def _bounded(value):
    if _read_size(value) > 2_000_000:
        raise _error("PM_SPEC_READ_TOO_LARGE", "Whole materialization exceeds the read budget", 413)
    return value


@_snapshot_read
def get_materialization(ops, principal, workspace_id, materialization_id):
    if not principal.allows("observe"):
        raise _error("FORBIDDEN", "Spec reads require observe scope", 403)
    pm_workspace.require_member(ops.db, principal, workspace_id)
    value = _record(ops.db, workspace_id, materialization_id)
    if not source_visible(ops.db, principal, {"operation_id": value["operation_id"]}):
        raise _error("NOT_FOUND", "Original spec sources are unavailable", 404)
    return _bounded({"version": 1, "workspace_id": workspace_id, "materialization": value})


@_snapshot_read
def list_materializations(ops, principal, workspace_id, *, package_id=None, after=0, limit=20):
    if not principal.allows("observe"):
        raise _error("FORBIDDEN", "Spec reads require observe scope", 403)
    pm_workspace.require_member(ops.db, principal, workspace_id)
    if (type(after) is not int or not 0 <= after <= 2**63 - 1
            or type(limit) is not int or not 1 <= limit <= 50
            or package_id is not None and (not isinstance(package_id, str) or not pm_work_packages.PACKAGE.fullmatch(package_id))):
        raise _error("INVALID_PARAMS", "Select an exact package, a signed 64-bit cursor and a limit of 1–50", 422)
    rows = ops.db.execute("SELECT * FROM pm_spec_materializations WHERE workspace_id=? AND sequence>? "
        "AND (? IS NULL OR package_id=?) ORDER BY sequence LIMIT ?", (workspace_id, after, package_id, package_id, limit + 1)).fetchall()
    result = {"version": 1, "workspace_id": workspace_id, "materializations": [],
              "next_after": None, "source_visibility_filtered": False}
    scanned = after
    for row in rows[:limit]:
        if not source_visible(ops.db, principal, {"operation_id": row["operation_id"]}):
            scanned = row["sequence"]
            result["source_visibility_filtered"] = True
            continue
        value = {key: value for key, value in _record(ops.db, workspace_id, row["materialization_id"]).items() if key != "document"}
        candidate = {**result, "materializations": [*result["materializations"], value], "next_after": 2**63 - 1}
        if _read_size(candidate) > 2_000_000:
            if scanned == after:
                raise _error("PM_SPEC_READ_TOO_LARGE", "Whole materialization exceeds the read budget", 413)
            result["next_after"] = scanned
            return _bounded(result)
        result["materializations"].append(value)
        scanned = row["sequence"]
    result["next_after"] = scanned if len(rows) > limit else None
    return _bounded(result)


def _result(record):
    return {"status": "materialized", "execution_authorized": False,
            **{key: record[key] for key in ("workspace_id", "materialization_id", "package_id", "document_digest", "artifact_ref", "git_receipt", "operation_id")}}


async def _run(ctx):
    store = ctx.service.context["artifact_store"]
    try:
        return await _materialize(ctx)
    except (OperationError, StepFailed, Cancelled, ResourceReadOnly) as error:
        run = pm_runs.get_by_operation(ctx.service.db, ctx.operation_id)
        uncertain = ctx.service.db.execute("SELECT 1 FROM operation_steps WHERE operation_id=? AND status IN ('started','uncertain') "
            "AND name NOT IN ('pm.spec.freeze','pm.spec.record')", (ctx.operation_id,)).fetchone()
        if isinstance(error, Cancelled) and uncertain:
            raise Uncertain("pm.spec.materialize", "Cancellation is waiting for the original materialization receipts") from None
        if run and run["state"] not in pm_runs.TERMINAL and not uncertain:
            pm_runs.complete_service(ctx.service.journal, run["run_id"], run["generation"],
                {"status": "not_materialized", "reason": "cancelled" if isinstance(error, Cancelled) else "materialization_stopped"})
        raise
    finally:
        asyncio.get_running_loop().call_soon(store.schedule_reap, ctx.operation_id)


async def _materialize(ctx):
    principal = pm_workspace.admitted_principal(ctx)
    wid, identity = ctx.target["workspace_id"], "pmsp_" + ctx.operation_id[3:]
    saved = ctx.service.db.execute("SELECT materialization_id FROM pm_spec_materializations WHERE operation_id=?", (ctx.operation_id,)).fetchone()
    if saved:
        return _result(_record(ctx.service.db, wid, identity))
    authority = pm_run_operations.execution_authority(ctx.service.db, principal, wid, ctx.operation_id)

    def freeze():
        if ctx.service.db.execute("SELECT 1 FROM pm_spec_materializations WHERE package_id=?", (ctx.target["package_id"],)).fetchone():
            raise _error("PM_SPEC_ALREADY_MATERIALIZED", "Use the original immutable materialization for this package")
        package, plan = _selected(ctx.service, principal, ctx.target, ctx.params, ctx.preconditions)
        document = pm_spec_format.document(ctx.service.db, package)
        if (pm_spec_format.digest(document) != ctx.admission_binding["pm_spec_materialization"]["document_digest"]
                or pm_revision_git.binding_id(plan) != ctx.admission_binding["git_binding_id"]):
            raise _error("PM_SPEC_SOURCE_CHANGED", "Original spec or PM carrier identity changed")
        revision = pm_plans._revision(ctx.service.db, wid, package["document"]["context"]["plan_ref"]["revision_id"])
        thread = pm_discussions._thread(ctx.service.db, wid, revision["thread_id"])
        run = pm_runs.create(ctx.service, principal, operation_id=ctx.operation_id, workspace_id=wid,
            thread_id=thread["thread_id"], expected_version=thread["version"], adapter="connector.git",
            purpose="Materialize spec " + package["package_id"], execution_authority=authority)
        pm_runs.bind_worktree(ctx.service.journal, run["run_id"], run["generation"], plan)
        previous = pm_plans.latest_git_receipt(ctx.service.db, wid)
        return {"run_id": run["run_id"], "generation": run["generation"], "plan": plan,
                "document": document, "expected_parent": previous["commit_sha"] if previous else plan["source_sha"]}

    fixed = ctx.effect("pm.spec.freeze", freeze)
    ctx.set_refs(workspace_id=wid, run_id=fixed["run_id"], materialization_id=identity)

    async def check():
        ctx.check_cancel()
        pm_run_operations.require_execution(ctx.service.journal, authority, wid)
        _selected(ctx.service, principal, ctx.target, ctx.params, ctx.preconditions)
        if not source_visible(ctx.service.db, principal, ctx.op):
            raise _error("PM_SPEC_SOURCE_UNAVAILABLE", "Original spec sources were withdrawn", 404)

    document, store = fixed["document"], ctx.service.context["artifact_store"]
    raw = pm_spec_format.canonical(document).encode()
    artifact_ref = {"artifact_id": "art_" + ctx.operation_id[3:], "revision": 1, "digest": pm_spec_format.digest(document)}
    files = pm_spec_format.files(document, identity, artifact_ref)
    # Local snapshot storage uses the established durable artifact protocol. Its
    # exact bytes are source-gated even before a Git materialization is complete.
    async def reserve():
        await check()
        return store.reserve_revision(ctx, {}, {"display_name": identity + ".json", "media_type": "application/json",
            "size_bytes": len(raw), "expected_digest": artifact_ref["digest"]}, {})

    async def reread(_):
        row = ctx.service.db.execute("SELECT * FROM artifact_uploads WHERE operation_id=?", (ctx.operation_id,)).fetchone()
        return dict(row) if row else RERUN

    row = await ctx.step("upload.reserve", reserve, reconcile=reread)
    row = dict(ctx.service.db.execute("SELECT * FROM artifact_uploads WHERE operation_id=?", (ctx.operation_id,)).fetchone())
    existing = store.published(row) if row["attempt"] else None
    if not existing and row["receive_state"] != "complete":
        await check()
        reader = asyncio.StreamReader()
        reader.feed_data(raw)
        reader.feed_eof()
        await store.receive_reserved(ctx.operation_id, row, reader, len(raw), ctx.actor)
        row = dict(ctx.service.db.execute("SELECT * FROM artifact_uploads WHERE operation_id=?", (ctx.operation_id,)).fetchone())
    # Before a new publish, validate scope/work associations as well as the
    # OpContext's lifecycle/source barrier. Completed publish only replays.
    published = ctx.service.db.execute("SELECT status FROM operation_steps WHERE operation_id=? AND name='upload.publish'", (ctx.operation_id,)).fetchone()
    if not existing and (published is None or published[0] == "failed"):
        await check()
    await artifacts.finish_revision(_ArtifactContext(ctx, check), row, existing=existing, event="artifact.spec_snapshot")
    if store.read_content(artifact_ref["artifact_id"], 1) != raw:
        raise _error("PM_SPEC_ARTIFACT_CHANGED", "The frozen specification artifact bytes changed")

    with controller_authority.use(authority, ctx.service.journal):
        started = ctx.service.db.execute("SELECT 1 FROM operation_steps WHERE operation_id=? AND name='pm.revision.commit'", (ctx.operation_id,)).fetchone()
        if not started:
            await pm_worktree.ensure(ctx, fixed["plan"], check=check)
        receipt = await pm_revision_git.commit_revision(ctx, fixed["plan"], files, expected_parent=fixed["expected_parent"], check=check)

    def record():
        # Both external receipts have already been proven. This records the
        # original fact; later withdrawal cannot turn it into a new write.
        ctx.service.db.execute("INSERT INTO pm_spec_materializations(materialization_id,workspace_id,package_id,document_digest,document,artifact_ref,git_receipt,operation_id,actor,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (identity, wid, ctx.target["package_id"], artifact_ref["digest"], raw.decode(), pm_spec_format.canonical(artifact_ref),
             pm_spec_format.canonical(receipt), ctx.operation_id, ctx.actor, time.time()))
        result = _result(_record(ctx.service.db, wid, identity))
        pm_runs.complete_service(ctx.service.journal, fixed["run_id"], fixed["generation"], result)
        ctx.service.journal.api_event("pm_workspace", wid, "pm.spec.materialized", result, actor=ctx.actor)
        return result

    return ctx.effect("pm.spec.record", record, receipt_only=True)


class _ArtifactContext:
    """Check current intent only for new artifact IO; original receipts replay."""
    def __init__(self, ctx, check):
        self.ctx, self.check = ctx, check

    def __getattr__(self, name):
        return getattr(self.ctx, name)

    async def step(self, name, fn, **kwargs):
        async def guarded():
            await self.check()
            return await fn()
        return await self.ctx.step(name, guarded, **kwargs)


def install(ops):
    schema(ops.journal)
    if ACTION not in ops.actions:
        ops.register(ActionDef(ACTION, "manage", "Materialize one fixed package in the owned PM spec carrier", _run, _admit,
            ("workspace_id", "package_id"), authorize_existing=_authorize_existing))
