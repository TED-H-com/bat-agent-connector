"""Tests for E04 Phase B2: /result, /changes, /diff, RPC/MCP parity, capabilities, and read-side invariants."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from bat_agent_connector import (
    checkpoints,
    dashboard_sync,
    mcp_server,
    pm_attempt_result_reads,
    pm_attempt_results,
    pm_delivery_lineage,
    pm_execution_verification,
    pm_quality_reviews,
    pm_work_start,
    pm_workspace,
    pr_publication,
    registry,
    service,
    verification,
    work_items,
)
from bat_agent_connector.errors import BatError, ConnectionLost, InvokeTimeout
from bat_agent_connector.operations import OperationError, OperationService, Uncertain
from tests.operation_helpers import settle_operations
from tests.test_api_v1 import http, token
from tests.test_checkpoints import RealGitLog
from tests.test_pm_package_handoff import (
    dispatch_runner,
)
from tests.test_pm_package_handoff import (
    pm as handoff_pm_fixture,
)
from tests.test_pm_package_handoff import (
    ready as handoff_ready_fixture,
)
from tests.test_pm_package_handoff import (
    world as handoff_world_fixture,
)
from tests.test_pm_plan_operations import OWNER as PLAN_OWNER
from tests.test_pm_run_api import unpack
from tests.test_pm_work_packages import (
    action as pkg_action,
)
from tests.test_pm_work_packages import (
    add_task_evidence,
)
from tests.test_pm_work_packages import (
    create as create_package,
)
from tests.test_pm_work_start import STARTER_OWNER, _start_envelope

world = handoff_world_fixture
pm = handoff_pm_fixture
ready = handoff_ready_fixture


def _write_sample_turn_ended_result(tmp_path: Path, payload: dict) -> Path:
    out_file = tmp_path / "sample_turn_ended_result.json"
    out_file.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"SAMPLE_TURN_ENDED_RESULT_PATH={out_file}")
    return out_file


class OverridableGitLog(RealGitLog):
    """Uses explicit per-path overrides when set, otherwise falls back to RealGitLog."""

    def __contains__(self, cwd) -> bool:
        return dict.__contains__(self, cwd) or super().__contains__(cwd)

    def __getitem__(self, cwd):
        if dict.__contains__(self, cwd):
            return dict.__getitem__(self, cwd)
        return super().__getitem__(cwd)


@pytest.fixture
async def start_ready(ready, mock):
    w = ready
    mock.git_logs = OverridableGitLog()
    mock.commit_files = {}
    mock.commit_diffs = {}
    dispatch_runner(w)
    ops = w["d"].ops
    pm_work_start.install(ops)
    pm_attempt_results.install(ops)
    w["owner_token"] = token(
        w["d"],
        STARTER_OWNER.actor,
        "observe",
        "manage",
        "start",
        "operate",
        "integrate",
    )
    return w


async def _advance_start_public(ops: OperationService, op_id: str, rounds: int = 8) -> dict:
    """Advance work.start and its children through public OperationService APIs only."""
    for _ in range(rounds):
        ops.wake(op_id)
        await settle_operations(ops)
        cur = ops.get(op_id)
        if cur["status"] in {"succeeded", "failed", "cancelled", "needs_attention"}:
            break
    cur = ops.get(op_id)
    assert cur["error_code"] != "INTERNAL", [(s["name"], s["error"]) for s in cur["steps"] if s["error"]]
    return cur


async def _start_managed_attempt(
    w,
    mock,
    *,
    key: str = "e04-b2-start",  # gitleaks:allow (test idempotency key)
    echo_send: bool = True,
    work_item_id: str | None = None,
    delivery: str | None = "pull_request",
) -> dict:
    """Drive a real work.start to a started managed session on mock BAT."""
    mock.echo_sends = echo_send
    ops = w["d"].ops
    env = await _start_envelope(w, work_item_id=work_item_id, key=key, delivery=delivery)
    if delivery is None:
        env["params"].pop("delivery", None)
    parent, fresh = ops.create(STARTER_OWNER, **env)
    assert fresh is True
    done = await _advance_start_public(ops, parent["operation_id"])
    assert done["status"] == "succeeded", done
    res = done["result"]
    assert res["stage"] == "started"
    handoff_id = res["children"]["handoff"]
    handoff = ops.get(handoff_id)
    hres = handoff["result"]
    sid = hres["session_id"]
    wt = hres["worktree_path"]
    mid = hres["message_id"]
    base_sha = hres["source_sha"]

    if not any(t.get("id") == sid for t in mock.ws_doc.get("terminals", [])):
        mock.ws_doc["terminals"].append(
            {
                "id": sid,
                "workspaceId": "ws-1",
                "title": "Managed Attempt Session",
                "type": "terminal",
                "cwd": wt,
                "worktreePath": wt,
                "worktreeBranch": hres["branch"],
                "agentPreset": "claude-code",
                "sdkSessionId": f"sdk-{sid[:8]}",
                "model": "claude-sonnet-4-5",
            }
        )
    return {
        "workspace_id": w["workspace_id"],
        "work_item_id": work_item_id or w["target"]["work_item_id"],
        "attempt_id": res["attempt_id"],
        "start_operation_id": parent["operation_id"],
        "handoff_operation_id": handoff_id,
        "host": hres["host"],
        "session_id": sid,
        "worktree_path": wt,
        "turn_marker": mid,
        "base_sha": base_sha,
    }


def _set_turn_messages(
    mock,
    sid: str,
    turn_marker: str,
    *,
    streaming: bool = False,
    pending_ask: dict | None = None,
    assistant_id: str = "m-asst-1",
    assistant_text: str = "Implemented credential validation and all unit tests pass.",
    include_echo: bool = True,
    last_data_at: int = 1_790_001_005_000,
    extra_items: list[dict] | None = None,
) -> None:
    msgs: list[dict] = []
    if include_echo:
        msgs.append(
            {
                "id": turn_marker,
                "role": "user",
                "content": "[batc] Implement credential validation and tests.",
                "timestamp": 1_790_001_000_000,
            }
        )
    if extra_items:
        msgs.extend(extra_items)
    msgs.append(
        {
            "id": assistant_id,
            "role": "assistant",
            "content": assistant_text,
            "timestamp": last_data_at,
        }
    )
    mock.metas[sid] = {
        **(mock.metas.get(sid) or {"cwd": "/srv/demo"}),
        "isStreaming": streaming,
        "model": "claude-sonnet-4-5",
        "numTurns": 1,
        "contextTokens": 1234,
        "contextWindow": 200000,
        "lastDataAt": last_data_at,
    }
    mock.states[sid] = {
        "isStreaming": streaming,
        "messages": msgs,
        "pendingAskUser": pending_ask,
        "pendingPermission": None,
    }


def _seed_worktree_commits(mock, wt: str, base_sha: str) -> tuple[str, str]:
    c1 = "1" * 40
    c2 = "2" * 40
    mock.git_logs[wt] = [
        {"hash": c2, "author": "dev", "date": "2026-10-15", "message": "feat: add tests"},
        {"hash": c1, "author": "dev", "date": "2026-10-15", "message": "feat: add auth"},
        {"hash": base_sha, "author": "dev", "date": "2026-10-15", "message": "base"},
    ]
    mock.commit_files[c1] = [{"file": "src/auth.py", "status": "M"}]
    mock.commit_files[c2] = [
        {"file": "src/auth.py", "status": "M"},
        {"file": "tests/test_auth.py", "status": "A"},
    ]
    mock.commit_diffs[(c1, "src/auth.py")] = (
        "diff --git a/src/auth.py b/src/auth.py\n"
        "--- a/src/auth.py\n"
        "+++ b/src/auth.py\n"
        "@@ -1,2 +1,3 @@\n"
        "+def verify():\n"
        "+    return True\n"
        "-pass\n"
    )
    mock.commit_diffs[(c2, "src/auth.py")] = (
        "diff --git a/src/auth.py b/src/auth.py\n"
        "--- a/src/auth.py\n"
        "+++ b/src/auth.py\n"
        "@@ -1,3 +1,4 @@\n"
        "+# validated\n"
    )
    mock.commit_diffs[(c2, "tests/test_auth.py")] = (
        "diff --git a/tests/test_auth.py b/tests/test_auth.py\n"
        "--- /dev/null\n"
        "+++ b/tests/test_auth.py\n"
        "@@ -0,0 +1,2 @@\n"
        "+def test_verify():\n"
        "+    assert True\n"
    )
    return c1, c2


async def _call_mcp_tool(w, monkeypatch, tok: str, tool_name: str, arguments: dict) -> dict:
    monkeypatch.setenv("BATC_TASK_URL", f"http://127.0.0.1:{w['port']}/rpc")
    monkeypatch.setenv("BATC_API_TOKEN", tok)
    mcp, mcp_fleet = mcp_server.build_server(w["d"].fleet.config, read_only=True, principal_only=True)
    try:
        return unpack(await mcp.call_tool(tool_name, arguments))
    finally:
        await mcp_fleet.close()


async def test_result_makes_zero_bat_calls_and_collector_check_tracks_outcomes(start_ready, mock):
    w = start_ready
    d = w["d"]
    collector = d.result_collector
    att = await _start_managed_attempt(w, mock, key="e04-zero-bat")  # gitleaks:allow (test idempotency key)
    wid, aid, sid = att["workspace_id"], att["attempt_id"], att["session_id"]

    # 1. Before any collector pass: collection.status is 'not_checked_since_start' and turn fields are 'unknown' (never False).
    mock.invokes.clear()
    st0, res0 = await http(
        w["port"],
        "GET",
        f"/api/v1/pm/workspaces/{wid}/work-attempts/{aid}/result",
        tok=w["owner_token"],
    )
    assert st0 == 200, res0
    assert mock.invokes == []
    assert res0["collection"] == {"status": "not_checked_since_start"}
    assert res0["conversation"]["turn_started"] == "unknown"
    assert res0["conversation"]["turn_done"] == "unknown"
    assert res0["conversation"]["turn_phase"] == "unknown"
    assert res0["conversation"]["turn_attribution"] == "unknown"
    assert res0["execution"]["state"] == "unknown"
    assert res0["execution"]["state_source"] == "work_start"
    assert "turn_boundary_not_collected" in res0["completeness"]["reasons"]

    # 2. Session echo is not visible -> collector records outcome 'turn_marker_not_visible' and /result surfaces it with 0 BAT calls.
    _set_turn_messages(mock, sid, att["turn_marker"], streaming=False, include_echo=False)
    await d.inventory._refresh_scheduled("h1")
    assert collector.last_check[aid]["outcome"] == "turn_marker_not_visible"

    mock.invokes.clear()
    st1, res1 = await http(
        w["port"],
        "GET",
        f"/api/v1/pm/workspaces/{wid}/work-attempts/{aid}/result",
        tok=w["owner_token"],
    )
    assert st1 == 200
    assert mock.invokes == []
    assert res1["collection"]["status"] == "checked"
    assert res1["collection"]["outcome"] == "turn_marker_not_visible"
    assert res1["execution"]["state"] == "unknown"
    assert res1["execution"]["state_source"] == "collector_check"
    assert res1["execution"]["state_reason"] == "turn_marker_not_visible"
    assert "turn_marker_not_visible" in res1["completeness"]["reasons"]
    assert res1["conversation"]["turn_done"] == "unknown"

    # 3. Mid-turn streaming -> collector records 'mid_turn'; /result uses inventory_observation 'active' with 0 BAT calls.
    _set_turn_messages(mock, sid, att["turn_marker"], streaming=True, include_echo=True, last_data_at=1_790_001_010_000)
    await d.inventory._refresh_scheduled("h1")
    assert collector.last_check[aid]["outcome"] == "mid_turn"

    mock.invokes.clear()
    st2, res2 = await http(
        w["port"],
        "GET",
        f"/api/v1/pm/workspaces/{wid}/work-attempts/{aid}/result",
        tok=w["owner_token"],
    )
    assert st2 == 200
    assert mock.invokes == []
    assert res2["collection"]["outcome"] == "mid_turn"
    assert res2["execution"]["state"] == "active"
    assert res2["execution"]["state_source"] == "inventory_observation"

    # 4. Turn ends -> collector records 'recorded', then subsequent pass records 'unchanged'.
    _seed_worktree_commits(mock, att["worktree_path"], att["base_sha"])
    _set_turn_messages(mock, sid, att["turn_marker"], streaming=False, include_echo=True, last_data_at=1_790_001_020_000)
    await d.inventory._refresh_scheduled("h1")
    assert collector.last_check[aid]["outcome"] == "recorded"

    await collector.collect_host("h1")
    assert collector.last_check[aid]["outcome"] == "unchanged"

    mock.invokes.clear()
    st3, res3 = await http(
        w["port"],
        "GET",
        f"/api/v1/pm/workspaces/{wid}/work-attempts/{aid}/result",
        tok=w["owner_token"],
    )
    assert st3 == 200
    assert mock.invokes == []
    assert res3["execution"]["state"] == "turn-ended"
    assert res3["execution"]["state_source"] == "result_receipt"
    assert res3["conversation"]["turn_started"] is True
    assert res3["conversation"]["turn_done"] is True
    assert res3["conversation"]["turn_phase"] == "terminal"
    assert res3["conversation"]["turn_attribution"] == "correlated"
    assert res3["conversation"]["transcript_read"] == {
        "http": f"/api/v1/sessions/h1/{sid}/messages?after={att['turn_marker']}",
        "rpc": "session_read",
        "mcp": "session_read",
        "after": att["turn_marker"],
    }


async def test_lost_connection_keeps_last_fixed_result_and_marks_partial(start_ready, mock):
    w = start_ready
    d = w["d"]
    att = await _start_managed_attempt(w, mock, key="e04-lost-conn")  # gitleaks:allow (test idempotency key)
    wid, aid, sid, wt = att["workspace_id"], att["attempt_id"], att["session_id"], att["worktree_path"]

    _, c2 = _seed_worktree_commits(mock, wt, att["base_sha"])
    _set_turn_messages(
        mock,
        sid,
        att["turn_marker"],
        streaming=False,
        assistant_text="Completed login implementation and verified unit tests.",
    )
    await d.inventory._refresh_scheduled("h1")

    st_ok, before_loss = await http(
        w["port"],
        "GET",
        f"/api/v1/pm/workspaces/{wid}/work-attempts/{aid}/result",
        tok=w["owner_token"],
    )
    assert st_ok == 200
    saved_result_id = before_loss["sources"]["result_id"]
    assert saved_result_id is not None
    assert before_loss["changes"]["fixed_result_commit"]["commit_sha"] == c2
    assert before_loss["changes"]["summary"]["additions"] == 5

    # Stop mock BAT and run inventory refresh so hosts_observed records reachable=0.
    await mock.stop()
    await mock.drop_all()
    ref_out = await d.inventory.refresh_host("h1")
    assert ref_out["reachable"] is False

    # /result makes zero BAT calls and preserves the fixed receipt while marking partial + bat_unreachable.
    mock.invokes.clear()
    st_lost, after_loss = await http(
        w["port"],
        "GET",
        f"/api/v1/pm/workspaces/{wid}/work-attempts/{aid}/result",
        tok=w["owner_token"],
    )
    assert st_lost == 200
    assert mock.invokes == []
    assert after_loss["sources"]["result_id"] == saved_result_id
    assert after_loss["conversation"]["latest_assistant_summary"] == before_loss["conversation"]["latest_assistant_summary"]
    assert after_loss["changes"]["fixed_result_commit"]["commit_sha"] == c2
    assert after_loss["changes"]["summary"] == before_loss["changes"]["summary"]
    assert after_loss["execution"]["state"] == "unknown"
    assert after_loss["execution"]["state_source"] == "inventory_observation"
    assert after_loss["execution"]["state_reason"] == "bat_unreachable"
    assert after_loss["completeness"]["status"] == "partial"
    assert "bat_unreachable" in after_loss["completeness"]["reasons"]
    assert after_loss["completeness"]["last_successful_observation"]["result_id"] == saved_result_id

    # /changes also degrades gracefully to partial + cached_receipt (never 500) when BAT is down.
    st_ch, ch_lost = await http(
        w["port"],
        "GET",
        f"/api/v1/pm/workspaces/{wid}/work-attempts/{aid}/changes",
        tok=w["owner_token"],
    )
    assert st_ch == 200
    assert ch_lost["head_freshness"] == "cached_receipt"
    assert ch_lost["live_head_commit"] == c2
    assert ch_lost["completeness"]["status"] == "partial"
    assert "bat_unreachable" in ch_lost["completeness"]["reasons"]


async def test_large_diff_over_pipe_limit_lists_unavailable_without_zero_stats(start_ready, mock):
    w = start_ready
    d = w["d"]
    att = await _start_managed_attempt(w, mock, key="e04-large-diff")  # gitleaks:allow (test idempotency key)
    wid, aid, sid, wt = att["workspace_id"], att["attempt_id"], att["session_id"], att["worktree_path"]

    c1, c2 = _seed_worktree_commits(mock, wt, att["base_sha"])
    # Simulate a file whose diff exceeded BAT's pipe buffer (BAT returns "") and a >256 KiB diff file.
    mock.commit_files[c2].append({"file": "assets/oversized.bundle.js", "status": "A"})
    mock.commit_diffs[(c2, "assets/oversized.bundle.js")] = ""
    mock.commit_files[c2].append({"file": "docs/huge.txt", "status": "A"})
    mock.commit_diffs[(c2, "docs/huge.txt")] = (
        "diff --git a/docs/huge.txt b/docs/huge.txt\n"
        "--- /dev/null\n"
        "+++ b/docs/huge.txt\n"
        "@@ -0,0 +1,4000 @@\n"
        + ("+" + ("x" * 90) + "\n") * 3200
    )

    _set_turn_messages(mock, sid, att["turn_marker"], streaming=False)
    await d.inventory._refresh_scheduled("h1")

    st_res, res = await http(
        w["port"],
        "GET",
        f"/api/v1/pm/workspaces/{wid}/work-attempts/{aid}/result",
        tok=w["owner_token"],
    )
    assert st_res == 200
    summary = res["changes"]["summary"]
    assert summary["aggregation"] == "per_commit"
    assert "assets/oversized.bundle.js" in summary["unavailable"]
    by_path = {f["path"]: f for f in summary["files"]}
    assert by_path["assets/oversized.bundle.js"]["additions"] == "unknown"
    assert by_path["assets/oversized.bundle.js"]["deletions"] == "unknown"
    assert by_path["src/auth.py"]["additions"] == 3
    assert by_path["src/auth.py"]["deletions"] == 1
    assert by_path["src/auth.py"]["commits_touched"] == [c1, c2]
    assert "diff_exceeded_pipe_limit" in res["completeness"]["reasons"]
    assert res["completeness"]["status"] == "partial"

    # /diff on the empty-string diff file reports available=False, reason='diff_exceeded_pipe_limit', unknown stats.
    st_df_unavail, df_unavail = await http(
        w["port"],
        "GET",
        f"/api/v1/pm/workspaces/{wid}/work-attempts/{aid}/diff?commit={c2}&path=assets/oversized.bundle.js",
        tok=w["owner_token"],
    )
    assert st_df_unavail == 200
    assert df_unavail["available"] is False
    assert df_unavail["reason"] == "diff_exceeded_pipe_limit"
    assert df_unavail["diff"] is None
    assert df_unavail["stats"] == {"additions": "unknown", "deletions": "unknown"}

    # /diff on the >256 KiB diff file truncates UTF-8 output at 262_144 bytes and marks truncated=True.
    st_df_huge, df_huge = await http(
        w["port"],
        "GET",
        f"/api/v1/pm/workspaces/{wid}/work-attempts/{aid}/diff?commit={c2}&path=docs/huge.txt",
        tok=w["owner_token"],
    )
    assert st_df_huge == 200
    assert df_huge["available"] is True
    assert df_huge["truncated"] is True
    assert len(df_huge["diff"].encode("utf-8")) <= pm_attempt_result_reads.MAX_DIFF_BYTES
    assert df_huge["stats"]["additions"] == 3200


async def test_foreign_or_manual_session_remains_read_only_and_rejected_for_attempt(start_ready, mock):
    w = start_ready
    att = await _start_managed_attempt(w, mock, key="e04-foreign-sess")  # gitleaks:allow (test idempotency key)
    wid, aid, sid = att["workspace_id"], att["attempt_id"], att["session_id"]

    # Mismatch the registry start_operation_id so the session is not proven managed by this attempt's handoff.
    registry.update("h1", sid, start_operation_id="op_" + "f" * 32)

    mock.invokes.clear()
    st_ch, ch = await http(
        w["port"],
        "GET",
        f"/api/v1/pm/workspaces/{wid}/work-attempts/{aid}/changes",
        tok=w["owner_token"],
    )
    assert st_ch == 200
    assert mock.invokes == []
    assert ch["completeness"]["status"] == "unavailable"
    assert "session_not_managed_by_attempt" in ch["completeness"]["reasons"]

    mock.invokes.clear()
    st_df, df = await http(
        w["port"],
        "GET",
        f"/api/v1/pm/workspaces/{wid}/work-attempts/{aid}/diff?commit={'1' * 40}&path=src/auth.py",
        tok=w["owner_token"],
    )
    assert st_df == 404
    assert mock.invokes == []
    assert df["error"]["code"] == "PM_DIFF_NOT_FOUND"

    mock.invokes.clear()
    st_res, res = await http(
        w["port"],
        "GET",
        f"/api/v1/pm/workspaces/{wid}/work-attempts/{aid}/result",
        tok=w["owner_token"],
    )
    assert st_res == 200
    assert mock.invokes == []
    assert res["completeness"]["status"] == "unavailable"
    assert "session_not_managed_by_attempt" in res["completeness"]["reasons"]


async def test_non_member_gets_404_on_result_changes_and_diff(start_ready, mock, monkeypatch):
    w = start_ready
    d = w["d"]
    att = await _start_managed_attempt(w, mock, key="e04-non-member")  # gitleaks:allow (test idempotency key)
    wid, aid, sid, wt = att["workspace_id"], att["attempt_id"], att["session_id"], att["worktree_path"]
    c1, _ = _seed_worktree_commits(mock, wt, att["base_sha"])
    _set_turn_messages(mock, sid, att["turn_marker"], streaming=False)
    await d.inventory._refresh_scheduled("h1")

    outsider_tok = token(d, "outsider-user", "observe", "manage", "start")
    no_observe_tok = token(d, STARTER_OWNER.actor, "manage", "start")

    for path in (
        f"/api/v1/pm/workspaces/{wid}/work-attempts/{aid}/result",
        f"/api/v1/pm/workspaces/{wid}/work-attempts/{aid}/changes",
        f"/api/v1/pm/workspaces/{wid}/work-attempts/{aid}/diff?commit={c1}&path=src/auth.py",
    ):
        st_404, body_404 = await http(w["port"], "GET", path, tok=outsider_tok)
        assert st_404 == 404, (path, body_404)
        st_403, _ = await http(w["port"], "GET", path, tok=no_observe_tok)
        assert st_403 == 403, path

    # Also verify MCP tools reject non-member with NOT_FOUND.
    for tool_name, args in (
        ("pm_work_attempt_result_get", {"workspace_id": wid, "attempt_id": aid}),
        ("pm_work_attempt_changes_get", {"workspace_id": wid, "attempt_id": aid}),
        ("pm_work_attempt_diff_get", {"workspace_id": wid, "attempt_id": aid, "commit": c1, "path": "src/auth.py"}),
    ):
        with pytest.raises(Exception, match="NOT_FOUND"):
            await _call_mcp_tool(w, monkeypatch, outsider_tok, tool_name, args)


async def test_withdrawn_lineage_removes_result_changes_and_diff_together(start_ready, mock):
    w = start_ready
    d = w["d"]
    ops = d.ops
    task, evidence = await add_task_evidence(w)
    await create_package(w, evidence_refs=[evidence])
    att = await _start_managed_attempt(w, mock, key="e04-withdrawn")  # gitleaks:allow (test idempotency key)
    wid, aid, sid, wt = att["workspace_id"], att["attempt_id"], att["session_id"], att["worktree_path"]
    c1, _ = _seed_worktree_commits(mock, wt, att["base_sha"])
    _set_turn_messages(mock, sid, att["turn_marker"], streaming=False)
    await d.inventory._refresh_scheduled("h1")

    # Add a contributor member 'bob' via the public pm.workspace.member.set operation.
    ws_cur = pm_workspace.get_workspace(ops.db, PLAN_OWNER, wid)
    await pkg_action(
        w,
        "pm.workspace.member.set",
        {"workspace_id": wid},
        {"actor": "bob", "role": "contributor"},
        {"expected_version": ws_cur["version"]},
    )
    bob_tok = token(d, "bob", "observe", "manage")

    # Bob can read all three endpoints while a member and while source lineage is intact.
    for path in (
        f"/api/v1/pm/workspaces/{wid}/work-attempts/{aid}/result",
        f"/api/v1/pm/workspaces/{wid}/work-attempts/{aid}/changes",
        f"/api/v1/pm/workspaces/{wid}/work-attempts/{aid}/diff?commit={c1}&path=src/auth.py",
    ):
        st_ok, _ = await http(w["port"], "GET", path, tok=bob_tok)
        assert st_ok == 200, path

    # Withdraw Bob's membership via pm.workspace.member.set (role=None): all three reads refuse with 404.
    ws_cur2 = pm_workspace.get_workspace(ops.db, PLAN_OWNER, wid)
    await pkg_action(
        w,
        "pm.workspace.member.set",
        {"workspace_id": wid},
        {"actor": "bob", "role": None},
        {"expected_version": ws_cur2["version"]},
    )
    for path in (
        f"/api/v1/pm/workspaces/{wid}/work-attempts/{aid}/result",
        f"/api/v1/pm/workspaces/{wid}/work-attempts/{aid}/changes",
        f"/api/v1/pm/workspaces/{wid}/work-attempts/{aid}/diff?commit={c1}&path=src/auth.py",
    ):
        st_gone, body_gone = await http(w["port"], "GET", path, tok=bob_tok)
        assert st_gone == 404, (path, body_gone)

    # Now withdraw the original source task authority so package lineage is unavailable even for owner.
    with ops.journal.tx():
        ops.db.execute("UPDATE tasks SET controller_authority='{}' WHERE task_id=?", (task["task_id"],))
    for path in (
        f"/api/v1/pm/workspaces/{wid}/work-attempts/{aid}/result",
        f"/api/v1/pm/workspaces/{wid}/work-attempts/{aid}/changes",
        f"/api/v1/pm/workspaces/{wid}/work-attempts/{aid}/diff?commit={c1}&path=src/auth.py",
    ):
        st_lin, body_lin = await http(w["port"], "GET", path, tok=w["owner_token"])
        assert st_lin == 404, (path, body_lin)


async def test_same_position_with_different_digest_stores_both_and_flags_conflict(start_ready, mock):
    w = start_ready
    d = w["d"]
    att = await _start_managed_attempt(w, mock, key="e04-conflict")  # gitleaks:allow (test idempotency key)
    wid, aid, sid, wt = att["workspace_id"], att["attempt_id"], att["session_id"], att["worktree_path"]
    _seed_worktree_commits(mock, wt, att["base_sha"])

    # First turn-end collection at (turn_marker, 'm-asst-1').
    _set_turn_messages(
        mock,
        sid,
        att["turn_marker"],
        streaming=False,
        assistant_id="m-asst-1",
        assistant_text="First assistant text at m-asst-1.",
        last_data_at=1_790_001_005_000,
    )
    await d.inventory._refresh_scheduled("h1")

    # Mutate the text of 'm-asst-1' in mock BAT at the same last_message_id and trigger a new refresh + collect pass.
    _set_turn_messages(
        mock,
        sid,
        att["turn_marker"],
        streaming=False,
        assistant_id="m-asst-1",
        assistant_text="Conflicting mutated text at the exact same m-asst-1 position.",
        last_data_at=1_790_001_099_000,
    )
    await d.inventory._refresh_scheduled("h1")

    rows = pm_attempt_results.receipts(d.ops.db, wid, aid)["receipts"]
    assert len(rows) == 2
    assert rows[0]["last_message_id"] == rows[1]["last_message_id"] == "m-asst-1"
    assert rows[0]["digest"] != rows[1]["digest"]

    ev_page = d.journal.api_events(0, 200, resource_id=aid)
    conflict_evs = [e for e in ev_page["events"] if e["kind"] == "work.attempt.result_conflict"]
    assert len(conflict_evs) == 1
    assert conflict_evs[0]["actor"] == "connector"

    st_res, res = await http(
        w["port"],
        "GET",
        f"/api/v1/pm/workspaces/{wid}/work-attempts/{aid}/result",
        tok=w["owner_token"],
    )
    assert st_res == 200
    assert res["completeness"]["status"] == "partial"
    assert "source_digest_conflict" in res["completeness"]["reasons"]
    assert len(res["results"]) == 2


async def test_commit_after_turn_end_causes_no_digest_conflict(start_ready, mock):
    w = start_ready
    d = w["d"]
    att = await _start_managed_attempt(w, mock, key="e04-post-commit")  # gitleaks:allow (test idempotency key)
    wid, aid, sid, wt = att["workspace_id"], att["attempt_id"], att["session_id"], att["worktree_path"]
    c1, c2 = _seed_worktree_commits(mock, wt, att["base_sha"])

    _set_turn_messages(mock, sid, att["turn_marker"], streaming=False)
    await d.inventory._refresh_scheduled("h1")

    rows_before = pm_attempt_results.receipts(d.ops.db, wid, aid)["receipts"]
    assert len(rows_before) == 1
    digest_before = rows_before[0]["digest"]

    # Add a new commit c3 in the worktree after turn-end.
    c3 = "3" * 40
    mock.git_logs[wt] = [
        {"hash": c3, "author": "dev", "date": "2026-10-15", "message": "chore: post-turn cleanup"},
        *mock.git_logs[wt],
    ]
    mock.commit_files[c3] = [{"file": "src/auth.py", "status": "M"}]
    mock.commit_diffs[(c3, "src/auth.py")] = (
        "diff --git a/src/auth.py b/src/auth.py\n"
        "--- a/src/auth.py\n"
        "+++ b/src/auth.py\n"
        "@@ -1,4 +1,5 @@\n"
        "+# post-turn comment\n"
    )

    # Re-running collector does not create a digest conflict or a second receipt row.
    await d.result_collector.collect_host("h1")
    rows_after = pm_attempt_results.receipts(d.ops.db, wid, aid)["receipts"]
    assert len(rows_after) == 1
    assert rows_after[0]["digest"] == digest_before

    st_res, res = await http(
        w["port"],
        "GET",
        f"/api/v1/pm/workspaces/{wid}/work-attempts/{aid}/result",
        tok=w["owner_token"],
    )
    assert st_res == 200
    assert "source_digest_conflict" not in res["completeness"]["reasons"]

    # First /changes call on c3 computes live summary (memo_hit=False) with 0 DB writes.
    before_changes = d.ops.db.total_changes
    st_ch1, ch1 = await http(
        w["port"],
        "GET",
        f"/api/v1/pm/workspaces/{wid}/work-attempts/{aid}/changes",
        tok=w["owner_token"],
    )
    assert st_ch1 == 200
    assert ch1["live_head_commit"] == c3
    assert ch1["head_freshness"] == "live"
    assert ch1["memo_hit"] is False
    assert ch1["summary"]["additions"] == 6
    assert d.ops.db.total_changes == before_changes

    # Second /changes call on c3 hits in-memory LRU memo (memo_hit=True) and issues zero git:diff calls and zero DB writes.
    mock.invokes.clear()
    st_ch2, ch2 = await http(
        w["port"],
        "GET",
        f"/api/v1/pm/workspaces/{wid}/work-attempts/{aid}/changes",
        tok=w["owner_token"],
    )
    assert st_ch2 == 200
    assert ch2["memo_hit"] is True
    assert ch2["summary"] == ch1["summary"]
    assert [i for i in mock.invokes if i["channel"] in {"git:diff", "git:diff-files"}] == []
    assert d.ops.db.total_changes == before_changes


async def test_git_status_failure_or_empty_list_yields_unknown_never_clean(start_ready, mock):
    w = start_ready
    d = w["d"]
    att = await _start_managed_attempt(w, mock, key="e04-git-status")  # gitleaks:allow (test idempotency key)
    wid, aid, sid, wt = att["workspace_id"], att["attempt_id"], att["session_id"], att["worktree_path"]
    _, c2 = _seed_worktree_commits(mock, wt, att["base_sha"])

    # Default mock git:status returns [] -> must be 'unknown' with reason 'bat_empty_status_not_clean_proof', never 0.
    mock.git_status[wt] = []
    _set_turn_messages(mock, sid, att["turn_marker"], streaming=False)
    await d.inventory._refresh_scheduled("h1")

    st_res, res = await http(
        w["port"],
        "GET",
        f"/api/v1/pm/workspaces/{wid}/work-attempts/{aid}/result",
        tok=w["owner_token"],
    )
    assert st_res == 200
    assert res["changes"]["uncommitted"] == {
        "count": "unknown",
        "files": "unknown",
        "evidence": "unproven",
        "reason": "bat_empty_status_not_clean_proof",
    }
    assert res["changes"]["fixed_result_commit"]["commit_sha"] == c2
    assert res["changes"]["fixed_result_commit"]["uncommitted"] == "unknown"
    assert "bat_empty_status_not_clean_proof" in res["completeness"]["reasons"]

    # When git:status raises BatError on /changes, uncommitted is also 'unknown' with 'bat_empty_status_not_clean_proof'.
    def fail_git_status(_params):
        raise BatError("GIT_STATUS_FAILED", "simulated git:status error")

    mock.handlers["git:status"] = fail_git_status
    try:
        st_ch_err, ch_err = await http(
            w["port"],
            "GET",
            f"/api/v1/pm/workspaces/{wid}/work-attempts/{aid}/changes",
            tok=w["owner_token"],
        )
    finally:
        mock.handlers.pop("git:status", None)
    assert st_ch_err == 200
    assert ch_err["uncommitted"]["count"] == "unknown"
    assert ch_err["uncommitted"]["reason"] == "bat_empty_status_not_clean_proof"

    # Non-empty git:status reports exact count and marks each file diff_available=False.
    mock.git_status[wt] = [{"file": "src/dirty.py", "status": "M", "staged": False}]
    st_ch_dirty, ch_dirty = await http(
        w["port"],
        "GET",
        f"/api/v1/pm/workspaces/{wid}/work-attempts/{aid}/changes",
        tok=w["owner_token"],
    )
    assert st_ch_dirty == 200
    assert ch_dirty["uncommitted"]["count"] == 1
    assert ch_dirty["uncommitted"]["evidence"] == "bat_git_status_nonempty"
    assert ch_dirty["uncommitted"]["files"] == [
        {
            "file": "src/dirty.py",
            "path": "src/dirty.py",
            "status": "M",
            "staged": False,
            "diff_available": False,
            "unavailable_reason": "uncommitted_diff_not_supported_by_bat",
        }
    ]


async def test_bounded_wait_returns_on_event_times_out_and_rejects_expired_cursor(start_ready, mock, monkeypatch):
    w = start_ready
    d = w["d"]
    orig_api_event = d.journal.api_event
    monkeypatch.setattr(d.journal, "api_event", lambda *a, **kw: 0)
    att = await _start_managed_attempt(w, mock, key="e04-bounded-wait")  # gitleaks:allow (test idempotency key)
    monkeypatch.setattr(d.journal, "api_event", orig_api_event)
    wid, aid, sid, wt = att["workspace_id"], att["attempt_id"], att["session_id"], att["worktree_path"]
    _seed_worktree_commits(mock, wt, att["base_sha"])

    # 0. Append 500 unrelated events through the public journal API, then 1 related event; read with cursor=0&wait_ms=0.
    with d.journal.tx():
        for i in range(500):
            d.journal.api_event("task", f"unrelated-{i}", "task.updated", {"i": i}, actor=STARTER_OWNER.actor)
        rel_seq = d.journal.api_event(
            "pm_work_attempt",
            aid,
            "work.attempt.result_recorded",
            {
                "workspace_id": wid,
                "work_item_id": att["work_item_id"],
                "attempt_id": aid,
                "operation_id": att["start_operation_id"],
                "execution_operation_id": att["handoff_operation_id"],
            },
            actor="connector",
        )

    orig_event_page = dashboard_sync.event_page
    event_page_calls: list[dict] = []

    def counting_event_page(journal, principal, after, limit=100, **kwargs):
        event_page_calls.append({"after": after, "limit": limit, **kwargs})
        return orig_event_page(journal, principal, after, limit, **kwargs)

    monkeypatch.setattr(dashboard_sync, "event_page", counting_event_page)
    st_500, res_500 = await http(
        w["port"],
        "GET",
        f"/api/v1/pm/workspaces/{wid}/work-attempts/{aid}/result?cursor=0&wait_ms=0",
        tok=w["owner_token"],
    )
    monkeypatch.setattr(dashboard_sync, "event_page", orig_event_page)
    assert st_500 == 200
    assert res_500["wait"]["matched_seq"] == rel_seq
    assert len(event_page_calls) <= 4 * pm_attempt_result_reads.WAIT_MAX_PAGES_PER_RESOURCE
    assert all(bool(c.get("resource_type")) for c in event_page_calls)

    # 1. Small wait_ms with no new events times out cleanly with timed_out=True and retry_after_ms=1000.
    cur0 = d.journal.api_head()
    mock.invokes.clear()
    st_to, res_to = await http(
        w["port"],
        "GET",
        f"/api/v1/pm/workspaces/{wid}/work-attempts/{aid}/result?cursor={cur0}&wait_ms=10",
        tok=w["owner_token"],
    )
    assert st_to == 200
    assert mock.invokes == []
    assert res_to["wait"] == {"requested_ms": 10, "timed_out": True, "retry_after_ms": 1000}

    # 2. Bounded wait with wait_ms=10000 wakes early as soon as collector records a receipt (no sleep in test).
    _set_turn_messages(mock, sid, att["turn_marker"], streaming=False)
    cur_before_collect = d.journal.api_head()

    wait_task = asyncio.create_task(
        http(
            w["port"],
            "GET",
            f"/api/v1/pm/workspaces/{wid}/work-attempts/{aid}/result?cursor={cur_before_collect}&wait_ms=10000",
            tok=w["owner_token"],
        )
    )
    collect_task = asyncio.create_task(d.inventory._refresh_scheduled("h1"))
    (st_woke, res_woke), _ = await asyncio.gather(wait_task, collect_task)
    assert st_woke == 200
    assert res_woke["wait"]["timed_out"] is False
    assert res_woke["wait"]["matched_seq"] > cur_before_collect
    assert res_woke["execution"]["state"] == "turn-ended"

    # 3. Ahead/invalid cursor returns 409 DASHBOARD_RESET_REQUIRED on both HTTP and RPC.
    ahead_cursor = d.journal.api_head() + 500
    st_409, body_409 = await http(
        w["port"],
        "GET",
        f"/api/v1/pm/workspaces/{wid}/work-attempts/{aid}/result?cursor={ahead_cursor}&wait_ms=0",
        tok=w["owner_token"],
    )
    assert st_409 == 409
    assert body_409["error"]["code"] == "DASHBOARD_RESET_REQUIRED"

    with pytest.raises(OperationError) as exc_rpc:
        await d.call_api(
            "pm_work_attempt_result_get",
            {"workspace_id": wid, "attempt_id": aid, "cursor": ahead_cursor, "wait_ms": 0},
            STARTER_OWNER,
        )
    assert exc_rpc.value.status == 409
    assert exc_rpc.value.code == "DASHBOARD_RESET_REQUIRED"


async def test_diff_with_unlisted_path_or_out_of_range_commit_gets_404(start_ready, mock):
    w = start_ready
    d = w["d"]
    att = await _start_managed_attempt(w, mock, key="e04-diff-404")  # gitleaks:allow (test idempotency key)
    wid, aid, sid, wt = att["workspace_id"], att["attempt_id"], att["session_id"], att["worktree_path"]
    c1, _ = _seed_worktree_commits(mock, wt, att["base_sha"])
    mock.git_status[wt] = [{"file": "src/uncommitted_only.py", "status": "M", "staged": False}]
    _set_turn_messages(mock, sid, att["turn_marker"], streaming=False)
    await d.inventory._refresh_scheduled("h1")

    for query_str in (
        "path=src/auth.py",  # missing commit
        "commit=not-a-sha&path=src/auth.py",  # malformed commit
        f"commit={att['base_sha']}&path=src/auth.py",  # base_commit itself is outside (base..head]
        f"commit={'9' * 40}&path=src/auth.py",  # commit not in scanned range
        f"commit={c1}&path=tests/test_auth.py",  # path only touched in c2, not in c1's diff-files
        f"commit={c1}&path=src/uncommitted_only.py",  # uncommitted file not in c1's diff-files
        f"commit={c1}&path=../secret.txt",  # path traversal rejected
    ):
        st, body = await http(
            w["port"],
            "GET",
            f"/api/v1/pm/workspaces/{wid}/work-attempts/{aid}/diff?{query_str}",
            tok=w["owner_token"],
        )
        assert st == 404, (query_str, st, body)
        assert body["error"]["code"] == "PM_DIFF_NOT_FOUND"


async def test_reads_never_write_and_receipt_actor_is_system_connector(start_ready, mock):
    w = start_ready
    d = w["d"]
    ops = d.ops
    att = await _start_managed_attempt(w, mock, key="e04-reads-never-write")  # gitleaks:allow (test idempotency key)
    wid, aid, sid, wt = att["workspace_id"], att["attempt_id"], att["session_id"], att["worktree_path"]
    c1, _ = _seed_worktree_commits(mock, wt, att["base_sha"])
    _set_turn_messages(mock, sid, att["turn_marker"], streaming=False)
    await d.inventory._refresh_scheduled("h1")

    # Verify all receipt events were recorded with actor == 'connector'.
    ev_page = d.journal.api_events(0, 200, resource_id=aid)
    rec_evs = [e for e in ev_page["events"] if e["kind"].startswith("work.attempt.result_")]
    assert len(rec_evs) == 1
    assert rec_evs[0]["actor"] == "connector"

    def db_snapshot():
        return (
            d.journal.api_head(),
            ops.db.execute("SELECT COUNT(*) FROM session_results").fetchone()[0],
            ops.db.execute("SELECT COUNT(*) FROM session_reading_clock").fetchone()[0],
            ops.db.execute("SELECT COUNT(*) FROM session_message_index").fetchone()[0],
            ops.db.total_changes,
        )

    snap_before = db_snapshot()
    for path in (
        f"/api/v1/pm/workspaces/{wid}/work-attempts/{aid}/result",
        f"/api/v1/pm/workspaces/{wid}/work-attempts/{aid}/changes",
        f"/api/v1/pm/workspaces/{wid}/work-attempts/{aid}/diff?commit={c1}&path=src/auth.py",
    ):
        st, body = await http(w["port"], "GET", path, tok=w["owner_token"])
        assert st == 200, (path, body)
        assert db_snapshot() == snap_before


async def test_mcp_read_equals_http_read_for_result_changes_and_diff(start_ready, mock, monkeypatch):
    w = start_ready
    d = w["d"]
    att = await _start_managed_attempt(w, mock, key="e04-mcp-parity")  # gitleaks:allow (test idempotency key)
    wid, aid, sid, wt = att["workspace_id"], att["attempt_id"], att["session_id"], att["worktree_path"]
    c1, _ = _seed_worktree_commits(mock, wt, att["base_sha"])

    # Capture baseline pm_work_attempt_get output before and after result collection.
    st_att_base, att_base = await http(
        w["port"],
        "GET",
        f"/api/v1/pm/workspaces/{wid}/work-attempts/{aid}",
        tok=w["owner_token"],
    )
    assert st_att_base == 200

    _set_turn_messages(mock, sid, att["turn_marker"], streaming=False)
    await d.inventory._refresh_scheduled("h1")

    # 1. /result parity across HTTP, RPC, and MCP.
    from bat_agent_connector.api_auth import Principal

    owner_principal = Principal(
        STARTER_OWNER.actor,
        frozenset({"observe", "manage", "start", "operate", "integrate"}),
    )
    st_r, http_r = await http(
        w["port"],
        "GET",
        f"/api/v1/pm/workspaces/{wid}/work-attempts/{aid}/result",
        tok=w["owner_token"],
    )
    assert st_r == 200
    rpc_r = await d.call_api(
        "pm_work_attempt_result_get",
        {"workspace_id": wid, "attempt_id": aid},
        owner_principal,
    )
    mcp_r = await _call_mcp_tool(
        w,
        monkeypatch,
        w["owner_token"],
        "pm_work_attempt_result_get",
        {"workspace_id": wid, "attempt_id": aid},
    )
    assert http_r == rpc_r == mcp_r

    # 2. /changes parity across HTTP, RPC, and MCP (once memo is warm, all three are byte-identical).
    st_c, http_c = await http(
        w["port"],
        "GET",
        f"/api/v1/pm/workspaces/{wid}/work-attempts/{aid}/changes",
        tok=w["owner_token"],
    )
    assert st_c == 200
    rpc_c = await d.call_api(
        "pm_work_attempt_changes_get",
        {"workspace_id": wid, "attempt_id": aid},
        owner_principal,
    )
    mcp_c = await _call_mcp_tool(
        w,
        monkeypatch,
        w["owner_token"],
        "pm_work_attempt_changes_get",
        {"workspace_id": wid, "attempt_id": aid},
    )
    assert http_c == rpc_c == mcp_c

    # 3. /diff parity across HTTP, RPC, and MCP.
    st_d, http_d = await http(
        w["port"],
        "GET",
        f"/api/v1/pm/workspaces/{wid}/work-attempts/{aid}/diff?commit={c1}&path=src/auth.py",
        tok=w["owner_token"],
    )
    assert st_d == 200
    rpc_d = await d.call_api(
        "pm_work_attempt_diff_get",
        {"workspace_id": wid, "attempt_id": aid, "commit": c1, "path": "src/auth.py"},
        owner_principal,
    )
    mcp_d = await _call_mcp_tool(
        w,
        monkeypatch,
        w["owner_token"],
        "pm_work_attempt_diff_get",
        {"workspace_id": wid, "attempt_id": aid, "commit": c1, "path": "src/auth.py"},
    )
    assert http_d == rpc_d == mcp_d

    # 4. Capabilities parity between HTTP /api/v1/capabilities and MCP pm_capabilities_get.
    st_cap, http_cap = await http(w["port"], "GET", "/api/v1/capabilities", tok=w["owner_token"])
    assert st_cap == 200
    mcp_cap = await _call_mcp_tool(
        w,
        monkeypatch,
        w["owner_token"],
        "pm_capabilities_get",
        {"workspace_id": wid},
    )
    expected_cap = pm_attempt_result_reads.capabilities(d.ops)
    assert http_cap["features"]["pm_workspaces"]["work_attempt_results"] == expected_cap
    assert mcp_cap["work_attempt_results"] == expected_cap

    # 5. Existing pm_work_attempt_get output is unchanged.
    st_att_after, att_after = await http(
        w["port"],
        "GET",
        f"/api/v1/pm/workspaces/{wid}/work-attempts/{aid}",
        tok=w["owner_token"],
    )
    assert st_att_after == 200
    assert att_after == att_base


def _assert_action_registry_invariants(ops: OperationService, payload: dict) -> None:
    for entry in payload["next_allowed_actions"]:
        if entry.get("kind") == "action":
            act = entry["action"]
            assert act in ops.actions, act
            adef = ops.actions[act]
            assert adef.scope in entry["required_scopes"], (act, entry["required_scopes"])
            assert "observe" in entry["required_scopes"], (act, entry["required_scopes"])
            if adef.target_keys:
                assert set(entry["target"]) == set(adef.target_keys), (act, entry["target"], adef.target_keys)
    for b in payload["blocked_actions"]:
        assert b["action"] in ops.actions, b


async def test_state_facets_next_actions_and_links_invariants(start_ready, mock, monkeypatch, tmp_path):
    w = start_ready
    d = w["d"]
    ops = d.ops
    att = await _start_managed_attempt(w, mock, key="e04-facets-actions")  # gitleaks:allow (test idempotency key)
    wid, aid, sid, wt = att["workspace_id"], att["attempt_id"], att["session_id"], att["worktree_path"]
    _, c2 = _seed_worktree_commits(mock, wt, att["base_sha"])

    _set_turn_messages(
        mock,
        sid,
        att["turn_marker"],
        streaming=False,
        assistant_text="All done! Implemented login and tests pass.",
    )
    await d.inventory._refresh_scheduled("h1")

    st_res, res = await http(
        w["port"],
        "GET",
        f"/api/v1/pm/workspaces/{wid}/work-attempts/{aid}/result",
        tok=w["owner_token"],
    )
    assert st_res == 200
    _assert_action_registry_invariants(ops, res)

    # Write the real turn-ended /result JSON to tmp_path and verify it round-trips.
    sample_path = _write_sample_turn_ended_result(tmp_path, res)
    assert json.loads(sample_path.read_text(encoding="utf-8")) == res

    # Events read link names the real RPC and MCP methods.
    assert res["events_read"] == {
        "http": f"/api/v1/events?after={res['cursor']}&resource_type=pm_work_attempt&resource_id={aid}",
        "rpc": "api_events",
        "mcp": "events_list",
        "resource_type": "pm_work_attempt",
        "resource_id": aid,
    }

    # Controller identity disclosure: only {server_id, controller_id} plus caller_is_controller and requester_actor.
    assert res["identity"]["requester_actor"] == STARTER_OWNER.actor
    assert res["identity"]["caller_is_controller"] is True
    assert set(res["identity"]["controller"].keys()) == {"server_id", "controller_id"}
    assert "scopes" not in res["identity"]
    assert "admin" not in res["identity"]

    # 1. State facets separation & usage invariants.
    facets = res["execution"]["state_facets"]
    assert facets["self_reported"] == "claimed_done"
    assert facets["verification"] == "unverified"
    assert facets["acceptance"] == {"status": "unassessed", "semantic_acceptance": "unassessed"}
    assert facets["review_decision"] == {
        "kind": "unassessed",
        "acceptance": "unassessed",
        "dod": "unassessed",
        "qualified": False,
    }
    assert facets["merge"] == "not_started"
    assert facets["deployment"] == {"state": "not_deployed", "verified": False, "is_current": False}
    assert res["execution"]["exit_code"] == "unknown"
    assert res["execution"]["exit_code_reason"] == "not_exposed_by_bat"
    assert res["usage"]["duration_seconds"] == "unknown"
    assert res["usage"]["duration_reason"] == "not_exposed_by_bat"
    assert "cost_usd" not in res["usage"]

    # 2. Links contain only the existing Dashboard routes (no work_attempt_hash).
    assert res["links"] == {
        "start_operation_hash": f"#/op/{att['start_operation_id']}",
        "execution_operation_hash": f"#/op/{att['handoff_operation_id']}",
        "session_hash": f"#/session/h1/{sid}",
        "work_item_hash": f"#/pm/{wid}/board?select={att['work_item_id']}",
    }

    # 3. Next allowed actions for controller with integrate + operate + manage scopes at turn-ended:
    # session.send, pm.execution.verify, and session.record_verification carry targets matching ActionDef.target_keys.
    send_act = next(a for a in res["next_allowed_actions"] if a.get("action") == "session.send")
    assert send_act["required_scopes"] == ["observe", "operate"]
    ver_act = next(a for a in res["next_allowed_actions"] if a.get("action") == "pm.execution.verify")
    assert ver_act["required_scopes"] == ["observe", "operate"]
    sess_ver_act = next(a for a in res["next_allowed_actions"] if a.get("action") == "session.record_verification")
    assert sess_ver_act["target"] == {"host": "h1", "session_id": sid}
    qr_create_act = next(a for a in res["next_allowed_actions"] if a.get("action") == "pm.quality.review.create")
    assert qr_create_act["target"] == {"workspace_id": wid, "work_item_id": att["work_item_id"]}
    # Without a current quality review for this execution, pm.quality.review.record is not offered.
    assert all(a.get("action") != "pm.quality.review.record" for a in res["next_allowed_actions"])
    assert all(b.get("action") != "pm.quality.review.record" for b in res["blocked_actions"])

    pr_act = next(a for a in res["next_allowed_actions"] if a.get("action") == "github.pr.publish")
    assert pr_act == {
        "kind": "action",
        "action": "github.pr.publish",
        "target": {
            "workspace_id": wid,
            "repository": "o/r",
            "host": "h1",
            "source_operation_id": att["handoff_operation_id"],
        },
        "required_scopes": ["integrate", "manage", "observe"],
        "required_params": ["candidate_sha", "base_ref", "public_title", "public_body", "draft"],
        "optional_params": ["quality_ref"],
        "prefilled_params": {"candidate_sha": c2},
    }
    assert "public_title" not in pr_act["prefilled_params"]
    assert "public_body" not in pr_act["prefilled_params"]

    # Caller missing 'integrate' and 'operate' scopes gets those actions in blocked_actions with 'missing_scope'.
    no_integ_tok = token(d, STARTER_OWNER.actor, "observe", "manage", "start")
    _, res_no_integ = await http(
        w["port"],
        "GET",
        f"/api/v1/pm/workspaces/{wid}/work-attempts/{aid}/result",
        tok=no_integ_tok,
    )
    _assert_action_registry_invariants(ops, res_no_integ)
    assert {"action": "github.pr.publish", "reason": "missing_scope"} in res_no_integ["blocked_actions"]
    assert {"action": "session.send", "reason": "missing_scope"} in res_no_integ["blocked_actions"]
    assert {"action": "pm.execution.verify", "reason": "missing_scope"} in res_no_integ["blocked_actions"]

    # Non-controller contributor member gets controller-bound actions in blocked_actions with 'not_controller'.
    ws_cur = pm_workspace.get_workspace(ops.db, PLAN_OWNER, wid)
    await pkg_action(
        w,
        "pm.workspace.member.set",
        {"workspace_id": wid},
        {"actor": "colleague", "role": "contributor"},
        {"expected_version": ws_cur["version"]},
    )
    colleague_tok = token(d, "colleague", "observe", "manage", "operate", "integrate")
    _, res_other = await http(
        w["port"],
        "GET",
        f"/api/v1/pm/workspaces/{wid}/work-attempts/{aid}/result",
        tok=colleague_tok,
    )
    _assert_action_registry_invariants(ops, res_other)
    assert res_other["identity"]["caller_is_controller"] is False
    assert {"action": "session.send", "reason": "not_controller"} in res_other["blocked_actions"]
    assert {"action": "github.pr.publish", "reason": "not_controller"} in res_other["blocked_actions"]

    # Reader member sees neither 'scopes' nor 'admin' in identity, and writer actions are blocked with 'not_writer_member'.
    ws_cur2 = pm_workspace.get_workspace(ops.db, PLAN_OWNER, wid)
    await pkg_action(
        w,
        "pm.workspace.member.set",
        {"workspace_id": wid},
        {"actor": "reader-user", "role": "reader"},
        {"expected_version": ws_cur2["version"]},
    )
    reader_tok = token(d, "reader-user", "observe", "manage", "operate", "integrate")
    _, res_reader = await http(
        w["port"],
        "GET",
        f"/api/v1/pm/workspaces/{wid}/work-attempts/{aid}/result",
        tok=reader_tok,
    )
    _assert_action_registry_invariants(ops, res_reader)
    assert res_reader["identity"]["caller_is_controller"] is False
    assert set(res_reader["identity"]["controller"].keys()) == {"server_id", "controller_id"}
    assert "scopes" not in res_reader["identity"]
    assert "admin" not in res_reader["identity"]
    assert "scopes" not in res_reader["identity"]["controller"]
    assert "admin" not in res_reader["identity"]["controller"]
    assert {"action": "pm.quality.review.create", "reason": "not_writer_member"} in res_reader["blocked_actions"]
    assert all(b.get("action") != "pm.quality.review.record" for b in res_reader["blocked_actions"])

    # When a current quality review exists for this execution, pm.quality.review.record is offered with {workspace_id, review_id}.
    orig_qr_for_work = pm_quality_reviews.for_work
    monkeypatch.setattr(
        pm_quality_reviews,
        "for_work",
        lambda _ops, _p, _wid, _wiid: {
            "reviews": [
                {
                    "review_id": "qrev_cur_1",
                    "version": 1,
                    "assessment": {"kind": "assessment"},
                    "qualified": True,
                    "acceptance": "accepted",
                    "dod": "satisfied",
                    "updated_at": 600.0,
                    "context": {"execution_operation_id": att["handoff_operation_id"]},
                }
            ],
            "next_cursor": None,
            "visibility_filtered": False,
        },
    )
    _, res_with_rev = await http(
        w["port"],
        "GET",
        f"/api/v1/pm/workspaces/{wid}/work-attempts/{aid}/result",
        tok=w["owner_token"],
    )
    _assert_action_registry_invariants(ops, res_with_rev)
    qr_rec_act = next(a for a in res_with_rev["next_allowed_actions"] if a.get("action") == "pm.quality.review.record")
    assert qr_rec_act["target"] == {"workspace_id": wid, "review_id": "qrev_cur_1"}

    _, res_reader_with_rev = await http(
        w["port"],
        "GET",
        f"/api/v1/pm/workspaces/{wid}/work-attempts/{aid}/result",
        tok=reader_tok,
    )
    _assert_action_registry_invariants(ops, res_reader_with_rev)
    assert {"action": "pm.quality.review.record", "reason": "not_writer_member"} in res_reader_with_rev["blocked_actions"]
    monkeypatch.setattr(pm_quality_reviews, "for_work", orig_qr_for_work)

    # Waiting state offers only session.answer (never session.approve_pending or session.permissions),
    # and live pending sets execution.state='waiting' without changing self_reported to 'reported_blocker'.
    _set_turn_messages(
        mock,
        sid,
        att["turn_marker"],
        streaming=True,
        pending_ask={"toolUseId": "tu-1", "questions": [{"question": "Proceed with migration?"}]},
        assistant_text="All done! Implemented login and tests pass.",
    )
    await d.inventory.refresh_host("h1")
    _, res_waiting = await http(
        w["port"],
        "GET",
        f"/api/v1/pm/workspaces/{wid}/work-attempts/{aid}/result",
        tok=w["owner_token"],
    )
    _assert_action_registry_invariants(ops, res_waiting)
    assert res_waiting["execution"]["state"] == "waiting"
    assert res_waiting["execution"]["pending"] is not None
    assert res_waiting["execution"]["state_facets"]["self_reported"] == "claimed_done"
    assert [
        a["action"] for a in res_waiting["next_allowed_actions"] if a.get("kind") == "action"
    ] == ["session.answer"]
    # Restore turn-ended state on the session.
    _set_turn_messages(
        mock,
        sid,
        att["turn_marker"],
        streaming=False,
        pending_ask=None,
        assistant_text="All done! Implemented login and tests pass.",
    )
    await d.inventory.refresh_host("h1")

    # 4. needs_attention on a second work item maps to 'accepted' (never 'failed') with resume & cancel endpoints.
    base_work = work_items._get_item(ops.db, w["target"]["work_item_id"])
    wi_attn = (
        await pkg_action(
            w,
            "work_item.create",
            {"project_id": base_work["project_id"]},
            {"title": "Needs attention item", "goal": "Goal", "acceptance": "Acceptance"},
        )
    )["work_item_id"]
    env_attn = await _start_envelope(w, work_item_id=wi_attn, key="e04-attn")  # gitleaks:allow (test idempotency key)
    orig_kick = ops.kick
    monkeypatch.setattr(ops, "kick", lambda: None)
    op_attn, _ = ops.create(STARTER_OWNER, **env_attn)
    aid_attn = op_attn["result"]["attempt_id"]
    with ops.journal.tx():
        ops.db.execute("UPDATE work_items SET version=version+1 WHERE work_item_id=?", (wi_attn,))
    monkeypatch.setattr(ops, "kick", orig_kick)
    await _advance_start_public(ops, op_attn["operation_id"], rounds=2)
    assert ops.get(op_attn["operation_id"])["status"] == "needs_attention"

    observe_only_tok = token(d, STARTER_OWNER.actor, "observe")
    _, res_attn = await http(
        w["port"],
        "GET",
        f"/api/v1/pm/workspaces/{wid}/work-attempts/{aid_attn}/result",
        tok=observe_only_tok,
    )
    _assert_action_registry_invariants(ops, res_attn)
    assert res_attn["execution"]["operation_status"] == "needs_attention"
    assert res_attn["execution"]["state"] == "accepted"
    assert res_attn["execution"]["state_source"] == "work_start"
    assert res_attn["execution"]["attention"]["resumable"] is True
    endpoints_attn = [
        a["endpoint"] for a in res_attn["next_allowed_actions"] if a.get("kind") == "operation_endpoint"
    ]
    assert endpoints_attn == ["cancel", "resume"]
    for ep in res_attn["next_allowed_actions"]:
        if ep.get("kind") == "operation_endpoint":
            assert ep["required_scopes"] == ["observe"]

    _, res_attn_colleague = await http(
        w["port"],
        "GET",
        f"/api/v1/pm/workspaces/{wid}/work-attempts/{aid_attn}/result",
        tok=colleague_tok,
    )
    _assert_action_registry_invariants(ops, res_attn_colleague)
    assert [
        a["endpoint"] for a in res_attn_colleague["next_allowed_actions"] if a.get("kind") == "operation_endpoint"
    ] == []

    # 5. start_unknown on a third work item offers only cancel and blocks work.start with WAIT_ORIGINAL_OPERATION_RECONCILE.
    wi_unk = (
        await pkg_action(
            w,
            "work_item.create",
            {"project_id": base_work["project_id"]},
            {"title": "Uncertain handoff item", "goal": "Goal", "acceptance": "Acceptance"},
        )
    )["work_item_id"]
    client = d.fleet.client("h1")
    orig_invoke = client.invoke
    lost_once = False

    async def lose_send_ack(name, *args, **kwargs):
        nonlocal lost_once
        val = await orig_invoke(name, *args, **kwargs)
        if name == "claude:send-message" and not lost_once:
            lost_once = True
            raise InvokeTimeout("Simulated lost ACK on claude:send-message")
        return val

    monkeypatch.setattr(client, "invoke", lose_send_ack)
    env_unk = await _start_envelope(w, work_item_id=wi_unk, key="e04-unk")  # gitleaks:allow (test idempotency key)
    op_unk, _ = ops.create(STARTER_OWNER, **env_unk)
    aid_unk = op_unk["result"]["attempt_id"]
    for _ in range(4):
        ops.wake(op_unk["operation_id"])
        await settle_operations(ops)
        if (ops.get(op_unk["operation_id"]).get("result") or {}).get("stage") == "start_unknown":
            break
    monkeypatch.setattr(client, "invoke", orig_invoke)

    _, res_unk = await http(
        w["port"],
        "GET",
        f"/api/v1/pm/workspaces/{wid}/work-attempts/{aid_unk}/result",
        tok=w["owner_token"],
    )
    _assert_action_registry_invariants(ops, res_unk)
    assert res_unk["execution"]["stage"] == "start_unknown"
    assert res_unk["execution"]["state"] == "unknown"
    assert res_unk["execution"]["state_reason"] == "handoff_outcome_uncertain"
    assert [a.get("endpoint") for a in res_unk["next_allowed_actions"]] == ["cancel"]
    assert {"action": "work.start", "reason": "WAIT_ORIGINAL_OPERATION_RECONCILE"} in res_unk["blocked_actions"]

    # 5b. Parent work.start in operation_status=='uncertain' (before start_unknown stage) also yields state=='unknown',
    # 'start_outcome_uncertain' in completeness.reasons, cancel offered, and work.start blocked.
    wi_punc = (
        await pkg_action(
            w,
            "work_item.create",
            {"project_id": base_work["project_id"]},
            {"title": "Uncertain parent work.start item", "goal": "Goal", "acceptance": "Acceptance"},
        )
    )["work_item_id"]
    orig_admit_child = pm_work_start._admit_child

    def admit_child_raises_uncertain(*_a, **_kw):
        raise Uncertain("prepare", "Simulated uncertain parent work.start step")

    monkeypatch.setattr(pm_work_start, "_admit_child", admit_child_raises_uncertain)
    env_punc = await _start_envelope(w, work_item_id=wi_punc, key="e04-punc")  # gitleaks:allow (test idempotency key)
    op_punc, _ = ops.create(STARTER_OWNER, **env_punc)
    aid_punc = op_punc["result"]["attempt_id"]
    ops.wake(op_punc["operation_id"])
    await settle_operations(ops)
    monkeypatch.setattr(pm_work_start, "_admit_child", orig_admit_child)
    assert ops.get(op_punc["operation_id"])["status"] == "uncertain"

    _, res_punc = await http(
        w["port"],
        "GET",
        f"/api/v1/pm/workspaces/{wid}/work-attempts/{aid_punc}/result",
        tok=w["owner_token"],
    )
    _assert_action_registry_invariants(ops, res_punc)
    assert res_punc["execution"]["operation_status"] == "uncertain"
    assert res_punc["execution"]["stage"] == "preparing"
    assert res_punc["execution"]["state"] == "unknown"
    assert "start_outcome_uncertain" in res_punc["completeness"]["reasons"]
    assert [a.get("endpoint") for a in res_punc["next_allowed_actions"]] == ["cancel"]
    assert {"action": "work.start", "reason": "WAIT_ORIGINAL_OPERATION_RECONCILE"} in res_punc["blocked_actions"]

    # 6. Without a recorded delivery goal, delivery_goal is 'unknown' and github.pr.publish is not offered.
    orig_get_attempt = pm_work_start.get_attempt
    orig_ops_get = ops.get

    def get_attempt_no_delivery(ops_arg, principal_arg, wid_arg, aid_arg):
        env_att = orig_get_attempt(ops_arg, principal_arg, wid_arg, aid_arg)
        att_copy = dict(env_att["attempt"])
        att_copy.pop("delivery", None)
        return {**env_att, "attempt": att_copy}

    def ops_get_no_delivery(op_id, *args, **kwargs):
        op_row = orig_ops_get(op_id, *args, **kwargs)
        if op_id == att["start_operation_id"] and isinstance(op_row, dict):
            op_copy = copy.deepcopy(op_row)
            ws_intent = ((op_copy.get("external_refs") or {}).get("admission_binding") or {}).get("work_start")
            if isinstance(ws_intent, dict):
                ws_intent.pop("delivery", None)
            return op_copy
        return op_row

    import copy

    monkeypatch.setattr(pm_work_start, "get_attempt", get_attempt_no_delivery)
    monkeypatch.setattr(ops, "get", ops_get_no_delivery)
    _, res_nodeliv = await http(
        w["port"],
        "GET",
        f"/api/v1/pm/workspaces/{wid}/work-attempts/{aid}/result",
        tok=w["owner_token"],
    )
    monkeypatch.setattr(pm_work_start, "get_attempt", orig_get_attempt)
    monkeypatch.setattr(ops, "get", orig_ops_get)
    _assert_action_registry_invariants(ops, res_nodeliv)
    assert res_nodeliv["delivery"]["delivery_goal"] == "unknown"
    assert all(a.get("action") != "github.pr.publish" for a in res_nodeliv["next_allowed_actions"])
    assert all(b.get("action") != "github.pr.publish" for b in res_nodeliv["blocked_actions"])


async def test_cursor_before_snapshot_missing_turn_fields_and_zh_tw_self_reported(start_ready, mock, monkeypatch):
    w = start_ready
    d = w["d"]
    att = await _start_managed_attempt(w, mock, key="e04-cursor-zh-tw")  # gitleaks:allow (test idempotency key)
    wid, aid, sid, wt = att["workspace_id"], att["attempt_id"], att["session_id"], att["worktree_path"]
    _seed_worktree_commits(mock, wt, att["base_sha"])

    # 1. Pure zh-TW final assistant message with no English words -> self_reported == 'claimed_done'.
    _set_turn_messages(
        mock,
        sid,
        att["turn_marker"],
        streaming=False,
        assistant_text="已完成權杖驗證與單元測試，所有檢查皆已通過。",
    )
    await d.inventory._refresh_scheduled("h1")

    # 2. Wrap pm_attempt_results.receipts so it appends one related event while the snapshot is being assembled.
    orig_receipts = pm_attempt_results.receipts
    injected_seq: list[int] = []

    def receipts_with_concurrent_event(db, workspace_id, attempt_id, **kwargs):
        if not injected_seq:
            with d.journal.tx():
                ev_seq = d.journal.api_event(
                    "pm_work_attempt",
                    aid,
                    "work.attempt.result_recorded",
                    {
                        "workspace_id": wid,
                        "work_item_id": att["work_item_id"],
                        "attempt_id": aid,
                        "operation_id": att["start_operation_id"],
                        "execution_operation_id": att["handoff_operation_id"],
                        "note": "concurrent",
                    },
                    actor="connector",
                )
            injected_seq.append(int(ev_seq))
        return orig_receipts(db, workspace_id, attempt_id, **kwargs)

    monkeypatch.setattr(pm_attempt_results, "receipts", receipts_with_concurrent_event)
    st_r1, res1 = await http(
        w["port"],
        "GET",
        f"/api/v1/pm/workspaces/{wid}/work-attempts/{aid}/result",
        tok=w["owner_token"],
    )
    monkeypatch.setattr(pm_attempt_results, "receipts", orig_receipts)
    assert st_r1 == 200
    assert len(injected_seq) == 1
    assert res1["cursor"] < injected_seq[0]
    assert res1["execution"]["state_facets"]["self_reported"] == "claimed_done"

    # Next read with that cursor and wait_ms=0 immediately sees the event in wait.matched_seq.
    st_r2, res2 = await http(
        w["port"],
        "GET",
        f"/api/v1/pm/workspaces/{wid}/work-attempts/{aid}/result?cursor={res1['cursor']}&wait_ms=0",
        tok=w["owner_token"],
    )
    assert st_r2 == 200
    assert res2["wait"]["timed_out"] is False
    assert res2["wait"]["matched_seq"] == injected_seq[0]

    # 3. English message containing 'blocked' in a benign sentence is NOT misclassified as 'reported_blocker'.
    _set_turn_messages(
        mock,
        sid,
        att["turn_marker"],
        streaming=False,
        assistant_id="m-asst-2",
        assistant_text="No errors or blocked items remain.",
        last_data_at=1_790_001_050_000,
    )
    await d.inventory._refresh_scheduled("h1")
    _, res_benign = await http(
        w["port"],
        "GET",
        f"/api/v1/pm/workspaces/{wid}/work-attempts/{aid}/result",
        tok=w["owner_token"],
    )
    assert res_benign["execution"]["state_facets"]["self_reported"] == "claimed_done"

    # 4. Collector behavior when service.session_read omits turn facts (never infers a boundary):
    orig_session_read = service.session_read
    receipts_before_count = len(pm_attempt_results.receipts(d.ops.db, wid, aid)["receipts"])
    _set_turn_messages(
        mock,
        sid,
        att["turn_marker"],
        streaming=False,
        assistant_id="m-asst-3",
        assistant_text="Completed another turn without turn metadata.",
        last_data_at=1_790_001_080_000,
    )

    # (c) All four facts omitted: no receipt is written; last_check records 'turn_marker_not_visible'.
    async def session_read_without_four_facts(*args, **kwargs):
        out = dict(await orig_session_read(*args, **kwargs))
        for k in ("turn_started", "turn_done", "turn_phase", "turn_attribution"):
            out.pop(k, None)
        return out

    monkeypatch.setattr(service, "session_read", session_read_without_four_facts)
    await d.inventory._refresh_scheduled("h1")
    assert len(pm_attempt_results.receipts(d.ops.db, wid, aid)["receipts"]) == receipts_before_count
    assert d.result_collector.last_check[aid]["outcome"] == "turn_marker_not_visible"

    # (b) turn_started, turn_done, turn_phase omitted, turn_attribution == 'correlated', session still live & no error:
    # no receipt is written; last_check records 'no_boundary'.
    async def session_read_without_three_facts(*args, **kwargs):
        out = dict(await orig_session_read(*args, **kwargs))
        for k in ("turn_started", "turn_done", "turn_phase"):
            out.pop(k, None)
        out["turn_attribution"] = "correlated"
        return out

    monkeypatch.setattr(service, "session_read", session_read_without_three_facts)
    d.result_collector._memo.pop(aid, None)
    await d.inventory._refresh_scheduled("h1")
    assert len(pm_attempt_results.receipts(d.ops.db, wid, aid)["receipts"]) == receipts_before_count
    assert d.result_collector.last_check[aid]["outcome"] == "no_boundary"

    # (a) Same omission with the session's process ended (runtimeStatus == 'exited'):
    # receipt is stored with boundary 'process_ended' and None for the three omitted facts; /result reports 'unknown'.
    mock.metas[sid]["runtimeStatus"] = "exited"
    d.result_collector._memo.pop(aid, None)
    await d.inventory._refresh_scheduled("h1")
    monkeypatch.setattr(service, "session_read", orig_session_read)

    assert d.result_collector.last_check[aid]["outcome"] == "recorded"
    stored_after_a = pm_attempt_results.receipts(d.ops.db, wid, aid)["receipts"]
    assert len(stored_after_a) == receipts_before_count + 1
    latest_stored_doc = stored_after_a[0]["document"]
    assert latest_stored_doc["last_message_id"] == "m-asst-3"
    assert latest_stored_doc["boundary"] == "process_ended"
    assert latest_stored_doc["turn_started"] is None
    assert latest_stored_doc["turn_done"] is None
    assert latest_stored_doc["turn_phase"] is None
    assert latest_stored_doc["turn_attribution"] == "correlated"
    assert latest_stored_doc["turn_progress"] == {
        "turn_started": None,
        "turn_done": None,
        "turn_phase": None,
        "turn_attribution": "correlated",
        "boundary": "process_ended",
    }

    _, res_missing = await http(
        w["port"],
        "GET",
        f"/api/v1/pm/workspaces/{wid}/work-attempts/{aid}/result",
        tok=w["owner_token"],
    )
    assert res_missing["conversation"]["turn_started"] == "unknown"
    assert res_missing["conversation"]["turn_done"] == "unknown"
    assert res_missing["conversation"]["turn_phase"] == "unknown"
    assert res_missing["conversation"]["turn_attribution"] == "correlated"
    assert res_missing["execution"]["state"] == "process-ended"
    assert res_missing["execution"]["state_facets"]["self_reported"] == "none"

    # 5. Receipt with boundary == 'agent_error' sets self_reported == 'reported_blocker' and execution.state == 'failed'.
    mock.metas[sid].pop("runtimeStatus", None)
    await d.inventory._refresh_scheduled("h1")
    err_doc = pm_attempt_results.build_result_document(
        result_id="res_" + "e" * 32,
        turn_marker=att["turn_marker"],
        last_message_id="m-asst-err",
        slice_items=[{"id": "m-asst-err", "role": "assistant", "tool": "", "text": "Agent runtime error"}],
        boundary="agent_error",
    )
    pm_attempt_results.record_receipt(
        d.journal,
        workspace_id=wid,
        attempt_id=aid,
        source_key=f"bat:h1:{sid}",
        source_generation=pm_attempt_results.source_generation_from_registry("h1", sid, registry.get("h1", sid)),
        turn_marker=att["turn_marker"],
        last_message_id="m-asst-err",
        digest=pm_attempt_results.compute_digest(
            source_key=f"bat:h1:{sid}",
            source_generation=pm_attempt_results.source_generation_from_registry("h1", sid, registry.get("h1", sid)),
            turn_marker=att["turn_marker"],
            last_message_id="m-asst-err",
            turn_items=[{"id": "m-asst-err", "role": "assistant", "tool": "", "text": "Agent runtime error"}],
        ),
        document=err_doc,
        observed_at=1_790_001_090.0,
        operation_id=att["handoff_operation_id"],
        start_operation_id=att["start_operation_id"],
        result_id="res_" + "e" * 32,
    )
    _, res_err = await http(
        w["port"],
        "GET",
        f"/api/v1/pm/workspaces/{wid}/work-attempts/{aid}/result",
        tok=w["owner_token"],
    )
    assert res_err["execution"]["state"] == "failed"
    assert res_err["execution"]["state_reason"] == "agent_error"
    assert res_err["execution"]["state_facets"]["self_reported"] == "reported_blocker"


async def test_verification_latest_timestamp_unknown_status_review_matching_and_coverage(
    start_ready, mock, monkeypatch
):
    w = start_ready
    d = w["d"]
    att = await _start_managed_attempt(w, mock, key="e04-ver-cov")  # gitleaks:allow (test idempotency key)
    wid, aid, sid, wt = att["workspace_id"], att["attempt_id"], att["session_id"], att["worktree_path"]
    _, c2 = _seed_worktree_commits(mock, wt, att["base_sha"])
    _set_turn_messages(mock, sid, att["turn_marker"], streaming=False)
    await d.inventory._refresh_scheduled("h1")

    # 0. EXPLAIN QUERY PLAN of the exact verification query uses pm_execution_verify_by_attempt.
    eqp_rows = [
        tuple(r)
        for r in d.ops.db.execute(
            f"EXPLAIN QUERY PLAN {pm_attempt_result_reads.VERIFY_OPS_SQL}",
            (wid, att["handoff_operation_id"]),
        ).fetchall()
    ]
    print(f"EXPLAIN_QUERY_PLAN_VERIFY_OPS={eqp_rows}")
    assert any("USING INDEX pm_execution_verify_by_attempt" in str(r[-1]) for r in eqp_rows), eqp_rows

    # 1. Record a session_verification with an unmapped status -> maps to 'unknown', never 'failed', and operation_id is None.
    monkeypatch.setattr(
        verification,
        "get",
        lambda host, session_id: (
            {
                "status": "custom_unmapped_status",
                "candidate_commit": c2,
                "actor": STARTER_OWNER.actor,
                "recorded_at": 100.0,
            }
            if (host, session_id) == ("h1", sid)
            else None
        ),
    )
    _, res_unmapped = await http(
        w["port"],
        "GET",
        f"/api/v1/pm/workspaces/{wid}/work-attempts/{aid}/result",
        tok=w["owner_token"],
    )
    assert res_unmapped["verification"]["status"] == "unknown"
    assert res_unmapped["execution"]["state_facets"]["verification"] == "unknown"
    assert res_unmapped["verification"]["runs"][0]["kind"] == "session_verification"
    assert res_unmapped["verification"]["runs"][0]["operation_id"] is None

    # 2. When two verification runs exist across kinds, overall status comes from the latest recorded_at timestamp.
    ver_op_id = "op_" + "a" * 32
    with d.journal.tx():
        d.ops.db.execute(
            """INSERT INTO operations
               (operation_id, actor, entry, idem_key, request_hash, action, target, params, preconditions, status, created_at, updated_at)
               VALUES (?, ?, 'operations.create', 'e04-ver-op', 'hash', ?, ?, ?, '{}', 'succeeded', 200.0, 200.0)""",
            (
                ver_op_id,
                STARTER_OWNER.actor,
                pm_execution_verification.ACTION,
                json.dumps({"workspace_id": wid, "execution_operation_id": att["handoff_operation_id"]}),
                json.dumps({"candidate_commit": c2}),
            ),
        )
    monkeypatch.setattr(pm_execution_verification, "operation_visible", lambda _db, _p, _row: True)
    monkeypatch.setattr(
        pm_execution_verification,
        "get_verification",
        lambda _ops, _p, _wid, oid: {
            "operation_status": "succeeded",
            "verification": {
                "status": "failed",
                "exit_code": 1,
                "created_at": 200.0,
                "reference": {"candidate_commit": c2},
            },
        },
    )
    monkeypatch.setattr(
        verification,
        "get",
        lambda host, session_id: (
            {
                "status": "passed",
                "candidate_commit": c2,
                "exit_code": 0,
                "actor": STARTER_OWNER.actor,
                "recorded_at": 500.0,
            }
            if (host, session_id) == ("h1", sid)
            else None
        ),
    )
    _, res_latest_wins = await http(
        w["port"],
        "GET",
        f"/api/v1/pm/workspaces/{wid}/work-attempts/{aid}/result",
        tok=w["owner_token"],
    )
    assert res_latest_wins["verification"]["status"] == "verified"
    assert res_latest_wins["execution"]["state_facets"]["verification"] == "verified"
    assert [r["status"] for r in res_latest_wins["verification"]["runs"]] == ["failed", "verified"]

    # 2b. With 52 runs where only the newest (52nd) is verified, status is 'verified' and coverage.verification is 'partial'.
    monkeypatch.setattr(verification, "get", lambda _h, _s: None)
    newest_ver_op_id = f"op_ver52_{51:024d}"
    with d.journal.tx():
        for idx in range(52):
            oid_idx = f"op_ver52_{idx:024d}"
            ts_idx = 1000.0 + idx
            d.ops.db.execute(
                """INSERT INTO operations
                   (operation_id, actor, entry, idem_key, request_hash, action, target, params, preconditions, status, created_at, updated_at)
                   VALUES (?, ?, 'operations.create', ?, 'hash', ?, ?, ?, '{}', 'succeeded', ?, ?)""",
                (
                    oid_idx,
                    STARTER_OWNER.actor,
                    f"e04-ver52-{idx}",
                    pm_execution_verification.ACTION,
                    json.dumps({"workspace_id": wid, "execution_operation_id": att["handoff_operation_id"]}),
                    json.dumps({"candidate_commit": c2}),
                    ts_idx,
                    ts_idx,
                ),
            )

    def get_ver_52(_ops, _p, _wid, oid):
        is_newest = oid == newest_ver_op_id
        idx_num = int(oid.split("_")[-1]) if oid.startswith("op_ver52_") else 0
        return {
            "operation_status": "succeeded",
            "verification": {
                "status": "passed" if is_newest else "failed",
                "exit_code": 0 if is_newest else 1,
                "created_at": 1000.0 + idx_num if oid.startswith("op_ver52_") else 200.0,
                "reference": {"candidate_commit": c2},
            },
        }

    monkeypatch.setattr(pm_execution_verification, "get_verification", get_ver_52)
    _, res_52 = await http(
        w["port"],
        "GET",
        f"/api/v1/pm/workspaces/{wid}/work-attempts/{aid}/result",
        tok=w["owner_token"],
    )
    assert res_52["verification"]["status"] == "verified"
    assert res_52["execution"]["state_facets"]["verification"] == "verified"
    assert res_52["completeness"]["coverage"]["verification"] == "partial"
    assert len(res_52["verification"]["runs"]) == 50
    assert res_52["verification"]["runs"][-1]["operation_id"] == newest_ver_op_id
    assert res_52["verification"]["runs"][-1]["status"] == "verified"

    # 3. Quality review for a different execution_operation_id does not apply to this attempt.
    monkeypatch.setattr(
        pm_quality_reviews,
        "for_work",
        lambda _ops, _p, _wid, _wiid: {
            "reviews": [
                {
                    "review_id": "qrev_other",
                    "version": 2,
                    "assessment": {"kind": "assessment"},
                    "qualified": True,
                    "acceptance": "accepted",
                    "dod": "satisfied",
                    "context": {"execution_operation_id": "op_other_attempt_handoff"},
                }
            ],
            "next_cursor": None,
            "visibility_filtered": False,
        },
    )
    _, res_other_rev = await http(
        w["port"],
        "GET",
        f"/api/v1/pm/workspaces/{wid}/work-attempts/{aid}/result",
        tok=w["owner_token"],
    )
    assert res_other_rev["verification"]["quality_reviews"]["current"] is None
    assert res_other_rev["execution"]["state_facets"]["review_decision"]["kind"] == "unassessed"
    assert res_other_rev["execution"]["state_facets"]["acceptance"]["status"] == "unassessed"

    # 4. Delivery lineage attribution & fixed_result_commit priority-1 timestamp tie-breaking:
    before_fixed = res_other_rev["changes"]["fixed_result_commit"]
    assert before_fixed["source_kind"] == "execution_verification"
    assert before_fixed["commit_sha"] == c2

    # 4a. Another attempt's merge and integration change nothing for this attempt.
    monkeypatch.setattr(
        pm_delivery_lineage,
        "for_work",
        lambda _ops, _p, _wid, _wiid, **_kw: {
            "deliveries": [
                {
                    "operation_id": "op_dlv_other_integ",
                    "action": "integration.apply",
                    "status": "succeeded",
                    "created_at": 300.0,
                    "updated_at": 300.0,
                    "target": {"repository": "o/r", "pull_number": 99},
                    "integration": {
                        "sources": [
                            {
                                "source_kind": "execution",
                                "source_id": "op_other_attempt_handoff",
                                "candidate_sha": "8" * 40,
                                "delivered_sha": "7" * 40,
                            }
                        ]
                    },
                    "requirement_acceptance": "not_evaluated_by_delivery",
                },
                {
                    "operation_id": "op_dlv_other_merge",
                    "action": "delivery.merge_pr",
                    "status": "succeeded",
                    "created_at": 310.0,
                    "updated_at": 310.0,
                    "target": {"repository": "o/r", "pull_number": 99},
                    "merge": {
                        "merged": True,
                        "merged_sha": "9" * 40,
                        "head_sha": "7" * 40,
                    },
                    "requirement_acceptance": "not_evaluated_by_delivery",
                },
            ],
            "next_after": None,
            "source_visibility_filtered": False,
        },
    )
    _, res_other_dlv = await http(
        w["port"],
        "GET",
        f"/api/v1/pm/workspaces/{wid}/work-attempts/{aid}/result",
        tok=w["owner_token"],
    )
    assert res_other_dlv["delivery"]["pull_requests"] == []
    assert res_other_dlv["delivery"]["integrations"] == []
    assert res_other_dlv["delivery"]["merges"] == []
    assert res_other_dlv["execution"]["state_facets"]["merge"] == "not_started"
    assert res_other_dlv["changes"]["fixed_result_commit"] == before_fixed

    # 4b. An integration.preview for this attempt is listed under delivery.integrations, merge stays not_started, pull_requests is [].
    monkeypatch.setattr(
        pm_delivery_lineage,
        "for_work",
        lambda _ops, _p, _wid, _wiid, **_kw: {
            "deliveries": [
                {
                    "operation_id": "op_dlv_preview_1",
                    "action": "integration.preview",
                    "status": "succeeded",
                    "created_at": 350.0,
                    "updated_at": 350.0,
                    "target": {"repository": "o/r", "pull_number": 42},
                    "integration": {
                        "sources": [
                            {
                                "source_kind": "execution",
                                "source_id": att["handoff_operation_id"],
                                "candidate_sha": c2,
                            }
                        ]
                    },
                    "requirement_acceptance": "not_evaluated_by_delivery",
                }
            ],
            "next_after": None,
            "source_visibility_filtered": False,
        },
    )
    _, res_prev_dlv = await http(
        w["port"],
        "GET",
        f"/api/v1/pm/workspaces/{wid}/work-attempts/{aid}/result",
        tok=w["owner_token"],
    )
    assert res_prev_dlv["delivery"]["pull_requests"] == []
    assert len(res_prev_dlv["delivery"]["integrations"]) == 1
    assert res_prev_dlv["delivery"]["integrations"][0]["action"] == "integration.preview"
    assert res_prev_dlv["delivery"]["integrations"][0]["operation_id"] == "op_dlv_preview_1"
    assert res_prev_dlv["execution"]["state_facets"]["merge"] == "not_started"
    assert res_prev_dlv["changes"]["fixed_result_commit"] == before_fixed

    # 4c. An attributed integration.apply with delivered_sha becomes fixed_result_commit;
    # and an attributed github.pr.publish receipt has no invented state/html_url/draft fields.
    delivered_sha = "4" * 40
    monkeypatch.setattr(
        pr_publication,
        "list_creation_receipts",
        lambda _ops, _p, _wid, child_op_id: {
            "creation_receipts": [
                {
                    "receipt": {
                        "publication_operation_id": "op_pub_pr_42",
                        "workspace_id": wid,
                        "child_operation_id": child_op_id,
                        "repair_parent_id": None,
                        "provider_origin": "https://github.com",
                        "repository": "o/r",
                        "repository_id": 123,
                        "pull_number": 42,
                        "native_pr_id": 4200,
                        "native_pr_node_id": "PR_kwDO42",
                        "branch_ref": "refs/heads/bat/att",
                        "candidate_sha": c2,
                        "base_ref": "refs/heads/main",
                        "base_sha": att["base_sha"],
                        "observed_head_sha": c2,
                        "observed_base_sha": att["base_sha"],
                        "target_verified": True,
                        "quality_ref": None,
                        "created_at": 360.0,
                        "requirement_acceptance": "not_evaluated_by_delivery",
                    }
                }
            ],
            "next_after": None,
        },
    )
    monkeypatch.setattr(
        pm_delivery_lineage,
        "for_work",
        lambda _ops, _p, _wid, _wiid, **_kw: {
            "deliveries": [
                {
                    "operation_id": "op_dlv_apply_1",
                    "action": "integration.apply",
                    "status": "succeeded",
                    "created_at": 370.0,
                    "updated_at": 370.0,
                    "target": {"repository": "o/r", "pull_number": 42},
                    "integration": {
                        "sources": [
                            {
                                "source_kind": "execution",
                                "source_id": att["handoff_operation_id"],
                                "candidate_sha": c2,
                                "delivered_sha": delivered_sha,
                            }
                        ]
                    },
                    "requirement_acceptance": "not_evaluated_by_delivery",
                }
            ],
            "next_after": None,
            "source_visibility_filtered": False,
        },
    )
    _, res_apply_dlv = await http(
        w["port"],
        "GET",
        f"/api/v1/pm/workspaces/{wid}/work-attempts/{aid}/result",
        tok=w["owner_token"],
    )
    pr_row = res_apply_dlv["delivery"]["pull_requests"][0]
    assert pr_row["pull_number"] == 42
    assert "state" not in pr_row
    assert "html_url" not in pr_row
    assert "draft" not in pr_row
    assert res_apply_dlv["execution"]["state_facets"]["merge"] == "pr_open"
    assert res_apply_dlv["changes"]["fixed_result_commit"] == {
        "commit_sha": delivered_sha,
        "source_kind": "integration",
        "source_id": "op_dlv_apply_1",
        "recorded_by": None,
        "recorded_at": 370.0,
        "uncommitted": "unknown",
    }

    # 4d. An attributed merge after the integration wins fixed_result_commit by timestamp.
    merged_sha = "5" * 40
    monkeypatch.setattr(
        pm_delivery_lineage,
        "for_work",
        lambda _ops, _p, _wid, _wiid, **_kw: {
            "deliveries": [
                {
                    "operation_id": "op_dlv_apply_1",
                    "action": "integration.apply",
                    "status": "succeeded",
                    "created_at": 370.0,
                    "updated_at": 370.0,
                    "target": {"repository": "o/r", "pull_number": 42},
                    "integration": {
                        "sources": [
                            {
                                "source_kind": "execution",
                                "source_id": att["handoff_operation_id"],
                                "candidate_sha": c2,
                                "delivered_sha": delivered_sha,
                            }
                        ]
                    },
                    "requirement_acceptance": "not_evaluated_by_delivery",
                },
                {
                    "operation_id": "op_dlv_merge_1",
                    "action": "delivery.merge_pr",
                    "status": "succeeded",
                    "created_at": 450.0,
                    "updated_at": 450.0,
                    "target": {"repository": "o/r", "pull_number": 42},
                    "merge": {
                        "merged": True,
                        "merged_sha": merged_sha,
                        "head_sha": delivered_sha,
                    },
                    "requirement_acceptance": "not_evaluated_by_delivery",
                },
            ],
            "next_after": None,
            "source_visibility_filtered": False,
        },
    )
    _, res_merge_dlv = await http(
        w["port"],
        "GET",
        f"/api/v1/pm/workspaces/{wid}/work-attempts/{aid}/result",
        tok=w["owner_token"],
    )
    assert res_merge_dlv["execution"]["state_facets"]["merge"] == "merged"
    assert res_merge_dlv["changes"]["fixed_result_commit"] == {
        "commit_sha": merged_sha,
        "source_kind": "integration",
        "source_id": "op_dlv_merge_1",
        "recorded_by": None,
        "recorded_at": 450.0,
        "uncommitted": "unknown",
    }

    # 5. Visibility-filtered verification or delivery sets coverage.verification / coverage.delivery to 'partial'.
    monkeypatch.setattr(
        pm_quality_reviews,
        "for_work",
        lambda _ops, _p, _wid, _wiid: {
            "reviews": [],
            "next_cursor": None,
            "visibility_filtered": True,
        },
    )
    monkeypatch.setattr(
        pm_delivery_lineage,
        "for_work",
        lambda _ops, _p, _wid, _wiid, **_kw: {
            "deliveries": [],
            "next_after": None,
            "source_visibility_filtered": True,
        },
    )
    _, res_part_ver = await http(
        w["port"],
        "GET",
        f"/api/v1/pm/workspaces/{wid}/work-attempts/{aid}/result",
        tok=w["owner_token"],
    )
    assert res_part_ver["completeness"]["coverage"]["verification"] == "partial"
    assert res_part_ver["completeness"]["coverage"]["delivery"] == "partial"
    assert res_part_ver["completeness"]["status"] == "partial"


async def test_changes_connection_lost_during_diff_reports_bat_unreachable_and_skips_memo(
    start_ready, mock, monkeypatch
):
    w = start_ready
    d = w["d"]
    att = await _start_managed_attempt(w, mock, key="e04-changes-conn-lost")  # gitleaks:allow (test idempotency key)
    wid, aid, sid, wt = att["workspace_id"], att["attempt_id"], att["session_id"], att["worktree_path"]
    _seed_worktree_commits(mock, wt, att["base_sha"])
    _set_turn_messages(mock, sid, att["turn_marker"], streaming=False)
    await d.inventory._refresh_scheduled("h1")

    # Add a new commit c3 with two files so /changes walks git:diff live.
    c3 = "3" * 40
    mock.git_logs[wt] = [
        {"hash": c3, "author": "dev", "date": "2026-10-15", "message": "feat: two files"},
        *mock.git_logs[wt],
    ]
    mock.commit_files[c3] = [
        {"file": "src/first.py", "status": "A"},
        {"file": "src/second.py", "status": "A"},
    ]
    mock.commit_diffs[(c3, "src/first.py")] = (
        "diff --git a/src/first.py b/src/first.py\n"
        "--- /dev/null\n"
        "+++ b/src/first.py\n"
        "@@ -0,0 +1,1 @@\n"
        "+x = 1\n"
    )
    mock.commit_diffs[(c3, "src/second.py")] = (
        "diff --git a/src/second.py b/src/second.py\n"
        "--- /dev/null\n"
        "+++ b/src/second.py\n"
        "@@ -0,0 +1,1 @@\n"
        "+y = 2\n"
    )

    read_client = checkpoints._read_fleet(d.ops).client("h1")
    orig_invoke = read_client.invoke

    async def drop_on_second_diff(name, params=None, *args, **kwargs):
        if name == "git:diff" and isinstance(params, dict) and params.get("filePath") == "src/second.py":
            raise ConnectionLost("simulated connection drop during git:diff")
        return await orig_invoke(name, params, *args, **kwargs)

    monkeypatch.setattr(read_client, "invoke", drop_on_second_diff)
    st_drop, ch_drop = await http(
        w["port"],
        "GET",
        f"/api/v1/pm/workspaces/{wid}/work-attempts/{aid}/changes",
        tok=w["owner_token"],
    )
    monkeypatch.setattr(read_client, "invoke", orig_invoke)

    assert st_drop == 200
    assert ch_drop["completeness"]["status"] == "partial"
    assert "bat_unreachable" in ch_drop["completeness"]["reasons"]
    assert "diff_exceeded_pipe_limit" not in ch_drop["completeness"]["reasons"]
    assert ch_drop["summary"]["unavailable"] == ["src/second.py"]
    by_path = {f["path"]: f for f in ch_drop["summary"]["files"]}
    assert by_path["src/second.py"]["additions"] == "unknown"
    assert by_path["src/second.py"]["deletions"] == "unknown"

    # Because summary.reason == 'bat_unreachable' was not memoized, the next call recomputes live and succeeds.
    st_ok, ch_ok = await http(
        w["port"],
        "GET",
        f"/api/v1/pm/workspaces/{wid}/work-attempts/{aid}/changes",
        tok=w["owner_token"],
    )
    assert st_ok == 200
    assert ch_ok["memo_hit"] is False
    assert "bat_unreachable" not in ch_ok["completeness"]["reasons"]
    by_path_ok = {f["path"]: f for f in ch_ok["summary"]["files"]}
    assert by_path_ok["src/second.py"]["additions"] == 1

