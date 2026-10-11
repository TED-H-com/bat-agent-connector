"""Read-only parent/child result sources from explicit central relationships.

Execution acceptance, observed activity, artifact availability and delivery are
separate facts. None changes a work item's completion or grants source ownership.
"""

from __future__ import annotations

import json
import time

from . import work_items
from .operations import OperationError

MAX_SOURCES = 20


def _brief(db, work_item_id):
    item = work_items._get_item(db, work_item_id)
    return {"work_item_id": item["work_item_id"], "project_id": item["project_id"], "title": item["title"],
            "parent_id": item["parent_id"], "derived_from": item["derived_from"], "archived": item["archived"],
            "version": item["version"], "completion": work_items.completion(item)}


def _receipts(db, kind, ref):
    if kind == "operation":
        clause, args = "source_kind='execution' AND source_id=?", (ref,)
    elif kind == "checkpoint":
        clause, args = "source_kind='checkpoint' AND source_id=?", (ref,)
    elif kind == "task":
        clause, args = "source_kind='task_command' AND source_id IN (SELECT command_id FROM commands WHERE task_id=?)", (ref,)
    else:
        return [], False
    rows = db.execute("SELECT operation_id,seq,repository,pull_number,source_kind,source_id,pinned_sha,delivered_sha,"  # noqa: S608 - fixed clauses
                      "delivered_at FROM integration_receipts WHERE status='delivered' AND " + clause
                      + " ORDER BY delivered_at DESC,operation_id,seq LIMIT ?", (*args, MAX_SOURCES + 1)).fetchall()
    return [dict(row) for row in rows[:MAX_SOURCES]], len(rows) > MAX_SOURCES


def _observation(ops, target):
    host, sid = target.get("host"), target.get("session_id")
    inventory = ops.context.get("inventory")
    if not inventory or host not in ops.context["fleet"].config.hosts or not sid:
        return {"status": "unavailable", "reason": "session_observation_unavailable"}
    row = inventory.get_session(host, sid)
    if not row:
        return {"status": "unavailable", "reason": "session_observation_unavailable"}
    return {"status": "available", "streaming": row.get("streaming"), "pending": row.get("pending"),
            "observed_at": row.get("observed_at"), "gone_at": row.get("gone_at"),
            "stale": row.get("stale"), "fields_stale": row.get("fields_stale"),
            "stale_reason": row.get("stale_reason")}


def _summary(ops, work_item_id):
    db = ops.db
    item = _brief(db, work_item_id)
    rows = db.execute("""SELECT kind,ref,note,linked_at FROM work_item_links WHERE work_item_id=?
        AND removed_at IS NULL ORDER BY linked_at,link_id LIMIT ?""", (work_item_id, MAX_SOURCES + 1)).fetchall()
    links = []
    for row in rows[:MAX_SOURCES]:
        link = dict(row)
        target = work_items._link_summary(db, row["kind"], row["ref"])
        receipts, more = _receipts(db, row["kind"], row["ref"])
        link.update(target=target, delivered_to=receipts, delivery_truncated=more)
        if row["kind"] == "session":
            link["observation"] = _observation(ops, target)
        elif row["kind"] == "operation" and target.get("session"):
            link["observation"] = _observation(ops, target["session"])
        links.append(link)
    refs = db.execute("""SELECT r.artifact_id,r.revision,r.digest,r.created_at,a.digest AS stored_digest,
        a.display_name,a.size_bytes,a.media_type,a.state,a.operation_id AS source_operation_id
        FROM artifact_references r LEFT JOIN artifact_revisions a USING(artifact_id,revision)
        WHERE r.owner_kind='work_item' AND r.owner_id=? AND r.role='result' AND r.released_at IS NULL
        ORDER BY r.created_at,r.artifact_id,r.revision LIMIT ?""", (work_item_id, MAX_SOURCES + 1)).fetchall()
    artifacts = []
    for ref in refs[:MAX_SOURCES]:
        artifact = dict(ref)
        digest_matches = artifact.pop("stored_digest") == artifact["digest"]
        artifact["available"] = artifact["state"] == "ready" and digest_matches
        artifact["reason"] = None if artifact["available"] else "artifact_revision_unavailable"
        artifact["content_url"] = (f"/api/v1/artifacts/{ref['artifact_id']}/revisions/{ref['revision']}/content"
                                   if artifact["available"] else None)
        consumers = db.execute("""SELECT DISTINCT owner_kind,owner_id,role FROM artifact_references
            WHERE artifact_id=? AND revision=? AND digest=? AND released_at IS NULL
            ORDER BY owner_kind,owner_id,role LIMIT ?""",
            (ref["artifact_id"], ref["revision"], ref["digest"], MAX_SOURCES + 1)).fetchall()
        artifact["recorded_consumers"] = [dict(row) for row in consumers[:MAX_SOURCES]]
        artifact["consumers_truncated"] = len(consumers) > MAX_SOURCES
        artifact["live_consumers"] = "unknown"
        source = db.execute("SELECT document FROM artifact_capture_sources WHERE operation_id=?",
                             (ref["source_operation_id"],)).fetchone()
        # Source capture documents can contain workspace paths; return only fixed identities.
        source = json.loads(source[0]) if source else {"kind": "upload"}
        artifact["source"] = {key: source.get(key) for key in
                              ("kind", "host", "session_id", "task_id", "command_id", "execution_operation_id")
                              if isinstance(source.get(key), str)}
        artifacts.append(artifact)
    return {**item, "links": links, "links_truncated": len(rows) > MAX_SOURCES,
            "result_artifacts": artifacts, "artifacts_truncated": len(refs) > MAX_SOURCES}


def read(ops, principal, work_item_id, *, limit=50, after=None):
    if not principal.allows("observe"):
        raise OperationError("FORBIDDEN", "result sources need observe", 403)
    if not isinstance(work_item_id, str) or not work_items.WORK_ITEM_ID.fullmatch(work_item_id):
        raise OperationError("WORK_ITEM_NOT_FOUND", "no such work item", 404)
    from .pm_source_lineage import read_response, require
    require(ops.db, principal, "work_item", work_item_id)
    if type(limit) is not int or not 1 <= limit <= 100:
        raise OperationError("INVALID_PARAMS", "limit must be 1-100", 422)
    if after is not None and (not isinstance(after, str) or not work_items.WORK_ITEM_ID.fullmatch(after)):
        raise OperationError("INVALID_CURSOR", "after must be a returned child cursor", 422)
    item = _summary(ops, work_item_id)
    children = ops.db.execute("""SELECT work_item_id FROM work_items WHERE parent_id=? AND project_id=?
        AND archived_at IS NULL AND (? IS NULL OR work_item_id>?) ORDER BY work_item_id LIMIT ?""",
        (work_item_id, item["project_id"], after, after, limit + 1)).fetchall()
    return read_response(ops.db, principal, {"version": 1, "source": "central_journal", "read_at": time.time(), "item": item,
            "parent": _brief(ops.db, item["parent_id"]) if item["parent_id"] else None,
            "derived_from": _brief(ops.db, item["derived_from"]) if item["derived_from"] else None,
            "children": [_summary(ops, row[0]) for row in children[:limit]],
            "children_next_cursor": children[limit - 1][0] if len(children) > limit else None,
            "completion_rule": "existing_work_item_completion"})
