"""Native connection vocabulary and bounds from the shared contract."""

from __future__ import annotations

import json
import re
from importlib.resources import files

from .operations import OperationError

CONTRACT = json.loads(files(__package__).joinpath("connection_contract.json").read_text(encoding="utf-8"))
VERSION = CONTRACT["contract_version"]
BOUNDS = CONTRACT["bounds"]
DIGEST = re.compile(r"[0-9a-f]{" + str(BOUNDS["digest_hex_chars"]) + r"}")


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()


def require_version(body):
    if (
        not isinstance(body, dict)
        or type(body.get("contract_version")) is not int
        or body["contract_version"] != VERSION
    ):
        raise OperationError(
            "CONNECTION_CONTRACT_UNSUPPORTED",
            "This connection contract is unsupported; update the app / 不支援此連線契約；請更新應用程式",
            409,
        )


def valid_digest(value, *, nullable=False):
    return nullable and value is None or isinstance(value, str) and DIGEST.fullmatch(value) is not None


def invalid_plan():
    raise OperationError(
        "INVALID_NATIVE_PLAN",
        "Use the exact bounded native review commitment / 請使用完整且符合大小限制的本機檢閱承諾",
        422,
    )


def validate_native_plan(plan):
    if (
        not isinstance(plan, dict)
        or set(plan) != {"contract_version", "generator_version", "review_digest", "artifacts"}
        or len(canonical(plan)) > BOUNDS["native_plan_bytes"]
    ):
        invalid_plan()
    require_version(plan)
    if type(plan["generator_version"]) is not int:
        invalid_plan()
    if plan["generator_version"] not in CONTRACT["generator_versions"]:
        raise OperationError(
            "CONNECTION_GENERATOR_UNSUPPORTED",
            "This native generator is unsupported; update the app / 不支援此本機產生器；請更新應用程式",
            409,
        )
    if (
        not valid_digest(plan["review_digest"])
        or not isinstance(plan["artifacts"], list)
        or len(plan["artifacts"]) != len(CONTRACT["roles"])
    ):
        invalid_plan()
    for role, artifact in zip(CONTRACT["roles"], plan["artifacts"], strict=True):
        if (
            not isinstance(artifact, dict)
            or set(artifact) != {"role", "before_digest", "after_digest"}
            or artifact["role"] != role
            or not valid_digest(artifact["before_digest"], nullable=True)
            or not valid_digest(artifact["after_digest"])
        ):
            invalid_plan()
    return plan


def decode_native_json(raw, *, code):
    """Reject ambiguous native wire objects before admission or receipt handling."""
    reason = "Native connection JSON needs unique fields and valid Unicode / 本機連線 JSON 的欄位不可重複，文字須為有效 Unicode"

    def unique_fields(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise OperationError(code, reason, 422)
            value[key] = item
        return value

    try:
        body = json.loads(raw, object_pairs_hook=unique_fields)
        canonical(body)  # Escaped lone surrogates cannot enter commitments or HMACs.
    except (ValueError, TypeError):
        raise OperationError(code, reason, 422) from None
    return body


def effect_v2(body, *, action, kind):
    """The caller supplies the persisted action/kind; wire fields cannot dispatch."""
    v2 = CONTRACT["v2"]
    effect = v2["effect_kinds"].get(kind) if isinstance(kind, str) else None
    if (not effect or effect["action"] != action or not isinstance(body, dict)
            or type(body.get("contract_version")) is not int or body["contract_version"] != v2["contract_version"]
            or body.get("kind") != kind):
        raise OperationError("CONNECTION_CONTRACT_UNSUPPORTED",
            "Unsupported connection effect contract / 不支援此連線效果契約", 409)
    return effect


def bounded_v2(value, limit):
    try:
        return len(canonical(value)) <= limit
    except (ValueError, TypeError):
        return False


def valid_state(value):
    return isinstance(value, dict) and (value == {"state": "absent"} or (
        set(value) == {"state", "digest"} and value["state"] == "present" and valid_digest(value["digest"])))


def validate_native_plan_v2(plan, *, action, kind):
    effect = effect_v2(plan, action=action, kind=kind)
    if (set(plan) != {"contract_version", "kind", "generator_version", "review_digest", "artifacts"}
            or not bounded_v2(plan, BOUNDS["native_plan_bytes"])
            or type(plan["generator_version"]) is not int
            or plan["generator_version"] not in CONTRACT["v2"]["generator_versions"]
            or not valid_digest(plan["review_digest"]) or not isinstance(plan["artifacts"], list)
            or len(plan["artifacts"]) != len(effect["roles"])):
        invalid_plan()
    for name, role in zip(effect["roles"], plan["artifacts"], strict=True):
        if (not isinstance(role, dict) or set(role) != {"role", "before", "after"} or role["role"] != name
                or not valid_state(role["before"]) or not valid_state(role["after"])
                or role["after"]["state"] not in effect["after_states"][name]
                or role["after"]["state"] == "absent" and effect["absent_after_requires_unchanged"]
                and role["before"] != role["after"]):
            invalid_plan()
    return plan
