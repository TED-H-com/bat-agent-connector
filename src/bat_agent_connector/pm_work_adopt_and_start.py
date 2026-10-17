"""One fixed owner decision composes adoption and ordinary Work starts.

The parent owns only its durable child relations. Plan adoption, attempts,
packages, BAT writes and reconciliation remain in their existing operations.
"""
from __future__ import annotations

import copy
import json
import time
from contextvars import ContextVar

from . import (
    controller_authority,
    pm_evidence,
    pm_lifecycle_gates,
    pm_plans,
    pm_runs,
    pm_source_lineage,
    pm_work_packages,
    pm_workspace,
    repository_sync,
    work_items,
)
from . import (
    pm_conversation_adoption as adoption,
)
from . import (
    pm_conversation_proposals as proposals,
)
from . import (
    pm_work_start as start,
)
from .operations import ActionDef, NeedsAttention, OperationError, Wait

ACTION = "work.adopt_and_start"
PREVIEW = "pm_work_adopt_and_start_preview"
LINK = "work_adopt_and_start_parent"
MAX_SELECTED_WORKS = 10
ADOPT_PARAMS = frozenset({"proposal_id", "review", "rationale"})
ADOPT_PRE = frozenset({"expected_version", "expected_plan_version", "expected_accepted_revision_id", "expected_proposal_digest"})
WORK_PRE = start.PRECONDITIONS - {"expected_scope_revision"}
RUNNABLE = frozenset({"accepted", "running", "waiting_external", "waiting_checks", "uncertain", "needs_attention"})
UNRESUMABLE = start._UNRESUMABLE_PRE_CHILD | {
    "PM_ADOPTED_PLAN_REQUIRED", "PM_WORK_START_CONTROLLER_CHANGED",
    "PM_WORK_START_CONTEXT_INVALID", "PM_WORK_START_CHILD_CHANGED", "VERSION_CONFLICT",
    "PM_CONVERSATION_CHANGED",
}
PER_WORK_REFUSAL_CODES = frozenset({
    "WORK_ITEM_NOT_FOUND", "WORK_ITEM_ARCHIVED", "WORK_ITEM_CHANGED",
    "PROJECT_NOT_FOUND", "PROJECT_ARCHIVED", "PROJECT_CHANGED", "PM_PROJECT_BINDING_REQUIRED",
    "PM_WORK_BINDING_CHANGED", "PM_PACKAGE_VERSION_CONFLICT", "PM_PACKAGE_HISTORY_LIMIT",
    "PM_WORK_ATTEMPT_CHANGED", "PM_WORK_START_IN_PROGRESS", "PM_WORK_START_OUTCOME_UNPROVEN",
    "PM_WORK_CONTROLLER_CONFLICT", "PROJECT_REPOSITORY_CHANGED", "REPOSITORY_NOT_BOUND",
    "REPOSITORY_HOST_UNAVAILABLE", "REPOSITORY_BINDING_CHANGED", "PM_WORK_START_PREREQUISITES",
    "PM_WORK_START_NOT_READY", "PM_HANDOFF_TOO_LARGE",
})
_STEERING = ContextVar("work_adopt_and_start_steering", default=None)


def _bad(message, code="INVALID_PARAMS", status=422):
    return OperationError(code, message, status)


def installed(db):
    return db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='pm_work_adopt_and_start_children'").fetchone() is not None


def install(ops):
    with ops.journal.tx():
        ops.db.execute("""CREATE TABLE IF NOT EXISTS pm_work_adopt_and_start_children (
            parent_id TEXT NOT NULL, slot TEXT NOT NULL, ordinal INTEGER NOT NULL,
            operation_id TEXT NOT NULL UNIQUE, request TEXT NOT NULL,
            request_digest TEXT NOT NULL, created_at REAL NOT NULL,
            PRIMARY KEY(parent_id,slot), UNIQUE(parent_id,ordinal))""")
        for verb in ("UPDATE", "DELETE"):
            ops.db.execute(f"CREATE TRIGGER IF NOT EXISTS pm_work_adopt_and_start_children_{verb.lower()}_immutable "
                f"BEFORE {verb} ON pm_work_adopt_and_start_children "
                "BEGIN SELECT RAISE(ABORT,'Adopt-and-start child is immutable'); END")
        ops.db.execute("""CREATE TRIGGER IF NOT EXISTS pm_work_adopt_and_start_child_settled
            AFTER UPDATE OF status ON operations
            WHEN NEW.status != OLD.status AND NEW.status IN ('succeeded','failed','cancelled','needs_attention','uncertain')
            BEGIN UPDATE operations SET next_run_at=0
                WHERE action='work.adopt_and_start' AND status='waiting_external'
                AND operation_id=(SELECT parent_id FROM pm_work_adopt_and_start_children
                    WHERE operation_id=NEW.operation_id);
            END""")
    if ACTION not in ops.actions:
        ops.register(ActionDef(ACTION, "start", "Adopt the reviewed plan and start the selected existing Works",
            _run, _admit, ("workspace_id", "thread_id"), authorize_existing=_authorize_existing))


def _selection(works, review, *, preview=False):
    if not isinstance(works, list) or not 1 <= len(works) <= MAX_SELECTED_WORKS:
        raise _bad("Select one to ten existing Works")
    known = {item["id"] for item in review["requirements"]}
    seen = set()
    for work in works:
        required = {"work_item_id", "requirement_ids"}
        if not preview:
            required |= start.PARAMS_REQUIRED
        allowed = {"work_item_id", "requirement_ids", "model"} | start.PARAMS_REQUIRED
        if not isinstance(work, dict) or not required <= set(work) or set(work) - allowed:
            raise _bad("Supply exact selected Work parameters")
        wi = work["work_item_id"]
        if not isinstance(wi, str) or not work_items.WORK_ITEM_ID.fullmatch(wi):
            raise _bad("Select an existing Work ID", "INVALID_TARGET")
        if wi in seen:
            raise _bad("Each selected Work must appear once")
        seen.add(wi)
        ids = work["requirement_ids"]
        if (not isinstance(ids, list) or not 1 <= len(ids) <= 50
                or any(not isinstance(rid, str) or not pm_plans.ITEM_ID.fullmatch(rid) for rid in ids)
                or len(set(ids)) != len(ids) or not set(ids) <= known):
            raise _bad("Select distinct requirements from this reviewed proposal")


def _preview_plan(review):
    """Pure advisory projection; synthetic IDs never enter durable plan records."""
    rows = {row["key"]: row for row in review["rows"]}
    ids = {key: "pmi_" + pm_runs._digest(key)[:32] for key in rows}
    return {"document": {"requirements": [
        {"id": item["id"], "text": rows[item["source_key"]]["text"],
         "source_input_id": ids[item["source_key"]], "priority": item["priority"],
         "source_status": rows[item["source_key"]]["status"],
         "depends_on": item["depends_on"],
         "acceptance": [{"source_input_id": ids[key], "text": rows[key]["text"], "source_status": rows[key]["status"]}
                        for key in item["acceptance_keys"]]}
        for item in review["requirements"]]}}


def _advisory(ops, principal, wid, local, work, review):
    previous = local["latest_package_id"]
    if previous:
        record = pm_work_packages._record(ops.db, wid, previous)
        if pm_work_packages._access(ops, principal, record) >= pm_work_packages.MAX_ANCESTORS:
            raise _bad("The original Work package lineage cannot be extended", "PM_PACKAGE_HISTORY_LIMIT", 409)
    plan = _preview_plan(review)
    params, document = start.compose_thin_package(ops, local["work"], plan,
        work["requirement_ids"], local["code_source"], local["delivery"], local["instruction"], previous)
    package = {"mode": "compose", "package_ref": None, "description": params}
    checks = pm_work_packages.document_readiness_checks(document)
    selected = [item for item in plan["document"]["requirements"] if item["id"] in work["requirement_ids"]]
    start._require_ready(selected, package, {"checks": checks})
    start.check_handoff_size(ops, wid, local["instruction"], local["code_source"], package)
    return checks


def _admit(ops, principal, target, params, pre):
    if not isinstance(target, dict) or set(target) != {"workspace_id", "thread_id"}:
        raise _bad("Supply exact workspace_id and thread_id", "INVALID_TARGET")
    if not isinstance(params, dict) or set(params) != ADOPT_PARAMS | {"works"}:
        raise _bad("Supply the proposal, whole review, rationale and selected Works")
    if not isinstance(pre, dict) or set(pre) != ADOPT_PRE | {"works"}:
        raise _bad("Supply the original proposal and Work preconditions", "PRECONDITION_REQUIRED")
    wid = target["workspace_id"]
    with ops.journal.tx():
        workspace = start._member(ops, principal, wid, write=True, decide=True)
        adopted = adoption._admit(ops, principal, target,
            {key: params[key] for key in ADOPT_PARAMS}, {key: pre[key] for key in ADOPT_PRE})
        _selection(params["works"], params["review"])
        if not isinstance(pre["works"], list) or len(pre["works"]) != len(params["works"]):
            raise _bad("Retain one ordered set of preconditions per selected Work", "PRECONDITION_REQUIRED")
        controller = controller_authority.binding(ops.db, principal, pm_workspace_id=wid)
        works = []
        for work, conditions in zip(params["works"], pre["works"], strict=True):
            if (not isinstance(conditions, dict) or set(conditions) != WORK_PRE | {"work_item_id"}
                    or conditions["work_item_id"] != work["work_item_id"]):
                raise _bad("Work preconditions must retain the original selection order", "PRECONDITION_REQUIRED")
            wi = work["work_item_id"]
            pm_source_lineage.require(ops.db, principal, "work_item", wi)
            child_params = {key: value for key, value in work.items() if key != "work_item_id"}
            child_pre = {key: conditions[key] for key in WORK_PRE}
            local = start.check_work_preconditions(ops, principal, wid, wi, child_params, child_pre,
                controller=controller, pending_scope=True)
            _advisory(ops, principal, wid, local, work, params["review"])
            works.append({"work_item_id": wi, "project_id": local["work"]["project_id"],
                "params": copy.deepcopy(child_params), "preconditions": copy.deepcopy(child_pre)})
        intent = {"version": 1, "workspace_id": wid, "thread_id": target["thread_id"],
            "server_id": workspace["server_id"], "controller": controller,
            "execution_epoch": pm_lifecycle_gates.head(ops.db, wid)["execution_epoch"],
            "adopt_params": {key: copy.deepcopy(params[key]) for key in ADOPT_PARAMS},
            "adopt_preconditions": {key: pre[key] for key in ADOPT_PRE}, "works": works,
            "conversation_review": adopted["conversation_review"]}
    return {**adopted, "controller_authority": controller,
        "work_adopt_and_start": intent, "work_adopt_and_start_digest": pm_runs._digest(intent)}


def _intent(parent):
    saved = (parent.get("external_refs") or {}).get("admission_binding") or {}
    intent = saved.get("work_adopt_and_start")
    if not isinstance(intent, dict) or pm_runs._digest(intent) != saved.get("work_adopt_and_start_digest"):
        raise _bad("The original adopt-and-start decision changed", "PM_WORK_START_CONTEXT_INVALID", 409)
    if (parent["action"] != ACTION or parent["target"] != {"workspace_id": intent["workspace_id"], "thread_id": intent["thread_id"]}
            or {key: parent["params"][key] for key in ADOPT_PARAMS} != intent["adopt_params"]
            or {key: parent["preconditions"][key] for key in ADOPT_PRE} != intent["adopt_preconditions"]
            or parent["params"]["works"] != [{"work_item_id": row["work_item_id"], **row["params"]} for row in intent["works"]]
            or parent["preconditions"]["works"] != [{"work_item_id": row["work_item_id"], **row["preconditions"]} for row in intent["works"]]):
        raise _bad("The stored request no longer matches its admission", "PM_WORK_START_CONTEXT_INVALID", 409)
    return intent


def operation_visible(db, principal, operation):
    try:
        parent = adoption._operation(db, operation["operation_id"])
        intent = _intent(parent)
        if not principal.allows("observe"):
            return False
        workspace = pm_workspace.require_member(db, principal, intent["workspace_id"])
        if workspace["server_id"] != intent["server_id"]:
            return False
        if not proposals.operation_visible(db, principal,
                {"operation_id": intent["conversation_review"]["proposal_operation_id"]}):
            return False
        for work in intent["works"]:
            pm_source_lineage.require(db, principal, "work_item", work["work_item_id"])
        return True
    except (OperationError, KeyError, TypeError, ValueError):
        return False


source_visible = operation_visible


def _authorize_existing(ops, principal, parent, verb):
    if not operation_visible(ops.db, principal, parent):
        raise _bad("Original proposal or selected Work sources are unavailable", "NOT_FOUND", 404)
    start._member(ops, principal, parent["target"]["workspace_id"], write=True, decide=True)
    if principal.actor != parent["actor"]:
        raise _bad("Only the original owner controls this decision", "FORBIDDEN", 403)


def _adoption_receipt(ops, child):
    if child is None:
        return None
    # Validate every immutable conversation relation before using the inner
    # pm.plan.adopt receipt, including recovery before its parent status settles.
    children = adoption._children(ops.db, child)
    if not children or children[-1]["action"] != "pm.plan.adopt":
        return None
    leaf = children[-1]
    row = ops.db.execute("SELECT result FROM pm_plan_adoptions WHERE operation_id=?", (leaf["operation_id"],)).fetchone()
    if row is None:
        return None
    receipt = json.loads(row["result"])
    if receipt.get("workspace_id") != child["target"]["workspace_id"]:
        raise _bad("Adoption receipt has another workspace", "PM_WORK_START_CHILD_CHANGED", 409)
    return {**receipt, "adoption_operation_id": leaf["operation_id"]}


def _request(ops, parent, slot):
    intent = _intent(parent)
    common = {"idempotency_key": f"work-adopt-and-start:{parent['operation_id']}:{slot}"}
    if slot == "adopt":
        return {**common, "action": proposals.ADOPT, "target": parent["target"],
            "params": intent["adopt_params"], "preconditions": intent["adopt_preconditions"]}
    if not isinstance(slot, str) or not slot.startswith("work:") or not slot[5:].isdecimal():
        raise _bad("Unrecognized child slot", "PM_WORK_START_CHILD_CHANGED", 409)
    index = int(slot[5:])
    if slot != f"work:{index}" or index >= len(intent["works"]):
        raise _bad("Child slot is outside the original selection", "PM_WORK_START_CHILD_CHANGED", 409)
    receipt = _adoption_receipt(ops, _child(ops, parent, "adopt"))
    if not receipt:
        raise _bad("A Work start requires the original adoption receipt", "PM_ADOPTED_PLAN_REQUIRED", 409)
    work = intent["works"][index]
    return {**common, "action": start.ACTION,
        "target": {"workspace_id": intent["workspace_id"], "work_item_id": work["work_item_id"]},
        "params": work["params"],
        "preconditions": {**work["preconditions"], "expected_scope_revision": receipt["scope_revision"]}}


def _child(ops, parent, slot):
    row = ops.db.execute("SELECT * FROM pm_work_adopt_and_start_children WHERE parent_id=? AND slot=?",
        (parent["operation_id"], slot)).fetchone()
    if row is None:
        return None
    request = _request(ops, parent, slot)
    child = adoption._operation(ops.db, row["operation_id"])
    if (json.loads(row["request"]) != request or row["request_digest"] != pm_runs._digest(request)
            or row["ordinal"] != (0 if slot == "adopt" else int(slot[5:]) + 1)
            or any(child.get(key) != value for key, value in request.items())
            or child["actor"] != parent["actor"] or child["external_refs"].get(LINK) != parent["operation_id"]
            or not controller_authority.same_controller(controller_authority.operation_binding(child), _intent(parent)["controller"])):
        raise _bad("The original child relation changed", "PM_WORK_START_CHILD_CHANGED", 409)
    return child


def _children(ops, parent):
    rows = ops.db.execute("SELECT slot FROM pm_work_adopt_and_start_children WHERE parent_id=? ORDER BY ordinal",
        (parent["operation_id"],)).fetchall()
    if len(rows) > MAX_SELECTED_WORKS + 1:
        raise _bad("Too many children for the original selection", "PM_WORK_START_CHILD_CHANGED", 409)
    return {row["slot"]: _child(ops, parent, row["slot"]) for row in rows}


def _guard(ops, parent):
    intent = _intent(parent)
    if parent["cancel_requested"] or parent["status"] not in RUNNABLE:
        raise _bad("The original owner decision stopped new effects", "PM_WORK_START_STOPPED", 409)
    principal = adoption._current_principal(ops, parent)
    start._member(ops, principal, intent["workspace_id"], write=True, decide=True)
    controller = controller_authority.binding(ops.db, principal, pm_workspace_id=intent["workspace_id"])
    if not controller_authority.same_controller(controller, intent["controller"]):
        raise _bad("Original owner controller changed", "PM_WORK_START_CONTROLLER_CHANGED", 403)
    if not operation_visible(ops.db, principal, parent):
        raise _bad("Original decision sources were withdrawn", "PM_CONVERSATION_SOURCE_UNAVAILABLE", 404)
    proposal = proposals._view(ops.db, proposals._row(ops.db, intent["workspace_id"], intent["adopt_params"]["proposal_id"]))
    adoption.check_frozen_review(ops, intent["workspace_id"], proposal,
        intent["adopt_params"]["review"], intent["conversation_review"])
    pm_lifecycle_gates.require_local(ops.db, parent["operation_id"])
    receipt = _adoption_receipt(ops, _child(ops, parent, "adopt"))
    if receipt:
        current = pm_plans.accepted_plan(ops, principal, intent["workspace_id"])["plan"]
        if current is None or current["adoption"]["scope_revision"] != receipt["scope_revision"]:
            raise _bad("The adopted scope changed; review again", "PM_PACKAGE_SCOPE_CHANGED", 409)
    return principal


def _admit_child(ctx, slot):
    def create():
        parent = ctx.service._row(ctx.operation_id)
        principal = _guard(ctx.service, parent)
        request = _request(ctx.service, parent, slot)
        child, fresh = ctx.service.create(principal, **request, entry="work-adopt-and-start")
        if not fresh:
            raise _bad("Child key exists without its immutable relation", "PM_WORK_START_CHILD_CHANGED", 409)
        ctx.service.db.execute("INSERT INTO pm_work_adopt_and_start_children VALUES(?,?,?,?,?,?,?)",
            (ctx.operation_id, slot, 0 if slot == "adopt" else int(slot[5:]) + 1,
             child["operation_id"], pm_runs._json(request), pm_runs._digest(request), time.time()))
        ctx.service._merge_refs(child["operation_id"], {LINK: ctx.operation_id})
        for root in pm_source_lineage.roots(ctx.service.db, "operation", ctx.operation_id):
            ctx.service.db.execute("INSERT OR IGNORE INTO pm_source_lineage VALUES(?,?,?,?)",
                ("operation", child["operation_id"], root, ctx.operation_id))
        return {"operation_id": child["operation_id"]}
    return ctx.effect(ACTION + ".child." + slot, create)


def _refusal(ops, parent, slot):
    row = ops.db.execute("SELECT response FROM operation_steps WHERE operation_id=? AND name=? AND status='succeeded'",
        (parent["operation_id"], ACTION + ".refused." + slot)).fetchone()
    return json.loads(row["response"]) if row else None


def _record_refusal(ctx, slot, exc):
    def record():
        error = {"code": exc.code, "message": exc.message}
        ctx.service._step_status(ctx.operation_id, ACTION + ".child." + slot, "failed", error=error)
        return {"error_code": exc.code, "error_message": exc.message}
    return ctx.effect(ACTION + ".refused." + slot, record, receipt_only=True)


def _uncertain(ops, child):
    if child["status"] == "uncertain" or start._uncertain(ops, child):
        return True
    descendants = (adoption._children(ops.db, child) if child["action"] == proposals.ADOPT
                   else start._children(ops, child).values())
    return any(item["status"] == "uncertain" or start._uncertain(ops, item) for item in descendants)


def _adoption_failure(ops, child):
    if child is None or _uncertain(ops, child):
        return None
    if child["status"] in {"failed", "cancelled"}:
        return child
    return next((item for item in adoption._children(ops.db, child)
                 if item["status"] in {"failed", "cancelled"}), None)


def _state(ops, parent):
    intent = _intent(parent)
    children = _children(ops, parent)
    child = children.get("adopt")
    receipt = _adoption_receipt(ops, child)
    adoption_failed = _adoption_failure(ops, child)
    refusal = _refusal(ops, parent, "adopt")
    adoption_state = {"operation_id": child["operation_id"] if child else None,
        "status": "succeeded" if receipt else child["status"] if child else "refused" if refusal else "not_started",
        **{key: receipt.get(key) if receipt else None for key in ("revision_id", "scope_revision", "adoption_operation_id")},
        "plan_version": receipt.get("version") if receipt else None,
        "error_code": child["error_code"] if child else (refusal or {}).get("error_code"),
        "error_message": child["status_reason"] if child else (refusal or {}).get("error_message")}
    works = []
    for index, work in enumerate(intent["works"]):
        slot = f"work:{index}"
        child = children.get(slot)
        refusal = _refusal(ops, parent, slot)
        result = (child.get("result") or {}) if child else {}
        unknown = _uncertain(ops, child) if child else False
        stage = ("start_unknown" if unknown else "started" if child and child["status"] == "succeeded"
                 else "starting" if child and child["status"] in RUNNABLE else "not_started")
        handoff = start._children(ops, child).get("handoff") if child else None
        handoff_result = (handoff.get("result") or {}) if handoff else {}
        works.append({"index": index, "slot": slot, "work_item_id": work["work_item_id"],
            "requirement_ids": work["params"]["requirement_ids"], "stage": stage,
            "status": child["status"] if child else "refused" if refusal else "not_started",
            "start_operation_id": child["operation_id"] if child else None,
            "attempt_id": "att_" + child["operation_id"][3:] if child else None,
            "binding_id": result.get("binding_id"), "package_id": result.get("package_id"),
            "handoff_operation_id": result.get("execution_operation_id"),
            "session_id": (result.get("session") or {}).get("session_id"),
            "worktree_path": handoff_result.get("worktree_path"), "branch": handoff_result.get("branch"),
            "error_code": child["error_code"] if child else (refusal or {}).get("error_code"),
            "error_message": child["status_reason"] if child else (refusal or {}).get("error_message")})
    started = sum(work["stage"] == "started" for work in works)
    unknown = sum(work["stage"] == "start_unknown" for work in works)
    if not receipt:
        stage = ("adoption_failed" if adoption_failed or adoption_state["status"] == "refused"
                 else "adoption_paused" if adoption_state["status"] == "needs_attention" else "adopting")
    else:
        stage = ("started" if started == len(works) else "partially_started" if started
                 else "start_unknown" if unknown else "starting" if any(work["stage"] == "starting" for work in works)
                 else "adopted_not_started")
    return {"workspace_id": intent["workspace_id"], "thread_id": intent["thread_id"],
        "proposal_id": intent["adopt_params"]["proposal_id"], "stage": stage, "adopted": bool(receipt),
        "adoption": adoption_state, "works": works,
        "summary": {"selected_count": len(works), "started_count": started,
            "not_started_count": sum(work["stage"] == "not_started" for work in works), "unknown_count": unknown}}


def _save(ops, parent, *, stage=None):
    state = _state(ops, parent)
    if stage:
        state["stage"] = stage
    with ops.journal.tx():
        ops.db.execute("UPDATE operations SET result=?,updated_at=? WHERE operation_id=?",
            (pm_runs._json(state), time.time(), parent["operation_id"]))
    return state


async def _run(ctx):
    try:
        return _advance(ctx)
    except OperationError as exc:
        parent = ctx.service._row(ctx.operation_id)
        state = _save(ctx.service, parent)
        if parent["cancel_requested"]:
            ctx.check_cancel()
        if exc.code in UNRESUMABLE:
            if not state["adopted"]:
                _save(ctx.service, parent, stage="adoption_failed")
            raise
        # Completed per-Work refusals and an unstarted adoption refusal are
        # terminal; repairable parent gates retain the original child keys.
        if (_adoption_failure(ctx.service, _child(ctx.service, parent, "adopt"))
                or state["stage"] == "adoption_failed" or all(
                item["status"] in {"succeeded", "failed", "cancelled", "refused"} for item in state["works"])):
            raise
        raise NeedsAttention(exc.code, exc.message) from exc


def _advance(ctx):
    ops, parent = ctx.service, ctx.service._row(ctx.operation_id)
    state = _save(ops, parent)
    adopt = _child(ops, parent, "adopt")
    if not state["adopted"]:
        if state["adoption"]["status"] == "refused":
            raise _bad(state["adoption"]["error_message"], state["adoption"]["error_code"], 409)
        if adopt is None:
            _guard(ops, parent)
            try:
                _admit_child(ctx, "adopt")
            except OperationError as exc:
                # Authority and lifecycle failures are parent gates, not an
                # unreviewed replacement decision or a manufactured child.
                if exc.code in {"VERSION_CONFLICT", "PM_CONVERSATION_CHANGED", "INVALID_PARAMS"}:
                    _record_refusal(ctx, "adopt", exc)
                raise
            _save(ops, parent)
            raise Wait("waiting_external", "Waiting for the original plan adoption", 1)
        failure = _adoption_failure(ops, adopt)
        if failure:
            raise _bad(failure["status_reason"] or "Original adoption stopped", failure["error_code"] or "PM_CONVERSATION_CHILD_STOPPED", 409)
        if adopt["status"] == "needs_attention":
            raise NeedsAttention(adopt["error_code"] or "PM_CONVERSATION_CHILD_STOPPED", adopt["status_reason"] or "Original adoption paused")
        raise Wait("waiting_external", "Waiting for the original plan adoption", 1)
    # An inner receipt may precede the middle operation's terminal transition.
    if adopt["status"] != "succeeded":
        failure = _adoption_failure(ops, adopt)
        if failure:
            raise _bad(failure["status_reason"] or "Original adoption stopped",
                failure["error_code"] or "PM_CONVERSATION_CHILD_STOPPED", 409)
        if adopt["status"] == "needs_attention":
            raise NeedsAttention(adopt["error_code"] or "PM_CONVERSATION_CHILD_STOPPED",
                adopt["status_reason"] or "Completing the original adoption paused")
        raise Wait("waiting_external", "Completing the original adoption receipt", 1)
    for index in range(len(_intent(parent)["works"])):
        slot = f"work:{index}"
        child = _child(ops, parent, slot)
        if _refusal(ops, parent, slot):
            continue
        if child is None:
            _guard(ops, parent)
            try:
                _admit_child(ctx, slot)
            except OperationError as exc:
                if exc.code not in PER_WORK_REFUSAL_CODES:
                    raise
                # Outside ctx.effect: the child INSERT and Attempt allocation
                # have already rolled back before this separate receipt commits.
                _record_refusal(ctx, slot, exc)
                _save(ops, parent)
                continue
            _save(ops, parent)
            raise Wait("waiting_external", "Waiting for the original Work start", 1)
        if _uncertain(ops, child):
            state = _save(ops, parent)
            raise NeedsAttention("PM_WORK_START_OUTCOME_UNPROVEN", "Read back the original start; never resend it")
        if child["status"] == "needs_attention":
            _save(ops, parent, stage="partially_started" if state["summary"]["started_count"] else "adopted_not_started")
            raise NeedsAttention(child["error_code"] or "PM_WORK_START_CHILD_STOPPED", child["status_reason"] or "Original start paused")
        if child["status"] not in {"succeeded", "failed", "cancelled"}:
            raise Wait("waiting_external", "Waiting for the original Work start", 1)
    state = _save(ops, parent)
    failed = next((work for work in state["works"] if work["stage"] != "started"), None)
    if failed:
        raise _bad(failed["error_message"] or "The adopted plan has Works that did not start",
            failed["error_code"] or "PM_WORK_START_CHILD_STOPPED", 409)
    return state


def _parent_relation(ops, op):
    """Resolve only immutable direct or grandchild relations, never caller IDs."""
    link = (op.get("external_refs") or {}).get(LINK)
    if not installed(ops.db):
        if link is not None:
            raise _bad("Unproven adopt-and-start parent", "PM_WORK_START_CHILD_CHANGED", 409)
        return None
    row = ops.db.execute("SELECT parent_id,slot FROM pm_work_adopt_and_start_children WHERE operation_id=?",
        (op["operation_id"],)).fetchone()
    if row is not None or link is not None:
        if row is None or link != row["parent_id"]:
            raise _bad("Unproven adopt-and-start child link", "PM_WORK_START_CHILD_CHANGED", 409)
        parent = adoption._operation(ops.db, row["parent_id"])
        child = _child(ops, parent, row["slot"])
        if child is None or child["operation_id"] != op["operation_id"]:
            raise _bad("Child is outside its original selection", "PM_WORK_START_CHILD_CHANGED", 409)
        return parent, child
    refs = op.get("external_refs") or {}
    for table, key in (("pm_conversation_children", adoption.LINK), ("pm_work_start_children", start.LINK)):
        if not ops.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone():
            continue
        query = ("SELECT parent_id FROM pm_conversation_children WHERE operation_id=?" if table == "pm_conversation_children"
                 else "SELECT parent_id FROM pm_work_start_children WHERE operation_id=?")
        row = ops.db.execute(query, (op["operation_id"],)).fetchone()
        mid_id = row["parent_id"] if row else None
        claimed = refs.get(key)
        if isinstance(claimed, dict):
            claimed = claimed.get("operation_id")
        if mid_id is None and isinstance(claimed, str):
            mid_id = claimed
        if mid_id is None:
            continue
        mid = adoption._operation(ops.db, mid_id)
        # Do not recursively traverse arbitrary forged chains. This feature
        # has exactly two levels below its root, proved by the private table.
        root = ops.db.execute("SELECT parent_id,slot FROM pm_work_adopt_and_start_children WHERE operation_id=?", (mid_id,)).fetchone()
        if root is None:
            if mid["external_refs"].get(LINK) is not None:
                raise _bad("Unproven middle child link", "PM_WORK_START_CHILD_CHANGED", 409)
            continue
        parent = adoption._operation(ops.db, root["parent_id"])
        mid = _child(ops, parent, root["slot"])
        if row is None or mid is None:
            raise _bad("Missing original grandchild relation", "PM_WORK_START_CHILD_CHANGED", 409)
        descendants = (adoption._children(ops.db, mid) if table == "pm_conversation_children"
                       else start._children(ops, mid).values())
        if not any(item["operation_id"] == op["operation_id"] for item in descendants):
            raise _bad("Grandchild is outside the original decision", "PM_WORK_START_CHILD_CHANGED", 409)
        return parent, mid
    return None


def require_child(ops_or_db, op_or_id):
    if hasattr(ops_or_db, "db"):
        ops, db = ops_or_db, ops_or_db.db
    else:
        db = ops_or_db
        current = pm_source_lineage._EXECUTION.get()
        ops = current[0] if current is not None and current[0].db is db else start._Reader(db)
    if isinstance(op_or_id, dict):
        op = op_or_id
    else:
        row = db.execute("SELECT * FROM operations WHERE operation_id=?", (op_or_id,)).fetchone()
        if row is None:
            return None
        from .operations import OperationService
        op = OperationService._decode(row)
    relation = _parent_relation(ops, op)
    if relation is None:
        return None
    if not hasattr(ops, "context"):
        raise _bad("A private child requires its original operation worker", "PM_WORK_START_AUTHORITY_UNAVAILABLE", 403)
    parent, mid = relation
    _guard(ops, parent)
    if mid["action"] == start.ACTION:
        intent = _intent(parent)
        start.require_work_controller(db, intent["workspace_id"], mid["target"]["work_item_id"], intent["controller"])
    return parent


def admit_restart(ops, operation_id, name):
    if require_child(ops, operation_id) is None:
        return
    if not ops.db.in_transaction:
        raise _bad("Retry requires the existing journal transaction", "PM_WORK_START_RETRY_TRANSACTION", 409)
    row = ops.db.execute("SELECT status FROM operation_steps WHERE operation_id=? AND name=?", (operation_id, name)).fetchone()
    if not row or row["status"] not in {"started", "uncertain"}:
        raise _bad("Only proven-unsent unresolved steps may restart", "PM_WORK_START_RETRY_UNPROVEN", 409)


def authorize_child(ops, principal, op, verb):
    relation = _parent_relation(ops, op)
    if relation is None:
        return
    parent, _ = relation
    _authorize_existing(ops, principal, parent, verb)
    current = controller_authority.binding(ops.db, principal, pm_workspace_id=parent["target"]["workspace_id"])
    if not controller_authority.same_controller(current, _intent(parent)["controller"]):
        raise _bad("Steering requires the original decision controller", "CONTROLLER_MISMATCH", 403)
    forwarded = _STEERING.get() == (id(ops), parent["operation_id"], verb)
    if verb == "cancel" and parent["status"] in RUNNABLE and not forwarded:
        raise _bad("Cancel the original adopt-and-start decision", "FORBIDDEN", 403)
    if verb == "resume" and (parent["cancel_requested"] or parent["status"] not in RUNNABLE) and not start._uncertain(ops, op):
        raise _bad("A stopped decision cannot resume unsent children", "FORBIDDEN", 403)


def cancel_children(ops, principal, parent):
    """Stop future children; already-started sessions retain their own authority."""
    token = _STEERING.set((id(ops), parent["operation_id"], "cancel"))
    try:
        for child in _children(ops, parent).values():
            if child["action"] == proposals.ADOPT:
                for grandchild in adoption._children(ops.db, child):
                    if grandchild["status"] in RUNNABLE:
                        ops.cancel(principal, grandchild["operation_id"])
            if child["status"] in RUNNABLE:
                ops.cancel(principal, child["operation_id"])
    finally:
        _STEERING.reset(token)
    state = _save(ops, parent, stage="cancelled")
    if state["summary"]["unknown_count"] or any(_uncertain(ops, child) for child in _children(ops, parent).values()):
        ops.db.execute("UPDATE operations SET cancel_requested=1 WHERE operation_id=?", (parent["operation_id"],))
        _save(ops, parent, stage="partially_started" if state["summary"]["started_count"] else "start_unknown")
        return ops._transition(parent["operation_id"], "needs_attention", actor=principal.actor,
            reason="Cancellation cannot settle an unproven original effect", error_code="PM_WORK_START_OUTCOME_UNPROVEN")
    return None


def resume_children(ops, principal, parent):
    token = _STEERING.set((id(ops), parent["operation_id"], "resume"))
    try:
        for child in _children(ops, parent).values():
            # Sent effects are reconciled only by their existing workers. A
            # parent resume never clears a child fence or replaces its key.
            if child["status"] == "needs_attention" and not _uncertain(ops, child):
                if child["action"] == proposals.ADOPT:
                    for grandchild in adoption._children(ops.db, child):
                        if grandchild["status"] == "needs_attention" and not start._uncertain(ops, grandchild):
                            ops.resume(principal, grandchild["operation_id"])
                ops.resume(principal, child["operation_id"])
    finally:
        _STEERING.reset(token)


def _review_preview(ops, principal, target, body):
    start._member(ops, principal, target["workspace_id"])
    proposal = proposals.get_proposal(ops, principal, target["workspace_id"], body["proposal_id"])["proposal"]
    context = proposal["context"]
    pre = {"expected_version": context["thread_version"],
        "expected_plan_version": context["plan_head"]["version"],
        "expected_accepted_revision_id": context["plan_head"]["accepted_revision_id"],
        "expected_proposal_digest": proposal["document_digest"]}
    adoption.check_review(ops, principal, target,
        {"proposal_id": body["proposal_id"], "review": body["review"],
         "rationale": body.get("rationale") or "Preview the reviewed selection"}, pre)
    _selection(body["works"], body["review"], preview=True)
    return pre


async def preview(ops, principal, workspace_id, thread_id, body):
    """Bounded reads only; one shared source read per distinct selected target."""
    if (not isinstance(body, dict) or not {"proposal_id", "review", "works"} <= set(body)
            or set(body) - {"proposal_id", "review", "rationale", "works"}):
        raise _bad("Preview requires one proposal, its whole review and selected Works")
    body = copy.deepcopy(body)
    target = {"workspace_id": workspace_id, "thread_id": thread_id}
    pre = _review_preview(ops, principal, target, body)
    # A read-only member may preview. The write path separately requires its
    # full workspace-bound authority; no new credential is constructed here.
    controller = controller_authority.binding(ops.db, principal)
    sources, branches, works = {}, {}, []
    for raw in body["works"]:
        wi = raw["work_item_id"]
        pm_source_lineage.require(ops.db, principal, "work_item", wi)
        work = work_items._get_item(ops.db, wi, active=True)
        project = work_items._get_project(ops.db, work["project_id"], active=True)
        if not ops.db.execute("SELECT 1 FROM pm_project_bindings WHERE workspace_id=? AND project_id=?",
                (workspace_id, project["project_id"])).fetchone():
            raise _bad("Explicitly bind this Work's project first", "PM_PROJECT_BINDING_REQUIRED", 409)
        if raw.get("instruction") is not None:
            pm_plans._text(raw["instruction"], "instruction", 4000)
        selected = {"work_item_id": wi, "requirement_ids": raw["requirement_ids"],
            "instruction": (raw.get("instruction") or work.get("request") or work.get("goal") or work["title"] or "Start work").strip()[:4000],
            "agent": raw.get("agent", "claude"), "model": raw.get("model"),
            "delivery": raw.get("delivery", "pull_request")}
        raw_target = raw.get("code_target") if raw.get("code_target") is not None else {}
        if (not isinstance(raw_target, dict) or set(raw_target) - set(start.CODE_TARGET_KEYS)
                or any(value is not None and (not isinstance(value, str) or not value.strip() or len(value) > 256)
                       for value in raw_target.values())):
            raise _bad("code_target accepts repository, host, workspace_id and source_ref")
        candidates = [item for item in repository_sync.capabilities(ops)
            if item["repository"].lower() in {name.lower() for name in project["repositories"]}
            and all(not raw_target.get(key) or (str(item[key]).lower() == raw_target[key].strip().lower() if key == "repository"
                    else item[key] == raw_target[key].strip()) for key in ("repository", "host", "workspace_id"))]
        result = {**selected, "code_target": None, "source": None, "candidates": candidates,
            "package_plan": None, "preconditions": None,
            "readiness": {"ready": False, "blocking_code": None, "blocking_message": None, "checks": []}}
        if len(candidates) != 1:
            result["readiness"].update(blocking_code="REPOSITORY_NOT_BOUND",
                blocking_message="Select one configured repository binding")
            works.append(result)
            continue
        selected_target = {key: candidates[0][key] for key in ("repository", "host", "workspace_id")}
        try:
            config = repository_sync.config_snapshot(ops, selected_target)
            source_ref = raw_target.get("source_ref")
            if source_ref is None:
                if config["repository"] not in branches:
                    document = await repository_sync.gh_read(ops.context["github"].repository(config["repository"]))
                    name = document.get("default_branch")
                    if not isinstance(name, str) or not name.strip():
                        raise _bad("Repository default branch is unavailable", "REPOSITORY_SOURCE_UNAVAILABLE", 409)
                    branches[config["repository"]] = "refs/heads/" + name.strip()
                source_ref = branches[config["repository"]]
            source_ref = source_ref.strip()
            repository_sync.check_ref(source_ref)
            source_target = {**selected_target, "source_ref": source_ref}
            source_key = tuple(source_target[key] for key in start.CODE_TARGET_KEYS)
            if source_key not in sources:
                sources[source_key] = await repository_sync.source_preview(ops, source_target)
            source = sources[source_key]
            # Network awaits never hold a DB snapshot or a stale authorization.
            _review_preview(ops, principal, target, body)
            pm_source_lineage.require(ops.db, principal, "work_item", wi)
            work = work_items._get_item(ops.db, wi, active=True)
            project = work_items._get_project(ops.db, work["project_id"], active=True)
            binding = pm_evidence._binding(ops.db, workspace_id, wi)
            conditions = {"expected_work_item_fingerprint": work_items.fingerprint(work),
                "expected_work_version": work["version"], "expected_project_version": project["version"],
                "expected_binding_id": binding["binding_id"] if binding else None,
                "expected_previous_package_id": pm_work_packages._latest(ops.db, workspace_id, wi),
                "expected_latest_attempt_id": start.latest_attempt_id(ops.db, workspace_id, wi),
                "repository_id": source["repository_id"], "binding_digest": source["binding_digest"],
                "expected_source_sha": source["source_sha"]}
            child_params = {key: value for key, value in selected.items() if key != "work_item_id"}
            child_params["code_target"] = {**source["target"], "source_ref": source["source_ref"]}
            result.update(code_target=child_params["code_target"],
                source={**child_params["code_target"], "source_sha": source["source_sha"]},
                preconditions={"work_item_id": wi, **conditions})
            local = start.check_work_preconditions(ops, principal, workspace_id, wi, child_params, conditions,
                controller=controller, pending_scope=True)
            checks = _advisory(ops, principal, workspace_id, local, selected, body["review"])
            result.update(readiness={"ready": True, "blocking_code": None, "blocking_message": None, "checks": checks},
                package_plan={"mode": "compose", "bind_needed": True, "package_needed": True,
                    "previous_package_id": conditions["expected_previous_package_id"]})
        except OperationError as exc:
            if exc.code not in PER_WORK_REFUSAL_CODES | {"REPOSITORY_SOURCE_UNAVAILABLE"}:
                raise
            result["readiness"].update(blocking_code=exc.code, blocking_message=exc.message)
        works.append(result)
    _review_preview(ops, principal, target, body)
    return {**target, "proposal_id": body["proposal_id"], "adoptable": True, "adoptable_reason": None,
        "ready": all(item["readiness"]["ready"] for item in works), "works": works,
        "preconditions": {**pre, "works": [item["preconditions"] for item in works]}, "observed_at": time.time()}


def capabilities(ops=None):
    available = ops is not None and ACTION in ops.actions
    return {"version": 1, "supported": available, "available": available,
        "action": ACTION, "preview_read": PREVIEW, "reads": [PREVIEW], "mcp_tools": [PREVIEW],
        "routes": {"preview": "/api/v1/pm/workspaces/{workspace_id}/threads/{thread_id}/adopt-and-start-preview",
            "operations": "/api/v1/operations"},
        "required_scopes": sorted(start.REQUIRED_SCOPES), "preview_required_scopes": ["observe"],
        "max_selected_works": MAX_SELECTED_WORKS, "child_actions": [proposals.ADOPT, start.ACTION],
        "delivery_goals": sorted(start.DELIVERY_GOALS), "agents": sorted(start.AGENTS),
        "existing_sessions": "continue_under_original_authority"}
