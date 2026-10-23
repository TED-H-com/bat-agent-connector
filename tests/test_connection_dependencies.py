"""Dependency reads against real journal, configuration and observation producers."""
from __future__ import annotations

import copy
import json
import sqlite3
import uuid

import pytest

from bat_agent_connector import connection_dependencies as dependencies
from bat_agent_connector import pm_work_profiles, repository_sync, setup_connections
from bat_agent_connector.api_auth import Principal
from bat_agent_connector.connection_endpoints import setup_endpoint
from bat_agent_connector.connection_source_descriptor import build_source_descriptor
from bat_agent_connector.operations import OperationError
from tests.operation_helpers import settle_operations
from tests.test_pm_work_start import (  # noqa: F401 - producer fixtures
    STARTER_OWNER,
    _hold_before_handoff,
    pm,
    ready,
)
from tests.test_pm_work_start import start_ready as start_fixture
from tests.test_pr_publication import DELIVERER, delivery_ready, fixed  # noqa: F401 - producer fixtures
from tests.test_pr_publication import create as publish
from tests.test_pr_publication import pub as publication_fixture
from tests.test_repository_sync import REQUEST, TARGET, envelope
from tests.test_repository_sync import world as repository_world
from tests.test_work_items import act

world = repository_world
start_ready = start_fixture
pub = publication_fixture
OWNER = Principal("dependency-owner", frozenset({"observe", "manage", "start", "approve"}))
OBSERVER = Principal("dependency-observer", frozenset({"observe"}))


async def connect(w, who=OWNER, installation="desktop-a"):
    d = w["d"]
    host = d.fleet.config.host("h1")
    descriptor = build_source_descriptor(kind="bat_profile", profile_reference="fixture-profile",
        association={"url": setup_endpoint(host.url), "fingerprint": host.fingerprint},
        availability_reason="SOURCE_AVAILABLE", source_state={"fixture": "a" * 64})
    body = {"action": "connection.reference.apply", "target": {"installation_ref": installation},
        "params": {"source": descriptor, "source_reference": descriptor["reference"],
            "remote_profile_id": host.profile_id, "service_ref": "h1", "workspace_ref": "ws-1",
            "label": "Fixture", "preferences": {}}, "preconditions": {"expected_revision": 0}}
    review = setup_connections.preview(d.ops, who, body)["preview"]
    body["preconditions"]["review_digest"] = review["review_digest"]
    op, _ = d.ops.create(who, **body, idempotency_key=uuid.uuid4().hex)
    await settle_operations(d.ops)
    assert d.ops.get(op["operation_id"])["status"] == "succeeded"
    return setup_connections.list_connections(d.ops, who, installation)["connections"][-1]


@pytest.fixture
async def connected(world, mock, monkeypatch):
    w, d = world, world["d"]
    setup_connections.install(d.ops)
    await d.inventory.refresh_host("h1")
    w["connection"] = await connect(w)
    def forbidden(*args, **kwargs):
        pytest.fail("Dependency read attempted an effect")
    def read(*, who=OWNER, installation="desktop-a", connection=None, **kwargs):
        before = d.ops.db.total_changes
        scans, bat, github = copy.deepcopy(d.inventory._scans), mock.channels(), list(w["gh"].requests)
        with monkeypatch.context() as patch:
            for obj, names in ((d.inventory, ("refresh_host",)), (d.ops, ("create",)),
                               (d.fleet, ("client",))):
                for name in names:
                    patch.setattr(obj, name, forbidden)
            try:
                return dependencies.read(d.ops, who, installation,
                    connection or w["connection"]["connection_id"], **kwargs)
            finally:
                assert d.ops.db.total_changes == before
                assert d.inventory._scans == scans and mock.channels() == bat and w["gh"].requests == github
    w["read"] = read
    return w


async def workspace(w, who=OWNER):
    return (await act(w["d"], who, "pm.workspace.create", params={"name": "Private workspace",
        "repositories": ["o/r"]}))["result"]["workspace"]["workspace_id"]


async def profile(w, wid, who=OWNER):
    pm_work_profiles.install(w["d"].ops)
    return (await act(w["d"], who, "pm.work_profile.save", {"workspace_id": wid},
        {"document": {"version": 1, "binding": TARGET, "agent": "claude"}}))["result"]


async def pending(w, count, *, status="accepted"):
    pv = await repository_sync.source_preview(w["d"].ops, REQUEST)
    ops, ids = w["d"].ops, []
    for _ in range(count):
        op, _ = ops.create(OWNER, **envelope(pv, uuid.uuid4().hex))
        oid = op["operation_id"]
        if status != "accepted":
            ops._transition(oid, "running")
            ops._transition(oid, status)
        ids.append(oid)
    return ids


def refs(result, kind):
    return {r["ref"] for r in result["impacts"] if r["kind"] == kind}


async def test_visible_shared_and_completed_history_is_informative(connected):
    w = connected
    wid = await workspace(w)
    saved = await profile(w, wid)
    pid = (await act(w["d"], OWNER, "project.create", params={"name": "Shared", "repositories": ["o/r"]}))["result"]["project_id"]
    work = (await act(w["d"], OWNER, "work_item.create", {"project_id": pid}, {"title": "Completed"}))["result"]["work_item_id"]
    await act(w["d"], OWNER, "work_item.link", {"work_item_id": work},
        {"kind": "operation", "ref": w["connection"]["operation_id"]})
    await act(w["d"], OWNER, "work_item.update", {"work_item_id": work}, {"state": "done"}, {"expected_version": 1})
    await act(w["d"], OWNER, "work_item.update", {"work_item_id": work}, {"archived": True}, {"expected_version": 2})
    result = w["read"]()
    assert wid in refs(result, "pm_workspace") and work in refs(result, "work_item")
    assert saved["profile_id"] + "/1" in refs(result, "work_profile")
    assert refs(result, "repository") == {"o/r"}
    assert refs(result, "work_link")
    assert all(row["disposition"] == "informative" for row in result["impacts"])
    assert result["server_complete"] and not result["complete"]
    for domain in ("native_selection", "native_resources"):
        assert result["domains"][domain] == {"covered": False, "complete": False, "reason": "NATIVE_REVIEW_REQUIRED"}


async def test_hidden_references_have_no_ids_names_or_counts(connected):
    w, stranger = connected, Principal("private-owner", OWNER.scopes)
    wid = await workspace(w, stranger)
    saved = await profile(w, wid, stranger)
    first = w["read"]()
    assert first["domains"]["work_history"]["reason"] == "REFERENCES_UNAVAILABLE"
    encoded = json.dumps(first)
    assert wid not in encoded and saved["profile_id"] not in encoded and "Private workspace" not in encoded
    await profile(w, wid, stranger)
    assert w["read"]() == first
    assert not first["server_complete"]


async def test_principal_installation_and_scope_are_non_disclosing(connected):
    w = connected
    other = await connect(w, OBSERVER, "desktop-a")
    own = w["read"](who=OBSERVER, connection=other["connection_id"])
    assert refs(own, "connection") == {other["connection_id"]}
    for kwargs in ({"who": OBSERVER}, {"installation": "desktop-b"},
                   {"who": Principal(OWNER.actor, frozenset({"observe"}))}):
        with pytest.raises(OperationError) as error:
            w["read"](**kwargs)
        assert error.value.code == "NOT_FOUND" and error.value.status == 404


async def test_independent_installations_do_not_change_each_others_digest(connected):
    w = connected
    before = w["read"]()
    other = await connect(w, installation="desktop-b")
    assert w["read"]() == before
    assert other["connection_id"] not in json.dumps(before)


@pytest.mark.parametrize("status", ["waiting_external", "uncertain", "needs_attention"])
async def test_unsettled_statuses_and_sent_effects(connected, status):
    w, ops = connected, connected["d"].ops
    oid = (await pending(w, 1, status=status))[0]
    ops._step_start(oid, "session.start", {}, bookkeeping=True)
    ops._step_status(oid, "session.start", "sent")
    row = next(r for r in w["read"]()["impacts"] if r["ref"] == oid)
    assert row["kind"] == "operation" and row["disposition"] == "blocking"
    if status == "needs_attention":
        ops._transition(oid, "failed")
        assert oid in refs(w["read"](), "operation")


async def test_paired_paging_survives_equal_timestamps_and_empty_filtered_pages(connected, monkeypatch):
    w, ops = connected, connected["d"].ops
    ids = await pending(w, 205)
    stranger = Principal("hidden", OWNER.scopes)
    for _ in range(201):
        ops.create(stranger, action="pm.workspace.create", params={"name": "Hidden"}, idempotency_key=uuid.uuid4().hex)
    # Controlled clock collision on real producer rows, not fabricated list results.
    ops.db.execute("UPDATE operations SET created_at=100 WHERE action='repository.continue'")
    calls, original = [], ops.list
    def listing(**kwargs):
        calls.append(kwargs)
        return original(**kwargs)
    monkeypatch.setattr(ops, "list", listing)
    first = w["read"]()
    second = w["read"](offset=first["next_offset"])
    assert set(ids) <= refs(first, "operation") | refs(second, "operation")
    assert len(calls) >= 6 and all(c.get("before_id") for c in calls if c.get("before") is not None)
    assert first["dependency_digest"] == second["dependency_digest"]


async def test_bound_full_digest_off_page_changes_and_order_independence(connected, monkeypatch):
    w, ops = connected, connected["d"].ops
    ids = await pending(w, 1001)
    before = w["read"]()
    assert len(before["impacts"]) == 200 and not before["server_complete"]
    assert before["domains"]["unsettled_operations"]["reason"] == "DEPENDENCY_LIMIT"
    assert len(w["read"](offset=800)["impacts"]) == 200
    assert w["read"](offset=1000)["impacts"] == []
    outside = next(oid for oid in ids if oid not in refs(before, "operation"))
    ops._transition(outside, "running")
    after = w["read"]()
    assert after["dependency_digest"] != before["dependency_digest"]
    original = ops.list
    def reverse(**kwargs):
        page = original(**kwargs)
        page["operations"].reverse()
        return page
    monkeypatch.setattr(ops, "list", reverse)
    assert w["read"]() == after


@pytest.mark.parametrize("missing", [True, False])
async def test_unknown_or_stale_source_is_incomplete(connected, missing):
    w, db = connected, connected["d"].ops.db
    if missing:
        db.execute("DELETE FROM discovery_latest")
    else:
        db.execute("UPDATE hosts_observed SET last_success_at=0")
    result = w["read"]()
    assert result["domains"]["current_source"]["complete"] is False
    assert result["domains"]["current_source"]["reason"] == "SOURCE_UNAVAILABLE"
    assert not result["server_complete"]


async def test_busy_query_retains_exact_refusal_and_covers_original_reference(connected):
    w, ops = connected, connected["d"].ops
    target = {"installation_ref": "desktop-a", "connection_id": w["connection"]["connection_id"]}
    body = {"action": "connection.reference.apply", "target": target, "params": {"label": "Renamed"},
        "preconditions": {"expected_revision": 1}}
    body["preconditions"]["review_digest"] = setup_connections.preview(ops, OWNER, body)["preview"]["review_digest"]
    op, _ = ops.create(OWNER, **body, idempotency_key="rename")
    oid = op["operation_id"]
    assert oid in refs(w["read"](), "operation")
    with pytest.raises(OperationError) as error:
        setup_connections.preview(ops, OWNER, body)
    assert (error.value.code, error.value.status, error.value.message) == ("CONNECTION_BUSY", 409,
        f"Connection operation {oid} is still open; finish or cancel it first / 連線操作 {oid} 尚未完成；請先完成或取消該操作")
    await settle_operations(ops)
    result = w["read"]()
    assert {oid, w["connection"]["operation_id"]} <= refs(result, "operation_history")


async def test_original_start_attempt_package_and_child_bindings(connected, start_ready):
    w = connected
    connection = await connect(w, STARTER_OWNER)
    parent, child = await _hold_before_handoff(start_ready)
    result = w["read"](who=STARTER_OWNER, connection=connection["connection_id"])
    assert parent in refs(result, "operation") and child in refs(result, "operation_history")
    assert refs(result, "attempt") and refs(result, "work_package") and refs(result, "start_child")
    assert result["server_complete"]


async def test_publication_hidden_sources_are_generic_blockers(connected, pub):
    from tests.test_pm_package_handoff import revoke
    w = connected
    connection = await connect(w, DELIVERER)
    operation = publish(pub)
    result = w["read"](who=DELIVERER, connection=connection["connection_id"])
    assert operation["operation_id"] in refs(result, "operation")
    revoke(w)
    result = w["read"](who=DELIVERER, connection=connection["connection_id"])
    assert operation["operation_id"] not in json.dumps(result)
    assert result["domains"]["unsettled_operations"]["reason"] == "REFERENCES_UNAVAILABLE"


async def test_cursor_coverage_gap_is_incomplete(connected, monkeypatch):
    ops = connected["d"].ops
    await pending(connected, 201)
    original = ops.list
    def gap(**kwargs):
        page = original(**kwargs)
        page["next_before_id"] = None
        return page
    monkeypatch.setattr(ops, "list", gap)
    assert connected["read"]()["domains"]["unsettled_operations"] == {
        "covered": True, "complete": False, "reason": "COVERAGE_GAP"}


async def test_checkpoint_continuation_follows_recorded_source(connected, mock):
    from tests.test_checkpoints import MANUAL, bat_git_status, make_checkpoint
    w, ops = connected, connected["d"].ops
    mock.metas[MANUAL]["cwd"] = str(w["human"])
    mock.handlers["git:status"] = bat_git_status
    checkpoint = await make_checkpoint(w["d"])
    op, _ = ops.create(OWNER, action="checkpoint.continue", target={"checkpoint_id": checkpoint["checkpoint_id"]},
        params={"instructions": "Continue from recorded source"}, idempotency_key="continue")
    assert op["operation_id"] in refs(w["read"](), "operation")


async def test_unavailable_work_link_source_cannot_claim_complete_history(connected, mock, monkeypatch):
    from bat_agent_connector import pm_source_lineage
    from tests.test_checkpoints import MANUAL, bat_git_status, make_checkpoint
    w = connected
    mock.metas[MANUAL]["cwd"] = str(w["human"])
    mock.handlers["git:status"] = bat_git_status
    checkpoint = await make_checkpoint(w["d"])
    pid = (await act(w["d"], OWNER, "project.create", params={"name": "Linked", "repositories": ["o/r"]}))["result"]["project_id"]
    work = (await act(w["d"], OWNER, "work_item.create", {"project_id": pid}, {"title": "History"}))["result"]["work_item_id"]
    await act(w["d"], OWNER, "work_item.link", {"work_item_id": work},
        {"kind": "checkpoint", "ref": checkpoint["checkpoint_id"]})
    original = pm_source_lineage.visible
    def unavailable(db, principal, kind, identity):
        if kind == "checkpoint":
            raise sqlite3.OperationalError("Source reader unavailable")
        return original(db, principal, kind, identity)
    monkeypatch.setattr(pm_source_lineage, "visible", unavailable)
    result = w["read"]()
    assert result["domains"]["work_history"]["reason"] == "REFERENCES_UNAVAILABLE"
    assert not result["server_complete"] and not refs(result, "work_link")
