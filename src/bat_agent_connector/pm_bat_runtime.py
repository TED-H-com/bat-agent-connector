"""Original dedicated BAT PM process proof, separate from native CLI events.

The authenticated BAT transport is the source of these observations. A profile
label, send ACK or idle session alone never releases the workspace PM writer.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
import time

from . import pm_runs
from .operations import OperationError

ADAPTER = "bat-pm-linux-v1"
PROFILE = "codex-pm-linux-v1"
CODEX_VERSION = "0.160.0"
CODEX_SOURCE = "a956835d020762cb2b570053af06f643a11c0ecc"
MAX_PROOF_BYTES = 1024 * 1024


def _bad():
    return OperationError("PM_BAT_BOUNDARY_UNVERIFIED", "The original dedicated BAT PM runtime is unproven", 409)


def _text(value, limit=512):
    return isinstance(value, str) and bool(value.strip()) and len(value) <= limit and "\x00" not in value


def _hex(value, length):
    return isinstance(value, str) and re.fullmatch("[0-9a-f]{" + str(length) + "}", value) is not None


def validate(raw, *, session_id, context_digest):
    """Bound and validate readback without rewriting BAT's original proof."""
    try:
        if len(pm_runs._json(raw).encode("utf-8")) > MAX_PROOF_BYTES:
            raise _bad()
        value = raw["pmRuntime"]
        attestation, process = value["attestation"], value["process"]
        valid = (raw["sessionId"] == session_id and value["sessionId"] == session_id and value["profile"] == PROFILE
            and type(value["profileVersion"]) is int and value["profileVersion"] == 1
            and value["clientContextDigest"] == context_digest and _hex(context_digest, 64)
            and value["codexVersion"] == CODEX_VERSION and value["codexSourceRevision"] == CODEX_SOURCE
            and _hex(value["batSourceRevision"], 40) and _text(value["batVersion"])
            and _hex(value["batBinaryDigest"], 64) and _hex(value["codexBinaryDigest"], 64)
            and value.get("receiptPersistence") != "failed"
            and all(_text(value[key]) for key in ("runtimeId", "threadId", "actualModel", "runtimeCwd"))
            and _hex(value["configDigest"], 64)
            and type(process["pid"]) is int and process["pid"] > 0
            and type(process["startTicks"]) is int and process["startTicks"] > 0
            and _hex(process["bootIdDigest"], 64)
            and all(attestation[key] is True for key in
                ("optionsConfirmed", "isolatedConfigConfirmed", "externalToolsDisabled", "freshThread"))
            and attestation["environmentAccess"] is False
            and attestation["skills"] == [] and attestation["mcpServers"] == []
            and type(attestation["initialNumTurns"]) is int and attestation["initialNumTurns"] == 0
            and attestation["toolCatalog"] == "not_exposed_by_protocol"
            and attestation["internalToolOptionsReadback"] == "not_exposed_by_protocol")
    except (TypeError, ValueError, KeyError, UnicodeError):
        raise _bad() from None
    if not valid:
        raise _bad()
    return copy.deepcopy(value)


def identity(value):
    return {key: copy.deepcopy(value[key]) for key in (
        "sessionId", "profile", "profileVersion", "runtimeId", "clientContextDigest", "batVersion", "batSourceRevision",
        "batBinaryDigest", "codexVersion", "codexSourceRevision", "codexBinaryDigest", "process", "threadId", "actualModel", "configDigest",
        "runtimeCwd", "attestation")}


def schema(journal):
    with journal.tx():
        journal.db.execute("""CREATE TABLE IF NOT EXISTS pm_bat_runtime_observations (
            run_id TEXT NOT NULL, sequence INTEGER NOT NULL, document TEXT NOT NULL,
            document_digest TEXT NOT NULL, created_at REAL NOT NULL,
            PRIMARY KEY(run_id,sequence), UNIQUE(run_id,document_digest))""")
        journal.db.execute("""CREATE TABLE IF NOT EXISTS pm_bat_abort_proofs (
            run_id TEXT PRIMARY KEY,document TEXT NOT NULL,document_digest TEXT NOT NULL,created_at REAL NOT NULL)""")
        for verb in ("UPDATE", "DELETE"):
            journal.db.execute(f"CREATE TRIGGER IF NOT EXISTS pm_bat_runtime_{verb.lower()}_immutable "
                f"BEFORE {verb} ON pm_bat_runtime_observations BEGIN SELECT RAISE(ABORT, 'BAT PM proof is immutable'); END")
            journal.db.execute(f"CREATE TRIGGER IF NOT EXISTS pm_bat_abort_{verb.lower()}_immutable "
                f"BEFORE {verb} ON pm_bat_abort_proofs BEGIN SELECT RAISE(ABORT, 'BAT PM abort proof is immutable'); END")


def record_abort(journal, run, raw, *, session_id):
    """Accept only original zero-process rejection/death, never turn authority.

    Config preflight may fail before attestation or thread creation. Those null
    fields must block send/import without trapping a positively dead writer.
    """
    try:
        value = raw['pmRuntime']
        valid = (len(pm_runs._json(raw).encode('utf-8')) <= MAX_PROOF_BYTES
            and raw['sessionId'] == session_id and value['sessionId'] == session_id
            and value['profile'] == PROFILE and type(value['profileVersion']) is int and value['profileVersion'] == 1
            and value['clientContextDigest'] == run['context_digest'] and _text(value['runtimeId'])
            and _hex(value['batSourceRevision'], 40) and _text(value['batVersion'])
            and value['codexVersion'] == CODEX_VERSION and value['codexSourceRevision'] == CODEX_SOURCE
            and value.get('receiptPersistence') != 'failed')
        process = value.get('process')
        no_process = (value.get('status') == 'failed' and value.get('launchAttempted') is False
            and _text(value.get('failureReason')) and all(value.get(key) is None for key in ('process', 'send', 'terminal', 'exit')))
        exit_proof, observed = value.get('exit') or {}, value.get('processObservation') or {}
        dead = (value.get('launchAttempted') is True and isinstance(process, dict)
            and type(process.get('pid')) is int and process['pid'] > 0
            and type(process.get('startTicks')) is int and process['startTicks'] > 0 and _hex(process.get('bootIdDigest'), 64)
            and (exit_proof.get('confirmedDead') is True and exit_proof.get('process') == process
                and (type(exit_proof.get('exitCode')) is int or type(exit_proof.get('signal')) is int)
                or observed.get('state') == 'dead' and observed.get('proof') in {
                    'host_rebooted', 'pid_reused', 'original_process_exited', 'original_pid_absent'}))
    except (TypeError, ValueError, KeyError, UnicodeError):
        return None
    if not valid or not (no_process or dead):
        return None
    with journal.tx():
        pm_runs._active(journal.db, run['run_id'], run['generation'])
        prior = observations(journal.db, run['run_id'])
        if prior and (value['runtimeId'] != prior[0]['runtimeId'] or process != prior[0]['process']):
            return None
        saved = journal.db.execute('SELECT document FROM pm_bat_abort_proofs WHERE run_id=?', (run['run_id'],)).fetchone()
        if saved:
            return json.loads(saved[0])
        result = {'session_id': session_id, 'reason': 'never_launched' if no_process else 'original_process_dead',
                  'runtime': value, 'automatic_turn_authorized': False}
        journal.db.execute('INSERT INTO pm_bat_abort_proofs VALUES(?,?,?,?)',
            (run['run_id'], pm_runs._json(result), pm_runs._digest(result), time.time()))
        return result


def observations(db, run_id):
    return [json.loads(row[0]) for row in db.execute(
        "SELECT document FROM pm_bat_runtime_observations WHERE run_id=? ORDER BY sequence", (run_id,))]


def record(journal, run, raw, *, session_id):
    value = validate(raw, session_id=session_id, context_digest=run["context_digest"])
    with journal.tx():
        pm_runs._active(journal.db, run["run_id"], run["generation"])
        prior = observations(journal.db, run["run_id"])
        if prior and identity(value) != identity(prior[0]):
            raise OperationError("PM_BAT_RUNTIME_CHANGED", "The dedicated PM process identity changed", 409)
        # A disappearing or rewritten native proof is a source conflict. Never
        # replace it with a newer process, even when its session label matches.
        for key in ("terminal", "exit"):
            old = next((item.get(key) for item in prior if item.get(key)
                        and (key != "exit" or item[key].get("confirmedDead") is True)), None)
            if old is not None and value.get(key) != old:
                raise OperationError("PM_BAT_RUNTIME_CHANGED", "Original terminal or exit proof changed", 409)
        sent = next((item["send"] for item in prior if item.get("send")), None)
        if sent is not None and (not isinstance(value.get("send"), dict)
                or any(value["send"].get(key) != sent[key] for key in
                       ("attempted", "promptDigest", "clientMessageId", "requestId") if key in sent)):
            raise OperationError("PM_BAT_RUNTIME_CHANGED", "Original send identity changed", 409)
        encoded, digest = pm_runs._json(value), pm_runs._digest(value)
        if not journal.db.execute("SELECT 1 FROM pm_bat_runtime_observations WHERE run_id=? AND document_digest=?",
                                  (run["run_id"], digest)).fetchone():
            if len(prior) >= 128:
                raise OperationError("PM_BAT_PROOF_LIMIT", "The original runtime proof budget is exhausted", 409)
            journal.db.execute("INSERT INTO pm_bat_runtime_observations VALUES(?,?,?,?,?)",
                (run["run_id"], len(prior) + 1, encoded, digest, time.time()))
    return value


def death_proof(db, run):
    values = observations(db, run["run_id"])
    for value in reversed(values):
        proof = value.get("exit")
        if (isinstance(proof, dict) and proof.get("confirmedDead") is True
                and proof.get("process") == value["process"] and _text(proof.get("action"))
                and (type(proof.get("exitCode")) is int or type(proof.get("signal")) is int)):
            return {"runtime_id": value["runtimeId"], "process": value["process"], "exit": proof}
        observed = value.get("processObservation") or {}
        if observed.get("state") == "dead" and observed.get("proof") in {
                "host_rebooted", "pid_reused", "original_process_exited", "original_pid_absent"}:
            return {"runtime_id": value["runtimeId"], "process": value["process"], "observation": observed}
    return None


def public_process_proof(proof):
    """Project public evidence without modifying the original private receipt."""
    if not isinstance(proof, dict):
        return None
    def pick(value, fields):
        # Optional provider metadata must not smuggle an arbitrary nested object
        # through an otherwise allowed scalar field.
        return {key: value[key] for key in fields if key in value
            and type(value[key]) in (str, int, float, bool, type(None))} if isinstance(value, dict) else None
    def process(value):
        return pick(value, ('pid', 'startTicks', 'bootIdDigest'))
    value = pick(proof, ('runtime_id', 'session_id', 'reason', 'automatic_turn_authorized'))
    if 'process' in proof:
        value['process'] = process(proof['process'])
    if isinstance(proof.get('exit'), dict):
        value['exit'] = pick(proof['exit'], ('confirmedDead', 'exitCode', 'signal', 'action'))
        value['exit']['process'] = process(proof['exit'].get('process'))
    if isinstance(proof.get('observation'), dict):
        value['observation'] = pick(proof['observation'], ('state', 'proof', 'observedAt', 'observed_at'))
    if isinstance(proof.get('runtime'), dict):
        # Abort-only proof has its complete original record in the immutable
        # private abort journal. Public reads retain identity and death facts.
        original = proof['runtime']
        value['runtime'] = pick(original, ('sessionId', 'runtimeId', 'profile', 'profileVersion', 'clientContextDigest',
            'batVersion', 'batSourceRevision', 'batBinaryDigest', 'codexVersion', 'codexSourceRevision',
            'codexBinaryDigest', 'threadId', 'actualModel', 'configDigest', 'status', 'launchAttempted', 'failureReason'))
        facts = public_process_proof({'process': original.get('process'), 'exit': original.get('exit'),
            'observation': original.get('processObservation')})
        value['runtime'].update(facts)
        value['runtime_digest'] = pm_runs._digest(original)
    return value


def provenance(db, run):
    """Compact references to the immutable original proof, never a new probe."""
    rows = db.execute("SELECT sequence,document,document_digest FROM pm_bat_runtime_observations "
                      "WHERE run_id=? ORDER BY sequence DESC LIMIT 1", (run["run_id"],)).fetchone()
    if not rows:
        return None
    value = json.loads(rows["document"])
    terminal = value.get("terminal") or {}
    return {"adapter": ADAPTER, "profile": value["profile"], "profile_version": value["profileVersion"],
        "session_id": value["sessionId"], "runtime_id": value["runtimeId"],
        "observation_sequence": rows["sequence"], "observation_digest": rows["document_digest"],
        "bat_source_revision": value["batSourceRevision"], "bat_binary_digest": value["batBinaryDigest"],
        "codex_source_revision": value["codexSourceRevision"], "codex_binary_digest": value["codexBinaryDigest"],
        "native_event_digest": terminal.get("eventDigest"), "native_message_id": terminal.get("nativeMessageId"),
        "process_completion": public_process_proof(death_proof(db, run))}


def verified_reply(journal, run_id, generation):
    """Recompute original BAT proof inside the destination's transaction.

    BAT replay message IDs and native app-server item IDs remain distinct.
    No caller can substitute content, process identity or native evidence.
    """
    from . import pm_runtime
    run = pm_runs.require_writer(journal, run_id, generation)
    if run['adapter'] != ADAPTER:
        raise _bad()
    binding = run['session_binding']
    launch = next((row['data'] for row in run['receipts']
        if row['phase'] == 'prepare' and row['status'] == 'prepared'), {})
    if not binding or not launch or not run['send_cursor']:
        raise _bad()
    observed = pm_runtime.assess_turn(binding['session_id'], run['send_cursor'], pm_runs.events(journal.db, run_id),
        run['cursor'], binding['sdk_session_id'], gap=bool(run['gaps']), has_more=run['has_more'],
        actual_model=binding['actual_model'], expected_prompt_digest=launch['prompt_digest'])
    require_completed(journal.db, run, observed, prompt_digest=launch['prompt_digest'], message_id='batc-pm-' + run_id)
    terminal, payload = pm_runs._terminal(journal.db, run, turn_id=observed['native_turn_id'],
        sdk_session_id=observed['sdk_session_id'])
    if payload.get('reason') != 'completed':
        raise _bad()
    proof = {'adapter': ADAPTER, 'contract': PROFILE, 'run_id': run_id, 'context_digest': run['context_digest'],
        'prompt_digest': launch['prompt_digest'], 'native_thread_id': binding['sdk_session_id'],
        'client_message_id': 'batc-pm-' + run_id, 'send_cursor': run['send_cursor'], 'observed_cursor': run['cursor'],
        'native_event_seqs': observed['source_event_seqs'],
        'terminal': {key: terminal[key] for key in ('epoch', 'native_seq', 'digest')},
        'bat_runtime': provenance(journal.db, run), 'coverage': run['context']['coverage']}
    return run, observed, proof


def require_completed(db, run, assessment, *, prompt_digest, message_id):
    values = observations(db, run["run_id"])
    value = values[-1] if values else {}
    native, sent = value.get("terminal") or {}, value.get("send") or {}
    event = native.get("event")
    params = event.get("params", {}) if isinstance(event, dict) else {}
    turn = params.get("turn", {}) if isinstance(params, dict) else {}
    items = turn.get('items', []) if isinstance(turn, dict) else []
    final = [item for item in items if isinstance(item, dict) and item.get('type') == 'agentMessage'
             and item.get('id') == native.get('nativeMessageId')] if isinstance(items, list) else []
    if (not death_proof(db, run) or not assessment.get("complete")
            or native.get("native") is not True or native.get("status") != "completed"
            or native.get("threadId") != value.get("threadId")
            or native.get("turnId") != assessment.get("native_turn_id")
            or native.get("threadId") != assessment.get("sdk_session_id")
            or not isinstance(event, dict) or event.get("method") != "turn/completed"
            or native.get("eventDigest") != pm_runs._digest(event)
            or params.get("threadId") != native.get("threadId")
            or not isinstance(turn, dict) or turn.get("id") != native.get("turnId") or turn.get("status") != "completed"
            or not _text(native.get("nativeMessageId"))
            or len(final) != 1 or final[0].get('text') != assessment.get('content')
            or native.get("outputDigest") != hashlib.sha256(assessment["content"].encode("utf-8")).hexdigest()
            or sent.get("attempted") is not True or sent.get("promptDigest") != prompt_digest
            or sent.get("clientMessageId") != message_id or sent.get("turnId") != native.get("turnId")
            or sent.get("acknowledged") is not True or sent.get("nativeStarted") is not True
            or sent.get("observedClientMessageId") != message_id):
        raise OperationError("PM_BAT_COMPLETION_UNPROVEN", "The original BAT turn and dedicated process exit are not proven", 409)
    return value
