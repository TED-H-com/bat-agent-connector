"""Explicit synthetic file grants; no personal transcripts, CLI launches or model calls."""
from __future__ import annotations

import asyncio
import json
from dataclasses import replace

import pytest

from bat_agent_connector import pm_reads, session_history
from bat_agent_connector.errors import ConfigError
from bat_agent_connector.operations import OperationError
from bat_agent_connector.session_history_config import HistoryConfig, HistorySource, parse_history
from tests.test_api_v1 import daemon, served  # noqa: F401
from tests.test_pm_api import OWNER, seed

SESSION = "11111111-1111-4111-8111-111111111111"
AT = "2026-10-18T10:00:00Z"


def append(path, *records):
    with path.open("ab") as stream:
        for record in records:
            stream.write(json.dumps(record).encode() + b"\n")


def message(text="Visible reply", mid="message-1", role="assistant"):
    return {"timestamp": AT, "type": "response_item", "payload": {
        "type": "message", "id": mid, "role": role, "content": [{"type": "output_text", "text": text}]}}


@pytest.fixture
async def ready(daemon, tmp_path, monkeypatch):  # noqa: F811
    workspace, _ = await seed(daemon)
    wid = workspace["workspace_id"]
    source = HistorySource("example", wid, "codex-history-jsonl-v1", tmp_path / "history.jsonl", SESSION, (OWNER.actor,))
    append(source.path, {"type": "session_meta", "payload": {"id": SESSION, "cli_version": "0.160.0",
        "base_instructions": "PRIVATE INSTRUCTIONS", "cwd": "/private/checkout", "creator_account_id": "PRIVATE ACCOUNT"}})
    daemon.history_collector.config = HistoryConfig(10, (source,))
    monkeypatch.setattr(daemon.journal, "owner_valid", lambda: True)
    return daemon, wid, source


def read(d, wid, source, **params):
    return pm_reads.read(d.ops, OWNER, "pm_history_get", {"workspace_id": wid, "source_id": source.source_id, **params})


async def test_history_requires_its_own_grant_and_keeps_stable_readonly_pages(ready):
    d, wid, source = ready
    append(source.path, message(), message("Another reply", "message-2"))
    version = d.journal.db.execute("PRAGMA user_version").fetchone()[0]
    await d.history_collector.collect_once()
    first = read(d, wid, source, limit=1)
    assert first["source"]["entries"] == 2 and first["next_after"] == 1
    assert first["source"]["execution_status"] == "unknown"
    assert first["source"]["ownership"] == "external_read_only"
    assert first["source"]["coverage"]["history_complete"] is False
    assert first["entries"][0]["parts"] == [{"kind": "text", "text": "Visible reply"}]
    restarted = session_history.HistoryCollector(d.journal, d.history_collector.config)
    await restarted.collect_once()
    repeated = read(d, wid, source, limit=1)
    assert repeated["entries"] == first["entries"] and repeated["next_after"] == first["next_after"]
    second = read(d, wid, source, after=first["next_after"])
    assert second["entries"][0]["parts"][0]["text"] == "Another reply"
    assert second["next_after"] is None
    dump = "\n".join(d.journal.db.iterdump())
    assert "PRIVATE INSTRUCTIONS" not in dump and "PRIVATE ACCOUNT" not in dump and "/private/checkout" not in dump
    assert str(source.path) not in dump and SESSION not in json.dumps(first)
    assert d.journal.db.execute("PRAGMA user_version").fetchone()[0] == version
    assert session_history.capabilities()["start_owned"] is False


async def test_source_and_reader_withdrawal_hide_body_before_any_collection(ready):
    d, wid, source = ready
    append(source.path, message())
    await d.history_collector.collect_once()
    d.history_collector.config = HistoryConfig(10, (replace(source, readers=("another",)),))
    assert session_history.list_sources(d.ops, OWNER, wid)["rows"] == []
    with pytest.raises(OperationError, match="unavailable"):
        read(d, wid, source)
    d.history_collector.config = HistoryConfig(10, ())
    with pytest.raises(OperationError, match="unavailable"):
        read(d, wid, source)
    assert d.journal.db.execute("SELECT COUNT(*) FROM session_history_entries").fetchone()[0] == 1


async def test_changed_binding_never_transfers_original_history(ready):
    d, wid, source = ready
    append(source.path, message())
    await d.history_collector.collect_once()
    changed = replace(source, path=source.path.parent / "other.jsonl")
    changed.path.write_text(source.path.read_text())
    d.history_collector.config = HistoryConfig(10, (changed,))
    await d.history_collector.collect_once()
    result = read(d, wid, changed)
    assert result["source"]["status"] == "binding_changed" and result["entries"] == []


@pytest.mark.parametrize("change", ["truncate", "replace", "unlink", "conflict"])
async def test_original_file_failure_withdraws_derived_content(ready, change):
    d, wid, source = ready
    append(source.path, message())
    await d.history_collector.collect_once()
    if change == "truncate":
        source.path.write_text("")
    elif change == "replace":
        replacement = source.path.with_suffix(".new")
        replacement.write_text(source.path.read_text())
        replacement.replace(source.path)
    elif change == "unlink":
        source.path.unlink()
    else:
        append(source.path, message("Changed original content"))
    await d.history_collector.collect_once()
    result = read(d, wid, source)
    assert not result["source"]["readable"] and result["entries"] == []
    assert d.journal.db.execute("SELECT COUNT(*) FROM session_history_entries").fetchone()[0] == 1


async def test_unfinished_lines_resume_once_and_duplicate_ids_do_not_duplicate_entries(ready):
    d, wid, source = ready
    raw = json.dumps(message()).encode()
    with source.path.open("ab") as stream:
        stream.write(raw)
    await d.history_collector.collect_once()
    assert read(d, wid, source)["source"]["status"] == "catching_up"
    assert read(d, wid, source)["entries"] == []
    with source.path.open("ab") as stream:
        stream.write(b"\n" + raw + b"\n")
    await d.history_collector.collect_once()
    result = read(d, wid, source)
    assert len(result["entries"]) == 1 and result["source"]["status"] == "current"


async def test_claude_public_content_omits_reasoning_and_tool_arguments(ready):
    d, wid, source = ready
    source = replace(source, adapter="claude-history-jsonl-v1")
    source.path.write_text("")
    d.history_collector.config = HistoryConfig(10, (source,))
    append(source.path, {"type": "assistant", "uuid": "message-1", "timestamp": AT,
        "sessionId": SESSION, "version": "2.1.0", "message": {"role": "assistant", "content": [
            {"type": "thinking", "thinking": "PRIVATE REASONING"},
            {"type": "text", "text": "Visible explanation"},
            {"type": "tool_use", "name": "Read", "input": {"path": "PRIVATE TOOL INPUT"}}]}})
    await d.history_collector.collect_once()
    result = read(d, wid, source)
    assert result["entries"][0]["parts"] == [{"kind": "text", "text": "Visible explanation"},
        {"kind": "tool_request", "name": "Read"}]
    assert result["source"]["coverage"]["omitted_parts"] == 2
    assert "PRIVATE" not in "\n".join(d.journal.db.iterdump())


async def test_withdrawal_during_file_read_prevents_commit(ready, monkeypatch):
    d, wid, source = ready
    append(source.path, message())
    original = session_history.read_page
    loop = asyncio.get_running_loop()

    def revoke(*args, **kwargs):
        page = original(*args, **kwargs)
        loop.call_soon_threadsafe(setattr, d.history_collector, "config", HistoryConfig())
        return page

    monkeypatch.setattr(session_history, "read_page", revoke)
    await d.history_collector.collect_once()
    assert d.journal.db.execute("SELECT COUNT(*) FROM session_history_entries").fetchone()[0] == 0


@pytest.mark.parametrize("value", [None, {"sources": "all"}, {"interval_s": True}, {"interval_s": float('nan')},
    {"sources": [{"path": "/example/*.jsonl"}]}, {"allow_usage_sources": True}])
def test_history_configuration_rejects_implicit_or_malformed_grants(value):
    with pytest.raises(ConfigError):
        parse_history({"session_history": value})


def test_usage_configuration_does_not_grant_history():
    assert parse_history({"usage": {"sources": [{"source_id": "usage-only"}]}}) == HistoryConfig()


async def test_config_activation_withdraws_access_and_pending_read(ready, monkeypatch):
    import threading

    from bat_agent_connector import managed_setup
    d, wid, source = ready
    append(source.path, message())
    started, finish = threading.Event(), threading.Event()
    original = session_history.read_page

    def held(*args, **kwargs):
        result = original(*args, **kwargs)
        started.set()
        assert finish.wait(5)
        return result

    monkeypatch.setattr(session_history, "read_page", held)
    task = asyncio.create_task(d.history_collector.collect_once())
    try:
        assert await asyncio.to_thread(started.wait, 2)
        managed_setup._activate(d, replace(d._config, session_history=HistoryConfig()), {}, "history-withdrawal")
        assert session_history.list_sources(d.ops, OWNER, wid)["rows"] == []
    finally:
        finish.set()
        await task
    assert d.journal.db.execute("SELECT COUNT(*) FROM session_history_entries").fetchone()[0] == 0


async def test_lease_loss_prevents_history_commit(ready, monkeypatch):
    d, wid, source = ready
    append(source.path, message())
    original = session_history.read_page

    def read_then_lose(*args, **kwargs):
        result = original(*args, **kwargs)
        monkeypatch.setattr(d.journal, "owner_valid", lambda: False)
        return result

    monkeypatch.setattr(session_history, "read_page", read_then_lose)
    await d.history_collector.collect_once()
    assert read(d, wid, source)["source"]["status"] == "pending"
    assert d.journal.db.execute("SELECT COUNT(*) FROM session_history_entries").fetchone()[0] == 0


@pytest.mark.parametrize("field,value", [("forked_from_id", SESSION), ("parent_thread_id", SESSION),
    ("forked_from_ordinal_exclusive", 0), ("source", {"subagent": "review"}),
    ("source", {"subagent": {"thread_spawn": {"parent_thread_id": SESSION}}}),
    ("history_base", 0), ("subagent_history_start_ordinal", 0), ("id", "different")])
async def test_inherited_or_mismatched_codex_history_is_not_claimed(ready, field, value):
    d, wid, source = ready
    source.path.write_text("")
    append(source.path, {"type": "session_meta", "payload": {"id": SESSION, field: value}}, message())
    await d.history_collector.collect_once()
    result = read(d, wid, source)
    assert result["source"]["status"] == "history_identity_unproven" and result["entries"] == []


async def test_failed_atomic_page_replays_without_duplicate_or_skipped_entries(ready):
    import sqlite3
    d, wid, source = ready
    append(source.path, message(), message("Second", "message-2"))
    d.journal.db.execute("""CREATE TRIGGER fixture_fail BEFORE INSERT ON session_history_entries
        WHEN NEW.sequence = 2 BEGIN SELECT RAISE(ABORT, 'fixture'); END""")
    with pytest.raises(sqlite3.IntegrityError, match="fixture"):
        await d.history_collector.collect_once()
    assert d.journal.db.execute("SELECT COUNT(*) FROM session_history_entries").fetchone()[0] == 0
    assert d.journal.db.execute("SELECT COUNT(*) FROM session_history_sources").fetchone()[0] == 0
    d.journal.db.execute("DROP TRIGGER fixture_fail")
    await d.history_collector.collect_once()
    assert [e["sequence"] for e in read(d, wid, source)["entries"]] == [1, 2]
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        d.journal.db.execute("DELETE FROM session_history_entries")


async def test_read_pages_never_collect_and_admin_still_needs_content_grant(ready, monkeypatch):
    from bat_agent_connector.api_auth import Principal
    d, wid, source = ready
    append(source.path, message())
    await d.history_collector.collect_once()

    def forbidden(*args, **kwargs):
        raise AssertionError("A client read cannot scan original files")

    monkeypatch.setattr(session_history, "read_page", forbidden)
    changes = d.journal.db.total_changes
    assert read(d, wid, source)["entries"]
    assert session_history.list_sources(d.ops, OWNER, wid)["rows"]
    assert d.journal.db.total_changes == changes
    admin = Principal("ungranted-admin", frozenset({"observe"}), admin=True)
    assert session_history.list_sources(d.ops, admin, wid)["rows"] == []
    with pytest.raises(OperationError):
        session_history.read(d.ops, admin, wid, source.source_id)
    for params in [{"after": True}, {"after": -1}, {"after": 2**53}, {"limit": 0}, {"limit": 101}]:
        with pytest.raises(OperationError, match="INVALID_PARAMS"):
            read(d, wid, source, **params)


async def test_entry_limit_preserves_original_page_but_still_detects_source_removal(ready, monkeypatch):
    d, wid, source = ready
    append(source.path, message())
    await d.history_collector.collect_once()
    monkeypatch.setattr(session_history, "MAX_ENTRIES", 1)
    append(source.path, message("More", "message-2"))
    await d.history_collector.collect_once()
    result = read(d, wid, source)
    assert result["source"]["status"] == "entry_limit" and len(result["entries"]) == 1
    assert not result["source"]["coverage"]["file_at_eof"]
    source.path.unlink()
    await d.history_collector.collect_once()
    assert read(d, wid, source)["entries"] == []


async def test_history_transports_share_scopes_and_current_reader_grants(served, tmp_path, monkeypatch):  # noqa: F811
    from bat_agent_connector import mcp_server
    from tests.test_api_v1 import http, token
    from tests.test_pm_run_api import unpack
    d, port = served
    workspace, _ = await seed(d)
    wid = workspace["workspace_id"]
    source = HistorySource("fixture", wid, "codex-history-jsonl-v1", tmp_path / "fixture.jsonl", SESSION, (OWNER.actor,))
    append(source.path, {"type": "session_meta", "payload": {"id": SESSION}}, message())
    d.history_collector.config = HistoryConfig(10, (source,))
    monkeypatch.setattr(d.journal, "owner_valid", lambda: True)
    await d.history_collector.collect_once()
    auth = token(d, OWNER.actor, *OWNER.scopes)
    path = f"/api/v1/pm/workspaces/{wid}/history"
    status, catalog = await http(port, "GET", path, tok=auth)
    assert status == 200 and catalog["rows"][0]["source_id"] == source.source_id
    status, result = await http(port, "GET", path + "/fixture?after=0&limit=1", tok=auth)
    assert status == 200 and result["entries"]
    assert await d.call_api("pm_history_get", {"workspace_id": wid, "source_id": "fixture", "limit": 1}, OWNER) == result
    assert (await http(port, "GET", path, tok=token(d, OWNER.actor, "manage")))[0] == 403
    assert (await http(port, "GET", path, tok=token(d, "outsider", "observe")))[0] == 404
    for query in ["path=/private", "refresh=1", "limit=0", "after=9007199254740992", "after=-1"]:
        assert (await http(port, "GET", path + "/fixture?" + query, tok=auth))[0] == 422
    assert (await http(port, "POST", path + "/fixture", tok=auth, body={}))[0] in (404, 405)
    monkeypatch.setenv("BATC_TASK_URL", f"http://127.0.0.1:{port}/rpc")
    monkeypatch.setenv("BATC_API_TOKEN", auth)
    server, fleet = mcp_server.build_server(d.fleet.config, read_only=True, principal_only=True)
    try:
        names = {t.name for t in await server.list_tools()}
        assert {"pm_history_sources_list", "pm_history_get"} <= names
        got = unpack(await server.call_tool("pm_history_get", {"workspace_id": wid, "source_id": "fixture", "limit": 1}))
        assert got == result
        d.history_collector.config = HistoryConfig(10, (replace(source, readers=()),))
        assert (await http(port, "GET", path + "/fixture", tok=auth))[0] == 404
        got = unpack(await server.call_tool("pm_history_sources_list", {"workspace_id": wid}))
        assert got["rows"] == []
    finally:
        await fleet.close()


async def test_byte_bounded_pages_keep_unicode_and_every_original_sequence(ready):
    d, wid, source = ready
    for n in range(12):
        append(source.path, message("🧪" * 16001, f"message-{n}"))
    await d.history_collector.collect_once()
    assert read(d, wid, source)["source"]["status"] == "catching_up"
    await d.history_collector.collect_once()
    assert read(d, wid, source)["source"]["status"] == "current"
    after, sequences = 0, []
    while True:
        result = read(d, wid, source, after=after, limit=100)
        assert result["entries"]
        assert len(json.dumps(result["entries"]).encode()) < session_history.MAX_PAGE_BYTES + 1000
        assert all(e["clipped"] and e["parts"][0]["text"] == "🧪" * 16000 for e in result["entries"])
        sequences.extend(e["sequence"] for e in result["entries"])
        if result["next_after"] is None:
            break
        assert result["next_after"] > after
        after = result["next_after"]
    assert sequences == list(range(1, 13))


async def test_source_local_sequences_do_not_disclose_other_history_activity(ready):
    d, wid, first = ready
    second = replace(first, source_id="second", session_id="second-session", path=first.path.parent / "second.jsonl")
    append(second.path, {"type": "session_meta", "payload": {"id": second.session_id}}, message())
    d.history_collector.config = HistoryConfig(10, (first, second))
    append(first.path, message(), message("More", "message-2"))
    await d.history_collector.collect_once()
    result = read(d, wid, second)
    assert [e["sequence"] for e in result["entries"]] == [1]
    assert result["entries"][0]["entry_id"] != read(d, wid, first)["entries"][0]["entry_id"]


@pytest.mark.parametrize("sidechain", [True, "false", 0, None])
async def test_claude_sidechain_requires_explicit_false_if_present(ready, sidechain):
    d, wid, source = ready
    source = replace(source, adapter="claude-history-jsonl-v1")
    source.path.write_text("")
    d.history_collector.config = HistoryConfig(10, (source,))
    append(source.path, {"type": "assistant", "uuid": "message-1", "timestamp": AT,
        "sessionId": SESSION, "isSidechain": sidechain, "message": {"role": "assistant", "content": "Unsupported lineage"}})
    await d.history_collector.collect_once()
    result = read(d, wid, source)
    assert result["source"]["status"] == "history_identity_unproven" and result["entries"] == []
