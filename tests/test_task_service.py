from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import os
import stat
import subprocess
import sys
import time
from contextlib import ExitStack, contextmanager
from functools import wraps
from pathlib import Path

import pytest

from bat_agent_connector import (
    api_auth,
    goose_acp,
    lifecycle,
    mcp_server,
    orchestrate,
    registry,
    service,
    task_bat,
)
from bat_agent_connector.errors import TaskIdentityMismatch, WriteRefused
from bat_agent_connector.goose_acp import PINNED_GOOSE_VERSION, GooseACP, GooseConfig
from bat_agent_connector.model_router import MinimalReviewGate, MinimalTaskRouter, ModelRouter, RouterConfig
from bat_agent_connector.pm_providers import (
    AgyShimAdapter,
    ProviderCatalog,
    ProviderEntry,
    ProviderSetupError,
    ProviderSwitcher,
    UncertainPrompt,
    classify_provider_error,
)
from bat_agent_connector.task_core import TaskCoordinator
from bat_agent_connector.task_daemon import TaskDaemon
from bat_agent_connector.task_handoff import history_excerpt, ledger_summary
from bat_agent_connector.task_journal import Journal
from bat_agent_connector.task_verifier import ObservedVerifier, VerificationSettings, load_settings
from tests.conftest import adopt, controller_scope, make_config

WORDS = "請保留 `原文`，不要改成英文。\n第二行：修好它。"


TASK_CALLER = api_auth.Principal(api_auth.ADMIN_ACTOR, frozenset(), admin=True)


@pytest.fixture
def task_controller(controller_context):
    """A test-owned journal scope, opened and closed inside the test's Context."""
    @contextmanager
    def scope():
        with ExitStack() as contexts:
            def bind(value):
                journal = value.journal if isinstance(value, TaskDaemon) else value
                contexts.enter_context(controller_scope(journal, TASK_CALLER))
                return value
            yield bind
    return scope


def authenticated_task_test(fn):
    # pytest async test and fixture teardown run in different Contexts. Keep both
    # sides of each authority scope inside the test coroutine, including failure.
    if inspect.iscoroutinefunction(fn):
        @wraps(fn)
        async def asynchronous(*args, **kwargs):
            with kwargs["task_controller"]() as bind:
                return await fn(*args, **{**kwargs, "task_controller": bind})
        return asynchronous
    @wraps(fn)
    def synchronous(*args, **kwargs):
        with kwargs["task_controller"]() as bind:
            return fn(*args, **{**kwargs, "task_controller": bind})
    return synchronous


def review_json(verdict="pass", commit="a" * 40, tree="b" * 40, findings=()):
    return "Checked the diff and tests.\n" + json.dumps(
        {"verdict": verdict, "candidate_commit": commit, "tree_hash": tree, "findings": list(findings)})


REVIEW_PASS = review_json()


async def test_goose_executable_version_is_pinned(tmp_path):
    binary = tmp_path / "goose"
    binary.write_text("#!/bin/sh\necho 'goose 1.52.0'\n")
    binary.chmod(0o700)
    goose = GooseACP(GooseConfig(command=(str(binary), "acp")))
    assert await goose.check_pinned_version() == PINNED_GOOSE_VERSION
    binary.write_text("#!/bin/sh\necho 'goose 1.53.0'\n")
    with pytest.raises(RuntimeError, match="pinned ACP contract version"):
        await goose.check_pinned_version()


def submit(journal, **kw):
    return journal.submit(project="p", host="h1", workspace="w", original_words=WORDS,
                          acceptance="tests pass", idempotency_key="discord:message:1", **kw)


class FakeBAT:
    def __init__(self):
        self.starts = []
        self.sends = []
        self.reads = {}
        self.identity = {"candidate_commit": "a" * 40, "tree_hash": "b" * 40, "clean": True}
        self.diff_excerpt = None
        self.review_diff = None
        self.verifier_available = True
        self.reviewer_kind = "codex"
        self.started_ids = set()
        self.vanished_ids = set()
        self.presence_override = None
        self.successors = {}
        self.failover_allowed = False
        self.start_error = False
        self.start_gate = None
        self.prepare_kind = "claude"
        self.failover_calls = 0
        self.interrupts = 0
        self.send_error = False
        self.reconcile_echo = None
        self.active_sends = 0
        self.max_active_sends = 0

    async def start(self, task, *, role, agent, session_id):
        self.starts.append((role, agent, session_id))
        self.started_ids.add(session_id)
        if self.start_gate is not None:
            await self.start_gate.wait()
        if self.start_error:
            raise TimeoutError("start response lost")
        return session_id

    async def recover_start(self, task, *, role, session_id):
        return session_id in self.started_ids

    async def prepare_send(self, task, session_id):
        return {"agent_kind": self.prepare_kind,
                "before_cursor": "2026-09-27T00:00:00+00:00" if self.prepare_kind == "codex" else None}

    async def session_presence(self, task, session_id):
        if self.presence_override:
            return self.presence_override
        return "vanished" if session_id in self.vanished_ids else "present"

    async def send(self, task, session_id, text, message_id):
        self.active_sends += 1
        self.max_active_sends = max(self.max_active_sends, self.active_sends)
        await asyncio.sleep(0)
        self.active_sends -= 1
        self.sends.append((session_id, text, message_id))
        if self.send_error:
            raise TimeoutError("lost response after possible acceptance")
        return {"accepted": True, "turn_marker": message_id if self.prepare_kind == "claude"
                else "2026-09-27T00:00:00+00:00",
                "turn_attribution": "exact_echo" if self.prepare_kind == "claude" else "timestamp_cursor"}

    async def read(self, task, session_id, marker):
        read = self.reads.get(session_id, {"turn_started": False, "turn_done": False}).copy()
        read.setdefault("streaming", False)
        if read.get("turn_started") and "turn_attribution" not in read:
            read["turn_attribution"] = "correlated"
        return read

    async def reconcile_send(self, task, session_id, prompt_sha256, before, message_id):
        echo = self.reconcile_echo
        if (echo and echo["session_id"] == session_id and echo["message_id"] == message_id
                and hashlib.sha256(echo["text"].encode()).hexdigest() == prompt_sha256):
            return {"accepted": True, "turn_marker": echo["turn_id"],
                    "turn_attribution": "exact_echo"}
        return None

    async def interrupt(self, task, session_id):
        self.interrupts += 1

    async def failover(self, task, session_id, successor_id, *, handoff_message_id, handoff_command_id):
        self.failover_calls += 1
        if not self.failover_allowed:
            raise ValueError("Codex cannot Claude-to-Codex fail over")
        self.sends.append((successor_id, "handoff for task " + task["task_id"], handoff_message_id))
        self.successors[session_id] = {"session_id": successor_id, "marker": handoff_message_id}
        return self.successors[session_id]

    async def recover_failover(self, task, *, successor_id, handoff_message_id, handoff_command_id):
        return self.successors.get(task["session_id"])

    async def candidate_identity(self, task):
        return self.identity

    async def candidate_diff_excerpt(self, task):
        return self.diff_excerpt

    async def candidate_review_diff(self, task):
        return self.review_diff

    async def run_verification(self, task):
        if not self.verifier_available:
            return None
        return {"source": "observed_runner", **{k: self.identity[k] for k in ("candidate_commit", "tree_hash")},
                "command": "pytest -q", "exit_code": 0, "log_ref": "journal:fake", "output_sha256": "c" * 64}

    async def available_agents(self, task):
        return frozenset({self.reviewer_kind, "codex"})


@authenticated_task_test
def test_journal_idempotency_restart_and_metrics(task_controller, tmp_path):
    path = tmp_path / "tasks.db"
    j = task_controller(Journal(path))
    task = submit(j)
    assert task["original_words"] == WORDS
    assert submit(j)["task_id"] == task["task_id"]
    with pytest.raises(ValueError, match="different task"):
        j.submit(project="p", host="h1", workspace="w", original_words="rewritten",
                 idempotency_key="discord:message:1")
    assert j.db.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert j.db.execute("PRAGMA synchronous").fetchone()[0] == 2
    j.close()
    j = task_controller(Journal(path))
    assert j.get(task["task_id"])["original_words"] == WORDS
    assert j.get(task["task_id"])["delivered"] is False
    assert j.get(task["task_id"])["time_to_deliver_s"] is None
    with pytest.raises(ValueError, match="invalid transition"):
        j.change(task["task_id"], "done")
    j.request_ted(task["task_id"], "need choice")
    assert j.get(task["task_id"])["ted_interventions"] == 0
    j.ted_action(task["task_id"], action="pause", source_message_id="discord:ted:1")
    j.ted_action(task["task_id"], action="pause", source_message_id="discord:ted:1")
    assert j.get(task["task_id"])["ted_interventions"] == 1
    j.close()


@authenticated_task_test
async def test_timeout_no_resend_and_reconcile(task_controller, tmp_path):
    path = tmp_path / "tasks.db"
    j = task_controller(Journal(path))
    task = submit(j)
    fake = FakeBAT()
    fake.send_error = True
    core = TaskCoordinator(j, fake)
    await core.tick(task["task_id"])
    assert j.get(task["task_id"])["state"] == "uncertain"
    assert len(fake.sends) == 1
    sid = j.get(task["task_id"])["session_id"]
    j.close()
    j = task_controller(Journal(path))
    core = TaskCoordinator(j, fake)
    await core.tick(task["task_id"])
    assert len(fake.sends) == 1
    fake.reads[sid] = {"turn_started": True, "turn_done": False,
                       "turn_attribution": "correlated"}
    assert (await core.tick(task["task_id"]))["state"] == "running"
    assert len(fake.sends) == 1
    j.close()


@authenticated_task_test
async def test_send_timeout_after_acceptance_read_back_exact_prompt(task_controller, tmp_path):
    class AcceptedThenLost(FakeBAT):
        async def send(self, task, session_id, text, message_id):
            self.reconcile_echo = {"session_id": session_id, "message_id": message_id,
                                   "text": text, "turn_id": "user-confirmed-1"}
            await super().send(task, session_id, text, message_id)

    path = tmp_path / "tasks.db"
    journal = task_controller(Journal(path))
    task = submit(journal)
    fake = AcceptedThenLost()
    fake.prepare_kind = "codex"
    fake.send_error = True
    core = TaskCoordinator(journal, fake)
    result = await core.tick(task["task_id"])
    assert result["state"] == "running" and result["turn_marker"] == "user-confirmed-1"
    assert len(fake.sends) == 1
    send = next(c for c in journal.commands(task["task_id"]) if c["kind"] == "send")
    assert send["status"] == "accepted" and send["marker"] == "user-confirmed-1"
    assert "send_reconciled_delivered" in [e["kind"] for e in journal.events(task["task_id"])]
    journal.close()
    journal = task_controller(Journal(path))
    await TaskCoordinator(journal, fake).tick(task["task_id"])
    assert len(fake.sends) == 1
    journal.close()


@authenticated_task_test
async def test_codex_accepted_timestamp_requires_exact_read_back(task_controller, tmp_path):
    class AcceptedTimestampBAT(FakeBAT):
        async def send(self, task, session_id, text, message_id):
            self.reconcile_echo = {"session_id": session_id, "message_id": message_id,
                                   "text": text, "turn_id": "user-codex-exact"}
            return await super().send(task, session_id, text, message_id)

    journal = task_controller(Journal(tmp_path / "accepted.db"))
    task = submit(journal)
    fake = AcceptedTimestampBAT()
    fake.prepare_kind = "codex"
    result = await TaskCoordinator(journal, fake).tick(task["task_id"])
    assert result["state"] == "running" and result["turn_marker"] == "user-codex-exact"
    send = next(c for c in journal.commands(task["task_id"]) if c["kind"] == "send")
    assert send["status"] == "accepted" and send["marker"] == "user-codex-exact"
    assert len(fake.sends) == 1
    assert "send_reconciled_delivered" in [e["kind"] for e in journal.events(task["task_id"])]
    journal.close()

    journal = task_controller(Journal(tmp_path / "unproven.db"))
    task = submit(journal)
    fake = FakeBAT()
    fake.prepare_kind = "codex"
    core = TaskCoordinator(journal, fake)
    result = await core.tick(task["task_id"])
    assert result["state"] == "uncertain" and not result["delivered"]
    fake.reads[result["session_id"]] = {"turn_started": True, "turn_done": True,
                                         "turn_attribution": "timestamp_cursor",
                                         "messages": [{"role": "assistant", "text": REVIEW_PASS}]}
    assert (await core.tick(task["task_id"]))["state"] == "uncertain"
    assert len(fake.sends) == 1
    journal.close()


async def test_bat_read_back_requires_exact_new_user_echo(fleet_factory, mock):
    fleet = fleet_factory()
    adapter = task_bat.BatTaskAdapter(fleet)
    sid = "sess-codex-0002"
    mock.states[sid]["isStreaming"] = False
    prompt = "Ted's exact original request\nBAT-STATUS: MILESTONE"
    before = await adapter.prepare_send({"host": "h1"}, sid)
    mock.states[sid]["messages"].append({"id": "assistant-unrelated", "role": "assistant",
                                          "content": "REVIEW: PASS", "timestamp": 1_900_000_000_000})
    digest = hashlib.sha256(prompt.encode()).hexdigest()
    assert await adapter.reconcile_send({"host": "h1"}, sid, digest, before, "batc-lost") is None
    mock.states[sid]["messages"].append({"id": "user-exact", "role": "user",
                                          "content": prompt, "timestamp": 1_900_000_000_001})
    proof = await adapter.reconcile_send({"host": "h1"}, sid, digest, before, "batc-lost")
    assert proof["turn_marker"] == "user-exact"
    read = await service.session_read(fleet, "h1", sid, after=proof["turn_marker"])
    assert read["turn_attribution"] == "correlated"
    assert await adapter.reconcile_send({"host": "h1"}, sid, "0" * 64, before, "batc-lost") is None
    later_before = await adapter.prepare_send({"host": "h1"}, sid)
    mock.states[sid]["messages"].append({"id": "assistant-later", "role": "assistant",
                                          "content": "REVIEW: PASS", "timestamp": 1_900_000_000_002})
    assert await adapter.reconcile_send({"host": "h1"}, sid, digest, later_before, "batc-later") is None
    await fleet.close()


@authenticated_task_test
async def test_verification_waits_for_quiet_candidate_window(task_controller, tmp_path):
    journal = task_controller(Journal(tmp_path / "quiet.db"))
    task = submit(journal)
    fake = FakeBAT()
    core = TaskCoordinator(journal, fake, verification_quiet_s=0.05)
    await core.tick(task["task_id"])
    lead = journal.get(task["task_id"])["session_id"]
    fake.reads[lead] = {"turn_started": True, "turn_done": True,
                        "turn_attribution": "correlated",
                        "messages": [{"role": "assistant", "text": "BAT-STATUS: MILESTONE"}]}
    first = await core.tick(task["task_id"])
    assert first["state"] == "verifying"
    await core.tick(task["task_id"])
    assert not any(e["kind"] == "verification_observed" for e in journal.events(task["task_id"]))
    await asyncio.sleep(0.06)
    third = await core.tick(task["task_id"])
    assert any(e["kind"] == "verification_observed" for e in journal.events(task["task_id"]))
    assert third["state"] == "done"  # trusted tests are the verdict; no reviewer
    journal.close()


@authenticated_task_test
async def test_daemon_records_actual_journal_for_send_fence(task_controller, tmp_path, mock):
    daemon = task_controller(TaskDaemon(make_config(mock), db_path=tmp_path / "custom" / "jobs.db"))
    daemon.acquire_owner()
    try:
        assert service.task_service_db() == (tmp_path / "custom" / "jobs.db").resolve()
    finally:
        daemon.release_owner()
        daemon.journal.close()


@authenticated_task_test
async def test_verification_budget_is_recipe_aware(task_controller, tmp_path, mock):
    daemon = task_controller(TaskDaemon(make_config(mock), db_path=tmp_path / "budget.db"))
    assert daemon.verification_budget("feature-to-staging") >= 3600
    assert daemon.verification_budget("small-task-with-tests") >= 900
    daemon.verification_timeout_s = 7
    assert daemon.verification_budget("feature-to-staging") == 7
    await daemon.fleet.close()
    daemon.journal.close()


@authenticated_task_test
async def test_verification_deadline_and_failure_do_not_stall_worker(task_controller, tmp_path, mock):
    daemon = task_controller(TaskDaemon(make_config(mock), db_path=tmp_path / "tasks.db"))
    daemon.verification_timeout_s = 1
    hung = daemon.journal.submit(project="p", host="h1", workspace="w", original_words="hung",
                                 idempotency_key="hung")
    failed = daemon.journal.submit(project="p", host="h1", workspace="w", original_words="failed",
                                   idempotency_key="failed")
    healthy = daemon.journal.submit(project="p", host="h1", workspace="w", original_words="healthy",
                                    idempotency_key="healthy")
    for item in (hung, failed, healthy):
        daemon.journal.change(item["task_id"], "dispatching")
        daemon.journal.change(item["task_id"], "verifying")
    calls = []

    async def tick(task_id):
        calls.append(task_id)
        if task_id == hung["task_id"]:
            await asyncio.Event().wait()
        if task_id == failed["task_id"]:
            raise RuntimeError("private verifier detail must stay out of journal")

    daemon.coordinator.tick = tick
    for item in (hung, failed, healthy):
        daemon.journal.db.execute("UPDATE tasks SET verifying_started_at=?, progress_at=? WHERE task_id=?",
                                  (time.time(), time.time(), item["task_id"]))
        await daemon._tick_task(item["task_id"])
    assert all(item["task_id"] in calls for item in (hung, failed, healthy))
    assert daemon.journal.get(hung["task_id"])["state"] == "needs_ted"
    assert daemon.journal.get(failed["task_id"])["state"] == "needs_ted"
    assert daemon.journal.get(healthy["task_id"])["state"] == "verifying"
    assert "verification_timeout" in [e["kind"] for e in daemon.journal.events(hung["task_id"])]
    assert "verification_error" in [e["kind"] for e in daemon.journal.events(failed["task_id"])]
    assert "private verifier detail" not in json.dumps(daemon.journal.events(failed["task_id"]))
    daemon.journal.db.execute("UPDATE tasks SET verifying_started_at=?, progress_at=? WHERE task_id=?",
                              (time.time() - 2, time.time() - 2, healthy["task_id"]))
    before = len(calls)
    await daemon._tick_task(healthy["task_id"])
    assert len(calls) == before
    assert daemon.journal.get(healthy["task_id"])["state"] == "needs_ted"
    assert "verification_deadline" in [e["kind"] for e in daemon.journal.events(healthy["task_id"])]
    await daemon.fleet.close()
    daemon.journal.close()


@authenticated_task_test
async def test_verification_clocks_ignore_heartbeats_and_cap_active_phase(task_controller, tmp_path, mock):
    daemon = task_controller(TaskDaemon(make_config(mock), db_path=tmp_path / "clocks.db"))
    task = daemon.journal.submit(project="p", host="h1", workspace="w", original_words="x",
                                 idempotency_key="clocks", recipe="small-task-with-tests")
    daemon.journal.change(task["task_id"], "dispatching")
    daemon.journal.change(task["task_id"], "accepted")
    daemon.journal.change(task["task_id"], "running")
    started = daemon.journal.change(task["task_id"], "verifying")
    assert started["verifying_started_at"] and started["progress_at"]
    # Reviewer start (verifying -> dispatching -> verifying) keeps the same phase clock.
    daemon.journal.db.execute("UPDATE tasks SET verifying_started_at=? WHERE task_id=?",
                              (started["verifying_started_at"] - 100, task["task_id"]))
    daemon.journal.change(task["task_id"], "dispatching")
    again = daemon.journal.change(task["task_id"], "verifying")
    assert again["verifying_started_at"] == started["verifying_started_at"] - 100
    budget = daemon.verification_budget("small-task-with-tests")
    # A fresh updated_at heartbeat no longer extends a stalled phase.
    daemon.journal.db.execute("UPDATE tasks SET verifying_started_at=?, progress_at=?, updated_at=? "
                              "WHERE task_id=?", (time.time() - budget - 1, time.time() - budget - 1,
                                                  time.time(), task["task_id"]))
    assert daemon.verification_remaining(daemon.journal.get(task["task_id"])) <= 0
    # Real progress extends the idle clock, but never past the absolute cap.
    daemon.journal.progress(task["task_id"])
    assert daemon.verification_remaining(daemon.journal.get(task["task_id"])) > 0
    daemon.journal.db.execute("UPDATE tasks SET verifying_started_at=? WHERE task_id=?",
                              (time.time() - daemon.verification_cap("small-task-with-tests") - 1,
                               task["task_id"]))
    daemon.journal.progress(task["task_id"])
    assert daemon.verification_remaining(daemon.journal.get(task["task_id"])) <= 0
    await daemon._tick_task(task["task_id"])
    assert daemon.journal.get(task["task_id"])["state"] == "needs_ted"
    await daemon.fleet.close()
    daemon.journal.close()


class FailingTestsBAT(FakeBAT):
    def __init__(self, kinds):
        super().__init__()
        self.kinds = list(kinds)
        self.runs = 0
        self.installs = 0

    async def run_verification(self, task):
        self.runs += 1
        evidence = await super().run_verification(task)
        if evidence:
            evidence["exit_code"] = 0 if not self.kinds or self.kinds[0] == "pass" else 1
        return evidence

    async def verification_failure(self, task, evidence):
        kind = self.kinds.pop(0) if self.kinds else "code"
        return {"kind": kind, "summary": "FAILED test_x - assert 1 == 2"}

    async def install_dependencies(self, task):
        self.installs += 1
        return {"ok": True, "reason": "installed", "lockfile": "package-lock.json"}


async def _to_verifying(core, journal, fake, task):
    await core.tick(task["task_id"])
    lead = journal.get(task["task_id"])["session_id"]
    fake.reads[lead] = {"turn_started": True, "turn_done": True,
                        "messages": [{"role": "assistant", "text": "BAT-STATUS: MILESTONE"}]}
    await core.tick(task["task_id"])
    return lead


@authenticated_task_test
async def test_code_test_failure_reworks_lead_then_escalates_after_budget(task_controller, tmp_path):
    j = task_controller(Journal(tmp_path / "rework.db"))
    task = submit(j, recipe="small-task-with-tests")
    fake = FailingTestsBAT(["code", "code"])
    core = TaskCoordinator(j, fake)
    lead = await _to_verifying(core, j, fake, task)
    result = await core.tick(task["task_id"])
    assert result["state"] == "running" and result["verification_failures"] == 1
    assert result["session_id"] == lead
    assert "Do not re-plan" in fake.sends[-1][1]
    fake.identity = {"candidate_commit": "d" * 40, "tree_hash": "e" * 40, "clean": True}
    fake.reads[lead] = {"turn_started": True, "turn_done": True,
                        "messages": [{"role": "assistant", "text": "fixed\nBAT-STATUS: MILESTONE"}]}
    await core.tick(task["task_id"])
    result = await core.tick(task["task_id"])
    assert result["state"] == "needs_ted" and "rework budget exhausted" in result["result"]
    j.close()


@authenticated_task_test
async def test_environment_test_failure_escalates_without_rework(task_controller, tmp_path):
    j = task_controller(Journal(tmp_path / "env.db"))
    task = submit(j)
    fake = FailingTestsBAT(["environment"])
    core = TaskCoordinator(j, fake)
    await _to_verifying(core, j, fake, task)
    sends = len(fake.sends)
    result = await core.tick(task["task_id"])
    assert result["state"] == "needs_ted" and "environment" in result["result"]
    assert len(fake.sends) == sends
    j.close()


@authenticated_task_test
async def test_missing_dependencies_install_once_then_retest(task_controller, tmp_path):
    j = task_controller(Journal(tmp_path / "deps.db"))
    task = submit(j)
    fake = FailingTestsBAT(["missing_dependencies", "pass"])
    core = TaskCoordinator(j, fake)
    await _to_verifying(core, j, fake, task)
    result = await core.tick(task["task_id"])
    assert fake.installs == 1 and fake.runs == 2
    assert result["state"] == "verifying" and result["verification_failures"] == 0
    await core.tick(task["task_id"])
    assert j.get(task["task_id"])["state"] == "done"
    assert j.get(task["task_id"])["reviewer_session_id"] is None
    kinds = [e["kind"] for e in j.events(task["task_id"])]
    assert kinds.count("dependency_install") == 1 and "dependency_install_result" in kinds
    j.close()


@authenticated_task_test
async def test_missing_dependencies_after_install_becomes_code_rework(task_controller, tmp_path):
    j = task_controller(Journal(tmp_path / "deps2.db"))
    task = submit(j)
    fake = FailingTestsBAT(["missing_dependencies", "missing_dependencies"])
    core = TaskCoordinator(j, fake)
    await _to_verifying(core, j, fake, task)
    result = await core.tick(task["task_id"])
    assert fake.installs == 1
    assert result["state"] == "running" and result["verification_failures"] == 1
    j.close()


@authenticated_task_test
async def test_codex_timestamp_never_proves_lost_send_or_review(task_controller, tmp_path):
    j = task_controller(Journal(tmp_path / "tasks.db"))
    task = submit(j)
    fake = FakeBAT()
    fake.prepare_kind = "codex"
    fake.send_error = True
    core = TaskCoordinator(j, fake)
    assert (await core.tick(task["task_id"]))["state"] == "uncertain"
    sid = j.get(task["task_id"])["session_id"]
    fake.reads[sid] = {"turn_started": True, "turn_done": True,
                       "turn_attribution": "timestamp_cursor",
                       "messages": [{"role": "assistant", "text": "REVIEW: PASS\nBAT-STATUS: MILESTONE"}]}
    assert (await core.tick(task["task_id"]))["state"] == "uncertain"
    assert not j.get(task["task_id"])["delivered"] and len(fake.sends) == 1
    j.close()

    j = task_controller(Journal(tmp_path / "review.db"))
    task = submit(j)
    fake = FakeBAT()
    core = TaskCoordinator(j, fake)
    await core.tick(task["task_id"])
    lead = j.get(task["task_id"])["session_id"]
    fake.reads[lead] = {"turn_started": True, "turn_done": True, "turn_attribution": "correlated",
                        "messages": [{"role": "assistant", "text": "BAT-STATUS: MILESTONE"}]}
    held = await core.tick(task["task_id"])
    assert held["state"] == "verifying" and held["reviewer_session_id"] is None
    j.close()


@authenticated_task_test
async def test_operator_reconcile_is_command_scoped_and_dispatches_new_prompt(task_controller, mock, tmp_path):
    daemon = task_controller(TaskDaemon(make_config(mock, writes=True, orchestrate=True), tmp_path / "tasks.db"))
    class OwnedFakeBAT(FakeBAT):
        async def start(self, task, *, role, agent, session_id):
            adopt(session_id, task_id=task["task_id"], role=role)
            return await super().start(task, role=role, agent=agent, session_id=session_id)
    fake = OwnedFakeBAT()
    fake.send_error = True
    daemon.adapter = fake
    daemon.coordinator = TaskCoordinator(daemon.journal, fake)
    task = submit(daemon.journal)
    await daemon.coordinator.tick(task["task_id"])
    command = next(c for c in daemon.journal.commands(task["task_id"]) if c["kind"] == "send")
    assert command["status"] == "uncertain"
    server = await asyncio.start_server(daemon._handle, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]

    async def rpc(token, method, params):
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        body = json.dumps({"method": method, "params": params}).encode()
        writer.write(b"POST /rpc HTTP/1.1\r\nHost: localhost\r\nAuthorization: Bearer " +
                     token.encode() + b"\r\nContent-Length: " + str(len(body)).encode() +
                     b"\r\n\r\n" + body)
        await writer.drain()
        raw = await reader.read()
        writer.close()
        await writer.wait_closed()
        return raw.split(b"\r\n\r\n", 1)[0], json.loads(raw.split(b"\r\n\r\n", 1)[1])

    params = {"task_id": task["task_id"], "command_id": command["command_id"],
              "outcome": "not_delivered", "actor": "operator", "source": "ticket:123",
              "evidence": "inspected BAT turn list and found no matching user prompt",
              "next_prompt": "Inspect the repo and report current state for this task."}
    try:
        status, result = await rpc(daemon._admin_token, "work_reconcile", params)
        assert b"400 Bad Request" in status and result["error"] == "ValueError"
        status, result = await rpc(daemon._admin_token, "work_reconcile_capability",
                                   {"task_id": task["task_id"], "command_id": command["command_id"]})
        assert b"200 OK" in status
        cap = result["result"]["capability"]
        status, result = await rpc(cap, "work_reconcile", {**params, "task_id": "other-task"})
        assert b"400 Bad Request" in status
        fake.send_error = False
        status, result = await rpc(cap, "work_reconcile", params)
        assert b"200 OK" in status and result["result"]["state"] == "running"
        assert len(fake.sends) == 2 and fake.sends[1][1] == params["next_prompt"]
        assert fake.sends[0][1] != fake.sends[1][1]
        assert daemon.journal.command_get(command["command_id"])["status"] == "resolved_not_delivered"
        rec = daemon.journal.reconciliations(task["task_id"])[0]
        assert (rec["actor"], rec["source"], rec["outcome"]) == ("operator", "ticket:123", "not_delivered")
        status, _ = await rpc(cap, "work_reconcile", params)
        assert b"400 Bad Request" in status
        assert len(fake.sends) == 2
    finally:
        server.close()
        await server.wait_closed()
        await daemon.fleet.close()
        daemon.journal.close()


@authenticated_task_test
async def test_operator_followup_intent_survives_crash_without_automatic_send(task_controller, tmp_path):
    path = tmp_path / "tasks.db"
    j = task_controller(Journal(path))
    task = submit(j)
    fake = FakeBAT()
    fake.send_error = True
    core = TaskCoordinator(j, fake)
    await core.tick(task["task_id"])
    old = next(c for c in j.commands(task["task_id"]) if c["kind"] == "send")
    cap = j.issue_reconcile_capability(task["task_id"], old["command_id"])
    followup = "New explicit operator instruction"
    result = j.resolve_send(task["task_id"], old["command_id"], token=cap, outcome="superseded",
                            actor="operator", source="ticket:456", evidence="previous turn superseded",
                            next_prompt_sha256=hashlib.sha256(followup.encode()).hexdigest(),
                            next_before={"agent_kind": "claude"})
    new_command = j.command_get(result["_next_command_id"])
    assert new_command["status"] == "needs_review"
    j.close()
    j = task_controller(Journal(path))
    assert (await TaskCoordinator(j, fake).tick(task["task_id"]))["state"] == "uncertain"
    assert len(fake.sends) == 1
    assert j.command_get(new_command["command_id"])["status"] == "uncertain"
    j.close()


@authenticated_task_test
async def test_task_bat_adapter_contract_with_mock_host(task_controller, fleet_factory, mock, tmp_path):
    fleet = fleet_factory(writes=True, orchestrate=True, tabs=True,
                          safety={"write_min_interval_s": 0})
    j = task_controller(Journal(tmp_path / "tasks.db"))
    task = j.submit(project="p", host="h1", workspace="demo-project", original_words=WORDS,
                    idempotency_key="adapter-contract")
    adapter = task_bat.BatTaskAdapter(fleet, ObservedVerifier(VerificationSettings()), j)
    lead = "task-lead-contract"
    start_command, _ = j.command(task["task_id"], "start_lead", lead,
                                 {"role": "lead", "agent": "codex"}, "adapter-contract-start")
    assert await adapter.start(task, role="lead", agent="codex", session_id=lead) == lead
    start = next(i for i in mock.invokes if i["channel"] == "claude:start-session"
                 and i["params"]["sessionId"] == lead)
    assert start["params"]["options"]["agentPreset"] == "codex-agent-worktree"
    assert not any(t.get("id") == lead for t in mock.ws_doc["terminals"])
    assert await adapter.recover_start(task, role="lead", session_id=lead)
    j.command_status(start_command["command_id"], "settled")
    j.add_branch(task["task_id"], session_id=lead, provider="codex", role="lead", reason="start")
    j.change(task["task_id"], "dispatching")
    task = j.change(task["task_id"], "accepted", fields={"session_id": lead})
    before = await adapter.prepare_send(task, lead)
    assert before["agent_kind"] == "codex" and before["before_cursor"]
    prompt = "synthetic contract prompt"
    command, _ = j.command(task["task_id"], "send", lead,
                           {"prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest()}, "contract-send")
    send = await adapter.send(task, lead, prompt, command["message_id"])
    assert send["accepted"]
    reviewer = "task-review-contract"
    assert await adapter.start(task, role="reviewer", agent="codex", session_id=reviewer) == reviewer
    review_start = next(i for i in mock.invokes if i["channel"] == "claude:start-session"
                        and i["params"]["sessionId"] == reviewer)
    opts = review_start["params"]["options"]
    assert opts["codexSandboxMode"] == "read-only" and opts["codexApprovalPolicy"] == "never"
    assert opts["cwd"] == start["params"]["options"]["cwd"]
    assert not any(t.get("id") == reviewer for t in mock.ws_doc["terminals"])
    j.close()


@authenticated_task_test
async def test_claude_sessions_pin_opus_55_for_lead_and_reviewer(task_controller, fleet_factory, mock, tmp_path):
    fleet = fleet_factory(writes=True, orchestrate=True, safety={"write_min_interval_s": 0})
    j = task_controller(Journal(tmp_path / "tasks.db"))
    task = j.submit(project="p", host="h1", workspace="demo-project", original_words=WORDS,
                    idempotency_key="claude-pin", lead_agent="claude")
    adapter = task_bat.BatTaskAdapter(fleet, ObservedVerifier(VerificationSettings()), j)
    lead = "task-lead-claude"
    start_command, _ = j.command(task["task_id"], "start_lead", lead,
                                 {"role": "lead", "agent": "claude"}, "claude-pin-start")
    assert await adapter.start(task, role="lead", agent="claude", session_id=lead) == lead
    assert await adapter.recover_start(task, role="lead", session_id=lead)
    j.command_status(start_command["command_id"], "settled")
    j.add_branch(task["task_id"], session_id=lead, provider="claude", role="lead", reason="start")
    j.change(task["task_id"], "dispatching")
    task = j.change(task["task_id"], "accepted", fields={"session_id": lead})
    reviewer = "task-review-claude"
    assert await adapter.start(task, role="reviewer", agent="claude", session_id=reviewer) == reviewer
    for sid, preset in ((lead, "claude-code-worktree"), (reviewer, "claude-code")):
        opts = next(i for i in mock.invokes if i["channel"] == "claude:start-session"
                    and i["params"]["sessionId"] == sid)["params"]["options"]
        assert opts["agentPreset"] == preset and opts["model"] == task_bat.CLAUDE_BAT_MODEL
        assert opts["model"].startswith("claude-opus-5-5")
        assert task_bat.registry.get("h1", sid)["confinement"]["level"] == "prompt_gated"
        assert task_bat.registry.get("h1", sid)["confinement"]["verification"]["status"] == "options_confirmed"
    j.close()


@authenticated_task_test
async def test_available_agents_uses_host_usage_snapshot_and_quota_latch(task_controller, tmp_path):
    class Client:
        def __init__(self):
            self.reply = None

        async def invoke(self, channel, params, retry_on_disconnect=True):
            assert channel == "agent:usage-snapshot" and params == {}
            if isinstance(self.reply, Exception):
                raise self.reply
            return self.reply

    class Fleet:
        client_obj = Client()

        def client(self, _host):
            return self.client_obj

    j = task_controller(Journal(tmp_path / "tasks.db"))
    fleet = Fleet()
    adapter = task_bat.BatTaskAdapter(fleet, None, j)
    now_ms = time.time() * 1000

    def snap(five, seven, age_s=0):
        return {"claude": {"fetchedAt": now_ms - age_s * 1000, "fiveHour": {"utilization": five},
                           "sevenDay": {"utilization": seven}}}

    async def agents(reply):
        adapter._agents_cache.clear()
        fleet.client_obj.reply = reply
        return await adapter.available_agents({"host": "h1"})

    assert await agents(snap(0.12, 0.05)) == {"claude", "codex"}
    assert await agents(snap(0.9, 0.05)) == {"codex"}
    assert await agents(snap(0.1, 0.95)) == {"codex"}
    assert await agents(snap(0.1, 0.1, age_s=7200)) == {"codex"}  # stale snapshot is unknown
    assert await agents({"codex": {}}) == {"codex"}
    assert await agents(TimeoutError("host down")) == {"codex"}
    j.provider_use("claude", "quota_error")
    assert await agents(snap(0.12, 0.05)) == {"codex"}
    j.close()


@pytest.mark.skip(reason="removed: independent reviewer, per-step routing, and mid-task failover")
@authenticated_task_test
async def test_real_agents_follow_ted_order_and_record_provider_usage(task_controller, tmp_path):
    journal = task_controller(Journal(tmp_path / "tasks.db"))
    fake = FakeBAT()
    fake.reviewer_kind = "claude"  # Claude available on the host
    task = submit(journal)  # Codex lead
    core = TaskCoordinator(journal, fake)
    await core.tick(task["task_id"])
    lead = journal.get(task["task_id"])["session_id"]
    fake.reads[lead] = {"turn_started": True, "turn_done": True, "turn_attribution": "correlated",
                        "streaming": False, "messages": [{"role": "assistant", "text": "BAT-STATUS: MILESTONE"}]}
    await core.tick(task["task_id"])
    await core.tick(task["task_id"])
    reviewer = journal.get(task["task_id"])["reviewer_session_id"]
    assert fake.starts[-1][:2] == ("reviewer", "claude")  # other family, Opus first
    # A Claude reviewer at its usage limit is not a review outcome: fall back to Codex.
    fake.reads[reviewer] = {"turn_started": True, "turn_done": True, "turn_attribution": "correlated",
                            "first_turn_proven": True,
                            "messages": [{"role": "assistant", "text": "Claude usage limit reached."}]}
    fake.reviewer_kind = "codex"
    after = await core.tick(task["task_id"])
    assert after["state"] == "verifying" and after["reviewer_session_id"] is None
    assert after["review_rejections"] == 0
    await core.tick(task["task_id"])
    assert fake.starts[-1][:2] == ("reviewer", "codex")
    usage = journal.db.execute("SELECT provider,outcome FROM provider_usage ORDER BY usage_id").fetchall()
    assert [tuple(r) for r in usage] == [("codex", "success"), ("claude", "success"),
                                         ("claude", "quota_error"), ("codex", "success")]
    journal.close()


@authenticated_task_test
async def test_claude_lead_falls_back_to_codex_and_reviewer_uses_other_family(task_controller, tmp_path):
    journal = task_controller(Journal(tmp_path / "tasks.db"))
    fake = FakeBAT()  # only Codex available
    task = submit(journal, lead_agent="claude")
    await TaskCoordinator(journal, fake).tick(task["task_id"])
    assert fake.starts[0][:2] == ("lead", "codex")
    assert journal.get(task["task_id"])["lead_agent"] == "codex"
    route = journal.routes(task["task_id"])
    assert route == [] or route[0]["reason"] == "lead_fallback_unavailable"
    available = frozenset({"claude", "codex"})
    assert TaskCoordinator._pick_agent("reviewer", {"lead_agent": "claude"}, available) == (
        "codex", "review_other_family")
    assert TaskCoordinator._pick_agent("reviewer", {"lead_agent": "codex"}, available) == (
        "claude", "review_other_family")
    assert TaskCoordinator._pick_agent("reviewer", {"lead_agent": "codex"}, frozenset({"codex"})) == (
        "codex", "review_same_family_only")
    assert TaskCoordinator._pick_agent("lead", {"lead_agent": "claude"}, available) == ("claude", "lead_agent")
    journal.close()


@authenticated_task_test
async def test_task_initial_send_after_start_ignores_only_start_spacing(task_controller, fleet_factory, mock, tmp_path):
    fleet = fleet_factory(writes=True, orchestrate=True,
                          safety={"write_min_interval_s": 60, "max_writes_per_hour": 100})
    journal = task_controller(Journal(tmp_path / "initial-spacing.db"))
    task = journal.submit(project="p", host="h1", workspace="demo-project", original_words=WORDS,
                          idempotency_key="initial-spacing")
    adapter = task_bat.BatTaskAdapter(fleet, ObservedVerifier(VerificationSettings()), journal)
    session_id = "task-initial-spacing"
    assert await adapter.start(task, role="lead", agent="claude", session_id=session_id) == session_id
    task = journal.change(task["task_id"], "dispatching")
    task = journal.change(task["task_id"], "accepted", fields={"session_id": session_id})
    prompt = "first lead prompt"
    command, _ = journal.command(task["task_id"], "send", session_id,
                                 {"purpose": "lead:initial",
                                  "prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest()},
                                 "initial-spacing:send")
    sent = await adapter.send(task, session_id, prompt, command["message_id"])
    assert sent["accepted"]
    assert len([i for i in mock.invokes if i["channel"] == "claude:send-message"
                and i["params"]["sessionId"] == session_id]) == 1
    with pytest.raises(WriteRefused, match="state is unavailable"):
        await service.session_send(fleet, "h1", session_id, "later prompt", confirm=True)
    (registry.registry_path().parent / service.TASK_SERVICE_POINTER).write_text(
        json.dumps({"db_path": str(journal.path.resolve())}))
    # The raw adapter test has not settled its intent; the shared task gate now runs before rate limiting.
    with pytest.raises(WriteRefused, match="TASK_COMMAND_PENDING"):
        await service.session_send(fleet, "h1", session_id, "later prompt", confirm=True)
    journal.close()
    await fleet.close()


@authenticated_task_test
async def test_task_reservation_excludes_paused_lead_from_legacy_cleanup(task_controller,
        fleet_factory, mock, tmp_path, monkeypatch):
    fleet = fleet_factory(writes=True, orchestrate=True, auto_cleanup=True,
                          safety={"write_min_interval_s": 0})
    journal = task_controller(Journal(tmp_path / "tasks.db"))
    task = journal.submit(project="p", host="h1", workspace="demo-project",
                          original_words=WORDS, idempotency_key="paused-reserved-lead")
    adapter = task_bat.BatTaskAdapter(fleet, ObservedVerifier(VerificationSettings()), journal)
    core = TaskCoordinator(journal, adapter)
    original_start = adapter.start
    started = {}
    original_reserve = registry.reserve
    reserved = []

    def tagged_reservation(host, entry, max_active, replaces=None):
        result = original_reserve(host, entry, max_active, replaces=replaces)
        reserved.append(registry.get(host, entry["session_id"]))
        return result

    async def pause_after_start(*args, **kwargs):
        sid = await original_start(*args, **kwargs)
        started["sid"] = sid
        assert registry.get("h1", sid)["task_id"] == task["task_id"]
        journal.pause(task["task_id"])
        return sid

    monkeypatch.setattr(registry, "reserve", tagged_reservation)
    monkeypatch.setattr(adapter, "start", pause_after_start)
    result = await core.tick(task["task_id"])
    sid = started["sid"]
    assert result["paused"] and result["state"] == "accepted"
    assert reserved and reserved[0]["task_id"] == task["task_id"]
    assert not any(i["channel"] == "claude:send-message" and
                   i["params"].get("sessionId") == sid for i in mock.invokes)
    from bat_agent_connector.operations import OperationError
    with pytest.raises(OperationError, match="LEGACY_CLEANUP_DISABLED") as disabled:
        await lifecycle.session_cleanup(fleet, "h1", session_id=sid, confirm=True, dry_run=False, min_idle_s=0)
    assert disabled.value.status == 409
    decision = await lifecycle.session_cleanup(fleet, "h1", session_id=sid,
                                               dry_run=True, min_idle_s=0)
    assert decision["decisions"][0]["decision"] == "KEEP"
    main = await lifecycle.main_session(fleet, "h1", "demo-project")
    assert not main or main["session_id"] != sid
    assert registry.get("h1", sid)["task_id"] == task["task_id"]
    journal.close()
    await fleet.close()


@authenticated_task_test
async def test_headless_session_lookup_restored_from_task_branch_and_bat_meta(task_controller,
        fleet_factory, mock, tmp_path):
    fleet = fleet_factory(writes=True, orchestrate=True, tabs=False,
                          safety={"write_min_interval_s": 0})
    j = task_controller(Journal(tmp_path / "tasks.db"))
    task = j.submit(project="p", host="h1", workspace="demo-project", original_words=WORDS,
                    idempotency_key="headless-lookup")
    adapter = task_bat.BatTaskAdapter(fleet, ObservedVerifier(VerificationSettings()), j)
    sid = "headless-started-session"
    start, _ = j.command(task["task_id"], "start_lead", sid, {"agent": "claude", "role": "lead"}, "headless-start")
    try:
        await adapter.start(task, role="lead", agent="claude", session_id=sid)
        j.command_status(start["command_id"], "settled")
        j.add_branch(task["task_id"], session_id=sid, provider="claude", role="lead", reason="start")
        j.change(task["task_id"], "dispatching")
        task = j.change(task["task_id"], "accepted", fields={"session_id": sid})
        assert not any(t.get("id") == sid for t in mock.ws_doc["terminals"])
        task_bat.registry.registry_path().unlink()
        assert task_bat.registry.get("h1", sid) is None
        assert await adapter.session_presence(task, sid) == "present"
        assert task_bat.registry.get("h1", sid)["recovered_from"] == "task_journal"
        assert (await adapter.prepare_send(task, sid))["agent_kind"] == "claude"
        prompt = "first prompt"
        command, _ = j.command(task["task_id"], "send", sid,
                               {"prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest()}, "headless-send")
        assert (await adapter.send(task, sid, prompt, command["message_id"]))["accepted"]
        assert len([i for i in mock.invokes if i["channel"] == "claude:send-message"
                    and i["params"]["sessionId"] == sid]) == 1
    finally:
        await fleet.close()
        j.close()


@authenticated_task_test
async def test_bat_presence_distinguishes_orphan_worktree_from_vanished_session(task_controller,
        fleet_factory, mock, tmp_path):
    fleet = fleet_factory(writes=True, orchestrate=True, tabs=False,
                          safety={"write_min_interval_s": 0})
    j = task_controller(Journal(tmp_path / "tasks.db"))
    task = j.submit(project="p", host="h1", workspace="demo-project", original_words=WORDS,
                    idempotency_key="presence-contract")
    adapter = task_bat.BatTaskAdapter(fleet, ObservedVerifier(VerificationSettings()), j)
    sid = "presence-session"
    try:
        await adapter.start(task, role="lead", agent="claude", session_id=sid)
        j.add_branch(task["task_id"], session_id=sid, provider="claude", role="lead", reason="start")
        mock.metas[sid] = None
        assert await adapter.session_presence(task, sid) == "uncertain"
        assert task_bat.registry.get("h1", sid)["status"] == "active"
        mock.worktrees.pop(sid)
        assert await adapter.session_presence(task, sid) == "uncertain"
        assert task_bat.registry.get("h1", sid)["status"] == "active"
    finally:
        await fleet.close()
        j.close()


@authenticated_task_test
async def test_bat_restart_null_meta_and_worktree_never_creates_replacement(task_controller,
        fleet_factory, mock, tmp_path):
    path = tmp_path / "tasks.db"
    fleet = fleet_factory(writes=True, orchestrate=True, tabs=False,
                          safety={"write_min_interval_s": 0})
    j = task_controller(Journal(path))
    task = j.submit(project="p", host="h1", workspace="demo-project", original_words=WORDS,
                    idempotency_key="restart-null-presence")
    adapter = task_bat.BatTaskAdapter(fleet, ObservedVerifier(VerificationSettings()), j)
    sid = "restart-unloaded-session"
    try:
        await adapter.start(task, role="lead", agent="claude", session_id=sid)
        j.add_branch(task["task_id"], session_id=sid, provider="claude", role="lead", reason="start")
        j.change(task["task_id"], "dispatching")
        j.change(task["task_id"], "accepted", fields={"session_id": sid})
        disk_worktree = mock.worktrees.pop(sid)
        loaded_meta = mock.metas[sid]
        mock.metas[sid] = None  # BAT restart unloads runtime; disk worktree remains.
        assert (await TaskCoordinator(j, adapter).tick(task["task_id"]))["state"] == "uncertain"
        assert j.get(task["task_id"])["session_replacements"] == 0
        assert not any(c["kind"] == "send" for c in j.commands(task["task_id"]))
        j.close()
        j = task_controller(Journal(path))
        adapter.journal = j
        core = TaskCoordinator(j, adapter)
        assert (await core.tick(task["task_id"]))["state"] == "uncertain"
        assert len(j.get(task["task_id"])["branches"]) == 1
        mock.worktrees[sid] = disk_worktree
        mock.metas[sid] = loaded_meta
        assert (await core.tick(task["task_id"]))["state"] == "accepted"
        assert (await core.tick(task["task_id"]))["state"] == "running"
        assert j.get(task["task_id"])["session_replacements"] == 0
        assert len([i for i in mock.invokes if i["channel"] == "claude:send-message"
                    and i["params"]["sessionId"] == sid]) == 1
    finally:
        await fleet.close()
        j.close()


@authenticated_task_test
async def test_lead_meta_present_but_bat_worktree_null_blocks_initial_send(task_controller,
        fleet_factory, mock, tmp_path):
    fleet = fleet_factory(writes=True, orchestrate=True, tabs=False,
                          safety={"write_min_interval_s": 0})
    j = task_controller(Journal(tmp_path / "tasks.db"))
    task = j.submit(project="p", host="h1", workspace="demo-project", original_words=WORDS,
                    idempotency_key="null-worktree-loaded-meta")
    adapter = task_bat.BatTaskAdapter(fleet, ObservedVerifier(VerificationSettings()), j)
    sid = "loaded-meta-null-worktree"
    try:
        await adapter.start(task, role="lead", agent="claude", session_id=sid)
        j.add_branch(task["task_id"], session_id=sid, provider="claude", role="lead", reason="start")
        j.change(task["task_id"], "dispatching")
        task = j.change(task["task_id"], "accepted", fields={"session_id": sid})
        saved = task_bat.registry.get("h1", sid)
        assert saved["worktree_path"] and saved["branch"] and mock.metas[sid]
        assert mock.dispatch("git:getRoot", {"cwd": saved["worktree_path"]}) == saved["worktree_path"]
        mock.worktrees.pop(sid)
        assert await adapter.session_presence(task, sid) == "uncertain"
        assert (await TaskCoordinator(j, adapter).tick(task["task_id"]))["state"] == "uncertain"
        assert j.get(task["task_id"])["session_replacements"] == 0
        assert not any(i["channel"] == "claude:send-message" for i in mock.invokes)
    finally:
        await fleet.close()
        j.close()


@authenticated_task_test
async def test_headless_reviewer_restores_from_lead_worktree_when_own_status_null(task_controller,
        fleet_factory, mock, tmp_path):
    fleet = fleet_factory(writes=True, orchestrate=True, tabs=False,
                          safety={"write_min_interval_s": 0})
    j = task_controller(Journal(tmp_path / "tasks.db"))
    task = j.submit(project="p", host="h1", workspace="demo-project", original_words=WORDS,
                    idempotency_key="reviewer-lookup")
    adapter = task_bat.BatTaskAdapter(fleet, ObservedVerifier(VerificationSettings()), j)
    lead_sid, reviewer_sid = "review-lead", "review-headless"
    try:
        await adapter.start(task, role="lead", agent="claude", session_id=lead_sid)
        j.add_branch(task["task_id"], session_id=lead_sid, provider="claude", role="lead", reason="start")
        j.change(task["task_id"], "dispatching")
        task = j.change(task["task_id"], "accepted", fields={"session_id": lead_sid})
        lead_path = task_bat.registry.get("h1", lead_sid)["worktree_path"]
        j.command(task["task_id"], "start_reviewer", reviewer_sid,
                  {"role": "reviewer", "agent": "codex"}, "reviewer-recovery-intent")
        assert await adapter.start(task, role="reviewer", agent="codex", session_id=reviewer_sid) == reviewer_sid
        path = task_bat.registry.registry_path()
        task_bat.registry._write(path, [e for e in task_bat.registry.list_entries()
                                        if e.get("session_id") != reviewer_sid])
        assert task_bat.registry.get("h1", reviewer_sid) is None
        assert await adapter.recover_start(task, role="reviewer", session_id=reviewer_sid)
        task_bat.registry._write(path, [e for e in task_bat.registry.list_entries()
                                        if e.get("session_id") != reviewer_sid])
        j.add_branch(task["task_id"], session_id=reviewer_sid, provider="codex", role="reviewer",
                     reason="start")
        assert await adapter.session_presence(task, reviewer_sid) == "present"
        reviewer = task_bat.registry.get("h1", reviewer_sid)
        assert reviewer["cwd"] == reviewer["worktree_path"] == lead_path
        assert reviewer["lead_session_id"] == lead_sid and reviewer["role"] == "reviewer"
        assert reviewer["agent_preset"] == "codex-agent"
        start = next(c for c in j.commands(task["task_id"]) if c["kind"] == "start_reviewer")
        j.command_status(start["command_id"], "settled")
        task = j.change(task["task_id"], "verifying", fields={"reviewer_session_id": reviewer_sid})
        prompt = "review candidate"
        command, _ = j.command(task["task_id"], "send", reviewer_sid,
                               {"prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest()}, "reviewer-send")
        assert (await adapter.send(task, reviewer_sid, prompt, command["message_id"]))["accepted"]
    finally:
        await fleet.close()
        j.close()


@authenticated_task_test
async def test_existing_registry_row_requires_matching_bat_metadata_and_worktree(task_controller,
        fleet_factory, mock, tmp_path):
    fleet = fleet_factory(writes=True, orchestrate=True, tabs=False,
                          safety={"write_min_interval_s": 0})
    j = task_controller(Journal(tmp_path / "tasks.db"))
    task = j.submit(project="p", host="h1", workspace="demo-project", original_words=WORDS,
                    idempotency_key="stale-row")
    adapter = task_bat.BatTaskAdapter(fleet, ObservedVerifier(VerificationSettings()), j)
    sid = "stale-registry-row"
    try:
        await adapter.start(task, role="lead", agent="claude", session_id=sid)
        j.add_branch(task["task_id"], session_id=sid, provider="claude", role="lead", reason="start")
        good_cwd = mock.metas[sid]["cwd"]
        mock.metas[sid]["cwd"] = "/srv/other"
        assert await adapter.session_presence(task, sid) == "uncertain"
        mock.metas[sid]["cwd"] = good_cwd
        task_bat.registry.update("h1", sid, cwd="/srv/other")
        assert await adapter.session_presence(task, sid) == "uncertain"
        task_bat.registry.update("h1", sid, cwd=good_cwd, worktree_path="/srv/other")
        assert await adapter.session_presence(task, sid) == "uncertain"
        assert not any(i["channel"] == "claude:send-message" for i in mock.invokes)
    finally:
        await fleet.close()
        j.close()


@authenticated_task_test
async def test_start_ack_recovery_uses_reserved_id_when_local_registry_lost(task_controller,
        fleet_factory, mock, tmp_path):
    fleet = fleet_factory(writes=True, orchestrate=True, tabs=False,
                          safety={"write_min_interval_s": 0})
    j = task_controller(Journal(tmp_path / "tasks.db"))
    task = j.submit(project="p", host="h1", workspace="demo-project", original_words=WORDS,
                    idempotency_key="recover-start-lookup")
    adapter = task_bat.BatTaskAdapter(fleet, ObservedVerifier(VerificationSettings()), j)
    sid = "reserved-start-session"
    try:
        j.command(task["task_id"], "start_lead", sid, {"role": "lead", "agent": "claude"},
                  "start-intent-test")
        await adapter.start(task, role="lead", agent="claude", session_id=sid)
        task_bat.registry.registry_path().unlink()
        assert await adapter.recover_start(task, role="lead", session_id=sid)
        assert task_bat.registry.get("h1", sid)["recovered_from"] == "task_journal"
        assert not any(t.get("id") == sid for t in mock.ws_doc["terminals"])
    finally:
        await fleet.close()
        j.close()


@pytest.mark.parametrize("reply", [
    {"ok": True, "sessionId": "different-session"}, {"ok": True},
])
async def test_bat_start_rejects_missing_or_mismatched_session_id(fleet_factory, mock, reply):
    fleet = fleet_factory(writes=True, orchestrate=True, tabs=False,
                          safety={"write_min_interval_s": 0})
    mock.handlers["claude:start-session"] = lambda p: reply
    try:
        with pytest.raises(WriteRefused, match="reserved session ID"):
            await task_bat.orchestrate.session_start(
                fleet, "h1", "demo-project", "claude", confirm=True, prompt=None,
                session_id="reserved-session", retain_on_error=True)
        assert not any(i["channel"] == "claude:send-message" for i in mock.invokes)
    finally:
        await fleet.close()


@pytest.mark.parametrize("reply", [
    {"ok": True, "sessionId": "different-reviewer"}, {"ok": True},
])
@authenticated_task_test
async def test_reviewer_start_rejects_missing_or_mismatched_session_id(task_controller,
        fleet_factory, mock, tmp_path, reply):
    fleet = fleet_factory(writes=True, orchestrate=True, tabs=False,
                          safety={"write_min_interval_s": 0})
    j = task_controller(Journal(tmp_path / "tasks.db"))
    task = j.submit(project="p", host="h1", workspace="demo-project", original_words=WORDS,
                    idempotency_key="review-ack")
    adapter = task_bat.BatTaskAdapter(fleet, ObservedVerifier(VerificationSettings()), j)
    try:
        await adapter.start(task, role="lead", agent="claude", session_id="review-ack-lead")
        j.add_branch(task["task_id"], session_id="review-ack-lead", provider="claude",
                     role="lead", reason="start")
        j.change(task["task_id"], "dispatching", fields={"session_id": "review-ack-lead"})
        task = j.change(task["task_id"], "accepted")
        mock.handlers["claude:start-session"] = lambda p: reply
        with pytest.raises(WriteRefused, match="reviewer start"):
            await adapter.start(task, role="reviewer", agent="codex", session_id="review-ack-reviewer")
        assert task_bat.registry.get("h1", "review-ack-reviewer")["status"] == "uncertain"
        assert not any(i["channel"] == "claude:send-message" for i in mock.invokes)
    finally:
        await fleet.close()
        j.close()


@authenticated_task_test
async def test_vanished_initial_session_one_replacement_and_restart(task_controller, tmp_path):
    class VanishesOnStart(FakeBAT):
        async def start(self, task, *, role, agent, session_id):
            sid = await super().start(task, role=role, agent=agent, session_id=session_id)
            self.vanished_ids.add(sid)
            return sid

    path = tmp_path / "tasks.db"
    j = task_controller(Journal(path))
    task = submit(j)
    fake = VanishesOnStart()
    core = TaskCoordinator(j, fake)
    result = await core.tick(task["task_id"])
    assert result["state"] == "queued" and result["session_replacements"] == 1
    assert result["task_id"] == task["task_id"] and result["original_words"] == WORDS
    assert fake.sends == []
    first = result["branches"][0]["session_id"]
    j.close()
    j = task_controller(Journal(path))
    core = TaskCoordinator(j, fake)
    result = await core.tick(task["task_id"])
    assert result["state"] == "needs_ted" and result["session_replacements"] == 1
    assert [b["session_id"] for b in result["branches"]] == [first, fake.starts[1][2]]
    assert result["branches"][1]["reason"] == "vanished_replacement"
    assert fake.sends == [] and len(fake.starts) == 2
    await core.tick(task["task_id"])
    assert len(fake.starts) == 2
    j.close()


@authenticated_task_test
async def test_vanished_after_uncertain_initial_send_never_replaced(task_controller, tmp_path):
    path = tmp_path / "tasks.db"
    j = task_controller(Journal(path))
    task = submit(j)
    fake = FakeBAT()
    fake.send_error = True
    core = TaskCoordinator(j, fake)
    assert (await core.tick(task["task_id"]))["state"] == "uncertain"
    fake.vanished_ids.add(j.get(task["task_id"])["session_id"])
    j.close()
    j = task_controller(Journal(path))
    core = TaskCoordinator(j, fake)
    await core.tick(task["task_id"])
    assert j.get(task["task_id"])["state"] == "uncertain"
    assert j.get(task["task_id"])["session_replacements"] == 0
    assert len(fake.sends) == 1 and len(fake.starts) == 1
    j.close()


@authenticated_task_test
async def test_start_then_rejected_send_with_vanished_session_replaces_once(task_controller, tmp_path):
    class RejectedAfterStart(FakeBAT):
        async def send(self, task, session_id, text, message_id):
            self.vanished_ids.add(session_id)
            self.sends.append((session_id, text, message_id))
            return {"accepted": False, "reason": "session absent"}

    path = tmp_path / "tasks.db"
    j = task_controller(Journal(path))
    task = submit(j)
    fake = RejectedAfterStart()
    core = TaskCoordinator(j, fake)
    result = await core.tick(task["task_id"])
    assert result["state"] == "queued" and result["session_replacements"] == 1
    assert j.commands(task["task_id"])[-1]["status"] == "rejected"
    assert len(fake.sends) == 1
    j.close()
    j = task_controller(Journal(path))
    result = await TaskCoordinator(j, fake).tick(task["task_id"])
    assert result["state"] == "needs_ted" and result["session_replacements"] == 1
    assert len(fake.sends) == 2 and fake.sends[0][0] != fake.sends[1][0]
    assert fake.sends[0][1] == fake.sends[1][1]  # one fresh command for the new branch
    assert fake.sends[0][2] != fake.sends[1][2]
    j.close()


@authenticated_task_test
async def test_pause_during_final_presence_check_cancels_unsent_command(task_controller, tmp_path):
    class PresenceGateBAT(FakeBAT):
        def __init__(self):
            super().__init__()
            self.probes = 0
            self.entered = asyncio.Event()
            self.release = asyncio.Event()

        async def session_presence(self, task, session_id):
            self.probes += 1
            if self.probes == 2:
                self.entered.set()
                await self.release.wait()
            return "present"

    j = task_controller(Journal(tmp_path / "tasks.db"))
    task = submit(j)
    fake = PresenceGateBAT()
    core = TaskCoordinator(j, fake)
    tick = asyncio.create_task(core.tick(task["task_id"]))
    await asyncio.wait_for(fake.entered.wait(), 2)
    paused = await core.pause(task["task_id"])
    assert paused["paused"]
    fake.release.set()
    result = await asyncio.wait_for(tick, 2)
    assert result["paused"] and fake.sends == []
    sends = [c for c in j.commands(task["task_id"]) if c["kind"] == "send"]
    assert len(sends) == 1 and sends[0]["status"] == "cancelled"
    j.resume(task["task_id"])
    assert (await core.tick(task["task_id"]))["state"] == "running"
    assert len(fake.sends) == 1 and fake.sends[0][2] != sends[0]["message_id"]
    j.close()


@authenticated_task_test
async def test_pause_during_late_session_send_lookup_blocks_bat_frame(task_controller,
        fleet_factory, mock, tmp_path, monkeypatch):
    fleet = fleet_factory(writes=True, orchestrate=True, tabs=False,
                          safety={"write_min_interval_s": 0})
    mock.echo_sends = True
    j = task_controller(Journal(tmp_path / "tasks.db"))
    task = j.submit(project="p", host="h1", workspace="demo-project", original_words=WORDS,
                    idempotency_key="late-send-pause")
    adapter = task_bat.BatTaskAdapter(fleet, ObservedVerifier(VerificationSettings()), j)
    core = TaskCoordinator(j, adapter)
    entered, release = asyncio.Event(), asyncio.Event()
    original = service._live_state
    gated = False

    async def late_lookup(*args):
        nonlocal gated
        if (not gated and any(c["kind"] == "send" and c["status"] == "needs_review"
                              for c in j.commands(task["task_id"]))):
            gated = True
            entered.set()
            await release.wait()
        return await original(*args)

    monkeypatch.setattr(service, "_live_state", late_lookup)
    try:
        tick = asyncio.create_task(core.tick(task["task_id"]))
        await asyncio.wait_for(entered.wait(), 5)
        assert j.pause(task["task_id"])["paused"]
        release.set()
        assert (await asyncio.wait_for(tick, 5))["paused"]
        assert not any(i["channel"] == "claude:send-message" for i in mock.invokes)
        sends = [c for c in j.commands(task["task_id"]) if c["kind"] == "send"]
        assert len(sends) == 1 and sends[0]["status"] == "cancelled"
        j.resume(task["task_id"])
        await core.tick(task["task_id"])
        sent = [i for i in mock.invokes if i["channel"] == "claude:send-message"]
        assert len(sent) == 1 and sent[0]["params"]["clientMessageId"] != sends[0]["message_id"]
    finally:
        release.set()
        await fleet.close()
        j.close()


@pytest.mark.skip(reason="removed: independent reviewer, per-step routing, and mid-task failover")
@authenticated_task_test
async def test_settled_reviewer_start_requires_presence_before_prompt(task_controller, tmp_path):
    j = task_controller(Journal(tmp_path / "tasks.db"))
    task = submit(j)
    fake = FakeBAT()
    core = TaskCoordinator(j, fake)
    await core.tick(task["task_id"])
    lead = j.get(task["task_id"])["session_id"]
    fake.reads[lead] = {"turn_started": True, "turn_done": True, "turn_attribution": "correlated",
                        "messages": [{"role": "assistant", "text": "BAT-STATUS: MILESTONE"}]}
    await core.tick(task["task_id"])
    fake.presence_override = "uncertain"
    result = await core.tick(task["task_id"])
    reviewer = result["reviewer_session_id"]
    assert reviewer and result["state"] == "uncertain"
    assert not any(sid == reviewer for sid, _, _ in fake.sends)
    assert any(c["kind"] == "start_reviewer" and c["status"] == "settled"
               for c in j.commands(task["task_id"]))
    fake.presence_override = None
    assert (await core.tick(task["task_id"]))["state"] == "verifying"
    assert (await core.tick(task["task_id"]))["state"] == "verifying"
    assert len([sid for sid, _, _ in fake.sends if sid == reviewer]) == 1
    j.close()


@authenticated_task_test
async def test_bat_adapter_recovers_codex_successor_without_handoff_turn_proof(task_controller,
        fleet_factory, mock, monkeypatch, tmp_path):
    journal = task_controller(Journal(tmp_path / "tasks.db"))
    task = submit(journal, lead_agent="claude")
    journal.change(task["task_id"], "dispatching")
    journal.change(task["task_id"], "accepted", fields={"session_id": "old-contract"})
    journal.change(task["task_id"], "running")
    task = journal.change(task["task_id"], "quota_limited")
    _, handoff = journal.reserve_failover(task["task_id"], "old-contract", "successor-contract")
    frame_hash = hashlib.sha256(b"contract handoff").hexdigest()
    journal.command_prompt_hash(handoff["command_id"], frame_hash)
    fleet = fleet_factory(writes=True, orchestrate=True)
    path, branch = "/srv/demo/.bat-worktrees/contract", "bat/worktree-contract"
    mock.metas["successor-contract"] = {"cwd": path, "isStreaming": False}
    mock.worktrees["old-contract"] = {"worktreePath": path, "branchName": branch}
    mock.worktrees["successor-contract"] = {"worktreePath": path, "branchName": branch}
    old = {"session_id": "old-contract", "worktree_path": path, "branch": branch}
    successor = {"session_id": "successor-contract", "failover_of": "old-contract",
                 "shares_worktree_with": "old-contract", "status": "active",
                 "task_id": task["task_id"],
                 "worktree_path": path, "branch": branch,
                 "handoff_status": "sent", "handoff_message_id": handoff["message_id"],
                 "handoff_command_id": handoff["command_id"], "handoff_frame_sha256": frame_hash}
    monkeypatch.setattr(task_bat.registry, "get", lambda host, sid: {
        "old-contract": old, "successor-contract": successor}.get(sid))
    adapter = task_bat.BatTaskAdapter(fleet, journal=journal)
    try:
        found = await adapter.recover_failover(task, successor_id="successor-contract",
                                              handoff_message_id=handoff["message_id"],
                                              handoff_command_id=handoff["command_id"])
        assert found == {"session_id": "successor-contract", "marker": handoff["message_id"]}
        successor["handoff_frame_sha256"] = hashlib.sha256(b"different frame").hexdigest()
        assert (await adapter.recover_failover(task, successor_id="successor-contract",
                handoff_message_id=handoff["message_id"], handoff_command_id=handoff["command_id"])) == {
                    "identity_mismatch": True}
        successor["handoff_frame_sha256"] = frame_hash
        mock.worktrees["successor-contract"]["branchName"] = "bat/other"
        assert (await adapter.recover_failover(task, successor_id="successor-contract",
                handoff_message_id=handoff["message_id"], handoff_command_id=handoff["command_id"])) == {
                    "identity_mismatch": True}
        mock.worktrees["successor-contract"]["branchName"] = branch
        successor["handoff_command_id"] = "old-handoff"
        assert (await adapter.recover_failover(task, successor_id="successor-contract",
                handoff_message_id=handoff["message_id"], handoff_command_id=handoff["command_id"])) == {
                    "identity_mismatch": True}
        successor["handoff_command_id"] = handoff["command_id"]
        mock.worktrees["successor-contract"] = None
        assert await adapter.recover_failover(task, successor_id="successor-contract",
                handoff_message_id=handoff["message_id"], handoff_command_id=handoff["command_id"]) is None
        assert not any(i["channel"] == "claude:send-message" for i in mock.invokes)
    finally:
        await fleet.close()
        journal.close()


@pytest.mark.skip(reason="removed: independent reviewer, per-step routing, and mid-task failover")
@authenticated_task_test
async def test_operator_review_pass_requires_current_candidate_and_turn_reference(task_controller, tmp_path):
    j = task_controller(Journal(tmp_path / "tasks.db"))
    task = submit(j)
    fake = FakeBAT()
    core = TaskCoordinator(j, fake)
    await core.tick(task["task_id"])
    lead = j.get(task["task_id"])["session_id"]
    fake.reads[lead] = {"turn_started": True, "turn_done": True, "turn_attribution": "correlated",
                        "messages": [{"role": "assistant", "text": "BAT-STATUS: MILESTONE"}]}
    await core.tick(task["task_id"])
    fake.prepare_kind = "codex"
    await core.tick(task["task_id"])
    reviewer = j.get(task["task_id"])["reviewer_session_id"]
    command = next(c for c in reversed(j.commands(task["task_id"])) if c["kind"] == "send")
    assert command["session_id"] == reviewer and command["status"] == "uncertain"
    cap = j.issue_reconcile_capability(task["task_id"], command["command_id"])
    identity = fake.identity
    with pytest.raises(ValueError, match="turn reference"):
        await core.resolve_command(task["task_id"], command["command_id"], token=cap,
                                   outcome="delivered", actor="operator", source="ticket:review",
                                   evidence="inspected reviewer turn", observed_result="review_pass",
                                   candidate_commit=identity["candidate_commit"], tree_hash=identity["tree_hash"])
    with pytest.raises(ValueError, match="candidate changed"):
        await core.resolve_command(task["task_id"], command["command_id"], token=cap,
                                   outcome="delivered", actor="operator", source="ticket:review",
                                   evidence="inspected reviewer turn", observed_result="review_pass",
                                   turn_ref="bat-message:77", candidate_commit="d" * 40,
                                   tree_hash=identity["tree_hash"])
    done = await core.resolve_command(task["task_id"], command["command_id"], token=cap,
                                      outcome="delivered", actor="operator", source="ticket:review",
                                      evidence="BAT reviewer message 77 explicitly passed candidate",
                                      observed_result="review_pass", turn_ref="bat-message:77",
                                      candidate_commit=identity["candidate_commit"],
                                      tree_hash=identity["tree_hash"])
    assert done["state"] == "done" and done["delivered"]
    assert j.reconciliations(task["task_id"])[0]["turn_ref"] == "bat-message:77"
    j.close()


@pytest.mark.skip(reason="removed: independent reviewer, per-step routing, and mid-task failover")
@authenticated_task_test
async def test_rules_review_verification_failover_pause_and_writer(task_controller, tmp_path):
    j = task_controller(Journal(tmp_path / "tasks.db"))
    task = submit(j, interpretation="Hermes guessed the wrong feature")
    fake = FakeBAT()
    fake.verifier_available = False
    core = TaskCoordinator(j, fake)
    await core.tick(task["task_id"])
    lead = j.get(task["task_id"])["session_id"]
    assert fake.sends[0][1].count(WORDS) == 1
    assert "Hermes guessed the wrong feature" not in fake.sends[0][1]
    assert "tests pass" not in fake.sends[0][1]
    assert j.get(task["task_id"])["interpretation"] == "Hermes guessed the wrong feature"
    fake.reads[lead] = {"turn_started": True, "turn_done": True,
                                "messages": [{"role": "assistant", "text": "working"}]}
    await core.tick(task["task_id"])
    assert j.get(task["task_id"])["continuations"] == 1
    await core.pause(task["task_id"], abort_current=True)
    assert fake.interrupts == 1
    before = len(fake.sends)
    await core.tick(task["task_id"])
    assert len(fake.sends) == before
    j.resume(task["task_id"])
    fake.reads[lead] = {"turn_started": True, "turn_done": True,
                                "messages": [{"role": "assistant", "text": "BAT-STATUS: MILESTONE"}]}
    await core.tick(task["task_id"])
    assert j.get(task["task_id"])["state"] == "verifying"
    await core.tick(task["task_id"])
    assert not any(role == "reviewer" for role, _, _ in fake.starts)
    fake.verifier_available = True
    await core.tick(task["task_id"])
    assert any(role == "reviewer" and agent == "codex" for role, agent, _ in fake.starts)
    reviewer = j.get(task["task_id"])["reviewer_session_id"]
    fake.reads[reviewer] = {"turn_started": True, "turn_done": True, "turn_attribution": "correlated",
                            "first_turn_proven": True,
                            "messages": [{"role": "assistant", "text": review_json(
                                "reject", findings=[{"severity": "medium", "summary": "needs tests"}])}]}
    await core.tick(task["task_id"])
    assert j.get(task["task_id"])["review_rejections"] == 1
    fake.reads[lead] = {"turn_started": True, "turn_done": True,
                                "messages": [{"role": "assistant", "text": "BAT-STATUS: MILESTONE"}]}
    await core.tick(task["task_id"])
    fake.identity = {"candidate_commit": "d" * 40, "tree_hash": "e" * 40, "clean": True}
    fake.reviewer_kind = "claude"
    await core.tick(task["task_id"])
    assert any(role == "reviewer" and agent == "claude" for role, agent, _ in fake.starts)
    reviewer2 = j.get(task["task_id"])["reviewer_session_id"]
    assert reviewer2 != reviewer
    fake.reads[reviewer2] = {"turn_started": True, "turn_done": True, "turn_attribution": "correlated",
                             "first_turn_proven": True,
                             "messages": [{"role": "assistant",
                                           "text": review_json(commit="d" * 40, tree="e" * 40)}]}
    done = await core.tick(task["task_id"])
    assert done["state"] == "done" and done["delivered"]
    assert done["time_to_deliver_s"] is not None
    j.close()


@pytest.mark.skip(reason="removed: independent reviewer, per-step routing, and mid-task failover")
@authenticated_task_test
async def test_review_with_high_defect_findings_without_marker_reworks(task_controller, tmp_path):
    j = task_controller(Journal(tmp_path / "tasks.db"))
    task = submit(j, interpretation="review fallback")
    fake = FakeBAT()
    core = TaskCoordinator(j, fake)
    await core.tick(task["task_id"])
    lead = j.get(task["task_id"])["session_id"]
    fake.reads[lead] = {"turn_started": True, "turn_done": True,
                        "messages": [{"role": "assistant", "text": "BAT-STATUS: MILESTONE"}]}
    await core.tick(task["task_id"])
    fake.verifier_available = True
    await core.tick(task["task_id"])
    reviewer = j.get(task["task_id"])["reviewer_session_id"]
    fake.reads[reviewer] = {"turn_started": True, "turn_done": True,
                            "turn_attribution": "correlated", "first_turn_proven": True,
                            "messages": [{"role": "assistant", "text":
                                "Findings\n- High: native data bypasses the fix\n- Medium: CTA is misleading"}]}
    result = await core.tick(task["task_id"])
    assert result["state"] == "running"
    assert result["review_rejections"] == 1
    assert any("Independent review rejected" in text for _, text, _ in fake.sends)
    j.close()


QUOTED_PASS_THEN_REJECT = ("The template says REVIEW: PASS when everything is fine.\n"
                           + review_json("reject", findings=[{"severity": "high", "summary": "bug"}]))


@pytest.mark.parametrize("messages, reason", [
    ([{"role": "assistant", "text": QUOTED_PASS_THEN_REJECT}], "verdict_conflicting"),
    ([{"role": "assistant", "text": "REVIEW: PASS"}], "verdict_missing"),
    ([{"role": "assistant", "text": REVIEW_PASS}, {"role": "assistant", "text": "done, see above"}],
     "verdict_missing"),
    ([{"role": "assistant", "text": review_json() + "\n" + review_json("reject")}], "verdict_conflicting"),
    ([{"role": "assistant", "text": review_json(commit="f" * 40)}], "verdict_candidate_mismatch"),
    ([{"role": "assistant", "text": review_json(tree="f" * 40)}], "verdict_candidate_mismatch"),
    ([{"role": "assistant", "text": review_json(findings=[{"severity": "High", "summary": "x"}])}],
     "pass_with_high_findings"),
    ([{"role": "assistant", "text": REVIEW_PASS + "\n- High: data loss on retry"}], "pass_with_high_findings"),
    ([{"role": "assistant", "text": '{"verdict": "maybe", "candidate_commit": "' + "a" * 40
       + '", "tree_hash": "' + "b" * 40 + '", "findings": []}'}], "verdict_invalid"),
    ([{"role": "assistant", "text": REVIEW_PASS + " \u2026[+5000 chars]"}], "final_message_truncated"),
])
def test_review_verdict_is_structured_final_message_only(messages, reason):
    from bat_agent_connector.task_core import review_verdict

    verdict = review_verdict({"messages": [{"role": "user", "text": REVIEW_PASS}, *messages]},
                             "a" * 40, "b" * 40)
    assert verdict["verdict"] == "reject" and verdict["reason"] == reason


def test_review_verdict_pass_and_low_findings():
    from bat_agent_connector.task_core import review_verdict

    text = review_json(findings=[{"severity": "low", "summary": "nit"}]) + "\nHigh: none"
    verdict = review_verdict({"messages": [{"role": "assistant", "text": "thinking"},
                                           {"role": "assistant", "text": text}]}, "a" * 40, "b" * 40)
    assert verdict["verdict"] == "pass"
    assert "Independently review" in __import__(
        "bat_agent_connector.task_core", fromlist=["x"]).reviewer_prompt(
        {"original_words": WORDS}, "a" * 40, "b" * 40)


def test_bat_status_uses_last_line_of_final_message():
    from bat_agent_connector.task_core import classify_read

    def read(*texts):
        return {"turn_done": True, "messages": [{"role": "assistant", "text": t} for t in texts]}

    assert classify_read(read("BAT-STATUS: MILESTONE", "still working\nBAT-STATUS: CONTINUE")) == "continue"
    assert classify_read(read("Protocol: end with BAT-STATUS: NEED-HUMAN if blocked.\n"
                              "Implemented.\nBAT-STATUS: MILESTONE")) == "verifying"
    assert classify_read(read("Earlier I wrote BAT-STATUS: MILESTONE\nBAT-STATUS: NEED-HUMAN login")) == "needs_ted"
    assert classify_read(read("BAT-STATUS: MILESTONE", "no marker now")) == "continue"
    assert classify_read(read("BAT-STATUS: MILESTONE \u2026[+900 chars]")) == "continue"


@pytest.mark.skip(reason="removed: independent reviewer, per-step routing, and mid-task failover")
@authenticated_task_test
async def test_quoted_pass_followed_by_reject_reworks_not_delivers(task_controller, tmp_path):
    j = task_controller(Journal(tmp_path / "tasks.db"))
    task = submit(j)
    fake = FakeBAT()
    core = TaskCoordinator(j, fake)
    await core.tick(task["task_id"])
    lead = j.get(task["task_id"])["session_id"]
    fake.reads[lead] = {"turn_started": True, "turn_done": True,
                        "messages": [{"role": "assistant", "text": "BAT-STATUS: MILESTONE"}]}
    await core.tick(task["task_id"])
    await core.tick(task["task_id"])
    reviewer = j.get(task["task_id"])["reviewer_session_id"]
    fake.reads[reviewer] = {"turn_started": True, "turn_done": True, "turn_attribution": "correlated",
                            "first_turn_proven": True,
                            "messages": [{"role": "assistant", "text": QUOTED_PASS_THEN_REJECT}]}
    result = await core.tick(task["task_id"])
    assert result["state"] == "running" and not result["delivered"]
    assert result["review_rejections"] == 1
    assert any(e["kind"] == "review_verdict_rejected" for e in j.events(task["task_id"]))
    j.close()


@pytest.mark.skip(reason="removed: independent reviewer, per-step routing, and mid-task failover")
@authenticated_task_test
async def test_failover_and_single_writer(task_controller, tmp_path):
    j = task_controller(Journal(tmp_path / "tasks.db"))
    task = submit(j, lead_agent="claude")
    fake = FakeBAT()
    fake.failover_allowed = True
    core = TaskCoordinator(j, fake)
    await core.tick(task["task_id"])
    j.change(task["task_id"], "quota_limited")
    successor_task = await core.tick(task["task_id"])
    successor = successor_task["session_id"]
    assert successor != task["session_id"]
    assert successor_task["state"] == "uncertain"
    assert fake.failover_calls == 1
    handoff = next(c for c in j.commands(task["task_id"]) if c["kind"] == "send"
                   and json.loads(c["payload"]).get("purpose") == "failover_handoff")
    cap = j.issue_reconcile_capability(task["task_id"], handoff["command_id"])
    await core.resolve_command(task["task_id"], handoff["command_id"], token=cap,
                               outcome="superseded", actor="operator", source="test:handoff",
                               evidence="synthetic handoff turn inspected", next_prompt="Inspect task state")
    # Two sends to the same BAT session serialize in the daemon.
    current = j.get(task["task_id"])
    await asyncio.gather(core._send(current, successor, "a", "a"),
                         core._send(current, successor, "b", "b"))
    assert fake.max_active_sends == 1
    j.close()


def _kinds(feed):
    return [e["kind"] for e in feed["events"]]


def _force_done(j, task_id, commit="c" * 40, result="Merged https://github.com/o/r/pull/7 after review"):
    with j.tx():
        j.db.execute("UPDATE tasks SET state='done',verification_commit=?,result=? WHERE task_id=?",
                     (commit, result, task_id))
        j._event(task_id, "delivered", {"from": "verifying", "to": "done"})


@authenticated_task_test
async def test_event_feed_returns_only_milestones_with_monotonic_cursor(task_controller, tmp_path):
    j = task_controller(Journal(tmp_path / "tasks.db"))
    task = submit(j, discord_thread_id="origin-1")
    tid = task["task_id"]
    j.change(tid, "dispatching")
    j.change(tid, "accepted")
    j.change(tid, "running")
    j.change(tid, "verifying")
    j.change(tid, "verifying", event="verification_stability_wait")
    j.change(tid, "needs_ted", fields={"result": "Which license should the package use?"})
    j.change(tid, "running", event="ted_answered")
    j.change(tid, "verifying")
    j.change(tid, "accepted")  # rework is not a new start
    j.change(tid, "verifying")
    _force_done(j, tid)
    feed = j.milestones(0, 50)
    assert _kinds(feed) == ["started", "needs_ted", "started", "done"]
    cursors = [e["cursor"] for e in feed["events"]]
    assert cursors == sorted(cursors) and len(set(cursors)) == 4
    started, needs, resumed, done = feed["events"]
    assert started["origin_thread_id"] == "origin-1" and started["project"] == "p"
    assert started["workspace"] == "w" and started["task_id"] == tid and not started["resumed"]
    assert needs["reason"] == "Which license should the package use?"
    assert needs["summary"].startswith("Needs user input: Which license")
    assert resumed["resumed"] and resumed["reason_code"] == "ted_answered"
    assert done["commit"] == "c" * 40 and done["pr_url"] == "https://github.com/o/r/pull/7"
    assert done["link"] == done["pr_url"] and done["title"] == WORDS.splitlines()[0]
    assert feed["next_cursor"] == feed["head_cursor"] == j.head_cursor() and not feed["has_more"]
    # Paging: a persisted cursor never repeats an event.
    first = j.milestones(0, 1)
    assert _kinds(first) == ["started"] and first["has_more"]
    rest = j.milestones(first["next_cursor"], 50)
    assert _kinds(rest) == ["needs_ted", "started", "done"]
    assert j.milestones(rest["next_cursor"], 50)["events"] == []
    # limit=0 lets a new reader start from now without reading history.
    empty = j.milestones(0, 0)
    assert empty["events"] == [] and empty["head_cursor"] == j.head_cursor()
    with pytest.raises(ValueError):
        j.milestones(-1, 10)
    with pytest.raises(ValueError):
        j.milestones(0, 201)
    j.close()


@authenticated_task_test
async def test_event_feed_failed_and_needs_ted_reasons(task_controller, tmp_path):
    j = task_controller(Journal(tmp_path / "tasks.db"))
    task = submit(j)
    tid = task["task_id"]
    j.change(tid, "dispatching")
    j.change(tid, "accepted")
    j.change(tid, "running")
    j.request_ted(tid, "Need production credentials decision")
    j.change(tid, "needs_ted")
    j.change(tid, "failed", fields={"result": "Ted cancelled the task"})
    feed = j.milestones(0, 50)
    assert _kinds(feed) == ["started", "needs_ted", "failed"]
    assert feed["events"][1]["reason"] == "Need production credentials decision"
    assert feed["events"][2]["reason"] == "Ted cancelled the task"
    assert feed["events"][2]["summary"] == "Failed: Ted cancelled the task"
    assert feed["events"][0]["origin_thread_id"] is None
    j.close()


@authenticated_task_test
async def test_event_feed_uses_task_result_for_legacy_transition_without_reason(task_controller, tmp_path):
    j = task_controller(Journal(tmp_path / "tasks.db"))
    tid = submit(j)["task_id"]
    with j.tx():
        j.db.execute("UPDATE tasks SET state='needs_ted',result='Legacy blocker text' WHERE task_id=?", (tid,))
        j._event(tid, "verification_timeout", {"from": "verifying", "to": "needs_ted"})
    [event] = j.milestones(0, 50)["events"]
    assert event["kind"] == "needs_ted" and event["reason"] == "Legacy blocker text"
    assert event["reason_code"] == "verification_timeout"
    j.close()


@authenticated_task_test
async def test_legacy_chat_outbox_is_dropped_on_open(task_controller, tmp_path):
    path = tmp_path / "tasks.db"
    j = task_controller(Journal(path))
    submit(j, discord_thread_id="origin-1")
    j.db.execute("ALTER TABLE events ADD COLUMN discord_message_id TEXT")
    j.db.execute("ALTER TABLE events ADD COLUMN discord_status TEXT NOT NULL DEFAULT 'pending'")
    j.db.execute("CREATE TABLE board (board_key TEXT PRIMARY KEY, message_id TEXT, rendered TEXT, "
                 "status TEXT NOT NULL DEFAULT 'pending')")
    before = j.head_cursor()
    j.close()
    j = task_controller(Journal(path))
    columns = {r[1] for r in j.db.execute("PRAGMA table_info(events)")}
    assert not {"discord_message_id", "discord_status"} & columns
    assert not j.db.execute("SELECT 1 FROM sqlite_master WHERE name='board'").fetchone()
    assert j.head_cursor() == before and j.events()[0]["kind"] == "submitted"
    j.close()
    # Reopening an already-migrated journal is a no-op.
    task_controller(Journal(path)).close()


@authenticated_task_test
async def test_daemon_and_mcp_expose_read_only_event_feed(task_controller, mock, tmp_path, monkeypatch):
    settings = tmp_path / "task-settings.toml"
    settings.write_text('[task_service]\nrepo_urls = { p = "https://github.com/o/p" }\n')
    settings.chmod(0o600)
    monkeypatch.setenv("BATC_TASK_SETTINGS", str(settings))
    assert load_settings().repo_urls == {"p": "https://github.com/o/p"}
    daemon = task_controller(TaskDaemon(make_config(mock, writes=True, orchestrate=True), tmp_path / "tasks.db"))
    try:
        tid = submit(daemon.journal, discord_thread_id="origin-9")["task_id"]
        daemon.journal.change(tid, "dispatching")
        daemon.journal.change(tid, "accepted")
        _force_done(daemon.journal, tid, result="verified")
        feed = await daemon.call("work_events", {"since_cursor": 0, "limit": 10}, principal=TASK_CALLER)
        assert _kinds(feed) == ["started", "done"]
        assert feed["events"][1]["commit_url"] == "https://github.com/o/p/commit/" + "c" * 40
        assert feed["events"][1]["link"] == feed["events"][1]["commit_url"]
        assert (await daemon.call("work_events", {"since_cursor": feed["next_cursor"]}, principal=TASK_CALLER))["events"] == []
        assert not hasattr(daemon, "publisher")
    finally:
        await daemon.fleet.close()
        daemon.journal.close()
    seen = {}

    def fake_request(method, **params):
        seen.update(method=method, **params)
        return {"events": [], "next_cursor": 5, "head_cursor": 5, "has_more": False}

    monkeypatch.setattr(mcp_server, "task_request", fake_request)
    server, fleet = mcp_server.build_server(make_config(mock))
    tools = {t.name: t for t in await server.list_tools()}
    assert tools["work_events"].annotations.read_only_hint
    await server.call_tool("work_events", {"since_cursor": 3, "limit": 7})
    assert seen == {"method": "work_events", "since_cursor": 3, "limit": 7}
    await fleet.close()


async def test_service_has_no_chat_publisher():
    import importlib.util

    assert importlib.util.find_spec("bat_agent_connector.task_discord") is None
    src = Path(mcp_server.__file__).parent
    for name in ("task_daemon.py", "task_journal.py", "task_core.py", "cli.py"):
        text = (src / name).read_text()
        assert "BATC_DISCORD" not in text and "discord.com" not in text


class FakeJev:
    def __init__(self, choice="planning", confidence=0.5):
        self.choice = choice
        self.confidence = confidence

    async def ask(self, state, questions):
        if self.choice is None:
            return None
        return {"step_type": {"choice": self.choice, "confidence": self.confidence}}


@authenticated_task_test
async def test_router_cap_quota_and_explicit_rules(task_controller, tmp_path):
    j = task_controller(Journal(tmp_path / "tasks.db"))
    task = submit(j)
    tid = task["task_id"]
    catalog = ProviderCatalog([
        ProviderEntry("agy-claude", "agy-shim", "http://127.0.0.1:18795/v1", "claude-opus-4-6-thinking", 10),
        ProviderEntry("codex", "codex-acp"), ProviderEntry("claude", "claude-acp", model="claude-opus-5-5"),
        ProviderEntry("agy-gemini-flash", "agy-shim", "http://127.0.0.1:18795/v1", "gemini", 0),
    ])
    router = ModelRouter(j, RouterConfig(allow_gemini_status=True), catalog)
    assert (await router.choose(tid, "update", "status", expected_type="status_relay"))["provider"] == \
        "agy-gemini-flash"
    router = ModelRouter(j, RouterConfig(agy_claude_daily_cap=1), catalog)
    assert (await router.choose(tid, "review", "candidate", expected_type="review"))["provider"] == "claude"
    router.record_provider_result("claude", "quota_error")
    assert (await router.choose(tid, "review2", "candidate", expected_type="review"))["provider"] == "agy-claude"
    router.record_provider_result("agy-claude", "success")
    third = await router.choose(tid, "review3", "candidate", expected_type="review")
    assert third["provider"] == "codex" and third["reason"] == "rule_high_stakes_claude_exhausted"
    routine = await ModelRouter(j).choose(tid, "impl", "edit", expected_type="implementation")
    assert routine["provider"] == "codex" and routine["reason"] == "rule_implementation_default"
    assert len(j.routes(tid)) == 5
    assert all(r["confidence"] is None and r["jev_backend"] is None for r in j.routes(tid))
    assert all(e["kind"] == "model_route" for e in j.events(tid)[-5:])
    j.close()


@authenticated_task_test
async def test_router_has_no_classifier_and_requires_known_step_type(task_controller, tmp_path):
    journal = task_controller(Journal(tmp_path / "tasks.db"))
    task = submit(journal)
    router = ModelRouter(journal)
    assert not hasattr(router, "classifier") and not hasattr(router, "prescreen")
    with pytest.raises(ValueError, match="unknown PM step type"):
        await router.choose(task["task_id"], "x", "x", expected_type="unclassified")
    with pytest.raises(TypeError):
        await router.choose(task["task_id"], "x", "x")
    journal.close()


@authenticated_task_test
async def test_router_provider_override_and_recipe_precedence(task_controller, tmp_path, monkeypatch):
    journal = task_controller(Journal(tmp_path / "tasks.db"))
    task = submit(journal, engine="goose")
    router = ModelRouter(journal)
    choice = await router.choose(task["task_id"], "planning:recipe", "plan",
                                 expected_type="planning", provider_override="claude")
    assert choice["provider"] == "claude" and choice["reason"] == "task_or_recipe_override"
    assert (await router.choose(task["task_id"], "planning:recipe", "plan",
                                expected_type="planning")) == choice
    monkeypatch.setattr(goose_acp, "load", lambda _name: {
        "instructions": "plan", "prompt": "do it", "pm_provider": "claude"})
    seen = []

    async def fake_run(*args, **kwargs):
        seen.append(kwargs["provider_id"])
        return {"stop_reason": "end_turn"}

    goose = GooseACP()
    monkeypatch.setattr(goose, "run", fake_run)
    await goose.run_task({**task, "_route_provider": "codex"}, str(tmp_path), capability="synthetic")
    assert seen == ["claude"]
    journal.close()


@pytest.mark.skip(reason="removed: independent reviewer, per-step routing, and mid-task failover")
@authenticated_task_test
async def test_pause_while_jev_routes_start_does_not_start_bat(task_controller, tmp_path):
    class SlowRouter:
        def __init__(self):
            self.entered = asyncio.Event()
            self.release = asyncio.Event()

        async def choose(self, *args, **kwargs):
            self.entered.set()
            await self.release.wait()
            return {"provider": "codex"}

    journal = task_controller(Journal(tmp_path / "tasks.db"))
    task = submit(journal)
    fake = FakeBAT()
    router = SlowRouter()
    core = TaskCoordinator(journal, fake, router=router)
    tick = asyncio.create_task(core.tick(task["task_id"]))
    await asyncio.wait_for(router.entered.wait(), 2)
    assert (await core.pause(task["task_id"]))["paused"]
    router.release.set()
    assert (await asyncio.wait_for(tick, 2))["paused"]
    assert fake.starts == [] and fake.sends == []
    journal.close()


@pytest.mark.skip(reason="removed: independent reviewer, per-step routing, and mid-task failover")
@authenticated_task_test
async def test_rules_route_each_pm_phase_by_rule_without_prescreen(task_controller, tmp_path):
    journal = task_controller(Journal(tmp_path / "tasks.db"))
    task = submit(journal)
    catalog = ProviderCatalog([
        ProviderEntry("agy-claude", "agy-shim", "http://127.0.0.1:18796/v1",
                      "claude-opus-4-6-thinking", 10),
        ProviderEntry("codex", "codex-acp"), ProviderEntry("claude", "claude-acp", model="claude-opus-5-5"),
        ProviderEntry("agy-gemini-flash", "agy-shim", "http://127.0.0.1:18796/v1",
                      "gemini-flash-test"),
    ])
    router = ModelRouter(journal, RouterConfig(), catalog)
    fake = FakeBAT()
    fake.reviewer_kind = "claude"
    core = TaskCoordinator(journal, fake, router=router)
    await core.tick(task["task_id"])
    lead = journal.get(task["task_id"])["session_id"]
    fake.reads[lead] = {"turn_started": True, "turn_done": True,
                        "turn_attribution": "correlated", "streaming": False,
                        "messages": [{"role": "assistant", "text": "BAT-STATUS: MILESTONE"}]}
    await core.tick(task["task_id"])
    await core.tick(task["task_id"])
    reviewer = journal.get(task["task_id"])["reviewer_session_id"]
    assert reviewer and reviewer != lead
    fake.reads[reviewer] = {"turn_started": True, "turn_done": True,
                            "turn_attribution": "correlated", "first_turn_proven": True,
                            "messages": [{"role": "assistant", "text": REVIEW_PASS}]}
    assert (await core.tick(task["task_id"]))["state"] == "done"
    routes = journal.routes(task["task_id"])
    # Session starts record the agent that really runs: the Codex lead and a
    # Claude reviewer from the other family; the rest are explicit rules.
    assert {(r["step_type"], r["provider"], r["reason"]) for r in routes} == {
        ("planning", "codex", "lead_agent"), ("review", "claude", "review_other_family"),
        ("review", "claude", "session_agent"), ("implementation", "codex", "session_agent")}
    starts = [(b["role"], b["provider"]) for b in journal.get(task["task_id"])["branches"]]
    assert starts == [("lead", "codex"), ("reviewer", "claude")]
    assert not any(e["kind"] == "jev_prescreen" for e in journal.events(task["task_id"]))
    journal.close()


@authenticated_task_test
async def test_work_status_and_result_make_no_routing_call(task_controller, mock, tmp_path):
    daemon = task_controller(TaskDaemon(make_config(mock), db_path=tmp_path / "tasks.db"))
    task = submit(daemon.journal)

    async def forbidden(*_args, **_kwargs):
        raise AssertionError("status reads must not route or classify")

    daemon.router.choose = forbidden
    daemon.minimal_router.choose = forbidden
    first = await daemon.call("work_status", {"task_id": task["task_id"]}, principal=TASK_CALLER)
    await daemon.call("work_result", {"task_id": task["task_id"]}, principal=TASK_CALLER)
    second = await daemon.call("work_status", {"task_id": task["task_id"]}, principal=TASK_CALLER)
    assert first["routing_metrics"] == second["routing_metrics"] == {"count": 0, "by_provider": {}}
    assert daemon.journal.routes(task["task_id"]) == []
    await daemon.fleet.close()
    daemon.journal.close()


async def test_mcp_work_submit_returns_without_bat(mock, monkeypatch):
    seen = {}

    def fake_request(method, **params):
        seen.update(method=method, **params)
        return {"task_id": "task-1", "state": "queued"}

    monkeypatch.setattr(mcp_server, "task_request", fake_request)
    server, fleet = mcp_server.build_server(make_config(mock, writes=True, orchestrate=True))
    result = await server.call_tool("work_submit", {"project": "p", "host": "h1", "workspace": "w",
                                                    "original_words": WORDS, "idempotency_key": "discord:1"})
    assert "task-1" in json.dumps(result.model_dump(), default=str)
    assert seen["original_words"] == WORDS and seen["method"] == "work_submit"
    await fleet.close()


@authenticated_task_test
async def test_daemon_uses_configured_minimal_default_and_standard_opt_out(task_controller, mock, tmp_path, monkeypatch):
    monkeypatch.setenv("BATC_TASK_DEFAULT_PATH", "minimal")
    daemon = task_controller(TaskDaemon(make_config(mock, writes=True, orchestrate=True), tmp_path / "tasks.db"))
    try:
        minimal = await daemon.call("work_submit", {
            "project": "p", "host": "h1", "workspace": "w", "original_words": WORDS,
            "idempotency_key": "default:minimal"}, principal=TASK_CALLER)
        assert daemon.journal.get(minimal["task_id"])["task_path"] == "minimal"
        standard = await daemon.call("work_submit", {
            "project": "p", "host": "h1", "workspace": "w", "original_words": WORDS,
            "idempotency_key": "default:standard", "task_path": "standard"}, principal=TASK_CALLER)
        assert daemon.journal.get(standard["task_id"])["task_path"] == "standard"
    finally:
        await daemon.fleet.close()
        daemon.journal.close()


@authenticated_task_test
async def test_daemon_submit_is_journal_only(task_controller, mock, tmp_path):
    daemon = task_controller(TaskDaemon(make_config(mock, writes=True, orchestrate=True), tmp_path / "tasks.db"))
    daemon.acquire_owner()  # owner-first initialization precedes the timed request, as in serve().
    try:
        result = await asyncio.wait_for(daemon.call("work_submit", {
            "project": "p", "host": "h1", "workspace": "w", "original_words": WORDS,
            "idempotency_key": "discord:message:1"}, principal=TASK_CALLER), timeout=0.5)
        assert result["state"] == "queued"
        assert daemon.journal.get(result["task_id"])["original_words"] == WORDS
        assert not mock.invokes
    finally:
        await daemon.fleet.close()
        daemon.journal.close()


@authenticated_task_test
async def test_submit_enters_one_goose_session_without_jev(task_controller, mock, tmp_path):
    class NoJev:
        async def ask(self, *_args, **_kwargs):
            raise AssertionError("submit makes no model call")
        async def ask_with_model(self, *_args, **_kwargs):
            raise AssertionError("submit makes no model call")

    daemon = task_controller(TaskDaemon(make_config(mock, writes=True, orchestrate=True), tmp_path / "tasks.db"))
    daemon.recipe_chooser = NoJev()  # ignored; nothing calls it
    daemon.minimal_router = NoJev()
    parent = await daemon.call("work_submit", {
        "project": "p", "host": "h1", "workspace": "w", "original_words": WORDS,
        "idempotency_key": "goose:parent", "task_path": "minimal"}, principal=TASK_CALLER)
    try:
        assert parent["engine"] == "goose" and parent["goose"] == "disabled" and parent["state"] == "queued"
        assert daemon.journal.get(parent["task_id"])["recipe"] == "goose-session"
        assert daemon.journal.engine_decision(parent["task_id"])["reason"] == "goose_session"
        follow = await daemon.call("work_submit", {
            "project": "p", "host": "h1", "workspace": "w", "original_words": "adjust the button only",
            "idempotency_key": "goose:follow", "task_path": "minimal",
            "parent_task_id": parent["task_id"], "continuation": True}, principal=TASK_CALLER)
        repeat = await daemon.call("work_submit", {
            "project": "p", "host": "h1", "workspace": "w", "original_words": "adjust the button only",
            "idempotency_key": "goose:follow", "task_path": "minimal",
            "parent_task_id": parent["task_id"], "continuation": True}, principal=TASK_CALLER)
        assert follow["task_id"] == parent["task_id"] and follow["continuation"] is True
        assert repeat["task_id"] == parent["task_id"]
        assert sum(e["kind"] == "continuation" for e in daemon.journal.events(parent["task_id"])) == 1
        assert daemon.journal.routes(parent["task_id"]) == []
    finally:
        await daemon.fleet.close()
        daemon.journal.close()


@authenticated_task_test
async def test_presplit_executor_is_the_only_jev_call_and_skips_opus(task_controller, mock, tmp_path):
    class OnlyHere:
        calls = 0
        async def ask(self, state, questions):
            self.calls += 1
            assert state["executor_model"] == "grok"
            assert set(questions) == {"executor"}
            return {"executor": {"type": "choice", "choice": "accept", "confidence": 1.0,
                                 "probabilities": {"accept": 1.0, "reject": 0.0}}}

    daemon = task_controller(TaskDaemon(make_config(mock, writes=True, orchestrate=True), tmp_path / "tasks.db"))
    jev = OnlyHere()
    daemon.jev = jev
    try:
        normal = await daemon.call("work_submit", {
            "project": "p", "host": "h1", "workspace": "w", "original_words": WORDS,
            "idempotency_key": "plain", "task_path": "minimal"}, principal=TASK_CALLER)
        assert jev.calls == 0 and daemon.journal.get(normal["task_id"])["pm_provider"] is None
        split = await daemon.call("work_submit", {
            "project": "p", "host": "h1", "workspace": "w", "original_words": WORDS,
            "idempotency_key": "split", "task_path": "minimal", "executor_model": "grok"}, principal=TASK_CALLER)
        assert jev.calls == 1
        row = daemon.journal.get(split["task_id"])
        assert row["pm_provider"] == "grok" and row["engine"] == "goose"
        assert daemon.journal.engine_decision(split["task_id"])["reason"] == "presplit_grok"
    finally:
        await daemon.fleet.close()
        daemon.journal.close()


@authenticated_task_test
async def test_quota_limited_stops_at_needs_ted(task_controller, tmp_path):
    j = task_controller(Journal(tmp_path / "quota.db"))
    task = submit(j)
    for state in ("dispatching", "accepted", "running", "quota_limited"):
        j.change(task["task_id"], state)
    result = await TaskCoordinator(j, FakeBAT()).tick(task["task_id"])
    assert result["state"] == "needs_ted"
    assert "not failing over" in result["result"]
    j.close()


async def test_minimal_rejects_invalid_typed_engine_answer():
    class Invalid:
        backend = "typesafe"

        async def ask(self, _state, questions):
            assert set(questions) == {"engine"}
            return {"engine": {"type": "choice", "choice": "goose", "confidence": 0.9,
                               "probabilities": {"goose": 0.9}}}

    choice = await MinimalTaskRouter(Invalid()).choose(project="p", recipe="feature-to-staging",
                                                        original_words=WORDS)
    assert choice["selected"] == "rules_engine" and choice["jev_backend"] is None


@authenticated_task_test
async def test_minimal_goose_choice_is_pluggable_only_after_explicit_live_gate(task_controller, mock, tmp_path):
    class GooseChoice:
        backend = "openrouter_jev"

        async def ask(self, _state, questions):
            assert set(questions) == {"engine"}
            return {"engine": {"type": "choice", "choice": "goose", "confidence": 1.0,
                               "probabilities": {"rules_engine": 0.0, "goose": 1.0}}}

    daemon = task_controller(TaskDaemon(make_config(mock, writes=True, orchestrate=True), tmp_path / "tasks.db"))
    daemon.goose = GooseACP(GooseConfig(enabled=True))  # synthetic contract; no live process launched
    daemon.minimal_router = MinimalTaskRouter(GooseChoice())
    try:
        result = await daemon.call("work_submit", {
            "project": "p", "host": "h1", "workspace": "w", "original_words": WORDS,
            "idempotency_key": "minimal:goose-gated", "task_path": "minimal"}, principal=TASK_CALLER)
        assert result["engine"] == "goose"
        decision = daemon.journal.engine_decision(result["task_id"])
        assert decision["jev_backend"] is None and decision["reason"] == "goose_session"
        assert daemon.goose.catalog.order == ("claude", "agy-claude", "codex")
    finally:
        await daemon.fleet.close()
        daemon.journal.close()


@authenticated_task_test
async def test_minimal_small_task_uses_observed_tests_without_reviewer_or_step_jev(task_controller, tmp_path):
    class NoStepRouter:
        async def choose(self, *_args, **_kwargs):
            raise AssertionError("minimal path must not route PM steps")

        async def prescreen(self, *_args, **_kwargs):
            raise AssertionError("minimal path must not prescreen")

    journal = task_controller(Journal(tmp_path / "tasks.db"))
    task = journal.submit(project="p", host="h1", workspace="w", original_words=WORDS,
                          recipe="small-task-with-tests", task_path="minimal",
                          engine_decision={"selected": "rules_engine", "effective": "rules",
                                           "confidence": 0.9, "jev_backend": "typesafe"},
                          idempotency_key="minimal:small")
    fake = FakeBAT()
    fake.review_diff = {"diff": "diff --git a/README.md b/README.md\n+one line\n",
                        "paths": ["README.md"]}

    class PassingJev:
        backend = "typesafe"
        calls = 0

        async def ask(self, state, questions):
            self.calls += 1
            assert state["original_words"] == WORDS
            assert "+one line" in state["candidate_diff"]
            assert list(questions) == ["review_gate"]
            return {"review_gate": {"type": "choice", "choice": "pass", "confidence": 0.95,
                                    "probabilities": {"pass": 0.95, "fail": 0.02,
                                                      "risk": 0.02, "unsure": 0.01}}}

    jev = PassingJev()
    core = TaskCoordinator(journal, fake, router=NoStepRouter(),
                           minimal_review_gate=MinimalReviewGate(jev, RouterConfig()))
    await core.tick(task["task_id"])
    session_id = journal.get(task["task_id"])["session_id"]
    fake.reads[session_id] = {"turn_started": True, "turn_done": True,
                              "turn_attribution": "correlated", "streaming": False,
                              "messages": [{"role": "assistant", "text": "BAT-STATUS: MILESTONE"}]}
    assert (await core.tick(task["task_id"]))["state"] == "verifying"
    result = await core.tick(task["task_id"])
    assert result["state"] == "done" and result["delivered"]
    assert result["review_passed"] is False and result["reviewer_session_id"] is None
    assert jev.calls == 0  # the review gate is not on the normal path
    assert journal.minimal_review_gate(task["task_id"], "a" * 40, "b" * 40) is None
    assert not any(e["kind"] == "minimal_review_decision" for e in journal.events(task["task_id"]))
    assert [role for role, _agent, _sid in fake.starts] == ["lead"]
    assert journal.observed_verification(task["task_id"])["exit_code"] == 0
    assert journal.routes(task["task_id"]) == []
    assert not any(e["kind"] == "jev_prescreen" for e in journal.events(task["task_id"]))
    journal.close()


async def test_minimal_review_threshold_accepts_calibrated_floor():
    class BorderlineJev:
        backend = "typesafe"

        async def ask(self, _state, _questions):
            return {"review_gate": {"type": "choice", "choice": "pass", "confidence": 0.50,
                                    "probabilities": {"pass": 0.65, "fail": 0.25,
                                                      "risk": 0.05, "unsure": 0.05}}}

    result = await MinimalReviewGate(BorderlineJev(), RouterConfig()).judge(
        original_words="small README task", diff="diff --git a/README.md b/README.md\n+one line\n",
        paths=["README.md"])
    assert result == {"verdict": "pass", "confidence": 0.50, "jev_backend": "typesafe", "reason": "jev_pass"}


@pytest.mark.parametrize(("choice", "confidence", "paths", "answer", "reason"), [
    ("pass", 0.49, ["README.md"], True, "low_confidence"),
    ("fail", 0.95, ["README.md"], True, "jev_fail"),
    ("pass", 0.95, ["src/auth/login.py"], True, "sensitive_path"),
    ("pass", 0.95, ["README.md"], False, "jev_unavailable_or_invalid"),
])
@pytest.mark.skip(reason="removed: independent reviewer, per-step routing, and mid-task failover")
@authenticated_task_test
async def test_minimal_review_escalates_to_cross_agent(task_controller, tmp_path, choice, confidence, paths, answer, reason):
    class ReviewJev:
        backend = "openrouter_jev" if answer else None
        calls = 0

        async def ask(self, state, questions):
            self.calls += 1
            assert state["original_words"] == WORDS
            assert list(questions) == ["review_gate"]
            if not answer:
                return None
            rest = (1 - confidence) / 3
            return {"review_gate": {"type": "choice", "choice": choice,
                                    "confidence": confidence,
                                    "probabilities": {key: confidence if key == choice else rest
                                                      for key in ("pass", "fail", "risk", "unsure")}}}

    journal = task_controller(Journal(tmp_path / "escalate.db"))
    task = journal.submit(project="p", host="h1", workspace="w", original_words=WORDS,
                          recipe="small-task-with-tests", task_path="minimal",
                          engine_decision={"selected": "rules_engine", "effective": "rules"},
                          idempotency_key="minimal:escalate")
    fake = FakeBAT()
    fake.review_diff = {"diff": "diff --git a/x b/x\n+changed\n", "paths": paths}
    jev = ReviewJev()
    core = TaskCoordinator(journal, fake, minimal_review_gate=MinimalReviewGate(jev, RouterConfig()))
    await core.tick(task["task_id"])
    sid = journal.get(task["task_id"])["session_id"]
    fake.reads[sid] = {"turn_started": True, "turn_done": True, "turn_attribution": "correlated",
                       "streaming": False, "messages": [{"role": "assistant", "text": "BAT-STATUS: MILESTONE"}]}
    await core.tick(task["task_id"])
    result = await core.tick(task["task_id"])
    assert result["state"] == "verifying" and not result["delivered"]
    assert result["reviewer_session_id"] is not None
    assert [role for role, _agent, _sid in fake.starts] == ["lead", "reviewer"]
    gate = journal.minimal_review_gate(task["task_id"], "a" * 40, "b" * 40)
    assert gate["verdict"] == "escalate" and gate["reason"] == reason
    assert jev.calls == (0 if reason == "sensitive_path" else 1)
    reviewer_id = result["reviewer_session_id"]
    fake.reads[reviewer_id] = {"turn_started": True, "turn_done": True,
                               "first_turn_proven": True, "turn_attribution": "correlated",
                               "streaming": False,
                               "messages": [{"role": "assistant", "text": REVIEW_PASS}]}
    assert (await core.tick(task["task_id"]))["state"] == "done"
    journal.close()


@pytest.mark.skip(reason="removed: independent reviewer, per-step routing, and mid-task failover")
@authenticated_task_test
async def test_minimal_review_large_diff_and_pending_restart_escalate_without_jev(task_controller, tmp_path):
    class NoJev:
        backend = None
        calls = 0

        async def ask(self, *_args):
            self.calls += 1
            raise AssertionError("large or pending candidate must not call Jev")

    jev = NoJev()
    gate = MinimalReviewGate(jev, RouterConfig())
    too_large = await gate.judge(original_words=WORDS, diff="x" * 3501, paths=["README.md"])
    assert too_large["reason"] == "diff_too_large" and jev.calls == 0
    journal = task_controller(Journal(tmp_path / "pending.db"))
    task = journal.submit(project="p", host="h1", workspace="w", original_words=WORDS,
                          recipe="small-task-with-tests", task_path="minimal",
                          engine_decision={"selected": "rules_engine", "effective": "rules"},
                          idempotency_key="minimal:pending")
    fake = FakeBAT()
    fake.review_diff = {"diff": "diff --git a/README.md b/README.md\n+line", "paths": ["README.md"]}
    core = TaskCoordinator(journal, fake, minimal_review_gate=gate)
    await core.tick(task["task_id"])
    sid = journal.get(task["task_id"])["session_id"]
    fake.reads[sid] = {"turn_started": True, "turn_done": True, "turn_attribution": "correlated",
                       "streaming": False, "messages": [{"role": "assistant", "text": "BAT-STATUS: MILESTONE"}]}
    await core.tick(task["task_id"])
    diff_hash = hashlib.sha256(fake.review_diff["diff"].encode()).hexdigest()
    journal.reserve_minimal_review(task["task_id"], "a" * 40, "b" * 40, diff_hash, 0.85)
    journal.close()
    reopened = task_controller(Journal(tmp_path / "pending.db"))
    result = await TaskCoordinator(reopened, fake, minimal_review_gate=gate).tick(task["task_id"])
    assert result["reviewer_session_id"] and not result["delivered"] and jev.calls == 0
    recovered = reopened.minimal_review_gate(task["task_id"], "a" * 40, "b" * 40)
    assert recovered["verdict"] == "escalate" and recovered["reason"] == "pending_after_restart"
    reopened.close()


async def test_bat_candidate_review_diff_includes_both_sides_of_rename(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "--template=", "-q", str(repo)], check=True)
    old = repo / "README.md"
    old.write_text("one line\n")
    subprocess.run(["git", "-C", str(repo), "add", "README.md"], check=True)
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=test", "-c",
                    "user.email=test@example.com", "commit", "-qm", "base"], check=True)
    base = subprocess.check_output(["git", "-C", str(repo), "rev-parse", "HEAD"], text=True).strip()
    new = repo / ".github" / "workflows" / "check.yml"
    new.parent.mkdir(parents=True)
    old.rename(new)
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=test", "-c",
                    "user.email=test@example.com", "commit", "-qm", "rename"], check=True)
    monkeypatch.setattr(task_bat.registry, "get", lambda *_: {"worktree_path": str(repo)})
    adapter = task_bat.BatTaskAdapter(None, ObservedVerifier(VerificationSettings()))
    candidate = await adapter.candidate_review_diff({"host": "h1", "session_id": "s", "base_commit": base})
    assert set(candidate["paths"]) == {"README.md", ".github/workflows/check.yml"}
    assert "diff --git" in candidate["diff"]


@authenticated_task_test
async def test_minimal_prefers_warm_session_id_and_goose_provider_order(task_controller, tmp_path):
    class WarmBAT(FakeBAT):
        async def find_warm(self, task):
            assert task["task_path"] == "minimal"
            return "existing-clean-idle-session"

    journal = task_controller(Journal(tmp_path / "tasks.db"))
    task = journal.submit(project="p", host="h1", workspace="w", original_words=WORDS,
                          task_path="minimal", engine_decision={"selected": "rules_engine",
                          "effective": "rules"}, idempotency_key="minimal:warm")
    fake = WarmBAT()
    result = await TaskCoordinator(journal, fake).tick(task["task_id"])
    assert result["session_id"] == "existing-clean-idle-session"
    assert fake.starts[0][2] == "existing-clean-idle-session"
    assert result["branches"][0]["reason"] == "warm_reuse"
    assert ProviderCatalog().order == ("claude", "agy-claude", "codex")
    assert ProviderCatalog().entry("claude").model == "claude-opus-5-5"
    assert ProviderCatalog().entry("agy-claude").model == "claude-opus-4-6-thinking"
    journal.close()


@authenticated_task_test
async def test_warm_start_mismatched_ack_remains_uncertain(task_controller, tmp_path):
    class WrongAck(FakeBAT):
        async def find_warm(self, _task):
            return "reserved-warm-session"

        async def start(self, task, *, role, agent, session_id):
            await super().start(task, role=role, agent=agent, session_id=session_id)
            return "unrelated-session"

    journal = task_controller(Journal(tmp_path / "mismatch.db"))
    task = journal.submit(project="p", host="h1", workspace="w", original_words=WORDS,
                          task_path="minimal", engine_decision={"selected": "rules_engine",
                          "effective": "rules"}, idempotency_key="minimal:warm-mismatch")
    result = await TaskCoordinator(journal, WrongAck()).tick(task["task_id"])
    assert result["state"] == "uncertain" and result["branch_ids"] == []
    command = journal.commands(task["task_id"])[0]
    assert command["session_id"] == "reserved-warm-session" and command["status"] == "uncertain"
    journal.close()


@authenticated_task_test
async def test_bat_warm_reuse_claims_only_clean_completed_service_session(task_controller, fleet_factory, mock, tmp_path):
    class CleanVerifier:
        settings = VerificationSettings()
        head = "a" * 40

        async def identity(self, _task, _cwd):
            return {"candidate_commit": self.head, "tree_hash": "b" * 40, "clean": True}

    fleet = fleet_factory(writes=True, orchestrate=True, safety={"write_min_interval_s": 0})
    journal = task_controller(Journal(tmp_path / "warm.db"))
    verifier = CleanVerifier()
    adapter = task_bat.BatTaskAdapter(fleet, verifier, journal)
    previous = journal.submit(project="p", host="h1", workspace="demo-project",
                              original_words="Previous completed request", idempotency_key="warm:previous",
                              task_path="minimal", recipe="small-task-with-tests",
                              engine_decision={"selected": "rules_engine", "effective": "rules"})
    old_sid = "warm-session-001"
    await adapter.start(previous, role="lead", agent="codex", session_id=old_sid)
    journal.change(previous["task_id"], "dispatching")
    journal.change(previous["task_id"], "accepted", fields={"session_id": old_sid})
    journal.change(previous["task_id"], "verifying")
    journal.record_observed_verification(previous["task_id"], {
        "source": "observed_runner", "candidate_commit": "a" * 40, "tree_hash": "b" * 40,
        "command": "pytest -q", "exit_code": 0, "log_ref": "fake:old",
        "output_sha256": "c" * 64})
    journal.reserve_minimal_review(previous["task_id"], "a" * 40, "b" * 40, "d" * 64, 0.85)
    journal.finish_minimal_review(previous["task_id"], "a" * 40, "b" * 40, {
        "verdict": "pass", "confidence": 0.95, "jev_backend": "typesafe", "reason": "jev_pass"})
    journal.change(previous["task_id"], "done", fields={
        "verification_commit": "a" * 40, "verification_tree": "b" * 40})
    old_capability = journal.issue_capability(previous["task_id"])
    independent = journal.submit(project="p", host="h1", workspace="demo-project",
                                 original_words="Unrelated request", task_path="minimal",
                                 engine_decision={"selected": "rules_engine", "effective": "rules"},
                                 idempotency_key="warm:independent")
    assert await adapter.find_warm(independent) is None  # independent work starts fresh from base
    current = journal.submit(project="p", host="h1", workspace="demo-project",
                             original_words="New focused request", task_path="minimal",
                             engine_decision={"selected": "rules_engine", "effective": "rules"},
                             idempotency_key="warm:current", parent_task_id=previous["task_id"])
    verifier.head = "e" * 40
    assert await adapter.find_warm(current) is None  # HEAD moved past the verified commit
    verifier.head = "a" * 40
    # A10: a readable permission mismatch cannot be claimed as a warm lead.
    original_meta = dict(mock.metas[old_sid])
    creation = registry.get("h1", old_sid)["confinement"]
    mock.metas[old_sid]["codexSandboxMode"] = "danger-full-access"
    assert await adapter.find_warm(current) is None
    assert registry.get("h1", old_sid)["task_id"] == previous["task_id"]
    assert registry.get("h1", old_sid)["confinement"] == creation
    assert journal.authorize_capability(old_capability, previous["task_id"])
    mock.metas[old_sid] = original_meta
    assert await adapter.find_warm(current) == old_sid
    starts_before = len([i for i in mock.invokes if i["channel"] == "claude:start-session"])
    assert await adapter.start({**current, "_warm_session_id": old_sid},
                               role="lead", agent="codex", session_id=old_sid) == old_sid
    assert len([i for i in mock.invokes if i["channel"] == "claude:start-session"]) == starts_before
    assert registry.get("h1", old_sid)["task_id"] == current["task_id"]
    assert not journal.authorize_capability(old_capability, previous["task_id"])
    assert await adapter.find_warm(current) is None  # already claimed, never offered twice
    assert journal.get(current["task_id"])["base_commit"] == "a" * 40
    entry = registry.get("h1", old_sid)
    with pytest.raises(TaskIdentityMismatch, match="ownership changed"):
        registry.claim_warm("h1", old_sid, previous_task_id=previous["task_id"],
                            task_id="another-task", workspace_id=entry["workspace_id"],
                            cwd=entry["cwd"], branch=entry["branch"],
                            expected_incarnation=entry["controller_incarnation"])
    journal.close()
    await fleet.close()


@authenticated_task_test
def test_warm_candidates_are_limited_to_the_same_workstream(task_controller, tmp_path):
    journal = task_controller(Journal(tmp_path / "ws.db"))
    decision = {"selected": "rules_engine", "effective": "rules"}

    def done(key, **kw):
        task = journal.submit(project="p", host="h1", workspace="w", original_words="req " + key,
                              task_path="minimal", engine_decision=decision, idempotency_key=key, **kw)
        journal.db.execute("""UPDATE tasks SET state='done',session_id=?,verification_commit=?,
            delivered_at=? WHERE task_id=?""", ("s-" + key, "a" * 40, time.time(), task["task_id"]))
        return journal.get(task["task_id"])

    root = done("root", discord_thread_id="thread-1")
    other = done("other", discord_thread_id="thread-2")
    sibling = done("sibling", parent_task_id=root["task_id"])

    def ids(key, **kw):
        task = journal.submit(project="p", host="h1", workspace="w", original_words="next " + key,
                              task_path="minimal", engine_decision=decision, idempotency_key=key, **kw)
        return {t["task_id"] for t in journal.warm_candidates(task)}

    assert ids("fresh") == set()
    assert ids("same-thread-no-continuation", discord_thread_id="thread-1") == set()
    assert ids("continuation", discord_thread_id="thread-1", continuation=True) == {root["task_id"]}
    assert ids("child", parent_task_id=root["task_id"]) == {root["task_id"], sibling["task_id"]}
    assert other["task_id"] not in ids("child-2", parent_task_id=root["task_id"])
    with pytest.raises(ValueError, match="parent task"):
        journal.submit(project="p", host="h1", workspace="other", original_words="x", task_path="minimal",
                       engine_decision=decision, idempotency_key="bad-parent", parent_task_id=root["task_id"])
    journal.close()


@authenticated_task_test
def test_context_refs_are_validated_stored_and_keep_old_idempotency(task_controller, tmp_path):
    journal = task_controller(Journal(tmp_path / "refs.db"))
    refs = {"attachments": ["https://cdn.example/a.png"], "previous_message_id": "m-41",
            "plan": "docs/plan.md", "commit": "ABCDEF1"}
    task = journal.submit(project="p", host="h1", workspace="w", original_words=WORDS,
                          idempotency_key="refs:1", context_refs=refs)
    assert journal.get(task["task_id"])["context_refs"] == {**refs, "commit": "abcdef1"}
    plain = journal.submit(project="p", host="h1", workspace="w", original_words=WORDS, idempotency_key="refs:2")
    assert plain["payload_hash"] == hashlib.sha256(json.dumps(dict(
        project="p", host="h1", workspace="w", original_words=WORDS, discord_thread_id=None,
        recipe="feature-to-staging", acceptance="", engine="rules", interpretation=None, lead_agent="codex",
        pm_provider=None, base_branch=None), sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    for bad in ({"unknown": "x"}, {"commit": "not-hex"}, {"attachments": "one"}, {"plan": ""}):
        with pytest.raises(ValueError):
            journal.submit(project="p", host="h1", workspace="w", original_words=WORDS,
                           idempotency_key="refs:bad", context_refs=bad)
    journal.close()


@authenticated_task_test
def test_delivery_distinguishes_verified_adopted_merged_deployed(task_controller, tmp_path):
    journal = task_controller(Journal(tmp_path / "stages.db"))
    task = submit(journal)
    tid = task["task_id"]
    assert journal.delivery(tid)["stage"] is None
    with pytest.raises(ValueError, match="verified"):
        journal.mark_stage(tid, stage="merged", ref="abc", actor="hermes")
    journal.db.execute("""UPDATE tasks SET state='done',verification_commit=?,verification_tree=?,review_passed=1,
        delivered_at=? WHERE task_id=?""", ("a" * 40, "b" * 40, time.time(), tid))
    delivery = journal.delivery(tid)
    assert delivery["stage"] == "verified" and delivery["verified"]["basis"] == "independent_review"
    assert delivery["adopted"] is None and delivery["merged"] is None and delivery["deployed"] is None
    journal.mark_stage(tid, stage="merged", ref="https://github.com/o/r/pull/9", actor="hermes")
    assert journal.mark_stage(tid, stage="merged", ref="other", actor="ted")["merged"]["ref"].endswith("/pull/9")
    assert journal.delivery(tid)["stage"] == "merged"
    assert journal.mark_stage(tid, stage="deployed", ref="serve pid 42", actor="executor")["stage"] == "deployed"
    with pytest.raises(ValueError):
        journal.mark_stage(tid, stage="verified", ref="x", actor="hermes")
    journal.close()


@authenticated_task_test
def test_gate_eval_counts_false_pass_and_false_escalate(task_controller, tmp_path):
    from bat_agent_connector.gate_eval import evaluate

    journal = task_controller(Journal(tmp_path / "eval.db"))
    decision = {"selected": "rules_engine", "effective": "rules"}

    def gate(key, conf, reason, verdict, *, reviewed=None, chars=200):
        task = journal.submit(project="p", host="h1", workspace="w", original_words=key, task_path="minimal",
                              recipe="small-task-with-tests", engine_decision=decision, idempotency_key=key)
        commit = hashlib.sha1(key.encode()).hexdigest()
        journal.reserve_minimal_review(task["task_id"], commit, "b" * 40, "d" * 64, 0.5,
                                       diff_chars=chars, paths=["README.md"])
        journal.finish_minimal_review(task["task_id"], commit, "b" * 40, {
            "verdict": verdict, "confidence": conf, "jev_backend": "typesafe", "reason": reason})
        if reviewed == "pass":
            journal.db.execute("UPDATE tasks SET review_passed=1,verification_commit=? WHERE task_id=?",
                               (commit, task["task_id"]))
        elif reviewed == "reject":
            journal.note(task["task_id"], "review_verdict_rejected", {"candidate_commit": commit})

    gate("a", 0.48, "low_confidence", "escalate", reviewed="pass")
    gate("b", 0.46, "low_confidence", "escalate", reviewed="reject", chars=3000)
    gate("c", 0.9, "jev_risk", "escalate", reviewed="pass")
    gate("d", 0.7, "jev_pass", "pass")
    journal.close()
    result = evaluate(tmp_path / "eval.db")
    s = result["summary"]
    assert s["threshold_0.50"] == {"pass": 1, "escalate": 3, "false_pass": 0, "false_escalate": 2,
                                   "undecidable": 1}
    assert s["threshold_0.45"]["false_pass"] == 1 and s["threshold_0.45"]["false_escalate"] == 1
    assert s["pass_and_tiny"]["false_pass"] == 0 and s["pass_and_tiny"]["false_escalate"] == 1
    assert [r["jev_choice"] for r in result["rows"]] == ["pass", "pass", "risk", "pass"]


@authenticated_task_test
def test_minimal_small_completion_still_requires_observed_clean_tests(task_controller, tmp_path):
    journal = task_controller(Journal(tmp_path / "gate.db"))
    task = journal.submit(project="p", host="h1", workspace="w", original_words="Small edit",
                          recipe="small-task-with-tests", task_path="minimal",
                          engine_decision={"selected": "rules_engine", "effective": "rules"},
                          idempotency_key="small:gate")
    journal.change(task["task_id"], "dispatching")
    journal.change(task["task_id"], "verifying")
    with pytest.raises(ValueError, match="observed commit/tree verification"):
        journal.change(task["task_id"], "done", fields={
            "verification_commit": "a" * 40, "verification_tree": "b" * 40})
    journal.close()


@authenticated_task_test
async def test_goose_acp_scoped_tool_smoke(task_controller, mock, tmp_path, monkeypatch):
    """Fake ACP Goose invokes a real scoped MCP tool backed by fake BAT."""
    daemon = task_controller(TaskDaemon(make_config(mock, writes=True, orchestrate=True, managed_roots=["/srv"]), tmp_path / "tasks.db"))
    fake = FakeBAT()
    daemon.adapter = fake
    daemon.coordinator = TaskCoordinator(daemon.journal, fake)
    task = submit(daemon.journal, engine="goose")
    daemon.journal.change(task["task_id"], "dispatching")
    daemon.journal.change(task["task_id"], "accepted", fields={"session_id": "lead-0001"})
    adopt("lead-0001", task_id=task["task_id"], role="lead")
    daemon.ops.context["coordinator"] = daemon.coordinator
    server = await asyncio.start_server(daemon._handle, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    monkeypatch.setenv("BATC_TASK_URL", f"http://127.0.0.1:{port}/rpc")
    script = tmp_path / "fake_goose.py"
    script.write_text('''import json, os, subprocess, sys
def reply(req, result):
    print(json.dumps({"jsonrpc":"2.0","id":req["id"],"result":result}), flush=True)
initialized = False
for line in sys.stdin:
    req=json.loads(line)
    if req["method"] == "initialize":
        reply(req,{"protocolVersion":1,"agentCapabilities":{}})
    elif req["method"] == "notifications/initialized":
        initialized = True
    elif req["method"] == "session/new":
        assert initialized

        scoped=req["params"]["mcpServers"]
        assert len(scoped)==1 and scoped[0]["name"]=="bat-task"
        reply(req,{"sessionId":"fake-goose-session"})
    elif req["method"] == "session/prompt":
        env=os.environ.copy()
        for item in scoped[0]["env"]: env[item["name"]]=item["value"]
        p=subprocess.Popen([scoped[0]["command"],*scoped[0]["args"]],stdin=subprocess.PIPE,stdout=subprocess.PIPE,text=True,env=env)
        def call(obj):
            p.stdin.write(json.dumps(obj)+"\\n");p.stdin.flush()
            return json.loads(p.stdout.readline())
        call({"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-03-26","capabilities":{},"clientInfo":{"name":"fake-goose","version":"1"}}})
        p.stdin.write(json.dumps({"jsonrpc":"2.0","method":"notifications/initialized"})+"\\n");p.stdin.flush()
        result=call({"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"task_send","arguments":{"text":"check task","step_id":"one"}}})
        print(json.dumps({"jsonrpc":"2.0","method":"session/update","params":{"update":{"sessionUpdate":"agent_message_chunk","content":{"type":"text","text":json.dumps(result)}}}}), flush=True)
        p.terminate();p.wait()
        reply(req,{"stopReason":"end_turn"})
''')
    goose = GooseACP(GooseConfig(timeout_s=20, enabled=True, provider="codex"))
    try:
        capability = daemon.journal.issue_capability(task["task_id"])
        result = await goose.run_task(task, str(tmp_path), capability=capability,
                                      command=(sys.executable, "-u", str(script)))
        assert result["stop_reason"] == "end_turn"
        assert len(fake.sends) == 1 and fake.sends[0][1] == "check task"
    finally:
        server.close()
        await server.wait_closed()
        await daemon.fleet.close()
        daemon.journal.close()


@authenticated_task_test
async def test_uncertain_submission_marker_and_busy_rejection(task_controller, tmp_path):
    j = task_controller(Journal(tmp_path / "tasks.db"))
    task = submit(j)
    fake = FakeBAT()
    fake.send_error = True
    core = TaskCoordinator(j, fake)
    await core.tick(task["task_id"])
    sends = [c for c in j.commands(task["task_id"]) if c["kind"] == "send"]
    assert len(sends) == 1 and sends[0]["status"] == "uncertain"
    assert any(e["kind"] == "command_intent" and json.loads(e["body"]).get("needs_review")
               for e in j.events(task["task_id"]))
    await core.tick(task["task_id"])
    assert len(fake.sends) == 1
    with pytest.raises(ValueError, match="does not allow dispatch"):
        j.command(task["task_id"], "send", j.get(task["task_id"])["session_id"], {}, "another-step")
    j.close()

    class BusyBAT(FakeBAT):
        async def send(self, task, session_id, text, message_id):
            self.sends.append((session_id, text, message_id))
            return {"accepted": False, "reason": "busy"}

    j = task_controller(Journal(tmp_path / "busy.db"))
    task = submit(j)
    busy = BusyBAT()
    core = TaskCoordinator(j, busy)
    assert (await core.tick(task["task_id"]))["state"] == "needs_ted"
    assert [c["status"] for c in j.commands(task["task_id"]) if c["kind"] == "send"] == ["rejected"]
    await core.tick(task["task_id"])
    assert len(busy.sends) == 1
    j.close()


@authenticated_task_test
async def test_start_recovery_pause_during_start_and_branch_history(task_controller, tmp_path):
    j = task_controller(Journal(tmp_path / "tasks.db"))
    task = submit(j)
    fake = FakeBAT()
    fake.start_error = True
    core = TaskCoordinator(j, fake)
    assert (await core.tick(task["task_id"]))["state"] == "uncertain"
    assert len(fake.starts) == 1
    fake.start_error = False
    assert (await core.tick(task["task_id"]))["state"] == "accepted"
    await core.tick(task["task_id"])
    assert len(fake.starts) == 1 and len(fake.sends) == 1
    assert j.get(task["task_id"])["branches"][0]["session_id"] == fake.starts[0][2]
    j.close()

    j = task_controller(Journal(tmp_path / "paused.db"))
    task = submit(j)
    fake = FakeBAT()
    fake.start_gate = asyncio.Event()
    core = TaskCoordinator(j, fake)
    tick = asyncio.create_task(core.tick(task["task_id"]))
    while not fake.starts:
        await asyncio.sleep(0)
    await core.pause(task["task_id"])
    fake.start_gate.set()
    paused = await tick
    assert paused["paused"] and paused["state"] == "accepted" and not fake.sends
    j.resume(task["task_id"])
    await core.tick(task["task_id"])
    assert len(fake.sends) == 1
    j.close()


@pytest.mark.skip(reason="removed: independent reviewer, per-step routing, and mid-task failover")
@authenticated_task_test
async def test_candidate_change_requires_fresh_review_and_attribution(task_controller, tmp_path):
    j = task_controller(Journal(tmp_path / "tasks.db"))
    task = submit(j)
    fake = FakeBAT()
    core = TaskCoordinator(j, fake)
    await core.tick(task["task_id"])
    lead = j.get(task["task_id"])["session_id"]
    fake.reads[lead] = {"turn_started": True, "turn_done": True,
                        "messages": [{"role": "assistant", "text": "BAT-STATUS: MILESTONE"}]}
    await core.tick(task["task_id"])
    await core.tick(task["task_id"])
    old = j.get(task["task_id"])["reviewer_session_id"]
    fake.reads[old] = {"turn_started": True, "turn_done": True, "turn_attribution": "correlated",
                       "messages": [{"role": "assistant", "text": REVIEW_PASS}]}
    assert (await core.tick(task["task_id"]))["state"] == "verifying"
    fake.reads[old]["first_turn_proven"] = True
    fake.identity = {"candidate_commit": "d" * 40, "tree_hash": "e" * 40, "clean": True}
    assert (await core.tick(task["task_id"]))["state"] == "verifying"
    assert j.get(task["task_id"])["reviewer_session_id"] != old
    assert not j.get(task["task_id"])["delivered"]
    j.close()


@authenticated_task_test
def test_ledger_handoff_archive_and_permissions(task_controller, tmp_path):
    j = task_controller(Journal(tmp_path / "tasks.db"))
    task = submit(j)
    summary = ledger_summary(j, task["task_id"])
    assert task["task_id"] in summary and WORDS in summary
    assert "Hermes" not in summary
    data = "H" * 12_000 + "M" * 50_000 + "T" * 148_001
    result = history_excerpt(data, tmp_path / "private", task["task_id"])
    assert result["truncated"] and "middle omitted" in result["excerpt"]
    assert "M" * 1000 not in result["excerpt"]
    assert result["excerpt"].count("H") >= 12_000
    assert result["excerpt"].count("T") >= 148_000
    assert os.stat(result["archive_path"]).st_mode & 0o777 == stat.S_IRUSR | stat.S_IWUSR
    assert os.stat(result["excerpt_path"]).st_mode & 0o777 == stat.S_IRUSR | stat.S_IWUSR
    assert Path(result["excerpt_path"]).read_text() == result["excerpt"]
    assert (tmp_path / "private").stat().st_mode & 0o777 == 0o700
    assert open(result["archive_path"]).read() == data
    j.close()


@authenticated_task_test
async def test_live_failover_uses_private_history_fallback_when_ledger_unavailable(task_controller, mock, tmp_path, monkeypatch):
    daemon = task_controller(TaskDaemon(make_config(mock, writes=True, orchestrate=True), tmp_path / "tasks.db"))
    task = submit(daemon.journal, lead_agent="claude")
    daemon.journal.change(task["task_id"], "dispatching")
    daemon.journal.change(task["task_id"], "accepted", fields={"session_id": "old"})
    daemon.journal.change(task["task_id"], "running")
    task = daemon.journal.change(task["task_id"], "quota_limited")
    _, handoff = daemon.journal.reserve_failover(task["task_id"], "old", "successor")
    monkeypatch.setenv("BATC_TASK_LOCAL_HOST_ALIAS", "h1")
    monkeypatch.setattr(task_bat.registry, "get", lambda host, sid: {"agent_preset": "claude-agent"})

    def broken_summary(journal, task_id, **kwargs):
        raise ValueError("ledger unavailable")

    monkeypatch.setattr(task_bat, "ledger_summary", broken_summary)
    history = "H" * 12_000 + "M" * 50_000 + "T" * 148_001

    async def fake_read(*args, **kwargs):
        return {"messages": [{"role": "assistant", "text": history}], "next_offset": None}

    observed = {}

    async def fake_failover(*args, **kwargs):
        observed.update(kwargs)
        kwargs["task_authority"].before_handoff_send("synthetic BAT handoff\n" + kwargs["instructions"])
        return {"new_session_id": "successor", "prompt_sent": True, "message_id": "handoff"}

    monkeypatch.setattr(task_bat.service, "session_read", fake_read)
    monkeypatch.setattr(task_bat.lifecycle, "session_failover", fake_failover)
    async def verified(*args):
        return True
    monkeypatch.setattr(daemon.adapter, "_verified_failover_successor", verified)
    try:
        result = await daemon.adapter.failover(task, "old", "successor",
                                               handoff_message_id=handoff["message_id"],
                                               handoff_command_id=handoff["command_id"])
        assert result["session_id"] == "successor"
        assert observed["ledger_only"] is True
        files = list((tmp_path / "handoff-archive").glob("*.txt"))
        full = next(p for p in files if not p.name.endswith(".excerpt.txt"))
        excerpt = next(p for p in files if p.name.endswith(".excerpt.txt"))
        assert full.stat().st_mode & 0o777 == 0o600
        assert excerpt.stat().st_mode & 0o777 == 0o600
        assert "H" * 11_980 in excerpt.read_text() and "T" * 148_000 in excerpt.read_text()
        assert str(excerpt) in observed["instructions"] and str(full) in observed["instructions"]
    finally:
        await daemon.fleet.close()
        daemon.journal.close()


@authenticated_task_test
async def test_history_fallback_preserves_chronological_page_order(task_controller, mock, tmp_path, monkeypatch):
    daemon = task_controller(TaskDaemon(make_config(mock, writes=True, orchestrate=True), tmp_path / "tasks.db"))
    task = submit(daemon.journal, lead_agent="claude")

    async def fake_read(*args, **kwargs):
        if kwargs["offset"] == 0:
            return {"messages": [{"role": "user", "text": "middle"},
                                 {"role": "assistant", "text": "latest"}], "next_offset": 2}
        return {"messages": [{"role": "user", "text": "earliest"}], "next_offset": None}

    monkeypatch.setattr(task_bat.service, "session_read", fake_read)
    try:
        assert await daemon.adapter._history_for_fallback(task, "old") == (
            "user: earliest\nuser: middle\nassistant: latest")
    finally:
        await daemon.fleet.close()
        daemon.journal.close()


@authenticated_task_test
def test_provider_switches_branch_and_uncertain_never_replays(task_controller, tmp_path):
    j = task_controller(Journal(tmp_path / "tasks.db"))
    task = submit(j)
    entries = [ProviderEntry("agy-claude", "agy-shim", "http://127.0.0.1:9999/v1", "claude-opus-4-6-thinking", 1),
               ProviderEntry("codex", "codex-acp"), ProviderEntry("claude", "claude-acp", model="claude-opus-5-5")]
    catalog = ProviderCatalog(entries)
    switcher = ProviderSwitcher(j, catalog)
    assert switcher.initial(task["task_id"]) == "claude"
    assert switcher.fallback(task["task_id"], "claude", outcome="quota_error",
                             prompt_status="not_sent") == "agy-claude"
    assert switcher.fallback(task["task_id"], "agy-claude", outcome="rate_limited",
                             prompt_status="not_sent") == "codex"
    branches = j.get(task["task_id"])["branches"]
    assert [b["provider"] for b in branches] == ["claude", "agy-claude", "codex"]
    assert j.get(task["task_id"])["branch_ids"] == [b["branch_id"] for b in branches]
    assert branches[1]["parent_branch_id"] == branches[0]["branch_id"]
    with pytest.raises(UncertainPrompt):
        switcher.fallback(task["task_id"], "codex", outcome="auth_error", prompt_status="uncertain")
    assert len(j.get(task["task_id"])["branches"]) == 3
    j.close()


@authenticated_task_test
def test_provider_starts_on_agy_after_primary_quota_recorded(task_controller, tmp_path):
    journal = task_controller(Journal(tmp_path / "tasks.db"))
    task = submit(journal)
    journal.provider_use("claude", "quota_error")
    switcher = ProviderSwitcher(journal, ProviderCatalog())
    assert switcher.initial(task["task_id"]) == "agy-claude"
    assert [branch["provider"] for branch in journal.branches(task["task_id"])] == ["agy-claude"]
    journal.close()


async def test_agy_provider_contract_with_fake_endpoint():
    seen = []

    async def handle(reader, writer):
        request = await reader.readuntil(b"\r\n\r\n")
        seen.append(request)
        body = b'{"data":[{"id":"claude-opus-4-6-thinking"}]}'
        writer.write(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: " +
                     str(len(body)).encode() + b"\r\n\r\n" + body)
        await writer.drain()
        writer.close()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    try:
        entry = ProviderEntry("agy-claude", "agy-shim", f"http://127.0.0.1:{port}/v1",
                              "claude-opus-4-6-thinking", 2)
        adapter = AgyShimAdapter()
        assert await adapter.contract_probe(entry, {"BATC_AGY_SHIM_TOKEN": "fake-local-token"})
        wrong_model = ProviderEntry("other", "openai-compatible", f"http://127.0.0.1:{port}/v1",
                                    "missing-model", 2)
        assert not await adapter.contract_probe(wrong_model, {"BATC_AGY_SHIM_TOKEN": "fake-local-token"})
        assert seen and b"GET /v1/models" in seen[0]
    finally:
        server.close()
        await server.wait_closed()


@pytest.mark.parametrize("failure", ["quota", "setup"])
@authenticated_task_test
async def test_goose_provider_fallback_before_prompt_only(task_controller, tmp_path, monkeypatch, failure):
    script = tmp_path / "fake_acp.py"
    script.write_text('''import json, os, sys
log=sys.argv[1]
for line in sys.stdin:
    req=json.loads(line)
    with open(log,"a") as f: f.write(os.environ["GOOSE_PROVIDER"]+":"+os.environ["GOOSE_MODEL"]+":"+req["method"]+":"+str("BATC_PRIVATE_TEST_SECRET" in os.environ)+"\\n")
    if req["method"]=="notifications/initialized":
        continue
    if req["method"]=="initialize" and os.environ["GOOSE_PROVIDER"]=="claude-acp" and sys.argv[2]=="quota":
        result={"jsonrpc":"2.0","id":req["id"],"error":{"status":429}}
    elif req["method"]=="session/new" and os.environ["GOOSE_PROVIDER"]=="claude-acp" and sys.argv[2]=="setup":
        result={"jsonrpc":"2.0","id":req["id"],"error":{"code":"unsupported_model"}}
    elif req["method"]=="session/new":
        result={"jsonrpc":"2.0","id":req["id"],"result":{"sessionId":"s"}}
    elif req["method"]=="session/prompt":
        result={"jsonrpc":"2.0","id":req["id"],"result":{"stopReason":"end_turn"}}
    else:
        result={"jsonrpc":"2.0","id":req["id"],"result":{"protocolVersion":1}}
    print(json.dumps(result),flush=True)
''')
    log = tmp_path / "calls.txt"
    monkeypatch.setenv("BATC_AGY_SHIM_TOKEN", "fake-local-token")
    monkeypatch.setenv("BATC_PRIVATE_TEST_SECRET", "must-not-enter-Goose")
    catalog = ProviderCatalog([
        ProviderEntry("agy-claude", "agy-shim", "http://127.0.0.1:18795/v1", "claude-opus-4-6-thinking", 1),
        ProviderEntry("codex", "codex-acp"), ProviderEntry("claude", "claude-acp", model="claude-opus-5-5"),
    ])
    j = task_controller(Journal(tmp_path / "tasks.db"))
    task = submit(j, engine="goose")
    goose = GooseACP(GooseConfig(enabled=True, timeout_s=5), catalog)
    result = await goose.run_task(task, str(tmp_path), capability="synthetic",
                                  command=(sys.executable, "-u", str(script), str(log), failure), journal=j)
    assert result["stop_reason"] == "end_turn"
    lines = log.read_text().splitlines()
    assert lines.count("claude-acp:claude-opus-5-5:session/prompt:False") == 0
    assert lines.count("openai:claude-opus-4-6-thinking:session/prompt:False") == 1
    assert all(line.endswith(":False") for line in lines)
    assert [b["provider"] for b in j.branches(task["task_id"])] == ["claude", "agy-claude"]
    assert j.provider_unavailable("claude", since=0)
    j.close()


@authenticated_task_test
async def test_goose_uncertain_prompt_does_not_switch(task_controller, tmp_path, monkeypatch):
    script = tmp_path / "fake_acp.py"
    script.write_text('''import json, os, sys
for line in sys.stdin:
    req=json.loads(line)
    if req["method"]=="notifications/initialized":
        continue
    if req["method"]=="session/prompt":
        with open(sys.argv[1],"a") as f: f.write(os.environ["GOOSE_PROVIDER"]+"\\n")
        result={"jsonrpc":"2.0","id":req["id"],"error":{"status":429}}
    elif req["method"]=="session/new":
        result={"jsonrpc":"2.0","id":req["id"],"result":{"sessionId":"s"}}
    else:
        result={"jsonrpc":"2.0","id":req["id"],"result":{"protocolVersion":1}}
    print(json.dumps(result),flush=True)
''')
    log = tmp_path / "prompts.txt"
    monkeypatch.setenv("BATC_AGY_SHIM_TOKEN", "fake-local-token")
    catalog = ProviderCatalog([
        ProviderEntry("agy-claude", "agy-shim", "http://127.0.0.1:18795/v1", "claude-opus-4-6-thinking", 1),
        ProviderEntry("codex", "codex-acp"), ProviderEntry("claude", "claude-acp", model="claude-opus-5-5"),
    ])
    j = task_controller(Journal(tmp_path / "tasks.db"))
    task = submit(j, engine="goose")
    goose = GooseACP(GooseConfig(enabled=True, timeout_s=5), catalog)
    with pytest.raises(UncertainPrompt):
        await goose.run_task(task, str(tmp_path), capability="synthetic",
                             command=(sys.executable, "-u", str(script), str(log)), journal=j)
    assert log.read_text().splitlines() == ["claude-acp"]
    assert [b["provider"] for b in j.branches(task["task_id"])] == ["claude"]
    j.close()


async def test_goose_pinned_protocol_rejects_unknown_version(tmp_path):
    script = tmp_path / "wrong_version.py"
    script.write_text('''import json, sys
for line in sys.stdin:
    req=json.loads(line)
    print(json.dumps({"jsonrpc":"2.0","id":req["id"],"result":{"protocolVersion":2}}),flush=True)
''')
    goose = GooseACP(GooseConfig(enabled=True, provider="codex", timeout_s=5))
    with pytest.raises(RuntimeError, match="pinned version 1"):
        await goose.run("synthetic", str(tmp_path), "hello", capability="synthetic",
                        command=(sys.executable, "-u", str(script)))


@pytest.mark.skip(reason="removed: independent reviewer, per-step routing, and mid-task failover")
@authenticated_task_test
async def test_failover_recovers_reserved_successor_without_reuse(task_controller, tmp_path):
    class LostReplyBAT(FakeBAT):
        async def failover(self, task, session_id, successor_id, **kwargs):
            await super().failover(task, session_id, successor_id, **kwargs)
            raise TimeoutError("successor accepted but reply lost")

    j = task_controller(Journal(tmp_path / "tasks.db"))
    task = submit(j, lead_agent="claude")
    fake = LostReplyBAT()
    fake.reviewer_kind = "claude"  # Claude is available for the lead
    fake.failover_allowed = True
    core = TaskCoordinator(j, fake)
    await core.tick(task["task_id"])
    old_sid = j.get(task["task_id"])["session_id"]
    j.change(task["task_id"], "quota_limited")
    assert (await core.tick(task["task_id"]))["state"] == "uncertain"
    recovered = await core.tick(task["task_id"])
    assert recovered["state"] == "uncertain" and recovered["session_id"] != old_sid
    assert fake.failover_calls == 1
    handoff = next(c for c in j.commands(task["task_id"]) if c["kind"] == "send"
                   and json.loads(c["payload"]).get("purpose") == "failover_handoff")
    assert handoff["status"] == "uncertain"
    assert [b["provider"] for b in recovered["branches"]] == ["claude", "codex"]
    j.close()


@pytest.mark.skip(reason="removed: independent reviewer, per-step routing, and mid-task failover")
@authenticated_task_test
async def test_normal_failover_refuses_different_successor_response(task_controller, tmp_path):
    class WrongReplyBAT(FakeBAT):
        async def failover(self, task, session_id, successor_id, **kwargs):
            result = await super().failover(task, session_id, successor_id, **kwargs)
            return {**result, "session_id": "previous-successor"}

    j = task_controller(Journal(tmp_path / "tasks.db"))
    task = submit(j, lead_agent="claude")
    fake = WrongReplyBAT()
    fake.failover_allowed = True
    core = TaskCoordinator(j, fake)
    await core.tick(task["task_id"])
    old_sid = j.get(task["task_id"])["session_id"]
    j.change(task["task_id"], "quota_limited")
    result = await core.tick(task["task_id"])
    cmds = j.commands(task["task_id"])
    failover = next(c for c in cmds if c["kind"] == "failover")
    handoff = next(c for c in cmds if json.loads(c["payload"]).get("purpose") == "failover_handoff")
    assert result["state"] == "uncertain" and result["session_id"] == old_sid
    assert [b["session_id"] for b in result["branches"]] == [old_sid]
    assert failover["session_id"] == handoff["session_id"] != "previous-successor"
    assert json.loads(failover["payload"])["operator_only"] is True
    sends = len(fake.sends)
    await core.tick(task["task_id"])
    assert len(fake.sends) == sends and fake.failover_calls == 1
    j.close()


@pytest.mark.skip(reason="removed: independent reviewer, per-step routing, and mid-task failover")
@authenticated_task_test
async def test_recovery_conflict_stays_operator_only_after_registry_changes(task_controller, tmp_path):
    class LostReplyBAT(FakeBAT):
        async def failover(self, task, session_id, successor_id, **kwargs):
            await super().failover(task, session_id, successor_id, **kwargs)
            raise TimeoutError("accepted successor, lost response")

    path = tmp_path / "tasks.db"
    j = task_controller(Journal(path))
    task = submit(j, lead_agent="claude")
    fake = LostReplyBAT()
    fake.reviewer_kind = "claude"  # Claude is available for the lead
    fake.failover_allowed = True
    core = TaskCoordinator(j, fake)
    await core.tick(task["task_id"])
    old_sid = j.get(task["task_id"])["session_id"]
    j.change(task["task_id"], "quota_limited")
    assert (await core.tick(task["task_id"]))["state"] == "uncertain"
    failover = next(c for c in j.commands(task["task_id"]) if c["kind"] == "failover")
    handoff = j.command_get(json.loads(failover["payload"])["handoff_command_id"])
    reserved = failover["session_id"]
    fake.successors[old_sid] = {"session_id": "previous-successor", "marker": handoff["message_id"]}
    assert (await core.tick(task["task_id"]))["session_id"] == old_sid
    assert json.loads(j.command_get(failover["command_id"])["payload"])["operator_only"] is True
    fake.successors[old_sid] = {"session_id": reserved, "marker": handoff["message_id"]}
    j.close()
    j = task_controller(Journal(path))
    core = TaskCoordinator(j, fake)
    assert (await core.tick(task["task_id"]))["session_id"] == old_sid
    assert len(j.get(task["task_id"])["branches"]) == 1 and fake.failover_calls == 1
    cap = j.issue_reconcile_capability(task["task_id"], failover["command_id"])
    reconciled = await core.resolve_command(task["task_id"], failover["command_id"], token=cap,
                                            outcome="superseded", actor="operator", source="incident:42",
                                            evidence="reserved successor identity cannot be verified")
    assert reconciled["state"] == "human_owned" and reconciled["session_id"] == old_sid
    assert j.command_get(failover["command_id"])["status"] == "resolved_superseded"
    assert j.command_get(handoff["command_id"])["status"] == "resolved_superseded"
    assert len(j.get(task["task_id"])["branches"]) == 1
    j.close()


@pytest.mark.parametrize("wait_stage", ["start_ack", "identity_lookup", "send_transport"])
@pytest.mark.skip(reason="removed: independent reviewer, per-step routing, and mid-task failover")
@authenticated_task_test
async def test_pause_during_failover_wait_never_submits_handoff(task_controller,
        fleet_factory, mock, tmp_path, monkeypatch, wait_stage):
    fleet = fleet_factory(writes=True, orchestrate=True, tabs=False,
                          safety={"write_min_interval_s": 0})
    started = await orchestrate.session_start(fleet, "h1", "demo-project", "claude",
                                              confirm=True, prompt=None, use_worktree=True)
    old_sid = started["session_id"]
    mock.states[old_sid]["messages"] = [{"role": "assistant", "content": "You've hit your usage limit",
                                          "timestamp": 1_790_000_000_000}]
    j = task_controller(Journal(tmp_path / "tasks.db"))
    task = j.submit(project="p", host="h1", workspace="demo-project", original_words=WORDS,
                    idempotency_key="pause-failover:" + wait_stage, lead_agent="claude")
    j.change(task["task_id"], "dispatching")
    j.change(task["task_id"], "accepted", fields={"session_id": old_sid})
    j.add_branch(task["task_id"], session_id=old_sid, provider="claude", role="lead", reason="test")
    j.change(task["task_id"], "running")
    j.change(task["task_id"], "quota_limited")
    adapter = task_bat.BatTaskAdapter(fleet, ObservedVerifier(VerificationSettings()), j)
    core = TaskCoordinator(j, adapter)
    client = fleet.client("h1")
    entered, release = asyncio.Event(), asyncio.Event()
    if wait_stage in {"start_ack", "identity_lookup"}:
        original_invoke = client.invoke

        async def held_invoke(channel, params=None, **kwargs):
            reply = await original_invoke(channel, params, **kwargs)
            if ((wait_stage == "start_ack" and channel == "claude:start-session"
                 and params["sessionId"] != old_sid)
                    or (wait_stage == "identity_lookup" and channel == "worktree:status"
                        and params["sessionId"] != old_sid)):
                entered.set()
                await release.wait()
            return reply

        monkeypatch.setattr(client, "invoke", held_invoke)
    else:
        original_checked = client._invoke_checked

        async def held_checked(channel, params, timeout, **kwargs):
            if channel == "claude:send-message" and params["sessionId"] != old_sid:
                entered.set()
                await release.wait()
            return await original_checked(channel, params, timeout, **kwargs)

        monkeypatch.setattr(client, "_invoke_checked", held_checked)
    try:
        tick = asyncio.create_task(core.tick(task["task_id"]))
        await asyncio.wait_for(entered.wait(), 5)
        assert (await core.pause(task["task_id"]))["paused"]
        release.set()
        paused = await asyncio.wait_for(tick, 5)
        assert paused["state"] == "uncertain" and paused["paused"]
        assert paused["session_id"] == old_sid and len(paused["branches"]) == 1
        assert not any(i["channel"] == "claude:send-message" for i in mock.invokes)
        failover = next(c for c in j.commands(task["task_id"]) if c["kind"] == "failover")
        handoff = j.command_get(json.loads(failover["payload"])["handoff_command_id"])
        assert json.loads(failover["payload"])["operator_only"] is True
        assert handoff["status"] == "uncertain"
        j.resume(task["task_id"])
        assert (await core.tick(task["task_id"]))["state"] == "uncertain"
        assert not any(i["channel"] == "claude:send-message" for i in mock.invokes)
    finally:
        release.set()
        await fleet.close()
        j.close()


@pytest.mark.parametrize("mismatch", ["successor_cwd", "successor_branch",
                                      "actual_frame_prompt", "late_registry_branch",
                                      "late_registry_owner", "late_git_root"])
@pytest.mark.skip(reason="removed: independent reviewer, per-step routing, and mid-task failover")
@authenticated_task_test
async def test_failover_rejects_wrong_successor_or_frame_before_handoff(task_controller,
        fleet_factory, mock, tmp_path, monkeypatch, mismatch):
    fleet = fleet_factory(writes=True, orchestrate=True, tabs=False,
                          safety={"write_min_interval_s": 0})
    started = await orchestrate.session_start(fleet, "h1", "demo-project", "claude",
                                              confirm=True, prompt=None, use_worktree=True)
    old_sid = started["session_id"]
    mock.states[old_sid]["messages"] = [{"role": "assistant", "content": "You've hit your usage limit",
                                          "timestamp": 1_790_000_000_000}]
    j = task_controller(Journal(tmp_path / "tasks.db"))
    task = j.submit(project="p", host="h1", workspace="demo-project", original_words=WORDS,
                    idempotency_key="wrong-failover:" + mismatch, lead_agent="claude")
    j.change(task["task_id"], "dispatching")
    j.change(task["task_id"], "accepted", fields={"session_id": old_sid})
    j.add_branch(task["task_id"], session_id=old_sid, provider="claude", role="lead", reason="test")
    j.change(task["task_id"], "running")
    j.change(task["task_id"], "quota_limited")
    core = TaskCoordinator(j, task_bat.BatTaskAdapter(
        fleet, ObservedVerifier(VerificationSettings()), j))
    client = fleet.client("h1")
    original_invoke = client.invoke

    async def changed_invoke(channel, params=None, **kwargs):
        if channel == "claude:send-message" and mismatch == "actual_frame_prompt":
            return await original_invoke(channel, {**params, "prompt": params["prompt"] + " ALTERED"}, **kwargs)
        if channel == "claude:send-message" and mismatch == "late_registry_branch":
            registry.update("h1", params["sessionId"], branch="bat/wrong-branch")
        if channel == "claude:send-message" and mismatch == "late_git_root":
            mock.handlers["git:getRoot"] = lambda _: "/srv/other"
        if channel == "claude:send-message" and mismatch == "late_registry_owner":
            original_guard = kwargs["frame_guard"]

            def changed_guard(frame):
                registry.update("h1", params["sessionId"], failover_of="unrelated-session")
                original_guard(frame)

            kwargs["frame_guard"] = changed_guard
        result = await original_invoke(channel, params, **kwargs)
        if channel == "claude:start-session" and params["sessionId"] != old_sid:
            sid = params["sessionId"]
            if mismatch == "successor_cwd":
                mock.metas[sid]["cwd"] = "/srv/other"
            elif mismatch == "successor_branch":
                mock.worktrees[sid]["branchName"] = "bat/wrong-branch"
        return result

    monkeypatch.setattr(client, "invoke", changed_invoke)
    try:
        result = await core.tick(task["task_id"])
        assert result["state"] == "uncertain" and result["session_id"] == old_sid
        assert len(result["branches"]) == 1
        assert not any(i["channel"] == "claude:send-message" for i in mock.invokes)
        failover = next(c for c in j.commands(task["task_id"]) if c["kind"] == "failover")
        handoff = j.command_get(json.loads(failover["payload"])["handoff_command_id"])
        assert json.loads(failover["payload"])["operator_only"] is True
        assert handoff["status"] == "uncertain"
        assert json.loads(handoff["payload"])["prompt_sha256"]
        assert (await core.tick(task["task_id"]))["session_id"] == old_sid
        assert not any(i["channel"] == "claude:send-message" for i in mock.invokes)
    finally:
        await fleet.close()
        j.close()


@pytest.mark.parametrize("change", ["git_root", "branch", "failover_of"])
@pytest.mark.skip(reason="removed: independent reviewer, per-step routing, and mid-task failover")
@authenticated_task_test
async def test_failover_rechecks_identity_after_semaphore_wait(task_controller,
        fleet_factory, mock, tmp_path, monkeypatch, change):
    fleet = fleet_factory(writes=True, orchestrate=True, tabs=False,
                          safety={"write_min_interval_s": 0})
    started = await orchestrate.session_start(fleet, "h1", "demo-project", "claude",
                                              confirm=True, prompt=None, use_worktree=True)
    old_sid = started["session_id"]
    mock.states[old_sid]["messages"] = [{"role": "assistant", "content": "You've hit your usage limit",
                                          "timestamp": 1_790_000_000_000}]
    j = task_controller(Journal(tmp_path / "tasks.db"))
    task = j.submit(project="p", host="h1", workspace="demo-project", original_words=WORDS,
                    idempotency_key="sem-wait:" + change, lead_agent="claude")
    j.change(task["task_id"], "dispatching")
    j.change(task["task_id"], "accepted", fields={"session_id": old_sid})
    j.add_branch(task["task_id"], session_id=old_sid, provider="claude", role="lead", reason="test")
    j.change(task["task_id"], "running")
    j.change(task["task_id"], "quota_limited")
    core = TaskCoordinator(j, task_bat.BatTaskAdapter(
        fleet, ObservedVerifier(VerificationSettings()), j))
    client = fleet.client("h1")
    original_checked = client._invoke_checked
    entered = asyncio.Event()
    blocked_sem = asyncio.Semaphore(1)
    await blocked_sem.acquire()

    async def waiting_checked(channel, params, timeout, **kwargs):
        if channel != "claude:send-message":
            return await original_checked(channel, params, timeout, **kwargs)
        old_sem = client._sem
        client._sem = blocked_sem
        entered.set()
        try:
            return await original_checked(channel, params, timeout, **kwargs)
        finally:
            client._sem = old_sem

    monkeypatch.setattr(client, "_invoke_checked", waiting_checked)
    try:
        tick = asyncio.create_task(core.tick(task["task_id"]))
        await asyncio.wait_for(entered.wait(), 5)
        failover = next(c for c in j.commands(task["task_id"]) if c["kind"] == "failover")
        successor = failover["session_id"]
        if change == "git_root":
            mock.handlers["git:getRoot"] = lambda _: "/srv/other"
        elif change == "branch":
            mock.worktrees[successor]["branchName"] = "bat/wrong-branch"
        else:
            registry.update("h1", successor, failover_of="unrelated-session")
        blocked_sem.release()
        result = await asyncio.wait_for(tick, 5)
        handoff = j.command_get(json.loads(failover["payload"])["handoff_command_id"])
        assert result["state"] == "uncertain" and result["session_id"] == old_sid
        assert len(result["branches"]) == 1 and handoff["status"] == "uncertain"
        assert json.loads(j.command_get(failover["command_id"])["payload"])["operator_only"] is True
        assert not any(i["channel"] == "claude:send-message" for i in mock.invokes)
        await core.tick(task["task_id"])
        assert not any(i["channel"] == "claude:send-message" for i in mock.invokes)
    finally:
        blocked_sem.release()
        await fleet.close()
        j.close()


@pytest.mark.skip(reason="removed: independent reviewer, per-step routing, and mid-task failover")
@authenticated_task_test
async def test_verified_successor_handoff_frame_hash_survives_restart(task_controller, fleet_factory, mock, tmp_path):
    fleet = fleet_factory(writes=True, orchestrate=True, tabs=False,
                          safety={"write_min_interval_s": 0})
    started = await orchestrate.session_start(fleet, "h1", "demo-project", "claude",
                                              confirm=True, prompt=None, use_worktree=True)
    old_sid = started["session_id"]
    mock.states[old_sid]["messages"] = [{"role": "assistant", "content": "You've hit your usage limit",
                                          "timestamp": 1_790_000_000_000}]
    path = tmp_path / "tasks.db"
    j = task_controller(Journal(path))
    task = j.submit(project="p", host="h1", workspace="demo-project", original_words=WORDS,
                    idempotency_key="verified-handoff", lead_agent="claude")
    j.change(task["task_id"], "dispatching")
    j.change(task["task_id"], "accepted", fields={"session_id": old_sid})
    j.add_branch(task["task_id"], session_id=old_sid, provider="claude", role="lead", reason="test")
    j.change(task["task_id"], "running")
    old_task = j.change(task["task_id"], "quota_limited")
    adapter = task_bat.BatTaskAdapter(fleet, ObservedVerifier(VerificationSettings()), j)
    try:
        result = await TaskCoordinator(j, adapter).tick(task["task_id"])
        assert result["state"] == "uncertain" and result["session_id"] != old_sid
        assert len(result["branches"]) == 2
        sends = [i for i in mock.invokes if i["channel"] == "claude:send-message"]
        assert len(sends) == 1 and sends[0]["params"]["sessionId"] == result["session_id"]
        failover = next(c for c in j.commands(task["task_id"]) if c["kind"] == "failover")
        handoff = j.command_get(json.loads(failover["payload"])["handoff_command_id"])
        actual_hash = hashlib.sha256(sends[0]["params"]["prompt"].encode()).hexdigest()
        assert json.loads(handoff["payload"])["prompt_sha256"] == actual_hash
        assert registry.get("h1", result["session_id"])["handoff_frame_sha256"] == actual_hash
        j.close()
        reopened = task_controller(Journal(path))
        adapter.journal = reopened
        try:
            assert await adapter.recover_failover(old_task, successor_id=result["session_id"],
                                                  handoff_message_id=handoff["message_id"],
                                                  handoff_command_id=handoff["command_id"]) == {
                                                      "session_id": result["session_id"],
                                                      "marker": handoff["message_id"]}
        finally:
            reopened.close()
    finally:
        await fleet.close()


@pytest.mark.skip(reason="removed: independent reviewer, per-step routing, and mid-task failover")
@authenticated_task_test
async def test_lost_codex_handoff_stays_scoped_uncertain_across_restart(task_controller, tmp_path):
    path = tmp_path / "tasks.db"
    j = task_controller(Journal(path))
    task = submit(j, lead_agent="claude")
    fake = FakeBAT()
    fake.failover_allowed = True
    core = TaskCoordinator(j, fake)
    await core.tick(task["task_id"])
    j.change(task["task_id"], "quota_limited")
    result = await core.tick(task["task_id"])
    successor = result["session_id"]
    assert result["state"] == "uncertain"
    handoff = next(c for c in j.commands(task["task_id"]) if c["kind"] == "send"
                   and json.loads(c["payload"]).get("purpose") == "failover_handoff")
    assert handoff["status"] == "uncertain" and handoff["session_id"] == successor
    assert fake.sends[-1][2] == handoff["message_id"]
    fake.reads[successor] = {"turn_started": True, "turn_done": True,
                             "turn_attribution": "echo_not_visible", "streaming": False,
                             "messages": [{"role": "assistant", "text": REVIEW_PASS}]}
    sends = len(fake.sends)
    assert (await core.tick(task["task_id"]))["state"] == "uncertain"
    assert len(fake.sends) == sends and fake.failover_calls == 1 and not j.get(task["task_id"])["delivered"]
    j.close()
    j = task_controller(Journal(path))
    core = TaskCoordinator(j, fake)
    assert (await core.tick(task["task_id"]))["state"] == "uncertain"
    assert len(fake.sends) == sends
    cap = j.issue_reconcile_capability(task["task_id"], handoff["command_id"])
    fake.reads[successor]["streaming"] = True
    with pytest.raises(ValueError, match="confirmed idle"):
        await core.resolve_command(task["task_id"], handoff["command_id"], token=cap,
                                   outcome="delivered", actor="operator", source="ticket:handoff",
                                   evidence="inspected exact successor and handoff command",
                                   next_prompt="Inspect task state after the handoff")
    fake.reads[successor]["streaming"] = False
    fake.prepare_kind = "codex"
    result = await core.resolve_command(task["task_id"], handoff["command_id"], token=cap,
                                        outcome="delivered", actor="operator", source="ticket:handoff",
                                        evidence="inspected exact successor and handoff command",
                                        next_prompt="Inspect task state after the handoff")
    assert result["state"] == "uncertain" and len(fake.sends) == sends + 1
    assert fake.sends[-1][1] != fake.sends[-2][1]
    assert fake.failover_calls == 1
    assert j.command_get(handoff["command_id"])["status"] == "resolved_delivered"
    assert not j.get(task["task_id"])["delivered"]
    assert (await core.tick(task["task_id"]))["state"] == "uncertain"
    assert len(fake.sends) == sends + 1
    j.close()


@authenticated_task_test
def test_handoff_ledger_preserves_words_beyond_old_2200_limit(task_controller, tmp_path):
    path = tmp_path / "tasks.db"
    j = task_controller(Journal(path))
    words = "前" * 2201 + "完整結尾"
    task = j.submit(project="p", host="h1", workspace="w", original_words=words,
                    idempotency_key="long-request")
    assert words in ledger_summary(j, task["task_id"])
    prompt = task_bat.lifecycle.build_handoff_prompt(
        old_sid="old", workspace="w", cwd="/tmp/synthetic", same_worktree=True,
        branch="feat/test", first_prompt=None, last_prompt=None, recent=[], git={},
        evidence="quota", instructions=ledger_summary(j, task["task_id"]),
        authoritative_original=True)
    assert words in prompt and "overrides the original task's scope" not in prompt
    j.close()
    j = task_controller(Journal(path))
    assert j.get(task["task_id"])["original_words"] == words
    assert words in ledger_summary(j, task["task_id"])
    with pytest.raises(ValueError, match="too long"):
        j.submit(project="p", host="h1", workspace="w", original_words="x" * 19_001,
                 idempotency_key="over-bat-limit")
    j.close()


@pytest.mark.parametrize("word_count", [17_950, 18_050])
@authenticated_task_test
async def test_failover_full_original_archive_verified_and_restart(task_controller, mock, tmp_path, monkeypatch, word_count):
    path = tmp_path / "tasks.db"
    daemon = task_controller(TaskDaemon(make_config(mock, writes=True, orchestrate=True), path))
    words = "Ted 原話。" + "字" * word_count + "END-VERBATIM"
    task = daemon.journal.submit(project="p", host="h1", workspace="w", original_words=words,
                                 idempotency_key=f"long:{word_count}", lead_agent="claude")
    daemon.journal.change(task["task_id"], "dispatching")
    daemon.journal.change(task["task_id"], "accepted", fields={"session_id": "old"})
    daemon.journal.change(task["task_id"], "running")
    task = daemon.journal.change(task["task_id"], "quota_limited")
    _, handoff = daemon.journal.reserve_failover(task["task_id"], "old", "successor")
    monkeypatch.setenv("BATC_TASK_LOCAL_HOST_ALIAS", "h1")
    monkeypatch.setattr(task_bat.registry, "get", lambda host, sid: {"agent_preset": "claude-agent"})
    called = []

    async def fake_failover(*args, **kwargs):
        called.append(kwargs)
        kwargs["task_authority"].before_handoff_send("synthetic BAT handoff\n" + kwargs["instructions"])
        return {"new_session_id": "successor", "prompt_sent": True,
                "message_id": kwargs["task_authority"].handoff_message_id}

    monkeypatch.setattr(task_bat.lifecycle, "session_failover", fake_failover)
    async def verified(*args):
        return True
    monkeypatch.setattr(daemon.adapter, "_verified_failover_successor", verified)
    try:
        with pytest.raises(ValueError, match="host verifier is unset"):
            await daemon.adapter.failover(task, "old", "successor",
                                          handoff_message_id=handoff["message_id"],
                                          handoff_command_id=handoff["command_id"])
        assert not called
        monkeypatch.setenv("BATC_TASK_ARCHIVE_VERIFY_SSH_HOST", "bat-local-user")

        monkeypatch.setattr(task_bat.subprocess, "run", lambda command, **kwargs:
                            subprocess.CompletedProcess(command, 1, "", "unreadable"))
        with pytest.raises(ValueError, match="cannot verify"):
            await daemon.adapter.failover(task, "old", "successor",
                                          handoff_message_id=handoff["message_id"],
                                          handoff_command_id=handoff["command_id"])
        assert not called

        def fake_ssh(command, **kwargs):
            archive = next(p for p in (tmp_path / "handoff-archive").glob("*.original.txt")
                           if str(p) in command[-1])
            return subprocess.CompletedProcess(command, 0,
                                               hashlib.sha256(archive.read_bytes()).hexdigest() + "  file\n", "")

        monkeypatch.setattr(task_bat.subprocess, "run", fake_ssh)
        result = await daemon.adapter.failover(task, "old", "successor",
                                               handoff_message_id=handoff["message_id"],
                                               handoff_command_id=handoff["command_id"])
        assert result["marker"] == handoff["message_id"] and len(called) == 1
        archives = list((tmp_path / "handoff-archive").glob("*.original.txt"))
        referenced = next(p for p in archives if str(p) in called[0]["instructions"])
        assert referenced.stat().st_mode & 0o777 == 0o600
        assert referenced.read_text() == words
        assert hashlib.sha256(words.encode()).hexdigest() in called[0]["instructions"]
        assert json.loads(daemon.journal.command_get(handoff["command_id"])["payload"])["prompt_sha256"]
        daemon.journal.close()
        reopened = task_controller(Journal(path))
        assert reopened.get(task["task_id"])["original_words"] == referenced.read_text()
        reopened.close()
    finally:
        await daemon.fleet.close()


@authenticated_task_test
async def test_daemon_lock_and_rpc_task_scope(task_controller, mock, tmp_path):
    path = tmp_path / "tasks.db"
    first = TaskDaemon(make_config(mock, writes=True, orchestrate=True), path)
    second = TaskDaemon(make_config(mock, writes=True, orchestrate=True), path)
    first.acquire_owner()
    with pytest.raises(RuntimeError, match="another task daemon"):
        second.acquire_owner()
    first.release_owner()
    second.acquire_owner()
    second.release_owner()
    task_controller(first)
    task_a = submit(first.journal, engine="goose")
    task_b = first.journal.submit(project="p", host="h1", workspace="w", original_words="other",
                                  engine="goose", idempotency_key="discord:message:2")
    with pytest.raises(ValueError, match="caller-supplied"):
        await first.call("task_run_verification", {"task_id": task_a["task_id"], "exit_code": 0}, principal=TASK_CALLER)
    cap = first.journal.issue_capability(task_a["task_id"])
    server = await asyncio.start_server(first._handle, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]

    async def rpc(token, method, task_id):
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        body = json.dumps({"method": method, "params": {"task_id": task_id}}).encode()
        writer.write(b"POST /rpc HTTP/1.1\r\nHost: localhost\r\nAuthorization: Bearer " +
                     token.encode() + b"\r\nContent-Length: " + str(len(body)).encode() +
                     b"\r\n\r\n" + body)
        await writer.drain()
        raw = await reader.read()
        writer.close()
        await writer.wait_closed()
        return raw

    try:
        assert b"authorization failed" not in await rpc(first._admin_token, "work_status", task_a["task_id"])
        assert b"400 Bad Request" in await rpc("bad", "work_status", task_a["task_id"])
        assert b"400 Bad Request" in await rpc(cap, "work_status", task_a["task_id"])
        assert b"400 Bad Request" in await rpc(cap, "task_read", task_b["task_id"])
    finally:
        server.close()
        await server.wait_closed()
        await first.fleet.close()
        await second.fleet.close()
        first.journal.close()
        second.journal.close()


@authenticated_task_test
async def test_live_goose_remains_disabled_after_restart(task_controller, mock, tmp_path):
    path = tmp_path / "tasks.db"
    daemon = task_controller(TaskDaemon(make_config(mock, writes=True, orchestrate=True), path))
    task = submit(daemon.journal, engine="goose")
    await daemon._tick_task(task["task_id"])
    assert daemon.journal.get(task["task_id"])["state"] == "queued"  # switch off: start nothing
    await daemon.fleet.close()
    daemon.journal.close()
    again = task_controller(TaskDaemon(make_config(mock, writes=True, orchestrate=True), path))
    try:
        await again._tick_task(task["task_id"])
        assert again.journal.get(task["task_id"])["state"] == "queued"
        assert not again.journal.commands(task["task_id"])
        submitted = await again.call("work_submit", {"project": "p", "host": "h1", "workspace": "w",
                                                     "original_words": WORDS, "idempotency_key": "new"}, principal=TASK_CALLER)
        assert submitted["engine"] == "goose" and submitted["goose"] == "disabled"
        assert again.journal.get(submitted["task_id"])["state"] == "queued"
    finally:
        await again.fleet.close()
        again.journal.close()


def test_provider_config_contract_and_structured_errors(tmp_path):
    assert GooseConfig().provider == "claude"
    assert ProviderCatalog().order == ("claude", "agy-claude", "codex")
    assert ProviderCatalog().entry("claude").model == "claude-opus-5-5"
    assert ProviderCatalog().entry("agy-claude").model == "claude-opus-4-6-thinking"
    with pytest.raises(ValueError, match="claude-opus-5-5"):
        ProviderEntry("claude", "claude-acp", model="claude-sonnet-5")
    with pytest.raises(ValueError, match="Opus|opus"):
        ProviderEntry("agy-claude", "agy-shim", "http://127.0.0.1:18796/v1",
                      "claude-sonnet-4-6", 2)
    path = tmp_path / "providers.toml"
    path.write_text('''fallback_order = ["claude", "agy-claude", "codex"]
[[providers]]
id = "claude"
kind = "claude-acp"
model = "claude-opus-5-5"
[[providers]]
id = "agy-claude"
kind = "agy-shim"
base_url = "http://127.0.0.1:18795/v1"
model = "claude-opus-4-6-thinking"
daily_cap = 2
[[providers]]
id = "codex"
kind = "codex-acp"
''')
    path.chmod(0o600)
    catalog = ProviderCatalog.from_file(path)
    assert catalog.order == ("claude", "agy-claude", "codex")
    assert catalog.environment("codex", {})["GOOSE_PROVIDER"] == "chatgpt_codex"
    assert catalog.environment("claude", {}) == {"GOOSE_PROVIDER": "claude-acp",
                                                   "GOOSE_MODEL": "claude-opus-5-5"}
    gemini = ProviderCatalog([ProviderEntry("gemini", "gemini", model="gemini-flash")], ("gemini",))
    assert gemini.environment("gemini", {}) == {"GOOSE_PROVIDER": "gemini", "GOOSE_MODEL": "gemini-flash"}
    with pytest.raises(ProviderSetupError, match="Gemini model"):
        ProviderCatalog([ProviderEntry("gemini", "gemini")], ("gemini",)).environment("gemini", {})
    assert classify_provider_error(status=429) == "rate_limited"
    assert classify_provider_error(code="insufficient_quota") == "quota_error"
    assert classify_provider_error(status=401) == "auth_error"
    assert classify_provider_error(code="unknown") is None
    path.chmod(0o644)
    with pytest.raises(ValueError, match="mode 0600"):
        ProviderCatalog.from_file(path)


def test_observed_verifier_ssh_command_keeps_remote_script_quoted():
    from bat_agent_connector.task_verifier import _remote_command

    cmd = _remote_command("worker-1", "/srv/work tree", ("pytest", "-q", "tests/test_task_service.py"))
    assert cmd[:4] == ("ssh", "-o", "BatchMode=yes", "worker-1")
    import shlex
    script = "cd -- " + shlex.quote("/srv/work tree") + " && pytest -q tests/test_task_service.py"
    assert cmd[4] == "sh -lc " + shlex.quote(script)


async def test_observed_verification_artifact_bound_to_clean_commit(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "--template=", "-q", str(repo)], check=True)
    (repo / "code.txt").write_text("candidate")
    subprocess.run(["git", "-C", str(repo), "add", "code.txt"], check=True)
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=Test",
                    "-c", "user.email=test@example.invalid", "commit", "-qm", "candidate"], check=True)
    artifacts = tmp_path / "artifacts"
    runner = ObservedVerifier(VerificationSettings(
        commands={"p": (sys.executable, "-c", "print('proof')", "fake-private-argument")},
        artifact_dir=str(artifacts)))
    task = {"task_id": "synthetic", "host": "local", "project": "p"}
    result = await runner.observe(task, str(repo))
    assert result and result["exit_code"] == 0
    assert result["output_sha256"] == hashlib.sha256(b"proof\n").hexdigest()
    assert result["candidate_commit"] == (await runner.identity(task, str(repo)))["candidate_commit"]
    assert result["command"].startswith("argv_sha256:")
    assert "fake-private-argument" not in json.dumps(result)
    assert os.stat(result["log_ref"]).st_mode & 0o777 == 0o600
    assert artifacts.stat().st_mode & 0o777 == 0o700
    (repo / "code.txt").write_text("dirty")
    assert await runner.observe(task, str(repo)) is None


def _committed_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "--template=", "-q", str(repo)], check=True)
    (repo / "code.txt").write_text("candidate")
    subprocess.run(["git", "-C", str(repo), "add", "code.txt"], check=True)
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=Test",
                    "-c", "user.email=test@example.invalid", "commit", "-qm", "candidate"], check=True)
    return repo


def _pid_alive(pid):
    try:
        state = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0]
    except (OSError, IndexError):
        return False
    return state != "Z"


# The child closes its output but keeps running, and leaves a background
# grandchild: drain() finishes early and an unbounded proc.wait() would hang.
HANGING = ("sh", "-c", 'sleep 60 & echo $! > "$0"; exec >/dev/null 2>&1; sleep 60')


async def test_observed_verifier_one_deadline_kills_local_group(tmp_path):
    repo = _committed_repo(tmp_path)
    pidfile = tmp_path / "grandchild.pid"
    runner = ObservedVerifier(VerificationSettings(
        commands={"p": (*HANGING, str(pidfile))}, timeout_s=1,
        artifact_dir=str(tmp_path / "artifacts")))
    started = time.monotonic()
    result = await runner.observe({"task_id": "hang", "host": "local", "project": "p"}, str(repo))
    assert time.monotonic() - started < 10
    assert result["exit_code"] == 124
    assert not _pid_alive(int(pidfile.read_text()))


async def test_observed_verifier_ssh_timeout_kills_remote_tree(tmp_path, monkeypatch):
    repo = _committed_repo(tmp_path)
    bindir = tmp_path / "bin"
    bindir.mkdir()
    fake_ssh = bindir / "ssh"
    # Stand-in for ssh: run the final remote-command argument with a local shell.
    fake_ssh.write_text('#!/bin/sh\neval "last=\\${$#}"\nexec sh -c "$last"\n')
    fake_ssh.chmod(0o700)
    monkeypatch.setenv("PATH", str(bindir) + os.pathsep + os.environ["PATH"])
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir()
    pidfile = tmp_path / "remote-grandchild.pid"
    runner = ObservedVerifier(VerificationSettings(
        commands={"p": (*HANGING, str(pidfile))}, timeout_s=1,
        ssh_hosts={"host-a": "worker-1"}, artifact_dir=str(tmp_path / "artifacts")))
    task = {"task_id": "hang-ssh", "host": "host-a", "project": "p"}
    started = time.monotonic()
    result = await runner.observe(task, str(repo))
    assert time.monotonic() - started < 15
    assert result["exit_code"] == 124
    assert not _pid_alive(int(pidfile.read_text()))
    assert not list((tmp_path / "home").glob(".batc-verify-*.pid"))
    # A normal remote run removes its pgid file and keeps the exit status.
    ok = ObservedVerifier(VerificationSettings(
        commands={"p": ("sh", "-c", "echo ok; exit 3")}, ssh_hosts={"host-a": "worker-1"},
        artifact_dir=str(tmp_path / "artifacts")))
    done = await ok.observe(task, str(repo))
    assert done["exit_code"] == 3
    assert not list((tmp_path / "home").glob(".batc-verify-*.pid"))


def test_verification_failure_classes(tmp_path):
    from bat_agent_connector.task_verifier import classify_failure

    def evidence(text, code=1):
        log = tmp_path / f"log-{abs(hash(text))}.log"
        log.write_text(text)
        return {"exit_code": code, "log_ref": str(log)}

    assert classify_failure(evidence("FAILED tests/test_x.py::test_a - assert 1 == 2")) == "code"
    assert classify_failure(evidence("sh: 1: vitest: not found")) == "missing_dependencies"
    assert classify_failure(evidence("Error: Cannot find module 'zod'")) == "missing_dependencies"
    assert classify_failure(evidence("fatal: could not read Username for 'https://github.com'")) == "environment"
    assert classify_failure(evidence("npm ERR! code EACCES")) == "environment"
    assert classify_failure(evidence("anything", code=124)) == "environment"
    assert classify_failure({"exit_code": 1, "log_ref": str(tmp_path / "missing.log")}) == "environment"


async def test_install_dependencies_uses_tracked_lockfile_once_and_stays_clean(tmp_path, monkeypatch):
    from bat_agent_connector import task_verifier

    repo = _committed_repo(tmp_path)
    (repo / "package-lock.json").write_text("{}")
    (repo / ".gitignore").write_text("node_modules/\n")
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=Test",
                    "-c", "user.email=test@example.invalid", "commit", "-qm", "lock"], check=True)
    monkeypatch.setitem(task_verifier.LOCKFILE_INSTALLS, "package-lock.json",
                        ("sh", "-c", "mkdir -p node_modules && echo installed"))
    runner = ObservedVerifier(VerificationSettings(artifact_dir=str(tmp_path / "artifacts")))
    result = await runner.install_dependencies({"task_id": "deps", "host": "local", "project": "p"}, str(repo))
    assert result["ok"] and result["lockfile"] == "package-lock.json"
    monkeypatch.setitem(task_verifier.LOCKFILE_INSTALLS, "package-lock.json",
                        ("sh", "-c", "echo dirty > package-lock.json"))
    dirty = await runner.install_dependencies({"task_id": "deps", "host": "local", "project": "p"}, str(repo))
    assert not dirty["ok"] and dirty["reason"] == "install_dirtied_worktree"


@pytest.mark.asyncio
@authenticated_task_test
async def test_external_worktree_creation_is_restart_idempotent(task_controller, tmp_path, fleet_factory, monkeypatch):
    journal = task_controller(Journal(tmp_path / "tasks.sqlite3"))
    task = journal.submit(project="p", host="h1", workspace="w", original_words="x",
                          base_branch="feat/task-service", idempotency_key="external-restart")
    adapter = task_bat.BatTaskAdapter(
        fleet_factory(), ObservedVerifier(VerificationSettings(ssh_hosts={"h1": "worker-1"})), journal
    )
    scripts = []

    async def folder(_task):
        return "/srv/repo"

    async def ssh(_task, script):
        scripts.append(script)
        return ""

    async def identity(_task, _cwd):
        return {"candidate_commit": "a" * 40, "tree_hash": "b" * 40, "clean": True}

    monkeypatch.setattr(adapter, "_workspace_folder", folder)
    monkeypatch.setattr(adapter, "_ssh_script", ssh)
    monkeypatch.setattr(adapter.verifier, "identity", identity)
    first = await adapter._ensure_external_worktree(task)
    second = await adapter._ensure_external_worktree(journal.get(task["task_id"]))
    assert first == second
    assert len(scripts) == 2 and all("worktree list --porcelain" in script for script in scripts)


@pytest.mark.asyncio
@authenticated_task_test
async def test_external_worktree_script_quotes_workspace_path(task_controller, tmp_path, fleet_factory, monkeypatch):
    journal = task_controller(Journal(tmp_path / "tasks.sqlite3"))
    task = journal.submit(project="p", host="h1", workspace="w", original_words="x",
                          base_branch="feat/task-service", idempotency_key="quoted-root")
    adapter = task_bat.BatTaskAdapter(
        fleet_factory(), ObservedVerifier(VerificationSettings(ssh_hosts={"h1": "worker-1"})), journal)
    scripts = []

    async def capture(_task, script):
        scripts.append(script)
        return ""

    async def identity(_task, _cwd):
        return {"candidate_commit": "a" * 40, "tree_hash": "b" * 40, "clean": True}

    async def folder(_task):
        return "/srv/Operator's repo"

    monkeypatch.setattr(adapter, "_workspace_folder", folder)
    monkeypatch.setattr(adapter, "_ssh_script", capture)
    monkeypatch.setattr(adapter.verifier, "identity", identity)
    await adapter._ensure_external_worktree(task)
    assert subprocess.run(["bash", "-n", "-c", scripts[0]], check=False).returncode == 0
    assert '"$ref"' in scripts[0]


@pytest.mark.asyncio
@authenticated_task_test
async def test_external_cleanup_retains_unmerged_commit_and_recovers_after_restart(task_controller,
        tmp_path, fleet_factory, monkeypatch):
    root = tmp_path / "Operator's repo"
    root.mkdir()
    subprocess.run(["git", "init", "--template=", "-q", str(root)], check=True)
    (root / "code.txt").write_text("base")
    subprocess.run(["git", "-C", str(root), "add", "code.txt"], check=True)
    commit = ["git", "-C", str(root), "-c", "user.name=Test",
              "-c", "user.email=test@example.invalid", "commit", "-qm"]
    subprocess.run([*commit, "base"], check=True)
    journal_path = tmp_path / "tasks.db"
    journal = task_controller(Journal(journal_path))
    task = journal.submit(project="p", host="h1", workspace="w", original_words="keep work",
                          idempotency_key="cleanup-retain")
    suffix = task["task_id"].replace("-", "")[:12]
    branch = f"batc/task-{suffix}"
    path = root / ".bat-worktrees" / f"batc-task-{suffix}"
    subprocess.run(["git", "-C", str(root), "worktree", "add", "-q", "-b",
                    branch, str(path), "HEAD"], check=True)
    (path / "code.txt").write_text("unmerged candidate")
    subprocess.run(["git", "-C", str(path), "add", "code.txt"], check=True)
    subprocess.run(["git", "-C", str(path), "-c", "user.name=Test",
                    "-c", "user.email=test@example.invalid", "commit", "-qm", "candidate"], check=True)
    candidate = subprocess.run(["git", "-C", str(path), "rev-parse", "HEAD"],
                               check=True, capture_output=True, text=True).stdout.strip()
    journal.change(task["task_id"], "dispatching", fields={
        "external_worktree_path": str(path), "external_branch": branch})
    journal.change(task["task_id"], "failed")
    adapter = task_bat.BatTaskAdapter(fleet_factory(),
        ObservedVerifier(VerificationSettings(ssh_hosts={"h1": "unused"})), journal)

    async def folder(_task):
        return str(root)

    async def local_shell(_task, script):
        result = subprocess.run(["sh", "-lc", script], capture_output=True, text=True, check=False)
        if result.returncode:
            raise ValueError(result.stderr or result.stdout or "cleanup failed")
        return result.stdout.strip()

    monkeypatch.setattr(adapter, "_workspace_folder", folder)
    monkeypatch.setattr(adapter, "_ssh_script", local_shell)
    (path / "untracked.txt").write_text("do not discard")
    with pytest.raises(ValueError):
        await adapter.cleanup_external_worktree(journal.get(task["task_id"]))
    assert path.exists()
    (path / "untracked.txt").unlink()
    with pytest.raises(ValueError):
        await adapter.cleanup_external_worktree({**journal.get(task["task_id"]),
                                                 "state": "done", "verification_commit": "0" * 40})
    assert path.exists()
    first = await adapter.cleanup_external_worktree(journal.get(task["task_id"]))
    assert first == {"path": str(path), "branch": branch,
                     "retained_ref": f"refs/batc/tasks/{suffix}",
                     "commit": candidate, "mode": "removed"}
    assert not path.exists()
    journal.close()  # Crash after Git removal but before journal settlement.
    journal = task_controller(Journal(journal_path))
    assert [t["task_id"] for t in journal.list_cleanup_pending()] == [task["task_id"]]
    second = await adapter.cleanup_external_worktree(journal.get(task["task_id"]))
    assert second["mode"] == "already_removed" and second["commit"] == candidate
    journal.complete_external_cleanup(task["task_id"], second)
    assert not journal.list_cleanup_pending()
    assert journal.get(task["task_id"])["external_worktree_path"] is None
    assert any(e["kind"] == "external_worktree_retained" for e in journal.events(task["task_id"]))
    for ref in (f"refs/heads/{branch}", f"refs/batc/tasks/{suffix}"):
        value = subprocess.run(["git", "-C", str(root), "rev-parse", "--verify", ref],
                               check=True, capture_output=True, text=True).stdout.strip()
        assert value == candidate
    journal.close()


@pytest.mark.asyncio
@authenticated_task_test
async def test_terminal_cleanup_requires_shared_finalization_before_pointer_is_cleared(task_controller, mock, tmp_path, monkeypatch):
    from bat_agent_connector import task_cleanup
    daemon = task_controller(TaskDaemon(make_config(mock, writes=True, orchestrate=True), tmp_path / "tasks.db"))
    task = submit(daemon.journal)
    suffix = task["task_id"].replace("-", "")[:12]
    path = f"/srv/project/.bat-worktrees/batc-task-{suffix}"
    daemon.journal.change(task["task_id"], "dispatching", fields={
        "external_worktree_path": path, "external_branch": f"batc/task-{suffix}"})
    daemon.journal.change(task["task_id"], "failed")
    async def legacy_remover(_task):
        pytest.fail("daemon must not call the independent legacy remover")
    monkeypatch.setattr(daemon.adapter, "cleanup_external_worktree", legacy_remover)
    calls = []
    async def scheduled(_daemon, fixed_task):
        calls.append(fixed_task["task_id"])
        return {"status": "succeeded"}  # A return value is not a durable shared receipt.
    monkeypatch.setattr(task_cleanup, "automatic", scheduled)
    await daemon._tick_task(task["task_id"])
    assert calls == [task["task_id"]]
    assert daemon.journal.get(task["task_id"])["external_worktree_path"] == path
    assert task["task_id"] in daemon._cleanup_retry_after
    assert not any(e["kind"] == "external_worktree_retained" for e in daemon.journal.events(task["task_id"]))
    daemon.journal.close()

@pytest.mark.asyncio
@authenticated_task_test
async def test_goose_acp_error_settles_from_bat_readback(task_controller, mock, tmp_path, monkeypatch, caplog):
    """A lost ACP terminal reply must not discard a committed, idle BAT turn."""
    daemon = task_controller(TaskDaemon(make_config(mock, writes=True, orchestrate=True), tmp_path / "tasks.db"))
    task = submit(daemon.journal, engine="goose")
    daemon.journal.change(task["task_id"], "dispatching")
    daemon.journal.change(task["task_id"], "accepted", fields={"session_id": "lead-readback"})
    adopt("lead-readback", cwd="/remote/not-on-box", task_id=task["task_id"], role="lead")
    start, _ = daemon.journal.command(task["task_id"], "start_lead", "lead-readback", {}, "original-start")
    daemon.journal.command_status(start["command_id"], "settled")

    async def fail_run(*args, **kwargs):
        raise RuntimeError("PRIVATE_PROVIDER_ERROR_DO_NOT_LOG")

    async def identity(_task):
        return {"candidate_commit": "a" * 40, "tree_hash": "b" * 40, "clean": True}

    async def read(_task, _sid, _marker):
        return {"streaming": False, "pending": None}

    class FakeGoose:
        config = GooseConfig(enabled=True)
        run_task = fail_run
    monkeypatch.setattr(daemon, "goose", FakeGoose())
    monkeypatch.setattr(daemon.adapter, "candidate_identity", identity)
    monkeypatch.setattr(daemon.adapter, "read", read)
    await daemon._tick_task(task["task_id"])
    settled = daemon.journal.get(task["task_id"])
    command = next(c for c in daemon.journal.commands(task["task_id"]) if c["kind"] == "goose_run")
    assert settled["state"] == "verifying"
    assert command["status"] == "settled"
    assert any(e["kind"] == "goose_readback_settled" for e in daemon.journal.events(task["task_id"]))
    assert "PRIVATE_PROVIDER_ERROR_DO_NOT_LOG" not in caplog.text
    await daemon.fleet.close()
    daemon.journal.close()

def test_agy_adapter_model_effort_policy(monkeypatch):
    monkeypatch.setenv("BATC_AGY_SHIM_TOKEN", "local-token")
    adapter = AgyShimAdapter()
    claude = ProviderEntry("agy-claude", "agy-shim", "http://127.0.0.1:18797/v1",
                           "claude-opus-4-6-thinking", 1)
    gemini = ProviderEntry("agy-gemini", "agy-shim", "http://127.0.0.1:18797/v1",
                           "gemini-3.8-flash", 1)
    assert adapter.environment(claude, {"BATC_AGY_SHIM_TOKEN": "local-token"})["BATC_AGY_REQUEST_POLICY"] == "omit_reasoning_effort"
    assert adapter.environment(gemini, {"BATC_AGY_SHIM_TOKEN": "local-token"})["BATC_AGY_REQUEST_POLICY"] == "pass_reasoning_effort"

@pytest.mark.asyncio
@authenticated_task_test
async def test_session_presence_retries_transient_workspace_timeout(task_controller, fleet_factory, tmp_path, monkeypatch):
    fleet = fleet_factory(writes=True, orchestrate=True, tabs=False,
                          safety={"write_min_interval_s": 0})
    j = task_controller(Journal(tmp_path / "tasks.db"))
    task = j.submit(project="p", host="h1", workspace="demo-project", original_words=WORDS,
                    idempotency_key="presence-retry")
    sid = "presence-retry-session"
    j.add_branch(task["task_id"], session_id=sid, provider="codex", role="lead", reason="start")
    adapter = task_bat.BatTaskAdapter(fleet, ObservedVerifier(VerificationSettings()), j)
    calls = 0
    client = fleet.client("h1")
    async def invoke(channel, params, **kwargs):
        nonlocal calls
        if channel == "claude:get-session-meta":
            calls += 1
            if calls < 3:
                raise TimeoutError("workspace:load transient timeout")
            return {"cwd": "/srv/demo", "isStreaming": False}
        return {}
    monkeypatch.setattr(client, "invoke", invoke)
    async def restore(*args, **kwargs):
        return None
    monkeypatch.setattr(adapter, "_restore_headless_lookup", restore)
    try:
        assert await adapter.session_presence(task, sid) == "present"
        assert calls == 3
    finally:
        await fleet.close()
        j.close()


@pytest.mark.asyncio
@authenticated_task_test
async def test_reviewer_start_polls_existing_session_after_start_timeout(task_controller,
        fleet_factory, tmp_path, monkeypatch):
    fleet = fleet_factory(writes=True, orchestrate=True, tabs=False,
                          safety={"write_min_interval_s": 0})
    j = task_controller(Journal(tmp_path / "tasks.db"))
    task = j.submit(project="p", host="h1", workspace="demo-project", original_words=WORDS,
                    idempotency_key="reviewer-start-retry")
    lead = "reviewer-start-lead"
    j.add_branch(task["task_id"], session_id=lead, provider="codex", role="lead", reason="start")
    j.change(task["task_id"], "dispatching")
    task = j.change(task["task_id"], "accepted", fields={"session_id": lead})
    adapter = task_bat.BatTaskAdapter(fleet, ObservedVerifier(VerificationSettings()), j)
    lead_entry = {"session_id": lead, "workspace_id": "ws-1", "workspace_name": "demo-project",
                  "origin_cwd": "/srv/demo", "cwd": "/srv/demo", "worktree_path": "/srv/demo",
                  "branch": "branch", "agent_preset": "codex-agent"}
    monkeypatch.setattr(task_bat.registry, "get", lambda _h, sid: lead_entry if sid == lead else None)
    monkeypatch.setattr(task_bat.registry, "reserve", lambda *a, **k: None)
    monkeypatch.setattr(task_bat.registry, "update", lambda *a, **k: None)
    monkeypatch.setattr(adapter, "session_presence", lambda *a, **k: asyncio.sleep(0, result="present"))

    async def shared_grant(_fleet, host, sid, owner):
        assert owner["id"] == lead
        return task_bat.resource_policy.WriteGrant(
            host, "session.create", sid, task_bat.resource_policy.BY_ACTION["session.create"].channels)

    monkeypatch.setattr(task_bat.resource_policy, "authorize_shared_session", shared_grant)
    calls = []
    client = fleet.client("h1")
    async def invoke(channel, params, **kwargs):
        calls.append(channel)
        if channel == "claude:start-session":
            kwargs["on_transport"]()  # Model a lost reply after the frame, not a pre-transport timeout.
            raise TimeoutError("host-a workspace load timeout")
        if channel == "claude:get-session-meta":
            return {"cwd": "/srv/demo", "isStreaming": False}
        return {}
    monkeypatch.setattr(client, "invoke", invoke)
    try:
        result = await adapter.start({**task, "branches": [{"session_id": lead, "role": "lead"}]},
                                     role="reviewer", agent="codex", session_id="reviewer-retry")
        assert result == "reviewer-retry"
        assert calls.count("claude:start-session") == 1
        assert "claude:get-session-meta" in calls
    finally:
        await fleet.close()
        j.close()


class _Receiver:
    """Loopback JSON webhook that records requests and replies with scripted statuses."""

    def __init__(self, statuses=None):
        import http.server
        import threading

        self.requests = []
        self.statuses = list(statuses or [])
        receiver = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(self):  # noqa: N802
                body = self.rfile.read(int(self.headers["Content-Length"]))
                receiver.requests.append(({k.lower(): v for k, v in self.headers.items()}, body))
                status = receiver.statuses.pop(0) if receiver.statuses else 200
                self.send_response(status)
                self.end_headers()
                self.wfile.write(b"{}")

            def log_message(self, *args):
                pass

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}/webhooks/task-events"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()


def _advance(j, tid, *states):
    for state in states:
        j.change(tid, state)


@authenticated_task_test
async def test_event_push_starts_at_now_signs_and_advances_in_order(task_controller, tmp_path):
    from bat_agent_connector.task_push import EventPusher, EventWebhook, sign

    j = task_controller(Journal(tmp_path / "tasks.db"))
    old = submit(j, discord_thread_id="origin-old")["task_id"]
    _advance(j, old, "dispatching", "accepted")  # history before push is configured
    receiver = _Receiver()
    secret = "s" * 40
    pusher = EventPusher(j, EventWebhook(receiver.url, secret))
    try:
        assert await pusher.run_once() == 0 and receiver.requests == []  # backlog never pushed
        tid = j.submit(project="p", host="h1", workspace="w", original_words=WORDS,
                       idempotency_key="k2", discord_thread_id="origin-2")["task_id"]
        _advance(j, tid, "dispatching", "accepted", "running")
        j.change(tid, "needs_ted", fields={"result": "Pick a license"})
        assert await pusher.run_once() == 2
        payloads = [json.loads(body) for _, body in receiver.requests]
        assert [p["kind"] for p in payloads] == ["started", "needs_ted"]
        assert payloads[0]["type"] == "task.milestone" and payloads[0]["origin_thread_id"] == "origin-2"
        assert payloads[1]["delivered_through"] == payloads[0]["cursor"]
        headers, body = receiver.requests[0]
        assert headers["x-webhook-signature-v2"] == sign(secret, headers["x-webhook-timestamp"], body)
        assert headers["x-request-id"] == f"batc-milestone-{payloads[0]['cursor']}-0"
        assert j.push_state()["cursor"] == j.head_cursor()
        assert await pusher.run_once() == 0 and len(receiver.requests) == 2  # no re-push
    finally:
        receiver.close()
        j.close()


@authenticated_task_test
async def test_event_push_failure_backs_off_and_retries_without_skipping(task_controller, tmp_path):
    from bat_agent_connector.task_push import EventPusher, EventWebhook

    now = [1000.0]
    j = task_controller(Journal(tmp_path / "tasks.db"))
    receiver = _Receiver(statuses=[502])
    pusher = EventPusher(j, EventWebhook(receiver.url), clock=lambda: now[0])
    try:
        await pusher.run_once()  # initialise at head
        tid = submit(j, discord_thread_id="origin")["task_id"]
        _advance(j, tid, "dispatching", "accepted")
        before = j.push_state()["cursor"]
        assert await pusher.run_once() == 0
        state = j.push_state()
        assert state["cursor"] == before and state["failures"] == 1 and state["last_error"] == "HTTP 502"
        assert await pusher.run_once() == 0 and len(receiver.requests) == 1  # still backing off
        now[0] += 3
        assert await pusher.run_once() == 1
        ids = [h["x-request-id"] for h, _ in receiver.requests]
        assert ids[0].endswith("-0") and ids[1].endswith("-1")  # retry is not a receiver-side duplicate
        assert j.push_state()["failures"] == 0 and j.push_state()["cursor"] == j.head_cursor()
    finally:
        receiver.close()
        j.close()


@authenticated_task_test
async def test_event_webhook_settings_require_loopback_and_private_secret(task_controller, mock, tmp_path, monkeypatch):
    from bat_agent_connector.task_push import validate_callback_url

    for bad in ("http://example.com/hook", "http://user:pw@127.0.0.1/x", "ftp://127.0.0.1/x"):
        with pytest.raises(ValueError):
            validate_callback_url(bad)
    secret = tmp_path / "hook.secret"
    secret.write_text("x" * 40)
    secret.chmod(0o644)
    settings = tmp_path / "task-settings.toml"
    settings.write_text('[task_service.event_webhook]\nurl = "http://127.0.0.1:8644/webhooks/task-events"\n'
                        f'secret_file = "{secret}"\n')
    settings.chmod(0o600)
    monkeypatch.setenv("BATC_TASK_SETTINGS", str(settings))
    with pytest.raises(ValueError, match="0600"):
        task_controller(TaskDaemon(make_config(mock, writes=True, orchestrate=True), tmp_path / "tasks.db")).acquire_owner()
    secret.chmod(0o600)
    daemon = task_controller(TaskDaemon(make_config(mock, writes=True, orchestrate=True), tmp_path / "tasks2.db"))
    try:
        assert daemon.pusher.webhook.url.endswith("/webhooks/task-events")
        assert daemon.pusher.webhook.secret == "x" * 40
        feed = await daemon.call("work_events", {"limit": 0}, principal=TASK_CALLER)
        assert feed["push"] == {"configured": True}
    finally:
        await daemon.fleet.close()
        daemon.journal.close()
