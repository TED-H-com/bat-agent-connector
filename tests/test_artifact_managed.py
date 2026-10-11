"""B2 uses actual bounded temporary bytes/Git and central execution records, no live host."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

import pytest

from bat_agent_connector import api_auth, artifacts, cli, registry
from bat_agent_connector import artifact_managed as managed
from bat_agent_connector.operations import AmbiguousOutcome, OperationError
from bat_agent_connector.task_daemon import TaskDaemon
from tests import test_api_v1 as api
from tests import test_artifact_capture as manual
from tests.conftest import controller_scope
from tests.test_artifact_capture import finish, principal
from tests.test_artifacts import PERSON, action, continuation, make_checkpoint
from tests.test_checkpoints import bat_writes, git

daemon, human, served = manual.daemon, manual.human, api.served


@pytest.fixture
async def execution(daemon, mock):
    cp = await make_checkpoint(daemon)
    op = await continuation(daemon, cp, [])
    assert op["status"] == "succeeded", op
    sid, path = op["result"]["session_id"], Path(op["result"]["worktree_path"])
    mock.metas[sid]["isStreaming"] = False
    (path / "result.bin").write_bytes(b"result\x00\xff\n")
    return op, sid, path


def source_request(execution, **changes):
    op, sid, _ = execution
    return {"host": "h1", "session_id": sid, "relative_path": "result.bin",
            "execution_operation_id": op["operation_id"], **changes}


async def preview(d, person, execution, **changes):
    return await managed.preview(d.ops, person, source_request(execution, **changes))


def submit(d, person, pv, *, key="managed-one", name="artifact.capture.managed"):
    return d.ops.create(person, action=name, target={"preview_id": pv["preview_id"]},
                        params={"preview_token": pv["preview_token"]},
                        preconditions={"expected_fingerprint": pv["fingerprint"]}, idempotency_key=key)[0]


def accept(d, person, op, pv, *, key="accept-one", **changes):
    ref = op["result"]
    return d.ops.create(person, action="artifact.accept", target={"artifact_id": ref["artifact_id"], "revision": ref["revision"]},
                        params={"digest": ref["digest"], "source_fingerprint": pv["fingerprint"],
                                "receipt": "Reviewed the exact binary result against the requirement.", **changes},
                        idempotency_key=key)[0]


async def test_managed_capture_and_accept_exact_bytes_without_execution_or_completion(daemon, execution, mock):
    person, _ = principal(daemon, scopes=("observe", "manage", "approve"))
    writes = len(bat_writes(mock))
    pv = await preview(daemon, person, execution)
    op = await finish(daemon, submit(daemon, person, pv))
    assert op["status"] == "succeeded", op
    result = op["result"]
    assert daemon.artifact_store.read_content(result["artifact_id"], 1) == b"result\x00\xff\n"
    row = artifacts.get(daemon.journal.db, result["artifact_id"], 1)
    assert row["source"]["kind"] == "managed_capture"
    assert row["source"]["source"]["lineage"]["execution_operation_id"] == execution[0]["operation_id"]
    approved = await finish(daemon, accept(daemon, person, op, pv))
    assert approved["status"] == "succeeded", approved
    assert approved["result"]["source_commit"] == git(execution[2], "rev-parse", "HEAD")
    assert approved["result"]["meaning"] == "artifact_revision_review"
    assert accept(daemon, person, op, pv)["operation_id"] == approved["operation_id"]
    assert len(artifacts.get(daemon.journal.db, result["artifact_id"], 1)["acceptances"]) == 1
    assert not daemon.journal.db.execute("SELECT 1 FROM tasks").fetchone()
    assert not daemon.journal.db.execute("SELECT 1 FROM work_items").fetchone()
    assert len(bat_writes(mock)) == writes


@pytest.mark.parametrize("change", ["unknown", "other_action", "uncertain", "not_accepted", "wrong_sid", "created_later"])
async def test_client_execution_selector_requires_central_receipts(daemon, execution, change):
    person, _ = principal(daemon)
    oid = execution[0]["operation_id"]
    if change == "unknown":
        oid = "op_" + "a" * 32
    elif change == "other_action":
        oid = (await action(daemon, "project.create", params={"name": "unrelated"}))["operation_id"]
    elif change == "uncertain":
        daemon.journal.db.execute("UPDATE operations SET status='uncertain' WHERE operation_id=?", (oid,))
    elif change == "not_accepted":
        daemon.journal.db.execute("UPDATE operation_steps SET response='{}' WHERE operation_id=? AND name='send'", (oid,))
    elif change == "wrong_sid":
        daemon.journal.db.execute("UPDATE operation_steps SET request='{}' WHERE operation_id=? AND name='session.start'", (oid,))
    else:
        registry.update("h1", execution[1], created_at=execution[0]["created_at"] + 999)
    with pytest.raises(OperationError, match="ARTIFACT_LINEAGE_UNPROVEN"):
        await preview(daemon, person, execution, execution_operation_id=oid)
    assert not daemon.ops.context["artifact_host"].calls
    assert not daemon.journal.db.execute("SELECT 1 FROM artifact_uploads").fetchone()


@pytest.mark.parametrize("change", ["file", "head", "owner", "cwd", "retired", "runtime", "tab"])
async def test_managed_fixed_preview_refuses_source_changes(daemon, execution, mock, change):
    person, _ = principal(daemon)
    pv = await preview(daemon, person, execution)
    _, sid, path = execution
    if change == "file":
        (path / "result.bin").write_bytes(b"changed")
    elif change == "head":
        git(path, "update-ref", "HEAD", "HEAD~1")
    elif change == "owner":
        registry.update("h1", sid, task_id="new-owner", role="lead")
    elif change == "cwd":
        mock.metas[sid]["cwd"] = str(path.parent)
    elif change == "retired":
        registry.update("h1", sid, status="stopped")
    elif change == "runtime":
        mock.metas[sid]["sdkSessionId"] = "replacement"
    else:
        mock.ws_doc["terminals"].append({"id": sid, "agentPreset": "claude-code", "cwd": str(path.parent)})
    op = await finish(daemon, submit(daemon, person, pv))
    assert op["status"] == "failed", op
    assert op["error_code"] in {"SOURCE_CHANGED", "ARTIFACT_LINEAGE_UNPROVEN"}
    assert not list(daemon.artifact_store.root.glob("revisions/*/*/content"))


async def test_manual_and_managed_previews_cannot_cross_actions(daemon, execution):
    from tests.test_artifact_capture import preview as manual_preview
    person, _ = principal(daemon)
    manual = await manual_preview(daemon, person)
    managed_pv = await preview(daemon, person, execution)
    for pv, name in [(manual, "artifact.capture.managed"), (managed_pv, "artifact.capture")]:
        with pytest.raises(OperationError, match="PREVIEW_MISMATCH"):
            submit(daemon, person, pv, name=name)


@pytest.mark.parametrize("verb", ["replay", "resume", "cancel"])
async def test_managed_capture_rechecks_original_credential_and_combined_scope(daemon, execution, verb):
    person, _ = principal(daemon)
    pv = await preview(daemon, person, execution)
    op = submit(daemon, person, pv)
    for scopes in [("manage",), ("observe", "manage")]:
        other, _ = principal(daemon, scopes=scopes)
        with pytest.raises(OperationError, match="FORBIDDEN"):
            if verb == "replay":
                submit(daemon, other, pv)
            else:
                getattr(daemon.ops, verb)(other, op["operation_id"])


@pytest.mark.parametrize("phase", ["receive", "publish"])
async def test_managed_capture_restart_keeps_exact_receipt_without_reading_advanced_source(daemon, execution, monkeypatch, phase):
    person, _ = principal(daemon)
    pv = await preview(daemon, person, execution)
    method = "receive_reserved" if phase == "receive" else "publish"
    real = getattr(daemon.artifact_store, method)
    async def receive(*args):
        await real(*args)
        raise AmbiguousOutcome("lost received receipt")
    def publish(*args):
        real(*args)
        raise AmbiguousOutcome("lost publish receipt")
    monkeypatch.setattr(daemon.artifact_store, method, receive if phase == "receive" else publish)
    op = await finish(daemon, submit(daemon, person, pv))
    assert op["status"] == "uncertain", op
    await daemon.artifact_store.close_reaper()
    config, path = daemon.fleet.config, daemon.journal.path
    daemon.journal.close()
    (execution[2] / "result.bin").unlink()
    reopened = TaskDaemon(config, path)
    try:
        class NoRead:
            async def capture(self, *args):
                raise AssertionError("completed capture must not read or dispatch")
        reopened.ops.context["artifact_host"] = NoRead()
        reopened.journal.db.execute("UPDATE operations SET next_run_at=0 WHERE operation_id=?", (op["operation_id"],))
        done = await finish(reopened, op)
        assert done["status"] == "succeeded", done
        assert reopened.artifact_store.read_content(done["result"]["artifact_id"], 1) == b"result\x00\xff\n"
        assert len(list(reopened.artifact_store.root.glob("revisions/*/*/content"))) == 1
    finally:
        await reopened.artifact_store.close_reaper()
        await reopened.fleet.close()
        await reopened.inventory.close()
        reopened.journal.close()


async def test_acceptance_checks_original_bytes_but_not_later_live_source(daemon, execution):
    person, _ = principal(daemon, scopes=("observe", "manage", "approve"))
    pv = await preview(daemon, person, execution)
    op = await finish(daemon, submit(daemon, person, pv))
    (execution[2] / "result.bin").unlink()
    approved = await finish(daemon, accept(daemon, person, op, pv))
    assert approved["status"] == "succeeded", approved
    original = daemon.artifact_store.content_path(op["result"]["artifact_id"], 1)
    original.chmod(0o600)
    original.write_bytes(b"corrupt")
    second = await finish(daemon, accept(daemon, person, op, pv, key="second-review"))
    assert second["status"] == "failed" and second["error_code"] == "ARTIFACT_CONTENT_UNAVAILABLE", second
    assert accept(daemon, person, op, pv)["result"] == approved["result"]
    assert len(artifacts.get(daemon.journal.db, op["result"]["artifact_id"], 1)["acceptances"]) == 1


@pytest.mark.parametrize("field,value", [("digest", "0" * 64), ("source_fingerprint", "0" * 64),
                                        ("receipt", ""), ("receipt", {}), ("digest", [])])
async def test_accept_rejects_non_exact_or_malformed_reviews(daemon, execution, field, value):
    person, _ = principal(daemon, scopes=("observe", "manage", "approve"))
    pv = await preview(daemon, person, execution)
    op = await finish(daemon, submit(daemon, person, pv))
    with pytest.raises(OperationError):
        accept(daemon, person, op, pv, **{field: value})
    assert not daemon.journal.db.execute("SELECT 1 FROM artifact_acceptances").fetchone()


async def test_accept_replay_does_not_lose_approve_requirement(daemon, execution):
    person, token = principal(daemon, scopes=("observe", "manage", "approve"))
    pv = await preview(daemon, person, execution)
    op = await finish(daemon, submit(daemon, person, pv))
    approved = await finish(daemon, accept(daemon, person, op, pv))
    daemon.journal.db.execute("UPDATE api_principals SET scopes=? WHERE token_hash=?",
                             (json.dumps(["manage", "observe"]), person.credential_id))
    reduced = api_auth.authenticate(daemon.journal.db, token, daemon._admin_token)
    with pytest.raises(OperationError, match="FORBIDDEN"):
        accept(daemon, reduced, op, pv)
    for verb in ("resume", "cancel"):
        with pytest.raises(OperationError, match="FORBIDDEN"):
            getattr(daemon.ops, verb)(reduced, approved["operation_id"])


@pytest.mark.parametrize("ownership", ["standalone", "task", "reserved_task", "missing_event", "old_event"])
async def test_send_lineage_uses_real_central_send_and_original_command(daemon, execution, ownership):
    task_owned = ownership != "standalone"
    sid = execution[1]
    tid = None
    if task_owned:
        # The task and its existing execution carrier belong to the same caller.
        with controller_scope(daemon.journal, PERSON):
            task = daemon.journal.submit(project="fixture", host="h1", workspace="demo-project", original_words="review result",
                                         engine="goose", idempotency_key="managed-task")
        assert task["controller_authority"] == registry.get("h1", sid)["controller_authority"]
        tid = task["task_id"]
        daemon.journal.change(tid, "dispatching", fields={"session_id": sid})
        daemon.journal.change(tid, "accepted")
        registry.update("h1", sid, task_id=tid, role="lead")
    sent = await action(daemon, "session.send", {"host": "h1", "session_id": sid}, {"text": "Read the existing result"})
    assert sent["status"] == "succeeded", sent
    if ownership == "reserved_task":
        # reserve_failover creates the successor's send before its start. Preserve
        # this real send/ACK and model that earlier reservation timestamp only.
        daemon.journal.db.execute("UPDATE commands SET created_at=? WHERE command_id=?",
            (registry.get("h1", sid)["created_at"] - 1, sent["external_refs"]["command_id"]))
    person, _ = principal(daemon, scopes=("observe", "manage", "approve"))
    if ownership in {"missing_event", "old_event"}:
        cid = sent["external_refs"]["command_id"]
        if ownership == "missing_event":
            daemon.journal.db.execute("DELETE FROM events WHERE task_id=? AND kind IN ('command_accepted','command_settled')",
                                     (tid,))
        else:
            daemon.journal.db.execute("UPDATE events SET created_at=? WHERE task_id=? AND kind IN ('command_accepted','command_settled')",
                                     (registry.get("h1", sid)["created_at"] - 1, tid))
        assert daemon.journal.command_get(cid)["status"] == "accepted"
        with pytest.raises(OperationError, match="ARTIFACT_LINEAGE_UNPROVEN"):
            await preview(daemon, person, execution, execution_operation_id=sent["operation_id"])
        assert not daemon.ops.context["artifact_host"].calls
        assert not daemon.journal.db.execute("SELECT 1 FROM artifact_uploads").fetchone()
        return
    pv = await preview(daemon, person, execution, execution_operation_id=sent["operation_id"])
    if task_owned:
        task_before = daemon.journal.get(tid)
        selector = {"task_id": tid, "command_id": sent["external_refs"]["command_id"]}
        request = {k: v for k, v in source_request(execution).items() if k != "execution_operation_id"}
        pv = await managed.preview(daemon.ops, person, {**request, **selector})
        assert pv["source"]["lineage"]["command_id"] == selector["command_id"]
        assert pv["source"]["lineage"]["accepted_at"] >= registry.get("h1", sid)["created_at"]
        assert pv["source"]["lineage"]["accepted_event_id"] > 0
    op = await finish(daemon, submit(daemon, person, pv))
    assert op["status"] == "succeeded", op
    approved = await finish(daemon, accept(daemon, person, op, pv))
    assert approved["status"] == "succeeded", approved
    if task_owned:
        assert daemon.journal.get(tid) == task_before
        daemon.journal.command_status(selector["command_id"], "uncertain")
        with pytest.raises(OperationError, match="ARTIFACT_LINEAGE_UNPROVEN"):
            await managed.preview(daemon.ops, person, {**request, **selector})
        assert artifacts.get(daemon.journal.db, op["result"]["artifact_id"], 1)["acceptances"]


async def test_acceptance_of_old_revision_does_not_follow_latest_or_accept_upload(daemon, execution):
    from tests.test_artifacts import upload
    person, _ = principal(daemon, scopes=("observe", "manage", "approve"))
    pv = await preview(daemon, person, execution)
    op = await finish(daemon, submit(daemon, person, pv))
    aid = op["result"]["artifact_id"]
    newest = await upload(daemon, b"separate later revision", target={"artifact_id": aid}, pre={"expected_latest_revision": 1})
    assert newest["revision"] == 2
    approved = await finish(daemon, accept(daemon, person, op, pv))
    assert approved["status"] == "succeeded" and approved["result"]["revision"] == 1
    with pytest.raises(OperationError, match="ARTIFACT_LINEAGE_UNPROVEN"):
        accept(daemon, person, {"result": newest}, pv, key="not-managed")
    assert not artifacts.get(daemon.journal.db, aid, 2)["acceptances"]


async def test_accept_receipt_crash_reopen_replays_without_bytes_or_duplicate_event(daemon, execution):
    person, _ = principal(daemon, scopes=("observe", "manage", "approve"))
    pv = await preview(daemon, person, execution)
    captured = await finish(daemon, submit(daemon, person, pv))
    real = managed.run_accept
    async def lost(ctx):
        await real(ctx)
        raise asyncio.CancelledError("process interruption after committed local receipt")
    from dataclasses import replace
    daemon.ops.actions["artifact.accept"] = replace(daemon.ops.actions["artifact.accept"], run=lost)
    op = accept(daemon, person, captured, pv)
    with pytest.raises(asyncio.CancelledError):
        await daemon.ops._execute(op["operation_id"])
    assert daemon.ops.get(op["operation_id"])["status"] == "running"
    assert daemon.journal.db.execute("SELECT count(*) FROM artifact_acceptances").fetchone()[0] == 1
    daemon.artifact_store.content_path(captured["result"]["artifact_id"], 1).unlink()
    await daemon.artifact_store.close_reaper()
    config, path = daemon.fleet.config, daemon.journal.path
    daemon.journal.close()
    reopened = TaskDaemon(config, path)
    try:
        reopened.journal.db.execute("UPDATE operations SET next_run_at=0 WHERE operation_id=?", (op["operation_id"],))
        done = await finish(reopened, op)
        assert done["status"] == "succeeded", done
        assert reopened.journal.db.execute("SELECT count(*) FROM artifact_acceptances").fetchone()[0] == 1
        assert reopened.journal.db.execute("SELECT count(*) FROM api_events WHERE kind='artifact.accepted'").fetchone()[0] == 1
    finally:
        await reopened.artifact_store.close_reaper()
        await reopened.fleet.close()
        await reopened.inventory.close()
        reopened.journal.close()


@pytest.mark.parametrize("field,value", [("execution_operation_id", []), ("task_id", {}), ("commit", "0" * 40),
                                        ("lineage", {}), ("relative_path", "../outside")])
async def test_managed_preview_rejects_malformed_and_self_claimed_evidence(daemon, execution, field, value):
    person, _ = principal(daemon)
    with pytest.raises(OperationError):
        await preview(daemon, person, execution, **{field: value})
    assert not daemon.ops.context["artifact_host"].calls


async def test_http_rpc_scopes_and_fixed_managed_envelope(served, execution):
    d, port = served
    person, token = principal(d, scopes=("observe", "manage", "approve"))
    request = source_request(execution)
    url = "/api/v1/artifact-managed-capture-previews"
    assert (await api.http(port, "POST", url, body=request))[0] == 401
    assert (await api.http(port, "POST", url, tok=api.token(d, "manager", "manage"), body=request))[0] == 403
    code, out = await api.http(port, "POST", url, tok=token, body=request)
    assert code == 200, out
    pv = out["preview"]
    body = {"action": "artifact.capture.managed", "target": {"preview_id": pv["preview_id"]},
            "params": {"preview_token": pv["preview_token"]}, "preconditions": {"expected_fingerprint": pv["fingerprint"]}}
    code, out = await api.http(port, "POST", "/api/v1/operations", tok=token, body=body,
                               headers={"Idempotency-Key": "http-managed"})
    assert code == 202, out
    op = await finish(d, out["operation"])
    assert op["status"] == "succeeded", op
    code, out = await api.http(port, "POST", "/rpc", tok=token, body={"method": "artifact_managed_capture_preview", "params": request})
    assert code == 200 and "preview_token" in out["result"]["preview"], out
    manage_only = api.token(d, "not-approver", "manage", "observe")
    ref = op["result"]
    acceptance = {"action": "artifact.accept", "target": {"artifact_id": ref["artifact_id"], "revision": 1},
                  "params": {"digest": ref["digest"], "source_fingerprint": pv["fingerprint"], "receipt": "reviewed exact result"}}
    code, _ = await api.http(port, "POST", "/api/v1/operations", tok=manage_only, body=acceptance,
                             headers={"Idempotency-Key": "forbidden-accept"})
    assert code == 403 and not d.journal.db.execute("SELECT 1 FROM artifact_acceptances").fetchone()
    code, out = await api.http(port, "POST", "/api/v1/operations", tok=token, body=acceptance,
                               headers={"Idempotency-Key": "http-accept"})
    assert code == 202, out
    assert (await finish(d, out["operation"]))["status"] == "succeeded"
    _, caps = await d.api.capabilities(api_auth.Principal("manager", frozenset({"manage"})))
    assert not next(a for a in caps["actions"] if a["action"] == "artifact.capture.managed")["allowed"]


async def test_mcp_and_cli_use_same_central_actions_and_no_mcp_admin_fallback(served, execution, monkeypatch, tmp_path, capsys):
    from bat_agent_connector.mcp_server import build_server
    from tests.test_mcp_principal import call
    d, port = served
    person, token = principal(d, scopes=("observe", "manage", "approve"))
    monkeypatch.setenv("BATC_TASK_URL", f"http://127.0.0.1:{port}/rpc")
    monkeypatch.delenv("BATC_API_TOKEN", raising=False)
    server, fleet = build_server(d.fleet.config, principal_only=True)
    try:
        assert "BATC_API_TOKEN" in await call(server, "artifact_managed_capture_preview", source_request(execution))
        monkeypatch.setenv("BATC_API_TOKEN", token)
        assert "preview_token" in await call(server, "artifact_managed_capture_preview", source_request(execution))
        pv = await preview(d, person, execution)
        params = {"preview_id": pv["preview_id"], "preview_token": pv["preview_token"], "fingerprint": pv["fingerprint"],
                  "idempotency_key": "mcp-managed", "confirm": True}
        assert "artifact.capture.managed" in await call(server, "artifact_capture_managed", params)
        op = d.ops.list(actor=person.actor)["operations"][0]
        op = await finish(d, op)
        assert op["status"] == "succeeded", op
        ref = op["result"]
        params = {k: ref[k] for k in ("artifact_id", "revision", "digest")}
        params.update(source_fingerprint=pv["fingerprint"], receipt="reviewed", idempotency_key="mcp-accept", confirm=True)
        assert "artifact.accept" in await call(server, "artifact_accept", params)
        file = tmp_path / "reviewed-preview.json"
        file.write_text(json.dumps(pv))
        args = cli.build_parser().parse_args(["artifact", "managed-capture", "--preview-file", str(file), "--key", "mcp-managed", "--confirm"])
        assert await asyncio.to_thread(cli.cmd_artifact, args) == 0
        assert json.loads(capsys.readouterr().out)["operation"]["operation_id"] == op["operation_id"]
        args = cli.build_parser().parse_args(["artifact", "accept", ref["artifact_id"], "1", "--digest", ref["digest"],
                  "--source-fingerprint", pv["fingerprint"], "--receipt", "reviewed", "--key", "mcp-accept", "--confirm"])
        assert await asyncio.to_thread(cli.cmd_artifact, args) == 0
        approved = json.loads(capsys.readouterr().out)["operation"]
        assert (await finish(d, approved))["status"] == "succeeded"
        args = cli.build_parser().parse_args(["artifact", "managed-capture-preview", "h1", execution[1], "result.bin",
                                               "--execution-operation-id", execution[0]["operation_id"]])
        assert await asyncio.to_thread(cli.cmd_artifact, args) == 0
        assert json.loads(capsys.readouterr().out)["preview"]["source"]["lineage"] == pv["source"]["lineage"]
        monkeypatch.delenv("BATC_API_TOKEN")
        assert "BATC_API_TOKEN" in await call(server, "artifact_accept", params)
    finally:
        await fleet.close()


@pytest.mark.parametrize("command", ["managed-capture", "accept"])
def test_cli_read_only_refuses_managed_mutations_before_io(monkeypatch, capsys, command):
    from bat_agent_connector import task_daemon
    def forbidden(*args, **kw):
        pytest.fail("read-only mutation attempted I/O")
    monkeypatch.setattr(task_daemon, "request", forbidden)
    monkeypatch.setattr(cli, "Path", forbidden)
    argv = ["--preview-file", "not-opened"] if command == "managed-capture" else ["art_" + "0" * 32, "1",
            "--digest", "0" * 64, "--source-fingerprint", "0" * 64, "--receipt", "reviewed"]
    assert cli.main(["--read-only", "artifact", command, *argv, "--key", "refused", "--confirm"]) == 1
    assert "--read-only" in capsys.readouterr().err


@pytest.mark.parametrize("revision", [True, 0, -1, 2**63, [], {}])
async def test_accept_revision_admission_is_bounded_before_operation_or_lookup(served, revision):
    d, port = served
    token = api.token(d, "approver", "approve")
    body = {"action": "artifact.accept", "target": {"artifact_id": "art_" + "a" * 32, "revision": revision},
            "params": {"digest": "a" * 64, "source_fingerprint": "b" * 64, "receipt": "reviewed"}}
    code, out = await api.http(port, "POST", "/api/v1/operations", tok=token, body=body,
                               headers={"Idempotency-Key": "invalid-revision"})
    assert code == 422 and out["error"]["code"] == "INVALID_PARAMS", out
    assert not d.ops.list()["operations"]


@pytest.mark.parametrize("kind", ["symlink", "hardlink"])
async def test_managed_capture_uses_same_strict_reader_for_actual_unsafe_files(daemon, execution, kind, tmp_path):
    person, _ = principal(daemon)
    path = execution[2] / "result.bin"
    outside = tmp_path / "outside.bin"
    outside.write_bytes(b"unrelated bytes")
    if kind == "symlink":
        path.unlink()
        path.symlink_to(outside)
    else:
        os.link(path, execution[2] / "second.bin")
    with pytest.raises(OperationError):
        await preview(daemon, person, execution)
    assert outside.read_bytes() == b"unrelated bytes"
    assert not daemon.journal.db.execute("SELECT 1 FROM artifact_uploads").fetchone()


async def test_managed_binding_is_rechecked_after_source_read_before_staging(daemon, execution, monkeypatch):
    person, _ = principal(daemon)
    pv = await preview(daemon, person, execution)
    adapter = daemon.ops.context["artifact_host"]
    real = adapter.capture
    async def changed(host, request):
        result = await real(host, request)
        registry.update("h1", execution[1], task_id="replacement-owner", role="lead")
        return result
    monkeypatch.setattr(adapter, "capture", changed)
    op = await finish(daemon, submit(daemon, person, pv))
    assert op["status"] == "failed" and op["error_code"] == "ARTIFACT_LINEAGE_UNPROVEN", op
    assert not daemon.journal.db.execute("SELECT 1 FROM artifact_capture_sources").fetchone()
    assert not list(daemon.artifact_store.root.glob("revisions/*/*/content"))


async def test_managed_unknown_read_can_only_reread_original_fixed_evidence(daemon, execution, monkeypatch):
    person, _ = principal(daemon)
    pv = await preview(daemon, person, execution)
    adapter = daemon.ops.context["artifact_host"]
    real = adapter.capture
    async def lost(host, request):
        await real(host, request)
        raise AmbiguousOutcome("source read completed but reply was lost before staging")
    monkeypatch.setattr(adapter, "capture", lost)
    op = await finish(daemon, submit(daemon, person, pv))
    assert op["status"] == "uncertain", op
    monkeypatch.setattr(adapter, "capture", real)
    (execution[2] / "result.bin").write_bytes(b"later contents cannot replace original input")
    daemon.journal.db.execute("UPDATE operations SET next_run_at=0 WHERE operation_id=?", (op["operation_id"],))
    done = await finish(daemon, op)
    assert done["status"] == "failed" and done["error_code"] == "SOURCE_CHANGED", done
    assert not list(daemon.artifact_store.root.glob("revisions/*/*/content"))


def test_acceptance_schema_upgrade_preserves_existing_data_step_and_rows(tmp_path):
    from bat_agent_connector.task_journal import Journal
    path = tmp_path / "old.db"
    j = Journal(path)
    task = j.submit(project="fixture", host="fixture", workspace="fixture", original_words="preserved", idempotency_key="old")
    version = j.db.execute("PRAGMA user_version").fetchone()[0]
    j.db.execute("DROP TABLE artifact_acceptances")
    j.close()
    for _ in range(2):
        j = Journal(path)
        assert j.get(task["task_id"])["original_words"] == "preserved"
        assert j.db.execute("PRAGMA user_version").fetchone()[0] == version
        assert j.db.execute("SELECT count(*) FROM artifact_acceptances").fetchone()[0] == 0
        j.close()
