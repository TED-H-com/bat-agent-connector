"""A selected Attempt and its fixed Result survive a newer Work start."""
from __future__ import annotations

from tests.test_api_v1 import http
from tests.test_checkpoints import LocalRunner, git, snapshot
from tests.test_integration import commit
from tests.test_pm_work_attempt_result import (
    _result,
    _set_turn_messages,
    _start_managed_attempt,
    pm,
    ready,
    start_ready,
    world,
)

_ = (world, pm, ready, start_ready)


async def test_new_attempt_refuses_original_selection_without_replacing_its_result(start_ready, mock):
    w = start_ready
    ops = w["d"].ops
    ops.context["git_runner"] = LocalRunner()
    manual_before = snapshot(w["human"])
    selected = await _start_managed_attempt(w, mock, key="selected-attempt")
    worktree = selected["worktree_path"]
    git(worktree, "config", "user.name", "fixture")
    git(worktree, "config", "user.email", "fixture@example.invalid")
    candidate = commit(worktree, "selected.txt", "Original selected result\n", "Complete selected work")
    # Only BAT's external observation is supplied; Git and the Result collector
    # run for real, and no operation, Attempt or Result row is manufactured.
    mock.commit_files[candidate] = [{"file": "selected.txt", "status": "A"}]
    mock.commit_diffs[candidate, "selected.txt"] = git(worktree, "show", "--format=", candidate, "--", "selected.txt")
    _set_turn_messages(mock, selected["session_id"], selected["turn_marker"], streaming=False)
    await w["d"].inventory._refresh_scheduled(selected["host"])
    original = await _result(w, selected)
    assert original["changes"]["fixed_result_commit"]["commit_sha"] == candidate
    assert original["identity"]["attempt_id"] == selected["attempt_id"]
    assert original["results"]

    successor = await _start_managed_attempt(w, mock, key="successor-attempt")
    assert successor["attempt_id"] != selected["attempt_id"]
    assert successor["work_item_id"] == selected["work_item_id"]
    prefix = f"/api/v1/pm/workspaces/{selected['workspace_id']}"
    status, attempts = await http(w["port"], "GET",
        prefix + f"/work/{selected['work_item_id']}/attempts", tok=w["owner_token"])
    assert status == 200, attempts
    assert [row["attempt_id"] for row in attempts["attempts"]] == [
        selected["attempt_id"], successor["attempt_id"],
    ]
    assert attempts["attempts"][-1]["predecessor_attempt_id"] == selected["attempt_id"]

    counts = {table: ops.db.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
              for table in ("operations", "work_inputs", "work_attempts", "session_results")}
    frames = list(mock.invokes)
    status, refused = await http(w["port"], "POST", "/api/v1/operations", tok=w["owner_token"],
        headers={"Idempotency-Key": "original-selection-next-input"}, body={
            "action": "session.send",
            "target": {"workspace_id": selected["workspace_id"], "work_item_id": selected["work_item_id"]},
            "params": {"text": "Continue the selected result, not its successor", "schedule": "after_current"},
            "preconditions": {"expected_attempt_id": selected["attempt_id"]},
        })
    assert status == 409, refused
    assert refused["error"]["code"] == "PM_WORK_ATTEMPT_CHANGED"
    assert refused["error"]["admission_refused"] is True
    assert "operation" not in refused
    assert {table: ops.db.execute(f"SELECT count(*) FROM {table}").fetchone()[0] for table in counts} == counts
    assert mock.invokes == frames

    retained = await _result(w, selected)
    assert retained["identity"] == original["identity"]
    assert retained["results"] == original["results"]
    assert retained["changes"]["fixed_result_commit"] == original["changes"]["fixed_result_commit"]
    assert mock.invokes == frames  # Reading the old Result does not dispatch.
    assert snapshot(w["human"]) == manual_before
