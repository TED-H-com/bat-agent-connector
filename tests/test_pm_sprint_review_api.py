"""Existing HTTP/RPC/MCP transports carry the exact guided backend contract."""

import copy

import pytest

from bat_agent_connector import controller_authority, mcp_server, pm_sprint_review
from bat_agent_connector.operations import OperationError
from tests import test_pm_sprint_review_planning as p
from tests.test_api_v1 import daemon as daemon_fixture
from tests.test_api_v1 import http, token
from tests.test_api_v1 import served as served_fixture
from tests.test_pm_run_api import unpack

ops, sprint_ops = p.ops, p.sprint_ops
daemon, served = daemon_fixture, served_fixture


async def test_capability_requires_every_action_and_result_lineage_gate(served):
    d, port = served
    auth = token(d, "guide-reader", "observe")
    status, caps = await http(port, "GET", "/api/v1/capabilities", tok=auth)
    assert status == 200 and caps["features"]["pm_workspaces"]["guided_sprint_review"]["version"] == 1
    for name in (
        "pm.meeting.draft",
        "pm.meeting.complete",
        "pm.governance.record",
        "pm.governance.decide",
        "pm.input.record",
        "pm.thread.plan",
        "pm.quality.review.record",
        "pm.follow_up.prepare",
    ):
        action = d.ops.actions.pop(name)
        try:
            assert pm_sprint_review.capabilities(d.ops) == {}
        finally:
            d.ops.actions[name] = action


async def test_guided_reads_and_original_key_replay_have_transport_parity(ops, served, monkeypatch, mock):
    d, port = served
    with controller_authority.use(controller_authority.binding(ops.db, p.OWNER), ops.journal):
        target, recorded, planned, req, plan_req = await p.planning(ops)
    original_ops, original_journal = d.ops, d.journal
    d.ops, d.journal = ops, ops.journal
    fleet = None
    try:
        auth = token(d, p.OWNER.actor, "observe", "manage")
        monkeypatch.setenv("BATC_TASK_URL", f"http://127.0.0.1:{port}/rpc")
        monkeypatch.setenv("BATC_API_TOKEN", auth)
        server, fleet = mcp_server.build_server(d.fleet.config, principal_only=True)
        source = req["params"]["source_ref"]
        wid, mid = target["workspace_id"], source["meeting_id"]
        record = ops.db.execute("SELECT record_id FROM pm_governance_records").fetchone()[0]
        calls = [
            ("pm_meeting_get", {"meeting_id": mid}, "/meetings/" + mid),
            ("pm_meetings_list", {}, "/meetings"),
            (
                "pm_meeting_history_list",
                {"meeting_id": mid, "kind": "drafts"},
                "/meetings/" + mid + "/history/drafts",
            ),
            ("pm_governance_get", {"record_id": record}, "/governance/" + record),
            ("pm_thread_get", {"thread_id": target["thread_id"]}, "/threads/" + target["thread_id"]),
        ]
        tables = [r[0] for r in ops.db.execute("SELECT name FROM sqlite_master WHERE type='table'")]

        def counts():
            return [ops.db.execute("SELECT count(*) FROM " + t).fetchone()[0] for t in tables]

        before = ops.db.total_changes, counts(), list(mock.invokes)
        for method, fields, path in calls:
            status, expected = await http(port, "GET", f"/api/v1/pm/workspaces/{wid}" + path, tok=auth)
            assert status == 200, expected
            params = {"workspace_id": wid, **fields}
            assert await d.call_api(method, params, p.OWNER) == expected
            assert unpack(await server.call_tool(method, params)) == expected
        assert (ops.db.total_changes, counts(), list(mock.invokes)) == before
        for request, original in ((req, recorded), (plan_req, planned)):
            body = copy.deepcopy(request)
            key = body.pop("idempotency_key")
            status, result = await http(
                port, "POST", "/api/v1/operations", tok=auth, body=body, headers={"Idempotency-Key": key}
            )
            assert status == 200 and result["operation"]["operation_id"] == original["operation_id"]
            assert (await d.call_api("op_submit", request, p.OWNER))["operation"]["result"] == original[
                "result"
            ]
            replay = unpack(
                await server.call_tool("operation_submit", {**request, "wait_s": 0, "confirm": True})
            )
            assert replay["operation"]["operation_id"] == original["operation_id"] and not replay["created"]
        from mcp.server.mcpserver.exceptions import ToolError

        from tests.test_pm_sprint_review import basis

        view = p.m.read(ops, {"workspace_id": wid, "meeting_id": mid})
        created = await p.settle(
            ops,
            ops.create(
                p.OWNER,
                **p.m.create_request(
                    ops,
                    {"workspace_id": wid, "sprint_id": view["meeting"]["sprint_id"]},
                    view["snapshot"],
                    "review",
                ),
            )[0],
        )
        fresh_target = {"workspace_id": wid, "meeting_id": created["result"]["meeting_id"]}
        params = p.m.draft_params("review", decisions=[], review_basis=basis())
        params["output"]["feedback"] = []
        oversized = p.m.request(ops, fresh_target, "draft", **params)
        monkeypatch.setattr(pm_sprint_review, "MAX_SOURCE_BYTES", 1)
        body = {k: v for k, v in oversized.items() if k != "idempotency_key"}
        status, result = await http(
            port,
            "POST",
            "/api/v1/operations",
            tok=auth,
            body=body,
            headers={"Idempotency-Key": oversized["idempotency_key"]},
        )
        assert status == 413 and result["error"]["code"] == "PM_SPRINT_REVIEW_SOURCE_LIMIT"
        with pytest.raises(OperationError) as exc:
            await d.call_api("op_submit", oversized, p.OWNER)
        assert exc.value.status == 413 and exc.value.code == "PM_SPRINT_REVIEW_SOURCE_LIMIT"
        with pytest.raises(ToolError, match="PM_SPRINT_REVIEW_SOURCE_LIMIT"):
            await server.call_tool("operation_submit", {**oversized, "wait_s": 0, "confirm": True})
    finally:
        if fleet:
            await fleet.close()
        d.ops, d.journal = original_ops, original_journal


@pytest.mark.parametrize(
    "field,code",
    [
        ("completion_digest", "PM_SPRINT_REVIEW_REFERENCE_MISMATCH"),
        ("decision_id", "PM_SPRINT_REVIEW_DECISION_REQUIRED"),
    ],
)
async def test_new_conflict_codes_keep_http_rpc_mcp_mapping(ops, served, monkeypatch, field, code):
    d, port = served
    with controller_authority.use(controller_authority.binding(ops.db, p.OWNER), ops.journal):
        target, _, _, request, _ = await p.planning(ops)
    original_ops, original_journal = d.ops, d.journal
    d.ops, d.journal = ops, ops.journal
    fleet = None
    try:
        from mcp.server.mcpserver.exceptions import ToolError

        request = copy.deepcopy(request)
        request["idempotency_key"] = "wrong-review-reference-" + field
        request["params"]["source_ref"][field] = "0" * 64 if field == "completion_digest" else "unrecorded"
        request["preconditions"]["expected_version"] = 3
        auth = token(d, p.OWNER.actor, "observe", "manage")
        monkeypatch.setenv("BATC_TASK_URL", f"http://127.0.0.1:{port}/rpc")
        monkeypatch.setenv("BATC_API_TOKEN", auth)
        server, fleet = mcp_server.build_server(d.fleet.config, principal_only=True)
        body = {k: v for k, v in request.items() if k != "idempotency_key"}
        status, result = await http(
            port,
            "POST",
            "/api/v1/operations",
            tok=auth,
            body=body,
            headers={"Idempotency-Key": request["idempotency_key"]},
        )
        assert status == 409 and result["error"]["code"] == code
        with pytest.raises(OperationError) as error:
            await d.call_api("op_submit", request, p.OWNER)
        assert error.value.status == 409 and error.value.code == code
        with pytest.raises(ToolError, match=code):
            await server.call_tool("operation_submit", {**request, "wait_s": 0, "confirm": True})
    finally:
        if fleet:
            await fleet.close()
        d.ops, d.journal = original_ops, original_journal
