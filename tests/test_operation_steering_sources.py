"""Public steering must refuse unreadable saved receipts before any journal write."""
import json

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from bat_agent_connector import mcp_server, pm_access, pm_source_lineage
from bat_agent_connector.operations import OperationError
from tests import test_api_v1 as api
from tests.conftest import adopt, controller_scope
from tests.test_api_v1 import MANUAL, http, token, write_frames
from tests.test_pm_run_api import unpack
from tests.test_pm_work_start import OWNER, _seed_plan_and_work, _setup_full_pm_world

daemon, served = api.daemon, api.served


@pytest.mark.parametrize("action", ["session.send", "work.start"])
@pytest.mark.parametrize("verb", ["cancel", "resume"])
@pytest.mark.parametrize("transport", ["http", "mcp"])
async def test_unavailable_receipt_source_refuses_before_steering(
    served, tmp_path, monkeypatch, mock, action, verb, transport,
):
    d, port = served
    actor = OWNER
    ops = d.ops
    if action == "work.start":
        _, workspace = _setup_full_pm_world(tmp_path, ops=ops)
        context = _seed_plan_and_work(ops, workspace)
        envelope = {"action": action, "target": context["target"], "params": context["params"],
                    "preconditions": context["preconditions"], "idempotency_key": "original-start"}
    else:
        with controller_scope(d.journal, actor):
            adopt(MANUAL)
        envelope = {"action": action, "target": {"host": "h1", "session_id": MANUAL},
                    "params": {"text": "Continue the original work"}, "idempotency_key": "original-send"}
    operation, _ = ops.create(actor, **envelope)
    oid = operation["operation_id"]
    # An existing receipt can name a source that is no longer readable even
    # though the operation's own admission roots remain readable (#237).
    refs = operation["external_refs"] or {}
    refs["retained_receipt"] = {"artifact_id": "art_" + "f" * 32}
    ops.db.execute("UPDATE operations SET external_refs=?,status='needs_attention' WHERE operation_id=?",
                   (json.dumps(refs), oid))
    pm_source_lineage.schema(d.journal)
    auth = token(d, actor.actor, *actor.scopes)
    path = f"/api/v1/operations/{oid}"
    status, readable = await http(port, "GET", path, tok=auth)
    assert status == 200, readable
    # Preserve the exact saved reference when its original source is absent.
    ops.db.execute("INSERT INTO pm_source_lineage VALUES('artifact',?,?,?)",
                   (refs["retained_receipt"]["artifact_id"], "op_" + "e" * 32, oid))
    before = ops.get(oid)
    assert pm_access.visible(ops.db, actor, before)
    with pytest.raises(OperationError, match="Original resource sources are unavailable"):
        pm_source_lineage.require_document(ops.db, actor, before)
    event_count = ops.db.execute("SELECT COUNT(*) FROM api_events").fetchone()[0]
    changes = ops.db.total_changes
    frames = write_frames(mock)
    kicks = []
    monkeypatch.setattr(ops, "kick", lambda: kicks.append(True))
    if transport == "http":
        status, denied = await http(port, "POST", path + "/" + verb, tok=auth, body={})
        assert status == 404 and denied["error"]["code"] == "NOT_FOUND", denied
    else:
        monkeypatch.setenv("BATC_TASK_URL", f"http://127.0.0.1:{port}/rpc")
        monkeypatch.setenv("BATC_API_TOKEN", auth)
        server, fleet = mcp_server.build_server(d.fleet.config, read_only=False, principal_only=True)
        try:
            with pytest.raises(ToolError, match="NOT_FOUND"):
                unpack(await server.call_tool("operation_" + verb, {"operation_id": oid, "confirm": True}))
        finally:
            await fleet.close()
    assert ops.get(oid) == before
    assert ops.db.total_changes == changes
    assert ops.db.execute("SELECT COUNT(*) FROM api_events").fetchone()[0] == event_count
    assert not kicks and write_frames(mock) == frames
