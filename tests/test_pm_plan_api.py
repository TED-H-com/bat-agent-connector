"""Shared immutable-plan reads, private workspace visibility and explicit drafting."""

from __future__ import annotations

import json

import pytest

from bat_agent_connector import api_auth, mcp_server, pm_plans, pm_reads, pm_runs
from bat_agent_connector.operations import OperationError
from tests.operation_helpers import settle_operations
from tests.test_api_v1 import daemon as api_daemon
from tests.test_api_v1 import http, token, write_frames
from tests.test_api_v1 import served as api_served
from tests.test_pm_api import OTHER, OWNER, seed
from tests.test_pm_run_api import grant, unpack
from tests.test_pm_worktree import OWNER as WORKTREE_OWNER
from tests.test_pm_worktree import pm as pm_fixture
from tests.test_pm_worktree import world as repository_world
from tests.test_work_items import act

daemon = api_daemon
served = api_served
pm = pm_fixture
world = repository_world


async def select_planning(d, target, principal=OWNER):
    source = (await act(d, principal, "pm.input.record", target,
        {"slot": "scope", "value": "Private membership scope", "status": "user_confirmed",
         "source_ref": {"kind": "statement", "ref": "Owner direction"}}, {"expected_version": 1}))["result"]["input_id"]
    assumption = (await act(d, principal, "pm.input.record", target,
        {"slot": "finance", "value": "Pricing is an unresolved proposal", "status": "agent_assumption",
         "source_ref": {"kind": "statement", "ref": "Candidate option"}}, {"expected_version": 2}))["result"]["input_id"]
    await act(d, principal, "pm.thread.plan", target,
              {"selected_input_ids": [source, assumption], "rationale": "Prepare only the selected scope"}, {"expected_version": 3})
    return source, assumption


def draft_body(d, target, *, markdown="A fixed plan, with unknown pricing kept explicit."):
    head = pm_plans.head(d.ops.db, target["workspace_id"])
    thread = d.ops.db.execute("SELECT version FROM pm_threads WHERE thread_id=?", (target["thread_id"],)).fetchone()
    return {"action": "pm.plan.draft", "target": target,
            "params": {"previous_revision_id": head["latest_revision_id"], "markdown": markdown},
            "preconditions": {"expected_version": thread[0], "expected_plan_version": head["version"]}}


async def draft(d, target, principal=OWNER, **kwargs):
    body = draft_body(d, target, **kwargs)
    return await act(d, principal, body["action"], body["target"], body["params"], body["preconditions"])


async def test_http_draft_replay_keeps_fixed_plan_and_never_adopts_or_starts_bat(served, mock):
    d, port = served
    _, target = await seed(d)
    source, assumption = await select_planning(d, target)
    auth = token(d, OWNER.actor, *OWNER.scopes)
    body = draft_body(d, target)
    body["params"]["requirements"] = [{"id": "membership", "source_input_id": source}]
    status, accepted = await http(port, "POST", "/api/v1/operations", tok=auth, body=body,
                                  headers={"Idempotency-Key": "original-plan"})
    assert status == 202
    await settle_operations(d.ops)
    op = d.ops.get(accepted["operation"]["operation_id"])
    assert op["status"] == "succeeded", op
    rid, wid = op["result"]["revision_id"], target["workspace_id"]
    status, detail = await http(port, "GET", f"/api/v1/pm/workspaces/{wid}/plans/{rid}", tok=auth)
    assert status == 200 and detail["plan"]["status"] == "draft"
    doc = detail["plan"]["document"]
    assert doc["markdown"] == body["params"]["markdown"]
    assert doc["requirements"][0]["source_input_id"] == source
    assert doc["sections"]["finance"]["statements"][0]["input_id"] == assumption
    assert doc["sections"]["finance"]["statements"][0]["status"] == "agent_assumption"
    assert not doc["execution_authorized"] and detail["plan"]["adoption"] is None
    await act(d, OWNER, "pm.message.append", target, {"content": "Later notes do not rewrite that plan"}, {"expected_version": 4})
    status, replay = await http(port, "POST", "/api/v1/operations", tok=auth, body=body,
                                headers={"Idempotency-Key": "original-plan"})
    assert status == 200 and not replay["created"] and replay["operation"]["operation_id"] == op["operation_id"]
    assert (await http(port, "GET", f"/api/v1/pm/workspaces/{wid}/plans/{rid}", tok=auth))[1] == detail
    status, current = await http(port, "GET", f"/api/v1/pm/workspaces/{wid}/plans/accepted", tok=auth)
    assert status == 200 and current["plan"] is None and current["head"]["latest_revision_id"] == rid
    assert d.ops.db.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 0
    assert d.ops.db.execute("SELECT COUNT(*) FROM pm_plan_adoptions").fetchone()[0] == 0
    assert not write_frames(mock)


async def test_http_rpc_mcp_plan_reads_share_cursor_and_head_contract(served, monkeypatch):
    d, port = served
    _, target = await seed(d)
    await select_planning(d, target)
    first, second = await draft(d, target), await draft(d, target, markdown="A subsequent fixed proposal")
    wid, rid = target["workspace_id"], first["result"]["revision_id"]
    auth = token(d, OWNER.actor, *OWNER.scopes)
    monkeypatch.setenv("BATC_TASK_URL", f"http://127.0.0.1:{port}/rpc")
    monkeypatch.setenv("BATC_API_TOKEN", auth)
    server, fleet = mcp_server.build_server(d.fleet.config, read_only=True, principal_only=True)
    cases = [
        ("pm_plans_list", {"workspace_id": wid, "after": 0, "limit": 1}, f"/api/v1/pm/workspaces/{wid}/plans?after=0&limit=1"),
        ("pm_plan_get", {"workspace_id": wid, "revision_id": rid}, f"/api/v1/pm/workspaces/{wid}/plans/{rid}"),
        ("pm_plan_accepted_get", {"workspace_id": wid}, f"/api/v1/pm/workspaces/{wid}/plans/accepted"),
        ("pm_plan_head_get", {"workspace_id": wid}, f"/api/v1/pm/workspaces/{wid}/plans/head"),
    ]
    try:
        for method, params, path in cases:
            status, expected = await http(port, "GET", path, tok=auth)
            assert status == 200
            assert await d.call_api(method, params, OWNER) == expected
            assert unpack(await server.call_tool(method, params)) == expected
        listing = await d.call_api("pm_plans_list", {"workspace_id": wid, "limit": 1}, OWNER)
        assert len(listing["revisions"]) == 1 and listing["next_cursor"] is not None
        assert "document" not in listing["revisions"][0]
        next_page = await d.call_api("pm_plans_list", {"workspace_id": wid, "after": listing["next_cursor"], "limit": 1}, OWNER)
        assert next_page["revisions"][0]["revision_id"] == second["result"]["revision_id"]
        assert next_page["next_cursor"] is None
    finally:
        await fleet.close()


async def test_private_plan_head_content_operations_and_events_are_not_visible_to_outsider(served):
    d, port = served
    _, target = await seed(d)
    await select_planning(d, target)
    op = await draft(d, target, markdown="Private pricing and direction rationale")
    wid, rid = target["workspace_id"], op["result"]["revision_id"]
    auth = token(d, OTHER.actor, *OTHER.scopes)
    for suffix in ("", "/head", "/accepted", "/" + rid):
        status, _ = await http(port, "GET", f"/api/v1/pm/workspaces/{wid}/plans{suffix}", tok=auth)
        assert status == 404
    for method, params in (("pm_plans_list", {}), ("pm_plan_head_get", {}), ("pm_plan_accepted_get", {}),
                           ("pm_plan_get", {"revision_id": rid})):
        with pytest.raises(OperationError, match="NOT_FOUND"):
            await d.call_api(method, {"workspace_id": wid, **params}, OTHER)
    for path in ("/api/v1/operations", "/api/v1/events", "/api/v1/bootstrap"):
        status, body = await http(port, "GET", path, tok=auth)
        assert status == 200
        serialized = json.dumps(body)
        assert rid not in serialized and op["operation_id"] not in serialized
        assert "Private pricing" not in serialized and "Private membership" not in serialized
    other_workspace, _ = await seed(d)
    with pytest.raises(OperationError, match="PM_PLAN_NOT_FOUND"):
        await d.call_api("pm_plan_get", {"workspace_id": other_workspace["workspace_id"], "revision_id": rid}, OWNER)


@pytest.mark.parametrize("suffix", ["?limit=0", "?limit=101", "?after=-1", "?after=wrong", "?after=1.5",
                                     "?after=0&after=1", "?actor=owner", "/head?limit=1", "/accepted?revision_id=other"])
async def test_plan_http_rejects_ambiguous_or_unbounded_query(served, suffix):
    d, port = served
    _, target = await seed(d)
    auth = token(d, OWNER.actor, *OWNER.scopes)
    status, _ = await http(port, "GET", f"/api/v1/pm/workspaces/{target['workspace_id']}/plans{suffix}", tok=auth)
    assert status == 422


@pytest.mark.parametrize("params", [{"after": True}, {"after": "0"}, {"limit": False}, {"scope": "admin"}])
async def test_plan_rpc_rejects_wrong_types_and_unknown_fields(daemon, params):
    _, target = await seed(daemon)
    with pytest.raises(OperationError, match="INVALID_PARAMS"):
        await daemon.call_api("pm_plans_list", {"workspace_id": target["workspace_id"], **params}, OWNER)


async def test_reader_and_revoked_member_cannot_create_or_replay_plan_draft(daemon):
    d = daemon
    _, target = await seed(d)
    await select_planning(d, target)
    await grant(d, target["workspace_id"], OTHER.actor, "reader")
    body = draft_body(d, target)
    with pytest.raises(OperationError, match="FORBIDDEN"):
        d.ops.create(OTHER, **body, idempotency_key="selected-plan")
    await grant(d, target["workspace_id"], OTHER.actor, "contributor")
    op, _ = d.ops.create(OTHER, **body, idempotency_key="selected-plan")
    await settle_operations(d.ops)
    assert d.ops.get(op["operation_id"])["status"] == "succeeded"
    await grant(d, target["workspace_id"], OTHER.actor, None)
    with pytest.raises(OperationError, match="NOT_FOUND"):
        d.ops.create(OTHER, **body, idempotency_key="selected-plan")


async def test_capabilities_follow_registered_actions_scopes_role_and_bound_worktree(served):
    d, port = served
    _, target = await seed(d)
    wid = target["workspace_id"]
    owner = api_auth.Principal(OWNER.actor, frozenset({"observe", "manage", "start"}))
    caps = pm_reads.read(d.ops, owner, "pm_capabilities_get", {"workspace_id": wid})
    assert caps["plan_drafting"] and caps["plan_adoption"] and not caps["plan_adoption_available"]
    assert caps["plan_adoption_reason"] == "pm_worktree_required" and caps["worktree_selection"] is None
    low_scope = pm_reads.read(d.ops, OWNER, "pm_capabilities_get", {"workspace_id": wid})
    assert low_scope["plan_adoption_reason"] == "scope_required"
    await grant(d, wid, OTHER.actor, "contributor")
    assert pm_reads.read(d.ops, OTHER, "pm_capabilities_get", {"workspace_id": wid})["plan_adoption_reason"] == "owner_required"
    action = d.ops.actions.pop("pm.plan.adopt")
    try:
        assert not pm_reads.read(d.ops, owner, "pm_capabilities_get", {"workspace_id": wid})["plan_adoption"]
        auth = token(d, owner.actor, *owner.scopes)
        status, global_caps = await http(port, "GET", "/api/v1/capabilities", tok=auth)
        assert status == 200 and not global_caps["features"]["pm_workspaces"]["plan_adoption"]
    finally:
        d.ops.actions["pm.plan.adopt"] = action


async def test_bound_worktree_capabilities_expose_only_reusable_selection(pm):
    d, wid = pm["d"], pm["workspace_id"]
    principal = api_auth.Principal(WORKTREE_OWNER.actor, frozenset({"observe", "manage", "start"}))
    thread = d.ops.db.execute("SELECT thread_id FROM pm_threads WHERE workspace_id=?", (wid,)).fetchone()[0]
    op = await act(d, principal, "pm.run.prepare", {"workspace_id": wid, "thread_id": thread},
                   {"purpose": "Prepare original source", "repository_binding": pm["selection"]}, {"expected_version": 1})
    assert op["status"] == "succeeded"
    caps = pm_reads.read(d.ops, principal, "pm_capabilities_get", {"workspace_id": wid})
    assert caps["plan_adoption_available"] and caps["plan_adoption_reason"] is None
    assert caps["worktree_selection"] == pm["selection"]
    binding = pm_runs.workspace_worktree(d.ops.db, wid)
    serialized = json.dumps(caps)
    for private in (binding["clone_path"], binding["worktree_path"], binding["execution_authority"]["controller_id"]):
        assert private not in serialized


async def test_accepted_read_exposes_real_git_adoption_without_rewriting_source_status(pm):
    d, wid = pm["d"], pm["workspace_id"]
    principal = api_auth.Principal(WORKTREE_OWNER.actor, frozenset({"observe", "manage", "start"}))
    tid = d.ops.db.execute("SELECT thread_id FROM pm_threads WHERE workspace_id=?", (wid,)).fetchone()[0]
    target = {"workspace_id": wid, "thread_id": tid}
    _, assumption = await select_planning(d, target, principal)
    await act(d, principal, "pm.run.prepare", target,
              {"purpose": "Prepare selected scope", "repository_binding": pm["selection"]}, {"expected_version": 4})
    proposal = await draft(d, target, principal)
    rid = proposal["result"]["revision_id"]
    result = await act(d, principal, "pm.plan.adopt", {"workspace_id": wid, "revision_id": rid},
                       {"rationale": "Adopt this fixed scope with pricing still unresolved"}, {"expected_accepted_revision_id": None})
    accepted = await d.call_api("pm_plan_accepted_get", {"workspace_id": wid}, principal)
    assert accepted["head"]["accepted_revision_id"] == rid
    assert accepted["plan"]["is_current_accepted"] and accepted["plan"]["status"] == "adopted"
    assert accepted["plan"]["adoption"]["commit_sha"] == result["result"]["commit_sha"]
    assert accepted == await d.call_api("pm_plan_get", {"workspace_id": wid, "revision_id": rid}, principal)
    source = next(item for item in accepted["plan"]["document"]["selected_inputs"] if item["input_id"] == assumption)
    assert source["status"] == "agent_assumption" and not accepted["plan"]["document"]["execution_authorized"]


@pytest.mark.parametrize("method,params", [("pm_plans_list", {}), ("pm_plan_head_get", {}),
    ("pm_plan_accepted_get", {}), ("pm_plan_get", {"revision_id": "pmrev_" + "0" * 32})])
async def test_plan_mcp_reads_never_use_implicit_admin(daemon, monkeypatch, method, params):
    monkeypatch.delenv("BATC_API_TOKEN", raising=False)
    server, fleet = mcp_server.build_server(daemon.fleet.config, read_only=True)
    try:
        with pytest.raises(Exception, match="BATC_API_TOKEN"):
            await server.call_tool(method, {"workspace_id": "ws_" + "0" * 32, **params})
    finally:
        await fleet.close()
