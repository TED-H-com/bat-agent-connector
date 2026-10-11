"""Fixed engineering packages, real temporary Git reads, and no execution effects."""
from __future__ import annotations

import copy
import json
import sqlite3
import uuid
from pathlib import Path

import pytest

from bat_agent_connector import (
    pm_discussions,
    pm_evidence,
    pm_plans,
    pm_workspace,
    repository_sync,
    work_items,
)
from bat_agent_connector import pm_work_packages as packages
from bat_agent_connector.api_auth import Principal
from bat_agent_connector.operations import OpContext, OperationError, Uncertain
from tests.operation_helpers import settle_operations
from tests.test_checkpoints import bat_writes, git, snapshot
from tests.test_pm_plan_operations import OWNER, adopt, draft
from tests.test_pm_plan_operations import act as thread_action
from tests.test_pm_plan_operations import target as thread_target
from tests.test_pm_spec_sources import FILES, carrier_state, prepare
from tests.test_pm_spec_sources import read as read_specs
from tests.test_pm_worktree import pm as pm_fixture
from tests.test_pm_worktree import resume
from tests.test_repository_sync import REQUEST
from tests.test_repository_sync import world as repository_world

world = repository_world
pm = pm_fixture
READER = Principal("package-reader", frozenset({"observe", "manage"}))
CONTRIBUTOR = Principal("package-author", frozenset({"observe", "manage"}))
OUTSIDER = Principal("unrelated-reader", frozenset({"observe", "manage"}))


async def action(w, name, target=None, params=None, pre=None, *, who=OWNER):
    ops = w["d"].ops
    op, _ = ops.create(who, action=name, target=target or {}, params=params or {}, preconditions=pre or {},
                       idempotency_key=uuid.uuid4().hex)
    await settle_operations(ops)
    result = ops.get(op["operation_id"])
    assert result["status"] == "succeeded", result
    return result["result"]


async def member(w, actor, role):
    current = pm_workspace.get_workspace(w["d"].ops.db, OWNER, w["workspace_id"])
    return await action(w, "pm.workspace.member.set", {"workspace_id": w["workspace_id"]},
                        {"actor": actor, "role": role}, {"expected_version": current["version"]})


async def associate(w, revision_id=None):
    ops = w["d"].ops
    old = pm_evidence._binding(ops.db, **w["target"])
    return await action(w, "pm.work.bind", w["target"], {"rationale": "Explicit fixed requirement association",
        "plan_revision_id": revision_id or w["revision"]["revision_id"], "requirement_ids": ["member.login"]},
        {"expected_binding_id": old["binding_id"] if old else None,
         "expected_work_version": work_items._get_item(ops.db, w["target"]["work_item_id"])["version"]})


@pytest.fixture
async def ready(pm):
    w, ops = pm, pm["d"].ops
    version = ops.db.execute("PRAGMA user_version").fetchone()[0]
    packages.install(ops)
    packages.install(ops)
    assert ops.db.execute("PRAGMA user_version").fetchone()[0] == version
    await prepare(w)
    scope = await thread_action(w, "pm.input.record", {"slot": "scope", "value": "Members can log in",
        "status": "user_confirmed", "source_ref": {"kind": "statement", "ref": "Selected owner direction"}})
    acceptance = await thread_action(w, "pm.input.record", {"slot": "quality", "value": "Valid credentials allow access; an invalid password is denied",
        "status": "user_confirmed", "source_ref": {"kind": "statement", "ref": "Explicit acceptance criteria"}})
    await thread_action(w, "pm.thread.plan", {"selected_input_ids": [scope["input_id"], acceptance["input_id"]],
                                             "rationale": "Plan only the selected login behavior"})
    w["requirements"] = [{"id": "member.login", "source_input_id": scope["input_id"], "acceptance_input_ids": [acceptance["input_id"]]}]
    w["revision"] = await draft(w, requirements=w["requirements"])
    adopted = await adopt(w)
    assert adopted["status"] == "succeeded", adopted
    pid = (await action(w, "project.create", params={"name": "Synthetic membership", "repositories": ["o/r"]}))["project_id"]
    work = (await action(w, "work_item.create", {"project_id": pid}, {"title": "Implement login",
        "goal": "Account access", "acceptance": "Verify allowed and denied access"}))["work_item_id"]
    wid = w["workspace_id"]
    await action(w, "pm.project.bind", {"workspace_id": wid, "project_id": pid}, {"rationale": "Manage this explicit project"},
                 {"expected_workspace_version": pm_workspace.get_workspace(ops.db, OWNER, wid)["version"],
                  "expected_project_version": work_items._get_project(ops.db, pid)["version"]})
    w["target"] = {"workspace_id": wid, "work_item_id": work}
    await associate(w)
    specs = await read_specs(w, paths=["openspec/specs/identity/login/spec.md", "openspec/changes/recovery/tasks.md"])
    refs = [{"repository": "o/r", **{key: value[key] for key in ("commit_sha", "path", "source_id", "sha256")}}
            for value in specs["documents"]]
    spec = next(value for value in refs if value["path"].endswith("spec.md"))["source_id"]
    w["params"] = {"title": "Login engineering package", "requirements": ["member.login"], "source_refs": refs,
        "code_source": w["selection"], "rationale": "Prepare exact adopted behavior for engineering review",
        "design": {"status": "described", "text": "Keep the existing identity module; server validates credentials and returns access or a generic denial.", "source_ids": [spec]},
        "tasks": [{"key": "identity.login", "description": "Implement and test credential validation",
                   "requirement_ids": ["member.login"], "depends_on": [], "source_ids": [spec]}],
        "verification": [{"key": "verify.login", "requirement_id": "member.login", "acceptance_input_ids": [acceptance["input_id"]],
            "method": "Exercise valid credentials and wrong passwords", "expected_evidence": "Fixed-head automated allow/deny test results", "source_ids": [spec]}],
        "delivery": {"repository": "o/r", "repository_id": w["selection"]["repository_id"], "base_ref": "refs/heads/main", "goal": "pull_request",
            **{key: {"status": "not_applicable", "text": "This slice prepares a PR only; deployment is a separate explicit decision."}
               for key in ("migration", "rollout", "rollback", "observation")}}}
    return w


def request(w, *, previous=None, **params):
    ops, wid = w["d"].ops, w["workspace_id"]
    return {"action": packages.ACTION, "target": w["target"], "params": {**copy.deepcopy(w["params"]), **params,
            **({"previous_package_id": previous} if previous is not None else {})},
        "preconditions": {"expected_binding_id": pm_evidence._binding(ops.db, **w["target"])["binding_id"],
            "expected_work_fingerprint": work_items.fingerprint(work_items._get_item(ops.db, w["target"]["work_item_id"])),
            "expected_scope_revision": pm_plans.accepted_plan(ops, OWNER, wid)["plan"]["adoption"]["scope_revision"],
            "expected_previous_package_id": previous}, "idempotency_key": uuid.uuid4().hex}


async def create(w, *, who=OWNER, **params):
    op, _ = w["d"].ops.create(who, **request(w, **params))
    await settle_operations(w["d"].ops)
    done = w["d"].ops.get(op["operation_id"])
    assert done["status"] == "succeeded", done
    return done


def read(w, operation, who=OWNER):
    return packages.get_package(w["d"].ops, who, w["workspace_id"], operation["result"]["package_id"])["package"]


def readiness(w, operation):
    return packages.readiness(w["d"].ops, OWNER, w["workspace_id"], operation["result"]["package_id"])


async def test_fixed_sources_requirements_and_verification_preserve_human_work_and_never_dispatch(ready, mock):
    w, ops = ready, ready["d"].ops
    before, carrier = snapshot(w["human"]), carrier_state(w["plan"])
    work = work_items.work_item_get(ops.db, w["target"]["work_item_id"])
    counts = {table: ops.db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] for table in ("tasks", "artifacts", "pm_runs", "pm_messages")}
    envelope = request(w)
    operation, _ = ops.create(OWNER, **envelope)
    await settle_operations(ops)
    operation = ops.get(operation["operation_id"])
    assert operation["status"] == "succeeded", operation
    package = read(w, operation)
    document = package["document"]
    assert operation["params"] == envelope["params"] and operation["preconditions"] == envelope["preconditions"]
    assert document["context"]["work_ref"]["work_fingerprint"] == work_items.fingerprint(work_items._get_item(ops.db, w["target"]["work_item_id"]))
    assert document["context"]["plan_ref"]["revision_id"] == w["revision"]["revision_id"]
    assert document["context"]["plan_ref"]["plan_commit_sha"] != document["code_source"]["source_sha"] == w["sha"]
    assert document["requirements"][0]["text"] == "Members can log in"
    assert {value["source_ref"]["ref"] for value in document["source_inputs"]} == {"Selected owner direction", "Explicit acceptance criteria"}
    assert document["verification"][0]["verification"] == "not_run"
    assert not document["execution_authorized"] and document["execution_spec"] == "not_assigned"
    by_path = {value["path"]: value for value in document["sources"]["documents"]}
    assert by_path["openspec/specs/identity/login/spec.md"]["content"] == FILES["openspec/specs/identity/login/spec.md"]
    reports = by_path["openspec/changes/recovery/tasks.md"]["observations"]["task_reports"]
    assert reports[0]["reported_checked"] and not any(row["counts_as_done"] for row in reports)
    assert document["sources"]["spec_read"]["schema"]["validation"] == "not_run"
    status = readiness(w, operation)
    assert status["status"] == "ready" and status["ready_for"] == "engineering_handoff_review"
    assert not status["execution_available"] and not status["execution_authorized"] and status["coverage"]["quality"] == "unassessed"
    assert carrier_state(w["plan"]) == carrier and snapshot(w["human"]) == before and not bat_writes(mock)
    assert work_items.work_item_get(ops.db, w["target"]["work_item_id"]) == work
    assert all(ops.db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == count for table, count in counts.items())
    assert all(method == "GET" for method, _, _ in w["gh"].requests)
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        ops.db.execute("UPDATE pm_work_packages SET document='{}'")
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        ops.db.execute("DELETE FROM pm_work_packages")


async def test_exact_idempotency_replays_receipt_without_source_read_or_authority_borrowing(ready):
    w, ops = ready, ready["d"].ops
    req = request(w)
    original, _ = ops.create(OWNER, **req)
    await settle_operations(ops)
    original = ops.get(original["operation_id"])
    count = len(w["runner"].phases)
    w["state"]["sha"] = "a" * 40
    replay, created = ops.create(OWNER, **req)
    assert not created and replay["operation_id"] == original["operation_id"]
    assert await packages._run(OpContext(ops, original)) == original["result"]
    assert len(w["runner"].phases) == count
    assert ops.db.execute("SELECT COUNT(*) FROM pm_work_packages").fetchone()[0] == 1
    ops.db.execute("DELETE FROM pm_workspace_members WHERE workspace_id=? AND actor=?", (w["workspace_id"], OWNER.actor))
    assert await packages._run(OpContext(ops, original)) == original["result"]
    assert not packages.operation_visible(ops.db, OWNER, original)
    with pytest.raises(OperationError):
        read(w, original)
    with pytest.raises(OperationError):
        ops.create(OWNER, **req)


async def test_local_effect_reply_loss_recovers_without_rereading_or_creating_second_package(ready, monkeypatch):
    w, ops = ready, ready["d"].ops
    original_effect = OpContext.effect
    armed = True
    def interrupted(ctx, name, fn, **kwargs):
        nonlocal armed
        result = original_effect(ctx, name, fn, **kwargs)
        if name == "pm.package.record" and armed:
            armed = False
            raise Uncertain(name, "Simulated lost local receipt response")
        return result
    monkeypatch.setattr(OpContext, "effect", interrupted)
    operation, _ = ops.create(OWNER, **request(w))
    await settle_operations(ops)
    operation = ops.get(operation["operation_id"])
    assert operation["status"] == "uncertain", operation
    count = len(w["runner"].phases)
    ops.db.execute("DELETE FROM pm_workspace_members WHERE workspace_id=? AND actor=?", (w["workspace_id"], OWNER.actor))
    recovered = await resume(w, operation)
    assert recovered["status"] == "succeeded", recovered
    assert len(w["runner"].phases) == count and ops.db.execute("SELECT COUNT(*) FROM pm_work_packages").fetchone()[0] == 1
    assert not packages.operation_visible(ops.db, OWNER, recovered)


@pytest.mark.parametrize("boundary", ["params", "target", "preconditions"])
async def test_exact_envelope_rejects_injected_execution_or_approval_fields(ready, boundary):
    w, ops = ready, ready["d"].ops
    req = request(w)
    req[boundary]["execute"] = True
    count = len(w["runner"].phases)
    with pytest.raises(OperationError, match="INVALID_PARAMS"):
        ops.create(OWNER, **req)
    assert len(w["runner"].phases) == count


@pytest.mark.parametrize("change", ["requirement", "acceptance", "task_cycle", "path", "receipt", "source_digest"])
async def test_sources_and_requirement_ids_cannot_be_invented_or_substituted(ready, change):
    w, ops = ready, ready["d"].ops
    req = request(w)
    params = req["params"]
    if change == "requirement":
        params["requirements"] = ["referral.discount15"]
    elif change == "acceptance":
        params["verification"][0]["acceptance_input_ids"] = ["pmi_" + "a" * 32]
    elif change == "task_cycle":
        params["tasks"][0]["depends_on"] = ["identity.login"]
    elif change == "path":
        params["source_refs"][0]["path"] = "../../unpublished-secret"
    elif change == "receipt":
        params["sources"] = {"verified": True}
    else:
        params["source_refs"][0]["sha256"] = "a" * 64
    if change != "source_digest":
        with pytest.raises(OperationError):
            ops.create(OWNER, **req)
    else:
        op, _ = ops.create(OWNER, **req)
        await settle_operations(ops)
        failed = ops.get(op["operation_id"])
        assert failed["status"] == "failed" and failed["error_code"] == "PM_PACKAGE_SOURCE_CHANGED", failed
    assert not ops.db.execute("SELECT 1 FROM pm_work_packages").fetchone()


@pytest.mark.parametrize("stop", ["cancel", "revoke", "work_changed"])
async def test_guard_after_fixed_source_read_prevents_stale_or_unauthorized_record(ready, stop):
    w, ops = ready, ready["d"].ops
    op, _ = ops.create(OWNER, **request(w))
    def interrupt(_):
        if stop == "cancel":
            ops.cancel(OWNER, op["operation_id"])
        elif stop == "revoke":
            ops.db.execute("UPDATE pm_workspace_members SET role='reader' WHERE workspace_id=? AND actor=?", (w["workspace_id"], OWNER.actor))
        else:
            ops.db.execute("UPDATE work_items SET request='Changed content',version=version+1 WHERE work_item_id=?", (w["target"]["work_item_id"],))
    w["runner"].after["read.spec-sources"] = interrupt
    await settle_operations(ops)
    done = ops.get(op["operation_id"])
    assert done["status"] == ("cancelled" if stop == "cancel" else "failed"), done
    assert not ops.db.execute("SELECT 1 FROM pm_work_packages").fetchone()


async def test_current_membership_allows_contributor_creation_reader_read_and_blocks_outsider(ready):
    w, ops = ready, ready["d"].ops
    await member(w, READER.actor, "reader")
    await member(w, CONTRIBUTOR.actor, "contributor")
    done = await create(w, who=CONTRIBUTOR)
    assert read(w, done, READER)["actor"] == CONTRIBUTOR.actor
    assert packages.operation_visible(ops.db, READER, done)
    for who in (READER, OUTSIDER, Principal(CONTRIBUTOR.actor, frozenset({"manage"}))):
        with pytest.raises(OperationError):
            ops.create(who, **request(w, previous=done["result"]["package_id"]))
    for who in (OUTSIDER, Principal(CONTRIBUTOR.actor, frozenset({"manage"}))):
        with pytest.raises(OperationError):
            read(w, done, who)
    await member(w, READER.actor, None)
    assert not packages.operation_visible(ops.db, READER, done)


async def test_changes_preserve_fixed_history_and_require_new_explicit_package(ready):
    w, ops = ready, ready["d"].ops
    one = await create(w)
    frozen = read(w, one)
    item = work_items._get_item(ops.db, w["target"]["work_item_id"])
    await action(w, "work_item.update", {"work_item_id": item["work_item_id"]}, {"request": "Add a precise failure scenario"}, {"expected_version": item["version"]})
    assert read(w, one) == frozen and readiness(w, one)["status"] == "blocked"
    with pytest.raises(OperationError, match="PM_PACKAGE_VERSION_CONFLICT"):
        ops.create(OWNER, **request(w))
    two = await create(w, previous=one["result"]["package_id"], rationale="Explicit revised engineering approach")
    assert read(w, one) == frozen and read(w, two)["previous_package_id"] == one["result"]["package_id"]
    assert readiness(w, two)["status"] == "ready"
    await associate(w)
    assert readiness(w, two)["status"] == "blocked" and read(w, two)


async def test_parallel_package_requests_never_fork_the_latest_chain(ready):
    w, ops = ready, ready["d"].ops
    first, _ = ops.create(OWNER, **request(w))
    second, _ = ops.create(OWNER, **request(w, rationale="Competing engineering draft"))
    await settle_operations(ops)
    results = [ops.get(value["operation_id"]) for value in (first, second)]
    assert sorted(value["status"] for value in results) == ["failed", "succeeded"], results
    failed = next(value for value in results if value["status"] == "failed")
    assert failed["error_code"] == "PM_PACKAGE_VERSION_CONFLICT"
    assert ops.db.execute("SELECT COUNT(*) FROM pm_work_packages").fetchone()[0] == 1


async def test_unknown_acceptance_design_delivery_and_dependencies_are_blocked_not_failed_quality(ready):
    w = ready
    requirements = copy.deepcopy(w["requirements"])
    requirements.append({"id": "identity.foundation", "source_input_id": requirements[0]["source_input_id"]})
    requirements[0]["depends_on"] = ["identity.foundation"]
    revision = await draft(w, requirements=requirements)
    assert (await adopt(w, revision))["status"] == "succeeded"
    await associate(w, revision["revision_id"])
    params = copy.deepcopy(w["params"])
    params["design"]["status"] = "unresolved"
    params["verification"][0]["acceptance_input_ids"] = []
    params["delivery"]["rollback"]["status"] = "unresolved"
    done = await create(w, **params, unresolved=["Identity foundation has no evaluated result"])
    status = readiness(w, done)
    blocked = {value["check"]: value for value in status["checks"] if value["status"] == "blocked"}
    assert {"design", "delivery_plan", "unresolved", "acceptance:member.login", "dependency:member.login"} <= set(blocked)
    assert blocked["dependency:member.login"]["assessment"] == "unknown"
    assert status["coverage"]["quality"] == "unassessed" and not status["execution_authorized"]


async def test_new_adopted_scope_blocks_old_package_but_never_rebinds_its_sources(ready):
    w = ready
    one = await create(w)
    original = read(w, one)
    revision = await draft(w, requirements=w["requirements"], markdown="# Changed scope narrative\n\nNew direction requires review.\n")
    assert (await adopt(w, revision))["status"] == "succeeded"
    assert read(w, one) == original
    assert next(value for value in readiness(w, one)["checks"] if value["check"] == "scope")["status"] == "blocked"
    with pytest.raises(OperationError, match="PM_PACKAGE_SCOPE_CHANGED"):
        w["d"].ops.create(OWNER, **request(w, previous=one["result"]["package_id"]))


async def test_published_code_and_spec_commits_remain_distinct_and_no_dirty_bytes_enter_package(ready):
    w = ready
    path = "openspec/specs/identity/login/spec.md"
    (w["human"] / path).write_text("New published specification\n")
    git(w["human"], "add", path)
    git(w["human"], "commit", "-qm", "New published code revision")
    git(w["human"], "push", str(w["remote"]), "main")
    w["state"]["sha"] = git(w["human"], "rev-parse", "HEAD")
    pv = await repository_sync.source_preview(w["d"].ops, REQUEST)
    selection = {**pv["target"], **{key: pv[key] for key in ("source_ref", "source_sha", "repository_id", "binding_digest")}}
    (w["human"] / path).write_text("Unpublished human text\n")
    Path(w["plan"]["worktree_path"], path).write_text("Unpublished PM text\n")
    before, carrier = snapshot(w["human"]), carrier_state(w["plan"])
    done = await create(w, code_source=selection)
    document = read(w, done)["document"]
    assert document["code_source"]["source_sha"] != document["sources"]["spec_read"]["commit_sha"] == w["sha"]
    assert next(value for value in document["sources"]["documents"] if value["path"] == path)["content"] == FILES[path]
    assert next(value for value in readiness(w, done)["checks"] if value["check"] == "spec_code_alignment")["status"] == "blocked"
    assert snapshot(w["human"]) == before and carrier_state(w["plan"]) == carrier


async def test_sources_optional_for_proportionate_work_but_workspace_strings_never_grant_repository_authority(ready):
    w = ready
    params = copy.deepcopy(w["params"])
    params["source_refs"] = []
    for value in (params["design"], *params["tasks"], *params["verification"]):
        value["source_ids"] = []
    params["design"] = {"status": "not_applicable", "text": "A small test-only change needs no separate architecture document", "source_ids": []}
    done = await create(w, **params)
    assert read(w, done)["document"]["sources"]["spec_read"] is None
    assert readiness(w, done)["status"] == "ready"
    params["code_source"]["repository"] = str(w["human"])
    with pytest.raises(OperationError):
        w["d"].ops.create(OWNER, **request(w, previous=done["result"]["package_id"], **params))


async def test_adoption_does_not_upgrade_unconfirmed_acceptance_into_ready(ready):
    w = ready
    candidate = await thread_action(w, "pm.input.record", {"slot": "quality", "value": "A proposed latency limit needs a decision",
        "status": "agent_assumption", "source_ref": {"kind": "statement", "ref": "Engineering suggestion, not a commitment"}})
    requirement = {**w["requirements"][0], "acceptance_input_ids": [candidate["input_id"]]}
    current = pm_discussions.get_thread(w["d"].ops, OWNER, **thread_target(w))
    await thread_action(w, "pm.thread.plan.revise", {"selected_input_ids": [requirement["source_input_id"], candidate["input_id"]],
        "rationale": "Retain the candidate explicitly while deciding its applicability"},
        pre={"expected_version": current["thread"]["version"], "expected_draft_id": current["draft"]["draft_id"]})
    revision = await draft(w, requirements=[requirement])
    assert (await adopt(w, revision))["status"] == "succeeded"
    await associate(w, revision["revision_id"])
    verification = [{**w["params"]["verification"][0], "acceptance_input_ids": [candidate["input_id"]]}]
    done = await create(w, verification=verification)
    assert next(value for value in readiness(w, done)["checks"] if value["check"] == "source_status:member.login")["status"] == "blocked"
    document = read(w, done)["document"]
    assert document["requirements"][0]["acceptance"][0]["source_status"] == "agent_assumption"
    assert next(value for value in document["source_inputs"] if value["input_id"] == candidate["input_id"])["confirmed_by"] is None


async def test_changed_published_preview_fails_before_reading_spec_or_creating_package(ready):
    w, ops = ready, ready["d"].ops
    op, _ = ops.create(OWNER, **request(w))
    count = len(w["runner"].phases)
    w["state"]["sha"] = "f" * 40
    await settle_operations(ops)
    done = ops.get(op["operation_id"])
    assert done["status"] == "failed" and done["error_code"] == "PM_PACKAGE_CODE_SOURCE_CHANGED", done
    assert len(w["runner"].phases) == count and not ops.db.execute("SELECT 1 FROM pm_work_packages").fetchone()


async def add_task_evidence(w):
    ops = w["d"].ops
    task = ops.journal.submit(project="synthetic", host="h1", workspace="ws-1", original_words="Synthetic source task",
                              idempotency_key=uuid.uuid4().hex)
    await action(w, "work_item.link", {"work_item_id": w["target"]["work_item_id"]}, {"kind": "task", "ref": task["task_id"]})
    captured = await action(w, "pm.evidence.capture", w["target"], pre={"expected_binding_id": pm_evidence._binding(ops.db, **w["target"])["binding_id"]})
    facts = pm_evidence.get_capture(ops, OWNER, w["workspace_id"], captured["capture_id"])["capture"]
    evidence = [pm_evidence.get_evidence(ops, OWNER, w["workspace_id"], eid)["evidence"] for eid in facts["evidence_ids"]]
    ref = next(value for value in evidence if value["source_kind"] == "execution_state")
    return task, {key: ref[key] for key in ("evidence_id", "fact_digest")}


async def test_withdrawn_original_source_hides_latest_narrative_and_original_operation(ready, monkeypatch):
    w, ops = ready, ready["d"].ops
    task, evidence = await add_task_evidence(w)
    one = await create(w, evidence_refs=[evidence])
    two = await create(w, previous=one["result"]["package_id"], evidence_refs=[], rationale="A later narrative may quote original analysis")
    original = pm_workspace.operation_visible
    def integration_hook(db, principal, op):
        return original(db, principal, op) and (op["action"] != packages.ACTION or packages.operation_visible(db, principal, op))
    monkeypatch.setattr(pm_workspace, "operation_visible", integration_hook)
    assert ops.get(two["operation_id"], principal=OWNER)
    assert packages.operation_visible(ops.db, OWNER, two)
    ops.db.execute("UPDATE tasks SET controller_authority='{}' WHERE task_id=?", (task["task_id"],))
    for operation in (one, two):
        assert not packages.operation_visible(ops.db, OWNER, operation)
        with pytest.raises(OperationError):
            read(w, operation)
        with pytest.raises(OperationError):
            ops.get(operation["operation_id"], principal=OWNER)
    page = packages.list_packages(ops, OWNER, w["workspace_id"], limit=1)
    assert page["packages"] == [] and page["next_cursor"] and page["coverage"]["visibility_filtered"]
    tail = packages.list_packages(ops, OWNER, w["workspace_id"], after=page["next_cursor"])
    assert tail["packages"] == [] and tail["next_cursor"] is None and tail["coverage"]["scanned"] == 1
    assert await packages._run(OpContext(ops, one)) == one["result"]


async def test_optional_dod_is_fixed_separate_from_requirement_acceptance(ready):
    w = ready
    dod = await action(w, "pm.dod.revise", {"workspace_id": w["workspace_id"]},
        {"plan_revision_id": w["revision"]["revision_id"], "criteria": [{"id": "quality.tests", "text": "Relevant tests pass"}],
         "rationale": "Explicit shared quality baseline"}, {"expected_revision_id": None})
    done = await create(w, dod_revision_id=dod["revision_id"])
    document = read(w, done)["document"]
    assert document["dod"]["acceptance_criteria"] == "separate_requirement_data"
    assert document["dod"]["evaluation"] == "unassessed"
    assert document["requirements"][0]["acceptance"][0]["text"] != document["dod"]["criteria"][0]["text"]
    assert document["context"]["dod_ref"]["revision_id"] == dod["revision_id"]


async def test_wire_budget_keeps_whole_utf8_packages_and_cursor_never_skips(ready, monkeypatch):
    w, ops = ready, ready["d"].ops
    params = {"design": {**w["params"]["design"], "text": "漢字" * 64_000}}
    created, previous = [], None
    for _ in range(6):
        done = await create(w, previous=previous, **params)
        created.append(done["result"]["package_id"])
        previous = created[-1]
    page = packages.list_packages(ops, OWNER, w["workspace_id"])
    assert page["coverage"]["byte_limited"] and 0 < len(page["packages"]) < len(created)
    assert len(json.dumps(page, ensure_ascii=False).encode()) <= 2_000_000
    tail = packages.list_packages(ops, OWNER, w["workspace_id"], after=page["next_cursor"])
    assert [value["package_id"] for value in page["packages"] + tail["packages"]] == created
    assert tail["next_cursor"] is None and all(value["document"]["design"]["text"] == params["design"]["text"] for value in page["packages"])
    monkeypatch.setattr(packages, "MAX_READ_BYTES", 1000)
    with pytest.raises(OperationError) as error:
        packages.list_packages(ops, OWNER, w["workspace_id"])
    assert error.value.status == 413
    with pytest.raises(OperationError) as error:
        packages.get_package(ops, OWNER, w["workspace_id"], created[0])
    assert error.value.status == 413


async def test_readiness_checks_config_and_project_membership_without_network_or_rewriting_history(ready):
    w, ops = ready, ready["d"].ops
    done = await create(w)
    before = read(w, done)
    count, requests = len(w["runner"].phases), len(w["gh"].requests)
    project = work_items._get_project(ops.db, before["document"]["context"]["work_ref"]["project_id"])
    await action(w, "project.update", {"project_id": project["project_id"]}, {"repositories": []}, {"expected_version": project["version"]})
    assert readiness(w, done)["status"] == "blocked"
    assert read(w, done) == before and len(w["runner"].phases) == count and len(w["gh"].requests) == requests
