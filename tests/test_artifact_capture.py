"""B1: real temporary Git/files, mock BAT, no live SSH or manual mutations."""

from __future__ import annotations

import asyncio
import base64
import hmac
import json
import os
import sys
from dataclasses import replace
from types import SimpleNamespace

import pytest

from bat_agent_connector import api_auth, artifacts, cli, platform_files, registry
from bat_agent_connector import artifact_capture as capture
from bat_agent_connector import artifact_capture_helper as helper
from bat_agent_connector.artifact_host import CAPTURE_HELPER, ArtifactHost
from bat_agent_connector.mcp_server import build_server
from bat_agent_connector.operations import AmbiguousOutcome, OperationError
from bat_agent_connector.task_daemon import TaskDaemon
from tests import test_api_v1 as api
from tests.operation_helpers import settle_operations
from tests.test_artifacts import daemon as artifact_daemon
from tests.test_artifacts import human as artifact_human
from tests.test_checkpoints import MANUAL, bat_writes, git, snapshot

served = api.served
human = artifact_human


class LocalCaptureHost(ArtifactHost):
    def __init__(self):
        super().__init__({"h1": "fixture-only"}, 10)
        self.calls = []

    def capture_argv(self, host):
        return sys.executable, "-I", "-S", "-B", "-c", CAPTURE_HELPER

    async def capture(self, host, request):
        self.calls.append(dict(request))
        return await super().capture(host, request)


@pytest.fixture
async def daemon(mock, human, tmp_path):
    fixture = artifact_daemon.__wrapped__(mock, human, tmp_path)
    d = await anext(fixture)
    d.ops.context["artifact_host"] = LocalCaptureHost()
    await d.inventory.refresh_host("h1")
    try:
        yield d
    finally:
        await d.fleet.close()
        await d.inventory.close()
        await fixture.aclose()


def principal(d, actor="capture-person", scopes=("observe", "manage")):
    token = api.token(d, actor, *scopes)
    return api_auth.authenticate(d.journal.db, token, d._admin_token), token


async def preview(d, person, path="notes.txt"):
    return await capture.preview(d.ops, person, {"host": "h1", "session_id": MANUAL, "relative_path": path})


def submit(d, person, pv, key="capture-one"):
    return d.ops.create(person, action="artifact.capture", target={"preview_id": pv["preview_id"]},
                        params={"preview_token": pv["preview_token"]},
                        preconditions={"expected_fingerprint": pv["fingerprint"]}, idempotency_key=key)[0]


async def finish(d, op):
    await settle_operations(d.ops)
    await d.artifact_store.reap_best_effort()
    return d.ops.get(op["operation_id"])


def helper_request(human, path="notes.txt"):
    return {"mode": "preview", "root": str(human), "repository_root": str(human),
            "relative_path": path, "max_file_bytes": 1024}


async def test_capture_preserves_manual_bytes_index_refs_and_publishes_central_proof(daemon, human, mock):
    person, _ = principal(daemon)
    (human / "notes.txt").write_bytes(b"uncommitted continuation input\x00\xff")
    before = snapshot(human)
    pv = await preview(daemon, person)
    assert not pv["snapshot"] and not daemon.journal.db.execute("SELECT 1 FROM artifacts").fetchone()
    op = await finish(daemon, submit(daemon, person, pv))
    assert op["status"] == "succeeded", op
    result = op["result"]
    assert result["revision"] == 1
    assert daemon.artifact_store.read_content(result["artifact_id"], 1) == (human / "notes.txt").read_bytes()
    row = artifacts.get(daemon.journal.db, result["artifact_id"], 1)
    assert row["source"]["kind"] == "manual_capture"
    assert row["source"]["actor"] == person.actor
    assert row["source"]["operation_id"] == op["operation_id"]
    assert row["source"]["evidence"] == pv["evidence"]
    assert submit(daemon, person, pv)["operation_id"] == op["operation_id"]
    assert len(list(daemon.artifact_store.root.glob("revisions/*/*/content"))) == 1
    assert snapshot(human) == before
    assert not bat_writes(mock)


@pytest.mark.parametrize("path", ["/etc/passwd", "../notes.txt", "x/../notes.txt", "./notes.txt", ".git/config",
                                 ".GIT/config", "x//notes.txt", "x/", "x\\notes.txt", "x\nnotes.txt", ""])
async def test_preview_rejects_paths_before_remote_file_read(daemon, path):
    person, _ = principal(daemon)
    with pytest.raises(OperationError) as refused:
        await preview(daemon, person, path)
    assert refused.value.code == "INVALID_CAPTURE_PATH"
    assert not daemon.ops.context["artifact_host"].calls


@pytest.mark.parametrize("kind", ["symlink", "parent_symlink", "root_symlink", "hardlink", "directory", "fifo", "large"])
def test_helper_rejects_unsafe_source_files(human, kind, tmp_path):
    request = helper_request(human)
    path = human / "notes.txt"
    if kind == "symlink":
        path.unlink()
        path.symlink_to(tmp_path / "outside")
    elif kind == "parent_symlink":
        (human / "link").symlink_to(human, target_is_directory=True)
        request["relative_path"] = "link/notes.txt"
    elif kind == "root_symlink":
        (tmp_path / "alias").symlink_to(human, target_is_directory=True)
        request["root"] = str(tmp_path / "alias")
    elif kind == "hardlink":
        os.link(path, human / "second")
    elif kind in {"directory", "fifo"}:
        path.unlink()
        path.mkdir() if kind == "directory" else os.mkfifo(path)
    elif kind == "large":
        request["max_file_bytes"] = 1
    with pytest.raises((helper.Refusal, OSError)):
        helper.execute(request)


@pytest.mark.parametrize("change", ["content", "replace", "root", "head"])
def test_helper_detects_changes_during_read(human, monkeypatch, change):
    original = helper.read_chunk
    altered = False
    def changing(fd, size):
        nonlocal altered
        data = original(fd, size)
        if not altered:
            altered = True
            if change == "content":
                (human / "notes.txt").write_text("changed")
            elif change == "replace":
                (human / "replacement").write_text("v2\n")
                (human / "replacement").replace(human / "notes.txt")
            elif change == "root":
                human.rename(human.with_name("moved"))
                human.mkdir()
                (human / "notes.txt").write_text("v2\n")
            else:
                git(human, "update-ref", "HEAD", "HEAD~1")
        return data
    monkeypatch.setattr(helper, "read_chunk", changing)
    with pytest.raises(helper.Refusal) as refused:
        helper.execute(helper_request(human))
    assert refused.value.code == "SOURCE_CHANGED"


async def test_preview_binds_actual_credential_even_with_same_actor_label(daemon):
    person, _ = principal(daemon)
    other, _ = principal(daemon)
    assert person == other  # legacy actor/scope equality is intentionally unchanged
    assert person.credential_id != other.credential_id
    pv = await preview(daemon, person)
    with pytest.raises(OperationError) as refused:
        submit(daemon, other, pv)
    assert refused.value.code == "PREVIEW_MISMATCH"
    assert not daemon.journal.db.execute("SELECT 1 FROM operations").fetchone()
    assert person.credential_id not in json.dumps(pv)


@pytest.mark.parametrize("verb", ["replay", "resume", "cancel"])
@pytest.mark.parametrize("authority", ["other_credential", "manage_only", "observe_only", "reduced", "admin"])
async def test_existing_capture_requires_original_credential_and_current_combined_scopes(daemon, verb, authority):
    person, token = principal(daemon)
    pv = await preview(daemon, person)
    op = submit(daemon, person, pv)
    if authority == "reduced":
        daemon.journal.db.execute("UPDATE api_principals SET scopes=? WHERE token_hash=?",
                                 (json.dumps(["manage"]), person.credential_id))
        other = api_auth.authenticate(daemon.journal.db, token, daemon._admin_token)
        assert other.credential_id == person.credential_id
    elif authority == "admin":
        other = api_auth.authenticate(daemon.journal.db, daemon._admin_token, daemon._admin_token)
    else:
        scopes = {"other_credential": ("observe", "manage"), "manage_only": ("manage",),
                  "observe_only": ("observe",)}[authority]
        other, _ = principal(daemon, scopes=scopes)
    daemon.journal.db.execute("UPDATE operations SET status='needs_attention' WHERE operation_id=?",
                             (op["operation_id"],))
    before = daemon.ops.get(op["operation_id"])
    with pytest.raises(OperationError) as refused:
        if verb == "replay":
            submit(daemon, other, pv)
        else:
            getattr(daemon.ops, verb)(other, op["operation_id"])
    # The admin actor has its own key namespace, so its create is a new, mismatched preview admission.
    expected = ("PREVIEW_MISMATCH", 409) if authority == "admin" and verb == "replay" else ("FORBIDDEN", 403)
    assert (refused.value.code, refused.value.status) == expected
    assert daemon.ops.get(op["operation_id"]) == before
    assert len(daemon.ops.list()["operations"]) == 1
    assert not daemon.journal.db.execute("SELECT 1 FROM artifact_uploads").fetchone()
    assert len(daemon.ops.context["artifact_host"].calls) == 1  # original preview only
    assert person.credential_id not in json.dumps(before)


@pytest.mark.parametrize("verb", ["replay", "resume", "cancel"])
async def test_original_capture_controls_do_not_expire_or_readmit_accepted_preview(daemon, monkeypatch, verb):
    person, _ = principal(daemon)
    pv = await preview(daemon, person)
    op = submit(daemon, person, pv)
    daemon.journal.db.execute("UPDATE operations SET status='needs_attention' WHERE operation_id=?",
                             (op["operation_id"],))
    monkeypatch.setattr(capture, "time", SimpleNamespace(time=lambda: pv["expires_at"] + 1))
    with pytest.raises(OperationError, match="PREVIEW_EXPIRED"):
        capture._decode(daemon.ops, pv["preview_token"])
    # Original admission is durable: changed current quota must not block receipt/control access.
    daemon.artifact_store.settings = replace(daemon.artifact_store.settings, max_store_bytes=1)
    result = submit(daemon, person, pv) if verb == "replay" else getattr(daemon.ops, verb)(person, op["operation_id"])
    assert result["operation_id"] == op["operation_id"]
    assert result["status"] == {"replay": "needs_attention", "resume": "running", "cancel": "cancelled"}[verb]
    assert len(daemon.ops.list()["operations"]) == 1
    assert not daemon.journal.db.execute("SELECT 1 FROM artifact_uploads").fetchone()
    assert len(daemon.ops.context["artifact_host"].calls) == 1


async def test_preview_refuses_oversized_signed_source_identity_before_returning_token(daemon, monkeypatch):
    person, _ = principal(daemon)
    original = await preview(daemon, person)
    long_path = "/" + "a" * 4095
    async def source(*_args):
        return {**original["source"], "root": long_path, "repository_root": long_path,
                "tab": {**original["source"]["tab"], "cwd": long_path, "worktreePath": long_path}}
    async def read(*_args, **_kwargs):
        return original["evidence"], b""
    monkeypatch.setattr(capture, "_source", source)
    monkeypatch.setattr(capture, "_read", read)
    with pytest.raises(OperationError, match="preview bound") as refused:
        await preview(daemon, person, "a/" * 1900 + "notes.txt")
    assert refused.value.code == "SOURCE_UNAVAILABLE"
    assert not daemon.journal.db.execute("SELECT 1 FROM operations").fetchone()


async def test_expired_tampered_preview_and_forged_lineage_reserve_nothing(daemon):
    person, _ = principal(daemon)
    pv = await preview(daemon, person)
    with pytest.raises(OperationError, match="PREVIEW_TOKEN_INVALID"):
        submit(daemon, person, {**pv, "preview_token": pv["preview_token"] + "x"})
    version, raw, _sig = pv["preview_token"].split(".")
    claims = json.loads(base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)))
    claims.update(iat=1, exp=1 + capture.TTL_S)
    body = capture._bytes(claims)
    signed = version + "." + capture._b64(body) + "." + capture._b64(hmac.digest(daemon.ops.context["capture_key"], b"cap1." + body, "sha256"))
    with pytest.raises(OperationError, match="PREVIEW_EXPIRED"):
        submit(daemon, person, {**pv, "preview_token": signed})
    with pytest.raises(OperationError, match="INVALID_PARAMS"):
        daemon.ops.create(person, action="artifact.capture", target={"preview_id": pv["preview_id"]},
                          params={"preview_token": pv["preview_token"], "source": {"execution_id": "fake"}},
                          preconditions={"expected_fingerprint": pv["fingerprint"]}, idempotency_key="fake")
    assert not daemon.journal.db.execute("SELECT 1 FROM artifact_uploads").fetchone()


@pytest.mark.parametrize("change", ["file", "head", "cwd", "tab", "sdk_identity", "registry"])
async def test_capture_refuses_changed_source_without_publishing(daemon, human, mock, change):
    person, _ = principal(daemon)
    pv = await preview(daemon, person)
    if change == "file":
        (human / "notes.txt").write_text("different")
    elif change == "head":
        git(human, "update-ref", "HEAD", "HEAD~1")
    elif change == "cwd":
        mock.metas[MANUAL]["cwd"] = str(human.parent)
    elif change == "tab":
        mock.ws_doc["terminals"] = [t for t in mock.ws_doc["terminals"] if t["id"] != MANUAL]
    elif change == "sdk_identity":
        mock.ws_doc["terminals"][0]["sdkSessionId"] = "different"
    else:
        # Simulate unproven registry evidence appearing after the manual preview.
        # Recovery cannot adopt this row without its original controller proof.
        entries = registry.list_entries()
        entries.append({"host": "h1", "session_id": MANUAL, "workspace": "fixture", "agent": "claude",
                        "status": "starting", "cwd": str(human)})
        platform_files.atomic_write(registry.registry_path(), json.dumps({"sessions": entries}).encode())
    op = await finish(daemon, submit(daemon, person, pv))
    assert op["status"] == "failed", op
    assert op["error_code"] in {"SOURCE_CHANGED", "CAPTURE_SOURCE_NOT_MANUAL"}
    assert not list(daemon.artifact_store.root.glob("revisions/*/*/content"))
    assert not bat_writes(mock)


async def test_capture_respects_existing_quota_and_client_content_route_cannot_substitute(daemon):
    person, _ = principal(daemon)
    pv = await preview(daemon, person)
    daemon.artifact_store.settings = replace(daemon.artifact_store.settings, max_store_bytes=1)
    with pytest.raises(OperationError, match="ARTIFACT_STORE_FULL"):
        submit(daemon, person, pv)
    daemon.artifact_store.settings = replace(daemon.artifact_store.settings, max_store_bytes=1024)
    op = submit(daemon, person, pv)
    with pytest.raises(OperationError, match="UPLOAD_STATE"):
        daemon.artifact_store.check_receive(person, op["operation_id"], pv["evidence"]["size_bytes"])
    daemon.ops.cancel(person, op["operation_id"])
    assert (await finish(daemon, op))["status"] == "cancelled"
    assert not daemon.ops.context["artifact_host"].calls[1:]


@pytest.mark.parametrize("stage", ["partial", "complete"])
async def test_interrupted_capture_reconciles_only_complete_bytes_and_proof(daemon, human, monkeypatch, stage):
    person, _ = principal(daemon)
    pv = await preview(daemon, person)
    real = daemon.artifact_store.receive_reserved
    async def interrupted(operation_id, row, reader, length, actor):
        if stage == "partial":
            reader = asyncio.StreamReader()
            reader.feed_data(b"v")
            reader.feed_eof()
        try:
            await real(operation_id, row, reader, length, actor)
        except OperationError:
            pass
        raise AmbiguousOutcome("fixture receiver interrupted before step receipt")
    monkeypatch.setattr(daemon.artifact_store, "receive_reserved", interrupted)
    op = await finish(daemon, submit(daemon, person, pv))
    assert op["status"] == "uncertain", op
    monkeypatch.setattr(daemon.artifact_store, "receive_reserved", real)
    if stage == "complete":
        (human / "notes.txt").write_text("later source edit does not invalidate completed capture")
    daemon.journal.db.execute("UPDATE operations SET next_run_at=0 WHERE operation_id=?", (op["operation_id"],))
    op = await finish(daemon, op)
    assert op["status"] == "succeeded", op
    assert daemon.artifact_store.read_content(op["result"]["artifact_id"], 1) == b"v2\n"
    captures = [r for r in daemon.ops.context["artifact_host"].calls if r["mode"] == "capture"]
    assert len(captures) == (2 if stage == "partial" else 1)
    row = daemon.journal.db.execute("SELECT * FROM artifact_uploads WHERE operation_id=?", (op["operation_id"],)).fetchone()
    assert row["released_at"] and row["reserved_bytes"] == 0


async def test_cancel_during_source_read_never_stages_or_publishes(daemon, monkeypatch):
    person, _ = principal(daemon)
    pv = await preview(daemon, person)
    host = daemon.ops.context["artifact_host"]
    real = host.capture
    async def cancelled(hostname, request):
        result = await real(hostname, request)
        operation_id = daemon.ops.list(action="artifact.capture")["operations"][0]["operation_id"]
        daemon.ops.cancel(person, operation_id)
        return result
    monkeypatch.setattr(host, "capture", cancelled)
    op = await finish(daemon, submit(daemon, person, pv))
    assert op["status"] == "cancelled", op
    assert not list(daemon.artifact_store.root.glob("revisions/*/*/content"))
    assert not daemon.journal.db.execute("SELECT 1 FROM artifact_capture_sources").fetchone()


async def test_cancel_during_staging_reaps_partial_bytes_and_keeps_true_capture_proof(daemon, human, monkeypatch):
    person, _ = principal(daemon)
    (human / "notes.txt").write_bytes(b"z" * 70000)
    pv = await preview(daemon, person)
    real = daemon.artifact_store.receive_reserved
    async def cancelled(operation_id, row, reader, length, actor):
        read = reader.read
        async def cancel_after_chunk(size):
            data = await read(size)
            daemon.ops.cancel(person, operation_id)
            return data
        reader.read = cancel_after_chunk
        return await real(operation_id, row, reader, length, actor)
    monkeypatch.setattr(daemon.artifact_store, "receive_reserved", cancelled)
    op = await finish(daemon, submit(daemon, person, pv))
    assert op["status"] == "cancelled", op
    assert not list(daemon.artifact_store.root.glob("revisions/*/*/content"))
    assert not list(daemon.artifact_store.root.glob("staging/*"))
    row = daemon.journal.db.execute("SELECT * FROM artifact_uploads WHERE operation_id=?", (op["operation_id"],)).fetchone()
    assert row["receive_state"] == "partial" and row["received_size"] == 65536
    assert row["reserved_bytes"] == 0 and row["released_at"]
    proof = json.loads(daemon.journal.db.execute("SELECT document FROM artifact_capture_sources WHERE operation_id=?",
                                               (op["operation_id"],)).fetchone()[0])
    assert proof["evidence"] == pv["evidence"]  # remote read succeeded; central publication did not


@pytest.mark.parametrize("wire", [b"not-json\n", b"x" * 9000 + b"\n", b'{"ok":true}\nextra'])
async def test_capture_transport_rejects_unbounded_or_invalid_preview_output(monkeypatch, wire):
    host = LocalCaptureHost()
    monkeypatch.setattr(host, "capture_argv", lambda _: (sys.executable, "-c", f"import sys;sys.stdout.buffer.write({wire!r})"))
    with pytest.raises(AmbiguousOutcome):
        await host.capture("h1", {"mode": "preview", "max_file_bytes": 16})


async def test_manual_binding_rechecked_after_helper_read_and_before_staging(daemon, mock, monkeypatch):
    person, _ = principal(daemon)
    pv = await preview(daemon, person)
    host = daemon.ops.context["artifact_host"]
    real = host.capture
    async def rebound(hostname, request):
        result = await real(hostname, request)
        mock.ws_doc["terminals"][0]["sdkSessionId"] = "rebound-while-reading"
        return result
    monkeypatch.setattr(host, "capture", rebound)
    op = await finish(daemon, submit(daemon, person, pv))
    assert op["status"] == "failed" and op["error_code"] == "SOURCE_CHANGED"
    assert not daemon.journal.db.execute("SELECT 1 FROM artifact_capture_sources").fetchone()


@pytest.mark.parametrize("cancel_after_publish", [False, True])
async def test_publish_lost_reply_restart_uses_one_revision_without_rereading_source(daemon, human, monkeypatch, cancel_after_publish):
    person, _ = principal(daemon)
    pv = await preview(daemon, person)
    real = daemon.artifact_store.publish
    def lost(row):
        real(row)
        raise AmbiguousOutcome("fixture lost publish reply")
    monkeypatch.setattr(daemon.artifact_store, "publish", lost)
    op = await finish(daemon, submit(daemon, person, pv))
    assert op["status"] == "uncertain", op
    if cancel_after_publish:
        daemon.ops.cancel(person, op["operation_id"])
    await daemon.artifact_store.close_reaper()
    path, config = daemon.journal.path, daemon.fleet.config
    daemon.journal.close()
    (human / "notes.txt").write_text("later unrelated change")
    reopened = TaskDaemon(config, path)
    try:
        class NeverRead:
            async def capture(self, *args):
                raise AssertionError("completed capture must not reread source")
        reopened.ops.context["artifact_host"] = NeverRead()
        reopened.journal.db.execute("UPDATE operations SET next_run_at=0 WHERE operation_id=?", (op["operation_id"],))
        done = await finish(reopened, op)
        assert done["status"] == "succeeded", done
        assert reopened.artifact_store.read_content(done["result"]["artifact_id"], 1) == b"v2\n"
        assert len(list(reopened.artifact_store.root.glob("revisions/*/*/content"))) == 1
    finally:
        await reopened.artifact_store.close_reaper()
        await reopened.fleet.close()
        await reopened.inventory.close()
        reopened.journal.close()


async def test_http_rpc_scope_credential_and_read_boundaries(served):
    d, port = served
    person, token = principal(d)
    request = {"host": "h1", "session_id": MANUAL, "relative_path": "notes.txt"}
    for tok, expected in [(None, 401), (api.token(d, "manager", "manage"), 403)]:
        status, _ = await api.http(port, "POST", "/api/v1/artifact-capture-previews", tok=tok, body=request)
        assert status == expected
    status, result = await api.http(port, "POST", "/api/v1/artifact-capture-previews", tok=token, body=request)
    assert status == 200, result
    pv = result["preview"]
    body = {"action": "artifact.capture", "target": {"preview_id": pv["preview_id"]},
            "params": {"preview_token": pv["preview_token"]}, "preconditions": {"expected_fingerprint": pv["fingerprint"]}}
    status, _ = await api.http(port, "POST", "/api/v1/operations", tok=api.token(d, "read-only", "observe"), body=body,
                              headers={"Idempotency-Key": "forbidden-capture"})
    assert status == 403 and not d.journal.db.execute("SELECT 1 FROM operations").fetchone()
    task = d.journal.submit(project="fixture", host="h1", workspace="fixture", original_words="fixture",
                            idempotency_key="task-capability")
    task_token = d.journal.issue_capability(task["task_id"])
    assert (await api.http(port, "POST", "/api/v1/artifact-capture-previews", tok=task_token, body=request))[0] == 401
    status, result = await api.http(port, "POST", "/api/v1/operations", tok=token, body=body,
                                    headers={"Idempotency-Key": "http-capture"})
    assert status == 202, result
    op = await finish(d, result["operation"])
    assert op["status"] == "succeeded", op
    for scopes in [("manage",), ("observe", "manage")]:
        other = api.token(d, person.actor, *scopes)
        status, refusal = await api.http(port, "POST", "/api/v1/operations", tok=other, body=body,
                                         headers={"Idempotency-Key": "http-capture"})
        assert status == 403 and "operation" not in refusal
        for verb in ("cancel", "resume"):
            status, refusal = await api.http(port, "POST", f"/api/v1/operations/{op['operation_id']}/{verb}", tok=other)
            assert status == 403 and "operation" not in refusal
    assert d.ops.get(op["operation_id"])["status"] == "succeeded"
    ref = op["result"]
    url = f"/api/v1/artifacts/{ref['artifact_id']}/revisions/1"
    assert (await api.http(port, "GET", url, tok=api.token(d, "reader", "observe")))[0] == 200
    assert (await api.http(port, "GET", url, tok=api.token(d, "non-reader", "manage")))[0] == 403
    status, _ = await api.http(port, "POST", "/rpc", tok=token,
                               body={"method": "artifact_capture_preview", "params": {**request, "entry": "cli"}})
    assert status == 200
    _, capabilities = await d.api.capabilities(api_auth.Principal("manage-only", frozenset({"manage"})))
    assert not next(a for a in capabilities["actions"] if a["action"] == "artifact.capture")["allowed"]


async def test_mcp_no_admin_fallback_and_cli_share_fixed_request(served, monkeypatch, tmp_path, capsys):
    d, port = served
    person, token = principal(d)
    monkeypatch.setenv("BATC_TASK_URL", f"http://127.0.0.1:{port}/rpc")
    monkeypatch.delenv("BATC_API_TOKEN", raising=False)
    server, fleet = build_server(d.fleet.config, principal_only=True)
    from tests.test_mcp_principal import call
    try:
        params = {"host": "h1", "session_id": MANUAL, "relative_path": "notes.txt"}
        assert "BATC_API_TOKEN" in await call(server, "artifact_capture_preview", params)
        monkeypatch.setenv("BATC_API_TOKEN", token)
        assert "preview_token" in await call(server, "artifact_capture_preview", params)
        pv = await preview(d, person)
        args = {"preview_id": pv["preview_id"], "preview_token": pv["preview_token"],
                "fingerprint": pv["fingerprint"], "idempotency_key": "mcp-capture", "confirm": True}
        assert "artifact.capture" in await call(server, "artifact_capture", args)
        await settle_operations(d.ops)
        stored = d.ops.list(actor=person.actor)["operations"]
        assert stored[0]["entry"] == "mcp" and stored[0]["status"] == "succeeded"
        preview_file = tmp_path / "preview.json"
        preview_file.write_text(json.dumps({"preview": pv}))
        parsed = cli.build_parser().parse_args(["artifact", "capture", "--preview-file", str(preview_file),
                                               "--key", "cli-capture", "--confirm"])
        assert await asyncio.to_thread(cli.cmd_artifact, parsed) == 0
        out = json.loads(capsys.readouterr().out)
        assert out["operation"]["entry"] == "cli"
        assert out["operation"]["params"] == {"preview_token": pv["preview_token"]}
        await settle_operations(d.ops)
    finally:
        await fleet.close()


def test_cli_read_only_capture_refuses_before_preview_file_or_rpc(monkeypatch, capsys):
    from bat_agent_connector import task_daemon
    def forbidden(*_args, **_kwargs):
        pytest.fail("read-only capture reached file, configuration or RPC I/O")
    monkeypatch.setattr(cli, "Path", forbidden)
    monkeypatch.setattr(cli, "load_config", forbidden)
    monkeypatch.setattr(task_daemon, "request", forbidden)
    assert cli.main(["--read-only", "artifact", "capture", "--preview-file", "unopened-preview.json",
                     "--key", "reviewed-capture", "--confirm"]) == 1
    assert "--read-only" in capsys.readouterr().err


def test_cli_read_only_capture_preview_uses_fixed_read_rpc(monkeypatch, capsys):
    from bat_agent_connector import task_daemon
    calls = []
    monkeypatch.setenv("BATC_API_TOKEN", "fixture-only-credential")
    def request(method, **params):
        calls.append((method, params))
        return {"preview": {"preview_id": "fixture-only-preview"}}
    monkeypatch.setattr(task_daemon, "request", request)
    assert cli.main(["--read-only", "artifact", "capture-preview", "h1", MANUAL, "notes.txt"]) == 0
    assert calls == [("artifact_capture_preview", {"_auth_token": "fixture-only-credential", "entry": "cli",
                                                 "host": "h1", "session_id": MANUAL, "relative_path": "notes.txt"})]
    assert json.loads(capsys.readouterr().out)["preview"]["preview_id"] == "fixture-only-preview"
