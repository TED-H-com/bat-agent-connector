"""The managed bootstrap contract shared with native UI recovery tests."""
from __future__ import annotations

import json
import os
from pathlib import Path

from bat_agent_connector import __version__, api_auth
from bat_agent_connector.api_v1 import ApiV1
from bat_agent_connector.config import Config
from bat_agent_connector.task_daemon import TaskDaemon

GOLDEN = Path(__file__).parent / "fixtures" / "managed-bootstrap.json"


async def test_managed_bootstrap_matches_shared_golden(tmp_path):
    daemon = TaskDaemon(Config(hosts={}), tmp_path / "journal.db")
    daemon.managed_installation = {"installation_id": "fixture-installation", "runtime_version": __version__}
    principal = api_auth.Principal("fixture-operator", frozenset({"observe", "operate"}))
    try:
        status, response = await ApiV1(daemon).bootstrap(principal)
        assert status == 200
        identity = {key: response["sync"][key] for key in ("server_id", "principal_id")}
        assert response["capabilities"]["desktop_identity"] == identity
        assert response["capabilities"]["identity"] == identity
        assert response["sync"]["checkpoint"]["token"].startswith("s1.")
        # Only nondeterministic identities, signed proof and build provenance are redacted.
        # Everything else is the real handler output, including all capability fields.
        normalized = json.loads(json.dumps(response).replace(identity["server_id"], "fixture-server")
            .replace(identity["principal_id"], "fixture-principal")
            .replace(response["sync"]["checkpoint"]["token"], "fixture-checkpoint-proof"))
        normalized["capabilities"]["build"]["commit"] = None
        normalized["capabilities"]["build"]["source"] = "package"
        if os.environ.get("BATC_UPDATE_BOOTSTRAP_GOLDEN") == "1":
            GOLDEN.write_text(json.dumps(normalized, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        assert normalized == json.loads(GOLDEN.read_text(encoding="utf-8"))
    finally:
        await daemon.fleet.close()
        await daemon.inventory.close()
        daemon.journal.close()
