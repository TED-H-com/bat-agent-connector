"""Frozen barrier from 447da212; do not regenerate when the implementation changes.

Function and constant source segments are exact copies. Only unchanged database
root/visibility primitives are imported from production. The original package
name preserves the frozen references() relative import without editing it.
"""
from bat_agent_connector import pm_source_lineage as lineage
from bat_agent_connector.operations import OperationError

SOURCE = r'''
MAX_ROOTS = 64


_REF_KEYS = {"operation_id": "operation", "execution_operation_id": "operation", "source_operation_id": "operation",
    "start_operation_id": "operation", "capture_operation_id": "operation", "work_item_id": "work_item",
    "worktree_id": "worktree", "checkpoint_id": "checkpoint", "task_id": "execution",
    "session_resource_id": "session", "execution_id": "execution", "operation": "operation",
    "apply_operation_id": "operation", "preview_id": "delivery_preview", "deployment_id": "deployment",
    "retry_of": "deployment", "rollback_of": "deployment"}


_RESOURCE_KINDS = frozenset({"operation", "session", "worktree", "work_item", "execution", "checkpoint", "artifact",
    "integration", "integration_preview", "pr_merge_preview", "delivery_preview", "deployment"})


def references(value, *, inherited_host=None, depth=0):
    """Named central read-model identifiers only; do not parse prose or paths."""
    if depth > 32:
        raise OperationError("PM_SOURCE_LINEAGE_LIMIT", "Source references exceed the bounded document depth", 409)
    refs = set()
    if isinstance(value, list):
        for item in value:
            refs.update(references(item, inherited_host=inherited_host, depth=depth + 1))
    elif isinstance(value, dict):
        host = value.get("host", inherited_host)
        for key, kind in _REF_KEYS.items():
            if isinstance(value.get(key), str):
                refs.add((kind, value[key]))
        for key, kind in (("work_item_ids", "work_item"), ("operation_ids", "operation"), ("session_ids", "session")):
            for identity in value.get(key, []) if isinstance(value.get(key), list) else []:
                if isinstance(identity, str):
                    refs.add((kind, host + "/" + identity if kind == "session" and isinstance(host, str) else identity))
        for identity in value.get("original_ids", []) if isinstance(value.get("original_ids"), list) else []:
            if isinstance(identity, str) and identity.startswith("op_"):
                refs.add(("operation", identity))
        if isinstance(value.get("artifact_id"), str):
            refs.add(("artifact", value["artifact_id"]))
        if isinstance(host, str) and isinstance(value.get("session_id"), str):
            refs.add(("session", host + "/" + value["session_id"]))
        kind, identity = value.get("resource_type"), value.get("resource_id")
        if kind in _RESOURCE_KINDS and isinstance(identity, str):
            refs.add((kind, host + "/" + identity if kind == "session" and "/" not in identity and isinstance(host, str) else identity))
        # Work links, integration selections and cleanup items use these exact
        # tagged references rather than the ordinary named identity fields.
        kind, identity = value.get("kind"), value.get("ref", value.get("id"))
        if kind in _RESOURCE_KINDS and isinstance(identity, str):
            refs.add((kind, identity))
        if kind == "execution" and isinstance(identity, str) and identity.startswith("op_"):
            refs.add(("operation", identity))
        if kind == "checkpoint_run" and isinstance(identity, str):
            refs.add(("operation", identity))
        for kind_field, id_field in (("source_kind", "source_id"), ("owner_kind", "owner_id")):
            kind, identity = value.get(kind_field), value.get(id_field)
            if kind in _RESOURCE_KINDS and isinstance(identity, str):
                refs.add(("operation" if kind == "execution" and identity.startswith("op_") else kind, identity))
        from .pm_delivery_lineage import references as delivery_references
        refs.update(delivery_references(value))
        for child in value.values():
            if isinstance(child, (dict, list)):
                refs.update(references(child, inherited_host=host, depth=depth + 1))
    return refs


def document_roots(db, value):
    if not installed(db) or db.execute("SELECT 1 FROM pm_source_lineage LIMIT 1").fetchone() is None:
        return set()
    result = set()
    for kind, identity in references(value):
        result.update(roots(db, kind, identity))
    if len(result) > MAX_ROOTS:
        raise OperationError("PM_SOURCE_LINEAGE_LIMIT", "Too many original sources for one document", 409)
    return result


def require_document(db, principal, value):
    if not visible_roots(db, principal, document_roots(db, value)):
        raise OperationError("NOT_FOUND", "Original resource sources are unavailable", 404)


def filter_page(db, principal, page, keys):
    """Filter whole records, preserving the upstream scan cursor and source truth."""
    result = dict(page)
    changed = False
    for key in keys:
        if isinstance(result.get(key), list):
            rows = result[key]
            result[key] = [row for row in rows if visible_roots(db, principal, document_roots(db, row))]
            changed |= len(rows) != len(result[key])
    if changed:
        result["source_visibility_filtered"] = True
        if "count" in result:
            result["count"] = sum(len(result[key]) for key in keys if isinstance(result.get(key), list))
    return result


_COLLECTIONS = frozenset({"sessions", "operations", "artifacts", "work_items", "work", "children",
    "links", "result_artifacts", "checkpoints", "candidates", "integrations", "events", "relations",
    "retained", "tombstones", "known_sessions", "instructions", "archived"})


def read_response(db, principal, value):
    """Final public read barrier for legacy projections sharing the central API.

Only established collection fields may drop whole rows. Singular records and
unknown response shapes are refused as a whole if they contain hidden sources;
fixed PM documents are not rewritten into misleading partial descriptions.
"""
    if not installed(db) or not isinstance(value, dict):
        require_document(db, principal, value)
        return value
    result = filter_page(db, principal, value, _COLLECTIONS)
    if ("next_cursor" in result and isinstance(result.get("items"), list)
            and all(isinstance(item, dict) and "deployment_id" in item for item in result["items"])):
        result = filter_page(db, principal, result, {"items"})
    if (isinstance(result.get("snapshot"), dict) and isinstance(result.get("sync"), dict)
            and isinstance(result.get("capabilities"), dict) and "pagination" in result):
        result["snapshot"] = {key: read_response(db, principal, part) if isinstance(part, dict) else part
                              for key, part in result["snapshot"].items()}
    require_document(db, principal, result)
    return result
'''


def namespace():
    scope = {'__package__': 'bat_agent_connector', 'OperationError': OperationError,
             'installed': lineage.installed, 'roots': lineage.roots, 'visible_roots': lineage.visible_roots}
    exec(compile(SOURCE, '<447da212 read barrier>', 'exec'), scope)
    return scope
