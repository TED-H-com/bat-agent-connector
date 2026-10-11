"""Task cleanup authority over real temporary Git and MockBat; no live writes."""
import copy
import json
from pathlib import Path

import pytest

from bat_agent_connector import cleanup, registry
from tests.test_cleanup import CLEANER, apply, setup_work
from tests.test_cleanup import daemon as cleanup_daemon
from tests.test_cleanup import human as cleanup_human
from tests.test_cleanup import known_live_terminals as cleanup_live_terminals

daemon = cleanup_daemon
human = cleanup_human
known_live_terminals = cleanup_live_terminals


def owned(d, sid, *, state="failed", key="terminal"):
    task = d.journal.submit(project="fixture", host="h1", workspace="demo-project", original_words="task work",
                            idempotency_key=key)
    tid = task["task_id"]
    d.journal.change(tid, "dispatching")
    d.journal.change(tid, "accepted", fields={"session_id": sid})
    d.journal.add_branch(tid, session_id=sid, provider="claude", role="lead", reason="start")
    registry.update("h1", sid, task_id=tid, role="lead")
    if state == "failed":
        d.journal.change(tid, "failed")
    return tid


async def test_task_preview_removes_terminal_carrier_retains_branch_and_history(daemon, mock):
    cp, op = await setup_work(daemon, mock)
    sid = op["result"]["session_id"]
    tid = owned(daemon, sid)
    before = list(daemon.journal.db.iterdump()), registry.registry_path().read_bytes(), copy.deepcopy(mock.invokes)
    target = {"kind": "task", "task_id": tid}
    doc = await cleanup.preview(daemon.ops, CLEANER, target)
    assert list(daemon.journal.db.iterdump()) == before[0]
    assert registry.registry_path().read_bytes() == before[1]
    assert doc["ready"], [(i["kind"], i["reasons"]) for i in doc["items"]]
    branch = next(i for i in doc["items"] if i["kind"] == "local_branch")
    assert branch["decision"] == "retain"
    result = await apply(daemon, doc)
    assert result["status"] == "succeeded", result
    assert result["external_refs"]["task_id"] == tid
    assert not Path(op["result"]["worktree_path"]).exists()
    assert cleanup.lookup(daemon.journal.db, tid)
    assert daemon.journal.get(tid)["state"] == "failed"
    assert len([i for i in mock.invokes[len(before[2]):] if i["channel"] == "claude:stop-session"]) == 1


@pytest.mark.parametrize("status,blocked", [("settled", False), ("rejected", False), ("cancelled", False),
    ("intent", True), ("uncertain", True), ("needs_review", True), ("running", True)])
async def test_task_command_disposition(daemon, mock, status, blocked):
    _, op = await setup_work(daemon, mock)
    sid = op["result"]["session_id"]
    tid = owned(daemon, sid, state="accepted")
    cmd, _ = daemon.journal.command(tid, "interrupt", sid, {}, "command")
    daemon.journal.command_status(cmd["command_id"], status)
    daemon.journal.change(tid, "failed")
    doc = await cleanup.preview(daemon.ops, CLEANER, {"kind": "task", "task_id": tid})
    assert doc["ready"] is not blocked


async def external(d, mock, *, state="failed", commit=False):
    from tests.test_checkpoints import git
    _, continuation = await setup_work(d, mock)
    root = Path(continuation["external_refs"]["clone_path"])
    task = d.journal.submit(project="fixture", host="h1", workspace="demo-project", original_words="external work",
                            idempotency_key="external")
    tid = task["task_id"]
    suffix = tid.replace("-", "")[:12]
    path, branch = root / ".bat-worktrees" / ("batc-task-" + suffix), "batc/task-" + suffix
    git(root, "worktree", "add", "-b", branch, str(path), "HEAD")
    if commit:
        (path / "result.txt").write_text("unmerged result")
        git(path, "add", ".")
        git(path, "-c", "user.name=test", "-c", "user.email=test@example.invalid", "commit", "-qm", "result")
    base, sha = git(root, "rev-parse", "HEAD"), git(path, "rev-parse", "HEAD")
    d.journal.change(tid, "dispatching", fields={"external_worktree_path": str(path), "external_branch": branch,
                                                "base_commit": base})
    d.journal.change(tid, state)
    return tid, path, sha


async def test_automatic_shared_finalization_preserves_original_ids_and_operation(daemon, mock):
    from bat_agent_connector import task_cleanup
    from tests.operation_helpers import settle_operations
    from tests.test_checkpoints import git
    tid, path, sha = await external(daemon, mock, commit=True)
    operation = await task_cleanup.automatic(daemon, daemon.journal.get(tid))
    assert operation, [(i["kind"], i["reasons"], i.get("task_cleanup")) for i in (await cleanup.preview(daemon.ops, task_cleanup.SYSTEM, {"kind": "task", "task_id": tid}, _automatic=True))["items"]]
    assert operation["external_refs"]["task_id"] == tid
    assert (await task_cleanup.automatic(daemon, daemon.journal.get(tid)))["operation_id"] == operation["operation_id"]
    accepted = [e for e in daemon.journal.events(tid) if e["kind"] == "cleanup_accepted"]
    assert len(accepted) == 1
    assert json.loads(accepted[0]["body"])["operation_id"] == operation["operation_id"]
    await settle_operations(daemon.ops, timeout=60)
    done = daemon.ops.get(operation["operation_id"])
    assert done["status"] == "succeeded", done
    assert not path.exists()
    assert daemon.journal.get(tid)["external_worktree_path"] is None
    history = cleanup.lookup(daemon.journal.db, tid)
    assert len(history) == 1 and history[0]["reason"] == "task_lifecycle"
    assert history[0]["path"] == str(path) and history[0]["after"]["head"] == sha
    assert git(path.parent.parent, "rev-parse", history[0]["after"]["retained_ref"]) == sha
    assert "claude:stop-session" not in mock.channels()
    later = await cleanup.preview(daemon.ops, CLEANER, {"kind": "task", "task_id": tid})
    old = next(i for i in later["items"] if i["kind"] == "worktree" and i.get("flavor") == "task")
    assert old["resource_id"] == history[0]["resource_id"] and old["decision"] == "retain"


async def test_explicit_terminal_paused_cleanup_allowed_automatic_retains(daemon, mock):
    from bat_agent_connector import task_cleanup
    tid, path, _ = await external(daemon, mock, state="dispatching")
    daemon.journal.pause(tid)
    daemon.journal.change(tid, "failed")
    task = daemon.journal.get(tid)
    assert task["paused"]
    assert await task_cleanup.automatic(daemon, task) is None
    assert not daemon.journal.db.execute("SELECT 1 FROM operations WHERE action='cleanup.apply'").fetchone()
    doc = await cleanup.preview(daemon.ops, CLEANER, {"kind": "task", "task_id": tid})
    item = next(i for i in doc["items"] if i.get("flavor") == "task" and i["kind"] == "worktree")
    bound = item["task_cleanup"]["binding"]["tasks"][0]
    assert bound["paused"] and bound["control_version"] == task["control_version"]
    assert doc["ready"], doc
    done = await apply(daemon, doc)
    assert done["status"] == "succeeded", done
    assert not path.exists()
    assert daemon.journal.get(tid)["paused"]
    assert daemon.journal.get(tid)["external_worktree_path"] is None


@pytest.mark.parametrize("change_head", [False, True])
async def test_done_cleanup_requires_original_observed_verification_head(daemon, mock, change_head):
    from tests.test_checkpoints import git
    tid, path, sha = await external(daemon, mock, state="dispatching")
    tree = git(path, "rev-parse", "HEAD^{tree}")
    daemon.journal.change(tid, "accepted")
    daemon.journal.change(tid, "verifying")
    daemon.journal.record_observed_verification(tid, {
        "source": "observed_runner", "candidate_commit": sha, "tree_hash": tree,
        "command": "fixture verification", "exit_code": 0, "log_ref": "fixture:observed",
        "output_sha256": "c" * 64})
    daemon.journal.change(tid, "done", fields={"verification_commit": sha, "verification_tree": tree})
    target = {"kind": "task", "task_id": tid}
    doc = await cleanup.preview(daemon.ops, CLEANER, target)
    assert doc["ready"], doc
    if change_head:
        (path / "later.txt").write_text("changed after reviewed verification")
        git(path, "add", ".")
        git(path, "-c", "user.name=test", "-c", "user.email=test@example.invalid", "commit", "-qm", "later")
        fresh = await cleanup.preview(daemon.ops, CLEANER, target)
        item = next(i for i in fresh["items"] if i.get("flavor") == "task" and i["kind"] == "worktree")
        assert not item["task_cleanup"]["eligible"] and not fresh["ready"]
        assert {"code": "BINDING_MISMATCH", "detail": "verified_commit_changed"} in item["task_cleanup"]["reasons"]
    done = await apply(daemon, doc)
    if change_head:
        assert done["status"] == "failed" and done["error_code"] == "PREVIEW_STALE", done
        assert path.exists() and daemon.journal.get(tid)["external_worktree_path"] == str(path)
    else:
        assert done["status"] == "succeeded", done
        assert not path.exists() and daemon.journal.get(tid)["external_worktree_path"] is None
    assert "claude:stop-session" not in mock.channels()


async def test_default_target_keeps_guard_and_active_shared_successor_blocks_task(daemon, mock):
    cp, op = await setup_work(daemon, mock)
    sid = op["result"]["session_id"]
    original = owned(daemon, sid)
    default = await cleanup.preview(daemon.ops, CLEANER, {"kind": "checkpoint", "checkpoint_id": cp["checkpoint_id"]})
    assert not default["ready"]
    successor = owned(daemon, sid, state="accepted", key="successor")
    doc = await cleanup.preview(daemon.ops, CLEANER, {"kind": "task", "task_id": original})
    assert not doc["ready"]
    for item in doc["items"]:
        if item["kind"] in {"session", "worktree"}:
            assert set(item["task_cleanup"]["task_ids"]) == {original, successor}
            assert item["decision"] == "retain"
    assert Path(op["result"]["worktree_path"]).exists()


@pytest.mark.parametrize("change", ["version", "successor", "streaming", "pending"])
async def test_final_frame_race_sends_no_stop(daemon, mock, monkeypatch, change):
    _, op = await setup_work(daemon, mock)
    sid = op["result"]["session_id"]
    tid = owned(daemon, sid)
    doc = await cleanup.preview(daemon.ops, CLEANER, {"kind": "task", "task_id": tid})
    client = daemon.fleet.client("h1")
    read = client.guard_read
    reached = []
    async def race(channel, params):
        result = await read(channel, params)
        if channel == "claude:get-session-state":
            reached.append(change)
            if change == "version":
                daemon.journal.db.execute("UPDATE tasks SET control_version=control_version+1 WHERE task_id=?", (tid,))
            elif change == "successor":
                owned(daemon, sid, state="accepted", key="successor")
            elif change == "streaming":
                result = {**result, "isStreaming": True}
            else:
                result = {**result, "pendingPermission": {"toolUseId": "new-question"}}
        return result
    monkeypatch.setattr(client, "guard_read", race)
    before = mock.channels().count("claude:stop-session")
    done = await apply(daemon, doc)
    assert reached == [change]
    assert done["status"] in {"needs_attention", "failed"}, done
    assert mock.channels().count("claude:stop-session") == before
    assert Path(op["result"]["worktree_path"]).exists()


async def test_warm_claim_and_cleanup_reservation_contend(daemon, mock):
    from bat_agent_connector.errors import ResourceReadOnly
    _, op = await setup_work(daemon, mock)
    sid = op["result"]["session_id"]
    tid = owned(daemon, sid)
    doc = await cleanup.preview(daemon.ops, CLEANER, {"kind": "task", "task_id": tid})
    item = next(i for i in doc["items"] if i["kind"] == "worktree")
    cleanup._mark(item, "fixture-reservation", "reserved", ops=daemon.ops)
    entry = registry.get("h1", sid)
    with pytest.raises(ResourceReadOnly, match="CLEANUP_IN_PROGRESS"):
        registry.claim_warm("h1", sid, previous_task_id=tid, task_id="successor", workspace_id=entry["workspace_id"],
                            cwd=entry["cwd"], branch=entry["branch"])
    assert registry.get("h1", sid)["task_id"] == tid


async def test_lost_task_stop_ack_retains_original_claim_and_never_resends(daemon, mock, monkeypatch):
    from bat_agent_connector import lifecycle
    from bat_agent_connector.errors import ConnectionLost
    from tests.operation_helpers import settle_operations
    _, op = await setup_work(daemon, mock)
    sid = op["result"]["session_id"]
    tid = owned(daemon, sid)
    doc = await cleanup.preview(daemon.ops, CLEANER, {"kind": "task", "task_id": tid})
    stop, calls = lifecycle._stop, []
    async def lost(*args, **kwargs):
        calls.append(1)
        await stop(*args, **kwargs)
        raise ConnectionLost("ACK lost")
    monkeypatch.setattr(lifecycle, "_stop", lost)
    done = await apply(daemon, doc)
    assert done["status"] == "uncertain", done
    daemon.journal.db.execute("UPDATE tasks SET control_version=control_version+1 WHERE task_id=?", (tid,))
    daemon.journal.db.execute("UPDATE operations SET next_run_at=0 WHERE operation_id=?", (done["operation_id"],))
    await settle_operations(daemon.ops, timeout=30)
    assert calls == [1]
    assert Path(op["result"]["worktree_path"]).exists()
    assert registry.get("h1", sid)["cleanup_reservation"] == done["operation_id"]


async def test_private_automatic_origin_cannot_be_requested_by_public_principal(daemon, mock):
    from bat_agent_connector import api_auth, task_cleanup
    from bat_agent_connector.operations import OperationError
    tid, _, _ = await external(daemon, mock)
    target = {"kind": "task", "task_id": tid}
    impostor = api_auth.Principal(task_cleanup.SYSTEM.actor, task_cleanup.SYSTEM.scopes)
    with pytest.raises(OperationError, match="FORBIDDEN"):
        await cleanup.preview(daemon.ops, impostor, target, _automatic=True)
    doc = await cleanup.preview(daemon.ops, task_cleanup.SYSTEM, target, _automatic=True)
    with pytest.raises(OperationError, match="FORBIDDEN"):
        daemon.ops.create(impostor, **cleanup.apply_request(doc, "impostor"))
    assert not daemon.journal.db.execute("SELECT 1 FROM operations WHERE action='cleanup.apply'").fetchone()


async def test_historical_backfill_is_idempotent_and_live_retained_read_is_truthful(daemon, mock):
    from bat_agent_connector import task_cleanup
    from tests.test_checkpoints import git
    tid, path, sha = await external(daemon, mock, commit=True)
    before = await cleanup.preview(daemon.ops, CLEANER, {"kind": "task", "task_id": tid})
    rid = next(i["resource_id"] for i in before["items"] if i.get("flavor") == "task" and i["kind"] == "worktree")
    task_ref = "refs/batc/tasks/" + tid.replace("-", "")[:12]
    git(path.parent.parent, "update-ref", task_ref, sha)
    git(path.parent.parent, "worktree", "remove", str(path))
    daemon.journal.complete_external_cleanup(tid, {"path": str(path), "branch": "batc/task-" + tid.replace("-", "")[:12],
        "retained_ref": task_ref, "commit": sha, "mode": "removed"})
    task_cleanup.backfill(daemon.ops)
    db = list(daemon.journal.db.iterdump())
    task_cleanup.backfill(daemon.ops)
    assert list(daemon.journal.db.iterdump()) == db
    history = cleanup.lookup(daemon.journal.db, tid)
    assert history[0]["resource_id"] == rid and history[0]["reason"] == "historical_task_cleanup"
    assert history[0]["operation_id"] is None and history[0]["accepted_authorization"] is None
    retained = await cleanup.retained(daemon.ops, resource_id=rid)
    assert retained["retained"][0]["commit_sha"] == sha
    assert retained["retained"][0]["operation_id"] is None and retained["retained"][0]["task_event_id"]
    git(path.parent.parent, "update-ref", "-d", task_ref, sha)
    absent = await cleanup.retained(daemon.ops, resource_id=rid)
    assert not absent["retained"] and absent["unavailable"][0]["reason"] == "RETAINED_CONTENT_MISSING"


async def test_task_http_mcp_cli_share_preview_and_operation(daemon, mock, monkeypatch, tmp_path, capsys):
    import asyncio
    import json

    from bat_agent_connector import api_auth, cli, mcp_server
    from tests.operation_helpers import settle_operations
    from tests.test_api_v1 import http
    _, op = await setup_work(daemon, mock)
    tid = owned(daemon, op["result"]["session_id"])
    token = api_auth.issue(daemon.journal.db, CLEANER.actor, ["observe", "cleanup"])
    observer = api_auth.issue(daemon.journal.db, "observer", ["observe"])
    server = await asyncio.start_server(daemon._handle, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    monkeypatch.setenv("BATC_TASK_URL", f"http://127.0.0.1:{port}/rpc")
    monkeypatch.setenv("BATC_API_TOKEN", token)
    try:
        for target in ({"kind": []}, {"kind": "task", "task_id": []}, {"kind": "task", "task_id": tid, "allow_task": True}):
            status, body = await http(port, "POST", "/api/v1/cleanup-previews", tok=token, body={"target": target})
            assert status == 422 and body["error"]["code"] == "INVALID_TARGET", body
        status, body = await http(port, "POST", "/api/v1/cleanup-previews", tok=token,
                                 body={"target": {"kind": "task", "task_id": tid}, "origin": "task_lifecycle"})
        assert status == 422
        args = cli.build_parser().parse_args(["resource-cleanup", "preview", "--task", tid, "--json"])
        assert await asyncio.to_thread(cli.cmd_resource_cleanup, args) == 0
        doc = json.loads(capsys.readouterr().out)["preview"]
        assert doc["target"] == {"kind": "task", "task_id": tid} and doc["ready"]
        request = cleanup.apply_request(doc, "reviewed-task")
        status, body = await http(port, "POST", "/api/v1/operations", tok=observer, body=request)
        assert status == 403
        tool_server, fleet = mcp_server.build_server(daemon.fleet.config)
        try:
            result = await tool_server.call_tool("cleanup_preview", {"target": doc["target"]})
            assert not result.is_error, result
            status, body = await http(port, "POST", "/api/v1/operations", tok=token, body=request)
            assert status == 202, body
            accepted = body["operation"]["operation_id"]
            status, replay = await http(port, "POST", "/api/v1/operations", tok=token, body=request)
            assert status == 200 and not replay["created"] and replay["operation"]["operation_id"] == accepted
            await settle_operations(daemon.ops, timeout=60)
            assert daemon.ops.get(accepted)["status"] == "succeeded"
        finally:
            await fleet.close()
    finally:
        server.close()
        await server.wait_closed()


async def test_absent_task_session_releases_capacity_after_carrier_receipt(daemon, mock):
    _, op = await setup_work(daemon, mock)
    sid = op["result"]["session_id"]
    tid = owned(daemon, sid)
    mock.metas[sid] = None
    mock.ws_doc["terminals"] = [t for t in mock.ws_doc["terminals"] if t["id"] != sid]
    doc = await cleanup.preview(daemon.ops, CLEANER, {"kind": "task", "task_id": tid})
    assert doc["ready"]
    done = await apply(daemon, doc)
    assert done["status"] == "succeeded", done
    assert registry.get("h1", sid)["status"] == "absent_at_cleanup"
    assert registry.get("h1", sid)["task_id"] == tid
    assert mock.channels().count("claude:stop-session") == 0


async def test_preupgrade_remove_crash_finishes_from_real_retained_read_without_resend(daemon, mock):
    from bat_agent_connector import task_cleanup
    from tests.operation_helpers import settle_operations
    from tests.test_checkpoints import git
    tid, path, sha = await external(daemon, mock, commit=True)
    ref = "refs/batc/tasks/" + tid.replace("-", "")[:12]
    git(path.parent.parent, "update-ref", ref, sha)
    git(path.parent.parent, "worktree", "remove", str(path))
    # Crash before the old remover could clear the task pointer or emit its event.
    operation = await task_cleanup.automatic(daemon, daemon.journal.get(tid))
    assert operation
    await settle_operations(daemon.ops, timeout=60)
    assert daemon.ops.get(operation["operation_id"])["status"] == "succeeded"
    assert daemon.journal.get(tid)["external_worktree_path"] is None
    assert cleanup.lookup(daemon.journal.db, tid)[0]["after"]["already_absent"]
    assert not daemon.journal.db.execute("SELECT 1 FROM operation_steps WHERE operation_id=?", (operation["operation_id"],)).fetchone()
    assert git(path.parent.parent, "rev-parse", ref) == sha


async def test_automatic_crash_after_remove_recovers_same_steps_without_resend(daemon, mock, monkeypatch):
    from bat_agent_connector import task_cleanup
    from tests.operation_helpers import settle_operations
    tid, path, _ = await external(daemon, mock, commit=True)
    finish, phases = cleanup._finalize, []
    host_call = cleanup._host_call
    async def counted(*args, **kwargs):
        req = args[2]
        if req.get("phase") in cleanup.MUTATING_PHASES and not req.get("probe"):
            phases.append(req["phase"])
        return await host_call(*args, **kwargs)
    def crash(ctx, item, after):
        if item.get("flavor") == "task":
            raise OSError("local finalization unavailable after ACK")
        return finish(ctx, item, after)
    monkeypatch.setattr(cleanup, "_host_call", counted)
    monkeypatch.setattr(cleanup, "_finalize", crash)
    operation = await task_cleanup.automatic(daemon, daemon.journal.get(tid))
    await settle_operations(daemon.ops, timeout=60)
    assert daemon.ops.get(operation["operation_id"])["status"] == "uncertain"
    assert not path.exists() and daemon.journal.get(tid)["external_worktree_path"] == str(path)
    assert phases == ["preserve", "remove.worktree"]
    monkeypatch.setattr(cleanup, "_finalize", finish)
    daemon.journal.db.execute("UPDATE operations SET next_run_at=0 WHERE operation_id=?", (operation["operation_id"],))
    await settle_operations(daemon.ops, timeout=60)
    assert daemon.ops.get(operation["operation_id"])["status"] == "succeeded"
    assert daemon.journal.get(tid)["external_worktree_path"] is None
    assert phases == ["preserve", "remove.worktree"]


@pytest.mark.parametrize("blocker", ["dirty", "unknown_command", "active_successor"])
async def test_automatic_never_uses_terminal_state_as_blanket_authority(daemon, mock, blocker):
    from bat_agent_connector import task_cleanup
    tid, path, _ = await external(daemon, mock, state="dispatching" if blocker == "unknown_command" else "failed")
    if blocker == "dirty":
        (path / "only-copy.txt").write_text("must remain")
    elif blocker == "unknown_command":
        command, _ = daemon.journal.command(tid, "start_lead", "unknown", {}, "unresolved")
        daemon.journal.command_status(command["command_id"], "uncertain")
        daemon.journal.change(tid, "failed")
    else:
        successor = daemon.journal.submit(project="fixture", host="h1", workspace="demo-project", original_words="successor",
                                          idempotency_key="shared-active")
        daemon.journal.change(successor["task_id"], "dispatching", fields={
            "external_worktree_path": str(path), "external_branch": daemon.journal.get(tid)["external_branch"]})
    assert await task_cleanup.automatic(daemon, daemon.journal.get(tid)) is None
    assert path.exists() and daemon.journal.get(tid)["external_worktree_path"] == str(path)
    assert not daemon.journal.db.execute("SELECT 1 FROM operations WHERE action='cleanup.apply'").fetchone()


async def test_same_actor_replay_requires_scope_and_automatic_replay_requires_internal_issuer(daemon, mock):
    from bat_agent_connector import api_auth, task_cleanup
    from bat_agent_connector.operations import OperationError
    from tests.operation_helpers import settle_operations
    tid, _, _ = await external(daemon, mock)
    doc = await cleanup.preview(daemon.ops, task_cleanup.SYSTEM, {"kind": "task", "task_id": tid}, _automatic=True)
    request = cleanup.apply_request(doc, "private-original")
    operation, _ = daemon.ops.create(task_cleanup.SYSTEM, **copy.deepcopy(request))
    await settle_operations(daemon.ops, timeout=60)
    assert daemon.ops.get(operation["operation_id"])["status"] == "succeeded"
    for scopes in (frozenset(), task_cleanup.SYSTEM.scopes):
        with pytest.raises(OperationError, match="FORBIDDEN" if not scopes else "CONTROLLER_MISMATCH"):
            daemon.ops.create(api_auth.Principal(task_cleanup.SYSTEM.actor, scopes), **copy.deepcopy(request))
    same, created = daemon.ops.create(task_cleanup.SYSTEM, **copy.deepcopy(request))
    assert not created and same["operation_id"] == operation["operation_id"]


async def test_bare_task_cleanup_capability_cannot_bypass_stop_guard(daemon, mock):
    from bat_agent_connector import lifecycle, task_cleanup
    from bat_agent_connector.errors import ResourceReadOnly
    from bat_agent_connector.operations import OperationError
    from bat_agent_connector.safety import Audit
    _, op = await setup_work(daemon, mock)
    sid = op["result"]["session_id"]
    owned(daemon, sid)
    before = len(mock.invokes)
    with pytest.raises(ResourceReadOnly, match="TASK_OWNED"):
        await lifecycle._stop(daemon.fleet, "h1", sid, Audit(daemon.fleet.config.safety), cleanup=True, _task_cleanup=object())
    counterfeit = object.__new__(task_cleanup.Authority)
    object.__setattr__(counterfeit, "_issuer", daemon.coordinator)
    with pytest.raises(OperationError, match="TASK_OWNED"):
        await lifecycle._stop(daemon.fleet, "h1", sid, Audit(daemon.fleet.config.safety), cleanup=True, _task_cleanup=counterfeit)
    assert len(mock.invokes) == before
