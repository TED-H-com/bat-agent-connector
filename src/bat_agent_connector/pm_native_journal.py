"""Original native PM evidence, separate from BAT replay and terminal semantics."""
from __future__ import annotations

import json
import re
import time
import uuid

from . import pm_discussions, pm_runs, pm_workspace
from .operations import OperationError

ADAPTER = "codex-native-linux"
KINDS = {"launch_intent", "process_started", "preflight", "send_intent", "turn_assessment",
         "process_exited", "process_exit_unknown", "invalid_native_event", "process_not_started", "process_reconciled_dead"}
DEAD_PROOFS = {"host_rebooted", "original_pid_absent", "pid_reused", "original_process_exited"}


def _bad(message, code="PM_NATIVE_EVIDENCE_INVALID"):
    return OperationError(code, message, 409)


def schema(journal):
    pm_runs.schema(journal)
    with journal.tx():
        journal.db.execute("""CREATE TABLE IF NOT EXISTS pm_native_lifecycle (
            sequence INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL REFERENCES pm_runs(run_id),
            kind TEXT NOT NULL, data TEXT NOT NULL, digest TEXT NOT NULL, created_at REAL NOT NULL,
            UNIQUE(run_id,kind))""")
        journal.db.execute("""CREATE TABLE IF NOT EXISTS pm_native_events (
            run_id TEXT NOT NULL REFERENCES pm_runs(run_id), native_seq INTEGER NOT NULL,
            envelope TEXT NOT NULL, digest TEXT NOT NULL, created_at REAL NOT NULL,
            PRIMARY KEY(run_id,native_seq))""")
        for table in ("pm_native_lifecycle", "pm_native_events"):
            for verb in ("UPDATE", "DELETE"):
                journal.db.execute(f"CREATE TRIGGER IF NOT EXISTS {table}_{verb.lower()}_immutable BEFORE {verb} ON {table} "
                                   "BEGIN SELECT RAISE(ABORT, 'Native PM evidence is immutable'); END")


def lifecycle(db, run_id):
    return {row["kind"]: json.loads(row["data"]) for row in db.execute(
        "SELECT kind,data FROM pm_native_lifecycle WHERE run_id=? ORDER BY sequence", (run_id,))}


def events(db, run_id):
    return [json.loads(row[0]) for row in db.execute(
        "SELECT envelope FROM pm_native_events WHERE run_id=? ORDER BY native_seq", (run_id,))]


def _active(journal, run_id, generation):
    run = pm_runs._active(journal.db, run_id, generation)
    if run["adapter"] != ADAPTER:
        raise _bad("This run does not own the native PM adapter")
    return run


def _process(value):
    if (not isinstance(value, dict) or set(value) != {"pid", "start_ticks", "boot_id_digest"}
            or type(value["pid"]) is not int or value["pid"] <= 0
            or type(value["start_ticks"]) is not int or value["start_ticks"] < 0
            or not isinstance(value["boot_id_digest"], str) or not re.fullmatch(r"[0-9a-f]{64}", value["boot_id_digest"])):
        raise _bad("The original native process identity is required")
    return value


def record(journal, run_id, generation, value):
    """Persist a native adapter fact without rewriting its keys or payload."""
    if not isinstance(value, dict) or value.get("run_id") != run_id or value.get("kind") not in KINDS:
        raise _bad("Native lifecycle evidence belongs to another run")
    encoded = pm_runs._json(value)
    if len(encoded.encode()) > 2 * 1024 * 1024:
        raise _bad("Native lifecycle evidence exceeds its bound")
    with journal.tx():
        old = journal.db.execute("SELECT data FROM pm_native_lifecycle WHERE run_id=? AND kind=?", (run_id, value["kind"])).fetchone()
        if old:
            if old[0] != encoded or pm_runs.get(journal.db, run_id)["generation"] != generation:
                raise _bad("Original native lifecycle evidence changed", "PM_NATIVE_EVIDENCE_CONFLICT")
            return value
        run = _active(journal, run_id, generation)
        saved = lifecycle(journal.db, run_id)
        if value["kind"] != "launch_intent" and "launch_intent" not in saved:
            raise _bad("Native evidence needs the original launch intent")
        if "process" in value:
            identity = _process(value["process"])
            original = saved.get("process_started", saved.get("send_intent", saved.get("process_exit_unknown", {}))).get("process")
            if original is not None and identity != original:
                raise _bad("Native evidence names a different process")
        if value["kind"] in {"process_started", "process_exited", "process_exit_unknown", "process_reconciled_dead", "send_intent"}:
            _process(value.get("process"))
        if value["kind"] == "process_not_started" and (
                value.get("process_launch_attempted") is not False or "process_started" in saved or "send_intent" in saved):
            raise _bad("An attempted native process cannot be reported as never started")
        if value["kind"] == "process_reconciled_dead" and (
                value.get("state") != "dead" or value.get("proof") not in DEAD_PROOFS
                or not any(kind in saved for kind in ("process_started", "send_intent", "process_exit_unknown"))):
            raise _bad("Process recovery must prove the original process has exited")
        if value["kind"] == "launch_intent":
            pm_runs.require_requester(journal.db, run)
            if value.get("context_digest") != run["context_digest"]:
                raise _bad("Native launch does not bind the original context")
        if value["kind"] == "send_intent":
            pm_runs.require_requester(journal.db, run)
            launch = saved["launch_intent"]
            capability = saved.get("preflight", {}).get("capability")
            if (not capability or value.get("capability") != capability
                    or value.get("context_digest") != run["context_digest"]
                    or value.get("prompt_digest") != launch["prompt_digest"]
                    or value.get("thread_id") != capability.get("thread_id")
                    or value.get("process") != saved.get("process_started", {}).get("process")
                    or not isinstance(value.get("client_message_id"), str) or not value["client_message_id"]
                    or type(value.get("request_id")) is not int or value["request_id"] < 1):
                raise _bad("Native send does not match the fixed context and process")
        journal.db.execute("INSERT INTO pm_native_lifecycle(run_id,kind,data,digest,created_at) VALUES(?,?,?,?,?)",
                           (run_id, value["kind"], encoded, pm_runs._digest(value), time.time()))
        if value["kind"] == "send_intent":
            journal.db.execute("UPDATE pm_run_state SET status='running',updated_at=? WHERE run_id=?", (time.time(), run_id))
        return value


def record_event(journal, run_id, generation, envelope):
    if (not isinstance(envelope, dict) or set(envelope) != {"run_id", "seq", "event"}
            or envelope["run_id"] != run_id or type(envelope["seq"]) is not int
            or envelope["seq"] < 1 or not isinstance(envelope["event"], dict)):
        raise _bad("Native event envelope is invalid")
    encoded = pm_runs._json(envelope)
    if len(encoded.encode()) > 1024 * 1024:
        raise _bad("Native event exceeds its bound")
    with journal.tx():
        old = journal.db.execute("SELECT envelope FROM pm_native_events WHERE run_id=? AND native_seq=?", (run_id, envelope["seq"])).fetchone()
        if old:
            if old[0] != encoded or pm_runs.get(journal.db, run_id)["generation"] != generation:
                raise _bad("Original native event changed", "PM_NATIVE_EVIDENCE_CONFLICT")
            return envelope
        _active(journal, run_id, generation)
        if "send_intent" not in lifecycle(journal.db, run_id):
            raise _bad("Native event has no original durable send intent")
        count, size = journal.db.execute("SELECT COUNT(*),COALESCE(SUM(length(CAST(envelope AS BLOB))),0) FROM pm_native_events WHERE run_id=?",
                                        (run_id,)).fetchone()
        if envelope["seq"] != count + 1 or count >= 10000 or size + len(encoded.encode()) > 8 * 1024 * 1024:
            raise _bad("Native event stream has a gap or exceeds its bound")
        journal.db.execute("INSERT INTO pm_native_events VALUES(?,?,?,?,?)",
                           (run_id, envelope["seq"], encoded, pm_runs._digest(envelope), time.time()))
        return envelope


def death_proof(db, run_id):
    saved = lifecycle(db, run_id)
    absent = saved.get("process_not_started")
    if absent and absent.get("process_launch_attempted") is False and "process_started" not in saved and "send_intent" not in saved:
        return absent
    ended = saved.get("process_exited")
    if (ended and ended.get("process_death_confirmed") is True and "process" in ended
            and type(ended.get("exit_code")) is int):
        return ended
    observed = saved.get("process_reconciled_dead")
    if (observed and observed.get("state") == "dead" and observed.get("proof") in DEAD_PROOFS):
        return observed
    return None


def assessment(db, run_id):
    from .pm_native_runtime import assess_turn
    saved = lifecycle(db, run_id)
    sent = saved.get("send_intent")
    original = saved.get("turn_assessment", {}).get("assessment")
    values = events(db, run_id)
    if not sent or not original:
        return {"complete": False, "reason": "native_completion_unproven"}
    if any(item["event"].get("id") != sent["request_id"] for item in values if "id" in item["event"]):
        return {"complete": False, "reason": "native_request_mismatch"}
    observed = assess_turn(values, run_id=run_id, thread_id=sent["thread_id"], prompt_digest=sent["prompt_digest"],
                           client_message_id=sent["client_message_id"], actual_model=sent["capability"]["actual_model"])
    ended = saved.get("process_exited")
    if original != observed or ended and ended.get("native_event_count") != len(values):
        return {"complete": False, "reason": "native_stream_incomplete"}
    return observed


def abort(journal, run_id, generation, *, reason, code="PM_NATIVE_INCOMPLETE", cancelled=False):
    """Release only after proof that the original process cannot continue."""
    with journal.tx():
        run = pm_runs.get(journal.db, run_id)
        if run["generation"] != generation:
            raise _bad("PM writer changed", "PM_WRITER_CHANGED")
        if run["state"] in pm_runs.TERMINAL:
            return run
        run = _active(journal, run_id, generation)
        proof = death_proof(journal.db, run_id)
        if not proof:
            raise _bad("Original native process death is not proven", "PM_NATIVE_PROCESS_UNKNOWN")
        outcome = {"reason": reason, "code": code, "cancelled": cancelled, "process_proof": proof,
                   "native_assessment": assessment(journal.db, run_id), "assistant_imported": False}
        pm_runs._release(journal.db, run, "aborted", outcome)
        return pm_runs.get(journal.db, run_id)


def import_reply(journal, run_id, generation):
    """Recompute native completion; never route this evidence through BAT's parser."""
    with journal.tx():
        run = pm_runs.get(journal.db, run_id)
        if run["generation"] != generation:
            raise _bad("PM writer changed", "PM_WRITER_CHANGED")
        if run["state"] == "completed":
            return run
        run = _active(journal, run_id, generation)
        pm_runs.require_requester(journal.db, run)
        proof = death_proof(journal.db, run_id)
        observed = assessment(journal.db, run_id)
        if not proof or not observed.get("complete"):
            raise _bad("Native completion and process death must both be proven", "PM_COMPLETION_UNPROVEN")
        saved = lifecycle(journal.db, run_id)
        sent = saved["send_intent"]
        content = pm_discussions._text(observed["content"], "assistant content", 128000)
        message_id = "pmm_" + uuid.uuid5(uuid.UUID(pm_workspace.server_id(journal.db)), run_id + ":assistant").hex
        now = time.time()
        journal.db.execute("INSERT INTO pm_messages(message_id,workspace_id,thread_id,actor,content,created_at,operation_id) VALUES(?,?,?,?,?,?,?)",
                           (message_id, run["workspace_id"], run["thread_id"], run["execution_authority"]["actor"], content, now, run["operation_id"]))
        source_refs = {"adapter": ADAPTER, "contract": sent["capability"]["contract"], "run_id": run_id,
                       "context_digest": run["context_digest"], "prompt_digest": sent["prompt_digest"],
                       "native_thread_id": sent["thread_id"], "native_event_seqs": observed["source_event_seqs"],
                       "event_digests": [pm_runs._digest(value) for value in events(journal.db, run_id)],
                       "process": sent["process"], "process_proof": proof, "client_message_id": sent["client_message_id"],
                       "request_id": sent["request_id"], "config_digest": sent["capability"]["config_digest"],
                       "coverage": run["context"]["coverage"],
                       "input_ids": [value["input_id"] for group in run["context"]["inputs_by_status"].values() for value in group],
                       "message_ids": [value["message_id"] for value in run["context"].get("messages", [])]}
        journal.db.execute("INSERT INTO pm_assistant_provenance VALUES(?,?,?,?,?,?,?,?,?,?)",
                           (message_id, run_id, observed["native_turn_id"], observed["native_message_id"], sent["thread_id"],
                            observed["actual_model"], "complete", pm_runs._json(source_refs), run["context_digest"], now))
        journal.db.execute("UPDATE pm_threads SET version=version+1,updated_at=? WHERE thread_id=?", (now, run["thread_id"]))
        outcome = {"message_id": message_id, "native_turn_id": observed["native_turn_id"],
                   "native_thread_id": sent["thread_id"], "actual_model": observed["actual_model"], "completeness": "complete"}
        pm_runs.receipt(journal, run_id, generation, "import", "confirmed", outcome)
        pm_runs._release(journal.db, run, "completed", outcome)
        journal.api_event("pm_thread", run["thread_id"], "pm.run.imported",
                          {"workspace_id": run["workspace_id"], "thread_id": run["thread_id"], "run_id": run_id,
                           "message_id": message_id, "operation_id": run["operation_id"]}, actor=run["execution_authority"]["actor"])
        return pm_runs.get(journal.db, run_id)
