"""Apply PM membership to shared operation and event read surfaces.

Legacy Connector resources retain their existing visibility. A PM operation's
parameters/results must not bypass workspace membership through the global feed.
Filtering a page retains its original cursor, including wholly hidden pages.
"""
from __future__ import annotations

import json

from . import pm_read_cache
from .operations import OperationError


def operation_source_visible(db, principal, operation_id):
    """Original derived-source access, without upgrading ordinary source claims."""
    from . import pm_source_lineage
    if not isinstance(operation_id, str):
        return False
    if not pm_source_lineage.visible(db, principal, "operation", operation_id):
        return False
    row = db.execute("SELECT action FROM operations WHERE operation_id=?", (operation_id,)).fetchone()
    if row and row[0] in {"pm.conversation.propose", "pm.conversation.adopt"}:
        from .pm_conversation_proposals import operation_visible
        return operation_visible(db, principal, {"operation_id": operation_id})
    if row and row[0] == 'pm.overview.assess':
        from .pm_overview_assessments import operation_visible
        return operation_visible(db, principal, {'operation_id': operation_id})
    if row and row[0] == 'pm.continuation.prepare':
        from .pm_continuation import operation_visible
        return operation_visible(db, principal, {'operation_id': operation_id})
    return True


def require_operation_sources(db, principal, operation_ids):
    from .pm_source_reads import scope
    with scope(db, principal):
        if any(not operation_source_visible(db, principal, identity) for identity in set(operation_ids)):
            raise OperationError("PM_SOURCE_UNAVAILABLE", "Original PM sources are unavailable", 404)


def inherit_operation_sources(db, operation_id, source_operation_ids):
    """Freeze proven roots atomically with a derived record, never parse prose."""
    from . import pm_source_lineage
    if not db.in_transaction:
        raise OperationError("PM_SOURCE_TRANSACTION_REQUIRED", "Freeze original sources inside the record transaction", 409)
    if not pm_source_lineage.installed(db):
        return
    roots = pm_source_lineage.roots(db, "operation", operation_id)
    for identity in set(source_operation_ids):
        if identity == operation_id:
            continue
        roots.update(pm_source_lineage.roots(db, "operation", identity))
        row = db.execute("SELECT action FROM operations WHERE operation_id=?", (identity,)).fetchone()
        if row and row[0] in {"pm.conversation.propose", "pm.conversation.adopt", "pm.continuation.prepare", "pm.overview.assess"}:
            roots.add(identity)
        if len(roots) > pm_source_lineage.MAX_ROOTS:
            raise OperationError("PM_SOURCE_LINEAGE_LIMIT", "Too many original sources for one PM record", 409)
    for original in roots:
        db.execute("INSERT OR IGNORE INTO pm_source_lineage VALUES(?,?,?,?)",
            ("operation", operation_id, original, operation_id))


def visible(db, principal, operation):
    from .pm_maintenance_repairs import source_read_context
    from .pm_source_reads import MAX_NODES, scope, validate_scope
    validate_scope(db, principal)
    # A completed same-snapshot grant needs no second source savepoint or WAL
    # anchor. Validate borrowed state first, including same-task loop turns.
    selection = {key: operation[key] for key in ('operation_id', 'action', 'target', 'actor') if key in operation}
    if not pm_read_cache.is_guarded() and not source_read_context(db, principal):
        with pm_read_cache.scope(db, principal):
            found, result = pm_read_cache.recall(_visible, (principal, selection))
        if found and result:
            return result
    with scope(db, principal) as state:
        # These are the visibility reader's entire inputs. Domain readers load
        # their own canonical records by ID; caller results/contexts are not
        # authority and need not be walked to construct an identical read key.
        try:
            key = pm_read_cache._key(selection)
        except TypeError:
            return _visible(db, principal, selection)
        repairs = source_read_context(db, principal)
        if repairs:
            # Reuse only inside this exact guarded repair traversal. These
            # answers may assume its backedges and never enter the full memo.
            guard = pm_read_cache.guard_identity()
            local_key = guard, repairs, key
            if guard is not None and local_key in state['guarded_visibility']:
                return True
            generation = pm_read_cache.refusal_generation()
            result = _visible(db, principal, selection)
            if (result and guard is not None and generation == pm_read_cache.refusal_generation()
                    and len(state['guarded_visibility']) < MAX_NODES):
                state['guarded_visibility'][local_key] = True
            return result
        if key in state['visibility']:
            return True
        generation = pm_read_cache.refusal_generation()
        result = pm_read_cache.remember(_visible, (principal, selection),
            lambda: _visible(db, principal, selection), cache_errors=False, cache_if=bool)
        # Conversation/spec cycles refuse, never grant. Keep only completed
        # positives, separately bounded without consuming source depth/nodes.
        if (result and not pm_read_cache.is_guarded() and generation == pm_read_cache.refusal_generation()
                and len(state['visibility']) < MAX_NODES):
            state['visibility'][key] = True
        return result


def _visible(db, principal, operation):
    if operation.get("action") in {"connection.reference.apply", "setup.connection.apply"}:
        from .connection_handoff import visible as connection_visible
        if not connection_visible(db, principal, operation):
            return False
    from .pm_source_lineage import visible as source_visible
    if not source_visible(db, principal, "operation", operation.get("operation_id")):
        return False
    if operation.get('action') == 'github.pr.publish':
        from .pr_publication import operation_visible
        return operation_visible(db, principal, operation)
    if operation.get("action") == "work.adopt_and_start":
        from .pm_work_adopt_and_start import operation_visible as adopt_start_visible
        return adopt_start_visible(db, principal, operation)
    if operation.get("action") == "work.start":
        from .pm_work_start import operation_visible
        return operation_visible(db, principal, operation)
    if operation.get("action") == "session.send":
        from . import pm_work_follow_up
        if pm_work_follow_up.is_work_follow_up(operation):
            return pm_work_follow_up.operation_visible(db, principal, operation)
    if not operation.get("action", "").startswith("pm."):
        return True
    from .pm_workspace import operation_visible
    return operation_visible(db, principal, operation)


def require_visible(db, principal, operation):
    if not visible(db, principal, operation):
        raise OperationError("NOT_FOUND", "operation not found", 404)


def event_visible(db, principal, event):
    from .pm_source_lineage import document_roots, visible_roots
    if not visible_roots(db, principal, document_roots(db, event)):
        return False
    if event['resource_type'] == 'pm_workspace_adapter':
        from .pm_workspace_adapter import operation_visible
        return operation_visible(db, principal, {'operation_id': event.get('body', {}).get('operation_id')})
    if event["resource_type"] == "operation":
        row = db.execute("SELECT operation_id,actor,action,target,result FROM operations WHERE operation_id=?",
                         (event["resource_id"],)).fetchone()
        if row is None:
            from . import pm_work_follow_up
            body = event.get("body") if isinstance(event.get("body"), dict) else {}
            action = str(body.get("action") or "")
            if pm_work_follow_up.is_work_input_event(db, event):
                return pm_work_follow_up.missing_row_event_visible(db, principal, event)
            return not action.startswith("pm.") and action not in {"work.start", "work.adopt_and_start"}
        return visible(db, principal, {**dict(row), "target": json.loads(row["target"]),
                                      "result": json.loads(row["result"]) if row["result"] else None})
    if event["resource_type"] == "pm_work_attempt" or event["kind"].startswith("work.start."):
        from .pm_work_start import operation_visible
        operation_id = event.get("body", {}).get("operation_id")
        return bool(operation_id) and operation_visible(db, principal, {"operation_id": operation_id})
    if event["kind"].startswith("pm.dependencies."):
        from .pm_dependencies import operation_visible
        operation_id = event.get("body", {}).get("operation_id")
        return bool(operation_id) and operation_visible(db, principal, {"operation_id": operation_id})
    if event["resource_type"] == "pm_lifecycle" or event["kind"].startswith("pm.lifecycle."):
        from .pm_lifecycle import operation_visible as lifecycle_visible
        operation_id = event.get("body", {}).get("operation_id")
        return bool(operation_id) and lifecycle_visible(db, principal, {"operation_id": operation_id})
    if event["resource_type"] == "pm_issue_mapping" or event["kind"].startswith("pm.issue."):
        from .pm_issue_mappings import operation_visible
        operation_id = event.get("body", {}).get("operation_id")
        return bool(operation_id) and operation_visible(db, principal, {"operation_id": operation_id})
    if event["resource_type"] == "pm_maintenance_check" or event["kind"].startswith("pm.maintenance.check."):
        from .pm_maintenance_checks import operation_visible
        operation_id = event.get("body", {}).get("operation_id")
        return bool(operation_id) and operation_visible(db, principal, {"operation_id": operation_id})
    if event["resource_type"] == "pm_maintenance_repair" or event["kind"].startswith("pm.maintenance.repair."):
        from .pm_maintenance_repairs import operation_visible
        operation_id = event.get("body", {}).get("operation_id")
        return bool(operation_id) and operation_visible(db, principal, {"operation_id": operation_id})
    if event["resource_type"] == "pm_quality_review" or event["kind"].startswith("pm.quality.review."):
        from .pm_quality_reviews import operation_visible
        operation_id = event.get("body", {}).get("operation_id")
        return bool(operation_id) and operation_visible(db, principal, {"operation_id": operation_id})
    if event["resource_type"] == "pm_execution_verification" or event["kind"].startswith("pm.execution.verify."):
        from .pm_execution_verification import operation_visible
        operation_id = event.get("body", {}).get("operation_id")
        return bool(operation_id) and operation_visible(db, principal, {"operation_id": operation_id})
    if event["resource_type"] == "pm_work_batch" or event["kind"].startswith("pm.work_batch."):
        from .pm_work_batches import operation_visible
        operation_id = event.get("body", {}).get("operation_id")
        return bool(operation_id) and operation_visible(db, principal, {"operation_id": operation_id})
    if event["kind"].startswith("pm.spec."):
        from .pm_spec_materialization import operation_visible
        operation_id = event.get("body", {}).get("operation_id")
        return bool(operation_id) and operation_visible(db, principal, {"operation_id": operation_id})
    if event["resource_type"] == "pm_maintenance_review" or event["kind"].startswith("pm.maintenance_review."):
        from .pm_maintenance_review import operation_visible
        operation_id = event.get("body", {}).get("operation_id")
        return bool(operation_id) and operation_visible(db, principal, {"operation_id": operation_id})
    if event["resource_type"] == "pm_ahp_observation" or event["kind"].startswith("pm.ahp."):
        from .pm_ahp_observations import operation_visible
        operation_id = event.get("body", {}).get("operation_id")
        return bool(operation_id) and operation_visible(db, principal, {"operation_id": operation_id})
    if event["resource_type"] == "pm_github_observation" or event["kind"].startswith("pm.github."):
        from .pm_github_observation import operation_visible
        operation_id = event.get("body", {}).get("operation_id")
        return bool(operation_id) and operation_visible(db, principal, {"operation_id": operation_id})
    if event["resource_type"] == "pm_workload" or event["kind"].startswith("pm.workload."):
        from .pm_workload import operation_visible
        operation_id = event.get("body", {}).get("operation_id")
        return bool(operation_id) and operation_visible(db, principal, {"operation_id": operation_id})
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
    if event["resource_type"] == "pm_conversation_proposal" or event["kind"].startswith("pm.conversation."):
        from .pm_conversation_proposals import operation_visible as conversation_visible
        operation_id = event.get("body", {}).get("operation_id")
        return bool(operation_id) and conversation_visible(db, principal, {"operation_id": operation_id})
    if event['resource_type'] == 'pm_overview_assessment' or event['kind'] == 'pm.overview.assess':
        from .pm_overview_assessments import operation_visible
        identity = event.get('body', {}).get('operation_id')
        return bool(identity) and operation_visible(db, principal, {'operation_id': identity})
    if event['resource_type'] == 'pm_continuation' or event['kind'] == 'pm.continuation.prepare':
        from .pm_continuation import operation_visible
        identity = event.get('body', {}).get('operation_id')
        return bool(identity) and operation_visible(db, principal, {'operation_id': identity})
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
    if event['kind'] in {'pm.sprint.close', 'pm.sprint.cancel', 'pm.evidence.capture'}:
        from .pm_quality_projections import operation_visible
        operation_id = event.get('body', {}).get('operation_id')
        return bool(operation_id) and operation_visible(db, principal, {'operation_id': operation_id})
    if event["resource_type"] == "pm_snapshot" or event["kind"].startswith("pm.snapshot."):
        # A maintenance report emits the ordinary Snapshot event in the same
        # transaction. Its metadata inherits the original preparing operation.
        if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='pm_document_snapshots'").fetchone():
            row = db.execute('''SELECT o.operation_id,o.action,o.actor,o.target,o.result
                FROM pm_document_snapshots s JOIN operations o ON o.operation_id=s.operation_id
                WHERE s.snapshot_id=?''', (event.get('body', {}).get('snapshot_id'),)).fetchone()
            if row:
                return visible(db, principal, {**dict(row), 'target': json.loads(row['target']),
                    'result': json.loads(row['result']) if row['result'] else None})
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
