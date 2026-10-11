"""Resource policy: sessions created in BAT are read-only through every entry point (mock server only).

Covers the plan's acceptance scenarios A01 (HTTP/MCP/CLI writes to manual or unknown resources are refused
before any BAT mutation, force/bulk/old entry points included) and A02 (a connector record whose session,
folder, link or worktree points into a human checkout grants nothing).
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from bat_agent_connector import cli, lifecycle, orchestrate, registry, resource_policy, service
from bat_agent_connector.channels import GUARDED_CHANNELS, ORCHESTRATE_CHANNELS, WRITE_CHANNELS
from bat_agent_connector.config import parse_config
from bat_agent_connector.errors import ConfigError, ResourceReadOnly
from bat_agent_connector.mcp_server import build_server
from bat_agent_connector.resource_policy import BAT_WRITE_CHANNELS, BY_ACTION, MUTATIONS
from tests import test_api_v1 as api
from tests.conftest import adopt, make_config

daemon, served = api.daemon, api.served

MANUAL = "sess-claude-0001"  # a tab the user opened in BAT, loaded
MANUAL_UNLOADED = "sess-unload-0003"
UNKNOWN = "uncertain-0009"  # connector reservation whose BAT start was never acknowledged
WT = "/srv/demo/.bat-worktrees/0000abcd"
SRC = Path(__file__).resolve().parents[1] / "src" / "bat_agent_connector"


def write_frames(mock) -> list[dict]:
    return [i for i in mock.invokes if i["channel"] in BAT_WRITE_CHANNELS]


def all_tiers(fleet_factory, **kw):
    return fleet_factory(writes=True, orchestrate=True, tabs=True, auto_cleanup=True,
                         default_permission_mode="allow_all", safety={"write_min_interval_s": 0}, **kw)


def add_unknown_session():
    registry.reserve("h1", {"session_id": UNKNOWN, "cwd": WT, "worktree_path": WT, "origin_cwd": "/srv/demo",
                            "agent_preset": "claude-code-worktree", "workspace_id": "ws-1"}, 8)
    registry.update("h1", UNKNOWN, status="uncertain")


def add_managed_wt(mock, sid="managed-0001", **row):
    """A Claude session the connector started in its own worktree (registry record + BAT state)."""
    fields = {"cwd": WT, "worktree_path": WT, "origin_cwd": "/srv/demo", "branch": "bat/worktree-0000abcd",
              "agent_preset": "claude-code-worktree", "workspace_id": "ws-1", **row}
    adopt(sid, **fields)
    mock.metas[sid] = {"cwd": WT, "isStreaming": False, "numTurns": 1}
    mock.states[sid] = {"isStreaming": False, "messages": [], "pendingAskUser": None, "pendingPermission": None}
    mock.worktrees[sid] = {"worktreePath": WT, "branchName": "bat/worktree-0000abcd", "sourceBranch": "main",
                           "diff": "", "merged": False, "mergedKind": "ahead"}
    return sid


# --------------------------------------------------------------------------- the mutation table
def test_mutation_table_classifies_every_bat_write_channel():
    covered = set().union(*(m.channels for m in MUTATIONS if m.via == "bat"))
    assert covered == set(WRITE_CHANNELS | ORCHESTRATE_CHANNELS | GUARDED_CHANNELS)
    assert all(m.entry_points and m.rule for m in MUTATIONS)
    assert len({m.action for m in MUTATIONS}) == len(MUTATIONS)


def test_only_the_policy_mints_write_grants():
    import re
    minting = re.compile(r"WriteGrant\(|_create_grant\(|replace\(\s*grant")
    offenders = [p.name for p in SRC.glob("*.py")
                 if p.name != "resource_policy.py" and minting.search(p.read_text())]
    assert offenders == []


def test_a_session_grant_never_covers_a_frame_without_its_session_id(controller_context):
    adopt("s-1")
    row = registry.get("h1", "s-1")
    ownership = {key: row[key] for key in ("controller_authority", "controller_incarnation")}
    grant = resource_policy.WriteGrant("h1", "session.send", "s-1", BY_ACTION["session.send"].channels, **ownership)
    resource_policy.check_grant(grant, "h1", "claude:send-message", {"sessionId": "s-1"})
    for params in ({}, None, {"sessionId": "s-2"}):
        with pytest.raises(ResourceReadOnly, match="GRANT_MISMATCH"):
            resource_policy.check_grant(grant, "h1", "claude:send-message", params)
    tab = resource_policy.WriteGrant("h1", "workspace.register_tab", "s-1", frozenset({"workspace:save"}), **ownership)
    resource_policy.check_grant(tab, "h1", "workspace:save", {"profileId": "default"})


# --------------------------------------------------------------------------- A01: every entry point
SESSION_WRITES = {
    "send": lambda f, sid: service.session_send(f, "h1", sid, "hi", confirm=True, queue=True),
    "continue": lambda f, sid: service.session_continue(f, "h1", sid, confirm=True, queue=True),
    "interrupt_hard": lambda f, sid: service.session_interrupt(f, "h1", sid, "hard", confirm=True),
    "answer": lambda f, sid: service.session_answer(f, "h1", sid, confirm=True, answers=["yes"]),
    "permissions_force": lambda f, sid: lifecycle.session_set_permissions(f, "h1", sid, confirm=True, force=True),
    "merge": lambda f, sid: orchestrate.worktree_merge(f, "h1", sid, confirm=True),
    "remove_all_overrides": lambda f, sid: orchestrate.worktree_remove(
        f, "h1", sid, confirm=True, delete_branch=True, allow_unmerged=True, discard_uncommitted=True),
    "failover_force": lambda f, sid: lifecycle.session_failover(f, "h1", sid, confirm=True, force=True),
}


@pytest.mark.parametrize("target,code", [(MANUAL, "MANUAL_READ_ONLY"), (MANUAL_UNLOADED, "MANUAL_READ_ONLY"),
                                         (UNKNOWN, "UNKNOWN_READ_ONLY")])
@pytest.mark.parametrize("op", sorted(SESSION_WRITES))
async def test_session_writes_refused_before_any_frame(fleet_factory, mock, target, code, op):
    add_unknown_session()
    mock.states[MANUAL]["pendingAskUser"] = {"toolUseId": "tu1", "questions": [{"question": "Q?"}]}
    mock.states[MANUAL]["messages"].append({"id": "q", "role": "assistant", "timestamp": 1_790_000_030_000,
                                            "content": "You've hit your limit · resets 5pm (UTC)"})
    f = all_tiers(fleet_factory)
    with pytest.raises(ResourceReadOnly) as ei:
        await SESSION_WRITES[op](f, target)
    assert ei.value.code == code
    assert write_frames(mock) == []
    assert not any(e.get("phase") == "attempt" for e in _audit())
    await f.close()


def _audit() -> list[dict]:
    from bat_agent_connector.safety import audit_path

    try:
        return [json.loads(x) for x in audit_path().read_text().splitlines()]
    except OSError:
        return []


async def test_bulk_and_relay_paths_skip_bat_sessions(fleet_factory, mock):
    mock.states["sess-codex-0002"]["pendingPermission"] = {"toolUseId": "p1", "toolName": "Bash", "input": {}}
    mock.metas["sess-codex-0002"]["isStreaming"] = False
    add_unknown_session()
    f = all_tiers(fleet_factory)
    approved = await lifecycle.approve_pending(f, "h1", confirm=True, raise_to_allow_all=False)
    assert approved["count"] == 1 and approved["sessions"][0]["skipped"] == "read_only"
    relayed = await lifecycle.session_relay(f, "h1", "do it", session_id=MANUAL, confirm=True)
    assert relayed["sent"] is False and relayed["read_only_code"] == "MANUAL_READ_ONLY"
    main = await lifecycle.session_relay(f, "h1", "do it", workspace="demo-project", confirm=True)
    assert main["sent"] is False and main["no_session"]
    swept = await lifecycle.session_cleanup(f, "h1", dry_run=True, min_idle_s=0)
    assert {d["decision"] for d in swept["decisions"]} <= {"KEEP"}
    assert write_frames(mock) == []
    await f.close()


async def test_mcp_tools_refuse_bat_sessions(mock, served, monkeypatch):
    d, port = served
    monkeypatch.setenv("BATC_TASK_URL", f"http://127.0.0.1:{port}/rpc")
    monkeypatch.setenv("BATC_API_TOKEN", api.token(d, "policy-test", "observe", "operate", "start"))
    server, fleet = build_server(make_config(mock, writes=True, orchestrate=True))
    for tool, args in (("session_send", {"text": "hi", "confirm": True}),
                       ("session_interrupt", {"confirm": True, "mode": "hard"}),
                       ("worktree_remove", {"confirm": True, "discard_uncommitted": True}),
                       ("session_failover", {"confirm": True, "force": True})):
        try:
            res = await server.call_tool(tool, {"host": "h1", "session_id": MANUAL, **args})
            text = json.dumps(res.model_dump() if hasattr(res, "model_dump") else res, default=str)
        except Exception as e:  # noqa: BLE001 - the SDK may raise the tool error instead of returning it
            text = str(e)
        assert "MANUAL_READ_ONLY" in text, (tool, text)
    policy = await server.call_tool("session_policy", {"host": "h1", "session_id": MANUAL})
    assert "MANUAL_READ_ONLY" in json.dumps(policy.model_dump() if hasattr(policy, "model_dump") else policy,
                                            default=str)
    assert write_frames(mock) == []
    await fleet.close()


async def test_cli_refuses_bat_sessions(mock, tmp_path, capsys, served, monkeypatch):
    d, port = served
    monkeypatch.setenv("BATC_TASK_URL", f"http://127.0.0.1:{port}/rpc")
    monkeypatch.setenv("BATC_API_TOKEN", api.token(d, "policy-test", "operate", "start"))
    cfg = tmp_path / "hosts.toml"
    cfg.write_text(f'[hosts.h1]\nurl = "{mock.url}"\nfingerprint = "{mock.fingerprint}"\n'
                   'token_ref = "env:BATC_TEST_TOKEN"\nwrites = true\norchestrate = true\n')
    for argv in (["send", "h1", MANUAL, "hi", "--confirm"],
                 ["interrupt", "h1", MANUAL, "--mode", "hard", "--confirm"],
                 ["remove-worktree", "h1", MANUAL, "--confirm"]):
        rc = await asyncio.to_thread(cli.main, ["--config", str(cfg), *argv])
        assert rc == 1
        assert "MANUAL_READ_ONLY" in capsys.readouterr().err
    assert write_frames(mock) == []


# --------------------------------------------------------------------------- A02: binding mismatches
@pytest.mark.parametrize("case,code", [
    ("meta_cwd_in_main_checkout", "BINDING_MISMATCH"),
    ("worktree_link_to_main_checkout", "BINDING_MISMATCH"),
    ("bat_tracks_another_worktree", "BINDING_MISMATCH"),
    ("tab_points_elsewhere", "BINDING_MISMATCH"),
    ("record_cwd_is_main_checkout", "WORKDIR_NOT_MANAGED"),
    ("record_worktree_outside_bat_worktrees", "WORKDIR_NOT_MANAGED"),
    ("shares_a_bat_session_worktree", "WORKDIR_NOT_MANAGED"),
])
async def test_connector_record_pointing_at_a_human_checkout_grants_nothing(fleet_factory, mock, case, code):
    sid = "managed-0001"
    if case == "record_cwd_is_main_checkout":
        adopt(sid, workspace_id="ws-1")
        mock.metas[sid] = {"cwd": "/srv/demo", "isStreaming": False}
    elif case == "record_worktree_outside_bat_worktrees":
        add_managed_wt(mock, sid, cwd="/srv/other", worktree_path="/srv/other")
        mock.metas[sid]["cwd"] = "/srv/other"
    elif case == "shares_a_bat_session_worktree":
        add_managed_wt(mock, sid)
        # Model a pre-policy invalid relationship: a new reservation now refuses it.
        registry.update("h1", sid, shares_worktree_with=MANUAL, failover_of=MANUAL)
    else:
        add_managed_wt(mock, sid)
    if case == "meta_cwd_in_main_checkout":
        mock.metas[sid]["cwd"] = "/srv/demo"
    elif case == "worktree_link_to_main_checkout":
        mock.handlers["git:getRoot"] = lambda p: "/srv/demo"
    elif case == "bat_tracks_another_worktree":
        mock.worktrees[sid]["worktreePath"] = "/srv/demo/.bat-worktrees/ffff0000"
    elif case == "tab_points_elsewhere":
        mock.ws_doc["terminals"].append({"id": sid, "workspaceId": "ws-1", "agentPreset": "claude-code-worktree",
                                         "cwd": "/srv/demo", "worktreePath": "/srv/demo"})
    f = all_tiers(fleet_factory)
    ops = ("send", "answer", "permissions_force", "merge", "remove_all_overrides")
    if case == "bat_tracks_another_worktree":  # only worktree actions read worktree:status (a branch diff)
        ops = ("merge", "remove_all_overrides")
    for op in ops:
        with pytest.raises(ResourceReadOnly) as ei:
            await SESSION_WRITES[op](f, sid)
        assert ei.value.code == code, op
    assert write_frames(mock) == []
    await f.close()


async def test_connector_worktree_session_is_writable(fleet_factory, mock):
    sid = add_managed_wt(mock)
    f = all_tiers(fleet_factory)
    r = await service.session_send(f, "h1", sid, "next step", confirm=True)
    assert r["accepted"]
    pol = await resource_policy.session_policy(f, "h1", sid)
    assert pol["provenance"] == "connector_managed" and pol["isolation"] == "legacy_shared_clone"
    assert pol["actions"]["session.send"]["allowed"]
    assert pol["actions"]["worktree.merge"] == {"allowed": False, "code": "DESTINATION_MANUAL",
                                                "reason": pol["actions"]["worktree.merge"]["reason"]}
    await f.close()


@pytest.mark.parametrize("marker", [{"worktree_made_by": "connector"}, {"checkpoint_id": "cp-fixture"},
                                  {"integration_operation_id": "op-fixture"}, {"branch": "batc/task-fixture"}])
@pytest.mark.parametrize("link", ["failover_of", "lead_session_id", "shares_worktree_with"])
async def test_connector_creation_root_refuses_bat_actions_when_child_loses_markers(fleet_factory, mock, marker, link):
    """B01/D05, §06/§08: a markerless successor/reviewer/shared row cannot reclassify an SSH worktree."""
    root = add_managed_wt(mock, "root-0001", **marker)
    sid = add_managed_wt(mock, "child-0002", **{link: root})
    f = all_tiers(fleet_factory)
    policy = await resource_policy.session_policy(f, "h1", sid)
    assert policy["worktree_made_by"] == "connector"
    assert policy["actions"]["worktree.remove"]["code"] == "NOT_A_BAT_WORKTREE"
    with pytest.raises(ResourceReadOnly, match="NOT_A_BAT_WORKTREE"):
        await orchestrate.worktree_remove(f, "h1", sid, confirm=True, delete_branch=True)
    assert write_frames(mock) == []
    await f.close()


@pytest.mark.parametrize("root_maker", ["bat", "connector"])
@pytest.mark.parametrize("link", [None, "lead_session_id", "shares_worktree_with", "failover_of"])
async def test_legacy_reviewer_worktree_needs_a_proven_registry_carrier(fleet_factory, mock, root_maker, link):
    """B01/D05: a journal-only reviewer link cannot authorize BAT writes in a managed root."""
    from bat_agent_connector.resource_ids import registry_worktree_root

    root = add_managed_wt(mock, "root-0001", role="lead", task_id="task-fixture", worktree_made_by=root_maker)
    sid = add_managed_wt(mock, "review-0002", role="reviewer", task_id="task-fixture",
                         **({link: root} if link else {}))
    f = all_tiers(fleet_factory, managed_roots=["/srv/demo"])
    entries = registry.list_entries("h1")
    row = registry.get("h1", sid)
    def lead_of(task_id):
        return {"session_id": root, "worktree_path": WT} if task_id == "task-fixture" else None
    # The observation resolver may know the lead from its journal. Policy must not guess that knowledge.
    assert registry_worktree_root(entries, "h1", sid, lead_of)["session_id"] == root
    assert resource_policy.worktree_maker(row, entries, lead_of, host="h1") == root_maker
    cls = resource_policy.classify(f.config.host("h1"), sid, terminal=service.registry_terminal(row), entries=entries)
    expected = root_maker if link else "unknown"
    assert cls.worktree_made_by == expected and cls.writable
    policy = await resource_policy.session_policy(f, "h1", sid)
    assert policy["worktree_made_by"] == expected
    assert policy["actions"]["session.send"]["allowed"]
    for action in resource_policy.BAT_WORKTREE_ACTIONS:
        assert policy["actions"][action]["allowed"] == (expected == "bat")
        if expected != "bat":
            assert policy["actions"][action]["code"] == "NOT_A_BAT_WORKTREE"
    if expected != "bat":
        if expected == "unknown":
            assert "cannot be proven" in policy["actions"]["worktree.remove"]["reason"]
        with pytest.raises(ResourceReadOnly, match="NOT_A_BAT_WORKTREE"):
            await orchestrate.worktree_merge(f, "h1", sid, confirm=True)
        with pytest.raises(ResourceReadOnly, match="NOT_A_BAT_WORKTREE"):
            await orchestrate.worktree_remove(f, "h1", sid, confirm=True, delete_branch=True,
                                              allow_unmerged=True, discard_uncommitted=True)
    assert write_frames(mock) == []
    await f.close()


@pytest.mark.parametrize("managed_checkout", [False, True])
@pytest.mark.parametrize("parent_maker", ["bat", "connector"])
async def test_nonsharing_failover_without_worktree_never_gets_bat_worktree_grant(
        fleet_factory, mock, managed_checkout, parent_maker):
    """B01/D05, §06/§08: losing the predecessor's carrier cannot authorize main-checkout worktree actions."""
    root = add_managed_wt(mock, "root-0001", worktree_made_by=parent_maker)
    sid = "child-0002"
    adopt(sid, cwd="/srv/demo", origin_cwd="/srv/demo", workspace_id="ws-1", agent_preset="codex-agent",
          failover_of=root, shares_worktree_with=None, worktree_path=None, branch=None)
    mock.metas[sid] = {"cwd": "/srv/demo", "isStreaming": False}
    f = all_tiers(fleet_factory, managed_roots=["/srv/demo"] if managed_checkout else [])
    policy = await resource_policy.session_policy(f, "h1", sid)
    assert policy["worktree_made_by"] == "bat"  # No longer inherits the old carrier's maker.
    code = "NOT_A_BAT_WORKTREE" if managed_checkout else "WORKDIR_NOT_MANAGED"
    for action in resource_policy.BAT_WORKTREE_ACTIONS:
        assert policy["actions"][action]["code"] == code
    with pytest.raises(ResourceReadOnly, match=code):
        await orchestrate.worktree_remove(f, "h1", sid, confirm=True, delete_branch=True,
                                          allow_unmerged=True, discard_uncommitted=True)
    assert write_frames(mock) == []
    await f.close()


# --------------------------------------------------------------------------- destinations
async def test_new_sessions_never_work_directly_in_a_human_checkout(fleet_factory, mock):
    f = all_tiers(fleet_factory)
    with pytest.raises(ResourceReadOnly, match="DESTINATION_MANUAL"):
        await orchestrate.session_start(f, "h1", "demo-project", confirm=True, use_worktree=False, prompt="x")
    with pytest.raises(ResourceReadOnly, match="DESTINATION_MANUAL"):
        await orchestrate.session_start(f, "h1", "demo-project", confirm=True, cwd_override="/srv/demo",
                                        external_branch="main", task_id="0123456789abcdef")
    assert write_frames(mock) == [] and registry.list_entries("h1") == []
    await f.close()
    strict = fleet_factory(writes=True, orchestrate=True, shared_clone_worktrees=False)
    with pytest.raises(ResourceReadOnly, match="shared_clone_worktrees"):
        await orchestrate.session_start(strict, "h1", "demo-project", confirm=True)
    assert write_frames(mock) == []
    await strict.close()
    owned = fleet_factory(writes=True, orchestrate=True, shared_clone_worktrees=False, managed_roots=["/srv/demo"])
    r = await orchestrate.session_start(owned, "h1", "demo-project", confirm=True, use_worktree=False)
    assert r["isolation"] == "managed_clone"
    await owned.close()


async def test_unexpected_worktree_location_stops_the_start(fleet_factory, mock):
    mock.handlers["worktree:create"] = lambda p: {"success": True, "worktreePath": "/srv/demo",
                                                  "branchName": "main", "sourceBranch": "main"}
    f = all_tiers(fleet_factory)
    with pytest.raises(ResourceReadOnly, match="DESTINATION_UNKNOWN"):
        await orchestrate.session_start(f, "h1", "demo-project", confirm=True, prompt="x")
    assert "claude:start-session" not in mock.channels() and "worktree:remove" not in mock.channels()
    assert registry.list_entries("h1")[0]["status"] == "failed"
    await f.close()


async def test_reviewer_or_successor_cannot_share_a_bat_session_folder(fleet_factory, mock):
    f = all_tiers(fleet_factory)
    t, _ = await service._resolve_session(f.client("h1"), MANUAL)
    with pytest.raises(ResourceReadOnly, match="MANUAL_READ_ONLY"):
        await resource_policy.authorize_shared_session(f, "h1", "new-0001", t)
    sid = add_managed_wt(mock)
    t, _ = await service._resolve_session(f.client("h1"), sid)
    grant = await resource_policy.authorize_shared_session(f, "h1", "new-0001", t)
    assert grant.workdir == WT and grant.channels == BY_ACTION["session.create"].channels
    await f.close()


# --------------------------------------------------------------------------- reads
async def test_list_views_show_provenance(fleet_factory, mock):
    f = all_tiers(fleet_factory)
    started = await orchestrate.session_start(f, "h1", "demo-project", confirm=True)
    rows = {r["session_id"]: r for r in (await service.sessions_list(f, "h1"))["sessions"]}
    assert rows[MANUAL]["provenance"] == "manual" and rows[MANUAL]["api_access"] == "read_only"
    assert rows[started["session_id"]]["provenance"] == "connector_managed"
    assert rows[started["session_id"]]["api_access"] == "managed"
    host = await resource_policy.session_policy(f, "h1")
    assert {m["action"] for m in host["mutations"]} == set(BY_ACTION)
    manual = await resource_policy.session_policy(f, "h1", MANUAL)
    assert all(not v["allowed"] and v["code"] == "MANUAL_READ_ONLY" for v in manual["actions"].values())
    await f.close()


def test_managed_roots_config():
    base = {"url": "wss://127.0.0.1:1/", "fingerprint": "AA" * 32, "token_ref": "env:X"}
    cfg = parse_config({"hosts": {"a": {**base, "managed_roots": ["/srv/batc//clones/"]}}})
    assert cfg.host("a").managed_roots == ("/srv/batc/clones",) and cfg.host("a").shared_clone_worktrees
    for bad in (["relative/path"], ["/srv/../etc"], ["/"], "/srv"):
        with pytest.raises(ConfigError):
            parse_config({"hosts": {"a": {**base, "managed_roots": bad}}})
    hc = cfg.host("a")
    assert resource_policy.in_managed_root(hc, "/srv/batc/clones/repo/.bat-worktrees/x")
    assert not resource_policy.in_managed_root(hc, "/srv/batc/clones-evil/repo")
    assert resource_policy.in_bat_worktrees("/srv/mono/.bat-worktrees/ab12", "/srv/mono/app")
    assert not resource_policy.in_bat_worktrees("/srv/mono/.bat-worktrees/ab12/deeper", "/srv/mono")
    assert not resource_policy.in_bat_worktrees("/srv/other/.bat-worktrees/ab12", "/srv/mono")


async def test_policy_verdicts_follow_host_tiers(fleet_factory, mock):
    sid = add_managed_wt(mock)
    f = fleet_factory()  # read-only host
    pol = await resource_policy.session_policy(f, "h1", sid)
    assert pol["api_access"] == "managed"
    assert {v["code"] for v in pol["actions"].values()} == {"TIER_DISABLED"}
    await f.close()


async def test_recovered_start_updates_one_record_for_resource_policy(fleet_factory, mock):
    sid = add_managed_wt(mock, "recovered-0001")
    registry.update("h1", sid, status="uncertain")
    registry.ensure_existing("h1", registry.get("h1", sid))  # read-back settles the existing reservation
    assert len([e for e in registry.list_entries("h1") if e["session_id"] == sid]) == 1
    f = all_tiers(fleet_factory)
    r = await service.session_send(f, "h1", sid, "go", confirm=True)
    assert r["accepted"]
    await f.close()


# --------------------------------------------------------------------------- review follow-ups
async def test_a_tab_worktree_the_record_lacks_is_a_binding_mismatch(fleet_factory, mock):
    sid = "managed-root-0002"
    adopt(sid, cwd="/srv/demo")  # started without a worktree in a managed root
    mock.metas[sid] = {"cwd": "/srv/demo", "isStreaming": False}
    mock.ws_doc["terminals"].append({"id": sid, "workspaceId": "ws-1", "agentPreset": "claude-code-worktree",
                                     "cwd": "/srv/demo", "worktreePath": "/srv/demo/.bat-worktrees/feedbeef",
                                     "worktreeBranch": "bat/worktree-feedbeef"})
    f = all_tiers(fleet_factory, managed_roots=["/srv/demo"])
    for op in ("remove_all_overrides", "send"):
        with pytest.raises(ResourceReadOnly, match="BINDING_MISMATCH"):
            await SESSION_WRITES[op](f, sid)
    assert write_frames(mock) == []
    await f.close()


async def test_shared_clone_setting_is_applied_before_any_ssh_git(controller_context, fleet_factory, monkeypatch):
    from bat_agent_connector import task_bat
    from bat_agent_connector.task_verifier import ObservedVerifier, VerificationSettings

    journal, _ = controller_context
    task = journal.submit(project="p", host="h1", workspace="demo-project", original_words="x",
                          base_branch="main", idempotency_key="k1")
    adapter = task_bat.BatTaskAdapter(fleet_factory(writes=True, orchestrate=True, shared_clone_worktrees=False),
                                      ObservedVerifier(VerificationSettings(ssh_hosts={"h1": "worker-1"})), journal)
    scripts = []

    async def ssh(_task, script):
        scripts.append(script)
        return ""

    monkeypatch.setattr(adapter, "_ssh_script", ssh)
    with pytest.raises(ResourceReadOnly, match="shared_clone_worktrees"):
        await adapter._ensure_external_worktree(task)
    assert scripts == []


async def test_a_managed_root_that_resolves_into_a_human_checkout_is_refused(fleet_factory, mock):
    mock.handlers["git:getRoot"] = lambda p: "/home/operator/proj" if p["cwd"] == "/srv/demo" else p["cwd"]
    f = all_tiers(fleet_factory, managed_roots=["/srv/demo"])
    with pytest.raises(ResourceReadOnly, match="DESTINATION_MANUAL"):
        await orchestrate.session_start(f, "h1", "demo-project", use_worktree=False, confirm=True, prompt="x")
    assert write_frames(mock) == [] and registry.list_entries("h1") == []
    await f.close()


async def test_a_worktree_under_the_resolved_git_root_is_the_connectors(fleet_factory, mock):
    real = "/mnt/data/demo"  # the workspace folder /srv/demo is a symlink to it
    wt = real + "/.bat-worktrees/abcd1234"
    mock.handlers["git:getRoot"] = lambda p: real if p["cwd"] == "/srv/demo" else p["cwd"]
    mock.handlers["worktree:create"] = lambda p: {"success": True, "worktreePath": wt,
                                                  "branchName": "bat/worktree-abcd1234", "sourceBranch": "main"}
    f = all_tiers(fleet_factory)
    r = await orchestrate.session_start(f, "h1", "demo-project", confirm=True)
    assert r["worktree_path"] == wt and registry.get("h1", r["session_id"])["origin_root"] == real
    sent = await service.session_send(f, "h1", r["session_id"], "go on", confirm=True)
    assert sent["accepted"]
    await f.close()


def test_merge_destination_is_the_recorded_origin(fleet_factory):
    adopt("m-1", cwd="/srv/m/demo/.bat-worktrees/0000abcd", worktree_path="/srv/m/demo/.bat-worktrees/0000abcd",
          origin_cwd="/srv/demo")
    hc = parse_config({"hosts": {"h1": {"url": "wss://h1.invalid:1", "fingerprint": "a" * 64,
                                        "token_ref": "env:X", "managed_roots": ["/srv/m"]}}}).host("h1")
    t = {"id": "m-1", "workspaceId": "ws-1"}
    moved = {"workspaces": [{"id": "ws-1", "folderPath": "/srv/m/demo"}]}
    with pytest.raises(ResourceReadOnly, match="BINDING_MISMATCH"):
        resource_policy.merge_origin(hc, "m-1", t, moved)
    same = {"workspaces": [{"id": "ws-1", "folderPath": "/srv/demo"}]}
    assert resource_policy.merge_origin(hc, "m-1", t, same) == "/srv/demo"
    with pytest.raises(ResourceReadOnly, match="DESTINATION_MANUAL"):
        resource_policy.check_merge_destination(hc, "/srv/demo")
