from __future__ import annotations

import asyncio
import hashlib
import json
from types import SimpleNamespace

import pytest

from bat_agent_connector import controller_authority as ca
from bat_agent_connector import task_actions, task_control
from bat_agent_connector.api_auth import Principal
from bat_agent_connector.errors import ResourceReadOnly
from bat_agent_connector.operations import OperationError, OperationService
from bat_agent_connector.task_bat import BatTaskAdapter
from bat_agent_connector.task_core import TaskCoordinator
from bat_agent_connector.task_daemon import TaskDaemon
from bat_agent_connector.task_journal import Journal
from tests.test_task_service import FakeBAT

ALICE = Principal("alice", frozenset({"start", "operate", "manage"}))
BOB = Principal("bob", frozenset(), admin=True)


@pytest.fixture
def journal(tmp_path):
    value = Journal(tmp_path / "tasks.db")
    yield value
    value.close()


def submit(journal, principal=ALICE, **kwargs):
    with ca.use(ca.binding(journal.db, principal) if principal else None, journal):
        return journal.submit(project="demo", host="h1", workspace="w1", original_words="Inspect the requested change",
                              idempotency_key=kwargs.pop("idempotency_key", "task-one"), **kwargs)


class RecordingBAT(FakeBAT):
    def __init__(self):
        super().__init__()
        self.authorities = []

    async def start(self, *args, **kwargs):
        self.authorities.append(ca.require())
        return await super().start(*args, **kwargs)

    async def send(self, *args, **kwargs):
        self.authorities.append(ca.require())
        return await super().send(*args, **kwargs)


async def test_background_and_restart_use_original_task_not_inherited_caller(journal):
    task = submit(journal)
    original = task_control.authority(task)
    adapter = RecordingBAT()
    coordinator = TaskCoordinator(journal, adapter)
    with ca.use(ca.binding(journal.db, BOB), journal):
        await asyncio.create_task(coordinator.tick(task["task_id"]))
        assert ca.current()["actor"] == "bob"
    path = journal.path
    journal.close()
    reopened = Journal(path)
    try:
        coordinator = TaskCoordinator(reopened, adapter)
        with ca.use(ca.binding(reopened.db, BOB), reopened):
            await asyncio.create_task(coordinator.tick(task["task_id"]))
        assert adapter.starts and adapter.sends
        assert adapter.authorities == [original, original]
        assert all(task_control.authority(command) == original for command in reopened.commands(task["task_id"]))
        assert reopened.get(task["task_id"])["controller_authority"] == original
    finally:
        reopened.close()


async def test_legacy_task_is_read_only_but_sent_unknown_can_be_reconciled(journal):
    task = submit(journal, None)
    adapter = RecordingBAT()
    coordinator = TaskCoordinator(journal, adapter)
    with ca.use(ca.binding(journal.db, BOB), journal):
        await coordinator.tick(task["task_id"])
    assert not adapter.starts and not journal.commands(task["task_id"])
    journal.change(task["task_id"], "dispatching")
    journal.change(task["task_id"], "accepted", fields={"session_id": "owned-before-upgrade"})
    command, _ = journal.command(task["task_id"], "send", "owned-before-upgrade",
        {"prompt_sha256": hashlib.sha256(b"original prompt").hexdigest(), "before": {}}, "old-send")
    journal.command_status(command["command_id"], "uncertain")
    journal.change(task["task_id"], "uncertain")
    adapter.reads["owned-before-upgrade"] = {"turn_started": True, "turn_attribution": "correlated"}
    with ca.use(ca.binding(journal.db, BOB), journal):
        result = await coordinator.tick(task["task_id"])
        await coordinator.tick(task["task_id"])
    assert result["state"] == "running"
    assert journal.command_get(command["command_id"])["status"] == "accepted"
    assert not adapter.starts and not adapter.sends
    assert len(journal.commands(task["task_id"])) == 1


def test_foreign_admin_cannot_control_or_reparent_task(journal):
    task = submit(journal)
    ops = OperationService(journal, actions=task_actions.ACTIONS)
    with ca.use(ca.binding(journal.db, BOB), journal):
        with pytest.raises(ResourceReadOnly, match="FOREIGN_CONTROLLER"):
            task_actions.admit_control(ops, BOB, {"task_id": task["task_id"]}, {}, {})
        with pytest.raises(ResourceReadOnly, match="FOREIGN_CONTROLLER"):
            journal.submit(project="demo", host="h1", workspace="w1", original_words="Inspect the requested change",
                           idempotency_key="task-one")
    assert not journal.get(task["task_id"])["paused"]


def test_task_capability_delegates_original_controller_only_for_its_task(journal, monkeypatch):
    task = submit(journal, engine="goose")
    journal.change(task["task_id"], "dispatching")
    journal.change(task["task_id"], "accepted", fields={"session_id": "lead"})
    token = journal.issue_capability(task["task_id"])
    principal = Principal(f"task:{task['task_id']}:{hashlib.sha256(token.encode()).hexdigest()}", frozenset({"operate"}))
    ops = OperationService(journal, actions=task_actions.ACTIONS)
    def admit(*_):
        assert ca.current() == task_control.authority(task)
        return task_control.admission_binding(journal.get(task["task_id"]), session=True)
    monkeypatch.setattr(task_actions.api_actions, "_admit_session", admit)
    with ca.use(ca.binding(journal.db, principal), journal):
        binding = task_actions.admit_send(ops, principal, {"task_id": task["task_id"]},
                                          {"text": "One bounded step", "step_id": "step-1"}, {})
        assert binding["controller_authority"] == task_control.authority(task)
        assert ca.current()["actor"] == principal.actor
        other = submit(journal, BOB, idempotency_key="other")
        with pytest.raises(OperationError, match="different task"):
            task_actions.admit_task(ops, principal, {"task_id": other["task_id"]}, {}, {})


def test_operation_steering_checks_original_controller_and_current_scope(journal):
    task = submit(journal)
    ops = OperationService(journal, actions=task_actions.ACTIONS)
    op = {"action": "task.pause", "actor": ALICE.actor, "target": {"task_id": task["task_id"]},
          "external_refs": {"admission_binding": task_control.admission_binding(task)}}
    with pytest.raises(ResourceReadOnly, match="FOREIGN_CONTROLLER"):
        task_actions.authorize_controller_existing(ops, BOB, op, "cancel")
    with pytest.raises(OperationError, match="current action scope"):
        task_actions.authorize_controller_existing(ops, Principal(ALICE.actor, frozenset({"observe"})), op, "cancel")
    task_actions.authorize_controller_existing(ops, ALICE, op, "cancel")


async def test_daemon_background_and_rpc_wrappers_restore_and_clear_context(journal):
    task = submit(journal)
    seen = []
    async def background(_tid):
        seen.append(ca.require())
    async def rpc(_method, _params, _principal, **_kwargs):
        seen.append(ca.require())
    daemon = SimpleNamespace(journal=journal, _tick_task_bound=background, _call_api_bound=rpc)
    with ca.use(ca.binding(journal.db, BOB), journal):
        await TaskDaemon._tick_task(daemon, task["task_id"])
        await TaskDaemon.call_api(daemon, "unused", {}, ALICE)
        assert ca.current()["actor"] == BOB.actor
    assert seen == [task_control.authority(task), ca.binding(journal.db, ALICE)]
    assert ca.current() is None


async def test_warm_reuse_refuses_foreign_task_before_host_access(journal):
    previous = submit(journal, BOB, idempotency_key="previous")
    task = submit(journal, ALICE)
    adapter = BatTaskAdapter(SimpleNamespace(), journal=journal)
    with ca.use(task_control.authority(task), journal):
        with pytest.raises(ResourceReadOnly, match="FOREIGN_CONTROLLER"):
            await adapter._warm_identity(task, previous)


def test_generic_operation_binding_is_not_a_task_binding(journal):
    context = SimpleNamespace(admission_binding={"controller_authority": ca.binding(journal.db, ALICE)})
    assert task_control.check_binding(context) is None


def test_failover_commands_keep_original_authority(journal):
    task = submit(journal)
    journal.change(task["task_id"], "dispatching")
    journal.change(task["task_id"], "accepted", fields={"session_id": "lead"})
    journal.change(task["task_id"], "running")
    journal.change(task["task_id"], "quota_limited")
    with ca.use(task_control.authority(task), journal):
        first, handoff = journal.reserve_failover(task["task_id"], "lead", "successor")
    assert json.loads(first["controller_authority"]) == task_control.authority(task)
    assert json.loads(handoff["controller_authority"]) == task_control.authority(task)
