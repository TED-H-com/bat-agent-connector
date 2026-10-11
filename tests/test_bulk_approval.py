"""Reviewed bulk selections exercise the real operation owner and MockBat frames."""
from __future__ import annotations

import asyncio

import pytest

from bat_agent_connector import api_auth, bulk_approval, registry
from tests import test_api_v1 as api
from tests import test_operations_unification as task_tests
from tests.conftest import adopt
from tests.operation_helpers import settle_operations

daemon, served, owned = api.daemon, api.served, task_tests.owned
SID = "sess-codex-0002"


def caller(d, actor="operator-dashboard", scopes=("operate", "observe")):
    token = api.token(d, actor, *scopes)
    return token, api_auth.authenticate(d.journal.db, token, d._admin_token)


def pending(mock, sid=SID, prompt_id="permission-reviewed"):
    mock.states[sid]["pendingPermission"] = {"toolUseId": prompt_id, "toolName": "Bash", "input": {"command": "pytest"}}
    mock.states[sid]["pendingAskUser"] = None


def request(doc, *, mode=None, key="bulk-fixed"):
    item = next(i for i in doc["items"] if i["eligible"])
    return {"action": bulk_approval.ACTION, "target": {"host": "h1"},
            "params": {"preview_token": doc["preview_token"], "selection": [{"item_id": item["item_id"], "mode": mode}]},
            "preconditions": {"expected_fingerprint": doc["fingerprint"]}, "idempotency_key": key}


async def settle_batch(d, op_id):
    for _ in range(6):
        d.ops.db.execute("UPDATE operations SET next_run_at=0 WHERE operation_id=?", (op_id,))
        await settle_operations(d.ops)
        row = d.ops.get(op_id)
        if row["status"] in {"succeeded", "failed", "cancelled", "needs_attention"}:
            return row
        await asyncio.sleep(0)
    return d.ops.get(op_id)


@pytest.mark.parametrize("mode", [None, "default", "allow_all"])
async def test_fixed_bulk_children_own_answer_and_each_setting(served, mock, mode):
    d, port = served
    adopt(SID, agent_preset="codex-agent")
    pending(mock)
    d.fleet.config.host("h1").default_permission_mode = "allow_all"
    token, principal = caller(d)
    status, doc = await api.http(port, "POST", "/api/v1/approval-previews", tok=token, body={"host": "h1"})
    assert status == 200, doc
    assert not api.write_frames(mock) and not d.ops.list()["operations"]
    item = next(i for i in doc["items"] if i["session_id"] == SID)
    assert item["eligible"] and item["prompt"]["input"] == {"command": "pytest"}, doc
    status, accepted = await api.http(port, "POST", "/api/v1/operations", tok=token, body=request(doc, mode=mode))
    assert status == 202, accepted
    result = await settle_batch(d, accepted["operation"]["operation_id"])
    assert result["status"] == "succeeded" and result["result"]["all_succeeded"], result
    frames = api.write_frames(mock)
    expected = ["claude:resolve-permission"] + (["claude:set-codex-sandbox-mode", "claude:set-codex-approval-policy"] if mode else [])
    assert [f["channel"] for f in frames] == expected
    assert frames[0]["params"]["toolUseId"] == "permission-reviewed"
    assert frames[0]["params"]["result"]["dontAskAgain"] is True
    children = [o for o in d.ops.list()["operations"] if o["operation_id"] != result["operation_id"]]
    assert len(children) == (2 if mode else 1) and all(o["actor"] == principal.actor for o in children)
    replay, fresh = d.ops.create(principal, **request(doc, mode=mode))
    assert not fresh and replay["operation_id"] == result["operation_id"] and api.write_frames(mock) == frames


async def test_preview_skips_manual_and_confined_without_deferred_adoption(daemon, mock):
    _, principal = caller(daemon)
    pending(mock, api.MANUAL)
    pending(mock)
    adopt(SID, agent_preset="codex-agent", write_scope="confined", permission_raise_pending="allow_all")
    before = registry.get("h1", SID)
    doc = await bulk_approval.preview(daemon.ops, principal, {"host": "h1"})
    assert not any(i["eligible"] for i in doc["items"]), doc
    assert {i["code"] for i in doc["items"] if i["session_id"] in {SID, api.MANUAL}} == {"MANUAL_READ_ONLY", "CONFINEMENT_RAISE_REFUSED"}
    assert not api.write_frames(mock) and not daemon.ops.list()["operations"]
    assert registry.get("h1", SID) == before
    await daemon.fleet.close()


async def test_task_owned_bulk_uses_original_commands_and_frame_receipts(owned, mock):
    d, tid = owned
    sid = api.MANUAL
    pending(mock, sid)
    mock.metas[sid]["isStreaming"] = False
    _, principal = caller(d)
    doc = await bulk_approval.preview(d.ops, principal, {"host": "h1"})
    op, _ = d.ops.create(principal, **request(doc, mode="default"))
    result = await settle_batch(d, op["operation_id"])
    assert result["status"] == "succeeded" and result["result"]["all_succeeded"], result
    commands = d.journal.commands(tid)
    assert [c["kind"] for c in commands] == ["answer", "permissions"]
    assert all(c["status"] == "settled" for c in commands)
    assert [f["channel"] for f in api.write_frames(mock)] == ["claude:resolve-permission", "claude:set-permission-mode"]


@pytest.mark.parametrize("change", ["tool_id", "input", "owner", "retired", "cleanup", "cancel"])
async def test_last_frame_refuses_changes_during_guard_read(owned, mock, monkeypatch, change):
    d, tid = owned
    adopt(SID, agent_preset="codex-agent")
    pending(mock)
    _, principal = caller(d)
    doc = await bulk_approval.preview(d.ops, principal, {"host": "h1"})
    op, _ = d.ops.create(principal, **request(doc))
    client, original = d.fleet.client("h1"), d.fleet.client("h1").guard_read
    changed = False
    async def read(channel, params):
        nonlocal changed
        result = await original(channel, params)
        if channel == "claude:get-session-state" and not changed:
            changed = True
            if change in {"tool_id", "input"}:
                p = dict(result["pendingPermission"])
                p["toolUseId" if change == "tool_id" else "input"] = "replacement" if change == "tool_id" else {"command": "different"}
                result = {**result, "pendingPermission": p}
            elif change == "owner":
                registry.update("h1", SID, task_id=tid, role="lead")
            elif change == "retired":
                registry.update("h1", SID, status="retired")
            elif change == "cleanup":
                from bat_agent_connector import cleanup
                cleanup._mark({"resource_id": "fixture-session", "generation": "one", "host": "h1",
                               "kind": "session", "session_id": SID, "path": "/srv/demo"}, "op_cleanup", "reserved")
            else:
                d.ops.cancel(principal, op["operation_id"])
        return result
    monkeypatch.setattr(client, "guard_read", read)
    result = await settle_batch(d, op["operation_id"])
    assert changed and not api.write_frames(mock), result
    if change == "cancel":
        assert result["status"] == "cancelled", result
    else:
        assert result["status"] == "succeeded" and not result["result"]["all_succeeded"], result
    assert result["external_refs"]["bulk_items"][0]["answer"]["status"] in {"failed", "cancelled"}


@pytest.mark.parametrize("selection", [None, [], {}, [None], [{"item_id": [] , "mode": None}],
    [{"item_id": "missing", "mode": None}], [{"item_id": "ITEM", "mode": []}],
    [{"item_id": "ITEM", "mode": {}}], [{"item_id": "ITEM", "mode": None, "force": True}],
    [{"item_id": "ITEM", "mode": None}, {"item_id": "ITEM", "mode": None}]])
async def test_malformed_selections_refuse_before_operation(served, mock, selection):
    import json
    d, port = served
    adopt(SID, agent_preset="codex-agent")
    pending(mock)
    tok, principal = caller(d)
    doc = await bulk_approval.preview(d.ops, principal, {"host": "h1"})
    body = request(doc)
    body["params"]["selection"] = json.loads(json.dumps(selection).replace('"ITEM"', json.dumps(next(i["item_id"] for i in doc["items"] if i["eligible"]))))
    status, out = await api.http(port, "POST", "/api/v1/operations", tok=tok, body=body)
    assert status == 422 and out["error"]["code"] == "INVALID_PARAMS", out
    assert not d.ops.list()["operations"] and not api.write_frames(mock)


@pytest.mark.parametrize("change", ["credential", "scope", "expired", "tamper", "fingerprint", "registry"])
async def test_preview_binding_and_replay_authority(served, mock, monkeypatch, change):
    from bat_agent_connector.operations import OperationError
    d, port = served
    adopt(SID, agent_preset="codex-agent")
    pending(mock)
    tok, principal = caller(d)
    doc = await bulk_approval.preview(d.ops, principal, {"host": "h1"})
    body = request(doc)
    if change in {"credential", "scope"}:
        tok, other = caller(d, scopes=("operate",) if change == "scope" else ("observe", "operate"))
    elif change == "expired":
        # Patch this module's clock object, never the shared stdlib clock.
        from types import SimpleNamespace
        monkeypatch.setattr(bulk_approval, "time", SimpleNamespace(time=lambda: doc["expires_at"] + 1))
    elif change == "tamper":
        body["params"]["preview_token"] += "x"
    elif change == "fingerprint":
        body["preconditions"]["expected_fingerprint"] = "changed"
    else:
        registry.update("h1", SID, created_at="replacement-incarnation")
    status, out = await api.http(port, "POST", "/api/v1/operations", tok=tok, body=body)
    assert status in {403, 409}, out
    assert not d.ops.list()["operations"] and not api.write_frames(mock)
    if change in {"credential", "scope"}:
        op, _ = d.ops.create(principal, **body)
        for verb in ("replay", "resume", "cancel"):
            code = "FORBIDDEN" if other.actor == principal.actor else "CONTROLLER_MISMATCH"
            with pytest.raises(OperationError, match=code):
                if verb == "replay":
                    d.ops.create(other, **body)
                else:
                    getattr(d.ops, verb)(other, op["operation_id"])


async def test_accepted_replay_ignores_preview_expiry_and_later_policy(daemon, mock, monkeypatch):
    from types import SimpleNamespace
    adopt(SID, agent_preset="codex-agent")
    pending(mock)
    _, principal = caller(daemon)
    doc = await bulk_approval.preview(daemon.ops, principal, {"host": "h1"})
    op, _ = daemon.ops.create(principal, **request(doc))
    result = await settle_batch(daemon, op["operation_id"])
    monkeypatch.setattr(bulk_approval, "time", SimpleNamespace(time=lambda: doc["expires_at"] + 1))
    registry.update("h1", SID, status="retired")
    replay, fresh = daemon.ops.create(principal, **request(doc))
    assert not fresh and replay["operation_id"] == result["operation_id"]
    assert len(api.write_frames(mock)) == 1
    await daemon.fleet.close()


@pytest.mark.parametrize("reply", [False, None, "lost", "invoke_error"])
async def test_answer_ambiguous_ack_never_repicks_or_raises_permissions(daemon, mock, monkeypatch, reply):
    from bat_agent_connector.errors import ConnectionLost
    adopt(SID, agent_preset="codex-agent")
    pending(mock)
    _, principal = caller(daemon)
    doc = await bulk_approval.preview(daemon.ops, principal, {"host": "h1"})
    client, original = daemon.fleet.client("h1"), daemon.fleet.client("h1")._roundtrip
    async def broken(frame, timeout, **kwargs):
        out = await original(frame, timeout, **kwargs)
        if frame["channel"] == "claude:resolve-permission":
            if reply == "lost":
                raise ConnectionLost("lost answer ACK")
            if reply == "invoke_error":
                return {"type": "invoke-error", "id": frame["id"], "error": "unproven effect"}
            return {**out, "result": reply}
        return out
    monkeypatch.setattr(client, "_roundtrip", broken)
    op, _ = daemon.ops.create(principal, **request(doc, mode="default"))
    out = await settle_batch(daemon, op["operation_id"])
    child_id = next(iter(out["external_refs"]["bulk_children"].values()))
    assert daemon.ops.get(child_id)["status"] == "uncertain", out
    pending(mock, prompt_id="new-not-reviewed")
    for _ in range(6):
        daemon.ops.db.execute("UPDATE operations SET next_run_at=0 WHERE operation_id=?", (child_id,))
        out = await settle_batch(daemon, op["operation_id"])
    assert out["status"] == "needs_attention", out
    assert len(api.write_frames(mock)) == 1 and len(daemon.ops.list()["operations"]) == 2
    daemon.ops.cancel(principal, op["operation_id"])
    out = await settle_batch(daemon, op["operation_id"])
    assert out["status"] == "needs_attention" and out["cancel_requested"], out
    assert out["external_refs"]["bulk_items"][0]["answer"]["unproven"]
    assert len(api.write_frames(mock)) == 1
    await daemon.fleet.close()


async def test_pretransport_read_loss_rejects_original_task_command(owned, mock, monkeypatch):
    from bat_agent_connector.errors import ConnectionLost
    d, tid = owned
    pending(mock, api.MANUAL)
    _, principal = caller(d)
    doc = await bulk_approval.preview(d.ops, principal, {"host": "h1"})
    original = d.fleet.client("h1").guard_read
    async def broken(channel, params):
        if channel == "claude:get-session-state":
            raise ConnectionLost("lost identity read before answer")
        return await original(channel, params)
    monkeypatch.setattr(d.fleet.client("h1"), "guard_read", broken)
    op, _ = d.ops.create(principal, **request(doc))
    out = await settle_batch(d, op["operation_id"])
    assert out["status"] == "succeeded" and not out["result"]["all_succeeded"], out
    assert out["result"]["items"][0]["answer"]["code"] == "BULK_NOT_SENT"
    assert not api.write_frames(mock) and d.journal.commands(tid)[0]["status"] == "rejected"
    assert d.journal.get(tid)["state"] != "uncertain"


async def test_atomic_child_creation_survives_actual_restart(owned, mock, monkeypatch):
    d, _ = owned
    adopt(SID, agent_preset="codex-agent")
    pending(mock)
    _, principal = caller(d)
    doc = await bulk_approval.preview(d.ops, principal, {"host": "h1"})
    op, _ = d.ops.create(principal, **request(doc, mode="default"))
    original = d.ops._step_done
    def crash(op_id, name, *args, **kwargs):
        if name.startswith("bulk."):
            raise KeyboardInterrupt("process stopped before child transaction commit")
        return original(op_id, name, *args, **kwargs)
    monkeypatch.setattr(d.ops, "_step_done", crash)
    # Invoke the handler directly to model process interruption without killing pytest's event loop.
    from bat_agent_connector.operations import OpContext
    with pytest.raises(KeyboardInterrupt):
        await bulk_approval.run(OpContext(d.ops, d.ops._row(op["operation_id"])))
    assert len(d.ops.list()["operations"]) == 1 and not api.write_frames(mock)
    async with task_tests.restarted_daemon(d) as restarted:
        result = await settle_batch(restarted, op["operation_id"])
        assert result["status"] == "succeeded" and result["result"]["all_succeeded"], result
        assert len(restarted.ops.list()["operations"]) == 3 and len(api.write_frames(mock)) == 3


@pytest.mark.parametrize("first", ["http", "mcp", "cli"])
async def test_actual_transports_share_original_intent_and_no_key_is_independent(served, mock, monkeypatch, tmp_path, capsys, first):
    import json

    from bat_agent_connector import cli
    from bat_agent_connector.mcp_server import build_server
    from tests.test_interrupt_operations import mcp_result
    d, port = served
    adopt(SID, agent_preset="codex-agent")
    pending(mock)
    tok, _ = caller(d)
    monkeypatch.setenv("BATC_API_TOKEN", tok)
    monkeypatch.setenv("BATC_TASK_URL", f"http://127.0.0.1:{port}/rpc")
    monkeypatch.setattr(cli, "load_config", lambda _: d.fleet.config)
    server, fleet = build_server(d.fleet.config, principal_only=True)
    try:
        doc = await mcp_result(server, "approval_preview", {"host": "h1"})
        preview_file = tmp_path / "preview.json"
        preview_file.write_text(json.dumps(doc))
        body = request(doc)
        args = {"host": "h1", "confirm": True, **body["params"],
                "expected_fingerprint": doc["fingerprint"], "idempotency_key": body["idempotency_key"]}
        async def through(door):
            if door == "http":
                status, out = await api.http(port, "POST", "/api/v1/operations", tok=tok, body=body)
                assert status in {200, 202}, out
            elif door == "mcp":
                out = await mcp_result(server, "approve_pending", args)
            else:
                assert await asyncio.to_thread(cli.main, ["--json", "approve-pending", "h1", "--confirm",
                    "--preview-file", str(preview_file), "--selection", json.dumps(body["params"]["selection"]),
                    "--key", body["idempotency_key"]]) == 0
                out = json.loads(capsys.readouterr().out)
            return out["operation"]["operation_id"]
        op_id = await through(first)
        for door in ("http", "mcp", "cli"):
            assert await through(door) == op_id
        result = await settle_batch(d, op_id)
        assert result["result"]["all_succeeded"] and len(api.write_frames(mock)) == 1
        assert d.ops.get(op_id)["actor"] == "operator-dashboard"
        args.pop("idempotency_key")
        one = await mcp_result(server, "approve_pending", args)
        two = await mcp_result(server, "approve_pending", args)
        assert one["operation_id"] != two["operation_id"]
        assert all(o["idempotency_key"] is None and o["idempotency_enabled"] is False for o in (one, two))
        # Their explicit old prompt cannot silently select whatever is now pending.
        pending(mock, prompt_id="later-prompt")
        for new in (one, two):
            final = await settle_batch(d, new["operation_id"])
            assert final["status"] == "succeeded" and not final["result"]["all_succeeded"]
        assert len(api.write_frames(mock)) == 1
    finally:
        await fleet.close()


async def test_legacy_missing_preview_and_readonly_have_no_io_or_raw_fallback(served, mock, monkeypatch, tmp_path, capsys):
    from bat_agent_connector import cli
    from bat_agent_connector.mcp_server import build_server
    from tests.test_mcp_principal import call
    d, port = served
    tok, _ = caller(d)
    monkeypatch.setenv("BATC_API_TOKEN", tok)
    monkeypatch.setenv("BATC_TASK_URL", f"http://127.0.0.1:{port}/rpc")
    monkeypatch.setattr(cli, "load_config", lambda _: d.fleet.config)
    status, out = await api.http(port, "POST", "/rpc", tok=tok,
        body={"method": "approve_pending", "params": {"host": "h1", "confirm": True}})
    assert status == 400 and out["error"] == "BULK_PREVIEW_REQUIRED"
    server, fleet = build_server(d.fleet.config, principal_only=True)
    try:
        assert "BULK_PREVIEW_REQUIRED" in await call(server, "approve_pending", {"host": "h1", "confirm": True})
        code = await asyncio.to_thread(cli.main, ["--read-only", "--json", "approve-pending", "h1", "--confirm",
            "--preview-file", str(tmp_path / "must-not-be-read"), "--selection", "[]"])
        text = capsys.readouterr()
        assert code != 0 and "read-only" in (text.out + text.err).lower()
        monkeypatch.delenv("BATC_API_TOKEN")
        assert "BATC_API_TOKEN" in await call(server, "approval_preview", {"host": "h1"})
        assert not d.ops.list()["operations"] and not api.write_frames(mock)
    finally:
        await fleet.close()


@pytest.mark.parametrize("crash_at", ["answer", "permissions.sandbox", "permissions.approval"])
async def test_restart_reuses_child_ids_and_saved_ack_without_resend(owned, mock, monkeypatch, crash_at):
    d, _ = owned
    adopt(SID, agent_preset="codex-agent")
    pending(mock)
    _, principal = caller(d)
    doc = await bulk_approval.preview(d.ops, principal, {"host": "h1"})
    op, _ = d.ops.create(principal, **request(doc, mode="default"))
    original = d.ops._step_done
    def crash(op_id, name, *args, **kwargs):
        original(op_id, name, *args, **kwargs)
        if name == crash_at:
            raise asyncio.CancelledError("worker interrupted after durable ACK")
    monkeypatch.setattr(d.ops, "_step_done", crash)
    await d.ops._execute(op["operation_id"])
    answer_id = next(iter(d.ops.get(op["operation_id"])["external_refs"]["bulk_children"].values()))
    if crash_at == "answer":
        with pytest.raises(asyncio.CancelledError):
            await d.ops._execute(answer_id)
    else:
        await d.ops._execute(answer_id)
        await d.ops._execute(op["operation_id"])
        permission_id = next(v for k, v in d.ops.get(op["operation_id"])["external_refs"]["bulk_children"].items() if k.endswith(".permissions"))
        with pytest.raises(asyncio.CancelledError):
            await d.ops._execute(permission_id)
    before = dict(d.ops.get(op["operation_id"])["external_refs"]["bulk_children"])
    assert before and api.write_frames(mock)
    async with task_tests.restarted_daemon(d) as restarted:
        result = await settle_batch(restarted, op["operation_id"])
        assert result["status"] == "succeeded" and result["result"]["all_succeeded"], result
        assert before.items() <= result["external_refs"]["bulk_children"].items()
        assert len(restarted.ops.list()["operations"]) == 3
        assert [f["channel"] for f in api.write_frames(mock)] == ["claude:resolve-permission", "claude:set-codex-sandbox-mode", "claude:set-codex-approval-policy"]


async def test_guard_read_does_not_reenter_single_slot_and_audit_keeps_actor(daemon, mock, monkeypatch):
    from bat_agent_connector.safety import Audit
    adopt(SID, agent_preset="codex-agent")
    pending(mock)
    _, principal = caller(daemon)
    doc = await bulk_approval.preview(daemon.ops, principal, {"host": "h1"})
    monkeypatch.setattr(daemon.fleet.client("h1"), "_sem", asyncio.Semaphore(1))
    op, _ = daemon.ops.create(principal, **request(doc, mode="default"))
    out = await asyncio.wait_for(settle_batch(daemon, op["operation_id"]), 10)
    assert out["result"]["all_succeeded"]
    entries = Audit(daemon.fleet.config.safety)._tail()
    assert all(e["actor"] == principal.actor for e in entries)
    assert len([e for e in entries if e["phase"] == "attempt"]) == 3
    await settle_batch(daemon, op["operation_id"])
    assert Audit(daemon.fleet.config.safety)._tail() == entries
    await daemon.fleet.close()


@pytest.mark.parametrize("case", ["ambiguous", "large", "unloaded"])
async def test_preview_does_not_offer_unprovable_or_truncated_prompt(daemon, mock, case):
    adopt(api.MANUAL)
    pending(mock, api.MANUAL)
    if case == "ambiguous":
        mock.states[api.MANUAL]["pendingAskUser"] = {"toolUseId": "ask-other"}
    elif case == "large":
        mock.states[api.MANUAL]["pendingPermission"]["input"] = {"text": "x" * 8193}
    else:
        mock.metas[api.MANUAL]["cwd"] = None
    _, principal = caller(daemon)
    doc = await bulk_approval.preview(daemon.ops, principal, {"host": "h1"})
    item = next(i for i in doc["items"] if i["session_id"] == api.MANUAL)
    assert not item["eligible"] and "prompt" not in item
    assert item["code"] == {"ambiguous": "BULK_PENDING_AMBIGUOUS", "large": "BULK_PROMPT_TOO_LARGE", "unloaded": "BULK_RUNTIME_UNPROVEN"}[case]
    if case == "unloaded":
        assert not any(i["channel"] == "claude:get-session-state" and i["params"].get("sessionId") == api.MANUAL for i in mock.invokes)
    assert not api.write_frames(mock)
    await daemon.fleet.close()


async def test_outgoing_preview_bound_refuses_unusable_token(daemon, mock, monkeypatch):
    from bat_agent_connector.operations import OperationError
    adopt(SID, agent_preset="codex-agent")
    pending(mock)
    _, principal = caller(daemon)
    monkeypatch.setattr(bulk_approval, "MAX_TOKEN", 64)
    with pytest.raises(OperationError, match="BULK_PREVIEW_TOO_LARGE"):
        await bulk_approval.preview(daemon.ops, principal, {"host": "h1"})
    assert not daemon.ops.list()["operations"] and not api.write_frames(mock)
    await daemon.fleet.close()


@pytest.mark.parametrize("change", ["pause", "version", "host_policy", "parent_cancel"])
async def test_permission_second_frame_keeps_partial_evidence_on_fresh_refusal(owned, mock, monkeypatch, change):
    d, tid = owned
    adopt(SID, agent_preset="codex-agent", task_id=tid, role="lead")
    d.journal.db.execute("UPDATE tasks SET session_id=? WHERE task_id=?", (SID, tid))
    d.fleet.config.host("h1").default_permission_mode = "allow_all"
    pending(mock)
    _, principal = caller(d)
    doc = await bulk_approval.preview(d.ops, principal, {"host": "h1"})
    op, _ = d.ops.create(principal, **request(doc, mode="allow_all"))
    original = d.fleet.client("h1").guard_read
    changed = False
    async def read(channel, params):
        nonlocal changed
        out = await original(channel, params)
        if channel == "claude:get-session-meta" and len(api.write_frames(mock)) == 2 and not changed:
            changed = True
            if change == "host_policy":
                d.fleet.config.host("h1").default_permission_mode = "default"
            elif change == "parent_cancel":
                d.ops.cancel(principal, op["operation_id"])
            else:
                d.journal.pause(tid)
                if change == "version":
                    d.journal.resume(tid)
        return out
    monkeypatch.setattr(d.fleet.client("h1"), "guard_read", read)
    out = await settle_batch(d, op["operation_id"])
    assert changed and len(api.write_frames(mock)) == 2, out
    item = out["external_refs"]["bulk_items"][0]
    assert item["answer"]["acknowledged"] and not item["complete"]
    assert item["permissions"]["status"] in {"failed", "cancelled"}, out
    assert item["permissions"]["permission_frames"][0]["acknowledged"] is True
    commands = d.journal.commands(tid)
    assert [c["status"] for c in commands] == ["settled", "uncertain"]
    task = d.journal.get(tid)
    assert task["state"] == ("accepted" if change in {"pause", "version"} else "uncertain")
    assert bool(task["paused"]) is (change == "pause")


async def test_independent_items_finish_while_unknown_item_keeps_its_identity(daemon, mock, monkeypatch):
    adopt(SID, agent_preset="codex-agent")
    adopt(api.MANUAL)
    pending(mock)
    pending(mock, api.MANUAL)
    _, principal = caller(daemon)
    doc = await bulk_approval.preview(daemon.ops, principal, {"host": "h1"})
    body = request(doc)
    body["params"]["selection"] = [{"item_id": i["item_id"], "mode": None} for i in doc["items"] if i["eligible"]]
    original = daemon.fleet.client("h1")._roundtrip
    async def false_ack(frame, timeout, **kwargs):
        out = await original(frame, timeout, **kwargs)
        if frame["channel"] == "claude:resolve-permission" and frame["params"]["sessionId"] == SID:
            return {**out, "result": False}
        return out
    monkeypatch.setattr(daemon.fleet.client("h1"), "_roundtrip", false_ack)
    op, _ = daemon.ops.create(principal, **body)
    out = await settle_batch(daemon, op["operation_id"])
    rows = out["external_refs"]["bulk_items"]
    assert len(rows) == 2 and sum(i["complete"] for i in rows) == 1
    assert len(api.write_frames(mock)) == 2 and len(daemon.ops.list()["operations"]) == 3
    await daemon.fleet.close()


async def test_linked_child_controls_require_original_actor_and_combined_scope(daemon, mock):
    from bat_agent_connector.operations import OperationError
    adopt(SID, agent_preset="codex-agent")
    pending(mock)
    _, principal = caller(daemon)
    doc = await bulk_approval.preview(daemon.ops, principal, {"host": "h1"})
    op, _ = daemon.ops.create(principal, **request(doc))
    # Create durable child without dispatching it, to exercise accepted control checks.
    from bat_agent_connector.operations import OpContext, Wait
    with pytest.raises(Wait):
        await bulk_approval.run(OpContext(daemon.ops, daemon.ops._row(op["operation_id"])))
    child_id = next(iter(daemon.ops.get(op["operation_id"])["external_refs"]["bulk_children"].values()))
    child = daemon.ops.get(child_id)
    for other in (api_auth.Principal(principal.actor, frozenset({"operate"})),
                  api_auth.Principal("another", frozenset({"observe", "operate", "manage"}))):
        # Another actor's key namespace creates a distinct ordinary action; it cannot
        # replay this child. Existing-child controls still require the original actor.
        for verb in (("replay", "cancel", "resume") if other.actor == principal.actor else ("cancel", "resume")):
            code = "FORBIDDEN" if other.actor == principal.actor else "CONTROLLER_MISMATCH"
            with pytest.raises(OperationError, match=code):
                if verb == "replay":
                    daemon.ops.create(other, **{k: child[k] for k in ("action", "target", "params", "preconditions", "idempotency_key")})
                else:
                    getattr(daemon.ops, verb)(other, child_id)
    daemon.ops.cancel(principal, op["operation_id"])
    out = await settle_batch(daemon, op["operation_id"])
    assert out["status"] == "cancelled" and not api.write_frames(mock)
    with pytest.raises(OperationError, match="BULK_CANCEL_REQUESTED"):
        daemon.ops.resume(principal, child_id)
    await daemon.fleet.close()


async def test_standalone_initial_read_loss_is_proven_unsent(daemon, mock, monkeypatch):
    from bat_agent_connector import service
    from bat_agent_connector.errors import ConnectionLost
    adopt(SID, agent_preset="codex-agent")
    pending(mock)
    _, principal = caller(daemon)
    doc = await bulk_approval.preview(daemon.ops, principal, {"host": "h1"})
    async def broken(*args, **kwargs):
        raise ConnectionLost("initial resolution read failed")
    monkeypatch.setattr(service, "_resolve_session", broken)
    op, _ = daemon.ops.create(principal, **request(doc))
    out = await settle_batch(daemon, op["operation_id"])
    item = out["result"]["items"][0]
    assert out["status"] == "succeeded" and item["answer"]["code"] == "BULK_NOT_SENT", out
    assert not item["answer"]["unproven"] and not api.write_frames(mock)
    await daemon.fleet.close()


@pytest.mark.parametrize("second_due,expected", [(120, 30), (12, 12), (None, 1)])
async def test_parent_wait_follows_earliest_child_deadline_with_bounds(daemon, mock, second_due, expected):
    import time

    from bat_agent_connector.operations import OpContext, Wait
    adopt(SID, agent_preset="codex-agent")
    adopt(api.MANUAL)
    pending(mock)
    pending(mock, api.MANUAL)
    _, principal = caller(daemon)
    doc = await bulk_approval.preview(daemon.ops, principal, {"host": "h1"})
    body = request(doc)
    body["params"]["selection"] = [{"item_id": i["item_id"], "mode": None} for i in doc["items"] if i["eligible"]]
    op, _ = daemon.ops.create(principal, **body)
    with pytest.raises(Wait):
        await bulk_approval.run(OpContext(daemon.ops, daemon.ops._row(op["operation_id"])))
    children = list(daemon.ops.get(op["operation_id"])["external_refs"]["bulk_children"].values())
    for child, delay in zip(children, (600, second_due)):
        if delay is not None:
            daemon.ops._transition(child, "running")
            daemon.ops._transition(child, "uncertain", next_run_at=time.time() + delay)
    with pytest.raises(Wait) as waiting:
        await bulk_approval.run(OpContext(daemon.ops, daemon.ops._row(op["operation_id"])))
    assert expected - 1 <= waiting.value.delay_s <= expected
    assert len(daemon.ops.list()["operations"]) == 3 and not api.write_frames(mock)
    await daemon.fleet.close()


@pytest.mark.parametrize("control", ["cancel", "resume"])
async def test_explicit_parent_controls_clear_wait_deadline(daemon, mock, control):
    import time

    from bat_agent_connector.operations import OpContext, Wait
    adopt(SID, agent_preset="codex-agent")
    pending(mock)
    _, principal = caller(daemon)
    doc = await bulk_approval.preview(daemon.ops, principal, {"host": "h1"})
    op, _ = daemon.ops.create(principal, **request(doc))
    with pytest.raises(Wait):
        await bulk_approval.run(OpContext(daemon.ops, daemon.ops._row(op["operation_id"])))
    child = next(iter(daemon.ops.get(op["operation_id"])["external_refs"]["bulk_children"].values()))
    daemon.ops._transition(child, "running")
    daemon.ops._transition(child, "uncertain", next_run_at=time.time() + 600)
    scheduled_after = time.time()
    await daemon.ops._execute(op["operation_id"])
    assert daemon.ops.get(op["operation_id"])["next_run_at"] >= scheduled_after + 28
    if control == "resume":
        daemon.ops._transition(child, "needs_attention")
        await daemon.ops._execute(op["operation_id"])
        assert daemon.ops.get(op["operation_id"])["status"] == "needs_attention"
    result = getattr(daemon.ops, control)(principal, op["operation_id"])
    assert result["status"] == "running" and result["next_run_at"] <= time.time()
    if control == "resume":
        resumed = daemon.ops.get(child)
        assert resumed["status"] == "running" and resumed["next_run_at"] <= time.time()
    else:
        result = await settle_batch(daemon, op["operation_id"])
        assert result["status"] == "cancelled" and daemon.ops.get(child)["status"] == "cancelled"
        assert not api.write_frames(mock)
    await daemon.fleet.close()
