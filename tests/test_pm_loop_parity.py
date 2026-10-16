"""Field rules for the E10 operation loop projection.

Parity tests that compare HTTP and MCP stay on the public routes. These tests
call ``attach`` only for the projection's own field rules.
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
