"""Original proposal visibility survives ordinary native/Git/snapshot derivation."""
from __future__ import annotations

import uuid

import pytest

from bat_agent_connector import (
    pm_access,
    pm_brief_exports,
    pm_discussions,
    pm_native_operations,
    pm_plans,
    pm_runs,
    pm_snapshots,
    pm_source_lineage,
)
from bat_agent_connector.operations import OpContext, OperationError
from tests import test_pm_conversation_proposals as conversation
from tests import test_pm_native_operations as native
from tests import test_pm_plan_operations as plans
from tests.operation_helpers import settle_operations
from tests.test_checkpoints import snapshot as checkout_bytes

world = conversation.world
pm = conversation.pm
ready = conversation.ready
proposal_world = conversation.proposal_world
OWNER = conversation.OWNER


def withdrawal(w, monkeypatch):
    """Withdraw exactly one original source at its real centralized read gate.

    Keep workspace membership and all derived rows intact; replacing membership
    would not exercise preservation of the original source boundary.
    """
    ops = w["d"].ops
    source = ops.db.execute("SELECT operation_id FROM pm_messages WHERE message_id=?", (w["message"]["message_id"],)).fetchone()[0]
    visible = pm_access.visible
    state = {"available": True}
    monkeypatch.setattr(pm_access, "visible", lambda db, principal, op:
        (state["available"] or op["operation_id"] != source) and visible(db, principal, op))
    return state


async def adopt_proposal(w):
    pm_native_operations.install(w["d"].ops)
    proposal = await conversation.proposed(w)
    request = conversation.adoption_request(w, proposal)
    done = await conversation.complete(w, w["d"].ops.create(OWNER, **request)[0])
    assert done["status"] == "succeeded", done
    return proposal, done


async def snapshot(w):
    ops, wid = w["d"].ops, w["workspace_id"]
    pm_snapshots.install(ops)
    op = ops.create(OWNER, action="pm.snapshot.create", target={"workspace_id": wid}, params={},
        preconditions={"expected_plan_version": pm_plans.head(ops.db, wid)["version"]}, idempotency_key=uuid.uuid4().hex)[0]
    await settle_operations(ops)
    done = ops.get(op["operation_id"])
    assert done["status"] == "succeeded", done
    return done


def unavailable(call):
    with pytest.raises(OperationError) as error:
        call()
    assert error.value.status == 404


async def test_withdrawn_proposal_hides_ordinary_analysis_git_plan_snapshot_brief_and_metadata(proposal_world, monkeypatch):
    w, ops = proposal_world, proposal_world["d"].ops
    original_checkout = checkout_bytes(w["human"])
    access = withdrawal(w, monkeypatch)
    proposal, adoption = await adopt_proposal(w)
    revision_id = adoption["result"]["revision_id"]
    # A normal discussion turn must preserve proposal roots too, although its
    # action and output destination are no longer conversation-specific.
    ordinary = await native.finish(ops, native.request(ops, plans.target(w), who=OWNER, version=plans.version(w)))
    assert ordinary["status"] == "succeeded", ordinary
    run = pm_runs.get_by_operation(ops.db, ordinary["operation_id"])
    assert proposal["operation_id"] in pm_source_lineage.roots(ops.db, "operation", ordinary["operation_id"])
    message = ops.db.execute("SELECT * FROM pm_messages WHERE operation_id=?", (ordinary["operation_id"],)).fetchone()
    assert pm_runs.message_source_visible(ops.db, OWNER, message)
    snap = await snapshot(w)
    sid = snap["result"]["snapshot_id"]
    assert pm_brief_exports.render_brief(ops, OWNER, w["workspace_id"], sid)["content"]
    assert pm_plans.public_head(ops, OWNER, w["workspace_id"])["accepted_revision_id"] == revision_id
    assert pm_discussions.get_thread(ops, OWNER, **plans.target(w))["messages"][-1]["role"] == "assistant"
    sends = sum(instance.sends for instance in w["instances"])
    access["available"] = False
    for call in (
        lambda: pm_discussions.get_thread(ops, OWNER, **plans.target(w)),
        lambda: pm_discussions.context(ops, OWNER, **plans.target(w)),
        lambda: pm_plans.get_plan(ops, OWNER, w["workspace_id"], revision_id),
        lambda: pm_plans.public_head(ops, OWNER, w["workspace_id"]),
        lambda: pm_plans.accepted_plan(ops, OWNER, w["workspace_id"]),
        lambda: pm_runs.read(ops, OWNER, w["workspace_id"], run["run_id"]),
        lambda: pm_snapshots.get_snapshot(ops, OWNER, w["workspace_id"], sid),
        lambda: pm_snapshots.latest_snapshot(ops, OWNER, w["workspace_id"]),
        lambda: pm_brief_exports.render_brief(ops, OWNER, w["workspace_id"], sid),
    ):
        unavailable(call)
    assert not pm_runs.message_source_visible(ops.db, OWNER, message)
    threads = pm_discussions.list_threads(ops, OWNER, w["workspace_id"])
    assert threads["threads"] == [] and threads["visibility_filtered"]
    listing = pm_plans.list_plans(ops, OWNER, w["workspace_id"])
    assert revision_id not in {row["revision_id"] for row in listing["revisions"]}
    assert listing["head"] is None and listing["head_unavailable"] and listing["visibility_filtered"]
    listing = pm_snapshots.list_snapshots(ops, OWNER, w["workspace_id"])
    assert listing["snapshots"] == [] and listing["visibility_filtered"]
    assert run["run_id"] not in {row["run_id"] for row in pm_runs.list_runs(ops, OWNER, w["workspace_id"])["runs"]}
    private_ops = {ordinary["operation_id"], adoption["operation_id"], snap["operation_id"]}
    private_ops.update(row[0] for row in ops.db.execute("SELECT operation_id FROM pm_conversation_children WHERE parent_id=?", (adoption["operation_id"],)))
    for identity in private_ops:
        assert not pm_access.visible(ops.db, OWNER, ops.get(identity)), identity
    events = ops.journal.api_events(limit=500)["events"]
    private_events = [event for event in events if event["resource_id"] in private_ops | {sid, revision_id}]
    assert private_events and all(not pm_access.event_visible(ops.db, OWNER, event) for event in private_events)
    # Internal immutable recovery reads the exact completed receipt before live
    # sources; this must not reopen the host operation or repeat a model send.
    restored = pm_runs.create(ops, OWNER, operation_id=ordinary["operation_id"], workspace_id=w["workspace_id"],
        thread_id=run["thread_id"], expected_version=run["thread_version"], adapter=run["adapter"], purpose=run["purpose"],
        execution_authority=run["execution_authority"], max_chars=run["context"]["coverage"]["max_chars"])
    assert restored["run_id"] == run["run_id"]
    assert await pm_snapshots._run(OpContext(ops, snap)) == snap["result"]
    assert sum(instance.sends for instance in w["instances"]) == sends
    assert checkout_bytes(w["human"]) == original_checkout


async def test_readable_historical_plan_and_snapshot_survive_unrelated_hidden_current_head(proposal_world, monkeypatch):
    w, ops = proposal_world, proposal_world["d"].ops
    initial = await plans.adopt(w)
    assert initial["status"] == "succeeded", initial
    old_revision = initial["result"]["revision_id"]
    old_snapshot = await snapshot(w)
    access = withdrawal(w, monkeypatch)
    _, adopted = await adopt_proposal(w)
    current_snapshot = await snapshot(w)
    later_snapshot = await snapshot(w)
    access["available"] = False
    old = pm_plans.get_plan(ops, OWNER, w["workspace_id"], old_revision)
    assert old["head"] is None and old["head_unavailable"]
    assert old["plan"]["revision_id"] == old_revision and old["plan"]["is_current_accepted"] is None
    unavailable(lambda: pm_plans.get_plan(ops, OWNER, w["workspace_id"], adopted["result"]["revision_id"]))
    old_sid = old_snapshot["result"]["snapshot_id"]
    assert pm_snapshots.get_snapshot(ops, OWNER, w["workspace_id"], old_sid)["snapshot"]["snapshot_id"] == old_sid
    assert pm_brief_exports.render_brief(ops, OWNER, w["workspace_id"], old_sid)["content"]
    after = old_snapshot["result"]["sequence"]
    page = pm_snapshots.list_snapshots(ops, OWNER, w["workspace_id"], after=after, limit=1)
    assert page["snapshots"] == [] and page["visibility_filtered"]
    assert page["next_cursor"] == current_snapshot["result"]["sequence"]
    final = pm_snapshots.list_snapshots(ops, OWNER, w["workspace_id"], after=page["next_cursor"], limit=1)
    assert final["snapshots"] == [] and final["visibility_filtered"] and final["next_cursor"] is None
    assert later_snapshot["result"]["sequence"] > page["next_cursor"]
    unavailable(lambda: pm_snapshots.get_snapshot(ops, OWNER, w["workspace_id"], current_snapshot["result"]["snapshot_id"]))


async def test_ordinary_corrections_and_manual_plan_revision_keep_roots_and_typed_exclusions(proposal_world, monkeypatch):
    w, ops = proposal_world, proposal_world["d"].ops
    access = withdrawal(w, monkeypatch)
    proposal, _ = await adopt_proposal(w)
    page = pm_discussions.get_thread(ops, OWNER, **plans.target(w))
    excluded = next(row for row in page["inputs"] if row.get("review_disposition", {}).get("candidate_key") == "referral")
    assert excluded["review_disposition"]["included"] is False
    context = pm_discussions.context(ops, OWNER, **plans.target(w))
    assert any(row.get("review_disposition", {}).get("original_kind") == "excluded"
               for rows in context["inputs_by_status"].values() for row in rows)
    included = next(row for row in page["inputs"] if row.get("review_disposition", {}).get("candidate_key") == "login")
    corrected = await plans.act(w, "pm.input.record", {"slot": included["slot"], "value": "Account login needs the original reviewed criteria",
        "status": "user_confirmed", "source_ref": included["source_ref"], "supersedes_input_id": included["input_id"]})
    child = ops.db.execute("SELECT operation_id FROM pm_inputs WHERE input_id=?", (corrected["input_id"],)).fetchone()[0]
    assert proposal["operation_id"] in pm_source_lineage.roots(ops.db, "operation", child)
    manual = await plans.draft(w, markdown="# Follow-up\nRetains the original fixed inputs.\n")
    operation = pm_plans._revision(ops.db, w["workspace_id"], manual["revision_id"])["operation_id"]
    assert proposal["operation_id"] in pm_source_lineage.roots(ops.db, "operation", operation)
    pending = plans.adoption(w, manual)
    assert proposal["operation_id"] in pm_source_lineage.roots(ops.db, "operation", pending["operation_id"])
    access["available"] = False
    await settle_operations(ops)
    done = ops.get(pending["operation_id"])
    assert done["status"] == "failed", done
    assert ops.db.execute("SELECT COUNT(*) FROM operation_steps WHERE operation_id=? AND name='pm.revision.commit'", (done["operation_id"],)).fetchone()[0] == 0
    unavailable(lambda: pm_plans.get_plan(ops, OWNER, w["workspace_id"], manual["revision_id"]))


async def test_original_source_withdrawal_at_final_native_frame_prevents_send(proposal_world, monkeypatch):
    w, ops = proposal_world, proposal_world["d"].ops
    access = withdrawal(w, monkeypatch)
    await adopt_proposal(w)
    w["settings"]["before_intent"] = lambda runtime: access.update(available=False)
    op = await native.finish(ops, native.request(ops, plans.target(w), who=OWNER, version=plans.version(w)))
    assert op["status"] == "failed", op
    assert w["instances"][-1].sends == 0
    assert not ops.db.execute("SELECT 1 FROM pm_messages WHERE operation_id=?", (op["operation_id"],)).fetchone()
