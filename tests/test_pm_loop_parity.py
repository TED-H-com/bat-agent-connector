"""Field rules and public exits for the E10 operation loop projection.

Parity tests that compare HTTP and MCP stay on the public routes. Field-rule
tests call ``attach`` directly. Exit tests use the HTTP app and the RPC door.
"""
from __future__ import annotations

import json
import time
import uuid

from bat_agent_connector import pm_discussions, pm_evidence, pm_source_lineage, pm_work_start, pm_workspace
from bat_agent_connector.api_auth import Principal
from bat_agent_connector.operations import OperationService
from bat_agent_connector.pm_delivery_lineage import ACTIONS as DELIVERY_ACTIONS
from bat_agent_connector.pm_operation_loop import LOOP_ACTIONS, attach
from bat_agent_connector.task_journal import Journal
from tests.operation_helpers import settle_operations
from tests.test_api_v1 import daemon as daemon_fixture
from tests.test_api_v1 import http, token
from tests.test_api_v1 import served as served_fixture

daemon, served = daemon_fixture, served_fixture

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
    intent = {"workspace_id": WS, "work_item_id": work_item_id, "package_plan": {}}
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
    assert withheld["loop"]["work_item_id"] is None
    assert withheld["loop"]["completeness"] == {"status": "unavailable", "reason": "source_withdrawn"}
    assert withheld["loop"]["operation_id"] == "op_pkg_hidden"

    ops.db.execute(
        "INSERT INTO pm_source_lineage VALUES('work_item', ?, 'op_missing_root', 'op_missing_root')", (WI,))
    again = await attach(ops, OWNER, visible)
    assert again["loop"]["work_item_id"] is None
    assert again["loop"]["completeness"]["reason"] == "source_withdrawn"
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
    hidden_loop = (await attach(ops, OWNER, hidden_attempt))["loop"]
    assert hidden_loop["work_item_id"] is None
    assert hidden_loop["completeness"] == {"status": "unavailable", "reason": "source_withdrawn"}

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
    assert outsider_loop["completeness"]["reason"] == "source_withdrawn"

    thread_id = "pmt_" + "ab" * 16
    missing_thread = _op(ops, action="pm.message.append", operation_id="op_msg_missing",
                         target={"workspace_id": WS, "thread_id": thread_id})
    missing_loop = (await attach(ops, OWNER, missing_thread))["loop"]
    assert missing_loop["dashboard_hash"] == "#/op/op_msg_missing"
    assert thread_id not in json.dumps(missing_loop)
    assert missing_loop["completeness"] == {"status": "unavailable", "reason": "source_withdrawn"}
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
