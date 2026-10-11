"""Personal conversation receipts and positions over observed BAT message identities.

Only IDs and content digests are retained, never a second transcript. A receipt is
for one displayed revision; it cannot mark skipped messages or future edits read.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from collections import Counter

from . import dashboard_sync
from .operations import ActionDef, OpContext, OperationError
from .summarize import is_tool, summarize_message


def schema(journal):
    with journal.tx():
        journal.db.execute("""CREATE TABLE IF NOT EXISTS session_message_index (
            host TEXT NOT NULL, session_id TEXT NOT NULL, message_id TEXT NOT NULL,
            revision TEXT NOT NULL, page_offset INTEGER NOT NULL, active INTEGER NOT NULL,
            PRIMARY KEY(host,session_id,message_id))""")
        journal.db.execute("""CREATE TABLE IF NOT EXISTS session_message_versions (
            host TEXT NOT NULL, session_id TEXT NOT NULL, message_id TEXT NOT NULL,
            revision TEXT NOT NULL, PRIMARY KEY(host,session_id,message_id,revision))""")
        journal.db.execute("""CREATE TABLE IF NOT EXISTS session_reading_status (
            host TEXT NOT NULL, session_id TEXT NOT NULL, complete INTEGER NOT NULL,
            observed_at REAL NOT NULL, generation INTEGER NOT NULL, PRIMARY KEY(host,session_id))""")
        journal.db.execute("""CREATE TABLE IF NOT EXISTS session_reading_clock (
            singleton INTEGER PRIMARY KEY CHECK(singleton=1), generation INTEGER NOT NULL)""")
        journal.db.execute("INSERT OR IGNORE INTO session_reading_clock VALUES(1,0)")
        journal.db.execute("""CREATE TABLE IF NOT EXISTS session_message_reads (
            principal_id TEXT NOT NULL, host TEXT NOT NULL, session_id TEXT NOT NULL,
            message_id TEXT NOT NULL, revision TEXT NOT NULL, read_at REAL NOT NULL,
            PRIMARY KEY(principal_id,host,session_id,message_id,revision))""")
        journal.db.execute("""CREATE TABLE IF NOT EXISTS session_reading_positions (
            principal_id TEXT NOT NULL, host TEXT NOT NULL, session_id TEXT NOT NULL,
            message_id TEXT NOT NULL, revision TEXT NOT NULL, offset INTEGER NOT NULL,
            version INTEGER NOT NULL, saved_at REAL NOT NULL,
            PRIMARY KEY(principal_id,host,session_id))""")


def index_messages(messages, *, complete, include_tools=False):
    """Called with source messages, before the response's text/size truncation."""
    visible = [summary for message in messages
               if (summary := summarize_message(message, include_tools=False, max_chars=2**31)) is not None]
    counts = Counter(summary.get("id") for summary in visible if isinstance(summary.get("id"), str))
    # Tool details do not enter human read counts. They do occupy positions in
    # an include_tools page, so anchors need offsets for the requested view.
    offsets = {}
    offset = 0
    for message in reversed(messages):
        if is_tool(message):
            if include_tools:
                offset += 1
            continue
        if summarize_message(message, include_tools=False, max_chars=50) is not None:
            mid = message.get("id")
            if isinstance(mid, str):
                offsets[mid] = offset
            offset += 1
    index = []
    for summary in reversed(visible):
        mid = summary.get("id")
        if not isinstance(mid, str) or not mid or len(mid) > 512 or counts[mid] != 1:
            complete = False
            continue
        revision = hashlib.sha256(json.dumps(summary, sort_keys=True, ensure_ascii=False,
                                             separators=(",", ":")).encode()).hexdigest()
        index.append({"id": mid, "revision": revision, "page_offset": offsets[mid],
                      "text": summary["text"]})
    return {"messages": index, "complete": bool(complete)}


def reading(journal, principal, host, sid):
    db = journal.db
    pid = dashboard_sync.identity(journal, principal)["principal_id"]
    status = db.execute("SELECT * FROM session_reading_status WHERE host=? AND session_id=?", (host, sid)).fetchone()
    counts = db.execute("""SELECT COUNT(*) AS known,
        COALESCE(SUM(NOT EXISTS(SELECT 1 FROM session_message_reads r
            WHERE r.principal_id=? AND r.host=i.host AND r.session_id=i.session_id
            AND r.message_id=i.message_id AND r.revision=i.revision)),0) AS unread
        FROM session_message_index i WHERE i.host=? AND i.session_id=? AND i.active=1""", (pid, host, sid)).fetchone()
    position = db.execute("""SELECT p.message_id,p.revision,p.offset,p.version,p.saved_at,i.page_offset
        FROM session_reading_positions p LEFT JOIN session_message_index i
        ON i.host=p.host AND i.session_id=p.session_id AND i.message_id=p.message_id AND i.active=1
        WHERE p.principal_id=? AND p.host=? AND p.session_id=?""", (pid, host, sid)).fetchone()
    return {"unread_count": counts["unread"] if status else None, "known_count": counts["known"],
            "complete": bool(status and status["complete"]), "observed_at": status["observed_at"] if status else None,
            "position": dict(position) if position else None}


def begin_observation(journal):
    # Reserve before network I/O. A delayed older request must not roll the index
    # back after another browser has already observed a newer message revision.
    with journal.tx():
        journal.db.execute("UPDATE session_reading_clock SET generation=generation+1 WHERE singleton=1")
        return journal.db.execute("SELECT generation FROM session_reading_clock WHERE singleton=1").fetchone()[0]


def observe(journal, principal, response, *, generation=None):
    snapshot = response.pop("_reading_index", None)
    host, sid = response["host"], response["session_id"]
    pid = dashboard_sync.identity(journal, principal)["principal_id"]
    if snapshot is not None:
        generation = begin_observation(journal) if generation is None else generation
        with journal.tx():
            previous = journal.db.execute("SELECT generation FROM session_reading_status WHERE host=? AND session_id=?", (host, sid)).fetchone()
            current = not previous or generation > previous["generation"]
            if current and snapshot["complete"]:
                journal.db.execute("UPDATE session_message_index SET active=0 WHERE host=? AND session_id=?", (host, sid))
            # Offsets from a previous partial scan are no longer safe for restoration.
            elif current:
                journal.db.execute("UPDATE session_message_index SET page_offset=-1 WHERE host=? AND session_id=?", (host, sid))
            for message in snapshot["messages"]:
                values = (host, sid, message["id"], message["revision"])
                journal.db.execute("INSERT OR IGNORE INTO session_message_versions VALUES(?,?,?,?)", values)
                if not current:
                    continue
                journal.db.execute("""INSERT INTO session_message_index VALUES(?,?,?,?,?,1)
                    ON CONFLICT(host,session_id,message_id) DO UPDATE SET
                    revision=excluded.revision,page_offset=excluded.page_offset,active=1""", (*values, message["page_offset"]))
            if current:
                journal.db.execute("""INSERT INTO session_reading_status VALUES(?,?,?,?,?)
                    ON CONFLICT(host,session_id) DO UPDATE SET complete=excluded.complete,
                    observed_at=excluded.observed_at,generation=excluded.generation""",
                    (host, sid, int(snapshot["complete"]), time.time(), generation))
        indexed = {message["id"]: message for message in snapshot["messages"]}
        for message in response["messages"]:
            source = indexed.get(message.get("id"))
            if source and message.get("role") != "tool":
                marked = journal.db.execute("""SELECT 1 FROM session_message_reads
                    WHERE principal_id=? AND host=? AND session_id=? AND message_id=? AND revision=?""",
                    (pid, host, sid, source["id"], source["revision"])).fetchone()
                message["reading"] = {"revision": source["revision"], "unread": not bool(marked),
                                      "can_mark": message.get("text") == source["text"]}
    response["reading"] = reading(journal, principal, host, sid)
    if snapshot is not None and response["reading"]["position"]:
        position = response["reading"]["position"]
        source = indexed.get(position["message_id"])
        # The page offset belongs to this response's include_tools mode, even
        # if a newer concurrent observer used a different transcript filter.
        position["page_offset"] = source["page_offset"] if source else None
    return response


def access(journal, principal, row):
    """Project objective inventory into this caller's mutation availability."""
    from . import controller_authority, registry
    from .errors import ResourceReadOnly

    row = dict(row)
    if row.get("api_access") == "managed" and row.get("host") and row.get("session_id"):
        try:
            with controller_authority.use(controller_authority.binding(journal.db, principal), journal):
                controller_authority.require_entry(registry.get(row["host"], row["session_id"]))
        except ResourceReadOnly as exc:
            row.update(api_access="read_only", read_only_code=exc.code, read_only_reason=str(exc))
    return row


def decorate(journal, principal, document):
    def personal(row):
        if not row.get("host") or not row.get("session_id"):
            return row
        row = {**row, "reading": reading(journal, principal, row["host"], row["session_id"])}
        # Inventory stores objective provenance, not the last browser's access.
        # Compute control availability per caller after reading shared inventory.
        return access(journal, principal, row)

    document = dict(document)
    for key in ("sessions", "work"):
        if key in document:
            document[key] = [personal(row) for row in document[key]]
    if document.get("session"):
        document["session"] = personal(document["session"])
    return document


def _invalid(message):
    raise OperationError("INVALID_READING", message, 422)


def _validate(ops, target, params, pre, *, position=False):
    if set(target) != {"host", "session_id"} or any(
            not isinstance(target[k], str) or not 1 <= len(target[k]) <= 512 for k in target):
        _invalid("reading requires an exact host and session ID")
    host, sid = target["host"], target["session_id"]
    if not ops.db.execute("SELECT 1 FROM session_reading_status WHERE host=? AND session_id=?", (host, sid)).fetchone():
        raise OperationError("READING_UNOBSERVED", "read this exact session before saving reading state", 409)
    if position:
        if set(params) != {"message_id", "revision", "offset"} or type(params["offset"]) is not int or abs(params["offset"]) > 100000:
            _invalid("position requires a displayed message revision and bounded pixel offset")
        if set(pre) != {"expected_version"} or type(pre["expected_version"]) is not int or pre["expected_version"] < 0:
            _invalid("position requires its last observed version")
        messages = [{k: params[k] for k in ("message_id", "revision")}]
    else:
        if set(params) != {"messages"} or pre or not isinstance(params["messages"], list) or not 1 <= len(params["messages"]) <= 100:
            _invalid("reading requires 1-100 displayed message revisions")
        messages = params["messages"]
    seen = set()
    for message in messages:
        if (not isinstance(message, dict) or set(message) != {"message_id", "revision"}
                or not isinstance(message["message_id"], str) or not 1 <= len(message["message_id"]) <= 512
                or not isinstance(message["revision"], str) or not re.fullmatch(r"[0-9a-f]{64}", message["revision"])):
            _invalid("invalid displayed message revision")
        key = (message["message_id"], message["revision"])
        if key in seen:
            _invalid("duplicate displayed message revision")
        seen.add(key)
        if not ops.db.execute("""SELECT 1 FROM session_message_versions
                WHERE host=? AND session_id=? AND message_id=? AND revision=?""", (host, sid, *key)).fetchone():
            raise OperationError("READING_UNOBSERVED", "this message revision has not been observed", 409)
    return host, sid, messages


def _admit(ops, principal, target, params, pre):
    _validate(ops, target, params, pre)
    return dashboard_sync.identity(ops.journal, principal)


def _admit_position(ops, principal, target, params, pre):
    _validate(ops, target, params, pre, position=True)
    return dashboard_sync.identity(ops.journal, principal)


def _authorize_existing(ops, principal, op, _verb):
    saved = (op.get("external_refs") or {}).get("admission_binding") or {}
    if {k: v for k, v in saved.items() if k not in {"controller_authority", "pm_lineage", "pm_source_roots"}} != dashboard_sync.identity(ops.journal, principal):
        raise OperationError("FORBIDDEN", "reading belongs to the original effective principal", 403)


def _binding(ctx):
    binding = ctx.admission_binding or {}
    metadata = ctx.service.db.execute("SELECT server_id FROM api_sync_metadata WHERE singleton=1").fetchone()
    if not binding.get("principal_id") or not metadata or metadata[0] != binding.get("server_id"):
        raise OperationError("READ_IDENTITY_CHANGED", "the original journal identity is unavailable", 409)
    return binding["principal_id"]


async def _run(ctx: OpContext):
    def mark():
        host, sid, messages = _validate(ctx.service, ctx.target, ctx.params, ctx.preconditions)
        pid = _binding(ctx)
        changed = False
        for message in messages:
            changed |= bool(ctx.service.db.execute("INSERT OR IGNORE INTO session_message_reads VALUES(?,?,?,?,?,?)",
                (pid, host, sid, message["message_id"], message["revision"], time.time())).rowcount)
        if changed:
            ctx.service.journal.api_event("session", f"{host}/{sid}", "session.read",
                {"operation_id": ctx.operation_id}, actor=ctx.actor)
        return {"host": host, "session_id": sid, "messages": messages}
    return ctx.effect("session_read", mark, request=ctx.params)


async def _position(ctx: OpContext):
    def save():
        host, sid, _ = _validate(ctx.service, ctx.target, ctx.params, ctx.preconditions, position=True)
        pid = _binding(ctx)
        old = ctx.service.db.execute("""SELECT version FROM session_reading_positions
            WHERE principal_id=? AND host=? AND session_id=?""", (pid, host, sid)).fetchone()
        version = old["version"] if old else 0
        if version != ctx.preconditions["expected_version"]:
            raise OperationError("READING_POSITION_CHANGED", "another entry saved a newer reading position; reload before saving", 409)
        params = ctx.params
        ctx.service.db.execute("""INSERT INTO session_reading_positions VALUES(?,?,?,?,?,?,?,?)
            ON CONFLICT(principal_id,host,session_id) DO UPDATE SET message_id=excluded.message_id,
            revision=excluded.revision,offset=excluded.offset,version=excluded.version,saved_at=excluded.saved_at""",
            (pid, host, sid, params["message_id"], params["revision"], params["offset"], version + 1, time.time()))
        ctx.service.journal.api_event("session", f"{host}/{sid}", "session.position",
            {"operation_id": ctx.operation_id}, actor=ctx.actor)
        return {"host": host, "session_id": sid, "version": version + 1}
    return ctx.effect("session_position", save, request=ctx.params)


ACTIONS = [
    ActionDef("session.read", "observe", "Mark explicitly displayed conversation revisions read", _run, _admit,
              target_keys=("host", "session_id"), authorize_existing=_authorize_existing),
    ActionDef("session.position", "observe", "Remember a personal conversation reading position", _position, _admit_position,
              target_keys=("host", "session_id"), authorize_existing=_authorize_existing),
]
