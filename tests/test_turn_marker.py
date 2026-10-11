"""Turn markers: a send/relay returns turn_marker; session_wait/session_read(after=...) ignore older output."""

from __future__ import annotations

import asyncio

import pytest

from bat_agent_connector import lifecycle, service
from bat_agent_connector.errors import BatError, WriteRefused
from tests.conftest import adopt

SID = "sess-claude-0001"
MANAGED = {"writes": True, "managed_roots": ["/srv"]}


@pytest.fixture(autouse=True)
def connector_sessions(controller_context):
    """Turn-marker mechanics run on sessions the connector created in a managed root."""
    adopt(SID)
    adopt("sess-codex-0002")


def test_marker_ms_parsing():
    assert service.marker_ms(None) is None
    assert service.marker_ms("user-1790486096680") == 1790486096680
    assert service.marker_ms("1790486096680") == 1790486096680
    assert service.marker_ms(1790486096680) == 1790486096680
    assert service.marker_ms("2026-09-27T01:14:56+00:00") == 1790471696000
    with pytest.raises(BatError):
        service.marker_ms("not-a-marker")


async def test_send_returns_marker_and_read_hides_previous_turn(fleet_factory, mock):
    mock.echo_sends = True
    f = fleet_factory(**MANAGED)
    r = await service.session_send(f, "h1", SID, "four new requests", confirm=True)
    assert r["marker_source"] == "sent_message"
    assert r["turn_marker"].startswith("batc-") and r["turn_marker"] == r["message_id"] and r["after_ms"]

    # right after the send: idle, no reply yet -> the previous turn's messages must not come back
    rd = await service.session_read(f, "h1", SID, after=r["turn_marker"])
    assert rd["messages"] == [] and rd["turn_started"] is False and rd["turn_done"] is False
    assert "do NOT report" in rd["note"]
    plain = await service.session_read(f, "h1", SID, last_n=3)
    assert any("live message" in (m["text"] or "") for m in plain["messages"])  # old behaviour unchanged

    # the session answers the new turn
    mock.states[SID]["messages"].append(
        {"id": "new-1", "role": "assistant", "content": "working on the 4 items", "timestamp": r["after_ms"] + 5}
    )
    rd = await service.session_read(f, "h1", SID, after=r["turn_marker"])
    assert [m["text"] for m in rd["messages"]] == ["working on the 4 items"]
    assert rd["turn_done"] is True
    await f.close()


async def test_wait_after_ignores_stale_idle_then_returns_on_reply(fleet_factory, mock):
    mock.echo_sends = True
    f = fleet_factory(**MANAGED)
    r = await service.session_send(f, "h1", SID, "new task", confirm=True)
    marker = r["turn_marker"]

    # stale idle: the session has not replied after the marker -> must NOT return "idle"
    w = await service.session_wait(f, "h1", SID, timeout_s=1, after=marker)
    assert w["status"] == "timeout" and w["turn_started"] is False and "not started" in w["note"]

    async def answer():
        await asyncio.sleep(0.3)
        mock.metas[SID]["isStreaming"] = True
        await mock.broadcast("agent:turn-end", {"sessionId": SID})  # an older turn ending: still streaming
        await asyncio.sleep(0.3)
        mock.states[SID]["messages"].append(
            {"id": "new-2", "role": "assistant", "content": "done", "timestamp": r["after_ms"] + 10}
        )
        mock.metas[SID]["isStreaming"] = False
        await mock.broadcast("agent:turn-end", {"sessionId": SID})

    t = asyncio.create_task(answer())
    w = await service.session_wait(f, "h1", SID, timeout_s=5, after=marker)
    await t
    assert w["status"] == "event" and w["event"] == "agent:turn-end" and w["turn_done"] is True

    # already answered -> returns at once
    w = await service.session_wait(f, "h1", SID, timeout_s=5, after=marker)
    assert w["status"] == "done" and w["elapsed_s"] < 2
    await f.close()


async def test_marker_fallback_without_echo(fleet_factory, mock):
    f = fleet_factory(**MANAGED)  # mock does not echo the prompt
    r = await service.session_send(f, "h1", SID, "x", confirm=True)
    assert r["marker_source"] == "pending_echo"
    assert r["turn_marker"] == r["message_id"]
    assert r["after_ms"] == 1_790_000_020_000  # newest message before the send (host clock)
    w = await service.session_wait(f, "h1", SID, after=r["turn_marker"], timeout_s=1)
    assert w["status"] == "timeout" and w["turn_attribution"] == "echo_not_visible"
    await f.close()


async def test_custom_message_id_is_still_an_exact_echo_marker(fleet_factory, mock):
    mock.echo_sends = True
    f = fleet_factory(**MANAGED)
    sent = await service.session_send(f, "h1", SID, "custom id", confirm=True, message_id="caller-id-A")
    assert sent["turn_marker"] == "caller-id-A"
    rd = await service.session_read(f, "h1", SID, after=sent["turn_marker"])
    assert rd["after"]["message_id"] == "caller-id-A" and rd["messages"] == []
    await f.close()


async def test_same_claude_message_id_can_retry_without_rate_interval(fleet_factory, mock):
    mock.echo_sends = True
    f = fleet_factory(**MANAGED)
    first = await service.session_send(f, "h1", SID, "retry me", confirm=True, message_id="retry-id-A")
    again = await service.session_send(f, "h1", SID, "retry me", confirm=True, message_id="retry-id-A")
    assert again["turn_marker"] == first["turn_marker"]
    assert len([m for m in mock.states[SID]["messages"] if m.get("id") == "retry-id-A"]) == 1
    with pytest.raises(WriteRefused, match="different text"):
        await service.session_send(f, "h1", SID, "changed text", confirm=True, message_id="retry-id-A")
    await f.close()


async def test_codex_marker_is_labeled_timestamp_fallback(fleet_factory, mock):
    f = fleet_factory(**MANAGED)
    c = f.client("h1")
    original_invoke = c.invoke
    retries = []

    async def observe(channel, params=None, **kwargs):
        if channel == "claude:send-message":
            retries.append(kwargs.get("retry_on_disconnect"))
        return await original_invoke(channel, params, **kwargs)

    c.invoke = observe
    mock.metas["sess-codex-0002"]["isStreaming"] = False
    mock.states["sess-codex-0002"]["messages"].append(
        {"id": "old", "role": "assistant", "content": "old", "timestamp": 1_790_000_000_000}
    )
    sent = await service.session_send(f, "h1", "sess-codex-0002", "new", confirm=True)
    assert sent["message_id"].startswith("batc-")
    assert sent["marker_source"] == "codex_timestamp_fallback"
    assert sent["turn_marker"] == str(sent["after_ms"])
    assert retries == [False]
    rd = await service.session_read(f, "h1", "sess-codex-0002", after=sent["turn_marker"])
    assert rd["turn_attribution"] == "timestamp_cursor"
    await f.close()


async def test_relay_surfaces_marker(fleet_factory, mock):
    mock.echo_sends = True
    f = fleet_factory(**MANAGED)
    r = await lifecycle.session_relay(f, "h1", session_id=SID, message="1. fix chat\n2. mobile", confirm=True)
    assert r["sent"] is True and r["turn_marker"].startswith("batc-")
    assert f'after="{r["turn_marker"]}"' in r["next"]
    await f.close()


async def test_exact_id_ignores_same_prompt_from_another_send(fleet_factory, mock):
    mock.echo_sends = True
    f = fleet_factory(**MANAGED, safety={"write_min_interval_s": 0})
    first = await service.session_send(f, "h1", SID, "identical prompt", confirm=True)
    second = await service.session_send(f, "h1", SID, "identical prompt", confirm=True)
    assert first["turn_marker"] != second["turn_marker"]
    assert second["marker_source"] == "sent_message"
    rd = await service.session_read(f, "h1", SID, after=second["turn_marker"])
    assert rd["after"]["message_id"] == second["message_id"]
    assert rd["messages"] == []
    await f.close()


async def test_queued_old_turn_output_is_unconfirmed(fleet_factory, mock):
    mock.echo_sends = True
    mock.metas[SID]["isStreaming"] = True
    mock.metas[SID]["numTurns"] = 3
    f = fleet_factory(**MANAGED)
    sent = await service.session_send(f, "h1", SID, "queued work", confirm=True, queue=True)
    assert sent["queued"] is True and sent["turn_marker"].startswith("batc-")
    mock.states[SID]["messages"].append({"id": "older-reply", "role": "assistant",
                                          "content": "previous task done", "timestamp": sent["after_ms"] + 1})
    mock.metas[SID]["numTurns"] = 4
    rd = await service.session_read(f, "h1", SID, after=sent["turn_marker"])
    assert rd["messages"] == [] and rd["turn_phase"] == "accepted"
    mock.states[SID]["messages"].append({"id": "new-reply", "role": "assistant",
                                          "content": "queued work done", "timestamp": sent["after_ms"] + 2})
    mock.metas[SID]["numTurns"] = 5
    mock.metas[SID]["isStreaming"] = False
    rd = await service.session_read(f, "h1", SID, after=sent["turn_marker"])
    assert [m["text"] for m in rd["messages"]] == ["queued work done"]
    assert rd["turn_phase"] == "terminal" and rd["turn_done"] is True
    await f.close()


async def test_disconnect_after_acceptance_reconciles_by_echo(fleet_factory, mock):
    mock.echo_sends = True
    f = fleet_factory(**MANAGED)
    sent = await service.session_send(f, "h1", SID, "work after reconnect", confirm=True)
    await mock.drop_all()
    mock.states[SID]["messages"].append({"id": "answer-after-drop", "role": "assistant",
                                          "content": "completed", "timestamp": sent["after_ms"] + 1})
    rd = await service.session_read(f, "h1", SID, after=sent["turn_marker"])
    assert [m["text"] for m in rd["messages"]] == ["completed"]
    assert rd["turn_done"] is True
    await f.close()
