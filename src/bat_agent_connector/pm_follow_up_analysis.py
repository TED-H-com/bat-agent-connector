"""One sourced Follow-up assessment, using the existing native PM operation."""
from __future__ import annotations

import hashlib
import json
import re
import time

from . import (
    pm_discussions,
    pm_follow_up,
    pm_follow_up_format,
    pm_native_journal,
    pm_native_operations,
    pm_runs,
    pm_sprints,
    pm_workspace,
)
from .operations import ActionDef, OperationError, OperationService

ACTION = "pm.follow_up.analyze"
MAX_READ_BYTES = 2_000_000
_ID = re.compile(r"pmfa_[0-9a-f]{32}")
_DIGEST = re.compile(r"[0-9a-f]{64}")


def _error(message, code="PM_FOLLOW_UP_ANALYSIS_INVALID", status=422):
    return OperationError(code, message, status)


def schema(journal):
    from . import pm_bat_runtime
    pm_bat_runtime.schema(journal)
    pm_follow_up.schema(journal)
    pm_native_journal.schema(journal)
    with journal.tx():
        journal.db.execute("""CREATE TABLE IF NOT EXISTS pm_follow_up_assessments (
            sequence INTEGER PRIMARY KEY AUTOINCREMENT, assessment_id TEXT NOT NULL UNIQUE,
            workspace_id TEXT NOT NULL, thread_id TEXT NOT NULL, operation_id TEXT NOT NULL UNIQUE,
            run_id TEXT NOT NULL UNIQUE REFERENCES pm_runs(run_id), intake_id TEXT NOT NULL,
            intake_digest TEXT NOT NULL, context_digest TEXT NOT NULL,
            raw_reply TEXT NOT NULL, raw_reply_digest TEXT NOT NULL, document TEXT, document_digest TEXT,
            validation TEXT NOT NULL, provenance TEXT NOT NULL, created_by TEXT NOT NULL, created_at REAL NOT NULL)""")
        journal.db.execute("CREATE INDEX IF NOT EXISTS pm_follow_up_assessments_workspace ON pm_follow_up_assessments(workspace_id,sequence)")
        for verb in ("UPDATE", "DELETE"):
            journal.db.execute(f"CREATE TRIGGER IF NOT EXISTS pm_follow_up_assessments_{verb.lower()}_immutable "
                              f"BEFORE {verb} ON pm_follow_up_assessments BEGIN SELECT RAISE(ABORT,'PM assessment is immutable'); END")


def install(ops):
    schema(ops.journal)
    if ACTION not in ops.actions:
        ops.register(ActionDef(ACTION, "manage", "Analyze one fixed Follow-up intake without adopting or executing work",
                               _run, _admit, ("workspace_id", "thread_id"), authorize_existing=_authorize_existing))


def _selection(ops, principal, target, params, pre):
    from .pm_analysis_adapters import ordinary_params
    pm_native_operations._scopes(principal)
    if (set(target) != {"workspace_id", "thread_id"}
            or set(params) - {"intake_id", "purpose", "model", "max_chars", "timeout_s", "adapter"}
            or set(pre) != {"expected_version", "expected_intake_digest"}):
        raise _error("Supply exactly a workspace, thread, fixed intake, bounded purpose and original digest/version")
    # Reuse the ordinary analysis budget and caller contract; the thread mode is
    # unchanged, and no caller-supplied profile, path or runtime setting is accepted.
    pm_native_operations._values(ops, principal, target,
        ordinary_params(params), {"expected_version": pre["expected_version"]})
    thread = pm_discussions._thread(ops.db, target["workspace_id"], target["thread_id"])
    if thread["mode"] not in {"idea", "planning"}:
        raise _error("Select an existing Idea or Planning discussion without changing its mode", "PM_MODE_REFUSED", 409)
    digest = pre["expected_intake_digest"]
    if not isinstance(digest, str) or not _DIGEST.fullmatch(digest):
        raise _error("A complete original intake digest is required")
    intake = pm_follow_up.get_intake(ops, principal, target["workspace_id"], params.get("intake_id"))["intake"]
    if intake["document_digest"] != digest:
        raise _error("The selected intake digest differs", "VERSION_CONFLICT", 409)
    source_thread = intake["document"].get("thread_ref")
    if source_thread and source_thread["thread_id"] != target["thread_id"]:
        raise _error("This intake belongs to another selected discussion", "PM_FOLLOW_UP_THREAD_MISMATCH", 409)
    context = pm_follow_up_format.context(intake, target, pre["expected_version"], params["purpose"], params.get("max_chars", 64000))
    return intake, context


def _admit(ops, principal, target, params, pre):
    from .pm_analysis_adapters import admission
    intake, context = _selection(ops, principal, target, params, pre)
    return {**admission(ops, principal, target, params, pre),
        "follow_up_analysis": {"intake_id": intake["intake_id"], "intake_digest": intake["document_digest"],
            "access_refs": intake["document"]["access_refs"], "context": context, "context_digest": pm_runs._digest(context)}}


def operation_visible(db, principal, operation):
    """Read original admission even when no run/assessment has been committed."""
    try:
        row = db.execute("SELECT * FROM operations WHERE operation_id=?", (operation["operation_id"],)).fetchone()
        if row is None or row["action"] != ACTION or not principal.allows("observe"):
            return False
        op = OperationService._decode(row)
        pm_workspace.require_member(db, principal, op["target"]["workspace_id"])
        binding = ((op.get("external_refs") or {}).get("admission_binding") or {}).get("follow_up_analysis")
        if not isinstance(binding, dict):
            return False
        intake = pm_follow_up._row(db, op["target"]["workspace_id"], binding["intake_id"])
        return (binding["intake_id"] == op["params"]["intake_id"]
            and binding["intake_digest"] == intake["document_digest"] == op["preconditions"]["expected_intake_digest"]
            and binding["access_refs"] == intake["document"]["access_refs"]
            and pm_follow_up._visible(db, principal, op["target"]["workspace_id"], pm_follow_up._original_access(db, intake)))
    except (OperationError, KeyError, TypeError, ValueError):
        return False


def _require_visible(db, principal, operation):
    if not operation_visible(db, principal, operation):
        raise _error("Original Follow-up analysis sources are unavailable", "PM_FOLLOW_UP_SOURCE_UNAVAILABLE", 404)


def _authorize_existing(ops, principal, op, verb):
    pm_native_operations._scopes(principal)
    pm_workspace.require_member(ops.db, principal, op["target"]["workspace_id"], write=True)
    if verb != "replay" and principal.actor != op["actor"]:
        raise _error("Only the original requester controls this analysis", "FORBIDDEN", 403)
    _require_visible(ops.db, principal, op)


def _freeze(ctx, principal, authority):
    from . import pm_analysis_adapters
    _require_visible(ctx.service.db, principal, {"operation_id": ctx.operation_id})
    # Recheck permission and literal request version, but never rebuild the
    # admitted profile/context from current files, later answers or latest heads.
    pm_native_operations._values(ctx.service, principal, ctx.target,
        pm_analysis_adapters.ordinary_params(ctx.params), {"expected_version": ctx.preconditions["expected_version"]})
    binding = ctx.admission_binding["follow_up_analysis"]
    context = binding["context"]
    if pm_runs._digest(context) != binding["context_digest"]:
        raise _error("Original analysis context cannot be verified", "PM_FOLLOW_UP_ANALYSIS_UNAVAILABLE", 409)
    run = pm_runs.create(ctx.service, principal, operation_id=ctx.operation_id, **ctx.target,
        expected_version=ctx.preconditions["expected_version"], adapter=pm_analysis_adapters.selection(ctx.params),
        purpose=ctx.params["purpose"], execution_authority=authority, max_chars=ctx.params.get("max_chars", 64000),
        _context_factory=lambda: context)
    return pm_analysis_adapters.prepare(ctx, run, pm_follow_up_format.prompt(run))


async def _run(ctx):
    from .pm_analysis_adapters import run_analysis
    return await run_analysis(ctx, prepare=_freeze, import_reply=_import)


def _checked(value):
    if pm_sprints._read_size(value) > MAX_READ_BYTES:
        raise _error("A complete assessment response must fit 2,000,000 UTF-8 bytes", "PM_FOLLOW_UP_ANALYSIS_TOO_LARGE", 413)
    return value


def _public_proof(proof):
    from .pm_bat_runtime import ADAPTER
    if proof.get('adapter') == ADAPTER:
        # The verifier projects nested BAT facts. Keep an explicit outer schema
        # as well; future journal diagnostics are never public by default.
        fields = ('adapter', 'contract', 'run_id', 'context_digest', 'prompt_digest', 'native_thread_id',
            'client_message_id', 'send_cursor', 'observed_cursor', 'native_event_seqs', 'terminal', 'bat_runtime', 'coverage')
        return {key: proof[key] for key in fields}
    # Keep the original raw lifecycle in the private journal. The public proof
    # has an explicit schema, so future runtime diagnostic fields cannot expose
    # a configuration path, environment, stderr or a private spool location.
    fields = {"kind", "run_id", "process", "state", "proof", "observed_at", "exit_code", "signal",
              "termination", "sent", "native_event_count", "stderr_bytes", "process_death_confirmed",
              "process_launch_attempted"}
    return {**proof, "process_proof": {key: value for key, value in proof["process_proof"].items() if key in fields}}


def _import(journal, run_id, generation):
    from .pm_analysis_adapters import verified_reply
    with journal.tx():
        run = pm_runs.get(journal.db, run_id)
        if run["generation"] != generation or run["context"].get("output_destination") != pm_follow_up_format.DESTINATION:
            raise _error("The original assessment writer and destination are required", "PM_OUTPUT_DESTINATION_REFUSED", 409)
        if run["state"] == "completed":
            row = journal.db.execute("SELECT 1 FROM pm_follow_up_assessments WHERE run_id=?", (run_id,)).fetchone()
            if not row:
                raise _error("Original assessment receipt is missing", "PM_FOLLOW_UP_ANALYSIS_UNAVAILABLE", 409)
            return run  # Internal committed receipt recovery survives later source withdrawal.
        run, observed, proof = verified_reply(journal, run_id, generation)
        raw = pm_discussions._text(observed["content"], "assistant content", pm_follow_up_format.MAX_REPLY_CHARS)
        context = run["context"]
        document, validation = pm_follow_up_format.validate(raw, context["source_references"])
        provenance = {**_public_proof(proof), "native_turn_id": observed["native_turn_id"], "native_message_id": observed["native_message_id"],
            "actual_model": observed["actual_model"], "completeness": "complete", "intake_id": context["intake_id"],
            "intake_digest": context["intake_digest"], "source_references": context["source_references"],
            "profile": context["profile"], "output_contract_version": pm_follow_up_format.VERSION}
        value = {"assessment_id": "pmfa_" + run["operation_id"][3:], "workspace_id": run["workspace_id"],
            "thread_id": run["thread_id"], "operation_id": run["operation_id"], "run_id": run_id,
            "intake_id": context["intake_id"], "intake_digest": context["intake_digest"], "context_digest": run["context_digest"],
            "raw_reply": raw, "raw_reply_digest": hashlib.sha256(raw.encode()).hexdigest(), "document": document,
            "document_digest": pm_runs._digest(document) if document is not None else None, "validation": validation,
            "provenance": provenance, "created_by": run["execution_authority"]["actor"], "created_at": time.time()}
        _checked({"version": 1, "workspace_id": run["workspace_id"],
                  "assessment": {"sequence": 9223372036854775807, **value, **_semantics()}})
        columns = tuple(value)
        encoded = {key: pm_runs._json(value[key]) if key in {"document", "validation", "provenance"} and value[key] is not None
                   else value[key] for key in columns}
        journal.db.execute(f"INSERT INTO pm_follow_up_assessments({','.join(columns)}) VALUES({','.join('?' for _ in columns)})",  # noqa: S608 - fixed internal columns
                           tuple(encoded[key] for key in columns))
        outcome = {"assessment_id": value["assessment_id"], "intake_id": value["intake_id"],
            "validation_status": validation["status"], "conclusions_verified": False, "assessment_adopted": False,
            "assistant_imported": False, "native_turn_id": observed["native_turn_id"], "actual_model": observed["actual_model"]}
        pm_runs.receipt(journal, run_id, generation, "import", "confirmed", outcome)
        pm_runs._release(journal.db, run, "completed", outcome)
        journal.api_event("pm_follow_up_assessment", value["assessment_id"], ACTION,
            {"workspace_id": run["workspace_id"], "thread_id": run["thread_id"], "run_id": run_id,
             "assessment_id": value["assessment_id"], "operation_id": run["operation_id"]}, actor=run["execution_authority"]["actor"])
        return pm_runs.get(journal.db, run_id)


def _semantics():
    return {"assessment_basis": "unadopted_agent_analysis", "conclusions_verified": False,
            "accepted_plan_changed": False, "execution_authorized": False}


def _row(db, workspace_id, assessment_id):
    if not isinstance(assessment_id, str) or not _ID.fullmatch(assessment_id):
        raise _error("Assessment not found", "PM_FOLLOW_UP_ANALYSIS_NOT_FOUND", 404)
    row = db.execute("SELECT * FROM pm_follow_up_assessments WHERE workspace_id=? AND assessment_id=?", (workspace_id, assessment_id)).fetchone()
    if row is None:
        raise _error("Assessment not found", "PM_FOLLOW_UP_ANALYSIS_NOT_FOUND", 404)
    return row


def _view(row):
    value = dict(row)
    for key in ("document", "validation", "provenance"):
        value[key] = json.loads(value[key]) if value[key] is not None else None
    if (hashlib.sha256(value["raw_reply"].encode()).hexdigest() != value["raw_reply_digest"]
            or (pm_runs._digest(value["document"]) if value["document"] is not None else None) != value["document_digest"]):
        raise _error("Original assessment cannot be verified", "PM_FOLLOW_UP_ANALYSIS_UNAVAILABLE", 409)
    return {**value, **_semantics()}


def get_assessment(ops, principal, workspace_id, assessment_id):
    with ops.journal.tx():
        pm_discussions._read(ops, principal, workspace_id)
        row = _row(ops.db, workspace_id, assessment_id)
        _require_visible(ops.db, principal, row)
        return _checked({"version": 1, "workspace_id": workspace_id, "assessment": _view(row)})


def list_assessments(ops, principal, workspace_id, *, intake_id=None, after=0, limit=50):
    with ops.journal.tx():
        pm_discussions._read(ops, principal, workspace_id)
        pm_discussions._limit(limit)
        if type(after) is not int or not 0 <= after <= 2**63 - 1:
            raise _error("after must be a nonnegative 64-bit insertion sequence")
        if intake_id is not None:
            pm_follow_up.get_intake(ops, principal, workspace_id, intake_id)
        rows = ops.db.execute("""SELECT * FROM pm_follow_up_assessments WHERE workspace_id=? AND sequence>?
            AND (? IS NULL OR intake_id=?) ORDER BY sequence LIMIT ?""", (workspace_id, after, intake_id, intake_id, limit + 1)).fetchall()
        result = {"version": 1, "workspace_id": workspace_id, "assessments": [], "next_cursor": None,
                  "coverage": {"visibility_filtered": False, "scanned": 0, "max_bytes": MAX_READ_BYTES}}
        cursor = after
        for row in rows[:limit]:
            if not operation_visible(ops.db, principal, row):
                result["coverage"]["visibility_filtered"] = True
            else:
                metadata = {key: row[key] for key in ("sequence", "assessment_id", "workspace_id", "thread_id", "operation_id",
                    "run_id", "intake_id", "intake_digest", "context_digest", "document_digest", "raw_reply_digest", "created_by", "created_at")}
                metadata.update(validation=json.loads(row["validation"]), **_semantics())
                result["assessments"].append(metadata)
            cursor = row["sequence"]
            result["coverage"]["scanned"] += 1
        result["next_cursor"] = cursor if len(rows) > limit else None
        return _checked(result)
