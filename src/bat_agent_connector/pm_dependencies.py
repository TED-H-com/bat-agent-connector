"""Durable dependency captures and sourced declarations; Connector is the authority."""
from __future__ import annotations

import json
import re
import time
from urllib.parse import urlsplit

from . import pm_access, pm_runs, pm_spec_sources, pm_workspace
from .operations import ActionDef, OperationError, OperationService
from .pm_dependency_budget import MAX_CAPTURE_BYTES, MAX_DECLARATION_BYTES, bounded
from .pm_dependency_parser import KINDS, RELATIONS, Inventory, dependency_key, stable, text
from .pm_dependency_paths import MAX_FILES, MAX_TOTAL_BYTES, safe_path
from .pm_release_baselines import _json, _snapshot

ACTIONS = tuple("pm.dependencies." + verb for verb in ("capture", "declare", "retract"))
MAX_DECLARED = 256


def bad(code="INVALID_PARAMS", status=422):
    messages = {
        "INVALID_PARAMS": "Use the documented dependency fields and source reference / 請使用相依契約欄位並提供來源",
        "VERSION_CONFLICT": "Read the current dependency version before changing it / 請先讀取目前相依版本",
        "PM_DEPENDENCIES_BINDING_CHANGED": "The pinned repository binding changed / 固定儲存庫綁定已變更",
        "PM_DEPENDENCIES_SOURCE_UNAVAILABLE": "Dependency source unavailable / 相依來源無法使用",
        "PM_DEPENDENCIES_LIMIT": "Dependency declaration limit reached / 相依宣告已達上限",
        "FORBIDDEN": "Dependency changes require manage and observe scopes / 變更相依需要管理與觀察權限",
    }
    return OperationError(code, messages.get(code, messages["PM_DEPENDENCIES_SOURCE_UNAVAILABLE"]), status)


def schema(journal):
    with journal.tx():
        for statement in (
            """CREATE TABLE IF NOT EXISTS pm_dependency_heads (
                workspace_id TEXT NOT NULL, source_key TEXT NOT NULL, version INTEGER NOT NULL,
                revision_id TEXT, PRIMARY KEY(workspace_id,source_key))""",
            """CREATE TABLE IF NOT EXISTS pm_dependency_revisions (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT, revision_id TEXT NOT NULL UNIQUE,
                workspace_id TEXT NOT NULL, source_key TEXT NOT NULL, document TEXT NOT NULL,
                document_digest TEXT NOT NULL, source_binding TEXT NOT NULL,
                created_at REAL NOT NULL, operation_id TEXT NOT NULL UNIQUE)""",
            """CREATE TABLE IF NOT EXISTS pm_dependency_declarations (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT, declaration_id TEXT NOT NULL UNIQUE,
                workspace_id TEXT NOT NULL, source_key TEXT NOT NULL, document TEXT NOT NULL,
                operation_id TEXT NOT NULL UNIQUE)""",
            """CREATE TABLE IF NOT EXISTS pm_dependency_retractions (
                declaration_id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL, source_key TEXT NOT NULL,
                actor TEXT NOT NULL, created_at REAL NOT NULL, operation_id TEXT NOT NULL UNIQUE)""",
            """CREATE TABLE IF NOT EXISTS pm_dependency_revision_summaries (
                revision_id TEXT PRIMARY KEY, summary TEXT NOT NULL)""",
            "CREATE INDEX IF NOT EXISTS pm_dependency_history ON pm_dependency_revisions(workspace_id,source_key,sequence)",
            "CREATE INDEX IF NOT EXISTS pm_dependency_declared ON pm_dependency_declarations(workspace_id,source_key,sequence)",
        ):
            journal.db.execute(statement)
        for table in ("pm_dependency_revisions", "pm_dependency_declarations", "pm_dependency_retractions", "pm_dependency_revision_summaries"):
            for verb in ("UPDATE", "DELETE"):
                journal.db.execute(f"CREATE TRIGGER IF NOT EXISTS {table}_{verb.lower()}_immutable BEFORE {verb} ON {table} "
                    "BEGIN SELECT RAISE(ABORT, 'Dependency evidence is immutable'); END")


def install(ops):
    schema(ops.journal)
    for action in ACTIONS:
        if action not in ops.actions:
            ops.register(ActionDef(action, "manage", "Record pinned dependency evidence / 記錄固定版本相依證據",
                _run, _admitter(action), ("workspace_id",), authorize_existing=_authorize_existing))


def capabilities():
    return {"version": 1, "actions": list(ACTIONS), "relations": list(RELATIONS), "kinds": list(KINDS),
            "source": "existing_pm_pinned_git", "max_files": MAX_FILES, "max_bytes": MAX_TOTAL_BYTES,
            "capture_executes_code": False}


def source_identity(binding):
    # Identity of the carrier is separate from the pinned content revision.
    return {k: binding[k] for k in ("pm_workspace_id", "server_id", "target", "repository_id", "binding_digest", "config_digest", "operation_id", "creation_operation_id")}


def source_key(binding):
    return stable("pmdepsrc_", source_identity(binding))


def source_access(db, principal, wid, identity):
    if not principal.allows("observe"):
        raise bad("FORBIDDEN", 403)
    workspace = pm_workspace.require_member(db, principal, wid)
    current = pm_runs.workspace_worktree(db, wid)
    if (not current or source_identity(current) != identity
            or current["target"]["repository"] not in workspace["repositories"]):
        raise bad("PM_DEPENDENCIES_SOURCE_UNAVAILABLE", 404)
    pm_access.require_operation_sources(db, principal, [current["creation_operation_id"]])
    original = db.execute("SELECT * FROM operations WHERE operation_id=?", (current["creation_operation_id"],)).fetchone()
    if not original or not pm_access.visible(db, principal, OperationService._decode(original)):
        raise bad("PM_DEPENDENCIES_SOURCE_UNAVAILABLE", 404)
    return current


def current_binding(ops, principal, wid):
    # Current config digest and exact PM carrier checks are shared with the spec reader.
    binding = pm_spec_sources._binding(ops, principal, wid)
    source_access(ops.db, principal, wid, source_identity(binding))
    return binding


def head(db, wid, key):
    row = db.execute("SELECT version,revision_id FROM pm_dependency_heads WHERE workspace_id=? AND source_key=?", (wid, key)).fetchone()
    return dict(row) if row else {"version": 0, "revision_id": None}


def record(db, wid, revision_id):
    row = db.execute("SELECT * FROM pm_dependency_revisions WHERE workspace_id=? AND revision_id=?", (wid, revision_id)).fetchone()
    if row is None:
        raise bad("PM_DEPENDENCIES_SOURCE_UNAVAILABLE", 404)
    doc, binding = json.loads(row["document"]), json.loads(row["source_binding"])
    if pm_spec_sources._digest(doc) != row["document_digest"] or doc["capture"]["revision_id"] != revision_id:
        raise bad("PM_DEPENDENCIES_SOURCE_UNAVAILABLE", 409)
    return {**dict(row), "document": doc, "source_binding": binding}


def declared(db, wid, key):
    return [json.loads(row[0]) for row in db.execute("""SELECT d.document FROM pm_dependency_declarations d
        LEFT JOIN pm_dependency_retractions r ON r.declaration_id=d.declaration_id
        WHERE d.workspace_id=? AND d.source_key=? AND r.declaration_id IS NULL
        ORDER BY d.sequence LIMIT ?""", (wid, key, MAX_DECLARED + 1)).fetchall()]


def _source_ref(value, binding):
    if isinstance(value, str):
        try:
            parsed = urlsplit(text(value))
            if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or any(c.isspace() for c in value):
                raise ValueError
        except ValueError:
            raise bad() from None
        return {"url": value}
    if (not isinstance(value, dict) or set(value) != {"path", "commit_sha"} or not safe_path(value["path"])
            or value["commit_sha"] != binding["source_sha"]):
        raise bad()
    return value


def _check(ops, principal, action, target, params, pre):
    if not principal.allows("manage") or not principal.allows("observe"):
        raise bad("FORBIDDEN", 403)
    if set(target) != {"workspace_id"}:
        raise bad()
    wid = target["workspace_id"]
    workspace = pm_workspace.require_member(ops.db, principal, wid, write=True, decide=action != ACTIONS[0])
    binding = current_binding(ops, principal, wid)
    key, current = source_key(binding), head(ops.db, wid, source_key(binding))
    if (set(pre) != {"expected_version", "expected_workspace_version", "expected_binding_digest"}
            or type(pre["expected_version"]) is not int or type(pre["expected_workspace_version"]) is not int):
        raise bad()
    if pre["expected_version"] != current["version"] or pre["expected_workspace_version"] != workspace["version"]:
        raise bad("VERSION_CONFLICT", 409)
    if pre["expected_binding_digest"] != binding["binding_digest"]:
        raise bad("PM_DEPENDENCIES_BINDING_CHANGED", 409)
    intent = {"binding": binding, "version": current["version"], "workspace_version": workspace["version"]}
    if action == ACTIONS[0]:
        if set(params) != {"repository", "commit_sha"} or params["repository"] != binding["target"]["repository"] or params["commit_sha"] != binding["source_sha"]:
            raise bad("PM_DEPENDENCIES_BINDING_CHANGED", 409)
    elif action == ACTIONS[1]:
        fields = {"component_id", "dependency", "relation", "evidence_level", "source_ref"}
        if not fields <= params.keys() or params.keys() - fields - {"note"}:
            raise bad()
        dep = params["dependency"]
        if not isinstance(dep, dict) or set(dep) != {"name", "ecosystem", "kind", "version"}:
            raise bad()
        try:
            text(dep["name"])
            text(dep["ecosystem"])
            if dep["version"] is not None:
                text(dep["version"])
            if "note" in params:
                text(params["note"])
        except ValueError:
            raise bad() from None
        if (not isinstance(dep["kind"], str) or not isinstance(params["evidence_level"], str)
                or not isinstance(params["relation"], str) or dep["kind"] not in KINDS or params["evidence_level"] not in {"declared", "inferred"}
                or params["relation"] not in RELATIONS or params["evidence_level"] == "declared" and params["relation"] not in {"contract", "reference"}):
            raise bad()
        if not current["revision_id"]:
            raise bad("PM_DEPENDENCIES_SOURCE_UNAVAILABLE", 409)
        captured = record(ops.db, wid, current["revision_id"])["document"]
        if params["component_id"] not in {c["component_id"] for c in captured["components"]}:
            raise bad()
        if len(declared(ops.db, wid, key)) >= MAX_DECLARED:
            raise bad("PM_DEPENDENCIES_LIMIT", 409)
        intent["source_ref"] = _source_ref(params["source_ref"], binding)
    else:
        if set(params) != {"declaration_id"} or not isinstance(params["declaration_id"], str) or not re.fullmatch(r"pmdecl_[0-9a-f]{32}", params["declaration_id"]):
            raise bad()
        if params["declaration_id"] not in {d["declaration_id"] for d in declared(ops.db, wid, key)}:
            raise bad("PM_DEPENDENCIES_SOURCE_UNAVAILABLE", 404)
    return intent


def _admitter(action):
    def admit(ops, principal, target, params, pre):
        with ops.journal.tx():
            intent = _check(ops, principal, action, target, params, pre)
        return {"principal": pm_workspace.principal_binding(principal), "dependency_intent": intent}
    return admit


def operation_visible(db, principal, operation):
    with _snapshot(db):
        row = db.execute("SELECT * FROM operations WHERE operation_id=?", (operation.get("operation_id"),)).fetchone()
        if not row or row["action"] not in ACTIONS:
            return False
        op = OperationService._decode(row)
        try:
            binding = op["external_refs"]["admission_binding"]["dependency_intent"]["binding"]
            source_access(db, principal, op["target"]["workspace_id"], source_identity(binding))
            return True
        except (OperationError, KeyError, TypeError, ValueError):
            return False


def _authorize_existing(ops, principal, operation, verb):
    if not principal.allows("manage") or not operation_visible(ops.db, principal, operation):
        raise bad("PM_DEPENDENCIES_SOURCE_UNAVAILABLE", 404)
    current_binding(ops, principal, operation["target"]["workspace_id"])
    if verb != "replay":
        pm_workspace.require_member(ops.db, principal, operation["target"]["workspace_id"], write=True,
                                    decide=operation["action"] != ACTIONS[0])


async def _run(ctx):
    name = "pm.dependencies.record"
    saved = ctx.service.db.execute("SELECT status FROM operation_steps WHERE operation_id=? AND name=?", (ctx.operation_id, name)).fetchone()
    if saved and saved[0] == "succeeded":
        return ctx.effect(name, lambda: {})
    principal = pm_workspace.admitted_principal(ctx)
    intent = ctx.admission_binding["dependency_intent"]
    ops, wid, binding = ctx.service, ctx.target["workspace_id"], intent["binding"]

    def check():
        ctx.check_cancel()
        if _check(ops, principal, ctx.op["action"], ctx.target, ctx.params, ctx.preconditions) != intent:
            raise bad("PM_DEPENDENCIES_BINDING_CHANGED", 409)

    check()
    inventory = None
    if ctx.op["action"] == ACTIONS[0]:
        from .pm_dependency_sources import collect
        source = await collect(ops, principal, wid, binding)
        inventory = Inventory(binding["target"]["repository"], binding["source_sha"], source["files"], source["contents"]).parse()
        from .pm_dependency_reads import discover_peers
        inventory["peer_revisions"] = discover_peers(ops, principal, wid)
        inventory.update(files=source["files"], tree_sha=source["tree_sha"], catalog_reasons=source["catalog_reasons"], unreadable_paths=source["unreadable_paths"])
        # No await between this final host/workspace frame and the atomic CAS+receipt.
        await pm_spec_sources._guard(ops, principal, wid, binding)

    def apply():
        check()
        now, key = time.time(), source_key(binding)
        current = head(ops.db, wid, key)
        revision_id = current["revision_id"]
        result = {"workspace_id": wid, "version": current["version"] + 1}
        if inventory is not None:
            revision_id = "pmdep_" + ctx.operation_id[3:]
            partial = bool(inventory["catalog_reasons"] or inventory["parser_reasons"] or any(f["status"] != "read" for f in inventory["files"]))
            doc = {**inventory, "repository": binding["target"]["repository"], "repository_id": binding["repository_id"],
                "binding_digest": binding["binding_digest"], "commit_sha": binding["source_sha"],
                "coverage": {"scope": "allowlisted_git_artifacts", "working_tree_included": False,
                             "effective_ci_configuration": "unknown", "go_sum_is_lockfile": False},
                "capture": {"revision_id": revision_id, "commit_sha": binding["source_sha"], "captured_at": now,
                            "status": "partial" if partial else "complete"}, "partial": partial}
            bounded(doc, limit=MAX_CAPTURE_BYTES)
            ops.db.execute("""INSERT INTO pm_dependency_revisions(revision_id,workspace_id,source_key,document,
                document_digest,source_binding,created_at,operation_id) VALUES(?,?,?,?,?,?,?,?)""",
                (revision_id, wid, key, _json(doc), pm_spec_sources._digest(doc), _json(source_identity(binding)), now, ctx.operation_id))
            summary = {**doc["capture"], "repository": doc["repository"], "binding_digest": doc["binding_digest"], "partial": partial}
            ops.db.execute("INSERT INTO pm_dependency_revision_summaries VALUES(?,?)", (revision_id, _json(summary)))
            result.update(revision_id=revision_id, status=doc["capture"]["status"])
        elif ctx.op["action"] == ACTIONS[1]:
            did, params = "pmdecl_" + ctx.operation_id[3:], ctx.params
            dep = {**params["dependency"], "key": dependency_key(params["dependency"]["ecosystem"], params["dependency"]["name"])}
            doc = {"declaration_id": did, "component_id": params["component_id"], "dependency": dep,
                "dependency_key": dep["key"], "relation": params["relation"], "direction": "component_to_dependency",
                "version": dep["version"], "resolved": None, "evidence_level": params["evidence_level"],
                "version_reason": "not_declared" if dep["version"] is None else None, "resolution_reason": "owner_declaration",
                "source_ref": intent["source_ref"], "evidence_refs": [intent["source_ref"]], "note": params.get("note"),
                "actor": ctx.actor, "created_at": now, "capture_revision_id": revision_id}
            bounded([*declared(ops.db, wid, key), doc], limit=MAX_DECLARATION_BYTES)
            ops.db.execute("""INSERT INTO pm_dependency_declarations(declaration_id,workspace_id,source_key,document,operation_id)
                VALUES(?,?,?,?,?)""", (did, wid, key, _json(doc), ctx.operation_id))
            result["declaration_id"] = did
        else:
            did = ctx.params["declaration_id"]
            ops.db.execute("INSERT INTO pm_dependency_retractions VALUES(?,?,?,?,?,?)", (did, wid, key, ctx.actor, now, ctx.operation_id))
            result["declaration_id"] = did
        ops.db.execute("""INSERT INTO pm_dependency_heads VALUES(?,?,?,?) ON CONFLICT(workspace_id,source_key)
            DO UPDATE SET version=excluded.version,revision_id=excluded.revision_id""", (wid, key, result["version"], revision_id))
        pm_access.inherit_operation_sources(ops.db, ctx.operation_id, [binding["creation_operation_id"]])
        ops.journal.api_event("pm_dependencies", wid, ctx.op["action"], {**result, "operation_id": ctx.operation_id}, actor=ctx.actor)
        return result
    return ctx.effect(name, apply)
