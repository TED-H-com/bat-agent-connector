"""Frozen native wire shapes; no native transport, file generator or live BAT."""
import copy

import pytest

from bat_agent_connector import connection_handoff as handoff
from bat_agent_connector.operations import OperationError


def receipt():
    return {"contract_version": 1, "installation_ref": "desktop-a", "operation_id": "op_" + "a" * 32,
        "step": "native_effects", "review_digest": "b" * 64, "expected_revision": 0,
        "epoch": 1, "effect_id": "effect_" + "c" * 64, "challenge": "d" * 64,
        "outcome": "applied", "roles": [{"role": "inventory", "before_digest": "e" * 64, "after_digest": "f" * 64}]}


def test_frozen_receipt_exact_shape_and_challenge_excluded_from_evidence():
    body = receipt()
    assert set(body) == handoff.RECEIPT_FIELDS
    evidence = handoff.validate_receipt(body)
    assert evidence == {key: value for key, value in body.items() if key != "challenge"}
    assert handoff.digest(evidence) == handoff.digest(dict(reversed(list(evidence.items()))))
    assert set(handoff.CLAIMS) == {"contract_version", "server_id", "principal_id", "installation_ref",
        "operation_id", "step", "review_digest", "expected_revision", "epoch", "effect_id", "expires_at"}
    assert handoff.STEP == "native_effects" and handoff.TTL_S == 300


@pytest.mark.parametrize("change", [
    {"path": "/private/file"}, {"outcome": "success"}, {"epoch": True}, {"expected_revision": -1},
    {"challenge": "unbounded"}, {"roles": []}, {"roles": [{"role": "path", "before_digest": "a" * 64, "after_digest": "b" * 64}]},
    {"outcome": "unchanged"}, {"roles": receipt()["roles"] * 2},
])
def test_receipt_rejects_untyped_or_private_payloads(change):
    body = {**copy.deepcopy(receipt()), **change}
    with pytest.raises(OperationError) as error:
        handoff.validate_receipt(body)
    assert error.value.status == 422
