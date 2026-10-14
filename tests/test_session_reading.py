"""Reading metadata is personal, durable and independent of acknowledgement."""
from __future__ import annotations

import asyncio
import copy
import json

import pytest

from bat_agent_connector import api_auth, cli, dashboard_sync, mcp_server
from bat_agent_connector import session_reading as reading
from bat_agent_connector.operations import OperationError, OperationService
from bat_agent_connector.task_journal import Journal
from tests import test_api_v1 as api
from tests.operation_helpers import settle_operations

daemon = api.daemon
served = api.served
READER = api_auth.Principal("reader", frozenset({"observe"}))
OTHER = api_auth.Principal("other", READER.scopes)
TARGET = {"host": "h1", "session_id": "sess-claude-0001"}


def observe(d, messages=None, *, complete=True, principal=READER):
    messages = messages if messages is not None else [
        {"id": f"message-{i}", "role": "assistant", "text": f"Original {i}"} for i in range(5)]
    snapshot = reading.index_messages(messages, complete=complete)
    return reading.observe(d.journal, principal, {**TARGET, "messages": copy.deepcopy(messages), "_reading_index": snapshot})


def state(d, principal=READER):
    return reading.reading(d.journal, principal, **{"host": TARGET["host"], "sid": TARGET["session_id"]})


def receipt(message):
    return {"message_id": message["id"], "revision": message["reading"]["revision"]}


def mark(d, message, *, key="read", principal=READER):
    return d.ops.create(principal, action="session.read", target=TARGET, params={"messages": [receipt(message)]},
                        preconditions={}, idempotency_key=key)[0]


def position(d, message, *, version=0, key="position", principal=READER):
    return d.ops.create(principal, action="session.position", target=TARGET,
        params={**receipt(message), "offset": -25}, preconditions={"expected_version": version}, idempotency_key=key)[0]


async def test_read_marks_only_requested_revision_not_skipped_history_or_other_principals(daemon, mock):
    doc = observe(daemon)
    assert state(daemon)["unread_count"] == 5 and state(daemon)["complete"]
    assert not daemon.journal.db.execute("SELECT 1 FROM session_message_reads").fetchone()
    op = mark(daemon, doc["messages"][-1])
    await settle_operations(daemon.ops)
    assert daemon.ops.get(op["operation_id"])["status"] == "succeeded"
    assert state(daemon)["unread_count"] == 4 and state(daemon, OTHER)["unread_count"] == 5
    assert not mock.frames
    assert not state(daemon)["position"]
    assert "Original 4" not in str(list(daemon.journal.db.execute("SELECT * FROM session_message_index")))


async def test_changed_message_and_delayed_receipt_cannot_clear_new_content(daemon):
    doc = observe(daemon)
    pending = mark(daemon, doc["messages"][-1])
    updated = [{k: v for k, v in message.items() if k != "reading"} for message in doc["messages"]]
    updated[-1]["text"] += " appended by the agent"
    current = observe(daemon, updated)
    await settle_operations(daemon.ops)
    assert daemon.ops.get(pending["operation_id"])["status"] == "succeeded"
    assert state(daemon)["unread_count"] == 5
    assert receipt(doc["messages"][-1]) != receipt(current["messages"][-1])
    mark(daemon, current["messages"][-1], key="current")
    await settle_operations(daemon.ops)
    assert state(daemon)["unread_count"] == 4
    assert mark(daemon, doc["messages"][-1])["operation_id"] == pending["operation_id"]
    assert state(daemon)["unread_count"] == 4


async def test_rotation_shares_identity_but_changed_scopes_cannot_replay_or_resume(daemon):
    doc = observe(daemon)
    op = mark(daemon, doc["messages"][0])
    await settle_operations(daemon.ops)
    rotated = api_auth.Principal(READER.actor, READER.scopes, credential_id="rotated")
    assert state(daemon, rotated)["unread_count"] == 4
    altered = api_auth.Principal(READER.actor, READER.scopes | {"manage"})
    assert state(daemon, altered)["unread_count"] == 5
    with pytest.raises(OperationError, match="original effective principal"):
        mark(daemon, doc["messages"][0], principal=altered)
    for verb in (daemon.ops.resume, daemon.ops.cancel):
        with pytest.raises(OperationError, match="original effective principal"):
            verb(altered, op["operation_id"])


@pytest.mark.parametrize("create", [mark, position])
async def test_pre_controller_personal_metadata_receipts_remain_replayable(daemon, mock, create):
    message = observe(daemon)["messages"][0]
    original = create(daemon, message)
    await settle_operations(daemon.ops)
    refs = daemon.ops.get(original["operation_id"])["external_refs"]
    refs["admission_binding"].pop("controller_authority")
    daemon.journal.db.execute("UPDATE operations SET external_refs=? WHERE operation_id=?",
                              (json.dumps(refs), original["operation_id"]))
    assert create(daemon, message)["operation_id"] == original["operation_id"]
    changed = api_auth.Principal(READER.actor, READER.scopes | {"manage"})
    with pytest.raises(OperationError, match="original effective principal"):
        create(daemon, message, principal=changed)
    assert not mock.frames


async def test_position_is_separate_has_exact_id_and_concurrent_entry_cannot_overwrite(daemon):
    doc = observe(daemon)
    first = position(daemon, doc["messages"][1])
    competing = position(daemon, doc["messages"][3], key="second-entry")
    await daemon.ops._execute(first["operation_id"])
    await daemon.ops._execute(competing["operation_id"])
    assert daemon.ops.get(competing["operation_id"])["error_code"] == "READING_POSITION_CHANGED"
    anchor = state(daemon)["position"]
    assert anchor["message_id"] == "message-1" and anchor["version"] == 1 and anchor["page_offset"] == 3
    assert state(daemon)["unread_count"] == 5
    assert not state(daemon, OTHER)["position"]
    updated = [{k: v for k, v in message.items() if k != "reading"} for message in doc["messages"]]
    updated.append({"id": "new-message", "role": "assistant", "text": "Newer"})
    observe(daemon, updated)
    assert state(daemon)["position"]["page_offset"] == 4


async def test_receipts_and_anchor_survive_restart_without_transcript_or_schema_step(daemon):
    doc = observe(daemon)
    saved = position(daemon, doc["messages"][0])
    mark(daemon, doc["messages"][0])
    await settle_operations(daemon.ops)
    schema_version = daemon.journal.db.execute("PRAGMA user_version").fetchone()[0]
    journal = Journal(daemon.journal.path)
    try:
        assert journal.db.execute("PRAGMA user_version").fetchone()[0] == schema_version
        assert reading.reading(journal, READER, "h1", TARGET["session_id"])["unread_count"] == 4
        ops = OperationService(journal, actions=reading.ACTIONS)
        replay, created = ops.create(READER, action="session.position", target=TARGET,
            params={**receipt(doc["messages"][0]), "offset": -25}, preconditions={"expected_version": 0}, idempotency_key="position")
        assert not created and replay["operation_id"] == saved["operation_id"]
        assert reading.reading(journal, READER, "h1", TARGET["session_id"])["position"]["version"] == 1
    finally:
        journal.close()


def test_partial_history_missing_ids_duplicate_ids_and_truncated_text_are_honest(daemon):
    doc = observe(daemon)
    partial = [{"id": "new", "role": "assistant", "text": "new"}, {"role": "assistant", "text": "no ID"}]
    result = observe(daemon, partial)
    assert not result["reading"]["complete"] and result["reading"]["known_count"] == 6
    assert "reading" not in result["messages"][-1]
    assert not reading.index_messages([partial[0], partial[0]], complete=True)["complete"]
    snapshot = reading.index_messages([{ "id": "long", "role": "assistant", "text": "full content"}], complete=True)
    clipped = reading.observe(daemon.journal, READER, {**TARGET, "messages": [{"id": "long", "text": "full…"}], "_reading_index": snapshot})
    assert not clipped["messages"][0]["reading"]["can_mark"]
    # Complete replacement drops no longer present messages from the current observed count.
    assert state(daemon)["known_count"] == 1
    assert len(doc["messages"]) == 5


async def test_late_observation_never_rolls_new_content_back_or_revives_old_anchor_offsets(daemon):
    older = reading.begin_observation(daemon.journal)
    original = [{"id": "message", "role": "assistant", "text": "Old"}]
    old_index = reading.index_messages(original, complete=True)
    new = observe(daemon, [{**original[0], "text": "New"}])
    mark(daemon, new["messages"][0])
    await settle_operations(daemon.ops)
    old = reading.observe(daemon.journal, READER, {**TARGET, "messages": original,
        "_reading_index": old_index}, generation=older)
    assert old["messages"][0]["reading"]["unread"]
    assert state(daemon)["unread_count"] == 0
    assert daemon.journal.db.execute("SELECT revision FROM session_message_index").fetchone()[0] == new["messages"][0]["reading"]["revision"]


@pytest.mark.parametrize("mutate", [
    lambda p: p.update(messages=[]),
    lambda p: p["messages"][0].update(revision="f" * 64),
    lambda p: p["messages"].append(p["messages"][0]),
    lambda p: p["messages"][0].update(message_id="unknown"),
])
def test_invalid_or_unobserved_receipts_refused_before_operation(daemon, mutate):
    params = {"messages": [receipt(observe(daemon)["messages"][0])]}
    mutate(params)
    with pytest.raises(OperationError):
        daemon.ops.create(READER, action="session.read", target=TARGET, params=params, preconditions={}, idempotency_key="bad")
    assert not daemon.journal.db.execute("SELECT 1 FROM operations").fetchone()


async def test_actual_http_prefix_resolves_exact_session_and_same_identity_shares_receipts(served, mock):
    d, port = served
    auth = api.token(d, "reader", "observe")
    status, doc = await api.http(port, "GET", "/api/v1/sessions/h1/sess-claude/messages?last_n=2", tok=auth)
    assert status == 200 and doc["session_id"] == TARGET["session_id"]
    assert "_reading_index" not in doc and doc["reading"]["known_count"] > len(doc["messages"])
    assert doc["reading"]["complete"]
    assert not d.journal.db.execute("SELECT 1 FROM session_message_reads").fetchone()
    code, result = await api.http(port, "POST", "/api/v1/operations", tok=auth,
        headers={"Idempotency-Key": "explicit"}, body={"action": "session.read", "target": TARGET,
        "params": {"messages": [receipt(doc["messages"][-1])]}, "preconditions": {}})
    assert code in (200, 202)
    await settle_operations(d.ops)
    rotated = api.token(d, "reader", "observe")
    _, updated = await api.http(port, "GET", "/api/v1/sessions/h1/sess-claude/messages?last_n=2", tok=rotated)
    assert updated["reading"]["unread_count"] == doc["reading"]["unread_count"] - 1
    assert not updated["messages"][-1]["reading"]["unread"]
    assert not api.write_frames(mock)
    assert result["operation"]["action"] == "session.read"
    assert dashboard_sync.identity(d.journal, READER)["principal_id"]


@pytest.mark.parametrize("method", ["inventory_sessions", "inventory_session", "project_get"])
async def test_http_mcp_cli_reading_parity_is_personal_and_never_marks_read(
        served, mock, monkeypatch, capsys, method):
    from tests.test_work_items import project

    d, port = served
    await d.inventory.refresh_host("h1")
    pid = await project(d, task_project="reading-project")
    task = d.journal.submit(project="reading-project", host="h1", workspace="demo-project",
                            original_words="Fixture work", idempotency_key="reading-work")
    d.journal.change(task["task_id"], "dispatching")
    d.journal.change(task["task_id"], "accepted", fields={"session_id": TARGET["session_id"]})
    doc = observe(d)
    mark(d, doc["messages"][0])
    position(d, doc["messages"][1])
    await settle_operations(d.ops)
    monkeypatch.setenv("BATC_TASK_URL", f"http://127.0.0.1:{port}/rpc")
    cases = {
        "inventory_sessions": ("/api/v1/sessions?order=id&limit=1&provider=claude",
            {"order": "id", "limit": 1, "provider": "claude"},
            ["inventory", "sessions", "--order", "id", "--limit", "1", "--provider", "claude"], "sessions"),
        "inventory_session": ("/api/v1/sessions/h1/" + TARGET["session_id"], TARGET,
            ["inventory", "session", "h1", TARGET["session_id"]], "session"),
        "project_get": ("/api/v1/projects/" + pid, {"project_id": pid}, ["project", "show", pid], "work"),
    }
    path, params, command, field = cases[method]
    tables = ("session_message_reads", "session_reading_positions", "operations")
    before = {table: [tuple(row) for row in d.journal.db.execute(f"SELECT * FROM {table}")]
              for table in tables}
    server, fleet = mcp_server.build_server(d.fleet.config, read_only=True)
    try:
        for principal, unread in ((READER, 4), (OTHER, 5)):
            auth = api.token(d, principal.actor, *principal.scopes)
            monkeypatch.setenv("BATC_API_TOKEN", auth)
            monkeypatch.setenv("BATC_TASK_CAPABILITY", auth)
            status, expected = await api.http(port, "GET", path, tok=auth)
            assert status == 200, expected
            row = expected[field] if field == "session" else expected[field][0]
            assert row["session_id"] == TARGET["session_id"]
            assert row["reading"]["unread_count"] == unread
            assert bool(row["reading"]["position"]) == (principal == READER)
            result = await server.call_tool(method, params)
            if isinstance(result, tuple):
                content, structured = result
                actual = structured if structured is not None else json.loads(content[0].text)
            else:
                content = result.content if hasattr(result, "content") else result
                actual = json.loads(content[0].text)
            assert actual == expected
            assert await asyncio.to_thread(cli.main, ["--json", *command]) == 0
            assert json.loads(capsys.readouterr().out) == expected
    finally:
        await fleet.close()
    assert before == {table: [tuple(row) for row in d.journal.db.execute(f"SELECT * FROM {table}")]
                      for table in tables}
    assert not api.write_frames(mock)


async def test_tool_rows_change_page_offsets_without_entering_read_counts(daemon):
    raw = [{"id": "text-old", "role": "assistant", "content": "Read this"},
           {"id": "tool", "toolName": "Bash", "input": {"command": "echo hi"}, "status": "completed", "result": "hi"},
           {"id": "text-new", "role": "assistant", "content": "Latest"}]
    from bat_agent_connector.summarize import summarize_message
    def document(include_tools):
        return {**TARGET, "messages": [s for m in raw if (s := summarize_message(m, include_tools=include_tools, max_chars=1000))],
                "_reading_index": reading.index_messages(raw, complete=True, include_tools=include_tools)}
    doc = reading.observe(daemon.journal, READER, document(True))
    assert doc["reading"]["known_count"] == 2
    assert "reading" not in doc["messages"][1]
    op = position(daemon, doc["messages"][0])
    await settle_operations(daemon.ops)
    assert daemon.ops.get(op["operation_id"])["status"] == "succeeded"
    # A delayed tools view can race with a newer plain transcript observation.
    old = reading.begin_observation(daemon.journal)
    plain = reading.observe(daemon.journal, READER, document(False))
    assert plain["reading"]["position"]["page_offset"] == 1
    with_tools = reading.observe(daemon.journal, READER, document(True), generation=old)
    assert with_tools["reading"]["position"]["page_offset"] == 2
    assert with_tools["reading"]["unread_count"] == 2


async def test_incomplete_reasons_and_unindexed_count_follow_current_read_with_mock_bat(served, mock):
    d, port = served
    auth = api.token(d, "reader", "observe")
    path = "/api/v1/sessions/h1/sess-claude/messages?last_n=5"

    # Complete read has no incomplete_reasons and no unindexed_count.
    status, doc = await api.http(port, "GET", path, tok=auth)
    assert status == 200 and doc["reading"]["complete"] is True
    assert doc["reading"]["incomplete_reasons"] == []
    assert "unindexed_count" not in doc["reading"]

    # hasMore missing -> archive_unconfirmed.
    mock.handlers["claude:load-archived"] = lambda p: {
        "messages": [{"id": "arch-missing-flag", "role": "user", "content": "archived"}],
        "total": 1,
    }
    status, doc = await api.http(port, "GET", path, tok=auth)
    assert status == 200 and doc["reading"]["complete"] is False
    assert doc["reading"]["incomplete_reasons"] == ["archive_unconfirmed"]
    assert "unindexed_count" not in doc["reading"]

    # Non-dict answer -> archive_unconfirmed.
    mock.handlers["claude:load-archived"] = lambda p: ["not-a-dict"]
    status, doc = await api.http(port, "GET", path, tok=auth)
    assert status == 200 and doc["reading"]["complete"] is False
    assert doc["reading"]["incomplete_reasons"] == ["archive_unconfirmed"]
    assert "unindexed_count" not in doc["reading"]

    # 3,000-message budget reached -> archive_budget.
    mock.handlers["claude:load-archived"] = lambda p: {
        "messages": [
            {"id": f"budget-{p['offset'] + i}", "role": "assistant", "content": f"item {p['offset'] + i}"}
            for i in range(p["limit"])
        ],
        "hasMore": True,
        "total": 3500,
    }
    status, doc = await api.http(port, "GET", path, tok=auth)
    assert status == 200 and doc["reading"]["complete"] is False
    assert doc["reading"]["incomplete_reasons"] == ["archive_budget"]
    assert "unindexed_count" not in doc["reading"]

    # Duplicate ID and missing ID -> message_ids + unindexed_count.
    mock.handlers["claude:load-archived"] = lambda p: {
        "messages": [
            {"id": "dup-id", "role": "assistant", "content": "first"},
            {"id": "dup-id", "role": "assistant", "content": "second"},
            {"role": "assistant", "content": "missing id"},
        ],
        "hasMore": False,
        "total": 3,
    }
    status, doc = await api.http(port, "GET", path, tok=auth)
    assert status == 200 and doc["reading"]["complete"] is False
    assert doc["reading"]["incomplete_reasons"] == ["message_ids"]
    assert doc["reading"]["unindexed_count"] == 3

