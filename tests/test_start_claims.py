"""A10 / 計畫 §06, §12: only a dead owner's unsent start can be reclaimed."""

from __future__ import annotations

import asyncio
import hashlib
import json
import multiprocessing as mp
import os
import stat

import pytest

from bat_agent_connector import (
    confinement,
    controller_authority,
    lifecycle,
    orchestrate,
    platform_files,
    registry,
)
from bat_agent_connector.fleet import Fleet
from bat_agent_connector.task_journal import Journal
from tests.conftest import controller_scope, make_config
from tests.test_confinement import MANAGED
from tests.test_confinement_recovery import reviewer_adapter
from tests.test_lifecycle import _add_wt_claude

SID = "claimed-start"


def _recover_process(config, gate, ready, release, authority):
    """A real connector process; only the winner is paused before its BAT frame."""
    async def run():
        fleet = Fleet(config, idle_timeout=0, actor="test")
        client = fleet.client("h1")
        invoke = client.invoke

        async def paused(channel, params=None, **kwargs):
            if channel == "claude:start-session":
                ready.put(("claimed", registry.get("h1", SID)))
                assert await asyncio.to_thread(release.wait, 15)
            return await invoke(channel, params, **kwargs)

        client.invoke = paused
        try:
            result = await orchestrate.session_start(fleet, "h1", "demo-project", confirm=True, session_id=SID)
            ready.put(("started", result))
        except confinement.ConfinementRefused as exc:
            ready.put((exc.code, registry.get("h1", SID)))
        finally:
            await fleet.close()

    assert gate.wait(15)
    with controller_authority.use(authority):
        asyncio.run(run())


def _crash_reservation(entry, ready, release=None, replaces=None, *, authority):
    with controller_authority.use(authority):
        registry.reserve("h1", entry, 10, replaces=replaces)
    ready.set()
    if release is not None:
        assert release.wait(15)
    os._exit(0)  # no Python cleanup: the OS must release the flock


def _seed_unsent(status):
    path = registry.registry_path()
    # Explicit original creation proof for an abandoned reservation, not a legacy adoption.
    row = controller_authority.stamp({"host": "h1", "session_id": SID,
                                      "status": status, "start_sent": False})
    platform_files.atomic_write(path, json.dumps({"sessions": [row]}).encode())


async def _join(process):
    await asyncio.to_thread(process.join, 10)
    assert process.exitcode == 0


@pytest.mark.parametrize("status", ["starting", "failed"])
async def test_two_processes_reclaim_unsent_start_only_once(mock, status, controller_context):
    """Both a crash-before-frame row and a failed unsent row admit one winner."""
    _seed_unsent(status)
    ctx = mp.get_context("spawn")
    gates, release, ready = [ctx.Event(), ctx.Event()], ctx.Event(), ctx.Queue()
    config = make_config(mock, writes=True, orchestrate=True, safety={"write_min_interval_s": 0}, **MANAGED)
    processes = [ctx.Process(target=_recover_process, args=(config, gate, ready, release, controller_context[1])) for gate in gates]
    for process in processes:
        process.start()
    try:
        gates[0].set()
        results = [await asyncio.to_thread(ready.get, True, 15)]
        gates[1].set()
        results.append(await asyncio.to_thread(ready.get, True, 15))
        assert {kind for kind, _ in results} == {"claimed", "START_IN_PROGRESS"}
        winner = next(row for kind, row in results if kind == "claimed")
        refused = next(row for kind, row in results if kind == "START_IN_PROGRESS")
        assert registry.get("h1", SID) == refused == winner
        assert winner["status"] == "starting" and winner["start_sent"] is False
        assert winner["start_claim_token"]
        assert "claude:start-session" not in mock.channels()
        assert mock.channels().count("worktree:create") == 1
        release.set()
        kind, result = await asyncio.to_thread(ready.get, True, 15)
        assert kind == "started" and result["session_id"] == SID
        for process in processes:
            await _join(process)
        assert mock.channels().count("claude:start-session") == 1
        assert sum(row["session_id"] == SID for row in registry.list_entries("h1")) == 1
        assert registry.get("h1", SID)["start_claim_token"] is None
        digest = hashlib.sha256(("h1\0" + SID).encode()).hexdigest()
        path = registry.registry_path().parent / "start-claims" / (digest + ".lock")
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
    finally:
        release.set()
        for gate in gates:
            gate.set()
        for process in processes:
            if process.is_alive():
                process.terminate()
            await asyncio.to_thread(process.join, 10)
        ready.close()
        ready.join_thread()


async def test_crashed_owner_releases_unsent_start_claim(fleet_factory, mock):
    ctx = mp.get_context("spawn")
    ready = ctx.Event()
    child = ctx.Process(target=_crash_reservation, args=({"session_id": SID, "start_sent": False}, ready),
                        kwargs={"authority": controller_authority.require()})
    child.start()
    assert await asyncio.to_thread(ready.wait, 10)
    await _join(child)
    row = registry.get("h1", SID)
    assert row["status"] == "starting" and row["start_claim_token"]
    fleet = fleet_factory(writes=True, orchestrate=True, safety={"write_min_interval_s": 0}, **MANAGED)
    try:
        result = await orchestrate.session_start(fleet, "h1", "demo-project", confirm=True, session_id=SID)
        assert result["session_id"] == SID
        assert mock.channels().count("claude:start-session") == 1
        assert registry.get("h1", SID)["status"] == "active"
    finally:
        await fleet.close()


@pytest.mark.parametrize("path", ["start", "failover", "task_lead", "reviewer"])
async def test_live_start_claim_refuses_another_coroutine_before_worktree_or_frame(
        fleet_factory, mock, tmp_path, monkeypatch, path):
    fleet = fleet_factory(writes=True, orchestrate=True, safety={"write_min_interval_s": 0}, **MANAGED)
    journal = Journal(tmp_path / "claims.db")
    with controller_scope(journal):
        entered, release = asyncio.Event(), asyncio.Event()
        client = fleet.client("h1")
        invoke = client.invoke
        adapter, task, lead = None, None, None
        if path == "failover":
            lead = _add_wt_claude(mock)
        elif path in {"task_lead", "reviewer"}:
            adapter, task = reviewer_adapter(fleet, mock, journal, monkeypatch)

        async def paused(channel, params=None, **kwargs):
            if channel == "claude:start-session":
                entered.set()
                await release.wait()
            return await invoke(channel, params, **kwargs)

        client.invoke = paused

        async def start():
            if path == "start":
                return await orchestrate.session_start(fleet, "h1", "demo-project", confirm=True, session_id=SID)
            if path == "failover":
                return await lifecycle.session_failover(fleet, "h1", lead, confirm=True, successor_session_id=SID)
            return await adapter.start(task, role="lead" if path == "task_lead" else "reviewer",
                                       agent="codex", session_id=SID)

        pending = asyncio.create_task(start())
        try:
            await asyncio.wait_for(entered.wait(), 10)
            row = registry.get("h1", SID)
            baseline = list(mock.invokes)
            with pytest.raises(confinement.ConfinementRefused, match="START_IN_PROGRESS") as refused:
                await asyncio.create_task(start())
            assert refused.value.code == "START_IN_PROGRESS" and refused.value.sent is False
            assert registry.get("h1", SID) == row
            assert not any(i["channel"].startswith("worktree:") or i["channel"] == "claude:start-session"
                           for i in mock.invokes[len(baseline):])
            # An unrelated coroutine also cannot release the live reservation.
            with pytest.raises(confinement.ConfinementRefused, match="START_IN_PROGRESS"):
                registry.fail_reservation("h1", SID)
            with pytest.raises(confinement.ConfinementRefused, match="START_IN_PROGRESS"):
                registry.update("h1", SID, status="failed")
            with pytest.raises(confinement.ConfinementRefused, match="START_IN_PROGRESS"):
                registry.ensure_existing("h1", row)
            assert registry.get("h1", SID) == row
            release.set()
            await pending
            assert mock.channels().count("claude:start-session") == 1
        finally:
            release.set()
            if not pending.done():
                pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)
            await fleet.close()
            journal.close()


async def test_child_coroutine_cannot_borrow_parent_start_claim(controller_context):
    @registry.start_call
    async def owner():
        registry.reserve("h1", {"session_id": SID, "start_sent": False}, 10)

        async def contender():
            with pytest.raises(confinement.ConfinementRefused, match="START_IN_PROGRESS"):
                registry.reserve("h1", {"session_id": SID, "start_sent": False}, 10)
            with pytest.raises(confinement.ConfinementRefused, match="START_IN_PROGRESS"):
                registry.fail_reservation("h1", SID)

        await asyncio.create_task(contender())  # inherits the owner's ContextVar
        registry.fail_reservation("h1", SID)

    await owner()
    registry.reserve("h1", {"session_id": SID, "start_sent": False}, 10)
    registry.fail_reservation("h1", SID)


async def test_start_claim_survives_failed_status_until_owner_returns(controller_context):
    entered, release = asyncio.Event(), asyncio.Event()

    @registry.start_call
    async def owner():
        registry.reserve("h1", {"session_id": SID, "start_sent": False}, 10)
        registry.fail_reservation("h1", SID)
        entered.set()
        await release.wait()

    pending = asyncio.create_task(owner())
    try:
        await asyncio.wait_for(entered.wait(), 10)
        before = registry.get("h1", SID)
        with pytest.raises(confinement.ConfinementRefused, match="START_IN_PROGRESS"):
            registry.reserve("h1", {"session_id": SID, "start_sent": False}, 10)
        assert registry.get("h1", SID) == before
    finally:
        release.set()
        await pending
    registry.reserve("h1", {"session_id": SID, "start_sent": False}, 10)
    registry.fail_reservation("h1", SID)


async def test_nested_start_releases_its_claim_before_owner_retries(controller_context):
    _seed_unsent("failed")

    @registry.start_call
    async def attempt():
        registry.claim_unsent("h1", SID)
        registry.reserve("h1", {"session_id": SID, "start_sent": False}, 10)
        registry.fail_reservation("h1", SID)
        raise RuntimeError("fixture start raised")

    @registry.start_call
    async def owner():
        registry.claim_unsent("h1", SID)  # preparation claim transferred to the nested start
        for _ in range(2):
            with pytest.raises(RuntimeError, match="fixture start raised"):
                await attempt()

    await owner()
    assert registry.get("h1", SID)["start_claim_token"] is None


async def test_failover_unsent_recovery_requires_abandoned_claim(fleet_factory, mock, monkeypatch):
    fleet = fleet_factory(writes=True, orchestrate=True, safety={"write_min_interval_s": 0}, **MANAGED)
    lead = _add_wt_claude(mock)
    client = fleet.client("h1")
    invoke = client.invoke

    async def refused(channel, params=None, **kwargs):
        if channel == "claude:start-session":
            raise confinement.ConfinementRefused("HOST_ACCOUNT_UNVERIFIED", "fixture", sent=False)
        return await invoke(channel, params, **kwargs)

    client.invoke = refused
    ctx = mp.get_context("spawn")
    ready, release = ctx.Event(), ctx.Event()
    child = None
    failures = []
    fail = registry.fail_reservation

    def observed_fail(*args, **kwargs):
        failures.append(args)
        return fail(*args, **kwargs)

    try:
        with pytest.raises(confinement.ConfinementRefused, match="HOST_ACCOUNT_UNVERIFIED"):
            await lifecycle.session_failover(fleet, "h1", lead, confirm=True, successor_session_id=SID)
        entry = registry.get("h1", SID)
        child = ctx.Process(target=_crash_reservation, args=(entry, ready, release, lead),
                            kwargs={"authority": controller_authority.require()})
        child.start()
        assert await asyncio.to_thread(ready.wait, 10)
        live = registry.get("h1", SID)
        client.invoke = invoke
        monkeypatch.setattr(registry, "fail_reservation", observed_fail)
        baseline = len(mock.invokes)
        with pytest.raises(confinement.ConfinementRefused, match="START_IN_PROGRESS"):
            await lifecycle.session_failover(fleet, "h1", lead, confirm=True, successor_session_id=SID)
        assert not failures and registry.get("h1", SID) == live
        assert not any(i["channel"].startswith("worktree:") for i in mock.invokes[baseline:])
        release.set()
        await _join(child)
        result = await lifecycle.session_failover(fleet, "h1", lead, confirm=True, successor_session_id=SID)
        assert result["new_session_id"] == SID and result["prompt_sent"]
        assert len(failures) == 1
        assert mock.channels().count("claude:start-session") == mock.channels().count("claude:send-message") == 1
    finally:
        release.set()
        if child:
            if child.is_alive():
                child.terminate()
            await asyncio.to_thread(child.join, 10)
        await fleet.close()


@pytest.mark.parametrize("failure", ["refusal", "cancellation"])
async def test_owner_unsent_failure_releases_claim_for_same_id_retry(fleet_factory, mock, failure):
    fleet = fleet_factory(writes=True, orchestrate=True, safety={"write_min_interval_s": 0}, **MANAGED)
    client = fleet.client("h1")
    invoke = client.invoke

    async def interrupted(channel, params=None, **kwargs):
        if channel == "claude:start-session":
            if failure == "cancellation":
                raise asyncio.CancelledError("fixture")
            raise confinement.ConfinementRefused("HOST_ACCOUNT_UNVERIFIED", "fixture", sent=False)
        return await invoke(channel, params, **kwargs)

    client.invoke = interrupted
    try:
        exception = asyncio.CancelledError if failure == "cancellation" else confinement.ConfinementRefused
        with pytest.raises(exception):
            await orchestrate.session_start(fleet, "h1", "demo-project", confirm=True, session_id=SID,
                                            retain_on_error=True)
        row = registry.get("h1", SID)
        assert row["status"] == "failed" and row["start_sent"] is False
        assert row["start_claim_token"] is None and "claude:start-session" not in mock.channels()
        client.invoke = invoke
        assert (await orchestrate.session_start(fleet, "h1", "demo-project", confirm=True, session_id=SID))["started"]
        assert mock.channels().count("claude:start-session") == 1
    finally:
        await fleet.close()
