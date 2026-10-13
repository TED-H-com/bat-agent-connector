"""One owner decision composes the existing discussion, plan and Git actions."""
from __future__ import annotations

import json
import time

from . import (
    api_auth,
    controller_authority,
    pm_discussions,
    pm_native_operations,
    pm_plans,
    pm_profiles,
    pm_runs,
    pm_workspace,
)
from . import pm_conversation_format as fmt
from . import pm_conversation_proposals as proposals
from .api_auth import Principal
from .operations import ActionDef, NeedsAttention, OperationService, Wait

LINK = "pm_conversation_parent"
TERMINAL = {"succeeded", "failed", "cancelled", "needs_attention"}


def install(ops):
    with ops.journal.tx():
        ops.db.execute("""CREATE TABLE IF NOT EXISTS pm_conversation_children (
            parent_id TEXT NOT NULL, ordinal INTEGER NOT NULL, operation_id TEXT NOT NULL UNIQUE,
            request TEXT NOT NULL, request_digest TEXT NOT NULL, created_at REAL NOT NULL,
            PRIMARY KEY(parent_id,ordinal))""")
        for verb in ("UPDATE", "DELETE"):
            ops.db.execute(f"CREATE TRIGGER IF NOT EXISTS pm_conversation_children_{verb.lower()}_immutable "
                f"BEFORE {verb} ON pm_conversation_children BEGIN SELECT RAISE(ABORT,'Proposal child is immutable'); END")
    if proposals.ADOPT not in ops.actions:
        ops.register(ActionDef(proposals.ADOPT, "manage", "Adopt one complete owner-reviewed conversation update",
            _run, _admit, ("workspace_id", "thread_id"), authorize_existing=_authorize_existing))


def _admit(ops, principal, target, params, pre):
    pm_native_operations._scopes(principal)
    pm_workspace.require_member(ops.db, principal, target.get("workspace_id"), decide=True)
    if (set(target) != {"workspace_id", "thread_id"} or set(params) != {"proposal_id", "review", "rationale"}
            or set(pre) != {"expected_version", "expected_plan_version", "expected_accepted_revision_id", "expected_proposal_digest"}):
        raise proposals.error("Supply one exact proposal, whole review, rationale and original versions")
    proposal = proposals.get_proposal(ops, principal, target["workspace_id"], params["proposal_id"])["proposal"]
    if (proposal["thread_id"] != target["thread_id"] or proposal["document"] is None
            or proposal["document_digest"] != pre["expected_proposal_digest"]):
        raise proposals.error("Only the fixed valid proposal can be reviewed", "PM_CONVERSATION_CHANGED", 409)
    context = proposal["context"]
    thread = pm_discussions._thread(ops.db, **target)
    head = pm_plans.head(ops.db, target["workspace_id"])
    if (type(pre["expected_version"]) is not int or type(pre["expected_plan_version"]) is not int
            or pre["expected_version"] not in {thread["version"], context["thread_version"]}
            or not proposal["adoptable"]
            or head["version"] != pre["expected_plan_version"]
            or head["accepted_revision_id"] != pre["expected_accepted_revision_id"]):
        raise proposals.error("The reviewed discussion or plan baseline changed", "VERSION_CONFLICT", 409)
    pm_discussions._text(params["rationale"], "rationale", 4000)
    try:
        fmt.review(params["review"], proposal["document"], proposals.baseline_ids(context))
    except (ValueError, TypeError, RecursionError) as exc:
        raise proposals.error(str(exc)[:300]) from None
    # Fail before recording any owner inputs if managed Git adoption cannot run.
    from . import pm_plan_operations, pm_revision_git
    git_binding_id = pm_revision_git.binding_id(pm_plan_operations._binding(ops, target["workspace_id"]))
    return {"principal": pm_workspace.principal_binding(principal), "pm_lineage": {"workspace_id": target["workspace_id"]},
        "pm_source_roots": [proposal["operation_id"]], "conversation_review": {"proposal_operation_id": proposal["operation_id"], "proposal_digest": proposal["document_digest"],
            "review_digest": pm_runs._digest(params["review"]), "context_digest": proposal["context_digest"], "git_binding_id": git_binding_id}}


def _authorize_existing(ops, principal, op, verb):
    pm_native_operations._scopes(principal)
    pm_workspace.require_member(ops.db, principal, op["target"]["workspace_id"], decide=True)
    if verb != "replay" and principal.actor != op["actor"]:
        raise proposals.error("Only the original owner controls this review", "FORBIDDEN", 403)
    proposals.require_visible(ops.db, principal, op)


def _operation(db, identity):
    row = db.execute("SELECT * FROM operations WHERE operation_id=?", (identity,)).fetchone()
    if not row:
        raise proposals.error("Original review operation is missing", "PM_CONVERSATION_CHANGED", 409)
    return OperationService._decode(row)


def _children(db, parent):
    rows = db.execute("SELECT * FROM pm_conversation_children WHERE parent_id=? ORDER BY ordinal", (parent["operation_id"],)).fetchall()
    if len(rows) > 23:
        raise proposals.error("Review child bound exceeded", "PM_CONVERSATION_CHANGED", 409)
    result = []
    for index, row in enumerate(rows):
        request = json.loads(row["request"])
        op = _operation(db, row["operation_id"])
        if (row["ordinal"] != index or pm_runs._digest(request) != row["request_digest"]
                or any(op.get(key) != value for key, value in request.items())
                or op["actor"] != parent["actor"] or op["external_refs"].get(LINK) != parent["operation_id"]):
            raise proposals.error("Original review child relation changed", "PM_CONVERSATION_CHANGED", 409)
        result.append(op)
    return result


def _current_principal(ops, parent):
    binding = parent["external_refs"]["admission_binding"]
    saved = binding["principal"]
    original = Principal(saved["actor"], frozenset(saved["scopes"]), admin=saved["admin"])
    pm_native_operations._scopes(original)
    required, now = {"observe", "manage", "start"}, time.time()
    rows = ops.db.execute("""SELECT token_hash,scopes FROM api_principals WHERE actor=? AND revoked_at IS NULL
        AND created_at<=? AND (expires_at IS NULL OR expires_at>?) ORDER BY created_at DESC LIMIT 64""",
        (original.actor, now, now)).fetchall()
    live = None
    for row in rows:
        try:
            scopes = json.loads(row["scopes"])
            if isinstance(scopes, list) and required.issubset(scopes):
                live = Principal(original.actor, frozenset(required), credential_id=row["token_hash"])
                break
        except (ValueError, TypeError):
            continue
    if live is None and original.admin:
        daemon = ops.context.get("daemon")
        token = vars(daemon).get("_admin_token") if daemon is not None else None
        if isinstance(token, str) and token:
            candidate = api_auth.authenticate(ops.db, token, token)
            if candidate and candidate.actor == original.actor:
                live = candidate
    if live is None:
        raise proposals.error("No current credential permits this original owner decision", "PM_CONVERSATION_AUTHORITY_UNAVAILABLE", 403)
    pm_workspace.require_member(ops.db, live, parent["target"]["workspace_id"], decide=True)
    authority = controller_authority.binding(ops.db, live)
    if any(authority[key] != binding["controller_authority"][key] for key in ("server_id", "controller_id", "actor")):
        raise proposals.error("Original review controller changed", "PM_CONVERSATION_AUTHORITY_UNAVAILABLE", 403)
    return live


def _guard(ops, parent):
    db = ops.db
    if parent["action"] != proposals.ADOPT or parent["cancel_requested"] or parent["status"] in TERMINAL:
        raise proposals.error("The original owner review stopped new effects", "PM_CONVERSATION_STOPPED", 409)
    principal = _current_principal(ops, parent)
    proposals.require_visible(db, principal, parent)
    from .pm_lifecycle_gates import require_local
    require_local(db, parent["operation_id"])
    proposal = proposals._view(db, proposals._row(db, parent["target"]["workspace_id"], parent["params"]["proposal_id"]))
    frozen = parent["external_refs"]["admission_binding"]["conversation_review"]
    from . import pm_revision_git
    if frozen != {"proposal_operation_id": proposal["operation_id"], "proposal_digest": proposal["document_digest"],
            "review_digest": pm_runs._digest(parent["params"]["review"]), "context_digest": proposal["context_digest"],
            "git_binding_id": pm_revision_git.binding_id(pm_runs.workspace_worktree(db, parent["target"]["workspace_id"]))}:
        raise proposals.error("Original owner decision changed", "PM_CONVERSATION_CHANGED", 409)
    children = _children(db, parent)
    context = proposal["context"]
    thread = pm_discussions._thread(db, **parent["target"])
    expected_draft = context["draft_id"]
    applied_children = []
    expected_head = dict(context["plan_head"])
    for child in children:
        applied = db.execute("SELECT result FROM pm_discussion_applied WHERE operation_id=?", (child["operation_id"],)).fetchone()
        if applied:
            result = json.loads(applied[0])
            applied_children.append(child["operation_id"])
            expected_draft = result.get("draft_id", expected_draft)
        draft = db.execute("SELECT result FROM pm_plan_applied WHERE operation_id=?", (child["operation_id"],)).fetchone()
        if draft:
            expected_head["version"] += 1
            expected_head["latest_revision_id"] = json.loads(draft[0])["revision_id"]
    if (not pm_discussions.content_unchanged(db, thread, context["thread_version"], allowed_operations=applied_children)
            or thread["draft_id"] != expected_draft
            or pm_plans.head(db, parent["target"]["workspace_id"]) != expected_head):
        raise proposals.error("Discussion or plan changed during the reviewed update", "VERSION_CONFLICT", 409)
    return principal, proposal, children


def require_child(db, operation_id):
    """Local mutations and final host frames retain the whole owner decision."""
    if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='pm_conversation_children'").fetchone():
        return
    row = db.execute("SELECT * FROM pm_conversation_children WHERE operation_id=?", (operation_id,)).fetchone()
    if not row:
        return
    parent = _operation(db, row["parent_id"])
    from .pm_source_lineage import _EXECUTION
    current = _EXECUTION.get()
    if current is None or current[0].db is not db:
        raise proposals.error("A private child requires its original operation worker", "PM_CONVERSATION_AUTHORITY_UNAVAILABLE", 403)
    _, _, children = _guard(current[0], parent)
    if not any(child["operation_id"] == operation_id for child in children):
        raise proposals.error("Original review child is unavailable", "PM_CONVERSATION_CHANGED", 409)


def _request(parent, proposal, children, *, base_version=None):
    reviewed = parent["params"]["review"]
    originals = proposal["document"]["candidates"]
    rows = {row["key"]: row for row in reviewed["rows"]}
    ordinal, count = len(children), len(originals)
    context = proposal["context"]
    version = context["thread_version"] if base_version is None else base_version
    common = {"target": parent["target"], "idempotency_key": "pm-conversation:" + parent["operation_id"] + ":" + str(ordinal)}
    if ordinal < count:
        original = originals[ordinal]
        row = rows[original["key"]]
        return {**common, "action": "pm.input.record", "params": {"slot": original["domain"] + "." + row["key"],
            "value": row["text"], "status": row["status"],
            "source_ref": {"kind": "statement", "ref": "Owner review " + parent["operation_id"] +
                "; original proposal " + proposal["proposal_id"] + "; candidate " + row["key"]}},
            "preconditions": {"expected_version": version + ordinal}}
    inputs = {item["key"]: children[index]["result"]["input_id"] for index, item in enumerate(originals)}
    selected = [inputs[item["key"]] for item in originals if rows[item["key"]]["include"]]
    if ordinal == count:
        previous = context["draft_id"]
        return {**common, "action": "pm.thread.plan.revise" if previous else "pm.thread.plan",
            "params": {"title": reviewed["title"], "rationale": parent["params"]["rationale"], "selected_input_ids": selected},
            "preconditions": {"expected_version": version + count, **({"expected_draft_id": previous} if previous else {})}}
    if ordinal == count + 1:
        return {**common, "action": "pm.plan.draft", "params": {"title": reviewed["title"],
            "previous_revision_id": context["plan_head"]["latest_revision_id"],
            "sections": {domain: [inputs[item["key"]] for item in originals
                if item["domain"] == domain and rows[item["key"]]["include"]] for domain in pm_profiles.DOMAINS},
            "requirements": [{"id": item["id"], "source_input_id": inputs[item["source_key"]],
                "acceptance_input_ids": [inputs[key] for key in item["acceptance_keys"]], "priority": item["priority"],
                "depends_on": item["depends_on"]} for item in reviewed["requirements"]]},
            "preconditions": {"expected_version": version + count + 1, "expected_plan_version": context["plan_head"]["version"]}}
    if ordinal == count + 2:
        return {**common, "action": "pm.plan.adopt",
            "target": {"workspace_id": parent["target"]["workspace_id"], "revision_id": children[-1]["result"]["revision_id"]},
            "params": {"rationale": parent["params"]["rationale"]},
            "preconditions": {"expected_accepted_revision_id": context["plan_head"]["accepted_revision_id"]}}
    return None


def _create(ctx):
    def create():
        parent = ctx.service._row(ctx.operation_id)
        principal, proposal, children = _guard(ctx.service, parent)
        thread = pm_discussions._thread(ctx.service.db, **parent['target'])
        applied = sum(ctx.service.db.execute('SELECT 1 FROM pm_discussion_applied WHERE operation_id=?',
            (child['operation_id'],)).fetchone() is not None for child in children)
        request = _request(parent, proposal, children, base_version=thread['version'] - applied)
        child, fresh = ctx.service.create(principal, **request, entry="pm-conversation")
        if not fresh:
            raise proposals.error("Child key exists without original relation", "PM_CONVERSATION_CHANGED", 409)
        ctx.service.db.execute("INSERT INTO pm_conversation_children VALUES(?,?,?,?,?,?)", (ctx.operation_id, len(children),
            child["operation_id"], pm_runs._json(request), pm_runs._digest(request), time.time()))
        ctx.service._merge_refs(child["operation_id"], {LINK: ctx.operation_id})
        ctx.service.db.execute("INSERT OR IGNORE INTO pm_source_lineage VALUES(?,?,?,?)",
            ("operation", child["operation_id"], proposal["operation_id"], ctx.operation_id))
        return {"operation_id": child["operation_id"]}
    ordinal = len(_children(ctx.service.db, ctx.op))
    return ctx.effect("pm.conversation.child." + str(ordinal), create)


async def _run(ctx):
    children = _children(ctx.service.db, ctx.op)
    if children:
        last = children[-1]
        # Original accepted receipt recovery precedes all new-effect gates.
        if last["action"] == "pm.plan.adopt":
            saved = ctx.service.db.execute("SELECT result FROM pm_plan_adoptions WHERE operation_id=?", (last["operation_id"],)).fetchone()
            if saved:
                return {**json.loads(saved[0]), "proposal_id": ctx.params["proposal_id"],
                    "review_digest": ctx.admission_binding["conversation_review"]["review_digest"],
                    "adoption_operation_id": last["operation_id"], "child_operation_ids": [child["operation_id"] for child in children]}
        if last["status"] in TERMINAL - {"succeeded"}:
            raise NeedsAttention("PM_CONVERSATION_CHILD_STOPPED", "Original " + last["action"] + " ended as " + last["status"])
        if last["status"] != "succeeded":
            raise Wait("waiting_external", "Waiting for the original reviewed update child", 1)
    # A committed receipt is never replayed by manufacturing another child key.
    _guard(ctx.service, ctx.service._row(ctx.operation_id))
    _create(ctx)
    raise Wait("waiting_external", "Original reviewed update child admitted", 1)


def input_disposition(db, value):
    """Keep exclusion and edited-owner attribution typed across later discussions."""
    if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='pm_conversation_children'").fetchone():
        return value
    row = db.execute("SELECT parent_id,ordinal FROM pm_conversation_children WHERE operation_id=?", (value["operation_id"],)).fetchone()
    if not row:
        return value
    parent = _operation(db, row["parent_id"])
    proposal = proposals._view(db, proposals._row(db, parent["target"]["workspace_id"], parent["params"]["proposal_id"]))
    original = proposal["document"]["candidates"][row["ordinal"]]
    reviewed = next(item for item in parent["params"]["review"]["rows"] if item["key"] == original["key"])
    return {**value, "review_disposition": {"included": reviewed["include"], "original_kind": original["kind"],
        "candidate_key": original["key"], "proposal_id": proposal["proposal_id"], "operation_id": parent["operation_id"],
        "reviewed_by": parent["actor"], "rationale": parent["params"]["rationale"], "source_refs": original["source_refs"]}}
