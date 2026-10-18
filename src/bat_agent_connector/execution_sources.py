"""Read-only delivery sources from accepted central executions, never branch-name ownership."""

from __future__ import annotations

import json

from . import artifact_managed, registry, resource_policy
from .operations import OperationError

KINDS = {"execution", "task_command"}
IDENTITY = ("created_at", "cwd", "worktree_path", "branch", "origin_cwd", "origin_root", "task_id", "role",
            "shares_worktree_with", "lead_session_id", "start_operation_id", "repository_binding", "published_sha")


def reference(ops, kind, source_id):
    if kind == "execution":
        op = ops.get(source_id, steps=False)
        refs, result = op.get("external_refs") or {}, op.get("result") or {}
        target = refs.get("resolved_target") or op["target"]
        host = result.get("host") or target.get("host") or refs.get("host")
        sid = result.get("session_id") or target.get("session_id") or refs.get("session_id")
        return host, sid, {"execution_operation_id": source_id}, op
    command = ops.db.execute("SELECT * FROM commands WHERE command_id=?", (source_id,)).fetchone()
    task = ops.db.execute("SELECT * FROM tasks WHERE task_id=?", (command["task_id"],)).fetchone() if command else None
    if not command or not task:
        raise OperationError("SOURCE_NOT_FOUND", "accepted task command was not found", 404)
    return task["host"], command["session_id"], {"task_id": task["task_id"], "command_id": source_id}, {
        **dict(task), "command_created_at": command["created_at"]}


def creation_base(ops, entry, origin, kind):
    if entry.get("published_sha"):
        return entry["published_sha"]
    if kind == "task_command" and origin.get("base_commit"):
        return origin["base_commit"]
    if entry.get("checkpoint_id"):
        row = ops.db.execute("SELECT commit_sha FROM checkpoints WHERE checkpoint_id=?", (entry["checkpoint_id"],)).fetchone()
        if row:
            return row[0]
    oid = entry.get("start_operation_id") or (origin["operation_id"] if kind == "execution" else None)
    row = ops.db.execute("""SELECT s.response FROM operation_steps s JOIN operations o USING(operation_id)
        WHERE s.operation_id=? AND o.action='session.start' AND s.name='source.resolve' AND s.status='succeeded'""", (oid,)).fetchone()
    return json.loads(row[0]).get("base_commit") if row else None


def resolve(ops, kind, source_id, *, _entries=None):
    host, sid, selector, origin = reference(ops, kind, source_id)
    if not host or not sid or host not in ops.context["fleet"].config.hosts:
        raise OperationError("SOURCE_UNAVAILABLE", "execution has no configured session binding", 409)
    entry = registry.get(host, sid) if _entries is None else next((row for row in _entries if row.get("session_id") == sid), None)
    if not entry:
        raise OperationError("SOURCE_LINEAGE_UNPROVEN", "execution has no current creation record", 409)
    try:
        proof = artifact_managed.lineage(ops, host, sid, entry, selector)
    except OperationError as error:
        raise OperationError("SOURCE_LINEAGE_UNPROVEN", error.message, 409) from None
    cls = resource_policy.classify(ops.context["fleet"].config.host(host), sid, terminal=None,
                                   entries=registry.list_entries(host) if _entries is None else _entries)
    branch = entry.get("branch")
    if (cls.provenance != resource_policy.MANAGED or not cls.writable or not cls.workdir
            or not isinstance(branch, str) or not resource_policy.HEAD_REF.fullmatch(branch)
            or cls.workdir != resource_policy.norm(entry.get("worktree_path"))):
        raise OperationError("SOURCE_LINEAGE_UNPROVEN", "execution needs its own recorded managed Git worktree", 409)
    if kind == "task_command":
        if origin.get("external_worktree_path") and (origin["external_worktree_path"] != cls.workdir
                                                     or origin.get("external_branch") != branch):
            raise OperationError("SOURCE_LINEAGE_UNPROVEN", "task carrier no longer matches its creation record", 409)
    observed = ops.db.execute("SELECT body FROM observation_resources WHERE resource_type='session' AND resource_id=?",
                              (f"{host}/{sid}",)).fetchone()
    wid = json.loads(observed[0]).get("worktree_id") if observed else None
    return {"host": host, "session_id": sid, "location": cls.workdir, "worktree": cls.workdir, "worktree_id": wid,
            "location_class": cls.isolation, "ref": branch, "pin": None, "start": creation_base(ops, entry, origin, kind),
            "label": branch, "workspace": entry.get("workspace_id") or entry.get("workspace"),
            "lineage": proof, "registry_identity": {key: entry.get(key) for key in IDENTITY}}


def revalidate(ops, sources):
    for source in sources:
        if source["kind"] not in KINDS:
            continue
        fresh = resolve(ops, source["kind"], source["id"])
        if any(fresh.get(key) != source.get(key) for key in
               ("host", "session_id", "location", "ref", "start", "worktree_id", "lineage", "registry_identity")):
            raise OperationError("SOURCE_CHANGED", "execution source binding changed since preview; review again", 409)


def project_ids(ops, kind, source_id, host, sid, origin):
    ids = set()
    if kind == "execution" and origin["action"] == "repository.continue":
        pid = (origin.get("params") or {}).get("project_id")
        if pid:
            ids.add(pid)
    if kind == "task_command":
        ids.update(row[0] for row in ops.db.execute("SELECT project_id FROM projects WHERE task_project=?",
                                                   (origin["project"],)))
    refs = [("operation" if kind == "execution" else "task", source_id if kind == "execution" else origin["task_id"])]
    if host and sid:
        refs.append(("session", f"{host}/{sid}"))
    for link_kind, ref in refs:
        ids.update(row[0] for row in ops.db.execute("""SELECT w.project_id FROM work_item_links l
            JOIN work_items w USING(work_item_id) WHERE l.kind=? AND l.ref=? AND l.removed_at IS NULL""", (link_kind, ref)))
    return sorted(ids)


def candidates(ops, host, limit=50):
    """Bounded accepted starts and latest accepted task commands, including an explanation when unavailable."""
    rows = ops.db.execute("""SELECT operation_id FROM operations WHERE status='succeeded'
        AND action IN ('session.start','repository.continue','session.send')
        AND COALESCE(json_extract(result,'$.host'),json_extract(target,'$.host'),json_extract(external_refs,'$.host'))=?
        ORDER BY created_at DESC,operation_id DESC LIMIT ?""", (host, limit)).fetchall()
    refs = [("execution", row[0]) for row in rows]
    refs += [("task_command", row[0]) for row in ops.db.execute("""SELECT c.command_id FROM commands c
        JOIN tasks t USING(task_id) WHERE t.host=? AND c.session_id=t.session_id AND c.kind='send'
        AND c.status IN ('accepted','settled') AND NOT EXISTS (SELECT 1 FROM commands n WHERE n.task_id=c.task_id
          AND n.session_id=c.session_id AND n.kind='send' AND n.status IN ('accepted','settled')
          AND (n.created_at>c.created_at OR (n.created_at=c.created_at AND n.command_id>c.command_id)))
        ORDER BY c.created_at DESC,c.command_id DESC LIMIT ?""", (host, limit))]
    out, seen = [], set()
    for kind, source_id in refs:
        source_host, sid, _, origin = reference(ops, kind, source_id)
        if kind == "execution" and (source_host, sid) in seen:
            continue
        seen.add((source_host, sid))
        # Task commands have one source identity; the wrapping session.send is not another result.
        if kind == "execution" and (origin.get("external_refs") or {}).get("command_id"):
            continue
        out.append(describe(ops, kind, source_id))
    return sorted(out, key=lambda item: (item["created_at"], item["id"]), reverse=True)[:limit]


def describe(ops, kind, source_id, *, _registry=None):
    source_host, sid, _, origin = reference(ops, kind, source_id)
    item = {"kind": kind, "id": source_id, "host": source_host, "session_id": sid,
                "created_at": origin["created_at"] if kind == "execution" else origin["command_created_at"],
                "operation_id": source_id if kind == "execution" else None,
                "task_id": origin["task_id"] if kind == "task_command" else None,
                "status": origin["status"] if kind == "execution" else origin["state"],
                "action": origin["action"] if kind == "execution" else "task.command",
                "actor": origin["actor"] if kind == "execution" else None,
                "repository": (origin.get("target") or {}).get("repository") if kind == "execution" else None,
                "title": (origin.get("params") or {}).get("title") if kind == "execution" else origin["original_words"][:120],
                "project_ids": project_ids(ops, kind, source_id, source_host, sid, origin)}
    try:
        entries = None
        if _registry is not None and source_host in ops.context["fleet"].config.hosts:
            if source_host not in _registry:
                _registry[source_host] = registry.list_entries(source_host)
            entries = _registry[source_host]
        source = resolve(ops, kind, source_id, _entries=entries)
        item.update(branch=source["ref"], worktree_path=source["location"], eligible=True, unavailable=None)
    except OperationError as error:
        item.update(branch=None, worktree_path=None, eligible=False,
                        unavailable={"code": error.code, "message": error.message})
    # Worktree identity is the observation's creation slot, never a hash of a folder name.
    observed = ops.db.execute("SELECT body FROM observation_resources WHERE resource_type='session' AND resource_id=?",
                                  (f"{source_host}/{sid}",)).fetchone()
    item["worktree_id"] = json.loads(observed[0]).get("worktree_id") if observed else None
    return item


def for_project(ops, project, limit=50):
    """Exact stored project IDs, explicit Work Item links, and the configured Task Service mapping."""
    pid = project["project_id"]
    rows = ops.db.execute("""SELECT o.operation_id FROM operations o WHERE
        o.action IN ('session.start','repository.continue','checkpoint.continue','integration.handoff','session.send')
        AND (json_extract(o.params,'$.project_id')=? OR EXISTS (
          SELECT 1 FROM work_item_links l JOIN work_items w USING(work_item_id)
          WHERE w.project_id=? AND l.removed_at IS NULL AND ((l.kind='operation' AND l.ref=o.operation_id)
            OR (l.kind='session' AND l.ref=COALESCE(json_extract(o.result,'$.host'),json_extract(o.target,'$.host')) || '/'
              || COALESCE(json_extract(o.result,'$.session_id'),json_extract(o.target,'$.session_id'))))))
        ORDER BY o.created_at DESC,o.operation_id DESC LIMIT ?""", (pid, pid, limit))
    # One registry read per host in this synchronous response only. Later reads
    # and every write/preview still load current creation evidence independently.
    registry_rows = {}
    items = [describe(ops, "execution", row[0], _registry=registry_rows) for row in rows]
    tasks = ops.db.execute("""SELECT t.* FROM tasks t WHERE t.project=? OR EXISTS (
        SELECT 1 FROM work_item_links l JOIN work_items w USING(work_item_id)
        WHERE w.project_id=? AND l.removed_at IS NULL AND l.kind='task' AND l.ref=t.task_id)
        ORDER BY t.submitted_at DESC,t.task_id DESC LIMIT ?""", (project.get("task_project"), pid, limit))
    for task in tasks:
        command = ops.db.execute("""SELECT command_id FROM commands WHERE task_id=? AND session_id=?
            AND kind='send' AND status IN ('accepted','settled') ORDER BY created_at DESC,command_id DESC LIMIT 1""",
                                 (task["task_id"], task["session_id"])).fetchone()
        if command:
            items.append(describe(ops, "task_command", command[0], _registry=registry_rows))
        else:
            items.append({"kind": "task", "id": task["task_id"], "task_id": task["task_id"], "host": task["host"],
                          "session_id": task["session_id"], "created_at": task["submitted_at"], "status": task["state"],
                          "title": task["original_words"][:120], "action": "task", "eligible": False})
    return sorted(items, key=lambda item: (item["created_at"], item["id"]), reverse=True)[:limit]


def for_worktree(ops, resource):
    """Recorded current bindings; history remains available through the existing relations API."""
    inv = ops.context.get("inventory")
    sessions, work = [], []
    for row in ops.db.execute("""SELECT DISTINCT session_resource_id FROM session_worktree_bindings
        WHERE worktree_id=? AND end_seq IS NULL ORDER BY session_resource_id LIMIT 100""", (resource["resource_id"],)):
        host, _, sid = row[0].partition("/")
        observed = inv.get_session(host, sid) if inv and host in ops.context["fleet"].config.hosts else None
        sessions.append({"host": host, "session_id": sid, "observation": observed})
        entry = registry.get(host, sid) or {}
        command = ops.db.execute("""SELECT command_id FROM commands WHERE task_id=? AND session_id=?
            AND kind='send' AND status IN ('accepted','settled') ORDER BY created_at DESC,command_id DESC LIMIT 1""",
                                 (entry.get("task_id"), sid)).fetchone()
        if command:
            item = describe(ops, "task_command", command[0])
        else:
            operation = ops.db.execute("""SELECT operation_id FROM operations WHERE status='succeeded'
                AND action IN ('session.start','repository.continue','checkpoint.continue','integration.handoff')
                AND json_extract(result,'$.host')=? AND json_extract(result,'$.session_id')=?
                ORDER BY created_at DESC,operation_id DESC LIMIT 1""", (host, sid)).fetchone()
            item = describe(ops, "execution", operation[0]) if operation else None
        if item and item.get("worktree_id") == resource["resource_id"]:
            work.append(item)
    return {"known_sessions": sessions, "work": work}
