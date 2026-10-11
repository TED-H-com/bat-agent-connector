"""Flow and attempts retain their original authority, scope and quality boundary."""
from __future__ import annotations

import json
import types
import uuid

import pytest

from bat_agent_connector import controller_authority, pm_evidence, pm_kanban, pm_workspace, work_items
from bat_agent_connector.api_auth import Principal
from bat_agent_connector.operations import ActionDef, OperationError
from tests.test_pm_discussions import OUTSIDER, OWNER, READER, member, settle, workspace
from tests.test_pm_evidence import (
    act,
    bind_work,
    capture,
    item,
    link,
    prepared,
    project,
    session,
    source_operation,
    task,
)
from tests.test_pm_evidence import ops as ops  # Shared central-journal fixture, no host/provider activity.

APPROVER = Principal(OWNER.actor, OWNER.scopes | {"approve"})


def card(ops, target, who=READER):
    return pm_kanban.get_card(ops, who, **target)["card"]


def board(ops, target, **kwargs):
    return pm_kanban.get_board(ops, READER, target["workspace_id"], **kwargs)


async def update(ops, target, **params):
    work = work_items._get_item(ops.db, target["work_item_id"])
    return await act(ops, "work_item.update", {"work_item_id": work["work_item_id"]}, params,
                     {"expected_version": work["version"]})


async def decide(ops, target, action="approve"):
    work = work_items._get_item(ops.db, target["work_item_id"])
    return await act(ops, "work_item." + action, {"work_item_id": work["work_item_id"]},
                     {"note": "PRIVATE NOTE"}, {"expected_fingerprint": work_items.fingerprint(work)}, who=APPROVER)


def clock(ops, target, monkeypatch, times, now):
    rows = ops.db.execute("SELECT seq FROM api_events WHERE resource_type='work_item' AND resource_id=? AND kind IN (?,?,?,?,?) ORDER BY seq",
                          (target["work_item_id"], *pm_kanban._FLOW_KINDS)).fetchall()
    assert len(rows) == len(times)
    with ops.journal.tx():
        ops.db.executemany("UPDATE api_events SET created_at=? WHERE seq=?", [(t, r[0]) for r, t in zip(rows, times, strict=True)])
    monkeypatch.setattr(pm_kanban, "time", types.SimpleNamespace(time=lambda: now))


def commands(ops, count=3):
    source = task(ops)
    tid = source["task_id"]
    ops.journal.change(tid, "dispatching", fields={"session_id": "same-session"})
    values = []
    for index in range(count):
        command, _ = ops.journal.command(tid, "send", "same-session", {"prompt": "PRIVATE PROMPT"}, uuid.uuid4().hex)
        ops.journal.command_status(command["command_id"], "settled" if index < count - 1 else "accepted")
        values.append(command)
    return source, values


async def execution(ops, *, accepted=True, matching=True, action="session.start"):
    async def run(ctx):
        return ctx.effect("send", lambda: {"accepted": accepted, "message_id": "message" if matching else "different"},
                          request={"message_id": "message", "prompt": "PRIVATE PAYLOAD"})
    if action not in ops.actions:
        ops.register(ActionDef(action, "manage", "Journal-only mock execution", run))
    op, _ = ops.create(OWNER, action=action, target={}, params={}, idempotency_key=uuid.uuid4().hex)
    return await settle(ops, op)


async def test_only_explicit_bindings_form_cards_and_read_is_nonmutating(ops):
    target = await prepared(ops)
    pid = await project(ops, "Same repository without association")
    await item(ops, pid)
    actions, changes, head = set(ops.actions), ops.db.total_changes, ops.journal.api_head()
    out = board(ops, target)
    assert [c["work_item_id"] for c in out["cards"]] == [target["work_item_id"]]
    assert out["journal_cursor"] == head
    assert set(ops.actions) == actions and ops.db.total_changes == changes
    assert out["cards"][0]["column"] == "backlog" and out["wip"]["count"] == 0
    assert out["capabilities"]["read_only"] and not out["capabilities"]["dispatch"]
    assert out["capabilities"]["wip_limit"] == {"status": "not_configured", "value": None}
    assert out["capabilities"]["resource_capacity"]["value"] is None


async def test_member_observe_and_explicit_work_scope_are_checked_each_read(ops):
    target = await prepared(ops)
    assert card(ops, target)["work_item_id"] == target["work_item_id"]
    for who in (OUTSIDER, Principal(READER.actor, frozenset({"manage"}))):
        for read in (lambda: pm_kanban.get_board(ops, who, target["workspace_id"]), lambda: card(ops, target, who)):
            with pytest.raises(OperationError) as caught:
                read()
            assert caught.value.status in {403, 404}
    await member(ops, pm_workspace.get_workspace(ops.db, OWNER, target["workspace_id"]), READER.actor, None)
    with pytest.raises(OperationError, match="NOT_FOUND"):
        card(ops, target)
    other = await workspace(ops)
    with pytest.raises(OperationError, match="PM_WORK_NOT_FOUND"):
        pm_kanban.get_card(ops, OWNER, other["workspace_id"], target["work_item_id"])


@pytest.mark.parametrize("params", [{"after": True}, {"after": -1}, {"limit": 0}, {"limit": 101}, {"limit": "10"}, {"project_id": "name"}])
async def test_pagination_and_filters_are_strict(ops, params):
    target = await prepared(ops)
    with pytest.raises(OperationError, match="INVALID_PARAMS"):
        board(ops, target, **params)


async def test_backlog_direct_completion_claim_has_no_invented_start_or_quality(ops):
    target = await prepared(ops)
    await update(ops, target, state="done")
    c = card(ops, target)
    assert c["column"] == "review" and c["flow"]["wip"]
    assert c["flow"]["age_seconds"] is None and c["flow"]["started_at"] is None
    assert c["quality"]["dod"] == c["quality"]["acceptance_criteria"] == "unassessed"
    await decide(ops, target)
    c = card(ops, target)
    assert c["column"] == "accepted" and not c["flow"]["wip"]
    assert c["flow"]["cycle_seconds"] is None and c["quality"]["dod"] == "unassessed"
    assert not c["quality"]["management_approval_is_quality"]


async def test_waiting_age_includes_wait_and_updates_never_reset_state_entry(ops, monkeypatch):
    target = await prepared(ops)
    await update(ops, target, state="doing")
    await update(ops, target, state="waiting")
    await update(ops, target, title="Waiting with a clearer title")
    clock(ops, target, monkeypatch, [10, 20, 30, 40], 60)
    c = card(ops, target)
    assert c["column"] == "waiting"
    assert c["flow"]["started_at"] == 20 and c["flow"]["age_seconds"] == 40
    assert c["flow"]["state_since"] == c["flow"]["waiting_since"] == 30
    assert c["flow"]["waiting_seconds"] == c["flow"]["total_waiting_seconds"] == 30
    assert c["flow"]["blocked"] == "unknown" and c["flow"]["blocked_reason"] is None


async def test_started_work_returned_to_backlog_stays_wip_and_claim_does_not_close(ops, monkeypatch):
    target = await prepared(ops)
    await update(ops, target, state="doing")
    await update(ops, target, state="waiting")
    await update(ops, target, state="todo")
    clock(ops, target, monkeypatch, [10, 20, 30, 50], 80)
    c = card(ops, target)
    assert c["column"] == "backlog" and c["flow"]["wip"]
    assert c["flow"]["age_seconds"] == 60 and c["flow"]["total_waiting_seconds"] == 20
    await update(ops, target, state="done")
    clock(ops, target, monkeypatch, [10, 20, 30, 50, 70], 80)
    assert card(ops, target)["flow"]["closed_at"] is None


async def test_content_approval_continue_and_content_edit_have_distinct_cycles(ops, monkeypatch):
    target = await prepared(ops)
    await update(ops, target, state="doing")
    await decide(ops, target)
    clock(ops, target, monkeypatch, [10, 20, 40], 50)
    assert card(ops, target)["flow"]["cycle_seconds"] == 20
    await decide(ops, target, "continue")
    clock(ops, target, monkeypatch, [10, 20, 40, 60], 80)
    c = card(ops, target)
    assert c["flow"]["reopened"] == 1 and c["flow"]["age_seconds"] == 20
    await decide(ops, target)
    await update(ops, target, title="Changed accepted content")
    clock(ops, target, monkeypatch, [10, 20, 40, 60, 90, 100], 120)
    c = card(ops, target)
    assert c["column"] == "review" and c["flow"]["reopened"] == 2
    assert c["flow"]["started_at"] == 100 and c["flow"]["age_seconds"] == 20
    assert c["flow"]["closed_at"] is None
    assert "PRIVATE NOTE" not in json.dumps(c)


async def test_unchecking_approved_steps_is_one_reopening_not_two_events(ops):
    target = await prepared(ops)
    await update(ops, target, state="doing", steps=[{"text": "Implement", "done": True}])
    await decide(ops, target)
    await update(ops, target, steps=[{"text": "Implement", "done": False}])
    c = card(ops, target)
    assert c["column"] == "in_progress" and c["flow"]["reopened"] == 1


async def test_claim_rejection_keeps_original_age_and_direct_claim_can_start_on_continue(ops, monkeypatch):
    target = await prepared(ops)
    await update(ops, target, state="done")
    await decide(ops, target, "continue")
    clock(ops, target, monkeypatch, [10, 20, 30], 50)
    c = card(ops, target)
    assert c["flow"]["reopened"] == 1 and c["flow"]["started_at"] == 30
    await update(ops, target, state="done")
    await decide(ops, target, "continue")
    clock(ops, target, monkeypatch, [10, 20, 30, 60, 70], 90)
    c = card(ops, target)
    assert c["flow"]["reopened"] == 2 and c["flow"]["started_at"] == 30
    assert c["flow"]["age_seconds"] == 60


async def test_checked_steps_are_a_review_claim_not_a_start_time(ops):
    target = await prepared(ops)
    await update(ops, target, steps=[{"text": "Claimed step", "done": True}])
    c = card(ops, target)
    assert c["column"] == "review" and c["flow"]["wip"]
    assert c["flow"]["started_at"] is None and c["quality"]["dod"] == "unassessed"


async def test_pruned_complete_intermediate_cycle_is_detected_even_if_remaining_states_match(ops, monkeypatch):
    target = await prepared(ops)
    await update(ops, target, state="doing")
    await update(ops, target, state="waiting")
    await update(ops, target, state="doing")
    clock(ops, target, monkeypatch, [10, 20, 30, 40], 60)
    ops.db.execute("DELETE FROM api_events WHERE resource_id=? AND created_at IN (30,40)", (target["work_item_id"],))
    c = card(ops, target)
    assert c["flow"]["history"] == "missing_history" and c["flow"]["age_seconds"] is None


@pytest.mark.parametrize("failure", ["missing", "gap", "clock", "future", "truncated", "inaccessible"])
async def test_incomplete_or_nonmonotonic_history_never_fabricates_age(ops, monkeypatch, failure):
    target = await prepared(ops)
    await update(ops, target, state="doing")
    await update(ops, target, state="waiting")
    clock(ops, target, monkeypatch, [10, 20, 30], 50)
    wid = target["work_item_id"]
    if failure in {"missing", "gap"}:
        ops.db.execute("DELETE FROM api_events WHERE resource_id=? AND kind=? AND (?='missing' OR json_extract(body,'$.to')='doing')",
                       (wid, "work_item.created" if failure == "missing" else "work_item.state", failure))
    elif failure in {"clock", "future"}:
        clock(ops, target, monkeypatch, [10, 40, 30] if failure == "clock" else [10, 20, 60], 50)
    elif failure == "truncated":
        monkeypatch.setattr(pm_kanban, "MAX_HISTORY", 2)
    else:
        row = ops.db.execute("SELECT body FROM api_events WHERE resource_id=? AND kind='work_item.state' ORDER BY seq LIMIT 1", (wid,)).fetchone()
        oid = json.loads(row[0])["operation_id"]
        ops.db.execute("UPDATE operations SET action='pm.private' WHERE operation_id=?", (oid,))
    c = card(ops, target)
    assert c["flow"]["wip"] and c["flow"]["history"] != "available_central_events"
    assert c["flow"]["started_at"] is c["flow"]["waiting_seconds"] is c["flow"]["age_seconds"] is None


async def test_archiving_work_or_project_is_not_cancellation(ops):
    target = await prepared(ops)
    await update(ops, target, state="doing")
    await update(ops, target, archived=True)
    out = board(ops, target)
    assert out["cards"][0]["column"] == "archived" and out["wip"]["count"] == 0
    assert out["cards"][0]["flow"]["cancelled"] == "unknown" and out["wip"]["archived_excluded"] == 1
    await update(ops, target, archived=False)
    pid = card(ops, target)["work"]["project_id"]
    p = work_items._get_project(ops.db, pid)
    await act(ops, "project.update", {"project_id": pid}, {"archived": True}, {"expected_version": p["version"]})
    assert card(ops, target)["project_archived"] and board(ops, target)["wip"]["count"] == 0


async def test_three_commands_one_task_one_session_produce_one_card_and_three_attempts(ops):
    target = await prepared(ops)
    source, sends = commands(ops)
    await link(ops, target, "task", source["task_id"])
    await update(ops, target, state="doing")
    out = board(ops, target)
    assert len(out["cards"]) == 1 and out["wip"]["count"] == 1
    execution = out["cards"][0]["execution"]
    assert len(execution["tasks"]) == 1 and execution["accepted_attempts"] == 3
    assert {a["attempt_id"] for a in execution["attempts"]} == {s["command_id"] for s in sends}
    assert all(a["completion"] == "unassessed" for a in execution["attempts"])
    assert "PRIVATE" not in json.dumps(out)


async def test_unknown_session_stream_does_not_erase_saved_verification_or_add_attempts(ops):
    target = await prepared(ops)
    source = task(ops)
    await link(ops, target, "task", source["task_id"])
    await link(ops, target, "session", session(ops))
    ops.journal.record_observed_verification(source["task_id"], {"candidate_commit": "1" * 40, "tree_hash": "2" * 40,
        "command": "pytest", "exit_code": 0, "log_ref": "private-log", "output_sha256": "3" * 64, "source": "observed_runner"})
    await capture(ops, target)
    c = card(ops, target)
    assert c["execution"]["attempts"] == [] and c["execution"]["sessions"][0]["streaming"] is None
    proof = next(e for e in c["latest_capture"]["evidence"] if e["source_kind"] == "observed_verification")
    assert proof["assessment"]["verification"] == "passed" and c["quality"]["dod"] == "unassessed"
    assert "/private/path" not in json.dumps(c) and "private-log" not in json.dumps(c)


async def test_accepted_send_remains_accepted_if_its_outer_operation_is_uncertain(ops):
    target = await prepared(ops)
    op = await execution(ops)
    await link(ops, target, "operation", op["operation_id"])
    ops.db.execute("UPDATE operations SET status='needs_attention' WHERE operation_id=?", (op["operation_id"],))
    e = card(ops, target)["execution"]
    assert e["accepted_attempts"] == 1 and e["attempts"][0]["status"] == "needs_attention"
    assert e["attempts"][0]["completion"] == "unassessed"


async def test_wrapper_operation_and_task_command_are_one_attempt_despite_retries(ops):
    target = await prepared(ops)
    source, sends = commands(ops, 1)
    await link(ops, target, "task", source["task_id"])
    op = await source_operation(ops, action="session.send")
    ops.db.execute("UPDATE operations SET external_refs=?,attempts=9 WHERE operation_id=?",
                   (json.dumps({"task_id": source["task_id"], "command_id": sends[0]["command_id"]}), op["operation_id"]))
    await link(ops, target, "operation", op["operation_id"])
    e = card(ops, target)["execution"]
    assert len(e["attempts"]) == e["accepted_attempts"] == 1
    assert e["operations"][0]["status"] == "succeeded" and "attempts" not in e["operations"][0]


@pytest.mark.parametrize("accepted,matching,expected", [(True, True, 1), (False, True, 0), (True, False, 0)])
async def test_operation_success_requires_matching_send_receipt_to_count_acceptance(ops, accepted, matching, expected):
    target = await prepared(ops)
    op = await execution(ops, accepted=accepted, matching=matching)
    await link(ops, target, "operation", op["operation_id"])
    e = card(ops, target)["execution"]
    assert e["accepted_attempts"] == expected and len(e["attempts"]) == 1
    assert e["attempts"][0]["status"] == "succeeded" and e["attempts"][0]["completion"] == "unassessed"


async def test_successful_merge_like_payload_never_becomes_an_execution_attempt(ops):
    target = await prepared(ops)
    op = await source_operation(ops)
    await link(ops, target, "operation", op["operation_id"])
    c = card(ops, target)
    assert c["execution"]["operations"][0]["status"] == "succeeded"
    assert c["execution"]["attempts"] == [] and c["quality"]["dod"] == "unassessed"


async def test_command_intent_and_terminal_task_do_not_imply_acceptance_or_dod(ops):
    target = await prepared(ops)
    source = task(ops)
    ops.journal.change(source["task_id"], "dispatching")
    command, _ = ops.journal.command(source["task_id"], "send", None, {}, uuid.uuid4().hex)
    ops.journal.command_status(command["command_id"], "uncertain")
    await link(ops, target, "task", source["task_id"])
    ops.db.execute("UPDATE tasks SET state='done' WHERE task_id=?", (source["task_id"],))
    c = card(ops, target)
    assert c["execution"]["accepted_attempts"] == 0 and c["execution"]["attempts"][0]["accepted"] is None
    assert c["execution"]["tasks"][0]["state"] == "done"
    assert c["column"] == "backlog" and c["quality"]["dod"] == "unassessed"


async def test_current_source_access_is_rechecked_for_attempts_and_saved_capture(ops):
    target = await prepared(ops)
    source, sends = commands(ops, 1)
    op = await source_operation(ops)
    await link(ops, target, "task", source["task_id"])
    await link(ops, target, "operation", op["operation_id"])
    await capture(ops, target)
    before = card(ops, target)
    assert before["execution"]["accepted_attempts"] == 1
    private = await workspace(ops, OUTSIDER)
    authority = controller_authority.binding(ops.db, OUTSIDER, pm_workspace_id=private["workspace_id"])
    ops.db.execute("UPDATE commands SET controller_authority=? WHERE command_id=?", (json.dumps(authority), sends[0]["command_id"]))
    ops.db.execute("UPDATE operations SET action='pm.private',target=? WHERE operation_id=?",
                   (json.dumps({"workspace_id": private["workspace_id"]}), op["operation_id"]))
    c = card(ops, target)
    assert c["execution"]["accepted_attempts"] == 0 and c["execution"]["coverage"]["unavailable_sources"]
    assert c["latest_capture"]["coverage"]["inaccessible_sources"]
    serialized = json.dumps(c)
    assert sends[0]["command_id"] not in serialized and op["operation_id"] not in serialized
    assert private["workspace_id"] not in serialized and "PRIVATE" not in serialized


async def test_private_task_link_does_not_reveal_task_session_or_attempt_ids(ops):
    target = await prepared(ops)
    private = await workspace(ops, OUTSIDER)
    authority = controller_authority.binding(ops.db, OUTSIDER, pm_workspace_id=private["workspace_id"])
    with controller_authority.use(authority, ops.journal):
        source, sends = commands(ops, 1)
    await link(ops, target, "task", source["task_id"])
    serialized = json.dumps(card(ops, target))
    assert source["task_id"] not in serialized and sends[0]["command_id"] not in serialized
    assert "same-session" not in serialized


async def test_rebinding_never_rewrites_old_capture_or_duplicates_card(ops):
    target = await prepared(ops)
    await capture(ops, target)
    first = card(ops, target)
    await update(ops, target, title="New content")
    await bind_work(ops, target)
    c = card(ops, target)
    assert c["latest_capture"] == first["latest_capture"]
    assert not c["capture_matches_binding"] and not c["capture_matches_work_version"]
    assert c["binding"]["previous_binding_id"] == first["binding"]["binding_id"]
    assert len(board(ops, target)["cards"]) == 1


async def test_removed_links_do_not_reappear_as_current_attempts(ops):
    target = await prepared(ops)
    source, _ = commands(ops, 1)
    await link(ops, target, "task", source["task_id"])
    await capture(ops, target)
    await link(ops, target, "task", source["task_id"], remove=True)
    c = card(ops, target)
    assert c["execution"]["attempts"] == [] and c["latest_capture"]["evidence"]
    assert c["execution"]["coverage"]["removed_links"] == "not_scanned"


async def test_parent_and_creator_are_not_dependencies_or_assignees(ops):
    target = await prepared(ops)
    first = card(ops, target)
    pid = first["work"]["project_id"]
    child = (await act(ops, "work_item.create", {"project_id": pid}, {"title": "Child", "parent_id": target["work_item_id"]}))["work_item_id"]
    child_target = {**target, "work_item_id": child}
    await bind_work(ops, child_target)
    c = card(ops, child_target)
    assert c["dependencies"]["status"] == "unknown" and c["dependencies"]["items"] == []
    assert c["assignment"]["actor"] is None


async def test_pagination_counts_only_its_page_and_limits_attempt_source_coverage(ops, monkeypatch):
    target = await prepared(ops)
    pid = card(ops, target)["work"]["project_id"]
    second = {**target, "work_item_id": await item(ops, pid)}
    await bind_work(ops, second)
    await update(ops, target, state="doing")
    await update(ops, second, state="doing")
    page1 = board(ops, target, limit=1)
    page2 = board(ops, target, limit=1, after=page1["next_cursor"])
    assert page1["wip"]["count"] == page2["wip"]["count"] == 1
    assert page1["wip"]["scope"] == page2["wip"]["scope"] == "returned_page"
    assert page2["next_cursor"] is None
    assert page1["cards"][0]["work_item_id"] != page2["cards"][0]["work_item_id"]
    source, _ = commands(ops, 3)
    await link(ops, target, "task", source["task_id"])
    monkeypatch.setattr(pm_kanban, "MAX_ATTEMPTS", 2)
    c = card(ops, target)
    assert c["execution"]["accepted_attempts"] == 2 and c["execution"]["coverage"]["truncated"]


async def test_utf8_response_bound_continues_without_skipping_the_omitted_work(ops, monkeypatch):
    target = await prepared(ops)
    await update(ops, target, title="繁體中文" * 25)
    pid = card(ops, target)["work"]["project_id"]
    second = {**target, "work_item_id": await item(ops, pid)}
    await bind_work(ops, second)
    one = board(ops, target, limit=1)
    bound = len(pm_evidence._json(one).encode()) + 30
    monkeypatch.setattr(pm_kanban, "MAX_BOARD_BYTES", bound)
    first = board(ops, target)
    assert len(pm_evidence._json(first).encode()) <= bound
    assert len(first["cards"]) == 1 and first["coverage"]["byte_limited"]
    next_page = board(ops, target, after=first["next_cursor"])
    assert next_page["cards"][0]["work_item_id"] == second["work_item_id"]
    assert next_page["next_cursor"] is None
