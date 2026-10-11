"""Actual PM HTTP service in a disk-temporary journal; no BAT hosts or execution."""
from __future__ import annotations

import asyncio
import contextlib
import json
import os
import signal
import sys
import tempfile
import threading
from pathlib import Path

from bat_agent_connector import api_auth, platform_files
from bat_agent_connector.config import Config
from bat_agent_connector.task_daemon import TaskDaemon


async def main():
    loop, task = asyncio.get_running_loop(), asyncio.current_task()
    previous_signal = signal.signal(signal.SIGTERM, lambda *_: loop.call_soon_threadsafe(task.cancel))
    commands = asyncio.Queue()

    def read_commands():
        # Daemon reader can stay blocked on stdin when SIGTERM cancels the main task.
        # It must not hold asyncio's default executor open during shutdown.
        while True:
            line = sys.stdin.readline()
            try:
                loop.call_soon_threadsafe(commands.put_nowait, line)
            except RuntimeError:
                return
            if not line:
                return

    threading.Thread(target=read_commands, daemon=True).start()
    try:
        await serve(commands)
    finally:
        signal.signal(signal.SIGTERM, previous_signal)


async def serve(commands):
    scratch = Path(os.environ.get("BATC_PM_FIXTURE_TEMP", Path.home() / "agent-work" / "tmp" / "pytest"))
    scratch.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="pm-central-ui-", dir=scratch) as temporary:
        root = Path(temporary)
        platform_files.ensure_private_directory(root / "state")
        platform_files.ensure_private_directory(root / "config")
        os.environ.update(BATC_CONFIG_DIR=str(root / "config"), BATC_STATE_DIR=str(root / "state"))
        os.environ.pop("BATC_DEVICE_ID", None)
        daemon = TaskDaemon(Config(hosts={}), root / "journal.db")
        server = worker = None
        try:
            with daemon.journal.tx():
                token = api_auth.issue(daemon.journal.db, "pm-fixture-owner", ["observe", "manage"])
            server = await asyncio.start_server(daemon._handle, "127.0.0.1", 0)
            worker = asyncio.create_task(daemon.ops.loop(0.03))
            print(json.dumps({"port": server.sockets[0].getsockname()[1], "token": token}), flush=True)
            while line := await commands.get():
                command = json.loads(line)
                if command["action"] == "stop":
                    break
                if command["action"] != "verify":
                    raise ValueError("Unknown fixture command")
                assert not daemon._config.hosts
                assert daemon.journal.db.execute("SELECT count(*) FROM tasks").fetchone()[0] == 0
                actions = [row[0] for row in daemon.journal.db.execute("SELECT action FROM operations")]
                assert actions and all(action.startswith("pm.") for action in actions)
                print(json.dumps({"actions": actions, "tasks": 0, "hosts": 0}), flush=True)
        finally:
            if worker is not None:
                worker.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await worker
            if server is not None:
                server.close()
                await server.wait_closed()
            await daemon.ops.drain()
            await daemon.artifact_store.close_reaper()
            await daemon.fleet.close()
            await daemon.inventory.close()
            daemon.journal.close()


if __name__ == "__main__":
    with contextlib.suppress(asyncio.CancelledError):
        asyncio.run(main())
