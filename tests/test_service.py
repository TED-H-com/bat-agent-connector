"""Read tools + write tools against the mock server."""

from __future__ import annotations

import asyncio
import json

import pytest

from bat_agent_connector import service
from bat_agent_connector.config import SafetyConfig
from bat_agent_connector.errors import WriteRefused
from bat_agent_connector.safety import Audit, audit_path
from tests.conftest import adopt, controller_scope
from tests.mockbat import TOKEN

MANAGED = {"writes": True, "managed_roots": ["/srv"]}


def adopt_all():
    """Mark the mock agent sessions as connector-created in a managed root (write-mechanics tests)."""
    adopt("sess-claude-0001")
    adopt("sess-codex-0002")
    adopt("sess-unload-0003", cwd="/srv/other")


def test_initial_task_send_still_obeys_hourly_cap(tmp_path):
    audit = Audit(SafetyConfig(write_min_interval_s=60, max_writes_per_hour=1),
                  tmp_path / "audit.jsonl")
    audit.record(host="h1", session_id="task-session", channel="claude:start-session",
                 phase="attempt")
    with pytest.raises(WriteRefused, match="max_writes_per_hour"):
        audit.check_rate("h1", "task-session", initial_task_send=True)


async def test_hosts_list_and_status(fleet_factory):
    f = fleet_factory()
    r = await service.hosts_list(f)
    assert r["hosts"][0]["reachable"] and r["hosts"][0]["server_version"] == "9.9.9"
    assert TOKEN not in json.dumps(r)
    s = await service.host_status(f, "h1")
    assert s["workspaces"] == 2 and s["agent_sessions"] == 3 and s["loaded"] == 2 and s["streaming"] == 1
    await f.close()


async def test_workspaces_and_sessions(fleet_factory, mock):
    f = fleet_factory()
    w = await service.workspaces_list(f)
    assert {x["name"] for x in w["workspaces"]} == {"demo-project", "other"}
    s = await service.sessions_list(f)
    ids = [x["session_id"] for x in s["sessions"]]
    assert "shell-0004" not in ids and len(ids) == 3
    first = s["sessions"][0]
    assert first["session_id"] == "sess-claude-0001" and first["last_activity_source"] == "transcript"
    unl = next(x for x in s["sessions"] if x["session_id"] == "sess-unload-0003")
    assert unl["loaded"] is False and unl["last_activity_source"] == "archive"
    # unloaded Claude session: never probed with get-session-state
    states = [i for i in mock.invokes if i["channel"] == "claude:get-session-state"]
    assert all(i["params"]["sessionId"] != "sess-unload-0003" for i in states)
    s2 = await service.sessions_list(f, workspace="other")
    assert [x["session_id"] for x in s2["sessions"]] == ["sess-unload-0003"]
    await f.close()


async def test_pending_question_detected(fleet_factory, mock):
    mock.states["sess-codex-0002"]["pendingAskUser"] = {
        "toolUseId": "tu1",
        "questions": [{"question": "Which DB?", "options": [{"label": "pg"}, {"label": "sqlite"}]}],
    }
    f = fleet_factory()
    s = await service.sessions_list(f, agent="codex")
    p = s["sessions"][0]["pending"]
    assert p["kind"] == "ask_user" and p["questions"][0]["options"] == ["pg", "sqlite"]
    await f.close()


async def test_session_read_paging(fleet_factory):
    f = fleet_factory()
    r = await service.session_read(f, "h1", "sess-claude", last_n=5)  # prefix
    assert [m["text"] for m in r["messages"]] == [f"live message {i}" for i in range(5, 10)]
    assert r["next_offset"] == 5
    r2 = await service.session_read(f, "h1", "sess-claude-0001", last_n=10, offset=5)
    texts = [m["text"] for m in r2["messages"]]
    assert texts[-5:] == [f"live message {i}" for i in range(5)]
    assert texts[:5] == [f"archived {i}" for i in range(45, 50)]
    r3 = await service.session_read(f, "h1", "sess-claude-0001", last_n=3, include_tools=True)
    assert r3["messages"][-1]["role"] == "tool"
    r4 = await service.session_read(f, "h1", "sess-claude-0001", last_n=50, max_chars=500)
    assert r4["truncated_by_size"] and r4["messages"][-1]["text"] == "live message 9"
    await f.close()


async def test_session_wait_event_and_idle(fleet_factory, mock):
    f = fleet_factory()
    r = await service.session_wait(f, "h1", "sess-claude-0001", timeout_s=2)
    assert r["status"] == "idle"

    async def fire():
        await asyncio.sleep(0.3)
        await mock.broadcast("agent:stream", {"sessionId": "sess-codex-0002", "data": {"text": "x"}})
        await mock.broadcast("agent:turn-end", {"sessionId": "other"})
        await mock.broadcast("agent:turn-end", {"sessionId": "sess-codex-0002"})

    t = asyncio.create_task(fire())
    r = await service.session_wait(f, "h1", "sess-codex-0002", timeout_s=5)
    await t
    assert r["status"] == "event" and r["event"] == "agent:turn-end"
    r = await service.session_wait(f, "h1", "sess-codex-0002", timeout_s=1)
    assert r["status"] == "timeout"
    await f.close()


# ----------------------------------------------------------------------------- writes
async def test_write_refused_when_disabled_or_unconfirmed(fleet_factory, mock):
    f = fleet_factory()
    with pytest.raises(WriteRefused):
        await service.session_send(f, "h1", "sess-claude-0001", "hi", confirm=True)
    f2 = fleet_factory(writes=True)
    with pytest.raises(WriteRefused):
        await service.session_send(f2, "h1", "sess-claude-0001", "hi", confirm=False)
    f3 = fleet_factory(writes=True, read_only=True)
    with pytest.raises(WriteRefused):
        await service.session_send(f3, "h1", "sess-claude-0001", "hi", confirm=True)
    assert "claude:send-message" not in mock.channels()


async def test_direct_send_is_blocked_for_verifying_task_session(fleet_factory, mock, monkeypatch, tmp_path):
    f = fleet_factory(**MANAGED)
    from bat_agent_connector import registry
    from bat_agent_connector.task_journal import Journal

    journal = Journal(tmp_path / "tasks.db")
    with controller_scope(journal):
        adopt_all()
        task = journal.submit(project="p", host="h1", workspace="w", original_words="do it", idempotency_key="k")
        journal.db.execute("UPDATE tasks SET state='verifying',session_id=? WHERE task_id=?",
                           ("sess-claude-0001", task["task_id"]))
        registry.update("h1", "sess-claude-0001", task_id=task["task_id"], role="lead")
        monkeypatch.setattr(service, "task_service_db", lambda: journal.path)
        with pytest.raises(WriteRefused, match="TASK_VERIFYING"):
            await service.session_send(f, "h1", "sess-claude-0001", "outside task service", confirm=True)
        journal.close()
        assert "claude:send-message" not in mock.channels()
        await f.close()


async def test_task_send_fence_uses_daemon_db_and_fails_closed(tmp_path, fleet_factory, mock):
    """A07: the actual legacy path fails closed when the canonical owner cannot be read."""
    from bat_agent_connector import registry
    from bat_agent_connector.errors import TaskControlRefused
    from bat_agent_connector.task_journal import Journal

    journal = Journal(tmp_path / "elsewhere" / "custom.db")
    with controller_scope(journal):
        sid = "sess-claude-0001"
        adopt(sid, task_id="missing-task", role="lead")
        fleet = fleet_factory(**MANAGED)
        pointer = registry.registry_path().parent / service.TASK_SERVICE_POINTER
        for pointer_body in (None, {"db_path": str(tmp_path / "missing.db")}):
            if pointer_body:
                pointer.write_text(json.dumps(pointer_body))
            with pytest.raises(TaskControlRefused, match="TASK_OWNER_UNAVAILABLE"):
                await service.session_send(fleet, "h1", sid, "outside", confirm=True)
        pointer.write_text(json.dumps({"db_path": str(journal.path)}))
        with pytest.raises(TaskControlRefused, match="TASK_OWNER_UNAVAILABLE"):
            await service.session_send(fleet, "h1", sid, "outside", confirm=True)
        task = journal.submit(project="p", host="h1", workspace="w", original_words="do it", idempotency_key="k")
        journal.db.execute("UPDATE tasks SET state='verifying',session_id=? WHERE task_id=?", (sid, task["task_id"]))
        registry.update("h1", sid, task_id=task["task_id"])
        with pytest.raises(TaskControlRefused, match="TASK_VERIFYING"):
            await service.session_send(fleet, "h1", sid, "outside", confirm=True)
        assert "claude:send-message" not in mock.channels()
        journal.close()
        await fleet.close()


async def test_send_resume_idempotent_rate_limit_audit(fleet_factory, mock):
    adopt_all()
    f = fleet_factory(**MANAGED)
    body = "please continue with step 3 SECRET-BODY"
    r = await service.session_send(f, "h1", "sess-unload-0003", body, confirm=True, message_id="mid-1")
    assert r["resumed"] and r["accepted"] and r["message_id"] == "mid-1"
    chans = mock.channels()
    assert chans.index("claude:client-resume") < chans.index("claude:send-message")
    resume = next(i for i in mock.invokes if i["channel"] == "claude:client-resume")
    assert resume["params"]["options"]["cwd"] == "/srv/other"
    with pytest.raises(WriteRefused, match="rate limit"):
        await service.session_send(f, "h1", "sess-unload-0003", "again", confirm=True)
    log = audit_path().read_text()
    assert "SECRET-BODY" not in log and "text_sha256_16" in log and TOKEN not in log
    # streaming session refused without queue
    with pytest.raises(WriteRefused, match="streaming"):
        await service.session_send(f, "h1", "sess-codex-0002", "x", confirm=True)
    r = await service.session_send(f, "h1", "sess-codex-0002", "x", confirm=True, queue=True)
    assert r["queued"] is True
    await f.close()


async def test_interrupt_modes(fleet_factory, mock):
    adopt_all()
    f = fleet_factory(**MANAGED)
    r = await service.session_interrupt(f, "h1", "sess-claude-0001", "soft", confirm=True)
    assert r["channel"] == "claude:interrupt-turn"
    r = await service.session_interrupt(f, "h1", "sess-codex-0002", "soft", confirm=True)
    assert r["channel"] == "claude:abort-session" and r["note"]
    await f.close()


async def test_answer_ask_user_and_permission(fleet_factory, mock):
    adopt_all()
    f = fleet_factory(**MANAGED)
    mock.states["sess-claude-0001"]["pendingAskUser"] = {
        "toolUseId": "tu9",
        "questions": [{"question": "Q1?"}],
    }
    r = await service.session_answer(f, "h1", "sess-claude-0001", confirm=True, answers=["yes"])
    inv = [i for i in mock.invokes if i["channel"] == "claude:resolve-ask-user"][-1]
    assert inv["params"] == {"sessionId": "sess-claude-0001", "toolUseId": "tu9", "answers": {"Q1?": "yes"}}
    mock.states["sess-codex-0002"]["pendingPermission"] = {
        "toolUseId": "tp1",
        "toolName": "Bash",
        "input": {"command": "ls"},
    }
    r = await service.session_answer(f, "h1", "sess-codex-0002", confirm=True, permission="allow")
    assert r["channel"] == "claude:resolve-permission"
    inv = [i for i in mock.invokes if i["channel"] == "claude:resolve-permission"][-1]
    assert inv["params"]["result"] == {"behavior": "allow", "updatedInput": {"command": "ls"}}
    with pytest.raises(WriteRefused):
        await service.session_answer(
            f, "h1", "sess-claude-0001", confirm=True, answers=["again"]
        )  # nothing pending
    await f.close()
