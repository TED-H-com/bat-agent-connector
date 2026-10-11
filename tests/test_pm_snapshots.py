"""Fixed DocumentSnapshot facts, source permissions and honest Brief projections."""
from __future__ import annotations

import json
import sqlite3
import uuid

import pytest

from bat_agent_connector import (
    artifacts,
    pm_discussions,
    pm_evidence,
    pm_plans,
    pm_snapshots,
    pm_workspace,
    work_items,
)
from bat_agent_connector.api_auth import Principal
from bat_agent_connector.operations import OpContext, OperationError, OperationService
from bat_agent_connector.task_journal import Journal
from tests.test_pm_discussions import CONTRIBUTOR, OUTSIDER, OWNER, READER, record, settle, workspace
from tests.test_pm_evidence import (
    act,
    bind_project,
    bind_work,
    capture,
    item,
    link,
    prepared,
    project,
    source_operation,
    task,
)
from tests.test_pm_plans import adopt, adoption, draft, planning


@pytest.fixture
def ops(tmp_path):
    journal = Journal(tmp_path / "snapshot.db")
    service = OperationService(journal, actions=work_items.ACTIONS)
    service.context["artifact_store"] = artifacts.ArtifactStore(service)
    pm_workspace.install(service)
    pm_discussions.install(service)
    pm_plans.install(service)
    pm_evidence.install(service)
    version = journal.db.execute("PRAGMA user_version").fetchone()[0]
    pm_snapshots.install(service)
    pm_snapshots.install(service)
    assert journal.db.execute("PRAGMA user_version").fetchone()[0] == version
    yield service
    journal.close()


def request(ops, wid, **params):
    return {"action": "pm.snapshot.create", "target": {"workspace_id": wid}, "params": params,
            "preconditions": {"expected_plan_version": pm_plans.head(ops.db, wid)["version"]},
            "idempotency_key": uuid.uuid4().hex}


async def snapshot(ops, wid, *, who=CONTRIBUTOR, **params):
    op, _ = ops.create(who, **request(ops, wid, **params))
    result = (await settle(ops, op))["result"]
    return pm_snapshots.get_snapshot(ops, who, wid, result["snapshot_id"])["snapshot"]


async def accepted(ops):
    target, confirmed, assumption, acceptance = await planning(ops)
    result = await draft(ops, target, requirements=[{"id": "login", "source_input_id": confirmed,
                                                    "acceptance_input_ids": [acceptance]}])
    adopt(adoption(ops, target, result["revision_id"]))
    return target, result, confirmed, assumption


async def work_for(ops, target):
    pid = await project(ops)
    work = await item(ops, pid)
    await bind_project(ops, target["workspace_id"], pid)
    result = {"workspace_id": target["workspace_id"], "work_item_id": work}
    await bind_work(ops, result)
    return result


async def test_empty_workspace_is_unknown_not_a_complete_project(ops):
    ws = await workspace(ops)
    out = await snapshot(ops, ws["workspace_id"], who=OWNER)
    assert out["baseline"]["status"] == "unavailable" and out["candidate_document"] is None
    assert out["work"] == out["evidence"] == []
    assert set(out["brief"]) == {"goals", "delivery", "blockers", "deviations", "decisions"}
    assert out["brief"]["delivery"]["coverage"]["qualified_deliverables"] is None
    assert out["brief"]["deviations"]["status"] == "unassessed"
    assert out["coverage"]["cost"] == "unknown" and not out["coverage"]["network_checked"]
    assert out["server_id"] == pm_workspace.server_id(ops.db)
    assert out["journal_cursor"] < ops.journal.api_head()
    assert out["execution_authorized"] is False and ops.db.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 0


async def test_candidate_draft_never_becomes_accepted_goal_or_commitment(ops):
    target, *_ = await planning(ops)
    result = await draft(ops, target)
    out = await snapshot(ops, target["workspace_id"])
    assert out["baseline"]["document"] is None and out["brief"]["goals"]["items"] == []
    assert out["candidate_document"]["revision_id"] == result["revision_id"]
    assert out["candidate_document"]["status"] == "draft" and not out["candidate_document"]["is_current_commitment"]
    assert pm_plans.head(ops.db, target["workspace_id"])["accepted_revision_id"] is None


async def test_accepted_baseline_preserves_eight_domains_source_status_and_later_drafts(ops):
    target, result, confirmed, assumption = await accepted(ops)
    accepted_before = pm_plans.accepted_plan(ops, OWNER, target["workspace_id"])["plan"]
    later = await draft(ops, target, title="Candidate expansion")
    out = await snapshot(ops, target["workspace_id"])
    assert out["baseline"]["document"] == accepted_before["document"]
    assert out["baseline"]["document_revision"] == result["revision_id"]
    assert out["candidate_document"]["revision_id"] == later["revision_id"]
    assert len(out["baseline"]["document"]["sections"]) == 8
    sources = {s["input_id"]: s for s in out["baseline"]["document"]["selected_inputs"]}
    assert sources[confirmed]["status"] == "user_confirmed" and sources[assumption]["status"] == "agent_assumption"
    assert out["brief"]["goals"]["items"][0]["status"] == "user_confirmed"
    assert out["brief"]["decisions"]["items"][0]["source"]["decision_id"] == accepted_before["adoption"]["decision_id"]


async def test_snapshot_freezes_head_bindings_and_capture_and_does_not_refresh_them(ops):
    target, first, *_ = await accepted(ops)
    work = await work_for(ops, target)
    await bind_work(ops, work, plan_revision_id=first["revision_id"], requirement_ids=["login"])
    await capture(ops, work)
    old = await snapshot(ops, target["workspace_id"])
    later = await draft(ops, target, title="Next proposal")
    adopt(adoption(ops, target, later["revision_id"], previous=first["revision_id"]))
    await bind_work(ops, work, plan_revision_id=later["revision_id"])
    item_before = work_items._get_item(ops.db, work["work_item_id"])
    await act(ops, "work_item.update", {"work_item_id": work["work_item_id"]}, {"state": "done"},
              {"expected_version": item_before["version"]})
    await capture(ops, work)
    assert pm_snapshots.get_snapshot(ops, OWNER, target["workspace_id"], old["snapshot_id"])["snapshot"] == old
    refreshed = await snapshot(ops, target["workspace_id"])
    assert refreshed["work"][0]["capture"]["capture_id"] != old["work"][0]["capture"]["capture_id"]
    assert refreshed["baseline"]["document_revision"] == later["revision_id"]
    assert refreshed["snapshot_digest"] != old["snapshot_digest"]
    assert refreshed["brief"]["delivery"]["status"] == "quality_unassessed"


async def test_old_plan_association_and_new_binding_do_not_manufacture_drift(ops):
    target, first, *_ = await accepted(ops)
    work = await work_for(ops, target)
    await bind_work(ops, work, plan_revision_id=first["revision_id"])
    cap = await capture(ops, work)
    later = await draft(ops, target, title="Changed narrative")
    adopt(adoption(ops, target, later["revision_id"], previous=first["revision_id"]))
    old_binding = await snapshot(ops, target["workspace_id"])
    assert old_binding["work"][0]["binding"]["plan_revision_id"] == first["revision_id"]
    assert old_binding["brief"]["deviations"]["items"] == [] and old_binding["brief"]["deviations"]["status"] == "unassessed"
    await bind_work(ops, work, plan_revision_id=later["revision_id"])
    fresh_binding = await snapshot(ops, target["workspace_id"])
    entry = fresh_binding["work"][0]
    assert entry["capture"]["capture_id"] == cap["capture_id"] and entry["coverage"]["capture_matches_binding"] is False
    assert fresh_binding["brief"]["deviations"]["status"] == "unassessed"


async def test_missing_capture_is_explicit_and_never_triggers_collection(ops):
    work = await prepared(ops)
    out = await snapshot(ops, work["workspace_id"])
    assert out["work"][0]["capture"] is None and out["evidence"] == []
    missing = out["brief"]["blockers"]["items"]
    assert len(missing) == 1 and missing[0]["kind"] == "missing_capture"
    assert out["brief"]["blockers"]["coverage"]["complete_diagnosis"] is False
    assert ops.db.execute("SELECT COUNT(*) FROM pm_evidence_captures").fetchone()[0] == 0


async def test_recorded_attention_and_passed_verification_do_not_assert_dod_or_deployment(ops):
    work = await prepared(ops)
    source = task(ops)
    await link(ops, work, "task", source["task_id"])
    ops.db.execute("UPDATE tasks SET state='needs_ted' WHERE task_id=?", (source["task_id"],))
    ops.journal.record_observed_verification(source["task_id"], {"candidate_commit": "1" * 40, "tree_hash": "2" * 40,
        "command": "pytest", "exit_code": 0, "log_ref": "private-log", "output_sha256": "3" * 64, "source": "observed_runner"})
    await capture(ops, work)
    out = await snapshot(ops, work["workspace_id"])
    assert any(item["status"] == "needs_ted" for item in out["brief"]["blockers"]["items"])
    delivery = out["brief"]["delivery"]
    verification = next(i for i in delivery["items"] if i["kind"] == "observed_verification")
    assert verification["status"] == "passed" and verification["details"]["candidate_commit"] == "1" * 40
    assert delivery["coverage"]["dod"] == "unassessed" and delivery["coverage"]["completion_ratio"] is None
    assert not any(i["kind"] == "deployment" for i in delivery["items"])


async def test_operation_success_is_not_a_delivery_fact_and_shared_evidence_is_deduplicated(ops):
    work = await prepared(ops)
    second = {**work, "work_item_id": await item(ops, work_items._get_item(ops.db, work["work_item_id"])["project_id"], "Other deliverable")}
    await bind_work(ops, second)
    operation = await source_operation(ops)
    for selected in (work, second):
        await link(ops, selected, "operation", operation["operation_id"])
        await capture(ops, selected)
    out = await snapshot(ops, work["workspace_id"])
    assert len(out["evidence"]) == len({e["evidence_id"] for e in out["evidence"]})
    assert len([e for e in out["evidence"] if e["source_kind"] == "operation_status"]) == 1
    assert not any(i["kind"] == "operation_status" for i in out["brief"]["delivery"]["items"])
    assert "PRIVATE" not in json.dumps(out)


async def test_decisions_only_use_accepted_fixed_inputs_not_later_discussion(ops):
    target, *_ = await planning(ops)
    open_input = await record(ops, target, slot="risk", value="Which login provider?", status="unresolved")
    discussion = pm_discussions.get_thread(ops, OWNER, **target)
    selected = [i["input_id"] for i in discussion["draft"]["selected_inputs"]]
    await act(ops, "pm.thread.plan.revise", target,
              {"selected_input_ids": [*selected, open_input["input_id"]], "rationale": "Keep an explicit unresolved decision"},
              {"expected_version": discussion["thread"]["version"], "expected_draft_id": discussion["draft"]["draft_id"]})
    proposal = await draft(ops, target)
    adopt(adoption(ops, target, proposal["revision_id"]))
    out = await snapshot(ops, target["workspace_id"])
    await record(ops, target, slot="risk", value="A later unrelated question", status="unresolved")
    newer = await snapshot(ops, target["workspace_id"])
    for result in (out, newer):
        items = result["brief"]["decisions"]["items"]
        assert any(i["text"] == "Which login provider?" and i["status"] == "unresolved" for i in items)
        assert "A later unrelated question" not in json.dumps(result)


async def test_repo_reference_is_an_attributed_claim_and_conflict_remains_open(ops):
    target, *_ = await planning(ops)
    repository_input = await record(ops, target, value="Repository appears to provide password reset", status="repo_evidence",
        source_ref={"kind": "repository", "ref": "example/product", "revision": "claimed-sha", "path": "README.md"})
    conflicting = await record(ops, target, slot="risk", value="Participants disagree about refunds", status="conflicting")
    discussion = pm_discussions.get_thread(ops, OWNER, **target)
    await act(ops, "pm.thread.plan.revise", target,
        {"selected_input_ids": [*[i["input_id"] for i in discussion["draft"]["selected_inputs"]],
                                repository_input["input_id"], conflicting["input_id"]], "rationale": "Preserve source uncertainty"},
        {"expected_version": discussion["thread"]["version"], "expected_draft_id": discussion["draft"]["draft_id"]})
    proposal = await draft(ops, target)
    adopt(adoption(ops, target, proposal["revision_id"]))
    out = await snapshot(ops, target["workspace_id"])
    claim = next(i for i in out["brief"]["goals"]["items"] if i["status"] == "repo_evidence")
    assert claim["details"]["evidence_verification"] == "unverified_claim" and claim["details"]["confirmed_by"] is None
    assert claim["source"]["source_ref"]["revision"] == "claimed-sha"
    assert any(i["status"] == "conflicting" for i in out["brief"]["decisions"]["items"])


async def test_work_metadata_version_difference_is_visible_without_refreshing_capture(ops):
    work = await prepared(ops)
    captured = await capture(ops, work)
    before = work_items._get_item(ops.db, work["work_item_id"])
    await act(ops, "work_item.update", {"work_item_id": work["work_item_id"]}, {"state": "waiting"},
              {"expected_version": before["version"]})
    out = await snapshot(ops, work["workspace_id"])
    saved = out["work"][0]
    assert saved["coverage"]["capture_matches_work_version"] is False
    assert saved["capture"]["capture_id"] == captured["capture_id"]
    assert saved["metadata_version"] != saved["capture"]["source_work_version"]
    assert not any(i["status"] == "waiting" for i in out["brief"]["blockers"]["items"])


async def test_saved_snapshot_rechecks_source_visibility_and_rebuilds_all_derived_items(ops):
    work = await prepared(ops)
    source = task(ops)
    await link(ops, work, "task", source["task_id"])
    ops.db.execute("UPDATE tasks SET state='needs_ted' WHERE task_id=?", (source["task_id"],))
    await capture(ops, work)
    before = await snapshot(ops, work["workspace_id"])
    assert any(e["source_kind"] == "execution_state" for e in before["evidence"])
    ops.db.execute("UPDATE tasks SET controller_authority='{}' WHERE task_id=?", (source["task_id"],))
    filtered = pm_snapshots.get_snapshot(ops, READER, work["workspace_id"], before["snapshot_id"])["snapshot"]
    assert filtered["snapshot_digest"] == before["snapshot_digest"] and filtered["view_digest"] != before["view_digest"]
    assert filtered["visibility"]["filtered"] and filtered["coverage"]["inaccessible_sources"]
    assert source["task_id"] not in json.dumps(filtered)
    assert not any(i["status"] == "needs_ted" for i in filtered["brief"]["blockers"]["items"])
    visible = {e["evidence_id"] for e in filtered["evidence"]}
    assert all(set(w["evidence_ids"]) <= visible for w in filtered["work"])
    assert filtered["work"][0]["coverage"]["inaccessible_sources"]
    assert all("brief" not in entry and "evidence" not in entry for entry in pm_snapshots.list_snapshots(ops, READER, work["workspace_id"])["snapshots"])


async def test_lost_reply_and_restart_recover_original_ids_without_duplicating_snapshot(ops, tmp_path):
    work = await prepared(ops)
    intent = request(ops, work["workspace_id"], title="Weekly Brief")
    operation, _ = ops.create(CONTRIBUTOR, **intent)
    applied = await pm_snapshots._run(OpContext(ops, operation))
    replay, created = ops.create(CONTRIBUTOR, **intent)
    assert not created and (await settle(ops, replay))["result"] == applied
    before = pm_snapshots.get_snapshot(ops, READER, work["workspace_id"], applied["snapshot_id"])
    restarted = Journal(tmp_path / "snapshot.db")
    try:
        service = OperationService(restarted)
        pm_snapshots.install(service)
        assert service.create(CONTRIBUTOR, **intent)[0]["result"] == applied
        assert pm_snapshots.get_snapshot(service, READER, work["workspace_id"], applied["snapshot_id"]) == before
        assert restarted.db.execute("SELECT COUNT(*) FROM pm_document_snapshots").fetchone()[0] == 1
    finally:
        restarted.close()


async def test_latest_uses_insertion_sequence_even_if_clock_rolls_back(ops, monkeypatch):
    work = await prepared(ops)
    first = await snapshot(ops, work["workspace_id"])
    old_clock = pm_snapshots.time.time
    with monkeypatch.context() as clock:
        clock.setattr(pm_snapshots.time, "time", lambda: old_clock() - 1000)
        second = await snapshot(ops, work["workspace_id"])
    assert second["as_of"] < first["as_of"]
    assert pm_snapshots.latest_snapshot(ops, OWNER, work["workspace_id"])["snapshot"]["snapshot_id"] == second["snapshot_id"]


@pytest.mark.parametrize("who", [READER, OUTSIDER, Principal(OWNER.actor, frozenset({"manage"})), Principal(OWNER.actor, frozenset({"observe"}))])
async def test_creation_requires_membership_role_and_both_scopes(ops, who):
    work = await prepared(ops)
    with pytest.raises(OperationError):
        ops.create(who, **request(ops, work["workspace_id"]))
    assert ops.db.execute("SELECT COUNT(*) FROM pm_document_snapshots").fetchone()[0] == 0


async def test_revoked_membership_blocks_queued_create_replay_and_every_read(ops):
    work = await prepared(ops)
    out = await snapshot(ops, work["workspace_id"])
    intent = request(ops, work["workspace_id"])
    queued, _ = ops.create(CONTRIBUTOR, **intent)
    ops.db.execute("DELETE FROM pm_workspace_members WHERE workspace_id=? AND actor=?", (work["workspace_id"], CONTRIBUTOR.actor))
    assert (await settle(ops, queued, success=False))["status"] == "failed"
    for action in (lambda: ops.create(CONTRIBUTOR, **intent),
                   lambda: pm_snapshots.get_snapshot(ops, CONTRIBUTOR, work["workspace_id"], out["snapshot_id"]),
                   lambda: pm_snapshots.latest_snapshot(ops, CONTRIBUTOR, work["workspace_id"]),
                   lambda: pm_snapshots.list_snapshots(ops, CONTRIBUTOR, work["workspace_id"])):
        with pytest.raises(OperationError, match="NOT_FOUND"):
            action()


async def test_plan_version_is_rechecked_at_execution_before_snapshot_effect(ops):
    target, *_ = await planning(ops)
    queued, _ = ops.create(CONTRIBUTOR, **request(ops, target["workspace_id"]))
    # Apply another admitted draft directly to interleave before the queued snapshot.
    from tests.test_pm_plans import draft_operation
    draft_op = draft_operation(ops, target)
    await pm_plans._run_draft(OpContext(ops, draft_op))
    result = await settle(ops, queued, success=False)
    assert result["error_code"] == "VERSION_CONFLICT"
    assert ops.db.execute("SELECT COUNT(*) FROM pm_document_snapshots").fetchone()[0] == 0


@pytest.mark.parametrize("change", [
    {"params": {"facts": {"done": True}}}, {"params": {"title": ""}},
    {"preconditions": {}}, {"preconditions": {"expected_plan_version": True}},
    {"preconditions": {"expected_plan_version": -1}}, {"target_extra": True},
])
async def test_malformed_create_cannot_inject_facts_or_omit_exact_plan_version(ops, change):
    work = await prepared(ops)
    intent = request(ops, work["workspace_id"])
    if change.get("target_extra"):
        intent["target"]["work_item_id"] = work["work_item_id"]
    else:
        intent.update(change)
    with pytest.raises(OperationError):
        ops.create(CONTRIBUTOR, **intent)


async def test_pagination_is_bounded_metadata_only_and_wrong_workspace_is_not_found(ops):
    work = await prepared(ops)
    snapshots = [await snapshot(ops, work["workspace_id"], title=f"Brief {i}") for i in range(3)]
    first = pm_snapshots.list_snapshots(ops, READER, work["workspace_id"], limit=2)
    second = pm_snapshots.list_snapshots(ops, READER, work["workspace_id"], limit=2, after=first["next_cursor"])
    assert [s["snapshot_id"] for s in first["snapshots"] + second["snapshots"]] == [s["snapshot_id"] for s in snapshots]
    assert second["next_cursor"] is None
    assert all("document" not in s and "evidence" not in s for s in first["snapshots"])
    other = await workspace(ops)
    with pytest.raises(OperationError, match="PM_SNAPSHOT_NOT_FOUND"):
        pm_snapshots.get_snapshot(ops, OWNER, other["workspace_id"], snapshots[0]["snapshot_id"])
    assert pm_snapshots.latest_snapshot(ops, OWNER, other["workspace_id"])["snapshot"] is None
    for kwargs in ({"limit": 101}, {"limit": True}, {"after": -1}, {"after": "1"}):
        with pytest.raises(OperationError, match="INVALID_PARAMS"):
            pm_snapshots.list_snapshots(ops, OWNER, work["workspace_id"], **kwargs)


async def test_work_and_evidence_bounds_keep_partial_coverage_and_fixed_source_refs(ops, monkeypatch):
    work = await prepared(ops)
    second = {**work, "work_item_id": await item(ops, work_items._get_item(ops.db, work["work_item_id"])["project_id"], "Second")}
    await bind_work(ops, second)
    operation = await source_operation(ops)
    await link(ops, work, "operation", operation["operation_id"])
    await capture(ops, work)
    monkeypatch.setattr(pm_snapshots, "MAX_WORK", 1)
    monkeypatch.setattr(pm_snapshots, "MAX_EVIDENCE", 1)
    out = await snapshot(ops, work["workspace_id"])
    assert out["coverage"]["work_truncated"] and out["coverage"]["evidence_truncated"]
    assert len(out["work"]) == len(out["evidence"]) == 1
    assert out["work"][0]["capture"]["capture_id"] and len(out["work"][0]["evidence_ids"]) == 1


async def test_actual_utf8_byte_bound_truncates_context_without_slicing_source_facts(ops, monkeypatch):
    work = await prepared(ops)
    source = task(ops)
    await link(ops, work, "task", source["task_id"])
    ops.db.execute("UPDATE tasks SET state='needs_ted' WHERE task_id=?", (source["task_id"],))
    await capture(ops, work)
    first = await snapshot(ops, work["workspace_id"], title="進度報告" * 30)
    full = {"version": 1, "workspace_id": work["workspace_id"], "snapshot": first}
    full_bytes = len(pm_snapshots._json(full).encode("utf-8"))
    monkeypatch.setattr(pm_snapshots, "MAX_SNAPSHOT_BYTES", full_bytes - 1500)
    reduced = await snapshot(ops, work["workspace_id"], title="進度報告" * 30)
    output = {"version": 1, "workspace_id": work["workspace_id"], "snapshot": reduced}
    assert len(pm_snapshots._json(output).encode("utf-8")) <= pm_snapshots.MAX_SNAPSHOT_BYTES
    assert reduced["coverage"]["bytes_truncated"]
    for evidence in reduced["evidence"]:
        assert evidence == pm_evidence.get_evidence(ops, OWNER, work["workspace_id"], evidence["evidence_id"])["evidence"]


async def test_snapshot_tables_are_append_only_and_installation_preserves_existing_rows(ops):
    work = await prepared(ops)
    out = await snapshot(ops, work["workspace_id"])
    for table in ("pm_document_snapshots", "pm_snapshot_applied"):
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            ops.db.execute(f"DELETE FROM {table}")
    pm_snapshots.install(ops)
    assert pm_snapshots.get_snapshot(ops, OWNER, work["workspace_id"], out["snapshot_id"])["snapshot"] == out
