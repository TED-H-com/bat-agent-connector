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


async def test_stale_commit_and_paginated_revisions(pm, monkeypatch):
    await prepare(pm)
    await run(pm)
    await run(pm)
    ops, wid = pm["d"].ops, pm["workspace_id"]
    first = pm_dependency_reads.revisions(ops, OWNER, wid, limit=1)
    assert len(first["revisions"]) == 1 and first["next_cursor"] is not None
    second = pm_dependency_reads.revisions(ops, OWNER, wid, after=first["next_cursor"], limit=1)
    assert len(second["revisions"]) == 1 and second["next_cursor"] is None
    binding = pm_runs.workspace_worktree(ops.db, wid)
    binding["source_sha"] = "f" * 40
    monkeypatch.setattr(pm_runs, "workspace_worktree", lambda db, workspace_id: binding)
    assert read(pm)["stale"] and read(pm)["capture"]["commit_sha"] == pm["sha"]


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
