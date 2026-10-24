"""PM workspace membership in the existing journal, separate from BAT workspaces.

No legacy project/session is adopted by name, repository or path. Controller IDs
identify a service-side actor namespace; this module does not grant session control.
Accepted intent keeps its authenticated credential authority, but membership is
checked again at the local effect. Revoking a token prevents future requests;
removing membership also prevents pending, unapplied workspace mutations.
"""
from __future__ import annotations

import json
import re
import time
import uuid

from .api_auth import Principal
from .operations import ActionDef, OpContext, OperationError

ROLES = ("owner", "contributor", "reader")
_ACTOR = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}")
_WORKSPACE = re.compile(r"ws_[0-9a-f]{32}")
_CONTROL = re.compile(r"[\x00-\x1f\x7f\u2028\u2029]")


def schema(journal) -> None:
    """Idempotent additive DDL; do not consume a journal data-step version."""
    with journal.tx():
        journal.db.execute("""CREATE TABLE IF NOT EXISTS pm_workspaces (
            workspace_id TEXT PRIMARY KEY, server_id TEXT NOT NULL, name TEXT NOT NULL,
            repositories TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 1,
            created_by TEXT NOT NULL, created_at REAL NOT NULL, updated_at REAL NOT NULL,
            operation_id TEXT NOT NULL UNIQUE)""")
        journal.db.execute("""CREATE TABLE IF NOT EXISTS pm_workspace_members (
            workspace_id TEXT NOT NULL REFERENCES pm_workspaces(workspace_id), actor TEXT NOT NULL,
            role TEXT NOT NULL CHECK(role IN ('owner','contributor','reader')),
            controller_id TEXT NOT NULL, created_at REAL NOT NULL, updated_at REAL NOT NULL,
            PRIMARY KEY(workspace_id,actor))""")
        journal.db.execute("""CREATE TABLE IF NOT EXISTS pm_workspace_applied (
            operation_id TEXT PRIMARY KEY, result TEXT NOT NULL, applied_at REAL NOT NULL)""")
        from .dashboard_sync import install_pm_access
        install_pm_access(journal.db)
        from .pm_lifecycle import schema as lifecycle_schema
        lifecycle_schema(journal)
        from .pm_work_start import schema as work_start_schema
        work_start_schema(journal)
        from .pm_work_follow_up import schema as work_follow_up_schema
        work_follow_up_schema(journal)


def server_id(db) -> str:
    """Reuse bootstrap's durable server identity, never create a second identity."""
    return db.execute("SELECT server_id FROM api_sync_metadata WHERE singleton=1").fetchone()[0]


def controller_for(db, principal: Principal) -> str:
    """Stable across clients, credentials and roles; no read-side provisioning."""
    return "ctr_" + uuid.uuid5(uuid.UUID(server_id(db)), principal.actor).hex


def principal_binding(principal: Principal) -> dict:
    """Only call with an authenticated principal; contains no credential hash."""
    return {"actor": principal.actor, "admin": principal.admin, "scopes": sorted(principal.scopes)}


def admitted_principal(ctx: OpContext) -> Principal:
    """Recover server-bound authority, never infer admin from an actor's spelling."""
    value = (ctx.admission_binding or {}).get("principal")
    if (not isinstance(value, dict) or value.get("actor") != ctx.op["actor"]
            or type(value.get("admin")) is not bool or not isinstance(value.get("scopes"), list)
            or any(not isinstance(scope, str) for scope in value["scopes"])):
        raise OperationError("AUTHORITY_UNAVAILABLE", "The operation has no valid authenticated authority", 403)
    return Principal(value["actor"], frozenset(value["scopes"]), admin=value["admin"])


def _workspace(db, workspace_id: str):
    if not isinstance(workspace_id, str) or not _WORKSPACE.fullmatch(workspace_id):
        raise OperationError("NOT_FOUND", "Workspace not found", 404)
    row = db.execute("SELECT * FROM pm_workspaces WHERE workspace_id=?", (workspace_id,)).fetchone()
    if row is None:
        raise OperationError("NOT_FOUND", "Workspace not found", 404)
    return row


def require_member(db, principal: Principal, workspace_id: str, *, write=False, decide=False) -> dict:
    row = _workspace(db, workspace_id)
    member = db.execute("SELECT * FROM pm_workspace_members WHERE workspace_id=? AND actor=?",
                        (workspace_id, principal.actor)).fetchone()
    if not principal.admin and member is None:
        raise OperationError("NOT_FOUND", "Workspace not found", 404)
    role = member["role"] if member else "owner"  # explicit authenticated administrator override
    if not principal.admin and ((decide and role != "owner") or (write and role == "reader")):
        raise OperationError("FORBIDDEN", "Workspace membership does not permit this action", 403)
    return {key: row[key] for key in ("workspace_id", "server_id", "name", "version", "created_by",
                                      "created_at", "updated_at")} | {
        "repositories": json.loads(row["repositories"]),
        "membership": {"actor": principal.actor, "role": role,
                       "controller_id": controller_for(db, principal)},
    }


def list_workspaces(db, principal: Principal) -> list[dict]:
    rows = db.execute("SELECT workspace_id FROM pm_workspaces ORDER BY created_at,workspace_id") if principal.admin else db.execute(
        "SELECT workspace_id FROM pm_workspace_members WHERE actor=? ORDER BY created_at,workspace_id", (principal.actor,))
    return [require_member(db, principal, row["workspace_id"]) for row in rows.fetchall()]


def get_workspace(db, principal: Principal, workspace_id: str) -> dict:
    result = require_member(db, principal, workspace_id)
    if principal.admin or result["membership"]["role"] == "owner":
        result["members"] = [dict(row) for row in db.execute("""SELECT actor,role,controller_id,created_at,updated_at
            FROM pm_workspace_members WHERE workspace_id=? ORDER BY actor""", (workspace_id,))]
    return result


def operation_visible(db, principal: Principal, op: dict) -> bool:
    """Read filter for all PM actions; callers retain their ordinary scope checks."""
    if op.get("action") in {"pm.history.link", "pm.history.unlink"}:
        from .pm_external_history_links import operation_visible as history_visible
        return history_visible(db, principal, op)
    if op.get("action") == "work.adopt_and_start":
        from .pm_work_adopt_and_start import operation_visible as adopt_start_visible
        return adopt_start_visible(db, principal, op)
    if op.get("action") == "work.start":
        from .pm_work_start import operation_visible as work_start_visible
        return work_start_visible(db, principal, op)
    if op.get("action") == "session.send":
        from . import pm_work_follow_up
        if pm_work_follow_up.is_work_follow_up(op):
            return pm_work_follow_up.operation_visible(db, principal, op)
    if not op.get("action", "").startswith("pm."):
        return True
    workspace_id = (op.get("target") or {}).get("workspace_id")
    if not workspace_id and op.get("action") == "pm.workspace.create":
        row = db.execute("SELECT workspace_id FROM pm_workspaces WHERE operation_id=?",
                         (op["operation_id"],)).fetchone()
        if row is None:
            return principal.admin or op.get("actor") == principal.actor
        workspace_id = row["workspace_id"]
    if not workspace_id:
        return False
    try:
        require_member(db, principal, workspace_id)
    except OperationError:
        return False
    if op['action'] == 'pm.thread.create':
        from .pm_thread_contexts import operation_visible as context_visible
        return context_visible(db, principal, op)
    if op['action'] == 'pm.work_input.defer':
        from .pm_work_input_idea import operation_visible as deferred_visible
        return deferred_visible(db, principal, op)
    if op['action'] in {'pm.run.analyze', 'pm.run.bat.analyze'}:
        from .pm_thread_contexts import frozen_visible
        run = db.execute('SELECT context FROM pm_runs WHERE operation_id=?', (op['operation_id'],)).fetchone()
        if run and not frozen_visible(db, principal, workspace_id, json.loads(run[0])):
            return False
        from .pm_context_bundles import operation_visible as bundle_visible
        if not bundle_visible(db, principal, op):
            return False
    if op['action'].startswith('pm.dependencies.'):
        from .pm_dependencies import operation_visible as dependencies_visible
        return dependencies_visible(db, principal, op)
    if op['action'] == 'pm.workspace.adapter.select':
        from .pm_workspace_adapter import operation_visible as adapter_visible
        return adapter_visible(db, principal, op)
    if op['action'].startswith('pm.lifecycle.'):
        from .pm_lifecycle import operation_visible as lifecycle_visible
        return lifecycle_visible(db, principal, op)
    if op['action'].startswith('pm.issue.'):
        from .pm_issue_mappings import operation_visible as issue_operation_visible
        return issue_operation_visible(db, principal, op)
    if op['action'] == 'pm.maintenance.repair.runtime.observe':
        from .pm_maintenance_repair_runtime import operation_visible as runtime_visible
        return runtime_visible(db, principal, op)
    if op['action'] == 'pm.maintenance.check':
        from .pm_maintenance_checks import operation_visible as maintenance_visible
        return maintenance_visible(db, principal, op)
    if op['action'].startswith('pm.maintenance.repair'):
        from .pm_maintenance_repairs import operation_visible as repair_visible
        return repair_visible(db, principal, op)
    if op['action'].startswith('pm.quality.review.'):
        from .pm_quality_reviews import operation_visible as quality_visible
        return quality_visible(db, principal, op)
    if op['action'] == 'pm.session.quiescence.observe':
        from .pm_session_quiescence import operation_visible as quiescence_visible
        return quiescence_visible(db, principal, op)
    if op['action'] == 'pm.execution.runtime.observe':
        from .pm_execution_runtime import operation_visible as execution_runtime_visible
        return execution_runtime_visible(db, principal, op)
    if op['action'] == 'pm.execution.verify':
        from .pm_execution_verification import operation_visible as verification_visible
        return verification_visible(db, principal, op)
    if op['action'].startswith('pm.work_batch.'):
        from .pm_work_batches import operation_visible as batch_visible
        return batch_visible(db, principal, op)
    if op['action'] == 'pm.spec.materialize':
        from .pm_spec_materialization import operation_visible as spec_visible
        return spec_visible(db, principal, op)
    if op['action'].startswith('pm.maintenance_review.'):
        from .pm_maintenance_review import operation_visible as review_visible
        return review_visible(db, principal, op)
    if op['action'] == 'pm.ahp.observe':
        from .pm_ahp_observations import operation_visible as ahp_visible
        return ahp_visible(db, principal, op)
    if op['action'] == 'pm.github.observe':
        from .pm_github_observation import operation_visible as github_visible
        return github_visible(db, principal, op)
    if op['action'] == 'pm.workload.capture':
        from .pm_workload import operation_visible as workload_visible
        return workload_visible(db, principal, op)
    if op['action'] == 'pm.maintenance_policy.adopt':
        from .pm_maintenance_policy import operation_visible as policy_visible
        return policy_visible(db, principal, op)
    if op['action'] == 'pm.release_baseline.register':
        from .pm_release_baselines import operation_visible as baseline_visible
        return baseline_visible(db, principal, op)
    if op['action'].startswith('pm.work_package.'):
        from .pm_work_packages import operation_visible as package_operation_visible
        return package_operation_visible(db, principal, op)
    if op['action'].startswith('pm.conversation.'):
        from .pm_conversation_proposals import operation_visible as conversation_visible
        return conversation_visible(db, principal, op)
    if op['action'] == 'pm.overview.assess':
        from .pm_overview_assessments import operation_visible as overview_visible
        return overview_visible(db, principal, op)
    if op['action'] == 'pm.continuation.prepare':
        from .pm_continuation import operation_visible as continuation_visible
        return continuation_visible(db, principal, op)
    if op['action'] == 'pm.follow_up.analyze':
        from .pm_follow_up_analysis import operation_visible as assessment_operation_visible
        return assessment_operation_visible(db, principal, op)
    if op['action'].startswith('pm.follow_up.'):
        from .pm_follow_up import operation_visible as follow_up_operation_visible
        return follow_up_operation_visible(db, principal, op)
    if op['action'].startswith('pm.governance.'):
        from .pm_governance import operation_visible as governance_operation_visible
        return governance_operation_visible(db, principal, op)
    if op['action'].startswith('pm.meeting.'):
        from .pm_meetings import operation_visible as meeting_operation_visible
        return meeting_operation_visible(db, principal, op)
    if op['action'] in {'pm.sprint.close', 'pm.sprint.cancel', 'pm.evidence.capture'}:
        from .pm_quality_projections import operation_visible as quality_selection_visible
        return quality_selection_visible(db, principal, op)
    return True


def _only(value: dict, fields, label: str) -> None:
    if set(value) - set(fields):
        raise OperationError("INVALID_PARAMS", f"Unexpected {label} fields", 422)


def _text(value, label, limit):
    if not isinstance(value, str) or not value.strip() or len(value) > limit or _CONTROL.search(value):
        raise OperationError("INVALID_PARAMS", f"{label} must be a nonempty single line of at most {limit} characters", 422)
    return value.strip()


def _create_values(target, params, pre):
    _only(target, (), "target")
    _only(pre, (), "preconditions")
    _only(params, ("name", "repositories"), "params")
    name = _text(params.get("name"), "name", 120)
    repos = params.get("repositories", [])
    if not isinstance(repos, list) or len(repos) > 20:
        raise OperationError("INVALID_PARAMS", "repositories must be a list of at most 20 explicit strings", 422)
    return name, list(dict.fromkeys(_text(repo, "repository", 300) for repo in repos))


def _member_values(db, principal, target, params, pre):
    _only(target, ("workspace_id",), "target")
    _only(params, ("actor", "role"), "params")
    _only(pre, ("expected_version",), "preconditions")
    workspace = require_member(db, principal, target.get("workspace_id"), decide=True)
    if type(pre.get("expected_version")) is not int or pre["expected_version"] < 1:
        raise OperationError("PRECONDITION_REQUIRED", "Supply the workspace expected_version", 422)
    if workspace["version"] != pre["expected_version"]:
        raise OperationError("VERSION_CONFLICT", "Workspace changed; read its current version", 409)
    actor, role = params.get("actor"), params.get("role")
    if not isinstance(actor, str) or not _ACTOR.fullmatch(actor) or "role" not in params or role not in (*ROLES, None):
        raise OperationError("INVALID_PARAMS", "Supply an actor and owner, contributor, reader or null role", 422)
    member = db.execute("SELECT role FROM pm_workspace_members WHERE workspace_id=? AND actor=?",
                        (workspace["workspace_id"], actor)).fetchone()
    if member and member["role"] == "owner" and role != "owner":
        owners = db.execute("SELECT COUNT(*) FROM pm_workspace_members WHERE workspace_id=? AND role='owner'",
                            (workspace["workspace_id"],)).fetchone()[0]
        if owners <= 1:
            raise OperationError("LAST_OWNER", "A workspace must retain at least one owner", 409)
    return workspace, actor, role, member["role"] if member else None


def _admit_create(ops, principal, target, params, pre):
    _create_values(target, params, pre)
    return {"principal": principal_binding(principal)}


def _admit_member(ops, principal, target, params, pre):
    _member_values(ops.db, principal, target, params, pre)
    return {"principal": principal_binding(principal)}


def _authorize_existing(ops, principal, op, verb):
    if not principal.allows("manage") or not operation_visible(ops.db, principal, op):
        raise OperationError("FORBIDDEN", "This workspace operation is not available to the caller", 403)
    if op["action"] == "pm.workspace.member.set":
        require_member(ops.db, principal, op["target"]["workspace_id"], decide=True)
    elif verb != "replay" and not principal.admin and op["actor"] != principal.actor:
        raise OperationError("FORBIDDEN", "Only the original caller may control workspace creation", 403)


def _once(ctx, change):
    with ctx.service.journal.tx():
        row = ctx.service.db.execute("SELECT result FROM pm_workspace_applied WHERE operation_id=?",
                                     (ctx.operation_id,)).fetchone()
        if row:
            return json.loads(row["result"])
        principal = admitted_principal(ctx)
        if not principal.allows("manage"):
            raise OperationError("FORBIDDEN", "Workspace changes require manage scope", 403)
        result = change(ctx.service.db, principal, time.time())
        ctx.service.db.execute("INSERT INTO pm_workspace_applied VALUES(?,?,?)",
                               (ctx.operation_id, json.dumps(result, ensure_ascii=False), time.time()))
        return result


async def _run_create(ctx):
    def change(db, principal, now):
        name, repositories = _create_values(ctx.target, ctx.params, ctx.preconditions)
        workspace_id = "ws_" + uuid.uuid4().hex
        db.execute("""INSERT INTO pm_workspaces(workspace_id,server_id,name,repositories,created_by,
            created_at,updated_at,operation_id) VALUES(?,?,?,?,?,?,?,?)""",
                   (workspace_id, server_id(db), name, json.dumps(repositories), principal.actor, now, now, ctx.operation_id))
        db.execute("INSERT INTO pm_workspace_members VALUES(?,?,?,?,?,?)",
                   (workspace_id, principal.actor, "owner", controller_for(db, principal), now, now))
        from .pm_lifecycle import initialize
        initialize(db, workspace_id, now)
        ctx.service.journal.api_event("pm_workspace", workspace_id, "pm.workspace.created",
                                     {"workspace_id": workspace_id, "version": 1, "operation_id": ctx.operation_id}, actor=principal.actor)
        return {"workspace": require_member(db, principal, workspace_id)}
    return _once(ctx, change)


async def _run_member(ctx):
    def change(db, principal, now):
        workspace, actor, role, old_role = _member_values(db, principal, ctx.target, ctx.params, ctx.preconditions)
        wid = workspace["workspace_id"]
        if role == old_role:
            return {"workspace": workspace}
        if role is None:
            db.execute("DELETE FROM pm_workspace_members WHERE workspace_id=? AND actor=?", (wid, actor))
        else:
            db.execute("""INSERT INTO pm_workspace_members VALUES(?,?,?,?,?,?)
                ON CONFLICT(workspace_id,actor) DO UPDATE SET role=excluded.role,updated_at=excluded.updated_at""",
                       (wid, actor, role, controller_for(db, Principal(actor, frozenset())), now, now))
        db.execute("UPDATE pm_workspaces SET version=version+1,updated_at=? WHERE workspace_id=?", (now, wid))
        ctx.service.journal.api_event("pm_workspace", wid, "pm.workspace.member.changed",
                                     {"workspace_id": wid, "actor": actor, "role": role,
                                      "version": workspace["version"] + 1, "operation_id": ctx.operation_id}, actor=principal.actor)
        # Self-removal must still commit its receipt, without reauthorizing a new read.
        result = {**workspace, "version": workspace["version"] + 1, "updated_at": now}
        if actor == principal.actor:
            result["membership"] = {**result["membership"], "role": role}
        return {"workspace": result}
    return _once(ctx, change)


def install(ops) -> None:
    schema(ops.journal)
    actions = (
        ActionDef("pm.workspace.create", "manage", "Create an independent PM workspace",
                  _run_create, _admit_create, authorize_existing=_authorize_existing),
        ActionDef("pm.workspace.member.set", "manage", "Set or remove workspace membership",
                  _run_member, _admit_member, ("workspace_id",), _authorize_existing),
    )
    for action in actions:
        if action.name not in ops.actions:
            ops.register(action)
