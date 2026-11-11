"""Append-only launcher facts. Installation and transport belong to future callers."""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, astuple, dataclass, replace

from . import controller_authority
from .errors import ResourceReadOnly
from .native_process import _json

CREATION_UNPROVEN = "NATIVE_CREATION_UNPROVEN"
IDENTITY_MISMATCH = "NATIVE_IDENTITY_MISMATCH"
CONTROLLER_UNSUPPORTED = "NATIVE_CONTROLLER_UNSUPPORTED"
PROCESS_UNPROVEN = "NATIVE_PROCESS_IDENTITY_UNPROVEN"
RECORD_CONFLICT = "NATIVE_RECORD_CONFLICT"
EVIDENCE_INVALID = "NATIVE_EVIDENCE_INVALID"
FENCE_UNPROVEN = "NATIVE_FENCE_UNPROVEN"


class NativeEvidenceError(ResourceReadOnly):
    pass


def require(condition, code=EVIDENCE_INVALID):
    if not condition:
        raise NativeEvidenceError(code, "Native launcher evidence does not satisfy the journal contract")


@dataclass(frozen=True)
class SessionKey:
    server_id: str
    adapter_instance_id: str
    native_session_id: str
    incarnation: str


@dataclass(frozen=True)
class RecordContext:
    key: SessionKey
    operation_id: str
    attempt_id: str
    turn_ordinal: int
    input_id: str


@dataclass(frozen=True)
class ProcessIdentity:
    pid: int
    start_ticks: int
    boot_id_digest: str


@dataclass(frozen=True)
class LaunchIntent:
    context: RecordContext
    reservation_id: str
    nonce: str
    work_id: str
    carrier_receipt: dict
    controller_authority: dict
    controller_incarnation: str
    host_binding_digest: str
    source_sha: str
    cli: str
    cli_version: str
    cli_artifact_digest: str
    protocol_digest: str
    profile_revision: int
    private_profile: str
    cwd: str
    policy_digest: str
    input_digest: str
    mode: str
    observed_at: float


@dataclass(frozen=True)
class CreatedSession:
    launch: RecordContext
    nonce: str
    native_session_id: str
    mode: str
    cwd: str
    private_profile: str
    policy_digest: str
    process: ProcessIdentity
    event_digest: str
    observed_at: float
    sequence: int


def schema(journal):
    with journal.tx():
        journal.db.execute("""CREATE TABLE IF NOT EXISTS native_session_creations (
            server_id TEXT NOT NULL, adapter_instance_id TEXT NOT NULL, native_session_id TEXT NOT NULL,
            incarnation TEXT NOT NULL, operation_id TEXT NOT NULL, attempt_id TEXT NOT NULL,
            turn_ordinal INTEGER NOT NULL, input_id TEXT NOT NULL, sequence INTEGER NOT NULL,
            kind TEXT NOT NULL, data TEXT NOT NULL, digest TEXT NOT NULL,
            PRIMARY KEY(server_id,adapter_instance_id,native_session_id,incarnation))""")
        journal.db.execute("""CREATE TABLE IF NOT EXISTS native_process_receipts (
            server_id TEXT NOT NULL, adapter_instance_id TEXT NOT NULL, native_session_id TEXT NOT NULL,
            incarnation TEXT NOT NULL, operation_id TEXT NOT NULL, attempt_id TEXT NOT NULL,
            turn_ordinal INTEGER NOT NULL, input_id TEXT NOT NULL, sequence INTEGER NOT NULL,
            kind TEXT NOT NULL, data TEXT NOT NULL, digest TEXT NOT NULL,
            PRIMARY KEY(server_id,adapter_instance_id,native_session_id,incarnation,operation_id,attempt_id,turn_ordinal,input_id,sequence))""")
        journal.db.execute("""CREATE TABLE IF NOT EXISTS native_turn_receipts (
            server_id TEXT NOT NULL, adapter_instance_id TEXT NOT NULL, native_session_id TEXT NOT NULL,
            incarnation TEXT NOT NULL, operation_id TEXT NOT NULL, attempt_id TEXT NOT NULL,
            turn_ordinal INTEGER NOT NULL, input_id TEXT NOT NULL, sequence INTEGER NOT NULL,
            kind TEXT NOT NULL, data TEXT NOT NULL, digest TEXT NOT NULL,
            PRIMARY KEY(server_id,adapter_instance_id,native_session_id,incarnation,operation_id,attempt_id,turn_ordinal,input_id,sequence))""")
        journal.db.execute("""CREATE TABLE IF NOT EXISTS native_event_records (
            server_id TEXT NOT NULL, adapter_instance_id TEXT NOT NULL, native_session_id TEXT NOT NULL,
            incarnation TEXT NOT NULL, operation_id TEXT NOT NULL, attempt_id TEXT NOT NULL,
            turn_ordinal INTEGER NOT NULL, input_id TEXT NOT NULL, sequence INTEGER NOT NULL,
            kind TEXT NOT NULL, data TEXT NOT NULL, digest TEXT NOT NULL,
            PRIMARY KEY(server_id,adapter_instance_id,native_session_id,incarnation,operation_id,attempt_id,turn_ordinal,input_id,sequence))""")


def _key(key):
    require(type(key) is SessionKey and all(isinstance(v, str) for v in astuple(key)))
    require(key.server_id and key.adapter_instance_id and re.fullmatch(r"[0-9a-f]{32}", key.incarnation))
    return astuple(key)


def _context(context):
    require(type(context) is RecordContext)
    require(all(isinstance(v, str) and v for v in (context.operation_id, context.attempt_id, context.input_id)))
    require(type(context.turn_ordinal) is int and context.turn_ordinal >= 1)
    return (*_key(context.key), context.operation_id, context.attempt_id, context.turn_ordinal, context.input_id)


def ordinary_authority(value):
    require(isinstance(value, dict) and "pm_operation_id" not in value
            and not str(value.get("actor", "")).startswith("pm:"), CONTROLLER_UNSUPPORTED)
    return controller_authority._validated(value)


def process_identity(value):
    require(type(value) is ProcessIdentity, PROCESS_UNPROVEN)
    require(type(value.pid) is int and value.pid > 0 and type(value.start_ticks) is int and value.start_ticks >= 0
            and isinstance(value.boot_id_digest, str) and re.fullmatch(r"[0-9a-f]{64}", value.boot_id_digest), PROCESS_UNPROVEN)
    return asdict(value)


def _rows(db, key, table):
    args = _key(key)
    if table == "creation":
        rows = db.execute("SELECT * FROM native_session_creations WHERE server_id=? AND adapter_instance_id=? AND native_session_id=? AND incarnation=?", args)
    elif table == "process":
        rows = db.execute("SELECT * FROM native_process_receipts WHERE server_id=? AND adapter_instance_id=? AND native_session_id=? AND incarnation=? ORDER BY sequence", args)
    elif table == "turn":
        rows = db.execute("SELECT * FROM native_turn_receipts WHERE server_id=? AND adapter_instance_id=? AND native_session_id=? AND incarnation=? ORDER BY sequence", args)
    else:
        require(table == "event")
        rows = db.execute("SELECT * FROM native_event_records WHERE server_id=? AND adapter_instance_id=? AND native_session_id=? AND incarnation=? ORDER BY sequence", args)
    return [{**dict(row), "data": json.loads(row["data"])} for row in rows]


def creation(db, key):
    rows = _rows(db, key, "creation")
    return rows[0] if rows else None


def processes(db, key):
    return _rows(db, key, "process")


def _matching(rows, context):
    return [row for row in rows if (row["operation_id"], row["attempt_id"], row["turn_ordinal"], row["input_id"])
            == (context.operation_id, context.attempt_id, context.turn_ordinal, context.input_id)]


def _append(journal, table, context, sequence, kind, value, validate):
    require(not journal.db.in_transaction, FENCE_UNPROVEN)
    require(type(sequence) is int and sequence >= 0)
    encoded = _json(value).decode()
    require(len(encoded.encode()) <= 2 * 1024 * 1024)
    values = (*_context(context), sequence, kind, encoded)
    digest = hashlib.sha256(_json(values)).hexdigest()
    with journal.tx():
        rows = _rows(journal.db, context.key, table)
        matching = _matching(rows, context)
        old = rows[0] if table == "creation" and rows else next((r for r in matching if r["sequence"] == sequence), None)
        if old is not None:
            require(old["digest"] == digest, RECORD_CONFLICT)
            return old
        if table != "creation":
            require(sequence == max((r["sequence"] for r in matching), default=-1) + 1, FENCE_UNPROVEN)
        validate(rows)
        args = (*values, digest)
        if table == "creation":
            journal.db.execute("INSERT INTO native_session_creations VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", args)
        elif table == "process":
            journal.db.execute("INSERT INTO native_process_receipts VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", args)
        elif table == "turn":
            journal.db.execute("INSERT INTO native_turn_receipts VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", args)
        else:
            journal.db.execute("INSERT INTO native_event_records VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", args)
        return next(row for row in _rows(journal.db, context.key, table) if row["digest"] == digest)


def _intent(db, context, code=CREATION_UNPROVEN):
    _context(context)
    rows = _matching(processes(db, context.key), context)
    intent = next((r for r in rows if r["kind"] == "launch_intent"), None)
    require(intent is not None, code)
    return intent["data"], rows


def record_intent(journal, intent):
    require(type(intent) is LaunchIntent)
    _context(intent.context)
    binding = ordinary_authority(intent.controller_authority)
    require(binding["server_id"] == intent.context.key.server_id, IDENTITY_MISMATCH)
    require(intent.context.turn_ordinal == 1 and intent.mode in {"create_reply", "process_init"})
    require(all(isinstance(v, str) and v for v in (intent.reservation_id, intent.nonce, intent.work_id,
        intent.cli, intent.cli_version, intent.private_profile, intent.cwd)))
    require(isinstance(intent.carrier_receipt, dict) and intent.carrier_receipt.get("cwd") == intent.cwd)
    require(all(isinstance(v, str) and re.fullmatch(r"[0-9a-f]{64}", v) for v in (
        intent.host_binding_digest, intent.cli_artifact_digest, intent.protocol_digest, intent.policy_digest,
        intent.input_digest, intent.carrier_receipt.get("creation_digest"))))
    require(isinstance(intent.source_sha, str) and re.fullmatch(r"[0-9a-f]{40}", intent.source_sha)
            and isinstance(intent.controller_incarnation, str) and re.fullmatch(r"[0-9a-f]{32}", intent.controller_incarnation))
    require(type(intent.profile_revision) is int and intent.profile_revision >= 1)
    return _append(journal, "process", intent.context, 0, "launch_intent", asdict(intent),
                   lambda rows: require(not rows, RECORD_CONFLICT))


def record_process(journal, context, sequence, kind, nonce, payload):
    require(kind in {"spawn_attempted", "create_sent", "process_started", "process_exited", "process_exit_unknown", "death_observed"})
    if "process" in payload:
        payload = {**payload, "process": process_identity(payload["process"])}

    def validate(rows):
        intent, prior = _intent(journal.db, context, FENCE_UNPROVEN)
        require(nonce == intent["nonce"], IDENTITY_MISMATCH)
        kinds = {r["kind"] for r in prior}
        require(kind == "spawn_attempted" or "spawn_attempted" in kinds, FENCE_UNPROVEN)
        if kind in {"spawn_attempted", "create_sent"}:
            require(kind not in kinds, FENCE_UNPROVEN)

    return _append(journal, "process", context, sequence, kind, {"nonce": nonce, "payload": payload}, validate)


def record_creation(journal, observed):
    require(type(observed) is CreatedSession)
    process_identity(observed.process)
    require(observed.mode in {"create_reply", "process_init"} and isinstance(observed.native_session_id, str) and observed.native_session_id)
    require(isinstance(observed.event_digest, str) and re.fullmatch(r"[0-9a-f]{64}", observed.event_digest))
    intent, prior = _intent(journal.db, observed.launch)
    context = replace(observed.launch, key=replace(observed.launch.key, native_session_id=observed.native_session_id))

    def validate(_):
        ordinary_authority(intent["controller_authority"])
        require(all(getattr(observed, field) == intent[field] for field in ("nonce", "mode", "cwd", "private_profile", "policy_digest")), IDENTITY_MISMATCH)
        require(not observed.launch.key.native_session_id or observed.launch.key.native_session_id == observed.native_session_id, IDENTITY_MISMATCH)
        fence = "create_sent" if observed.mode == "create_reply" else "spawn_attempted"
        require(any(r["kind"] == fence and r["sequence"] < observed.sequence for r in prior), FENCE_UNPROVEN)

    return _append(journal, "creation", context, observed.sequence, "created",
                   {"intent": intent, "observed": asdict(observed)}, validate)


def record_turn(journal, context, sequence, kind, nonce, payload):
    require(kind in {"turn_sent", "ack", "terminal", "interrupted"})

    def validate(rows):
        receipt = creation(journal.db, context.key)
        if receipt is None:
            intent, prior = _intent(journal.db, context)
            require(context.turn_ordinal == 1 and intent["mode"] == "process_init"
                    and any(r["kind"] == "spawn_attempted" for r in prior)
                    and payload.get("input_digest") == intent["input_digest"], CREATION_UNPROVEN)
        else:
            intent = receipt["data"]["intent"]
        require(nonce == intent["nonce"], IDENTITY_MISMATCH)
        sent = any(r["kind"] == "turn_sent" for r in _matching(rows, context))
        require((kind == "turn_sent" and not sent) or (kind != "turn_sent" and sent), FENCE_UNPROVEN)

    return _append(journal, "turn", context, sequence, kind, {"nonce": nonce, "payload": payload}, validate)


def record_event(journal, context, sequence, payload):
    def validate(_):
        rows = _matching(_rows(journal.db, context.key, "turn"), context)
        require(any(r["kind"] == "turn_sent" for r in rows), FENCE_UNPROVEN)
    return _append(journal, "event", context, sequence, "native_event", payload, validate)
