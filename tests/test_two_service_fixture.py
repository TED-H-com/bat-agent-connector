"""Pin the real producer behind the browser/native two-service isolation test."""
from __future__ import annotations

import asyncio
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlencode

import pytest


@pytest.fixture
async def sources(tmp_path):
    root = Path(__file__).resolve().parents[1]
    processes = []

    async def read(process):
        line = await asyncio.wait_for(process.stdout.readline(), 20)
        assert line, "Two-service fixture exited before replying"
        return json.loads(line)

    async def command(process, action, **data):
        process.stdin.write((json.dumps({"action": action, **data}) + "\n").encode())
        await process.stdin.drain()
        return await read(process)

    async def launch(label):
        process = await asyncio.create_subprocess_exec(sys.executable, str(root / "desktop/tests/two-service-fixture.py"), label,
            cwd=root, env={**os.environ, "TMPDIR": str(tmp_path), "TMP": str(tmp_path), "TEMP": str(tmp_path),
                "PYTHONPATH": os.pathsep.join([str(root / "src"), str(root)])},
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE)
        processes.append(process)
        return process, await read(process)

    async def request(source, path, body=None, key=None, reset=None):
        def send():
            headers = {"Authorization": "Bearer " + source["token"]}
            if body is not None:
                headers["Content-Type"] = "application/json"
            if key:
                headers["Idempotency-Key"] = key
            req = urllib.request.Request(f'http://127.0.0.1:{source["port"]}/api/v1{path}', headers=headers,
                data=json.dumps(body).encode() if body is not None else None)
            try:
                with urllib.request.urlopen(req, timeout=5) as response:
                    return json.load(response)
            except urllib.error.HTTPError as error:
                assert reset and error.code == 409
                value = json.load(error)
                assert value["error"]["code"] == "EVENT_CURSOR_RESET"
                assert value["error"]["reason"] == reset
                return value
        return await asyncio.to_thread(send)

    try:
        yield launch, command, request
    finally:
        for process in processes:
            if process.returncode is None:
                process.stdin.write(b'{"action":"stop"}\n')
                await process.stdin.drain()
                try:
                    await asyncio.wait_for(process.wait(), 3)
                except asyncio.TimeoutError:
                    process.terminate()
                    await process.wait()
        assert not list(tmp_path.iterdir()), "Fixture scratch must be removed on shutdown"


@pytest.mark.asyncio
async def test_independent_service_fixture_identity_receipt_and_same_port_handoff(sources):
    launch, command, request = sources
    (pa, a), (pb, b) = await asyncio.gather(launch("A"), launch("B"))
    assert a["actor"] == b["actor"]
    for field in ("server_id", "principal_id", "project_id", "work_item_id", "workspace_id", "token"):
        assert a[field] != b[field]
    for service, other in ((a, b), (b, a)):
        bootstrap = await request(service, "/bootstrap")
        assert bootstrap["sync"]["server_id"] == service["server_id"]
        assert bootstrap["sync"]["principal_id"] == service["principal_id"]
        hosts = await request(service, "/hosts")
        assert [host["host"] for host in hosts["hosts"]] == ["h1"]
        sessions = await request(service, "/sessions")
        assert [row["session_id"] for row in sessions["sessions"]] == ["sess-claude-0001"]
        work = await request(service, "/work-items/" + service["work_item_id"])
        assert service["marker"] in json.dumps(work) and other["marker"] not in json.dumps(work)
    body = {"action": "session.send", "target": {"host": "h1", "session_id": "sess-claude-0001"},
        "params": {"text": a["marker"], "queue": False}, "preconditions": {}}
    operation = (await request(a, "/operations?wait=0", body, "fixture-pending-key"))["operation"]
    assert operation["status"] == "accepted"
    receipts = await request(a, "/sessions/h1/sess-claude-0001/instructions")
    assert [(row["operation_id"], row["phase"]) for row in receipts["instructions"]] == [(operation["operation_id"], "pending")]
    await command(pa, "unbind")
    await command(pb, "unbind")
    b["port"] = (await command(pb, "bind", port=a["port"]))["port"]
    assert (await request(b, "/bootstrap"))["sync"]["server_id"] == b["server_id"]
    assert (await request(b, "/sessions/h1/sess-claude-0001/instructions"))["instructions"] == []
    await command(pb, "unbind")
    await command(pa, "bind", port=a["port"])
    replay = (await request(a, "/operations?wait=0", body, "fixture-pending-key"))["operation"]
    assert replay["operation_id"] == operation["operation_id"] and replay["status"] == "accepted"
    for process in (pa, pb):
        assert (await command(process, "report"))["sends"] == []


@pytest.mark.asyncio
@pytest.mark.parametrize("reason", ["cursor_expired", "access_changed"])
async def test_managed_fixture_real_runtime_rebind_first_poll_reset_and_original_receipt(sources, reason):
    launch, command, request = sources
    process, source = await launch("managed")
    bootstrap = await request(source, "/bootstrap")
    assert bootstrap["capabilities"]["desktop_identity"] == bootstrap["capabilities"]["identity"]
    body = {"action": "session.send", "target": {"host": "h1", "session_id": "sess-claude-0001"},
        "params": {"text": "Managed original instruction", "queue": False}, "preconditions": {}}
    key = "managed-original-key"
    operation = (await request(source, "/operations?wait=0", body, key))["operation"]
    assert operation["status"] == "accepted"
    receipts = await request(source, "/sessions/h1/sess-claude-0001/instructions")
    stale = (await request(source, "/bootstrap"))["sync"]["checkpoint"]
    old_port = source["port"]
    assert (await command(process, "crash"))["occupied_port"] == old_port
    if reason == "cursor_expired":
        assert (await command(process, reason))["retained_after"] > stale["cursor"]
    recovered = await command(process, "recover")
    assert recovered["same_identity_and_token"] and recovered["children"] == 2
    source["port"] = recovered["port"]
    assert source["port"] != old_port
    if reason == "access_changed":
        assert (await command(process, reason))["status"] == "succeeded"
    await request(source, "/events?" + urlencode({"after": stale["cursor"], "checkpoint": stale["token"]}), reset=reason)
    fresh = await request(source, "/bootstrap")
    assert fresh["sync"]["checkpoint"] != stale
    for field in ("server_id", "principal_id"):
        assert fresh["sync"][field] == bootstrap["sync"][field]
    assert fresh["capabilities"]["actor"] == bootstrap["capabilities"]["actor"]
    assert (await request(source, "/operations?wait=0", body, key))["operation"]["operation_id"] == operation["operation_id"]
    assert (await request(source, "/sessions/h1/sess-claude-0001/instructions"))["instructions"] == receipts["instructions"]
    report = await command(process, "report")
    assert len(report["instructions"]["operations"]) == 1 and report["sends"] == []
