"""A10 / 計畫 §06, §12, §28: sent Task Service starts are read back, never resent."""

from __future__ import annotations

import copy
import json

import pytest

from bat_agent_connector import confinement, registry, task_bat
from bat_agent_connector.errors import ConnectionLost
from bat_agent_connector.task_core import TaskCoordinator
from tests.test_confinement import MANAGED
from tests.test_confinement_recovery import reviewer_adapter


def unreadable_meta(kind):
    if kind == "raises":
        raise ConnectionLost("fixture metadata read disconnected")
    return None if kind == "null" else ["fixture non-dict metadata"]


async def prepare_reviewer(fleet, mock, journal, monkeypatch, agent):
    adapter, task = reviewer_adapter(fleet, mock, journal, monkeypatch)
    journal.change(task["task_id"], "dispatching")
    task = journal.change(task["task_id"], "verifying", fields={
        "session_id": task["session_id"], "lead_agent": "claude" if agent == "codex" else "codex",
        "review_commit": "a" * 40, "review_tree": "b" * 40})

    async def available(_task):
        return frozenset({agent})

    monkeypatch.setattr(adapter, "available_agents", available)
    return adapter, task


async def unsettled_reviewer(fleet_factory, mock, controller_context, monkeypatch, *, agent, reply, readback):
    fleet = fleet_factory(writes=True, orchestrate=True, safety={"write_min_interval_s": 0}, **MANAGED)
    journal, _ = controller_context
    adapter, task = await prepare_reviewer(fleet, mock, journal, monkeypatch, agent)
    client = fleet.client("h1")
    invoke = client.invoke
    reads = []
    available = False
    reserved_id = None

    async def lose_start_reply(channel, params=None, **kwargs):
        nonlocal reserved_id
        result = await invoke(channel, params, **kwargs)
        if channel == "claude:start-session":
            reserved_id = params["sessionId"]
            assert registry.get("h1", reserved_id)["start_sent"] is True
            if reply == "lost":
                raise ConnectionLost("fixture start reply lost after transport")
            if reply == "different_id":
                return {"ok": True, "sessionId": "another-reviewer"}
            return {"ok": False, "sessionId": reserved_id, "error": "fixture unconfirmed start"}
        if channel == "claude:get-session-meta" and params["sessionId"] == reserved_id:
            reads.append(reserved_id)
            if not available:
                return unreadable_meta(readback)
        return result

    monkeypatch.setattr(client, "invoke", lose_start_reply)
    core = TaskCoordinator(journal, adapter)
    try:
        result = await core._start(task, role="reviewer")
        command = next(c for c in journal.commands(task["task_id"]) if c["kind"] == "start_reviewer")
        sid = command["session_id"]
        row = copy.deepcopy(registry.get("h1", sid))
        assert result["state"] == "uncertain" and result["reviewer_session_id"] is None
        assert command["status"] == "uncertain"
        assert json.loads(command["payload"])["start_sent"] is True
        assert row["status"] == "uncertain" and row["start_sent"] is True
        assert row["error_code"] == "CONFINEMENT_START_UNSETTLED"
        assert reads == [sid] * 3 and mock.channels().count("claude:start-session") == 1
        assert not any(c["kind"] == "send" for c in journal.commands(task["task_id"]))
        assert "claude:send-message" not in mock.channels()
        with pytest.raises(confinement.ConfinementRefused, match="CONFINEMENT_START_UNSETTLED"):
            await adapter.start(result, role="reviewer", agent=agent, session_id=sid)
        assert registry.get("h1", sid) == row and reads == [sid] * 3

        available = True
        recovered = await core.tick(task["task_id"])
        assert recovered["state"] == "verifying" and recovered["reviewer_session_id"] == sid
        assert journal.command_get(command["command_id"])["status"] == "settled"
        assert registry.get("h1", sid)["status"] == "active"
        assert registry.get("h1", sid)["error_code"] is None
        assert registry.get("h1", sid)["confinement"]["verification"]["status"] == "options_confirmed"
        assert mock.channels().count("claude:start-session") == 1
        assert "claude:send-message" not in mock.channels()
    finally:
        await fleet.close()


@pytest.mark.parametrize("agent", ["codex", "claude"])
@pytest.mark.parametrize("readback", ["raises", "null", "non_dict"])
async def test_reviewer_lost_reply_polls_only_and_recovers_next_tick(
        fleet_factory, mock, controller_context, monkeypatch, agent, readback):
    await unsettled_reviewer(fleet_factory, mock, controller_context, monkeypatch,
                             agent=agent, reply="lost", readback=readback)


@pytest.mark.parametrize("agent", ["codex", "claude"])
@pytest.mark.parametrize("reply", ["different_id", "ok_false"])
async def test_reviewer_unconfirmed_reply_keeps_single_start_frame(
        fleet_factory, mock, controller_context, monkeypatch, agent, reply):
    await unsettled_reviewer(fleet_factory, mock, controller_context, monkeypatch,
                             agent=agent, reply=reply, readback="null")


@pytest.mark.parametrize("readback", ["raises", "null", "non_dict"])
async def test_reviewer_readback_backoff_can_settle_without_another_start(
        fleet_factory, mock, controller_context, monkeypatch, readback):
    fleet = fleet_factory(writes=True, orchestrate=True, **MANAGED)
    journal, _ = controller_context
    adapter, task = reviewer_adapter(fleet, mock, journal, monkeypatch)
    client = fleet.client("h1")
    invoke = client.invoke
    reads = []

    async def transient_read(channel, params=None, **kwargs):
        result = await invoke(channel, params, **kwargs)
        if channel == "claude:start-session":
            raise ConnectionLost("fixture start reply lost after transport")
        if channel == "claude:get-session-meta" and params["sessionId"] == "review-transient":
            reads.append(params["sessionId"])
            if len(reads) < 3:
                return unreadable_meta(readback)
        return result

    monkeypatch.setattr(client, "invoke", transient_read)
    try:
        sid = await adapter.start(task, role="reviewer", agent="codex", session_id="review-transient")
        assert registry.get("h1", sid)["status"] == "active"
        assert registry.get("h1", sid)["confinement"]["verification"]["status"] == "options_confirmed"
        assert reads == [sid] * 3 and mock.channels().count("claude:start-session") == 1
    finally:
        await fleet.close()


@pytest.mark.parametrize("readback", ["raises", "null", "non_dict"])
async def test_lead_lost_reply_retry_is_fenced_until_readback_recovers(
        fleet_factory, mock, controller_context, monkeypatch, readback):
    fleet = fleet_factory(writes=True, orchestrate=True, **MANAGED)
    journal, _ = controller_context
    task = journal.submit(project="p", host="h1", workspace="demo-project", original_words="Start fixture",
                          idempotency_key="lead-single-frame")
    adapter = task_bat.BatTaskAdapter(fleet, journal=journal)
    client = fleet.client("h1")
    invoke = client.invoke
    available = False

    async def available_agents(_task):
        return frozenset({"codex"})

    async def lost_ack(channel, params=None, **kwargs):
        result = await invoke(channel, params, **kwargs)
        if channel == "claude:start-session":
            raise ConnectionLost("fixture lead start reply lost after transport")
        if channel == "claude:get-session-meta" and not available:
            return unreadable_meta(readback)
        return result

    monkeypatch.setattr(adapter, "available_agents", available_agents)
    monkeypatch.setattr(client, "invoke", lost_ack)
    core = TaskCoordinator(journal, adapter)
    try:
        result = await core._start(task, role="lead")
        command = next(c for c in journal.commands(task["task_id"]) if c["kind"] == "start_lead")
        sid = command["session_id"]
        assert result["state"] == "uncertain" and command["status"] == "uncertain"
        assert registry.get("h1", sid)["status"] == "uncertain"
        assert registry.get("h1", sid)["start_sent"] is True
        assert json.loads(command["payload"])["start_sent"] is True
        if readback == "raises":
            with pytest.raises(ConnectionLost):
                await core.tick(task["task_id"])
        else:
            assert (await core.tick(task["task_id"]))["state"] == "uncertain"
        assert mock.channels().count("claude:start-session") == 1
        assert "claude:send-message" not in mock.channels()
        available = True
        recovered = await core.tick(task["task_id"])
        assert recovered["state"] == "accepted" and recovered["session_id"] == sid
        assert registry.get("h1", sid)["status"] == "active"
        assert mock.channels().count("claude:start-session") == 1
    finally:
        await fleet.close()
