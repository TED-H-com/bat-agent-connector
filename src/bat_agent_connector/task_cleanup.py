"""Coordinator authority for reviewed terminal task cleanup; no second scheduler."""
from __future__ import annotations

import asyncio
import contextvars
import hashlib
import json
import posixpath
import re
import weakref
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass, field

from . import controller_authority, registry, task_control
from .api_auth import Principal
from .errors import ResourceReadOnly
from .operations import OperationError, _canonical

_HELD = contextvars.ContextVar("task_cleanup_locks", default=frozenset())
TERMINAL = {"done", "failed"}
SYSTEM = Principal("task-service.cleanup", frozenset({"observe", "cleanup"}))


def command_unresolved(command, task=None):
    """Task command statuses are not operation statuses; ACK is distinct from completion."""
    if command["status"] in {"settled", "rejected", "cancelled"}:
        return False
    # Accepted send is a proven dispatch; TaskCoordinator deliberately leaves it accepted.
    # Terminal ownership plus the separate live-idle/content checks permits reclaim, not delivery.
    return not (task and task["state"] in TERMINAL and command["status"] == "accepted"
                and command["kind"] == "send" and command.get("marker"))


def owners(ops, item, entries=None):
    """Enumerate every durable owner, including historical and shared warm successors."""
    entries = registry.list_entries() if entries is None else entries
    host, path = item["host"], item.get("content_path") or item.get("path")
    related = [e for e in entries if e.get("host") == host and (
        e.get("session_id") == item.get("session_id") or path and
        (e.get("worktree_path") or e.get("cwd")) == path)]
    sids = {e["session_id"] for e in related}
    if item.get("session_id"):
        sids.add(item["session_id"])
    ids = {e["task_id"] for e in related if e.get("task_id")}
    original = set(item.get("original_ids", []))
    tasks = {r["task_id"]: dict(r) for r in ops.db.execute("SELECT * FROM tasks WHERE host=?", (host,))}
    for tid, task in tasks.items():
        if (tid in original or task.get("session_id") in sids or task.get("reviewer_session_id") in sids
                or path and task.get("external_worktree_path") == path):
            ids.add(tid)
    for table in ("commands", "branches"):
        for row in ops.db.execute(f"SELECT task_id,session_id FROM {table}"):  # noqa: S608 - fixed tables
            if row["task_id"] in tasks and row["session_id"] in sids:
                ids.add(row["task_id"])
    return sorted(ids), related, tasks


def verdict(coordinator, item):
    ops = coordinator.operations
    ids, entries, tasks = owners(ops, item)
    reasons = []
    def refuse(code, **evidence):
        reasons.append({"code": code, **evidence})
    if not coordinator.journal.owner_valid() or not ids:
        refuse("TASK_OWNED", detail="task_owner_unavailable")
    if item["kind"] not in {"session", "worktree"}:
        refuse("RESOURCE_KIND_UNSUPPORTED")
    if not item.get("proven"):
        refuse("TASK_OWNED", detail="creation_unproven")
    bound = []
    for tid in ids:
        task = tasks.get(tid)
        if task is None:
            refuse("TASK_OWNED", task_id=tid, detail="owner_missing")
            continue
        try:
            task_control.require_authority(task)
        except ResourceReadOnly as exc:
            refuse("CONTROLLER_MISMATCH", task_id=tid, detail=getattr(exc, "code", "authority_unavailable"))
        if task["state"] not in TERMINAL:
            refuse("TASK_OWNED", task_id=tid, state=task["state"], paused=bool(task["paused"]))
        commands = [dict(r) for r in ops.db.execute("SELECT * FROM commands WHERE task_id=? ORDER BY command_id", (tid,))]
        for cmd in commands:
            if command_unresolved(cmd, task):
                refuse("COMMAND_UNRESOLVED", task_id=tid, command_id=cmd["command_id"], status=cmd["status"])
        if tid not in _HELD.get() and coordinator._task_locks.get(tid, asyncio.Lock()).locked():
            refuse("ACTIVE_EXECUTION", task_id=tid)
        bound.append({k: task.get(k) for k in ("task_id", "host", "control_version", "state", "paused",
            "session_id", "reviewer_session_id", "external_worktree_path", "external_branch", "verification_commit",
            "controller_authority")}
            | {"commands": [{k: c[k] for k in ("command_id", "kind", "session_id", "status", "marker")}
                             | {"payload_sha256": hashlib.sha256(c["payload"].encode()).hexdigest()}
                             for c in commands]})
    bindings = []
    for entry in sorted(entries, key=lambda e: e["session_id"]):
        if (entry.get("status") in {"starting", "uncertain"} or entry.get("handoff_status") == "pending"):
            refuse("COMMAND_UNRESOLVED", session_id=entry["session_id"])
        writer = coordinator._writers.get((item["host"], entry["session_id"]))
        if writer and writer.locked() and not set(ids) <= _HELD.get():
            refuse("ACTIVE_WRITER", session_id=entry["session_id"])
        bindings.append({k: entry.get(k) for k in ("host", "session_id", "created_at", "task_id", "role",
            "cwd", "worktree_path", "branch", "failover_of", "warm_from_task_id", "reuse_worktree_from")})
    if item.get("flavor") == "task":
        tid = item["creation_evidence"]["intent"]
        try:
            # Fixed identity check without the cleanup guard (this is a pure read verdict).
            suffix = tid.replace("-", "")[:12]
            if (not re.fullmatch(r"[0-9a-f]{12}", suffix)
                    or item["path"] != posixpath.join(item["repository"], ".bat-worktrees", "batc-task-" + suffix)
                    or item["branch"] != "batc/task-" + suffix):
                raise ValueError()
        except (KeyError, ValueError):
            refuse("BINDING_MISMATCH")
        task = tasks.get(tid)
        head = item.get("observation", {}).get("head") or item.get("retained_proof", {}).get("sha")
        if task and task["state"] == "done" and head and head != task.get("verification_commit"):
            refuse("BINDING_MISMATCH", detail="verified_commit_changed")
    return {"eligible": not reasons, "task_ids": ids, "reasons": reasons,
            "binding": {"resource_id": item["resource_id"], "generation": item["generation"], "host": item["host"],
                        "path": item.get("path"), "branch": item.get("branch"), "tasks": bound, "sessions": bindings}}


def check(ops, item):
    coordinator = ops.context.get("coordinator")
    accepted = item.get("task_cleanup")
    if not coordinator or not accepted or not accepted.get("eligible"):
        raise OperationError("TASK_OWNED", "TaskCoordinator has not authorized this resource", 409)
    current = coordinator.cleanup_verdict(item)
    if not current["eligible"] or current["binding"] != accepted["binding"]:
        raise OperationError("PREVIEW_STALE", "task ownership, version, command or resource changed", 409)
    return coordinator


@dataclass(frozen=True, init=False, eq=False)
class Authority:
    """Not constructible or serializable by a caller; issued under original coordinator locks."""
    _issuer: object = field(repr=False)
    _ctx: object = field(repr=False)
    _item: dict = field(repr=False)

    def __init__(self, *args, **kwargs):
        raise TypeError("only TaskCoordinator can issue cleanup authority")

    async def before_frame(self, fleet, host, sid):
        from . import resource_policy, service
        self.check(fleet, host, sid)
        client = fleet.client(host)
        meta = await client.guard_read("claude:get-session-meta", {"sessionId": sid})
        if (not isinstance(meta, dict) or meta.get("cwd") != self._item["path"]
                or meta.get("isStreaming") is True):
            raise OperationError("ACTIVE_WRITER", "task runtime idle identity is not proven", 409)
        state = await client.guard_read("claude:get-session-state", {"sessionId": sid})
        if (not isinstance(state, dict) or state.get("isStreaming") is True
                or not (meta.get("isStreaming") is False or state.get("isStreaming") is False)):
            raise OperationError("ACTIVE_WRITER", "task runtime state is unavailable or streaming", 409)
        if any(state.get(k) for k in service.SESSION_WAITING_FIELDS):
            raise OperationError("SESSION_WAITING", "task runtime has pending input", 409)
        self.check(fleet, host, sid)
        hc = fleet.config.host(host)
        resource_policy.check_cleanup_worktree(hc, self._item.get("repository") or self._item["path"],
                                              None, (self._item.get("registry") or {}).get("branch") or "batc/session")

    def check(self, fleet, host, sid):
        from . import cleanup
        issuer = self._issuer
        if (issuer is not getattr(fleet, "task_coordinator", None)
                or self not in getattr(issuer, "_cleanup_authorities", ()) or self._item["host"] != host
                or self._item.get("session_id") != sid or cleanup._OWNER.get() != self._ctx.operation_id):
            raise OperationError("TASK_OWNED", "invalid coordinator cleanup authority", 409)
        check(self._ctx.service, self._item)
        saved = cleanup._registry_document().get("cleanup_guards", {}).get(self._item["resource_id"], {})
        if saved.get("operation_id") != self._ctx.operation_id or saved.get("status") != "reserved":
            raise OperationError("TASK_OWNED", "cleanup reservation is unavailable", 409)


@asynccontextmanager
async def authority(coordinator, ctx, item):
    ids = item["task_cleanup"]["task_ids"]
    async with AsyncExitStack() as stack:
        for tid in sorted(ids):
            await stack.enter_async_context(coordinator._task_locks.setdefault(tid, asyncio.Lock()))
        sids = sorted({s["session_id"] for s in item["task_cleanup"]["binding"]["sessions"]})
        for sid in sids:
            await stack.enter_async_context(coordinator._lock(item["host"], sid))
        held = _HELD.set(frozenset(ids))
        obj = object.__new__(Authority)
        for key, value in (("_issuer", coordinator), ("_ctx", ctx), ("_item", item)):
            object.__setattr__(obj, key, value)
        if not hasattr(coordinator, "_cleanup_authorities"):
            coordinator._cleanup_authorities = weakref.WeakSet()
        coordinator._cleanup_authorities.add(obj)
        try:
            yield obj
        finally:
            coordinator._cleanup_authorities.discard(obj)
            _HELD.reset(held)


def finalize(ctx, item, after):
    """Receipt-only pointer CAS; successful remote effects never overwrite a successor."""
    if item.get("flavor") != "task" or not after.get("removed"):
        return
    tid = item["creation_evidence"]["intent"]
    task = ctx.service.journal.get(tid)
    accepted = next((t for t in item.get("task_cleanup", {}).get("binding", {}).get("tasks", []) if t["task_id"] == tid), None)
    if not accepted or any(task.get(k) != accepted.get(k) for k in
                           ("control_version", "state", "external_worktree_path", "external_branch")):
        return  # receipt remains searchable; a changed local incarnation is not ours to project onto.
    proof = {"path": item["path"], "branch": item["branch"], "retained_ref": after["retained_ref"],
             "commit": after["head"], "mode": "already_removed" if after.get("already_absent") else "removed",
             "resource_id": item["resource_id"],
             "operation_id": ctx.operation_id}
    ctx.service.journal.complete_external_cleanup(tid, proof)


async def automatic(daemon, task):
    """The existing daemon tick submits one fixed carrier operation; it never runs a second remover."""
    with controller_authority.use(task_control.authority(task), daemon.journal):
        return await _automatic_bound(daemon, task)


async def _automatic_bound(daemon, task):
    from . import cleanup
    ops = daemon.ops
    key = "task-cleanup:" + task["task_id"] + ":" + cleanup._hash(
        [task["host"], task["external_worktree_path"], task["external_branch"]])[:24]
    prior = ops.db.execute("SELECT operation_id FROM operations WHERE actor=? AND idem_key=?",
                           (SYSTEM.actor, key)).fetchone()
    if prior:
        return ops.get(prior[0])
    task_control.require_authority(task)
    doc = await cleanup.preview(ops, SYSTEM, {"kind": "task", "task_id": task["task_id"]},
                                _automatic=True)
    if not doc["ready"]:
        return None
    from .observation import event_context
    with ops.journal.tx():
        operation, created = ops.create(SYSTEM, **cleanup.apply_request(doc, key), entry="task_lifecycle")
        ops._merge_refs(operation["operation_id"], {"task_id": task["task_id"]})
        if created:
            with event_context(actor=SYSTEM.actor, entry_point="task_lifecycle", operation_id=operation["operation_id"],
                               actor_basis="task_lifecycle", actor_evidence={"source": "coordinator_terminal_policy"}):
                ops.journal._event(task["task_id"], "cleanup_accepted", {"operation_id": operation["operation_id"],
                    "resource_ids": [i["resource_id"] for i in doc["items"] if i["decision"] == "reclaim"],
                    "origin": "task_lifecycle"})
    return ops.get(operation["operation_id"])


def historical(ops):
    """Only the original terminal-cleanup event proves an old external carrier was removed."""
    from . import cleanup
    from .resource_ids import worktree_id
    records = []
    for row in ops.db.execute("SELECT * FROM events WHERE kind='external_worktree_retained' ORDER BY event_id"):
        task = ops.db.execute("SELECT * FROM tasks WHERE task_id=?", (row["task_id"],)).fetchone()
        if not task:
            continue
        try:
            proof = json.loads(row["body"])
            path, branch, commit = proof["path"], proof["branch"], proof["commit"]
            root = posixpath.dirname(posixpath.dirname(path))
            suffix = task["task_id"].replace("-", "")[:12]
            rid = worktree_id(task["host"], "task", task["task_id"], "external_worktree")
            allowed_refs = {"refs/batc/tasks/" + suffix, "refs/batc/retained/" + rid + "/" + commit}
            if (path != root + "/.bat-worktrees/batc-task-" + suffix or branch != "batc/task-" + suffix
                    or not re.fullmatch(r"[0-9a-f]{40}", commit) or proof["retained_ref"] not in allowed_refs
                    or proof.get("mode") not in {"removed", "already_removed"}):
                continue
        except (ValueError, TypeError, KeyError):
            continue
        item = cleanup._resource(task["host"], "worktree", task["task_id"], "external_worktree",
            path=path, repository=root, branch=branch, flavor="task", base=task["base_commit"],
            task_owned=True, proven=True, historical_cleanup=True, original_ids=[task["task_id"]])
        item.update(resource_id=rid, generation=cleanup._hash(["worktree", task["host"], "task", task["task_id"], "external_worktree"]))
        item["creation_evidence"].update(intent_type="task", task_event_id=row["event_id"])
        records.append((item, proof, dict(row)))
    return records


def backfill(ops):
    """Idempotent historical evidence projection, without inventing a reviewed operation or live proof."""
    from . import cleanup
    for item, proof, event in historical(ops):
        rid = item["resource_id"]
        if ops.db.execute("SELECT 1 FROM resource_tombstones WHERE resource_id=?", (rid,)).fetchone():
            continue
        ref, commit = proof["retained_ref"], proof["commit"]
        ret_id = "ret_" + cleanup._hash([rid, commit, item["repository"]])[:32]
        origin = "task-event:" + str(event["event_id"])
        doc = {**item, "operation_id": None, "actor": None, "reason": "historical_task_cleanup",
               "accepted_authorization": None, "last_observation": None, "after": proof,
               "retained_ids": [ret_id], "receipt": {"task_event_id": event["event_id"]},
               "cleaned_at": event["created_at"], "pull_requests": [], "runtime_restored": False}
        fact = {"host": item["host"], "repository": item["repository"], "ref": ref, "commit": commit, "tree": None}
        with ops.journal.tx():
            ops.db.execute("INSERT OR IGNORE INTO cleanup_retained VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (ret_id, rid, origin, "historical", commit, item["host"], item["repository"], ref, commit, "",
                 cleanup._hash(fact), _canonical(item["creation_evidence"]), event["created_at"]))
            ops.db.execute("INSERT OR IGNORE INTO resource_tombstones VALUES(?,?,?,?,?,?)",
                (rid, item["generation"], item["host"], item["kind"], _canonical(doc), event["created_at"]))
            for kind, external in (("original", event["task_id"]), ("path", item["path"]), ("ref", item["branch"])):
                ops.db.execute("INSERT OR IGNORE INTO cleanup_aliases VALUES(?,?,?,?)", (kind, external, item["host"], rid))
            ops.journal.api_event("cleanup", rid, "cleanup.historical", {"task_event_id": event["event_id"], "retained_ids": [ret_id]})


def retire_absent(ctx, item):
    """Local capacity CAS after an absent-runtime receipt and a confirmed carrier outcome."""
    import time

    from . import cleanup
    failure = {"capacity_released": False, "capacity_reason": "task_binding_changed"}
    with registry._locked(registry.registry_path()):
        doc = cleanup._registry_document()
        saved = item["task_cleanup"]["binding"]
        coordinator = ctx.service.context["coordinator"]
        current = coordinator.cleanup_verdict(item)
        # Our own carrier finalization clears pointers; it does not change task incarnation.
        if len(current["binding"]["tasks"]) != len(saved["tasks"]):
            return failure
        for now, before in zip(current["binding"]["tasks"], saved["tasks"], strict=True):
            if now.get("external_worktree_path") is None and before.get("external_worktree_path"):
                carrier = ctx.service.db.execute("SELECT 1 FROM cleanup_receipts WHERE operation_id=? AND resource_id=? "
                    "AND status='succeeded'", (ctx.operation_id, item.get("worktree_id"))).fetchone()
                if carrier:
                    now["external_worktree_path"] = before["external_worktree_path"]
                    now["external_branch"] = before["external_branch"]
        if not current["eligible"] or current["binding"] != saved:
            return failure
        entry = next((e for e in doc.get("sessions", []) if e.get("host") == item["host"]
                      and e.get("session_id") == item["session_id"]), None)
        if not entry:
            return failure
        if (entry.get("retirement") or {}).get("operation_id") == ctx.operation_id:
            return {"capacity_released": True, "registry_status": entry["status"]}
        if entry.get("status") != "active":
            return {"capacity_released": False, "registry_status": entry.get("status"), "capacity_reason": "not_counted"}
        registry.refuse_start_claim(registry.registry_path(), item["host"], item["session_id"])
        entry.update(status="absent_at_cleanup", retired_at=time.time(), updated_at=time.time(), retirement={
            "actor": ctx.actor, "reason": "task cleanup observed absent runtime and completed carrier",
            "operation_id": ctx.operation_id, "carrier_resource_id": item.get("worktree_id")})
        registry._write_document(registry.registry_path(), doc)
        return {"capacity_released": True, "registry_status": "absent_at_cleanup"}


def authorize_existing(ops, principal, operation, verb):
    """Task cleanup replay/control keeps current cleanup scope; automatic origin cannot be minted by an actor label."""
    params = operation.get("params") or {}
    token = params.get("preview_token", "")
    try:
        import base64
        raw = token.split(".")[1]
        payload = json.loads(base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)))
    except (ValueError, IndexError, TypeError):
        return  # Existing malformed old operations still cannot execute the cleanup handler.
    if payload.get("target", {}).get("kind") != "task":
        return
    if not principal.allows("cleanup"):
        raise OperationError("FORBIDDEN", "task cleanup replay/control requires current cleanup scope", 403)
    if payload.get("origin") == "task_lifecycle" and verb == "replay" and principal is not SYSTEM:
        raise OperationError("FORBIDDEN", "automatic cleanup submission is coordinator-only", 403)


async def finalize_absent(ctx, item):
    """Read back a pre-upgrade removed carrier; no Git/BAT mutation or fabricated old operation."""
    from . import cleanup
    proof = item["retained_proof"]
    await cleanup._phase_consumers(ctx, item)
    result = await cleanup._host_call(ctx.service, item["host"], {"phase": "verify.retained",
        "repository": item["repository"], "roots": list(cleanup._host_config(ctx.service, item["host"]).managed_roots),
        "retained": [{"ref": proof["ref"], "commit_sha": proof["sha"]}]})
    if not result or not result[0].get("available"):
        raise OperationError("RETAINED_CONTENT_MISSING", "the original task retained ref is unavailable", 409)
    check(ctx.service, item)
    sha, ref, tree = proof["sha"], proof["ref"], result[0]["tree_sha"]
    rid = item["resource_id"]
    ret_id = "ret_" + cleanup._hash([rid, sha, item["repository"]])[:32]
    fact = {"host": item["host"], "repository": item["repository"], "ref": ref, "commit": sha, "tree": tree}
    with ctx.service.journal.tx():
        ctx.service.db.execute("INSERT OR IGNORE INTO cleanup_retained VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (ret_id, rid, ctx.operation_id, "retained.readback", sha, item["host"], item["repository"], ref, sha, tree,
             cleanup._hash(fact), _canonical(item["creation_evidence"]), ctx.op["created_at"]))
    cleanup._finalize(ctx, item, {"removed": True, "already_absent": True, "retained_ref": ref, "head": sha})
