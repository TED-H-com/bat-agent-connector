"""Real setup journal/write/readback against the shared source-policy vectors."""
import json
from urllib.parse import urlsplit

import pytest

from bat_agent_connector import managed_setup as setup
from bat_agent_connector.config import load_config, parse_config, tomllib
from bat_agent_connector.operations import OperationError
from tests import test_managed_setup as managed_tests
from tests.test_connection_sources import VECTORS, windows_readers
from tests.test_managed_setup import create, settle

installed = managed_tests.installed


@pytest.mark.parametrize("row", VECTORS["token_stores"], ids=lambda row: row["id"])
async def test_admission_to_saved_runtime_on_windows(installed, mock, tmp_path, monkeypatch, row):
    daemon, principal, root = installed
    roaming = tmp_path / "roaming"
    windows_readers(monkeypatch, roaming)
    directory = roaming / "BetterAgentTerminal" / "profiles"
    directory.mkdir(parents=True)
    endpoint = urlsplit(mock.url)
    index = directory / "index.json"
    index.write_text(json.dumps({"profiles": [{"id": row["profile_id"], "type": "remote",
        "remoteHost": endpoint.hostname, "remotePort": endpoint.port,
        "remoteFingerprint": mock.fingerprint}]}))
    store = directory / "remote-tokens.enc.json"
    store.write_text(row["store"].replace("synthetic-token", mock.token))
    store.chmod(0o600)
    before = [(path.read_bytes(), path.stat().st_mtime_ns) for path in (index, store)]
    config_path = root / "config" / "hosts.toml"
    original_config = config_path.read_bytes()
    state = setup.state(daemon, principal)
    assert state["profiles_error"] is None
    available = row["expected"]["python_admission"]["state"] == "available"
    assert state["profiles"][0]["token_available"] is available
    params = {"import_profile_id": row["profile_id"]}
    if not available:
        with pytest.raises(OperationError) as error:
            create(installed, "setup.host", {"host": "fixture"}, params)
        assert error.value.code == "PROFILE_UNAVAILABLE"
        assert not daemon.ops.list()["operations"]
        assert not mock.auth_frames
        assert config_path.read_bytes() == original_config
    else:
        result = await settle(installed, create(installed, "setup.host", {"host": "fixture"}, params))
        assert result["status"] == "succeeded", result
        written = tomllib.loads(config_path.read_text())
        assert written["hosts"]["fixture"]["bat_profiles_dir"] == str(directory)
        assert mock.token not in config_path.read_text() + json.dumps(result) + json.dumps(state)
        # Both the real saved reference and an unconfigured directory default
        # resolve the profile seen by setup in this same Windows temporary tree.
        assert load_config(config_path).host("fixture").resolve_token() == mock.token
        default_host = dict(written["hosts"]["fixture"])
        default_host.pop("bat_profiles_dir")
        assert parse_config({"hosts": {"fixture": default_host}}).host("fixture").resolve_token() == mock.token
        # Saved references are pinned; a new default selection cannot redirect it.
        monkeypatch.setenv("APPDATA", str(tmp_path / "different-roaming"))
        assert load_config(config_path).host("fixture").resolve_token() == mock.token
    assert [(path.read_bytes(), path.stat().st_mtime_ns) for path in (index, store)] == before
