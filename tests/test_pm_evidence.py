"""Workspace association, frozen source facts and truthful completion boundaries."""
from __future__ import annotations

import copy
import json
import sqlite3
import uuid

import pytest

from bat_agent_connector import (
    artifacts,
    controller_authority,
    deployment_store,
    pm_discussions,
    pm_evidence,
    pm_plans,
    pm_runs,
    pm_workspace,
    work_items,
)
from bat_agent_connector.api_auth import Principal
from bat_agent_connector.operations import ActionDef, OpContext, OperationError, OperationService
from bat_agent_connector.task_journal import Journal
from tests.test_pm_discussions import CONTRIBUTOR, OUTSIDER, OWNER, READER, member, settle, workspace
from tests.test_pm_plans import adopt, adoption, draft, planning
from tests.test_pm_runs import create as create_run


@pytest.fixture
def ops(tmp_path):
    journal = Journal(tmp_path / "evidence.db")
    service = OperationService(journal, actions=work_items.ACTIONS)
    service.context["artifact_store"] = artifacts.ArtifactStore(service)
    pm_workspace.install(service)
    pm_discussions.install(service)
    pm_plans.install(service)
    version = journal.db.execute("PRAGMA user_version").fetchone()[0]
    pm_evidence.install(service)
    pm_evidence.install(service)
    assert journal.db.execute("PRAGMA user_version").fetchone()[0] == version
    yield service
    journal.close()


async def act(ops, action, target=None, params=None, pre=None, *, who=OWNER):
    op, _ = ops.create(who, action=action, target=target or {}, params=params or {},
                       preconditions=pre or {}, idempotency_key=uuid.uuid4().hex)
    return (await settle(ops, op))["result"]


async def project(ops, name="Example product"):
    return (await act(ops, "project.create", params={"name": name, "repositories": ["example/product"]}))["project_id"]


async def item(ops, project_id, title="A useful deliverable"):
    return (await act(ops, "work_item.create", {"project_id": project_id}, {"title": title}))["work_item_id"]


async def bind_project(ops, wid, project_id):
    return await act(ops, "pm.project.bind", {"workspace_id": wid, "project_id": project_id}, {"rationale": "Track this existing project"},
                     {"expected_workspace_version": pm_workspace.get_workspace(ops.db, OWNER, wid)["version"],
                      "expected_project_version": work_items._get_project(ops.db, project_id)["version"]})


def work_request(ops, target, **params):
    binding = pm_evidence._binding(ops.db, **target)
    return {"action": "pm.work.bind", "target": target, "params": {"rationale": "Associate this exact work", **params},
            "preconditions": {"expected_work_version": work_items._get_item(ops.db, target["work_item_id"])["version"],
                              "expected_binding_id": binding["binding_id"] if binding else None},
            "idempotency_key": uuid.uuid4().hex}


async def bind_work(ops, target, **params):
    op, _ = ops.create(OWNER, **work_request(ops, target, **params))
    return (await settle(ops, op))["result"]


async def prepared(ops):
    ws = await workspace(ops)
    await member(ops, ws, CONTRIBUTOR.actor, "contributor")
    await member(ops, ws, READER.actor, "reader")
    pid = await project(ops)
    work = await item(ops, pid)
    await bind_project(ops, ws["workspace_id"], pid)
    target = {"workspace_id": ws["workspace_id"], "work_item_id": work}
    await bind_work(ops, target)
    return target


def capture_request(ops, target):
    return {"action": "pm.evidence.capture", "target": target, "params": {},
            "preconditions": {"expected_binding_id": pm_evidence._binding(ops.db, **target)["binding_id"]},
            "idempotency_key": uuid.uuid4().hex}


async def capture(ops, target, who=CONTRIBUTOR):
    op, _ = ops.create(who, **capture_request(ops, target))
    return (await settle(ops, op))["result"]


def facts(ops, target, capture_id=None):
    doc = (pm_evidence.get_capture(ops, OWNER, target["workspace_id"], capture_id) if capture_id
           else pm_evidence.latest_capture(ops, OWNER, **target))["capture"]
    return [pm_evidence.get_evidence(ops, OWNER, target["workspace_id"], eid)["evidence"] for eid in doc["evidence_ids"]]


async def link(ops, target, kind, ref, **params):
    return await act(ops, "work_item.link", {"work_item_id": target["work_item_id"]}, {"kind": kind, "ref": ref, **params})


async def source_operation(ops, *, action="fixture.source", params=None, target=None, result=None):
    async def run(ctx):
        return result or {"verified": True, "merged": True, "deployed": True, "prompt": "PRIVATE PROMPT"}
    if action not in ops.actions:
        ops.register(ActionDef(action, "manage", "Fixture source without external effects", run))
    op, _ = ops.create(OWNER, action=action, target=target or {}, params=params or {"prompt": "PRIVATE PARAMS"}, idempotency_key=uuid.uuid4().hex)
    return await settle(ops, op)


def task(ops):
    return ops.journal.submit(project="example", host="mock-host", workspace="mock-workspace", original_words="PRIVATE TASK WORDS",
                              idempotency_key=uuid.uuid4().hex)


async def test_same_repository_never_binds_or_imports_projects_and_work(ops):
    ws = await workspace(ops)
    pid = await project(ops)
    work = await item(ops, pid)
    target = {"workspace_id": ws["workspace_id"], "work_item_id": work}
    assert pm_evidence.list_projects(ops, OWNER, ws["workspace_id"])["bindings"] == []
    assert pm_evidence.list_work(ops, OWNER, ws["workspace_id"])["bindings"] == []
    with pytest.raises(OperationError, match="PM_PROJECT_BINDING_REQUIRED"):
        ops.create(OWNER, **work_request(ops, target))
    await bind_project(ops, ws["workspace_id"], pid)
    assert pm_evidence.list_work(ops, OWNER, ws["workspace_id"])["bindings"] == []
    before = copy.deepcopy(work_items.work_item_get(ops.db, work))
    await bind_work(ops, target)
    assert work_items.work_item_get(ops.db, work) == before
    assert pm_evidence.list_work(ops, OWNER, ws["workspace_id"])["bindings"][0]["execution_spec"] == "not_assigned"


async def test_historically_adopted_requirement_association_is_exact_and_does_not_follow_head(ops):
    discussion, confirmed, *_ = await planning(ops)
    first = await draft(ops, discussion, requirements=[{"id": "member.login", "source_input_id": confirmed}])
    adopt(adoption(ops, discussion, first["revision_id"]))
    second = await draft(ops, discussion, requirements=[{"id": "member.next", "source_input_id": confirmed}])
    adopt(adoption(ops, discussion, second["revision_id"], previous=first["revision_id"]))
    pid = await project(ops)
    wid = discussion["workspace_id"]
    target = {"workspace_id": wid, "work_item_id": await item(ops, pid)}
    await bind_project(ops, wid, pid)
    with pytest.raises(OperationError, match="INVALID_PARAMS"):
        ops.create(OWNER, **work_request(ops, target, plan_revision_id=first["revision_id"], requirement_ids=["member.next"]))
    await bind_work(ops, target, plan_revision_id=first["revision_id"], requirement_ids=["member.login"])
    saved = copy.deepcopy(pm_evidence._binding(ops.db, **target))
    await capture(ops, target)
    cap = pm_evidence.latest_capture(ops, OWNER, **target)["capture"]
    assert cap["plan_revision_id"] == first["revision_id"] and cap["requirement_ids"] == ["member.login"]
    assert pm_plans.head(ops.db, wid)["accepted_revision_id"] == second["revision_id"]
    proposed = await draft(ops, discussion)
    with pytest.raises(OperationError, match="PM_ADOPTED_PLAN_REQUIRED"):
        ops.create(OWNER, **work_request(ops, target, plan_revision_id=proposed["revision_id"]))
    assert pm_evidence._binding(ops.db, **target) == saved
    assert ops.db.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 0


async def test_rebinding_appends_history_and_captures_require_current_binding(ops):
    target = await prepared(ops)
    old = pm_evidence._binding(ops.db, **target)
    stale = capture_request(ops, target)
    await bind_work(ops, target, rationale="Explicitly refresh the work association")
    current = pm_evidence._binding(ops.db, **target)
    assert current["previous_binding_id"] == old["binding_id"]
    with pytest.raises(OperationError, match="PM_WORK_BINDING_CHANGED"):
        ops.create(OWNER, **stale)
    assert ops.db.execute("SELECT COUNT(*) FROM pm_work_bindings").fetchone()[0] == 2
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        ops.db.execute("UPDATE pm_work_bindings SET rationale='rewritten'")


async def test_work_done_and_approval_are_claims_without_quality_or_deployment_proof(ops):
    target = await prepared(ops)
    work = work_items._get_item(ops.db, target["work_item_id"])
    await act(ops, "work_item.update", {"work_item_id": work["work_item_id"]}, {"state": "done"}, {"expected_version": work["version"]})
    first = await capture(ops, target)
    row = facts(ops, target)[0]
    assert row["document"]["facts"]["completion"]["display_state"] == "awaiting_approval"
    assert row["dod"] == "unassessed" and row["cost"] == "unknown"
    work = work_items._get_item(ops.db, target["work_item_id"])
    approver = Principal(OWNER.actor, frozenset({"observe", "manage", "approve"}))
    await act(ops, "work_item.approve", {"work_item_id": work["work_item_id"]}, {},
              {"expected_fingerprint": work_items.fingerprint(work)}, who=approver)
    await capture(ops, target)
    latest = facts(ops, target)[0]
    assert latest["document"]["facts"]["completion"]["approved"] is True
    assert latest["dod"] == "unassessed" and latest["evidence_id"] != row["evidence_id"]
    assert facts(ops, target, first["capture_id"])[0] == row


async def test_replay_deduplicates_facts_across_captures_and_multiple_work_items(ops):
    target = await prepared(ops)
    op = await source_operation(ops)
    await link(ops, target, "operation", op["operation_id"])
    first = await capture(ops, target)
    before = facts(ops, target)
    await capture(ops, target)
    assert facts(ops, target) == before
    assert ops.db.execute("SELECT COUNT(*) FROM pm_evidence").fetchone()[0] == len(before)
    assert first["capture_sequence"] < pm_evidence.latest_capture(ops, OWNER, **target)["capture"]["sequence"]
    other = {**target, "work_item_id": await item(ops, work_items._get_item(ops.db, target["work_item_id"])["project_id"], "Same result contributor")}
    await bind_work(ops, other)
    await link(ops, other, "operation", op["operation_id"])
    await capture(ops, other)
    for source in (target, other):
        row = next(e for e in facts(ops, source) if e["source_kind"] == "operation_status")
        assert row["source_id"] == op["operation_id"]
    assert ops.db.execute("SELECT COUNT(*) FROM pm_evidence WHERE source_kind='operation_status'").fetchone()[0] == 1
    assert "PRIVATE" not in json.dumps(facts(ops, target))


async def test_lost_ack_recovers_original_capture_and_restart_preserves_ids(ops, tmp_path):
    target = await prepared(ops)
    request = capture_request(ops, target)
    op, _ = ops.create(OWNER, **request)
    applied = await pm_evidence._run(OpContext(ops, op))
    replay, created = ops.create(OWNER, **request)
    assert not created and (await settle(ops, replay))["result"] == applied
    before = facts(ops, target)
    restarted = Journal(tmp_path / "evidence.db")
    try:
        recovered = OperationService(restarted)
        pm_evidence.install(recovered)
        assert recovered.create(OWNER, **request)[0]["result"] == applied
        assert facts(recovered, target) == before
        assert restarted.db.execute("SELECT COUNT(*) FROM pm_evidence_captures").fetchone()[0] == 1
    finally:
        restarted.close()


async def test_latest_capture_uses_insertion_sequence_when_clock_moves_backwards(ops, monkeypatch):
    target = await prepared(ops)
    one = await capture(ops, target)
    old_clock = pm_evidence.time.time
    with monkeypatch.context() as clock:
        clock.setattr(pm_evidence.time, "time", lambda: old_clock() - 1000)
        two = await capture(ops, target)
    first = pm_evidence.get_capture(ops, OWNER, target["workspace_id"], one["capture_id"])["capture"]
    latest = pm_evidence.latest_capture(ops, OWNER, **target)["capture"]
    assert latest["capture_id"] == two["capture_id"] and latest["sequence"] > first["sequence"]
    assert latest["captured_at"] < first["captured_at"]


async def test_observed_runner_binds_verification_to_exact_commit_while_task_done_remains_distinct(ops):
    target = await prepared(ops)
    source = task(ops)
    await link(ops, target, "task", source["task_id"])
    ops.db.execute("UPDATE tasks SET state='done' WHERE task_id=?", (source["task_id"],))
    ops.journal.record_observed_verification(source["task_id"], {"candidate_commit": "1" * 40, "tree_hash": "2" * 40,
        "command": "pytest --fixture-secret PRIVATE", "exit_code": 0, "log_ref": "private-log", "output_sha256": "3" * 64,
        "source": "observed_runner"})
    await capture(ops, target)
    evidence = facts(ops, target)
    passed = next(e for e in evidence if e["source_kind"] == "observed_verification")["document"]["facts"]
    assert passed["verification"] == "passed" and passed["candidate_commit"] == "1" * 40
    assert all(e["dod"] == "unassessed" for e in evidence)
    assert "PRIVATE" not in json.dumps(evidence) and "private-log" not in json.dumps(evidence)
    assert not any(e["source_kind"] == "deployment" for e in evidence)


async def test_claimed_verification_or_merge_in_operation_payload_does_not_become_proof(ops):
    target = await prepared(ops)
    op = await source_operation(ops, action="session.record_verification")
    await link(ops, target, "operation", op["operation_id"])
    await capture(ops, target)
    evidence = facts(ops, target)
    assert {e["source_kind"] for e in evidence} == {"work_claim", "operation_status", "work_link"}
    assert all(e["dod"] == "unassessed" for e in evidence)
    assert not any(e["document"]["facts"].get("verification") == "passed" for e in evidence)


async def test_merge_receipt_is_specific_head_evidence_and_new_head_never_rewrites_old_capture(ops):
    target = await prepared(ops)
    preview_id = "mpv_" + "a" * 32
    doc = {"target": {"number": 1, "head_sha": "1" * 40, "base_sha": "2" * 40}, "repository": "example/product"}
    ops.db.execute("INSERT INTO pr_merge_previews VALUES(?,?,?,?,?,?,?)", (preview_id, "example/product", 1, json.dumps(doc), "a" * 64, 100, 200))
    op = await source_operation(ops, action="github.pr.merge", target={"repository": "example/product", "pull_number": 1}, params={"preview_id": preview_id})
    await link(ops, target, "operation", op["operation_id"])
    await link(ops, target, "pull_request", "example/product#1")
    await capture(ops, target)
    assert not any(e["source_kind"] == "pr_merge" for e in facts(ops, target))
    ops._step_start(op["operation_id"], "merge.verify", {"preview_id": preview_id})
    ops._step_done(op["operation_id"], "merge.verify", {"verified": True, "merged_sha": "3" * 40, "merged_onto_base_sha": "2" * 40})
    old_capture = await capture(ops, target)
    old = facts(ops, target)
    merged = next(e for e in old if e["source_kind"] == "pr_merge")
    assert merged["document"]["facts"]["head_sha"] == "1" * 40 and merged["dod"] == "unassessed"
    later = {**doc, "target": {**doc["target"], "head_sha": "4" * 40}}
    ops.db.execute("INSERT INTO pr_merge_previews VALUES(?,?,?,?,?,?,?)", ("mpv_" + "b" * 32, "example/product", 1, json.dumps(later), "b" * 64, 300, 400))
    await capture(ops, target)
    assert facts(ops, target, old_capture["capture_id"]) == old
    heads = {e["document"]["facts"]["head_sha"] for e in facts(ops, target) if e["source_kind"] == "pr_observation"}
    assert heads == {"1" * 40, "4" * 40}


async def test_deployment_status_success_needs_saved_runtime_proof_and_never_claims_current_environment(ops):
    target = await prepared(ops)
    op = await source_operation(ops, action="deployment.start")
    await link(ops, target, "operation", op["operation_id"])
    dep_id = "dep_" + op["operation_id"][3:]
    ops.db.execute("""INSERT INTO deployments(deployment_id,operation_id,recipe,environment_key,identity,recipe_snapshot,
        recipe_digest,state,document,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
        (dep_id, op["operation_id"], "fixture", "fixture-env", json.dumps({"source_sha": "1" * 40}), "{}", "a" * 64,
         "succeeded", json.dumps({"verified": False}), 100, 100))
    first = await capture(ops, target)
    initial = next(e for e in facts(ops, target) if e["source_kind"] == "deployment")
    assert initial["document"]["facts"]["deployment"] == "unverified"
    deployment_store.update(ops.journal, dep_id, facts={"verified": True,
        "evidence": {"provider": {"conclusion": "success"}, "runtime": {"checked_at": 200,
            "observed": {"source_sha": "1" * 40, "repository_id": 1, "environment": "staging", "healthy": True}}}})
    await capture(ops, target)
    latest = next(e for e in facts(ops, target) if e["source_kind"] == "deployment")
    assert latest["document"]["facts"]["deployment"] == "verified"
    assert latest["document"]["facts"]["current_environment"] == "not_assessed" and latest["dod"] == "unassessed"
    assert next(e for e in facts(ops, target, first["capture_id"]) if e["source_kind"] == "deployment") == initial


@pytest.mark.parametrize("who,code", [(CONTRIBUTOR, "FORBIDDEN"), (READER, "FORBIDDEN"), (OUTSIDER, "NOT_FOUND"),
    (Principal(OWNER.actor, frozenset({"manage"})), "FORBIDDEN")])
async def test_association_requires_owner_and_observe_manage_scopes(ops, who, code):
    target = await prepared(ops)
    with pytest.raises(OperationError, match=code):
        ops.create(who, **work_request(ops, target))


async def test_reader_cannot_capture_and_private_workspace_reads_do_not_leak(ops):
    target = await prepared(ops)
    result = await capture(ops, target)
    evidence_id = facts(ops, target)[0]["evidence_id"]
    with pytest.raises(OperationError, match="FORBIDDEN"):
        ops.create(READER, **capture_request(ops, target))
    for read in (lambda: pm_evidence.list_projects(ops, OUTSIDER, target["workspace_id"]),
                 lambda: pm_evidence.list_work(ops, OUTSIDER, target["workspace_id"]),
                 lambda: pm_evidence.list_evidence(ops, OUTSIDER, target["workspace_id"]),
                 lambda: pm_evidence.get_evidence(ops, OUTSIDER, target["workspace_id"], evidence_id),
                 lambda: pm_evidence.get_capture(ops, OUTSIDER, target["workspace_id"], result["capture_id"]),
                 lambda: pm_evidence.latest_capture(ops, OUTSIDER, **target)):
        with pytest.raises(OperationError, match="NOT_FOUND"):
            read()
    assert pm_evidence.get_evidence(ops, READER, target["workspace_id"], evidence_id)["evidence"]["dod"] == "unassessed"


async def test_foreign_private_pm_operation_in_legacy_link_is_unavailable_without_payload_leak(ops):
    target = await prepared(ops)
    private = await workspace(ops, OUTSIDER)
    op = ops.db.execute("SELECT operation_id FROM pm_workspaces WHERE workspace_id=?", (private["workspace_id"],)).fetchone()[0]
    # Simulate a corrupt/legacy link; ordinary work_item.link already disallows PM operations.
    ops.db.execute("""INSERT INTO work_item_links(work_item_id,kind,ref,note,linked_by,linked_at,link_operation)
        VALUES(?,?,?,?,?,?,?)""", (target["work_item_id"], "operation", op, "PRIVATE NOTE", OWNER.actor, 1, "legacy-link"))
    await capture(ops, target)
    serialized = json.dumps({"capture": pm_evidence.latest_capture(ops, OWNER, **target), "facts": facts(ops, target)})
    assert op not in serialized and private["workspace_id"] not in serialized and "PRIVATE" not in serialized
    assert pm_evidence.latest_capture(ops, OWNER, **target)["capture"]["coverage"]["unavailable_sources"] == 1


async def test_foreign_pm_task_cannot_be_transported_through_global_work_link(ops):
    target = await prepared(ops)
    private = await workspace(ops, OUTSIDER)
    authority = controller_authority.binding(ops.db, OUTSIDER, pm_workspace_id=private["workspace_id"])
    with controller_authority.use(authority, ops.journal):
        source = task(ops)
    await link(ops, target, "task", source["task_id"])
    await capture(ops, target)
    serialized = json.dumps(facts(ops, target))
    assert source["task_id"] not in serialized and private["workspace_id"] not in serialized
    assert pm_evidence.latest_capture(ops, OWNER, **target)["capture"]["coverage"]["unavailable_sources"] == 1


async def test_source_visibility_is_rechecked_on_saved_fact_read(ops):
    target = await prepared(ops)
    op = await source_operation(ops)
    await link(ops, target, "operation", op["operation_id"])
    await capture(ops, target)
    source = next(e for e in facts(ops, target) if e["source_kind"] == "operation_status")
    # Model a source becoming private; persisted evidence must not bypass its current read policy.
    ops.db.execute("UPDATE operations SET action='pm.private',target=? WHERE operation_id=?",
                   (json.dumps({"workspace_id": (await workspace(ops, OUTSIDER))["workspace_id"]}), op["operation_id"]))
    with pytest.raises(OperationError, match="PM_EVIDENCE_NOT_FOUND"):
        pm_evidence.get_evidence(ops, OWNER, target["workspace_id"], source["evidence_id"])
    listed = pm_evidence.list_evidence(ops, OWNER, target["workspace_id"])
    assert listed["coverage"]["inaccessible_sources"]
    latest = pm_evidence.latest_capture(ops, OWNER, **target)["capture"]
    assert source["evidence_id"] not in latest["evidence_ids"]
    assert [e["evidence_id"] for e in latest["evidence"]] == latest["evidence_ids"]
    assert all("document" not in e for e in latest["evidence"])


async def test_removed_links_preserve_history_without_reappearing_in_latest_capture(ops):
    target = await prepared(ops)
    op = await source_operation(ops)
    await link(ops, target, "operation", op["operation_id"])
    first = await capture(ops, target)
    saved = facts(ops, target)
    await link(ops, target, "operation", op["operation_id"], remove=True)
    await capture(ops, target)
    assert {e["source_kind"] for e in facts(ops, target)} == {"work_claim"}
    assert facts(ops, target, first["capture_id"]) == saved


async def test_membership_loss_rechecked_before_queued_capture_and_replay(ops):
    target = await prepared(ops)
    request = capture_request(ops, target)
    op, _ = ops.create(CONTRIBUTOR, **request)
    ops.db.execute("DELETE FROM pm_workspace_members WHERE workspace_id=? AND actor=?", (target["workspace_id"], CONTRIBUTOR.actor))
    result = await settle(ops, op, success=False)
    assert result["status"] == "failed" and result["error_code"] == "NOT_FOUND"
    with pytest.raises(OperationError, match="NOT_FOUND"):
        ops.create(CONTRIBUTOR, **request)
    assert ops.db.execute("SELECT COUNT(*) FROM pm_evidence_captures").fetchone()[0] == 0


@pytest.mark.parametrize("field,value", [("after", True), ("after", -1), ("limit", 0), ("limit", 101), ("limit", "1")])
async def test_pages_are_bounded_with_strict_integer_cursors(ops, field, value):
    target = await prepared(ops)
    for reader in (pm_evidence.list_projects, pm_evidence.list_work, pm_evidence.list_evidence):
        with pytest.raises(OperationError, match="INVALID_PARAMS"):
            reader(ops, OWNER, target["workspace_id"], **{field: value})


async def test_capture_rejects_asserted_facts_and_explicitly_marks_truncated_source_coverage(ops, monkeypatch):
    target = await prepared(ops)
    request = capture_request(ops, target)
    with pytest.raises(OperationError, match="INVALID_PARAMS"):
        ops.create(OWNER, **{**request, "params": {"verified": True, "facts": {"deployed": True}}})
    for number in range(3):
        await link(ops, target, "pull_request", f"example/product#{number + 1}")
    monkeypatch.setattr(pm_evidence, "MAX_LINKS", 2)
    await capture(ops, target)
    cap = pm_evidence.latest_capture(ops, OWNER, **target)["capture"]
    assert cap["coverage"]["truncated"] and cap["coverage"]["links_examined"] == 2
    assert cap["coverage"]["network_checked"] is False and cap["coverage"]["unavailable_sources"] == 2
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        ops.db.execute("DELETE FROM pm_evidence")


def integration_receipt(ops, operation_id, source_id):
    values = {"operation_id": operation_id, "seq": 1, "preview_id": "fixture-preview", "repository": "example/product",
              "pull_number": 1, "head_ref": "review", "source_kind": "execution", "source_id": source_id,
              "source_host": "mock-host", "location_class": "managed_clone", "pinned_sha": "1" * 40,
              "mode": "merge", "source_key": "fixture-source", "status": "delivered", "delivered_sha": "2" * 40,
              "delivered_at": 100, "actor": OWNER.actor, "created_at": 90, "updated_at": 100}
    ops.db.execute("INSERT INTO integration_receipts(" + ",".join(values) + ") VALUES(" + ",".join("?" for _ in values) + ")",
                   tuple(values.values()))


async def test_delivered_integration_is_pushed_not_merged_and_checks_both_source_operations(ops):
    target = await prepared(ops)
    source = await source_operation(ops)
    push = await source_operation(ops, action="integration.apply")
    await link(ops, target, "operation", push["operation_id"])
    integration_receipt(ops, push["operation_id"], source["operation_id"])
    await capture(ops, target)
    receipt = next(e for e in facts(ops, target) if e["source_kind"] == "integration_delivery")
    assert receipt["document"]["facts"]["delivery"] == "pushed"
    assert receipt["document"]["facts"]["pr_state"] == "unknown" and receipt["dod"] == "unassessed"
    private = await workspace(ops, OUTSIDER)
    ops.db.execute("UPDATE operations SET action='pm.private',target=? WHERE operation_id=?",
                   (json.dumps({"workspace_id": private["workspace_id"]}), source["operation_id"]))
    with pytest.raises(OperationError, match="PM_EVIDENCE_NOT_FOUND"):
        pm_evidence.get_evidence(ops, OWNER, target["workspace_id"], receipt["evidence_id"])
    await capture(ops, target)
    assert source["operation_id"] not in json.dumps(facts(ops, target))


def session(ops, sid="mock-session"):
    ref = f"mock-host/{sid}"
    ops.db.execute("""INSERT INTO sessions_observed(host,session_id,body,digest,provenance,api_access,first_seen_at,last_seen_at)
        VALUES(?,?,?,?,?,?,?,?)""", ("mock-host", sid, json.dumps({"streaming": None, "pending": None, "cwd": "/private/path"}),
                                    "a" * 64, "manual", "read_only", 10, 20))
    return ref


async def test_session_worktree_uses_explicit_bindings_and_unknown_activity_never_means_done(ops):
    target = await prepared(ops)
    ref = session(ops)
    await link(ops, target, "session", ref)
    await capture(ops, target)
    assert not any(e["source_kind"] == "resource_relation" for e in facts(ops, target))
    cursor = ops.journal.api_head()
    ops.db.execute("INSERT INTO session_worktree_bindings VALUES(?,?,?,NULL,?,?)", (ref, "wt_" + "a" * 32, cursor, cursor, "fixture-binding"))
    await capture(ops, target)
    evidence = facts(ops, target)
    observed = next(e for e in evidence if e["source_kind"] == "session_observation")
    relation = next(e for e in evidence if e["source_kind"] == "resource_relation")
    assert observed["document"]["facts"]["streaming"] is None and observed["dod"] == "unassessed"
    assert relation["document"]["facts"]["worktree_id"] == "wt_" + "a" * 32
    assert "/private/path" not in json.dumps(evidence)
    assert ops.db.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 0


async def test_foreign_private_pm_session_is_not_exposed_via_legacy_session_link(ops):
    target = await prepared(ops)
    discussion, *_ = await planning(ops)
    pm_runs.schema(ops.journal)
    run = create_run(ops, discussion)
    pm_runs.bind_session(ops.journal, run["run_id"], run["generation"], host="mock-host", session_id="pm-private-session",
                         incarnation="a" * 32, worktree_binding={"repository": "example/product", "commit": "b" * 40})
    ref = session(ops, "pm-private-session")
    await link(ops, target, "session", ref)
    await capture(ops, target)
    assert ref not in json.dumps(facts(ops, target))
    assert pm_evidence.latest_capture(ops, OWNER, **target)["capture"]["coverage"]["unavailable_sources"] == 1


async def test_binding_pagination_and_evidence_filter_do_not_duplicate_shared_facts(ops):
    target = await prepared(ops)
    pid = work_items._get_item(ops.db, target["work_item_id"])["project_id"]
    another = {**target, "work_item_id": await item(ops, pid, "Second item")}
    await bind_work(ops, another)
    first = pm_evidence.list_work(ops, OWNER, target["workspace_id"], limit=1)
    second = pm_evidence.list_work(ops, OWNER, target["workspace_id"], limit=1, after=first["next_cursor"])
    assert {first["bindings"][0]["work_item_id"], second["bindings"][0]["work_item_id"]} == {target["work_item_id"], another["work_item_id"]}
    assert second["next_cursor"] is None
    op = await source_operation(ops)
    await link(ops, target, "operation", op["operation_id"])
    await capture(ops, target)
    first = pm_evidence.list_evidence(ops, OWNER, **target, limit=1)
    second = pm_evidence.list_evidence(ops, OWNER, **target, limit=100, after=first["next_cursor"])
    ids = [e["evidence_id"] for e in first["evidence"] + second["evidence"]]
    assert len(ids) == len(set(ids)) == len(facts(ops, target))
    assert pm_evidence.list_evidence(ops, OWNER, **another)["evidence"] == []


@pytest.mark.parametrize("authority", ["invalid-json", "{}", "[]", '{"pm_workspace_id":"private"}'])
async def test_malformed_task_authority_is_unavailable_not_treated_as_legacy(ops, authority):
    target = await prepared(ops)
    source = task(ops)
    await link(ops, target, "task", source["task_id"])
    ops.db.execute("UPDATE tasks SET controller_authority=? WHERE task_id=?", (authority, source["task_id"]))
    await capture(ops, target)
    assert source["task_id"] not in json.dumps(facts(ops, target))
    assert pm_evidence.latest_capture(ops, OWNER, **target)["capture"]["coverage"]["unavailable_sources"] == 1


async def test_command_authority_is_checked_independently_and_rechecked_for_integration(ops):
    target = await prepared(ops)
    source = task(ops)
    ops.db.execute("UPDATE tasks SET state='accepted' WHERE task_id=?", (source["task_id"],))
    command, _ = ops.journal.command(source["task_id"], "send", "mock-session", {"message": "PRIVATE"}, "command-fixture")
    await link(ops, target, "task", source["task_id"])
    push = await source_operation(ops, action="integration.apply")
    integration_receipt(ops, push["operation_id"], command["command_id"])
    ops.db.execute("UPDATE integration_receipts SET source_kind='task_command' WHERE operation_id=?", (push["operation_id"],))
    await capture(ops, target)
    old = [e for e in facts(ops, target) if e["source_kind"] in {"integration_delivery", "execution_command", "resource_relation"}]
    assert len(old) == 3
    private = await workspace(ops, OUTSIDER)
    authority = controller_authority.binding(ops.db, OUTSIDER, pm_workspace_id=private["workspace_id"])
    ops.db.execute("UPDATE commands SET controller_authority=? WHERE command_id=?", (json.dumps(authority), command["command_id"]))
    for e in old:
        with pytest.raises(OperationError, match="PM_EVIDENCE_NOT_FOUND"):
            pm_evidence.get_evidence(ops, OWNER, target["workspace_id"], e["evidence_id"])
    await capture(ops, target)
    assert command["command_id"] not in json.dumps(facts(ops, target))
    assert pm_evidence.latest_capture(ops, OWNER, **target)["capture"]["coverage"]["unavailable_sources"] == 3


@pytest.mark.parametrize("kind", ["checkpoint", "checkpoint_run", "branch"])
async def test_unsupported_integration_sources_are_reported_without_publishing_source_ids(ops, kind):
    target = await prepared(ops)
    push = await source_operation(ops, action="integration.apply")
    await link(ops, target, "operation", push["operation_id"])
    integration_receipt(ops, push["operation_id"], "not-yet-projected-source")
    ops.db.execute("UPDATE integration_receipts SET source_kind=? WHERE operation_id=?", (kind, push["operation_id"]))
    await capture(ops, target)
    assert "not-yet-projected-source" not in json.dumps(facts(ops, target))
    assert pm_evidence.latest_capture(ops, OWNER, **target)["capture"]["coverage"]["unsupported_sources"] == 1


@pytest.mark.parametrize("kind", ["operation", "task", "command"])
async def test_legacy_sources_pointing_at_private_pm_session_do_not_expose_its_identity(ops, kind):
    target = await prepared(ops)
    discussion, *_ = await planning(ops)
    pm_runs.schema(ops.journal)
    run = create_run(ops, discussion)
    pm_runs.bind_session(ops.journal, run["run_id"], run["generation"], host="mock-host", session_id="private-session",
                         incarnation="a" * 32, worktree_binding={"repository": "example/product", "commit": "b" * 40})
    if kind == "operation":
        op = await source_operation(ops)
        ops.db.execute("UPDATE operations SET target=? WHERE operation_id=?",
                       (json.dumps({"host": "mock-host", "session_id": "private-session"}), op["operation_id"]))
        await link(ops, target, "operation", op["operation_id"])
    else:
        source = task(ops)
        await link(ops, target, "task", source["task_id"])
        if kind == "task":
            ops.db.execute("UPDATE tasks SET session_id='private-session' WHERE task_id=?", (source["task_id"],))
        else:
            ops.db.execute("UPDATE tasks SET state='accepted' WHERE task_id=?", (source["task_id"],))
            ops.journal.command(source["task_id"], "send", "private-session", {"message": "PRIVATE"}, "private-session-command")
    await capture(ops, target)
    assert "private-session" not in json.dumps(facts(ops, target))
    assert pm_evidence.latest_capture(ops, OWNER, **target)["capture"]["coverage"]["unavailable_sources"] >= 1


async def test_optional_github_sync_does_not_change_evidence_only_service_reads(ops):
    work = await prepared(ops)
    result = pm_evidence.list_projects(ops, OWNER, work['workspace_id'])
    assert len(result['bindings']) == 1
    assert 'github_sync' not in result
    assert ops.db.execute("SELECT 1 FROM sqlite_master WHERE name='board_github_sync'").fetchone() is None
