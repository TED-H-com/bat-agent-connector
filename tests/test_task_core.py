import pytest

from bat_agent_connector.task_core import (
    TaskAdapter,
    TaskCoordinator,
    classify_read,
    final_agent_text,
    initial_prompt,
    review_verdict,
    reviewer_prompt,
)
from bat_agent_connector.task_journal import Journal


class MockTaskAdapter(TaskAdapter):
    async def start(self, task, role, agent, session_id):
        return ""

    async def recover_start(self, task, role, session_id):
        return False

    async def prepare_send(self, task, session_id):
        return {}

    async def session_presence(self, task, session_id):
        return ""

    async def send(self, task, session_id, text, message_id):
        return {}

    async def reconcile_send(self, task, session_id, prompt_sha256, initial_lead, final_lead):
        return {}

    async def read(self, task, session_id, marker):
        return {}

    async def interrupt(self, task, session_id):
        pass

    async def failover(self, task, session_id, successor_id, handoff_command_id, handoff_message_id, summary):
        pass

    async def recover_failover(self, task, successor_id, handoff_command_id):
        pass

    async def candidate_identity(self, task):
        return None

    async def candidate_review_diff(self, task):
        return None

    async def run_verification(self, task):
        return None

    async def available_agents(self, task):
        return frozenset()


@pytest.mark.asyncio
async def test_coordinator_start_lead_agent(tmp_path, monkeypatch):
    journal = Journal(tmp_path / "test.db")
    journal.db.execute(
        "INSERT INTO tasks (task_id, idem_key, engine, recipe, payload_hash, project, host, workspace, original_words, acceptance, state, paused, control_version, submitted_at, updated_at, session_id, lead_agent, review_rejections, session_replacements) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
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
            "queued",
            0,
            1,
            0.0,
            0.0,
            None,
            "claude",
            0,
            0,
        ),
    )

    adapter = MockTaskAdapter()

    class Fleet:
        pass

    adapter.fleet = Fleet()
    coord = TaskCoordinator(journal, adapter)
    import bat_agent_connector.task_control as tc

    monkeypatch.setattr(tc, "require_authority", lambda task: True)
    # mock check_incarnation to not raise
    monkeypatch.setattr(tc, "check_incarnation", lambda journal, task: task)

    async def mock_route(*args, **kwargs):
        pass

    coord._route = mock_route

    async def mock_available(task):
        return frozenset(["claude"])

    coord._available_agents = mock_available

    # Change mock command generation in journal to return fresh = True
    original_command = journal.command

    def mock_command(*args, **kwargs):
        cmd, _ = original_command(*args, **kwargs)
        return cmd, True

    journal.command = mock_command

    async def mock_start_adapter(task, role, agent, session_id):
        return "cmd1"

    adapter.start = mock_start_adapter

    t1 = await coord._start(journal.get("task_1"), role="lead")
    assert t1["state"] == "uncertain"


@pytest.mark.asyncio
async def test_coordinator_start_review_agent(tmp_path, monkeypatch):
    journal = Journal(tmp_path / "test.db")
    journal.db.execute(
        "INSERT INTO tasks (task_id, idem_key, engine, recipe, payload_hash, project, host, workspace, original_words, acceptance, state, paused, control_version, submitted_at, updated_at, session_id, lead_agent, review_rejections, session_replacements, verification_commit) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
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
            "claude",
            0,
            0,
            "c1",
        ),
    )

    adapter = MockTaskAdapter()

    class Fleet:
        pass

    adapter.fleet = Fleet()
    coord = TaskCoordinator(journal, adapter)
    import bat_agent_connector.task_control as tc

    monkeypatch.setattr(tc, "require_authority", lambda task: True)
    # mock check_incarnation to not raise
    monkeypatch.setattr(tc, "check_incarnation", lambda journal, task: task)

    async def mock_route(*args, **kwargs):
        pass

    coord._route = mock_route

    async def mock_available(task):
        return frozenset(["claude"])

    coord._available_agents = mock_available

    # Change mock command generation in journal to return fresh = True
    original_command = journal.command

    def mock_command(*args, **kwargs):
        cmd, _ = original_command(*args, **kwargs)
        return cmd, True

    journal.command = mock_command

    async def mock_start_adapter(task, role, agent, session_id):
        return "cmd1"

    adapter.start = mock_start_adapter

    t1 = await coord._start(journal.get("task_1"), role="reviewer")
    assert t1["state"] == "uncertain"


@pytest.mark.asyncio
async def test_coordinator_start_paused(tmp_path, monkeypatch):
    journal = Journal(tmp_path / "test.db")
    journal.db.execute(
        "INSERT INTO tasks (task_id, idem_key, engine, recipe, payload_hash, project, host, workspace, original_words, acceptance, state, paused, control_version, submitted_at, updated_at, session_id, lead_agent, review_rejections, session_replacements) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
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
            "queued",
            1,
            1,
            0.0,
            0.0,
            None,
            "claude",
            0,
            0,
        ),
    )

    adapter = MockTaskAdapter()

    class Fleet:
        pass

    adapter.fleet = Fleet()
    coord = TaskCoordinator(journal, adapter)
    import bat_agent_connector.task_control as tc

    monkeypatch.setattr(tc, "require_authority", lambda task: True)
    monkeypatch.setattr(tc, "check_incarnation", lambda journal, task: task)

    async def mock_route(*args, **kwargs):
        pass

    coord._route = mock_route

    async def mock_available(task):
        return frozenset(["claude"])

    coord._available_agents = mock_available

    # Change mock command generation in journal to return fresh = True
    original_command = journal.command

    def mock_command(*args, **kwargs):
        cmd, _ = original_command(*args, **kwargs)
        return cmd, True

    journal.command = mock_command

    await coord._start(journal.get("task_1"), role="lead")
    assert journal.get("task_1")["paused"] == 1


@pytest.mark.asyncio
async def test_coordinator_start_fresh_dispatching(tmp_path, monkeypatch):
    journal = Journal(tmp_path / "test.db")
    journal.db.execute(
        "INSERT INTO tasks (task_id, idem_key, engine, recipe, payload_hash, project, host, workspace, original_words, acceptance, state, paused, control_version, submitted_at, updated_at, session_id, lead_agent, review_rejections, session_replacements) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
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
            "queued",
            0,
            1,
            0.0,
            0.0,
            None,
            "claude",
            0,
            0,
        ),
    )

    adapter = MockTaskAdapter()

    class Fleet:
        pass

    adapter.fleet = Fleet()
    coord = TaskCoordinator(journal, adapter)
    import bat_agent_connector.task_control as tc

    monkeypatch.setattr(tc, "require_authority", lambda task: True)
    monkeypatch.setattr(tc, "check_incarnation", lambda journal, task: task)

    async def mock_route(*args, **kwargs):
        pass

    coord._route = mock_route

    async def mock_available(task):
        return frozenset(["claude"])

    coord._available_agents = mock_available

    async def mock_start_adapter(task, role, agent, session_id):
        return "cmd1"

    adapter.start = mock_start_adapter

    # Change mock command generation in journal to return fresh = True
    original_command = journal.command

    def mock_command(*args, **kwargs):
        cmd, _ = original_command(*args, **kwargs)
        return cmd, True

    journal.command = mock_command

    t1 = await coord._start(journal.get("task_1"), role="lead")
    assert t1["state"] in ["dispatching", "uncertain"]


@pytest.mark.asyncio
async def test_coordinator_route(tmp_path):
    journal = Journal(tmp_path / "test.db")
    coord = TaskCoordinator(journal, MockTaskAdapter())
    assert await coord._route({"task_id": "t1"}, "step", "type") is None


@pytest.mark.asyncio
async def test_session_control(tmp_path, monkeypatch):
    journal = Journal(tmp_path / "test.db")
    journal.db.execute(
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

    adapter = MockTaskAdapter()

    class Fleet:
        pass

    adapter.fleet = Fleet()
    coord = TaskCoordinator(journal, adapter)

    import bat_agent_connector.task_control as tc

    monkeypatch.setattr(tc, "check", lambda *args, **kwargs: journal.get("task_1"))

    async def dummy_fn(*args, **kwargs):
        return {"accepted": True, "turn_marker": "m1"}

    result = await coord.session_control("task_1", "host1", "session_1", "send", {}, dummy_fn)
    assert result["turn_marker"] == "m1"

    # Test answer failure due to missing permission/answers
    with pytest.raises(Exception, match="pass exactly one of answers"):
        await coord.session_control("task_1", "host1", "session_1", "answer", {}, dummy_fn)

    # Test rejected status on send
    async def dummy_reject_fn(*args, **kwargs):
        return {"accepted": False}

    result2 = await coord.session_control("task_1", "host1", "session_1", "send", {}, dummy_reject_fn)
    assert not result2.get("accepted")


@pytest.mark.asyncio
async def test_session_control_exceptions(tmp_path, monkeypatch):
    journal = Journal(tmp_path / "test.db")
    journal.db.execute(
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

    adapter = MockTaskAdapter()

    class Fleet:
        pass

    adapter.fleet = Fleet()
    coord = TaskCoordinator(journal, adapter)

    import bat_agent_connector.task_control as tc

    monkeypatch.setattr(tc, "check", lambda *args, **kwargs: journal.get("task_1"))

    async def fail_fn(*args, **kwargs):
        from bat_agent_connector.errors import WriteRefused

        raise WriteRefused("CODE", "MESSAGE")

    with pytest.raises(Exception):
        await coord.session_control("task_1", "host1", "session_1", "send", {}, fail_fn)

    # Check that status was marked as uncertain due to guard.frames being empty? Actually guard.frames isn't empty in test setup

    async def fail_fn_other(*args, **kwargs):
        raise ValueError("boom")

    with pytest.raises(Exception):
        await coord.session_control("task_1", "host1", "session_1", "send", {}, fail_fn_other)


@pytest.mark.asyncio
async def test_session_control_send_codex(tmp_path, monkeypatch):
    journal = Journal(tmp_path / "test.db")
    journal.db.execute(
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

    adapter = MockTaskAdapter()

    class Fleet:
        pass

    adapter.fleet = Fleet()

    async def prepare_send(task, sid):
        return {"agent_kind": "codex"}

    adapter.prepare_send = prepare_send

    coord = TaskCoordinator(journal, adapter)

    import bat_agent_connector.task_control as tc

    monkeypatch.setattr(tc, "check", lambda *args, **kwargs: journal.get("task_1"))

    async def mock_reconcile(*args, **kwargs):
        pass

    coord._reconcile_command = mock_reconcile

    async def dummy_fn(*args, **kwargs):
        return {"accepted": True, "turn_marker": "m1"}

    result = await coord.session_control("task_1", "host1", "session_1", "send", {}, dummy_fn)
    assert result["turn_marker"] == "m1"


def test_initial_prompt():
    task = {"original_words": "my specific test string"}
    prompt = initial_prompt(task)
    assert "my specific test string" in prompt


def test_reviewer_prompt():
    task = {"original_words": "another specific test string"}
    prompt = reviewer_prompt(task, "c1abc", "t1abc")
    assert "another specific test string" in prompt
    assert "c1abc" in prompt
    assert "t1abc" in prompt


def test_final_agent_text():
    read1 = {
        "messages": [
            {"role": "user", "text": "a"},
            {"role": "assistant", "text": "b"},
            {"role": "user", "text": "c"},
        ]
    }
    assert final_agent_text(read1) == "b"

    read2 = {"messages": [{"role": "user", "text": "a"}, {"role": "tool", "text": "b"}]}
    assert final_agent_text(read2) is None
    assert final_agent_text({}) is None


def test_review_verdict():
    assert review_verdict({}, "c1", "t1")["reason"] == "verdict_missing"
    assert (
        review_verdict({"messages": [{"role": "assistant", "text": ""}]}, "c1", "t1")["reason"]
        == "verdict_missing"
    )
    assert (
        review_verdict({"messages": [{"role": "assistant", "text": "no json here"}]}, "c1", "t1")["reason"]
        == "verdict_missing"
    )
    assert (
        review_verdict(
            {"messages": [{"role": "assistant", "text": '{"verdict": "pass", "findings": "not a list"}'}]},
            "c1",
            "t1",
        )["reason"]
        == "verdict_invalid"
    )
    assert (
        review_verdict(
            {"messages": [{"role": "assistant", "text": '{"verdict": "other", "findings": []}'}]}, "c1", "t1"
        )["reason"]
        == "verdict_invalid"
    )
    assert (
        review_verdict(
            {
                "messages": [
                    {
                        "role": "assistant",
                        "text": '{"verdict": "pass", "findings": []} {"verdict": "reject", "findings": []}',
                    }
                ]
            },
            "c1",
            "t1",
        )["reason"]
        == "verdict_conflicting"
    )
    assert (
        review_verdict(
            {
                "messages": [
                    {"role": "assistant", "text": '{"verdict": "pass", "findings": []} REVIEW: REJECT'}
                ]
            },
            "c1",
            "t1",
        )["reason"]
        == "verdict_conflicting"
    )
    assert (
        review_verdict(
            {
                "messages": [
                    {
                        "role": "assistant",
                        "text": '{"verdict": "pass", "findings": [], "candidate_commit": "c2", "tree_hash": "t1"}',
                    }
                ]
            },
            "c1",
            "t1",
        )["reason"]
        == "verdict_candidate_mismatch"
    )
    assert (
        review_verdict(
            {
                "messages": [
                    {
                        "role": "assistant",
                        "text": '{"verdict": "reject", "findings": [], "candidate_commit": "c1", "tree_hash": "t1"}',
                    }
                ]
            },
            "c1",
            "t1",
        )["reason"]
        == "reviewer_reject"
    )
    assert (
        review_verdict(
            {
                "messages": [
                    {
                        "role": "assistant",
                        "text": '{"verdict": "pass", "findings": [{"severity": "HIGH"}], "candidate_commit": "c1", "tree_hash": "t1"}',
                    }
                ]
            },
            "c1",
            "t1",
        )["reason"]
        == "pass_with_high_findings"
    )

    res = review_verdict(
        {
            "messages": [
                {
                    "role": "assistant",
                    "text": '{"verdict": "pass", "findings": [], "candidate_commit": "c1", "tree_hash": "t1"}',
                }
            ]
        },
        "c1",
        "t1",
    )
    assert res["verdict"] == "pass"


def test_classify_read():
    assert classify_read({"pending": {"kind": "permission"}}) == "waiting_permission"
    assert classify_read({"pending": {"kind": "other"}}) == "needs_ted"
    assert classify_read({"turn_done": False}) == "running"
    assert (
        classify_read({"turn_done": True, "messages": [{"role": "assistant", "text": "quota exhausted"}]})
        == "quota_limited"
    )
    assert (
        classify_read(
            {"turn_done": True, "messages": [{"role": "assistant", "text": "BAT-STATUS: NEED_HUMAN"}]}
        )
        == "needs_ted"
    )
    assert (
        classify_read(
            {"turn_done": True, "messages": [{"role": "assistant", "text": "BAT-STATUS: MILESTONE"}]}
        )
        == "verifying"
    )
    read = {"turn_done": True, "messages": [{"role": "assistant", "text": "BAT-STATUS: MILESTONE"}]}
    assert classify_read(read) == "verifying"
    assert (
        classify_read({"turn_done": True, "messages": [{"role": "assistant", "text": "continue"}]})
        == "continue"
    )


def test_review_verdict_clipped_and_value_error():
    assert (
        review_verdict({"messages": [{"role": "assistant", "text": "foo \u2026[+12 chars]"}]}, "c1", "t1")[
            "reason"
        ]
        == "final_message_truncated"
    )
    assert (
        review_verdict({"messages": [{"role": "assistant", "text": "{"}]}, "c1", "t1")["reason"]
        == "verdict_missing"
    )


@pytest.mark.asyncio
async def test_available_agents_exception_with_tmp_path(tmp_path):
    class ExceptionAdapter(MockTaskAdapter):
        async def available_agents(self, task):
            raise Exception("unknown availability")

    coordinator = TaskCoordinator(Journal(tmp_path / "test.db"), ExceptionAdapter())
    agents = await coordinator._available_agents({})
    assert agents == frozenset({"codex"})


@pytest.mark.asyncio
async def test_available_agents_task_control_refused(tmp_path):
    from bat_agent_connector.task_core import TaskControlRefused

    class ExceptionAdapter(MockTaskAdapter):
        async def available_agents(self, task):
            raise TaskControlRefused("CODE", "MESSAGE")

    coordinator = TaskCoordinator(Journal(tmp_path / "test2.db"), ExceptionAdapter())
    with pytest.raises(TaskControlRefused):
        await coordinator._available_agents({})


def test_coordinator_init(tmp_path):
    journal = Journal(tmp_path / "test.db")
    adapter = MockTaskAdapter()
    coord = TaskCoordinator(
        journal, adapter, max_continuations=10, max_review_rejections=5, verification_quiet_s=-1.0
    )
    assert coord.verification_quiet_s == 0.0
    assert getattr(adapter, "fleet", None) is None

    class AdapterWithFleet(MockTaskAdapter):
        def __init__(self):
            class Fleet:
                task_coordinator = None

            self.fleet = Fleet()

    adapter2 = AdapterWithFleet()
    coord2 = TaskCoordinator(journal, adapter2)
    assert adapter2.fleet.task_coordinator is coord2


def test_coordinator_cleanup_verdict_and_authority(tmp_path, monkeypatch):
    journal = Journal(tmp_path / "test.db")
    coord = TaskCoordinator(journal, MockTaskAdapter())
    import bat_agent_connector.task_cleanup as cleanup

    original_verdict = cleanup.verdict
    original_authority = cleanup.authority

    try:
        cleanup.verdict = lambda c, i: "v_" + i
        cleanup.authority = lambda c, ctx, i: "a_" + i
        assert coord.cleanup_verdict("item1") == "v_item1"
        assert coord.cleanup_authority("ctx1", "item2") == "a_item2"
        assert coord._lock("h1", "s1") is coord._writers[("h1", "s1")]
    finally:
        cleanup.verdict = original_verdict
        cleanup.authority = original_authority


def test_coordinator_pick_agent():
    assert TaskCoordinator._pick_agent("lead", {"lead_agent": "claude"}, frozenset(["claude"])) == (
        "claude",
        "lead_agent",
    )
    assert TaskCoordinator._pick_agent("lead", {"lead_agent": "gpt"}, frozenset(["claude", "codex"])) == (
        "codex",
        "lead_fallback_unavailable",
    )
    assert TaskCoordinator._pick_agent(
        "review", {"lead_agent": "claude"}, frozenset(["claude", "codex"])
    ) == ("codex", "review_other_family")
    assert TaskCoordinator._pick_agent("review", {"lead_agent": "claude"}, frozenset(["claude"])) == (
        "claude",
        "review_same_family_only",
    )


@pytest.mark.asyncio
async def test_coordinator_pause(tmp_path, monkeypatch):
    journal = Journal(tmp_path / "test.db")
    journal.db.execute(
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

    adapter = MockTaskAdapter()
    coord = TaskCoordinator(journal, adapter)
    import bat_agent_connector.task_control as tc

    monkeypatch.setattr(tc, "require_authority", lambda task: True)

    t1 = await coord.pause("task_1")
    assert t1["paused"] == 1

    adapter.interrupted = False

    async def mock_interrupt(task, session_id):
        adapter.interrupted = True

    adapter.interrupt = mock_interrupt

    t2 = await coord.pause("task_1", abort_current=True)
    assert t2["paused"] == 1
    assert adapter.interrupted


@pytest.mark.asyncio
async def test_coordinator_tick_bound_exceptions(tmp_path, monkeypatch):
    from bat_agent_connector.task_core import TaskControlRefused

    journal = Journal(tmp_path / "test.db")
    journal.db.execute(
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
    coord = TaskCoordinator(journal, MockTaskAdapter())

    async def mock_tick_refused(task_id):
        raise TaskControlRefused("CODE", "MESSAGE")

    coord._tick = mock_tick_refused

    t1 = await coord._tick_bound("task_1")
    assert t1["task_id"] == "task_1"

    from bat_agent_connector.errors import ResourceReadOnly

    async def mock_tick_readonly(task_id):
        raise ResourceReadOnly("CODE", "MESSAGE")

    coord._tick = mock_tick_readonly

    t2 = await coord._tick_bound("task_1")
    assert t2["task_id"] == "task_1"


@pytest.mark.asyncio
async def test_coordinator_tick_paused_or_done(tmp_path, monkeypatch):
    journal = Journal(tmp_path / "test.db")
    journal.db.execute(
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
            "done",
            0,
            1,
            0.0,
            0.0,
            "session_1",
        ),
    )
    coord = TaskCoordinator(journal, MockTaskAdapter())
    t1 = await coord._tick("task_1")
    assert t1["state"] == "done"

    journal.db.execute(
        "INSERT INTO tasks (task_id, idem_key, engine, recipe, payload_hash, project, host, workspace, original_words, acceptance, state, paused, control_version, submitted_at, updated_at, session_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "task_2",
            "k2",
            "goose",
            "recipe1",
            "hash2",
            "project1",
            "host1",
            "workspace1",
            100,
            "auto",
            "running",
            1,
            1,
            0.0,
            0.0,
            "session_1",
        ),
    )
    t2 = await coord._tick("task_2")
    assert t2["paused"] == 1


def test_coordinator_check_control(tmp_path, monkeypatch):
    journal = Journal(tmp_path / "test.db")
    journal.db.execute(
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
    coord = TaskCoordinator(journal, MockTaskAdapter())
    t1 = journal.get("task_1")
    assert coord._check_control(t1)["control_version"] == 1


@pytest.mark.asyncio
async def test_coordinator_tick_queued(tmp_path, monkeypatch):
    journal = Journal(tmp_path / "test.db")
    journal.db.execute(
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
            "queued",
            0,
            1,
            0.0,
            0.0,
            None,
        ),
    )
    coord = TaskCoordinator(journal, MockTaskAdapter())
    import bat_agent_connector.task_control as tc

    monkeypatch.setattr(tc, "require_authority", lambda task: True)

    async def mock_start(task, role):
        return {"state": "accepted"}

    coord._start = mock_start
    t1 = await coord._tick("task_1")
    assert t1["state"] == "accepted"


@pytest.mark.asyncio
async def test_coordinator_tick_quota_limited(tmp_path, monkeypatch):
    journal = Journal(tmp_path / "test.db")
    journal.db.execute(
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
            "quota_limited",
            0,
            1,
            0.0,
            0.0,
            "session_1",
        ),
    )
    coord = TaskCoordinator(journal, MockTaskAdapter())
    import bat_agent_connector.task_control as tc

    monkeypatch.setattr(tc, "require_authority", lambda task: True)

    t1 = await coord._tick("task_1")
    assert t1["state"] == "needs_ted"
    assert "quota exhausted" in t1["result"]


@pytest.mark.asyncio
async def test_coordinator_tick_pending_command(tmp_path, monkeypatch):
    journal = Journal(tmp_path / "test.db")
    journal.db.execute(
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
    journal.db.execute(
        "INSERT INTO commands (task_id, command_id, idem_key, kind, status, session_id, payload, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ("task_1", "cmd1", "k2", "send", "intent", "session_1", '{"operation_id": "op1"}', 0.0, 0.0),
    )
    coord = TaskCoordinator(journal, MockTaskAdapter())
    import bat_agent_connector.task_control as tc

    monkeypatch.setattr(tc, "require_authority", lambda task: True)

    async def mock_reconcile(task, cmd):
        return {"reconciled": True}

    coord._reconcile_command = mock_reconcile

    t1 = await coord._tick("task_1")
    assert t1["reconciled"]


@pytest.mark.asyncio
async def test_coordinator_tick_uncertain_state(tmp_path, monkeypatch):
    journal = Journal(tmp_path / "test.db")
    journal.db.execute(
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
            "uncertain",
            0,
            1,
            0.0,
            0.0,
            "session_1",
        ),
    )
    adapter = MockTaskAdapter()

    async def mock_presence(task, sid):
        return "present"

    adapter.session_presence = mock_presence

    coord = TaskCoordinator(journal, adapter)
    import bat_agent_connector.task_control as tc

    monkeypatch.setattr(tc, "require_authority", lambda task: True)

    t1 = await coord._tick("task_1")
    assert t1["state"] == "accepted"
