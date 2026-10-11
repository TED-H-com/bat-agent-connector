"""Task controls through real central HTTP/journal, temporary state and MockBat only."""
from __future__ import annotations

import asyncio
import contextlib
import json
import os
import sys
import tempfile
from pathlib import Path

from bat_agent_connector import api_auth, platform_files
from bat_agent_connector.channels import GUARDED_CHANNELS, ORCHESTRATE_CHANNELS, WRITE_CHANNELS
from bat_agent_connector.task_daemon import TaskDaemon
from tests.conftest import adopt, controller_scope, make_config
from tests.mockbat import TOKEN, MockBat

SID = "sess-claude-0001"


async def main():
    with tempfile.TemporaryDirectory(prefix="batc-task-controls-ui-") as temporary:
        root = Path(temporary)
        platform_files.ensure_private_directory(root / "state")
        platform_files.ensure_private_directory(root / "config")
        os.environ.update(BATC_CONFIG_DIR=str(root / "config"), BATC_STATE_DIR=str(root / "state"), BATC_TEST_TOKEN=TOKEN)
        os.environ.pop("BATC_DEVICE_ID", None)
        mock = MockBat()
        await mock.start()
        daemon = TaskDaemon(make_config(mock, writes=True, orchestrate=True, managed_roots=["/srv"],
                                       default_permission_mode="allow_all", safety={"write_min_interval_s": 0}), root / "journal.db")
        token = api_auth.issue(daemon.journal.db, "task-controls-browser", ["observe", "operate"])
        principal = api_auth.authenticate(daemon.journal.db, token, daemon._admin_token)
        with controller_scope(daemon.journal, principal):
            task = daemon.journal.submit(project="Temporary task controls", host="h1", workspace="demo-project",
                                         original_words="Do not execute work; exercise coordinator controls only",
                                         engine="goose", idempotency_key="fixture-task")
            tid = task["task_id"]
            daemon.journal.db.execute("UPDATE tasks SET state='accepted',session_id=? WHERE task_id=?", (SID, tid))
            adopt(SID, task_id=tid, role="lead", agent_preset="claude-code")
        server = await asyncio.start_server(daemon._handle, "127.0.0.1", 0)
        worker = asyncio.create_task(daemon.ops.loop(0.1))
        print(json.dumps({"port": server.sockets[0].getsockname()[1], "token": token, "task_id": tid}), flush=True)
        try:
            while line := await asyncio.to_thread(sys.stdin.readline):
                command = json.loads(line)
                if command["action"] == "stop":
                    break
                if command["action"] == "change-version":
                    with controller_scope(daemon.journal, principal):
                        daemon.journal.pause(tid)
                    print(json.dumps({"version": daemon.journal.get(tid)["control_version"]}), flush=True)
                elif command["action"] == "verify":
                    op = daemon.ops.get(command["operation_id"])
                    current = daemon.journal.get(tid)
                    assert op["status"] == "succeeded", op
                    assert op["action"] == command["expected_action"]
                    assert current["control_version"] == command["version"]
                    assert bool(current["paused"]) == command["paused"]
                    frames = [r["channel"] for r in mock.invokes if r["channel"] in WRITE_CHANNELS | ORCHESTRATE_CHANNELS | GUARDED_CHANNELS]
                    assert frames == ["claude:abort-session"] * command["aborts"], frames
                    assert len(daemon.ops.list(limit=200)["operations"]) == command["operations"]
                    assert all(s["status"] == "succeeded" for s in op["steps"])
                    assert [s["name"] for s in op["steps"]] == command["steps"]
                    print(json.dumps({"verified": True, "steps": command["steps"], "frames": frames}), flush=True)
                elif command["action"] == "history":
                    # Seed completed local receipts; no worker or BAT/provider effect is executed.
                    for n in range(105):
                        op, _ = daemon.ops.create(principal, action="task.pause", target={"task_id": tid},
                                                  params={"abort_current": False}, idempotency_key=f"history-{n}")
                        daemon.ops._transition(op["operation_id"], "cancelled", reason=f"History fixture {n}")
                    print(json.dumps({"count": len(daemon.ops.list(limit=200)["operations"])}), flush=True)
        finally:
            worker.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await worker
            await daemon.ops.drain()
            server.close()
            await server.wait_closed()
            await daemon.artifact_store.close_reaper()
            await daemon.fleet.close()
            await daemon.inventory.close()
            daemon.journal.close()
            await mock.stop()


asyncio.run(main())
