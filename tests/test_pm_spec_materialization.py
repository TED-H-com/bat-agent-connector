"""Owned immutable spec commits, original recovery, and ordinary artifact handoff."""
from __future__ import annotations

import copy
import json
import sqlite3
import uuid
from pathlib import Path

import pytest

from bat_agent_connector import (
    pm_access,
    pm_plans,
    pm_runs,
    pm_source_lineage,
    pm_source_reads,
    pm_spec_format,
)
from bat_agent_connector import pm_spec_materialization as specs
from bat_agent_connector.operations import OperationError
from tests.operation_helpers import settle_operations
from tests.test_checkpoints import bat_writes, git, snapshot
from tests.test_pm_package_handoff import dispatch_runner
from tests.test_pm_plan_operations import OWNER, adopt, draft
from tests.test_pm_work_packages import create, read
from tests.test_pm_work_packages import pm as pm_fixture
from tests.test_pm_work_packages import ready as ready_fixture
from tests.test_pm_work_packages import world as world_fixture

world = world_fixture
pm = pm_fixture
ready = ready_fixture


@pytest.fixture
async def fixed(ready):
    w = ready
    version = w["d"].ops.db.execute("PRAGMA user_version").fetchone()[0]
    specs.install(w["d"].ops)
    specs.install(w["d"].ops)
    assert w["d"].ops.db.execute("PRAGMA user_version").fetchone()[0] == version
    w["package"] = read(w, await create(w))
    dispatch_runner(w)
    return w


def request(w):
    return {"action": specs.ACTION, "target": {"workspace_id": w["workspace_id"], "package_id": w["package"]["package_id"]},
            "params": {}, "preconditions": {"expected_package_digest": w["package"]["document_digest"],
                "expected_scope_revision": pm_plans.accepted_plan(w["d"].ops, OWNER, w["workspace_id"])["plan"]["adoption"]["scope_revision"]},
            "idempotency_key": uuid.uuid4().hex}


async def materialize(w, envelope=None):
    op, _ = w["d"].ops.create(OWNER, **(envelope or request(w)))
    await settle_operations(w["d"].ops)
    result = w["d"].ops.get(op["operation_id"])
    assert result["status"] == "succeeded", result
    return result


def record(w, op):
    return specs.get_materialization(w["d"].ops, OWNER, w["workspace_id"], op["result"]["materialization_id"])["materialization"]


async def test_fixed_owned_git_artifact_and_idempotent_original_receipts(fixed, mock):
    w, ops = fixed, fixed["d"].ops
    human, carrier = snapshot(w["human"]), Path(w["plan"]["worktree_path"])
    before = git(carrier, "rev-parse", "HEAD")
    envelope = request(w)
    op = await materialize(w, envelope)
    value = record(w, op)
    assert value["document"]["package"] == w["package"]["document"]
    root, index = pm_spec_format.paths(value["document"])
    raw = ops.context["artifact_store"].read_content(value["artifact_ref"]["artifact_id"], 1)
    assert raw == (carrier / root / "package.json").read_bytes()
    assert pm_spec_format.digest(value["document"]) == value["document_digest"]
    assert git(carrier, "rev-parse", "HEAD^") == before
    assert git(carrier, "rev-parse", "HEAD") == value["git_receipt"]["commit_sha"]
    assert set(git(carrier, "diff-tree", "--no-commit-id", "--name-only", "-r", "HEAD").splitlines()) == set(value["git_receipt"]["file_digests"])
    assert len(value["git_receipt"]["file_digests"]) == 7
    assert json.loads((carrier / index).read_text())["artifact_ref"] == value["artifact_ref"]
    assert pm_source_lineage.roots(ops.db, "artifact", value["artifact_ref"]["artifact_id"]) == {op["operation_id"]}
    assert pm_runs.get_by_operation(ops.db, op["operation_id"])["state"] == "completed_service"
    assert pm_plans.latest_git_receipt(ops.db, w["workspace_id"])["commit_sha"] == value["git_receipt"]["commit_sha"]
    replay, created = ops.create(OWNER, **envelope)
    assert not created and replay["operation_id"] == op["operation_id"]
    with pytest.raises(OperationError, match="original immutable"):
        ops.create(OWNER, **request(w))
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        ops.db.execute("DELETE FROM pm_spec_materializations")
    assert snapshot(w["human"]) == human
    assert not bat_writes(mock)


async def test_shared_visibility_loads_canonical_spec_sources(fixed, mock):
    w, ops = fixed, fixed["d"].ops
    human = snapshot(w["human"])
    op = await materialize(w)
    compact = {key: op[key] for key in ("operation_id", "action", "target", "actor")}
    forged = {**compact, "external_refs": {"admission_binding": {"pm_spec_materialization": {}}}}
    changes = ops.db.total_changes
    with pm_source_reads.scope(ops.db, OWNER):
        assert pm_access.visible(ops.db, OWNER, compact)
        assert specs.source_visible(ops.db, OWNER, forged)
        assert pm_access.visible(ops.db, OWNER, forged)
    assert ops.db.total_changes == changes
    ops.db.execute("DELETE FROM pm_workspace_members WHERE workspace_id=? AND actor=?",
                   (w["workspace_id"], OWNER.actor))
    assert not pm_access.visible(ops.db, OWNER, compact)
    assert not specs.source_visible(ops.db, OWNER, forged)
    assert snapshot(w["human"]) == human
    assert not bat_writes(mock)


async def test_explicit_task_keys_survive_reorder_edit_remove_and_reintroduction(fixed):
    w = fixed
    first = record(w, await materialize(w))
    original = first["document"]["tasks"][0]["task_id"]
    one = copy.deepcopy(w["params"]["tasks"][0])
    two = {**one, "key": "identity.audit", "description": "Record safe audit events"}
    w["package"] = read(w, await create(w, previous=w["package"]["package_id"], tasks=[two, {**one, "description": "Edited words"}]))
    second = record(w, await materialize(w))
    assert next(t for t in second["document"]["tasks"] if t["key"] == one["key"])["task_id"] == original
    w["package"] = read(w, await create(w, previous=w["package"]["package_id"], tasks=[two]))
    third = record(w, await materialize(w))
    removed = next(t for t in third["document"]["tasks"] if t["key"] == one["key"])
    assert removed["state"] == "removed" and removed["task_id"] == original
    # An unmaterialized package also participates in the stable identity history.
    w["package"] = read(w, await create(w, previous=w["package"]["package_id"], tasks=[one, two]))
    w["package"] = read(w, await create(w, previous=w["package"]["package_id"], tasks=[two, one]))
    final = record(w, await materialize(w))
    restored = next(t for t in final["document"]["tasks"] if t["key"] == one["key"])
    assert restored["task_id"] == original and restored["state"] == "active"
    assert [row["state"] for row in restored["history"]] == ["active", "active", "removed", "active", "active"]
    assert len(final["document"]["source_chain"]) == 5


async def test_next_plan_commit_extends_spec_carrier_without_rewriting_original_plan(fixed):
    w, ops = fixed, fixed["d"].ops
    value = record(w, await materialize(w))
    old_plan = pm_plans.original_git_receipt(ops.db, w["workspace_id"])
    w["revision"] = await draft(w, requirements=w["requirements"], markdown="# Revised plan\n\nExact adopted scope remains explicit.")
    next_plan = await adopt(w)
    assert next_plan["status"] == "succeeded", next_plan
    latest = pm_plans.latest_git_receipt(ops.db, w["workspace_id"])
    assert latest["commit_sha"] != value["git_receipt"]["commit_sha"]
    assert git(w["plan"]["worktree_path"], "rev-parse", latest["commit_sha"] + "^") == value["git_receipt"]["commit_sha"]
    assert git(w["plan"]["worktree_path"], "show", old_plan["commit_sha"] + ":docs/pm/revisions/" + old_plan["revision_id"] + "/plan.md")

    # Historical evidence does not authorize bypassing the currently adopted
    # baseline, and a refused old-plan adoption cannot move the carrier tip.
    with pytest.raises(OperationError) as conflict:
        await adopt(w, {"revision_id": old_plan["revision_id"]})
    assert conflict.value.code == "PM_ACCEPTED_REVISION_CONFLICT"
    assert pm_plans.latest_git_receipt(ops.db, w["workspace_id"])["commit_sha"] == latest["commit_sha"]
    assert git(w["plan"]["worktree_path"], "rev-parse", "HEAD") == latest["commit_sha"]
