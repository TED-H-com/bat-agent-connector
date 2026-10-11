"""Real cleanup API and Git worktrees; MockBat and local test Git runner only."""
from __future__ import annotations

import asyncio
import contextlib
import json
import os
import sys
import tempfile
from pathlib import Path

from bat_agent_connector import api_auth, cleanup, platform_files
from tests.mockbat import TOKEN, MockBat
from tests.test_checkpoints import OPERATOR
from tests.test_checkpoints import daemon as daemon_fixture
from tests.test_checkpoints import human as human_fixture
from tests.test_cleanup import setup_work


async def main():
    with tempfile.TemporaryDirectory(prefix="batc-cleanup-ui-") as temporary:
        root = Path(temporary)
        platform_files.ensure_private_directory(root / "state")
        platform_files.ensure_private_directory(root / "config")
        os.environ.update(BATC_CONFIG_DIR=str(root / "config"), BATC_STATE_DIR=str(root / "state"), BATC_TEST_TOKEN=TOKEN)
        os.environ.pop("BATC_DEVICE_ID", None)
        mock = MockBat()
        await mock.start()
        for terminal in mock.ws_doc["terminals"]:
            if mock.metas.get(terminal["id"]) is None:
                mock.metas[terminal["id"]] = {"cwd": terminal["cwd"], "isStreaming": False}
        human = human_fixture.__wrapped__(root)
        fixture = daemon_fixture.__wrapped__(mock, human, root)
        daemon = next(fixture)
        cp, operation = await setup_work(daemon, mock)
        task_id = None
        if "--task" in sys.argv:
            from tests.test_task_cleanup import owned
            task_id = owned(daemon, operation["result"]["session_id"])
        managed_path = Path(operation["result"]["worktree_path"])
        human_before = {str(p.relative_to(human)): p.read_bytes() for p in human.rglob("*") if p.is_file()}
        frames_before = len(mock.invokes)
        # A narrower cleanup credential keeps the controller that created this fixture carrier.
        token = api_auth.issue(daemon.journal.db, OPERATOR.actor, ["observe", "cleanup", "cleanup_discard"])
        server = await asyncio.start_server(daemon._handle, "127.0.0.1", 0)
        worker = asyncio.create_task(daemon.ops.loop(0.1))
        print(json.dumps({"port": server.sockets[0].getsockname()[1], "token": token,
                          "checkpoint_id": cp["checkpoint_id"], "session_id": operation["result"]["session_id"],
                          "task_id": task_id}), flush=True)
        try:
            while line := await asyncio.to_thread(sys.stdin.readline):
                command = json.loads(line)
                if command["action"] == "stop":
                    break
                if command["action"] == "verify_task":
                    op = daemon.ops.get(command["operation_id"])
                    assert task_id and op["action"] == "cleanup.apply" and op["status"] == "succeeded", op
                    assert len([r for r in daemon.ops.list()["operations"] if r["action"] == "cleanup.apply"]) == 1
                    receipts = cleanup.receipts(daemon.ops, op["operation_id"])
                    assert sum(r["status"] == "succeeded" for r in receipts) == 2, receipts
                    assert any(r["status"] == "retained" for r in receipts), receipts
                    assert not managed_path.exists()
                    assert daemon.journal.get(task_id)["state"] == "failed"
                    assert cleanup.lookup(daemon.journal.db, task_id)
                    stops = [r for r in mock.invokes[frames_before:] if r["channel"] == "claude:stop-session"]
                    assert len(stops) == 1 and stops[0]["params"]["sessionId"] == operation["result"]["session_id"], stops
                    assert human_before == {str(p.relative_to(human)): p.read_bytes() for p in human.rglob("*") if p.is_file()}
                    print(json.dumps({"verified": True, "stop_frames": len(stops)}), flush=True)
                else:
                    raise ValueError("Unknown fixture command")
        finally:
            worker.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await worker
            server.close()
            await server.wait_closed()
            await daemon.inventory.close()
            with contextlib.suppress(StopIteration):
                next(fixture)
            await mock.stop()


if __name__ == "__main__":
    asyncio.run(main())
