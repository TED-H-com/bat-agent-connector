"""E01/E02/plan §23: capacity projection never changes cleanup's external outcome."""
from __future__ import annotations

import copy
import json
from contextlib import contextmanager
from pathlib import Path

import pytest

from bat_agent_connector import api_auth, cleanup, controller_authority, lifecycle, registry, task_control
from bat_agent_connector.errors import ResourceReadOnly
from bat_agent_connector.operations import OpContext
from tests.operation_helpers import settle_operations
from tests.test_cleanup import setup_work
from tests.test_cleanup_capacity import (  # noqa: F401 - real Git/MockBat fixtures
    CLEANER,
    add_receipt,
    apply,
    close_clients,
    git,
    known_live_terminals,
    planner,
    standalone_worktree,
)
from tests.test_cleanup_capacity import daemon as capacity_daemon
from tests.test_cleanup_capacity import human as capacity_human
from tests.test_cleanup_unstarted import SimulatedCrash

daemon = capacity_daemon
human = capacity_human


def row_edit(sid, *, remove=False, **fields):
    path = registry.registry_path()
    with registry._locked(path):
        document = json.loads(path.read_text())
        if remove:
            document["sessions"] = [e for e in document["sessions"] if e["session_id"] != sid]
        else:
            next(e for e in document["sessions"] if e["session_id"] == sid).update(fields)
        registry._write_document(path, document)


async def absent_checkpoint(d, mock, *, release=False):
    cp, op = await setup_work(d, mock)
    mock.metas[op["result"]["session_id"]] = None
    if release:
        path = Path(op["result"]["worktree_path"])
        (path / "undelivered.txt").write_text("keep branch after reclaim")
        git(path, "add", ".")
        git(path, "-c", "user.name=test", "-c", "user.email=test@example.invalid", "commit", "-qm", "result")
    doc = await cleanup.preview(d.ops, CLEANER, {"kind": "checkpoint", "checkpoint_id": cp["checkpoint_id"]})
    if release:
        rid = next(i["resource_id"] for i in doc["items"] if i["kind"] == "worktree")
        doc = await cleanup.preview(d.ops, CLEANER, doc["target"], {"release_undelivered": [rid]})
    session = next(i for i in doc["items"] if i["kind"] == "session")
    worktree = next(i for i in doc["items"] if i["kind"] == "worktree")
    assert session["decision"] == "already_absent" and worktree["decision"] == "reclaim"
    return doc, session, worktree


def succeeded_worktree(d, done, item):
    assert done["status"] == "succeeded", done
    rows = {r["resource_id"]: r for r in cleanup.receipts(d.ops, done["operation_id"])}
    assert rows[item["resource_id"]]["status"] == "succeeded"
    assert rows[item["resource_id"]]["error"] is None
    assert cleanup.lookup(d.journal.db, item["resource_id"])
    assert json.loads(registry.registry_path().read_text())["cleanup_guards"][item["resource_id"]]["status"] == "cleaned"
    assert not Path(item["path"]).exists()
    return rows


async def test_e01_host_cleanup_old_rows_never_fail_capacity_projection(daemon, mock, human, tmp_path):
    repo, active = await standalone_worktree(daemon, mock, human, tmp_path)
    sid = active["session_id"]
    mock.metas[sid] = None
    head = git(repo, "rev-parse", "HEAD")
    add_receipt(daemon, {"observation": {"head": head}}, source_kind="session", source_id=sid)
    old = {}
    path = registry.registry_path()
    with registry._locked(path):
        document = json.loads(path.read_text())
        for status in ("cleaned", "failed", "superseded", "removed", "uncertain", "starting", "legacy_status"):
            name = "historical-" + status
            entry = {"host": "h1", "session_id": name, "created_at": 1.5, "status": status,
                     "cwd": str(human if status == "failed" else repo), "agent_preset": "claude"}
            if status in {"cleaned", "uncertain", "starting"}:
                worktree = repo / ".bat-worktrees" / name
                branch = "bat/worktree-" + name
                git(repo, "branch", branch, head)
                if status != "cleaned":
                    git(repo, "worktree", "add", "-q", str(worktree), branch)
                entry.update(cwd=str(worktree), worktree_path=str(worktree), origin_root=str(repo),
                             branch=branch, base_commit=head)
            document["sessions"].append(entry)
            mock.metas[name] = None
            old[name] = copy.deepcopy(entry)
        registry._write_document(path, document)
    doc = await cleanup.preview(daemon.ops, CLEANER, {"kind": "host", "host": "h1"})
    sessions = {i["session_id"]: i for i in doc["items"] if i["kind"] == "session"}
    for status in ("starting", "uncertain"):
        item = sessions["historical-" + status]
        assert item["decision"] == "retain" and "COMMAND_UNRESOLVED" in {r["code"] for r in item["reasons"]}
        carrier = next(i for i in doc["items"] if i["resource_id"] == item["worktree_id"])
        assert carrier["decision"] == "retain" and "COMMAND_UNRESOLVED" in {r["code"] for r in carrier["reasons"]}
    done = await apply(daemon, doc)
    active_wt = next(i for i in doc["items"] if i.get("path") == active["worktree_path"] and i["kind"] == "worktree")
    rows = succeeded_worktree(daemon, done, active_wt)
    assert registry.get("h1", sid)["status"] == "absent_at_cleanup"
    assert rows[sessions[sid]["resource_id"]]["after_state"]["capacity_released"]
    for name, original in old.items():
        assert registry.get("h1", name) == original
        after = rows[sessions[name]["resource_id"]]["after_state"]
        assert after["capacity_released"] is False and after["registry_status"] == original["status"]
        assert after["capacity_reason"] == ("start_unsettled" if original["status"] == "starting" else "not_counted")
    assert not any(r["status"] == "uncertain" or r["error"] for r in rows.values())


@pytest.mark.parametrize("failure", ["read", "write"])
async def test_e01_capacity_registry_io_failure_records_bookkeeping_without_changing_outcome(
        daemon, mock, monkeypatch, failure):
    doc, session, wt = await absent_checkpoint(daemon, mock)
    retire = registry.retire
    writes = registry._write
    class UnreadableRegistry:
        def read_text(self):
            raise OSError("retirement read unavailable")
    def fail_write(*args, **kwargs):
        raise OSError("retirement write unavailable")
    def failed(*args, **kwargs):
        with monkeypatch.context() as patch:
            if failure == "read":
                patch.setattr(registry, "registry_path", lambda: UnreadableRegistry())
                from contextlib import nullcontext
                patch.setattr(registry, "_locked", lambda path: nullcontext())
            else:
                patch.setattr(registry, "_write", fail_write)
            return retire(*args, **kwargs)
    monkeypatch.setattr(registry, "retire", failed)
    done = await apply(daemon, doc)
    rows = succeeded_worktree(daemon, done, wt)
    assert rows[session["resource_id"]]["after_state"]["capacity_reason"] == "registry_io_failed"
    assert registry.get("h1", session["session_id"])["status"] == "active"
    assert registry._write is writes


@pytest.mark.parametrize("change", ["generation", "missing", "task", "starting"])
async def test_e01_capacity_changes_after_validation_do_not_degrade_reclaimed_worktree(daemon, mock, monkeypatch, change):
    # Keep the branch so this exercises bookkeeping after the last host mutation, independently
    # of the next mutation's ownership/consumer checks (which still refuse newly unsettled starts).
    doc, session, wt = await absent_checkpoint(daemon, mock, release=True)
    finalize = cleanup._finalize

    def changed(ctx, item, after):
        if item["resource_id"] == wt["resource_id"]:
            fields = {"created_at": session["registry"]["created_at"] + 1} if change == "generation" else \
                     {"task_id": "task-new-owner"} if change == "task" else {"status": "starting"} if change == "starting" else {}
            row_edit(session["session_id"], remove=change == "missing", **fields)
        return finalize(ctx, item, after)

    monkeypatch.setattr(cleanup, "_finalize", changed)
    done = await apply(daemon, doc)
    rows = succeeded_worktree(daemon, done, wt)
    receipt = rows[session["resource_id"]]
    reason = "task_owned" if change == "task" else "start_unsettled" if change == "starting" else "generation_changed"
    assert receipt["status"] == "already_absent" and receipt["error"] is None
    assert receipt["after_state"]["capacity_released"] is False
    assert receipt["after_state"]["capacity_reason"] == reason
    assert not any(r["status"] == "uncertain" or r["error"] for r in rows.values())


@pytest.mark.parametrize("error", ["refused", "duplicate", "io"])
@pytest.mark.parametrize("at_finalize", [False, True])
async def test_e01_capacity_registry_failure_never_fails_a_cleanup_run(daemon, mock, monkeypatch, error, at_finalize):
    doc, session, wt = await absent_checkpoint(daemon, mock)
    if not at_finalize:
        git(wt["repository"], "worktree", "remove", wt["path"])
        doc = await cleanup.preview(daemon.ops, CLEANER, doc["target"])
    def refused(*args, **kwargs):
        if error == "duplicate":
            raise registry.RegistryInvariantError("duplicate session identity")
        raise ResourceReadOnly("BINDING_MISMATCH", "registry refused retirement") if error == "refused" else OSError("registry unavailable")
    monkeypatch.setattr(registry, "retire", refused)
    done = await apply(daemon, doc)
    assert done["status"] == "succeeded", done
    if at_finalize:
        succeeded_worktree(daemon, done, wt)
    row = next(r for r in done["result"]["items"] if r["resource_id"] == session["resource_id"])
    assert row["status"] == "already_absent" and row["error"] is None
    assert row["after_state"]["capacity_reason"] == ("registry_io_failed" if error == "io" else "registry_refused")
    assert row["after_state"]["capacity_error"]["code"] == {
        "refused": "BINDING_MISMATCH", "duplicate": "REGISTRY_DUPLICATE_SESSION", "io": "REGISTRY_IO_FAILED"}[error]
    assert registry.get("h1", session["session_id"])["status"] == "active"


async def test_e01_capacity_owner_lookup_refusal_preserves_absence_receipt(daemon, mock, monkeypatch):
    doc, session, wt = await absent_checkpoint(daemon, mock)
    git(wt["repository"], "worktree", "remove", wt["path"])
    doc = await cleanup.preview(daemon.ops, CLEANER, doc["target"])
    original = cleanup._retire_absent_sessions
    def refused(*args, **kwargs):
        raise registry.RegistryInvariantError("duplicate session identity")
    def project(ctx, carrier_id=None):
        with monkeypatch.context() as patch:
            patch.setattr(task_control, "owner_task", refused)
            return original(ctx, carrier_id)
    monkeypatch.setattr(cleanup, "_retire_absent_sessions", project)
    done = await apply(daemon, doc)
    assert done["status"] == "succeeded", done
    row = next(r for r in done["result"]["items"] if r["resource_id"] == session["resource_id"])
    assert row["status"] == "already_absent" and row["error"] is None
    assert row["after_state"]["capacity_reason"] == "registry_refused"
    assert row["after_state"]["capacity_error"]["code"] == "REGISTRY_DUPLICATE_SESSION"
    assert registry.get("h1", session["session_id"])["status"] == "active"


async def test_e01_duplicate_registry_after_planner_stop_does_not_hide_confirmed_stop(daemon, mock, human, tmp_path, monkeypatch):
    _, start = await planner(daemon, mock, human, tmp_path)
    retire, damaged = registry.retire, []
    def duplicate(*args, **kwargs):
        path = registry.registry_path()
        document = json.loads(path.read_text())
        row = next(e for e in document["sessions"] if e["session_id"] == start["session_id"])
        document["sessions"].append(copy.deepcopy(row))  # Simulate an incompatible writer at retirement.
        path.write_text(json.dumps(document))
        damaged.append(path.read_bytes())
        return retire(*args, **kwargs)
    monkeypatch.setattr(registry, "retire", duplicate)
    result = await lifecycle.fanout_from_plan(daemon.fleet, "h1", start["session_id"], confirm=True)
    kept = result["planner_cleanup"]
    assert kept["stopped"] is True and kept["capacity_released"] is False
    assert kept["capacity_reason"] == "registry_refused"
    assert kept["capacity_error"]["code"] == "REGISTRY_DUPLICATE_SESSION"
    assert registry.registry_path().read_bytes() == damaged[0]
    assert mock.metas[start["session_id"]] is None
    assert sum(i["channel"] == "claude:stop-session" for i in mock.invokes) == 1


@pytest.mark.parametrize("crash", ["before", "after"])
async def test_e01_capacity_retirement_replays_once_after_finalize_crash(daemon, mock, monkeypatch, crash):
    doc, session, wt = await absent_checkpoint(daemon, mock)
    op, _ = daemon.ops.create(CLEANER, **cleanup.apply_request(doc, "capacity-crash"))
    op = daemon.ops._transition(op["operation_id"], "running")
    retire_absent, write = cleanup._retire_absent_sessions, registry._write
    writes = []
    def counted_write(path, items):
        writes.append(copy.deepcopy(next(e for e in items if e["session_id"] == session["session_id"])))
        return write(path, items)
    monkeypatch.setattr(registry, "_write", counted_write)
    def interrupted(ctx, carrier_id=None):
        if carrier_id == wt["resource_id"]:
            if crash == "after":
                retire_absent(ctx, carrier_id)
            raise SimulatedCrash()
        return retire_absent(ctx, carrier_id)
    with monkeypatch.context() as patch:
        patch.setattr(cleanup, "_retire_absent_sessions", interrupted)
        with pytest.raises(SimulatedCrash):
            await cleanup._run(OpContext(daemon.ops, op))
    receipt = next(r for r in cleanup.receipts(daemon.ops, op["operation_id"]) if r["resource_id"] == wt["resource_id"])
    assert receipt["status"] == "succeeded" and cleanup.lookup(daemon.journal.db, wt["resource_id"])
    await settle_operations(daemon.ops, timeout=60)
    done = daemon.ops.get(op["operation_id"])
    succeeded_worktree(daemon, done, wt)
    assert len(writes) == 1 and writes[0]["status"] == "absent_at_cleanup"
    row = registry.get("h1", session["session_id"])
    await cleanup._run(OpContext(daemon.ops, done))  # Idempotent resumed handler replay; completed API operations are not resumable.
    assert len(writes) == 1 and registry.get("h1", session["session_id"]) == row


@pytest.mark.parametrize("refusal", ["generation", "refused", "io"])
async def test_e01_confirmed_planner_stop_reports_capacity_refusal_honestly(daemon, mock, human, tmp_path, monkeypatch, refusal):
    _, start = await planner(daemon, mock, human, tmp_path)
    original = registry.get("h1", start["session_id"])
    retire = registry.retire
    def refused(*args, **kwargs):
        if refusal == "generation":
            row_edit(start["session_id"], created_at=original["created_at"] + 1)
            return retire(*args, **kwargs)
        raise ResourceReadOnly("BINDING_MISMATCH", "registry refused") if refusal == "refused" else OSError("registry unavailable")
    monkeypatch.setattr(registry, "retire", refused)
    result = await lifecycle.fanout_from_plan(daemon.fleet, "h1", start["session_id"], confirm=True)
    kept = result["planner_cleanup"]
    assert kept["stopped"] is True and kept["capacity_released"] is False
    assert kept["capacity_reason"] == {"generation": "generation_changed", "refused": "registry_refused", "io": "registry_io_failed"}[refusal]
    assert mock.metas[start["session_id"]] is None
    assert registry.get("h1", start["session_id"])["status"] == "active"
    assert sum(i["channel"] == "claude:stop-session" for i in mock.invokes) == 1


@pytest.mark.parametrize("status", ["superseded", "removed", "cleaned", "failed", "uncertain", "unknown", "starting", "task"])
def test_e01_capacity_retirement_only_changes_counted_set(monkeypatch, tmp_path, status):
    monkeypatch.setattr(registry, "registry_path", lambda: tmp_path / "registry.json")
    entry = {"host": "h1", "session_id": "capacity-history", "created_at": 1.5,
             "status": "active" if status == "task" else status}
    if status == "task":
        entry["task_id"] = "task-owner"
    registry.registry_path().write_text(json.dumps({"sessions": [entry]}))
    before = registry.registry_path().read_bytes()
    result = registry.retire("h1", entry["session_id"], "absent_at_cleanup", created_at=1.5, actor="person", reason="absent")
    assert registry.registry_path().read_bytes() == before
    assert result == {"capacity_released": False, "registry_status": entry["status"], "capacity_reason":
                      "task_owned" if status == "task" else "start_unsettled" if status == "starting" else "not_counted"}


@pytest.mark.parametrize("replacement", ["nonce", "controller", "legacy"])
@pytest.mark.parametrize("fence", ["incarnation", "expected"])
@pytest.mark.parametrize("settled", [False, True])
def test_capacity_retirement_checks_original_controller_inside_registry_lock(controller_context, monkeypatch, replacement, fence, settled):
    journal, _ = controller_context
    registry.reserve("h1", {"session_id": "retirement-race"}, 10)
    registry.update("h1", "retirement-race", status="active")
    original = registry.get("h1", "retirement-race")
    foreign = controller_authority.binding(journal.db, api_auth.Principal("other-controller", frozenset({"cleanup"})))
    locked, replacement_bytes = registry._locked, []

    @contextmanager
    def replace_before_locked_read(path):
        with locked(path):
            document = json.loads(path.read_text())
            row = next(row for row in document["sessions"] if row["session_id"] == "retirement-race")
            if replacement == "nonce":
                row["controller_incarnation"] = "f" * 32
            elif replacement == "controller":
                row["controller_authority"] = foreign
            else:
                row.pop("controller_authority")
            if settled:
                row.update(status="absent_at_cleanup", retirement={"actor": "test-operator", "reason": "confirmed absence",
                    "operation_id": None, "carrier_resource_id": None})
            registry._write_document(path, document)
            replacement_bytes.append(path.read_bytes())
            yield

    # Ownership was valid before the lock; a concurrent replacement must win.
    controller_authority.require_entry(original)
    monkeypatch.setattr(registry, "_locked", replace_before_locked_read)
    kwargs = {"controller_incarnation": original["controller_incarnation"]} if fence == "incarnation" else {"expected": original}
    def retire():
        return registry.retire("h1", "retirement-race", "absent_at_cleanup", created_at=original["created_at"],
                               actor="test-operator", reason="confirmed absence", **kwargs)
    if replacement == "nonce":
        result = retire()
        assert result == {"capacity_released": False, "registry_status": "absent_at_cleanup" if settled else "active",
                          "capacity_reason": "generation_changed"}
    else:
        code = "FOREIGN_CONTROLLER" if replacement == "controller" else "CONTROLLER_AUTHORITY_REQUIRED"
        with pytest.raises(ResourceReadOnly, match=code):
            retire()
    assert len(replacement_bytes) == 1
    assert registry.registry_path().read_bytes() == replacement_bytes[0]
