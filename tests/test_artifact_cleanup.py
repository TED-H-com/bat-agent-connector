"""Cleanup projection preparation; real local artifact bytes and Git, mocked BAT."""

from __future__ import annotations

import copy
import json
import os
from pathlib import Path

import pytest

from bat_agent_connector import artifact_cleanup, artifacts
from tests.test_artifacts import (
    LocalArtifactHost,
    continuation,
    make_checkpoint,
    upload,
)
from tests.test_artifacts import daemon as artifact_daemon
from tests.test_artifacts import human as artifact_human

daemon = artifact_daemon
human = artifact_human


async def setup_replica(d):
    d.ops.context["artifact_host"] = LocalArtifactHost()
    ref = await upload(d)
    cp = await make_checkpoint(d)
    op = await continuation(d, cp, [ref])
    assert op["status"] == "succeeded", op
    item = {"kind": "worktree", "flavor": "checkpoint", "proven": True, "host": "h1",
            "path": op["result"]["worktree_path"], "repository": op["external_refs"]["clone_path"],
            "branch": op["external_refs"]["branch"],
            "creation_evidence": {"intent": op["operation_id"], "slot": "worktree",
                                  "intent_type": "checkpoint.continue"}}
    return ref, op, item


async def test_replica_projection_is_exact_and_read_only(daemon, mock):
    ref, op, item = await setup_replica(daemon)
    before = list(daemon.journal.db.iterdump())
    calls = copy.deepcopy(mock.invokes)
    out = artifact_cleanup.replica_evidence(daemon.ops, item)
    prefix = f".batc-inputs/{ref['artifact_id']}-r{ref['revision']}"
    assert out["replica_manifest"] == [{"path": prefix + "/notes.txt", "bytes": len(b"immutable input"),
                                       "digest": ref["digest"]}]
    assert out["bookkeeping_names"] == [f".batc-inputs/.attempts/{ref['artifact_id']}-r1/.attempt-1",
                                        f".batc-inputs/.attempts/{ref['artifact_id']}-r1/.closed-1",
                                        ".batc-inputs/.owner"]
    assert list(daemon.journal.db.iterdump()) == before
    assert mock.invokes == calls
    assert daemon.artifact_store.read_content(ref["artifact_id"], ref["revision"]) == b"immutable input"


@pytest.mark.parametrize("field,value", [
    ("host", "other-host"), ("path", "/unproven/.batc-inputs"), ("repository", "/other-clone"),
    ("branch", "other-branch"), ("flavor", "bat"), ("proven", False), ("kind", "session"),
])
async def test_replica_projection_refuses_mismatched_creation_binding(daemon, field, value):
    _, _, item = await setup_replica(daemon)
    item[field] = value
    assert artifact_cleanup.replica_evidence(daemon.ops, item) == {"replica_manifest": [], "bookkeeping_names": []}


@pytest.mark.parametrize("change", ["unverified", "host", "path", "digest", "evidence", "attempt"])
async def test_replica_projection_requires_verified_exact_materialization(daemon, change):
    _, op, item = await setup_replica(daemon)
    db = daemon.journal.db
    column, value = {"unverified": ("state", "uncertain"), "host": ("host", "other-host"),
                     "path": ("managed_path", item["path"] + "/.batc-inputs/other.txt"),
                     "digest": ("digest", "f" * 64), "evidence": ("evidence", "null"),
                     "attempt": ("attempt", 99)}[change]
    db.execute(f"UPDATE artifact_materializations SET {column}=? WHERE operation_id=?", (value, op["operation_id"]))
    assert artifact_cleanup.replica_evidence(daemon.ops, item) == {"replica_manifest": [], "bookkeeping_names": []}


@pytest.mark.parametrize("failure", ["missing", "corrupt"])
async def test_replica_projection_keeps_last_copy_when_original_is_unavailable(daemon, failure):
    ref, _, item = await setup_replica(daemon)
    original = daemon.artifact_store.content_path(ref["artifact_id"], ref["revision"])
    saved = original.with_name("saved-content")
    original.rename(saved)
    if failure == "corrupt":
        original.write_bytes(b"changed original")
    try:
        assert artifact_cleanup.replica_evidence(daemon.ops, item) == {"replica_manifest": [], "bookkeeping_names": []}
    finally:
        if original.exists():
            original.unlink()
        saved.rename(original)
    assert artifact_cleanup.replica_evidence(daemon.ops, item)["replica_manifest"]


@pytest.mark.parametrize("failure", ["missing", "corrupt", "unchanged"])
async def test_cleanup_resume_rechecks_original_before_removing_accepted_replica(daemon, mock, monkeypatch, failure):
    from bat_agent_connector import api_auth, cleanup
    from bat_agent_connector.operations import OperationError
    from tests.operation_helpers import settle_operations
    from tests.test_artifacts import PERSON
    from tests.test_cleanup import apply

    ref, continuation_op, item = await setup_replica(daemon)
    cleaner = api_auth.Principal(PERSON.actor, frozenset({"observe", "cleanup"}))
    sid = continuation_op["result"]["session_id"]
    mock.metas[sid]["isStreaming"] = False
    for terminal in mock.ws_doc["terminals"]:
        if mock.metas.get(terminal["id"]) is None:
            mock.metas[terminal["id"]] = {"cwd": terminal["cwd"], "isStreaming": False}
    doc = await cleanup.preview(daemon.ops, cleaner, {
        "kind": "checkpoint", "checkpoint_id": continuation_op["target"]["checkpoint_id"]})
    assert doc["ready"], doc
    accepted = next(row for row in doc["items"] if row["kind"] == "worktree")
    assert accepted["replica_evidence"]["replica_manifest"]
    original_call = cleanup._host_call
    remove_attempts = []

    async def hold_remove(ops, host, request, *args, **kwargs):
        if request.get("phase") == "remove.worktree":
            remove_attempts.append(request)
            raise OperationError("PREVIEW_STALE", "test pause after preserving refs", 409)
        return await original_call(ops, host, request, *args, **kwargs)

    monkeypatch.setattr(cleanup, "_host_call", hold_remove)
    paused = await apply(daemon, doc, cleaner)
    assert paused["status"] == "needs_attention", paused
    assert remove_attempts
    assert daemon.journal.db.execute("SELECT 1 FROM operation_steps WHERE operation_id=? "
        "AND name LIKE '%.preserve.%' AND status='succeeded'", (paused["operation_id"],)).fetchone()
    original = daemon.artifact_store.content_path(ref["artifact_id"], ref["revision"])
    saved = original.with_name("saved-content")
    if failure != "unchanged":
        original.rename(saved)
        if failure == "corrupt":
            original.write_bytes(b"corrupt original")
    monkeypatch.setattr(cleanup, "_host_call", original_call)
    try:
        daemon.ops.resume(cleaner, paused["operation_id"])
        await settle_operations(daemon.ops)
        resumed = daemon.ops.get(paused["operation_id"])
        if failure == "unchanged":
            assert resumed["status"] == "succeeded", resumed
            assert not Path(item["path"]).exists()
        else:
            assert resumed["status"] == "needs_attention", resumed
            evidence = accepted["replica_evidence"]["replica_manifest"][0]
            assert (Path(item["path"]) / evidence["path"]).read_bytes() == b"immutable input"
            receipts = cleanup.receipts(daemon.ops, paused["operation_id"])
            assert any(r["error"] and r["error"].get("refused_code", r["error"]["code"]) == "PREVIEW_STALE"
                       for r in receipts), receipts
    finally:
        if failure != "unchanged":
            original.unlink(missing_ok=True)
            saved.rename(original)


async def test_replica_projection_uses_recorded_attempts_without_guessing_gaps(daemon):
    ref, op, item = await setup_replica(daemon)
    db = daemon.journal.db
    row = artifacts.materializations(db, operation_id=op["operation_id"])[0]
    step = db.execute("SELECT name,request FROM operation_steps WHERE operation_id=? AND name LIKE ?",
                      (op["operation_id"], f"artifact.{row['materialization_id']}.transfer.%")).fetchone()
    request = json.loads(step["request"])
    request["attempt"] = 3
    db.execute("UPDATE operation_steps SET name=?,request=? WHERE operation_id=? AND name=?",
               (step["name"].removesuffix("1") + "3", artifacts.canonical(request), op["operation_id"], step["name"]))
    db.execute("UPDATE artifact_materializations SET attempt=3 WHERE materialization_id=?", (row["materialization_id"],))
    out = artifact_cleanup.replica_evidence(daemon.ops, item)
    assert out["replica_manifest"]
    directory = f".batc-inputs/.attempts/{ref['artifact_id']}-r1"
    assert out["bookkeeping_names"] == [directory + "/.attempt-3", directory + "/.closed-3", ".batc-inputs/.owner"]
    request["worktree"] = "/another-worktree"
    db.execute("UPDATE operation_steps SET request=? WHERE operation_id=? AND name=?",
               (artifacts.canonical(request), op["operation_id"], step["name"].removesuffix("1") + "3"))
    assert artifact_cleanup.replica_evidence(daemon.ops, item) == {"replica_manifest": [], "bookkeeping_names": []}


@pytest.mark.parametrize("content", ["unchanged", "edited", "extra", "missing", "symlink", "hardlink", "tracked"])
async def test_replica_projection_drives_cleanup_content_contract(daemon, content):
    # These checks become normal tests once the reviewed cleanup base is merged.
    host = pytest.importorskip("bat_agent_connector.cleanup_host", reason="awaiting shared cleanup base")
    _, op, item = await setup_replica(daemon)
    evidence = artifact_cleanup.replica_evidence(daemon.ops, item)
    entry = evidence["replica_manifest"][0]
    wt = Path(item["path"])
    replica = wt / entry["path"]
    tracked = set()
    if content == "edited":
        replica.chmod(0o600)
        replica.write_bytes(b"new unique work")
    elif content == "extra":
        (replica.parent / "extra.txt").write_bytes(b"unique extra")
    elif content == "missing":
        replica.unlink()
    elif content == "symlink":
        replica.unlink()
        replica.symlink_to(wt / "notes.txt")
    elif content == "hardlink":
        os.link(replica, wt / "second-link.txt")
    elif content == "tracked":
        tracked.add(entry["path"])
    facts = [fact for fact in host.manifest(str(wt)) if fact["path"].startswith(".batc-inputs")]
    ordinary, replicas, books, missing = host.replica_content(facts, evidence, tracked, [])
    if content == "unchanged":
        assert ordinary == [] and missing == []
        assert [fact["path"] for fact in replicas] == [entry["path"]]
        assert {fact["path"] for fact in books} <= set(evidence["bookkeeping_names"])
    else:
        assert ordinary, content
        assert content == "extra" or not replicas
        assert missing == ([entry["path"]] if content == "missing" else [])
