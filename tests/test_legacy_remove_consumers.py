"""Legacy removal must keep a worktree still used by another execution (MockBat only)."""
from __future__ import annotations

import json

import pytest

from bat_agent_connector import orchestrate, platform_files, registry, service
from bat_agent_connector.errors import ResourceReadOnly
from bat_agent_connector.task_journal import Journal


@pytest.fixture
def dependency_journal(tmp_path):
    journal = Journal(tmp_path / "central.sqlite")
    pointer = registry.registry_path().parent / service.TASK_SERVICE_POINTER
    platform_files.atomic_write(pointer, json.dumps({"db_path": str(journal.path)}).encode())
    yield journal
    journal.close()


async def standalone(fleet_factory, mock):
    # Unrelated terminals have positive live cwd evidence, including a shell tab.
    for t in mock.ws_doc["terminals"]:
        mock.metas[t["id"]] = {"cwd": t["cwd"], "isStreaming": False}
        mock.states[t["id"]] = {"isStreaming": False}
    fleet = fleet_factory(writes=True, orchestrate=True, safety={"write_min_interval_s": 0})
    source = await orchestrate.session_start(fleet, "h1", "demo-project", confirm=True)
    return fleet, source


def successor(mock, source):
    sid = "successor-sharing-0001"
    old = {k: v for k, v in registry.get("h1", source["session_id"]).items()
           if k not in {"controller_authority", "controller_incarnation"}}
    registry.reserve("h1", {**old, "session_id": sid, "failover_of": source["session_id"],
                            "shares_worktree_with": source["session_id"]}, 10)
    registry.update("h1", sid, status="active")
    mock.metas[sid] = {"cwd": source["worktree_path"], "isStreaming": True}
    mock.states[sid] = {"isStreaming": True}
    return sid


@pytest.mark.parametrize("delete_branch", [False, True])
@pytest.mark.parametrize("allow_unmerged", [False, True])
@pytest.mark.parametrize("discard_uncommitted", [False, True])
async def test_idle_predecessor_cannot_remove_active_successors_shared_worktree(
        fleet_factory, mock, dependency_journal, delete_branch, allow_unmerged, discard_uncommitted):
    fleet, source = await standalone(fleet_factory, mock)
    successor(mock, source)
    before = len(mock.invokes)
    try:
        with pytest.raises(ResourceReadOnly, match="LEGACY_WORKTREE_REMOVE_DISABLED"):
            await orchestrate.worktree_remove(fleet, "h1", source["session_id"], confirm=True,
                                              delete_branch=delete_branch, discard_uncommitted=discard_uncommitted,
                                              allow_unmerged=allow_unmerged)
        assert source["worktree_path"] not in mock.removed_paths
        assert registry.get("h1", source["session_id"])["status"] == "active"
        assert not any(i["channel"] in {"worktree:remove", "worktree:rehydrate"}
                       for i in mock.invokes[before:])
    finally:
        await fleet.close()


async def test_legacy_remove_refusal_does_not_create_a_dependency_journal(fleet_factory, mock):
    fleet, source = await standalone(fleet_factory, mock)
    before = len(mock.invokes)
    try:
        with pytest.raises(ResourceReadOnly, match="cleanup_preview and cleanup_apply"):
            await orchestrate.worktree_remove(fleet, "h1", source["session_id"], confirm=True)
        assert service.task_service_db() is None
        assert not any(i["channel"] in {"worktree:remove", "worktree:rehydrate"}
                       for i in mock.invokes[before:])
        assert not mock.removed_paths
    finally:
        await fleet.close()


async def test_operator_mcp_cannot_reopen_legacy_removal(fleet_factory, mock):
    from bat_agent_connector.mcp_server import build_server

    fleet, source = await standalone(fleet_factory, mock)
    successor(mock, source)
    server, operator = build_server(fleet.config)
    before = len(mock.invokes)
    try:
        try:
            result = await server.call_tool("worktree_remove", {"host": "h1", "session_id": source["session_id"],
                "confirm": True, "delete_branch": True, "allow_unmerged": True, "discard_uncommitted": True})
            message = json.dumps(result.model_dump() if hasattr(result, "model_dump") else result, default=str)
        except Exception as exc:  # noqa: BLE001 - MCP transports may return or raise the same refusal.
            message = str(exc)
        assert "LEGACY_WORKTREE_REMOVE_DISABLED" in message
        assert source["worktree_path"] not in mock.removed_paths
        assert not any(i["channel"] in {"worktree:remove", "worktree:rehydrate"}
                       for i in mock.invokes[before:])
    finally:
        await operator.close()
        await fleet.close()
