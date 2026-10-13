"""Sourced plan drafts, immutable history and exact Git/journal adoption boundary."""

from __future__ import annotations

import copy
import json
import sqlite3
import uuid

import pytest

from bat_agent_connector import pm_discussions, pm_plans, pm_workspace
from bat_agent_connector.operations import ActionDef, OpContext, OperationError, OperationService
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


@pytest.fixture
def ops(tmp_path):
    journal = Journal(tmp_path / "journal.db")
    service = OperationService(journal)
    pm_workspace.install(service)
    pm_discussions.install(service)
    pm_plans.install(service)
    pm_plans.install(service)
    yield service
    journal.close()


async def planning(ops):
    target = await prepared(ops)
    confirmed = await act(ops, target, "pm.input.record", {"slot": "scope", "value": "Members can log in",
        "status": "user_confirmed", "source_ref": {"kind": "statement", "ref": "owner's selected direction"}})
    assumption = await record(ops, target, slot="finance", value="Subscription pricing is a candidate")
    acceptance = await record(ops, target, slot="quality", value="Password reset restores account access")
    await act(ops, target, "pm.thread.plan", {"selected_input_ids": [confirmed["input_id"], assumption["input_id"], acceptance["input_id"]],
                                             "rationale": "Prepare the membership plan"})
    return target, confirmed["input_id"], assumption["input_id"], acceptance["input_id"]


def draft_operation(ops, target, *, who=CONTRIBUTOR, **params):
    current = pm_plans.head(ops.db, target["workspace_id"])
    return ops.create(who, action="pm.plan.draft", target=target,
        params={"previous_revision_id": current["latest_revision_id"], **params},
        preconditions={"expected_version": read(ops, target)["thread"]["version"], "expected_plan_version": current["version"]},
        idempotency_key=uuid.uuid4().hex)[0]


async def draft(ops, target, **params):
    return (await settle(ops, draft_operation(ops, target, **params)))["result"]


def adoption(ops, target, revision_id, *, who=OWNER, rationale="Adopt this exact proposal", previous=None):
    async def run(ctx):
        return pm_plans.adopt_revision(ctx)
    def admit(service, principal, target, params, pre):
        return {**pm_plans.prepare_adoption(service, principal, target, params, pre), "git_binding_id": "3" * 64}
    if "pm.plan.adopt" not in ops.actions:
        # Only this test integration layer registers adoption. Production needs real managed Git.
        ops.register(ActionDef("pm.plan.adopt", "manage", "Adopt exact plan after Git readback", run,
                               admit, ("workspace_id", "revision_id"),
                               authorize_existing=pm_plans.authorize_existing))
    op, _ = ops.create(who, action="pm.plan.adopt", target={"workspace_id": target["workspace_id"], "revision_id": revision_id},
                       params={"rationale": rationale}, preconditions={"expected_accepted_revision_id": previous},
                       idempotency_key=uuid.uuid4().hex)
    return OpContext(ops, op)


def receipt(ctx):
    files = pm_plans.revision_files(ctx.service.db, ctx.target["workspace_id"], ctx.target["revision_id"])
    return {"operation_id": ctx.operation_id, "workspace_id": ctx.target["workspace_id"], "revision_id": ctx.target["revision_id"],
            "commit_sha": "1" * 40, "tree_sha": "2" * 40, "document_digest": files["document_digest"],
            "file_digests": files["file_digests"], "binding_id": "3" * 64, "status": "verified"}


def adopt(ctx):
    pm_plans.record_git_receipt(ctx, receipt(ctx))
    return pm_plans.adopt_revision(ctx)


async def test_professional_draft_preserves_all_domains_and_source_truth(ops):
    target, confirmed, assumption, acceptance = await planning(ops)
    saved = await draft(ops, target, requirements=[{"id": "membership.login", "source_input_id": confirmed,
                                                  "acceptance_input_ids": [acceptance], "priority": "must"}])
    plan = pm_plans.get_plan(ops, OWNER, target["workspace_id"], saved["revision_id"])["plan"]
    document = plan["document"]
    assert set(document["sections"]) == set(pm_plans.pm_profiles.DOMAINS)
    assert document["sections"]["finance"]["statements"][0]["status"] == "agent_assumption"
    assert document["sections"]["schedule"]["coverage"] == "unresolved"
    assert document["requirements"][0]["text"] == "Members can log in"
    assert document["requirements"][0]["acceptance"][0]["source_status"] == "agent_assumption"
    assert plan["adoption"] is None and not plan["is_current_accepted"]
    assert pm_plans.accepted_plan(ops, OWNER, target["workspace_id"])["plan"] is None
    assert not document["execution_authorized"]
    assert ops.db.execute("SELECT COUNT(*) FROM work_items").fetchone()[0] == 0
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        ops.db.execute("UPDATE pm_plan_revisions SET document='{}'")


async def test_draft_requires_explicit_planning_and_selected_sources(ops):
    target = await prepared(ops)
    with pytest.raises(OperationError, match="PM_PLANNING_REQUIRED"):
        draft_operation(ops, target)
    target, confirmed, _, _ = await planning(ops)
    candidate = await record(ops, target, value="Referral discounts")
    with pytest.raises(OperationError, match="INVALID_PARAMS"):
        draft_operation(ops, target, requirements=[{"id": "referral", "source_input_id": candidate["input_id"]}])
    with pytest.raises(OperationError, match="INVALID_PARAMS"):
        draft_operation(ops, target, requirements=[{"id": "referral", "source_input_id": confirmed, "text": "15% discount"}])


async def test_later_confirmation_never_rewrites_frozen_source_status(ops):
    target, _, assumption, _ = await planning(ops)
    one = await draft(ops, target)
    await act(ops, target, "pm.input.confirm", {"input_id": assumption, "rationale": "Later choose this pricing direction"})
    two = await draft(ops, target)
    for revision in (one, two):
        plan = pm_plans.get_plan(ops, OWNER, target["workspace_id"], revision["revision_id"])["plan"]
        assert next(item for item in plan["document"]["selected_inputs"] if item["input_id"] == assumption)["status"] == "agent_assumption"


async def test_draft_concurrency_preserves_previous_version(ops):
    target, *_ = await planning(ops)
    first, competing = draft_operation(ops, target), draft_operation(ops, target)
    result = await settle(ops, first)
    other = ops.get(competing["operation_id"])
    assert other["status"] == "failed" and other["error_code"] == "VERSION_CONFLICT"
    assert pm_plans.head(ops.db, target["workspace_id"])["latest_revision_id"] == result["result"]["revision_id"]
    assert len(pm_plans.list_plans(ops, OWNER, target["workspace_id"])["revisions"]) == 1


async def test_git_only_does_not_adopt_and_reply_loss_replays_original_decision(ops):
    target, *_ = await planning(ops)
    first = await draft(ops, target)
    ctx = adoption(ops, target, first["revision_id"])
    with pytest.raises(OperationError, match="PM_GIT_RECEIPT_REQUIRED"):
        pm_plans.adopt_revision(ctx)
    saved_receipt = pm_plans.record_git_receipt(ctx, receipt(ctx))
    assert pm_plans.accepted_plan(ops, OWNER, target["workspace_id"])["plan"] is None
    assert pm_plans.record_git_receipt(ctx, saved_receipt) == saved_receipt
    adopted = pm_plans.adopt_revision(ctx)
    assert pm_plans.adopt_revision(ctx) == adopted
    assert adopted["scope_changed"] and not adopted["execution_authorized"]
    assert ops.db.execute("SELECT COUNT(*) FROM pm_plan_adoptions").fetchone()[0] == 1
    assert (await settle(ops, ctx.op))["result"] == adopted


async def test_new_git_commit_preserves_previous_accepted_until_journal_adoption(ops):
    target, *_ = await planning(ops)
    first = await draft(ops, target)
    accepted = adopt(adoption(ops, target, first["revision_id"]))
    second = await draft(ops, target, markdown="# Updated narrative\n\nProgress remains unverified.\n")
    ctx = adoption(ops, target, second["revision_id"], previous=first["revision_id"])
    pm_plans.record_git_receipt(ctx, receipt(ctx))
    assert pm_plans.accepted_plan(ops, OWNER, target["workspace_id"])["plan"]["revision_id"] == first["revision_id"]
    updated = pm_plans.adopt_revision(ctx)
    assert updated["scope_changed"] is None and updated["scope_impact"] == "unknown"
    assert updated["scope_revision"] != accepted["scope_revision"]
    assert updated["document_revision"] != accepted["document_revision"]
    assert pm_plans.get_plan(ops, OWNER, target["workspace_id"], first["revision_id"])["plan"]["status"] == "adopted"


async def test_concurrent_adoptions_commit_only_one_pointer(ops):
    target, *_ = await planning(ops)
    first = await draft(ops, target)
    second = await draft(ops, target)
    one, two = adoption(ops, target, first["revision_id"]), adoption(ops, target, second["revision_id"])
    pm_plans.record_git_receipt(one, receipt(one))
    pm_plans.record_git_receipt(two, receipt(two))
    accepted = pm_plans.adopt_revision(one)
    with pytest.raises(OperationError, match="PM_ACCEPTED_REVISION_CONFLICT"):
        pm_plans.adopt_revision(two)
    assert pm_plans.head(ops.db, target["workspace_id"])["accepted_revision_id"] == accepted["revision_id"]


async def test_same_scope_and_prose_preserve_direction_revision(ops):
    target, *_ = await planning(ops)
    first = await draft(ops, target)
    initial = adopt(adoption(ops, target, first["revision_id"]))
    second = await draft(ops, target)
    updated = adopt(adoption(ops, target, second["revision_id"], previous=first["revision_id"]))
    assert updated["scope_changed"] is False and updated["scope_impact"] == "document_only"
    assert updated["scope_revision"] == initial["scope_revision"]
    assert updated["document_digest"] != initial["document_digest"]


async def test_contradictory_scope_prose_is_preserved_and_not_classified_as_progress(ops):
    target, confirmed, *_ = await planning(ops)
    reqs = [{"id": "login", "source_input_id": confirmed}]
    first = await draft(ops, target, requirements=reqs)
    initial = adopt(adoption(ops, target, first["revision_id"]))
    prose = "# Revised scope\nAdd a referral program with a 15% discount. All verification is complete.\n"
    second = await draft(ops, target, requirements=reqs, markdown=prose)
    updated = adopt(adoption(ops, target, second["revision_id"], previous=first["revision_id"],
                             rationale="Explicit owner decision on the complete fixed proposal"))
    accepted = pm_plans.accepted_plan(ops, OWNER, target["workspace_id"])["plan"]
    assert accepted["document"]["markdown"] == prose
    assert accepted["document"]["requirements"][0]["text"] == "Members can log in"
    assert updated["scope_impact"] == "unknown" and updated["scope_changed"] is None
    assert updated["scope_revision"] != initial["scope_revision"]
    assert accepted["document"]["scope_coverage"]["markdown"] == "unclassified"
    assert ops.db.execute("SELECT COUNT(*) FROM work_items").fetchone()[0] == 0


@pytest.mark.parametrize("field,value", [("operation_id", "op_foreign"), ("commit_sha", "short"),
                                       ("tree_sha", "2" * 64), ("document_digest", "0" * 64),
                                       ("file_digests", {}), ("status", "claimed"), ("binding_id", "unknown")])
async def test_foreign_or_unverified_receipt_cannot_adopt(ops, field, value):
    target, *_ = await planning(ops)
    saved = await draft(ops, target)
    ctx = adoption(ops, target, saved["revision_id"])
    evidence = receipt(ctx)
    evidence[field] = value
    with pytest.raises(OperationError, match="PM_GIT_RECEIPT_INVALID"):
        pm_plans.record_git_receipt(ctx, evidence)
    assert pm_plans.head(ops.db, target["workspace_id"])["accepted_revision_id"] is None


async def test_receipt_is_immutable_and_not_a_public_adoption_bypass(ops):
    target, *_ = await planning(ops)
    saved = await draft(ops, target)
    assert "pm.plan.adopt" not in ops.actions
    ctx = adoption(ops, target, saved["revision_id"])
    evidence = receipt(ctx)
    pm_plans.record_git_receipt(ctx, evidence)
    changed = {**evidence, "commit_sha": "4" * 40}
    with pytest.raises(OperationError, match="PM_GIT_RECEIPT_CONFLICT"):
        pm_plans.record_git_receipt(ctx, changed)
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        ops.db.execute("DELETE FROM pm_plan_git_receipts")


async def test_reader_and_outsider_cannot_write_or_read_private_plans(ops):
    target, *_ = await planning(ops)
    saved = await draft(ops, target)
    with pytest.raises(OperationError, match="FORBIDDEN"):
        draft_operation(ops, target, who=READER)
    with pytest.raises(OperationError, match="FORBIDDEN"):
        adoption(ops, target, saved["revision_id"], who=CONTRIBUTOR)
    with pytest.raises(OperationError, match="NOT_FOUND"):
        pm_plans.get_plan(ops, OUTSIDER, target["workspace_id"], saved["revision_id"])


async def test_membership_loss_preserves_git_effect_but_prevents_new_adoption(ops):
    target, *_ = await planning(ops)
    await member(ops, {"workspace_id": target["workspace_id"]}, CONTRIBUTOR.actor, "owner")
    saved = await draft(ops, target)
    ctx = adoption(ops, target, saved["revision_id"], who=CONTRIBUTOR)
    await member(ops, {"workspace_id": target["workspace_id"]}, CONTRIBUTOR.actor, "reader")
    pm_plans.record_git_receipt(ctx, receipt(ctx))
    with pytest.raises(OperationError, match="FORBIDDEN"):
        pm_plans.adopt_revision(ctx)
    assert pm_plans.head(ops.db, target["workspace_id"])["accepted_revision_id"] is None


async def test_lost_adoption_reply_after_demotion_reads_original_fact(ops):
    target, *_ = await planning(ops)
    await member(ops, {"workspace_id": target["workspace_id"]}, CONTRIBUTOR.actor, "owner")
    saved = await draft(ops, target)
    ctx = adoption(ops, target, saved["revision_id"], who=CONTRIBUTOR)
    result = adopt(ctx)
    await member(ops, {"workspace_id": target["workspace_id"]}, CONTRIBUTOR.actor, "reader")
    assert pm_plans.adopt_revision(ctx) == result
    with pytest.raises(OperationError, match="FORBIDDEN"):
        pm_plans.authorize_existing(ops, CONTRIBUTOR, ctx.op, "resume")


async def test_fixed_revision_files_have_no_self_commit_and_stable_byte_digests(ops):
    target, *_ = await planning(ops)
    saved = await draft(ops, target, markdown="  # 原始計畫\n\n尚未採用\n")
    files = pm_plans.revision_files(ops.db, target["workspace_id"], saved["revision_id"])
    assert files == pm_plans.revision_files(ops.db, target["workspace_id"], saved["revision_id"])
    base = f"docs/pm/revisions/{saved['revision_id']}"
    assert set(files["files"]) == {f"{base}/plan.md", f"{base}/plan.json"}
    assert files["files"][f"{base}/plan.md"] == "  # 原始計畫\n\n尚未採用\n"
    metadata = json.loads(files["files"][f"{base}/plan.json"])
    assert "commit_sha" not in metadata and "document_digest" not in metadata
    assert files["document_digest"] == saved["document_digest"]


async def test_requirement_dependency_cycles_and_authored_confirmation_are_rejected(ops):
    target, confirmed, *_ = await planning(ops)
    reqs = [{"id": "first", "source_input_id": confirmed, "depends_on": ["second"]},
            {"id": "second", "source_input_id": confirmed, "depends_on": ["first"]}]
    with pytest.raises(OperationError, match="cycle"):
        draft_operation(ops, target, requirements=reqs)
    bad = copy.deepcopy(reqs[:1])
    bad[0].update(depends_on=[], source_status="user_confirmed")
    with pytest.raises(OperationError, match="INVALID_PARAMS"):
        draft_operation(ops, target, requirements=bad)


async def test_profile_v2_plan_still_reads_and_validates_after_catalog_upgrade(ops, monkeypatch):
    from bat_agent_connector import pm_profiles
    assert pm_profiles.VERSION == 3
    target, *_ = await planning(ops)
    with monkeypatch.context() as legacy:
        legacy.setattr(pm_profiles, 'VERSION', 2)
        saved = await draft(ops, target)
    ctx = adoption(ops, target, saved['revision_id'])
    result = adopt(ctx)
    plan = pm_plans.get_plan(ops, OWNER, target['workspace_id'], saved['revision_id'])['plan']
    assert plan['document']['profile_version'] == 2
    assert plan['adoption']['revision_id'] == result['revision_id']
    assert pm_plans.revision_files(ops.db, target['workspace_id'], saved['revision_id'])['document_digest'] == plan['document_digest']
