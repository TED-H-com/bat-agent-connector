"""Field rules and public exits for the E10 operation loop projection.

Parity tests that compare HTTP and MCP stay on the public routes. Field-rule
tests call ``attach`` directly. Exit tests use the HTTP app, the RPC door, and
the principal MCP server.
"""
from __future__ import annotations

import asyncio
import json
import time
import uuid

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from bat_agent_connector import (
    controller_authority,
    pm_discussions,
    pm_evidence,
    pm_source_lineage,
    pm_work_start,
    pm_workspace,
)
from bat_agent_connector.api_auth import Principal
from bat_agent_connector.mcp_server import build_server
from bat_agent_connector.operations import OperationService
from bat_agent_connector.pm_delivery_lineage import ACTIONS as DELIVERY_ACTIONS
from bat_agent_connector.pm_operation_loop import LOOP_ACTIONS, attach
from bat_agent_connector.task_journal import Journal
from tests.operation_helpers import settle_operations
from tests.test_api_v1 import MANUAL, http, token
from tests.test_api_v1 import daemon as daemon_fixture
from tests.test_api_v1 import served as served_fixture
from tests.test_pm_plan_operations import OWNER as PACKAGE_OWNER
from tests.test_pm_work_packages import action as pkg_action
from tests.test_pm_work_packages import create as create_package
from tests.test_pm_work_packages import member as add_member
from tests.test_pm_work_packages import ready as ready_fixture
from tests.test_pm_work_start import STARTER_OWNER, _advance_start, _start_envelope
from tests.test_pm_worktree import pm as pm_fixture
from tests.test_repository_sync import world as world_fixture

daemon, served = daemon_fixture, served_fixture
ready, pm, world = ready_fixture, pm_fixture, world_fixture

OWNER = Principal("loop-owner", frozenset({"observe", "manage", "start", "operate"}))
WS = "ws_" + "ab" * 16
WI = "wi_" + "cd" * 10
WI_OTHER = "wi_" + "ef" * 10
ATT = "att_" + "12" * 16


def _service(tmp_path):
    journal = Journal(tmp_path / "loop.db")
    ops = OperationService(journal)
    pm_evidence.schema(journal)
    pm_source_lineage.schema(journal)
    now = time.time()
    server = pm_workspace.server_id(ops.db)
    with journal.tx():
        ops.db.execute(
            "INSERT INTO pm_workspaces VALUES(?,?,?,?,1,?,?,?,?)",
            (WS, server, "Loop", '["o/r"]', OWNER.actor, now, now, "op_ws_loop"),
        )
        ops.db.execute(
            "INSERT INTO pm_workspace_members VALUES(?,?,?,?,?,?)",
            (WS, OWNER.actor, "owner", pm_workspace.controller_for(ops.db, OWNER), now, now),
        )
    return ops, journal


def _bind(ops, work_item_id, *, binding_id="pmb_loop_1", operation_id="op_bind_loop"):
    ops.db.execute(
        """INSERT INTO pm_work_bindings(binding_id,workspace_id,work_item_id,project_binding_id,project_id,
           work_version,requirement_ids,rationale,actor,created_at,operation_id)
           VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
        (binding_id, WS, work_item_id, "pmbj_loop", "prj_" + "a1" * 10, 1, "[]", "bound",
         OWNER.actor, time.time(), operation_id),
    )


def _op(ops, *, action, operation_id, target=None, result=None, refs=None, status="succeeded", updated_at=1710000000.0):
    ops.db.execute(
        """INSERT INTO operations(operation_id,actor,entry,idem_key,request_hash,action,target,params,preconditions,
           status,result,external_refs,created_at,updated_at)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (operation_id, OWNER.actor, "test", operation_id, "hash", action,
         json.dumps(target or {}), "{}", "{}", status,
         json.dumps(result) if result is not None else None,
         json.dumps(refs) if refs is not None else None,
         updated_at, updated_at),
    )
    return ops.get(operation_id)


def _work_start_refs(work_item_id=WI):
    # A planted child walks package_plan["mode"]. A missing mode is a KeyError, which the loop does not catch.
    intent = {"workspace_id": WS, "work_item_id": work_item_id, "package_plan": {"mode": "recorded"}}
    return {"admission_binding": {"work_start": intent, "work_start_digest": pm_work_start._digest(intent)}}


def _attempt(ops, operation_id, *, attempt_id=ATT, work_item_id=WI):
    ops.db.execute(
        """INSERT INTO work_attempts(attempt_id,workspace_id,work_item_id,origin_kind,origin_id,start_operation_id,
           predecessor_attempt_id,admission_digest,created_at) VALUES(?,?,?,?,?,?,?,?,?)""",
        (attempt_id, WS, work_item_id, "operation", operation_id, operation_id, None, "digest", time.time()),
    )


async def test_allowlist_is_the_section_32_list():
    assert LOOP_ACTIONS == frozenset(pm_discussions.ACTIONS) | {
        "pm.run.analyze", "pm.run.bat.analyze",
        "pm.conversation.propose", "pm.conversation.adopt", "pm.plan.draft", "pm.plan.adopt",
        "pm.work_package.create", "work.start", "session.send",
        "github.pr.publish", "github.pr.update", "pm.maintenance.repair",
    } | DELIVERY_ACTIONS
    assert "work.adopt_and_start" not in LOOP_ACTIONS
    assert "repository.continue" not in LOOP_ACTIONS


async def test_discussion_fields_and_non_loop_operation_stays_unchanged(tmp_path):
    journal = Journal(tmp_path / "discuss.db")
    ops = OperationService(journal)
    pm_workspace.install(ops)
    pm_discussions.install(ops)
    created, _ = ops.create(OWNER, action="pm.workspace.create", params={"name": "A useful product"},
                            idempotency_key=uuid.uuid4().hex)
    await settle_operations(ops)
    workspace_op = ops.get(created["operation_id"])
    before = ops.db.total_changes
    assert await attach(ops, OWNER, workspace_op) is workspace_op
    assert "loop" not in workspace_op
    assert ops.db.total_changes == before

    ws = workspace_op["result"]["workspace"]
    thread_op, _ = ops.create(
        OWNER, action="pm.thread.create", target={"workspace_id": ws["workspace_id"]},
        params={"title": "Paid membership"}, preconditions={"expected_version": ws["version"]},
        idempotency_key=uuid.uuid4().hex)
    await settle_operations(ops)
    thread_op = ops.get(thread_op["operation_id"])
    thread_id = thread_op["result"]["thread_id"]
    projected = await attach(ops, OWNER, thread_op)
    loop = projected["loop"]
    assert loop["operation_id"] == thread_op["operation_id"]
    assert loop["work_item_id"] is None and loop["attempt_id"] is None
    assert loop["state"] == {"operation_status": "succeeded", "updated_at": thread_op["updated_at"]}
    assert loop["links"] is None and loop["result_read"] is None
    assert loop["dashboard_hash"] == f"#/pm/{ws['workspace_id']}/discussions/{thread_id}"
    assert loop["completeness"] == {"status": "complete", "reason": None}
    assert loop["next_allowed_actions"] == [] and loop["blocked_actions"] == []
    assert "loop" not in ops.get(thread_op["operation_id"])

    message, _ = ops.create(
        OWNER, action="pm.message.append", target={"workspace_id": ws["workspace_id"], "thread_id": thread_id},
        params={"content": "A paid tier may be useful"},
        preconditions={"expected_version": pm_discussions.get_thread(ops, OWNER, ws["workspace_id"], thread_id)["thread"]["version"]},
        idempotency_key=uuid.uuid4().hex)
    await settle_operations(ops)
    message = ops.get(message["operation_id"])
    message_loop = (await attach(ops, OWNER, message))["loop"]
    assert message_loop["dashboard_hash"] == loop["dashboard_hash"]
    assert message_loop["work_item_id"] is None
    assert message_loop["completeness"] == {"status": "complete", "reason": None}
    journal.close()


async def test_package_work_id_follows_visibility(tmp_path):
    ops, journal = _service(tmp_path)
    _bind(ops, WI)
    visible = _op(ops, action="pm.work_package.create", operation_id="op_pkg_visible",
                  target={"workspace_id": WS, "work_item_id": WI})
    hidden = _op(ops, action="pm.work_package.create", operation_id="op_pkg_hidden",
                 target={"workspace_id": WS, "work_item_id": WI_OTHER})
    shown = await attach(ops, OWNER, visible)
    withheld = await attach(ops, OWNER, hidden)
    assert shown["loop"]["work_item_id"] == WI
    assert shown["loop"]["attempt_id"] is None
    assert shown["loop"]["dashboard_hash"] == f"#/pm/{WS}/board?select={WI}"
    assert shown["loop"]["completeness"] == {"status": "complete", "reason": None}
    assert shown["loop"]["links"] is None and shown["loop"]["result_read"] is None
    # No binding and no lineage row: E04 still accepts the Work.
    assert withheld["loop"]["work_item_id"] == WI_OTHER
    assert withheld["loop"]["completeness"] == {"status": "complete", "reason": None}
    assert withheld["loop"]["dashboard_hash"] == f"#/pm/{WS}/board?select={WI_OTHER}"
    assert withheld["loop"]["operation_id"] == "op_pkg_hidden"

    ops.db.execute(
        "INSERT INTO pm_source_lineage VALUES('work_item', ?, 'op_missing_root', 'op_missing_root')", (WI,))
    again = await attach(ops, OWNER, visible)
    assert again["loop"]["work_item_id"] is None
    assert again["loop"]["completeness"]["reason"] == "no_work_item"
    assert again["loop"]["dashboard_hash"] == "#/op/op_pkg_visible"
    journal.close()


async def test_work_start_attempt_fields_and_missing_attempt(tmp_path):
    ops, journal = _service(tmp_path)
    _bind(ops, WI)
    started = _op(ops, action="work.start", operation_id="op_start_fields",
                  target={"workspace_id": WS, "work_item_id": WI}, refs=_work_start_refs())
    missing = _op(ops, action="work.start", operation_id="op_start_missing",
                  target={"workspace_id": WS, "work_item_id": WI}, refs=_work_start_refs())
    _attempt(ops, started["operation_id"])
    row = dict(ops.db.execute("SELECT * FROM work_attempts WHERE start_operation_id=?",
                              (started["operation_id"],)).fetchone())
    projected = await attach(ops, OWNER, started)
    loop = projected["loop"]
    assert loop["operation_id"] == started["operation_id"]
    assert loop["work_item_id"] == WI and loop["attempt_id"] == ATT
    assert loop["state"]["operation_status"] == "succeeded"
    assert loop["state"]["updated_at"] == started["updated_at"]
    assert loop["state"]["attempt_stage"] == pm_work_start._attempt_view(ops, row)["stage"]
    assert loop["links"]["start_operation_hash"] == "#/op/op_start_fields"
    assert loop["links"]["work_item_hash"] == f"#/pm/{WS}/board?select={WI}"
    assert loop["links"]["execution_operation_hash"] is None and loop["links"]["session_hash"] is None
    assert loop["result_read"]["result"]["name"] == "pm_work_attempt_result_get"
    assert loop["result_read"]["result"]["http"].endswith("/result")
    assert "{workspace_id}" in loop["result_read"]["changes"]["http"]
    assert loop["result_read"]["diff"]["name"] == "pm_work_attempt_diff_get"
    assert loop["completeness"] == {"status": "complete", "reason": None}
    assert loop["dashboard_hash"] == f"#/pm/{WS}/board?select={WI}"

    absent = (await attach(ops, OWNER, missing))["loop"]
    assert absent["work_item_id"] is None and absent["attempt_id"] is None
    assert "attempt_stage" not in absent["state"]
    assert absent["result_read"] is None
    assert absent["links"]["start_operation_hash"] == "#/op/op_start_missing"
    assert absent["links"]["work_item_hash"] is None
    assert absent["completeness"] == {"status": "partial", "reason": "no_attempt"}
    assert absent["dashboard_hash"] == "#/op/op_start_missing"
    journal.close()


async def test_proven_child_uses_parent_attempt_and_unproven_child_has_no_loop(tmp_path):
    ops, journal = _service(tmp_path)
    _bind(ops, WI)
    parent = _op(ops, action="work.start", operation_id="op_parent_start",
                 target={"workspace_id": WS, "work_item_id": WI}, refs=_work_start_refs())
    _attempt(ops, parent["operation_id"], attempt_id=ATT)
    child = _op(
        ops, action="repository.continue", operation_id="op_child_continue",
        target={"workspace_id": WS, "work_item_id": WI_OTHER},
        refs={"work_start_parent": {"operation_id": parent["operation_id"], "slot": "handoff"}},
    )
    ops.db.execute(
        """INSERT INTO pm_work_start_children(parent_id,slot,ordinal,operation_id,request,request_digest,created_at)
           VALUES(?,?,?,?,?,?,?)""",
        (parent["operation_id"], "handoff", 2, child["operation_id"], "{}", "digest", time.time()),
    )
    child = ops.get(child["operation_id"])
    loop = (await attach(ops, OWNER, child))["loop"]
    assert loop["operation_id"] == child["operation_id"]
    assert loop["work_item_id"] == WI and loop["attempt_id"] == ATT
    assert loop["links"]["start_operation_hash"] == f"#/op/{parent['operation_id']}"
    assert WI_OTHER not in json.dumps(loop)

    broken = _op(
        ops, action="pm.work_package.create", operation_id="op_broken_child",
        target={"workspace_id": WS, "work_item_id": WI},
        refs={"work_start_parent": {"operation_id": parent["operation_id"], "slot": "package"}},
    )
    assert await attach(ops, OWNER, broken) is broken
    lone = {"operation_id": "op_not_child", "action": "repository.continue", "status": "succeeded",
            "updated_at": 1.0, "target": {}, "external_refs": {}}
    assert await attach(ops, OWNER, lone) is lone
    journal.close()


async def test_session_hash_requires_a_readable_session(tmp_path):
    ops, journal = _service(tmp_path)
    readable = _op(ops, action="session.send", operation_id="op_send_read",
                   target={"host": "h1", "session_id": "s1"})
    unread = _op(ops, action="session.send", operation_id="op_send_unread",
                 target={"host": "h1", "session_id": "s2"})
    now = time.time()
    ops.db.execute(
        """INSERT INTO sessions_observed(host,session_id,body,digest,provenance,api_access,attention,sort_key,
           first_seen_at,last_seen_at,missing_count) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
        ("h1", "s1", "{}", "digest", "connector_managed", "managed", 0, 0, now, now, 0),
    )
    shown = (await attach(ops, OWNER, readable))["loop"]
    hidden = (await attach(ops, OWNER, unread))["loop"]
    assert shown["dashboard_hash"] == "#/session/h1/s1"
    assert shown["work_item_id"] is None and shown["links"] is None
    assert shown["completeness"] == {"status": "partial", "reason": "session_target"}
    assert hidden["dashboard_hash"] == "#/op/op_send_unread"
    assert "s2" not in json.dumps(hidden)
    assert hidden["completeness"]["reason"] == "session_target"

    ops.db.execute(
        "INSERT INTO pm_source_lineage VALUES('session', 'h1/s1', 'op_missing_session', 'op_missing_session')")
    withdrawn = (await attach(ops, OWNER, readable))["loop"]
    assert withdrawn["dashboard_hash"] == "#/op/op_send_read"
    assert "s1" not in json.dumps(withdrawn)
    assert withdrawn["completeness"] == {"status": "unavailable", "reason": "source_withdrawn"}
    journal.close()


async def test_delivery_work_id_is_exactly_one_visible_lineage_row(tmp_path):
    ops, journal = _service(tmp_path)
    _bind(ops, WI, binding_id="pmb_del_1", operation_id="op_bind_del_1")
    _bind(ops, WI_OTHER, binding_id="pmb_del_2", operation_id="op_bind_del_2")

    def lineage(operation_id, original_id, work_ids):
        # The original has to stay visible. A work_item root that is work.start
        # uses that action's source check, which is membership of its workspace.
        _op(ops, action="work.start", operation_id=original_id,
            target={"workspace_id": WS, "work_item_id": WI}, refs=_work_start_refs())
        ops.db.execute(
            "INSERT INTO pm_source_lineage VALUES('operation', ?, ?, ?)",
            (operation_id, original_id, operation_id),
        )
        for work_id in work_ids:
            ops.db.execute(
                "INSERT INTO pm_source_lineage VALUES('work_item', ?, ?, ?)",
                (work_id, original_id, operation_id),
            )

    lineage("op_del_one", "op_root_one", [WI])
    lineage("op_del_two", "op_root_two", [WI, WI_OTHER])
    lineage("op_del_none", "op_root_none", [])
    lineage("op_publish", "op_root_publish", [WI])
    one = _op(ops, action="integration.apply", operation_id="op_del_one", target={})
    two = _op(ops, action="github.pr.merge", operation_id="op_del_two", target={})
    none = _op(ops, action="deployment.start", operation_id="op_del_none", target={})
    publish = _op(ops, action="github.pr.publish", operation_id="op_publish",
                  target={"workspace_id": WS, "repository": "o/r", "host": "h1", "source_operation_id": "op_src"})
    repair = _op(ops, action="pm.maintenance.repair", operation_id="op_repair", target={"workspace_id": WS})

    one_loop = (await attach(ops, OWNER, one))["loop"]
    assert one_loop["work_item_id"] == WI
    assert one_loop["attempt_id"] is None
    assert one_loop["completeness"] == {"status": "complete", "reason": None}
    assert one_loop["dashboard_hash"] == f"#/pm/{WS}/board?select={WI}"
    two_loop = (await attach(ops, OWNER, two))["loop"]
    assert two_loop["work_item_id"] is None
    assert two_loop["completeness"] == {"status": "partial", "reason": "delivery_unlinked"}
    none_loop = (await attach(ops, OWNER, none))["loop"]
    assert none_loop["work_item_id"] is None
    assert none_loop["completeness"]["reason"] == "delivery_unlinked"
    publish_loop = (await attach(ops, OWNER, publish))["loop"]
    repair_loop = (await attach(ops, OWNER, repair))["loop"]
    assert publish_loop["work_item_id"] is None and repair_loop["work_item_id"] is None
    assert publish_loop["completeness"] == {"status": "complete", "reason": None}
    assert repair_loop["completeness"] == {"status": "complete", "reason": None}
    assert publish_loop["dashboard_hash"] == "#/op/op_publish"
    journal.close()


def _member(ops, actor, role):
    ops.db.execute(
        "INSERT INTO pm_workspace_members VALUES(?,?,?,?,?,?)",
        (WS, actor, role, pm_workspace.controller_for(ops.db, Principal(actor, frozenset())), time.time(), time.time()),
    )


def _child_row(ops, parent_id, slot, operation_id, ordinal):
    ops.db.execute(
        """INSERT INTO pm_work_start_children(parent_id,slot,ordinal,operation_id,request,request_digest,created_at)
           VALUES(?,?,?,?,?,?,?)""",
        (parent_id, slot, ordinal, operation_id, "{}", "digest", time.time()),
    )


async def test_child_rule_requires_equal_links_and_a_visible_parent(tmp_path):
    ops, journal = _service(tmp_path)
    _bind(ops, WI)
    _bind(ops, WI_OTHER, binding_id="pmb_child_other", operation_id="op_bind_child_other")
    parent = _op(ops, action="work.start", operation_id="op_child_parent",
                 target={"workspace_id": WS, "work_item_id": WI}, refs=_work_start_refs())
    _attempt(ops, parent["operation_id"], attempt_id=ATT)
    other = _op(ops, action="session.send", operation_id="op_not_start", target={"host": "h1", "session_id": "s1"})

    def linked(operation_id, action, parent_id, slot, ordinal, *, target, refs_slot=None):
        op = _op(ops, action=action, operation_id=operation_id, target=target,
                 refs={"work_start_parent": {"operation_id": parent_id, "slot": refs_slot or slot}})
        _child_row(ops, parent_id, slot, operation_id, ordinal)
        return ops.get(op["operation_id"])

    proven = linked("op_pkg_child", "pm.work_package.create", parent["operation_id"], "package", 1,
                    target={"workspace_id": WS, "work_item_id": WI_OTHER})
    loop = (await attach(ops, OWNER, proven))["loop"]
    assert loop["operation_id"] == "op_pkg_child"
    assert loop["work_item_id"] == WI and loop["attempt_id"] == ATT
    assert loop["links"]["start_operation_hash"] == "#/op/op_child_parent"
    assert WI_OTHER not in json.dumps(loop)
    assert "PM_WORK_START_CHILD_CHANGED" not in json.dumps(loop)

    no_attempt_parent = _op(ops, action="work.start", operation_id="op_child_no_attempt",
                            target={"workspace_id": WS, "work_item_id": WI}, refs=_work_start_refs())
    no_attempt = linked("op_pkg_no_attempt", "pm.work_package.create", no_attempt_parent["operation_id"], "handoff", 1,
                        target={"workspace_id": WS, "work_item_id": WI_OTHER})
    waiting = (await attach(ops, OWNER, no_attempt))["loop"]
    assert waiting["work_item_id"] is None and waiting["attempt_id"] is None
    assert waiting["completeness"] == {"status": "partial", "reason": "no_attempt"}
    assert WI_OTHER not in json.dumps(waiting)

    unequal = linked("op_pkg_unequal", "pm.work_package.create", parent["operation_id"], "bind", 2,
                     target={"workspace_id": WS, "work_item_id": WI}, refs_slot="package")
    assert await attach(ops, OWNER, unequal) is unequal
    table_only = _op(ops, action="pm.work_package.create", operation_id="op_pkg_table_only",
                     target={"workspace_id": WS, "work_item_id": WI})
    _child_row(ops, parent["operation_id"], "preview", table_only["operation_id"], 3)
    table_only = ops.get(table_only["operation_id"])
    assert await attach(ops, OWNER, table_only) is table_only
    missing_parent = linked("op_child_missing_parent", "repository.continue", "op_absent_parent", "handoff", 1,
                            target={})
    assert await attach(ops, OWNER, missing_parent) is missing_parent
    wrong_action = linked("op_child_wrong_action", "repository.continue", other["operation_id"], "package", 1,
                          target={})
    assert await attach(ops, OWNER, wrong_action) is wrong_action

    _member(ops, "loop-reader", "reader")
    _member(ops, "loop-blind", "contributor")
    reader = Principal("loop-reader", frozenset({"observe"}))
    blind = Principal("loop-blind", frozenset({"manage"}))
    outsider = Principal("loop-outsider", frozenset({"observe", "manage"}))
    assert (await attach(ops, reader, proven))["loop"]["work_item_id"] == WI
    assert await attach(ops, blind, proven) is proven
    assert await attach(ops, outsider, proven) is proven
    journal.close()


async def test_completeness_uses_the_first_matching_reason(tmp_path):
    ops, journal = _service(tmp_path)
    _bind(ops, WI)
    pm_discussions.install(ops)

    hidden_attempt = _op(ops, action="work.start", operation_id="op_start_hidden_work",
                         target={"workspace_id": WS, "work_item_id": WI}, refs=_work_start_refs())
    _attempt(ops, hidden_attempt["operation_id"], attempt_id="att_" + "34" * 16, work_item_id=WI_OTHER)
    ops.db.execute(
        "INSERT INTO pm_source_lineage VALUES('work_item', ?, 'op_missing_attempt_work', 'op_missing_attempt_work')",
        (WI_OTHER,))
    hidden_loop = (await attach(ops, OWNER, hidden_attempt))["loop"]
    assert hidden_loop["work_item_id"] is None
    assert hidden_loop["completeness"] == {"status": "partial", "reason": "no_work_item"}

    no_work = _op(ops, action="work.start", operation_id="op_start_no_work",
                  target={"workspace_id": WS, "work_item_id": WI_OTHER}, refs=_work_start_refs(WI_OTHER))
    assert (await attach(ops, OWNER, no_work))["loop"]["completeness"] == {
        "status": "partial", "reason": "no_work_item"}
    malformed = _op(ops, action="pm.work_package.create", operation_id="op_pkg_malformed",
                    target={"workspace_id": WS, "work_item_id": "wi_nope"})
    absent_key = _op(ops, action="pm.work_package.create", operation_id="op_pkg_absent",
                     target={"workspace_id": WS})
    assert (await attach(ops, OWNER, malformed))["loop"]["completeness"]["reason"] == "no_work_item"
    assert (await attach(ops, OWNER, absent_key))["loop"]["completeness"]["reason"] == "no_work_item"

    _member(ops, "loop-reader", "reader")
    reader = Principal("loop-reader", frozenset({"observe"}))
    outsider = Principal("loop-outsider", frozenset({"observe", "manage"}))
    package = _op(ops, action="pm.work_package.create", operation_id="op_pkg_member",
                  target={"workspace_id": WS, "work_item_id": WI})
    assert (await attach(ops, reader, package))["loop"]["work_item_id"] == WI
    outsider_loop = (await attach(ops, outsider, package))["loop"]
    assert outsider_loop["work_item_id"] is None
    assert outsider_loop["completeness"]["reason"] == "no_work_item"

    thread_id = "pmt_" + "ab" * 16
    missing_thread = _op(ops, action="pm.message.append", operation_id="op_msg_missing",
                         target={"workspace_id": WS, "thread_id": thread_id})
    missing_loop = (await attach(ops, OWNER, missing_thread))["loop"]
    assert missing_loop["dashboard_hash"] == "#/op/op_msg_missing"
    assert thread_id not in json.dumps(missing_loop)
    assert missing_loop["completeness"] == {"status": "complete", "reason": None}
    pending = _op(ops, action="pm.thread.create", operation_id="op_thread_pending",
                  target={"workspace_id": WS})
    bad_thread = _op(ops, action="pm.message.append", operation_id="op_msg_bad_thread",
                     target={"workspace_id": WS, "thread_id": "thread-1"})
    assert (await attach(ops, OWNER, pending))["loop"]["completeness"] == {"status": "complete", "reason": None}
    assert (await attach(ops, OWNER, bad_thread))["loop"]["completeness"]["reason"] is None
    assert (await attach(ops, OWNER, bad_thread))["loop"]["dashboard_hash"] == "#/op/op_msg_bad_thread"

    with_work = _op(ops, action="session.send", operation_id="op_send_work",
                    target={"host": "h1", "session_id": "s4", "work_item_id": WI})
    assert (await attach(ops, OWNER, with_work))["loop"]["completeness"] == {"status": "complete", "reason": None}

    visible_exec = _op(ops, action="session.send", operation_id="op_exec_visible",
                       target={"host": "h1", "session_id": "s9"})
    shown = _op(ops, action="work.start", operation_id="op_start_exec",
                target={"workspace_id": WS, "work_item_id": WI}, refs=_work_start_refs(),
                result={"execution_operation_id": visible_exec["operation_id"], "stage": "started"})
    _attempt(ops, shown["operation_id"], attempt_id="att_" + "56" * 16)
    shown_loop = (await attach(ops, OWNER, shown))["loop"]
    assert shown_loop["links"]["execution_operation_hash"] == "#/op/op_exec_visible"
    assert shown_loop["completeness"] == {"status": "complete", "reason": None}
    withheld = _op(ops, action="work.start", operation_id="op_start_exec_hidden",
                   target={"workspace_id": WS, "work_item_id": WI}, refs=_work_start_refs(),
                   result={"execution_operation_id": "op_exec_missing", "stage": "started"})
    _attempt(ops, withheld["operation_id"], attempt_id="att_" + "78" * 16)
    withheld_loop = (await attach(ops, OWNER, withheld))["loop"]
    assert withheld_loop["links"]["execution_operation_hash"] is None
    assert "op_exec_missing" not in json.dumps(withheld_loop)
    assert withheld_loop["completeness"] == {"status": "unavailable", "reason": "source_withdrawn"}

    sessioned = _op(ops, action="work.start", operation_id="op_start_session",
                    target={"workspace_id": WS, "work_item_id": WI}, refs=_work_start_refs(),
                    result={"session": {"host": "h1", "session_id": "s3"}, "stage": "started"})
    _attempt(ops, sessioned["operation_id"], attempt_id="att_" + "9a" * 16)
    assert (await attach(ops, OWNER, sessioned))["loop"]["links"]["session_hash"] == "#/session/h1/s3"
    ops.db.execute(
        "INSERT INTO pm_source_lineage VALUES('session', 'h1/s3', 'op_missing_link', 'op_missing_link')")
    withdrawn_session = (await attach(ops, OWNER, sessioned))["loop"]
    assert withdrawn_session["links"]["session_hash"] is None
    assert "s3" not in json.dumps(withdrawn_session["links"])
    assert withdrawn_session["completeness"]["reason"] == "source_withdrawn"

    original = _op(ops, action="work.start", operation_id="op_del_root_recorded",
                   target={"workspace_id": WS, "work_item_id": WI}, refs=_work_start_refs())
    delivery = _op(ops, action="integration.apply", operation_id="op_del_recorded",
                   target={"workspace_id": "ws_" + "ff" * 16})
    ops.db.execute("INSERT INTO pm_source_lineage VALUES('operation', ?, ?, ?)",
                   (delivery["operation_id"], original["operation_id"], delivery["operation_id"]))
    ops.db.execute("INSERT INTO pm_source_lineage VALUES('work_item', ?, ?, ?)",
                   (WI, original["operation_id"], delivery["operation_id"]))
    recorded = (await attach(ops, OWNER, delivery))["loop"]
    assert recorded["work_item_id"] is None and WI not in json.dumps(recorded)
    assert recorded["completeness"] == {"status": "partial", "reason": "delivery_unlinked"}
    journal.close()


async def test_missing_child_stores_fall_through_to_the_allowlist(tmp_path):
    journal = Journal(tmp_path / "bare.db")
    ops = OperationService(journal)
    send = _op(ops, action="session.send", operation_id="op_bare_send",
               target={"host": "h1", "session_id": "s1"})
    loop = (await attach(ops, OWNER, send))["loop"]
    assert loop["operation_id"] == "op_bare_send"
    assert loop["work_item_id"] is None and loop["attempt_id"] is None
    assert loop["dashboard_hash"] == "#/op/op_bare_send"
    assert loop["completeness"] == {"status": "partial", "reason": "session_target"}
    linked = _op(
        ops, action="session.send", operation_id="op_bare_linked", target={"host": "h1", "session_id": "s1"},
        refs={"work_start_parent": {"operation_id": "op_none", "slot": "handoff"}})
    assert await attach(ops, OWNER, linked) is linked
    plain = {"operation_id": "op_bare_continue", "action": "repository.continue", "status": "succeeded",
             "updated_at": 1.0, "target": {}, "external_refs": {}}
    assert await attach(ops, OWNER, plain) is plain
    journal.close()


def _loop_of(operation):
    assert "loop" in operation
    assert operation["loop"]["operation_id"] == operation["operation_id"]
    return operation["loop"]


async def test_single_operation_exits_attach_loop_and_lists_do_not(served):
    daemon, port = served
    auth = token(daemon, "loop-exit", "observe", "manage")
    outsider = token(daemon, "loop-outsider", "observe", "manage")

    async def post(body, key, *, wait=None):
        path = "/api/v1/operations" if wait is None else f"/api/v1/operations?wait={wait}"
        return await http(port, "POST", path, tok=auth, body=body, headers={"Idempotency-Key": key})

    async def rpc(method, params):
        return await http(port, "POST", "/rpc", tok=auth, body={"method": method, "params": params})

    status, workspace = await post(
        {"action": "pm.workspace.create", "params": {"name": "A useful product"}}, "loop-workspace")
    assert status == 202 and workspace["created"] is True
    assert "loop" not in workspace["operation"]
    await settle_operations(daemon.ops)
    workspace_id = workspace["operation"]["operation_id"]
    stored = daemon.ops.get(workspace_id)
    assert "loop" not in stored and stored["status"] == "succeeded"
    wid = stored["result"]["workspace"]["workspace_id"]
    version = stored["result"]["workspace"]["version"]
    status, got = await http(port, "GET", "/api/v1/operations/" + workspace_id, tok=auth)
    assert status == 200
    assert set(got) == {"operation", "work_items", "cleanup_receipts"}
    assert "loop" not in got["operation"]

    def thread_body(title):
        return {"action": "pm.thread.create", "target": {"workspace_id": wid}, "params": {"title": title},
                "preconditions": {"expected_version": version}}

    status, created = await post(thread_body("Cancel on HTTP"), "loop-http-cancel")
    assert status == 202 and created["created"] is True
    http_id = created["operation"]["operation_id"]
    _loop_of(created["operation"])
    assert "loop" not in daemon.ops.get(http_id)
    status, cancelled = await http(port, "POST", f"/api/v1/operations/{http_id}/cancel", tok=auth)
    assert status == 200 and cancelled["operation"]["status"] == "cancelled"
    _loop_of(cancelled["operation"])
    before = daemon.ops.db.total_changes
    status, again = await http(port, "GET", "/api/v1/operations/" + http_id, tok=auth)
    assert status == 200 and daemon.ops.db.total_changes == before
    assert set(again) == {"operation", "work_items", "cleanup_receipts"}
    assert _loop_of(again["operation"])["state"]["operation_status"] == "cancelled"
    status, via_rpc = await rpc("op_get", {"operation_id": http_id})
    assert status == 200
    assert _loop_of(via_rpc["result"]["operation"]) == again["operation"]["loop"]
    assert "loop" not in daemon.ops.get(http_id)

    status, submitted = await rpc("op_submit", {**thread_body("Cancel on RPC"), "idempotency_key": "loop-rpc-cancel"})
    assert status == 200 and submitted["result"]["created"] is True
    rpc_id = submitted["result"]["operation"]["operation_id"]
    _loop_of(submitted["result"]["operation"])
    status, rpc_cancelled = await rpc("op_cancel", {"operation_id": rpc_id})
    assert status == 200 and rpc_cancelled["result"]["operation"]["status"] == "cancelled"
    _loop_of(rpc_cancelled["result"]["operation"])

    replay = thread_body("Replay after wait")
    status, first = await post(replay, "loop-replay")
    assert status == 202 and _loop_of(first["operation"])
    await settle_operations(daemon.ops)
    assert daemon.ops.get(first["operation"]["operation_id"])["status"] == "succeeded"
    status, waited = await post(replay, "loop-replay", wait=1)
    assert status == 200 and waited["created"] is False
    assert waited["operation"]["status"] == "succeeded"
    _loop_of(waited["operation"])
    assert "loop" not in daemon.ops.get(waited["operation"]["operation_id"])
    status, rpc_waited = await rpc("op_submit", {**replay, "idempotency_key": "loop-replay", "wait_s": 1})
    assert status == 200 and rpc_waited["result"]["created"] is False
    assert _loop_of(rpc_waited["result"]["operation"]) == waited["operation"]["loop"]

    status, attention = await post(thread_body("Resume on HTTP"), "loop-http-resume")
    attention_id = attention["operation"]["operation_id"]
    daemon.ops.db.execute("UPDATE operations SET status='needs_attention' WHERE operation_id=?", (attention_id,))
    status, resumed = await http(port, "POST", f"/api/v1/operations/{attention_id}/resume", tok=auth)
    assert status == 200 and resumed["operation"]["status"] == "running"
    assert _loop_of(resumed["operation"])["state"]["operation_status"] == "running"
    assert "loop" not in daemon.ops.get(attention_id)

    status, rpc_attention = await rpc(
        "op_submit", {**thread_body("Resume on RPC"), "idempotency_key": "loop-rpc-resume"})
    rpc_attention_id = rpc_attention["result"]["operation"]["operation_id"]
    daemon.ops.db.execute(
        "UPDATE operations SET status='needs_attention' WHERE operation_id=?", (rpc_attention_id,))
    status, rpc_resumed = await rpc("op_resume", {"operation_id": rpc_attention_id})
    assert status == 200 and rpc_resumed["result"]["operation"]["status"] == "running"
    _loop_of(rpc_resumed["result"]["operation"])

    status, listed = await http(port, "GET", "/api/v1/operations", tok=auth)
    assert status == 200 and listed["operations"]
    assert all("loop" not in item for item in listed["operations"])
    status, rpc_listed = await rpc("op_list", {})
    assert status == 200 and rpc_listed["result"]["operations"]
    assert all("loop" not in item for item in rpc_listed["result"]["operations"])

    status, hidden = await http(port, "GET", "/api/v1/operations/" + http_id, tok=outsider)
    assert status == 404 and hidden["error"]["code"] == "NOT_FOUND" and "operation" not in hidden
    status, rpc_hidden = await http(port, "POST", "/rpc", tok=outsider,
                                    body={"method": "op_get", "params": {"operation_id": http_id}})
    assert status == 400 and rpc_hidden["error"] == "NOT_FOUND" and "operation" not in rpc_hidden
    missing = "op_" + "ab" * 16
    status, absent = await http(port, "GET", "/api/v1/operations/" + missing, tok=auth)
    assert status == 404 and absent["error"]["code"] == "NOT_FOUND" and "operation" not in absent

    status, refused = await post(
        {"action": "pm.thread.create", "target": {}, "params": {"title": "No workspace"},
         "preconditions": {"expected_version": version}}, "loop-refused")
    assert "operation" not in refused and refused["error"]["admission_refused"] is True
    status, rpc_refused = await rpc("op_submit", {
        "action": "pm.thread.create", "target": {}, "params": {"title": "No workspace"},
        "preconditions": {"expected_version": version}, "idempotency_key": "loop-rpc-refused"})
    assert status == 400 and "operation" not in rpc_refused and isinstance(rpc_refused["error"], str)


# The §3.2 rows, identified from the refusal the endpoint returned. This is not a
# principal-by-action outcome matrix: the HTTP call supplies the code.
_ACTOR_ONLY = frozenset({
    "Only the original requester controls this proposal",
    "Only the original requester may control native analysis",
    "Only the original requester may control this adoption",
    "relay child controls retain the original actor",
})
_SCOPE_ONLY = frozenset({
    "PM operation controls require manage scope",
    "Package reads need observe; creation also needs manage",
    "Native PM analysis requires observe, manage and start scopes",
    "Plan operations require manage scope",
    "Plan adoption requires observe, manage and start scopes",
    "Publication requires observe, manage and integrate authority",
    "dispatch linked work needs manage",
    "Starting work requires observe, manage and start scopes",
    "resuming merge-and-deploy needs both merge and deploy scopes",
})
_MEMBERSHIP = "Workspace membership does not permit this action"


def _spec_reason(code, message, operation, principal, ops):
    """§3.2 reason for this refusal, or None when the table says to omit the verb."""
    if code == "CONTROLLER_MISMATCH":
        return "not_controller"
    if code != "FORBIDDEN":
        return None
    if message in _ACTOR_ONLY:
        return "not_controller"
    if message == _MEMBERSHIP:
        if _owner_gate(ops, operation) or _member_role(ops, principal, operation) != "reader":
            return None
        return "not_writer_member"
    if message in _SCOPE_ONLY:
        return "missing_scope"
    return None


def _owner_gate(ops, operation) -> bool:
    action = operation.get("action")
    params = operation.get("params") if isinstance(operation.get("params"), dict) else {}
    target = operation.get("target") if isinstance(operation.get("target"), dict) else {}
    if action in {"pm.plan.adopt", "pm.conversation.adopt"}:
        return True
    if action not in pm_discussions.ACTIONS:
        return False
    decide = pm_discussions._decision(action, params)
    superseded = params.get("supersedes_input_id")
    if action == "pm.input.record" and isinstance(superseded, str):
        old = ops.db.execute(
            "SELECT confirmed_by FROM pm_inputs WHERE thread_id=? AND input_id=?",
            (target.get("thread_id"), superseded)).fetchone()
        decide = decide or bool(old and old[0])
    return bool(decide)


def _member_role(ops, principal, operation):
    target = operation.get("target") if isinstance(operation.get("target"), dict) else {}
    workspace_id = target.get("workspace_id")
    if not isinstance(workspace_id, str):
        return None
    row = ops.db.execute(
        "SELECT role FROM pm_workspace_members WHERE workspace_id=? AND actor=?",
        (workspace_id, principal.actor)).fetchone()
    if row is not None:
        return row["role"]
    if principal.admin:
        return "owner"
    return None


def _insert_operation(ops, *, actor, action, status, target=None, params=None, refs=None):
    operation_id = "op_" + uuid.uuid4().hex
    now = time.time()
    ops.db.execute(
        """INSERT INTO operations(operation_id,actor,entry,idem_key,request_hash,action,target,params,preconditions,
           status,external_refs,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (operation_id, actor, "test", operation_id, "hash", action, json.dumps(target or {}),
         json.dumps(params or {}), "{}", status, json.dumps(refs) if refs is not None else None, now, now))
    return operation_id


async def _agree(daemon, port, tok, principal, operation_id, verb):
    """Read ``loop`` as this principal, then call the real HTTP verb as the same principal."""
    status, body = await http(port, "GET", "/api/v1/operations/" + operation_id, tok=tok)
    assert status == 200, body
    operation = body["operation"]
    loop = operation["loop"]
    offered = [row for row in loop["next_allowed_actions"]
               if row.get("kind") == "operation_endpoint" and row.get("endpoint") == verb]
    blocked = [row for row in loop["blocked_actions"] if row.get("action") == verb]
    assert not offered or not blocked
    status, result = await http(port, "POST", f"/api/v1/operations/{operation_id}/{verb}", tok=tok)
    where = (operation["action"], verb, principal.actor, result)
    if offered:
        assert status < 400, where
        return
    assert status >= 400, where
    reason = _spec_reason(result["error"]["code"], result["error"]["message"], operation, principal, daemon.ops)
    if reason is None:
        assert blocked == [], where
    else:
        assert blocked == [{"action": verb, "reason": reason}], where


def _plant_attempt(ops, operation_id, workspace_id, work_item_id):
    attempt_id = "att_" + uuid.uuid4().hex
    ops.db.execute(
        """INSERT INTO work_attempts(attempt_id,workspace_id,work_item_id,origin_kind,origin_id,start_operation_id,
           predecessor_attempt_id,admission_digest,created_at) VALUES(?,?,?,?,?,?,?,?,?)""",
        (attempt_id, workspace_id, work_item_id, "operation", operation_id, operation_id, None, "digest", time.time()))
    return attempt_id


def _endpoint_rows(rows):
    return [row for row in rows if row.get("kind") == "operation_endpoint"]


def _steering_blocks(rows):
    return [row for row in rows if row.get("action") in {"cancel", "resume", "work.start"}]


async def test_work_start_steering_copies_the_result_envelope(served):
    daemon, port = served
    ops = daemon.ops
    principal = Principal("loop-start", frozenset({"observe", "manage", "start"}))
    created, _ = ops.create(principal, action="pm.workspace.create", params={"name": "A useful product"},
                            idempotency_key=uuid.uuid4().hex)
    await settle_operations(ops)
    workspace = ops.get(created["operation_id"])["result"]["workspace"]
    wid = workspace["workspace_id"]
    work_id = "wi_" + "ab" * 10
    controller = controller_authority.binding(ops.db, principal, pm_workspace_id=wid)
    intent = {"workspace_id": wid, "work_item_id": work_id, "package_plan": {}, "controller": controller}
    refs = {"admission_binding": {"work_start": intent, "work_start_digest": pm_work_start._digest(intent)}}
    tok = token(daemon, principal.actor, "observe", "manage", "start")
    target = {"workspace_id": wid, "work_item_id": work_id}

    async def compare(status_name, *, attempt):
        operation_id = _insert_operation(
            ops, actor=principal.actor, action="work.start", status=status_name, target=target, refs=refs)
        attempt_id = _plant_attempt(ops, operation_id, wid, work_id) if attempt else None
        status, body = await http(port, "GET", "/api/v1/operations/" + operation_id, tok=tok)
        assert status == 200, body
        loop = body["operation"]["loop"]
        if attempt_id is None:
            assert loop["next_allowed_actions"] == [] and loop["blocked_actions"] == []
            return
        status, envelope = await http(
            port, "GET", f"/api/v1/pm/workspaces/{wid}/work-attempts/{attempt_id}/result", tok=tok)
        assert status == 200, envelope
        assert loop["next_allowed_actions"] == _endpoint_rows(envelope["next_allowed_actions"])
        assert loop["blocked_actions"] == _steering_blocks(envelope["blocked_actions"])
        assert loop["next_allowed_actions"] or loop["blocked_actions"]

    for status_name in ("accepted", "needs_attention", "uncertain"):
        await compare(status_name, attempt=True)
    await compare("accepted", attempt=False)


async def _workspace(ops, principal):
    created, _ = ops.create(principal, action="pm.workspace.create", params={"name": "A useful product"},
                            idempotency_key=uuid.uuid4().hex)
    await settle_operations(ops)
    return ops.get(created["operation_id"])["result"]["workspace"]


async def _add_member(ops, principal, workspace_id, actor, role):
    current = pm_workspace.get_workspace(ops.db, principal, workspace_id)
    created, _ = ops.create(
        principal, action="pm.workspace.member.set", target={"workspace_id": workspace_id},
        params={"actor": actor, "role": role}, preconditions={"expected_version": current["version"]},
        idempotency_key=uuid.uuid4().hex)
    await settle_operations(ops)
    done = ops.get(created["operation_id"])
    assert done["status"] == "succeeded", done


async def test_discussion_session_and_delivery_cancel_and_resume_match_http(served, mock):
    daemon, port = served
    ops = daemon.ops
    actor = Principal("loop-actor", frozenset({"observe", "manage", "start", "operate", "merge", "deploy"}))
    writer = Principal("loop-writer", frozenset({"observe", "manage", "operate", "merge", "deploy"}))
    reader = Principal("loop-reader", frozenset({"observe", "manage", "operate"}))
    narrow = Principal(actor.actor, frozenset({"observe"}))
    other = Principal("loop-other", writer.scopes)
    workspace = await _workspace(ops, actor)
    wid = workspace["workspace_id"]
    await _add_member(ops, actor, wid, writer.actor, "contributor")
    await _add_member(ops, actor, wid, reader.actor, "reader")
    await _add_member(ops, actor, wid, other.actor, "contributor")
    version = pm_workspace.get_workspace(ops.db, actor, wid)["version"]
    thread, _ = ops.create(
        actor, action="pm.thread.create", target={"workspace_id": wid}, params={"title": "Paid membership"},
        preconditions={"expected_version": version}, idempotency_key=uuid.uuid4().hex)
    await settle_operations(ops)
    thread = ops.get(thread["operation_id"])
    assert thread["status"] == "succeeded", thread
    thread_id = thread["result"]["thread_id"]
    message_version = pm_discussions.get_thread(ops, actor, wid, thread_id)["thread"]["version"]
    mock.handlers["git:log"] = lambda _payload: [{"hash": "a" * 40}]
    started, _ = ops.create(actor, action="session.start", target={"host": "h1", "workspace": "ws-1"},
                            idempotency_key=uuid.uuid4().hex)
    await settle_operations(ops)
    started = ops.get(started["operation_id"])
    assert started["status"] == "succeeded", started
    session_id = started["result"]["session_id"]
    tokens = {
        actor: token(daemon, actor.actor, "observe", "manage", "start", "operate", "merge", "deploy"),
        writer: token(daemon, writer.actor, *sorted(writer.scopes)),
        reader: token(daemon, reader.actor, *sorted(reader.scopes)),
        narrow: token(daemon, narrow.actor, "observe"),
        other: token(daemon, other.actor, *sorted(other.scopes)),
    }

    def message():
        created, _ = ops.create(
            actor, action="pm.message.append", target={"workspace_id": wid, "thread_id": thread_id},
            params={"content": "A paid tier may be useful"}, preconditions={"expected_version": message_version},
            idempotency_key=uuid.uuid4().hex)
        return created["operation_id"]

    def send():
        created, _ = ops.create(
            actor, action="session.send", target={"host": "h1", "session_id": session_id},
            params={"text": "Discuss the current goal"}, idempotency_key=uuid.uuid4().hex)
        return created["operation_id"]

    def delivery(status):
        return _insert_operation(
            ops, actor=actor.actor, action="delivery.merge_and_deploy", status=status,
            target={"repository": "o/r", "recipe": "prod"})

    def attention(make):
        operation_id = make()
        ops.db.execute("UPDATE operations SET status='needs_attention' WHERE operation_id=?", (operation_id,))
        return operation_id

    cases = (
        ("pm.message.append", message, lambda: attention(message)),
        ("session.send", send, lambda: attention(send)),
        ("delivery.merge_and_deploy", lambda: delivery("accepted"), lambda: delivery("needs_attention")),
    )
    for _action, cancel_make, resume_make in cases:
        for principal in (actor, writer, reader, narrow, other):
            await _agree(daemon, port, tokens[principal], principal, cancel_make(), "cancel")
            await _agree(daemon, port, tokens[principal], principal, resume_make(), "resume")


async def test_package_cancel_and_resume_match_http(ready):
    w = ready
    daemon = w["d"]
    ops = daemon.ops
    source = await create_package(w)
    await add_member(w, "pkg-writer", "contributor")
    await add_member(w, "pkg-reader", "reader")
    await add_member(w, "pkg-other", "contributor")
    writer = Principal("pkg-writer", frozenset({"observe", "manage"}))
    reader = Principal("pkg-reader", frozenset({"observe", "manage"}))
    narrow = Principal(PACKAGE_OWNER.actor, frozenset({"observe"}))
    other = Principal("pkg-other", frozenset({"observe", "manage"}))
    tokens = {
        PACKAGE_OWNER: token(daemon, PACKAGE_OWNER.actor, *sorted(PACKAGE_OWNER.scopes)),
        writer: token(daemon, writer.actor, "observe", "manage"),
        reader: token(daemon, reader.actor, "observe", "manage"),
        narrow: token(daemon, narrow.actor, "observe"),
        other: token(daemon, other.actor, "observe", "manage"),
    }
    server = await asyncio.start_server(daemon._handle, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    try:
        for principal in (PACKAGE_OWNER, writer, reader, narrow, other):
            for status, verb in (("accepted", "cancel"), ("needs_attention", "resume")):
                operation_id = _insert_operation(
                    ops, actor=source["actor"], action=source["action"], status=status, target=source["target"],
                    params=source["params"], refs=source["external_refs"])
                await _agree(daemon, port, tokens[principal], principal, operation_id, verb)
    finally:
        server.close()
        await server.wait_closed()


def unpack(result):
    if isinstance(result, tuple):
        content, structured = result
        return structured if structured is not None else json.loads(content[0].text)
    if getattr(result, "structuredContent", None) is not None:
        return result.structuredContent
    content = result.content if hasattr(result, "content") else result
    return json.loads(content[0].text)


def _operation_count(db):
    return db.execute("SELECT COUNT(*) FROM operations").fetchone()[0]


def _attempt_count(db):
    return db.execute("SELECT COUNT(*) FROM work_attempts").fetchone()[0]


def _attempts_for(db, operation_id):
    return db.execute(
        "SELECT COUNT(*) FROM work_attempts WHERE start_operation_id=?", (operation_id,)).fetchone()[0]


def _has_key(value, key):
    if isinstance(value, dict):
        return key in value or any(_has_key(item, key) for item in value.values())
    if isinstance(value, list):
        return any(_has_key(item, key) for item in value)
    return False


def _scrub_loop(operation):
    """One placeholder for the operation id, then the two facts that are not that id.

    ``state.updated_at`` is this row's own timestamp. A ``work.start`` attempt id is
    ``att_`` plus the operation id's hex, so replacing the operation id leaves it.
    """
    loop = operation["loop"]
    assert loop["operation_id"] == operation["operation_id"]
    assert loop["state"]["updated_at"] == operation["updated_at"]
    scrubbed = json.loads(json.dumps(loop).replace(operation["operation_id"], "op_PLACEHOLDER"))
    assert scrubbed["operation_id"] == "op_PLACEHOLDER"
    scrubbed["state"].pop("updated_at")
    scrubbed.pop("attempt_id")
    return scrubbed


async def _submit(port, tok, body, key):
    return await http(port, "POST", "/api/v1/operations", tok=tok, body=body, headers={"Idempotency-Key": key})


async def _mcp_submit(server, body, key):
    return unpack(await server.call_tool(
        "operation_submit", {**body, "idempotency_key": key, "wait_s": 0, "confirm": True}))


async def _same_operation(server, port, tok, operation_id):
    """HTTP and MCP reads of one operation, then its cancel and its resume refusal."""
    status, body = await http(port, "GET", "/api/v1/operations/" + operation_id, tok=tok)
    assert status == 200, body
    via_mcp = unpack(await server.call_tool("operation_get", {"operation_id": operation_id}))
    assert body["operation"]["loop"] == via_mcp["operation"]["loop"]
    status, cancelled = await http(port, "POST", f"/api/v1/operations/{operation_id}/cancel", tok=tok)
    assert status == 200, cancelled
    via_cancel = unpack(await server.call_tool(
        "operation_cancel", {"operation_id": operation_id, "confirm": True}))
    assert cancelled["operation"]["loop"] == via_cancel["operation"]["loop"]
    status, resumed = await http(port, "POST", f"/api/v1/operations/{operation_id}/resume", tok=tok)
    assert status == 409 and resumed["error"]["code"] == "NOT_RESUMABLE" and "operation" not in resumed
    with pytest.raises(ToolError, match="NOT_RESUMABLE"):
        await server.call_tool("operation_resume", {"operation_id": operation_id, "confirm": True})
    return body["operation"]["loop"]


def _hide_source(db, kind, identity):
    db.execute(
        "INSERT INTO pm_source_lineage VALUES(?, ?, 'op_missing_loop', 'op_missing_loop')",
        (kind, identity),
    )


async def _refuse(server, port, tok, body, key, code, status_code, *, admission_refused, db):
    before = _operation_count(db)
    status, refused = await _submit(port, tok, body, key)
    assert status == status_code, refused
    assert refused["error"]["code"] == code and "operation" not in refused
    if admission_refused:
        assert refused["error"]["admission_refused"] is True
    else:
        assert "admission_refused" not in refused["error"]
    with pytest.raises(ToolError, match=code):
        await server.call_tool("operation_submit", {
            **body, "idempotency_key": key + "-mcp", "wait_s": 0, "confirm": True})
    assert _operation_count(db) == before


@pytest.fixture
async def loop_start(ready):
    from tests.test_pm_package_handoff import dispatch_runner

    dispatch_runner(ready)
    pm_work_start.install(ready["d"].ops)
    return ready


async def test_http_and_mcp_share_one_loop_and_the_same_refusals(served, mock, monkeypatch):
    daemon, port = served
    actor = "loop-parity"
    tok = token(daemon, actor, "observe", "manage", "start", "operate")
    monkeypatch.setenv("BATC_TASK_URL", f"http://127.0.0.1:{port}/rpc")
    monkeypatch.setenv("BATC_API_TOKEN", tok)
    server, fleet = build_server(daemon.fleet.config, principal_only=True)
    try:
        status, http_cap = await http(port, "GET", "/api/v1/capabilities", tok=tok)
        assert status == 200, http_cap
        assert http_cap["features"]["pm_workspaces"]["operation_loop"] == {"version": 1}
        mcp_cap = unpack(await server.call_tool("capabilities_get", {}))
        assert mcp_cap["features"]["pm_workspaces"]["operation_loop"] == {"version": 1}

        mock.handlers["git:log"] = lambda _payload: [{"hash": "a" * 40}]
        status, started = await _submit(
            port, tok, {"action": "session.start", "target": {"host": "h1", "workspace": "ws-1"}}, "loop-session")
        assert status == 202, started
        await settle_operations(daemon.ops)
        status, started_read = await http(
            port, "GET", "/api/v1/operations/" + started["operation"]["operation_id"], tok=tok)
        assert status == 200 and started_read["operation"]["status"] == "succeeded", started_read
        sid = started_read["operation"]["result"]["session_id"]
        await daemon.inventory.refresh_host("h1")

        http_body = {"action": "session.send", "target": {"host": "h1", "session_id": sid},
                     "params": {"text": "Discuss the current goal"}}
        mcp_body = {"action": "session.send", "target": {"host": "h1", "session_id": sid},
                    "params": {"text": "Discuss the current goal over MCP"}}
        status, http_created = await _submit(port, tok, http_body, "loop-http-send")
        assert status == 202 and http_created["created"] is True, http_created
        mcp_created = await _mcp_submit(server, mcp_body, "loop-mcp-send")
        assert mcp_created["created"] is True
        http_id = http_created["operation"]["operation_id"]
        mcp_id = mcp_created["operation"]["operation_id"]
        assert http_id != mcp_id
        assert http_created["operation"]["loop"]["attempt_id"] is None
        assert _scrub_loop(http_created["operation"]) == _scrub_loop(mcp_created["operation"])
        created_rows = _operation_count(daemon.ops.db)
        await settle_operations(daemon.ops)
        assert daemon.ops.get(http_id)["status"] == "succeeded"
        assert daemon.ops.get(mcp_id)["status"] == "succeeded"

        status, replayed = await _submit(port, tok, http_body, "loop-http-send")
        assert status == 200 and replayed["created"] is False
        assert replayed["operation"]["operation_id"] == http_id
        assert replayed["operation"]["loop"]["operation_id"] == http_id
        mcp_replay = await _mcp_submit(server, mcp_body, "loop-mcp-send")
        assert mcp_replay["created"] is False
        assert mcp_replay["operation"]["operation_id"] == mcp_id
        assert mcp_replay["operation"]["loop"]["operation_id"] == mcp_id
        assert _operation_count(daemon.ops.db) == created_rows

        kept = await _same_operation(server, port, tok, http_id)
        await _same_operation(server, port, tok, mcp_id)
        assert kept["dashboard_hash"] == f"#/session/h1/{sid}"

        # A successful resume leaves needs_attention, so each transport resumes its own row.
        http_attention = {"action": "session.send", "target": {"host": "h1", "session_id": sid},
                          "params": {"text": "Resume this send on HTTP"}}
        mcp_attention = {"action": "session.send", "target": {"host": "h1", "session_id": sid},
                         "params": {"text": "Resume this send on MCP"}}
        status, http_hold = await _submit(port, tok, http_attention, "loop-http-hold")
        assert status == 202, http_hold
        mcp_hold = await _mcp_submit(server, mcp_attention, "loop-mcp-hold")
        http_hold_id = http_hold["operation"]["operation_id"]
        mcp_hold_id = mcp_hold["operation"]["operation_id"]
        daemon.ops.db.execute(
            "UPDATE operations SET status='needs_attention' WHERE operation_id IN (?, ?)",
            (http_hold_id, mcp_hold_id))
        status, http_held = await http(port, "GET", "/api/v1/operations/" + http_hold_id, tok=tok)
        assert status == 200, http_held
        mcp_held = unpack(await server.call_tool("operation_get", {"operation_id": http_hold_id}))
        assert http_held["operation"]["loop"] == mcp_held["operation"]["loop"]
        assert any(row.get("endpoint") == "resume" for row in http_held["operation"]["loop"]["next_allowed_actions"])
        status, http_resumed = await http(port, "POST", f"/api/v1/operations/{http_hold_id}/resume", tok=tok)
        assert status == 200 and http_resumed["operation"]["status"] == "running", http_resumed
        mcp_resumed = unpack(await server.call_tool(
            "operation_resume", {"operation_id": mcp_hold_id, "confirm": True}))
        assert mcp_resumed["operation"]["status"] == "running"
        assert _scrub_loop(http_resumed["operation"]) == _scrub_loop(mcp_resumed["operation"])

        _hide_source(daemon.ops.db, "session", f"h1/{sid}")
        status, hidden = await http(port, "GET", "/api/v1/operations/" + http_id, tok=tok)
        assert status == 404, hidden
        assert hidden["error"] == {
            "code": "NOT_FOUND", "message": "Original resource sources are unavailable"}
        assert "loop" not in hidden and "operation" not in hidden
        assert http_id not in json.dumps(hidden) and sid not in json.dumps(hidden)
        with pytest.raises(ToolError, match="NOT_FOUND") as refused:
            await server.call_tool("operation_get", {"operation_id": http_id})
        mcp_text = str(refused.value)
        assert "Original resource sources are unavailable" in mcp_text
        assert "loop" not in mcp_text and http_id not in mcp_text and sid not in mcp_text
        assert sid in json.dumps(kept)

        status, workspace = await _submit(
            port, tok, {"action": "pm.workspace.create", "params": {"name": "A useful product"}}, "loop-parity-ws")
        assert status == 202, workspace
        await settle_operations(daemon.ops)
        created = daemon.ops.get(workspace["operation"]["operation_id"])
        assert created["status"] == "succeeded", created
        wid = created["result"]["workspace"]["workspace_id"]
        version = created["result"]["workspace"]["version"]
        thread = {"action": "pm.thread.create", "target": {"workspace_id": wid}, "params": {"title": "Hidden"},
                  "preconditions": {"expected_version": version}}
        narrow = token(daemon, actor, "observe")
        monkeypatch.setenv("BATC_API_TOKEN", narrow)
        await _refuse(server, port, narrow, thread, "loop-no-manage", "FORBIDDEN", 403,
                      admission_refused=True, db=daemon.ops.db)
        outsider = token(daemon, "loop-parity-outsider", "observe", "manage")
        monkeypatch.setenv("BATC_API_TOKEN", outsider)
        await _refuse(server, port, outsider, thread, "loop-non-member", "NOT_FOUND", 404,
                      admission_refused=True, db=daemon.ops.db)
        manual = {"action": "session.send", "target": {"host": "h1", "session_id": MANUAL},
                  "params": {"text": "Leave the manual session alone"}}
        monkeypatch.setenv("BATC_API_TOKEN", tok)
        await _refuse(server, port, tok, manual, "loop-manual", "MANUAL_READ_ONLY", 403,
                      admission_refused=False, db=daemon.ops.db)
        before = _operation_count(daemon.ops.db)
        changed = {**http_body, "params": {"text": "Different words for the same key"}}
        status, conflict = await _submit(port, tok, changed, "loop-http-send")
        assert status == 409 and conflict["error"]["code"] == "IDEMPOTENCY_CONFLICT"
        assert "operation" not in conflict and "admission_refused" not in conflict["error"]
        with pytest.raises(ToolError, match="IDEMPOTENCY_CONFLICT"):
            await _mcp_submit(server, {**mcp_body, "params": {"text": "Different MCP words"}}, "loop-mcp-send")
        assert _operation_count(daemon.ops.db) == before
    finally:
        await fleet.close()


async def test_work_start_loop_matches_across_http_and_mcp(loop_start, monkeypatch):
    w = loop_start
    daemon, ops, port = w["d"], w["d"].ops, w["port"]
    wid, work_id = w["workspace_id"], w["target"]["work_item_id"]
    tok = token(daemon, STARTER_OWNER.actor, *sorted(STARTER_OWNER.scopes))
    monkeypatch.setenv("BATC_TASK_URL", f"http://127.0.0.1:{port}/rpc")
    monkeypatch.setenv("BATC_API_TOKEN", tok)
    server, fleet = build_server(daemon.fleet.config, principal_only=True)
    try:
        http_env = await _start_envelope(w, key="loop-http-start")
        http_body = {key: http_env[key] for key in ("action", "target", "params", "preconditions")}
        status, http_created = await _submit(port, tok, http_body, http_env["idempotency_key"])
        assert status == 202 and http_created["created"] is True, http_created
        http_id = http_created["operation"]["operation_id"]
        http_attempt = ops.db.execute(
            "SELECT attempt_id FROM work_attempts WHERE start_operation_id=?", (http_id,)).fetchone()["attempt_id"]
        assert http_created["operation"]["loop"]["attempt_id"] == http_attempt
        status, http_cancelled = await http(port, "POST", f"/api/v1/operations/{http_id}/cancel", tok=tok)
        assert status == 200 and http_cancelled["operation"]["status"] == "cancelled", http_cancelled

        mcp_env = await _start_envelope(w, key="loop-mcp-start")
        mcp_created = await _mcp_submit(server, {
            key: mcp_env[key] for key in ("action", "target", "params", "preconditions")}, mcp_env["idempotency_key"])
        assert mcp_created["created"] is True
        mcp_id = mcp_created["operation"]["operation_id"]
        mcp_attempt = ops.db.execute(
            "SELECT attempt_id FROM work_attempts WHERE start_operation_id=?", (mcp_id,)).fetchone()["attempt_id"]
        assert mcp_created["operation"]["loop"]["attempt_id"] == mcp_attempt
        assert http_id != mcp_id
        assert _scrub_loop(http_created["operation"]) == _scrub_loop(mcp_created["operation"])
        mcp_cancelled = unpack(await server.call_tool(
            "operation_cancel", {"operation_id": mcp_id, "confirm": True}))
        assert mcp_cancelled["operation"]["status"] == "cancelled"
        await settle_operations(ops)
        assert ops.get(http_id)["status"] == "cancelled" and ops.get(mcp_id)["status"] == "cancelled"

        kept = await _same_operation(server, port, tok, http_id)
        await _same_operation(server, port, tok, mcp_id)
        assert kept["work_item_id"] == work_id and work_id in kept["dashboard_hash"]
        rows_after_start = _operation_count(ops.db)
        status, replayed = await _submit(port, tok, http_body, http_env["idempotency_key"])
        assert status == 200 and replayed["created"] is False
        assert replayed["operation"]["loop"]["operation_id"] == http_id
        mcp_replay = await _mcp_submit(server, {
            key: mcp_env[key] for key in ("action", "target", "params", "preconditions")}, mcp_env["idempotency_key"])
        assert mcp_replay["created"] is False and mcp_replay["operation"]["loop"]["operation_id"] == mcp_id
        assert _operation_count(ops.db) == rows_after_start
        assert _attempts_for(ops.db, http_id) == 1 and _attempts_for(ops.db, mcp_id) == 1

        for path in (
            f"/api/v1/pm/workspaces/{wid}/work/{work_id}/start-preview",
            f"/api/v1/pm/workspaces/{wid}/work-attempts/{http_attempt}",
            f"/api/v1/pm/workspaces/{wid}/work-attempts/{http_attempt}/result",
        ):
            method = "POST" if path.endswith("start-preview") else "GET"
            body = {"instruction": "Previewed via HTTP."} if method == "POST" else None
            status, document = await http(port, method, path, tok=tok, body=body)
            assert status == 200, (path, document)
            assert not _has_key(document, "loop")

        live_env = await _start_envelope(w, key="loop-live-start")
        status, live = await _submit(port, tok, {
            key: live_env[key] for key in ("action", "target", "params", "preconditions")}, live_env["idempotency_key"])
        assert status == 202 and live["created"] is True, live
        assert ops.get(live["operation"]["operation_id"])["status"] == "accepted"
        before_ops, before_attempts = _operation_count(ops.db), _attempt_count(ops.db)
        for key_name in ("loop-blocked-http", "loop-blocked-mcp"):
            blocked = await _start_envelope(w, key=key_name)
            body = {key: blocked[key] for key in ("action", "target", "params", "preconditions")}
            if key_name.endswith("http"):
                status, refused = await _submit(port, tok, body, blocked["idempotency_key"])
                assert status == 409 and refused["error"]["code"] == "PM_WORK_START_IN_PROGRESS", refused
                assert refused["error"]["admission_refused"] is True and "operation" not in refused
            else:
                with pytest.raises(ToolError, match="PM_WORK_START_IN_PROGRESS"):
                    await _mcp_submit(server, body, blocked["idempotency_key"])
        assert _operation_count(ops.db) == before_ops and _attempt_count(ops.db) == before_attempts

        _hide_source(ops.db, "work_item", work_id)
        status, hidden = await http(port, "GET", "/api/v1/operations/" + http_id, tok=tok)
        assert status == 404, hidden
        assert hidden["error"] == {
            "code": "NOT_FOUND", "message": "Original resource sources are unavailable"}
        assert "loop" not in hidden and "operation" not in hidden
        assert http_id not in json.dumps(hidden) and work_id not in json.dumps(hidden)
        with pytest.raises(ToolError, match="NOT_FOUND") as refused:
            await server.call_tool("operation_get", {"operation_id": http_id})
        mcp_text = str(refused.value)
        assert "Original resource sources are unavailable" in mcp_text
        assert "loop" not in mcp_text and http_id not in mcp_text and work_id not in mcp_text
        assert kept["work_item_id"] == work_id
    finally:
        await fleet.close()


async def test_child_session_withdrawn_from_another_row_stays_readable(loop_start, monkeypatch):
    """A proven package child copies the managed session from the handoff, not from its own fields."""
    w = loop_start
    daemon, ops, port = w["d"], w["d"].ops, w["port"]
    tok = token(daemon, STARTER_OWNER.actor, *sorted(STARTER_OWNER.scopes))
    monkeypatch.setenv("BATC_TASK_URL", f"http://127.0.0.1:{port}/rpc")
    monkeypatch.setenv("BATC_API_TOKEN", tok)
    server, fleet = build_server(daemon.fleet.config, principal_only=True)
    try:
        env = await _start_envelope(w, key="loop-child-session")
        body = {key: env[key] for key in ("action", "target", "params", "preconditions")}
        status, created = await _submit(port, tok, body, env["idempotency_key"])
        assert status == 202 and created["created"] is True, created
        parent_id = created["operation"]["operation_id"]
        done = await _advance_start(ops, parent_id)
        assert done["status"] == "succeeded", done
        child_id = done["result"]["children"]["package"]
        host, sid = done["result"]["session"]["host"], done["result"]["session"]["session_id"]
        assert isinstance(child_id, str) and child_id != parent_id
        assert sid not in json.dumps(ops.get(child_id))

        status, before = await http(port, "GET", "/api/v1/operations/" + child_id, tok=tok)
        assert status == 200, before
        before_mcp = unpack(await server.call_tool("operation_get", {"operation_id": child_id}))
        assert before["operation"]["loop"] == before_mcp["operation"]["loop"]
        kept = before["operation"]["loop"]
        assert kept["links"]["session_hash"] == f"#/session/{host}/{sid}"
        assert sid in json.dumps(kept)

        _hide_source(ops.db, "session", f"{host}/{sid}")
        status, hidden = await http(port, "GET", "/api/v1/operations/" + child_id, tok=tok)
        assert status == 200, hidden
        hidden_mcp = unpack(await server.call_tool("operation_get", {"operation_id": child_id}))
        assert hidden["operation"]["loop"] == hidden_mcp["operation"]["loop"]
        http_text, mcp_text = json.dumps(hidden), json.dumps(hidden_mcp)
        session_hash = f"#/session/{host}/{sid}"
        assert sid not in http_text and sid not in mcp_text
        assert session_hash not in http_text and session_hash not in mcp_text
        withdrawn = {"status": "unavailable", "reason": "source_withdrawn"}
        assert hidden["operation"]["loop"]["completeness"] == withdrawn
        assert hidden["operation"]["loop"]["links"]["session_hash"] is None
        assert sid in json.dumps(kept)
        assert ops.get(child_id)["status"] == "succeeded"
    finally:
        await fleet.close()


async def test_changed_child_receipt_stays_readable_as_attempt_unreadable(loop_start, monkeypatch):
    """A work.start whose child receipt no longer matches still returns the operation."""
    w = loop_start
    daemon, ops, port = w["d"], w["d"].ops, w["port"]
    tok = token(daemon, STARTER_OWNER.actor, *sorted(STARTER_OWNER.scopes))
    monkeypatch.setenv("BATC_TASK_URL", f"http://127.0.0.1:{port}/rpc")
    monkeypatch.setenv("BATC_API_TOKEN", tok)
    server, fleet = build_server(daemon.fleet.config, principal_only=True)
    try:
        env = await _start_envelope(w, key="loop-child-changed")
        body = {key: env[key] for key in ("action", "target", "params", "preconditions")}
        status, created = await _submit(port, tok, body, env["idempotency_key"])
        assert status == 202 and created["created"] is True, created
        parent_id = created["operation"]["operation_id"]
        done = await _advance_start(ops, parent_id)
        assert done["status"] == "succeeded", done
        child_id = done["result"]["children"]["package"]
        # The child link row is immutable. _child compares this operation's params
        # with the stored request (pm_work_start.py:1124-1127).
        child = ops.get(child_id)
        changed = dict(child["params"])
        changed["title"] = str(changed.get("title") or "package") + " receipt changed"
        ops.db.execute(
            "UPDATE operations SET params=? WHERE operation_id=?",
            (json.dumps(changed, ensure_ascii=False), child_id))

        status, got = await http(port, "GET", "/api/v1/operations/" + parent_id, tok=tok)
        assert status == 200, got
        via_mcp = unpack(await server.call_tool("operation_get", {"operation_id": parent_id}))
        status, cancelled = await http(port, "POST", f"/api/v1/operations/{parent_id}/cancel", tok=tok)
        assert status == 200, cancelled
        assert got["operation"]["loop"] == via_mcp["operation"]["loop"] == cancelled["operation"]["loop"]
        loop = got["operation"]["loop"]
        assert loop["completeness"] == {"status": "partial", "reason": "attempt_unreadable"}
        assert "attempt_stage" not in loop["state"]
        assert loop["result_read"]["result"]["name"]
        assert loop["next_allowed_actions"] == [] and loop["blocked_actions"] == []
        assert loop["links"]["session_hash"] is None
        assert loop["links"]["execution_operation_hash"] is None
        assert "PM_WORK_START_CHILD_CHANGED" not in json.dumps(loop)
    finally:
        await fleet.close()


async def test_first_start_of_an_unbound_work_keeps_the_work_hash(loop_start, monkeypatch):
    """A Work with no binding yet is visible on the create, the MCP submit, and the GET."""
    from bat_agent_connector import work_items

    w = loop_start
    daemon, ops, port = w["d"], w["d"].ops, w["port"]
    wid = w["workspace_id"]
    tok = token(daemon, STARTER_OWNER.actor, *sorted(STARTER_OWNER.scopes))
    monkeypatch.setenv("BATC_TASK_URL", f"http://127.0.0.1:{port}/rpc")
    monkeypatch.setenv("BATC_API_TOKEN", tok)
    server, fleet = build_server(daemon.fleet.config, principal_only=True)
    project_id = work_items._get_item(ops.db, w["target"]["work_item_id"])["project_id"]

    async def unbound_work(title):
        work_id = (await pkg_action(
            w, "work_item.create", {"project_id": project_id},
            {"title": title, "goal": "Authenticate members",
             "acceptance": "Valid credentials pass, invalid fail"},
        ))["work_item_id"]
        assert pm_evidence._binding(ops.db, wid, work_id) is None
        return work_id

    async def hold_due():
        return None

    try:
        http_work = await unbound_work("Unbound start over HTTP")
        mcp_work = await unbound_work("Unbound start over MCP")
        # Admission records the attempt. Hold the worker so this read is before the bind child.
        monkeypatch.setattr(ops, "run_due", hold_due)
        http_env = await _start_envelope(w, work_item_id=http_work, key="loop-unbound-http")
        mcp_env = await _start_envelope(w, work_item_id=mcp_work, key="loop-unbound-mcp")
        assert http_env["preconditions"]["expected_binding_id"] is None
        assert http_env["params"]["requirement_ids"]
        http_body = {key: http_env[key] for key in ("action", "target", "params", "preconditions")}
        mcp_body = {key: mcp_env[key] for key in ("action", "target", "params", "preconditions")}
        status, created = await _submit(port, tok, http_body, http_env["idempotency_key"])
        assert status == 202 and created["created"] is True, created
        parent_id = created["operation"]["operation_id"]
        submitted = await _mcp_submit(server, mcp_body, mcp_env["idempotency_key"])
        assert submitted["created"] is True
        status, got = await http(port, "GET", "/api/v1/operations/" + parent_id, tok=tok)
        assert status == 200, got
        assert ops.get(parent_id)["status"] == "accepted"
        assert pm_evidence._binding(ops.db, wid, http_work) is None
        assert pm_evidence._binding(ops.db, wid, mcp_work) is None
        attempt_id = ops.db.execute(
            "SELECT attempt_id FROM work_attempts WHERE start_operation_id=?", (parent_id,)).fetchone()["attempt_id"]
        status, result = await http(
            port, "GET",
            f"/api/v1/pm/workspaces/{wid}/work-attempts/{attempt_id}/result?wait_ms=0", tok=tok)
        assert status == 200, result
        work_hash = result["links"]["work_item_hash"]
        mcp_id = submitted["operation"]["operation_id"]
        mcp_attempt = ops.db.execute(
            "SELECT attempt_id FROM work_attempts WHERE start_operation_id=?", (mcp_id,)).fetchone()["attempt_id"]
        status, mcp_result = await http(
            port, "GET",
            f"/api/v1/pm/workspaces/{wid}/work-attempts/{mcp_attempt}/result?wait_ms=0", tok=tok)
        assert status == 200, mcp_result
        for reply, work_id, shown in (
            (created["operation"]["loop"], http_work, work_hash),
            (got["operation"]["loop"], http_work, work_hash),
            (submitted["operation"]["loop"], mcp_work, mcp_result["links"]["work_item_hash"]),
        ):
            assert reply["work_item_id"] == work_id, reply
            assert reply["links"]["work_item_hash"] == shown == f"#/pm/{wid}/board?select={work_id}"
    finally:
        await fleet.close()
