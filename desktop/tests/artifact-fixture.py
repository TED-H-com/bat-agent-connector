"""Real artifact HTTP bytes and local helper; all BAT sessions and Git repositories are fixtures."""
from __future__ import annotations

import asyncio
import contextlib
import json
import os
import sys
import tempfile
from pathlib import Path

from bat_agent_connector import api_auth, platform_files
from tests.mockbat import TOKEN, MockBat
from tests.test_artifacts import LocalArtifactHost, make_checkpoint
from tests.test_checkpoints import daemon as daemon_fixture
from tests.test_checkpoints import make_human as human_fixture


async def main():
    with tempfile.TemporaryDirectory(prefix="batc-artifact-ui-") as temporary:
        root = Path(temporary)
        platform_files.ensure_private_directory(root / "state")
        platform_files.ensure_private_directory(root / "config")
        os.environ.update(BATC_CONFIG_DIR=str(root / "config"), BATC_STATE_DIR=str(root / "state"), BATC_TEST_TOKEN=TOKEN)
        os.environ.pop("BATC_DEVICE_ID", None)
        mock = MockBat()
        await mock.start()
        human = human_fixture(root)
        fixture = daemon_fixture.__wrapped__(mock, human, root)
        daemon = next(fixture)
        daemon.ops.context["artifact_host"] = LocalArtifactHost()
        cp = await make_checkpoint(daemon)
        token = api_auth.issue(daemon.journal.db, "artifact-browser", ["observe", "manage", "start", "operate"])
        server = await asyncio.start_server(daemon._handle, "127.0.0.1", 0)
        worker = asyncio.create_task(daemon.ops.loop(0.1))
        print(json.dumps({"port": server.sockets[0].getsockname()[1], "token": token,
                          "checkpoint_id": cp["checkpoint_id"], "session_id": cp["source_session_id"]}), flush=True)
        try:
            while line := await asyncio.to_thread(sys.stdin.readline):
                command = json.loads(line)
                if command["action"] == "stop":
                    break
                if command["action"] == "verify":
                    operation = daemon.ops.get(command["operation_id"])
                    materialized = operation["external_refs"].get("materializations", [])
                    print(json.dumps({"contents": [list(Path(row["managed_path"]).read_bytes()) for row in materialized],
                                      "states": [row["state"] for row in materialized]}), flush=True)
        finally:
            worker.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await worker
            server.close()
            await server.wait_closed()
            await daemon.artifact_store.close_reaper()
            await daemon.inventory.close()
            with contextlib.suppress(StopIteration):
                next(fixture)
            await mock.stop()


if __name__ == "__main__":
    asyncio.run(main())
