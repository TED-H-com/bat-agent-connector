"""PM source history, explicit drafting, membership and durable recovery; no runner."""

from __future__ import annotations

import json
import sqlite3
import uuid

import pytest

from bat_agent_connector import pm_discussions as pm
from bat_agent_connector import pm_workspace
from bat_agent_connector.api_auth import Principal
from bat_agent_connector.operations import OpContext, OperationError, OperationService
from bat_agent_connector.task_journal import Journal
from tests.operation_helpers import settle_operations

OWNER = Principal("project-owner", frozenset({"observe", "manage"}))
CONTRIBUTOR = Principal("pm-assistant", frozenset({"observe", "manage"}))
READER = Principal("project-reader", frozenset({"observe", "manage"}))
OUTSIDER = Principal("another-project", frozenset({"observe", "manage"}))


@pytest.fixture
def ops(tmp_path):
    journal = Journal(tmp_path / "journal.db")
    service = OperationService(journal)
    pm_workspace.install(service)
    pm.install(service)
    pm.install(service)
    yield service
    journal.close()


def submit(ops, action, target, params, version, *, who=OWNER, key=None):
    return ops.create(who, action=action, target=target, params=params,
        preconditions={"expected_version": version}, idempotency_key=key or uuid.uuid4().hex)


async def settle(ops, op, *, success=True):
    await settle_operations(ops)
    result = ops.get(op["operation_id"])
    if success:
        assert result["status"] == "succeeded", result
    return result


async def workspace(ops, who=OWNER):
    op, _ = ops.create(who, action="pm.workspace.create", params={"name": "A useful product"}, idempotency_key=uuid.uuid4().hex)
    return (await settle(ops, op))["result"]["workspace"]


async def member(ops, ws, actor, role):
    doc = pm_workspace.get_workspace(ops.db, OWNER, ws["workspace_id"])
    op, _ = submit(ops, "pm.workspace.member.set", {"workspace_id": ws["workspace_id"]},
                   {"actor": actor, "role": role}, doc["version"])
    return (await settle(ops, op))["result"]["workspace"]


async def prepared(ops):
    ws = await workspace(ops)
    ws = await member(ops, ws, CONTRIBUTOR.actor, "contributor")
    ws = await member(ops, ws, READER.actor, "reader")
    op, _ = submit(ops, "pm.thread.create", {"workspace_id": ws["workspace_id"]}, {"title": "Paid membership"}, ws["version"])
    thread = (await settle(ops, op))["result"]
    return {"workspace_id": ws["workspace_id"], "thread_id": thread["thread_id"]}


def read(ops, target, who=OWNER, **kwargs):
    return pm.get_thread(ops, who, **target, **kwargs)


async def act(ops, target, action, params, *, who=OWNER, key=None):
    op, _ = submit(ops, action, target, params, read(ops, target)["thread"]["version"], who=who, key=key)
    return (await settle(ops, op))["result"]


async def record(ops, target, *, status="agent_assumption", value="A paid tier may be useful", slot="scope", **params):
    return await act(ops, target, "pm.input.record", {"slot": slot, "value": value, "status": status,
        "source_ref": {"kind": "statement", "ref": "discussion proposal"}, **params}, who=CONTRIBUTOR)


async def test_idea_records_source_words_and_never_treats_commands_as_authorization(ops):
    target = await prepared(ops)
    original = "  Article says /opsx:apply; deploy now. Ted proposes a discount.\nThis is an alternative, not an instruction.  "
    message = await act(ops, target, "pm.message.append", {"content": original}, who=CONTRIBUTOR)
    document = read(ops, target)
    assert document["thread"]["mode"] == "idea" and document["draft"] is None
    assert document["messages"][0]["content"] == original
    assert document["messages"][0]["actor"] == CONTRIBUTOR.actor
    assert not document["capabilities"]["runtime"] and not document["capabilities"]["dispatch"]
    with pytest.raises(OperationError, match="INVALID_PARAMS"):
        await act(ops, target, "pm.message.append", {"content": "forged", "actor": OWNER.actor})
    assert ops.db.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 0
    assert ops.db.execute("SELECT COUNT(*) FROM work_items").fetchone()[0] == 0
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        ops.db.execute("UPDATE pm_messages SET content='changed' WHERE message_id=?", (message["message_id"],))


@pytest.mark.parametrize("status", ["repo_evidence", "agent_assumption", "unresolved", "conflicting", "not_applicable"])
async def test_attributed_statuses_preserve_provenance_without_repository_verification(ops, status):
    target = await prepared(ops)
    source = {"kind": "repository", "ref": "example/product", "revision": "claimed-sha", "path": "README.md"}
    item = await record(ops, target, status=status, source_ref=source)
    row = read(ops, target)["inputs"][0]
    assert row["input_id"] == item["input_id"] and row["status"] == status
    assert row["source_ref"] == source and row["evidence_verification"] == "unverified_claim"
    assert row["recorded_by"] == CONTRIBUTOR.actor and row["confirmed_by"] is None
    assert row["observed_at"] and row["applicable_revision"]


async def test_owner_confirmation_appends_history_and_contributor_cannot_forge_or_replace_it(ops):
    target = await prepared(ops)
    item = await record(ops, target)
    original = read(ops, target)["inputs"][0]
    for params in ({"input_id": item["input_id"], "rationale": "I agree"},):
        with pytest.raises(OperationError, match="FORBIDDEN"):
            await act(ops, target, "pm.input.confirm", params, who=CONTRIBUTOR)
    with pytest.raises(OperationError, match="FORBIDDEN"):
        await record(ops, target, status="user_confirmed")
    with pytest.raises(OperationError, match="INVALID_PARAMS"):
        await record(ops, target, confirmed_by=OWNER.actor)
    confirmed = await act(ops, target, "pm.input.confirm", {"input_id": item["input_id"], "rationale": "This is the chosen business direction"})
    doc = read(ops, target)
    assert len(doc["inputs"]) == 1 and len(doc["input_history"]) == 2
    latest = doc["inputs"][0]
    assert latest["input_id"] == confirmed["input_id"] and latest["supersedes_input_id"] == item["input_id"]
    assert latest["confirmed_by"] == OWNER.actor and latest["source_ref"] == original["source_ref"]
    assert next(row for row in doc["input_history"] if row["input_id"] == item["input_id"]) == original
    with pytest.raises(OperationError, match="FORBIDDEN"):
        await record(ops, target, supersedes_input_id=confirmed["input_id"])
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        ops.db.execute("DELETE FROM pm_inputs WHERE input_id=?", (item["input_id"],))


async def test_explicit_planning_freezes_selected_assumptions_and_preserves_unselected_candidates(ops):
    target = await prepared(ops)
    selected = await record(ops, target, value="Members can buy access")
    candidate = await record(ops, target, value="Consider referral discounts later")
    with pytest.raises(OperationError, match="FORBIDDEN"):
        await act(ops, target, "pm.thread.plan", {"selected_input_ids": [selected["input_id"]], "rationale": "Write a plan"}, who=CONTRIBUTOR)
    result = await act(ops, target, "pm.thread.plan", {"selected_input_ids": [selected["input_id"]], "rationale": "Explore paid membership only"})
    draft = read(ops, target)["draft"]
    assert draft["draft_id"] == result["draft_id"] and draft["status"] == "draft"
    assert draft["selected_inputs"][0]["status"] == "agent_assumption"
    assert draft["selected_inputs"][0]["confirmed_by"] is None
    assert draft["candidate_input_ids"] == [candidate["input_id"]] and draft["candidate_mode"] == "idea"
    assert {row["slot"] for row in draft["unknowns"]} == {"value", "schedule", "finance", "stakeholders", "resources", "risk", "quality"}
    assert not draft["execution_authorized"] and not draft["adopted"] and draft["scope_revision"] is None
    await act(ops, target, "pm.input.confirm", {"input_id": selected["input_id"], "rationale": "Later confirmation"})
    assert read(ops, target)["draft"] == draft  # later confirmation cannot rewrite the chosen historical sources
    with pytest.raises(OperationError, match="PM_MODE_REFUSED"):
        await act(ops, target, "pm.thread.plan", {"selected_input_ids": [candidate["input_id"]], "rationale": "Again"})
    assert ops.db.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 0


async def test_membership_cross_workspace_and_source_ids_are_authoritative(ops):
    target = await prepared(ops)
    other = await prepared(ops)
    source = await act(ops, other, "pm.message.append", {"content": "other discussion"})
    item = await record(ops, other)
    with pytest.raises(OperationError, match="PM_SOURCE_NOT_FOUND"):
        await record(ops, target, source_ref={"kind": "message", "message_id": source["message_id"]})
    with pytest.raises(OperationError, match="PM_INPUT_NOT_FOUND"):
        await act(ops, target, "pm.thread.plan", {"selected_input_ids": [item["input_id"]], "rationale": "foreign source"})
    with pytest.raises(OperationError, match="PM_THREAD_NOT_FOUND"):
        read(ops, {**target, "workspace_id": other["workspace_id"]})
    with pytest.raises(OperationError, match="NOT_FOUND"):
        read(ops, target, OUTSIDER)
    assert read(ops, target, READER)["capabilities"]["write"] is False
    with pytest.raises(OperationError, match="FORBIDDEN"):
        await act(ops, target, "pm.message.append", {"content": "not permitted"}, who=READER)
    no_scope = Principal(OWNER.actor, frozenset({"manage"}))
    with pytest.raises(OperationError, match="FORBIDDEN"):
        read(ops, target, no_scope)


async def test_current_membership_and_version_are_rechecked_at_mutation(ops):
    target = await prepared(ops)
    pending, _ = submit(ops, "pm.message.append", target, {"content": "pending"}, 1, who=CONTRIBUTOR)
    # Model a separately committed membership change before the worker gets its turn.
    ops.db.execute("UPDATE pm_workspace_members SET role='reader' WHERE workspace_id=? AND actor=?", (target["workspace_id"], CONTRIBUTOR.actor))
    denied = await settle(ops, pending, success=False)
    assert denied["status"] == "failed" and denied["error_code"] == "FORBIDDEN"
    first, _ = submit(ops, "pm.message.append", target, {"content": "first"}, 1)
    second, _ = submit(ops, "pm.message.append", target, {"content": "second"}, 1)
    await settle(ops, first)
    assert ops.get(second["operation_id"])["error_code"] == "VERSION_CONFLICT"
    assert [row["content"] for row in read(ops, target)["messages"]] == ["first"]


async def test_owner_demotion_blocks_pending_confirmation_and_planning(ops):
    target = await prepared(ops)
    item = await record(ops, target)
    current = read(ops, target)["thread"]["version"]
    pending, _ = submit(ops, "pm.input.confirm", target, {"input_id": item["input_id"], "rationale": "yes"}, current)
    ops.db.execute("UPDATE pm_workspace_members SET role='contributor' WHERE workspace_id=? AND actor=?", (target["workspace_id"], OWNER.actor))
    result = await settle(ops, pending, success=False)
    assert result["status"] == "failed" and result["error_code"] == "FORBIDDEN"
    assert read(ops, target)["inputs"][0]["confirmed_by"] is None


async def test_same_key_replay_and_crash_after_local_commit_do_not_reapply_or_require_old_version(ops):
    target = await prepared(ops)
    request = {"action": "pm.message.append", "target": target, "params": {"content": "exact source"},
               "preconditions": {"expected_version": 1}, "idempotency_key": "fixed-original"}
    op, _ = ops.create(OWNER, **request)
    op = await settle(ops, op)
    await act(ops, target, "pm.message.append", {"content": "later"})
    replay, created = ops.create(OWNER, **request)
    assert not created and replay["operation_id"] == op["operation_id"]
    assert await pm._run(OpContext(ops, op)) == op["result"]  # worker crash after commit, before success receipt
    assert len(read(ops, target)["messages"]) == 2 and read(ops, target)["thread"]["version"] == 3
    with pytest.raises(OperationError, match="IDEMPOTENCY_CONFLICT"):
        ops.create(OWNER, **{**request, "params": {"content": "changed"}})
    ops.db.execute("DELETE FROM pm_workspace_members WHERE workspace_id=? AND actor=?", (target["workspace_id"], OWNER.actor))
    with pytest.raises(OperationError, match="NOT_FOUND"):
        ops.create(OWNER, **request)
    # Internal recovery reads the committed receipt, never converts an applied effect to failure.
    assert await pm._run(OpContext(ops, op)) == op["result"]


async def test_observe_only_credentials_cannot_control_original_actors_pending_operation(ops):
    target = await prepared(ops)
    op, _ = submit(ops, "pm.message.append", target, {"content": "pending"}, 1)
    reduced = Principal(OWNER.actor, frozenset({"observe"}))
    for control in (ops.cancel, ops.resume):
        with pytest.raises(OperationError, match="FORBIDDEN"):
            control(reduced, op["operation_id"])
    assert ops.get(op["operation_id"])["status"] == "accepted"


async def test_source_history_and_draft_survive_reopen_without_read_side_effects(ops):
    target = await prepared(ops)
    item = await record(ops, target)
    await act(ops, target, "pm.thread.plan", {"selected_input_ids": [item["input_id"]], "rationale": "prepare only"})
    expected = read(ops, target)
    journal = Journal(ops.journal.path)
    try:
        reopened = OperationService(journal)
        pm.install(reopened)
        before = journal.db.total_changes
        assert read(reopened, target) == expected
        listed = pm.list_threads(reopened, OWNER, target["workspace_id"])
        assert listed["threads"][0]["thread_id"] == target["thread_id"]
        assert listed["executable_modes"] == ["idea", "planning"]
        pm.context(reopened, OWNER, **target)
        assert journal.db.total_changes == before
    finally:
        journal.close()


async def test_bounded_context_keeps_selected_refs_unknowns_and_explicit_omissions(ops):
    target = await prepared(ops)
    item = await record(ops, target, value="A" * 4000)
    await act(ops, target, "pm.thread.plan", {"selected_input_ids": [item["input_id"]], "rationale": "a hypothesis"})
    for index in range(4):
        await act(ops, target, "pm.message.append", {"content": f"{index}:" + "x" * 2000})
    doc = read(ops, target, limit=2)
    assert doc["coverage"]["messages_truncated"] and doc["next_message_cursor"]
    older = read(ops, target, limit=2, before_message=doc["next_message_cursor"])
    assert {m["message_id"] for m in doc["messages"]}.isdisjoint({m["message_id"] for m in older["messages"]})
    context = pm.context(ops, OWNER, **target, max_chars=2000)
    assert context["selected_input_ids"] == [item["input_id"]]
    assert context["coverage"]["omitted_input_ids"] == [item["input_id"]]
    assert context["coverage"]["content_chars"] <= 2000
    assert not context["inputs_by_status"]["user_confirmed"]
    assert context["authority"] == "context_only" and not context["execution_authorized"]


@pytest.mark.parametrize("params", [
    {"source_ref": {"kind": []}}, {"applicable_revision": []}, {"source_ref": None},
    {"status": "verified"}, {"value": ""}, {"slot": "Invalid field"}, {"observed_at": 0},
    {"status": "repo_evidence", "source_ref": {"kind": "external", "ref": "https://example.com"}},
])
async def test_malformed_or_forged_input_metadata_is_refused_before_operation(ops, params):
    target = await prepared(ops)
    before = ops.db.execute("SELECT COUNT(*) FROM operations").fetchone()[0]
    with pytest.raises(OperationError, match="INVALID_PARAMS"):
        await record(ops, target, **params)
    assert ops.db.execute("SELECT COUNT(*) FROM operations").fetchone()[0] == before
    assert not read(ops, target)["inputs"]


async def test_message_source_is_exact_and_confirmation_does_not_claim_repo_verification(ops):
    target = await prepared(ops)
    message = await act(ops, target, "pm.message.append", {"content": "Password reset is required"})
    await act(ops, target, "pm.input.record", {"slot": "scope", "value": "Password reset", "status": "user_confirmed",
        "source_ref": {"kind": "message", "message_id": message["message_id"]}})
    row = read(ops, target)["inputs"][0]
    assert row["evidence_verification"] == "central_message" and row["confirmed_by"] == OWNER.actor
    assert "credential" not in json.dumps(ops.list())


async def test_confirmed_source_correction_needs_current_owner_even_on_control_or_replay(ops):
    target = await prepared(ops)
    item = await act(ops, target, "pm.input.record", {"slot": "scope", "value": "Approved scope", "status": "user_confirmed",
        "source_ref": {"kind": "statement", "ref": "owner decision"}})
    pending, _ = submit(ops, "pm.input.record", target,
        {"slot": "scope", "value": "A proposed correction", "status": "agent_assumption",
         "source_ref": {"kind": "statement", "ref": "owner's alternative"}, "supersedes_input_id": item["input_id"]},
        read(ops, target)["thread"]["version"])
    ops.db.execute("UPDATE pm_workspace_members SET role='contributor' WHERE workspace_id=? AND actor=?", (target["workspace_id"], OWNER.actor))
    with pytest.raises(OperationError, match="FORBIDDEN"):
        ops.cancel(OWNER, pending["operation_id"])
    denied = await settle(ops, pending, success=False)
    assert denied["error_code"] == "FORBIDDEN"
    assert read(ops, target)["inputs"][0]["value"] == "Approved scope"


async def test_superseded_selection_is_refused_and_planning_receipt_recovers_without_duplicate_draft(ops):
    target = await prepared(ops)
    item = await record(ops, target)
    revised = await act(ops, target, "pm.input.confirm", {"input_id": item["input_id"], "rationale": "selected"})
    with pytest.raises(OperationError, match="PM_INPUT_CHANGED"):
        await act(ops, target, "pm.thread.plan", {"selected_input_ids": [item["input_id"]], "rationale": "stale choice"})
    op, _ = submit(ops, "pm.thread.plan", target, {"selected_input_ids": [revised["input_id"]], "rationale": "prepare planning"},
                   read(ops, target)["thread"]["version"])
    op = await settle(ops, op)
    ops.db.execute("DELETE FROM pm_workspace_members WHERE workspace_id=? AND actor=?", (target["workspace_id"], OWNER.actor))
    assert await pm._run(OpContext(ops, op)) == op["result"]
    assert ops.db.execute("SELECT COUNT(*) FROM pm_planning_drafts").fetchone()[0] == 1


async def test_selected_draft_sources_remain_complete_outside_current_input_page(ops):
    target = await prepared(ops)
    first = await record(ops, target, value="fixed selected source")
    await act(ops, target, "pm.thread.plan", {"selected_input_ids": [first["input_id"]], "rationale": "fixed direction"})
    # Fill a historical journal fixture without starting 101 scheduler turns.
    for number in range(101):
        ops.db.execute("""INSERT INTO pm_inputs(input_id,workspace_id,thread_id,slot,value,status,source_ref,recorded_by,
            observed_at,applicable_revision,operation_id) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
            ("pmi_" + f"{number:032x}", target["workspace_id"], target["thread_id"], "risk", "later candidate", "unresolved",
             json.dumps({"kind": "statement", "ref": "fixture"}), CONTRIBUTOR.actor, number + 1, "fixture-r1", f"fixture-{number}"))
    doc = read(ops, target)
    assert doc["coverage"]["inputs_truncated"]
    assert first["input_id"] not in {row["input_id"] for row in doc["inputs"]}
    assert doc["draft"]["selected_inputs"][0]["input_id"] == first["input_id"]
    context = pm.context(ops, OWNER, **target)
    assert context["inputs_by_status"]["agent_assumption"][0]["input_id"] == first["input_id"]
    assert context["coverage"]["inputs_truncated"]
    assert all(slot["basis"] == "not_in_loaded_current_inputs" for slot in context["missing_slots"])
