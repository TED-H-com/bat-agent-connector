"""Tests for the work.start compound action and pm_work_start_preview (v1.3 E02, slice P3)."""
from __future__ import annotations

import json
import sqlite3
import time
import uuid

import pytest

from bat_agent_connector import controller_authority, pm_work_start, pm_workspace
from bat_agent_connector.api_auth import Principal
from bat_agent_connector.operations import ActionDef, NeedsAttention, OperationError, OperationService
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

    # Once first reaches a terminal state, a new start is admitted and links predecessor_attempt_id.
    ops._transition(first["operation_id"], "failed", error_code="TEST_FAIL", reason="Terminal failure")
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


def test_child_settled_trigger_wakes_waiting_parent(tmp_path):
    ops = _service(tmp_path)
    ops.register(ActionDef("dummy.child", "start", "Dummy child", lambda ctx: None))
    ws = "ws_" + "1" * 32
    wi = "wi_" + "2" * 20
    parent, _ = ops.create(
        OWNER,
        action="work.start",
        target={"workspace_id": ws, "work_item_id": wi},
        params={"instruction": "Wait on child"},
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
    due = time.time() + 3600
    with ops.journal.tx():
        ops.db.execute(
            "INSERT INTO pm_work_start_children VALUES(?,?,?,?,?,?,?)",
            (parent["operation_id"], "bind", 0, child["operation_id"], "{}", "d" * 64, time.time()),
        )
        ops._transition(parent["operation_id"], "running")
        ops._transition(parent["operation_id"], "waiting_external", reason="Waiting for child", next_run_at=due)
    assert ops.get(parent["operation_id"])["next_run_at"] == due
    ops._transition(child["operation_id"], "running")
    assert ops.get(parent["operation_id"])["next_run_at"] == due
    ops._transition(child["operation_id"], "succeeded", result={"done": True})
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


def test_admin_principal_and_concurrent_start_rejection_and_reuse_after_failure(tmp_path):
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

    # Transition to needs_attention and verify admin can resume.
    ops._transition(op_admin["operation_id"], "running")
    ops._transition(op_admin["operation_id"], "needs_attention", error_code="TEST_PAUSE", reason="Paused")
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

    # 3. Once the first start is in a terminal failure state, a new start is allowed, links predecessor_attempt_id,
    # and reuses a matching package when present.
    ops._transition(op_admin["operation_id"], "failed", error_code="START_FAILED", reason="Terminal failure")
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

    pre2 = {**ctx["preconditions"], "expected_previous_package_id": pkg_id}
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
    assert done1["status"] == "needs_attention", done1
    assert done1["error_code"] == "REPOSITORY_BINDING_CHANGED"
    res1 = done1["result"]
    assert res1["stage"] == "start_failed"
    assert res1["package_reused"] is False
    created_pkg_id = res1["package_id"]
    assert isinstance(created_pkg_id, str) and created_pkg_id.startswith("pmwp_")
    assert res1["session"] is None
    assert not bat_writes(mock)
    assert pm_work_packages._latest(ops.db, wid, wi_id) == created_pkg_id

    # Restore workspace folder and mark first attempt failed so a fresh start attempt can be submitted.
    mock.ws_doc["workspaces"][0]["folderPath"] = orig_folder
    ops._transition(op1["operation_id"], "failed", error_code="REPOSITORY_BINDING_CHANGED", reason="Binding moved")

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
        assert done1["status"] == "needs_attention", done1
        assert done1["error_code"] == "PM_PACKAGE_CODE_SOURCE_CHANGED"
        assert "review again" in done1["status_reason"].lower()
        assert done1["result"]["stage"] == "start_failed"
        assert not bat_writes(mock)
        return

    # move_point == "before_handoff": let package creation succeed at old SHA, then move GitHub SHA before handoff resolve.
    w["runner"].before["fetch"] = lambda _: w["state"].__setitem__("sha", new_sha)
    op1, _ = ops.create(STARTER_OWNER, **env1)
    done1 = await _advance_start(ops, op1["operation_id"])
    assert done1["status"] == "needs_attention", done1
    assert done1["error_code"] == "REPOSITORY_REF_CHANGED"
    assert "review again" in done1["status_reason"].lower()
    assert done1["result"]["stage"] == "start_failed"
    first_pkg_id = done1["result"]["package_id"]
    assert isinstance(first_pkg_id, str) and first_pkg_id.startswith("pmwp_")
    assert not bat_writes(mock)

    # Transition op1 to terminal failed so the user can start again with the reviewed new SHA.
    ops._transition(op1["operation_id"], "failed", error_code="REPOSITORY_REF_CHANGED", reason=done1["status_reason"])
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

    # 2. Cancelling parent after bind child is created stops new child creation while bind child still finishes.
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
    # Already-admitted bind child continues and finishes under its own operation, while no package/handoff child is created.
    await settle_operations(ops)
    assert ops.get(cancel_bind_id)["status"] == "succeeded"
    assert (
        ops.db.execute(
            "SELECT COUNT(*) FROM pm_work_start_children WHERE parent_id=?", (op_cancel["operation_id"],)
        ).fetchone()[0]
        == 1
    )


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
    assert done_guard["status"] == "needs_attention", done_guard
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
    # Mark handoff child succeeded with a session result and wake parent to complete attempt 2 cleanly without git I/O.
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
    ops._transition(handoff_id, "running")
    ops._transition(handoff_id, "succeeded", result=handoff_res)
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


