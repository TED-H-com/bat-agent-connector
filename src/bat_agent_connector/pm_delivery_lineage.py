"""Original PM sources through the existing fixed delivery engines.

Only server-selected identities and original receipts create these associations.
They describe provenance/privacy; success remains the delivery engine's result.
"""
from __future__ import annotations

import hashlib
import json
import re
import time

from .operations import OperationError

CONTRACT = "pm-engineering-delivery/v1"
ACTIONS = frozenset({"integration.preview", "integration.apply", "integration.handoff",
    "github.pr.merge", "delivery.merge_and_deploy", "deployment.start", "deployment.rollback"})


def identity(provider, repository_id, value):
    if (not isinstance(provider, str) or not provider or type(repository_id) is not int
            or repository_id <= 0 or value is None):
        return None
    return hashlib.sha256(json.dumps([provider, repository_id, value], separators=(",", ":")).encode()).hexdigest()


def schema(journal):
    journal.db.execute("""CREATE TABLE IF NOT EXISTS pm_delivery_links (
        operation_id TEXT NOT NULL, resource_type TEXT NOT NULL, resource_id TEXT NOT NULL,
        provider_origin TEXT NOT NULL, repository_id INTEGER NOT NULL, repository TEXT NOT NULL,
        pull_number INTEGER, source_sha TEXT, relation TEXT NOT NULL, created_at REAL NOT NULL,
        PRIMARY KEY(operation_id,resource_type,resource_id,relation))""")
    journal.db.execute("CREATE INDEX IF NOT EXISTS pm_delivery_pr_lookup ON pm_delivery_links "
        "(provider_origin,repository COLLATE NOCASE,pull_number,resource_type)")
    for verb in ("UPDATE", "DELETE"):
        journal.db.execute(f"CREATE TRIGGER IF NOT EXISTS pm_delivery_links_{verb.lower()}_immutable "
            f"BEFORE {verb} ON pm_delivery_links BEGIN SELECT RAISE(ABORT, 'PM delivery lineage is immutable'); END")


def references(document):
    """Exact provider identities only; names and SHAs without a provider do not qualify."""
    provider = document.get("provider_origin", document.get("provider"))
    target = document.get("target") if isinstance(document.get("target"), dict) else document
    rid = target.get("repository_id", document.get("repository_id"))
    refs = set()
    number = target.get("number", document.get("pull_number"))
    if type(number) is int and number > 0 and (key := identity(provider, rid, number)):
        refs.add(("github_pr", key))
    for field in ("source_sha", "merged_sha", "new_head", "head_sha"):
        sha = target.get(field, document.get(field))
        if isinstance(sha, str) and re.fullmatch(r"[0-9a-f]{40}", sha) and (key := identity(provider, rid, sha)):
            refs.add(("git_revision", key))
    return refs


def known_pr_roots(ops, target):
    from .pm_source_lineage import _table, roots
    result = set()
    if not _table(ops.db, "pm_delivery_links"):
        return result
    cfg = ops.context.get("github_config")
    if cfg is None:
        return result
    for row in ops.db.execute("SELECT DISTINCT resource_id FROM pm_delivery_links WHERE resource_type='github_pr' "
            "AND provider_origin=? AND repository=? COLLATE NOCASE AND pull_number=?",
            (cfg.api_url, target.get("repository"), target.get("pull_number"))):
        result.update(roots(ops.db, "github_pr", row[0]))
    return result


def action_roots(ops, action, target, params):
    from .pm_source_lineage import roots
    if action == "integration.handoff":
        original = ops._row(target["operation_id"])
        return roots(ops.db, "delivery_preview", original["params"]["preview_id"])
    if action.startswith("integration.") or action == "github.pr.update":
        return known_pr_roots(ops, target)
    if action == "deployment.start":
        from .deployment import bound_environment, recipe
        env = bound_environment(ops, recipe(ops, target["recipe"]))
        key = identity(env["provider_origin"], env["repository_id"], params.get("source_sha"))
        return roots(ops.db, "git_revision", key) if key else set()
    return set()


def admission(ops, principal, action, target, params, originals):
    """Freeze delivery target against original assignment, never caller lineage."""
    if action not in ACTIONS or not originals:
        return None
    from . import deployment, integration, pr_delivery
    from .pm_package_handoff import binding
    from .pm_workspace import require_member
    provider = ops.context["github_config"].api_url
    if action in {"integration.apply", "integration.handoff"}:
        if action == "integration.handoff":
            apply = ops._row(target["operation_id"])
            preview_id = apply["params"]["preview_id"]
        else:
            preview_id = params["preview_id"]
        preview = integration.get_preview(ops.db, preview_id)
        document = json.loads(preview["document"])
        selected = {"provider_origin": document.get("provider_origin"), "repository": preview["repository"],
            "repository_id": document["repository_id"], "pull_number": preview["pull_number"], "host": preview["host"]}
    elif action in {"github.pr.merge", "delivery.merge_and_deploy"}:
        preview = pr_delivery.get_preview(ops.db, params["preview_id"])
        selected = {"provider_origin": preview["provider"], "repository": preview["repository"],
            "repository_id": preview["target"]["repository_id"], "pull_number": preview["target"]["number"]}
    elif action.startswith("deployment."):
        r = deployment.recipe(ops, target["recipe"])
        env = deployment.bound_environment(ops, r)
        selected = {"provider_origin": env["provider_origin"], "repository": r.repository,
            "repository_id": env["repository_id"]}
    else:
        selected = {"provider_origin": provider, "repository": target["repository"],
            "pull_number": target["pull_number"], "host": target["host"]}
    assignment = False
    for original_id in originals:
        original = ops._row(original_id)
        saved = binding(original)
        if not saved:
            # Owned spec artifacts can be immutable ancestors of the assigned
            # engineering package. They remain privacy roots, not assignments.
            continue
        assignment = True
        contract = saved["contract"]
        require_member(ops.db, principal, contract["package_ref"]["workspace_id"], write=True)
        package = contract["package"]
        code = package["code_source"]
        # source.resolve is the original fixed repository/host config receipt.
        row = ops.db.execute("SELECT response FROM operation_steps WHERE operation_id=? "
            "AND name='source.resolve' AND status='succeeded'", (original_id,)).fetchone()
        plan = json.loads(row[0]) if row else {}
        if (selected["repository"].lower() != code["repository"].lower()
                or selected.get("repository_id", code["repository_id"]) != code["repository_id"]
                or selected["provider_origin"] != provider
                or (plan.get("config") or {}).get("provider_origin") != provider):
            raise OperationError("PM_DELIVERY_TARGET_CHANGED", "Delivery must retain the original provider and repository identity", 409)
        selected["repository_id"] = code["repository_id"]
    if not assignment or not selected.get("repository_id"):
        raise OperationError("PM_DELIVERY_SOURCE_UNPROVEN", "Delivery requires an original fixed engineering assignment", 409)
    from . import pm_maintenance_repair_evidence
    return pm_maintenance_repair_evidence.delivery_binding(ops, principal, action, target, params,
        {"contract": CONTRACT, **selected}, originals)


def bind(db, operation_id, selected, *, revision=None, relation="selected_target"):
    """Within the caller's journal transaction, including sent-effect bookkeeping."""
    from .pm_source_lineage import installed, roots
    if not installed(db):
        return
    originals = roots(db, "operation", operation_id)
    if not originals:
        return
    provider, rid, repo = selected["provider_origin"], selected["repository_id"], selected["repository"]
    values = []
    if type(selected.get("pull_number")) is int:
        values.append(("github_pr", selected["pull_number"]))
    if revision is not None:
        if not isinstance(revision, str) or not re.fullmatch(r"[0-9a-f]{40}", revision):
            raise OperationError("PM_DELIVERY_IDENTITY_INVALID", "Delivery omitted its exact source revision", 409)
        values.append(("git_revision", revision))
    for kind, value in values:
        key = identity(provider, rid, value)
        if key is None:
            raise OperationError("PM_DELIVERY_IDENTITY_INVALID", "Delivery omitted its original provider identity", 409)
        db.execute("INSERT OR IGNORE INTO pm_delivery_links VALUES(?,?,?,?,?,?,?,?,?,?)",
            (operation_id, kind, key, provider, rid, repo, selected.get("pull_number"), revision, relation, time.time()))
        for original_id in originals:
            db.execute("INSERT OR IGNORE INTO pm_source_lineage VALUES(?,?,?,?)",
                (kind, key, original_id, operation_id))


def record(ctx, revision, relation):
    selected = (ctx.admission_binding or {}).get("pm_delivery")
    if selected:
        with ctx.service.journal.tx():
            bind(ctx.service.db, ctx.operation_id, selected, revision=revision, relation=relation)


def admitted_roots(db, operation_id):
    """Only INSERT-time original authority, never later native-target associations."""
    from .pm_source_lineage import installed
    if not installed(db):
        return set()
    return {row[0] for row in db.execute("SELECT original_operation_id FROM pm_source_lineage "
        "WHERE resource_type='operation' AND resource_id=?", (operation_id,))}


def require_target(ctx, *, provider, repository_id, pull_number=None):
    from .pm_source_lineage import roots
    selected = (ctx.admission_binding or {}).get("pm_delivery")
    if selected and (selected["provider_origin"] != provider or selected["repository_id"] != repository_id):
        raise OperationError("PM_DELIVERY_TARGET_CHANGED", "The provider or numeric repository changed", 409)
    number = pull_number if pull_number is not None else ctx.target.get("pull_number", (selected or {}).get("pull_number"))
    key = identity(provider, repository_id, number)
    known = roots(ctx.service.db, "github_pr", key) if key else set()
    if not known <= admitted_roots(ctx.service.db, ctx.operation_id):
        raise OperationError("PM_DELIVERY_SOURCE_CHANGED", "The fixed delivery target has additional PM sources; submit a newly reviewed intent", 409)


def observed_roots(ops, operation):
    """Read only original native target receipts; never infer from a branch/path."""
    from .pm_source_lineage import _table, roots
    action = operation["action"]
    if action not in ACTIONS:
        return set()
    refs = operation.get("external_refs") or {}
    document = (refs.get("admission_binding") or {}).get("pm_delivery")
    preview_id = operation["params"].get("preview_id")
    if action == "integration.handoff":
        preview_id = ops._row(operation["target"]["operation_id"])["params"]["preview_id"]
    if preview_id:
        table = "integration_previews" if preview_id.startswith("ipv_") else "pr_merge_previews"
        row = ops.db.execute(f"SELECT document FROM {table} WHERE preview_id=?", (preview_id,)).fetchone() if _table(ops.db, table) else None  # noqa: S608
        if row:
            document = json.loads(row[0])
    if action.startswith("deployment.") or action == "delivery.merge_and_deploy":
        from .deployment_store import deployment
        dep = deployment(ops.db, operation_id=operation["operation_id"])
        if dep is None and action == "deployment.rollback":
            dep = deployment(ops.db, deployment_id=operation["params"].get("deployment_id"))
        if dep:
            snapshot = dep["recipe_snapshot"]
            document = {"provider_origin": snapshot.get("provider_origin"), "repository_id": snapshot.get("repository_id"),
                "source_sha": dep["identity"].get("source_sha"), "merge": document or {}}
        elif action == "deployment.start":
            from .deployment import bound_environment, recipe
            env = bound_environment(ops, recipe(ops, operation["target"]["recipe"]))
            document = {"provider_origin": env["provider_origin"], "repository_id": env["repository_id"],
                "source_sha": operation["params"].get("source_sha")}
    found = set()
    if document:
        for kind, key in references(document):
            found.update(roots(ops.db, kind, key))
        if isinstance(document.get("merge"), dict):
            for kind, key in references(document["merge"]):
                found.update(roots(ops.db, kind, key))
    return found


def require_admitted_sources(ops, operation):
    if not observed_roots(ops, operation) <= admitted_roots(ops.db, operation["operation_id"]):
        raise OperationError("PM_DELIVERY_SOURCE_CHANGED", "PM sources changed after delivery admission; submit a newly reviewed intent", 409)


def require_current(ops, operation):
    binding = operation["external_refs"]["admission_binding"]
    if not binding.get("pm_delivery"):
        return
    from .api_auth import Principal
    from .pm_workspace import require_member
    caller = binding["controller_authority"]
    require_member(ops.db, Principal(caller["actor"], frozenset(caller["scopes"]), admin=caller["admin"]),
        binding["pm_lineage"]["workspace_id"], write=True)
    if ops.context["github_config"].api_url != binding["pm_delivery"]["provider_origin"]:
        raise OperationError("PM_DELIVERY_TARGET_CHANGED", "Restore the original provider before any new delivery effect", 409)


def preserve_preview(ops, document):
    """Retain preview privacy even when the ordinary scope cache later expires."""
    from .pm_source_lineage import installed, roots
    if not installed(ops.db):
        return
    originals = set()
    for kind, key in references(document):
        originals.update(roots(ops.db, kind, key))
    for original_id in originals:
        for kind in ("pr_merge_preview", "delivery_preview"):
            ops.db.execute("INSERT OR IGNORE INTO pm_source_lineage VALUES(?,?,?,?)",
                (kind, document["preview_id"], original_id, original_id))


def sent_readback(ctx, name):
    """Only fixed provider observations after this operation's recorded send."""
    if not (ctx.admission_binding or {}).get("pm_delivery") and not observed_roots(ctx.service, ctx.op):
        return False
    if re.fullmatch(r"merge\.verify(?:\.retry\.[0-9]+)?", name):
        return ctx.op["action"] in {"github.pr.merge", "delivery.merge_and_deploy"} and bool(ctx.service.db.execute(
            "SELECT 1 FROM operation_steps WHERE operation_id=? AND name='merge.submit'",
            (ctx.operation_id,)).fetchone())
    if re.fullmatch(r"deploy\.(?:locate|observe|verify|record)(?:\.retry\.[0-9]+)?", name):
        from .deployment_store import deployment
        saved = deployment(ctx.service.db, operation_id=ctx.operation_id)
        return bool(saved and (saved.get("dispatch_sent") or saved.get("run_id") or saved.get("on_merge_pending")))
    return False


async def readback_step(ctx, name, observe, *, request=None, reconcile=None):
    """Read the original provider result, then persist only its local receipt.

    The registered caller supplies an observation function, never a write. The
    receipt-only step contains no provider call and grants no next investment.
    """
    if not sent_readback(ctx, name):
        return await ctx.step(name, observe, request=request, reconcile=reconcile)
    row = ctx.service.db.execute("SELECT status FROM operation_steps WHERE operation_id=? AND name=?",
        (ctx.operation_id, name)).fetchone()
    if row and row[0] == "succeeded":
        return await ctx.step(name, observe, request=request)
    receipt = await observe()
    async def preserve():
        return receipt
    async def recovered(_):
        return receipt
    return await ctx.step(name, preserve, request=request, reconcile=recovered, receipt_only=True)


def _facts(ops, op):
    """Bounded facts from existing saved results, never a new provider observation."""
    from . import deployment, deployment_store, integration
    saved = op.get("result") or {}
    refs = op.get("external_refs") or {}
    selected = (refs.get("admission_binding") or {}).get("pm_delivery") or {}
    row = {"operation_id": op["operation_id"], "action": op["action"], "status": op["status"],
        "created_at": op["created_at"], "updated_at": op["updated_at"], "error_code": op["error_code"],
        "target": {key: selected[key] for key in ("provider_origin", "repository", "repository_id", "pull_number", "host")
            if key in selected}, "requirement_acceptance": "not_evaluated_by_delivery"}
    if op["action"].startswith("integration."):
        source_rows = integration.receipts(ops.db, op["operation_id"])
        row["integration"] = {"preview_id": op["params"].get("preview_id", saved.get("preview_id")),
            "head_sha": op["preconditions"].get("expected_head_sha", (saved.get("target") or {}).get("head_sha")),
            "new_head": saved.get("new_head"), "pushed": saved.get("pushed"),
            "sources": [{key: source.get(key) for key in ("seq", "source_kind", "source_id", "pinned_sha",
                "status", "effective_status", "delivered_sha")} for source in source_rows]}
    if op["action"] in {"github.pr.merge", "delivery.merge_and_deploy"}:
        merged = saved.get("merge", saved) if saved else refs.get("merge_receipt") or {}
        row["merge"] = {key: merged.get(key) for key in ("merged", "verified", "merged_sha", "method")}
        if merged.get("merged_sha") and "merged" not in merged:
            # verify_merge stores this receipt only after observing merged=true
            # at the expected head; verification remains its separate fact.
            row["merge"]["merged"] = True
        row["merge"].update(head_sha=op["preconditions"].get("expected_head_sha"),
            base_sha=op["preconditions"].get("expected_base_sha"), preview_id=op["params"].get("preview_id"))
    dep = deployment_store.deployment(ops.db, operation_id=op["operation_id"])
    if dep:
        status = deployment.status(ops, dep["deployment_id"])
        row["deployment"] = {key: status.get(key) for key in ("deployment_id", "source_sha", "environment",
            "recipe", "workflow", "run_id", "run_attempt", "verified", "is_current", "state", "error_code", "provider_url")}
    return row


def for_work(ops, principal, workspace_id, work_item_id, *, after=0, limit=50):
    """Read original fixed work receipts; scan cursors survive source filtering."""
    from . import pm_evidence, pm_source_lineage, pm_workspace
    from .pm_package_handoff import binding
    if not principal.allows("observe"):
        raise OperationError("FORBIDDEN", "Delivery history requires observe", 403)
    pm_workspace.require_member(ops.db, principal, workspace_id)
    if pm_evidence._binding(ops.db, workspace_id, work_item_id) is None:
        raise OperationError("NOT_FOUND", "Managed work association not found", 404)
    if type(after) is not int or not 0 <= after <= 2**63 - 1 or type(limit) is not int or not 1 <= limit <= 100:
        raise OperationError("INVALID_PARAMS", "after must be a nonnegative signed 64-bit cursor; limit must be 1..100", 422)
    originals = pm_source_lineage.roots(ops.db, "work_item", work_item_id)
    pm_source_lineage.require(ops.db, principal, "work_item", work_item_id)
    packages = []
    for original_id in sorted(originals):
        saved = binding(ops._row(original_id))
        if not saved:
            continue
        ref = saved["contract"]["package_ref"]
        if ref["workspace_id"] != workspace_id:
            raise OperationError("NOT_FOUND", "Original managed work belongs to another workspace", 404)
        packages.append({"operation_id": original_id, **ref})
    result = {"workspace_id": workspace_id, "work_item_id": work_item_id, "contract": CONTRACT,
        "original_packages": packages, "deliveries": [], "next_after": None,
        "requirement_acceptance": "not_evaluated_by_delivery", "source_visibility_filtered": False}
    if not originals:
        return result
    marks = ",".join("?" for _ in originals)
    actions = ",".join("?" for _ in ACTIONS)
    rows = ops.db.execute(f"""SELECT DISTINCT o.rowid AS delivery_seq,o.* FROM operations o
        JOIN pm_source_lineage l ON l.resource_type='operation' AND l.resource_id=o.operation_id
        WHERE l.original_operation_id IN ({marks}) AND o.action IN ({actions}) AND o.rowid>?
        ORDER BY o.rowid LIMIT ?""", (*sorted(originals), *sorted(ACTIONS), after, limit + 1)).fetchall()  # noqa: S608
    scanned = after
    for row in rows[:limit]:
        op = ops._decode(row)
        if not pm_source_lineage.visible(ops.db, principal, "operation", op["operation_id"]):
            result["source_visibility_filtered"] = True
            scanned = row["delivery_seq"]
            continue
        facts = _facts(ops, op)
        candidate = {**result, "deliveries": [*result["deliveries"], facts], "next_after": 2**63 - 1}
        if len(json.dumps(candidate, ensure_ascii=False, separators=(",", ":")).encode()) > 192_000:
            if scanned == after:
                raise OperationError("PM_DELIVERY_READ_TOO_LARGE", "A whole delivery receipt exceeds the read budget", 413)
            result["next_after"] = scanned
            return result
        result["deliveries"].append(facts)
        scanned = row["delivery_seq"]
    result["next_after"] = scanned if len(rows) > limit else None
    return result
