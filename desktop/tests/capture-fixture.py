"""Real capture HTTP + readonly helper, temporary Git source and mock BAT only."""
from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path

from bat_agent_connector import api_auth, artifacts, platform_files
from tests.mockbat import TOKEN, MockBat
from tests.test_artifact_capture import daemon as daemon_fixture
from tests.test_checkpoints import MANUAL, bat_writes, snapshot
from tests.test_checkpoints import make_human as human_fixture


async def main():
    with tempfile.TemporaryDirectory(prefix="batc-capture-ui-") as temporary:
        root = Path(temporary)
        platform_files.ensure_private_directory(root / "state")
        platform_files.ensure_private_directory(root / "config")
        os.environ.update(BATC_CONFIG_DIR=str(root / "config"), BATC_STATE_DIR=str(root / "state"), BATC_TEST_TOKEN=TOKEN)
        os.environ.pop("BATC_DEVICE_ID", None)
        mock = MockBat()
        await mock.start()
        human = human_fixture(root)
        contents = bytes([0, 255, 128, 13, 10, 60, 38, 34, 195, 169])
        source = human / "notes.txt"
        source.write_bytes(contents)
        before = snapshot(human)
        fixture = daemon_fixture.__wrapped__(mock, human, root)
        daemon = await anext(fixture)
        token = api_auth.issue(daemon.journal.db, "capture-browser", ["observe", "manage"])
        server = await asyncio.start_server(daemon._handle, "127.0.0.1", 0)
        worker = asyncio.create_task(daemon.ops.loop(0.1))
        print(json.dumps({"port": server.sockets[0].getsockname()[1], "token": token,
                          "session_id": MANUAL, "digest": hashlib.sha256(contents).hexdigest(),
                          "size_bytes": len(contents)}), flush=True)
        try:
            while line := await asyncio.to_thread(sys.stdin.readline):
                command = json.loads(line)
                if command["action"] == "stop":
                    break
                if command["action"] == "verify":
                    assert source.read_bytes() == contents
                    assert snapshot(human) == before
                    assert not bat_writes(mock)
                    assert not daemon.journal.db.execute("SELECT 1 FROM work_items").fetchone()
                    assert not daemon.journal.db.execute("SELECT 1 FROM projects").fetchone()
                    operation_id = command.get("operation_id")
                    if operation_id:
                        operation = daemon.ops.get(operation_id)
                        assert operation["status"] == "succeeded", operation
                        ref = operation["result"]
                        row = artifacts.get(daemon.journal.db, ref["artifact_id"], ref["revision"])
                        assert row["source"]["kind"] == "manual_capture"
                        assert row["source"]["operation_id"] == operation_id
                        assert row["source"]["source"]["session_id"] == MANUAL
                        assert row["digest"] == hashlib.sha256(contents).hexdigest()
                        assert daemon.artifact_store.read_content(ref["artifact_id"], ref["revision"]) == contents
                        assert daemon.journal.db.execute("SELECT COUNT(*) FROM artifacts").fetchone()[0] == 1
                    else:
                        assert not daemon.journal.db.execute("SELECT 1 FROM artifacts").fetchone()
                    print(json.dumps({"verified": True, "source_unchanged": True, "bytes": list(contents)}), flush=True)
        finally:
            worker.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await worker
            server.close()
            await server.wait_closed()
            await fixture.aclose()
            await mock.stop()


if __name__ == "__main__":
    asyncio.run(main())
