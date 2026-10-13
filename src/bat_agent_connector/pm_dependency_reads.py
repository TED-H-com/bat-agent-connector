"""Current-authority dependency projections. Reads never contact the host or scan files."""
from __future__ import annotations

import json
import re

from . import pm_dependencies as dep
from . import pm_maintenance_policy, pm_workspace
from .operations import OperationError
from .pm_dependency_budget import MAX_READ_BYTES, bounded
from .pm_dependency_parser import KINDS, package_name, stable
from .pm_release_baselines import _size, _snapshot_read

MAX_REPOSITORIES = 32
MAX_PEER_BYTES = 1_000_000
MAX_LINKS = 2000
FRESHNESS = {"stale": None, "stale_reason": "repository_head_not_observed"}


def _current(ops, principal, wid):
    if not principal.allows("observe"):
        raise OperationError("FORBIDDEN", "Dependency reads require observe scope / 讀取相依需要觀察權限", 403)
    pm_workspace.require_member(ops.db, principal, wid)
    try:
        return dep.current_binding(ops, principal, wid)
    except OperationError:
        # All source-derived rows, versions and counts withdraw as a unit.
        return None


def _peer_selection(result, doc):
    """Expose why the frozen peer set may miss visible peers; such a read is partial."""
    reasons = [reason for reason in doc.get("peer_selection_reasons") or [] if isinstance(reason, str)]
    if reasons:
        result["cross_repository"] = {**result["cross_repository"], "selection_reasons": reasons}
        result["partial"] = True


def _empty(wid):
    return {"workspace_id": wid, "version": None, "workspace_version": None, "source": None,
            "capture": {"revision_id": None, "commit_sha": None, "captured_at": None, "status": "unknown"},
            **FRESHNESS, "partial": True, "reason": "source_unavailable", "components": [],
            "dependencies": {kind: [] for kind in KINDS}, "edges": [], "declared": [], "files": [], "unreadable_paths": [],
            "verification_hints": [], "counts": None, "maintenance_owner": None, "maintenance_owner_reason": "not_available",
            "cross_repository": {"status": "bounded", "complete": False}, "cross_repository_edges": [], "external_components": []}


def _owner(ops, principal, wid):
    policy = pm_maintenance_policy.current_policy(ops, principal, wid).get("policy")
    if policy:
        # The adopting actor/controller is authority, not a maintenance assignment.
        owner = policy["document"]["policy"].get("maintenance_owner")
        if isinstance(owner, str) and owner.strip():
            return {"actor": owner, "policy_id": policy["policy_id"], "basis": "adopted_maintenance_policy"}
    return None


def _visible_record(ops, principal, wid, revision_id):
    saved = dep.record(ops.db, wid, revision_id)
    dep.source_access(ops.db, principal, wid, saved["source_binding"])
    return saved


_PEER_PAGE = 64
# Candidate heads examined per capture, visible or not. It bounds capture work only.
MAX_PEER_SCAN = 1024


def _peer_candidates(ops, principal, wid, cursor, limit):
    return ops.db.execute("""SELECT h.workspace_id,h.revision_id FROM pm_dependency_heads h
        JOIN pm_workspaces w ON w.workspace_id=h.workspace_id
        JOIN pm_workspace_worktrees b ON b.workspace_id=w.workspace_id
        JOIN pm_dependency_revisions r ON r.revision_id=h.revision_id
        WHERE h.workspace_id!=? AND (? OR EXISTS
            (SELECT 1 FROM pm_workspace_members m WHERE m.workspace_id=w.workspace_id AND m.actor=?))
        AND EXISTS (SELECT 1 FROM json_each(w.repositories) j WHERE j.value=json_extract(b.binding,'$.target.repository'))
        AND json_extract(r.source_binding,'$.binding_digest')=json_extract(b.binding,'$.binding_digest')
        AND json_extract(r.source_binding,'$.operation_id')=json_extract(b.binding,'$.operation_id')
        AND (h.workspace_id>? OR (h.workspace_id=? AND h.revision_id>?))
        ORDER BY h.workspace_id,h.revision_id LIMIT ?""",
        (wid, principal.admin, principal.actor, cursor[0], cursor[0], cursor[1], limit)).fetchall()


def discover_peers(ops, principal, wid, reasons=None):
    """Capture a bounded peer selection once; reads never repopulate its hidden slots.

    Visibility, same-repository, duplicate-repository and byte-budget filters run
    before the ``MAX_REPOSITORIES`` cap, so a hidden or ineligible row takes no
    slot. ``reasons`` receives ``peer_scan_limit`` when the candidate scan bound
    stops with candidates left, and ``peer_byte_budget`` when a visible peer did
    not fit the byte budget.
    """
    reasons = [] if reasons is None else reasons
    own = _current(ops, principal, wid)
    own_repository = own["target"]["repository"] if own else None
    peers, repositories, size, scanned, cursor = [], set(), 0, 0, ("", "")
    while len(peers) < MAX_REPOSITORIES:
        limit = min(_PEER_PAGE, MAX_PEER_SCAN - scanned)
        rows = _peer_candidates(ops, principal, wid, cursor, limit + 1)
        more = len(rows) > limit
        if limit <= 0:
            if more:
                reasons.append("peer_scan_limit")
            break
        for row in rows[:limit]:
            scanned += 1
            cursor = (row["workspace_id"], row["revision_id"])
            if _current(ops, principal, row["workspace_id"]) is None:
                continue
            try:
                record = _visible_record(ops, principal, row["workspace_id"], row["revision_id"])
            except OperationError:
                continue
            repository = record["document"]["repository"]
            # Same-repository rows are never shown as peers, so they take no slot either.
            if repository == own_repository or repository in repositories:
                continue
            length = _size(record["document"])
            if size + length > MAX_PEER_BYTES:
                if "peer_byte_budget" not in reasons:
                    reasons.append("peer_byte_budget")
                continue
            repositories.add(repository)
            size += length
            # Freeze this bounded selection; later readers never refill hidden slots.
            peers.append({"workspace_id": row["workspace_id"], "revision_id": row["revision_id"],
                          "document_digest": record["document_digest"]})
            if len(peers) >= MAX_REPOSITORIES:
                break
        if not more:
            break
    return peers


def _related(ops, principal, wid, doc):
    """Filter the fixed capture-time peer set before constructing any graph or count."""
    peers = []
    for source in doc.get("peer_revisions", []):
        peer_id = source["workspace_id"]
        try:
            binding = _current(ops, principal, peer_id)
            if binding is None:
                continue
            record = _visible_record(ops, principal, peer_id, source["revision_id"])
            if record["document_digest"] != source["document_digest"] or record["document"]["repository"] == doc["repository"]:
                continue
        except OperationError:
            continue
        peers.append({"workspace_id": peer_id, **record["document"], **FRESHNESS})
    return peers


def _links(documents, *, byte_limit=500_000, diagnostics=None):
    components = [c for d in documents for c in d["components"]]
    targets = {}
    for c in components:
        if c["published_name"] is not None:
            targets.setdefault((c["ecosystem"], package_name(c["ecosystem"], c["published_name"])), []).append(c)
    links, size = [], 2
    for doc in documents:
        lookup = {d["key"]: d for d in doc["dependencies"]}
        sources = {c["component_id"]: c for c in doc["components"]}
        for edge in doc["edges"]:
            dependency = lookup[edge["dependency_key"]]
            source = sources[edge["component_id"]]
            for target in targets.get((dependency["ecosystem"], package_name(dependency["ecosystem"], dependency["name"])), []):
                if source["component_id"] == target["component_id"]:
                    continue
                if len(links) >= MAX_LINKS:
                    if diagnostics is not None:
                        diagnostics["truncated"] = True
                    return links
                link = {"link_id": stable("pmdeplink_", edge["edge_id"], target["component_id"]),
                    "component_id": source["component_id"], "target_component_id": target["component_id"],
                    "dependency_key": edge["dependency_key"], "edge_id": edge["edge_id"], "relation": edge["relation"],
                    "version": edge["version"], "resolved": edge["resolved"], "evidence_level": edge["evidence_level"],
                    "version_reason": edge.get("version_reason"), "resolution_reason": edge.get("resolution_reason"),
                    "basis": "exact_published_package", "direction": "component_to_component",
                    "evidence_refs": [*source["evidence_refs"],
                        *(ref for ref in edge["evidence_refs"] if ref not in source["evidence_refs"]), *target["evidence_refs"]]}
                length = _size(link) + 2
                if size + length > byte_limit:
                    if diagnostics is not None:
                        diagnostics["truncated"] = True
                    return links
                links.append(link)
                size += length
    return links


def _legacy_budget(result):
    """Legacy captures remain readable; any omitted collections are explicit.

    New captures reserve projection space before committing. This fallback only
    handles older documents/declarations that predate those storage limits.
    """
    if _size(result) <= MAX_READ_BYTES - 4096:
        return result
    result.update(partial=True, reason="projection_byte_budget", counts=None,
                  projection_omitted_collections=[])
    for field in ("verification_hints", "declared", "edges", "dependencies", "components", "files", "unreadable_paths"):
        if field not in result:
            continue
        result["projection_omitted_collections"].append(field)
        result[field] = {kind: [] for kind in KINDS} if field == "dependencies" else []
        if field == "edges":
            result["dependencies"] = {kind: [] for kind in KINDS}
            result["projection_omitted_collections"].append("dependencies")
        if _size(result) <= MAX_READ_BYTES - 4096:
            break
    return bounded(result)


@_snapshot_read
def get(ops, principal, workspace_id):
    wid = workspace_id
    binding = _current(ops, principal, wid)
    result = _empty(wid)
    if binding is None:
        return result
    current = dep.head(ops.db, wid, dep.source_key(binding))
    result.update(version=current["version"], workspace_version=pm_workspace.require_member(ops.db, principal, wid)["version"],
        source={"repository": binding["target"]["repository"], "commit_sha": binding["source_sha"], "binding_digest": binding["binding_digest"]},
        reason="not_captured", **FRESHNESS)
    if current["revision_id"] is None:
        return result
    try:
        saved = _visible_record(ops, principal, wid, current["revision_id"])
    except OperationError as exc:
        if exc.status not in (403, 404):
            raise
        return _empty(wid)
    doc = saved["document"]
    declarations = dep.declared(ops.db, wid, dep.source_key(binding))
    peers = _related(ops, principal, wid, doc)
    owner = _owner(ops, principal, wid)
    grouped = {kind: [] for kind in KINDS}
    for dependency in doc["dependencies"]:
        for kind in dependency["kinds"]:
            grouped[kind].append(dependency)
    result.update({key: value for key, value in doc.items()
                   if key not in {"dependencies", "peer_revisions", "peer_selection_reasons"}})
    _peer_selection(result, doc)
    result.update(dependencies=grouped, declared=declarations, **FRESHNESS,
        reason=None, maintenance_owner=owner, maintenance_owner_reason=None if owner else "not_named_in_visible_policy",
        components=[{**c, "maintenance_owner": owner, "maintenance_owner_reason": None if owner else "not_named_in_visible_policy"} for c in doc["components"]])
    result["counts"] = {"components": len(doc["components"]), "dependencies": len(doc["dependencies"]),
        "edges": len(doc["edges"]), "declared": len(declarations), "basis": "captured_revision", "partial": doc["partial"]}
    _legacy_budget(result)
    selected, diagnostics = [], {}
    size = _size(result)
    for peer in peers:
        components = [{**c, **FRESHNESS, "capture_revision_id": peer["capture"]["revision_id"]} for c in peer["components"]]
        length = _size(components) + 2
        if size + length > MAX_READ_BYTES - 4096:
            diagnostics["truncated"] = True
            continue
        result["external_components"].extend(components)
        selected.append(peer)
        size += length
    if not result.get("projection_omitted_collections"):
        result["cross_repository_edges"] = _links([doc, *selected], byte_limit=MAX_READ_BYTES - size - 4096, diagnostics=diagnostics)
    if diagnostics.get("truncated"):
        result["partial"] = True
        result["cross_repository"]["reason"] = "projection_byte_budget"
    return bounded(result)


@_snapshot_read
def revisions(ops, principal, workspace_id, *, after=0, limit=20):
    if type(after) is not int or not 0 <= after < 2**63 or type(limit) is not int or not 1 <= limit <= 100:
        raise dep.bad()
    binding = _current(ops, principal, workspace_id)
    result = {"workspace_id": workspace_id, "revisions": [], "next_cursor": None, "reason": None}
    if binding is None:
        return {**result, "reason": "source_unavailable"}
    rows = ops.db.execute("""SELECT r.revision_id,r.sequence,s.summary FROM pm_dependency_revisions r
        LEFT JOIN pm_dependency_revision_summaries s ON s.revision_id=r.revision_id
        WHERE r.workspace_id=? AND r.source_key=? AND r.sequence>? ORDER BY r.sequence LIMIT ?""",
        (workspace_id, dep.source_key(binding), after, limit + 1)).fetchall()
    for row in rows[:limit]:
        # Source identity/access was checked once above. Old rows have explicit
        # unknown metadata; no migration or full-document decode happens on reads.
        summary = json.loads(row["summary"]) if row["summary"] is not None else {
            "revision_id": row["revision_id"], "captured_at": None, "commit_sha": None,
            "status": "unknown", "partial": True, "reason": "summary_not_recorded",
            "repository": binding["target"]["repository"], "binding_digest": binding["binding_digest"]}
        result["revisions"].append({**summary, **FRESHNESS})
    if len(rows) > limit:
        result["next_cursor"] = rows[limit - 1]["sequence"]
    return result


@_snapshot_read
def impact(ops, principal, workspace_id, *, dependency):
    if not isinstance(dependency, str) or not re.fullmatch(r"pmdepkey_[0-9a-f]{32}", dependency):
        raise dep.bad()
    current = get(ops, principal, workspace_id)
    result = {"workspace_id": workspace_id, "dependency": dependency, "capture": current["capture"],
              **FRESHNESS, "partial": current["partial"], "components": [],
              "coverage": "captured_edges_only", "declared": [], "counts": None}
    if current["capture"]["revision_id"] is None:
        # Distinguish a withdrawn source from one that was never captured.
        return {**result, "reason": current["reason"], "cross_repository": current["cross_repository"]}
    flat = [d for values in current["dependencies"].values() for d in values]
    doc = {**current, "dependencies": list({d["key"]: d for d in flat}.values())}
    saved = _visible_record(ops, principal, workspace_id, current["capture"]["revision_id"])["document"]
    peers = _related(ops, principal, workspace_id, saved)
    documents = [doc, *peers]
    if not any(d["key"] == dependency for document in documents for d in document["dependencies"]) and not any(
            d["dependency_key"] == dependency for d in current["declared"]):
        omitted = any(d["key"] == dependency for d in saved["dependencies"])
        reason = "dependency_omitted" if omitted else "capture_budget" if saved.get("omitted_edges") else "dependency_not_observed"
        return {**result, "partial": True, "reason": reason}
    edges = [e for d in documents for e in d["edges"]]
    links = _links(documents)
    reached = {e["component_id"]: 1 for e in edges if e["dependency_key"] == dependency}
    # Monotone shortest distances handle cycles without revisiting indefinitely.
    for _ in range(sum(len(d["components"]) for d in documents)):
        changed = False
        for link in links:
            source, target = link["component_id"], link["target_component_id"]
            if target in reached and (source not in reached or reached[source] > reached[target] + 1):
                reached[source] = reached[target] + 1
                changed = True
        if not changed:
            break
    for document in documents:
        for c in document["components"]:
            cid = c["component_id"]
            if cid not in reached:
                continue
            evidence = [e for e in edges if e["component_id"] == cid and e["dependency_key"] == dependency]
            evidence += [link for link in links if link["component_id"] == cid and link["target_component_id"] in reached]
            result["components"].append({**c, **FRESHNESS, "distance": reached[cid], "direct": reached[cid] == 1,
                                          "relations": sorted({e["relation"] for e in evidence}), "edges": evidence})
    result["components"].sort(key=lambda c: (c["distance"], c["component_id"]))
    result["declared"] = [d for d in current["declared"] if d["dependency_key"] == dependency]
    result["partial"] = True
    result["reason"] = "bounded_captured_graph"
    result["cross_repository"] = {"status": "bounded", "complete": False}
    _peer_selection(result, saved)
    result["counts"] = {"components": len(result["components"]), "basis": "visible_captured_graph", "partial": True}
    # Impact may repeat evidence along several paths. Keep a bounded prefix of
    # complete component rows, with counts explicitly referring to returned rows.
    base_size = _size({**result, "components": []})
    rows, size = [], base_size
    for component in result["components"]:
        length = _size(component) + 2
        if size + length > MAX_READ_BYTES - 4096:
            result["reason"] = "projection_byte_budget"
            break
        rows.append(component)
        size += length
    result["components"] = rows
    result["counts"] = {"components": len(rows), "basis": "returned_components", "partial": True}
    return bounded(result)
