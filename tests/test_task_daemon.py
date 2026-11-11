import asyncio

import pytest

from bat_agent_connector.config import parse_config
from bat_agent_connector.task_daemon import TaskDaemon


@pytest.fixture
def mock_daemon(tmp_path):
    config_data = {"hosts": {"test": {"url": "wss://test", "fingerprint": "a" * 64, "token_ref": "ref"}}}
    config = parse_config(config_data)
    daemon = TaskDaemon(config, db_path=tmp_path / "test.db")

    class FakeJournal:
        def __init__(self):
            self.task = {"task_id": "test_task", "engine": "goose", "session_id": "sid_1", "host": "h1"}

        def get(self, task_id):
            return self.task

    daemon.journal = FakeJournal()

    class FakeAdapter:
        async def read(self, task, session_id, marker):
            return {"method": "task_read"}

    daemon.adapter = FakeAdapter()
    return daemon


@pytest.mark.asyncio
async def test_task_daemon_call_bound(mock_daemon):
    res = await mock_daemon._call_bound("task_read", {"task_id": "test_task", "marker": None})
    assert res["method"] == "task_read"

    try:
        await mock_daemon._call_bound("unrecognized", {"task_id": "test_task"})
        assert False, "Should raise ValueError for unknown method"
    except ValueError as e:
        assert "unknown task method" in str(e)


@pytest.mark.asyncio
async def test_task_daemon_worker(mock_daemon, monkeypatch):

    # We want to test _worker but it's an infinite loop
    # We will raise StopLoop inside asyncio.sleep
    class StopLoop(Exception):
        pass

    async def mock_sleep(*args, **kwargs):
        raise StopLoop()

    monkeypatch.setattr(asyncio, "sleep", mock_sleep)

    # Needs a realish journal object
    class FakeDB:
        def execute(self, *args, **kwargs):
            pass

    class FakeJournal:
        def __init__(self):
            self.task_ids = ["task_1", "task_2"]
            self.db = FakeDB()

        def queue(self):
            return self.task_ids

        def processable(self):
            return self.task_ids

        def list_active(self):
            return [{"task_id": "task_1"}, {"task_id": "task_2"}]

        def list_cleanup_pending(self):
            return []

        # Add methods needed by owner_valid
        def owner_valid(self):
            return True

        def get(self, tid):
            return {"task_id": tid, "state": "queued"}

    mock_daemon.journal = FakeJournal()

    # mock _tick_task
    tick_calls = []

    async def mock_tick(tid):
        tick_calls.append(tid)

    mock_daemon._tick_task = mock_tick

    # mock ops processing
    mock_daemon.ops = type(
        "FakeOps",
        (),
        {
            "retry_stalled": lambda *args: None,
            "dispatch": lambda *args: None,
            "journal": type("OpsJournal", (), {"owner_valid": lambda *args: True})(),
        },
    )()

    # We expect _worker to catch StopLoop
    try:
        await mock_daemon._worker()
    except StopLoop:
        pass

    # Restore sleep to allow task switching
    if hasattr(asyncio, "sleep"):
        monkeypatch.undo()

    # Wait for the mocked tasks to complete
    await asyncio.sleep(0)

    # Check that it called _tick_task for both tasks
    assert len(tick_calls) == 2
    assert set(tick_calls) == {"task_1", "task_2"}
