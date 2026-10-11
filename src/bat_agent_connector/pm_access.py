"""Apply PM membership to shared operation and event read surfaces.

Legacy Connector resources retain their existing visibility. A PM operation's
parameters/results must not bypass workspace membership through the global feed.
Filtering a page retains its original cursor, including wholly hidden pages.
"""
from __future__ import annotations

import json

from .operations import OperationError


def visible(db, principal, operation):
    if not operation.get("action", "").startswith("pm."):
        return True
    from .pm_workspace import operation_visible
    return operation_visible(db, principal, operation)


def require_visible(db, principal, operation):
    if not visible(db, principal, operation):
        raise OperationError("NOT_FOUND", "operation not found", 404)


def event_visible(db, principal, event):
    if event["resource_type"] == "operation":
        row = db.execute("SELECT operation_id,actor,action,target,result FROM operations WHERE operation_id=?",
                         (event["resource_id"],)).fetchone()
        if row is None:
            return not event.get("body", {}).get("action", "").startswith("pm.")
        return visible(db, principal, {**dict(row), "target": json.loads(row["target"]),
                                      "result": json.loads(row["result"]) if row["result"] else None})
    if event["resource_type"] == "pm_maintenance_policy" or event["kind"].startswith("pm.maintenance_policy."):
        from .pm_maintenance_policy import operation_visible
        operation_id = event.get("body", {}).get("operation_id")
        return bool(operation_id) and operation_visible(db, principal, {"operation_id": operation_id})
    if event["resource_type"] == "pm_release_baseline" or event["kind"].startswith("pm.release_baseline."):
        from .pm_release_baselines import operation_visible
        operation_id = event.get("body", {}).get("operation_id")
        return bool(operation_id) and operation_visible(db, principal, {"operation_id": operation_id})
    if event["resource_type"] == "pm_work_package" or event["kind"].startswith("pm.work_package."):
        from .pm_work_packages import operation_visible
        operation_id = event.get("body", {}).get("operation_id")
        return bool(operation_id) and operation_visible(db, principal, {"operation_id": operation_id})
    if event["resource_type"] == "pm_follow_up_assessment" or event["kind"] == "pm.follow_up.analyze":
        from .pm_follow_up_analysis import operation_visible as assessment_visible
        operation_id = event.get("body", {}).get("operation_id")
        return bool(operation_id) and assessment_visible(db, principal, {"operation_id": operation_id})
    if event["resource_type"] == "pm_follow_up" or event["kind"].startswith("pm.follow_up."):
        from .pm_follow_up import event_visible as follow_up_event_visible
        return follow_up_event_visible(db, principal, event)
    if event["resource_type"] == "pm_governance" or event["kind"].startswith("pm.governance."):
        from .pm_governance import event_visible as governance_event_visible
        return governance_event_visible(db, principal, event)
    if event["resource_type"] == "pm_meeting" or event["kind"].startswith("pm.meeting."):
        from .pm_meetings import operation_visible
        operation_id = event.get("body", {}).get("operation_id")
        return bool(operation_id) and operation_visible(db, principal, {"operation_id": operation_id})
    if event["resource_type"].startswith("pm_") or event["kind"].startswith("pm."):
        from .pm_workspace import require_member
        workspace_id = event.get("body", {}).get("workspace_id")
        if not workspace_id:
            return False
        try:
            require_member(db, principal, workspace_id)
        except OperationError:
            return False
    return True


def filter_events(db, principal, page):
    return {**page, "events": [event for event in page["events"] if event_visible(db, principal, event)]}
