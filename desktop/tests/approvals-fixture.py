"""Actual bulk HTTP and child receipts; all runtime writes go to MockBat."""
from __future__ import annotations

import asyncio
import contextlib
import json
import os
import sys
import tempfile
from pathlib import Path

from bat_agent_connector import api_auth, platform_files
from bat_agent_connector.task_daemon import TaskDaemon
from tests.conftest import adopt, controller_scope, make_config
from tests.mockbat import TOKEN, MockBat

SID = "sess-codex-0002"
MANUAL = "sess-claude-0001"
CHANNELS = {"claude:resolve-permission", "claude:set-codex-sandbox-mode", "claude:set-codex-approval-policy"}


async def main():
    with tempfile.TemporaryDirectory(prefix="batc-approvals-ui-") as temporary:
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
        token = api_auth.issue(daemon.journal.db, "approvals-browser", ["observe", "operate"])
        principal = api_auth.authenticate(daemon.journal.db, token, daemon._admin_token)
        with controller_scope(daemon.journal, principal):
            adopt(SID, agent_preset="codex-agent")
        for sid in (SID, MANUAL):
            mock.states[sid]["pendingPermission"] = {"toolUseId": "reviewed-prompt", "toolName": "Bash", "input": {"command": "pytest"}}
            mock.states[sid]["pendingAskUser"] = None
        await daemon.inventory.refresh_host("h1")
        server = await asyncio.start_server(daemon._handle, "127.0.0.1", 0)
        worker = asyncio.create_task(daemon.ops.loop(0.1))
        print(json.dumps({"port": server.sockets[0].getsockname()[1], "token": token, "managed": SID, "manual": MANUAL}), flush=True)
        try:
            while line := await asyncio.to_thread(sys.stdin.readline):
                command = json.loads(line)
                action = command["action"]
                if action == "stop":
                    break
                if action == "verify":
                    operations = daemon.ops.list()["operations"]
                    parents = [op for op in operations if op["action"] == "session.approve_pending"]
                    assert len(parents) == 1 and len(operations) == 3, operations
                    op = daemon.ops.get(command["operation_id"])
                    assert op["actor"] == "approvals-browser" and op["status"] == "succeeded", op
                    assert op["result"]["all_succeeded"] is True and op["result"]["count"] == 1, op
                    frames = [item for item in mock.invokes if item["channel"] in CHANNELS]
                    assert [item["channel"] for item in frames] == ["claude:resolve-permission", "claude:set-codex-sandbox-mode", "claude:set-codex-approval-policy"], frames
                    assert all(item["params"]["sessionId"] == SID for item in frames), frames
                    assert frames[0]["params"]["toolUseId"] == "reviewed-prompt", frames
                    assert frames[0]["params"]["result"]["dontAskAgain"] is True, frames
                    assert mock.states[MANUAL]["pendingPermission"]["toolUseId"] == "reviewed-prompt"
                    print(json.dumps({"verified": True, "frames": len(frames)}), flush=True)
                else:
                    raise ValueError("Unknown fixture command")
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
