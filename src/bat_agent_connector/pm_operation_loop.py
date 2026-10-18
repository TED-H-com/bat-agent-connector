"""Read-only ``loop`` projection for one public operation response.

The projection recomputes on every call. It performs no BAT IPC, no database
writes, and it does not keep its own cache or table. An id or hash whose source
is no longer visible is omitted; the operation itself stays in the response.
"""
from __future__ import annotations

LOOP_ACTIONS = frozenset({
    "pm.thread.create",
    "pm.thread.rename",
    "pm.message.append",
    "pm.input.record",
    "pm.input.confirm",
    "pm.thread.plan",
    "pm.thread.plan.revise",
    "pm.run.analyze",
    "pm.run.bat.analyze",
    "pm.conversation.propose",
    "pm.continuation.prepare",
    "pm.conversation.adopt",
    "pm.plan.draft",
    "pm.plan.adopt",
    "pm.work_package.create",
    "work.start",
    "session.send",
    "github.pr.publish",
    "github.pr.update",
    "github.pr.merge",
    "delivery.merge_and_deploy",
    "deployment.start",
    "deployment.rollback",
    "integration.preview",
    "integration.apply",
    "integration.handoff",
    "pm.maintenance.repair",
})

_DISCUSSION = frozenset({
    "pm.continuation.prepare",
    "pm.thread.create",
    "pm.thread.rename",
    "pm.message.append",
    "pm.input.record",
    "pm.input.confirm",
    "pm.thread.plan",
    "pm.thread.plan.revise",
    "pm.run.analyze",
    "pm.run.bat.analyze",
})
_RESULT_HTTP = "/api/v1/pm/workspaces/{workspace_id}/work-attempts/{attempt_id}"


async def attach(ops, principal, operation):
    """Return ``operation`` with ``loop``, or the same object when ``loop`` does not apply."""
    if not isinstance(operation, dict) or not isinstance(operation.get("action"), str):
        return operation
    kind, parent_id = _classify(ops.db, operation)
    parent = None
    if kind == "broken":
        # One parent store is missing, or the two disagree. Do not raise
        # PM_WORK_START_CHILD_CHANGED and do not fall through to the allowlist.
        return operation
    if kind == "proven":
        parent = ops._row(parent_id)
        from .pm_work_start import ACTION as work_start
        if parent is None or parent.get("action") != work_start or not _parent_visible(ops, principal, parent):
            return operation
    elif operation["action"] not in LOOP_ACTIONS:
        return operation
    return {**operation, "loop": await _project(ops, principal, operation, parent)}


def execution_work_identity(ops, principal, item):
    """Read-only association for a visible, proven Work handoff in a project list.

    Reuse loop lineage and visibility checks without collecting Result/actions.
    This association grants no control and does not match names or checkout paths.
    """
    if item.get("kind") != "execution" or not _operation_visible(ops, principal, item.get("id")):
        return None
    operation = ops.get(item["id"], steps=False)
    kind, parent_id = _classify(ops.db, operation)
    if kind != "proven":
        return None
    parent = ops._row(parent_id)
    if parent is None or parent.get("action") != "work.start" or not _parent_visible(ops, principal, parent):
        return None
    attempt = _attempt_row(ops.db, parent_id)
    if not attempt or not _work_visible(ops, principal, attempt["workspace_id"], attempt["work_item_id"]):
        return None
    view, unreadable, dropped = _attempt_view(ops, principal, operation, attempt)
    if (not view or unreadable or dropped or view.get("stage") != "started"
            or view.get("execution_operation_id") != operation["operation_id"]):
        return None
    pair = _session_pair(view.get("session"))
    if pair != _session_pair(item) or pair is None or not _session_source_visible(ops.db, principal, *pair):
        return None
    return {key: attempt[key] for key in ("workspace_id", "work_item_id", "attempt_id")}


def _classify(db, operation):
    from .pm_work_start import LINK, installed

    refs = operation.get("external_refs") if isinstance(operation.get("external_refs"), dict) else {}
    link = refs.get(LINK)
    row = None
    if installed(db) and isinstance(operation.get("operation_id"), str):
        row = db.execute(
            "SELECT parent_id, slot FROM pm_work_start_children WHERE operation_id=?",
            (operation["operation_id"],),
        ).fetchone()
    if link is None and row is None:
        return "none", None
    if row is None or not isinstance(link, dict) or link != {"operation_id": row["parent_id"], "slot": row["slot"]}:
        return "broken", None
    return "proven", row["parent_id"]


def _parent_visible(ops, principal, parent) -> bool:
    from .pm_access import visible

    return bool(visible(ops.db, principal, parent))


async def _project(ops, principal, operation, parent):
    from .pm_delivery_lineage import ACTIONS as delivery_actions

    action = operation["action"]
    proven = parent is not None
    work_input = _input_attempt(ops, principal, operation) if not proven else None
    start_id = parent["operation_id"] if proven else (operation["operation_id"] if action == "work.start" else None)
    if work_input is not None:
        start_id = work_input["start_operation_id"]
    attempt = work_input if work_input is not None else (_attempt_row(ops.db, start_id) if start_id else None)
    view, view_unreadable, dropped = (
        _attempt_view(ops, principal, operation, attempt) if attempt else (None, False, False))
    delivery_unlinked = False
    expects_work = proven or work_input is not None or action in {"work.start", "pm.work_package.create"}
    expects_attempt = proven or work_input is not None or action == "work.start"
    work_item_id = None
    workspace_id = None
    work_visible = False
    attempt_id = attempt["attempt_id"] if attempt else None

    if proven or work_input is not None or action == "work.start":
        work_item_id, workspace_id, work_visible, dropped = _start_work(
            ops, principal, parent or operation, attempt, dropped)
    elif action == "pm.work_package.create":
        work_item_id, workspace_id, work_visible, dropped = _package_work(ops, principal, operation, dropped)
    elif action in delivery_actions:
        work_item_id, workspace_id, delivery_unlinked = _delivery_work(ops, principal, operation)
        work_visible = work_item_id is not None

    thread_hash, dropped = _discussion_hash(ops, principal, operation, dropped)
    session_hash, link_session, dropped = _sessions(ops, principal, operation, view, dropped)
    execution_id, dropped = _execution(ops, principal, view, dropped)
    allowed, blocked, actions_unreadable = await _actions(ops, principal, operation, parent, attempt)
    unreadable = view_unreadable or actions_unreadable
    if unreadable:
        # The view and the copied work.start rows are one read. A refusal omits both.
        link_session = None
        execution_id = None
        if parent is None and action == "work.start":
            allowed, blocked = [], []
    stage = None if unreadable else (
        view.get("stage") if view and isinstance(view.get("stage"), str) else None)
    state = {"operation_status": operation.get("status"), "updated_at": operation.get("updated_at")}
    if attempt_id is not None and stage is not None:
        state["attempt_stage"] = stage
    published_session = session_hash if action == "session.send" else None
    if thread_hash:
        dashboard = thread_hash
    elif workspace_id and work_item_id:
        dashboard = f"#/pm/{workspace_id}/board?select={work_item_id}"
    elif published_session:
        dashboard = published_session
    else:
        dashboard = f"#/op/{operation['operation_id']}"
    return {
        "operation_id": operation["operation_id"],
        "work_item_id": work_item_id,
        "attempt_id": attempt_id,
        "state": state,
        "next_allowed_actions": allowed,
        "blocked_actions": blocked,
        "links": _links(start_id, workspace_id, work_item_id, execution_id, link_session),
        "dashboard_hash": dashboard,
        "result_read": _result_read(attempt_id),
        "completeness": _completeness(
            operation, dropped=dropped, delivery_unlinked=delivery_unlinked, expects_work=expects_work,
            work_visible=work_visible, expects_attempt=expects_attempt, attempt_present=attempt is not None,
            attempt_unreadable=unreadable, work_input=work_input is not None),
    }


def _start_work(ops, principal, source, attempt, dropped):
    """Publish ``work_attempts.work_item_id`` only. A missing row does not copy the target.

    An invisible Work is ``no_work_item``. This path never records ``dropped``.
    """
    if attempt is not None:
        candidate, workspace_id = attempt["work_item_id"], attempt["workspace_id"]
        if _work_visible(ops, principal, workspace_id, candidate):
            return candidate, workspace_id, True, dropped
        return None, None, False, dropped
    target = source.get("target") if isinstance(source.get("target"), dict) else {}
    visible = _work_visible(ops, principal, target.get("workspace_id"), target.get("work_item_id"))
    return None, None, visible, dropped


def _package_work(ops, principal, operation, dropped):
    target = operation.get("target") if isinstance(operation.get("target"), dict) else {}
    candidate, workspace_id = target.get("work_item_id"), target.get("workspace_id")
    if _work_visible(ops, principal, workspace_id, candidate):
        return candidate, workspace_id, True, dropped
    return None, None, False, dropped


def _delivery_work(ops, principal, operation):
    from .pm_delivery_lineage import admitted_roots
    from .pm_source_lineage import installed

    roots = admitted_roots(ops.db, operation["operation_id"])
    found = []
    if roots and installed(ops.db):
        marks = ",".join("?" for _ in roots)
        rows = ops.db.execute(
            f"SELECT DISTINCT resource_id FROM pm_source_lineage WHERE resource_type='work_item' "  # noqa: S608
            f"AND original_operation_id IN ({marks})",
            tuple(sorted(roots)),
        ).fetchall()
        for (work_id,) in rows:
            workspace_id = _visible_workspace(ops, principal, operation, work_id)
            if workspace_id is None:
                continue
            found.append((workspace_id, work_id))
            # Two distinct visible Works already answer delivery_unlinked.
            if len({item for _, item in found}) > 1:
                return None, None, True
    if len(found) == 1:
        return found[0][1], found[0][0], False
    return None, None, True


def _visible_workspace(ops, principal, operation, work_id):
    """Delivery reverse lookup. ``for_work`` also requires a current binding."""
    recorded = _recorded_workspace(operation)
    spaces = [recorded] if recorded else _binding_workspaces(ops.db, work_id)
    for workspace_id in spaces:
        if _work_visible(ops, principal, workspace_id, work_id) and _bound(ops.db, workspace_id, work_id):
            return workspace_id
    return None


def _bound(db, workspace_id, work_id) -> bool:
    from . import pm_evidence
    from .pm_source_lineage import _table

    if not _table(db, "pm_work_bindings"):
        return False
    return pm_evidence._binding(db, workspace_id, work_id) is not None


def _discussion_hash(ops, principal, operation, dropped):
    recorded = _recorded_thread(operation)
    if recorded is None:
        return None, dropped
    workspace_id, thread_id = recorded
    from .pm_discussions import THREAD_ID

    if not isinstance(thread_id, str) or not THREAD_ID.fullmatch(thread_id) or not isinstance(workspace_id, str):
        return None, dropped
    from . import pm_discussions, pm_workspace
    from .operations import OperationError

    try:
        pm_workspace.require_member(ops.db, principal, workspace_id)
        pm_discussions._thread(ops.db, workspace_id, thread_id)
    except OperationError:
        # A non-member or a missing thread omits the hash and is not source_withdrawn.
        return None, dropped
    if not pm_discussions._thread_sources_visible(ops.db, principal, thread_id):
        return None, True
    return f"#/pm/{workspace_id}/discussions/{thread_id}", dropped


def _sessions(ops, principal, operation, view, dropped):
    """Dashboard session hash only when this principal can read the session.

    ``links.session_hash`` follows the Result ``_links`` pair, then drops it
    when the session's original sources are no longer visible.
    """
    dashboard = None
    if operation.get("action") == "session.send":
        pair = _session_pair(operation.get("target"))
        if pair is not None:
            if _session_source_visible(ops.db, principal, *pair):
                if principal.allows("observe") and _session_in_journal(ops.db, *pair):
                    dashboard = f"#/session/{pair[0]}/{pair[1]}"
            else:
                dropped = True
    link = None
    if view and isinstance(view.get("session"), dict):
        pair = _session_pair(view["session"])
        if pair is not None:
            if _session_source_visible(ops.db, principal, *pair):
                link = pair
            else:
                dropped = True
    return dashboard, link, dropped


def _execution(ops, principal, view, dropped):
    if not view or not isinstance(view.get("execution_operation_id"), str) or not view["execution_operation_id"]:
        return None, dropped
    execution_id = view["execution_operation_id"]
    if _operation_visible(ops, principal, execution_id):
        return execution_id, dropped
    return None, True


def _links(start_id, workspace_id, work_item_id, execution_id, session):
    if not start_id:
        return None
    host, sid = session if session else (None, None)
    return {
        "start_operation_hash": f"#/op/{start_id}",
        "execution_operation_hash": f"#/op/{execution_id}" if execution_id else None,
        "session_hash": f"#/session/{host}/{sid}" if host and sid else None,
        "work_item_hash": (
            f"#/pm/{workspace_id}/board?select={work_item_id}" if workspace_id and work_item_id else None
        ),
    }


def _result_read(attempt_id):
    if not attempt_id:
        return None
    from .pm_attempt_result_reads import CHANGES_GET, DIFF_GET, RESULT_GET

    return {
        "result": {"name": RESULT_GET, "http": f"{_RESULT_HTTP}/result"},
        "changes": {"name": CHANGES_GET, "http": f"{_RESULT_HTTP}/changes"},
        "diff": {"name": DIFF_GET, "http": f"{_RESULT_HTTP}/diff"},
    }


def _completeness(operation, *, dropped, delivery_unlinked, expects_work, work_visible, expects_attempt,
                  attempt_present, attempt_unreadable, work_input=False):
    if dropped:
        return {"status": "unavailable", "reason": "source_withdrawn"}
    if delivery_unlinked:
        return {"status": "partial", "reason": "delivery_unlinked"}
    if expects_work and not work_visible:
        return {"status": "partial", "reason": "no_work_item"}
    if expects_attempt and work_visible and not attempt_present:
        return {"status": "partial", "reason": "no_attempt"}
    if attempt_unreadable:
        return {"status": "partial", "reason": "attempt_unreadable"}
    # Only an immutable admitted work_inputs row can associate this send with
    # an Attempt. A Work-shaped target alone is not that proof.
    if operation.get("action") == "session.send" and not work_input:
        return {"status": "partial", "reason": "session_target"}
    return {"status": "complete", "reason": None}


def _input_attempt(ops, principal, operation):
    from . import pm_work_follow_up, pm_work_start

    if (not pm_work_follow_up.is_work_follow_up(operation)
            or not pm_work_follow_up.installed(ops.db)
            or not pm_work_start.installed(ops.db)
            or not pm_work_follow_up.operation_visible(ops.db, principal, operation)):
        return None
    row = ops.db.execute(
        """SELECT a.* FROM work_inputs i JOIN work_attempts a
           ON a.attempt_id=i.attempt_id AND a.workspace_id=i.workspace_id
           AND a.work_item_id=i.work_item_id WHERE i.operation_id=?""",
        (operation["operation_id"],),
    ).fetchone()
    if row is None or operation.get("target") != {
        "workspace_id": row["workspace_id"], "work_item_id": row["work_item_id"],
    }:
        return None
    if not _operation_visible(ops, principal, row["start_operation_id"]):
        return None
    return dict(row)


def _attempt_row(db, start_operation_id):
    from .pm_work_start import installed

    if not start_operation_id or not installed(db):
        return None
    row = db.execute("SELECT * FROM work_attempts WHERE start_operation_id=?", (start_operation_id,)).fetchone()
    return dict(row) if row else None


def _attempt_view(ops, principal, operation, row):
    """Return the view, whether it is unreadable, and a case-2 drop.

    ``get_attempt`` refuses the view when a child operation's sources are
    hidden. ``NOT_FOUND`` omits the view. That is ``source_withdrawn`` only
    when this operation's own scanned fields do not name that child. Any
    other ``OperationError`` is ``attempt_unreadable``. ``KeyError`` and
    ``TypeError`` are bugs and are not caught.
    """
    from . import pm_source_lineage
    from .operations import OperationError
    from .pm_work_start import _attempt_view as view

    try:
        shown = view(ops, row)
    except OperationError as error:
        return None, error.code != "NOT_FOUND", False
    children = shown.get("children") if isinstance(shown.get("children"), dict) else {}
    hidden = []
    try:
        for child_id in children.values():
            if not isinstance(child_id, str) or not child_id:
                continue
            try:
                pm_source_lineage.require(ops.db, principal, "operation", child_id)
            except OperationError as error:
                if error.code != "NOT_FOUND":
                    raise
                hidden.append(child_id)
    except OperationError as error:
        return None, error.code != "NOT_FOUND", False
    if not hidden:
        return shown, False, False
    try:
        scanned = {identity for kind, identity in pm_source_lineage.references(operation) if kind == "operation"}
    except OperationError:
        return None, True, False
    # A scanned child is case 1: the read barrier owns that 404.
    return None, False, any(child_id not in scanned for child_id in hidden)


def _work_visible(ops, principal, workspace_id, work_item_id) -> bool:
    """E04 attempt context: observe, membership, and lineage. A binding is not required."""
    from . import pm_source_lineage, pm_workspace
    from .operations import OperationError

    if not _work_id(work_item_id) or not isinstance(workspace_id, str) or not principal.allows("observe"):
        return False
    try:
        pm_workspace.require_member(ops.db, principal, workspace_id, write=False)
        pm_source_lineage.require(ops.db, principal, "work_item", work_item_id)
    except OperationError:
        return False
    return True


def _work_id(value) -> bool:
    from .work_items import WORK_ITEM_ID

    return isinstance(value, str) and WORK_ITEM_ID.fullmatch(value) is not None


def _recorded_workspace(operation):
    target = operation.get("target") if isinstance(operation.get("target"), dict) else {}
    if isinstance(target.get("workspace_id"), str):
        return target["workspace_id"]
    refs = operation.get("external_refs") if isinstance(operation.get("external_refs"), dict) else {}
    binding = refs.get("admission_binding") if isinstance(refs.get("admission_binding"), dict) else {}
    lineage = binding.get("pm_lineage") if isinstance(binding.get("pm_lineage"), dict) else {}
    if isinstance(lineage.get("workspace_id"), str):
        return lineage["workspace_id"]
    return None


def _binding_workspaces(db, work_item_id):
    from .pm_source_lineage import _table

    if not _table(db, "pm_work_bindings"):
        return []
    rows = db.execute(
        """SELECT workspace_id FROM pm_work_bindings b WHERE work_item_id=?
           AND NOT EXISTS (SELECT 1 FROM pm_work_bindings n WHERE n.workspace_id=b.workspace_id
           AND n.work_item_id=b.work_item_id AND n.sequence>b.sequence)""",
        (work_item_id,),
    ).fetchall()
    return [row[0] for row in rows]


def _recorded_thread(operation):
    if operation.get("action") not in _DISCUSSION:
        return None
    target = operation.get("target") if isinstance(operation.get("target"), dict) else {}
    if operation["action"] == "pm.thread.create":
        result = operation.get("result") if isinstance(operation.get("result"), dict) else {}
        thread_id = result.get("thread_id")
    else:
        thread_id = target.get("thread_id")
    if isinstance(thread_id, str) and thread_id:
        return target.get("workspace_id"), thread_id
    return None


def _session_pair(value):
    if not isinstance(value, dict):
        return None
    host, sid = value.get("host"), value.get("session_id")
    if isinstance(host, str) and host and isinstance(sid, str) and sid:
        return host, sid
    return None


def _session_source_visible(db, principal, host, sid) -> bool:
    from .pm_source_lineage import visible

    return bool(visible(db, principal, "session", f"{host}/{sid}"))


def _session_in_journal(db, host, sid) -> bool:
    from .pm_source_lineage import _table

    if _table(db, "sessions_observed") and db.execute(
            "SELECT 1 FROM sessions_observed WHERE host=? AND session_id=?", (host, sid)).fetchone():
        return True
    return bool(_table(db, "observation_resources") and db.execute(
        "SELECT 1 FROM observation_resources WHERE resource_type='session' AND resource_id=?",
        (f"{host}/{sid}",)).fetchone())


def _operation_visible(ops, principal, operation_id) -> bool:
    from .pm_access import visible

    row = ops._row(operation_id)
    return bool(row and visible(ops.db, principal, row))


# Refusal strings already used by the Result envelope. Anything else is omitted.
_NOT_CONTROLLER = "not_controller"
_MISSING_SCOPE = "missing_scope"
_NOT_WRITER = "not_writer_member"
_RECONCILE = "WAIT_ORIGINAL_OPERATION_RECONCILE"
_ACTOR_ONLY = frozenset({
    "Only the original requester controls this proposal",
    "Only the original requester may control native analysis",
    "Only the original requester may control this adoption",
    "Only the original owner controls this review",
    "relay child controls retain the original actor",
})
_SCOPE_ONLY = frozenset({
    "PM operation controls require manage scope",
    "Package reads need observe; creation also needs manage",
    "Native PM analysis requires observe, manage and start scopes",
    "Plan operations require manage scope",
    "Plan adoption requires observe, manage and start scopes",
    "Publication requires observe, manage and integrate authority",
    "dispatch linked work needs manage",
    "Starting work requires observe, manage and start scopes",
    "Evidence reads require observe; changes also require manage",
    "task operation control requires its current action scope",
    "resuming merge-and-deploy needs both merge and deploy scopes",
})
_MEMBERSHIP = "Workspace membership does not permit this action"
_STEER_BLOCKED = frozenset({"cancel", "resume", "work.start"})


async def _actions(ops, principal, operation, parent, attempt):
    """Cancel and resume for this operation.

    ``work.start`` copies this principal's Result envelope. Every other action,
    including a proven child, reads the same conditions ``cancel`` and ``resume``
    read. This does not call ``_may_steer`` and does not write.
    """
    if parent is None and operation["action"] == "work.start":
        return await _work_start_actions(ops, principal, attempt)
    allowed, blocked = _status_actions(ops, principal, operation)
    return allowed, blocked, False


async def _work_start_actions(ops, principal, attempt):
    if attempt is None:
        return [], [], False
    from .operations import OperationError
    from .pm_attempt_result_reads import RESULT_GET, read

    try:
        envelope = await read(ops, principal, RESULT_GET, {
            "workspace_id": attempt["workspace_id"],
            "attempt_id": attempt["attempt_id"],
            "wait_ms": 0,
        })
    except OperationError as error:
        if error.code == "NOT_FOUND":
            return [], [], False
        return [], [], True
    allowed = [row for row in envelope.get("next_allowed_actions") or [] if row.get("kind") == "operation_endpoint"]
    blocked = [row for row in envelope.get("blocked_actions") or [] if row.get("action") in _STEER_BLOCKED]
    return allowed, blocked, False


def _status_actions(ops, principal, operation):
    from .operations import TERMINAL

    allowed, blocked = [], []
    status = operation.get("status")
    if status not in TERMINAL:
        _consider(ops, principal, operation, "cancel", allowed, blocked)
    if status == "uncertain":
        blocked.append({"action": operation["action"], "reason": _RECONCILE})
    elif status == "needs_attention":
        _consider(ops, principal, operation, "resume", allowed, blocked)
    return allowed, blocked


def _consider(ops, principal, operation, verb, allowed, blocked):
    reason = _steer_reason(ops, principal, operation, verb)
    if reason is None:
        allowed.append(_endpoint(operation, verb, _required_scopes(principal, operation, verb)))
    elif reason != "omit":
        blocked.append({"action": verb, "reason": reason})


def _endpoint(operation, verb, scopes):
    operation_id = operation["operation_id"]
    return {
        "kind": "operation_endpoint",
        "endpoint": verb,
        "operation_id": operation_id,
        "http_method": "POST",
        "http_path": f"/api/v1/operations/{operation_id}/{verb}",
        "required_scopes": scopes,
    }


def _required_scopes(principal, operation, verb):
    """Scopes a pure scope check required. Actor or admin allowance stays an empty list."""
    if principal.admin:
        return []
    return _pure_scopes(operation, verb)


def _pure_scopes(operation, verb):
    action = operation.get("action")
    params = operation.get("params") if isinstance(operation.get("params"), dict) else {}
    found = []
    from .pm_discussions import ACTIONS as discussion

    if action in discussion:
        found.append("manage")
    if action == "pm.work_package.create":
        found.extend(("observe", "manage"))
    if action in {"pm.run.analyze", "pm.run.bat.analyze", "pm.conversation.propose", "pm.conversation.adopt", "pm.continuation.prepare"}:
        found.extend(("observe", "manage", "start"))
    if action in {"pm.plan.draft", "pm.plan.adopt"}:
        found.append("manage")
    if action == "pm.plan.adopt":
        found.extend(("observe", "start"))
    if action == "github.pr.publish":
        from .pr_publication import REQUIRED
        found.extend(sorted(REQUIRED))
    if action == "pm.work.bind":
        found.extend(("observe", "manage"))
    from .pm_work_follow_up import is_work_follow_up
    if is_work_follow_up(operation):
        found.append("observe")
    if action == "pm.maintenance.repair":
        found.append("manage")
    if action == "repository.continue" and params.get("work_item_id"):
        found.append("manage")
    if verb == "resume" and action == "delivery.merge_and_deploy":
        found.extend(("merge", "deploy"))
    return list(dict.fromkeys(found))


def _steer_reason(ops, principal, operation, verb):
    """Return a blocked reason, ``omit``, or None when the verb is allowed."""
    from .errors import ResourceReadOnly
    from .operations import OperationError

    try:
        _steer_checks(ops, principal, operation, verb)
    except OperationError as error:
        return _map_refusal(error, ops, principal, operation) or "omit"
    except (KeyError, TypeError, ResourceReadOnly):
        return "omit"
    return None


def _steer_checks(ops, principal, operation, verb):
    from . import bulk_approval, orchestration_operations, pm_work_follow_up
    from .operations import OperationError
    from .pm_access import require_visible

    require_visible(ops.db, principal, operation)
    ops._authorize_controller(principal, operation, verb)
    bulk_approval.authorize_child(ops, principal, operation, verb)
    orchestration_operations.authorize_child(ops, principal, operation, verb)
    defined = ops.actions.get(operation["action"])
    if defined and defined.authorize_existing:
        defined.authorize_existing(ops, principal, operation, verb)
    if verb == "cancel":
        pm_work_follow_up.check_cancel_allowed(ops, principal, operation)
    elif verb == "resume":
        pm_work_follow_up.resume(ops, principal, operation)
    if not (operation.get("actor") == principal.actor or principal.admin
            or (defined and principal.allows(defined.scope))):
        raise OperationError(
            "FORBIDDEN", f"{verb} needs the operation's own actor or its {operation['action']} scope", 403)
    if (verb == "resume" and operation.get("action") == "delivery.merge_and_deploy"
            and not (principal.allows("merge") and principal.allows("deploy"))):
        raise OperationError("FORBIDDEN", "resuming merge-and-deploy needs both merge and deploy scopes", 403)


def _map_refusal(error, ops, principal, operation):
    """First matching §3.2 row for the refusal the endpoint would raise."""
    if error.code == "CONTROLLER_MISMATCH":
        return _NOT_CONTROLLER
    if error.code != "FORBIDDEN":
        return None
    message = error.message
    if message in _ACTOR_ONLY:
        return _NOT_CONTROLLER
    if message == _MEMBERSHIP:
        if _owner_gate(ops, operation) or _member_role(ops, principal, operation) != "reader":
            return None
        return _NOT_WRITER
    if message in _SCOPE_ONLY:
        return _MISSING_SCOPE
    return None


def _owner_gate(ops, operation) -> bool:
    """True when this action's membership check is ``decide=True`` (an owner check)."""
    action = operation.get("action")
    params = operation.get("params") if isinstance(operation.get("params"), dict) else {}
    target = operation.get("target") if isinstance(operation.get("target"), dict) else {}
    if action in {"pm.plan.adopt", "pm.conversation.adopt", "pm.work.bind"}:
        return True
    from .pm_discussions import ACTIONS, _decision

    if action not in ACTIONS:
        return False
    decide = _decision(action, params)
    superseded = params.get("supersedes_input_id")
    if action == "pm.input.record" and isinstance(superseded, str):
        old = ops.db.execute(
            "SELECT confirmed_by FROM pm_inputs WHERE thread_id=? AND input_id=?",
            (target.get("thread_id"), superseded),
        ).fetchone()
        decide = decide or bool(old and old[0])
    return bool(decide)


def _member_role(ops, principal, operation):
    target = operation.get("target") if isinstance(operation.get("target"), dict) else {}
    workspace_id = target.get("workspace_id")
    if not isinstance(workspace_id, str):
        return None
    row = ops.db.execute(
        "SELECT role FROM pm_workspace_members WHERE workspace_id=? AND actor=?",
        (workspace_id, principal.actor),
    ).fetchone()
    if row is not None:
        return row["role"]
    if principal.admin:
        return "owner"
    return None
