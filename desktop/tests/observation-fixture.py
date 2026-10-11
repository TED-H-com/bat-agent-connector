"""Real observation HTTP/journal with only temporary identities and MockBat state."""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

from bat_agent_connector import api_auth, observation, platform_files
from bat_agent_connector.task_daemon import TaskDaemon
from tests.conftest import adopt, controller_scope, make_config
from tests.mockbat import TOKEN, MockBat


async def main():
    with tempfile.TemporaryDirectory(prefix="batc-observation-ui-") as temporary:
        root = Path(temporary)
        platform_files.ensure_private_directory(root / "state")
        platform_files.ensure_private_directory(root / "config")
        os.environ.update(BATC_CONFIG_DIR=str(root / "config"), BATC_STATE_DIR=str(root / "state"), BATC_TEST_TOKEN=TOKEN)
        mock = MockBat()
        await mock.start()
        daemon = TaskDaemon(make_config(mock, managed_roots=["/srv"]), root / "journal.db")
        sid = "sess-claude-0001"
        journal = daemon.journal
        token = api_auth.issue(journal.db, "observation-browser", ["observe", "operate"])
        principal = api_auth.authenticate(journal.db, token, daemon._admin_token)
        with controller_scope(journal, principal):
            adopt(sid)
            task = journal.submit(project="fixture", host="h1", workspace="w", original_words="Fixture observation only", idempotency_key="fixture")
            command, _ = journal.command(task["task_id"], "start_lead", sid, {"agent": "claude"}, "fixture-start")
            journal.command_status(command["command_id"], "settled")
            journal.add_branch(task["task_id"], session_id=sid, provider="claude", role="lead", reason="start")
        await daemon.inventory.refresh_host("h1")
        wid = observation.worktree(journal, "h1", "fixture", "fixture-intent", "lead", path="/srv/fixture-worktree", session_id=sid)
        for _ in range(25):
            journal.api_event("session", f"h1/{sid}", "session.updated", {"worktree_id": wid})
        server = await asyncio.start_server(daemon._handle, "127.0.0.1", 0)
        print(json.dumps({"port": server.sockets[0].getsockname()[1], "token": token,
                          "execution_id": task["task_id"], "worktree_id": wid}), flush=True)
        try:
            while line := await asyncio.to_thread(sys.stdin.readline):
                action = json.loads(line)["action"]
                if action == "stop":
                    break
                if action == "question":
                    mock.metas[sid]["isStreaming"] = True
                    mock.states[sid]["pendingAskUser"] = {"toolUseId": "fixture-question", "questions": [{"question": "Which fixture branch?"}]}
                    await daemon.inventory.refresh_host("h1")
                elif action == "append":
                    journal.api_event("session", f"h1/{sid}", "session.updated", {"worktree_id": wid})
                else:
                    raise ValueError("Unknown fixture action")
                print(json.dumps({"cursor": journal.api_head()}), flush=True)
        finally:
            server.close()
            await server.wait_closed()
            await daemon.artifact_store.close_reaper()
            await daemon.inventory.close()
            journal.close()
            await mock.stop()


if __name__ == "__main__":
    asyncio.run(main())
