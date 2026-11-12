"""Real-action rebinding regression adapted from the independent Review probe."""

import datetime as dt
from types import SimpleNamespace

import pytest

from bat_agent_connector import pm_attempt_result_reads, pm_evidence, pm_sprints, pm_workspace, work_items
from tests import test_pm_quality_projections as q
from tests import test_pm_sprint_review_withdrawal as review
from tests import test_pm_work_packages as packages

world, pm, ready, start_ready = review.world, review.pm, review.ready, review.start_ready


@pytest.mark.parametrize("assessed", [False, True], ids=["unassessed", "assessed"])
@pytest.mark.parametrize("rebind", [False, True], ids=["matching", "rebound"])
async def test_original_result_is_not_relabelled_after_work_rebinding(
    start_ready, mock, monkeypatch, assessed, rebind
):
    w, ops = start_ready, start_ready["d"].ops
    old_target, selected, _ = await review.prepared(
        w, mock, monkeypatch, "assessment" if assessed else "result"
    )
    original = review.m.read(ops, old_target, review.OWNER)["draft"]["document"]["guided_review"]["results"][
        0
    ]
    ref = selected["results"][0]
    package = pm_attempt_result_reads.read_fixed_receipt(
        ops, review.OWNER, w["workspace_id"], ref["attempt_id"], ref["result_id"], ref["digest"]
    )["package"]
    context = package["document"]["context"]
    requirements = [entry["id"] for entry in package["document"]["requirements"]]
    old_meeting = review.m.read(ops, old_target, review.OWNER)["meeting"]
    sprint = {"workspace_id": w["workspace_id"], "sprint_id": old_meeting["sprint_id"]}
    if rebind:
        await packages.action(
            w,
            "pm.sprint.cancel",
            sprint,
            {"rationale": "End the old commitment before selecting new scope"},
            {"expected_version": 3},
        )
        revision = await packages.draft(
            w,
            requirements=[dict(row, id="different.requirement") for row in w["requirements"]],
            markdown="# Explicitly revised scope\n",
        )
        assert (await packages.adopt(w, revision))["status"] == "succeeded"
        old = pm_evidence._binding(ops.db, **w["target"])
        await packages.action(
            w,
            "pm.work.bind",
            w["target"],
            dict(
                rationale="Associate new scope explicitly",
                plan_revision_id=revision["revision_id"],
                requirement_ids=["different.requirement"],
            ),
            dict(
                expected_binding_id=old["binding_id"],
                expected_work_version=work_items._get_item(ops.db, w["target"]["work_item_id"])["version"],
            ),
        )
        w["dod"] = (
            await packages.action(
                w,
                "pm.dod.revise",
                dict(workspace_id=w["workspace_id"]),
                dict(
                    plan_revision_id=revision["revision_id"],
                    criteria=[dict(id="checks", text="New explicit criterion")],
                    rationale="Fixed new commitment",
                ),
                dict(expected_revision_id=w["dod"]),
            )
        )["revision_id"]
    binding = pm_evidence._binding(ops.db, **w["target"])
    if rebind:
        sprint = await next_sprint(w, monkeypatch)
    snap = await q.snapshot(w)
    target = await review.new_meeting(w, sprint, snap)
    note = await review.m.apply(
        ops, target, "note", who=review.OWNER, content="Inspect original result provenance"
    )
    selected["feedback_items"] = [
        dict(id="voice", source_ref=dict(kind="meeting_note", note_id=note["note_id"]))
    ]
    await review.draft(w, target, selected)
    frozen = review.m.read(ops, target, review.OWNER)["draft"]["document"]["guided_review"]
    fact = frozen["results"][0]
    # Unlike the original review probe, refusing readable historical evidence is
    # not an accepted alternative: the lead explicitly chose to retain it.
    assert fact["binding_id"] == context["work_ref"]["binding_id"]
    assert fact["plan_ref"] == {
        key: context["plan_ref"][key] for key in ("revision_id", "document_digest", "scope_revision")
    }
    assert fact["requirements"] == requirements
    assert fact["assessment"] == original["assessment"]
    if rebind:
        assert "original_binding_differs" in fact["gaps"]
        assert frozen["coverage"]["selected_bindings"] == 0
        row = next(row for row in frozen["requirements"] if row["requirement_id"] == "different.requirement")
        assert row["acceptance"] == "unassessed" and not row["qualified"]
        assert all(
            item["acceptance"] == "unassessed" and not item["qualified"] for item in row["contributions"]
        )
        assert any(
            gap["reason"] == "result_not_selected" and gap["source_id"] == binding["binding_id"]
            for gap in frozen["gaps"]
        )
        mismatch = next(gap for gap in frozen["gaps"] if gap["reason"] == "original_binding_differs")
        assert mismatch["source_id"] == ref["result_id"] and mismatch["issue_id"].startswith("pmsrg_")
    else:
        assert fact == original
        assert frozen["coverage"]["selected_bindings"] == 1
        assert not any(
            gap["reason"] in {"original_binding_differs", "result_not_selected"} for gap in frozen["gaps"]
        )


async def next_sprint(w, monkeypatch, *, dod=None):
    ops, wid = w["d"].ops, w["workspace_id"]
    binding = pm_evidence._binding(ops.db, wid, w["target"]["work_item_id"])
    goal = (
        await q.base.execute(
            w,
            q.envelope(
                "pm.product_goal.revise",
                {"workspace_id": wid},
                {
                    "plan_revision_id": binding["plan_revision_id"],
                    "text": "Verify original login result",
                    "rationale": "Fixed goal",
                },
                {"expected_revision_id": pm_sprints._head(ops.db, wid, "product_goal")},
            ),
        )
    )["result"]["revision_id"]
    backlog = (
        await q.base.execute(
            w,
            q.envelope(
                "pm.backlog.revise",
                {"workspace_id": wid},
                {
                    "product_goal_revision_id": goal,
                    "binding_ids": [binding["binding_id"]],
                    "rationale": "Selected original work",
                },
                {"expected_revision_id": pm_sprints._head(ops.db, wid, "backlog")},
            ),
        )
    )["result"]["revision_id"]
    start = dt.datetime(2026, 10, 11, tzinfo=dt.timezone.utc)
    end = start + dt.timedelta(days=7)
    monkeypatch.setattr(pm_sprints, "time", SimpleNamespace(time=lambda: start.timestamp() + 60))
    op = await q.base.execute(
        w,
        q.envelope(
            "pm.sprint.create",
            {"workspace_id": wid},
            {
                "title": "Original login",
                "mode": "personal_iteration",
                "owner_actor": review.OWNER.actor,
                "developers": [review.OWNER.actor],
                "backlog_revision_id": backlog,
                "dod_revision_id": dod or w["dod"],
                "goal": "Login meets original acceptance",
                "starts_at": start.isoformat(),
                "ends_at": end.isoformat(),
                "capacity_note": "One result",
                "rationale": "Fixed iteration",
            },
            {"expected_workspace_version": pm_workspace.get_workspace(ops.db, review.OWNER, wid)["version"]},
        ),
    )
    target = {"workspace_id": wid, "sprint_id": op["result"]["sprint_id"]}
    await q.base.execute(
        w,
        q.envelope(
            "pm.sprint.select",
            target,
            {
                "items": [
                    {
                        "binding_id": binding["binding_id"],
                        "assignee": review.OWNER.actor,
                        "how": "Verify the fixed result",
                    }
                ],
                "rationale": "Original selection",
            },
            {"expected_version": 1},
        ),
    )
    await q.base.execute(
        w,
        q.envelope("pm.sprint.start", target, {"rationale": "Begin fixed timebox"}, {"expected_version": 2}),
    )
    monkeypatch.setattr(pm_sprints, "time", SimpleNamespace(time=lambda: end.timestamp()))
    return target
