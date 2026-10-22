"""Capabilities from real updater transitions; only release/install boundaries are fake."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from bat_agent_connector import api_auth, api_v1, updater
from bat_agent_connector.config import Config
from bat_agent_connector.task_daemon import TaskDaemon
from bat_agent_connector.update_release import UpdateError
from tests.test_connector_updates import deployment, make_package, release, update_tree  # noqa: F401

pytestmark = pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux updater producer")
GOLDEN = Path(__file__).parent / "fixtures" / "connector-update-facts.json"
AT = "2026-10-22T12:00:00+00:00"


@pytest.fixture
def process(deployment, monkeypatch):
    u, receipt, _ = deployment
    monkeypatch.setenv("HOME", str(u.home))
    monkeypatch.setattr(updater, "timestamp", lambda: AT)
    build = {"version": "0.2.7", "commit": "a" * 40, "source": "vcs"}
    monkeypatch.setattr(api_v1, "build_identity", lambda: dict(build))
    package = receipt.parent / "tools/bat-agent-connector/lib/python3.13/site-packages/bat_agent_connector"
    monkeypatch.setattr(updater, "__file__", str(package / "updater.py"))
    return u, receipt, build


async def read_capabilities(tmp_path):
    daemon = TaskDaemon(Config(hosts={}), tmp_path / "journal.db")
    try:
        status, caps = await api_v1.ApiV1(daemon).capabilities(
            api_auth.Principal("fixture-observer", frozenset({"observe"})))
        assert status == 200
        return {key: caps[key] for key in ("build", "connector_update")}
    finally:
        await daemon.fleet.close()
        await daemon.inventory.close()
        daemon.journal.close()


@pytest.mark.parametrize("scenario", [
    "not_enrolled", "same", "failed", "unreadable", "incompatible", "commit_mismatch", "outside_root", "unsupported",
])
async def test_capabilities_update_states_are_read_only(process, tmp_path, monkeypatch, scenario):
    u, receipt, build = process
    if scenario != "not_enrolled":
        u.enroll(receipt, "preview")
        if scenario == "failed":
            u.find_release = lambda channel: release("b" * 40, "0.2.8")
            def fail(*args, **kwargs):
                raise UpdateError("fixture_install_failed")
            u.install = fail
            with pytest.raises(UpdateError):
                u.apply()
        elif scenario == "unreadable":
            (u.root / "state.json").chmod(0o644)
        elif scenario == "incompatible":
            monkeypatch.setattr(updater, "EPOCH", updater.EPOCH + 1)
        elif scenario == "commit_mismatch":
            build["commit"] = "c" * 40
        elif scenario == "outside_root":
            monkeypatch.setattr(updater, "__file__", str(tmp_path / "checkout/updater.py"))
        elif scenario == "unsupported":
            monkeypatch.setattr(updater, "sys", SimpleNamespace(platform="darwin"))
        (u.root / "lease").unlink()
    before = update_tree(u.root)
    reads = []
    read = updater.files.read_private
    def counted(path, **kwargs):
        reads.append((Path(path), kwargs.get("max_bytes", 1024 * 1024)))
        return read(path, **kwargs)
    monkeypatch.setattr(updater.files, "read_private", counted)
    result = await read_capabilities(tmp_path)
    block = result["connector_update"]
    expected = {"same": "reported", "failed": "reported", "incompatible": "unreadable",
                "commit_mismatch": "not_managed", "outside_root": "not_managed"}.get(scenario, scenario)
    assert block["state"] == expected
    assert block["activation"] == "next_launch"
    if expected == "reported":
        assert block["installed"] == {"version": "0.2.7", "commit": "a" * 40}
        assert block["channel"] == "preview" and block["enabled"] is True
        assert block["pending"] is False
        assert block["last"] == ({"status": "failed", "code": "fixture_install_failed", "at": AT}
                                 if scenario == "failed" else {"status": "enrolled_for_next_launch", "at": AT})
    elif expected == "unreadable":
        assert block["code"] == ("update_state_incompatible" if scenario == "incompatible" else "update_state_unreadable")
    assert update_tree(u.root) == before
    state_reads = [entry for entry in reads if entry[0] == u.root / "state.json"]
    assert len(state_reads) == (0 if scenario == "unsupported" else 1)
    assert all(size <= 1024 * 1024 for _, size in state_reads)
    for private in (str(u.home), "receipt", "proof", "previous", "https://", "root"):
        assert private not in json.dumps(block)
