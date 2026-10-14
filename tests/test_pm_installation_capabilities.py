"""Installation eligibility is a scoped read without workspace or execution effects."""
from __future__ import annotations

import pytest

from bat_agent_connector import pm_native_runtime
from tests.test_api_v1 import daemon as api_daemon
from tests.test_api_v1 import http, token, write_frames
from tests.test_api_v1 import served as api_served

daemon, served = api_daemon, api_served


@pytest.mark.parametrize("reason", [None, "PM_NATIVE_PLATFORM_UNSUPPORTED", "PM_NATIVE_EXECUTABLE_UNAVAILABLE",
    "PM_NATIVE_HOME_UNAVAILABLE", "PM_NATIVE_MANAGED_CONFIG_UNSUPPORTED", "PM_NATIVE_AUTH_UNAVAILABLE",
    "PM_NATIVE_INSTALLATION_UNAVAILABLE"])
async def test_installation_hints_without_workspace_or_model(served, monkeypatch, mock, reason):
    d, port = served
    calls = []
    def eligibility():
        calls.append(True)
        return {"adapter": "codex-native-linux", "eligible_for_preflight": reason is None, "reason": reason,
                "model_execution_verified": False, "auth_validity": "unverified"}
    monkeypatch.setattr(pm_native_runtime, "installation_eligibility", eligibility)
    auth = token(d, "installation-reader", "observe")
    before = d.ops.list(limit=50)
    status, result = await http(port, "GET", "/api/v1/pm/installation-capabilities", tok=auth)
    assert status == 200
    assert result["workspace_required"] is True
    assert result["native_analysis"]["reason"] == reason
    assert result["native_analysis"]["eligible_for_preflight"] is (reason is None)
    assert result["native_analysis"]["action_registered"] is ("pm.run.analyze" in d.ops.actions)
    assert len(calls) == 1
    assert "authorized" not in result["native_analysis"]
    assert d.ops.list(limit=50) == before
    assert not write_frames(mock)


async def test_installation_read_requires_observe_and_cannot_write(served, monkeypatch, mock):
    d, port = served
    def forbidden_probe():
        raise AssertionError("Unauthorized or write request must not probe installation")
    monkeypatch.setattr(pm_native_runtime, "installation_eligibility", forbidden_probe)
    auth = token(d, "installation-no-observe", "start")
    status, _ = await http(port, "GET", "/api/v1/pm/installation-capabilities", tok=auth)
    assert status == 403
    status, _ = await http(port, "GET", "/api/v1/pm/installation-capabilities")
    assert status == 401
    auth = token(d, "installation-reader", "observe")
    status, _ = await http(port, "POST", "/api/v1/pm/installation-capabilities", tok=auth, body={})
    assert status in (404, 405)
    assert not write_frames(mock)
