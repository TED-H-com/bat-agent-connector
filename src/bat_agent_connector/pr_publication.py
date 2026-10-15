"""Publish an exact owned PM handoff result and preserve original native PR creation."""
from __future__ import annotations

import ast
import copy
import hashlib
import json
import re
import time
from types import SimpleNamespace
from urllib.parse import quote

from . import (
    api_auth,
    controller_authority,
    execution_sources,
    integration,
    pm_delivery_lineage,
    pm_package_handoff,
    pm_source_lineage,
    pm_workspace,
    resource_policy,
)
from . import pr_publication_host as host
from .github import GitHubAmbiguous, GitHubReadLimit
from .operations import (
    RERUN,
    ActionDef,
    NeedsAttention,
    OpContext,
    OperationError,
    OperationService,
    _canonical,
)

ACTION = "github.pr.publish"
CONTRACT = "owned_pr_creation_v1"
REQUIRED = frozenset({"observe", "manage", "integrate"})
POST = "pr.publication.post"
CREATED = "pr.publication.created"
MAX_BYTES = 256_000


def _bad(code, message, status=409):
    return OperationError(code, message, status)


def _digest(value):
    return hashlib.sha256(_canonical(value).encode()).hexdigest()


def _object(value):
    return value if isinstance(value, dict) else {}


def _observed_sha(value):
    return value if isinstance(value, str) and integration.SHA.fullmatch(value) else None


def schema(journal):
    with journal.tx():
        journal.db.execute("""CREATE TABLE IF NOT EXISTS pr_creation_receipts (
            sequence INTEGER PRIMARY KEY AUTOINCREMENT, operation_id TEXT NOT NULL UNIQUE,
            workspace_id TEXT NOT NULL, child_operation_id TEXT NOT NULL, repair_parent_id TEXT,
            provider_origin TEXT NOT NULL, repository_id INTEGER NOT NULL, native_pr_id INTEGER NOT NULL,
            document TEXT NOT NULL, document_digest TEXT NOT NULL, created_at REAL NOT NULL,
            UNIQUE(provider_origin,repository_id,native_pr_id))""")
        journal.db.execute("CREATE INDEX IF NOT EXISTS pr_creation_child ON pr_creation_receipts(repair_parent_id,child_operation_id,sequence)")
        for verb in ("UPDATE", "DELETE"):
            journal.db.execute(f"CREATE TRIGGER IF NOT EXISTS pr_creation_{verb.lower()}_immutable BEFORE {verb} ON pr_creation_receipts "
                               "BEGIN SELECT RAISE(ABORT, 'PR creation receipt is immutable'); END")


def install(ops):
    schema(ops.journal)
    if ACTION not in ops.actions:
        ops.register(ActionDef(ACTION, "integrate", "Publish an exact owned result as a new pull request", _run, _admit,
            ("workspace_id", "repository", "host", "source_operation_id"), authorize_existing=_authorize_existing))


def capabilities():
    return {"available": True, "contract": CONTRACT, "action": ACTION, "scope": "integrate",
        "required_scopes": sorted(REQUIRED), "creation_only": True, "unknown_post_resend": False,
        "max_commits": 200, "max_files": 500, "max_response_bytes": MAX_BYTES}


def _exact(value, keys, optional=()):
    if not isinstance(value, dict) or set(keys) - set(value) or set(value) - set(keys) - set(optional):
        raise _bad("INVALID_PARAMS", "Supply exactly the registered publication fields", 422)


def _member(db, principal, wid, *, write=False):
    needed = REQUIRED if write else {"observe"}
    if any(not principal.allows(scope) for scope in needed):
        raise _bad("FORBIDDEN", "Publication requires observe, manage and integrate authority", 403)
    pm_workspace.require_member(db, principal, wid, write=write)


def _operation(db, oid):
    row = db.execute("SELECT * FROM operations WHERE operation_id=?", (oid,)).fetchone()
    if row is None:
        raise _bad("PR_SOURCE_UNAVAILABLE", "The original publication source is unavailable", 404)
    return OperationService._decode(row)


def _child_digest(child):
    return _digest({key: child.get(key) for key in
        ("operation_id", "action", "target", "params", "preconditions", "actor", "idempotency_key")})


def _quality_access(db, principal, intent):
    from .pm_source_reads import scope
    with scope(db, principal):
        _quality_sources(db, principal, intent)


def _quality_sources(db, principal, intent):
    selected = intent.get("quality_ref")
    if selected is None:
        return
    if not pm_source_lineage._table(db, "pm_quality_reviews"):
        raise _bad("PR_QUALITY_UNAVAILABLE", "The original quality assessment is unavailable", 404)
    from . import pm_quality_reviews as quality
    value = quality.get_assessment(quality._Reader(db), principal, intent["workspace_id"], **selected)
    context = value["context"]
    if (context["execution_operation_id"] != intent["child_operation_id"]
            or context["handoff_contract_digest"] != intent["handoff_contract_digest"]
            or context["native_result"]["candidate_commit"] != intent["candidate_sha"]
            or context["native_result"]["repository_id"] != intent["repository_id"]
            or context["native_result"]["provider_origin"] != intent["provider_origin"]):
        raise _bad("PR_QUALITY_SOURCE_CHANGED", "The selected quality assessment belongs to another original result")


def _repair_parent(ops, child, *, write=False):
    parent_id = (child.get("external_refs") or {}).get("pm_maintenance_repair_parent")
    installed = pm_source_lineage._table(ops.db, "pm_maintenance_repair_children")
    row = ops.db.execute("SELECT parent_id FROM pm_maintenance_repair_children WHERE operation_id=?",
                         (child["operation_id"],)).fetchone() if installed else None
    if parent_id is None and row is None:
        return None
    if row is None or row[0] != parent_id:
        raise _bad("PR_REPAIR_RELATION_UNPROVEN", "The private repair child relation is unavailable")
    from . import pm_maintenance_repairs as repairs
    parent = _operation(ops.db, parent_id)
    fixed_child = repairs._child(ops, parent)
    if fixed_child is None or _child_digest(fixed_child) != _child_digest(child):
        raise _bad("PR_REPAIR_RELATION_UNPROVEN", "The exact original repair child is unavailable")
    if write:
        repairs._guard(ops, parent, scopes=repairs.REQUIRED | REQUIRED)
    return parent_id


def _source(ops, principal, target, pre, *, write):
    _member(ops.db, principal, target["workspace_id"], write=write)
    child = _operation(ops.db, target["source_operation_id"])
    saved = pm_package_handoff.binding(child)
    if (child["action"] != "repository.continue" or child["status"] != "succeeded" or not saved
            or not pm_package_handoff.source_visible(ops.db, principal, child)):
        raise _bad("PR_SOURCE_UNAVAILABLE", "Publication requires the exact readable completed PM handoff", 404)
    code = saved["contract"]["package"]["code_source"]
    receipt = ops.db.execute("SELECT response FROM operation_steps WHERE operation_id=? AND name='source.resolve' "
                            "AND status='succeeded'", (child["operation_id"],)).fetchone()
    provider = (json.loads(receipt[0]).get("config") or {}).get("provider_origin") if receipt else None
    if (saved["contract"]["package_ref"]["workspace_id"] != target["workspace_id"]
            or code["repository"].lower() != target["repository"].lower() or code["host"] != target["host"]
            or code["repository_id"] != pre["repository_id"] or not provider):
        raise _bad("PR_SOURCE_TARGET_CHANGED", "Publication must retain the original workspace, host and native repository")
    if write:
        own = controller_authority.binding(ops.db, principal)
        if not controller_authority.same_controller(own, controller_authority.operation_binding(child)):
            raise _bad("PR_FOREIGN_CONTROLLER", "Only the original handoff controller may publish this result", 403)
        pm_package_handoff.guard(OpContext(ops, child))
    return child, provider, _repair_parent(ops, child, write=write)


def _config(ops, target):
    # Existing integration admission validates host tiers, runner and managed roots.
    repo, host_id, _ = integration._admit_target(ops, {**target, "pull_number": 1})
    area = integration.Area(ops, repo, host_id)
    return repo, area, {"provider_origin": ops.context["github_config"].api_url,
        "repository": repo.repository, "remote_url": area.url, "area": area.path,
        "workspace": repo.integrate.workspace, "host": host_id}


def _admit(ops, principal, target, params, pre):
    _exact(target, ("workspace_id", "repository", "host", "source_operation_id"))
    _exact(params, ("candidate_sha", "base_ref", "public_title", "public_body", "draft"), ("quality_ref",))
    _exact(pre, ("repository_id", "expected_base_sha"))
    if (any(not isinstance(target[k], str) for k in target)
            or not re.fullmatch(r"op_[0-9a-f]{32}", target["source_operation_id"])
            or type(pre["repository_id"]) is not int or pre["repository_id"] <= 0
            or any(not isinstance(v, str) or not integration.SHA.fullmatch(v)
                   for v in (params["candidate_sha"], pre["expected_base_sha"]))
            or not isinstance(params["base_ref"], str) or not resource_policy.HEAD_REF.fullmatch(params["base_ref"])
            or type(params["draft"]) is not bool
            or not isinstance(params["public_title"], str) or not params["public_title"].strip()
            or len(params["public_title"].encode()) > 256 or "\n" in params["public_title"]
            or not isinstance(params["public_body"], str) or len(params["public_body"].encode()) > 32_000
            or any("\x00" in params[k] for k in ("public_title", "public_body"))):
        raise _bad("INVALID_PARAMS", "Select exact SHAs, a base branch and bounded public PR text", 422)
    if "quality_ref" in params:
        quality = params["quality_ref"]
        _exact(quality, ("review_id", "operation_id", "version", "document_digest"))
        if (not isinstance(quality["review_id"], str) or not 1 <= len(quality["review_id"]) <= 80
                or not isinstance(quality["operation_id"], str) or not re.fullmatch(r"op_[0-9a-f]{32}", quality["operation_id"])
                or type(quality["version"]) is not int or quality["version"] < 1
                or not isinstance(quality["document_digest"], str) or not re.fullmatch(r"[0-9a-f]{64}", quality["document_digest"])):
            raise _bad("INVALID_PARAMS", "Select one exact immutable quality assessment", 422)
    child, provider, parent = _source(ops, principal, target, pre, write=True)
    _, _, config = _config(ops, target)
    if provider != config["provider_origin"]:
        raise _bad("PR_PROVIDER_CHANGED", "The original provider binding changed")
    source = integration.resolve_sources(ops, target["host"], [{"kind": "execution", "id": child["operation_id"]}])[0]
    if not isinstance(source["start"], str) or not integration.SHA.fullmatch(source["start"]):
        raise _bad("PR_SOURCE_BASE_UNPROVEN", "The owned handoff lacks a fixed original creation base")
    intent = {"contract": CONTRACT, **copy.deepcopy(target), **copy.deepcopy(params),
        "repository_id": pre["repository_id"], "base_sha": pre["expected_base_sha"],
        "provider_origin": provider, "config": config, "source": source,
        "child_operation_id": child["operation_id"], "child_request_digest": _child_digest(child),
        "handoff_contract_digest": pm_package_handoff.binding(child)["contract_digest"], "repair_parent_id": parent}
    _quality_access(ops.db, principal, intent)
    return {"principal": pm_workspace.principal_binding(principal), "pm_pr_publication": intent,
        "pm_pr_publication_digest": _digest(intent), "pm_lineage": {"workspace_id": target["workspace_id"]}}


def _intent(op):
    binding = (op.get("external_refs") or {}).get("admission_binding") or {}
    value = binding.get("pm_pr_publication")
    if (op.get("action") != ACTION or not isinstance(value, dict)
            or _digest(value) != binding.get("pm_pr_publication_digest")
            or any(value.get(k) != v for k, v in op["target"].items())
            or any(value.get(k) != v for k, v in op["params"].items())
            or value.get("repository_id") != op["preconditions"].get("repository_id")
            or value.get("base_sha") != op["preconditions"].get("expected_base_sha")):
        raise _bad("PR_INTENT_CHANGED", "The original fixed publication intent is unavailable")
    return value


def operation_visible(db, principal, operation):
    from .pm_source_reads import scope
    with scope(db, principal):
        return _operation_visible(db, principal, operation)


def _operation_visible(db, principal, operation):
    try:
        op = _operation(db, operation["operation_id"])
        intent = _intent(op)
        _member(db, principal, intent["workspace_id"])
        child = _operation(db, intent["child_operation_id"])
        _quality_access(db, principal, intent)
        if (_child_digest(child) != intent["child_request_digest"]
                or _object(pm_package_handoff.binding(child)).get("contract_digest") != intent["handoff_contract_digest"]
                or _repair_parent(SimpleNamespace(db=db), child) != intent["repair_parent_id"]
                or not pm_package_handoff.source_visible(db, principal, child)
                or not pm_source_lineage.visible(db, principal, "operation", child["operation_id"])):
            return False
        parent = intent["repair_parent_id"]
        if parent:
            from . import pm_maintenance_repairs as repairs
            return repairs.operation_visible(db, principal, {"operation_id": parent})
        return True
    except (OperationError, KeyError, TypeError, ValueError):
        return False


def _authorize_existing(ops, principal, op, verb):
    _member(ops.db, principal, op["target"]["workspace_id"], write=True)
    if not operation_visible(ops.db, principal, op):
        raise _bad("NOT_FOUND", "Publication not found", 404)


def _live_principal(ctx):
    original = pm_workspace.admitted_principal(ctx)
    principal, now = None, time.time()
    for row in ctx.service.db.execute("SELECT token_hash,scopes FROM api_principals WHERE actor=? AND revoked_at IS NULL "
            "AND created_at<=? AND (expires_at IS NULL OR expires_at>?) ORDER BY created_at DESC LIMIT 64",
            (original.actor, now, now)):
        scopes = json.loads(row["scopes"])
        if isinstance(scopes, list) and REQUIRED <= set(scopes):
            principal = api_auth.Principal(original.actor, REQUIRED, credential_id=row["token_hash"])
            break
    if principal is None and original.admin:
        daemon = ctx.service.context.get("daemon")
        token = vars(daemon).get("_admin_token") if daemon else None
        principal = api_auth.authenticate(ctx.service.db, token, token) if isinstance(token, str) and token else None
        if principal and principal.actor != original.actor:
            principal = None
    if principal is None:
        raise _bad("PR_AUTHORITY_UNAVAILABLE", "No current credential authorizes the original publication", 403)
    return principal


def _guard(ctx, *, publication=False):
    ctx.check_cancel()
    ctx.require_controller()
    intent = _intent(ctx.op)
    principal = _live_principal(ctx)
    _quality_access(ctx.service.db, principal, intent)
    child, provider, parent = _source(ctx.service, principal, ctx.target, ctx.preconditions, write=True)
    _, _, config = _config(ctx.service, ctx.target)
    if (config != intent["config"] or provider != intent["provider_origin"] or parent != intent["repair_parent_id"]
            or _child_digest(child) != intent["child_request_digest"]
            or pm_package_handoff.binding(child)["contract_digest"] != intent["handoff_contract_digest"]):
        raise _bad("PR_SOURCE_CHANGED", "The original publication source or binding changed")
    execution_sources.revalidate(ctx.service, [intent["source"]])
    if parent:
        from . import pm_maintenance_repairs as repairs
        repairs.require_publication_child(ctx.service, ctx.op)
    pm_source_lineage.require_new_effect(ctx.service, ctx.operation_id)
    from . import pm_lifecycle_gates
    lifecycle = pm_lifecycle_gates.operation_binding(ctx.service.db, ctx.operation_id)
    if lifecycle is None:
        raise _bad("PR_LIFECYCLE_UNPROVEN", "The original publication lifecycle binding is unavailable")
    pm_lifecycle_gates._require(pm_lifecycle_gates.head(ctx.service.db, lifecycle["workspace_id"]), lifecycle["execution_epoch"])
    if publication and parent:
        from . import pm_maintenance_repair_evidence as evidence
        row = ctx.service.db.execute("SELECT response FROM operation_steps WHERE operation_id=? "
            "AND name='pr.publication.prepare' AND status='succeeded'", (ctx.operation_id,)).fetchone()
        pinned = json.loads(row[0]) if row else {}
        evidence.require_publication(ctx.service, principal, _operation(ctx.service.db, parent),
            child_operation_id=child["operation_id"], candidate_sha=intent["candidate_sha"],
            candidate_tree=pinned.get("tree_hash"), base_ref=intent["base_ref"], base_sha=intent["base_sha"],
            files=pinned.get("files"), files_complete=pinned.get("files_complete") is True,
            quality_ref=intent.get("quality_ref"))


async def _get(ctx, path, query=None, *, new=True):
    if new:
        _guard(ctx)
    intent = _intent(ctx.op)
    if ctx.service.context["github"].cfg.api_url != intent["provider_origin"]:
        raise GitHubAmbiguous("The original provider is unavailable for receipt recovery")
    value = await ctx.service.context["github"].bounded_get(path, query=query, max_bytes=MAX_BYTES,
        timeout_s=_timeout(ctx, 20) if new else 20)
    if value["status"] != 200:
        raise GitHubAmbiguous("The original provider identity could not be observed")
    return value["payload"]


def _timeout(ctx, maximum):
    parent_id = _intent(ctx.op)["repair_parent_id"]
    if parent_id is None:
        return maximum
    from . import pm_maintenance_repairs as repairs
    remaining = repairs._record_for(ctx.service, _operation(ctx.service.db, parent_id))["deadline_at"] - time.time()
    if remaining <= 0:
        raise _bad("PM_REPAIR_DEADLINE", "The original repair deadline forbids another publication frame")
    return min(maximum, remaining)


async def _preflight(ctx, *, head):
    intent = _intent(ctx.op)
    path = ctx.service.context["github"]._repo(intent["repository"])
    repo = await _get(ctx, path)
    base = await _get(ctx, path + "/git/ref/heads/" + quote(intent["base_ref"], safe=""))
    if (not isinstance(repo, dict) or type(repo.get("id")) is not int or repo["id"] != intent["repository_id"]
            or not isinstance(repo.get("full_name"), str) or repo["full_name"].lower() != intent["repository"].lower()
            or _object(_object(base).get("object")).get("sha") != intent["base_sha"]):
        raise _bad("PR_PROVIDER_TARGET_CHANGED", "The provider repository or fixed base changed")
    if head:
        value = await _get(ctx, path + "/git/ref/heads/" + quote(host.branch(ctx.operation_id), safe=""))
        if _object(_object(value).get("object")).get("sha") != intent["candidate_sha"]:
            raise _bad("PR_PUBLISHED_HEAD_CHANGED", "The exact newly published branch is unavailable")
    return {"repository_id": repo["id"], "base_sha": intent["base_sha"], "head_checked": head}


def _raw_receipt(ops, oid):
    row = ops.db.execute("SELECT * FROM pr_creation_receipts WHERE operation_id=?", (oid,)).fetchone()
    if row is None:
        return None
    document = json.loads(row["document"])
    step = ops.db.execute("SELECT status,response FROM operation_steps WHERE operation_id=? AND name=?", (oid, CREATED)).fetchone()
    if (_digest(document) != row["document_digest"] or not step or step["status"] != "succeeded"
            or json.loads(step["response"]) != document or document["publication_operation_id"] != oid
            or any(document.get(k) != row[k] for k in
                ("workspace_id", "child_operation_id", "repair_parent_id", "provider_origin", "repository_id", "native_pr_id"))):
        raise _bad("PR_RECEIPT_UNAVAILABLE", "The original native creation receipt cannot be verified", 404)
    op = _operation(ops.db, oid)
    intent = _intent(op)
    if document["intent_digest"] != _digest(intent) or document["branch_ref"] != host.branch(oid):
        raise _bad("PR_RECEIPT_UNAVAILABLE", "The receipt no longer matches its exact original intent", 404)
    return document


def _preserve(ctx, payload):
    intent = _intent(ctx.op)
    # Only the original POST 201 enters this function. An otherwise malformed
    # response remains unresolved; no candidate GET can call this function.
    if (not isinstance(payload, dict) or type(payload.get("id")) is not int or not 0 < payload["id"] < 2**63
            or type(payload.get("number")) is not int or not 0 < payload["number"] < 2**63
            or not isinstance(payload.get("node_id"), str) or not 1 <= len(payload["node_id"]) <= 256):
        raise GitHubAmbiguous("Original PR creation response lacks a native identity")
    head, base = _object(payload.get("head")), _object(payload.get("base"))
    verified = (isinstance(head, dict) and isinstance(base, dict)
        and head.get("ref") == host.branch(ctx.operation_id) and head.get("sha") == intent["candidate_sha"]
        and base.get("ref") == intent["base_ref"] and base.get("sha") == intent["base_sha"]
        and type(_object(head.get("repo")).get("id")) is int
        and type(_object(base.get("repo")).get("id")) is int
        and _object(head.get("repo")).get("id") == intent["repository_id"]
        and _object(base.get("repo")).get("id") == intent["repository_id"])
    pinned_row = ctx.service.db.execute("SELECT response FROM operation_steps WHERE operation_id=? "
        "AND name='pr.publication.prepare' AND status='succeeded'", (ctx.operation_id,)).fetchone()
    pinned = json.loads(pinned_row[0]) if pinned_row else {}
    document = {"contract": CONTRACT, "publication_operation_id": ctx.operation_id,
        **{key: copy.deepcopy(intent[key]) for key in ("workspace_id", "child_operation_id", "child_request_digest",
            "repair_parent_id", "provider_origin", "repository", "repository_id", "candidate_sha", "base_ref", "base_sha")},
        "branch_ref": host.branch(ctx.operation_id), "intent_digest": _digest(intent),
        "native_pr_id": payload["id"], "native_pr_node_id": payload["node_id"], "pull_number": payload["number"],
        "http_status": 201, "response_digest": _digest(payload), "target_verified": verified,
        "observed_head_sha": _observed_sha(head.get("sha")),
        "observed_base_sha": _observed_sha(base.get("sha")),
        "candidate_tree": pinned.get("tree_hash"), "files_digest": _digest(pinned.get("files")),
        "files_complete": pinned.get("files_complete") is True, "quality_ref": intent.get("quality_ref"),
        "created_at": time.time(), "requirement_acceptance": "not_evaluated_by_publication"}

    def save():
        ctx.service.db.execute("""INSERT INTO pr_creation_receipts(operation_id,workspace_id,child_operation_id,
            repair_parent_id,provider_origin,repository_id,native_pr_id,document,document_digest,created_at)
            VALUES(?,?,?,?,?,?,?,?,?,?)""", (ctx.operation_id, document["workspace_id"], document["child_operation_id"],
            document["repair_parent_id"], document["provider_origin"], document["repository_id"], document["native_pr_id"],
            _canonical(document), _digest(document), document["created_at"]))
        pm_delivery_lineage.bind(ctx.service.db, ctx.operation_id, document,
            revision=intent["candidate_sha"], relation="original_pr_creation_201")
        return document
    return ctx.effect(CREATED, save, request={"intent_digest": _digest(intent), "original_status": 201}, receipt_only=True)


async def _run(ctx):
    intent = _intent(ctx.op)
    # Preserve completed POST identity before consulting mutable configuration.
    saved = _raw_receipt(ctx.service, ctx.operation_id)
    if saved:
        return await _post(ctx)  # Also settle the original outer sent step.
    if ctx.service.db.execute("SELECT 1 FROM operation_steps WHERE operation_id=? AND name=?", (ctx.operation_id, POST)).fetchone():
        return await _post(ctx)  # Original sent POST recovery does not require live write authority.
    digest = _digest(intent)

    def original_area():
        repo = ctx.service.context["github_config"].repos.get(intent["repository"].lower())
        if repo is None or repo.integrate is None or repo.integrate.remote_url != intent["config"]["remote_url"]:
            raise GitHubAmbiguous("The original Git publication binding is unavailable")
        area = integration.Area(ctx.service, repo, intent["host"])
        if area.path != intent["config"]["area"]:
            raise GitHubAmbiguous("The original managed publication area changed")
        return area

    async def prepare():
        await _preflight(ctx, head=False)
        area = original_area()
        lines = await host.run(area, host.prepare(area, ctx.operation_id, intent, digest), guard=lambda: _guard(ctx),
            timeout=lambda: _timeout(ctx, min(120, area.repo.integrate.fetch_timeout_s)))
        if "source_ready " + intent["candidate_sha"] not in lines:
            raise _bad("PR_SOURCE_CHANGED", "The exact current owned result could not be pinned")
        prepared = [line.split() for line in lines if line.startswith("prepared ")]
        if (len(prepared) != 1 or len(prepared[0]) != 3 or prepared[0][1] != intent["candidate_sha"]
                or not integration.SHA.fullmatch(prepared[0][2])):
            raise GitHubAmbiguous("The local candidate pin was not observed")
        raw = [line[5:] for line in lines if line.startswith("file ")]
        if "files_end" not in lines or len(raw) > 500 or sum(len(p.encode()) + 1 for p in raw) > 128_000:
            raise _bad("PR_CHANGE_LIMIT", "The complete changed path set exceeds its bound")
        try:
            files = [ast.literal_eval(path) if path.startswith('"') else path for path in raw]
        except (ValueError, SyntaxError):
            raise _bad("PR_PATH_UNREADABLE", "A changed path could not be represented exactly") from None
        if not files or any(not isinstance(p, str) or not p or "\x00" in p or "\ufffd" in p for p in files):
            raise _bad("PR_PATH_UNREADABLE", "The complete changed path set could not be represented exactly")
        return {"candidate_sha": prepared[0][1], "tree_hash": prepared[0][2], "intent_digest": digest,
            "base_sha": intent["base_sha"], "base_ref": intent["base_ref"], "files": files, "files_complete": True}

    async def again(_):
        return RERUN  # Only managed local pins and bounded GETs; no remote write.

    await ctx.step("pr.publication.prepare", prepare, request={"intent_digest": digest}, reconcile=again)

    async def push():
        await _preflight(ctx, head=False)
        area = original_area()
        lines = await host.run(area, host.push(area, ctx.operation_id, intent, digest), guard=lambda: _guard(ctx, publication=True),
            timeout=lambda: _timeout(ctx, min(120, area.repo.integrate.fetch_timeout_s)))
        if f"created {digest} {intent['candidate_sha']} {host.branch(ctx.operation_id)}" not in lines:
            raise GitHubAmbiguous("The original create-only branch receipt is unavailable")
        return {"created": True, "branch_ref": host.branch(ctx.operation_id), "candidate_sha": intent["candidate_sha"]}

    async def pushed(_):
        area = original_area()
        lines = await host.run(area, host.readback(area, ctx.operation_id, digest))
        if (f"created {digest} {intent['candidate_sha']} {host.branch(ctx.operation_id)}" in lines
                and "seen " + intent["candidate_sha"] in lines):
            return {"created": True, "branch_ref": host.branch(ctx.operation_id), "candidate_sha": intent["candidate_sha"]}
        return None

    await ctx.step("pr.publication.push", push, request={"intent_digest": digest}, reconcile=pushed)

    return await _post(ctx)


async def _post(ctx):
    intent = _intent(ctx.op)
    digest = _digest(intent)
    async def create():
        await _preflight(ctx, head=True)
        _guard(ctx, publication=True)  # Final unsent POST frame; no intervening awaited read.
        status, payload = await ctx.service.context["github"].create_pull(intent["repository"],
            title=intent["public_title"], body=intent["public_body"], head=host.branch(ctx.operation_id),
            base=intent["base_ref"], draft=intent["draft"], timeout_s=_timeout(ctx, 30))
        if status != 201:
            if status in {400, 401, 403, 404, 409, 422}:
                raise _bad("PR_CREATE_REFUSED", "GitHub refused the original pull request creation")
            raise GitHubAmbiguous("No original PR creation response was established")
        try:
            return _preserve(ctx, payload)
        except Exception:  # noqa: BLE001 - after 201, failed local preservation never proves the write absent
            raise GitHubAmbiguous("The original PR creation receipt could not be preserved") from None

    async def created(_):
        saved = _raw_receipt(ctx.service, ctx.operation_id)
        if saved:
            return saved
        # A negative search does not prove the POST was unsent. Candidates are
        # deliberately NOT returned as receipts or promoted to creation facts.
        try:
            path = ctx.service.context["github"]._repo(intent["repository"]) + "/pulls"
            candidates = await _get(ctx, path, {"state": "all", "head": intent["repository"].split("/")[0] + ":" +
                host.branch(ctx.operation_id), "base": intent["base_ref"], "per_page": 20, "page": 1}, new=False)
            count = min(len(candidates), 20) if isinstance(candidates, list) else None
            ctx.set_refs(pr_publication_recovery={"status": "unresolved_original_201_missing", "candidates_observed": count,
                "candidate_limit": 20, "search_complete": False, "ownership_established": False, "post_resend_allowed": False})
        except (GitHubAmbiguous, GitHubReadLimit):
            pass
        return None

    receipt = await ctx.step(POST, create, request={"intent_digest": digest}, reconcile=created)
    return _outcome(receipt)


def _outcome(receipt):
    if not receipt["target_verified"]:
        raise NeedsAttention("PR_CREATED_TARGET_CHANGED", "The original PR was created but its head or base differed; review its saved identity")
    return {"published": True, "creation_receipt": receipt, "merged": False, "deployed": False}


def get_publication(ops, principal, publication_operation_id):
    op = _operation(ops.db, publication_operation_id)
    if not operation_visible(ops.db, principal, op):
        raise _bad("NOT_FOUND", "Publication not found", 404)
    return {"operation_id": publication_operation_id, "status": op["status"],
        "creation_receipt": _raw_receipt(ops, publication_operation_id),
        "recovery": (op.get("external_refs") or {}).get("pr_publication_recovery")}


def require_creation_receipt(ops, principal, publication_operation_id, *, child_operation_id,
        provider_origin, repository_id, repair_parent_id=None, native_pr_id=None):
    receipt = get_publication(ops, principal, publication_operation_id)["creation_receipt"]
    expected = {"child_operation_id": child_operation_id, "provider_origin": provider_origin,
        "repository_id": repository_id, "repair_parent_id": repair_parent_id}
    if native_pr_id is not None:
        expected["native_pr_id"] = native_pr_id
    if not receipt or not receipt["target_verified"] or any(receipt.get(k) != v for k, v in expected.items()):
        raise _bad("PR_CREATION_UNPROVEN", "The exact original native PR creation is not proven")
    return receipt


def list_creation_receipts(ops, principal, repair_parent_id, child_operation_id, *, after=0, limit=20):
    if (type(after) is not int or not 0 <= after < 2**63 or type(limit) is not int or not 1 <= limit <= 50
            or not isinstance(child_operation_id, str) or not integration.OP_ID.fullmatch(child_operation_id)
            or repair_parent_id is not None and
            (not isinstance(repair_parent_id, str) or not integration.OP_ID.fullmatch(repair_parent_id))):
        raise _bad("INVALID_PARAMS", "Use a valid publication cursor and limit from 1 through 50", 422)
    rows = ops.db.execute("SELECT sequence,operation_id FROM pr_creation_receipts WHERE repair_parent_id IS ? "
        "AND child_operation_id=? AND sequence>? ORDER BY sequence LIMIT ?",
        (repair_parent_id, child_operation_id, after, limit + 1)).fetchall()
    receipts = []
    for row in rows[:limit]:
        if operation_visible(ops.db, principal, {"operation_id": row["operation_id"]}):
            receipts.append({"sequence": row["sequence"], "receipt": _raw_receipt(ops, row["operation_id"])})
    return {"creation_receipts": receipts, "next_after": rows[limit - 1]["sequence"] if len(rows) > limit else None}
