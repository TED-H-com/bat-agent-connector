"""A05/A07/A09: operation admission and the single Task Service authority."""

import asyncio
import hashlib
import json
from contextlib import asynccontextmanager
from dataclasses import replace

import pytest

from bat_agent_connector import registry, service, task_control, verification
from bat_agent_connector.errors import OwnerConflict, ResourceReadOnly
from bat_agent_connector.task_daemon import TaskDaemon
from tests.conftest import controller_scope, make_config
from tests.operation_helpers import settle_operations


def test_a09_same_fleet_different_journals_refuses_second_owner(mock, tmp_path):
    config = make_config(mock, writes=True, orchestrate=True)
    first = TaskDaemon(config, tmp_path / "first" / "tasks.db")
    second = TaskDaemon(config, tmp_path / "second" / "tasks.db")
    first.acquire_owner()
    pointer = registry.registry_path().parent / service.TASK_SERVICE_POINTER
    before = pointer.read_bytes()
    try:
        with pytest.raises(OwnerConflict) as caught:
            second.acquire_owner()
        assert caught.value.code == "OWNER_CONFLICT"
        assert caught.value.owner == json.loads(before)
        assert pointer.read_bytes() == before
        assert not second._db_path.parent.exists()  # no journal/token/provider initialization
        first.journal.db.execute("UPDATE daemon_owner SET heartbeat_at=0")
        with pytest.raises(OwnerConflict):
            second.acquire_owner()  # a stale heartbeat cannot steal a live flock
    finally:
        first.journal.close()
    second.acquire_owner()
    assert json.loads(pointer.read_text())["db_path"] == str(second._db_path.resolve())
    assert json.loads(pointer.read_text())["owner_id"] != json.loads(before)["owner_id"]
    second.journal.close()


def test_a09_restart_releases_lease_and_reuses_journal(mock, tmp_path):
    config = make_config(mock, writes=True, orchestrate=True)
    path = tmp_path / "tasks.db"
    first = TaskDaemon(config, path)
    task = first.journal.submit(project="p", host="h1", workspace="w", original_words="do it",
                                idempotency_key="original")
    first.journal.close()
    second = TaskDaemon(config, path)
    try:
        assert second.journal.get(task["task_id"])["original_words"] == "do it"
        assert second.journal.db.execute("SELECT owner_id FROM daemon_owner").fetchone()[0] == second._owner_id
    finally:
        second.journal.close()


def test_a09_owner_lock_precedes_initialization_and_pointer(mock, tmp_path, monkeypatch):
    config = make_config(mock, writes=True, orchestrate=True)
    first = TaskDaemon(config, tmp_path / "first" / "tasks.db")
    second = TaskDaemon(config, tmp_path / "second" / "tasks.db")
    original = first._initialize

    def initializing():
        assert not (registry.registry_path().parent / service.TASK_SERVICE_POINTER).exists()
        assert not first._db_path.exists()
        with pytest.raises(OwnerConflict) as caught:
            second.acquire_owner()
        assert caught.value.code == "OWNER_CONFLICT"
        assert caught.value.owner["lease_path"] == str(registry.registry_path().parent / "task-daemon.lock")
        assert not second._db_path.parent.exists()
        original()
    monkeypatch.setattr(first, "_initialize", initializing)
    first.acquire_owner()
    first.journal.close()

from bat_agent_connector import api_auth, lifecycle  # noqa: E402
from bat_agent_connector.channels import GUARDED_CHANNELS, ORCHESTRATE_CHANNELS, WRITE_CHANNELS  # noqa: E402
from bat_agent_connector.errors import (  # noqa: E402
    ConnectionLost,
    InvokeError,
    InvokeTimeout,
    TaskControlRefused,
    WriteRefused,
)
from bat_agent_connector.operations import OpContext, OperationError  # noqa: E402
from tests.conftest import adopt  # noqa: E402

SID = "sess-claude-0001"
OWNER = api_auth.Principal("operator-dashboard", frozenset(api_auth.SCOPES))


def capability_caller(d, tid):
    # A real, task-scoped capability has a stable identity across retries.
    tokens = d.__dict__.setdefault("_fixture_capabilities", {})
    if tid not in tokens:
        tokens[tid] = d.journal.issue_capability(tid)
    return d.capability_principal(tokens[tid], "session.send", {"task_id": tid})


async def call_owned(d, method, params, **kwargs):
    caller = capability_caller(d, params["task_id"]) if method in {
        "task_send", "task_run_verification", "task_request_ted"} else OWNER
    kwargs.setdefault("principal", caller)
    return await d.call(method, params, **kwargs)



@pytest.fixture
async def owned(mock, tmp_path):
    daemon = TaskDaemon(make_config(mock, writes=True, orchestrate=True, managed_roots=["/srv"],
                                    default_permission_mode="allow_all", safety={"write_min_interval_s": 0}),
                        tmp_path / "tasks.db")
    with controller_scope(daemon.journal, OWNER):
        task = daemon.journal.submit(project="p", host="h1", workspace="demo-project", original_words="do it",
                                     engine="goose", idempotency_key="fixture")
        daemon.journal.db.execute("UPDATE tasks SET state='accepted',session_id=? WHERE task_id=?", (SID, task["task_id"]))
        adopt(SID, task_id=task["task_id"], role="lead", agent_preset="claude-code")
        yield daemon, task["task_id"]
    await daemon.ops.drain()
    await daemon.fleet.close()
    await daemon.inventory.close()
    daemon.journal.close()


def writes(mock):
    return [r for r in mock.invokes if r["channel"] in WRITE_CHANNELS | ORCHESTRATE_CHANNELS | GUARDED_CHANNELS]


@pytest.mark.parametrize("action", ["send", "answer", "interrupt", "permissions"])
@pytest.mark.parametrize("error", [ConnectionLost, InvokeTimeout])
@pytest.mark.parametrize("after_frame", [False, True], ids=["before_frame", "after_frame"])
async def test_a07_failure_before_any_frame_rejects_command_without_uncertain_task(owned, mock, monkeypatch,
                                                                                 action, error, after_frame):
    d, tid = owned
    prior = d.journal.get(tid)
    mock.states[SID]["pendingAskUser"] = {"toolUseId": "ask-1", "questions": [{"question": "Choice?"}]}
    handlers = {"send": (service.session_send, {"text": "one instruction"}),
                "answer": (service.session_answer, {"answers": ["yes"], "tool_use_id": "ask-1"}),
                "interrupt": (service.session_interrupt, {}),
                "permissions": (lifecycle.session_set_permissions, {"mode": "allow_all"})}
    fn, params = handlers[action]
    original_lookup = service._resolve_session
    client = d.fleet.client("h1")
    original_roundtrip = client._roundtrip

    async def fail_lookup(*args, **kwargs):
        # The outer admission lookup succeeds; fail the lower-layer lookup after command intent.
        if d.journal.commands(tid):
            raise error("session lookup failed")
        return await original_lookup(*args, **kwargs)

    async def lose_reply(frame, timeout, **kwargs):
        result = await original_roundtrip(frame, timeout, **kwargs)
        if frame["channel"] in WRITE_CHANNELS | ORCHESTRATE_CHANNELS | GUARDED_CHANNELS:
            raise error("write reply lost")
        return result

    with monkeypatch.context() as patch:
        if after_frame:
            patch.setattr(client, "_roundtrip", lose_reply)
        else:
            patch.setattr(service, "_resolve_session", fail_lookup)
            patch.setattr(lifecycle, "_resolve_session", fail_lookup)
        with pytest.raises(error):
            await fn(d.fleet, "h1", SID, confirm=True, **params)
    command = d.journal.commands(tid)[-1]
    assert command["status"] == ("uncertain" if after_frame else "rejected")
    task = d.journal.get(tid)
    assert task["state"] == ("uncertain" if after_frame else prior["state"])
    assert task["control_version"] == prior["control_version"]
    assert len(writes(mock)) == int(after_frame)
    if after_frame:
        with pytest.raises(TaskControlRefused, match="TASK_COMMAND_PENDING"):
            await service.session_interrupt(d.fleet, "h1", SID, confirm=True)
        assert len(d.journal.commands(tid)) == 1
    else:
        await fn(d.fleet, "h1", SID, confirm=True, **params)
        assert len(d.journal.commands(tid)) == 2
        assert len(writes(mock)) == 1


@pytest.mark.parametrize("action", ["send", "answer", "interrupt", "permissions"])
@pytest.mark.parametrize("condition,code", [("verifying", "TASK_VERIFYING"), ("paused", "TASK_PAUSED"),
                                            ("pending", "TASK_COMMAND_PENDING"), ("uncertain", "TASK_RECONCILIATION_REQUIRED"),
                                            ("done", "TASK_STATE_BLOCKED")])
async def test_a07_task_owned_actions_respect_coordinator_gate(owned, mock, action, condition, code):
    d, tid = owned
    if condition == "paused":
        d.journal.pause(tid)
    elif condition == "pending":
        d.journal.command(tid, "send", SID, {}, "pending")
    else:
        d.journal.db.execute("UPDATE tasks SET state=? WHERE task_id=?", (condition, tid))
    handlers = {"send": (service.session_send, {"text": "outside"}),
                "answer": (service.session_answer, {"permission": "allow"}),
                "interrupt": (service.session_interrupt, {"mode": "hard"}),
                "permissions": (lifecycle.session_set_permissions, {"mode": "allow_all", "force": True})}
    fn, params = handlers[action]
    with pytest.raises(TaskControlRefused) as caught:
        await fn(d.fleet, "h1", SID, confirm=True, **params)
    assert caught.value.code == code
    assert not writes(mock)
    assert not d.journal.commands(tid) if condition != "pending" else len(d.journal.commands(tid)) == 1
    if action != "permissions":
        p = api_auth.Principal("operator-dashboard", frozenset({"operate"}))
        http_params = {"text": "outside"} if action == "send" else {"mode": "hard"} if action == "interrupt" else {
            "permission": "allow", "tool_use_id": "permission-1"}
        with pytest.raises(OperationError) as refused:
            d.ops.create(p, action="session." + action, target={"host": "h1", "session_id": SID},
                         params=http_params, idempotency_key="gate")
        assert refused.value.code == code
        assert d.journal.db.execute("SELECT count(*) FROM operations").fetchone()[0] == 0


@pytest.mark.parametrize("change,code", [("pause", "TASK_PAUSED"), ("version", "CONTROL_VERSION_CONFLICT"),
                                        ("binding", "TASK_BINDING_MISMATCH"), ("verifying", "TASK_VERIFYING")])
async def test_a07_late_state_version_and_owner_change_blocks_frame(owned, mock, monkeypatch, change, code):
    d, tid = owned
    client = d.fleet.client("h1")
    original = client._invoke_checked

    async def delayed(channel, *args, **kwargs):
        if channel == "claude:send-message":
            if change == "pause":
                # Keep the admitted version to exercise the state check independently.
                d.journal.db.execute("UPDATE tasks SET paused=1 WHERE task_id=?", (tid,))
            elif change == "version":
                d.journal.pause(tid)
                d.journal.resume(tid)
            elif change == "binding":
                registry.update("h1", SID, task_id="replacement-task")
            else:
                d.journal.db.execute("UPDATE tasks SET state='verifying' WHERE task_id=?", (tid,))
        return await original(channel, *args, **kwargs)
    monkeypatch.setattr(client, "_invoke_checked", delayed)
    with pytest.raises(TaskControlRefused) as refused:
        await service.session_send(d.fleet, "h1", SID, "outside", confirm=True, before_invoke=lambda: None)
    assert refused.value.code == code
    assert not writes(mock)
    assert d.journal.commands(tid)[0]["status"] == "rejected"


async def test_a07_client_resume_and_each_permission_channel_are_gated(owned, mock, monkeypatch):
    d, tid = owned
    client = d.fleet.client("h1")
    original = client._invoke_checked

    async def late_pause(channel, *args, **kwargs):
        if channel in {"claude:client-resume", "claude:set-codex-approval-policy"}:
            d.journal.pause(tid)
        return await original(channel, *args, **kwargs)
    monkeypatch.setattr(client, "_invoke_checked", late_pause)
    mock.metas[SID] = None
    with pytest.raises(TaskControlRefused, match="CONTROL_VERSION_CONFLICT"):
        await service.session_send(d.fleet, "h1", SID, "outside", confirm=True)
    assert not writes(mock)
    d.journal.resume(tid)
    codex = "sess-codex-0002"
    adopt(codex, task_id=tid, role="lead", agent_preset="codex-agent")
    d.journal.db.execute("UPDATE tasks SET session_id=? WHERE task_id=?", (codex, tid))
    with pytest.raises(TaskControlRefused, match="CONTROL_VERSION_CONFLICT"):
        await lifecycle.session_set_permissions(d.fleet, "h1", codex, "allow_all", confirm=True)
    assert [r["channel"] for r in writes(mock)] == ["claude:set-codex-sandbox-mode"]


async def test_a05_task_actions_share_actor_keys_and_original_effects(owned):
    d, tid = owned
    payload = {"task_id": tid, "actor": "ted", "source_message_id": "message-1", "idempotency_key": "pause"}
    first = await call_owned(d, "work_pause", payload)
    second = await call_owned(d, "work_pause", payload)
    assert first == second and first["operation_status"] == "succeeded"
    assert d.journal.get(tid)["control_version"] == 1
    assert d.journal.get(tid)["ted_interventions"] == 1
    with pytest.raises(OperationError, match="IDEMPOTENCY_CONFLICT"):
        await call_owned(d, "work_pause", {**payload, "abort_current": True})
    p = api_auth.Principal("second-client", frozenset({"operate"}))
    with pytest.raises(ResourceReadOnly, match="FOREIGN_CONTROLLER"):
        await call_owned(d, "work_pause", payload, principal=p)
    resume = await call_owned(d, "work_resume", {"task_id": tid, "idempotency_key": "resume"})
    again = await call_owned(d, "work_resume", {"task_id": tid, "idempotency_key": "resume"})
    assert resume == again and d.journal.get(tid)["control_version"] == 2


async def test_a05_task_effect_and_receipt_commit_together(owned, monkeypatch):
    d, tid = owned
    p = api_auth.Principal("operator-dashboard", frozenset({"operate"}))
    op, _ = d.ops.create(p, action="task.pause", target={"task_id": tid}, idempotency_key="crash")
    ctx = OpContext(d.ops, d.ops._row(op["operation_id"]))
    original = d.ops._step_done

    def crash(*args, **kwargs):
        raise RuntimeError("crash before receipt commit")
    monkeypatch.setattr(d.ops, "_step_done", crash)
    with pytest.raises(RuntimeError):
        ctx.effect("task_pause", lambda: d.journal.pause(tid))
    assert d.journal.get(tid)["control_version"] == 0
    assert not d.journal.get(tid)["paused"]
    monkeypatch.setattr(d.ops, "_step_done", original)
    result = ctx.effect("task_pause", lambda: d.journal.pause(tid))
    assert result["control_version"] == 1
    assert ctx.effect("task_pause", lambda: d.journal.pause(tid)) == result
    assert d.journal.get(tid)["control_version"] == 1
    await settle_operations(d.ops)
    assert d.ops.get(op["operation_id"])["status"] == "succeeded"
    assert d.journal.get(tid)["control_version"] == 1


async def test_a07_goose_run_does_not_block_its_own_task_send(owned, mock):
    d, tid = owned
    d.journal.command(tid, "goose_run", SID, {}, "goose")
    first = await call_owned(d, "task_send", {"task_id": tid, "text": "one instruction", "step_id": "step-1"})
    second = await call_owned(d, "task_send", {"task_id": tid, "text": "one instruction", "step_id": "step-1"})
    assert first == second and first["operation_status"] == "succeeded"
    assert len([r for r in writes(mock) if r["channel"] == "claude:send-message"]) == 1
    assert len([c for c in d.journal.commands(tid) if c["kind"] == "send"]) == 1
    with pytest.raises(OperationError, match="IDEMPOTENCY_CONFLICT"):
        await call_owned(d, "task_send", {"task_id": tid, "text": "different", "step_id": "step-1"})


def task_send_operation(d, tid, key):
    return d.ops.create(capability_caller(d, tid),
                        action="session.send", target={"task_id": tid},
                        params={"text": "one instruction", "step_id": "pause-race"}, idempotency_key=key)


async def assert_paused_send_refusal(d, tid, mock, op, paused):
    refused = d.ops.get(op["operation_id"])
    assert refused["status"] == "failed" and refused["error_code"] == "TASK_PAUSED"
    step = next(s for s in refused["steps"] if s["name"] == "task_send_refusal")
    assert step["status"] == "failed" and step["error"]["code"] == "TASK_PAUSED"
    assert not d.journal.commands(tid) and not writes(mock)
    assert d.journal.get(tid) == paused
    assert await d.coordinator.tick(tid) == paused  # the coordinator still does nothing while paused
    replay, created = task_send_operation(d, tid, op["idem_key"])
    assert not created and d.ops.get(replay["operation_id"]) == refused
    await settle_operations(d.ops)
    assert not d.journal.commands(tid) and not writes(mock)
    d.journal.resume(tid)
    # Resume cannot change this key's definitive refusal into a successful send.
    replay, created = task_send_operation(d, tid, op["idem_key"])
    assert not created and d.ops.get(replay["operation_id"]) == refused
    fresh, created = task_send_operation(d, tid, op["idem_key"] + ":resumed")
    assert created
    await settle_operations(d.ops)
    assert d.ops.get(fresh["operation_id"])["status"] == "succeeded"
    assert len(d.journal.commands(tid)) == 1 and d.journal.commands(tid)[0]["status"] == "accepted"
    assert len(writes(mock)) == 1 and writes(mock)[0]["channel"] == "claude:send-message"


async def test_a07_pause_while_send_waits_for_session_lock_refuses_operation(owned, mock, monkeypatch):
    d, tid = owned
    entered = asyncio.Event()
    original = d.coordinator._send

    async def sending(*args, **kwargs):
        entered.set()
        return await original(*args, **kwargs)

    monkeypatch.setattr(d.coordinator, "_send", sending)
    async with d.coordinator._lock("h1", SID):
        op, _ = task_send_operation(d, tid, "pause-at-session-lock")
        worker = asyncio.create_task(settle_operations(d.ops))
        await asyncio.wait_for(entered.wait(), 5)
        paused = await d.coordinator.pause(tid)
    await worker
    await assert_paused_send_refusal(d, tid, mock, op, paused)


@pytest.mark.parametrize("boundary", ["route", "presence", "prepare_send"])
async def test_a07_pause_during_send_preparation_refuses_operation(owned, mock, monkeypatch, boundary):
    d, tid = owned
    entered, release = asyncio.Event(), asyncio.Event()
    owner, method = (d.coordinator, "_route") if boundary == "route" else (
        d.adapter, "session_presence" if boundary == "presence" else "prepare_send")
    original = getattr(owner, method)

    async def preparing(*args, **kwargs):
        entered.set()
        await release.wait()
        return await original(*args, **kwargs)

    monkeypatch.setattr(owner, method, preparing)
    if boundary == "presence":
        # The public scoped send uses goose:*; exercise the original initial-send probe too.
        original_send = d.coordinator._send

        async def initial_send(task, sid, text, purpose, **kwargs):
            return await original_send(task, sid, text, "lead:initial", **kwargs)

        monkeypatch.setattr(d.coordinator, "_send", initial_send)
    op, _ = task_send_operation(d, tid, "pause-at-" + boundary)
    worker = asyncio.create_task(settle_operations(d.ops))
    try:
        await asyncio.wait_for(entered.wait(), 5)
        paused = await d.coordinator.pause(tid)
    finally:
        release.set()
        await worker
    if boundary == "presence":
        monkeypatch.setattr(d.coordinator, "_send", original_send)
    await assert_paused_send_refusal(d, tid, mock, op, paused)


@pytest.mark.parametrize("status,code", [(None, "TASK_SEND_NOT_DISPATCHED"), ("rejected", "NOT_ACCEPTED"),
                                       ("cancelled", "TASK_SEND_NOT_DISPATCHED"), ("uncertain", "UNCERTAIN")])
async def test_a07_task_send_requires_its_accepted_command_receipt(owned, mock, monkeypatch, status, code):
    d, tid = owned

    async def early_return(task, sid, text, purpose, *, operation):
        if status:
            def command_effect():
                command, _ = d.journal.command(tid, "send", sid, {"operation_id": operation.operation_id},
                                                "op:" + operation.operation_id)
                d.journal.command_status(command["command_id"], status)
                return command
            operation.effect("task_send_command", command_effect)
        return task

    monkeypatch.setattr(d.coordinator, "_send", early_return)
    op, _ = task_send_operation(d, tid, "early-return")
    await settle_operations(d.ops)
    result = d.ops.get(op["operation_id"])
    assert result["status"] == ("uncertain" if status == "uncertain" else "failed")
    assert result["error_code"] == code and not writes(mock)


@pytest.mark.parametrize("receipt_status", ["started", "failed"])
async def test_a05_paused_send_refusal_survives_restart_before_operation_settlement(owned, mock, monkeypatch, receipt_status):
    d, tid = owned
    original = d.coordinator._send

    async def paused_send(*args, **kwargs):
        await d.coordinator.pause(tid)
        return await original(*args, **kwargs)

    op, _ = task_send_operation(d, tid, "refusal-restart")
    with monkeypatch.context() as patch:
        patch.setattr(d.coordinator, "_send", paused_send)
        await settle_operations(d.ops)
    d.journal.resume(tid)
    # Crash after saving the refusal intent/receipt, before recording the operation's terminal status.
    if receipt_status == "started":
        d.journal.db.execute("UPDATE operation_steps SET status='started',error=NULL,finished_at=NULL "
                             "WHERE operation_id=? AND name='task_send_refusal'", (op["operation_id"],))
    d.journal.db.execute("UPDATE operations SET status='running',error_code=NULL WHERE operation_id=?",
                         (op["operation_id"],))
    await settle_operations(d.ops)
    result = d.ops.get(op["operation_id"])
    assert result["status"] == "failed" and result["error_code"] == "TASK_PAUSED"
    assert result["steps"][-1]["status"] == "failed" and result["steps"][-1]["error"]["code"] == "TASK_PAUSED"
    assert not d.journal.commands(tid) and not writes(mock)


@asynccontextmanager
async def restarted_daemon(d):
    d.release_owner()  # the stopped worker no longer owns the fleet; the new daemon reads persisted rows
    restarted = TaskDaemon(d._config, d._db_path)
    restarted.__dict__["_fixture_capabilities"] = dict(d.__dict__.get("_fixture_capabilities", {}))
    restarted.acquire_owner()
    try:
        yield restarted
    finally:
        await restarted.fleet.close()
        await restarted.inventory.close()
        restarted.journal.close()


def stop_after_command_status(d, patch, status):
    original = d.journal.command_status

    def stop(command_id, new_status, **kwargs):
        original(command_id, new_status, **kwargs)
        if new_status == status:
            raise asyncio.CancelledError("crash after command status committed")

    patch.setattr(d.journal, "command_status", stop)


@pytest.mark.parametrize("error", [ConnectionLost, InvokeTimeout])
@pytest.mark.parametrize("command_status", ["intent", "needs_review", "uncertain"])
@pytest.mark.parametrize("tick_first", [False, True], ids=["operation_first", "coordinator_first"])
async def test_a05_a07_failed_dispatch_receipt_survives_crash_before_command_rejection(
        owned, mock, monkeypatch, error, command_status, tick_first):
    """A05/A07: the committed dispatch refusal proves no prompt, even before the command write."""
    d, tid = owned
    prior = d.journal.get(tid)
    mock.metas[SID] = None
    client = d.fleet.client("h1")
    original = client._roundtrip
    op, _ = task_send_operation(d, tid, "dispatch-refusal-crash")
    saved_error = {}

    async def lose_resume_reply(frame, timeout, **kwargs):
        result = await original(frame, timeout, **kwargs)
        if frame["channel"] == "claude:client-resume":
            raise error("resume reply lost before prompt")
        return result

    def crash_before_rejected(command_id, status, **kwargs):
        assert status == "rejected"
        step = next(s for s in d.ops.get(op["operation_id"])["steps"] if s["name"] == "task_dispatch")
        assert step["status"] == "failed"
        saved_error.update(step["error"])
        raise asyncio.CancelledError("crash after dispatch receipt, before command status")

    with monkeypatch.context() as patch:
        patch.setattr(client, "_roundtrip", lose_resume_reply)
        patch.setattr(d.journal, "command_status", crash_before_rejected)
        with pytest.raises(asyncio.CancelledError):
            await d.ops._execute(op["operation_id"])
    command = d.journal.commands(tid)[0]
    assert command["status"] == "needs_review" and d.journal.get(tid) == prior
    d.journal.command_status(command["command_id"], command_status)
    readbacks = []

    async def unexpected_readback(*args, **kwargs):
        readbacks.append(args)
        raise AssertionError("failed receipt must not read BAT back")

    async with restarted_daemon(d) as restarted:
        monkeypatch.setattr(restarted.adapter, "reconcile_send", unexpected_readback)
        if tick_first:
            assert await restarted.coordinator.tick(tid) == prior
        await settle_operations(restarted.ops)
        result = restarted.ops.get(op["operation_id"])
        assert result["status"] == "failed" and result["error_code"] == saved_error["code"] == "BAT_ERROR"
        assert result["status_reason"] == saved_error["message"]
        commands = restarted.journal.commands(tid)
        assert len(commands) == 1 and commands[0]["command_id"] == command["command_id"]
        assert commands[0]["status"] == "rejected" and restarted.journal.get(tid) == prior
        assert not readbacks
        assert [r["channel"] for r in writes(mock)] == ["claude:client-resume"]
        assert_terminal_send_replay(restarted, tid, op, "BAT_ERROR", commands, 1, mock)


@pytest.mark.parametrize("accepted", [False, True])
async def test_a05_a07_succeeded_dispatch_receipt_survives_crash_before_task_result(
        owned, mock, monkeypatch, accepted):
    """A05/A07: replay the saved BAT reply through the live result rules, never dispatch/read again."""
    d, tid = owned
    mock.echo_sends = True
    if not accepted:
        mock.handlers["claude:send-message"] = lambda params: {"accepted": False}
    op, _ = task_send_operation(d, tid, "dispatch-success-crash")

    async def crash(*args, **kwargs):
        step = d.ops.db.execute("SELECT status,response FROM operation_steps WHERE operation_id=? AND name='task_dispatch'",
                                (op["operation_id"],)).fetchone()
        assert step["status"] == "succeeded" and json.loads(step["response"])["accepted"] is accepted
        raise asyncio.CancelledError("crash before processing the saved dispatch reply")

    with monkeypatch.context() as patch:
        patch.setattr(d.coordinator, "_finish_send", crash)
        with pytest.raises(asyncio.CancelledError):
            await d.ops._execute(op["operation_id"])
    assert d.journal.commands(tid)[0]["status"] == "needs_review"
    assert d.journal.get(tid)["state"] == "accepted"
    readbacks = []

    async def no_readback(*args, **kwargs):
        readbacks.append(args)
        raise AssertionError("saved dispatch reply needs no read-back")

    async with restarted_daemon(d) as restarted:
        monkeypatch.setattr(restarted.adapter, "reconcile_send", no_readback)
        await settle_operations(restarted.ops)
        result = restarted.ops.get(op["operation_id"])
        assert result["status"] == ("succeeded" if accepted else "failed")
        if not accepted:
            assert result["error_code"] == "NOT_ACCEPTED"
        assert restarted.journal.get(tid)["state"] == ("running" if accepted else "needs_ted")
        assert restarted.journal.commands(tid)[0]["status"] == ("accepted" if accepted else "rejected")
        assert len(restarted.journal.commands(tid)) == 1 and not readbacks
        assert [r["channel"] for r in writes(mock)] == ["claude:send-message"]
        replay, created = task_send_operation(restarted, tid, "dispatch-success-crash")
        assert not created and restarted.ops.get(replay["operation_id"]) == result


@pytest.mark.parametrize("scoped", [False, True], ids=["session_operation", "task_operation"])
@pytest.mark.parametrize("failure", ["malformed_reply", "bookkeeping"])
async def test_a07_post_frame_send_error_is_uncertain_and_settles_by_readback(
        owned, mock, monkeypatch, scoped, failure):
    """A07/A08: an unexpected exception after the prompt is never a definitive failed receipt."""
    d, tid = owned
    mock.metas[SID] = None
    mock.echo_sends = True
    mock.states[SID]["messages"] = list(mock.archives[SID])
    client = d.fleet.client("h1")
    original = client._roundtrip
    principal, request, op = admission_control(d, tid, mock, "send", scoped=scoped)

    async def malformed_reply(frame, timeout, **kwargs):
        result = await original(frame, timeout, **kwargs)
        return {"unexpected": "reply"} if frame["channel"] == "claude:send-message" else result

    def bookkeeping_failure(*args, **kwargs):
        raise ValueError("could not record send reply")

    async def unproven(*args, **kwargs):
        return None

    with monkeypatch.context() as patch:
        if failure == "malformed_reply":
            patch.setattr(client, "_roundtrip", malformed_reply)
        else:
            patch.setattr(registry, "record_turn", bookkeeping_failure)
        patch.setattr(d.adapter, "reconcile_send", unproven)
        await settle_operations(d.ops)
    result = d.ops.get(op["operation_id"])
    step_name = "task_dispatch" if scoped else "send"
    assert result["status"] == "uncertain"
    assert next(s for s in result["steps"] if s["name"] == step_name)["status"] == "uncertain"
    command = d.journal.commands(tid)[0]
    assert command["status"] == d.journal.get(tid)["state"] == "uncertain"
    d.journal.db.execute("UPDATE operations SET next_run_at=0 WHERE operation_id=?", (op["operation_id"],))
    async with restarted_daemon(d) as restarted:
        await settle_operations(restarted.ops)
        result = restarted.ops.get(op["operation_id"])
        assert result["status"] == "succeeded"
        assert next(s for s in result["steps"] if s["name"] == step_name)["status"] == "succeeded"
        if not scoped:
            await restarted.coordinator.tick(tid)
        assert restarted.journal.command_get(command["command_id"])["status"] == "accepted"
        assert restarted.journal.get(tid)["state"] == "running"
        assert len(restarted.journal.commands(tid)) == 1
        assert [r["channel"] for r in writes(mock)] == ["claude:client-resume", "claude:send-message"]
        replay, created = restarted.ops.create(principal, **request)
        assert not created and restarted.ops.get(replay["operation_id"]) == result


@pytest.mark.parametrize("window", ["before_receipt_commit", "after_result_commit"])
async def test_a05_task_send_result_receipt_survives_crash_without_repeating_effect(
        owned, mock, monkeypatch, window):
    """A05: result and command acceptance commit together; both sides replay only saved evidence."""
    d, tid = owned
    mock.echo_sends = True
    op, _ = task_send_operation(d, tid, "result-receipt-crash")
    original_done = d.ops._step_done
    original_send = d.coordinator._send

    def crash_before_commit(operation_id, name, response, **kwargs):
        if name == "task_send_result":
            raise asyncio.CancelledError("crash inside task/result receipt transaction")
        return original_done(operation_id, name, response, **kwargs)

    async def crash_after_result(*args, **kwargs):
        await original_send(*args, **kwargs)
        raise asyncio.CancelledError("crash after task/result receipt commit")

    with monkeypatch.context() as patch:
        if window == "before_receipt_commit":
            patch.setattr(d.ops, "_step_done", crash_before_commit)
        else:
            patch.setattr(d.coordinator, "_send", crash_after_result)
        with pytest.raises(asyncio.CancelledError):
            await d.ops._execute(op["operation_id"])
    prior = d.journal.get(tid)
    command = d.journal.commands(tid)[0]
    committed = window == "after_result_commit"
    assert command["status"] == ("accepted" if committed else "needs_review")
    assert prior["state"] == ("running" if committed else "accepted")

    async def no_readback(*args, **kwargs):
        pytest.fail("result receipt recovery must not read BAT back")

    async with restarted_daemon(d) as restarted:
        monkeypatch.setattr(restarted.adapter, "reconcile_send", no_readback)
        await settle_operations(restarted.ops)
        assert restarted.ops.get(op["operation_id"])["status"] == "succeeded"
        if committed:
            assert restarted.journal.get(tid) == prior
        assert restarted.journal.commands(tid)[0]["status"] == "accepted"
        assert restarted.journal.get(tid)["state"] == "running"
        events = [e for e in restarted.journal.events(tid) if e["kind"] == "state"]
        assert sum(json.loads(e["body"]).get("to") == "running" for e in events) == 1
        assert len(restarted.journal.commands(tid)) == 1
        assert [r["channel"] for r in writes(mock)] == ["claude:send-message"]


@pytest.mark.parametrize("kind", ["send", "answer", "interrupt"])
async def test_a07_bat_refusal_records_failed_runtime_step_and_rejected_command(owned, mock, kind):
    """A07: a BAT error reply is an explicit refusal, not a lost command outcome."""
    d, tid = owned
    principal, request, op = admission_control(d, tid, mock, kind)
    prior = d.journal.get(tid)
    channel = {"send": "claude:send-message", "answer": "claude:resolve-ask-user", "interrupt": "claude:interrupt-turn"}[kind]

    def refuse(params):
        raise RuntimeError("BAT refused the control")

    mock.handlers[channel] = refuse
    await settle_operations(d.ops)
    result = d.ops.get(op["operation_id"])
    assert result["status"] == "failed" and result["error_code"] == "BAT_ERROR"
    assert next(s for s in result["steps"] if s["name"] == kind)["status"] == "failed"
    assert d.journal.commands(tid)[0]["status"] == "rejected" and d.journal.get(tid) == prior
    replay, created = d.ops.create(principal, **request)
    assert not created and d.ops.get(replay["operation_id"]) == result


def command_ref_operation(d, tid, mock, kind):
    if kind in {"send", "answer", "interrupt"}:
        return admission_control(d, tid, mock, kind)[2]
    if kind == "task_send":
        return task_send_operation(d, tid, "command-refs")[0]
    # An operator reconciliation reserves one new command beside the original uncertain command.
    command, _ = d.journal.command(tid, "send", SID, {
        "purpose": "goose:old", "before": {}, "prompt_sha256": "d" * 64, "control_version": 0}, "old-command")
    d.journal.command_status(command["command_id"], "uncertain")
    d.journal.change(tid, "uncertain")
    cap = d.journal.issue_reconcile_capability(tid, command["command_id"])
    target = {"task_id": tid, "command_id": command["command_id"]}
    principal = d.capability_principal(cap, "task.command.reconcile", target, "command-refs")
    return d.ops.create(principal, action="task.command.reconcile", target=target, params={
        "outcome": "not_delivered", "actor": "operator", "source": "ticket-1", "evidence": "checked exact turn",
        "next_prompt": "one instruction"}, idempotency_key="command-refs")[0]


def assert_command_refs(d, op_id, step):
    receipt = d.ops.db.execute("SELECT response FROM operation_steps WHERE operation_id=? AND name=? AND status='succeeded'",
                               (op_id, step)).fetchone()
    command = d.journal.command_get(json.loads(receipt["response"])["command_id"])
    refs = d.ops.get(op_id)["external_refs"]
    assert {key: refs[key] for key in ("task_id", "command_id", "control_version")} == task_control.command_refs(command)
    return command


@pytest.mark.parametrize("kind", ["send", "answer", "interrupt", "task_send", "prepared"])
@pytest.mark.parametrize("boundary", ["receipt", "frame"])
@pytest.mark.parametrize("old_row", [False, True], ids=["atomic_refs", "legacy_missing_refs"])
async def test_a05_a07_command_refs_commit_with_receipt_and_survive_restart(owned, mock, monkeypatch, kind, boundary, old_row):
    """A05/A07: linkage is durable before the first frame and survives lost replies/restarts."""
    d, tid = owned
    mock.echo_sends = True
    mock.states[SID]["messages"] = list(mock.archives[SID])
    op = command_ref_operation(d, tid, mock, kind)
    op_id = op["operation_id"]
    step = "task_command" if kind in {"send", "answer", "interrupt"} else "task_send_command"
    channel = {"answer": "claude:resolve-ask-user", "interrupt": "claude:interrupt-turn"}.get(kind, "claude:send-message")
    original_effect = OpContext.effect
    original_roundtrip = d.fleet.client("h1")._roundtrip

    def after_receipt(ctx, name, fn, **kwargs):
        result = original_effect(ctx, name, fn, **kwargs)
        if name == step:
            assert_command_refs(d, op_id, step)
            if boundary == "receipt":
                raise asyncio.CancelledError("crash immediately after command/receipt/refs commit")
        return result

    async def after_frame(frame, timeout, **kwargs):
        result = await original_roundtrip(frame, timeout, **kwargs)
        if frame["channel"] == channel:
            assert_command_refs(d, op_id, step)
            if kind == "interrupt":
                mock.metas[SID]["isStreaming"] = mock.states[SID]["isStreaming"] = False
            raise asyncio.CancelledError("crash after command frame, before recording the outcome")
        return result

    with monkeypatch.context() as patch:
        patch.setattr(OpContext, "effect", after_receipt)
        if boundary == "frame":
            patch.setattr(d.fleet.client("h1"), "_roundtrip", after_frame)
        with pytest.raises(asyncio.CancelledError):
            await d.ops._execute(op_id)
    command = assert_command_refs(d, op_id, step)
    ids = [c["command_id"] for c in d.journal.commands(tid)]
    assert len(ids) == (2 if kind == "prepared" else 1)
    assert d.ops.get(op_id)["external_refs"]["admission_binding"] == op["external_refs"]["admission_binding"]
    assert len(writes(mock)) == int(boundary == "frame")
    if old_row:
        d.ops.db.execute("UPDATE operations SET external_refs=? WHERE operation_id=?",
                         (json.dumps({"admission_binding": op["external_refs"]["admission_binding"]}), op_id))
    async with restarted_daemon(d) as restarted:
        await settle_operations(restarted.ops)
        result = restarted.ops.get(op_id)
        assert result["status"] == ("succeeded" if boundary == "frame" else "uncertain")
        await restarted.coordinator.tick(tid)
        status = "accepted" if kind in {"send", "task_send", "prepared"} else "settled"
        assert restarted.journal.command_get(command["command_id"])["status"] == (status if boundary == "frame" else "uncertain")
        assert_command_refs(restarted, op_id, step)
        assert [c["command_id"] for c in restarted.journal.commands(tid)] == ids
        assert len(writes(mock)) == int(boundary == "frame")


async def test_a05_reconcile_reservation_commits_new_command_refs_before_dispatch_receipt(owned, mock, monkeypatch):
    d, tid = owned
    mock.echo_sends = True
    op = command_ref_operation(d, tid, mock, "prepared")
    original_effect = OpContext.effect

    def after_reservation(ctx, name, fn, **kwargs):
        result = original_effect(ctx, name, fn, **kwargs)
        if name == "task_reconcile":
            command = d.journal.command_get(result["_next_command_id"])
            refs = d.ops.get(op["operation_id"])["external_refs"]
            assert {k: refs[k] for k in ("task_id", "command_id", "control_version")} == task_control.command_refs(command)
            raise asyncio.CancelledError("crash after reserving the operator command, before its dispatch receipt")
        return result

    with monkeypatch.context() as patch:
        patch.setattr(OpContext, "effect", after_reservation)
        with pytest.raises(asyncio.CancelledError):
            await d.ops._execute(op["operation_id"])
    ids = [c["command_id"] for c in d.journal.commands(tid)]
    assert len(ids) == 2 and not writes(mock)
    assert not any(s["name"] == "task_send_command" for s in d.ops.get(op["operation_id"])["steps"])
    async with restarted_daemon(d) as restarted:
        await settle_operations(restarted.ops)
        done = restarted.ops.get(op["operation_id"])
        assert done["status"] == "succeeded"
        assert_command_refs(restarted, op["operation_id"], "task_send_command")
        assert [c["command_id"] for c in restarted.journal.commands(tid)] == ids
        assert [frame["channel"] for frame in writes(mock)] == ["claude:send-message"]


@pytest.mark.parametrize("continuation", [False, True], ids=["submit", "continuation"])
@pytest.mark.parametrize("old_row", [False, True], ids=["atomic_refs", "legacy_missing_refs"])
async def test_a05_submission_refs_commit_with_receipt_and_replay_without_effect(owned, mock, monkeypatch, continuation, old_row):
    d, tid = owned
    params = {"project": "p", "original_words": "one instruction"}
    if continuation:
        params.update(continuation=True, parent_task_id=tid)
    op, _ = d.ops.create(api_auth.Principal("operator-dashboard", frozenset({"start"})), action="task.submit",
                         target={"host": "h1", "workspace": "demo-project"}, params=params,
                         idempotency_key="submission-refs")
    step = "task_continuation" if continuation else "task_submit"
    original_effect = OpContext.effect

    def after_receipt(ctx, name, fn, **kwargs):
        result = original_effect(ctx, name, fn, **kwargs)
        if name == step:
            assert d.ops.get(op["operation_id"])["external_refs"]["task_id"] == result["task_id"]
            raise asyncio.CancelledError("crash after submitting task and committing its refs")
        return result

    with monkeypatch.context() as patch:
        patch.setattr(OpContext, "effect", after_receipt)
        with pytest.raises(asyncio.CancelledError):
            await d.ops._execute(op["operation_id"])
    refs = d.ops.get(op["operation_id"])["external_refs"]
    snapshot = task_effect_snapshot(d, refs["task_id"])
    count = d.journal.db.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
    if old_row:
        retained = {"admission_binding": refs["admission_binding"]} if continuation else {}
        d.ops.db.execute("UPDATE operations SET external_refs=? WHERE operation_id=?", (json.dumps(retained), op["operation_id"]))

    def no_effect(*args, **kwargs):
        pytest.fail("saved submission receipt must not rerun its effect")

    async with restarted_daemon(d) as restarted:
        monkeypatch.setattr(restarted.journal, "record_continuation" if continuation else "submit", no_effect)
        await settle_operations(restarted.ops)
        done = restarted.ops.get(op["operation_id"])
        assert done["status"] == "succeeded" and done["external_refs"] == refs
        assert done["result"]["task_id"] == refs["task_id"]
        assert task_effect_snapshot(restarted, refs["task_id"]) == snapshot
        assert restarted.journal.db.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == count
        assert not writes(mock)


@pytest.mark.parametrize("kind", ["send", "answer", "interrupt", "task_send", "prepared"])
@pytest.mark.parametrize("keep_binding", [False, True], ids=["preupgrade_unbound", "bound"])
async def test_a05_old_command_receipt_replay_repairs_refs_without_repeating_effect(owned, mock, monkeypatch, kind, keep_binding):
    d, tid = owned
    mock.echo_sends = True
    mock.states[SID]["messages"] = list(mock.archives[SID])
    op = command_ref_operation(d, tid, mock, kind)
    if kind == "interrupt":
        mock.metas[SID]["isStreaming"] = mock.states[SID]["isStreaming"] = False
    await settle_operations(d.ops)
    done = d.ops.get(op["operation_id"])
    assert done["status"] == "succeeded"
    d.journal.pause(tid)
    d.journal.resume(tid)
    snapshot = task_effect_snapshot(d, tid)
    ids = [c["command_id"] for c in d.journal.commands(tid)]
    frames = len(writes(mock))
    refs = done["external_refs"]
    retained = {"admission_binding": refs["admission_binding"]} if keep_binding else {}
    d.ops.db.execute("UPDATE operations SET external_refs=?,status='running',result=NULL WHERE operation_id=?",
                     (json.dumps(retained), op["operation_id"]))

    def no_new_command(*args, **kwargs):
        pytest.fail("repair must replay the receipt, never create a command")

    async with restarted_daemon(d) as restarted:
        monkeypatch.setattr(restarted.journal, "command", no_new_command)
        await settle_operations(restarted.ops)
        result = restarted.ops.get(op["operation_id"])
        assert result["status"] == "succeeded" and result["result"] == done["result"]
        assert result["steps"] == done["steps"]
        assert {k: result["external_refs"][k] for k in ("task_id", "command_id", "control_version")} == {
            k: refs[k] for k in ("task_id", "command_id", "control_version")}
        assert (result["external_refs"].get("admission_binding") or None) == retained.get("admission_binding")
        assert task_effect_snapshot(restarted, tid) == snapshot
        assert [c["command_id"] for c in restarted.journal.commands(tid)] == ids
        assert len(writes(mock)) == frames
        first_repair = restarted.ops.get(op["operation_id"])
        task_control.replay_command_refs(OpContext(restarted.ops, restarted.ops._row(op["operation_id"])))
        assert restarted.ops.get(op["operation_id"]) == first_repair


async def test_a05_command_receipt_and_refs_roll_back_together(owned, monkeypatch):
    d, tid = owned
    op, _ = task_send_operation(d, tid, "refs-rollback")
    ctx = OpContext(d.ops, d.ops._row(op["operation_id"]))
    original = d.ops._merge_refs
    before = d.ops.get(op["operation_id"])["external_refs"]

    def create():
        command, fresh = d.journal.command(tid, "send", SID, {"control_version": 0}, "refs-rollback")
        return {**command, "fresh": fresh}

    def crash_after_merge(operation_id, refs):
        original(operation_id, refs)
        raise asyncio.CancelledError("crash during nested refs savepoint")

    with monkeypatch.context() as patch:
        patch.setattr(d.ops, "_merge_refs", crash_after_merge)
        with pytest.raises(asyncio.CancelledError):
            ctx.effect("task_send_command", create, refs=task_control.command_refs)
    assert not d.journal.commands(tid)
    assert d.ops.get(op["operation_id"])["external_refs"] == before
    assert d.ops.get(op["operation_id"])["steps"][0]["status"] == "started"
    command = ctx.effect("task_send_command", create, refs=task_control.command_refs)
    assert_command_refs(d, op["operation_id"], "task_send_command")
    assert len(d.journal.commands(tid)) == 1
    assert command["command_id"] == d.journal.commands(tid)[0]["command_id"]
    d.ops.cancel(OWNER, op["operation_id"])


def assert_terminal_send_replay(d, tid, op, code, commands, frames, mock):
    refused = d.ops.get(op["operation_id"])
    assert refused["status"] == "failed" and refused["error_code"] == code
    assert d.journal.commands(tid) == commands and len(writes(mock)) == frames
    replay, created = task_send_operation(d, tid, op["idem_key"])
    assert not created and d.ops.get(replay["operation_id"]) == refused


@pytest.mark.parametrize("boundary", ["last_paused", "presence", "version", "other_cancel"])
async def test_a05_a07_cancelled_send_command_survives_restart_without_uncertain_task(owned, mock, monkeypatch, boundary):
    d, tid = owned
    op, _ = task_send_operation(d, tid, "cancelled-crash")
    with monkeypatch.context() as patch:
        if boundary == "presence":
            original_send = d.coordinator._send
            probes = 0

            async def initial_send(task, sid, text, purpose, **kwargs):
                return await original_send(task, sid, text, "lead:initial", **kwargs)

            async def presence(task, sid):
                nonlocal probes
                probes += 1
                if probes == 2:
                    await d.coordinator.pause(tid)
                return "present"

            patch.setattr(d.coordinator, "_send", initial_send)
            patch.setattr(d.adapter, "session_presence", presence)
        else:
            original_effect = OpContext.effect

            def pause_after_command_receipt(ctx, name, fn, **kwargs):
                result = original_effect(ctx, name, fn, **kwargs)
                if name == "task_send_command":
                    if boundary == "other_cancel":
                        d.journal.command_status(result["command_id"], "cancelled")
                    else:
                        d.journal.pause(tid)
                return result

            patch.setattr(OpContext, "effect", pause_after_command_receipt)
        stop_after_command_status(d, patch, "cancelled")
        with pytest.raises(asyncio.CancelledError):
            await d.ops._execute(op["operation_id"])
    if boundary == "version":
        d.journal.resume(tid)  # another control commits after the crash, before the worker can recover
    snapshot, commands = task_effect_snapshot(d, tid), d.journal.commands(tid)
    assert len(commands) == 1 and commands[0]["status"] == "cancelled" and not writes(mock)
    assert d.ops.get(op["operation_id"])["status"] == "running"
    assert not any(s["name"] in {"task_send_result", "task_send_refusal"} for s in d.ops.get(op["operation_id"])["steps"])
    code = "CONTROL_VERSION_CONFLICT" if boundary == "version" else (
        "TASK_SEND_NOT_DISPATCHED" if boundary == "other_cancel" else "TASK_PAUSED")
    async with restarted_daemon(d) as restarted:
        await settle_operations(restarted.ops)
        assert_terminal_send_replay(restarted, tid, op, code, commands, 0, mock)
        assert task_effect_snapshot(restarted, tid) == snapshot
        if code == "TASK_PAUSED":
            assert await restarted.coordinator.tick(tid) == snapshot["task"]
            restarted.journal.resume(tid)
            assert_terminal_send_replay(restarted, tid, op, code, commands, 0, mock)
            fresh, _ = task_send_operation(restarted, tid, "cancelled-crash:resumed")
            await settle_operations(restarted.ops)
            assert restarted.ops.get(fresh["operation_id"])["status"] == "succeeded"
            assert len(restarted.journal.commands(tid)) == 2 and len(writes(mock)) == 1


@pytest.mark.parametrize("cause", ["local_refusal", "version", "accepted_false", "vanished", "unknown_presence"])
@pytest.mark.parametrize("crash_window", ["command_status", "local_outcome"])
async def test_a05_a07_rejected_send_command_survives_restart_without_uncertain_task(owned, mock, monkeypatch,
                                                                                   cause, crash_window):
    d, tid = owned
    op, _ = task_send_operation(d, tid, "rejected-crash")
    initial = cause in {"vanished", "unknown_presence"}
    with monkeypatch.context() as patch:
        if initial:
            original_send = d.coordinator._send
            probes = 0

            async def initial_send(task, sid, text, purpose, **kwargs):
                return await original_send(task, sid, text, "lead:initial", **kwargs)

            async def presence(*args):
                nonlocal probes
                probes += 1
                return "present" if probes == 1 else "vanished"

            patch.setattr(d.coordinator, "_send", initial_send)
            patch.setattr(d.adapter, "session_presence", presence)
        elif cause in {"local_refusal", "version"}:
            original_send = d.adapter.send

            async def refusing(*args):
                if cause == "version":
                    d.journal.resume(tid)
                    return await original_send(*args)
                raise service.WriteRefused("local streaming guard refused send")

            patch.setattr(d.adapter, "send", refusing)
        else:
            mock.handlers["claude:send-message"] = lambda params: {"accepted": False}
        if crash_window == "command_status":
            stop_after_command_status(d, patch, "rejected")
        elif cause in {"local_refusal", "version"}:
            original_transition = d.ops._transition

            def stop_before_operation_failure(operation_id, status, **kwargs):
                if status == "failed":
                    raise asyncio.CancelledError("crash after local outcome before operation settlement")
                return original_transition(operation_id, status, **kwargs)

            patch.setattr(d.ops, "_transition", stop_before_operation_failure)
        else:
            original_start = d.ops._step_start

            def stop_before_refusal_intent(operation_id, name, request):
                if name == "task_send_refusal":
                    raise asyncio.CancelledError("crash after local outcome before refusal intent")
                return original_start(operation_id, name, request)

            patch.setattr(d.ops, "_step_start", stop_before_refusal_intent)
        with pytest.raises(asyncio.CancelledError):
            await d.ops._execute(op["operation_id"])
    snapshot, commands = task_effect_snapshot(d, tid), d.journal.commands(tid)
    frames = int(cause == "accepted_false")
    assert len(commands) == 1 and commands[0]["status"] == "rejected" and len(writes(mock)) == frames
    assert d.ops.get(op["operation_id"])["status"] == "running"
    assert not any(s["name"] == "task_send_refusal" for s in d.ops.get(op["operation_id"])["steps"])
    code = {"local_refusal": "REFUSED", "version": "CONTROL_VERSION_CONFLICT"}.get(cause, "NOT_ACCEPTED")
    async with restarted_daemon(d) as restarted:
        if initial:
            async def presence(*args):
                return "unknown" if cause == "unknown_presence" else "vanished"
            monkeypatch.setattr(restarted.adapter, "session_presence", presence)
        await settle_operations(restarted.ops)
        assert_terminal_send_replay(restarted, tid, op, code, commands, frames, mock)
        task = restarted.journal.get(tid)
        assert task["state"] != "uncertain" and task["paused"] == snapshot["task"]["paused"]
        assert task["control_version"] == snapshot["task"]["control_version"]
        if cause in {"local_refusal", "version"} or crash_window == "local_outcome":
            assert task_effect_snapshot(restarted, tid) == snapshot
        elif cause == "vanished":
            assert task["state"] == "queued" and task["session_id"] is None and task["session_replacements"] == 1
        else:
            assert task["state"] == "needs_ted"


async def test_a07_rejected_send_recovery_preserves_a_later_accepted_command(owned, mock, monkeypatch):
    d, tid = owned
    op, _ = task_send_operation(d, tid, "older-rejected")
    mock.handlers["claude:send-message"] = lambda params: {"accepted": False}
    with monkeypatch.context() as patch:
        stop_after_command_status(d, patch, "rejected")
        with pytest.raises(asyncio.CancelledError):
            await d.ops._execute(op["operation_id"])
    mock.handlers.pop("claude:send-message")
    fresh, _ = task_send_operation(d, tid, "later-accepted")
    await d.ops._execute(fresh["operation_id"])
    assert d.ops.get(fresh["operation_id"])["status"] == "succeeded"
    snapshot, commands = task_effect_snapshot(d, tid), d.journal.commands(tid)
    assert commands[-1]["status"] == "accepted" and snapshot["task"]["state"] == "running"
    async with restarted_daemon(d) as restarted:
        await settle_operations(restarted.ops)
        assert_terminal_send_replay(restarted, tid, op, "NOT_ACCEPTED", commands, 2, mock)
        assert task_effect_snapshot(restarted, tid) == snapshot


@pytest.mark.parametrize("status", ["cancelled", "rejected"])
@pytest.mark.parametrize("restart_path", ["send", "tick"])
async def test_a07_legacy_send_reuses_terminal_command_without_uncertain_task(owned, mock, monkeypatch, status, restart_path):
    d, tid = owned
    async def presence(*args):
        return "present"
    with monkeypatch.context() as patch:
        async def refusing(*args):
            if status == "cancelled":
                from bat_agent_connector.errors import TaskDispatchCancelled
                raise TaskDispatchCancelled("cancelled before frame")
            raise service.WriteRefused("local streaming guard refused send")

        patch.setattr(d.adapter, "send", refusing)
        patch.setattr(d.adapter, "session_presence", presence)
        stop_after_command_status(d, patch, status)
        with pytest.raises(asyncio.CancelledError):
            await d.coordinator._send(d.journal.get(tid), SID, "one instruction", "lead:initial")
    commands, snapshot = d.journal.commands(tid), task_effect_snapshot(d, tid)
    async with restarted_daemon(d) as restarted:
        monkeypatch.setattr(restarted.adapter, "session_presence", presence)
        if restart_path == "send":
            result = await restarted.coordinator._send(restarted.journal.get(tid), SID, "one instruction", "lead:initial")
        else:
            result = await restarted.coordinator.tick(tid)
        assert result["state"] == ("accepted" if status == "cancelled" else "needs_ted")
        assert restarted.journal.commands(tid) == commands and not writes(mock)
        if status == "cancelled":
            assert task_effect_snapshot(restarted, tid) == snapshot


@pytest.mark.parametrize("action", ["answer", "interrupt", "permissions", "relay"])
async def test_a07_other_controls_refuse_pause_while_waiting_for_session_lock(owned, mock, monkeypatch, action):
    d, tid = owned
    entered = asyncio.Event()
    original = d.coordinator.session_control

    async def controlling(*args, **kwargs):
        entered.set()
        return await original(*args, **kwargs)

    monkeypatch.setattr(d.coordinator, "session_control", controlling)
    mock.states[SID]["pendingAskUser"] = {"toolUseId": "ask-1", "questions": [{"question": "Choice?"}]}
    async with d.coordinator._lock("h1", SID):
        if action in {"answer", "interrupt"}:
            op, _ = d.ops.create(OWNER,
                                 action="session." + action, target={"host": "h1", "session_id": SID},
                                 params={"tool_use_id": "ask-1", "answers": ["yes"]} if action == "answer" else {},
                                 idempotency_key="pause-other")
            worker = asyncio.create_task(settle_operations(d.ops))
        elif action == "permissions":
            worker = asyncio.create_task(lifecycle.session_set_permissions(d.fleet, "h1", SID, confirm=True))
        elif action == "relay":
            worker = asyncio.create_task(lifecycle.session_relay(d.fleet, "h1", "instruction", session_id=SID,
                                                                confirm=True, dry_run=False))
        await asyncio.wait_for(entered.wait(), 5)
        paused = await d.coordinator.pause(tid)
    if action in {"answer", "interrupt"}:
        await worker
        result = d.ops.get(op["operation_id"])
        assert result["status"] == "failed" and result["error_code"] == "CONTROL_VERSION_CONFLICT"
    else:
        with pytest.raises(TaskControlRefused, match="CONTROL_VERSION_CONFLICT"):
            await worker
    assert not d.journal.commands(tid) and not writes(mock)
    assert d.journal.get(tid) == paused


async def test_a07_approve_pending_deferred_raise_and_relay_do_not_jump_pause(owned, mock):
    d, tid = owned
    d.journal.pause(tid)
    mock.states[SID]["pendingPermission"] = {"toolUseId": "permission-1", "toolName": "Bash", "input": {}}
    registry.update("h1", SID, permission_raise_pending="allow_all")
    with pytest.raises(WriteRefused, match="LEGACY_PERMISSION_RAISE_DISABLED"):
        await lifecycle.approve_pending(d.fleet, "h1", confirm=True)
    result = await lifecycle._raise_deferred(d.fleet, "h1", False)
    assert result[0]["error_code"] == "LEGACY_PERMISSION_RAISE_DISABLED"
    assert registry.get("h1", SID)["permission_raise_pending"] == "allow_all"
    assert not writes(mock)
    with pytest.raises(TaskControlRefused, match="TASK_PAUSED"):
        await lifecycle.session_relay(d.fleet, "h1", "a request", session_id=SID, confirm=True, dry_run=False)
    assert not writes(mock)


async def test_a07_waiting_permission_versions_reviewers_and_policy_first(owned, mock):
    d, tid = owned
    d.journal.db.execute("UPDATE tasks SET state='waiting_permission' WHERE task_id=?", (tid,))
    with pytest.raises(TaskControlRefused, match="TASK_STATE_BLOCKED"):
        await service.session_send(d.fleet, "h1", SID, "outside", confirm=True)
    with pytest.raises(TaskControlRefused, match="CONTROL_VERSION_CONFLICT"):
        await service.session_interrupt(d.fleet, "h1", SID, confirm=True, control_version=99)
    mock.states[SID]["pendingPermission"] = {"toolUseId": "permission-1", "toolName": "Bash", "input": {}}
    answer = await service.session_answer(d.fleet, "h1", SID, permission="allow", confirm=True, tool_use_id="permission-1")
    assert answer["permission"] == "allow"
    reviewer = "sess-codex-0002"
    adopt(reviewer, task_id=tid, role="reviewer", agent_preset="codex-agent")
    d.journal.db.execute("UPDATE tasks SET reviewer_session_id=? WHERE task_id=?", (reviewer, tid))
    with pytest.raises(TaskControlRefused, match="TASK_STATE_BLOCKED"):
        await service.session_interrupt(d.fleet, "h1", reviewer, confirm=True)
    # Losing creation evidence leaves an unknown resource; policy wins over the task pause.
    registry.update("h1", SID, created_at=None, recovered_from=None)
    d.journal.pause(tid)
    from bat_agent_connector.errors import ResourceReadOnly
    with pytest.raises(ResourceReadOnly):
        await service.session_interrupt(d.fleet, "h1", SID, confirm=True)


async def test_a07_task_pause_abort_is_journaled_control(owned, mock):
    d, tid = owned
    d.journal.db.execute("UPDATE tasks SET state='verifying' WHERE task_id=?", (tid,))
    result = await call_owned(d, "work_pause", {"task_id": tid, "abort_current": True, "idempotency_key": "abort"})
    assert result["paused"] and result["operation_status"] == "succeeded"
    op = d.ops.get(result["operation_id"])
    assert [(s["name"], s["status"]) for s in op["steps"]] == [("task_pause", "succeeded"), ("abort_current", "succeeded")]
    assert [r["channel"] for r in writes(mock)] == ["claude:abort-session"]
    assert await call_owned(d, "work_pause", {"task_id": tid, "abort_current": True, "idempotency_key": "abort"}) == result
    assert len(writes(mock)) == 1


async def test_a09_second_client_uses_central_owner(owned, mock, fleet_factory, monkeypatch):
    import asyncio
    d, tid = owned
    client = fleet_factory(writes=True, orchestrate=True, managed_roots=["/srv"], safety={"write_min_interval_s": 0})
    server = await asyncio.start_server(d._handle, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    d._endpoint = f"http://127.0.0.1:{port}/rpc"
    d._write_owner_pointer()
    try:
        result = await service.session_send(client, "h1", SID, "through owner", confirm=True)
        assert result["accepted"]
        commands = d.journal.commands(tid)
        assert len(commands) == 1 and commands[0]["kind"] == "send"
        assert len(writes(mock)) == 1
    finally:
        server.close()
        await server.wait_closed()
        await client.close()


async def test_a05_task_submit_actor_scope_continuation_and_upgrade_replay(owned):
    d, tid = owned
    payload = {"project": "p", "host": "h1", "workspace": "demo-project", "original_words": "new task",
               "idempotency_key": "submit"}
    first = await call_owned(d, "work_submit", payload)
    assert await call_owned(d, "work_submit", payload) == first
    p = api_auth.Principal("another-client", frozenset({"start"}))
    other = await call_owned(d, "work_submit", payload, principal=p)
    assert other["task_id"] != first["task_id"]
    with pytest.raises(OperationError, match="IDEMPOTENCY_CONFLICT"):
        await call_owned(d, "work_submit", {**payload, "original_words": "different"})
    old = d.journal.submit(project="p", host="h1", workspace="demo-project", original_words="historical",
                           engine="goose", recipe="goose-session", idempotency_key="historical")
    linked = await call_owned(d, "work_submit", {**payload, "original_words": "historical", "idempotency_key": "historical"})
    assert linked["task_id"] == old["task_id"]
    separate = await call_owned(d, "work_submit", {**payload, "original_words": "historical", "idempotency_key": "historical"}, principal=p)
    assert separate["task_id"] != old["task_id"]
    follow = {**payload, "parent_task_id": tid, "continuation": True, "idempotency_key": "follow"}
    result = await call_owned(d, "work_submit", follow)
    assert await call_owned(d, "work_submit", follow) == result
    assert d.journal.get(tid)["continuations"] == 1
    with pytest.raises(OperationError, match="IDEMPOTENCY_CONFLICT"):
        await call_owned(d, "work_submit", {**follow, "original_words": "different steering"})


async def test_a05_restart_after_effect_commit_replays_without_increment(mock, tmp_path):
    config = make_config(mock, writes=True, orchestrate=True)
    first = TaskDaemon(config, tmp_path / "restart.db")
    task = first.journal.submit(project="p", host="h1", workspace="w", original_words="do it", idempotency_key="old")
    principal = api_auth.Principal("operator-dashboard", frozenset({"operate"}))
    op, _ = first.ops.create(principal, action="task.pause", target={"task_id": task["task_id"]},
                              preconditions={"control_version": 0}, idempotency_key="crash")
    ctx = OpContext(first.ops, first.ops._row(op["operation_id"]))
    ctx.effect("task_pause", lambda: first.journal.pause(task["task_id"]))
    first.journal.db.execute("UPDATE operations SET status='running' WHERE operation_id=?", (op["operation_id"],))
    await first.fleet.close()
    first.journal.close()
    second = TaskDaemon(config, tmp_path / "restart.db")
    try:
        await settle_operations(second.ops)
        assert second.ops.get(op["operation_id"])["status"] == "succeeded"
        assert second.journal.get(task["task_id"])["control_version"] == 1
        assert len([e for e in second.journal.events(task["task_id"]) if e["kind"] == "paused"]) == 1
    finally:
        await second.fleet.close()
        second.journal.close()


async def test_a05_verify_request_ted_and_stage_use_original_receipts(owned, monkeypatch):
    d, tid = owned
    calls = []
    evidence = {"candidate_commit": "a" * 40, "tree_hash": "b" * 40, "command": "trusted tests",
                "exit_code": 0, "log_ref": "log-1", "output_sha256": "c" * 64, "source": "observed_runner"}

    async def verify(task):
        calls.append(task["task_id"])
        return evidence
    monkeypatch.setattr(d.adapter, "run_verification", verify)
    result = await call_owned(d, "task_run_verification", {"task_id": tid, "idempotency_key": "verify"})
    assert await call_owned(d, "task_run_verification", {"task_id": tid, "idempotency_key": "verify"}) == result
    assert calls == [tid]
    assert d.journal.db.execute("SELECT count(*) FROM observed_verifications").fetchone()[0] == 1
    with pytest.raises(OperationError, match="IDEMPOTENCY_CONFLICT"):
        await call_owned(d, "task_run_verification", {"task_id": tid, "idempotency_key": "verify", "exit_code": 1})
    ask = {"task_id": tid, "idempotency_key": "ted", "reason": "needs a decision"}
    first = await call_owned(d, "task_request_ted", ask)
    assert await call_owned(d, "task_request_ted", ask) == first
    assert len([e for e in d.journal.events(tid) if e["kind"] == "ted_requested"]) == 1
    with pytest.raises(OperationError, match="IDEMPOTENCY_CONFLICT"):
        await call_owned(d, "task_request_ted", {**ask, "reason": "different"})
    d.journal.db.execute("UPDATE tasks SET state='done',verification_commit=?,verification_tree=? WHERE task_id=?",
                         ("a" * 40, "b" * 40, tid))
    stage = {"task_id": tid, "idempotency_key": "stage", "stage": "adopted", "ref": "commit-a"}
    first = await call_owned(d, "work_mark_stage", stage)
    assert await call_owned(d, "work_mark_stage", stage) == first
    with pytest.raises(OperationError, match="IDEMPOTENCY_CONFLICT"):
        await call_owned(d, "work_mark_stage", {**stage, "ref": "commit-b"})


def task_effect_snapshot(d, tid):
    return {"task": d.journal.get(tid), "verification": d.journal.observed_verification(tid),
            "delivery": d.journal.delivery(tid), "events": d.journal.events(tid),
            "commands": d.journal.commands(tid)}


def trusted_evidence():
    return {"candidate_commit": "a" * 40, "tree_hash": "b" * 40, "command": "trusted tests",
            "exit_code": 0, "log_ref": "log-1", "output_sha256": "c" * 64, "source": "observed_runner"}


def finish_verified_task(d, tid):
    evidence = trusted_evidence()
    d.journal.change(tid, "verifying")
    d.journal.record_observed_verification(tid, evidence)
    return d.journal.change(tid, "done", fields={"verification_commit": evidence["candidate_commit"],
                                                "verification_tree": evidence["tree_hash"], "result": "tests passed"})


def locked_task_operation(d, tid, action, key):
    params = {"task.request_ted": {"reason": "needs a decision"}, "task.verify": {},
              "task.mark_stage": {"stage": "adopted", "ref": "commit-a"}}[action]
    return d.ops.create(OWNER if action == "task.mark_stage" else capability_caller(d, tid),
                        action=action, target={"task_id": tid}, params=params, idempotency_key=key)


async def wait_for_operation_running(d, op):
    async def running():
        while d.ops.get(op["operation_id"])["status"] == "accepted":
            await asyncio.sleep(0)
        assert d.ops.get(op["operation_id"])["status"] == "running"
    await asyncio.wait_for(running(), 5)


def assert_locked_action_refusal(d, tid, op, snapshot, code):
    refused = d.ops.get(op["operation_id"])
    assert refused["status"] == "failed" and refused["error_code"] == code
    assert refused["steps"] == []  # the state check happens before any effect or receipt intent
    assert task_effect_snapshot(d, tid) == snapshot
    replay, created = locked_task_operation(d, tid, op["action"], op["idem_key"])
    assert not created and d.ops.get(replay["operation_id"]) == refused


@pytest.mark.parametrize("action", ["task.request_ted", "task.verify"])
async def test_a05_locked_task_action_refuses_task_completed_by_tick(owned, mock, monkeypatch, action):
    d, tid = owned
    entered, release = asyncio.Event(), asyncio.Event()
    completed = {}
    d.journal.change(tid, "verifying")
    d.coordinator.verification_quiet_s = 0

    async def idle(*args):
        return {"streaming": False, "pending": None}

    async def candidate(*args):
        return {"clean": True, "candidate_commit": "a" * 40, "tree_hash": "b" * 40}

    async def verifying(*args):
        entered.set()
        await release.wait()
        return trusted_evidence()

    original_tick = d.coordinator._tick

    async def completing(task_id):
        result = await original_tick(task_id)
        completed.update(task_effect_snapshot(d, tid))  # captured before tick releases the task lock
        return result

    monkeypatch.setattr(d.adapter, "read", idle)
    monkeypatch.setattr(d.adapter, "candidate_identity", candidate)
    monkeypatch.setattr(d.adapter, "run_verification", verifying)
    monkeypatch.setattr(d.coordinator, "_tick", completing)
    tick = asyncio.create_task(d.coordinator.tick(tid))
    try:
        await asyncio.wait_for(entered.wait(), 5)
        op, _ = locked_task_operation(d, tid, action, "tick-completes")
        worker = asyncio.create_task(settle_operations(d.ops))
        await wait_for_operation_running(d, op)
    finally:
        release.set()
        await tick
    await worker
    assert completed["task"]["state"] == "done" and completed["delivery"]["stage"] == "verified"
    assert_locked_action_refusal(d, tid, op, completed, "TASK_STATE_BLOCKED")
    assert not writes(mock)


@pytest.mark.parametrize("change", ["state", "verification"])
async def test_a05_mark_stage_rechecks_verified_done_after_task_lock(owned, mock, change):
    d, tid = owned
    finish_verified_task(d, tid)
    async with d.coordinator._task_locks.setdefault(tid, asyncio.Lock()):
        op, _ = locked_task_operation(d, tid, "task.mark_stage", "stage-race")
        worker = asyncio.create_task(settle_operations(d.ops))
        await wait_for_operation_running(d, op)
        # Model a corrected completion record; normal journal transitions cannot leave a terminal task.
        assignment = "state='running'" if change == "state" else "verification_commit=NULL"
        d.journal.db.execute(f"UPDATE tasks SET {assignment} WHERE task_id=?", (tid,))
        snapshot = task_effect_snapshot(d, tid)
    await worker
    assert_locked_action_refusal(d, tid, op, snapshot, "TASK_STATE_BLOCKED")
    assert not writes(mock)


@pytest.mark.parametrize("action", ["task.request_ted", "task.verify"])
async def test_a05_scoped_task_action_rechecks_pause_after_task_lock(owned, mock, action):
    d, tid = owned
    async with d.coordinator._task_locks.setdefault(tid, asyncio.Lock()):
        op, _ = locked_task_operation(d, tid, action, "scoped-pause-race")
        worker = asyncio.create_task(settle_operations(d.ops))
        await wait_for_operation_running(d, op)
        await d.coordinator.pause(tid)
        snapshot = task_effect_snapshot(d, tid)
    await worker
    # Pause advanced the incarnation before this operation's first effect.
    assert_locked_action_refusal(d, tid, op, snapshot, "CONTROL_VERSION_CONFLICT")
    assert not writes(mock)


@pytest.mark.parametrize("action,method", [("task.request_ted", "task_request_ted"),
                                         ("task.verify", "task_run_verification"), ("task.mark_stage", "work_mark_stage")])
async def test_a05_locked_task_action_replays_receipt_after_state_change(owned, monkeypatch, action, method):
    d, tid = owned

    async def verifying(*args):
        return trusted_evidence()

    monkeypatch.setattr(d.adapter, "run_verification", verifying)
    if action == "task.mark_stage":
        finish_verified_task(d, tid)
    params = {"task.request_ted": {"reason": "needs a decision"}, "task.verify": {},
              "task.mark_stage": {"stage": "adopted", "ref": "commit-a"}}[action]
    first = await call_owned(d, method, {"task_id": tid, "idempotency_key": "receipt-replay", "control_version": 0, **params})
    original = d.ops.get(first["operation_id"])
    if action == "task.mark_stage":
        d.journal.db.execute("UPDATE tasks SET verification_commit=NULL WHERE task_id=?", (tid,))
    elif action == "task.request_ted":
        d.journal.change(tid, "failed")
    else:
        d.journal.pause(tid)
    snapshot = task_effect_snapshot(d, tid)
    # Resume a worker after the succeeded receipt committed, before the operation result was saved.
    d.journal.db.execute("UPDATE operations SET status='running',result=NULL WHERE operation_id=?", (first["operation_id"],))
    await settle_operations(d.ops)
    replay = d.ops.get(first["operation_id"])
    assert replay["status"] == "succeeded" and replay["result"] == original["result"]
    assert replay["steps"] == original["steps"] and task_effect_snapshot(d, tid) == snapshot


@pytest.mark.parametrize("action", ["task.pause", "task.resume"])
async def test_a05_task_controls_do_not_wait_for_task_lock_or_change_terminal_task(owned, mock, action):
    d, tid = owned
    async with d.coordinator._task_locks.setdefault(tid, asyncio.Lock()):
        op, _ = d.ops.create(OWNER, action=action,
                             target={"task_id": tid}, idempotency_key="control-race")
        finish_verified_task(d, tid)
        snapshot = task_effect_snapshot(d, tid)
        await settle_operations(d.ops)
    assert d.ops.get(op["operation_id"])["status"] == "succeeded"
    assert task_effect_snapshot(d, tid) == snapshot and not writes(mock)


async def test_a05_reconcile_refuses_command_settled_while_waiting_for_task_lock(owned, mock, monkeypatch):
    d, tid = owned
    cmd, _ = d.journal.command(tid, "send", SID, {"purpose": "goose:one", "before": {}, "prompt_sha256": "d" * 64}, "pending")
    d.journal.command_status(cmd["command_id"], "uncertain")
    d.journal.change(tid, "uncertain")
    cap = d.journal.issue_reconcile_capability(tid, cmd["command_id"])
    entered, release = asyncio.Event(), asyncio.Event()

    async def readback(*args):
        entered.set()
        await release.wait()
        return {"accepted": True, "turn_attribution": "exact_echo", "turn_marker": cmd["message_id"]}

    monkeypatch.setattr(d.adapter, "reconcile_send", readback)
    tick = asyncio.create_task(d.coordinator.tick(tid))
    try:
        await asyncio.wait_for(entered.wait(), 5)
        params = {"outcome": "not_delivered", "actor": "operator", "source": "ticket-1", "evidence": "checked the exact turn"}
        target = {"task_id": tid, "command_id": cmd["command_id"]}
        principal = d.capability_principal(cap, "task.command.reconcile", target, "settled-race")
        op, _ = d.ops.create(principal, action="task.command.reconcile", target=target, params=params,
                             idempotency_key="settled-race")
        worker = asyncio.create_task(settle_operations(d.ops))
        await wait_for_operation_running(d, op)
    finally:
        release.set()
        await tick
    await worker
    refused = d.ops.get(op["operation_id"])
    assert refused["status"] == "failed" and refused["error_code"] == "INVALID_PARAMS" and refused["steps"] == []
    assert d.journal.get(tid)["state"] == "running" and d.journal.command_get(cmd["command_id"])["status"] == "accepted"
    assert d.journal.authorize_reconcile_capability(cap, tid, cmd["command_id"])
    assert d.journal.reconciliations(tid) == [] and not writes(mock)


async def test_a05_reconcile_capability_consumption_and_receipt_are_atomic(owned, monkeypatch):
    d, tid = owned
    cmd, _ = d.journal.command(tid, "send", SID, {"purpose": "goose:one", "prompt_sha256": "d" * 64}, "uncertain-send")
    d.journal.command_status(cmd["command_id"], "uncertain")
    d.journal.change(tid, "uncertain")
    cap = d.journal.issue_reconcile_capability(tid, cmd["command_id"])
    params = {"task_id": tid, "command_id": cmd["command_id"], "outcome": "not_delivered", "actor": "operator",
              "source": "ticket-1", "evidence": "inspected the exact turn", "idempotency_key": "reconcile"}
    principal = d.capability_principal(cap, "task.command.reconcile", params, "reconcile")
    first = await call_owned(d, "work_reconcile", params, principal=principal)
    assert not d.journal.authorize_reconcile_capability(cap, tid, cmd["command_id"])
    assert cap not in json.dumps([dict(r) for r in d.journal.db.execute("SELECT * FROM operations")])
    assert d.capability_principal(cap, "task.command.reconcile", params, "different-key") is None
    assert d.capability_principal(cap, "task.command.reconcile", params, "reconcile") == principal
    assert await call_owned(d, "work_reconcile", params, principal=principal) == first
    with pytest.raises(OperationError, match="IDEMPOTENCY_CONFLICT"):
        await call_owned(d, "work_reconcile", {**params, "outcome": "delivered"}, principal=principal)
    # Crash after consuming the capability, before the operation result was committed.
    d.journal.db.execute("UPDATE operations SET status='running',result=NULL WHERE operation_id=?", (first["operation_id"],))
    await settle_operations(d.ops)
    assert d.ops.get(first["operation_id"])["status"] == "succeeded"
    assert len(d.journal.reconciliations(tid)) == 1


async def test_a07_task_resume_reconciles_before_dispatch(owned, mock):
    d, tid = owned
    cmd, _ = d.journal.command(tid, "send", SID, {"purpose": "goose:one", "prompt_sha256": "d" * 64, "before": {}}, "pending")
    await call_owned(d, "work_pause", {"task_id": tid, "idempotency_key": "pause"})
    await call_owned(d, "work_resume", {"task_id": tid, "idempotency_key": "resume"})
    await d.coordinator.tick(tid)
    assert d.journal.command_get(cmd["command_id"])["status"] == "uncertain"
    assert not writes(mock)
    with pytest.raises(TaskControlRefused, match="TASK_COMMAND_PENDING"):
        await service.session_send(d.fleet, "h1", SID, "outside", confirm=True)


async def test_a07_pause_abort_late_resume_cannot_abort_new_control_version(owned, mock, monkeypatch):
    d, tid = owned
    original = d.fleet.client("h1")._invoke_checked

    async def resume_at_boundary(channel, *args, **kwargs):
        if channel == "claude:abort-session":
            d.journal.resume(tid)
        return await original(channel, *args, **kwargs)
    monkeypatch.setattr(d.fleet.client("h1"), "_invoke_checked", resume_at_boundary)
    with pytest.raises(OperationError, match="CONTROL_VERSION_CONFLICT"):
        await call_owned(d, "work_pause", {"task_id": tid, "abort_current": True, "idempotency_key": "abort"})
    assert not writes(mock)
    op = d.ops.list()["operations"][0]
    assert op["status"] == "failed" and op["error_code"] == "CONTROL_VERSION_CONFLICT"
    assert d.journal.get(tid)["control_version"] == 2


async def test_a05_rpc_http_and_mcp_share_task_action_keys(owned, monkeypatch):
    import asyncio

    from bat_agent_connector.mcp_server import build_server
    from tests.test_api_v1 import http

    d, tid = owned
    server = await asyncio.start_server(d._handle, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    monkeypatch.setenv("BATC_TASK_URL", f"http://127.0.0.1:{port}/rpc")
    monkeypatch.setenv("BATC_TASK_ADMIN_TOKEN_FILE", str(d.admin_token_path))
    mcp, mcp_fleet = build_server(d.fleet.config)
    try:
        first = await mcp.call_tool("work_pause", {"task_id": tid, "idempotency_key": "shared"})
        text = json.dumps(first.model_dump(), default=str)
        op = d.ops.list()["operations"][0]
        assert op["operation_id"] in text
        status, result = await http(port, "POST", "/api/v1/operations?wait=2", tok=d._admin_token,
                                    body={"action": "task.pause", "target": {"task_id": tid},
                                          "params": {"abort_current": False, "actor": "service", "source_message_id": None},
                                          "idempotency_key": "shared"})
        assert status == 200 and result["operation"]["operation_id"] == op["operation_id"]
        assert d.journal.get(tid)["control_version"] == 1
    finally:
        await mcp_fleet.close()
        server.close()
        await server.wait_closed()


@pytest.mark.parametrize("kind", ["send", "failover", "start_lead", "answer", "interrupt", "permissions"])
async def test_a07_every_unresolved_runtime_kind_blocks_controls(owned, mock, kind):
    d, tid = owned
    d.journal.command(tid, kind, SID, {}, "pending-kind")
    with pytest.raises(TaskControlRefused, match="TASK_COMMAND_PENDING"):
        await service.session_interrupt(d.fleet, "h1", SID, confirm=True)
    assert not writes(mock)


async def test_a05_long_legacy_task_key_keeps_its_original_payload(owned):
    d, _ = owned
    params = {"project": "p", "host": "h1", "workspace": "demo-project", "original_words": "long key",
              "idempotency_key": "k" * 256}
    first = await call_owned(d, "work_submit", params)
    assert await call_owned(d, "work_submit", params) == first
    op = d.ops.get(first["operation_id"])
    assert op["params"]["legacy_idempotency_key"] == params["idempotency_key"]
    assert len(op["idem_key"]) <= 200
    with pytest.raises(OperationError, match="IDEMPOTENCY_CONFLICT"):
        await call_owned(d, "work_submit", {**params, "original_words": "changed"})


async def test_a07_scoped_operation_observes_late_control_version(owned, mock):
    d, tid = owned
    principal = OWNER
    op, _ = d.ops.create(principal, action="session.send", target={"task_id": tid},
                         params={"text": "instruction", "step_id": "step"},
                         preconditions={"control_version": 0}, idempotency_key="late")
    d.journal.pause(tid)
    d.journal.resume(tid)
    await settle_operations(d.ops)
    assert d.ops.get(op["operation_id"])["error_code"] == "CONTROL_VERSION_CONFLICT"
    assert not writes(mock)


async def test_a07_uncertain_task_send_reads_back_after_restart_without_resending(owned, mock, monkeypatch):
    from bat_agent_connector.errors import InvokeTimeout
    d, tid = owned

    async def lost(*args):
        raise InvokeTimeout("lost send reply")
    monkeypatch.setattr(d.adapter, "send", lost)
    params = {"task_id": tid, "text": "instruction", "step_id": "one", "idempotency_key": "lost"}
    first = await call_owned(d, "task_send", params)
    assert first["operation_status"] == "uncertain"
    command = next(c for c in d.journal.commands(tid) if c["kind"] == "send")
    assert command["status"] == "uncertain"
    # Restarted worker has no successful operation result. Missing proof must not dispatch again.
    d.journal.db.execute("UPDATE operations SET status='running',next_run_at=0 WHERE operation_id=?", (first["operation_id"],))
    await settle_operations(d.ops)
    assert not writes(mock)
    assert d.ops.get(first["operation_id"])["status"] == "uncertain"
    assert len([c for c in d.journal.commands(tid) if c["kind"] == "send"]) == 1


@pytest.mark.parametrize("action", ["session.send", "session.answer", "session.interrupt"])
@pytest.mark.parametrize("proven", [True, False], ids=["proven", "unproven"])
async def test_a07_operation_readback_and_coordinator_tick_settle_task_command(owned, mock, monkeypatch,
                                                                            action, proven):
    d, tid = owned
    prior = d.journal.change(tid, "running")
    mock.echo_sends = True
    if action == "session.answer":
        mock.states[SID]["pendingAskUser"] = {"toolUseId": "ask-1", "questions": [{"question": "Choice?"}]}
    if action == "session.interrupt":
        mock.metas[SID]["isStreaming"] = mock.states[SID]["isStreaming"] = True
    params = {"session.send": {"text": "runtime instruction"},
              "session.answer": {"answers": ["yes"], "tool_use_id": "ask-1"},
              "session.interrupt": {"mode": "soft"}}[action]
    channel = {"session.send": "claude:send-message", "session.answer": "claude:resolve-ask-user",
               "session.interrupt": "claude:interrupt-turn"}[action]
    principal = api_auth.Principal("operator-dashboard", frozenset({"operate"}))
    target = {"host": "h1", "session_id": SID}
    op, _ = d.ops.create(principal, action=action, target=target, params=params,
                         preconditions={"control_version": prior["control_version"]}, idempotency_key="lost")
    op_id = op["operation_id"]
    step_name = action.removeprefix("session.")
    command_status = "accepted" if action == "session.send" else "settled"
    client = d.fleet.client("h1")
    original_roundtrip = client._roundtrip

    async def lose_reply(frame, timeout, **kwargs):
        result = await original_roundtrip(frame, timeout, **kwargs)
        if frame["channel"] == channel:
            if action == "session.interrupt":
                mock.metas[SID]["isStreaming"] = mock.states[SID]["isStreaming"] = False
            raise ConnectionLost("reply lost after BAT took the frame")
        return result

    with monkeypatch.context() as patch:
        patch.setattr(client, "_roundtrip", lose_reply)
        await settle_operations(d.ops)
    command = d.journal.commands(tid)[-1]
    cid = command["command_id"]
    row = d.ops.get(op_id)
    assert row["status"] == "uncertain"
    assert next(s for s in row["steps"] if s["name"] == step_name)["status"] == "uncertain"
    assert row["external_refs"]["command_id"] == cid
    assert json.loads(command["payload"])["operation_id"] == op_id
    assert command["status"] == d.journal.get(tid)["state"] == "uncertain"
    assert registry.get_turn("h1", SID, "batc-" + op_id) is None
    assert len(writes(mock)) == 1 and writes(mock)[0]["channel"] == channel
    original_reconcile = d.coordinator._reconcile_command
    reconciled = []

    async def reconcile(task, cmd):
        assert d.coordinator._task_locks[tid].locked()
        reconciled.append(cmd["command_id"])
        return await original_reconcile(task, cmd)

    async def no_dispatch(*args, **kwargs):
        raise AssertionError("read-back must not re-enter session_control")

    async def unreadable(frame, timeout, **kwargs):
        result = await original_roundtrip(frame, timeout, **kwargs)
        return {**result, "result": None} if frame["channel"] in {
            "claude:get-session-meta", "claude:get-session-state", "claude:load-archived"} else result

    with monkeypatch.context() as patch:
        patch.setattr(d.coordinator, "session_control", no_dispatch)
        patch.setattr(d.coordinator, "_reconcile_command", reconcile)
        if not proven:
            patch.setattr(client, "_roundtrip", unreadable)
        for attempt in range(1 if proven else 2):
            d.journal.db.execute("UPDATE operations SET next_run_at=0 WHERE operation_id=?", (op_id,))
            await settle_operations(d.ops)
            row = d.ops.get(op_id)
            assert row["status"] == ("succeeded" if proven else "uncertain")
            assert next(s for s in row["steps"] if s["name"] == step_name)["status"] == (
                "succeeded" if proven else "uncertain")
            # The operation's proof does not settle the coordinator's command or bypass its gate.
            assert d.journal.command_get(cid)["status"] == d.journal.get(tid)["state"] == "uncertain"
            assert len(reconciled) == attempt
            with pytest.raises(OperationError, match="TASK_COMMAND_PENDING"):
                d.ops.create(principal, action="session.interrupt", target=target, idempotency_key="later")
            task = await d.coordinator.tick(tid)
            assert reconciled == [cid] * (attempt + 1)
            assert task["state"] == (prior["state"] if proven else "uncertain")
            assert task["control_version"] == prior["control_version"]
            assert d.journal.command_get(cid)["status"] == (command_status if proven else "uncertain")
            assert len(writes(mock)) == len(d.journal.commands(tid)) == 1
    if proven:
        later, _ = d.ops.create(principal, action="session.interrupt", target=target, idempotency_key="later")
        await settle_operations(d.ops)
        assert d.ops.get(later["operation_id"])["status"] == "succeeded"
        assert len(writes(mock)) == len(d.journal.commands(tid)) == 2


async def test_a09_live_owner_lease_loss_blocks_a_waiting_frame(owned, mock, monkeypatch):
    d, _ = owned
    original = d.fleet.client("h1")._invoke_checked

    async def lose_owner(channel, *args, **kwargs):
        if channel == "claude:send-message":
            d.release_owner()
        return await original(channel, *args, **kwargs)
    monkeypatch.setattr(d.fleet.client("h1"), "_invoke_checked", lose_owner)
    with pytest.raises(TaskControlRefused, match="TASK_OWNER_UNAVAILABLE"):
        await service.session_send(d.fleet, "h1", SID, "outside", confirm=True)
    assert not writes(mock)


async def test_a07_missing_registry_owner_does_not_downgrade_a_reserved_task_session(owned, mock):
    d, _ = owned
    registry.update("h1", SID, task_id=None)
    with pytest.raises(TaskControlRefused, match="TASK_BINDING_MISMATCH"):
        await service.session_send(d.fleet, "h1", SID, "outside", confirm=True)
    assert not writes(mock)


async def test_a07_task_scoped_send_preserves_late_gate_code(owned, mock, monkeypatch):
    d, tid = owned
    original = d.fleet.client("h1")._invoke_checked

    async def pause_at_frame(channel, *args, **kwargs):
        if channel == "claude:send-message":
            d.journal.pause(tid)
        return await original(channel, *args, **kwargs)
    monkeypatch.setattr(d.fleet.client("h1"), "_invoke_checked", pause_at_frame)
    with pytest.raises(OperationError, match="CONTROL_VERSION_CONFLICT"):
        await call_owned(d, "task_send", {"task_id": tid, "text": "instruction", "step_id": "one"})
    assert not writes(mock)
    assert d.journal.commands(tid)[0]["status"] == "rejected"


async def test_a07_verifying_refusal_does_not_wait_for_coordinator_lock(owned):
    import asyncio
    d, tid = owned
    lock = d.coordinator._task_locks.setdefault(tid, asyncio.Lock())
    d.journal.db.execute("UPDATE tasks SET state='verifying' WHERE task_id=?", (tid,))
    async with lock:
        with pytest.raises(TaskControlRefused, match="TASK_VERIFYING"):
            await asyncio.wait_for(service.session_interrupt(d.fleet, "h1", SID, confirm=True), 1)


async def test_a05_historical_bridge_and_operation_intent_are_atomic(owned, monkeypatch):
    d, _ = owned
    old = d.journal.submit(project="p", host="h1", workspace="demo-project", original_words="historical",
                           engine="goose", recipe="goose-session", idempotency_key="pre-upgrade")
    original = d.ops._step_done
    monkeypatch.setattr(d.ops, "kick", lambda: None)

    def crash(*args, **kwargs):
        raise RuntimeError("crash before bridge commit")
    monkeypatch.setattr(d.ops, "_step_done", crash)
    params = {"project": "p", "host": "h1", "workspace": "demo-project", "original_words": "historical",
              "idempotency_key": "pre-upgrade"}
    with pytest.raises(RuntimeError, match="bridge commit"):
        await call_owned(d, "work_submit", params)
    assert d.journal.db.execute("SELECT count(*) FROM operations").fetchone()[0] == 0
    monkeypatch.setattr(d.ops, "_step_done", original)
    result = await call_owned(d, "work_submit", params)
    assert result["task_id"] == old["task_id"]


async def test_a05_historical_bridge_survives_restart_before_task_effect(mock, tmp_path, monkeypatch):
    config = make_config(mock, writes=True, orchestrate=True)
    path = tmp_path / "upgrade.db"
    first = TaskDaemon(config, path)
    old = first.journal.submit(project="p", host="h1", workspace="w", original_words="historical",
                               engine="goose", recipe="goose-session", idempotency_key="pre-upgrade")
    monkeypatch.setattr(first.ops, "kick", lambda: None)

    async def crash():
        raise RuntimeError("crash after admission commit")
    monkeypatch.setattr(first.ops, "run_due", crash)
    with pytest.raises(RuntimeError, match="admission commit"):
        await first.call("work_submit", {"project": "p", "host": "h1", "workspace": "w", "original_words": "historical",
                                         "idempotency_key": "pre-upgrade"})
    op_id = first.journal.db.execute("SELECT operation_id FROM operations").fetchone()[0]
    await first.fleet.close()
    first.journal.close()
    second = TaskDaemon(config, path)
    try:
        await settle_operations(second.ops)
        assert second.ops.get(op_id)["result"]["task_id"] == old["task_id"]
        assert second.journal.db.execute("SELECT count(*) FROM tasks").fetchone()[0] == 1
    finally:
        await second.fleet.close()
        second.journal.close()


async def test_a07_runtime_codex_ack_requires_original_command_readback(owned, mock, monkeypatch):
    d, tid = owned
    sid = "sess-codex-0002"
    adopt(sid, task_id=tid, role="lead", agent_preset="codex-agent")
    mock.metas[sid]["isStreaming"] = False
    d.journal.db.execute("UPDATE tasks SET session_id=? WHERE task_id=?", (sid, tid))

    async def unknown(*args, **kwargs):
        return None
    monkeypatch.setattr(d.adapter, "reconcile_send", unknown)
    result = await service.session_send(d.fleet, "h1", sid, "runtime instruction", confirm=True)
    assert result["accepted"] is True  # the old result projection is retained in Part A
    assert d.journal.get(tid)["state"] == "uncertain"
    assert d.journal.commands(tid)[-1]["status"] == "uncertain"
    with pytest.raises(TaskControlRefused, match="TASK_COMMAND_PENDING"):
        await service.session_send(d.fleet, "h1", sid, "another instruction", confirm=True)
    assert len([r for r in writes(mock) if r["channel"] == "claude:send-message"]) == 1


async def test_a07_frame_guard_binds_prompt_before_client_resume(owned, mock):

    from bat_agent_connector.task_control import FrameGuard
    d, tid = owned
    prompt_hash = hashlib.sha256(b"intended prompt").hexdigest()
    command, _ = d.journal.command(tid, "send", SID, {"prompt_sha256": prompt_hash}, "bound")
    guard = FrameGuard(d.journal, tid, "h1", SID, 0, command["command_id"], prompt_sha256=prompt_hash)
    with pytest.raises(TaskControlRefused, match="TASK_BINDING_MISMATCH"):
        await service.session_send(d.fleet, "h1", SID, "different prompt", confirm=True, _task_guard=guard)
    assert writes(mock) == []


async def test_a05_cli_reconcile_retries_keep_command_capability_identity(owned, monkeypatch, capsys):
    import asyncio

    from bat_agent_connector import cli
    d, tid = owned
    cmd, _ = d.journal.command(tid, "send", SID, {"purpose": "goose:one", "prompt_sha256": "d" * 64}, "old-send")
    d.journal.command_status(cmd["command_id"], "uncertain")
    d.journal.change(tid, "uncertain")
    server = await asyncio.start_server(d._handle, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    monkeypatch.setenv("BATC_TASK_URL", f"http://127.0.0.1:{port}/rpc")
    monkeypatch.setenv("BATC_TASK_ADMIN_TOKEN_FILE", str(d.admin_token_path))
    argv = ["--json", "task-reconcile", "--task-id", tid, "--command-id", cmd["command_id"],
            "--key", "reconcile", "--outcome", "not_delivered", "--actor", "operator", "--source", "ticket-1", "--evidence", "checked exact turn"]
    try:
        assert await asyncio.to_thread(cli.main, argv) == 0
        first = json.loads(capsys.readouterr().out)
        assert await asyncio.to_thread(cli.main, argv) == 0
        assert json.loads(capsys.readouterr().out) == first
        assert len(d.journal.reconciliations(tid)) == 1
        changed = ["delivered" if arg == "not_delivered" else arg for arg in argv]
        assert await asyncio.to_thread(cli.main, changed) == 1
        assert "IDEMPOTENCY_CONFLICT" in capsys.readouterr().err
        assert d.journal.db.execute("SELECT count(*) FROM operations").fetchone()[0] == 1
    finally:
        server.close()
        await server.wait_closed()


async def test_a07_reconcile_rechecks_control_version_before_effect(owned, mock, monkeypatch):
    d, tid = owned
    cmd, _ = d.journal.command(tid, "send", SID, {"purpose": "goose:one", "prompt_sha256": "d" * 64}, "old-send")
    d.journal.command_status(cmd["command_id"], "uncertain")
    d.journal.change(tid, "uncertain")
    cap = d.journal.issue_reconcile_capability(tid, cmd["command_id"])

    async def idle(*args, **kwargs):
        return {"streaming": False, "pending": None}

    async def late_pause(*args, **kwargs):
        d.journal.pause(tid)
        return {"agent_kind": "claude", "before_was_empty": True}
    monkeypatch.setattr(d.adapter, "read", idle)
    monkeypatch.setattr(d.adapter, "prepare_send", late_pause)
    params = {"task_id": tid, "command_id": cmd["command_id"], "outcome": "not_delivered", "actor": "operator",
              "source": "ticket-1", "evidence": "checked exact turn", "next_prompt": "new instruction",
              "idempotency_key": "reconcile", "control_version": 0}
    principal = d.capability_principal(cap, "task.command.reconcile", params, "reconcile")
    with pytest.raises(OperationError, match="CONTROL_VERSION_CONFLICT"):
        await call_owned(d, "work_reconcile", params, principal=principal)
    assert d.journal.authorize_reconcile_capability(cap, tid, cmd["command_id"])
    assert d.journal.command_get(cmd["command_id"])["status"] == "uncertain"
    assert d.journal.reconciliations(tid) == []
    assert not writes(mock)


@pytest.mark.parametrize("action,params", [("session.send", {"text": "one instruction", "step_id": "one"}),
                                         ("task.verify", {}), ("task.request_ted", {"reason": "decision"})])
async def test_a07_api_actor_name_cannot_impersonate_task_capability(owned, mock, action, params):
    d, tid = owned
    actor = "task:" + tid + ":pretend"
    token = api_auth.issue(d.journal.db, actor, ["operate"])
    principal = api_auth.authenticate(d.journal.db, token, d._admin_token)
    with pytest.raises(OperationError, match="FORBIDDEN"):
        d.ops.create(principal, action=action, target={"task_id": tid}, params=params, idempotency_key="attempt")
    assert d.journal.db.execute("SELECT count(*) FROM operations").fetchone()[0] == 0
    assert not writes(mock)


def admission_control(d, tid, mock, kind, *, scoped=False, pre=None, key="admission"):
    if kind == "answer":
        mock.states[SID]["pendingAskUser"] = {"toolUseId": "ask-1", "questions": [{"question": "Choice?"}]}
    elif kind == "permission":
        mock.states[SID]["pendingPermission"] = {"toolUseId": "permission-1"}
    elif kind == "interrupt":
        mock.metas[SID]["isStreaming"] = mock.states[SID]["isStreaming"] = True
    action = "session.answer" if kind == "permission" else "session." + kind
    params = {"send": {"text": "one instruction"}, "answer": {"answers": ["yes"], "tool_use_id": "ask-1"},
              "permission": {"permission": "allow", "tool_use_id": "permission-1"}, "interrupt": {"mode": "soft"}}[kind]
    if scoped:
        params["step_id"] = "one"
    principal = capability_caller(d, tid) if scoped else OWNER
    request = {"action": action, "target": {"task_id": tid} if scoped else {"host": "h1", "session_id": SID},
               "params": params, "preconditions": pre or {}, "idempotency_key": key}
    return principal, request, d.ops.create(principal, **request)[0]


@pytest.mark.parametrize("kind", ["send", "answer", "interrupt", "permission"])
@pytest.mark.parametrize("raced", [False, True], ids=["unraced", "pause_resume"])
@pytest.mark.parametrize("explicit", [False, True], ids=["omitted_version", "explicit_version"])
async def test_a07_session_operation_keeps_admission_incarnation(owned, mock, kind, raced, explicit):
    d, tid = owned
    principal, request, op = admission_control(d, tid, mock, kind, pre={"control_version": 0} if explicit else {})
    binding = {"task_id": tid, "control_version": 0, "host": "h1", "session_id": SID, "role": "lead"}
    assert op["external_refs"]["admission_binding"] == binding
    assert op["preconditions"] == request["preconditions"]  # server metadata never changes the hashed request
    if raced:
        d.journal.pause(tid)
        d.journal.resume(tid)
    snapshot = task_effect_snapshot(d, tid)
    await settle_operations(d.ops)
    result = d.ops.get(op["operation_id"])
    assert result["status"] == ("failed" if raced else "succeeded")
    assert result["external_refs"]["admission_binding"] == binding
    assert len(writes(mock)) == int(not raced)
    if raced:
        assert result["error_code"] == "CONTROL_VERSION_CONFLICT"
        assert task_effect_snapshot(d, tid) == snapshot
        assert not d.journal.commands(tid)
    else:
        d.journal.pause(tid)
        d.journal.resume(tid)
    replay_snapshot = task_effect_snapshot(d, tid)
    replay, created = d.ops.create(principal, **request)
    assert not created and replay["operation_id"] == op["operation_id"]
    await settle_operations(d.ops)
    assert d.ops.get(op["operation_id"]) == result
    assert task_effect_snapshot(d, tid) == replay_snapshot
    assert len(writes(mock)) == int(not raced)
    with pytest.raises(OperationError, match="IDEMPOTENCY_CONFLICT"):
        d.ops.create(principal, **{**request, "preconditions": {"control_version": 0 if not explicit else 2}})
    with pytest.raises(OperationError, match="CONTROL_VERSION_CONFLICT"):
        d.ops.create(principal, **{**request, "preconditions": {"control_version": 0}, "idempotency_key": "stale"})


@pytest.mark.parametrize("scoped", [False, True], ids=["session_target", "task_target"])
async def test_a07_send_refuses_replaced_admission_session(owned, mock, scoped):
    d, tid = owned
    principal, request, op = admission_control(d, tid, mock, "send", scoped=scoped)
    replacement = "sess-codex-0002"
    d.journal.change(tid, "accepted", fields={"session_id": replacement})
    adopt(replacement, task_id=tid, role="lead", agent_preset="codex-agent")
    snapshot = task_effect_snapshot(d, tid)
    await settle_operations(d.ops)
    result = d.ops.get(op["operation_id"])
    assert result["status"] == "failed" and result["error_code"] == "TASK_BINDING_MISMATCH"
    assert not d.journal.commands(tid) and not writes(mock)
    assert task_effect_snapshot(d, tid) == snapshot
    replay, created = d.ops.create(principal, **request)
    assert not created and d.ops.get(replay["operation_id"]) == result


@pytest.mark.parametrize("action", ["task.verify", "task.request_ted", "task.mark_stage", "task.pause",
                                    "task.resume", "task.command.reconcile", "task.submit"])
async def test_a07_task_actions_keep_admission_version(owned, mock, action):
    d, tid = owned
    principal = OWNER
    target, params = {"task_id": tid}, {}
    if action == "task.mark_stage":
        finish_verified_task(d, tid)
        params = {"stage": "adopted", "ref": "commit-a"}
    elif action == "task.request_ted":
        params = {"reason": "decision"}
    elif action == "task.pause":
        d.journal.pause(tid)  # a queued pause must not undo a later resume
    elif action == "task.command.reconcile":
        cmd, _ = d.journal.command(tid, "send", SID, {"purpose": "goose:one"}, "pending")
        d.journal.command_status(cmd["command_id"], "uncertain")
        d.journal.change(tid, "uncertain")
        target["command_id"] = cmd["command_id"]
        cap = d.journal.issue_reconcile_capability(tid, cmd["command_id"])
        principal = d.capability_principal(cap, action, target, "admission")
        params = {"outcome": "not_delivered", "actor": "operator", "source": "ticket-1", "evidence": "checked"}
    elif action == "task.submit":
        target = {"host": "h1", "workspace": "demo-project"}
        params = {"project": "p", "original_words": "follow up", "continuation": True, "parent_task_id": tid}
    request = {"action": action, "target": target, "params": params, "idempotency_key": "admission"}
    version = d.journal.get(tid)["control_version"]
    op, _ = d.ops.create(principal, **request)
    assert op["external_refs"]["admission_binding"]["control_version"] == version
    if action == "task.pause":
        d.journal.resume(tid)
    elif action == "task.mark_stage":
        # Model a corrected terminal incarnation; terminal pause/resume intentionally makes no change.
        d.journal.db.execute("UPDATE tasks SET control_version=control_version+1 WHERE task_id=?", (tid,))
    else:
        d.journal.pause(tid)
    snapshot = task_effect_snapshot(d, tid)
    await settle_operations(d.ops)
    result = d.ops.get(op["operation_id"])
    assert result["status"] == "failed" and result["error_code"] == "CONTROL_VERSION_CONFLICT"
    assert result["steps"] == []  # no effect intent, command, or task write
    assert task_effect_snapshot(d, tid) == snapshot and not writes(mock)
    replay, created = d.ops.create(principal, **request)
    assert not created and d.ops.get(replay["operation_id"]) == result
    if action == "task.command.reconcile":
        assert d.journal.authorize_reconcile_capability(cap, tid, cmd["command_id"])


async def test_a05_admission_binding_is_atomic_with_operation_row(owned, mock, monkeypatch):
    d, tid = owned
    def fail_event(*args, **kwargs):
        raise RuntimeError("crash before admission commits")
    with monkeypatch.context() as patch:
        patch.setattr(d.journal, "api_event", fail_event)
        with pytest.raises(RuntimeError, match="admission commits"):
            admission_control(d, tid, mock, "send")
    assert d.journal.db.execute("SELECT count(*) FROM operations").fetchone()[0] == 0
    _, _, op = admission_control(d, tid, mock, "send")
    assert op["external_refs"]["admission_binding"]["control_version"] == 0


@pytest.mark.parametrize("kind,scoped", [("send", False), ("answer", False), ("interrupt", False), ("send", True)])
async def test_a07_preupgrade_operation_without_admission_binding_keeps_old_behavior(owned, mock, kind, scoped):
    d, tid = owned
    _, _, op = admission_control(d, tid, mock, kind, scoped=scoped)
    d.journal.db.execute("UPDATE operations SET external_refs=NULL WHERE operation_id=?", (op["operation_id"],))
    d.journal.pause(tid)
    d.journal.resume(tid)
    await settle_operations(d.ops)
    assert d.ops.get(op["operation_id"])["status"] == "succeeded"
    assert len(writes(mock)) == len(d.journal.commands(tid)) == 1


@pytest.mark.parametrize("entry", ["rpc", "mcp", "cli"])
async def test_a05_legacy_task_door_records_admission_binding(owned, entry):
    d, tid = owned
    result = await call_owned(d, "work_pause", {"task_id": tid, "entry": entry, "idempotency_key": "legacy-door"})
    op = d.ops.get(result["operation_id"])
    assert op["entry"] == entry and op["preconditions"] == {}
    assert op["external_refs"]["admission_binding"] == {"task_id": tid, "control_version": 0}
    d.journal.resume(tid)
    assert await call_owned(d, "work_pause", {"task_id": tid, "entry": entry, "idempotency_key": "legacy-door"}) == result


@pytest.mark.parametrize("scoped", [False, True], ids=["session_target", "task_target"])
async def test_a07_admission_incarnation_survives_restart(owned, mock, scoped):
    d, tid = owned
    principal, request, op = admission_control(d, tid, mock, "send", scoped=scoped)
    d.journal.pause(tid)
    d.journal.resume(tid)
    snapshot = task_effect_snapshot(d, tid)
    async with restarted_daemon(d) as restarted:
        assert restarted.ops.get(op["operation_id"])["external_refs"] == op["external_refs"]
        await settle_operations(restarted.ops)
        refused = restarted.ops.get(op["operation_id"])
        assert refused["status"] == "failed" and refused["error_code"] == "CONTROL_VERSION_CONFLICT"
        assert task_effect_snapshot(restarted, tid) == snapshot and not writes(mock)
        replay, created = restarted.ops.create(principal, **request)
        assert not created and restarted.ops.get(replay["operation_id"]) == refused
        _, _, fresh = admission_control(restarted, tid, mock, "send", scoped=scoped, key="new-incarnation")
        await settle_operations(restarted.ops)
        assert restarted.ops.get(fresh["operation_id"])["status"] == "succeeded"
        assert len(writes(mock)) == len(restarted.journal.commands(tid)) == 1


@pytest.mark.parametrize("action", ["task.verify", "task.pause", "task.command.reconcile"])
async def test_a07_task_session_actions_refuse_replaced_admission_role(owned, mock, action):
    d, tid = owned
    target = {"task_id": tid}
    params = {"abort_current": True} if action == "task.pause" else {}
    principal = OWNER
    if action == "task.command.reconcile":
        reviewer = "sess-codex-0002"
        adopt(reviewer, task_id=tid, role="reviewer", agent_preset="codex-agent")
        d.journal.change(tid, "accepted", fields={"reviewer_session_id": reviewer})
        cmd, _ = d.journal.command(tid, "send", reviewer, {}, "reviewer-send")
        d.journal.command_status(cmd["command_id"], "uncertain")
        d.journal.change(tid, "uncertain")
        target["command_id"] = cmd["command_id"]
        cap = d.journal.issue_reconcile_capability(tid, cmd["command_id"])
        principal = d.capability_principal(cap, action, target, "role")
        params = {"outcome": "not_delivered", "actor": "operator", "source": "ticket-1", "evidence": "checked"}
    op, _ = d.ops.create(principal, action=action, target=target, params=params, idempotency_key="role")
    binding = op["external_refs"]["admission_binding"]
    assert binding["role"] == ("reviewer" if action == "task.command.reconcile" else "lead")
    if action == "task.command.reconcile":
        d.journal.change(tid, "uncertain", fields={"reviewer_session_id": None})
    else:
        d.journal.change(tid, "accepted", fields={"session_id": "sess-codex-0002"})
        adopt("sess-codex-0002", task_id=tid, role="lead", agent_preset="codex-agent")
    snapshot = task_effect_snapshot(d, tid)
    await settle_operations(d.ops)
    refused = d.ops.get(op["operation_id"])
    assert refused["status"] == "failed" and refused["error_code"] == "TASK_BINDING_MISMATCH"
    assert refused["steps"] == [] and not writes(mock)
    assert task_effect_snapshot(d, tid) == snapshot


@pytest.mark.parametrize("kind", ["send", "answer", "interrupt"])
async def test_a07_readback_of_old_incarnation_does_not_dispatch_again(owned, mock, monkeypatch, kind):
    d, tid = owned
    mock.echo_sends = True
    _, _, op = admission_control(d, tid, mock, kind)
    client = d.fleet.client("h1")
    original = client._roundtrip
    async def lose_reply(frame, timeout, **kwargs):
        result = await original(frame, timeout, **kwargs)
        if frame["channel"] in WRITE_CHANNELS:
            if kind == "interrupt":
                mock.metas[SID]["isStreaming"] = mock.states[SID]["isStreaming"] = False
            raise ConnectionLost("BAT accepted, reply lost")
        return result
    with monkeypatch.context() as patch:
        patch.setattr(client, "_roundtrip", lose_reply)
        await settle_operations(d.ops)
    assert d.ops.get(op["operation_id"])["status"] == "uncertain"
    d.journal.pause(tid)
    d.journal.resume(tid)
    d.journal.db.execute("UPDATE operations SET next_run_at=0 WHERE operation_id=?", (op["operation_id"],))
    await settle_operations(d.ops)
    assert d.ops.get(op["operation_id"])["status"] == "succeeded"
    await d.coordinator.tick(tid)
    assert d.journal.commands(tid)[0]["status"] == ("accepted" if kind == "send" else "settled")
    assert len(writes(mock)) == len(d.journal.commands(tid)) == 1


def pending_answer(mock, variant):
    mock.states[SID]["pendingAskUser"] = {"toolUseId": "ask-1", "questions": [{"question": "Choice?"}]}
    mock.states[SID]["pendingPermission"] = {"toolUseId": "permission-1", "toolName": "Bash", "input": {}}
    if variant == "ask_user":
        return {"answers": ["yes"]}, "pendingAskUser", "ask-1", "claude:resolve-ask-user"
    return {"permission": "allow"}, "pendingPermission", "permission-1", "claude:resolve-permission"


@pytest.mark.parametrize("variant", ["ask_user", "permission"])
async def test_a07_legacy_answer_resolves_prompt_before_frame_and_reconciles_after_restart(owned, mock, monkeypatch, variant):
    d, tid = owned
    params, _, prompt_id, channel = pending_answer(mock, variant)
    prior = d.journal.get(tid)
    client = d.fleet.client("h1")
    original = client._roundtrip

    async def lose_reply(frame, timeout, **kwargs):
        if frame["channel"] == channel:
            command = d.journal.commands(tid)[0]
            payload = json.loads(command["payload"])
            assert payload["tool_use_id"] == frame["params"]["toolUseId"] == prompt_id
            assert payload["tool_use_id_source"] == "service"
            assert not d.journal.db.in_transaction  # identity is committed before the BAT frame
        result = await original(frame, timeout, **kwargs)
        if frame["channel"] == channel:
            raise ConnectionLost("BAT accepted the answer, reply lost")
        return result

    with monkeypatch.context() as patch:
        patch.setattr(client, "_roundtrip", lose_reply)
        with pytest.raises(ConnectionLost):
            await service.session_answer(d.fleet, "h1", SID, confirm=True, **params)
    command = d.journal.commands(tid)[0]
    assert command["status"] == d.journal.get(tid)["state"] == "uncertain"
    assert "tool_use_id" not in params
    assert len(writes(mock)) == 1
    async with restarted_daemon(d) as restarted:
        assert restarted.journal.command_get(command["command_id"])["payload"] == command["payload"]
        client = restarted.fleet.client("h1")
        original_read = client._roundtrip
        async def unreadable(frame, timeout, **kwargs):
            result = await original_read(frame, timeout, **kwargs)
            return {**result, "result": None} if frame["channel"] == "claude:get-session-state" else result
        with monkeypatch.context() as patch:
            patch.setattr(client, "_roundtrip", unreadable)
            assert (await restarted.coordinator.tick(tid))["state"] == "uncertain"
        assert restarted.journal.command_get(command["command_id"])["status"] == "uncertain"
        assert len(writes(mock)) == len(restarted.journal.commands(tid)) == 1
        task = await restarted.coordinator.tick(tid)
        assert task["state"] == "running" and task["control_version"] == prior["control_version"]
        assert restarted.journal.command_get(command["command_id"])["status"] == "settled"
        assert len(writes(mock)) == len(restarted.journal.commands(tid)) == 1
        await service.session_interrupt(restarted.fleet, "h1", SID, confirm=True)
        assert len(writes(mock)) == len(restarted.journal.commands(tid)) == 2


@pytest.mark.parametrize("variant", ["ask_user", "permission"])
async def test_a07_legacy_answer_prompt_change_rejects_command_before_frame(owned, mock, monkeypatch, variant):
    d, tid = owned
    params, field, prompt_id, _ = pending_answer(mock, variant)
    prior = d.journal.get(tid)
    original = d.journal.command
    def replace_prompt(*args, **kwargs):
        command = original(*args, **kwargs)
        mock.states[SID][field] = {**mock.states[SID][field], "toolUseId": "replacement-prompt"}
        return command
    monkeypatch.setattr(d.journal, "command", replace_prompt)
    message = "tool_use_id does not match the pending " + ("question" if variant == "ask_user" else "permission request")
    with pytest.raises(WriteRefused, match=message):
        await service.session_answer(d.fleet, "h1", SID, confirm=True, **params)
    command = d.journal.commands(tid)[0]
    assert command["status"] == "rejected" and json.loads(command["payload"])["tool_use_id"] == prompt_id
    assert d.journal.get(tid) == prior and not writes(mock)
    assert mock.states[SID][field]["toolUseId"] == "replacement-prompt"


@pytest.mark.parametrize("variant", ["ask_user", "permission"])
@pytest.mark.parametrize("state_kind", ["absent", "missing_id", "unreadable", "unloaded"])
async def test_a07_legacy_answer_without_resolvable_prompt_creates_no_command(owned, mock, monkeypatch, variant, state_kind):
    d, tid = owned
    params, field, _, _ = pending_answer(mock, variant)
    if state_kind == "absent":
        mock.states[SID][field] = None  # the other kind is pending; never choose that prompt
    elif state_kind == "missing_id":
        del mock.states[SID][field]["toolUseId"]
    elif state_kind == "unloaded":
        mock.metas[SID] = None
    else:
        client = d.fleet.client("h1")
        original = client._roundtrip
        async def unreadable(frame, timeout, **kwargs):
            result = await original(frame, timeout, **kwargs)
            return {**result, "result": None} if frame["channel"] == "claude:get-session-state" else result
        monkeypatch.setattr(client, "_roundtrip", unreadable)
    snapshot = task_effect_snapshot(d, tid)
    message = "session is not loaded on the host; nothing to answer" if state_kind == "unloaded" else (
        "session has no pending " + ("ask-user question" if variant == "ask_user" else "permission request"))
    with pytest.raises(WriteRefused, match=message):
        await service.session_answer(d.fleet, "h1", SID, confirm=True, **params)
    assert task_effect_snapshot(d, tid) == snapshot and not writes(mock)


@pytest.mark.parametrize("variant", ["ask_user", "permission"])
@pytest.mark.parametrize("matches", [False, True], ids=["explicit_mismatch", "explicit_match"])
async def test_a07_explicit_answer_prompt_keeps_existing_behavior(owned, mock, variant, matches):
    d, tid = owned
    params, _, prompt_id, channel = pending_answer(mock, variant)
    params["tool_use_id"] = prompt_id if matches else "other-prompt"
    prior = d.journal.get(tid)
    if matches:
        result = await service.session_answer(d.fleet, "h1", SID, confirm=True, **params)
        assert result["tool_use_id"] == prompt_id and result["channel"] == channel
    else:
        with pytest.raises(WriteRefused, match="tool_use_id does not match"):
            await service.session_answer(d.fleet, "h1", SID, confirm=True, **params)
    command = d.journal.commands(tid)[0]
    payload = json.loads(command["payload"])
    assert payload["tool_use_id"] == params["tool_use_id"] and "tool_use_id_source" not in payload
    assert command["status"] == ("settled" if matches else "rejected")
    assert d.journal.get(tid) == prior and len(writes(mock)) == int(matches)


@pytest.mark.parametrize("variant", ["ask_user", "permission"])
async def test_a07_legacy_mcp_answer_without_prompt_id_uses_owner_resolution(owned, mock, monkeypatch, variant):
    from bat_agent_connector.mcp_server import build_server
    d, tid = owned
    params, pending_field, prompt_id, _ = pending_answer(mock, variant)
    # The authenticated adapter may infer only one unambiguous live prompt.
    other_field = "pendingPermission" if pending_field == "pendingAskUser" else "pendingAskUser"
    mock.states[SID][other_field] = None
    server = await asyncio.start_server(d._handle, "127.0.0.1", 0)
    d._endpoint = f"http://127.0.0.1:{server.sockets[0].getsockname()[1]}/rpc"
    d._write_owner_pointer()
    token = api_auth.issue(d.journal.db, "legacy-mcp-answer", ["operate"])
    monkeypatch.setenv("BATC_API_TOKEN", token)
    monkeypatch.setenv("BATC_TASK_URL", d._endpoint)
    mcp, fleet = build_server(d.fleet.config)
    try:
        result = await mcp.call_tool("session_answer", {"host": "h1", "session_id": SID, "confirm": True, **params})
        assert not result.is_error
        assert json.loads(d.journal.commands(tid)[0]["payload"])["tool_use_id"] == prompt_id
        assert d.journal.commands(tid)[0]["status"] == "settled"
        op = d.ops.list()["operations"][0]
        assert op["actor"] == "legacy-mcp-answer" and op["entry"] == "mcp"
        assert op["external_refs"]["resolved_params"] == {"tool_use_id": prompt_id}
        assert op["external_refs"]["admission_binding"]["task_id"] == tid
        assert len(writes(mock)) == 1
    finally:
        await fleet.close()
        server.close()
        await server.wait_closed()


@pytest.mark.parametrize("kind", ["claude", "codex"])
async def test_a07_permission_mode_identity_survives_lost_reply_without_replay(owned, mock, monkeypatch, kind):
    d, tid = owned
    sid = SID if kind == "claude" else "sess-codex-0002"
    if kind == "codex":
        adopt(sid, task_id=tid, role="lead", agent_preset="codex-agent")
        d.journal.change(tid, "accepted", fields={"session_id": sid})
    client = d.fleet.client("h1")
    original = client._roundtrip
    async def lose_reply(frame, timeout, **kwargs):
        result = await original(frame, timeout, **kwargs)
        if frame["channel"] in WRITE_CHANNELS:
            raise ConnectionLost("permission setting reply lost")
        return result
    with monkeypatch.context() as patch:
        patch.setattr(client, "_roundtrip", lose_reply)
        with pytest.raises(ConnectionLost):
            await lifecycle.session_set_permissions(d.fleet, "h1", sid, confirm=True)
    command = d.journal.commands(tid)[0]
    payload = json.loads(command["payload"])
    assert command["session_id"] == sid and payload["mode"] == "allow_all" and payload["control_version"] == 0
    async with restarted_daemon(d) as restarted:
        assert (await restarted.coordinator.tick(tid))["state"] == "uncertain"
        assert restarted.journal.command_get(command["command_id"])["status"] == "uncertain"
        assert len(writes(mock)) == len(restarted.journal.commands(tid)) == 1


@pytest.mark.parametrize("variant", ["ask_user", "permission"])
async def test_a07_answer_resolution_rechecks_task_gate_before_command(owned, mock, monkeypatch, variant):
    d, tid = owned
    params, _, _, _ = pending_answer(mock, variant)
    client = d.fleet.client("h1")
    original = client._roundtrip
    paused = {}
    async def pause_at_read(frame, timeout, **kwargs):
        result = await original(frame, timeout, **kwargs)
        if frame["channel"] == "claude:get-session-state":
            d.journal.pause(tid)
            paused.update(task_effect_snapshot(d, tid))
        return result
    monkeypatch.setattr(client, "_roundtrip", pause_at_read)
    with pytest.raises(TaskControlRefused, match="CONTROL_VERSION_CONFLICT"):
        await service.session_answer(d.fleet, "h1", SID, confirm=True, **params)
    assert task_effect_snapshot(d, tid) == paused and not writes(mock)


async def test_a07_relay_runtime_command_has_original_send_readback_identity(owned, mock, monkeypatch):
    d, tid = owned
    mock.echo_sends = True
    client = d.fleet.client("h1")
    original = client._roundtrip
    async def lose_reply(frame, timeout, **kwargs):
        result = await original(frame, timeout, **kwargs)
        if frame["channel"] == "claude:send-message":
            raise ConnectionLost("relay reply lost")
        return result
    with monkeypatch.context() as patch:
        patch.setattr(client, "_roundtrip", lose_reply)
        with pytest.raises(ConnectionLost):
            await lifecycle.session_relay(d.fleet, "h1", "exact request", session_id=SID, confirm=True)
    command = d.journal.commands(tid)[0]
    frame = writes(mock)[0]
    payload = json.loads(command["payload"])
    assert payload["prompt_sha256"] == hashlib.sha256(frame["params"]["prompt"].encode()).hexdigest()
    assert payload["before"]["agent_kind"] == "claude"
    assert command["message_id"] == frame["params"]["clientMessageId"]
    async with restarted_daemon(d) as restarted:
        assert (await restarted.coordinator.tick(tid))["state"] == "running"
        assert restarted.journal.command_get(command["command_id"])["status"] == "accepted"
        assert len(writes(mock)) == len(restarted.journal.commands(tid)) == 1


@pytest.mark.parametrize("door", ["legacy", "session_operation", "task_operation"])
@pytest.mark.parametrize("refusal", ["streaming", "version_changed"])
async def test_a07_refusal_after_resume_rejects_only_the_unsent_command(owned, mock, monkeypatch, door, refusal):
    """A05/A07: a pre-send refusal is definitive, preserves the task and replays with the same key."""
    d, tid = owned
    mock.metas[SID] = None
    expected = d.journal.get(tid)
    code = "REFUSED" if refusal == "streaming" or door == "task_operation" else "CONTROL_VERSION_CONFLICT"
    client = d.fleet.client("h1")
    original = client._roundtrip

    async def resume_then_refuse(frame, timeout, **kwargs):
        nonlocal expected
        result = await original(frame, timeout, **kwargs)
        if frame["channel"] == "claude:client-resume":
            if refusal == "streaming":
                mock.metas[SID]["isStreaming"] = True
            else:
                d.journal.pause(tid)
                d.journal.resume(tid)
                expected = d.journal.get(tid)
        return result

    monkeypatch.setattr(client, "_roundtrip", resume_then_refuse)
    if door == "legacy":
        with pytest.raises(WriteRefused, match="currently streaming" if refusal == "streaming" else code):
            await service.session_send(d.fleet, "h1", SID, "one instruction", confirm=True)
    else:
        principal, request, op = admission_control(d, tid, mock, "send", scoped=door == "task_operation")
        await settle_operations(d.ops)
        result = d.ops.get(op["operation_id"])
        assert result["status"] == "failed" and result["error_code"] == code
        step = "task_dispatch" if door == "task_operation" else "send"
        assert next(s for s in result["steps"] if s["name"] == step)["status"] == "failed"
        replay, created = d.ops.create(principal, **request)
        assert not created and replay["operation_id"] == op["operation_id"]
        await settle_operations(d.ops)
        assert d.ops.get(op["operation_id"]) == result
    commands = d.journal.commands(tid)
    assert len(commands) == 1 and commands[0]["status"] == "rejected"
    assert d.journal.get(tid) == expected
    assert [r["channel"] for r in writes(mock)] == ["claude:client-resume"]


def configure_verification_tick(d, tid, monkeypatch):
    monkeypatch.setattr(d.goose, "config", replace(d.goose.config, enabled=True))
    d.coordinator.verification_quiet_s = 0
    d.journal.change(tid, "verifying")

    async def idle(*args):
        return {"streaming": False, "pending": None}

    async def candidate(*args):
        return {"clean": True, "candidate_commit": "a" * 40, "tree_hash": "b" * 40}

    async def head(*args):
        return "a" * 40

    async def clean(*args):
        return False

    monkeypatch.setattr(d.adapter, "read", idle)
    monkeypatch.setattr(d.adapter, "candidate_identity", candidate)
    monkeypatch.setattr(lifecycle, "_candidate_head", head)
    monkeypatch.setattr(lifecycle, "_git_dirty", clean)


@pytest.mark.parametrize("control", ["pause", "version", "owner", "binding"])
@pytest.mark.parametrize("boundary", ["observe", "install", "rerun"])
async def test_a07_daemon_verification_control_cancellation_preserves_task_and_restarts(
        owned, mock, monkeypatch, control, boundary):
    """A07: real adapter and daemon tick discard an obsolete trusted run, including dependency retries."""
    d, tid = owned
    configure_verification_tick(d, tid, monkeypatch)
    entered, release = asyncio.Event(), asyncio.Event()
    runs, records = [], []
    original_record = verification.record

    async def observe(task, cwd, *, before_run=None):
        if before_run:
            before_run()
        runs.append(task["control_version"])
        run = len(runs)
        if boundary == "observe" and run == 1 or boundary == "rerun" and run == 2:
            entered.set()
            await release.wait()
        return {**trusted_evidence(), "exit_code": int(boundary != "observe" and run == 1)}

    async def dependencies(task, cwd, *, before_run=None):
        if before_run:
            before_run()
        if boundary == "install":
            entered.set()
            await release.wait()
        return {"ok": True, "reason": "installed", "lockfile": "uv.lock"}

    async def missing(*args):
        return {"kind": "missing_dependencies"}

    def record(*args, **kwargs):
        records.append(kwargs)
        return original_record(*args, **kwargs)

    monkeypatch.setattr(d.adapter.verifier, "observe", observe)
    monkeypatch.setattr(d.adapter.verifier, "install_dependencies", dependencies)
    monkeypatch.setattr(d.adapter, "verification_failure", missing)
    monkeypatch.setattr(verification, "record", record)
    tick = asyncio.create_task(d._tick_task(tid))
    try:
        await asyncio.wait_for(entered.wait(), 5)
        if control in {"pause", "version"}:
            await call_owned(d, "work_pause" if control == "pause" else "work_resume",
                         {"task_id": tid, "idempotency_key": "cancel-verification"})
        elif control == "owner":
            monkeypatch.setattr(d.journal, "owner_valid", lambda: False)
        else:
            d.journal.db.execute("UPDATE tasks SET session_id='replacement' WHERE task_id=?", (tid,))
        snapshot = task_effect_snapshot(d, tid)
        before_records = len(records)
    finally:
        release.set()
        await tick
    assert d.journal.get(tid)["state"] == "verifying"
    assert d.journal.get(tid)["paused"] == int(control == "pause")
    assert task_effect_snapshot(d, tid) == snapshot
    assert len(records) == before_records == int(boundary != "observe")
    assert not writes(mock)
    if control == "pause":
        with monkeypatch.context() as patch:
            patch.setattr(d, "verification_remaining", lambda task: 0)
            await d._tick_task(tid)  # a paused task has no verification deadline
            assert task_effect_snapshot(d, tid) == snapshot
        await call_owned(d, "work_resume", {"task_id": tid, "idempotency_key": "restart-verification"})
    elif control == "owner":
        monkeypatch.setattr(d.journal, "owner_valid", lambda: True)
    elif control == "binding":
        d.journal.db.execute("UPDATE tasks SET session_id=? WHERE task_id=?", (SID, tid))
    if control in {"owner", "binding"}:
        await call_owned(d, "work_resume", {"task_id": tid, "idempotency_key": "restart-verification"})
    await d._tick_task(tid)
    assert d.journal.get(tid)["state"] == "done"
    assert len(records) == before_records + 1
    assert runs[-1] == d.journal.get(tid)["control_version"]
    assert len(runs) == (3 if boundary == "rerun" else 2)
    assert not writes(mock)


async def test_a07_genuine_verifier_error_still_needs_ted(owned, mock, monkeypatch):
    d, tid = owned
    configure_verification_tick(d, tid, monkeypatch)

    async def broken(*args, **kwargs):
        raise ValueError("trusted runner failed")

    monkeypatch.setattr(d.adapter.verifier, "observe", broken)
    await d._tick_task(tid)
    task = d.journal.get(tid)
    assert task["state"] == "needs_ted" and task["result"] == "ValueError"
    assert not d.journal.observed_verification(tid) and not writes(mock)
    assert d.journal.events(tid)[-1]["kind"] == "verification_error"


@pytest.mark.parametrize("role", ["lead", "reviewer"])
@pytest.mark.parametrize("control", ["pause", "version", "owner", "binding"])
async def test_a07_tick_start_control_refusal_preserves_task_control(owned, mock, monkeypatch, role, control):
    """A07: start adapters must not turn a pre-frame control refusal into an uncertain task."""
    d, tid = owned
    configure_verification_tick(d, tid, monkeypatch)
    if role == "lead":
        d.journal.db.execute("UPDATE tasks SET state='queued',session_id=NULL WHERE task_id=?", (tid,))
    entered, release = asyncio.Event(), asyncio.Event()

    async def starting(task_id):
        return await d.coordinator._start(d.journal.get(task_id), role=role)

    async def start(task, **kwargs):
        entered.set()
        await release.wait()
        task_control.check_incarnation(d.journal, task)
        pytest.fail("a changed task must not start the reserved session")

    monkeypatch.setattr(d.coordinator, "_tick", starting)
    monkeypatch.setattr(d.adapter, "start", start)
    tick = asyncio.create_task(d._tick_task(tid))
    try:
        await asyncio.wait_for(entered.wait(), 5)
        if control in {"pause", "version"}:
            await call_owned(d, "work_pause" if control == "pause" else "work_resume",
                         {"task_id": tid, "idempotency_key": "cancel-start"})
        elif control == "owner":
            monkeypatch.setattr(d.journal, "owner_valid", lambda: False)
        else:
            d.journal.db.execute("UPDATE tasks SET session_id='replacement' WHERE task_id=?", (tid,))
        current = d.journal.get(tid)
    finally:
        release.set()
        await tick
    result = d.journal.get(tid)
    assert result["paused"] == current["paused"] and result["control_version"] == current["control_version"]
    assert result["state"] == ("dispatching" if control == "owner" else "verifying" if role == "reviewer" else "queued")
    assert d.journal.commands(tid)[0]["status"] == ("intent" if control == "owner" else "cancelled")
    assert not writes(mock)


@pytest.mark.parametrize("door", ["legacy", "session_operation", "task_operation", "coordinator"])
@pytest.mark.parametrize("error", [ConnectionLost, InvokeTimeout])
async def test_a07_resume_transport_loss_rejects_command_without_uncertain_task(owned, mock, monkeypatch, door, error):
    """A05/A07: only resume reached BAT; the rejected send and its failed operation never replay a frame."""
    d, tid = owned
    mock.metas[SID] = None
    prior = d.journal.get(tid)
    client = d.fleet.client("h1")
    original = client._roundtrip

    async def lose_resume_reply(frame, timeout, **kwargs):
        result = await original(frame, timeout, **kwargs)
        if frame["channel"] == "claude:client-resume":
            raise error("resume reply lost before send-message")
        return result

    with monkeypatch.context() as patch:
        patch.setattr(client, "_roundtrip", lose_resume_reply)
        if door == "legacy":
            with pytest.raises(error):
                await service.session_send(d.fleet, "h1", SID, "one instruction", confirm=True)
        elif door == "coordinator":
            result = await d.coordinator._send(prior, SID, "one instruction", "lead:followup")
            assert result["state"] == "needs_ted" and "BAT_ERROR" in result["result"]
        else:
            principal, request, op = admission_control(d, tid, mock, "send", scoped=door == "task_operation")
            await settle_operations(d.ops)
            result = d.ops.get(op["operation_id"])
            assert result["status"] == "failed" and result["error_code"] == "BAT_ERROR"
            step = "task_dispatch" if door == "task_operation" else "send"
            assert next(s for s in result["steps"] if s["name"] == step)["status"] == "failed"
            replay, created = d.ops.create(principal, **request)
            assert not created and replay["operation_id"] == op["operation_id"]
            await settle_operations(d.ops)
            assert d.ops.get(op["operation_id"]) == result
    commands = d.journal.commands(tid)
    assert len(commands) == 1 and commands[0]["status"] == "rejected"
    if door == "coordinator":
        assert d.journal.get(tid)["state"] == "needs_ted"
        assert d.journal.get(tid)["control_version"] == prior["control_version"]
    else:
        assert d.journal.get(tid) == prior
    assert [r["channel"] for r in writes(mock)] == ["claude:client-resume"]
    if door == "coordinator":
        return  # the user must explicitly intervene; a terminal local refusal does not permit another control.
    # The failed resume must not leave a pending task command blocking later controls.
    await service.session_interrupt(d.fleet, "h1", SID, confirm=True)
    assert d.journal.commands(tid)[-1]["status"] == "settled"


@pytest.mark.parametrize("operation", [False, True], ids=["legacy", "session_operation"])
async def test_a07_send_reply_loss_after_resume_uses_original_readback(owned, mock, monkeypatch, operation):
    d, tid = owned
    prior = d.journal.change(tid, "running")
    mock.metas[SID] = None
    mock.echo_sends = True
    # A real resume rehydrates the transcript the coordinator read before dispatch.
    mock.states[SID]["messages"] = list(mock.archives[SID])
    client = d.fleet.client("h1")
    original = client._roundtrip

    async def lose_send_reply(frame, timeout, **kwargs):
        result = await original(frame, timeout, **kwargs)
        if frame["channel"] == "claude:send-message":
            raise ConnectionLost("send reply lost after resume")
        return result

    with monkeypatch.context() as patch:
        patch.setattr(client, "_roundtrip", lose_send_reply)
        if operation:
            principal, request, op = admission_control(d, tid, mock, "send")
            await settle_operations(d.ops)
            result = d.ops.get(op["operation_id"])
            assert result["status"] == "uncertain"
            assert next(s for s in result["steps"] if s["name"] == "send")["status"] == "uncertain"
        else:
            with pytest.raises(ConnectionLost):
                await service.session_send(d.fleet, "h1", SID, "one instruction", confirm=True)
    command = d.journal.commands(tid)[0]
    assert command["status"] == d.journal.get(tid)["state"] == "uncertain"
    if operation:
        d.journal.db.execute("UPDATE operations SET next_run_at=0 WHERE operation_id=?", (op["operation_id"],))
        await settle_operations(d.ops)
        assert d.ops.get(op["operation_id"])["status"] == "succeeded"
        assert d.journal.command_get(command["command_id"])["status"] == "uncertain"
    async with restarted_daemon(d) as restarted:
        task = await restarted.coordinator.tick(tid)
        assert task["state"] == prior["state"] and task["control_version"] == prior["control_version"]
        assert restarted.journal.command_get(command["command_id"])["status"] == "accepted"
        assert len(restarted.journal.commands(tid)) == 1
        assert [r["channel"] for r in writes(mock)] == ["claude:client-resume", "claude:send-message"]


@pytest.mark.parametrize("operation", [False, True], ids=["legacy", "session_operation"])
@pytest.mark.parametrize("stale", ["paused", "version_changed"])
async def test_a07_preliminary_resume_checks_task_binding_without_sending(owned, mock, monkeypatch, operation, stale):
    d, tid = owned
    mock.metas[SID] = None
    client = d.fleet.client("h1")
    original = client._invoke_checked
    expected = {}
    code = "TASK_PAUSED" if stale == "paused" else "CONTROL_VERSION_CONFLICT"

    async def change_before_resume(channel, *args, **kwargs):
        if channel == "claude:client-resume":
            if stale == "paused":
                d.journal.db.execute("UPDATE tasks SET paused=1 WHERE task_id=?", (tid,))
            else:
                d.journal.pause(tid)
                d.journal.resume(tid)
            expected.update(d.journal.get(tid))
        return await original(channel, *args, **kwargs)

    monkeypatch.setattr(client, "_invoke_checked", change_before_resume)
    if operation:
        _, _, op = admission_control(d, tid, mock, "send")
        await settle_operations(d.ops)
        result = d.ops.get(op["operation_id"])
        assert result["status"] == "failed" and result["error_code"] == code
    else:
        with pytest.raises(TaskControlRefused, match=code):
            await service.session_send(d.fleet, "h1", SID, "one instruction", confirm=True)
    assert d.journal.get(tid) == expected
    commands = d.journal.commands(tid)
    assert len(commands) == 1 and commands[0]["status"] == "rejected"
    assert not writes(mock)


def coordinator_tick_send(d, tid, patch, kind):
    d.journal.db.execute("UPDATE tasks SET engine='rules' WHERE task_id=?", (tid,))
    if kind == "initial_lead":
        # Presence was proved before the runtime unloaded; dispatch still uses the real adapter and mockbat.
        async def present(task, sid):
            return "present"
        patch.setattr(d.adapter, "session_presence", present)
    else:
        d.journal.change(tid, "running", fields={"turn_marker": "previous-turn"})
        async def completed_turn(task, sid, marker):
            return {"streaming": False, "turn_started": True, "turn_done": True,
                    "turn_attribution": "correlated", "messages": [
                        {"role": "assistant", "text": "BAT-STATUS: CONTINUE"}]}
        patch.setattr(d.adapter, "read", completed_turn)


@pytest.mark.parametrize("kind", ["initial_lead", "followup"])
@pytest.mark.parametrize("error,code", [(ConnectionLost, "BAT_ERROR"), (InvokeTimeout, "BAT_ERROR"),
                                       (InvokeError, "BAT_ERROR"), (OSError, "INTERNAL")])
async def test_a07_daemon_tick_handles_pre_frame_send_failure_without_uncertainty(owned, mock, monkeypatch, kind, error, code):
    d, tid = owned
    coordinator_tick_send(d, tid, monkeypatch, kind)
    mock.metas[SID] = None
    client = d.fleet.client("h1")
    original = client._roundtrip

    async def lose_resume_reply(frame, timeout, **kwargs):
        result = await original(frame, timeout, **kwargs)
        if frame["channel"] == "claude:client-resume":
            raise error("resume failed before the command frame")
        return result

    monkeypatch.setattr(client, "_roundtrip", lose_resume_reply)
    await d._tick_task(tid)
    task = d.journal.get(tid)
    assert task["state"] == "needs_ted" and code in task["result"]
    commands = d.journal.commands(tid)
    assert len(commands) == 1 and commands[0]["status"] == "rejected"
    assert json.loads(commands[0]["payload"])["purpose"] == ("lead:initial" if kind == "initial_lead" else "continue")
    events = d.journal.events(tid)
    assert any(e["kind"] == "send_rejected" and json.loads(e["body"]) == {
        "command_id": commands[0]["command_id"], "code": code} for e in events)
    assert not any(json.loads(e["body"]).get("to") == "uncertain" for e in events)
    assert [r["channel"] for r in writes(mock)] == ["claude:client-resume"]
    snapshot = task_effect_snapshot(d, tid)
    await d._tick_task(tid)
    assert task_effect_snapshot(d, tid) == snapshot
    assert [r["channel"] for r in writes(mock)] == ["claude:client-resume"]


@pytest.mark.parametrize("kind", ["initial_lead", "followup"])
@pytest.mark.parametrize("boundary", ["before_resume", "resume_reply_loss", "version_changed"])
async def test_a07_daemon_tick_cancels_pre_frame_send_after_task_control(owned, mock, monkeypatch, kind, boundary):
    d, tid = owned
    coordinator_tick_send(d, tid, monkeypatch, kind)
    mock.metas[SID] = None
    client = d.fleet.client("h1")
    original_checked, original_roundtrip = client._invoke_checked, client._roundtrip
    controlled = {}

    async def change_before_resume(channel, *args, **kwargs):
        if channel == "claude:client-resume":
            controlled.update(d.journal.pause(tid))
        return await original_checked(channel, *args, **kwargs)

    async def change_then_lose_reply(frame, timeout, **kwargs):
        result = await original_roundtrip(frame, timeout, **kwargs)
        if frame["channel"] == "claude:client-resume":
            d.journal.pause(tid)
            if boundary == "version_changed":
                d.journal.resume(tid)
            controlled.update(d.journal.get(tid))
            raise ConnectionLost("resume reply lost after task control")
        return result

    monkeypatch.setattr(client, "_invoke_checked" if boundary == "before_resume" else "_roundtrip",
                        change_before_resume if boundary == "before_resume" else change_then_lose_reply)
    await d._tick_task(tid)
    assert d.journal.get(tid) == controlled and controlled["state"] != "uncertain"
    commands = d.journal.commands(tid)
    assert len(commands) == 1 and commands[0]["status"] == "cancelled"
    assert [r["channel"] for r in writes(mock)] == ([] if boundary == "before_resume" else ["claude:client-resume"])
    if boundary != "version_changed":
        snapshot = task_effect_snapshot(d, tid)
        await d._tick_task(tid)
        assert task_effect_snapshot(d, tid) == snapshot
        assert [r["channel"] for r in writes(mock)] == ([] if boundary == "before_resume" else ["claude:client-resume"])


@pytest.mark.parametrize("presence", ["vanished", "replacement_limit", "unreadable", "paused", "version_changed"])
async def test_a07_daemon_initial_send_failure_preserves_presence_and_control_rules(owned, mock, monkeypatch, presence):
    d, tid = owned
    coordinator_tick_send(d, tid, monkeypatch, "initial_lead")
    if presence == "replacement_limit":
        d.journal.db.execute("UPDATE tasks SET session_replacements=1 WHERE task_id=?", (tid,))
    mock.metas[SID] = None
    probes, controlled = 0, {}
    client = d.fleet.client("h1")
    original = client._roundtrip

    async def lose_resume_reply(frame, timeout, **kwargs):
        result = await original(frame, timeout, **kwargs)
        if frame["channel"] == "claude:client-resume":
            raise ConnectionLost("resume reply lost before prompt")
        return result

    async def final_presence(task, sid):
        nonlocal probes
        probes += 1
        if probes <= 2:
            return "present"
        if presence == "unreadable":
            raise ConnectionLost("presence read failed")
        if presence in {"paused", "version_changed"}:
            d.journal.pause(tid)
            if presence == "version_changed":
                d.journal.resume(tid)
            controlled.update(d.journal.get(tid))
            return "vanished"  # A later pause/version must win over this stale absence proof.
        return "vanished"

    monkeypatch.setattr(client, "_roundtrip", lose_resume_reply)
    monkeypatch.setattr(d.adapter, "session_presence", final_presence)
    await d._tick_task(tid)
    task = d.journal.get(tid)
    commands = d.journal.commands(tid)
    assert probes == 3 and len(commands) == 1
    assert task["state"] != "uncertain"
    if controlled:
        assert task == controlled and commands[0]["status"] == "cancelled"
    else:
        assert commands[0]["status"] == "rejected"
        assert task["state"] == ("queued" if presence == "vanished" else "needs_ted")
        assert task["session_replacements"] == int(presence != "unreadable")
        assert task["session_id"] == (None if presence == "vanished" else SID)
    assert any(e["kind"] == "send_rejected" and json.loads(e["body"])["code"] == "BAT_ERROR"
               for e in d.journal.events(tid))
    assert [r["channel"] for r in writes(mock)] == ["claude:client-resume"]
