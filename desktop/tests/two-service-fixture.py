"""Independent real HTTP/journal + MockBat, following central-fixture.py.

Only listener lifetime and worker scheduling are controlled by stdin. After real
registry actions seed the fixture, admitted UI instructions deliberately stay pending.
No HTTP response, source identity, receipt or idempotency lookup is substituted.
"""
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

from bat_agent_connector import api_auth, dashboard_sync, platform_files
from bat_agent_connector.task_daemon import TaskDaemon
from tests.conftest import adopt, controller_scope, make_config
from tests.mockbat import TOKEN, MockBat
from tests.operation_helpers import settle_operations


async def serve(commands, label):
    with tempfile.TemporaryDirectory(prefix=f"two-service-{label}-") as temporary:
        root = Path(temporary)
        for name in ("state", "config"):
            platform_files.ensure_private_directory(root / name)
        os.environ.update(BATC_CONFIG_DIR=str(root / "config"), BATC_STATE_DIR=str(root / "state"), BATC_TEST_TOKEN=TOKEN)
        os.environ.pop("BATC_DEVICE_ID", None)
        marker = f"ONLY_{label}_PRIVATE_FACT"
        mock = MockBat()
        mock.ws_doc["workspaces"] = mock.ws_doc["workspaces"][:1]
        mock.ws_doc["terminals"] = mock.ws_doc["terminals"][:1]
        mock.states["sess-claude-0001"]["messages"] = [{"id": "same-message", "role": "assistant",
            "content": marker, "timestamp": 1_790_000_000_000}]
        await mock.start()
        daemon = TaskDaemon(make_config(mock, writes=True, managed_roots=["/srv"],
            safety={"write_min_interval_s": 0}), root / "journal.db")
        server = None
        connections, handlers = set(), set()

        async def handle(reader, writer):
            task = asyncio.current_task()
            connections.add(writer)
            handlers.add(task)
            try:
                await daemon._handle(reader, writer)
            finally:
                connections.discard(writer)
                handlers.discard(task)

        async def unbind():
            nonlocal server
            if server:
                server.close()
                await server.wait_closed()
                server = None
            for writer in list(connections):
                writer.close()
            for task in list(handlers):
                task.cancel()
            await asyncio.gather(*list(handlers), return_exceptions=True)

        async def bind(port=0):
            nonlocal server
            await unbind()
            server = await asyncio.start_server(handle, "127.0.0.1", port)
            return server.sockets[0].getsockname()[1]

        try:
            scopes = ["observe", "operate", "manage"] + (["approve"] if label == "B" else [])
            token = api_auth.issue(daemon.journal.db, "fixture-operator", scopes)
            principal = api_auth.authenticate(daemon.journal.db, token, daemon._admin_token)
            with controller_scope(daemon.journal, principal):
                adopt("sess-claude-0001")
            await daemon.inventory.refresh_host("h1")

            async def seed(action, target, params):
                op, _ = daemon.ops.create(principal, action=action, target=target, params=params,
                    preconditions={}, idempotency_key=f"fixture-{action}")
                await settle_operations(daemon.ops)
                result = daemon.ops.get(op["operation_id"])
                assert result["status"] == "succeeded", result
                return result["result"]

            project = await seed("project.create", {}, {"name": "Shared project"})
            work = await seed("work_item.create", {"project_id": project["project_id"]},
                {"title": "Shared work", "goal": marker})
            workspace = await seed("pm.workspace.create", {}, {"name": "Shared workspace"})
            metadata = {"label": label, "marker": marker, "token": token, "actor": principal.actor,
                **dashboard_sync.identity(daemon.journal, principal), "project_id": project["project_id"],
                "work_item_id": work["work_item_id"], "workspace_id": workspace["workspace"]["workspace_id"], "port": await bind()}
            print(json.dumps(metadata), flush=True)
            # Intentionally no ops.loop: the real operation/receipt remains pending.
            while line := await commands.get():
                command = json.loads(line)
                if command["action"] == "stop":
                    break
                if command["action"] == "unbind":
                    await unbind()
                    reply = {"listening": False}
                elif command["action"] == "bind":
                    reply = {"port": await bind(command.get("port", 0))}
                elif command["action"] == "report":
                    reply = {"sync": dashboard_sync.checkpoint(daemon.journal, principal),
                        "instructions": daemon.ops.list(actor=principal.actor, action="session.send"),
                        "sends": [frame for frame in mock.invokes if frame["channel"] == "claude:send-message"]}
                else:
                    raise ValueError("Unknown fixture command")
                print(json.dumps(reply), flush=True)
        finally:
            await unbind()
            await daemon.ops.drain()
            await daemon.artifact_store.close_reaper()
            await daemon.fleet.close()
            await daemon.inventory.close()
            daemon.journal.close()
            await mock.stop()


async def main():
    label = sys.argv[1]
    assert label in {"A", "B"}
    loop, task = asyncio.get_running_loop(), asyncio.current_task()
    previous = signal.signal(signal.SIGTERM, lambda *_: loop.call_soon_threadsafe(task.cancel))
    commands = asyncio.Queue()

    def read():
        while line := sys.stdin.readline():
            loop.call_soon_threadsafe(commands.put_nowait, line)
        with contextlib.suppress(RuntimeError):
            loop.call_soon_threadsafe(commands.put_nowait, "")

    threading.Thread(target=read, daemon=True).start()
    try:
        await serve(commands, label)
    finally:
        signal.signal(signal.SIGTERM, previous)


if __name__ == "__main__":
    with contextlib.suppress(asyncio.CancelledError):
        asyncio.run(main())
