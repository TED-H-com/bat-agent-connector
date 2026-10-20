"""Hermetic adapter version facts for the shared frontend projection."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

from bat_agent_connector import pm_native_runtime as native
from tests.test_api_v1 import daemon as api_daemon
from tests.test_api_v1 import http, token, write_frames
from tests.test_api_v1 import served as api_served

daemon, served = api_daemon, api_served
GOLDEN = Path(__file__).parent / "fixtures" / "pm-version-facts.json"


@pytest.mark.skipif(sys.platform != 'linux', reason='Linux PM adapter producer and executable fixture')
async def test_pm_version_facts_match_real_producers(served, monkeypatch, tmp_path, mock):
    daemon, port = served
    empty_path = tmp_path / "empty-path"
    native_home = tmp_path / "codex-home"
    config = tmp_path / "config"
    for directory in (empty_path, native_home, config):
        directory.mkdir()
    monkeypatch.setenv("PATH", str(empty_path))
    monkeypatch.setenv("CODEX_HOME", str(native_home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config))
    monkeypatch.setattr(native, "SYSTEM_CONFIG_ROOT", config)
    before = daemon.ops.list(limit=100)
    status, installation = await http(port, "GET", "/api/v1/pm/installation-capabilities",
                                      tok=token(daemon, "fixture-reader", "observe"))
    assert status == 200
    assert installation["native_analysis"]["required_version"] == native.VERSION
    assert installation["native_analysis"]["model_execution_verified"] is False
    # An older executable is rejected before preflight. The producer exposes a
    # code, not the observed version: a UI cannot manufacture that missing fact.
    executable = tmp_path / "older-codex"
    executable.write_text(f"#!{sys.executable}\nprint('codex-cli 0.159.0')\n", encoding="utf-8")
    executable.chmod(0o700)
    with pytest.raises(native.NativeRuntimeError) as refused:
        await native._runtime_version(str(executable))
    result = {"installation": installation, "older_executable_error": refused.value.code}
    assert result["older_executable_error"] == "PM_NATIVE_VERSION_UNSUPPORTED"
    assert daemon.ops.list(limit=100) == before and not write_frames(mock)
    if os.environ.get("BATC_UPDATE_PM_VERSION_GOLDEN") == "1":
        GOLDEN.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    assert result == json.loads(GOLDEN.read_text(encoding="utf-8"))
