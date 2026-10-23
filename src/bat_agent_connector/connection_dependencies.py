"""Read-only, principal-local inputs for a future connection dependency review.

Completeness describes coverage, not permission to detach. Native selection and
resources always require a separate review. No read here refreshes observations.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import time

from . import dashboard_sync, pm_access, pm_source_lineage, pm_source_reads, pm_workspace, repository_sync
from .connection_endpoints import setup_endpoint
from .inventory import _observation_reason
from .operations import TERMINAL, OperationError
from .pm_release_baselines import _snapshot

PAGE_SIZE = 200
MAX_ROWS = 1000
SERVER_DOMAINS = ("identity", "work_history", "repository_workspace", "current_source", "unsettled_operations")
# Explicit journal relationships only; never infer dependencies from names or paths.
TABLES = (
    ("projects", "project", "project_id"), ("work_items", "work_item", "work_item_id"),
    ("work_item_links", "work_link", "link_id"), ("pm_workspaces", "pm_workspace", "workspace_id"),
    ("work_profiles", "work_profile", "profile_id"), ("work_profile_defaults", "profile_default", "sequence"),
    ("pm_project_bindings", "project_binding", "binding_id"), ("pm_work_bindings", "work_binding", "binding_id"),
    ("pm_work_packages", "work_package", "package_id"), ("work_attempts", "attempt", "attempt_id"),
    ("pm_work_start_children", "start_child", "operation_id"),
    ("checkpoints", "checkpoint", "checkpoint_id"), ("checkpoint_runs", "checkpoint_run", "operation_id"),
)
JSON_FIELDS = {"document", "repositories"}
REF_FIELDS = {"project_id", "work_item_id", "workspace_id", "profile_id", "package_id", "attempt_id",
              "operation_id", "start_operation_id", "parent_id", "predecessor_attempt_id", "origin_id"}


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"),
                                     allow_nan=False).encode()).hexdigest()


def open_connection_operation(ops, principal_id, target):
    """The existing busy gate, shared with dependency reads; creates have no ID."""
    if "connection_id" not in target:
        return None
    return ops.db.execute("""SELECT operation_id FROM operations
        WHERE action IN ('setup.connection.apply','connection.reference.apply','connection.reference.detach')
        AND status NOT IN ('succeeded','failed','cancelled')
        AND json_extract(external_refs,'$.admission_binding.principal_id')=?
        AND json_extract(target,'$.installation_ref')=? AND json_extract(target,'$.connection_id')=?
        ORDER BY created_at,operation_id LIMIT 1""",
        (principal_id, target["installation_ref"], target["connection_id"])).fetchone()


def _pages(ops, unavailable):
    before = before_id = None
    while True:
        page = ops.list(before=before, before_id=before_id, limit=PAGE_SIZE)
        yield page["operations"]
        following = page.get("next_before"), page.get("next_before_id")
        if following == (None, None):
            return
        if None in following or before is not None and following >= (before, before_id):
            unavailable("unsettled_operations", "COVERAGE_GAP")
            return
        before, before_id = following


def _documents(value):
    if isinstance(value, list):
        for child in value:
            yield from _documents(child)
    elif isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _documents(child)


def _related(value, connection, repositories, identities):
    documents = tuple(_documents(value))
    hosts = {doc["host"] for doc in documents if isinstance(doc.get("host"), str)}
    # PM membership IDs are not BAT workspace locations.
    workspaces = {doc["workspace_id"] for doc in documents if isinstance(doc.get("workspace_id"), str)
                  and not doc["workspace_id"].startswith("ws_")}
    location_matches = (not hosts or connection["service_ref"] in hosts) and (
        connection["workspace_ref"] is None or not workspaces or connection["workspace_ref"] in workspaces)
    for doc in documents:
        if location_matches:
            if doc.get("host") == connection["service_ref"] and connection["service_ref"] is not None:
                if connection["workspace_ref"] is None or doc.get("workspace_id", connection["workspace_ref"]) == connection["workspace_ref"]:
                    return True
            repository, names = doc.get("repository"), doc.get("repositories")
            if isinstance(repository, str) and repository in repositories or isinstance(names, list) and any(
                    isinstance(name, str) and name in repositories for name in names):
                return True
        if any(isinstance(doc.get(key), str) and doc[key] in identities for key in REF_FIELDS):
            return True
        if any(identity in identities for _, identity in pm_source_lineage.references(doc)):
            return True
    return False


def _visible(ops, principal, kind, row) -> bool | None:
    """True: visible; False: access refused; None: visibility could not be read."""
    try:
        if (row.get("workspace_id") or "").startswith("ws_"):
            try:
                pm_workspace.require_member(ops.db, principal, row["workspace_id"])
            except OperationError as exc:
                if (exc.code, exc.status) in {("NOT_FOUND", 404), ("FORBIDDEN", 403)}:
                    return False
                raise
        if kind in {"work_item", "checkpoint"} and not pm_source_lineage.visible(ops.db, principal, kind, row[kind + "_id"]):
            return False
        if kind == "work_link" and not pm_source_lineage.visible(ops.db, principal, row["kind"], row["ref"]):
            return False
        oid = row.get("operation_id", row.get("start_operation_id", row.get("link_operation")))
        if oid:
            return pm_access.visible(ops.db, principal, ops.get(oid, steps=False))
        return True
    except (OperationError, sqlite3.Error):
        return None


def read(ops, principal, installation_ref, connection_id, *, offset=0, limit=PAGE_SIZE):
    """Return v1 impacts, domain coverage and a digest of the entire checked set.

    Offset pages are presentation only: callers must compare dependency_digest
    between pages and re-read it at admission. No total or hidden-reference counts
    are returned. An incomplete result is never authorization for an effect.
    """
    if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= PAGE_SIZE:
        raise OperationError("INVALID_PARAMS", "Use a nonnegative offset and limit 1-200 / 請使用非負位移與 1–200 的頁面大小", 422)
    with _snapshot(ops.db), pm_source_reads.scope(ops.db, principal):
        return _read(ops, principal, installation_ref, connection_id, offset, limit)


def _read(ops, principal, installation_ref, connection_id, offset, limit):
    from . import setup_connections
    identity = dashboard_sync.identity(ops.journal, principal)
    target = {"installation_ref": installation_ref, "connection_id": connection_id}
    connection = setup_connections._row(ops, identity["principal_id"], target)
    if not connection or _visible(ops, principal, "connection", connection) is not True:
        raise OperationError("NOT_FOUND", "Connection not found in this principal and installation", 404)
    domains = {key: {"covered": True, "complete": True, "reason": None} for key in SERVER_DOMAINS}
    for key in ("native_selection", "native_resources"):
        domains[key] = {"covered": False, "complete": False, "reason": "NATIVE_REVIEW_REQUIRED"}
    impacts = {}

    def unavailable(domain, reason="REFERENCES_UNAVAILABLE"):
        domains[domain].update(complete=False, reason=reason)

    def add(kind, ref, body, domain, disposition="informative"):
        impacts[kind, str(ref)] = ({"kind": kind, "ref": str(ref), "revision": _digest(body),
                                   "disposition": disposition}, domain)

    add("connection", connection_id, {**identity, **connection}, "identity")
    inventory = ops.context["inventory"]
    host = inventory.config.hosts.get(connection["service_ref"])
    bound = host and {"url": setup_endpoint(host.url), "fingerprint": host.fingerprint} == connection["association"] and host.profile_id == connection["remote_profile_id"]
    evidence = inventory.current_source_evidence(connection["service_ref"])
    reason = evidence.reason or _observation_reason(evidence.observation, time.time(), inventory.settings.stale_after_s)
    if not bound or reason or not evidence.discovery_bodies:
        unavailable("current_source", "SOURCE_UNAVAILABLE")
    if connection["service_ref"]:
        add("source_observation", connection["service_ref"], {
            "bound": bool(bound), "reason": reason, "observation": evidence.observation,
            "discovery": sorted(evidence.discovery_bodies, key=_digest)}, "current_source")
    bindings = sorted((row for row in repository_sync.capabilities(ops) if bound and row["host"] == connection["service_ref"]
                and (connection["workspace_ref"] is None or row["workspace_id"] == connection["workspace_ref"])), key=_digest)
    repositories = {row["repository"] for row in bindings}
    for repository in sorted(repositories):
        add("repository", repository, sorted((r for r in bindings if r["repository"] == repository), key=_digest), "repository_workspace")
    if bound and connection["workspace_ref"]:
        add("bat_workspace", connection["workspace_ref"], bindings, "repository_workspace")
    if not bound:
        unavailable("repository_workspace", "SOURCE_UNAVAILABLE")
        unavailable("work_history", "SOURCE_UNAVAILABLE")

    records = []
    for table, kind, key in TABLES:
        if not ops.db.execute("SELECT 1 FROM sqlite_schema WHERE name=? AND type='table'", (table,)).fetchone():
            unavailable("work_history", "COVERAGE_GAP")
            continue
        for raw in ops.db.execute(f"SELECT * FROM {table}"):  # noqa: S608 - fixed table names
            row = dict(raw)
            for field in (JSON_FIELDS | ({"request"} if kind == "start_child" else set())) & row.keys():
                if row[field] is not None:
                    row[field] = json.loads(row[field])
            ref = str(row[key]) + ("/" + str(row["revision"]) if kind == "work_profile" else "")
            records.append((kind, ref, row))
    busy = open_connection_operation(ops, identity["principal_id"], target)
    identities = {connection["operation_id"]} | ({busy["operation_id"]} if busy else set())
    for page in _pages(ops, unavailable):
        # Keep the paired raw cursor even when this page has no visible rows.
        for op in page:
            own = op["target"].get("installation_ref")
            original = op["target"].get("connection_id") == connection_id or (op.get("result") or {}).get("connection_id") == connection_id
            if own is not None and (own != installation_ref or
                    op["operation_id"] not in identities and not original):
                continue
            if own is not None and op["external_refs"].get("admission_binding", {}).get("principal_id") != identity["principal_id"]:
                continue
            if own is not None and original:
                identities.add(op["operation_id"])
            records.append(("operation", op["operation_id"], op))

    # Fixed point over explicit IDs includes original/child effects and historical
    # Work bindings even if their records precede the record establishing the link.
    selected = []
    while True:
        remaining = []
        for kind, ref, row in records:
            if ref in identities or _related(row, connection, repositories, identities):
                selected.append((kind, ref, row))
                identities.add(ref)
                identities.update(identity for _, identity in pm_source_lineage.references(row))
                for key in REF_FIELDS - {"workspace_id"}:
                    if isinstance(row.get(key), str):
                        identities.add(row[key])
            else:
                remaining.append((kind, ref, row))
        if len(remaining) == len(records):
            break
        records = remaining
    for kind, ref, row in selected:
        domain = "repository_workspace" if kind == "pm_workspace" else "work_history"
        disposition = "informative"
        if kind == "operation":
            row = ops.get(ref)
            unsettled = row["status"] not in TERMINAL or any(step["status"] in {"started", "sent", "uncertain"} for step in row["steps"])
            domain = "unsettled_operations" if unsettled else "work_history"
            disposition = "blocking" if unsettled else "informative"
            kind = "operation" if unsettled else "operation_history"
        visibility = _visible(ops, principal, kind, row)
        if visibility is not True:
            if visibility is None or disposition == "blocking":
                unavailable(domain)
            continue
        if kind in {"operation", "operation_history"}:
            # Requests/responses bind original publication and launch receipts,
            # including changes that do not increment the operation's version.
            row["steps"] = [dict(step) for step in ops.db.execute(
                "SELECT * FROM operation_steps WHERE operation_id=? ORDER BY seq", (ref,))]
        add(kind, ref, row, domain, disposition)
    rows = [value[0] for _, value in sorted(impacts.items())]
    if len(rows) > MAX_ROWS:
        for domain in {value[1] for value in impacts.values()}:
            unavailable(domain, "DEPENDENCY_LIMIT")
    digest = _digest({"version": 1, "identity": identity, "target": target, "domains": domains, "impacts": rows})
    end = min(offset + limit, MAX_ROWS)
    return {"version": 1, "impacts": rows[offset:end], "domains": domains, "dependency_digest": digest,
            "server_complete": all(domains[key]["complete"] for key in SERVER_DOMAINS),
            "complete": all(domain["complete"] for domain in domains.values()),
            "next_offset": end if end < min(len(rows), MAX_ROWS) else None}
