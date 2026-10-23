"""Current-source access for resources derived from fixed PM engineering work.

Only server admission creates roots. Resource identities come from explicit
creation/relationship records, never names, filesystem paths or prompt text.
The public read gates are separate from internal original-effect reconciliation.
"""
from __future__ import annotations

import json
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps

from . import pm_read_cache
from .operations import OperationError

MAX_ROOTS = 64
_EXECUTION = ContextVar("pm_source_execution", default=None)
# Other derived writes must first implement fixed lineage and original recovery.
DERIVED_ACTIONS = frozenset({"artifact.capture.managed", "artifact.accept", "github.pr.publish", "pm.execution.verify",
    "session.send", "session.answer", "session.interrupt", "session.read", "session.position",
    "session.labels.set", "session.record_verification", "work.start", "work.adopt_and_start", "work_item.update", "work_item.pin",
    "work_item.order", "work_item.approve", "work_item.continue"})
_REF_KEYS = {"operation_id": "operation", "execution_operation_id": "operation", "source_operation_id": "operation",
    "start_operation_id": "operation", "capture_operation_id": "operation", "work_item_id": "work_item",
    "worktree_id": "worktree", "checkpoint_id": "checkpoint", "task_id": "execution",
    "session_resource_id": "session", "execution_id": "execution", "operation": "operation",
    "apply_operation_id": "operation", "preview_id": "delivery_preview", "deployment_id": "deployment",
    "retry_of": "deployment", "rollback_of": "deployment"}
_RESOURCE_KINDS = frozenset({"operation", "session", "worktree", "work_item", "execution", "checkpoint", "artifact",
    "integration", "integration_preview", "pr_merge_preview", "delivery_preview", "deployment"})


def schema(journal):
    with journal.tx():
        journal.db.execute("""CREATE TABLE IF NOT EXISTS pm_source_lineage (
            resource_type TEXT NOT NULL, resource_id TEXT NOT NULL,
            original_operation_id TEXT NOT NULL, creation_operation_id TEXT NOT NULL,
            PRIMARY KEY(resource_type,resource_id,original_operation_id))""")
        for verb in ("UPDATE", "DELETE"):
            journal.db.execute(f"CREATE TRIGGER IF NOT EXISTS pm_source_lineage_{verb.lower()}_immutable "
                f"BEFORE {verb} ON pm_source_lineage BEGIN SELECT RAISE(ABORT, 'PM source lineage is immutable'); END")
        journal.db.execute("CREATE INDEX IF NOT EXISTS pm_source_original_lookup ON pm_source_lineage "
            "(original_operation_id,resource_type,resource_id)")
        from .pm_delivery_lineage import schema as delivery_schema
        delivery_schema(journal)


@pm_read_cache.memoized(guard_independent=True)
def installed(db):
    return db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='pm_source_lineage'").fetchone() is not None


@pm_read_cache.memoized(guard_independent=True)
def _table(db, name):
    return db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None


def roots(db, kind, identity, *, _seen=None):
    if _seen is not None:
        return _roots_collect(db, kind, identity, _seen=_seen)
    return _root_read(db, kind, identity)


@pm_read_cache.memoized(guard_independent=True, freeze=frozenset, thaw=set)
def _root_read(db, kind, identity):
    return _roots_collect(db, kind, identity)


def _roots_collect(db, kind, identity, *, _seen=None):
    if not installed(db) or not isinstance(identity, str):
        return set()
    seen = set() if _seen is None else _seen
    key = kind, identity
    if key in seen:
        return set()
    if len(seen) >= 256:
        raise OperationError("PM_SOURCE_LINEAGE_LIMIT", "Source lineage exceeds the bounded read contract", 409)
    seen.add(key)
    found = {row[0] for row in db.execute("SELECT original_operation_id FROM pm_source_lineage WHERE resource_type=? AND resource_id=?",
                                         (kind, identity))}
    refs = []
    if kind == "operation":
        row = db.execute("SELECT action,target,params FROM operations WHERE operation_id=?", (identity,)).fetchone()
        if row:
            action, target, params = row[0], json.loads(row[1]), json.loads(row[2])
            if action == "integration.preview":
                refs.append(("integration_preview", "ipv_" + identity[3:]))
            elif action == "integration.handoff":
                refs.append(("operation", target.get("operation_id")))
            elif action in {"integration.apply", "github.pr.merge", "delivery.merge_and_deploy"}:
                refs.append(("delivery_preview", params.get("preview_id")))
    elif kind == "integration":
        refs.append(("operation", identity))
    elif kind in {"delivery_preview", "integration_preview"} and identity.startswith("ipv_"):
        refs.append(("operation", "op_" + identity[4:]))
        if _table(db, "integration_previews"):
            row = db.execute("SELECT document FROM integration_previews WHERE preview_id=?", (identity,)).fetchone()
            if row:
                from .pm_delivery_lineage import references as delivery_references
                refs.extend(delivery_references(json.loads(row[0])))
    elif kind in {"delivery_preview", "pr_merge_preview"} and _table(db, "pr_merge_previews"):
        row = db.execute("SELECT document FROM pr_merge_previews WHERE preview_id=?", (identity,)).fetchone()
        if row:
            from .pm_delivery_lineage import references as delivery_references
            refs.extend(delivery_references(json.loads(row[0])))
    elif kind == "deployment" and _table(db, "deployments"):
        row = db.execute("SELECT operation_id,recipe_snapshot,identity FROM deployments WHERE deployment_id=?", (identity,)).fetchone()
        if row:
            refs.append(("operation", row[0]))
            from .pm_delivery_lineage import references as delivery_references
            refs.extend(delivery_references({**json.loads(row[1]), **json.loads(row[2])}))
    if kind == "artifact" and _table(db, "artifact_revisions"):
        # The whole artifact retains all historical source roots, including an
        # upload appended to a previously captured artifact identity.
        refs += [("operation", r[0]) for r in db.execute("SELECT operation_id FROM artifact_revisions WHERE artifact_id=?", (identity,))]
    elif kind == "worktree" and _table(db, "session_worktree_bindings"):
        refs += [("session", r[0]) for r in db.execute("SELECT session_resource_id FROM session_worktree_bindings WHERE worktree_id=?", (identity,))]
    elif kind == "checkpoint" and _table(db, "checkpoints"):
        row = db.execute("SELECT host,source_session_id FROM checkpoints WHERE checkpoint_id=?", (identity,)).fetchone()
        if row:
            refs.append(("session", row[0] + "/" + row[1]))
    elif kind == "execution" and _table(db, "tasks"):
        row = db.execute("SELECT host,session_id FROM tasks WHERE task_id=?", (identity,)).fetchone()
        if row and row[1]:
            refs.append(("session", row[0] + "/" + row[1]))
    for child_kind, child_id in refs:
        found.update(_roots_collect(db, child_kind, child_id, _seen=seen))
    if len(found) > MAX_ROOTS:
        raise OperationError("PM_SOURCE_LINEAGE_LIMIT", "Too many original sources for one resource", 409)
    return found


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


@pm_read_cache.memoized(guard_independent=True)
def _original_action(db, operation_id):
    row = db.execute('SELECT action FROM operations WHERE operation_id=?', (operation_id,)).fetchone()
    return row[0] if row else None


def visible_roots(db, principal, original_ids):
    # Public metadata has no original sources and needs no authority scope.
    # A missing principal must still never authorize a referenced resource.
    if not original_ids:
        return True
    if principal is None:
        return False
    from .pm_maintenance_repairs import source_backedge
    from .pm_source_reads import check, scope
    with scope(db, principal):
        for identity in original_ids:
            # Only the exact repair traversal already proving the entire graph
            # may visit its saved receipt -> owned child -> same repair edge.
            # Never memoize that partial grant. Completed outer authorizations
            # retain the shared memo and bounds like every other original root.
            if _original_action(db, identity) == 'pm.maintenance.repair' and source_backedge(db, principal, identity):
                visible = _visible_roots(db, principal, (identity,))
            else:
                visible = check(db, principal, 'original_root', identity,
                    lambda identity=identity: _visible_roots(db, principal, (identity,)))
            if not visible:
                return False
        return True


def _visible_roots(db, principal, original_ids):
    from .pm_package_handoff import source_visible
    for original_id in original_ids:
        operation = pm_read_cache.operation(db, original_id)
        if operation is None:
            return False
        if operation['action'] == 'pm.input.record' and (operation.get('external_refs') or {}).get('admission_binding', {}).get('pm_review_candidate'):
            from .pm_review_candidate_sources import operation_visible as candidate_visible
            if not candidate_visible(db, principal, operation):
                return False
        elif operation['action'] == 'pm.workspace.adapter.select':
            from .pm_workspace_adapter import operation_visible as adapter_visible
            if not adapter_visible(db, principal, operation):
                return False
        elif operation["action"] in {"pm.conversation.propose", "pm.conversation.adopt"}:
            from .pm_conversation_proposals import operation_visible as conversation_visible
            if not conversation_visible(db, principal, operation):
                return False
        elif operation['action'] == 'pm.overview.assess':
            from .pm_overview_assessments import operation_visible as overview_visible
            if not overview_visible(db, principal, operation):
                return False
        elif operation['action'] == 'pm.continuation.prepare':
            from .pm_continuation import operation_visible as continuation_visible
            if not continuation_visible(db, principal, operation):
                return False
        elif operation['action'] == 'pm.work_input.defer':
            from .pm_work_input_idea import operation_visible as deferred_visible
            if not deferred_visible(db, principal, operation):
                return False
        elif operation['action'] == 'pm.follow_up.analyze':
            from .pm_follow_up_analysis import operation_visible as follow_up_visible
            if not follow_up_visible(db, principal, operation):
                return False
        elif operation["action"] == "pm.spec.materialize":
            from .pm_spec_materialization import source_visible as spec_visible
            if not spec_visible(db, principal, operation):
                return False
        elif operation["action"] == "pm.maintenance.repair":
            from .pm_maintenance_repairs import source_visible as repair_visible
            if not repair_visible(db, principal, operation):
                return False
        elif operation["action"] == "pm.run.bat.analyze":
            from .pm_context_bundles import operation_visible as bundle_visible
            from .pm_workspace import require_member
            try:
                require_member(db, principal, operation["target"]["workspace_id"])
            except OperationError:
                return False
            if not bundle_visible(db, principal, operation):
                return False
        elif operation["action"] == "work.adopt_and_start":
            from .pm_work_adopt_and_start import source_visible as adopt_start_visible
            if not adopt_start_visible(db, principal, operation):
                return False
        elif operation["action"] == "work.start":
            from .pm_work_start import source_visible as work_start_visible
            if not work_start_visible(db, principal, operation):
                return False
        elif not source_visible(db, principal, operation):
            return False
    return True


@pm_read_cache.memoized
def visible(db, principal, kind, identity):
    return visible_roots(db, principal, roots(db, kind, identity))


def require(db, principal, kind, identity):
    if not visible(db, principal, kind, identity):
        raise OperationError("NOT_FOUND", "Original resource sources are unavailable", 404)


def require_document(db, principal, value):
    if not visible_roots(db, principal, document_roots(db, value)):
        raise OperationError("NOT_FOUND", "Original resource sources are unavailable", 404)


def require_session(db, principal, host, sid):
    if not installed(db) or not isinstance(host, str) or not isinstance(sid, str):
        return
    # Legacy observation accepts prefixes. A prefix may not bypass a protected
    # exact identity; ambiguous resolution still belongs to the existing reader.
    for row in db.execute("SELECT DISTINCT resource_id FROM pm_source_lineage WHERE resource_type='session'"):
        h, _, exact = row[0].partition("/")
        if h == host and exact.startswith(sid):
            require(db, principal, "session", row[0])


def inherit(ops, principal, action, target, params, binding):
    if not installed(ops.db):
        return binding
    original_ids = document_roots(ops.db, {"target": target, "params": params, "binding": binding})
    from . import pm_delivery_lineage
    if action in pm_delivery_lineage.ACTIONS or action == "github.pr.update":
        original_ids.update(pm_delivery_lineage.action_roots(ops, action, target, params))
    if len(original_ids) > MAX_ROOTS:
        raise OperationError("PM_SOURCE_LINEAGE_LIMIT", "Too many original sources for one delivery", 409)
    if not original_ids:
        return binding
    if not visible_roots(ops.db, principal, original_ids):
        raise OperationError("NOT_FOUND", "Original resource sources are unavailable", 404)
    if action not in DERIVED_ACTIONS | pm_delivery_lineage.ACTIONS and not action.startswith("pm.") and not (action == "repository.continue" and (binding or {}).get("pm_handoff")):
        raise OperationError("PM_DERIVED_ACTION_UNAVAILABLE", "This action has no fixed PM source handoff contract yet", 409)
    workspaces = set()
    for root in original_ids:
        original = ops._row(root)
        workspaces.add(original["external_refs"]["admission_binding"]["pm_lineage"]["workspace_id"])
    if len(workspaces) != 1:
        raise OperationError("PM_SOURCE_WORKSPACE_MISMATCH", "One execution may not mix independently managed PM workspaces", 409)
    fixed_workspace = ((binding or {}).get("pm_lineage") or {}).get("workspace_id")
    if fixed_workspace is not None and fixed_workspace not in workspaces:
        raise OperationError("PM_SOURCE_WORKSPACE_MISMATCH", "Original source and requested PM workspace differ", 409)
    if action.startswith("pm.") and target.get("workspace_id") not in workspaces:
        raise OperationError("PM_SOURCE_WORKSPACE_MISMATCH", "Original source and requested PM workspace differ", 409)
    delivery = pm_delivery_lineage.admission(ops, principal, action, target, params, original_ids)
    return {**(binding or {}), **({"pm_delivery": delivery} if delivery else {}), "pm_source_roots": sorted(original_ids),
            "pm_lineage": {"workspace_id": next(iter(workspaces))}}


def bind_operation(db, operation_id, target, params, binding):
    """Inside operation INSERT transaction, before any start or event publication."""
    if not installed(db):
        return
    original_ids = binding.get("pm_source_roots", [])
    resources = [("operation", operation_id)]
    if binding.get("pm_bat_binding") is not None:
        from .session_start_operations import NAMESPACE
        original_ids = [operation_id, *original_ids]
        resources.append(("session", binding["pm_bat_binding"]["target"]["host"] + "/" + str(uuid.uuid5(NAMESPACE, operation_id))))
    if (binding.get("pm_spec_materialization") is not None or binding.get("pm_maintenance_repair") is not None
            or binding.get('pm_review_candidate') is not None or binding.get('pm_work_input_idea') is not None
            or binding.get('overview_assessment') is not None or binding.get('pm_adapter_selection') is not None):
        original_ids = [operation_id, *original_ids]
    if binding.get("pm_handoff") is not None:
        from .checkpoints import _SESSION_NS
        original_ids = [operation_id, *original_ids]
        resources.append(("session", target["host"] + "/" + str(uuid.uuid5(_SESSION_NS, operation_id))))
        resources.append(("work_item", params["work_item_id"]))
    if binding.get("pm_delivery") and "host" in binding["pm_delivery"] and "operation_id" in target:
        # integration.handoff uses the existing deterministic creation identity.
        from .checkpoints import _SESSION_NS
        resources.append(("session", binding["pm_delivery"]["host"] + "/" + str(uuid.uuid5(_SESSION_NS, operation_id))))
    original_ids = sorted(set(original_ids))
    if len(original_ids) > MAX_ROOTS:
        raise OperationError("PM_SOURCE_LINEAGE_LIMIT", "Too many original sources including this new operation", 409)
    for kind, identity in resources:
        for original in original_ids:
            db.execute("INSERT OR IGNORE INTO pm_source_lineage VALUES(?,?,?,?)", (kind, identity, original, operation_id))
    if binding.get("pm_delivery"):
        from .pm_delivery_lineage import bind
        bind(db, operation_id, binding["pm_delivery"])


def _source_guard(fn):
    @wraps(fn)
    def guard(ops, *args):
        # A synchronous validation only: end the snapshot and memo before the
        # caller writes or awaits. Every live/effect guard still runs each time.
        from .pm_release_baselines import _snapshot
        with _snapshot(ops.db), pm_read_cache.scope(ops.db):
            return fn(ops, *args)
    return guard


@_source_guard
def require_new_effect(ops, operation_id):
    from .pm_conversation_adoption import require_child as require_conversation_child
    require_conversation_child(ops.db, operation_id)
    from .pm_delivery_lineage import require_admitted_sources
    op = ops._row(operation_id)
    from .pm_work_batches import require_child
    require_child(ops, op)
    from .pm_work_start import require_child as require_work_start_child
    require_work_start_child(ops, op)
    from .pm_work_adopt_and_start import require_child as require_adopt_start_child
    require_adopt_start_child(ops, op)
    from .pm_maintenance_repairs import require_new_effect as require_repair_effect
    require_repair_effect(ops, op)
    require_admitted_sources(ops, op)
    originals = roots(ops.db, "operation", operation_id)
    if not originals:
        return
    from .api_auth import Principal
    authority = op["external_refs"]["admission_binding"]["controller_authority"]
    principal = Principal(authority["actor"], frozenset(authority["scopes"]), admin=authority["admin"])
    if not visible_roots(ops.db, principal, originals):
        raise OperationError("PM_HANDOFF_SOURCE_UNAVAILABLE", "Original sources were withdrawn before a new effect", 404)
    from .pm_delivery_lineage import require_current
    require_current(ops, op)


@contextmanager
def execution(ops, operation_id):
    """Identify the existing worker, never grant a frame or admit a new step."""
    token = _EXECUTION.set((ops, operation_id))
    try:
        yield
    finally:
        _EXECUTION.reset(token)


def require_frame():
    current = _EXECUTION.get()
    if current is None:
        return
    _require_frame(*current)


@_source_guard
def _require_frame(ops, operation_id):
    from .pm_conversation_adoption import require_child as require_conversation_child
    require_conversation_child(ops.db, operation_id)
    from .pm_work_batches import require_child
    require_child(ops, ops._row(operation_id))
    from .pm_work_start import require_child as require_work_start_child
    require_work_start_child(ops, ops._row(operation_id))
    from .pm_work_adopt_and_start import require_child as require_adopt_start_child
    require_adopt_start_child(ops, operation_id)
    from .pm_maintenance_repairs import require_child as require_repair_child
    require_repair_child(ops, ops._row(operation_id))
    from .pm_delivery_lineage import require_admitted_sources
    require_admitted_sources(ops, ops._row(operation_id))
    if not roots(ops.db, "operation", operation_id):
        return
    require_new_effect(ops, operation_id)
    # Lifecycle epoch belongs to the original ctx.step admission; an already
    # admitted bounded step may finish after a stop. Do not redefine that here.
    from .operations import OpContext
    from .pm_package_handoff import guard
    context = OpContext(ops, ops._row(operation_id))
    context.check_cancel()
    guard(context)
    # A new explicit package assignment has its own current contract. Ancestor
    # contracts remain privacy roots, not competing execution specifications.
    if (context.admission_binding or {}).get("pm_handoff"):
        return
    originals = roots(ops.db, "operation", operation_id)
    inherited = set()
    for original_id in originals:
        saved = ops._row(original_id)["external_refs"]["admission_binding"]
        inherited.update(saved.get("pm_source_roots", []))
    # Follow-up controls use the active original assignment(s), while checking
    # every ancestor's current source visibility above.
    for original_id in originals - inherited:
        guard(OpContext(ops, ops._row(original_id)))


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


def read_request(db, principal, selection):
    require_document(db, principal, selection)
    if isinstance(selection, dict):
        require_session(db, principal, selection.get("host"), selection.get("session_id"))


def http_selection(name, kwargs, query, body):
    """Normalize existing route variable names, not user-provided resource kinds."""
    selection = {key: values[0] for key, values in query.items() if len(values) == 1}
    selection.update(body if isinstance(body, dict) else {})
    selection.update(kwargs)
    if "sid" in kwargs:
        selection["session_id"] = kwargs["sid"]
    if "aid" in kwargs:
        selection["artifact_id"] = kwargs["aid"]
    if "cp" in kwargs:
        selection["checkpoint_id"] = kwargs["cp"]
    if "op" in kwargs:
        selection["operation_id"] = kwargs["op"]
    if "pv" in kwargs and name == "integration_preview":
        selection["preview_id"] = kwargs["pv"]
    if "dep" in kwargs:
        selection["deployment_id"] = kwargs["dep"]
    if "task" in kwargs:
        selection["task_id"] = kwargs["task"]
    if "wi" in kwargs:
        selection["work_item_id"] = kwargs["wi"]
    if "wid" in kwargs and name in {"worktree", "worktree_history", "worktree_relations"}:
        selection["worktree_id"] = kwargs["wid"]
    if "wid" in kwargs and name in {"work_item_results", "work_item_repair"}:
        selection["work_item_id"] = kwargs["wid"]
    return selection
