"""Personal, versioned work-item reading markers in the central journal.

Reading is explicit and independent of event acknowledgments, pending requests and
completion approval. No BAT/Git calls or work-item edits occur here.
"""
from __future__ import annotations

import time

from . import dashboard_sync
from .operations import ActionDef, OpContext, OperationError


def schema(journal):
    with journal.tx():
        journal.db.execute("""CREATE TABLE IF NOT EXISTS work_item_reads (
            principal_id TEXT NOT NULL, work_item_id TEXT NOT NULL REFERENCES work_items(work_item_id),
            read_version INTEGER NOT NULL CHECK(read_version > 0), read_at REAL NOT NULL,
            PRIMARY KEY(principal_id, work_item_id))""")


def reading(db, principal_id, item):
    row = db.execute("SELECT read_version,read_at FROM work_item_reads WHERE principal_id=? AND work_item_id=?",
                     (principal_id, item["work_item_id"])).fetchone()
    version = row["read_version"] if row else 0
    return {"read_version": version, "current_version": item["version"],
            "unread": version < item["version"], "read_at": row["read_at"] if row else None}


def _check(ops, target, params, pre):
    from .work_items import WORK_ITEM_ID
    wid = target.get("work_item_id")
    if set(target) != {"work_item_id"} or not isinstance(wid, str) or not WORK_ITEM_ID.fullmatch(wid):
        raise OperationError("INVALID_TARGET", "target must name an exact work_item_id", 422)
    if params:
        raise OperationError("INVALID_PARAMS", "reading markers accept no params", 422)
    version = pre.get("expected_version")
    if set(pre) != {"expected_version"} or type(version) is not int or version < 1:
        raise OperationError("PRECONDITION_REQUIRED", "expected_version must be the work-item version you read", 422)
    item = ops.db.execute("SELECT version FROM work_items WHERE work_item_id=?", (wid,)).fetchone()
    if item is None:
        raise OperationError("WORK_ITEM_NOT_FOUND", "no such work item", 404)
    if version > item["version"]:
        raise OperationError("VERSION_CONFLICT", "the displayed version is ahead of the current work item", 409)
    return wid, version


def _admit(ops, principal, target, params, pre):
    _check(ops, target, params, pre)
    return dashboard_sync.identity(ops.journal, principal)


def _authorize_existing(ops, principal, op, _verb):
    saved = (op.get("external_refs") or {}).get("admission_binding") or {}
    if {k: v for k, v in saved.items() if k != "controller_authority"} != dashboard_sync.identity(ops.journal, principal):
        raise OperationError("FORBIDDEN", "reading markers belong to the original effective principal", 403)


async def _run(ctx: OpContext):
    def mark():
        wid, version = _check(ctx.service, ctx.target, ctx.params, ctx.preconditions)
        binding = ctx.admission_binding or {}
        metadata = ctx.service.db.execute("SELECT server_id FROM api_sync_metadata WHERE singleton=1").fetchone()
        if not binding.get("principal_id") or not metadata or binding.get("server_id") != metadata[0]:
            raise OperationError("READ_IDENTITY_CHANGED", "the original journal identity is unavailable", 409)
        principal_id = binding["principal_id"]
        before = reading(ctx.service.db, principal_id, {"work_item_id": wid, "version": version})
        if version > before["read_version"]:
            ctx.service.db.execute("""INSERT INTO work_item_reads VALUES(?,?,?,?)
                ON CONFLICT(principal_id,work_item_id) DO UPDATE SET
                    read_version=excluded.read_version,read_at=excluded.read_at""",
                (principal_id, wid, version, time.time()))
            ctx.service.journal.api_event("work_item", wid, "work_item.read",
                {"version": version, "operation_id": ctx.operation_id}, actor=ctx.actor)
        # This receipt names the version marked, never claims the current item is now read.
        return {"work_item_id": wid, "read_version": max(version, before["read_version"])}
    return ctx.effect("work_item_read", mark, request=ctx.preconditions)


ACTIONS = [ActionDef("work_item.read", "observe", "Mark the displayed work-item version read for yourself",
                     _run, _admit, target_keys=("work_item_id",), authorize_existing=_authorize_existing)]
