"""Checkpoint reads use real actions, disk Git, mock BAT and the HTTP API."""
from __future__ import annotations

import json

import pytest

from bat_agent_connector import api_auth, pm_access, pm_source_lineage, pm_workspace_adapter
from tests import test_checkpoints as checkpoints
from tests.test_api_v1 import http, token
from tests.test_api_v1 import served as api_served
from tests.test_work_items import PERSON, act, item, project

daemon, human, served = checkpoints.daemon, checkpoints.human, api_served
OWNER = api_auth.Principal(PERSON.actor, PERSON.scopes | {"start", "operate"})


@pytest.fixture
async def linked_checkpoint(served):
    d, port = served
    cp = await checkpoints.make_checkpoint(d)
    continued = await checkpoints.run(d, "checkpoint.continue", {"checkpoint_id": cp["checkpoint_id"]},
                                     {"instructions": "Review this fixed checkpoint"})
    assert continued["status"] == "succeeded", continued
    wid = await item(d, await project(d), "Checkpoint source")
    await act(d, PERSON, "work_item.link", {"work_item_id": wid},
              {"kind": "checkpoint", "ref": cp["checkpoint_id"]})
    # A real PM action installs the first provenance root. Empty lineage tables
    # short-circuit document_roots, which hid this failure in checkpoint tests.
    workspace = (await act(d, OWNER, "pm.workspace.create", params={"name": "Source visibility"}))["result"]["workspace"]
    workspace_id = workspace["workspace_id"]
    original = await act(d, OWNER, pm_workspace_adapter.ACTION, {"workspace_id": workspace_id},
        {"adapter": pm_workspace_adapter.ADAPTERS[0], "rationale": "Keep the selected PM source"},
        pm_workspace_adapter.read(d.ops, OWNER, workspace_id)["preconditions"])
    assert pm_source_lineage.roots(d.ops.db, "operation", original["operation_id"]) == {original["operation_id"]}
    return d, port, cp, continued, wid, original


@pytest.mark.parametrize("surface", ["work", "checkpoint", "continuation", "results"])
async def test_checkpoint_links_and_continuation_are_publicly_readable(linked_checkpoint, surface):
    d, port, cp, continued, wid, _ = linked_checkpoint
    auth = token(d, OWNER.actor, *OWNER.scopes)
    paths = {"work": f"/api/v1/work-items/{wid}", "checkpoint": f"/api/v1/checkpoints/{cp['checkpoint_id']}",
             "continuation": f"/api/v1/operations/{continued['operation_id']}", "results": f"/api/v1/work-items/{wid}/result-sources"}
    # The operation-only gate does not walk ordinary checkpoint.continue params;
    # its public response barrier does. Do not change that gate's semantics.
    assert pm_access.visible(d.ops.db, OWNER, continued)
    path = paths[surface]
    status, body = await http(port, "GET", path, tok=auth)
    assert status == 200, (path, body)
    assert cp["checkpoint_id"] in json.dumps(body)
    assert (await d.call_api("work_item_get", {"work_item_id": wid}, OWNER))["links"][0]["ref"] == cp["checkpoint_id"]


async def test_checkpoint_reads_retain_source_session_visibility(linked_checkpoint):
    d, port, cp, continued, wid, original = linked_checkpoint
    source = cp["host"] + "/" + cp["source_session_id"]
    assert cp["source_session_id"] != continued["result"]["session_id"]
    # Model an existing historical session provenance edge. All referenced
    # records and their access policy were produced by real actions above;
    # neither checkpoint data nor the visibility readers are test doubles.
    with d.journal.tx():
        d.ops.db.execute("INSERT INTO pm_source_lineage VALUES(?,?,?,?)",
            ("session", source, original["operation_id"], original["operation_id"]))
    assert pm_source_lineage.roots(d.ops.db, "checkpoint", cp["checkpoint_id"]) == {original["operation_id"]}
    for actor, expected in ((OWNER.actor, 200), ("unrelated-reader", 404)):
        principal = api_auth.Principal(actor, OWNER.scopes)
        assert pm_source_lineage.visible(d.ops.db, principal, "session", source) == (expected == 200)
        auth = token(d, actor, *principal.scopes)
        for path in (f"/api/v1/work-items/{wid}", f"/api/v1/checkpoints/{cp['checkpoint_id']}",
                     f"/api/v1/operations/{continued['operation_id']}", f"/api/v1/work-items/{wid}/result-sources"):
            status, body = await http(port, "GET", path, tok=auth)
            # Work detail has a public top-level link collection: hide its
            # entire source row. Singular checkpoint/operation/result documents
            # retain the existing non-disclosing refusal instead.
            filtered = expected == 404 and path == f"/api/v1/work-items/{wid}"
            assert status == (200 if filtered else expected), (path, body)
            if expected == 404:
                if filtered:
                    assert body["links"] == [] and body["source_visibility_filtered"]
                else:
                    assert body["error"]["code"] == "NOT_FOUND"
                assert all(value not in json.dumps(body) for value in
                           (cp["checkpoint_id"], cp["source_session_id"], original["operation_id"]))
