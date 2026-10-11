"""Real durable operations and journal; fake native process, never a model turn."""
from __future__ import annotations

import asyncio
import hashlib
import inspect
import sqlite3
import uuid

import pytest

from bat_agent_connector import (
    pm_discussions,
    pm_native_journal,
    pm_native_operations,
    pm_native_runtime,
    pm_runs,
    pm_workspace,
)
from bat_agent_connector.api_auth import Principal
from bat_agent_connector.operations import OpContext, OperationError, OperationService
from bat_agent_connector.task_journal import Journal
from tests.operation_helpers import settle_operations
from tests.test_pm_discussions import act, prepared, read, record
from tests.test_pm_native_runtime import CLIENT, MODEL, THREAD, TURN, protocol

OWNER = Principal("project-owner", frozenset({"observe", "manage", "start"}))
IDENTITY = {"pid": 424242, "start_ticks": 9101, "boot_id_digest": "a" * 64}


async def invoke(fn, *args):
    result = fn(*args)
    return await result if inspect.isawaitable(result) else result


class FakeRuntime:
    def __init__(self, root, run_id, *, model, record_lifecycle, settings, instances):
        self.root, self.run_id, self.model, self.sink = root, run_id, model, record_lifecycle
        self.settings = settings
        self.process_launch_attempted = False
        self.identity = IDENTITY.copy()
        self.sends, self.rows, self.closed = 0, [], False
        self.ready = asyncio.Event()
        self.release = asyncio.Event()
        instances.append(self)

    async def life(self, kind, **value):
        return await invoke(self.sink, {"kind": kind, "run_id": self.run_id, **value})

    async def __aenter__(self):
        if self.settings.get("prelaunch_error"):
            raise pm_native_runtime.NativeRuntimeError("PM_NATIVE_UNAVAILABLE")
        self.process_launch_attempted = True
        if self.settings.get("launch_unknown"):
            raise OSError("spawn returned no process object")
        await self.life("process_started", process=self.identity, contract=pm_native_runtime.CONTRACT, started_at=1.0)
        return self

    async def __aexit__(self, *_):
        self.closed = True
        if self.settings.get("missing_exit"):
            return
        unknown = self.settings.get("exit_unknown", False)
        await self.life("process_exit_unknown" if unknown else "process_exited", process=self.identity,
            exit_code=None if unknown else 0, signal=None,
            termination="sigkill_unconfirmed" if unknown else "stdin_closed", sent=bool(self.sends),
            native_event_count=len(self.rows), stderr_bytes=0, process_death_confirmed=not unknown)

    async def preflight(self):
        cap = {"contract": pm_native_runtime.CONTRACT, "adapter": pm_native_journal.ADAPTER,
            "version": pm_native_runtime.VERSION, "source": pm_native_runtime.SOURCE,
            "run_id": self.run_id, "thread_id": THREAD, "actual_model": self.model or MODEL,
            "options_confirmed": True, "isolated_config_confirmed": True, "external_tools_disabled": True,
            "environment_access": False, "skills": [], "mcp_servers": [], "automatic_pm_turn": True,
            "config_digest": "b" * 64, **self.settings.get("capability", {})}
        await self.life("preflight", capability=cap)
        self.cap = cap
        if self.settings.get("block_preflight"):
            self.ready.set()
            await self.release.wait()
        return cap

    async def run_turn(self, prompt, *, before_send, record_event, context_digest, timeout):
        intent = {"kind": "send_intent", "run_id": self.run_id, "thread_id": THREAD, "process": self.identity,
            "prompt_digest": hashlib.sha256(prompt.encode()).hexdigest(), "context_digest": context_digest,
            "client_message_id": CLIENT, "request_id": 6, "capability": self.cap}
        if hook := self.settings.get("before_intent"):
            await invoke(hook, self)
        await invoke(before_send, intent)
        if hook := self.settings.get("after_intent"):
            await invoke(hook, self)
        self.sends += 1
        self.ready.set()
        if self.settings.get("block_turn"):
            await self.release.wait()
        values = protocol(prompt, CLIENT)
        if self.settings.get("ack_only"):
            values = values[:1]
        if self.settings.get("ack_mismatch"):
            values[0]["id"] = 42
        if self.settings.get("tool_item"):
            values[-1]["params"]["turn"]["items"].append({"type": "commandExecution", "id": "unsafe"})
        for seq, value in enumerate(values, 1):
            envelope = {"run_id": self.run_id, "seq": seq, "event": value}
            await invoke(record_event, envelope)
            self.rows.append(envelope)
        result = pm_native_runtime.assess_turn(self.rows, run_id=self.run_id, thread_id=THREAD,
            prompt_digest=intent["prompt_digest"], client_message_id=CLIENT, actual_model=self.cap["actual_model"],
            stream_complete=not self.settings.get("stream_unknown", False))
        if not self.settings.get("missing_assessment"):
            await self.life("turn_assessment", assessment=result)
        return result


@pytest.fixture
def world(tmp_path):
    journal = Journal(tmp_path / "native.db")
    ops = OperationService(journal)
    pm_workspace.install(ops)
    pm_discussions.install(ops)
    pm_native_operations.install(ops)
    pm_native_operations.install(ops)
    settings, instances = {}, []
    ops.context["pm_native_runtime_factory"] = lambda *args, **kwargs: FakeRuntime(*args, **kwargs, settings=settings, instances=instances)
    # Never inspect the test PID in the actual host's /proc.
    ops.context["pm_native_process_inspector"] = lambda identity: {"state": "unknown", "process": identity, "proof": "identity_unavailable", "observed_at": 1.0}
    yield ops, settings, instances
    assert all(task.done() for task in ops._active.values()), "test leaked an operation task"
    journal.close()


def request(ops, target, *, params=None, who=OWNER, key=None, version=None):
    return ops.create(who, action="pm.run.analyze", target=target,
        params={"purpose": "Compare the original problem and evidence", **(params or {})},
        preconditions={"expected_version": version or read(ops, target)["thread"]["version"]},
        idempotency_key=key or uuid.uuid4().hex)[0]


async def finish(ops, op):
    await settle_operations(ops)
    return ops.get(op["operation_id"])


def run(ops, op):
    return pm_runs.get_by_operation(ops.db, op["operation_id"])


def writer(ops, target):
    row = ops.db.execute("SELECT run_id FROM pm_writer_slots WHERE workspace_id=?", (target["workspace_id"],)).fetchone()
    return row[0] if row else None


async def await_runtime(ops, instances, op):
    await ops.run_due()
    async def ready():
        while not instances:
            await asyncio.sleep(0)
        await instances[-1].ready.wait()
    await asyncio.wait_for(ready(), 3)
    assert ops.get(op["operation_id"])["status"] == "running"
    return instances[-1]


async def retry(ops, op):
    ops.db.execute("UPDATE operations SET next_run_at=0 WHERE operation_id=?", (op["operation_id"],))
    return await finish(ops, op)


async def test_native_turn_preserves_frozen_sources_raw_events_and_shared_assistant_provenance(world):
    ops, settings, instances = world
    target = await prepared(ops)
    item = await record(ops, target, value="Referral discount remains an unadopted idea")
    message = await act(ops, target, "pm.message.append", {"content": "Only analyze the payment problem"})
    before = read(ops, target)
    def intent_saved(runtime):
        facts = pm_native_journal.lifecycle(ops.db, runtime.run_id)
        assert "send_intent" in facts and runtime.sends == 0
        assert facts["send_intent"]["context_digest"] == pm_runs.get(ops.db, runtime.run_id)["context_digest"]
    settings["after_intent"] = intent_saved
    op = await finish(ops, request(ops, target, params={"model": "explicit-model"}))
    assert op["status"] == "succeeded", op
    saved = run(ops, op)
    assert saved["state"] == "completed" and writer(ops, target) is None
    assert saved["context"]["messages"] == before["messages"]
    assert instances[0].sends == 1 and instances[0].closed
    assert instances[0].root == ops.journal.path.parent / "pm-native"
    doc = read(ops, target)
    answer = doc["messages"][-1]
    assert answer["role"] == "assistant" and answer["actor"] == "pm:" + target["workspace_id"]
    proof = answer["provenance"]
    assert proof["native_turn_id"] == TURN and proof["model"] == "explicit-model"
    refs = proof["source_refs"]
    assert refs["adapter"] == pm_native_journal.ADAPTER and refs["native_thread_id"] == THREAD
    assert refs["message_ids"] == [message["message_id"]]
    assert refs["input_ids"] == [item["input_id"]]
    assert refs["native_event_seqs"] == [4] and len(refs["event_digests"]) == 4
    assert pm_native_journal.events(ops.db, saved["run_id"]) == instances[0].rows
    assert ops.db.execute("SELECT COUNT(*) FROM pm_run_native_events").fetchone()[0] == 0
    assert doc["thread"]["mode"] == "idea" and doc["inputs"] == before["inputs"] and doc["draft"] is None
    for table in ("tasks", "work_items"):
        assert ops.db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0
    replay = request(ops, target, params={"model": "explicit-model"}, key=op["idempotency_key"], version=before["thread"]["version"])
    assert replay["operation_id"] == op["operation_id"] and len(instances) == 1
    # Receipt recovery reads already committed facts even if the caller later loses membership.
    ops.db.execute("DELETE FROM pm_workspace_members WHERE workspace_id=? AND actor=?", (target["workspace_id"], OWNER.actor))
    assert await pm_native_operations._analyze(OpContext(ops, op)) == op["result"]
    assert len(instances) == 1


@pytest.mark.parametrize("setting", ["ack_only", "ack_mismatch", "tool_item", "stream_unknown", "missing_assessment"])
async def test_incomplete_or_wrong_native_turn_aborts_without_inventing_reply(world, setting):
    ops, settings, instances = world
    target = await prepared(ops)
    settings[setting] = True
    op = await finish(ops, request(ops, target))
    assert op["status"] == "failed" and op["error_code"] == "PM_COMPLETION_UNPROVEN", op
    assert run(ops, op)["state"] == "aborted" and not writer(ops, target)
    assert read(ops, target)["messages"] == [] and instances[0].sends == 1


@pytest.mark.parametrize("setting,sent,expected", [("prelaunch_error", 0, "failed"), ("launch_unknown", 0, "uncertain"), ("exit_unknown", 1, "uncertain")])
async def test_process_facts_distinguish_not_started_from_unknown(world, setting, sent, expected):
    ops, settings, instances = world
    target = await prepared(ops)
    settings[setting] = True
    op = await finish(ops, request(ops, target))
    assert op["status"] == expected, op
    assert instances[0].sends == sent
    assert (writer(ops, target) is None) == (expected == "failed")
    assert read(ops, target)["messages"] == []
    if expected == "uncertain":
        again = await retry(ops, op)
        assert again["status"] == "uncertain" and len(instances) == 1


@pytest.mark.parametrize("state", ["alive", "unknown", "dead"])
async def test_recovery_observes_original_identity_without_relaunch_or_signals(world, state):
    ops, settings, instances = world
    target = await prepared(ops)
    settings.update(exit_unknown=True, ack_only=True)
    op = await finish(ops, request(ops, target))
    original = run(ops, op)
    inspected = []
    def inspect_process(identity):
        inspected.append(identity)
        return {"state": state, "process": identity, "observed_at": 2.0,
                "proof": "original_pid_absent" if state == "dead" else "identity_unavailable"}
    ops.context["pm_native_process_inspector"] = inspect_process
    recovered = await retry(ops, op)
    assert inspected == [IDENTITY] and len(instances) == 1 and instances[0].sends == 1
    assert read(ops, target)["messages"] == []
    if state == "dead":
        assert recovered["status"] == "failed" and run(ops, op)["state"] == "aborted"
        assert writer(ops, target) is None
        settings.clear()
        new = await finish(ops, request(ops, target))
        assert new["status"] == "succeeded" and run(ops, new)["generation"] == original["generation"] + 1
    else:
        assert recovered["status"] == "uncertain" and writer(ops, target) == original["run_id"]


@pytest.mark.parametrize("phase", ["block_preflight", "block_turn"])
@pytest.mark.parametrize("unknown", [False, True])
async def test_cancel_stops_own_live_task_and_requires_confirmed_exit(world, phase, unknown):
    ops, settings, instances = world
    target = await prepared(ops)
    settings.update({phase: True, "exit_unknown": unknown})
    op = request(ops, target)
    runtime = await await_runtime(ops, instances, op)
    pm_native_operations.cancel(ops, OWNER, ops.get(op["operation_id"]))
    done = await finish(ops, op)
    assert runtime.closed and runtime.sends == int(phase == "block_turn")
    assert read(ops, target)["messages"] == []
    assert done["status"] == ("uncertain" if unknown else "cancelled"), done
    assert bool(writer(ops, target)) == unknown
    if unknown:
        ops.context["pm_native_process_inspector"] = lambda identity: {"state": "dead", "process": identity, "observed_at": 2.0, "proof": "original_pid_absent"}
        done = await retry(ops, op)
        assert done["status"] == "cancelled" and not writer(ops, target)
        assert len(instances) == 1


async def test_requester_demotion_while_native_turn_runs_stops_without_import(world):
    ops, settings, instances = world
    target = await prepared(ops)
    settings["block_turn"] = True
    op = request(ops, target)
    runtime = await await_runtime(ops, instances, op)
    ops.db.execute("UPDATE pm_workspace_members SET role='reader' WHERE workspace_id=? AND actor=?", (target["workspace_id"], OWNER.actor))
    done = await finish(ops, op)
    assert done["status"] == "failed" and done["error_code"] == "FORBIDDEN", done
    assert runtime.closed and runtime.sends == 1 and run(ops, op)["state"] == "aborted"
    assert not writer(ops, target) and read(ops, target)["messages"] == []


async def test_authority_rechecked_immediately_before_durable_send(world):
    ops, settings, instances = world
    target = await prepared(ops)
    settings["before_intent"] = lambda _: ops.db.execute("UPDATE pm_workspace_members SET role='reader' WHERE workspace_id=? AND actor=?", (target["workspace_id"], OWNER.actor))
    op = await finish(ops, request(ops, target))
    assert op["status"] == "failed" and op["error_code"] == "FORBIDDEN"
    assert instances[0].sends == 0 and instances[0].closed and not writer(ops, target)
    assert "send_intent" not in pm_native_journal.lifecycle(ops.db, run(ops, op)["run_id"])


async def test_parallel_request_retains_single_workspace_writer(world):
    ops, settings, instances = world
    target = await prepared(ops)
    settings["block_turn"] = True
    first = request(ops, target)
    runtime = await await_runtime(ops, instances, first)
    second = request(ops, target)
    await ops._execute(second["operation_id"])
    assert ops.get(second["operation_id"])["error_code"] == "PM_WRITER_BUSY"
    assert writer(ops, target) == run(ops, first)["run_id"] and len(instances) == 1
    runtime.release.set()
    assert (await finish(ops, first))["status"] == "succeeded"


async def test_native_evidence_replay_is_immutable_and_not_borrowable(world):
    ops, settings, instances = world
    target = await prepared(ops)
    op = await finish(ops, request(ops, target))
    saved = run(ops, op)
    rid, generation = saved["run_id"], saved["generation"]
    value = pm_native_journal.events(ops.db, rid)[0]
    assert pm_native_journal.record_event(ops.journal, rid, generation, value) == value
    with pytest.raises(OperationError, match="EVIDENCE_CONFLICT"):
        pm_native_journal.record_event(ops.journal, rid, generation, {**value, "event": {"id": 100}})
    with pytest.raises(OperationError, match="EVIDENCE_CONFLICT"):
        pm_native_journal.record_event(ops.journal, rid, generation + 1, value)
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        ops.db.execute("UPDATE pm_native_events SET envelope='{}' WHERE run_id=?", (rid,))
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        ops.db.execute("DELETE FROM pm_native_lifecycle WHERE run_id=?", (rid,))
    assert pm_native_journal.import_reply(ops.journal, rid, generation)["state"] == "completed"
    assert len(read(ops, target)["messages"]) == 1


@pytest.mark.parametrize("params", [{"auth_file": "/tmp/auth"}, {"executable": "sh"}, {"tools": ["shell"]}, {"max_chars": True}, {"timeout_s": 0}, {"model": ""}])
async def test_public_analysis_cannot_set_runtime_paths_tools_or_unbounded_values(world, params):
    ops, _, instances = world
    target = await prepared(ops)
    with pytest.raises(OperationError, match="INVALID_PARAMS"):
        request(ops, target, params=params)
    assert not instances and not writer(ops, target)


@pytest.mark.parametrize("later", ["none", "cancel", "revoke"])
async def test_lost_execute_ack_recovers_same_turn_and_preserves_later_cancel_or_revocation(world, monkeypatch, later):
    ops, _, instances = world
    target = await prepared(ops)
    original_done = ops._step_done
    lost = False
    def lose_ack(operation_id, name, *args, **kwargs):
        nonlocal lost
        if name == "pm.native.execute" and not lost:
            lost = True
            raise OSError("lost local acknowledgement after original process completed")
        return original_done(operation_id, name, *args, **kwargs)
    monkeypatch.setattr(ops, "_step_done", lose_ack)
    op = await finish(ops, request(ops, target))
    assert op["status"] == "uncertain" and instances[0].closed and instances[0].sends == 1, op
    assert writer(ops, target) == run(ops, op)["run_id"] and read(ops, target)["messages"] == []
    if later == "cancel":
        pm_native_operations.cancel(ops, OWNER, op)
    elif later == "revoke":
        ops.db.execute("UPDATE pm_workspace_members SET role='reader' WHERE workspace_id=? AND actor=?", (target["workspace_id"], OWNER.actor))
    recovered = await retry(ops, op)
    assert len(instances) == 1 and instances[0].sends == 1 and not writer(ops, target)
    if later == "none":
        assert recovered["status"] == "succeeded" and len(read(ops, target)["messages"]) == 1
        assert recovered["result"]["outcome"]["native_turn_id"] == TURN
    else:
        assert recovered["status"] == ("cancelled" if later == "cancel" else "failed"), recovered
        assert read(ops, target)["messages"] == [] and run(ops, op)["state"] == "aborted"


async def test_restart_reuses_original_native_journal_and_never_relaunches(world):
    ops, settings, instances = world
    target = await prepared(ops)
    settings.update(exit_unknown=True, ack_only=True)
    op = await finish(ops, request(ops, target))
    # New journal/service simulates a new process; no live runtime object exists.
    reopened = Journal(ops.journal.path)
    recovered = OperationService(reopened)
    pm_workspace.install(recovered)
    pm_discussions.install(recovered)
    pm_native_operations.install(recovered)
    def forbidden_factory(*args, **kwargs):
        pytest.fail("restart attempted a second native process")
    recovered.context["pm_native_runtime_factory"] = forbidden_factory
    recovered.context["pm_native_process_inspector"] = lambda identity: {
        "state": "dead", "process": identity, "proof": "pid_reused", "current_start_ticks": identity["start_ticks"] + 1, "observed_at": 2.0}
    try:
        result = await retry(recovered, op)
        assert result["status"] == "failed" and run(recovered, op)["state"] == "aborted"
        assert not writer(recovered, target) and read(recovered, target)["messages"] == []
        assert len(instances) == 1
    finally:
        reopened.close()


async def test_worker_shutdown_closes_live_runtime_then_original_operation_reconciles(world):
    ops, settings, instances = world
    target = await prepared(ops)
    settings["block_turn"] = True
    op = request(ops, target)
    runtime = await await_runtime(ops, instances, op)
    worker = ops._active[op["operation_id"]]
    worker.cancel()
    await asyncio.gather(worker, return_exceptions=True)
    assert runtime.closed and pm_native_journal.death_proof(ops.db, run(ops, op)["run_id"])
    assert writer(ops, target) and read(ops, target)["messages"] == []
    result = await retry(ops, op)
    assert result["status"] == "failed" and not writer(ops, target) and len(instances) == 1


@pytest.mark.parametrize("change", ["cancel", "revoke"])
async def test_stop_after_execute_step_started_but_before_runtime_is_proven_zero_effect(world, monkeypatch, change):
    ops, _, instances = world
    target = await prepared(ops)
    original = ops._step_start
    def before_launch(operation_id, name, *args, **kwargs):
        result = original(operation_id, name, *args, **kwargs)
        if name == "pm.native.execute":
            if change == "cancel":
                ops.db.execute("UPDATE operations SET cancel_requested=1 WHERE operation_id=?", (operation_id,))
            else:
                ops.db.execute("UPDATE pm_workspace_members SET role='reader' WHERE workspace_id=? AND actor=?", (target["workspace_id"], OWNER.actor))
        return result
    monkeypatch.setattr(ops, "_step_start", before_launch)
    op = await finish(ops, request(ops, target))
    assert op["status"] == ("cancelled" if change == "cancel" else "failed"), op
    assert instances == [] and not writer(ops, target) and run(ops, op)["state"] == "aborted"
    assert pm_native_journal.death_proof(ops.db, run(ops, op)["run_id"])["kind"] == "process_not_started"


async def test_messages_added_during_analysis_do_not_rewrite_frozen_context(world):
    ops, settings, instances = world
    target = await prepared(ops)
    await act(ops, target, "pm.message.append", {"content": "Original question"})
    settings["block_turn"] = True
    op = request(ops, target)
    runtime = await await_runtime(ops, instances, op)
    frozen = run(ops, op)["context"]
    later, _ = ops.create(OWNER, action="pm.message.append", target=target, params={"content": "A new question for the next turn"},
        preconditions={"expected_version": read(ops, target)["thread"]["version"]}, idempotency_key=uuid.uuid4().hex)
    await ops._execute(later["operation_id"])
    assert ops.get(later["operation_id"])["status"] == "succeeded"
    runtime.release.set()
    done = await finish(ops, op)
    assert done["status"] == "succeeded" and run(ops, op)["context"] == frozen
    rows = read(ops, target)["messages"]
    assert len(rows) == 3 and rows[1]["content"] == "A new question for the next turn"
    assert rows[-1]["provenance"]["source_refs"]["message_ids"] == [rows[0]["message_id"]]


@pytest.mark.parametrize("capability", [
    {"environment_access": True}, {"external_tools_disabled": False}, {"skills": ["coding"]},
    {"mcp_servers": ["external"]}, {"automatic_pm_turn": False}, {"config_digest": ""},
])
async def test_preflight_boundary_must_be_proven_before_send(world, capability):
    ops, settings, instances = world
    target = await prepared(ops)
    settings["capability"] = capability
    op = await finish(ops, request(ops, target))
    assert op["status"] == "failed" and op["error_code"] == "PM_NATIVE_BOUNDARY_UNVERIFIED", op
    assert instances[0].sends == 0 and instances[0].closed and not writer(ops, target)
    assert read(ops, target)["messages"] == []


async def test_planning_analysis_keeps_draft_and_unresolved_statuses_unchanged(world):
    ops, _, _ = world
    target = await prepared(ops)
    selected = await record(ops, target, status="unresolved", value="Decide whether subscription has a free trial")
    await act(ops, target, "pm.thread.plan", {"selected_input_ids": [selected["input_id"]], "rationale": "Prepare a plan draft"})
    before = read(ops, target)
    done = await finish(ops, request(ops, target))
    after = read(ops, target)
    assert done["status"] == "succeeded" and after["draft"] == before["draft"]
    assert after["thread"]["mode"] == "planning" and after["inputs"] == before["inputs"]
    assert not after["draft"]["adopted"] and not after["draft"]["execution_authorized"]


@pytest.mark.parametrize("name", ["pm.native.freeze", "pm.native.import"])
async def test_local_journal_receipt_failure_replays_transaction_without_duplicate_turn(world, monkeypatch, name):
    ops, _, instances = world
    target = await prepared(ops)
    original_done = ops._step_done
    lost = False
    def lose_receipt(operation_id, step, *args, **kwargs):
        nonlocal lost
        if step == name and not lost:
            lost = True
            raise sqlite3.OperationalError("simulated interrupted local transaction")
        return original_done(operation_id, step, *args, **kwargs)
    monkeypatch.setattr(ops, "_step_done", lose_receipt)
    op = await finish(ops, request(ops, target))
    assert op["status"] == "uncertain" and read(ops, target)["messages"] == [], op
    assert len(instances) == (0 if name == "pm.native.freeze" else 1)
    recovered = await retry(ops, op)
    assert recovered["status"] == "succeeded" and len(instances) == 1 and instances[0].sends == 1
    assert len(read(ops, target)["messages"]) == 1 and not writer(ops, target)
