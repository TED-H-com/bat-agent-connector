"""Withdraw original workspace source access through member.set, never SQL tampering."""

import copy

import pytest

from bat_agent_connector import (
    pm_access,
    pm_discussions,
    pm_follow_up,
    pm_governance,
    pm_meetings,
    pm_plans,
    pm_snapshots,
    pm_sprints,
    pm_workspace,
)
from bat_agent_connector.operations import OpContext, OperationError
from tests import test_pm_meetings as m
from tests import test_pm_quality_projections as q
from tests import test_pm_work_attempt_result as r
from tests.test_pm_sprint_review import basis
from tests.test_pm_work_packages import action, member

world, pm, ready, start_ready = r.world, r.pm, r.ready, r.start_ready
OWNER = r.PLAN_OWNER


async def withdraw(ops, wid):
    from bat_agent_connector.api_auth import Principal

    other = Principal("source-owner", frozenset({"observe", "manage"}))
    for actor, role, who in ((other.actor, "owner", OWNER), (OWNER.actor, None, other)):
        req = q.envelope(
            "pm.workspace.member.set",
            {"workspace_id": wid},
            {"actor": actor, "role": role},
            {"expected_version": pm_workspace.get_workspace(ops.db, who, wid)["version"]},
        )
        op = ops.create(who, **req)[0]
        await ops._execute(op["operation_id"])
        assert ops.get(op["operation_id"])["status"] == "succeeded"


async def new_meeting(w, sprint, snap):
    result = await action(
        w,
        "pm.meeting.create",
        sprint,
        dict(
            kind="review",
            title="Original outcomes",
            snapshot_id=snap["snapshot_id"],
            rationale="Fixed discussion",
        ),
        {"expected_sprint_version": 3},
    )
    return {"workspace_id": w["workspace_id"], "meeting_id": result["meeting_id"]}


async def draft(w, target, selected):
    decision = m.draft_params("review")["decisions"][0]
    decision.update(owner_actor=OWNER.actor, follow_up_ids=["check_quality"])
    params = m.draft_params(
        "review", decisions=[decision], follow_ups=[m.follow_up(OWNER.actor)], review_basis=selected
    )
    params["output"]["feedback"] = []
    req = m.request(w["d"].ops, target, "draft", **params)
    result = await q.base.execute(w, req)
    return result, req


async def decide(w, target):
    ops = w["d"].ops
    view = m.read(ops, target, OWNER)
    fixed = view["draft"]
    proposed = fixed["document"]["decisions"][0]
    ref = dict(
        meeting_id=target["meeting_id"],
        draft_revision_id=fixed["revision_id"],
        draft_digest=fixed["document_digest"],
        decision_id=proposed["id"],
    )
    reqid = view["snapshot"]["baseline"]["document"]["requirements"][0]["id"]
    record = await action(
        w,
        "pm.governance.record",
        {"workspace_id": w["workspace_id"]},
        dict(
            kind="change",
            root_cause_key=fixed["revision_id"],
            snapshot_id=view["snapshot"]["snapshot_id"],
            title="Retain direction",
            description=proposed["text"],
            owner_actor=OWNER.actor,
            source_refs=[{"kind": "requirement", "id": reqid}],
            rationale="Retain incomplete evidence",
            review_proposal_ref=ref,
            **(
                {
                    "disambiguation": {
                        "record_ids": [
                            row[0] for row in ops.db.execute("SELECT record_id FROM pm_governance_records")
                        ],
                        "rationale": "Separate fixed Review",
                    }
                }
                if ops.db.execute("SELECT 1 FROM pm_governance_records").fetchone()
                else {}
            ),
        ),
        {"expected_plan_version": pm_plans.head(ops.db, w["workspace_id"])["version"]},
    )
    record = await action(
        w,
        "pm.governance.decide",
        {"workspace_id": w["workspace_id"], "record_id": record["record_id"]},
        {"disposition": "adopted_direction", "rationale": "Explicit direction"},
        {"expected_version": record["version"]},
    )
    rev = pm_governance._revision(ops.db, record["revision_id"])
    return dict(
        decision_id=proposed["id"], revision_id=rev["revision_id"], document_digest=rev["document_digest"]
    )


async def complete(w, target):
    proof = await decide(w, target)
    fixed = m.read(w["d"].ops, target, OWNER)["draft"]
    req = m.request(
        w["d"].ops,
        target,
        "complete",
        draft_revision_id=fixed["revision_id"],
        discussion_summary="Sources and gaps discussed",
        decision_ids=["retain_direction"],
        governance_decisions=[proof],
    )
    return await q.base.execute(w, req)


async def input_from_review(w, target):
    ops = w["d"].ops
    completion = m.read(ops, target, OWNER)["completion"]
    source = dict(
        kind="sprint_review_decision",
        meeting_id=target["meeting_id"],
        completion_id=completion["completion_id"],
        completion_digest=pm_snapshots._digest(completion["document"]),
        decision_id="retain_direction",
    )
    idea = await action(
        w,
        "pm.thread.create",
        {"workspace_id": w["workspace_id"]},
        {"title": "Explicit next scope"},
        {"expected_version": pm_workspace.get_workspace(ops.db, OWNER, w["workspace_id"])["version"]},
    )
    target = {"workspace_id": w["workspace_id"], "thread_id": idea["thread_id"]}
    req = q.envelope(
        "pm.input.record",
        target,
        {
            "slot": "scope",
            "value": "Investigate remaining evidence",
            "status": "unresolved",
            "source_ref": source,
        },
        {"expected_version": 1},
    )
    original = await q.base.execute(w, req)
    return target, original


async def prepared(w, mock, monkeypatch, kind):
    ops = w["d"].ops
    att = await r._start_managed_attempt(w, mock)
    r._set_turn_messages(mock, att["session_id"], att["turn_marker"])
    await w["d"].inventory._refresh_scheduled("h1")
    receipt = r.pm_attempt_results.receipts(ops.db, w["workspace_id"], att["attempt_id"])["receipts"][0]
    for mod in (pm_sprints, pm_meetings, pm_governance, pm_follow_up):
        mod.install(ops)
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
                "criteria": [{"id": "regression", "text": "Original regression passes"}],
                "rationale": "Fixed DoD",
            },
            {"expected_revision_id": None},
        )
    )["revision_id"]
    sprint = await q.sprint(w, monkeypatch)
    snap = await q.snapshot(w)
    target = await new_meeting(w, sprint, snap)
    note = await m.apply(ops, target, "note", who=OWNER, content="Original stakeholder wording")
    selected = basis()
    selected["feedback_items"] = [
        dict(id="voice", source_ref={"kind": "meeting_note", "note_id": note["note_id"]})
    ]
    selected["results"] = [
        {k: receipt[k] for k in ("attempt_id", "result_id", "digest")} | {"assessment_ref": None}
    ]
    if kind == "assessment":
        from tests.test_checkpoints import git

        w["package"] = r.pm_attempt_result_reads.read_fixed_receipt(
            ops,
            r.STARTER_OWNER,
            w["workspace_id"],
            att["attempt_id"],
            receipt["result_id"],
            receipt["digest"],
        )["package"]
        w["child"] = ops.get(att["handoff_operation_id"])
        w["candidate"] = git(att["worktree_path"], "rev-parse", "HEAD")
        w["tree"] = git(att["worktree_path"], "rev-parse", "HEAD^{tree}")
        w["proof"] = q.base.observed(w, w["child"], candidate=w["candidate"], tree=w["tree"])
        await member(w, q.base.REVIEWER.actor, "contributor")
        selected["results"][0]["assessment_ref"] = await q.qualified(w)
    await draft(w, target, selected)
    if kind == "note":
        sourced = await m.apply(
            ops, target, "note", who=OWNER, content="Feedback informed by the original Result"
        )
        selected = basis()
        selected["feedback_items"] = [
            dict(id="voice", source_ref={"kind": "meeting_note", "note_id": sourced["note_id"]})
        ]
    elif kind in ("governance", "follow_up"):
        proof = await decide(w, target)
        selected["results"] = []
        selected["governance_revision_ids"] = [proof["revision_id"]]
        if kind == "follow_up":
            intake = await action(
                w,
                "pm.follow_up.prepare",
                {"workspace_id": w["workspace_id"]},
                {"snapshot_id": snap["snapshot_id"], "governance_revision_ids": [proof["revision_id"]]},
                {
                    "expected_version": 0,
                    "expected_plan_version": pm_plans.head(ops.db, w["workspace_id"])["version"],
                },
            )
            original = pm_follow_up.get_intake(ops, OWNER, w["workspace_id"], intake["intake_id"])["intake"]
            selected["governance_revision_ids"] = []
            selected["follow_up_ref"] = {k: original[k] for k in ("intake_id", "document_digest")} | {
                "issue_ids": [i["issue_id"] for i in original["document"]["issues"]]
            }
    elif kind == "input":
        await complete(w, target)
        thread, recorded = await input_from_review(w, target)
        target = await new_meeting(w, sprint, snap)
        await m.apply(ops, target, "note", who=OWNER, content="Discuss original feedback")
        selected = basis()
        selected["feedback_items"] = [
            dict(
                id="voice",
                source_ref={
                    "kind": "discussion_input",
                    "thread_id": thread["thread_id"],
                    "input_id": recorded["result"]["input_id"],
                    "operation_id": recorded["operation_id"],
                },
            )
        ]
    project = ops.db.execute(
        "SELECT project_id FROM work_items WHERE work_item_id=?", (att["work_item_id"],)
    ).fetchone()[0]
    return target, selected, project


@pytest.mark.parametrize("kind", ["result", "assessment", "note", "input", "governance", "follow_up"])
@pytest.mark.parametrize("stage", ["admission", "effect", "completion", "planning", "document"])
async def test_withdrawal_of_each_original_source_at_each_derived_stage(
    start_ready, mock, monkeypatch, kind, stage
):
    w, ops = start_ready, start_ready["d"].ops
    target, selected, project = await prepared(w, mock, monkeypatch, kind)
    if stage in ("admission", "effect"):
        # Stage a new draft while the original Result is still readable.
        decision = m.draft_params("review")["decisions"][0]
        decision.update(owner_actor=OWNER.actor, follow_up_ids=["check_quality"])
        params = m.draft_params(
            "review", decisions=[decision], follow_ups=[m.follow_up(OWNER.actor)], review_basis=selected
        )
        params["output"]["feedback"] = []
        req = m.request(ops, target, "draft", **params)
        pending = ops.create(OWNER, **req)[0] if stage == "effect" else None
        await withdraw(ops, w["workspace_id"])
        with pytest.raises(OperationError):
            if pending:
                await pm_meetings._run(OpContext(ops, pending))
            else:
                ops.create(OWNER, **req)
        if pending:
            assert not ops.db.execute(
                "SELECT 1 FROM pm_meeting_drafts WHERE operation_id=?", (pending["operation_id"],)
            ).fetchone()
        return
    saved, req = await draft(w, target, selected)
    completed = await complete(w, target)
    thread = plan = None
    if stage in ("planning", "document"):
        thread, recorded = await input_from_review(w, target)
        plan = await q.base.execute(
            w,
            q.envelope(
                "pm.thread.plan",
                thread,
                {"selected_input_ids": [recorded["result"]["input_id"]], "rationale": "Explicit Planning"},
                {"expected_version": 2},
            ),
        )
        if stage == "document":
            await action(
                w,
                "pm.plan.draft",
                thread,
                {"previous_revision_id": pm_plans.head(ops.db, w["workspace_id"])["latest_revision_id"]},
                {
                    "expected_version": 3,
                    "expected_plan_version": pm_plans.head(ops.db, w["workspace_id"])["version"],
                },
            )
    original = copy.deepcopy(saved["result"])
    await withdraw(ops, w["workspace_id"])
    for op in (saved, completed, *([plan] if plan else [])):
        assert not pm_access.visible(ops.db, OWNER, op)
        with pytest.raises(OperationError):
            ops.get(op["operation_id"], principal=OWNER)
    with pytest.raises(OperationError):
        m.read(ops, target, OWNER)
    with pytest.raises(OperationError):
        ops.create(OWNER, **req)
    if thread:
        with pytest.raises(OperationError):
            pm_discussions.get_thread(ops, OWNER, **thread)
    assert ops.get(saved["operation_id"])["result"] == original


@pytest.mark.parametrize("kind", ["result", "assessment", "note", "input", "governance", "follow_up"])
async def test_individual_source_denial_hides_planning_while_membership_remains(
    start_ready, mock, monkeypatch, kind
):
    """Real producers; inject only one source's access denial, not workspace denial."""
    from bat_agent_connector import pm_attempt_result_reads, pm_quality_projections

    w, ops = start_ready, start_ready["d"].ops
    target, selected, _ = await prepared(w, mock, monkeypatch, kind)
    saved, req = await draft(w, target, selected)
    completed = await complete(w, target)
    thread, recorded = await input_from_review(w, target)
    planned = await q.base.execute(
        w,
        q.envelope(
            "pm.thread.plan", thread,
            {"selected_input_ids": [recorded["result"]["input_id"]], "rationale": "Explicit Planning"},
            {"expected_version": 2},
        ),
    )
    # There is no per-note deletion action. Exercise the authoritative reader's
    # unavailable result without changing source bytes or broad membership policy.
    if kind == "result":
        module, name = pm_attempt_result_reads, "read_fixed_receipt"
    elif kind == "assessment":
        module, name = pm_quality_projections, "original"
    elif kind == "note":
        ref = selected["feedback_items"][0]["source_ref"]
        identity = ops.db.execute("SELECT operation_id FROM pm_meeting_notes WHERE note_id=?", (ref["note_id"],)).fetchone()[0]
        original = pm_meetings.operation_visible
        monkeypatch.setattr(pm_meetings, "operation_visible", lambda db, principal, operation:
            False if operation["operation_id"] == identity else original(db, principal, operation))
    elif kind == "input":
        identity = selected["feedback_items"][0]["source_ref"]["operation_id"]
        original = pm_access.operation_source_visible
        monkeypatch.setattr(pm_access, "operation_source_visible", lambda db, principal, operation_id:
            False if operation_id == identity else original(db, principal, operation_id))
    elif kind == "governance":
        identity = pm_governance._revision(ops.db, selected["governance_revision_ids"][0])["record_id"]
        original = pm_governance._access

        def inaccessible_record(reader, principal, record):
            if record["record_id"] == identity:
                raise OperationError("PM_SOURCE_UNAVAILABLE", "Original source is unavailable", 404)
            return original(reader, principal, record)

        monkeypatch.setattr(pm_governance, "_access", inaccessible_record)
    else:
        module, name = pm_follow_up, "get_intake"
    if kind in {"result", "assessment", "follow_up"}:
        def unavailable(*args, **kwargs):
            raise OperationError("PM_SOURCE_UNAVAILABLE", "Original source is unavailable", 404)
        monkeypatch.setattr(module, name, unavailable)
    assert pm_workspace.require_member(ops.db, OWNER, w["workspace_id"])["membership"]["role"] == "owner"
    before = ops.db.total_changes, list(mock.invokes)
    for operation in (saved, completed, recorded, planned):
        assert not pm_access.visible(ops.db, OWNER, operation)
        with pytest.raises(OperationError):
            ops.get(operation["operation_id"], principal=OWNER)
    for read in (
        lambda: m.read(ops, target, OWNER),
        lambda: pm_meetings.list_history(ops, OWNER, **target, kind="drafts"),
        lambda: pm_discussions.get_thread(ops, OWNER, **thread),
    ):
        with pytest.raises(OperationError):
            read()
    with pytest.raises(OperationError):
        ops.create(OWNER, **req)
    assert (ops.db.total_changes, list(mock.invokes)) == before
