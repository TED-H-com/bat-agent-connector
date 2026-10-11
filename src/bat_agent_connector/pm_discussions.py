"""Durable, sourced PM discussions and explicit Planning drafts.

All effects are central journal transactions. No runner, shell, adoption or
dispatch is invoked, including when those instructions occur in source text.
"""

from __future__ import annotations

import json
import re
import time

from . import pm_profiles, pm_workspace
from .operations import ActionDef, OperationError

THREAD_ID = re.compile(r"pmt_[0-9a-f]{32}")
INPUT_ID = re.compile(r"pmi_[0-9a-f]{32}")
MESSAGE_ID = re.compile(r"pmm_[0-9a-f]{32}")
DRAFT_ID = re.compile(r"pmd_[0-9a-f]{32}")
SLOT = re.compile(r"[a-z][a-z0-9_.-]{0,79}")
ACTIONS = ("pm.thread.create", "pm.message.append", "pm.input.record", "pm.input.confirm", "pm.thread.plan", "pm.thread.plan.revise")


def install(ops):
    pm_workspace.schema(ops.journal)
    with ops.journal.tx():
        for statement in (
            """CREATE TABLE IF NOT EXISTS pm_threads (
                thread_id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL REFERENCES pm_workspaces(workspace_id),
                title TEXT NOT NULL, mode TEXT NOT NULL, version INTEGER NOT NULL,
                created_by TEXT NOT NULL, created_at REAL NOT NULL, updated_at REAL NOT NULL,
                draft_id TEXT, operation_id TEXT NOT NULL UNIQUE)""",
            """CREATE TABLE IF NOT EXISTS pm_messages (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT, message_id TEXT NOT NULL UNIQUE,
                workspace_id TEXT NOT NULL, thread_id TEXT NOT NULL REFERENCES pm_threads(thread_id),
                actor TEXT NOT NULL, content TEXT NOT NULL, created_at REAL NOT NULL, operation_id TEXT NOT NULL UNIQUE)""",
            """CREATE TABLE IF NOT EXISTS pm_inputs (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT, input_id TEXT NOT NULL UNIQUE,
                workspace_id TEXT NOT NULL, thread_id TEXT NOT NULL REFERENCES pm_threads(thread_id),
                slot TEXT NOT NULL, value TEXT NOT NULL, status TEXT NOT NULL, source_ref TEXT NOT NULL,
                recorded_by TEXT NOT NULL, confirmed_by TEXT, observed_at REAL NOT NULL, applicable_revision TEXT NOT NULL,
                supersedes_input_id TEXT UNIQUE, confirmation_rationale TEXT,
                operation_id TEXT NOT NULL UNIQUE)""",
            """CREATE TABLE IF NOT EXISTS pm_planning_drafts (
                draft_id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL, thread_id TEXT NOT NULL UNIQUE,
                title TEXT NOT NULL, rationale TEXT NOT NULL, selected_input_ids TEXT NOT NULL,
                candidate_input_ids TEXT NOT NULL, source_thread_version INTEGER NOT NULL,
                created_by TEXT NOT NULL, created_at REAL NOT NULL, operation_id TEXT NOT NULL UNIQUE)""",
            """CREATE TABLE IF NOT EXISTS pm_planning_draft_revisions (
                draft_id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL, thread_id TEXT NOT NULL,
                title TEXT NOT NULL, rationale TEXT NOT NULL, selected_input_ids TEXT NOT NULL,
                candidate_input_ids TEXT NOT NULL, source_thread_version INTEGER NOT NULL,
                created_by TEXT NOT NULL, created_at REAL NOT NULL, operation_id TEXT NOT NULL UNIQUE,
                previous_draft_id TEXT NOT NULL UNIQUE)""",
            """CREATE TABLE IF NOT EXISTS pm_discussion_applied (
                operation_id TEXT PRIMARY KEY, result TEXT NOT NULL, applied_at REAL NOT NULL)""",
            "CREATE INDEX IF NOT EXISTS pm_thread_workspace ON pm_threads(workspace_id,created_at,thread_id)",
            "CREATE INDEX IF NOT EXISTS pm_messages_thread ON pm_messages(thread_id,sequence)",
            "CREATE INDEX IF NOT EXISTS pm_inputs_thread ON pm_inputs(thread_id,sequence)",
        ):
            ops.db.execute(statement)
        # The model is append-only even for confirmation/correction. Keep source history intact.
        for table in ("pm_messages", "pm_inputs", "pm_planning_drafts", "pm_planning_draft_revisions"):
            for verb in ("UPDATE", "DELETE"):
                ops.db.execute(f"CREATE TRIGGER IF NOT EXISTS {table}_{verb.lower()}_immutable BEFORE {verb} ON {table} "
                               "BEGIN SELECT RAISE(ABORT, 'PM source history is immutable'); END")
    for action in ACTIONS:
        if action not in ops.actions:
            ops.register(ActionDef(action, "manage", "Save PM discussion or an explicit Planning draft", _run,
                _admitter(action), ("workspace_id",) if action == "pm.thread.create" else ("workspace_id", "thread_id"),
                authorize_existing=_authorize_existing))


def _bad(message, code="INVALID_PARAMS", status=422):
    return OperationError(code, message, status)


def _text(value, field, limit):
    if not isinstance(value, str) or not value.strip() or len(value) > limit or "\x00" in value:
        raise _bad(f"{field} must be nonempty text of at most {limit} characters")
    return value  # Preserve the caller's original words, including whitespace and command-looking text.


def _only(value, allowed):
    if not isinstance(value, dict) or set(value) - set(allowed):
        raise _bad("unexpected PM fields")


def _thread(db, workspace_id, thread_id):
    if not isinstance(thread_id, str) or not THREAD_ID.fullmatch(thread_id):
        raise _bad("no such discussion", "PM_THREAD_NOT_FOUND", 404)
    row = db.execute("SELECT * FROM pm_threads WHERE workspace_id=? AND thread_id=?", (workspace_id, thread_id)).fetchone()
    if not row:
        raise _bad("no such discussion", "PM_THREAD_NOT_FOUND", 404)
    return dict(row)


def _input(db, thread_id, input_id, *, current=False):
    if not isinstance(input_id, str) or not INPUT_ID.fullmatch(input_id):
        raise _bad("no such input", "PM_INPUT_NOT_FOUND", 404)
    row = db.execute("SELECT * FROM pm_inputs WHERE thread_id=? AND input_id=?", (thread_id, input_id)).fetchone()
    if row is None:
        raise _bad("no such input", "PM_INPUT_NOT_FOUND", 404)
    if current and db.execute("SELECT 1 FROM pm_inputs WHERE supersedes_input_id=?", (input_id,)).fetchone():
        raise _bad("input was superseded; read its current revision", "PM_INPUT_CHANGED", 409)
    return dict(row)


def _decision(action, params):
    return action in {"pm.input.confirm", "pm.thread.plan", "pm.thread.plan.revise"} or (
        action == "pm.input.record" and params.get("status") == "user_confirmed")


def _member(ops, principal, action, target, params):
    decide = _decision(action, params)
    if action == "pm.input.record" and isinstance(params.get("supersedes_input_id"), str):
        old = ops.db.execute("SELECT confirmed_by FROM pm_inputs WHERE thread_id=? AND input_id=?",
                            (target.get("thread_id"), params["supersedes_input_id"])).fetchone()
        decide = decide or bool(old and old[0])
    return pm_workspace.require_member(ops.db, principal, target.get("workspace_id"), write=True, decide=decide)


def _authorize_existing(ops, principal, op, control):
    if not principal.allows("manage"):
        raise _bad("PM operation controls require manage scope", "FORBIDDEN", 403)
    _member(ops, principal, op["action"], op["target"], op["params"])


def _source(db, thread_id, value):
    _only(value, ("kind", "message_id", "ref", "revision", "path"))
    kind = value.get("kind")
    if not isinstance(kind, str):
        raise _bad("source kind must be text")
    if kind == "message":
        if set(value) != {"kind", "message_id"} or not isinstance(value.get("message_id"), str):
            raise _bad("message source needs an exact message_id")
        row = db.execute("SELECT 1 FROM pm_messages WHERE thread_id=? AND message_id=?",
                         (thread_id, value["message_id"])).fetchone()
        if not row:
            raise _bad("source message is not in this discussion", "PM_SOURCE_NOT_FOUND", 404)
    elif kind in {"repository", "external", "statement"}:
        if "message_id" in value or (kind != "repository" and set(value) - {"kind", "ref"}):
            raise _bad("source fields do not match source kind")
        _text(value.get("ref"), "source_ref.ref", 2000)
        for key in ("revision", "path"):
            if key in value:
                _text(value[key], f"source_ref.{key}", 1000)
    else:
        raise _bad("source kind must be message, repository, external or statement")
    return value


def _check(ops, principal, action, target, params, pre):
    workspace = _member(ops, principal, action, target, params)
    create = action == "pm.thread.create"
    if set(target) != ({"workspace_id"} if create else {"workspace_id", "thread_id"}):
        raise _bad("PM action needs exact workspace and discussion targets")
    revise = action == "pm.thread.plan.revise"
    expected = {"expected_version", "expected_draft_id"} if revise else {"expected_version"}
    if set(pre) != expected or type(pre.get("expected_version")) is not int:
        raise _bad("exact expected_version and expected_draft_id are required" if revise else "expected_version is required",
                   "PRECONDITION_REQUIRED")
    if revise and (not isinstance(pre["expected_draft_id"], str) or not DRAFT_ID.fullmatch(pre["expected_draft_id"])):
        raise _bad("expected_draft_id must identify the current Planning draft", "PRECONDITION_REQUIRED")
    thread = None if create else _thread(ops.db, workspace["workspace_id"], target["thread_id"])
    version = workspace["version"] if create else thread["version"]
    if pre["expected_version"] != version:
        raise _bad("read the current PM version before changing it", "VERSION_CONFLICT", 409)
    if thread and action not in pm_profiles.get(thread["mode"])["allowed_commands"]:
        raise _bad("command is unavailable in this discussion mode", "PM_MODE_REFUSED", 409)
    if revise and pre["expected_draft_id"] != thread["draft_id"]:
        raise _bad("read the current Planning draft before revising its sources", "PM_DRAFT_CONFLICT", 409)
    if create:
        _only(params, ("title",))
        _text(params.get("title"), "title", 160)
    elif action == "pm.message.append":
        _only(params, ("content",))
        _text(params.get("content"), "content", 12000)
    elif action == "pm.input.record":
        _only(params, ("slot", "value", "status", "source_ref", "applicable_revision", "supersedes_input_id"))
        if not isinstance(params.get("slot"), str) or not SLOT.fullmatch(params["slot"]):
            raise _bad("slot must be a stable lowercase field key")
        _text(params.get("value"), "value", 4000)
        if params.get("status") not in pm_profiles.STATUSES:
            raise _bad("unknown input status")
        _source(ops.db, thread["thread_id"], params.get("source_ref"))
        if params.get("status") == "repo_evidence" and params["source_ref"]["kind"] != "repository":
            raise _bad("repo_evidence needs an attributed repository reference")
        revision = params.get("applicable_revision")
        if revision is not None and (not isinstance(revision, str)
                or revision not in {f"{thread['thread_id']}@{thread['version']}", thread["draft_id"]}):
            raise _bad("applicable_revision must name the current discussion or its draft")
        if params.get("supersedes_input_id") is not None:
            old = _input(ops.db, thread["thread_id"], params["supersedes_input_id"], current=True)
            if old["slot"] != params["slot"]:
                raise _bad("a correction must preserve the input slot")
            if old["confirmed_by"]:
                pm_workspace.require_member(ops.db, principal, target["workspace_id"], decide=True)
    elif action == "pm.input.confirm":
        _only(params, ("input_id", "rationale"))
        _input(ops.db, thread["thread_id"], params.get("input_id"), current=True)
        _text(params.get("rationale"), "rationale", 4000)
    elif action in {"pm.thread.plan", "pm.thread.plan.revise"}:
        _only(params, ("selected_input_ids", "rationale", "title"))
        selected = params.get("selected_input_ids")
        if (not isinstance(selected, list) or not 1 <= len(selected) <= 50
                or not all(isinstance(value, str) for value in selected) or len(set(selected)) != len(selected)):
            raise _bad("select 1-50 distinct current input IDs")
        for input_id in selected:
            _input(ops.db, thread["thread_id"], input_id, current=True)
        _text(params.get("rationale"), "rationale", 4000)
        if "title" in params:
            _text(params["title"], "title", 160)
    return workspace, thread


def _admitter(action):
    def admit(ops, principal, target, params, pre):
        _check(ops, principal, action, target, params, pre)
        return {"principal": pm_workspace.principal_binding(principal)}
    return admit


async def _run(ctx):
    principal = pm_workspace.admitted_principal(ctx)
    with ctx.service.journal.tx():
        saved = ctx.service.db.execute("SELECT result FROM pm_discussion_applied WHERE operation_id=?", (ctx.operation_id,)).fetchone()
        if saved:
            return json.loads(saved[0])
        _member(ctx.service, principal, ctx.op["action"], ctx.target, ctx.params)
        _, thread = _check(ctx.service, principal, ctx.op["action"], ctx.target, ctx.params, ctx.preconditions)
        result = _apply(ctx, thread)
        ctx.service.db.execute("INSERT INTO pm_discussion_applied VALUES(?,?,?)",
                               (ctx.operation_id, json.dumps(result, ensure_ascii=False), time.time()))
    return result


def _apply(ctx, thread):
    db, params, now = ctx.service.db, ctx.params, time.time()
    suffix, workspace_id = ctx.operation_id[3:], ctx.target["workspace_id"]
    action = ctx.op["action"]
    if action == "pm.thread.create":
        thread_id = "pmt_" + suffix
        db.execute("INSERT INTO pm_threads VALUES(?,?,?,?,?,?,?,?,?,?)",
            (thread_id, workspace_id, params["title"], "idea", 1, ctx.actor, now, now, None, ctx.operation_id))
        result = {"thread_id": thread_id, "version": 1, "mode": "idea"}
    else:
        thread_id = thread["thread_id"]
        result = {"thread_id": thread_id, "version": thread["version"] + 1, "mode": thread["mode"]}
        if action == "pm.message.append":
            result["message_id"] = "pmm_" + suffix
            db.execute("INSERT INTO pm_messages(message_id,workspace_id,thread_id,actor,content,created_at,operation_id) VALUES(?,?,?,?,?,?,?)",
                       (result["message_id"], workspace_id, thread_id, ctx.actor, params["content"], now, ctx.operation_id))
        elif action in {"pm.input.record", "pm.input.confirm"}:
            confirm = action == "pm.input.confirm"
            values = _input(db, thread_id, params["input_id"]) if confirm else params
            status = "user_confirmed" if confirm else values["status"]
            result["input_id"] = "pmi_" + suffix
            source = values["source_ref"] if confirm else json.dumps(values["source_ref"], ensure_ascii=False)
            db.execute("""INSERT INTO pm_inputs(input_id,workspace_id,thread_id,slot,value,status,source_ref,
                recorded_by,confirmed_by,observed_at,applicable_revision,supersedes_input_id,confirmation_rationale,operation_id)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (result["input_id"], workspace_id, thread_id, values["slot"], values["value"], status, source,
                 ctx.actor, ctx.actor if status == "user_confirmed" else None, now,
                 values.get("applicable_revision") or f"{thread_id}@{thread['version']}",
                 params["input_id"] if confirm else params.get("supersedes_input_id"),
                 params["rationale"] if confirm else None, ctx.operation_id))
        else:
            selected = params["selected_input_ids"]
            candidates = [row[0] for row in db.execute("""SELECT input_id FROM pm_inputs i WHERE thread_id=?
                AND NOT EXISTS(SELECT 1 FROM pm_inputs n WHERE n.supersedes_input_id=i.input_id)
                ORDER BY sequence""", (thread_id,)) if row[0] not in selected]
            result.update(draft_id="pmd_" + suffix, mode="planning", execution_authorized=False)
            revise = action == "pm.thread.plan.revise"
            title = _draft_row(db, thread)["title"] if revise else thread["title"]
            values = (result["draft_id"], workspace_id, thread_id, params.get("title", title), params["rationale"],
                      json.dumps(selected), json.dumps(candidates), thread["version"], ctx.actor, now, ctx.operation_id)
            if revise:
                result["previous_draft_id"] = thread["draft_id"]
                db.execute("INSERT INTO pm_planning_draft_revisions VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", (*values, thread["draft_id"]))
            else:
                db.execute("INSERT INTO pm_planning_drafts VALUES(?,?,?,?,?,?,?,?,?,?,?)", values)
            db.execute("UPDATE pm_threads SET mode='planning',draft_id=? WHERE thread_id=?", (result["draft_id"], thread_id))
        db.execute("UPDATE pm_threads SET version=version+1,updated_at=? WHERE thread_id=?", (now, thread_id))
    result["workspace_id"] = workspace_id
    ctx.service.journal.api_event("pm_thread", thread_id, action,
        {**result, "operation_id": ctx.operation_id}, actor=ctx.actor)
    return result


def _read(ops, principal, workspace_id):
    if not principal.allows("observe"):
        raise _bad("PM reads require observe scope", "FORBIDDEN", 403)
    return pm_workspace.require_member(ops.db, principal, workspace_id)


def _limit(limit):
    if type(limit) is not int or not 1 <= limit <= 100:
        raise _bad("limit must be 1-100")
    return limit


def _input_out(row):
    result = dict(row)
    result["source_ref"] = json.loads(result["source_ref"])
    result["evidence_verification"] = "central_message" if result["source_ref"]["kind"] == "message" else "unverified_claim"
    return result


def _capabilities(ops, workspace, principal):
    from . import pm_native_capabilities

    role = workspace["membership"]["role"]
    return {"write": principal.allows("manage") and (principal.admin or role in {"owner", "contributor"}),
            "decide": principal.allows("manage") and (principal.admin or role == "owner"),
            "runtime": False, "runtime_adapter": "bat", "runtime_legacy": True,
            "native_analysis": pm_native_capabilities.read(ops, principal, workspace),
            "adopt": False, "dispatch": False}


def list_threads(ops, principal, workspace_id, *, limit=50, after=None):
    workspace = _read(ops, principal, workspace_id)
    _limit(limit)
    if after is not None:
        _thread(ops.db, workspace_id, after)
    rows = ops.db.execute("SELECT * FROM pm_threads WHERE workspace_id=? AND (? IS NULL OR thread_id>?) ORDER BY thread_id LIMIT ?",
                          (workspace_id, after, after, limit + 1)).fetchall()
    return {"version": 1, "workspace_id": workspace_id, "threads": [dict(row) for row in rows[:limit]],
            "next_cursor": rows[limit - 1]["thread_id"] if len(rows) > limit else None,
            "capabilities": _capabilities(ops, workspace, principal), **pm_profiles.catalog(),
            "runtime": {"available": False, "adapter": "bat", "legacy": True,
                        "reason": "external_tool_policy_unverified"}}


def _draft_row(db, thread, *, draft_id=None):
    """Read an exact immutable selection, including the original pre-revision table."""
    draft_id = thread["draft_id"] if draft_id is None else draft_id
    if not draft_id:
        return None
    if not isinstance(draft_id, str) or not DRAFT_ID.fullmatch(draft_id):
        raise _bad("no such Planning draft", "PM_DRAFT_NOT_FOUND", 404)
    params = (thread["workspace_id"], thread["thread_id"], draft_id)
    row = db.execute("SELECT *,NULL AS previous_draft_id FROM pm_planning_drafts WHERE workspace_id=? AND thread_id=? AND draft_id=?",
                     params).fetchone()
    if row is None:
        row = db.execute("SELECT * FROM pm_planning_draft_revisions WHERE workspace_id=? AND thread_id=? AND draft_id=?", params).fetchone()
    if row is None:
        raise _bad("no such Planning draft", "PM_DRAFT_NOT_FOUND", 404)
    return dict(row)


def _draft(db, thread, *, draft_id=None):
    draft = _draft_row(db, thread, draft_id=draft_id)
    if draft is None:
        return None
    for field in ("selected_input_ids", "candidate_input_ids"):
        draft[field] = json.loads(draft[field])
    # Selected sources are always returned in full even if the current-input page excludes them.
    draft["selected_inputs"] = [_input_out(_input(db, thread["thread_id"], value)) for value in draft["selected_input_ids"]]
    draft["candidate_inputs"] = [_input_out(_input(db, thread["thread_id"], value)) for value in draft["candidate_input_ids"][:100]]
    slots = {row["slot"] for row in draft["selected_inputs"]}
    draft.update(status="draft", scope_revision=None, execution_authorized=False, adopted=False,
        candidate_mode="idea", candidates_truncated=len(draft["candidate_input_ids"]) > 100,
        unknowns=[{"slot": slot["key"], "domain": slot["domain"], "status": "unresolved"}
                  for slot in pm_profiles.get("planning")["input_slots"] if slot["key"] not in slots])
    return draft


def get_thread(ops, principal, workspace_id, thread_id, *, limit=50, before_message=None, before_input=None):
    from .pm_runs import message_out

    workspace = _read(ops, principal, workspace_id)
    _limit(limit)
    for cursor in (before_message, before_input):
        if cursor is not None and (type(cursor) is not int or cursor < 1):
            raise _bad("history cursors must be positive sequence numbers")
    with ops.journal.tx():
        thread = _thread(ops.db, workspace_id, thread_id)
        messages = ops.db.execute("""SELECT * FROM pm_messages WHERE thread_id=? AND (? IS NULL OR sequence<?)
            ORDER BY sequence DESC LIMIT ?""", (thread_id, before_message, before_message, limit + 1)).fetchall()
        history = ops.db.execute("""SELECT * FROM pm_inputs WHERE thread_id=? AND (? IS NULL OR sequence<?)
            ORDER BY sequence DESC LIMIT ?""", (thread_id, before_input, before_input, limit + 1)).fetchall()
        inputs = ops.db.execute("""SELECT * FROM pm_inputs i WHERE thread_id=?
            AND NOT EXISTS(SELECT 1 FROM pm_inputs n WHERE n.supersedes_input_id=i.input_id)
            ORDER BY sequence DESC LIMIT 101""", (thread_id,)).fetchall()
        return {"version": 1, "thread": thread, "profile": pm_profiles.get(thread["mode"]),
            "messages": [message_out(ops.db, row) for row in reversed(messages[:limit])], "inputs": [_input_out(row) for row in inputs[:100]],
            "input_history": [_input_out(row) for row in history[:limit]], "draft": _draft(ops.db, thread),
            "next_message_cursor": messages[limit - 1]["sequence"] if len(messages) > limit else None,
            "next_input_cursor": history[limit - 1]["sequence"] if len(history) > limit else None,
            "coverage": {"messages_truncated": len(messages) > limit, "inputs_truncated": len(inputs) > 100,
                         "history_truncated": len(history) > limit},
            "capabilities": _capabilities(ops, workspace, principal)}


def context(ops, principal, workspace_id, thread_id, *, max_chars=32000):
    if type(max_chars) is not int or not 2000 <= max_chars <= 64000:
        raise _bad("max_chars must be 2000-64000")
    doc = get_thread(ops, principal, workspace_id, thread_id, limit=20)
    result = {"version": 1, "thread_id": thread_id, "workspace_id": workspace_id,
        "thread_version": doc["thread"]["version"], "mode": doc["thread"]["mode"],
        "profile": doc["profile"], "messages": [], "inputs_by_status": {status: [] for status in pm_profiles.STATUSES},
        "draft_id": doc["draft"]["draft_id"] if doc["draft"] else None,
        "selected_input_ids": doc["draft"]["selected_input_ids"] if doc["draft"] else [],
        "coverage": {**doc["coverage"], "omitted_message_ids": [], "omitted_input_ids": [], "max_chars": max_chars},
        "authority": "context_only", "runtime_available": False, "execution_authorized": False}
    known_slots = {item["slot"] for item in doc["inputs"]}
    result["missing_slots"] = [{**slot, "status": "unresolved", "basis": "not_in_loaded_current_inputs"}
                               for slot in doc["profile"]["input_slots"] if slot["key"] not in known_slots]
    # Bound serialized context content, keep every selected source ID even when its text does not fit.
    inputs = [*(doc["draft"]["selected_inputs"] if doc["draft"] else []), *doc["inputs"]]
    seen = set()
    used = 0
    for item in inputs:
        if item["input_id"] in seen:
            continue
        seen.add(item["input_id"])
        size = len(json.dumps(item, ensure_ascii=False))
        if used + size > max_chars:
            result["coverage"]["omitted_input_ids"].append(item["input_id"])
        else:
            result["inputs_by_status"][item["status"]].append(item)
            used += size
    for message in reversed(doc["messages"]):
        size = len(json.dumps(message, ensure_ascii=False))
        if used + size > max_chars:
            result["coverage"]["omitted_message_ids"].append(message["message_id"])
        else:
            result["messages"].insert(0, message)
            used += size
    result["coverage"]["content_chars"] = used
    result["coverage"]["bounded_content_only"] = True
    return result
