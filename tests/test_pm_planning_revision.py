"""Explicit source-set revisions preserve historical plans and direction authority."""

from __future__ import annotations

import copy
import sqlite3
import uuid

import pytest

from bat_agent_connector import (
    pm_discussions,
    pm_plans,
    pm_profiles,
    pm_revision_git,
    pm_runs,
    pm_workspace,
    pm_worktree,
)
from bat_agent_connector.api_auth import Principal
from bat_agent_connector.operations import OpContext, OperationError, OperationService
from bat_agent_connector.task_journal import Journal
from tests.test_pm_discussions import (
    CONTRIBUTOR,
    OUTSIDER,
    OWNER,
    READER,
    act,
    member,
    prepared,
    read,
    record,
    settle,
)
from tests.test_pm_plans import adopt, adoption, draft, draft_operation, planning
from tests.test_pm_runs import create as create_run


@pytest.fixture
def ops(tmp_path):
    journal = Journal(tmp_path / "planning.db")
    service = OperationService(journal)
    pm_workspace.install(service)
    version = journal.db.execute("PRAGMA user_version").fetchone()[0]
    pm_discussions.install(service)
    pm_discussions.install(service)
    pm_plans.install(service)
    pm_runs.schema(journal)
    assert journal.db.execute("PRAGMA user_version").fetchone()[0] == version
    yield service
    journal.close()


def revision_request(ops, target, selected, **params):
    doc = read(ops, target)
    return {"action": "pm.thread.plan.revise", "target": target,
            "params": {"selected_input_ids": selected, "rationale": "Include the newly discussed sources", **params},
            "preconditions": {"expected_version": doc["thread"]["version"], "expected_draft_id": doc["thread"]["draft_id"]},
            "idempotency_key": uuid.uuid4().hex}


async def revise(ops, target, selected, **params):
    op, _ = ops.create(OWNER, **revision_request(ops, target, selected, **params))
    return (await settle(ops, op))["result"]


async def test_reselection_keeps_old_drafts_plan_and_accepted_scope_fixed(ops):
    target, confirmed, assumption, acceptance = await planning(ops)
    old = read(ops, target)["draft"]
    first = await draft(ops, target)
    adopted = adopt(adoption(ops, target, first["revision_id"]))
    accepted_before = copy.deepcopy(pm_plans.accepted_plan(ops, OWNER, target["workspace_id"]))
    head_before = pm_plans.head(ops.db, target["workspace_id"])
    new = await record(ops, target, slot="risk", status="unresolved", value="Password reset abuse still needs investigation")
    await revise(ops, target, [confirmed, assumption, acceptance, new["input_id"]], title="會員規劃修訂")
    current = read(ops, target)
    changed = current["draft"]
    assert changed["previous_draft_id"] == old["draft_id"]
    assert changed["title"] == "會員規劃修訂" and current["thread"]["mode"] == "planning"
    assert changed["selected_inputs"][-1]["status"] == "unresolved"
    assert pm_discussions._draft(ops.db, current["thread"], draft_id=old["draft_id"]) == old
    assert pm_plans.head(ops.db, target["workspace_id"]) == head_before
    assert pm_plans.accepted_plan(ops, OWNER, target["workspace_id"]) == accepted_before
    second = await draft(ops, target)
    latest = pm_plans.get_plan(ops, OWNER, target["workspace_id"], second["revision_id"])["plan"]
    assert latest["document"]["planning_draft_id"] == changed["draft_id"]
    assert latest["document"]["selected_inputs"][-1]["input_id"] == new["input_id"]
    assert latest["document"]["requirements"] == []
    assert not latest["document"]["execution_authorized"]
    assert pm_plans.accepted_plan(ops, OWNER, target["workspace_id"])["plan"] == accepted_before["plan"]
    assert accepted_before["plan"]["adoption"]["scope_revision"] == adopted["scope_revision"]
    assert ops.db.execute("SELECT COUNT(*) FROM work_items").fetchone()[0] == 0
    assert ops.db.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 0
    assert ops.db.execute("SELECT COUNT(*) FROM operation_steps").fetchone()[0] == 0


async def test_idea_slots_use_declared_domains_without_promoting_status_or_requirements(ops):
    target = await prepared(ops)
    examples = [("problem", "scope", "agent_assumption"), ("alternatives", "governance", "conflicting"),
                ("unknowns", "risk", "unresolved"), ("finance", "finance", "not_applicable")]
    sources = []
    for slot, _, status in examples:
        sources.append((await record(ops, target, slot=slot, status=status, value=f"Original {slot} words"))["input_id"])
    unclassified = (await record(ops, target, slot="custom_note", status="unresolved"))["input_id"]
    await act(ops, target, "pm.thread.plan", {"selected_input_ids": [*sources, unclassified], "rationale": "Draft these possibilities"})
    result = await draft(ops, target)
    doc = pm_plans.get_plan(ops, OWNER, target["workspace_id"], result["revision_id"])["plan"]["document"]
    for source, (slot, domain, status) in zip(sources, examples):
        section = doc["sections"][domain]
        assert section["source_input_ids"] == [source]
        assert section["statements"][0]["slot"] == slot and section["statements"][0]["status"] == status
        assert section["statements"][0]["confirmed_by"] is None
    assert doc["requirements"] == [] and doc["unclassified_source_input_ids"] == [unclassified]
    assert doc["sections"]["schedule"]["coverage"] == "unresolved"


async def test_later_confirmation_requires_explicit_current_input_selection(ops):
    target, confirmed, assumption, _ = await planning(ops)
    original = read(ops, target)["draft"]
    replacement = await act(ops, target, "pm.input.confirm", {"input_id": assumption, "rationale": "Select this pricing option"})
    assert read(ops, target)["draft"] == original
    with pytest.raises(OperationError, match="PM_INPUT_CHANGED"):
        ops.create(OWNER, **revision_request(ops, target, [confirmed, assumption]))
    await revise(ops, target, [confirmed, replacement["input_id"]])
    current = read(ops, target)
    assert current["draft"]["selected_inputs"][1]["status"] == "user_confirmed"
    assert current["draft"]["selected_inputs"][1]["supersedes_input_id"] == assumption
    assert pm_discussions._draft(ops.db, current["thread"], draft_id=original["draft_id"]) == original
    assert original["selected_inputs"][1]["status"] == "agent_assumption"


async def test_revisions_link_original_ids_and_survive_service_restart(ops, tmp_path):
    target, confirmed, *_ = await planning(ops)
    first = read(ops, target)["draft"]
    await revise(ops, target, [confirmed], title="Selected title")
    second = read(ops, target)["draft"]
    await revise(ops, target, [confirmed], rationale="Keep this source after another review")
    third = read(ops, target)["draft"]
    assert third["previous_draft_id"] == second["draft_id"] and third["title"] == "Selected title"
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        ops.db.execute("UPDATE pm_planning_draft_revisions SET rationale='rewritten'")
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        ops.db.execute("DELETE FROM pm_planning_drafts")
    restarted = Journal(tmp_path / "planning.db")
    try:
        service = OperationService(restarted)
        pm_workspace.install(service)
        pm_discussions.install(service)
        doc = read(service, target)
        assert doc["draft"] == third
        for historical in (first, second):
            assert pm_discussions._draft(service.db, doc["thread"], draft_id=historical["draft_id"]) == historical
    finally:
        restarted.close()


async def test_missing_draft_precondition_or_stale_draft_even_with_current_thread_cannot_reselect(ops):
    target, confirmed, *_ = await planning(ops)
    request = revision_request(ops, target, [confirmed])
    for pre in ({"expected_version": request["preconditions"]["expected_version"]},
                {**request["preconditions"], "expected_draft_id": None},
                {**request["preconditions"], "expected_version": True},
                {**request["preconditions"], "unrecognized": "field"}):
        with pytest.raises(OperationError, match="PRECONDITION_REQUIRED"):
            ops.create(OWNER, **{**request, "preconditions": pre})
    first, _ = ops.create(OWNER, **request)
    competing, _ = ops.create(OWNER, **{**request, "idempotency_key": uuid.uuid4().hex})
    await settle(ops, first)
    assert ops.get(competing["operation_id"])["error_code"] == "VERSION_CONFLICT"
    request["preconditions"]["expected_version"] = read(ops, target)["thread"]["version"]
    request["idempotency_key"] = uuid.uuid4().hex
    with pytest.raises(OperationError, match="PM_DRAFT_CONFLICT"):
        ops.create(OWNER, **request)
    assert ops.db.execute("SELECT COUNT(*) FROM pm_planning_draft_revisions").fetchone()[0] == 1


@pytest.mark.parametrize("who,code", [(CONTRIBUTOR, "FORBIDDEN"), (READER, "FORBIDDEN"), (OUTSIDER, "NOT_FOUND"),
                                      (Principal(OWNER.actor, frozenset({"observe"})), "FORBIDDEN")])
async def test_owner_and_manage_scope_are_required(ops, who, code):
    target, confirmed, *_ = await planning(ops)
    with pytest.raises(OperationError, match=code):
        ops.create(who, **revision_request(ops, target, [confirmed]))
    assert ops.db.execute("SELECT COUNT(*) FROM pm_planning_draft_revisions").fetchone()[0] == 0


async def test_membership_is_rechecked_at_effect_boundary(ops):
    target, confirmed, *_ = await planning(ops)
    await member(ops, target, CONTRIBUTOR.actor, "owner")
    before = read(ops, target)["draft"]
    op, _ = ops.create(CONTRIBUTOR, **revision_request(ops, target, [confirmed]))
    # Model an independent client's membership transaction before this queued command executes.
    with ops.journal.tx():
        ops.db.execute("UPDATE pm_workspace_members SET role='reader' WHERE workspace_id=? AND actor=?",
                       (target["workspace_id"], CONTRIBUTOR.actor))
    result = await settle(ops, op, success=False)
    assert result["status"] == "failed" and result["error_code"] == "FORBIDDEN"
    assert read(ops, target)["draft"] == before


async def test_lost_ack_returns_original_revision_once_and_key_is_workspace_scoped(ops):
    target, confirmed, *_ = await planning(ops)
    request = revision_request(ops, target, [confirmed])
    op, _ = ops.create(OWNER, **request)
    # The local transaction committed, but the operation worker has not stored its result yet.
    applied = await pm_discussions._run(OpContext(ops, op))
    recovered, created = ops.create(OWNER, **request)
    assert not created and recovered["operation_id"] == op["operation_id"]
    assert (await settle(ops, recovered))["result"] == applied
    assert ops.create(OWNER, **request)[0]["result"] == applied
    with pytest.raises(OperationError, match="IDEMPOTENCY_CONFLICT"):
        ops.create(OWNER, **{**request, "params": {**request["params"], "rationale": "Different direction"}})
    other, other_source, *_ = await planning(ops)
    another = revision_request(ops, other, [other_source])
    another["idempotency_key"] = request["idempotency_key"]
    independent, created = ops.create(OWNER, **another)
    assert created and independent["operation_id"] != op["operation_id"]
    await settle(ops, independent)
    assert ops.db.execute("SELECT COUNT(*) FROM pm_planning_draft_revisions").fetchone()[0] == 2
    assert pm_discussions._draft(ops.db, read(ops, target)["thread"])["draft_id"] == applied["draft_id"]


async def test_foreign_input_ids_and_drafts_are_not_selectable_or_readable(ops):
    target, confirmed, *_ = await planning(ops)
    other, other_source, *_ = await planning(ops)
    with pytest.raises(OperationError, match="PM_INPUT_NOT_FOUND"):
        ops.create(OWNER, **revision_request(ops, target, [other_source]))
    with pytest.raises(OperationError, match="PM_DRAFT_NOT_FOUND"):
        pm_discussions._draft(ops.db, read(ops, target)["thread"], draft_id=read(ops, other)["draft"]["draft_id"])
    with pytest.raises(OperationError, match="INVALID_PARAMS"):
        ops.create(OWNER, **revision_request(ops, target, [confirmed, confirmed]))
    idea = await prepared(ops)
    request = revision_request(ops, idea, [confirmed])
    request["preconditions"]["expected_draft_id"] = read(ops, target)["draft"]["draft_id"]
    with pytest.raises(OperationError, match="PM_MODE_REFUSED"):
        ops.create(OWNER, **request)


async def test_reselection_invalidates_queued_plan_draft_without_changing_existing_revision(ops):
    target, confirmed, *_ = await planning(ops)
    original = await draft(ops, target)
    revise_op, _ = ops.create(OWNER, **revision_request(ops, target, [confirmed]))
    stale = draft_operation(ops, target)
    await settle(ops, revise_op)
    assert ops.get(stale["operation_id"])["error_code"] == "VERSION_CONFLICT"
    assert pm_plans.head(ops.db, target["workspace_id"])["latest_revision_id"] == original["revision_id"]


@pytest.mark.parametrize("prior_version", [1, 2])
async def test_profile_upgrade_and_reselection_leave_frozen_run_context_unchanged(ops, monkeypatch, prior_version):
    target, confirmed, *_ = await planning(ops)
    with monkeypatch.context() as prior:
        prior.setattr(pm_profiles, "VERSION", prior_version)
        run = create_run(ops, target)
    frozen = copy.deepcopy(run["context"])
    await revise(ops, target, [confirmed])
    assert pm_profiles.VERSION == 3 and read(ops, target)["profile"]["version"] == 3
    loaded = pm_runs.get(ops.db, run["run_id"])
    assert loaded["context"] == frozen and loaded["context_digest"] == run["context_digest"]
    assert loaded["context"]["profile"]["version"] == prior_version
    assert loaded["context"]["draft_id"] != read(ops, target)["draft"]["draft_id"]


async def test_multibyte_plan_and_sources_fit_exact_git_bound_or_fail_before_admission(ops):
    target = await prepared(ops)
    sources = [(await record(ops, target, value="需" * 4000))["input_id"] for _ in range(6)]
    await act(ops, target, "pm.thread.plan", {"selected_input_ids": sources, "rationale": "Discuss the complete source material"})
    thread = read(ops, target)["thread"]
    document, _ = pm_plans._document(ops.db, target["workspace_id"], thread, {"markdown": "x"}, "pmrev_" + "0" * 32)
    overhead = sum(len(value.encode("utf-8")) for value in pm_plans._files(document["revision_id"], document)["files"].values()) - 1
    remaining = pm_plans.MAX_DOCUMENT_BYTES - overhead
    markdown = "文" * (remaining // 3) + "x" * (remaining % 3)
    assert len(markdown) <= 128_000 and len(markdown.encode("utf-8")) > len(markdown)
    assert pm_plans.MAX_DOCUMENT_BYTES == pm_worktree.MAX_SNAPSHOT_BYTES == 512_000
    before = ops.db.execute("SELECT COUNT(*) FROM operations").fetchone()[0]
    with pytest.raises(OperationError, match="PM_PLAN_TOO_LARGE"):
        draft_operation(ops, target, markdown=markdown + "文")
    assert ops.db.execute("SELECT COUNT(*) FROM operations").fetchone()[0] == before
    assert pm_plans.head(ops.db, target["workspace_id"])["latest_revision_id"] is None
    operation = draft_operation(ops, target, markdown=markdown)
    result = (await settle(ops, operation))["result"]
    files = pm_plans.revision_files(ops.db, target["workspace_id"], result["revision_id"])
    assert sum(len(value.encode("utf-8")) for value in files["files"].values()) == 512_000
    # The managed Git boundary accepts these exact fixed bytes; no transport is invoked.
    ctx = OpContext(ops, ops.get(operation["operation_id"]))
    binding = {"server_id": pm_workspace.server_id(ops.db), "pm_workspace_id": target["workspace_id"],
               "operation_id": "op_" + "2" * 32, "binding_digest": "3" * 64}
    request = pm_revision_git.request(ctx, binding, files, "1" * 40)
    assert request["document_digest"] == result["document_digest"]
