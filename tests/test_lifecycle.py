"""Triage, Jev (optional), permissions, quota failover and gated cleanup (mock server only)."""

from __future__ import annotations

import pytest

from bat_agent_connector import channels, confinement, lifecycle, orchestrate, registry, triage
from bat_agent_connector.config import JevConfig
from bat_agent_connector.errors import ResourceReadOnly, WriteRefused
from bat_agent_connector.jev import Jev
from bat_agent_connector.operations import OperationError
from tests.conftest import adopt

# the demo checkout as the connector's own clone: merges into its main checkout are allowed
MANAGED_CLONE = {"managed_roots": ["/srv/demo"]}

QUOTA_MSG = (
    "You've hit your monthly spend limit · raise it at example.invalid/settings/usage · "
    "your weekly limit resets Sep 28, 4pm (America/New_York)"
)
NOW_MS = 1_790_000_000_000


def msg(i, role, text):
    return {"id": f"x{i}", "role": role, "content": text, "timestamp": NOW_MS + i * 1000}


# --------------------------------------------------------------------------- patterns
@pytest.mark.parametrize(
    "text",
    [
        QUOTA_MSG,
        "You've hit your limit · resets 5pm (UTC)",
        "Claude AI usage limit reached|1790000000",
        "Credit balance is too low",
        "Error: insufficient_quota - You exceeded your current quota",
        "5-hour limit reached ∙ resets 3am",
    ],
)
def test_quota_patterns(text):
    c = triage.classify_messages([msg(0, "user", "do it"), msg(1, "assistant", text)])
    assert c["state"] == "quota_exhausted" and c["source"] == "pattern" and c["confidence"] > 0.9
    assert c["evidence"]


def test_quota_resets_parsed():
    c = triage.classify_messages([msg(0, "user", "go"), msg(1, "assistant", QUOTA_MSG)])
    assert c["resets"].startswith("Sep 28, 4pm")
    c = triage.classify_messages([msg(0, "user", "go"), msg(1, "assistant", "Claude AI usage limit reached|1790000000")])
    assert c["resets"]


def test_other_states():
    base = [msg(0, "user", "go")]
    assert triage.classify_messages(base + [msg(1, "assistant", "API Error: 529 overloaded")])["state"] == (
        "rate_limited_transient"
    )
    assert triage.classify_messages(base, streaming=True)["state"] == "working"
    p = {"kind": "permission", "toolName": "Bash", "input_preview": "pytest"}
    assert triage.classify_messages(base, pending=p)["state"] == "waiting_permission"
    long_report = "Done. " + "All tests pass and the change is committed. " * 20
    assert triage.classify_messages(base + [msg(1, "assistant", long_report)])["state"] == "done_idle"
    amb = triage.classify_messages(base + [msg(1, "assistant", "Error: something odd failed to start")])
    assert amb["state"] == "error_other" and amb["ambiguous"]
    # quota message answered later by real work -> not exhausted
    later = base + [msg(1, "assistant", QUOTA_MSG), msg(2, "user", "continue"), msg(3, "assistant", long_report)]
    assert triage.classify_messages(later)["state"] == "done_idle"


# --------------------------------------------------------------------------- jev (optional)
async def test_jev_off_without_key(monkeypatch):
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    j = Jev(JevConfig())
    assert not j.enabled and j.status() == "no-api-key"
    assert await j.classify_state("x") is None
    c = {"state": "error_other", "ambiguous": True, "source": "pattern", "confidence": 0.5}
    assert (await triage.refine_with_jev(j, dict(c), [], "auto"))["source"] == "pattern"


async def test_jev_fail_open_and_validation(monkeypatch):
    monkeypatch.setenv("TYPESAFE_API_KEY", "test-key-not-real")
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    j = Jev(JevConfig(timeout_s=0.5))

    def boom(body, key):
        raise TimeoutError("slow")

    monkeypatch.setattr(j, "_post", boom)
    assert await j.classify_state("x") is None
    c = await triage.refine_with_jev(j, {"state": "error_other", "ambiguous": True, "source": "pattern"}, [], "auto")
    assert c["state"] == "error_other" and c["jev"] == "unavailable"

    probs = dict.fromkeys(("rate_limited_transient", "waiting_permission", "working", "done_idle", "error_other"), 0.0)
    good = {"answers": {"state": {"type": "choice", "choice": "quota_exhausted", "confidence": 0.93,
                                  "probabilities": {**probs, "quota_exhausted": 1.0}}}}
    monkeypatch.setattr(j, "_post", lambda b, k: good)
    c = await triage.refine_with_jev(j, {"state": "error_other", "ambiguous": True, "source": "pattern"}, [], "auto")
    assert c["state"] == "quota_exhausted" and c["source"] == "jev" and c["confidence"] == 0.93
    bad = {"answers": {"state": {"type": "choice", "choice": "nonsense", "confidence": 0.9, "probabilities": {}}}}
    monkeypatch.setattr(j, "_post", lambda b, k: bad)
    assert await j.classify_state("x") is None  # invalid answers are rejected, never trusted


def test_jev_config_disabled_flag(monkeypatch):
    monkeypatch.setenv("TYPESAFE_API_KEY", "test-key-not-real")
    assert Jev(JevConfig(enabled="false")).enabled is False


# --------------------------------------------------------------------------- triage over the mock
async def test_sessions_triage_quota(fleet_factory, mock, monkeypatch):
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    mock.states["sess-claude-0001"]["messages"].append(msg(99, "assistant", QUOTA_MSG))
    f = fleet_factory()
    r = await triage.sessions_triage(f, "h1", states=["quota_exhausted"])
    assert [s["session_id"] for s in r["sessions"]] == ["sess-claude-0001"]
    row = r["sessions"][0]
    assert row["source"] == "pattern" and "spend limit" in row["evidence"] and r["jev"] == "no-api-key"
    assert r["counts_by_state"]["working"] == 1  # the streaming codex session
    await f.close()


# --------------------------------------------------------------------------- failover
def _add_wt_claude(mock, sid="wt-claude-0007", managed=True):
    """A quota-stopped Claude worktree session; managed=True: the connector started it (registry record)."""
    mock.ws_doc["terminals"].append(
        {
            "id": sid,
            "workspaceId": "ws-1",
            "title": "Claude Agent",
            "agentPreset": "claude-code-worktree",
            "cwd": "/srv/demo/.bat-worktrees/abc",
            "worktreePath": "/srv/demo/.bat-worktrees/abc",
            "worktreeBranch": "bat/worktree-abc",
        }
    )
    mock.metas[sid] = {"cwd": "/srv/demo/.bat-worktrees/abc", "isStreaming": False}
    mock.states[sid] = {
        "isStreaming": False,
        "messages": [
            msg(0, "user", "ORIGINAL TASK: add the login API"),
            msg(1, "assistant", "Working on it; endpoints drafted."),
            msg(2, "user", "also add tests"),
            msg(3, "assistant", QUOTA_MSG),
        ],
    }
    mock.git_branch["/srv/demo/.bat-worktrees/abc"] = "bat/worktree-abc"
    mock.worktrees[sid] = {"worktreePath": "/srv/demo/.bat-worktrees/abc",
                           "branchName": "bat/worktree-abc", "sourceBranch": "main",
                           "diff": "", "merged": False, "mergedKind": "unknown"}
    mock.git_status["/srv/demo/.bat-worktrees/abc"] = [{"status": "M", "file": "api.py"}]
    if managed:
        adopt(sid, cwd="/srv/demo/.bat-worktrees/abc", origin_cwd="/srv/demo",
              worktree_path="/srv/demo/.bat-worktrees/abc", branch="bat/worktree-abc",
              agent_preset="claude-code-worktree", workspace_id="ws-1")
    return sid


@pytest.mark.parametrize("reply", [
    {"ok": True, "sessionId": "wrong-successor"}, {"ok": True},
])
async def test_failover_rejects_missing_or_mismatched_successor_ack(fleet_factory, mock, reply):
    sid = _add_wt_claude(mock)
    fleet = fleet_factory(writes=True, orchestrate=True, safety={"write_min_interval_s": 0})
    mock.handlers["claude:start-session"] = lambda params: reply
    try:
        with pytest.raises(WriteRefused, match="reserved session ID"):
            await lifecycle.session_failover(fleet, "h1", sid, confirm=True)
        assert not any(i["channel"] == "claude:send-message" for i in mock.invokes)
    finally:
        await fleet.close()


async def test_failover_rejects_host_worktree_branch_change(fleet_factory, mock):
    sid = _add_wt_claude(mock)
    fleet = fleet_factory(writes=True, orchestrate=True)
    mock.worktrees[sid]["branchName"] = "bat/worktree-different"
    try:
        with pytest.raises(WriteRefused, match="worktree branch"):
            await lifecycle.session_failover(fleet, "h1", sid, confirm=True)
        assert not any(i["channel"] == "claude:start-session" for i in mock.invokes)
    finally:
        await fleet.close()


async def test_failover_same_worktree(fleet_factory, mock):
    sid = _add_wt_claude(mock)
    f = fleet_factory(writes=True, orchestrate=True, default_permission_mode="allow_all")
    with pytest.raises(WriteRefused, match="confirm"):
        await lifecycle.session_failover(f, "h1", sid)
    dry = await lifecycle.session_failover(f, "h1", sid, dry_run=True)
    assert dry["dry_run"] and dry["same_worktree"] and "claude:start-session" not in mock.channels()
    r = await lifecycle.session_failover(f, "h1", sid, confirm=True)
    assert r["old_session_id"] == sid and r["new_session_id"] != sid and r["same_worktree"]
    assert r["branch"] == "bat/worktree-abc" and r["prompt_sent"] and not r["counts_toward_cap"]
    assert r["isolation"] == "legacy_shared_clone" and registry.get("h1", sid)["status"] == "superseded"
    assert "worktree:create" not in mock.channels()
    start = next(i for i in mock.invokes if i["channel"] == "claude:start-session")["params"]["options"]
    assert start["agentPreset"] == "codex-agent-worktree" and start["useWorktree"] is True
    assert start["worktreePath"] == "/srv/demo/.bat-worktrees/abc" and start["cwd"] == "/srv/demo"
    assert start["codexSandboxMode"] == "danger-full-access" and start["codexApprovalPolicy"] == "never"
    sent = next(i for i in mock.invokes if i["channel"] == "claude:send-message")["params"]["prompt"]
    assert "ORIGINAL TASK" in sent and "also add tests" in sent and "bat/worktree-abc" in sent
    assert "M api.py" in sent and "spend limit" in sent
    assert all(i["params"].get("sessionId") != sid for i in mock.invokes if i["channel"] == "claude:send-message")
    e = registry.get("h1", r["new_session_id"])
    assert e["failover_of"] == sid and e["status"] == "active" and e["worktree_path"].endswith("/abc")
    again = await lifecycle.session_failover(f, "h1", sid, confirm=True)
    assert again["skipped"] and again["new_session_id"] == r["new_session_id"]
    sends = len([i for i in mock.invokes if i["channel"] == "claude:send-message"])
    with pytest.raises(WriteRefused, match="reserved successor and handoff"):
        await lifecycle.session_failover(f, "h1", sid, confirm=True,
                                         successor_session_id="another-reservation",
                                         handoff_message_id="batc-another-handoff",
                                         handoff_command_id="another-command")
    assert len([i for i in mock.invokes if i["channel"] == "claude:send-message"]) == sends
    await f.close()


async def test_a10_task_failover_records_missing_options_without_changing_engine(fleet_factory, mock):
    sid = _add_wt_claude(mock)
    f = fleet_factory(writes=True, orchestrate=True, default_permission_mode="allow_all",
                      safety={"write_min_interval_s": 0})
    client = f.client("h1")
    invoke = client.invoke

    async def omit_permission_fields(channel, params=None, **kwargs):
        result = await invoke(channel, params, **kwargs)
        if channel == "claude:get-session-meta" and params["sessionId"] != sid:
            return {k: v for k, v in result.items() if k not in confinement.OPTION_KEYS}
        return result

    client.invoke = omit_permission_fields
    try:
        r = await lifecycle.session_failover(f, "h1", sid, confirm=True, task_id="task-test")
        assert r["prompt_sent"]
        assert r["confinement"]["verification"]["status"] == "unknown"
        assert r["confinement"]["level"] == "none"
        assert r["confinement"]["gap"] == "task_recipe_compatibility"
    finally:
        await f.close()


async def test_failover_of_a_bat_session_is_refused_before_any_write(fleet_factory, mock):
    sid = _add_wt_claude(mock, managed=False)  # the user's own BAT worktree session
    f = fleet_factory(writes=True, orchestrate=True)
    for kwargs in ({"dry_run": True}, {"confirm": True}, {"confirm": True, "force": True}):
        with pytest.raises(ResourceReadOnly, match="MANUAL_READ_ONLY"):
            await lifecycle.session_failover(f, "h1", sid, **kwargs)
    r = await lifecycle.session_failover(f, "h1", all_exhausted=True, confirm=True)
    # The person's session is skipped before the per-call cap, so it never takes a managed session's slot.
    assert r["exhausted_found"] == 1 and r["failovers"] == [] and not r["truncated_by_max_start_per_call"]
    assert r["skipped_read_only"] == [{"session_id": sid, "skipped": "read_only", "code": "MANUAL_READ_ONLY"}]
    assert not any(i["channel"] in channels.WRITE_CHANNELS | channels.ORCHESTRATE_CHANNELS for i in mock.invokes)
    assert registry.list_entries("h1") == []
    await f.close()


async def test_failover_refuses_non_exhausted_and_non_claude(fleet_factory, mock):
    adopt("sess-claude-0001")
    f = fleet_factory(writes=True, orchestrate=True, **MANAGED_CLONE)
    with pytest.raises(WriteRefused, match="does not look quota-exhausted"):
        await lifecycle.session_failover(f, "h1", "sess-claude-0001", confirm=True)
    with pytest.raises(WriteRefused, match="not a Claude"):
        await lifecycle.session_failover(f, "h1", "sess-codex-0002", confirm=True)
    await f.close()


async def test_failover_main_checkout_and_all_exhausted(fleet_factory, mock):
    mock.states["sess-claude-0001"]["messages"].append(msg(99, "assistant", QUOTA_MSG))
    adopt("sess-claude-0001")  # a connector session in the main checkout of a human clone
    f = fleet_factory(writes=True, orchestrate=True)
    r = await lifecycle.session_failover(f, "h1", all_exhausted=True, confirm=True)
    assert r["failovers"] == [] and r["skipped_read_only"][0]["code"] == "WORKDIR_NOT_MANAGED"  # legacy boundary
    assert "claude:start-session" not in mock.channels()
    await f.close()
    f = fleet_factory(writes=True, orchestrate=True, **MANAGED_CLONE)
    r = await lifecycle.session_failover(f, "h1", all_exhausted=True, confirm=True)
    assert r["count"] == 1 and r["exhausted_found"] == 1
    fo = r["failovers"][0]
    assert fo["same_worktree"] is False and fo["cwd"] == "/srv/demo"
    start = next(i for i in mock.invokes if i["channel"] == "claude:start-session")["params"]["options"]
    assert start["agentPreset"] == "codex-agent" and "useWorktree" not in start
    assert "codexSandboxMode" not in start  # host default_permission_mode = default
    await f.close()


async def test_failover_of_orchestrated_session_does_not_double_count(fleet_factory, mock):
    f = fleet_factory(writes=True, orchestrate=True, safety={"write_min_interval_s": 0})
    a = await orchestrate.session_start(f, "h1", "demo-project", "claude", confirm=True)
    b = await orchestrate.session_start(f, "h1", "demo-project", "claude", confirm=True)  # cap (2) reached
    sid = a["session_id"]
    mock.states[sid] = {"isStreaming": False, "messages": [msg(0, "user", "task"), msg(1, "assistant", QUOTA_MSG)]}
    r = await lifecycle.session_failover(f, "h1", sid, confirm=True)
    assert r["same_worktree"] and r["counts_toward_cap"] is False
    assert registry.get("h1", sid)["status"] == "superseded"
    assert len(registry.list_entries("h1", active_only=True)) == 2 and b["session_id"]
    await f.close()


# --------------------------------------------------------------------------- permissions
async def test_session_start_allow_all_defaults(fleet_factory, mock):
    f = fleet_factory(writes=True, orchestrate=True, default_permission_mode="allow_all")
    await orchestrate.session_start(f, "h1", "demo-project", "claude", confirm=True)
    o = next(i for i in mock.invokes if i["channel"] == "claude:start-session")["params"]["options"]
    assert o["permissionMode"] == "bypassPermissions"
    await f.close()
    f2 = fleet_factory(writes=True, orchestrate=True)
    mock.invokes.clear()
    await orchestrate.session_start(f2, "h1", "demo-project", "codex", confirm=True)
    o = next(i for i in mock.invokes if i["channel"] == "claude:start-session")["params"]["options"]
    assert "codexSandboxMode" not in o and "permissionMode" not in o
    await f2.close()


async def test_set_permissions_and_approve_pending(fleet_factory, mock):
    f = fleet_factory(writes=True)
    with pytest.raises(WriteRefused, match="allow-all"):
        await lifecycle.session_set_permissions(f, "h1", "sess-codex-0002", confirm=True)
    await f.close()
    mock.states["sess-codex-0002"]["pendingPermission"] = {"toolUseId": "tu1", "toolName": "Bash", "input": {"command": "pytest"}}
    mock.metas["sess-codex-0002"]["isStreaming"] = False
    f = fleet_factory(writes=True, default_permission_mode="allow_all")
    with pytest.raises(WriteRefused, match="LEGACY_PERMISSION_RAISE_DISABLED"):
        await lifecycle.approve_pending(f, "h1", confirm=True)
    assert "claude:resolve-permission" not in mock.channels()
    with pytest.raises(ResourceReadOnly, match="MANUAL_READ_ONLY"):
        await lifecycle.session_set_permissions(f, "h1", "sess-codex-0002", confirm=True, force=True)
    assert mock.perm_calls == []
    await f.close()
    adopt("sess-codex-0002")
    f = fleet_factory(writes=True, default_permission_mode="allow_all", **MANAGED_CLONE)
    dry = await lifecycle.approve_pending(f, "h1", dry_run=True)
    assert dry["count"] == 1 and "claude:resolve-permission" not in mock.channels()
    with pytest.raises(WriteRefused, match="LEGACY_PERMISSION_RAISE_DISABLED"):
        await lifecycle.approve_pending(f, "h1", confirm=True)
    assert "claude:resolve-permission" not in mock.channels() and not mock.perm_calls
    # The private explicit answer-only path retains the individual answer gate.
    r = await lifecycle.approve_pending(f, "h1", confirm=True, raise_to_allow_all=False)
    assert r["sessions"][0]["approved"] and not mock.perm_calls
    await f.close()


async def test_confined_sessions_stay_confined_on_an_allow_all_host(fleet_factory, mock):
    # A10: record CLI options honestly (Claude is only prompt gated); subsequent raises are refused.
    f = fleet_factory(writes=True, orchestrate=True, default_permission_mode="allow_all",
                      safety={"write_min_interval_s": 0}, **MANAGED_CLONE)
    a = await orchestrate.session_start(f, "h1", "demo-project", "claude", confirm=True, write_scope="confined")
    b = await orchestrate.session_start(f, "h1", "demo-project", "codex", confirm=True, write_scope="confined")
    starts = [i["params"]["options"] for i in mock.invokes if i["channel"] == "claude:start-session"]
    assert starts[0]["permissionMode"] == "default" and "codexSandboxMode" not in starts[0]
    assert (starts[1]["codexSandboxMode"], starts[1]["codexApprovalPolicy"]) == ("workspace-write", "on-request")
    assert a["permissions"] == b["permissions"] == "confined"
    ea, eb = registry.get("h1", a["session_id"]), registry.get("h1", b["session_id"])
    assert ea["write_scope"] == eb["write_scope"] == "confined" and ea["permission_mode_claude"] == "default"
    assert eb["agent_params"] == {"sandboxMode": "workspace-write", "approvalPolicy": "on-request"}
    with pytest.raises(WriteRefused, match="confined"):
        await lifecycle.session_set_permissions(f, "h1", b["session_id"], "allow_all", confirm=True)
    assert mock.perm_calls == []

    mock.states[b["session_id"]] = {"isStreaming": False, "messages": [msg(0, "user", "task")],
                                    "pendingPermission": {"toolUseId": "tu9", "toolName": "Bash",
                                                          "input": {"command": "rm -rf /home/operator/app"}}}
    with pytest.raises(WriteRefused, match="LEGACY_PERMISSION_RAISE_DISABLED"):
        await lifecycle.approve_pending(f, "h1", confirm=True)
    assert "claude:resolve-permission" not in mock.channels()

    mock.states[a["session_id"]] = {"isStreaming": False,
                                    "messages": [msg(0, "user", "task"), msg(1, "assistant", QUOTA_MSG)]}
    fo = await lifecycle.session_failover(f, "h1", a["session_id"], confirm=True)
    succ = [i["params"]["options"] for i in mock.invokes if i["channel"] == "claude:start-session"][-1]
    assert (succ["codexSandboxMode"], succ["codexApprovalPolicy"]) == ("workspace-write", "on-request")
    assert registry.get("h1", fo["new_session_id"])["write_scope"] == "confined" and fo["permissions"] == "confined"
    await f.close()


async def test_claude_mode_not_switched_mid_turn(fleet_factory, mock):
    adopt("sess-claude-0001")
    f = fleet_factory(writes=True, default_permission_mode="allow_all", **MANAGED_CLONE)
    mock.metas["sess-claude-0001"]["isStreaming"] = True
    with pytest.raises(lifecycle.TurnInFlight):
        await lifecycle.session_set_permissions(f, "h1", "sess-claude-0001", confirm=True)
    assert mock.perm_calls == []
    mock.metas["sess-claude-0001"]["isStreaming"] = False
    r = await lifecycle.session_set_permissions(f, "h1", "sess-claude-0001", confirm=True)
    assert r["mode"] == "allow_all"
    assert mock.perm_calls[0][0] == "claude:set-permission-mode"
    await f.close()


async def legacy_evaluate(fleet, host, **kwargs):
    """Legacy apply is always refused; preserve the prior decision tests as read-only evaluations."""
    with pytest.raises(OperationError, match="LEGACY_CLEANUP_DISABLED") as caught:
        await lifecycle.session_cleanup(fleet, host, **kwargs)
    assert caught.value.status == 409
    assert "resource-cleanup" in caught.value.message
    return await lifecycle.session_cleanup(fleet, host, **{**kwargs, "dry_run": True})


# --------------------------------------------------------------------------- cleanup
@pytest.mark.parametrize("entry", ["planner", "relay", "merge", "remove", "failover", "permissions", "approve", "verification"])
async def test_e01_legacy_mutations_require_confirmation_before_writes(fleet_factory, mock, entry):
    f = fleet_factory(writes=True, orchestrate=True, auto_cleanup=True,
                      default_permission_mode="allow_all", safety={"write_min_interval_s": 0})
    sid = "sess-claude-0001"
    calls = {
        "planner": lambda: lifecycle.fanout_plan_session(f, "h1", "demo-project", "plan", confirm=False),
        "relay": lambda: lifecycle.session_relay(f, "h1", "task", session_id=sid, start_if_missing=True, confirm=False),
        "merge": lambda: orchestrate.worktree_merge(f, "h1", sid, confirm=False),
        "remove": lambda: orchestrate.worktree_remove(f, "h1", sid, confirm=False),
        "failover": lambda: lifecycle.session_failover(f, "h1", sid, confirm=False),
        "permissions": lambda: lifecycle.session_set_permissions(f, "h1", sid, confirm=False),
        "approve": lambda: lifecycle.approve_pending(f, "h1", confirm=False),
        "verification": lambda: lifecycle.session_record_verification(f, "h1", sid, "0" * 40, "pytest", 0,
                                                                      "mock host", "tests.log", confirm=False),
    }
    before = len(mock.invokes)
    with pytest.raises(WriteRefused, match="confirm=true"):
        await calls[entry]()
    assert not any(i["channel"] in channels.WRITE_CHANNELS | channels.ORCHESTRATE_CHANNELS for i in mock.invokes[before:])
    assert registry.list_entries("h1") == []
    assert mock.metas[sid] is not None
    await f.close()


@pytest.mark.parametrize("confirm", [False, True])
async def test_e01_legacy_cleanup_disabled_apply_never_writes_with_auto_cleanup(fleet_factory, mock, confirm):
    f = fleet_factory(writes=True, orchestrate=True, auto_cleanup=True)
    before = len(mock.invokes)
    with pytest.raises(OperationError, match="LEGACY_CLEANUP_DISABLED") as caught:
        await lifecycle.session_cleanup(f, "h1", confirm=confirm, dry_run=False)
    assert caught.value.status == 409
    assert len(mock.invokes) == before and registry.list_entries("h1") == []
    await f.close()


@pytest.mark.parametrize("confirm", [False, True])
async def test_e01_legacy_cleanup_evaluation_never_rehydrates_worktrees(fleet_factory, mock, confirm):
    f = fleet_factory(writes=True, orchestrate=True, auto_cleanup=True,
                      safety={"write_min_interval_s": 0}, **MANAGED_CLONE)
    started = await _finished_wt(f, mock)
    sid = started["session_id"]
    mock.worktrees.pop(sid)  # BAT forgot the worktree; live cwd and the connector's creation record remain.
    entries, before = registry.list_entries("h1"), len(mock.invokes)
    result = await lifecycle.session_cleanup(f, "h1", confirm=confirm, dry_run=True, session_id=sid)
    assert not any(i["channel"] in channels.WRITE_CHANNELS | channels.ORCHESTRATE_CHANNELS for i in mock.invokes[before:])
    assert sid not in mock.worktrees and registry.list_entries("h1") == entries
    row = result["decisions"][0]
    assert row["decision"] == "ESCALATE" and "no worktree state" in row["reasons"][0]
    assert row["rehydrated"] is False
    await f.close()


async def _finished_wt(f, mock, kind="unknown", diff="", verified=True):
    r = await orchestrate.session_start(f, "h1", "demo-project", "codex", confirm=True)
    sid = r["session_id"]
    mock.worktrees[sid].update(mergedKind=kind, diff=diff)
    mock.states[sid] = {
        "isStreaming": False,
        "messages": [
            msg(0, "user", "Add feature X. Commit on your branch, run tests, report results."),
            {"id": "t1", "toolName": "Bash", "input": {"command": "uv run pytest -q"}, "status": "completed",
             "timestamp": NOW_MS + 2000},
            msg(3, "assistant", "Done: feature X implemented, 12 tests pass, committed on the branch."),
        ],
    }
    if verified:
        await lifecycle.session_record_verification(
            f, "h1", sid, "abc1234", "uv run pytest -q", 0,
            "mock host /srv/demo worktree, Python 3.12", "artifacts/test-run.log", confirm=True,
        )
    return r


async def test_shared_worktree_reviewer_never_owns_cleanup(fleet_factory, mock):
    f = fleet_factory(writes=True, orchestrate=True, auto_cleanup=True,
                      safety={"write_min_interval_s": 0})
    r = await _finished_wt(f, mock)
    registry.update("h1", r["session_id"], role="reviewer", lead_session_id="lead-session")
    decision = await lifecycle.session_cleanup(f, "h1", session_id=r["session_id"], dry_run=True)
    row = decision["decisions"][0]
    assert row["decision"] == "KEEP" and not row["remove_worktree"] and not row["stop"]
    await f.close()


async def test_legacy_cleanup_and_main_relay_skip_task_owned_sessions(fleet_factory, mock):
    f = fleet_factory(writes=True, orchestrate=True, auto_cleanup=True,
                      safety={"write_min_interval_s": 0})
    started = await _finished_wt(f, mock)
    sid = started["session_id"]
    registry.update("h1", sid, task_id="task-service-owned")
    decision = await legacy_evaluate(f, "h1", session_id=sid,
                                               confirm=True, dry_run=False, min_idle_s=0)
    assert decision["decisions"][0]["decision"] == "KEEP"
    assert not any(i["channel"] in {"worktree:remove", "claude:stop-session"}
                   and i["params"].get("sessionId") == sid for i in mock.invokes)
    # Legacy task lookup records are readable evidence, not adoptable controller ownership.
    with registry._locked(registry.registry_path()):
        registry._write(registry.registry_path(), registry.list_entries() + [
            {"host": "h1", "session_id": old, "task_id": "task-service-owned", "status": "active"}
            for old in ("sess-claude-0001", "sess-codex-0002")])
    assert await lifecycle.main_session(f, "h1", "demo-project") is None
    await f.close()


async def test_cleanup_clean_only_and_apply(fleet_factory, mock, monkeypatch):
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    f = fleet_factory(writes=True, orchestrate=True, safety={"write_min_interval_s": 0})
    r = await _finished_wt(f, mock)
    d = await lifecycle.session_cleanup(f, "h1", dry_run=True)
    row = d["decisions"][0]
    assert row["decision"] == "CLEAN_ONLY" and "no changes" in row["reasons"][0]
    assert not any(i["channel"] in ("worktree:remove", "claude:stop-session") for i in mock.invokes)
    with pytest.raises(OperationError, match="LEGACY_CLEANUP_DISABLED"):
        await lifecycle.session_cleanup(f, "h1", confirm=True, dry_run=False)
    await f.close()
    f = fleet_factory(writes=True, orchestrate=True, auto_cleanup=True, safety={"write_min_interval_s": 0})
    d = await legacy_evaluate(f, "h1", confirm=True, dry_run=False)
    row = d["decisions"][0]
    assert row["decision"] == "CLEAN_ONLY" and not row.get("actions")
    assert "worktree:remove" not in mock.channels() and "claude:stop-session" not in mock.channels()
    assert registry.get("h1", r["session_id"])["status"] == "active"
    await f.close()


async def test_cleanup_merge_needs_jev(fleet_factory, mock, monkeypatch):
    diff = "diff --git a/x.py b/x.py\n+++ b/x.py\n+print('x')\n"
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    f = fleet_factory(writes=True, orchestrate=True, auto_cleanup=True, safety={"write_min_interval_s": 0},
                      **MANAGED_CLONE)
    await _finished_wt(f, mock, "ahead", diff)
    d = await lifecycle.session_cleanup(f, "h1", dry_run=True)
    assert d["decisions"][0]["decision"] == "ESCALATE" and "Jev unavailable" in d["escalation_summary"]

    async def fake_gate(self, task, final, diff_excerpt, tests):
        return {"claims_done": 0.95, "diff_verdict": "safe_complete", "diff_confidence": 0.9, "tests_ok": 0.9}

    monkeypatch.setattr(Jev, "merge_gate", fake_gate)
    d = await legacy_evaluate(f, "h1", confirm=True, dry_run=False)
    row = d["decisions"][0]
    assert row["decision"] == "MERGE_AND_CLEAN" and not row.get("actions")
    assert "worktree:merge" not in mock.channels() and "claude:stop-session" not in mock.channels()
    await f.close()


async def test_cleanup_requires_commit_bound_execution_not_jev(fleet_factory, mock, monkeypatch):
    diff = "diff --git a/x.py b/x.py\n+++ b/x.py\n+print('x')\n"
    f = fleet_factory(writes=True, orchestrate=True, auto_cleanup=True, safety={"write_min_interval_s": 0},
                      **MANAGED_CLONE)
    r = await _finished_wt(f, mock, "ahead", diff, verified=False)

    async def optimistic_gate(self, task, final, diff_excerpt, tests):
        return {"claims_done": 0.99, "diff_verdict": "safe_complete", "diff_confidence": 0.99, "tests_ok": 0.99}

    monkeypatch.setattr(Jev, "merge_gate", optimistic_gate)
    d = await lifecycle.session_cleanup(f, "h1", session_id=r["session_id"], dry_run=True)
    assert d["decisions"][0]["decision"] == "ESCALATE"
    assert not d["decisions"][0]["gates"]["verified_candidate"]
    mock.states[r["session_id"]]["messages"][-1] = msg(3, "assistant", "BAT-STATUS: MILESTONE finished")
    d = await lifecycle.session_cleanup(f, "h1", session_id=r["session_id"], dry_run=True)
    assert d["decisions"][0]["decision"] == "ESCALATE"
    assert d["decisions"][0]["gates"]["agent_claims_complete"]

    await lifecycle.session_record_verification(
        f, "h1", r["session_id"], "abc1234", "uv run pytest -q", 0,
        "mock host, Python 3.12", "artifacts/pytest.log", confirm=True,
    )
    d = await lifecycle.session_cleanup(f, "h1", session_id=r["session_id"], dry_run=True)
    assert d["decisions"][0]["decision"] == "MERGE_AND_CLEAN"
    assert d["decisions"][0]["gates"]["verified_candidate"]

    wt = r["worktree_path"]
    mock.git_logs = {wt: [{"hash": "def5678"}]}
    d = await lifecycle.session_cleanup(f, "h1", session_id=r["session_id"], dry_run=True)
    assert d["decisions"][0]["decision"] == "ESCALATE"
    assert not d["decisions"][0]["gates"]["verified_candidate"]
    await f.close()


async def test_cleanup_apply_never_evaluates_or_merges_changed_candidate(fleet_factory, mock, monkeypatch):
    f = fleet_factory(writes=True, orchestrate=True, auto_cleanup=True, safety={"write_min_interval_s": 0},
                      **MANAGED_CLONE)
    r = await _finished_wt(f, mock, "ahead", "diff --git a/x b/x\n+++ b/x\n+code\n")
    async def unexpected_evaluate(*args, **kwargs):
        raise AssertionError("disabled apply must not evaluate or mutate")
    monkeypatch.setattr(lifecycle, "_evaluate", unexpected_evaluate)
    before = len(mock.invokes)
    with pytest.raises(OperationError, match="LEGACY_CLEANUP_DISABLED"):
        await lifecycle.session_cleanup(f, "h1", session_id=r["session_id"], dry_run=False, confirm=True)
    assert len(mock.invokes) == before
    await f.close()


async def test_cleanup_escalates_risky_and_keeps_working(fleet_factory, mock, monkeypatch):
    secret = "diff --git a/cfg.py b/cfg.py\n+++ b/cfg.py\n+AWS = 'AKIA" + "QX7ZK2LMN4PR8TVW'\n"
    f = fleet_factory(writes=True, orchestrate=True, safety={"write_min_interval_s": 0})
    await _finished_wt(f, mock, "ahead", secret)
    r2 = await orchestrate.session_start(f, "h1", "demo-project", "codex", confirm=True)
    mock.metas[r2["session_id"]]["isStreaming"] = True
    d = await lifecycle.session_cleanup(f, "h1", dry_run=True)
    by = {x["session_id"]: x for x in d["decisions"]}
    assert by[r2["session_id"]]["decision"] == "KEEP"
    esc = [x for x in d["decisions"] if x["decision"] == "ESCALATE"]
    assert esc and "secret" in esc[0]["reasons"][0]
    await f.close()


async def test_cleanup_never_merges_into_a_human_checkout(fleet_factory, mock, monkeypatch):
    f = fleet_factory(writes=True, orchestrate=True, auto_cleanup=True, safety={"write_min_interval_s": 0})
    r = await _finished_wt(f, mock, "ahead", "diff --git a/x b/x\n+++ b/x\n+code\n")

    async def confident_gate(self, task, final, diff_excerpt, tests):
        return {"claims_done": 0.95, "diff_verdict": "safe_complete", "diff_confidence": 0.95, "tests_ok": 0.95}

    monkeypatch.setattr(Jev, "merge_gate", confident_gate)
    d = await legacy_evaluate(f, "h1", session_id=r["session_id"], dry_run=False, confirm=True)
    row = d["decisions"][0]
    assert row["decision"] == "ESCALATE" and "human checkout" in row["reasons"][-1]
    assert "worktree:merge" not in mock.channels() and "claude:stop-session" not in mock.channels()
    await f.close()


async def test_cleanup_keeps_bat_sessions_and_legacy_boundaries(fleet_factory, mock):
    # a legacy registry: a failover successor of the user's BAT session, plus a connector session in his checkout
    sid = _add_wt_claude(mock, managed=False)
    with registry._locked(registry.registry_path()):
        registry._write(registry.registry_path(), registry.list_entries() + [{
            "host": "h1", "session_id": "succ-0001", "status": "active", "cwd": "/srv/demo/.bat-worktrees/abc",
            "worktree_path": "/srv/demo/.bat-worktrees/abc", "origin_cwd": "/srv/demo", "failover_of": sid,
            "shares_worktree_with": sid, "handoff_status": "sent"}])
    mock.metas["succ-0001"] = {"cwd": "/srv/demo/.bat-worktrees/abc", "isStreaming": False}
    adopt("sess-codex-0002")
    f = fleet_factory(writes=True, orchestrate=True, auto_cleanup=True, safety={"write_min_interval_s": 0})
    d = await legacy_evaluate(f, "h1", confirm=True, dry_run=False, min_idle_s=0)
    by = {x["session_id"]: x for x in d["decisions"]}
    assert by[sid]["decision"] == "KEEP" and "manual" in by[sid]["reasons"][0]
    assert by["succ-0001"]["decision"] == "KEEP" and by["succ-0001"]["api_access"] == "read_only"
    assert by["sess-codex-0002"]["decision"] == "KEEP" and "legacy boundary" in by["sess-codex-0002"]["reasons"][0]
    assert not any(i["channel"] in channels.WRITE_CHANNELS | channels.ORCHESTRATE_CHANNELS for i in mock.invokes)
    await f.close()


async def test_cleanup_stops_superseded_claude_after_failover(fleet_factory, mock):
    sid = _add_wt_claude(mock)
    f = fleet_factory(writes=True, orchestrate=True, auto_cleanup=True, safety={"write_min_interval_s": 0})
    fo = await lifecycle.session_failover(f, "h1", sid, confirm=True)
    d = await lifecycle.session_cleanup(f, "h1", dry_run=True)
    by = {x["session_id"]: x for x in d["decisions"]}
    assert by[sid]["decision"] == "CLEAN_ONLY" and by[sid]["stop"] and not by[sid]["remove_worktree"]
    new = by[fo["new_session_id"]]
    assert new["decision"] in ("KEEP", "ESCALATE")  # dirty shared worktree is never removed
    d = await legacy_evaluate(f, "h1", confirm=True, dry_run=False, session_id=sid)
    assert d["decisions"][0]["stop"] and not d["decisions"][0].get("actions")
    assert "claude:stop-session" not in mock.channels()
    assert "worktree:remove" not in mock.channels()
    await f.close()


def test_redact_secrets_in_handoff():
    from bat_agent_connector.redact import redact_secrets

    hook = "https://discord.com/api/webhooks/123456789/" + "A" * 60
    t = redact_secrets(f"add {hook} and token = {'x' * 30} ghp_{'b' * 36}")
    assert "AAAA" not in t and "xxxx" not in t and "bbbb" not in t
    assert "discord.com/api/webhooks/123456789/[REDACTED]" in t
    p = lifecycle.build_handoff_prompt(
        old_sid="s", workspace="w", cwd="/srv/w", same_worktree=False, branch="main",
        first_prompt=f"post to {hook}", last_prompt=None, recent=[], git={}, evidence="limit",
    )
    assert "AAAA" not in p and "[REDACTED]" in p


def test_decision_alias():
    assert lifecycle.normalize_decision("escalate_to_ted") == "ESCALATE"
    assert "ESCALATE" in lifecycle.DECISIONS


def test_handoff_custom_instructions():
    p = lifecycle.build_handoff_prompt(
        old_sid="s", workspace="w", cwd="/srv/w", same_worktree=True, branch="b",
        first_prompt="task", last_prompt=None, recent=[], git={}, evidence="limit",
        instructions="Only commit the work in progress.",
    )
    assert "Only commit the work in progress." in p and "How to proceed" not in p
    assert "Continue the work" not in p


async def test_codex_model_default_and_archive_cleanup(fleet_factory, mock, monkeypatch):
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    f = fleet_factory(writes=True, orchestrate=True, auto_cleanup=True, codex_model="gpt-x-test", orchestrate_max_sessions=8,
                      safety={"write_min_interval_s": 0})
    await orchestrate.session_start(f, "h1", "demo-project", "codex", confirm=True)
    o = next(i for i in mock.invokes if i["channel"] == "claude:start-session")["params"]["options"]
    assert o["model"] == "gpt-x-test"
    mock.invokes.clear()
    await orchestrate.session_start(f, "h1", "demo-project", "claude", confirm=True)
    o = next(i for i in mock.invokes if i["channel"] == "claude:start-session")["params"]["options"]
    assert o.get("model") != "gpt-x-test"
    # an ahead branch marked archive-only is cleaned (branch kept), never merged
    r = await _finished_wt(f, mock, kind="ahead", diff="diff --git a/x b/x\n+++ b/x\n+wip\n")
    registry.update("h1", r["session_id"], cleanup_policy="archive")
    d = await legacy_evaluate(f, "h1", session_id=r["session_id"], confirm=True, dry_run=False)
    row = d["decisions"][0]
    assert row["decision"] == "CLEAN_ONLY" and "archive" in row["reasons"][0]
    assert "worktree:merge" not in mock.channels()
    assert "worktree:remove" not in mock.channels()
    await f.close()


async def test_failover_codex_model_and_instructions(fleet_factory, mock):
    sid = _add_wt_claude(mock)
    f = fleet_factory(writes=True, orchestrate=True, codex_model="gpt-x-test")
    r = await lifecycle.session_failover(f, "h1", sid, confirm=True, instructions="Only commit the WIP.",
                                         archive_only=True)
    start = next(i for i in mock.invokes if i["channel"] == "claude:start-session")["params"]["options"]
    assert start["model"] == "gpt-x-test" and r["archive_only"]
    sent = next(i for i in mock.invokes if i["channel"] == "claude:send-message")["params"]["prompt"]
    assert "Only commit the WIP." in sent
    assert registry.get("h1", r["new_session_id"])["cleanup_policy"] == "archive"
    await f.close()


ZH_DONE = (
    "已完成並提交至分支 `bat/worktree-df43e6d3`。\n\nCommit：`6e15a05 phase7: add guarded watch reports`\n\n"
    "驗證全部通過：\n- `cargo test --workspace`\n\n工作目錄乾淨。測試未向真實 Discord webhook 發送請求。"
)


@pytest.mark.parametrize(
    "text,done",
    [
        (ZH_DONE, True),
        ("完成，已提交 commit a1b2c3d，全部通過。", True),
        ("Done: implemented X, all tests pass. Commit `deadbee` on bat/wt-1.", True),
        ("已完成大部分，但尚未提交；需要你確認要用哪個 API。", False),
        ("實作完成 80%，仍失敗 2 個測試，commit 1234abcd 為暫存。", False),
        ("已完成並提交。", False),  # no commit named: Jev decides alone
        ("Committed `abc1234`, but should I also migrate the DB?", False),
        ("I will commit next.", False),
        ("已完成並提交 commit `9f8e7d6`，驗證全部通過。尚未 push。", True),  # not pushed is the normal BAT flow
    ],
)
def test_completion_markers_language_independent(text, done):
    assert lifecycle.completion_markers(text)["claims_done"] is done


def test_apply_markers_lifts_low_jev_score_only_with_markers():
    g = lifecycle._apply_markers({"claims_done": 0.53}, ZH_DONE)
    assert g["claims_done"] == 0.9 and g["claims_done_jev"] == 0.53 and g["markers"]["commit"] == "6e15a05"
    g = lifecycle._apply_markers({"claims_done": 0.53}, "已完成大部分，但尚未提交。commit 1234abc")
    assert g["claims_done"] == 0.53
    assert lifecycle._apply_markers(None, ZH_DONE) is None


def test_final_output_skips_bat_system_notices():
    msgs = [msg(0, "assistant", ZH_DONE), msg(1, "system", "BAT ignored 1 late event from an older Codex turn.")]
    assert lifecycle._final_output(msgs).startswith("已完成")


async def test_jev_merge_gate_prompt_is_language_neutral(monkeypatch):
    seen = {}

    async def fake_ask(self, state, questions):
        seen.update(questions=questions, state=state)
        return None

    monkeypatch.setattr(Jev, "ask", fake_ask)
    await Jev(JevConfig()).merge_gate("", ZH_DONE, "diff", "{}")
    ins = seen["questions"]["claims_done"]["instructions"]
    assert "any language" in ins and "Chinese" in ins and "may be empty" in ins
    assert seen["state"]["final_output"].startswith("已完成")


async def test_cleanup_merges_zh_tw_completion(fleet_factory, mock, monkeypatch):
    diff = "diff --git a/x.rs b/x.rs\n+++ b/x.rs\n+fn x() {}\n"
    f = fleet_factory(writes=True, orchestrate=True, auto_cleanup=True, safety={"write_min_interval_s": 0},
                      **MANAGED_CLONE)
    r = await _finished_wt(f, mock, "ahead", diff)
    mock.states[r["session_id"]]["messages"][-1] = msg(3, "assistant", ZH_DONE)

    async def zh_blind_gate(self, task, final, diff_excerpt, tests):
        return {"claims_done": 0.53, "diff_verdict": "safe_complete", "diff_confidence": 0.95, "tests_ok": 0.9}

    monkeypatch.setattr(Jev, "merge_gate", zh_blind_gate)
    d = await lifecycle.session_cleanup(f, "h1", dry_run=True)
    row = d["decisions"][0]
    assert row["decision"] == "MERGE_AND_CLEAN", row
    assert row["jev"]["claims_done_jev"] == 0.53
    await f.close()


async def test_cleanup_rebuilds_empty_branch_diff(fleet_factory, mock, monkeypatch):
    """BAT returns an empty worktree diff when git's output exceeds a pipe buffer; rebuild it per commit/file."""
    f = fleet_factory(writes=True, orchestrate=True, auto_cleanup=True, safety={"write_min_interval_s": 0},
                      **MANAGED_CLONE)
    r = await _finished_wt(f, mock, "ahead", "")
    wt = mock.worktrees[r["session_id"]]["worktreePath"]
    mock.git_logs = {wt: [{"hash": "c2c2c2c2"}, {"hash": "c1c1c1c1"}, {"hash": "abc1234"}]}
    await lifecycle.session_record_verification(
        f, "h1", r["session_id"], "c2c2c2c2", "uv run pytest -q", 0,
        "mock host /srv/demo worktree, Python 3.12", "artifacts/test-run-2.log", confirm=True,
    )
    mock.commit_files = {
        "c1c1c1c1": [{"status": "M", "file": "src/x.rs"}],
        "c2c2c2c2": [{"status": "A", "file": "assets/big.json"}],
    }
    mock.commit_diffs = {("c1c1c1c1", "src/x.rs"): "diff --git a/src/x.rs b/src/x.rs\n+++ b/src/x.rs\n+fn x() {}\n"}
    seen = {}

    async def gate(self, task, final, diff_excerpt, tests):
        seen["diff"] = diff_excerpt
        return {"claims_done": 0.95, "diff_verdict": "safe_complete", "diff_confidence": 0.9, "tests_ok": 0.9}

    monkeypatch.setattr(Jev, "merge_gate", gate)
    d = await lifecycle.session_cleanup(f, "h1", dry_run=True)
    row = d["decisions"][0]
    assert row["decision"] == "MERGE_AND_CLEAN", row
    assert "+fn x() {}" in seen["diff"] and "assets/big.json" in seen["diff"]
    fb = row["diff_fallback"]
    assert fb["commits"] == ["c1c1c1c1", "c2c2c2c2"] and fb["unavailable"] == ["assets/big.json"]
    assert row["diff_stats"]["files"] == 2
    # main's HEAD not on the branch: no rebuild, Jev sees nothing and stays unsure
    mock.git_logs = {wt: [{"hash": "c2c2c2c2"}]}
    d = await lifecycle.session_cleanup(f, "h1", dry_run=True)
    assert d["decisions"][0]["diff_fallback"]["commits"] == [] and seen["diff"] == ""
    await f.close()


def test_placeholder_secrets_do_not_trip_risk_check():
    fake = 'let tok' + 'en = "' + "abcdefghijklmnopqrstuvwxyz" + '0123456789.ABC_def-ghi";\n'
    hook = '"https://discord.com/api/webhooks/123456/{token}"\n'
    diff = "diff --git a/t.rs b/t.rs\n+++ b/t.rs\n+" + fake + "+" + hook + '+api_key = "your-api-key-here"\n'
    assert lifecycle.risk_checks(diff) == []
    real = "diff --git a/c.py b/c.py\n+++ b/c.py\n+tok" + "en = 'Qm9vN3kLp2" + "XzR7tVw4Yh8JdS'\n"
    assert "secret" in lifecycle.risk_checks(real)[0]
    assert "secret" in lifecycle.risk_checks("+AWS = 'AKIA" + "QX7ZK2LMN4PR8TVW'\n")[0]
