"""Explicit schedule commitments, independent forecasts and fixed evidence actuals."""
from __future__ import annotations

import copy
import json
import sqlite3
import uuid

import pytest

from bat_agent_connector import (
    artifacts,
    pm_discussions,
    pm_evidence,
    pm_plans,
    pm_roadmap,
    pm_snapshots,
    pm_workspace,
    work_items,
)
from bat_agent_connector.api_auth import Principal
from bat_agent_connector.operations import OpContext, OperationError, OperationService
from bat_agent_connector.task_journal import Journal
from tests.test_pm_discussions import CONTRIBUTOR, OUTSIDER, OWNER, READER, settle, workspace
from tests.test_pm_evidence import act, bind_work, capture, link, source_operation, task
from tests.test_pm_plans import adopt, adoption, draft, draft_operation
from tests.test_pm_snapshots import accepted, snapshot, work_for


@pytest.fixture
def ops(tmp_path):
    journal = Journal(tmp_path / "roadmap.db")
    service = OperationService(journal, actions=work_items.ACTIONS)
    service.context["artifact_store"] = artifacts.ArtifactStore(service)
    pm_workspace.install(service)
    pm_discussions.install(service)
    pm_plans.install(service)
    pm_evidence.install(service)
    pm_snapshots.install(service)
    version = journal.db.execute("PRAGMA user_version").fetchone()[0]
    pm_roadmap.install(service)
    pm_roadmap.install(service)
    assert journal.db.execute("PRAGMA user_version").fetchone()[0] == version
    yield service
    journal.close()


def request(ops, wid, kind="baseline", **params):
    current = pm_roadmap.head(ops.db, wid)
    pre = {"expected_roadmap_version": current["version"]}
    if kind == "baseline":
        pre["expected_plan_version"] = pm_plans.head(ops.db, wid)["version"]
        params = {"rationale": "Record this explicit schedule", **params}
    else:
        params = {"baseline_revision_id": current["baseline_revision_id"], **params}
        if kind == "forecast":
            params = {"rationale": "Revise our current forecast", **params}
    action = next(action for action, value in pm_roadmap.ACTIONS.items() if value == kind)
    return {"action": action, "target": {"workspace_id": wid}, "params": params,
            "preconditions": pre, "idempotency_key": uuid.uuid4().hex}


async def revision(ops, wid, kind="baseline", *, who=OWNER, **params):
    operation, _ = ops.create(who, **request(ops, wid, kind, **params))
    result = (await settle(ops, operation))["result"]
    return pm_roadmap.get_revision(ops, who, wid, result["revision_id"])["revision"]


def milestone(plan, mid="release", **values):
    return {"milestone_id": mid, "title": "Usable membership", "plan_revision_id": plan["revision_id"],
            "requirement_ids": ["login"], "baseline_date": "2026-11-01", "depends_on": [], **values}


async def mapped(ops):
    target, plan, *_ = await accepted(ops)
    work = await work_for(ops, target)
    binding = await bind_work(ops, work, plan_revision_id=plan["revision_id"], requirement_ids=["login"])
    selected = milestone(plan, work_binding_ids=[binding["binding_id"]])
    baseline = await revision(ops, target["workspace_id"], milestones=[selected])
    return target, plan, work, binding, selected, baseline


async def test_empty_read_never_infers_milestones_from_the_plan(ops):
    target, *_ = await accepted(ops)
    out = pm_roadmap.get_roadmap(ops, READER, target["workspace_id"])
    assert out["head"]["version"] == 0
    assert out["baseline"] is out["forecast"] is out["observation"] is None
    assert pm_roadmap.list_revisions(ops, READER, target["workspace_id"])["revisions"] == []


async def test_baseline_is_an_explicit_schedule_with_exact_adopted_plan_and_no_execution(ops):
    target, plan, *_ = await accepted(ops)
    wid = target["workspace_id"]
    before = pm_plans.accepted_plan(ops, OWNER, wid)
    saved = await revision(ops, wid, title="Release schedule", milestones=[milestone(plan)])
    document = saved["document"]
    assert document["status"] == "accepted_scheduling_baseline" and document["git_materialization"] == "not_recorded"
    assert not document["execution_authorized"] and pm_plans.accepted_plan(ops, OWNER, wid) == before
    selected = document["milestones"][0]
    assert selected["first_baseline_revision_id"] == saved["revision_id"]
    assert selected["original_baseline_date"] == selected["baseline_date"] == "2026-11-01"
    assert selected["plan_document_digest"] == plan["document_digest"]
    assert document["changes"]["added_requirement_refs"] == [{"plan_revision_id": plan["revision_id"], "requirement_id": "login"}]
    assert saved["created_by"] == OWNER.actor and saved["baseline_revision_id"] == saved["revision_id"]
    assert ops.db.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 0


@pytest.mark.parametrize("invalid", ["2026-02-29", "2026-04-31", "2026-1-01", "2026-13-01", "0000-01-01",
                                     "2026-11-01T00:00:00Z", "2026-W41-1", "2026-W41", "20261011", "", True, 20261101])
async def test_dates_reject_invalid_calendar_values_before_effects(ops, invalid):
    target, plan, *_ = await accepted(ops)
    with pytest.raises(OperationError, match="INVALID_PARAMS"):
        ops.create(OWNER, **request(ops, target["workspace_id"], milestones=[milestone(plan, baseline_date=invalid)]))
    assert pm_roadmap.head(ops.db, target["workspace_id"])["version"] == 0


async def test_dates_may_be_unknown_and_dependencies_do_not_infer_them(ops):
    target, plan, *_ = await accepted(ops)
    saved = await revision(ops, target["workspace_id"], milestones=[milestone(plan, "alpha", baseline_date="2028-02-29"),
        milestone(plan, "beta", baseline_date=None, depends_on=["alpha"])])
    assert [m["baseline_date"] for m in saved["document"]["milestones"]] == ["2028-02-29", None]


@pytest.mark.parametrize("shape", ["duplicate", "self", "missing", "cycle", "duplicate_dependency"])
async def test_dependency_graph_is_validated_before_commit(ops, shape):
    target, plan, *_ = await accepted(ops)
    selected = [milestone(plan, "alpha"), milestone(plan, "beta")]
    if shape == "duplicate":
        selected[1]["milestone_id"] = "alpha"
    elif shape == "self":
        selected[0]["depends_on"] = ["alpha"]
    elif shape == "missing":
        selected[0]["depends_on"] = ["missing"]
    elif shape == "cycle":
        selected[0]["depends_on"], selected[1]["depends_on"] = ["beta"], ["alpha"]
    else:
        selected[1]["depends_on"] = ["alpha", "alpha"]
    with pytest.raises(OperationError, match="INVALID_PARAMS"):
        ops.create(OWNER, **request(ops, target["workspace_id"], milestones=selected))


async def test_scope_requires_same_workspace_adopted_revision_and_exact_requirement_ids(ops):
    target, plan, *_ = await accepted(ops)
    candidate = await draft(ops, target)
    other, foreign, *_ = await accepted(ops)
    assert other != target
    for selected, code in ((milestone(candidate), "PM_ADOPTED_PLAN_REQUIRED"),
                           (milestone(plan, requirement_ids=["invented"]), "INVALID_PARAMS"),
                           (milestone(foreign), "NOT_FOUND")):
        with pytest.raises(OperationError, match=code):
            ops.create(OWNER, **request(ops, target["workspace_id"], milestones=[selected]))


async def test_historically_adopted_scope_does_not_follow_the_latest_accepted_pointer(ops):
    target, first, confirmed, _ = await accepted(ops)
    later = await draft(ops, target, requirements=[{"id": "next", "source_input_id": confirmed}])
    adopt(adoption(ops, target, later["revision_id"], previous=first["revision_id"]))
    saved = await revision(ops, target["workspace_id"], milestones=[milestone(first)])
    item = saved["document"]["milestones"][0]
    assert item["plan_revision_id"] == first["revision_id"] and item["requirement_ids"] == ["login"]
    assert saved["document"]["plan_head_at_creation"]["accepted_revision_id"] == later["revision_id"]
    assert "drift" not in saved["document"] and "completion" not in saved["document"]


async def test_work_bindings_are_explicit_exact_and_cannot_mix_two_revisions_of_one_work(ops):
    target, plan, work, old, selected, _ = await mapped(ops)
    newer = await bind_work(ops, work, plan_revision_id=plan["revision_id"], requirement_ids=["login"])
    # Historical binding is still valid; new association is not silently substituted.
    saved = await revision(ops, target["workspace_id"], milestones=[selected])
    assert saved["document"]["milestones"][0]["work_binding_ids"] == [old["binding_id"]]
    with pytest.raises(OperationError, match="INVALID_PARAMS"):
        ops.create(OWNER, **request(ops, target["workspace_id"], milestones=[{**selected,
            "work_binding_ids": [old["binding_id"], newer["binding_id"]]}]))
    with pytest.raises(OperationError, match="PM_MILESTONE_BINDING_SCOPE"):
        ops.create(OWNER, **request(ops, target["workspace_id"], milestones=[{**selected, "requirement_ids": []}]))
    unscoped = await bind_work(ops, work)
    with pytest.raises(OperationError, match="PM_MILESTONE_BINDING_SCOPE"):
        ops.create(OWNER, **request(ops, target["workspace_id"], milestones=[{**selected, "work_binding_ids": [unscoped["binding_id"]]}]))


async def test_forecasts_never_rewrite_baseline_and_unspecified_dates_stay_unknown(ops):
    target, plan, *_ = await accepted(ops)
    wid = target["workspace_id"]
    baseline = await revision(ops, wid, milestones=[milestone(plan, "alpha"), milestone(plan, "beta")])
    first = await revision(ops, wid, "forecast", updates=[{"milestone_id": "alpha", "forecast_date": "2026-12-01"}])
    assert first["document"]["milestones"] == [{"milestone_id": "alpha", "forecast_date": "2026-12-01"},
                                                {"milestone_id": "beta", "forecast_date": None}]
    second = await revision(ops, wid, "forecast", updates=[{"milestone_id": "beta", "forecast_date": "2026-12-02"}])
    third = await revision(ops, wid, "forecast", updates=[{"milestone_id": "alpha", "forecast_date": None}])
    assert second["previous_revision_id"] == first["revision_id"] and third["previous_revision_id"] == second["revision_id"]
    assert third["document"]["milestones"] == [{"milestone_id": "alpha", "forecast_date": None},
                                               {"milestone_id": "beta", "forecast_date": "2026-12-02"}]
    assert pm_roadmap.get_roadmap(ops, READER, wid)["baseline"] == baseline


@pytest.mark.parametrize("updates", [[{"milestone_id": "release"}], [{"milestone_id": "release", "forecast_date": "2026-02-29"}],
    [{"milestone_id": "unknown", "forecast_date": None}], [{"milestone_id": "release", "forecast_date": None}] * 2, []])
async def test_forecast_updates_require_explicit_valid_dates_and_distinct_current_milestones(ops, updates):
    target, plan, *_ = await accepted(ops)
    await revision(ops, target["workspace_id"], milestones=[milestone(plan)])
    with pytest.raises(OperationError, match="INVALID_PARAMS"):
        ops.create(OWNER, **request(ops, target["workspace_id"], "forecast", updates=updates))


async def test_removal_and_reintroduction_preserve_first_identity_and_baseline_history(ops):
    target, plan, *_ = await accepted(ops)
    wid = target["workspace_id"]
    first = await revision(ops, wid, milestones=[milestone(plan, baseline_date=None)])
    forecast = await revision(ops, wid, "forecast", updates=[{"milestone_id": "release", "forecast_date": "2026-12-01"}])
    snap = await snapshot(ops, wid)
    observation = await revision(ops, wid, "observation", snapshot_id=snap["snapshot_id"], who=CONTRIBUTOR)
    removed = await revision(ops, wid, milestones=[])
    assert removed["document"]["changes"]["removed_milestone_ids"] == ["release"]
    assert removed["document"]["changes"]["removed_requirement_refs"] == first["document"]["changes"]["added_requirement_refs"]
    current = pm_roadmap.get_roadmap(ops, OWNER, wid)
    assert current["forecast"] is current["observation"] is None
    restored = await revision(ops, wid, milestones=[milestone(plan, title="Renamed release", baseline_date="2027-01-01")])
    selected = restored["document"]["milestones"][0]
    assert selected["first_baseline_revision_id"] == first["revision_id"] and selected["original_baseline_date"] is None
    assert selected["baseline_date"] == "2027-01-01" and restored["document"]["changes"]["reintroduced_milestone_ids"] == ["release"]
    assert restored["previous_revision_id"] == removed["revision_id"]
    for old in (first, forecast, observation):
        assert pm_roadmap.get_revision(ops, OWNER, wid, old["revision_id"])["revision"] == old


async def test_observation_freezes_exact_capture_and_keeps_verification_distinct_from_completion(ops):
    target, _, work, binding, _, baseline = await mapped(ops)
    source = task(ops)
    await link(ops, work, "task", source["task_id"])
    ops.db.execute("UPDATE tasks SET state='needs_ted' WHERE task_id=?", (source["task_id"],))
    ops.journal.record_observed_verification(source["task_id"], {"candidate_commit": "1" * 40, "tree_hash": "2" * 40,
        "command": "pytest", "exit_code": 0, "log_ref": "PRIVATE log", "output_sha256": "3" * 64, "source": "observed_runner"})
    captured = await capture(ops, work)
    snap = await snapshot(ops, target["workspace_id"])
    observation = await revision(ops, target["workspace_id"], "observation", snapshot_id=snap["snapshot_id"], who=CONTRIBUTOR)
    document = observation["document"]
    assert document["baseline_revision_id"] == baseline["revision_id"] and document["snapshot_digest"] == snap["snapshot_digest"]
    selected = document["milestones"][0]
    assert selected["work"] == [{"work_binding_id": binding["binding_id"], "work_item_id": work["work_item_id"],
                                 "capture_id": captured["capture_id"], "match_status": "exact"}]
    assert selected["actual"]["completion"] == selected["actual"]["dod"] == "unassessed"
    assert selected["actual"]["actual_finish_date"] is None and selected["coverage"]["scope_assessment"] == "unassessed"
    fact = next(e for e in selected["actual"]["facts"] if e["source_kind"] == "observed_verification")
    assert fact["assessment"]["verification"] == "passed" and fact["version_refs"]["candidate_commit"] == "1" * 40
    assert "PRIVATE" not in json.dumps(observation)
    assert document["as_of"] == snap["as_of"] and document["journal_cursor"] == snap["journal_cursor"]
    assert not document["coverage"]["network_checked"]


@pytest.mark.parametrize("change,expected", [("before_work", "missing_work"), ("no_capture", "missing_capture"),
    ("new_binding", "binding_mismatch"), ("old_capture", "capture_binding_mismatch")])
async def test_observation_cannot_replace_exact_binding_with_newest_work_data(ops, change, expected):
    target, plan, *_ = await accepted(ops)
    wid = target["workspace_id"]
    if change == "before_work":
        snap = await snapshot(ops, wid)
    work = await work_for(ops, target)
    binding = await bind_work(ops, work, plan_revision_id=plan["revision_id"], requirement_ids=["login"])
    if change in {"new_binding", "old_capture"}:
        await capture(ops, work)
        newer = await bind_work(ops, work, plan_revision_id=plan["revision_id"], requirement_ids=["login"])
        if change == "old_capture":
            binding = newer
    if change != "before_work":
        snap = await snapshot(ops, wid)
    await revision(ops, wid, milestones=[milestone(plan, work_binding_ids=[binding["binding_id"]])])
    observation = await revision(ops, wid, "observation", snapshot_id=snap["snapshot_id"])
    selected = observation["document"]["milestones"][0]
    assert selected["work"][0]["match_status"] == expected and selected["work"][0]["capture_id"] is None
    assert selected["evidence_ids"] == selected["actual"]["facts"] == []


async def test_later_work_plan_forecast_and_capture_changes_do_not_rewrite_fixed_observation(ops):
    target, plan, work, _, _, _ = await mapped(ops)
    wid = target["workspace_id"]
    await capture(ops, work)
    first_forecast = await revision(ops, wid, "forecast", updates=[{"milestone_id": "release", "forecast_date": "2026-12-01"}])
    snap = await snapshot(ops, wid)
    original = await revision(ops, wid, "observation", snapshot_id=snap["snapshot_id"])
    newer = await draft(ops, target)
    adopt(adoption(ops, target, newer["revision_id"], previous=plan["revision_id"]))
    await bind_work(ops, work, plan_revision_id=newer["revision_id"])
    before = work_items._get_item(ops.db, work["work_item_id"])
    await act(ops, "work_item.update", {"work_item_id": work["work_item_id"]}, {"state": "done"}, {"expected_version": before["version"]})
    await capture(ops, work)
    await snapshot(ops, wid)
    latest_forecast = await revision(ops, wid, "forecast", updates=[{"milestone_id": "release", "forecast_date": None}])
    current = pm_roadmap.get_roadmap(ops, OWNER, wid)
    assert current["observation"] == original and current["forecast"] == latest_forecast
    assert original["document"]["forecast_revision_id"] == first_forecast["revision_id"]


async def test_observation_rechecks_source_visibility_and_filters_all_actual_references(ops):
    target, _, work, _, _, _ = await mapped(ops)
    source = task(ops)
    await link(ops, work, "task", source["task_id"])
    await capture(ops, work)
    snap = await snapshot(ops, target["workspace_id"])
    original = await revision(ops, target["workspace_id"], "observation", snapshot_id=snap["snapshot_id"])
    assert source["task_id"] in json.dumps(original)
    ops.db.execute("UPDATE tasks SET controller_authority='{}' WHERE task_id=?", (source["task_id"],))
    filtered = pm_roadmap.get_revision(ops, READER, target["workspace_id"], original["revision_id"])["revision"]
    assert source["task_id"] not in json.dumps(filtered)
    assert filtered["document_digest"] == original["document_digest"] and filtered["view_digest"] != original["view_digest"]
    selected = filtered["document"]["milestones"][0]
    assert selected["coverage"]["inaccessible_sources"] and filtered["document"]["coverage"]["inaccessible_sources"]
    assert selected["evidence_ids"] == [f["evidence_id"] for f in selected["actual"]["facts"]]
    assert pm_roadmap.get_roadmap(ops, READER, target["workspace_id"])["observation"] == filtered


async def test_successful_operations_and_done_claims_do_not_mean_dod_or_deployment(ops):
    target, _, work, _, _, _ = await mapped(ops)
    source = await source_operation(ops)
    await link(ops, work, "operation", source["operation_id"])
    before = work_items._get_item(ops.db, work["work_item_id"])
    await act(ops, "work_item.update", {"work_item_id": work["work_item_id"]}, {"state": "done"}, {"expected_version": before["version"]})
    await capture(ops, work)
    snap = await snapshot(ops, target["workspace_id"])
    observed = await revision(ops, target["workspace_id"], "observation", snapshot_id=snap["snapshot_id"])
    actual = observed["document"]["milestones"][0]["actual"]
    assert actual["dod"] == actual["completion"] == "unassessed" and actual["actual_finish_date"] is None
    assert any(f["assessment"].get("state") == "done" for f in actual["facts"])
    assert any(f["assessment"].get("status") == "succeeded" for f in actual["facts"])
    assert not any(f["source_kind"] == "deployment" for f in actual["facts"])


@pytest.mark.parametrize("kind", ["baseline", "forecast", "observation"])
async def test_lost_ack_and_restart_return_original_revision_and_do_not_duplicate_effects(ops, tmp_path, kind):
    target, plan, *_ = await accepted(ops)
    wid = target["workspace_id"]
    if kind == "baseline":
        params = {"milestones": [milestone(plan)]}
    else:
        await revision(ops, wid, milestones=[milestone(plan)])
        params = ({"updates": [{"milestone_id": "release", "forecast_date": None}]} if kind == "forecast"
                  else {"snapshot_id": (await snapshot(ops, wid))["snapshot_id"]})
    intent = request(ops, wid, kind, **params)
    operation, _ = ops.create(OWNER, **intent)
    saved = await pm_roadmap._run(OpContext(ops, operation))
    replay, created = ops.create(OWNER, **intent)
    assert not created and (await settle(ops, replay))["result"] == saved
    before = pm_roadmap.get_revision(ops, READER, wid, saved["revision_id"])
    journal = Journal(tmp_path / "roadmap.db")
    try:
        restarted = OperationService(journal)
        pm_roadmap.install(restarted)
        assert restarted.create(OWNER, **intent)[0]["result"] == saved
        assert pm_roadmap.get_revision(restarted, READER, wid, saved["revision_id"]) == before
        assert journal.db.execute("SELECT COUNT(*) FROM pm_roadmap_revisions WHERE kind=?", (kind,)).fetchone()[0] == 1
    finally:
        journal.close()


async def test_stale_roadmap_version_and_queued_plan_changes_fail_before_effect(ops):
    target, plan, *_ = await accepted(ops)
    wid = target["workspace_id"]
    intent = request(ops, wid, milestones=[milestone(plan)])
    first, _ = ops.create(OWNER, **intent)
    second, _ = ops.create(OWNER, **{**intent, "idempotency_key": uuid.uuid4().hex})
    await settle(ops, first)
    assert ops.get(second["operation_id"])["error_code"] == "VERSION_CONFLICT"
    pending, _ = ops.create(OWNER, **request(ops, wid, milestones=[milestone(plan)]))
    await pm_plans._run_draft(OpContext(ops, draft_operation(ops, target)))
    assert (await settle(ops, pending, success=False))["error_code"] == "VERSION_CONFLICT"
    assert pm_roadmap.head(ops.db, wid)["version"] == 1


async def test_rebaseline_invalidates_queued_old_baseline_forecast_even_with_new_head_version(ops):
    target, plan, *_ = await accepted(ops)
    wid = target["workspace_id"]
    old = await revision(ops, wid, milestones=[milestone(plan)])
    await revision(ops, wid, milestones=[milestone(plan, baseline_date=None)])
    with pytest.raises(OperationError, match="PM_ROADMAP_BASELINE_CHANGED"):
        ops.create(OWNER, **request(ops, wid, "forecast", baseline_revision_id=old["revision_id"],
                                   updates=[{"milestone_id": "release", "forecast_date": None}]))


@pytest.mark.parametrize("who", [CONTRIBUTOR, READER, OUTSIDER, Principal(OWNER.actor, frozenset({"manage"})), Principal(OWNER.actor, frozenset({"observe"}))])
async def test_only_current_owner_with_both_scopes_can_set_baseline_and_forecast(ops, who):
    target, plan, *_ = await accepted(ops)
    wid = target["workspace_id"]
    with pytest.raises(OperationError):
        ops.create(who, **request(ops, wid, milestones=[milestone(plan)]))
    await revision(ops, wid, milestones=[milestone(plan)])
    with pytest.raises(OperationError):
        ops.create(who, **request(ops, wid, "forecast", updates=[{"milestone_id": "release", "forecast_date": None}]))


async def test_queued_membership_revocation_blocks_observation_replay_and_all_reads(ops):
    target, plan, *_ = await accepted(ops)
    wid = target["workspace_id"]
    baseline = await revision(ops, wid, milestones=[milestone(plan)])
    snap = await snapshot(ops, wid)
    intent = request(ops, wid, "observation", snapshot_id=snap["snapshot_id"])
    for who in (READER, OUTSIDER):
        with pytest.raises(OperationError):
            ops.create(who, **intent)
    operation, _ = ops.create(CONTRIBUTOR, **intent)
    ops.db.execute("DELETE FROM pm_workspace_members WHERE workspace_id=? AND actor=?", (wid, CONTRIBUTOR.actor))
    assert (await settle(ops, operation, success=False))["status"] == "failed"
    for call in (lambda: ops.create(CONTRIBUTOR, **intent), lambda: pm_roadmap.get_roadmap(ops, CONTRIBUTOR, wid),
                 lambda: pm_roadmap.get_revision(ops, CONTRIBUTOR, wid, baseline["revision_id"]),
                 lambda: pm_roadmap.list_revisions(ops, CONTRIBUTOR, wid)):
        with pytest.raises(OperationError, match="NOT_FOUND"):
            call()


async def test_pagination_uses_insertion_order_even_when_clock_rolls_back(ops, monkeypatch):
    target, plan, *_ = await accepted(ops)
    wid = target["workspace_id"]
    baseline = await revision(ops, wid, milestones=[milestone(plan)])
    current_time = pm_roadmap.time.time
    with monkeypatch.context() as clock:
        clock.setattr(pm_roadmap.time, "time", lambda: current_time() - 1000)
        forecast = await revision(ops, wid, "forecast", updates=[{"milestone_id": "release", "forecast_date": None}])
    assert forecast["created_at"] < baseline["created_at"]
    first = pm_roadmap.list_revisions(ops, READER, wid, limit=1)
    second = pm_roadmap.list_revisions(ops, READER, wid, after=first["next_cursor"], limit=1)
    assert [r["revision_id"] for r in first["revisions"] + second["revisions"]] == [baseline["revision_id"], forecast["revision_id"]]
    assert second["next_cursor"] is None and "document" not in first["revisions"][0]
    assert [r["revision_id"] for r in pm_roadmap.list_revisions(ops, READER, wid, kind="baseline")["revisions"]] == [baseline["revision_id"]]
    other = await workspace(ops)
    with pytest.raises(OperationError, match="NOT_FOUND"):
        pm_roadmap.get_revision(ops, OWNER, other["workspace_id"], baseline["revision_id"])
    for kwargs in ({"limit": True}, {"limit": 101}, {"after": -1}, {"after": "1"}, {"kind": []}, {"kind": "actual"}):
        with pytest.raises(OperationError, match="INVALID_PARAMS"):
            pm_roadmap.list_revisions(ops, READER, wid, **kwargs)


@pytest.mark.parametrize("change", [{"params": {"milestones": [], "actual_finish_date": "2026-11-01"}},
    {"params": {"milestones": []}}, {"preconditions": {}}, {"preconditions": {"expected_roadmap_version": True, "expected_plan_version": 0}},
    {"preconditions": {"expected_roadmap_version": -1, "expected_plan_version": 0}}, {"target_extra": True}])
async def test_malformed_requests_cannot_inject_results_or_bypass_cas(ops, change):
    target, plan, *_ = await accepted(ops)
    intent = request(ops, target["workspace_id"], milestones=[milestone(plan)])
    if change.get("target_extra"):
        intent["target"]["project_id"] = "foreign"
    else:
        intent.update(change)
    with pytest.raises(OperationError):
        ops.create(OWNER, **intent)


async def test_observation_fact_reference_limits_preserve_exact_matches_and_mark_partial_coverage(ops, monkeypatch):
    target, _, work, _, _, _ = await mapped(ops)
    source = task(ops)
    await link(ops, work, "task", source["task_id"])
    await capture(ops, work)
    snap = await snapshot(ops, target["workspace_id"])
    monkeypatch.setattr(pm_roadmap, "MAX_OBSERVATION_REFS", 1)
    out = await revision(ops, target["workspace_id"], "observation", snapshot_id=snap["snapshot_id"])
    selected = out["document"]["milestones"][0]
    assert selected["work"][0]["match_status"] == "exact" and len(selected["actual"]["facts"]) == 1
    assert selected["coverage"]["evidence_truncated"] and out["document"]["coverage"]["evidence_truncated"]
    assert out["document"]["snapshot_digest"] == snap["snapshot_digest"]


async def test_utf8_observation_bound_keeps_whole_facts_and_marks_byte_truncation(ops, monkeypatch):
    target, _, work, _, _, _ = await mapped(ops)
    source = task(ops)
    await link(ops, work, "task", source["task_id"])
    await capture(ops, work)
    snap = await snapshot(ops, target["workspace_id"], title="進度報告" * 20)
    before = await revision(ops, target["workspace_id"], "observation", snapshot_id=snap["snapshot_id"])
    size = len(pm_roadmap._json(before["document"]).encode("utf-8"))
    monkeypatch.setattr(pm_roadmap, "MAX_OBSERVATION_BYTES", size - 100)
    limited = await revision(ops, target["workspace_id"], "observation", snapshot_id=snap["snapshot_id"])
    assert len(pm_roadmap._json(limited["document"]).encode("utf-8")) <= pm_roadmap.MAX_OBSERVATION_BYTES
    assert limited["document"]["coverage"]["bytes_truncated"]
    originals = {f["evidence_id"]: f for f in before["document"]["milestones"][0]["actual"]["facts"]}
    assert all(originals[f["evidence_id"]] == f for f in limited["document"]["milestones"][0]["actual"]["facts"])
    assert limited["document"]["milestones"][0]["work"] == before["document"]["milestones"][0]["work"]


async def test_document_byte_limit_counts_utf8_and_rejects_before_revision_written(ops, monkeypatch):
    target, plan, *_ = await accepted(ops)
    wid = target["workspace_id"]
    selected = [milestone(plan, title="里程碑" * 60)]
    intent = request(ops, wid, milestones=selected, rationale="更新日期" * 100)
    _, document = pm_roadmap._check(ops, OWNER, intent["action"], intent["target"], intent["params"], intent["preconditions"], "pmrb_" + "0" * 32)
    encoded = len(pm_roadmap._json(document).encode("utf-8"))
    assert encoded > len(pm_roadmap._json(document))
    monkeypatch.setattr(pm_roadmap, "MAX_DOCUMENT_BYTES", encoded - 1)
    with pytest.raises(OperationError, match="PM_ROADMAP_TOO_LARGE"):
        ops.create(OWNER, **intent)
    assert pm_roadmap.head(ops.db, wid)["version"] == 0


async def test_history_and_original_milestone_identity_are_append_only(ops):
    target, plan, *_ = await accepted(ops)
    saved = await revision(ops, target["workspace_id"], milestones=[milestone(plan)])
    for table in ("pm_roadmap_revisions", "pm_roadmap_milestones", "pm_roadmap_applied"):
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            ops.db.execute(f"DELETE FROM {table}")
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        ops.db.execute("UPDATE pm_roadmap_milestones SET original_baseline_date='2020-01-01'")
    pm_roadmap.install(ops)
    assert pm_roadmap.get_revision(ops, OWNER, target["workspace_id"], saved["revision_id"])["revision"] == copy.deepcopy(saved)
