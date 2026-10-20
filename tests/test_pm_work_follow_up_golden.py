"""Real Work result producer output shared with follow-up browser tests.

Public HTTP cases and injected producer-boundary facts are named separately.
No action row or reason is constructed by this test.
"""
from __future__ import annotations

import copy
import json
import os
import re
from pathlib import Path

from bat_agent_connector import pm_attempt_result_reads, pm_workspace, registry
from tests.operation_helpers import settle_operations
from tests.test_api_v1 import http, token
from tests.test_pm_plan_operations import OWNER as PLAN_OWNER
from tests.test_pm_work_attempt_result import _start_managed_attempt, pm, ready, start_ready, world
from tests.test_pm_work_follow_up import _post, _refresh
from tests.test_pm_work_packages import action as pkg_action
from tests.test_pm_work_start import STARTER_OWNER

_ = (pm, ready, start_ready, world)
GOLDEN = Path(__file__).parent / "fixtures" / "work-follow-up-decisions.json"


def _normalize(payload, att, operation_id):
    """Retain all producer fields, redacting only volatile evidence/identities."""
    replacements = {
        att["workspace_id"]: "ws_" + "a" * 32,
        att["work_item_id"]: "wi_" + "0" * 19 + "1",
        att["attempt_id"]: "att_" + "b" * 32,
        operation_id: "op_" + "c" * 32,
        att["session_id"]: "search-results",
        att["host"]: "demo",
        STARTER_OWNER.actor: "owner",
    }
    text = json.dumps(payload, ensure_ascii=False)
    for old, new in replacements.items():
        text = text.replace(old, new)
    identifiers = {}

    def identifier(match):
        value = match[0]
        if value in replacements.values():
            return value
        if value not in identifiers:
            prefix, _, suffix = value.rpartition("_")
            identifiers[value] = prefix + "_" + format(len(identifiers) + 1, f"0{len(suffix)}x")
        return identifiers[value]

    text = re.sub(r"\b[a-z][a-z0-9_]*_[0-9a-f]{20,64}\b", identifier, text)
    text = re.sub(r"\b[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\b", "00000000-0000-4000-8000-000000000001", text)
    text = re.sub(r"(?<![a-z0-9_])[0-9a-f]{40,64}(?![a-z0-9_])", lambda match: "1" * len(match[0]), text)

    text = re.sub(r"(?<![a-z0-9_])[0-9a-f]{32}(?![a-z0-9_])", "2" * 32, text)
    text = re.sub(r"after=\d+", "after=0", text)

    def walk(value, key=""):
        if isinstance(value, dict):
            return {k: walk(v, k) for k, v in value.items()}
        if isinstance(value, list):
            return [walk(v, key) for v in value]
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            if key.endswith("_at") or key == "last_seen":
                return 1_790_001_000
            if key in {"cursor", "seq", "head_cursor", "next_cursor"}:
                return 0
        if key == "source_generation" and isinstance(value, str):
            return value.split(":", 1)[0] + ":1790001000"
        if isinstance(value, str) and value.startswith("/") and not value.startswith("/api/"):
            return "/fixture/worktree"
        return value

    return walk(json.loads(text))


async def test_work_follow_up_producer_golden(start_ready, mock, monkeypatch):
    w = start_ready
    ops = w["d"].ops
    att = await _start_managed_attempt(w, mock, key="follow-up-golden")
    await _refresh(w, mock, att, att["turn_marker"], streaming=True)
    status, accepted = await _post(w, att, "Original accepted instruction", key="follow-up-golden-input")
    assert status == 202, accepted
    await settle_operations(ops)
    status, inputs = await http(
        w["port"], "GET",
        f"/api/v1/pm/workspaces/{att['workspace_id']}/work/{att['work_item_id']}/inputs?attempt_id={att['attempt_id']}",
        tok=w["owner_token"],
    )
    assert status == 200 and len(inputs["items"]) == 1, inputs

    async def result(actor=STARTER_OWNER.actor, scopes=("observe", "operate", "manage", "start")):
        status, body = await http(
            w["port"], "GET",
            f"/api/v1/pm/workspaces/{att['workspace_id']}/work-attempts/{att['attempt_id']}/result",
            tok=token(w["d"], actor, *scopes),
        )
        assert status == 200, body
        return body

    async def member(actor, role, who=PLAN_OWNER):
        current = pm_workspace.get_workspace(ops.db, who, w["workspace_id"])
        await pkg_action(w, "pm.workspace.member.set", {"workspace_id": w["workspace_id"]},
                         {"actor": actor, "role": role}, {"expected_version": current["version"]}, who=who)

    cases = {"allowed": await result(), "missing_scope": await result(scopes=("observe",))}
    await member("colleague", "owner")
    cases["different_controller"] = await result(actor="colleague")
    await member(STARTER_OWNER.actor, "reader")
    cases["original_actor_without_write_access"] = await result()
    # The alternate owner restores the original actor through the real operation.
    from bat_agent_connector.api_auth import Principal
    await member(STARTER_OWNER.actor, "owner", Principal("colleague", frozenset({"observe", "manage"})))

    # The existing registry-mismatch scenario is a real, readable result with
    # no session.send row, while the Work's original receipt still exists.
    original_start = registry.get(att["host"], att["session_id"])["start_operation_id"]
    registry.update(att["host"], att["session_id"], start_operation_id="op_" + "f" * 32)
    cases["no_send_row"] = await result()
    registry.update(att["host"], att["session_id"], start_operation_id=original_start)

    # These are explicit unit boundaries of the real producer, not claims that
    # these independent authorization facts are reachable in one stable HTTP read.
    # binding() already checks writer membership, shadowing not_writer_member.
    with monkeypatch.context() as boundary:
        boundary.setattr(pm_attempt_result_reads, "_is_attempt_controller", lambda *_args: True)
        boundary.setattr(pm_attempt_result_reads, "_is_writer_member", lambda *_args, **_kwargs: False)
        cases["not_writer_member_boundary"] = await result()

    original_context = pm_attempt_result_reads._attempt_context

    def codex_context(*args, **kwargs):
        ctx = copy.deepcopy(original_context(*args, **kwargs))
        ctx["intent"]["agent"] = "codex"
        return ctx

    with monkeypatch.context() as boundary:
        boundary.setattr(pm_attempt_result_reads, "_attempt_context", codex_context)
        cases["codex_boundary"] = await result()

    # requires_original_actor exists for lifecycle endpoints, not session.send.
    # Pin that distinction instead of manufacturing a blocked send response.
    def other_requester_context(*args, **kwargs):
        ctx = copy.deepcopy(original_context(*args, **kwargs))
        ctx["parent"]["actor"] = "colleague"
        ctx["view"]["operation_status"] = "waiting_external"
        return ctx

    with monkeypatch.context() as boundary:
        boundary.setattr(pm_attempt_result_reads, "_attempt_context", other_requester_context)
        cases["different_original_actor_boundary"] = await result()
    assert not any(row.get("kind") == "operation_endpoint"
                   for row in cases["different_original_actor_boundary"]["next_allowed_actions"])
    assert any(row.get("action") == "session.send" for row in cases["different_original_actor_boundary"]["next_allowed_actions"])

    expected_reasons = {
        "allowed": None, "missing_scope": "missing_scope", "different_controller": "not_controller",
        "original_actor_without_write_access": "not_controller", "no_send_row": None,
        "not_writer_member_boundary": "not_writer_member", "codex_boundary": "codex_uncorrelated_turn_boundary",
        "different_original_actor_boundary": None,
    }
    for name, body in cases.items():
        reasons = [row["reason"] for row in body["blocked_actions"] if row.get("action") == "session.send"]
        assert reasons == ([expected_reasons[name]] if expected_reasons[name] else []), (name, body)
    assert not any(row.get("action") == "session.send" for row in cases["no_send_row"]["next_allowed_actions"])
    normalized = _normalize({"cases": cases, "inputs": inputs}, att, accepted["operation"]["operation_id"])
    if os.environ.get("BATC_UPDATE_WORK_FOLLOW_UP_GOLDEN") == "1":
        GOLDEN.write_text(json.dumps(normalized, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    assert normalized == json.loads(GOLDEN.read_text(encoding="utf-8"))
