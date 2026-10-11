"""A07: task identity metadata cannot authorize a task-owned failover."""

import asyncio
import json

import pytest

from bat_agent_connector import cli, lifecycle, registry, task_control
from bat_agent_connector.errors import TaskControlRefused
from bat_agent_connector.mcp_server import build_server
from bat_agent_connector.task_daemon import TaskDaemon
from tests import test_api_v1 as api
from tests.conftest import controller_scope, make_config
from tests.test_lifecycle import _add_wt_claude

daemon, served = api.daemon, api.served


@pytest.fixture
async def failover_owned(mock, tmp_path):
    daemon = TaskDaemon(make_config(mock, writes=True, orchestrate=True, managed_roots=["/srv/demo"],
                                    safety={"write_min_interval_s": 0}), tmp_path / "tasks.db")
    with controller_scope(daemon.journal, api.api_auth.Principal("operator-dashboard", frozenset(api.api_auth.SCOPES))):
        sid = _add_wt_claude(mock)
        task = daemon.journal.submit(project="p", host="h1", workspace="demo-project", original_words="do it",
                                     engine="goose", lead_agent="claude", idempotency_key="failover-fixture")
        tid = task["task_id"]
        daemon.journal.change(tid, "dispatching")
        daemon.journal.change(tid, "accepted", fields={"session_id": sid})
        daemon.journal.add_branch(tid, session_id=sid, provider="claude", role="lead", reason="test")
        daemon.journal.change(tid, "running")
        task = daemon.journal.change(tid, "quota_limited")
        registry.update("h1", sid, task_id=tid, role="lead")
        try:
            yield daemon, task, sid
        finally:
            await daemon.fleet.close()
            await daemon.inventory.close()
            daemon.journal.close()


def reserved_authority(daemon, task, sid):
    _, handoff = daemon.journal.reserve_failover(task["task_id"], sid, "reserved-successor")
    callbacks = dict.fromkeys(task_control.FAILOVER_CALLBACKS, lambda *args: None)
    return daemon.coordinator._failover_authority(
        task, "reserved-successor", handoff["command_id"], handoff["message_id"], **callbacks)


@pytest.mark.parametrize("forgery", ["bare_task_id", "wrong_type", "unissued", "wrong_task",
                                    *task_control.FAILOVER_CALLBACKS])
async def test_a07_task_owned_failover_requires_coordinator_authority(failover_owned, mock, forgery):
    daemon, task, sid = failover_owned
    authority = None
    if forgery == "wrong_type":
        authority = {"task_id": task["task_id"], "successor_session_id": "forged-successor"}
    elif forgery not in {"bare_task_id", "wrong_type"}:
        authority = reserved_authority(daemon, task, sid)
        if forgery == "unissued":
            copy = object.__new__(task_control.TaskFailoverAuthority)
            for name, value in vars(authority).items():
                object.__setattr__(copy, name, value)
            authority = copy
        else:
            object.__setattr__(authority, "task_id" if forgery == "wrong_task" else forgery,
                               "another-task" if forgery == "wrong_task" else None)
    prior = daemon.journal.get(task["task_id"])
    commands = daemon.journal.commands(task["task_id"])
    changes = daemon.journal.db.total_changes
    entries = registry.registry_path().read_bytes()
    with pytest.raises(TaskControlRefused) as caught:
        await lifecycle.session_failover(daemon.fleet, "h1", sid, confirm=True, force=True,
                                         task_id=task["task_id"], task_authority=authority)
    assert caught.value.code == "TASK_OWNED_CONTROL_REQUIRED"
    assert daemon.journal.get(task["task_id"]) == prior
    assert daemon.journal.commands(task["task_id"]) == commands
    assert daemon.journal.db.total_changes == changes
    assert registry.registry_path().read_bytes() == entries
    assert not any(r["channel"] in {"claude:start-session", "claude:send-message", "worktree:create"}
                   for r in mock.invokes)


def test_a07_failover_authority_cannot_be_constructed_from_parameters():
    with pytest.raises(TypeError, match="only the TaskCoordinator"):
        task_control.TaskFailoverAuthority(task_id="public-task-id", successor_session_id="public-session-id")


@pytest.mark.parametrize("control", ["pause", "version", "owner_lost"])
async def test_a07_failover_authority_rechecks_control_after_writer_lock(
        failover_owned, mock, monkeypatch, control):
    daemon, task, sid = failover_owned
    authority = reserved_authority(daemon, task, sid)
    entered = asyncio.Event()
    original = lifecycle.resource_policy.authorize_shared_session

    async def checked(*args):
        grant = await original(*args)
        entered.set()
        return grant

    monkeypatch.setattr(lifecycle.resource_policy, "authorize_shared_session", checked)
    lock = lifecycle._write_lock("h1")
    async with lock:
        call = asyncio.create_task(lifecycle.session_failover(
            daemon.fleet, "h1", sid, confirm=True, task_authority=authority))
        try:
            await asyncio.wait_for(entered.wait(), 5)
            if control == "pause":
                daemon.journal.pause(task["task_id"])
            elif control == "version":
                daemon.journal.resume(task["task_id"])
            else:
                monkeypatch.setattr(daemon.journal, "owner_valid", lambda: False)
            prior = daemon.journal.get(task["task_id"])
            commands = daemon.journal.commands(task["task_id"])
            entries = registry.registry_path().read_bytes()
        except BaseException:
            call.cancel()
            raise
    with pytest.raises(TaskControlRefused) as caught:
        await asyncio.wait_for(call, 5)
    assert caught.value.code == ("TASK_OWNER_UNAVAILABLE" if control == "owner_lost" else "CONTROL_VERSION_CONFLICT")
    assert daemon.journal.get(task["task_id"]) == prior
    assert daemon.journal.commands(task["task_id"]) == commands
    assert registry.registry_path().read_bytes() == entries
    assert not any(r["channel"] in {"claude:start-session", "claude:send-message"} for r in mock.invokes)


async def test_a07_coordinator_failover_uses_reserved_authority_end_to_end(failover_owned, mock):
    daemon, task, sid = failover_owned
    result = await daemon.coordinator._failover(task)
    commands = daemon.journal.commands(task["task_id"])
    failover = next(c for c in commands if c["kind"] == "failover")
    handoff = daemon.journal.command_get(json.loads(failover["payload"])["handoff_command_id"])
    assert result["session_id"] == failover["session_id"] == handoff["session_id"] != sid
    assert failover["status"] == "settled" and handoff["status"] == "uncertain"
    entry = registry.get("h1", result["session_id"])
    assert entry["task_id"] == task["task_id"]
    assert entry["handoff_command_id"] == handoff["command_id"]
    assert entry["handoff_message_id"] == handoff["message_id"]
    assert entry["handoff_frame_sha256"] == json.loads(handoff["payload"])["prompt_sha256"]
    frames = [r for r in mock.invokes if r["channel"] in {"claude:start-session", "claude:send-message"}]
    assert [r["channel"] for r in frames] == ["claude:start-session", "claude:send-message"]
    assert all(r["params"]["sessionId"] == result["session_id"] for r in frames)
    assert frames[-1]["params"]["clientMessageId"] == handoff["message_id"]
    assert task["original_words"] in frames[-1]["params"]["prompt"]
    assert len(commands) == 2


@pytest.mark.parametrize("door", ["mcp", "cli"])
async def test_a07_standalone_failover_arguments_use_the_central_owner(served, mock, monkeypatch, door):
    sid = _add_wt_claude(mock)
    d, port = served
    config = d.fleet.config
    mock.handlers['git:log'] = lambda p: [{'hash': 'a' * 40}]
    monkeypatch.setenv('BATC_TASK_URL', f'http://127.0.0.1:{port}/rpc')
    monkeypatch.setenv('BATC_API_TOKEN', api.token(d, 'operator-dashboard', 'start', 'operate'))
    if door == "mcp":
        server, fleet = build_server(config)
        try:
            result = await server.call_tool("session_failover", {"host": "h1", "session_id": sid, "confirm": True})
            text = json.dumps(result.model_dump() if hasattr(result, "model_dump") else result, default=str)
            assert "prompt_sent" in text and "new_session_id" in text
            assert "TASK_OWNED_CONTROL_REQUIRED" not in text
        finally:
            await fleet.close()
    else:
        monkeypatch.setattr(cli, "load_config", lambda path: config)
        args = cli.build_parser().parse_args(["failover", "h1", sid, "--confirm"])
        result, _ = await cli._run(args)
        assert result["prompt_sent"] is True and result["old_session_id"] == sid
    successors = [e for e in registry.list_entries("h1") if e.get("failover_of") == sid]
    assert len(successors) == 1 and not successors[0].get("task_id")
    assert successors[0]["handoff_status"] == "sent"
    assert sum(r["channel"] == "claude:start-session" for r in mock.invokes) == 1
    assert sum(r["channel"] == "claude:send-message" for r in mock.invokes) == 1


@pytest.mark.parametrize("refusal", [False, True])
async def test_failover_confinement_await_preserves_changed_task_authority(
        failover_owned, mock, monkeypatch, refusal):
    from bat_agent_connector import confinement

    daemon, task, sid = failover_owned
    authority = reserved_authority(daemon, task, sid)
    original = confinement.guard_start_frame

    async def pause_during_check(*args, **kwargs):
        await original(*args, **kwargs)
        daemon.journal.pause(task["task_id"])
        if refusal:
            raise confinement.ConfinementRefused("HOST_ACCOUNT_UNVERIFIED", "fixture refusal", sent=False)

    monkeypatch.setattr(confinement, "guard_start_frame", pause_during_check)
    with pytest.raises(TaskControlRefused):
        await lifecycle.session_failover(daemon.fleet, "h1", sid, confirm=True, task_authority=authority)
    assert daemon.journal.get(task["task_id"])["paused"]
    assert not any(r["channel"] in {"claude:start-session", "claude:send-message"} for r in mock.invokes)
    successor = registry.get("h1", "reserved-successor")
    assert successor["start_sent"] is False and successor["status"] == "failed"
    assert successor["error_code"] == "CONTROL_VERSION_CONFLICT"
