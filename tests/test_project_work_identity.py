"""Project navigation may join a Work only through visible start/attempt lineage."""
from __future__ import annotations

import copy
import json

import pytest

from bat_agent_connector.pm_operation_loop import execution_work_identity
from tests.test_api_v1 import http, token
from tests.test_checkpoints import bat_writes
from tests.test_pm_loop_parity import _hide_source, _open_mcp, unpack
from tests.test_pm_work_attempt_result import _start_managed_attempt, pm, ready, start_ready, world
from tests.test_pm_work_packages import member
from tests.test_pm_work_start import STARTER_OWNER

_ = (world, pm, ready, start_ready)


async def project(w, tok=None):
    pid = w["d"].ops.db.execute("SELECT project_id FROM work_items WHERE work_item_id=?",
        (w["target"]["work_item_id"],)).fetchone()[0]
    status, doc = await http(w["port"], "GET", "/api/v1/projects/" + pid, tok=tok or w["owner_token"])
    assert status == 200, doc
    return doc


async def test_http_mcp_project_identity_is_readable_without_controller_transfer(start_ready, mock, monkeypatch):
    w = start_ready
    await member(w, "project-reader", "reader")
    att = await _start_managed_attempt(w, mock)
    expected = {key: att[key] for key in ("workspace_id", "work_item_id", "attempt_id")}
    peer_token = token(w["d"], "project-reader", "observe")
    outsider = token(w["d"], "project-outsider", "observe")
    ops = w["d"].ops
    before = (ops.db.execute("SELECT count(*) FROM operations").fetchone()[0], copy.deepcopy(bat_writes(mock)))
    owner_doc = await project(w)
    owner_row = next(row for row in owner_doc["work"] if row["id"] == att["handoff_operation_id"])
    assert owner_row["work_identity"] == expected
    peer_doc = await project(w, peer_token)
    assert next(row for row in peer_doc["work"] if row["id"] == owner_row["id"])["work_identity"] == expected
    assert all("work_identity" not in row for row in (await project(w, outsider))["work"])
    server, fleet = _open_mcp(w["d"], monkeypatch, w["port"], peer_token)
    try:
        via_mcp = unpack(await server.call_tool("project_get", {"project_id": owner_doc["project"]["project_id"]}))
        assert next(row for row in via_mcp["work"] if row["id"] == owner_row["id"])["work_identity"] == expected
    finally:
        await fleet.close()
    assert (ops.db.execute("SELECT count(*) FROM operations").fetchone()[0], bat_writes(mock)) == before


@pytest.mark.parametrize("source", ["work_item", "parent", "handoff", "package", "session"])
async def test_withdrawn_sources_remove_project_work_association(start_ready, mock, source):
    w = start_ready
    att = await _start_managed_attempt(w, mock)
    assert any(row.get("work_identity") for row in (await project(w))["work"])
    parent = w["d"].ops.get(att["start_operation_id"])
    kind, identity = {
        "work_item": ("work_item", att["work_item_id"]),
        "parent": ("operation", att["start_operation_id"]),
        "handoff": ("operation", att["handoff_operation_id"]),
        "package": ("operation", parent["result"]["children"]["package"]),
        "session": ("session", att["host"] + "/" + att["session_id"]),
    }[source]
    _hide_source(w["d"].ops.db, kind, identity)
    assert all("work_identity" not in row for row in (await project(w))["work"])


async def test_titles_paths_and_one_parent_reference_cannot_join_work(start_ready, mock):
    w = start_ready
    att = await _start_managed_attempt(w, mock)
    ops = w["d"].ops
    row = next(row for row in (await project(w))["work"] if row["id"] == att["handoff_operation_id"])
    assert execution_work_identity(ops, STARTER_OWNER, {**row, "session_id": "manual-session"}) is None
    child = ops.get(att["handoff_operation_id"])
    refs = child["external_refs"]
    refs.pop("work_start_parent")
    ops.db.execute("UPDATE operations SET external_refs=? WHERE operation_id=?", (json.dumps(refs), child["operation_id"]))
    assert all("work_identity" not in item for item in (await project(w))["work"])
    assert execution_work_identity(ops, STARTER_OWNER, {**row, "kind": "task"}) is None
