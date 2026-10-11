"""Legacy send/continue/answer share real central operations and mock BAT frames."""
from __future__ import annotations

import asyncio
import json

import pytest

from bat_agent_connector import api_auth, cli, mcp_server, registry, service, task_daemon
from bat_agent_connector.errors import ConnectionLost
from bat_agent_connector.mcp_server import build_server
from bat_agent_connector.operations import NO_KEY_PREFIX, OperationError
from tests import test_api_v1 as api
from tests import test_operations_unification as task_tests
from tests.conftest import adopt, controller_scope
from tests.operation_helpers import settle_operations
from tests.test_interrupt_operations import mcp_result, rpc

daemon, served, owned = api.daemon, api.served, task_tests.owned
SID = api.MANUAL
PERSON = api_auth.Principal("operator-dashboard", frozenset({"operate", "observe"}))


def request(kind, **extra):
    params = {"send": {"text": "reviewed prompt"}, "continue": {},
              "answer": {"permission": "allow"}}[kind]
    return {"host": "h1", "session_id": SID, "confirm": True, **params, **extra}


def pending(mock, *, sid=SID, kind="permission", tuid="reviewed-prompt"):
    mock.states[sid]["pendingAskUser"] = None
    mock.states[sid]["pendingPermission"] = None
    field = "pendingPermission" if kind == "permission" else "pendingAskUser"
    mock.states[sid][field] = {"toolUseId": tuid, "toolName": "Read", "input": {"file": "fixture"},
                               "questions": [{"question": "Which?"}]}


async def invoke(d, kind, **params):
    return await d.call_api("session_" + kind, request(kind, **params), PERSON)


@pytest.mark.parametrize("kind", ["send", "answer", "continue"])
@pytest.mark.parametrize("first", ["http", "mcp", "cli"])
async def test_named_keys_share_canonical_action_across_real_transports(served, mock, monkeypatch, capsys, kind, first):
    d, port = served
    adopt(SID)
    pending(mock)
    if kind == "send":
        mock.metas[SID]["isStreaming"] = True  # queue must survive each real transport
    tok = api.token(d, "operator-dashboard", "operate", "observe")
    monkeypatch.setenv("BATC_TASK_URL", f"http://127.0.0.1:{port}/rpc")
    monkeypatch.setenv("BATC_API_TOKEN", tok)
    monkeypatch.setattr(cli, "load_config", lambda _: d.fleet.config)
    params = {"send": {"text": "reviewed prompt", "message_id": "caller-message", "queue": True},
              "continue": {"text": "continue"},
              "answer": {"permission": "allow", "tool_use_id": "reviewed-prompt", "dont_ask_again": True}}[kind]
    command = {"send": ["send", "h1", SID, "reviewed prompt", "--message-id", "caller-message", "--queue"],
               "continue": ["continue", "h1", SID],
               "answer": ["answer", "h1", SID, "--permission", "allow", "--tool-use-id", "reviewed-prompt", "--dont-ask-again"]}[kind]
    action = "session.answer" if kind == "answer" else "session.send"
    server, fleet = build_server(d.fleet.config)
    async def through(door):
        if door == "http":
            code, out = await api.http(port, "POST", "/api/v1/operations", tok=tok, body={
                "action": action, "target": {"host": "h1", "session_id": SID}, "params": params,
                "idempotency_key": "shared-control"})
            assert code in {200, 202}, out
            await settle_operations(d.ops)
            return out["operation"]["operation_id"]
        if door == "mcp":
            out = await mcp_result(server, "session_" + kind, request(kind, **params, idempotency_key="shared-control"))
        else:
            assert await asyncio.to_thread(cli.main, ["--json", *command, "--confirm", "--key", "shared-control"]) == 0
            out = json.loads(capsys.readouterr().out)
        assert out["operation_status"] == "succeeded" and out["idempotency_key"] == "shared-control"
        assert out["host"] == "h1" and out["session_id"] == SID
        if kind == "answer":
            assert out["result"] is True and out["permission_tool"] == "Read"
        else:
            assert out["accepted"] is True
            if kind == "send":
                assert out["queued"] is True
            assert {"after_ms", "after", "note", "turn_phase", "turn_attribution", "resumed"} <= out.keys()
        return out["operation_id"]
    try:
        op_id = await through(first)
        for door in ("http", "mcp", "cli"):
            assert await through(door) == op_id
        frames = api.write_frames(mock)
        assert len(frames) == 1
        if kind == "send":
            assert frames[0]["params"]["clientMessageId"] == "caller-message"
        elif kind == "answer":
            assert frames[0]["params"]["result"]["dontAskAgain"] is True
        assert d.ops.get(op_id)["actor"] == "operator-dashboard"
        assert not d.journal.db.execute("SELECT 1 FROM commands").fetchone()
    finally:
        await fleet.close()


@pytest.mark.parametrize("door", ["cli", "mcp"])
async def test_failed_control_receipt_keeps_actionable_reason_on_keyed_replay(
        served, mock, monkeypatch, capsys, door):
    from tests.test_mcp_principal import call
    d, port = served
    adopt(SID)
    mock.metas[SID]["isStreaming"] = True
    # This principal cannot use operation GET to recover an omitted failure explanation.
    token = api.token(d, "operator-dashboard", "operate")
    monkeypatch.setenv("BATC_TASK_URL", f"http://127.0.0.1:{port}/rpc")
    monkeypatch.setenv("BATC_API_TOKEN", token)
    monkeypatch.setattr(cli, "load_config", lambda _: d.fleet.config)
    server, fleet = build_server(d.fleet.config)

    async def attempt():
        if door == "mcp":
            return await call(server, "session_send", request("send", idempotency_key="refused-control"))
        code = await asyncio.to_thread(cli.main, ["--json", "send", "h1", SID, "reviewed prompt",
                                                 "--confirm", "--key", "refused-control"])
        assert code == 1
        return capsys.readouterr().out

    try:
        first = await attempt()
        op = d.ops.list()["operations"][0]
        assert op["status"] == "failed" and op["error_code"] == "REFUSED"
        assert "pass queue=true" in op["status_reason"]
        assert "operation_status_reason" in first and op["status_reason"] in first
        assert op["operation_id"] in first
        assert (await api.http(port, "GET", "/api/v1/operations/" + op["operation_id"], tok=token))[0] == 403
        mock.metas[SID]["isStreaming"] = False

        async def unexpected(*_a, **_kw):
            pytest.fail("a named replay observed the changed session instead of returning its receipt")
        monkeypatch.setattr(service, "_resolve_session", unexpected)
        replay = await attempt()
        assert op["status_reason"] in replay and op["operation_id"] in replay
        assert len(d.ops.list()["operations"]) == 1 and not api.write_frames(mock)
    finally:
        await fleet.close()


@pytest.mark.parametrize("kind", ["send", "continue", "answer"])
async def test_true_no_key_operations_and_prefix_history_are_durable(daemon, mock, kind):
    adopt(SID)
    results = []
    for _ in range(2):
        pending(mock)
        results.append(await invoke(daemon, kind, session_id="sess-claude"))
    assert results[0]["operation_id"] != results[1]["operation_id"]
    for out in results:
        assert out["operation_status"] == "succeeded" and out["session_id"] == SID
        assert out["idempotency_key"] is None and not out["idempotency_enabled"]
        op = daemon.ops.get(out["operation_id"])
        assert op["target"]["session_id"] == "sess-claude"
        assert op["external_refs"]["resolved_target"]["session_id"] == SID
        assert NO_KEY_PREFIX not in json.dumps(op)
        if kind == "answer":
            assert "tool_use_id" not in op["params"]
            assert op["external_refs"]["resolved_params"] == {"tool_use_id": "reviewed-prompt"}
    assert len(api.write_frames(mock)) == 2
    assert not daemon.journal.api_events(related_resource_type="session", related_resource_id="h1/sess-claude")["events"]
    events = daemon.journal.api_events(related_resource_type="session", related_resource_id=f"h1/{SID}")["events"]
    assert {e["kind"] for e in events} >= {"operation.accepted", "operation.succeeded"}
    await daemon.fleet.close()


@pytest.mark.parametrize("kind", ["send", "continue", "answer"])
async def test_named_replay_does_not_resolve_changed_selector_or_pending_prompt(daemon, mock, monkeypatch, kind):
    adopt(SID)
    pending(mock)
    first = await invoke(daemon, kind, session_id="sess-claude", idempotency_key="fixed")
    async def forbidden(*_a, **_k):
        pytest.fail("replay re-resolved a selector or pending prompt")
    monkeypatch.setattr(service, "_resolve_session", forbidden)
    monkeypatch.setattr(service, "_meta", forbidden)
    again = await invoke(daemon, kind, session_id="sess-claude", idempotency_key="fixed")
    assert again == first
    with pytest.raises(OperationError, match="IDEMPOTENCY_CONFLICT"):
        await invoke(daemon, kind, idempotency_key="fixed")
    assert len(api.write_frames(mock)) == 1
    await daemon.fleet.close()


@pytest.mark.parametrize("kind", ["send", "continue", "answer"])
async def test_bound_full_id_cannot_retarget_a_longer_prefix(daemon, mock, monkeypatch, kind):
    adopt(SID)
    pending(mock)
    create = daemon.ops.create
    def changed(*args, **kwargs):
        result = create(*args, **kwargs)
        mock.ws_doc["terminals"][0]["id"] += "-replacement"
        adopt(mock.ws_doc["terminals"][0]["id"])
        return result
    monkeypatch.setattr(daemon.ops, "create", changed)
    out = await invoke(daemon, kind)
    assert out["operation_status"] == "failed" and out["operation_error_code"] == "TASK_BINDING_MISMATCH"
    assert not api.write_frames(mock)
    await daemon.fleet.close()


@pytest.mark.parametrize("kind", ["permission", "question"])
async def test_omitted_answer_identity_is_fixed_before_admission_and_changed_prompt_refuses(daemon, mock, monkeypatch, kind):
    adopt(SID)
    pending(mock, kind=kind)
    create = daemon.ops.create
    def changed(*args, **kwargs):
        out = create(*args, **kwargs)
        pending(mock, kind=kind, tuid="later-prompt")
        return out
    monkeypatch.setattr(daemon.ops, "create", changed)
    params = {"permission": None, "answers": ["first"]} if kind == "question" else {}
    out = await invoke(daemon, "answer", **params)
    assert out["operation_status"] == "failed" and out["tool_use_id"] == "reviewed-prompt"
    assert not api.write_frames(mock)
    await daemon.fleet.close()


@pytest.mark.parametrize("evidence", [None, {}, {"toolUseId": []}, {"toolUseId": ""}, "both"])
async def test_answer_without_id_requires_positive_unambiguous_pending_evidence(daemon, mock, evidence):
    adopt(SID)
    pending(mock)
    if evidence == "both":
        mock.states[SID]["pendingAskUser"] = {"toolUseId": "other-kind"}
    else:
        mock.states[SID]["pendingPermission"] = evidence
    with pytest.raises(OperationError, match="PENDING_UNAVAILABLE"):
        await invoke(daemon, "answer")
    assert not daemon.ops.list()["operations"] and not api.write_frames(mock)
    await daemon.fleet.close()


@pytest.mark.parametrize("kind,bad", [
    ("send", {"text": []}), ("send", {"message_id": {}}), ("send", {"message_id": ""}),
    ("send", {"queue": "false"}), ("continue", {"queue": None}),
    ("answer", {"permission": []}), ("answer", {"permission": {}}),
    ("answer", {"tool_use_id": []}), ("answer", {"tool_use_id": ""}),
    ("answer", {"permission": None, "answers": [1]}), ("answer", {"dont_ask_again": "false"}),
    ("answer", {"deny_message": []}), ("send", {"control_version": True}),
])
async def test_malformed_legacy_controls_refuse_before_observation_or_admission(daemon, mock, monkeypatch, kind, bad):
    async def forbidden(*_a, **_k):
        pytest.fail("malformed request reached session observation")
    monkeypatch.setattr(service, "_resolve_session", forbidden)
    with pytest.raises(OperationError) as refused:
        await invoke(daemon, kind, **bad)
    assert refused.value.code == "INVALID_PARAMS"
    assert not daemon.ops.list()["operations"] and not api.write_frames(mock)


@pytest.mark.parametrize("kind", ["send", "continue", "answer"])
@pytest.mark.parametrize("change,code", [("pause", "TASK_PAUSED"), ("version", "CONTROL_VERSION_CONFLICT"),
                                        ("owner", "TASK_BINDING_MISMATCH")])
async def test_task_owned_controls_keep_original_command_and_final_frame_guard(owned, mock, monkeypatch, kind, change, code):
    d, tid = owned
    pending(mock)
    client = d.fleet.client("h1")
    original = client._invoke_checked
    async def late(channel, *args, **kwargs):
        if channel in {"claude:send-message", "claude:resolve-permission"}:
            if change == "pause":
                d.journal.db.execute("UPDATE tasks SET paused=1 WHERE task_id=?", (tid,))
            elif change == "version":
                d.journal.pause(tid)
                d.journal.resume(tid)
            else:
                registry.update("h1", SID, task_id="replacement-task")
        return await original(channel, *args, **kwargs)
    monkeypatch.setattr(client, "_invoke_checked", late)
    out = await invoke(d, kind, idempotency_key="task-gate")
    assert out["operation_status"] == "failed" and out["operation_error_code"] == code
    assert not api.write_frames(mock)
    commands = d.journal.commands(tid)
    assert len(commands) == 1 and commands[0]["status"] == "rejected"
    assert (await invoke(d, kind, idempotency_key="task-gate"))["operation_id"] == out["operation_id"]
    assert len(d.journal.commands(tid)) == 1


@pytest.mark.parametrize("kind", ["send", "continue", "answer"])
@pytest.mark.parametrize("case,code", [("manual", "MANUAL_READ_ONLY"), ("unknown", "UNKNOWN_READ_ONLY"),
                                      ("scope", "FORBIDDEN"), ("confirm", "CONFIRM_REQUIRED"), ("tier", "TIER_DISABLED")])
async def test_legacy_control_authority_refuses_without_operation_or_frame(daemon, mock, kind, case, code):
    params = request(kind)
    person = PERSON
    if case not in {"manual", "unknown"}:
        adopt(SID)
    if case == "unknown":
        registry.reserve("h1", {"session_id": SID, "status": "active"}, 10)
    elif case == "scope":
        person = api_auth.Principal("observer", frozenset({"observe"}))
    elif case == "confirm":
        params["confirm"] = False
    elif case == "tier":
        daemon.fleet.read_only = True
    try:
        with pytest.raises(Exception) as refused:
            await daemon.call_api("session_" + kind, params, person)
        assert getattr(refused.value, "code", None) == code
        assert not daemon.ops.list()["operations"] and not api.write_frames(mock)
    finally:
        await daemon.fleet.close()


@pytest.mark.parametrize("kind", ["send", "continue", "answer"])
async def test_mcp_requires_agent_token_and_unavailable_owner_has_no_direct_fallback(served, mock, monkeypatch, capsys, kind):
    from tests.test_mcp_principal import call
    d, port = served
    adopt(SID)
    pending(mock)
    monkeypatch.delenv("BATC_API_TOKEN", raising=False)
    monkeypatch.setenv("BATC_TASK_ADMIN_TOKEN_FILE", str(d.admin_token_path))
    monkeypatch.setenv("BATC_TASK_URL", f"http://127.0.0.1:{port}/rpc")
    monkeypatch.setattr(cli, "load_config", lambda _: d.fleet.config)
    server, fleet = build_server(d.fleet.config)
    try:
        assert "BATC_API_TOKEN" in await call(server, "session_" + kind, request(kind))
        monkeypatch.setenv("BATC_API_TOKEN", api.token(d, "operator-dashboard", "operate"))
        def unavailable(*_a, **_k):
            raise ConnectionRefusedError("owner unavailable")
        monkeypatch.setattr(task_daemon, "request", unavailable)
        monkeypatch.setattr(mcp_server, "task_request", unavailable)
        assert "owner unavailable" in await call(server, "session_" + kind, request(kind))
        argv = [kind, "h1", SID] + (["reviewed prompt"] if kind == "send" else ["--permission", "allow"] if kind == "answer" else [])
        assert await asyncio.to_thread(cli.main, [*argv, "--confirm"]) == 1
        assert "outcome may be unknown" in capsys.readouterr().err
        assert not d.ops.list()["operations"]
        assert not api.write_frames(mock)
    finally:
        await fleet.close()


@pytest.mark.parametrize("kind", ["send", "answer"])
async def test_lost_ack_restart_reconciles_fixed_identity_without_resend(daemon, mock, monkeypatch, kind):
    adopt(SID, agent_preset="claude-code")
    pending(mock)
    mock.echo_sends = True
    client = daemon.fleet.client("h1")
    original = client._roundtrip
    channel = "claude:send-message" if kind == "send" else "claude:resolve-permission"
    async def lost(frame, *args, **kwargs):
        result = await original(frame, *args, **kwargs)
        if frame["channel"] == channel:
            raise ConnectionLost("fixture: reply lost after effect frame")
        return result
    monkeypatch.setattr(client, "_roundtrip", lost)
    extra = {"message_id": "caller-lost-message"} if kind == "send" else {}
    first = await invoke(daemon, kind, idempotency_key="lost", **extra)
    assert first["operation_status"] == "uncertain"
    assert first["accepted" if kind == "send" else "result"] is None
    good_state = mock.states[SID]
    await daemon.fleet.close()
    await daemon.inventory.close()
    daemon.journal.close()
    reopened = task_daemon.TaskDaemon(daemon._config, daemon._db_path)
    try:
        mock.states[SID] = {}  # missing fields are not affirmative readback
        reopened.journal.db.execute("UPDATE operations SET next_run_at=0")
        out = await invoke(reopened, kind, idempotency_key="lost", **extra)
        await settle_operations(reopened.ops)
        assert out["operation_status"] == "uncertain" and out["operation_id"] == first["operation_id"]
        mock.states[SID] = good_state
        reopened.journal.db.execute("UPDATE operations SET next_run_at=0")
        out = await invoke(reopened, kind, idempotency_key="lost", **extra)
        await settle_operations(reopened.ops)
        out = await invoke(reopened, kind, idempotency_key="lost", **extra)
        assert out["operation_status"] == "succeeded" and out["operation_id"] == first["operation_id"]
        if kind == "answer":
            assert out["tool_use_id"] == "reviewed-prompt" and out["result"] is None
        else:
            assert out["message_id"] == "caller-lost-message" and out["accepted"] is True
        assert len(api.write_frames(mock)) == 1
    finally:
        await reopened.ops.drain()
        await reopened.fleet.close()
        await reopened.inventory.close()
        reopened.journal.close()


async def test_custom_message_id_readback_cannot_prove_another_payload(daemon, mock, monkeypatch):
    adopt(SID, agent_preset="claude-code")
    registry.record_turn("h1", SID, "caller-collision", queued=False, baseline_turns=0)
    mock.states[SID]["messages"].append({"id": "caller-collision", "role": "user", "content": "older text"})
    async def lost(*_a, **_k):
        raise ConnectionLost("fixture uncertain send")
    monkeypatch.setattr(service, "session_send", lost)
    out = await invoke(daemon, "send", message_id="caller-collision", idempotency_key="collision")
    daemon.journal.db.execute("UPDATE operations SET next_run_at=0")
    out = await invoke(daemon, "send", message_id="caller-collision", idempotency_key="collision")
    assert out["operation_status"] == "uncertain" and out["accepted"] is None
    assert not api.write_frames(mock)
    await daemon.fleet.close()


async def test_prompt_binding_and_original_intent_commit_together(daemon, mock, monkeypatch):
    adopt(SID)
    pending(mock)
    event = daemon.journal.api_event
    def crash(resource_type, resource_id, kind, *args, **kwargs):
        if kind == "operation.accepted":
            raise RuntimeError("fixture crash before operation/binding commit")
        return event(resource_type, resource_id, kind, *args, **kwargs)
    monkeypatch.setattr(daemon.journal, "api_event", crash)
    with pytest.raises(RuntimeError, match="before operation/binding"):
        await invoke(daemon, "answer")
    assert not daemon.ops.list()["operations"] and not api.write_frames(mock)
    await daemon.fleet.close()


@pytest.mark.parametrize("answers", [["first"], {"Which?": "first"}, {"0": "first"}])
async def test_answer_preserves_question_mapping_and_complete_receipt(daemon, mock, answers):
    adopt(SID)
    pending(mock, kind="question")
    out = await invoke(daemon, "answer", permission=None, answers=answers)
    assert out["operation_status"] == "succeeded" and out["result"] is True
    assert out["questions"] == out["answered"] == 1
    assert api.write_frames(mock)[0]["params"]["answers"] == {"Which?": "first"}
    await daemon.fleet.close()


async def test_answer_preserves_deny_message_and_codex_session_permission(daemon, mock):
    sid = "sess-codex-0002"
    adopt(sid)
    pending(mock, sid=sid)
    denied = await invoke(daemon, "answer", session_id=sid, permission="deny", deny_message="reviewed refusal")
    assert denied["operation_status"] == "succeeded" and denied["permission"] == "deny"
    assert api.write_frames(mock)[0]["params"]["result"] == {"behavior": "deny", "message": "reviewed refusal"}
    pending(mock, sid=sid, tuid="later-permission")
    allowed = await invoke(daemon, "answer", session_id=sid, dont_ask_again=True)
    assert allowed["operation_status"] == "succeeded" and allowed["permission_tool"] == "Read"
    assert api.write_frames(mock)[1]["params"]["result"]["dontAskAgain"] is True
    await daemon.fleet.close()


@pytest.mark.parametrize("action,params", [
    ("session.send", {"text": "prompt", "queue": []}),
    ("session.send", {"text": "prompt", "message_id": 1}),
    ("session.answer", {"permission": [], "tool_use_id": "reviewed"}),
    ("session.answer", {"permission": "allow", "tool_use_id": {}}),
])
async def test_http_and_rpc_reject_malformed_canonical_params_without_admission(served, action, params):
    d, port = served
    tok = api.token(d, "operator-dashboard", "operate")
    body = {"action": action, "target": {"host": "h1", "session_id": SID}, "params": params,
            "idempotency_key": "malformed"}
    code, out = await api.http(port, "POST", "/api/v1/operations", tok=tok, body=body)
    assert code == 422 and out["error"]["code"] == "INVALID_PARAMS"
    code, out = await rpc(port, tok, "op_submit", body)
    assert code == 400 and out["error"] == "INVALID_PARAMS"
    assert not d.ops.list()["operations"]


@pytest.mark.parametrize("action", ["session.send", "session.answer", "session.interrupt"])
@pytest.mark.parametrize("bad", [[], {}, " "])
async def test_canonical_session_targets_refuse_malformed_host_before_policy(served, action, bad):
    d, port = served
    tok = api.token(d, "operator-dashboard", "operate")
    params = {"session.send": {"text": "prompt"}, "session.answer": {"permission": "allow", "tool_use_id": "reviewed"},
              "session.interrupt": {"mode": "soft"}}[action]
    body = {"action": action, "target": {"host": bad, "session_id": SID}, "params": params,
            "idempotency_key": "malformed-host"}
    code, out = await api.http(port, "POST", "/api/v1/operations", tok=tok, body=body)
    assert code == 422 and out["error"]["code"] == "INVALID_TARGET"
    code, out = await rpc(port, tok, "op_submit", body)
    assert code == 400 and out["error"] == "INVALID_TARGET"
    assert not d.ops.list()["operations"]


@pytest.mark.parametrize("kind", ["send", "answer"])
async def test_raw_operations_cannot_select_legacy_key_or_resolved_input_mode(served, kind):
    d, port = served
    tok = api.token(d, "operator-dashboard", "operate")
    params = {"text": "prompt"} if kind == "send" else {"permission": "allow", "tool_use_id": "reviewed"}
    body = {"action": "session." + kind, "target": {"host": "h1", "session_id": SID}, "params": params,
            "_legacy_session": True, "_resolved_target": {"host": "h1", "session_id": "forged-session"},
            "_resolved_params": {"tool_use_id": "forged-prompt"}}
    code, out = await api.http(port, "POST", "/api/v1/operations", tok=tok, body=body)
    assert code == 422 and out["error"]["code"] == "IDEMPOTENCY_KEY_REQUIRED"
    code, out = await rpc(port, tok, "op_submit", body)
    assert code == 400 and out["error"] == "IDEMPOTENCY_KEY_REQUIRED"
    assert not d.ops.list()["operations"]


@pytest.mark.parametrize("kind", ["send", "continue", "answer"])
async def test_legacy_control_key_namespace_keeps_original_actor(daemon, mock, kind):
    results = []
    for actor, sid in (("one", SID), ("two", "sess-codex-0002")):
        principal = api_auth.Principal(actor, frozenset({"operate"}))
        with controller_scope(daemon.journal, principal):
            adopt(sid, agent_preset="codex-agent" if actor == "two" else "claude-code")
        pending(mock, sid=sid)
        out = await daemon.call_api("session_" + kind, request(kind, session_id=sid, idempotency_key="shared-key"), principal)
        assert daemon.ops.get(out["operation_id"])["actor"] == actor
        results.append(out["operation_id"])
    assert results[0] != results[1] and len(api.write_frames(mock)) == 2
    await daemon.fleet.close()


@pytest.mark.parametrize("kind", ["send", "continue", "answer"])
def test_cli_read_only_refuses_controls_before_config_stdin_or_rpc(monkeypatch, capsys, kind):
    from types import SimpleNamespace
    def forbidden(*_a, **_k):
        pytest.fail("read-only control reached configuration/stdin/RPC")
    # Replace only this module's sys binding, not global stdlib classes.
    monkeypatch.setattr(cli, "sys", SimpleNamespace(stdin=SimpleNamespace(read=forbidden), stderr=cli.sys.stderr))
    monkeypatch.setattr(cli, "load_config", forbidden)
    monkeypatch.setattr(task_daemon, "request", forbidden)
    argv = [kind, "h1", SID] + (["-"] if kind == "send" else ["--permission", "allow"] if kind == "answer" else [])
    assert cli.main(["--read-only", *argv, "--confirm"]) == 1
    assert "--read-only" in capsys.readouterr().err
