"""Current original sources on the shared HTTP/RPC/MCP and artifact read paths."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from bat_agent_connector import api_auth, artifact_managed, artifacts
from bat_agent_connector.mcp_server import build_server
from bat_agent_connector.operations import OperationError
from tests.operation_helpers import settle_operations
from tests.test_api_v1 import http, token
from tests.test_artifact_capture import LocalCaptureHost
from tests.test_artifact_managed import submit as capture_submit
from tests.test_checkpoints import MANUAL
from tests.test_mcp_principal import call
from tests.test_pm_package_handoff import fixed as fixed_fixture
from tests.test_pm_package_handoff import pm as pm_fixture
from tests.test_pm_package_handoff import ready as ready_fixture
from tests.test_pm_package_handoff import request, revoke, run
from tests.test_pm_package_handoff import world as world_fixture
from tests.test_pm_plan_operations import OWNER

world, pm, ready, fixed = world_fixture, pm_fixture, ready_fixture, fixed_fixture


@pytest.fixture
async def dispatched(fixed, mock):
    w = fixed
    w["envelope"] = await request(w)
    w["execution"] = await run(w, w["envelope"])
    assert w["execution"]["status"] == "succeeded", w["execution"]
    w["sid"] = w["execution"]["result"]["session_id"]
    mock.metas[w["sid"]]["isStreaming"] = False
    await w["d"].inventory.refresh_host("h1")
    w["token"] = token(w["d"], OWNER.actor, "observe", "manage", "start", "operate", "approve")
    w["person"] = api_auth.authenticate(w["d"].ops.db, w["token"], w["d"]._admin_token)
    return w


async def test_withdrawal_hides_every_original_projection_but_keeps_public_inventory(dispatched, monkeypatch):
    w, d = dispatched, dispatched["d"]
    sid, oid, wid = w["sid"], w["execution"]["operation_id"], w["target"]["work_item_id"]
    base = f"/api/v1/sessions/h1/{sid}"
    singular = [f"/api/v1/operations/{oid}", base, base + "?live=true", base + "/history", base + "/relations",
        base + "/instructions", base + "/messages", base + "/wait?timeout=0",
        f"/api/v1/work-items/{wid}", f"/api/v1/work-items/{wid}/result-sources"]
    assert (await http(w["port"], "GET", f"/api/v1/operations/{oid}", tok=w["token"]))[0] == 200
    revoke(w)
    for path in singular:
        status, body = await http(w["port"], "GET", path, tok=w["token"])
        assert status == 404 and body["error"]["code"] == "NOT_FOUND", (path, status, body)
    for path in ("/api/v1/sessions", "/api/v1/work-items", "/api/v1/operations", "/api/v1/events?after=0&limit=200", "/api/v1/bootstrap"):
        status, body = await http(w["port"], "GET", path, tok=w["token"])
        assert status == 200, (path, body)
        assert sid not in json.dumps(body) and oid not in json.dumps(body) and wid not in json.dumps(body), path
    legacy = (await http(w["port"], "GET", f"/api/v1/sessions/h1/{MANUAL}", tok=w["token"]))
    assert legacy[0] == 200, legacy
    for method, params in (
        ("op_get", {"operation_id": oid}), ("inventory_session", {"host": "h1", "session_id": sid}),
        ("session_read", {"host": "h1", "session_id": sid}),
        ("resource_history", {"resource_type": "session", "resource_id": "h1/" + sid}),
        ("resource_relations", {"resource_type": "session", "resource_id": "h1/" + sid}),
        ("work_item_get", {"work_item_id": wid})):
        with pytest.raises(OperationError) as caught:
            await d.call_api(method, params, w["person"])
        assert caught.value.code == "NOT_FOUND"
    status, body = await http(w["port"], "POST", "/api/v1/operations", tok=w["token"], body=w["envelope"])
    assert status == 404 and not body["error"].get("admission_refused", False), body
    monkeypatch.setenv("BATC_TASK_URL", f"http://127.0.0.1:{w['port']}/rpc")
    monkeypatch.setenv("BATC_API_TOKEN", w["token"])
    server, fleet = build_server(d.fleet.config, principal_only=True)
    try:
        result = await call(server, "operation_get", {"operation_id": oid})
        assert "NOT_FOUND" in result and "Implement only" not in result
    finally:
        await fleet.close()


async def test_managed_capture_carries_original_sources_to_artifact_and_download(dispatched):
    w, d = dispatched, dispatched["d"]
    d.ops.context["artifact_host"] = LocalCaptureHost()
    path = Path(w["execution"]["result"]["worktree_path"])
    content = b"private derived engineering result\n"
    (path / "result.txt").write_bytes(content)
    source = {"host": "h1", "session_id": w["sid"], "relative_path": "result.txt",
              "execution_operation_id": w["execution"]["operation_id"]}
    preview = await artifact_managed.preview(d.ops, w["person"], source)
    op = capture_submit(d, w["person"], preview)
    await settle_operations(d.ops)
    op = d.ops.get(op["operation_id"])
    assert op["status"] == "succeeded", op
    aid = op["result"]["artifact_id"]
    assert d.artifact_store.read_content(aid, 1) == content
    assert artifacts.get(d.ops.db, aid, 1, principal=w["person"])["source"]["source"]["lineage"]["execution_operation_id"] == w["execution"]["operation_id"]
    revoke(w)
    for suffix in ("", "/content"):
        status, body = await http(w["port"], "GET", f"/api/v1/artifacts/{aid}/revisions/1" + suffix, tok=w["token"])
        assert status == 404 and "private derived" not in json.dumps(body), body
    assert artifacts.list_artifacts(d.ops.db, principal=w["person"])["artifacts"] == []
    assert (await d.call_api("artifacts_list", {}, w["person"]))["artifacts"] == []
    with pytest.raises(OperationError):
        artifacts.get(d.ops.db, aid, 1, principal=w["person"])
    with pytest.raises(OperationError):
        await artifact_managed.preview(d.ops, w["person"], source)
    with pytest.raises(OperationError):
        capture_submit(d, w["person"], preview)
    await d.artifact_store.close_reaper()


async def test_observation_withdrawn_during_wait_discards_original_response(dispatched):
    w, d = dispatched, dispatched["d"]
    entered, release = asyncio.Event(), asyncio.Event()
    # Use the actual SessionObservation authorization loop with a bounded mock
    # BAT read delayed at the source, not a replacement observation facade.
    from bat_agent_connector import service
    original = service.session_read
    async def delayed(*args, **kwargs):
        result = await original(*args, **kwargs)
        entered.set()
        await release.wait()
        return result
    from unittest.mock import patch
    with patch.object(service, "session_read", delayed):
        pending = asyncio.create_task(d.call_api("session_read", {"host": "h1", "session_id": w["sid"]}, w["person"]))
        await asyncio.wait_for(entered.wait(), 5)
        revoke(w)
        release.set()
        with pytest.raises(OperationError) as caught:
            await pending
        assert caught.value.code == "NOT_FOUND"


async def test_unimplemented_derivative_cannot_remove_source_policy(dispatched):
    w, d = dispatched, dispatched["d"]
    # Linking this private execution into another work would copy its summaries
    # outside the fixed lineage. The first slice refuses that derivative.
    create, _ = d.ops.create(w["person"], action="work_item.create", target={"project_id": w["envelope"]["params"]["project_id"]},
                             params={"title": "Ordinary unrelated work"}, idempotency_key="public-work")
    await settle_operations(d.ops)
    other = d.ops.get(create["operation_id"])["result"]["work_item_id"]
    with pytest.raises(OperationError) as caught:
        d.ops.create(w["person"], action="work_item.link", target={"work_item_id": other},
            params={"kind": "operation", "ref": w["execution"]["operation_id"]}, idempotency_key="do-not-republish")
    assert caught.value.code == "PM_DERIVED_ACTION_UNAVAILABLE"
    revoke(w)
    status, body = await http(w["port"], "GET", f"/api/v1/work-items/{other}", tok=w["token"])
    assert status == 200 and "Ordinary unrelated work" in json.dumps(body)
