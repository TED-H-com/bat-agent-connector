"""Selected issue publication through real central routes and a loopback provider."""
import json
import uuid

import pytest

from bat_agent_connector import controller_authority, mcp_server
from bat_agent_connector.operations import OperationError
from tests.operation_helpers import settle_operations
from tests.test_api_v1 import http, token
from tests.test_mcp_principal import call
from tests.test_pm_governance_api import send
from tests.test_pm_issue_mappings import OWNER, cancellation, request
from tests.test_pm_issue_mappings import mapped as mapped_fixture
from tests.test_pm_issue_mappings import pm as pm_fixture
from tests.test_pm_issue_mappings import ready as ready_fixture
from tests.test_pm_issue_mappings import world as world_fixture
from tests.test_pm_run_api import unpack
from tests.test_pm_work_packages import CONTRIBUTOR, READER, add_task_evidence, create, member, read

world, pm, ready, mapped = world_fixture, pm_fixture, ready_fixture, mapped_fixture


@pytest.fixture
async def served(mapped):
    w = mapped
    await member(w, READER.actor, "reader")
    await member(w, CONTRIBUTOR.actor, "contributor")
    return w, w["port"]


async def test_selected_publication_and_read_parity_are_canonical_and_reads_have_no_effects(served, monkeypatch):
    w, port = served
    d, wid = w["d"], w["workspace_id"]
    envelope = request(w)
    op = await send(d, port, envelope, CONTRIBUTOR)
    mid = op["result"]["mapping_id"]
    auth = token(d, READER.actor, "observe")
    monkeypatch.setenv("BATC_TASK_URL", f"http://127.0.0.1:{port}/rpc")
    monkeypatch.setenv("BATC_API_TOKEN", auth)
    server, fleet = mcp_server.build_server(d.fleet.config, read_only=True, principal_only=True)
    before, calls = d.ops.db.total_changes, len(w["gh"].requests)
    try:
        tools = {tool.name: tool for tool in await server.list_tools()}
        for method, fields, suffix in [
            ("pm_issue_mappings_list", {"after": 0, "limit": 1}, "?after=0&limit=1"),
            ("pm_issue_mapping_get", {"mapping_id": mid}, "/" + mid),
        ]:
            assert tools[method].annotations.read_only_hint
            status, expected = await http(port, "GET", f"/api/v1/pm/workspaces/{wid}/issue-mappings" + suffix, tok=auth)
            assert status == 200, expected
            params = {"workspace_id": wid, **fields}
            assert await d.call_api(method, params.copy(), READER) == expected
            assert unpack(await server.call_tool(method, params)) == expected
        assert d.ops.db.total_changes == before and len(w["gh"].requests) == calls
        status, caps = await http(port, "GET", "/api/v1/capabilities", tok=auth)
        assert status == 200 and caps["features"]["pm_workspaces"]["issue_mapping"]
    finally:
        await fleet.close()
    replay = await send(d, port, envelope, CONTRIBUTOR)
    assert replay["operation_id"] == op["operation_id"] and replay["result"] == op["result"]
    assert len(w["issue_provider"].issues) == 1
    stopped = await send(d, port, cancellation(w, op), CONTRIBUTOR)
    assert stopped["result"]["state"] == "cancelled" and stopped["result"]["publication"] is None
    assert w["issue_provider"].issues[1]["state"] == "open" and not w["issue_provider"].comments


async def test_exact_queries_mapping_cas_and_current_membership(served):
    w, port = served
    d, wid = w["d"], w["workspace_id"]
    op = await send(d, port, request(w), CONTRIBUTOR)
    mid = op["result"]["mapping_id"]
    auth = token(d, READER.actor, "observe")
    prefix = f"/api/v1/pm/workspaces/{wid}/issue-mappings"
    for suffix in ["?after=", "?after=-1", "?after=0&after=1", "?limit=", "?limit=0", "?limit=&limit=1",
                   "?actor=", "?execute=true", "?work_item_id=", "/" + mid + "?after=0", "/" + mid + "?actor="]:
        assert (await http(port, "GET", prefix + suffix, tok=auth))[0] == 422, suffix
    for params in [{"after": True}, {"after": None}, {"after": "0"}, {"after": 2**63}, {"limit": False},
                   {"limit": None}, {"actor": OWNER.actor}, {"work_item_id": w["target"]["work_item_id"]}]:
        with pytest.raises(OperationError, match="INVALID_PARAMS"):
            await d.call_api("pm_issue_mappings_list", {"workspace_id": wid, **params}, READER)
    with pytest.raises(OperationError, match="INVALID_PARAMS"):
        await d.call_api("pm_issue_mapping_get", {"workspace_id": wid, "mapping_id": mid, "after": 0}, READER)
    conflict = request(w)
    status, answer = await http(port, "POST", "/api/v1/operations", tok=token(d, CONTRIBUTOR.actor, *CONTRIBUTOR.scopes),
        body={k: v for k, v in conflict.items() if k != "idempotency_key"}, headers={"Idempotency-Key": conflict["idempotency_key"]})
    assert status == 409 and answer["error"]["code"] == "PM_ISSUE_VERSION_CONFLICT"
    denied = request(w, previous=op["result"]["revision_id"])
    status, _ = await http(port, "POST", "/api/v1/operations", tok=token(d, READER.actor, *READER.scopes),
        body={k: v for k, v in denied.items() if k != "idempotency_key"}, headers={"Idempotency-Key": denied["idempotency_key"]})
    assert status == 403
    await member(w, READER.actor, None)
    for suffix in ["", "/" + mid]:
        assert (await http(port, "GET", prefix + suffix, tok=auth))[0] == 404
    assert (await http(port, "GET", "/api/v1/operations/" + op["operation_id"], tok=auth))[0] == 404
    assert (await http(port, "GET", prefix, tok=token(d, OWNER.actor, "manage")))[0] == 403


async def sourced_package(w):
    with controller_authority.use(controller_authority.binding(w["d"].ops.db, OWNER), w["d"].journal):
        source, evidence = await add_task_evidence(w)
    w["package"] = read(w, await create(w, previous=w["package"]["package_id"], evidence_refs=[evidence]))
    return source


async def test_original_source_withdrawal_hides_pending_and_completed_facts_on_every_entry(served, monkeypatch):
    w, port = served
    d, wid = w["d"], w["workspace_id"]
    source = await sourced_package(w)
    original = request(w, public_body="Selected public text; private source context stays local")
    one = await send(d, port, original, CONTRIBUTOR)
    pending_request = request(w, previous=one["result"]["revision_id"], public_body="PRIVATE PENDING DRAFT NOT YET PUBLISHED")
    pending, _ = d.ops.create(CONTRIBUTOR, **pending_request)
    auth = token(d, READER.actor, "observe")
    monkeypatch.setenv("BATC_TASK_URL", f"http://127.0.0.1:{port}/rpc")
    monkeypatch.setenv("BATC_API_TOKEN", auth)
    server, fleet = mcp_server.build_server(d.fleet.config, read_only=True, principal_only=True)
    d.ops.db.execute("UPDATE tasks SET controller_authority='{}' WHERE task_id=?", (source["task_id"],))
    try:
        for op in (one, pending):
            oid = op["operation_id"]
            assert (await http(port, "GET", "/api/v1/operations/" + oid, tok=auth))[0] == 404
            with pytest.raises(OperationError):
                await d.call_api("op_get", {"operation_id": oid}, READER)
            assert "NOT_FOUND" in await call(server, "operation_get", {"operation_id": oid})
        mid = one["result"]["mapping_id"]
        prefix = f"/api/v1/pm/workspaces/{wid}/issue-mappings"
        assert (await http(port, "GET", prefix + "/" + mid, tok=auth))[0] == 404
        with pytest.raises(OperationError) as error:
            await d.call_api("pm_issue_mapping_get", {"workspace_id": wid, "mapping_id": mid}, READER)
        assert error.value.code in await call(server, "pm_issue_mapping_get", {"workspace_id": wid, "mapping_id": mid})
        for method, rpc, params, path in [
            ("pm_issue_mappings_list", "pm_issue_mappings_list", {"workspace_id": wid}, prefix),
            ("operations_list", "op_list", {"action": "pm.issue.publish"}, "/api/v1/operations?action=pm.issue.publish"),
            ("events_list", "api_events", {"after": 0, "limit": 500}, "/api/v1/events?after=0&limit=500"),
        ]:
            status, payload = await http(port, "GET", path, tok=auth)
            assert status == 200
            for encoded in (json.dumps(payload), json.dumps(await d.call_api(rpc, params.copy(), READER)), await call(server, method, params)):
                assert one["operation_id"] not in encoded and pending["operation_id"] not in encoded
                assert mid not in encoded and "PRIVATE PENDING DRAFT" not in encoded
        status, hidden = await http(port, "POST", "/api/v1/operations", tok=token(d, CONTRIBUTOR.actor, *CONTRIBUTOR.scopes),
            body={k: v for k, v in original.items() if k != "idempotency_key"}, headers={"Idempotency-Key": original["idempotency_key"]})
        assert status == 404 and not hidden["error"].get("admission_refused", False)
        await settle_operations(d.ops)
        assert len(w["issue_provider"].comments) == 0 and len(w["issue_provider"].issues) == 1
    finally:
        await fleet.close()


async def test_wait_return_rechecks_original_sources_http_rpc_mcp_without_losing_original_key(served, monkeypatch):
    w, port = served
    d = w["d"]
    source = await sourced_package(w)
    envelope = request(w)
    one = await send(d, port, envelope, CONTRIBUTOR)
    authority = d.ops.db.execute("SELECT controller_authority FROM tasks WHERE task_id=?", (source["task_id"],)).fetchone()[0]
    native_wait = d.ops.wait

    async def withdrawn(operation_id, *args, **kwargs):
        result = await native_wait(operation_id, *args, **kwargs)
        d.ops.db.execute("UPDATE tasks SET controller_authority='{}' WHERE task_id=?", (source["task_id"],))
        return result

    monkeypatch.setattr(d.ops, "wait", withdrawn)
    auth = token(d, CONTRIBUTOR.actor, *CONTRIBUTOR.scopes)
    monkeypatch.setenv("BATC_TASK_URL", f"http://127.0.0.1:{port}/rpc")
    monkeypatch.setenv("BATC_API_TOKEN", auth)
    server, fleet = mcp_server.build_server(d.fleet.config, read_only=False, principal_only=True)
    try:
        status, hidden = await http(port, "POST", "/api/v1/operations?wait=1", tok=auth,
            body={k: v for k, v in envelope.items() if k != "idempotency_key"}, headers={"Idempotency-Key": envelope["idempotency_key"]})
        assert status == 404 and not hidden["error"].get("admission_refused", False)
        d.ops.db.execute("UPDATE tasks SET controller_authority=? WHERE task_id=?", (authority, source["task_id"]))
        with pytest.raises(OperationError) as error:
            await d.call_api("op_submit", {**envelope, "wait_s": 1}, CONTRIBUTOR)
        assert error.value.status == 404
        d.ops.db.execute("UPDATE tasks SET controller_authority=? WHERE task_id=?", (authority, source["task_id"]))
        refused = await call(server, "operation_submit", {**envelope, "wait_s": 1, "confirm": True})
        assert "NOT_FOUND" in refused and one["operation_id"] not in refused
        assert not d.ops.get(one["operation_id"])["cancel_requested"]
        assert len(w["issue_provider"].issues) == 1 and not w["issue_provider"].comments
        assert d.ops.has_idempotency_record(CONTRIBUTOR, action=envelope["action"], target=envelope["target"],
                                             idempotency_key=envelope["idempotency_key"])
        # A genuinely new refused request is distinct from the admitted original.
        status, rejected = await http(port, "POST", "/api/v1/operations", tok=auth,
            body={k: v for k, v in envelope.items() if k != "idempotency_key"}, headers={"Idempotency-Key": uuid.uuid4().hex})
        assert status == 404 and rejected["error"]["admission_refused"] is True
    finally:
        await fleet.close()
