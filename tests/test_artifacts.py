"""Artifacts Part A (plan §08/12/13, B04): real local bytes/Git, mocked BAT; no real host writes."""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import os
import shutil
import sys
import threading
from dataclasses import replace
from pathlib import Path

import pytest

from bat_agent_connector import api_auth, artifacts, checkpoints, registry, resource_policy, service
from bat_agent_connector.artifact_host import HELPER, ArtifactHost
from bat_agent_connector.errors import InvokeTimeout, ResourceReadOnly
from bat_agent_connector.operations import AmbiguousOutcome, OperationError
from bat_agent_connector.task_daemon import TaskDaemon
from bat_agent_connector.task_journal import LATEST_DATA_STEP, Journal
from tests.operation_helpers import settle_operations
from tests.test_checkpoints import MANUAL, git
from tests.test_checkpoints import daemon as checkpoint_daemon
from tests.test_checkpoints import human as checkpoint_human
from tests.test_journal_compatibility import snapshot as journal_snapshot

PERSON = api_auth.Principal("artifact-person", frozenset({"observe", "manage", "start", "operate", "approve"}))


@pytest.fixture
def human(tmp_path, checkpoint_repo_seed):
    return checkpoint_human.__wrapped__(tmp_path, checkpoint_repo_seed)


@pytest.fixture
async def daemon(mock, human, tmp_path):
    fixture = checkpoint_daemon.__wrapped__(mock, human, tmp_path)
    d = next(fixture)
    try:
        yield d
    finally:
        await d.artifact_store.close_reaper()
        fixture.close()


class LocalArtifactHost(ArtifactHost):
    def __init__(self):
        super().__init__({"h1": "local-test"}, 10)
        self.calls = []

    def argv(self, host):
        return (sys.executable, "-c", HELPER)

    async def call(self, host, request, content=b""):
        self.calls.append(dict(request))
        return await super().call(host, request, content)


async def action(d, name, target=None, params=None, pre=None, key=None):
    op, _ = d.ops.create(PERSON, action=name, target=target or {}, params=params or {},
                         preconditions=pre or {}, idempotency_key=key or "test." + os.urandom(8).hex())
    await settle_operations(d.ops)
    return d.ops.get(op["operation_id"])


async def reserve(d, data=b"immutable input", key="upload-one", target=None, pre=None, name="notes.txt"):
    return await action(d, "artifact.upload", target, {"display_name": name, "size_bytes": len(data),
                        "expected_digest": hashlib.sha256(data).hexdigest(), "media_type": "text/plain"}, pre, key)


def reader(data):
    stream = asyncio.StreamReader()
    stream.feed_data(data)
    stream.feed_eof()
    return stream


async def upload(d, data=b"immutable input", **kw):
    op = await reserve(d, data, **kw)
    if op["status"] == "waiting_external":
        await d.artifact_store.receive(PERSON, op["operation_id"], reader(data), len(data))
        await settle_operations(d.ops)
        await d.artifact_store.reap_best_effort()
        op = d.ops.get(op["operation_id"])
    assert op["status"] == "succeeded", op
    return {k: op["result"][k] for k in ("artifact_id", "revision", "digest")}


async def continuation(d, cp, refs, key="continue-one", **params):
    d.ops.context.setdefault("artifact_host", LocalArtifactHost())
    return await action(d, "checkpoint.continue", {"checkpoint_id": cp["checkpoint_id"]},
                        {"instructions": "Read the attached input and continue.", "artifacts": refs, **params},
                        {"expected_source_head_sha": cp["head_sha"], **params.pop("pre", {})}, key)


async def make_checkpoint(d, **params):
    await d.inventory.refresh_host("h1")
    op = await action(d, "checkpoint.create", {"host": "h1", "session_id": MANUAL}, {"last_n": 5, **params})
    assert op["status"] == "succeeded", op
    return checkpoints.get(d.journal.db, op["result"]["checkpoint_id"])


def sends(mock):
    return [x for x in mock.invokes if x["channel"] == "claude:send-message"]


async def test_artifact_upload_is_immutable_and_idempotent(daemon):
    ref = await upload(daemon)
    assert await upload(daemon) == ref
    assert daemon.artifact_store.read_content(ref["artifact_id"], 1) == b"immutable input"
    with pytest.raises(OperationError) as conflict:
        await reserve(daemon, b"different", key="upload-one")
    assert conflict.value.code == "IDEMPOTENCY_CONFLICT"
    ref2 = await upload(daemon, b"new", key="upload-two", target={"artifact_id": ref["artifact_id"]},
                        pre={"expected_latest_revision": 1})
    assert ref2["revision"] == 2 and ref2["artifact_id"] == ref["artifact_id"]
    assert daemon.artifact_store.read_content(ref["artifact_id"], 1) == b"immutable input"
    with pytest.raises(OperationError) as exc:
        await reserve(daemon, b"newer", key="upload-three", target={"artifact_id": ref["artifact_id"]}, pre={"expected_latest_revision": 1})
    assert exc.value.code == "REVISION_CONFLICT"
    assert len(list(daemon.artifact_store.root.glob("revisions/*/*/content"))) == 2


async def test_artifact_limits_include_partial_and_concurrent_reservations(daemon):
    daemon.artifact_store.settings = replace(daemon.artifact_store.settings, max_file_bytes=6, max_store_bytes=10)
    op = await reserve(daemon, b"abcdef")
    with pytest.raises(OperationError) as exc:
        await reserve(daemon, b"ghijkl", key="concurrent")
    assert exc.value.code == "ARTIFACT_STORE_FULL"
    with pytest.raises(OperationError, match="retry"):
        await daemon.artifact_store.receive(PERSON, op["operation_id"], reader(b"abcde"), 6)
    with pytest.raises(OperationError) as exc:
        await daemon.artifact_store.receive(PERSON, op["operation_id"], reader(b"abcdef"), 6)
    assert exc.value.code == "ARTIFACT_STORE_FULL"  # 6 reserved + 5 partial
    with pytest.raises(OperationError) as exc:
        await reserve(daemon, b"1234567", key="too-big")
    assert exc.value.code == "ARTIFACT_TOO_LARGE"


async def test_upload_publish_lost_reply_and_restart_read_back(daemon, monkeypatch):
    real = daemon.artifact_store.publish
    def lost(row):
        real(row)
        raise AmbiguousOutcome("publish ACK lost")
    monkeypatch.setattr(daemon.artifact_store, "publish", lost)
    op = await reserve(daemon)
    await daemon.artifact_store.receive(PERSON, op["operation_id"], reader(b"immutable input"), 15)
    await settle_operations(daemon.ops)
    assert daemon.ops.get(op["operation_id"])["status"] == "uncertain"
    # Close SQLite and construct a new daemon: memory contains no step or upload result.
    path, config = daemon.journal.path, daemon.fleet.config
    await daemon.artifact_store.close_reaper()
    daemon.journal.close()
    reopened = TaskDaemon(config, path)
    try:
        reopened.journal.db.execute("UPDATE operations SET next_run_at=0 WHERE operation_id=?", (op["operation_id"],))
        await settle_operations(reopened.ops)
        await reopened.artifact_store.reap_best_effort()
        done = reopened.ops.get(op["operation_id"])
        assert done["status"] == "succeeded", json.dumps(done, indent=2)
        assert len(list(reopened.artifact_store.root.glob("revisions/*/*/content"))) == 1
        assert not (reopened.artifact_store.root / "staging" / op["operation_id"]).exists()
    finally:
        await reopened.artifact_store.close_reaper()
        await reopened.fleet.close()
        await reopened.inventory.close()
        reopened.journal.close()


async def test_attachment_change_invalidates_work_item_approval(daemon):
    project = await action(daemon, "project.create", params={"name": "Attachments"})
    item = await action(daemon, "work_item.create", {"project_id": project["result"]["project_id"]}, {"title": "Review"})
    from bat_agent_connector import work_items
    wid = item["result"]["work_item_id"]
    old = work_items.work_item_get(daemon.journal.db, wid)["work_item"]
    approved = await action(daemon, "work_item.approve", {"work_item_id": wid}, pre={"expected_fingerprint": old["completion"]["fingerprint"]})
    assert approved["status"] == "succeeded"
    ref = await upload(daemon)
    updated = await action(daemon, "work_item.update", {"work_item_id": wid}, {"attachments": [{**ref, "role": "input"}]}, {"expected_version": work_items.work_item_get(daemon.journal.db, wid)["work_item"]["version"]})
    assert updated["status"] == "succeeded", updated
    current = work_items.work_item_get(daemon.journal.db, wid)["work_item"]
    assert current["completion"]["fingerprint"] != old["completion"]["fingerprint"] and not current["completion"]["approved"]
    assert daemon.journal.db.execute("SELECT owner_kind FROM artifact_references").fetchone()[0] == "work_item"


@pytest.mark.parametrize("version", [1, 2, 3, 17])
def test_artifact_migration_preserves_existing_journal_and_empty_manifests(tmp_path, version):
    path = tmp_path / "legacy.db"
    j = Journal(path)
    task = j.submit(project="p", host="h1", workspace="w", original_words="keep", idempotency_key="legacy")
    before = j.api_events(0, 10)
    j.db.execute("ALTER TABLE checkpoints DROP COLUMN attachments")
    j.db.execute("ALTER TABLE work_items DROP COLUMN attachments")
    tables = ("checkpoint_source_confirmations", "artifact_materializations", "artifact_references",
              "artifact_uploads", "artifact_revisions", "artifacts")
    for table in tables:
        j.db.execute(f"DROP TABLE {table}")
    j.db.execute(f"PRAGMA user_version={version}")
    j.close()
    if version > LATEST_DATA_STEP:
        before = journal_snapshot(path)
        with pytest.raises(ValueError, match="JOURNAL_NEWER_DATA_STEP"):
            Journal(path)
        assert journal_snapshot(path) == before
        return
    j = Journal(path)
    assert j.get(task["task_id"])["original_words"] == "keep"
    assert j.api_events(0, 10) == before
    assert j.db.execute("PRAGMA user_version").fetchone()[0] == max(version, LATEST_DATA_STEP)
    assert set(tables) <= {x[0] for x in j.db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    for table in ("checkpoints", "work_items"):
        assert next(x for x in j.db.execute(f"PRAGMA table_info({table})") if x[1] == "attachments")[4] == "'[]'"
    snapshot = list(j.db.iterdump())
    j.close()
    j = Journal(path)
    assert j.db.execute("PRAGMA user_version").fetchone()[0] == max(version, LATEST_DATA_STEP)
    assert list(j.db.iterdump()) == snapshot  # idempotent second open, including data
    j.close()


class Writer:
    def __init__(self):
        self.data = bytearray()
    def write(self, data):
        self.data.extend(data)
    async def drain(self):
        pass


@pytest.mark.parametrize("failure", [ValueError("private scratch detail"),
    ResourceReadOnly("DESTINATION_UNKNOWN", "private scratch detail"), OSError("private scratch detail")],
    ids=["value", "policy", "filesystem"])
async def test_reaper_failure_never_stops_ticks_or_fails_cancel(daemon, monkeypatch, caplog, failure):
    """Plan §09/B04: failed scratch cleanup preserves committed intent and task reconciliation."""
    first = await reserve(daemon)
    calls = []
    def fail():
        calls.append(True)
        raise failure
    monkeypatch.setattr(daemon.artifact_store, "reap_terminal", fail)
    with daemon.journal.tx():
        token = api_auth.issue(daemon.journal.db, PERSON.actor, list(PERSON.scopes))
    writer = Writer()
    await daemon.api.handle("POST", f"/api/v1/operations/{first['operation_id']}/cancel",
        {"host": "localhost", "authorization": "Bearer " + token, "content-length": "0"}, reader(b""), writer)
    assert writer.data.startswith(b"HTTP/1.1 200")
    assert json.loads(writer.data.split(b"\r\n\r\n", 1)[1])["operation"]["status"] == "cancelled"
    admitted = await reserve(daemon, key="admitted-despite-reaper")
    assert admitted["status"] == "waiting_external"
    result = await daemon.artifact_store.receive(PERSON, admitted["operation_id"], reader(b"immutable input"), 15)
    assert result["operation_id"] == admitted["operation_id"]
    cancelled = await daemon.call_api("op_cancel", {"operation_id": admitted["operation_id"]}, PERSON)
    assert cancelled["operation"]["status"] == "cancelled"
    partial = await reserve(daemon, key="partial-despite-reaper")
    with pytest.raises(OperationError) as error:
        await daemon.artifact_store.receive(PERSON, partial["operation_id"], reader(b"partial"), 15)
    assert error.value.code == "UPLOAD_INCOMPLETE"  # reaping does not replace the original failure
    row = daemon.journal.db.execute("SELECT reserved_bytes,released_at FROM artifact_uploads WHERE operation_id=?",
                                   (first["operation_id"],)).fetchone()
    assert row[0] == 15 and row[1] is None
    ready = await upload(daemon, key="ready-despite-reaper")
    assert daemon.journal.db.execute("SELECT reserved_bytes FROM artifact_uploads WHERE artifact_id=?",
                                    (ready["artifact_id"],)).fetchone()[0] == 15
    next_revision = await reserve(daemon, key="next-despite-reaper", target={"artifact_id": ready["artifact_id"]},
                                  pre={"expected_latest_revision": 1})
    assert next_revision["status"] == "waiting_external"  # terminal scratch does not block revision CAS
    task = daemon.journal.submit(project="p", host="h1", workspace="w", original_words="keep ticking", idempotency_key="tick")
    ticks = asyncio.Queue()
    async def tick(task_id):
        ticks.put_nowait(task_id)
    monkeypatch.setattr(daemon, "_tick_task", tick)
    worker = asyncio.create_task(daemon._worker())
    reaper = asyncio.create_task(daemon._artifact_reap_loop())
    try:
        assert await asyncio.wait_for(ticks.get(), 2) == task["task_id"]
        assert await asyncio.wait_for(ticks.get(), 3) == task["task_id"]
        assert not worker.done() and not reaper.done() and len(calls) >= 2
    finally:
        worker.cancel()
        reaper.cancel()
        await asyncio.gather(worker, reaper, return_exceptions=True)
        await daemon.artifact_store.close_reaper()
    assert type(failure).__name__ in caplog.text and first["operation_id"] in caplog.text
    assert "private scratch detail" not in caplog.text


async def test_slow_reaper_never_delays_task_ticks(daemon, monkeypatch):
    """Plan §09: blocking filesystem cleanup stays off the task worker's event loop."""
    op = await reserve(daemon)
    with pytest.raises(OperationError):
        await daemon.artifact_store.receive(PERSON, op["operation_id"], reader(b"partial"), 15)
    daemon.ops.cancel(PERSON, op["operation_id"])
    started, ticked = asyncio.Event(), asyncio.Event()
    release = threading.Event()
    loop = asyncio.get_running_loop()
    real = daemon.artifact_store._remove_staging
    def slow(operation_id):
        loop.call_soon_threadsafe(started.set)
        release.wait(5)
        real(operation_id)
    monkeypatch.setattr(daemon.artifact_store, "_remove_staging", slow)
    daemon.journal.submit(project="p", host="h1", workspace="w", original_words="keep ticking", idempotency_key="slow-tick")
    async def tick(_task_id):
        ticked.set()
    monkeypatch.setattr(daemon, "_tick_task", tick)
    reaper = asyncio.create_task(daemon.artifact_store.reap_best_effort())
    worker = None
    try:
        await asyncio.wait_for(started.wait(), 2)
        worker = asyncio.create_task(daemon._worker())
        await asyncio.wait_for(ticked.wait(), 2)
        assert not reaper.done()
        assert daemon.journal.db.execute("SELECT released_at FROM artifact_uploads WHERE operation_id=?",
                                        (op["operation_id"],)).fetchone()[0] is None
    finally:
        release.set()
        await reaper
        if worker:
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)
        await daemon.artifact_store.close_reaper()
    assert daemon.journal.db.execute("SELECT released_at FROM artifact_uploads WHERE operation_id=?",
                                    (op["operation_id"],)).fetchone()[0] is not None


async def test_artifact_http_mcp_cli_contract_and_scopes(daemon, monkeypatch, tmp_path):
    from bat_agent_connector import cli, mcp_server
    with daemon.journal.tx():
        token = api_auth.issue(daemon.journal.db, PERSON.actor, list(PERSON.scopes))
        other = api_auth.issue(daemon.journal.db, "other", ["observe", "manage"])
    op = await reserve(daemon)
    class NoRead:
        async def read(self, _):
            raise AssertionError("body read before admission")
    base = {"host": "127.0.0.1", "content-type": "application/octet-stream", "content-length": "15"}
    path = op["external_refs"]["content_url"]
    for headers, status in ((base, 401), ({**base, "authorization": "Bearer " + other}, 403),
                            ({**base, "authorization": "Bearer " + token, "transfer-encoding": "chunked"}, 422),
                            ({**base, "authorization": "Bearer " + token, "content-length": "16"}, 422)):
        writer = Writer()
        await daemon.api.handle("POST", path, headers, NoRead(), writer)
        assert writer.data.startswith(f"HTTP/1.1 {status}".encode()), writer.data
    loop = asyncio.create_task(daemon.ops.loop())
    server = await asyncio.start_server(daemon._handle, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    monkeypatch.setenv("BATC_TASK_URL", f"http://127.0.0.1:{port}/rpc")
    monkeypatch.setenv("BATC_API_TOKEN", token)
    try:
        args = cli.build_parser().parse_args(["artifact", "upload", str(tmp_path / "local.txt"), "--key", "cli", "--confirm"])
        (tmp_path / "local.txt").write_bytes(b"CLI content")
        assert await asyncio.to_thread(cli.cmd_artifact, args) == 0
        await settle_operations(daemon.ops)
        mcp, fleet = mcp_server.build_server(daemon.fleet.config)
        tools = await mcp.list_tools()
        names = {x.name for x in tools}
        assert {"artifact_upload", "artifact_get", "artifacts_list"} <= names
        assert "artifact_materialize" not in names
        # The tool uses the same HTTP adapter after scoped OperationService intent.
        result = await mcp.call_tool("artifact_upload", {"display_name": "model.txt", "content_base64": "bW9kZWw=",
                                    "idempotency_key": "mcp", "confirm": True})
        assert result is not None
        await settle_operations(daemon.ops)
        await fleet.close()
        refs = artifacts.list_artifacts(daemon.journal.db)["artifacts"]
        assert len(refs) == 3  # includes the pending HTTP admission reservation
        ready = next(x["revision"] for x in refs if x["revision"] and x["revision"]["state"] == "ready")
        writer = Writer()
        await daemon.api.handle("GET", ready["content_url"], {"host": "localhost", "authorization": "Bearer " + token}, NoRead(), writer)
        assert b"Content-Disposition: attachment" in writer.data and b"nosniff" in writer.data and b"no-store" in writer.data
    finally:
        server.close()
        await server.wait_closed()
        loop.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await loop


async def test_upload_window_expires_and_removes_only_its_own_staging(daemon):
    kept = await upload(daemon)
    expired = await reserve(daemon, b"partial data", key="expires")
    with pytest.raises(OperationError):
        await daemon.artifact_store.receive(PERSON, expired["operation_id"], reader(b"part"), 12)
    active = await reserve(daemon, b"active", key="active")
    daemon.journal.db.execute("UPDATE artifact_uploads SET deadline=0 WHERE operation_id=?", (expired["operation_id"],))
    daemon.ops.wake(expired["operation_id"])
    await settle_operations(daemon.ops)
    await daemon.artifact_store.reap_best_effort()
    failed = daemon.ops.get(expired["operation_id"])
    assert failed["status"] == "failed" and failed["error_code"] == "UPLOAD_EXPIRED"
    assert not (daemon.artifact_store.root / "staging" / expired["operation_id"]).exists()
    assert daemon.ops.get(active["operation_id"])["status"] == "waiting_external"
    assert daemon.artifact_store.read_content(kept["artifact_id"], 1) == b"immutable input"


async def test_operation_wake_only_moves_waiting_external(daemon):
    op = await reserve(daemon)
    db = daemon.journal.db
    for state in ("needs_attention", "uncertain", "accepted", "running", "failed", "succeeded", "cancelled"):
        db.execute("UPDATE operations SET status=?,next_run_at=12345 WHERE operation_id=?", (state, op["operation_id"]))
        daemon.ops.wake(op["operation_id"])
        assert db.execute("SELECT status,next_run_at FROM operations WHERE operation_id=?", (op["operation_id"],)).fetchone()[1] == 12345
    db.execute("UPDATE operations SET status='waiting_external',next_run_at=12345 WHERE operation_id=?", (op["operation_id"],))
    daemon.ops.wake(op["operation_id"])
    assert db.execute("SELECT next_run_at FROM operations WHERE operation_id=?", (op["operation_id"],)).fetchone()[0] == 0
    # No new state-transition event was written by wake itself.


async def test_materialized_inputs_are_git_excluded_and_inside_the_worktree(daemon, mock, human):
    daemon.ops.context["artifact_host"] = LocalArtifactHost()
    ref = await upload(daemon)
    cp = await make_checkpoint(daemon, artifacts=[ref])
    done = await continuation(daemon, cp, [ref])
    assert done["status"] == "succeeded", done
    wt = done["result"]["worktree_path"]
    mat = done["result"]["materializations"][0]
    assert Path(mat["managed_path"]).is_relative_to(wt)
    assert Path(mat["managed_path"]).read_bytes() == b"immutable input"
    assert git(wt, "--no-optional-locks", "status", "--porcelain") == ""
    common = Path(git(wt, "rev-parse", "--path-format=absolute", "--git-common-dir"))
    assert (common / "info/exclude").read_text().count("/.batc-inputs/") == 1
    prompt = sends(mock)[0]["params"]["prompt"]
    assert ".batc-inputs/" in prompt and ref["digest"] in prompt
    assert str(human) not in prompt
    assert cp["artifacts"] == [ref]


async def test_continue_prompt_limit_is_checked_before_operation_or_host_write(daemon, mock, monkeypatch):
    """B04/plan §13: an oversized exact input manifest has no durable dispatch intent or host side effect."""
    adapter = LocalArtifactHost()
    daemon.ops.context["artifact_host"] = adapter
    ref = await upload(daemon, name="a" * 190 + ".txt")
    cp = await make_checkpoint(daemon)
    monkeypatch.setattr(service, "MAX_PROMPT_CHARS", 2000)
    before = daemon.journal.db.execute("SELECT COUNT(*) FROM operations").fetchone()[0]
    host_calls = list(mock.invokes)
    git_calls = list(daemon.ops.context["git_runner"].scripts)
    with pytest.raises(OperationError) as error:
        daemon.ops.create(PERSON, action="checkpoint.continue", target={"checkpoint_id": cp["checkpoint_id"]},
            params={"instructions": "Read carefully. " * 15, "artifacts": [ref]},
            preconditions={"expected_source_head_sha": cp["head_sha"]}, idempotency_key="oversized-manifest")
    assert error.value.code == "INVALID_PARAMS" and error.value.status == 422
    assert daemon.journal.db.execute("SELECT COUNT(*) FROM operations").fetchone()[0] == before
    assert mock.invokes == host_calls and not adapter.calls and daemon.ops.context["git_runner"].scripts == git_calls


@pytest.mark.parametrize("version,ready", [("2.30.9", False), ("2.31.0", True), ("2.39.5 (Apple Git-154)", True)])
async def test_artifact_probe_reports_git_floor_and_blocks_unready_admission(daemon, mock, tmp_path, monkeypatch, version, ready):
    """B04/plan §28: observed unsupported Git is diagnosable before creating continuation intent."""
    adapter = LocalArtifactHost()
    daemon.ops.context["artifact_host"] = adapter
    ref = await upload(daemon)
    cp = await make_checkpoint(daemon)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake_git = bin_dir / "git"
    fake_git.write_text("#!/bin/sh\nprintf '%s\\n' 'git version " + version + "'\n")
    fake_git.chmod(0o755)
    monkeypatch.setenv("PATH", str(bin_dir) + os.pathsep + os.environ["PATH"])
    result = await adapter.probe("h1")
    assert result["git_version"] == version and result["ok"] is ready
    _, capabilities = await daemon.api.capabilities(principal=PERSON)
    assert capabilities["artifacts"]["hosts"][0]["readiness"] == result
    if not ready:
        assert result["code"] == "ARTIFACT_ADAPTER_UNAVAILABLE" and "Git 2.31+" in result["message"]
        before = daemon.journal.db.execute("SELECT COUNT(*) FROM operations").fetchone()[0]
        host_calls, helper_calls = list(mock.invokes), list(adapter.calls)
        git_calls = list(daemon.ops.context["git_runner"].scripts)
        with pytest.raises(OperationError) as error:
            daemon.ops.create(PERSON, action="checkpoint.continue", target={"checkpoint_id": cp["checkpoint_id"]},
                params={"instructions": "Read the input.", "artifacts": [ref]},
                preconditions={"expected_source_head_sha": cp["head_sha"]}, idempotency_key="unsupported-git")
        assert error.value.code == "ARTIFACT_ADAPTER_UNAVAILABLE" and "2.31+" in str(error.value)
        assert daemon.journal.db.execute("SELECT COUNT(*) FROM operations").fetchone()[0] == before
        assert mock.invokes == host_calls and adapter.calls == helper_calls
        assert daemon.ops.context["git_runner"].scripts == git_calls
        # A later readiness probe can discover an upgrade; an old failed probe does not trap the host.
        fake_git.write_text("#!/bin/sh\nprintf '%s\\n' 'git version 2.31.0'\n")
        assert (await adapter.probe("h1"))["ok"]


@pytest.mark.parametrize("missing", ["exclude", "info"])
async def test_artifact_materialization_creates_missing_git_exclude(daemon, mock, missing):
    """B04/plan §13: managed clones without Git templates still receive ignored inputs."""
    adapter = LocalArtifactHost()
    daemon.ops.context["artifact_host"] = adapter
    ref = await upload(daemon)
    cp = await make_checkpoint(daemon)
    old_mode = []
    real = adapter.call
    async def without_templates(host, request, content=b""):
        if request["mode"] == "receive":
            info = Path(request["clone"]) / ".git/info"
            if missing == "exclude":
                old_mode.append(info.stat().st_mode & 0o777)
                (info / "exclude").unlink()
            else:
                shutil.rmtree(info)
        return await real(host, request, content)
    adapter.call = without_templates
    done = await continuation(daemon, cp, [ref])
    assert done["status"] == "succeeded" and len(sends(mock)) == 1, done
    info = Path(done["external_refs"]["clone_path"]) / ".git/info"
    assert info.is_dir() and info.stat().st_mode & 0o777 == (old_mode[0] if old_mode else 0o755)
    exclude = info / "exclude"
    assert exclude.stat().st_nlink == 1 and exclude.stat().st_mode & 0o777 == 0o644
    assert exclude.read_text().count("/.batc-inputs/") == 1
    assert git(done["result"]["worktree_path"], "--no-optional-locks", "status", "--porcelain") == ""


@pytest.mark.parametrize("kind", ["info-file", "info-symlink", "exclude-directory", "exclude-symlink", "exclude-hardlink"])
async def test_artifact_exclude_refuses_non_directory_or_shared_files(daemon, mock, tmp_path, kind):
    """B04/plan §06: repairing an absent exclude never adopts symlinks, non-files or shared files."""
    adapter = LocalArtifactHost()
    daemon.ops.context["artifact_host"] = adapter
    ref = await upload(daemon)
    cp = await make_checkpoint(daemon)
    outside = tmp_path / "outside"
    outside.mkdir()
    kept = outside / "exclude"
    kept.write_text("keep\n")
    real = adapter.call
    async def unknown_exclude(host, request, content=b""):
        if request["mode"] == "receive":
            info = Path(request["clone"]) / ".git/info"
            if kind.startswith("info-"):
                shutil.rmtree(info)
                if kind == "info-file":
                    info.write_text("unknown")
                else:
                    info.symlink_to(outside, target_is_directory=True)
            else:
                exclude = info / "exclude"
                exclude.unlink()
                if kind == "exclude-directory":
                    exclude.mkdir()
                elif kind == "exclude-symlink":
                    exclude.symlink_to(kept)
                else:
                    os.link(kept, exclude)
        return await real(host, request, content)
    adapter.call = unknown_exclude
    blocked = await continuation(daemon, cp, [ref])
    assert blocked["status"] == "needs_attention" and blocked["error_code"] == "DESTINATION_UNKNOWN", blocked
    assert not sends(mock) and not registry.list_entries("h1")
    assert kept.read_text() == "keep\n" and list(outside.iterdir()) == [kept]


async def test_artifact_policy_refuses_escape_before_any_host_write(daemon, mock, tmp_path):
    hc = daemon.fleet.config.host("h1")
    root = hc.managed_roots[0] + "/checkpoint-example"
    op = "op_" + "a" * 32
    wt = root + "/.bat-worktrees/batc-cp-" + op[3:15]
    valid = ".batc-inputs/art_" + "a" * 32 + "-r1/notes.txt"
    resource_policy.check_artifact_destination(hc, root, wt, "batc/cp-" + op[3:15], valid)
    for relative in ("../escape", ".batc-inputs/../../outside", ".batc-inputs/art_" + "a" * 32 + "-r1/../bad", ".batc-inputs/art_" + "a" * 32 + "-r1/.git"):
        with pytest.raises(ResourceReadOnly):
            resource_policy.check_artifact_destination(hc, root, wt, "batc/cp-" + op[3:15], relative)
    assert not sends(mock)
    adapter = LocalArtifactHost()
    daemon.ops.context["artifact_host"] = adapter
    ref = await upload(daemon)
    cp = await make_checkpoint(daemon)
    outside = tmp_path / "outside"
    outside.mkdir()
    real = adapter.call
    async def symlink(host, request, content=b""):
        if request["mode"] == "receive":
            exclude = Path(request["clone"]) / ".git/info/exclude"
            before = exclude.read_bytes()
            (Path(request["worktree"]) / ".batc-inputs").symlink_to(outside, target_is_directory=True)
            result = await real(host, request, content)
            assert exclude.read_bytes() == before  # refusal precedes even the exclude append
            return result
        return await real(host, request, content)
    adapter.call = symlink
    blocked = await continuation(daemon, cp, [ref])
    assert blocked["status"] == "needs_attention" and blocked["error_code"] == "DESTINATION_UNKNOWN", blocked
    assert not list(outside.iterdir()) and not sends(mock)


async def test_B04_transfer_interrupted_before_dispatch(daemon, mock):
    adapter = LocalArtifactHost()
    daemon.ops.context["artifact_host"] = adapter
    ref = await upload(daemon)
    cp = await make_checkpoint(daemon)
    real = adapter.call
    async def interrupted(host, request, content=b""):
        if request["mode"] == "receive" and request["attempt"] == 1:
            return await real(host, request, content[:3])
        return await real(host, request, content)
    adapter.call = interrupted
    op = await continuation(daemon, cp, [ref])
    assert op["status"] == "needs_attention" and op["error_code"] == "ARTIFACT_SIZE_MISMATCH", op
    assert not sends(mock) and not registry.list_entries("h1")
    daemon.ops.resume(PERSON, op["operation_id"])
    await settle_operations(daemon.ops)
    done = daemon.ops.get(op["operation_id"])
    assert done["status"] == "succeeded" and len(sends(mock)) == 1, done
    assert done["result"]["materializations"][0]["attempt"] == 2


@pytest.mark.parametrize("changed,code", [("digest", "ARTIFACT_DIGEST_MISMATCH"), ("size_bytes", "ARTIFACT_SIZE_MISMATCH"), ("path", "BINDING_MISMATCH")])
async def test_B04_digest_size_or_path_mismatch_blocks_first_command(daemon, mock, changed, code):
    adapter = LocalArtifactHost()
    daemon.ops.context["artifact_host"] = adapter
    ref = await upload(daemon)
    cp = await make_checkpoint(daemon)
    real = adapter.call
    async def mismatch(host, request, content=b""):
        result = await real(host, request, content)
        if request["mode"] == "verify":
            result[changed] = 999 if changed == "size_bytes" else "wrong"
        return result
    adapter.call = mismatch
    op = await continuation(daemon, cp, [ref])
    assert op["status"] == "needs_attention" and op["error_code"] == code, op
    assert not sends(mock)
    assert op["external_refs"]["materializations"][0]["state"] == "blocked"


async def test_B04_source_advanced_after_preview_keeps_selection(daemon, mock, human):
    adapter = LocalArtifactHost()
    daemon.ops.context["artifact_host"] = adapter
    ref = await upload(daemon)
    cp = await make_checkpoint(daemon)
    git(human, "commit", "-q", "--allow-empty", "-m", "source advanced")
    op = await continuation(daemon, cp, [ref])
    assert op["status"] == "needs_attention" and op["error_code"] == "SOURCE_MOVED", op
    assert not sends(mock) and not adapter.calls
    confirmed = await action(daemon, "checkpoint.continue.revalidate", {"operation_id": op["operation_id"]},
                             {"observed_source_head_sha": git(human, "rev-parse", "HEAD")},
                             {"expected_input_manifest_digest": op["external_refs"]["input_manifest_digest"]})
    assert confirmed["status"] == "succeeded", confirmed
    await settle_operations(daemon.ops)
    done = daemon.ops.get(op["operation_id"])
    assert done["status"] == "succeeded" and done["params"]["artifacts"] == [ref]
    assert done["result"]["base_commit"] == cp["commit_sha"] and len(sends(mock)) == 1


@pytest.mark.parametrize("lost_step", ["start", "send"])
async def test_B04_resume_after_start_or_send_ack_loss_never_duplicates_execution(daemon, mock, monkeypatch, lost_step):
    from bat_agent_connector import orchestrate
    adapter = LocalArtifactHost()
    daemon.ops.context["artifact_host"] = adapter
    ref = await upload(daemon)
    cp = await make_checkpoint(daemon)
    module, name = (orchestrate, "session_start") if lost_step == "start" else (service, "session_send")
    real = getattr(module, name)
    async def lost(*args, **kwargs):
        await real(*args, **kwargs)
        raise InvokeTimeout("accepted, reply lost")
    monkeypatch.setattr(module, name, lost)
    op = await continuation(daemon, cp, [ref])
    assert op["status"] == "uncertain", op
    monkeypatch.setattr(module, name, real)
    daemon.journal.db.execute("UPDATE operations SET next_run_at=0 WHERE operation_id=?", (op["operation_id"],))
    await settle_operations(daemon.ops)
    done = daemon.ops.get(op["operation_id"])
    assert done["status"] == "succeeded", done
    assert len([x for x in mock.invokes if x["channel"] == "claude:start-session"]) == 1
    assert len(sends(mock)) == 1


@pytest.mark.parametrize("change", ["file", "source"])
async def test_B04_inputs_changed_after_start_keep_session_and_block_send(daemon, mock, monkeypatch, human, change):
    from bat_agent_connector import orchestrate
    adapter = LocalArtifactHost()
    daemon.ops.context["artifact_host"] = adapter
    ref = await upload(daemon)
    cp = await make_checkpoint(daemon)
    real = orchestrate.session_start
    async def tamper(*args, **kw):
        result = await real(*args, **kw)
        path = next(Path(kw["cwd_override"]).glob(".batc-inputs/*/notes.txt"))
        if change == "file":
            path.write_bytes(b"changed input")
        else:
            git(human, "commit", "-q", "--allow-empty", "-m", "source moved during start")
        return result
    monkeypatch.setattr(orchestrate, "session_start", tamper)
    op = await continuation(daemon, cp, [ref])
    expected = "ARTIFACT_SIZE_MISMATCH" if change == "file" else "SOURCE_MOVED"
    assert op["status"] == "needs_attention" and op["error_code"] == expected, op
    assert not sends(mock) and len(registry.list_entries("h1")) == 1
    if change == "file":
        Path(op["external_refs"]["materializations"][0]["managed_path"]).write_bytes(b"immutable input")
        daemon.ops.resume(PERSON, op["operation_id"])
    else:
        await action(daemon, "checkpoint.continue.revalidate", {"operation_id": op["operation_id"]},
                     {"observed_source_head_sha": git(human, "rev-parse", "HEAD")},
                     {"expected_input_manifest_digest": op["external_refs"]["input_manifest_digest"]})
    await settle_operations(daemon.ops)
    done = daemon.ops.get(op["operation_id"])
    assert done["status"] == "succeeded", done
    assert len([x for x in mock.invokes if x["channel"] == "claude:start-session"]) == 1 and len(sends(mock)) == 1


async def test_cancelled_unsettled_publish_keeps_original_and_its_quota(daemon, monkeypatch):
    daemon.artifact_store.settings = replace(daemon.artifact_store.settings, max_store_bytes=16)
    real = daemon.artifact_store.publish
    def lost(row):
        real(row)
        raise AmbiguousOutcome("publish ACK lost")
    monkeypatch.setattr(daemon.artifact_store, "publish", lost)
    op = await reserve(daemon)
    await daemon.artifact_store.receive(PERSON, op["operation_id"], reader(b"immutable input"), 15)
    await settle_operations(daemon.ops)
    assert daemon.ops.get(op["operation_id"])["status"] == "uncertain"
    daemon.journal.db.execute("UPDATE operations SET status='needs_attention' WHERE operation_id=?", (op["operation_id"],))
    daemon.ops.cancel(PERSON, op["operation_id"])
    await daemon.artifact_store.reap_terminal()
    ref = op["external_refs"]
    formal = daemon.artifact_store.content_path(ref["artifact_id"], ref["revision"])
    assert formal.read_bytes() == b"immutable input"
    assert daemon.artifact_store.used_bytes() == 15
    with pytest.raises(OperationError) as exc:
        await reserve(daemon, b"two", key="no-free-quota")
    assert exc.value.code == "ARTIFACT_STORE_FULL"


async def test_B04_missing_original_blocks_then_resumes_same_parent(daemon, mock):
    daemon.ops.context["artifact_host"] = LocalArtifactHost()
    ref = await upload(daemon)
    cp = await make_checkpoint(daemon)
    formal = daemon.artifact_store.content_path(ref["artifact_id"], ref["revision"])
    saved = formal.with_name("saved-input")
    formal.rename(saved)  # simulate unavailable storage; Connector never deletes an original
    op = await continuation(daemon, cp, [ref])
    assert op["status"] == "needs_attention" and op["error_code"] == "ARTIFACT_CONTENT_UNAVAILABLE", op
    assert not sends(mock)
    saved.rename(formal)
    daemon.ops.resume(PERSON, op["operation_id"])
    await settle_operations(daemon.ops)
    done = daemon.ops.get(op["operation_id"])
    assert done["status"] == "succeeded" and len(sends(mock)) == 1, done
