"""PM native runtime evidence; protocol reads use the local mock BAT only."""

from __future__ import annotations

import copy

import pytest

from bat_agent_connector import channels, pm_runtime
from bat_agent_connector.errors import ChannelNotAllowed
from tests.test_client import client_for

SID, SDK, EPOCH = "pm-session", "native-thread", "replay-generation"
BASE = {"epoch": EPOCH, "seq": 3}
MODEL = "fixture-model"


def meta(**kw):
    return {"sdkSessionId": SDK, "model": MODEL, "numTurns": 0, "isStreaming": False,
            **pm_runtime.readonly_start_options(), **kw}


def snapshot(**kw):
    return {"mode": "snapshot", "sessionId": SID, "cursor": BASE,
            "state": {"sessionId": SID, "meta": meta(), "messages": [], "isStreaming": False}, **kw}


def event(seq, channel, **params):
    return {"seq": seq, "at": seq * 1000, "channel": channel, "params": {"sessionId": SID, **params}}


def transcript(reason="completed"):
    return [event(4, "claude:message", message={"id": "user-1", "role": "user", "content": "Explore options"}),
            event(5, "claude:status", meta=meta(numTurns=1, isStreaming=True)),
            event(6, "claude:message", message={"id": "answer-1", "role": "assistant", "content": "A candidate, not adopted."}),
            event(7, "claude:turn-end", payload={"reason": reason, "turnId": "turn-1", "sdkSessionId": SDK,
                                                "result": "A candidate, not adopted."})]


def delta(items=None, **kw):
    items = transcript() if items is None else items
    return {"mode": "delta", "sessionId": SID, "events": items,
            "cursor": {"epoch": EPOCH, "seq": items[-1]["seq"] if items else BASE["seq"]}, "hasMore": False, **kw}


def assess(items=None, **kw):
    items = transcript() if items is None else items
    args = {"session_id": SID, "send_cursor": BASE, "events": items,
            "current_cursor": {"epoch": EPOCH, "seq": items[-1]["seq"] if items else BASE["seq"]},
            "expected_sdk_session_id": SDK, "expected_prompt_digest": pm_runtime.prompt_digest("Explore options")}
    return pm_runtime.assess_turn(**{**args, **kw})


async def test_sync_channel_is_read_only_and_does_not_enable_any_write(mock):
    mock.handlers["claude:sync-session"] = lambda params: snapshot()
    async with client_for(mock) as client:
        replay = await pm_runtime.sync_session(client, SID)
        assert pm_runtime.fresh_send_cursor(replay, SDK) == BASE
        with pytest.raises(ChannelNotAllowed):
            await client.invoke("claude:send-message", {"sessionId": SID, "prompt": "should not send"})
    assert mock.channels() == ["claude:sync-session"]
    assert not channels.is_write("agent:sync-session")


async def test_native_replay_roundtrip_preserves_source_events(mock):
    raw = delta()
    mock.handlers["claude:sync-session"] = lambda params: raw
    async with client_for(mock) as client:
        replay = await pm_runtime.sync_session(client, SID, BASE)
    assert replay["events"] == raw["events"]
    assert replay["events"] is not raw["events"]
    assert replay["gap"] is False
    assert mock.invokes[-1]["params"] == {"sessionId": SID, "cursor": BASE}


def test_native_completion_imports_final_message_even_without_streamed_result():
    items = transcript()
    items[-1]["params"]["payload"]["result"] = ""
    result = assess(items)
    assert result["status"] == "completed"
    assert result["content"] == "A candidate, not adopted."
    assert result["native_message_id"] == "answer-1"
    assert result["native_turn_id"] == "turn-1"
    assert result["sdk_session_id"] == SDK
    assert result["actual_model"] == MODEL
    assert result["source_event_seqs"] == [6, 7]


@pytest.mark.parametrize("reason", ["aborted", "error", "resting", None])
def test_terminal_event_does_not_mean_success(reason):
    result = assess(transcript(reason))
    assert result["status"] == "incomplete" and not result["complete"]


@pytest.mark.parametrize("patch", [{"turnId": None}, {"sdkSessionId": "other-native-thread"}, {"sdkSessionId": None}])
def test_native_terminal_requires_original_session_and_real_turn(patch):
    items = transcript()
    items[-1]["params"]["payload"].update(patch)
    assert assess(items)["status"] == "unknown"


def test_idle_and_finished_message_without_terminal_remain_pending():
    items = transcript()[:-1]
    items[1]["params"]["meta"]["isStreaming"] = False
    assert assess(items)["status"] == "pending"


@pytest.mark.parametrize("kw", [{"gap": True}, {"current_cursor": {"epoch": "restarted", "seq": 7}},
                                {"current_cursor": {"epoch": EPOCH, "seq": 8}}])
def test_missing_source_evidence_cannot_complete(kw):
    assert assess(**kw)["status"] == "unknown"


def test_pagination_must_finish_before_import():
    assert assess(has_more=True)["status"] == "pending"


def test_snapshot_after_send_is_sticky_gap_even_with_complete_transcript():
    raw = snapshot()
    raw["state"]["messages"] = [{"role": "assistant", "content": "Done"}]
    replay = pm_runtime.parse_replay(raw, SID, BASE)
    assert replay["gap"] is True
    assert assess(gap=replay["gap"])["status"] == "unknown"


@pytest.mark.parametrize("mutation", ["sequence", "cursor", "epoch", "session", "nested_session", "bool_seq", "pagination"])
def test_corrupt_replay_cannot_advance_cursor(mutation):
    raw = delta()
    if mutation == "sequence":
        raw["events"].pop(1)
    elif mutation == "cursor":
        raw["cursor"]["seq"] = 9
    elif mutation == "epoch":
        raw["cursor"]["epoch"] = "changed"
    elif mutation == "session":
        raw["sessionId"] = "foreign"
    elif mutation == "nested_session":
        raw["events"][0]["params"]["sessionId"] = "foreign"
    elif mutation == "bool_seq":
        raw["events"][0]["seq"] = True
    else:
        raw["hasMore"] = "false"
    with pytest.raises(pm_runtime.RuntimeEvidenceError):
        pm_runtime.parse_replay(raw, SID, BASE)


@pytest.mark.parametrize("change", ["message", "running", "turns", "sdk", "permissions", "pending", "unknown"])
def test_presend_checkpoint_must_be_fresh_and_readonly(change):
    raw = snapshot()
    state = raw["state"]
    if change == "message":
        state["messages"] = [{"role": "assistant", "content": "old"}]
    elif change == "running":
        state["isStreaming"] = True
    elif change == "turns":
        state["meta"]["numTurns"] = 1
    elif change == "sdk":
        state["meta"]["sdkSessionId"] = "old-thread"
    elif change == "permissions":
        state["meta"]["codexSandboxMode"] = "workspace-write"
    elif change == "pending":
        state["pendingPermission"] = {"id": "approval"}
    else:
        state["meta"].pop("numTurns")
    with pytest.raises((pm_runtime.RuntimeEvidenceError, pm_runtime.confinement.ConfinementRefused)):
        pm_runtime.fresh_send_cursor(pm_runtime.parse_replay(raw, SID), SDK)


def test_permission_or_model_drift_prevents_normal_completion():
    items = transcript()
    items[1]["params"]["meta"]["codexApprovalPolicy"] = "on-request"
    assert assess(items)["reason"] == "permission_options_changed"
    assert assess(actual_model="different-model")["reason"] == "native_model_changed"


def test_replayed_or_concurrent_input_is_not_associated_with_original_run():
    assert assess(expected_prompt_digest=pm_runtime.prompt_digest("a different prompt"))["status"] == "unknown"
    items = transcript()
    items[-1] = event(7, "claude:message", message={"role": "user", "content": "another request"})
    items.append(event(8, "claude:turn-end", payload={"reason": "completed", "turnId": "turn-2", "sdkSessionId": SDK}))
    assert assess(items)["reason"] == "multiple_inputs_in_fresh_session"


def test_late_second_terminal_cannot_rewrite_the_original_turn():
    items = transcript()
    items.append(event(8, "claude:turn-end", payload={"reason": "completed", "turnId": "old-turn", "sdkSessionId": SDK}))
    assert assess(items)["reason"] == "multiple_native_terminals"


def test_oversized_reply_is_not_silently_imported_as_complete():
    items = transcript()
    items[2]["params"]["message"]["content"] = "x" * (pm_runtime.MAX_OUTPUT_CHARS + 1)
    assert assess(items)["status"] == "incomplete"


def test_replay_with_journal_epoch_envelopes_keeps_identity():
    items = [{"epoch": EPOCH, **item} for item in transcript()]
    assert assess(items)["complete"]
    items[1]["epoch"] = "other-generation"
    assert not assess(items)["complete"]


def test_installed_runtime_options_and_model_do_not_claim_tool_isolation():
    report = pm_runtime.capability_report(bat_version="fixture-version", meta=meta(),
                                         runtime={"codex": {"state": "ready", "version": "fixture-runtime"}},
                                         replay=pm_runtime.parse_replay(snapshot(), SID))
    assert report["runtime"]["status"] == "ready"
    assert report["session_options"]["status"] == "options_confirmed"
    assert report["filesystem"]["enforcement"] == "sandbox_enforcement_unverified"
    assert report["external_tools"]["status"] == "unverified"
    assert not report["automatic_pm_turn"] and not report["write_files"]
    assert report["fallback"] == "observer_context_only"
    assert report["authentication"]["model_provider"] == "unverified"
    assert report["headless"] == {"status": "unverified", "session_observed": True}


async def test_probe_only_reads_and_does_not_expose_runtime_paths(mock):
    mock.handlers["runtime:get-status"] = lambda params: {"codex": {"state": "ready", "version": "test", "path": "/private/runtime"}}
    mock.handlers["claude:sync-session"] = lambda params: snapshot()
    mock.metas[SID] = meta()
    async with client_for(mock) as client:
        report = await pm_runtime.probe(client, SID)
    assert report["authentication"]["transport"] == "authenticated"
    assert report["actual_model"] == MODEL
    assert report["native_completion"]["status"] == "replay_available"
    assert all(channel in channels.READ_CHANNELS for channel in mock.channels())
    assert "path" not in report["runtime"]


def test_runtime_options_are_fresh_values():
    options = pm_runtime.readonly_start_options()
    options["codexSandboxMode"] = "danger-full-access"
    assert pm_runtime.readonly_start_options()["codexSandboxMode"] == "read-only"
    original = transcript()
    saved = copy.deepcopy(original)
    assess(original)
    assert original == saved
