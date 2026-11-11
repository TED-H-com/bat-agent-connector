"""Launcher evidence uses real disk SQLite; no CLI, model or credential access."""
import ast
import copy
import sqlite3
import uuid
from dataclasses import asdict, replace
from pathlib import Path

import pytest

from bat_agent_connector import controller_authority as authority
from bat_agent_connector import native_journal as native
from bat_agent_connector import pm_native_journal
from bat_agent_connector.api_auth import Principal
from bat_agent_connector.task_journal import Journal


@pytest.fixture
def journal(tmp_path):
    journal = Journal(tmp_path / "native.db")
    native.schema(journal)
    yield journal
    journal.close()


def launcher(journal, *, binding=None, mode="create_reply"):
    """Test-only producer of the typed, observed launcher boundary."""
    binding = binding or authority.binding(journal.db, Principal("fixture-owner", frozenset({"operate"})))
    context = native.RecordContext(native.SessionKey(binding["server_id"], "adapter-fixture", "", "a" * 32),
                                   "op_start", "attempt-fixture", 1, "input-first")
    intent = native.LaunchIntent(context, "reservation-fixture", "launch-nonce", "work-fixture",
        {"creation_digest": "b" * 64, "cwd": "/managed/work"}, binding, "c" * 32,
        "d" * 64, "e" * 40, "fixture-cli", "1.0", "f" * 64, "1" * 64, 1,
        "/managed/profile", "/managed/work", "2" * 64, "3" * 64, mode, 10.0)
    observed = native.CreatedSession(context, intent.nonce, "session-fixture", mode,
        intent.cwd, intent.private_profile, intent.policy_digest,
        native.ProcessIdentity(424242, 25, "4" * 64), "5" * 64, 11.0, 3)
    return intent, observed


def prepare(journal, intent, *, create=True):
    native.record_intent(journal, intent)
    native.record_process(journal, intent.context, 1, "spawn_attempted", intent.nonce, {})
    if create:
        native.record_process(journal, intent.context, 2, "create_sent", intent.nonce, {})


def created(journal, **kwargs):
    intent, observed = launcher(journal, **kwargs)
    prepare(journal, intent, create=intent.mode == "create_reply")
    row = native.record_creation(journal, observed)
    context = replace(intent.context, key=replace(intent.context.key, native_session_id=observed.native_session_id))
    return intent, observed, context, row


def snapshot(db):
    return tuple(db.iterdump())


@pytest.mark.parametrize("table", ["creation", "process", "turn", "event"])
def test_each_fact_replays_without_writes_and_conflicts_preserve_original(journal, table):
    intent, observed, context, creation = created(journal)
    if table in {"turn", "event"}:
        native.record_turn(journal, context, 0, "turn_sent", intent.nonce, {"input_digest": intent.input_digest})

    def write(changed):
        if table == "creation":
            return native.record_creation(journal, replace(observed, event_digest=("9" * 64 if changed else observed.event_digest)))
        if table == "process":
            return native.record_process(journal, intent.context, 3, "process_exit_unknown", intent.nonce, {"exit_code": 0 if changed else None})
        if table == "turn":
            return native.record_turn(journal, context, 1, "terminal", intent.nonce, {"status": "failed" if changed else "completed"})
        return native.record_event(journal, context, 0, {"text": "changed" if changed else "fixture"})
    row = write(False)
    before, count = snapshot(journal.db), journal.db.total_changes
    assert write(False) == row
    assert journal.db.total_changes == count
    with pytest.raises(native.NativeEvidenceError, match="NATIVE_RECORD_CONFLICT"):
        write(True)
    assert snapshot(journal.db) == before and journal.db.total_changes == count
    assert native.creation(journal.db, context.key) == creation


def test_fences_are_committed_and_ordered_before_effects(journal):
    intent, observed = launcher(journal)
    with pytest.raises(native.NativeEvidenceError, match="NATIVE_FENCE_UNPROVEN"):
        native.record_process(journal, intent.context, 1, "spawn_attempted", intent.nonce, {})
    native.record_intent(journal, intent)
    with sqlite3.connect(journal.path) as reader:
        assert reader.execute("SELECT kind FROM native_process_receipts").fetchall() == [("launch_intent",)]
    with pytest.raises(native.NativeEvidenceError, match="NATIVE_FENCE_UNPROVEN"):
        native.record_process(journal, intent.context, 2, "create_sent", intent.nonce, {})
    with journal.tx(), pytest.raises(native.NativeEvidenceError, match="NATIVE_FENCE_UNPROVEN"):
        native.record_process(journal, intent.context, 1, "spawn_attempted", intent.nonce, {})
    prepare(journal, intent)
    with sqlite3.connect(journal.path) as reader:
        assert reader.execute("SELECT kind FROM native_process_receipts ORDER BY sequence").fetchall() == [
            ("launch_intent",), ("spawn_attempted",), ("create_sent",)]
    context = replace(intent.context, key=replace(intent.context.key, native_session_id=observed.native_session_id))
    with pytest.raises(native.NativeEvidenceError, match="NATIVE_CREATION_UNPROVEN"):
        native.record_turn(journal, context, 0, "turn_sent", intent.nonce, {})
    native.record_creation(journal, observed)
    native.record_turn(journal, context, 0, "turn_sent", intent.nonce, {})
    with sqlite3.connect(journal.path) as reader:
        assert reader.execute("SELECT kind FROM native_turn_receipts").fetchall() == [("turn_sent",)]
    before = snapshot(journal.db)
    with pytest.raises(native.NativeEvidenceError, match="NATIVE_FENCE_UNPROVEN"):
        native.record_turn(journal, context, 2, "turn_sent", intent.nonce, {})
    assert snapshot(journal.db) == before


def test_process_init_first_input_fence_does_not_authorize_a_second_turn(journal):
    intent, observed = launcher(journal, mode="process_init")
    prepare(journal, intent, create=False)
    native.record_turn(journal, intent.context, 0, "turn_sent", intent.nonce, {"input_digest": intent.input_digest})
    second = replace(intent.context, turn_ordinal=2, input_id="second", operation_id="op_second")
    with pytest.raises(native.NativeEvidenceError, match="NATIVE_CREATION_UNPROVEN"):
        native.record_turn(journal, second, 0, "turn_sent", intent.nonce, {})
    native.record_creation(journal, observed)


@pytest.mark.parametrize("change,code", [
    ({"nonce": "other"}, "NATIVE_IDENTITY_MISMATCH"),
    ({"cwd": "/personal/checkout"}, "NATIVE_IDENTITY_MISMATCH"),
    ({"private_profile": "/personal/profile"}, "NATIVE_IDENTITY_MISMATCH"),
    ({"policy_digest": "9" * 64}, "NATIVE_IDENTITY_MISMATCH"),
    ({"mode": "history"}, "NATIVE_EVIDENCE_INVALID"),
    ({"process": {"pid": 424242}}, "NATIVE_PROCESS_IDENTITY_UNPROVEN"),
    ({"event_digest": ""}, "NATIVE_EVIDENCE_INVALID"),
    ({"native_session_id": ""}, "NATIVE_EVIDENCE_INVALID"),
])
def test_creation_refuses_unbound_observation(journal, change, code):
    intent, observed = launcher(journal)
    prepare(journal, intent)
    before = snapshot(journal.db)
    with pytest.raises(native.NativeEvidenceError, match=code):
        native.record_creation(journal, replace(observed, **change))
    assert snapshot(journal.db) == before


def test_creation_requires_original_intent_and_creation_fence(journal):
    intent, observed = launcher(journal)
    with pytest.raises(native.NativeEvidenceError, match="NATIVE_CREATION_UNPROVEN"):
        native.record_creation(journal, observed)
    native.record_intent(journal, intent)
    with pytest.raises(native.NativeEvidenceError, match="NATIVE_FENCE_UNPROVEN"):
        native.record_creation(journal, observed)
    prepare(journal, intent)
    changed = replace(observed, launch=replace(intent.context, operation_id="op_foreign"))
    with pytest.raises(native.NativeEvidenceError, match="NATIVE_CREATION_UNPROVEN"):
        native.record_creation(journal, changed)


@pytest.mark.parametrize("payload", [{"params": {"session_id": "session-fixture"}},
    {"source": "external_history"}, {"source": "work_link"}, {"source": "native_session_file"},
    {"session_id": "preallocated-uuid"}, {"exit_code": 0}])
def test_user_or_history_payloads_cannot_create_receipts(journal, payload):
    intent, observed = launcher(journal)
    prepare(journal, intent)
    before = snapshot(journal.db)
    for value in (payload, asdict(observed)):
        with pytest.raises(native.NativeEvidenceError, match="NATIVE_EVIDENCE_INVALID"):
            native.record_creation(journal, value)
    assert snapshot(journal.db) == before


@pytest.mark.parametrize("field", ["pm_operation_id", "actor"])
def test_pm_run_is_not_a_coding_controller(journal, field):
    intent, _ = launcher(journal)
    binding = {**intent.controller_authority, field: "op_pm" if field == "pm_operation_id" else "pm:fixture"}
    with pytest.raises(native.NativeEvidenceError, match="NATIVE_CONTROLLER_UNSUPPORTED"):
        native.record_intent(journal, replace(intent, controller_authority=binding))
    assert not native.processes(journal.db, intent.context.key)


def test_even_a_valid_bounded_pm_authority_cannot_create_coding_evidence(journal):
    intent, _ = launcher(journal)
    actor = "pm:ws_" + "a" * 32
    binding = {**intent.controller_authority, "actor": actor, "pm_workspace_id": actor[3:],
        "controller_id": "ctr_" + uuid.uuid5(uuid.UUID(intent.context.key.server_id), actor).hex,
        "pm_operation_id": "op_" + "b" * 32,
        "pm_requester": {"actor": "fixture-owner", "admin": False, "scopes": ["operate"]}}
    assert authority._validated(binding) == binding
    with pytest.raises(native.NativeEvidenceError, match="NATIVE_CONTROLLER_UNSUPPORTED"):
        native.record_intent(journal, replace(intent, controller_authority=binding))


def test_canonical_replay_and_creation_fields_survive_reopen(journal):
    intent, observed, context, row = created(journal)
    assert row["data"] == {"intent": asdict(intent), "observed": asdict(observed)}
    native.record_turn(journal, context, 0, "turn_sent", intent.nonce, {})
    event = native.record_event(journal, context, 0, {"text": "證據", "status": "partial"})
    with sqlite3.connect(journal.path) as reader:
        original = reader.execute("SELECT data,digest FROM native_event_records").fetchone()
    assert native.record_event(journal, context, 0, {"status": "partial", "text": "證據"}) == event
    other = Journal(journal.path)
    try:
        assert native.creation(other.db, context.key) == row
        assert native.record_creation(other, observed) == row
        assert tuple(other.db.execute("SELECT data,digest FROM native_event_records").fetchone()) == original
    finally:
        other.close()


@pytest.mark.parametrize("field", ["nonce", "cwd"])
def test_existing_creation_identity_conflicts_before_accepting_changed_evidence(journal, field):
    _, observed, _, _ = created(journal)
    before = snapshot(journal.db)
    with pytest.raises(native.NativeEvidenceError, match="NATIVE_RECORD_CONFLICT"):
        native.record_creation(journal, replace(observed, **{field: "changed"}))
    assert snapshot(journal.db) == before


def test_schema_is_explicit_idempotent_and_preserves_existing_history_on_reopen(tmp_path):
    path = tmp_path / "schema.db"
    journal = Journal(path)
    try:
        pm_native_journal.schema(journal)
        journal.api_event("fixture", "original", "fixture.saved", {"kept": True}, actor="fixture")
        before = snapshot(journal.db)
        version = journal.db.execute("PRAGMA user_version").fetchone()[0]
        assert not journal.db.execute("SELECT name FROM sqlite_master WHERE name LIKE 'native_%'").fetchall()
        native.schema(journal)
        native.schema(journal)
        assert {row[0] for row in journal.db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'native_%'")} == {
            "native_session_creations", "native_process_receipts", "native_turn_receipts", "native_event_records"}
        after = snapshot(journal.db)
        assert [line for line in after if not line.startswith(("CREATE TABLE native_", 'INSERT INTO "native_'))] == list(before)
        assert journal.db.execute("PRAGMA user_version").fetchone()[0] == version
    finally:
        journal.close()
    reopened = Journal(path)
    try:
        native.schema(reopened)
        assert snapshot(reopened.db) == after
    finally:
        reopened.close()


def test_module_issues_no_fact_updates_deletes_or_replacements(journal):
    calls = []
    journal.db.set_trace_callback(calls.append)
    intent, observed, context, _ = created(journal)
    native.record_creation(journal, observed)
    native.record_turn(journal, context, 0, "turn_sent", intent.nonce, {})
    native.record_event(journal, context, 0, {"status": "partial"})
    assert not any(sql.lstrip().upper().startswith(("UPDATE", "DELETE", "REPLACE")) for sql in calls)
    tree = ast.parse(Path(native.__file__).read_text())
    strings = [node.value.upper() for node in ast.walk(tree) if isinstance(node, ast.Constant) and isinstance(node.value, str)]
    assert not any(word in sql for sql in strings for word in ("UPDATE ", "DELETE ", "REPLACE ", "ON CONFLICT"))
    before = copy.deepcopy(native.creation(journal.db, context.key))
    assert before["digest"] and before["data"]
