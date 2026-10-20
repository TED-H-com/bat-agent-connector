"""Workspace PM route agreement from real choice, capability and profile handlers."""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from types import SimpleNamespace

from bat_agent_connector import pm_access, pm_native_runtime, pm_workspace_adapter
from bat_agent_connector.api_auth import Principal
from tests.operation_helpers import settle_operations
from tests.test_api_v1 import http, token, write_frames
from tests.test_pm_worktree import pm, world  # noqa: F401

GOLDEN = Path(__file__).parent / "fixtures" / "pm-effective-route.json"
OWNER = Principal("pm-owner", frozenset({"observe", "manage", "start"}))
CASES = {
    "saved_bat_blocked": (False, "bat-pm-linux-v1", "PM_WORKTREE_REQUIRED", "saved"),
    "saved_native_ready": (True, "codex-native-linux", None, "saved"),
    "saved_native_blocked": (False, "codex-native-linux", "PM_NATIVE_PLATFORM_UNSUPPORTED", "saved"),
    "unset_native_ready": (True, None, None, "unset"),
    "unset_blocked": (False, None, "no_eligible_adapter", "unset"),
    "choice_unreadable": (False, None, "original_sources_unavailable", "unreadable"),
    "scope_missing": (False, "codex-native-linux", "scope_required", "saved"),
}


async def test_pm_effective_route_matches_producer_golden(pm, mock, tmp_path, monkeypatch):  # noqa: F811
    w, d = pm, pm["d"]
    ops, wid, port = d.ops, w["workspace_id"], w["port"]
    native_home, system, empty, tools = (tmp_path / name for name in ("native-home", "system", "empty", "tools"))
    for directory in (native_home, system, empty, tools):
        directory.mkdir()
    (native_home / "auth.json").write_text("{}", encoding="utf-8")
    for name in ("codex", "bwrap"):
        executable = tools / name
        executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        executable.chmod(0o755)

    async def operation(action, params, *, target=None, preconditions=None):
        created, _ = ops.create(OWNER, action=action, target=target or {"workspace_id": wid},
            params=params, preconditions=preconditions or {}, idempotency_key="effective-" + str(len(ops.list()["operations"])))
        await settle_operations(ops)
        done = ops.get(created["operation_id"])
        assert done["status"] == "succeeded", done
        return done

    saved = await operation("pm.work_profile.save", {"document": {
        "version": 1, "binding": {"repository": "o/r", "host": "h1", "workspace_id": "ws-1"},
        "agent": "claude", "delivery_policy_ref": {"goal": "pull_request"}}})
    profile_id = saved["result"]["profile_id"]
    base = f"/api/v1/pm/workspaces/{wid}"
    cases = {}

    async def capture(name, *, eligible=True, principal=OWNER):
        auth = token(d, principal.actor, *principal.scopes)
        before = (len(mock.invokes), len(write_frames(mock)), len(w["runner"].scripts), ops.db.total_changes)
        # Only capability/profile reads see the installation inputs. Git setup
        # retains the outer toolchain; no stdlib module or class is patched.
        with monkeypatch.context() as inputs:
            inputs.setenv("PATH", str(tools if eligible else empty))
            inputs.setenv("CODEX_HOME", str(native_home))
            inputs.setattr(pm_native_runtime, "SYSTEM_CONFIG_ROOT", system)
            inputs.setattr(pm_native_runtime, "sys", SimpleNamespace(platform="linux" if eligible else "fixture-unsupported"))
            async def get(path):
                status, body = await http(port, "GET", path, tok=auth)
                assert status == 200, body
                return body
            caps = await get("/api/v1/capabilities")
            workspace = await get(base + "/capabilities")
            profile = await get(base + "/work-profiles/" + profile_id)
            readiness = await get(base + "/work-profiles/" + profile_id + "/readiness")
        assert before == (len(mock.invokes), len(write_frames(mock)), len(w["runner"].scripts), ops.db.total_changes)
        # Copy producer fields only; unrelated capabilities and worktree paths
        # are outside this golden's consumer input projection.
        cases[name] = {"capabilities": {"features": {"pm_workspaces": caps["features"]["pm_workspaces"]},
            "scopes": caps["scopes"], "actions": [row for row in caps["actions"] if row["action"] in
                {"pm.run.analyze", "pm.run.bat.analyze", "pm.conversation.propose"}]},
            "workspace": {key: workspace[key] for key in ("workspace_id", "native_analysis", "bat_analysis",
                "structured_analysis", "workspace_adapter", "effective_pm_route") if key in workspace},
            "profile": profile, "readiness": readiness}

    await capture("unset_native_ready")
    await capture("unset_blocked", eligible=False)

    async def choose(adapter):
        return await operation(pm_workspace_adapter.ACTION, {"adapter": adapter, "rationale": "Use this bounded PM route"},
            preconditions=pm_workspace_adapter.read(ops, OWNER, wid)["preconditions"])

    first = await choose("bat-pm-linux-v1")
    await capture("saved_bat_blocked")
    await choose("codex-native-linux")
    await capture("saved_native_ready")
    tid = ops.db.execute("SELECT thread_id FROM pm_threads WHERE workspace_id=?", (wid,)).fetchone()[0]
    await operation("pm.run.prepare", {"purpose": "Prepare the original PM worktree", "repository_binding": w["selection"]},
        target={"workspace_id": wid, "thread_id": tid}, preconditions={"expected_version": 1})
    await capture("saved_native_blocked", eligible=False)
    # Same original-source visibility seam as test_pm_workspace_adapter: the
    # preceding choice becomes inaccessible, without replacing saved choice rows.
    visible = pm_access.operation_source_visible
    with monkeypatch.context() as access:
        access.setattr(pm_access, "operation_source_visible", lambda db, principal, identity:
            identity != first["operation_id"] and visible(db, principal, identity))
        await capture("choice_unreadable")
    await capture("scope_missing", principal=Principal(OWNER.actor, OWNER.scopes - {"start"}))

    identities, digests = {wid: "ws_" + "1" * 32, profile_id: "wpf_" + "a" * 32}, {}
    def normalize(value, key=""):
        if isinstance(value, dict):
            return {name: normalize(item, name) for name, item in value.items()}
        if isinstance(value, list):
            return [normalize(item, key) for item in value]
        if isinstance(value, str) and re.fullmatch(r"(?:ws|wpf|op)_[0-9a-f]{32}", value):
            prefix = value.split("_")[0]
            if value not in identities:
                count = sum(item.startswith(prefix + "_") for item in identities) + 1
                identities[value] = prefix + "_" + f"{count:032x}"
            return identities[value]
        if isinstance(value, str) and key.endswith("_digest"):
            return digests.setdefault(value, f"{len(digests) + 1:064x}")
        if key in {"created_at", "updated_at", "observed_at", "recorded_at"} and value is not None:
            return 1790000000.0
        return value
    bundle = normalize({name: cases[name] for name in CASES})
    raw = json.dumps(bundle, ensure_ascii=False, indent=2) + "\n"
    assert str(tmp_path) not in raw and mock.token not in raw
    if os.environ.get("BATC_UPDATE_PM_EFFECTIVE_ROUTE_GOLDEN") == "1":
        GOLDEN.write_text(raw, encoding="utf-8")
    # C02 fails here on the stacked base, before relying on any new facet field.
    assert bundle["saved_bat_blocked"]["readiness"]["pm_analysis"]["pm_ready"] is False
    for name, (ready, adapter, reason, source) in CASES.items():
        row = bundle[name]
        facet = row["workspace"]["effective_pm_route"]
        assert facet == row["readiness"]["pm_analysis"]["effective_pm_route"]
        assert (facet["ready"], facet["adapter"], facet["reason"], facet["choice_source"]) == (ready, adapter, reason, source)
        assert row["readiness"]["pm_analysis"]["pm_ready"] is ready
        assert facet["model_execution_verified"] is False and facet["execution_authorized"] is False
    assert bundle == json.loads(GOLDEN.read_text(encoding="utf-8"))
