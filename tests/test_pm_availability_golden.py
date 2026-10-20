"""Producer-owned PM availability facts, shared with frontend reason tests."""
from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace

from bat_agent_connector import pm_native_runtime
from tests.test_api_v1 import daemon as api_daemon
from tests.test_api_v1 import http, token, write_frames
from tests.test_api_v1 import served as api_served
from tests.test_pm_api import OWNER, seed

# Reuse the real Connector HTTP / PM setup; never launch a model.
daemon, served = api_daemon, api_served
GOLDEN = Path(__file__).parent / "fixtures" / "pm-availability.json"


async def test_pm_availability_producer_golden(served, monkeypatch, tmp_path, mock):
    d, port = served
    _, target = await seed(d)
    native = tmp_path / "native"
    native.mkdir()
    (native / "auth.json").write_text("{}", encoding="utf-8")  # fake readable auth, never model proof
    executable = {"present": True}
    # Replace module-owned dependencies, never globally patch stdlib classes.
    monkeypatch.setattr(pm_native_runtime, "shutil", SimpleNamespace(which=lambda _: "/fixture/tool" if executable["present"] else None))
    monkeypatch.setattr(pm_native_runtime, "_host_paths", lambda: (tmp_path, native))
    monkeypatch.setattr(pm_native_runtime, "_check_host_config", lambda: None)
    before = d.ops.list(limit=100)

    async def capture(scopes):
        auth = token(d, OWNER.actor, *scopes)
        async def get(path):
            status, body = await http(port, "GET", "/api/v1" + path, tok=auth)
            assert status == 200, body
            return body
        caps = await get("/capabilities")
        # Only the availability input projection is shared. Each value is copied
        # directly from the real global handler; unrelated host/actions are omitted.
        return {"capabilities": {"features": {"pm_workspaces": caps["features"]["pm_workspaces"]},
                    "scopes": caps["scopes"], "actions": [row for row in caps["actions"]
                        if row["action"] in {"pm.run.analyze", "pm.run.bat.analyze", "pm.conversation.propose"}]},
                "workspace": await get(f"/pm/workspaces/{target['workspace_id']}/capabilities"),
                "installation": await get("/pm/installation-capabilities")}

    owner = ("observe", "manage", "start")
    cases = {"owner": await capture(owner), "observer": await capture(("observe",))}
    executable["present"] = False
    cases["executable_unavailable"] = await capture(owner)
    executable["present"] = True
    removed = {name: d.ops.actions.pop(name) for name in
               ("pm.run.analyze", "pm.run.bat.analyze", "pm.conversation.propose")}
    try:
        cases["action_unregistered"] = await capture(owner)
    finally:
        d.ops.actions.update(removed)
    # Current global producer always advertises PM workspaces. Preserve that
    # fact; frontend tests explicitly omit the optional feature in a compatibility
    # mutation of the owner row rather than pretending the producer emitted it.
    assert all(case["capabilities"]["features"]["pm_workspaces"]["version"] == 1 for case in cases.values())
    assert cases["owner"]["workspace"]["native_analysis"]["authorized"] is True
    assert cases["observer"]["workspace"]["native_analysis"]["authorized"] is False
    assert cases["executable_unavailable"]["workspace"]["native_analysis"]["reason"] == "PM_NATIVE_EXECUTABLE_UNAVAILABLE"
    assert cases["action_unregistered"]["workspace"]["native_analysis"]["action_registered"] is False
    assert d.ops.list(limit=100) == before and not write_frames(mock)
    normalized = json.loads(json.dumps(cases).replace(target["workspace_id"], "ws_" + "1" * 32))
    if os.environ.get("BATC_UPDATE_PM_AVAILABILITY_GOLDEN") == "1":
        GOLDEN.write_text(json.dumps(normalized, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    assert normalized == json.loads(GOLDEN.read_text(encoding="utf-8"))
