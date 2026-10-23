"""Native discovery pages captured from the real authenticated HTTP producers."""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

from bat_agent_connector import dashboard_sync
from tests import test_setup_connections as producer
from tests.operation_helpers import settle_operations

installed = producer.installed
served_setup = producer.served_setup
source = producer.source
GOLDEN = Path("desktop/src-tauri/tests/fixtures/connection-discovery.json")


async def test_connection_discovery_matches_real_producers(installed, served_setup, source):
    daemon, principal, root = installed
    _, ident = await producer.accept(served_setup, producer.intent(source, action="setup.connection.apply"))
    await settle_operations(daemon.ops)
    result = {}
    for name, route in {
        "operations": "/api/v1/operations?limit=200",
        "operation": "/api/v1/operations/" + ident,
        "connections": "/api/v1/connections?installation_ref=desktop-a",
    }.items():
        status, result[name] = await served_setup("GET", route)
        assert status == 200
    status, bootstrap = await served_setup("GET", "/api/v1/bootstrap")
    assert status == 200
    result["bootstrap_operations"] = bootstrap["snapshot"]["operations"]
    cursor = daemon.journal.api_head() - 1
    result["replay_sync"] = dashboard_sync.checkpoint(daemon.journal, principal, cursor)
    checkpoint = result["replay_sync"]["checkpoint"]
    status, result["events"] = await served_setup("GET",
        f"/api/v1/events?after={cursor}&limit=100&checkpoint={checkpoint['token']}")
    assert status == 200
    status, result["gap"] = await served_setup("GET", "/api/v1/events?after=999999&checkpoint=invalid")
    assert status == 409
    native = result["operation"]["operation"]["external_refs"]["admission_binding"]
    replacements = {str(root): "/fixture", ident: "op_" + "a" * 32, principal.actor: "fixture-operator",
        native["server_id"]: "11111111-1111-4111-8111-111111111111",
        native["principal_id"]: "1" * 64, native["review_digest"]: "b" * 64,
        native["controller_authority"]["controller_id"]: "ctr_" + "e" * 32}
    preserved = {value for artifact in native["native_plan"]["artifacts"]
        for value in (artifact["before_digest"], artifact["after_digest"])} | {native["native_plan"]["review_digest"]}
    def normalize(value, key=""):
        if isinstance(value, dict):
            return {k: normalize(v, k) for k, v in value.items()}
        if isinstance(value, list):
            return [normalize(v) for v in value]
        if key.endswith(("_at", "_at_epoch")) and isinstance(value, (float, int)):
            return 1800000000.0
        if isinstance(value, str):
            if key.endswith("_at"):
                return "2027-01-15T08:00:00Z"
            if key == "token":
                return "fixture-checkpoint-proof"
            for actual, example in replacements.items():
                value = value.replace(actual, example)
            return re.sub(r"[0-9a-f]{64}", lambda m: m[0] if m[0] in preserved or m[0] in {"1" * 64, "b" * 64} else "e" * 64, value)
        return value
    result = normalize(result)
    if os.environ.get("BATC_UPDATE_DISCOVERY_GOLDEN") == "1":
        GOLDEN.parent.mkdir(parents=True, exist_ok=True)
        GOLDEN.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    assert result == json.loads(GOLDEN.read_text(encoding="utf-8"))
