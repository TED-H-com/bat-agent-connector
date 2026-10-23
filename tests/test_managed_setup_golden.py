"""Setup evidence shared by the real Python producer and browser status tests."""
from __future__ import annotations

import json
import os
from pathlib import Path

from tests import test_managed_setup as producer

installed = producer.installed
setup_evidence = producer.setup_evidence
connection_evidence = producer.connection_evidence

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
        for facet in state["hosts"][0]["connection_observation"]["facets"].values():
            if facet["observed_at"] is not None:
                facet["observed_at"] = state["hosts"][0]["connection_checked_at"]
            if facet["valid_until"] is not None:
                facet["valid_until"] = "2027-01-15T08:13:00Z"
    _golden("legacy", _sanitize(document))


def test_original_connection_fields_remain_byte_identical():
    document = json.loads(GOLDEN.read_text())
    fields = ["connected", "connection_status", "connection_checked_at"]
    actual = {name: {key: state["hosts"][0][key] for key in fields} for name, state in document["states"].items()}
    # Frozen from the existing producer golden before the additive envelope.
    assert json.dumps(actual, separators=(",", ":")) == (
        '{"unreachable":{"connected":false,"connection_status":"unreachable","connection_checked_at":1800000600.0},'
        '"checking":{"connected":null,"connection_status":"checking","connection_checked_at":1799999700.0},'
        '"connected":{"connected":true,"connection_status":"connected","connection_checked_at":1800000600.0}}')


async def test_connection_observation_matches_shared_golden(connection_evidence):
    document = {}
    # All additional reasons and shapes come from the actual producers. Replace
    # endpoint-derived bindings and local values, never invent a typed reason.
    extra = json.dumps(connection_evidence["cases"])
    source_op = connection_evidence["operation"]
    extra = extra.replace(source_op["params"]["url"], "wss://bat.example:9876")
    extra = extra.replace(source_op["params"]["fingerprint"], "A" * 64)
    extra = extra.replace(source_op["result"]["revision"], "b" * 64)
    document["observations"] = json.loads(extra)
    _golden("observations", _sanitize(document["observations"]))


def _sanitize(document):
    bindings = {}
    def sanitize(value):
        if isinstance(value, list):
            return [sanitize(item) for item in value]
        if not isinstance(value, dict):
            return value
        result = {}
        for key, item in value.items():
            if key == "binding_version":
                bindings.setdefault(item, str(len(bindings) + 1) * 64)
                result[key] = bindings[item]
            elif key == "error" and isinstance(item, str):
                result[key] = None  # Unbounded exception text is never a display input.
            elif key == "discovery":
                continue  # Setup does not consume Inventory's discovery diagnostics.
            else:
                result[key] = sanitize(item)
        return result
    return sanitize(document)


def _golden(part, document):
    saved = json.loads(GOLDEN.read_text(encoding="utf-8"))
    expected = {key: value for key, value in saved.items() if key != "observations"} if part == "legacy" else saved.get(part)
    if os.environ.get("BATC_UPDATE_SETUP_GOLDEN") == "1":
        updated = {**document, **({"observations": saved["observations"]} if "observations" in saved else {})} if part == "legacy" else {**saved, part: document}
        GOLDEN.write_text(json.dumps(updated, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        expected = document
    assert document == expected
