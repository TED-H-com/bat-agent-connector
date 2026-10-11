"""A10: recover a started successor without losing or duplicating its handoff."""

from __future__ import annotations

import copy
import hashlib
import json

import pytest

from bat_agent_connector import confinement, lifecycle, registry, task_bat
from bat_agent_connector.errors import InvokeTimeout
from tests.test_confinement import MANAGED
from tests.test_lifecycle import _add_wt_claude


class DaemonCrash(BaseException):
    """Bypass ordinary dispatch error handling at a precise crash boundary."""


def lose_start_confirmation(client, failure):
    invoke = client.invoke
    lost = False

    async def interrupted(channel, params=None, **kwargs):
        nonlocal lost
        result = await invoke(channel, params, **kwargs)
        if not lost and channel == ("claude:start-session" if failure == "ack" else "claude:get-session-meta"):
            if failure == "ack":
                lost = True
                raise InvokeTimeout("start frame arrived; acknowledgement lost")
            if params["sessionId"] == "successor":
                lost = True
                return None
        return result

    client.invoke = interrupted
    return invoke


def handoffs(mock):
    return [i["params"] for i in mock.invokes if i["channel"] == "claude:send-message"]


@pytest.mark.parametrize("policy,failure", [("confined", "ack"), ("confined", "meta"), ("allow_all", "ack")])
async def test_manual_failover_recovered_start_sends_reserved_handoff_once(fleet_factory, mock, failure, policy):
    lead = _add_wt_claude(mock)
    fleet = fleet_factory(writes=True, orchestrate=True, default_permission_mode=policy,
                          safety={"write_min_interval_s": 0}, **MANAGED)
    client = fleet.client("h1")
    invoke = lose_start_confirmation(client, failure)
    try:
        with pytest.raises((InvokeTimeout, confinement.ConfinementRefused)):
            await lifecycle.session_failover(fleet, "h1", lead, confirm=True, successor_session_id="successor")
        pending = registry.get("h1", "successor")
        assert pending["start_uncertain"] and pending["handoff_status"] == "pending"
        assert pending["branch"] == "bat/worktree-abc" and pending["handoff_frame_sha256"] is None
        assert not handoffs(mock)
        client.invoke = invoke
        result = await lifecycle.session_failover(fleet, "h1", lead, confirm=True)
        assert result["prompt_sent"] is True and result["error"] is None and "skipped" not in result
        assert result["message_id"] == pending["handoff_message_id"]
        assert result["new_session_id"] == "successor" and not result["counts_toward_cap"]
        assert len(handoffs(mock)) == 1 and handoffs(mock)[0]["clientMessageId"] == result["message_id"]
        assert "ORIGINAL TASK" in handoffs(mock)[0]["prompt"]
        assert len([i for i in mock.invokes if i["channel"] == "claude:start-session"]) == 1
        assert (await lifecycle.session_failover(fleet, "h1", lead, confirm=True))["skipped"]
        assert len(handoffs(mock)) == 1
    finally:
        await fleet.close()


@pytest.mark.parametrize("observed", ["different", "missing", "normalized"])
@pytest.mark.parametrize("status", ["starting", "uncertain"])
async def test_failover_recovery_checks_reserved_cwd_before_promotion(fleet_factory, mock, observed, status):
    """A10: matching permissions cannot identify a successor in another checkout."""
    lead = _add_wt_claude(mock)
    fleet = fleet_factory(writes=True, orchestrate=True, default_permission_mode="confined",
                          safety={"write_min_interval_s": 0}, **MANAGED)
    client = fleet.client("h1")
    invoke = lose_start_confirmation(client, "ack")
    try:
        with pytest.raises(InvokeTimeout):
            await lifecycle.session_failover(fleet, "h1", lead, confirm=True, successor_session_id="successor")
        registry.update("h1", "successor", status=status)
        row = registry.get("h1", "successor")
        client.invoke = invoke
        if observed == "missing":
            del mock.metas["successor"]["cwd"]
        else:
            mock.metas["successor"]["cwd"] = "/srv/another-checkout" if observed == "different" else row["cwd"] + "/./"
        if observed == "normalized":
            assert (await lifecycle.session_failover(fleet, "h1", lead, confirm=True))["prompt_sent"]
            assert len(handoffs(mock)) == 1
        else:
            code = "FAILOVER_SUCCESSOR_MISMATCH" if observed == "different" else "CONFINEMENT_START_UNSETTLED"
            with pytest.raises(confinement.ConfinementRefused, match=code):
                await lifecycle.session_failover(fleet, "h1", lead, confirm=True)
            kept = registry.get("h1", "successor")
            assert kept["status"] == status and kept["start_uncertain"]
            assert kept["confinement"] == row["confinement"]
            assert kept["handoff_frame_sha256"] is None and not handoffs(mock)
            if observed == "different":
                assert kept["error_code"] == code
                reads = mock.channels().count("claude:get-session-meta")
                mock.metas["successor"]["cwd"] = row["cwd"]
                with pytest.raises(confinement.ConfinementRefused, match=code):
                    await lifecycle.session_failover(fleet, "h1", lead, confirm=True)
                assert mock.channels().count("claude:get-session-meta") == reads
                assert registry.get("h1", "successor") == kept
                assert not handoffs(mock)
    finally:
        await fleet.close()


@pytest.mark.parametrize("phase", ["post_start", "recovery"])
@pytest.mark.parametrize("policy", ["confined", "allow_all"])
async def test_failover_recorded_permission_mismatch_is_terminal(fleet_factory, mock, phase, policy):
    """A10: a later matching read cannot rewrite mismatch evidence or dispatch context."""
    lead = _add_wt_claude(mock)
    fleet = fleet_factory(writes=True, orchestrate=True, default_permission_mode=policy,
                          safety={"write_min_interval_s": 0}, **MANAGED)
    client = fleet.client("h1")
    invoke = client.invoke
    try:
        if phase == "recovery":
            invoke = lose_start_confirmation(client, "ack")
            with pytest.raises(InvokeTimeout):
                await lifecycle.session_failover(fleet, "h1", lead, confirm=True, successor_session_id="successor")
            mock.metas["successor"]["codexSandboxMode"] = "read-only"
            client.invoke = invoke
        else:
            async def drift(channel, params=None, **kwargs):
                result = await invoke(channel, params, **kwargs)
                if channel == "claude:start-session":
                    mock.metas["successor"]["codexSandboxMode"] = "read-only"
                return result
            client.invoke = drift
        with pytest.raises(confinement.ConfinementRefused, match="CONFINEMENT_MISMATCH"):
            await lifecycle.session_failover(fleet, "h1", lead, confirm=True, successor_session_id="successor")
        row = copy.deepcopy(registry.get("h1", "successor"))
        assert row["confinement"]["verification"]["status"] == "mismatch" and row["start_uncertain"]
        mock.metas["successor"].update(row["confinement"]["options"])
        client.invoke = invoke
        reads = mock.channels().count("claude:get-session-meta")
        with pytest.raises(confinement.ConfinementRefused, match="CONFINEMENT_MISMATCH"):
            await lifecycle.session_failover(fleet, "h1", lead, confirm=True)
        assert mock.channels().count("claude:get-session-meta") == reads
        assert registry.get("h1", "successor") == row and not handoffs(mock)
        assert confinement.confirm(row["confinement"], mock.metas["successor"]) == row["confinement"]
    finally:
        await fleet.close()


@pytest.mark.parametrize("start_recovered", [True, False])
async def test_failover_crash_before_handoff_keeps_durable_unsent_proof(fleet_factory, mock, monkeypatch, start_recovered):
    lead = _add_wt_claude(mock)
    fleet = fleet_factory(writes=True, orchestrate=True, default_permission_mode="confined",
                          safety={"write_min_interval_s": 0}, **MANAGED)
    client = fleet.client("h1")
    try:
        if start_recovered:
            invoke = lose_start_confirmation(client, "ack")
            with pytest.raises(InvokeTimeout):
                await lifecycle.session_failover(fleet, "h1", lead, confirm=True, successor_session_id="successor")
            client.invoke = invoke

        invoke_checked = client._invoke_checked

        async def crash_before_handoff(channel, *args, **kwargs):
            if channel == "claude:send-message":
                assert registry.get("h1", "successor")["status"] == "active"
                raise DaemonCrash()
            return await invoke_checked(channel, *args, **kwargs)

        with monkeypatch.context() as patch:
            patch.setattr(client, "_invoke_checked", crash_before_handoff)
            with pytest.raises(DaemonCrash):
                await lifecycle.session_failover(fleet, "h1", lead, confirm=True, successor_session_id="successor")
        row = registry.get("h1", "successor")
        assert not row["start_uncertain"] and row["handoff_frame_sha256"] is None
        assert row["handoff_status"] == "pending" and not handoffs(mock)
        mock.git_status[row["cwd"]].append({"status": "M", "file": "recovered.py"})
        result = await lifecycle.session_failover(fleet, "h1", lead, confirm=True)
        assert result["prompt_sent"] and result["message_id"] == row["handoff_message_id"]
        assert len(handoffs(mock)) == 1
        assert "recovered.py" in handoffs(mock)[0]["prompt"]  # rebuilt through the ordinary handoff path
        await lifecycle.session_failover(fleet, "h1", lead, confirm=True)
        assert len(handoffs(mock)) == 1
    finally:
        await fleet.close()


@pytest.mark.parametrize("failure", ["crash", "ack"])
async def test_failover_attempted_handoff_is_never_resent(fleet_factory, mock, failure):
    lead = _add_wt_claude(mock)
    fleet = fleet_factory(writes=True, orchestrate=True, default_permission_mode="confined",
                          safety={"write_min_interval_s": 0}, **MANAGED)
    client = fleet.client("h1")
    roundtrip = client._roundtrip

    async def lost_reply(frame, timeout, on_transport=None):
        result = await roundtrip(frame, timeout, on_transport=on_transport)
        if frame.get("channel") == "claude:send-message":
            raise DaemonCrash() if failure == "crash" else InvokeTimeout("handoff acknowledgement lost")
        return result

    client._roundtrip = lost_reply
    try:
        if failure == "crash":
            with pytest.raises(DaemonCrash):
                await lifecycle.session_failover(fleet, "h1", lead, confirm=True, successor_session_id="successor")
        else:
            result = await lifecycle.session_failover(fleet, "h1", lead, confirm=True, successor_session_id="successor")
            assert not result["prompt_sent"] and result["error"]
        row = registry.get("h1", "successor")
        assert row["handoff_frame_sha256"] == hashlib.sha256(handoffs(mock)[0]["prompt"].encode()).hexdigest()
        client._roundtrip = roundtrip
        assert (await lifecycle.session_failover(fleet, "h1", lead, confirm=True))["skipped"]
        assert registry.get("h1", "successor")["handoff_status"] == "uncertain"
        assert len(handoffs(mock)) == 1
    finally:
        await fleet.close()


async def test_failover_crash_after_start_ack_before_activation_recovers_handoff(fleet_factory, mock, monkeypatch):
    lead = _add_wt_claude(mock)
    fleet = fleet_factory(writes=True, orchestrate=True, default_permission_mode="confined",
                          safety={"write_min_interval_s": 0}, **MANAGED)
    update = registry.update

    def crash_before_activation(host, sid, **fields):
        if sid == "successor" and fields.get("status") == "active":
            raise DaemonCrash()
        update(host, sid, **fields)

    monkeypatch.setattr(registry, "update", crash_before_activation)
    try:
        with pytest.raises(DaemonCrash):
            await lifecycle.session_failover(fleet, "h1", lead, confirm=True, successor_session_id="successor")
        assert registry.get("h1", "successor")["status"] == "starting" and not handoffs(mock)
        monkeypatch.setattr(registry, "update", update)
        assert (await lifecycle.session_failover(fleet, "h1", lead, confirm=True))["prompt_sent"]
        assert len(handoffs(mock)) == 1
        assert len([i for i in mock.invokes if i["channel"] == "claude:start-session"]) == 1
    finally:
        await fleet.close()


async def test_task_failover_recovered_start_preserves_journal_and_frame_guards(fleet_factory, mock, controller_context, monkeypatch):
    lead = _add_wt_claude(mock)
    fleet = fleet_factory(writes=True, orchestrate=True, default_permission_mode="confined",
                          safety={"write_min_interval_s": 0}, **MANAGED)
    # Task Service keeps its existing policy; this source already carries a confined intent.
    registry.update("h1", lead, write_scope="confined",
                    confinement=confinement.snapshot("claude", {"permissionMode": "default"}))
    journal, _ = controller_context
    task = journal.submit(project="p", host="h1", workspace="demo-project", original_words="Continue the fixture",
                          idempotency_key="handoff-recovery", lead_agent="claude")
    journal.change(task["task_id"], "dispatching")
    journal.change(task["task_id"], "accepted", fields={"session_id": lead})
    journal.add_branch(task["task_id"], session_id=lead, provider="claude", role="lead", reason="fixture")
    journal.change(task["task_id"], "running")
    task = journal.change(task["task_id"], "quota_limited")
    _, handoff = journal.reserve_failover(task["task_id"], lead, "successor")
    adapter = task_bat.BatTaskAdapter(fleet, journal=journal)
    from bat_agent_connector.task_core import TaskCoordinator
    TaskCoordinator(journal, adapter)
    registry.update('h1', lead, task_id=task['task_id'], role='lead')
    verified = adapter._verified_failover_successor
    checks = []

    async def observed_verification(*args, **kwargs):
        checks.append((kwargs.get("before_send", False), kwargs.get("reader") is not None))
        return await verified(*args, **kwargs)

    record_frame = registry.record_handoff_frame
    frames_recorded = []

    def observed_frame(*args, **kwargs):
        frames_recorded.append(kwargs["prompt_sha256"])
        return record_frame(*args, **kwargs)

    monkeypatch.setattr(adapter, "_verified_failover_successor", observed_verification)
    monkeypatch.setattr(registry, "record_handoff_frame", observed_frame)
    client = fleet.client("h1")
    invoke = lose_start_confirmation(client, "ack")
    kwargs = {"handoff_message_id": handoff["message_id"], "handoff_command_id": handoff["command_id"]}
    try:
        with pytest.raises(InvokeTimeout):
            await adapter.failover(task, lead, "successor", **kwargs)
        assert not handoffs(mock)
        assert not checks and not frames_recorded
        client.invoke = invoke
        result = await adapter.failover(task, lead, "successor", **kwargs)
        assert result == {"session_id": "successor", "marker": handoff["message_id"]}
        assert await adapter._verified_failover_successor(task, "successor", **kwargs) is True
        digest = json.loads(journal.command_get(handoff["command_id"])["payload"])["prompt_sha256"]
        assert len(handoffs(mock)) == 1
        assert handoffs(mock)[0]["clientMessageId"] == handoff["message_id"]
        assert hashlib.sha256(handoffs(mock)[0]["prompt"].encode()).hexdigest() == digest
        assert registry.get("h1", "successor")["handoff_frame_sha256"] == digest
        assert frames_recorded == [digest]
        assert (True, False) in checks and (True, True) in checks and (False, False) in checks
        assert await adapter.failover(task, lead, "successor", **kwargs) == result
        assert len(handoffs(mock)) == 1
    finally:
        await fleet.close()


def without_timestamps(value):
    if isinstance(value, dict):
        return {k: without_timestamps(v) for k, v in value.items() if k not in {"created_at", "updated_at", "checked_at"}}
    if isinstance(value, list):
        return [without_timestamps(v) for v in value]
    return value


async def test_recovered_failover_row_matches_normally_confirmed_successor(fleet_factory, mock, tmp_path, monkeypatch):
    rows = []
    for lost_ack in (False, True):
        monkeypatch.setenv("BATC_STATE_DIR", str(tmp_path / ("recovered" if lost_ack else "normal")))
        mock.ws_doc["terminals"] = [t for t in mock.ws_doc["terminals"] if t["id"] != "wt-claude-0007"]
        lead = _add_wt_claude(mock)
        fleet = fleet_factory(writes=True, orchestrate=True, default_permission_mode="confined",
                              safety={"write_min_interval_s": 0}, **MANAGED)
        client = fleet.client("h1")
        try:
            kwargs = {"confirm": True, "successor_session_id": "successor", "handoff_message_id": "batc-handoff"}
            if lost_ack:
                invoke = lose_start_confirmation(client, "ack")
                with pytest.raises(InvokeTimeout):
                    await lifecycle.session_failover(fleet, "h1", lead, **kwargs)
                client.invoke = invoke
            assert (await lifecycle.session_failover(fleet, "h1", lead, **kwargs))["prompt_sent"]
            rows.append(without_timestamps(registry.get("h1", "successor")))
        finally:
            await fleet.close()
    assert rows[0] == rows[1]


async def test_legacy_unsettled_failover_backfills_proof_without_resetting_a_frame(fleet_factory, mock):
    lead = _add_wt_claude(mock)
    fleet = fleet_factory(writes=True, orchestrate=True, default_permission_mode="confined",
                          safety={"write_min_interval_s": 0}, **MANAGED)
    client = fleet.client("h1")
    invoke = lose_start_confirmation(client, "ack")
    try:
        with pytest.raises(InvokeTimeout):
            await lifecycle.session_failover(fleet, "h1", lead, confirm=True, successor_session_id="successor")
        path = registry.registry_path()
        data = json.loads(path.read_text())
        legacy = next(e for e in data["sessions"] if e["session_id"] == "successor")
        del legacy["branch"], legacy["handoff_frame_sha256"]
        legacy["handoff_message_id"] = None
        path.write_text(json.dumps(data))
        client.invoke = invoke
        assert (await lifecycle.session_failover(fleet, "h1", lead, confirm=True))["prompt_sent"]
        sent = registry.get("h1", "successor")
        # A late read-back using an older snapshot cannot erase the frame proof
        # or replace the message ID chosen by the first recovery process.
        record = registry.confirm_failover_start("h1", "successor", message_id="batc-stale", status="active",
                                                start_uncertain=False)
        assert record["handoff_frame_sha256"] == sent["handoff_frame_sha256"]
        assert record["handoff_message_id"] == sent["handoff_message_id"]
        await lifecycle.session_failover(fleet, "h1", lead, confirm=True)
        assert len(handoffs(mock)) == 1
    finally:
        await fleet.close()
