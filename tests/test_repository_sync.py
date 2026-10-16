"""Published GitHub head -> new managed resources, real temporary Git and central transports."""
from __future__ import annotations

import asyncio
import base64
import json
import re
import shlex
from pathlib import Path

import pytest

from bat_agent_connector import api_auth, artifact_managed, cleanup, cli, registry, repository_sync
from bat_agent_connector.config import ConfigError, parse_config
from bat_agent_connector.errors import InvokeTimeout
from bat_agent_connector.operations import AmbiguousOutcome, OperationError
from bat_agent_connector.task_daemon import TaskDaemon
from tests.fakegithub import TOKEN, FakeGitHub
from tests.operation_helpers import settle_operations
from tests.test_api_v1 import http, token
from tests.test_checkpoints import LocalRunner, RealGitLog, bat_writes, git, snapshot

READER = api_auth.Principal("reader", frozenset({"observe"}))
STARTER = api_auth.Principal("starter", frozenset({"start"}))
TARGET = {"repository": "o/r", "host": "h1", "workspace_id": "ws-1"}
REQUEST = {**TARGET, "source_ref": "refs/heads/main"}


class Runner(LocalRunner):
    def __init__(self):
        super().__init__()
        self.after = {}
        self.before = {}
        self.phases = []

    async def run(self, host, script, timeout_s=None):
        req = json.loads(base64.b64decode(shlex.split(script)[-1])) if script.startswith("python3 -c") else {}
        phase = req.get("phase")
        self.phases.append(phase)
        if phase in self.before:
            self.before.pop(phase)(req)
        result = await super().run(host, script, timeout_s)
        if phase in self.after:
            self.after.pop(phase)(req)
        return result


@pytest.fixture
async def world(mock, tmp_path, monkeypatch):
    human, remote, managed = tmp_path / "human", tmp_path / "remote.git", tmp_path / "managed"
    human.mkdir()
    managed.mkdir()
    git(human, "init", "--template=", "-q", "-b", "main")
    git(human, "config", "user.name", "fixture")
    git(human, "config", "user.email", "fixture@example.invalid")
    (human / "hello.txt").write_text("published bytes\n")
    git(human, "add", ".")
    git(human, "commit", "-qm", "published")
    sha = git(human, "rev-parse", "HEAD")
    git(tmp_path, "clone", "--bare", "--no-hardlinks", str(human), str(remote))
    (human / "hello.txt").write_text("unpublished human edit\n")
    gh = FakeGitHub()
    gh.start()
    state = {"sha": sha}
    def provide(method, path, _):
        if method == "GET" and path == "/repos/o/r/git/ref/heads/main":
            gh.script.append(("GET", re.escape(path) + "$", 200, {},
                              {"ref": "refs/heads/main", "object": {"type": "commit", "sha": state["sha"]}}))
    gh.before_request = provide
    monkeypatch.setenv("REPO_FAKE_TOKEN", TOKEN)
    raw = {"hosts": {"h1": {"url": mock.url, "fingerprint": mock.fingerprint, "token_ref": "env:BATC_TEST_TOKEN",
           "writes": True, "orchestrate": True, "orchestrate_register_tabs": False,
           "orchestrate_max_sessions": 4, "managed_roots": [str(managed)]}},
           "safety": {"write_min_interval_s": 0}, "github": {"token_ref": "env:REPO_FAKE_TOKEN", "api_url": gh.url,
           "repos": [{"repository": "o/r", "sync": {"remote_url": str(remote),
                      "bindings": [{"host": "h1", "workspace_id": "ws-1"}]}}]}}
    mock.ws_doc["workspaces"][0]["folderPath"] = str(human)
    mock.git_logs = RealGitLog()
    mock.handlers["git:branch"] = lambda p: git(p["cwd"], "branch", "--show-current")
    mock.echo_sends = True
    daemon = TaskDaemon(parse_config(raw), tmp_path / "journal.db")
    runner = Runner()
    daemon.ops.context["git_runner"] = runner
    server = await asyncio.start_server(daemon._handle, "127.0.0.1", 0)
    w = {"d": daemon, "gh": gh, "state": state, "human": human, "remote": remote, "managed": managed,
         "sha": sha, "runner": runner, "raw": raw, "port": server.sockets[0].getsockname()[1]}
    yield w
    server.close()
    await server.wait_closed()
    await w["d"].fleet.close()
    await w["d"].inventory.close()
    w["d"].journal.close()
    gh.stop()


async def preview(w):
    return await repository_sync.preview(w["d"].ops, READER, REQUEST)


def envelope(pv, key="published-1", **params):
    return {"action": "repository.continue", "target": TARGET,
            "params": {"source_ref": pv["source_ref"], "source_sha": pv["source_sha"], "prompt": "Work here", **params},
            "preconditions": pv["preconditions"], "idempotency_key": key}


async def settle(w, op):
    await settle_operations(w["d"].ops)
    return w["d"].ops.get(op["operation_id"])


async def start(w, pv=None, **params):
    op, _ = w["d"].ops.create(STARTER, **envelope(pv or await preview(w), **params))
    return await settle(w, op)


@pytest.mark.parametrize("agent", ["claude", "codex"])
async def test_exact_published_start_and_replay_preserves_manual(world, mock, agent):
    w = world
    before = snapshot(w["human"])
    remote_before = git(w["remote"], "for-each-ref")
    pv = await preview(w)
    assert not w["runner"].scripts and not bat_writes(mock) and not w["d"].ops.list()["operations"]
    op = await start(w, pv, agent=agent)
    assert op["status"] == "succeeded", (op["error_code"], op["status_reason"], op["steps"])
    result = op["result"]
    assert git(result["worktree_path"], "rev-parse", "HEAD") == w["sha"]
    assert Path(result["worktree_path"], "hello.txt").read_text() == "published bytes\n"
    assert snapshot(w["human"]) == before and git(w["remote"], "for-each-ref") == remote_before
    assert [x["name"] for x in op["steps"]] == ["source.resolve", "repository.fetch", "worktree.prepare", "verify.start", "session.start", "send"]
    row = registry.get("h1", result["session_id"])
    assert row["published_sha"] == w["sha"] and row["start_operation_id"] == op["operation_id"]
    proof = artifact_managed.lineage(w["d"].ops, "h1", result["session_id"], row, {"execution_operation_id": op["operation_id"]})
    assert proof["action"] == "repository.continue"
    w["state"]["sha"] = "a" * 40
    mock.ws_doc["workspaces"] = []
    count = len(bat_writes(mock))
    again = await start(w, pv, agent=agent)
    assert again["operation_id"] == op["operation_id"] and len(bat_writes(mock)) == count
    assert all(method == "GET" for method, _, _ in w["gh"].requests)


@pytest.mark.parametrize("change,code", [("head", "REPOSITORY_REF_CHANGED"), ("workspace", "REPOSITORY_BINDING_CHANGED"),
    ("repository_id", "REPOSITORY_BINDING_CHANGED")])
async def test_reviewed_binding_changes_before_effect(world, mock, change, code):
    pv = await preview(world)
    if change == "head":
        world["state"]["sha"] = "b" * 40
    elif change == "workspace":
        mock.ws_doc["workspaces"][0]["folderPath"] += "-changed"
    else:
        world["gh"].repository_id += 1
    op = await start(world, pv)
    assert op["status"] == "failed" and op["error_code"] == code
    assert not bat_writes(mock) and not world["runner"].scripts and not list(world["managed"].iterdir())


@pytest.mark.parametrize("phase", ["fetch", "prepare"])
async def test_lost_git_reply_reads_positive_receipt_after_ref_moves(world, mock, phase):
    def lost(_):
        world["state"]["sha"] = "f" * 40
        raise AmbiguousOutcome("lost SSH reply")
    world["runner"].after[phase] = lost
    op = await start(world)
    assert op["status"] == "uncertain", op
    world["d"].ops.db.execute("UPDATE operations SET next_run_at=0 WHERE operation_id=?", (op["operation_id"],))
    after = await settle(world, op)
    assert after["status"] == "succeeded", (after["status_reason"], after["steps"])
    assert world["runner"].phases.count(phase) == 1 and mock.channels().count("claude:start-session") == 1


@pytest.mark.parametrize("channel", ["claude:start-session", "claude:send-message"])
async def test_lost_bat_reply_never_resends(world, mock, monkeypatch, channel):
    client = world["d"].fleet.client("h1")
    invoke = client.invoke
    dropped = False
    async def lose(name, *args, **kwargs):
        nonlocal dropped
        result = await invoke(name, *args, **kwargs)
        if name == channel and not dropped:
            dropped = True
            raise InvokeTimeout("lost BAT reply")
        return result
    monkeypatch.setattr(client, "invoke", lose)
    op = await start(world)
    assert op["status"] == "uncertain", op
    world["state"]["sha"] = "c" * 40
    world["d"].ops.db.execute("UPDATE operations SET next_run_at=0 WHERE operation_id=?", (op["operation_id"],))
    after = await settle(world, op)
    assert after["status"] == "succeeded", after
    assert mock.channels().count(channel) == 1


async def test_http_rpc_mcp_principal_and_scope_parity(world, mock, monkeypatch):
    from bat_agent_connector.mcp_server import build_server
    from tests.test_mcp_principal import call
    d, port = world["d"], world["port"]
    reader, starter = token(d, "reader", "observe"), token(d, "agent-start", "start")
    path = "/api/v1/repository-previews"
    assert (await http(port, "POST", path, tok=starter, body=REQUEST))[0] == 403
    status, result = await http(port, "POST", path, tok=reader, body=REQUEST)
    assert status == 200, result
    pv = result["preview"]
    assert (await d.call_api("repository_preview", dict(REQUEST), READER))["preview"] == pv
    assert (await http(port, "POST", "/api/v1/operations", tok=reader, body=envelope(pv)))[0] == 403
    monkeypatch.setenv("BATC_TASK_URL", f"http://127.0.0.1:{port}/rpc")
    monkeypatch.setenv("BATC_API_TOKEN", starter)
    mcp, fleet = build_server(d.fleet.config, principal_only=True)
    try:
        out = await call(mcp, "work_continue_from_repository", {**REQUEST, "source_sha": pv["source_sha"],
            **pv["preconditions"], "prompt": "Work here", "idempotency_key": "mcp", "confirm": True, "wait_s": 0})
        assert "accepted" in out, out
        await settle_operations(d.ops)
        result = d.ops.list()["operations"][0]
        assert result["actor"] == "agent-start" and result["status"] == "succeeded", result
    finally:
        await fleet.close()


async def test_cleanup_uses_receipts_and_retains_manual(world, mock):
    op = await start(world)
    assert op["status"] == "succeeded", op
    items = cleanup._all(world["d"].ops)[0]
    carrier = next(x for x in items.values() if x.get("flavor") == "published" and x["kind"] == "worktree")
    assert carrier["proven"] and carrier["creation_evidence"]["intent_type"] == "repository.continue"
    assert op["operation_id"] in carrier["original_ids"] and carrier["source"]["operation"] == op["operation_id"]
    assert not any(x.get("path") == str(world["human"]) and x.get("proven") for x in items.values())


async def test_actual_reviewed_cleanup_removes_only_owned_carrier(world, mock):
    mock.ws_doc["terminals"] = [mock.ws_doc["terminals"][0]]
    mock.metas["sess-claude-0001"]["cwd"] = str(world["human"])
    op = await start(world)
    assert op["status"] == "succeeded", op
    sid, path = op["result"]["session_id"], op["result"]["worktree_path"]
    mock.metas[sid]["isStreaming"] = False
    before = snapshot(world["human"])
    cleaner = api_auth.Principal(STARTER.actor, frozenset({"observe", "cleanup"}))
    doc = await cleanup.preview(world["d"].ops, cleaner, {"kind": "host", "host": "h1"})
    assert doc["ready"], json.dumps([{k: i.get(k) for k in ("kind", "path", "session_id", "reasons", "observation")} for i in doc["items"]], indent=2)
    request = cleanup.apply_request(doc, "cleanup-published")
    accepted, _ = world["d"].ops.create(cleaner, **request)
    done = await settle(world, accepted)
    assert done["status"] == "succeeded", (done["status_reason"], done["steps"])
    assert not Path(path).exists() and snapshot(world["human"]) == before
    assert registry.get("h1", sid)["status"] == "cleaned"
    assert world["d"].ops.get(op["operation_id"])["result"]["worktree_path"] == path


async def test_lost_start_reply_cannot_adopt_rebound_registry(world, mock, monkeypatch):
    c = world["d"].fleet.client("h1")
    invoke = c.invoke
    async def rebound(channel, *args, **kwargs):
        result = await invoke(channel, *args, **kwargs)
        if channel == "claude:start-session":
            registry.update("h1", result["sessionId"], repository_binding="new-binding")
            raise InvokeTimeout("start ACK lost then row rebound")
        return result
    monkeypatch.setattr(c, "invoke", rebound)
    op = await start(world)
    assert op["status"] == "uncertain", op
    world["d"].ops.db.execute("UPDATE operations SET next_run_at=0 WHERE operation_id=?", (op["operation_id"],))
    after = await settle(world, op)
    assert after["status"] == "needs_attention" and after["error_code"] == "REPOSITORY_BINDING_CHANGED", after
    assert mock.channels().count("claude:start-session") == 1 and "claude:send-message" not in mock.channels()


async def test_cancel_unknown_start_keeps_same_carrier_until_readback(world, mock, monkeypatch):
    c = world["d"].fleet.client("h1")
    invoke = c.invoke
    async def lost(channel, *args, **kwargs):
        result = await invoke(channel, *args, **kwargs)
        if channel == "claude:start-session":
            raise InvokeTimeout("lost start ACK")
        return result
    monkeypatch.setattr(c, "invoke", lost)
    op = await start(world)
    assert op["status"] == "uncertain", op
    world["d"].ops.cancel(STARTER, op["operation_id"])
    world["d"].ops.db.execute("UPDATE operations SET next_run_at=0 WHERE operation_id=?", (op["operation_id"],))
    after = await settle(world, op)
    assert after["status"] == "cancelled", after
    assert Path(op["external_refs"]["worktree_path"]).is_dir()
    assert mock.channels().count("claude:start-session") == 1 and "claude:send-message" not in mock.channels()


@pytest.mark.parametrize("changed", ["missing", "incarnation"])
async def test_lost_start_reservation_is_not_proof_of_no_send(world, mock, monkeypatch, changed):
    c = world["d"].fleet.client("h1")
    invoke = c.invoke
    async def lost(channel, *args, **kwargs):
        result = await invoke(channel, *args, **kwargs)
        if channel == "claude:start-session":
            raise InvokeTimeout("lost start ACK")
        return result
    monkeypatch.setattr(c, "invoke", lost)
    op = await start(world)
    assert op["status"] == "uncertain", op
    sid = op["external_refs"]["session_id"]
    if changed == "missing":
        document = json.loads(registry.registry_path().read_text())
        document["sessions"] = [e for e in document["sessions"] if e["session_id"] != sid]
        registry.registry_path().write_text(json.dumps(document))
    else:
        registry.update("h1", sid, created_at=registry.get("h1", sid)["created_at"] + 1)
    world["d"].ops.db.execute("UPDATE operations SET next_run_at=0 WHERE operation_id=?", (op["operation_id"],))
    after = await settle(world, op)
    assert after["status"] == "needs_attention" and after["error_code"] == "REPOSITORY_START_UNPROVEN", after
    assert mock.channels().count("claude:start-session") == 1 and "claude:send-message" not in mock.channels()


def test_config_binds_exact_workspace_once(world):
    raw = world["raw"]
    raw["github"]["repos"].append({"repository": "other/repo", "sync": {
        "remote_url": str(world["remote"]), "bindings": [{"host": "h1", "workspace_id": "ws-1"}]}})
    with pytest.raises(ConfigError, match="only one"):
        parse_config(raw)


@pytest.mark.parametrize("params", [{"agent": []}, {"prompt": {}}, {"source_sha": "main"}, {"source_ref": "refs/tags/v1"},
    {"source_ref": "refs/heads/a.lock"}, {"remote_url": "https://example.invalid/other"}, {"host": "elsewhere"}])
async def test_malformed_admission_and_unbound_target(world, mock, params):
    pv = await preview(world)
    with pytest.raises(OperationError):
        world["d"].ops.create(STARTER, **envelope(pv, **params))
    assert not bat_writes(mock) and not world["d"].ops.list()["operations"]
    with pytest.raises(OperationError, match="NOT_BOUND"):
        await repository_sync.preview(world["d"].ops, READER, {**REQUEST, "workspace_id": "demo-project"})


def test_cli_readonly_confirm_and_no_key_independence(monkeypatch, capsys):
    from bat_agent_connector import task_daemon
    calls = []
    monkeypatch.setattr(task_daemon, "request", lambda method, **params: calls.append((method, params)) or {"operation": {"status": "accepted"}})
    args = ["repository", "continue", "o/r", "h1", "ws-1", "--ref", "refs/heads/main", "--sha", "a" * 40,
            "--repository-id", "1", "--binding-digest", "b" * 64, "--prompt", "work"]
    assert cli.main(["--read-only", *args, "--confirm"]) != 0 and not calls
    assert cli.main(args) != 0 and not calls
    assert cli.main([*args, "--confirm"]) == 0
    assert cli.main([*args, "--confirm"]) == 0
    assert calls[0][1]["idempotency_key"] != calls[1][1]["idempotency_key"]
    assert all(p["target"] == TARGET and p["entry"] == "cli" for _, p in calls)


@pytest.mark.parametrize("status,exit_code", [("failed", 1), ("cancelled", 1), ("accepted", 0), ("uncertain", 0), ("succeeded", 0)])
def test_cli_reports_durable_outcome_with_original_key(monkeypatch, capsys, status, exit_code):
    from bat_agent_connector import task_daemon
    monkeypatch.setattr(task_daemon, "request", lambda *args, **kw: {"operation": {
        "status": status, "operation_id": "op_fixed", "status_reason": "stored reason", "error_code": "STORED_CODE"}})
    args = ["repository", "continue", "o/r", "h1", "ws-1", "--ref", "refs/heads/main", "--sha", "a" * 40,
            "--repository-id", "1", "--binding-digest", "b" * 64, "--prompt", "work", "--key", "original-key", "--confirm"]
    assert cli.main(args) == exit_code
    result = json.loads(capsys.readouterr().out)
    assert result["idempotency_key"] == "original-key" and result["operation"]["status"] == status
    assert result["operation"]["operation_id"] == "op_fixed" and result["operation"]["status_reason"] == "stored reason"


async def test_nonprincipal_mcp_replays_after_tier_disabled_but_new_key_refuses(world, mock, monkeypatch):
    from bat_agent_connector.mcp_server import build_server
    from tests.test_mcp_principal import call
    pv = await preview(world)
    op = await start(world, pv, agent="claude")
    assert op["status"] == "succeeded", op
    count = len(bat_writes(mock))
    d, port = world["d"], world["port"]
    d.fleet.config.host("h1").writes = False
    d.fleet.config.host("h1").orchestrate = False
    monkeypatch.setenv("BATC_TASK_URL", f"http://127.0.0.1:{port}/rpc")
    monkeypatch.setenv("BATC_API_TOKEN", token(d, "starter", "start"))
    server, fleet = build_server(d.fleet.config)
    try:
        assert "work_continue_from_repository" in {t.name for t in await server.list_tools()}
        args = {**REQUEST, "source_sha": pv["source_sha"], **pv["preconditions"], "prompt": "Work here",
                "idempotency_key": "published-1", "agent": "claude", "confirm": True, "wait_s": 0}
        replay = await call(server, "work_continue_from_repository", args)
        assert op["operation_id"] in replay and "succeeded" in replay, replay
        refused = await call(server, "work_continue_from_repository", {**args, "idempotency_key": "new"})
        assert "REPOSITORY_HOST_UNAVAILABLE" in refused, refused
        assert len(bat_writes(mock)) == count and len(d.ops.list()["operations"]) == 1
    finally:
        await fleet.close()
    reader, fleet = build_server(d.fleet.config, read_only=True)
    try:
        assert "work_continue_from_repository" not in {t.name for t in await reader.list_tools()}
    finally:
        await fleet.close()


async def test_max_in_flight_one_final_guard(world):
    world["d"].fleet.client("h1")._sem = asyncio.Semaphore(1)
    op = await start(world)
    assert op["status"] == "succeeded", op


@pytest.mark.parametrize("boundary", ["claude:start-session", "claude:send-message", "workspace:save"])
@pytest.mark.parametrize("change", ["workspace", "policy"])
async def test_final_bat_frame_refuses_changed_binding(world, mock, monkeypatch, boundary, change):
    d = world["d"]
    d.fleet.config.host("h1").orchestrate_register_tabs = boundary == "workspace:save"
    c = d.fleet.client("h1")
    invoke = c._invoke_checked
    async def race(channel, params, *args, **kwargs):
        if channel == boundary:
            if change == "workspace":
                mock.ws_doc["workspaces"][0]["folderPath"] += "-rebound"
            else:
                d.fleet.config.host("h1").default_permission_mode = "allow_all"
        return await invoke(channel, params, *args, **kwargs)
    monkeypatch.setattr(c, "_invoke_checked", race)
    op = await start(world)
    assert op["status"] in {"failed", "needs_attention"}, op
    assert boundary not in mock.channels()
    assert Path(op["external_refs"]["worktree_path"]).is_dir()


async def test_git_remote_head_disagrees_with_api(world, mock):
    world["runner"].before["fetch"] = lambda _: git(world["remote"], "update-ref", "-d", "refs/heads/main")
    op = await start(world)
    assert op["status"] == "needs_attention" and op["error_code"] == "REPOSITORY_REF_CHANGED"
    assert not bat_writes(mock)


@pytest.mark.parametrize("tamper", ["config", "symlink", "hardlink", "wrong-marker"])
async def test_existing_clone_cannot_redirect_writes(world, mock, tamper):
    human_before = snapshot(world["human"])
    def corrupt(req):
        clone = Path(req["clone_path"])
        if tamper == "config":
            git(clone, "config", "core.hooksPath", str(world["human"]))
        elif tamper == "wrong-marker":
            git(clone, "config", "batc.operation", "op_" + "0" * 32)
        else:
            p = clone / ".git" / "unsafe"
            if tamper == "symlink":
                p.symlink_to(world["human"] / ".git")
            else:
                p.hardlink_to(world["human"] / ".git" / "config")
    world["runner"].after["fetch"] = corrupt
    op = await start(world)
    assert op["status"] == "needs_attention" and op["error_code"] in {"REPOSITORY_CLONE_TAMPERED", "REPOSITORY_CLONE_NOT_OURS"}
    assert not bat_writes(mock) and snapshot(world["human"]) == human_before


async def test_cleanup_reservation_before_prepare_blocks_all_frames(world, mock):
    def reserve(req):
        cleanup._mark({"resource_id": "fixture-published", "generation": "original", "host": "h1",
                       "kind": "worktree", "path": req["worktree_path"]}, "cleanup-fixture", "reserved")
    world["runner"].after["fetch"] = reserve
    op = await start(world)
    assert op["status"] == "failed" and op["error_code"] == "CLEANUP_IN_PROGRESS", op
    assert not bat_writes(mock) and not Path(op["external_refs"]["worktree_path"]).exists()


async def test_all_positive_receipts_complete_without_current_policy_or_ref(world, mock, monkeypatch):
    d = world["d"]
    original = d.ops._transition
    lost = False
    def crash(oid, status, **kwargs):
        nonlocal lost
        if status == "succeeded" and not lost:
            lost = True
            raise OSError("local final bookkeeping unavailable")
        return original(oid, status, **kwargs)
    monkeypatch.setattr(d.ops, "_transition", crash)
    op, _ = d.ops.create(STARTER, **envelope(await preview(world)))
    with pytest.raises(OSError, match="local final"):
        await d.ops._execute(op["operation_id"])
    op = d.ops.get(op["operation_id"])
    assert all(s["status"] == "succeeded" for s in op["steps"]), op
    count = len(bat_writes(mock))
    world["state"]["sha"] = "d" * 40
    d.fleet.config.host("h1").writes = False
    mock.ws_doc["workspaces"] = []
    await d.fleet.close()
    await d.inventory.close()
    db_path = d.journal.path
    d.journal.close()
    world["d"] = TaskDaemon(d.fleet.config, db_path)
    world["d"].ops.context["git_runner"] = world["runner"]
    after = await settle(world, op)
    assert after["status"] == "succeeded" and len(bat_writes(mock)) == count, after
