"""A10 / 計畫 §06, §12: sent starts are fenced and registry session identities are unique."""
from __future__ import annotations

import copy
import json

import pytest

from bat_agent_connector import confinement, lifecycle, orchestrate, platform_files, registry, task_bat
from bat_agent_connector.errors import InvokeError, WriteRefused
from bat_agent_connector.task_core import TaskCoordinator
from bat_agent_connector.task_journal import Journal
from tests.test_confinement import MANAGED


def error_after_creation(mock):
    """Model BAT's late error as an actual invoke-error response on the wire."""
    attempts = []

    def start(params):
        attempts.append(params["sessionId"])
        mock.handlers.pop("claude:start-session")
        try:
            result = mock.dispatch("claude:start-session", params)
        finally:
            mock.handlers["claude:start-session"] = start
        if len(attempts) == 1:
            raise RuntimeError("fixture thread/start returned no thread id after session insertion")
        return result

    mock.handlers["claude:start-session"] = start
    return attempts


def unique_rows():
    rows = registry.list_entries()
    keys = [(row["host"], row["session_id"]) for row in rows]
    assert len(keys) == len(set(keys))
    return rows


@pytest.mark.parametrize("legacy_failed", [False, True])
async def test_a10_general_invoke_error_retains_and_fences_the_sent_id(fleet_factory, mock, legacy_failed):
    fleet = fleet_factory(writes=True, orchestrate=True, **MANAGED)
    attempts = error_after_creation(mock)
    try:
        with pytest.raises(InvokeError, match="no thread id"):
            await orchestrate.session_start(fleet, "h1", "demo-project", "codex", confirm=True,
                                            session_id="late-error")
        row = unique_rows()[0]
        assert row["status"] == "uncertain" and row["start_sent"] is True
        assert row["worktree_path"] and len(mock.worktrees) == 1 and "worktree:remove" not in mock.channels()
        if legacy_failed:
            registry.update("h1", "late-error", status="failed")  # Row written by the pre-fence version.
        before = copy.deepcopy(unique_rows())
        with pytest.raises(confinement.ConfinementRefused, match="CONFINEMENT_START_UNSETTLED"):
            await orchestrate.session_start(fleet, "h1", "demo-project", "codex", confirm=True,
                                            session_id="late-error")
        assert unique_rows() == before and attempts == ["late-error"]
        assert mock.channels().count("claude:start-session") == mock.channels().count("worktree:create") == 1
    finally:
        await fleet.close()


async def test_a10_lead_invoke_error_is_fenced_and_recovers_one_registry_row(
        fleet_factory, mock, tmp_path, monkeypatch):
    fleet = fleet_factory(writes=True, orchestrate=True, orchestrate_max_sessions=1, **MANAGED)
    journal = Journal(tmp_path / "tasks.db")
    task = journal.submit(project="p", host="h1", workspace="demo-project", original_words="Start fixture",
                          idempotency_key="invoke-error-start")
    adapter = task_bat.BatTaskAdapter(fleet, journal=journal)

    async def available(_task):
        return frozenset({"codex"})

    monkeypatch.setattr(adapter, "available_agents", available)
    attempts = error_after_creation(mock)
    core = TaskCoordinator(journal, adapter)
    try:
        result = await core._start(task, role="lead")
        command = next(c for c in journal.commands(task["task_id"]) if c["kind"] == "start_lead")
        sid = command["session_id"]
        assert result["state"] == "uncertain" and command["status"] == "uncertain"
        row = unique_rows()[0]
        assert len(unique_rows()) == 1 and row["session_id"] == sid
        assert row["status"] == "uncertain" and row["start_sent"] is True
        assert row["error_code"] == "CONFINEMENT_START_UNSETTLED"
        assert attempts == [sid] and mock.channels().count("claude:start-session") == 1
        assert "worktree:remove" not in mock.channels() and "claude:send-message" not in mock.channels()
        result = await core.tick(task["task_id"])
        assert result["state"] == "accepted" and result["session_id"] == sid
        assert len(unique_rows()) == len(registry.list_entries("h1", active_only=True)) == 1
        assert unique_rows()[0]["status"] == "active"
        assert attempts == [sid] and mock.channels().count("claude:start-session") == 1
        with pytest.raises(WriteRefused, match="cap reached.*1 active"):
            await orchestrate.session_start(fleet, "h1", "demo-project", confirm=True)
        assert len(unique_rows()) == 1 and mock.channels().count("claude:start-session") == 1
    finally:
        await fleet.close()
        journal.close()


@pytest.mark.parametrize("status,sent", [("failed", True), ("superseded", True), ("failed", None)])
def test_a10_reserve_cannot_append_another_row_for_an_existing_id(controller_context, status, sent):
    registry.reserve("h1", {"session_id": "reserved-id", "start_sent": False}, 4)
    registry.update("h1", "reserved-id", status=status, start_sent=sent)
    row = copy.deepcopy(registry.get("h1", "reserved-id"))
    with pytest.raises(confinement.ConfinementRefused, match="CONFINEMENT_START_UNSETTLED"):
        registry.reserve("h1", {"session_id": "reserved-id", "start_sent": False}, 4)
    assert unique_rows() == [row]


@pytest.mark.parametrize("reader", ["list", "reserve", "ensure_existing", "claim_warm"])
def test_a10_registry_duplicate_identity_fails_loudly_without_changes(reader):
    path = registry.registry_path()
    row = {"host": "h1", "session_id": "duplicate-id", "status": "failed", "start_sent": True}
    platform_files.atomic_write(path, json.dumps({"sessions": [row, row]}).encode())
    before = path.read_bytes()
    with pytest.raises(registry.RegistryInvariantError, match="REGISTRY_DUPLICATE_SESSION"):
        if reader == "list":
            registry.list_entries()
        elif reader == "reserve":
            registry.reserve("h1", {"session_id": "another-id"}, 4)
        elif reader == "ensure_existing":
            registry.ensure_existing("h1", row)
        else:
            registry.claim_warm("h1", "duplicate-id", previous_task_id="before", task_id="after",
                                workspace_id="ws-1", cwd="/srv/demo", branch="fixture-branch")
    assert path.read_bytes() == before


def test_a10_registry_write_rejects_duplicate_identity_atomically():
    path = registry.registry_path()
    platform_files.ensure_private_directory(path.parent)
    row = {"host": "h1", "session_id": "unique-id"}
    registry._write(path, [row])
    before = path.read_bytes()
    with pytest.raises(registry.RegistryInvariantError, match="REGISTRY_DUPLICATE_SESSION"):
        registry._write(path, [row, row])
    assert path.read_bytes() == before
    registry._write(path, [row, {**row, "host": "h2"}])
    assert len(unique_rows()) == 2  # The identity includes the host.


async def test_a10_start_failover_recovery_and_warm_claim_preserve_unique_rows(fleet_factory, mock):
    fleet = fleet_factory(writes=True, orchestrate=True, default_permission_mode="allow_all",
                          safety={"write_min_interval_s": 0}, **MANAGED)
    try:
        started = await orchestrate.session_start(fleet, "h1", "demo-project", "claude", confirm=True)
        source = started["session_id"]
        assert len(unique_rows()) == 1
        registry.ensure_existing("h1", copy.deepcopy(registry.get("h1", source)))
        assert len(unique_rows()) == 1
        attempts = error_after_creation(mock)
        with pytest.raises(InvokeError, match="no thread id"):
            await lifecycle.session_failover(fleet, "h1", source, confirm=True, force=True)
        successor = attempts[0]
        row = registry.get("h1", successor)
        assert row["start_sent"] is True and row["start_uncertain"] is True
        assert row["status"] == "starting" and row["error_code"] == "CONFINEMENT_START_UNSETTLED"
        assert len(unique_rows()) == 2 and "worktree:remove" not in mock.channels()
        recovered = await lifecycle.session_failover(fleet, "h1", source, confirm=True, force=True)
        assert recovered["new_session_id"] == successor and recovered["prompt_sent"] is True
        assert attempts == [successor] and len(unique_rows()) == 2
        assert mock.channels().count("claude:start-session") == 2  # Source + one successor frame.
        await lifecycle.session_failover(fleet, "h1", source, confirm=True, force=True)
        assert attempts == [successor] and len(unique_rows()) == 2
        registry.ensure_existing("h1", copy.deepcopy(registry.get("h1", successor)))
        assert len(unique_rows()) == 2
        registry.update("h1", successor, task_id="previous-task", role="lead")
        row = registry.get("h1", successor)
        registry.claim_warm("h1", successor, previous_task_id="previous-task", task_id="next-task",
                            workspace_id=row["workspace_id"], cwd=row["cwd"], branch=row["branch"])
        assert len(unique_rows()) == 2 and registry.get("h1", successor)["task_id"] == "next-task"
    finally:
        await fleet.close()
