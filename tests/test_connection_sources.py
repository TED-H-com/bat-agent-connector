"""BAT source policy: real readers, temporary stores, no account scanning."""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from bat_agent_connector import config
from bat_agent_connector import managed_setup as setup


def write_profile(directory, data, *, enc=False, profile_id="remote-1"):
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "index.json").write_text(json.dumps({"profiles": [{
        "id": profile_id, "type": "remote", "remoteHost": "127.0.0.1",
        "remotePort": 9876, "remoteFingerprint": "AA" * 32,
    }]}))
    store = directory / "remote-tokens.enc.json"
    store.write_text(json.dumps({"enc": enc, "data": data}))
    store.chmod(0o600)


def windows_readers(monkeypatch, roaming):
    # Replace only each reader's imported binding, never global sys/Path classes.
    monkeypatch.setattr(setup, "sys", SimpleNamespace(platform="win32"))
    monkeypatch.setattr(config, "sys", SimpleNamespace(platform="win32"))
    monkeypatch.setenv("APPDATA", str(roaming))


def test_windows_setup_selects_new_directory(tmp_path, monkeypatch):
    windows_readers(monkeypatch, tmp_path)
    directory = tmp_path / "BetterAgentTerminal" / "profiles"
    write_profile(directory, json.dumps({"tokens": {"remote-1": "synthetic-token"}}))
    profiles, error = setup._profiles({})
    assert error is None
    assert profiles[0]["token_available"]
    assert setup._profile_directory({}) == directory


@pytest.mark.parametrize("data", [
    {"tokens": {"remote-1": "synthetic-token"}},
    json.dumps({"tokens": {"remote-1": "synthetic-token", "REMOTE-1": "other-token"}}),
], ids=["object-data", "case-only-competing-key"])
def test_setup_does_not_offer_unsupported_admission(tmp_path, data):
    write_profile(tmp_path, data)
    profiles, error = setup._profiles({"bat": {"profiles_dir": str(tmp_path)}})
    assert error is None
    assert not profiles[0]["token_available"]


def test_case_only_mismatch_is_already_unavailable(tmp_path):
    write_profile(tmp_path, json.dumps({"tokens": {"REMOTE-1": "synthetic-token"}}))
    profiles, _ = setup._profiles({"bat": {"profiles_dir": str(tmp_path)}})
    assert not profiles[0]["token_available"]


def test_windows_runtime_and_setup_use_same_default(tmp_path, monkeypatch):
    windows_readers(monkeypatch, tmp_path)
    # Legacy directory is visible to baseline setup but not its runtime default.
    directory = tmp_path / "org.tonyq.better-agent-terminal" / "profiles"
    write_profile(directory, json.dumps({"tokens": {"remote-1": "synthetic-token"}}))
    profiles, error = setup._profiles({})
    assert error is None and profiles[0]["token_available"]
    host = config.parse_config({"hosts": {"fixture": {
        "url": profiles[0]["url"], "fingerprint": profiles[0]["fingerprint"],
        "token_ref": "bat-profile:remote-1",
    }}}).host("fixture")
    assert host.resolve_token() == "synthetic-token"


def test_token_available_is_admission_but_existing_resolution_is_legacy(tmp_path):
    write_profile(tmp_path, {"tokens": {"remote-1": "synthetic-token"}})
    host = config.HostConfig("fixture", "wss://127.0.0.1:9876/", "AA" * 32,
                             "bat-profile:remote-1", bat_profiles_dir=str(tmp_path))
    assert host.resolve_token() == "synthetic-token"
    assert not host.token_available()
