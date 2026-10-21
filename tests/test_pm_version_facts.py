"""Hermetic adapter version facts for the shared frontend projection."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

from bat_agent_connector import pm_native_runtime as native
from bat_agent_connector import pm_runs
from tests.test_api_v1 import daemon as api_daemon
from tests.test_api_v1 import http, token, write_frames
from tests.test_api_v1 import served as api_served
from tests.test_pm_bat_operations import OWNER, bat_world, request  # noqa: F401
from tests.test_pm_discussions import prepared

daemon, served = api_daemon, api_served
GOLDEN = Path(__file__).parent / "fixtures" / "pm-version-facts.json"


@pytest.fixture(autouse=True)
def isolated_adapter_host(monkeypatch, tmp_path):
    empty_path = tmp_path / "empty-path"
    native_home = tmp_path / "codex-home"
    config = tmp_path / "config"
    for directory in (empty_path, native_home, config):
        directory.mkdir()
    monkeypatch.setenv("PATH", str(empty_path))
    monkeypatch.setenv("CODEX_HOME", str(native_home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config))
    monkeypatch.setattr(native, "SYSTEM_CONFIG_ROOT", config)


@pytest.mark.skipif(sys.platform != 'linux', reason='Linux PM adapter producer and executable fixture')
async def test_pm_version_facts_match_real_producers(served, tmp_path, mock):
    daemon, port = served
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
    check_golden('native', result)


def check_golden(key, result):
    saved = json.loads(GOLDEN.read_text(encoding="utf-8")) if GOLDEN.exists() else {}
    if os.environ.get("BATC_UPDATE_PM_VERSION_GOLDEN") == "1":
        saved[key] = result
        GOLDEN.write_text(json.dumps(saved, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    assert result == saved[key]


async def test_bat_version_facts_match_immutable_run_producer(bat_world):  # noqa: F811
    ops, _, _, _ = bat_world
    target = await prepared(ops)
    operation = await request(ops, target)
    assert operation['status'] == 'succeeded'
    run = pm_runs.get_by_operation(ops.db, operation['operation_id'])
    public = pm_runs.read(ops, OWNER, target['workspace_id'], run['run_id'])
    # De-identified subset of real public fields. The record describes a past
    # run, not a fresh executable probe; no numeric Codex version is exposed.
    provenance = public['bat_runtime_provenance']
    assert isinstance(public['updated_at'], (int, float))
    # Normalize only the nondeterministic clock in this de-identified golden.
    result = {'adapter': public['adapter'], 'updated_at': 1790000000.0,
        'bat_runtime_provenance': {key: provenance[key] for key in (
            'adapter', 'profile', 'profile_version', 'bat_source_revision', 'codex_source_revision')}}
    check_golden('bat_run', result)
