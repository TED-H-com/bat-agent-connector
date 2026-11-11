"""Exact immutable Result reads use real collectors with mock BAT only."""

import pytest

from bat_agent_connector import pm_attempt_result_reads as reads
from bat_agent_connector import pm_attempt_results
from bat_agent_connector.operations import OperationError
from tests import test_pm_work_attempt_result as r
from tests.test_pm_work_start import STARTER_OWNER

world = r.world
pm = r.pm
ready = r.ready
start_ready = r.start_ready


async def test_fixed_receipt_is_not_replaced_by_latest_and_reads_do_not_write(start_ready, mock):
    w = start_ready
    att = await r._start_managed_attempt(w, mock)
    r._set_turn_messages(mock, att["session_id"], att["turn_marker"])
    await w["d"].inventory._refresh_scheduled("h1")
    ops = w["d"].ops
    first = pm_attempt_results.receipts(ops.db, w["workspace_id"], att["attempt_id"])["receipts"][0]
    r._set_turn_messages(
        mock, att["session_id"], att["turn_marker"], assistant_id="later", assistant_text="Later claim"
    )
    await w["d"].inventory._refresh_scheduled("h1")
    tables = [row[0] for row in ops.db.execute("SELECT name FROM sqlite_master WHERE type='table'")]

    def counts():
        return [ops.db.execute("SELECT count(*) FROM " + t).fetchone()[0] for t in tables]

    before = ops.db.total_changes, counts(), list(mock.invokes)
    args = ops, STARTER_OWNER, w["workspace_id"], att["attempt_id"], first["result_id"], first["digest"]
    original = reads.read_fixed_receipt(*args)
    assert original["receipt"] == first
    assert original["work_item_id"] == att["work_item_id"]
    with pytest.raises(OperationError, match="PM_SPRINT_REVIEW_REFERENCE_MISMATCH"):
        reads.read_fixed_receipt(*args[:-1], "incorrect")
    assert (ops.db.total_changes, counts(), list(mock.invokes)) == before


async def test_collected_reported_done_remains_unaccepted_in_guided_review(start_ready, mock, monkeypatch):
    from bat_agent_connector import pm_meetings, pm_sprints
    from tests import test_pm_meetings as m
    from tests import test_pm_quality_projections as q
    from tests.test_pm_sprint_review import basis
    from tests.test_pm_work_packages import action

    w, ops = start_ready, start_ready["d"].ops
    att = await r._start_managed_attempt(w, mock)
    r._set_turn_messages(mock, att["session_id"], att["turn_marker"])
    await w["d"].inventory._refresh_scheduled("h1")
    receipt = pm_attempt_results.receipts(ops.db, w["workspace_id"], att["attempt_id"])["receipts"][0]
    pm_sprints.install(ops)
    pm_meetings.install(ops)
    binding = ops.db.execute(
        "SELECT plan_revision_id FROM pm_work_bindings WHERE work_item_id=?", (att["work_item_id"],)
    ).fetchone()
    w["dod"] = (
        await action(
            w,
            "pm.dod.revise",
            {"workspace_id": w["workspace_id"]},
            {
                "plan_revision_id": binding[0],
                "criteria": [{"id": "checks", "text": "Verify the selected change"}],
                "rationale": "Fixed commitment",
            },
            {"expected_revision_id": None},
        )
    )["revision_id"]
    sprint = await q.sprint(w, monkeypatch)
    snap = await q.snapshot(w)
    created = await q.base.execute(
        w,
        q.envelope(
            "pm.meeting.create",
            sprint,
            {
                "kind": "review",
                "title": "Original outcomes",
                "snapshot_id": snap["snapshot_id"],
                "rationale": "Discuss originals",
            },
            {"expected_sprint_version": 3},
        ),
    )
    target = {"workspace_id": w["workspace_id"], "meeting_id": created["result"]["meeting_id"]}
    await m.apply(ops, target, "note", who=r.PLAN_OWNER, content="Original stakeholder feedback")
    selected = basis()
    selected["results"] = [
        {k: receipt[k] for k in ("attempt_id", "result_id", "digest")} | {"assessment_ref": None}
    ]
    params = m.draft_params("review", decisions=[], review_basis=selected)
    params["output"]["feedback"] = []
    await m.apply(ops, target, "draft", who=r.PLAN_OWNER, **params)
    doc = m.read(ops, target, r.PLAN_OWNER)["draft"]["document"]["guided_review"]
    fact = doc["results"][0]
    assert fact["reported"] == "claimed_done"
    assert fact["acceptance"] == "unassessed" and not fact["qualified"]
    assert "acceptance_missing" in fact["gaps"]
    assert doc["requirements"][0]["acceptance"] == "unassessed"


async def test_exact_accepted_quality_and_missing_dod_are_distinct(start_ready, mock, monkeypatch):
    from bat_agent_connector import pm_meetings, pm_sprints
    from tests import test_pm_meetings as m
    from tests import test_pm_quality_projections as q
    from tests.test_checkpoints import git
    from tests.test_pm_sprint_review import basis
    from tests.test_pm_work_packages import action, member

    w, ops = start_ready, start_ready["d"].ops
    att = await r._start_managed_attempt(w, mock)
    r._set_turn_messages(mock, att["session_id"], att["turn_marker"])
    await w["d"].inventory._refresh_scheduled("h1")
    receipt = pm_attempt_results.receipts(ops.db, w["workspace_id"], att["attempt_id"])["receipts"][0]
    w["package"] = reads.read_fixed_receipt(
        ops, r.STARTER_OWNER, w["workspace_id"], att["attempt_id"], receipt["result_id"], receipt["digest"]
    )["package"]
    w["child"] = ops.get(att["handoff_operation_id"])
    w["candidate"] = git(att["worktree_path"], "rev-parse", "HEAD")
    w["tree"] = git(att["worktree_path"], "rev-parse", "HEAD^{tree}")
    w["proof"] = q.base.observed(w, w["child"], candidate=w["candidate"], tree=w["tree"])
    pm_sprints.install(ops)
    pm_meetings.install(ops)
    await member(w, q.base.REVIEWER.actor, "contributor")
    w["dod"] = (
        await action(
            w,
            "pm.dod.revise",
            {"workspace_id": w["workspace_id"]},
            {
                "plan_revision_id": w["package"]["document"]["context"]["plan_ref"]["revision_id"],
                "criteria": [{"id": "regression", "text": "Fixed regression passes"}],
                "rationale": "Keep original quality commitment",
            },
            {"expected_revision_id": None},
        )
    )["revision_id"]
    sprint = await q.sprint(w, monkeypatch)
    snap = await q.snapshot(w)
    created = await q.base.execute(
        w,
        q.envelope(
            "pm.meeting.create",
            sprint,
            {
                "kind": "review",
                "title": "Original evidence",
                "snapshot_id": snap["snapshot_id"],
                "rationale": "Compare proof",
            },
            {"expected_sprint_version": 3},
        ),
    )
    target = {"workspace_id": w["workspace_id"], "meeting_id": created["result"]["meeting_id"]}
    note = await m.apply(ops, target, "note", who=r.PLAN_OWNER, content="Exact stakeholder feedback")
    ref = await q.qualified(w)
    selected = basis()
    selected["feedback_items"] = [
        {"id": "voice", "source_ref": {"kind": "meeting_note", "note_id": note["note_id"]}}
    ]
    selected["results"] = [
        {k: receipt[k] for k in ("attempt_id", "result_id", "digest")} | {"assessment_ref": ref}
    ]
    params = m.draft_params("review", decisions=[], review_basis=selected)
    params["output"]["feedback"] = []
    await m.apply(ops, target, "draft", who=r.PLAN_OWNER, **params)
    original = m.read(ops, target, r.PLAN_OWNER)["draft"]
    fact = original["document"]["guided_review"]["results"][0]
    assert fact["reported"] == "claimed_done" and fact["acceptance"] == "accepted" and fact["qualified"]
    assert original["document"]["guided_review"]["requirements"][0]["qualified"]
    await q.revoke(w, ref)
    assert m.read(ops, target, r.PLAN_OWNER)["draft"] == original
    await m.apply(
        ops,
        target,
        "complete",
        who=r.PLAN_OWNER,
        draft_revision_id=original["revision_id"],
        discussion_summary="Original evidence remains historical after newer assessment",
        decision_ids=[],
        governance_decisions=[],
    )
    assert m.read(ops, target, r.PLAN_OWNER)["draft"] == original
    created = await q.base.execute(
        w,
        q.envelope(
            "pm.meeting.create",
            sprint,
            {
                "kind": "review",
                "title": "Next evidence discussion",
                "snapshot_id": snap["snapshot_id"],
                "rationale": "Explicit new context",
            },
            {"expected_sprint_version": 3},
        ),
    )
    target = {"workspace_id": w["workspace_id"], "meeting_id": created["result"]["meeting_id"]}
    note = await m.apply(ops, target, "note", who=r.PLAN_OWNER, content="New fixed feedback")
    selected["feedback_items"][0]["source_ref"]["note_id"] = note["note_id"]
    review = await q.base.new_review(w, dod=False)
    op = await q.base.execute(w, q.base.assessment(w, review), actor=q.base.REVIEWER)
    selected["results"][0]["assessment_ref"] = {
        k: op["result"][k] for k in ("review_id", "version", "document_digest")
    } | {"operation_id": op["operation_id"], "candidate_commit": w["candidate"]}
    await m.apply(ops, target, "draft", who=r.PLAN_OWNER, **params)
    fact = m.read(ops, target, r.PLAN_OWNER)["draft"]["document"]["guided_review"]["results"][0]
    assert not fact["qualified"] and "dod_missing" in fact["gaps"]


async def test_receipt_from_other_session_cannot_impersonate_original_attempt(start_ready, mock):
    w, ops = start_ready, start_ready["d"].ops
    att = await r._start_managed_attempt(w, mock)
    r._set_turn_messages(mock, att["session_id"], att["turn_marker"])
    await w["d"].inventory._refresh_scheduled("h1")
    first = pm_attempt_results.receipts(ops.db, w["workspace_id"], att["attempt_id"])["receipts"][0]
    foreign, _ = pm_attempt_results.record_receipt(
        ops,
        workspace_id=w["workspace_id"],
        attempt_id=att["attempt_id"],
        source_key="bat:h2:foreign-session",
        source_generation=first["source_generation"],
        source_position=first["source_position"],
        digest=first["digest"],
        document=first["document"],
        observed_at=first["observed_at"],
        operation_id=first["operation_id"],
        result_id=pm_attempt_results.new_result_id(),
    )
    with pytest.raises(OperationError, match="PM_SPRINT_REVIEW_REFERENCE_MISMATCH"):
        reads.read_fixed_receipt(
            ops, STARTER_OWNER, w["workspace_id"], att["attempt_id"], foreign["result_id"], foreign["digest"]
        )
