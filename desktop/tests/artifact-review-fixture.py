"""Managed capture/review through actual central HTTP, temporary Git and MockBat."""
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
from tests.operation_helpers import settle_operations
from tests.test_artifact_capture import daemon as daemon_fixture
from tests.test_artifact_managed import execution as execution_fixture
from tests.test_checkpoints import bat_writes, git, snapshot
from tests.test_checkpoints import make_human as human_fixture


def worktree_snapshot(path):
    index = Path(git(path, "rev-parse", "--path-format=absolute", "--git-path", "index"))
    return {"files": {str(p.relative_to(path)): p.read_bytes() for p in path.rglob("*") if p.is_file()},
            "refs": git(path, "for-each-ref"), "head": git(path, "rev-parse", "HEAD"),
            "index": (hashlib.sha256(index.read_bytes()).hexdigest(), index.stat().st_mtime_ns)}


async def standalone_execution(daemon, mock, human, root):
    # This optional integration mode runs against a central with durable session.start.
    repository = root / "managed" / "standalone-source"
    repository.parent.mkdir(parents=True, exist_ok=True)
    git(root, "clone", "-q", str(human), str(repository))
    mock.ws_doc["workspaces"] = [{"id": "ws-start", "name": "fixture-start", "folderPath": str(repository)}]
    def create_worktree(params):
        sid = params["sessionId"]
        path = repository / ".bat-worktrees" / sid
        branch = "bat/" + sid
        git(repository, "worktree", "add", "-q", "-b", branch, str(path), params["baseBranch"])
        result = {"success": True, "worktreePath": str(path), "branchName": branch,
                  "sourceBranch": params["baseBranch"]}
        mock.worktrees[sid] = {**result, "diff": "", "merged": False, "mergedKind": "unknown"}
        mock.git_branch[str(path)] = branch
        return result
    mock.handlers["worktree:create"] = create_worktree
    operation, _ = daemon.ops.create(api_auth.Principal("fixture-starter", frozenset({"start"})),
        action="session.start", target={"host": "h1", "workspace": "ws-start"},
        params={"prompt": "Produce the fixture result."}, idempotency_key="fixture-managed-start")
    await settle_operations(daemon.ops)
    operation = daemon.ops.get(operation["operation_id"])
    assert operation["status"] == "succeeded", operation
    sid, path = operation["result"]["session_id"], Path(operation["result"]["worktree_path"])
    (path / "result.bin").write_bytes(b"result\x00\xff\n")
    return operation, sid, path


async def main():
    with tempfile.TemporaryDirectory(prefix="batc-artifact-review-ui-") as temporary:
        root = Path(temporary)
        platform_files.ensure_private_directory(root / "state")
        platform_files.ensure_private_directory(root / "config")
        os.environ.update(BATC_CONFIG_DIR=str(root / "config"), BATC_STATE_DIR=str(root / "state"), BATC_TEST_TOKEN=TOKEN)
        os.environ.pop("BATC_DEVICE_ID", None)
        mock = MockBat()
        await mock.start()
        human = human_fixture(root)
        fixture = daemon_fixture.__wrapped__(mock, human, root)
        daemon = await anext(fixture)
        operation, sid, path = (await standalone_execution(daemon, mock, human, root) if "--start" in sys.argv
                                else await execution_fixture.__wrapped__(daemon, mock))
        await daemon.inventory.refresh_host("h1")
        contents = (path / "result.bin").read_bytes()
        source_before, human_before, writes_before = worktree_snapshot(path), snapshot(human), len(bat_writes(mock))
        token = api_auth.issue(daemon.journal.db, "artifact-review-browser", ["observe", "manage", "approve"])
        reviewer = api_auth.issue(daemon.journal.db, "artifact-review-approver", ["observe", "approve"])
        server = await asyncio.start_server(daemon._handle, "127.0.0.1", 0)
        worker = asyncio.create_task(daemon.ops.loop(0.1))
        print(json.dumps({"port": server.sockets[0].getsockname()[1], "token": token, "reviewer": reviewer, "session_id": sid,
                          "execution_id": operation["operation_id"], "digest": hashlib.sha256(contents).hexdigest()}), flush=True)
        try:
            while line := await asyncio.to_thread(sys.stdin.readline):
                command = json.loads(line)
                if command["action"] == "stop":
                    break
                if command["action"] != "verify":
                    raise ValueError("Unknown fixture command")
                capture = daemon.ops.get(command["capture_id"])
                review = daemon.ops.get(command["accept_id"])
                assert capture["action"] == "artifact.capture.managed" and capture["status"] == "succeeded", capture
                assert review["action"] == "artifact.accept" and review["status"] == "succeeded", review
                assert review["actor"] == "artifact-review-approver"
                ref = capture["result"]
                row = artifacts.get(daemon.journal.db, ref["artifact_id"], ref["revision"])
                assert row["source"]["kind"] == "managed_capture"
                assert row["source"]["source"]["lineage"]["execution_operation_id"] == operation["operation_id"]
                assert row["digest"] == hashlib.sha256(contents).hexdigest()
                assert daemon.artifact_store.read_content(ref["artifact_id"], ref["revision"]) == contents
                assert len(row["acceptances"]) == 1 and row["acceptances"][0]["operation_id"] == review["operation_id"]
                for action in ("artifact.capture.managed", "artifact.accept"):
                    assert daemon.journal.db.execute("SELECT COUNT(*) FROM operations WHERE action=?", (action,)).fetchone()[0] == 1
                assert worktree_snapshot(path) == source_before and snapshot(human) == human_before
                assert len(bat_writes(mock)) == writes_before
                assert not daemon.journal.db.execute("SELECT 1 FROM tasks").fetchone()
                assert not daemon.journal.db.execute("SELECT 1 FROM work_items").fetchone()
                print(json.dumps({"verified": True, "bytes": list(contents), "acceptances": 1}), flush=True)
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
