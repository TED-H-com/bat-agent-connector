"""Production Rust request builders against a real disk-backed Python operation."""
import json
import os
import subprocess
from pathlib import Path

from bat_agent_connector import connection_handoff as handoff
from tests import conftest
from tests import test_setup_connections as producer
from tests.operation_helpers import settle_operations

isolated_dirs = conftest.isolated_dirs
installed = producer.installed
served_setup = producer.served_setup
source = producer.source


async def test_original_rust_requests_and_expired_readback(installed, served_setup, source, monkeypatch):
    # This native-job check fails explicitly if its required oracle was not built.
    oracle = Path(os.environ.get("BATC_PARTICIPANT_REQUEST_ORACLE", ""))
    assert oracle.is_file(), "Build participant_request_oracle before running this test"

    def run(command, value=None):
        result = subprocess.run(  # noqa: S603 - native-job oracle, never a request path
            [str(oracle), command], input=None if value is None else json.dumps(value).encode(),
            capture_output=True, check=True)
        return json.loads(result.stdout)

    async def send(path, body, expected=200):
        # Exact Rust bytes. Python never parses/re-encodes or repairs a request.
        status, raw = await served_setup.raw_request(path, body.encode())
        response = json.loads(raw.split(b"\r\n\r\n", 1)[1])
        assert status == expected, response
        return response

    plan = run("prepare")
    body = producer.intent(source, action="setup.connection.apply")
    body["params"]["native_plan"] = plan["manifest"]
    _, ident = await producer.accept(served_setup, body)
    daemon = installed[0]
    await settle_operations(daemon.ops)
    operation = daemon.ops.get(ident)
    inputs = {"plan": plan, "operation": operation}
    initial = run("requests", inputs)
    before = (installed[2] / "config/hosts.toml").read_bytes()
    assert operation["external_refs"].get("native_effects_sent") is not True
    envelope = await send(producer.HANDOFF, initial["claim"])
    assert daemon.ops.get(ident)["external_refs"]["native_effects_sent"] is True
    requests = run("requests", {**inputs, "envelope": envelope})
    assert requests["claim"] == initial["claim"]
    assert requests["readback"] == initial["readback"]
    uncertain = await send(producer.RECEIPT, requests["uncertain"])
    evidence = json.loads(requests["uncertain"])
    evidence.pop("challenge")
    reference = "receipt_" + handoff.digest(evidence)
    assert uncertain == {"receipt": {**evidence, "receipt_ref": reference}, "replayed": False}
    # Deterministic time observation only; original IDs, signer and review remain real.
    monkeypatch.setattr(handoff, "now", lambda: envelope["native_handoff"]["expires_at"] + 1)
    expired = await send(producer.HANDOFF, requests["readback"])
    assert expired["read_only"] is True
    assert expired["native_handoff"]["receipt"] == uncertain["receipt"]
    assert expired["native_handoff"]["epoch"] == envelope["native_handoff"]["epoch"]
    assert expired["native_handoff"]["challenge"] == envelope["native_handoff"]["challenge"]
    await send(producer.HANDOFF, requests["claim"], 409)
    await send(producer.RECEIPT, requests["uncertain_other"], 409)
    # A different uncertain receipt is forbidden; one definitive successor is accepted.
    successor = await send(producer.RECEIPT, requests["abandoned"])
    assert successor["receipt"]["outcome"] == "abandoned"
    await send(producer.RECEIPT, requests["uncertain"])  # exact historical replay
    await send(producer.RECEIPT, requests["refused"], 409)
    row = daemon.journal.db.execute("SELECT response FROM operation_steps WHERE operation_id=? AND name=?",
        (ident, handoff.STEP)).fetchone()
    assert len(json.loads(row[0])["receipts"]) == 2
    settled = await send(producer.HANDOFF, requests["readback"])
    assert settled["native_handoff"]["receipt"] == successor["receipt"]
    assert (installed[2] / "config/hosts.toml").read_bytes() == before
