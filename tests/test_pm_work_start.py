"""Tests for the work.start compound action and pm_work_start_preview (v1.3 E02, slice P3)."""
from __future__ import annotations

import json
import sqlite3
import time

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
    code_target = {
        "repository": "o/r",
        "host": "h1",
        "workspace_id": "w1",
        "source_ref": "refs/heads/main",
    }
    config = repository_sync.config_snapshot(ops, {"repository": "o/r", "host": "h1", "workspace_id": "w1"})
    binding_digest = repository_sync.digest(
        {"config": config, "workspace": {"workspace_id": "w1", "folder": "/tmp/w1"}, "repository_id": 42}
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
