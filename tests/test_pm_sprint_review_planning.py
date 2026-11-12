"""Explicit Planning, source inheritance and same-key durable recovery."""

import uuid

import pytest

from bat_agent_connector import (
    pm_access,
    pm_discussions,
    pm_governance,
    pm_meetings,
    pm_plans,
    pm_snapshots,
    pm_workspace,
)
from bat_agent_connector.operations import OperationError, OperationService
from bat_agent_connector.task_journal import Journal
from tests import test_pm_sprint_review_decisions as d
from tests.test_pm_discussions import OWNER, READER, member, settle

completed, m, ops, sprint_ops = d.completed, d.m, d.ops, d.sprint_ops


async def planning(ops):
    target, completion, _ = await completed(ops)
    wid = target["workspace_id"]
    doc = m.read(ops, target)["completion"]["document"]
    source = {
        "kind": "sprint_review_decision",
        "meeting_id": target["meeting_id"],
        "completion_id": completion["result"]["completion_id"],
        "completion_digest": pm_snapshots._digest(doc),
        "decision_id": "retain_direction",
    }
    req = dict(
        action="pm.thread.create",
        target={"workspace_id": wid},
        params={"title": "Explicit next Planning"},
        preconditions={"expected_version": pm_workspace.get_workspace(ops.db, OWNER, wid)["version"]},
        idempotency_key=uuid.uuid4().hex,
    )
    idea = await settle(ops, ops.create(OWNER, **req)[0])
    target = {"workspace_id": wid, "thread_id": idea["result"]["thread_id"]}
    req = dict(
        action="pm.input.record",
        target=target,
        params={
            "slot": "scope",
            "value": "Investigate the open gaps",
            "status": "unresolved",
            "source_ref": source,
        },
        preconditions={"expected_version": 1},
        idempotency_key=uuid.uuid4().hex,
    )
    recorded = await settle(ops, ops.create(OWNER, **req)[0])
    plan_req = dict(
        action="pm.thread.plan",
        target=target,
        params={"selected_input_ids": [recorded["result"]["input_id"]], "rationale": "Explicit draft only"},
        preconditions={"expected_version": 2},
        idempotency_key=uuid.uuid4().hex,
    )
    before = pm_plans.head(ops.db, wid), ops.db.execute("SELECT count(*) FROM work_items").fetchone()[0]
    planned = await settle(ops, ops.create(OWNER, **plan_req)[0])
    assert (
        pm_plans.head(ops.db, wid),
        ops.db.execute("SELECT count(*) FROM work_items").fetchone()[0],
    ) == before
    return target, recorded, planned, req, plan_req


async def test_explicit_draft_is_unresolved_sourced_and_read_only(ops):
    target, recorded, planned, _, _ = await planning(ops)
    assert planned["result"]["draft_id"].startswith("pmd_")
    tables = [r[0] for r in ops.db.execute("SELECT name FROM sqlite_master WHERE type='table'")]

    def counts():
        return [ops.db.execute("SELECT count(*) FROM " + t).fetchone()[0] for t in tables]

    before = ops.db.total_changes, counts()
    view = pm_discussions.get_thread(ops, OWNER, **target)
    value = view["inputs"][0]
    assert value["status"] == "unresolved"
    assert value["source_decision"]["decision"]["recorded_by"] == OWNER.actor
    assert not value["source_decision"]["scope_applied"]
    pm_discussions.list_threads(ops, OWNER, target["workspace_id"])
    pm_discussions.context(ops, OWNER, **target, max_chars=64000)
    assert (ops.db.total_changes, counts()) == before
    assert pm_access.visible(ops.db, OWNER, recorded)


async def test_original_input_and_plan_keys_replay_after_restart_without_effects(ops, tmp_path):
    target, recorded, planned, req, plan_req = await planning(ops)
    reopened = Journal(tmp_path / "sprints.db")
    try:
        recovered = OperationService(reopened)
        pm_discussions.install(recovered)
        pm_meetings.install(recovered)
        pm_governance.install(recovered)
        before = [tuple(r) for r in reopened.db.execute("SELECT * FROM pm_planning_drafts")]
        for envelope, original in ((req, recorded), (plan_req, planned)):
            result, fresh = recovered.create(OWNER, **envelope)
            assert (
                not fresh
                and result["operation_id"] == original["operation_id"]
                and result["result"] == original["result"]
            )
        assert [tuple(r) for r in reopened.db.execute("SELECT * FROM pm_planning_drafts")] == before
    finally:
        reopened.close()


async def test_membership_withdrawal_hides_derived_draft_and_feed(ops):
    target, recorded, planned, _, _ = await planning(ops)
    assert pm_discussions.get_thread(ops, READER, **target)
    await member(ops, {"workspace_id": target["workspace_id"]}, READER.actor, None)
    with pytest.raises(OperationError):
        pm_discussions.get_thread(ops, READER, **target)
    for op in (recorded, planned):
        assert not pm_access.visible(ops.db, READER, op)
        with pytest.raises(OperationError):
            ops.get(op["operation_id"], principal=READER)


@pytest.mark.parametrize("change", ["digest", "decision", "confirmed", "supersedes", "foreign"])
async def test_planning_requires_original_completed_decision(ops, change):
    target, _, _, request, _ = await planning(ops)
    from copy import deepcopy

    altered = deepcopy(request)
    # A fresh Idea is needed because the original has explicitly advanced to Planning.
    wid = target["workspace_id"]
    idea = await settle(
        ops,
        ops.create(
            OWNER,
            action="pm.thread.create",
            target={"workspace_id": wid},
            params={"title": "Another explicit Idea"},
            preconditions={"expected_version": pm_workspace.get_workspace(ops.db, OWNER, wid)["version"]},
            idempotency_key=uuid.uuid4().hex,
        )[0],
    )
    altered["target"]["thread_id"] = idea["result"]["thread_id"]
    altered["idempotency_key"] = uuid.uuid4().hex
    params = altered["params"]
    if change == "digest":
        params["source_ref"]["completion_digest"] = "0" * 64
    elif change == "decision":
        params["source_ref"]["decision_id"] = "unrecorded"
    elif change == "confirmed":
        params["status"] = "user_confirmed"
    elif change == "supersedes":
        params["supersedes_input_id"] = request["params"]["source_ref"]["completion_id"]
    else:
        params["source_ref"]["meeting_id"] = "pmmeet_" + "f" * 32
    with pytest.raises(OperationError):
        ops.create(OWNER, **altered)
