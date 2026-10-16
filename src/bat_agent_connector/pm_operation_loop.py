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
    return {**operation, "loop": _project(ops, principal, operation, parent)}


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


def _project(ops, principal, operation, parent):
    from .pm_delivery_lineage import ACTIONS as delivery_actions

    action = operation["action"]
    proven = parent is not None
    start_id = parent["operation_id"] if proven else (operation["operation_id"] if action == "work.start" else None)
    attempt = _attempt_row(ops.db, start_id) if start_id else None
    view = _attempt_view(ops, attempt) if attempt else None
    dropped = False
    delivery_unlinked = False
    expects_work = proven or action in {"work.start", "pm.work_package.create"}
    expects_attempt = proven or action == "work.start"
    work_item_id = None
    workspace_id = None
    work_visible = False
    attempt_id = attempt["attempt_id"] if attempt else None

    if proven or action == "work.start":
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
    stage = view.get("stage") if view and isinstance(view.get("stage"), str) else None
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
        "next_allowed_actions": [],
        "blocked_actions": [],
        "links": _links(start_id, workspace_id, work_item_id, execution_id, link_session),
        "dashboard_hash": dashboard,
        "result_read": _result_read(attempt_id),
        "completeness": _completeness(
            operation, dropped=dropped, delivery_unlinked=delivery_unlinked, expects_work=expects_work,
            work_visible=work_visible, expects_attempt=expects_attempt, attempt_present=attempt is not None),
    }


def _start_work(ops, principal, source, attempt, dropped):
    """Publish ``work_attempts.work_item_id`` only. A missing row does not copy the target."""
    if attempt is not None:
        candidate, workspace_id = attempt["work_item_id"], attempt["workspace_id"]
        if _work_visible(ops, principal, workspace_id, candidate):
            return candidate, workspace_id, True, dropped
        if _work_id(candidate):
            dropped = True
        return None, None, False, dropped
    target = source.get("target") if isinstance(source.get("target"), dict) else {}
    visible = _work_visible(ops, principal, target.get("workspace_id"), target.get("work_item_id"))
    return None, None, visible, dropped


def _package_work(ops, principal, operation, dropped):
    target = operation.get("target") if isinstance(operation.get("target"), dict) else {}
    candidate, workspace_id = target.get("work_item_id"), target.get("workspace_id")
    if _work_visible(ops, principal, workspace_id, candidate):
        return candidate, workspace_id, True, dropped
    if _work_id(candidate):
        dropped = True
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
            if workspace_id is not None:
                found.append((workspace_id, work_id))
    distinct = {work_id for _, work_id in found}
    if len(distinct) == 1:
        return found[0][1], found[0][0], False
    return None, None, True


def _visible_workspace(ops, principal, operation, work_id):
    recorded = _recorded_workspace(operation)
    spaces = [recorded] if recorded else _binding_workspaces(ops.db, work_id)
    for workspace_id in spaces:
        if _work_visible(ops, principal, workspace_id, work_id):
            return workspace_id
    return None


def _discussion_hash(ops, principal, operation, dropped):
    recorded = _recorded_thread(operation)
    if recorded is None:
        return None, dropped
    workspace_id, thread_id = recorded
    from .pm_discussions import THREAD_ID

    if not isinstance(thread_id, str) or not THREAD_ID.fullmatch(thread_id):
        return None, dropped
    if _thread_visible(ops, principal, workspace_id, thread_id):
        return f"#/pm/{workspace_id}/discussions/{thread_id}", dropped
    return None, True


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
                  attempt_present):
    if dropped:
        return {"status": "unavailable", "reason": "source_withdrawn"}
    if delivery_unlinked:
        return {"status": "partial", "reason": "delivery_unlinked"}
    if expects_work and not work_visible:
        return {"status": "partial", "reason": "no_work_item"}
    if expects_attempt and work_visible and not attempt_present:
        return {"status": "partial", "reason": "no_attempt"}
    target = operation.get("target") if isinstance(operation.get("target"), dict) else {}
    if operation.get("action") == "session.send" and not target.get("work_item_id"):
        return {"status": "partial", "reason": "session_target"}
    return {"status": "complete", "reason": None}


def _attempt_row(db, start_operation_id):
    from .pm_work_start import installed

    if not start_operation_id or not installed(db):
        return None
    row = db.execute("SELECT * FROM work_attempts WHERE start_operation_id=?", (start_operation_id,)).fetchone()
    return dict(row) if row else None


def _attempt_view(ops, row):
    from .operations import OperationError
    from .pm_work_start import _attempt_view as view

    try:
        return view(ops, row)
    except (OperationError, KeyError, TypeError):
        # An unproven child receipt makes the attempt view raise. Omit the
        # stage and the link fields that come from it; do not fail the read.
        return None


def _work_visible(ops, principal, workspace_id, work_item_id) -> bool:
    from . import pm_evidence, pm_source_lineage, pm_workspace
    from .operations import OperationError

    if not _work_id(work_item_id) or not isinstance(workspace_id, str) or not principal.allows("observe"):
        return False
    try:
        pm_workspace.require_member(ops.db, principal, workspace_id)
    except OperationError:
        return False
    if not pm_source_lineage._table(ops.db, "pm_work_bindings"):
        return False
    if pm_evidence._binding(ops.db, workspace_id, work_item_id) is None:
        return False
    return bool(pm_source_lineage.visible(ops.db, principal, "work_item", work_item_id))


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


def _thread_visible(ops, principal, workspace_id, thread_id) -> bool:
    from . import pm_discussions, pm_workspace
    from .operations import OperationError

    if not isinstance(workspace_id, str):
        return False
    try:
        pm_workspace.require_member(ops.db, principal, workspace_id)
        pm_discussions._thread(ops.db, workspace_id, thread_id)
    except OperationError:
        return False
    return bool(pm_discussions._thread_sources_visible(ops.db, principal, thread_id))


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
