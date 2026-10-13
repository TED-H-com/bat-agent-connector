"""Current-authority dependency projections. Reads never contact the host or scan files."""
from __future__ import annotations

import re

from . import pm_dependencies as dep
from . import pm_maintenance_policy, pm_workspace
from .operations import OperationError
from .pm_dependency_parser import KINDS, stable
from .pm_release_baselines import _bounded, _snapshot_read

MAX_REPOSITORIES = 32
MAX_PEER_BYTES = 1_000_000
MAX_LINKS = 2000


def _current(ops, principal, wid):
    if not principal.allows("observe"):
        raise dep.bad("FORBIDDEN", 403)
    pm_workspace.require_member(ops.db, principal, wid)
    try:
        return dep.current_binding(ops, principal, wid)
    except OperationError:
        # All source-derived rows, versions and counts withdraw as a unit.
        return None


def _empty(wid):
    return {"workspace_id": wid, "version": None, "workspace_version": None, "source": None,
            "capture": {"revision_id": None, "commit_sha": None, "captured_at": None, "status": "unknown"},
            "stale": None, "partial": True, "reason": "source_unavailable", "components": [],
            "dependencies": {kind: [] for kind in KINDS}, "edges": [], "declared": [], "files": [],
            "verification_hints": [], "counts": None, "maintenance_owner": None,
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


def discover_peers(ops, principal, wid):
    """Capture a bounded peer selection once; reads never repopulate its hidden slots."""
    rows = ops.db.execute("""SELECT h.workspace_id,h.revision_id FROM pm_dependency_heads h
        JOIN pm_workspaces w ON w.workspace_id=h.workspace_id
        JOIN pm_workspace_worktrees b ON b.workspace_id=w.workspace_id
        JOIN pm_dependency_revisions r ON r.revision_id=h.revision_id
        WHERE h.workspace_id!=? AND (? OR EXISTS
            (SELECT 1 FROM pm_workspace_members m WHERE m.workspace_id=w.workspace_id AND m.actor=?))
        AND EXISTS (SELECT 1 FROM json_each(w.repositories) j WHERE j.value=json_extract(b.binding,'$.target.repository'))
        AND json_extract(r.source_binding,'$.binding_digest')=json_extract(b.binding,'$.binding_digest')
        AND json_extract(r.source_binding,'$.operation_id')=json_extract(b.binding,'$.operation_id')
        ORDER BY h.workspace_id LIMIT ?""", (wid, principal.admin, principal.actor, MAX_REPOSITORIES)).fetchall()
    peers, repositories, size = [], set(), 0
    for row in rows:
        if _current(ops, principal, row["workspace_id"]) is not None:
            try:
                record = _visible_record(ops, principal, row["workspace_id"], row["revision_id"])
            except OperationError:
                continue
            repository = record["document"]["repository"]
            # Freeze this bounded selection; later readers never refill hidden slots.
            from .pm_release_baselines import _size
            length = _size(record["document"])
            if repository in repositories or size + length > MAX_PEER_BYTES:
                continue
            repositories.add(repository)
            size += length
            peers.append({"workspace_id": row["workspace_id"], "revision_id": row["revision_id"],
                          "document_digest": record["document_digest"]})
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
        peers.append({"workspace_id": peer_id, "stale": record["document"]["commit_sha"] != binding["source_sha"], **record["document"]})
    return peers


def _links(documents):
    components = [c for d in documents for c in d["components"]]
    targets = {}
    for c in components:
        if c["published_name"] is not None:
            targets.setdefault((c["ecosystem"], c["name"]), []).append(c)
    links = []
    for doc in documents:
        lookup = {d["key"]: d for d in doc["dependencies"]}
        sources = {c["component_id"]: c for c in doc["components"]}
        for edge in doc["edges"]:
            dependency = lookup[edge["dependency_key"]]
            source = sources[edge["component_id"]]
            for target in targets.get((dependency["ecosystem"], dependency["name"]), []):
                if source["component_id"] == target["component_id"]:
                    continue
                if len(links) >= MAX_LINKS:
                    return links
                links.append({"link_id": stable("pmdeplink_", edge["edge_id"], target["component_id"]),
                    "component_id": source["component_id"], "target_component_id": target["component_id"],
                    "dependency_key": edge["dependency_key"], "edge_id": edge["edge_id"], "relation": edge["relation"],
                    "version": edge["version"], "resolved": edge["resolved"], "evidence_level": edge["evidence_level"],
                    "basis": "exact_published_package", "direction": "component_to_component",
                    "evidence_refs": [*source["evidence_refs"], *target["evidence_refs"]]})
    return links


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
        reason="not_captured", stale=False)
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
    result.update({key: value for key, value in doc.items() if key not in {"dependencies", "peer_revisions"}})
    result.update(dependencies=grouped, declared=declarations, stale=doc["commit_sha"] != binding["source_sha"],
        reason=None, maintenance_owner=owner, components=[{**c, "maintenance_owner": owner} for c in doc["components"]],
        cross_repository_edges=_links([doc, *peers]),
        external_components=[{**c, "stale": peer["stale"], "capture_revision_id": peer["capture"]["revision_id"]} for peer in peers for c in peer["components"]])
    result["counts"] = {"components": len(doc["components"]), "dependencies": len(doc["dependencies"]),
        "edges": len(doc["edges"]), "declared": len(declarations), "basis": "captured_revision", "partial": doc["partial"]}
    return _bounded(result)


@_snapshot_read
def revisions(ops, principal, workspace_id, *, after=0, limit=20):
    if type(after) is not int or not 0 <= after < 2**63 or type(limit) is not int or not 1 <= limit <= 100:
        raise dep.bad()
    binding = _current(ops, principal, workspace_id)
    result = {"workspace_id": workspace_id, "revisions": [], "next_cursor": None}
    if binding is None:
        return result
    rows = ops.db.execute("""SELECT revision_id,sequence FROM pm_dependency_revisions
        WHERE workspace_id=? AND source_key=? AND sequence>? ORDER BY sequence LIMIT ?""",
        (workspace_id, dep.source_key(binding), after, limit + 1)).fetchall()
    for row in rows[:limit]:
        saved = _visible_record(ops, principal, workspace_id, row["revision_id"])
        doc = saved["document"]
        result["revisions"].append({**doc["capture"], "repository": doc["repository"], "binding_digest": doc["binding_digest"],
            "stale": doc["commit_sha"] != binding["source_sha"], "partial": doc["partial"]})
    if len(rows) > limit:
        result["next_cursor"] = rows[limit - 1]["sequence"]
    return result


@_snapshot_read
def impact(ops, principal, workspace_id, *, dependency):
    if not isinstance(dependency, str) or not re.fullmatch(r"pmdepkey_[0-9a-f]{32}", dependency):
        raise dep.bad()
    current = get(ops, principal, workspace_id)
    result = {"workspace_id": workspace_id, "dependency": dependency, "capture": current["capture"],
              "stale": current["stale"], "partial": current["partial"], "components": [],
              "coverage": "captured_edges_only", "declared": [], "counts": None}
    if current["capture"]["revision_id"] is None:
        return result
    flat = [d for values in current["dependencies"].values() for d in values]
    doc = {**current, "dependencies": list({d["key"]: d for d in flat}.values())}
    saved = _visible_record(ops, principal, workspace_id, current["capture"]["revision_id"])["document"]
    peers = _related(ops, principal, workspace_id, saved)
    documents = [doc, *peers]
    if not any(d["key"] == dependency for document in documents for d in document["dependencies"]) and not any(
            d["dependency_key"] == dependency for d in current["declared"]):
        return {**result, "partial": True, "reason": "dependency_not_observed"}
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
            result["components"].append({**c, "stale": document.get("stale", False), "distance": reached[cid], "direct": reached[cid] == 1,
                                          "relations": sorted({e["relation"] for e in evidence}), "edges": evidence})
    result["components"].sort(key=lambda c: (c["distance"], c["component_id"]))
    result["declared"] = [d for d in current["declared"] if d["dependency_key"] == dependency]
    result["partial"] = True
    result["reason"] = "bounded_captured_graph"
    result["cross_repository"] = {"status": "bounded", "complete": False}
    result["counts"] = {"components": len(result["components"]), "basis": "visible_captured_graph", "partial": True}
    return _bounded(result)
