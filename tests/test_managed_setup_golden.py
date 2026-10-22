"""Setup evidence shared by the real Python producer and browser status tests."""
from __future__ import annotations

import json
import os
from pathlib import Path

from tests import test_managed_setup as producer

installed = producer.installed
setup_evidence = producer.setup_evidence

GOLDEN = Path(__file__).parent / "fixtures" / "managed-setup-evidence.json"


async def test_managed_setup_evidence_matches_shared_golden(setup_evidence):
    operation = setup_evidence["operation"]
    # Project the receipt fields the setup page consumes, plus the producer's result.
    # Redact random IDs, local TLS endpoint/pin, configuration hashes and times only.
    receipt = {key: operation[key] for key in ("operation_id", "actor", "action", "target", "params",
        "preconditions", "idempotency_key", "status", "created_at", "updated_at", "result")}
    document = json.loads(json.dumps({"operation": receipt, "states": setup_evidence["states"]})
        .replace(operation["params"]["url"], "wss://bat.example:9876")
        .replace(operation["params"]["fingerprint"], "A" * 64)
        .replace(operation["params"]["secret_ref"], "setup_" + "b" * 32)
        .replace(operation["actor"], "desktop-fixture")
        .replace(operation["operation_id"], "op_" + "c" * 32)
        .replace(operation["preconditions"]["config_revision"], "a" * 64)
        .replace(operation["result"]["revision"], "b" * 64))
    document["operation"].update(created_at=1799999990.0, updated_at=1800000000.0)
    for condition, state in document["states"].items():
        state["hosts"][0]["connection_checked_at"] = 1799999700.0 if condition == "checking" else 1800000600.0
    if os.environ.get("BATC_UPDATE_SETUP_GOLDEN") == "1":
        GOLDEN.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    assert document == json.loads(GOLDEN.read_text(encoding="utf-8"))
