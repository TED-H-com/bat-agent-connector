"""Real central PM decisions, scripted providers and disk-temporary owned Git.

All product state is seeded through the production action registry. Only BAT,
GitHub and the bounded native model process are synthetic. No live credentials,
native configuration, provider writes or additional actions are used.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import os
import re
import signal
import sys
import tempfile
import threading
import uuid
from pathlib import Path

from bat_agent_connector import (
    api_auth,
    platform_files,
    pm_discussions,
    pm_native_runtime,
    pm_overview,
    pm_plans,
    pm_runs,
    pm_workspace,
    repository_sync,
)
from bat_agent_connector.config import parse_config
from bat_agent_connector.task_daemon import TaskDaemon
from tests import test_pm_native_operations as native
from tests.fakegithub import TOKEN as GH_TOKEN
from tests.fakegithub import FakeGitHub
from tests.mockbat import TOKEN, MockBat
from tests.operation_helpers import settle_operations
from tests.test_checkpoints import RealGitLog, bat_writes, git, snapshot
from tests.test_pm_native_runtime import protocol
from tests.test_pm_worktree import Runner

COPY = {
    "en": {
        "project": "Member access", "plan": "Member access launch",
        "goal": "Restore member access safely",
        "login": "Members can sign in with email",
        "login_test": "Valid credentials sign in; invalid credentials are refused",
        "recovery": "Members can reset a forgotten password",
        "recovery_initial_test": "Reset links expire and work only once; the expiry duration remains open",
        "recovery_test": "Reset links expire after 15 minutes and work only once",
        "issue": "Password reset expiry needs a decision",
        "description": "Recovery work is waiting until the reset-link expiry is agreed.",
        "response": "Agree a 15-minute expiry with the PM before implementing recovery",
        "updated": "Member access with agreed reset expiry",
        "summary": "Keep sign-in and recovery; agree the reset expiry and exclude referral rewards.",
        "exclusion": "Referral rewards are outside this release",
    },
    "zh-TW": {
        "project": "會員存取", "plan": "會員存取上線計畫",
        "goal": "讓會員安全恢復帳號存取",
        "login": "會員可使用電子郵件登入",
        "login_test": "正確帳密可登入；錯誤帳密會被拒絕",
        "recovery": "會員可重設忘記的密碼",
        "recovery_initial_test": "重設連結會失效且只能使用一次；效期長度待確認",
        "recovery_test": "重設連結在 15 分鐘後失效，且只能使用一次",
        "issue": "密碼重設連結效期待決定",
        "description": "密碼重設工作正在等待確認連結效期。",
        "response": "先與 PM 確認 15 分鐘效期，再實作密碼重設",
        "updated": "會員存取與已確認的重設效期",
        "summary": "保留登入與密碼重設，確認連結效期，並排除推薦獎勵。",
        "exclusion": "推薦獎勵不在本次上線範圍",
    },
}


def envelope(copy, message_ref):
    rows = [("goal", "governance", "goal"), ("login", "scope", "login"),
        ("login_test", "quality", "login_test"), ("recovery", "scope", "recovery"),
        ("recovery_test", "quality", "recovery_test"), ("referral", "scope", "exclusion")]
    return {"version": 1, "title": copy["updated"], "summary": copy["summary"],
        "candidates": [{"key": key, "domain": domain, "text": copy[text],
            "kind": "excluded" if key == "referral" else "proposal",
            "source_refs": [message_ref, "context.0"] if key == "recovery_test"
                else [message_ref] if key == "referral" else ["accepted_plan"]}
            for key, domain, text in rows],
        "requirements": [{"id": "member." + key, "source_key": key,
            "acceptance_keys": [key + "_test"], "priority": "must",
            "depends_on": ["member.login"] if key == "recovery" else []}
            for key in ("login", "recovery")],
        "removed_requirement_ids": [], "questions": []}


async def seed(daemon, owner, copy):
    ops = daemon.ops

    async def act(action, target=None, params=None, pre=None):
        op, _ = ops.create(owner, action=action, target=target or {}, params=params or {},
            preconditions=pre or {}, idempotency_key="walkthrough-seed-" + uuid.uuid4().hex)
        await settle_operations(ops)
        done = ops.get(op["operation_id"])
        assert done["status"] == "succeeded", done
        return done["result"]

    workspace = (await act("pm.workspace.create", params={"name": copy["project"],
        "repositories": ["o/r"]}))["workspace"]
    wid = workspace["workspace_id"]
    thread = await act("pm.thread.create", {"workspace_id": wid}, {"title": copy["plan"]},
        {"expected_version": workspace["version"]})
    target = {"workspace_id": wid, "thread_id": thread["thread_id"]}

    def version():
        return pm_discussions.get_thread(ops, owner, **target)["thread"]["version"]

    selected, ids = [], {}
    for key, slot in [("goal", "value"), ("login", "scope"), ("login_test", "quality"),
            ("recovery", "scope"), ("recovery_test", "quality")]:
        result = await act("pm.input.record", target, {"slot": slot, "value": copy["recovery_initial_test" if key == "recovery_test" else key],
            "status": "user_confirmed", "source_ref": {"kind": "statement", "ref": copy["plan"]}},
            {"expected_version": version()})
        ids[key] = result["input_id"]
        selected.append(result["input_id"])
    await act("pm.thread.plan", target, {"selected_input_ids": selected,
        "rationale": copy["goal"]}, {"expected_version": version()})
    revision = await act("pm.plan.draft", target, {"previous_revision_id": None,
        "requirements": [{"id": "member." + key, "source_input_id": ids[key],
            "acceptance_input_ids": [ids[key + "_test"]], "priority": "must",
            "depends_on": ["member.login"] if key == "recovery" else []}
            for key in ("login", "recovery")]},
        {"expected_version": version(), "expected_plan_version": pm_plans.head(ops.db, wid)["version"]})
    preview = await repository_sync.source_preview(ops,
        {"repository": "o/r", "host": "h1", "workspace_id": "ws-1", "source_ref": "refs/heads/main"})
    selection = {**preview["target"], **{key: preview[key]
        for key in ("source_ref", "source_sha", "repository_id", "binding_digest")}}
    await act("pm.run.prepare", target, {"purpose": copy["goal"], "repository_binding": selection},
        {"expected_version": version()})
    await act("pm.plan.adopt", {"workspace_id": wid, "revision_id": revision["revision_id"]},
        {"rationale": copy["goal"]}, {"expected_accepted_revision_id": None})
    project = await act("project.create", params={"name": copy["project"], "repositories": ["o/r"]})
    await act("pm.project.bind", {"workspace_id": wid, "project_id": project["project_id"]},
        {"rationale": copy["goal"]}, {"expected_workspace_version": pm_workspace.get_workspace(ops.db, owner, wid)["version"],
            "expected_project_version": 1})
    work = []
    for key, state in [("login", "doing"), ("recovery", "waiting")]:
        item = await act("work_item.create", {"project_id": project["project_id"]},
            {"title": copy[key], "state": state,
                "acceptance": copy["recovery_initial_test" if key == "recovery" else "login_test"]})
        await act("pm.work.bind", {"workspace_id": wid, "work_item_id": item["work_item_id"]},
            {"rationale": copy["goal"], "plan_revision_id": revision["revision_id"], "requirement_ids": ["member." + key]},
            {"expected_binding_id": None, "expected_work_version": 1})
        work.append(item["work_item_id"])
    snap = await act("pm.snapshot.create", {"workspace_id": wid}, {},
        {"expected_plan_version": pm_plans.head(ops.db, wid)["version"]})
    # Work's recorded state is Waiting. The sourced issue blocks its requirement;
    # do not invent a work-item state/flag absent from the central contract.
    issue = await act("pm.governance.record", {"workspace_id": wid},
        {"kind": "issue", "root_cause_key": "reset-expiry", "snapshot_id": snap["snapshot_id"],
            "title": copy["issue"], "description": copy["description"], "owner_actor": owner.actor,
            "affected_requirement_ids": ["member.recovery"], "response": copy["response"],
            "source_refs": [{"kind": "requirement", "id": "member.recovery"}], "rationale": copy["description"]},
        {"expected_plan_version": pm_plans.head(ops.db, wid)["version"]})
    overview = pm_overview.read(ops, owner, wid)
    assert overview["plan"]["goal"] == copy["goal"]
    assert overview["progress"]["requirements"] == 2 and overview["progress"]["blocked"] == 1
    assert overview["attention_counts"]["blocker"] == 1
    assert pm_plans.head(ops.db, wid)["accepted_revision_id"] == revision["revision_id"]
    return {"workspace_id": wid, "revision_id": revision["revision_id"], "issue_id": issue["record_id"],
        "work_item_ids": work, "head": pm_plans.head(ops.db, wid), "copy": copy}


async def serve(commands):
    scratch = Path(os.environ.get("BATC_PM_FIXTURE_TEMP", Path.home() / "agent-work/tmp/pytest"))
    scratch.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="pm-walkthrough-ui-", dir=scratch) as temporary:
        root = Path(temporary)
        for name in ("config", "state"):
            platform_files.ensure_private_directory(root / name)
        os.environ.update(BATC_CONFIG_DIR=str(root / "config"), BATC_STATE_DIR=str(root / "state"),
            BATC_TEST_TOKEN=TOKEN, REPO_FAKE_TOKEN=GH_TOKEN)
        os.environ.pop("BATC_DEVICE_ID", None)
        human, remote, managed = root / "human", root / "remote.git", root / "managed"
        human.mkdir()
        managed.mkdir()
        git(human, "init", "-q", "-b", "main")
        git(human, "config", "user.name", "fixture")
        git(human, "config", "user.email", "fixture@example.invalid")
        (human / "hello.txt").write_text("published fixture\n")
        git(human, "add", ".")
        git(human, "commit", "-qm", "published fixture")
        sha = git(human, "rev-parse", "HEAD")
        git(root, "clone", "--bare", "--no-hardlinks", str(human), str(remote))
        (human / "hello.txt").write_text("unpublished human edit\n")
        before, remote_before = snapshot(human), git(remote, "for-each-ref")
        mock, gh = MockBat(), FakeGitHub()
        await mock.start()
        gh.start()

        def provide(method, path, _):
            if method == "GET" and path == "/repos/o/r/git/ref/heads/main":
                gh.script.append(("GET", re.escape(path) + "$", 200, {},
                    {"ref": "refs/heads/main", "object": {"type": "commit", "sha": sha}}))

        gh.before_request = provide
        mock.ws_doc["workspaces"][0]["folderPath"] = str(human)
        mock.git_logs = RealGitLog()
        mock.handlers["git:branch"] = lambda p: git(p["cwd"], "branch", "--show-current")
        daemon = TaskDaemon(parse_config({"hosts": {"h1": {"url": mock.url,
            "fingerprint": mock.fingerprint, "token_ref": "env:BATC_TEST_TOKEN", "writes": True,
            "orchestrate": True, "orchestrate_register_tabs": False, "orchestrate_max_sessions": 4,
            "managed_roots": [str(managed)]}}, "safety": {"write_min_interval_s": 0},
            "github": {"token_ref": "env:REPO_FAKE_TOKEN", "api_url": gh.url, "repos": [{"repository": "o/r",
                "sync": {"remote_url": str(remote), "bindings": [{"host": "h1", "workspace_id": "ws-1"}]}}]}}),
            root / "journal.db")
        server = worker = None
        try:
            daemon.ops.context["git_runner"] = Runner()
            token = api_auth.issue(daemon.journal.db, "walkthrough-owner", ["observe", "manage", "start"])
            owner = api_auth.authenticate(daemon.journal.db, token, daemon._admin_token)
            seeded = await seed(daemon, owner, COPY[os.environ.get("BATC_WALKTHROUGH_LOCALE", "en")])
            settings = {}
            instances = []

            def reply_protocol(prompt, client_id):
                frozen = json.loads(prompt.split("\n\n", 1)[1])["context"]
                message_ref = "message." + frozen["messages"][-1]["message_id"]
                settings["raw_reply"] = json.dumps(envelope(seeded["copy"], message_ref), ensure_ascii=False)
                events = protocol(prompt, client_id)
                events[2]["params"]["item"]["text"] = settings["raw_reply"]
                events[-1]["params"]["turn"]["items"][-1]["text"] = settings["raw_reply"]
                return events

            native.protocol = reply_protocol
            daemon.ops.context["pm_native_runtime_factory"] = lambda *args, **kwargs: native.FakeRuntime(
                *args, **kwargs, settings=settings, instances=instances)
            daemon.ops.context["pm_native_process_inspector"] = lambda identity: {
                "state": "unknown", "process": identity, "proof": "identity_unavailable", "observed_at": 1.0}
            # Installation hints describe the scripted runtime, never inspect a
            # real user's native configuration. The normal per-turn proof runs.
            pm_native_runtime.installation_eligibility = lambda: {"adapter": "codex-native-linux",
                "contract": pm_native_runtime.CONTRACT, "required_version": pm_native_runtime.VERSION,
                "eligible_for_preflight": True, "options_confirmed": False, "reason": None,
                "execution_scope": "frozen_context_only", "execution_location": "connector_service",
                "version_verification": "per_run", "auth_validity": "unverified", "model_execution_verified": False}
            seed_ids = {op[0] for op in daemon.ops.db.execute("SELECT operation_id FROM operations")}
            server = await asyncio.start_server(daemon._handle, "127.0.0.1", 0)
            worker = asyncio.create_task(daemon.ops.loop(0.03))
            print(json.dumps({"port": server.sockets[0].getsockname()[1], "token": token, **seeded}), flush=True)
            while line := await commands.get():
                command = json.loads(line)
                if command["action"] == "stop":
                    break
                if command["action"] != "verify":
                    raise ValueError("Unknown walkthrough command")
                ops = daemon.ops
                requests = command["requests"]
                request_keys = {row["key"] for row in requests}
                created = [ops.get(row[0]) for row in ops.db.execute("SELECT operation_id FROM operations")
                    if row[0] not in seed_ids]
                browser_ops = [op for op in created if op["idempotency_key"] in request_keys]
                assert sorted(op["action"] for op in browser_ops) == sorted([
                    "pm.thread.create", "pm.message.append", "pm.conversation.propose", "pm.conversation.adopt"])
                assert all(op["actor"] == owner.actor and op["status"] == "succeeded" for op in browser_ops)
                assert {row["key"] for row in requests} == {op["idempotency_key"] for op in browser_ops}
                for row in requests:
                    assert row["key"] and any(op["idempotency_key"] == row["key"] and op["action"] == row["action"]
                        and op["operation_id"] == row["operation_id"] for op in browser_ops)
                adoption = next(op for op in browser_ops if op["action"] == "pm.conversation.adopt")
                assert adoption["status"] == "succeeded", adoption
                assert {op["operation_id"] for op in created} - {op["operation_id"] for op in browser_ops} == set(
                    adoption["result"]["child_operation_ids"])
                assert len([r for r in requests if r["action"] == adoption["action"]]) == 2
                assert ops.db.execute("SELECT count(*) FROM pm_plan_adoptions").fetchone()[0] == 2  # Seed + one review.
                head = pm_plans.head(ops.db, seeded["workspace_id"])
                assert head["accepted_revision_id"] == adoption["result"]["revision_id"] != seeded["revision_id"]
                plan = pm_plans.accepted_plan(ops, owner, seeded["workspace_id"])["plan"]
                assert [r["id"] for r in plan["document"]["requirements"]] == ["member.login", "member.recovery"]
                assert seeded["copy"]["exclusion"] not in plan["document"]["markdown"]
                assert not adoption["result"]["execution_authorized"]
                path = pm_runs.workspace_worktree(ops.db, seeded["workspace_id"])["worktree_path"]
                assert git(path, "rev-parse", "HEAD") == adoption["result"]["commit_sha"]
                assert snapshot(human) == before and git(remote, "for-each-ref") == remote_before
                assert not bat_writes(mock) and all(method == "GET" for method, _, _ in gh.requests)
                assert ops.db.execute("SELECT count(*) FROM tasks").fetchone()[0] == 0
                assert len(instances) == 1 and instances[0].sends == 1 and instances[0].closed
                print(json.dumps({"verified": True, "browser_operations": len(browser_ops),
                    "adoptions": 1, "head": head, "tasks": 0, "bat_writes": 0}), flush=True)
        finally:
            if worker is not None:
                worker.cancel()
                await asyncio.gather(worker, return_exceptions=True)
            if server is not None:
                server.close()
                await server.wait_closed()
            await daemon.ops.drain()
            await daemon.artifact_store.close_reaper()
            await daemon.fleet.close()
            await daemon.inventory.close()
            daemon.journal.close()
            await mock.stop()
            gh.stop()


async def main():
    loop, task = asyncio.get_running_loop(), asyncio.current_task()
    previous = signal.signal(signal.SIGTERM, lambda *_: loop.call_soon_threadsafe(task.cancel))
    commands = asyncio.Queue()

    def read_commands():
        while True:
            line = sys.stdin.readline()
            try:
                loop.call_soon_threadsafe(commands.put_nowait, line)
            except RuntimeError:
                return
            if not line:
                return

    threading.Thread(target=read_commands, daemon=True).start()
    try:
        await serve(commands)
    finally:
        signal.signal(signal.SIGTERM, previous)


if __name__ == "__main__":
    with contextlib.suppress(asyncio.CancelledError):
        asyncio.run(main())
