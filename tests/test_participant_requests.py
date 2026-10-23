"""Rust request bytes pass unchanged through the real handoff HTTP handlers."""
import json
from pathlib import Path
from types import SimpleNamespace

from bat_agent_connector import connection_handoff as handoff
from bat_agent_connector import operations
from tests import test_setup_connections as producer
from tests.operation_helpers import settle_operations

installed = producer.installed
served_setup = producer.served_setup
source = producer.source


async def test_rust_request_bytes_are_accepted_unchanged(installed, served_setup, source, monkeypatch):
    requests = json.loads((Path(__file__).resolve().parents[1] / "desktop/fleet-core/tests/fixtures/participant-requests.json").read_bytes())
    digest = handoff.digest
    # Stable synthetic review and signer outputs match the de-identified Rust inputs.
    # Admission, source/revision checks, sent fence and receipt validation stay real.
    monkeypatch.setattr(handoff, "digest", lambda value: "b" * 64
        if isinstance(value, dict) and {"action", "params", "plan"} <= value.keys() else digest(value))
    monkeypatch.setattr(handoff, "_challenge", lambda *_: "c" * 64)
    monkeypatch.setattr(handoff, "now", lambda: 1800000000.0)
    with monkeypatch.context() as fixed:
        fixed.setattr(operations, "uuid", SimpleNamespace(uuid4=lambda: SimpleNamespace(hex="a" * 32)))
        _, ident = await producer.accept(served_setup, producer.intent(source, action="setup.connection.apply"))
    daemon = installed[0]
    await settle_operations(daemon.ops)
    before = (installed[2] / "config/hosts.toml").read_bytes()
    for name, path in [("claim", producer.HANDOFF), ("readback", producer.HANDOFF),
                       ("receipt", producer.RECEIPT), ("receipt", producer.RECEIPT)]:
        raw = requests[name].encode("utf-8")  # No JSON re-encoding, substitution or repair.
        status, response = await served_setup.raw_request(path, raw)
        assert status == 200, response
        if name == "receipt":
            reply = json.loads(response.split(b"\r\n\r\n", 1)[1])
            assert reply["receipt"]["operation_id"] == ident
            assert reply["receipt"]["outcome"] == "refused"
            assert reply["receipt"]["receipt_ref"] == "receipt_" + digest(
                {key: value for key, value in json.loads(raw).items() if key != "challenge"})
    row = daemon.journal.db.execute("SELECT response FROM operation_steps WHERE operation_id=? AND name=?",
        (ident, handoff.STEP)).fetchone()
    assert len(json.loads(row[0])["receipts"]) == 1
    assert daemon.ops.get(ident)["external_refs"]["native_effects_sent"] is True
    assert (installed[2] / "config/hosts.toml").read_bytes() == before
