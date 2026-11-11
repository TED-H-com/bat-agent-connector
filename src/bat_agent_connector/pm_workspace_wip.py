"""Advisory workspace policy: immutable decisions, never admission authority."""
from __future__ import annotations

import datetime
import hashlib
import json
import re
import time

from . import pm_evidence, pm_release_baselines, pm_source_lineage, pm_workspace
from .operations import ActionDef, OperationError

ACTION = "pm.wip_policy.save"
READ = "pm_workspace_wip_get"
BASIS = "work_commitments_v1"
MAX_WORKS = 10_000
MAX_RECORDS = 50_000
# One expression covers PM targets and the direct BAT handoff's fixed PM package.
WORKSPACE_SQL = "CASE WHEN action='repository.continue' THEN json_extract(params,'$.pm_package_ref.workspace_id') ELSE json_extract(target,'$.workspace_id') END"
INDEXES = (
    f"CREATE INDEX IF NOT EXISTS pm_wip_operations_workspace ON operations(action,json_extract(target,'$.execution_operation_id'),({WORKSPACE_SQL}),actor)",
    "CREATE INDEX IF NOT EXISTS pm_wip_management_work ON management_applied(json_extract(result,'$.work_item_id'))",
    "CREATE INDEX IF NOT EXISTS pm_wip_commands_task ON commands(task_id,kind)",
)


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _digest(value):
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _bad(message, code="INVALID_PARAMS", status=422):
    return OperationError(code, message, status)


def _exact(value, keys):
    if not isinstance(value, dict) or set(value) != set(keys):
        raise _bad("Use the exact workspace WIP policy fields")


def _text(value, maximum):
    if not isinstance(value, str) or not value.strip() or len(value) > maximum or "\0" in value:
        raise _bad(f"Text must contain 1–{maximum} characters")
    try:
        value.encode("utf-8")
    except UnicodeError:
        raise _bad("Text must be UTF-8") from None


def validate_policy(policy):
    _exact(policy, {"schema_version", "mode", "limit", "counting_basis", "basis"})
    if type(policy["schema_version"]) is not int or policy["schema_version"] != 1 or policy["counting_basis"] != BASIS:
        raise _bad("Select the supported policy version and counting basis")
    mode = policy["mode"]
    if mode not in ("advisory", "disabled"):
        raise _bad("Only advisory and disabled policies are supported", "PM_WIP_MODE_UNSUPPORTED")
    limit = policy["limit"]
    if mode == "disabled":
        if limit is not None:
            raise _bad("Disabled policy requires a null limit")
    elif type(limit) is not int or not 1 <= limit <= 1000:
        raise _bad("Advisory limit must be an integer from 1 to 1000")
    basis = policy["basis"]
    _exact(basis, {"assumptions", "review_on"})
    _text(basis["assumptions"], 2000)
    date = basis["review_on"]
    if date is not None:
        try:
            if not isinstance(date, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date):
                raise ValueError
            datetime.date.fromisoformat(date)
        except ValueError:
            raise _bad("review_on must be null or an ISO date") from None
    encoded = _json(policy)
    if len(encoded.encode("utf-8")) > 16_384:
        raise _bad("Policy exceeds 16 KiB")
    return json.loads(encoded)


def installed(db):
    return pm_source_lineage._table(db, "pm_workspace_wip_policies")


def schema(journal):
    with journal.tx():
        journal.db.execute("""CREATE TABLE IF NOT EXISTS pm_workspace_wip_policies (
            sequence INTEGER PRIMARY KEY AUTOINCREMENT, policy_id TEXT NOT NULL UNIQUE,
            workspace_id TEXT NOT NULL, revision INTEGER NOT NULL,
            previous_digest TEXT, document TEXT NOT NULL, document_digest TEXT NOT NULL,
            actor TEXT NOT NULL, created_at REAL NOT NULL, operation_id TEXT NOT NULL UNIQUE,
            UNIQUE(workspace_id,revision))""")
        for verb in ("UPDATE", "DELETE"):
            journal.db.execute(f"CREATE TRIGGER IF NOT EXISTS pm_workspace_wip_policies_{verb.lower()}_immutable "
                f"BEFORE {verb} ON pm_workspace_wip_policies BEGIN SELECT RAISE(ABORT, 'Workspace WIP policy history is immutable'); END")
        for ddl in INDEXES:
            journal.db.execute(ddl)


def install(ops):
    schema(ops.journal)
    if ACTION not in ops.actions:
        ops.register(ActionDef(ACTION, "manage", "Save advisory workspace WIP policy without changing admission",
                               _run, _admit, ("workspace_id",), authorize_existing=_authorize_existing))


def capabilities(ops):
    from . import pm_kanban
    if ACTION not in ops.actions or not installed(ops.db):
        return None
    return {"version": 1, "read": READ, "action": ACTION, "modes": ["advisory", "disabled"],
            "enforcement": False, "counting_basis": BASIS,
            "bounds": {"works": MAX_WORKS, "contributing_records": MAX_RECORDS,
                       "per_work_history": pm_kanban.MAX_HISTORY}}


def _record(db, wid, revision=None):
    if not installed(db):
        return None
    row = db.execute("SELECT * FROM pm_workspace_wip_policies WHERE workspace_id=? "  # noqa: S608 - fixed SQL fragments
                     + ("AND revision=?" if revision is not None else "ORDER BY revision DESC LIMIT 1"),
                     (wid, revision) if revision is not None else (wid,)).fetchone()
    if row is None:
        return None
    doc = json.loads(row["document"])
    if _digest(doc) != row["document_digest"]:
        raise _bad("Original policy cannot be verified", "PM_WIP_POLICY_CORRUPT", 409)
    return {**dict(row), "document": doc}


def _check(ops, principal, target, params, pre):
    _exact(target, {"workspace_id"})
    _exact(params, {"policy", "rationale"})
    _exact(pre, {"expected_policy_revision", "expected_policy_digest"})
    wid = target["workspace_id"]
    pm_evidence._member(ops, principal, wid, write=True, decide=True)
    revision, digest = pre["expected_policy_revision"], pre["expected_policy_digest"]
    if type(revision) is not int or not 0 <= revision < 2**63 - 1:
        raise _bad("Expected policy revision must be a nonnegative integer")
    if (revision == 0 and digest is not None or revision > 0 and
            (not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest))):
        raise _bad("Supply the original revision and document digest")
    current = _record(ops.db, wid)
    if (revision, digest) != ((current["revision"], current["document_digest"]) if current else (0, None)):
        raise _bad("Read the current workspace WIP policy before saving", "PM_WIP_POLICY_CHANGED", 409)
    _text(params["rationale"], 4000)
    return {"workspace_id": wid, "policy": validate_policy(params["policy"]), "rationale": params["rationale"],
            "previous_revision": revision, "previous_digest": digest}


def _admit(ops, principal, target, params, pre):
    with ops.journal.tx():
        intent = _check(ops, principal, target, params, pre)
    return {"principal": pm_workspace.principal_binding(principal), "wip_policy_intent": intent,
            "wip_policy_intent_digest": _digest(intent)}


def _authorize_existing(ops, principal, op, verb):
    pm_evidence._member(ops, principal, op["target"]["workspace_id"], write=True, decide=verb != "replay")


async def _run(ctx):
    def record():
        intent = _check(ctx.service, pm_workspace.admitted_principal(ctx), ctx.target, ctx.params, ctx.preconditions)
        saved = ctx.admission_binding or {}
        if intent != saved.get("wip_policy_intent") or _digest(intent) != saved.get("wip_policy_intent_digest"):
            raise _bad("Original policy context changed", "PM_WIP_POLICY_CHANGED", 409)
        policy_id, now = "pmwip_" + ctx.operation_id[3:], time.time()
        document = {**intent, "policy_id": policy_id, "revision": intent["previous_revision"] + 1,
                    "operation_id": ctx.operation_id, "actor": ctx.actor, "created_at": now}
        digest = _digest(document)
        ctx.service.db.execute("""INSERT INTO pm_workspace_wip_policies
            (policy_id,workspace_id,revision,previous_digest,document,document_digest,actor,created_at,operation_id)
            VALUES(?,?,?,?,?,?,?,?,?)""", (policy_id, intent["workspace_id"], document["revision"], intent["previous_digest"],
                _json(document), digest, ctx.actor, now, ctx.operation_id))
        result = {"workspace_id": intent["workspace_id"], "policy_id": policy_id,
                  "revision": document["revision"], "document_digest": digest, "admission_checked": False}
        ctx.service.journal.api_event("pm_workspace", intent["workspace_id"], "pm.wip_policy.saved",
                                      {**result, "operation_id": ctx.operation_id}, actor=ctx.actor)
        return result
    return ctx.effect("pm.wip_policy.record", record)


@pm_release_baselines._snapshot_read
def get_wip(ops, principal, workspace_id, *, policy_revision=None):
    from .pm_wip_count import collect
    pm_evidence._member(ops, principal, workspace_id)
    if policy_revision is not None and (type(policy_revision) is not int or not 1 <= policy_revision < 2**63):
        raise _bad("policy_revision must be a positive integer")
    current = _record(ops.db, workspace_id)
    selected = _record(ops.db, workspace_id, policy_revision) if policy_revision is not None else current
    if policy_revision is not None and selected is None:
        raise _bad("Policy revision not found", "NOT_FOUND", 404)
    now = time.time()
    count = collect(ops, principal, workspace_id, now)
    policy = selected["document"]["policy"] if selected else None
    limit = policy["limit"] if policy and policy["mode"] == "advisory" else None
    used = count["used"]
    return {"version": 1, "scope": "workspace_commitments", "workspace_id": workspace_id, "counting_basis": BASIS,
            "policy": selected, "selected_policy_revision": selected["revision"] if selected else 0,
            "effective_policy_revision": current["revision"] if current else 0, "observed_at": now,
            "journal_cursor": ops.journal.api_head(), **count,
            "remaining": max(0, limit - used) if limit is not None and used is not None else None,
            "over_limit": used > limit if limit is not None and used is not None else None, "admission_checked": False}
