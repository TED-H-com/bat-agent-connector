"""Fixed W35 engineering handoffs in the existing journal; no execution or artifacts.

Public integration must call ``operation_visible`` for this action, including the
original operation's parameters and steps. Source receipts remain recoverable
internally after authority loss; public reads never borrow the creator's access.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
import time

from . import (
    pm_evidence,
    pm_plans,
    pm_read_cache,
    pm_runs,
    pm_spec_sources,
    pm_sprints,
    pm_workspace,
    repository_sync,
    work_items,
)
from . import (
    pm_work_package_format as fmt,
)
from .operations import RERUN, ActionDef, Cancelled, OperationError, OperationService, StepFailed

ACTION = "pm.work_package.create"
PACKAGE = re.compile(r"pmwp_[0-9a-f]{32}")
MAX_READ_BYTES = 2_000_000
MAX_DOCUMENT_BYTES = 1_500_000
MAX_ANCESTORS = 256
PRECONDITIONS = {"expected_binding_id", "expected_work_fingerprint", "expected_scope_revision", "expected_previous_package_id"}


def _bad(code, message, status=409):
    return OperationError(code, message, status)


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _digest(value):
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _size(value):
    return len(json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8"))


def _checked(value):
    if _size(value) > MAX_READ_BYTES:
        raise _bad("PM_PACKAGE_READ_TOO_LARGE", "The complete work package exceeds the read byte limit", 413)
    return value


def schema(journal):
    pm_sprints.schema(journal)
    pm_runs.schema(journal)
    with journal.tx():
        journal.db.execute("""CREATE TABLE IF NOT EXISTS pm_work_packages (
            sequence INTEGER PRIMARY KEY AUTOINCREMENT, package_id TEXT NOT NULL UNIQUE,
            workspace_id TEXT NOT NULL, work_item_id TEXT NOT NULL,
            previous_package_id TEXT UNIQUE, document_digest TEXT NOT NULL, document TEXT NOT NULL,
            actor TEXT NOT NULL, created_at REAL NOT NULL, operation_id TEXT NOT NULL UNIQUE)""")
        journal.db.execute("CREATE INDEX IF NOT EXISTS pm_package_work ON pm_work_packages(workspace_id,work_item_id,sequence)")
        # Direct handoffs can be admitted before a Work link exists. Index their
        # frozen package identity; reads must not scan every operation's JSON.
        journal.db.execute("""CREATE INDEX IF NOT EXISTS pm_handoff_package ON operations(
            json_extract(external_refs,'$.admission_binding.pm_handoff.contract.package_ref.workspace_id'),
            json_extract(external_refs,'$.admission_binding.pm_handoff.contract.package_ref.package_id'), operation_id)
            WHERE json_extract(external_refs,'$.admission_binding.pm_handoff.contract.package_ref.package_id') IS NOT NULL""")
        for verb in ("UPDATE", "DELETE"):
            journal.db.execute(f"CREATE TRIGGER IF NOT EXISTS pm_work_packages_{verb.lower()}_immutable BEFORE {verb} ON pm_work_packages "
                               "BEGIN SELECT RAISE(ABORT, 'PM work package history is immutable'); END")


def install(ops):
    schema(ops.journal)
    if ACTION not in ops.actions:
        ops.register(ActionDef(ACTION, "manage", "Freeze one sourced engineering work package; never start work", _run,
                               _admit, ("workspace_id", "work_item_id"), authorize_existing=_authorize_existing))


def _member(ops, principal, wid, *, write=False):
    if not principal.allows("observe") or write and not principal.allows("manage"):
        raise _bad("FORBIDDEN", "Package reads need observe; creation also needs manage", 403)
    return pm_workspace.require_member(ops.db, principal, wid, write=write)


@pm_read_cache.memoized(guard_independent=True, freeze=pm_read_cache.json_snapshot, thaw=json.loads)
def _record(db, wid, package_id):
    if not isinstance(package_id, str) or not PACKAGE.fullmatch(package_id):
        raise _bad("PM_PACKAGE_NOT_FOUND", "Work package not found", 404)
    row = db.execute("SELECT * FROM pm_work_packages WHERE workspace_id=? AND package_id=?", (wid, package_id)).fetchone()
    if row is None:
        raise _bad("PM_PACKAGE_NOT_FOUND", "Work package not found", 404)
    try:
        document = json.loads(row["document"])
        if (document.get("workspace_id") != wid or document.get("package_id") != package_id
                or document.get("work_item_id") != row["work_item_id"]
                or document.get("previous_package_id") != row["previous_package_id"]
                or document.get("operation_id") != row["operation_id"] or _digest(document) != row["document_digest"]):
            raise ValueError
    except (AttributeError, TypeError, ValueError):
        raise _bad("PM_PACKAGE_CORRUPT", "The original work package cannot be verified") from None
    return {**dict(row), "document": document}


def _latest(db, wid, work_item_id):
    row = db.execute("SELECT package_id FROM pm_work_packages WHERE workspace_id=? AND work_item_id=? ORDER BY sequence DESC LIMIT 1",
                     (wid, work_item_id)).fetchone()
    return row[0] if row else None


def _plan_ref(plan):
    adoption = plan["adoption"]
    if not adoption:
        raise _bad("PM_ADOPTED_PLAN_REQUIRED", "A work package needs an explicitly adopted plan")
    return {"revision_id": plan["revision_id"], "document_digest": plan["document_digest"],
            "scope_revision": adoption["scope_revision"], "decision_id": adoption["decision_id"],
            "adoption_operation_id": "op_" + adoption["decision_id"][6:],
            "plan_commit_sha": adoption["commit_sha"], "plan_tree_sha": adoption["tree_sha"]}


def _original_plan(ops, principal, wid, ref):
    pm_plans._read(ops, principal, wid)
    from .pm_access import require_operation_sources
    require_operation_sources(ops.db, principal, [
        *pm_plans.source_operation_ids(ops.db, wid, ref["revision_id"], include_adoption=False),
        ref["adoption_operation_id"],
    ])
    plan = pm_plans._revision(ops.db, wid, ref["revision_id"])
    pm_plans.revision_files(ops.db, wid, ref["revision_id"])
    row = ops.db.execute("SELECT result FROM pm_plan_adoptions WHERE workspace_id=? AND revision_id=? AND operation_id=?",
                         (wid, ref["revision_id"], ref["adoption_operation_id"])).fetchone()
    if not row or _plan_ref({**plan, "adoption": json.loads(row[0])}) != ref:
        raise _bad("PM_PACKAGE_SOURCE_UNAVAILABLE", "The original adopted plan is unavailable", 404)
    return plan


def _git_identity(binding):
    return {key: binding[key] for key in ("pm_workspace_id", "server_id", "repository_id", "binding_digest", "source_sha")}


def _evidence(ops, principal, wid, refs):
    result = []
    for ref in refs:
        source = pm_evidence.get_evidence(ops, principal, wid, ref["evidence_id"])["evidence"]
        if source["fact_digest"] != ref["fact_digest"]:
            raise _bad("PM_PACKAGE_SOURCE_UNAVAILABLE", "The original evidence is unavailable", 404)
        result.append(source)
    return result


def _access_context(ops, principal, wid, context):
    from .pm_source_reads import check, scope
    # Several original-source readers can reach this same adopted plan/work
    # context. An ancestry pointer or code-configuration digest is not read by
    # this historical predicate; current configuration stays in the caller.
    selected = {key: context[key] for key in (
        'server_id', 'plan_ref', 'work_ref', 'evidence_refs', 'git_binding', 'dod_ref') if key in context}
    key = _digest([wid, selected])
    with scope(ops.db, principal) as state:
        def visible():
            _access_context_sources(ops, principal, wid, context)
            return True
        if ('package_sources', key) in state['active']:
            visible()
        elif not check(ops.db, principal, 'package_sources', key, visible):
            raise _bad('NOT_FOUND', 'Original package sources unavailable', 404)


def _access_context_sources(ops, principal, wid, context):
    workspace = _member(ops, principal, wid)
    if workspace["server_id"] != context["server_id"]:
        raise _bad("PM_PACKAGE_SOURCE_UNAVAILABLE", "The original workspace identity is unavailable", 404)
    _original_plan(ops, principal, wid, context["plan_ref"])
    work = context["work_ref"]
    row = ops.db.execute("SELECT * FROM pm_work_bindings WHERE workspace_id=? AND binding_id=?", (wid, work["binding_id"])).fetchone()
    binding = {**dict(row), "requirement_ids": json.loads(row["requirement_ids"])} if row else None
    if (binding is None or _digest(binding) != work["binding_document_digest"]
            or binding["work_item_id"] != work["work_item_id"] or binding["project_id"] != work["project_id"]
            or binding["plan_revision_id"] != context["plan_ref"]["revision_id"]):
        raise _bad("PM_PACKAGE_SOURCE_UNAVAILABLE", "The original work association is unavailable", 404)
    _evidence(ops, principal, wid, context["evidence_refs"])
    if context["git_binding"] is not None:
        binding = pm_runs.workspace_worktree(ops.db, wid)
        if binding is None or _git_identity(binding) != context["git_binding"]:
            raise _bad("PM_PACKAGE_SOURCE_UNAVAILABLE", "The original repository source association is unavailable", 404)
    if context["dod_ref"] is not None:
        ref = context["dod_ref"]
        row = pm_sprints._artifact(ops.db, wid, ref["revision_id"], "dod")
        if _digest(row["document"]) != ref["document_digest"]:
            raise _bad("PM_PACKAGE_SOURCE_UNAVAILABLE", "The original DoD revision is unavailable", 404)


def _access(ops, principal, record):
    # A revised narrative can quote a prior package. Keep its original source
    # policy even when the latest package selects fewer explicit evidence IDs.
    seen = set()
    while record is not None:
        identifier = record["package_id"]
        if identifier in seen or len(seen) >= MAX_ANCESTORS:
            raise _bad("PM_PACKAGE_SOURCE_UNAVAILABLE", "Package source history cannot be verified", 404)
        seen.add(identifier)
        _access_context(ops, principal, record["workspace_id"], record["document"]["context"])
        previous = record["previous_package_id"]
        prior = _record(ops.db, record["workspace_id"], previous) if previous else None
        if prior is not None and prior["work_item_id"] != record["work_item_id"]:
            raise _bad("PM_PACKAGE_SOURCE_UNAVAILABLE", "The original package lineage is unavailable", 404)
        record = prior
    return len(seen)


def _check(ops, principal, target, params, pre):
    fmt.exact(target, ("workspace_id", "work_item_id"))
    fmt.exact(params, fmt.FIELDS, fmt.OPTIONAL)
    fmt.exact(pre, PRECONDITIONS)
    if (not isinstance(pre["expected_binding_id"], str) or not re.fullmatch(r"pmwb_[0-9a-f]{32}", pre["expected_binding_id"])
            or not isinstance(pre["expected_work_fingerprint"], str) or not pm_plans.DIGEST.fullmatch(pre["expected_work_fingerprint"])
            or not isinstance(pre["expected_scope_revision"], str) or not re.fullmatch(r"pmscope_[0-9a-f]{32}", pre["expected_scope_revision"])
            or pre["expected_previous_package_id"] is not None and (not isinstance(pre["expected_previous_package_id"], str)
                or not PACKAGE.fullmatch(pre["expected_previous_package_id"]))):
        raise fmt.bad("Supply the exact original binding, work fingerprint, scope revision and previous package")
    wid = target["workspace_id"]
    workspace = _member(ops, principal, wid, write=True)
    item = work_items._get_item(ops.db, target["work_item_id"], active=True)
    project = work_items._get_project(ops.db, item["project_id"], active=True)
    binding = pm_evidence._binding(ops.db, wid, item["work_item_id"])
    if binding is None or binding["binding_id"] != pre["expected_binding_id"] or binding["project_id"] != item["project_id"]:
        raise _bad("PM_WORK_BINDING_CHANGED", "Select the current explicit work association")
    if pre["expected_work_fingerprint"] != work_items.fingerprint(item):
        raise _bad("WORK_ITEM_CHANGED", "Read this work's current content before preparing a package")
    if binding["plan_revision_id"] is None:
        raise _bad("PM_ADOPTED_PLAN_REQUIRED", "Associate work with a fixed adopted plan first")
    plan = pm_plans.get_plan(ops, principal, wid, binding["plan_revision_id"])["plan"]
    plan_ref = _plan_ref(plan)
    current = pm_plans.accepted_plan(ops, principal, wid)["plan"]
    if current is None or pre["expected_scope_revision"] != plan_ref["scope_revision"] or _plan_ref(current)["scope_revision"] != plan_ref["scope_revision"]:
        raise _bad("PM_PACKAGE_SCOPE_CHANGED", "Prepare work against the current explicitly adopted scope")
    previous = params.get("previous_package_id")
    if previous != pre["expected_previous_package_id"] or previous != _latest(ops.db, wid, item["work_item_id"]):
        raise _bad("PM_PACKAGE_VERSION_CONFLICT", "Read the last fixed package before revising it")
    if previous is not None:
        prior = _record(ops.db, wid, previous)
        if prior["work_item_id"] != item["work_item_id"] or _access(ops, principal, prior) >= MAX_ANCESTORS:
            raise _bad("PM_PACKAGE_HISTORY_LIMIT", "The original work package lineage cannot be extended")
    allowed = {r["id"]: r for r in plan["document"]["requirements"] if r["id"] in binding["requirement_ids"]}
    descriptions = fmt.descriptions(ops, params, allowed)
    code = descriptions["code_source"]
    if code["repository"].lower() not in {name.lower() for name in project["repositories"]}:
        raise _bad("PROJECT_REPOSITORY_CHANGED", "Select the bound work project's configured code repository")
    git_binding = None
    if descriptions["source_refs"]:
        git_binding = pm_spec_sources._binding(ops, principal, wid)
        for ref in descriptions["source_refs"]:
            if (ref["repository"] != git_binding["target"]["repository"] or ref["commit_sha"] != git_binding["source_sha"]
                    or code["repository_id"] != git_binding["repository_id"]):
                raise _bad("PM_PACKAGE_SOURCE_MISMATCH", "Select fixed documents from the original bound code repository")
    evidence = _evidence(ops, principal, wid, descriptions["evidence_refs"])
    dod, dod_ref = None, None
    if params.get("dod_revision_id") is not None:
        row = pm_sprints._artifact(ops.db, wid, params["dod_revision_id"], "dod")
        dod = row["document"]
        dod_plan = pm_plans.get_plan(ops, principal, wid, dod["plan_ref"]["revision_id"])["plan"]
        if _plan_ref(dod_plan)["scope_revision"] != plan_ref["scope_revision"]:
            raise _bad("PM_PACKAGE_DOD_SCOPE_MISMATCH", "Select the Definition of Done for this adopted scope")
        dod_ref = {"revision_id": row["revision_id"], "document_digest": _digest(dod)}
    context = {"server_id": workspace["server_id"], "plan_ref": plan_ref,
        "work_ref": {"binding_id": binding["binding_id"], "work_item_id": item["work_item_id"], "project_id": item["project_id"],
                     "binding_document_digest": _digest(binding), "work_version": item["version"],
                     "work_fingerprint": work_items.fingerprint(item), "project_version": project["version"]},
        "evidence_refs": descriptions["evidence_refs"], "dod_ref": dod_ref,
        "git_binding": _git_identity(git_binding) if git_binding else None,
        "code_config_digest": repository_sync.digest(repository_sync.config_snapshot(ops, {key: code[key] for key in ("repository", "host", "workspace_id")})),
        "previous_package_id": previous}
    inputs = {value["source_input_id"] for value in descriptions["requirements"]}
    inputs.update(value["source_input_id"] for requirement in descriptions["requirements"] for value in requirement["acceptance"])
    document = {**descriptions, "context": context, "dod": dod, "evidence": evidence,
                "source_inputs": [value for value in plan["document"]["selected_inputs"] if value["input_id"] in inputs],
                "previous_package_id": previous, "requirement_coverage": "explicit_selected_adopted_requirements"}
    if _size(document) > MAX_DOCUMENT_BYTES:
        raise _bad("PM_PACKAGE_TOO_LARGE", "Select a smaller complete engineering work package", 413)
    return document


def _admit(ops, principal, target, params, pre):
    with ops.journal.tx():
        document = _check(ops, principal, target, params, pre)
    # Metadata, not additions to params/target/preconditions: exact envelope
    # matching and the user's original idempotency key remain unchanged.
    return {"principal": pm_workspace.principal_binding(principal), "package_context": document["context"],
            "package_description_digest": _digest(document)}


class _SourceReader:
    def __init__(self, db):
        self.db = db

    def get(self, operation_id, *, steps=False, principal=None):
        row = self.db.execute("SELECT * FROM operations WHERE operation_id=?", (operation_id,)).fetchone()
        if row is None or row["action"].startswith("pm."):
            raise _bad("NOT_FOUND", "Original source operation not found", 404)
        return OperationService._decode(row)


def operation_visible(db, principal, operation):
    """DB-only hook for the integrator's shared operation/feed policy."""
    row = db.execute("SELECT * FROM operations WHERE operation_id=?", (operation.get("operation_id"),)).fetchone()
    if row is None or row["action"] != ACTION:
        return False
    op = OperationService._decode(row)
    binding = (op.get("external_refs") or {}).get("admission_binding") or {}
    context = binding.get("package_context")
    if not isinstance(context, dict):
        return False
    reader = _SourceReader(db)
    try:
        wid = op["target"]["workspace_id"]
        _access_context(reader, principal, wid, context)
        previous = context["previous_package_id"]
        if previous:
            _access(reader, principal, _record(db, wid, previous))
        return True
    except (OperationError, KeyError, TypeError, ValueError):
        return False


def _authorize_existing(ops, principal, op, verb):
    _member(ops, principal, op["target"]["workspace_id"], write=True)
    if not operation_visible(ops.db, principal, op):
        raise _bad("NOT_FOUND", "Original work package sources are unavailable", 404)


def _current_description(ctx, principal):
    document = _check(ctx.service, principal, ctx.target, ctx.params, ctx.preconditions)
    binding = ctx.admission_binding or {}
    if document["context"] != binding.get("package_context") or _digest(document) != binding.get("package_description_digest"):
        raise _bad("PM_PACKAGE_CONTEXT_CHANGED", "The admitted package context changed; keep the original request")
    return document


async def _read_sources(ctx, principal):
    ctx.check_cancel()
    document = _current_description(ctx, principal)
    code = document["code_source"]
    preview = await repository_sync.source_preview(ctx.service, {key: code[key] for key in ("repository", "host", "workspace_id", "source_ref")})
    observed = {**preview["target"], **{key: preview[key] for key in ("source_ref", "source_sha", "repository_id", "binding_digest")}}
    if observed != code:
        raise _bad("PM_PACKAGE_CODE_SOURCE_CHANGED", "The selected published code identity changed")
    ctx.check_cancel()
    _current_description(ctx, principal)
    documents, spec_read = [], None
    refs = document["source_refs"]
    if refs:
        first = refs[0]
        source = await pm_spec_sources.read_sources(ctx.service, principal, ctx.target["workspace_id"],
            repository=first["repository"], commit_sha=first["commit_sha"], paths=[ref["path"] for ref in refs])
        by_path = {value["path"]: value for value in source["documents"]}
        for ref in refs:
            value = by_path.get(ref["path"])
            if (value is None or value["status"] != "read" or value["source_id"] != ref["source_id"]
                    or value["sha256"] != ref["sha256"] or value["commit_sha"] != ref["commit_sha"]):
                raise _bad("PM_PACKAGE_SOURCE_CHANGED", "The original selected document cannot be read exactly")
            documents.append(value)
        documents = source["documents"]
        spec_read = {key: value for key, value in source.items() if key != "documents"}
    ctx.check_cancel()
    _current_description(ctx, principal)
    return {"code_source": code, "code_read_basis": "exact_published_preview_at_creation", "code_observed_at": time.time(),
            "documents": documents, "spec_read": spec_read,
            "sources_digest": _digest(documents), "description_digest": _digest(document)}


def _validate_sources(document, sources):
    if (sources["description_digest"] != _digest(document) or sources["code_source"] != document["code_source"]
            or sources["sources_digest"] != _digest(sources["documents"])):
        raise _bad("PM_PACKAGE_SOURCE_CHANGED", "The fixed source receipt does not match its admitted description")
    refs = {ref["path"]: ref for ref in document["source_refs"]}
    documents = sources["documents"]
    if len(documents) != len(refs) or {value["path"] for value in documents} != set(refs):
        raise _bad("PM_PACKAGE_SOURCE_CHANGED", "The fixed source receipt does not match the exact selected paths")
    for value in documents:
        ref = refs[value["path"]]
        raw = value["content"].encode("utf-8")
        if (value["status"] != "read" or any(value[key] != ref[key] for key in ("commit_sha", "source_id", "sha256"))
                or hashlib.sha256(raw).hexdigest() != ref["sha256"] or len(raw) != value["size"]):
            raise _bad("PM_PACKAGE_SOURCE_CHANGED", "Original source bytes do not match the saved read receipt")
    read = sources["spec_read"]
    if (bool(refs) != bool(read) or read is not None and (
            read["document_digest"] != _digest({**{k: v for k, v in read.items() if k != "document_digest"}, "documents": documents})
            or any(read[key] != document["context"]["git_binding"][key] for key in ("server_id", "repository_id", "binding_digest"))
            or read["commit_sha"] != document["context"]["git_binding"]["source_sha"])):
        raise _bad("PM_PACKAGE_SOURCE_CHANGED", "Original specification provenance does not match the saved read receipt")


async def _run(ctx):
    # A committed local effect is the original result even if membership, source
    # availability or accepted scope changed later. Public access is independent.
    saved = ctx.service.db.execute("SELECT response FROM operation_steps WHERE operation_id=? AND name='pm.package.record' AND status='succeeded'",
                                   (ctx.operation_id,)).fetchone()
    if saved:
        return json.loads(saved[0])
    principal = pm_workspace.admitted_principal(ctx)
    _current_description(ctx, principal)

    async def reread(_):
        return RERUN  # Pure fixed-source reads; no start, write, or transfer intent.

    async def read_sources():
        try:
            return await _read_sources(ctx, principal)
        except Cancelled:
            # This step has only bounded reads. Record an explicit cancelled read
            # instead of letting generic external-call handling label it INTERNAL.
            raise OperationError("CANCELLED", "Fixed source reads stopped before package creation", 409) from None

    try:
        sources = await ctx.step("pm.package.sources", read_sources,
            request={"description_digest": ctx.admission_binding["package_description_digest"]}, reconcile=reread)
    except StepFailed:
        ctx.check_cancel()  # A cancelled pure read has no effect to reconcile.
        raise
    ctx.check_cancel()

    def record():
        document = _current_description(ctx, principal)
        _validate_sources(document, sources)
        now = time.time()
        document.update(package_id="pmwp_" + ctx.operation_id[3:], workspace_id=ctx.target["workspace_id"],
            work_item_id=ctx.target["work_item_id"], operation_id=ctx.operation_id,
            created_by=ctx.actor, created_at=now, schema_version=1, sources=sources,
            status="frozen_engineering_description", execution_authorized=False, execution_spec="not_assigned",
            quality_assessment="unassessed", claims_as_evidence=False)
        if _size(document) > MAX_DOCUMENT_BYTES:
            raise _bad("PM_PACKAGE_TOO_LARGE", "The complete package and original sources exceed the byte limit", 413)
        digest = _digest(document)
        ctx.service.db.execute("""INSERT INTO pm_work_packages(package_id,workspace_id,work_item_id,previous_package_id,
            document_digest,document,actor,created_at,operation_id) VALUES(?,?,?,?,?,?,?,?,?)""",
            (document["package_id"], document["workspace_id"], document["work_item_id"], document["previous_package_id"],
             digest, _json(document), ctx.actor, now, ctx.operation_id))
        result = {"workspace_id": document["workspace_id"], "work_item_id": document["work_item_id"],
                  "package_id": document["package_id"], "document_digest": digest,
                  "previous_package_id": document["previous_package_id"], "execution_authorized": False,
                  "execution_spec": "not_assigned"}
        ctx.service.journal.api_event("pm_work_package", document["package_id"], "pm.work_package.created",
                                      {**result, "operation_id": ctx.operation_id}, actor=ctx.actor)
        return result

    return ctx.effect("pm.package.record", record, request={"sources_digest": sources["sources_digest"]})


def _view(record):
    return {key: record[key] for key in ("sequence", "package_id", "workspace_id", "work_item_id", "previous_package_id",
                                       "document_digest", "actor", "created_at", "operation_id", "document")}


def get_package(ops, principal, workspace_id, package_id):
    with ops.journal.tx():
        _member(ops, principal, workspace_id)
        record = _record(ops.db, workspace_id, package_id)
        _access(ops, principal, record)
        return _checked({"version": 1, "workspace_id": workspace_id, "package": _view(record)})


def list_packages(ops, principal, workspace_id, *, work_item_id=None, after=0, limit=50):
    with ops.journal.tx():
        _member(ops, principal, workspace_id)
        pm_sprints._page(after, limit)
        if work_item_id is not None and (not isinstance(work_item_id, str) or not work_items.WORK_ITEM_ID.fullmatch(work_item_id)):
            raise fmt.bad("work_item_id must identify a Connector work item")
        rows = ops.db.execute("""SELECT sequence,package_id FROM pm_work_packages WHERE workspace_id=? AND sequence>?
            AND (? IS NULL OR work_item_id=?) ORDER BY sequence LIMIT ?""", (workspace_id, after, work_item_id, work_item_id, limit + 1)).fetchall()
        result = {"version": 1, "workspace_id": workspace_id, "packages": [], "next_cursor": None,
                  "coverage": {"after": after, "returned": 0, "scanned": 0, "visibility_filtered": False, "byte_limited": False, "has_more": False}}
        cursor = after
        for index, row in enumerate(rows[:limit]):
            previous = copy.deepcopy(result)
            try:
                record = _record(ops.db, workspace_id, row["package_id"])
                _access(ops, principal, record)
                result["packages"].append(_view(record))
            except OperationError as error:
                if error.status != 404:
                    raise
                result["coverage"]["visibility_filtered"] = True
            more = index + 1 < len(rows)
            result["coverage"].update(returned=len(result["packages"]), scanned=index + 1, has_more=more)
            result["next_cursor"] = row["sequence"] if more else None
            if _size(result) > MAX_READ_BYTES:
                if index == 0:
                    raise _bad("PM_PACKAGE_READ_TOO_LARGE", "One whole work package exceeds the page byte limit", 413)
                result = previous
                result["next_cursor"] = cursor
                result["coverage"].update(byte_limited=True, has_more=True)
                break
            cursor = row["sequence"]
        return _checked(result)


def document_readiness_checks(doc, *, accepted_dependencies=frozenset()):
    checks = []

    def check(name, passed, detail):
        checks.append({"check": name, "status": "passed" if passed else "blocked", "detail": detail})

    code = doc["code_source"]
    check("design", doc["design"]["status"] != "unresolved", "Engineering design may be proportionate or explicitly not applicable.")
    check("delivery_plan", all(doc["delivery"][aspect]["status"] != "unresolved"
          for aspect in ("migration", "rollout", "rollback", "observation")),
          "Migration, rollout, rollback and observation need descriptions or explicit tailoring rationales.")
    check("unresolved", not doc["unresolved"], "Unresolved engineering questions are retained and block this handoff.")
    check("spec_code_alignment", all(ref["commit_sha"] == code["source_sha"] for ref in doc["source_refs"]),
          "Different spec and code commits remain distinct; compatibility has not been assessed in this slice.")
    requirement_ids = {r["id"] for r in doc["requirements"]}
    check("task_coverage", requirement_ids <= {rid for task in doc["tasks"] for rid in task["requirement_ids"]},
          "Every selected requirement needs at least one technical task.")
    for requirement in doc["requirements"]:
        rid = requirement["id"]
        refs = {entry["source_input_id"] for entry in requirement["acceptance"]}
        mapped = {ref for item in doc["verification"] if item["requirement_id"] == rid for ref in item["acceptance_input_ids"]}
        check("acceptance:" + rid, bool(refs) and refs <= mapped,
              "Every original acceptance condition needs a proposed verification method; methods have not run.")
        check("source_status:" + rid, requirement["source_status"] == "user_confirmed"
              and all(entry["source_status"] == "user_confirmed" for entry in requirement["acceptance"]),
              "Unconfirmed, assumed or conflicting source statements are preserved; adoption alone does not confirm them.")
        if requirement["depends_on"]:
            qualified = set(requirement["depends_on"]) <= accepted_dependencies
            checks.append({"check": "dependency:" + rid, "status": "passed" if qualified else "blocked", "assessment": "accepted" if qualified else "unknown",
                           "requirement_ids": requirement["depends_on"],
                           "detail": "Declared prerequisites have explicit original qualified assessments." if qualified else "Required outcomes have not been evaluated; work claims and checkboxes do not prove readiness."})
    return checks


def readiness(ops, principal, workspace_id, package_id, *, quality_refs=None):
    """Current local handoff checks; never a runtime or delivery authorization."""
    with ops.journal.tx():
        package = get_package(ops, principal, workspace_id, package_id)["package"]
        doc, checks = package["document"], []
        from .pm_requirement_acceptance import require_dependencies
        accepted_dependencies = set(require_dependencies(ops, principal, package, quality_refs))

        def check(name, passed, detail):
            checks.append({"check": name, "status": "passed" if passed else "blocked", "detail": detail})

        current = pm_plans.accepted_plan(ops, principal, workspace_id)["plan"]
        check("scope", current is not None and _plan_ref(current)["scope_revision"] == doc["context"]["plan_ref"]["scope_revision"],
              "The package retains its original plan; a different current scope needs explicit replanning.")
        work = work_items._get_item(ops.db, doc["work_item_id"])
        project = work_items._get_project(ops.db, work["project_id"])
        binding = pm_evidence._binding(ops.db, workspace_id, doc["work_item_id"])
        check("work", bool(binding) and binding["binding_id"] == doc["context"]["work_ref"]["binding_id"]
              and work_items.fingerprint(work) == doc["context"]["work_ref"]["work_fingerprint"]
              and work["project_id"] == doc["context"]["work_ref"]["project_id"] and not work["archived"] and not project["archived"],
              "Current active work and its explicit association must match the frozen package.")
        check("package_revision", _latest(ops.db, workspace_id, doc["work_item_id"]) == package_id,
              "A newer package is a separate choice, never a replacement of the original attempt specification.")
        code = doc["code_source"]
        try:
            config = repository_sync.config_snapshot(ops, {key: code[key] for key in ("repository", "host", "workspace_id")})
            configured = repository_sync.digest(config) == doc["context"]["code_config_digest"]
        except OperationError:
            configured = False
        check("code_configuration", configured, "Published head and adapter readiness are not refreshed by this local read.")
        check("project_repository", code["repository"].lower() in {name.lower() for name in project["repositories"]},
              "The current work project must still name the selected code repository.")
        checks.extend(document_readiness_checks(doc, accepted_dependencies=accepted_dependencies))
        return _checked({"version": 1, "workspace_id": workspace_id, "package_id": package_id,
            "document_digest": package["document_digest"], "status": "ready" if all(c["status"] == "passed" for c in checks) else "blocked",
            "ready_for": "engineering_handoff_review", "checks": checks,
            "coverage": {"basis": "fixed_package_and_current_local_state", "network_checked": False, "runtime": "not_checked",
                         "budget": "not_checked", "delivery_target": "not_checked", "quality": "unassessed"},
            "execution_available": False, "execution_authorized": False,
            "dispatch_readiness": "separate_fixed_handoff_admission",
            "handoff": {"action": "repository.continue", "contract": "pm-engineering-handoff/v1",
                        "code_basis_required": True, "compatibility": "unverified",
                        "source_materialization": "bounded_full_prompt_or_fixed_spec_artifact",
                        "materialization_action": "pm.spec.materialize"}})
