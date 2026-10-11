"""Actual permission HTTP/operations with isolated state and MockBat only."""
from __future__ import annotations

import asyncio
import contextlib
import json
import os
import sys
import tempfile
from dataclasses import replace
from pathlib import Path

from bat_agent_connector import api_auth, platform_files
from bat_agent_connector.task_daemon import TaskDaemon
from tests.conftest import adopt, controller_scope, make_config
from tests.mockbat import TOKEN, MockBat

CLAUDE = "sess-claude-0001"
CODEX = "sess-codex-0002"
CHANNELS = {"claude:set-permission-mode", "claude:set-codex-sandbox-mode", "claude:set-codex-approval-policy"}


async def main():
    with tempfile.TemporaryDirectory(prefix="batc-permissions-ui-") as temporary:
        root = Path(temporary)
        platform_files.ensure_private_directory(root / "state")
        platform_files.ensure_private_directory(root / "config")
        os.environ.update(BATC_CONFIG_DIR=str(root / "config"), BATC_STATE_DIR=str(root / "state"), BATC_TEST_TOKEN=TOKEN)
        os.environ.pop("BATC_DEVICE_ID", None)
        mock = MockBat()
        await mock.start()
        config = make_config(mock, writes=True, managed_roots=["/srv"], default_permission_mode="allow_all",
                             safety={"write_min_interval_s": 0})
        daemon = TaskDaemon(config, root / "journal.db")
        token = api_auth.issue(daemon.journal.db, "permissions-browser", ["observe", "operate"])
        principal = api_auth.authenticate(daemon.journal.db, token, daemon._admin_token)
        with controller_scope(daemon.journal, principal):
            for sid in (CLAUDE, CODEX):
                adopt(sid)
        mock.metas[CLAUDE]["isStreaming"] = True
        mock.states[CLAUDE]["isStreaming"] = True
        await daemon.inventory.refresh_host("h1")
        server = await asyncio.start_server(daemon._handle, "127.0.0.1", 0)
        worker = asyncio.create_task(daemon.ops.loop(0.1))
        print(json.dumps({"port": server.sockets[0].getsockname()[1], "token": token,
                          "claude": CLAUDE, "codex": CODEX}), flush=True)
        try:
            while line := await asyncio.to_thread(sys.stdin.readline):
                command = json.loads(line)
                action = command["action"]
                if action == "stop":
                    break
                if action == "idle":
                    mock.metas[CLAUDE]["isStreaming"] = False
                    mock.states[CLAUDE]["isStreaming"] = False
                    await daemon.inventory.refresh_host("h1")
                    print(json.dumps({"idle": True}), flush=True)
                elif action in {"policy-default", "policy-all"}:
                    config.hosts["h1"] = replace(config.hosts["h1"], default_permission_mode=(
                        "default" if action == "policy-default" else "allow_all"))
                    print(json.dumps({"policy_updated": True}), flush=True)
                elif action == "verify-refused-admission":
                    assert not daemon.journal.db.execute("SELECT 1 FROM operations").fetchone()
                    assert not [item for item in mock.invokes if item["channel"] in CHANNELS]
                    print(json.dumps({"not_admitted": True, "frames": 0}), flush=True)
                elif action == "codex-partial":
                    # Metadata and transport receipt remain distinct: the setter may mutate then return false.
                    def unproven(params):
                        mock.metas[CODEX]["codexApprovalPolicy"] = params["policy"]
                        return False
                    mock.handlers["claude:set-codex-approval-policy"] = unproven
                    print(json.dumps({"partial_ready": True}), flush=True)
                elif action == "verify":
                    frames = [item for item in mock.invokes if item["channel"] in CHANNELS]
                    assert len(frames) == command["frames"], frames
                    operation = daemon.ops.get(command["operation_id"])
                    assert operation["action"] == "session.permissions"
                    assert operation["actor"] == "permissions-browser"
                    assert operation["target"] == {"host": "h1", "session_id": command["session_id"]}
                    assert operation["status"] == command["status"], operation
                    if command.get("error_code"):
                        assert operation["error_code"] == command["error_code"], operation
                    if command["status"] == "succeeded":
                        assert operation["result"]["mode"] == "allow_all"
                        assert operation["result"]["calls"] == [{"channel": "claude:set-permission-mode", "result": True}]
                    if command["status"] == "uncertain":
                        steps = {step["name"]: step["status"] for step in operation["steps"]}
                        assert steps["permissions.sandbox"] == "succeeded", steps
                        assert steps["permissions.approval"] == "uncertain", steps
                    count = daemon.journal.db.execute("SELECT COUNT(*) FROM operations WHERE action='session.permissions'").fetchone()[0]
                    assert count == command["operations"], count
                    print(json.dumps({"verified": True, "frames": len(frames), "operations": count}), flush=True)
                else:
                    raise ValueError("Unknown fixture action")
        finally:
            worker.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await worker
            server.close()
            await server.wait_closed()
            await daemon.artifact_store.close_reaper()
            await daemon.inventory.close()
            await daemon.fleet.close()
            daemon.journal.close()
            await mock.stop()


if __name__ == "__main__":
    asyncio.run(main())
