from __future__ import annotations

import asyncio
import hashlib
import json
import time
from types import SimpleNamespace

import pytest

from bat_agent_connector import controller_authority as ca
from bat_agent_connector import registry, task_actions, task_control
from bat_agent_connector.api_auth import Principal
from bat_agent_connector.errors import ResourceReadOnly
from bat_agent_connector.operations import OperationError, OperationService
from bat_agent_connector.task_bat import BatTaskAdapter
from bat_agent_connector.task_core import TaskCoordinator
from bat_agent_connector.task_daemon import TaskDaemon
from bat_agent_connector.task_journal import Journal
from tests.conftest import adopt, make_config
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


@pytest.mark.parametrize("next_step", ["goose", "registry_loss"])
async def test_successful_start_preserves_creation_receipt_for_next_step(mock, tmp_path, next_step):
    mock.handlers["agent:usage-snapshot"] = lambda _: {"claude": {
        "fetchedAt": time.time() * 1000,
        "fiveHour": {"utilization": 0}, "sevenDay": {"utilization": 0}}}
    daemon = TaskDaemon(make_config(mock, writes=True, orchestrate=True, tabs=False,
                                   safety={"write_min_interval_s": 0}), tmp_path / "daemon" / "tasks.db")
    calls = []

    async def run_goose(task, _cwd, *, capability, journal):
        assert journal.authorize_capability(capability, task["task_id"])
        calls.append(ca.require())

    try:
        daemon.goose = SimpleNamespace(config=SimpleNamespace(enabled=True, provider="claude"),
                                       run_task=run_goose)
        original = ca.binding(daemon.journal.db, ALICE)
        with ca.use(original, daemon.journal):
            task = daemon.journal.submit(project="demo", host="h1", workspace="demo-project",
                original_words="Inspect the requested change", idempotency_key="successful-start",
                engine="goose", lead_agent="claude", task_path="minimal",
                engine_decision={"selected": "goose", "effective": "goose"})
        # Exercise the successful coordinator ACK path, not adapter.start in isolation.
        with ca.use(ca.binding(daemon.journal.db, BOB), daemon.journal):
            await daemon._tick_task(task["task_id"])
        current = daemon.journal.get(task["task_id"])
        assert current["state"] == "running"
        sid = current["session_id"]
        entry = registry.get("h1", sid)
        start = next(c for c in daemon.journal.commands(task["task_id"]) if c["kind"] == "start_lead")
        receipt = json.loads(start["payload"])
        assert start["status"] == "settled" and receipt["start_sent"] is True
        assert receipt["controller_incarnation"] == entry["controller_incarnation"]
        assert receipt["controller_authority"] == original
        assert task_control.authority(start) == original
        frames = [i for i in mock.invokes if i["channel"] in {"claude:start-session", "claude:send-message"}]
        assert [i["channel"] for i in frames] == ["claude:start-session", "claude:send-message"]

        if next_step == "goose":
            with ca.use(ca.binding(daemon.journal.db, BOB), daemon.journal):
                await daemon._tick_task(task["task_id"])
            assert calls == [original]
            command = next(c for c in daemon.journal.commands(task["task_id"]) if c["kind"] == "goose_run")
            assert command["status"] == "settled"
            assert daemon.journal.get(task["task_id"])["state"] == "verifying"
        else:
            registry.registry_path().unlink()
            with ca.use(original, daemon.journal):
                assert await daemon.adapter.session_presence(current, sid) == "present"
            restored = registry.get("h1", sid)
            assert restored["recovered_from"] == "task_journal"
            assert restored["controller_incarnation"] == receipt["controller_incarnation"]
            assert restored["controller_authority"] == original
            assert calls == []
        assert [i for i in mock.invokes if i["channel"] in {"claude:start-session", "claude:send-message"}] == frames
    finally:
        await daemon.artifact_store.close_reaper()
        await daemon.api.session_observation.close()
        await daemon.inventory.close()
        await daemon.fleet.close()
        daemon.journal.close()


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


def test_task_cannot_capture_another_journals_controller(journal, tmp_path):
    other = Journal(tmp_path / "other.db")
    try:
        with ca.use(ca.binding(other.db, ALICE), other):
            with pytest.raises(ResourceReadOnly, match="CONTROLLER_SERVER_CHANGED"):
                journal.submit(project="p", host="h1", workspace="w", original_words="requested work",
                               idempotency_key="wrong-server")
        assert not journal.db.execute("SELECT 1 FROM tasks").fetchone()
    finally:
        other.close()


def test_task_frame_keeps_original_session_incarnation(journal, monkeypatch):
    from bat_agent_connector import registry
    task = submit(journal)
    with ca.use(task_control.authority(task), journal):
        adopt("lead", task_id=task["task_id"], role="lead")
        journal.change(task["task_id"], "dispatching")
        task = journal.change(task["task_id"], "accepted", fields={"session_id": "lead"})
        prompt_hash = hashlib.sha256(b"fixed words").hexdigest()
        command, _ = journal.command(task["task_id"], "send", "lead", {"prompt_sha256": prompt_hash}, "send")
        guard = task_control.FrameGuard(journal, task["task_id"], "h1", "lead", task["control_version"],
                                        command_id=command["command_id"], prompt_sha256=prompt_hash)
        guard.check()
        owner = registry.get("h1", "lead")
        monkeypatch.setattr(registry, "get", lambda *_: {**owner, "controller_incarnation": "f" * 32})
        with pytest.raises(ResourceReadOnly, match="CONTROLLER_INCARNATION_CHANGED"):
            guard.check()


async def test_foreign_admin_cannot_mint_reconciliation_capability(journal):
    task = submit(journal)
    daemon = SimpleNamespace(journal=journal)
    with ca.use(ca.binding(journal.db, BOB), journal):
        with pytest.raises(ResourceReadOnly, match="FOREIGN_CONTROLLER"):
            await TaskDaemon._call_bound(daemon, "work_reconcile_capability",
                                         {"task_id": task["task_id"], "command_id": "unused"})
    assert not journal.db.execute("SELECT 1 FROM capabilities").fetchone()


@pytest.mark.parametrize("missing", ["guard_owner", "command_nonce"])
def test_task_frame_without_original_creation_is_read_only(journal, monkeypatch, missing):
    from bat_agent_connector import registry
    from bat_agent_connector.errors import TaskControlRefused
    task = submit(journal)
    with ca.use(task_control.authority(task), journal):
        adopt("lead", task_id=task["task_id"], role="lead")
        journal.change(task["task_id"], "dispatching")
        task = journal.change(task["task_id"], "accepted", fields={"session_id": "lead"})
        prompt_hash = hashlib.sha256(b"fixed words").hexdigest()
        command, _ = journal.command(task["task_id"], "send", "lead", {"prompt_sha256": prompt_hash}, "send")
        if missing == "command_nonce":
            payload = json.loads(command["payload"])
            payload.pop("controller_incarnation")
            journal.db.execute("UPDATE commands SET payload=? WHERE command_id=?", (json.dumps(payload), command["command_id"]))
        with monkeypatch.context() as patch:
            if missing == "guard_owner":
                patch.setattr(registry, "get", lambda *_: None)
            guard = task_control.FrameGuard(journal, task["task_id"], "h1", "lead", task["control_version"],
                                            command_id=command["command_id"], prompt_sha256=prompt_hash)
        with pytest.raises(TaskControlRefused, match="CONTROLLER_BINDING_UNPROVEN"):
            guard.check()


@pytest.mark.parametrize("case", ["legacy_task", "foreign_entry", "replaced_entry", "missing_start"])
async def test_goose_never_launches_without_original_session(journal, monkeypatch, case):
    from bat_agent_connector import registry
    task_caller = None if case == "legacy_task" else ALICE
    entry_caller = BOB if case == "foreign_entry" else ALICE
    task = submit(journal, task_caller, engine="goose")
    journal.change(task["task_id"], "dispatching")
    journal.change(task["task_id"], "accepted", fields={"session_id": "lead"})
    with ca.use(ca.binding(journal.db, entry_caller), journal):
        adopt("lead", task_id=task["task_id"], role="lead")
    if case != "missing_start":
        nonce = "f" * 32 if case == "replaced_entry" else registry.get("h1", "lead")["controller_incarnation"]
        command, _ = journal.command(task["task_id"], "start_lead", "lead", {}, "original-start")
        journal.db.execute("UPDATE commands SET payload=? WHERE command_id=?", (
            json.dumps({"controller_incarnation": nonce}), command["command_id"]))
        journal.command_status(command["command_id"], "settled")
    original_commands = journal.commands(task["task_id"])
    monkeypatch.setattr(journal, "owner_valid", lambda: True, raising=False)
    async def no_launch(*_args, **_kwargs):
        pytest.fail("unproven or foreign task must not start a model")
    daemon = SimpleNamespace(journal=journal, goose=SimpleNamespace(config=SimpleNamespace(enabled=True), run_task=no_launch))
    daemon._tick_task_bound = lambda tid: TaskDaemon._tick_task_bound(daemon, tid)
    with ca.use(ca.binding(journal.db, BOB), journal):
        await TaskDaemon._tick_task(daemon, task["task_id"])
    assert journal.get(task["task_id"])["state"] == "accepted"
    assert journal.commands(task["task_id"]) == original_commands
    assert not journal.db.execute("SELECT 1 FROM capabilities").fetchone()
    assert registry.get("h1", "lead")["controller_authority"]["actor"] == entry_caller.actor


async def test_in_process_task_submit_requires_explicit_authenticated_caller(journal):
    from bat_agent_connector.task_daemon import LegacyTaskError
    daemon = SimpleNamespace(journal=journal)
    with ca.use(ca.binding(journal.db, BOB), journal):
        with pytest.raises(LegacyTaskError, match="authenticated task caller"):
            await TaskDaemon._task_operation(daemon, "work_submit", {
                "host": "h1", "workspace": "w", "project": "p", "original_words": "cannot borrow ambient admin"})
    assert not journal.db.execute("SELECT 1 FROM tasks").fetchone()
