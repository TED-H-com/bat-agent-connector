import json

import pytest

from bat_agent_connector.task_core import (
    TaskCoordinator,
    classify_read,
    initial_prompt,
    review_verdict,
)
from bat_agent_connector.task_journal import Journal


def test_initial_prompt():
    task = {
        "engine": "goose",
        "payload": {"prompt": "Fix this bug"},
        "original_words": "Fix this bug",
    }
    assert "Fix this bug" in initial_prompt(task)


def test_classify_read():
    read = {"pending": {"kind": "permission"}}
    assert classify_read(read) == "waiting_permission"

    read = {"pending": {"kind": "other"}}
    assert classify_read(read) == "needs_ted"

    read = {"messages": [{"role": "assistant", "text": "I am working on it"}]}
    assert classify_read(read) == "running"

    read = {
        "turn_done": True,
        "messages": [{"role": "assistant", "text": "BAT-STATUS: NEED-HUMAN\n"}],
    }
    assert classify_read(read) == "needs_ted"

    read = {"messages": [{"role": "assistant", "text": "quota exhausted"}]}
    assert classify_read(read) == "quota_limited"

    read = {
        "turn_done": True,
        "messages": [{"role": "assistant", "text": "BAT-STATUS: MILESTONE\n"}],
    }
    assert classify_read(read) == "verifying"

    read = {
        "turn_done": True,
        "messages": [{"role": "assistant", "text": "BAT-STATUS: CONTINUE\n"}],
    }
    assert classify_read(read) == "continue"


def test_review_verdict():
    # Test verdict_missing
    read = {"messages": [{"role": "assistant", "text": "no verdict here"}]}
    res = review_verdict(read, "c1", "t1")
    assert res["verdict"] == "reject"
    assert res["reason"] == "verdict_missing"

    # Test final_message_truncated
    read = {
        "messages": [
            {"role": "assistant", "text": '{"verdict": "pass"} \u2026[+123 chars]'}
        ]
    }
    res = review_verdict(read, "c1", "t1")
    assert res["verdict"] == "reject"
    assert res["reason"] == "final_message_truncated"

    # Test valid pass
    verdict_json = json.dumps(
        {
            "verdict": "pass",
            "candidate_commit": "c1",
            "tree_hash": "t1",
            "findings": [],
        }
    )
    read = {"messages": [{"role": "assistant", "text": verdict_json}]}
    res = review_verdict(read, "c1", "t1")
    assert res["verdict"] == "pass"

    # Test pass with high severity findings
    verdict_json = json.dumps(
        {
            "verdict": "pass",
            "candidate_commit": "c1",
            "tree_hash": "t1",
            "findings": [{"severity": "high"}],
        }
    )
    read = {"messages": [{"role": "assistant", "text": verdict_json}]}
    res = review_verdict(read, "c1", "t1")
    assert res["verdict"] == "reject"
    assert res["reason"] == "pass_with_high_findings"

    # Test conflicting verdicts
    verdict_json1 = json.dumps({"verdict": "pass", "findings": []})
    verdict_json2 = json.dumps({"verdict": "reject", "findings": []})
    read = {
        "messages": [
            {
                "role": "assistant",
                "text": f"{verdict_json1}\n{verdict_json2}\nREVIEW: PASS\nREVIEW: REJECT",
            }
        ]
    }
    res = review_verdict(read, "c1", "t1")
    assert res["verdict"] == "reject"
    assert res["reason"] == "verdict_conflicting"


class FakeAdapter:
    async def available_agents(self, task):
        return {"agent1"}

    async def candidate_identity(self, task):
        return {
            "clean": True,
            "reviewer_session_id": "r1",
            "candidate_commit": "c1",
            "tree_hash": "t1",
        }

    async def interrupt(self, task, session_id):
        pass

    async def recover_start(self, task, role, session_id):
        return True

    async def read(self, task, session_id, marker=None):
        return {
            "streaming": False,
            "pending": False,
            "turn_done": True,
            "messages": [{"role": "assistant", "text": "BAT-STATUS: CONTINUE"}],
        }

    async def failover(
        self, task, session_id, successor_id, handoff_message_id, handoff_command_id
    ):
        return {"status": "success"}


@pytest.mark.asyncio
async def test_coordinator_tick_verifying(tmp_path, monkeypatch):
    journal = Journal(tmp_path / "test.db")
    memory_db = journal.db

    # insert dummy task
    memory_db.execute(
        "INSERT INTO tasks (task_id, idem_key, engine, recipe, payload_hash, project, host, workspace, original_words, acceptance, state, paused, control_version, submitted_at, updated_at, session_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "task_1",
            "k1",
            "goose",
            "recipe1",
            "hash1",
            "project1",
            "host1",
            "workspace1",
            "original_words_1",
            "auto",
            "verifying",
            0,
            1,
            0.0,
            0.0,
            "session_1",
        ),
    )

    adapter = FakeAdapter()
    coordinator = TaskCoordinator(journal, adapter)

    import bat_agent_connector.task_control as task_control

    monkeypatch.setattr(task_control, "require_authority", lambda task: True)

    async def mock_verify(task):
        pass

    coordinator._verify_and_review = mock_verify

    await coordinator._tick("task_1")


@pytest.mark.asyncio
async def test_coordinator_failover(tmp_path, monkeypatch):
    journal = Journal(tmp_path / "test.db")
    memory_db = journal.db

    memory_db.execute(
        "INSERT INTO tasks (task_id, idem_key, engine, recipe, payload_hash, project, host, workspace, original_words, acceptance, state, paused, control_version, submitted_at, updated_at, session_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "task_1",
            "k1",
            "goose",
            "recipe1",
            "hash1",
            "project1",
            "host1",
            "workspace1",
            "words",
            "auto",
            "quota_limited",
            0,
            1,
            0.0,
            0.0,
            "session_1",
        ),
    )

    memory_db.execute(
        "INSERT INTO commands (task_id, command_id, idem_key, kind, status, session_id, payload, created_at, updated_at, message_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "task_1",
            "cmd2",
            "k3",
            "send",
            "needs_review",
            "session_2",
            '{"purpose": "failover_handoff", "old_session_id": "session_1"}',
            0.0,
            0.0,
            "msg1"
        ),
    )

    memory_db.execute(
        "INSERT INTO commands (task_id, command_id, idem_key, kind, status, session_id, payload, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "task_1",
            "cmd1",
            "k2",
            "failover",
            "intent",
            "session_2",
            '{"handoff_message_id": "msg1", "handoff_command_id": "cmd2", "old_session_id": "session_1"}',
            0.0,
            0.0,
        ),
    )


    adapter = FakeAdapter()
    coordinator = TaskCoordinator(journal, adapter)

    import bat_agent_connector.task_control as task_control

    monkeypatch.setattr(task_control, "require_authority", lambda task: True)

    task = journal.get("task_1")
    # This should now throw ValueError because commands need reconciliation
    with pytest.raises(ValueError, match="task has a command requiring reconciliation"):
        await coordinator._failover(task)


@pytest.mark.asyncio
async def test_coordinator_verify_and_review(tmp_path, monkeypatch):
    journal = Journal(tmp_path / "test.db")
    memory_db = journal.db

    memory_db.execute(
        "INSERT INTO tasks (task_id, idem_key, engine, recipe, payload_hash, project, host, workspace, original_words, acceptance, state, paused, control_version, submitted_at, updated_at, session_id, verification_commit, verification_tree) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "task_1",
            "k1",
            "goose",
            "recipe1",
            "hash1",
            "project1",
            "host1",
            "workspace1",
            "words",
            "auto",
            "verifying",
            0,
            1,
            0.0,
            0.0,
            "session_1",
            "c1",
            "t1",
        ),
    )

    adapter = FakeAdapter()

    async def mock_run_verification(task):
        return {
            "source": "observed_runner",
            "candidate_commit": "c1",
            "tree_hash": "t1",
            "command": "test",
            "exit_code": 0,
            "log_ref": "log",
            "output_sha256": "hash",
        }

    adapter.run_verification = mock_run_verification

    coordinator = TaskCoordinator(journal, adapter)
    coordinator.minimal_review_gate = lambda *args: None

    async def mock_route(*args, **kwargs):
        pass

    coordinator._route = mock_route

    import bat_agent_connector.task_control as task_control

    monkeypatch.setattr(task_control, "require_authority", lambda task: True)

    task = journal.get("task_1")
    await coordinator._verify_and_review(task)

    task = journal.get("task_1")
    assert task["state"] == "done"
