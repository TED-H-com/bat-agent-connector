"""Historical feedback stays exact and records supersession only at freeze time."""

import copy
import json

import pytest

from bat_agent_connector import pm_access, pm_sprint_review, pm_workspace
from tests import test_pm_discussions as discussions
from tests import test_pm_sprint_review as review

ops, sprint_ops = review.ops, review.sprint_ops


@pytest.mark.parametrize("supersede_before", [False, True], ids=["after-completion", "before-selection"])
async def test_supersession_is_frozen_without_disclosing_the_successor(
    ops, monkeypatch, tmp_path, supersede_before
):
    _, _, _, target, basis = await review.prepared(ops)
    wid = target["workspace_id"]
    created = await discussions.settle(
        ops,
        ops.create(
            review.OWNER,
            action="pm.thread.create",
            target={"workspace_id": wid},
            params={"title": "Exact feedback history"},
            preconditions={
                "expected_version": pm_workspace.get_workspace(ops.db, review.OWNER, wid)["version"]
            },
            idempotency_key="feedback-idea",
        )[0],
    )
    thread = {"workspace_id": wid, "thread_id": created["result"]["thread_id"]}
    old = await discussions.record(ops, thread, value="  歷史意見\n保留  ", status="conflicting")
    original = dict(ops.db.execute("SELECT * FROM pm_inputs WHERE input_id=?", (old["input_id"],)).fetchone())

    async def supersede():
        new = await discussions.record(
            ops, thread, value="PRIVATE SUCCESSOR", status="unresolved", supersedes_input_id=old["input_id"]
        )
        row = dict(ops.db.execute("SELECT * FROM pm_inputs WHERE input_id=?", (new["input_id"],)).fetchone())
        visible = pm_access.operation_source_visible
        monkeypatch.setattr(
            pm_access,
            "operation_source_visible",
            lambda db, principal, identity: (
                False if identity == row["operation_id"] else visible(db, principal, identity)
            ),
        )
        return row

    successor = await supersede() if supersede_before else None
    basis["feedback_items"] = [
        {
            "id": "historical",
            "source_ref": {
                "kind": "discussion_input",
                "thread_id": thread["thread_id"],
                "input_id": old["input_id"],
                "operation_id": original["operation_id"],
            },
        }
    ]
    await review.save(ops, target, basis)
    fixed = review.m.read(ops, target)["draft"]
    fact = fixed["document"]["guided_review"]["feedback"][0]
    assert fact["content"] == "  歷史意見\n保留  "
    assert fact["original"]["status"] == "conflicting" and fact["original"]["input_id"] == old["input_id"]
    assert fact["superseded_at_selection"] is supersede_before
    await review.m.apply(
        ops,
        target,
        "complete",
        draft_revision_id=fixed["revision_id"],
        discussion_summary="Discuss original feedback",
        decision_ids=[],
        governance_decisions=[],
    )
    if successor is None:
        successor = await supersede()
    before = ops.db.total_changes
    assert review.m.read(ops, target)["draft"] == copy.deepcopy(fixed)
    assert ops.db.total_changes == before
    for private in (successor["input_id"], successor["operation_id"], successor["value"]):
        assert private not in json.dumps(fact, ensure_ascii=False)
    plan = [
        tuple(row)
        for row in ops.db.execute("EXPLAIN QUERY PLAN " + pm_sprint_review.SUCCESSOR_SQL, (old["input_id"],))
    ]
    assert any("COVERING INDEX" in str(row) and "supersedes_input_id=?" in str(row) for row in plan)
    (tmp_path / "successor-plan.json").write_text(json.dumps(plan))
    print("SUCCESSOR_QUERY_PLAN", json.dumps(plan))
