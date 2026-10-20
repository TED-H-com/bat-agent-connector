"""Projects and work items: the connector's own management data.

A project groups work items and names the repositories and Task Service project it covers. A work item is one
goal: the request verbatim, acceptance criteria, steps, a state, and links to the sessions, checkpoints,
operations, tasks and PRs that carried it out. Both live only in the journal; nothing here reaches a host, Git or
GitHub, so each change is one SQLite transaction stamped with the operation that made it, and a re-run after a
restart returns the recorded result instead of applying twice.

Rules ported from Project Hub v4.68.2 (kieiken/project-hub@031aedd4, MIT; see THIRD_PARTY_NOTICES.md):

- hierarchy.js: a rename keeps every ID and relation; a change names the version it was based on.
- project-order.js: one saved order per parent, pinned entries first, a reorder names the order it saw, and an
  archived entry keeps its slot so a restore puts it back where it was. A branch (derived_from a sibling) that
  was never placed sits right after its source.
- completion.js: "done" from an agent is a claim. A person approves it against a fingerprint of the content they
  read; editing the content afterwards asks again, and "keep working" clears the claim for the current steps.
- task-ids.js: IDs are never reused (random, and archived rows are kept).

Design: docs/design/work-items.md.
"""

from __future__ import annotations

import hashlib
import json
import re
import secrets
import time
from collections import defaultdict
from collections.abc import Callable

from . import artifacts
from .operations import ActionDef, OpContext, OperationError, OperationService

PROJECT_ID = re.compile(r"prj_[0-9a-f]{20}")
WORK_ITEM_ID = re.compile(r"wi_[0-9a-f]{20}")
STATES = ("todo", "doing", "waiting", "done")
LINK_KINDS = ("session", "checkpoint", "operation", "task", "pull_request", "github_source")
STARTS_SESSION = ("checkpoint.continue", "integration.handoff", "repository.continue")  # their result names the session they started
NAME_MAX = {"project": 80, "work_item": 120}
TEXT_MAX = 20_000
NOTE_MAX = 500
STEPS_MAX = 50
STEP_MAX = 300
REPOS_MAX = 20
LIST_MAX = 200
_REPO = re.compile(r"[A-Za-z0-9][A-Za-z0-9-]{0,38}/[A-Za-z0-9._-]{1,100}")
_PULL = re.compile(r"([A-Za-z0-9][A-Za-z0-9-]{0,38}/[A-Za-z0-9._-]{1,100})#([1-9][0-9]{0,8})")
_CHECKPOINT = re.compile(r"cp_[0-9a-f]{32}")
_OPERATION = re.compile(r"op_[0-9a-f]{32}")
_TASK = re.compile(r"[0-9a-f-]{8,64}")
_NOT_ONE_LINE = re.compile(r"[\x00-\x1f\x7f  ]")
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
PROJECT_FIELDS = ("name", "description", "parent_id", "repositories", "task_project")
ITEM_FIELDS = ("title", "goal", "request", "acceptance", "steps", "state", "parent_id", "attachments")
CONTENT_FIELDS = ("title", "goal", "request", "acceptance", "steps")
MAX_DEPTH = 32  # levels in a project or work item tree


def _now() -> float:
    return time.time()


def _sha(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     separators=(",", ":")).encode()).hexdigest()


def _bad(code: str, message: str, status: int = 422) -> OperationError:
    return OperationError(code, message, status)


# ------------------------------------------------------------------ completion (from completion.js)
def fingerprint(item: dict) -> str:
    """What a person approves: the content they read, title included (anyone with manage can rename, so a rename
    asks again). Order, pins and links are not content."""
    content = {k: item[k] for k in CONTENT_FIELDS}
    if item.get("attachments"):
        content["attachments"] = item["attachments"]
    return _sha(content)


def steps_hash(steps: list[dict]) -> str:
    return _sha(steps)


def completion(item: dict) -> dict:
    fp = fingerprint(item)
    steps = item["steps"]
    approved = item["state"] == "done" and item["approved_fingerprint"] == fp
    all_steps = bool(steps) and all(s["done"] for s in steps)
    pending = not approved and (item["state"] == "done"
                                or (all_steps and item["continued_steps"] != steps_hash(steps)))
    return {"fingerprint": fp, "approved": approved, "pending": pending,
            "display_state": "awaiting_approval" if item["state"] == "done" and not approved else item["state"],
            "claimed_by": item["done_by"] if item["state"] == "done" else None,
            "approved_by": item["approved_by"] if approved else None,
            "approved_at": item["approved_at"] if approved else None}


# ------------------------------------------------------------------ input checks
def _one_line(value, field: str, limit: int) -> str:
    if not isinstance(value, str):
        raise _bad("INVALID_PARAMS", f"{field} must be a string")
    text = value.strip()
    if not text or len(text) > limit or _NOT_ONE_LINE.search(text):
        raise _bad("INVALID_PARAMS", f"{field} must be one line of 1-{limit} characters")
    return text


def _text(value, field: str, limit: int = TEXT_MAX) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise _bad("INVALID_PARAMS", f"{field} must be a string")
    text = value.replace("\r\n", "\n").replace("\r", "\n")
    if len(text) > limit or _CONTROL.search(text):
        raise _bad("INVALID_PARAMS", f"{field} must be at most {limit} characters of text")
    return text


def _steps(value) -> list[dict]:
    if value is None:
        return []
    if not isinstance(value, list) or len(value) > STEPS_MAX:
        raise _bad("INVALID_PARAMS", f"steps must be a list of at most {STEPS_MAX} entries")
    out = []
    for s in value:
        if isinstance(s, str):
            s = {"text": s, "done": False}
        if not isinstance(s, dict) or set(s) - {"text", "done"} or not isinstance(s.get("done", False), bool):
            raise _bad("INVALID_PARAMS", "each step is a string or {text, done}")
        out.append({"text": _one_line(s.get("text"), "a step", STEP_MAX), "done": bool(s.get("done", False))})
    return out


def _repos(value) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or len(value) > REPOS_MAX:
        raise _bad("INVALID_PARAMS", f"repositories must be a list of at most {REPOS_MAX} owner/name entries")
    out: list[str] = []
    for r in value:
        if not isinstance(r, str) or not _REPO.fullmatch(r):
            raise _bad("INVALID_PARAMS", f"repository {str(r)[:60]!r} is not owner/name")
        if r.lower() not in (x.lower() for x in out):
            out.append(r)
    return out


def _task_project(value) -> str | None:
    if value in (None, ""):
        return None
    return _one_line(value, "task_project", 100)


def _bool(params: dict, key: str) -> bool:
    value = params.get(key)
    if not isinstance(value, bool):
        raise _bad("INVALID_PARAMS", f"params.{key} must be true or false")
    return value


def _expect_version(row, pre: dict) -> None:
    exp = pre.get("expected_version")
    if isinstance(exp, bool) or not isinstance(exp, int):
        raise _bad("PRECONDITION_REQUIRED", "preconditions.expected_version is required (the version you read)")
    if exp != row["version"]:
        raise _bad("VERSION_CONFLICT", f"it changed since you read it (version {row['version']}, you read {exp}); "
                   "read it again", 409)


def _expect_fingerprint(item: dict, pre: dict) -> None:
    exp = pre.get("expected_fingerprint")
    if not isinstance(exp, str) or not exp:
        raise _bad("PRECONDITION_REQUIRED", "preconditions.expected_fingerprint is required (from the item you "
                   "read)")
    if exp != fingerprint(item):
        raise _bad("CONTENT_CHANGED", "the work item's content changed since you read it; read it again", 409)


def _only(params: dict, allowed: tuple[str, ...]) -> None:
    extra = sorted(set(params) - set(allowed))
    if extra:
        raise _bad("INVALID_PARAMS", f"unknown params: {', '.join(extra)}")


# ------------------------------------------------------------------ rows
def _project(row) -> dict:
    return {"project_id": row["project_id"], "name": row["name"], "description": row["description"],
            "parent_id": row["parent_id"], "derived_from": row["derived_from"],
            "repositories": json.loads(row["repositories"]), "task_project": row["task_project"],
            "pinned": bool(row["pinned"]), "archived": row["archived_at"] is not None,
            "archived_at": row["archived_at"], "archived_by": row["archived_by"], "version": row["version"],
            "created_by": row["created_by"], "created_at": row["created_at"], "updated_at": row["updated_at"]}


def _item(row) -> dict:
    item = {"work_item_id": row["work_item_id"], "project_id": row["project_id"], "parent_id": row["parent_id"],
            "derived_from": row["derived_from"], "title": row["title"], "goal": row["goal"],
            "request": row["request"], "acceptance": row["acceptance"], "steps": json.loads(row["steps"]),
            "attachments": json.loads(row["attachments"]),
            "state": row["state"], "done_by": row["done_by"], "done_at": row["done_at"],
            "approved_fingerprint": row["approved_fingerprint"], "approved_by": row["approved_by"],
            "approved_at": row["approved_at"], "continued_steps": row["continued_steps"],
            "pinned": bool(row["pinned"]), "archived": row["archived_at"] is not None,
            "archived_at": row["archived_at"], "archived_by": row["archived_by"], "version": row["version"],
            "created_by": row["created_by"], "created_at": row["created_at"], "updated_at": row["updated_at"]}
    item['external'] = json.loads(row['github_external']) if 'github_external' in row.keys() and row['github_external'] else None
    return item


def _public(item: dict) -> dict:
    out = {k: v for k, v in item.items() if k not in {"approved_fingerprint", "continued_steps"}}
    out["completion"] = completion(item)
    return out


def _get_project(db, project_id, *, active: bool = False) -> dict:
    row = db.execute("SELECT * FROM projects WHERE project_id=?", (str(project_id),)).fetchone()
    if row is None:
        raise _bad("PROJECT_NOT_FOUND", "no such project", 404)
    if active and row["archived_at"] is not None:
        raise _bad("PROJECT_ARCHIVED", "the project is archived; restore it first", 409)
    return _project(row)


def _get_item(db, work_item_id, *, active: bool = False) -> dict:
    row = db.execute("SELECT * FROM work_items WHERE work_item_id=?", (str(work_item_id),)).fetchone()
    if row is None:
        raise _bad("WORK_ITEM_NOT_FOUND", "no such work item", 404)
    if active and row["archived_at"] is not None:
        raise _bad("WORK_ITEM_ARCHIVED", "the work item is archived; restore it first", 409)
    return _item(row)


def _target_id(target: dict, key: str, pattern: re.Pattern) -> str:
    value = target.get(key)
    if not isinstance(value, str) or not pattern.fullmatch(value):
        raise _bad("INVALID_TARGET", f"target.{key} is malformed")
    return value


# ------------------------------------------------------------------ order (from project-order.js)
def _saved_order(db, scope: str, parent: str) -> list[str] | None:
    row = db.execute("SELECT ids FROM tree_order WHERE scope=? AND parent=?", (scope, parent)).fetchone()
    return json.loads(row["ids"]) if row else None


def siblings(items: list[dict], saved: list[str] | None, key: str) -> list[dict]:
    """Display order of one parent's active children (``items`` in creation order). Saved entries keep their
    relative order; an entry never placed goes right after its source when that source is a sibling, otherwise
    to the end. Pinned entries come first."""
    ids = {x[key] for x in items}
    rank = {i: n for n, i in enumerate(saved or [])}
    loose = [x for x in items if x[key] not in rank]
    result: list[dict] = []
    seen: set[str] = set()

    def add(x: dict) -> None:  # depth-first, iterative: a long chain of branches must not recurse
        stack = [x]
        while stack:
            cur = stack.pop()
            if cur[key] in seen:
                continue
            seen.add(cur[key])
            result.append(cur)
            stack.extend(reversed([c for c in loose if c["derived_from"] == cur[key]]))

    for x in sorted((x for x in items if x[key] in rank), key=lambda x: rank[x[key]]):
        add(x)
    for x in loose:
        if x["derived_from"] not in ids:
            add(x)
    for x in loose:  # a cycle of derived entries still shows
        add(x)
    return [x for x in result if x["pinned"]] + [x for x in result if not x["pinned"]]


def _tree(items: list[dict], scope: str, db, key: str, decorate: Callable[[dict], dict]) -> list[dict]:
    by_parent: dict[str, list[dict]] = defaultdict(list)
    active = {x[key] for x in items}
    for x in items:
        by_parent[x["parent_id"] if x["parent_id"] in active else ""].append(x)
    placed: set[str] = set()

    def build(parent: str, path: frozenset) -> list[dict]:
        out = []
        for x in siblings(by_parent.get(parent, []), _saved_order(db, scope, parent), key):
            if x[key] in path or x[key] in placed:
                continue
            placed.add(x[key])
            out.append({**decorate(x), "children": build(x[key], path | {x[key]})})
        return out

    roots = build("", frozenset())
    for x in items:  # a parent cycle never hides an entry
        if x[key] not in placed:
            placed.add(x[key])
            roots.append({**decorate(x), "children": build(x[key], frozenset({x[key]}))})
    return roots


def _check_order(current: list[dict], key: str, before, order, versions=None) -> None:
    def valid(ids) -> bool:
        return isinstance(ids, list) and all(isinstance(i, str) for i in ids) and len(set(ids)) == len(ids)

    if not valid(before):
        raise _bad("PRECONDITION_REQUIRED", "preconditions.before is required (the order you saw)")
    if not valid(order):
        raise _bad("INVALID_PARAMS", "params.order must list each sibling once")
    now_ids = [x[key] for x in current]
    if before != now_ids or sorted(order) != sorted(now_ids):
        raise _bad("ORDER_CHANGED", "the order changed since you read it; read it again", 409)
    if versions is not None:
        if not isinstance(versions, dict) or set(versions) != set(now_ids) or any(type(v) is not int or v < 1 for v in versions.values()):
            raise _bad("PRECONDITION_REQUIRED", "expected_versions must name every displayed sibling version")
        if any(versions[row[key]] != row["version"] for row in current):
            raise _bad("VERSION_CONFLICT", "a sibling changed since you read it; read it again", 409)
    pinned = {x[key] for x in current if x["pinned"]}
    if any((a in pinned) != (b in pinned) for a, b in zip(order, now_ids, strict=True)):
        raise _bad("PINNED_FIRST", "pinned entries stay above the others; unpin one to move it down", 409)


def _save_order(db, scope: str, parent: str, current: list[dict], key: str, order: list[str],
                everyone: list[dict]) -> list[str]:
    """Save ``order`` for the active siblings. ``everyone`` (archived included, creation order) seeds a first save,
    so an entry archived before any reorder still has a slot to come back to."""
    active = {x[key] for x in current}
    old = _saved_order(db, scope, parent)
    if old is None:
        old = [x[key] for x in siblings(everyone, None, key)]
    it = iter(order)
    merged = [next(it) if i in active else i for i in old]  # archived or moved IDs keep their slot
    merged += list(it)
    db.execute("""INSERT INTO tree_order(scope,parent,ids) VALUES(?,?,?)
        ON CONFLICT(scope,parent) DO UPDATE SET ids=excluded.ids""", (scope, parent, json.dumps(merged)))
    return [i for i in merged if i in active]


def _active_projects(db) -> list[dict]:
    return [_project(r) for r in db.execute(
        "SELECT * FROM projects WHERE archived_at IS NULL ORDER BY created_at, project_id")]


def _active_items(db, project_id: str) -> list[dict]:
    return [_item(r) for r in db.execute("""SELECT * FROM work_items WHERE project_id=? AND archived_at IS NULL
        ORDER BY created_at, work_item_id""", (project_id,))]


def _item_scope(project_id: str) -> str:
    return "items:" + project_id


# ------------------------------------------------------------------ relations
def _project_parent(db, parent_id, *, moving: str | None = None) -> str | None:
    if parent_id in (None, ""):
        return None
    if not isinstance(parent_id, str) or not PROJECT_ID.fullmatch(parent_id):
        raise _bad("INVALID_PARAMS", "params.parent_id is malformed")
    _get_project(db, parent_id, active=True)
    seen, cur = set(), parent_id
    while cur:
        if cur == moving or cur in seen:
            raise _bad("CYCLE", "a project cannot sit under itself or one of its own sub-projects", 409)
        seen.add(cur)
        row = db.execute("SELECT parent_id FROM projects WHERE project_id=?", (cur,)).fetchone()
        cur = row["parent_id"] if row else None
    _fits(len(seen), _height(db, "projects", "project_id", moving))
    return parent_id


def _item_parent(db, project_id: str, parent_id, *, moving: str | None = None) -> str | None:
    if parent_id in (None, ""):
        return None
    if not isinstance(parent_id, str) or not WORK_ITEM_ID.fullmatch(parent_id):
        raise _bad("INVALID_PARAMS", "params.parent_id is malformed")
    parent = _get_item(db, parent_id, active=True)
    if parent["project_id"] != project_id:
        raise _bad("WRONG_PROJECT", "a work item's parent must be in the same project", 409)
    seen, cur = set(), parent_id
    while cur:
        if cur == moving or cur in seen:
            raise _bad("CYCLE", "a work item cannot sit under itself or one of its own children", 409)
        seen.add(cur)
        row = db.execute("SELECT parent_id FROM work_items WHERE work_item_id=?", (cur,)).fetchone()
        cur = row["parent_id"] if row else None
    _fits(len(seen), _height(db, "work_items", "work_item_id", moving))
    return parent_id


def _height(db, table: str, key: str, root: str | None) -> int:
    """Levels below ``root`` (0 for a leaf or a new entry)."""
    levels, frontier = 0, [root] if root else []
    while frontier:
        frontier = [r[key] for p in frontier for r in db.execute(
            f"SELECT {key} FROM {table} WHERE parent_id=?", (p,))]  # noqa: S608 - fixed names
        levels += bool(frontier)
        if levels > MAX_DEPTH:
            break
    return levels


def _fits(ancestors: int, height: int) -> None:
    if ancestors + 1 + height > MAX_DEPTH:
        raise _bad("TOO_DEEP", f"a tree is at most {MAX_DEPTH} levels deep", 409)


def _name_free(db, name: str, *, other_than: str | None = None) -> None:
    row = db.execute("""SELECT project_id FROM projects WHERE archived_at IS NULL AND name=?
        AND project_id IS NOT ?""", (name, other_than)).fetchone()
    if row:
        raise _bad("NAME_TAKEN", "another project already has this name", 409)


def _descendants(db, table: str, key: str, root: str, *, archived_by_op: str | None = None) -> list[str]:
    out, frontier = [], [root]
    while frontier:
        nxt = []
        for parent in frontier:
            sql = f"SELECT {key} FROM {table} WHERE parent_id=? AND "  # noqa: S608 - fixed names
            sql += "archived_at IS NULL" if archived_by_op is None else "archive_operation=?"
            args = (parent,) if archived_by_op is None else (parent, archived_by_op)
            for r in db.execute(sql, args):
                if r[key] not in out and r[key] != root:
                    out.append(r[key])
                    nxt.append(r[key])
        frontier = nxt
    return out


# ------------------------------------------------------------------ links
def _link_ref(ops: OperationService, kind, ref) -> str:
    db = ops.db
    if kind not in LINK_KINDS:
        raise _bad("INVALID_PARAMS", f"params.kind must be one of {', '.join(LINK_KINDS)}")
    if not isinstance(ref, str) or not ref or len(ref) > 300:
        raise _bad("INVALID_PARAMS", "params.ref is required")
    if kind == "session":
        host, sep, sid = ref.partition("/")
        fleet = ops.context.get("fleet")
        if not sep or not sid or (fleet is not None and host not in fleet.config.hosts):
            raise _bad("INVALID_PARAMS", "a session ref is host/session_id on a configured host")
        if not db.execute("SELECT 1 FROM sessions_observed WHERE host=? AND session_id=?", (host, sid)).fetchone():
            raise _bad("LINK_TARGET_NOT_FOUND", "the connector has never observed this session", 404)
    elif kind == "checkpoint":
        if not _CHECKPOINT.fullmatch(ref) or not db.execute(
                "SELECT 1 FROM checkpoints WHERE checkpoint_id=?", (ref,)).fetchone():
            raise _bad("LINK_TARGET_NOT_FOUND", "no such checkpoint", 404)
    elif kind == "operation":
        if not _OPERATION.fullmatch(ref) or not db.execute(
                "SELECT 1 FROM operations WHERE operation_id=?", (ref,)).fetchone():
            raise _bad("LINK_TARGET_NOT_FOUND", "no such operation", 404)
        action = db.execute("SELECT action FROM operations WHERE operation_id=?", (ref,)).fetchone()[0]
        if action.startswith("pm."):
            raise _bad("SCOPED_LINK_REQUIRED", "PM operations require workspace-scoped associations", 422)
    elif kind == "task":
        if not _TASK.fullmatch(ref) or not db.execute("SELECT 1 FROM tasks WHERE task_id=?", (ref,)).fetchone():
            raise _bad("LINK_TARGET_NOT_FOUND", "no such task", 404)
    elif kind == "github_source":
        from .board_github import URL
        if not URL.fullmatch(ref):
            raise _bad("INVALID_PARAMS", "a github_source ref is a canonical GitHub issue or PR URL")
    elif not _PULL.fullmatch(ref):
        raise _bad("INVALID_PARAMS", "a pull_request ref is owner/name#number")
    return ref


def _link_summary(db, kind: str, ref: str) -> dict:
    """What a link points at now, from the journal and inventory only (no host is asked)."""
    if kind == "session":
        host, _, sid = ref.partition("/")
        row = db.execute("SELECT body,api_access,gone_at,last_seen_at FROM sessions_observed WHERE host=? "
                         "AND session_id=?", (host, sid)).fetchone()
        if row is None:
            return {"found": False}
        body = json.loads(row["body"])
        return {"found": True, "host": host, "session_id": sid, "title": body.get("title"),
                "api_access": row["api_access"], "gone": row["gone_at"] is not None,
                "last_seen_at": row["last_seen_at"]}
    if kind == "checkpoint":
        row = db.execute("SELECT host,source_session_id,branch,commit_sha,note,captured_at FROM checkpoints "
                         "WHERE checkpoint_id=?", (ref,)).fetchone()
        return {"found": False} if row is None else {"found": True, **dict(row)}
    if kind == "operation":
        row = db.execute("SELECT action,status,error_code,result,created_at FROM operations WHERE operation_id=?",
                         (ref,)).fetchone()
        if row is None:
            return {"found": False}
        result = json.loads(row["result"]) if row["result"] else {}
        started = row["action"] in STARTS_SESSION and isinstance(result.get("session_id"), str)
        session = {"host": result.get("host"), "session_id": result["session_id"]} if started else None
        return {"found": True, "action": row["action"], "status": row["status"], "error_code": row["error_code"],
                "created_at": row["created_at"], "session": session}
    if kind == "task":
        row = db.execute("SELECT project,host,workspace,state,submitted_at FROM tasks WHERE task_id=?",
                         (ref,)).fetchone()
        return {"found": False} if row is None else {"found": True, **dict(row)}
    if kind == "github_source":
        return {"found": True, "url": ref, "source": "github"}
    m = _PULL.fullmatch(ref)
    return {"found": True, "repository": m.group(1), "number": int(m.group(2))} if m else {"found": False}


# ------------------------------------------------------------------ applying a change once
def _once(ctx: OpContext, change: Callable[[object, float], dict]) -> dict:
    """Run ``change`` and record the operation in the same transaction. A re-run of the same operation (the
    daemon restarted before the result was stored) returns the recorded result."""
    journal, db = ctx.service.journal, ctx.service.db
    with journal.tx():
        row = db.execute("SELECT result FROM management_applied WHERE operation_id=?",
                         (ctx.operation_id,)).fetchone()
        if row is not None:
            return json.loads(row["result"])
        from .pm_source_lineage import require_new_effect
        require_new_effect(ctx.service, ctx.operation_id)
        from .pm_lifecycle_gates import require_local
        require_local(db, ctx.operation_id)
        now = _now()
        result = change(db, now)
        db.execute("INSERT INTO management_applied(operation_id,result,applied_at) VALUES(?,?,?)",
                   (ctx.operation_id, json.dumps(result, ensure_ascii=False), now))
    return result


def _event(ctx: OpContext, resource_type: str, resource_id: str, kind: str, body: dict) -> None:
    ctx.service.journal.api_event(resource_type, resource_id, kind,
                                  {**body, "operation_id": ctx.operation_id}, actor=ctx.op["actor"])


# ------------------------------------------------------------------ projects
def _check_project_create(ops: OperationService, params: dict) -> dict:
    _only(params, ("name", "description", "parent_id", "derived_from", "repositories", "task_project"))
    db = ops.db
    name = _one_line(params.get("name"), "name", NAME_MAX["project"])
    _name_free(db, name)
    derived = params.get("derived_from")
    if derived not in (None, ""):
        if not isinstance(derived, str) or not PROJECT_ID.fullmatch(derived):
            raise _bad("INVALID_PARAMS", "params.derived_from is malformed")
        _get_project(db, derived)
    return {"name": name, "description": _text(params.get("description"), "description"),
            "parent_id": _project_parent(db, params.get("parent_id")), "derived_from": derived or None,
            "repositories": _repos(params.get("repositories")),
            "task_project": _task_project(params.get("task_project"))}


def _admit_project_create(ops, principal, target, params, pre) -> None:
    _check_project_create(ops, params)


async def _run_project_create(ctx: OpContext) -> dict:
    def change(db, now):
        v = _check_project_create(ctx.service, ctx.params)
        pid = "prj_" + secrets.token_hex(10)
        db.execute("""INSERT INTO projects(project_id,name,description,parent_id,derived_from,repositories,
            task_project,created_by,operation_id,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                   (pid, v["name"], v["description"], v["parent_id"], v["derived_from"],
                    json.dumps(v["repositories"]), v["task_project"], ctx.op["actor"], ctx.operation_id, now, now))
        _event(ctx, "project", pid, "project.created", {"name": v["name"], "parent_id": v["parent_id"]})
        return {"project_id": pid, "version": 1}
    return _once(ctx, change)


def _check_project_update(ops: OperationService, target: dict, params: dict, pre: dict) -> tuple[dict, dict]:
    db = ops.db
    project = _get_project(db, _target_id(target, "project_id", PROJECT_ID))
    _expect_version(project, pre)
    if "archived" in params:
        _only(params, ("archived",))
        archived = _bool(params, "archived")
        if archived and not project["archived"]:
            kids = [r["project_id"] for r in db.execute(
                "SELECT project_id FROM projects WHERE parent_id=? AND archived_at IS NULL", (project["project_id"],))]
            if kids:
                raise _bad("HAS_CHILDREN", f"archive its {len(kids)} sub-project(s) first", 409)
        if not archived and project["archived"]:
            if project["parent_id"] and _get_project(db, project["parent_id"])["archived"]:
                raise _bad("PARENT_ARCHIVED", "restore its parent project first", 409)
            _name_free(db, project["name"], other_than=project["project_id"])
        return project, {"archived": archived}
    _only(params, PROJECT_FIELDS)
    if project["archived"]:
        raise _bad("PROJECT_ARCHIVED", "the project is archived; restore it first", 409)
    changes: dict = {}
    if "name" in params:
        changes["name"] = _one_line(params["name"], "name", NAME_MAX["project"])
        _name_free(db, changes["name"], other_than=project["project_id"])
    if "description" in params:
        changes["description"] = _text(params["description"], "description")
    if "parent_id" in params:
        changes["parent_id"] = _project_parent(db, params["parent_id"], moving=project["project_id"])
    if "repositories" in params:
        changes["repositories"] = _repos(params["repositories"])
    if "task_project" in params:
        changes["task_project"] = _task_project(params["task_project"])
    return project, {k: v for k, v in changes.items() if v != project[k]}


def _admit_project_update(ops, principal, target, params, pre) -> None:
    _check_project_update(ops, target, params, pre)


async def _run_project_update(ctx: OpContext) -> dict:
    def change(db, now):
        project, changes = _check_project_update(ctx.service, ctx.target, ctx.params, ctx.preconditions)
        pid = project["project_id"]
        if "archived" in changes:
            if changes["archived"] == project["archived"]:
                return {"project_id": pid, "version": project["version"], "changed": False}
            db.execute("""UPDATE projects SET archived_at=?,archived_by=?,version=version+1,updated_at=?
                WHERE project_id=?""", (now if changes["archived"] else None,
                                        ctx.op["actor"] if changes["archived"] else None, now, pid))
            _event(ctx, "project", pid, "project.archived" if changes["archived"] else "project.restored", {})
            return {"project_id": pid, "version": project["version"] + 1, "changed": True}
        if not changes:
            return {"project_id": pid, "version": project["version"], "changed": False}
        cols = {k: json.dumps(v) if k == "repositories" else v for k, v in changes.items()}
        sets = ",".join(f"{k}=?" for k in cols)
        db.execute(f"UPDATE projects SET {sets},version=version+1,updated_at=? WHERE project_id=?",  # noqa: S608
                   (*cols.values(), now, pid))
        _event(ctx, "project", pid, "project.updated", {"fields": sorted(changes)})
        return {"project_id": pid, "version": project["version"] + 1, "changed": True, "fields": sorted(changes)}
    return _once(ctx, change)


def _check_project_order(ops: OperationService, params: dict) -> tuple[str, list[dict]]:
    _only(params, ("parent_id", "order"))
    parent = params.get("parent_id") or ""
    if parent:
        if not isinstance(parent, str) or not PROJECT_ID.fullmatch(parent):
            raise _bad("INVALID_PARAMS", "params.parent_id is malformed")
        _get_project(ops.db, parent, active=True)
    current = siblings([p for p in _active_projects(ops.db) if (p["parent_id"] or "") == parent],
                       _saved_order(ops.db, "projects", parent), "project_id")
    return parent, current


def _admit_project_order(ops, principal, target, params, pre) -> None:
    _, current = _check_project_order(ops, params)
    _check_order(current, "project_id", pre.get("before"), params.get("order"), pre.get("expected_versions"))


async def _run_project_order(ctx: OpContext) -> dict:
    def change(db, now):
        parent, current = _check_project_order(ctx.service, ctx.params)
        _check_order(current, "project_id", ctx.preconditions.get("before"), ctx.params.get("order"), ctx.preconditions.get("expected_versions"))
        everyone = [_project(r) for r in db.execute("""SELECT * FROM projects WHERE parent_id IS ?
            ORDER BY created_at, project_id""", (parent or None,))]
        order = _save_order(db, "projects", parent, current, "project_id", ctx.params["order"], everyone)
        _event(ctx, "project", parent or "root", "project.ordered", {"parent_id": parent or None})
        return {"parent_id": parent or None, "order": order}
    return _once(ctx, change)


def _check_pin(row: dict, params: dict, pre: dict) -> bool:
    _only(params, ("pinned",))
    pinned = _bool(params, "pinned")
    before = pre.get("before")
    if not isinstance(before, bool):
        raise _bad("PRECONDITION_REQUIRED", "preconditions.before is required (whether it was pinned when you "
                   "looked)")
    if row["archived"]:
        raise _bad("ARCHIVED", "restore it before pinning", 409)
    if before != row["pinned"]:
        raise _bad("PIN_CHANGED", "the pin changed since you read it; read it again", 409)
    return pinned


def _admit_project_pin(ops, principal, target, params, pre) -> None:
    _check_pin(_get_project(ops.db, _target_id(target, "project_id", PROJECT_ID)), params, pre)


async def _run_project_pin(ctx: OpContext) -> dict:
    def change(db, now):
        project = _get_project(db, _target_id(ctx.target, "project_id", PROJECT_ID))
        pinned = _check_pin(project, ctx.params, ctx.preconditions)
        db.execute("UPDATE projects SET pinned=?,updated_at=? WHERE project_id=?",
                   (int(pinned), now, project["project_id"]))
        if pinned != project["pinned"]:
            _event(ctx, "project", project["project_id"], "project.pinned" if pinned else "project.unpinned", {})
        return {"project_id": project["project_id"], "pinned": pinned}
    return _once(ctx, change)


# ------------------------------------------------------------------ work items
def _check_item_create(ops: OperationService, target: dict, params: dict) -> dict:
    _only(params, ("title", "goal", "request", "acceptance", "steps", "state", "parent_id", "derived_from", "attachments"))
    db = ops.db
    project = _get_project(db, _target_id(target, "project_id", PROJECT_ID), active=True)
    state = params.get("state", "todo")
    if state not in STATES[:3]:
        raise _bad("INVALID_PARAMS", "a new work item starts as todo, doing or waiting")
    derived = params.get("derived_from")
    if derived not in (None, ""):
        if not isinstance(derived, str) or not WORK_ITEM_ID.fullmatch(derived):
            raise _bad("INVALID_PARAMS", "params.derived_from is malformed")
        _get_item(db, derived)
    return {"attachments": artifacts.normalize_refs(db, params.get("attachments", []), ops.context["artifact_store"].settings, roles=True),
            "project_id": project["project_id"],
            "title": _one_line(params.get("title"), "title", NAME_MAX["work_item"]),
            "goal": _text(params.get("goal"), "goal"), "request": _text(params.get("request"), "request"),
            "acceptance": _text(params.get("acceptance"), "acceptance"), "steps": _steps(params.get("steps")),
            "state": state, "parent_id": _item_parent(db, project["project_id"], params.get("parent_id")),
            "derived_from": derived or None}


def _admit_item_create(ops, principal, target, params, pre) -> None:
    _check_item_create(ops, target, params)


async def _run_item_create(ctx: OpContext) -> dict:
    def change(db, now):
        v = _check_item_create(ctx.service, ctx.target, ctx.params)
        wid = "wi_" + secrets.token_hex(10)
        db.execute("""INSERT INTO work_items(work_item_id,project_id,parent_id,derived_from,title,goal,request,
            acceptance,steps,state,created_by,operation_id,created_at,updated_at)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                   (wid, v["project_id"], v["parent_id"], v["derived_from"], v["title"], v["goal"], v["request"],
                    v["acceptance"], json.dumps(v["steps"], ensure_ascii=False), v["state"], ctx.op["actor"],
                    ctx.operation_id, now, now))
        db.execute("UPDATE work_items SET attachments=? WHERE work_item_id=?", (artifacts.canonical(v["attachments"]), wid))
        artifacts.reference(db, "work_item", wid, v["attachments"], ctx.operation_id)
        _event(ctx, "work_item", wid, "work_item.created",
               {"project_id": v["project_id"], "title": v["title"], "parent_id": v["parent_id"]})
        return {"work_item_id": wid, "project_id": v["project_id"], "version": 1}
    return _once(ctx, change)


def _check_item_update(ops: OperationService, target: dict, params: dict, pre: dict) -> tuple[dict, dict]:
    db = ops.db
    item = _get_item(db, _target_id(target, "work_item_id", WORK_ITEM_ID))
    _expect_version(item, pre)
    if "archived" in params:
        _only(params, ("archived",))
        archived = _bool(params, "archived")
        _get_project(db, item["project_id"], active=True)  # an archived project's items are frozen
        if not archived and item["archived"]:
            if item["parent_id"] and _get_item(db, item["parent_id"])["archived"]:
                raise _bad("PARENT_ARCHIVED", "restore its parent work item first", 409)
        return item, {"archived": archived}
    _only(params, ITEM_FIELDS)
    if item["archived"]:
        raise _bad("WORK_ITEM_ARCHIVED", "the work item is archived; restore it first", 409)
    _get_project(db, item["project_id"], active=True)
    changes: dict = {}
    if "title" in params:
        changes["title"] = _one_line(params["title"], "title", NAME_MAX["work_item"])
    for key in ("goal", "request", "acceptance"):
        if key in params:
            changes[key] = _text(params[key], key)
    if "attachments" in params:
        changes["attachments"] = artifacts.normalize_refs(db, params["attachments"], ops.context["artifact_store"].settings, roles=True)
    if "steps" in params:
        changes["steps"] = _steps(params["steps"])
    if "state" in params:
        if params["state"] not in STATES:
            raise _bad("INVALID_PARAMS", f"params.state must be one of {', '.join(STATES)}")
        changes["state"] = params["state"]
    if "parent_id" in params:
        changes["parent_id"] = _item_parent(db, item["project_id"], params["parent_id"],
                                            moving=item["work_item_id"])
    steps = changes.get("steps", item["steps"])
    if changes.get("state") == "done" and item["state"] != "done" and not all(s["done"] for s in steps):
        raise _bad("STEPS_OPEN", "check every step (or remove the open ones) before marking it done", 409)
    return item, {k: v for k, v in changes.items() if v != item[k]}


def _admit_item_update(ops, principal, target, params, pre) -> None:
    _check_item_update(ops, target, params, pre)


def _manual_state(db, item):
    if item.get('external'):
        external = {**item['external'], 'manual': True, 'board_lane': None}
        db.execute('UPDATE work_items SET github_external=? WHERE work_item_id=?',
                   (json.dumps(external), item['work_item_id']))


async def _run_item_update(ctx: OpContext) -> dict:
    actor = ctx.op["actor"]

    def change(db, now):
        item, changes = _check_item_update(ctx.service, ctx.target, ctx.params, ctx.preconditions)
        wid = item["work_item_id"]
        if "archived" in changes:
            return _archive_item(ctx, db, now, item, changes["archived"])
        if not changes:
            return {"work_item_id": wid, "version": item["version"], "changed": False}
        after = {**item, **changes}
        if ("steps" in changes and after["state"] == "done" and item["state"] == "done"
                and not all(s["done"] for s in after["steps"])):
            after["state"] = changes["state"] = "doing"  # an unchecked or added step reopens it (Hub's setStep)
        claim: dict = {}
        if changes.get("state") == "done":
            claim = {"done_by": actor, "done_at": now}
        elif "state" in changes and item["state"] == "done":
            claim = {"done_by": None, "done_at": None, "approved_fingerprint": None, "approved_by": None,
                     "approved_at": None}
        elif item["state"] == "done" and any(k in changes for k in (*CONTENT_FIELDS, "attachments")):
            claim = {"done_by": actor, "done_at": now}  # whoever changes done content now presents it as done
        after.update(claim)
        cols = {**changes, **claim}
        if "steps" in cols:
            cols["steps"] = json.dumps(cols["steps"], ensure_ascii=False)
        if "attachments" in cols:
            artifacts.reference(db, "work_item", wid, changes["attachments"], ctx.operation_id)
            cols["attachments"] = artifacts.canonical(cols["attachments"])
        sets = ",".join(f"{k}=?" for k in cols)
        db.execute(f"UPDATE work_items SET {sets},version=version+1,updated_at=? WHERE work_item_id=?",  # noqa: S608
                   (*cols.values(), now, wid))
        fields = sorted(k for k in changes if k != "state")
        if fields:
            _event(ctx, "work_item", wid, "work_item.updated", {"fields": fields})
        if "state" in changes:
            _manual_state(db, item)
            _event(ctx, "work_item", wid, "work_item.state", {"from": item["state"], "to": changes["state"]})
        return {"work_item_id": wid, "version": item["version"] + 1, "changed": True, "fields": sorted(changes),
                "completion": completion(after)}
    return _once(ctx, change)


def _archive_item(ctx: OpContext, db, now: float, item: dict, archived: bool) -> dict:
    wid = item["work_item_id"]
    if archived == item["archived"]:
        return {"work_item_id": wid, "version": item["version"], "changed": False}
    if archived:
        ids = [wid, *_descendants(db, "work_items", "work_item_id", wid)]
        for i in ids:
            db.execute("""UPDATE work_items SET archived_at=?,archived_by=?,archive_operation=?,version=version+1,
                updated_at=? WHERE work_item_id=? AND archived_at IS NULL""",
                       (now, ctx.op["actor"], ctx.operation_id, now, i))
            _event(ctx, "work_item", i, "work_item.archived", {"with": wid if i != wid else None})
    else:
        row = db.execute("SELECT archive_operation FROM work_items WHERE work_item_id=?", (wid,)).fetchone()
        ids = [wid, *_descendants(db, "work_items", "work_item_id", wid, archived_by_op=row["archive_operation"])]
        for i in ids:
            db.execute("""UPDATE work_items SET archived_at=NULL,archived_by=NULL,archive_operation=NULL,
                version=version+1,updated_at=? WHERE work_item_id=?""", (now, i))
            _event(ctx, "work_item", i, "work_item.restored", {"with": wid if i != wid else None})
    return {"work_item_id": wid, "version": item["version"] + 1, "changed": True, "work_item_ids": ids}


def _check_item_order(ops: OperationService, target: dict, params: dict) -> tuple[str, str, list[dict]]:
    _only(params, ("parent_id", "order"))
    db = ops.db
    project = _get_project(db, _target_id(target, "project_id", PROJECT_ID), active=True)
    parent = params.get("parent_id") or ""
    if parent:
        if not isinstance(parent, str) or not WORK_ITEM_ID.fullmatch(parent):
            raise _bad("INVALID_PARAMS", "params.parent_id is malformed")
        if _get_item(db, parent, active=True)["project_id"] != project["project_id"]:
            raise _bad("WRONG_PROJECT", "params.parent_id is in another project", 409)
    scope = _item_scope(project["project_id"])
    current = siblings([x for x in _active_items(db, project["project_id"]) if (x["parent_id"] or "") == parent],
                       _saved_order(db, scope, parent), "work_item_id")
    return scope, parent, current


def _admit_item_order(ops, principal, target, params, pre) -> None:
    _, _, current = _check_item_order(ops, target, params)
    _check_order(current, "work_item_id", pre.get("before"), params.get("order"), pre.get("expected_versions"))


async def _run_item_order(ctx: OpContext) -> dict:
    def change(db, now):
        scope, parent, current = _check_item_order(ctx.service, ctx.target, ctx.params)
        _check_order(current, "work_item_id", ctx.preconditions.get("before"), ctx.params.get("order"), ctx.preconditions.get("expected_versions"))
        everyone = [_item(r) for r in db.execute("""SELECT * FROM work_items WHERE project_id=? AND parent_id IS ?
            ORDER BY created_at, work_item_id""", (ctx.target["project_id"], parent or None))]
        order = _save_order(db, scope, parent, current, "work_item_id", ctx.params["order"], everyone)
        pid = ctx.target["project_id"]
        _event(ctx, "project", pid, "work_item.ordered", {"parent_id": parent or None})
        return {"project_id": pid, "parent_id": parent or None, "order": order}
    return _once(ctx, change)


def _admit_item_pin(ops, principal, target, params, pre) -> None:
    item = _get_item(ops.db, _target_id(target, "work_item_id", WORK_ITEM_ID))
    _get_project(ops.db, item["project_id"], active=True)
    _check_pin(item, params, pre)


async def _run_item_pin(ctx: OpContext) -> dict:
    def change(db, now):
        item = _get_item(db, _target_id(ctx.target, "work_item_id", WORK_ITEM_ID))
        _get_project(db, item["project_id"], active=True)
        pinned = _check_pin(item, ctx.params, ctx.preconditions)
        db.execute("UPDATE work_items SET pinned=?,updated_at=? WHERE work_item_id=?",
                   (int(pinned), now, item["work_item_id"]))
        if pinned != item["pinned"]:
            _event(ctx, "work_item", item["work_item_id"], "work_item.pinned" if pinned else "work_item.unpinned", {})
        return {"work_item_id": item["work_item_id"], "pinned": pinned}
    return _once(ctx, change)


def _check_decide(ops: OperationService, target: dict, params: dict, pre: dict) -> dict:
    _only(params, ("note",))
    _text(params.get("note"), "note", NOTE_MAX)
    item = _get_item(ops.db, _target_id(target, "work_item_id", WORK_ITEM_ID), active=True)
    _get_project(ops.db, item["project_id"], active=True)
    _expect_fingerprint(item, pre)
    return item


def _admit_decide(ops, principal, target, params, pre) -> None:
    _check_decide(ops, target, params, pre)


def _admit_continue(ops, principal, target, params, pre) -> None:
    _undecided(_check_decide(ops, target, params, pre))


def _undecided(item: dict) -> None:
    c = completion(item)
    if not (c["pending"] or c["approved"]):
        raise _bad("NOTHING_TO_DECIDE", "nobody has claimed this work item is done", 409)


async def _run_approve(ctx: OpContext) -> dict:
    actor = ctx.op["actor"]

    def change(db, now):
        item = _check_decide(ctx.service, ctx.target, ctx.params, ctx.preconditions)
        fp = fingerprint(item)
        if completion(item)["approved"]:
            return {"work_item_id": item["work_item_id"], "version": item["version"], "fingerprint": fp,
                    "approved_by": item["approved_by"], "changed": False}
        _manual_state(db, item)
        claimed = item["done_by"] if item["state"] == "done" else actor
        db.execute("""UPDATE work_items SET state='done',done_by=?,done_at=COALESCE(?,done_at),approved_fingerprint=?,
            approved_by=?,approved_at=?,continued_steps=NULL,version=version+1,updated_at=? WHERE work_item_id=?""",
                   (claimed, None if item["state"] == "done" else now, fp, actor, now, now, item["work_item_id"]))
        _event(ctx, "work_item", item["work_item_id"], "work_item.approved",
               {"fingerprint": fp, "claimed_by": claimed, "note": ctx.params.get("note") or None})
        return {"work_item_id": item["work_item_id"], "version": item["version"] + 1, "fingerprint": fp,
                "approved_by": actor}
    return _once(ctx, change)


async def _run_continue(ctx: OpContext) -> dict:
    def change(db, now):
        item = _check_decide(ctx.service, ctx.target, ctx.params, ctx.preconditions)
        _undecided(item)
        _manual_state(db, item)
        state = "doing" if item["state"] == "done" else item["state"]
        db.execute("""UPDATE work_items SET state=?,done_by=CASE WHEN ?='done' THEN NULL ELSE done_by END,
            done_at=CASE WHEN ?='done' THEN NULL ELSE done_at END,approved_fingerprint=NULL,approved_by=NULL,
            approved_at=NULL,continued_steps=?,version=version+1,updated_at=? WHERE work_item_id=?""",
                   (state, item["state"], item["state"], steps_hash(item["steps"]), now, item["work_item_id"]))
        _event(ctx, "work_item", item["work_item_id"], "work_item.continued",
               {"from": item["state"], "to": state, "note": ctx.params.get("note") or None})
        return {"work_item_id": item["work_item_id"], "version": item["version"] + 1, "state": state}
    return _once(ctx, change)


def _check_link(ops: OperationService, target: dict, params: dict) -> tuple[dict, str, str, bool, str]:
    _only(params, ("kind", "ref", "note", "remove"))
    item = _get_item(ops.db, _target_id(target, "work_item_id", WORK_ITEM_ID), active=True)
    _get_project(ops.db, item["project_id"], active=True)
    remove = params.get("remove", False)
    if not isinstance(remove, bool):
        raise _bad("INVALID_PARAMS", "params.remove must be true or false")
    kind, ref = params.get("kind"), params.get("ref")
    if remove:  # a link whose target is gone can still be removed
        if kind not in LINK_KINDS or not isinstance(ref, str) or not ref:
            raise _bad("INVALID_PARAMS", "params.kind and params.ref name the link to remove")
    else:
        ref = _link_ref(ops, kind, ref)
    note = _text(params.get("note"), "note", NOTE_MAX).strip()
    return item, kind, ref, remove, note


def _admit_link(ops, principal, target, params, pre) -> None:
    item, kind, ref, remove, _ = _check_link(ops, target, params)
    if remove and not _active_link(ops.db, item["work_item_id"], kind, ref):
        raise _bad("NOT_LINKED", "this work item has no such link", 409)


def _active_link(db, wid: str, kind: str, ref: str):
    return db.execute("""SELECT link_id FROM work_item_links WHERE work_item_id=? AND kind=? AND ref=?
        AND removed_at IS NULL""", (wid, kind, ref)).fetchone()


async def _run_link(ctx: OpContext) -> dict:
    actor = ctx.op["actor"]

    def change(db, now):
        item, kind, ref, remove, note = _check_link(ctx.service, ctx.target, ctx.params)
        wid = item["work_item_id"]
        existing = _active_link(db, wid, kind, ref)
        if remove:
            if existing is None:
                raise _bad("NOT_LINKED", "this work item has no such link", 409)
            db.execute("""UPDATE work_item_links SET removed_at=?,removed_by=?,remove_operation=?
                WHERE link_id=?""", (now, actor, ctx.operation_id, existing["link_id"]))
            _event(ctx, "work_item", wid, "work_item.unlinked", {"kind": kind, "ref": ref, "link_id": existing["link_id"]})
            return {"work_item_id": wid, "kind": kind, "ref": ref, "linked": False}
        if existing is not None:
            return {"work_item_id": wid, "kind": kind, "ref": ref, "linked": True, "already": True}
        db.execute("""INSERT INTO work_item_links(work_item_id,kind,ref,note,linked_by,linked_at,link_operation)
            VALUES(?,?,?,?,?,?,?)""", (wid, kind, ref, note or None, actor, now, ctx.operation_id))
        _event(ctx, "work_item", wid, "work_item.linked", {"kind": kind, "ref": ref, "link_id": _active_link(db, wid, kind, ref)["link_id"]})
        return {"work_item_id": wid, "kind": kind, "ref": ref, "linked": True}
    return _once(ctx, change)


# ------------------------------------------------------------------ reads
def _counts(items: list[dict]) -> dict:
    out = {"total": 0, "todo": 0, "doing": 0, "waiting": 0, "awaiting_approval": 0, "done": 0, "pending": 0}
    for x in items:
        c = completion(x)
        out["total"] += 1
        out[c["display_state"]] += 1
        out["pending"] += int(c["pending"])
    return out


def projects_list(db, *, include_archived: bool = False) -> dict:
    """The project tree in display order, with each project's work item counts."""
    projects = _active_projects(db)
    by_project: dict[str, list[dict]] = defaultdict(list)
    for r in db.execute("SELECT * FROM work_items WHERE archived_at IS NULL"):
        by_project[r["project_id"]].append(_item(r))

    def decorate(p: dict) -> dict:
        return {**p, "counts": _counts(by_project.get(p["project_id"], []))}

    out = {"projects": _tree(projects, "projects", db, "project_id", decorate)}
    if include_archived:
        out["archived"] = [_project(r) for r in db.execute(
            "SELECT * FROM projects WHERE archived_at IS NOT NULL ORDER BY archived_at DESC LIMIT ?", (LIST_MAX,))]
    return out


def _project_path(db, project: dict) -> list[dict]:
    path, seen, cur = [], {project["project_id"]}, project["parent_id"]
    while cur and cur not in seen:
        seen.add(cur)
        row = db.execute("SELECT project_id,name,parent_id FROM projects WHERE project_id=?", (cur,)).fetchone()
        if row is None:
            break
        path.insert(0, {"project_id": row["project_id"], "name": row["name"]})
        cur = row["parent_id"]
    return path


def _navigation_item(item):
    state = completion(item)
    return {**{key: item[key] for key in ('work_item_id', 'project_id', 'parent_id', 'derived_from',
            'title', 'state', 'pinned', 'archived', 'version', 'updated_at')},
        'completion': {key: state[key] for key in ('display_state', 'pending', 'approved')},
        'step_summary': {'total': len(item['steps']), 'done': sum(step['done'] for step in item['steps']),
            'next': next((step['text'] for step in item['steps'] if not step['done']), None)}}


def project_get(db, project_id: str, *, include_archived: bool = False, ops=None, principal=None,
                projection: str = 'full') -> dict:
    """One project; navigation omits content that is read on the selected item."""
    if not isinstance(projection, str) or projection not in {'full', 'navigation'}:
        raise _bad('INVALID_PARAMS', 'projection must be full or navigation')
    present = _navigation_item if projection == 'navigation' else _public
    if not isinstance(project_id, str) or not PROJECT_ID.fullmatch(project_id):
        raise _bad("PROJECT_NOT_FOUND", "no such project", 404)
    project = _get_project(db, project_id)
    items = _active_items(db, project_id)
    children = siblings([p for p in _active_projects(db) if p["parent_id"] == project_id],
                        _saved_order(db, "projects", project_id), "project_id")
    out = {"project": {**project, "counts": _counts(items)}, "path": _project_path(db, project),
           "sub_projects": [{"project_id": p["project_id"], "name": p["name"], "pinned": p["pinned"]}
                            for p in children],
           "work_items": _tree(items, _item_scope(project_id), db, "work_item_id", present)}
    if ops is not None and 'project.github.sync' in ops.actions:
        from .board_github import settings
        out['github_sync'] = settings(db, project_id, ops)
    if projection == 'navigation':
        out['projection'] = 'navigation'
    if ops is not None:
        from . import execution_sources, integration
        out["work"] = [integration.candidate_evidence(ops, item) for item in execution_sources.for_project(ops, project)]
        if principal is not None:
            from .pm_operation_loop import execution_work_identity
            for item in out["work"]:
                identity = execution_work_identity(ops, principal, item)
                if identity is not None:
                    item["work_identity"] = identity
    if include_archived:
        out["archived"] = [present(_item(r)) for r in db.execute(
            """SELECT * FROM work_items WHERE project_id=? AND archived_at IS NOT NULL ORDER BY archived_at DESC
            LIMIT ?""", (project_id, LIST_MAX))]
    return out


def work_items_list(db, *, project_id: str | None = None, state: str | None = None, pending: bool | None = None,
                    include_archived: bool = False, limit: int = 50, cursor: str | None = None,
                    principal_id: str | None = None, unread: bool | None = None) -> dict:
    """Work items across projects, most recently changed first (e.g. pending=true: waiting for a person). Pages
    continue from ``next_cursor``: the last row's change time and ID, since a subtree archive or restore gives many
    rows the same time."""
    limit = max(1, min(LIST_MAX, int(limit)))
    if unread is not None and (type(unread) is not bool or not principal_id):
        raise _bad("INVALID_FILTER", "unread must be a boolean with an authenticated reading identity")
    if state is not None and state not in (*STATES, "awaiting_approval"):
        raise _bad("INVALID_FILTER", f"unknown state {state!r}")
    sql = """SELECT w.*, p.name AS project_name FROM work_items w JOIN projects p ON p.project_id=w.project_id
        WHERE 1=1"""
    args: list = []
    if not include_archived:
        sql += " AND w.archived_at IS NULL AND p.archived_at IS NULL"
    if project_id:
        sql, args = sql + " AND w.project_id=?", [*args, project_id]
    if state in STATES and state != "done":
        sql, args = sql + " AND w.state=?", [*args, state]
    elif state in ("done", "awaiting_approval"):
        sql += " AND w.state='done'"
    if cursor:
        at, wid = _cursor(cursor)
        sql += " AND (w.updated_at<? OR (w.updated_at=? AND w.work_item_id>?))"
        args += [at, at, wid]
    sql += " ORDER BY w.updated_at DESC, w.work_item_id"
    out, last, more = [], None, False
    for r in db.execute(sql, args):  # completion is computed, so filter it here, then page
        x = _public(_item(r))
        c = x["completion"]
        if (pending is not None and c["pending"] != pending) or (state and c["display_state"] != state):
            continue
        if principal_id:
            from .work_item_reads import reading
            x["reading"] = reading(db, principal_id, x)
            if unread is not None and x["reading"]["unread"] != unread:
                continue
        if len(out) == limit:
            more = True
            break
        out.append({**x, "project_name": r["project_name"]})
        last = f"{x['updated_at']!r}|{x['work_item_id']}"
    return {"work_items": out, "next_cursor": last if more else None}


def _cursor(value) -> tuple[float, str]:
    at, sep, wid = str(value).partition("|")
    try:
        when = float(at)
    except ValueError:
        when = None
    if not sep or when is None or not WORK_ITEM_ID.fullmatch(wid):
        raise _bad("INVALID_CURSOR", "cursor must be a next_cursor from an earlier page")
    return when, wid


def work_item_get(db, work_item_id: str, *, events: int = 50, principal_id: str | None = None) -> dict:
    """One work item with its completion state, place in the tree, links (with what each points at now) and
    recent history."""
    if not isinstance(work_item_id, str) or not WORK_ITEM_ID.fullmatch(work_item_id):
        raise _bad("WORK_ITEM_NOT_FOUND", "no such work item", 404)
    item = _get_item(db, work_item_id)
    if principal_id:
        from .work_item_reads import reading
        item["reading"] = reading(db, principal_id, item)
    project = _get_project(db, item["project_id"])
    path, seen, cur = [], {work_item_id}, item["parent_id"]
    while cur and cur not in seen:
        seen.add(cur)
        row = db.execute("SELECT work_item_id,title,parent_id FROM work_items WHERE work_item_id=?",
                         (cur,)).fetchone()
        if row is None:
            break
        path.insert(0, {"work_item_id": row["work_item_id"], "title": row["title"]})
        cur = row["parent_id"]
    scope = _item_scope(item["project_id"])
    kids = siblings([_item(r) for r in db.execute("""SELECT * FROM work_items WHERE parent_id=?
        AND archived_at IS NULL ORDER BY created_at, work_item_id""", (work_item_id,))],
                    _saved_order(db, scope, work_item_id), "work_item_id")
    derived = [_item(r) for r in db.execute("""SELECT * FROM work_items WHERE derived_from=? AND archived_at IS NULL
        ORDER BY created_at""", (work_item_id,))]
    source = None
    if item["derived_from"]:
        row = db.execute("SELECT work_item_id,project_id,title,archived_at FROM work_items WHERE work_item_id=?",
                         (item["derived_from"],)).fetchone()
        if row:
            source = {"work_item_id": row["work_item_id"], "project_id": row["project_id"], "title": row["title"],
                      "archived": row["archived_at"] is not None}
    links, removed = [], []
    for r in db.execute("SELECT * FROM work_item_links WHERE work_item_id=? ORDER BY linked_at", (work_item_id,)):
        link = {"kind": r["kind"], "ref": r["ref"], "note": r["note"], "linked_by": r["linked_by"],
                "linked_at": r["linked_at"]}
        if r["removed_at"] is None:
            links.append({**link, "target": _link_summary(db, r["kind"], r["ref"])})
        else:
            removed.append({**link, "removed_by": r["removed_by"], "removed_at": r["removed_at"]})
    history = [{"seq": r["seq"], "kind": r["kind"], "body": json.loads(r["body"]), "actor": r["actor"],
                "created_at": r["created_at"]}
               for r in db.execute("""SELECT * FROM api_events WHERE resource_type='work_item' AND resource_id=?
                   ORDER BY seq DESC LIMIT ?""", (work_item_id, max(0, min(200, int(events)))))]
    brief = lambda x: {"work_item_id": x["work_item_id"], "title": x["title"],  # noqa: E731
                       "display_state": completion(x)["display_state"], "pinned": x["pinned"]}
    return {"work_item": _public(item),
            "project": {"project_id": project["project_id"], "name": project["name"],
                        "archived": project["archived"]},
            "path": path, "children": [brief(x) for x in kids], "derived": [brief(x) for x in derived],
            "derived_from": source, "links": links, "removed_links": removed, "events": history}


def work_items_for(db, kind: str, ref: str) -> list[dict]:
    """Work items that link to a resource (for the session, checkpoint and operation pages)."""
    return [{"work_item_id": r["work_item_id"], "title": r["title"], "project_id": r["project_id"]}
            for r in db.execute("""SELECT w.work_item_id,w.title,w.project_id FROM work_item_links l
                JOIN work_items w ON w.work_item_id=l.work_item_id WHERE l.kind=? AND l.ref=?
                AND l.removed_at IS NULL AND w.archived_at IS NULL ORDER BY l.linked_at""", (kind, ref))]


ACTIONS = [
    ActionDef("project.create", "manage", "Create a project (name, description, parent, repositories)",
              _run_project_create, _admit_project_create),
    ActionDef("project.update", "manage", "Rename, describe, move, archive or restore a project",
              _run_project_update, _admit_project_update, ("project_id",)),
    ActionDef("project.order", "manage", "Reorder the projects under one parent", _run_project_order,
              _admit_project_order),
    ActionDef("project.pin", "manage", "Pin a project above its siblings, or unpin it", _run_project_pin,
              _admit_project_pin, ("project_id",)),
    ActionDef("work_item.create", "manage", "Add a work item to a project", _run_item_create, _admit_item_create,
              ("project_id",)),
    ActionDef("work_item.update", "manage", "Edit, move, set the state of, archive or restore a work item",
              _run_item_update, _admit_item_update, ("work_item_id",)),
    ActionDef("work_item.order", "manage", "Reorder the work items under one parent", _run_item_order,
              _admit_item_order, ("project_id",)),
    ActionDef("work_item.pin", "manage", "Pin a work item above its siblings, or unpin it", _run_item_pin,
              _admit_item_pin, ("work_item_id",)),
    ActionDef("work_item.approve", "approve", "Accept a work item as done, for the content you read",
              _run_approve, _admit_decide, ("work_item_id",)),
    ActionDef("work_item.continue", "manage", "Send a done claim back: the work item is not finished",
              _run_continue, _admit_continue, ("work_item_id",)),
    ActionDef("work_item.link", "manage", "Link a work item to a session, checkpoint, operation, task or PR",
              _run_link, _admit_link, ("work_item_id",)),
]
