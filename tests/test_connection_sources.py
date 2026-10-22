"""BAT source policy: real readers, temporary stores, no account scanning."""
from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from bat_agent_connector import config
from bat_agent_connector import managed_setup as setup
from bat_agent_connector.connection_sources import (
    BAT_SOURCE_POLICY,
    SourceUnavailable,
    TokenMode,
    parse_token_store,
    read_token_store,
    select_profiles_directory,
)

GOLDEN = Path(__file__).parent / "fixtures" / "bat-source-policy-v1.json"
VECTORS = json.loads(GOLDEN.read_text())


def prepare_directory(root, row):
    for relative, kind in row["entries"].items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        if kind == "directory":
            path.mkdir(exist_ok=True)
        else:
            path.write_text("synthetic non-directory")


def directory_result(root, row):
    configured = row.get("profiles_dir")
    if configured and not configured.startswith("~"):
        configured = root / configured
    environment = {} if row.get("no_appdata") else {"APPDATA": str(root / "roaming")}
    try:
        selected = select_profiles_directory(configured, platform=row["platform"],
            environment=environment, home=root / "home")
        return {"path": selected.relative_to(root).as_posix()}
    except SourceUnavailable as error:
        return {"reason": str(error)}


def token_result(row, mode):
    result = parse_token_store(row["store"], mode=mode).lookup(row["profile_id"])
    return {"state": result.state, "reason": result.reason}


def test_policy_vectors_match_producer(tmp_path):
    """Only Python expectations are regenerated; native expectations stay reviewed."""
    actual = json.loads(GOLDEN.read_text())
    assert actual["policy"] == BAT_SOURCE_POLICY == 1
    for row in actual["directories"]:
        root = tmp_path / row["id"]
        prepare_directory(root, row)
        denied = root / row["unreadable"] if row.get("unreadable") else None
        if denied:
            denied.chmod(0)
        try:
            outcome = directory_result(root, row)
        finally:
            if denied:
                denied.chmod(0o700)
        row["expected"]["python_admission"] = outcome
        row["expected"]["python_runtime"] = outcome
    for row in actual["token_stores"]:
        row["expected"]["python_admission"] = token_result(row, TokenMode.ADMISSION)
        row["expected"]["python_runtime"] = token_result(row, TokenMode.LEGACY_RUNTIME)
    if os.environ.get("BATC_UPDATE_SOURCE_POLICY") == "1":
        GOLDEN.write_text(json.dumps(actual, ensure_ascii=True, indent=2) + "\n")
    assert actual == json.loads(GOLDEN.read_text())


@pytest.mark.parametrize("row", VECTORS["token_stores"], ids=lambda row: row["id"])
def test_all_python_readers_match_vectors(tmp_path, row):
    write_profile(tmp_path, "", profile_id=row["profile_id"])
    path = tmp_path / "remote-tokens.enc.json"
    path.write_text(row["store"])
    before = path.read_bytes(), path.stat().st_mtime_ns
    profiles, error = setup._profiles({"bat": {"profiles_dir": str(tmp_path)}})
    assert error is None
    expected = row["expected"]
    assert profiles[0]["token_state"] == expected["python_admission"]["state"]
    admitted = expected["python_admission"]["state"] == "available"
    assert profiles[0]["token_available"] is admitted
    host = config.HostConfig("fixture", profiles[0]["url"], profiles[0]["fingerprint"],
        "bat-profile:" + row["profile_id"], bat_profiles_dir=str(tmp_path))
    assert host.token_available() is admitted
    if expected["python_runtime"]["state"] == "available":
        assert host.resolve_token() == read_token_store(tmp_path, mode=TokenMode.LEGACY_RUNTIME).lookup(row["profile_id"]).token
    else:
        with pytest.raises(config.TokenUnavailable):
            host.resolve_token()
    if admitted:
        assert expected["python_runtime"]["state"] == "available"
        assert expected["rust_legacy_fleet"]["state"] == "available"
    assert (path.read_bytes(), path.stat().st_mtime_ns) == before


@pytest.mark.parametrize("mode", list(TokenMode))
def test_results_never_expose_token_or_parser_exception(mode, caplog):
    secret = "synthetic-sensitive-value"
    contents = json.dumps({"enc": False, "data": json.dumps({"tokens": {"remote-1": secret}})})
    store = parse_token_store(contents, mode=mode)
    result = store.lookup("remote-1")
    assert result.token == secret
    assert secret not in repr(store) + repr(result) + result.reason + caplog.text
    broken = parse_token_store(contents[:-1], mode=mode)
    assert secret not in repr(broken) + broken.reason + caplog.text


def test_selection_rechecks_new_directory_and_never_falls_back_on_bad_store(tmp_path, monkeypatch):
    windows_readers(monkeypatch, tmp_path)
    old = tmp_path / "org.tonyq.better-agent-terminal" / "profiles"
    new = tmp_path / "BetterAgentTerminal" / "profiles"
    write_profile(old, json.dumps({"tokens": {"remote-1": "synthetic-token"}}))
    assert setup._profile_directory({}) == old
    write_profile(new, "damaged")
    assert setup._profile_directory({}) == new
    assert not setup._profiles({})[0][0]["token_available"]
    with pytest.raises(config.TokenUnavailable):
        config._token_from_bat_profiles(None, "remote-1")


def test_directory_failure_is_specific_and_does_not_expose_path(tmp_path, monkeypatch):
    windows_readers(monkeypatch, tmp_path)
    new = tmp_path / "BetterAgentTerminal"
    new.write_text("not a directory")
    assert setup._profiles({}) == ([], "DATA_DIRECTORY_INVALID")
    with pytest.raises(config.TokenUnavailable, match="^DATA_DIRECTORY_INVALID$"):
        config._token_from_bat_profiles(None, "remote-1")


def test_directory_read_denied_does_not_fall_back(tmp_path):
    denied = tmp_path / "BetterAgentTerminal"
    denied.mkdir()
    (tmp_path / "org.tonyq.better-agent-terminal").mkdir()
    denied.chmod(0)
    try:
        with pytest.raises(SourceUnavailable, match="^DATA_DIRECTORY_UNREADABLE$"):
            select_profiles_directory(platform="win32", environment={"APPDATA": str(tmp_path)}, home=tmp_path)
    finally:
        denied.chmod(0o700)


def test_legacy_token_bounds_are_not_narrowed():
    token = "x" * (64 * 1024 + 1)
    contents = json.dumps({"enc": False, "data": json.dumps({"tokens": {"remote-1": token}})})
    assert parse_token_store(contents, mode=TokenMode.LEGACY_RUNTIME).lookup("remote-1").token == token
    assert parse_token_store(contents, mode=TokenMode.ADMISSION).lookup("remote-1").reason == "TOKEN_INVALID"


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
