"""Synthetic codec vectors; these do not claim a native reference participant."""
import copy
import hashlib
import itertools
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from bat_agent_connector import connection_contract as contract
from bat_agent_connector import connection_handoff as handoff
from bat_agent_connector.operations import OperationError

ACTION, KIND = "connection.reference.detach", "reference_detach"
ABSENT = {"state": "absent"}
PRESENT = {"state": "present", "digest": "a" * 64}


def plan():
    return {"contract_version": 2, "kind": KIND, "generator_version": 1, "review_digest": "b" * 64,
        "artifacts": [{"role": "local_catalog", "before": PRESENT.copy(),
                       "after": {"state": "present", "digest": "c" * 64}},
                      {"role": "selection", "before": ABSENT.copy(), "after": ABSENT.copy()}]}


def claims(native_plan=None, *, maximum=False):
    operation = {"action": ACTION, "operation_id": "op_" + "d" * 32,
        "target": {"installation_ref": "😀" * 128 if maximum else "desktop-a", "connection_id": "conn_" + "e" * 32},
        "preconditions": {"expected_revision": 2**63 - 1 if maximum else 1}}
    binding = {"server_id": "12345678-1234-1234-1234-123456789abc", "principal_id": "f" * 64,
               "review_digest": "1" * 64, "native_plan": native_plan or plan()}
    return handoff.build_claims_v2(operation, binding, kind=KIND,
        issued_at=2**63 - 301 if maximum else 100, epoch=2**63 - 1 if maximum else 1)


def receipt(native=None, outcome="applied", phase="readback", code=None):
    native = native or claims()
    return {**{key: native[key] for key in handoff.RECEIPT_FIELDS - {"challenge", "outcome", "roles", "code", "native_review_digest", "phase"}},
        "action": ACTION, "kind": KIND, "connection_id": native["connection_id"], "challenge": "2" * 64,
        "native_review_digest": native["native_plan"]["review_digest"], "outcome": outcome, "phase": phase, "code": code,
        "roles": [{**copy.deepcopy(a), "observation": a["after"]["state"]} for a in native["native_plan"]["artifacts"]]}


def validate(body, native_plan=None):
    return handoff.validate_receipt_v2(body, action=ACTION, kind=KIND, plan=native_plan or plan())


def test_v1_bytes_and_version_separation():
    from tests.test_connection_handoff import receipt as v1_receipt
    raw = Path("src/bat_agent_connector/connection_contract.json").read_bytes()
    original = raw.split(b',\n  "v2":', 1)[0] + b"\n}\n"
    assert hashlib.sha256(original).hexdigest() == "fb556e67511a90c6af30bd7bdd481d0826dabdf61e0ceeba073b286fbf442669"
    old = json.loads(Path("tests/fixtures/native-plan-manifest.json").read_text())["create"]
    assert contract.validate_native_plan(old) == old
    with pytest.raises(OperationError):
        contract.validate_native_plan(plan())
    with pytest.raises(OperationError):
        contract.validate_native_plan_v2(old, action=ACTION, kind=KIND)
    with pytest.raises(OperationError):
        handoff.validate_receipt(receipt())
    with pytest.raises(OperationError):
        validate(v1_receipt())


@pytest.mark.parametrize("path,value", [
    (("extra",), True), (("kind",), "reference_select"), (("contract_version",), 1),
    (("contract_version",), 3), (("contract_version",), True), (("generator_version",), True),
    (("generator_version",), 2), (("artifacts", 1, "after"), {"state": "present"}),
    (("artifacts",), []), (("artifacts", 0, "role"), "selection"),
    (("artifacts", 0, "after"), ABSENT),
    (("artifacts", 0), {"role": "local_catalog", "before": ABSENT, "after": ABSENT}),
    (("artifacts", 1, "before"), {"state": "present", "digest": None}),
    (("artifacts", 1, "after"), {"state": "absent", "digest": hashlib.sha256(b"").hexdigest()}),
    (("artifacts", 0, "before"), {"state": "absent", "digest": hashlib.sha256(b"").hexdigest()}),
    (("artifacts", 1, "before"), PRESENT), (("review_digest",), "x" * 4097),
])
def test_plan_rejects_invalid_vectors(path, value):
    body = plan()
    target = body
    for part in path[:-1]:
        target = target[part]
    target[path[-1]] = copy.deepcopy(value)
    with pytest.raises(OperationError):
        contract.validate_native_plan_v2(body, action=ACTION, kind=KIND)


def test_plan_states_order_and_persisted_action_dispatch():
    body = plan()
    assert contract.validate_native_plan_v2(body, action=ACTION, kind=KIND) == body
    body["artifacts"][0]["before"] = {"state": "present", "digest": hashlib.sha256(b"").hexdigest()}
    assert contract.validate_native_plan_v2(body, action=ACTION, kind=KIND) == body
    body["artifacts"][1].update(before=PRESENT, after=PRESENT)
    assert contract.validate_native_plan_v2(body, action=ACTION, kind=KIND) == body
    body["artifacts"].reverse()
    with pytest.raises(OperationError):
        contract.validate_native_plan_v2(body, action=ACTION, kind=KIND)
    with pytest.raises(OperationError):
        contract.validate_native_plan_v2(plan(), action="setup.connection.apply", kind=KIND)
    with pytest.raises(OperationError):
        contract.validate_native_plan_v2(plan(), action=ACTION, kind="future_kind")


@pytest.mark.parametrize("surface", ["plan", "receipt"])
def test_every_required_key(surface):
    body = plan() if surface == "plan" else receipt()
    check = (lambda b: contract.validate_native_plan_v2(b, action=ACTION, kind=KIND)) if surface == "plan" else validate
    for key in body:
        incomplete = {k: v for k, v in body.items() if k != key}
        with pytest.raises(OperationError):
            check(incomplete)


def test_receipt_matrix_and_observations():
    definition = contract.CONTRACT["v2"]["effect_kinds"][KIND]
    codes = [None, *contract.CONTRACT["codes"], *contract.CONTRACT["v2"]["codes"], "unknown"]
    for outcome, phase, code in itertools.product(contract.CONTRACT["outcomes"], contract.CONTRACT["phases"], codes):
        native_plan = plan()
        if outcome == "unchanged":
            native_plan["artifacts"][0]["after"] = PRESENT.copy()
        body = receipt(claims(native_plan), outcome, phase, code)
        if code in definition["matrix"].get(outcome, {}).get(phase, []):
            assert "challenge" not in validate(body, native_plan)
        else:
            with pytest.raises(OperationError):
                validate(body, native_plan)
    for observation in ("present", "absent", "unreadable"):
        body = receipt(outcome="uncertain", phase="publishing", code="LOCAL_THIRD_BYTES")
        role = body["roles"][0]
        role["observation"] = observation
        role.pop("after")
        if observation != "unreadable":
            role["after"] = PRESENT if observation == "present" else ABSENT
        validate(body)
        role["after"] = ABSENT if observation == "present" else PRESENT
        with pytest.raises(OperationError):
            validate(body)


@pytest.mark.parametrize("fault", ["extra", "order", "before", "after", "review", "unchanged", "kind", "version", "observation"])
def test_receipt_rejects_mismatched_evidence(fault):
    body = receipt()
    if fault == "order":
        body["roles"].reverse()
    elif fault in {"before", "after"}:
        body["roles"][0][fault] = ABSENT
    elif fault == "observation":
        body["roles"][0]["observation"] = []
    else:
        key, value = {"extra": ("path", "/ignored"), "review": ("native_review_digest", "3" * 64),
            "unchanged": ("outcome", "unchanged"), "kind": ("kind", "reference_select"), "version": ("contract_version", 1)}[fault]
        body[key] = value
    with pytest.raises(OperationError):
        validate(body)


def test_hmac_separates_versions_actions_and_kinds():
    ops = SimpleNamespace(context={"daemon": SimpleNamespace(_admin_token="synthetic-key")})
    native = claims()
    token = handoff._challenge_v2(ops, native)
    assert token != handoff._challenge(ops, native)
    for key, value in (("contract_version", 1), ("kind", "future_kind"), ("action", "future.action"),
                       ("connection_id", "conn_" + "0" * 32)):
        assert token != handoff._challenge_v2(ops, {**native, key: value})
    assert native["expires_at"] == 400


def test_claims_bind_original_authority_and_declared_scope():
    native = claims()
    assert native["action"] == ACTION and native["kind"] == KIND and native["connection_id"].startswith("conn_")
    effect = contract.CONTRACT["v2"]["effect_kinds"][KIND]
    assert effect["authorization"] == {"scope": "observe", "bindings": ["principal_id", "installation_ref", "connection_id"]}
    assert set(contract.CONTRACT["v2"]["effect_kinds"]) == {KIND}


def test_wire_budgets_and_maximum_vectors(monkeypatch):
    native_plan = plan()
    native_plan["artifacts"][1].update(before=PRESENT, after=PRESENT)
    native = claims(native_plan, maximum=True)
    body = receipt(native, "refused", "preflight", "CONNECTION_DETACH_WOULD_STOP_TUNNEL")
    validate(body, native_plan)
    for key, value, call in (
        ("native_plan_bytes", native_plan, lambda: contract.validate_native_plan_v2(native_plan, action=ACTION, kind=KIND)),
        ("receipt_bytes", body, lambda: validate(body, native_plan)),
        ("handoff_bytes", {**native, "challenge": "0" * 64, "receipt": None}, lambda: claims(native_plan, maximum=True)),
    ):
        size = len(contract.canonical(value))
        assert size <= contract.BOUNDS[key]
        with monkeypatch.context() as patch:
            patch.setitem(contract.BOUNDS, key, size - 1)
            with pytest.raises(OperationError):
                call()
