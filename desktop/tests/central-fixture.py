"""Actual Connector HTTP with temporary journal and MockBat; controlled only by test stdin."""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

from bat_agent_connector import api_auth, platform_files
from bat_agent_connector.task_daemon import TaskDaemon
from tests.conftest import adopt, controller_scope, make_config
from tests.mockbat import TOKEN, MockBat


async def main():
    with tempfile.TemporaryDirectory(prefix="batc-central-ui-") as temporary:
        root = Path(temporary)
        platform_files.ensure_private_directory(root / "state")
        platform_files.ensure_private_directory(root / "config")
        os.environ.update(BATC_CONFIG_DIR=str(root / "config"), BATC_STATE_DIR=str(root / "state"),
                          BATC_TEST_TOKEN=TOKEN)
        mock = MockBat()
        await mock.start()
        daemon = TaskDaemon(make_config(mock, managed_roots=["/srv"]), root / "journal.db")
        with daemon.journal.tx():
            token = api_auth.issue(daemon.journal.db, "fixture-operator", ["observe", "operate"])
        principal = api_auth.authenticate(daemon.journal.db, token, daemon._admin_token)
        with controller_scope(daemon.journal, principal):
            adopt("sess-claude-0001")
        await daemon.inventory.refresh_host("h1")
        server = await asyncio.start_server(daemon._handle, "127.0.0.1", 0)
        print(json.dumps({"port": server.sockets[0].getsockname()[1], "token": token}), flush=True)
        try:
            while line := await asyncio.to_thread(sys.stdin.readline):
                action = json.loads(line)["action"]
                if action == "append":
                    cursor = daemon.journal.api_event("session", "h1/sess-claude-0001", "session.updated", {})
                    print(json.dumps({"cursor": cursor}), flush=True)
                elif action == "prune":
                    daemon.journal.db.execute("DELETE FROM api_events")
                    print(json.dumps({"cursor": daemon.journal.api_head()}), flush=True)
                elif action == "stop":
                    break
                else:
                    raise ValueError("Unknown fixture control")
        finally:
            server.close()
            await server.wait_closed()
            await daemon.inventory.close()
            daemon.journal.close()
            await mock.stop()


if __name__ == "__main__":
    asyncio.run(main())
