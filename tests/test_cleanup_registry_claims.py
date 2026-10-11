"""Cleanup/start admission shares a registry flock and process-held claim identity."""

import asyncio
import json
import multiprocessing as mp

import pytest

from bat_agent_connector import cleanup, confinement, controller_authority, platform_files, registry
from bat_agent_connector.errors import ResourceReadOnly

SID = "cleanup-race"
pytestmark = pytest.mark.usefixtures("controller_context")


def item(host="h1", kind="session"):
    return {"resource_id": f"cleanup-{host}-{kind}", "generation": "original-generation", "host": host,
            "kind": kind, "session_id": SID if kind == "session" else None, "path": "/managed/worktree",
            "registry": registry.get(host, SID)}


def _start_owner(ready, release, authority):
    @registry.start_call
    async def start():
        registry.reserve("h1", {"session_id": SID, "start_sent": False, "cwd": "/managed/worktree"}, 10)
        ready.set()
        await asyncio.to_thread(release.wait, 15)
        registry.update("h1", SID, status="active")
    with controller_authority.use(authority):
        asyncio.run(start())


def _start_contender(result, authority):
    try:
        with controller_authority.use(authority):
            registry.reserve("h1", {"session_id": SID, "start_sent": False}, 10)
    except ResourceReadOnly as exc:
        result.put(exc.code)
    else:
        result.put("unexpected-start")


@pytest.mark.parametrize("kind", ["session", "worktree"])
def test_live_process_start_claim_wins_before_cleanup_reservation(kind, controller_context):
    ctx = mp.get_context("spawn")
    ready, release = ctx.Event(), ctx.Event()
    child = ctx.Process(target=_start_owner, args=(ready, release, controller_context[1]))
    child.start()
    try:
        assert ready.wait(15)
        path = registry.registry_path()
        before = path.read_bytes()
        with pytest.raises(confinement.ConfinementRefused) as refused:
            cleanup._mark(item(kind=kind), "cleanup-op", "reserved")
        assert refused.value.code == "START_IN_PROGRESS" and refused.value.sent is False
        assert path.read_bytes() == before
        release.set()
        child.join(15)
        assert child.exitcode == 0
        cleanup._mark(item(kind=kind), "cleanup-op", "reserved")
        assert json.loads(path.read_text())["cleanup_guards"][item(kind=kind)["resource_id"]]["status"] == "reserved"
        assert len(registry.list_entries()) == 1
    finally:
        release.set()
        if child.is_alive():
            child.terminate()
        child.join(15)


def test_cleanup_reservation_wins_before_other_process_start(controller_context):
    cleanup._mark(item(), "cleanup-op", "reserved")
    path = registry.registry_path()
    before = path.read_bytes()
    ctx = mp.get_context("spawn")
    result = ctx.Queue()
    child = ctx.Process(target=_start_contender, args=(result, controller_context[1]))
    child.start()
    try:
        assert result.get(timeout=15) == "CLEANUP_IN_PROGRESS"
        child.join(15)
        assert child.exitcode == 0
        assert path.read_bytes() == before and registry.list_entries() == []
    finally:
        if child.is_alive():
            child.terminate()
        child.join(15)
        result.close()
        result.join_thread()


@pytest.mark.parametrize("status,code", [("reserved", "CLEANUP_IN_PROGRESS"), ("cleaned", "RESOURCE_CLEANED")])
@pytest.mark.parametrize("entrypoint", ["reserve", "claim_unsent", "ensure_existing"])
def test_cleanup_blocks_unsent_recovery_before_any_registry_change(status, code, entrypoint):
    registry.reserve("h1", {"session_id": SID, "start_sent": False, "cwd": "/managed/worktree"}, 10)
    registry.update("h1", SID, status="failed")
    cleanup._mark(item(), "cleanup-op", status)
    path = registry.registry_path()
    before = path.read_bytes()
    with pytest.raises(ResourceReadOnly) as refused:
        if entrypoint == "reserve":
            registry.reserve("h1", {"session_id": SID, "start_sent": False}, 10)
        elif entrypoint == "claim_unsent":
            registry.claim_unsent("h1", SID)
        else:
            registry.ensure_existing("h1", registry.get("h1", SID))
    assert refused.value.code == code
    assert path.read_bytes() == before
    assert len(registry.list_entries()) == 1


@pytest.mark.parametrize("operation", ["reserved", "cleaned", "release", "retire"])
def test_cleanup_rejects_duplicate_registry_identity_before_writing(operation):
    path = registry.registry_path()
    platform_files.ensure_private_directory(path.parent)
    row = {"host": "h1", "session_id": SID, "created_at": 1, "status": "active"}
    platform_files.atomic_write(path, json.dumps({"sessions": [row, row]}).encode())
    before = path.read_bytes()
    with pytest.raises(registry.RegistryInvariantError, match="REGISTRY_DUPLICATE_SESSION"):
        if operation == "release":
            cleanup._release(item(), "cleanup-op")
        elif operation == "retire":
            registry.retire("h1", SID, "stopped", created_at=1, actor="fixture", reason="verified-stop")
        else:
            cleanup._mark(item(), "cleanup-op", operation)
    assert path.read_bytes() == before


def test_cleanup_release_preserves_other_host_reservation_for_same_session_id():
    for host in ("h1", "h2"):
        registry.reserve(host, {"session_id": SID, "start_sent": False}, 10)
        registry.update(host, SID, status="active")
        cleanup._mark(item(host), "same-cleanup-op", "reserved")
    cleanup._release(item(), "same-cleanup-op")
    assert registry.get("h1", SID)["cleanup_reservation"] is None
    assert registry.get("h2", SID)["cleanup_reservation"] == "same-cleanup-op"
    with pytest.raises(ResourceReadOnly, match="CLEANUP_IN_PROGRESS"):
        cleanup.guard("h2", session_id=SID)
    assert len(registry.list_entries()) == 2
