"""Real PM package entry points, source privacy and bounded execution authority."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from bat_agent_connector import (
    api_auth,
    controller_authority,
    mcp_server,
    pm_discussions,
    pm_run_operations,
    pm_runs,
    pm_workspace,
)
from bat_agent_connector.errors import ResourceReadOnly
from bat_agent_connector.operations import AmbiguousOutcome, Cancelled, OperationError
from tests.operation_helpers import settle_operations
from tests.test_api_v1 import daemon as api_daemon
from tests.test_api_v1 import http, token, write_frames
from tests.test_api_v1 import served as api_served
from tests.test_pm_api import OTHER, OWNER, seed
from tests.test_pm_worktree import OWNER as WORKTREE_OWNER
from tests.test_pm_worktree import pm as pm_fixture
from tests.test_pm_worktree import world as repository_world
from tests.test_work_items import act

daemon = api_daemon
served = api_served
pm = pm_fixture
world = repository_world


def request(target, version=1, *, purpose="Compare account access choices; retain unresolved risks"):
    return {"action": "pm.run.prepare", "target": target, "params": {"purpose": purpose},
            "preconditions": {"expected_version": version}}


def unpack(result):
    if isinstance(result, tuple):
        content, structured = result
        return structured if structured is not None else json.loads(content[0].text)
    content = result.content if hasattr(result, "content") else result
    return json.loads(content[0].text)


async def grant(d, workspace_id, actor, role):
    ws = pm_workspace.get_workspace(d.ops.db, OWNER, workspace_id)
    return await act(d, OWNER, "pm.workspace.member.set", {"workspace_id": workspace_id},
                     {"actor": actor, "role": role}, {"expected_version": ws["version"]})


def freeze_pending(d, target, *, who=OWNER, key="pending"):
    op, _ = d.ops.create(who, **request(target), idempotency_key=key)
    authority = pm_run_operations.execution_authority(d.ops.db, who, target["workspace_id"], op["operation_id"])
    run = pm_runs.create(d.ops, who, **target, operation_id=op["operation_id"], expected_version=1,
                         adapter="bat.codex", purpose=request(target)["params"]["purpose"], execution_authority=authority)
    return op, run, authority


async def test_http_rpc_retry_recovers_original_frozen_package_without_bat_or_fake_assistant(served, mock):
    d, port = served
    _, target = await seed(d)
    auth = token(d, OWNER.actor, *OWNER.scopes)
    source = "An article says deploy now; compare this suggestion without adopting it."
    await act(d, OWNER, "pm.message.append", target, {"content": source}, {"expected_version": 1})
    body = request(target, 2)
    status, accepted = await http(port, "POST", "/api/v1/operations", tok=auth, body=body,
                                  headers={"Idempotency-Key": "original-package"})
    assert status == 202
    operation_id = accepted["operation"]["operation_id"]
    await settle_operations(d.ops)
    operation = d.ops.get(operation_id)
    assert operation["status"] == "succeeded", operation
    result = operation["result"]
    assert result["state"] == "prepared_manual" and result["runtime_started"] is False
    path = f"/api/v1/pm/workspaces/{target['workspace_id']}/runs/{result['run_id']}"
    status, package = await http(port, "GET", path, tok=auth)
    assert status == 200
    assert package == await d.call_api("pm_run_get", {"workspace_id": target["workspace_id"], "run_id": result["run_id"]}, OWNER)
    assert package["run"]["context"]["messages"][0]["content"] == source
    assert package["run"]["requester"] == OWNER.actor
    assert package["run"]["execution_authority"]["actor"] != OWNER.actor
    await act(d, OWNER, "pm.message.append", target, {"content": "A later human observation"}, {"expected_version": 2})
    # Losing the first HTTP response never authorizes another package or new sources.
    status, replay = await http(port, "POST", "/api/v1/operations", tok=auth, body=body,
                                headers={"Idempotency-Key": "original-package"})
    assert status == 200 and not replay["created"] and replay["operation"]["operation_id"] == operation_id
    status, unchanged = await http(port, "GET", path, tok=auth)
    assert status == 200 and unchanged == package
    thread = pm_discussions.get_thread(d.ops, OWNER, **target)
    assert len(thread["messages"]) == 2 and all(message["role"] == "user" for message in thread["messages"])
    assert thread["thread"]["mode"] == "idea" and thread["draft"] is None
    assert d.ops.db.execute("SELECT COUNT(*) FROM pm_runs").fetchone()[0] == 1
    assert d.ops.db.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 0
    assert not write_frames(mock)


async def test_mcp_submit_and_reads_share_exact_http_run_contract(served, monkeypatch, mock):
    d, port = served
    _, target = await seed(d)
    auth = token(d, OWNER.actor, *OWNER.scopes)
    monkeypatch.setenv("BATC_TASK_URL", f"http://127.0.0.1:{port}/rpc")
    monkeypatch.setenv("BATC_API_TOKEN", auth)
    server, fleet = mcp_server.build_server(d.fleet.config, read_only=False, principal_only=True)
    try:
        accepted = unpack(await server.call_tool("operation_submit", {**request(target),
                                "idempotency_key": "mcp-package", "confirm": True, "wait_s": 0}))
        operation_id = accepted["operation"]["operation_id"]
        await settle_operations(d.ops)
        run_id = d.ops.get(operation_id)["result"]["run_id"]
        cases = [
            ("pm_runs_list", {"workspace_id": target["workspace_id"]}, f"/api/v1/pm/workspaces/{target['workspace_id']}/runs"),
            ("pm_run_get", {"workspace_id": target["workspace_id"], "run_id": run_id},
             f"/api/v1/pm/workspaces/{target['workspace_id']}/runs/{run_id}"),
        ]
        for method, params, path in cases:
            status, expected = await http(port, "GET", path, tok=auth)
            assert status == 200
            assert unpack(await server.call_tool(method, params)) == expected
        assert d.ops.db.execute("SELECT COUNT(*) FROM pm_runs").fetchone()[0] == 1
        assert not write_frames(mock)
    finally:
        await fleet.close()


async def test_outsider_cannot_read_package_or_global_operation_events(served):
    d, port = served
    _, target = await seed(d)
    op = await act(d, OWNER, "pm.run.prepare", target, {"purpose": "Private release assumptions"}, {"expected_version": 1})
    run_id, operation_id = op["result"]["run_id"], op["operation_id"]
    auth = token(d, OTHER.actor, *OTHER.scopes)
    wid = target["workspace_id"]
    for path in (f"/api/v1/pm/workspaces/{wid}/runs", f"/api/v1/pm/workspaces/{wid}/runs/{run_id}",
                 f"/api/v1/pm/workspaces/{wid}/capabilities", f"/api/v1/operations/{operation_id}"):
        status, value = await http(port, "GET", path, tok=auth)
        assert status == 404, value
    for path in ("/api/v1/operations", "/api/v1/events", "/api/v1/bootstrap"):
        status, value = await http(port, "GET", path, tok=auth)
        assert status == 200
        serialized = json.dumps(value)
        assert "Private release assumptions" not in serialized and operation_id not in serialized and run_id not in serialized
    for method, params in (("pm_run_get", {"workspace_id": wid, "run_id": run_id}),
                           ("pm_runs_list", {"workspace_id": wid})):
        with pytest.raises(OperationError, match="NOT_FOUND"):
            await d.call_api(method, params, OTHER)


@pytest.mark.parametrize("query", ["limit=0", "limit=101", "limit=1&limit=2", "thread_id=foreign", "actor=owner", "run_id=foreign"])
async def test_run_listing_rejects_ambiguous_or_unauthorized_parameters(served, query):
    d, port = served
    _, target = await seed(d)
    auth = token(d, OWNER.actor, *OWNER.scopes)
    status, _ = await http(port, "GET", f"/api/v1/pm/workspaces/{target['workspace_id']}/runs?{query}", tok=auth)
    assert status in {404, 422}


async def test_reader_cannot_prepare_contributor_can_and_workspace_writer_is_exclusive(daemon):
    d = daemon
    _, target = await seed(d)
    await grant(d, target["workspace_id"], OTHER.actor, "reader")
    with pytest.raises(OperationError, match="FORBIDDEN"):
        d.ops.create(OTHER, **request(target), idempotency_key="reader")
    await grant(d, target["workspace_id"], OTHER.actor, "contributor")
    result = await act(d, OTHER, "pm.run.prepare", target, request(target)["params"], {"expected_version": 1})
    assert result["result"]["state"] == "prepared_manual"
    _, active, _ = freeze_pending(d, target)
    competing, _ = d.ops.create(OTHER, **request(target), idempotency_key="competing")
    # Run just this admitted competing operation, preserving the existing writer.
    await d.ops._execute(competing["operation_id"])
    failed = d.ops.get(competing["operation_id"])
    assert failed["status"] == "failed" and failed["error_code"] == "PM_WRITER_BUSY"
    assert pm_runs.require_writer(d.journal, active["run_id"], active["generation"])["run_id"] == active["run_id"]


async def test_manage_only_prepare_is_refused_at_admission_without_writer_or_operation(served):
    d, port = served
    _, target = await seed(d)
    auth = token(d, OWNER.actor, "manage")
    count = d.ops.db.execute("SELECT COUNT(*) FROM operations").fetchone()[0]
    status, result = await http(port, "POST", "/api/v1/operations", tok=auth, body=request(target),
                                headers={"Idempotency-Key": "requires-read-authority"})
    assert status == 403 and "observe" in json.dumps(result)
    assert d.ops.db.execute("SELECT COUNT(*) FROM operations").fetchone()[0] == count
    assert not d.ops.db.execute("SELECT 1 FROM pm_writer_slots").fetchone()


async def test_execution_requires_original_requester_active_writer_and_never_generic_pm_actor(daemon):
    d = daemon
    _, target = await seed(d)
    _, run, authority = freeze_pending(d, target)
    assert pm_run_operations.require_execution(d.journal, authority, target["workspace_id"])["run_id"] == run["run_id"]
    row = {"controller_authority": authority, "controller_incarnation": "a" * 32}
    with controller_authority.use(authority, d.journal):
        assert controller_authority.require_entry(row) == row
    impersonation = controller_authority.binding(d.ops.db, api_auth.Principal(authority["actor"], OWNER.scopes))
    with controller_authority.use(impersonation, d.journal), pytest.raises(ResourceReadOnly):
        controller_authority.require_entry(row)
    pm_runs.complete_preparation(d.journal, run["run_id"], run["generation"], worktree_binding=None, snapshot=None)
    with controller_authority.use(authority, d.journal), pytest.raises(ResourceReadOnly):
        controller_authority.require_entry(row)
    _, next_run, next_authority = freeze_pending(d, target, key="next-generation")
    assert next_run["generation"] == run["generation"] + 1
    with controller_authority.use(next_authority, d.journal):
        # Shared worktree binding can be used by the next active writer.
        assert controller_authority.require_binding(authority) == authority
        # The previous bounded native session cannot be reused by that new turn.
        with pytest.raises(ResourceReadOnly, match="PM_SESSION_RUN_CHANGED"):
            controller_authority.require_entry(row)


async def test_admin_requester_does_not_delegate_admin_cleanup_or_delivery_to_pm(daemon):
    d = daemon
    _, target = await seed(d)
    administrator = api_auth.Principal("administrator", frozenset({"admin", "cleanup", "deploy", "merge"}), admin=True)
    _, run, authority = freeze_pending(d, target, who=administrator, key="admin-bounded-pm")
    assert authority["admin"] is False and set(authority["scopes"]) == {"observe", "manage", "start"}
    assert run["request_authority"] == pm_workspace.principal_binding(administrator)
    assert authority["pm_requester"]["admin"] is True
    assert set(authority["pm_requester"]["scopes"]) == set(administrator.scopes)
    assert pm_run_operations.require_execution(d.journal, authority, target["workspace_id"])["run_id"] == run["run_id"]


async def test_membership_revocation_during_local_preparation_keeps_sources_and_releases_writer(daemon, monkeypatch):
    d = daemon
    _, target = await seed(d)
    await grant(d, target["workspace_id"], OTHER.actor, "contributor")
    original_create = pm_runs.create
    def revoke_after_freeze(*args, **kwargs):
        run = original_create(*args, **kwargs)
        d.ops.db.execute("DELETE FROM pm_workspace_members WHERE workspace_id=? AND actor=?", (target["workspace_id"], OTHER.actor))
        return run
    monkeypatch.setattr(pm_runs, "create", revoke_after_freeze)
    op, _ = d.ops.create(OTHER, **request(target), idempotency_key="revoked-during-run")
    await settle_operations(d.ops)
    failed = d.ops.get(op["operation_id"])
    assert failed["status"] == "failed" and failed["error_code"] == "NOT_FOUND"
    run = pm_runs.get_by_operation(d.ops.db, op["operation_id"])
    assert run["state"] == "failed" and run["context"]["profile"]["mode"] == "idea"
    assert d.ops.db.execute("SELECT run_id FROM pm_writer_slots WHERE workspace_id=?", (target["workspace_id"],)).fetchone()[0] is None
    with pytest.raises(OperationError, match="NOT_FOUND"):
        await d.call_api("pm_run_get", {"workspace_id": target["workspace_id"], "run_id": run["run_id"]}, OTHER)


@pytest.mark.parametrize("unresolved", [False, True])
async def test_cancellation_releases_only_when_external_effects_are_proven_absent(daemon, monkeypatch, unresolved):
    d = daemon
    _, target = await seed(d)
    original = pm_run_operations._prepare_package
    async def cancel_after_freeze(ctx):
        authority = pm_run_operations.execution_authority(d.ops.db, OWNER, target["workspace_id"], ctx.operation_id)
        pm_runs.create(d.ops, OWNER, **target, operation_id=ctx.operation_id, expected_version=1,
                       adapter="bat.codex", purpose=request(target)["params"]["purpose"], execution_authority=authority)
        if unresolved:
            d.ops._step_start(ctx.operation_id, "pm.worktree.prepare", {})
        raise Cancelled()
    monkeypatch.setattr(pm_run_operations, "_prepare_package", cancel_after_freeze)
    op, _ = d.ops.create(OWNER, **request(target), idempotency_key="cancelled")
    await settle_operations(d.ops)
    assert d.ops.get(op["operation_id"])["status"] == ("uncertain" if unresolved else "cancelled")
    run = pm_runs.get_by_operation(d.ops.db, op["operation_id"])
    assert run["state"] == ("prepared" if unresolved else "failed")
    holder = d.ops.db.execute("SELECT run_id FROM pm_writer_slots WHERE workspace_id=?", (target["workspace_id"],)).fetchone()[0]
    assert holder == (run["run_id"] if unresolved else None)
    monkeypatch.setattr(pm_run_operations, "_prepare_package", original)


async def test_cleanup_refusal_never_masks_original_failure(daemon, monkeypatch):
    d = daemon
    _, target = await seed(d)
    async def fail_after_freeze(ctx):
        authority = pm_run_operations.execution_authority(d.ops.db, OWNER, target["workspace_id"], ctx.operation_id)
        run = pm_runs.create(d.ops, OWNER, **target, operation_id=ctx.operation_id, expected_version=1,
                            adapter="bat.codex", purpose=request(target)["params"]["purpose"], execution_authority=authority)
        # A native start receipt itself forbids releasing the writer, even with
        # no current OperationService step describing that historic attempt.
        pm_runs.receipt(d.journal, run["run_id"], run["generation"], "start", "unknown", {"native_id": "original"})
        raise OperationError("ORIGINAL_FAILURE", "The original boundary failure", 409)
    monkeypatch.setattr(pm_run_operations, "_prepare_package", fail_after_freeze)
    op, _ = d.ops.create(OWNER, **request(target), idempotency_key="preserve-error")
    await settle_operations(d.ops)
    outcome = d.ops.get(op["operation_id"])
    assert outcome["status"] == "failed" and outcome["error_code"] == "ORIGINAL_FAILURE"
    assert pm_runs.get_by_operation(d.ops.db, op["operation_id"])["state"] == "unknown"


async def test_capabilities_are_shared_read_only_and_distinguish_context_from_runtime(served, monkeypatch, mock):
    d, port = served
    _, target = await seed(d)
    auth = token(d, OWNER.actor, *OWNER.scopes)
    wid = target["workspace_id"]
    status, expected = await http(port, "GET", f"/api/v1/pm/workspaces/{wid}/capabilities", tok=auth)
    assert status == 200 and expected["context_packages"] is True
    assert expected["runtime"]["automatic_pm_turn"] is False
    assert expected["runtime"]["blocked_reason"] == "external_tool_policy_unverified"
    assert expected == await d.call_api("pm_capabilities_get", {"workspace_id": wid}, OWNER)
    monkeypatch.setenv("BATC_TASK_URL", f"http://127.0.0.1:{port}/rpc")
    monkeypatch.setenv("BATC_API_TOKEN", auth)
    server, fleet = mcp_server.build_server(d.fleet.config, read_only=True, principal_only=True)
    try:
        assert unpack(await server.call_tool("pm_capabilities_get", {"workspace_id": wid})) == expected
    finally:
        await fleet.close()
    assert not write_frames(mock)


async def test_registered_prepare_writes_fixed_draft_and_reuses_owned_worktree_after_branch_moves(pm, mock):
    d = pm["d"]
    wid = pm["workspace_id"]
    tid = d.ops.db.execute("SELECT thread_id FROM pm_threads WHERE workspace_id=?", (wid,)).fetchone()[0]
    principal = api_auth.Principal(WORKTREE_OWNER.actor, frozenset({"observe", "manage", "start"}))
    target = {"workspace_id": wid, "thread_id": tid}
    params = {"purpose": "Compare architecture choices", "repository_binding": pm["selection"]}
    first = await act(d, principal, "pm.run.prepare", target, params, {"expected_version": 1}, key="registered-first")
    first_run = pm_runs.get(d.ops.db, first["result"]["run_id"])
    binding = first_run["worktree_binding"]
    snapshot = first_run["outcome"]["snapshot"]
    contents = json.loads(Path(binding["worktree_path"], snapshot["path"]).read_text())
    assert contents["status"] == "draft" and contents["payload"]["context_digest"] == first_run["context_digest"]
    assert contents["payload"]["purpose"] == params["purpose"]
    pm["state"]["sha"] = "f" * 40
    second = await act(d, principal, "pm.run.prepare", target, params, {"expected_version": 1}, key="registered-next")
    next_run = pm_runs.get(d.ops.db, second["result"]["run_id"])
    assert next_run["worktree_binding"] == binding and next_run["generation"] == first_run["generation"] + 1
    assert pm["runner"].phases.count("fetch") == 1
    assert len(list(Path(binding["worktree_path"]).glob("docs/pm/drafts/*/context.json"))) == 2
    count = len(pm["runner"].phases)
    replay = await act(d, principal, "pm.run.prepare", target, params, {"expected_version": 1}, key="registered-first")
    assert replay["operation_id"] == first["operation_id"] and len(pm["runner"].phases) == count
    assert not write_frames(mock)
    assert not pm_discussions.get_thread(d.ops, principal, **target)["messages"]


@pytest.mark.parametrize("phase", ["fetch", "prepare", "pm-snapshot"])
async def test_registered_cancel_after_lost_ack_reads_original_effect_without_resending(pm, phase):
    d = pm["d"]
    wid = pm["workspace_id"]
    tid = d.ops.db.execute("SELECT thread_id FROM pm_threads WHERE workspace_id=?", (wid,)).fetchone()[0]
    principal = api_auth.Principal(WORKTREE_OWNER.actor, frozenset({"observe", "manage", "start"}))
    target = {"workspace_id": wid, "thread_id": tid}
    def lose(_):
        raise AmbiguousOutcome("Lost the original reply")
    pm["runner"].after[phase] = lose
    first = await act(d, principal, "pm.run.prepare", target,
                      {"purpose": "Compare choices", "repository_binding": pm["selection"]},
                      {"expected_version": 1}, key="lost-and-cancelled", ok=False)
    assert first["status"] == "uncertain", first
    d.ops.cancel(principal, first["operation_id"])
    await settle_operations(d.ops)
    final = d.ops.get(first["operation_id"])
    run = pm_runs.get_by_operation(d.ops.db, first["operation_id"])
    if phase == "pm-snapshot":
        assert final["status"] == "succeeded" and run["state"] == "prepared_manual", final
        assert run["outcome"]["snapshot"]
    else:
        assert final["status"] == "cancelled" and run["state"] == "failed", final
        assert "pm-snapshot" not in pm["runner"].phases
    assert pm["runner"].phases.count(phase) == 1
    assert d.ops.db.execute("SELECT run_id FROM pm_writer_slots WHERE workspace_id=?", (wid,)).fetchone()[0] is None


async def test_cancel_attention_keeps_unknown_result_until_original_receipt_is_read_back(pm):
    d = pm["d"]
    wid = pm["workspace_id"]
    tid = d.ops.db.execute("SELECT thread_id FROM pm_threads WHERE workspace_id=?", (wid,)).fetchone()[0]
    principal = api_auth.Principal(WORKTREE_OWNER.actor, frozenset({"observe", "manage", "start"}))
    target = {"workspace_id": wid, "thread_id": tid}
    params = {"purpose": "Compare choices", "repository_binding": pm["selection"]}
    def lost(_):
        raise AmbiguousOutcome("Original result unavailable")
    pm["runner"].after["pm-snapshot"] = lost
    first = await act(d, principal, "pm.run.prepare", target, params, {"expected_version": 1}, key="attention-original", ok=False)
    oid = first["operation_id"]
    assert first["status"] == "uncertain"
    d.ops._transition(oid, "needs_attention", error_code="UNCERTAIN_UNRESOLVED", reason="Read-back budget exhausted")
    # A failed read-back still cannot prove cancellation or release its writer.
    pm["runner"].before["read.pm-snapshot"] = lost
    cancelled = d.ops.cancel(principal, oid)
    assert cancelled["status"] == "running" and cancelled["cancel_requested"]
    await settle_operations(d.ops)
    assert d.ops.get(oid)["status"] == "uncertain"
    original_run = pm_runs.get_by_operation(d.ops.db, oid)
    assert d.ops.db.execute("SELECT run_id FROM pm_writer_slots WHERE workspace_id=?", (wid,)).fetchone()[0] == original_run["run_id"]
    replay, created = d.ops.create(principal, action="pm.run.prepare", target=target, params=params,
                                  preconditions={"expected_version": 1}, idempotency_key="attention-original")
    assert not created and replay["operation_id"] == oid
    d.ops.cancel(principal, oid)
    await settle_operations(d.ops)
    final = d.ops.get(oid)
    assert final["status"] == "succeeded" and final["cancel_requested"]
    assert pm_runs.get_by_operation(d.ops.db, oid)["state"] == "prepared_manual"
    assert pm["runner"].phases.count("pm-snapshot") == 1
