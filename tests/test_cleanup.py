"""Managed cleanup, plan §23/E01/E02: real temporary Git repositories and MockBat only."""
from __future__ import annotations

import asyncio
import copy
import hashlib
import json
from pathlib import Path

import pytest

from bat_agent_connector import api_auth, cleanup, lifecycle, orchestrate, registry, service
from bat_agent_connector.operations import OperationError
from tests.operation_helpers import settle_operations
from tests.test_checkpoints import (  # noqa: F401 - pytest fixtures shared with real Git checkpoint tests
    LocalRunner,
    git,
    make_checkpoint,
    run,
    snapshot,
)
from tests.test_checkpoints import (
    daemon as checkpoint_daemon,
)
from tests.test_checkpoints import (
    human as checkpoint_human,
)

daemon = checkpoint_daemon
human = checkpoint_human


@pytest.fixture(autouse=True)
def known_live_terminals(mock):
    # These unrelated fixture terminals have known live cwd. Unknown cwd is tested explicitly below.
    for terminal in mock.ws_doc["terminals"]:
        if mock.metas.get(terminal["id"]) is None:
            mock.metas[terminal["id"]] = {"cwd": terminal["cwd"], "isStreaming": False}

CLEANER = api_auth.Principal("operator-dashboard", frozenset({"observe", "cleanup"}))
DISCARDER = api_auth.Principal("operator-dashboard", frozenset({"observe", "cleanup", "cleanup_discard"}))


async def setup_work(d, mock, **checkpoint_params):
    cp = await make_checkpoint(d, **checkpoint_params)
    op = await run(d, "checkpoint.continue", {"checkpoint_id": cp["checkpoint_id"]}, {"instructions": "finish"})
    assert op["status"] == "succeeded", op
    sid = op["result"]["session_id"]
    mock.metas[sid]["isStreaming"] = False
    return cp, op


async def apply(d, doc, principal=CLEANER):
    op, _ = d.ops.create(principal, **copy.deepcopy(cleanup.apply_request(doc, "apply-" + doc["preview_id"])))
    await settle_operations(d.ops, timeout=60)
    return d.ops.get(op["operation_id"])


def attachment_evidence(monkeypatch, wt, names, bookkeeping=()):
    # Simulate the artifacts package's future read of verified artifact_materializations.
    evidence = {"replica_manifest": [{"path": name, "bytes": (wt / name).stat().st_size,
                                     "digest": hashlib.sha256((wt / name).read_bytes()).hexdigest()} for name in names],
                "bookkeeping_names": list(bookkeeping)}
    monkeypatch.setattr(cleanup, "_replica_evidence", lambda ops, item: copy.deepcopy(evidence) if item["path"] == str(wt)
                        else {"replica_manifest": [], "bookkeeping_names": []})
    return evidence


async def test_e01_preview_is_pure_and_signed_plan_cannot_be_changed(daemon, mock, human):
    cp, op = await setup_work(daemon, mock)
    clone = Path(op["external_refs"]["clone_path"])
    before, manual = git(clone, "for-each-ref"), snapshot(human)
    db = list(daemon.journal.db.iterdump())
    reg = registry.registry_path().read_bytes()
    doc = await cleanup.preview(daemon.ops, CLEANER, {"kind": "checkpoint", "checkpoint_id": cp["checkpoint_id"]})
    assert doc["ready"], [(i["kind"], i["reasons"], i.get("observation")) for i in doc["items"]]
    assert before == git(clone, "for-each-ref") and manual == snapshot(human)
    assert db == list(daemon.journal.db.iterdump()) and reg == registry.registry_path().read_bytes()
    req = cleanup.apply_request(doc, "wrong")
    req["preconditions"]["preview_fingerprint"] = "wrong"
    with pytest.raises(OperationError, match="PREVIEW_MISMATCH"):
        daemon.ops.create(CLEANER, **req)
    req = cleanup.apply_request(doc, "actor")
    with pytest.raises(OperationError, match="PREVIEW_MISMATCH"):
        daemon.ops.create(api_auth.Principal("other-cleaner", DISCARDER.scopes), **req)
    req = cleanup.apply_request(doc, "tamper")
    req["params"]["preview_token"] += "broken"
    with pytest.raises(OperationError, match="PREVIEW_TOKEN_INVALID"):
        daemon.ops.create(CLEANER, **req)


async def test_e01_accepted_authorization_is_server_recorded(daemon, mock):
    from tests.test_api_v1 import http

    cp, op = await setup_work(daemon, mock)
    wt = Path(op["result"]["worktree_path"])
    (wt / "result.txt").write_text("undelivered result")
    git(wt, "add", ".")
    git(wt, "-c", "user.name=test", "-c", "user.email=test@example.invalid", "commit", "-qm", "result")
    (wt / "uncommitted.txt").write_text("reviewed discard")
    target = {"kind": "checkpoint", "checkpoint_id": cp["checkpoint_id"]}
    doc = await cleanup.preview(daemon.ops, DISCARDER, target)
    item = next(i for i in doc["items"] if i["kind"] == "worktree")
    choices = {"discard_uncommitted": [item["resource_id"]], "release_undelivered": [item["resource_id"]]}
    doc = await cleanup.preview(daemon.ops, DISCARDER, target, choices)
    public_request = cleanup.apply_request(doc, "acceptance-authority")
    token = api_auth.issue(daemon.journal.db, DISCARDER.actor, sorted(DISCARDER.scopes))
    server = await asyncio.start_server(daemon._handle, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    try:
        forged = copy.deepcopy(public_request)
        forged["params"]["_accepted_authorization"] = {"actor": "spoofed", "scopes": [], "choices": {}}
        status, body = await http(port, "POST", "/api/v1/operations", tok=token, body=forged)
        assert status == 422 and body["error"]["code"] == "INVALID_PARAMS"
        assert not daemon.journal.db.execute("SELECT 1 FROM operations WHERE action='cleanup.apply'").fetchone()
        status, body = await http(port, "POST", "/api/v1/operations", tok=token, body=public_request)
        assert status == 202 and body["created"], body
        accepted = body["operation"]
        saved = json.loads(daemon.journal.db.execute("SELECT params FROM operations WHERE operation_id=?",
                           (accepted["operation_id"],)).fetchone()[0])
        assert saved["_accepted_authorization"] == {"actor": DISCARDER.actor, "scopes": sorted(DISCARDER.scopes),
                                                  "choices": choices}
        assert "_accepted_authorization" not in public_request["params"]
        status, body = await http(port, "POST", "/api/v1/operations", tok=token, body=public_request)
        assert status == 200 and not body["created"], body
        assert body["operation"]["operation_id"] == accepted["operation_id"]
        assert daemon.journal.db.execute("SELECT count(*) FROM operations WHERE action='cleanup.apply'").fetchone()[0] == 1
        await settle_operations(daemon.ops, timeout=60)
        finished = daemon.ops.get(accepted["operation_id"])
        assert finished["status"] == "succeeded", (finished.get("error"), [(r["plan"]["kind"], r["status"], r["error"])
                                                    for r in cleanup.receipts(daemon.ops, accepted["operation_id"])])
        accepted_run = daemon.journal.db.execute(
            "SELECT accepted_actor,accepted_scopes,accepted_choices FROM cleanup_runs WHERE operation_id=?",
            (accepted["operation_id"],)).fetchone()
        assert accepted_run["accepted_actor"] == saved["_accepted_authorization"]["actor"]
        assert json.loads(accepted_run["accepted_scopes"]) == saved["_accepted_authorization"]["scopes"]
        assert json.loads(accepted_run["accepted_choices"]) == choices
    finally:
        server.close()
        await server.wait_closed()


async def test_e01_preserve_precedes_nonforced_remove_and_cas_checks_delivered_refs(daemon, mock, human, monkeypatch):
    cp, op = await setup_work(daemon, mock)
    doc = await cleanup.preview(daemon.ops, CLEANER, {"kind": "checkpoint", "checkpoint_id": cp["checkpoint_id"]})
    wt = next(i for i in doc["items"] if i["kind"] == "worktree")
    branch = next(i for i in doc["items"] if i["kind"] == "local_branch")
    assert branch["decision"] == "reclaim" and branch["dependencies"] == [wt["resource_id"]]
    original = cleanup.snapshot
    branch_snapshots = []
    async def observe(*args, **kwargs):
        current = await original(*args, **kwargs)
        if kwargs.get("only") == branch["resource_id"]:
            branch_snapshots.append(next((i for i in current["items"] if i["resource_id"] == branch["resource_id"]), None))
        return current
    monkeypatch.setattr(cleanup, "snapshot", observe)
    done = await apply(daemon, doc)
    assert done["status"] == "succeeded", done
    assert len(branch_snapshots) == 1 and branch_snapshots[0]["observation"] == branch["observation"]
    assert branch_snapshots[0]["dependencies"] == branch["dependencies"]  # only settled worktree dependencies normalize.
    rows = {r["resource_id"]: r for r in cleanup.receipts(daemon.ops, done["operation_id"])}
    assert rows[wt["resource_id"]]["status"] == rows[branch["resource_id"]]["status"] == "succeeded"
    assert not Path(wt["path"]).exists()
    assert git(wt["repository"], "rev-parse", "refs/batc/retained/" + wt["resource_id"] + "/" + wt["observation"]["head"])
    assert git(wt["repository"], "show-ref", "--heads")
    assert wt["branch"] not in git(wt["repository"], "for-each-ref", "refs/heads/")
    assert Path(wt["repository"]).exists()
    ret = await cleanup.retained(daemon.ops)
    assert ret["retained"] and not ret["unavailable"]
    assert cleanup.lookup(daemon.journal.db, cp["checkpoint_id"])


async def test_e01_branch_moved_after_worktree_removal_is_stale(daemon, mock):
    import base64
    import shlex

    cp, op = await setup_work(daemon, mock)
    doc = await cleanup.preview(daemon.ops, CLEANER, {"kind": "checkpoint", "checkpoint_id": cp["checkpoint_id"]})
    wt = next(i for i in doc["items"] if i["kind"] == "worktree")
    branch = next(i for i in doc["items"] if i["kind"] == "local_branch")
    original = daemon.ops.context["git_runner"]
    class MovedBranch(LocalRunner):
        moved = None
        refs_after_move = None
        branch_phases = []
        async def run(self, host, script, timeout_s=None):
            req = json.loads(base64.b64decode(shlex.split(script)[-1]))
            if self.moved and req.get("kind") == "local_branch" and req.get("phase"):
                self.branch_phases.append(req["phase"])
            out = await original.run(host, script, timeout_s)
            if req.get("phase") == "remove.worktree" and json.loads(out).get("result", {}).get("removed"):
                assert not Path(wt["path"]).exists()
                sha = wt["observation"]["head"]
                self.moved = git(wt["repository"], "-c", "user.name=test", "-c", "user.email=test@example.invalid",
                                 "commit-tree", sha + "^{tree}", "-p", sha, "-m", "branch moved")
                git(wt["repository"], "update-ref", "refs/heads/" + wt["branch"], self.moved, sha)
                self.refs_after_move = git(wt["repository"], "for-each-ref")
            return out
    runner = MovedBranch()
    daemon.ops.context["git_runner"] = runner
    done = await apply(daemon, doc)
    rows = {r["resource_id"]: r for r in cleanup.receipts(daemon.ops, done["operation_id"])}
    assert rows[wt["resource_id"]]["status"] == "succeeded"
    assert rows[branch["resource_id"]]["status"] == "uncertain"
    assert rows[branch["resource_id"]]["error"]["code"] == "CLEANUP_PARTIAL_STATE", rows[branch["resource_id"]]
    assert rows[branch["resource_id"]]["error"]["refused_code"] == "PREVIEW_STALE"
    assert any(p["phase"] == "remove.worktree" for p in rows[branch["resource_id"]]["completed_phases"])
    assert "remove.branch" not in runner.branch_phases
    assert git(wt["repository"], "for-each-ref") == runner.refs_after_move
    assert git(wt["repository"], "rev-parse", wt["branch"]) == runner.moved


async def test_e01_release_keeps_commits_with_cleanup_scope(daemon, mock):
    cp, op = await setup_work(daemon, mock)
    wt = Path(op["result"]["worktree_path"])
    git(wt, "config", "user.name", "test")
    git(wt, "config", "user.email", "test@example.invalid")
    (wt / "result.txt").write_text("undelivered")
    git(wt, "add", ".")
    git(wt, "commit", "-qm", "result")
    target = {"kind": "checkpoint", "checkpoint_id": cp["checkpoint_id"]}
    doc = await cleanup.preview(daemon.ops, CLEANER, target)
    item = next(i for i in doc["items"] if i["kind"] == "worktree")
    assert "RESULTS_NOT_DELIVERED" in {r["code"] for r in item["reasons"]}
    doc = await cleanup.preview(daemon.ops, CLEANER, target, {"release_undelivered": [item["resource_id"]]})
    done = await apply(daemon, doc)
    assert done["status"] == "succeeded", done
    assert git(item["repository"], "rev-parse", item["branch"]) == item["observation"]["head"]
    assert not wt.exists()


async def test_e01_discard_requires_cleanup_discard(daemon, mock):
    cp, op = await setup_work(daemon, mock)
    wt = Path(op["result"]["worktree_path"])
    (wt / "uncommitted.txt").write_text("content")
    target = {"kind": "checkpoint", "checkpoint_id": cp["checkpoint_id"]}
    doc = await cleanup.preview(daemon.ops, CLEANER, target)
    item = next(i for i in doc["items"] if i["kind"] == "worktree")
    with pytest.raises(OperationError, match="DISCARD_SCOPE_REQUIRED"):
        await cleanup.preview(daemon.ops, CLEANER, target, {"discard_uncommitted": [item["resource_id"]]})
    doc = await cleanup.preview(daemon.ops, DISCARDER, target, {"discard_uncommitted": [item["resource_id"]]})
    done = await apply(daemon, doc, DISCARDER)
    assert done["status"] == "succeeded", done
    assert not wt.exists()


async def test_e01_stale_any_item_stops_before_mutation_and_reports_changes(daemon, mock):
    cp, op = await setup_work(daemon, mock)
    doc = await cleanup.preview(daemon.ops, CLEANER, {"kind": "checkpoint", "checkpoint_id": cp["checkpoint_id"]})
    (Path(op["result"]["worktree_path"]) / "changed.txt").write_text("changed")
    count = len(mock.invokes)
    done = await apply(daemon, doc)
    assert done["error_code"] == "PREVIEW_STALE", done
    assert not [i for i in mock.invokes[count:] if i["channel"] == "claude:stop-session"]
    assert not daemon.journal.db.execute("SELECT * FROM cleanup_retained").fetchall()


async def test_e01_task_owned_resources_are_retained(daemon, mock):
    cp, op = await setup_work(daemon, mock)
    registry.update("h1", op["result"]["session_id"], task_id="task-owned")
    doc = await cleanup.preview(daemon.ops, CLEANER, {"kind": "checkpoint", "checkpoint_id": cp["checkpoint_id"]})
    assert all("TASK_OWNED" in {r["code"] for r in i["reasons"]} for i in doc["items"] if i["kind"] in
               {"session", "worktree", "local_branch"})
    assert not doc["ready"]


async def test_e01_previews_serialize_per_host_and_share_read_deadline(daemon, mock, monkeypatch):
    cp, _ = await setup_work(daemon, mock)
    original = daemon.ops.context["git_runner"]
    active = maximum = 0
    class SlowRunner(LocalRunner):
        async def run(self, host, script, timeout_s=None):
            nonlocal active, maximum
            active += 1
            maximum = max(maximum, active)
            try:
                await asyncio.sleep(.08)
                return await original.run(host, script, timeout_s)
            finally:
                active -= 1
    daemon.ops.context["git_runner"] = SlowRunner()
    monkeypatch.setattr(cleanup, "READ_DEADLINE_S", .05)
    target = {"kind": "checkpoint", "checkpoint_id": cp["checkpoint_id"]}
    docs = await asyncio.gather(*(cleanup.preview(daemon.ops, CLEANER, target) for _ in range(3)))
    assert maximum == 1
    assert all(any(r["code"] == "OBSERVATION_UNAVAILABLE" for i in d["items"] for r in i["reasons"]) for d in docs)


async def test_e01_guard_refuses_legacy_writes_on_reserved_and_cleaned_resources(daemon, mock):
    from bat_agent_connector import mcp_server
    server, stdio_fleet = mcp_server.build_server(daemon.fleet.config)  # No daemon Journal in the legacy tool context.
    async def stdio_refusal(code, sid):
        before = len(mock.invokes)
        try:
            result = await server.call_tool("worktree_remove", {"host": "h1", "session_id": sid, "confirm": True})
            message = json.dumps(result.model_dump() if hasattr(result, "model_dump") else result, default=str)
        except Exception as e:  # noqa: BLE001 - MCP versions may return or raise the same tool refusal
            message = str(e)
        assert code in message
        assert not any(i["channel"] == "worktree:remove" for i in mock.invokes[before:])
    cp, op = await setup_work(daemon, mock)
    sid = op["result"]["session_id"]
    doc = await cleanup.preview(daemon.ops, CLEANER, {"kind": "checkpoint", "checkpoint_id": cp["checkpoint_id"]})
    item = next(i for i in doc["items"] if i["kind"] == "session")
    cleanup._mark(item, "op-test", "reserved")
    from bat_agent_connector.errors import ResourceReadOnly
    with pytest.raises(ResourceReadOnly, match="CLEANUP_IN_PROGRESS"):
        await orchestrate.worktree_remove(daemon.fleet, "h1", sid, confirm=True)
    await stdio_refusal("CLEANUP_IN_PROGRESS", sid)
    cleanup._release(item, "op-test")
    done = await apply(daemon, doc)
    assert done["status"] == "succeeded", done
    with pytest.raises(ResourceReadOnly, match="RESOURCE_CLEANED"):
        await orchestrate.worktree_remove(daemon.fleet, "h1", sid, confirm=True)
    await stdio_refusal("RESOURCE_CLEANED", sid)
    await stdio_fleet.close()


async def test_e01_legacy_apply_is_disabled_and_auto_cleanup_still_loads(daemon, mock):
    daemon.fleet.config.host("h1").auto_cleanup = True
    with pytest.raises(OperationError, match="LEGACY_CLEANUP_DISABLED"):
        await lifecycle.session_cleanup(daemon.fleet, "h1", confirm=True, dry_run=False)


def add_receipt(d, item, *, mode="merge", commits=None, picked=None, status="delivered", pinned=None,
                source_kind="checkpoint_run", source_id=None, source_host="h1"):
    """The same integration_receipts facts used by integration.receipts(); no ancestry inference."""
    import time
    record = {"operation_id": "op_" + "a" * 32, "seq": 1, "preview_id": "ipv_" + "b" * 32,
        "repository": "o/r", "pull_number": 7, "head_ref": "feature/result", "source_kind": source_kind,
        "source_id": source_id or next(x for x in item["original_ids"] if x.startswith("op_")), "source_host": source_host,
        "location_class": "managed_clone", "pinned_sha": pinned or item["observation"]["head"], "mode": mode,
        "source_key": "coverage", "status": status, "method": "squash", "delivered_sha": "c" * 40,
        "actor": "delivery", "created_at": time.time(), "updated_at": time.time(),
        "commits": json.dumps(commits) if commits else None, "picked": json.dumps(picked) if picked else None}
    d.journal.db.execute("DELETE FROM integration_receipts WHERE operation_id=?", (record["operation_id"],))
    columns = ",".join(record)
    d.journal.db.execute(f"INSERT INTO integration_receipts({columns}) VALUES({','.join('?' for _ in record)})",  # noqa: S608 - fixed columns
                         tuple(record.values()))


async def test_e02_squash_and_pick_use_exact_delivery_receipt_coverage(daemon, mock):
    cp, op = await setup_work(daemon, mock)
    wt = Path(op["result"]["worktree_path"])
    for n in (1, 2):
        git(wt, "-c", "user.name=test", "-c", "user.email=test@example.invalid", "commit", "--allow-empty", "-qm", f"result {n}")
    target = {"kind": "checkpoint", "checkpoint_id": cp["checkpoint_id"]}
    async def item():
        doc = await cleanup.preview(daemon.ops, CLEANER, target)
        return next(i for i in doc["items"] if i["kind"] == "worktree")
    i = await item()
    result = i["observation"]["results"]
    assert len(result) == 2
    add_receipt(daemon, i, mode="pick", commits=result[:1], picked=[{"source": result[0], "new": "d" * 40}])
    assert "RESULTS_NOT_DELIVERED" in {r["code"] for r in (await item())["reasons"]}
    add_receipt(daemon, i, mode="pick", commits=result, picked=[{"source": s, "new": "d" * 40} for s in result])
    assert (await item())["delivery"]["delivered"]
    # An exact receipt of a squash covers its source tip even though the destination is unrelated by ancestry.
    add_receipt(daemon, i)
    assert (await item())["delivery"]["delivered"]
    daemon.journal.db.execute("UPDATE integration_receipts SET source_kind='branch'")
    assert not (await item())["delivery"]["delivered"]
    daemon.journal.db.execute("UPDATE integration_receipts SET source_kind='checkpoint_run'")
    git(wt, "-c", "user.name=test", "-c", "user.email=test@example.invalid", "commit", "--allow-empty", "-qm", "new result")
    assert not (await item())["delivery"]["delivered"]
    add_receipt(daemon, await item(), status="unknown")
    assert "DELIVERY_UNCERTAIN" in {r["code"] for r in (await item())["reasons"]}


async def test_e01_pending_start_stop_and_waiting_sessions_are_retained(daemon, mock):
    cp, op = await setup_work(daemon, mock)
    sid = op["result"]["session_id"]
    mock.states[sid] = {"pendingAskUser": {"question": "choose"}, "messages": []}
    doc = await cleanup.preview(daemon.ops, CLEANER, {"kind": "checkpoint", "checkpoint_id": cp["checkpoint_id"]})
    assert any(r["code"] == "SESSION_WAITING" for i in doc["items"] for r in i["reasons"])
    assert not doc["ready"]
    mock.states[sid] = {"messages": []}
    registry.update("h1", sid, status="uncertain")
    doc = await cleanup.preview(daemon.ops, CLEANER, doc["target"])
    assert any(r["code"] == "COMMAND_UNRESOLVED" for i in doc["items"] for r in i["reasons"])
    assert not doc["ready"]


@pytest.mark.parametrize("field", service.SESSION_WAITING_FIELDS)
async def test_e01_every_waiting_field_racing_with_stop_keeps_session_and_worktree(daemon, mock, monkeypatch, field):
    cp, op = await setup_work(daemon, mock)
    sid, wt = op["result"]["session_id"], Path(op["result"]["worktree_path"])
    doc = await cleanup.preview(daemon.ops, CLEANER, {"kind": "checkpoint", "checkpoint_id": cp["checkpoint_id"]})
    assert doc["ready"]
    original = lifecycle._meta
    async def become_waiting(client, session_id):
        if session_id == sid:
            mock.states[sid][field] = True
        return await original(client, session_id)
    monkeypatch.setattr(lifecycle, "_meta", become_waiting)
    before = len(mock.invokes)
    done = await apply(daemon, doc)
    assert done["status"] == "needs_attention", done
    session = next(r for r in cleanup.receipts(daemon.ops, done["operation_id"]) if r["plan"].get("session_id") == sid)
    assert session["error"]["code"] == "SESSION_WAITING"
    assert not any(i["channel"] == "claude:stop-session" for i in mock.invokes[before:])
    assert wt.exists() and git(wt, "rev-parse", "HEAD") == cp["commit_sha"]


def test_e01_preview_recheck_and_stop_share_waiting_fields():
    import inspect
    assert cleanup.service.SESSION_WAITING_FIELDS is lifecycle.service.SESSION_WAITING_FIELDS
    for handler in (cleanup._plan, cleanup._phase_consumers, lifecycle._stop):
        assert "service.SESSION_WAITING_FIELDS" in inspect.getsource(handler)


def registered_terminal_elsewhere(mock, path):
    sid, recorded = "registered-elsewhere", str(Path(path).parent / "elsewhere")
    registry.reserve("h1", {"session_id": sid, "cwd": recorded, "worktree_path": recorded,
                            "agent_preset": "claude"}, 5)
    registry.update("h1", sid, status="active")
    mock.ws_doc["terminals"].append({"id": sid, "cwd": recorded, "agentPreset": "claude"})
    mock.metas[sid] = {"cwd": recorded, "isStreaming": False}
    mock.states[sid] = {"isStreaming": False, "messages": []}
    return sid


def change_live_cwd(mock, sid, path, mode):
    if mode == "inside":
        mock.metas[sid]["cwd"] = str(Path(path) / "nested")
    elif mode == "missing_cwd":
        mock.metas[sid] = {"isStreaming": False}
    elif mode == "missing_meta":
        mock.metas[sid] = None
    else:
        def failed(params):
            if params["sessionId"] == sid:
                raise RuntimeError("live metadata read failed")
            return copy.deepcopy(mock.metas.get(params["sessionId"]))
        mock.handlers["claude:get-session-meta"] = failed


@pytest.mark.parametrize("mode", ["inside", "missing_cwd", "missing_meta", "failed_read"])
async def test_e01_registered_terminal_live_cwd_or_unknown_blocks_preview_and_apply(daemon, mock, mode):
    cp, op = await setup_work(daemon, mock)
    path = op["result"]["worktree_path"]
    sid = registered_terminal_elsewhere(mock, path)
    target = {"kind": "checkpoint", "checkpoint_id": cp["checkpoint_id"]}
    reviewed = await cleanup.preview(daemon.ops, CLEANER, target)
    assert reviewed["ready"]
    change_live_cwd(mock, sid, path, mode)
    before = len(mock.invokes)
    blocked = await cleanup.preview(daemon.ops, CLEANER, target)
    code = "ACTIVE_EXECUTION" if mode == "inside" else "OBSERVATION_UNAVAILABLE"
    assert not blocked["ready"], blocked
    for item in blocked["items"]:
        if item["kind"] == "worktree" or item.get("session_id") == op["result"]["session_id"]:
            assert code in {r["code"] for r in item["reasons"]}, item
    done = await apply(daemon, reviewed)
    assert done["status"] == "failed" and done["error_code"] == "PREVIEW_STALE", done
    assert not any(i["channel"] == "claude:stop-session" for i in mock.invokes[before:])
    assert Path(path).exists() and git(path, "rev-parse", "HEAD") == cp["commit_sha"]


@pytest.mark.parametrize("phase", ["lock.session", "preserve", "remove.worktree"])
@pytest.mark.parametrize("mode", ["inside", "failed_read"])
async def test_e01_live_cwd_is_rechecked_under_host_flock_before_stop_and_removal(daemon, mock, monkeypatch, phase, mode):
    import fcntl
    import os
    cp, op = await setup_work(daemon, mock)
    path = op["result"]["worktree_path"]
    sid = registered_terminal_elsewhere(mock, path)
    doc = await cleanup.preview(daemon.ops, CLEANER, {"kind": "checkpoint", "checkpoint_id": cp["checkpoint_id"]})
    assert doc["ready"]
    original, gates = cleanup._host_call, []
    async def race(ops, host, req, timeout=cleanup.READ_DEADLINE_S, *, locked_check=None, locked_action=None):
        if locked_check and req.get("phase") == phase:
            check = locked_check
            async def under_lock():
                fd = os.open(req["repository"], os.O_RDONLY | os.O_DIRECTORY)
                try:
                    with pytest.raises(BlockingIOError):
                        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                finally:
                    os.close(fd)
                gates.append(phase)
                change_live_cwd(mock, sid, path, mode)
                await check()
            locked_check = under_lock
        return await original(ops, host, req, timeout, locked_check=locked_check, locked_action=locked_action)
    monkeypatch.setattr(cleanup, "_host_call", race)
    before = len(mock.invokes)
    done = await apply(daemon, doc)
    assert gates == [phase]
    assert done["status"] == "needs_attention", done
    rows = cleanup.receipts(daemon.ops, done["operation_id"])
    assert any(r["error"] and r["error"].get("refused_code", r["error"]["code"]) == ("PREVIEW_STALE" if mode == "inside" else "OBSERVATION_UNAVAILABLE")
               for r in rows), rows
    stops = [i for i in mock.invokes[before:] if i["channel"] == "claude:stop-session"]
    assert len(stops) == (0 if phase == "lock.session" else 1)
    assert Path(path).exists() and git(path, "rev-parse", "HEAD") == cp["commit_sha"]


async def test_e01_live_cwd_uses_path_components_not_string_prefix(daemon, mock):
    cp, op = await setup_work(daemon, mock)
    path = op["result"]["worktree_path"]
    sid = registered_terminal_elsewhere(mock, path)
    mock.metas[sid]["cwd"] = path + "-other/nested"
    doc = await cleanup.preview(daemon.ops, CLEANER, {"kind": "checkpoint", "checkpoint_id": cp["checkpoint_id"]})
    assert doc["ready"]
    done = await apply(daemon, doc)
    assert done["status"] == "succeeded", done
    assert not Path(path).exists()
    assert mock.metas[sid] is not None


async def test_e01_refused_locked_check_closes_host_input_without_waiting_for_deadline(daemon):
    import time
    root = daemon.ops.context["fleet"].config.host("h1").managed_roots[0]
    path = str(Path(root) / "leased")
    Path(path).mkdir(parents=True)
    refused_at = []
    async def refuse():
        refused_at.append(time.monotonic())
        raise OperationError("OBSERVATION_UNAVAILABLE", "live read failed", 409)
    with pytest.raises(OperationError, match="OBSERVATION_UNAVAILABLE"):
        await cleanup._host_call(daemon.ops, "h1", {"phase": "lock.session", "repository": path, "roots": [root]},
                                 timeout=10, locked_check=refuse)
    assert len(refused_at) == 1
    assert time.monotonic() - refused_at[0] < 2  # EOF ends the host helper immediately, including on Python 3.10.


async def test_e02_shared_worktree_is_one_item_and_checks_out_of_scope_consumers(daemon, mock):
    from bat_agent_connector.resource_ids import worktree_id
    cp, op = await setup_work(daemon, mock)
    path = op["result"]["worktree_path"]
    registry.reserve("h1", {"session_id": "reviewer", "cwd": path, "worktree_path": path,
        "branch": op["result"]["branch"], "shares_worktree_with": op["result"]["session_id"], "agent_preset": "claude"}, 5)
    registry.update("h1", "reviewer", status="active")
    mock.metas["reviewer"] = {"cwd": path, "isStreaming": True}
    doc = await cleanup.preview(daemon.ops, CLEANER, {"kind": "checkpoint", "checkpoint_id": cp["checkpoint_id"]})
    wts = [i for i in doc["items"] if i["kind"] == "worktree"]
    assert len(wts) == 1
    assert wts[0]["resource_id"] == worktree_id("h1", "checkpoint.continue", op["operation_id"], "worktree")
    assert "h1/reviewer" in wts[0]["original_ids"]
    assert "ACTIVE_WRITER" in {r["code"] for r in wts[0]["reasons"]}
    assert not doc["ready"]
    before = len(mock.invokes)
    with pytest.raises(OperationError, match="PREVIEW_BLOCKED"):
        await apply(daemon, doc)
    assert not any(i["channel"] in {"worktree:remove", "worktree:rehydrate"}
                   for i in mock.invokes[before:])
    assert Path(path).exists()


@pytest.mark.parametrize("phase", ["preserve", "discard", "discard.replica", "remove.worktree", "remove.branch"])
async def test_e01_lost_replies_reconcile_each_cleanup_phase(daemon, mock, monkeypatch, phase):
    import base64
    import shlex

    from bat_agent_connector.operations import AmbiguousOutcome, OperationService
    cp, op = await setup_work(daemon, mock)
    target = {"kind": "checkpoint", "checkpoint_id": cp["checkpoint_id"]}
    loss_phase = "discard" if phase == "discard.replica" else phase
    principal = DISCARDER if loss_phase == "discard" else CLEANER
    if phase == "discard.replica":
        wt = Path(op["result"]["worktree_path"])
        (Path(op["external_refs"]["clone_path"]) / ".git/info/exclude").write_text(".batc-inputs/\n")
        (wt / ".batc-inputs").mkdir()
        (wt / ".batc-inputs/attachment.txt").write_text("original")
        attachment_evidence(monkeypatch, wt, [".batc-inputs/attachment.txt"])
        (wt / ".batc-inputs/attachment.txt").write_text("edited attachment")
    elif phase == "discard":
        (Path(op["result"]["worktree_path"]) / "discard.txt").write_text("discard")
    doc = await cleanup.preview(daemon.ops, principal, target)
    if loss_phase == "discard":
        wt = next(i for i in doc["items"] if i["kind"] == "worktree")
        doc = await cleanup.preview(daemon.ops, principal, target, {"discard_uncommitted": [wt["resource_id"]]})
    original = daemon.ops.context["git_runner"]
    class LostReply(LocalRunner):
        lost = False
        async def run(self, host, script, timeout_s=None):
            req = json.loads(base64.b64decode(shlex.split(script)[-1]))
            out = await original.run(host, script, timeout_s)
            if req.get("phase") == loss_phase and not self.lost:
                self.lost = True
                raise AmbiguousOutcome("lost SSH reply after effect")
            return out
    daemon.ops.context["git_runner"] = LostReply()
    done = await apply(daemon, doc, principal)
    assert done["status"] == "uncertain", done
    context = daemon.ops.context
    daemon.ops = OperationService(daemon.journal, actions=cleanup.ACTIONS)
    daemon.ops.context.update(context)
    daemon.journal.db.execute("UPDATE operations SET next_run_at=0 WHERE operation_id=?", (done["operation_id"],))
    await settle_operations(daemon.ops, timeout=60)
    finished = daemon.ops.get(done["operation_id"])
    assert finished["status"] == "succeeded", finished
    assert any(json.loads(r[0] or "{}").get("reconciled") for r in daemon.journal.db.execute(
        "SELECT response FROM operation_steps WHERE operation_id=?", (done["operation_id"],)))


async def test_e01_lost_stop_reply_remains_uncertain_without_resending(daemon, mock, monkeypatch):
    from bat_agent_connector.errors import ConnectionLost
    cp, op = await setup_work(daemon, mock)
    doc = await cleanup.preview(daemon.ops, CLEANER, {"kind": "checkpoint", "checkpoint_id": cp["checkpoint_id"]})
    original = lifecycle._stop
    calls = []
    async def lost_stop(*args, **kwargs):
        calls.append(kwargs)
        await original(*args, **kwargs)
        raise ConnectionLost("stop reply lost")
    monkeypatch.setattr(lifecycle, "_stop", lost_stop)
    done = await apply(daemon, doc)
    assert done["status"] == "uncertain" and calls == [{"cleanup": True}]
    daemon.journal.db.execute("UPDATE operations SET next_run_at=0 WHERE operation_id=?", (done["operation_id"],))
    await settle_operations(daemon.ops, timeout=30)
    assert len(calls) == 1
    assert Path(op["result"]["worktree_path"]).exists()
    assert daemon.ops.get(done["operation_id"])["status"] == "uncertain"


async def test_e01_original_ids_remain_searchable_with_location_reason_and_pr(daemon, mock):
    cp, op = await setup_work(daemon, mock)
    target = {"kind": "checkpoint", "checkpoint_id": cp["checkpoint_id"]}
    doc = await cleanup.preview(daemon.ops, CLEANER, target)
    wt = next(i for i in doc["items"] if i["kind"] == "worktree")
    add_receipt(daemon, wt)
    doc = await cleanup.preview(daemon.ops, CLEANER, target)
    done = await apply(daemon, doc)
    assert done["status"] == "succeeded"
    result = cleanup.tombstones(daemon.ops, original_id=op["operation_id"])
    tomb = next(t for t in result["tombstones"] if t["kind"] == "worktree")
    assert tomb["path"] == wt["path"] and tomb["reason"] == "reviewed_cleanup"
    assert tomb["pull_requests"][0]["pull_number"] == 7
    assert cleanup.tombstones(daemon.ops, query=wt["path"])["tombstones"]
    assert cleanup.tombstones(daemon.ops, original_id=op["result"]["session_id"])["tombstones"]
    refs = await cleanup.retained(daemon.ops)
    assert refs["retained"]
    pin = refs["retained"][0]
    git(pin["repository"], "update-ref", "-d", pin["ref"])
    refs = await cleanup.retained(daemon.ops)
    assert any(r["retained_id"] == pin["retained_id"] for r in refs["unavailable"])
    assert all(r["retained_id"] != pin["retained_id"] for r in refs["retained"])


@pytest.mark.parametrize("version", [1, 3])
def test_cleanup_migration_is_atomic_additive_and_preserves_history(tmp_path, version):
    from bat_agent_connector.task_journal import LATEST_DATA_STEP, Journal
    j = Journal(tmp_path / "journal.db")
    task = j.submit(project="p", host="h1", workspace="w", original_words="context", idempotency_key="original")
    for table in ("cleanup_aliases", "resource_tombstones", "cleanup_retained", "cleanup_receipts", "cleanup_runs"):
        j.db.execute("DROP TABLE " + table)  # noqa: S608 - fixed test names
    j.db.execute(f"PRAGMA user_version={version}")  # noqa: S608 - fixed test versions
    cleanup.migrate(j)
    assert j.db.execute("PRAGMA user_version").fetchone()[0] == version
    j.close()
    j = Journal(tmp_path / "journal.db")
    assert j.db.execute("PRAGMA user_version").fetchone()[0] == max(version, LATEST_DATA_STEP)
    assert j.db.execute("SELECT name FROM sqlite_master WHERE name='cleanup_runs'").fetchone()
    assert j.get(task["task_id"])["original_words"] == "context"
    assert not j.db.execute("SELECT name FROM sqlite_master WHERE name='cleanup_consumers'").fetchone()
    before = list(j.db.iterdump())
    j.close()
    j = Journal(tmp_path / "journal.db")
    assert list(j.db.iterdump()) == before
    assert j.db.execute("PRAGMA user_version").fetchone()[0] == max(version, LATEST_DATA_STEP)
    j.close()


async def test_e01_partial_cleanup_resumes_only_unfinished_unchanged_items(daemon, mock):
    import base64
    import shlex
    cp, op = await setup_work(daemon, mock)
    wt = Path(op["result"]["worktree_path"])
    (wt / "discard.txt").write_text("discard by the person")
    target = {"kind": "checkpoint", "checkpoint_id": cp["checkpoint_id"]}
    first = await cleanup.preview(daemon.ops, DISCARDER, target)
    wi = next(i for i in first["items"] if i["kind"] == "worktree")
    doc = await cleanup.preview(daemon.ops, DISCARDER, target, {"discard_uncommitted": [wi["resource_id"]]})
    original = daemon.ops.context["git_runner"]
    class Refusal(LocalRunner):
        failed = False
        async def run(self, host, script, timeout_s=None):
            req = json.loads(base64.b64decode(shlex.split(script)[-1]))
            if req.get("phase") == "remove.worktree" and not self.failed:
                self.failed = True
                return json.dumps({"error": "WORKTREE_REMOVE_REFUSED"})
            return await original.run(host, script, timeout_s)
    daemon.ops.context["git_runner"] = Refusal()
    done = await apply(daemon, doc, DISCARDER)
    assert done["status"] == "needs_attention", done
    assert done["result"]["summary"]["partial"]
    count = len([i for i in mock.invokes if i["channel"] == "claude:stop-session"])
    # The accepted person's discard choice remains authorized; the resumer has only cleanup.
    daemon.ops.context["cleanup_key"] = b"rotated-after-acceptance"
    daemon.ops.resume(CLEANER, done["operation_id"])
    await settle_operations(daemon.ops, timeout=60)
    finished = daemon.ops.get(done["operation_id"])
    assert finished["status"] == "succeeded", finished
    assert len([i for i in mock.invokes if i["channel"] == "claude:stop-session"]) == count
    assert any(s["name"].endswith("remove.worktree.a2") for s in finished["steps"])
    assert any(CLEANER.actor in r["resumed_by"] for r in cleanup.receipts(daemon.ops, done["operation_id"]))


async def test_e01_attachment_replicas_are_removed_without_discard_scope(daemon, mock, monkeypatch):
    cp, op = await setup_work(daemon, mock)
    wt = Path(op["result"]["worktree_path"])
    clone = Path(op["external_refs"]["clone_path"])
    (clone / ".git/info/exclude").write_text(".batc-inputs/\n")
    replicas = wt / ".batc-inputs"
    replicas.mkdir()
    (replicas / "attachment.txt").write_text("replica; original in artifact store")
    books = [".batc-inputs/.owner", ".batc-inputs/.attempts/art_example-r1/.attempt-1",
             ".batc-inputs/.attempts/art_example-r1/.closed-1"]
    for name in books:
        (wt / name).parent.mkdir(parents=True, exist_ok=True)
        (wt / name).write_text("helper bookkeeping")
    attachment_evidence(monkeypatch, wt, [".batc-inputs/attachment.txt"], books)
    doc = await cleanup.preview(daemon.ops, CLEANER, {"kind": "checkpoint", "checkpoint_id": cp["checkpoint_id"]})
    item = next(i for i in doc["items"] if i["kind"] == "worktree")
    assert item["decision"] == "reclaim", item
    assert not any(r["code"] == "UNCOMMITTED_CHANGES" for r in item["reasons"])
    assert [f["path"] for f in item["observation"]["exempted_replicas"]] == [".batc-inputs/attachment.txt"]
    assert {f["path"] for f in item["observation"]["exempted_bookkeeping"]} == set(books)
    done = await apply(daemon, doc)
    assert done["status"] == "succeeded", done
    assert not wt.exists()
    tomb = cleanup.lookup(daemon.journal.db, str(wt))[0]
    assert "originals in artifact store" in tomb["attachment_replicas"]


async def test_e01_edited_or_extra_replica_content_counts_as_uncommitted(daemon, mock, monkeypatch):
    cp, op = await setup_work(daemon, mock)
    wt = Path(op["result"]["worktree_path"])
    (Path(op["external_refs"]["clone_path"]) / ".git/info/exclude").write_text(".batc-inputs/\n")
    (wt / ".batc-inputs").mkdir()
    (wt / ".batc-inputs/attachment.txt").write_text("original")
    attachment_evidence(monkeypatch, wt, [".batc-inputs/attachment.txt"])
    (wt / ".batc-inputs/attachment.txt").write_text("edited attachment")
    (wt / ".batc-inputs/output.txt").write_text("agent output")
    target = {"kind": "checkpoint", "checkpoint_id": cp["checkpoint_id"]}
    doc = await cleanup.preview(daemon.ops, CLEANER, target)
    item = next(i for i in doc["items"] if i["kind"] == "worktree")
    assert item["decision"] == "retain"
    assert "UNCOMMITTED_CHANGES" in {r["code"] for r in item["reasons"]}
    assert {".batc-inputs/attachment.txt", ".batc-inputs/output.txt"} <= {
        f["path"] for f in item["observation"]["manifest"]}
    assert not item["observation"]["exempted_replicas"]
    with pytest.raises(OperationError, match="DISCARD_SCOPE_REQUIRED"):
        await cleanup.preview(daemon.ops, CLEANER, target, {"discard_uncommitted": [item["resource_id"]]})
    doc = await cleanup.preview(daemon.ops, DISCARDER, target, {"discard_uncommitted": [item["resource_id"]]})
    done = await apply(daemon, doc, DISCARDER)
    assert done["status"] == "succeeded", done
    assert not wt.exists()
    assert cleanup.lookup(daemon.journal.db, str(wt))[0]["attachment_replicas"] is None


async def test_e01_replicas_without_manifest_are_ordinary_content(daemon, mock):
    cp, op = await setup_work(daemon, mock)
    wt = Path(op["result"]["worktree_path"])
    (Path(op["external_refs"]["clone_path"]) / ".git/info/exclude").write_text(".batc-inputs/\n")
    (wt / ".batc-inputs").mkdir()
    (wt / ".batc-inputs/attachment.txt").write_text("no materialization evidence yet")
    (wt / ".batc-inputs/.owner").write_text("no evidence for helper names either")
    doc = await cleanup.preview(daemon.ops, CLEANER, {"kind": "checkpoint", "checkpoint_id": cp["checkpoint_id"]})
    item = next(i for i in doc["items"] if i["kind"] == "worktree")
    assert item["replica_evidence"]["replica_manifest"] == []
    assert item["decision"] == "retain"
    assert "UNCOMMITTED_CHANGES" in {r["code"] for r in item["reasons"]}
    assert not item["observation"]["exempted_replicas"] and not item["observation"]["exempted_bookkeeping"]
    assert {".batc-inputs", ".batc-inputs/attachment.txt", ".batc-inputs/.owner"} <= {
        f["path"] for f in item["observation"]["manifest"]}


async def test_e01_replica_edit_after_preview_is_stale(daemon, mock, monkeypatch):
    cp, op = await setup_work(daemon, mock)
    wt = Path(op["result"]["worktree_path"])
    (Path(op["external_refs"]["clone_path"]) / ".git/info/exclude").write_text(".batc-inputs/\n")
    (wt / ".batc-inputs").mkdir()
    (wt / ".batc-inputs/attachment.txt").write_text("original")
    attachment_evidence(monkeypatch, wt, [".batc-inputs/attachment.txt"])
    doc = await cleanup.preview(daemon.ops, CLEANER, {"kind": "checkpoint", "checkpoint_id": cp["checkpoint_id"]})
    assert doc["ready"]
    calls = len(mock.invokes)
    (wt / ".batc-inputs/attachment.txt").write_text("new content after review")
    done = await apply(daemon, doc)
    assert done["error_code"] == "PREVIEW_STALE", done
    assert not any(i["channel"] == "claude:stop-session" for i in mock.invokes[calls:])
    assert (wt / ".batc-inputs/attachment.txt").read_text() == "new content after review"


@pytest.mark.parametrize("anomaly", ["missing", "link", "hardlink", "directory"])
async def test_e01_replica_anomalies_require_reviewed_discard(daemon, mock, monkeypatch, tmp_path, anomaly):
    cp, op = await setup_work(daemon, mock)
    wt = Path(op["result"]["worktree_path"])
    (Path(op["external_refs"]["clone_path"]) / ".git/info/exclude").write_text(".batc-inputs/\n")
    (wt / ".batc-inputs").mkdir()
    replica = wt / ".batc-inputs/attachment.txt"
    replica.write_text("original content")
    attachment_evidence(monkeypatch, wt, [".batc-inputs/attachment.txt"])
    outside = tmp_path / "original.txt"
    outside.write_text("original content")
    if anomaly == "directory":
        (wt / ".batc-inputs/unexpected/empty").mkdir(parents=True)
    else:
        replica.unlink()
        if anomaly == "link":
            replica.symlink_to(outside)
        elif anomaly == "hardlink":
            import os
            os.link(outside, replica)
    target = {"kind": "checkpoint", "checkpoint_id": cp["checkpoint_id"]}
    doc = await cleanup.preview(daemon.ops, CLEANER, target)
    item = next(i for i in doc["items"] if i["kind"] == "worktree")
    assert item["decision"] == "retain"
    assert "UNCOMMITTED_CHANGES" in {r["code"] for r in item["reasons"]}
    assert any(f["type"] == anomaly for f in item["observation"]["manifest"])
    doc = await cleanup.preview(daemon.ops, DISCARDER, target, {"discard_uncommitted": [item["resource_id"]]})
    done = await apply(daemon, doc, DISCARDER)
    assert done["status"] == "succeeded", done
    assert not wt.exists() and outside.read_text() == "original content"


async def test_e01_exact_temporary_requires_creation_markers_and_never_sweeps(daemon, mock):
    cp, op = await setup_work(daemon, mock)
    clone = Path(op["external_refs"]["clone_path"])
    temp = Path(str(clone) + ".batc-tmp-" + op["operation_id"][3:15])
    import subprocess
    subprocess.run(["git", "clone", "--quiet", "--no-checkout", "--no-hardlinks", str(clone), str(temp)], check=True)  # noqa: S603,S607 - temp Git only
    git(temp, "config", "batc.managed-clone", "true")
    git(temp, "config", "batc.source", str(cp["repo_root"]))
    decoy = temp.parent / "unowned.batc-tmp-name"
    decoy.mkdir()
    (decoy / "context.txt").write_text("manual")
    doc = await cleanup.preview(daemon.ops, CLEANER, {"kind": "checkpoint", "checkpoint_id": cp["checkpoint_id"]})
    item = next(i for i in doc["items"] if i["kind"] == "temporary" and i["path"] == str(temp))
    assert item["decision"] == "reclaim", json.dumps(item, indent=2)
    done = await apply(daemon, doc)
    assert done["status"] == "succeeded", done
    assert not temp.exists() and (decoy / "context.txt").read_text() == "manual"


async def test_e01_tree_preview_apply_matches_and_read_only_resources_survive(daemon, mock, human):
    person = api_auth.Principal("planner", frozenset({"manage"}))
    async def manage(action, target, params, key):
        op, _ = daemon.ops.create(person, action=action, target=target, params=params, idempotency_key=key)
        await settle_operations(daemon.ops, timeout=30)
        done = daemon.ops.get(op["operation_id"])
        assert done["status"] == "succeeded", done
        return done["result"]
    cp1, op1 = await setup_work(daemon, mock)
    cp2, op2 = await setup_work(daemon, mock, note="child checkpoint")
    mock.metas[op2["result"]["session_id"]]["isStreaming"] = True
    project = await manage("project.create", {}, {"name": "Project"}, "project")
    parent = await manage("work_item.create", {"project_id": project["project_id"]}, {"title": "Parent"}, "parent")
    child = await manage("work_item.create", {"project_id": project["project_id"]},
                         {"title": "Child", "parent_id": parent["work_item_id"]}, "child")
    for row, cp in ((parent, cp1), (child, cp2)):
        await manage("work_item.link", {"work_item_id": row["work_item_id"]},
                         {"kind": "checkpoint", "ref": cp["checkpoint_id"]}, row["work_item_id"])
    unknown = Path(op1["external_refs"]["clone_path"]) / "unowned-worktree"
    git(op1["external_refs"]["clone_path"], "worktree", "add", "-q", "-b", "person/unowned", str(unknown), cp1["commit_sha"])
    (unknown / "manual.txt").write_text("keep this work")
    before = snapshot(human)
    target = {"kind": "work_item", "work_item_id": parent["work_item_id"], "include_children": True}
    doc = await cleanup.preview(daemon.ops, CLEANER, target)
    assert set(doc["work_items"]) == {parent["work_item_id"], child["work_item_id"]}
    assert len({i["resource_id"] for i in doc["items"]}) == len(doc["items"])
    unowned = next(i for i in doc["items"] if i.get("path") == str(unknown))
    assert unowned["decision"] == "retain" and "UNKNOWN_READ_ONLY" in {r["code"] for r in unowned["reasons"]}
    # A direct API choice cannot override even the delivery reason of an unowned resource.
    manual_choice = await cleanup.preview(daemon.ops, CLEANER, target, {"release_undelivered": [unowned["resource_id"]]})
    kept = next(i for i in manual_choice["items"] if i["resource_id"] == unowned["resource_id"])
    assert not kept["overridden_reasons"] and "RESULTS_NOT_DELIVERED" in {r["code"] for r in kept["reasons"]}
    planned = {i["resource_id"] for i in doc["items"] if i["decision"] == "reclaim"}
    done = await apply(daemon, doc)
    assert done["status"] == "succeeded", done
    assert set(done["result"]["tombstones"]) == planned
    assert not Path(op1["result"]["worktree_path"]).exists()
    assert Path(op2["result"]["worktree_path"]).exists() and snapshot(human) == before
    assert (unknown / "manual.txt").read_text() == "keep this work"
    assert cleanup.lookup(daemon.journal.db, parent["work_item_id"])


def test_e01_keep_defaults_reject_purge_and_never_sweep_by_name():
    from bat_agent_connector.config import ConfigError, parse_config
    assert vars(parse_config({}).cleanup) == {"retained_refs": "keep", "history_retention": "forever", "permanent_delete": False}
    for settings in ({"permanent_delete": True}, {"history_retention": "30days"}, {"retained_refs": "delete"}):
        with pytest.raises(ConfigError):
            parse_config({"cleanup": settings})


async def test_e01_cleanup_adapters_share_the_action_contract(daemon, mock, monkeypatch, tmp_path):
    from bat_agent_connector import cli, mcp_server
    from tests.test_api_v1 import http
    cp, _ = await setup_work(daemon, mock)
    token = api_auth.issue(daemon.journal.db, CLEANER.actor, ["observe", "cleanup"])
    server = await asyncio.start_server(daemon._handle, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    monkeypatch.setenv("BATC_TASK_URL", f"http://127.0.0.1:{port}/rpc")
    monkeypatch.setenv("BATC_API_TOKEN", token)
    try:
        status, body = await http(port, "POST", "/api/v1/cleanup-previews", tok=token,
            body={"target": {"kind": "checkpoint", "checkpoint_id": cp["checkpoint_id"]}})
        assert status == 200 and body["preview"]["ready"]
        doc = body["preview"]
        # CLI submits the same reviewed document; the daemon's own OperationService executes it.
        file = tmp_path / "preview.json"
        file.write_text(json.dumps(body))
        args = cli.build_parser().parse_args(["resource-cleanup", "apply", "--preview-file", str(file), "--key", "same-plan", "--confirm"])
        assert await asyncio.to_thread(cli.cmd_resource_cleanup, args) == 0
        await settle_operations(daemon.ops, timeout=60)
        tool_server, fleet = mcp_server.build_server(daemon.fleet.config)
        tools = {t.name: t for t in await tool_server.list_tools()}
        assert {"cleanup_preview", "cleanup_apply", "cleanup_retained", "cleanup_tombstones"} <= tools.keys()
        ret = await tool_server.call_tool("cleanup_retained", {})
        text = json.dumps(ret.model_dump() if hasattr(ret, "model_dump") else ret, default=str)
        assert "refs/batc/retained/" in text
        assert len(daemon.journal.db.execute("SELECT * FROM cleanup_runs").fetchall()) == 1
        assert daemon.journal.db.execute("SELECT fingerprint FROM cleanup_runs").fetchone()[0] == doc["fingerprint"]
        await fleet.close()
    finally:
        server.close()
        await server.wait_closed()


async def test_e01_every_mutation_rechecks_policy_and_canonical_destination(daemon, mock, human):
    cp, op = await setup_work(daemon, mock)
    target = {"kind": "checkpoint", "checkpoint_id": cp["checkpoint_id"]}
    doc = await cleanup.preview(daemon.ops, CLEANER, target)
    wt = Path(op["result"]["worktree_path"])
    moved = wt.with_name(wt.name + "-kept")
    wt.rename(moved)
    wt.symlink_to(human, target_is_directory=True)
    manual, writes = snapshot(human), len(mock.invokes)
    fresh = await cleanup.preview(daemon.ops, CLEANER, target)
    item = next(i for i in fresh["items"] if i["kind"] == "worktree" and i.get("proven"))
    assert "WORKDIR_NOT_MANAGED" in {r["code"] for r in item["reasons"]}
    done = await apply(daemon, doc)
    assert done["error_code"] == "PREVIEW_STALE"
    assert not any(i["channel"] == "claude:stop-session" for i in mock.invokes[writes:])
    assert snapshot(human) == manual and moved.exists()


async def test_e01_accepted_authority_survives_key_rotation_and_carrier_stays_usable(daemon, mock):
    cp, op = await setup_work(daemon, mock)
    doc = await cleanup.preview(daemon.ops, CLEANER, {"kind": "checkpoint", "checkpoint_id": cp["checkpoint_id"]})
    accepted, _ = daemon.ops.create(CLEANER, **cleanup.apply_request(doc, "accepted"))
    cleanup.install(daemon.ops, "rotated-local-key")
    await settle_operations(daemon.ops, timeout=60)
    done = daemon.ops.get(accepted["operation_id"])
    assert done["status"] == "succeeded", done
    from bat_agent_connector import resource_policy
    hc = daemon.fleet.config.host("h1")
    resource_policy.check_cleanup_worktree(hc, op["external_refs"]["clone_path"], None, "batc/cp-new-slot")
    resource_policy._check_resolved(hc, op["external_refs"]["clone_path"], {})
    # New unsigned previews cannot survive the same rotation, even though accepted work can.
    with pytest.raises(OperationError, match="PREVIEW_TOKEN_INVALID"):
        cleanup.decode_token(daemon.ops, doc["preview_token"])


def repair_facts(d, cp, op, human):
    """Crash fixtures use the exact facts written by integration preview/receipt/handoff, and real Git paths."""
    import time

    from bat_agent_connector import resource_policy
    from bat_agent_connector.config import GitHubRepo, IntegrateConfig
    from bat_agent_connector.resource_ids import worktree_id
    repo = GitHubRepo("o/r", integrate=IntegrateConfig(("h1",), "https://github.example/o/r.git"))
    d.fleet.config.github.repos["o/r"] = repo
    area = Path(resource_policy.integration_area_path(d.fleet.config.host("h1"), "h1", "o/r", repo.integrate.remote_url))
    area.mkdir(parents=True)
    git(area, "init", "--template=", "-q", "--bare", str(area / "repo.git"))
    for key, value in {"managed-clone": "true", "role": "integration", "host": "h1", "repository": "o/r",
                       "remote-url": repo.integrate.remote_url}.items():
        git(area / "repo.git", "config", "batc." + key, value)
    git(area / "repo.git", "fetch", "-q", str(human), cp["commit_sha"])
    handoff_id, apply_id, pv_id = "op_" + "d" * 32, "op_" + "e" * 32, "ipv_" + "f" * 32
    branch = "batc/fix-" + handoff_id[3:15]
    wt = area / "wt" / ("batc-fix-" + handoff_id[3:15])
    git(area / "repo.git", "worktree", "add", "-q", "-b", branch, str(wt), cp["commit_sha"])
    pv = {"preview_id": pv_id, "operation_id": "op_" + "f" * 32, "actor": "reviewer", "host": "h1",
          "repository": "o/r", "repository_id": 1, "pull_number": 1, "head_ref": "feature/result",
          "head_sha": cp["commit_sha"], "base_ref": "main", "base_sha": cp["commit_sha"],
          "remote_url": repo.integrate.remote_url, "area_path": str(area), "sources": "[]", "document": "{}",
          "digest": "fixture", "ready": 1, "blocking": "[]", "created_at": time.time(), "expires_at": 0}
    columns = ",".join(pv)
    d.journal.db.execute(f"INSERT INTO integration_previews({columns}) VALUES({','.join('?' for _ in pv)})", tuple(pv.values()))  # noqa: S608 - fixed fixture keys
    row = dict(d.journal.db.execute("SELECT * FROM operations WHERE operation_id=?", (op["operation_id"],)).fetchone())
    row.update(operation_id=handoff_id, idem_key="crashed-handoff", action="integration.handoff", target=json.dumps({"operation_id": apply_id}),
               params=json.dumps({"seq": 1}), status="failed", external_refs=json.dumps(
                   {"admission_binding": json.loads(row["external_refs"])["admission_binding"],
                    "seq": 1, "worktree_path": str(wt), "branch": branch}))
    columns = ",".join(row)
    d.journal.db.execute(f"INSERT INTO operations({columns}) VALUES({','.join('?' for _ in row)})", tuple(row.values()))  # noqa: S608 - fixed fixture keys
    seed_item = {"original_ids": [op["operation_id"]], "observation": {"head": cp["commit_sha"]}}
    add_receipt(d, seed_item, status="conflict")
    d.journal.db.execute("UPDATE integration_receipts SET operation_id=?,preview_id=?,repair_worktree=?,repair_branch=?",
                         (apply_id, pv_id, str(wt), branch))
    return area, wt, handoff_id, worktree_id("h1", "integration.handoff", handoff_id, "repair")


async def test_e01_crashed_continue_and_handoff_intents_are_discovered_without_adoption(daemon, mock, human):
    cp, op = await setup_work(daemon, mock)
    daemon.journal.db.execute("DELETE FROM checkpoint_runs")  # Only the continue operation survived the crash.
    target = {"kind": "checkpoint", "checkpoint_id": cp["checkpoint_id"]}
    doc = await cleanup.preview(daemon.ops, CLEANER, target)
    from bat_agent_connector.resource_ids import worktree_id
    assert any(i["resource_id"] == worktree_id("h1", "checkpoint.continue", op["operation_id"], "worktree") for i in doc["items"])
    daemon.journal.db.execute("UPDATE operation_steps SET status='uncertain' WHERE operation_id=? AND name='worktree.prepare'", (op["operation_id"],))
    daemon.journal.db.execute("UPDATE operations SET status='failed' WHERE operation_id=?", (op["operation_id"],))
    doc = await cleanup.preview(daemon.ops, CLEANER, target)
    assert all("COMMAND_UNRESOLVED" in {r["code"] for r in i["reasons"]} for i in doc["items"] if i["kind"] in {"session", "worktree"} and i.get("proven"))
    area, wt, handoff, rid = repair_facts(daemon, cp, op, human)
    repair = await cleanup.preview(daemon.ops, CLEANER, {"kind": "integration", "operation_id": handoff})
    item = next(i for i in repair["items"] if i["resource_id"] == rid)
    assert item["path"] == str(wt) and item["repository"] == str(area)
    assert item["decision"] == "reclaim", item
    done = await apply(daemon, repair)
    assert done["status"] == "succeeded", done
    assert not wt.exists() and (area / "repo.git").exists()


async def test_e01_empty_integration_temporary_has_exact_intent_and_no_restore_promise(daemon, mock, human):
    cp, op = await setup_work(daemon, mock)
    area, _, handoff, _ = repair_facts(daemon, cp, op, human)
    # Reuse the real preview creation intent for the exact temporary created by Area.create().
    row = dict(daemon.journal.db.execute("SELECT * FROM operations WHERE operation_id=?", (op["operation_id"],)).fetchone())
    preview_op = "op_" + "f" * 32
    row.update(operation_id=preview_op, idem_key="empty-preview", action="integration.preview", target=json.dumps({"host": "h1", "repository": "o/r", "pull_number": 1}),
               params=json.dumps({"sources": []}), status="failed", external_refs=json.dumps(
                   {"admission_binding": json.loads(row["external_refs"])["admission_binding"], "area_path": str(area)}))
    columns = ",".join(row)
    daemon.journal.db.execute(f"INSERT INTO operations({columns}) VALUES({','.join('?' for _ in row)})", tuple(row.values()))  # noqa: S608 - fixed fixture keys
    step = dict(daemon.journal.db.execute("SELECT * FROM operation_steps WHERE operation_id=? AND name='worktree.prepare'", (op["operation_id"],)).fetchone())
    step.update(operation_id=preview_op, name="prepare", request=json.dumps({"area": str(area)}))
    columns = ",".join(step)
    daemon.journal.db.execute(f"INSERT INTO operation_steps({columns}) VALUES({','.join('?' for _ in step)})", tuple(step.values()))  # noqa: S608 - fixed fixture keys
    temp = area / ("repo.git.batc-tmp-" + preview_op[3:15])
    git(area, "init", "-q", "--bare", "--template=", str(temp))
    for key, value in {"managed-clone": "true", "role": "integration", "host": "h1", "repository": "o/r",
                       "remote-url": "https://github.example/o/r.git"}.items():
        git(temp, "config", "batc." + key, value)
    doc = await cleanup.preview(daemon.ops, CLEANER, {"kind": "integration", "operation_id": preview_op})
    item = next(i for i in doc["items"] if i["kind"] == "temporary" and i["path"] == str(temp))
    assert item["decision"] == "reclaim" and item["steps"] == ["remove.temporary", "finalize"], item
    done = await apply(daemon, doc)
    assert done["status"] == "succeeded", done
    assert not temp.exists()
    tomb = cleanup.lookup(daemon.journal.db, item["resource_id"])[0]
    assert tomb["retained_ids"] == []


@pytest.mark.parametrize("lost_reply", [False, True])
async def test_e01_cancel_reconciles_sent_steps_and_releases_only_confirmed_reservations(daemon, mock, lost_reply):
    cp, op = await setup_work(daemon, mock)
    target = {"kind": "checkpoint", "checkpoint_id": cp["checkpoint_id"]}
    doc = await cleanup.preview(daemon.ops, CLEANER, target)
    original = daemon.ops.context["git_runner"]
    from bat_agent_connector.operations import AmbiguousOutcome
    class CancelRunner(LocalRunner):
        fired = False
        async def run(self, host, script, timeout_s=None):
            out = await original.run(host, script, timeout_s)
            request = json.loads(__import__("base64").b64decode(__import__("shlex").split(script)[-1]))
            if request.get("phase") == "preserve" and not self.fired:
                self.fired = True
                pending = daemon.journal.db.execute("SELECT operation_id FROM cleanup_runs").fetchone()[0]
                daemon.ops.cancel(CLEANER, pending)
                if lost_reply:
                    raise AmbiguousOutcome("preserve reply lost while cancel was requested")
            return out
    daemon.ops.context["git_runner"] = CancelRunner()
    done = await apply(daemon, doc)
    if lost_reply:
        assert done["status"] == "uncertain", done
        daemon.journal.db.execute("UPDATE operations SET next_run_at=0 WHERE operation_id=?", (done["operation_id"],))
        await settle_operations(daemon.ops, timeout=60)
        done = daemon.ops.get(done["operation_id"])
    assert done["status"] == "cancelled", done
    wt = next(i for i in doc["items"] if i["kind"] == "worktree" and i.get("proven"))
    assert Path(wt["path"]).exists()
    assert git(wt["repository"], "rev-parse", "refs/batc/retained/" + wt["resource_id"] + "/" + wt["observation"]["head"])
    from bat_agent_connector.errors import ResourceReadOnly
    with pytest.raises(ResourceReadOnly, match="CLEANUP_IN_PROGRESS"):
        cleanup.guard("h1", path=wt["path"], branch=wt["branch"])
    rows = cleanup.receipts(daemon.ops, done["operation_id"])
    row = next(r for r in rows if r["resource_id"] == wt["resource_id"])
    assert row["status"] == "uncertain" and row["cancel_requested"]
    assert {p["phase"] for p in row["completed_phases"]} == {"stop", "preserve"}
    assert row["after_state"]["guard_released"] is False
    assert not any(i["decision"] == "reclaim" and i["resource_id"] == wt["resource_id"]
                   for i in (await cleanup.preview(daemon.ops, CLEANER, target))["items"])
