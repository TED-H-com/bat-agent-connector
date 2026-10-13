"""Dependency inventories use fixed mock-bound Git and central durable actions only."""
from __future__ import annotations

import asyncio
import json

import pytest

from bat_agent_connector import (
    mcp_server,
    pm_access,
    pm_dependency_reads,
    pm_runs,
    repository_sync,
)
from bat_agent_connector.api_auth import Principal
from bat_agent_connector.operations import OperationError
from bat_agent_connector.pm_dependency_parser import dependency_key
from tests.operation_helpers import settle_operations
from tests.test_api_v1 import http, token
from tests.test_checkpoints import bat_writes, git, snapshot
from tests.test_pm_run_api import unpack
from tests.test_pm_spec_sources import carrier_state
from tests.test_pm_worktree import OWNER, submit
from tests.test_pm_worktree import pm as pm_fixture
from tests.test_pm_worktree import world as world_fixture
from tests.test_repository_sync import REQUEST

pm, world = pm_fixture, world_fixture

FILES = {
    "pyproject.toml": '''[project]
name = "sample-api"
version = "1.0.0"
requires-python = ">=3.10"
dependencies = ["httpx>=0.27", "unlocked>=1"]
[build-system]
requires = ["hatchling>=1"]
[dependency-groups]
test = ["pytest>=8"]
dev = ["unclassified-helper>=1"]
''',
    "uv.lock": '''version = 1
[[package]]
name = "sample-api"
version = "1.0.0"
[[package]]
name = "httpx"
version = "0.28.1"
[[package]]
name = "hatchling"
version = "1.27.0"
[[package]]
name = "pytest"
version = "8.4.0"
''',
    "desktop/package.json": json.dumps({"name": "sample-desktop", "version": "1.0.0", "engines": {"node": ">=20"},
        "dependencies": {"shared-widget": "^1.0.0"}, "devDependencies": {"vite": "^6", "vitest": "^3", "typescript": "^5", "other-helper": "^1"},
        "scripts": {"build": "tsc && vite build", "test": "vitest run"}}),
    "desktop/package-lock.json": json.dumps({"lockfileVersion": 3, "packages": {
        "": {"name": "sample-desktop", "version": "1.0.0"}, "node_modules/shared-widget": {"version": "1.2.0"},
        "node_modules/vite": {"version": "6.1.0", "bin": {"vite": "bin/vite.js"}},
        "node_modules/vitest": {"version": "3.1.0", "bin": {"vitest": "vitest.mjs"}},
        "node_modules/typescript": {"version": "5.8.0", "bin": {"tsc": "bin/tsc"}}}}),
    "desktop/src-tauri/Cargo.toml": '''[package]
name = "sample-native"
version = "1.0.0"
[dependencies]
serde = "1"
[build-dependencies]
cc = "1"
[dev-dependencies]
pretty_assertions = "1"
''',
    "desktop/src-tauri/Cargo.lock": '''version = 3
[[package]]
name = "serde"
version = "1.0.200"
[[package]]
name = "cc"
version = "1.0.90"
[[package]]
name = "pretty_assertions"
version = "1.4.0"
''',
    "desktop/src-tauri/rust-toolchain.toml": '[toolchain]\nchannel = "stable"\n',
    ".python-version": "3.12\n",
    "desktop/.nvmrc": "22\n",
    ".github/workflows/ci.yml": '''name: CI
on: [push]
jobs:
  python:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Python tests
        run: uv run pytest -q
  web:
    runs-on: ubuntu-latest
    steps:
      - run: npm run test
        working-directory: desktop
      - run: npm run build
        working-directory: desktop
  native:
    runs-on: ubuntu-latest
    steps:
      - run: |
          cargo test --locked
        working-directory: desktop/src-tauri
''',
    "node_modules/ignored/package.json": '{"name":"never-read"}',
}


async def prepare(w, files=None):
    for name, content in (FILES if files is None else files).items():
        dest = w["human"] / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, tuple):
            dest.symlink_to(content[0])
        else:
            dest.write_bytes(content if isinstance(content, bytes) else content.encode())
    git(w["human"], "add", ".")
    git(w["human"], "commit", "--allow-empty", "-qm", "Synthetic dependency fixtures")
    git(w["human"], "push", str(w["remote"]), "main")
    w["state"]["sha"] = w["sha"] = git(w["human"], "rev-parse", "HEAD")
    preview = await repository_sync.source_preview(w["d"].ops, REQUEST)
    w["selection"] = {**preview["target"], **{key: preview[key] for key in ("source_ref", "source_sha", "repository_id", "binding_digest")}}
    op = await submit(w)
    assert op["status"] == "succeeded", op
    return w["plan"]


def read(w, principal=OWNER):
    return pm_dependency_reads.get(w["d"].ops, principal, w["workspace_id"])


def request(w, action="capture", params=None):
    current = read(w)
    return {"action": "pm.dependencies." + action, "target": {"workspace_id": w["workspace_id"]},
        "params": params if params is not None else {"repository": "o/r", "commit_sha": w["sha"]},
        "preconditions": {"expected_version": current["version"], "expected_workspace_version": current["workspace_version"],
                          "expected_binding_digest": current["source"]["binding_digest"]}}


async def run(w, action="capture", params=None, key=None):
    ops = w["d"].ops
    req = request(w, action, params)
    op, _ = ops.create(OWNER, **req, idempotency_key=key or action + str(ops.db.total_changes))
    await settle_operations(ops)
    return ops.get(op["operation_id"])


def by_name(result, name, relation=None):
    keys = {d["key"] for values in result["dependencies"].values() for d in values if d["name"] == name}
    return [e for e in result["edges"] if e["dependency_key"] in keys and (relation is None or e["relation"] == relation)]


async def test_realistic_capture_resolutions_classification_hints_and_read_only_carrier(pm, mock):
    plan = await prepare(pm)
    before, clone = snapshot(pm["human"]), carrier_state(plan)
    op = await run(pm)
    assert op["status"] == "succeeded", op
    result = read(pm)
    assert result["capture"]["revision_id"].startswith("pmdep_")
    assert result["capture"]["status"] == "complete" and not result["partial"]
    assert result["capture"]["commit_sha"] == pm["sha"] and isinstance(result["capture"]["captured_at"], float)
    assert {c["name"] for c in result["components"]} == {"sample-api", "sample-desktop", "sample-native"}
    assert all(c["maintenance_owner"] is None for c in result["components"])
    assert by_name(result, "httpx")[0]["resolved"] == "0.28.1"
    assert by_name(result, "httpx")[0]["evidence_level"] == "verified"
    assert by_name(result, "unlocked")[0]["resolved"] is None
    assert by_name(result, "unlocked")[0]["evidence_level"] == "declared"
    assert by_name(result, "pytest", "test")
    assert by_name(result, "hatchling", "build")
    assert by_name(result, "vite", "build")[0]["evidence_refs"][1]["script"] == "build"
    assert by_name(result, "vitest", "test") and by_name(result, "typescript", "build")
    assert by_name(result, "other-helper")[0]["basis"] == "dev_unclassified"
    assert by_name(result, "unclassified-helper")[0]["basis"] == "dev_unclassified"
    assert by_name(result, "pretty_assertions", "test")[0]["basis"] == "dev-dependencies"
    assert by_name(result, "cc", "build") and by_name(result, "serde", "runtime")
    assert by_name(result, "rust", "build") and by_name(result, "node", "build")
    assert {h["job_id"] for h in result["verification_hints"]} == {"python", "web", "native"}
    assert all(h["line"] > 0 and h["commit_sha"] == pm["sha"] for h in result["verification_hints"])
    assert not any(f["path"].startswith("node_modules/") for f in result["files"])
    count, changes = len(pm["runner"].phases), pm["d"].ops.db.total_changes
    assert read(pm) == result
    assert len(pm["runner"].phases) == count and changes == pm["d"].ops.db.total_changes
    assert snapshot(pm["human"]) == before and carrier_state(plan) == clone and not bat_writes(mock)


async def test_replay_uses_original_revision_and_key_without_rereading_git(pm):
    await prepare(pm)
    req = request(pm)
    op = await run(pm, key="capture-once")
    count = len(pm["runner"].phases)
    replay, created = pm["d"].ops.create(OWNER, **req, idempotency_key="capture-once")
    await settle_operations(pm["d"].ops)
    assert not created and replay["operation_id"] == op["operation_id"]
    assert len(pm["runner"].phases) == count
    assert len(pm_dependency_reads.revisions(pm["d"].ops, OWNER, pm["workspace_id"])["revisions"]) == 1


async def test_missing_large_invalid_and_symlink_files_remain_visible(pm):
    await prepare(pm, {"pyproject.toml": FILES["pyproject.toml"], "bad/package.json": "{broken", "requirements.txt": b"x" * 512001,
                       "rust-toolchain": ("../../outside",), ".python-version": b"\xff"})
    assert (await run(pm))["status"] == "succeeded"
    result = read(pm)
    files = {r["path"]: r for r in result["files"]}
    assert files["uv.lock"]["status"] == "missing"
    assert files["requirements.txt"]["status"] == "too_large" and files["requirements.txt"]["size"] == 512001
    assert files["bad/package.json"]["status"] == "unparsable"
    assert files["rust-toolchain"]["reason"] == "symlink_or_nonregular_blob"
    assert files[".python-version"]["reason"] == "non_utf8_or_binary"
    assert result["partial"] and result["counts"]["partial"] and result["capture"]["status"] == "partial"


async def test_empty_workspace_and_empty_repo_are_different_from_unknown(pm):
    result = read(pm)
    assert result["capture"]["status"] == "unknown" and result["counts"] is None and result["stale"] is None
    await prepare(pm, {})
    result = read(pm)
    assert result["reason"] == "not_captured" and result["counts"] is None and result["version"] == 0
    assert (await run(pm))["status"] == "succeeded"
    result = read(pm)
    assert result["capture"]["status"] == "complete" and result["counts"]["components"] == 0


@pytest.mark.parametrize("change", ["binding", "member", "config"])
async def test_final_frame_changes_refuse_capture_without_receipt(pm, change, monkeypatch):
    await prepare(pm)
    def withdraw(_):
        db = pm["d"].ops.db
        if change == "binding":
            original = pm_runs.workspace_worktree
            monkeypatch.setattr(pm_runs, "workspace_worktree", lambda db, wid: {**original(db, wid), "source_sha": "f" * 40})
        elif change == "member":
            db.execute("DELETE FROM pm_workspace_members WHERE workspace_id=? AND actor=?", (pm["workspace_id"], OWNER.actor))
        else:
            pm["d"].ops.context["github_config"].repos.clear()
    pm["runner"].after["read.dependencies"] = withdraw
    op = await run(pm)
    assert op["status"] == "failed", op
    assert pm["d"].ops.db.execute("SELECT count(*) FROM pm_dependency_revisions").fetchone()[0] == 0
    assert pm["d"].ops.db.execute("SELECT count(*) FROM pm_dependency_heads").fetchone()[0] == 0


async def test_declare_retract_version_preconditions_and_capture_is_immutable(pm):
    await prepare(pm)
    await run(pm)
    before = read(pm)
    params = {"component_id": before["components"][0]["component_id"], "dependency": {"ecosystem": "api", "name": "billing-contract", "kind": "api_schema", "version": "v1"},
        "relation": "contract", "evidence_level": "declared", "source_ref": {"path": "docs/billing.md", "commit_sha": pm["sha"]}, "note": "Explicit owner contract"}
    old = request(pm, "declare", params)
    op = await run(pm, "declare", params)
    assert op["status"] == "succeeded", op
    after = read(pm)
    assert after["edges"] == before["edges"] and len(after["declared"]) == 1
    assert after["declared"][0]["source_ref"]["commit_sha"] == pm["sha"]
    with pytest.raises(OperationError) as caught:
        pm["d"].ops.create(OWNER, **old, idempotency_key="stale-version")
    assert caught.value.code == "VERSION_CONFLICT"
    retracted = await run(pm, "retract", {"declaration_id": op["result"]["declaration_id"]})
    assert retracted["status"] == "succeeded" and read(pm)["declared"] == []
    params.update(relation="runtime", evidence_level="inferred", source_ref="https://example.com/candidate")
    assert (await run(pm, "declare", params))["status"] == "succeeded"
    assert read(pm)["declared"][0]["evidence_level"] == "inferred"
    assert read(pm)["edges"] == before["edges"]


async def test_source_withdrawal_removes_inventory_history_impact_and_operation_visibility(pm):
    await prepare(pm)
    op = await run(pm)
    db = pm["d"].ops.db
    db.execute("UPDATE pm_workspaces SET repositories='[]' WHERE workspace_id=?", (pm["workspace_id"],))
    result = read(pm)
    assert result["components"] == result["edges"] == result["files"] == result["declared"] == []
    assert result["counts"] is None and result["version"] is None
    assert pm_dependency_reads.revisions(pm["d"].ops, OWNER, pm["workspace_id"])["revisions"] == []
    assert pm_dependency_reads.impact(pm["d"].ops, OWNER, pm["workspace_id"], dependency=dependency_key("python", "httpx"))["components"] == []
    assert not pm_access.visible(db, OWNER, op)
    events = pm["d"].journal.api_events(after=0, limit=500)
    assert not any(e["kind"].startswith("pm.dependencies.") for e in pm_access.filter_events(db, OWNER, events)["events"])


async def test_pinned_commit_and_paginated_revisions_do_not_claim_freshness(pm):
    await prepare(pm)
    await run(pm)
    await run(pm)
    ops, wid = pm["d"].ops, pm["workspace_id"]
    first = pm_dependency_reads.revisions(ops, OWNER, wid, limit=1)
    assert len(first["revisions"]) == 1 and first["next_cursor"] is not None
    second = pm_dependency_reads.revisions(ops, OWNER, wid, after=first["next_cursor"], limit=1)
    assert len(second["revisions"]) == 1 and second["next_cursor"] is None
    assert read(pm)["stale"] is None and read(pm)["capture"]["commit_sha"] == pm["sha"]
    assert first["revisions"][0]["stale_reason"] == "repository_head_not_observed"



async def test_impact_follows_exact_component_edges_transitively(pm):
    files = {**FILES, "shared/package.json": json.dumps({"name": "shared-widget", "version": "1.2.0", "dependencies": {"leaf-library": "^2"}}),
        "shared/package-lock.json": json.dumps({"lockfileVersion": 3, "packages": {"node_modules/leaf-library": {"version": "2.1.0"}}})}
    await prepare(pm, files)
    await run(pm)
    impact = pm_dependency_reads.impact(pm["d"].ops, OWNER, pm["workspace_id"], dependency=dependency_key("npm", "leaf-library"))
    assert [(c["name"], c["distance"]) for c in impact["components"]] == [("shared-widget", 1), ("sample-desktop", 2)]
    assert impact["components"][1]["verification_hints"]
    assert impact["components"][1]["edges"][0]["basis"] == "exact_published_package"


async def test_http_rpc_mcp_scope_membership_and_no_read_effects(pm, monkeypatch):
    await prepare(pm)
    await run(pm)
    d, wid = pm["d"], pm["workspace_id"]
    server = await asyncio.start_server(d._handle, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    auth = token(d, OWNER.actor, "observe")
    observer = Principal(OWNER.actor, frozenset({"observe"}))
    monkeypatch.setenv("BATC_TASK_URL", f"http://127.0.0.1:{port}/rpc")
    monkeypatch.setenv("BATC_API_TOKEN", auth)
    mcp, fleet = mcp_server.build_server(d.fleet.config, read_only=True, principal_only=True)
    try:
        before, calls = d.ops.db.total_changes, len(pm["runner"].phases)
        tools = {tool.name: tool for tool in await mcp.list_tools()}
        base = f"/api/v1/pm/workspaces/{wid}/dependencies"
        for suffix, name, params in [("", "pm_dependencies_get", {}), ("/revisions?limit=1", "pm_dependencies_revisions_list", {"limit": 1}),
                ("/impact?dependency=" + dependency_key("python", "httpx"), "pm_dependencies_impact_get", {"dependency": dependency_key("python", "httpx")})]:
            assert name in tools and tools[name].annotations.read_only_hint
            status, expected = await http(port, "GET", base + suffix, tok=auth)
            assert status == 200, expected
            assert await d.call_api(name, {"workspace_id": wid, **params}, observer) == expected
            assert unpack(await mcp.call_tool(name, {"workspace_id": wid, **params})) == expected
        assert d.ops.db.total_changes == before and len(pm["runner"].phases) == calls
        assert (await http(port, "GET", base, tok=token(d, "outsider", "observe")))[0] == 404
        assert (await http(port, "GET", base, tok=token(d, OWNER.actor, "manage")))[0] == 403
        for suffix in ["?limit=1", "/revisions?limit=0", "/revisions?after=-1", "/revisions?limit=1&limit=2", "/impact?dependency=name", "/impact"]:
            assert (await http(port, "GET", base + suffix, tok=auth))[0] == 422
        status, caps = await http(port, "GET", "/api/v1/capabilities", tok=auth)
        assert status == 200 and caps["features"]["pm_dependencies"]["version"] == 1
    finally:
        await fleet.close()
        server.close()
        await server.wait_closed()


async def peer_repository(w, mock):
    """A second actual Git repository and carrier, sharing only the central service."""
    import re
    from dataclasses import replace

    from bat_agent_connector import controller_authority, pm_workspace, pm_worktree
    from bat_agent_connector.operations import ActionDef

    ops = w["d"].ops
    human, remote = w["human"].parent / "peer", w["human"].parent / "peer.git"
    human.mkdir()
    git(human, "init", "-q", "-b", "main")
    git(human, "config", "user.name", "fixture")
    git(human, "config", "user.email", "fixture@example.invalid")
    (human / "package.json").write_text(json.dumps({"name": "shared-widget", "version": "1.2.0", "dependencies": {"leaf-library": "^2"}}))
    (human / "package-lock.json").write_text(json.dumps({"lockfileVersion": 3, "packages": {"node_modules/leaf-library": {"version": "2.1.0"}}}))
    git(human, "add", ".")
    git(human, "commit", "-qm", "Synthetic peer package")
    git(human.parent, "clone", "--bare", "--no-hardlinks", str(human), str(remote))
    sha = git(human, "rev-parse", "HEAD")
    config = ops.context["github_config"]
    original = config.repos["o/r"]
    config.repos["o/peer"] = replace(original, repository="o/peer", sync=replace(original.sync, remote_url=str(remote), bindings=(("h1", "ws-dependency-peer"),)))
    mock.ws_doc["workspaces"].append({**mock.ws_doc["workspaces"][0], "id": "ws-dependency-peer", "folderPath": str(human)})
    provide_original = w["gh"].before_request
    def provide(method, path, body):
        provide_original(method, path, body)
        if method == "GET" and path == "/repos/o/peer":
            w["gh"].script.append(("GET", re.escape(path) + "$", 200, {}, {"id": 5000, "full_name": "o/peer"}))
        if method == "GET" and path == "/repos/o/peer/git/ref/heads/main":
            w["gh"].script.append(("GET", re.escape(path) + "$", 200, {}, {"ref": "refs/heads/main", "object": {"type": "commit", "sha": sha}}))
    w["gh"].before_request = provide
    created, _ = ops.create(OWNER, action="pm.workspace.create", params={"name": "Peer", "repositories": ["o/peer"]}, idempotency_key="peer-workspace")
    await settle_operations(ops)
    wid = ops.get(created["operation_id"])["result"]["workspace"]["workspace_id"]
    created, _ = ops.create(OWNER, action="pm.thread.create", target={"workspace_id": wid}, params={"title": "Peer"},
                            preconditions={"expected_version": 1}, idempotency_key="peer-thread")
    await settle_operations(ops)
    tid = ops.get(created["operation_id"])["result"]["thread_id"]
    authority = {**pm_runs.execution_identity(ops.db, wid), "server_id": pm_workspace.server_id(ops.db),
                 "scopes": ["observe", "manage", "start"], "admin": False, "pm_workspace_id": wid}
    pv = await repository_sync.source_preview(ops, {"repository": "o/peer", "host": "h1", "workspace_id": "ws-dependency-peer", "source_ref": "refs/heads/main"})
    selection = {**pv["target"], **{k: pv[k] for k in ("source_ref", "source_sha", "repository_id", "binding_digest")}}
    async def prepare_peer(ctx):
        execution = {**authority, "pm_operation_id": ctx.operation_id, "pm_requester": pm_workspace.principal_binding(OWNER)}
        record = pm_runs.create(ops, OWNER, operation_id=ctx.operation_id, workspace_id=wid, thread_id=tid,
            expected_version=1, adapter="manual", purpose="Fixture peer", execution_authority=execution)
        async def check():
            pm_runs.require_writer(ops.journal, record["run_id"], record["generation"])
        with controller_authority.use(execution, ops.journal):
            plan = await pm_worktree.select(ops, wid, execution, selection, operation_id=ctx.operation_id)
            pm_runs.bind_worktree(ops.journal, record["run_id"], record["generation"], plan)
            carrier = await pm_worktree.ensure(ctx, plan, check=check)
            pm_runs.fail(ops.journal, record["run_id"], record["generation"], "Fixture carrier ready", before_send=True)
            return carrier
    ops.register(ActionDef("pm.fixture.peer", "manage", "Fixture peer carrier", prepare_peer))
    created, _ = ops.create(OWNER, action="pm.fixture.peer", target={"workspace_id": wid}, idempotency_key="peer-carrier")
    await settle_operations(ops)
    assert ops.get(created["operation_id"])["status"] == "succeeded", ops.get(created["operation_id"])
    peer = {**w, "workspace_id": wid, "sha": sha}
    assert (await run(peer, params={"repository": "o/peer", "commit_sha": sha}))["status"] == "succeeded"
    return peer


async def test_cross_repository_pinned_edges_withdraw_without_hidden_counts(pm, mock):
    await prepare(pm)
    peer = await peer_repository(pm, mock)
    await run(pm)
    result = read(pm)
    assert len(result["cross_repository_edges"]) == 1
    link = result["cross_repository_edges"][0]
    assert {ref["repository"] for ref in link["evidence_refs"]} == {"o/r", "o/peer"}
    assert any(ref["path"] == "desktop/package-lock.json" for ref in link["evidence_refs"])
    assert link["resolved"] == "1.2.0" and link["evidence_level"] == "verified"
    assert {ref["commit_sha"] for ref in link["evidence_refs"]} == {pm["sha"], peer["sha"]}
    impact = pm_dependency_reads.impact(pm["d"].ops, OWNER, pm["workspace_id"], dependency=dependency_key("npm", "leaf-library"))
    assert [(c["name"], c["distance"]) for c in impact["components"]] == [("shared-widget", 1), ("sample-desktop", 2)]
    pm["d"].ops.db.execute("DELETE FROM pm_workspace_members WHERE workspace_id=? AND actor=?", (peer["workspace_id"], OWNER.actor))
    withdrawn = read(pm)
    assert withdrawn["cross_repository_edges"] == withdrawn["external_components"] == []
    assert withdrawn["counts"] == result["counts"]
    assert peer["workspace_id"] not in json.dumps(withdrawn)
    impact = pm_dependency_reads.impact(pm["d"].ops, OWNER, pm["workspace_id"], dependency=dependency_key("npm", "leaf-library"))
    assert impact["components"] == [] and impact["counts"] is None
    assert impact["reason"] == "dependency_not_observed"


def _first_sorting_copy(db, source_wid, fake="ws_" + "0" * 32):
    """Copy a workspace's carrier rows under an ID that sorts before every real workspace."""
    db.execute("INSERT INTO pm_workspaces SELECT ?,server_id,name,repositories,version,created_by,created_at,updated_at,'op_fake' "
               "FROM pm_workspaces WHERE workspace_id=?", (fake, source_wid))
    db.execute("INSERT INTO pm_workspace_members SELECT ?,actor,role,controller_id,created_at,updated_at "
               "FROM pm_workspace_members WHERE workspace_id=?", (fake, source_wid))
    db.execute("INSERT INTO pm_workspace_worktrees SELECT ?,run_id,binding,created_at FROM pm_workspace_worktrees "
               "WHERE workspace_id=?", (fake, source_wid))
    db.execute("INSERT INTO pm_dependency_heads SELECT ?,source_key,version,revision_id FROM pm_dependency_heads "
               "WHERE workspace_id=?", (fake, source_wid))
    return fake


def _stored(pm, result):
    return json.loads(pm["d"].ops.db.execute("SELECT document FROM pm_dependency_revisions WHERE revision_id=?",
                                             (result["capture"]["revision_id"],)).fetchone()[0])


@pytest.mark.parametrize("hidden_first", [False, True])
async def test_capture_peer_cap_counts_only_visible_peers(pm, mock, monkeypatch, hidden_first):
    await prepare(pm)
    peer = await peer_repository(pm, mock)
    if hidden_first:
        fake = _first_sorting_copy(pm["d"].ops.db, peer["workspace_id"])
        # The copy's head row passes the SQL filter but fails the source check.
        assert pm_dependency_reads._current(pm["d"].ops, OWNER, fake) is None
    monkeypatch.setattr(pm_dependency_reads, "MAX_REPOSITORIES", 1)
    assert (await run(pm))["status"] == "succeeded"
    result = read(pm)
    stored = _stored(pm, result)
    assert [row["workspace_id"] for row in stored["peer_revisions"]] == [peer["workspace_id"]]
    assert stored["peer_selection_reasons"] == []
    assert len(result["cross_repository_edges"]) == 1 and len(result["external_components"]) == 1
    assert result["partial"] is False and result["cross_repository"] == {"status": "bounded", "complete": False}
    assert "ws_" + "0" * 32 not in json.dumps(result)


async def test_capture_peer_scan_bound_records_a_reason(pm, mock, monkeypatch):
    await prepare(pm)
    peer = await peer_repository(pm, mock)
    _first_sorting_copy(pm["d"].ops.db, peer["workspace_id"])
    monkeypatch.setattr(pm_dependency_reads, "MAX_PEER_SCAN", 1)
    assert (await run(pm))["status"] == "succeeded"
    result = read(pm)
    stored = _stored(pm, result)
    # The bound stopped after one hidden candidate; the visible peer was never examined.
    assert stored["peer_revisions"] == [] and stored["peer_selection_reasons"] == ["peer_scan_limit"]
    assert result["partial"] is True
    assert result["cross_repository"] == {"status": "bounded", "complete": False, "selection_reasons": ["peer_scan_limit"]}
    assert result["counts"]["partial"] is False  # The captured catalog itself is complete.
    impact = pm_dependency_reads.impact(pm["d"].ops, OWNER, pm["workspace_id"], dependency=dependency_key("npm", "leaf-library"))
    assert impact["partial"] is True
    impact = pm_dependency_reads.impact(pm["d"].ops, OWNER, pm["workspace_id"], dependency=dependency_key("python", "httpx"))
    assert impact["cross_repository"]["selection_reasons"] == ["peer_scan_limit"]


def test_peer_filters_and_byte_budget_run_before_the_accepted_peer_cap(monkeypatch):
    # Ineligible heads span several SQL pages before the two accepted peers.
    candidates = [{'workspace_id': f'ws_{i:032x}', 'revision_id': f'pmdep_{i:032x}'} for i in range(7)]
    documents = [{'repository': repository} for repository in ('o/own', 'o/a', 'o/a', 'o/hidden', 'o/large', 'o/b', 'o/c')]
    monkeypatch.setattr(pm_dependency_reads, '_PEER_PAGE', 2)
    monkeypatch.setattr(pm_dependency_reads, 'MAX_REPOSITORIES', 2)
    monkeypatch.setattr(pm_dependency_reads, 'MAX_PEER_BYTES', 100)
    monkeypatch.setattr(pm_dependency_reads, '_peer_candidates', lambda ops, principal, wid, cursor, limit:
        [row for row in candidates if (row['workspace_id'], row['revision_id']) > cursor][:limit])
    monkeypatch.setattr(pm_dependency_reads, '_current', lambda ops, principal, wid:
        None if wid == candidates[3]['workspace_id'] else {'target': {'repository': 'o/own'}})
    monkeypatch.setattr(pm_dependency_reads, '_visible_record', lambda ops, principal, wid, rid:
        {'document': documents[int(wid[3:], 16)], 'document_digest': 'digest'})
    monkeypatch.setattr(pm_dependency_reads, '_size', lambda doc: 101 if doc['repository'] == 'o/large' else 40)
    reasons = []
    peers = pm_dependency_reads.discover_peers(None, OWNER, 'own', reasons)
    assert [row['workspace_id'] for row in peers] == [candidates[1]['workspace_id'], candidates[5]['workspace_id']]
    assert reasons == ['peer_byte_budget']


async def test_impact_before_capture_says_why_and_reads_have_their_own_403(pm):
    ops, wid, key = pm["d"].ops, pm["workspace_id"], dependency_key("python", "httpx")
    unavailable = pm_dependency_reads.impact(ops, OWNER, wid, dependency=key)
    assert unavailable["reason"] == "source_unavailable" and unavailable["counts"] is None
    assert unavailable["cross_repository"] == {"status": "bounded", "complete": False}
    await prepare(pm)
    pending = pm_dependency_reads.impact(ops, OWNER, wid, dependency=key)
    assert pending["reason"] == "not_captured" and pending["counts"] is None and pending["partial"] is True
    assert pending["capture"]["status"] == "unknown"
    assert pending["cross_repository"] == {"status": "bounded", "complete": False}
    manage_only = Principal(OWNER.actor, frozenset({"manage"}))
    for read_fn, kwargs in ((pm_dependency_reads.get, {}), (pm_dependency_reads.revisions, {}),
                            (pm_dependency_reads.impact, {"dependency": key})):
        with pytest.raises(OperationError) as refused:
            read_fn(ops, manage_only, wid, **kwargs)
        assert (refused.value.code, refused.value.status) == ("FORBIDDEN", 403)
        assert "observe" in refused.value.message and "changes" not in refused.value.message


@pytest.mark.parametrize("field,value", [("sha256", "f" * 64), ("blob_sha", "f" * 40), ("size", 1), ("path", "../outside")])
async def test_forged_host_file_evidence_never_becomes_a_capture(pm, field, value):
    await prepare(pm)
    original = pm["runner"].run
    async def forged(host, script, timeout_s=None):
        result = json.loads(await original(host, script, timeout_s))
        result["result"]["files"][0][field] = value
        return json.dumps(result)
    pm["runner"].run = forged
    result = await run(pm)
    assert result["status"] == "failed" and result["error_code"] == "PM_DEPENDENCIES_INVALID_SOURCE"
    assert read(pm)["capture"]["revision_id"] is None


async def test_schema_is_idempotent_and_captures_and_declarations_are_immutable(pm):
    import sqlite3

    from bat_agent_connector import pm_dependencies
    await prepare(pm)
    await run(pm)
    ops = pm["d"].ops
    version = ops.db.execute("PRAGMA user_version").fetchone()[0]
    pm_dependencies.schema(ops.journal)
    pm_dependencies.schema(ops.journal)
    assert ops.db.execute("PRAGMA user_version").fetchone()[0] == version
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        ops.db.execute("DELETE FROM pm_dependency_revisions")


@pytest.mark.parametrize("value", [None, "http://example.com/spec", "https://user:password@example.com/spec", {"path": "../secret", "commit_sha": "f" * 40}, {"path": "spec.md", "commit_sha": "f" * 40}])
async def test_declarations_require_an_exact_bound_or_https_source(pm, value):
    await prepare(pm)
    await run(pm)
    params = {"component_id": read(pm)["components"][0]["component_id"],
        "dependency": {"ecosystem": "api", "name": "contract", "kind": "api_schema", "version": None},
        "relation": "contract", "evidence_level": "declared", "source_ref": value}
    with pytest.raises(OperationError) as caught:
        pm["d"].ops.create(OWNER, **request(pm, "declare", params), idempotency_key="bad-source")
    assert caught.value.code == "INVALID_PARAMS" and read(pm)["declared"] == []


async def test_catalog_and_total_byte_limits_are_partial_not_silent_totals(pm):
    files = {f"component-{i:03}/.python-version": "3.12\n" for i in range(130)}
    # Byte budget is exercised before the catalog's later paths.
    files.update({f"a{i}/requirements.txt": "#" + "x" * 499999 for i in range(5)})
    await prepare(pm, files)
    assert (await run(pm))["status"] == "succeeded"
    result = read(pm)
    assert result["partial"] and "file_limit" in result["catalog_reasons"]
    assert len(result["files"]) == 128 and result["counts"]["partial"]
    assert any(f["reason"] == "selection_byte_budget" for f in result["files"])


async def test_capture_http_and_mcp_share_the_original_operation_key(pm, monkeypatch):
    await prepare(pm)
    d = pm["d"]
    server = await asyncio.start_server(d._handle, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    auth = token(d, OWNER.actor, "observe", "manage")
    monkeypatch.setenv("BATC_TASK_URL", f"http://127.0.0.1:{port}/rpc")
    monkeypatch.setenv("BATC_API_TOKEN", auth)
    mcp, fleet = mcp_server.build_server(d.fleet.config, principal_only=True)
    try:
        envelope = request(pm)
        status, accepted = await http(port, "POST", "/api/v1/operations", tok=auth,
            body=envelope, headers={"Idempotency-Key": "capture-http-mcp"})
        assert status == 202, accepted
        await settle_operations(d.ops)
        oid = accepted["operation"]["operation_id"]
        op = d.ops.get(oid)
        assert op["status"] == "succeeded", op
        reads = len(pm["runner"].phases)
        replay = unpack(await mcp.call_tool("operation_submit", {**envelope, "idempotency_key": "capture-http-mcp", "confirm": True}))
        assert replay["operation"]["operation_id"] == oid
        assert replay["operation"]["result"]["revision_id"] == op["result"]["revision_id"]
        assert len(pm["runner"].phases) == reads
        rejected, body = await http(port, "POST", "/api/v1/operations", tok=token(d, OWNER.actor, "observe"),
            body=request(pm), headers={"Idempotency-Key": "capture-no-manage"})
        assert rejected == 403 and "components" not in body
    finally:
        await fleet.close()
        server.close()
        await server.wait_closed()


async def test_unicode_path_json_expansion_stays_inside_existing_runner_ceiling(pm):
    prefix = "/".join(["界" * 50] * 8)
    files = {f"{prefix}/requirements-{i:03}.txt": "#" for i in range(128)}
    files[f"{prefix}/requirements-000.txt"] = "#" + "x" * 399999
    files[f"{prefix}/requirements-001.txt"] = "#" + "x" * 239999
    await prepare(pm, files)
    op = await run(pm)
    assert op["status"] == "succeeded", op
    result = read(pm)
    assert len(result["files"]) == 128 and result["partial"]
    assert any(f["reason"] == "selection_wire_budget" for f in result["files"])
    assert any(f["status"] == "read" and f["size"] == 400000 for f in result["files"])


async def test_dynamic_and_unsupported_dependency_fields_capture_as_partial(pm):
    await prepare(pm, {'pyproject.toml': '[project]\nname="app"\ndynamic=["dependencies"]\n',
        'uv.lock': 'version=1\n', 'requirements.txt': 'requests==2.31.0\n',
        'package.json': '{"name":"web","peerDependencies":{"react":"^19"}}',
        'package-lock.json': '{"lockfileVersion":3,"packages":{}}'})
    assert (await run(pm))['status'] == 'succeeded'
    result = read(pm)
    assert result['capture']['status'] == 'partial' and result['counts']['partial']
    assert {'python_dynamic_dependencies', 'npm_peer_dependencies_not_captured'} <= set(result['parser_reasons'])


async def test_freshness_is_unknown_without_repository_head_observation(pm):
    await prepare(pm)
    await run(pm)
    assert read(pm)['stale'] is None
    assert read(pm)['stale_reason'] == 'repository_head_not_observed'
    page = pm_dependency_reads.revisions(pm['d'].ops, OWNER, pm['workspace_id'])
    assert page['revisions'][0]['stale'] is None
    impact = pm_dependency_reads.impact(pm['d'].ops, OWNER, pm['workspace_id'], dependency=dependency_key('python', 'httpx'))
    assert impact['stale'] is None and impact['stale_reason'] == 'repository_head_not_observed'


async def test_revision_summaries_do_not_decode_inventory_documents(pm, monkeypatch):
    from bat_agent_connector import pm_dependencies
    await prepare(pm)
    await run(pm)
    await run(pm)
    def forbidden(*args, **kwargs):
        raise AssertionError('History must not decode or hash full inventory documents')
    monkeypatch.setattr(pm_dependencies, 'record', forbidden)
    result = pm_dependency_reads.revisions(pm['d'].ops, OWNER, pm['workspace_id'], limit=100)
    assert len(result['revisions']) == 2 and all(r['commit_sha'] == pm['sha'] for r in result['revisions'])
    pm['d'].ops.context['github_config'].repos.clear()
    withdrawn = pm_dependency_reads.revisions(pm['d'].ops, OWNER, pm['workspace_id'])
    assert withdrawn['revisions'] == [] and withdrawn['reason'] == 'source_unavailable'


@pytest.mark.parametrize('change', ['binding', 'member', 'config'])
async def test_final_guard_refuses_withdrawal_during_peer_discovery(pm, monkeypatch, change):
    await prepare(pm)
    def discover(ops, principal, wid, reasons=None):
        if change == 'binding':
            original = pm_runs.workspace_worktree
            monkeypatch.setattr(pm_runs, 'workspace_worktree', lambda db, wid: {**original(db, wid), 'source_sha': 'f' * 40})
        elif change == 'member':
            ops.db.execute('DELETE FROM pm_workspace_members WHERE workspace_id=? AND actor=?', (wid, OWNER.actor))
        else:
            ops.context['github_config'].repos.clear()
        return []
    monkeypatch.setattr(pm_dependency_reads, 'discover_peers', discover)
    op = await run(pm)
    assert op['status'] == 'failed'
    assert pm['d'].ops.db.execute('SELECT count(*) FROM pm_dependency_revisions').fetchone()[0] == 0
    assert pm['d'].ops.db.execute('SELECT count(*) FROM pm_dependency_heads').fetchone()[0] == 0


@pytest.mark.parametrize('code', ['PM_SPEC_COMMIT_UNAVAILABLE', 'REPOSITORY_BINDING_CHANGED', 'untrusted host text'])
async def test_host_error_codes_are_preserved_only_from_the_allowlist(pm, code):
    await prepare(pm)
    async def failure(*args, **kwargs):
        return json.dumps({'error': code})
    pm['runner'].run = failure
    op = await run(pm)
    expected = code if code.startswith(('PM_SPEC_', 'REPOSITORY_')) else 'PM_DEPENDENCIES_SOURCE_UNAVAILABLE'
    assert op['status'] == 'failed' and op['error_code'] == expected


async def test_unreadable_git_entries_and_trees_preserve_readable_inventory(pm):
    # Real pinned Git objects; all scratch stays in the disk-backed fixture.
    import os
    with open(os.fsencode(pm["human"]) + b"/bad-\xff", "wb") as stream:
        stream.write(b"fixture")
    large = pm["human"] / "huge"
    large.mkdir()
    for i in range(4000):
        (large / (f"entry-{i:05d}-" + "x" * 100)).write_bytes(b"fixture")
    await prepare(pm, {'pyproject.toml': '[project]\nname="readable"\ndependencies=["lib>=1"]\n'})
    op = await run(pm)
    assert op['status'] == 'succeeded', op
    result = read(pm)
    assert result['capture']['status'] == 'partial'
    assert result['components'][0]['name'] == 'readable'
    assert {row['reason'] for row in result['unreadable_paths']} == {'tree_too_large'}


async def test_peer_expansion_never_makes_a_stored_capture_unreadable(pm, monkeypatch):
    import copy

    from bat_agent_connector import pm_dependencies, pm_spec_sources
    from bat_agent_connector.pm_release_baselines import _json, _size
    await prepare(pm)
    await run(pm)
    ops, wid = pm['d'].ops, pm['workspace_id']
    current = read(pm)
    saved = pm_dependencies.record(ops.db, wid, current['capture']['revision_id'])
    doc = copy.deepcopy(saved['document'])
    edge = next(e for e in doc['edges'] if e['relation'] == 'runtime')
    # A valid legacy revision close to the previous 2 MB capture ceiling.
    doc['edges'] = [{**edge, 'edge_id': 'pmde_' + f'{i:032x}', 'version': f'>={i}',
        'evidence_refs': [{**edge['evidence_refs'][0], 'command': 'x' * 900}]} for i in range(1000)]
    revision = 'pmdep_' + 'd' * 32
    doc['capture']['revision_id'] = revision
    assert 1_000_000 < _size(doc) < 2_000_000
    ops.db.execute('INSERT INTO pm_dependency_revisions(revision_id,workspace_id,source_key,document,document_digest,source_binding,created_at,operation_id) VALUES(?,?,?,?,?,?,?,?)',
        (revision, wid, saved['source_key'], _json(doc), pm_spec_sources._digest(doc), _json(saved['source_binding']), saved['created_at'], 'op_' + 'd' * 32))
    ops.db.execute('UPDATE pm_dependency_heads SET revision_id=? WHERE workspace_id=?', (revision, wid))
    peer_component = {**copy.deepcopy(doc['components'][0]), 'component_id': 'pmcomp_peer', 'repository': 'o/peer',
        'name': 'httpx', 'published_name': 'httpx', 'verification_hints': [{'command': 'y' * 800000}]}
    peer = {**doc, 'components': [peer_component], 'edges': [], 'verification_hints': [], 'files': [], 'dependencies': []}
    assert _size(peer) < 1_000_000
    monkeypatch.setattr(pm_dependency_reads, '_related', lambda *args: [peer])
    result = read(pm)
    assert _size(result) <= 2_000_000 and result['capture']['revision_id'] == revision
    assert result['edges'] == doc['edges']
    assert result['cross_repository']['reason'] == 'projection_byte_budget'
    assert result['partial']
    history = pm_dependency_reads.revisions(ops, OWNER, wid)
    legacy = next(r for r in history['revisions'] if r['revision_id'] == revision)
    assert legacy['status'] == 'unknown' and legacy['reason'] == 'summary_not_recorded'


async def test_capture_budget_error_is_dependency_specific_and_bilingual(pm, monkeypatch):
    from bat_agent_connector.pm_dependency_parser import Inventory
    await prepare(pm)
    original = Inventory.parse
    def too_large(self):
        return {**original(self), 'parser_reasons': ['x' * 2_000_001]}
    monkeypatch.setattr(Inventory, 'parse', too_large)
    op = await run(pm)
    assert op['status'] == 'failed' and op['error_code'] == 'PM_DEPENDENCIES_READ_TOO_LARGE'
    assert '相依' in op['status_reason'] and 'baseline' not in op['status_reason']
    assert read(pm)['capture']['revision_id'] is None


async def test_large_locked_capture_is_stored_partial_deterministically_and_readable(pm):
    from bat_agent_connector import pm_dependencies
    from bat_agent_connector.pm_dependency_budget import MAX_CAPTURE_BYTES, MAX_READ_BYTES
    from bat_agent_connector.pm_release_baselines import _size
    count = 2000
    await prepare(pm, {'pyproject.toml': '[project]\nname="app"\ndependencies=' + json.dumps([f'package-{i:04d}>=1' for i in range(count)]),
        'uv.lock': ''.join(f'[[package]]\nname="package-{i:04d}"\nversion="1"\n' for i in range(count))})
    assert (await run(pm))['status'] == 'succeeded'
    first = read(pm)
    assert first['capture']['status'] == 'partial' and 'capture_budget' in first['parser_reasons']
    assert 0 < len(first['edges']) < count
    assert first['omitted_edges'] + len(first['edges']) == count
    assert all(e['evidence_level'] == 'verified' for e in first['edges'])
    assert _size(first) <= MAX_READ_BYTES
    stored = pm_dependencies.record(pm['d'].ops.db, pm['workspace_id'], first['capture']['revision_id'])['document']
    assert _size(stored) <= MAX_CAPTURE_BYTES
    assert (await run(pm))['status'] == 'succeeded'
    second = read(pm)
    assert second['edges'] == first['edges'] and second['omitted_edges'] == first['omitted_edges']
    assert second['counts']['partial'] and second['counts']['edges'] == len(second['edges'])


async def test_cpu_parser_runs_off_loop_and_final_guard_stays_on_loop(pm, monkeypatch):
    import threading

    from bat_agent_connector import pm_spec_sources
    from bat_agent_connector.pm_dependency_parser import Inventory
    await prepare(pm)
    loop_thread, parser_threads, guard_threads = threading.get_ident(), [], []
    original_parse, original_guard = Inventory.parse, pm_spec_sources._guard
    def parse(self):
        parser_threads.append(threading.get_ident())
        return original_parse(self)
    async def guard(*args, **kwargs):
        guard_threads.append(threading.get_ident())
        return await original_guard(*args, **kwargs)
    monkeypatch.setattr(Inventory, 'parse', parse)
    monkeypatch.setattr(pm_spec_sources, '_guard', guard)
    assert (await run(pm))['status'] == 'succeeded'
    assert parser_threads and all(t != loop_thread for t in parser_threads)
    assert len(guard_threads) >= 3 and all(t == loop_thread for t in guard_threads)


async def test_history_never_selects_large_row_tail_columns(pm):
    import sqlite3
    await prepare(pm)
    assert (await run(pm))['status'] == 'succeeded'
    db = pm['d'].ops.db
    def authorize(action, table, column, *_):
        if action == sqlite3.SQLITE_READ and table == 'pm_dependency_revisions' and column in {'document', 'created_at'}:
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK
    db.set_authorizer(authorize)
    try:
        result = pm_dependency_reads.revisions(pm['d'].ops, OWNER, pm['workspace_id'])
        assert result['revisions'][0]['captured_at'] is not None
    finally:
        db.set_authorizer(None)


async def test_impact_distinguishes_legacy_projection_omission_from_absence(pm):
    import copy

    from bat_agent_connector import pm_dependencies, pm_spec_sources
    from bat_agent_connector.pm_release_baselines import _json, _size
    await prepare(pm)
    assert (await run(pm))['status'] == 'succeeded'
    ops, wid = pm['d'].ops, pm['workspace_id']
    saved = pm_dependencies.record(ops.db, wid, read(pm)['capture']['revision_id'])
    doc = copy.deepcopy(saved['document'])
    # Legacy documents can expand past the response cap when dependencies have multiple kinds.
    for dependency in doc['dependencies'][:3]:
        dependency.update(name='x' * 200_000, kinds=list(pm_dependencies.KINDS))
    revision = 'pmdep_' + 'a' * 32
    doc['capture']['revision_id'] = revision
    assert _size(doc) < 2_000_000
    ops.db.execute('INSERT INTO pm_dependency_revisions(revision_id,workspace_id,source_key,document,document_digest,source_binding,created_at,operation_id) VALUES(?,?,?,?,?,?,?,?)',
        (revision, wid, saved['source_key'], _json(doc), pm_spec_sources._digest(doc), _json(saved['source_binding']), saved['created_at'], 'op_' + 'a' * 32))
    ops.db.execute('UPDATE pm_dependency_heads SET revision_id=? WHERE workspace_id=?', (revision, wid))
    assert 'dependencies' in read(pm)['projection_omitted_collections']
    result = pm_dependency_reads.impact(ops, OWNER, wid, dependency=doc['dependencies'][0]['key'])
    assert result['reason'] == 'dependency_omitted' and result['partial'] and result['counts'] is None


@pytest.mark.parametrize('directory', [False, True])
async def test_non_utf8_paths_only_make_missing_dependency_coverage_partial(pm, directory):
    import os
    raw = os.fsencode(pm['human']) + b'/unrelated-\xff'
    if directory:
        os.mkdir(raw)
        raw += b'/package.json'
    with open(raw, 'wb') as stream:
        stream.write(b'{"name":"hidden"}')
    await prepare(pm, {'pyproject.toml': '[project]\nname="app"', 'uv.lock': 'version=1'})
    assert (await run(pm))['status'] == 'succeeded'
    result = read(pm)
    assert result['partial'] is directory
    assert ('path_encoding_unsupported' in result['catalog_reasons']) is directory
    assert bool(result['unreadable_paths']) is directory
