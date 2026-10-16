"""Tests for E04 Phase B1: session_results store, canonical digest, changes summary, and AttemptResultCollector."""

from __future__ import annotations

import asyncio
import json
import sqlite3
import time

import pytest

from bat_agent_connector import (
    api_auth,
    channels,
    pm_access,
    pm_attempt_results,
    pm_work_start,
    registry,
    service,
)
from bat_agent_connector.api_auth import Principal
from bat_agent_connector.errors import BatError, InvokeTimeout
from bat_agent_connector.operations import OperationService
from bat_agent_connector.pm_attempt_results import (
    DECODER_VERSION,
    MAX_RESULT_DOCUMENT_BYTES,
    RESULT_DOC_SCHEMA,
    AttemptResultCollector,
    canonical_digest_bytes,
    collect_changes_summary,
    collect_head_observation,
    compute_digest,
    receipts,
    record_receipt,
)
from tests.conftest import controller_scope
from tests.operation_helpers import settle_operations
from tests.test_checkpoints import RealGitLog
from tests.test_pm_package_handoff import (
    dispatch_runner,
)
from tests.test_pm_package_handoff import (
    pm as handoff_pm_fixture,
)
from tests.test_pm_package_handoff import (
    ready as handoff_ready_fixture,
)
from tests.test_pm_package_handoff import (
    world as handoff_world_fixture,
)
from tests.test_pm_plan_operations import OWNER as PLAN_OWNER
from tests.test_pm_work_packages import action as pkg_action
from tests.test_pm_work_start import STARTER_OWNER, _start_envelope

world = handoff_world_fixture
pm = handoff_pm_fixture
ready = handoff_ready_fixture

READER_BOB = Principal("bob", frozenset({"observe"}))


class OverridableGitLog(RealGitLog):
    """Uses explicit per-path overrides when set, otherwise falls back to RealGitLog."""

    def __contains__(self, cwd) -> bool:
        return dict.__contains__(self, cwd) or super().__contains__(cwd)

    def __getitem__(self, cwd):
        if dict.__contains__(self, cwd):
            return dict.__getitem__(self, cwd)
        return super().__getitem__(cwd)


@pytest.fixture
async def start_ready(ready, mock):
    w = ready
    mock.git_logs = OverridableGitLog()
    dispatch_runner(w)
    ops = w["d"].ops
    pm_work_start.install(ops)
    pm_attempt_results.install(ops)
    api_auth.issue(ops.db, PLAN_OWNER.actor, ["observe", "manage", "start", "operate"])
    return w


async def _advance_start_public(ops: OperationService, op_id: str, rounds: int = 8) -> dict:
    """Advance work.start and its children through public OperationService APIs only."""
    for _ in range(rounds):
        ops.wake(op_id)
        await settle_operations(ops)
        cur = ops.get(op_id)
        if cur["status"] in {"succeeded", "failed", "cancelled", "needs_attention"}:
            break
    cur = ops.get(op_id)
    assert cur["error_code"] != "INTERNAL", [(s["name"], s["error"]) for s in cur["steps"] if s["error"]]
    return cur


async def _start_managed_attempt(w, mock, *, key: str = "e04-b1-start", echo_send: bool = True) -> dict:
    """Drive a real work.start to a started managed session on mock BAT."""
    mock.echo_sends = echo_send
    ops = w["d"].ops
    env = await _start_envelope(w, key=key)
    parent, fresh = ops.create(STARTER_OWNER, **env)
    assert fresh is True
    done = await _advance_start_public(ops, parent["operation_id"])
    assert done["status"] == "succeeded", done
    res = done["result"]
    assert res["stage"] == "started"
    handoff_id = res["children"]["handoff"]
    handoff = ops.get(handoff_id)
    hres = handoff["result"]
    sid = hres["session_id"]
    wt = hres["worktree_path"]
    mid = hres["message_id"]
    base_sha = hres["source_sha"]

    # Ensure mock workspace lists this session so inventory refresh observes it.
    if not any(t.get("id") == sid for t in mock.ws_doc.get("terminals", [])):
        mock.ws_doc["terminals"].append(
            {
                "id": sid,
                "workspaceId": "ws-1",
                "title": "Managed Attempt Session",
                "type": "terminal",
                "cwd": wt,
                "worktreePath": wt,
                "worktreeBranch": hres["branch"],
                "agentPreset": "claude-code",
                "sdkSessionId": f"sdk-{sid[:8]}",
                "model": "claude-sonnet-4-5",
            }
        )
    return {
        "workspace_id": w["workspace_id"],
        "work_item_id": w["target"]["work_item_id"],
        "attempt_id": res["attempt_id"],
        "start_operation_id": parent["operation_id"],
        "handoff_operation_id": handoff_id,
        "host": hres["host"],
        "session_id": sid,
        "worktree_path": wt,
        "turn_marker": mid,
        "base_sha": base_sha,
    }


def _set_turn_messages(
    mock,
    sid: str,
    turn_marker: str,
    *,
    streaming: bool = False,
    pending_ask: dict | None = None,
    assistant_id: str = "m-asst-1",
    assistant_text: str = "Implemented credential validation and all unit tests pass.",
    include_echo: bool = True,
    extra_items: list[dict] | None = None,
) -> None:
    msgs: list[dict] = []
    if include_echo:
        msgs.append(
            {
                "id": turn_marker,
                "role": "user",
                "content": "[batc] Implement credential validation and tests.",
                "timestamp": 1_790_001_000_000,
            }
        )
    if extra_items:
        msgs.extend(extra_items)
    msgs.append(
        {
            "id": assistant_id,
            "role": "assistant",
            "content": assistant_text,
            "timestamp": 1_790_001_005_000,
        }
    )
    mock.metas[sid] = {
        **(mock.metas.get(sid) or {"cwd": "/srv/demo"}),
        "isStreaming": streaming,
        "model": "claude-sonnet-4-5",
        "numTurns": 1,
        "contextTokens": 1234,
        "contextWindow": 200000,
        "lastDataAt": 1_790_001_005_000,
    }
    mock.states[sid] = {
        "isStreaming": streaming,
        "messages": msgs,
        "pendingAskUser": pending_ask,
        "pendingPermission": None,
    }


def test_schema_idempotent_no_user_version_and_append_only_triggers(start_ready):
    ops = start_ready["d"].ops
    db = ops.db
    before_ver = db.execute("PRAGMA user_version").fetchone()[0]

    # Installing twice is a no-op and does not change user_version.
    pm_attempt_results.install(ops)
    pm_attempt_results.install(ops)
    pm_attempt_results.schema(ops.journal)
    after_ver = db.execute("PRAGMA user_version").fetchone()[0]
    assert after_ver == before_ver == 3
    assert pm_attempt_results.installed(db) is True

    doc = pm_attempt_results.build_result_document(
        result_id="res_" + "1" * 32,
        turn_marker="batc-op_" + "a" * 32,
        last_message_id="m-1",
        slice_items=[{"id": "m-1", "role": "assistant", "tool": "", "text": "Done"}],
        boundary="turn_done",
    )
    stored, inserted = record_receipt(
        ops.journal,
        workspace_id=start_ready["workspace_id"],
        attempt_id="att_" + "b" * 32,
        source_key="bat:h1:s1",
        source_generation="inc1:1700000000.0",
        turn_marker="batc-op_" + "a" * 32,
        last_message_id="m-1",
        digest="sha256:" + "c" * 64,
        document=doc,
        observed_at=1700000001.0,
        operation_id="op_" + "a" * 32,
        result_id="res_" + "1" * 32,
    )
    assert inserted is True
    assert stored["result_id"] == "res_" + "1" * 32

    # Append-only triggers abort UPDATE and DELETE.
    with pytest.raises(sqlite3.IntegrityError, match="session_results is append-only"):
        db.execute("UPDATE session_results SET digest='sha256:00' WHERE result_id=?", (stored["result_id"],))
    with pytest.raises(sqlite3.IntegrityError, match="session_results is append-only"):
        db.execute("DELETE FROM session_results WHERE result_id=?", (stored["result_id"],))


async def test_session_read_turn_slice_false_is_byte_for_byte_unchanged(fleet_factory, mock):
    fleet = fleet_factory()
    mock.states["sess-claude-0001"]["messages"][2]["id"] = "batc-op_test"
    # Baseline call without turn_slice keyword vs explicit turn_slice=False.
    baseline = await service.session_read(
        fleet, "h1", "sess-claude-0001", after="batc-op_test", include_tools=True
    )
    explicit_false = await service.session_read(
        fleet, "h1", "sess-claude-0001", after="batc-op_test", include_tools=True, turn_slice=False
    )
    assert "_turn_slice" not in baseline
    assert "_turn_slice" not in explicit_false
    assert json.dumps(baseline, sort_keys=True) == json.dumps(explicit_false, sort_keys=True)

    # With turn_slice=True, popping _turn_slice leaves the rest of the response identical to baseline.
    with_slice = await service.session_read(
        fleet, "h1", "sess-claude-0001", after="batc-op_test", include_tools=True, turn_slice=True
    )
    slice_payload = with_slice.pop("_turn_slice")
    assert json.dumps(with_slice, sort_keys=True) == json.dumps(baseline, sort_keys=True)
    assert slice_payload["complete"] is True
    assert slice_payload["reason"] is None
    assert [item["id"] for item in slice_payload["items"]] == [f"m{i}" for i in range(3, 10)] + ["tool1"]


async def test_mid_turn_writes_nothing_and_turn_end_records_one_receipt_with_connector_actor(start_ready, mock):
    w = start_ready
    ops = w["d"].ops
    collector = w["d"].result_collector
    att = await _start_managed_attempt(w, mock, key="e04-mid-and-end")
    sid = att["session_id"]
    marker = att["turn_marker"]

    # 1. Mid-turn with isStreaming=True writes nothing.
    _set_turn_messages(mock, sid, marker, streaming=True)
    res_streaming = await collector.collect_host("h1")
    assert res_streaming == []
    assert ops.db.execute("SELECT COUNT(*) FROM session_results").fetchone()[0] == 0

    # 2. Mid-turn with isStreaming=False but pendingAskUser present writes nothing.
    _set_turn_messages(
        mock,
        sid,
        marker,
        streaming=False,
        pending_ask={"id": "ask-1", "questions": [{"question": "Which option?"}]},
    )
    res_pending = await collector.collect_host("h1")
    assert res_pending == []
    assert ops.db.execute("SELECT COUNT(*) FROM session_results").fetchone()[0] == 0

    # 3. Turn end (isStreaming=False, pending=None, turn_done=True) writes exactly one receipt.
    _set_turn_messages(mock, sid, marker, streaming=False, pending_ask=None)
    res_done = await collector.collect_host("h1")
    assert len(res_done) == 1
    row = res_done[0]
    assert row["workspace_id"] == att["workspace_id"]
    assert row["attempt_id"] == att["attempt_id"]
    assert row["source_key"] == f"bat:h1:{sid}"
    assert row["turn_marker"] == marker
    assert row["last_message_id"] == "m-asst-1"
    assert row["operation_id"] == att["handoff_operation_id"]
    assert row["document"]["schema_version"] == RESULT_DOC_SCHEMA
    assert row["document"]["boundary"] == "turn_done"
    assert row["document"]["latest_assistant_summary"] == {
        "text": "Implemented credential validation and all unit tests pass.",
        "message_id": "m-asst-1",
        "ts": row["document"]["latest_assistant_summary"]["ts"],
        "claim_status": "self_reported",
    }
    assert row["document"]["usage"]["context_tokens"] == 1234
    assert row["document"]["usage"]["context_window"] == 200000
    assert row["document"]["usage"]["num_turns"] == 1
    assert row["document"]["usage"]["input_tokens"] == "unknown"
    assert row["document"]["usage"]["duration_seconds"] == "unknown"
    assert "cost_usd" not in row["document"]["usage"]

    events = ops.db.execute(
        "SELECT * FROM api_events WHERE resource_type='pm_work_attempt' AND resource_id=? AND kind='work.attempt.result_recorded'",
        (att["attempt_id"],),
    ).fetchall()
    assert len(events) == 1
    assert events[0]["actor"] == "connector"
    ev_page = ops.journal.api_events(0, 50, resource_type="pm_work_attempt", resource_id=att["attempt_id"])
    recorded_evs = [e for e in ev_page["events"] if e["kind"] == "work.attempt.result_recorded"]
    assert len(recorded_evs) == 1
    assert recorded_evs[0]["actor"] == "connector"
    assert recorded_evs[0]["body"]["result_id"] == row["result_id"]
    assert recorded_evs[0]["body"]["digest"] == row["digest"]
    assert pm_access.event_visible(ops.db, STARTER_OWNER, recorded_evs[0]) is True


async def test_no_reader_fixes_receipt_via_inventory_refresh_and_survives_restart(start_ready, mock):
    w = start_ready
    ops = w["d"].ops
    inv = w["d"].inventory
    att = await _start_managed_attempt(w, mock, key="e04-no-reader-restart")
    sid = att["session_id"]
    marker = att["turn_marker"]
    _set_turn_messages(mock, sid, marker, streaming=False)

    # Trigger via Inventory._refresh_scheduled with zero read API calls.
    refresh_res = await inv._refresh_scheduled("h1")
    assert refresh_res["reachable"] is True

    stored = receipts(ops.db, att["workspace_id"], att["attempt_id"])
    assert len(stored["receipts"]) == 1
    assert stored["has_more"] is False
    first_id = stored["receipts"][0]["result_id"]

    # Repeat inventory pass adds no row and no event.
    ev_count_before = ops.db.execute(
        "SELECT COUNT(*) FROM api_events WHERE kind='work.attempt.result_recorded'"
    ).fetchone()[0]
    await inv._refresh_scheduled("h1")
    assert ops.db.execute("SELECT COUNT(*) FROM session_results").fetchone()[0] == 1
    assert (
        ops.db.execute("SELECT COUNT(*) FROM api_events WHERE kind='work.attempt.result_recorded'").fetchone()[0]
        == ev_count_before
    )

    # Simulate restart: construct a fresh OperationService and fresh AttemptResultCollector over the same journal.
    ops2 = OperationService(ops.journal, actions=list(ops.actions.values()))
    ops2.context.update(ops.context)
    pm_work_start.install(ops2)
    pm_attempt_results.install(ops2)
    collector2 = AttemptResultCollector(ops2)

    # Even with empty memo on restart, the restart backfill sees the existing receipt at the same position+digest
    # and writes no duplicate row or event.
    rechecked = await collector2.collect_host("h1")
    assert rechecked == []
    after_restart = receipts(ops2.db, att["workspace_id"], att["attempt_id"])
    assert len(after_restart["receipts"]) == 1
    assert after_restart["receipts"][0]["result_id"] == first_id


async def test_two_racing_collectors_produce_one_row_and_identical_digest_bytes(start_ready, mock, monkeypatch):
    w = start_ready
    ops = w["d"].ops
    att = await _start_managed_attempt(w, mock, key="e04-racing-collectors")  # gitleaks:allow (test idempotency key)
    sid = att["session_id"]
    marker = att["turn_marker"]
    _set_turn_messages(
        mock,
        sid,
        marker,
        streaming=False,
        assistant_id="m-race-1",
        assistant_text="Deterministic turn output for canonical digest check.",
    )

    # Two independent collector instances sharing the same database.
    c1 = AttemptResultCollector(ops)
    c2 = AttemptResultCollector(ops)

    both_reached_head = asyncio.Event()
    arrived = 0
    orig_head_obs = pm_attempt_results.collect_head_observation

    async def synchronized_head_obs(*args, **kwargs):
        nonlocal arrived
        arrived += 1
        if arrived >= 2:
            both_reached_head.set()
        await both_reached_head.wait()
        return await orig_head_obs(*args, **kwargs)

    monkeypatch.setattr(pm_attempt_results, "collect_head_observation", synchronized_head_obs)

    r1, r2 = await asyncio.gather(c1.collect_host("h1"), c2.collect_host("h1"))
    # Exactly one of the two racing inserts wins; the other is ignored by UNIQUE(..., digest).
    assert len(r1) + len(r2) == 1
    assert c1.last_digest_bytes is not None
    assert c1.last_digest_bytes == c2.last_digest_bytes

    rows = ops.db.execute(
        "SELECT * FROM session_results WHERE workspace_id=? AND attempt_id=?",
        (att["workspace_id"], att["attempt_id"]),
    ).fetchall()
    assert len(rows) == 1
    evs = ops.db.execute(
        "SELECT * FROM api_events WHERE resource_type='pm_work_attempt' AND resource_id=? AND kind='work.attempt.result_recorded'",
        (att["attempt_id"],),
    ).fetchall()
    assert len(evs) == 1


def test_deterministic_canonical_digest_bytes_vector():
    """Verify the exact canonical UTF-8 digest bytes for a known turn vector."""
    raw_items = [
        {
            "id": "tool-1",
            "ts": "2026-10-15T12:00:01-04:00",
            "role": "tool",
            "tool": "Bash",
            "text": "Bash {\"command\": \"pytest -q\"}",
            "status": "done",
            "completed_at": "2026-10-15T12:00:02-04:00",
            "duration_ms": 420,
        },
        {
            "id": "m-asst-1",
            "ts": "2026-10-15T12:00:03-04:00",
            "role": "assistant",
            "text": "All unit tests pass.",
            "model": "claude-sonnet-4-5",
            "agent": "claude",
        },
    ]
    # Changing machine-local timestamps, status, duration_ms, model or agent must not change digest bytes.
    mutated_items = [
        {
            **raw_items[0],
            "ts": "2026-10-16T00:00:01+08:00",
            "status": "running",
            "completed_at": "2026-10-16T00:00:99+08:00",
            "duration_ms": 9999,
            "denied": True,
        },
        {
            **raw_items[1],
            "ts": "2026-10-16T00:00:03+08:00",
            "model": "other-model",
            "agent": "other-agent",
            "deferred": True,
        },
    ]
    b1 = canonical_digest_bytes(
        source_key="bat:h1:sess-0001",
        source_generation="0123456789abcdef0123456789abcdef:1790000000.0",
        turn_marker="batc-op_11111111111111111111111111111111",
        last_message_id="m-asst-1",
        turn_items=raw_items,
    )
    b2 = canonical_digest_bytes(
        source_key="bat:h1:sess-0001",
        source_generation="0123456789abcdef0123456789abcdef:1790000000.0",
        turn_marker="batc-op_11111111111111111111111111111111",
        last_message_id="m-asst-1",
        turn_items=mutated_items,
    )
    assert b1 == b2
    expected_bytes = (
        b'{"decoder_version":"bat_ipc_session_v1","last_message_id":"m-asst-1",'
        b'"source_generation":"0123456789abcdef0123456789abcdef:1790000000.0",'
        b'"source_key":"bat:h1:sess-0001",'
        b'"turn_items":['
        b'{"id":"tool-1","role":"tool","text":"Bash {\\"command\\": \\"pytest -q\\"}","tool":"Bash"},'
        b'{"id":"m-asst-1","role":"assistant","text":"All unit tests pass.","tool":""}'
        b'],"turn_marker":"batc-op_11111111111111111111111111111111"}'
    )
    assert b1 == expected_bytes
    d1 = compute_digest(
        source_key="bat:h1:sess-0001",
        source_generation="0123456789abcdef0123456789abcdef:1790000000.0",
        turn_marker="batc-op_11111111111111111111111111111111",
        last_message_id="m-asst-1",
        turn_items=raw_items,
    )
    assert d1 == "sha256:b941973cd7876867ca83e4846d150102bd2a1ce9f32979cb466f576466fa0d0c"


async def test_same_position_different_digest_stores_both_and_respects_decoder_version(start_ready, mock):
    w = start_ready
    ops = w["d"].ops
    att = await _start_managed_attempt(w, mock, key="e04-conflict")
    sid = att["session_id"]
    marker = att["turn_marker"]

    # First collection at (marker, m-asst-1).
    _set_turn_messages(mock, sid, marker, streaming=False, assistant_id="m-asst-1", assistant_text="First version")
    c1 = AttemptResultCollector(ops)
    r1 = await c1.collect_host("h1")
    assert len(r1) == 1

    # Mutate text of the same message ID m-asst-1 with the same decoder_version -> stores second row + conflict event.
    _set_turn_messages(mock, sid, marker, streaming=False, assistant_id="m-asst-1", assistant_text="Mutated version")
    c2 = AttemptResultCollector(ops)
    r2 = await c2.collect_host("h1")
    assert len(r2) == 1
    assert r2[0]["digest"] != r1[0]["digest"]

    stored = receipts(ops.db, att["workspace_id"], att["attempt_id"])
    assert len(stored["receipts"]) == 2
    assert {x["digest"] for x in stored["receipts"]} == {r1[0]["digest"], r2[0]["digest"]}

    conflicts = ops.db.execute(
        "SELECT * FROM api_events WHERE resource_type='pm_work_attempt' AND resource_id=? AND kind='work.attempt.result_conflict'",
        (att["attempt_id"],),
    ).fetchall()
    assert len(conflicts) == 1
    assert conflicts[0]["actor"] == "connector"
    body = json.loads(conflicts[0]["body"])
    assert body["conflicting_digests"] == [r1[0]["digest"]]
    assert body["decoder_version"] == DECODER_VERSION

    # A row at the same position with a DIFFERENT decoder_version does NOT emit a conflict event.
    _set_turn_messages(mock, sid, marker, streaming=False, assistant_id="m-asst-1", assistant_text="Decoder v2 text")
    c3 = AttemptResultCollector(ops, decoder_version="bat_ipc_session_v2")
    r3 = await c3.collect_host("h1")
    assert len(r3) == 1
    assert len(receipts(ops.db, att["workspace_id"], att["attempt_id"])["receipts"]) == 3
    conflicts_after = ops.db.execute(
        "SELECT * FROM api_events WHERE resource_type='pm_work_attempt' AND resource_id=? AND kind='work.attempt.result_conflict'",
        (att["attempt_id"],),
    ).fetchall()
    assert len(conflicts_after) == 1


async def test_commit_after_turn_end_leaves_digest_unchanged_and_writes_no_row(start_ready, mock):
    w = start_ready
    ops = w["d"].ops
    att = await _start_managed_attempt(w, mock, key="e04-commit-after-turn")
    sid = att["session_id"]
    wt = att["worktree_path"]
    marker = att["turn_marker"]

    _set_turn_messages(mock, sid, marker, streaming=False, assistant_id="m-asst-1", assistant_text="Turn finished.")
    c1 = AttemptResultCollector(ops)
    r1 = await c1.collect_host("h1")
    assert len(r1) == 1

    # Add a new commit in the worktree after turn-end while conversation messages stay identical.
    new_head = "e" * 40
    mock.git_logs[wt] = [
        {"hash": new_head, "author": "dev", "date": "2026-10-15", "message": "post-turn commit"},
        {"hash": att["base_sha"], "author": "dev", "date": "2026-10-15", "message": "base commit"},
    ]
    c2 = AttemptResultCollector(ops)
    r2 = await c2.collect_host("h1")
    assert r2 == []
    assert c2.last_digest_bytes == c1.last_digest_bytes
    assert ops.db.execute("SELECT COUNT(*) FROM session_results").fetchone()[0] == 1
    assert (
        ops.db.execute("SELECT COUNT(*) FROM api_events WHERE kind='work.attempt.result_conflict'").fetchone()[0] == 0
    )


async def test_git_status_empty_error_and_nonempty_on_worktree_path_only(start_ready, mock):
    w = start_ready
    att = await _start_managed_attempt(w, mock, key="e04-git-status")
    sid = att["session_id"]
    wt = att["worktree_path"]
    head_sha = "f" * 40
    mock.git_logs[wt] = [
        {"hash": head_sha, "author": "dev", "date": "2026-10-15", "message": "head"},
        {"hash": att["base_sha"], "author": "dev", "date": "2026-10-15", "message": "base"},
    ]
    # Put dirty status on meta.cwd ("/srv/demo") to verify collector never queries meta.cwd.
    mock.metas[sid]["cwd"] = "/srv/demo"
    mock.git_status["/srv/demo"] = [{"file": "wrong-cwd.py", "status": "M"}]

    client = w["d"].result_collector.fleet.client("h1")

    # 1. Empty list [] -> unknown, never 0 or clean; fixed_result_commit still records head_sha.
    mock.git_status[wt] = []
    obs_empty = await collect_head_observation(
        client, wt, base_sha=att["base_sha"], result_id="res_test1", observed_at=1700000000.0
    )
    assert obs_empty["uncommitted"] == {
        "count": "unknown",
        "files": "unknown",
        "evidence": "unproven",
        "reason": "bat_empty_status_not_clean_proof",
    }
    assert obs_empty["fixed_result_commit"] == {
        "commit_sha": head_sha,
        "source_kind": "turn_end_observation",
        "source_id": "res_test1",
        "recorded_by": "connector",
        "uncommitted": "unknown",
        "recorded_at": 1700000000.0,
    }

    # 2. BatError on git:status -> unknown with reason, fixed_result_commit still records head_sha.
    mock.handlers["git:status"] = lambda _p: (_ for _ in ()).throw(BatError("git status failed"))
    try:
        obs_err = await collect_head_observation(
            client, wt, base_sha=att["base_sha"], result_id="res_test2", observed_at=1700000001.0
        )
    finally:
        mock.handlers.pop("git:status", None)
    assert obs_err["uncommitted"]["count"] == "unknown"
    assert obs_err["uncommitted"]["reason"] == "bat_empty_status_not_clean_proof"
    assert obs_err["fixed_result_commit"]["commit_sha"] == head_sha
    assert obs_err["fixed_result_commit"]["uncommitted"] == "unknown"

    # 3. Non-empty list on worktree_path -> exact count and files with diff_available=False.
    mock.git_status[wt] = [{"file": "src/auth.py", "status": "M"}, {"file": "tests/test_auth.py", "status": "A"}]
    obs_dirty = await collect_head_observation(
        client, wt, base_sha=att["base_sha"], result_id="res_test3", observed_at=1700000002.0
    )
    assert obs_dirty["uncommitted"]["count"] == 2
    assert obs_dirty["uncommitted"]["evidence"] == "bat_git_status_nonempty"
    assert obs_dirty["uncommitted"]["reason"] is None
    assert len(obs_dirty["uncommitted"]["files"]) == 2
    assert obs_dirty["uncommitted"]["files"][0]["diff_available"] is False
    assert obs_dirty["uncommitted"]["files"][0]["unavailable_reason"] == "uncommitted_diff_not_supported_by_bat"
    assert obs_dirty["fixed_result_commit"]["commit_sha"] == head_sha
    assert obs_dirty["fixed_result_commit"]["uncommitted"] == 2

    # Verify every git:status invoke used worktree_path, never /srv/demo.
    status_cwds = [inv["params"]["cwd"] for inv in mock.invokes if inv["channel"] == "git:status"]
    assert status_cwds and all(c == wt for c in status_cwds)


async def test_large_diff_unavailable_stats_and_commit_range_limits(start_ready, mock):
    w = start_ready
    att = await _start_managed_attempt(w, mock, key="e04-large-diff")
    wt = att["worktree_path"]
    base_sha = att["base_sha"]
    c1 = "1" * 40
    c2 = "2" * 40

    mock.git_logs[wt] = [
        {"hash": c2, "author": "dev", "date": "2026-10-15", "message": "commit 2"},
        {"hash": c1, "author": "dev", "date": "2026-10-15", "message": "commit 1"},
        {"hash": base_sha, "author": "dev", "date": "2026-10-15", "message": "base"},
    ]
    mock.commit_files = {
        c1: [{"file": "small.py", "status": "A"}, {"file": "huge.bin", "status": "A"}],
        c2: [{"file": "small.py", "status": "M"}],
    }
    mock.commit_diffs = {
        (c1, "small.py"): "diff --git a/small.py b/small.py\n--- /dev/null\n+++ b/small.py\n@@ -0,0 +1,2 @@\n+line1\n+line2\n",
        (c1, "huge.bin"): "",  # Empty string models BAT diff exceeding pipe buffer
        (c2, "small.py"): "diff --git a/small.py b/small.py\n--- a/small.py\n+++ b/small.py\n@@ -1,2 +1,3 @@\n line1\n-line2\n+line2_mod\n+line3\n",
    }

    client = w["d"].result_collector.fleet.client("h1")
    summary = await collect_changes_summary(client, wt, base_sha=base_sha, result_id="res_diff1")
    assert summary["aggregation"] == "per_commit"
    assert summary["commits"] == [c1, c2]
    assert summary["unavailable"] == ["huge.bin"]
    assert summary["complete"] is False
    assert "diff_exceeded_pipe_limit" in summary["reasons"]
    assert summary["additions"] == "unknown"
    assert summary["deletions"] == "unknown"

    by_path = {f["path"]: f for f in summary["files"]}
    assert by_path["small.py"] == {
        "path": "small.py",
        "status": "M",
        "additions": 4,
        "deletions": 1,
        "commits_touched": [c1, c2],
    }
    assert by_path["huge.bin"] == {
        "path": "huge.bin",
        "status": "A",
        "additions": "unknown",
        "deletions": "unknown",
        "commits_touched": [c1],
    }

    # Base not reached within log (< 30 commits) -> base_commit_not_ancestor, complete=False, no sum.
    mock.git_logs[wt] = [
        {"hash": c2, "author": "dev", "date": "2026-10-15", "message": "commit 2"},
        {"hash": c1, "author": "dev", "date": "2026-10-15", "message": "commit 1"},
    ]
    not_anc = await collect_changes_summary(client, wt, base_sha=base_sha, result_id="res_diff2")
    assert not_anc["complete"] is False
    assert not_anc["reason"] == "base_commit_not_ancestor"
    assert not_anc["additions"] == "unknown"
    assert not_anc["deletions"] == "unknown"
    assert not_anc["files"] == []

    # More than 30 commits without reaching base -> commit_limit_exceeded, complete=False, no sum.
    mock.git_logs[wt] = [
        {"hash": f"{i:040x}", "author": "dev", "date": "2026-10-15", "message": f"c{i}"}
        for i in range(1, 33)
    ]
    over_limit = await collect_changes_summary(client, wt, base_sha=base_sha, result_id="res_diff3")
    assert over_limit["complete"] is False
    assert over_limit["reason"] == "commit_limit_exceeded"
    assert over_limit["additions"] == "unknown"
    assert over_limit["deletions"] == "unknown"
    assert over_limit["files"] == []

    # Budget exceeded during git:diff -> lists remaining files in unavailable with changeset_time_budget_exceeded.
    mock.git_logs[wt] = [
        {"hash": c1, "author": "dev", "date": "2026-10-15", "message": "commit 1"},
        {"hash": base_sha, "author": "dev", "date": "2026-10-15", "message": "base"},
    ]
    never_set = asyncio.Event()

    async def slow_git_diff(params):
        if params.get("filePath") == "huge.bin":
            await never_set.wait()
        return mock.commit_diffs.get((params.get("commitHash"), params.get("filePath")), "")

    mock.handlers["git:diff"] = slow_git_diff
    try:
        timed_out = await collect_changes_summary(client, wt, base_sha=base_sha, result_id="res_diff4", budget_s=0.02)
    finally:
        never_set.set()
        mock.handlers.pop("git:diff", None)
    assert timed_out["complete"] is False
    assert "changeset_time_budget_exceeded" in timed_out["reasons"]
    assert "huge.bin" in timed_out["unavailable"]
    by_path_to = {f["path"]: f for f in timed_out["files"]}
    assert by_path_to["huge.bin"]["additions"] == "unknown"
    assert by_path_to["huge.bin"]["deletions"] == "unknown"


async def test_manual_foreign_uncertain_and_registry_mismatch_sessions_never_read(start_ready, mock, monkeypatch):
    w = start_ready
    ops = w["d"].ops
    collector = w["d"].result_collector

    # 1. Start a real attempt, then corrupt registry start_operation_id -> registry mismatch.
    att_mismatch = await _start_managed_attempt(w, mock, key="e04-reg-mismatch")  # gitleaks:allow (test idempotency key)
    sid_mismatch = att_mismatch["session_id"]
    _set_turn_messages(mock, sid_mismatch, att_mismatch["turn_marker"], streaming=False)
    registry.update("h1", sid_mismatch, start_operation_id="op_" + "9" * 32)

    # 2. Create a second work item and start an attempt whose handoff stops in 'uncertain' via lost ACK.
    from bat_agent_connector import work_items

    base_work = work_items._get_item(ops.db, w["target"]["work_item_id"])
    wi_uncertain = (
        await pkg_action(
            w,
            "work_item.create",
            {"project_id": base_work["project_id"]},
            {"title": "Uncertain slice", "goal": "Goal", "acceptance": "Acceptance"},
        )
    )["work_item_id"]
    client = w["d"].fleet.client("h1")
    orig_invoke = client.invoke
    lost_once = False
    uncertain_sid_holder: list[str] = []

    async def lose_send_ack(name, *args, **kwargs):
        nonlocal lost_once
        val = await orig_invoke(name, *args, **kwargs)
        if name == "claude:start-session" and args and isinstance(args[0], dict):
            uncertain_sid_holder.append(args[0].get("sessionId"))
        if name == "claude:send-message" and not lost_once:
            lost_once = True
            raise InvokeTimeout("Simulated lost ACK on claude:send-message")
        return val

    monkeypatch.setattr(client, "invoke", lose_send_ack)
    env_unc = await _start_envelope(w, work_item_id=wi_uncertain, key="e04-uncertain")
    parent_unc, _ = ops.create(STARTER_OWNER, **env_unc)
    for _ in range(4):
        ops.wake(parent_unc["operation_id"])
        await settle_operations(ops)
        cur = ops.get(parent_unc["operation_id"])
        if cur["result"] and cur["result"].get("stage") == "start_unknown":
            break
    monkeypatch.setattr(client, "invoke", orig_invoke)
    assert ops.get(parent_unc["operation_id"])["result"]["stage"] == "start_unknown"
    assert uncertain_sid_holder

    # 3. Also register a foreign controller session in registry.
    foreign_principal = Principal("mallory", frozenset({"observe", "manage", "start", "operate"}))
    with controller_scope(ops.journal, foreign_principal):
        registry.reserve(
            "h1",
            {
                "session_id": "sess-foreign-9999",
                "cwd": "/srv/demo",
                "origin_cwd": "/srv/demo",
                "start_operation_id": "op_" + "f" * 32,
            },
            32,
        )
        registry.update("h1", "sess-foreign-9999", status="active")

    # Clear mock invoke log and run collector: none of manual, foreign, uncertain, or mismatch sessions may be read.
    mock.invokes.clear()
    collected = await collector.collect_host("h1")
    assert collected == []
    assert ops.db.execute("SELECT COUNT(*) FROM session_results").fetchone()[0] == 0

    forbidden_sids = {
        "sess-claude-0001",
        "sess-codex-0002",
        "sess-foreign-9999",
        sid_mismatch,
        *uncertain_sid_holder,
    }
    invoked_sids = {inv.get("params", {}).get("sessionId") for inv in mock.invokes}
    assert not (invoked_sids & forbidden_sids)


async def test_collector_invokes_only_read_channels_and_skips_when_marker_not_visible(start_ready, mock):
    w = start_ready
    ops = w["d"].ops
    collector = w["d"].result_collector

    # 1. Start attempt without user echo visible (turn_attribution != "correlated") -> writes no row.
    att = await _start_managed_attempt(w, mock, key="e04-marker-and-readonly", echo_send=False)
    sid = att["session_id"]
    marker = att["turn_marker"]
    _set_turn_messages(mock, sid, marker, streaming=False, include_echo=False)

    mock.invokes.clear()
    res_no_marker = await collector.collect_host("h1")
    assert res_no_marker == []
    assert ops.db.execute("SELECT COUNT(*) FROM session_results").fetchone()[0] == 0

    # 2. Make user echo visible and collect -> succeeds and uses only READ_CHANNELS.
    _set_turn_messages(mock, sid, marker, streaming=False, include_echo=True)
    mock.invokes.clear()
    res_ok = await collector.collect_host("h1")
    assert len(res_ok) == 1

    used_channels = {channels.canonical(inv["channel"]) for inv in mock.invokes}
    assert used_channels
    assert used_channels <= channels.READ_CHANNELS
    assert not (used_channels & (channels.WRITE_CHANNELS | channels.ORCHESTRATE_CHANNELS | channels.GUARDED_CHANNELS))


async def test_document_bounds_stays_under_256kib_and_marks_truncated(start_ready, mock):
    w = start_ready
    ops = w["d"].ops
    collector = w["d"].result_collector
    att = await _start_managed_attempt(w, mock, key="e04-doc-bounds")
    sid = att["session_id"]
    marker = att["turn_marker"]

    # Build a large transcript with > 200 items, long messages, and > 50 tool calls.
    extra: list[dict] = []
    for i in range(220):
        extra.append(
            {
                "id": f"tool-heavy-{i}",
                "toolName": "Bash",
                "input": {"command": f"echo {i} " + ("x" * 900)},
                "status": "done",
                "timestamp": 1_790_001_000_100 + i,
            }
        )
        if i % 5 == 0:
            extra.append(
                {
                    "id": f"msg-heavy-{i}",
                    "role": "assistant",
                    "content": f"step {i} " + ("y" * 2500),
                    "timestamp": 1_790_001_000_500 + i,
                }
            )
    _set_turn_messages(
        mock,
        sid,
        marker,
        streaming=False,
        assistant_id="m-asst-final",
        assistant_text="Final summary " + ("z" * 5000),
        extra_items=extra,
    )

    res = await collector.collect_host("h1")
    assert len(res) == 1
    raw_doc = ops.db.execute(
        "SELECT document FROM session_results WHERE result_id=?", (res[0]["result_id"],)
    ).fetchone()[0]
    assert len(raw_doc.encode("utf-8")) <= MAX_RESULT_DOCUMENT_BYTES
    doc = res[0]["document"]
    assert doc["coverage"]["document_truncated"] is True
    assert "result_document_bounded" in doc["coverage"]["reasons"]
    assert len(doc["turn_item_ids"]) <= 200
    assert len(doc["messages_preview"]) <= 20
    assert len(doc["tool_activity"]) <= 50
    assert doc["latest_assistant_summary"]["text"] == pm_attempt_results.clip("Final summary " + ("z" * 5000), 4000)


async def test_inventory_after_refresh_hook_lifecycle_error_budget_and_unreachable(start_ready, mock):
    w = start_ready
    inv = w["d"].inventory
    inv.after_refresh.clear()

    # 1. Hook runs after refresh and while the read-only client is still open, then client closes.
    seen_connected: list[bool] = []

    async def observe_hook(host: str, result: dict) -> None:
        assert host == "h1"
        assert result["reachable"] is True
        seen_connected.append(inv.fleet.client(host).connected)

    inv.after_refresh.append(observe_hook)
    res1 = await inv._refresh_scheduled("h1")
    assert res1["reachable"] is True
    assert seen_connected == [True]
    assert inv.fleet.client("h1").connected is False

    # 2. A hook that raises or exceeds its budget leaves inventory result and schedule unchanged.
    never_fires = asyncio.Event()

    async def raising_hook(_host: str, _result: dict) -> None:
        raise RuntimeError("boom in hook")

    async def hanging_hook(_host: str, _result: dict) -> None:
        await never_fires.wait()

    inv.after_refresh = [raising_hook, hanging_hook]
    inv.after_refresh_budget_s = 0.02
    try:
        res2 = await inv._refresh_scheduled("h1")
    finally:
        never_fires.set()
    assert res2["reachable"] is True
    assert res2["sessions"] == res1["sessions"]
    assert inv._next_at["h1"] > time.time()
    assert inv.fleet.client("h1").connected is False

    # 3. An unreachable host skips the hook completely.
    called_on_unreachable = False

    async def unreachable_hook(_host: str, _result: dict) -> None:
        nonlocal called_on_unreachable
        called_on_unreachable = True

    inv.after_refresh = [unreachable_hook]
    mock.handlers["workspace:load"] = lambda _p: (_ for _ in ()).throw(BatError("host down"))
    try:
        res3 = await inv._refresh_scheduled("h1")
    finally:
        mock.handlers.pop("workspace:load", None)
    assert res3.get("reachable") is False
    assert called_on_unreachable is False
