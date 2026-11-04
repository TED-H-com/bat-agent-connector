"""Versioned project skill selection with host/workspace/digest provenance.

Selection is management data. Current BAT transports do not provide a verified
skill-application receipt, so this module explicitly reports selected_not_applied
and never modifies a user's workspace or smuggles activation into a prompt.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import shlex
import time
from pathlib import Path

from bat_agent_connector import hidden_process

from . import catalog_sources, service, work_items
from .operations import ActionDef, OperationError

HELPER = Path(__file__).with_name("skills_host_helper.py").read_text()
DIGEST = re.compile(r"[0-9a-f]{64}")
SKILL = re.compile(r"skill_[0-9a-f]{32}")


class SkillHost:
    def __init__(self, aliases):
        self.aliases = dict(aliases)

    def argv(self, host):
        alias = self.aliases.get(host)
        if not isinstance(alias, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.@-]{0,253}", alias):
            raise ValueError("skill_adapter_unavailable")
        return ("ssh", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes", "-o", "ConnectTimeout=5", alias,
                "python3 -I -S -B -c " + shlex.quote(HELPER))

    async def scan(self, host, workspace):
        process = await hidden_process.create_subprocess_exec(*self.argv(host), stdin=asyncio.subprocess.PIPE,
                                                       stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
        async def exchange():
            process.stdin.write(json.dumps({"workspace": workspace}).encode() + b"\n")
            await process.stdin.drain()
            process.stdin.close()
            raw = bytearray()
            while chunk := await process.stdout.read(8192):
                raw.extend(chunk)
                if len(raw) > 262144:
                    raise ValueError("skill_inventory_unavailable")
            await process.wait()
            if process.returncode:
                raise ValueError("skill_inventory_unavailable")
            return json.loads(raw)
        try:
            return await asyncio.wait_for(exchange(), 15)
        finally:
            if process.returncode is None:
                process.kill()
                await process.wait()


def install(ops):
    with ops.journal.tx():
        ops.db.execute("""CREATE TABLE IF NOT EXISTS project_skill_selections (
            project_id TEXT PRIMARY KEY REFERENCES projects(project_id), revision INTEGER NOT NULL,
            host TEXT NOT NULL, workspace_id TEXT NOT NULL, document TEXT NOT NULL, updated_at REAL NOT NULL)""")
        ops.db.execute("""CREATE TABLE IF NOT EXISTS project_skill_catalogs (
            host TEXT NOT NULL, workspace_id TEXT NOT NULL, folder TEXT NOT NULL,
            digest TEXT NOT NULL, document TEXT NOT NULL, observed_at REAL NOT NULL, PRIMARY KEY(host,workspace_id))""")
    if "project.skills.update" not in ops.actions:
        ops.register(ActionDef("project.skills.update", "manage", "Pin project skill selections from the reviewed host catalog",
                               _run, _admit, ("project_id",)))


def selection(db, project_id):
    row = db.execute("SELECT * FROM project_skill_selections WHERE project_id=?", (project_id,)).fetchone()
    return {"revision": row["revision"] if row else 0, "host": row["host"] if row else None,
            "workspace_id": row["workspace_id"] if row else None, "selected": json.loads(row["document"]) if row else [],
            "updated_at": row["updated_at"] if row else None, "application": "selected_not_applied"}


def _binding(ops, project_id, host, workspace_id):
    work_items._get_project(ops.db, project_id, active=True)
    if not isinstance(host, str) or host not in ops.context["fleet"].config.hosts:
        raise OperationError("UNKNOWN_HOST", "host is not configured", 404)
    if not isinstance(workspace_id, str) or not 1 <= len(workspace_id) <= 256 or any(ord(char) < 32 for char in workspace_id):
        raise OperationError("INVALID_PARAMS", "an exact workspace ID is required", 422)


def _normalize(raw):
    if (not isinstance(raw, dict) or raw.get("ok") is not True or raw.get("version") != 1
            or not isinstance(raw.get("skills"), list) or len(raw["skills"]) > 200 or type(raw.get("complete")) is not bool):
        raise ValueError("skill_inventory_unavailable")
    rows, ids = [], set()
    for row in raw["skills"]:
        if (not isinstance(row, dict) or not isinstance(row.get("skill_id"), str)
                or not SKILL.fullmatch(row["skill_id"]) or row["skill_id"] in ids
                or row.get("scope") not in {"project", "global"} or row.get("agent") != "claude"
                or type(row.get("available")) is not bool):
            raise ValueError("skill_inventory_unavailable")
        for name, limit in (("name", 200), ("description", 300), ("relative_path", 512)):
            if not isinstance(row.get(name), str) or len(row[name]) > limit or any(ord(char) < 32 for char in row[name]):
                raise ValueError("skill_inventory_unavailable")
        if row["available"] and (not isinstance(row.get("digest"), str) or not DIGEST.fullmatch(row["digest"])
                or type(row.get("files")) is not int or not 1 <= row["files"] <= 128
                or type(row.get("size_bytes")) is not int or not 0 <= row["size_bytes"] <= 8 * 1024 * 1024):
            raise ValueError("skill_inventory_unavailable")
        ids.add(row["skill_id"])
        clean = {key: row.get(key) for key in ("skill_id", "scope", "agent", "name", "description", "relative_path",
                                               "digest", "files", "size_bytes", "available")}
        clean["reason"] = row.get("reason") if row.get("reason") in {"unsafe_or_changed_source", "shadowed_source"} else None
        if not isinstance(clean["digest"], str) or not DIGEST.fullmatch(clean["digest"]):
            clean["digest"] = None
        for key in ("files", "size_bytes"):
            if type(clean[key]) is not int:
                clean[key] = None
        rows.append(clean)
    document = {"skills": rows, "complete": raw["complete"]}
    digest = hashlib.sha256(json.dumps(document, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return {**document, "catalog_digest": digest}


async def _catalog(ops, project_id, host, workspace_id, *, refresh=False):
    _binding(ops, project_id, host, workspace_id)
    binding = catalog_sources.host_binding(ops, host, skills=True)
    cached = ops.db.execute("SELECT * FROM project_skill_catalogs WHERE host=? AND workspace_id=?", (host, workspace_id)).fetchone()
    cached_value = catalog_sources.cached_document(cached, binding)
    if cached_value is None:
        cached = None
    base = {"source": "host_workspace", "status": "unavailable", "skills": [], "complete": False,
            "stale": False, "catalog_digest": None, "observed_at": None, "reason": None}
    try:
        document = await asyncio.wait_for(service._workspace(ops.context["fleet"].client(host)), 10)
        if catalog_sources.host_binding(ops, host, skills=True) != binding:
            raise ValueError("host_binding_changed")
        matches = [row for row in document.get("workspaces", []) if isinstance(row, dict) and row.get("id") == workspace_id]
        if len(matches) != 1 or not isinstance(matches[0].get("folderPath"), str):
            raise ValueError("workspace_unavailable")
        folder = matches[0]["folderPath"]
        if cached and cached["folder"] != folder:
            cached = None  # never show a previous workspace binding as the new folder's catalog
        if not folder.startswith("/") or ".." in folder.split("/"):
            raise ValueError("skill_host_platform_unavailable")
        now = time.time()
        if cached and not refresh and now - cached["observed_at"] < 30:
            return {**base, **cached_value, "status": "available", "observed_at": cached["observed_at"]}
        adapter = ops.context.get("skill_host")
        if adapter is None:
            artifact_host = ops.context.get("artifact_host")
            adapter = SkillHost(getattr(artifact_host, "aliases", {}))
        raw = await adapter.scan(host, folder)
        if catalog_sources.host_binding(ops, host, skills=True) != binding:
            raise ValueError("host_binding_changed")
        value = _normalize(raw)
        value["catalog_digest"] = hashlib.sha256(json.dumps({"host": host, "workspace_id": workspace_id,
            "folder": folder, "source_binding": binding, "catalog": value["catalog_digest"]}, sort_keys=True).encode()).hexdigest()
        ops.db.execute("""INSERT INTO project_skill_catalogs VALUES(?,?,?,?,?,?)
            ON CONFLICT(host,workspace_id) DO UPDATE SET folder=excluded.folder,digest=excluded.digest,
                document=excluded.document,observed_at=excluded.observed_at""",
            (host, workspace_id, folder, value["catalog_digest"],
             json.dumps({**value, "_source_binding": binding}, sort_keys=True), now))
        return {**base, **value, "status": "available", "observed_at": now}
    except Exception as exc:  # noqa: BLE001 - never publish remote paths/errors/credentials
        reason = str(exc) if isinstance(exc, ValueError) and str(exc) in {
            "workspace_unavailable", "skill_adapter_unavailable", "skill_host_platform_unavailable", "host_binding_changed"} else "skill_inventory_unavailable"
        if catalog_sources.host_binding(ops, host, skills=True) != binding:
            cached, reason = None, "host_binding_changed"
        if cached:
            return {**base, **cached_value, "status": "available", "stale": True,
                    "observed_at": cached["observed_at"], "reason": reason}
        return {**base, "reason": reason}


async def read(ops, principal, project_id, host, workspace_id, *, refresh=False):
    if not principal.allows("observe"):
        raise OperationError("FORBIDDEN", "skill selection needs observe", 403)
    if type(refresh) is not bool:
        raise OperationError("INVALID_PARAMS", "invalid refresh flag", 422)
    catalog = await _catalog(ops, project_id, host, workspace_id, refresh=refresh)
    saved = selection(ops.db, project_id)
    current = {row["skill_id"]: row for row in catalog["skills"]}
    saved["unresolved"] = [ref for ref in saved["selected"] if (saved["host"], saved["workspace_id"]) != (host, workspace_id)
                          or ref["skill_id"] not in current or not current[ref["skill_id"]]["available"]
                          or current[ref["skill_id"]]["digest"] != ref["digest"]]
    return {"version": 1, "project_id": project_id, "host": host, "workspace_id": workspace_id,
            "catalog": catalog, "selection": saved}


def _check(ops, target, params, pre):
    if set(target) != {"project_id"} or set(params) != {"host", "workspace_id", "selected"}:
        raise OperationError("INVALID_PARAMS", "skill selection requires project, host, workspace and fixed selections", 422)
    _binding(ops, target["project_id"], params["host"], params["workspace_id"])
    refs = params["selected"]
    if not isinstance(refs, list) or len(refs) > 20:
        raise OperationError("INVALID_PARAMS", "select at most 20 skills", 422)
    seen = set()
    for ref in refs:
        if (not isinstance(ref, dict) or set(ref) != {"skill_id", "digest"}
                or not isinstance(ref["skill_id"], str) or not SKILL.fullmatch(ref["skill_id"])
                or not isinstance(ref["digest"], str) or not DIGEST.fullmatch(ref["digest"]) or ref["skill_id"] in seen):
            raise OperationError("INVALID_PARAMS", "skills require unique IDs and fixed SHA-256 digests", 422)
        seen.add(ref["skill_id"])
    if (set(pre) != {"expected_revision", "expected_catalog_digest"} or type(pre["expected_revision"]) is not int
            or pre["expected_revision"] < 0 or not isinstance(pre["expected_catalog_digest"], str)
            or not DIGEST.fullmatch(pre["expected_catalog_digest"])):
        raise OperationError("PRECONDITION_REQUIRED", "review the current selection revision and catalog digest", 422)
    saved = selection(ops.db, target["project_id"])
    if saved["revision"] != pre["expected_revision"]:
        raise OperationError("VERSION_CONFLICT", "the project skill selection changed", 409)
    return saved


def _validate_catalog(catalog, saved, params, pre):
    if catalog["status"] != "available" or catalog["stale"] or catalog["catalog_digest"] != pre["expected_catalog_digest"]:
        raise OperationError("SKILL_CATALOG_CHANGED", "read the current host skill catalog before saving", 409)
    known = {row["skill_id"]: row for row in catalog["skills"]}
    for ref in params["selected"]:
        row = known.get(ref["skill_id"])
        retained = (saved["host"], saved["workspace_id"]) == (params["host"], params["workspace_id"]) and ref in saved["selected"]
        if not retained and (not row or not row["available"] or row["digest"] != ref["digest"]):
            raise OperationError("SKILL_SOURCE_CHANGED", "a selected skill is missing, unsafe or changed", 409)


def _admit(ops, principal, target, params, pre):
    saved = _check(ops, target, params, pre)
    row = ops.db.execute("SELECT * FROM project_skill_catalogs WHERE host=? AND workspace_id=?",
                         (params["host"], params["workspace_id"])).fetchone()
    cached = catalog_sources.cached_document(row, catalog_sources.host_binding(ops, params["host"], skills=True))
    if cached is None or time.time() - row["observed_at"] > 300:
        raise OperationError("SKILL_CATALOG_CHANGED", "read the current host skill catalog before saving", 409)
    _validate_catalog({**cached, "status": "available", "stale": False}, saved, params, pre)


async def _run(ctx):
    saved = _check(ctx.service, ctx.target, ctx.params, ctx.preconditions)
    current = await _catalog(ctx.service, ctx.target["project_id"], ctx.params["host"], ctx.params["workspace_id"], refresh=True)
    _validate_catalog(current, saved, ctx.params, ctx.preconditions)
    def save():
        saved = _check(ctx.service, ctx.target, ctx.params, ctx.preconditions)
        now = time.time()
        ctx.service.db.execute("""INSERT INTO project_skill_selections VALUES(?,?,?,?,?,?)
            ON CONFLICT(project_id) DO UPDATE SET revision=excluded.revision,host=excluded.host,
                workspace_id=excluded.workspace_id,document=excluded.document,updated_at=excluded.updated_at""",
            (ctx.target["project_id"], saved["revision"] + 1, ctx.params["host"], ctx.params["workspace_id"],
             json.dumps(ctx.params["selected"], sort_keys=True), now))
        ctx.service.journal.api_event("project", ctx.target["project_id"], "project.skills.updated",
                                      {"operation_id": ctx.operation_id, "application": "selected_not_applied"}, actor=ctx.actor)
        return selection(ctx.service.db, ctx.target["project_id"])
    return ctx.effect("project_skill_selection", save, request=ctx.params)
