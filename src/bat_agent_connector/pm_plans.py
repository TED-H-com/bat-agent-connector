"""Immutable professional PM plan revisions and Git-backed adoption receipts.

Drafting is a local operation. The integration layer registers adoption only when
its managed Git implementation can commit/read back the exact revision files.
Git completion alone never changes the accepted plan pointer.
"""

from __future__ import annotations

import hashlib
import json
import re
import time

from . import pm_discussions, pm_profiles, pm_workspace
from .operations import ActionDef, OperationError

REVISION_ID = re.compile(r"pmrev_[0-9a-f]{32}")
ITEM_ID = re.compile(r"[a-z][a-z0-9_.-]{0,79}")
GIT_SHA = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})")
DIGEST = re.compile(r"[0-9a-f]{64}")
# Match the managed Git carrier's bound on the two exact revision files.
MAX_DOCUMENT_BYTES = 512_000


def _json(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def _digest(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _bad(code, message, status=422):
    return OperationError(code, message, status)


def _only(value, keys):
    if not isinstance(value, dict) or set(value) - set(keys):
        raise _bad("INVALID_PARAMS", "Unexpected plan fields")


def _text(value, field, maximum):
    if not isinstance(value, str) or not value.strip() or len(value) > maximum or "\0" in value:
        raise _bad("INVALID_PARAMS", f"{field} must contain 1-{maximum} characters")
    try:
        value.encode("utf-8")
    except UnicodeError:
        raise _bad("INVALID_PARAMS", f"{field} must be valid UTF-8 text") from None
    return value


def schema(journal):
    pm_workspace.schema(journal)
    with journal.tx():
        for statement in (
            """CREATE TABLE IF NOT EXISTS pm_plan_heads (
                workspace_id TEXT PRIMARY KEY REFERENCES pm_workspaces(workspace_id),
                version INTEGER NOT NULL, latest_revision_id TEXT, accepted_revision_id TEXT)""",
            """CREATE TABLE IF NOT EXISTS pm_plan_revisions (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT, revision_id TEXT NOT NULL UNIQUE,
                workspace_id TEXT NOT NULL, thread_id TEXT NOT NULL, planning_draft_id TEXT NOT NULL,
                previous_revision_id TEXT, base_accepted_revision_id TEXT,
                document_digest TEXT NOT NULL, scope_digest TEXT NOT NULL, document TEXT NOT NULL,
                created_by TEXT NOT NULL, created_at REAL NOT NULL, operation_id TEXT NOT NULL UNIQUE)""",
            """CREATE TABLE IF NOT EXISTS pm_plan_git_receipts (
                operation_id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL, revision_id TEXT NOT NULL,
                receipt TEXT NOT NULL, created_at REAL NOT NULL)""",
            """CREATE TABLE IF NOT EXISTS pm_plan_adoptions (
                operation_id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL, revision_id TEXT NOT NULL,
                previous_revision_id TEXT, scope_revision TEXT NOT NULL, decision_id TEXT NOT NULL UNIQUE,
                rationale TEXT NOT NULL, adopted_by TEXT NOT NULL, created_at REAL NOT NULL, result TEXT NOT NULL)""",
            """CREATE TABLE IF NOT EXISTS pm_plan_applied (
                operation_id TEXT PRIMARY KEY, result TEXT NOT NULL, created_at REAL NOT NULL)""",
            "CREATE INDEX IF NOT EXISTS pm_plan_workspace ON pm_plan_revisions(workspace_id,sequence)",
        ):
            journal.db.execute(statement)
        for table in ("pm_plan_revisions", "pm_plan_git_receipts", "pm_plan_adoptions", "pm_plan_applied"):
            for verb in ("UPDATE", "DELETE"):
                journal.db.execute(f"CREATE TRIGGER IF NOT EXISTS {table}_{verb.lower()}_immutable BEFORE {verb} ON {table} "
                                   "BEGIN SELECT RAISE(ABORT, 'PM plan history is immutable'); END")


def install(ops):
    schema(ops.journal)
    if "pm.plan.draft" not in ops.actions:
        ops.register(ActionDef("pm.plan.draft", "manage", "Save an immutable sourced project plan draft", _run_draft,
                               _admit_draft, ("workspace_id", "thread_id"), authorize_existing=authorize_existing))


def head(db, workspace_id):
    row = db.execute("SELECT * FROM pm_plan_heads WHERE workspace_id=?", (workspace_id,)).fetchone()
    return dict(row) if row else {"workspace_id": workspace_id, "version": 0,
                                "latest_revision_id": None, "accepted_revision_id": None}


def _revision(db, workspace_id, revision_id):
    if not isinstance(revision_id, str) or not REVISION_ID.fullmatch(revision_id):
        raise _bad("PM_PLAN_NOT_FOUND", "Plan revision not found", 404)
    row = db.execute("SELECT * FROM pm_plan_revisions WHERE workspace_id=? AND revision_id=?",
                     (workspace_id, revision_id)).fetchone()
    if row is None:
        raise _bad("PM_PLAN_NOT_FOUND", "Plan revision not found", 404)
    value = dict(row)
    try:
        document = json.loads(value["document"])
        if (not isinstance(document, dict) or document.get("revision_id") != revision_id
                or document.get("workspace_id") != workspace_id or not isinstance(document.get("markdown"), str)):
            raise ValueError
        value["document"] = document
    except (TypeError, ValueError):
        raise _bad("PM_PLAN_CORRUPT", "Plan document cannot be parsed as its recorded revision", 409) from None
    return value


def _files(revision_id, document):
    base = f"docs/pm/revisions/{revision_id}"
    metadata = {key: value for key, value in document.items() if key != "markdown"}
    files = {f"{base}/plan.md": document["markdown"], f"{base}/plan.json": _json(metadata) + "\n"}
    digests = {path: _digest(value) for path, value in files.items()}
    return {"revision_id": revision_id, "workspace_id": document["workspace_id"],
            "files": files, "file_digests": digests, "document_digest": _digest(_json(digests))}


def revision_files(db, workspace_id, revision_id):
    """Internal fixed paths/bytes; transport authorization belongs to the caller."""
    revision = _revision(db, workspace_id, revision_id)
    files = _files(revision_id, revision["document"])
    if files["document_digest"] != revision["document_digest"]:
        raise _bad("PM_PLAN_CORRUPT", "Plan document digest does not match its immutable revision", 409)
    return files


def _source_ids(value, sources, label):
    if (not isinstance(value, list) or len(value) > 50 or not all(isinstance(item, str) for item in value)
            or len(set(value)) != len(value) or any(item not in sources for item in value)):
        raise _bad("INVALID_PARAMS", f"{label} must reference distinct selected Planning input IDs")
    return value


def _document(db, workspace_id, thread, params, revision_id):
    draft = pm_discussions._draft(db, thread)
    if thread["mode"] != "planning" or draft is None:
        raise _bad("PM_PLANNING_REQUIRED", "Explicitly select a direction into Planning before drafting a plan", 409)
    selected = draft["selected_inputs"]
    sources = {item["input_id"]: item for item in selected}
    mapping = params.get("sections")
    slot_domains = {slot["key"]: slot["domain"] for mode in ("idea", "planning")
                    for slot in pm_profiles.get(mode)["input_slots"]}
    if mapping is None:
        mapping = {domain: [item["input_id"] for item in selected if slot_domains.get(item["slot"]) == domain]
                   for domain in pm_profiles.DOMAINS}
    _only(mapping, pm_profiles.DOMAINS)
    sections = {}
    for domain in pm_profiles.DOMAINS:
        ids = _source_ids(mapping.get(domain, []), sources, domain)
        sections[domain] = {"source_input_ids": ids,
                            "coverage": "attributed_inputs" if ids else "unresolved",
                            "statements": [sources[item] for item in ids]}
    raw_requirements = params.get("requirements", [])
    if not isinstance(raw_requirements, list) or len(raw_requirements) > 100:
        raise _bad("INVALID_PARAMS", "requirements must contain at most 100 source-linked entries")
    requirements, seen = [], set()
    for item in raw_requirements:
        _only(item, ("id", "source_input_id", "acceptance_input_ids", "priority", "depends_on"))
        rid = item.get("id")
        if not isinstance(rid, str) or not ITEM_ID.fullmatch(rid) or rid in seen:
            raise _bad("INVALID_PARAMS", "Requirement IDs must be distinct stable lowercase keys")
        seen.add(rid)
        source_id = item.get("source_input_id")
        if not isinstance(source_id, str) or source_id not in sources:
            raise _bad("INVALID_PARAMS", "Requirement text must come from a selected Planning input")
        acceptance = _source_ids(item.get("acceptance_input_ids", []), sources, "acceptance_input_ids")
        priority = item.get("priority", "unresolved")
        if priority not in {"must", "should", "could", "unresolved"}:
            raise _bad("INVALID_PARAMS", "Unknown requirement priority")
        depends = item.get("depends_on", [])
        if (not isinstance(depends, list) or len(depends) > 100 or not all(isinstance(value, str) for value in depends)
                or len(set(depends)) != len(depends) or rid in depends):
            raise _bad("INVALID_PARAMS", "Invalid requirement dependencies")
        source = sources[source_id]
        requirements.append({"id": rid, "text": source["value"], "source_input_id": source_id,
                             "source_status": source["status"], "source_ref": source["source_ref"],
                             "confirmed_by": source["confirmed_by"], "priority": priority,
                             "priority_status": "proposal", "depends_on": depends,
                             "acceptance": [{"text": sources[value]["value"], "source_input_id": value,
                                             "source_status": sources[value]["status"]} for value in acceptance]})
    edges = {item["id"]: item["depends_on"] for item in requirements}
    for depends in edges.values():
        if any(value not in edges for value in depends):
            raise _bad("INVALID_PARAMS", "Requirement dependencies must name this revision's requirement IDs")
    visiting, visited = set(), set()

    def visit(key):
        if key in visiting:
            raise _bad("INVALID_PARAMS", "Requirement dependency cycle")
        if key in visited:
            return
        visiting.add(key)
        for dependency in edges[key]:
            visit(dependency)
        visiting.remove(key)
        visited.add(key)

    for key in edges:
        visit(key)
    title = _text(params.get("title", draft["title"]), "title", 200)
    markdown = params.get("markdown")
    if markdown is None:
        lines = [f"# {title}", "", "Planning proposal. Adoption and execution are separate decisions."]
        for domain, section in sections.items():
            lines.extend(["", f"## {domain}", ""])
            lines.extend([f"- [{item['status']}] {item['value']} ({item['input_id']})" for item in section["statements"]]
                         or ["Unresolved: no selected source covers this domain."])
        markdown = "\n".join(lines) + "\n"
    markdown = _text(markdown, "markdown", 128_000)
    covered = {value for section in sections.values() for value in section["source_input_ids"]}
    document = {"schema_version": 1, "revision_id": revision_id, "workspace_id": workspace_id,
                "title": title, "markdown": markdown, "content_status_at_creation": "proposal",
                "planning_draft_id": draft["draft_id"], "thread_id": thread["thread_id"],
                "source_thread_version": thread["version"], "profile_version": pm_profiles.VERSION,
                "selected_inputs": selected, "sections": sections, "requirements": requirements,
                "unclassified_source_input_ids": [item for item in sources if item not in covered],
                "adoption_status_source": "workspace_journal", "execution_authorized": False,
                "scope_coverage": {"structured": "selected_sources_only", "markdown": "unclassified"}}
    # This is a partial structured scope fingerprint. The whole fixed Markdown
    # belongs to an adopted plan too; prose changes have unknown scope impact.
    scope = {"sections": {key: sections[key] for key in ("governance", "scope", "quality")},
             "requirements": requirements}
    files = _files(revision_id, document)["files"]
    if sum(len(content.encode("utf-8")) for content in files.values()) > MAX_DOCUMENT_BYTES:
        raise _bad("PM_PLAN_TOO_LARGE", "Plan Markdown and metadata exceed 512,000 UTF-8 bytes in total")
    return document, _digest(_json(scope))


def _check_draft(ops, principal, target, params, pre, revision_id):
    _only(target, ("workspace_id", "thread_id"))
    _only(params, ("title", "markdown", "previous_revision_id", "sections", "requirements"))
    if set(pre) != {"expected_version", "expected_plan_version"}:
        raise _bad("PRECONDITION_REQUIRED", "Supply expected thread and plan versions")
    pm_workspace.require_member(ops.db, principal, target.get("workspace_id"), write=True)
    thread = pm_discussions._thread(ops.db, target["workspace_id"], target.get("thread_id"))
    current = head(ops.db, target["workspace_id"])
    if (type(pre["expected_version"]) is not int or type(pre["expected_plan_version"]) is not int
            or pre["expected_version"] != thread["version"] or pre["expected_plan_version"] != current["version"]):
        raise _bad("VERSION_CONFLICT", "Read current discussion and plan versions before drafting", 409)
    if "previous_revision_id" not in params or params["previous_revision_id"] != current["latest_revision_id"]:
        raise _bad("PM_PLAN_REVISION_CONFLICT", "A draft must name the current document revision as its parent", 409)
    document, scope_digest = _document(ops.db, target["workspace_id"], thread, params, revision_id)
    return thread, current, document, scope_digest


def _admit_draft(ops, principal, target, params, pre):
    _check_draft(ops, principal, target, params, pre, "pmrev_" + "0" * 32)
    return {"principal": pm_workspace.principal_binding(principal)}


async def _run_draft(ctx):
    with ctx.service.journal.tx():
        applied = ctx.service.db.execute("SELECT result FROM pm_plan_applied WHERE operation_id=?", (ctx.operation_id,)).fetchone()
        if applied:
            return json.loads(applied[0])
        principal = pm_workspace.admitted_principal(ctx)
        revision_id = "pmrev_" + ctx.operation_id[3:]
        thread, current, document, scope_digest = _check_draft(ctx.service, principal, ctx.target, ctx.params,
                                                             ctx.preconditions, revision_id)
        now, workspace_id = time.time(), ctx.target["workspace_id"]
        files = _files(revision_id, document)
        ctx.service.db.execute("""INSERT INTO pm_plan_revisions(revision_id,workspace_id,thread_id,planning_draft_id,
            previous_revision_id,base_accepted_revision_id,document_digest,scope_digest,document,created_by,created_at,operation_id)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""", (revision_id, workspace_id, thread["thread_id"], thread["draft_id"],
            current["latest_revision_id"], current["accepted_revision_id"], files["document_digest"], scope_digest,
            _json(document), ctx.actor, now, ctx.operation_id))
        ctx.service.db.execute("""INSERT INTO pm_plan_heads VALUES(?,?,?,?) ON CONFLICT(workspace_id)
            DO UPDATE SET version=excluded.version,latest_revision_id=excluded.latest_revision_id""",
            (workspace_id, current["version"] + 1, revision_id, current["accepted_revision_id"]))
        result = {"workspace_id": workspace_id, "revision_id": revision_id, "version": current["version"] + 1,
                  "document_digest": files["document_digest"], "structured_scope_digest": scope_digest,
                  "accepted_revision_id": current["accepted_revision_id"], "status": "draft", "execution_authorized": False}
        ctx.service.db.execute("INSERT INTO pm_plan_applied VALUES(?,?,?)", (ctx.operation_id, _json(result), now))
        ctx.service.journal.api_event("pm_plan", revision_id, "pm.plan.drafted", {**result, "operation_id": ctx.operation_id}, actor=ctx.actor)
        return result


def authorize_existing(ops, principal, op, verb):
    if not principal.allows("manage"):
        raise _bad("FORBIDDEN", "Plan operations require manage scope", 403)
    pm_workspace.require_member(ops.db, principal, op["target"].get("workspace_id"), write=True,
                                decide=op["action"] == "pm.plan.adopt")


def prepare_adoption(ops, principal, target, params, pre):
    """Admission helper for the integration layer's real managed-Git action."""
    if not principal.allows("manage"):
        raise _bad("FORBIDDEN", "Plan adoption requires manage scope", 403)
    _only(target, ("workspace_id", "revision_id"))
    _only(params, ("rationale",))
    if set(pre) != {"expected_accepted_revision_id"}:
        raise _bad("PRECONDITION_REQUIRED", "Supply the previous accepted revision, or null for the first adoption")
    pm_workspace.require_member(ops.db, principal, target.get("workspace_id"), decide=True)
    revision = _revision(ops.db, target["workspace_id"], target.get("revision_id"))
    current = head(ops.db, target["workspace_id"])
    if (pre["expected_accepted_revision_id"] != current["accepted_revision_id"]
            or revision["base_accepted_revision_id"] != current["accepted_revision_id"]):
        raise _bad("PM_ACCEPTED_REVISION_CONFLICT", "Accepted plan changed; prepare a revision against the current baseline", 409)
    _text(params.get("rationale"), "rationale", 8000)
    revision_files(ops.db, target["workspace_id"], revision["revision_id"])
    return {"principal": pm_workspace.principal_binding(principal), "revision_id": revision["revision_id"],
            "document_digest": revision["document_digest"], "scope_digest": revision["scope_digest"]}


def _adoption_context(ctx):
    if ctx.op["action"] != "pm.plan.adopt":
        raise _bad("PM_ADOPTION_AUTHORITY_REQUIRED", "Adoption requires its original registered operation", 403)
    principal = pm_workspace.admitted_principal(ctx)
    revision = _revision(ctx.service.db, ctx.target.get("workspace_id"), ctx.target.get("revision_id"))
    binding = ctx.admission_binding or {}
    if (binding.get("revision_id") != revision["revision_id"]
            or binding.get("document_digest") != revision["document_digest"]
            or binding.get("scope_digest") != revision["scope_digest"]):
        raise _bad("PM_ADOPTION_AUTHORITY_REQUIRED", "Original operation does not bind this exact plan revision", 403)
    return principal, revision


def _validate_receipt(ctx, revision, receipt):
    required = {"operation_id", "workspace_id", "revision_id", "commit_sha", "tree_sha", "document_digest",
                "file_digests", "binding_id", "status"}
    if not isinstance(receipt, dict) or set(receipt) != required:
        raise _bad("PM_GIT_RECEIPT_INVALID", "A verified exact Git receipt is required", 409)
    files = revision_files(ctx.service.db, revision["workspace_id"], revision["revision_id"])
    if (receipt["operation_id"] != ctx.operation_id or receipt["workspace_id"] != revision["workspace_id"]
            or receipt["revision_id"] != revision["revision_id"] or receipt["status"] != "verified"
            or receipt["document_digest"] != files["document_digest"] or receipt["file_digests"] != files["file_digests"]
            or receipt["binding_id"] != (ctx.admission_binding or {}).get("git_binding_id")
            or not isinstance(receipt["commit_sha"], str) or not GIT_SHA.fullmatch(receipt["commit_sha"])
            or not isinstance(receipt["tree_sha"], str) or not GIT_SHA.fullmatch(receipt["tree_sha"])
            or len(receipt["commit_sha"]) != len(receipt["tree_sha"])
            or not isinstance(receipt["binding_id"], str) or not DIGEST.fullmatch(receipt["binding_id"])):
        raise _bad("PM_GIT_RECEIPT_INVALID", "Git evidence does not identify this operation's exact revision files", 409)
    return receipt


def record_git_receipt(ctx, receipt):
    """Internal: call only after managed Git readback; never expose as an action.

    Recording an already occurred effect remains possible after membership loss.
    The subsequent adoption still requires current owner membership.
    """
    _, revision = _adoption_context(ctx)
    _validate_receipt(ctx, revision, receipt)
    with ctx.service.journal.tx():
        saved = ctx.service.db.execute("SELECT receipt FROM pm_plan_git_receipts WHERE operation_id=?", (ctx.operation_id,)).fetchone()
        if saved:
            if json.loads(saved[0]) != receipt:
                raise _bad("PM_GIT_RECEIPT_CONFLICT", "Original Git receipt is immutable", 409)
            return json.loads(saved[0])
        ctx.service.db.execute("INSERT INTO pm_plan_git_receipts VALUES(?,?,?,?,?)",
                               (ctx.operation_id, revision["workspace_id"], revision["revision_id"], _json(receipt), time.time()))
    return receipt


def adopt_revision(ctx):
    """Atomically adopt only a durable verified Git revision; replay original fact first."""
    with ctx.service.journal.tx():
        applied = ctx.service.db.execute("SELECT result FROM pm_plan_adoptions WHERE operation_id=?", (ctx.operation_id,)).fetchone()
        if applied:
            return json.loads(applied[0])
        principal, revision = _adoption_context(ctx)
        prepare_adoption(ctx.service, principal, ctx.target, ctx.params, ctx.preconditions)
        saved = ctx.service.db.execute("SELECT receipt FROM pm_plan_git_receipts WHERE operation_id=?", (ctx.operation_id,)).fetchone()
        if not saved:
            raise _bad("PM_GIT_RECEIPT_REQUIRED", "Git commit and exact readback have not been recorded", 409)
        receipt = _validate_receipt(ctx, revision, json.loads(saved[0]))
        workspace_id = revision["workspace_id"]
        current = head(ctx.service.db, workspace_id)
        previous_id = current["accepted_revision_id"]
        previous = _revision(ctx.service.db, workspace_id, previous_id) if previous_id else None
        prior_adoption = ctx.service.db.execute("SELECT scope_revision FROM pm_plan_adoptions WHERE workspace_id=? AND revision_id=? ORDER BY created_at DESC LIMIT 1",
                                                (workspace_id, previous_id)).fetchone() if previous_id else None
        structured_changed = previous is None or previous["scope_digest"] != revision["scope_digest"]
        prose_changed = previous is not None and any(previous["document"][key] != revision["document"][key]
                                                    for key in ("markdown", "title"))
        scope_impact = "changed" if structured_changed else "unknown" if prose_changed else "document_only"
        scope_changed = True if structured_changed else None if prose_changed else False
        scope_revision = "pmscope_" + ctx.operation_id[3:] if scope_changed is not False else prior_adoption[0]
        result = {"workspace_id": workspace_id, "revision_id": revision["revision_id"], "document_revision": revision["revision_id"],
                  "scope_revision": scope_revision, "scope_changed": scope_changed, "scope_impact": scope_impact,
                  "structured_scope_digest": revision["scope_digest"], "previous_revision_id": previous_id,
                  "decision_id": "pmdec_" + ctx.operation_id[3:], "document_digest": revision["document_digest"],
                  "commit_sha": receipt["commit_sha"], "tree_sha": receipt["tree_sha"], "adopted_by": ctx.actor,
                  "status": "adopted", "execution_authorized": False, "version": current["version"] + 1}
        ctx.service.db.execute("INSERT INTO pm_plan_adoptions VALUES(?,?,?,?,?,?,?,?,?,?)", (ctx.operation_id, workspace_id,
            revision["revision_id"], previous_id, scope_revision, result["decision_id"], ctx.params["rationale"], ctx.actor, time.time(), _json(result)))
        ctx.service.db.execute("UPDATE pm_plan_heads SET accepted_revision_id=?,version=version+1 WHERE workspace_id=?",
                               (revision["revision_id"], workspace_id))
        ctx.service.journal.api_event("pm_plan", revision["revision_id"], "pm.plan.adopted", {**result, "operation_id": ctx.operation_id}, actor=ctx.actor)
        return result


def latest_git_receipt(db, workspace_id):
    """Internal previous Git fact, including commits not yet adopted in SQLite."""
    row = db.execute("SELECT receipt FROM pm_plan_git_receipts WHERE workspace_id=? ORDER BY created_at DESC,operation_id DESC LIMIT 1",
                      (workspace_id,)).fetchone()
    return json.loads(row[0]) if row else None


def _read(ops, principal, workspace_id):
    if not principal.allows("observe"):
        raise _bad("FORBIDDEN", "Plan reads require observe scope", 403)
    pm_workspace.require_member(ops.db, principal, workspace_id)


def _get_plan(ops, principal, workspace_id, revision_id):
    _read(ops, principal, workspace_id)
    revision = _revision(ops.db, workspace_id, revision_id)
    revision_files(ops.db, workspace_id, revision_id)
    adoption = ops.db.execute("SELECT result,rationale,created_at FROM pm_plan_adoptions WHERE workspace_id=? AND revision_id=? ORDER BY created_at DESC LIMIT 1",
                              (workspace_id, revision_id)).fetchone()
    revision["adoption"] = {**json.loads(adoption[0]), "rationale": adoption[1], "created_at": adoption[2]} if adoption else None
    revision["status"] = "adopted" if adoption else "draft"
    revision["structured_scope_digest"] = revision.pop("scope_digest")
    revision["is_current_accepted"] = head(ops.db, workspace_id)["accepted_revision_id"] == revision_id
    return {"version": 1, "plan": revision, "head": head(ops.db, workspace_id)}


def get_plan(ops, principal, workspace_id, revision_id):
    with ops.journal.tx():
        return _get_plan(ops, principal, workspace_id, revision_id)


def accepted_plan(ops, principal, workspace_id):
    with ops.journal.tx():
        _read(ops, principal, workspace_id)
        current = head(ops.db, workspace_id)
        return _get_plan(ops, principal, workspace_id, current["accepted_revision_id"]) if current["accepted_revision_id"] else {
            "version": 1, "head": current, "plan": None}


def list_plans(ops, principal, workspace_id, *, after=0, limit=50):
    with ops.journal.tx():
        _read(ops, principal, workspace_id)
        if type(after) is not int or after < 0 or type(limit) is not int or not 1 <= limit <= 100:
            raise _bad("INVALID_PARAMS", "Use a nonnegative sequence and limit 1-100")
        rows = ops.db.execute("""SELECT sequence,revision_id,thread_id,planning_draft_id,previous_revision_id,
            document_digest,scope_digest AS structured_scope_digest,created_by,created_at FROM pm_plan_revisions
            WHERE workspace_id=? AND sequence>? ORDER BY sequence LIMIT ?""", (workspace_id, after, limit + 1)).fetchall()
        return {"version": 1, "workspace_id": workspace_id, "head": head(ops.db, workspace_id),
                "revisions": [dict(row) for row in rows[:limit]], "next_cursor": rows[limit - 1]["sequence"] if len(rows) > limit else None}
