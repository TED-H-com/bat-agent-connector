"""Failed standalone starts stay visible to the one reviewed cleanup authority."""
import json
import subprocess
from pathlib import Path

import pytest

from bat_agent_connector import api_auth, cleanup, confinement, registry
from bat_agent_connector.resource_ids import worktree_id
from tests.test_cleanup import apply, git
from tests.test_cleanup import daemon as cleanup_daemon
from tests.test_cleanup import human as cleanup_human
from tests.test_cleanup import known_live_terminals as cleanup_live
from tests.test_session_start_operations import START, create

daemon = cleanup_daemon
human = cleanup_human
known_live_terminals = cleanup_live
CLEANER = api_auth.Principal(START.actor, frozenset({"observe", "cleanup"}))


async def refused_start(daemon, mock, tmp_path, monkeypatch):
    repo = tmp_path / "managed-source"
    repo.mkdir()
    subprocess.run(["git", "init", "--template=", "-q", "-b", "main", str(repo)], check=True)
    git(repo, "config", "user.email", "fixture@example.invalid")
    git(repo, "config", "user.name", "Fixture")
    git(repo, "config", "batc.managed-clone", "true")
    (repo / "file.txt").write_text("original\n")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "initial")
    daemon.fleet.config.host("h1").managed_roots = [str(tmp_path)]
    mock.ws_doc["workspaces"][0]["folderPath"] = str(repo)
    mock.handlers["git:log"] = lambda p: [{"hash": git(p["cwd"], "rev-parse", "HEAD")}]
    mock.handlers["git:branch"] = lambda p: git(p["cwd"], "branch", "--show-current")
    mock.handlers["git:getRoot"] = lambda p: git(p["cwd"], "rev-parse", "--show-toplevel") if Path(p["cwd"]).exists() else None
    def worktree(params):
        path, branch = repo / ".bat-worktrees" / params["sessionId"], "bat/" + params["sessionId"]
        git(repo, "worktree", "add", "-b", branch, str(path), params["baseBranch"])
        result = {"success": True, "worktreePath": str(path), "branchName": branch, "sourceBranch": params["baseBranch"]}
        mock.worktrees[params["sessionId"]] = result
        return result
    async def refused(*args, **kwargs):
        raise confinement.ConfinementRefused("HOST_ACCOUNT_UNVERIFIED", "fixture unsent refusal", sent=False)
    mock.handlers["worktree:create"] = worktree
    monkeypatch.setattr(confinement, "guard_start_frame", refused)
    op = await create(daemon)
    assert op["status"] == "failed", op
    row = registry.get("h1", op["external_refs"]["session_id"])
    assert row["status"] == "failed" and Path(row["worktree_path"]).exists()
    return op, row


async def test_failed_start_carrier_uses_original_id_and_reviewed_cleanup(daemon, mock, tmp_path, monkeypatch):
    op, row = await refused_start(daemon, mock, tmp_path, monkeypatch)
    before = list(daemon.journal.db.iterdump()), registry.registry_path().read_bytes()
    doc = await cleanup.preview(daemon.ops, CLEANER, {"kind": "host", "host": "h1"})
    item = next(i for i in doc["items"] if i["kind"] == "worktree" and i["path"] == row["worktree_path"])
    assert item["resource_id"] == worktree_id("h1", "registry", f'{row["session_id"]}@{row["created_at"]}', "worktree")
    assert item["proven"] and item["decision"] == "reclaim", json.dumps(item, indent=2)
    assert op["operation_id"] in item["original_ids"]
    assert before == (list(daemon.journal.db.iterdump()), registry.registry_path().read_bytes())
    done = await apply(daemon, doc, principal=CLEANER)
    assert done["status"] == "succeeded", done
    assert not Path(row["worktree_path"]).exists()
    assert cleanup.lookup(daemon.journal.db, op["operation_id"])
    assert "worktree:remove" not in mock.channels() and "claude:start-session" not in mock.channels()


@pytest.mark.parametrize("change", ["missing_receipt", "wrong_request", "new_incarnation", "row_missing", "dirty", "unmarked_repository", "missing_workspace", "new_commit", "manual_consumer"])
async def test_failed_start_creation_proof_and_contents_are_not_inferred(daemon, mock, tmp_path, monkeypatch, change):
    op, row = await refused_start(daemon, mock, tmp_path, monkeypatch)
    if change == "manual_consumer":
        mock.ws_doc["terminals"].append({"id": "manual-consumer", "cwd": row["worktree_path"], "type": "claude", "workspaceId": row["workspace_id"]})
        mock.metas["manual-consumer"] = {"cwd": row["worktree_path"], "isStreaming": False}
    elif change == "new_commit":
        (Path(row["worktree_path"]) / "result.txt").write_text("new result")
        git(row["worktree_path"], "add", ".")
        git(row["worktree_path"], "commit", "-qm", "new undelivered result")
    elif change == "missing_workspace":
        saved = daemon.journal.db.execute("SELECT response FROM operation_steps WHERE operation_id=? AND name='source.resolve'", (op["operation_id"],)).fetchone()
        source = json.loads(saved[0])
        source.pop("workspace_id")
        daemon.journal.db.execute("UPDATE operation_steps SET response=? WHERE operation_id=? AND name='source.resolve'", (json.dumps(source), op["operation_id"]))
    elif change == "unmarked_repository":
        git(row["origin_root"], "config", "--unset", "batc.managed-clone")
    elif change == "missing_receipt":
        daemon.journal.db.execute("UPDATE operation_steps SET status='uncertain' WHERE operation_id=? AND name='worktree.create'", (op["operation_id"],))
    elif change == "wrong_request":
        daemon.journal.db.execute("UPDATE operation_steps SET request=? WHERE operation_id=? AND name='worktree.create'", (json.dumps({"session_id": "wrong"}), op["operation_id"]))
    elif change == "new_incarnation":
        registry.update("h1", row["session_id"], created_at=row["created_at"] + 1)
    elif change == "row_missing":
        registry.registry_path().write_text(json.dumps({"sessions": []}))
    else:
        (Path(row["worktree_path"]) / "new-result.txt").write_text("only copy")
    doc = await cleanup.preview(daemon.ops, CLEANER, {"kind": "host", "host": "h1"})
    items = [i for i in doc["items"] if i["kind"] == "worktree" and i["path"] == row["worktree_path"]]
    if change in {"missing_receipt", "wrong_request", "missing_workspace"}:
        assert not items
    else:
        assert len(items) == 1
        assert items[0]["decision"] == ("reclaim" if change == "row_missing" else "retain"), json.dumps(items[0], indent=2)
    assert Path(row["worktree_path"]).exists() and "worktree:remove" not in mock.channels()
