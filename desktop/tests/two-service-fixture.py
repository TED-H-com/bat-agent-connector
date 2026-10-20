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
import socket
import sys
import tempfile
import threading
from pathlib import Path
from urllib.parse import urlsplit

from bat_agent_connector import api_auth, dashboard_sync, managed_runtime, platform_files, pm_workspace
from bat_agent_connector.task_daemon import TaskDaemon
from tests.conftest import adopt, controller_scope, make_config
from tests.mockbat import TOKEN, MockBat
from tests.operation_helpers import settle_operations


async def seed(daemon, principal, label):
    with controller_scope(daemon.journal, principal):
        adopt("sess-claude-0001")
    await daemon.inventory.refresh_host("h1")

    async def action(name, target, params, pre=None):
        op, _ = daemon.ops.create(principal, action=name, target=target, params=params,
            preconditions=pre or {}, idempotency_key=f"fixture-{name}-{params.get('actor', '')}")
        await settle_operations(daemon.ops)
        result = daemon.ops.get(op["operation_id"])
        assert result["status"] == "succeeded", result
        return result["result"]

    project = await action("project.create", {}, {"name": "Shared project"})
    work = await action("work_item.create", {"project_id": project["project_id"]},
        {"title": "Shared work", "goal": f"ONLY_{label}_PRIVATE_FACT"})
    workspace = (await action("pm.workspace.create", {}, {"name": "Shared workspace"}))["workspace"]
    return {"project_id": project["project_id"], "work_item_id": work["work_item_id"],
        "workspace_id": workspace["workspace_id"]}


async def managed_child(root):
    """Product serve/ensure; substitute only MockBat and operation scheduling.

    The mailbox is fixture control, never a Connector route. All API handlers,
    managed proof/lease/manifest, bootstrap, events and receipts are production.
    """
    platform_files.ensure_private_directory(root / "config")
    platform_files.atomic_write(root / "config" / "hosts.toml", os.environ["BATC_FIXTURE_CONFIG"].encode())
    original = TaskDaemon.serve

    async def serve_owned(daemon, *args, **kwargs):
        token = platform_files.read_private(root / "identity.token").decode().strip()
        principal = api_auth.authenticate(daemon.journal.db, token, daemon._admin_token)
        running = daemon.ops._running
        # Keep the UI's real accepted instruction pending across the crash. Other
        # registry operations still run and settle through the production worker.
        daemon.ops._running = lambda op: daemon.ops.get(op)["action"] == "session.send" or running(op)
        if not (root / "fixture-resources.json").exists():
            resources = await seed(daemon, principal, "M")
            workspace = pm_workspace.get_workspace(daemon.journal.db, principal, resources["workspace_id"])
            op, _ = daemon.ops.create(principal, action="pm.workspace.member.set",
                target={"workspace_id": resources["workspace_id"]}, params={"actor": "fixture-second-owner", "role": "owner"},
                preconditions={"expected_version": workspace["version"]}, idempotency_key="fixture-second-owner")
            await settle_operations(daemon.ops)
            assert daemon.ops.get(op["operation_id"])["status"] == "succeeded"
            platform_files.atomic_write(root / "fixture-resources.json", json.dumps(resources).encode())

        async def mailbox():
            while True:
                request = root / "fixture-command.json"
                if request.exists():
                    command = json.loads(request.read_text())
                    request.unlink()
                    if command["action"] == "access_changed":
                        resources = json.loads((root / "fixture-resources.json").read_text())
                        workspace = pm_workspace.get_workspace(daemon.journal.db, principal, resources["workspace_id"])
                        op, _ = daemon.ops.create(principal, action="pm.workspace.member.set",
                            target={"workspace_id": resources["workspace_id"]}, params={"actor": principal.actor, "role": "contributor"},
                            preconditions={"expected_version": workspace["version"]}, idempotency_key="fixture-access-change")
                        await settle_operations(daemon.ops)
                        reply = daemon.ops.get(op["operation_id"])
                        assert reply["status"] == "succeeded", reply
                    elif command["action"] == "report":
                        reply = {"sync": dashboard_sync.checkpoint(daemon.journal, principal),
                            "instructions": daemon.ops.list(actor=principal.actor, action="session.send")}
                    else:
                        raise ValueError("Unknown managed child command")
                    platform_files.atomic_write(root / "fixture-reply.json", json.dumps(reply).encode())
                await asyncio.sleep(0.02)

        task = asyncio.create_task(mailbox())
        try:
            await original(daemon, *args, **kwargs)
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    TaskDaemon.serve = serve_owned
    await managed_runtime.serve(root)


async def serve_managed(commands):
    with tempfile.TemporaryDirectory(prefix="two-service-managed-") as temporary:
        root = Path(temporary) / "installation"
        mock = MockBat()
        mock.ws_doc["workspaces"] = mock.ws_doc["workspaces"][:1]
        mock.ws_doc["terminals"] = mock.ws_doc["terminals"][:1]
        mock.states["sess-claude-0001"]["messages"] = [{"id": "same-message", "role": "assistant",
            "content": "ONLY_M_PRIVATE_FACT", "timestamp": 1_790_000_000_000}]
        await mock.start()
        os.environ.update(BATC_TEST_TOKEN=TOKEN, BATC_FIXTURE_CONFIG=(
            f'[safety]\nwrite_min_interval_s = 0\n[hosts.h1]\nurl = {json.dumps(mock.url)}\n'
            f'fingerprint = {json.dumps(mock.fingerprint)}\ntoken_ref = "env:BATC_TEST_TOKEN"\n'
            'writes = true\nmanaged_roots = ["/srv"]\n'))
        children = []
        spawn = managed_runtime._spawn
        service_command = managed_runtime._service_command
        managed_runtime._service_command = lambda path: [sys.executable, str(Path(__file__).resolve()), "--managed-child", str(path)]

        def track(path):
            child = spawn(path)
            children.append(child)
            return child

        managed_runtime._spawn = track
        occupied = None
        try:
            launch = await asyncio.to_thread(managed_runtime.ensure, root, timeout=15)
            first = dict(launch)
            print(json.dumps({"label": "M", "marker": "ONLY_M_PRIVATE_FACT", **launch,
                **json.loads((root / "fixture-resources.json").read_text()), "port": urlsplit(launch["endpoint"]).port}), flush=True)
            while line := await commands.get():
                command = json.loads(line)
                action = command["action"]
                if action == "stop":
                    break
                if action == "crash":
                    # Only this Popen, never a PID from the managed pointer.
                    children[-1].kill()
                    await asyncio.to_thread(children[-1].wait, timeout=10)
                    occupied = socket.socket()
                    occupied.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                    occupied.bind(("127.0.0.1", urlsplit(launch["endpoint"]).port))
                    occupied.listen()
                    reply = {"crashed": True, "occupied_port": occupied.getsockname()[1]}
                elif action == "cursor_expired":
                    from bat_agent_connector.task_journal import Journal
                    journal = Journal(root / "state" / "tasks.sqlite3")
                    try:
                        # A real retained event beyond the saved cursor, followed by
                        # pruning, advances the product's retention trigger/floor.
                        head = journal.api_event("session", "h1/sess-claude-0001", "session.updated", {"revision": "retained-away"})
                        with journal.tx():
                            journal.db.execute("DELETE FROM api_events WHERE seq<=?", (head,))
                        reply = {"retained_after": head}
                    finally:
                        journal.close()
                elif action == "recover":
                    launch = await asyncio.to_thread(managed_runtime.ensure, root, timeout=15)
                    assert launch["endpoint"] != first["endpoint"]
                    for field in ("server_id", "principal_id", "actor", "token"):
                        assert launch[field] == first[field]
                    reply = {"endpoint": launch["endpoint"], "port": urlsplit(launch["endpoint"]).port,
                        "same_identity_and_token": True, "children": len(children)}
                elif action in {"access_changed", "report"}:
                    platform_files.atomic_write(root / "fixture-command.json", json.dumps(command).encode())
                    async def wait_reply():
                        while not (root / "fixture-reply.json").exists():
                            await asyncio.sleep(0.02)
                    await asyncio.wait_for(wait_reply(), 15)
                    reply = json.loads((root / "fixture-reply.json").read_text())
                    (root / "fixture-reply.json").unlink()
                    if action == "report":
                        reply["sends"] = [frame for frame in mock.invokes if frame["channel"] == "claude:send-message"]
                else:
                    raise ValueError("Unknown managed fixture command")
                print(json.dumps(reply), flush=True)
        finally:
            for child in children:
                if child.poll() is None:
                    child.terminate()
                    try:
                        await asyncio.to_thread(child.wait, timeout=10)
                    except TimeoutError:
                        child.kill()
                        await asyncio.to_thread(child.wait, timeout=10)
            if occupied:
                occupied.close()
            managed_runtime._spawn = spawn
            managed_runtime._service_command = service_command
            await mock.stop()


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
            resources = await seed(daemon, principal, label)
            metadata = {"label": label, "marker": marker, "token": token, "actor": principal.actor,
                **dashboard_sync.identity(daemon.journal, principal), **resources, "port": await bind()}
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
    assert label in {"A", "B", "managed"}
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
        if label == "managed":
            await serve_managed(commands)
        else:
            await serve(commands, label)
    finally:
        signal.signal(signal.SIGTERM, previous)


if __name__ == "__main__":
    with contextlib.suppress(asyncio.CancelledError):
        asyncio.run(managed_child(Path(sys.argv[2])) if sys.argv[1] == "--managed-child" else main())
