"""A10 review regressions: distinguish unsent refusals from acknowledged and unsettled starts."""

from __future__ import annotations

import asyncio
import copy
import json

import pytest

from bat_agent_connector import confinement, lifecycle, orchestrate, registry, task_bat
from bat_agent_connector.errors import InvokeError, InvokeTimeout, TaskIdentityMismatch
from bat_agent_connector.task_core import TaskCoordinator
from bat_agent_connector.task_journal import Journal
from bat_agent_connector.task_verifier import ObservedVerifier, VerificationSettings
from tests.conftest import adopt, controller_scope
from tests.test_confinement import ACCOUNT, MANAGED, AccountRunner, account_observation
from tests.test_lifecycle import _add_wt_claude
from tests.test_task_service import FakeBAT, submit


class RefuseAtFrame(AccountRunner):
    async def run_account_check(self, host, script, timeout_s=None, *, ssh_alias):
        self.scripts.append(script)
        return json.dumps(account_observation("verified" if len(self.scripts) == 1 else "unknown",
                                              "fixture_identity_unreadable"))


def reviewer_adapter(fleet, mock, journal, monkeypatch):
    lead = _add_wt_claude(mock)
    registry.update("h1", lead, workspace_name="demo-project")
    task = journal.submit(project="p", host="h1", workspace="demo-project", original_words="Review fixture",
                          idempotency_key="review-fixture")
    journal.add_branch(task["task_id"], session_id=lead, provider="claude", role="lead", reason="start")
    task = journal.change(task["task_id"], task["state"], fields={"session_id": lead})
    adapter = task_bat.BatTaskAdapter(fleet, ObservedVerifier(VerificationSettings()), journal)

    async def present(*args, **kwargs):
        return "present"

    monkeypatch.setattr(adapter, "session_presence", present)
    return adapter, task


@pytest.mark.parametrize("path", ["start", "failover", "reviewer"])
@pytest.mark.parametrize("retain", [False, True])
async def test_host_account_refusal_before_frame_releases_start(
        fleet_factory, mock, tmp_path, monkeypatch, path, retain):
    f = fleet_factory(writes=True, orchestrate=True, confinement=ACCOUNT, default_permission_mode="confined",
                      safety={"write_min_interval_s": 0}, **MANAGED)
    f.confinement_runner = RefuseAtFrame()
    journal = Journal(tmp_path / "tasks.db")
    with controller_scope(journal):
        try:
            with pytest.raises(confinement.ConfinementRefused, match="HOST_ACCOUNT_UNVERIFIED") as error:
                if path == "start":
                    await orchestrate.session_start(f, "h1", "demo-project", "claude", confirm=True,
                                                    retain_on_error=retain)
                elif path == "failover":
                    lead = _add_wt_claude(mock)
                    await lifecycle.session_failover(f, "h1", lead, confirm=True)
                else:
                    adapter, task = reviewer_adapter(f, mock, journal, monkeypatch)
                    await adapter.start(task, role="reviewer", agent="codex", session_id="review-refused")
            assert error.value.sent is False
            assert "claude:start-session" not in mock.channels()
            entry = registry.list_entries("h1")[-1]
            assert entry["status"] == "failed" and entry["start_sent"] is False
            assert not entry.get("start_uncertain")
            assert len(f.confinement_runner.scripts) == 2  # admission, then frame; never retried
            if path == "start":
                assert ("worktree:remove" in mock.channels()) is not retain
            if path == "reviewer":
                assert not any(i["channel"] == "claude:get-session-meta" and
                               i["params"]["sessionId"] == "review-refused" for i in mock.invokes)
        finally:
            await f.close()
            journal.close()


@pytest.mark.parametrize("role", ["lead", "reviewer"])
async def test_task_start_refused_before_frame_needs_ted_not_uncertain(tmp_path, role):
    class RefusedBAT(FakeBAT):
        async def start(self, *args, **kwargs):
            raise confinement.ConfinementRefused("HOST_ACCOUNT_UNVERIFIED", "fixture", sent=False)

    journal = Journal(tmp_path / "tasks.db")
    with controller_scope(journal):
        task = submit(journal)
        try:
            result = await TaskCoordinator(journal, RefusedBAT())._start(task, role=role)
            assert result["state"] == "needs_ted"
            command = journal.commands(task["task_id"])[0]
            assert command["kind"] == "start_" + role and command["status"] == "rejected"
            event = next(e for e in journal.events(task["task_id"]) if e["kind"] == "start_rejected")
            assert json.loads(event["body"])["code"] == "HOST_ACCOUNT_UNVERIFIED"
            assert not any('"to": "uncertain"' in e["body"] for e in journal.events(task["task_id"]))
        finally:
            journal.close()


@pytest.mark.parametrize("path", ["start", "failover"])
async def test_general_start_unreadable_meta_records_unknown_and_keeps_session(fleet_factory, mock, path):
    f = fleet_factory(writes=True, orchestrate=True, default_permission_mode="allow_all",
                      safety={"write_min_interval_s": 0}, **MANAGED)
    lead = _add_wt_claude(mock) if path == "failover" else None
    client = f.client("h1")
    invoke = client.invoke

    async def unreadable(channel, params=None, **kwargs):
        if channel == "claude:get-session-meta" and params["sessionId"] != lead:
            raise InvokeError("fixture metadata unavailable")
        return await invoke(channel, params, **kwargs)

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(client, "invoke", unreadable)
    try:
        result = (await orchestrate.session_start(f, "h1", "demo-project", "claude", confirm=True)
                  if path == "start" else await lifecycle.session_failover(f, "h1", lead, confirm=True))
        sid = result.get("session_id") or result["new_session_id"]
        entry = registry.get("h1", sid)
        assert entry["status"] == "active" and entry["confinement"]["verification"]["status"] == "unknown"
        assert entry["confinement"]["level"] == "none"
        assert "worktree:remove" not in mock.channels()
    finally:
        monkeypatch.undo()
        await f.close()


@pytest.mark.parametrize("path", ["start", "failover"])
@pytest.mark.parametrize("confined", [False, True])
async def test_post_start_mismatch_keeps_reservation_and_worktree(fleet_factory, mock, path, confined):
    f = fleet_factory(writes=True, orchestrate=True, default_permission_mode="confined" if confined else "allow_all",
                      safety={"write_min_interval_s": 0}, **MANAGED)
    lead = _add_wt_claude(mock) if path == "failover" else None
    client = f.client("h1")
    invoke = client.invoke

    async def drift(channel, params=None, **kwargs):
        result = await invoke(channel, params, **kwargs)
        if channel == "claude:start-session":
            meta = mock.metas[params["sessionId"]]
            meta.update({"permissionMode": "plan"} if path == "start" else {"codexSandboxMode": "read-only"})
        return result

    client.invoke = drift
    try:
        with pytest.raises(confinement.ConfinementRefused, match="CONFINEMENT_MISMATCH"):
            if path == "start":
                await orchestrate.session_start(f, "h1", "demo-project", "claude", confirm=True)
            else:
                await lifecycle.session_failover(f, "h1", lead, confirm=True)
        entry = registry.list_entries("h1")[-1]
        assert entry["status"] == "uncertain" and entry["error_code"] == "CONFINEMENT_MISMATCH"
        assert entry["confinement"]["verification"]["status"] == "mismatch"
        assert entry.get("worktree_path") and entry["session_id"] in mock.metas
        assert "worktree:remove" not in mock.channels() and "claude:send-message" not in mock.channels()
    finally:
        await f.close()


@pytest.mark.parametrize("agent", ["codex", "claude"])
async def test_reviewer_start_meta_failure_still_activates(fleet_factory, mock, tmp_path, monkeypatch, agent):
    f = fleet_factory(writes=True, orchestrate=True, **MANAGED)
    journal = Journal(tmp_path / "tasks.db")
    with controller_scope(journal):
        adapter, task = reviewer_adapter(f, mock, journal, monkeypatch)
        client = f.client("h1")
        invoke = client.invoke

        async def unreadable(channel, params=None, **kwargs):
            if channel == "claude:get-session-meta" and params["sessionId"] == "review-unreadable":
                raise InvokeError("fixture metadata unavailable")
            return await invoke(channel, params, **kwargs)

        monkeypatch.setattr(client, "invoke", unreadable)
        try:
            sid = await adapter.start(task, role="reviewer", agent=agent, session_id="review-unreadable")
            entry = registry.get("h1", sid)
            assert entry["status"] == "active"
            assert entry["confinement"]["verification"] == {
                "status": "unknown", "reason": "session_unloaded", "observed_options": {}}
            assert mock.channels().count("claude:start-session") == 1
        finally:
            await f.close()
            journal.close()


@pytest.mark.parametrize("agent", ["codex", "claude"])
@pytest.mark.parametrize("lost_ack", [False, True], ids=["acknowledged", "identity_poll"])
async def test_reviewer_permission_mismatch_keeps_task_uncertain_without_prompt(
        fleet_factory, mock, tmp_path, monkeypatch, agent, lost_ack):
    """A10: a readable reviewer mismatch is terminal before any review send intent."""
    fleet = fleet_factory(writes=True, orchestrate=True, safety={"write_min_interval_s": 0}, **MANAGED)
    journal = Journal(tmp_path / "tasks.db")
    with controller_scope(journal):
        adapter, task = reviewer_adapter(fleet, mock, journal, monkeypatch)
        journal.change(task["task_id"], "dispatching")
        task = journal.change(task["task_id"], "verifying", fields={
            "session_id": task["session_id"], "lead_agent": "claude" if agent == "codex" else "codex",
            "review_commit": "a" * 40, "review_tree": "b" * 40})

        async def available(_task):
            return frozenset({agent})

        monkeypatch.setattr(adapter, "available_agents", available)
        client = fleet.client("h1")
        invoke = client.invoke
        requested = {}
        reads = []

        async def wider_options(channel, params=None, **kwargs):
            result = await invoke(channel, params, **kwargs)
            if channel == "claude:start-session":
                requested.update(params["options"])
                mock.metas[params["sessionId"]].update(
                    {"codexSandboxMode": "danger-full-access"} if agent == "codex" else {"permissionMode": "bypassPermissions"})
                if lost_ack:
                    raise InvokeTimeout("fixture lost reviewer start acknowledgement")
            if channel == "claude:get-session-meta" and params["sessionId"] != task["session_id"]:
                reads.append(params["sessionId"])
                # A second read would match; the first mismatch must already stop the start.
                mock.metas[params["sessionId"]].update(requested)
            return result

        monkeypatch.setattr(client, "invoke", wider_options)
        core = TaskCoordinator(journal, adapter)
        try:
            result = await core._start(task, role="reviewer")
            assert result["state"] == "uncertain" and result["reviewer_session_id"] is None
            command = next(c for c in journal.commands(task["task_id"]) if c["kind"] == "start_reviewer")
            sid = command["session_id"]
            row = copy.deepcopy(registry.get("h1", sid))
            assert command["status"] == "uncertain"
            assert row["status"] == "uncertain" and row["error_code"] == "CONFINEMENT_MISMATCH"
            assert row["confinement"]["verification"]["status"] == "mismatch"
            assert row["confinement"]["verification"]["reason"] == "permission_options_changed"
            assert json.loads(command["payload"])["confinement"] == row["confinement"]
            assert reads == [sid] and mock.channels().count("claude:start-session") == 1
            assert "claude:send-message" not in mock.channels()
            assert not any(c["kind"] == "send" for c in journal.commands(task["task_id"]))
            assert not any(b["session_id"] == sid for b in journal.branches(task["task_id"]))
            assert not await adapter.recover_start(result, role="reviewer", session_id=sid)
            assert (await core.tick(task["task_id"]))["state"] == "uncertain"
            assert reads == [sid] and registry.get("h1", sid) == row
            assert "claude:send-message" not in mock.channels()
            # The journal preserves the refusal even if local lookup is lost later.
            registry._write(registry.registry_path(), [e for e in registry.list_entries() if e["session_id"] != sid])
            assert not await adapter.recover_start(result, role="reviewer", session_id=sid)
            assert reads == [sid]
        finally:
            await fleet.close()
            journal.close()


@pytest.mark.parametrize("agent", ["codex", "claude"])
@pytest.mark.parametrize("lost_ack", [False, True], ids=["acknowledged", "identity_poll"])
async def test_reviewer_matching_readback_still_activates(fleet_factory, mock, tmp_path, monkeypatch, agent, lost_ack):
    """A10: checking known mismatches does not change successful reviewer starts."""
    fleet = fleet_factory(writes=True, orchestrate=True, **MANAGED)
    journal = Journal(tmp_path / "tasks.db")
    with controller_scope(journal):
        adapter, task = reviewer_adapter(fleet, mock, journal, monkeypatch)
        client = fleet.client("h1")
        invoke = client.invoke

        async def start_ack(channel, params=None, **kwargs):
            result = await invoke(channel, params, **kwargs)
            if lost_ack and channel == "claude:start-session":
                raise InvokeTimeout("fixture lost reviewer start acknowledgement")
            return result

        monkeypatch.setattr(client, "invoke", start_ack)
        try:
            sid = await adapter.start(task, role="reviewer", agent=agent, session_id="review-matching")
            row = registry.get("h1", sid)
            assert row["status"] == "active" and not row.get("error_code")
            assert row["confinement"]["verification"]["status"] == "options_confirmed"
            assert mock.channels().count("claude:start-session") == 1
        finally:
            await fleet.close()
            journal.close()


@pytest.mark.parametrize("role", ["lead", "reviewer"])
@pytest.mark.parametrize("pending", [False, True], ids=["creation_confirmed", "creation_pending"])
@pytest.mark.parametrize("missing_lookup", [False, True], ids=["reserved", "headless"])
async def test_task_headless_start_mismatch_never_promotes(
        fleet_factory, mock, tmp_path, role, pending, missing_lookup):
    """A10: start recovery validates requested options before restoring an active lookup."""
    fleet = fleet_factory(writes=True, orchestrate=True, default_permission_mode="allow_all",
                          safety={"write_min_interval_s": 0}, **MANAGED)
    journal = Journal(tmp_path / "tasks.db")
    with controller_scope(journal):
        task = journal.submit(project="p", host="h1", workspace="demo-project", original_words="Review fixture",
                              idempotency_key="headless-permissions", lead_agent="claude")
        adapter = task_bat.BatTaskAdapter(fleet, ObservedVerifier(VerificationSettings()), journal)
        lead, reviewer = "permission-lead", "permission-reviewer"
        try:
            lead_command, _ = journal.command(task["task_id"], "start_lead", lead,
                                              {"role": "lead", "agent": "claude"}, "lead-start")
            await adapter.start(task, role="lead", agent="claude", session_id=lead)
            journal.command_status(lead_command["command_id"], "settled")
            journal.add_branch(task["task_id"], session_id=lead, provider="claude", role="lead", reason="start")
            journal.change(task["task_id"], "dispatching")
            task = journal.change(task["task_id"], "accepted", fields={"session_id": lead, "base_branch": None})
            sid = lead
            if role == "reviewer":
                journal.command(task["task_id"], "start_reviewer", reviewer,
                                {"role": "reviewer", "agent": "codex"}, "reviewer-start")
                await adapter.start(task, role="reviewer", agent="codex", session_id=reviewer)
                journal.add_branch(task["task_id"], session_id=reviewer, provider="codex", role="reviewer", reason="start")
                task = journal.change(task["task_id"], "verifying", fields={"reviewer_session_id": reviewer})
                sid = reviewer
            row = registry.get("h1", sid)
            record = (confinement.snapshot("claude" if role == "lead" else "codex", row["confinement"]["options"], task=True)
                      if pending else row["confinement"])
            registry.update("h1", sid, status="starting", confinement=record)
            confinement.record_task_start(journal, task["task_id"], sid, {"confinement": record})
            if missing_lookup:
                registry._write(registry.registry_path(), [e for e in registry.list_entries() if e["session_id"] != sid])
            mock.metas[sid].update({"permissionMode": "plan"} if role == "lead" else {"codexSandboxMode": "danger-full-access"})
            assert not await adapter.recover_start(task, role=role, session_id=sid)
            saved = registry.get("h1", sid)
            if missing_lookup:
                assert saved is None
            else:
                assert saved["status"] == "uncertain" and saved["error_code"] == "CONFINEMENT_MISMATCH"
                assert saved["confinement"]["verification"]["status"] == ("mismatch" if pending else "options_confirmed")
                if not pending:
                    assert saved["confinement"] == record  # live drift cannot rewrite confirmed creation evidence
            if pending:
                command = next(c for c in journal.commands(task["task_id"]) if c["session_id"] == sid)
                assert json.loads(command["payload"])["confinement"]["verification"]["status"] == "mismatch"
                reads = mock.channels().count("claude:get-session-meta")
                assert not await adapter.recover_start(task, role=role, session_id=sid)
                assert mock.channels().count("claude:get-session-meta") == reads
            assert (await TaskCoordinator(journal, adapter)._send(task, sid, "review fixture", role + ":initial"))["state"] == "uncertain"
            assert "claude:send-message" not in mock.channels()
            assert not any(c["kind"] == "send" for c in journal.commands(task["task_id"]))
        finally:
            await fleet.close()
            journal.close()


@pytest.mark.parametrize("agent", ["claude", "codex"])
async def test_task_lead_start_mismatch_never_sends_initial_prompt(fleet_factory, mock, tmp_path, monkeypatch, agent):
    """A10: the existing lead start gate keeps mismatches uncertain before its first prompt."""
    fleet = fleet_factory(writes=True, orchestrate=True, default_permission_mode="allow_all",
                          safety={"write_min_interval_s": 0}, **MANAGED)
    journal = Journal(tmp_path / "tasks.db")
    with controller_scope(journal):
        task = journal.submit(project="p", host="h1", workspace="demo-project", original_words="Lead fixture",
                              idempotency_key="lead-permissions", lead_agent=agent)
        adapter = task_bat.BatTaskAdapter(fleet, ObservedVerifier(VerificationSettings()), journal)

        async def available(_task):
            return frozenset({agent})

        monkeypatch.setattr(adapter, "available_agents", available)
        invoke = fleet.client("h1").invoke

        async def drift(channel, params=None, **kwargs):
            result = await invoke(channel, params, **kwargs)
            if channel == "claude:start-session":
                mock.metas[params["sessionId"]].update(
                    {"permissionMode": "plan"} if agent == "claude" else {"codexSandboxMode": "read-only"})
            return result

        monkeypatch.setattr(fleet.client("h1"), "invoke", drift)
        try:
            result = await TaskCoordinator(journal, adapter)._start(task, role="lead")
            assert result["state"] == "uncertain" and result["session_id"] is None
            command = next(c for c in journal.commands(task["task_id"]) if c["kind"] == "start_lead")
            row = registry.get("h1", command["session_id"])
            assert command["status"] == "uncertain"
            assert row["status"] == "uncertain" and row["error_code"] == "CONFINEMENT_MISMATCH"
            assert row["confinement"]["verification"]["status"] == "mismatch"
            assert mock.channels().count("claude:start-session") == 1
            assert "claude:send-message" not in mock.channels()
            reads = mock.channels().count("claude:get-session-meta")
            assert not await adapter.recover_start(result, role="lead", session_id=row["session_id"])
            assert mock.channels().count("claude:get-session-meta") == reads
        finally:
            await fleet.close()
            journal.close()


@pytest.mark.parametrize("phase", [1, 2, 3], ids=["post_start", "before_handoff", "at_frame"])
async def test_task_failover_options_mismatch_blocks_first_handoff(fleet_factory, mock, tmp_path, phase):
    """A10: Task successor options are checked at start and again by its handoff guards."""
    fleet = fleet_factory(writes=True, orchestrate=True, default_permission_mode="allow_all",
                          safety={"write_min_interval_s": 0}, **MANAGED)
    journal = Journal(tmp_path / "tasks.db")
    with controller_scope(journal):
        lead = _add_wt_claude(mock)
        task = journal.submit(project="p", host="h1", workspace="demo-project", original_words="Continue fixture",
                              idempotency_key="successor-permissions", lead_agent="claude")
        journal.change(task["task_id"], "dispatching")
        journal.change(task["task_id"], "accepted", fields={"session_id": lead})
        journal.add_branch(task["task_id"], session_id=lead, provider="claude", role="lead", reason="start")
        journal.change(task["task_id"], "running")
        task = journal.change(task["task_id"], "quota_limited")
        _, handoff = journal.reserve_failover(task["task_id"], lead, "permission-successor")
        adapter = task_bat.BatTaskAdapter(fleet, journal=journal)
        TaskCoordinator(journal, adapter)
        registry.update('h1', lead, task_id=task['task_id'], role='lead')
        reads = 0

        def metadata(params):
            nonlocal reads
            sid = params["sessionId"]
            if sid == "permission-successor":
                reads += 1
                if reads >= phase:
                    mock.metas[sid]["codexSandboxMode"] = "read-only"
            return copy.deepcopy(mock.metas.get(sid))

        mock.handlers["claude:get-session-meta"] = metadata
        try:
            with pytest.raises(confinement.ConfinementRefused if phase == 1 else TaskIdentityMismatch):
                await adapter.failover(task, lead, "permission-successor", handoff_message_id=handoff["message_id"],
                                       handoff_command_id=handoff["command_id"])
            assert reads == phase
            assert "claude:send-message" not in mock.channels()
            row = registry.get("h1", "permission-successor")
            assert row and row["handoff_frame_sha256"] is None
            if phase == 1:
                assert row["status"] == "uncertain" and row["error_code"] == "CONFINEMENT_MISMATCH"
                assert row["confinement"]["verification"]["status"] == "mismatch"
            else:
                assert row["confinement"]["verification"]["status"] == "options_confirmed"
        finally:
            await fleet.close()
            journal.close()


@pytest.mark.parametrize("observed", ["different", "missing"])
async def test_acknowledged_start_checks_reserved_cwd_before_promotion(fleet_factory, mock, observed):
    """A10: an ACK does not justify attaching a readable session in a different folder."""
    fleet = fleet_factory(writes=True, orchestrate=True, default_permission_mode="confined", **MANAGED)
    client = fleet.client("h1")
    invoke = client.invoke

    async def wrong_folder(channel, params=None, **kwargs):
        result = await invoke(channel, params, **kwargs)
        if channel == "claude:start-session":
            meta = mock.metas[params["sessionId"]]
            if observed == "different":
                meta["cwd"] = "/srv/another-checkout"
            else:
                del meta["cwd"]
        return result

    client.invoke = wrong_folder
    try:
        code = "START_SESSION_MISMATCH" if observed == "different" else "CONFINEMENT_START_UNSETTLED"
        with pytest.raises(confinement.ConfinementRefused, match=code):
            await orchestrate.session_start(fleet, "h1", "demo-project", "claude", confirm=True, prompt="go")
        row = registry.list_entries("h1")[-1]
        assert row["status"] == "uncertain" and row["error_code"] == code
        assert row["cwd"] == row["worktree_path"] and row["cwd"] != "/srv/another-checkout"
        assert "worktree:remove" not in mock.channels() and "claude:send-message" not in mock.channels()
    finally:
        await fleet.close()


@pytest.mark.parametrize("observed", ["different", "missing"])
async def test_reviewer_readback_checks_reserved_cwd_without_retrying_start(fleet_factory, mock, tmp_path, monkeypatch, observed):
    """A10: reviewer read-back cannot convert a foreign session into the task's reviewer."""
    fleet = fleet_factory(writes=True, orchestrate=True, **MANAGED)
    journal = Journal(tmp_path / "tasks.db")
    with controller_scope(journal):
        adapter, task = reviewer_adapter(fleet, mock, journal, monkeypatch)
        client = fleet.client("h1")
        invoke = client.invoke

        async def lost_ack(channel, params=None, **kwargs):
            result = await invoke(channel, params, **kwargs)
            if channel == "claude:start-session":
                meta = mock.metas[params["sessionId"]]
                if observed == "different":
                    meta["cwd"] = "/srv/another-checkout"
                else:
                    del meta["cwd"]
                raise InvokeTimeout("fixture lost reviewer start acknowledgement")
            return result

        client.invoke = lost_ack
        try:
            code = "START_SESSION_MISMATCH" if observed == "different" else "CONFINEMENT_START_UNSETTLED"
            with pytest.raises(confinement.ConfinementRefused, match=code):
                await adapter.start(task, role="reviewer", agent="codex", session_id="wrong-folder-reviewer")
            row = registry.get("h1", "wrong-folder-reviewer")
            assert row["status"] == "uncertain" and row["error_code"] == code
            assert mock.channels().count("claude:start-session") == 1
            assert "claude:send-message" not in mock.channels()
            if observed == "different":
                reads = mock.channels().count("claude:get-session-meta")
                assert not await adapter.recover_start(task, role="reviewer", session_id=row["session_id"])
                assert mock.channels().count("claude:get-session-meta") == reads
        finally:
            await fleet.close()
            journal.close()


async def test_legacy_codex_predecessor_uses_its_recorded_agent(fleet_factory):
    f = fleet_factory(writes=True, default_permission_mode="allow_all", **MANAGED)
    adopt("legacy-codex", agent_preset="codex-agent", write_scope="confined",
          agent_params={"sandboxMode": "read-only", "approvalPolicy": "never"})
    try:
        options, scope, _ = await confinement.start_decision(
            f, "h1", "codex", confined=True, predecessor=registry.get("h1", "legacy-codex"))
        assert options == {"codexSandboxMode": "read-only", "codexApprovalPolicy": "never"}
        assert scope == "confined"
    finally:
        await f.close()


async def test_post_start_cancellation_keeps_acknowledged_reservation(fleet_factory, mock):
    f = fleet_factory(writes=True, orchestrate=True, **MANAGED)
    invoke = f.client("h1").invoke

    async def cancelled(channel, params=None, **kwargs):
        if channel == "claude:get-session-meta":
            raise asyncio.CancelledError()
        return await invoke(channel, params, **kwargs)

    f.client("h1").invoke = cancelled
    try:
        with pytest.raises(asyncio.CancelledError):
            await orchestrate.session_start(f, "h1", "demo-project", "claude", confirm=True)
        assert registry.list_entries("h1")[0]["status"] == "uncertain"
        assert "worktree:remove" not in mock.channels()
    finally:
        await f.close()


@pytest.mark.parametrize("outcome", ["confirmed", "mismatch", "unreadable", "null"])
async def test_confined_failover_unsettled_successor_reads_back_before_new_attempt(fleet_factory, mock, outcome):
    f = fleet_factory(writes=True, orchestrate=True, default_permission_mode="confined",
                      safety={"write_min_interval_s": 0}, **MANAGED)
    lead = _add_wt_claude(mock)
    options = confinement.CONFINED_OPTIONS["codex"]
    previous = {k: v for k, v in registry.get("h1", lead).items()
                if k not in {"controller_authority", "controller_incarnation"}}
    registry.reserve("h1", {**previous, "session_id": "unsettled-successor", "failover_of": lead,
                            "agent_preset": "codex-agent-worktree", "write_scope": "confined",
                            "confinement": confinement.snapshot("codex", options), "start_uncertain": True}, 4)
    if outcome in {"confirmed", "mismatch"}:
        mock.metas["unsettled-successor"] = {"cwd": previous["cwd"], **options}
        if outcome == "mismatch":
            mock.metas["unsettled-successor"]["codexSandboxMode"] = "danger-full-access"
    elif outcome == "unreadable":
        invoke = f.client("h1").invoke

        async def unreadable(channel, params=None, **kwargs):
            if channel == "claude:get-session-meta" and params["sessionId"] == "unsettled-successor":
                raise InvokeError("fixture metadata unavailable")
            return await invoke(channel, params, **kwargs)

        f.client("h1").invoke = unreadable
    try:
        if outcome == "confirmed":
            result = await lifecycle.session_failover(f, "h1", lead, confirm=True)
            assert result["new_session_id"] == "unsettled-successor" and result["skipped"]
            entry = registry.get("h1", "unsettled-successor")
            assert entry["status"] == "active" and not entry["start_uncertain"]
            assert entry["confinement"]["verification"]["status"] == "options_confirmed"
        else:
            code = "CONFINEMENT_MISMATCH" if outcome == "mismatch" else "CONFINEMENT_START_UNSETTLED"
            with pytest.raises(confinement.ConfinementRefused, match=code):
                await lifecycle.session_failover(f, "h1", lead, confirm=True)
            assert registry.get("h1", "unsettled-successor")["start_uncertain"]
        assert "claude:start-session" not in mock.channels()
    finally:
        await f.close()


class PauseAtFrame(AccountRunner):
    """Synthetic account check blocks only the frame-boundary verification."""
    def __init__(self):
        super().__init__()
        self.entered, self.release = asyncio.Event(), asyncio.Event()
        self.cancelled = None

    async def run_account_check(self, host, script, timeout_s=None, *, ssh_alias):
        self.scripts.append(script)
        if len(self.scripts) == 2:
            self.entered.set()
            try:
                await self.release.wait()
            except asyncio.CancelledError as exc:
                self.cancelled = exc
                raise
        return json.dumps(account_observation("verified", "fixture_hardened_account"))


@pytest.mark.parametrize("path", ["start", "failover", "task_lead", "reviewer"])
@pytest.mark.parametrize("unsent_status", ["failed", "starting"], ids=["cancelled", "crash_without_unwind"])
async def test_start_preframe_cancellation_is_unsent_and_reuses_reserved_id(
        fleet_factory, mock, tmp_path, monkeypatch, path, unsent_status):
    """A10: cancellation propagates, with no start or rollback frame; same-ID recovery starts once."""
    fleet = fleet_factory(writes=True, orchestrate=True, confinement=ACCOUNT, default_permission_mode="confined",
                          safety={"write_min_interval_s": 0}, **MANAGED)
    pause = fleet.confinement_runner = PauseAtFrame()
    journal = Journal(tmp_path / 'cancel.db')
    with controller_scope(journal):
        sid = 'cancel-start'
        adapter, task, lead = None, None, None
        if path == 'failover':
            lead = _add_wt_claude(mock)
        elif path in {'reviewer', 'task_lead'}:
            adapter, task = reviewer_adapter(fleet, mock, journal, monkeypatch)
            role = 'reviewer' if path == 'reviewer' else 'lead'
            journal.command(task['task_id'], 'start_' + role, sid, {'agent': 'codex', 'role': role}, 'cancel-start')

        propagated = []

        async def start():
            try:
                if path == 'start':
                    return await orchestrate.session_start(fleet, 'h1', 'demo-project', 'claude', confirm=True, session_id=sid)
                if path == 'failover':
                    return await lifecycle.session_failover(fleet, 'h1', lead, confirm=True)
                return await adapter.start(task, role=role, agent='codex', session_id=sid)
            except asyncio.CancelledError as exc:
                propagated.append(exc)
                raise

        pending = asyncio.create_task(start())
        try:
            entered = asyncio.create_task(pause.entered.wait())
            try:
                done, _ = await asyncio.wait({entered, pending}, timeout=10,
                                             return_when=asyncio.FIRST_COMPLETED)
                if pending in done:
                    result = await pending  # Surface an early refusal with its original traceback.
                    pytest.fail(f"start completed before the account frame: {result!r}")
                assert entered in done, (
                    f"start did not reach account frame: checks={len(pause.scripts)}, "
                    f"channels={mock.channels()}, stack="
                    f"{[(frame.f_code.co_name, frame.f_lineno) for frame in pending.get_stack()]}"
                )
            finally:
                if not entered.done():
                    entered.cancel()
                    with pytest.raises(asyncio.CancelledError):
                        await entered
            row = registry.list_entries('h1')[-1]
            reserved_sid = row['session_id']
            assert row['start_sent'] is False
            pending.cancel('fixture pre-frame cancellation')
            with pytest.raises(asyncio.CancelledError):
                await pending
            # Python 3.10 drops cancel messages at Task.__await__; test the product
            # stack's original exception directly, before that interpreter boundary.
            assert propagated == [pause.cancelled]
            assert propagated[0].args == ('fixture pre-frame cancellation',)
            row = registry.get('h1', reserved_sid)
            assert row['start_sent'] is False and row['status'] == 'failed'
            assert 'claude:start-session' not in mock.channels()
            assert 'worktree:remove' not in mock.channels()
            assert not row.get('start_uncertain')
            registry.update("h1", reserved_sid, status=unsent_status)
            pause.release.set()
            if adapter:
                assert await adapter.recover_start(task, role=role, session_id=reserved_sid)
            else:
                result = await start()
                assert (result.get('session_id') or result['new_session_id']) == reserved_sid
            assert registry.get('h1', reserved_sid)['status'] == 'active'
            assert registry.get('h1', reserved_sid)['worktree_path'] == row['worktree_path']
            assert sum(e["session_id"] == reserved_sid for e in registry.list_entries("h1")) == 1
            starts = [i for i in mock.invokes if i['channel'] == 'claude:start-session']
            assert len(starts) == 1 and starts[0]['params']['sessionId'] == reserved_sid
        finally:
            if not pending.done():
                pending.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await pending
            await fleet.close()
            journal.close()


@pytest.mark.parametrize('path', ['start', 'failover', 'reviewer'])
async def test_start_postframe_cancellation_keeps_uncertain_and_does_not_resend(
        fleet_factory, mock, tmp_path, monkeypatch, path):
    """A10: cancel after the actual frame; recovery reads BAT, never re-sends the start."""
    fleet = fleet_factory(writes=True, orchestrate=True, confinement=ACCOUNT, default_permission_mode="confined",
                          safety={'write_min_interval_s': 0}, **MANAGED)
    fleet.confinement_runner = AccountRunner()
    journal = Journal(tmp_path / 'after.db')
    with controller_scope(journal):
        client = fleet.client('h1')
        invoke = client.invoke
        received = asyncio.Event()
        adapter, task, lead = None, None, None
        sid = 'cancel-after-frame'
        if path == 'failover':
            lead = _add_wt_claude(mock)
        if path == 'reviewer':
            adapter, task = reviewer_adapter(fleet, mock, journal, monkeypatch)
            journal.command(task['task_id'], 'start_reviewer', sid, {'agent': 'codex', 'role': 'reviewer'}, 'after-frame')

        async def lose_ack(channel, params=None, **kwargs):
            result = await invoke(channel, params, **kwargs)
            if channel == 'claude:start-session':
                received.set()
                await asyncio.Event().wait()
            return result

        monkeypatch.setattr(client, 'invoke', lose_ack)

        async def start():
            if path == 'start':
                return await orchestrate.session_start(fleet, 'h1', 'demo-project', 'claude', confirm=True,
                                                      session_id=sid, write_scope='confined')
            if path == 'failover':
                return await lifecycle.session_failover(fleet, 'h1', lead, confirm=True)
            return await adapter.start(task, role='reviewer', agent='codex', session_id=sid)

        pending = asyncio.create_task(start())
        try:
            await asyncio.wait_for(received.wait(), 10)
            row = registry.list_entries('h1')[-1]
            sid = row['session_id']
            assert row['start_sent'] is True
            pending.cancel()
            with pytest.raises(asyncio.CancelledError):
                await pending
            row = registry.get('h1', sid)
            assert row['start_sent'] is True and row['status'] in {'starting', 'uncertain'}
            assert 'worktree:remove' not in mock.channels()
            monkeypatch.setattr(client, 'invoke', invoke)
            if path == 'reviewer':
                assert await adapter.recover_start(task, role='reviewer', session_id=sid)
            elif path == 'failover':
                assert (await start())['new_session_id'] == sid
            else:
                with pytest.raises(confinement.ConfinementRefused, match='CONFINEMENT_START_UNSETTLED'):
                    await start()
            assert mock.channels().count('claude:start-session') == 1
        finally:
            if not pending.done():
                pending.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await pending
            await fleet.close()
            journal.close()


async def test_task_failover_preframe_cancellation_recovers_reserved_start_and_handoff(
        fleet_factory, mock, tmp_path):
    """A10: Task Service reconciliation restarts an unsent successor with its original handoff binding."""
    import hashlib

    fleet = fleet_factory(writes=True, orchestrate=True, confinement=ACCOUNT,
                          safety={'write_min_interval_s': 0}, **MANAGED)
    pause = fleet.confinement_runner = PauseAtFrame()
    journal = Journal(tmp_path / 'failover-cancel.db')
    with controller_scope(journal):
        lead = _add_wt_claude(mock)
        registry.update('h1', lead, write_scope='confined',
                        confinement=confinement.snapshot('claude', {'permissionMode': 'default'}))
        task = journal.submit(project='p', host='h1', workspace='demo-project', original_words='Continue fixture',
                              idempotency_key='failover-cancel', lead_agent='claude')
        journal.change(task['task_id'], 'dispatching')
        journal.change(task['task_id'], 'accepted', fields={'session_id': lead})
        journal.add_branch(task['task_id'], session_id=lead, provider='claude', role='lead', reason='start')
        journal.change(task['task_id'], 'running')
        task = journal.change(task['task_id'], 'quota_limited')
        command, handoff = journal.reserve_failover(task['task_id'], lead, 'cancel-task-successor')
        adapter = task_bat.BatTaskAdapter(fleet, journal=journal)
        TaskCoordinator(journal, adapter)
        registry.update('h1', lead, task_id=task['task_id'], role='lead')
        args = {'handoff_message_id': handoff['message_id'], 'handoff_command_id': handoff['command_id']}
        pending = asyncio.create_task(adapter.failover(task, lead, 'cancel-task-successor', **args))
        try:
            await asyncio.wait_for(pause.entered.wait(), 10)
            pending.cancel()
            with pytest.raises(asyncio.CancelledError):
                await pending
            row = registry.get('h1', 'cancel-task-successor')
            assert row['status'] == 'failed' and row['start_sent'] is False
            assert not any(i['channel'] in {'claude:start-session', 'claude:send-message'} for i in mock.invokes)
            pause.release.set()
            result = await TaskCoordinator(journal, adapter)._reconcile_command(task, command)
            assert result['state'] == 'uncertain' and result['session_id'] == 'cancel-task-successor'
            assert journal.command_get(command['command_id'])['status'] == 'settled'
            assert await adapter._verified_failover_successor(task, 'cancel-task-successor', **args) is True
            starts = [i for i in mock.invokes if i['channel'] == 'claude:start-session']
            sends = [i for i in mock.invokes if i['channel'] == 'claude:send-message']
            assert len(starts) == len(sends) == 1
            digest = json.loads(journal.command_get(handoff['command_id'])['payload'])['prompt_sha256']
            assert hashlib.sha256(sends[0]['params']['prompt'].encode()).hexdigest() == digest
            assert sends[0]['params']['clientMessageId'] == handoff['message_id']
            assert await adapter.recover_failover(task, successor_id='cancel-task-successor', **args)
            assert mock.channels().count('claude:start-session') == mock.channels().count('claude:send-message') == 1
        finally:
            if not pending.done():
                pending.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await pending
            await fleet.close()
            journal.close()


@pytest.mark.parametrize('boundary', ['git_log', 'connect', 'semaphore'])
async def test_start_cancellation_at_earlier_await_is_also_unsent(fleet_factory, mock, monkeypatch, boundary):
    """A10: all awaits after reservation, not just the account check, keep a pre-transport cancel unsent."""
    fleet = fleet_factory(writes=True, orchestrate=True, **MANAGED)
    client = fleet.client('h1')
    ready = asyncio.Event()
    invoke, connect = client.invoke, client.connect

    async def block(channel, params=None, **kwargs):
        if channel == ('git:log' if boundary == 'git_log' else 'claude:start-session'):
            if boundary == 'semaphore':
                await client._sem.acquire()
                ready.set()
            elif boundary == 'connect':
                async def delayed_connect():
                    ready.set()
                    await asyncio.Event().wait()
                monkeypatch.setattr(client, 'connect', delayed_connect)
            else:
                ready.set()
                await asyncio.Event().wait()
        return await invoke(channel, params, **kwargs)

    # Occupy all permits at the actual pre-frame semaphore boundary.
    if boundary == 'semaphore':
        for _ in range(client._sem._value - 1):
            await client._sem.acquire()
    monkeypatch.setattr(client, 'invoke', block)
    pending = asyncio.create_task(orchestrate.session_start(fleet, 'h1', 'demo-project', 'claude', confirm=True,
                                                          session_id='earlier-cancel'))
    try:
        await asyncio.wait_for(ready.wait(), 10)
        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pending
        row = registry.get('h1', 'earlier-cancel')
        assert row['status'] == 'failed' and row['start_sent'] is False
        assert row['worktree_path'] and 'claude:start-session' not in mock.channels()
        assert 'worktree:remove' not in mock.channels()
    finally:
        monkeypatch.setattr(client, 'connect', connect)
        await fleet.close()


@pytest.mark.parametrize('role', ['lead', 'reviewer'])
async def test_task_preparation_cancellation_recovers_from_unsent_command_before_registry(
        fleet_factory, mock, tmp_path, monkeypatch, role):
    """A10: start command evidence covers preparation awaits before a registry reservation exists."""
    fleet = fleet_factory(writes=True, orchestrate=True, safety={'write_min_interval_s': 0}, **MANAGED)
    journal = Journal(tmp_path / 'prepare-cancel.db')
    with controller_scope(journal):
        adapter, task = reviewer_adapter(fleet, mock, journal, monkeypatch)
        entered, release = asyncio.Event(), asyncio.Event()

        async def available(_task):
            return frozenset({'codex'})

        monkeypatch.setattr(adapter, 'available_agents', available)
        invoke = fleet.client('h1').invoke
        present = adapter.session_presence

        async def blocked_workspace(channel, params=None, **kwargs):
            if channel == 'workspace:load':
                entered.set()
                await release.wait()
            return await invoke(channel, params, **kwargs)

        async def blocked_lead(*args, **kwargs):
            entered.set()
            await release.wait()
            return 'present'

        if role == 'lead':
            monkeypatch.setattr(fleet.client('h1'), 'invoke', blocked_workspace)
        else:
            monkeypatch.setattr(adapter, 'session_presence', blocked_lead)
        core = TaskCoordinator(journal, adapter)
        pending = asyncio.create_task(core._start(task, role=role))
        try:
            await asyncio.wait_for(entered.wait(), 10)
            pending.cancel()
            with pytest.raises(asyncio.CancelledError):
                await pending
            command = next(c for c in journal.commands(task['task_id']) if c['kind'] == 'start_' + role)
            sid = command['session_id']
            assert registry.get('h1', sid) is None and json.loads(command['payload'])['start_sent'] is False
            assert 'claude:start-session' not in mock.channels()
            release.set()
            monkeypatch.setattr(fleet.client('h1'), 'invoke', invoke)
            monkeypatch.setattr(adapter, 'session_presence', present)
            result = await core._reconcile_command(task, command)
            assert result['state'] == ('accepted' if role == 'lead' else 'verifying')
            assert registry.get('h1', sid)['status'] == 'active'
            assert json.loads(journal.command_get(command['command_id'])['payload'])['start_sent'] is True
            assert mock.channels().count('claude:start-session') == 1
        finally:
            if not pending.done():
                pending.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await pending
            await fleet.close()
            journal.close()


async def test_task_lead_lost_ack_keeps_sent_evidence_until_readback_recovery(fleet_factory, mock, tmp_path, monkeypatch):
    """A10: an ordinary retry cannot replace a possibly-sent lead reservation with false unsent evidence."""
    fleet = fleet_factory(writes=True, orchestrate=True, **MANAGED)
    journal = Journal(tmp_path / 'sent-retry.db')
    with controller_scope(journal):
        task = journal.submit(project='p', host='h1', workspace='demo-project', original_words='Continue fixture',
                              idempotency_key='lead-lost-ack')
        adapter = task_bat.BatTaskAdapter(fleet, journal=journal)
        sid = 'lead-lost-ack'
        command, _ = journal.command(task['task_id'], 'start_lead', sid,
                                      {'agent': 'codex', 'role': 'lead', 'start_sent': False}, 'lead-lost-ack')
        invoke = fleet.client('h1').invoke

        async def lose_ack(channel, params=None, **kwargs):
            result = await invoke(channel, params, **kwargs)
            if channel == 'claude:start-session':
                raise InvokeTimeout('fixture lead acknowledgement lost after transport')
            return result

        monkeypatch.setattr(fleet.client('h1'), 'invoke', lose_ack)
        try:
            with pytest.raises(confinement.ConfinementRefused, match='CONFINEMENT_START_UNSETTLED'):
                await adapter.start(task, role='lead', agent='codex', session_id=sid)
            row = registry.get('h1', sid)
            assert row['start_sent'] is True and row['status'] == 'uncertain'
            assert json.loads(journal.command_get(command['command_id'])['payload'])['start_sent'] is True
            assert len(registry.list_entries('h1')) == 1
            assert mock.channels().count('claude:start-session') == 1
            assert 'worktree:remove' not in mock.channels() and 'claude:send-message' not in mock.channels()
            monkeypatch.setattr(fleet.client('h1'), 'invoke', invoke)
            assert await adapter.recover_start(task, role='lead', session_id=sid)
            assert registry.get('h1', sid)['status'] == 'active'
            assert mock.channels().count('claude:start-session') == 1
        finally:
            await fleet.close()
            journal.close()
