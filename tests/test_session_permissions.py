"""Per-frame permission receipts through the real central owner and MockBat."""
from __future__ import annotations

import json

import pytest

from bat_agent_connector import api_auth, registry
from tests import test_api_v1 as api
from tests import test_operations_unification as task_tests
from tests.conftest import adopt
from tests.operation_helpers import settle_operations

daemon, served, owned = api.daemon, api.served, task_tests.owned
PERSON = api_auth.Principal("operator-dashboard", frozenset({"operate", "observe"}))
CLAUDE, CODEX = api.MANUAL, "sess-codex-0002"


def intent(sid, *, mode="default", key="permissions-one"):
    return {"action": "session.permissions", "target": {"host": "h1", "session_id": sid},
            "params": {"mode": mode}, "idempotency_key": key}


@pytest.mark.parametrize("sid,agent,channels", [
    (CLAUDE, "claude-code", ["claude:set-permission-mode"]),
    (CODEX, "codex-agent", ["claude:set-codex-sandbox-mode", "claude:set-codex-approval-policy"]),
])
async def test_each_permission_frame_has_fixed_intent_and_true_ack(daemon, mock, sid, agent, channels):
    adopt(sid, agent_preset=agent)
    op, _ = daemon.ops.create(PERSON, **intent(sid))
    await settle_operations(daemon.ops)
    out = daemon.ops.get(op["operation_id"])
    assert out["status"] == "succeeded", out
    assert [c["channel"] for c in out["result"]["calls"]] == channels
    assert [c["channel"] for c in api.write_frames(mock)] == channels
    for step in out["steps"]:
        if step["name"] in {"permissions.mode", "permissions.sandbox", "permissions.approval"}:
            row = daemon.journal.db.execute("SELECT request,response FROM operation_steps WHERE operation_id=? AND name=?",
                                           (op["operation_id"], step["name"])).fetchone()
            request, response = json.loads(row["request"]), json.loads(row["response"])
            assert request["params"]["sessionId"] == sid
            assert response == {"channel": request["channel"], "result": True}
    assert registry.get("h1", sid)["permission_operation_id"] == op["operation_id"]
    assert "permission_raise_pending" not in (registry.get("h1", sid) or {}) or registry.get("h1", sid)["permission_raise_pending"] is None
    replay, fresh = daemon.ops.create(PERSON, **intent(sid))
    assert not fresh and replay["operation_id"] == op["operation_id"]
    assert len(api.write_frames(mock)) == len(channels)
    assert json.loads(daemon.journal.db.execute("SELECT params FROM operations WHERE operation_id=?", (op["operation_id"],)).fetchone()[0]) == {"mode": "default"}
    await daemon.fleet.close()


@pytest.mark.parametrize("sid,agent", [(CLAUDE, "claude-code"), (CODEX, "codex-agent")])
async def test_task_permissions_keep_single_original_command(owned, mock, sid, agent):
    d, tid = owned
    if sid == CLAUDE:
        registry.update("h1", sid, agent_preset=agent)
    else:
        adopt(sid, task_id=tid, role="lead", agent_preset=agent)
    d.journal.db.execute("UPDATE tasks SET session_id=? WHERE task_id=?", (sid, tid))
    op, _ = d.ops.create(PERSON, **intent(sid))
    await settle_operations(d.ops)
    out = d.ops.get(op["operation_id"])
    assert out["status"] == "succeeded", out
    commands = d.journal.commands(tid)
    assert len(commands) == 1 and commands[0]["kind"] == "permissions" and commands[0]["status"] == "settled"
    assert json.loads(commands[0]["payload"])["operation_id"] == op["operation_id"]
    assert out["external_refs"]["command_id"] == commands[0]["command_id"]
    assert len(out["external_refs"]["permission_frames"]) == (1 if sid == CLAUDE else 2)


@pytest.mark.parametrize("reply", [False, None, {}, {"ok": True}])
async def test_non_true_ack_remains_uncertain_even_matching_meta(daemon, mock, monkeypatch, reply):
    adopt(CODEX, agent_preset="codex-agent")
    client = daemon.fleet.client("h1")
    original = client._roundtrip
    async def respond(frame, timeout, **kwargs):
        result = await original(frame, timeout, **kwargs)
        if frame["channel"] == "claude:set-codex-sandbox-mode":
            return {**result, "result": reply}
        return result
    monkeypatch.setattr(client, "_roundtrip", respond)
    op, _ = daemon.ops.create(PERSON, **intent(CODEX))
    await settle_operations(daemon.ops)
    out = daemon.ops.get(op["operation_id"])
    assert out["status"] == "uncertain" and out["external_refs"]["permission_frames"][0]["acknowledged"] is None
    assert mock.metas[CODEX]["codexSandboxMode"] == "workspace-write"
    before = list(api.write_frames(mock))
    daemon.ops.db.execute("UPDATE operations SET next_run_at=0 WHERE operation_id=?", (op["operation_id"],))
    await settle_operations(daemon.ops)
    assert daemon.ops.get(op["operation_id"])["status"] == "uncertain" and api.write_frames(mock) == before
    assert registry.get("h1", CODEX).get("permission_operation_id") is None
    await daemon.fleet.close()


@pytest.mark.parametrize("failure", ["lost", "invoke_error"])
async def test_codex_second_sent_error_keeps_first_ack_and_original_task(owned, mock, monkeypatch, failure):
    from bat_agent_connector.errors import ConnectionLost
    d, tid = owned
    adopt(CODEX, task_id=tid, role="lead", agent_preset="codex-agent")
    d.journal.db.execute("UPDATE tasks SET session_id=? WHERE task_id=?", (CODEX, tid))
    client, original = d.fleet.client("h1"), d.fleet.client("h1")._roundtrip
    async def broken(frame, timeout, **kwargs):
        result = await original(frame, timeout, **kwargs)
        if frame["channel"] == "claude:set-codex-approval-policy":
            if failure == "lost":
                raise ConnectionLost("fixture lost ACK after BAT changed local mode")
            return {"type": "invoke-error", "id": frame["id"], "error": "fixture thread/resume failed"}
        return result
    monkeypatch.setattr(client, "_roundtrip", broken)
    op, _ = d.ops.create(PERSON, **intent(CODEX))
    await settle_operations(d.ops)
    out = d.ops.get(op["operation_id"])
    assert out["status"] == "uncertain", out
    assert [f["acknowledged"] for f in out["external_refs"]["permission_frames"]] == [True, None]
    assert d.journal.commands(tid)[0]["status"] == d.journal.get(tid)["state"] == "uncertain"
    assert mock.metas[CODEX]["codexApprovalPolicy"] == "on-request"
    d.ops.db.execute("UPDATE operations SET next_run_at=0 WHERE operation_id=?", (op["operation_id"],))
    await settle_operations(d.ops)
    assert len(api.write_frames(mock)) == 2 and len(d.journal.commands(tid)) == 1
    assert registry.get("h1", CODEX).get("execution_options") is None


@pytest.mark.parametrize("when", ["before_first", "before_second"])
@pytest.mark.parametrize("change", ["pause", "version", "owner"])
async def test_each_task_frame_rechecks_control_after_guard_read(owned, mock, monkeypatch, when, change):
    d, tid = owned
    adopt(CODEX, task_id=tid, role="lead", agent_preset="codex-agent")
    d.journal.db.execute("UPDATE tasks SET session_id=? WHERE task_id=?", (CODEX, tid))
    client, original = d.fleet.client("h1"), d.fleet.client("h1").guard_read
    changed = False
    async def read(channel, params):
        nonlocal changed
        result = await original(channel, params)
        if channel == "git:getRoot" and not changed and len(api.write_frames(mock)) == int(when == "before_second"):
            changed = True
            if change == "owner":
                monkeypatch.setattr(d.journal, "owner_valid", lambda: False)
            else:
                d.journal.pause(tid)
                if change == "version":
                    d.journal.resume(tid)
        return result
    monkeypatch.setattr(client, "guard_read", read)
    op, _ = d.ops.create(PERSON, **intent(CODEX))
    await settle_operations(d.ops)
    out = d.ops.get(op["operation_id"])
    assert changed and out["status"] == "failed", out
    assert out["error_code"] in {"TASK_PAUSED", "CONTROL_VERSION_CONFLICT", "TASK_OWNER_UNAVAILABLE"}
    assert len(api.write_frames(mock)) == int(when == "before_second")
    assert d.journal.commands(tid)[0]["status"] == ("uncertain" if when == "before_second" else "rejected")


@pytest.mark.parametrize("first", ["http", "mcp", "cli"])
async def test_real_transports_share_actor_key_and_full_permission_receipt(served, mock, monkeypatch, capsys, first):
    import asyncio

    from bat_agent_connector import cli
    from bat_agent_connector.mcp_server import build_server
    from tests.test_interrupt_operations import mcp_result
    d, port = served
    adopt(CODEX, agent_preset="codex-agent")
    token = api.token(d, PERSON.actor, "operate", "observe")
    monkeypatch.setenv("BATC_API_TOKEN", token)
    monkeypatch.setenv("BATC_TASK_URL", f"http://127.0.0.1:{port}/rpc")
    monkeypatch.setattr(cli, "load_config", lambda _: d.fleet.config)
    server, fleet = build_server(d.fleet.config)
    async def through(door):
        if door == "http":
            code, out = await api.http(port, "POST", "/api/v1/operations", tok=token, body=intent(CODEX))
            assert code in {200, 202}, out
            await settle_operations(d.ops)
            return out["operation"]["operation_id"]
        if door == "mcp":
            out = await mcp_result(server, "session_set_permissions", {"host": "h1", "session_id": CODEX,
                "mode": "default", "confirm": True, "idempotency_key": "permissions-one"})
        else:
            assert await asyncio.to_thread(cli.main, ["--json", "permissions", "h1", CODEX,
                "--mode", "default", "--confirm", "--key", "permissions-one"]) == 0
            out = json.loads(capsys.readouterr().out)
        assert out["operation_status"] == "succeeded" and len(out["calls"]) == 2
        assert out["agent_kind"] == "codex" and "enforcement is not proven" in out["note"]
        return out["operation_id"]
    try:
        op_id = await through(first)
        for door in ("http", "mcp", "cli"):
            assert await through(door) == op_id
        assert d.ops.get(op_id)["actor"] == PERSON.actor and len(api.write_frames(mock)) == 2
    finally:
        await fleet.close()


async def test_legacy_prefix_no_key_and_named_replay_are_original_intents(daemon, mock, monkeypatch):
    from bat_agent_connector import service
    adopt(CLAUDE)
    req = {"host": "h1", "session_id": "sess-claude", "mode": "default", "confirm": True}
    outputs = [await daemon.call_api("session_set_permissions", dict(req), PERSON) for _ in range(2)]
    assert outputs[0]["operation_id"] != outputs[1]["operation_id"]
    assert all(x["idempotency_key"] is None and not x["idempotency_enabled"] for x in outputs)
    first = await daemon.call_api("session_set_permissions", {**req, "idempotency_key": "fixed"}, PERSON)
    saved = daemon.ops.get(first["operation_id"])
    assert saved["target"]["session_id"] == "sess-claude" and saved["external_refs"]["resolved_target"]["session_id"] == CLAUDE
    async def forbidden(*_a, **_kw):
        pytest.fail("replay resolved a changed selector")
    monkeypatch.setattr(service, "_resolve_session", forbidden)
    assert await daemon.call_api("session_set_permissions", {**req, "idempotency_key": "fixed"}, PERSON) == first
    assert len(api.write_frames(mock)) == 3
    await daemon.fleet.close()


@pytest.mark.parametrize("params", [{}, {"mode": []}, {"mode": {}}, {"mode": "other"}, {"mode": None},
                                    {"mode": "default", "force": True}])
async def test_canonical_permission_validation_precedes_admission(served, mock, params):
    d, port = served
    adopt(CLAUDE)
    token = api.token(d, "validation", "operate")
    body = {**intent(CLAUDE), "params": params}
    code, _ = await api.http(port, "POST", "/api/v1/operations", tok=token, body=body)
    assert code == 422 and not d.ops.list()["operations"] and not api.write_frames(mock)


@pytest.mark.parametrize("case", ["scope", "manual", "unknown", "host_policy", "confined"])
async def test_authority_refusals_create_no_permission_operation(daemon, mock, case):
    from bat_agent_connector.errors import BatError
    from bat_agent_connector.operations import OperationError
    principal = PERSON
    if case != "manual":
        if case == "unknown":
            registry.reserve("h1", {"session_id": CLAUDE, "cwd": "/srv/demo", "status": "active"}, max_active=32)
        else:
            adopt(CLAUDE)
    if case == "scope":
        principal = api_auth.Principal("reader", frozenset({"observe"}))
    if case == "confined":
        daemon.fleet.config.host("h1").default_permission_mode = "allow_all"
        registry.update("h1", CLAUDE, write_scope="confined")
    mode = "allow_all" if case in {"host_policy", "confined"} else "default"
    with pytest.raises((OperationError, BatError)):
        daemon.ops.create(principal, **intent(CLAUDE, mode=mode))
    assert not daemon.ops.list()["operations"] and not api.write_frames(mock)
    await daemon.fleet.close()


@pytest.mark.parametrize("state,code", [(True, "PERMISSIONS_STREAMING"), (None, "PERMISSIONS_IDLE_UNPROVEN")])
async def test_claude_requires_positive_idle_without_deferred_write(daemon, mock, state, code):
    adopt(CLAUDE)
    mock.metas[CLAUDE]["isStreaming"] = state
    op, _ = daemon.ops.create(PERSON, **intent(CLAUDE))
    await settle_operations(daemon.ops)
    assert daemon.ops.get(op["operation_id"])["error_code"] == code
    assert not api.write_frames(mock) and not registry.get("h1", CLAUDE).get("permission_raise_pending")
    await daemon.fleet.close()


@pytest.mark.parametrize("coordinator_first", [False, True])
@pytest.mark.parametrize("change", ["unchanged", "pause", "version", "binding", "newer_policy"])
async def test_all_ack_recovery_settles_original_command_without_live_reads(owned, mock, monkeypatch, coordinator_first, change):
    from bat_agent_connector import service
    d, tid = owned
    adopt(CODEX, task_id=tid, role="lead", agent_preset="codex-agent")
    d.journal.db.execute("UPDATE tasks SET session_id=? WHERE task_id=?", (CODEX, tid))
    old_state = d.journal.get(tid)["state"]
    done = d.ops._step_done
    crashed = False
    def crash(op_id, name, response, **kw):
        nonlocal crashed
        done(op_id, name, response, **kw)
        if name == "permissions.approval" and not crashed:
            crashed = True
            raise OSError("fixture after all positive frame receipts")
    monkeypatch.setattr(d.ops, "_step_done", crash)
    op, _ = d.ops.create(PERSON, **intent(CODEX))
    await settle_operations(d.ops)
    assert d.ops.get(op["operation_id"])["status"] == "uncertain"
    assert d.journal.get(tid)["state"] == "uncertain"
    command_id = d.journal.commands(tid)[0]["command_id"]
    if coordinator_first:
        await d.coordinator.tick(tid)
    if change in {"pause", "version"}:
        d.journal.pause(tid)
        if change == "version":
            d.journal.resume(tid)
    elif change == "binding":
        d.journal.db.execute("UPDATE tasks SET session_id='replacement' WHERE task_id=?", (tid,))
    elif change == "newer_policy":
        registry.update("h1", CODEX, execution_options={"codexSandboxMode": "read-only"}, permission_operation_id="op_newer")
    before = d.journal.get(tid)
    async def forbidden(*_a, **_kw):
        pytest.fail("all-ACK recovery consulted live policy or sent a frame")
    monkeypatch.setattr(service, "_resolve_session", forbidden)
    monkeypatch.setattr(d.fleet.client("h1"), "invoke", forbidden)
    d.ops.db.execute("UPDATE operations SET next_run_at=0 WHERE operation_id=?", (op["operation_id"],))
    await settle_operations(d.ops)
    out = d.ops.get(op["operation_id"])
    assert out["status"] == "succeeded", out
    assert len(api.write_frames(mock)) == 2 and len(d.journal.commands(tid)) == 1
    assert d.journal.command_get(command_id)["status"] == "settled"
    if change in {"pause", "version", "binding"}:
        assert d.journal.get(tid) == before
        assert not out["result"]["registry_projection"]["updated"]
    else:
        assert d.journal.get(tid)["state"] == old_state
    if change == "newer_policy":
        assert registry.get("h1", CODEX)["permission_operation_id"] == "op_newer"
        assert not out["result"]["registry_projection"]["updated"]


async def test_partial_ack_restart_sends_only_the_missing_frame(owned, mock, monkeypatch):
    d, tid = owned
    adopt(CODEX, task_id=tid, role="lead", agent_preset="codex-agent")
    d.journal.db.execute("UPDATE tasks SET session_id=? WHERE task_id=?", (CODEX, tid))
    done = d.ops._step_done
    once = False
    def crash(op_id, name, response, **kw):
        nonlocal once
        done(op_id, name, response, **kw)
        if name == "permissions.sandbox" and not once:
            once = True
            raise OSError("fixture crash before the next frame intent")
    monkeypatch.setattr(d.ops, "_step_done", crash)
    op, _ = d.ops.create(PERSON, **intent(CODEX))
    await settle_operations(d.ops)
    assert d.ops.get(op["operation_id"])["status"] == "uncertain" and len(api.write_frames(mock)) == 1
    await d.coordinator.tick(tid)
    d.ops.db.execute("UPDATE operations SET next_run_at=0 WHERE operation_id=?", (op["operation_id"],))
    await settle_operations(d.ops)
    out = d.ops.get(op["operation_id"])
    assert out["status"] == "succeeded", out
    assert [c["channel"] for c in api.write_frames(mock)] == ["claude:set-codex-sandbox-mode", "claude:set-codex-approval-policy"]
    assert len(d.journal.commands(tid)) == 1 and d.journal.commands(tid)[0]["status"] == "settled"


async def test_http_host_policy_refusal_is_explicit_and_has_no_operation(served, mock):
    d, port = served
    adopt(CLAUDE)
    code, body = await api.http(port, "POST", "/api/v1/operations", tok=api.token(d, PERSON.actor, "operate"),
                                body=intent(CLAUDE, mode="allow_all"))
    assert code == 403 and body["error"]["code"] == "PERMISSIONS_HOST_POLICY", body
    assert not d.ops.list()["operations"] and not api.write_frames(mock)


@pytest.mark.parametrize("hour_limit", [1, 100])
async def test_audit_batch_resume_counts_only_new_frames_and_preserves_original_actor(owned, mock, monkeypatch, hour_limit):
    from dataclasses import replace

    from bat_agent_connector.safety import Audit
    d, tid = owned
    d.fleet.config.safety = replace(d.fleet.config.safety, write_min_interval_s=3600, max_writes_per_hour=hour_limit)
    adopt(CODEX, task_id=tid, role="lead", agent_preset="codex-agent")
    d.journal.db.execute("UPDATE tasks SET session_id=? WHERE task_id=?", (CODEX, tid))
    done = d.ops._step_done
    def crash(op_id, name, response, **kw):
        done(op_id, name, response, **kw)
        if name == "permissions.sandbox":
            raise OSError("fixture before unsent approval")
    monkeypatch.setattr(d.ops, "_step_done", crash)
    op, _ = d.ops.create(PERSON, **intent(CODEX))
    await settle_operations(d.ops)
    assert len(api.write_frames(mock)) == 1
    monkeypatch.setattr(d.ops, "_step_done", done)
    d.ops.db.execute("UPDATE operations SET next_run_at=0 WHERE operation_id=?", (op["operation_id"],))
    await settle_operations(d.ops)
    out = d.ops.get(op["operation_id"])
    assert out["status"] == ("failed" if hour_limit == 1 else "succeeded"), out
    records = Audit(d.fleet.config.safety)._tail()
    attempts = [r for r in records if r.get("phase") == "attempt"]
    assert len(attempts) == len(api.write_frames(mock)) == (1 if hour_limit == 1 else 2)
    assert all(r["actor"] == PERSON.actor and r["operation_id"] == op["operation_id"] for r in attempts)
    assert len([r for r in records if r.get("phase") == "result"]) == len(attempts)
    if hour_limit != 1:
        before = list(records)
        replay, fresh = d.ops.create(PERSON, **intent(CODEX))
        assert not fresh and replay["operation_id"] == op["operation_id"]
        assert Audit(d.fleet.config.safety)._tail() == before
        later, _ = d.ops.create(PERSON, **intent(CODEX, key="later"))
        await settle_operations(d.ops)
        assert "rate limit" in d.ops.get(later["operation_id"])["status_reason"]
        assert len(api.write_frames(mock)) == 2


@pytest.mark.parametrize("crash_at", ["task_command", "permissions.sandbox", "permissions.approval", "permissions.registry"])
@pytest.mark.parametrize("coordinator_first", [False, True])
async def test_real_restart_keeps_original_command_and_never_repeats_saved_ack(owned, mock, monkeypatch, crash_at, coordinator_first):
    d, tid = owned
    adopt(CODEX, task_id=tid, role="lead", agent_preset="codex-agent")
    d.journal.db.execute("UPDATE tasks SET session_id=? WHERE task_id=?", (CODEX, tid))
    from bat_agent_connector.operations import OpContext
    done = d.ops._step_done
    effect = OpContext.effect
    def crash_after_effect(ctx, name, fn, **kwargs):
        result = effect(ctx, name, fn, **kwargs)
        if name == crash_at == "task_command":
            import asyncio
            raise asyncio.CancelledError("fixture after committed original task command")
        return result
    monkeypatch.setattr(OpContext, "effect", crash_after_effect)
    def crash(op_id, name, response, **kw):
        done(op_id, name, response, **kw)
        if name == crash_at and name != "task_command":
            # A process interruption (not an application refusal) leaves the operation runnable.
            import asyncio
            raise asyncio.CancelledError("fixture process stopped after receipt")
    monkeypatch.setattr(d.ops, "_step_done", crash)
    op, _ = d.ops.create(PERSON, **intent(CODEX))
    import asyncio
    with pytest.raises(asyncio.CancelledError):
        await d.ops._execute(op["operation_id"])
    assert d.ops.get(op["operation_id"])["status"] == "running"
    before_commands = d.journal.commands(tid)
    monkeypatch.setattr(OpContext, "effect", effect)
    async with task_tests.restarted_daemon(d) as restarted:
        if coordinator_first:
            await restarted.coordinator.tick(tid)
        expected_state = restarted.journal.get(tid)["state"] if crash_at == "permissions.registry" else "accepted"
        await settle_operations(restarted.ops)
        out = restarted.ops.get(op["operation_id"])
        assert out["status"] == "succeeded", out
        commands = restarted.journal.commands(tid)
        assert len(commands) == 1 and commands[0]["status"] == "settled"
        if before_commands:
            assert commands[0]["command_id"] == before_commands[0]["command_id"]
        assert [f["channel"] for f in api.write_frames(mock)] == ["claude:set-codex-sandbox-mode", "claude:set-codex-approval-policy"]
        assert restarted.journal.get(tid)["state"] == expected_state


async def test_all_ack_cancel_finishes_receipts_without_new_frames(owned, mock, monkeypatch):
    d, tid = owned
    adopt(CODEX, task_id=tid, role="lead", agent_preset="codex-agent")
    d.journal.db.execute("UPDATE tasks SET session_id=? WHERE task_id=?", (CODEX, tid))
    done = d.ops._step_done
    def crash(op_id, name, response, **kw):
        done(op_id, name, response, **kw)
        if name == "permissions.approval":
            raise OSError("fixture all ACKs before bookkeeping")
    monkeypatch.setattr(d.ops, "_step_done", crash)
    op, _ = d.ops.create(PERSON, **intent(CODEX))
    await settle_operations(d.ops)
    assert d.ops.get(op["operation_id"])["status"] == "uncertain"
    d.ops.cancel(PERSON, op["operation_id"])
    await settle_operations(d.ops)
    out = d.ops.get(op["operation_id"])
    assert out["status"] == "succeeded" and out["cancel_requested"], out
    assert len(api.write_frames(mock)) == 2 and d.journal.commands(tid)[0]["status"] == "settled"


@pytest.mark.parametrize("change", ["retired", "identity", "host_policy", "streaming"])
async def test_final_frame_refreshes_resource_policy_and_runtime_with_single_client_slot(daemon, mock, monkeypatch, change):
    import asyncio
    from dataclasses import replace
    adopt(CLAUDE)
    host = daemon.fleet.config.host("h1")
    monkeypatch.setattr(host, "default_permission_mode", "allow_all")
    client = daemon.fleet.client("h1")
    client._sem = asyncio.Semaphore(1)
    original = client.guard_read
    changed = False
    async def read(channel, params):
        nonlocal changed
        result = await original(channel, params)
        if channel == "claude:get-session-meta" and not changed:
            changed = True
            if change == "identity":
                result = {**result, "cwd": "/different"}
            elif change == "streaming":
                result = {**result, "isStreaming": True}
            elif change == "retired":
                registry.update("h1", CLAUDE, status="retired")
            else:
                monkeypatch.setattr(daemon.fleet.config, "hosts", {"h1": replace(host, default_permission_mode="default")})
        return result
    monkeypatch.setattr(client, "guard_read", read)
    op, _ = daemon.ops.create(PERSON, **intent(CLAUDE, mode="allow_all"))
    await asyncio.wait_for(settle_operations(daemon.ops), 5)
    out = daemon.ops.get(op["operation_id"])
    assert changed and out["status"] == "failed" and not api.write_frames(mock), out
    await daemon.fleet.close()


async def test_existing_permission_receipts_and_controls_require_current_operate(daemon, mock):
    from bat_agent_connector.operations import OperationError
    adopt(CODEX, agent_preset="codex-agent")
    op, _ = daemon.ops.create(PERSON, **intent(CODEX))
    reader = api_auth.Principal(PERSON.actor, frozenset({"observe"}))
    for fn in (lambda: daemon.ops.create(reader, **intent(CODEX)),
               lambda: daemon.ops.cancel(reader, op["operation_id"]),
               lambda: daemon.ops.resume(reader, op["operation_id"])):
        with pytest.raises(OperationError, match="operate"):
            fn()
    await settle_operations(daemon.ops)
    assert len(api.write_frames(mock)) == 2
    await daemon.fleet.close()


async def test_partial_ack_cancel_preserves_partial_command_evidence(owned, mock, monkeypatch):
    d, tid = owned
    adopt(CODEX, task_id=tid, role="lead", agent_preset="codex-agent")
    d.journal.db.execute("UPDATE tasks SET session_id=? WHERE task_id=?", (CODEX, tid))
    done = d.ops._step_done
    def crash(op_id, name, response, **kw):
        done(op_id, name, response, **kw)
        if name == "permissions.sandbox":
            raise OSError("fixture before next frame intent")
    monkeypatch.setattr(d.ops, "_step_done", crash)
    op, _ = d.ops.create(PERSON, **intent(CODEX))
    await settle_operations(d.ops)
    d.ops.cancel(PERSON, op["operation_id"])
    await settle_operations(d.ops)
    out = d.ops.get(op["operation_id"])
    assert out["status"] == "cancelled" and len(api.write_frames(mock)) == 1
    assert d.journal.commands(tid)[0]["status"] == "uncertain"
    assert d.journal.get(tid)["state"] == "uncertain"
    assert out["external_refs"]["permission_frames"][0]["acknowledged"] is True


async def test_codex_batch_checks_rate_once_but_audits_each_frame(daemon, mock):
    from bat_agent_connector.safety import Audit
    adopt(CODEX, agent_preset="codex-agent")
    daemon.fleet.config.safety.write_min_interval_s = 3600
    daemon.fleet.config.safety.max_writes_per_hour = 1
    op, _ = daemon.ops.create(PERSON, **intent(CODEX))
    await settle_operations(daemon.ops)
    assert daemon.ops.get(op["operation_id"])["status"] == "succeeded"
    assert len(api.write_frames(mock)) == 2
    attempts = [r for r in Audit(daemon.fleet.config.safety)._tail() if r.get("phase") == "attempt"]
    assert len(attempts) == 2
    await daemon.fleet.close()


@pytest.mark.parametrize("task_owned", [False, True])
@pytest.mark.parametrize("phase", ["connect", "guard_read"])
@pytest.mark.parametrize("error", ["connection", "timeout", "oserror", "async_timeout"])
async def test_observed_pretransport_failure_is_definitive_and_keeps_task_usable(
        owned, mock, monkeypatch, task_owned, phase, error):
    from bat_agent_connector.errors import ConnectionLost, InvokeTimeout
    d, tid = owned
    sid = CLAUDE if task_owned else CODEX
    if not task_owned:
        adopt(CODEX, agent_preset="codex-agent")
    client = d.fleet.client("h1")
    import asyncio
    exception = {"connection": ConnectionLost, "timeout": InvokeTimeout, "oserror": OSError,
                 "async_timeout": asyncio.TimeoutError}[error]
    async def fail(*_args, **_kwargs):
        raise exception("fixture failed before setter transport")
    original = client.invoke
    async def invoke(channel, params=None, **kwargs):
        if channel.startswith("claude:set-"):
            with monkeypatch.context() as patch:
                patch.setattr(client, "connect" if phase == "connect" else "guard_read", fail)
                return await original(channel, params, **kwargs)
        return await original(channel, params, **kwargs)
    monkeypatch.setattr(client, "invoke", invoke)
    op, _ = d.ops.create(PERSON, **intent(sid))
    await settle_operations(d.ops)
    out = d.ops.get(op["operation_id"])
    assert out["status"] == "failed" and out["error_code"] == "PERMISSIONS_NOT_SENT", out
    assert not api.write_frames(mock)
    if task_owned:
        assert d.journal.commands(tid)[0]["status"] == "rejected" and d.journal.get(tid)["state"] == "accepted"
    monkeypatch.setattr(client, "invoke", original)
    next_op, _ = d.ops.create(PERSON, **intent(sid, key="fresh-after-proven-no-send"))
    await settle_operations(d.ops)
    assert d.ops.get(next_op["operation_id"])["status"] == "succeeded"


async def test_intent_only_process_interruption_cannot_reconstruct_no_send(owned, mock, monkeypatch):
    import asyncio
    d, tid = owned
    start = d.ops._step_start
    def crash(op_id, name, request):
        start(op_id, name, request)
        if name == "permissions.mode":
            raise asyncio.CancelledError("process interruption loses volatile transport marker")
    monkeypatch.setattr(d.ops, "_step_start", crash)
    op, _ = d.ops.create(PERSON, **intent(CLAUDE))
    with pytest.raises(asyncio.CancelledError):
        await d.ops._execute(op["operation_id"])
    assert not api.write_frames(mock)
    command_id = d.journal.commands(tid)[0]["command_id"]
    async with task_tests.restarted_daemon(d) as restarted:
        await restarted.coordinator.tick(tid)
        await settle_operations(restarted.ops)
        out = restarted.ops.get(op["operation_id"])
        assert out["status"] == "uncertain" and not api.write_frames(mock)
        assert restarted.journal.command_get(command_id)["status"] == "uncertain"


async def test_http_confined_raise_refusal_preserves_explicit_code(served, mock):
    d, port = served
    adopt(CLAUDE, write_scope="confined")
    d.fleet.config.host("h1").default_permission_mode = "allow_all"
    code, body = await api.http(port, "POST", "/api/v1/operations", tok=api.token(d, PERSON.actor, "operate"),
                               body=intent(CLAUDE, mode="allow_all"))
    assert code == 403 and body["error"]["code"] == "CONFINEMENT_RAISE_REFUSED", body
    assert not d.ops.list()["operations"] and not api.write_frames(mock)


@pytest.mark.parametrize("changed", [False, True])
async def test_needs_attention_all_ack_cancel_finishes_original_receipts(owned, mock, monkeypatch, changed):
    from bat_agent_connector.operations import OpContext
    d, tid = owned
    adopt(CODEX, task_id=tid, role="lead", agent_preset="codex-agent")
    d.journal.db.execute("UPDATE tasks SET session_id=? WHERE task_id=?", (CODEX, tid))
    done, effect = d.ops._step_done, OpContext.effect
    def crash(op_id, name, response, **kw):
        done(op_id, name, response, **kw)
        if name == "permissions.approval":
            raise OSError("fixture after last ACK before command settlement")
    def fail_local(ctx, name, fn, **kw):
        if name == "permissions.command_result":
            raise OSError("fixture unavailable local bookkeeping")
        return effect(ctx, name, fn, **kw)
    monkeypatch.setattr(d.ops, "_step_done", crash)
    monkeypatch.setattr(OpContext, "effect", fail_local)
    op, _ = d.ops.create(PERSON, **intent(CODEX))
    for _ in range(6):
        d.ops.db.execute("UPDATE operations SET next_run_at=0 WHERE operation_id=?", (op["operation_id"],))
        await settle_operations(d.ops)
    assert d.ops.get(op["operation_id"])["status"] == "needs_attention"
    assert d.journal.commands(tid)[0]["status"] == "uncertain"
    monkeypatch.setattr(OpContext, "effect", effect)
    if changed:
        d.journal.pause(tid)
        d.journal.resume(tid)
    before = d.journal.get(tid)
    async def forbidden(*_a, **_kw):
        pytest.fail("receipt completion must not read BAT or dispatch")
    monkeypatch.setattr(d.fleet.client("h1"), "invoke", forbidden)
    cancelled = d.ops.cancel(PERSON, op["operation_id"])
    assert cancelled["cancel_requested"] and cancelled["status"] == "running"
    await settle_operations(d.ops)
    result = d.ops.get(op["operation_id"])
    assert result["status"] == "succeeded" and len(api.write_frames(mock)) == 2
    assert d.journal.commands(tid)[0]["status"] == "settled"
    if changed:
        assert d.journal.get(tid) == before and not result["result"]["registry_projection"]["updated"]
    else:
        assert d.journal.get(tid)["state"] == "accepted"


async def test_unknown_agent_does_not_receive_codex_permission_setters(daemon, mock):
    adopt(CODEX, agent_preset="custom-agent")
    next(t for t in mock.ws_doc["terminals"] if t["id"] == CODEX)["agentPreset"] = "custom-agent"
    op, _ = daemon.ops.create(PERSON, **intent(CODEX))
    await settle_operations(daemon.ops)
    assert daemon.ops.get(op["operation_id"])["error_code"] == "PERMISSIONS_AGENT_UNSUPPORTED"
    assert not api.write_frames(mock)
    await daemon.fleet.close()


@pytest.mark.parametrize("during_read", [False, True])
async def test_standalone_admission_cannot_adopt_a_later_task_owner(owned, mock, monkeypatch, during_read):
    d, tid = owned
    adopt(CODEX, agent_preset="codex-agent")
    op, _ = d.ops.create(PERSON, **intent(CODEX))
    binding = op["external_refs"]["admission_binding"]
    assert "task_id" not in binding
    assert binding["session_controller"]["session_id"] == CODEX
    def claim():
        registry.update("h1", CODEX, task_id=tid, role="lead")
        d.journal.db.execute("UPDATE tasks SET session_id=? WHERE task_id=?", (CODEX, tid))
    if during_read:
        client = d.fleet.client("h1")
        original = client.invoke
        async def invoke(channel, params=None, **kw):
            result = await original(channel, params, **kw)
            if channel == "claude:get-session-meta":
                claim()
            return result
        monkeypatch.setattr(client, "invoke", invoke)
    else:
        claim()
    await settle_operations(d.ops)
    assert d.ops.get(op["operation_id"])["error_code"] == "TASK_BINDING_MISMATCH"
    assert not api.write_frames(mock) and not d.journal.commands(tid)


async def test_needs_attention_unknown_ack_cancel_keeps_normal_uncertainty_evidence(owned, mock, monkeypatch):
    d, tid = owned
    adopt(CODEX, task_id=tid, role="lead", agent_preset="codex-agent")
    d.journal.db.execute("UPDATE tasks SET session_id=? WHERE task_id=?", (CODEX, tid))
    client, original = d.fleet.client("h1"), d.fleet.client("h1")._roundtrip
    async def lose(frame, timeout, **kw):
        result = await original(frame, timeout, **kw)
        if frame["channel"] == "claude:set-codex-approval-policy":
            return {**result, "result": False}
        return result
    monkeypatch.setattr(client, "_roundtrip", lose)
    op, _ = d.ops.create(PERSON, **intent(CODEX))
    for _ in range(6):
        d.ops.db.execute("UPDATE operations SET next_run_at=0 WHERE operation_id=?", (op["operation_id"],))
        await settle_operations(d.ops)
    assert d.ops.get(op["operation_id"])["status"] == "needs_attention"
    cancelled = d.ops.cancel(PERSON, op["operation_id"])
    assert cancelled["status"] == "cancelled" and "never proven" in cancelled["status_reason"]
    await settle_operations(d.ops)
    assert len(api.write_frames(mock)) == 2
    assert d.journal.commands(tid)[0]["status"] == "uncertain"
    assert [f["acknowledged"] for f in cancelled["external_refs"]["permission_frames"]] == [True, None]
