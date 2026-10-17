"""A03 real central UI fixture. Temporary Git source and MockBat; no live resources."""
from __future__ import annotations

import asyncio
import contextlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from bat_agent_connector import api_auth, platform_files, session_metadata
from bat_agent_connector.channels import GUARDED_CHANNELS, ORCHESTRATE_CHANNELS, WRITE_CHANNELS
from bat_agent_connector.task_daemon import TaskDaemon
from tests.conftest import make_config
from tests.mockbat import TOKEN, MockBat
from tests.operation_helpers import settle_operations

SID = "sess-claude-0001"


def git(root, *args):
    return subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True).stdout  # noqa: S603,S607 - fixed temporary fixture commands


def snapshot(root):
    return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file()}


async def main():
    with tempfile.TemporaryDirectory(prefix="batc-labels-ui-") as temporary:
        root = Path(temporary)
        platform_files.ensure_private_directory(root / "state")
        platform_files.ensure_private_directory(root / "config")
        os.environ.update(BATC_CONFIG_DIR=str(root / "config"), BATC_STATE_DIR=str(root / "state"), BATC_TEST_TOKEN=TOKEN)
        os.environ.pop("BATC_DEVICE_ID", None)
        source = root / "manual"
        source.mkdir()
        git(source, "init", "--template=", "-b", "main")
        git(source, "config", "user.name", "Synthetic Fixture")
        git(source, "config", "user.email", "fixture@example.invalid")
        (source / "code.txt").write_bytes(b"original commit\n")
        git(source, "add", "code.txt")
        git(source, "commit", "-m", "Fixture")
        (source / "code.txt").write_bytes(b"staged but unpublished\n")
        git(source, "add", "code.txt")
        (source / "code.txt").write_bytes(b"unstaged bytes\x00\xff\n")
        (source / "untracked.txt").write_text("not captured")
        before = snapshot(source)
        mock = MockBat()
        await mock.start()
        mock.metas[SID]["cwd"] = str(source)
        daemon = TaskDaemon(make_config(mock, writes=False, orchestrate=False), root / "journal.db")
        await daemon.inventory.refresh_all()
        token = api_auth.issue(daemon.journal.db, "labels-browser", ["observe", "manage"])
        person = api_auth.authenticate(daemon.journal.db, token, "synthetic-admin")
        server = await asyncio.start_server(daemon._handle, "127.0.0.1", 0)
        worker = asyncio.create_task(daemon.ops.loop(0.1))
        print(json.dumps({"port": server.sockets[0].getsockname()[1], "token": token, "sid": SID}), flush=True)
        try:
            while line := await asyncio.to_thread(sys.stdin.readline):
                command = json.loads(line)
                if command["action"] == "stop":
                    break
                if command["action"] == "other-edit":
                    current = session_metadata.read(daemon.journal.db, "h1", SID)
                    daemon.ops.create(person, action="session.labels.set", target={"host":"h1", "session_id":SID},
                        params={"labels":["Other edit"]}, preconditions={"expected_version":current["version"]}, idempotency_key="other-edit")
                    await settle_operations(daemon.ops)
                    print(json.dumps({"version": current["version"] + 1}), flush=True)
                elif command["action"] == "verify":
                    assert snapshot(source) == before  # includes exact HEAD, index, refs, objects and source bytes
                    assert not [f for f in mock.invokes if f["channel"] in WRITE_CHANNELS | ORCHESTRATE_CHANNELS | GUARDED_CHANNELS]
                    current = daemon.inventory.get_session("h1", SID)
                    assert current["provenance"] == "manual" and current["api_access"] == "read_only"
                    assert current["connector_metadata"]["labels"] == command["labels"]
                    assert current["connector_metadata"]["version"] == command["version"]
                    assert daemon.journal.db.execute("PRAGMA user_version").fetchone()[0] == 3
                    assert daemon.journal.db.execute("SELECT COUNT(*) FROM api_events WHERE kind='session.labels_updated'").fetchone()[0] == command["version"]
                    print(json.dumps({"source_unchanged":True,"version":command["version"]}), flush=True)
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
