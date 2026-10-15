"""Explicit workspace work associations and immutable, journal-only evidence.

This layer observes existing Connector facts. It does not execute work, fetch a
provider, reinterpret a completion claim as quality acceptance, or grant control.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import time
import uuid

from . import controller_authority, deployment_store, pm_plans, pm_read_cache, pm_workspace, work_items
from .errors import ResourceReadOnly
from .operations import ActionDef, OperationError

MAX_LINKS = 50
MAX_RELATED = 20
MAX_EVIDENCE = 100
MAX_FACT_BYTES = 16_000
_BINDING = re.compile(r"pmwb_[0-9a-f]{32}")
_EVIDENCE = re.compile(r"pme_[0-9a-f]{32}")
_CAPTURE = re.compile(r"pmcap_[0-9a-f]{32}")
_SHA = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})")
_DIGEST = re.compile(r"[0-9a-f]{64}")
ACTIONS = ("pm.project.bind", "pm.work.bind", "pm.evidence.capture")


def _json(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def _digest(value):
    return hashlib.sha256(_json(value).encode()).hexdigest()


def _bad(code, message, status=422):
    return OperationError(code, message, status)


def _only(value, fields):
    if not isinstance(value, dict) or set(value) - set(fields):
        raise _bad("INVALID_PARAMS", "Unexpected evidence fields")


def _version(value):
    return type(value) is int and value >= 0


def _time(value):
    return value if type(value) in (int, float) and math.isfinite(value) else None


def _body(value):
    if isinstance(value, dict):
        return value
    try:
        parsed = json.loads(value or "{}")
    except (ValueError, TypeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _fields(value, fields):
    """Only fixed, bounded scalar fields; never operation params, prompts or logs."""
    return {key: item for key in fields if (item := value.get(key)) is None
            or type(item) in (bool, int) or type(item) is float and math.isfinite(item)
            or isinstance(item, str) and len(item) <= 1000}


def schema(journal):
    pm_workspace.schema(journal)
    with journal.tx():
        for statement in (
            """CREATE TABLE IF NOT EXISTS pm_project_bindings (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT, binding_id TEXT NOT NULL UNIQUE,
                workspace_id TEXT NOT NULL, project_id TEXT NOT NULL, project_version INTEGER NOT NULL,
                rationale TEXT NOT NULL, actor TEXT NOT NULL, created_at REAL NOT NULL,
                operation_id TEXT NOT NULL UNIQUE, UNIQUE(workspace_id,project_id))""",
            """CREATE TABLE IF NOT EXISTS pm_work_bindings (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT, binding_id TEXT NOT NULL UNIQUE,
                workspace_id TEXT NOT NULL, work_item_id TEXT NOT NULL, project_binding_id TEXT NOT NULL,
                project_id TEXT NOT NULL, work_version INTEGER NOT NULL, previous_binding_id TEXT UNIQUE,
                plan_revision_id TEXT, requirement_ids TEXT NOT NULL, rationale TEXT NOT NULL,
                actor TEXT NOT NULL, created_at REAL NOT NULL, operation_id TEXT NOT NULL UNIQUE)""",
            "CREATE INDEX IF NOT EXISTS pm_work_binding_history ON pm_work_bindings(workspace_id,work_item_id,sequence)",
            """CREATE TABLE IF NOT EXISTS pm_evidence (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT, evidence_id TEXT NOT NULL UNIQUE,
                server_id TEXT NOT NULL, workspace_id TEXT NOT NULL, source_kind TEXT NOT NULL,
                source_id TEXT NOT NULL, fact_digest TEXT NOT NULL, source_observed_at REAL,
                captured_at REAL NOT NULL, journal_cursor INTEGER NOT NULL, document TEXT NOT NULL,
                access_operations TEXT NOT NULL, UNIQUE(workspace_id,source_kind,source_id,fact_digest))""",
            """CREATE TABLE IF NOT EXISTS pm_evidence_captures (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT, capture_id TEXT NOT NULL UNIQUE,
                workspace_id TEXT NOT NULL, work_item_id TEXT NOT NULL, binding_id TEXT NOT NULL,
                journal_cursor INTEGER NOT NULL, captured_at REAL NOT NULL, document TEXT NOT NULL,
                actor TEXT NOT NULL, operation_id TEXT NOT NULL UNIQUE)""",
            "CREATE INDEX IF NOT EXISTS pm_capture_work ON pm_evidence_captures(workspace_id,work_item_id,sequence)",
            """CREATE TABLE IF NOT EXISTS pm_capture_evidence (
                capture_id TEXT NOT NULL, evidence_id TEXT NOT NULL,
                PRIMARY KEY(capture_id,evidence_id))""",
            """CREATE TABLE IF NOT EXISTS pm_evidence_applied (
                operation_id TEXT PRIMARY KEY, result TEXT NOT NULL)""",
        ):
            journal.db.execute(statement)
        for table in ("pm_project_bindings", "pm_work_bindings", "pm_evidence", "pm_evidence_captures",
                      "pm_capture_evidence", "pm_evidence_applied"):
            for verb in ("UPDATE", "DELETE"):
                journal.db.execute(f"CREATE TRIGGER IF NOT EXISTS {table}_{verb.lower()}_immutable BEFORE {verb} ON {table} "
                                   "BEGIN SELECT RAISE(ABORT, 'PM evidence history is immutable'); END")


def install(ops):
    schema(ops.journal)
    for action in ACTIONS:
        if action not in ops.actions:
            key = "project_id" if action == "pm.project.bind" else "work_item_id"
            ops.register(ActionDef(action, "manage", "Associate existing work or capture its central evidence", _run,
                                   _admitter(action), ("workspace_id", key), authorize_existing=_authorize_existing))


def _member(ops, principal, workspace_id, *, write=False, decide=False):
    if not principal.allows("observe") or write and not principal.allows("manage"):
        raise _bad("FORBIDDEN", "Evidence reads require observe; changes also require manage", 403)
    return pm_workspace.require_member(ops.db, principal, workspace_id, write=write, decide=decide)


def _binding(db, workspace_id, work_item_id):
    row = db.execute("SELECT * FROM pm_work_bindings WHERE workspace_id=? AND work_item_id=? ORDER BY sequence DESC LIMIT 1",
                     (workspace_id, work_item_id)).fetchone()
    return {**dict(row), "requirement_ids": json.loads(row["requirement_ids"])} if row else None


def _check(ops, principal, action, target, params, pre):
    workspace = _member(ops, principal, target.get("workspace_id"), write=True, decide=action != "pm.evidence.capture")
    project = action == "pm.project.bind"
    if set(target) != {"workspace_id", "project_id" if project else "work_item_id"}:
        raise _bad("INVALID_TARGET", "Exact workspace and source IDs are required")
    if project:
        _only(params, ("rationale",))
        if (set(pre) != {"expected_workspace_version", "expected_project_version"}
                or not all(_version(value) for value in pre.values())):
            raise _bad("PRECONDITION_REQUIRED", "Supply expected workspace and project versions")
        item = work_items._get_project(ops.db, target["project_id"], active=True)
        if pre["expected_workspace_version"] != workspace["version"] or pre["expected_project_version"] != item["version"]:
            raise _bad("VERSION_CONFLICT", "Read the current workspace and project before binding", 409)
        if ops.db.execute("SELECT 1 FROM pm_project_bindings WHERE workspace_id=? AND project_id=?",
                          (workspace["workspace_id"], item["project_id"])).fetchone():
            raise _bad("PM_PROJECT_ALREADY_BOUND", "This project is already explicitly bound", 409)
        binding = None
    else:
        item = work_items._get_item(ops.db, target["work_item_id"])
        binding = _binding(ops.db, workspace["workspace_id"], item["work_item_id"])
        expected = {"expected_binding_id", "expected_work_version"} if action == "pm.work.bind" else {"expected_binding_id"}
        if set(pre) != expected or (pre.get("expected_binding_id") is not None and (
                not isinstance(pre["expected_binding_id"], str) or not _BINDING.fullmatch(pre["expected_binding_id"]))):
            raise _bad("PRECONDITION_REQUIRED", "Supply the exact binding and applicable work version")
        if pre["expected_binding_id"] != (binding["binding_id"] if binding else None):
            raise _bad("PM_WORK_BINDING_CHANGED", "Read the current work association", 409)
        if action == "pm.work.bind":
            _only(params, ("rationale", "plan_revision_id", "requirement_ids"))
            if not _version(pre["expected_work_version"]) or pre["expected_work_version"] != item["version"]:
                raise _bad("VERSION_CONFLICT", "Read the current work version before associating it", 409)
            work_items._get_project(ops.db, item["project_id"], active=True)
            if item["archived"]:
                raise _bad("WORK_ITEM_ARCHIVED", "Archived work cannot acquire a new association", 409)
            if not ops.db.execute("SELECT 1 FROM pm_project_bindings WHERE workspace_id=? AND project_id=?",
                                  (workspace["workspace_id"], item["project_id"])).fetchone():
                raise _bad("PM_PROJECT_BINDING_REQUIRED", "Explicitly bind this work's project first", 409)
            _requirements(ops, principal, workspace["workspace_id"], params)
        else:
            _only(params, ('quality_ref',))
            if not binding:
                raise _bad("PM_WORK_BINDING_REQUIRED", "Explicitly associate this work first", 409)
            if binding["project_id"] != item["project_id"]:
                raise _bad("PM_WORK_BINDING_CHANGED", "The work moved to another project; explicitly reassociate it", 409)
            if 'quality_ref' in params:
                from .pm_quality_projections import for_capture
                for_capture(ops, principal, workspace['workspace_id'], binding, params['quality_ref'])
    if action != "pm.evidence.capture":
        pm_workspace._text(params.get("rationale"), "rationale", 4000)
    return workspace, item, binding


def _requirements(ops, principal, workspace_id, params):
    revision, selected = params.get("plan_revision_id"), params.get("requirement_ids", [])
    if (not isinstance(selected, list) or len(selected) > 100 or not all(isinstance(item, str) for item in selected)
            or len(set(selected)) != len(selected)):
        raise _bad("INVALID_PARAMS", "requirement_ids must be distinct IDs from a fixed adopted plan")
    if revision is None:
        if selected:
            raise _bad("INVALID_PARAMS", "Requirements need an explicitly selected adopted plan revision")
        return
    plan = pm_plans.get_plan(ops, principal, workspace_id, revision)["plan"]
    if not plan["adoption"]:
        raise _bad("PM_ADOPTED_PLAN_REQUIRED", "Only a historically adopted plan can be associated", 409)
    known = {item["id"] for item in plan["document"]["requirements"]}
    if any(item not in known for item in selected):
        raise _bad("INVALID_PARAMS", "Requirements must belong to this exact adopted revision")


def _authorize_existing(ops, principal, op, control):
    _member(ops, principal, op["target"]["workspace_id"], write=True, decide=op["action"] != "pm.evidence.capture")
    if op['action'] == 'pm.evidence.capture':
        from .pm_quality_projections import operation_visible
        if not operation_visible(ops.db, principal, op):
            raise _bad('NOT_FOUND', 'Original selected quality sources are unavailable', 404)


def _admitter(action):
    def admit(ops, principal, target, params, pre):
        _check(ops, principal, action, target, params, pre)
        return {"principal": pm_workspace.principal_binding(principal)}
    return admit


async def _run(ctx):
    ops, action = ctx.service, ctx.op["action"]
    with ops.journal.tx():
        saved = ops.db.execute("SELECT result FROM pm_evidence_applied WHERE operation_id=?", (ctx.operation_id,)).fetchone()
        if saved:
            return json.loads(saved[0])
        from .pm_lifecycle_gates import require_local
        require_local(ctx.service.db, ctx.operation_id)
        principal = pm_workspace.admitted_principal(ctx)
        _, item, binding = _check(ops, principal, action, ctx.target, ctx.params, ctx.preconditions)
        workspace_id, now = ctx.target["workspace_id"], time.time()
        if action == "pm.project.bind":
            binding_id = "pmpb_" + ctx.operation_id[3:]
            ops.db.execute("""INSERT INTO pm_project_bindings(binding_id,workspace_id,project_id,project_version,
                rationale,actor,created_at,operation_id) VALUES(?,?,?,?,?,?,?,?)""",
                (binding_id, workspace_id, item["project_id"], item["version"], ctx.params["rationale"], ctx.actor, now, ctx.operation_id))
            result = {"workspace_id": workspace_id, "project_id": item["project_id"], "binding_id": binding_id}
        elif action == "pm.work.bind":
            project = ops.db.execute("SELECT binding_id FROM pm_project_bindings WHERE workspace_id=? AND project_id=?",
                                     (workspace_id, item["project_id"])).fetchone()
            binding_id = "pmwb_" + ctx.operation_id[3:]
            ops.db.execute("""INSERT INTO pm_work_bindings(binding_id,workspace_id,work_item_id,project_binding_id,
                project_id,work_version,previous_binding_id,plan_revision_id,requirement_ids,rationale,actor,created_at,operation_id)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""", (binding_id, workspace_id, item["work_item_id"], project[0], item["project_id"],
                item["version"], binding["binding_id"] if binding else None, ctx.params.get("plan_revision_id"),
                _json(ctx.params.get("requirement_ids", [])), ctx.params["rationale"], ctx.actor, now, ctx.operation_id))
            result = {"workspace_id": workspace_id, "work_item_id": item["work_item_id"], "binding_id": binding_id,
                      "previous_binding_id": binding["binding_id"] if binding else None, "execution_authorized": False}
        else:
            result = _capture(ctx, principal, item, binding, now)
        ops.db.execute("INSERT INTO pm_evidence_applied VALUES(?,?)", (ctx.operation_id, _json(result)))
        ops.journal.api_event("pm_evidence", workspace_id, action, {**result, "operation_id": ctx.operation_id}, actor=ctx.actor)
        return result


def _read_operation(ops, principal, workspace_id, operation_id):
    """Use original read policy; observing another controller never grants its control."""
    try:
        from .pm_source_lineage import require
        require(ops.db, principal, "operation", operation_id)
        op = ops.get(operation_id, steps=False, principal=principal)
        authority = controller_authority.operation_binding(op)
        if authority and authority["server_id"] != pm_workspace.server_id(ops.db):
            return None
        source_workspace = authority.get("pm_workspace_id") if authority else None
        if source_workspace:
            pm_workspace.require_member(ops.db, principal, source_workspace)
        # A private PM discussion is not a delivery source for a different workspace.
        if op["action"].startswith("pm.") or source_workspace and source_workspace != workspace_id:
            return None
        refs, result = op.get("external_refs") or {}, op.get("result") or {}
        target = refs.get("resolved_target") or op["target"]
        host = result.get("host") or target.get("host") or refs.get("host")
        sid = result.get("session_id") or target.get("session_id") or refs.get("session_id")
        if host and sid and not _session_visible(ops, principal, workspace_id, f"{host}/{sid}"):
            return None
        return op
    except (OperationError, ResourceReadOnly):
        return None


def _authority_visible(ops, principal, workspace_id, raw):
    if raw is None:
        return True  # Legacy task reads have the existing global observe policy.
    try:
        authority = controller_authority._validated(_body(raw))
        if authority["server_id"] != pm_workspace.server_id(ops.db):
            return False
        source_workspace = authority.get("pm_workspace_id")
        if source_workspace:
            pm_workspace.require_member(ops.db, principal, source_workspace)
        return not source_workspace or source_workspace == workspace_id
    except (OperationError, ResourceReadOnly):
        return False


def _task_visible(ops, principal, workspace_id, task_id):
    row = ops.db.execute("SELECT controller_authority,host,session_id,reviewer_session_id FROM tasks WHERE task_id=?", (task_id,)).fetchone()
    return bool(row and _authority_visible(ops, principal, workspace_id, row[0])
                and all(_session_visible(ops, principal, workspace_id, f"{row[1]}/{sid}") for sid in row[2:] if sid))


def _command_visible(ops, principal, workspace_id, command_id):
    row = ops.db.execute("""SELECT c.task_id,c.controller_authority,t.host,c.session_id FROM commands c
        JOIN tasks t USING(task_id) WHERE c.command_id=?""", (command_id,)).fetchone()
    return bool(row and _task_visible(ops, principal, workspace_id, row[0])
                and _authority_visible(ops, principal, workspace_id, row[1])
                and (not row[3] or _session_visible(ops, principal, workspace_id, f"{row[2]}/{row[3]}")))


def _session_visible(ops, principal, workspace_id, ref):
    from .pm_source_lineage import visible
    if not visible(ops.db, principal, "session", ref):
        return False
    if not ops.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='pm_run_state'").fetchone():
        return True
    host, _, sid = ref.partition("/")
    rows = ops.db.execute("""SELECT r.workspace_id FROM pm_runs r JOIN pm_run_state s USING(run_id)
        WHERE json_extract(s.session_binding,'$.host')=? AND json_extract(s.session_binding,'$.session_id')=?""", (host, sid))
    for row in rows:
        if row[0] != workspace_id:
            return False
        try:
            pm_workspace.require_member(ops.db, principal, row[0])
        except OperationError:
            return False
    return True


class _Collector:
    def __init__(self, ops, principal, workspace_id, cursor):
        self.ops, self.db, self.principal = ops, ops.db, principal
        self.workspace_id, self.cursor = workspace_id, cursor
        self.facts, self.visited = {}, set()
        self.coverage = {"basis": "saved_central_sources", "network_checked": False, "links_examined": 0,
                         "unavailable_sources": 0, "unsupported_sources": 0, "truncated": False,
                         "cost": "unknown", "dod": "unassessed", "unlinked_activity": "not_scanned",
                         "removed_links": "not_scanned", "verification_scope": "linked_task_observed_runner_only",
                         "deployment_scope": "linked_operations_only", "resource_projection": "partial_central_index"}

    def rows(self, sql, params=()):
        rows = self.db.execute(sql + " LIMIT ?", (*params, MAX_RELATED + 1)).fetchall()
        self.coverage["truncated"] |= len(rows) > MAX_RELATED
        return rows[:MAX_RELATED]

    def add(self, kind, source_id, observed_at, facts, *, access=()):
        document = {"fact_type": kind, "facts": facts, "dod": "unassessed", "cost": "unknown"}
        digest = _digest(document)
        key = (kind, str(source_id), digest)
        if len(_json(document).encode()) > MAX_FACT_BYTES or key not in self.facts and len(self.facts) >= MAX_EVIDENCE:
            self.coverage["truncated"] = True
            return
        self.facts[key] = {"document": document, "source_observed_at": _time(observed_at), "access": sorted(set(access))}

    def operation(self, operation_id):
        key = ("operation", operation_id)
        if key in self.visited:
            return True
        op = _read_operation(self.ops, self.principal, self.workspace_id, operation_id)
        if op is None:
            self.coverage["unavailable_sources"] += 1
            return False
        self.visited.add(key)
        authority = controller_authority.operation_binding(op)
        facts = _fields(op, ("operation_id", "action", "status", "version", "actor", "error_code", "cancel_requested"))
        facts["controller_id"] = authority.get("controller_id") if authority else None
        self.add("operation_status", operation_id, op["updated_at"], facts, access=[operation_id])
        # Successful operation status alone does not establish these specific results.
        if op["action"] in {"github.pr.merge", "delivery.merge_and_deploy"}:
            for step in self.rows("""SELECT seq,response,finished_at FROM operation_steps WHERE operation_id=?
                AND status='succeeded' AND (name='merge.verify' OR name LIKE 'merge.verify.retry.%') ORDER BY seq DESC""", (operation_id,)):
                receipt = _body(step["response"])
                if receipt.get("verified") is not True or not _SHA.fullmatch(str(receipt.get("merged_sha", ""))):
                    continue
                preview = self.db.execute("SELECT document FROM pr_merge_previews WHERE preview_id=?",
                                          (op["params"].get("preview_id"),)).fetchone()
                target = _body(_body(preview[0]).get("target")) if preview else {}
                identities = _fields(target, ("head_sha", "base_sha"))
                self.add("pr_merge", f"{operation_id}:{step['seq']}", step["finished_at"],
                    {**_fields(op["target"], ("repository", "pull_number")), **identities,
                     **_fields(receipt, ("merged_sha", "merged_onto_base_sha", "base_moved")),
                     "state": "merged", "merge_verified": True, "head_coverage": "fixed" if target.get("head_sha") else "unknown",
                     "operation_id": operation_id, "step_seq": step["seq"]}, access=[operation_id])
        self.resources(operation_id)
        for dep in self.rows("SELECT deployment_id FROM deployments WHERE operation_id=? ORDER BY deployment_id", (operation_id,)):
            self.deployment(dep[0], operation_id)
        self.integrations("operation_id=? OR (source_kind='execution' AND source_id=?)", (operation_id, operation_id))
        return True

    def resources(self, operation_id):
        for row in self.rows("""SELECT DISTINCT r.resource_type,r.resource_id,r.linked_at_seq,r.evidence_ref
            FROM api_event_resources r JOIN api_events e ON e.seq=r.seq WHERE e.resource_type='operation'
            AND e.resource_id=? AND r.resource_type IN ('session','worktree') AND r.linked_at_seq<=? AND e.seq<=?
            ORDER BY r.linked_at_seq DESC,r.resource_type,r.resource_id""", (operation_id, self.cursor, self.cursor)):
            if row["resource_type"] == "session" and not _session_visible(self.ops, self.principal, self.workspace_id, row["resource_id"]):
                self.coverage["unavailable_sources"] += 1
                continue
            self.add("resource_relation", f"{operation_id}:{row['resource_type']}:{row['resource_id']}:{row['linked_at_seq']}", None,
                     {**dict(row), "operation_id": operation_id, "relation_basis": "central_event_index"}, access=[operation_id])

    def task(self, task_id):
        if ("task", task_id) in self.visited:
            return True
        row = self.db.execute("SELECT * FROM tasks WHERE task_id=?", (task_id,)).fetchone()
        if row is None or not _task_visible(self.ops, self.principal, self.workspace_id, task_id):
            self.coverage["unavailable_sources"] += 1
            return False
        self.visited.add(("task", task_id))
        task = dict(row)
        self.add("execution_state", task_id, task["updated_at"],
                 _fields(task, ("task_id", "state", "paused", "control_version", "host", "workspace", "session_id",
                                "reviewer_session_id", "verification_commit", "verification_tree")))
        for command in self.rows("SELECT * FROM commands WHERE task_id=? ORDER BY created_at DESC,command_id", (task_id,)):
            command = dict(command)
            if not _command_visible(self.ops, self.principal, self.workspace_id, command["command_id"]):
                self.coverage["unavailable_sources"] += 1
                continue
            self.add("execution_command", command["command_id"], command["updated_at"],
                     _fields(command, ("command_id", "task_id", "session_id", "kind", "status", "message_id")))
        for verification in self.rows("SELECT * FROM observed_verifications WHERE task_id=? ORDER BY verification_id DESC", (task_id,)):
            v = dict(verification)
            fixed = _SHA.fullmatch(v["candidate_commit"]) and _SHA.fullmatch(v["tree_hash"]) and _DIGEST.fullmatch(v["output_sha256"])
            self.add("observed_verification", v["verification_id"], v["created_at"],
                     {**_fields(v, ("verification_id", "task_id", "candidate_commit", "tree_hash", "exit_code", "output_sha256")),
                      "command_digest": _digest(v["command"]), "source": "observed_runner",
                      "verification": "passed" if fixed and v["exit_code"] == 0 else "failed" if fixed else "unknown"})
        self.integrations("source_kind='task_command' AND source_id IN (SELECT command_id FROM commands WHERE task_id=?)", (task_id,))
        for relation in self.rows("""SELECT r.relation_id,r.session_resource_id,v.seq,v.body FROM observation_relations r
            JOIN relation_revisions v ON v.relation_id=r.relation_id WHERE r.execution_id=? AND v.seq=(
                SELECT MAX(seq) FROM relation_revisions WHERE relation_id=r.relation_id AND seq<=?) ORDER BY v.seq DESC""",
                                  (task_id, self.cursor)):
            body = _body(relation["body"])
            command_id = body.get("start_command_id")
            if (not _session_visible(self.ops, self.principal, self.workspace_id, relation["session_resource_id"])
                    or command_id and not _command_visible(self.ops, self.principal, self.workspace_id, command_id)):
                self.coverage["unavailable_sources"] += 1
                continue
            facts = _fields(body, ("execution_id", "session_resource_id", "role", "anchor_id", "start_seq", "end_seq"))
            self.add("resource_relation", relation["relation_id"], None,
                     {**facts, "relation_id": relation["relation_id"], "revision_seq": relation["seq"],
                      "command_id": command_id, "relation_basis": "central_relation"})
        return True

    def session(self, ref):
        host, _, sid = ref.partition("/")
        row = self.db.execute("SELECT * FROM sessions_observed WHERE host=? AND session_id=?", (host, sid)).fetchone()
        if row is None or not _session_visible(self.ops, self.principal, self.workspace_id, ref):
            self.coverage["unavailable_sources"] += 1
            return False
        saved, body = dict(row), _body(row["body"])
        self.add("session_observation", ref, saved.get("last_seen_at"),
                 {"resource_id": ref, "host": host, "session_id": sid,
                  **_fields(saved, ("gone_at", "last_seen_at", "api_access")),
                  **_fields(body, ("streaming", "pending", "runtime_status", "stale", "stale_reason")),
                  "freshness": "saved_observation"})
        for binding in self.rows("""SELECT session_resource_id,worktree_id,start_seq,end_seq,linked_at_seq,evidence_ref
            FROM session_worktree_bindings WHERE session_resource_id=? AND start_seq<=? AND linked_at_seq<=?
            ORDER BY start_seq DESC""", (ref, self.cursor, self.cursor)):
            self.add("resource_relation", f"{ref}:{binding['start_seq']}", None,
                     {**dict(binding), "relation_basis": "central_session_worktree_binding"})
        return True

    def integrations(self, clause, params):
        for row in self.rows("SELECT * FROM integration_receipts WHERE " + clause + " ORDER BY operation_id,seq", params):  # noqa: S608 - internal fixed clauses
            receipt = dict(row)
            if _read_operation(self.ops, self.principal, self.workspace_id, receipt["operation_id"]) is None:
                self.coverage["unavailable_sources"] += 1
                continue
            access = [receipt["operation_id"]]
            task_id = command_id = None
            if receipt["source_kind"] == "execution":
                if _read_operation(self.ops, self.principal, self.workspace_id, receipt["source_id"]) is None:
                    self.coverage["unavailable_sources"] += 1
                    continue
                access.append(receipt["source_id"])
            elif receipt["source_kind"] == "task_command":
                command = self.db.execute("SELECT task_id FROM commands WHERE command_id=?", (receipt["source_id"],)).fetchone()
                if not command or not _command_visible(self.ops, self.principal, self.workspace_id, receipt["source_id"]):
                    self.coverage["unavailable_sources"] += 1
                    continue
                task_id, command_id = command[0], receipt["source_id"]
            else:
                # Checkpoint and branch-source privacy/provenance need their own projection.
                self.coverage["unsupported_sources"] += 1
                continue
            self.add("integration_delivery", f"{receipt['operation_id']}:{receipt['seq']}", receipt["updated_at"],
                     {**_fields(receipt, ("operation_id", "seq", "repository", "pull_number", "source_kind", "source_id",
                                         "pinned_sha", "delivered_sha", "delivered_at", "status")),
                      "delivery": "pushed" if receipt["status"] == "delivered" and receipt["delivered_sha"] else "unproven",
                      "pr_state": "unknown", "task_id": task_id, "command_id": command_id}, access=access)

    def pull(self, ref):
        match = work_items._PULL.fullmatch(ref)
        if not match:
            self.coverage["unavailable_sources"] += 1
            return False
        repository, number = match[1], int(match[2])
        rows = self.rows("SELECT * FROM pr_merge_previews WHERE repository=? AND pull_number=? ORDER BY created_at DESC,preview_id",
                         (repository, number))
        if not rows:
            self.coverage["unavailable_sources"] += 1
        for row in rows:
            target = _body(_body(row["document"]).get("target"))
            self.add("pr_observation", row["preview_id"], row["created_at"],
                     {"repository": repository, "pull_number": number, "preview_id": row["preview_id"], "digest": row["digest"],
                      **_fields(target, ("head_sha", "base_sha", "state", "draft")), "expires_at": row["expires_at"],
                      "freshness": "saved_observation", "merge_verified": False})
        return bool(rows)

    def deployment(self, deployment_id, operation_id):
        dep = deployment_store.deployment(self.db, deployment_id=deployment_id)
        proof = _body(dep.get("evidence"))
        runtime, identity = _body(proof.get("runtime")), dep["identity"]
        observed = _body(runtime.get("observed"))
        fixed = bool(_SHA.fullmatch(str(identity.get("source_sha", ""))))
        verified = dep.get("verified") is True and bool(proof) and fixed
        self.add("deployment", deployment_id, dep["updated_at"],
                 {**_fields(dep, ("deployment_id", "operation_id", "environment_key", "generation", "version", "state", "run_id", "run_attempt")),
                  "identity": _fields(identity, ("source_sha", "artifact_id", "artifact_digest", "release_id")),
                  "observed": _fields(observed, ("repository_id", "environment", "source_sha", "artifact_id", "artifact_digest", "healthy")),
                  "runtime_checked_at": _time(runtime.get("checked_at")), "verified": verified,
                  "deployment": "verified" if verified else "unverified", "current_environment": "not_assessed"}, access=[operation_id])

    def collect(self, item):
        self.add("work_claim", item["work_item_id"], item["updated_at"],
                 {**_fields(item, ("work_item_id", "project_id", "version", "state", "done_by", "done_at", "approved_by", "approved_at")),
                  "content_fingerprint": work_items.fingerprint(item), "completion": work_items.completion(item),
                  "authority": "recorded_work_claim_and_approval"})
        rows = self.db.execute("SELECT * FROM work_item_links WHERE work_item_id=? AND removed_at IS NULL ORDER BY link_id LIMIT ?",
                               (item["work_item_id"], MAX_LINKS + 1)).fetchall()
        self.coverage["truncated"] |= len(rows) > MAX_LINKS
        for row in rows[:MAX_LINKS]:
            self.coverage["links_examined"] += 1
            handler = {"operation": self.operation, "task": self.task, "session": self.session, "pull_request": self.pull}.get(row["kind"])
            if handler is None:
                self.coverage["unsupported_sources"] += 1
            elif handler(row["ref"]):
                self.add("work_link", row["link_id"], row["linked_at"],
                         _fields(dict(row), ("link_id", "work_item_id", "kind", "ref", "linked_by", "link_operation")),
                         access=[row["ref"]] if row["kind"] == "operation" else [])


def _capture(ctx, principal, item, binding, now):
    ops, wid = ctx.service, ctx.target["workspace_id"]
    cursor, server = ops.journal.api_head(), pm_workspace.server_id(ops.db)
    collector = _Collector(ops, principal, wid, cursor)
    if 'quality_ref' in ctx.params:
        from .pm_quality_projections import for_capture
        quality = for_capture(ops, principal, wid, binding, ctx.params['quality_ref'])
        collector.add('quality_assessment', quality['reference']['operation_id'], quality['assessed_at'], quality)
        if not collector.facts:
            raise _bad('PM_QUALITY_LIMIT', 'The whole original quality fact exceeds the evidence byte budget', 413)
    collector.collect(item)
    ids = []
    for (kind, source_id, digest), fact in collector.facts.items():
        evidence_id = "pme_" + uuid.uuid5(uuid.UUID(server), _json([wid, kind, source_id, digest])).hex
        ops.db.execute("""INSERT OR IGNORE INTO pm_evidence(evidence_id,server_id,workspace_id,source_kind,source_id,fact_digest,
            source_observed_at,captured_at,journal_cursor,document,access_operations) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
            (evidence_id, server, wid, kind, source_id, digest, fact["source_observed_at"], now, cursor,
             _json(fact["document"]), _json(fact["access"])))
        ids.append(evidence_id)
    capture_id = "pmcap_" + ctx.operation_id[3:]
    document = {"server_id": server, "workspace_id": wid, "work_item_id": item["work_item_id"], "binding_id": binding["binding_id"],
                "plan_revision_id": binding["plan_revision_id"], "requirement_ids": binding["requirement_ids"],
                "evidence_ids": ids, "coverage": collector.coverage, "source_work_version": item["version"],
                "journal_cursor": cursor, "captured_at": now, "dod": "unassessed", "execution_authorized": False}
    seq = ops.db.execute("""INSERT INTO pm_evidence_captures(capture_id,workspace_id,work_item_id,binding_id,journal_cursor,
        captured_at,document,actor,operation_id) VALUES(?,?,?,?,?,?,?,?,?)""",
        (capture_id, wid, item["work_item_id"], binding["binding_id"], cursor, now, _json(document), ctx.actor, ctx.operation_id)).lastrowid
    ops.db.executemany("INSERT INTO pm_capture_evidence VALUES(?,?)", [(capture_id, evidence_id) for evidence_id in ids])
    return {"workspace_id": wid, "work_item_id": item["work_item_id"], "capture_id": capture_id, "capture_sequence": seq,
            "binding_id": binding["binding_id"], "evidence_count": len(ids), "coverage": collector.coverage,
            "journal_cursor": cursor, "dod": "unassessed", "execution_authorized": False}


def _page(after, limit):
    if type(after) is not int or after < 0 or type(limit) is not int or not 1 <= limit <= 100:
        raise _bad("INVALID_PARAMS", "after must be a nonnegative sequence; limit must be 1-100")


def list_projects(ops, principal, workspace_id, *, after=0, limit=50):
    _member(ops, principal, workspace_id)
    _page(after, limit)
    rows = ops.db.execute("SELECT * FROM pm_project_bindings WHERE workspace_id=? AND sequence>? ORDER BY sequence LIMIT ?",
                          (workspace_id, after, limit + 1)).fetchall()
    return {"version": 1, "workspace_id": workspace_id, "bindings": [dict(row) for row in rows[:limit]],
            "next_cursor": rows[limit - 1]["sequence"] if len(rows) > limit else None}


def list_work(ops, principal, workspace_id, *, project_id=None, after=0, limit=50):
    _member(ops, principal, workspace_id)
    _page(after, limit)
    if project_id is not None and (not isinstance(project_id, str) or not work_items.PROJECT_ID.fullmatch(project_id)):
        raise _bad("INVALID_PARAMS", "project_id must be a Connector project ID")
    rows = ops.db.execute("""SELECT b.* FROM pm_work_bindings b WHERE workspace_id=? AND sequence>?
        AND (? IS NULL OR project_id=?) AND NOT EXISTS(SELECT 1 FROM pm_work_bindings n WHERE n.workspace_id=b.workspace_id
            AND n.work_item_id=b.work_item_id AND n.sequence>b.sequence) ORDER BY sequence LIMIT ?""",
        (workspace_id, after, project_id, project_id, limit + 1)).fetchall()
    return {"version": 1, "workspace_id": workspace_id,
            "bindings": [{**dict(row), "requirement_ids": json.loads(row["requirement_ids"]),
                          "association_basis": "explicit_owner_association", "execution_spec": "not_assigned"} for row in rows[:limit]],
            "next_cursor": rows[limit - 1]["sequence"] if len(rows) > limit else None}


def _visible(ops, principal, row):
    if row['source_kind'] == 'quality_assessment':
        from .pm_quality_projections import access
        try:
            access(ops, principal, row['workspace_id'], json.loads(row['document'])['facts'])
        except OperationError as exc:
            if exc.status not in (403, 404):
                raise
            return False
    if not all(_read_operation(ops, principal, row["workspace_id"], operation_id) is not None
               for operation_id in json.loads(row["access_operations"])):
        return False
    facts = json.loads(row["document"])["facts"]
    task_id = facts.get("task_id") or facts.get("execution_id")
    if row["source_kind"] == "work_link" and facts.get("kind") == "task":
        task_id = facts.get("ref")
    if task_id and not _task_visible(ops, principal, row["workspace_id"], task_id):
        return False
    if facts.get("command_id") and not _command_visible(ops, principal, row["workspace_id"], facts["command_id"]):
        return False
    ref = facts.get("session_resource_id")
    if row["source_kind"] == "session_observation" or facts.get("resource_type") == "session":
        ref = facts.get("resource_id")
    if row["source_kind"] == "work_link" and facts.get("kind") == "session":
        ref = facts.get("ref")
    return not ref or _session_visible(ops, principal, row["workspace_id"], ref)


def _evidence(row, *, detail):
    result = {key: row[key] for key in ("sequence", "evidence_id", "server_id", "workspace_id", "source_kind", "source_id",
                                       "fact_digest", "source_observed_at", "captured_at", "journal_cursor")}
    document = json.loads(row["document"])
    result.update(dod="unassessed", cost="unknown")
    if detail:
        result["document"] = document
    else:
        result["assessment"] = _fields(document["facts"], ("status", "state", "verification", "deployment", "delivery", "merge_verified"))
        if row['source_kind'] == 'quality_assessment':
            result['assessment'] = _fields(document['facts'], ('acceptance', 'dod', 'qualified', 'current_applicability', 'semantics'))
    return result


def list_evidence(ops, principal, workspace_id, *, work_item_id=None, after=0, limit=50):
    _member(ops, principal, workspace_id)
    _page(after, limit)
    if work_item_id is not None and (not isinstance(work_item_id, str) or not work_items.WORK_ITEM_ID.fullmatch(work_item_id)):
        raise _bad("INVALID_PARAMS", "work_item_id must be a Connector work ID")
    rows = ops.db.execute("""SELECT e.* FROM pm_evidence e WHERE workspace_id=? AND sequence>? AND (? IS NULL OR EXISTS(
        SELECT 1 FROM pm_capture_evidence ce JOIN pm_evidence_captures c USING(capture_id)
        WHERE ce.evidence_id=e.evidence_id AND c.workspace_id=e.workspace_id AND c.work_item_id=?)) ORDER BY sequence LIMIT ?""",
        (workspace_id, after, work_item_id, work_item_id, limit + 1)).fetchall()
    visible = [row for row in rows[:limit] if _visible(ops, principal, row)]
    return {"version": 1, "workspace_id": workspace_id, "evidence": [_evidence(row, detail=False) for row in visible],
            "coverage": {"inaccessible_sources": len(visible) != len(rows[:limit])},
            "next_cursor": rows[limit - 1]["sequence"] if len(rows) > limit else None}


@pm_read_cache.memoized(freeze=pm_read_cache.json_snapshot, thaw=json.loads)
def get_evidence(ops, principal, workspace_id, evidence_id):
    _member(ops, principal, workspace_id)
    if not isinstance(evidence_id, str) or not _EVIDENCE.fullmatch(evidence_id):
        raise _bad("PM_EVIDENCE_NOT_FOUND", "Evidence not found", 404)
    row = ops.db.execute("SELECT * FROM pm_evidence WHERE workspace_id=? AND evidence_id=?", (workspace_id, evidence_id)).fetchone()
    if not row or not _visible(ops, principal, row):
        raise _bad("PM_EVIDENCE_NOT_FOUND", "Evidence not found", 404)
    return {"version": 1, "evidence": _evidence(row, detail=True)}


def _capture_out(ops, principal, row):
    if not row:
        return None
    document = json.loads(row["document"])
    visible, summaries = [], []
    for evidence_id in document["evidence_ids"]:
        evidence = ops.db.execute("SELECT * FROM pm_evidence WHERE evidence_id=? AND workspace_id=?",
                                  (evidence_id, row["workspace_id"])).fetchone()
        if evidence and _visible(ops, principal, evidence):
            visible.append(evidence_id)
            summaries.append(_evidence(evidence, detail=False))
    return {**document, "capture_id": row["capture_id"], "sequence": row["sequence"], "actor": row["actor"],
            "operation_id": row["operation_id"], "evidence_ids": visible, "evidence": summaries,
            "coverage": {**document["coverage"], "inaccessible_sources": len(visible) != len(document["evidence_ids"])}}


def latest_capture(ops, principal, workspace_id, work_item_id):
    _member(ops, principal, workspace_id)
    if not isinstance(work_item_id, str) or not work_items.WORK_ITEM_ID.fullmatch(work_item_id):
        raise _bad("INVALID_PARAMS", "work_item_id must be a Connector work ID")
    row = ops.db.execute("SELECT * FROM pm_evidence_captures WHERE workspace_id=? AND work_item_id=? ORDER BY sequence DESC LIMIT 1",
                         (workspace_id, work_item_id)).fetchone()
    return {"version": 1, "workspace_id": workspace_id, "capture": _capture_out(ops, principal, row)}


def get_capture(ops, principal, workspace_id, capture_id):
    _member(ops, principal, workspace_id)
    if not isinstance(capture_id, str) or not _CAPTURE.fullmatch(capture_id):
        raise _bad("PM_CAPTURE_NOT_FOUND", "Capture not found", 404)
    row = ops.db.execute("SELECT * FROM pm_evidence_captures WHERE workspace_id=? AND capture_id=?", (workspace_id, capture_id)).fetchone()
    if not row:
        raise _bad("PM_CAPTURE_NOT_FOUND", "Capture not found", 404)
    return {"version": 1, "workspace_id": workspace_id, "capture": _capture_out(ops, principal, row)}
