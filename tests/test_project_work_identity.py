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


async def project(w, tok=None, projection="full"):
    pid = w["d"].ops.db.execute("SELECT project_id FROM work_items WHERE work_item_id=?",
        (w["target"]["work_item_id"],)).fetchone()[0]
    status, doc = await http(w["port"], "GET", "/api/v1/projects/" + pid + "?projection=" + projection, tok=tok or w["owner_token"])
    assert status == 200, doc
    return doc


@pytest.mark.parametrize("projection", ["full", "navigation"])
async def test_http_mcp_project_identity_is_readable_without_controller_transfer(start_ready, mock, monkeypatch, projection):
    w = start_ready
    await member(w, "project-reader", "reader")
    att = await _start_managed_attempt(w, mock)
    expected = {key: att[key] for key in ("workspace_id", "work_item_id", "attempt_id")}
    peer_token = token(w["d"], "project-reader", "observe")
    outsider = token(w["d"], "project-outsider", "observe")
    ops = w["d"].ops
    before = (ops.db.execute("SELECT count(*) FROM operations").fetchone()[0], copy.deepcopy(bat_writes(mock)))
    owner_doc = await project(w, projection=projection)
    owner_row = next(row for row in owner_doc["work"] if row["id"] == att["handoff_operation_id"])
    assert owner_row["work_identity"] == expected
    peer_doc = await project(w, peer_token, projection)
    assert next(row for row in peer_doc["work"] if row["id"] == owner_row["id"])["work_identity"] == expected
    assert all("work_identity" not in row for row in (await project(w, outsider, projection))["work"])
    server, fleet = _open_mcp(w["d"], monkeypatch, w["port"], peer_token)
    try:
        via_mcp = unpack(await server.call_tool("project_get", {"project_id": owner_doc["project"]["project_id"], "projection": projection}))
        assert next(row for row in via_mcp["work"] if row["id"] == owner_row["id"])["work_identity"] == expected
    finally:
        await fleet.close()
    assert (ops.db.execute("SELECT count(*) FROM operations").fetchone()[0], bat_writes(mock)) == before


@pytest.mark.parametrize("projection", ["full", "navigation"])
@pytest.mark.parametrize("source", ["work_item", "parent", "handoff", "package", "session"])
async def test_withdrawn_sources_remove_project_work_association(start_ready, mock, source, projection):
    w = start_ready
    att = await _start_managed_attempt(w, mock)
    assert any(row.get("work_identity") for row in (await project(w, projection=projection))["work"])
    parent = w["d"].ops.get(att["start_operation_id"])
    kind, identity = {
        "work_item": ("work_item", att["work_item_id"]),
        "parent": ("operation", att["start_operation_id"]),
        "handoff": ("operation", att["handoff_operation_id"]),
        "package": ("operation", parent["result"]["children"]["package"]),
        "session": ("session", att["host"] + "/" + att["session_id"]),
    }[source]
    _hide_source(w["d"].ops.db, kind, identity)
    assert all("work_identity" not in row for row in (await project(w, projection=projection))["work"])


async def test_titles_paths_and_one_parent_reference_cannot_join_work(start_ready, mock):
    projection = "full"
    w = start_ready
    att = await _start_managed_attempt(w, mock)
    ops = w["d"].ops
    row = next(row for row in (await project(w, projection=projection))["work"] if row["id"] == att["handoff_operation_id"])
    assert execution_work_identity(ops, STARTER_OWNER, {**row, "session_id": "manual-session"}) is None
    child = ops.get(att["handoff_operation_id"])
    refs = child["external_refs"]
    refs.pop("work_start_parent")
    ops.db.execute("UPDATE operations SET external_refs=? WHERE operation_id=?", (json.dumps(refs), child["operation_id"]))
    assert all("work_identity" not in item for item in (await project(w, projection=projection))["work"])
    assert execution_work_identity(ops, STARTER_OWNER, {**row, "kind": "task"}) is None


async def test_project_registry_read_is_shared_but_next_read_and_write_checks_are_fresh(start_ready, mock, monkeypatch, record_property):
    from bat_agent_connector import execution_sources, registry
    from bat_agent_connector.operations import OperationError
    w = start_ready
    attempt = await _start_managed_attempt(w, mock)
    ops = w['d'].ops
    original_read = registry._read
    calls = []
    def counted(path):
        calls.append(str(path))
        return original_read(path)
    monkeypatch.setattr(registry, '_read', counted)
    describer = execution_sources.describe
    # Compare the existing fresh-per-call resolver with the response-local reuse.
    def uncached(ops, kind, identity, *, _registry=None):
        return describer(ops, kind, identity)
    monkeypatch.setattr(execution_sources, 'describe', uncached)
    before = await project(w, projection='navigation')
    original_reads = len(calls)
    monkeypatch.setattr(execution_sources, 'describe', describer)
    calls.clear()
    document = await project(w, projection='navigation')
    assert before == document
    record_property('previous_registry_reads', original_reads)
    record_property('shared_registry_reads', len(calls))
    selected = next(row for row in document['work'] if row['id'] == attempt['handoff_operation_id'])
    assert selected['eligible'] and original_reads == 3 and len(calls) == 2
    reference = execution_sources.resolve(ops, 'execution', selected['id'])
    reference.update(kind='execution', id=selected['id'])
    registry.update(attempt['host'], attempt['session_id'], branch='refs/heads/changed-since-navigation')
    calls.clear()
    changed = await project(w, projection='navigation')
    assert next(row for row in changed['work'] if row['id'] == selected['id'])['branch'] == 'refs/heads/changed-since-navigation'
    # One lineage snapshot plus the original per-principal control check.
    assert len(calls) == 2
    with pytest.raises(OperationError) as caught:
        execution_sources.revalidate(ops, [reference])
    assert caught.value.code == 'SOURCE_CHANGED'
