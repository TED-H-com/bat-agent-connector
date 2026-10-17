"""Tests for the work.start compound action and pm_work_start_preview (v1.3 E02, slice P3)."""
from __future__ import annotations

import asyncio
import json
import sqlite3
import time
import uuid

import pytest

from bat_agent_connector import controller_authority, pm_work_start, pm_workspace
from bat_agent_connector.api_auth import Principal
from bat_agent_connector.operations import ActionDef, NeedsAttention, OperationError, OperationService, Wait
from bat_agent_connector.task_journal import Journal

OWNER = Principal("alice", frozenset({"observe", "manage", "start", "operate"}))
OTHER = Principal("bob", frozenset({"observe", "manage", "start", "operate"}))


def _service(tmp_path) -> OperationService:
    journal = Journal(tmp_path / "tasks.sqlite3")
    pm_workspace.schema(journal)
    ops = OperationService(journal)
    srv = pm_workspace.server_id(ops.db)
    now = time.time()
    with journal.tx():
        for idx, ws in enumerate(("ws_" + "1" * 32, "ws_" + "2" * 32)):
            ops.db.execute(
                "INSERT INTO pm_workspaces VALUES(?,?,?,?,1,?,?,?,?)",
                (ws, srv, f"WS {idx}", '["o/r"]', "alice", now, now, f"op_ws_{idx}"),
            )
            for who in (OWNER, OTHER):
                ops.db.execute(
                    "INSERT INTO pm_workspace_members VALUES(?,?,?,?,?,?)",
                    (ws, who.actor, "owner", pm_workspace.controller_for(ops.db, who), now, now),
                )

    def admit(service, principal, target, params, pre):
        wid, wi = target["workspace_id"], target["work_item_id"]
        ctrl = controller_authority.binding(service.db, principal, pm_workspace_id=wid)
        if not params.get("_skip_pre_controller_check"):
            pm_work_start.require_work_controller(service.db, wid, wi, ctrl)
        intent = {"workspace_id": wid, "work_item_id": wi, "controller": ctrl, "params": params}
        return {
            "principal": pm_workspace.principal_binding(principal),
            "work_start": intent,
            "work_start_digest": pm_work_start._digest(intent),
        }

    async def run(ctx):
        if ctx.params.get("pause"):
            raise NeedsAttention("PAUSED_FOR_TEST", "Paused for controller test")
        if ctx.params.get("wait_s"):
            raise Wait("waiting_external", "Waiting for child", delay_s=float(ctx.params["wait_s"]))
        return {"ok": True}

    ops.register(
        ActionDef(
            "work.start",
            "start",
            "Start work",
            run,
            admit,
            ("workspace_id", "work_item_id"),
        )
    )
    return ops


def test_schema_immutability_and_no_user_version(tmp_path):
    ops = _service(tmp_path)
    before_ver = ops.db.execute("PRAGMA user_version").fetchone()[0]
    pm_work_start.schema(ops.journal)
    assert ops.db.execute("PRAGMA user_version").fetchone()[0] == before_ver == 3
    ws = "ws_" + "1" * 32
    wi = "wi_" + "2" * 20
    op, created = ops.create(
        OWNER,
        action="work.start",
        target={"workspace_id": ws, "work_item_id": wi},
        params={"instruction": "Fix bug"},
        preconditions={},
        idempotency_key="k1",
    )
    assert created is True
    attempt_id = "att_" + op["operation_id"][3:]
    row = ops.db.execute("SELECT * FROM work_attempts WHERE attempt_id=?", (attempt_id,)).fetchone()
    assert row is not None
    assert row["origin_kind"] == "operation"
    assert row["origin_id"] == op["operation_id"]
    assert row["start_operation_id"] == op["operation_id"]
    assert row["predecessor_attempt_id"] is None

    with pytest.raises(sqlite3.IntegrityError, match="Work attempt history is immutable"):
        ops.db.execute("UPDATE work_attempts SET origin_id='op_other' WHERE attempt_id=?", (attempt_id,))
    with pytest.raises(sqlite3.IntegrityError, match="Work attempt history is immutable"):
        ops.db.execute("DELETE FROM work_attempts WHERE attempt_id=?", (attempt_id,))

    with ops.journal.tx():
        ops.db.execute(
            "INSERT INTO pm_work_start_children VALUES(?,?,?,?,?,?,?)",
            (op["operation_id"], "bind", 0, "op_" + "a" * 32, "{}", "d" * 64, time.time()),
        )
    with pytest.raises(sqlite3.IntegrityError, match="Work start child is immutable"):
        ops.db.execute("UPDATE pm_work_start_children SET slot='package' WHERE parent_id=?", (op["operation_id"],))
    with pytest.raises(sqlite3.IntegrityError, match="Work start child is immutable"):
        ops.db.execute("DELETE FROM pm_work_start_children WHERE parent_id=?", (op["operation_id"],))


def test_insert_hook_predecessor_chain_and_controller_rollback(tmp_path):
    ops = _service(tmp_path)
    pm_work_start.schema(ops.journal)
    ws = "ws_" + "1" * 32
    wi = "wi_" + "2" * 20
    first, _ = ops.create(
        OWNER,
        action="work.start",
        target={"workspace_id": ws, "work_item_id": wi},
        params={"instruction": "First attempt"},
        preconditions={},
        idempotency_key="k-first",
    )
    # While first is non-terminal (accepted), a second start with a new key rolls back in admit_operation.
    ops_after_first = ops.db.execute("SELECT COUNT(*) FROM operations").fetchone()[0]
    with pytest.raises(OperationError) as in_prog_exc:
        ops.create(
            OWNER,
            action="work.start",
            target={"workspace_id": ws, "work_item_id": wi},
            params={"instruction": "Concurrent second attempt"},
            preconditions={},
            idempotency_key="k-second-too-early",
        )
    assert in_prog_exc.value.code == "PM_WORK_START_IN_PROGRESS"
    assert first["operation_id"] in in_prog_exc.value.message
    assert ops.db.execute("SELECT COUNT(*) FROM operations").fetchone()[0] == ops_after_first
    assert ops.db.execute("SELECT COUNT(*) FROM work_attempts").fetchone()[0] == 1

    # Once first reaches a terminal state via public cancel, a new start is admitted and links predecessor_attempt_id.
    ops.cancel(OWNER, first["operation_id"])
    second, _ = ops.create(
        OWNER,
        action="work.start",
        target={"workspace_id": ws, "work_item_id": wi},
        params={"instruction": "Second attempt"},
        preconditions={},
        idempotency_key="k-second",
    )
    row2 = ops.db.execute(
        "SELECT * FROM work_attempts WHERE start_operation_id=?", (second["operation_id"],)
    ).fetchone()
    assert row2["predecessor_attempt_id"] == "att_" + first["operation_id"][3:]

    # Early check in _admit rejects a second controller.
    with pytest.raises(OperationError) as exc_info:
        ops.create(
            OTHER,
            action="work.start",
            target={"workspace_id": ws, "work_item_id": wi},
            params={"instruction": "Takeover"},
            preconditions={},
            idempotency_key="k-other",
        )
    assert exc_info.value.code == "PM_WORK_CONTROLLER_CONFLICT"

    # Even if a concurrent start bypassed _admit's pre-check before the winner committed,
    # the INSERT hook re-checks inside the transaction and rolls back the operation row.
    count_before = ops.db.execute("SELECT COUNT(*) FROM operations").fetchone()[0]
    with pytest.raises(OperationError) as exc_info2:
        ops.create(
            OTHER,
            action="work.start",
            target={"workspace_id": ws, "work_item_id": wi},
            params={"instruction": "Concurrent takeover", "_skip_pre_controller_check": True},
            preconditions={},
            idempotency_key="k-concurrent",
        )
    assert exc_info2.value.code == "PM_WORK_CONTROLLER_CONFLICT"
    assert ops.db.execute("SELECT COUNT(*) FROM operations").fetchone()[0] == count_before
    assert ops.db.execute("SELECT COUNT(*) FROM work_attempts").fetchone()[0] == 2


def test_workspace_key_namespacing_and_idempotency_conflict(tmp_path):
    ops = _service(tmp_path)
    ws1 = "ws_" + "1" * 32
    ws2 = "ws_" + "2" * 32
    wi = "wi_" + "3" * 20

    op1, fresh1 = ops.create(
        OWNER,
        action="work.start",
        target={"workspace_id": ws1, "work_item_id": wi},
        params={"instruction": "In ws1"},
        preconditions={},
        idempotency_key="shared-key",
    )
    assert fresh1 is True
    assert op1["idempotency_key"] == "shared-key"
    assert ops.has_idempotency_record(
        OWNER, action="work.start", target={"workspace_id": ws1, "work_item_id": wi}, idempotency_key="shared-key"
    )
    assert not ops.has_idempotency_record(
        OWNER, action="work.start", target={"workspace_id": ws2, "work_item_id": wi}, idempotency_key="shared-key"
    )

    # Same key in the same workspace with identical request replays.
    replay, fresh_replay = ops.create(
        OWNER,
        action="work.start",
        target={"workspace_id": ws1, "work_item_id": wi},
        params={"instruction": "In ws1"},
        preconditions={},
        idempotency_key="shared-key",
    )
    assert fresh_replay is False
    assert replay["operation_id"] == op1["operation_id"]
    assert replay["idempotency_key"] == "shared-key"

    # Same key in a different workspace creates a distinct operation.
    op2, fresh2 = ops.create(
        OWNER,
        action="work.start",
        target={"workspace_id": ws2, "work_item_id": wi},
        params={"instruction": "In ws2"},
        preconditions={},
        idempotency_key="shared-key",
    )
    assert fresh2 is True
    assert op2["operation_id"] != op1["operation_id"]
    assert op2["idempotency_key"] == "shared-key"

    # Same key in the same workspace with a different request raises IDEMPOTENCY_CONFLICT.
    with pytest.raises(OperationError) as exc_info:
        ops.create(
            OWNER,
            action="work.start",
            target={"workspace_id": ws1, "work_item_id": wi},
            params={"instruction": "Changed intent"},
            preconditions={},
            idempotency_key="shared-key",
        )
    assert exc_info.value.code == "IDEMPOTENCY_CONFLICT"
    assert exc_info.value.status == 409


@pytest.mark.asyncio
async def test_authorize_controller_blocks_different_actor_cancel_and_resume(tmp_path):
    ops = _service(tmp_path)
    ws = "ws_" + "1" * 32
    wi = "wi_" + "2" * 20
    op, _ = ops.create(
        OWNER,
        action="work.start",
        target={"workspace_id": ws, "work_item_id": wi},
        params={"instruction": "Pause me", "pause": True},
        preconditions={},
        idempotency_key="k-ctrl",
    )
    await ops._execute(op["operation_id"])
    assert ops.get(op["operation_id"])["status"] == "needs_attention"

    with pytest.raises(OperationError) as cancel_exc:
        ops.cancel(OTHER, op["operation_id"])
    assert cancel_exc.value.code == "CONTROLLER_MISMATCH"

    with pytest.raises(OperationError) as resume_exc:
        ops.resume(OTHER, op["operation_id"])
    assert resume_exc.value.code == "CONTROLLER_MISMATCH"

    # Original controller can resume and cancel.
    resumed = ops.resume(OWNER, op["operation_id"])
    assert resumed["status"] == "running"
    await ops._execute(op["operation_id"])
    cancelled = ops.cancel(OWNER, op["operation_id"])
    assert cancelled["status"] == "cancelled"


@pytest.mark.asyncio
async def test_child_settled_trigger_wakes_waiting_parent(tmp_path):
    ops = _service(tmp_path)

    async def run_child(ctx):
        return {"done": True}

    ops.register(ActionDef("dummy.child", "start", "Dummy child", run_child))
    ws = "ws_" + "1" * 32
    wi = "wi_" + "2" * 20
    parent, _ = ops.create(
        OWNER,
        action="work.start",
        target={"workspace_id": ws, "work_item_id": wi},
        params={"instruction": "Wait on child", "wait_s": 3600.0},
        preconditions={},
        idempotency_key="k-wake",
    )
    child, _ = ops.create(
        OWNER,
        action="dummy.child",
        target={},
        params={},
        preconditions={},
        idempotency_key="k-child",
    )
    with ops.journal.tx():
        ops.db.execute(
            "INSERT INTO pm_work_start_children VALUES(?,?,?,?,?,?,?)",
            (parent["operation_id"], "bind", 0, child["operation_id"], "{}", "d" * 64, time.time()),
        )
    await ops._execute(parent["operation_id"])
    waiting_parent = ops.get(parent["operation_id"])
    assert waiting_parent["status"] == "waiting_external"
    assert waiting_parent["next_run_at"] > time.time() + 1000
    await ops._execute(child["operation_id"])
    assert ops.get(child["operation_id"])["status"] == "succeeded"
    assert ops.get(parent["operation_id"])["next_run_at"] == 0


# ---------------------------------------------------------------------------
# Step 3: Admission, thin composer, and readiness rejection tests
# ---------------------------------------------------------------------------


def _setup_full_pm_world(tmp_path):
    """Build a lightweight local PM + repository_sync world without slow git clones."""
    from types import SimpleNamespace

    from bat_agent_connector import (
        artifacts,
        pm_discussions,
        pm_evidence,
        pm_lifecycle,
        pm_plans,
        pm_work_packages,
        repository_sync,
        work_items,
    )

    journal = Journal(tmp_path / "full.sqlite3")
    pm_workspace.schema(journal)
    ops = OperationService(journal)
    for adef in work_items.ACTIONS:
        ops.register(adef)
    pm_discussions.install(ops)
    pm_plans.install(ops)
    pm_lifecycle.install(ops)
    pm_evidence.install(ops)
    pm_work_packages.install(ops)
    repository_sync.install(ops)
    pm_work_start.install(ops)

    from dataclasses import dataclass

    @dataclass
    class _SyncCfg:
        remote_url: str
        bindings: tuple = (("h1", "w1"),)
        fetch_timeout_s: int = 10

    sync_cfg = _SyncCfg(remote_url=str(tmp_path / "remote.git"))
    repo_cfg = SimpleNamespace(repository="o/r", sync=sync_cfg)
    host_cfg = SimpleNamespace(
        name="h1",
        profile_id="prof-1",
        managed_roots=[str(tmp_path / "managed")],
        default_permission_mode="default",
        orchestrate_register_tabs=False,
        confinement="worktree",
    )
    fleet = SimpleNamespace(
        config=SimpleNamespace(
            github=SimpleNamespace(repos={"o/r": repo_cfg}),
            hosts={"h1": host_cfg},
        ),
        orchestrate_enabled=lambda h: h == "h1",
    )
    ops.context["fleet"] = fleet
    ops.context["github_config"] = SimpleNamespace(api_url="https://api.github.test")
    ops.context["github"] = SimpleNamespace()
    ops.context["git_runner"] = SimpleNamespace(available=lambda h: h == "h1")
    ops.context["artifact_store"] = SimpleNamespace(settings=artifacts.ArtifactSettings())

    now = time.time()
    srv = pm_workspace.server_id(ops.db)
    ws = "ws_" + "a" * 32
    with journal.tx():
        ops.db.execute(
            "INSERT INTO pm_workspaces VALUES(?,?,?,?,1,?,?,?,?)",
            (ws, srv, "Product WS", '["o/r"]', OWNER.actor, now, now, "op_" + "0" * 32),
        )
        for actor, role in ((OWNER, "owner"), (OTHER, "owner")):
            ops.db.execute(
                "INSERT INTO pm_workspace_members VALUES(?,?,?,?,?,?)",
                (ws, actor.actor, role, pm_workspace.controller_for(ops.db, actor), now, now),
            )
    return ops, ws


def _seed_plan_and_work(
    ops: OperationService,
    ws: str,
    *,
    req_id: str = "req-login.1",
    req_status: str = "user_confirmed",
    acc_status: str = "user_confirmed",
    with_acceptance: bool = True,
    depends_on: list[str] | None = None,
    bind_project: bool = True,
    bind_work: bool = True,
    work_title: str = "Implement login flow",
):
    import uuid

    from bat_agent_connector import pm_plans, pm_work_packages, repository_sync, work_items

    now = time.time()
    uid = uuid.uuid4().hex
    thread_id = "pmt_" + uid
    draft_id = "pmd_" + uid
    rev_id = "pmrev_" + uid
    scope_rev = "pmscope_" + uid
    decision_id = "pmdec_" + uid
    adopt_op_id = "op_" + uid
    draft_op_id = "op_" + uuid.uuid4().hex
    inp_req = "pmi_" + uuid.uuid4().hex
    inp_acc = "pmi_" + uuid.uuid4().hex
    inp_dep = "pmi_" + uuid.uuid4().hex

    selected_inputs = [
        {
            "input_id": inp_req,
            "slot": "scope.login",
            "value": "Users can sign in with their credentials.",
            "status": req_status,
            "source_ref": {"kind": "statement", "ref": "owner"},
            "confirmed_by": OWNER.actor if req_status == "user_confirmed" else None,
            "operation_id": adopt_op_id,
        }
    ]
    if with_acceptance:
        selected_inputs.append(
            {
                "input_id": inp_acc,
                "slot": "quality.login",
                "value": "Valid credentials succeed and invalid credentials return 401.",
                "status": acc_status,
                "source_ref": {"kind": "statement", "ref": "owner"},
                "confirmed_by": OWNER.actor if acc_status == "user_confirmed" else None,
                "operation_id": adopt_op_id,
            }
        )
    requirements = []
    if depends_on:
        for dep_id in depends_on:
            selected_inputs.append(
                {
                    "input_id": inp_dep,
                    "slot": "scope.base",
                    "value": "Prerequisite base capability.",
                    "status": "user_confirmed",
                    "source_ref": {"kind": "statement", "ref": "owner"},
                    "confirmed_by": OWNER.actor,
                    "operation_id": adopt_op_id,
                }
            )
            requirements.append(
                {
                    "id": dep_id,
                    "text": "Prerequisite base capability.",
                    "source_input_id": inp_dep,
                    "source_status": "user_confirmed",
                    "source_ref": {"kind": "statement", "ref": "owner"},
                    "confirmed_by": OWNER.actor,
                    "priority": "must",
                    "priority_status": "proposal",
                    "depends_on": [],
                    "acceptance": [
                        {
                            "text": "Valid credentials succeed and invalid credentials return 401.",
                            "source_input_id": inp_acc,
                            "source_status": "user_confirmed",
                        }
                    ]
                    if with_acceptance
                    else [],
                }
            )
    requirements.append(
        {
            "id": req_id,
            "text": "Users can sign in with their credentials.",
            "source_input_id": inp_req,
            "source_status": req_status,
            "source_ref": {"kind": "statement", "ref": "owner"},
            "confirmed_by": OWNER.actor if req_status == "user_confirmed" else None,
            "priority": "must",
            "priority_status": "proposal",
            "depends_on": list(depends_on or []),
            "acceptance": [
                {
                    "text": "Valid credentials succeed and invalid credentials return 401.",
                    "source_input_id": inp_acc,
                    "source_status": acc_status,
                }
            ]
            if with_acceptance
            else [],
        }
    )

    sections = {
        domain: {"source_input_ids": [], "coverage": "unresolved", "statements": []}
        for domain in ("problem", "value", "users", "scope", "architecture", "delivery", "quality", "governance")
    }
    plan_doc = {
        "schema_version": 1,
        "revision_id": rev_id,
        "workspace_id": ws,
        "title": "Adopted Plan",
        "markdown": "# Adopted Plan\n",
        "content_status_at_creation": "proposal",
        "planning_draft_id": draft_id,
        "thread_id": thread_id,
        "source_thread_version": 1,
        "profile_version": "1.0",
        "selected_inputs": selected_inputs,
        "sections": sections,
        "requirements": requirements,
        "unclassified_source_input_ids": [],
        "adoption_status_source": "workspace_journal",
        "execution_authorized": False,
        "scope_coverage": {"structured": "selected_sources_only", "markdown": "unclassified"},
    }
    files = pm_plans._files(rev_id, plan_doc)
    adoption = {
        "workspace_id": ws,
        "revision_id": rev_id,
        "decision_id": decision_id,
        "scope_revision": scope_rev,
        "commit_sha": "1" * 40,
        "tree_sha": "2" * 40,
    }
    project_id = "prj_" + uuid.uuid4().hex[:20]
    wi_id = "wi_" + uuid.uuid4().hex[:20]
    binding_id = "pmwb_" + uuid.uuid4().hex if bind_work else None
    proj_binding_id = "pmpb_" + uuid.uuid4().hex

    with ops.journal.tx():
        for oid, act in ((adopt_op_id, "pm.plan.adopt"), (draft_op_id, "pm.thread.plan")):
            ops.db.execute(
                """INSERT INTO operations(
                    operation_id,actor,entry,idem_key,request_hash,action,target,params,preconditions,status,created_at,updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?,'succeeded',?,?)""",
                (oid, OWNER.actor, "test", "k-" + oid, "h", act, "{}", "{}", "{}", now, now),
            )
        ops.db.execute(
            "INSERT INTO pm_threads VALUES(?,?,?,'planning',1,?,?,?,?,'op_t_' || ?)",
            (thread_id, ws, "Planning Thread", OWNER.actor, now, now, draft_id, uid),
        )
        ops.db.execute(
            "INSERT INTO pm_planning_drafts VALUES(?,?,?,?,?,?,?,1,?,?,?)",
            (
                draft_id,
                ws,
                thread_id,
                "Draft",
                "Rationale",
                json.dumps([x["input_id"] for x in selected_inputs]),
                "[]",
                OWNER.actor,
                now,
                draft_op_id,
            ),
        )
        ops.db.execute(
            """INSERT INTO pm_plan_revisions(
                revision_id,workspace_id,thread_id,planning_draft_id,previous_revision_id,
                base_accepted_revision_id,document_digest,scope_digest,document,created_by,created_at,operation_id
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                rev_id,
                ws,
                thread_id,
                draft_id,
                None,
                None,
                files["document_digest"],
                "0" * 64,
                pm_plans._json(plan_doc),
                OWNER.actor,
                now,
                adopt_op_id,
            ),
        )
        ops.db.execute(
            """INSERT INTO pm_plan_adoptions(
                operation_id,workspace_id,revision_id,previous_revision_id,scope_revision,
                decision_id,rationale,adopted_by,created_at,result
            ) VALUES(?,?,?,?,?,?,?,?,?,?)""",
            (
                adopt_op_id,
                ws,
                rev_id,
                None,
                scope_rev,
                decision_id,
                "Adopt",
                OWNER.actor,
                now,
                pm_plans._json(adoption),
            ),
        )
        ops.db.execute(
            "INSERT OR REPLACE INTO pm_plan_heads VALUES(?,1,?,?)",
            (ws, rev_id, rev_id),
        )
        ops.db.execute(
            """INSERT INTO projects(
                project_id,name,description,repositories,version,created_by,operation_id,created_at,updated_at
            ) VALUES(?,?,?,'["o/r"]',1,?,?,?,?)""",
            (project_id, "Core Project", "Desc", OWNER.actor, "op_prj_" + uid, now, now),
        )
        ops.db.execute(
            """INSERT INTO work_items(
                work_item_id,project_id,title,goal,request,acceptance,steps,state,
                version,created_by,operation_id,created_at,updated_at
            ) VALUES(?,?,?,?,?,?,'[]','todo',1,?,?,?,?)""",
            (wi_id, project_id, work_title, "Goal", "Implement login", "Acceptance", OWNER.actor, "op_wi_" + uid, now, now),
        )
        if bind_project:
            ops.db.execute(
                """INSERT INTO pm_project_bindings(
                    binding_id,workspace_id,project_id,project_version,rationale,actor,created_at,operation_id
                ) VALUES(?,?,?,1,'Bind project',?,?,?)""",
                (proj_binding_id, ws, project_id, OWNER.actor, now, "op_pb_" + uid),
            )
        if bind_work:
            ops.db.execute(
                """INSERT INTO pm_work_bindings(
                    binding_id,workspace_id,work_item_id,project_binding_id,project_id,work_version,
                    previous_binding_id,plan_revision_id,requirement_ids,rationale,actor,created_at,operation_id
                ) VALUES(?,?,?,?,?,1,NULL,?,?,'Bind work',?,?,?)""",
                (
                    binding_id,
                    ws,
                    wi_id,
                    proj_binding_id,
                    project_id,
                    rev_id,
                    json.dumps([req_id]),
                    OWNER.actor,
                    now,
                    "op_wb_" + uid,
                ),
            )

    work = work_items._get_item(ops.db, wi_id)
    project = work_items._get_project(ops.db, project_id)
    caps = repository_sync.capabilities(ops)
    bat_ws = caps[0]["workspace_id"] if caps else "w1"
    code_target = {
        "repository": "o/r",
        "host": "h1",
        "workspace_id": bat_ws,
        "source_ref": "refs/heads/main",
    }
    config = repository_sync.config_snapshot(ops, {"repository": "o/r", "host": "h1", "workspace_id": bat_ws})
    binding_digest = repository_sync.digest(
        {"config": config, "workspace": {"workspace_id": bat_ws, "folder": f"/tmp/{bat_ws}"}, "repository_id": 42}
    )
    preconditions = {
        "expected_work_item_fingerprint": work_items.fingerprint(work),
        "expected_work_version": work["version"],
        "expected_binding_id": binding_id,
        "expected_scope_revision": scope_rev,
        "expected_previous_package_id": pm_work_packages._latest(ops.db, ws, wi_id),
        "expected_project_version": project["version"],
        "repository_id": 42,
        "binding_digest": binding_digest,
        "expected_source_sha": "c" * 40,
        "expected_latest_attempt_id": pm_work_start.latest_attempt_id(ops.db, ws, wi_id),
    }
    params = {
        "instruction": "Implement the login endpoint and tests.",
        "agent": "claude",
        "delivery": "pull_request",
        "requirement_ids": [req_id],
        "code_target": code_target,
    }
    return {
        "target": {"workspace_id": ws, "work_item_id": wi_id},
        "params": params,
        "preconditions": preconditions,
        "project_id": project_id,
        "work_item_id": wi_id,
        "revision_id": rev_id,
        "scope_revision": scope_rev,
        "binding_id": binding_id,
    }


def test_thin_composer_truncates_long_title_and_uses_indexed_keys(tmp_path):
    from bat_agent_connector import work_items

    ops, ws = _setup_full_pm_world(tmp_path)
    long_title = "T" * 245
    ctx = _seed_plan_and_work(ops, ws, req_id="auth.login-v2", work_title=long_title)

    op, fresh = ops.create(
        OWNER,
        action="work.start",
        target=ctx["target"],
        params=ctx["params"],
        preconditions=ctx["preconditions"],
        idempotency_key="k-compose",
    )
    assert fresh is True
    # Work item keeps its full 245-char title.
    stored_work = work_items._get_item(ops.db, ctx["work_item_id"])
    assert stored_work["title"] == long_title
    assert len(stored_work["title"]) == 245

    intent = op["external_refs"]["admission_binding"]["work_start"]
    assert intent["composition_profile"] == "work_start_thin_v1"
    assert intent["title"] == long_title
    desc = intent["package_plan"]["description"]
    assert len(desc["title"]) == 200
    assert desc["title"] == ("T" * 199) + "…"
    assert [t["key"] for t in desc["tasks"]] == ["req_1"]
    assert desc["tasks"][0]["depends_on"] == []
    assert [v["key"] for v in desc["verification"]] == ["ver_1_1"]
    assert desc["design"]["status"] == "not_applicable"
    assert "no separate design was authored" in desc["design"]["text"]
    for aspect in ("migration", "rollout", "rollback", "observation"):
        assert desc["delivery"][aspect]["status"] == "not_applicable"
        assert "delivery stops at pull_request" in desc["delivery"][aspect]["text"]


@pytest.mark.parametrize(
    "case,kwargs,expected_code,expected_check",
    [
        ("unconfirmed_req", {"req_status": "candidate"}, "PM_WORK_START_NOT_READY", "source_status:req-login.1"),
        ("unconfirmed_acc", {"acc_status": "assumption"}, "PM_WORK_START_NOT_READY", "source_status:req-login.1"),
        ("missing_acc", {"with_acceptance": False}, "PM_WORK_START_NOT_READY", "acceptance:req-login.1"),
        ("depends_on_thin", {"depends_on": ["base.req"]}, "PM_WORK_START_PREREQUISITES", "dependency:req-login.1"),
    ],
)
def test_admission_rejects_unstartable_work_with_zero_children(
    tmp_path, case, kwargs, expected_code, expected_check
):
    ops, ws = _setup_full_pm_world(tmp_path)
    ctx = _seed_plan_and_work(ops, ws, **kwargs)
    ops_before = ops.db.execute("SELECT COUNT(*) FROM operations").fetchone()[0]
    with pytest.raises(OperationError) as exc_info:
        ops.create(
            OWNER,
            action="work.start",
            target=ctx["target"],
            params=ctx["params"],
            preconditions=ctx["preconditions"],
            idempotency_key=f"k-{case}",
        )
    assert exc_info.value.code == expected_code
    assert expected_check in exc_info.value.message
    if expected_code == "PM_WORK_START_PREREQUISITES":
        assert "pm.work_package.create" in exc_info.value.message
        assert "pm.work_batch.start" in exc_info.value.message
    assert ops.db.execute("SELECT COUNT(*) FROM operations").fetchone()[0] == ops_before
    assert ops.db.execute("SELECT COUNT(*) FROM pm_work_start_children").fetchone()[0] == 0
    assert ops.db.execute("SELECT COUNT(*) FROM work_attempts").fetchone()[0] == 0


def test_reused_package_with_unsatisfied_dependency_rejected_as_not_ready(tmp_path):
    from bat_agent_connector import pm_plans, pm_work_packages

    ops, ws = _setup_full_pm_world(tmp_path)
    ctx = _seed_plan_and_work(ops, ws, depends_on=["base.req"])
    plan = pm_plans.accepted_plan(ops, OWNER, ws)["plan"]
    plan_ref = pm_work_packages._plan_ref(plan)
    code_source = {
        **ctx["params"]["code_target"],
        "source_sha": ctx["preconditions"]["expected_source_sha"],
        "repository_id": ctx["preconditions"]["repository_id"],
        "binding_digest": ctx["preconditions"]["binding_digest"],
    }
    _, composed = pm_work_start.compose_thin_package(
        ops,
        {"title": "Implement login flow"},
        plan,
        ["req-login.1"],
        code_source,
        "pull_request",
        ctx["params"]["instruction"],
    )
    pkg_id = "pmwp_" + "d" * 32
    pkg_op_id = "op_" + "d" * 32
    now = time.time()
    binding_row = ops.db.execute(
        "SELECT * FROM pm_work_bindings WHERE workspace_id=? AND binding_id=?", (ws, ctx["binding_id"])
    ).fetchone()
    binding_obj = {**dict(binding_row), "requirement_ids": json.loads(binding_row["requirement_ids"])}
    srv = pm_workspace.server_id(ops.db)
    pkg_doc = {
        **composed,
        "package_id": pkg_id,
        "workspace_id": ws,
        "work_item_id": ctx["work_item_id"],
        "previous_package_id": None,
        "operation_id": pkg_op_id,
        "created_by": OWNER.actor,
        "created_at": now,
        "schema_version": 1,
        "status": "frozen_engineering_description",
        "execution_authorized": False,
        "execution_spec": "not_assigned",
        "quality_assessment": "unassessed",
        "claims_as_evidence": False,
        "context": {
            "server_id": srv,
            "plan_ref": plan_ref,
            "work_ref": {
                "binding_id": ctx["binding_id"],
                "work_item_id": ctx["work_item_id"],
                "project_id": ctx["project_id"],
                "binding_document_digest": pm_work_packages._digest(binding_obj),
                "work_version": 1,
                "work_fingerprint": ctx["preconditions"]["expected_work_item_fingerprint"],
                "project_version": 1,
            },
            "evidence_refs": [],
            "dod_ref": None,
            "git_binding": None,
            "code_config_digest": pm_work_start.repository_sync.digest(
                pm_work_start.repository_sync.config_snapshot(
                    ops, {"repository": "o/r", "host": "h1", "workspace_id": "w1"}
                )
            ),
            "previous_package_id": None,
        },
        "dod": None,
        "evidence": [],
        "source_inputs": plan["document"]["selected_inputs"],
        "requirement_coverage": "explicit_selected_adopted_requirements",
    }
    digest = pm_work_packages._digest(pkg_doc)
    with ops.journal.tx():
        ops.db.execute(
            "INSERT INTO pm_work_packages VALUES(1,?,?,?,?,?,?,?,?,?)",
            (pkg_id, ws, ctx["work_item_id"], None, digest, pm_work_packages._json(pkg_doc), OWNER.actor, now, pkg_op_id),
        )
    ctx["preconditions"]["expected_previous_package_id"] = pkg_id
    with pytest.raises(OperationError) as exc_info:
        ops.create(
            OWNER,
            action="work.start",
            target=ctx["target"],
            params=ctx["params"],
            preconditions=ctx["preconditions"],
            idempotency_key="k-reuse-dep",
        )
    assert exc_info.value.code == "PM_WORK_START_NOT_READY"
    assert "dependency:req-login.1" in exc_info.value.message
    assert ops.db.execute("SELECT COUNT(*) FROM pm_work_start_children").fetchone()[0] == 0


def test_admission_scopes_membership_preconditions_and_lifecycle_gates(tmp_path):
    ops, ws = _setup_full_pm_world(tmp_path)
    ctx = _seed_plan_and_work(ops, ws)

    # Missing manage scope -> FORBIDDEN
    start_only = Principal("alice", frozenset({"observe", "start"}))
    with pytest.raises(OperationError) as exc_scope:
        ops.create(
            start_only,
            action="work.start",
            target=ctx["target"],
            params=ctx["params"],
            preconditions=ctx["preconditions"],
            idempotency_key="k-scope",
        )
    assert exc_scope.value.code == "FORBIDDEN"

    # Non-member -> NOT_FOUND
    outsider = Principal("eve", frozenset({"observe", "manage", "start"}))
    with pytest.raises(OperationError) as exc_outsider:
        ops.create(
            outsider,
            action="work.start",
            target=ctx["target"],
            params=ctx["params"],
            preconditions=ctx["preconditions"],
            idempotency_key="k-outsider",
        )
    assert exc_outsider.value.code == "NOT_FOUND"

    # Unbound project -> PM_PROJECT_BINDING_REQUIRED
    ctx_unbound_proj = _seed_plan_and_work(ops, ws, bind_project=False, bind_work=False)
    with pytest.raises(OperationError) as exc_proj:
        ops.create(
            OWNER,
            action="work.start",
            target=ctx_unbound_proj["target"],
            params=ctx_unbound_proj["params"],
            preconditions=ctx_unbound_proj["preconditions"],
            idempotency_key="k-no-proj",
        )
    assert exc_proj.value.code == "PM_PROJECT_BINDING_REQUIRED"

    # Restore bound project and check precondition mismatches.
    ctx = _seed_plan_and_work(ops, ws)
    bad_fp = {**ctx["preconditions"], "expected_work_item_fingerprint": "0" * 64}
    with pytest.raises(OperationError) as exc_fp:
        ops.create(
            OWNER,
            action="work.start",
            target=ctx["target"],
            params=ctx["params"],
            preconditions=bad_fp,
            idempotency_key="k-bad-fp",
        )
    assert exc_fp.value.code == "WORK_ITEM_CHANGED"

    bad_scope = {**ctx["preconditions"], "expected_scope_revision": "pmscope_" + "f" * 32}
    with pytest.raises(OperationError) as exc_sc:
        ops.create(
            OWNER,
            action="work.start",
            target=ctx["target"],
            params=ctx["params"],
            preconditions=bad_scope,
            idempotency_key="k-bad-scope",
        )
    assert exc_sc.value.code == "PM_PACKAGE_SCOPE_CHANGED"

    # Lifecycle paused -> PM_LIFECYCLE_STOPPED
    with ops.journal.tx():
        ops.db.execute(
            "INSERT OR REPLACE INTO pm_lifecycle_heads VALUES(?, 'paused', 1, 1, 'pmlt_1', ?, 'explicit')",
            (ws, time.time()),
        )
    with pytest.raises(OperationError) as exc_paused:
        ops.create(
            OWNER,
            action="work.start",
            target=ctx["target"],
            params=ctx["params"],
            preconditions=ctx["preconditions"],
            idempotency_key="k-paused",
        )
    assert exc_paused.value.code == "PM_LIFECYCLE_STOPPED"

    # Lifecycle idea -> explicitly allowed!
    with ops.journal.tx():
        ops.db.execute(
            "INSERT OR REPLACE INTO pm_lifecycle_heads VALUES(?, 'idea', 2, 1, 'pmlt_2', ?, 'explicit')",
            (ws, time.time()),
        )
    op, fresh = ops.create(
        OWNER,
        action="work.start",
        target=ctx["target"],
        params=ctx["params"],
        preconditions=ctx["preconditions"],
        idempotency_key="k-idea",
    )
    assert fresh is True
    assert op["status"] == "accepted"
    assert ops.db.execute("SELECT stage FROM pm_lifecycle_heads WHERE workspace_id=?", (ws,)).fetchone()[0] == "idea"


@pytest.mark.asyncio
async def test_admin_principal_and_concurrent_start_rejection_and_reuse_after_failure(tmp_path):
    from types import SimpleNamespace

    from bat_agent_connector import api_auth, pm_plans, pm_work_packages

    ops, ws = _setup_full_pm_world(tmp_path)
    ctx = _seed_plan_and_work(ops, ws)

    # 1. Admin principal (with empty scopes frozenset but admin=True) can start and resume work.
    admin_token = "adm_secret_token"
    ops.context["daemon"] = SimpleNamespace(_admin_token=admin_token)
    admin_principal = api_auth.authenticate(ops.db, admin_token, admin_token)
    assert admin_principal is not None and admin_principal.admin is True
    # Also test a Principal with admin=True and empty scopes directly.
    minimal_admin = Principal(api_auth.ADMIN_ACTOR, frozenset(), admin=True, credential_id=admin_principal.credential_id)

    op_admin, fresh_admin = ops.create(
        minimal_admin,
        action="work.start",
        target=ctx["target"],
        params=ctx["params"],
        preconditions=ctx["preconditions"],
        idempotency_key="k-admin-1",
    )
    assert fresh_admin is True
    assert op_admin["actor"] == api_auth.ADMIN_ACTOR
    # _current_principal resolves the admin principal via daemon._admin_token and passes _member.
    resolved_admin = pm_work_start._current_principal(ops, op_admin)
    assert resolved_admin.admin is True
    assert resolved_admin.actor == api_auth.ADMIN_ACTOR

    # A withdrawn admin credential pauses in needs_attention; restoring that credential lets admin resume.
    ops.context["daemon"]._admin_token = None
    await ops._execute(op_admin["operation_id"])
    paused_admin = ops.get(op_admin["operation_id"])
    assert paused_admin["status"] == "needs_attention"
    assert paused_admin["error_code"] == "PM_WORK_START_AUTHORITY_UNAVAILABLE"
    ops.context["daemon"]._admin_token = admin_token
    resumed = ops.resume(minimal_admin, op_admin["operation_id"])
    assert resumed["status"] == "running"

    # 2. While op_admin is running (not terminal), a second start with a different key is rejected with PM_WORK_START_IN_PROGRESS.
    with pytest.raises(OperationError) as exc_in_prog:
        ops.create(
            minimal_admin,
            action="work.start",
            target=ctx["target"],
            params=ctx["params"],
            preconditions=ctx["preconditions"],
            idempotency_key="k-admin-concurrent",
        )
    assert exc_in_prog.value.code == "PM_WORK_START_IN_PROGRESS"
    assert op_admin["operation_id"] in exc_in_prog.value.message
    assert ops.db.execute("SELECT COUNT(*) FROM pm_work_start_children").fetchone()[0] == 0

    # Same idempotency_key still replays the original operation rather than hitting PM_WORK_START_IN_PROGRESS.
    replay, fresh_replay = ops.create(
        minimal_admin,
        action="work.start",
        target=ctx["target"],
        params=ctx["params"],
        preconditions=ctx["preconditions"],
        idempotency_key="k-admin-1",
    )
    assert fresh_replay is False
    assert replay["operation_id"] == op_admin["operation_id"]

    # 3. Once the first start is settled in a terminal state, a new start is allowed, links predecessor_attempt_id,
    # and reuses a matching package when present.
    await ops._execute(op_admin["operation_id"])
    ops.cancel(minimal_admin, op_admin["operation_id"])
    assert ops.get(op_admin["operation_id"])["status"] == "cancelled"
    plan = pm_plans.accepted_plan(ops, minimal_admin, ws)["plan"]
    plan_ref = pm_work_packages._plan_ref(plan)
    code_source = {
        **ctx["params"]["code_target"],
        "source_sha": ctx["preconditions"]["expected_source_sha"],
        "repository_id": ctx["preconditions"]["repository_id"],
        "binding_digest": ctx["preconditions"]["binding_digest"],
    }
    _, composed = pm_work_start.compose_thin_package(
        ops,
        {"title": "Implement login flow"},
        plan,
        ["req-login.1"],
        code_source,
        "pull_request",
        ctx["params"]["instruction"],
    )
    pkg_id = "pmwp_" + "e" * 32
    now = time.time()
    binding_row = ops.db.execute(
        "SELECT * FROM pm_work_bindings WHERE workspace_id=? AND binding_id=?", (ws, ctx["binding_id"])
    ).fetchone()
    binding_obj = {**dict(binding_row), "requirement_ids": json.loads(binding_row["requirement_ids"])}
    pkg_doc = {
        **composed,
        "package_id": pkg_id,
        "workspace_id": ws,
        "work_item_id": ctx["work_item_id"],
        "previous_package_id": None,
        "operation_id": "op_" + "e" * 32,
        "created_by": api_auth.ADMIN_ACTOR,
        "created_at": now,
        "schema_version": 1,
        "status": "frozen_engineering_description",
        "execution_authorized": False,
        "execution_spec": "not_assigned",
        "quality_assessment": "unassessed",
        "claims_as_evidence": False,
        "context": {
            "server_id": pm_workspace.server_id(ops.db),
            "plan_ref": plan_ref,
            "work_ref": {
                "binding_id": ctx["binding_id"],
                "work_item_id": ctx["work_item_id"],
                "project_id": ctx["project_id"],
                "binding_document_digest": pm_work_packages._digest(binding_obj),
                "work_version": 1,
                "work_fingerprint": ctx["preconditions"]["expected_work_item_fingerprint"],
                "project_version": 1,
            },
            "evidence_refs": [],
            "dod_ref": None,
            "git_binding": None,
            "code_config_digest": pm_work_start.repository_sync.digest(
                pm_work_start.repository_sync.config_snapshot(
                    ops, {"repository": "o/r", "host": "h1", "workspace_id": "w1"}
                )
            ),
            "previous_package_id": None,
        },
        "dod": None,
        "evidence": [],
        "source_inputs": plan["document"]["selected_inputs"],
        "requirement_coverage": "explicit_selected_adopted_requirements",
    }
    with ops.journal.tx():
        ops.db.execute(
            "INSERT INTO pm_work_packages VALUES(1,?,?,?,?,?,?,?,?,?)",
            (
                pkg_id,
                ws,
                ctx["work_item_id"],
                None,
                pm_work_packages._digest(pkg_doc),
                pm_work_packages._json(pkg_doc),
                api_auth.ADMIN_ACTOR,
                now,
                "op_" + "e" * 32,
            ),
        )

    pre2 = {
        **ctx["preconditions"],
        "expected_previous_package_id": pkg_id,
        "expected_latest_attempt_id": "att_" + op_admin["operation_id"][3:],
    }
    op_admin2, fresh_admin2 = ops.create(
        minimal_admin,
        action="work.start",
        target=ctx["target"],
        params=ctx["params"],
        preconditions=pre2,
        idempotency_key="k-admin-2",
    )
    assert fresh_admin2 is True
    intent2 = op_admin2["external_refs"]["admission_binding"]["work_start"]
    assert intent2["package_plan"]["mode"] == "reuse"
    assert intent2["package_plan"]["package_reused"] is True
    assert intent2["package_plan"]["package_id"] == pkg_id
    attempt2 = ops.db.execute(
        "SELECT * FROM work_attempts WHERE start_operation_id=?", (op_admin2["operation_id"],)
    ).fetchone()
    assert attempt2["predecessor_attempt_id"] == "att_" + op_admin["operation_id"][3:]


def test_concurrent_first_starts_with_null_expected_latest_attempt_id_serialize(tmp_path, monkeypatch):
    ops, ws = _setup_full_pm_world(tmp_path)
    ctx = _seed_plan_and_work(ops, ws)
    assert ctx["preconditions"]["expected_latest_attempt_id"] is None

    # Simulate two concurrent first starts that both pass _check_admission while expected_latest_attempt_id is None;
    # the first commits in the INSERT transaction and the second rolls back in admit_operation.
    both_admitted = [
        pm_work_start._admit(ops, OWNER, ctx["target"], ctx["params"], ctx["preconditions"]),
        pm_work_start._admit(ops, OWNER, ctx["target"], ctx["params"], ctx["preconditions"]),
    ]
    ops.actions[pm_work_start.ACTION] = ActionDef(
        pm_work_start.ACTION,
        "start",
        "Start work",
        pm_work_start._run,
        lambda service, principal, target, params, pre: both_admitted.pop(0),
        ("workspace_id", "work_item_id"),
        authorize_existing=pm_work_start._authorize_existing,
    )

    op1, fresh1 = ops.create(
        OWNER,
        action="work.start",
        target=ctx["target"],
        params=ctx["params"],
        preconditions=ctx["preconditions"],
        idempotency_key="k-conc-first-1",
    )
    assert fresh1 is True
    att1_id = "att_" + op1["operation_id"][3:]
    ops_after_first = ops.db.execute("SELECT COUNT(*) FROM operations").fetchone()[0]
    attempts_after_first = ops.db.execute("SELECT COUNT(*) FROM work_attempts").fetchone()[0]
    assert attempts_after_first == 1

    with pytest.raises(OperationError) as exc_second:
        ops.create(
            OWNER,
            action="work.start",
            target=ctx["target"],
            params=ctx["params"],
            preconditions=ctx["preconditions"],
            idempotency_key="k-conc-first-2",
        )
    assert exc_second.value.code == "PM_WORK_ATTEMPT_CHANGED"
    assert exc_second.value.status == 409
    assert att1_id in exc_second.value.message
    assert op1["operation_id"] in exc_second.value.message
    # Second INSERT rolled back cleanly: no second operation row and no second attempt row.
    assert ops.db.execute("SELECT COUNT(*) FROM operations").fetchone()[0] == ops_after_first
    assert ops.db.execute("SELECT COUNT(*) FROM work_attempts").fetchone()[0] == 1


# ---------------------------------------------------------------------------
# Step 4 fixtures and execution tests (real disk Git + FakeGitHub + mock BAT)
# ---------------------------------------------------------------------------

from bat_agent_connector.errors import InvokeTimeout  # noqa: E402
from tests.operation_helpers import settle_operations  # noqa: E402
from tests.test_checkpoints import bat_writes, git, snapshot  # noqa: E402
from tests.test_pm_package_handoff import dispatch_runner  # noqa: E402
from tests.test_pm_package_handoff import pm as handoff_pm_fixture  # noqa: E402
from tests.test_pm_package_handoff import ready as handoff_ready_fixture  # noqa: E402
from tests.test_pm_package_handoff import world as handoff_world_fixture  # noqa: E402
from tests.test_pm_plan_operations import OWNER as PLAN_OWNER  # noqa: E402
from tests.test_pm_work_packages import action as pkg_action  # noqa: E402
from tests.test_repository_sync import REQUEST as REPO_REQUEST  # noqa: E402

world = handoff_world_fixture
pm = handoff_pm_fixture
ready = handoff_ready_fixture

STARTER_OWNER = Principal(PLAN_OWNER.actor, frozenset({"observe", "manage", "start", "operate"}))


@pytest.fixture
async def start_ready(ready):
    from bat_agent_connector import api_auth

    w = ready
    dispatch_runner(w)
    ops = w["d"].ops
    pm_work_start.install(ops)
    api_auth.issue(ops.db, PLAN_OWNER.actor, ["observe", "manage", "start", "operate"])
    return w


async def _start_envelope(
    w,
    *,
    work_item_id: str | None = None,
    instruction: str = "Implement credential validation and tests.",
    requirement_ids: list[str] | None = None,
    delivery: str = "pull_request",
    agent: str = "claude",
    model: str | None = None,
    source_sha: str | None = None,
    key: str | None = None,
) -> dict:
    from bat_agent_connector import pm_evidence, pm_plans, pm_work_packages, repository_sync, work_items

    ops = w["d"].ops
    wid = w["workspace_id"]
    wi_id = work_item_id or w["target"]["work_item_id"]
    work = work_items._get_item(ops.db, wi_id)
    project = work_items._get_project(ops.db, work["project_id"])
    binding = pm_evidence._binding(ops.db, wid, wi_id)
    plan = pm_plans.accepted_plan(ops, STARTER_OWNER, wid)["plan"]
    pv = await repository_sync.source_preview(ops, REPO_REQUEST)
    code_target = {**pv["target"], "source_ref": pv["source_ref"]}
    preconditions = {
        "expected_work_item_fingerprint": work_items.fingerprint(work),
        "expected_work_version": work["version"],
        "expected_binding_id": binding["binding_id"] if binding else None,
        "expected_scope_revision": plan["adoption"]["scope_revision"],
        "expected_previous_package_id": pm_work_packages._latest(ops.db, wid, wi_id),
        "expected_project_version": project["version"],
        "repository_id": pv["repository_id"],
        "binding_digest": pv["binding_digest"],
        "expected_source_sha": source_sha or pv["source_sha"],
        "expected_latest_attempt_id": pm_work_start.latest_attempt_id(ops.db, wid, wi_id),
    }
    params: dict = {
        "instruction": instruction,
        "agent": agent,
        "delivery": delivery,
        "code_target": code_target,
    }
    if requirement_ids is not None:
        params["requirement_ids"] = requirement_ids
    elif binding is None:
        params["requirement_ids"] = ["member.login"]
    if model is not None:
        params["model"] = model
    return {
        "action": "work.start",
        "target": {"workspace_id": wid, "work_item_id": wi_id},
        "params": params,
        "preconditions": preconditions,
        "idempotency_key": key or uuid.uuid4().hex,
    }


async def _advance_start(ops, op_id: str, rounds: int = 6) -> dict:
    for _ in range(rounds):
        ops.db.execute(
            "UPDATE operations SET next_run_at=0 WHERE operation_id=? AND status IN ('waiting_external','waiting_checks')",
            (op_id,),
        )
        await settle_operations(ops)
        cur = ops.get(op_id)
        if cur["status"] in {"succeeded", "failed", "cancelled", "needs_attention"}:
            break
    cur = ops.get(op_id)
    assert cur["error_code"] != "INTERNAL", [(s["name"], s["error"]) for s in cur["steps"] if s["error"]]
    return cur


async def _hold_before_handoff(w) -> tuple[str, str]:
    """Admit a start, finish its package child, and leave the handoff child uncreated."""
    ops = w["d"].ops
    env = await _start_envelope(w, key=uuid.uuid4().hex)
    parent, _ = ops.create(STARTER_OWNER, **env)
    await ops._execute(parent["operation_id"])
    held = ops.get(parent["operation_id"])
    assert held["status"] == "waiting_external", held
    package_operation = held["result"]["children"]["package"]
    assert package_operation and held["result"]["children"]["handoff"] is None
    await ops._execute(package_operation)
    assert ops.get(package_operation)["status"] == "succeeded"
    assert ops.get(parent["operation_id"])["status"] == "waiting_external"
    attempt_id = "att_" + parent["operation_id"][3:]
    origin = ops.db.execute(
        "SELECT origin_id FROM work_attempts WHERE attempt_id=?", (attempt_id,)
    ).fetchone()
    assert origin["origin_id"] == parent["operation_id"]
    # Child completion wakes the parent. Park it so a later public action's settle
    # cannot admit the handoff child before that action commits.
    _park_until_executed(ops, parent["operation_id"])
    return parent["operation_id"], package_operation


def _park_until_executed(ops, operation_id: str) -> None:
    """Keep one held operation out of settle_operations until the test executes it."""
    ops.db.execute(
        "UPDATE operations SET next_run_at=? WHERE operation_id=? AND status NOT IN ('succeeded','failed','cancelled')",
        (time.time() + 3600, operation_id),
    )


async def _assert_pre_child_failed(w, mock, parent_id: str, package_operation: str, code: str, fragment: str) -> dict:
    ops = w["d"].ops
    frames = len(mock.invokes)
    await ops._execute(parent_id)
    done = ops.get(parent_id)
    assert done["status"] == "failed", done
    assert done["error_code"] == code
    assert fragment in (done["status_reason"] or "")
    assert done["result"]["stage"] == "start_failed"
    assert done["result"]["children"]["package"] == package_operation
    assert done["result"]["children"]["handoff"] is None
    assert ops.get(package_operation)["status"] == "succeeded"
    assert len(mock.invokes) == frames
    return done


async def _admit_next_start(w, parent_id: str, *, requirement_ids: list[str] | None = None) -> dict:
    ops = w["d"].ops
    env = await _start_envelope(w, key=uuid.uuid4().hex, requirement_ids=requirement_ids)
    try:
        nxt, fresh = ops.create(STARTER_OWNER, **env)
    except OperationError as exc:
        raise AssertionError(exc.code) from exc
    assert fresh is True
    assert nxt["operation_id"] != parent_id
    done = await _advance_start(ops, nxt["operation_id"])
    assert done["status"] == "succeeded", done
    assert done["result"]["predecessor_attempt_id"] == "att_" + parent_id[3:]
    return done


async def test_work_start_unbound_and_idea_stage_compose_and_dispatch_session(start_ready, mock):
    from bat_agent_connector import pm_lifecycle_gates, pm_package_handoff, work_items

    w = start_ready
    ops = w["d"].ops
    wid = w["workspace_id"]

    # Create a fresh unbound Work item in the bound project.
    base_work = work_items._get_item(ops.db, w["target"]["work_item_id"])
    unbound_wi = (
        await pkg_action(
            w,
            "work_item.create",
            {"project_id": base_work["project_id"]},
            {
                "title": "Unbound login slice",
                "goal": "Authenticate members",
                "acceptance": "Valid credentials pass, invalid fail",
            },
        )
    )["work_item_id"]

    # Put workspace into 'idea' stage to verify explicit work.start succeeds without mutating stage.
    with ops.journal.tx():
        ops.db.execute(
            "INSERT OR REPLACE INTO pm_lifecycle_heads VALUES(?, 'idea', 2, 1, 'pmlt_idea', ?, 'explicit')",
            (wid, time.time()),
        )

    # Verify manual/foreign sessions already present in ws-1 ("sess-claude-0001", "sess-codex-0002") are untouched.
    manual_ids = {t["id"] for t in mock.ws_doc["terminals"] if t["workspaceId"] == "ws-1"}
    assert "sess-claude-0001" in manual_ids
    human_before = snapshot(w["human"])

    env = await _start_envelope(w, work_item_id=unbound_wi, key="start-unbound-1")
    parent, fresh = ops.create(STARTER_OWNER, **env)
    assert fresh is True
    assert parent["result"]["stage"] == "preparing"

    done = await _advance_start(ops, parent["operation_id"])
    assert done["status"] == "succeeded", done
    res = done["result"]
    assert res["stage"] == "started"
    assert res["attempt_id"] == "att_" + parent["operation_id"][3:]
    assert res["package_reused"] is False
    assert res["composition_profile"] == "work_start_thin_v1"
    assert res["binding_id"].startswith("pmwb_")
    assert res["package_id"].startswith("pmwp_")
    assert res["children"]["bind"] is not None
    assert res["children"]["package"] is not None
    assert res["children"]["handoff"] is not None
    assert res["execution_operation_id"] == res["children"]["handoff"]
    assert res["session"]["host"] == "h1"
    assert isinstance(res["session"]["session_id"], str) and res["session"]["session_id"]
    assert res["session"]["session_id"] not in manual_ids
    assert res["existing_sessions"] == "continue_under_original_authority"

    # Idea stage is preserved unchanged.
    assert pm_lifecycle_gates.head(ops.db, wid)["stage"] == "idea"

    # Human checkout and manual sessions are untouched.
    assert snapshot(w["human"]) == human_before
    for inv in mock.invokes:
        assert inv["params"].get("sessionId") not in manual_ids

    # Prompt sent to the new managed session contains the complete canonical handoff contract.
    handoff_op = ops.get(res["children"]["handoff"])
    saved_contract = pm_package_handoff.binding(handoff_op)["contract"]
    send_inv = next(inv["params"] for inv in mock.invokes if inv["channel"] == "claude:send-message")
    assert pm_package_handoff.canonical(saved_contract) in send_inv["prompt"]
    assert git(handoff_op["result"]["worktree_path"], "rev-parse", "HEAD") == w["sha"]

    # Replay with the same idempotency_key returns the same operation with zero extra BAT writes.
    replay, fresh_replay = ops.create(STARTER_OWNER, **env)
    assert fresh_replay is False
    assert replay["operation_id"] == parent["operation_id"]
    assert mock.channels().count("claude:start-session") == 1
    assert mock.channels().count("claude:send-message") == 1


async def test_failed_handoff_preserves_package_and_retry_reuses_it(start_ready, mock, monkeypatch):
    from bat_agent_connector import pm_work_packages, repository_sync

    w = start_ready
    ops = w["d"].ops
    wid = w["workspace_id"]
    wi_id = w["target"]["work_item_id"]

    # Fail the first handoff during source.resolve (after package creation succeeds, before any BAT session is started).
    orig_folder = mock.ws_doc["workspaces"][0]["folderPath"]
    env1 = await _start_envelope(w, key="start-fail-handoff-1")
    orig_source_preview = repository_sync.source_preview
    preview_calls = 0

    async def move_after_package_preview(*args, **kwargs):
        nonlocal preview_calls
        res = await orig_source_preview(*args, **kwargs)
        preview_calls += 1
        if preview_calls == 1:
            mock.ws_doc["workspaces"][0]["folderPath"] = orig_folder + "-temporarily-moved"
        return res

    monkeypatch.setattr(repository_sync, "source_preview", move_after_package_preview)
    op1, _ = ops.create(STARTER_OWNER, **env1)
    done1 = await _advance_start(ops, op1["operation_id"])
    assert done1["status"] == "failed", done1
    assert done1["error_code"] == "REPOSITORY_BINDING_CHANGED"
    res1 = done1["result"]
    assert res1["stage"] == "start_failed"
    assert res1["package_reused"] is False
    created_pkg_id = res1["package_id"]
    assert isinstance(created_pkg_id, str) and created_pkg_id.startswith("pmwp_")
    assert res1["session"] is None
    assert not bat_writes(mock)
    assert pm_work_packages._latest(ops.db, wid, wi_id) == created_pkg_id

    # Restore workspace folder; op1 reached terminal 'failed' on its own so a fresh start attempt can be submitted directly.
    mock.ws_doc["workspaces"][0]["folderPath"] = orig_folder

    # Second start with same inputs and updated expected_previous_package_id reuses the package.
    env2 = await _start_envelope(w, key="start-retry-reuse-2")
    assert env2["preconditions"]["expected_previous_package_id"] == created_pkg_id
    op2, _ = ops.create(STARTER_OWNER, **env2)
    assert op2["result"]["stage"] == "package_prepared"
    done2 = await _advance_start(ops, op2["operation_id"])
    assert done2["status"] == "succeeded", done2
    res2 = done2["result"]
    assert res2["stage"] == "started"
    assert res2["package_reused"] is True
    assert res2["package_id"] == created_pkg_id
    assert res2["children"]["bind"] is None
    assert res2["children"]["package"] is None
    assert res2["children"]["handoff"] is not None
    assert res2["session"]["session_id"]

    # Only 1 package was ever created for this Work item.
    pkg_count = ops.db.execute(
        "SELECT COUNT(*) FROM pm_work_packages WHERE workspace_id=? AND work_item_id=?",
        (wid, wi_id),
    ).fetchone()[0]
    assert pkg_count == 1


@pytest.mark.parametrize("move_point", ["before_package", "before_handoff"])
async def test_published_head_move_requires_re_review_and_new_start_creates_package_revision(
    start_ready, mock, move_point
):
    from bat_agent_connector import pm_work_packages

    w = start_ready
    ops = w["d"].ops
    wid = w["workspace_id"]

    env1 = await _start_envelope(w, key=f"start-move-{move_point}-1")

    # Prepare a second commit in a separate clone and push to remote.git.
    code = w["human"].parent / f"new-code-{move_point}"
    git(w["human"].parent, "clone", "-q", str(w["remote"]), str(code))
    git(code, "config", "user.name", "fixture")
    git(code, "config", "user.email", "fixture@example.invalid")
    (code / "hello.txt").write_text(f"moved head {move_point}\n")
    git(code, "add", ".")
    git(code, "commit", "-qm", f"Move head {move_point}")
    git(code, "push", "-q", "origin", "main")
    new_sha = git(code, "rev-parse", "HEAD")

    if move_point == "before_package":
        w["state"]["sha"] = new_sha
        op1, _ = ops.create(STARTER_OWNER, **env1)
        done1 = await _advance_start(ops, op1["operation_id"])
        assert done1["status"] == "failed", done1
        assert done1["error_code"] == "PM_PACKAGE_CODE_SOURCE_CHANGED"
        assert "review again" in done1["status_reason"].lower()
        assert done1["result"]["stage"] == "start_failed"
        assert not bat_writes(mock)
        return

    # move_point == "before_handoff": let package creation succeed at old SHA, then move GitHub SHA before handoff resolve.
    w["runner"].before["fetch"] = lambda _: w["state"].__setitem__("sha", new_sha)
    op1, _ = ops.create(STARTER_OWNER, **env1)
    done1 = await _advance_start(ops, op1["operation_id"])
    assert done1["status"] == "failed", done1
    assert done1["error_code"] == "REPOSITORY_REF_CHANGED"
    assert "review again" in done1["status_reason"].lower()
    assert done1["result"]["stage"] == "start_failed"
    first_pkg_id = done1["result"]["package_id"]
    assert isinstance(first_pkg_id, str) and first_pkg_id.startswith("pmwp_")
    assert not bat_writes(mock)

    # op1 reached terminal 'failed' on its own so the user can start again with the reviewed new SHA.
    env2 = await _start_envelope(w, source_sha=new_sha, key="start-move-before_handoff-2")
    assert env2["preconditions"]["expected_previous_package_id"] == first_pkg_id
    op2, _ = ops.create(STARTER_OWNER, **env2)
    done2 = await _advance_start(ops, op2["operation_id"])
    assert done2["status"] == "succeeded", done2
    res2 = done2["result"]
    assert res2["stage"] == "started"
    assert res2["package_reused"] is False
    second_pkg_id = res2["package_id"]
    assert second_pkg_id != first_pkg_id

    # Verify the new package links previous_package_id to first_pkg_id and first_pkg_id is untouched.
    pkg1 = pm_work_packages.get_package(ops, STARTER_OWNER, wid, first_pkg_id)["package"]
    pkg2 = pm_work_packages.get_package(ops, STARTER_OWNER, wid, second_pkg_id)["package"]
    assert pkg1["document"]["code_source"]["source_sha"] == w["sha"]
    assert pkg2["document"]["code_source"]["source_sha"] == new_sha
    assert pkg2["previous_package_id"] == first_pkg_id


async def test_child_outcome_mapping_failed_needs_attention_and_guard_pause(start_ready, mock, monkeypatch):
    from bat_agent_connector import api_auth

    w = start_ready
    ops = w["d"].ops

    env1 = await _start_envelope(w, key="outcome-map-1")
    op1, _ = ops.create(STARTER_OWNER, **env1)

    # 1. A repairable pre-child stop stays needs_attention at the slot's in-progress stage.
    # A work-item version change is unresumable and is covered by its own test.
    assert api_auth.revoke(ops.db, STARTER_OWNER.actor) >= 1
    await ops._execute(op1["operation_id"])
    paused_guard = ops.get(op1["operation_id"])
    assert paused_guard["status"] == "needs_attention"
    assert paused_guard["error_code"] == "PM_WORK_START_AUTHORITY_UNAVAILABLE"
    assert paused_guard["result"]["stage"] == "preparing"
    assert paused_guard["result"]["children"]["package"] is None
    api_auth.issue(ops.db, STARTER_OWNER.actor, ["observe", "manage", "start", "operate"])
    ops.resume(STARTER_OWNER, op1["operation_id"])

    # 2. If a child stops in 'needs_attention', parent also stops in 'needs_attention' with that slot's in-progress stage.
    pkg_act = ops.actions["pm.work_package.create"]
    should_pause_pkg = True

    async def pkg_run_pause_once(ctx):
        nonlocal should_pause_pkg
        if should_pause_pkg:
            should_pause_pkg = False
            raise NeedsAttention("PKG_TEMP_PAUSE", "Temporary package pause")
        return await pkg_act.run(ctx)

    ops.actions["pm.work_package.create"] = ActionDef(
        pkg_act.name,
        pkg_act.scope,
        pkg_act.summary,
        pkg_run_pause_once,
        pkg_act.admit,
        pkg_act.target_keys,
        authorize_existing=pkg_act.authorize_existing,
    )
    try:
        done_na = await _advance_start(ops, op1["operation_id"])
        assert done_na["status"] == "needs_attention"
        assert done_na["error_code"] == "PKG_TEMP_PAUSE"
        assert done_na["result"]["stage"] == "preparing"
        pkg_child_id = done_na["result"]["children"]["package"]
        assert ops.get(pkg_child_id)["status"] == "needs_attention"

        # 3. If the child is cancelled (or fails) with no unproven external step, waking the parent ends parent as 'failed' with stage='start_failed'.
        ops.cancel(STARTER_OWNER, pkg_child_id)
        assert ops.get(pkg_child_id)["status"] == "cancelled"
        ops.resume(STARTER_OWNER, op1["operation_id"])
        done_failed = await _advance_start(ops, op1["operation_id"])
        assert done_failed["status"] == "failed", done_failed
        assert done_failed["error_code"] == "CANCELLED"
        assert done_failed["result"]["stage"] == "start_failed"
    finally:
        ops.actions["pm.work_package.create"] = pkg_act

    # 4. Handoff child in 'needs_attention' keeps stage='starting' on the needs_attention parent.
    handoff_act = ops.actions["repository.continue"]
    should_pause_handoff = True

    async def handoff_run_pause_once(ctx):
        nonlocal should_pause_handoff
        if should_pause_handoff:
            should_pause_handoff = False
            raise NeedsAttention("HANDOFF_TEMP_PAUSE", "Temporary handoff pause")
        return await handoff_act.run(ctx)

    ops.actions["repository.continue"] = ActionDef(
        handoff_act.name,
        handoff_act.scope,
        handoff_act.summary,
        handoff_run_pause_once,
        handoff_act.admit,
        handoff_act.target_keys,
        authorize_existing=handoff_act.authorize_existing,
    )
    try:
        env2 = await _start_envelope(w, key="outcome-map-2")
        op2, _ = ops.create(STARTER_OWNER, **env2)
        handoff_na = await _advance_start(ops, op2["operation_id"])
        assert handoff_na["status"] == "needs_attention"
        assert handoff_na["error_code"] == "HANDOFF_TEMP_PAUSE"
        assert handoff_na["result"]["stage"] == "starting"
        ops.cancel(STARTER_OWNER, op2["operation_id"])
    finally:
        ops.actions["repository.continue"] = handoff_act


@pytest.mark.parametrize("action", ["pause", "close"])
async def test_lifecycle_stop_ends_unresumable_start_failed(start_ready, mock, action):
    from bat_agent_connector import pm_lifecycle_gates
    from tests.test_pm_lifecycle import decide

    w = start_ready
    ops, wid = w["d"].ops, w["workspace_id"]
    stage = pm_lifecycle_gates.head(ops.db, wid)["stage"]
    assert stage in {"idea", "planning", "development"}
    parent_id, package_operation = await _hold_before_handoff(w)
    await decide(ops, wid, action, who=STARTER_OWNER)
    await _assert_pre_child_failed(
        w, mock, parent_id, package_operation, "PM_LIFECYCLE_STOPPED", "stopped new investment",
    )
    with pytest.raises(OperationError) as blocked:
        ops.create(STARTER_OWNER, **await _start_envelope(w, key=uuid.uuid4().hex))
    assert blocked.value.code == "PM_LIFECYCLE_STOPPED"
    await decide(ops, wid, "reopen", who=STARTER_OWNER, params={"stage": stage})
    await _admit_next_start(w, parent_id)


@pytest.mark.parametrize("action", ["pause", "close"])
async def test_lifecycle_epoch_change_ends_unresumable_start_failed(start_ready, mock, action):
    from bat_agent_connector import pm_lifecycle_gates
    from tests.test_pm_lifecycle import decide

    w = start_ready
    ops, wid = w["d"].ops, w["workspace_id"]
    stage = pm_lifecycle_gates.head(ops.db, wid)["stage"]
    assert stage in {"idea", "planning", "development"}
    parent_id, package_operation = await _hold_before_handoff(w)
    await decide(ops, wid, action, who=STARTER_OWNER)
    await decide(ops, wid, "reopen", who=STARTER_OWNER, params={"stage": stage})
    await _assert_pre_child_failed(
        w, mock, parent_id, package_operation, "PM_LIFECYCLE_EPOCH_CHANGED", "earlier execution epoch",
    )
    await _admit_next_start(w, parent_id)


async def test_scope_revision_change_ends_unresumable_start_failed(start_ready, mock):
    from bat_agent_connector import pm_plans
    from tests.test_pm_plan_operations import adopt, draft

    w = start_ready
    ops = w["d"].ops
    parent_id, package_operation = await _hold_before_handoff(w)
    plan = pm_plans.accepted_plan(ops, STARTER_OWNER, w["workspace_id"])["plan"]
    revision = await draft(
        w,
        markdown="# Revised login scope\n\nAdoption remains a separate decision.\n",
        requirements=w["requirements"],
    )
    adopted = await adopt(w, revision)
    assert adopted["status"] == "succeeded", adopted
    assert adopted["result"]["scope_changed"] is not False
    assert adopted["result"]["scope_revision"] != plan["adoption"]["scope_revision"]
    await _assert_pre_child_failed(
        w, mock, parent_id, package_operation, "PM_PACKAGE_SCOPE_CHANGED", "Adopted scope changed",
    )
    # The existing association still names the previous plan revision, so the new
    # start states the current plan's requirement ids and binds them.
    await _admit_next_start(
        w, parent_id, requirement_ids=[row["id"] for row in w["requirements"]],
    )


async def test_document_only_adoption_lets_the_held_start_continue(start_ready, mock):
    from bat_agent_connector import pm_plans
    from tests.test_pm_plan_operations import adopt, draft

    w = start_ready
    ops = w["d"].ops
    parent_id, _package_operation = await _hold_before_handoff(w)
    plan = pm_plans.accepted_plan(ops, STARTER_OWNER, w["workspace_id"])["plan"]
    revision = await draft(
        w,
        title=plan["document"]["title"],
        markdown=plan["document"]["markdown"],
        requirements=w["requirements"],
    )
    adopted = await adopt(w, revision)
    assert adopted["status"] == "succeeded", adopted
    assert adopted["result"]["scope_changed"] is False
    assert adopted["result"]["scope_revision"] == plan["adoption"]["scope_revision"]
    done = await _advance_start(ops, parent_id)
    assert done["status"] == "succeeded", done
    assert done["error_code"] != "PM_PACKAGE_SCOPE_CHANGED"


async def test_work_item_change_ends_unresumable_start_failed(start_ready, mock):
    from bat_agent_connector import work_items

    w = start_ready
    ops = w["d"].ops
    parent_id, package_operation = await _hold_before_handoff(w)
    wi_id = w["target"]["work_item_id"]
    item = work_items._get_item(ops.db, wi_id)
    await pkg_action(
        w, "work_item.update", {"work_item_id": wi_id},
        {"title": "Implement login revised"}, {"expected_version": item["version"]},
    )
    await _assert_pre_child_failed(
        w, mock, parent_id, package_operation, "WORK_ITEM_CHANGED",
        "Work item changed after work.start admission",
    )
    await _admit_next_start(w, parent_id)


async def test_project_change_ends_unresumable_start_failed(start_ready, mock):
    from bat_agent_connector import work_items

    w = start_ready
    ops = w["d"].ops
    parent_id, package_operation = await _hold_before_handoff(w)
    project = work_items._get_project(ops.db, work_items._get_item(
        ops.db, w["target"]["work_item_id"])["project_id"])
    await pkg_action(
        w, "project.update", {"project_id": project["project_id"]},
        {"description": "Revised project note"}, {"expected_version": project["version"]},
    )
    await _assert_pre_child_failed(
        w, mock, parent_id, package_operation, "PROJECT_CHANGED",
        "Project changed after work.start admission",
    )
    await _admit_next_start(w, parent_id)


async def test_work_binding_change_ends_unresumable_start_failed(start_ready, mock):
    from tests.test_pm_work_packages import associate

    w = start_ready
    parent_id, package_operation = await _hold_before_handoff(w)
    await associate(w)
    await _assert_pre_child_failed(
        w, mock, parent_id, package_operation, "PM_WORK_BINDING_CHANGED",
        "Work association changed after work.start admission",
    )
    await _admit_next_start(w, parent_id)


async def test_package_lineage_change_ends_unresumable_start_failed(start_ready, mock):
    from tests.test_pm_work_packages import create as create_package

    w = start_ready
    ops = w["d"].ops
    parent_id, package_operation = await _hold_before_handoff(w)
    package_id = ops.get(package_operation)["result"]["package_id"]
    await create_package(w, previous=package_id)
    await _assert_pre_child_failed(
        w, mock, parent_id, package_operation, "PM_PACKAGE_VERSION_CONFLICT",
        "Work package lineage changed",
    )
    await _admit_next_start(w, parent_id)


async def test_archived_work_item_ends_unresumable_start_failed(start_ready, mock):
    from bat_agent_connector import work_items

    w = start_ready
    ops = w["d"].ops
    parent_id, package_operation = await _hold_before_handoff(w)
    wi_id = w["target"]["work_item_id"]
    item = work_items._get_item(ops.db, wi_id)
    await pkg_action(
        w, "work_item.update", {"work_item_id": wi_id},
        {"archived": True}, {"expected_version": item["version"]},
    )
    await _assert_pre_child_failed(
        w, mock, parent_id, package_operation, "WORK_ITEM_ARCHIVED", "work item is archived",
    )
    with pytest.raises(OperationError) as blocked:
        ops.create(STARTER_OWNER, **await _start_envelope(w, key=uuid.uuid4().hex))
    assert blocked.value.code == "WORK_ITEM_ARCHIVED"
    restored = work_items._get_item(ops.db, wi_id)
    await pkg_action(
        w, "work_item.update", {"work_item_id": wi_id},
        {"archived": False}, {"expected_version": restored["version"]},
    )
    await _admit_next_start(w, parent_id)


async def test_archived_project_ends_unresumable_start_failed(start_ready, mock):
    from bat_agent_connector import work_items

    w = start_ready
    ops = w["d"].ops
    parent_id, package_operation = await _hold_before_handoff(w)
    project = work_items._get_project(ops.db, work_items._get_item(
        ops.db, w["target"]["work_item_id"])["project_id"])
    await pkg_action(
        w, "project.update", {"project_id": project["project_id"]},
        {"archived": True}, {"expected_version": project["version"]},
    )
    await _assert_pre_child_failed(
        w, mock, parent_id, package_operation, "PROJECT_ARCHIVED", "project is archived",
    )
    with pytest.raises(OperationError) as blocked:
        ops.create(STARTER_OWNER, **await _start_envelope(w, key=uuid.uuid4().hex))
    assert blocked.value.code == "PROJECT_ARCHIVED"
    restored = work_items._get_project(ops.db, project["project_id"])
    await pkg_action(
        w, "project.update", {"project_id": project["project_id"]},
        {"archived": False}, {"expected_version": restored["version"]},
    )
    await _admit_next_start(w, parent_id)


async def test_revoked_credential_stays_needs_attention_and_resume_passes(start_ready, mock):
    from bat_agent_connector import api_auth

    w = start_ready
    ops = w["d"].ops
    env = await _start_envelope(w, key="repair-credential")
    op, _ = ops.create(STARTER_OWNER, **env)
    frames = len(mock.invokes)
    assert api_auth.revoke(ops.db, STARTER_OWNER.actor) >= 1
    await ops._execute(op["operation_id"])
    paused = ops.get(op["operation_id"])
    assert paused["status"] == "needs_attention"
    assert paused["error_code"] == "PM_WORK_START_AUTHORITY_UNAVAILABLE"
    assert len(mock.invokes) == frames
    api_auth.issue(ops.db, STARTER_OWNER.actor, ["observe", "manage", "start", "operate"])
    ops.resume(STARTER_OWNER, op["operation_id"])
    done = await _advance_start(ops, op["operation_id"])
    assert done["status"] == "succeeded", done


async def test_lowered_workspace_role_stays_needs_attention_and_resume_passes(start_ready, mock):
    from bat_agent_connector import pm_workspace
    from bat_agent_connector.api_auth import Principal
    from tests.test_pm_work_packages import member

    w = start_ready
    ops, wid = w["d"].ops, w["workspace_id"]
    env = await _start_envelope(w, key="repair-role")
    op, _ = ops.create(STARTER_OWNER, **env)
    _park_until_executed(ops, op["operation_id"])
    await member(w, "second-owner", "owner")
    current = pm_workspace.get_workspace(ops.db, STARTER_OWNER, wid)
    await pkg_action(
        w, "pm.workspace.member.set", {"workspace_id": wid},
        {"actor": STARTER_OWNER.actor, "role": "reader"},
        {"expected_version": current["version"]},
    )
    frames = len(mock.invokes)
    await ops._execute(op["operation_id"])
    paused = ops.get(op["operation_id"])
    assert paused["status"] == "needs_attention"
    assert paused["error_code"] == "FORBIDDEN"
    assert "does not permit" in (paused["status_reason"] or "")
    assert len(mock.invokes) == frames
    second = Principal("second-owner", frozenset({"observe", "manage", "start"}))
    current = pm_workspace.get_workspace(ops.db, second, wid)
    await pkg_action(
        w, "pm.workspace.member.set", {"workspace_id": wid},
        {"actor": STARTER_OWNER.actor, "role": "owner"},
        {"expected_version": current["version"]}, who=second,
    )
    ops.resume(STARTER_OWNER, op["operation_id"])
    done = await _advance_start(ops, op["operation_id"])
    assert done["status"] == "succeeded", done


@pytest.mark.parametrize("channel", ["claude:start-session", "claude:send-message"])
async def test_lost_ack_marks_start_unknown_and_reconciles_without_second_send(start_ready, mock, monkeypatch, channel):
    w = start_ready
    ops = w["d"].ops
    client = w["d"].fleet.client("h1")
    invoke, lost = client.invoke, False

    async def lose_once(name, *args, **kwargs):
        nonlocal lost
        value = await invoke(name, *args, **kwargs)
        if name == channel and not lost:
            lost = True
            raise InvokeTimeout(f"Simulated lost ACK on {channel}")
        return value

    monkeypatch.setattr(client, "invoke", lose_once)
    env = await _start_envelope(w, key=f"start-lost-ack-{channel}")
    parent, _ = ops.create(STARTER_OWNER, **env)

    # Advance until handoff hits the lost ACK and becomes uncertain.
    for _ in range(4):
        ops.db.execute(
            "UPDATE operations SET next_run_at=0 WHERE operation_id=? AND status IN ('waiting_external','waiting_checks')",
            (parent["operation_id"],),
        )
        await settle_operations(ops)
        cur_parent = ops.get(parent["operation_id"])
        if cur_parent["result"] and cur_parent["result"].get("stage") == "start_unknown":
            break

    cur_parent = ops.get(parent["operation_id"])
    assert cur_parent["status"] == "waiting_external"
    assert cur_parent["result"]["stage"] == "start_unknown"
    handoff_id = cur_parent["result"]["children"]["handoff"]
    assert handoff_id is not None

    # Now let the uncertain handoff child reconcile by reading back the sent effect, then wake the parent.
    for _ in range(5):
        ops.db.execute("UPDATE operations SET next_run_at=0 WHERE operation_id=?", (handoff_id,))
        ops.db.execute("UPDATE operations SET next_run_at=0 WHERE operation_id=?", (parent["operation_id"],))
        await settle_operations(ops)
        done = ops.get(parent["operation_id"])
        if done["status"] == "succeeded":
            break

    done = ops.get(parent["operation_id"])
    assert done["status"] == "succeeded", done
    assert done["result"]["stage"] == "started"
    assert done["result"]["session"]["session_id"]
    assert mock.channels().count("claude:start-session") == 1
    assert mock.channels().count("claude:send-message") == 1


async def test_restart_mid_chain_and_parent_cancellation_preserve_child_authority(start_ready, mock):
    from bat_agent_connector import work_items

    w = start_ready
    ops = w["d"].ops

    # 1. Restart mid-chain: execute only the first parent tick (admitting bind child) and bind child completion,
    # then create a brand-new OperationService on the same journal and finish without duplicating children.
    base_work = work_items._get_item(ops.db, w["target"]["work_item_id"])
    wi_restart = (
        await pkg_action(
            w,
            "work_item.create",
            {"project_id": base_work["project_id"]},
            {"title": "Restart mid-chain slice", "goal": "Goal", "acceptance": "Acceptance"},
        )
    )["work_item_id"]
    env_restart = await _start_envelope(w, work_item_id=wi_restart, key="start-restart-mid")
    op_restart, _ = ops.create(STARTER_OWNER, **env_restart)
    await ops._execute(op_restart["operation_id"])
    mid = ops.get(op_restart["operation_id"])
    assert mid["status"] == "waiting_external"
    bind_id = mid["result"]["children"]["bind"]
    assert bind_id is not None
    assert mid["result"]["children"]["package"] is None
    await ops._execute(bind_id)
    assert ops.get(bind_id)["status"] == "succeeded"

    # Instantiate a second OperationService over the same journal (simulating daemon restart).
    ops2 = OperationService(ops.journal, actions=list(ops.actions.values()))
    ops2.context.update(ops.context)
    pm_work_start.install(ops2)
    done_restart = await _advance_start(ops2, op_restart["operation_id"])
    assert done_restart["status"] == "succeeded", done_restart
    assert done_restart["result"]["children"]["bind"] == bind_id
    assert (
        ops.db.execute(
            "SELECT COUNT(*) FROM pm_work_start_children WHERE parent_id=?", (op_restart["operation_id"],)
        ).fetchone()[0]
        == 3
    )

    # 2. Cancelling parent after bind child is created cancels the unfinished bind child and stops new child creation.
    wi_cancel = (
        await pkg_action(
            w,
            "work_item.create",
            {"project_id": base_work["project_id"]},
            {"title": "Cancelled slice", "goal": "Goal", "acceptance": "Acceptance"},
        )
    )["work_item_id"]
    env_cancel = await _start_envelope(w, work_item_id=wi_cancel, key="start-cancel-before-package")
    op_cancel, _ = ops.create(STARTER_OWNER, **env_cancel)
    await ops._execute(op_cancel["operation_id"])
    cancel_bind_id = ops.get(op_cancel["operation_id"])["result"]["children"]["bind"]
    assert cancel_bind_id is not None
    cancelled = ops.cancel(STARTER_OWNER, op_cancel["operation_id"])
    assert cancelled["status"] == "cancelled"
    assert cancelled["result"]["existing_sessions"] == "continue_under_original_authority"
    # Unfinished bind child is cascaded to cancelled and no package/handoff child is created.
    assert ops.get(cancel_bind_id)["status"] == "cancelled"
    assert (
        ops.db.execute(
            "SELECT COUNT(*) FROM pm_work_start_children WHERE parent_id=?", (op_cancel["operation_id"],)
        ).fetchone()[0]
        == 1
    )


async def test_cancel_parent_cancels_needs_attention_child_and_allows_new_start(start_ready, mock):
    w = start_ready
    ops = w["d"].ops

    pkg_act = ops.actions["pm.work_package.create"]
    should_pause_pkg = True

    async def pkg_run_pause_once(ctx):
        nonlocal should_pause_pkg
        if should_pause_pkg:
            should_pause_pkg = False
            raise NeedsAttention("PKG_TEMP_PAUSE", "Temporary package pause")
        return await pkg_act.run(ctx)

    ops.actions["pm.work_package.create"] = ActionDef(
        pkg_act.name,
        pkg_act.scope,
        pkg_act.summary,
        pkg_run_pause_once,
        pkg_act.admit,
        pkg_act.target_keys,
        authorize_existing=pkg_act.authorize_existing,
    )
    try:
        env1 = await _start_envelope(w, key="cancel-na-parent-1")
        op1, _ = ops.create(STARTER_OWNER, **env1)
        paused = await _advance_start(ops, op1["operation_id"])
        assert paused["status"] == "needs_attention"
        pkg_child_id = paused["result"]["children"]["package"]
        assert ops.get(pkg_child_id)["status"] == "needs_attention"

        # Cancelling the needs_attention parent cascades to the needs_attention child (no unproven step)
        # so the attempt is completely settled and a new start can proceed immediately.
        cancelled_parent = ops.cancel(STARTER_OWNER, op1["operation_id"])
        assert cancelled_parent["status"] == "cancelled"
        assert ops.get(pkg_child_id)["status"] == "cancelled"

        env2 = await _start_envelope(w, key="cancel-na-parent-2")
        assert env2["preconditions"]["expected_latest_attempt_id"] == "att_" + op1["operation_id"][3:]
        op2, fresh2 = ops.create(STARTER_OWNER, **env2)
        assert fresh2 is True
        done2 = await _advance_start(ops, op2["operation_id"])
        assert done2["status"] == "succeeded", done2
        assert done2["result"]["predecessor_attempt_id"] == "att_" + op1["operation_id"][3:]
        assert done2["result"]["session"]["session_id"]
    finally:
        ops.actions["pm.work_package.create"] = pkg_act


async def test_active_parent_with_pending_cancel_ends_cancelled_not_failed(start_ready, mock):
    w = start_ready
    ops = w["d"].ops

    pkg_act = ops.actions["pm.work_package.create"]
    child_entered = asyncio.Event()
    child_release = asyncio.Event()
    calls = 0

    async def pkg_run_controlled(ctx):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise NeedsAttention("PKG_HOLD", "Initial pause before active cancel")
        child_entered.set()
        await child_release.wait()
        ctx.check_cancel()
        return await pkg_act.run(ctx)

    ops.actions["pm.work_package.create"] = ActionDef(
        pkg_act.name,
        pkg_act.scope,
        pkg_act.summary,
        pkg_run_controlled,
        pkg_act.admit,
        pkg_act.target_keys,
        authorize_existing=pkg_act.authorize_existing,
    )
    try:
        env1 = await _start_envelope(w, key="active-cancel-parent-1")
        op1, _ = ops.create(STARTER_OWNER, **env1)
        paused = await _advance_start(ops, op1["operation_id"])
        assert paused["status"] == "needs_attention"
        pkg_child_id = paused["result"]["children"]["package"]
        assert ops.get(pkg_child_id)["status"] == "needs_attention"

        # Resume the child into an active run waiting on child_release, then cancel the child directly.
        ops.resume(STARTER_OWNER, pkg_child_id)
        await ops.run_due()
        await child_entered.wait()
        assert ops._running(pkg_child_id) is True
        ops.cancel(STARTER_OWNER, pkg_child_id)
        child_release.set()
        await ops.drain()
        assert ops.get(pkg_child_id)["status"] == "cancelled"

        # Resume the parent, start its active run via run_due, and cancel the parent while its run is active.
        ops.resume(STARTER_OWNER, op1["operation_id"])
        await ops.run_due()
        assert ops._running(op1["operation_id"]) is True
        ops.cancel(STARTER_OWNER, op1["operation_id"])
        await ops.drain()

        settled_parent = ops.get(op1["operation_id"])
        assert settled_parent["status"] == "cancelled", settled_parent
    finally:
        ops.actions["pm.work_package.create"] = pkg_act


async def test_lost_ack_after_fast_reuse_start_refused_by_expected_latest_attempt_id(start_ready, mock):
    w = start_ready
    ops = w["d"].ops
    wid = w["workspace_id"]
    wi_id = w["target"]["work_item_id"]

    # Create the engineering package ahead of time so the first work.start takes the fast reuse path.
    pv_compose = await pm_work_start.preview(
        ops, STARTER_OWNER, wid, wi_id, {"instruction": "Fast reuse start."}
    )
    assert pv_compose["package_plan"]["mode"] == "compose"
    pkg_op, _ = ops.create(
        STARTER_OWNER,
        action="pm.work_package.create",
        target={"workspace_id": wid, "work_item_id": wi_id},
        params=pv_compose["package_plan"]["description"],
        preconditions={
            "expected_binding_id": pv_compose["preconditions"]["expected_binding_id"],
            "expected_work_fingerprint": pv_compose["preconditions"]["expected_work_item_fingerprint"],
            "expected_scope_revision": pv_compose["preconditions"]["expected_scope_revision"],
            "expected_previous_package_id": None,
        },
        idempotency_key="precreate-pkg-for-reuse",
    )
    await settle_operations(ops)
    pkg_id = ops.get(pkg_op["operation_id"])["result"]["package_id"]

    pv_reuse = await pm_work_start.preview(
        ops, STARTER_OWNER, wid, wi_id, {"instruction": "Fast reuse start."}
    )
    assert pv_reuse["package_plan"]["mode"] == "reuse"
    assert pv_reuse["package_plan"]["package_id"] == pkg_id
    assert pv_reuse["preconditions"]["expected_latest_attempt_id"] is None

    start_params = {
        "instruction": "Fast reuse start.",
        "agent": "claude",
        "delivery": "pull_request",
        "code_target": pv_reuse["code_target"],
    }
    op1, fresh1 = ops.create(
        STARTER_OWNER,
        action="work.start",
        target={"workspace_id": wid, "work_item_id": wi_id},
        params=start_params,
        preconditions=pv_reuse["preconditions"],
        idempotency_key="fast-reuse-key-1",
    )
    assert fresh1 is True
    done1 = await _advance_start(ops, op1["operation_id"])
    assert done1["status"] == "succeeded", done1
    assert done1["result"]["package_reused"] is True
    att1_id = done1["result"]["attempt_id"]
    assert mock.channels().count("claude:start-session") == 1
    assert (
        ops.db.execute(
            "SELECT COUNT(*) FROM work_attempts WHERE workspace_id=? AND work_item_id=?", (wid, wi_id)
        ).fetchone()[0]
        == 1
    )
    assert ops.db.execute("SELECT COUNT(*) FROM pm_work_start_children").fetchone()[0] == 1

    # Simulate UI losing the ACK after the fast reuse start succeeded and resubmitting with a new key
    # and the original preconditions (expected_latest_attempt_id=None).
    with pytest.raises(OperationError) as exc_resubmit:
        ops.create(
            STARTER_OWNER,
            action="work.start",
            target={"workspace_id": wid, "work_item_id": wi_id},
            params=start_params,
            preconditions=pv_reuse["preconditions"],
            idempotency_key="fast-reuse-key-2",
        )
    assert exc_resubmit.value.code == "PM_WORK_ATTEMPT_CHANGED"
    assert exc_resubmit.value.status == 409
    assert att1_id in exc_resubmit.value.message
    assert op1["operation_id"] in exc_resubmit.value.message
    assert ops.db.execute("SELECT COUNT(*) FROM pm_work_start_children").fetchone()[0] == 1
    assert mock.channels().count("claude:start-session") == 1
    assert (
        ops.db.execute(
            "SELECT COUNT(*) FROM work_attempts WHERE workspace_id=? AND work_item_id=?", (wid, wi_id)
        ).fetchone()[0]
        == 1
    )


async def test_unsettled_latest_attempt_blocks_new_start_for_parent_child_and_unproven_outcome(
    start_ready, mock, monkeypatch
):
    from bat_agent_connector import api_auth

    w = start_ready
    ops = w["d"].ops

    env1 = await _start_envelope(w, key="settle-check-1")
    op1, _ = ops.create(STARTER_OWNER, **env1)

    # 1. Parent in needs_attention is NOT terminal (can be resumed) -> refused with PM_WORK_START_IN_PROGRESS.
    assert api_auth.revoke(ops.db, STARTER_OWNER.actor) >= 1
    await ops._execute(op1["operation_id"])
    paused_parent = ops.get(op1["operation_id"])
    assert paused_parent["status"] == "needs_attention"
    assert paused_parent["error_code"] == "PM_WORK_START_AUTHORITY_UNAVAILABLE"

    env_retry = await _start_envelope(w, key="settle-check-while-paused")
    with pytest.raises(OperationError) as exc_paused:
        ops.create(STARTER_OWNER, **env_retry)
    assert exc_paused.value.code == "PM_WORK_START_IN_PROGRESS"
    assert exc_paused.value.status == 409
    assert op1["operation_id"] in exc_paused.value.message

    # Resume parent and advance until handoff child hits a lost ACK on claude:start-session (uncertain)
    # and subsequent read-backs (workspace:get-state) also time out while BAT is unreachable.
    api_auth.issue(ops.db, STARTER_OWNER.actor, ["observe", "manage", "start", "operate"])
    ops.resume(STARTER_OWNER, op1["operation_id"])
    client = w["d"].fleet.client("h1")
    invoke, lost = client.invoke, False

    async def lose_start_and_readback(name, *args, **kwargs):
        nonlocal lost
        if name == "claude:start-session" and not lost:
            await invoke(name, *args, **kwargs)
            lost = True
            raise InvokeTimeout("Simulated lost ACK on claude:start-session")
        if lost and name == "claude:get-session-meta":
            raise InvokeTimeout("Simulated read-back timeout on claude:get-session-meta")
        return await invoke(name, *args, **kwargs)

    monkeypatch.setattr(client, "invoke", lose_start_and_readback)
    for _ in range(4):
        ops.db.execute(
            "UPDATE operations SET next_run_at=0 WHERE operation_id=? AND status IN ('waiting_external','waiting_checks')",
            (op1["operation_id"],),
        )
        await settle_operations(ops)
        cur = ops.get(op1["operation_id"])
        if cur["result"] and cur["result"].get("stage") == "start_unknown":
            break

    cur = ops.get(op1["operation_id"])
    handoff_id = cur["result"]["children"]["handoff"]
    assert handoff_id is not None
    assert ops.get(handoff_id)["status"] == "uncertain"

    # 2. Cancel parent while handoff child is still 'uncertain' (actively reading back):
    # parent is now 'cancelled', child is 'uncertain' (not in TERMINAL), so new start is refused with PM_WORK_START_IN_PROGRESS.
    ops.cancel(STARTER_OWNER, op1["operation_id"])
    assert ops.get(op1["operation_id"])["status"] == "cancelled"
    assert ops.get(handoff_id)["status"] == "uncertain"
    with pytest.raises(OperationError) as exc_child_running:
        ops.create(STARTER_OWNER, **(await _start_envelope(w, key="settle-check-child-uncertain")))
    assert exc_child_running.value.code == "PM_WORK_START_IN_PROGRESS"
    assert op1["operation_id"] in exc_child_running.value.message

    # 3. Exhaust the uncertain child's read-back budget so it enters 'needs_attention' (UNCERTAIN_UNRESOLVED),
    # then cancel the child so it enters terminal 'cancelled' with its external step still unproven (_uncertain == True).
    for _ in range(5):
        ops.db.execute("UPDATE operations SET next_run_at=0 WHERE operation_id=?", (handoff_id,))
        await settle_operations(ops)
        if ops.get(handoff_id)["status"] == "needs_attention":
            break
    assert ops.get(handoff_id)["status"] == "needs_attention"
    assert ops.get(handoff_id)["error_code"] == "UNCERTAIN_UNRESOLVED"

    # Even while child is in needs_attention with an unproven step, starting again is refused with PM_WORK_START_OUTCOME_UNPROVEN.
    with pytest.raises(OperationError) as exc_unproven_na:
        ops.create(STARTER_OWNER, **(await _start_envelope(w, key="settle-check-unproven-na")))
    assert exc_unproven_na.value.code == "PM_WORK_START_OUTCOME_UNPROVEN"
    assert handoff_id in exc_unproven_na.value.message

    # Cancel the needs_attention child -> it transitions to terminal 'cancelled' while its step remains unproven.
    ops.cancel(STARTER_OWNER, handoff_id)
    assert ops.get(handoff_id)["status"] == "cancelled"
    assert pm_work_start._uncertain(ops, ops.get(handoff_id)) is True

    with pytest.raises(OperationError) as exc_unproven:
        ops.create(STARTER_OWNER, **(await _start_envelope(w, key="settle-check-unproven")))
    assert exc_unproven.value.code == "PM_WORK_START_OUTCOME_UNPROVEN"
    assert exc_unproven.value.status == 409
    assert handoff_id in exc_unproven.value.message
    assert "may have started a session" in exc_unproven.value.message
    assert "BAT" in exc_unproven.value.message


class _ProcessStopped(BaseException):
    """The connector process died. step() must not record this as a BAT outcome."""


def _handoff_id(ops, parent_id):
    row = ops.db.execute(
        "SELECT operation_id FROM pm_work_start_children WHERE parent_id=? AND slot='handoff'",
        (parent_id,),
    ).fetchone()
    return row["operation_id"] if row else None


def _release_parent_waits(ops, parent_id):
    ids = [parent_id]
    ids.extend(
        row["operation_id"]
        for row in ops.db.execute(
            "SELECT operation_id FROM pm_work_start_children WHERE parent_id=?", (parent_id,)
        )
    )
    placeholders = ",".join("?" * len(ids))
    ops.db.execute(
        f"UPDATE operations SET next_run_at=0 WHERE operation_id IN ({placeholders}) "  # noqa: S608
        "AND status IN ('waiting_external','waiting_checks')",
        ids,
    )


def _session_start_row(ops, handoff_id):
    return ops.db.execute(
        "SELECT status, error FROM operation_steps WHERE operation_id=? AND name='session.start'",
        (handoff_id,),
    ).fetchone()


async def _until_session_start(ops, parent_id, status):
    for _ in range(12):
        _release_parent_waits(ops, parent_id)
        await settle_operations(ops)
        handoff_id = _handoff_id(ops, parent_id)
        row = _session_start_row(ops, handoff_id) if handoff_id else None
        if row is not None and row["status"] == status:
            return handoff_id
    raise AssertionError(f"session.start did not become {status}")


def _install_prereserve_fault(monkeypatch, ops, fault):
    """Fail the session.start git:getRoot that runs after the step intent and before reserve."""
    from bat_agent_connector.errors import ConnectionLost

    client = ops.context["fleet"].client("h1")
    original = client.invoke
    fired = {"done": False}

    async def invoke(channel, params=None, **kwargs):
        if channel == "git:getRoot" and not fired["done"]:
            open_start = ops.db.execute(
                "SELECT 1 FROM operation_steps WHERE name='session.start' AND status='started' LIMIT 1"
            ).fetchone()
            if open_start is not None:
                fired["done"] = True
                if fault == "drop":
                    raise ConnectionLost("git:getRoot dropped before reservation")
                raise _ProcessStopped("stopped after session.start intent and before reservation")
        return await original(channel, params, **kwargs)

    monkeypatch.setattr(client, "invoke", invoke)
    return fired


async def _drive_until_process_stops(ops, parent_id):
    """Run until the handoff dies, and do not schedule that still-running row again."""
    stopped = False
    for _ in range(40):
        if not stopped:
            await ops.run_due()
        running = [task for task in ops._active.values() if not task.done()]
        if not running:
            if stopped:
                return _handoff_id(ops, parent_id)
            _release_parent_waits(ops, parent_id)
            continue
        done, _pending = await asyncio.wait(running, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            if task.cancelled():
                continue
            exc = task.exception()
            if isinstance(exc, _ProcessStopped):
                stopped = True
            elif exc is not None:
                raise exc
    raise AssertionError("connector did not stop before the published reservation")


def _restart_service(ops):
    restarted = OperationService(ops.journal, actions=list(ops.actions.values()))
    restarted.context.update(ops.context)
    pm_work_start.install(restarted)
    return restarted


def _rerun_problems(ops, parent_id, handoff_id, frames):
    done = ops.get(parent_id)
    handoff = ops.get(handoff_id)
    step = _session_start_row(ops, handoff_id)
    problems = []
    if done["status"] != "succeeded":
        problems.append(f"parent {done['status']} {done.get('error_code')}")
    if handoff["status"] != "succeeded":
        problems.append(f"handoff {handoff['status']} {handoff.get('error_code')} step {step['status']}")
    if frames != 1:
        problems.append(f"start frames {frames}")
    if step["status"] != "succeeded":
        problems.append(f"session.start {step['status']} {step['error']}")
    return problems


async def _refuse_reason(w, ops, key):
    try:
        second, fresh = ops.create(STARTER_OWNER, **(await _start_envelope(w, key=key)))
    except OperationError as exc:
        return f"new work.start refused {exc.code}"
    if not fresh or second["action"] != "work.start":
        return "new work.start was not admitted"
    return None


async def test_unreserved_published_start_drop_reruns_once_and_allows_another_work_start(start_ready, mock, monkeypatch):
    """A dropped git:getRoot before reservation sends no frame; the read-back sends one."""
    from bat_agent_connector import registry
    from tests.operation_helpers import stop_workers

    w = start_ready
    ops = w["d"].ops
    try:
        _install_prereserve_fault(monkeypatch, ops, "drop")
        env = await _start_envelope(w, key="start-unreserved-drop")
        parent, _fresh = ops.create(STARTER_OWNER, **env)
        handoff_id = await _until_session_start(ops, parent["operation_id"], "uncertain")
        handoff = ops.get(handoff_id)
        sid = handoff["external_refs"]["session_id"]
        assert registry.get("h1", sid) is None
        assert "repository_reservation" not in (handoff["external_refs"] or {})
        assert mock.channels().count("claude:start-session") == 0
        assert ops.get(parent["operation_id"])["status"] == "waiting_external"

        ops.db.execute("UPDATE operations SET next_run_at=0 WHERE operation_id=?", (handoff_id,))
        await _advance_start(ops, parent["operation_id"])
        problems = _rerun_problems(
            ops, parent["operation_id"], handoff_id, mock.channels().count("claude:start-session")
        )
        refused = await _refuse_reason(w, ops, "start-after-unreserved-drop")
        if refused:
            problems.append(refused)
        assert not problems, "; ".join(problems)
        assert registry.get("h1", sid)["start_sent"] is True
    finally:
        await stop_workers(ops)


async def test_unreserved_published_start_process_stop_reruns_on_recovery(start_ready, mock, monkeypatch):
    """A process death after the start intent and before reserve leaves a runnable unsent step."""
    from bat_agent_connector import registry
    from tests.operation_helpers import stop_workers

    w = start_ready
    ops = w["d"].ops
    restarted = None
    try:
        _install_prereserve_fault(monkeypatch, ops, "stop")
        env = await _start_envelope(w, key="start-unreserved-stop")
        parent, _fresh = ops.create(STARTER_OWNER, **env)
        handoff_id = await _drive_until_process_stops(ops, parent["operation_id"])
        handoff = ops.get(handoff_id)
        step = _session_start_row(ops, handoff_id)
        sid = handoff["external_refs"]["session_id"]
        assert step["status"] == "started", step
        assert handoff["status"] == "running", handoff
        assert registry.get("h1", sid) is None
        assert "repository_reservation" not in (handoff["external_refs"] or {})
        assert mock.channels().count("claude:start-session") == 0
        assert ops.get(parent["operation_id"])["status"] == "waiting_external"
        await stop_workers(ops)

        restarted = _restart_service(ops)
        await _advance_start(restarted, parent["operation_id"])
        problems = _rerun_problems(
            restarted, parent["operation_id"], handoff_id, mock.channels().count("claude:start-session")
        )
        refused = await _refuse_reason(w, restarted, "start-after-unreserved-stop")
        if refused:
            problems.append(refused)
        assert not problems, "; ".join(problems)
        assert registry.get("h1", sid)["start_sent"] is True
    finally:
        if restarted is not None:
            await stop_workers(restarted)
        await stop_workers(ops)


async def test_replaced_unsent_reservation_keeps_unproven_start_locked(start_ready, mock, monkeypatch):
    """A second crash between reserve and its receipt must not settle the first start."""
    from bat_agent_connector import registry
    from bat_agent_connector.operations import OpContext
    from tests.operation_helpers import stop_workers

    w = start_ready
    original = w["d"].ops
    services = [original]
    set_refs = OpContext.set_refs
    reservations = []

    def crash_at_reservation_receipt(ctx, **refs):
        if "repository_reservation" not in refs:
            return set_refs(ctx, **refs)
        reservations.append(refs["repository_reservation"])
        if len(reservations) == 1:
            set_refs(ctx, **refs)
            raise _ProcessStopped("first reservation receipt is durable; no start frame sent")
        if len(reservations) == 2:
            raise _ProcessStopped("replacement reservation is durable; receipt write not reached")
        return set_refs(ctx, **refs)

    monkeypatch.setattr(OpContext, "set_refs", crash_at_reservation_receipt)
    try:
        parent, _fresh = original.create(
            STARTER_OWNER, **(await _start_envelope(w, key="start-replaced-unsent-reservation"))
        )
        parent_id = parent["operation_id"]
        handoff_id = await _drive_until_process_stops(original, parent_id)
        first = original.get(handoff_id)
        sid = first["external_refs"]["session_id"]
        receipt = first["external_refs"]["repository_reservation"]
        entry = registry.get("h1", sid)
        assert len(reservations) == 1 and receipt == reservations[0]
        assert entry["created_at"] == receipt["created_at"] and entry["start_sent"] is False
        assert _session_start_row(original, handoff_id)["status"] == "started"
        assert mock.channels().count("claude:start-session") == 0
        await stop_workers(original)

        first_recovery = _restart_service(original)
        services.append(first_recovery)
        assert await _drive_until_process_stops(first_recovery, parent_id) == handoff_id
        replaced = registry.get("h1", sid)
        assert len(reservations) == 2
        assert replaced["created_at"] == reservations[1]["created_at"] != receipt["created_at"]
        assert replaced["start_sent"] is False
        assert first_recovery.get(handoff_id)["external_refs"]["repository_reservation"] == receipt
        assert _session_start_row(first_recovery, handoff_id)["status"] == "started"
        assert mock.channels().count("claude:start-session") == 0
        await stop_workers(first_recovery)

        monkeypatch.setattr(OpContext, "set_refs", set_refs)
        second_recovery = _restart_service(first_recovery)
        services.append(second_recovery)
        await _advance_start(second_recovery, parent_id)
        handoff = second_recovery.get(handoff_id)
        step = _session_start_row(second_recovery, handoff_id)
        parent_after = second_recovery.get(parent_id)
        assert handoff["status"] == "needs_attention", handoff
        assert handoff["error_code"] == "REPOSITORY_START_UNPROVEN"
        # A reconciliation refusal preserves the original open step. It must
        # not settle it as failed and release the Work's unproven-start lock.
        assert step["status"] == "started" and step["error"] is None, dict(step)
        assert parent_after["status"] == "needs_attention", parent_after
        assert parent_after["error_code"] == "REPOSITORY_START_UNPROVEN"
        assert _handoff_id(second_recovery, parent_id) == handoff_id
        assert handoff["idempotency_key"] == first["idempotency_key"]
        assert handoff["external_refs"]["repository_reservation"] == receipt
        assert registry.get("h1", sid) == replaced
        assert mock.channels().count("claude:start-session") == 0
        assert mock.channels().count("claude:send-message") == 0
        assert await _refuse_reason(w, second_recovery, "start-after-replaced-reservation") == (
            "new work.start refused PM_WORK_START_OUTCOME_UNPROVEN"
        )
    finally:
        for service in reversed(services):
            await stop_workers(service)


async def test_unreserved_published_start_cancel_before_reservation_is_proven_failure(start_ready, mock, monkeypatch):
    """Cancel reaches the step only after read-back proves the start frame never left."""
    from bat_agent_connector import registry
    from tests.operation_helpers import stop_workers

    w = start_ready
    ops = w["d"].ops
    try:
        _install_prereserve_fault(monkeypatch, ops, "drop")
        parent, _fresh = ops.create(STARTER_OWNER, **(await _start_envelope(w, key="start-unreserved-cancel")))
        handoff_id = await _until_session_start(ops, parent["operation_id"], "uncertain")
        assert mock.channels().count("claude:start-session") == 0
        ops.cancel(STARTER_OWNER, handoff_id)
        await _advance_start(ops, parent["operation_id"])
        handoff = ops.get(handoff_id)
        step = _session_start_row(ops, handoff_id)
        parent_done = ops.get(parent["operation_id"])
        err = json.loads(step["error"] or "{}")
        problems = []
        if handoff["status"] != "cancelled":
            problems.append(f"handoff {handoff['status']} {handoff.get('error_code')}")
        if step["status"] != "failed" or err.get("code") != "CANCELLED":
            problems.append(f"session.start {step['status']} {step['error']}")
        if mock.channels().count("claude:start-session") != 0:
            problems.append(f"start frames {mock.channels().count('claude:start-session')}")
        if parent_done["status"] not in {"failed", "cancelled"}:
            problems.append(f"parent {parent_done['status']} {parent_done.get('error_code')}")
        refused = await _refuse_reason(w, ops, "start-after-unreserved-cancel")
        if refused:
            problems.append(refused)
        assert not problems, "; ".join(problems)
        assert registry.get("h1", handoff["external_refs"]["session_id"]) is None
    finally:
        await stop_workers(ops)


# ---------------------------------------------------------------------------
# Step 5 tests: STARTS_SESSION, visibility, events, lineage, and child guards
# ---------------------------------------------------------------------------


async def test_starts_session_repository_continue_links_and_unchanged_other_links(start_ready, mock, monkeypatch):
    from bat_agent_connector import pm_source_lineage, work_items

    w = start_ready
    ops = w["d"].ops
    wi_id = w["target"]["work_item_id"]

    env = await _start_envelope(w, key="start-links-1")
    parent, _ = ops.create(STARTER_OWNER, **env)
    done = await _advance_start(ops, parent["operation_id"])
    assert done["status"] == "succeeded", done
    handoff_id = done["result"]["execution_operation_id"]
    expected_session = done["result"]["session"]

    # 1. Work read includes operation link for repository.continue with session: {host, session_id}.
    wi_doc = work_items.work_item_get(ops.db, wi_id)
    op_links = [lnk for lnk in wi_doc["links"] if lnk["kind"] == "operation" and lnk["ref"] == handoff_id]
    assert len(op_links) == 1
    current_summary = op_links[0]["target"]
    assert current_summary["action"] == "repository.continue"
    assert current_summary["session"] == expected_session

    # Compare against legacy STARTS_SESSION tuple without repository.continue: only the "session" key changes!
    monkeypatch.setattr(work_items, "STARTS_SESSION", ("checkpoint.continue", "integration.handoff"))
    legacy_summary = work_items._link_summary(ops.db, "operation", handoff_id)
    assert {k: v for k, v in current_summary.items() if k != "session"} == {
        k: v for k, v in legacy_summary.items() if k != "session"
    }
    assert legacy_summary["session"] is None

    # 2. Links of other actions are 100% byte-identical before and after adding repository.continue to STARTS_SESSION.
    now = time.time()
    other_ops = [
        ("op_" + "1" * 32, "checkpoint.continue", json.dumps({"host": "h1", "session_id": "s-cp"})),
        ("op_" + "2" * 32, "integration.handoff", json.dumps({"host": "h1", "session_id": "s-int"})),
        ("op_" + "3" * 32, "work_item.create", json.dumps({"work_item_id": wi_id})),
        ("op_" + "4" * 32, "session.send", json.dumps({"host": "h1", "session_id": "s-send"})),
    ]
    with ops.journal.tx():
        for oid, act, res_json in other_ops:
            ops.db.execute(
                """INSERT INTO operations(
                    operation_id,actor,entry,idem_key,request_hash,action,target,params,preconditions,
                    status,result,created_at,updated_at
                ) VALUES(?,?, 'test', ?, 'h', ?, '{}', '{}', '{}', 'succeeded', ?, ?, ?)""",
                (oid, STARTER_OWNER.actor, "k-" + oid, act, res_json, now, now),
            )
    legacy_other = [work_items._link_summary(ops.db, "operation", oid) for oid, _, _ in other_ops]
    monkeypatch.setattr(
        work_items, "STARTS_SESSION", ("checkpoint.continue", "integration.handoff", "repository.continue")
    )
    new_other = [work_items._link_summary(ops.db, "operation", oid) for oid, _, _ in other_ops]
    assert new_other == legacy_other

    # 3. Visibility is not widened: non-member still fails pm_source_lineage.require on the Work item and session.
    outsider = Principal("outsider", frozenset({"observe", "manage", "start", "operate"}))
    pm_source_lineage.require(ops.db, STARTER_OWNER, "work_item", wi_id)
    pm_source_lineage.require(ops.db, STARTER_OWNER, "session", f"h1/{expected_session['session_id']}")
    with pytest.raises(OperationError) as exc_wi:
        pm_source_lineage.require(ops.db, outsider, "work_item", wi_id)
    assert exc_wi.value.code == "NOT_FOUND"
    assert exc_wi.value.status == 404
    with pytest.raises(OperationError) as exc_sess:
        pm_source_lineage.require(ops.db, outsider, "session", f"h1/{expected_session['session_id']}")
    assert exc_sess.value.code == "NOT_FOUND"
    assert exc_sess.value.status == 404


async def test_visibility_events_and_late_child_guard(start_ready, mock):
    from bat_agent_connector import pm_access, pm_workspace, work_items

    w = start_ready
    ops = w["d"].ops
    wid = w["workspace_id"]
    outsider = Principal("outsider", frozenset({"observe", "manage", "start", "operate"}))

    env = await _start_envelope(w, key="start-vis-1")
    parent, _ = ops.create(STARTER_OWNER, **env)
    done = await _advance_start(ops, parent["operation_id"])
    assert done["status"] == "succeeded", done

    # 1. Member sees parent operation via pm_workspace.operation_visible and pm_access.visible; non-member gets False / 404.
    assert pm_workspace.operation_visible(ops.db, STARTER_OWNER, done) is True
    assert pm_access.visible(ops.db, STARTER_OWNER, done) is True
    pm_access.require_visible(ops.db, STARTER_OWNER, done)

    assert pm_workspace.operation_visible(ops.db, outsider, done) is False
    assert pm_access.visible(ops.db, outsider, done) is False
    with pytest.raises(OperationError) as exc_vis:
        pm_access.require_visible(ops.db, outsider, done)
    assert exc_vis.value.code == "NOT_FOUND"
    assert exc_vis.value.status == 404

    # 2. Events: member sees both operation events and pm_work_attempt ('work.start.succeeded') event;
    # non-member sees neither while preserving page next_cursor.
    raw_page = ops.journal.api_events(0, limit=500)
    member_page = pm_access.filter_events(ops.db, STARTER_OWNER, raw_page)
    outsider_page = pm_access.filter_events(ops.db, outsider, raw_page)
    assert outsider_page["next_cursor"] == raw_page["next_cursor"]
    assert any(
        e["resource_type"] == "pm_work_attempt" and e["kind"] == "work.start.succeeded"
        for e in member_page["events"]
    )
    assert any(
        e["resource_type"] == "operation" and e["resource_id"] == done["operation_id"]
        for e in member_page["events"]
    )
    assert not any(e["resource_type"] == "pm_work_attempt" for e in outsider_page["events"])
    assert not any(
        e["resource_type"] == "operation" and e["resource_id"] == done["operation_id"]
        for e in outsider_page["events"]
    )

    # 3. Late child guard (require_child): if the Work item changes after handoff fetch before worktree prepare,
    # require_child blocks the next frame before any BAT session is started.
    base_work = work_items._get_item(ops.db, w["target"]["work_item_id"])
    wi_guard = (
        await pkg_action(
            w,
            "work_item.create",
            {"project_id": base_work["project_id"]},
            {"title": "Guarded slice", "goal": "Goal", "acceptance": "Acceptance"},
        )
    )["work_item_id"]
    env_guard = await _start_envelope(w, work_item_id=wi_guard, key="start-late-guard")
    starts_before = mock.channels().count("claude:start-session")

    def mutate_work_mid_handoff(_):
        ops.db.execute("UPDATE work_items SET version=version+1 WHERE work_item_id=?", (wi_guard,))

    w["runner"].after["fetch"] = mutate_work_mid_handoff
    op_guard, _ = ops.create(STARTER_OWNER, **env_guard)
    done_guard = await _advance_start(ops, op_guard["operation_id"])
    assert done_guard["status"] == "failed", done_guard
    assert done_guard["error_code"] == "WORK_ITEM_CHANGED"
    assert done_guard["result"]["stage"] == "start_failed"
    assert mock.channels().count("claude:start-session") == starts_before

    # 4. Source withdrawal (removing member from workspace) hides the original work.start operation and its events.
    ops.db.execute("DELETE FROM pm_workspace_members WHERE workspace_id=? AND actor=?", (wid, STARTER_OWNER.actor))
    assert pm_access.visible(ops.db, STARTER_OWNER, done) is False
    withdrawn_page = pm_access.filter_events(ops.db, STARTER_OWNER, raw_page)
    assert withdrawn_page["next_cursor"] == raw_page["next_cursor"]
    assert not any(e["resource_type"] == "pm_work_attempt" for e in withdrawn_page["events"])


# ---------------------------------------------------------------------------
# Step 6 tests: pm_work_start_preview (candidates, default_branch, zero writes, start acceptance)
# ---------------------------------------------------------------------------


async def test_work_start_preview_candidates_default_branch_zero_writes_and_start_acceptance(world, mock):
    from tests.test_api_v1 import http, token

    w = world
    ops = w["d"].ops
    pm_work_start.install(ops)

    now = time.time()
    srv = pm_workspace.server_id(ops.db)
    wid = "ws_" + "b" * 32
    with ops.journal.tx():
        ops.db.execute(
            "INSERT INTO pm_workspaces VALUES(?,?,?,?,1,?,?,?,?)",
            (wid, srv, "Preview WS", '["o/r"]', OWNER.actor, now, now, "op_" + "0" * 32),
        )
        ops.db.execute(
            "INSERT INTO pm_workspace_members VALUES(?,?,?,?,?,?)",
            (wid, OWNER.actor, "owner", pm_workspace.controller_for(ops.db, OWNER), now, now),
        )
    seeded = _seed_plan_and_work(ops, wid)
    wi_id = seeded["work_item_id"]

    outsider_tok = token(w["d"], "outsider-pv", "observe", "manage", "start")
    member_tok = token(w["d"], OWNER.actor, "observe", "manage", "start", "operate")

    def _journal_counts():
        return (
            ops.db.execute("SELECT COUNT(*) FROM operations").fetchone()[0],
            ops.db.execute("SELECT COUNT(*) FROM operation_steps").fetchone()[0],
            ops.db.execute("SELECT COUNT(*) FROM api_events").fetchone()[0],
            ops.db.execute("SELECT COUNT(*) FROM work_attempts").fetchone()[0],
        )

    counts_before = _journal_counts()

    # 1. Single candidate auto-selects; omitted source_ref uses GitHub default_branch ("main").
    pv = await pm_work_start.preview(ops, OWNER, wid, wi_id, {"instruction": "Preview and start."})
    assert _journal_counts() == counts_before
    assert pv["workspace_id"] == wid
    assert pv["work_item_id"] == wi_id
    assert len(pv["candidates"]) == 1
    assert pv["code_target"] == {
        "repository": "o/r",
        "host": "h1",
        "workspace_id": "ws-1",
        "source_ref": "refs/heads/main",
    }
    assert pv["source"] == {
        "source_ref": "refs/heads/main",
        "source_sha": w["sha"],
        "repository_id": 4242,
        "binding_digest": pv["preconditions"]["binding_digest"],
    }
    assert pv["binding"]["bound"] is True
    assert pv["binding"]["bind_needed"] is False
    assert pv["package_plan"]["mode"] == "compose"
    assert pv["package_plan"]["package_reused"] is False
    assert pv["readiness"]["status"] == "ready"
    assert pv["preconditions"]["expected_source_sha"] == w["sha"]

    # 2. Custom GitHub default_branch ("trunk") when source_ref is omitted.
    w["gh"].default_branch = "trunk"
    w["gh"].script.append(
        (
            "GET",
            r"/repos/o/r/git/ref/heads/trunk$",
            200,
            {},
            {"ref": "refs/heads/trunk", "object": {"type": "commit", "sha": w["sha"]}},
        )
    )
    try:
        pv_trunk = await pm_work_start.preview(ops, OWNER, wid, wi_id, {})
        assert pv_trunk["code_target"]["source_ref"] == "refs/heads/trunk"
        assert pv_trunk["source"]["source_ref"] == "refs/heads/trunk"
    finally:
        w["gh"].default_branch = "main"

    # 3. Multiple candidates or 0 candidates never guess: code_target, source, package_plan, preconditions are None.
    sync_cfg = ops.context["fleet"].config.github.repos["o/r"].sync
    orig_bindings = sync_cfg.bindings
    try:
        object.__setattr__(sync_cfg, "bindings", orig_bindings + (("h1", "ws-2"),))
        pv_multi = await pm_work_start.preview(ops, OWNER, wid, wi_id, {})
        assert len(pv_multi["candidates"]) == 2
        assert pv_multi["code_target"] is None
        assert pv_multi["source"] is None
        assert pv_multi["package_plan"] is None
        assert pv_multi["preconditions"] is None
        assert pv_multi["readiness"]["status"] == "blocked"
        assert pv_multi["readiness"]["checks"][0]["check"] == "code_target"

        # Selecting explicit workspace_id resolves the ambiguity.
        pv_explicit = await pm_work_start.preview(
            ops, OWNER, wid, wi_id, {"code_target": {"workspace_id": "ws-1"}}
        )
        assert pv_explicit["code_target"]["workspace_id"] == "ws-1"
        assert pv_explicit["preconditions"] is not None

        # Zero candidates when project repositories have no matching binding.
        object.__setattr__(sync_cfg, "bindings", ())
        pv_zero = await pm_work_start.preview(ops, OWNER, wid, wi_id, {})
        assert pv_zero["candidates"] == []
        assert pv_zero["code_target"] is None
        assert pv_zero["source"] is None
        assert pv_zero["package_plan"] is None
        assert pv_zero["preconditions"] is None
        assert pv_zero["readiness"]["status"] == "blocked"
    finally:
        object.__setattr__(sync_cfg, "bindings", orig_bindings)

    assert _journal_counts() == counts_before

    # 4. HTTP POST /api/v1/pm/workspaces/{ws}/work/{wi}/start-preview and RPC pm_work_start_preview work,
    # non-member gets 404, and preview's code_target + preconditions are accepted directly by work.start.
    st_out, body_out = await http(
        w["port"],
        "POST",
        f"/api/v1/pm/workspaces/{wid}/work/{wi_id}/start-preview",
        tok=outsider_tok,
        body={},
    )
    assert st_out == 404
    assert body_out["error"]["code"] == "NOT_FOUND"

    st_ok, http_pv = await http(
        w["port"],
        "POST",
        f"/api/v1/pm/workspaces/{wid}/work/{wi_id}/start-preview",
        tok=member_tok,
        body={"instruction": "Previewed via HTTP."},
    )
    assert st_ok == 200, http_pv
    assert http_pv["code_target"] == pv["code_target"]
    assert http_pv["preconditions"] == pv["preconditions"]

    rpc_pv = await w["d"]._call_api_bound(
        "pm_work_start_preview",
        {"workspace_id": wid, "work_item_id": wi_id, "instruction": "Previewed via HTTP."},
        OWNER,
    )
    assert rpc_pv["preconditions"] == http_pv["preconditions"]
    assert _journal_counts() == counts_before

    # Direct submission of preview's code_target and preconditions to work.start (compose path, then reuse path).
    op1, fresh1 = ops.create(
        OWNER,
        action="work.start",
        target={"workspace_id": wid, "work_item_id": wi_id},
        params={
            "instruction": "Previewed via HTTP.",
            "agent": "claude",
            "delivery": "pull_request",
            "code_target": http_pv["code_target"],
        },
        preconditions=http_pv["preconditions"],
        idempotency_key="pv-start-1",
    )
    assert fresh1 is True
    assert op1["result"]["package_reused"] is False
    assert op1["result"]["stage"] == "preparing"

    # Execute the package creation child (pure local DB step since source_refs is empty) and cancel before handoff.
    await ops._execute(op1["operation_id"])
    pkg_child_id = ops.get(op1["operation_id"])["result"]["children"]["package"]
    assert pkg_child_id is not None
    await ops._execute(pkg_child_id)
    pkg_child = ops.get(pkg_child_id)
    assert pkg_child["status"] == "succeeded", pkg_child
    created_pkg_id = pkg_child["result"]["package_id"]
    ops.cancel(OWNER, op1["operation_id"])

    # Second preview after package exists plans 'reuse' and its preconditions are accepted by work.start.
    pv_reuse = await pm_work_start.preview(
        ops, OWNER, wid, wi_id, {"instruction": "Second run reusing package."}
    )
    assert pv_reuse["package_plan"]["mode"] == "reuse"
    assert pv_reuse["package_plan"]["package_reused"] is True
    assert pv_reuse["package_plan"]["package_id"] == created_pkg_id
    assert pv_reuse["preconditions"]["expected_previous_package_id"] == created_pkg_id

    op2, fresh2 = ops.create(
        OWNER,
        action="work.start",
        target={"workspace_id": wid, "work_item_id": wi_id},
        params={
            "instruction": "Second run reusing package.",
            "agent": "claude",
            "delivery": "pull_request",
            "code_target": pv_reuse["code_target"],
        },
        preconditions=pv_reuse["preconditions"],
        idempotency_key="pv-start-2",
    )
    assert fresh2 is True
    assert op2["result"]["package_reused"] is True
    assert op2["result"]["package_id"] == created_pkg_id
    assert op2["result"]["stage"] == "package_prepared"


# ---------------------------------------------------------------------------
# Step 7 tests: pm_work_attempts_list, pm_work_attempt_get, and capability
# ---------------------------------------------------------------------------


async def test_work_attempts_list_get_visibility_and_capabilities(world, mock):
    from bat_agent_connector import mcp_server, pm_reads
    from tests.test_api_v1 import http, token

    w = world
    ops = w["d"].ops
    pm_work_start.install(ops)

    now = time.time()
    srv = pm_workspace.server_id(ops.db)
    wid = "ws_" + "c" * 32
    with ops.journal.tx():
        ops.db.execute(
            "INSERT INTO pm_workspaces VALUES(?,?,?,?,1,?,?,?,?)",
            (wid, srv, "Attempts WS", '["o/r"]', OWNER.actor, now, now, "op_" + "0" * 32),
        )
        ops.db.execute(
            "INSERT INTO pm_workspace_members VALUES(?,?,?,?,?,?)",
            (wid, OWNER.actor, "owner", pm_workspace.controller_for(ops.db, OWNER), now, now),
        )
    seeded = _seed_plan_and_work(ops, wid)
    wi_id = seeded["work_item_id"]

    outsider_tok = token(w["d"], "outsider-att", "observe", "manage", "start")
    member_tok = token(w["d"], OWNER.actor, "observe", "manage", "start", "operate")
    no_manage_tok = token(w["d"], "no-manage-user", "observe", "start")
    no_start_tok = token(w["d"], "no-start-user", "observe", "manage")

    # 1. Start attempt 1: compose package, execute package child, then cancel before handoff.
    pv1 = await pm_work_start.preview(ops, OWNER, wid, wi_id, {"instruction": "Attempt 1 instruction."})
    op1, _ = ops.create(
        OWNER,
        action="work.start",
        target={"workspace_id": wid, "work_item_id": wi_id},
        params={
            "instruction": "Attempt 1 instruction.",
            "agent": "claude",
            "delivery": "pull_request",
            "code_target": pv1["code_target"],
        },
        preconditions=pv1["preconditions"],
        idempotency_key="att-test-1",
    )
    att1_id = op1["result"]["attempt_id"]
    await ops._execute(op1["operation_id"])
    pkg_child_id = ops.get(op1["operation_id"])["result"]["children"]["package"]
    await ops._execute(pkg_child_id)
    pkg_id = ops.get(pkg_child_id)["result"]["package_id"]
    ops.cancel(OWNER, op1["operation_id"])

    # 2. Start attempt 2: reuse package, admit handoff child, and simulate handoff completion with session.
    pv2 = await pm_work_start.preview(ops, OWNER, wid, wi_id, {"instruction": "Attempt 2 instruction."})
    op2, _ = ops.create(
        OWNER,
        action="work.start",
        target={"workspace_id": wid, "work_item_id": wi_id},
        params={
            "instruction": "Attempt 2 instruction.",
            "agent": "claude",
            "delivery": "pull_request",
            "code_target": pv2["code_target"],
        },
        preconditions=pv2["preconditions"],
        idempotency_key="att-test-2",
    )
    att2_id = op2["result"]["attempt_id"]
    assert att2_id != att1_id
    assert op2["result"]["predecessor_attempt_id"] == att1_id

    # First parent tick on reuse path admits the handoff child and records execution_operation_id on work_attempts.
    await ops._execute(op2["operation_id"])
    handoff_id = ops.get(op2["operation_id"])["result"]["children"]["handoff"]
    assert handoff_id is not None
    # Execute handoff child with a stubbed handler returning a session receipt (avoiding slow git clone in read-model test).
    handoff_res = {
        "host": "h1",
        "workspace_id": "ws-1",
        "repository": "o/r",
        "session_id": "s-att-2",
        "worktree_path": "/wt/s-att-2",
        "branch": "bat/s-att-2",
        "source_ref": "refs/heads/main",
        "source_sha": w["sha"],
    }
    orig_handoff_act = ops.actions["repository.continue"]

    async def stub_handoff_run(_ctx):
        return handoff_res

    ops.actions["repository.continue"] = ActionDef(
        orig_handoff_act.name,
        orig_handoff_act.scope,
        orig_handoff_act.summary,
        stub_handoff_run,
        orig_handoff_act.admit,
        orig_handoff_act.target_keys,
        authorize_existing=orig_handoff_act.authorize_existing,
    )
    try:
        await ops._execute(handoff_id)
    finally:
        ops.actions["repository.continue"] = orig_handoff_act
    await ops._execute(op2["operation_id"])
    done2 = ops.get(op2["operation_id"])
    assert done2["status"] == "succeeded", done2
    assert done2["result"]["stage"] == "started"
    assert done2["result"]["session"] == {"host": "h1", "session_id": "s-att-2"}

    # 3. Verify pm_work_attempts_list & pm_work_attempt_get via HTTP, RPC, and pm_reads.
    st_list, http_list = await http(
        w["port"],
        "GET",
        f"/api/v1/pm/workspaces/{wid}/work/{wi_id}/attempts",
        tok=member_tok,
    )
    assert st_list == 200, http_list
    assert http_list["workspace_id"] == wid
    assert http_list["work_item_id"] == wi_id
    assert [a["attempt_id"] for a in http_list["attempts"]] == [att1_id, att2_id]

    a1, a2 = http_list["attempts"]
    # Attempt 1: cancelled before handoff -> execution_operation_id is None, session is None, predecessor is None.
    assert a1["start_operation_id"] == op1["operation_id"]
    assert a1["package_id"] == pkg_id
    assert a1["package_reused"] is False
    assert a1["execution_operation_id"] is None
    assert a1["session"] is None
    assert a1["predecessor_attempt_id"] is None
    assert a1["status"] == "cancelled"
    assert a1["stage"] == "cancelled"
    assert a1["children"] == {"bind": None, "package": pkg_child_id, "handoff": None}

    # Attempt 2: reused package, linked predecessor_attempt_id, reached handoff and session.
    assert a2["start_operation_id"] == op2["operation_id"]
    assert a2["package_id"] == pkg_id
    assert a2["package_reused"] is True
    assert a2["execution_operation_id"] == handoff_id
    assert a2["session"] == {"host": "h1", "session_id": "s-att-2"}
    assert a2["predecessor_attempt_id"] == att1_id
    assert a2["status"] == "succeeded"
    assert a2["stage"] == "started"
    assert a2["children"] == {"bind": None, "package": None, "handoff": handoff_id}
    assert a2["execution_operation"]["operation_id"] == handoff_id

    # Pagination cursor on attempts list.
    st_page1, page1 = await http(
        w["port"],
        "GET",
        f"/api/v1/pm/workspaces/{wid}/work/{wi_id}/attempts?limit=1",
        tok=member_tok,
    )
    assert st_page1 == 200
    assert [a["attempt_id"] for a in page1["attempts"]] == [att1_id]
    assert page1["next_cursor"] == a1["seq"]
    st_page2, page2 = await http(
        w["port"],
        "GET",
        f"/api/v1/pm/workspaces/{wid}/work/{wi_id}/attempts?after={page1['next_cursor']}&limit=1",
        tok=member_tok,
    )
    assert st_page2 == 200
    assert [a["attempt_id"] for a in page2["attempts"]] == [att2_id]

    # Single attempt read via HTTP and RPC.
    st_get, http_get = await http(
        w["port"],
        "GET",
        f"/api/v1/pm/workspaces/{wid}/work-attempts/{att2_id}",
        tok=member_tok,
    )
    assert st_get == 200, http_get
    assert http_get["attempt"] == a2

    rpc_list = await w["d"]._call_api_bound(
        "pm_work_attempts_list", {"workspace_id": wid, "work_item_id": wi_id}, OWNER
    )
    assert rpc_list == http_list
    rpc_get = await w["d"]._call_api_bound(
        "pm_work_attempt_get", {"workspace_id": wid, "attempt_id": att1_id}, OWNER
    )
    assert rpc_get["attempt"] == a1
    assert pm_reads.read(ops, OWNER, "pm_work_attempt_get", {"workspace_id": wid, "attempt_id": att2_id}) == http_get

    # 4. Non-member gets 404 on parent operation, attempts list, and single attempt get; member sees all.
    for path in (
        f"/api/v1/operations/{op2['operation_id']}",
        f"/api/v1/pm/workspaces/{wid}/work/{wi_id}/attempts",
        f"/api/v1/pm/workspaces/{wid}/work-attempts/{att2_id}",
    ):
        st_404, body_404 = await http(w["port"], "GET", path, tok=outsider_tok)
        assert st_404 == 404, (path, st_404, body_404)
        assert body_404["error"]["code"] == "NOT_FOUND"
        st_200, _ = await http(w["port"], "GET", path, tok=member_tok)
        assert st_200 == 200, path

    # 5. Capabilities advertise features.pm_workspaces.work_start and gate work.start action on observe+manage+start.
    st_cap, cap_member = await http(w["port"], "GET", "/api/v1/capabilities", tok=member_tok)
    assert st_cap == 200
    ws_cap = cap_member["features"]["pm_workspaces"]["work_start"]
    assert ws_cap["supported"] is True
    assert ws_cap["action"] == "work.start"
    assert ws_cap["required_scopes"] == ["manage", "observe", "start"]
    assert ws_cap["reads"] == ["pm_work_start_preview", "pm_work_attempts_list", "pm_work_attempt_get"]
    assert "pm_work_start_preview" in mcp_server.READ_TOOLS
    assert "pm_work_attempts_list" in mcp_server.READ_TOOLS
    assert "pm_work_attempt_get" in mcp_server.READ_TOOLS

    act_member = next(a for a in cap_member["actions"] if a["action"] == "work.start")
    assert act_member["allowed"] is True

    _, cap_no_manage = await http(w["port"], "GET", "/api/v1/capabilities", tok=no_manage_tok)
    assert next(a for a in cap_no_manage["actions"] if a["action"] == "work.start")["allowed"] is False

    _, cap_no_start = await http(w["port"], "GET", "/api/v1/capabilities", tok=no_start_tok)
    assert next(a for a in cap_no_start["actions"] if a["action"] == "work.start")["allowed"] is False


async def test_work_start_journal_only_admission_and_child_git_execution(start_ready):
    from bat_agent_connector import host_git

    w = start_ready
    ops = w["d"].ops
    runner = host_git.HostGitRunner(w["d"]._config, {"h1": "h1-bat"}, server_id="fixture-server")
    ops.context["git_runner"] = runner

    env = await _start_envelope(w, key="start-git-exec")
    parent, created = ops.create(STARTER_OWNER, **env)
    assert created is True
    # The admitted work.start operation's external_refs.admission_binding has no git_execution
    assert "git_execution" not in (parent["external_refs"].get("admission_binding") or {})

    # Execute parent step 1 (admits pm.work_package.create child), execute package child,
    # then execute parent step 2 (admits repository.continue handoff child).
    await ops._execute(parent["operation_id"])
    pkg_id = ops.db.execute(
        "SELECT operation_id FROM pm_work_start_children WHERE parent_id=? AND slot='package'",
        (parent["operation_id"],),
    ).fetchone()[0]
    await ops._execute(pkg_id)
    await ops._execute(parent["operation_id"])

    # work.start parent still has no git_execution
    refreshed_parent = ops.get(parent["operation_id"])
    assert "git_execution" not in (refreshed_parent["external_refs"].get("admission_binding") or {})

    # The admitted repository.continue child does have git_execution
    handoff_row = ops.db.execute(
        "SELECT operation_id FROM pm_work_start_children WHERE parent_id=? AND slot='handoff'",
        (parent["operation_id"],),
    ).fetchone()
    assert handoff_row is not None
    handoff_op = ops.get(handoff_row[0])
    assert handoff_op["action"] == "repository.continue"
    handoff_git_exec = (handoff_op["external_refs"].get("admission_binding") or {}).get("git_execution")
    assert handoff_git_exec is not None
    assert handoff_git_exec == {"h1": runner.execution_binding("h1")}


def _operation_counts(ops) -> dict[str, int]:
    def count(sql: str) -> int:
        return ops.db.execute(sql).fetchone()[0]

    return {
        "packages": count("SELECT COUNT(*) FROM pm_work_packages"),
        "attempts": count("SELECT COUNT(*) FROM work_attempts"),
        "starts": count("SELECT COUNT(*) FROM operations WHERE action='work.start'"),
        "handoffs": count("SELECT COUNT(*) FROM operations WHERE action='repository.continue'"),
        "children": count("SELECT COUNT(*) FROM pm_work_start_children"),
    }


async def test_execution_location_change_pauses_handoff_and_resume_starts_once(start_ready, mock):
    """Drift pauses the original handoff; restoring the location resumes that same start."""
    from bat_agent_connector import host_git

    w = start_ready
    ops = w["d"].ops
    parent_id, package_operation = await _hold_before_handoff(w)
    # HostGitRunner only speaks SSH. This fixture's published repository is local, so the
    # location fence stays on the real runner and the disk scripts stay on the fixture runner.
    scripts = w["runner"]
    runner = host_git.HostGitRunner(w["d"]._config, {"h1": "h1-bat"}, server_id="fixture-server")
    runner._ssh.run = scripts.run
    ops.context["git_runner"] = runner
    host = w["d"]._config.host("h1")
    assert host.git == "ssh"

    await ops._execute(parent_id)
    admitted = ops.get(parent_id)
    assert admitted["status"] == "waiting_external", admitted
    handoff_id = admitted["result"]["children"]["handoff"]
    assert handoff_id and admitted["result"]["children"]["package"] == package_operation
    handoff = ops.get(handoff_id)
    assert handoff["status"] == "accepted"
    assert handoff["action"] == "repository.continue"
    assert handoff["idempotency_key"] == f"work-start:{parent_id}:handoff"
    frozen = handoff["external_refs"]["admission_binding"]["git_execution"]["h1"]
    assert frozen == runner.execution_binding("h1")
    assert frozen["transport"] == "ssh"
    attempt_id = "att_" + parent_id[3:]
    before = _operation_counts(ops)
    child_rows = ops.db.execute(
        "SELECT slot, operation_id FROM pm_work_start_children WHERE parent_id=? ORDER BY ordinal",
        (parent_id,),
    ).fetchall()
    assert [(row["slot"], row["operation_id"]) for row in child_rows] == [
        ("package", package_operation),
        ("handoff", handoff_id),
    ]

    host.git = "local"
    try:
        frames = len(mock.invokes)
        await ops._execute(handoff_id)
        paused_child = ops.get(handoff_id)
        assert paused_child["status"] == "needs_attention", paused_child
        assert paused_child["error_code"] == "GIT_EXECUTION_LOCATION_CHANGED"
        assert paused_child["idempotency_key"] == handoff["idempotency_key"]
        assert ops.db.execute(
            "SELECT COUNT(*) FROM operation_steps WHERE operation_id=?", (handoff_id,)
        ).fetchone()[0] == 0
        assert scripts.phases == []
        assert len(mock.invokes) == frames
        await ops._execute(parent_id)
        paused = ops.get(parent_id)
        assert paused["status"] == "needs_attention", paused
        assert paused["error_code"] == "GIT_EXECUTION_LOCATION_CHANGED"
        assert paused["result"]["stage"] == "starting"
        assert paused["result"]["children"] == {
            "bind": None, "package": package_operation, "handoff": handoff_id,
        }
        assert paused["idempotency_key"] == admitted["idempotency_key"]
        assert _operation_counts(ops) == before
    finally:
        host.git = "ssh"

    ops.resume(STARTER_OWNER, parent_id)
    done = await _advance_start(ops, parent_id)
    assert done["status"] == "succeeded", done
    assert done["operation_id"] == parent_id
    assert done["idempotency_key"] == admitted["idempotency_key"]
    assert done["result"]["stage"] == "started"
    assert done["result"]["attempt_id"] == attempt_id
    assert done["result"]["children"]["package"] == package_operation
    assert done["result"]["children"]["handoff"] == handoff_id
    assert done["result"]["session"]["host"] == "h1"
    finished = ops.get(handoff_id)
    assert finished["status"] == "succeeded"
    assert finished["idempotency_key"] == f"work-start:{parent_id}:handoff"
    assert _operation_counts(ops) == before
    step_names = [
        row[0] for row in ops.db.execute(
            "SELECT name FROM operation_steps WHERE operation_id=? ORDER BY seq", (handoff_id,)
        )
    ]
    assert step_names.count("source.resolve") == step_names.count("repository.fetch") == 1
    assert len(step_names) == len(set(step_names))
    assert scripts.phases.count("fetch") == scripts.phases.count("prepare") == 1
    assert mock.channels().count("claude:start-session") == mock.channels().count("claude:send-message") == 1
    replay, created = ops.create(
        STARTER_OWNER,
        action="work.start",
        target=admitted["target"],
        params=admitted["params"],
        preconditions=admitted["preconditions"],
        idempotency_key=admitted["idempotency_key"],
    )
    assert created is False
    assert replay["operation_id"] == parent_id
    assert _operation_counts(ops) == before


