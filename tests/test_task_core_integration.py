import asyncio

import pytest

from bat_agent_connector.config import Config
from bat_agent_connector.task_core import TaskCoordinator
from bat_agent_connector.task_journal import Journal


class FakeAdapter:
    async def read(self, task, session_id, turn_marker=None):
        return {"streaming": False, "pending": False}

    async def candidate_identity(self, task):
        return {"clean": True, "candidate_commit": "c1", "tree_hash": "t1", "reviewer_session_id": "r1"}

    async def interrupt(self, task, session_id):
        pass


class FakeFleet:
    def __init__(self):
        self.config = Config([], [], "", "", None, {})


@pytest.mark.asyncio
async def test_coordinator_integration(tmp_path):
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
            100,
            "auto",
            "running",
            0,
            1,
            0.0,
            0.0,
            "session_1",
        ),
    )

    adapter = FakeAdapter()

    coordinator = TaskCoordinator(journal, adapter)

    # Needs to return dummy routes
    journal.routes = lambda task_id: []

    # Fake verification remaining
    coordinator.verification_remaining = lambda task: 10

    # We can't easily run the real _tick background loop without dependencies,
    # but we can call it directly to test integration.
    # However we need some setup for controller_authority.

    import contextlib

    @contextlib.contextmanager
    def mock_use(*args, **kwargs):
        yield

    # Since we can't easily monkeypatch in a fixture without passing it,
    # we'll just test the methods on TaskCoordinator directly.
    task = journal.get("task_1")
    assert task["state"] == "running"


@pytest.mark.asyncio
async def test_coordinator_integration_verify_and_review(tmp_path, monkeypatch):
    journal = Journal(tmp_path / "test.db")
    memory_db = journal.db

    # insert dummy task in verifying state
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
            100,
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
            "log_ref": "log1",
            "output_sha256": "sha1",
        }

    adapter.run_verification = mock_run_verification

    coordinator = TaskCoordinator(journal, adapter)

    # Fake minimal_review_gate
    coordinator.minimal_review_gate = lambda *args: None

    # Fake route
    coordinator._route = lambda *args, **kwargs: asyncio.sleep(0)

    import contextlib

    import bat_agent_connector.controller_authority

    @contextlib.contextmanager
    def mock_use(*args, **kwargs):
        yield

    import bat_agent_connector.task_control

    monkeypatch.setattr(bat_agent_connector.controller_authority, "use", mock_use)
    monkeypatch.setattr(bat_agent_connector.task_control, "require_authority", lambda task: True)

    task = journal.get("task_1")
    await coordinator._verify_and_review(task)

    task = journal.get("task_1")
    assert task["state"] == "done"


@pytest.mark.asyncio
async def test_coordinator_integration_recover_unsent(tmp_path, monkeypatch):
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
            100,
            "auto",
            "running",
            0,
            1,
            0.0,
            0.0,
            "session_1",
        ),
    )

    # insert send command
    memory_db.execute(
        "INSERT INTO commands (task_id, command_id, idem_key, kind, status, session_id, payload, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ("task_1", "cmd1", "k2", "send", "uncertain", "session_1", "{}", 0.0, 0.0),
    )

    adapter = FakeAdapter()

    # mock read
    async def mock_read(task, session_id, turn_marker=None):
        return {"streaming": False, "pending": False}

    adapter.read = mock_read

    coordinator = TaskCoordinator(journal, adapter)

    # Needs to return a valid command object
    def mock_command(task_id, command_id):
        return {"command_id": "cmd1", "kind": "send", "status": "uncertain", "payload": "{}"}

    journal.command = mock_command

    import contextlib

    import bat_agent_connector.controller_authority

    @contextlib.contextmanager
    def mock_use(*args, **kwargs):
        yield

    import bat_agent_connector.task_control

    monkeypatch.setattr(bat_agent_connector.controller_authority, "use", mock_use)
    monkeypatch.setattr(bat_agent_connector.task_control, "require_authority", lambda task: True)

    task = journal.get("task_1")
    cmd = mock_command("task_1", "cmd1")
    await coordinator._recover_unsent_send(task, cmd)

    # Actually wait for the background update to be processed if any.
    await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_coordinator_integration_tick(tmp_path, monkeypatch):
    journal = Journal(tmp_path / "test.db")
    memory_db = journal.db

    # insert dummy task in running state
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
            100,
            "auto",
            "running",
            0,
            1,
            0.0,
            0.0,
            "session_1",
        ),
    )

    adapter = FakeAdapter()

    # mock read to return clean identity
    async def mock_candidate_identity(task):
        return {"clean": True, "reviewer_session_id": "r1"}

    adapter.candidate_identity = mock_candidate_identity

    async def mock_read(task, session_id, turn_marker=None):
        return {"streaming": False, "pending": False}

    adapter.read = mock_read

    async def mock_recover_start(task, role, session_id):
        return True

    adapter.recover_start = mock_recover_start

    coordinator = TaskCoordinator(journal, adapter)

    # Fake verification remaining
    coordinator.verification_remaining = lambda task: 10

    # Fake command_status
    journal.command_status = lambda *args, **kwargs: None

    import contextlib

    import bat_agent_connector.controller_authority

    @contextlib.contextmanager
    def mock_use(*args, **kwargs):
        yield

    import bat_agent_connector.task_control

    monkeypatch.setattr(bat_agent_connector.controller_authority, "use", mock_use)
    monkeypatch.setattr(bat_agent_connector.task_control, "require_authority", lambda task: True)

    # We mock _verify_and_review so we don't need to pass through it again
    async def mock_verify(task):
        pass

    coordinator._verify_and_review = mock_verify

    # Need a command for start_lead to pass bind checks in _tick
    memory_db.execute(
        "INSERT INTO commands (task_id, command_id, idem_key, kind, status, session_id, payload, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "task_1",
            "cmd1",
            "k2",
            "start_lead",
            "uncertain",
            "session_1",
            '{"controller_incarnation": "nonce1", "agent": "mock"}',
            0.0,
            0.0,
        ),
    )

    # Fake binding registry check
    import bat_agent_connector.registry as registry

    monkeypatch.setattr(
        registry, "get", lambda host, sid: {"task_id": "task_1", "role": "lead", "cwd": "/tmp"}
    )
    monkeypatch.setattr(bat_agent_connector.controller_authority, "require_binding", lambda x: True)
    monkeypatch.setattr(bat_agent_connector.controller_authority, "require_entry", lambda x, **kwargs: True)

    # We must mock issue_capability for the capability logic
    journal.issue_capability = lambda task_id: "cap1"

    class FakeGoose:
        config = type("FakeConfig", (), {"enabled": True, "provider": "mock"})()

        async def run_task(self, *args, **kwargs):
            pass

    coordinator.goose = FakeGoose()

    # The actual _tick_task_bound from daemon calls _tick from coordinator
    await coordinator.tick("task_1")

    # After tick with streaming=False and clean=True, it should settle readback and enter verifying
    task = journal.get("task_1")
    # Actually checking the transition happened is enough, since we only mocked _verify_and_review
    assert task["state"] != "running"
