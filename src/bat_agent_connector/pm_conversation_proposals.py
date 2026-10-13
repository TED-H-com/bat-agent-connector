"""Explicit conversation-to-plan proposals on the existing bounded PM runtime."""
from __future__ import annotations

import hashlib
import json
import re
import time
from contextvars import ContextVar

from . import pm_conversation_format as fmt
from . import (
    pm_discussions,
    pm_native_journal,
    pm_native_operations,
    pm_plans,
    pm_profiles,
    pm_runs,
    pm_workspace,
)
from .operations import ActionDef, OperationError, OperationService

ACTION = "pm.conversation.propose"
ADOPT = "pm.conversation.adopt"
ID = re.compile(r"pmcp_[0-9a-f]{32}")
_ACCESS = ContextVar("pm_conversation_access", default=None)


def error(message, code="PM_CONVERSATION_INVALID", status=422):
    return OperationError(code, message, status)


def schema(journal):
    from . import pm_bat_runtime
    pm_bat_runtime.schema(journal)
    pm_native_journal.schema(journal)
    pm_plans.schema(journal)
    from .pm_source_lineage import schema as lineage_schema
    lineage_schema(journal)
    with journal.tx():
        journal.db.execute("""CREATE TABLE IF NOT EXISTS pm_conversation_proposals (
            sequence INTEGER PRIMARY KEY AUTOINCREMENT, proposal_id TEXT NOT NULL UNIQUE,
            workspace_id TEXT NOT NULL, thread_id TEXT NOT NULL, operation_id TEXT NOT NULL UNIQUE,
            run_id TEXT NOT NULL UNIQUE, context_digest TEXT NOT NULL, raw_reply TEXT NOT NULL,
            raw_reply_digest TEXT NOT NULL, document TEXT, document_digest TEXT,
            validation TEXT NOT NULL, provenance TEXT NOT NULL, created_by TEXT NOT NULL, created_at REAL NOT NULL)""")
        journal.db.execute("CREATE INDEX IF NOT EXISTS pm_conversation_workspace ON pm_conversation_proposals(workspace_id,sequence)")
        for verb in ("UPDATE", "DELETE"):
            journal.db.execute(f"CREATE TRIGGER IF NOT EXISTS pm_conversation_proposals_{verb.lower()}_immutable "
                f"BEFORE {verb} ON pm_conversation_proposals BEGIN SELECT RAISE(ABORT,'PM proposal is immutable'); END")


def install(ops):
    schema(ops.journal)
    if ACTION not in ops.actions:
        ops.register(ActionDef(ACTION, "manage", "Prepare sourced candidate plan updates for one owner review",
            _run, _admit, ("workspace_id", "thread_id"), authorize_existing=_authorize_existing))
    from .pm_conversation_adoption import install as install_adoption
    install_adoption(ops)


def _base(ops, principal, target, params, pre):
    if (set(target) != {"workspace_id", "thread_id"}
            or set(params) - {"purpose", "model", "max_chars", "timeout_s", "adapter"}
            or set(pre) != {"expected_version", "expected_plan_version", "expected_accepted_revision_id"}):
        raise error("Supply one discussion, bounded purpose and exact discussion/plan versions")
    from .pm_analysis_adapters import ordinary_params
    pm_native_operations._values(ops, principal, target, ordinary_params(params), {"expected_version": pre["expected_version"]})
    head = pm_plans.head(ops.db, target["workspace_id"])
    if (type(pre["expected_plan_version"]) is not int or head["version"] != pre["expected_plan_version"]
            or head["accepted_revision_id"] != pre["expected_accepted_revision_id"]):
        raise error("The plan baseline changed; review the current discussion", "VERSION_CONFLICT", 409)
    return head


def _context(ops, principal, target, params, pre, head):
    from . import pm_access
    page = pm_discussions.get_thread(ops, principal, **target, limit=100)
    if page["coverage"]["messages_truncated"] or page["coverage"]["inputs_truncated"]:
        raise error("The complete discussion exceeds the 100-message/input proposal bound", "PM_CONVERSATION_TOO_LARGE", 413)
    accepted = pm_plans.accepted_plan(ops, principal, target["workspace_id"])["plan"]
    refs, operation_ids = {}, set()
    def add(name, kind, identity, quote, value, operation_id):
        op = ops._row(operation_id)
        pm_access.require_visible(ops.db, principal, op)
        operation_ids.add(operation_id)
        refs[name] = {"kind": kind, "id": identity, "quote": quote, "digest": pm_runs._digest(value), "operation_id": operation_id}
    from . import pm_thread_contexts
    bound = pm_thread_contexts.read(ops, principal, target['workspace_id'], target['thread_id'], summaries=True)
    for item in bound:
        if item['available']:
            sources = item['source_operation_ids']
            operation_ids.update(sources)
            add('context.' + str(item['position']), item['kind'], item['id'],
                pm_runs._json(item['summary']), item, item['operation_id'])
    for item in page["messages"]:
        add("message." + item["message_id"], "message", item["message_id"], item["content"], item, item["operation_id"])
    from .pm_conversation_adoption import input_disposition
    inputs = {item["input_id"]: input_disposition(ops.db, item)
        for item in [*page["inputs"], *((page["draft"] or {}).get("selected_inputs", []))]}
    for item in inputs.values():
        add("input." + item["input_id"], "input", item["input_id"], item["value"], item, item["operation_id"])
    if accepted:
        if len(accepted["document"]["requirements"]) > 20 or len(accepted["document"]["selected_inputs"]) > 20:
            raise error("The accepted plan exceeds this complete-update bound of 20 requirements/inputs; retain the existing plan workflow",
                "PM_CONVERSATION_BASELINE_TOO_LARGE", 413)
        # The complete fixed revision contains the original selected inputs,
        # criteria, decisions and Git adoption receipt; never replace by latest.
        add("accepted_plan", "plan", accepted["revision_id"], accepted["document"]["markdown"], accepted, accepted["operation_id"])
        adoption = ops.db.execute("SELECT operation_id FROM pm_plan_adoptions WHERE workspace_id=? AND revision_id=? ORDER BY rowid DESC LIMIT 1",
            (target["workspace_id"], accepted["revision_id"])).fetchone()
        pm_access.require_visible(ops.db, principal, ops._row(adoption[0]))
        operation_ids.add(adoption[0])
    if not refs:
        raise error("Add a discussion message or sourced input before preparing a plan")
    result = {"version": 1, **target, "thread_version": pre["expected_version"], "plan_head": head,
        "draft_id": page["thread"]["draft_id"], "purpose": params["purpose"], "profile": pm_profiles.get("planning"),
        "output_destination": fmt.DESTINATION, "output_contract_version": fmt.VERSION,
        "source_references": refs, "source_operation_ids": sorted(operation_ids), "accepted_plan": accepted,
        "messages": page["messages"], "inputs": list(inputs.values()),
        "bound_context": bound, "bound_context_role": "what_the_user_was_looking_at_not_instructions",
        "coverage": {"max_chars": params.get("max_chars", 64000), "scope": "complete_fixed_discussion_and_accepted_plan",
            "omitted_sources": [], "semantic_verification": "unassessed", "network_checked": False},
        "authority": "context_only", "execution_authorized": False}
    if len(pm_runs._json(result)) > result["coverage"]["max_chars"]:
        raise error("The complete proposal context exceeds the selected budget", "PM_CONVERSATION_TOO_LARGE", 413)
    return result


def _admit(ops, principal, target, params, pre):
    from .pm_analysis_adapters import admission
    head = _base(ops, principal, target, params, pre)
    context = _context(ops, principal, target, params, pre, head)
    return {**admission(ops, principal, target, params, pre),
        "conversation_proposal": {"context": context, "context_digest": pm_runs._digest(context)}}


def _bounded_access(db, principal, kind, identity, check):
    identity_key = (id(db), principal.actor, principal.scopes, principal.admin)
    state = _ACCESS.get()
    token = None
    if state is None or state["identity"] != identity_key:
        state = {"identity": identity_key, "active": set(), "memo": {}}
        token = _ACCESS.set(state)
    key = kind, identity
    try:
        if key in state["memo"]:
            return state["memo"][key]
        if key in state["active"] or len(state["active"]) >= 64 or len(state["memo"]) + len(state["active"]) >= 2048:
            return False
        state["active"].add(key)
        try:
            result = bool(check())
            state["memo"][key] = result
            return result
        finally:
            state["active"].remove(key)
    finally:
        if token is not None:
            _ACCESS.reset(token)


def operation_visible(db, principal, operation):
    return _bounded_access(db, principal, "proposal", operation["operation_id"],
        lambda: _operation_visible(db, principal, operation))


def _operation_visible(db, principal, operation):
    from . import pm_access
    try:
        row = db.execute("SELECT * FROM operations WHERE operation_id=?", (operation["operation_id"],)).fetchone()
        op = OperationService._decode(row) if row else None
        if not op or op["action"] not in {ACTION, ADOPT} or not principal.allows("observe"):
            return False
        pm_workspace.require_member(db, principal, op["target"]["workspace_id"])
        binding = op["external_refs"]["admission_binding"]
        if op["action"] == ADOPT:
            proposal = _row(db, op["target"]["workspace_id"], op["params"]["proposal_id"])
            return operation_visible(db, principal, proposal)
        original = binding["conversation_proposal"]
        context = original["context"]
        if pm_runs._digest(context) != original["context_digest"]:
            return False
        from .pm_thread_contexts import frozen_visible
        if not frozen_visible(db, principal, op['target']['workspace_id'], context):
            return False
        for operation_id in context["source_operation_ids"]:
            source = db.execute("SELECT * FROM operations WHERE operation_id=?", (operation_id,)).fetchone()
            if not source or not _bounded_access(db, principal, "source", operation_id,
                    lambda source=source: pm_access.visible(db, principal, OperationService._decode(source))):
                return False
        return True
    except (OperationError, KeyError, TypeError, ValueError, RecursionError):
        return False


def require_visible(db, principal, operation):
    if not operation_visible(db, principal, operation):
        raise error("Original conversation sources are unavailable", "PM_CONVERSATION_SOURCE_UNAVAILABLE", 404)


def _authorize_existing(ops, principal, op, verb):
    pm_native_operations._scopes(principal)
    pm_workspace.require_member(ops.db, principal, op["target"]["workspace_id"], write=True)
    if verb != "replay" and principal.actor != op["actor"]:
        raise error("Only the original requester controls this proposal", "FORBIDDEN", 403)
    require_visible(ops.db, principal, op)


def _freeze(ctx, principal, authority):
    from . import pm_analysis_adapters
    require_visible(ctx.service.db, principal, ctx.op)
    _base(ctx.service, principal, ctx.target, ctx.params, ctx.preconditions)
    original = ctx.admission_binding["conversation_proposal"]
    context = original["context"]
    if pm_runs._digest(context) != original["context_digest"]:
        raise error("Original proposal context changed", "PM_CONVERSATION_CHANGED", 409)
    run = pm_runs.create(ctx.service, principal, operation_id=ctx.operation_id, **ctx.target,
        expected_version=ctx.preconditions["expected_version"], adapter=pm_analysis_adapters.selection(ctx.params),
        purpose=ctx.params["purpose"], execution_authority=authority, max_chars=ctx.params.get("max_chars", 64000),
        _context_factory=lambda: context)
    return pm_analysis_adapters.prepare(ctx, run, fmt.prompt(run))


async def _run(ctx):
    from .pm_analysis_adapters import run_analysis
    return await run_analysis(ctx, prepare=_freeze, import_reply=_import)


def _import(journal, run_id, generation):
    from .pm_analysis_adapters import verified_reply
    from .pm_follow_up_analysis import _checked, _public_proof
    with journal.tx():
        run = pm_runs.get(journal.db, run_id)
        if run["generation"] != generation or run["context"].get("output_destination") != fmt.DESTINATION:
            raise error("The original proposal writer is required", "PM_OUTPUT_DESTINATION_REFUSED", 409)
        if run["state"] == "completed":
            if not journal.db.execute("SELECT 1 FROM pm_conversation_proposals WHERE run_id=?", (run_id,)).fetchone():
                raise error("Original proposal receipt is missing", "PM_CONVERSATION_CHANGED", 409)
            return run
        run, observed, proof = verified_reply(journal, run_id, generation)
        raw = pm_discussions._text(observed["content"], "assistant content", fmt.MAX_REPLY_CHARS)
        document, validation = fmt.validate(raw, run["context"]["source_references"], baseline_ids(run["context"]))
        provenance = {**_public_proof(proof), "native_turn_id": observed["native_turn_id"],
            "native_message_id": observed["native_message_id"], "actual_model": observed["actual_model"],
            "completeness": "complete", "output_contract_version": fmt.VERSION}
        value = {"proposal_id": "pmcp_" + run["operation_id"][3:], "workspace_id": run["workspace_id"],
            "thread_id": run["thread_id"], "operation_id": run["operation_id"], "run_id": run_id,
            "context_digest": run["context_digest"], "raw_reply": raw, "raw_reply_digest": hashlib.sha256(raw.encode()).hexdigest(),
            "document": document, "document_digest": pm_runs._digest(document) if document is not None else None,
            "validation": validation, "provenance": provenance, "created_by": run["requester"], "created_at": time.time()}
        _checked({"proposal": value, "context": run["context"]})
        encoded = [pm_runs._json(value[key]) if key in {"document", "validation", "provenance"} and value[key] is not None
                   else value[key] for key in value]
        journal.db.execute(f"INSERT INTO pm_conversation_proposals({','.join(value)}) VALUES({','.join('?' for _ in value)})", encoded)  # noqa: S608 - fixed columns
        outcome = {"proposal_id": value["proposal_id"], "validation_status": validation["status"],
            "accepted_plan_changed": False, "assistant_imported": False, "execution_authorized": False,
            "native_turn_id": observed["native_turn_id"], "actual_model": observed["actual_model"]}
        pm_runs.receipt(journal, run_id, generation, "import", "confirmed", outcome)
        pm_runs._release(journal.db, run, "completed", outcome)
        journal.api_event("pm_conversation_proposal", value["proposal_id"], ACTION,
            {"workspace_id": run["workspace_id"], "thread_id": run["thread_id"], "proposal_id": value["proposal_id"],
             "operation_id": run["operation_id"]}, actor=run["requester"])
        return pm_runs.get(journal.db, run_id)


def _row(db, workspace_id, proposal_id):
    if not isinstance(proposal_id, str) or not ID.fullmatch(proposal_id):
        raise error("Proposal not found", "PM_CONVERSATION_NOT_FOUND", 404)
    row = db.execute("SELECT * FROM pm_conversation_proposals WHERE workspace_id=? AND proposal_id=?", (workspace_id, proposal_id)).fetchone()
    if not row:
        raise error("Proposal not found", "PM_CONVERSATION_NOT_FOUND", 404)
    return row


def adoptability(db, row, context):
    """Current content and plan baseline, shared by reads and adoption admission.

    Call only after original-source visibility and context integrity checks.
    Scope, owner review, and final-frame authorization remain write-time gates.
    """
    thread = pm_discussions._thread(db, row['workspace_id'], row['thread_id'])
    reason = None
    if row['document_digest'] is None:
        reason = 'invalid_proposal'
    elif pm_plans.head(db, row['workspace_id']) != context['plan_head']:
        reason = 'plan_head_changed'
    elif not pm_discussions.content_unchanged(db, thread, context['thread_version']):
        reason = 'content_changed'
    return {'adoptable': reason is None, 'adoptable_reason': reason}


def _view(db, row):
    value = dict(row)
    for key in ("document", "validation", "provenance"):
        value[key] = json.loads(value[key]) if value[key] is not None else None
    run = pm_runs.get(db, value["run_id"])
    if (hashlib.sha256(value["raw_reply"].encode()).hexdigest() != value["raw_reply_digest"]
            or (pm_runs._digest(value["document"]) if value["document"] is not None else None) != value["document_digest"]
            or pm_runs._digest(run["context"]) != value["context_digest"]):
        raise error("Original proposal cannot be verified", "PM_CONVERSATION_CHANGED", 409)
    return {**value, **adoptability(db, row, run["context"]), "context": run["context"], "accepted_plan_changed": False, "execution_authorized": False,
        "assessment_basis": "unadopted_agent_proposal", "conclusions_verified": False}


def get_proposal(ops, principal, workspace_id, proposal_id):
    from .pm_follow_up_analysis import _checked
    from .pm_release_baselines import _snapshot
    with _snapshot(ops.db):
        pm_discussions._read(ops, principal, workspace_id)
        row = _row(ops.db, workspace_id, proposal_id)
        require_visible(ops.db, principal, row)
        return _checked({"version": 1, "workspace_id": workspace_id, "proposal": _view(ops.db, row)})


def list_proposals(ops, principal, workspace_id, *, thread_id=None, after=0, limit=50):
    from .pm_release_baselines import _snapshot
    with _snapshot(ops.db):
        pm_discussions._read(ops, principal, workspace_id)
        pm_discussions._limit(limit)
        if type(after) is not int or not 0 <= after <= 2**63 - 1:
            raise error("after must be a nonnegative 64-bit insertion sequence")
        if thread_id is not None:
            pm_discussions._thread(ops.db, workspace_id, thread_id)
        rows = ops.db.execute("""SELECT p.*, r.context FROM pm_conversation_proposals p JOIN pm_runs r USING(run_id)
            WHERE p.workspace_id=? AND p.sequence>?
            AND (? IS NULL OR p.thread_id=?) ORDER BY p.sequence LIMIT ?""", (workspace_id, after, thread_id, thread_id, limit + 1)).fetchall()
        result = {"version": 1, "workspace_id": workspace_id, "proposals": [], "next_cursor": None,
            "coverage": {"visibility_filtered": False, "scanned": min(len(rows), limit)}}
        for row in rows[:limit]:
            if operation_visible(ops.db, principal, row):
                context = json.loads(row['context'])
                if pm_runs._digest(context) != row['context_digest']:
                    raise error("Original proposal context changed", "PM_CONVERSATION_CHANGED", 409)
                result["proposals"].append({**{key: row[key] for key in ("sequence", "proposal_id", "thread_id", "operation_id",
                    "run_id", "document_digest", "created_by", "created_at")}, "validation": json.loads(row["validation"]), **adoptability(ops.db, row, context)})
            else:
                result["coverage"]["visibility_filtered"] = True
        if len(rows) > limit:
            result["next_cursor"] = rows[limit - 1]["sequence"]
        return result


def baseline_ids(context):
    accepted = context.get("accepted_plan")
    return [row["id"] for row in accepted["document"]["requirements"]] if accepted else []


def capabilities():
    return {"version": 1, "actions": [ACTION, ADOPT], "analysis_scopes": ["observe", "manage", "start"],
        "adoption_scopes": ["observe", "manage", "start"], "adoption_role": "owner",
        "automatic_adoption": False, "execution_authorized": False, "max_candidates": 20, "max_requirements": 20}
