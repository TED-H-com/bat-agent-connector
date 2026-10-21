"""W44: one explicitly selected, immutable primary release baseline per workspace.

Selection records publication evidence; it neither publishes nor starts Maintain.
The integrator must wire operation_visible into operation/event/resource reads.
"""
from __future__ import annotations

import json
import re
import time
from contextlib import contextmanager
from functools import wraps

from . import pm_evidence, pm_read_cache, pm_workspace, work_items
from . import pm_release_sources as sources
from .operations import RERUN, ActionDef, Cancelled, OperationError, OperationService, StepFailed

ACTION = "pm.release_baseline.register"
BASELINE = re.compile(r"pmrb_[0-9a-f]{32}")
MAX_READ_BYTES = 2_000_000
MAX_ANCESTORS = 256


@contextmanager
def _snapshot(db):
    """One WAL read point for membership, head and all original source records."""
    if db.in_transaction:
        yield
        return
    db.execute("BEGIN")
    try:
        yield
        db.execute("COMMIT")
    except BaseException:
        db.execute("ROLLBACK")
        raise


def _snapshot_read(fn):
    @wraps(fn)
    def read(ops, *args, **kwargs):
        principal = args[0] if args else kwargs.get('principal')
        with _snapshot(ops.db), pm_read_cache.scope(ops.db, principal):
            return fn(ops, *args, **kwargs)
    return read


def _bad(message, code="INVALID_PARAMS", status=422):
    return OperationError(code, message, status)


def _json(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def _size(value):
    return len(json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8"))


def _bounded(value):
    if _size(value) > MAX_READ_BYTES:
        raise _bad("The complete release baseline exceeds the read byte limit", "PM_BASELINE_READ_TOO_LARGE", 413)
    return value


def schema(journal):
    pm_evidence.schema(journal)
    with journal.tx():
        journal.db.execute("""CREATE TABLE IF NOT EXISTS pm_release_baselines (
            sequence INTEGER PRIMARY KEY AUTOINCREMENT, baseline_id TEXT NOT NULL UNIQUE,
            workspace_id TEXT NOT NULL, version INTEGER NOT NULL, previous_baseline_id TEXT UNIQUE,
            document TEXT NOT NULL, document_digest TEXT NOT NULL, actor TEXT NOT NULL,
            created_at REAL NOT NULL, operation_id TEXT NOT NULL UNIQUE, UNIQUE(workspace_id,version))""")
        for verb in ("UPDATE", "DELETE"):
            journal.db.execute(f"CREATE TRIGGER IF NOT EXISTS pm_release_baselines_{verb.lower()}_immutable "
                               f"BEFORE {verb} ON pm_release_baselines "
                               "BEGIN SELECT RAISE(ABORT, 'Release baseline history is immutable'); END")


def install(ops):
    schema(ops.journal)
    if ACTION not in ops.actions:
        ops.register(ActionDef(ACTION, "manage", "Select an existing verified release baseline; never publish", _run,
                               _admit, ("workspace_id", "repository"), authorize_existing=_authorize_existing))


def capabilities():
    return {"version": 1, "sources": ["github_release", "deployment"],
            "github_release_channels": ["stable", "prerelease"], "package_registry": "unsupported",
            "primary_baselines_per_workspace": 1, "automatic_maintenance": False,
            "source_choices": True, "source_preview": True, "external_discovery": "explicit_read_only_request"}


def _member(ops, principal, wid, *, decide=False):
    if not principal.allows("manage" if decide else "observe"):
        raise _bad("Release baseline decisions need manage; reads need observe", "FORBIDDEN", 403)
    workspace = pm_workspace.require_member(ops.db, principal, wid)
    if decide:
        row = ops.db.execute("SELECT role FROM pm_workspace_members WHERE workspace_id=? AND actor=?", (wid, principal.actor)).fetchone()
        if row is None or row[0] != "owner":
            raise _bad("Only a current workspace owner may select its primary maintenance baseline", "FORBIDDEN", 403)
    return workspace


def _head(db, wid):
    row = db.execute("SELECT baseline_id,version FROM pm_release_baselines WHERE workspace_id=? ORDER BY version DESC LIMIT 1", (wid,)).fetchone()
    return {"baseline_id": row[0], "version": row[1]} if row else {"baseline_id": None, "version": 0}


@pm_read_cache.memoized(guard_independent=True, freeze=pm_read_cache.json_snapshot, thaw=json.loads)
def _record(db, wid, bid):
    if not isinstance(bid, str) or not BASELINE.fullmatch(bid):
        raise _bad("Release baseline not found", "PM_BASELINE_NOT_FOUND", 404)
    row = db.execute("SELECT * FROM pm_release_baselines WHERE workspace_id=? AND baseline_id=?", (wid, bid)).fetchone()
    if row is None:
        raise _bad("Release baseline not found", "PM_BASELINE_NOT_FOUND", 404)
    try:
        doc = json.loads(row["document"])
        if (sources.digest(doc) != row["document_digest"]
                or any(doc[key] != row[key] for key in ("baseline_id", "workspace_id", "operation_id", "version", "previous_baseline_id"))):
            raise ValueError
    except (ValueError, KeyError, TypeError):
        raise _bad("The original baseline document cannot be verified", "PM_BASELINE_CORRUPT", 409) from None
    return {**dict(row), "document": doc}


def _project(ops, wid, binding_id, repository, *, active=False):
    row = ops.db.execute("SELECT * FROM pm_project_bindings WHERE workspace_id=? AND binding_id=?", (wid, binding_id)).fetchone()
    if row is None:
        raise _bad("Select an explicit project association in this workspace", "PM_BASELINE_PROJECT_UNAVAILABLE", 404)
    project = work_items._get_project(ops.db, row["project_id"], active=active)
    if repository.lower() not in {name.lower() for name in project["repositories"]}:
        raise _bad("The original project no longer includes this repository", "PM_BASELINE_PROJECT_UNAVAILABLE", 404)
    return {"binding_id": row["binding_id"], "project_id": row["project_id"], "binding_digest": sources.digest(dict(row))}


class _Reader:
    def __init__(self, db):
        self.db = db

    def get(self, operation_id, *, steps=False, principal=None):
        row = self.db.execute("SELECT * FROM operations WHERE operation_id=?", (operation_id,)).fetchone()
        if row is None or row["action"].startswith("pm."):
            raise _bad("Original deployment operation unavailable", "NOT_FOUND", 404)
        return OperationService._decode(row)


def _access_context(ops, principal, wid, intent, *, decide=False):
    from .pm_source_reads import check, scope
    # Baseline/policy ancestry can reach the same publication context many
    # times. Only these inputs are read below; a previous-baseline pointer or
    # adoption revision does not create another deployment-source proof.
    # The existing snapshot memo keeps credential, write, owner, traversal
    # guard and refusal barriers. It never survives this synchronous read.
    context = {key: intent[key] for key in (
        'server_id', 'project_binding', 'repository', 'source', 'repository_id',
        'commit_sha', 'channel', 'deployment_receipt') if key in intent}
    key = sources.digest([wid, context, decide])
    with scope(ops.db, principal) as state:
        def visible():
            _access_context_sources(ops, principal, wid, intent, decide=decide)
            return True
        if ('baseline_sources', key) in state['active']:
            visible()
        elif not check(ops.db, principal, 'baseline_sources', key, visible):
            raise _bad('Original baseline sources unavailable', 'NOT_FOUND', 404)


def _access_context_sources(ops, principal, wid, intent, *, decide=False):
    # Source visibility is not an extra API scope: manage-only owners must be
    # able to receive/replay their admitted action. Standalone reads enforce
    # observe in their public entry before invoking this shared source policy.
    ws = (_member(ops, principal, wid, decide=True) if decide
          else pm_workspace.require_member(ops.db, principal, wid))
    if ws["server_id"] != intent["server_id"]:
        raise _bad("Original workspace source unavailable", "NOT_FOUND", 404)
    binding = intent["project_binding"]
    if _project(ops, wid, binding["binding_id"], intent["repository"]) != binding:
        raise _bad("Original project association unavailable", "NOT_FOUND", 404)
    if intent["source"]["kind"] == "deployment":
        proof = sources.deployment_receipt(ops, principal, wid, intent["source"]["deployment_id"],
            repository=intent["repository"], repository_id=intent["repository_id"],
            commit_sha=intent["commit_sha"], channel=intent["channel"])
        if proof != intent["deployment_receipt"]:
            raise _bad("The original deployment receipt is unavailable", "NOT_FOUND", 404)


def _access(ops, principal, wid, record, *, decide=False):
    seen = set()
    while record is not None:
        if record["baseline_id"] in seen or len(seen) >= MAX_ANCESTORS:
            raise _bad("Baseline source history cannot be verified", "PM_BASELINE_HISTORY_LIMIT", 409)
        seen.add(record["baseline_id"])
        _access_context(ops, principal, wid, record["document"]["intent"], decide=decide)
        previous = record["previous_baseline_id"]
        record = _record(ops.db, wid, previous) if previous else None
    return len(seen)


def _exact(value, keys, optional=()):
    if not isinstance(value, dict) or set(value) - set(keys) - set(optional) or set(keys) - set(value):
        raise _bad("Use the exact release-baseline request fields")


def _text(value, limit, label):
    if not isinstance(value, str) or not value.strip() or len(value) > limit or "\0" in value:
        raise _bad(f"{label} must contain 1–{limit} characters")
    return value


def _check(ops, principal, target, params, pre):
    _exact(target, {"workspace_id", "repository"})
    _exact(params, {"project_binding_id", "source", "channel", "maintenance_branch", "known_limitations", "rationale"})
    _exact(pre, {"expected_baseline_version", "expected_previous_baseline_id", "expected_repository_id", "expected_commit_sha"})
    repository = target["repository"]
    if (not isinstance(repository, str) or len(repository) > 256 or not sources.REPOSITORY.fullmatch(repository)
            or any(part in {".", ".."} for part in repository.split("/"))):
        raise _bad("Select an exact configured owner/repository")
    if (type(pre["expected_baseline_version"]) is not int or not 0 <= pre["expected_baseline_version"] < 2**63 - 1
            or type(pre["expected_repository_id"]) is not int or not 0 < pre["expected_repository_id"] < 2**63
            or not isinstance(pre["expected_commit_sha"], str) or not sources.SHA.fullmatch(pre["expected_commit_sha"])
            or pre["expected_previous_baseline_id"] is not None and (not isinstance(pre["expected_previous_baseline_id"], str)
                or not BASELINE.fullmatch(pre["expected_previous_baseline_id"]))
            or not isinstance(params["project_binding_id"], str) or not re.fullmatch(r"pmpb_[0-9a-f]{32}", params["project_binding_id"])):
        raise _bad("Select the exact baseline version, previous ID, repository identity, commit and project binding")
    wid = target["workspace_id"]
    workspace = _member(ops, principal, wid, decide=True)
    head = _head(ops.db, wid)
    if head != {"baseline_id": pre["expected_previous_baseline_id"], "version": pre["expected_baseline_version"]}:
        raise _bad("Read the current primary baseline before replacing it", "PM_BASELINE_VERSION_CONFLICT", 409)
    if head["baseline_id"] and _access(ops, principal, wid, _record(ops.db, wid, head["baseline_id"]), decide=True) >= MAX_ANCESTORS:
        raise _bad("Baseline source history cannot be extended", "PM_BASELINE_HISTORY_LIMIT", 409)
    config = sources.configured(ops, repository)
    if repository != config["repository"]:
        raise _bad("Use the configured repository spelling")
    project = _project(ops, wid, params["project_binding_id"], repository, active=True)
    source = params["source"]
    if (not isinstance(source, dict) or not isinstance(source.get("kind"), str)
            or source["kind"] not in {"github_release", "deployment"}):
        raise _bad("This version supports GitHub Release and verified deployment sources; package registries are unavailable",
                   "PM_BASELINE_SOURCE_UNSUPPORTED")
    if source["kind"] == "github_release":
        _exact(source, {"kind", "release_id"})
        if (type(source["release_id"]) is not int or not 0 < source["release_id"] < 2**63
                or not isinstance(params["channel"], str) or params["channel"] not in {"stable", "prerelease"}):
            raise _bad("Select a native release ID and its explicit stable or prerelease channel")
    else:
        _exact(source, {"kind", "deployment_id"})
        if not isinstance(source["deployment_id"], str) or not re.fullmatch(r"dep_[0-9a-f]{32}", source["deployment_id"]):
            raise _bad("Select an original deployment ID")
        _text(params["channel"], 256, "Deployment environment")
    branch = params["maintenance_branch"]
    if branch is not None:
        sources.ref_name(branch)
    limits = params["known_limitations"]
    if not isinstance(limits, list) or len(limits) > 40:
        raise _bad("Known limitations must be a list of at most 40 explicit claims")
    for limit in limits:
        _text(limit, 2000, "Known limitation")
    _text(params["rationale"], 4000, "Selection rationale")
    intent = {"server_id": workspace["server_id"], "repository": repository, "repository_id": pre["expected_repository_id"],
              "commit_sha": pre["expected_commit_sha"], "config": config, "project_binding": project,
              "source": source, "channel": params["channel"], "maintenance_branch": branch,
              "known_limitations": limits, "rationale": params["rationale"],
              "previous_baseline_id": head["baseline_id"], "previous_version": head["version"], "deployment_receipt": None}
    if source["kind"] == "deployment":
        proof = sources.deployment_receipt(ops, principal, wid, source["deployment_id"], repository=repository,
            repository_id=intent["repository_id"], commit_sha=intent["commit_sha"], channel=intent["channel"])
        if proof["provider_digest"] != config["provider_digest"]:
            raise _bad("The deployment belongs to another configured provider", "PM_BASELINE_REPOSITORY_CHANGED", 409)
        intent["deployment_receipt"] = proof
    return intent


def _admit(ops, principal, target, params, pre):
    with ops.journal.tx():
        intent = _check(ops, principal, target, params, pre)
    return {"principal": pm_workspace.principal_binding(principal), "release_baseline_intent": intent,
            "release_baseline_intent_digest": sources.digest(intent)}


def operation_visible(db, principal, operation):
    with _snapshot(db):
        return _operation_visible(db, principal, operation)


def _operation_visible(db, principal, operation):
    row = db.execute("SELECT * FROM operations WHERE operation_id=?", (operation.get("operation_id"),)).fetchone()
    if row is None or row["action"] != ACTION:
        return False
    op = OperationService._decode(row)
    binding = (op.get("external_refs") or {}).get("admission_binding") or {}
    intent = binding.get("release_baseline_intent")
    try:
        if sources.digest(intent) != binding.get("release_baseline_intent_digest"):
            return False
        reader = _Reader(db)
        wid = op["target"]["workspace_id"]
        _access_context(reader, principal, wid, intent)
        if intent["previous_baseline_id"]:
            _access(reader, principal, wid, _record(db, wid, intent["previous_baseline_id"]))
        return True
    except (OperationError, ValueError, TypeError, KeyError):
        return False


def _authorize_existing(ops, principal, op, verb):
    wid = op["target"]["workspace_id"]
    _member(ops, principal, wid, decide=True)
    binding = (op.get("external_refs") or {}).get("admission_binding") or {}
    intent = binding.get("release_baseline_intent")
    if not isinstance(intent, dict) or sources.digest(intent) != binding.get("release_baseline_intent_digest"):
        raise _bad("The original source intent is unavailable", "NOT_FOUND", 404)
    _access_context(ops, principal, wid, intent, decide=True)
    if intent["previous_baseline_id"]:
        _access(ops, principal, wid, _record(ops.db, wid, intent["previous_baseline_id"]), decide=True)


def _current(ctx, principal):
    ctx.check_cancel()
    from . import pm_maintenance_repairs
    pm_maintenance_repairs.require_delivery_child(ctx.service, ctx.op)
    intent = _check(ctx.service, principal, ctx.target, ctx.params, ctx.preconditions)
    binding = ctx.admission_binding or {}
    if intent != binding.get("release_baseline_intent") or sources.digest(intent) != binding.get("release_baseline_intent_digest"):
        raise _bad("The original fixed source context changed", "PM_BASELINE_CONTEXT_CHANGED", 409)
    return intent


async def _run(ctx):
    saved = ctx.service.db.execute("SELECT response FROM operation_steps WHERE operation_id=? AND name='pm.baseline.record' AND status='succeeded'",
                                   (ctx.operation_id,)).fetchone()
    if saved:
        return json.loads(saved[0])  # Original local receipt recovery precedes current authority/lifecycle checks.
    principal = pm_workspace.admitted_principal(ctx)
    try:
        intent = _current(ctx, principal)
    except (Cancelled, OperationError):
        # This named step has only fixed GETs. If its original read was interrupted,
        # authority/cancel loss proves there is no external mutation left to settle.
        # Keep failed evidence instead of leaving an indefinite unknown investment.
        row = ctx.service.db.execute("SELECT status FROM operation_steps WHERE operation_id=? AND name='pm.baseline.sources'",
                                     (ctx.operation_id,)).fetchone()
        if row and row[0] in {"started", "uncertain"}:
            ctx.service._step_status(ctx.operation_id, "pm.baseline.sources", "failed",
                error={"code": "PM_BASELINE_READ_STOPPED", "message": "Original read-only observation stopped without a baseline selection"})
        raise

    async def reread(_):
        return RERUN  # Bounded GETs have no external mutation; still subject to current epoch before rerun.

    async def read():
        try:
            from . import pm_maintenance_repair_baseline
            return await pm_maintenance_repair_baseline.bounded_read(ctx,
                lambda: sources.observe(ctx.service, intent, lambda: _current(ctx, principal)))
        except Cancelled:
            raise OperationError("CANCELLED", "Stopped release source reads before baseline selection", 409) from None

    try:
        proof = await ctx.step("pm.baseline.sources", read, request={"intent_digest": sources.digest(intent)}, reconcile=reread)
    except StepFailed:
        ctx.check_cancel()
        raise

    def record():
        current = _current(ctx, principal)
        if proof["intent_digest"] != sources.digest(current):
            raise _bad("The saved source observation does not match the original intent", "PM_BASELINE_SOURCE_UNPROVEN", 409)
        kind = current["source"]["kind"]
        now = time.time()
        bid = "pmrb_" + ctx.operation_id[3:]
        document = {"schema_version": 1, "baseline_id": bid, "workspace_id": ctx.target["workspace_id"],
                    "version": current["previous_version"] + 1, "previous_baseline_id": current["previous_baseline_id"],
                    "operation_id": ctx.operation_id, "intent": current, "proof": proof,
                    "release_version": {"kind": "tag", "value": proof["source"]["tag"]} if kind == "github_release"
                        else {"kind": "commit", "value": current["commit_sha"]},
                    "commit_sha": current["commit_sha"], "repository": current["repository"],
                    "repository_id": current["repository_id"], "channel": current["channel"],
                    "delivery_status": {"github_release": "published" if kind == "github_release" else "not_assessed",
                        "package_registry": "not_assessed", "deployment": "verified" if kind == "deployment" else "not_assessed"},
                    "quality": {"status": "unassessed", "functional_acceptance_verified": False,
                        "known_limitations": [{"text": text, "status": "owner_claim"} for text in current["known_limitations"]]},
                    "maintenance_authorized": False, "created_at": now, "created_by": ctx.actor}
        _bounded(document)
        doc_digest = sources.digest(document)
        ctx.service.db.execute("""INSERT INTO pm_release_baselines(baseline_id,workspace_id,version,previous_baseline_id,
            document,document_digest,actor,created_at,operation_id) VALUES(?,?,?,?,?,?,?,?,?)""",
            (bid, document["workspace_id"], document["version"], document["previous_baseline_id"], _json(document), doc_digest,
             ctx.actor, now, ctx.operation_id))
        result = {"workspace_id": document["workspace_id"], "baseline_id": bid, "version": document["version"],
                  "previous_baseline_id": document["previous_baseline_id"], "document_digest": doc_digest,
                  "maintenance_authorized": False}
        ctx.service.journal.api_event("pm_release_baseline", bid, "pm.release_baseline.registered",
                                      {**result, "operation_id": ctx.operation_id}, actor=ctx.actor)
        return result

    return ctx.effect("pm.baseline.record", record, request={"proof_digest": sources.digest(proof)})


def _view(row):
    return {key: row[key] for key in ("sequence", "baseline_id", "workspace_id", "version", "previous_baseline_id",
                                    "document", "document_digest", "actor", "created_at", "operation_id")}


@_snapshot_read
def get_baseline(ops, principal, workspace_id, baseline_id):
    _member(ops, principal, workspace_id)
    row = _record(ops.db, workspace_id, baseline_id)
    _access(ops, principal, workspace_id, row)
    return _bounded({"workspace_id": workspace_id, "baseline": _view(row)})


@_snapshot_read
def current_baseline(ops, principal, workspace_id):
    _member(ops, principal, workspace_id)
    head = _head(ops.db, workspace_id)
    baseline, filtered = None, False
    if head["baseline_id"]:
        try:
            baseline = get_baseline(ops, principal, workspace_id, head["baseline_id"])["baseline"]
        except OperationError as exc:
            if exc.status not in {403, 404}:
                raise
            filtered = True
    return _bounded({"workspace_id": workspace_id, "head": {**head, "baseline_id": None if filtered else head["baseline_id"]},
                     "baseline": baseline, "visibility_filtered": filtered, "maintenance_available": False})


@_snapshot_read
def list_baselines(ops, principal, workspace_id, *, after=0, limit=20):
    _member(ops, principal, workspace_id)
    if type(after) is not int or not 0 <= after <= 2**63 - 1 or type(limit) is not int or not 1 <= limit <= 100:
        raise _bad("Use a nonnegative bounded cursor and limit 1–100")
    result = {"workspace_id": workspace_id, "baselines": [], "next_cursor": None, "visibility_filtered": False}
    rows = ops.db.execute("SELECT baseline_id,sequence FROM pm_release_baselines WHERE workspace_id=? AND sequence>? ORDER BY sequence LIMIT ?",
                          (workspace_id, after, limit + 1)).fetchall()
    consumed = after
    for stub in rows[:limit]:
        row = _record(ops.db, workspace_id, stub["baseline_id"])
        try:
            _access(ops, principal, workspace_id, row)
        except OperationError as exc:
            if exc.status not in {403, 404}:
                raise
            result["visibility_filtered"] = True
            consumed = stub["sequence"]
            continue
        candidate = {**result, "baselines": [*result["baselines"], _view(row)], "next_cursor": stub["sequence"]}
        if _size(candidate) > MAX_READ_BYTES:
            if consumed == after:
                raise _bad("One complete baseline exceeds the page byte limit", "PM_BASELINE_READ_TOO_LARGE", 413)
            result["next_cursor"] = consumed
            return _bounded(result)
        result["baselines"].append(_view(row))
        consumed = stub["sequence"]
    result["next_cursor"] = consumed if len(rows) > limit else None
    return _bounded(result)
