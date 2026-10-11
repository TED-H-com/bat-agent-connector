"""Orchestrate tier (mock server only)."""

from __future__ import annotations

import json

import pytest

from bat_agent_connector import orchestrate, registry, service
from bat_agent_connector.errors import ResourceReadOnly, WriteRefused
from bat_agent_connector.resource_policy import BY_ACTION, WriteGrant
from tests.conftest import adopt


def tab_grant(sid: str) -> WriteGrant:
    adopt(sid)
    entry = registry.get("h1", sid)
    return WriteGrant("h1", "workspace.register_tab", sid, BY_ACTION["workspace.register_tab"].channels,
                      controller_authority=entry["controller_authority"],
                      controller_incarnation=entry["controller_incarnation"])


async def test_worktree_status_read_tier(fleet_factory, mock):
    mock.ws_doc["terminals"].append(
        {
            "id": "wt-sess-0005",
            "workspaceId": "ws-1",
            "agentPreset": "claude-code-worktree",
            "cwd": "/srv/demo/.bat-worktrees/aa",
            "worktreePath": "/srv/demo/.bat-worktrees/aa",
            "worktreeBranch": "bat/worktree-aa",
        }
    )
    mock.worktrees["wt-sess-0005"] = {
        "diff": "diff --git a/x b/x\n+++ b/x\n+new\n-old\n",
        "branchName": "bat/worktree-aa",
        "worktreePath": "/srv/demo/.bat-worktrees/aa",
        "sourceBranch": "main",
        "merged": False,
        "mergedKind": "ahead",
    }
    f = fleet_factory()
    r = await orchestrate.worktree_status(f, "h1")
    assert r["count"] == 1 and r["worktrees"][0]["diff_stats"] == {"files": 1, "additions": 1, "deletions": 1}
    d = await orchestrate.session_worktree_status(f, "h1", "wt-sess-0005", include_diff=True)
    assert d["merged_kind"] == "ahead" and d["worktree_dirty_files"] == 0 and "diff" in d
    assert not any(
        i["channel"] in ("worktree:merge", "worktree:remove", "worktree:rehydrate") for i in mock.invokes
    )
    await f.close()


async def test_orchestrate_disabled_by_default(fleet_factory, mock):
    f = fleet_factory(writes=True)
    with pytest.raises(WriteRefused, match="orchestrate"):
        await orchestrate.session_start(f, "h1", "demo-project", confirm=True)
    f2 = fleet_factory(writes=True, orchestrate=True)
    with pytest.raises(WriteRefused, match="confirm"):
        await orchestrate.session_start(f2, "h1", "demo-project")
    assert "worktree:create" not in mock.channels() and "claude:start-session" not in mock.channels()


async def test_session_start_worktree_cap_and_registry(fleet_factory, mock):
    f = fleet_factory(writes=True, orchestrate=True)
    r = await orchestrate.session_start(f, "h1", "demo-project", "codex", confirm=True, prompt="do task 1")
    assert r["branch"].startswith("bat/worktree-") and r["prompt_sent"] and r["tab"] is None
    start = next(i for i in mock.invokes if i["channel"] == "claude:start-session")
    o = start["params"]["options"]
    assert o["agentPreset"] == "codex-agent-worktree" and o["useWorktree"] and o["cwd"] == o["worktreePath"]
    assert json.loads(mock.dispatch("workspace:load", {}))["terminals"][-1]["id"] == "shell-0004"  # no tab
    # visible to sessions_list via the local registry
    s = await service.sessions_list(f, "h1")
    row = next(x for x in s["sessions"] if x["session_id"] == r["session_id"])
    assert row["orchestrated"] and row["has_tab"] is False
    await orchestrate.session_start(f, "h1", "demo-project", confirm=True)
    with pytest.raises(WriteRefused, match="cap"):
        await orchestrate.session_start(f, "h1", "demo-project", confirm=True)  # orchestrate_max_sessions=2
    assert len(registry.list_entries("h1", active_only=True)) == 2
    await f.close()


async def test_session_start_keeps_worktree_on_error_after_transport(fleet_factory, mock):
    def boom(p):
        raise RuntimeError("start failed")

    mock.handlers["claude:start-session"] = boom
    f = fleet_factory(writes=True, orchestrate=True)
    with pytest.raises(Exception, match="start failed"):
        await orchestrate.session_start(f, "h1", "demo-project", confirm=True)
    assert len(mock.worktrees) == 1
    row = registry.list_entries("h1")[0]
    assert row["status"] == "uncertain" and row["start_sent"] is True
    assert row["error_code"] == "CONFINEMENT_START_UNSETTLED"
    await f.close()


async def test_tab_registration_append_only(fleet_factory, mock):
    before = json.loads(json.dumps(mock.ws_doc))
    f = fleet_factory(writes=True, orchestrate=True, tabs=True)
    r = await orchestrate.session_start(f, "h1", "demo-project", confirm=True)
    assert r["tab"]["appended"] and r["tab"]["verified_previous_terminals_kept"]
    after = mock.ws_doc
    assert after["terminals"][:-1] == before["terminals"] and after["workspaces"] == before["workspaces"]
    assert after["terminals"][-1]["id"] == r["session_id"]
    await f.close()


async def test_tab_registration_backs_off_on_concurrent_change(fleet_factory, mock):
    f = fleet_factory(writes=True, orchestrate=True, tabs=True)
    c = f.client("h1")
    calls = {"n": 0}
    orig = mock.dispatch

    def racing(ch, p):
        if ch == "workspace:load":
            calls["n"] += 1
            mock.ws_doc["activeTerminalId"] = f"t{calls['n']}"  # someone else keeps saving
        return orig(ch, p)

    mock.dispatch = racing
    r = await c.append_workspace_terminal("default", {"id": "new-1", "workspaceId": "ws-1"}, retries=2,
                                          grant=tab_grant("new-1"))
    assert r["appended"] is False and "workspace:save" not in mock.channels()
    await f.close()


async def test_gui_save_between_reload_and_save_remains_a_host_race(fleet_factory, mock):
    f = fleet_factory(writes=True, orchestrate=True, tabs=True)
    c = f.client("h1")
    gui_tab = {"id": "gui-concurrent", "workspaceId": "ws-1"}
    mock.save_hook = lambda: mock.ws_doc["terminals"].append(gui_tab)
    r = await c.append_workspace_terminal("default", {"id": "connector-tab", "workspaceId": "ws-1"},
                                          grant=tab_grant("connector-tab"))
    assert r["appended"] is True
    assert gui_tab not in mock.ws_doc["terminals"]  # BAT offers no compare-and-swap save
    await f.close()


async def _wt_session(f, mock, kind="ahead"):
    r = await orchestrate.session_start(f, "h1", "demo-project", confirm=True)
    mock.worktrees[r["session_id"]]["mergedKind"] = kind
    return r


async def test_merge_never_targets_a_human_checkout(fleet_factory, mock):
    f = fleet_factory(writes=True, orchestrate=True, safety={"write_min_interval_s": 0})
    r = await _wt_session(f, mock, "ahead")
    assert r["isolation"] == "legacy_shared_clone"
    with pytest.raises(ResourceReadOnly, match="DESTINATION_MANUAL"):
        await orchestrate.worktree_merge(f, "h1", r["session_id"], confirm=True)
    assert "worktree:merge" not in mock.channels() and "worktree:rehydrate" not in mock.channels()
    await f.close()


async def test_merge_rules(fleet_factory, mock):
    # /srv/demo is the connector's own clone here, so merging into its main checkout is allowed
    f = fleet_factory(writes=True, orchestrate=True, managed_roots=["/srv/demo"],
                      safety={"write_min_interval_s": 0})
    r = await _wt_session(f, mock, "diverged")
    assert r["isolation"] == "managed_clone"
    m = await orchestrate.worktree_merge(f, "h1", r["session_id"], confirm=True)
    assert m["merged_now"] is False and "diverged" in m["reason"]
    mock.worktrees[r["session_id"]]["mergedKind"] = "ahead"
    mock.git_status[r["worktree_path"]] = [{"status": "M", "file": "a.py"}]
    m = await orchestrate.worktree_merge(f, "h1", r["session_id"], confirm=True)
    assert m["merged_now"] is False and "uncommitted" in m["reason"]
    mock.git_status[r["worktree_path"]] = []
    mock.git_branch["/srv/demo"] = "feature-x"
    m = await orchestrate.worktree_merge(f, "h1", r["session_id"], confirm=True)
    assert m["merged_now"] is False and "not 'main'" in m["reason"]
    mock.git_branch["/srv/demo"] = "main"
    mock.git_status["/srv/demo"] = [{"status": "??", "file": "junk"}]
    m = await orchestrate.worktree_merge(f, "h1", r["session_id"], confirm=True)
    assert m["merged_now"] is False
    assert "worktree:merge" not in mock.channels()
    mock.git_status["/srv/demo"] = []
    m = await orchestrate.worktree_merge(f, "h1", r["session_id"], confirm=True)
    assert m["merged_now"] is True
    await f.close()


async def test_legacy_remove_requires_reviewed_cleanup_even_when_clean(fleet_factory, mock):
    f = fleet_factory(writes=True, orchestrate=True, safety={"write_min_interval_s": 0})
    r = await _wt_session(f, mock, "ahead")
    mock.git_status[r["worktree_path"]] = [{"status": "M", "file": "a.py"}]
    with pytest.raises(ResourceReadOnly, match="LEGACY_WORKTREE_REMOVE_DISABLED"):
        await orchestrate.worktree_remove(f, "h1", r["session_id"], confirm=True)
    mock.git_status[r["worktree_path"]] = []
    with pytest.raises(ResourceReadOnly, match="LEGACY_WORKTREE_REMOVE_DISABLED"):
        await orchestrate.worktree_remove(f, "h1", r["session_id"], confirm=True, delete_branch=True)
    assert "worktree:remove" not in mock.channels()
    with pytest.raises(ResourceReadOnly, match="LEGACY_WORKTREE_REMOVE_DISABLED"):
        await orchestrate.worktree_remove(f, "h1", r["session_id"], confirm=True)
    assert "worktree:rehydrate" not in mock.channels()
    assert registry.get("h1", r["session_id"])["status"] == "active"
    await f.close()


def test_fanout_plan():
    plan = "# Plan\nGoal: ship v2\n\n- [ ] Add login API\n  with tests\n- [ ] Fix CSS\n- [x] done already\n"
    r = orchestrate.fanout_plan(plan)
    assert [t["title"] for t in r["tasks"]] == ["Add login API", "Fix CSS"]
    assert "with tests" in r["tasks"][0]["prompt"] and "Do not merge" in r["tasks"][0]["prompt"]


@pytest.mark.parametrize("runtime_knows_worktree", [False, True])
async def test_legacy_remove_refuses_before_either_rehydrate_path(fleet_factory, mock, runtime_knows_worktree):
    """Both _wt_status's early rehydrate and the later explicit repair must remain read-only."""
    f = fleet_factory(writes=True, orchestrate=True)
    r = await orchestrate.session_start(f, "h1", "demo-project", "codex", confirm=True)
    sid = r["session_id"]
    original = mock.worktrees.pop(sid)
    if runtime_knows_worktree:
        mock.codex_worktrees[sid] = original
    with pytest.raises(ResourceReadOnly, match="LEGACY_WORKTREE_REMOVE_DISABLED"):
        await orchestrate.worktree_remove(f, "h1", sid, confirm=True)
    assert "worktree:rehydrate" not in mock.channels() and "worktree:remove" not in mock.channels()
    # The existing status reader still works without repairing a missing BAT registration.
    status = await orchestrate.session_worktree_status(f, "h1", sid)
    assert status["session_id"] == sid
    assert "worktree:rehydrate" not in mock.channels() and "worktree:remove" not in mock.channels()
    await f.close()

@pytest.mark.asyncio
async def test_session_start_honors_and_reports_base_branch(fleet_factory, mock):
    f = fleet_factory(writes=True, orchestrate=True)
    result = await orchestrate.session_start(
        f, "h1", "demo-project", "codex", confirm=True, base_branch="feat/task-service"
    )
    create = [i for i in mock.invokes if i["channel"] == "worktree:create"][-1]
    assert create["params"]["baseBranch"] == "feat/task-service"
    assert result["source_branch"] == "feat/task-service"
    assert result["base_commit"] == "abc1234"
