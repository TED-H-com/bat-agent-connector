"""Review regressions through real readers; every file stays in disk tmp_path."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from bat_agent_connector import managed_setup as setup
from bat_agent_connector.connection_sources import TokenMode, parse_token_store, read_token_store
from bat_agent_connector.operations import OperationError
from tests import test_managed_setup as setup_producer
from tests.source_policy_inputs import format_error_store_inputs, profile_index_inputs, token_file_inputs

create = setup_producer.create
installed = setup_producer.installed


@pytest.mark.parametrize("row", format_error_store_inputs(), ids=lambda r: r["id"])
def test_format_errors_precede_ambiguity(row):
    result = parse_token_store(row["store"], mode=TokenMode.ADMISSION).lookup("remote-1")
    print(row["id"], result.state, result.reason)
    assert (result.state, result.reason) == ("unsupported", "TOKEN_STORE_FORMAT")


@pytest.mark.parametrize("host", ["evil.test／good.test", "10.0.0.5：9876", "user﹫evil.test"])
async def test_lookalike_profile_import_refused_before_hosts_write(installed, tmp_path, monkeypatch, host):
    daemon, principal, root = installed
    directory = tmp_path / "profiles"
    directory.mkdir()
    remote = {"id": "remote-1", "type": "remote", "remoteHost": host,
              "remotePort": 9876, "remoteFingerprint": "AA" * 32}
    (directory / "index.json").write_text(json.dumps({"profiles": [remote]}), encoding="utf-8")
    store = directory / "remote-tokens.enc.json"
    store.write_text(json.dumps({"enc": False, "data": json.dumps({"tokens": {"remote-1": "synthetic-token"}})}))
    store.chmod(0o600)
    monkeypatch.setattr(setup, "_profile_directory", lambda _: directory)
    path = root / "config" / "hosts.toml"
    before = path.read_bytes(), path.stat().st_mtime_ns
    with pytest.raises(OperationError, match="Select a readable BAT profile"):
        create(installed, "setup.host", {"host": "fixture"}, {"import_profile_id": "remote-1"})
    assert (path.read_bytes(), path.stat().st_mtime_ns) == before
    assert not daemon.journal.db.execute("SELECT 1 FROM operations").fetchone()


@pytest.mark.parametrize("row", token_file_inputs(), ids=lambda r: r["id"])
def test_file_admission_matches_private_file_rule(tmp_path, row):
    if row.get("posix_only") and os.name == "nt":
        pytest.skip("POSIX uid/mode checks do not model Windows file access")
    path = tmp_path / "remote-tokens.enc.json"
    path.write_bytes(bytes(row["store_bytes"]))
    path.chmod(row["mode"])
    result = read_token_store(tmp_path, mode=TokenMode.ADMISSION).lookup("remote-1")
    print(row["id"], result.state, result.reason)
    expected = ("available", "TOKEN_AVAILABLE") if row["id"] == "posix-mode-0600" else ("unavailable", "TOKEN_STORE_UNREADABLE")
    assert (result.state, result.reason) == expected


@pytest.mark.skipif(os.name == "nt", reason="LC_ALL encoding probe is POSIX; shared UTF-8 vector covers Windows")
def test_raw_chinese_index_is_utf8_independent_of_locale(tmp_path):
    row = next(r for r in profile_index_inputs() if r["id"] == "raw-utf8-chinese-name")
    assert "測試連線" in row["index"] and "\\u" not in row["index"]
    (tmp_path / "index.json").write_bytes(row["index"].encode("utf-8"))
    code = """import json,locale,sys
from bat_agent_connector.managed_setup import _profiles
assert locale.getpreferredencoding(False).lower() in ('ascii','ansi_x3.4-1968')
print(json.dumps(_profiles({'bat':{'profiles_dir':sys.argv[1]}})))
"""
    result = subprocess.run([sys.executable, "-c", code, str(tmp_path)], check=True,
        env={**os.environ, "LC_ALL": "C", "PYTHONUTF8": "0", "PYTHONCOERCECLOCALE": "0"},
        capture_output=True, encoding="ascii")
    profiles, error = json.loads(result.stdout)
    assert error is None and profiles[0]["name"] == "測試連線"


def test_catalog_contract_links_are_present():
    root = Path(__file__).resolve().parents[1]
    assert "bat-source-catalog.md" in (root / "desktop/fleet-core/README.md").read_text()
    row = next(row for row in (root / "docs/design/fleet-configuration.md").read_text().splitlines() if "| D3 BAT" in row)
    assert "bat-source-catalog.md" in row


@pytest.mark.skipif(os.name == "nt", reason="POSIX symlink fixture; Windows reparse behavior stays a native CI gate")
def test_admission_listing_refuses_linked_profiles_without_changing_runtime(tmp_path):
    directory = tmp_path / "actual"
    directory.mkdir()
    row = next(r for r in profile_index_inputs() if r["id"] == "raw-utf8-chinese-name")
    (directory / "index.json").write_text(row["index"], encoding="utf-8")
    store = directory / "remote-tokens.enc.json"
    store.write_text(json.dumps({"enc": False, "data": json.dumps({"tokens": {"remote-1": "synthetic-token"}})}))
    store.chmod(0o600)
    linked = tmp_path / "profiles"
    linked.symlink_to(directory, target_is_directory=True)
    assert read_token_store(linked, mode=TokenMode.LEGACY_RUNTIME).lookup("remote-1").state == "available"
    profiles, error = setup._profiles({"bat": {"profiles_dir": str(linked)}})
    assert profiles == []
    assert error == "BAT profiles are unavailable; enter the host and its trusted fingerprint"


@pytest.mark.skipif(os.name == "nt", reason="Private POSIX uid validation uses getuid, not Windows ownership semantics")
def test_foreign_owner_metadata_is_refused_with_real_uid(tmp_path):
    from bat_agent_connector._private_files import _validate

    path = tmp_path / "store"
    path.write_bytes(b"{}")
    path.chmod(0o600)
    metadata = path.stat()
    # No privileged chown and no global stat/os monkeypatch: validate a foreign
    # owner using this real disk file's other metadata and the real getuid().
    fields = list(metadata)
    fields[4] = os.getuid() + 1
    foreign = os.stat_result(fields)
    with pytest.raises(ValueError, match="owned by the current user"):
        _validate(foreign, 1024 * 1024)
