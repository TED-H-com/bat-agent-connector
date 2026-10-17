"""Opt-in browser journey through real Connector HTTP, disk Git and fake providers.

Run BATC_WORK_LOOP_BROWSER=1 pytest-disktmp uv run --extra dev pytest -q
tests/test_pm_work_loop_browser.py after installing desktop's Playwright Chromium.
All user writes originate in the browser; IPC only controls external BAT evidence.
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

from bat_agent_connector import pr_publication
from tests.test_checkpoints import MANUAL, LocalRunner, bat_writes, git, snapshot
from tests.test_integration import commit
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
from tests.test_pm_work_start import STARTER_OWNER

_ = (world, pm, ready, start_ready)  # Reuse disk-backed PM setup, without starting a Work.
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
    w, ops = start_ready, start_ready["d"].ops
    # The handoff test runner decodes a single protocol. This journey also runs
    # checkpoint and publication shell scripts, so execute every actual script.
    ops.context["git_runner"] = LocalRunner()
    publication_provider(w)
    mock.metas[MANUAL]["isStreaming"] = mock.states[MANUAL]["isStreaming"] = True
    manual = copy.deepcopy((mock.metas[MANUAL], mock.states[MANUAL]))
    before = snapshot(w["human"])
    seed_ids = {r[0] for r in ops.db.execute("SELECT operation_id FROM operations")}
    shots = ROOT / "desktop/test-results/work-loop" / f"{locale}-{width}"
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
                "work": w["target"]["work_item_id"], "locale": locale, "width": width, "shots": str(shots)})
            started = await receive("started")
            start = ops.get(started["operation_id"])
            assert start["action"] == "work.start" and start["status"] == "succeeded", start
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
            await send({"ready": True, "attempt": att["attempt_id"]})
            closed = await receive("closed")
            queued_id = closed["operation_id"]
            await wait_status(queued_id, "waiting_external")
            # Chromium is completely closed; its queue receipt is already durable.
            assert (await _get_op(w, queued_id))["input_receipt"]["state"] == "queued"
            assert len([f for f in mock.invokes if f["channel"] == "claude:send-message"]) == 1
            git(wt, "config", "user.name", "fixture")
            git(wt, "config", "user.email", "fixture@example.invalid")
            first_sha = await produce_commit(wt, "login.txt", "prevent double submit\n", "Fix login submit")
            _set_turn_messages(mock, sid, marker, assistant_id="first-answer", assistant_text="Fixed double submission.")
            await w["d"].inventory._refresh_scheduled("h1")
            # No forced wake/resend or journal edits: the production worker owns dispatch.
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
            done = await receive("done")
            assert await asyncio.wait_for(child.wait(), 10) == 0, (shots / "browser.log").read_text()
            assert snapshot(w["human"]) == before
            assert (mock.metas[MANUAL], mock.states[MANUAL]) == manual
            starts = [f for f in mock.invokes if f["channel"] == "claude:start-session"]
            sends = [f for f in mock.invokes if f["channel"] == "claude:send-message"]
            assert len(starts) == 1 and len(sends) == 2
            assert bat_writes(mock) == starts + sends, "Unexpected resume, interrupt or other BAT write"
            assert all(f["params"]["sessionId"] == sid for f in starts + sends)
            assert sends[-1]["params"]["clientMessageId"] == second_marker
            assert len([r for r in w["gh"].requests if r[:2] == ("POST", "/repos/o/r/pulls")]) == 1
            user_ops = [ops.get(oid) for oid in done["operations"]]
            assert [op["action"] for op in user_ops] == ["work.start", "session.send", "github.pr.publish"]
            assert all(op["actor"] == STARTER_OWNER.actor and op["status"] == "succeeded" for op in user_ops)
            assert len({op["idempotency_key"] for op in user_ops}) == 3
            created = {r[0] for r in ops.db.execute("SELECT operation_id FROM operations")} - seed_ids
            assert created == {op["operation_id"] for op in user_ops} | {
                child_id for child_id in result["children"].values() if child_id is not None}
            (shots / "evidence.json").write_text(json.dumps({"locale": locale, "width": width,
                "source_sha": git(ROOT, "rev-parse", "HEAD"),
                "source_dirty": bool(git(ROOT, "status", "--porcelain")),
                "python": sys.version, "browser": done["browser"],
                "same_attempt": att["attempt_id"], "browser_operations": 3, "bat_starts": 1,
                "bat_sends": 2, "github_posts": 1, "manual_unchanged": True,
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
