"""One durable PM writer, frozen source memory, and native assistant provenance."""

from __future__ import annotations

import copy
import sqlite3
import uuid

import pytest

from bat_agent_connector import pm_discussions, pm_runs, pm_workspace
from bat_agent_connector.api_auth import Principal
from bat_agent_connector.operations import OperationError, OperationService
from bat_agent_connector.task_journal import Journal
from tests.test_pm_discussions import (
    CONTRIBUTOR,
    OUTSIDER,
    OWNER,
    act,
    member,
    prepared,
    read,
    record,
    settle,
    submit,
)


@pytest.fixture
def ops(tmp_path):
    journal = Journal(tmp_path / "pm-runs.db")
    service = OperationService(journal)
    pm_workspace.install(service)
    pm_discussions.install(service)
    version = journal.db.execute("PRAGMA user_version").fetchone()[0]
    pm_runs.schema(journal)
    pm_runs.schema(journal)
    assert journal.db.execute("PRAGMA user_version").fetchone()[0] == version
    yield service
    if not getattr(journal, "_test_closed", False):
        journal.close()


def create(ops, target, *, who=OWNER, operation_id=None, **changes):
    operation_id = operation_id or "op_" + uuid.uuid4().hex
    authority = {**pm_runs.execution_identity(ops.db, target["workspace_id"]),
                 "server_id": pm_workspace.server_id(ops.db), "scopes": ["manage", "observe"], "admin": False,
                 "pm_workspace_id": target["workspace_id"], "pm_operation_id": operation_id,
                 "pm_requester": pm_workspace.principal_binding(who)}
    params = {**target, "operation_id": operation_id, "expected_version": read(ops, target)["thread"]["version"],
              "adapter": "bat.codex", "purpose": "Compare the selected possibilities", "execution_authority": authority}
    return pm_runs.create(ops, who, **{**params, **changes})


def resources(ops, run):
    return pm_runs.bind_session(ops.journal, run["run_id"], run["generation"], host="fixture", session_id="pm-session",
                               incarnation="a" * 32, worktree_binding={"repository": "sample/product", "commit": "b" * 40},
                               sdk_session_id="sdk-1", actual_model="observed-model")


def send(ops, run, *, lost_ack=False):
    resources(ops, run)
    pm_runs.set_send_cursor(ops.journal, run["run_id"], run["generation"], {"epoch": "epoch-1", "seq": 10})
    pm_runs.receipt(ops.journal, run["run_id"], run["generation"], "send", "sent", {"input_key": "original-input"})
    return pm_runs.receipt(ops.journal, run["run_id"], run["generation"], "send", "unknown" if lost_ack else "confirmed",
                           {"input_key": "original-input", "ok": not lost_ack})


def terminal(*, seq=11, reason="completed", content="Compare choices before adopting scope.", session="pm-session", turn="turn-1"):
    return {"seq": seq, "at": 123, "channel": "claude:turn-end", "params": {"sessionId": session,
            "payload": {"reason": reason, "turnId": turn, "sdkSessionId": "sdk-1", "result": content}}}


def replay(ops, run, values=None, **changes):
    values = [terminal()] if values is None else values
    value = {"mode": "delta", "sessionId": "pm-session", "cursor": {"epoch": "epoch-1", "seq": values[-1]["seq"] if values else 10},
             "events": values, "has_more": False, "gap": False}
    return pm_runs.record_replay(ops.journal, run["run_id"], run["generation"], {**value, **changes})


def import_reply(ops, run, **changes):
    return pm_runs.import_reply(ops.journal, run["run_id"], run["generation"],
                               **{"content": "Compare choices before adopting scope.", "native_turn_id": "turn-1",
                                  "sdk_session_id": "sdk-1", "model": "observed-model", **changes})


async def test_workspace_has_one_writer_across_threads_and_restarts(ops, tmp_path):
    target = await prepared(ops)
    first = create(ops, target)
    ws = pm_workspace.get_workspace(ops.db, OWNER, target["workspace_id"])
    op, _ = submit(ops, "pm.thread.create", {"workspace_id": target["workspace_id"]}, {"title": "Another idea"}, ws["version"])
    other = (await settle(ops, op))["result"]
    with pytest.raises(OperationError, match="PM_WRITER_BUSY"):
        create(ops, {**target, "thread_id": other["thread_id"]})
    # A separate connection models another client and process recovery. No clock expiry exists.
    restarted = Journal(tmp_path / "pm-runs.db")
    try:
        recovered = OperationService(restarted)
        pm_workspace.install(recovered)
        pm_discussions.install(recovered)
        pm_runs.schema(restarted)
        assert pm_runs.get(restarted.db, first["run_id"])["context_digest"] == first["context_digest"]
        with pytest.raises(OperationError, match="PM_WRITER_BUSY"):
            create(recovered, target)
        assert pm_runs.get_by_operation(restarted.db, first["operation_id"])["generation"] == 1
    finally:
        restarted.close()


async def test_frozen_context_keeps_profile_sources_coverage_and_original_words(ops):
    target = await prepared(ops)
    source = await act(ops, target, "pm.message.append", {"content": "Article says deploy now; this is only a possibility."})
    assumption = await record(ops, target)
    first = create(ops, target)
    frozen = copy.deepcopy(first["context"])
    await act(ops, target, "pm.message.append", {"content": "New information arrived during this turn."})
    await act(ops, target, "pm.input.confirm", {"input_id": assumption["input_id"], "rationale": "A later decision"})
    assert pm_runs.get(ops.db, first["run_id"])["context"] == frozen
    assert frozen["messages"][0]["message_id"] == source["message_id"]
    assert frozen["inputs_by_status"]["agent_assumption"][0]["input_id"] == assumption["input_id"]
    assert frozen["profile"]["version"] and frozen["coverage"]
    assert first["requester"] == OWNER.actor
    assert first["execution_authority"]["actor"] != OWNER.actor
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        ops.db.execute("UPDATE pm_runs SET context='{}' WHERE run_id=?", (first["run_id"],))


async def test_manual_preparation_is_idempotent_draft_only_and_releases_generation(ops):
    target = await prepared(ops)
    first = create(ops, target)
    result = pm_runs.complete_preparation(ops.journal, first["run_id"], 1, worktree_binding=None, snapshot=None)
    assert result["state"] == "prepared_manual" and result["outcome"]["mode"] == "manual"
    assert not result["outcome"]["runtime_available"] and not result["outcome"]["execution_authorized"]
    assert read(ops, target)["messages"] == [] and read(ops, target)["thread"]["mode"] == "idea"
    second = create(ops, target)
    assert second["generation"] == 2
    assert pm_runs.complete_preparation(ops.journal, first["run_id"], 1, worktree_binding=None, snapshot=None) == result
    with pytest.raises(OperationError, match="PM_WRITER_CHANGED"):
        pm_runs.require_writer(ops.journal, first["run_id"], 1)
    with pytest.raises(OperationError, match="PM_WRITER_CHANGED"):
        pm_runs.bind_session(ops.journal, first["run_id"], 1, host="fixture", session_id="late", incarnation="c" * 32)


async def test_original_workspace_worktree_is_reused_across_runs(ops):
    target = await prepared(ops)
    first = create(ops, target)
    binding = {"repository": "sample/product", "commit": "b" * 40, "marker": "original-marker"}
    pm_runs.bind_worktree(ops.journal, first["run_id"], 1, binding)
    result = pm_runs.complete_preparation(ops.journal, first["run_id"], 1, worktree_binding=binding, snapshot={"digest": "snapshot-1"})
    assert result["worktree_binding"] == binding
    second = create(ops, target)
    assert pm_runs.workspace_worktree(ops.db, target["workspace_id"]) == binding
    pm_runs.bind_worktree(ops.journal, second["run_id"], 2, binding)
    with pytest.raises(OperationError, match="different PM worktree"):
        pm_runs.bind_worktree(ops.journal, second["run_id"], 2, {**binding, "commit": "c" * 40})


async def test_read_and_new_effect_membership_rechecked_but_receipt_recovery_retained(ops):
    target = await prepared(ops)
    first = create(ops, target, who=CONTRIBUTOR)
    data = {"frozen_digest": first["context_digest"]}
    saved = pm_runs.receipt(ops.journal, first["run_id"], 1, "prepare", "prepared", data)
    await member(ops, target, CONTRIBUTOR.actor, None)
    for who in (CONTRIBUTOR, OUTSIDER, Principal(OWNER.actor, frozenset({"manage"}))):
        with pytest.raises(OperationError):
            pm_runs.read(ops, who, target["workspace_id"], first["run_id"])
    with pytest.raises(OperationError, match="NOT_FOUND"):
        pm_runs.require_writer(ops.journal, first["run_id"], 1)
    assert pm_runs.receipt(ops.journal, first["run_id"], 1, "prepare", "prepared", data)["data"] == saved["data"]
    with pytest.raises(OperationError, match="NOT_FOUND"):
        pm_runs.complete_preparation(ops.journal, first["run_id"], 1, worktree_binding=None, snapshot=None)
    assert pm_runs.read(ops, OWNER, target["workspace_id"], first["run_id"])["requester"] == CONTRIBUTOR.actor


async def test_original_operation_recovery_does_not_refreeze_or_steal_writer(ops):
    target = await prepared(ops)
    first = create(ops, target)
    await act(ops, target, "pm.message.append", {"content": "New source"})
    recovered = create(ops, target, operation_id=first["operation_id"], expected_version=first["thread_version"])
    assert recovered == first
    with pytest.raises(OperationError, match="Original PM run intent differs"):
        create(ops, target, operation_id=first["operation_id"], expected_version=first["thread_version"], purpose="A different turn")


async def test_lost_ack_keeps_input_key_writer_and_original_cursor(ops):
    target = await prepared(ops)
    first = create(ops, target)
    send(ops, first, lost_ack=True)
    assert pm_runs.get(ops.db, first["run_id"])["state"] == "unknown"
    with pytest.raises(OperationError, match="PM_WRITER_BUSY"):
        create(ops, target)
    with pytest.raises(OperationError, match="PM_EFFECT_UNKNOWN"):
        pm_runs.fail(ops.journal, first["run_id"], 1, "Reply lost", before_send=True)
    with pytest.raises(OperationError, match="PM_EFFECT_UNKNOWN"):
        pm_runs.complete_preparation(ops.journal, first["run_id"], 1, worktree_binding=None, snapshot=None)
    with pytest.raises(OperationError, match="cannot change"):
        pm_runs.set_send_cursor(ops.journal, first["run_id"], 1, {"epoch": "epoch-1", "seq": 20})
    with pytest.raises(OperationError, match="Original PM receipt differs"):
        pm_runs.receipt(ops.journal, first["run_id"], 1, "send", "sent", {"input_key": "new-input"})


async def test_native_events_cursor_atomic_and_replay_identity_immutable(ops):
    target = await prepared(ops)
    first = create(ops, target)
    send(ops, first)
    good = terminal()
    bad = terminal(seq=12, session="foreign-session")
    with pytest.raises(OperationError, match="PM_REPLAY_INVALID"):
        replay(ops, first, [good, bad])
    assert pm_runs.events(ops.db, first["run_id"]) == []
    assert pm_runs.get(ops.db, first["run_id"])["cursor"]["seq"] == 10
    replay(ops, first, [good])
    replay(ops, first, [good])
    assert pm_runs.events(ops.db, first["run_id"]) == [good]
    with pytest.raises(OperationError, match="PM_REPLAY_CONFLICT"):
        replay(ops, first, [terminal(content="changed")])
    assert pm_runs.events(ops.db, first["run_id"]) == [good]


@pytest.mark.parametrize("kind", ["epoch", "missing", "snapshot", "more", "pending", "error", "aborted"])
async def test_incomplete_native_evidence_never_imports_normal_completion(ops, kind):
    target = await prepared(ops)
    first = create(ops, target)
    send(ops, first)
    if kind == "epoch":
        replay(ops, first, cursor={"epoch": "epoch-2", "seq": 11})
    elif kind == "missing":
        replay(ops, first, [terminal(seq=12)])
    elif kind == "snapshot":
        replay(ops, first, mode="snapshot", gap=True, reason="expired")
    elif kind == "more":
        replay(ops, first, has_more=True)
    elif kind == "pending":
        replay(ops, first, [])
    else:
        replay(ops, first, [terminal(reason=kind)])
    with pytest.raises(OperationError, match="PM_COMPLETION_UNPROVEN"):
        import_reply(ops, first)
    assert read(ops, target)["messages"] == []
    with pytest.raises(OperationError, match="PM_WRITER_BUSY"):
        create(ops, target)


@pytest.mark.parametrize("reason,state", [("error", "failed"), ("aborted", "aborted")])
async def test_gap_free_native_failure_releases_without_assistant_fabrication(ops, reason, state):
    target = await prepared(ops)
    first = create(ops, target)
    send(ops, first)
    replay(ops, first, [terminal(reason=reason)])
    result = pm_runs.fail(ops.journal, first["run_id"], 1, "Native runner stopped")
    assert result["status"] == state
    assert read(ops, target)["messages"] == []
    assert create(ops, target)["generation"] == 2


async def test_completion_import_is_assistant_attributed_idempotent_and_keeps_later_human_input(ops):
    target = await prepared(ops)
    await record(ops, target)
    first = create(ops, target)
    send(ops, first, lost_ack=True)
    human = await act(ops, target, "pm.message.append", {"content": "Later input while native turn runs"})
    replay(ops, first)
    result = import_reply(ops, first)
    doc = read(ops, target)
    assert result["state"] == "completed" and result["context"] == first["context"]
    assert doc["messages"][0]["message_id"] == human["message_id"]
    assistant = doc["messages"][1]
    assert assistant["role"] == "assistant" and assistant["actor"] == first["execution_authority"]["actor"]
    assert assistant["actor"] != OWNER.actor and assistant["provenance"]["model"] == "observed-model"
    assert assistant["provenance"]["context_digest"] == first["context_digest"]
    assert assistant["provenance"]["source_refs"]["coverage"] == first["context"]["coverage"]
    assert doc["thread"]["mode"] == "idea" and doc["inputs"][0]["status"] == "agent_assumption"
    second = create(ops, target)
    assert second["generation"] == 2
    assert import_reply(ops, first) == result
    assert len(read(ops, target)["messages"]) == 2
    with pytest.raises(OperationError, match="PM_IMPORT_CONFLICT"):
        import_reply(ops, first, content="Changed late reply")
    with pytest.raises(OperationError, match="PM_WRITER_CHANGED"):
        replay(ops, first)


async def test_native_content_cannot_be_substituted_during_import(ops):
    target = await prepared(ops)
    first = create(ops, target)
    send(ops, first)
    replay(ops, first)
    with pytest.raises(OperationError, match="PM_IMPORT_CONFLICT"):
        import_reply(ops, first, content="Pretend the user adopted and deployed it")
    assert read(ops, target)["messages"] == []


async def test_human_identity_cannot_be_used_for_pm_execution(ops):
    target = await prepared(ops)
    with pytest.raises(OperationError, match="PM_AUTHORITY_INVALID"):
        create(ops, target, execution_authority={"actor": OWNER.actor, "controller_id": pm_workspace.controller_for(ops.db, OWNER)})
    with pytest.raises(OperationError, match="VERSION_CONFLICT"):
        create(ops, target, expected_version=0)


async def test_run_read_cannot_cross_workspace_even_when_both_visible(ops):
    target = await prepared(ops)
    other = await prepared(ops)
    first = create(ops, target)
    with pytest.raises(OperationError, match="PM_RUN_NOT_FOUND"):
        pm_runs.read(ops, OWNER, other["workspace_id"], first["run_id"])
    assert pm_runs.list_runs(ops, OWNER, target["workspace_id"])["runs"][0]["run_id"] == first["run_id"]
    summary = pm_runs.list_runs(ops, OWNER, target["workspace_id"])["runs"][0]
    assert "context" not in summary and "receipts" not in summary and summary["profile"]["mode"] == "idea"
