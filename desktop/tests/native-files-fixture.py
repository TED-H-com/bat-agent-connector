"""Temporary real-central fixture for Rust native transfers; no live providers or hosts."""
from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path

from bat_agent_connector import api_auth, platform_files
from tests.mockbat import TOKEN, MockBat
from tests.test_checkpoints import daemon as daemon_fixture
from tests.test_checkpoints import make_human as human_fixture


def source_snapshot(repo: Path):
    git_dir = repo / ".git"
    refs = [git_dir / "HEAD", *sorted((git_dir / "refs").rglob("*"))]
    if (git_dir / "packed-refs").exists():
        refs.append(git_dir / "packed-refs")
    return {
        "bytes": hashlib.sha256((repo / "notes.txt").read_bytes()).hexdigest(),
        "index": hashlib.sha256((git_dir / "index").read_bytes()).hexdigest(),
        "refs": {str(path.relative_to(git_dir)): hashlib.sha256(path.read_bytes()).hexdigest()
                 for path in refs if path.is_file()},
    }


async def main():
    with tempfile.TemporaryDirectory(prefix="batc-native-central-") as temporary:
        root = Path(temporary)
        platform_files.ensure_private_directory(root / "state")
        platform_files.ensure_private_directory(root / "config")
        os.environ.update(BATC_CONFIG_DIR=str(root / "config"), BATC_STATE_DIR=str(root / "state"), BATC_TEST_TOKEN=TOKEN)
        os.environ.pop("BATC_DEVICE_ID", None)
        mock = MockBat()
        await mock.start()
        human = human_fixture(root)
        before = source_snapshot(human)
        fixture = daemon_fixture.__wrapped__(mock, human, root)
        daemon = next(fixture)
        token = api_auth.issue(daemon.journal.db, "native-file-fixture", ["observe", "manage"])
        server = await asyncio.start_server(daemon._handle, "127.0.0.1", 0)
        worker = asyncio.create_task(daemon.ops.loop(0.05))
        print(json.dumps({"endpoint": f"http://127.0.0.1:{server.sockets[0].getsockname()[1]}/", "token": token}), flush=True)
        try:
            while line := await asyncio.to_thread(sys.stdin.readline):
                command = json.loads(line)
                if command["action"] == "stop":
                    break
                if command["action"] == "proof":
                    operations = daemon.journal.db.execute("SELECT operation_id,status FROM operations ORDER BY created_at").fetchall()
                    uploads = daemon.journal.db.execute("SELECT attempt FROM artifact_uploads ORDER BY operation_id").fetchall()
                    print(json.dumps({"source_unchanged": before == source_snapshot(human),
                                      "operations": [dict(row) for row in operations],
                                      "upload_attempts": [row[0] for row in uploads]}), flush=True)
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
