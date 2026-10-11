"""Public plan adoption with real owned Git, cancellation and durable recovery."""
from __future__ import annotations

import json
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

from bat_agent_connector import pm_discussions, pm_plan_operations, pm_plans, pm_runs, pm_worktree
from bat_agent_connector.api_auth import Principal
from bat_agent_connector.operations import AmbiguousOutcome, OpContext, OperationError, Uncertain
from tests.operation_helpers import settle_operations
from tests.test_checkpoints import bat_writes, git, snapshot
from tests.test_pm_worktree import pm as pm_fixture
from tests.test_pm_worktree import resume
from tests.test_repository_sync import world as repository_world

world = repository_world
pm = pm_fixture
OWNER = Principal("pm-owner", frozenset({"observe", "manage", "start"}))


def target(w):
    row = w["d"].ops.db.execute("SELECT thread_id FROM pm_threads WHERE workspace_id=?", (w["workspace_id"],)).fetchone()
    return {"workspace_id": w["workspace_id"], "thread_id": row[0]}


def version(w):
    return pm_discussions.get_thread(w["d"].ops, OWNER, **target(w))["thread"]["version"]


async def act(w, action, params, *, pre=None):
    ops = w["d"].ops
    op, _ = ops.create(OWNER, action=action, target=target(w), params=params,
        preconditions=pre or {"expected_version": version(w)}, idempotency_key=uuid.uuid4().hex)
    await settle_operations(ops)
    done = ops.get(op["operation_id"])
    assert done["status"] == "succeeded", done
    return done["result"]


async def draft(w, **params):
    current = pm_plans.head(w["d"].ops.db, w["workspace_id"])
    return await act(w, "pm.plan.draft", {"previous_revision_id": current["latest_revision_id"], **params},
                     pre={"expected_version": version(w), "expected_plan_version": current["version"]})


@pytest.fixture
async def ready(pm):
    w = pm
    pm_plan_operations.install(w["d"].ops)
    statement = await act(w, "pm.input.record", {"slot": "scope", "value": "Members can log in",
        "status": "user_confirmed", "source_ref": {"kind": "statement", "ref": "Explicit owner direction"}})
    await act(w, "pm.thread.plan", {"selected_input_ids": [statement["input_id"]], "rationale": "Prepare this selected direction"})
    w["revision"] = await draft(w)
    await act(w, "pm.run.prepare", {"purpose": "Prepare sourced planning context", "repository_binding": w["selection"]})
    w["plan"] = pm_runs.workspace_worktree(w["d"].ops.db, w["workspace_id"])
    return w


def adoption(w, revision=None, *, key=None):
    ops = w["d"].ops
    current = pm_plans.head(ops.db, w["workspace_id"])
    return ops.create(OWNER, action="pm.plan.adopt",
        target={"workspace_id": w["workspace_id"], "revision_id": (revision or w["revision"])["revision_id"]},
        params={"rationale": "Adopt this exact sourced proposal"},
        preconditions={"expected_accepted_revision_id": current["accepted_revision_id"]},
        idempotency_key=key or uuid.uuid4().hex)[0]


async def adopt(w, revision=None):
    op = adoption(w, revision)
    await settle_operations(w["d"].ops)
    return w["d"].ops.get(op["operation_id"])


def run(w, op):
    return pm_runs.get_by_operation(w["d"].ops.db, op["operation_id"])


def receipt(w, op):
    row = w["d"].ops.db.execute("SELECT receipt FROM pm_plan_git_receipts WHERE operation_id=?", (op["operation_id"],)).fetchone()
    return json.loads(row[0]) if row else None


def interrupt_host(w, monkeypatch, point="branch"):
    source = pm_worktree._host_source
    armed = {"value": True}
    anchors = {
        "intent": "    for kind, sha, content in objects:\n",
        "objects": "    for relative, content in files.items():\n",
        "files": '    if git(req["clone_path"], "rev-parse", "--verify", rev_ref(proposed), optional=True) is None:\n',
        "branch": "    # Only this proven PM-owned index is synchronized; objects were built without\n",
        "index": '    proposed["index_after"] = rev_index(req)\n',
    }
    def interrupted_source():
        script = source()
        if armed["value"]:
            script = script.replace(anchors[point], '    raise ValueError("PM_HOST_UNCERTAIN")\n' + anchors[point])
        return script
    monkeypatch.setattr(pm_worktree, "_host_source", interrupted_source)
    w["runner"].before["pm-revision"] = lambda _: armed.update(value=False)


async def test_explicit_plan_adoption_commits_scoped_files_and_decision_without_dispatch(ready, mock):
    w = ready
    before = snapshot(w["human"])
    done = await adopt(w)
    assert done["status"] == "succeeded", done
    result = done["result"]
    proof = receipt(w, done)
    files = pm_plans.revision_files(w["d"].ops.db, w["workspace_id"], w["revision"]["revision_id"])
    path = w["plan"]["worktree_path"]
    assert git(path, "rev-parse", "HEAD") == result["commit_sha"] == proof["commit_sha"]
    assert git(path, "rev-parse", "HEAD^") == w["sha"]
    assert set(git(path, "diff-tree", "--no-commit-id", "--name-only", "-r", "HEAD").splitlines()) == set(files["files"])
    assert result["status"] == "adopted" and result["decision_id"] and not result["execution_authorized"]
    assert pm_plans.head(w["d"].ops.db, w["workspace_id"])["accepted_revision_id"] == w["revision"]["revision_id"]
    assert run(w, done)["state"] == "completed_service"
    thread = pm_discussions.get_thread(w["d"].ops, OWNER, **target(w))
    assert all(message["role"] == "user" for message in thread["messages"])
    assert not w["d"].ops.db.execute("SELECT 1 FROM tasks").fetchone()
    assert not w["d"].ops.db.execute("SELECT 1 FROM work_items").fetchone()
    assert not bat_writes(mock) and snapshot(w["human"]) == before
    assert git(path, "status", "--porcelain").startswith("?? docs/pm/drafts/")


@pytest.mark.parametrize("point", ["intent", "objects", "files", "branch", "index"])
async def test_cancel_after_partial_git_keeps_original_writer_and_unresolved_step(ready, monkeypatch, point):
    w = ready
    interrupt_host(w, monkeypatch, point)
    first = await adopt(w)
    assert first["status"] == "uncertain", first
    original = run(w, first)
    w["d"].ops.cancel(OWNER, first["operation_id"])
    recovered = await resume(w, first)
    assert recovered["status"] == "uncertain", recovered
    assert run(w, first)["state"] not in pm_runs.TERMINAL
    assert pm_runs.require_writer(w["d"].journal, original["run_id"], original["generation"])
    assert next(step for step in recovered["steps"] if step["name"] == "pm.revision.commit")["status"] == "uncertain"
    assert not receipt(w, first)
    assert not pm_plans.head(w["d"].ops.db, w["workspace_id"])["accepted_revision_id"]
    assert w["runner"].phases.count("pm-revision") == 1


def revoke(w):
    w["d"].ops.db.execute("UPDATE pm_workspace_members SET role='reader' WHERE workspace_id=? AND actor=?",
                         (w["workspace_id"], OWNER.actor))


def lose(_):
    raise AmbiguousOutcome("Lost original Git acknowledgement")


async def test_lost_ack_recovers_exact_commit_without_repeating_git(ready):
    w = ready
    w["runner"].after["pm-revision"] = lose
    first = await adopt(w)
    assert first["status"] == "uncertain", first
    head = git(w["plan"]["worktree_path"], "rev-parse", "HEAD")
    assert not receipt(w, first) and run(w, first)["state"] not in pm_runs.TERMINAL
    recovered = await resume(w, first)
    assert recovered["status"] == "succeeded", recovered
    assert recovered["result"]["commit_sha"] == head
    assert w["runner"].phases.count("pm-revision") == 1
    assert git(w["plan"]["worktree_path"], "rev-list", "--count", w["sha"] + "..HEAD") == "1"


@pytest.mark.parametrize("lost_ack", [False, True])
@pytest.mark.parametrize("stop", ["cancel", "revoke"])
async def test_completed_git_fact_survives_cancel_or_authority_loss_without_adoption(ready, lost_ack, stop):
    w = ready
    def after(request):
        if stop == "cancel":
            w["d"].ops.cancel(OWNER, request["revision"]["operation_id"])
        else:
            revoke(w)
        if lost_ack:
            lose(request)
    w["runner"].after["pm-revision"] = after
    first = await adopt(w)
    if lost_ack:
        assert first["status"] == "uncertain", first
        first = await resume(w, first)
    assert first["status"] == ("cancelled" if stop == "cancel" else "failed"), first
    proof = receipt(w, first)
    assert proof and proof["commit_sha"] == git(w["plan"]["worktree_path"], "rev-parse", "HEAD")
    assert not pm_plans.head(w["d"].ops.db, w["workspace_id"])["accepted_revision_id"]
    completed = run(w, first)
    assert completed["state"] == "completed_service" and completed["outcome"]["status"] == "not_adopted"
    assert completed["outcome"]["git_receipt"] == proof
    assert not w["d"].ops.db.execute("SELECT 1 FROM pm_writer_slots WHERE run_id=?", (completed["run_id"],)).fetchone()
    assert w["runner"].phases.count("pm-revision") == 1


async def test_authority_loss_during_partial_recovery_retains_writer(ready, monkeypatch):
    w = ready
    interrupt_host(w, monkeypatch)
    first = await adopt(w)
    assert first["status"] == "uncertain", first
    revoke(w)
    recovered = await resume(w, first)
    assert recovered["status"] == "uncertain", recovered
    original = run(w, first)
    assert original["state"] not in pm_runs.TERMINAL
    assert w["d"].ops.db.execute("SELECT 1 FROM pm_writer_slots WHERE run_id=? AND generation=?",
                                (original["run_id"], original["generation"])).fetchone()
    with pytest.raises(OperationError, match="FORBIDDEN"):
        pm_runs.require_writer(w["d"].journal, original["run_id"], original["generation"])
    assert not receipt(w, first) and w["runner"].phases.count("pm-revision") == 1


async def test_unknown_readback_keeps_writer_and_blocks_competing_adoption(ready):
    w = ready
    w["runner"].after["pm-revision"] = lose
    first = await adopt(w)
    assert first["status"] == "uncertain", first
    w["runner"].before["read.pm-revision"] = lose
    unknown = await resume(w, first)
    assert unknown["status"] == "uncertain", unknown
    second_revision = await draft(w)
    second = await adopt(w, second_revision)
    assert second["status"] == "failed" and second["error_code"] == "PM_WRITER_BUSY", second
    original = run(w, first)
    assert pm_runs.require_writer(w["d"].journal, original["run_id"], original["generation"])
    assert w["runner"].phases.count("pm-revision") == 1
    recovered = await resume(w, first)
    assert recovered["status"] == "succeeded", recovered


async def test_completed_adoption_replays_fact_after_authority_loss(ready):
    w = ready
    original = await adopt(w)
    assert original["status"] == "succeeded", original
    revoke(w)
    count = len(w["runner"].phases)
    recovered = await pm_plan_operations._adopt(OpContext(w["d"].ops, original))
    assert recovered == original["result"] and len(w["runner"].phases) == count
    assert w["d"].ops.db.execute("SELECT COUNT(*) FROM pm_plan_adoptions").fetchone()[0] == 1
    with pytest.raises(OperationError, match="FORBIDDEN"):
        w["d"].ops.create(OWNER, action=original["action"], target=original["target"], params=original["params"],
                         preconditions=original["preconditions"], idempotency_key=original["idempotency_key"])


async def test_next_adoption_uses_git_only_parent_even_when_clock_moves_backwards(ready, monkeypatch):
    w = ready
    first = await adopt(w)
    assert first["status"] == "succeeded", first
    second_revision = await draft(w)
    w["runner"].after["pm-revision"] = lambda request: w["d"].ops.cancel(OWNER, request["revision"]["operation_id"])
    with monkeypatch.context() as patch:
        # Replace only this module's clock, never the shared stdlib time module.
        patch.setattr(pm_plans, "time", SimpleNamespace(time=lambda: 1.0))
        second = await adopt(w, second_revision)
    assert second["status"] == "cancelled", second
    parent = receipt(w, second)["commit_sha"]
    assert pm_plans.head(w["d"].ops.db, w["workspace_id"])["accepted_revision_id"] == w["revision"]["revision_id"]
    third_revision = await draft(w)
    third = await adopt(w, third_revision)
    assert third["status"] == "succeeded", third
    assert git(w["plan"]["worktree_path"], "rev-parse", "HEAD^") == parent
    assert git(w["plan"]["worktree_path"], "rev-list", "--count", w["sha"] + "..HEAD") == "3"


async def test_parallel_adoptions_preserve_one_accepted_direction(ready):
    w = ready
    second_revision = await draft(w)
    one, two = adoption(w), adoption(w, second_revision)
    await settle_operations(w["d"].ops)
    results = [w["d"].ops.get(op["operation_id"]) for op in (one, two)]
    assert sorted(op["status"] for op in results) == ["failed", "succeeded"], results
    failed = next(op for op in results if op["status"] == "failed")
    assert failed["error_code"] in {"PM_WRITER_BUSY", "PM_ACCEPTED_REVISION_CONFLICT"}
    assert w["d"].ops.db.execute("SELECT COUNT(*) FROM pm_plan_adoptions").fetchone()[0] == 1
    assert w["runner"].phases.count("pm-revision") == 1


async def test_zero_effect_unknown_can_cancel_without_retaining_writer(ready):
    w = ready
    w["runner"].before["pm-revision"] = lose
    first = await adopt(w)
    assert first["status"] == "uncertain", first
    w["d"].ops.cancel(OWNER, first["operation_id"])
    cancelled = await resume(w, first)
    assert cancelled["status"] == "cancelled", cancelled
    assert run(w, first)["state"] == "completed_service"
    assert not receipt(w, first)
    assert not Path(w["plan"]["clone_path"], ".git", "batc-pm-revisions.json").exists()


async def git_only(w, revision=None):
    w["runner"].after["pm-revision"] = lambda request: w["d"].ops.cancel(OWNER, request["revision"]["operation_id"])
    op = await adopt(w, revision)
    assert op["status"] == "cancelled" and receipt(w, op), op
    return op


async def test_explicit_same_revision_adoption_reuses_verified_git_without_new_commit(ready):
    w = ready
    original = await git_only(w)
    proof = receipt(w, original)
    recovered = await adopt(w)
    assert recovered["status"] == "succeeded", recovered
    result = recovered["result"]
    assert result["commit_sha"] == proof["commit_sha"]
    assert result["commit_operation_id"] == result["source_operation_id"] == original["operation_id"]
    assert receipt(w, recovered) == {**proof, "operation_id": recovered["operation_id"], "source_operation_id": original["operation_id"]}
    assert w["d"].ops.get(original["operation_id"])["cancel_requested"]
    assert w["runner"].phases.count("pm-revision") == 1
    assert git(w["plan"]["worktree_path"], "rev-list", "--count", w["sha"] + "..HEAD") == "1"
    assert pm_plans.latest_git_receipt(w["d"].ops.db, w["workspace_id"]) == proof


async def test_reusing_older_revision_does_not_move_next_git_parent_backwards(ready):
    w = ready
    first = await git_only(w)
    second_revision = await draft(w)
    second = await git_only(w, second_revision)
    source_head = receipt(w, second)["commit_sha"]
    accepted = await adopt(w)
    assert accepted["status"] == "succeeded", accepted
    assert accepted["result"]["commit_sha"] == receipt(w, first)["commit_sha"]
    assert git(w["plan"]["worktree_path"], "rev-parse", "HEAD") == source_head
    next_revision = await draft(w)
    next_adopted = await adopt(w, next_revision)
    assert next_adopted["status"] == "succeeded", next_adopted
    assert git(w["plan"]["worktree_path"], "rev-parse", "HEAD^") == source_head
    assert w["runner"].phases.count("pm-revision") == 3


async def test_read_verification_lost_ack_recovers_original_source_without_writing(ready):
    w = ready
    original = await git_only(w)
    w["runner"].after["read.pm-revision"] = lose
    first = await adopt(w)
    assert first["status"] == "uncertain", first
    recovered = await resume(w, first)
    assert recovered["status"] == "succeeded", recovered
    assert recovered["result"]["source_operation_id"] == original["operation_id"]
    assert w["runner"].phases.count("pm-revision") == 1


async def test_cancel_unknown_read_verification_releases_only_new_reader_writer(ready):
    w = ready
    original = await git_only(w)
    w["runner"].before["read.pm-revision"] = lose
    first = await adopt(w)
    assert first["status"] == "uncertain", first
    reads = w["runner"].phases.count("read.pm-revision")
    w["d"].ops.cancel(OWNER, first["operation_id"])
    cancelled = await resume(w, first)
    assert cancelled["status"] == "cancelled", cancelled
    assert run(w, cancelled)["state"] == "completed_service"
    assert receipt(w, original) and not receipt(w, cancelled)
    assert w["runner"].phases.count("read.pm-revision") == reads
    assert w["runner"].phases.count("pm-revision") == 1
    # A third decision still refers directly to the actual creation operation.
    completed = await adopt(w)
    assert completed["status"] == "succeeded", completed
    assert completed["result"]["source_operation_id"] == original["operation_id"]


@pytest.mark.parametrize("change", ["commit_ref", "head", "document", "config"])
async def test_reused_revision_requires_fresh_owned_chain_and_binding_proof(ready, change):
    w = ready
    original = await git_only(w)
    path = Path(w["plan"]["worktree_path"])
    proof = receipt(w, original)
    if change == "commit_ref":
        git(path, "update-ref", "refs/batc/pm-revisions/" + original["operation_id"][3:], w["sha"])
    elif change == "head":
        tree = git(path, "rev-parse", "HEAD^{tree}")
        foreign = git(path, "-c", "user.name=fixture", "-c", "user.email=fixture@example.invalid", "commit-tree", tree, "-p", "HEAD", "-m", "foreign")
        git(path, "update-ref", "refs/heads/" + w["plan"]["branch"], foreign)
    elif change == "document":
        Path(path, next(iter(proof["file_digests"]))).write_text("Unrelated edit")
    else:
        w["d"].fleet.config.hosts["h1"].default_permission_mode = "changed"
    failed = await adopt(w)
    assert failed["status"] in {"failed", "needs_attention"}, failed
    assert not receipt(w, failed)
    assert not pm_plans.head(w["d"].ops.db, w["workspace_id"])["accepted_revision_id"]
    assert w["runner"].phases.count("pm-revision") == 1


async def test_owner_revoked_during_reuse_read_records_proof_without_new_decision(ready):
    w = ready
    original = await git_only(w)
    w["runner"].after["read.pm-revision"] = lambda _: revoke(w)
    stopped = await adopt(w)
    assert stopped["status"] == "failed", stopped
    assert receipt(w, stopped)["source_operation_id"] == original["operation_id"]
    assert run(w, stopped)["state"] == "completed_service"
    assert not pm_plans.head(w["d"].ops.db, w["workspace_id"])["accepted_revision_id"]
    assert w["runner"].phases.count("pm-revision") == 1


async def test_source_receipt_alone_cannot_bypass_fresh_host_verification(ready):
    w = ready
    original = await git_only(w)
    requested = adoption(w)
    borrowed = {**receipt(w, original), "operation_id": requested["operation_id"], "source_operation_id": original["operation_id"]}
    with pytest.raises(OperationError, match="PM_GIT_RECEIPT_INVALID"):
        pm_plans.record_git_receipt(OpContext(w["d"].ops, requested), borrowed)
    assert not receipt(w, requested)
    await settle_operations(w["d"].ops)
    assert w["d"].ops.get(requested["operation_id"])["status"] == "succeeded"


async def test_repeated_cancelled_read_decisions_reference_original_creation_directly(ready):
    w = ready
    original = await git_only(w)
    w["runner"].after["read.pm-revision"] = lambda request: w["d"].ops.cancel(OWNER, request["active_operation_id"])
    verification = await adopt(w)
    assert verification["status"] == "cancelled", verification
    requested = adoption(w)
    forwarded = {**receipt(w, verification), "operation_id": requested["operation_id"],
                 "source_operation_id": verification["operation_id"]}
    with pytest.raises(OperationError, match="PM_GIT_RECEIPT_INVALID"):
        pm_plans.record_git_receipt(OpContext(w["d"].ops, requested), forwarded)
    await settle_operations(w["d"].ops)
    completed = w["d"].ops.get(requested["operation_id"])
    assert completed["status"] == "succeeded", completed
    assert completed["result"]["source_operation_id"] == original["operation_id"]
    assert w["runner"].phases.count("pm-revision") == 1


@pytest.mark.parametrize("reuse", [False, True])
async def test_restart_between_reconciled_host_step_and_journal_fact_keeps_exact_receipt(ready, monkeypatch, reuse):
    w = ready
    if reuse:
        await git_only(w)
    w["runner"].after["read.pm-revision" if reuse else "pm-revision"] = lose
    first = await adopt(w)
    assert first["status"] == "uncertain", first
    record = pm_plans.record_git_receipt
    armed = {"value": True}
    def interrupted(ctx, proof):
        if armed["value"]:
            armed["value"] = False
            raise Uncertain("pm.plan.git-receipt", "Simulated restart before the local fact transaction")
        return record(ctx, proof)
    monkeypatch.setattr(pm_plans, "record_git_receipt", interrupted)
    gap = await resume(w, first)
    assert gap["status"] == "uncertain" and not receipt(w, gap), gap
    step = next(item for item in gap["steps"] if item["name"] == ("pm.revision.verify" if reuse else "pm.revision.commit"))
    assert step["status"] == "succeeded"
    before = len(w["runner"].phases)
    completed = await resume(w, gap)
    assert completed["status"] == "succeeded", completed
    assert len(w["runner"].phases) == before
    assert "reconciled" not in receipt(w, completed)
    assert w["runner"].phases.count("pm-revision") == 1
