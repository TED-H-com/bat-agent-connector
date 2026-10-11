"""E01/E02/plan §23: cumulative effects survive later refusals, retry and cancellation."""
from __future__ import annotations

import base64
import json
import shlex
from pathlib import Path

import pytest

from bat_agent_connector import cleanup, lifecycle, registry
from bat_agent_connector.errors import ResourceReadOnly
from tests.operation_helpers import settle_operations
from tests.test_cleanup import change_live_cwd, registered_terminal_elsewhere
from tests.test_cleanup_uncertainty import (  # noqa: F401 - shared real Git/MockBat fixtures
    CLEANER,
    DISCARDER,
    LocalRunner,
    apply,
    close_clients,
    git,
    known_live_terminals,
    setup_work,
)
from tests.test_cleanup_uncertainty import daemon as checkpoint_daemon
from tests.test_cleanup_uncertainty import human as checkpoint_human

daemon = checkpoint_daemon
human = checkpoint_human


async def reviewed(d, mock, *, dirty=False, loaded=True):
    cp, op = await setup_work(d, mock)
    path = Path(op["result"]["worktree_path"])
    if not loaded:
        mock.metas[op["result"]["session_id"]] = None
    if dirty:
        (path / "discarded.txt").write_text("the person chose to discard this")
        (path / "notes.txt").write_text("edited tracked file")
    target = {"kind": "checkpoint", "checkpoint_id": cp["checkpoint_id"]}
    actor = DISCARDER if dirty else CLEANER
    doc = await cleanup.preview(d.ops, actor, target)
    item = next(i for i in doc["items"] if i["kind"] == "worktree")
    if dirty:
        doc = await cleanup.preview(d.ops, actor, target, {"discard_uncommitted": [item["resource_id"]]})
        item = next(i for i in doc["items"] if i["kind"] == "worktree")
    return doc, item, op


def race_consumer(monkeypatch, mock, phase, kind, path):
    sid = registered_terminal_elsewhere(mock, path)
    recorded = mock.metas[sid]["cwd"]
    original = cleanup._host_call
    fired = []

    async def call(ops, host, req, timeout=cleanup.READ_DEADLINE_S, *, locked_check=None, locked_action=None):
        if req.get("phase") == phase and req.get("kind") == kind and not fired:
            fired.append(phase)
            change_live_cwd(mock, sid, path, "inside")
        return await original(ops, host, req, timeout, locked_check=locked_check, locked_action=locked_action)

    monkeypatch.setattr(cleanup, "_host_call", call)
    return lambda: mock.metas[sid].update(cwd=recorded)


def partial(d, done, item, phase):
    assert done["status"] == "needs_attention" and done["error_code"] == "CLEANUP_PARTIAL_STATE", done
    row = next(r for r in cleanup.receipts(d.ops, done["operation_id"]) if r["resource_id"] == item["resource_id"])
    assert row["status"] == "uncertain" and row["error"]["code"] == "CLEANUP_PARTIAL_STATE"
    assert row["error"]["refused_phase"] == phase
    with pytest.raises(ResourceReadOnly, match="CLEANUP_IN_PROGRESS"):
        cleanup.guard(item["host"], path=item["path"], branch=item.get("branch"))
    return row


async def resume(d, done):
    d.ops.resume(CLEANER, done["operation_id"])
    await settle_operations(d.ops, timeout=60)
    return d.ops.get(done["operation_id"])


async def test_e01_completed_discard_survives_refusal_and_new_attempt(daemon, mock, monkeypatch):
    doc, item, _ = await reviewed(daemon, mock, dirty=True)
    clear = race_consumer(monkeypatch, mock, "remove.worktree", "worktree", item["path"])
    done = await apply(daemon, doc, DISCARDER)
    row = partial(daemon, done, item, "remove.worktree")
    discarded = next(p for p in row["completed_phases"] if p["phase"] == "discard")
    assert discarded["result"]["discarded"] and discarded["result"]["after"]["status"] == ""
    assert not (Path(item["path"]) / "discarded.txt").exists()
    assert any(p["phase"] == "stop" and p["effect"] == "runtime" for p in row["completed_phases"])
    blocked = await resume(daemon, done)
    again = partial(daemon, blocked, item, "remove.worktree")
    assert again["completed_phases"] == row["completed_phases"]
    clear()
    done = await resume(daemon, blocked)
    assert done["status"] == "succeeded", done
    assert any(s["name"].endswith("remove.worktree.a3") and s["status"] == "succeeded" for s in done["steps"])
    assert sum(s["status"] == "succeeded" and ".discard." in s["name"] for s in done["steps"]) == 1
    tomb = cleanup.lookup(daemon.journal.db, item["resource_id"])[0]
    assert discarded in tomb["completed_phases"]
    assert len(tomb["refused_phases"]) == 2


async def test_e01_completed_worktree_removal_survives_branch_refusal(daemon, mock, monkeypatch):
    doc, wt, _ = await reviewed(daemon, mock)
    branch = next(i for i in doc["items"] if i["kind"] == "local_branch")
    clear = race_consumer(monkeypatch, mock, "remove.branch", "local_branch", wt["path"])
    done = await apply(daemon, doc)
    row = partial(daemon, done, branch, "remove.branch")
    removed = next(p for p in row["completed_phases"] if p["phase"] == "remove.worktree")
    assert removed["resource_id"] == wt["resource_id"] and removed["result"]["removed"]
    assert not Path(wt["path"]).exists()
    assert git(wt["repository"], "rev-parse", "refs/heads/" + wt["branch"]) == wt["observation"]["head"]
    clear()
    done = await resume(daemon, done)
    assert done["status"] == "succeeded", done
    assert any(s["name"].endswith("remove.branch.a2") and s["status"] == "succeeded" for s in done["steps"])
    assert sum(s["status"] == "succeeded" and ".remove.worktree." in s["name"] for s in done["steps"]) == 1
    assert removed in cleanup.lookup(daemon.journal.db, branch["resource_id"])[0]["completed_phases"]


async def test_e01_completed_stop_survives_first_git_refusal(daemon, mock, monkeypatch):
    doc, item, _ = await reviewed(daemon, mock)
    clear = race_consumer(monkeypatch, mock, "preserve", "worktree", item["path"])
    done = await apply(daemon, doc)
    row = partial(daemon, done, item, "preserve")
    assert [p["phase"] for p in row["completed_phases"]] == ["stop"]
    assert row["completed_phases"][0]["result"]["stopped"]
    stops = sum(i["channel"] == "claude:stop-session" for i in mock.invokes)
    clear()
    done = await resume(daemon, done)
    assert done["status"] == "succeeded", done
    assert sum(i["channel"] == "claude:stop-session" for i in mock.invokes) == stops
    assert any(s["name"].endswith("preserve.a2") for s in done["steps"])


async def test_e01_retry_uses_recorded_post_discard_state(daemon, mock, monkeypatch):
    doc, item, _ = await reviewed(daemon, mock, dirty=True)
    clear = race_consumer(monkeypatch, mock, "remove.worktree", "worktree", item["path"])
    done = await apply(daemon, doc, DISCARDER)
    row = partial(daemon, done, item, "remove.worktree")
    clear()
    unexpected = Path(item["path"]) / "later.txt"
    unexpected.write_text("new content after reviewed discard")
    done = await resume(daemon, done)
    again = partial(daemon, done, item, "remove.worktree")
    assert again["error"]["refused_code"] == "PREVIEW_STALE"
    assert again["completed_phases"] == row["completed_phases"]
    assert unexpected.read_text() == "new content after reviewed discard"
    assert not any(s["name"].endswith("remove.worktree.a2") for s in done["steps"])


@pytest.mark.parametrize("when", ["between_phases", "needs_attention"])
async def test_e01_cancel_partial_keeps_completed_phases_and_guard(daemon, mock, monkeypatch, when):
    doc, item, _ = await reviewed(daemon, mock, dirty=True)
    if when == "needs_attention":
        race_consumer(monkeypatch, mock, "remove.worktree", "worktree", item["path"])
    else:
        original = daemon.ops.context["git_runner"]

        class CancelAfterDiscard(LocalRunner):
            async def run(self, host, script, timeout_s=None):
                out = await original.run(host, script, timeout_s)
                req = json.loads(base64.b64decode(shlex.split(script)[-1]))
                if req.get("phase") == "discard":
                    pending = daemon.journal.db.execute("SELECT operation_id FROM cleanup_runs").fetchone()[0]
                    daemon.ops.cancel(CLEANER, pending)
                return out

        daemon.ops.context["git_runner"] = CancelAfterDiscard()
    done = await apply(daemon, doc, DISCARDER)
    if when == "needs_attention":
        partial(daemon, done, item, "remove.worktree")
        done = daemon.ops.cancel(CLEANER, done["operation_id"])
    assert done["status"] == "cancelled", done
    row = next(r for r in cleanup.receipts(daemon.ops, done["operation_id"]) if r["resource_id"] == item["resource_id"])
    assert row["status"] == "uncertain" and row["cancel_requested"]
    assert row["error"]["code"] == "CLEANUP_PARTIAL_STATE"
    assert any(p["phase"] == "discard" for p in row["completed_phases"])
    from bat_agent_connector.api_v1 import ApiV1
    status, body = await ApiV1(daemon).operation(done["operation_id"], principal=CLEANER)
    assert status == 200
    live = next(r for r in body["cleanup_receipts"] if r["resource_id"] == item["resource_id"])
    assert live["cancel_requested"] and live["completed_phases"] == row["completed_phases"]
    assert not (Path(item["path"]) / "discarded.txt").exists()
    with pytest.raises(ResourceReadOnly, match="CLEANUP_IN_PROGRESS"):
        cleanup.guard(item["host"], path=item["path"])


async def test_e01_additive_only_refusal_lists_pins_and_releases_guard(daemon, mock, monkeypatch):
    doc, item, _ = await reviewed(daemon, mock, dirty=True, loaded=False)
    race_consumer(monkeypatch, mock, "discard", "worktree", item["path"])
    done = await apply(daemon, doc, DISCARDER)
    row = next(r for r in cleanup.receipts(daemon.ops, done["operation_id"]) if r["resource_id"] == item["resource_id"])
    assert row["status"] == "blocked_stale" and row["error"]["code"] == "PREVIEW_STALE"
    assert [p["phase"] for p in row["completed_phases"]] == ["preserve"]
    for pin in row["completed_phases"][0]["result"]["pins"]:
        assert git(item["repository"], "rev-parse", pin["ref"]) == pin["sha"]
    assert (Path(item["path"]) / "discarded.txt").exists()
    cleanup.guard(item["host"], path=item["path"])


async def test_e01_additive_only_cancel_records_pins_and_releases_guard(daemon, mock):
    doc, item, _ = await reviewed(daemon, mock, loaded=False)
    original = daemon.ops.context["git_runner"]

    class CancelAfterPreserve(LocalRunner):
        async def run(self, host, script, timeout_s=None):
            out = await original.run(host, script, timeout_s)
            req = json.loads(base64.b64decode(shlex.split(script)[-1]))
            if req.get("phase") == "preserve":
                pending = daemon.journal.db.execute("SELECT operation_id FROM cleanup_runs").fetchone()[0]
                daemon.ops.cancel(CLEANER, pending)
            return out

    daemon.ops.context["git_runner"] = CancelAfterPreserve()
    done = await apply(daemon, doc)
    assert done["status"] == "cancelled", done
    row = next(r for r in cleanup.receipts(daemon.ops, done["operation_id"]) if r["resource_id"] == item["resource_id"])
    assert row["status"] == "cancelled" and row["cancel_requested"] and row["after_state"]["guard_released"]
    assert [p["phase"] for p in row["completed_phases"]] == ["preserve"]
    assert Path(item["path"]).exists()
    for pin in row["completed_phases"][0]["result"]["pins"]:
        assert git(item["repository"], "rev-parse", pin["ref"]) == pin["sha"]
    cleanup.guard(item["host"], path=item["path"])


async def test_e02_shared_worktree_partial_stop_dependency_stays_reserved(daemon, mock, monkeypatch):
    cp, op = await setup_work(daemon, mock)
    sid, path = "shared-idle", op["result"]["worktree_path"]
    registry.reserve("h1", {"session_id": sid, "cwd": path, "worktree_path": path,
        "branch": op["result"]["branch"], "shares_worktree_with": op["result"]["session_id"], "agent_preset": "claude"}, 5)
    registry.update("h1", sid, status="active")
    mock.metas[sid] = {"cwd": path, "isStreaming": False}
    mock.states[sid] = {"isStreaming": False}
    mock.ws_doc["terminals"].append({"id": sid, "cwd": path, "agentPreset": "claude"})
    doc = await cleanup.preview(daemon.ops, CLEANER, {"kind": "checkpoint", "checkpoint_id": cp["checkpoint_id"]})
    sessions = [i for i in doc["items"] if i["kind"] == "session" and i["decision"] == "reclaim"]
    assert len(sessions) == 2
    blocker = sessions[-1]["session_id"]
    item = next(i for i in doc["items"] if i["kind"] == "worktree" and i["proven"])
    original = lifecycle._stop
    blocked = [True]

    async def waiting(fleet, host, current, audit, **kwargs):
        if blocked[0] and current == blocker:
            mock.states[current]["pendingQuestion"] = True
        result = await original(fleet, host, current, audit, **kwargs)
        if result.get("stopped"):
            # This fixture's terminal leaves the workspace; unknown live tabs are covered separately.
            mock.ws_doc["terminals"] = [t for t in mock.ws_doc["terminals"] if t["id"] != current]
        return result

    monkeypatch.setattr(lifecycle, "_stop", waiting)
    done = await apply(daemon, doc)
    row = partial(daemon, done, item, "dependencies")
    assert row["error"]["refused_code"] == "DEPENDENCY_FAILED"
    assert [p["phase"] for p in row["completed_phases"]] == ["stop"]
    blocked[0] = False
    mock.states[blocker].pop("pendingQuestion")
    done = await resume(daemon, done)
    assert done["status"] == "succeeded", (done["status"], done["error_code"], done["reason"],
        [(r["plan"]["kind"], r["status"], r["error"]) for r in cleanup.receipts(daemon.ops, done["operation_id"])])
    assert any(s["name"].endswith("stop.a2") for s in done["steps"])
    assert sum(i["channel"] == "claude:stop-session" for i in mock.invokes) == 2
