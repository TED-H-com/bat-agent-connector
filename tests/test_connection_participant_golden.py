"""Native record inputs captured from the real connection HTTP producers."""
import json
import os
from pathlib import Path

from bat_agent_connector import connection_handoff as handoff
from tests import test_setup_connections as producer
from tests.operation_helpers import settle_operations

installed = producer.installed
served_setup = producer.served_setup
source = producer.source
GOLDEN = Path("desktop/fleet-core/tests/fixtures/connection-participant-wire.json")


async def test_participant_wire_matches_real_producers(installed, served_setup, source):
    result = {}
    for outcome in ("applied", "refused", "uncertain"):
        _, ident = await producer.accept(served_setup, producer.intent(source, action="setup.connection.apply"),
            key="participant-" + outcome)
        await settle_operations(installed[0].ops)
        status, claimed = await served_setup("POST", producer.HANDOFF, producer.handoff_request(ident))
        assert status == 200
        native = claimed["native_handoff"]
        status, readback = await served_setup("POST", producer.HANDOFF, producer.handoff_request(ident, mode="readback"))
        assert status == 200
        wire = producer.participant_receipt(native, outcome)
        status, ack = await served_setup("POST", producer.RECEIPT, wire)
        assert status == 200
        case = {"claim": claimed, "readback": readback, "wire": wire, "ack": ack}
        status, case["settled_readback"] = await served_setup(
            "POST", producer.HANDOFF, producer.handoff_request(ident, mode="readback"))
        assert status == 200
        assert case["settled_readback"]["native_handoff"]["receipt"] == ack["receipt"]
        if outcome == "uncertain":
            wire = producer.participant_receipt(native, "abandoned")
            status, ack = await served_setup("POST", producer.RECEIPT, wire)
            assert status == 200
            case.update(definitive=wire, definitive_ack=ack)
        # De-identify values only; keep every producer field and exact role shape.
        replacements = {
            native["server_id"]: "11111111-1111-4111-8111-111111111111",
            native["principal_id"]: "1" * 64,
            ident: "op_" + "a" * 32,
            native["review_digest"]: "b" * 64,
            native["effect_id"]: "effect_" + handoff.digest(["op_" + "a" * 32, handoff.STEP]),
            native["challenge"]: "c" * 64,
        }
        encoded = json.dumps(case, ensure_ascii=False)
        for actual, example in replacements.items():
            encoded = encoded.replace(actual, example)
        case = json.loads(encoded)
        for mode in ("claim", "readback", "settled_readback"):
            case[mode]["native_handoff"]["expires_at"] = 1800000300.0
            case[mode]["native_handoff"]["epoch"] = 1
        for key in ("ack", "definitive_ack"):
            if key in case:
                evidence = case[key]["receipt"]
                evidence["epoch"] = 1
                evidence["receipt_ref"] = "receipt_" + handoff.digest(
                    {k: v for k, v in evidence.items() if k != "receipt_ref"})
        case["settled_readback"]["native_handoff"]["receipt"] = case["ack"]["receipt"]
        for key in ("wire", "definitive"):
            if key in case:
                case[key]["epoch"] = 1
        result[outcome] = case
    if os.environ.get("BATC_UPDATE_PARTICIPANT_GOLDEN") == "1":
        GOLDEN.parent.mkdir(parents=True, exist_ok=True)
        GOLDEN.write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    assert result == json.loads(GOLDEN.read_text(encoding="utf-8"))
