"""Opt-in browser journey through real Connector HTTP, disk Git and fake providers.

Run BATC_WORK_LOOP_BROWSER=1 pytest-disktmp uv run --extra dev pytest -q
tests/test_pm_work_loop_browser.py after installing desktop's Playwright Chromium.
User writes originate in the browser; a separately authorized MCP principal opens
its own Work. IPC coordinates the clients and controls external BAT evidence.
"""
from __future__ import annotations

import asyncio
import copy
import json
import os
import re
import shutil
import sys
from pathlib import Path
from urllib.parse import unquote

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from bat_agent_connector import pm_native_runtime, pr_publication
from bat_agent_connector.errors import InvokeTimeout
from tests import test_pm_conversation_proposals as conversation
from tests import test_pm_native_operations as native
from tests.test_api_v1 import http, token
from tests.test_checkpoints import MANUAL, LocalRunner, bat_writes, git, snapshot
from tests.test_integration import commit
from tests.test_pm_loop_parity import _open_mcp, unpack
from tests.test_pm_native_runtime import protocol
from tests.test_pm_plan_operations import target as thread_target
from tests.test_pm_work_attempt_result import (
    _enable_integrate,
    _result,
    _set_turn_messages,
    pm,
    ready,
    start_ready,
    world,
)
from tests.test_pm_work_follow_up import _get_op
from tests.test_pm_work_packages import member as add_member
from tests.test_pm_work_start import STARTER_OWNER

_ = (world, pm, ready, start_ready)  # Reuse disk-backed PM setup, without starting a Work.
proposal_world = conversation.proposal_world
pytestmark = pytest.mark.skipif(os.environ.get("BATC_WORK_LOOP_BROWSER") != "1",
    reason="Opt-in real Connector + Chromium journey; run by the desktop integration job")
ROOT = Path(__file__).resolve().parents[1]


def publication_provider(w):
    """Script only the external provider; keep the real GitHub HTTP client."""
    _enable_integrate(w)
    gh = w["gh"]

    def provide(method, path, body):
        if method == "GET" and "/git/ref/heads/" in path:
            ref = unquote(path.split("/git/ref/heads/", 1)[1])
            sha = git(w["remote"], "rev-parse", "refs/heads/" + ref)
            gh.script.append((method, re.escape(path) + "$", 200, {},
                {"ref": "refs/heads/" + ref, "object": {"type": "commit", "sha": sha}}))
        elif method == "POST" and path == "/repos/o/r/pulls":
            head = git(w["remote"], "rev-parse", "refs/heads/" + body["head"])
            base = git(w["remote"], "rev-parse", "refs/heads/" + body["base"])
            pr = gh.add_pr(21, head, head_ref=body["head"], id=821, node_id="PR_work_loop_21",
                title=body["title"], body=body["body"], draft=body["draft"])
            pr["base"].update(sha=base, ref=body["base"])
            gh.script.append((method, re.escape(path) + "$", 201, {}, pr))

    gh.before_request = provide


async def eventually(predicate, *, seconds=60):
    async def wait():
        while not predicate():
            await asyncio.sleep(0.05)
    await asyncio.wait_for(wait(), seconds)


@pytest.mark.parametrize(("locale", "width"), [("en", 1440), ("zh-TW", 768), ("zh-TW", 390)])
async def test_start_queue_close_reconnect_result_and_pr(start_ready, mock, tmp_path, locale, width):
    await work_loop(start_ready, mock, tmp_path, locale, width)


@pytest.mark.parametrize(("locale", "width"), [("en", 1440), ("zh-TW", 390)])
async def test_queued_writer_withdrawal_keeps_history_without_dispatch(start_ready, mock, tmp_path, locale, width):
    await work_loop(start_ready, mock, tmp_path, locale, width, withdraw_writer=True)


@pytest.mark.parametrize(("locale", "width"), [("en", 1440), ("zh-TW", 390)])
async def test_discussion_adopt_start_queue_and_pr(start_ready, proposal_world, mock, tmp_path, monkeypatch, locale, width):
    await discussion_loop(start_ready, mock, tmp_path, monkeypatch, locale, width)


async def test_adopt_lost_start_reply_recovers_original_work_from_ui(start_ready, proposal_world, mock, tmp_path, monkeypatch):
    await discussion_loop(start_ready, mock, tmp_path, monkeypatch, "en", 1440, lose_start_reply=True)


async def discussion_loop(start_ready, mock, tmp_path, monkeypatch, locale, width, *, lose_start_reply=False):
    w = start_ready
    peer_token = None
    if locale == "en" and not lose_start_reply:
        # Both configured project owners keep distinct controller identities.
        peer_token = token(w["d"], "mcp-peer", "observe", "manage", "start", "operate")
        await add_member(w, "mcp-peer", "owner")

    def reply_protocol(prompt, client_id):
        context = json.loads(prompt.split("\n\n", 1)[1])["context"]
        message = next(message for message in reversed(context["messages"]) if message["role"] == "user")
        reply = json.dumps(conversation.envelope("message." + message["message_id"]), ensure_ascii=False)
        events = protocol(prompt, client_id)
        events[2]["params"]["item"]["text"] = reply
        events[-1]["params"]["turn"]["items"][-1]["text"] = reply
        return events

    monkeypatch.setattr(native, "protocol", reply_protocol)
    monkeypatch.setattr(pm_native_runtime, "installation_eligibility", lambda: {
        "adapter": "codex-native-linux", "contract": pm_native_runtime.CONTRACT,
        "required_version": pm_native_runtime.VERSION, "eligible_for_preflight": True,
        "options_confirmed": False, "reason": None, "execution_scope": "frozen_context_only",
        "execution_location": "connector_service", "version_verification": "per_run",
        "auth_validity": "unverified", "model_execution_verified": False})
    lost_reply = None
    if lose_start_reply:
        client = w["d"].fleet.client("h1")
        invoke = client.invoke
        lost_reply = {"sent": False, "hide_receipt": True}

        async def lose_ack(name, *args, **kwargs):
            if lost_reply["sent"] and lost_reply["hide_receipt"] and name == "claude:get-session-meta":
                raise InvokeTimeout("Fixture read-back unavailable")
            result = await invoke(name, *args, **kwargs)
            if name == "claude:start-session" and not lost_reply["sent"]:
                lost_reply["sent"] = True
                raise InvokeTimeout("Fixture lost start reply")
            return result

        monkeypatch.setattr(client, "invoke", lose_ack)
    await work_loop(w, mock, tmp_path, locale, width, mode="adopt",
        peer_token=peer_token, monkeypatch=monkeypatch, lost_reply=lost_reply)


async def work_loop(w, mock, tmp_path, locale, width, *, mode="start", peer_token=None, monkeypatch=None,
                    withdraw_writer=False, lost_reply=None):
    ops = w["d"].ops
    # The handoff test runner decodes a single protocol. This journey also runs
    # checkpoint and publication shell scripts, so execute every actual script.
    ops.context["git_runner"] = LocalRunner()
    publication_provider(w)
    if withdraw_writer:
        await add_member(w, "queue-policy-owner", "owner")
    mock.metas[MANUAL]["isStreaming"] = mock.states[MANUAL]["isStreaming"] = True
    manual = copy.deepcopy((mock.metas[MANUAL], mock.states[MANUAL]))
    before = snapshot(w["human"])
    seed_ids = {r[0] for r in ops.db.execute("SELECT operation_id FROM operations")}
    create_work = mode == "adopt"
    created_work = None
    status, original_work = await http(w["port"], "GET",
        f"/api/v1/work-items/{w['target']['work_item_id']}", tok=w["owner_token"])
    assert status == 200, original_work
    project_id = original_work["work_item"]["project_id"]
    adoptions_before = ops.db.execute("SELECT count(*) FROM pm_plan_adoptions").fetchone()[0]
    entry = "lost-start-reply" if lost_reply else "withdraw-writer" if withdraw_writer else mode
    shots = ROOT / "desktop/test-results/work-loop" / f"{entry}-{locale}-{width}"
    shutil.rmtree(shots, ignore_errors=True)
    shots.mkdir(parents=True, exist_ok=True)
    browser_temp = tmp_path / "browser"
    browser_temp.mkdir()
    worker = asyncio.create_task(ops.loop(0.03))
    child = None

    async def produce_commit(wt, path, content, message):
        sha = await asyncio.to_thread(commit, wt, path, content, message)
        files = await asyncio.to_thread(git, wt, "show", "--format=", "--name-status", sha)
        mock.commit_files[sha] = [{"status": status, "file": name}
            for status, name in (line.split("\t", 1) for line in files.splitlines())]
        for item in mock.commit_files[sha]:
            mock.commit_diffs[sha, item["file"]] = await asyncio.to_thread(
                git, wt, "show", "--format=", sha, "--", item["file"])
        return sha

    async def wait_status(operation_id, expected):
        terminal = {"succeeded", "failed", "cancelled", "needs_attention", "uncertain"}
        try:
            await eventually(lambda: ops.get(operation_id)["status"] in terminal | {expected})
        except asyncio.TimeoutError:
            pytest.fail(f"Operation did not settle: {ops.get(operation_id)}")
        assert ops.get(operation_id)["status"] == expected, ops.get(operation_id)

    async def start_peer_work(original):
        server, fleet = _open_mcp(w["d"], monkeypatch, w["port"], peer_token)
        async def call(name, **arguments):
            return unpack(await server.call_tool(name, arguments))

        async def submit(action, target, params, key, preconditions=None):
            reply = await call("operation_submit", action=action, target=target, params=params,
                preconditions=preconditions or {}, idempotency_key=key, confirm=True, wait_s=0)
            identity = reply["operation"]["operation_id"]
            await wait_status(identity, "succeeded")
            return (await call("operation_get", operation_id=identity))["operation"]

        try:
            count = ops.db.execute("SELECT count(*) FROM operations").fetchone()[0]
            with pytest.raises(ToolError, match="PM_WORK_CONTROLLER_CONFLICT"):
                await call("operation_submit", action="session.send", target=w["target"],
                    params={"text": "This must never reach the other controller.", "schedule": "after_current"},
                    preconditions={"expected_attempt_id": original["attempt_id"]},
                    idempotency_key="peer-takeover-refused", confirm=True, wait_s=0)
            assert ops.db.execute("SELECT count(*) FROM operations").fetchone()[0] == count
            first = await call("work_item_get", work_item_id=w["target"]["work_item_id"])
            created = await submit("work_item.create", {"project_id": first["work_item"]["project_id"]},
                {"title": "Check keyboard focus", "goal": "Verify focus after a failed login"}, "peer-create")
            peer_work = created["result"]["work_item_id"]
            target = {"workspace_id": w["workspace_id"], "work_item_id": peer_work}
            instruction = "Check keyboard focus after a failed login."
            preview = await call("pm_work_start_preview", **target, instruction=instruction,
                requirement_ids=["member.login"], delivery="reviewable_changes")
            assert preview["readiness"]["status"] == "ready", preview
            started = await submit("work.start", target,
                {"instruction": instruction, "requirement_ids": ["member.login"], "delivery": "reviewable_changes",
                 "agent": "claude", "code_target": preview["code_target"]}, "peer-start", preview["preconditions"])
            assert started["actor"] == "mcp-peer"
            result = started["result"]
            execution = ops.get(result["children"]["handoff"])["result"]
            sid, wt = execution["session_id"], execution["worktree_path"]
            mock.ws_doc["terminals"].append({"id": sid, "workspaceId": "ws-1", "title": "Check keyboard focus",
                "type": "terminal", "cwd": wt, "worktreePath": wt, "worktreeBranch": execution["branch"],
                "agentPreset": "claude-code", "sdkSessionId": "sdk-mcp-peer", "model": "claude-sonnet-4-5"})
            git(wt, "config", "user.name", "fixture")
            git(wt, "config", "user.email", "fixture@example.invalid")
            sha = await produce_commit(wt, "keyboard.txt", "return focus after failed login\n", "Check keyboard focus")
            _set_turn_messages(mock, sid, execution["message_id"], assistant_id="peer-answer",
                assistant_text="Checked keyboard focus after login failure.")
            mock.states[sid]["messages"][0]["content"] = "[batc] " + instruction
            await w["d"].inventory._refresh_scheduled("h1")
            peer = {**target, "attempt_id": result["attempt_id"]}
            via_mcp = await call("pm_work_attempt_result_get", workspace_id=w["workspace_id"], attempt_id=peer["attempt_id"])
            via_owner = await _result(w, peer)
            assert via_mcp["changes"]["fixed_result_commit"]["commit_sha"] == sha
            assert via_owner["changes"]["fixed_result_commit"]["commit_sha"] == sha
            assert via_mcp["identity"]["caller_is_controller"] is True
            assert via_owner["identity"]["caller_is_controller"] is False
            first_result = await _result(w, original)
            assert via_owner["identity"]["controller"] != first_result["identity"]["controller"]
            assert started["loop"]["work_item_id"] == peer_work
            assert started["loop"]["attempt_id"] == peer["attempt_id"]
            assert started["loop"]["dashboard_hash"] == via_mcp["links"]["work_item_hash"]
            assert any(row["action"] == "session.send" and row["reason"] == "not_controller"
                for row in via_owner["blocked_actions"])
            assert peer["attempt_id"] != original["attempt_id"] and peer_work != original["work_item_id"]
            return {**peer, "candidate": sha, "session_id": sid, "operation_id": started["operation_id"],
                "create_operation_id": created["operation_id"], "host": "h1",
                "execution_operation_id": result["children"]["handoff"],
                "work_hash": via_mcp["links"]["work_item_hash"]}
        finally:
            await fleet.close()

    try:
        with (shots / "browser.log").open("w") as errors:
            child = await asyncio.create_subprocess_exec("node", "tests/work-loop-browser.mjs",
                cwd=ROOT / "desktop", stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                stderr=errors, env={**os.environ, "TMPDIR": str(browser_temp)})

            async def send(data):
                child.stdin.write((json.dumps(data) + "\n").encode())
                await child.stdin.drain()

            async def receive(stage):
                line = await asyncio.wait_for(child.stdout.readline(), 90)
                assert line, (shots / "browser.log").read_text()
                data = json.loads(line)
                assert data["stage"] == stage, data
                return data

            await send({"port": w["port"], "token": w["owner_token"], "workspace": w["workspace_id"],
                "work": w["target"]["work_item_id"], "locale": locale, "width": width, "shots": str(shots),
                "mode": mode, "thread": thread_target(w)["thread_id"], "peer_agent": bool(peer_token),
                "create_work": create_work, "project": project_id, "withdraw_writer": withdraw_writer,
                "lost_start_reply": lost_reply is not None})
            if create_work:
                created = await receive("created")
                await wait_status(created["operation_id"], "succeeded")
                creation = ops.get(created["operation_id"])
                assert creation["action"] == "work_item.create"
                assert creation["actor"] == STARTER_OWNER.actor
                assert creation["target"] == {"project_id": project_id}
                work_id = created["work_item_id"]
                assert creation["result"]["work_item_id"] == work_id != w["target"]["work_item_id"]
                assert ops.db.execute("SELECT count(*) FROM pm_work_bindings WHERE work_item_id=?", (work_id,)).fetchone()[0] == 0
                assert ops.db.execute("SELECT count(*) FROM work_attempts WHERE work_item_id=?", (work_id,)).fetchone()[0] == 0
                assert bat_writes(mock) == []
                w = {**w, "target": {"workspace_id": w["workspace_id"], "work_item_id": work_id}}
                created_work = {"work_item_id": work_id, "operation_id": creation["operation_id"], "unbound_before_adoption": True}
                await send({"verified": True})
            started = await receive("started")
            start = ops.get(started["operation_id"])
            recovery = None
            if mode == "adopt":
                if lost_reply is not None:
                    await wait_status(start["operation_id"], "needs_attention")
                    unknown = ops.get(start["operation_id"])
                    assert unknown["result"]["adopted"] and unknown["result"]["stage"] == "start_unknown", unknown
                    original = unknown["result"]["works"][0]
                    recovery = {"parent": start["operation_id"], "start": original["start_operation_id"],
                        "handoff": original["handoff_operation_id"], "attempt": original["attempt_id"]}
                    assert mock.channels().count("claude:start-session") == 1
                    assert mock.channels().count("claude:send-message") == 0
                    await send({"ready": False, "recovery": recovery})
                    await receive("unknown-shown")
                    lost_reply["hide_receipt"] = False
                    # The original scheduled read-back recovers the handoff;
                    # the UI then resumes only its paused original parents.
                    await eventually(lambda: ops.get(recovery["handoff"])["status"] == "succeeded")
                    assert mock.channels().count("claude:start-session") == 1
                    await send({"readback_restored": True})
                    for kind in ("start", "parent"):
                        resumed = await receive("resumed-" + kind)
                        assert resumed["operation_id"] == recovery[kind]
                        await wait_status(recovery[kind], "succeeded")
                        assert mock.channels().count("claude:start-session") == 1
                        await send({"settled": True})
                await wait_status(start["operation_id"], "succeeded")
                parent = ops.get(start["operation_id"])
                assert parent["action"] == "work.adopt_and_start" and parent["result"]["adopted"] is True
                assert parent["result"]["summary"]["started_count"] == 1
                start = ops.get(parent["result"]["works"][0]["start_operation_id"])
                if recovery:
                    assert start["operation_id"] == recovery["start"]
                    assert start["result"]["attempt_id"] == recovery["attempt"]
            assert start["action"] == "work.start" and start["status"] == "succeeded", start
            assert start["target"] == w["target"]
            result = start["result"]
            execution = ops.get(result["children"]["handoff"])["result"]
            sid, wt, marker = execution["session_id"], execution["worktree_path"], execution["message_id"]
            att = {"workspace_id": w["workspace_id"], "work_item_id": w["target"]["work_item_id"],
                "attempt_id": result["attempt_id"]}
            mock.ws_doc["terminals"].append({"id": sid, "workspaceId": "ws-1", "title": "Login repair",
                "type": "terminal", "cwd": wt, "worktreePath": wt, "worktreeBranch": execution["branch"],
                "agentPreset": "claude-code", "sdkSessionId": "sdk-work-loop", "model": "claude-sonnet-4-5"})
            _set_turn_messages(mock, sid, marker, streaming=True, assistant_text="Working on the login repair.")
            await w["d"].inventory._refresh_scheduled("h1")
            await send({"ready": True, "attempt": att["attempt_id"], "execution": result["children"]["handoff"]})
            closed = await receive("closed")
            queued_id = closed["operation_id"]
            await wait_status(queued_id, "waiting_external")
            # Chromium is completely closed; its queue receipt is already durable.
            assert (await _get_op(w, queued_id))["input_receipt"]["state"] == "queued"
            assert len([f for f in mock.invokes if f["channel"] == "claude:send-message"]) == 1
            if withdraw_writer:
                # A different authorized owner withdraws writing while the first
                # turn is still busy. Use public HTTP, never mutate the journal.
                owner_token = token(w["d"], "queue-policy-owner", "observe", "manage")
                status, workspace = await http(w["port"], "GET",
                    f"/api/v1/pm/workspaces/{w['workspace_id']}", tok=owner_token)
                assert status == 200, workspace
                status, demotion = await http(w["port"], "POST", "/api/v1/operations", tok=owner_token,
                    headers={"Idempotency-Key": "withdraw-queued-writer"},
                    body={"action": "pm.workspace.member.set", "target": {"workspace_id": w["workspace_id"]},
                        "params": {"actor": STARTER_OWNER.actor, "role": "reader"},
                        "preconditions": {"expected_version": workspace["workspace"]["version"]}})
                assert status == 202, demotion
                await wait_status(demotion["operation"]["operation_id"], "succeeded")
            git(wt, "config", "user.name", "fixture")
            git(wt, "config", "user.email", "fixture@example.invalid")
            first_sha = await produce_commit(wt, "login.txt", "prevent double submit\n", "Fix login submit")
            _set_turn_messages(mock, sid, marker, assistant_id="first-answer", assistant_text="Fixed double submission.")
            await w["d"].inventory._refresh_scheduled("h1")
            # No forced wake/resend or journal edits: the production worker owns dispatch.
            if withdraw_writer:
                await wait_status(queued_id, "failed")
                refused = await _get_op(w, queued_id)
                assert refused["error_code"] == "FORBIDDEN", refused
                assert refused["input_receipt"]["state"] == "failed"
                assert refused["input_receipt"]["error_code"] == "FORBIDDEN"
                final = await _result(w, att)
                assert final["changes"]["fixed_result_commit"]["commit_sha"] == first_sha
                assert not any(row.get("action") == "session.send" for row in final["next_allowed_actions"])
                assert {"action": "session.send", "reason": "not_controller"} in final["blocked_actions"]
                await send({"candidate": first_sha, "attempt": att["attempt_id"]})
                done = await receive("withdrawn")
                assert await asyncio.wait_for(child.wait(), 10) == 0, (shots / "browser.log").read_text()
                assert done["operations"] == [start["operation_id"], queued_id]
                assert snapshot(w["human"]) == before
                assert (mock.metas[MANUAL], mock.states[MANUAL]) == manual
                writes = bat_writes(mock)
                assert [frame["channel"] for frame in writes] == ["claude:start-session", "claude:send-message"]
                assert all(frame["params"]["sessionId"] == sid for frame in writes)
                assert not any(frame["params"].get("clientMessageId") == "batc-" + queued_id for frame in writes)
                assert not any(row[0] == "POST" for row in w["gh"].requests)
                assert git(w["remote"], "rev-parse", "refs/heads/main") == w["sha"]
                (shots / "evidence.json").write_text(json.dumps({"locale": locale, "width": width,
                    "source_sha": git(ROOT, "rev-parse", "HEAD"), "source_dirty": bool(git(ROOT, "status", "--porcelain")),
                    "python": sys.version, "browser": done["browser"], "entry": entry,
                    "same_attempt": att["attempt_id"], "candidate": first_sha, "manual_unchanged": True,
                    "queued_operation": queued_id, "receipt_state": "failed", "error_code": "FORBIDDEN",
                    "browser_operations": 2, "bat_starts": 1, "bat_sends": 1, "github_posts": 0,
                    "browser_closed_during_dispatch": True, "history_readable": True}, indent=2) + "\n")
                return
            await wait_status(queued_id, "succeeded")
            assert (await _get_op(w, queued_id))["input_receipt"]["state"] == "sent"
            first = await _result(w, att)
            assert first["changes"]["fixed_result_commit"]["commit_sha"] == first_sha
            candidate = await produce_commit(wt, "retry-test.txt", "retry after failure\n", "Add retry case")
            second_marker = "batc-" + queued_id
            _set_turn_messages(mock, sid, marker, assistant_id="second-answer",
                assistant_text="Added the retry-after-failure case.", last_data_at=1_790_001_020_000,
                extra_items=[{"id": second_marker, "role": "user", "content": closed["instruction"],
                    "timestamp": 1_790_001_010_000}])
            await w["d"].inventory._refresh_scheduled("h1")
            final = await _result(w, att)
            assert final["changes"]["fixed_result_commit"]["commit_sha"] == candidate, final
            await send({"candidate": candidate, "attempt": att["attempt_id"]})
            published = await receive("published")
            publication = ops.get(published["operation_id"])
            await wait_status(publication["operation_id"], "succeeded")
            receipt = pr_publication.require_creation_receipt(ops, STARTER_OWNER, publication["operation_id"],
                child_operation_id=result["children"]["handoff"], provider_origin=ops.context["github"].cfg.api_url,
                repository_id=w["gh"].repository_id)
            assert receipt["candidate_sha"] == candidate
            assert git(w["remote"], "rev-parse", "refs/heads/" + receipt["branch_ref"]) == candidate
            assert git(w["remote"], "rev-parse", "refs/heads/main") == w["sha"]
            await send({"verified": True})
            peer = None
            if peer_token:
                await receive("peer-ready")
                peer = await start_peer_work(att)
                await send(peer)
            done = await receive("done")
            assert await asyncio.wait_for(child.wait(), 10) == 0, (shots / "browser.log").read_text()
            assert done["resumed_operations"] == ([recovery[kind] for kind in ("start", "parent")] if recovery else [])
            assert done["discussion_return"] == {"from": "board", "workspace": w["workspace_id"],
                "work": w["target"]["work_item_id"], "attempt": att["attempt_id"]}
            if create_work:
                assert ops.db.execute("SELECT count(*) FROM work_attempts WHERE work_item_id=?",
                    (original_work["work_item"]["work_item_id"],)).fetchone()[0] == 0
            assert snapshot(w["human"]) == before
            assert (mock.metas[MANUAL], mock.states[MANUAL]) == manual
            starts = [f for f in mock.invokes if f["channel"] == "claude:start-session"]
            sends = [f for f in mock.invokes if f["channel"] == "claude:send-message"]
            assert len(starts) == (2 if peer else 1) and len(sends) == (3 if peer else 2)
            expected_writes = [starts[0], sends[0], sends[1], starts[1], sends[2]] if peer else starts + sends
            assert bat_writes(mock) == expected_writes
            assert all(f["params"]["sessionId"] == sid for f in starts[:1] + sends[:2])
            assert sends[1]["params"]["clientMessageId"] == second_marker
            if peer:
                assert starts[-1]["params"]["sessionId"] == sends[-1]["params"]["sessionId"] == peer["session_id"]
            assert len([r for r in w["gh"].requests if r[:2] == ("POST", "/repos/o/r/pulls")]) == 1
            user_ops = [ops.get(oid) for oid in done["operations"]]
            initial = ["pm.message.append", "pm.conversation.propose", "work.adopt_and_start"] if mode == "adopt" else ["work.start"]
            assert [op["action"] for op in user_ops] == [*(["work_item.create"] if create_work else []), *initial, "session.send", "github.pr.publish"]
            assert all(op["actor"] == STARTER_OWNER.actor and op["status"] == "succeeded" for op in user_ops)
            assert len({op["idempotency_key"] for op in user_ops}) == len(user_ops)
            created = {r[0] for r in ops.db.execute("SELECT operation_id FROM operations")} - seed_ids
            if mode == "start":
                assert created == {op["operation_id"] for op in user_ops} | {
                    child_id for child_id in result["children"].values() if child_id is not None}
            else:
                assert len([oid for oid in created if ops.get(oid)["action"] == "work.start"]) == (2 if peer else 1)
                assert ops.db.execute("SELECT count(*) FROM pm_plan_adoptions").fetchone()[0] == adoptions_before + 1
            (shots / "evidence.json").write_text(json.dumps({"locale": locale, "width": width,
                "source_sha": git(ROOT, "rev-parse", "HEAD"),
                "source_dirty": bool(git(ROOT, "status", "--porcelain")),
                "python": sys.version, "browser": done["browser"],
                "entry": entry, "recovery": recovery, "created_work_in_ui": created_work, "same_attempt": att["attempt_id"],
                "discussion_return": done["discussion_return"], "browser_operations": len(user_ops), "bat_starts": len(starts),
                "bat_sends": len(sends), "github_posts": 1, "manual_unchanged": True, "mcp_peer": peer,
                "candidate": candidate, "browser_closed_during_dispatch": True}, indent=2) + "\n")
    finally:
        (shots / "operations.json").write_text(json.dumps([ops.get(row[0]) for row in
            ops.db.execute("SELECT operation_id FROM operations") if row[0] not in seed_ids], indent=2) + "\n")
        if child and child.returncode is None:
            child.terminate()
            try:
                await asyncio.wait_for(child.wait(), 10)
            except asyncio.TimeoutError:
                child.kill()
                await child.wait()
        worker.cancel()
        await asyncio.gather(worker, return_exceptions=True)
        await ops.drain()
        await w["d"].artifact_store.close_reaper()
