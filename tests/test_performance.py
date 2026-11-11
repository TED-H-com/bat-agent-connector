import time

import pytest

from bat_agent_connector.task_core import TaskAdapter, TaskCoordinator
from bat_agent_connector.task_journal import Journal


class MockTaskAdapter(TaskAdapter):
    async def start(self, task, *, role, agent, session_id): return session_id
    async def recover_start(self, task, *, role, session_id): return True
    async def prepare_send(self, task, session_id): return {}
    async def session_presence(self, task, session_id): return "alive"
    async def send(self, task, session_id, text, message_id): return {}
    async def reconcile_send(self, task, session_id, prompt_sha256, before, message_id): return None
    async def read(self, task, session_id, marker): return {}
    async def interrupt(self, task, session_id): return None
    async def failover(self, task, session_id, successor_id, *, handoff_message_id, handoff_command_id): return {}
    async def recover_failover(self, task, *, successor_id, handoff_message_id, handoff_command_id): return None
    async def candidate_identity(self, task): return None
    async def candidate_review_diff(self, task): return None
    async def run_verification(self, task): return None
    async def available_agents(self, task): return frozenset(["codex", "grok"])

@pytest.mark.asyncio
async def test_coordinator_lock_performance(tmp_path):
    journal = Journal(tmp_path / "test.db")
    coord = TaskCoordinator(journal, MockTaskAdapter())

    start_time = time.perf_counter()
    num_iterations = 50000

    for _i in range(num_iterations):
        _l = coord._task_lock("t1")

    duration = time.perf_counter() - start_time

    # We expect dictionary access and creation to be very fast.
    assert duration < 0.5, f"Lock acquisition took too long: {duration:.4f}s"
    assert len(coord._task_locks) == 1

@pytest.mark.asyncio
async def test_coordinator_tick_performance(tmp_path):
    journal = Journal(tmp_path / "test.db")
    coord = TaskCoordinator(journal, MockTaskAdapter())

    # Setup some dummy tasks in the journal
    num_tasks = 1000
    task_ids = []
    for i in range(num_tasks):
        with journal.tx():
            t = journal.submit(project="project", host="host", workspace="workspace", original_words="hello", idempotency_key=f"k_{i}")
            task_ids.append(t["task_id"])

    start_time = time.perf_counter()

    for task_id in task_ids:
        await coord.tick(task_id)

    duration = time.perf_counter() - start_time
    # DB access for 1000 items sequentially shouldn't take more than a second or two in SQLite
    # 0.5s is usually a safe baseline for fast memory-backed or fast SSD backed tmp
    # but we'll set it to 1.5s to avoid flakes in CI
    assert duration < 1.5, f"Tick performance took too long: {duration:.4f}s"
