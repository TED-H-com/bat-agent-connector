"""Fixed PM-only Git revisions, exact receipt recovery and branch/index boundaries."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from bat_agent_connector.operations import AmbiguousOutcome
from tests.test_checkpoints import git, snapshot
from tests.test_pm_worktree import pm as pm_fixture
from tests.test_pm_worktree import resume, submit
from tests.test_repository_sync import world as repository_world

world = repository_world
pm = pm_fixture


def revision(pm, number="1"):
    revision_id = "pmrev_" + number * 32
    prefix = "docs/pm/revisions/" + revision_id + "/"
    files = {prefix + "plan.md": "# Adopted direction candidate\n\nSources remain attributed.\n",
             prefix + "plan.json": '{"status":"draft","scope":"selected"}\n'}
    digests = {path: hashlib.sha256(value.encode()).hexdigest() for path, value in files.items()}
    document_digest = hashlib.sha256(json.dumps(digests, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()
    return {"workspace_id": pm["workspace_id"], "revision_id": revision_id, "files": files,
            "file_digests": digests, "document_digest": document_digest}


async def test_commits_only_fixed_revision_files_and_preserves_drafts_and_source(pm):
    before = snapshot(pm["human"])
    data = revision(pm)
    done = await submit(pm, revision=data)
    assert done["status"] == "succeeded", (done["error_code"], done["status_reason"], done["steps"])
    receipt = done["result"]["revision"]
    path = Path(pm["plan"]["worktree_path"])
    assert receipt["status"] == "verified" and receipt["document_digest"] == data["document_digest"]
    assert git(path, "rev-parse", "HEAD") == receipt["commit_sha"]
    assert git(path, "rev-parse", "HEAD^") == pm["sha"]
    assert set(git(path, "diff-tree", "--no-commit-id", "--name-only", "-r", "HEAD").splitlines()) == set(data["files"])
    assert not git(path, "diff", "HEAD")
    assert git(path, "status", "--porcelain").startswith("?? docs/pm/drafts/")
    assert Path(path, done["result"]["snapshot"]["path"]).exists()
    assert snapshot(pm["human"]) == before
    for relative, value in data["files"].items():
        assert (path / relative).read_text() == value


async def test_next_turn_reads_verified_pm_head_and_second_revision_parent(pm):
    first = await submit(pm, revision=revision(pm))
    assert first["status"] == "succeeded", (first["error_code"], first["status_reason"])
    head = first["result"]["revision"]["commit_sha"]
    second = await submit(pm, revision=revision(pm, "2"), expected_parent=head)
    assert second["status"] == "succeeded", (second["error_code"], second["status_reason"])
    assert git(pm["plan"]["worktree_path"], "rev-parse", "HEAD^") == head
    assert pm["plan"]["source_sha"] == pm["sha"]
    follow = await submit(pm)
    assert follow["status"] == "succeeded", (follow["error_code"], follow["status_reason"])
    assert follow["result"]["carrier"]["head"] == second["result"]["revision"]["commit_sha"]


async def test_lost_commit_ack_reads_exact_operation_commit_without_duplicate(pm):
    def lose(_):
        raise AmbiguousOutcome("lost commit reply")
    pm["runner"].after["pm-revision"] = lose
    first = await submit(pm, revision=revision(pm))
    assert first["status"] == "uncertain", first
    head = git(pm["plan"]["worktree_path"], "rev-parse", "HEAD")
    recovered = await resume(pm, first)
    assert recovered["status"] == "succeeded", (recovered["error_code"], recovered["status_reason"])
    assert recovered["result"]["revision"]["commit_sha"] == head
    assert git(pm["plan"]["worktree_path"], "rev-list", "--count", pm["sha"] + "..HEAD") == "1"
    assert pm["runner"].phases.count("pm-revision") == 1


@pytest.mark.parametrize("change", ["staged_code", "worktree_code", "foreign_head", "index_flags", "unknown_file"])
async def test_commit_refuses_unrelated_or_hidden_changes_at_frame(pm, change):
    def drift(request):
        path = Path(request["worktree_path"])
        if change == "staged_code":
            (path / "hello.txt").write_text("unrelated")
            git(path, "add", "hello.txt")
        elif change == "worktree_code":
            (path / "hello.txt").write_text("unrelated")
        elif change == "foreign_head":
            tree = git(path, "rev-parse", "HEAD^{tree}")
            new = git(path, "-c", "user.name=fixture", "-c", "user.email=fixture@example.invalid", "commit-tree", tree, "-p", "HEAD", "-m", "foreign")
            git(path, "update-ref", "refs/heads/" + request["branch"], new)
        elif change == "index_flags":
            git(path, "update-index", "--assume-unchanged", "hello.txt")
            (path / "hello.txt").write_text("hidden unrelated change")
        else:
            (path / "unrelated.txt").write_text("foreign")
    pm["runner"].before["pm-revision"] = drift
    done = await submit(pm, revision=revision(pm))
    assert done["status"] == "needs_attention", done
    assert not list(Path(pm["plan"]["worktree_path"]).glob("docs/pm/revisions/*/plan.json"))


async def test_stale_parent_does_not_replace_an_existing_pm_commit(pm):
    first = await submit(pm, revision=revision(pm))
    assert first["status"] == "succeeded", first
    head = first["result"]["revision"]["commit_sha"]
    second = await submit(pm, revision=revision(pm, "2"), expected_parent=pm["sha"])
    assert second["status"] == "needs_attention" and second["error_code"] == "PM_REVISION_PARENT_CHANGED", second
    assert git(pm["plan"]["worktree_path"], "rev-parse", "HEAD") == head


@pytest.mark.parametrize("change", ["path", "digest", "bytes", "workspace"])
async def test_revision_request_cannot_expand_scope_or_change_fixed_bytes(pm, change):
    data = revision(pm)
    if change == "path":
        data["files"]["README.md"] = "overwrite"
    elif change == "digest":
        data["document_digest"] = "0" * 64
    elif change == "bytes":
        data["files"][next(iter(data["files"]))] += "new bytes"
    else:
        data["workspace_id"] = "ws_" + "f" * 32
    done = await submit(pm, revision=data)
    assert done["status"] == "failed" and done["error_code"] == "PM_REVISION_INVALID", done
    assert "pm-revision" not in pm["runner"].phases


@pytest.mark.parametrize("point", ["intent", "objects", "files", "branch", "index"])
async def test_interrupted_original_transaction_resumes_same_deterministic_commit(pm, monkeypatch, point):
    from bat_agent_connector import pm_worktree
    source = pm_worktree._host_source
    armed = {"value": True}
    anchors = {
        "intent": "    for kind, sha, content in objects:\n",
        "objects": "    for relative, content in files.items():\n",
        "files": '    if git(req["clone_path"], "rev-parse", "--verify", rev_ref(proposed), optional=True) is None:\n',
        "branch": "    # Only this proven PM-owned index is synchronized; objects were built without\n",
        "index": '    proposed["index_after"] = rev_index(req)\n',
    }
    def interrupted_source():
        script = source()
        if armed["value"]:
            script = script.replace(anchors[point], '    raise ValueError("PM_HOST_UNCERTAIN")\n' + anchors[point])
        return script
    monkeypatch.setattr(pm_worktree, "_host_source", interrupted_source)
    pm["runner"].before["pm-revision"] = lambda _: armed.update(value=False)
    first = await submit(pm, revision=revision(pm))
    assert first["status"] == "uncertain", (first["status"], first["error_code"], first["status_reason"])
    manifest = json.loads(Path(pm["plan"]["clone_path"], ".git", "batc-pm-revisions.json").read_text())
    expected = manifest["records"][0]["receipt"]["commit_sha"]
    recovered = await resume(pm, first)
    assert recovered["status"] == "succeeded", (recovered["error_code"], recovered["status_reason"])
    assert recovered["result"]["revision"]["commit_sha"] == expected
    assert git(pm["plan"]["worktree_path"], "rev-list", "--count", pm["sha"] + "..HEAD") == "1"
    assert pm["runner"].phases.count("pm-revision") == 2


async def test_interrupted_transaction_never_repairs_foreign_index_or_code(pm, monkeypatch):
    from bat_agent_connector import pm_worktree
    source = pm_worktree._host_source
    armed = {"value": True}
    def interrupted_source():
        script = source()
        if armed["value"]:
            script = script.replace('    for kind, sha, content in objects:\n',
                '    raise ValueError("PM_HOST_UNCERTAIN")\n    for kind, sha, content in objects:\n')
        return script
    monkeypatch.setattr(pm_worktree, "_host_source", interrupted_source)
    pm["runner"].before["pm-revision"] = lambda _: armed.update(value=False)
    first = await submit(pm, revision=revision(pm))
    assert first["status"] == "uncertain", first
    path = Path(pm["plan"]["worktree_path"])
    (path / "hello.txt").write_text("unrelated later work")
    git(path, "add", "hello.txt")
    index = git(path, "diff", "--cached")
    recovered = await resume(pm, first)
    assert recovered["status"] == "needs_attention", recovered
    assert git(path, "diff", "--cached") == index
    assert git(path, "rev-parse", "HEAD") == pm["sha"]
    assert pm["runner"].phases.count("pm-revision") == 1
