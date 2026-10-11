"""Plan changes remain fixed facts; work impact is an explicit, read-only candidate."""
from __future__ import annotations

import copy
import json
import uuid

import pytest

from bat_agent_connector import pm_discussions, pm_plans, pm_workspace, work_items
from bat_agent_connector import pm_plan_diff as diff
from bat_agent_connector.api_auth import Principal
from bat_agent_connector.operations import OperationError
from tests.test_pm_discussions import OUTSIDER, OWNER, READER, record, settle
from tests.test_pm_discussions import act as discussion_action
from tests.test_pm_evidence import (
    act,
    bind_project,
    bind_work,
    item,
    project,
)
from tests.test_pm_evidence import ops as ops
from tests.test_pm_plans import adopt, adoption, draft, planning


async def pair(ops):
    target, confirmed, assumption, acceptance = await planning(ops)
    base = [{"id": "login", "source_input_id": confirmed, "acceptance_input_ids": [acceptance], "priority": "must"},
            {"id": "reset", "source_input_id": acceptance, "depends_on": ["login"]},
            {"id": "pricing", "source_input_id": assumption}]
    one = await draft(ops, target, markdown="The selected membership direction remains a proposal.\n", requirements=base)
    adopt(adoption(ops, target, one["revision_id"]))
    changed = copy.deepcopy(base[:2])
    changed[0].update(acceptance_input_ids=[], priority="should")
    changed.append({"id": "pricing.review", "source_input_id": assumption})
    two = await draft(ops, target, markdown="The selected membership direction remains a proposal.\n", requirements=changed)
    return target, one, two, (confirmed, assumption, acceptance)


def compare(ops, target, one, two, **kwargs):
    return diff.compare(ops, OWNER, target["workspace_id"], one["revision_id"], two["revision_id"], **kwargs)["comparison"]


async def work_binding(ops, target, revision, requirement_ids):
    pid = await project(ops, "Example " + uuid.uuid4().hex)
    wid = await item(ops, pid, "A mutable title must never become historical evidence")
    await bind_project(ops, target["workspace_id"], pid)
    work = {"workspace_id": target["workspace_id"], "work_item_id": wid}
    result = await bind_work(ops, work, plan_revision_id=revision["revision_id"], requirement_ids=requirement_ids)
    return work, result["binding_id"]


async def test_structured_diff_preserves_acceptance_sources_and_all_eight_domains(ops):
    target, one, two, (_, assumption, acceptance) = await pair(ops)
    before = ops.db.total_changes
    value = compare(ops, target, one, two)
    assert ops.db.total_changes == before
    changes = value["changes"]
    assert set(changes["domains"]) == set(pm_plans.pm_profiles.DOMAINS)
    assert not any(section["changed"] for section in changes["domains"].values())
    assert changes["domains"]["schedule"]["before"]["coverage"] == "unresolved"
    finance = changes["domains"]["finance"]["after"]["statements"][0]
    assert finance["input_id"] == assumption and finance["status"] == "agent_assumption"
    assert finance["confirmed_by"] is None and finance["source_ref"]
    requirements = changes["requirements"]
    assert [entry["id"] for entry in requirements["added"]] == ["pricing.review"]
    assert [entry["id"] for entry in requirements["removed"]] == ["pricing"]
    changed = requirements["changed"][0]
    assert changed["id"] == "login" and changed["fields"] == ["acceptance", "priority"]
    removed = changed["acceptance"]["removed"][0]
    assert removed["source_input_id"] == acceptance and removed["source_status"] == "agent_assumption"
    acceptance_source = next(source for source in changes["sources"]["unchanged"] if source["input_id"] == acceptance)
    assert acceptance_source["source_ref"] and acceptance_source["confirmed_by"] is None
    assert requirements["unchanged_ids"] == ["reset"]
    assert value["scope"]["structured_digest_changed"] and value["scope"]["semantic_impact"] == "unassessed"
    assert value["impact_candidates"] == [] and value["coverage"]["unselected_work"] == "not_scanned"
    assert not value["execution_authorized"]


async def test_same_revision_and_same_content_are_stable_without_claiming_scope_changes(ops):
    target, *_ = await planning(ops)
    one, two = await draft(ops, target), await draft(ops, target)
    same = compare(ops, target, one, one)
    assert not same["scope"]["recorded_content_changed"]
    assert not same["scope"]["document_identity_changed"]
    other = compare(ops, target, one, two)
    assert other["scope"]["document_identity_changed"]
    assert not other["scope"]["recorded_content_changed"]
    assert other["scope"]["semantic_impact"] == "no_recorded_change"
    cursor = same["context"]["journal_cursor"]
    await record(ops, target, value="A later unrelated proposal")
    assert compare(ops, target, one, one, journal_cursor=cursor) == same
    assert compare(ops, target, one, two)["comparison_digest"] == other["comparison_digest"]


async def test_markdown_and_title_change_preserve_exact_prose_with_unknown_scope_impact(ops):
    target, *_ = await planning(ops)
    old_text = "# Membership\n\nLogin required. 推薦折扣尚未採用。\n"
    new_text = "# Membership\n\nRemove all login requirements. 推薦折扣15% SHALL apply。\n"
    one = await draft(ops, target, title="Proposal", markdown=old_text)
    two = await draft(ops, target, title="Contradictory prose", markdown=new_text)
    value = compare(ops, target, one, two)
    markdown = value["changes"]["markdown"]
    span = markdown["replacement"]
    assert old_text[:span["prefix_chars"]] + span["added"] + old_text[len(old_text) - span["suffix_chars"]:] == new_text
    assert span["removed"] in old_text
    assert markdown["classification"] == "unclassified"
    assert not value["scope"]["structured_digest_changed"]
    assert value["scope"]["prose_scope_impact"] == "unknown"
    assert value["scope"]["semantic_impact"] == "unassessed"
    assert value["changes"]["requirements"]["added"] == []
    assert pm_plans.accepted_plan(ops, OWNER, target["workspace_id"])["plan"] is None


@pytest.mark.parametrize("before,after", [("", "abc"), ("abc", ""), ("abc", "abC"), ("甲🙂乙", "甲丙乙"),
                                          ("same", "same"), ("a\n" * 10000, "b\n" * 10000)])
def test_exact_text_span_reconstructs_unicode_and_large_repeated_lines(before, after):
    value = diff._text_change(before, after)
    if before == after:
        assert not value["changed"] and value["replacement"] is None
    else:
        span = value["replacement"]
        end = len(before) - span["suffix_chars"]
        assert before[span["prefix_chars"]:end] == span["removed"]
        assert before[:span["prefix_chars"]] + span["added"] + before[end:] == after


async def test_explicit_bindings_produce_direct_and_dependency_candidates_without_latest_work(ops, monkeypatch):
    target, one, two, _ = await pair(ops)
    work, binding_id = await work_binding(ops, target, one, ["reset"])
    direct_work, direct_binding = await work_binding(ops, target, one, ["login"])
    frozen_version = work_items._get_item(ops.db, work["work_item_id"])["version"]
    cursor = ops.journal.api_head()
    value = compare(ops, target, one, two, binding_ids=[binding_id, direct_binding], journal_cursor=cursor)
    by_id = {entry["binding"]["binding_id"]: entry for entry in value["impact_candidates"]}
    indirect = by_id[binding_id]
    assert indirect["dependent_requirement_ids"] == ["reset"] and indirect["direct_requirement_ids"] == []
    assert by_id[direct_binding]["direct_requirement_ids"] == ["login"]
    assert indirect["assessment"] == "review_candidate" and not indirect["work_update_authorized"]
    assert indirect["binding"]["source_work_version"] == frozen_version
    assert indirect["binding"]["title"] is None and indirect["binding"]["work_state"] == "unknown"
    await act(ops, "work_item.update", {"work_item_id": work["work_item_id"]},
              {"title": "Later work title", "state": "done"}, {"expected_version": frozen_version})
    adopt(adoption(ops, target, two["revision_id"], previous=one["revision_id"]))
    await bind_work(ops, work, plan_revision_id=two["revision_id"], requirement_ids=["pricing.review"])
    def forbidden(*args, **kwargs):
        raise AssertionError("Plan comparison read mutable work state")
    monkeypatch.setattr(work_items, "_get_item", forbidden)
    assert compare(ops, target, one, two, binding_ids=[direct_binding, binding_id], journal_cursor=cursor) == value
    assert ops.db.execute("SELECT state FROM work_items WHERE work_item_id=?", (direct_work["work_item_id"],)).fetchone()[0] != "done"


async def test_unbound_and_other_revision_requirements_never_match_by_id(ops):
    target, one, two, _ = await pair(ops)
    _, other_binding = await work_binding(ops, target, one, ["login"])
    three = await draft(ops, target, requirements=[{"id": "login", "source_input_id": pair_id(ops, target, two)}])
    value = compare(ops, target, two, three, binding_ids=[other_binding])
    candidate = value["impact_candidates"][0]
    assert candidate["baseline_relation"] == "other_revision"
    assert candidate["direct_requirement_ids"] == []
    assert candidate["dependent_requirement_ids"] == []
    assert candidate["reasons"] == ["association_coverage_insufficient"]


async def test_unassigned_binding_keeps_unknown_coverage_without_creating_work(ops):
    target, one, two, _ = await pair(ops)
    work, old = await work_binding(ops, target, one, ["login"])
    unassigned = (await bind_work(ops, work))["binding_id"]
    value = compare(ops, target, one, two, binding_ids=[unassigned])
    candidate = value["impact_candidates"][0]
    assert candidate["baseline_relation"] == "unassigned"
    assert candidate["binding"]["previous_binding_id"] == old
    assert candidate["binding"]["plan_revision_id"] is None
    assert candidate["reasons"] == ["association_coverage_insufficient"]
    assert ops.db.execute("SELECT COUNT(*) FROM work_items").fetchone()[0] == 1


async def test_same_revision_work_is_not_called_drift_and_binding_retention_is_explicit(ops):
    target, one, _, _ = await pair(ops)
    _, bid = await work_binding(ops, target, one, ["login"])
    value = compare(ops, target, one, one, binding_ids=[bid])
    candidate = value["impact_candidates"][0]
    assert candidate["assessment"] == "no_identified_change"
    assert candidate["reasons"] == [] and candidate["semantic_impact"] == "unassessed"
    ops.db.execute("DELETE FROM api_events WHERE kind='pm.work.bind' AND json_extract(body,'$.binding_id')=?", (bid,))
    with pytest.raises(OperationError, match="PM_DIFF_CURSOR_UNPROVEN"):
        compare(ops, target, one, one, binding_ids=[bid], journal_cursor=value["context"]["journal_cursor"])


def pair_id(ops, target, revision):
    return pm_plans.get_plan(ops, OWNER, target["workspace_id"], revision["revision_id"])["plan"]["document"]["selected_inputs"][0]["input_id"]


async def test_source_confirmation_is_explicit_and_preserves_superseded_assumption(ops):
    target, _, assumption, _ = await planning(ops)
    one = await draft(ops, target, requirements=[{"id": "pricing", "source_input_id": assumption}])
    confirmed = await discussion_action(ops, target, "pm.input.confirm", {"input_id": assumption, "rationale": "Now adopt this direction"})
    thread = pm_discussions.get_thread(ops, OWNER, **target)
    op, _ = ops.create(OWNER, action="pm.thread.plan.revise", target=target,
        params={"selected_input_ids": [confirmed["input_id"]], "rationale": "Select the newly confirmed source"},
        preconditions={"expected_version": thread["thread"]["version"], "expected_draft_id": thread["thread"]["draft_id"]},
        idempotency_key=uuid.uuid4().hex)
    await settle(ops, op)
    two = await draft(ops, target, requirements=[{"id": "pricing", "source_input_id": confirmed["input_id"]}])
    value = compare(ops, target, one, two)
    changed = value["changes"]["requirements"]["changed"][0]
    assert changed["before"]["source_status"] == "agent_assumption"
    assert changed["after"]["source_status"] == "user_confirmed"
    assert changed["after"]["confirmed_by"] == OWNER.actor
    added = value["changes"]["sources"]["added"][0]
    assert added["supersedes_input_id"] == assumption
    assert added["source_ref"] == changed["before"]["source_ref"]


async def test_cursor_rejects_future_sources_and_never_uses_wall_clock_order(ops):
    target, one, two, _ = await pair(ops)
    saved = compare(ops, target, one, two)
    cursor = saved["context"]["journal_cursor"]
    _, binding = await work_binding(ops, target, one, ["login"])
    with pytest.raises(OperationError, match="PM_DIFF_SOURCE_AFTER_CURSOR"):
        compare(ops, target, one, two, binding_ids=[binding], journal_cursor=cursor)
    with pytest.raises(OperationError, match="PM_DIFF_SOURCE_AFTER_CURSOR"):
        compare(ops, target, one, two, journal_cursor=saved["from_revision"]["creation_event_seq"])
    ops.db.execute("UPDATE api_events SET created_at=0 WHERE resource_type IN ('pm_plan','pm_evidence')")
    assert compare(ops, target, one, two, journal_cursor=cursor) == saved
    with pytest.raises(OperationError, match="PM_DIFF_SOURCE_AFTER_CURSOR"):
        compare(ops, target, one, two, binding_ids=[binding], journal_cursor=cursor)


async def test_missing_creation_event_reports_unproven_without_reconstructing_history(ops):
    target, one, two, _ = await pair(ops)
    cursor = ops.journal.api_head()
    ops.db.execute("DELETE FROM api_events WHERE resource_type='pm_plan' AND resource_id=?", (one["revision_id"],))
    with pytest.raises(OperationError, match="PM_DIFF_CURSOR_UNPROVEN"):
        compare(ops, target, one, two, journal_cursor=cursor)


@pytest.mark.parametrize("kwargs", [{"binding_ids": "pmwb_" + "0" * 32}, {"binding_ids": ["unknown"]},
    {"binding_ids": ["pmwb_" + "0" * 32] * 2}, {"binding_ids": ["pmwb_" + f"{i:032x}" for i in range(101)]},
    {"journal_cursor": True}, {"journal_cursor": -1}, {"journal_cursor": 1.5}, {"journal_cursor": 2**63}])
async def test_invalid_selection_is_rejected(ops, kwargs):
    target, one, two, _ = await pair(ops)
    with pytest.raises(OperationError, match="INVALID_PARAMS"):
        compare(ops, target, one, two, **kwargs)


async def test_authorization_and_cross_workspace_ids_do_not_leak(ops):
    target, one, two, _ = await pair(ops)
    for principal in (OUTSIDER, Principal(READER.actor, frozenset({"manage"})),):
        with pytest.raises(OperationError):
            diff.compare(ops, principal, target["workspace_id"], one["revision_id"], two["revision_id"])
    reader = Principal(READER.actor, frozenset({"observe"}))
    assert diff.compare(ops, reader, target["workspace_id"], one["revision_id"], two["revision_id"])["comparison"]
    other, other_one, other_two, _ = await pair(ops)
    with pytest.raises(OperationError, match="PM_PLAN_NOT_FOUND"):
        compare(ops, target, one, other_two)
    _, binding = await work_binding(ops, other, other_one, ["login"])
    with pytest.raises(OperationError, match="PM_WORK_BINDING_NOT_FOUND"):
        compare(ops, target, one, two, binding_ids=[binding])
    ops.db.execute("DELETE FROM pm_workspace_members WHERE workspace_id=? AND actor=?", (target["workspace_id"], READER.actor))
    with pytest.raises(OperationError):
        diff.compare(ops, reader, target["workspace_id"], one["revision_id"], two["revision_id"])


async def test_corrupt_digest_and_oversize_response_never_return_partial_diff(ops, monkeypatch):
    target, one, two, _ = await pair(ops)
    monkeypatch.setattr(diff, "MAX_RESPONSE_BYTES", 100)
    with pytest.raises(OperationError, match="PM_DIFF_TOO_LARGE"):
        compare(ops, target, one, two)
    monkeypatch.setattr(diff, "MAX_RESPONSE_BYTES", 2_000_000)
    ops.db.execute("DROP TRIGGER pm_plan_revisions_update_immutable")
    ops.db.execute("UPDATE pm_plan_revisions SET scope_digest=? WHERE revision_id=?", ("0" * 64, one["revision_id"]))
    with pytest.raises(OperationError, match="PM_PLAN_CORRUPT"):
        compare(ops, target, one, two)


async def test_read_only_comparison_leaves_entire_journal_unchanged(ops):
    target, one, two, _ = await pair(ops)
    _, binding = await work_binding(ops, target, one, ["login"])
    before = "\n".join(ops.db.iterdump())
    value = compare(ops, target, one, two, binding_ids=[binding])
    assert "\n".join(ops.db.iterdump()) == before
    encoded = json.dumps(value)
    assert "A mutable title must never become historical evidence" not in encoded
    assert value["coverage"]["cost"] == "unknown"
    assert pm_workspace.get_workspace(ops.db, OWNER, target["workspace_id"])
