"""Authorized HTTP and MCP readers share Work facts without sharing write authority."""
from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import sys
from contextlib import asynccontextmanager

import anyio
import pytest
from mcp import ClientSession
from mcp.shared.memory import create_client_server_memory_streams

from bat_agent_connector.artifact_client import content_request
from bat_agent_connector.task_verifier import ObservedVerifier, VerificationSettings
from tests.operation_helpers import settle_operations
from tests.test_api_v1 import http, token
from tests.test_checkpoints import LocalRunner, git
from tests.test_integration import commit
from tests.test_pm_loop_parity import _open_mcp
from tests.test_pm_work_attempt_result import (
    _advance_start_public,
    _set_turn_messages,
    pm,
    ready,
    start_ready,
    world,
)

_ = world, pm, ready, start_ready


@pytest.fixture
async def parity_world(start_ready):
    try:
        yield start_ready
    finally:
        await start_ready["d"].artifact_store.close_reaper()


@asynccontextmanager
async def reader_client(w, monkeypatch, reader_token):
    server, fleet = _open_mcp(w["d"], monkeypatch, w["port"], reader_token)
    try:
        async with create_client_server_memory_streams() as (client_streams, server_streams):
            async with anyio.create_task_group() as group:
                lowlevel = server._lowlevel_server
                group.start_soon(lowlevel.run, *server_streams, lowlevel.create_initialization_options())
                async with ClientSession(*client_streams) as client:
                    await client.initialize()
                    yield client
                group.cancel_scope.cancel()
    finally:
        await fleet.close()


async def mcp_read(client, name, **arguments):
    reply = await client.call_tool(name, arguments)
    assert not reply.is_error, reply.content
    return json.loads(next(block.text for block in reply.content if block.type == "text"))


async def owner_read(w, path):
    status, reply = await http(w["port"], "GET", path, tok=w["owner_token"])
    assert status == 200, reply
    return reply


async def owner_submit(w, action, target, params, preconditions=None, *, key):
    status, reply = await http(w["port"], "POST", "/api/v1/operations", tok=w["owner_token"],
        headers={"Idempotency-Key": key}, body={"action": action, "target": target,
            "params": params, "preconditions": preconditions or {}})
    assert status == 202 and reply["created"], reply
    done = await _advance_start_public(w["d"].ops, reply["operation"]["operation_id"])
    assert done["status"] == "succeeded", done
    return done


def domain(envelopes):
    """Compare the entire response except the producer's caller-specific fields."""
    result = copy.deepcopy(envelopes)
    for field in ("read_version", "unread", "read_at"):
        result["work"]["work_item"]["reading"].pop(field)
    result["result"]["identity"].pop("caller_is_controller")
    result["result"].pop("next_allowed_actions")
    result["result"].pop("blocked_actions")
    attention = result["result"]["execution"]["attention"]
    if attention is not None:
        attention.pop("resumable")
    for field in ("latest_attempt_id", "next_cursor", "has_more", "visibility_filtered"):
        result["inputs"].pop(field)
    return result


async def test_owner_http_and_observe_only_mcp_reader_share_work_and_fixed_result(
    parity_world, mock, monkeypatch, tmp_path,
):
    w, ops = parity_world, parity_world["d"].ops
    wid, work_id = w["workspace_id"], w["target"]["work_item_id"]
    monkeypatch.setenv("BATC_TASK_URL", f"http://127.0.0.1:{w['port']}/rpc")
    ops.context["git_runner"] = LocalRunner()
    mock.echo_sends = True
    reader_token = token(w["d"], "result-reader", "observe")
    workspace = await owner_read(w, f"/api/v1/pm/workspaces/{wid}")
    await owner_submit(w, "pm.workspace.member.set", {"workspace_id": wid},
        {"actor": "result-reader", "role": "reader"},
        {"expected_version": workspace["workspace"]["version"]}, key="add-result-reader")

    status, preview = await http(w["port"], "POST",
        f"/api/v1/pm/workspaces/{wid}/work/{work_id}/start-preview", tok=w["owner_token"],
        body={"instruction": "Implement credential validation and tests.",
              "requirement_ids": ["member.login"], "delivery": "reviewable_changes"})
    assert status == 200 and preview["readiness"]["status"] == "ready", preview
    started = await owner_submit(w, "work.start", w["target"],
        {"instruction": "Implement credential validation and tests.", "agent": "claude",
         "requirement_ids": ["member.login"], "delivery": "reviewable_changes",
         "code_target": preview["code_target"]}, preview["preconditions"], key="start-shared-result")
    attempt_id = started["result"]["attempt_id"]
    execution_id = started["result"]["children"]["handoff"]
    execution = ops.get(execution_id)["result"]
    sid, wt = execution["session_id"], execution["worktree_path"]
    mock.ws_doc["terminals"].append({"id": sid, "workspaceId": "ws-1", "title": "Login check",
        "type": "terminal", "cwd": wt, "worktreePath": wt, "worktreeBranch": execution["branch"],
        "agentPreset": "claude-code", "sdkSessionId": "sdk-result-reader", "model": "claude-sonnet-4-5"})
    _set_turn_messages(mock, sid, execution["message_id"])
    await w["d"].inventory._refresh_scheduled("h1")
    sent = await owner_submit(w, "session.send", w["target"],
        {"text": "Also verify rejected credentials.", "schedule": "after_current"},
        {"expected_attempt_id": attempt_id}, key="shared-result-follow-up")
    marker = f"batc-{sent['operation_id']}"
    git(wt, "config", "user.name", "fixture")
    git(wt, "config", "user.email", "fixture@example.invalid")
    candidate = await asyncio.to_thread(commit, wt, "login-check.txt", "reject invalid credentials\n",
                                        "Verify credential rejection")
    mock.commit_files[candidate] = [{"status": "A", "file": "login-check.txt"}]
    mock.commit_diffs[candidate, "login-check.txt"] = git(wt, "show", "--format=", candidate, "--", "login-check.txt")
    _set_turn_messages(mock, sid, marker, assistant_id="follow-up-result")
    await w["d"].inventory._refresh_scheduled("h1")

    work_path = f"/api/v1/work-items/{work_id}"
    work = await owner_read(w, work_path)
    project_id = work["work_item"]["project_id"]
    w["d"].adapter.verifier = ObservedVerifier(VerificationSettings(
        commands={project_id: (sys.executable, "-c",
            "from pathlib import Path; assert Path('login-check.txt').read_text() == 'reject invalid credentials\\n'")},
        artifact_dir=str(tmp_path / "verifier-logs")))
    config = await owner_read(w,
        f"/api/v1/pm/workspaces/{wid}/executions/{execution_id}/verification-config")
    verified = await owner_submit(w, "pm.execution.verify",
        {"workspace_id": wid, "execution_operation_id": execution_id},
        {key: config[key] for key in ("candidate_commit", "tree_hash", "command_digest")},
        {"handoff_contract_digest": config["handoff_contract_digest"]}, key="verify-shared-result")
    assert verified["result"]["status"] == "passed", verified

    data = b"Credential rejection verified.\n"
    status, upload = await http(w["port"], "POST", "/api/v1/artifacts", tok=w["owner_token"],
        headers={"Idempotency-Key": "shared-result-artifact"},
        body={"params": {"display_name": "verification.txt", "size_bytes": len(data),
            "expected_digest": hashlib.sha256(data).hexdigest(), "media_type": "text/plain"}})
    assert status == 202, upload
    upload_id = upload["operation"]["operation_id"]
    await settle_operations(ops)
    assert ops.get(upload_id)["status"] == "waiting_external"
    await asyncio.to_thread(content_request, f"/api/v1/artifacts/uploads/{upload_id}/content",
                            data=data, token=w["owner_token"])
    uploaded = await _advance_start_public(ops, upload_id)
    assert uploaded["status"] == "succeeded", uploaded
    ref = {key: uploaded["result"][key] for key in ("artifact_id", "revision", "digest")}
    work = await owner_read(w, work_path)
    await owner_submit(w, "work_item.update", {"work_item_id": work_id},
        {"attachments": [{**ref, "role": "result"}]},
        {"expected_version": work["work_item"]["version"]}, key="attach-shared-result")

    attempt_path = f"/api/v1/pm/workspaces/{wid}/work-attempts/{attempt_id}"
    routes = {
        "work": (work_path, "work_item_get", {"work_item_id": work_id}),
        "result": (attempt_path + "/result", "pm_work_attempt_result_get",
                   {"workspace_id": wid, "attempt_id": attempt_id}),
        "changes": (attempt_path + "/changes", "pm_work_attempt_changes_get",
                    {"workspace_id": wid, "attempt_id": attempt_id}),
        "diff": (attempt_path + f"/diff?commit={candidate}&path=login-check.txt", "pm_work_attempt_diff_get",
                 {"workspace_id": wid, "attempt_id": attempt_id, "commit": candidate, "path": "login-check.txt"}),
        "inputs": (f"/api/v1/pm/workspaces/{wid}/work/{work_id}/inputs", "pm_work_inputs_list",
                   {"workspace_id": wid, "work_item_id": work_id}),
    }
    # Warm the shared live changes memo before comparing its diagnostic memo_hit field.
    await owner_read(w, routes["changes"][0])
    owner_caps = await owner_read(w, "/api/v1/capabilities")
    before = {name: await owner_read(w, path) for name, (path, _, _) in routes.items()}
    assert before["result"]["changes"]["fixed_result_commit"]["commit_sha"] == candidate
    assert before["diff"]["available"] and "+reject invalid credentials" in before["diff"]["diff"]
    assert before["result"]["verification"]["status"] == "verified"
    assert before["result"]["changes"]["result_artifacts"][0]["artifact_id"] == ref["artifact_id"]
    receipt = before["inputs"]["items"][0]
    assert receipt["operation_id"] == sent["operation_id"] and receipt["state"] == "sent"
    assert receipt["message_id"] == marker

    async with reader_client(w, monkeypatch, reader_token) as client:
        reader_caps = await mcp_read(client, "capabilities_get")
        assert reader_caps["actor"] == "result-reader" and reader_caps["scopes"] == ["observe"]
        assert reader_caps["actor"] != owner_caps["actor"]
        assert "operate" in owner_caps["scopes"]
        assert reader_caps["identity"]["server_id"] == owner_caps["identity"]["server_id"]
        peer = {name: await mcp_read(client, tool, **arguments)
                for name, (_, tool, arguments) in routes.items()}
        for envelopes in (before, peer):
            assert {field: envelopes["inputs"][field] for field in (
                "latest_attempt_id", "next_cursor", "has_more", "visibility_filtered")} == {
                    "latest_attempt_id": attempt_id, "next_cursor": None,
                    "has_more": False, "visibility_filtered": False}
        assert before["result"]["identity"]["caller_is_controller"] is True
        assert peer["result"]["identity"]["caller_is_controller"] is False
        assert before["work"]["work_item"]["reading"] == {
            "read_version": 0,
            "current_version": before["work"]["work_item"]["version"], "unread": True, "read_at": None,
        }
        assert peer["work"]["work_item"]["reading"] == {"read_version": 0,
            "current_version": before["work"]["work_item"]["version"], "unread": True, "read_at": None}
        assert before["result"]["execution"]["attention"] is peer["result"]["execution"]["attention"] is None
        assert any(row.get("action") == "session.send" for row in before["result"]["next_allowed_actions"])
        assert not any(row.get("action") == "session.send" for row in peer["result"]["next_allowed_actions"])
        assert {"action": "session.send", "reason": "not_controller"} in peer["result"]["blocked_actions"]
        assert not any(row.get("action") == "session.send" for row in before["result"]["blocked_actions"])
        assert peer["result"]["next_allowed_actions"] == []
        assert peer["result"]["blocked_actions"] == [
            {"action": "session.send", "reason": "not_controller"},
            {"action": "pm.execution.verify", "reason": "not_controller"},
            {"action": "session.record_verification", "reason": "not_controller"},
            {"action": "pm.quality.review.create", "reason": "not_writer_member"},
        ]
        assert before["result"]["blocked_actions"] == []
        assert {row["action"] for row in before["result"]["next_allowed_actions"]} == {
            "session.send", "pm.execution.verify", "session.record_verification", "pm.quality.review.create"}
        for row in before["result"]["next_allowed_actions"]:
            assert set(row["required_scopes"]) <= set(owner_caps["scopes"])
            assert next(action["allowed"] for action in owner_caps["actions"]
                        if action["action"] == row["action"]) is True
            assert next(action["allowed"] for action in reader_caps["actions"]
                        if action["action"] == row["action"]) is False
        count = ops.db.execute("SELECT count(*) FROM operations").fetchone()[0]
        refused = await client.call_tool("operation_submit", {"action": "session.send", "target": w["target"],
            "params": {"text": "Reader must not send.", "schedule": "after_current"},
            "preconditions": {"expected_attempt_id": attempt_id}, "confirm": True, "wait_s": 0,
            "idempotency_key": "refused-reader-follow-up"})
        assert refused.is_error and "FORBIDDEN" in str(refused.content)
        assert ops.db.execute("SELECT count(*) FROM operations").fetchone()[0] == count
        after = {name: await owner_read(w, path) for name, (path, _, _) in routes.items()}
        assert after == before
        assert domain(after) == domain(before)
        last_owner_caps = await owner_read(w, "/api/v1/capabilities")
        last_reader_caps = await mcp_read(client, "capabilities_get")
        assert last_owner_caps["identity"] == owner_caps["identity"]
        assert last_reader_caps["identity"] == reader_caps["identity"]
        assert set(peer["work"]) == set(before["work"])
        assert domain(peer) == domain(before)
