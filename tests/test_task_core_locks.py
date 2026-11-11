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


def test_coordinator_locks_reuse(tmp_path):
    journal = Journal(tmp_path / "test.db")
    coord = TaskCoordinator(journal, MockTaskAdapter())

    # Task lock reuse
    lock1 = coord._task_lock("t1")
    lock2 = coord._task_lock("t1")
    assert lock1 is lock2
    assert len(coord._task_locks) == 1

    # Task lock distinct keys
    lock3 = coord._task_lock("t2")
    assert lock1 is not lock3
    assert len(coord._task_locks) == 2

    # Session lock reuse
    slock1 = coord._lock("host1", "sid1")
    slock2 = coord._lock("host1", "sid1")
    assert slock1 is slock2
    assert len(coord._writers) == 1

    # Session lock distinct keys
    slock3 = coord._lock("host1", "sid2")
    slock4 = coord._lock("host2", "sid1")
    assert slock1 is not slock3
    assert slock1 is not slock4
    assert slock3 is not slock4
    assert len(coord._writers) == 3
