"""Local auth producer for browser connection tests; no external providers."""
import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

from bat_agent_connector import api_auth
from bat_agent_connector.config import Config
from bat_agent_connector.task_daemon import TaskDaemon


async def main():
    root = Path.home() / "agent-work" / "tmp"
    root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="browser-auth-", dir=root) as directory:
        os.environ.update(BATC_CONFIG_DIR=str(Path(directory) / "config"),
                          BATC_STATE_DIR=str(Path(directory) / "state"))
        daemon = TaskDaemon(Config(hosts={}), Path(directory) / "tasks.db")
        server = await asyncio.start_server(daemon._handle, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        daemon._endpoint = f"http://127.0.0.1:{port}/rpc"
        token = api_auth.issue(daemon.journal.db, "browser-test", api_auth.SCOPES)
        replacement = api_auth.issue(daemon.journal.db, "browser-test", api_auth.SCOPES)
        print(json.dumps({"origin": f"http://127.0.0.1:{port}", "token": token, "replacement": replacement}), flush=True)
        try:
            while line := await asyncio.to_thread(sys.stdin.readline):
                command = json.loads(line)
                daemon.journal.db.execute(
                    "UPDATE api_principals SET revoked_at=?,scopes=? WHERE token_hash=?",
                    (1 if command.get("status") == 401 else None,
                     json.dumps(["manage"] if command.get("status") == 403 else list(api_auth.SCOPES)),
                     api_auth._digest(token)))
                print(json.dumps({"updated": True}), flush=True)
        finally:
            server.close()
            await server.wait_closed()
            await daemon.fleet.close()
            await daemon.inventory.close()
            daemon.journal.close()
            daemon.release_owner()


if __name__ == "__main__":
    asyncio.run(main())
