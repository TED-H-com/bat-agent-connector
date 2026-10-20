"""Session runtime facets use producer reads, not client-invented session fields."""
from __future__ import annotations

import copy
import json
import os
import time
from pathlib import Path
from types import SimpleNamespace

from bat_agent_connector import inventory, work_item_results
from tests.conftest import adopt
from tests.test_api_v1 import MANUAL, http, token, write_frames
from tests.test_api_v1 import daemon as api_daemon
from tests.test_api_v1 import served as api_served

daemon, served = api_daemon, api_served
GOLDEN = Path(__file__).parent / "fixtures" / "session-runtime.json"
FIELDS = ("host", "session_id", "title", "api_access", "provenance", "loaded", "streaming", "pending",
          "runtime_status", "stale", "fields_stale", "gone_at", "observed_at", "state")


async def test_session_runtime_producer_golden(served, mock, monkeypatch):
    d, port = served
    adopt(MANUAL)
    clock = {"now": 1790000000.0}
    # Freeze this producer's clock dependency only, never global stdlib time.
    monkeypatch.setattr(inventory, "time", SimpleNamespace(time=lambda: clock["now"], strftime=time.strftime, gmtime=time.gmtime))
    d.inventory.settings.activity_every = 1
    auth = token(d, "operator-dashboard", "observe", "operate")
    before = d.ops.list(limit=100)
    original_meta = copy.deepcopy(mock.metas[MANUAL])
    cases = {}

    async def capture(name):
        status, listing = await http(port, "GET", "/api/v1/sessions?include_gone=true", tok=auth)
        assert status == 200, listing
        row = next(s for s in listing["sessions"] if s["session_id"] == MANUAL)
        status, detail = await http(port, "GET", f"/api/v1/sessions/h1/{MANUAL}", tok=auth)
        assert status == 200, detail
        def select(source):
            return {key: source[key] for key in FIELDS if key in source}
        assert select(row) == select(detail["session"])
        cases[name] = {"listed": select(row), "detail": {"session": select(detail["session"])},
                       "observation": work_item_results._observation(d.ops, {"host": "h1", "session_id": MANUAL})}

    async def refresh(*, streaming=False, runtime=None, permission=False, ask=False):
        clock["now"] += 1
        mock.metas[MANUAL] = {**original_meta, "isStreaming": streaming, "runtimeStatus": runtime}
        mock.states[MANUAL]["pendingPermission"] = {"toolUseId": "fixture-permission", "toolName": "Read", "input": {}} if permission else None
        mock.states[MANUAL]["pendingAskUser"] = {"toolUseId": "fixture-question", "questions": [{"question": "Which result?"}]} if ask else None
        result = await d.inventory.refresh_host("h1")
        assert result["reachable"], result

    # Closest real shapes: inventory retains runtime_status, but does not project
    # managed status/typed attention or lifecycle=ended into this API envelope.
    await refresh(runtime="blocked")
    await capture("managed_blocked_runtime")
    await refresh(runtime="exited")
    await capture("ended_runtime")
    await refresh(streaming=None)
    await capture("unknown_activity")
    await refresh(streaming=True, runtime="running", permission=True)
    await capture("streaming_pending_permission")

    def fail(_):
        raise RuntimeError("fixture observation failed")

    mock.handlers["workspace:load"] = fail
    try:
        clock["now"] += 1
        assert not (await d.inventory.refresh_host("h1"))["reachable"]
        await capture("stale_pending")
    finally:
        del mock.handlers["workspace:load"]
    await refresh(runtime="blocked", ask=True)
    await capture("managed_pending")
    await refresh(streaming=True, runtime="running")
    mock.handlers["claude:get-session-meta"] = fail
    try:
        clock["now"] += 1
        assert (await d.inventory.refresh_host("h1"))["reachable"]
        await capture("stale_activity_evidence")
    finally:
        del mock.handlers["claude:get-session-meta"]

    assert cases["stale_activity_evidence"]["listed"]["state"]["evidence"]["activity"]["stale"] is True
    assert cases["streaming_pending_permission"]["listed"]["pending"]["kind"] == "permission"
    assert cases["stale_pending"]["listed"]["stale"] is True
    assert cases["unknown_activity"]["listed"]["streaming"] is None
    assert all(c["listed"]["api_access"] == "managed" for c in cases.values())
    assert all("status" not in c["listed"] and "status" not in c["listed"]["state"] for c in cases.values())
    assert cases["ended_runtime"]["listed"]["state"]["lifecycle"] == "unknown"
    assert d.ops.list(limit=100) == before and not write_frames(mock)
    normalized = json.loads(json.dumps(cases).replace(MANUAL, "fixture-session").replace("h1", "fixture-host"))
    if os.environ.get("BATC_UPDATE_SESSION_RUNTIME_GOLDEN") == "1":
        GOLDEN.write_text(json.dumps(normalized, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    assert normalized == json.loads(GOLDEN.read_text())
