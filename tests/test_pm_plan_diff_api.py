"""Fixed plan comparisons share one private, read-only HTTP/RPC/MCP contract."""
from __future__ import annotations

from urllib.parse import urlencode

import pytest

from bat_agent_connector import mcp_server, pm_workspace, work_items
from bat_agent_connector.operations import OperationError
from tests.test_api_v1 import daemon as api_daemon
from tests.test_api_v1 import http, token, write_frames
from tests.test_api_v1 import served as api_served
from tests.test_pm_api import OTHER, OWNER, seed
from tests.test_pm_plan_api import draft_body, select_planning
from tests.test_pm_run_api import grant, unpack
from tests.test_work_items import act, item, project

daemon = api_daemon
served = api_served


async def revisions(d):
    _, target = await seed(d)
    source, assumption = await select_planning(d, target)
    ids = []
    for acceptance, markdown in [([assumption], "Consider membership access."), ([], "Membership access with unresolved pricing.")]:
        body = draft_body(d, target, markdown=markdown)
        body["params"]["requirements"] = [{"id": "membership", "source_input_id": source,
                                           "acceptance_input_ids": acceptance}]
        op = await act(d, OWNER, body["action"], body["target"], body["params"], body["preconditions"])
        ids.append(op["result"]["revision_id"])
    return target, {"workspace_id": target["workspace_id"], "from_revision_id": ids[0], "to_revision_id": ids[1]}


def endpoint(params, **extra):
    query = {key: value for key, value in {**params, **extra}.items() if key != "workspace_id"}
    if isinstance(query.get("binding_ids"), list):
        query["binding_ids"] = ",".join(query["binding_ids"])
    return f"/api/v1/pm/workspaces/{params['workspace_id']}/plan-comparison?{urlencode(query)}"


async def binding(d, wid):
    pid = await project(d)
    work = await item(d, pid, "A separately tracked deliverable")
    await act(d, OWNER, "pm.project.bind", {"workspace_id": wid, "project_id": pid},
              {"rationale": "Explicit association"},
              {"expected_workspace_version": pm_workspace.get_workspace(d.ops.db, OWNER, wid)["version"],
               "expected_project_version": work_items._get_project(d.ops.db, pid)["version"]})
    result = await act(d, OWNER, "pm.work.bind", {"workspace_id": wid, "work_item_id": work},
                       {"rationale": "Review this saved association without assigning a plan"},
                       {"expected_binding_id": None, "expected_work_version": work_items._get_item(d.ops.db, work)["version"]})
    return result["result"]["binding_id"]


async def test_fixed_comparison_http_rpc_mcp_parity_preserves_sources_and_has_no_writes(served, monkeypatch, mock):
    d, port = served
    _, params = await revisions(d)
    bid = await binding(d, params["workspace_id"])
    params.update(binding_ids=[bid], journal_cursor=d.journal.api_head())
    # A workspace owner only needs observe scope for a comparison.
    auth = token(d, OWNER.actor, "observe")
    monkeypatch.setenv("BATC_TASK_URL", f"http://127.0.0.1:{port}/rpc")
    monkeypatch.setenv("BATC_API_TOKEN", auth)
    server, fleet = mcp_server.build_server(d.fleet.config, read_only=True, principal_only=True)
    original = d.ops.db.total_changes
    try:
        status, expected = await http(port, "GET", endpoint(params), tok=auth)
        assert status == 200, expected
        assert await d.call_api("pm_plan_compare", params.copy(), OWNER) == expected
        assert unpack(await server.call_tool("pm_plan_compare", params)) == expected
        comparison = expected["comparison"]
        assert comparison["context"]["binding_ids"] == [bid]
        assert comparison["context"]["journal_cursor"] == params["journal_cursor"]
        changed = comparison["changes"]["requirements"]["changed"][0]
        assert changed["before"]["acceptance"][0]["source_status"] == "agent_assumption"
        assert changed["after"]["acceptance"] == []
        finance = comparison["changes"]["domains"]["finance"]["after"]["statements"][0]
        assert finance["status"] == "agent_assumption" and finance["confirmed_by"] is None
        assert comparison["scope"]["prose_scope_impact"] == "unknown"
        candidate = comparison["impact_candidates"][0]
        assert candidate["binding"]["title"] is None and candidate["binding"]["work_state"] == "unknown"
        assert not candidate["work_update_authorized"] and not comparison["execution_authorized"]
        assert d.ops.db.total_changes == original
        assert d.ops.db.execute("SELECT count(*) FROM tasks").fetchone()[0] == 0
        assert not write_frames(mock)
    finally:
        await fleet.close()


async def test_comparison_explicit_cursor_stays_fixed_and_omitted_bindings_do_not_scan_work(served):
    d, port = served
    target, params = await revisions(d)
    await binding(d, params["workspace_id"])
    auth = token(d, OWNER.actor, "observe")
    status, original = await http(port, "GET", endpoint(params), tok=auth)
    assert status == 200
    cursor = original["comparison"]["context"]["journal_cursor"]
    assert original["comparison"]["impact_candidates"] == []
    assert original["comparison"]["coverage"]["unselected_work"] == "not_scanned"
    version = d.ops.db.execute("SELECT version FROM pm_threads WHERE thread_id=?", (target["thread_id"],)).fetchone()[0]
    await act(d, OWNER, "pm.message.append", target, {"content": "Later discussion is not a revision"}, {"expected_version": version})
    assert d.journal.api_head() > cursor
    assert (await http(port, "GET", endpoint(params, journal_cursor=cursor), tok=auth)) == (200, original)


async def test_comparison_rechecks_membership_and_rejects_foreign_revisions_or_bindings(served):
    d, port = served
    _, params = await revisions(d)
    _, foreign = await revisions(d)
    bid = await binding(d, foreign["workspace_id"])
    auth = token(d, OTHER.actor, "observe")
    assert (await http(port, "GET", endpoint(params), tok=auth))[0] == 404
    await grant(d, params["workspace_id"], OTHER.actor, "reader")
    assert (await http(port, "GET", endpoint(params), tok=auth))[0] == 200
    await grant(d, params["workspace_id"], OTHER.actor, None)
    assert (await http(port, "GET", endpoint(params), tok=auth))[0] == 404
    owner = token(d, OWNER.actor, "observe")
    for extra in [{"to_revision_id": foreign["to_revision_id"]}, {"binding_ids": [bid]}]:
        assert (await http(port, "GET", endpoint(params, **extra), tok=owner))[0] == 404
    no_observe = token(d, OWNER.actor, "manage")
    assert (await http(port, "GET", endpoint(params), tok=no_observe))[0] == 403


@pytest.mark.parametrize("extra", [
    "&from_revision_id=other", "&to_revision_id=other", "&actor=owner",
    "&binding_ids=pmwb_" + "a" * 32 + "&binding_ids=pmwb_" + "b" * 32,
    "&journal_cursor=1&journal_cursor=2", "&journal_cursor=-1", "&journal_cursor=true",
    "&journal_cursor=1.5", "&binding_ids=not-a-binding",
    "&binding_ids=" + ",".join(["pmwb_" + "a" * 32] * 2),
    "&binding_ids=" + ",".join("pmwb_" + f"{i:032x}" for i in range(101)),
], ids=["duplicate-from", "duplicate-to", "actor-injection", "duplicate-bindings-key", "duplicate-cursor",
        "negative-cursor", "boolean-cursor", "decimal-cursor", "invalid-binding", "duplicate-binding", "over-limit"])
async def test_comparison_http_rejects_ambiguous_queries_and_unbounded_inputs(served, extra):
    d, port = served
    _, params = await revisions(d)
    status, _ = await http(port, "GET", endpoint(params) + extra, tok=token(d, OWNER.actor, "observe"))
    assert status == 422


@pytest.mark.parametrize("missing", ["from_revision_id", "to_revision_id"])
async def test_comparison_requires_both_exact_revision_ids(served, missing):
    d, port = served
    _, params = await revisions(d)
    del params[missing]
    assert (await http(port, "GET", endpoint(params), tok=token(d, OWNER.actor, "observe")))[0] == 422


@pytest.mark.parametrize("extra", [
    {"binding_ids": "pmwb_" + "a" * 32}, {"binding_ids": None},
    {"journal_cursor": True}, {"journal_cursor": "1"}, {"journal_cursor": -1}, {"actor": "owner"},
])
async def test_comparison_rpc_preserves_types_and_rejects_extra_identity(served, extra):
    d, _ = served
    _, params = await revisions(d)
    with pytest.raises(OperationError, match="INVALID_PARAMS"):
        await d.call_api("pm_plan_compare", {**params, **extra}, OWNER)


async def test_comparison_cursor_proves_selected_sources_existed_then(served):
    d, port = served
    _, params = await revisions(d)
    auth = token(d, OWNER.actor, "observe")
    future = d.journal.api_head() + 1
    assert (await http(port, "GET", endpoint(params, journal_cursor=future), tok=auth))[0] == 422
    status, body = await http(port, "GET", endpoint(params, journal_cursor=0), tok=auth)
    assert status == 409 and "PM_DIFF_SOURCE_AFTER_CURSOR" in str(body)
