"""The managed bootstrap contract shared with native UI recovery tests."""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from bat_agent_connector import __version__, api_auth
from bat_agent_connector.api_v1 import ApiV1
from bat_agent_connector.config import Config
from bat_agent_connector.task_daemon import TaskDaemon

GOLDEN = Path(__file__).parent / "fixtures" / "managed-bootstrap.json"


@pytest.mark.parametrize("managed", [True, False])
async def test_managed_bootstrap_matches_shared_golden(tmp_path, managed):
    daemon = TaskDaemon(Config(hosts={}), tmp_path / "journal.db")
    if managed:
        daemon.managed_installation = {"installation_id": "fixture-installation", "runtime_version": __version__}
    principal = api_auth.Principal("fixture-operator", frozenset({"observe", "operate"}))
    try:
        status, response = await ApiV1(daemon).bootstrap(principal)
        assert status == 200
        caps_status, capabilities = await ApiV1(daemon).capabilities(principal)
        assert caps_status == 200 and capabilities == response["capabilities"]
        identity = {key: response["sync"][key] for key in ("server_id", "principal_id")}
        if managed:
            assert response["capabilities"]["desktop_identity"] == identity
        else:
            assert "desktop_identity" not in response["capabilities"]
            assert "managed_installation" not in response["capabilities"]
        assert response["capabilities"]["identity"] == identity
        assert response["sync"]["checkpoint"]["token"].startswith("s1.")
        assert set(response["capabilities"]["build"]) == {"version", "commit", "source"}
        # Only nondeterministic identities, signed proof and build provenance are redacted.
        # Everything else is the real handler output, including all capability fields.
        normalized = json.loads(json.dumps(response).replace(identity["server_id"], "fixture-server")
            .replace(identity["principal_id"], "fixture-principal")
            .replace(response["sync"]["checkpoint"]["token"], "fixture-checkpoint-proof"))
        normalized["capabilities"]["build"]["commit"] = None
        normalized["capabilities"]["build"]["source"] = "package"
        if managed and os.environ.get("BATC_UPDATE_BOOTSTRAP_GOLDEN") == "1":
            GOLDEN.write_text(json.dumps(normalized, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        expected = json.loads(GOLDEN.read_text(encoding="utf-8"))
        if not managed:
            del expected["capabilities"]["desktop_identity"]
            del expected["capabilities"]["managed_installation"]
        assert normalized == expected
    finally:
        await daemon.fleet.close()
        await daemon.inventory.close()
        daemon.journal.close()
