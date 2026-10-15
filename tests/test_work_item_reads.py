from __future__ import annotations

import pytest

from bat_agent_connector import api_auth, dashboard_sync, host_git, work_item_reads, work_items
from bat_agent_connector.operations import OpContext, OperationError, OperationService
from bat_agent_connector.task_journal import Journal
from tests import test_work_items as fixtures
from tests.operation_helpers import settle_operations
from tests.test_api_v1 import http, token
from tests.test_work_items import PERSON, act, get, item, project

daemon = fixtures.daemon
served = fixtures.served

READER = api_auth.Principal("reader", frozenset({"observe"}))


def read(d, wid, principal=READER):
    return work_items.work_item_get(d.journal.db, wid,
        principal_id=dashboard_sync.identity(d.journal, principal)["principal_id"])["work_item"]


def mark(d, wid, version, *, key="read", principal=READER):
    return d.ops.create(principal, action="work_item.read", target={"work_item_id": wid}, params={},
                       preconditions={"expected_version": version}, idempotency_key=key)[0]


async def test_read_is_personal_and_never_approves_or_changes_the_item(daemon):
    wid = await item(daemon, await project(daemon), "Awaiting review")
    await act(daemon, PERSON, "work_item.update", {"work_item_id": wid}, {"state": "done"}, {"expected_version": 1})
    before = get(daemon, wid)
    assert before["completion"]["pending"]
    assert read(daemon, wid)["reading"]["unread"]
    first = mark(daemon, wid, before["version"])
    assert isinstance(daemon.ops.context["git_runner"], host_git.HostGitRunner)
    assert "git_execution" not in first["external_refs"]["admission_binding"]
    await settle_operations(daemon.ops)
    assert daemon.ops.get(first["operation_id"])["status"] == "succeeded"
    replayed, created = daemon.ops.create(READER, action="work_item.read", target={"work_item_id": wid}, params={},
                                          preconditions={"expected_version": before["version"]}, idempotency_key="read")
    assert not created and replayed["operation_id"] == first["operation_id"] and replayed["status"] == "succeeded"
    assert not read(daemon, wid)["reading"]["unread"]
    assert get(daemon, wid) == before  # includes approval fingerprint, version, updated_at and pending
    assert read(daemon, wid, PERSON)["reading"]["unread"]
    changed_scope = api_auth.Principal(READER.actor, frozenset({"observe", "manage"}))
    assert read(daemon, wid, changed_scope)["reading"]["unread"]
    with pytest.raises(OperationError, match="original effective principal"):
        mark(daemon, wid, before["version"], principal=changed_scope)
    for verb in (daemon.ops.cancel, daemon.ops.resume):
        with pytest.raises(OperationError, match="original effective principal"):
            verb(changed_scope, first["operation_id"])
    # Rotation of the secret is not a different reading identity.
    assert not read(daemon, wid, api_auth.Principal(READER.actor, READER.scopes))["reading"]["unread"]
    assert not daemon.fleet._clients


async def test_out_of_order_read_operations_do_not_erase_newer_unread_updates(daemon):
    wid = await item(daemon, await project(daemon), "First")
    old = mark(daemon, wid, 1)
    await act(daemon, PERSON, "work_item.update", {"work_item_id": wid}, {"title": "Second"}, {"expected_version": 1})
    assert read(daemon, wid)["reading"] == {"read_version": 1, "current_version": 2, "unread": True,
                                           "read_at": read(daemon, wid)["reading"]["read_at"]}
    newer = mark(daemon, wid, 2, key="newer")
    await settle_operations(daemon.ops)
    marker = read(daemon, wid)["reading"]
    older = mark(daemon, wid, 1, key="older-new-intent")
    await settle_operations(daemon.ops)
    assert read(daemon, wid)["reading"] == marker
    assert daemon.ops.get(older["operation_id"])["result"]["read_version"] == 2
    assert mark(daemon, wid, 1)["operation_id"] == old["operation_id"]
    assert daemon.ops.get(newer["operation_id"])["status"] == "succeeded"
    assert daemon.journal.db.execute("SELECT COUNT(*) FROM api_events WHERE kind='work_item.read'").fetchone()[0] == 2
    with pytest.raises(OperationError, match="ahead"):
        mark(daemon, wid, 3, key="future")


async def test_late_execution_marks_only_the_version_displayed_at_admission(daemon):
    wid = await item(daemon, await project(daemon), "First")
    old = mark(daemon, wid, 1)
    # Execute a competing update before running the accepted read operation.
    op, _ = daemon.ops.create(PERSON, action="work_item.update", target={"work_item_id": wid},
        params={"title": "Second"}, preconditions={"expected_version": 1}, idempotency_key="edit")
    await daemon.ops._execute(op["operation_id"])
    await daemon.ops._execute(old["operation_id"])
    assert read(daemon, wid)["reading"]["unread"]
    assert read(daemon, wid)["reading"]["read_version"] == 1


async def test_effect_receipt_survives_restart_without_consuming_schema_step(daemon):
    wid = await item(daemon, await project(daemon), "Restart")
    op = mark(daemon, wid, 1)
    version = daemon.journal.db.execute("PRAGMA user_version").fetchone()[0]
    # Simulate exit after the SQLite effect committed but before the operation was completed.
    result = await work_item_reads._run(OpContext(daemon.ops, daemon.ops.get(op["operation_id"])))
    path = daemon.journal.path
    reopened = Journal(path)
    try:
        ops = OperationService(reopened, actions=work_item_reads.ACTIONS)
        await settle_operations(ops)
        assert ops.get(op["operation_id"])["result"] == result
        assert reopened.db.execute("PRAGMA user_version").fetchone()[0] == version
        assert reopened.db.execute("SELECT COUNT(*) FROM api_events WHERE kind='work_item.read'").fetchone()[0] == 1
    finally:
        reopened.close()


@pytest.mark.parametrize("version", [True, 0, -1, "1", None])
async def test_invalid_read_preconditions_are_rejected_before_persistence(daemon, version):
    wid = await item(daemon, await project(daemon), "Input")
    before = daemon.journal.db.execute("SELECT COUNT(*) FROM operations").fetchone()[0]
    with pytest.raises(OperationError):
        mark(daemon, wid, version)
    assert daemon.journal.db.execute("SELECT COUNT(*) FROM operations").fetchone()[0] == before


async def test_http_rpc_bootstrap_pagination_and_events_do_not_mark_read(served):
    d, port = served
    pid = await project(d)
    ids = [await item(d, pid, f"Update {i}") for i in range(3)]
    auth = token(d, "reader", "observe")
    _, boot = await http(port, "GET", "/api/v1/bootstrap", tok=auth)
    assert boot["capabilities"]["features"]["work_item_reads"] == {"version": 1}
    checkpoint = boot["sync"]["checkpoint"]
    await http(port, "GET", f"/api/v1/events?after={checkpoint['cursor']}&checkpoint={checkpoint['token']}", tok=auth)
    _, one = await http(port, "GET", "/api/v1/work-items?unread=true&limit=1", tok=auth)
    assert len(one["work_items"]) == 1 and one["next_cursor"]
    wid = one["work_items"][0]["work_item_id"]
    _, detail = await http(port, "GET", f"/api/v1/work-items/{wid}", tok=auth)
    assert detail["work_item"]["reading"]["unread"]
    assert not d.journal.db.execute("SELECT 1 FROM work_item_reads").fetchone()
    mark(d, wid, 1)
    await settle_operations(d.ops)
    _, remaining = await http(port, "GET", "/api/v1/work-items?unread=true&limit=1", tok=auth)
    _, rest = await http(port, "GET", "/api/v1/work-items?unread=true&limit=1&cursor=" + remaining["next_cursor"], tok=auth)
    assert not rest["next_cursor"]
    assert {x["work_item_id"] for x in remaining["work_items"] + rest["work_items"]} == set(ids) - {wid}
    rpc = await d.call_api("work_items_list", {"unread": False}, READER)
    assert [x["work_item_id"] for x in rpc["work_items"]] == [wid]
    rpc_detail = await d.call_api("work_item_get", {"work_item_id": wid}, READER)
    assert not rpc_detail["work_item"]["reading"]["unread"]
    status, _ = await http(port, "GET", "/api/v1/work-items?unread=perhaps", tok=auth)
    assert status == 422
    status, _ = await http(port, "GET", "/api/v1/work-items?unread=true", tok=None)
    assert status == 401
