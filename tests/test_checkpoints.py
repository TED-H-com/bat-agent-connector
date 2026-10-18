"""Checkpoint continuation (plan §12): read a person's session, then start managed work at its commit.

Git runs for real in temp repositories (a local runner stands in for SSH); BAT is the mock.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from bat_agent_connector import (
    api_auth,
    checkpoints,
    confinement,
    lifecycle,
    orchestrate,
    registry,
    resource_policy,
)
from bat_agent_connector.channels import GUARDED_CHANNELS, ORCHESTRATE_CHANNELS, WRITE_CHANNELS
from bat_agent_connector.errors import ResourceReadOnly
from bat_agent_connector.operations import OperationError
from bat_agent_connector.task_daemon import TaskDaemon
from tests.conftest import build_checkpoint_repo, make_config
from tests.operation_helpers import settle_operations

MANUAL = "sess-claude-0001"
OPERATOR = api_auth.Principal("operator-dashboard", frozenset({"observe", "operate", "start"}))
ORIGIN = "https://github.example/o/r.git"


def git(cwd, *args) -> str:
    return subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True).stdout.strip()


class LocalRunner:
    """Runs the host git script locally (tests only); the production runner wraps it in ``ssh alias``."""

    def __init__(self) -> None:
        self.scripts: list[str] = []

    def available(self, host: str) -> bool:
        return True

    async def run(self, host: str, script: str, timeout_s: float | None = None) -> str:
        self.scripts.append(script)
        return await checkpoints._run(("sh", "-c", script), timeout_s)


class RealGitLog(dict):
    """mock BAT ``git:log`` answers from real repositories (BAT runs ``git log --pretty=%H``)."""

    def __contains__(self, cwd) -> bool:
        return subprocess.run(["git", "-C", str(cwd), "rev-parse"], capture_output=True).returncode == 0

    def __getitem__(self, cwd):
        out = git(cwd, "log", "--pretty=format:%H||%s", "-n", "200")
        return [{"hash": h, "message": m} for h, _, m in (line.partition("||") for line in out.splitlines())]


def snapshot(repo) -> dict:
    # --no-optional-locks: a plain `git status` here would refresh the index itself and hide a rewrite.
    index = repo / ".git" / "index"
    return {"refs": git(repo, "for-each-ref"), "worktrees": git(repo, "worktree", "list", "--porcelain"),
            "status": git(repo, "--no-optional-locks", "status", "--porcelain"),
            "config": git(repo, "config", "--local", "--list"), "head": git(repo, "rev-parse", "HEAD"),
            "index": (hashlib.sha256(index.read_bytes()).hexdigest(), index.stat().st_mtime_ns)}


def bat_git_status(p):
    """What BAT's git:status runs: a plain `git status`, which may refresh and rewrite .git/index."""
    r = subprocess.run(["git", "-C", p["cwd"], "status", "--porcelain", "--untracked-files=all"],
                       capture_output=True, text=True)
    return [{"path": line[3:], "status": line[:2].strip()} for line in r.stdout.splitlines()]


@pytest.fixture
def human(tmp_path, checkpoint_repo_seed):
    return make_human(tmp_path, checkpoint_repo_seed)


def make_human(tmp_path, seed=None):
    repo = tmp_path / "human" / "app"
    if seed is None:
        return build_checkpoint_repo(repo)
    # A full copy preserves config, refs and reflogs without sharing mutable files
    # or alternates with other tests (which may delete or corrupt their own repo).
    shutil.copytree(seed, repo)
    return repo


@pytest.fixture
def daemon(mock, human, tmp_path):
    mock.metas[MANUAL] = {"cwd": str(human), "isStreaming": False}
    mock.git_logs = RealGitLog()
    mock.handlers["git:status"] = bat_git_status
    d = TaskDaemon(make_config(mock, writes=True, orchestrate=True, managed_roots=[str(tmp_path / "managed")],
                               safety={"write_min_interval_s": 0}), tmp_path / "tasks.db")
    d.ops.context["git_runner"] = LocalRunner()
    from tests.conftest import controller_scope
    with controller_scope(d.journal, OPERATOR):
        yield d
    d.journal.close()


def bat_writes(mock, sid=None):
    writes = WRITE_CHANNELS | ORCHESTRATE_CHANNELS | GUARDED_CHANNELS
    return [i for i in mock.invokes if i["channel"] in writes
            and (sid is None or (i.get("params") or {}).get("sessionId") == sid)]


async def run(d, action, target, params=None, key=None):
    op, _ = d.ops.create(OPERATOR, action=action, target=target, params=params or {},
                         idempotency_key=key or f"{action}-{json.dumps(target, sort_keys=True)}-{params}")
    await settle_operations(d.ops)
    return d.ops.get(op["operation_id"])


async def make_checkpoint(d, **params):
    await d.inventory.refresh_host("h1")
    op = await run(d, "checkpoint.create", {"host": "h1", "session_id": MANUAL}, {"last_n": 5, **params})
    assert op["status"] == "succeeded", op
    return checkpoints.get(d.journal.db, op["result"]["checkpoint_id"])


@pytest.mark.parametrize("phase", ["admission", "frame"])
async def test_checkpoint_continue_host_account_refusal_needs_attention_and_resumes(daemon, mock, phase):
    """A10: an unsent host-account refusal resumes the same operation and session ID."""
    from dataclasses import replace

    from tests.test_confinement import ACCOUNT, AccountRunner
    from tests.test_confinement_recovery import RefuseAtFrame

    cp = await make_checkpoint(daemon)
    hc = daemon.fleet.config.host("h1")
    daemon.fleet.config.hosts["h1"] = replace(hc, confinement=ACCOUNT)
    daemon.fleet.confinement_runner = AccountRunner("unknown") if phase == "admission" else RefuseAtFrame()
    op = await run(daemon, "checkpoint.continue", {"checkpoint_id": cp["checkpoint_id"]}, {"instructions": "go"})
    assert op["status"] == "needs_attention" and op["error_code"] == "HOST_ACCOUNT_UNVERIFIED"
    assert "claude:start-session" not in mock.channels()
    request = daemon.journal.db.execute("SELECT request FROM operation_steps WHERE operation_id=? AND name=?",
                                        (op["operation_id"], "session.start")).fetchone()[0]
    sid = json.loads(request)["session_id"]
    daemon.fleet.confinement_runner = AccountRunner()
    daemon.ops.resume(OPERATOR, op["operation_id"])
    await daemon.ops.drain(timeout=30)
    done = daemon.ops.get(op["operation_id"])
    assert done["status"] == "succeeded", done
    assert registry.get("h1", sid)["status"] == "active"
    assert sum(e["session_id"] == sid for e in registry.list_entries("h1")) == 1
    assert mock.channels().count("claude:start-session") == 1
    await daemon.fleet.close()
    await daemon.inventory.close()


async def test_checkpoint_reconcile_legacy_reservation_requires_evidence(daemon, mock, monkeypatch):
    """A10: an operation in flight across the upgrade needs attention instead of endless readbacks."""
    from bat_agent_connector.errors import InvokeTimeout

    cp = await make_checkpoint(daemon)
    start = orchestrate.session_start

    async def upgrade_lost_result(*args, **kwargs):
        result = await start(*args, **kwargs)
        registry.update("h1", result["session_id"], confinement=None)
        raise InvokeTimeout("fixture result lost across upgrade")

    monkeypatch.setattr(orchestrate, "session_start", upgrade_lost_result)
    op = await run(daemon, "checkpoint.continue", {"checkpoint_id": cp["checkpoint_id"]}, {"instructions": "go"})
    assert op["status"] == "uncertain"
    daemon.journal.db.execute("UPDATE operations SET next_run_at=0 WHERE operation_id=?", (op["operation_id"],))
    await daemon.ops.drain(timeout=30)
    done = daemon.ops.get(op["operation_id"])
    assert done["status"] == "needs_attention" and done["error_code"] == "CONFINEMENT_EVIDENCE_MISSING"
    assert mock.channels().count("claude:start-session") == 1
    await daemon.fleet.close()
    await daemon.inventory.close()


async def test_checkpoint_reads_a_person_session_and_writes_nothing(daemon, mock, human):
    # Same content, new mtime: the index's stat data is stale, so a plain `git status` would rewrite it.
    st = (human / "notes.txt").stat()
    os.utime(human / "notes.txt", ns=(st.st_atime_ns, st.st_mtime_ns + 5_000_000_000))
    before = snapshot(human)
    cp = await make_checkpoint(daemon)
    assert cp["source_provenance"] == "manual" and cp["commit_sha"] == before["head"]
    assert cp["branch"] == "main" and cp["dirty"] == 0 and cp["repo_root"] == str(human)
    assert not [i for i in mock.invokes if i["channel"] == "git:status"]  # BAT's git:status may write the index
    assert len(cp["excerpt"]) == 5 and all(m["text"] for m in cp["excerpt"])
    assert bat_writes(mock) == []
    assert snapshot(human) == before
    events = daemon.journal.api_events(0, 100, resource_type="checkpoint")["events"]
    assert [e["kind"] for e in events] == ["checkpoint.created"]


async def test_checkpoint_refuses_a_commit_the_source_does_not_have(daemon, mock):
    await daemon.inventory.refresh_host("h1")
    op = await run(daemon, "checkpoint.create", {"host": "h1", "session_id": MANUAL}, {"commit": "e" * 40})
    assert op["status"] == "failed" and op["error_code"] == "COMMIT_NOT_FOUND"
    with pytest.raises(OperationError) as e:
        daemon.ops.create(OPERATOR, action="checkpoint.create", target={"host": "h1", "session_id": "nope"},
                          idempotency_key="k-missing")
    assert e.value.code == "NOT_FOUND"


@pytest.mark.parametrize("agent", ["claude", "codex"])
async def test_a10_checkpoint_absolute_path_carries_confinement(daemon, mock, human, agent):
    from dataclasses import replace
    daemon.fleet.config.hosts["h1"] = replace(daemon.fleet.config.host("h1"), default_permission_mode="allow_all")
    mock.states[MANUAL]["messages"][-2]["content"] = f"Write changes to {human}/notes.txt"
    first = git(human, "rev-list", "--max-parents=0", "HEAD")
    cp = await make_checkpoint(daemon, commit=first)
    mock.git_status[str(human)] = [{"path": "notes.txt", "status": "M"}]  # the person keeps working meanwhile
    before = snapshot(human)
    op = await run(daemon, "checkpoint.continue", {"checkpoint_id": cp["checkpoint_id"]},
                   {"instructions": "Add a changelog entry", "agent": agent})
    assert op["status"] == "succeeded", op
    r = op["result"]
    wt, sid = r["worktree_path"], r["session_id"]
    assert git(wt, "rev-parse", "HEAD") == first and git(wt, "status", "--porcelain") == ""
    assert git(wt, "rev-parse", "--abbrev-ref", "HEAD") == r["branch"] == "batc/cp-" + op["operation_id"][3:15]
    clone = op["external_refs"]["clone_path"]
    assert git(clone, "config", "--get", "remote.origin.url") == ORIGIN  # pushes go to GitHub, not the person
    assert git(clone, "config", "--get", "batc.managed-clone") == "true"
    assert snapshot(human) == before

    starts = [i for i in mock.invokes if i["channel"] == "claude:start-session"]
    assert len(starts) == 1 and starts[0]["params"]["options"]["cwd"] == wt
    # A10: plain default is prompt gated; cwd and acceptEdits never prove path confinement.
    assert r["write_scope"] == "confined"
    assert {k: starts[0]["params"]["options"][k] for k in confinement.CONFINED_OPTIONS[agent]} == confinement.CONFINED_OPTIONS[agent]
    sends = [i for i in mock.invokes if i["channel"] == "claude:send-message"]
    assert len(sends) == 1 and sends[0]["params"]["sessionId"] == sid
    prompt = sends[0]["params"]["prompt"]
    assert "Add a changelog entry" in prompt and first in prompt and "[assistant]" in prompt
    assert str(human) in prompt
    assert r["confinement"]["level"] == ("prompt_gated" if agent == "claude" else "os_sandbox")
    assert r["confinement"]["verification"]["status"] == "options_confirmed"
    assert registry.get("h1", sid)["confinement"] == r["confinement"]
    with pytest.raises(confinement.ConfinementRefused, match="CONFINEMENT_RAISE_REFUSED"):
        await lifecycle.session_set_permissions(daemon.fleet, "h1", sid, confirm=True, force=True)
    mock.states[sid]["pendingPermission"] = {"toolUseId": "test", "toolName": "Bash", "input": {}}
    bulk = await lifecycle.approve_pending(daemon.fleet, "h1", dry_run=True)
    assert next(x for x in bulk["sessions"] if x["session_id"] == sid)["skipped"] == "confined"
    mock.states[sid]["pendingPermission"] = None
    assert "not instructions" in prompt  # the person's conversation is fenced off as background
    assert bat_writes(mock, MANUAL) == []  # nothing was sent to the person's session

    hc = daemon.fleet.config.host("h1")
    entries = resource_policy._entries("h1")
    assert resource_policy.classify_row_for_read(hc, sid, has_tab=False, entries=entries)["api_access"] == "managed"
    follow = await run(daemon, "session.send", {"host": "h1", "session_id": sid}, {"text": "and a test"})
    assert follow["status"] == "succeeded", follow
    # Inspecting the new session never runs BAT's git:status (a plain `git status`) in the person's folder.
    mock.invokes.clear()
    st = await orchestrate.session_worktree_status(daemon.fleet, "h1", sid)
    assert "main_checkout_dirty_files" not in st and st["main_checkout_note"]
    assert not [i for i in mock.invokes if i["channel"].startswith("git:") and i["params"].get("cwd") == str(human)]

    # A second continuation reuses the connector clone and gets its own worktree and branch.
    again = await run(daemon, "checkpoint.continue", {"checkpoint_id": cp["checkpoint_id"]},
                      {"instructions": "Try another approach"}, key="second")
    assert again["status"] == "succeeded", again
    assert again["external_refs"]["clone_path"] == clone and again["result"]["worktree_path"] != wt
    runs = checkpoints.get(daemon.journal.db, cp["checkpoint_id"])["runs"]
    assert [x["session_id"] for x in runs] == [sid, again["result"]["session_id"]]
    assert snapshot(human) == before


async def test_continue_refusals_happen_before_anything_is_recorded(daemon, mock, human):
    cp = await make_checkpoint(daemon)
    target = {"checkpoint_id": cp["checkpoint_id"]}
    for params, code in [({"instructions": " "}, "INVALID_PARAMS"),
                         ({"instructions": "x", "agent": "gpt"}, "INVALID_PARAMS")]:
        with pytest.raises(OperationError) as e:
            daemon.ops.create(OPERATOR, action="checkpoint.continue", target=target, params=params,
                              idempotency_key=f"bad-{params}")
        assert e.value.code == code
    with pytest.raises(OperationError) as e:
        daemon.ops.create(OPERATOR, action="checkpoint.continue", target={"checkpoint_id": "cp_" + "0" * 32},
                          params={"instructions": "x"}, idempotency_key="k-none")
    assert e.value.status == 404
    # Starting an agent is its own grant: an operate token (send, answer, interrupt) does not include it.
    hermes = api_auth.Principal("hermes", frozenset({"observe", "operate"}))
    with pytest.raises(OperationError) as e:
        daemon.ops.create(hermes, action="checkpoint.continue", target=target, params={"instructions": "x"},
                          idempotency_key="k-scope")
    assert e.value.status == 403
    daemon.ops.context["git_runner"] = None
    with pytest.raises(OperationError) as e:
        daemon.ops.create(OPERATOR, action="checkpoint.continue", target=target, params={"instructions": "x"},
                          idempotency_key="k-runner")
    assert e.value.code == "GIT_RUNNER_UNAVAILABLE"
    viewer = api_auth.Principal("viewer", frozenset({"observe"}))
    with pytest.raises(OperationError) as e:
        daemon.ops.create(viewer, action="checkpoint.create", target={"host": "h1", "session_id": MANUAL},
                          idempotency_key="k-viewer")
    assert e.value.code == "FORBIDDEN"


async def test_continue_never_adopts_a_folder_it_did_not_create(daemon, mock, human, tmp_path):
    cp = await make_checkpoint(daemon)
    clone = checkpoints.clone_path(str(tmp_path / "managed"), "h1", str(human))
    subprocess.run(["git", "init", "--template=", "-q", clone], check=True)  # someone else's repo at the clone path
    op = await run(daemon, "checkpoint.continue", {"checkpoint_id": cp["checkpoint_id"]}, {"instructions": "x"})
    assert op["status"] == "failed" and op["error_code"] == "GIT_FAILED"
    assert "not a connector clone" in op["status_reason"]
    assert not any(i["channel"] == "claude:start-session" for i in mock.invokes)


async def test_prepare_script_is_idempotent(human, tmp_path):
    runner = LocalRunner()
    head = git(human, "rev-parse", "HEAD")
    dest = str(tmp_path / "managed" / "app-1")
    args = (str(human), dest, dest + "/.bat-worktrees/w1", "batc/cp-w1", head, dest + ".tmp")
    first = await runner.run("h1", checkpoints.prepare_script(*args))
    second = await runner.run("h1", checkpoints.prepare_script(*args))
    assert first == second == f"{head}\n0"
    assert git(dest, "worktree", "list", "--porcelain").count("worktree ") == 2


def test_first_prompt_keeps_the_newest_excerpt_within_the_limit():
    cp = {"repo_root": "/r/app", "branch": "main", "commit_sha": "a" * 40, "dirty": 2,
          "excerpt": [{"role": "user", "text": f"message {i} " + "x" * 3000} for i in range(20)]}
    text = checkpoints.first_prompt(cp, worktree="/m/app/.bat-worktrees/w", branch="batc/cp-1",
                                    instructions="Ship it")
    assert len(text) <= 20_000 and text.endswith("Task:\nShip it")
    assert "message 19 " in text and "message 0 " not in text and "2 uncommitted" in text


async def test_http_lists_checkpoints_and_reports_where_they_can_continue(daemon, mock):
    cp = await make_checkpoint(daemon)
    server = await asyncio.start_server(daemon._handle, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    with daemon.journal.tx():
        tok = api_auth.issue(daemon.journal.db, "viewer", ["observe"])

    async def get(path):
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        writer.write(f"GET {path} HTTP/1.1\r\nHost: 127.0.0.1\r\nAuthorization: Bearer {tok}\r\n\r\n".encode())
        await writer.drain()
        raw = await reader.read()
        writer.close()
        return json.loads(raw.partition(b"\r\n\r\n")[2])

    try:
        listed = await get(f"/api/v1/checkpoints?session_id={MANUAL}")
        assert [c["checkpoint_id"] for c in listed["checkpoints"]] == [cp["checkpoint_id"]]
        assert listed["checkpoints"][0]["excerpt_messages"] == 5
        one = await get(f"/api/v1/checkpoints/{cp['checkpoint_id']}")
        assert one["checkpoint"]["commit_sha"] == cp["commit_sha"] and one["checkpoint"]["runs"] == []
        caps = await get("/api/v1/capabilities")
        assert caps["features"]["checkpoints"] == ["h1"]
    finally:
        server.close()
        await server.wait_closed()
        await daemon.fleet.close()
        await daemon.inventory.close()


async def test_a_lost_start_reply_is_read_back_not_started_again(daemon, mock, human, tmp_path, monkeypatch):
    from bat_agent_connector.errors import InvokeTimeout

    cp = await make_checkpoint(daemon)
    client = daemon.fleet.client("h1")
    real_invoke = client.invoke

    async def start_reply_lost(channel, params=None, **kw):
        r = await real_invoke(channel, params, **kw)
        if channel == "claude:start-session":  # BAT started the session and its reply was lost
            raise InvokeTimeout("claude:start-session timed out")
        return r

    monkeypatch.setattr(client, "invoke", start_reply_lost)
    calls = lambda: [i for i in mock.invokes if i["channel"] == "claude:start-session"]  # noqa: E731
    meta_before = dict(mock.metas)
    op = await run(daemon, "checkpoint.continue", {"checkpoint_id": cp["checkpoint_id"]}, {"instructions": "go"})
    assert op["status"] == "uncertain"
    sid = op["external_refs"]["session_id"]
    # BAT has not loaded it yet: a null meta while the reservation exists is not proof of "never started".
    hidden = mock.metas.pop(sid)
    daemon.journal.db.execute("UPDATE operations SET next_run_at=0 WHERE operation_id=?", (op["operation_id"],))
    await settle_operations(daemon.ops)
    assert daemon.ops.get(op["operation_id"])["status"] == "uncertain" and len(calls()) == 1
    mock.metas[sid] = hidden
    monkeypatch.setattr(client, "invoke", real_invoke)
    await daemon.fleet.close()
    await daemon.inventory.close()
    daemon.journal.close()

    d2 = TaskDaemon(make_config(mock, writes=True, orchestrate=True, managed_roots=[str(tmp_path / "managed")],
                                safety={"write_min_interval_s": 0}), tmp_path / "tasks.db")  # the daemon restarts
    d2.ops.context["git_runner"] = LocalRunner()
    d2.journal.db.execute("UPDATE operations SET next_run_at=0 WHERE operation_id=?", (op["operation_id"],))
    await settle_operations(d2.ops)
    done = d2.ops.get(op["operation_id"])
    assert done["status"] == "succeeded", done
    assert len(calls()) == 1
    assert [i["params"]["sessionId"] for i in mock.invokes if i["channel"] == "claude:send-message"] == [sid]
    entry = registry.get("h1", sid)  # proven by read-back, so it carries what a normal start records
    assert entry["status"] == "active" and entry["worktree_path"] == entry["cwd"]
    assert entry["write_scope"] == "confined" and entry["permission_mode_claude"] == "default"
    assert checkpoints.started_from(d2.journal.db, "h1", sid)["checkpoint_id"] == cp["checkpoint_id"]
    assert set(meta_before) <= set(mock.metas)
    await d2.fleet.close()
    await d2.inventory.close()
    d2.journal.close()


@pytest.mark.parametrize("observed", ["different", "missing", "permissions"])
async def test_checkpoint_start_readback_refuses_identity_and_terminal_mismatch(daemon, mock, monkeypatch, observed):
    """A10: start reconciliation never promotes a foreign folder or replaces mismatch evidence."""
    from bat_agent_connector.errors import InvokeTimeout

    cp = await make_checkpoint(daemon)
    client = daemon.fleet.client("h1")
    invoke = client.invoke

    async def lost_ack(channel, params=None, **kwargs):
        result = await invoke(channel, params, **kwargs)
        if channel == "claude:start-session":
            raise InvokeTimeout("fixture lost start acknowledgement")
        return result

    monkeypatch.setattr(client, "invoke", lost_ack)
    op = await run(daemon, "checkpoint.continue", {"checkpoint_id": cp["checkpoint_id"]}, {"instructions": "go"})
    assert op["status"] == "uncertain"
    sid = op["external_refs"]["session_id"]
    row = registry.get("h1", sid)
    if observed == "missing":
        del mock.metas[sid]["cwd"]
        code = "CONFINEMENT_START_UNSETTLED"
    elif observed == "different":
        mock.metas[sid]["cwd"] = "/srv/another-checkout"
        code = "START_SESSION_MISMATCH"
    else:
        mock.metas[sid]["permissionMode"] = "bypassPermissions"
        code = "CONFINEMENT_MISMATCH"
    monkeypatch.setattr(client, "invoke", invoke)
    daemon.journal.db.execute("UPDATE operations SET next_run_at=0 WHERE operation_id=?", (op["operation_id"],))
    await daemon.ops.drain(timeout=60)
    refused = daemon.ops.get(op["operation_id"])
    assert refused["status"] == "needs_attention" and refused["error_code"] == code
    kept = registry.get("h1", sid)
    assert kept["status"] == row["status"] != "active"
    assert mock.channels().count("claude:start-session") == 1 and "claude:send-message" not in mock.channels()
    if observed != "missing":
        record = kept["confinement"]
        mock.metas[sid]["cwd"] = row["cwd"]
        mock.metas[sid].update(record["options"])
        reads = mock.channels().count("claude:get-session-meta")
        daemon.ops.resume(OPERATOR, op["operation_id"])
        await daemon.ops.drain(timeout=60)
        still_refused = daemon.ops.get(op["operation_id"])
        assert still_refused["status"] == "needs_attention" and still_refused["error_code"] == code
        assert mock.channels().count("claude:get-session-meta") == reads
        assert registry.get("h1", sid)["confinement"] == record
        assert "claude:send-message" not in mock.channels()
    await daemon.fleet.close()
    await daemon.inventory.close()


@pytest.mark.parametrize("failure", ["timeout", "disconnect"])
async def test_checkpoint_accepted_send_survives_evidence_read_failure(daemon, mock, monkeypatch, failure):
    """A10/§12: evidence loss after the durable send cannot fail or duplicate continuation."""
    from bat_agent_connector.errors import ConnectionLost, InvokeTimeout

    cp = await make_checkpoint(daemon)
    client = daemon.fleet.client("h1")
    invoke = client.invoke
    accepted = False
    evidence_failures = []

    async def fail_after_send(channel, params=None, **kwargs):
        nonlocal accepted
        if accepted and channel == "claude:get-session-meta":
            evidence_failures.append(params["sessionId"])
            raise InvokeTimeout("fixture evidence timeout") if failure == "timeout" else ConnectionLost("fixture disconnect")
        result = await invoke(channel, params, **kwargs)
        if channel == "claude:send-message":
            assert result["accepted"]
            accepted = True
        return result

    monkeypatch.setattr(client, "invoke", fail_after_send)
    target, params = {"checkpoint_id": cp["checkpoint_id"]}, {"instructions": "go"}
    op = await run(daemon, "checkpoint.continue", target, params, key="accepted-evidence")
    assert op["status"] == "succeeded", op
    result = op["result"]
    assert result["session_id"] in evidence_failures
    assert result["current_verification"]["status"] == "unknown"
    assert result["current_verification"]["reason"] == "readback_failed"
    assert result["confinement"] == registry.get("h1", result["session_id"])["confinement"]
    assert result["confinement"]["verification"]["status"] == "options_confirmed"
    assert next(s for s in op["steps"] if s["name"] == "send")["status"] == "succeeded"
    again = await run(daemon, "checkpoint.continue", target, params, key="accepted-evidence")
    assert again == op
    assert mock.channels().count("claude:start-session") == 1 and mock.channels().count("claude:send-message") == 1
    await daemon.fleet.close()
    await daemon.inventory.close()


async def test_a_lost_codex_instruction_is_found_in_the_transcript(daemon, mock, human, monkeypatch):
    from bat_agent_connector import service
    from bat_agent_connector.errors import InvokeTimeout

    cp = await make_checkpoint(daemon)
    mock.echo_sends = True  # BAT appends the prompt to the session's transcript
    real_send = service.session_send

    async def sent_but_reply_lost(*a, **kw):
        await real_send(*a, **kw)
        raise InvokeTimeout("claude:send-message timed out")

    monkeypatch.setattr(service, "session_send", sent_but_reply_lost)
    op = await run(daemon, "checkpoint.continue", {"checkpoint_id": cp["checkpoint_id"]},
                   {"instructions": "go", "agent": "codex"})
    assert op["status"] == "uncertain"
    monkeypatch.setattr(service, "session_send", real_send)
    daemon.journal.db.execute("UPDATE operations SET next_run_at=0 WHERE operation_id=?", (op["operation_id"],))
    await settle_operations(daemon.ops)
    done = daemon.ops.get(op["operation_id"])
    assert done["status"] == "succeeded", done
    assert len([i for i in mock.invokes if i["channel"] == "claude:send-message"]) == 1  # never sent twice
    step = daemon.journal.db.execute("SELECT response FROM operation_steps WHERE operation_id=? AND name='send'",
                                     (op["operation_id"],)).fetchone()
    assert '"settled_by":"transcript"' in step["response"]
    await daemon.fleet.close()
    await daemon.inventory.close()


async def test_preview_source_moved_and_unobserved_changes(daemon, mock, human):
    pv = await checkpoints.preview(daemon.ops, "h1", MANUAL)
    assert pv["head"] == git(human, "rev-parse", "HEAD") and len(pv["commits"]) == 2 and pv["dirty"] == 0
    assert pv["snapshot"]["supported"] is False
    (human / "notes.txt").write_text("mid-edit\n")
    assert (await checkpoints.preview(daemon.ops, "h1", MANUAL))["dirty"] == 1
    cp = await make_checkpoint(daemon)
    assert cp["dirty"] == 1
    assert (await checkpoints.source_head(daemon.ops, cp))["advanced"] is False
    git(human, "commit", "-qam", "more work")
    moved = await checkpoints.source_head(daemon.ops, cp)
    assert moved["advanced"] is True and moved["head"] == git(human, "rev-parse", "HEAD")
    assert checkpoints.get(daemon.journal.db, cp["checkpoint_id"])["commit_sha"] == cp["commit_sha"]
    daemon.ops.context["git_runner"] = None  # no SSH: uncommitted changes are not observed, not "clean"
    cp2 = await make_checkpoint(daemon, note="again")
    assert cp2["dirty"] is None
    text = checkpoints.first_prompt(cp2, worktree="/w", branch="b", instructions="x")
    assert "were not observed" in text
    await daemon.fleet.close()
    await daemon.inventory.close()


async def test_failed_git_status_stays_unknown_in_preview_and_saved_checkpoint(daemon, mock, human):
    index = human / ".git/index"
    broken = b"unreadable index fixture\n"
    index.write_bytes(broken)
    try:
        assert (await checkpoints.preview(daemon.ops, "h1", MANUAL))["dirty"] is None
        cp = await make_checkpoint(daemon)
        assert cp["dirty"] is None
        text = checkpoints.first_prompt(cp, worktree="/w", branch="b", instructions="x")
        assert "were not observed" in text
        assert index.read_bytes() == broken and bat_writes(mock) == []
    finally:
        await daemon.fleet.close()
        await daemon.inventory.close()


async def test_a_replay_after_the_agent_committed_does_not_recheck_the_moved_head(daemon, mock, human, monkeypatch):
    from bat_agent_connector import service
    from bat_agent_connector.errors import InvokeTimeout

    cp = await make_checkpoint(daemon)
    real_send = service.session_send

    async def lost(*a, **kw):
        raise InvokeTimeout("claude:send-message timed out")  # the reply never came; nothing proves the send

    monkeypatch.setattr(service, "session_send", lost)
    op = await run(daemon, "checkpoint.continue", {"checkpoint_id": cp["checkpoint_id"]}, {"instructions": "go"})
    assert op["status"] == "uncertain"
    wt = op["external_refs"]["worktree_path"]
    git(wt, "-c", "user.email=a@example.invalid", "-c", "user.name=agent", "commit", "-q", "--allow-empty", "-m",
        "the agent already works")  # HEAD has moved past the checkpoint
    monkeypatch.setattr(service, "session_send", real_send)
    daemon.journal.db.execute("UPDATE operations SET next_run_at=0, uncertain_tries=99 WHERE operation_id=?",
                              (op["operation_id"],))
    await settle_operations(daemon.ops)
    done = daemon.ops.get(op["operation_id"])
    assert done["error_code"] != "START_MISMATCH", done
    assert [s["name"] for s in done["steps"]][:3] == ["worktree.prepare", "verify.start", "session.start"]
    await daemon.fleet.close()
    await daemon.inventory.close()


async def test_the_clone_never_keeps_a_local_or_credentialed_origin(daemon, mock, human):
    git(human, "remote", "set-url", "origin", "https://operator:ghp_secret@github.example/o/r.git")
    cp = await make_checkpoint(daemon)
    op = await run(daemon, "checkpoint.continue", {"checkpoint_id": cp["checkpoint_id"]}, {"instructions": "go"})
    clone = op["external_refs"]["clone_path"]
    assert git(clone, "config", "--get", "remote.origin.url") == "https://github.example/o/r.git"
    assert "ghp_secret" not in (subprocess.run(["git", "-C", clone, "config", "--list"], capture_output=True,
                                               text=True).stdout)
    other = human.parent / "local-origin"
    subprocess.run(["git", "clone", "-q", str(human), str(other)], check=True)
    git(other, "remote", "set-url", "origin", str(human))  # its origin is a person's folder
    mock.metas[MANUAL] = {"cwd": str(other), "isStreaming": False}
    cp2 = await make_checkpoint(daemon, note="other")
    op2 = await run(daemon, "checkpoint.continue", {"checkpoint_id": cp2["checkpoint_id"]}, {"instructions": "go"},
                    key="local")
    assert op2["status"] == "succeeded", op2
    remotes = git(op2["external_refs"]["clone_path"], "remote")
    assert "origin" not in remotes.split()  # pushes cannot land in a person's folder
    await daemon.fleet.close()
    await daemon.inventory.close()


async def test_a_managed_session_continues_in_its_own_clone(daemon, mock, human):
    cp = await make_checkpoint(daemon)
    first = await run(daemon, "checkpoint.continue", {"checkpoint_id": cp["checkpoint_id"]}, {"instructions": "go"})
    wt, sid, clone = (first["result"]["worktree_path"], first["result"]["session_id"],
                      first["external_refs"]["clone_path"])
    Path(wt, "flag.txt").write_text("on\n")
    git(wt, "add", "flag.txt")
    git(wt, "-c", "user.email=a@example.invalid", "-c", "user.name=agent", "commit", "-qm", "flag")
    agent_commit = git(wt, "rev-parse", "HEAD")
    await daemon.inventory.refresh_host("h1")
    op = await run(daemon, "checkpoint.create", {"host": "h1", "session_id": sid}, {"last_n": 0})
    assert op["status"] == "succeeded", op
    cp2 = checkpoints.get(daemon.journal.db, op["result"]["checkpoint_id"])
    assert cp2["source_provenance"] == "connector_managed" and cp2["commit_sha"] == agent_commit
    second = await run(daemon, "checkpoint.continue", {"checkpoint_id": cp2["checkpoint_id"]},
                       {"instructions": "review it", "agent": "codex"}, key="again")
    assert second["status"] == "succeeded", second
    assert second["external_refs"]["clone_path"] == clone  # no clone of a clone
    assert git(second["result"]["worktree_path"], "rev-parse", "HEAD") == agent_commit
    await daemon.fleet.close()
    await daemon.inventory.close()


def test_checkpoint_paths_are_fixed_names_inside_a_managed_root(tmp_path):
    from bat_agent_connector.config import HostConfig
    from bat_agent_connector.errors import ResourceReadOnly

    hc = HostConfig(name="h1", url="wss://x/", fingerprint="0" * 64, token_ref="env:X",
                    managed_roots=("/srv/managed",))
    resource_policy.check_checkpoint_worktree(hc, "/srv/managed/app-1",
                                              "/srv/managed/app-1/.bat-worktrees/batc-cp-0123456789ab",
                                              "batc/cp-0123456789ab")
    for clone, path, branch in [
            ("/home/operator/app", "/home/operator/app/.bat-worktrees/batc-cp-0123456789ab", "batc/cp-0123456789ab"),
            ("/srv/managed", "/srv/managed/.bat-worktrees/batc-cp-0123456789ab", "batc/cp-0123456789ab"),
            ("/srv/managed/a/b", "/srv/managed/a/b/.bat-worktrees/batc-cp-0123456789ab", "batc/cp-0123456789ab"),
            ("/srv/managed/app-1", "/srv/managed/app-1/x/batc-cp-0123456789ab", "batc/cp-0123456789ab"),
            ("/srv/managed/app-1", "/srv/managed/app-1/.bat-worktrees/batc-cp-0123456789ab", "batc/cp-ffffffffffff")]:
        with pytest.raises(ResourceReadOnly):
            resource_policy.check_checkpoint_worktree(hc, clone, path, branch)
    assert "checkpoint.continue" in resource_policy.BY_ACTION["checkpoint.managed_worktree"].entry_points
    assert checkpoints.target_clone(("/srv/managed",), "h1", "/srv/managed/app-1/.bat-worktrees/batc-cp-1")[1]
    assert not checkpoints.target_clone(("/srv/managed",), "h1", "/home/operator/app")[1]


async def test_bat_worktree_actions_never_touch_a_worktree_the_connector_made(daemon, mock, human):
    """BAT has no record of a checkpoint worktree: a rehydrate would register it under the workspace folder (a
    person's checkout in real use) and copy its env files in, and a remove would prune that repository."""
    cp = await make_checkpoint(daemon)
    op = await run(daemon, "checkpoint.continue", {"checkpoint_id": cp["checkpoint_id"]},
                   {"instructions": "Add a changelog entry", "agent": "claude"})
    sid, wt = op["result"]["session_id"], op["result"]["worktree_path"]
    assert registry.get("h1", sid)["worktree_made_by"] == "connector"
    mock.invokes.clear()
    with pytest.raises(ResourceReadOnly) as e:
        await orchestrate.worktree_remove(daemon.fleet, "h1", sid, confirm=True, delete_branch=True)
    assert e.value.code == "NOT_A_BAT_WORKTREE"
    with pytest.raises(ResourceReadOnly) as e:
        await orchestrate.worktree_merge(daemon.fleet, "h1", sid, confirm=True)
    assert e.value.code == "NOT_A_BAT_WORKTREE"
    out = await lifecycle.session_cleanup(daemon.fleet, "h1", dry_run=True, session_id=sid, min_idle_s=0)
    assert out["decisions"][0]["decision"] == "KEEP" and "over SSH" in out["decisions"][0]["reasons"][0]
    assert not [i for i in mock.invokes if i["channel"].startswith("worktree:") and i["channel"] != "worktree:status"]
    assert git(wt, "rev-parse", "--abbrev-ref", "HEAD") == op["result"]["branch"]  # still there
    policy = await resource_policy.session_policy(daemon.fleet, "h1", sid)
    assert policy["worktree_made_by"] == "connector"
    assert policy["actions"]["worktree.remove"]["code"] == "NOT_A_BAT_WORKTREE"
    assert policy["actions"]["session.send"]["allowed"] and policy["actions"]["session.stop"]["allowed"]
    # rows written before worktree_made_by existed are recognised by their connector branch
    row = {**registry.get("h1", sid)}
    row.pop("worktree_made_by")
    assert resource_policy.worktree_maker(row) == "connector"
    assert resource_policy.worktree_maker({"branch": "bat/worktree-0000abcd"}) == "bat"


@pytest.mark.parametrize('stage', ['before', 'after'])
async def test_checkpoint_preframe_cancellation_restarts_unsent_reserved_session_once(daemon, mock, monkeypatch, stage):
    """A10: pre-frame cancel leaves durable unsent proof; post-frame cancel reconciles without another start."""
    from dataclasses import replace

    from tests.test_confinement import ACCOUNT, AccountRunner
    from tests.test_confinement_recovery import PauseAtFrame

    cp = await make_checkpoint(daemon)
    daemon.fleet.config.hosts['h1'] = replace(daemon.fleet.config.host('h1'), confinement=ACCOUNT)
    pause = PauseAtFrame()
    daemon.fleet.confinement_runner = pause if stage == 'before' else AccountRunner()
    ready = pause.entered if stage == 'before' else asyncio.Event()
    client = daemon.fleet.client('h1')
    invoke = client.invoke
    propagated = []
    start_session = orchestrate.session_start

    async def trace_start(*args, **kwargs):
        try:
            return await start_session(*args, **kwargs)
        except asyncio.CancelledError as exc:
            propagated.append(exc)
            raise

    monkeypatch.setattr(orchestrate, 'session_start', trace_start)

    async def after_frame(channel, params=None, **kwargs):
        result = await invoke(channel, params, **kwargs)
        if channel == 'claude:start-session':
            ready.set()
            await asyncio.Event().wait()
        return result

    if stage == 'after':
        monkeypatch.setattr(client, 'invoke', after_frame)
    op, _ = daemon.ops.create(OPERATOR, action='checkpoint.continue', target={'checkpoint_id': cp['checkpoint_id']},
                              params={'instructions': 'go'}, idempotency_key='preframe-checkpoint-cancel')
    await daemon.ops.run_due()
    pending = daemon.ops._active[op['operation_id']]
    try:
        await asyncio.wait_for(ready.wait(), 60)
        row = registry.list_entries('h1')[-1]
        sid, worktree = row['session_id'], row['cwd']
        pending.cancel('fixture operation cancellation')
        with pytest.raises(asyncio.CancelledError):
            await pending
        assert len(propagated) == 1 and propagated[0].args == ('fixture operation cancellation',)
        if stage == 'before':
            assert propagated[0] is pause.cancelled
        row = registry.get('h1', sid)
        assert row['start_sent'] is (stage == 'after')
        assert row['status'] == ('failed' if stage == 'before' else 'uncertain')
        assert mock.channels().count('claude:start-session') == (stage == 'after')
        assert not bat_writes(mock, MANUAL) and 'worktree:remove' not in mock.channels()
        pause.release.set()
        monkeypatch.setattr(client, 'invoke', invoke)
        await daemon.ops.drain(timeout=60)  # Process restart: replay the persisted started step.
        result = daemon.ops.get(op['operation_id'])
        assert result['status'] == 'succeeded', result
        assert result['result']['session_id'] == sid
        assert registry.get('h1', sid)['cwd'] == worktree
        assert mock.channels().count('claude:start-session') == 1
        assert mock.channels().count('claude:send-message') == 1
        starts = [i for i in mock.invokes if i['channel'] == 'claude:start-session']
        assert starts[0]['params']['sessionId'] == sid
    finally:
        if not pending.done():
            pending.cancel()
            with pytest.raises(asyncio.CancelledError):
                await pending
        await daemon.fleet.close()
        await daemon.inventory.close()
