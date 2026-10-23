"""Exchange real Python/Rust codec output; no native executor is simulated."""
import copy
import json
import os
from pathlib import Path

import pytest

from bat_agent_connector import connection_contract as contract
from bat_agent_connector import connection_handoff as handoff
from bat_agent_connector.operations import OperationError
from tests import test_connection_reference_contract as source


def vectors():
    rows = []

    def add(name, body, valid=True, plan=None):
        rows.append(dict(name=name, valid=valid, plan=plan, wire=contract.canonical(body).decode()))

    absent = source.plan()
    present = copy.deepcopy(absent)
    present["artifacts"][1].update(before=source.PRESENT, after=source.PRESENT)
    for name, plan in (("absent", absent), ("present", present)):
        add(name, contract.validate_native_plan_v2(plan, action=source.ACTION, kind=source.KIND))
    # Reuse the existing Python validator's parameter vectors, not another shape list.
    for index, (path, value) in enumerate(source.test_plan_rejects_invalid_vectors.pytestmark[0].args[1]):
        body = source.plan()
        target = body
        for key in path[:-1]:
            target = target[key]
        target[path[-1]] = value
        add(f"invalid-plan-{index}", body, False)
    for outcome, phases in contract.CONTRACT["v2"]["effect_kinds"][source.KIND]["matrix"].items():
        plan = source.plan()
        if outcome == "unchanged":
            plan["artifacts"][0]["after"] = plan["artifacts"][0]["before"]
        phase, codes = next(iter(phases.items()))
        body = source.receipt(source.claims(plan, maximum=True), outcome, phase, codes[0])
        source.validate(body, plan)
        add(outcome, body, plan=plan)
    for observation in contract.CONTRACT["observations"]:
        body = source.receipt(outcome="uncertain", phase="publishing", code="LOCAL_THIRD_BYTES")
        role = body["roles"][0]
        role["observation"] = observation
        role.pop("after")
        if observation != "unreadable":
            role["after"] = source.PRESENT if observation == "present" else source.ABSENT
        add("observation-" + observation, body, plan=absent)
    for field, value in (("code", "unknown"), ("epoch", True), ("epoch", 1.0),
                         ("expected_revision", 2**63), ("contract_version", 1),
                         ("native_review_digest", "0" * 64), ("action", "setup.connection.apply")):
        add("invalid-receipt-" + field + str(value), {**source.receipt(), field: value}, False, absent)
    return rows


def check(row):
    body = contract.decode_native_json(row["wire"], code="INVALID_NATIVE_PLAN")
    if row["plan"] is None:
        contract.validate_native_plan_v2(body, action=source.ACTION, kind=source.KIND)
        with pytest.raises(OperationError):
            contract.validate_native_plan(body)
    else:
        handoff.validate_receipt_v2(body, action=source.ACTION, kind=source.KIND, plan=row["plan"])
        with pytest.raises(OperationError):
            handoff.validate_receipt(body)


def test_python_vectors_and_rust_encoder():
    actual = "".join(contract.canonical(row).decode() + "\n" for row in vectors())
    path = Path("tests/fixtures/reference-codec-python.jsonl")
    if os.environ.get("BATC_UPDATE_REFERENCE_PYTHON") == "1":
        path.write_text(actual, encoding="utf-8")
    assert path.read_text(encoding="utf-8") == actual
    for line in Path("tests/fixtures/reference-codec-rust.jsonl").read_text(encoding="utf-8").splitlines():
        row = json.loads(line)
        if row["valid"]:
            check(row)
        else:
            with pytest.raises(OperationError):
                check(row)
