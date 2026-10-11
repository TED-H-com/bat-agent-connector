"""Reviewed connector resource cleanup (plan §23, E01/E02).

Preview is a pure read. Apply uses the existing operation journal and policy, keeps a ref before removing a
worktree, and leaves permanent receipts and original-ID aliases. Registry guards also protect stdio tools.
"""
from __future__ import annotations

import asyncio
import base64
import contextvars
import hashlib
import hmac
import json
import os
import posixpath
import re
import shlex
import time
import urllib.error
import urllib.request
from importlib import resources
from pathlib import Path
from urllib.parse import urlencode, urlsplit

from . import (
    checkpoints,
    controller_authority,
    integration,
    lifecycle,
    registry,
    resource_policy,
    service,
    task_cleanup,
    task_control,
)
from .api_auth import SCOPES
from .cleanup_host import temporary_subset as _temporary_subset
from .config import state_dir
from .errors import BatError, ResourceReadOnly
from .operations import (
    RERUN,
    ActionDef,
    AmbiguousOutcome,
    Cancelled,
    NeedsAttention,
    OperationError,
    StepFailed,
    Uncertain,
    _canonical,
)
from .resource_ids import registry_worktree_intent, registry_worktree_root, worktree_id
from .safety import Audit

VERSION = "cleanup-1"
TTL_S = 900
READ_DEADLINE_S = 20.0
MAX_ITEMS = 500
MAX_TOKEN_BYTES = 16384
MUTATING_PHASES = frozenset({"lock.session", "preserve", "discard", "remove.worktree", "remove.temporary", "remove.branch"})
PHASE_EFFECTS = {"preserve": "additive", "stop": "runtime", "discard": "destructive",
                 "remove.worktree": "destructive", "remove.temporary": "destructive", "remove.branch": "destructive"}
_HOST_LOCKS: dict[str, asyncio.Lock] = {}
_REPO_LOCKS: dict[tuple[str, str], asyncio.Lock] = {}
_OWNER = contextvars.ContextVar("cleanup_operation", default=None)
REASONS = {
    "MANUAL_READ_ONLY": "A person created this resource; it is read-only.",
    "UNKNOWN_READ_ONLY": "Creation ownership is not proven.",
    "WORKDIR_NOT_MANAGED": "The workdir is outside the managed roots.",
    "BINDING_MISMATCH": "The resource does not match its creation binding.",
    "FOREIGN_CONTROLLER": "This resource or one of its consumers belongs to another controller.",
    "CONTROLLER_AUTHORITY_REQUIRED": "The original controller authority is not proven.",
    "CONTROLLER_OWNER_UNPROVEN": "The original resource incarnation is not proven.",
    "CONTROLLER_INCARNATION_CHANGED": "The original resource incarnation changed.",
    "CONTROLLER_MEMBERSHIP_CHANGED": "Current PM membership no longer permits this resource mutation.",
    "CLONE_NOT_OURS": "The repository has no matching connector creation markers.",
    "CLONE_CONFIG_TAMPERED": "Repository config or object storage is unsafe.",
    "OBSERVATION_UNAVAILABLE": "Live observation was unavailable: the host is unconfigured or its read deadline expired.",
    "ACTIVE_WRITER": "A session is streaming or writing.",
    "SESSION_WAITING": "A session has a pending question, permission or queued turn.",
    "COMMAND_UNRESOLVED": "A command or external step has an unresolved outcome.",
    "ACTIVE_EXECUTION": "Another execution still needs this resource.",
    "CONTENT_REQUIRED": "An active integration preview or execution needs the content.",
    "TASK_OWNED": "The TaskCoordinator has not authorized this resource; use a task cleanup preview.",
    "UNCOMMITTED_CHANGES": "Uncommitted tracked, staged, untracked or ignored content exists.",
    "RESULTS_NOT_DELIVERED": "Delivery receipts do not cover all result commits.",
    "DELIVERY_UNCERTAIN": "Delivery has an unresolved outcome.",
    "RETENTION_RULE": "An explicit retention rule requires this content.",
    "SHARED_CONTAINER": "This container has shared resources.",
    "RETAINED_CONTENT_STORE": "The repository or pin carries retained content and evidence.",
    "REMOTE_OUT_OF_SCOPE": "Remote branch deletion is a separate action.",
    "RESOURCE_KIND_UNSUPPORTED": "This resource or Git state has no cleanup adapter.",
    "RESOURCE_CLEANED": "This resource generation has a confirmed cleanup tombstone.",
    "CLEANUP_IN_PROGRESS": "A cleanup operation has reserved this resource.",
    "TIER_DISABLED": "Cleanup needs the host write and orchestrate tiers.",
}


def _hash(value):
    return hashlib.sha256(_canonical(value).encode()).hexdigest()


class MutationUncertain(AmbiguousOutcome):
    def __init__(self, result):
        self.result = result
        super().__init__("host mutation may be partial: " + _canonical(result))


def migrate(journal):
    """Add cleanup facts on every open; numbered versions belong to data migrations."""
    db = journal.db
    with journal.tx():
        for ddl in (
            """CREATE TABLE IF NOT EXISTS cleanup_runs (operation_id TEXT PRIMARY KEY REFERENCES operations,
                preview_id TEXT NOT NULL, token_hash TEXT NOT NULL, fingerprint TEXT NOT NULL,
                document TEXT NOT NULL, accepted_actor TEXT NOT NULL, accepted_scopes TEXT NOT NULL,
                accepted_choices TEXT NOT NULL, validated_at REAL NOT NULL, supersedes_operation_id TEXT)""",
            """CREATE TABLE IF NOT EXISTS cleanup_receipts (operation_id TEXT NOT NULL REFERENCES cleanup_runs,
                resource_id TEXT NOT NULL, item_order INTEGER NOT NULL, plan TEXT NOT NULL, status TEXT NOT NULL,
                before_state TEXT NOT NULL, after_state TEXT, attempts INTEGER NOT NULL DEFAULT 0,
                error TEXT, settled_by TEXT, retained_ids TEXT NOT NULL DEFAULT '[]', updated_at REAL NOT NULL,
                PRIMARY KEY(operation_id,resource_id))""",
            """CREATE TABLE IF NOT EXISTS cleanup_retained (retained_id TEXT PRIMARY KEY, resource_id TEXT NOT NULL,
                operation_id TEXT NOT NULL, step TEXT NOT NULL, revision_key TEXT NOT NULL, host TEXT NOT NULL,
                repository TEXT NOT NULL, ref TEXT NOT NULL, commit_sha TEXT NOT NULL, tree_sha TEXT NOT NULL,
                digest TEXT NOT NULL, creation_evidence TEXT NOT NULL, created_at REAL NOT NULL,
                UNIQUE(operation_id,resource_id,revision_key))""",
            """CREATE TABLE IF NOT EXISTS resource_tombstones (resource_id TEXT PRIMARY KEY, generation TEXT NOT NULL,
                host TEXT NOT NULL, kind TEXT NOT NULL, document TEXT NOT NULL, cleaned_at REAL NOT NULL)""",
            """CREATE TABLE IF NOT EXISTS cleanup_aliases (kind TEXT NOT NULL, external_id TEXT NOT NULL,
                host TEXT NOT NULL, resource_id TEXT NOT NULL REFERENCES resource_tombstones,
                PRIMARY KEY(kind,external_id,host,resource_id))""",
            "CREATE INDEX IF NOT EXISTS cleanup_alias_lookup ON cleanup_aliases(external_id,host)",
            "CREATE INDEX IF NOT EXISTS cleanup_history_order ON resource_tombstones(cleaned_at,resource_id)",
        ):
            db.execute(ddl)


def _registry_document():
    path = registry.registry_path()
    try:
        d = json.loads(path.read_text())
    except FileNotFoundError:
        return {"sessions": [], "cleanup_guards": {}}
    except (OSError, ValueError):
        raise ResourceReadOnly("CLEANUP_IN_PROGRESS", "cleanup guard registry is unreadable") from None
    if (not isinstance(d, dict) or not isinstance(d.get("sessions", []), list) or
            not isinstance(d.get("cleanup_guards", {}), dict)):
        raise ResourceReadOnly("CLEANUP_IN_PROGRESS", "cleanup guard registry is invalid")
    registry._validate_unique(d.get("sessions", []))
    return d


def guard(host=None, *, session_id=None, path=None, branch=None, writer=True):
    """One refusal helper for every process; it never grants ownership and never creates a file."""
    if writer:
        from .worktree_merge_operations import check_writer
        check_writer(host, session_id, workdir=path)
    for g in _registry_document().get("cleanup_guards", {}).values():
        if host is not None and g.get("host") != host:
            continue
        # A stopped session and a deleted branch do not retire their carrier worktree/repository.
        kind = g.get("kind")
        matches = session_id and session_id in g.get("session_ids", [])
        if kind == "local_branch":
            matches = matches or (branch and branch == g.get("branch") and path == g.get("path"))
        elif kind != "session":
            matches = matches or (path and path == g.get("path"))
        if not matches:
            continue
        if g["status"] == "cleaned":
            raise ResourceReadOnly("RESOURCE_CLEANED", "resource was cleaned; see /api/v1/cleanup-tombstones")
        if g["operation_id"] != _OWNER.get():
            raise ResourceReadOnly("CLEANUP_IN_PROGRESS", "resource is reserved by " + g["operation_id"])


def _mark(item, op_id, status, *, ops=None):
    path = registry.registry_path()
    with registry._locked(path):
        d = _registry_document()
        guards = d.setdefault("cleanup_guards", {})
        old = guards.get(item["resource_id"])
        if old and old["operation_id"] == op_id and old["status"] == status:
            return
        if old and old["operation_id"] != op_id:
            raise ResourceReadOnly("CLEANUP_IN_PROGRESS", "resource has another cleanup owner")
        guard(item["host"], session_id=item.get("session_id"), path=item.get("path"), branch=item.get("branch"))
        if status == "reserved" and item.get("task_cleanup"):
            task_cleanup.check(ops, item)
        sessions = {item["session_id"]} if item.get("session_id") else set()
        if item["kind"] != "session" and item.get("path"):
            sessions.update(e["session_id"] for e in d.get("sessions", []) if e.get("host") == item["host"]
                            and (e.get("worktree_path") or e.get("cwd")) == item["path"])
        for sid in sorted(sessions):
            registry.refuse_start_claim(path, item["host"], sid)
        guards[item["resource_id"]] = {
            "operation_id": op_id, "status": status, "host": item["host"], "kind": item["kind"], "path": item.get("path"),
            "branch": item.get("branch"), "generation": item["generation"],
            "session_ids": [item["session_id"]] if item.get("session_id") else [],
        }
        for e in d.get("sessions", []):
            if e.get("host") == item["host"] and e.get("session_id") == item.get("session_id"):
                # Settled effects still get receipts after ownership changes, but
                # bookkeeping must never alter a newer or foreign registry row.
                try:
                    original = controller_authority.require_entry(item.get("registry"))
                    controller_authority.require_entry(e,
                        expected_incarnation=original["controller_incarnation"])
                except ResourceReadOnly:
                    continue
                if status == "cleaned" and item.get("task_cleanup"):
                    bound = next((b for b in item["task_cleanup"]["binding"]["sessions"]
                                  if b["session_id"] == e["session_id"]), None)
                    if not bound or any(e.get(k) != v for k, v in bound.items()):
                        continue
                e["cleanup_reservation"] = op_id if status == "reserved" else None
                if status == "cleaned":
                    e.update(status="cleaned", tombstone_resource_id=item["resource_id"])
        registry._write_document(path, d)


def _release(item, op_id):
    path = registry.registry_path()
    with registry._locked(path):
        d = _registry_document()
        old = d.get("cleanup_guards", {}).get(item["resource_id"])
        if old and old.get("status") == "reserved" and old["operation_id"] == op_id:
            del d["cleanup_guards"][item["resource_id"]]
            for e in d.get("sessions", []):
                if (e.get("cleanup_reservation") == op_id and e.get("host") == item["host"]
                        and e.get("session_id") == item.get("session_id")):
                    try:
                        original = controller_authority.require_entry(item.get("registry"))
                        controller_authority.require_entry(e, expected_incarnation=original["controller_incarnation"])
                    except ResourceReadOnly:
                        continue
                    e["cleanup_reservation"] = None
            registry._write_document(path, d)


def install(ops, admin_token):
    ops.context["cleanup_key"] = hmac.digest(admin_token.encode(), b"batc.cleanup.preview.v1", "sha256")
    for action in ACTIONS:
        if action.name not in ops.actions:
            ops.register(action)


def _b64(data):
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def decode_token(ops, token, *, allow_expired=False):
    try:
        version, raw, sig = token.split(".")
        if version != "v1" or len(token) > MAX_TOKEN_BYTES * 2:
            raise ValueError()
        payload = base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4))
        if _b64(payload) != raw:
            raise ValueError()
        expected = hmac.digest(ops.context["cleanup_key"], b"v1." + payload, "sha256")
        if not hmac.compare_digest(_b64(expected), sig):
            raise ValueError()
        data = json.loads(payload)
        if _canonical(data).encode() != payload or data["exp"] != data["iat"] + TTL_S:
            raise ValueError()
    except (ValueError, TypeError, KeyError, AttributeError):
        raise OperationError("PREVIEW_TOKEN_INVALID", "invalid cleanup preview token", 409) from None
    if not allow_expired and time.time() >= data["exp"]:
        raise OperationError("PREVIEW_EXPIRED", "cleanup preview expired; preview again", 409)
    return data


def _target(ops, value):
    if not isinstance(value, dict):
        raise OperationError("INVALID_TARGET", "target must be an object", 422)
    kind = value.get("kind")
    if not isinstance(kind, str):
        raise OperationError("INVALID_TARGET", "target kind must be a string", 422)
    key = {"work_item": "work_item_id", "checkpoint": "checkpoint_id", "integration": "operation_id",
           "host": "host", "task": "task_id"}.get(kind)
    if not key or set(value) - {"kind", key, "include_children"} or not isinstance(value.get(key), str):
        raise OperationError("INVALID_TARGET", "choose work_item, checkpoint, integration, host or task", 422)
    if "include_children" in value and (kind != "work_item" or not isinstance(value["include_children"], bool)):
        raise OperationError("INVALID_TARGET", "include_children applies only to work_item", 422)
    if kind == "host":
        if value[key] not in ops.context["fleet"].config.hosts:
            known = any(e.get("host") == value[key] for e in registry.list_entries()) or any(
                ops.db.execute(f"SELECT 1 FROM {table} WHERE host=? LIMIT 1", (value[key],)).fetchone()  # noqa: S608 - fixed table names
                for table in ("checkpoints", "checkpoint_runs", "integration_previews", "tasks", "sessions_observed"))
            if not known:
                # A failed prepare can leave creation facts before a preview/history row exists.
                known = any(i["host"] == value[key] for i in _all(ops)[0].values())
            if not known:
                raise OperationError("UNKNOWN_HOST", "host has no configuration or resource history", 404)
    else:
        table = {"work_item": "work_items", "checkpoint": "checkpoints", "integration": "operations", "task": "tasks"}[kind]
        row = ops.db.execute(f"SELECT * FROM {table} WHERE {key}=?", (value[key],)).fetchone()  # noqa: S608
        if not row or (kind == "integration" and not row["action"].startswith("integration.")):
            raise OperationError("NOT_FOUND", "cleanup target was not found", 404)
    return {"kind": kind, key: value[key], **({"include_children": value.get("include_children", False)}
                                              if kind == "work_item" else {})}


def _choices(value, principal=None):
    value = value or {}
    keys = {"discard_uncommitted", "release_undelivered"}
    if not isinstance(value, dict) or set(value) - keys:
        raise OperationError("INVALID_PARAMS", "unknown cleanup choice", 422)
    out = {}
    for key in sorted(keys):
        ids = value.get(key, [])
        if (not isinstance(ids, list) or len(ids) > MAX_ITEMS or
                not all(isinstance(x, str) and re.fullmatch(r"(?:cr|wt)_[0-9a-f]{32}", x) for x in ids)):
            raise OperationError("INVALID_PARAMS", key + " must contain resource IDs", 422)
        out[key] = sorted(set(ids))
    if out["discard_uncommitted"] and principal and not principal.allows("cleanup_discard"):
        raise OperationError("DISCARD_SCOPE_REQUIRED", "discard_uncommitted needs cleanup_discard", 403)
    return out


def _config(ops):
    cfg = ops.context["fleet"].config
    return _hash({"version": VERSION, "hosts": {k: {"roots": h.managed_roots, "profile": h.profile_id,
        "writes": h.writes, "orchestrate": h.orchestrate} for k, h in cfg.hosts.items()},
        "retention": vars(cfg.cleanup)})


def _resource(host, kind, intent, slot, **fields):
    generation = _hash([host, kind, intent, slot])
    return {"resource_id": "cr_" + generation[:32], "generation": generation, "kind": kind, "host": host,
            "creation_evidence": {"intent": intent, "slot": slot}, "original_ids": [], "relations": [],
            "consumers": [], "reasons": [], "overridden_reasons": [], "steps": [], "dependencies": [],
            **fields}


def _reason(item, code, **evidence):
    fact = {"code": code, "message": REASONS.get(code, code), **evidence}
    if fact not in item["reasons"]:
        item["reasons"].append(fact)


def _all(ops):
    """Project resource identities from authoritative creation facts, not folder-name ownership."""
    db, fleet = ops.db, ops.context["fleet"]
    regs = registry.list_entries()
    op_rows = [ops._decode(r) for r in db.execute("SELECT * FROM operations ORDER BY created_at")]
    cps = {r["checkpoint_id"]: dict(r) for r in db.execute("SELECT * FROM checkpoints")}
    pvs = {r["preview_id"]: dict(r) for r in db.execute("SELECT * FROM integration_previews")}
    items, worktrees, containers = {}, {}, {}

    def add(item):
        old = items.setdefault(item["resource_id"], item)
        for k in ("original_ids", "relations"):
            old[k] = sorted(set(old[k] + item[k]))
        return old

    def alias(item, *ids):
        item["original_ids"] = sorted(set(item["original_ids"] + [s for s in ids if s]))

    def wt(host, repo, path, branch, intent, flavor, base, ids, source=None):
        if not path or not repo:
            return None
        key = host, path
        if key in worktrees:
            item = worktrees[key]
            alias(item, *ids)
            if (repo, branch) != (item["repository"], item["branch"]):
                _reason(item, "BINDING_MISMATCH")
            return item
        intent_type, slot = {"checkpoint": ("checkpoint.continue", "worktree"),
                             "repair": ("integration.handoff", "repair"),
                             "task": ("task", "external_worktree"), "bat": ("registry", "worktree"),
                             "published": ("repository.continue", "worktree")}[flavor]
        projected = _resource(host, "worktree", intent, slot, path=path, repository=repo, branch=branch,
                              flavor=flavor, base=base, source=source, proven=True)
        projected["resource_id"] = worktree_id(host, intent_type, intent, slot)
        projected["generation"] = _hash(["worktree", host, intent_type, intent, slot])
        projected["creation_evidence"].update(intent_type=intent_type)
        if branch:
            # A branch's observation is projected later, but its creation slot already fixes its ID/host.
            projected["branch_id"] = _resource(host, "local_branch", intent, branch)["resource_id"]
        item = add(projected)
        alias(item, *ids)
        worktrees[key] = item
        ck = host, repo
        if ck not in containers:
            container = add(_resource(host, "integration_area" if flavor == "repair" else "clone", intent,
                                      repo, path=repo, repository=repo, proven=True, source=source))
            containers[ck] = container
        alias(containers[ck], *ids)
        return item

    from .repository_sync import carriers as published_carriers
    for plan in published_carriers(ops, op_rows):
        host = plan["target"]["host"]
        w = wt(host, plan["clone_path"], plan["worktree_path"], plan["branch"], plan["operation_id"],
               "published", plan["source_sha"], [plan["operation_id"], host + "/" + plan["session_id"]], source=plan["markers"])
        w["proven"] = plan["carrier_proven"]
        current = next((e for e in regs if e.get("host") == host and e.get("session_id") == plan["session_id"]), None)
        if current and (current.get("start_operation_id") != plan["operation_id"] or
                        current.get("repository_binding") != plan["binding_digest"]):
            _reason(w, "BINDING_MISMATCH")
    for r in db.execute("SELECT * FROM checkpoint_runs ORDER BY created_at"):
        cp = cps[r["checkpoint_id"]]
        wt(r["host"], r["clone_path"], r["worktree_path"], r["branch"], r["operation_id"], "checkpoint",
           cp["commit_sha"], [r["checkpoint_id"], r["operation_id"], r["host"] + "/" + r["session_id"]],
           cp["repo_root"])
    for op in op_rows:
        if op["action"] == "checkpoint.continue":
            refs = op.get("external_refs") or {}
            cp = cps.get(op["target"].get("checkpoint_id"))
            prepare = db.execute("SELECT request FROM operation_steps WHERE operation_id=? AND name='worktree.prepare'",
                                 (op["operation_id"],)).fetchone()
            repo = refs.get("clone_path")
            if cp and repo and prepare:
                temp = repo + ".batc-tmp-" + op["operation_id"][3:15]
                item = add(_resource(cp["host"], "temporary", op["operation_id"], temp,
                    path=temp, repository=repo, source=cp["repo_root"], proven=True,
                    original_ids=[op["operation_id"], cp["checkpoint_id"]]))
                add(_resource(cp["host"], "temporary", "container-lock:" + repo, repo + ".batc-lock",
                    path=repo + ".batc-lock", repository=repo, proven=False,
                    original_ids=[op["operation_id"], cp["checkpoint_id"]]))
    for op in op_rows:
        refs = op.get("external_refs") or {}
        host = refs.get("host") or op["target"].get("host")
        if op["action"] == "checkpoint.continue":
            cp = cps.get(op["target"].get("checkpoint_id"))
            if cp:
                host = cp["host"]
                wt(host, refs.get("clone_path"), refs.get("worktree_path"), refs.get("branch"),
                   op["operation_id"], "checkpoint", cp["commit_sha"],
                   [cp["checkpoint_id"], op["operation_id"], host + "/" + refs["session_id"]
                    if refs.get("session_id") else None], cp["repo_root"])
        if op["action"] == "integration.handoff":
            apply_id = op["target"].get("operation_id")
            ar = db.execute("SELECT * FROM integration_receipts WHERE operation_id=? AND seq=?",
                            (apply_id, refs.get("seq", op["params"].get("seq", 1)))).fetchone()
            pv = pvs.get(ar["preview_id"]) if ar else None
            if pv:
                wt(pv["host"], pv["area_path"], refs.get("worktree_path"), refs.get("branch"),
                   op["operation_id"], "repair", ar["integrated_sha"] or ar["pinned_sha"],
                   [op["operation_id"], apply_id, pv["preview_id"],
                    pv["host"] + "/" + refs["session_id"] if refs.get("session_id") else None],
                   {"host": pv["host"], "repository": pv["repository"], "remote-url": pv["remote_url"]})
        # A failed preview may not have produced integration_previews yet, but its prepare intent is durable.
        if op["action"] == "integration.preview" and host:
            step = db.execute("SELECT request FROM operation_steps WHERE operation_id=? AND name='prepare'",
                              (op["operation_id"],)).fetchone()
            area = refs.get("area_path") or (json.loads(step[0]).get("area") if step else None)
            if area and (host, area) not in containers:
                repo = fleet.config.github.repos.get(op["target"].get("repository", "").lower())
                source = {"host": host, "repository": repo.repository, "remote-url": repo.integrate.remote_url} if repo and repo.integrate else None
                item = add(_resource(host, "integration_area", op["operation_id"], area, path=area,
                                     repository=area, proven=bool(source), source=source))
                containers[host, area] = item
                alias(item, op["operation_id"])
    for pv in pvs.values():
        for source in json.loads(pv["sources"]):
            if source["kind"] == "branch":
                remote = add(_resource(pv["host"], "remote", "remote:" + pv["repository"], source["id"],
                    proven=False, ref=source.get("ref"), original_ids=[source["id"], pv["operation_id"], pv["preview_id"]]))
                remote["remote"] = True
        ck = pv["host"], pv["area_path"]
        if ck not in containers:
            containers[ck] = add(_resource(pv["host"], "integration_area", pv["operation_id"], pv["area_path"],
                path=pv["area_path"], repository=pv["area_path"], proven=True,
                source={"host": pv["host"], "repository": pv["repository"], "remote-url": pv["remote_url"]}))
        containers[ck].setdefault("source", {"host": pv["host"], "repository": pv["repository"], "remote-url": pv["remote_url"]})
        alias(containers[ck], pv["operation_id"], pv["preview_id"])
    for op in op_rows:
        if op["action"] not in {"integration.preview", "integration.apply"}:
            continue
        prepare = db.execute("SELECT request FROM operation_steps WHERE operation_id=? AND name='prepare'",
                             (op["operation_id"],)).fetchone()
        if not prepare:
            continue
        host = op["target"].get("host")
        pv = pvs.get(op["params"].get("preview_id"))
        area = (pv or {}).get("area_path") or json.loads(prepare[0]).get("area")
        container = containers.get((host, area))
        if container:
            temp = area + "/repo.git.batc-tmp-" + op["operation_id"][3:15]
            add(_resource(host, "temporary", op["operation_id"], temp, path=temp, repository=area,
                source=container.get("source"), proven=bool(container.get("source")),
                original_ids=[op["operation_id"], *(([pv["preview_id"]]) if pv else [])]))
            for step_prefix, name in (("check.", "batc-check-"), ("push.", "batc-push-")):
                if db.execute("SELECT 1 FROM operation_steps WHERE operation_id=? AND name LIKE ?",
                              (op["operation_id"], step_prefix + "%")).fetchone():
                    path = area + "/repo.git/" + name + op["operation_id"][3:15]
                    add(_resource(host, "temporary", op["operation_id"], path, path=path, repository=area,
                                  proven=False, original_ids=[op["operation_id"], *(([pv["preview_id"]]) if pv else [])]))
    for task in db.execute("SELECT * FROM tasks ORDER BY submitted_at"):
        path = task["external_worktree_path"]
        if path:
            w = wt(task["host"], posixpath.dirname(posixpath.dirname(path)), path,
                   task["external_branch"], task["task_id"], "task", task["base_commit"], [task["task_id"]])
            if w:
                w["task_owned"] = True
    for historical_item, _, _ in task_cleanup.historical(ops):
        path = historical_item["path"]
        if (historical_item["host"], path) not in worktrees:
            w = wt(historical_item["host"], historical_item["repository"], path, historical_item["branch"],
                   historical_item["creation_evidence"]["intent"], "task", historical_item["base"], historical_item["original_ids"])
            w.update(task_owned=True, historical_cleanup=True)
    from .start_resources import carriers
    for created in carriers(ops, op_rows, regs):
        host, path = created["host"], created["path"]
        w = wt(host, created["repository"], path, created["branch"], created["intent"], "bat", created["base"],
               [created["operation_id"], host + "/" + created["session_id"]])
        hc = fleet.config.hosts.get(host)
        w["proven"] = bool(hc and resource_policy.in_managed_root(hc, created["repository"])
                           and resource_policy.in_bat_worktrees(path, created["repository"]))
        w["creation_evidence"]["start_operation_id"] = created["operation_id"]
        if created["binding_mismatch"]:
            _reason(w, "BINDING_MISMATCH", detail="standalone start registry incarnation changed")
    for e in regs:
        host, sid = e.get("host"), e.get("session_id")
        if not host or not sid:
            continue
        creation = f"{sid}@{e.get('created_at')}"
        path = e.get("worktree_path") or e.get("cwd") or e.get("origin_cwd")
        task_id = task_control.owner_task(fleet, host, sid)
        session = add(_resource(host, "session", creation, sid, session_id=sid, path=path, registry=e,
                               proven=bool(e.get("created_at")), task_owned=bool(task_id)))
        alias(session, sid, host + "/" + sid, task_id)
        def lead_of(task_id, host=host):
            row = db.execute("SELECT session_id,external_worktree_path FROM tasks WHERE task_id=? AND host=?",
                             (task_id, host)).fetchone()
            return {"session_id": row[0], "worktree_path": row[1]} if row else None

        intent = registry_worktree_intent(regs, host, sid, lead_of)
        root = registry_worktree_root(regs, host, sid, lead_of) if intent else None
        if root and (host, root["worktree_path"]) not in worktrees:
            # A successful legacy BAT create/start records its origin root, branch and worktree path together.
            # The shared resolver already excludes connector-made roots/current rows,
            # including legacy batc/ branches, and follows proven reuse relationships.
            recorded = bool(root.get("created_at") and root.get("branch") and root.get("origin_root") and
                            root.get("status") in {"active", "starting", "uncertain", "superseded", "removed", "cleaned", *registry.RETIRED})
            hc = fleet.config.hosts.get(host)
            proven = bool(recorded and hc and resource_policy.in_managed_root(hc, root["origin_root"]) and
                          resource_policy.in_bat_worktrees(root["worktree_path"], root["origin_root"]))
            # Project the carrier from this creation record, even without a checkpoint/integration/task.
            # Out-of-root or unexpected-layout records remain visible, but confer no cleanup ownership.
            if recorded or root.get("task_id"):
                w = wt(host, root.get("origin_root") or root.get("origin_cwd"), root["worktree_path"], root.get("branch"),
                       intent[1], "bat", root.get("start_commit") or root.get("base_commit"), [host + "/" + sid, task_id])
                if w:
                    w["proven"] = proven
        w = worktrees.get((host, root["worktree_path"] if root else path))
        if w:
            alias(w, sid, host + "/" + sid, task_id)
            w["task_owned"] = w.get("task_owned", False) or bool(task_id)
            session["worktree_id"] = w["resource_id"]
            session["repository"] = w["repository"]
    # Include manual/unknown inventory and task leftovers even when the registry is incomplete.
    for r in db.execute("SELECT host,session_id,body,provenance FROM sessions_observed"):
        body = json.loads(r["body"])
        if any(i.get("session_id") == r["session_id"] and i["host"] == r["host"] for i in items.values()):
            continue
        task_id = task_control.owner_task(fleet, r["host"], r["session_id"])
        item = add(_resource(r["host"], "session", "observed:" + r["session_id"], r["session_id"],
                             session_id=r["session_id"], path=body.get("cwd"), proven=False,
                             provenance=r["provenance"], task_owned=bool(task_id)))
        alias(item, r["host"] + "/" + r["session_id"], task_id)
    for task in db.execute("SELECT * FROM tasks"):
        host, path = task["host"], task["external_worktree_path"]
        if path and (host, path) not in worktrees:
            repo = posixpath.dirname(posixpath.dirname(path))
            w = wt(host, repo, path, task["external_branch"], task["task_id"], "task", task["base_commit"],
                   [task["task_id"]])
            if w:
                w["task_owned"] = True
        for i in items.values():
            if path and i["host"] == host and i.get("path") == path:
                i["task_owned"] = True
                alias(i, task["task_id"])
    for item in items.values():
        if item["kind"] in {"session", "worktree"}:
            owner_ids, _, _ = task_cleanup.owners(ops, item, regs)
            if owner_ids:
                item["task_owned"] = True
                alias(item, *owner_ids)
    links = [dict(r) for r in db.execute("SELECT * FROM work_item_links")]
    # A recorded branch has an identity even when its host/HEAD cannot be observed.
    for w in worktrees.values():
        if w.get("branch"):
            add(_resource(w["host"], "local_branch", w["creation_evidence"]["intent"], w["branch"],
                          path=w["repository"], repository=w["repository"], branch=w["branch"], proven=w.get("proven", False),
                          flavor=w.get("flavor"), base=w.get("base"), original_ids=w["original_ids"],
                          task_owned=w.get("task_owned", False), worktree_id=w["resource_id"], content_path=w["path"]))
    for i in items.values():
        i["relations"] = sorted({r["work_item_id"] for r in links if r["ref"] in i["original_ids"]})
        i["controller_source"] = (items[i["worktree_id"]].get("controller_source")
                                  if i["kind"] == "local_branch" else _controller_source(i, regs))
    return items, op_rows, pvs, worktrees, containers, links


def _controller_source(item, entries):
    """Name the original creator record; paths and incidental consumers confer no authority."""
    evidence = item["creation_evidence"]
    intent = evidence["intent"]
    if item["kind"] == "session":
        entry = item.get("registry") or {}
    elif item.get("flavor") == "task":
        return {"kind": "task", "task_id": intent}
    elif str(evidence.get("start_operation_id") or intent).startswith("op_"):
        return {"kind": "operation", "operation_id": evidence.get("start_operation_id") or intent}
    else:
        entry = next((e for e in entries if e.get("host") == item["host"]
                      and f"{e.get('session_id')}@{e.get('created_at')}" == intent), {})
    if not entry:
        return None
    return {"kind": "registry", "host": item["host"], "session_id": entry.get("session_id"),
            "created_at": entry.get("created_at"), "controller_authority": entry.get("controller_authority"),
            "controller_incarnation": entry.get("controller_incarnation")}


def _require_resource_controller(ops, item):
    source = item.get("controller_source") or {}
    if source.get("kind") == "registry":
        controller_authority.require_entry(source)
        row = controller_authority.require_entry(registry.get(source["host"], source["session_id"]),
            expected_incarnation=source["controller_incarnation"])
        if row.get("created_at") != source.get("created_at"):
            raise ResourceReadOnly("CONTROLLER_RESOURCE_CHANGED", "original cleanup resource incarnation changed")
    elif source.get("kind") == "operation":
        original = ops._row(source["operation_id"])
        controller_authority.require_binding(controller_authority.operation_binding(original) if original else None)
    elif source.get("kind") == "task":
        task_control.require_authority(ops.journal.get(source["task_id"]))
    else:
        controller_authority.require_binding(None)

    # A shared carrier remains shared after another controller's session ends.
    # The path locates consumers only; it never supplies their ownership proof.
    path = item.get("content_path") or item.get("path")
    if path and item["kind"] in {"session", "worktree", "local_branch", "temporary"}:
        for row in registry.list_entries(item["host"]):
            if _inside(path, row.get("worktree_path") or row.get("cwd")):
                controller_authority.require_entry(row)


def _selection(ops, target, items, pvs, links, op_rows):
    kind = target["kind"]
    if kind == "host":
        return {rid for rid, i in items.items() if i["host"] == target["host"]}, []
    key = target.get("checkpoint_id") or target.get("operation_id") or target.get("work_item_id") or target.get("task_id")
    refs, wi_ids = {key}, []
    if kind == "work_item":
        wi_ids = [key]
        if target["include_children"]:
            todo = [key]
            while todo:
                parent = todo.pop()
                children = [r[0] for r in ops.db.execute("SELECT work_item_id FROM work_items WHERE parent_id=?", (parent,))
                            if r[0] not in wi_ids]
                wi_ids.extend(children)
                todo.extend(children)
        refs.update(r["ref"] for r in links if r["work_item_id"] in wi_ids)
    operations = {op["operation_id"]: op for op in op_rows}
    todo = list(refs)
    while todo:
        op = operations.get(todo.pop())
        if not op:
            continue
        related = (op["params"].get("preview_id") if op["action"] == "integration.apply" else
                   op["target"].get("operation_id") if op["action"] == "integration.handoff" else None)
        if isinstance(related, str) and related not in refs:
            refs.add(related)
            todo.append(related)
    for pv in pvs.values():
        if pv["operation_id"] in refs or pv["preview_id"] in refs:
            refs.update((pv["operation_id"], pv["preview_id"]))
            refs.update(s["id"] for s in json.loads(pv["sources"]))
            refs.update(s["worktree_id"] for s in json.loads(pv["sources"]) if s.get("worktree_id"))
    selected = {rid for rid, i in items.items() if rid in refs or i.get("worktree_id") in refs or refs.intersection(i["original_ids"]) or
                set(wi_ids).intersection(i["relations"])}
    # The source of a checkpoint is always listed read-only, even when not in inventory.
    cp_ids = {r for r in refs if r.startswith("cp_")}
    for cp_id in cp_ids:
        cp = ops.db.execute("SELECT * FROM checkpoints WHERE checkpoint_id=?", (cp_id,)).fetchone()
        if cp:
            item = _resource(cp["host"], "source", "manual:" + cp["repo_root"], cp["repo_root"], path=cp["repo_root"], proven=False,
                             provenance="manual", original_ids=[cp_id, cp["host"] + "/" + cp["source_session_id"]])
            old = items.setdefault(item["resource_id"], item)
            old["original_ids"] = sorted(set(old["original_ids"] + item["original_ids"]))
            selected.add(item["resource_id"])
    # Opaque or remote links remain findable; they are never interpreted as paths.
    for r in links:
        if r["work_item_id"] in wi_ids and not any(r["ref"] in i["original_ids"] for i in items.values()):
            item = _resource("", "artifact", "link:" + str(r["link_id"]), r["ref"], proven=False,
                             original_ids=[r["ref"]], relations=[r["work_item_id"]])
            items[item["resource_id"]] = item
            selected.add(item["resource_id"])
    selected.update(i["resource_id"] for i in items.values() if i.get("worktree_id") in selected and i["kind"] == "session")
    return selected, wi_ids


def _host_config(ops, host):
    hc = ops.context["fleet"].config.hosts.get(host)
    if hc is None:
        raise OperationError("OBSERVATION_UNAVAILABLE", "host is no longer configured; resource stays read-only", 409)
    return hc


async def _host_call(ops, host, req, timeout=READ_DEADLINE_S, *, locked_check=None, locked_action=None):
    if req.get("phase") in MUTATING_PHASES and not req.get("probe") and locked_check is None:
        raise OperationError("CLEANUP_GATE_REQUIRED", "a mutating phase requires the locked consumer check", 409)
    _host_config(ops, host)
    runner = ops.context.get("git_runner")
    if not runner or not runner.available(host):
        raise OperationError("GIT_RUNNER_UNAVAILABLE", "no SSH runner for host", 409)
    source = (resources.files(__package__) / "cleanup_host.py").read_text()
    arg = base64.b64encode(_canonical({**req, "deadline_s": timeout, **({"locked_check": True} if locked_check else {})}).encode()).decode()
    checked = False
    action_refusal = None
    async def check():
        nonlocal checked, action_refusal
        await locked_check()
        checked = True
        if locked_action:
            try:
                await locked_action()
            except OperationError as e:
                # A local, explicit pre-stop refusal is different from losing the BAT stop reply.
                action_refusal = e
                raise
    token = checkpoints._LOCKED_CHECK.set(check if locked_check else None)
    try:
        try:
            result = json.loads(await runner.run(host, "python3 -c " + shlex.quote(source) + " " + shlex.quote(arg),
                                                timeout_s=max(0.01, timeout)))
            if not isinstance(result, dict) or ("error" in result) == ("result" in result):
                raise ValueError("invalid host reply envelope")
            if "error" in result:
                if not isinstance(result["error"], str) or not result["error"] or (
                        "mutated" in result and not isinstance(result["mutated"], bool)):
                    raise ValueError("invalid host refusal")
                if "effects" in result and (not isinstance(result["effects"], list) or any(not isinstance(e, dict) or
                        not isinstance(e.get("action"), str) or not isinstance(e.get("target"), str) or
                        not isinstance(e.get("completed"), bool) for e in result["effects"])):
                    raise ValueError("invalid host mutation evidence")
            elif req.get("phase") in MUTATING_PHASES and not req.get("probe"):
                _check_phase_result(req["phase"], result["result"])
        except (Exception, asyncio.CancelledError) as e:
            if e is action_refusal:
                raise
            if checked:
                raise MutationUncertain({"error": "CLEANUP_HOST_PROTOCOL_UNCERTAIN", "mutated": True,
                                         "effects": [], "failure": type(e).__name__}) from e
            if locked_check and not isinstance(e, (OperationError, ResourceReadOnly, Cancelled, asyncio.CancelledError)):
                # _run_locked closes stdin on refusal; mutate waits for explicit proceed before any write.
                raise OperationError("CLEANUP_HOST_REFUSED", "host exchange failed before permission: " + type(e).__name__, 409) from e
            raise
    finally:
        checkpoints._LOCKED_CHECK.reset(token)
    if "error" in result:
        if result.get("mutated") is True:
            raise MutationUncertain(result)
        raise OperationError(result["error"], "host cleanup check refused", 409)
    if locked_check and not checked:
        raise OperationError("CLEANUP_HOST_REFUSED", "host did not receive permission to mutate", 409)
    return result["result"]


def _check_phase_result(phase, result):
    if not isinstance(result, dict):
        raise ValueError("invalid host phase result")
    flag = {"lock.session": "released", "discard": "discarded", "remove.worktree": "removed",
            "remove.temporary": "removed", "remove.branch": "deleted"}.get(phase)
    if flag and result.get(flag) is not True:
        raise ValueError("missing host completion flag")
    if phase == "preserve":
        pins = result.get("pins", [result])
        if not isinstance(pins, list) or not pins or any(not isinstance(p, dict) or
                any(not isinstance(p.get(k), str) or not p[k] for k in ("ref", "sha", "tree")) for p in pins):
            raise ValueError("invalid retained pin result")
        if any(not isinstance(result.get(k), str) or not result[k] for k in ("ref", "sha", "tree")):
            raise ValueError("missing retained result")
    if phase == "discard" and (not isinstance(result.get("acknowledged_missing_replicas", []), list) or
            any(not isinstance(p, str) for p in result.get("acknowledged_missing_replicas", []))):
        raise ValueError("invalid discard result")
    if phase == "discard" and (not isinstance(result.get("after"), dict) or
            not isinstance(result["after"].get("manifest"), list) or not isinstance(result["after"].get("status"), str)):
        raise ValueError("missing post-discard state")


def _inside(path, cwd):
    return (isinstance(path, str) and path.startswith("/") and isinstance(cwd, str) and cwd.startswith("/") and
            posixpath.commonpath((posixpath.normpath(path), posixpath.normpath(cwd))) == posixpath.normpath(path))


async def _terminal_observations(ops, host, deadline):
    fleet = ops.context["inventory"].fleet
    if host not in ops.context["fleet"].config.hosts or host not in fleet.config.hosts:
        return [{"session_id": None, "error": "OBSERVATION_UNAVAILABLE"}]
    client = fleet.client(host)
    async def bounded(awaitable):
        return await asyncio.wait_for(awaitable, max(.001, deadline - time.monotonic()))
    try:
        workspace = await bounded(service._workspace(client))
        if not isinstance(workspace.get("terminals"), list):
            raise ValueError("terminals unavailable")
    except (BatError, OSError, asyncio.TimeoutError, ValueError):
        return [{"session_id": None, "error": "OBSERVATION_UNAVAILABLE"}]
    observed = []
    for terminal in workspace["terminals"]:
        sid = terminal.get("id") if isinstance(terminal, dict) else None
        fact = {"session_id": sid}
        try:
            if not sid or time.monotonic() >= deadline:
                raise ValueError("terminal unreadable")
            meta = await bounded(service._meta(client, sid))
            cwd = (meta or {}).get("cwd")
            if not isinstance(cwd, str) or not cwd.startswith("/") or "\0" in cwd:
                raise ValueError("live cwd unavailable")
            fact.update(cwd=posixpath.normpath(cwd), streaming=bool(meta.get("isStreaming")))
        except (BatError, OSError, asyncio.TimeoutError, ValueError):
            fact["error"] = "OBSERVATION_UNAVAILABLE"
        observed.append(fact)
    return observed


async def _runtime(ops, item, deadline, *, terminal=None):
    if terminal and terminal.get("error"):
        return {"error": terminal["error"]}
    fleet = ops.context["inventory"].fleet  # read-only BAT fleet
    if item["host"] not in ops.context["fleet"].config.hosts or item["host"] not in fleet.config.hosts:
        return {"error": "OBSERVATION_UNAVAILABLE"}
    client = fleet.client(item["host"])
    async def read():
        meta = {"cwd": terminal["cwd"], "isStreaming": terminal["streaming"]} if terminal else await service._meta(client, item["session_id"])
        if meta is not None and (not isinstance(meta.get("cwd"), str) or not meta["cwd"].startswith("/") or "\0" in meta["cwd"]):
            return {"error": "OBSERVATION_UNAVAILABLE"}
        state = None
        preset = (item.get("registry") or {}).get("agent_preset", "claude")
        kind = "codex" if "codex" in preset else "claude"
        if service._state_safe(kind, meta):
            state = await client.invoke("claude:get-session-state", {"sessionId": item["session_id"]})
        if isinstance(state, dict):
            # Runtime facts only: do not archive raw transcripts or prompt text in cleanup receipts.
            state = {k: (bool(v) if k in service.SESSION_WAITING_FIELDS else v)
                     for k, v in state.items() if k in {"status", "sessionId", "isStreaming", *service.SESSION_WAITING_FIELDS}}
        return {"loaded": meta is not None, "cwd": (meta or {}).get("cwd"),
                "streaming": bool((meta or {}).get("isStreaming")), "state": state}
    try:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise asyncio.TimeoutError()
        return await asyncio.wait_for(read(), remaining)
    except (BatError, OSError, asyncio.TimeoutError):
        return {"error": "OBSERVATION_UNAVAILABLE"}


def _execution_source_matches(source, item):
    return (source.get("kind") in {"execution", "task_command"} and source.get("worktree_id")
            and source["worktree_id"] == (item.get("worktree_id") or item["resource_id"])
            and source.get("host") == item["host"] and source.get("ref") == item.get("branch")
            and source.get("location") == (item.get("content_path") or item.get("path")))


def _coverage(ops, item, observed):
    coverage, prs, covered = [], [], set()
    full_head = False
    results = observed.get("results")
    head = observed.get("head")
    ids = set(item["original_ids"])
    for op_id in {r[0] for r in ops.db.execute("SELECT DISTINCT operation_id FROM integration_receipts")}:
        for r in integration.receipts(ops.db, op_id):
            # A repair result is covered by a delivered resolution receipt as well as source revisions.
            preview = ops.db.execute("SELECT host,sources FROM integration_previews WHERE preview_id=?", (r["preview_id"],)).fetchone()
            exact_source = preview and any(_execution_source_matches(s, item) and s["kind"] == r["source_kind"]
                and s["id"] == r["source_id"] and s.get("pin") == r["pinned_sha"] and s["host"] == r["source_host"]
                and s["location_class"] == r["location_class"] for s in json.loads(preview["sources"]))
            match = (r["source_kind"] in {"checkpoint_run", "session"} and
                     r["source_host"] == item["host"] and r["location_class"] == "managed_clone" and
                     r["source_id"] in ids) or (r.get("repair_worktree") == item.get("path") and
                                               preview and preview["host"] == item["host"]) or exact_source
            if not match:
                continue
            coverage.append({k: r.get(k) for k in ("operation_id", "seq", "source_kind", "source_id",
                "pinned_sha", "mode", "commits", "picked", "resolution_sha", "delivered_sha", "effective_status")})
            if r["effective_status"] == "unknown":
                _reason(item, "DELIVERY_UNCERTAIN", operation_id=op_id, seq=r["seq"])
            if r["effective_status"] != "delivered" or not r["delivered_sha"]:
                continue
            prs.append({"repository": r["repository"], "pull_number": r["pull_number"],
                        "delivered_sha": r["delivered_sha"]})
            if r["mode"] != "pick" and r["pinned_sha"] == head or r.get("resolution_sha") == head:
                full_head = True
                covered.update(results or [])
                covered.add(head)
            if r["mode"] == "pick":
                picked = r.get("picked") or []
                # Integration records a list of {source, picked}; all listed picks must have exact mappings.
                mapped = {p.get("source") for p in picked if p.get("new") and p.get("source")}
                covered.update(set(r.get("commits") or []) & mapped)
    no_result = results == [] and item.get("base") == head
    delivered = no_result or full_head or (results is not None and bool(results) and set(results) <= covered)
    return {"delivered": delivered, "no_result": no_result, "receipts": coverage,
            "result_commits": results, "covered_commits": sorted(covered), "pull_requests": prs}


def _consumers(ops, item, op_rows, pvs, own_op=None):
    ids = set(item["original_ids"])
    needles = ids | {v for v in (item.get("path") if item["kind"] != "local_branch" else item.get("branch"),) if v}
    for op in op_rows:
        if op["operation_id"] == own_op or op["action"] == "cleanup.apply":
            continue  # reservations below handle concurrent cleanup, without circular self-consumers
        text = _canonical([op["target"], op["params"], op.get("external_refs")])
        related = op["operation_id"] in ids or any(n in text for n in needles)
        if not related:
            continue
        unresolved = [r["name"] for r in ops.db.execute("SELECT name FROM operation_steps WHERE operation_id=? "
                     "AND status IN ('started','uncertain')", (op["operation_id"],))]
        if unresolved:
            _reason(item, "COMMAND_UNRESOLVED", operation_id=op["operation_id"], steps=unresolved)
        if op["status"] not in {"succeeded", "failed", "cancelled"}:
            fact = {"kind": "operation", "id": op["operation_id"], "status": op["status"]}
            item["consumers"].append(fact)
            _reason(item, "ACTIVE_EXECUTION", **fact)
    for command in ops.db.execute("SELECT * FROM commands"):
        task = ops.db.execute("SELECT state FROM tasks WHERE task_id=?", (command["task_id"],)).fetchone()
        if not task_cleanup.command_unresolved(dict(command), dict(task) if task else None):
            continue
        if (command["session_id"] and item["host"] + "/" + command["session_id"] in ids or
                command["task_id"] in ids):
            _reason(item, "COMMAND_UNRESOLVED", command_id=command["command_id"], status=command["status"])
    for pv in pvs.values():
        if pv["expires_at"] <= time.time():
            continue
        if any(s.get("id") in ids or _execution_source_matches(s, item) for s in json.loads(pv["sources"])):
            fact = {"kind": "integration_preview", "id": pv["preview_id"]}
            item["consumers"].append(fact)
            _reason(item, "CONTENT_REQUIRED", **fact)
    e = item.get("registry") or {}
    if e.get("status") in {"starting", "uncertain"} or e.get("handoff_status") == "pending":
        _reason(item, "COMMAND_UNRESOLVED", session_id=item.get("session_id"), status=e.get("status"))
    if e.get("retain_content") or e.get("retention_rule"):
        _reason(item, "RETENTION_RULE", rule=e.get("retention_rule") or "retain_content")
    try:
        guard(item["host"], session_id=item.get("session_id"), path=item.get("path"), branch=item.get("branch"))
    except ResourceReadOnly as e:
        _reason(item, e.code)


def _plan(ops, item, choices, op_rows, pvs, own_op=None):
    item["reasons"] = [r for r in item["reasons"] if r["code"] == "BINDING_MISMATCH"]
    item["consumers"] = []
    item["steps"] = []
    item["overridden_reasons"] = []
    kind, obs = item["kind"], item.get("observation") or {}
    if item.get("historical_cleanup"):
        _reason(item, "RESOURCE_CLEANED")
    if not item.get("proven"):
        _reason(item, "MANUAL_READ_ONLY" if item.get("provenance") == "manual" else "UNKNOWN_READ_ONLY")
    if kind in {"session", "worktree", "local_branch", "temporary"}:
        try:
            _require_resource_controller(ops, item)
        except (BatError, OperationError) as error:
            _reason(item, error.code)
    if item.get("task_owned"):
        verdict = item.get("task_cleanup")
        if not verdict or not verdict["eligible"]:
            _reason(item, "TASK_OWNED")
            for reason in (verdict or {}).get("reasons", []):
                _reason(item, **reason)
        if item["kind"] == "local_branch":
            _reason(item, "RETAINED_CONTENT_STORE")
    if kind in {"clone", "integration_area", "git_pin", "retained_ref"}:
        _reason(item, "RETAINED_CONTENT_STORE")
    if kind == "remote":
        _reason(item, "REMOTE_OUT_OF_SCOPE")
    if kind in {"artifact", "source"}:
        _reason(item, "RESOURCE_KIND_UNSUPPORTED")
    hc = ops.context["fleet"].config.hosts.get(item["host"])
    if hc and kind in {"session", "worktree", "local_branch", "temporary"} and (not hc.writes or not hc.orchestrate):
        _reason(item, "TIER_DISABLED")
    if hc and not resource_policy.in_managed_root(hc, item.get("path") or item.get("repository")):
        _reason(item, "WORKDIR_NOT_MANAGED")
    if obs.get("error"):
        _reason(item, obs["error"] if obs["error"] in REASONS else "OBSERVATION_UNAVAILABLE")
    if item.get("live_host_unavailable"):
        _reason(item, "OBSERVATION_UNAVAILABLE")
    for consumer in item.get("live_consumers", []):
        if consumer.get("error"):
            _reason(item, "OBSERVATION_UNAVAILABLE", session_id=consumer.get("session_id"))
    if item.get("repository_error"):
        code = item["repository_error"]
        _reason(item, code if code in REASONS else "OBSERVATION_UNAVAILABLE")
    if item.get("path_observation", {}).get("error") and obs.get("loaded"):
        _reason(item, item["path_observation"]["error"])
    _consumers(ops, item, op_rows, pvs, own_op)
    if kind == "session":
        if item.get("registry", {}).get("status") in {"starting", "uncertain"}:
            _reason(item, "COMMAND_UNRESOLVED", registry_status=item["registry"]["status"])
        if obs.get("streaming") or (isinstance(obs.get("state"), dict) and obs["state"].get("isStreaming")):
            _reason(item, "ACTIVE_WRITER")
        state = obs.get("state") or {}
        if isinstance(state, dict) and any(state.get(k) for k in service.SESSION_WAITING_FIELDS):
            _reason(item, "SESSION_WAITING")
        if obs.get("loaded") and obs.get("cwd") != item.get("path"):
            _reason(item, "BINDING_MISMATCH")
        if item.get("automatic_no_stop") and obs.get("loaded"):
            _reason(item, "ACTIVE_EXECUTION", detail="automatic cleanup does not stop a runtime")
        if obs.get("loaded") and not item["reasons"]:
            item["steps"] = ["stop", "finalize"]
    if kind in {"worktree", "local_branch"}:
        if kind == "worktree" and obs.get("exists"):
            if obs.get("branch") != item.get("branch") or not obs.get("registration"):
                _reason(item, "BINDING_MISMATCH")
            if obs.get("status"):
                _reason(item, "UNCOMMITTED_CHANGES", manifest=obs.get("manifest_digest"))
            if obs.get("complex_state") or any(f.get("type") != "file" and not (
                    (f["path"] == ".batc-inputs" or f["path"].startswith(".batc-inputs/")) and
                    f["type"] in {"link", "hardlink", "directory", "missing"}) for f in obs.get("manifest", [])):
                _reason(item, "RESOURCE_KIND_UNSUPPORTED")
        coverage = _coverage(ops, item, item.get("branch_observation", obs) if obs.get("exists") is False else obs)
        item["delivery"] = coverage
        if not coverage["delivered"]:
            _reason(item, "RESULTS_NOT_DELIVERED", receipts=coverage["receipts"])
        for code, choice in (("UNCOMMITTED_CHANGES", "discard_uncommitted"),
                             ("RESULTS_NOT_DELIVERED", "release_undelivered")):
            if (item["resource_id"] in choices[choice] and kind == "worktree" and
                    item.get("proven") and (not item.get("task_owned") or
                        choice == "release_undelivered" and item.get("task_cleanup", {}).get("eligible"))):
                matched = [r for r in item["reasons"] if r["code"] == code]
                item["overridden_reasons"].extend(matched)
                item["reasons"] = [r for r in item["reasons"] if r["code"] != code]
        if kind == "worktree" and obs.get("exists") and not item["reasons"]:
            item["steps"] = ["preserve", *(["discard"] if obs.get("status") else []), "remove.worktree", "finalize"]
        if kind == "local_branch" and obs.get("head") and not item["reasons"]:
            item["steps"] = ["preserve", "remove.branch", "finalize"]
    if kind == "temporary":
        if obs.get("exists"):
            if not obs.get("content_available"):
                _reason(item, "CONTENT_REQUIRED")
            if not obs.get("git_only"):
                _reason(item, "UNCOMMITTED_CHANGES")
            if not item["reasons"]:
                commits = sorted(set(obs.get("refs", {}).values()) | ({obs["head"]} if obs.get("head") else set()))
                obs["head"] = obs.get("head") or (commits[0] if commits else None)
                item["steps"] = [*(["preserve"] if commits else []), "remove.temporary", "finalize"]
        elif obs.get("exists") is False and not obs.get("error"):
            item["states"] = {"resource": "absent"}
    item["decision"] = "reclaim" if item["steps"] else "retain"
    item["states"] = {"work": "history", "runtime": "active" if obs.get("loaded") else "absent",
                      "delivery": "delivered" if item.get("delivery", {}).get("delivered") else "not_delivered",
                      "resource": "present" if obs.get("exists", obs.get("loaded", True)) else "absent"}
    if (kind == "session" and obs.get("loaded") is False or kind in {"worktree", "temporary"} and obs.get("exists") is False) and not obs.get("error"):
        item["decision"] = "already_absent" if not item["reasons"] else "retain"
    return item


def _replica_evidence(ops, item):
    """Exact verified replicas with a readable immutable original; no directory exemptions."""
    from .artifact_cleanup import replica_evidence
    return replica_evidence(ops, item)


async def snapshot(ops, target, choices, *, only=None, own_op=None, automatic=False):
    items, op_rows, pvs, worktrees, containers, links = _all(ops)
    selected, wi_ids = _selection(ops, target, items, pvs, links, op_rows)
    configured = ops.context["fleet"].config.hosts
    for item in items.values():
        if item["host"] and item["host"] not in configured:
            item["observation"] = {"error": "OBSERVATION_UNAVAILABLE", "host_configured": False}
    if only:
        item = items.get(only) or next((w for w in worktrees.values() if w.get("branch_id") == only), None)
        hosts = [item["host"]] if item and item["host"] in configured else []
    else:
        hosts = sorted({items[r]["host"] for r in selected if items[r]["host"] in configured})
    if len(selected) > MAX_ITEMS:
        raise OperationError("PREVIEW_TOO_LARGE", "preview exceeds 500 resources", 413)
    for host in hosts:
        deadline = time.monotonic() + READ_DEADLINE_S
        host_items = [i for i in items.values() if i["host"] == host]
        hc = configured.get(host)
        if hc is None:
            for item in host_items:
                item["observation"] = {"error": "OBSERVATION_UNAVAILABLE", "host_configured": False}
            continue
        lock = _HOST_LOCKS.setdefault(host, asyncio.Lock())
        acquired = False
        try:
            await asyncio.wait_for(lock.acquire(), max(.001, deadline - time.monotonic()))
            acquired = True
            terminals = await _terminal_observations(ops, host, deadline)
            by_sid = {t["session_id"]: t for t in terminals if t["session_id"]}
            for t in terminals:
                sid, path = t["session_id"], t.get("cwd")
                if not sid:
                    continue
                i = next((i for i in host_items if i.get("session_id") == sid), None)
                w = next((w for w in worktrees.values() if w["host"] == host and _inside(w["path"], path)), None)
                if i is None:
                    i = _resource(host, "session", "observed:" + sid, sid, session_id=sid, path=path,
                                  proven=False, provenance="manual", original_ids=[sid, host + "/" + sid])
                    if w:
                        i.update(repository=w["repository"], worktree_id=w["resource_id"])
                    items[i["resource_id"]] = i
                    host_items.append(i)
                # A live binding is consumer evidence, never creation/ownership evidence.
                if target["kind"] == "host" or w and w["resource_id"] in selected:
                    selected.add(i["resource_id"])
            # Observe all sessions in selected containers, including consumers outside the requested work item.
            repos = {i.get("repository") for i in host_items if i["resource_id"] in selected and i.get("repository")}
            if only:
                repos = {i.get("repository") for i in host_items if i["resource_id"] == only and i.get("repository")}
            for repo in sorted(repos):
                related = [i for i in host_items if i.get("repository") == repo]
                for i in related:
                    if i["kind"] == "worktree" and i.get("flavor") == "checkpoint" and i.get("proven"):
                        i["replica_evidence"] = _replica_evidence(ops, i)
                req = {"repository": repo, "roots": list(hc.managed_roots),
                       "paths_only": bool(only),
                       "worktrees": [i["path"] for i in related if i["kind"] == "worktree" and
                           (not only or i["resource_id"] == only or i.get("branch_id") == only or
                            items.get(only, {}).get("worktree_id") == i["resource_id"])],
                       "branches": {i["branch"]: i.get("base") for i in related if i["kind"] == "worktree" and i.get("branch")},
                       "replicas": {i["path"]: i["replica_evidence"] for i in related if "replica_evidence" in i},
                       "temporaries": [i["path"] for i in related if i["kind"] == "temporary"],
                       "bases": {i["path"]: i["base"] for i in related if i["kind"] == "worktree" and i.get("base")}}
                try:
                    if time.monotonic() >= deadline:
                        raise asyncio.TimeoutError()
                    read = await asyncio.wait_for(_host_call(ops, host, req, deadline - time.monotonic()),
                                                  max(.001, deadline - time.monotonic()))
                except (BatError, OperationError, OSError, asyncio.TimeoutError, AmbiguousOutcome, ValueError) as e:
                    if getattr(e, "code", None) == "PREVIEW_TOO_LARGE":
                        raise OperationError("PREVIEW_TOO_LARGE", "repository observation exceeds cleanup limits", 413) from None
                    read = {"error": getattr(e, "code", "OBSERVATION_UNAVAILABLE")}
                carrier_source = (containers.get((host, repo)) or {}).get("source")
                markers = read.get("markers", {})
                if carrier_source and (isinstance(carrier_source, str) and markers.get("batc.source") != carrier_source or
                        isinstance(carrier_source, dict) and any(markers.get("batc." + k) != v for k,v in carrier_source.items())):
                    read["error"] = "CLONE_NOT_OURS"
                for i in related:
                    i["repository_identity"] = {k: read.get(k) for k in ("common_dir", "markers", "config_digest")}
                    i["repository_error"] = read.get("error")
                    if i["kind"] == "temporary":
                        i["observation"] = read.get("temporaries", {}).get(i["path"], {"error": "OBSERVATION_UNAVAILABLE"})
                        temp_markers = i["observation"].get("identity", {}).get("markers", {})
                        src = i.get("source")
                        if i["observation"].get("exists") and (isinstance(src, str) and temp_markers.get("batc.source") != src or
                                isinstance(src, dict) and any(temp_markers.get("batc." + k) != v for k,v in src.items())):
                            i["repository_error"] = "CLONE_NOT_OURS"
                    if i["kind"] == "worktree":
                        i["branch_observation"] = read.get("branches", {}).get(i.get("branch"), {})
                        i["observation"] = read.get("worktrees", {}).get(i["path"], {"error": read.get("error") or
                                                                                  "OBSERVATION_UNAVAILABLE"})
                        if i.get("flavor") == "task" and i["observation"].get("exists") is False:
                            suffix = i["creation_evidence"]["intent"].replace("-", "")[:12]
                            ref = "refs/batc/tasks/" + suffix
                            head = i["branch_observation"].get("head")
                            if head and read.get("refs", {}).get(ref) == head:
                                i["retained_proof"] = {"ref": ref, "sha": head}
                        # Markers must also match original creation, not just the directory shape.
                        markers = read.get("markers", {})
                        src = i.get("source")
                        if src and isinstance(src, str) and markers.get("batc.source") != src:
                            i["repository_error"] = "CLONE_NOT_OURS"
                        if src and isinstance(src, dict) and (markers.get("batc.role") != ("published" if i.get("flavor") == "published" else "integration") or any(markers.get("batc." + k) != v for k, v in src.items())):
                            i["repository_error"] = "CLONE_NOT_OURS"
                container = containers.get((host, repo))
                for ref, sha in read.get("refs", {}).items():
                    if ref.startswith("refs/batc/"):
                        i = _resource(host, "git_pin", container["creation_evidence"]["intent"] if container else repo,
                                      ref, repository=repo, ref=ref, path=repo, proven=False,
                                      original_ids=(container or {}).get("original_ids", []),
                                      relations=(container or {}).get("relations", []), observation={"head": sha})
                        items[i["resource_id"]] = i
                        if container and container["resource_id"] in selected:
                            selected.add(i["resource_id"])
                for path, observed in read.get("worktrees", {}).items():
                    if (host, path) not in worktrees and path not in {repo, repo + "/repo.git"}:
                        i = _resource(host, "worktree", "unproven:" + repo, path, path=path, repository=repo,
                                      proven=False, observation=observed,
                                      original_ids=(container or {}).get("original_ids", []))
                        items[i["resource_id"]] = i
                        if container and container["resource_id"] in selected:
                            selected.add(i["resource_id"])
            for i in host_items:
                if i["kind"] == "session":
                    i["observation"] = await _runtime(ops, i, deadline, terminal=by_sid.get(i["session_id"]))
            unknown = [t for t in terminals if t.get("error")]
            unknown.extend({"session_id": i["session_id"], "error": "OBSERVATION_UNAVAILABLE"}
                           for i in host_items if i["kind"] == "session" and i.get("observation", {}).get("error")
                           and i["session_id"] not in by_sid)
            for i in host_items:
                if i["kind"] in {"session", "worktree", "temporary"}:
                    i["live_consumers"] = unknown
            sessions = [i for i in host_items if i["kind"] == "session" and i.get("proven") and i.get("path") and
                        i["resource_id"] in selected and (not only or i["resource_id"] == only)]
            if sessions:
                try:
                    paths = await asyncio.wait_for(_host_call(ops, host, {"canonical_paths": sorted({i["path"] for i in sessions}),
                        "roots": list(hc.managed_roots)}, max(.01, deadline - time.monotonic())),
                        max(.001, deadline - time.monotonic()))
                except (BatError, OperationError, OSError, asyncio.TimeoutError, AmbiguousOutcome, ValueError):
                    paths = {}
                for i in sessions:
                    i["path_observation"] = paths.get(i["path"], {"error": "OBSERVATION_UNAVAILABLE"})
        except asyncio.TimeoutError:
            for i in host_items:
                if i["resource_id"] in selected:
                    i["observation"] = {"error": "OBSERVATION_UNAVAILABLE"}
        finally:
            if acquired:
                lock.release()
    for i in list(items.values()):
        if (i["kind"] != "worktree" or not i.get("proven") or
                i["resource_id"] not in selected and i.get("branch_id") not in selected):
            continue
        obs = i.get("observation", {})
        branch_obs = i.get("branch_observation") or {"head": obs.get("head"), "results": obs.get("results")}
        if not branch_obs.get("head"):
            continue
        b = _resource(i["host"], "local_branch", i["creation_evidence"]["intent"], i.get("branch"),
                      path=i["repository"], repository=i["repository"], branch=i.get("branch"), proven=True,
                      flavor=i.get("flavor"), base=i.get("base"), observation=branch_obs, original_ids=i["original_ids"], relations=i["relations"],
                      task_owned=i.get("task_owned", False), worktree_id=i["resource_id"],
                      content_path=i["path"], live_consumers=i.get("live_consumers", []),
                      controller_source=i.get("controller_source"),
                      repository_identity=i.get("repository_identity"), repository_error=i.get("repository_error"))
        i["branch_id"] = b["resource_id"]
        items[b["resource_id"]] = b
        selected.add(b["resource_id"])
    if target["kind"] == "task":
        for i in items.values():
            if i.get("task_owned"):
                coordinator = ops.context.get("coordinator")
                if coordinator:
                    i["task_cleanup"] = coordinator.cleanup_verdict(i)
                if automatic:
                    verdict = i.get("task_cleanup")
                    paused = [t["task_id"] for t in (verdict or {}).get("binding", {}).get("tasks", []) if t["paused"]]
                    if verdict and paused:
                        verdict["eligible"] = False
                        verdict["reasons"].append({"code": "TASK_OWNED", "detail": "automatic cleanup retains paused tasks",
                                                   "task_ids": paused})
                    if i["kind"] == "session":
                        i["automatic_no_stop"] = True
                    elif not (i["kind"] == "worktree" and i.get("flavor") == "task"
                              and i["creation_evidence"]["intent"] == target["task_id"]):
                        i.pop("task_cleanup", None)
    planning = selected | {items[r]["worktree_id"] for r in selected if items[r]["kind"] == "local_branch"}
    for i in items.values():
        if i["resource_id"] in planning or i["kind"] == "session":
            _plan(ops, i, choices, op_rows, pvs, own_op)
            if automatic and i.get("task_cleanup", {}).get("eligible"):
                released = [r for r in i["reasons"] if r["code"] == "RESULTS_NOT_DELIVERED"]
                i["overridden_reasons"].extend(released)
                i["reasons"] = [r for r in i["reasons"] if r["code"] != "RESULTS_NOT_DELIVERED"]
                if not i["reasons"] and i.get("observation", {}).get("exists"):
                    i.update(decision="reclaim", steps=["preserve", "remove.worktree", "finalize"])
            if (i.get("task_cleanup", {}).get("eligible") and not i["reasons"] and i.get("retained_proof")
                    and i.get("observation", {}).get("exists") is False):
                i.update(decision="reclaim", steps=["finalize"])
    for rid in sorted(planning, key=lambda r: {"worktree": 0, "local_branch": 1}.get(items[r]["kind"], 2)):
        i = items[rid]
        if i["kind"] == "worktree":
            for s in items.values():
                if s["kind"] != "session" or s["host"] != i["host"] or not (
                        _inside(i.get("path"), s.get("path")) or _inside(i.get("path"), s.get("observation", {}).get("cwd"))):
                    continue
                for reason in s["reasons"]:
                    if reason["code"] in {"ACTIVE_WRITER", "SESSION_WAITING", "COMMAND_UNRESOLVED", "TASK_OWNED",
                                          "OBSERVATION_UNAVAILABLE", "BINDING_MISMATCH", "CLEANUP_IN_PROGRESS"}:
                        _reason(i, reason["code"], session_id=s["session_id"])
                if s.get("observation", {}).get("loaded"):
                    if s["resource_id"] not in selected or s["decision"] != "reclaim":
                        _reason(i, "ACTIVE_EXECUTION", session_id=s["session_id"])
                    else:
                        i["dependencies"].append(s["resource_id"])
            if i["reasons"]:
                i.update(steps=[], decision="retain")
            # Do not stop the selected sessions when an unreviewed/unknown consumer needs their worktree.
            for s in items.values():
                if s["kind"] == "session" and s.get("worktree_id") == rid and s["resource_id"] in selected:
                    for reason in i["reasons"]:
                        if reason["code"] in {"ACTIVE_EXECUTION", "ACTIVE_WRITER", "SESSION_WAITING", "COMMAND_UNRESOLVED",
                                              "TASK_OWNED", "OBSERVATION_UNAVAILABLE", "BINDING_MISMATCH"}:
                            _reason(s, reason["code"], **{k: v for k, v in reason.items() if k != "code"})
                    if s["reasons"]:
                        s.update(steps=[], decision="retain")
        if i["kind"] == "local_branch":
            w = items[i["worktree_id"]]
            absent_cleaned = w.get("observation", {}).get("exists") is False and all(
                r["code"] == "RESOURCE_CLEANED" for r in w["reasons"])
            if (w["decision"] in {"reclaim", "already_absent"} or absent_cleaned) and i.get("flavor") in {"checkpoint", "repair", "bat", "published"}:
                i["dependencies"] = [w["resource_id"]] if w["decision"] == "reclaim" else []
            else:
                _reason(i, "CONTENT_REQUIRED", resource_id=w["resource_id"])
            if i.get("flavor") not in {"checkpoint", "repair", "bat", "published"}:
                _reason(i, "RESOURCE_KIND_UNSUPPORTED")
            if i["reasons"]:
                i.update(steps=[], decision="retain")
    if len(selected) > MAX_ITEMS:
        raise OperationError("PREVIEW_TOO_LARGE", "preview exceeds 500 resources; narrow the target", 413)
    if set(choices["discard_uncommitted"] + choices["release_undelivered"]) - selected:
        raise OperationError("INVALID_PARAMS", "choice names a resource outside this target", 422)
    ordered = sorted((items[r] for r in selected), key=lambda i: ({"session": 0, "worktree": 1,
                     "local_branch": 2}.get(i["kind"], 3), i["host"], i["resource_id"]))
    # Registry volatile timestamps and self reservations must not change the content fingerprint.
    for i in ordered:
        if "registry" in i:
            i["registry"] = {k: v for k, v in i["registry"].items() if k not in
                             {"updated_at", "cleanup_reservation"}}
    return {"target": target, "choices": choices, "items": ordered, "work_items": wi_ids,
            "config_digest": _config(ops), "contract_version": VERSION}


def _capacity_ready(items):
    absent_carriers = {i["resource_id"] for i in items if i["kind"] == "worktree" and i["decision"] == "already_absent"}
    for item in items:
        entry = item.get("registry", {})
        if (item["kind"] != "session" or item["decision"] != "already_absent" or
                entry.get("status") != "active" or item.get("task_owned") and not item.get("task_cleanup", {}).get("eligible")):
            continue
        carrier = item.get("worktree_id")
        if carrier in absent_carriers or carrier is None and not entry.get("worktree_path"):
            return True
    return False


async def preview(ops, principal, target, choices=None, *, _automatic=False):
    authority = controller_authority.require() if _automatic else controller_authority.binding(ops.db, principal)
    with controller_authority.use(authority, ops.journal):
        return await _preview(ops, principal, target, choices, _automatic=_automatic)


async def _preview(ops, principal, target, choices=None, *, _automatic=False):
    if not principal.allows("observe"):
        raise OperationError("FORBIDDEN", "preview needs observe", 403)
    target, choices = _target(ops, target), _choices(choices, principal)
    if _automatic and (principal is not task_cleanup.SYSTEM or target["kind"] != "task"):
        raise OperationError("FORBIDDEN", "automatic cleanup is coordinator-only", 403)
    doc = await snapshot(ops, target, choices, automatic=_automatic)
    fingerprint = _hash(doc)
    now = int(time.time())
    payload = {"actor": principal.actor, "target": target, "choices": choices, "fingerprint": fingerprint,
               "config_digest": doc["config_digest"], "contract_version": VERSION, "iat": now, "exp": now + TTL_S,
               "ready": any(i["decision"] == "reclaim" for i in doc["items"]) or _capacity_ready(doc["items"])}
    if _automatic:
        payload["origin"] = "task_lifecycle"
        payload["controller_authority"] = task_control.require_authority(ops.journal.get(target["task_id"]))
    raw = _canonical(payload).encode()
    if len(raw) > MAX_TOKEN_BYTES:
        raise OperationError("PREVIEW_TOO_LARGE", "preview token is too large", 413)
    token = "v1." + _b64(raw) + "." + _b64(hmac.digest(ops.context["cleanup_key"], b"v1." + raw, "sha256"))
    preview_id = "clpv_" + _hash(payload)[:32]
    return {**doc, "preview_id": preview_id, "preview_token": token, "fingerprint": fingerprint,
            "issued_at": now, "expires_at": now + TTL_S, "ready": payload["ready"],
            "blocking": [] if payload["ready"] else ["NO_RECLAIMABLE_RESOURCES"],
            "impact": {"reclaim": sum(i["decision"] == "reclaim" for i in doc["items"]),
                       "retain": sum(i["decision"] == "retain" for i in doc["items"]), "estimated_bytes": None,
                       "undelivered_commits_kept": [i["resource_id"] for i in doc["items"]
                           if any(r["code"] == "RESULTS_NOT_DELIVERED" for r in i["overridden_reasons"])]}}


def _admit(ops, principal, target, params, pre):
    if set(target) != {"preview_id"} or set(params) - {"preview_token", "supersedes_operation_id"} or set(pre) != {"preview_fingerprint"}:
        raise OperationError("INVALID_PARAMS", "apply accepts only the reviewed token and fingerprint", 422)
    payload = decode_token(ops, params.get("preview_token"))
    if (payload["actor"] != principal.actor or target["preview_id"] != "clpv_" + _hash(payload)[:32] or
            pre["preview_fingerprint"] != payload["fingerprint"]):
        raise OperationError("PREVIEW_MISMATCH", "actor, preview ID or fingerprint differs", 409)
    if payload.get("origin") == "task_lifecycle" and principal is not task_cleanup.SYSTEM:
        raise OperationError("FORBIDDEN", "automatic cleanup is coordinator-only", 403)
    _choices(payload["choices"], principal)
    if not payload["ready"]:
        raise OperationError("PREVIEW_BLOCKED", "preview has no reclaimable resources", 409)
    # Admission runs after OperationService hashes the public request and before it persists params.
    # This server-only field records acceptance authority without changing OperationService or its table.
    params["_accepted_authorization"] = {"actor": principal.actor,
        "scopes": sorted(SCOPES if principal.admin else principal.scopes), "choices": payload["choices"]}
    if payload.get("origin") == "task_lifecycle":
        params["_accepted_authorization"]["origin"] = "task_lifecycle"
        params["_accepted_authorization"]["task_id"] = payload["target"]["task_id"]
        authority = task_control.authority(ops.journal.get(payload["target"]["task_id"]))
        if authority != payload.get("controller_authority"):
            raise OperationError("CONTROLLER_RESOURCE_CHANGED", "automatic cleanup task authority changed", 409)
        with controller_authority.use(authority, ops.journal):
            task_control.require_authority(ops.journal.get(payload["target"]["task_id"]))
        return {"controller_authority": authority}


def authorize_controller_existing(ops, principal, op, verb):
    """Only the server's task-cleanup principal can replay its original delegated intent."""
    if principal is not task_cleanup.SYSTEM or op["action"] != "cleanup.apply":
        return False
    accepted = op["params"].get("_accepted_authorization") or {}
    if accepted.get("origin") != "task_lifecycle" or accepted.get("actor") != principal.actor:
        return False
    authority = controller_authority.operation_binding(op)
    with controller_authority.use(authority, ops.journal):
        task_control.require_authority(ops.journal.get(accepted.get("task_id")))
    return True


def apply_request(doc, key):
    return {"action": "cleanup.apply", "target": {"preview_id": doc["preview_id"]},
            "params": {"preview_token": doc["preview_token"]},
            "preconditions": {"preview_fingerprint": doc["fingerprint"]}, "idempotency_key": key}


def _receipt(ctx, item, status, **extra):
    if "error" not in extra and status in {"running", "uncertain"}:
        row = ctx.service.db.execute("SELECT error FROM cleanup_receipts WHERE operation_id=? AND resource_id=?",
                                     (ctx.operation_id, item["resource_id"])).fetchone()
        extra["error"] = json.loads(row[0]) if row and row[0] else None
    ctx.service.db.execute("UPDATE cleanup_receipts SET status=?,updated_at=?,after_state=COALESCE(?,after_state),"
        "error=?,settled_by=COALESCE(?,settled_by) WHERE operation_id=? AND resource_id=?",
        (status, time.time(), _canonical(extra.get("after")) if extra.get("after") is not None else None,
         _canonical(extra.get("error")) if extra.get("error") else None, extra.get("settled_by"),
         ctx.operation_id, item["resource_id"]))


def _resumed_by(ctx):
    return [r["actor"] for r in ctx.service.db.execute("SELECT actor,body FROM api_events WHERE resource_id=? "
        "AND kind='operation.running' ORDER BY seq", (ctx.operation_id,)) if
        json.loads(r["body"]).get("from") == "needs_attention"]


def _phase_histories(plans, steps, *, only=None):
    """Project durable step facts, including effects of the item's prerequisite resources."""
    histories = {}
    for rid in ([only] if only else plans):
        related, todo = set(), [rid]
        while todo:
            dep = todo.pop()
            if dep in related:
                continue
            related.add(dep)
            todo.extend(plans.get(dep, {}).get("dependencies", []))
        completed, refused = [], []
        for step in steps:
            parts = step["name"].split(".", 2)
            if len(parts) != 3 or parts[0] != "item" or parts[1] not in related:
                continue
            phase = parts[2].rsplit(".a", 1)[0]
            if phase not in PHASE_EFFECTS:
                continue
            fact = {"resource_id": parts[1], "phase": phase, "effect": PHASE_EFFECTS[phase], "step": step["name"]}
            if step["status"] == "succeeded":
                completed.append({**fact, "result": json.loads(step["response"] or "{}")})
            elif step["status"] == "failed":
                refused.append({**fact, "error": json.loads(step["error"] or "{}")})
        histories[rid] = {"completed_phases": completed, "refused_phases": refused}
    return histories


def _item_history(ctx, item):
    plans = {r["resource_id"]: json.loads(r["plan"]) for r in ctx.service.db.execute(
        "SELECT resource_id,plan FROM cleanup_receipts WHERE operation_id=?", (ctx.operation_id,))}
    steps = ctx.service.db.execute("SELECT * FROM operation_steps WHERE operation_id=? ORDER BY seq",
                                   (ctx.operation_id,)).fetchall()
    return _phase_histories(plans, steps, only=item["resource_id"])[item["resource_id"]]


def _has_irreversible(history):
    return any(p["effect"] in {"runtime", "destructive"} for p in history["completed_phases"])


def _pending_item(ctx, item):
    return ctx.service.db.execute("SELECT 1 FROM operation_steps WHERE operation_id=? AND name LIKE ? "
        "AND status IN ('started','uncertain')", (ctx.operation_id, "item." + item["resource_id"] + ".%" )).fetchone()


def receipts(ops, op_id):
    rows = ops.db.execute("SELECT * FROM cleanup_receipts WHERE operation_id=? ORDER BY item_order", (op_id,)).fetchall()
    histories = _phase_histories({r["resource_id"]: json.loads(r["plan"]) for r in rows},
        ops.db.execute("SELECT * FROM operation_steps WHERE operation_id=? ORDER BY seq", (op_id,)).fetchall())
    operation = ops.db.execute("SELECT cancel_requested FROM operations WHERE operation_id=?", (op_id,)).fetchone()
    out = []
    for r in rows:
        d = dict(r)
        for k in ("plan", "before_state", "after_state", "error", "retained_ids"):
            d[k] = json.loads(d[k]) if d[k] else None
        d["resumed_by"] = [r["actor"] for r in ops.db.execute("SELECT actor,body FROM api_events WHERE resource_id=? "
            "AND kind='operation.running' ORDER BY seq", (op_id,)) if json.loads(r["body"]).get("from") == "needs_attention"]
        d.update(histories[d["resource_id"]])
        d["cancel_requested"] = bool(operation and operation[0])
        out.append(d)
    return out


def _finalize(ctx, item, after):
    db, rid = ctx.service.db, item["resource_id"]
    retained_ids = [r[0] for r in db.execute("SELECT retained_id FROM cleanup_retained WHERE operation_id=? "
                                          "AND resource_id=?", (ctx.operation_id, rid))]
    doc = {**item, "operation_id": ctx.operation_id, "actor": ctx.actor, "reason": ctx.params["_accepted_authorization"].get("origin", "reviewed_cleanup"),
           "accepted_authorization": ctx.params["_accepted_authorization"], "last_observation": item.get("observation"),
           "after": after, "retained_ids": retained_ids, "receipt": {"operation_id": ctx.operation_id,
           "resource_id": rid}, **_item_history(ctx, item), "resumed_by": _resumed_by(ctx), "cleaned_at": time.time(),
           "pull_requests": item.get("delivery", {}).get("pull_requests", []),
           "attachment_replicas": "removed with worktree; originals in artifact store" if
               item.get("observation", {}).get("exempted_replicas") else None}
    with ctx.service.journal.tx():
        existing = db.execute("SELECT 1 FROM resource_tombstones WHERE resource_id=?", (rid,)).fetchone()
        if not existing:
            db.execute("INSERT INTO resource_tombstones VALUES(?,?,?,?,?,?)",
                       (rid, item["generation"], item["host"], item["kind"], _canonical(doc), doc["cleaned_at"]))
            aliases = [("original", x) for x in item["original_ids"] + item["relations"]]
            aliases += [("path", item["path"])] if item.get("path") else []
            aliases += [("ref", item["branch"])] if item.get("branch") else []
            for kind, external in aliases:
                db.execute("INSERT OR IGNORE INTO cleanup_aliases VALUES(?,?,?,?)", (kind, external, item["host"], rid))
            ctx.service.journal.api_event("cleanup", rid, "cleanup.cleaned", {"operation_id": ctx.operation_id,
                                         "kind": item["kind"], "retained_ids": retained_ids}, actor=ctx.actor)
        _receipt(ctx, item, "succeeded", after=after, settled_by="read_back")
        db.execute("UPDATE cleanup_receipts SET retained_ids=? WHERE operation_id=? AND resource_id=?",
                   (_canonical(retained_ids), ctx.operation_id, rid))
        task_cleanup.finalize(ctx, item, after)
    _mark(item, ctx.operation_id, "cleaned", **({"ops": ctx.service} if item.get("task_cleanup") else {}))
    if item["kind"] == "worktree":
        _retire_absent_sessions(ctx, item["resource_id"])


def _retire_absent_sessions(ctx, carrier_id=None):
    # Local receipt facts only; no history projection, BAT observation or new external effect.
    rows = [{**dict(r), "plan": json.loads(r["plan"])} for r in ctx.service.db.execute(
        "SELECT resource_id,plan,status FROM cleanup_receipts WHERE operation_id=?", (ctx.operation_id,))]
    carriers = {r["resource_id"] for r in rows if r["plan"]["kind"] == "worktree" and
                r["status"] in {"succeeded", "already_absent"} and
                (carrier_id is None or r["resource_id"] == carrier_id)}
    for row in rows:
        item = row["plan"]
        carrier = item.get("worktree_id")
        if item["kind"] != "session" or carrier_id is not None and carrier != carrier_id:
            continue
        if row["status"] not in {"retained", "already_absent"}:
            continue
        entry = item.get("registry", {})
        try:
            owner = task_control.owner_task(ctx.service.context["fleet"], item["host"], item["session_id"])
        except (ResourceReadOnly, registry.RegistryInvariantError, OSError) as error:
            _receipt(ctx, item, row["status"], after=_capacity_failure(error))
            continue
        if (owner and row["status"] == "already_absent" and item.get("task_cleanup", {}).get("eligible")
                and (carrier in carriers or carrier is None and not entry.get("worktree_path"))):
            try:
                ctx.require_controller()
                controller_authority.require_entry(entry)
                capacity = task_cleanup.retire_absent(ctx, item)
            except (ResourceReadOnly, registry.RegistryInvariantError, OSError) as error:
                capacity = _capacity_failure(error)
            _receipt(ctx, item, row["status"], after={**capacity, "carrier_resource_id": carrier,
                                                   "stopped_by_cleanup": False})
            continue
        if owner and row["status"] in {"retained", "already_absent"}:
            _receipt(ctx, item, row["status"], after={"capacity_released": False,
                "registry_status": entry.get("status"), "capacity_reason": "task_owned"})
            continue
        if row["status"] == "retained" and entry and (entry.get("status") != "active" or entry.get("task_id")):
            _receipt(ctx, item, "retained", after={"capacity_released": False, "registry_status": entry.get("status"),
                "capacity_reason": "task_owned" if entry.get("task_id") else
                    "start_unsettled" if entry.get("status") == "starting" else "not_counted"})
            continue
        if row["status"] != "already_absent":
            continue
        if carrier not in carriers and (carrier is not None or entry.get("worktree_path")):
            _receipt(ctx, item, "already_absent", after={"capacity_released": False,
                "registry_status": entry.get("status"), "capacity_reason":
                    "carrier_retained" if entry.get("status") == "active" else "not_counted",
                "carrier_resource_id": carrier, "stopped_by_cleanup": False})
            continue
        try:
            # Read-only descriptions above do not require creation authority. An actual
            # retirement must retain both the accepted owner and its locked row identity.
            ctx.require_controller()
            controller_authority.require_entry(entry)
            capacity = registry.retire(item["host"], item["session_id"], "absent_at_cleanup",
                created_at=entry.get("created_at"), controller_incarnation=entry["controller_incarnation"],
                actor=ctx.actor, operation_id=ctx.operation_id,
                carrier_resource_id=carrier, reason="session observed absent; no worktree or carrier removed/already absent")
        except (ResourceReadOnly, registry.RegistryInvariantError, OSError) as e:
            capacity = _capacity_failure(e)
        _receipt(ctx, item, "already_absent", after={**capacity, "carrier_resource_id": carrier, "stopped_by_cleanup": False})


def _capacity_failure(error):
    return {"capacity_released": False, "registry_status": None,
            "capacity_reason": "registry_io_failed" if isinstance(error, OSError) else "registry_refused",
            "capacity_error": {"code": getattr(error, "code", "REGISTRY_IO_FAILED")}}


def _progress(ctx):
    result = receipts(ctx.service, ctx.operation_id)
    summary = {"succeeded": sum(r["status"] == "succeeded" for r in result),
               "retained": sum(r["status"] == "retained" for r in result),
               "already_absent": sum(r["status"] == "already_absent" for r in result),
               "partial": any(r["status"] in {"failed", "blocked_stale", "uncertain", "running", "pending", "cancelled"} for r in result)}
    ctx.service._transition(ctx.operation_id, "running", result={"preview_id": ctx.target["preview_id"],
        "items": result, "summary": summary, "next_action": "inspect receipts; resume or preview again"})


async def _phase_consumers(ctx, item):
    ctx.require_controller()
    ops = ctx.service
    _require_resource_controller(ops, item)
    _host_config(ops, item["host"])
    if item.get("task_cleanup"):
        task_cleanup.check(ops, item)
    probe = {**item, "reasons": [], "consumers": []}
    _consumers(ops, probe, [ops._decode(r) for r in ops.db.execute("SELECT * FROM operations")],
               {r["preview_id"]: dict(r) for r in ops.db.execute("SELECT * FROM integration_previews")}, ctx.operation_id)
    if probe["reasons"]:
        raise OperationError("PREVIEW_STALE", "new content consumer before mutation", 409)
    host, path = item["host"], item.get("content_path") or item.get("path")
    deadline = time.monotonic() + READ_DEADLINE_S
    lock = _HOST_LOCKS.setdefault(host, asyncio.Lock())
    acquired = False
    try:
        await asyncio.wait_for(lock.acquire(), max(.001, deadline - time.monotonic()))
        acquired = True
        terminals = await _terminal_observations(ops, host, deadline)
        if any(t.get("error") for t in terminals):
            raise OperationError("OBSERVATION_UNAVAILABLE", "a terminal's live cwd is unknown; preview again", 409)
        by_sid = {t["session_id"]: t for t in terminals}
        entries = {e["session_id"]: e for e in registry.list_entries(host)}
        # A stop may share its worktree with other idle sessions in this exact accepted plan.
        # Their own stop phases remain prerequisites of the worktree removal.
        allowed = {}
        if item["kind"] == "session":
            for row in ops.db.execute("SELECT plan FROM cleanup_receipts WHERE operation_id=?", (ctx.operation_id,)):
                plan = json.loads(row[0])
                if plan["host"] == host and plan["kind"] == "session" and plan["decision"] == "reclaim" and "stop" in plan["steps"]:
                    allowed[plan["session_id"]] = plan["path"]
        for sid in sorted(set(entries) | set(by_sid)):
            e = entries.get(sid, {})
            runtime = await _runtime(ops, {"host": host, "session_id": sid, "registry": e}, deadline,
                                     terminal=by_sid.get(sid))
            if runtime.get("error"):
                raise OperationError("OBSERVATION_UNAVAILABLE", "a session's live cwd/state is unknown; preview again", 409)
            recorded = e.get("worktree_path") or e.get("cwd")
            if not _inside(path, runtime.get("cwd")) and not _inside(path, recorded):
                continue
            controller_authority.require_entry(e)
            task_owner = task_control.owner_task(ops.context["fleet"], host, sid)
            allowed_owner = bool(item.get("task_cleanup", {}).get("eligible") and task_owner in item["task_cleanup"]["task_ids"])
            if (task_owner and not allowed_owner
                    or e.get("status") in {"starting", "uncertain"} or e.get("handoff_status") == "pending"):
                raise OperationError("PREVIEW_STALE", "a task or unresolved start needs this worktree", 409)
            state = runtime.get("state") or {}
            if runtime.get("streaming") or state.get("isStreaming"):
                raise OperationError("ACTIVE_WRITER", "a session is streaming; preview again", 409)
            if any(state.get(k) for k in service.SESSION_WAITING_FIELDS):
                raise OperationError("SESSION_WAITING", "a session is waiting; preview again", 409)
            if runtime.get("loaded") and not (sid in allowed and recorded == allowed[sid] == runtime.get("cwd")):
                raise OperationError("PREVIEW_STALE", "an unreviewed session uses this worktree", 409)
        if item.get("task_cleanup"):
            task_cleanup.check(ops, item)
        ctx.require_controller()
        _require_resource_controller(ops, item)
    except asyncio.TimeoutError:
        raise OperationError("OBSERVATION_UNAVAILABLE", "live consumer read deadline expired; preview again", 409) from None
    finally:
        if acquired:
            lock.release()

def _release_unstarted(ctx, error):
    # Every external mutation has a durable step first. With no step at all, reservations have no effects.
    db = ctx.service.db
    if db.execute("SELECT 1 FROM operation_steps WHERE operation_id=?", (ctx.operation_id,)).fetchone():
        return
    path = registry.registry_path()
    with registry._locked(path):
        document = _registry_document()
        guards = document.get("cleanup_guards", {})
        owned = [rid for rid, g in guards.items() if g.get("operation_id") == ctx.operation_id and g.get("status") == "reserved"]
        for rid in owned:
            del guards[rid]
        sessions = [s for s in document.get("sessions", []) if s.get("cleanup_reservation") == ctx.operation_id]
        for session in sessions:
            session["cleanup_reservation"] = None
        if owned or sessions:
            registry._write_document(path, document)
    with ctx.service.journal.tx():
        updated = db.execute("UPDATE cleanup_receipts SET status='failed',updated_at=?,error=?,after_state=? "
            "WHERE operation_id=? AND status IN ('pending','running','uncertain')",
            (time.time(), _canonical({"code": getattr(error, "code", "INTERNAL")}),
             _canonical({"completed_phases": [], "guard_released": True}), ctx.operation_id)).rowcount
    if updated:
        _progress(ctx)


async def _run(ctx):
    try:
        return await _run_plan(ctx)
    except (Cancelled, Uncertain, AmbiguousOutcome):
        raise
    except Exception as error:
        # Covers resumed validation and local/read-only refusals outside the per-item handlers too.
        _release_unstarted(ctx, error)
        raise


async def _run_plan(ctx):
    ops, db = ctx.service, ctx.service.db
    existing = db.execute("SELECT * FROM cleanup_runs WHERE operation_id=?", (ctx.operation_id,)).fetchone()
    if existing:
        # Acceptance is durable. A signing-key rotation cannot revoke an already accepted operation on resume.
        if existing["token_hash"] != _hash(ctx.params["preview_token"]):
            raise OperationError("PREVIEW_MISMATCH", "accepted token differs from journal", 409)
        raw = ctx.params["preview_token"].split(".")[1]
        payload = json.loads(base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)))
        doc = json.loads(existing["document"])
        if time.time() >= payload["exp"] and not db.execute("SELECT 1 FROM operation_steps WHERE operation_id=?", (ctx.operation_id,)).fetchone():
            raise OperationError("PREVIEW_EXPIRED", "preview expired before any external step", 409)
    else:
        # Admission already verified this token and durably recorded its authority in the operation params.
        # Key rotation after acceptance cannot revoke queued work; expiry before execution still applies.
        raw = ctx.params["preview_token"].split(".")[1]
        payload = json.loads(base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)))
        if time.time() >= payload["exp"]:
            raise OperationError("PREVIEW_EXPIRED", "preview expired before execution", 409)
        doc = await snapshot(ops, payload["target"], payload["choices"], own_op=ctx.operation_id,
                             automatic=payload.get("origin") == "task_lifecycle")
        if _hash(doc) != payload["fingerprint"]:
            raise OperationError("PREVIEW_STALE", "live resources or consumers changed; preview again", 409)
        with ops.journal.tx():
            auth = ctx.params["_accepted_authorization"]
            db.execute("INSERT INTO cleanup_runs VALUES(?,?,?,?,?,?,?,?,?,?)", (ctx.operation_id,
                ctx.target["preview_id"], _hash(ctx.params["preview_token"]), payload["fingerprint"], _canonical(doc),
                auth["actor"], _canonical(auth["scopes"]), _canonical(auth["choices"]), time.time(),
                ctx.params.get("supersedes_operation_id")))
            for order, item in enumerate(doc["items"]):
                db.execute("INSERT INTO cleanup_receipts(operation_id,resource_id,item_order,plan,status,before_state,updated_at) "
                    "VALUES(?,?,?,?,?,?,?)", (ctx.operation_id, item["resource_id"], order, _canonical(item),
                    {"reclaim": "pending", "retain": "retained", "already_absent": "already_absent"}[item["decision"]],
                    _canonical(item.get("observation", {})), time.time()))
    ctx.set_refs(preview_id=ctx.target["preview_id"], fingerprint=payload["fingerprint"],
                 **({"task_id": payload["target"]["task_id"]} if payload["target"]["kind"] == "task" else {}))
    owner = _OWNER.set(ctx.operation_id)
    try:
        _retire_absent_sessions(ctx)
        for item in doc["items"]:
            if item["decision"] != "reclaim":
                continue
            row = db.execute("SELECT status FROM cleanup_receipts WHERE operation_id=? AND resource_id=?",
                             (ctx.operation_id, item["resource_id"])).fetchone()
            if row[0] == "succeeded":
                _mark(item, ctx.operation_id, "cleaned", **({"ops": ctx.service} if item.get("task_cleanup") else {}))
                if item["kind"] == "worktree":
                    _retire_absent_sessions(ctx, item["resource_id"])
                continue
            unresolved = db.execute("SELECT 1 FROM operation_steps WHERE operation_id=? AND name LIKE ? "
                                    "AND status IN ('started','uncertain')", (ctx.operation_id, "item." + item["resource_id"] + ".%" )).fetchone()
            if not unresolved:
                ctx.check_cancel()
            if any(db.execute("SELECT status FROM cleanup_receipts WHERE operation_id=? AND resource_id=?",
                              (ctx.operation_id, dep)).fetchone()[0] not in {"succeeded", "already_absent"} for dep in item["dependencies"]):
                history = _item_history(ctx, item)
                if unresolved or _has_irreversible(history):
                    _mark(item, ctx.operation_id, "reserved", **({"ops": ctx.service} if item.get("task_cleanup") else {}))
                    _receipt(ctx, item, "uncertain", after={**history, "refused_phase": "dependencies", "refused_code": "DEPENDENCY_FAILED"},
                             error={"code": "CLEANUP_PARTIAL_STATE" if _has_irreversible(history) else "UNCERTAIN_UNRESOLVED",
                                    "refused_phase": "dependencies", "refused_code": "DEPENDENCY_FAILED"})
                    _progress(ctx)
                    raise NeedsAttention("CLEANUP_PARTIAL_STATE" if _has_irreversible(history) else "UNCERTAIN_UNRESOLVED",
                                         "an unresolved call or completed prerequisite remains reserved; inspect receipts")
                _receipt(ctx, item, "failed", error={"code": "DEPENDENCY_FAILED"})
                _release(item, ctx.operation_id)
                continue
            lock = _REPO_LOCKS.setdefault((item["host"], item.get("repository") or item.get("path")), asyncio.Lock())
            async with lock:
                try:
                    _mark(item, ctx.operation_id, "reserved", **({"ops": ctx.service} if item.get("task_cleanup") else {}))
                    _receipt(ctx, item, "running")
                    if item.get("task_cleanup"):
                        async with ops.context["coordinator"].cleanup_authority(ctx, item) as authority:
                            await _execute_item(ctx, item, payload, authority=authority)
                    else:
                        await _execute_item(ctx, item, payload)
                except (Uncertain, AmbiguousOutcome, OSError) as e:
                    _receipt(ctx, item, "uncertain")
                    _progress(ctx)
                    if isinstance(e, Uncertain):
                        raise
                    raise Uncertain("item." + item["resource_id"], "item read-back remains unproven") from e
                except NeedsAttention:
                    _progress(ctx)
                    raise
                except (OperationError, ResourceReadOnly, StepFailed) as e:
                    code = getattr(e, "code", "CLEANUP_FAILED")
                    prior = db.execute("SELECT error FROM cleanup_receipts WHERE operation_id=? AND resource_id=?",
                                       (ctx.operation_id, item["resource_id"])).fetchone()
                    history = _item_history(ctx, item)
                    if _pending_item(ctx, item) or prior[0] and json.loads(prior[0]).get("mutated"):
                        saved = json.loads(prior[0]) if prior[0] else {"code": "UNCERTAIN"}
                        _receipt(ctx, item, "uncertain", error={**saved, "cause": code})
                        _progress(ctx)
                        raise NeedsAttention("CLEANUP_PARTIAL_STATE" if saved.get("mutated") else "UNCERTAIN_UNRESOLVED",
                                             "unresolved mutation remains reserved; inspect receipts") from None
                    if _has_irreversible(history):
                        refused = [p for p in history["refused_phases"] if p["resource_id"] == item["resource_id"]]
                        phase = refused[-1]["phase"] if refused else "validate"
                        _receipt(ctx, item, "uncertain", after={**history, "refused_phase": phase, "refused_code": code},
                                 error={"code": "CLEANUP_PARTIAL_STATE", "refused_phase": phase, "refused_code": code})
                        _progress(ctx)
                        raise NeedsAttention("CLEANUP_PARTIAL_STATE", "completed runtime/destructive phases remain reserved; inspect receipts") from None
                    _receipt(ctx, item, "blocked_stale" if code == "PREVIEW_STALE" else "failed",
                             error={"code": code, "message": str(e)[:300]})
                    # No unresolved call or completed runtime/destructive phase. Additive pins remain in the receipt.
                    _release(item, ctx.operation_id)
                    _progress(ctx)
                    if code == "PREVIEW_STALE":
                        raise NeedsAttention(code, "item changed; keep receipts and preview again") from None
        result = receipts(ops, ctx.operation_id)
        if any(r["status"] == "failed" for r in result):
            _progress(ctx)
            raise NeedsAttention("CLEANUP_PARTIAL", "some items failed; inspect receipts before resuming")
        return {"preview_id": ctx.target["preview_id"], "fingerprint": payload["fingerprint"], "items": result,
                "summary": {"succeeded": sum(r["status"] == "succeeded" for r in result),
                            "retained": sum(r["status"] == "retained" for r in result),
                            "already_absent": sum(r["status"] == "already_absent" for r in result), "partial": False},
                "tombstones": [r["resource_id"] for r in result if r["status"] == "succeeded"],
                "next_action": "cleanup_retained"}
    except Cancelled:
        # Cancellation never rolls back a runtime/destructive effect or releases its partial reservation.
        for item in doc["items"]:
            row = db.execute("SELECT status FROM cleanup_receipts WHERE operation_id=? AND resource_id=?",
                             (ctx.operation_id, item["resource_id"])).fetchone()
            if row[0] in {"succeeded", "retained", "already_absent"}:
                continue
            unresolved = _pending_item(ctx, item)
            history = _item_history(ctx, item)
            partial = _has_irreversible(history)
            keep = bool(unresolved) or partial
            status = "uncertain" if keep else "cancelled"
            _receipt(ctx, item, status, after={**history, "cancel_requested": True, "guard_released": not keep},
                     **({"error": {"code": "CLEANUP_PARTIAL_STATE", "cancel_requested": True}} if partial else {}))
            if partial:
                _mark(item, ctx.operation_id, "reserved", **({"ops": ctx.service} if item.get("task_cleanup") else {}))
            if not keep:
                _release(item, ctx.operation_id)
        _progress(ctx)
        raise
    finally:
        _OWNER.reset(owner)


def _partial_evidence(phase, item, before, current, observed):
    old = {f["path"]: f for f in (before or {}).get("manifest", [])}
    new = {f["path"]: f for f in current.get("manifest", [])}
    complete = "manifest" in current or current.get("exists") is False
    refs = {k: v for k, v in observed.get("refs", {}).items() if k == "refs/heads/" + (item.get("branch") or "") or
            k.startswith("refs/batc/retained/" + item["resource_id"] + "/")}
    expected_refs = {"refs/heads/" + item["branch"]: item["observation"].get("head")} if item.get("branch") else {}
    return {"phase": phase, "path": item["path"], "removed": sorted(old.keys() - new.keys()) if complete else None,
            "changed": [{"path": p, "before": old[p], "after": new[p]} for p in sorted(old.keys() & new.keys())
                        if old[p] != new[p]], "added": [new[p] for p in sorted(new.keys() - old.keys())],
            "remaining": current.get("manifest", [] if current.get("exists") is False else None), "observation": current, "refs": refs,
            "ref_changes": [{"ref": ref, "before": sha, "after": observed.get("refs", {}).get(ref)}
                            for ref, sha in expected_refs.items() if observed.get("refs", {}).get(ref) != sha],
            "directories_removed": sorted(set((before or {}).get("directories", [])) - set(current.get("directories", [])))
                if "directories" in current or current.get("exists") is False else None,
            "directories_remaining": current.get("directories"),
            "repository_identity": {k: observed.get(k) for k in ("common_dir", "markers", "config_digest")}}


def _record_partial(ctx, item, evidence):
    for row in ctx.service.db.execute("SELECT name FROM operation_steps WHERE operation_id=? AND name LIKE ? AND status='started'",
                                      (ctx.operation_id, "item." + item["resource_id"] + ".%")):
        ctx.service._step_status(ctx.operation_id, row[0], "uncertain")
    prior = ctx.service.db.execute("SELECT error FROM cleanup_receipts WHERE operation_id=? AND resource_id=?",
                                   (ctx.operation_id, item["resource_id"])).fetchone()
    _receipt(ctx, item, "uncertain", after=evidence,
             error={**(json.loads(prior[0]) if prior and prior[0] else {}),
                    "code": "CLEANUP_PARTIAL_STATE", "mutated": True, "phase": evidence["phase"],
                    "message": "state differs from both the reviewed before-state and the proven complete result"})


def _mutation_receipt(ctx, item, phase, result):
    _receipt(ctx, item, "uncertain", error={"code": "CLEANUP_MUTATION_UNCERTAIN", "mutated": True,
        "host_error": result["error"], "phase": phase, "effects": result.get("effects", []),
        "failure": result.get("failure")})


def _partial_attention(ctx, item, evidence):
    _record_partial(ctx, item, evidence)
    raise NeedsAttention("CLEANUP_PARTIAL_STATE", "partial cleanup remains reserved; inspect removed/changed/remaining evidence")


async def _execute_item(ctx, item, payload, *, authority=None):
    """Validate once per item; named phases reconcile their own exact effects after a restart."""
    ops, rid = ctx.service, item["resource_id"]
    hc = _host_config(ops, item["host"])
    begun = ops.db.execute("SELECT 1 FROM operation_steps WHERE operation_id=? AND name LIKE ?",
                           (ctx.operation_id, "item." + rid + ".%" )).fetchone()
    if not begun:
        current = await snapshot(ops, payload["target"], payload["choices"], only=rid, own_op=ctx.operation_id,
                                 automatic=payload.get("origin") == "task_lifecycle")
        actual = next((i for i in current["items"] if i["resource_id"] == rid), None)
        # Earlier planned session stops / worktree removals change dependencies, never the item's content.
        if item["kind"] == "local_branch" and actual is None:
            actual = item  # ref is still checked by the host CAS below; consumers checked separately below
        if item["kind"] == "worktree" and actual:
            actual["dependencies"] = item["dependencies"]
        if item["kind"] == "local_branch" and actual and set(actual["dependencies"]) <= set(item["dependencies"]):
            dropped = set(item["dependencies"]) - set(actual["dependencies"])
            settled = {r["resource_id"] for r in ops.db.execute(
                "SELECT resource_id,plan FROM cleanup_receipts WHERE operation_id=? AND status='succeeded'",
                (ctx.operation_id,)) if json.loads(r["plan"])["kind"] == "worktree"}
            if dropped <= settled:
                actual["dependencies"] = item["dependencies"]
        if actual != item:
            raise OperationError("PREVIEW_STALE", "resource or its consumers changed", 409)
    if item.get("task_cleanup") and item["steps"] == ["finalize"]:
        await task_cleanup.finalize_absent(ctx, item)
        return
    if item["kind"] == "session":
        async def stop():
            result = None
            async def checked_stop():
                ctx.check_cancel()
                await _phase_consumers(ctx, item)
                now = await _runtime(ops, item, time.monotonic() + READ_DEADLINE_S)
                if now != item["observation"]:
                    raise OperationError("PREVIEW_STALE", "session changed before stop", 409)
                path = await _host_call(ops, item["host"], {"canonical_paths": [item["path"]],
                    "roots": list(_host_config(ops, item["host"]).managed_roots)})
                if path.get(item["path"]) != item.get("path_observation") or path[item["path"]].get("error"):
                    raise OperationError("PREVIEW_STALE", "session workdir changed before stop", 409)

            async def send_stop():
                nonlocal result
                _host_config(ops, item["host"])
                result = await lifecycle._stop(ops.context["fleet"], item["host"], item["session_id"],
                                              Audit(ops.context["fleet"].config.safety), cleanup=True,
                                              **({"_task_cleanup": authority} if authority else {}))
                if not result.get("stopped"):
                    raise OperationError(result.get("code", "STOP_UNPROVEN"), result.get("reason", "stop was not confirmed"), 409)
                if isinstance(result.get("result"), dict) and result["result"].get("ok") is False:
                    raise AmbiguousOutcome("stop acknowledgment did not confirm termination")
            try:
                await _host_call(ops, item["host"], {"phase": "lock.session", "repository": item.get("repository") or item["path"],
                    "roots": list(hc.managed_roots)}, locked_check=checked_stop, locked_action=send_stop)
            except (MutationUncertain, OperationError) as e:
                if isinstance(e, OperationError) and not (result and result.get("stopped")):
                    raise
                uncertain = e.result if isinstance(e, MutationUncertain) else {"error": e.code, "mutated": True, "effects": []}
                if result and result.get("stopped"):
                    uncertain = {**uncertain, "effects": [*uncertain.get("effects", []),
                        {"action": "stop", "target": item["session_id"], "completed": True, "result": result}]}
                _mutation_receipt(ctx, item, "stop", uncertain)
                raise MutationUncertain(uncertain) from e
            return result

        async def reconcile_stop(_):
            # Missing meta alone is not proof of stop. A healthy BAT explicit terminal state is required.
            now = await _runtime(ops, item, time.monotonic() + READ_DEADLINE_S)
            state = now.get("state") or {}
            if isinstance(state, dict) and state.get("status") in {"stopped", "exited"} and state.get("sessionId") == item["session_id"]:
                return {"stopped": True, "termination_evidence": state}
            return None
        previous = ops.db.execute("SELECT name,status FROM operation_steps WHERE operation_id=? AND name LIKE ? ORDER BY seq DESC LIMIT 1",
                                  (ctx.operation_id, "item." + rid + ".stop.a%" )).fetchone()
        attempt = int(previous["name"].rsplit(".a", 1)[1]) + 1 if previous and previous["status"] == "failed" else 1
        name = "item." + rid + ".stop.a" + str(attempt) if not previous or previous["status"] == "failed" else previous["name"]
        await ctx.step(name, stop, request={"session_id": item["session_id"],
                       "before": item["observation"]}, reconcile=reconcile_stop)
        _finalize(ctx, item, {"stopped": True, "runtime_restored": False})
        return
    resource_policy.check_cleanup_worktree(hc, item["repository"], item["path"] if item["kind"] == "worktree" else None,
                                            item.get("branch") or "batc/temporary")
    if item["kind"] == "worktree":
        if item["flavor"] == "checkpoint":
            resource_policy.check_checkpoint_worktree(hc, item["repository"], item["path"], item["branch"])
        elif item["flavor"] == "repair":
            resource_policy.check_repair_worktree(hc, item["repository"], item["path"], item["branch"])
        elif item["flavor"] == "published":
            resource_policy.check_published_worktree(hc, item["repository"], item["path"], item["branch"])
        else:
            resource_policy.check_cleanup_worktree(hc, item["repository"], item["path"], item["branch"])
    elif item["kind"] == "local_branch":
        resource_policy.check_cleanup_worktree(hc, item["repository"], None, item["branch"])
    sha = item["observation"].get("head")
    retained_ref = "refs/batc/retained/" + rid + "/" + sha if sha else None
    req = {"repository": item["repository"], "roots": list(hc.managed_roots), "identity": item["repository_identity"],
           "path": item["path"] if item["kind"] in {"worktree", "temporary"} else None, "kind": item["kind"],
           "flavor": item.get("flavor"),
           "temporaries": [item["path"]] if item["kind"] == "temporary" else [], "branch": item.get("branch"), "sha": sha,
           "retained_ref": retained_ref, "delivered": item.get("delivery", {}).get("delivered", False),
           "worktrees": [item["path"]] if item["kind"] == "worktree" else [],
           "paths_only": True, "replicas": {item["path"]: item["replica_evidence"]} if "replica_evidence" in item else {},
           "bases": {item["path"]: item["base"]} if item.get("base") and item["kind"] == "worktree" else {}}
    before = item["observation"] if item["kind"] in {"worktree", "temporary"} else None
    discarded_replicas = sorted(set(item["observation"].get("missing_replicas", [])) | {
        e["path"] for e in item.get("replica_evidence", {}).get("replica_manifest", [])
        if e["path"] in item["observation"].get("extras", [])})

    async def read(*, probe=False, acknowledge_missing=False):
        extra = {"acknowledged_missing_replicas": {item["path"]: discarded_replicas}} if acknowledge_missing else {}
        try:
            return await _host_call(ops, item["host"], {**{k: v for k, v in req.items() if k != "phase"},
                                                     **extra, "probe": probe})
        except OperationError as e:
            if probe:
                raise AmbiguousOutcome("host read-back unavailable: " + e.code) from e
            raise

    async def verify_pins(commits):
        try:
            return await _host_call(ops, item["host"], {**req, "phase": "verify.retained",
                "retained": [{"ref": retained_ref.rsplit("/", 1)[0] + "/" + commit, "commit_sha": commit} for commit in commits]})
        except OperationError as e:
            raise AmbiguousOutcome("retained read-back unavailable: " + e.code) from e

    for phase in item["steps"]:
        if phase == "finalize":
            continue
        request = {**req, "phase": phase, "before": before}
        name = "item." + rid + "." + phase + ".a1"

        async def execute(request=request):
            ctx.check_cancel()
            async def checked_inputs():
                await _phase_consumers(ctx, item)
                # A resumed item may already have preserved refs and skip snapshot.
                # Before releasing the host lock gate, prove its accepted replica
                # exemptions still have their immutable original in the store.
                if "replica_evidence" in item and _replica_evidence(ops, item) != item["replica_evidence"]:
                    raise OperationError("PREVIEW_STALE", "artifact replica evidence changed; original content must remain available", 409)
            try:
                return await _host_call(ops, item["host"], request, locked_check=checked_inputs)
            except MutationUncertain as e:
                _mutation_receipt(ctx, item, request["phase"], e.result)
                raise
            except OperationError as e:
                prior = ops.db.execute("SELECT error FROM cleanup_receipts WHERE operation_id=? AND resource_id=?",
                                       (ctx.operation_id, rid)).fetchone()
                if prior[0] and json.loads(prior[0]).get("mutated"):
                    raise AmbiguousOutcome("refusal while an earlier partial mutation remains reserved: " + e.code) from e
                raise

        async def reconcile(_, phase=phase, request=request):
            observed = await read(probe=True)
            pinned = observed.get("refs", {}).get(retained_ref) == sha
            if phase == "remove.temporary" and sha:
                commits = sorted(set(item["observation"].get("refs", {}).values()) | {sha})
                verified = await verify_pins(commits)
                pinned = all(p["available"] for p in verified)
                if not pinned and observed.get("process_ended"):
                    current = observed.get("temporaries", {}).get(item["path"], {})
                    evidence = _partial_evidence(phase, item, request["before"], current, observed)
                    _partial_attention(ctx, item, {**evidence, "retained_refs": verified})
            if phase == "preserve" and pinned:
                commits = sorted(set(item["observation"].get("refs", {}).values()) | {sha}) if item["kind"] == "temporary" else [sha]
                result = await verify_pins(commits)
                if all(p["available"] for p in result):
                    return {"ref": retained_ref, "sha": sha,
                            "tree": next(p["tree_sha"] for p in result if p["commit_sha"] == sha),
                            "pins": [{"ref": p["ref"], "sha": p["commit_sha"], "tree": p["tree_sha"]} for p in result]}
            if phase == "remove.worktree" and pinned:
                wt = observed.get("worktrees", {}).get(item["path"], {})
                if wt.get("exists") is False and wt.get("registration") is None:
                    return {"removed": True}
            if phase == "remove.temporary" and (pinned or sha is None) and observed.get("temporaries", {}).get(item["path"], {}).get("exists") is False:
                return {"removed": True}
            if phase == "remove.branch" and pinned and "refs/heads/" + item["branch"] not in observed.get("refs", {}):
                return {"deleted": True, "sha": sha}
            if phase == "discard" and pinned:
                settled = await read(probe=True, acknowledge_missing=True) if discarded_replicas else observed
                wt = settled.get("worktrees", {}).get(item["path"], {})
                if wt.get("head") == sha and wt.get("status") == "" and not wt.get("complex_state"):
                    return {"discarded": True, "acknowledged_missing_replicas": discarded_replicas, "after": wt}
            if observed.get("process_ended") and observed.get("common_dir") == req["identity"]["common_dir"]:
                if phase == "preserve":
                    commits = set(item["observation"].get("refs", {}).values()) | {sha} if item["kind"] == "temporary" else {sha}
                    pins = {retained_ref.rsplit("/", 1)[0] + "/" + commit: commit for commit in commits}
                    if (any(ref in observed["refs"] and observed["refs"][ref] != commit for ref, commit in pins.items()) or
                            item["kind"] == "local_branch" and observed["refs"].get("refs/heads/" + item["branch"]) != sha):
                        current = observed.get("temporaries" if item["kind"] == "temporary" else "worktrees", {}).get(item["path"], {})
                        _partial_attention(ctx, item, _partial_evidence(phase, item, request["before"], current, observed))
                    if any(ref in observed["refs"] for ref in pins):
                        current = observed.get("temporaries" if item["kind"] == "temporary" else "worktrees", {}).get(item["path"], {})
                        _record_partial(ctx, item, _partial_evidence(phase, item, request["before"], current, observed))
                current = observed.get("worktrees", {}).get(item["path"])
                if phase in {"preserve", "remove.worktree", "discard"} and current == request["before"]:
                    return RERUN  # unchanged content; preserve CAS also completes any missing pins idempotently
                if phase == "remove.temporary" and pinned and observed.get("temporaries", {}).get(item["path"]) == request["before"]:
                    return RERUN
                if phase == "preserve" and item["kind"] == "temporary" and observed.get("temporaries", {}).get(item["path"]) == request["before"]:
                    return RERUN
                if phase == "remove.branch" and pinned and observed.get("refs", {}).get("refs/heads/" + item["branch"]) == sha:
                    return RERUN
                current = (observed.get("temporaries", {}) if item["kind"] == "temporary" else
                           observed.get("worktrees", {})).get(item["path"], {})
                evidence = _partial_evidence(phase, item, request["before"], current, observed)
                if phase == "remove.temporary" and (pinned or sha is None) and _temporary_subset(request["before"], current):
                    _record_partial(ctx, item, evidence)
                    if ops._row(ctx.operation_id)["cancel_requested"]:
                        _partial_attention(ctx, item, evidence)
                    # The original step intent authorizes exactly these unchanged remaining entries.
                    # The helper rechecks the subset and every retained commit under the directory flock.
                    try:
                        result = await execute({**request, "before": current, "recovery_before": request["before"]})
                    except Cancelled:
                        _partial_attention(ctx, item, evidence)
                    confirmed = await read(probe=True)
                    if confirmed.get("temporaries", {}).get(item["path"], {}).get("exists") is False:
                        return {**result, "recovery": evidence, "confirmed": True}
                    return None
                if current.get("manifest_complete") or current.get("manifest") is not None or item["kind"] == "local_branch":
                    _partial_attention(ctx, item, evidence)
            return None  # never retry when the remote process's outcome is unproven
        previous = ops.db.execute("SELECT name,status,error FROM operation_steps WHERE operation_id=? AND name LIKE ? ORDER BY seq DESC LIMIT 1",
                                  (ctx.operation_id, "item." + rid + "." + phase + ".a%" )).fetchone()
        if previous and previous["status"] == "failed":
            observed = await read(probe=True)
            stable_identity = all(observed.get(k) == req["identity"].get(k) for k in ("common_dir", "markers", "config_digest"))
            stable_content = (observed.get("temporaries", {}).get(item["path"]) == request["before"] if item["kind"] == "temporary" else
                              observed.get("refs", {}).get("refs/heads/" + item["branch"]) == sha if item["kind"] == "local_branch" else
                              observed.get("worktrees", {}).get(item["path"]) == request["before"])
            if not observed.get("process_ended") or not stable_identity or not stable_content:
                raise OperationError("PREVIEW_STALE", "failed removal no longer has its original binding", 409)
            n = int(previous["name"].rsplit(".a", 1)[1]) + 1
            name = "item." + rid + "." + phase + ".a" + str(n)
        elif previous:
            name = previous["name"]
        result = await ctx.step(name, execute, request=request, reconcile=reconcile)
        prior = ops.db.execute("SELECT error FROM cleanup_receipts WHERE operation_id=? AND resource_id=?",
                               (ctx.operation_id, rid)).fetchone()
        error = json.loads(prior[0]) if prior[0] else None
        # Clear only a now-proven phase's uncertainty, never its durable completed-step evidence.
        if error and error.get("mutated") and error.get("phase") == phase:
            _receipt(ctx, item, "running", error=None)
        else:
            _receipt(ctx, item, "running")
        if phase == "preserve":
            for pin in result.get("pins", [result]):
                commit = pin["sha"]
                ret_id = "ret_" + _hash([rid, commit, item["repository"]])[:32]
                fact = {"host": item["host"], "repository": item["repository"], "ref": pin["ref"],
                        "commit": commit, "tree": pin["tree"]}
                ops.db.execute("INSERT OR IGNORE INTO cleanup_retained VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (ret_id, rid, ctx.operation_id, name, commit, item["host"], item["repository"], pin["ref"], commit,
                     pin["tree"], _hash(fact), _canonical(item["creation_evidence"]), time.time()))
        if phase == "discard":
            req["acknowledged_missing_replicas"] = {item["path"]: result.get("acknowledged_missing_replicas", [])}
            if not isinstance(result.get("after"), dict):
                _partial_attention(ctx, item, {**_item_history(ctx, item), "phase": phase,
                                               "code": "POST_DISCARD_STATE_UNAVAILABLE"})
            before = result["after"]
    after = await read()
    if item["kind"] == "temporary":
        if after.get("temporaries", {}).get(item["path"], {}).get("exists") is not False:
            raise AmbiguousOutcome("temporary removal not confirmed")
    elif item["kind"] == "worktree":
        wt = after.get("worktrees", {}).get(item["path"], {})
        if wt.get("exists") or wt.get("registration"):
            raise AmbiguousOutcome("worktree removal not confirmed")
    elif "refs/heads/" + item["branch"] in after.get("refs", {}):
        raise AmbiguousOutcome("branch removal not confirmed")
    _finalize(ctx, item, {"removed": True, "retained_ref": retained_ref, "head": sha})


def lookup(db, original_id, host=None):
    rows = db.execute("SELECT DISTINCT t.document FROM resource_tombstones t LEFT JOIN cleanup_aliases a USING(resource_id) "
                      "WHERE (t.resource_id=? OR a.external_id=?) AND (? IS NULL OR t.host=?) ORDER BY t.cleaned_at DESC",
                      (original_id, original_id, host, host)).fetchall()
    return [json.loads(r[0]) for r in rows]


def tombstones(ops, *, query=None, host=None, original_id=None, work_item_id=None, kind=None, limit=50, cursor=None):
    limit = max(1, min(200, int(limit)))
    args, where = [], []
    for col, val in (("host", host), ("kind", kind)):
        if val:
            where.append(col + "=?")
            args.append(val)
    if cursor:
        try:
            stamp, rid = json.loads(base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)))
            where.append("(cleaned_at,resource_id)<(?,?)")
            args.extend([stamp, rid])
        except (ValueError, TypeError):
            raise OperationError("INVALID_PARAMS", "invalid history cursor", 422) from None
    search = original_id or work_item_id
    if search:
        where.append("resource_id IN (SELECT resource_id FROM cleanup_aliases WHERE external_id=?)")
        args.append(search)
    if query:
        where.append("document LIKE ? ESCAPE '\\'")
        args.append("%" + query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%")
    sql = "SELECT * FROM resource_tombstones" + (" WHERE " + " AND ".join(where) if where else "")  # noqa: S608 - fixed predicates, bound values
    rows = ops.db.execute(sql + " ORDER BY cleaned_at DESC,resource_id DESC LIMIT ?", (*args, limit + 1)).fetchall()
    page = rows[:limit]
    return {"tombstones": [json.loads(r["document"]) for r in page], "next_cursor":
            _b64(_canonical([page[-1]["cleaned_at"], page[-1]["resource_id"]]).encode()) if len(rows) > limit else None}


async def retained(ops, *, host=None, resource_id=None, query=None, limit=50, cursor=None):
    limit = max(1, min(200, int(limit)))
    rows = [dict(r) for r in ops.db.execute("SELECT * FROM cleanup_retained ORDER BY retained_id")]
    rows = [r for r in rows if (not host or r["host"] == host) and (not resource_id or r["resource_id"] == resource_id)
            and (not query or query.casefold() in _canonical(r).casefold()) and (not cursor or r["retained_id"] > cursor)]
    page = rows[:limit]
    for row in page:
        if row["step"] == "historical":
            row["operation_id"] = None
            row["task_event_id"] = json.loads(row["creation_evidence"]).get("task_event_id")
    available, unavailable = [], []
    groups = {}
    for r in page:
        groups.setdefault((r["host"], r["repository"]), []).append(r)
    for (h, repo), rs in groups.items():
        try:
            read = await _host_call(ops, h, {"phase": "verify.retained", "repository": repo,
                "roots": list(_host_config(ops, h).managed_roots), "retained": rs})
            for row in read:
                row["runtime_restored"] = False
                if not row["available"]:
                    row["reason"] = "RETAINED_CONTENT_MISSING"
                (available if row.pop("available") else unavailable).append(row)
        except (OperationError, BatError, OSError, AmbiguousOutcome, ValueError):
            unavailable.extend({**r, "reason": "OBSERVATION_UNAVAILABLE"} for r in rs)
    return {"retained": available, "unavailable": unavailable, "next_cursor": page[-1]["retained_id"] if len(rows) > limit else None,
            "retention": {"retained_refs": "keep", "history_retention": "forever", "permanent_delete": False}, "restore_available": False}


def http_request(path, *, body=None, token=None, key=None, timeout=40):
    """Thin loopback HTTP adapter for CLI/MCP, avoiding changes to the task RPC dispatcher."""
    parsed = urlsplit(os.environ.get("BATC_TASK_URL", "http://127.0.0.1:18796/rpc"))
    if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"} or parsed.username or parsed.password:
        raise ValueError("task daemon URL must be loopback")
    auth = token or os.environ.get("BATC_API_TOKEN")
    if not auth:
        auth = Path(os.environ.get("BATC_TASK_ADMIN_TOKEN_FILE", state_dir() / "task-admin.token")).read_text().strip()
    headers = {"Authorization": "Bearer " + auth, "Content-Type": "application/json"}
    if key:
        headers["Idempotency-Key"] = key
    req = urllib.request.Request(  # noqa: S310 - validated loopback HTTP
        parsed._replace(path=path.split("?", 1)[0], query=path.partition("?")[2], fragment="").geturl(),
        method="POST" if body is not None else "GET", data=_canonical(body).encode() if body is not None else None, headers=headers)
    try:
        with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req, timeout=timeout) as response:  # noqa: S310 - loopback
            out = json.load(response)
            status = response.status
    except urllib.error.HTTPError as e:
        out = json.load(e)
        status = e.code
    if "error" in out:
        raise OperationError(out["error"]["code"], out["error"]["message"], status)
    return out


def read_path(name, **filters):
    return "/api/v1/cleanup-" + name + "?" + urlencode({k: v for k, v in filters.items() if v is not None})


ACTIONS = [ActionDef("cleanup.apply", "cleanup", "Apply exactly a reviewed cleanup preview", _run,
                     admit=_admit, target_keys=("preview_id",), authorize_existing=task_cleanup.authorize_existing)]
