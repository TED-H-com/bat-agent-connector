"""The actual proposal/adoption/start chain uses disk Git and mocked BAT only."""
from __future__ import annotations

import asyncio
import copy
import sqlite3
import time
import uuid
from dataclasses import replace

import pytest

from bat_agent_connector import pm_work_adopt_and_start as compound
from bat_agent_connector import work_items
from bat_agent_connector.api_auth import Principal
from bat_agent_connector.errors import InvokeTimeout
from bat_agent_connector.operations import OperationError
from tests import test_pm_conversation_proposals as conversation
from tests import test_pm_work_start as starting
from tests.operation_helpers import settle_operations
from tests.test_api_v1 import http, token
from tests.test_checkpoints import snapshot
from tests.test_pm_work_attempt_result import _call_mcp_tool

world = starting.world
pm = starting.pm
ready = starting.ready
start_ready = starting.start_ready
proposal_world = conversation.proposal_world
OWNER = starting.STARTER_OWNER


@pytest.fixture
async def selected(start_ready, proposal_world):
    w = start_ready
    ops = w["d"].ops
    compound.install(ops)
    before = ops.db.execute("PRAGMA user_version").fetchone()[0]
    compound.install(ops)
    assert ops.db.execute("PRAGMA user_version").fetchone()[0] == before
    proposal = await conversation.proposed(w)
    env = conversation.adoption_request(w, proposal)
    start = await starting._start_envelope(w, requirement_ids=["member.login"])
    wi = start["target"]["work_item_id"]
    env["action"] = compound.ACTION
    env["params"]["works"] = [{"work_item_id": wi, **start["params"]}]
    env["preconditions"]["works"] = [{"work_item_id": wi,
        **{key: value for key, value in start["preconditions"].items() if key != "expected_scope_revision"}}]
    w["compound_request"] = env
    return w


def counts(ops):
    return tuple(ops.db.execute("SELECT COUNT(*) FROM " + table).fetchone()[0] for table in (
        "operations", "work_attempts", "pm_work_adopt_and_start_children", "pm_plan_adoptions"))


async def finish(ops, identity):
    for _ in range(30):
        ops.db.execute("UPDATE operations SET next_run_at=0 WHERE status='waiting_external'")
        await settle_operations(ops)
        result = ops.get(identity)
        if result["status"] in {"succeeded", "failed", "cancelled", "needs_attention"}:
            break
    assert result["error_code"] != "INTERNAL", result
    return result


async def test_admission_allocates_no_attempt_or_child_and_replay_keeps_fixed_selection(selected):
    ops, env = selected["d"].ops, selected["compound_request"]
    before = counts(ops)
    op, fresh = ops.create(OWNER, **env)
    assert fresh
    assert counts(ops) == (before[0] + 1, *before[1:])
    saved = compound._intent(op)
    assert "expected_scope_revision" not in saved["works"][0]["preconditions"]
    replay, fresh = ops.create(OWNER, **env)
    assert not fresh and replay["operation_id"] == op["operation_id"]
    modified = copy.deepcopy(env)
    modified["params"]["works"][0]["instruction"] += " Another change."
    with pytest.raises(OperationError, match="different request"):
        ops.create(OWNER, **modified)
    ops.cancel(OWNER, op["operation_id"])


async def test_real_adoption_then_work_start_retains_receipts_and_never_repeats(selected, mock):
    ops, env = selected["d"].ops, selected["compound_request"]
    before = snapshot(selected["human"])
    op, _ = ops.create(OWNER, **env)
    done = await finish(ops, op["operation_id"])
    assert done["status"] == "succeeded", done
    assert done["result"]["stage"] == "started"
    assert done["result"]["adopted"] is True
    assert done["result"]["summary"]["started_count"] == 1
    row = done["result"]["works"][0]
    child = ops.get(row["start_operation_id"])
    assert child["action"] == "work.start"
    assert child["preconditions"] == {**compound._intent(done)["works"][0]["preconditions"],
        "expected_scope_revision": done["result"]["adoption"]["scope_revision"]}
    assert row["attempt_id"] == "att_" + child["operation_id"][3:]
    assert mock.channels().count("claude:start-session") == 1
    replay, fresh = ops.create(OWNER, **env)
    assert not fresh and replay["operation_id"] == op["operation_id"]
    await settle_operations(ops)
    assert mock.channels().count("claude:start-session") == 1
    assert snapshot(selected["human"]) == before
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        ops.db.execute("DELETE FROM pm_work_adopt_and_start_children WHERE parent_id=?", (op["operation_id"],))


@pytest.mark.parametrize("phase", ["package", "handoff"])
async def test_parent_waits_for_inflight_external_step_without_manual_resume(selected, mock, monkeypatch, phase):
    ops = selected["d"].ops
    entered, release = asyncio.Event(), asyncio.Event()
    if phase == "package":
        original = compound.pm_work_packages._read_sources

        async def hold(*args, **kwargs):
            entered.set()
            await release.wait()
            return await original(*args, **kwargs)

        monkeypatch.setattr(compound.pm_work_packages, "_read_sources", hold)
    else:
        client = selected["d"].fleet.client("h1")
        original = client.invoke

        async def hold(name, *args, **kwargs):
            if name == "claude:start-session":
                entered.set()
                await release.wait()
            return await original(name, *args, **kwargs)

        monkeypatch.setattr(client, "invoke", hold)

    async def until(predicate):
        async def poll():
            while not predicate():
                await asyncio.sleep(0.03)
        await asyncio.wait_for(poll(), 60)

    # Adopt first, then exercise the real concurrent worker at the specific
    # start boundary. Adoption's disk Git work is not part of the barrier.
    identity = await hold_after_adoption(selected)
    ops.wake(identity)
    worker = asyncio.create_task(ops.loop(0.03))
    try:
        await asyncio.wait_for(entered.wait(), 60)
        child = compound._child(ops, ops._row(identity), "work:0")
        assert compound._uncertain(ops, child), "The in-flight external effect must remain fenced"
        attempts = ops.get(identity)["attempts"]
        await until(lambda: ops.get(identity)["status"] == "needs_attention"
            or ops.get(identity)["attempts"] > attempts and ops.get(identity)["status"] != "running")
        waiting = ops.get(identity)
        assert waiting["status"] == "waiting_external", waiting
        assert waiting["result"]["stage"] == "starting"
        assert waiting["result"]["summary"]["unknown_count"] == 0
        release.set()
        await until(lambda: ops.get(identity)["status"] in {"succeeded", "failed", "needs_attention"})
        done = ops.get(identity)
        assert done["status"] == "succeeded", done
        assert done["result"]["summary"]["started_count"] == 1
        assert mock.channels().count("claude:start-session") == 1
        assert ops.db.execute("SELECT count(*) FROM work_attempts").fetchone()[0] == 1
    finally:
        release.set()
        worker.cancel()
        await asyncio.gather(worker, return_exceptions=True)
        await settle_operations(ops)


@pytest.mark.parametrize("change,code", [
    ("duplicate", "INVALID_PARAMS"), ("empty", "INVALID_PARAMS"), ("too_many", "INVALID_PARAMS"),
    ("dependency", "PM_WORK_START_PREREQUISITES"), ("unconfirmed", "PM_WORK_START_NOT_READY"),
    ("unknown_requirement", "INVALID_PARAMS"), ("work_version", "WORK_ITEM_CHANGED"),
    ("proposal_digest", "PM_CONVERSATION_CHANGED"), ("scope_supplied", "PRECONDITION_REQUIRED"),
])
async def test_rejected_selection_allocates_nothing(selected, mock, change, code):
    ops, env = selected["d"].ops, copy.deepcopy(selected["compound_request"])
    work, pre = env["params"]["works"][0], env["preconditions"]["works"][0]
    if change == "duplicate":
        env["params"]["works"] *= 2
        env["preconditions"]["works"] *= 2
    elif change == "empty":
        env["params"]["works"] = []
    elif change == "too_many":
        env["params"]["works"] *= 11
    elif change == "dependency":
        work["requirement_ids"] = ["member.recovery"]
    elif change == "unconfirmed":
        next(row for row in env["params"]["review"]["rows"] if row["key"] == "login")["status"] = "agent_assumption"
    elif change == "unknown_requirement":
        work["requirement_ids"] = ["not.reviewed"]
    elif change == "work_version":
        pre["expected_work_version"] += 1
    elif change == "proposal_digest":
        env["preconditions"]["expected_proposal_digest"] = "0" * 64
    elif change == "scope_supplied":
        pre["expected_scope_revision"] = "pmscope_" + "0" * 32
    env["idempotency_key"] = uuid.uuid4().hex
    before = counts(ops), len(mock.invokes)
    with pytest.raises(OperationError) as error:
        ops.create(OWNER, **env)
    assert error.value.code == code
    assert (counts(ops), len(mock.invokes)) == before


async def hold_after_adoption(w, env=None):
    ops = w["d"].ops
    op, _ = ops.create(OWNER, **(env or w["compound_request"]))
    await ops._execute(op["operation_id"])
    child = compound._child(ops, ops._row(op["operation_id"]), "adopt")
    assert child
    # Execute the real adoption subtree one worker turn at a time, holding the
    # root before Work admission so a concurrent user edit has a precise point.
    for _ in range(25):
        await ops._execute(child["operation_id"])
        for leaf in compound.adoption._children(ops.db, ops._row(child["operation_id"])):
            if leaf["status"] not in {"succeeded", "failed", "cancelled", "needs_attention"}:
                await ops._execute(leaf["operation_id"])
        if ops.get(child["operation_id"])["status"] == "succeeded":
            break
    assert ops.get(child["operation_id"])["status"] == "succeeded"
    # The terminal wakeup cannot admit a Work until a later worker turn.
    assert not ops.db.execute("SELECT 1 FROM pm_work_adopt_and_start_children WHERE parent_id=? AND slot LIKE 'work:%'", (op["operation_id"],)).fetchone()
    ops.db.execute("UPDATE operations SET next_run_at=? WHERE operation_id=?", (time.time() + 3600, op["operation_id"]))
    return op["operation_id"]


async def add_second_work(w):
    env = copy.deepcopy(w["compound_request"])
    work = work_items._get_item(w["d"].ops.db, env["params"]["works"][0]["work_item_id"])
    wi = (await starting.pkg_action(w, "work_item.create", {"project_id": work["project_id"]},
        {"title": "Verify retry after invalid credentials", "goal": "Verify login recovery"}))["work_item_id"]
    start = await starting._start_envelope(w, work_item_id=wi, requirement_ids=["member.login"])
    env["params"]["works"].append({"work_item_id": wi, **start["params"]})
    env["preconditions"]["works"].append({"work_item_id": wi,
        **{key: value for key, value in start["preconditions"].items() if key != "expected_scope_revision"}})
    return env


async def test_adopted_plan_survives_one_refused_work_and_other_work_starts(selected, mock):
    ops = selected["d"].ops
    env = await add_second_work(selected)
    identity = await hold_after_adoption(selected, env)
    first = env["params"]["works"][0]["work_item_id"]
    item = work_items._get_item(ops.db, first)
    await starting.pkg_action(selected, "work_item.update", {"work_item_id": first},
        {"title": "Owner changed the requested scope"}, {"expected_version": item["version"]})
    done = await finish(ops, identity)
    assert done["status"] == "failed", done
    assert done["result"]["adopted"] and done["result"]["stage"] == "partially_started"
    assert [work["status"] for work in done["result"]["works"]] == ["refused", "succeeded"]
    assert done["result"]["works"][0]["start_operation_id"] is None
    assert done["result"]["works"][0]["error_code"] == "WORK_ITEM_CHANGED"
    assert not ops.db.execute("SELECT 1 FROM work_attempts WHERE work_item_id=?", (first,)).fetchone()
    assert mock.channels().count("claude:start-session") == 1
    with pytest.raises(OperationError) as error:
        ops.resume(OWNER, identity)
    assert error.value.code == "NOT_RESUMABLE"


async def test_cancel_after_adoption_keeps_plan_and_starts_nothing(selected, mock):
    ops = selected["d"].ops
    identity = await hold_after_adoption(selected)
    done = ops.cancel(OWNER, identity)
    assert done["status"] == "cancelled"
    assert done["result"]["adopted"] and done["result"]["stage"] == "cancelled"
    await settle_operations(ops)
    assert not ops.db.execute("SELECT 1 FROM work_attempts").fetchone()
    assert mock.channels().count("claude:start-session") == 0


async def test_preview_is_read_only_deduplicated_and_reports_one_work_dependency(selected, mock):
    ops = selected["d"].ops
    env = await add_second_work(selected)
    body = copy.deepcopy(env["params"])
    body["works"][1]["requirement_ids"] = ["member.recovery"]
    principal = Principal(OWNER.actor, frozenset({"observe"}))
    before, calls = ops.db.total_changes, len(mock.invokes)
    result = await compound.preview(ops, principal, **env["target"], body=body)
    assert ops.db.total_changes == before
    assert not result["ready"]
    assert result["works"][0]["readiness"]["ready"]
    assert result["works"][1]["readiness"]["blocking_code"] == "PM_WORK_START_PREREQUISITES"
    assert len([call for call in mock.invokes[calls:] if call["channel"] == "workspace:load"]) == 1
    assert not [call for call in mock.invokes[calls:] if call["channel"] in {"worktree:create", "claude:start-session", "claude:send-message"}]
    assert all("expected_scope_revision" not in row for row in result["preconditions"]["works"])
    # The first ready Work can be submitted using exactly the preview envelope.
    env["params"]["works"] = [body["works"][0]]
    env["preconditions"] = {**result["preconditions"], "works": result["preconditions"]["works"][:1]}
    op, _ = ops.create(OWNER, **env)
    ops.cancel(OWNER, op["operation_id"])


async def test_lost_start_ack_stays_unknown_and_original_receipt_recovers_without_resend(selected, mock, monkeypatch):
    ops = selected["d"].ops
    client = selected["d"].fleet.client("h1")
    invoke = client.invoke
    state = {"sent": False, "hide_receipt": True}

    async def lose_ack(name, *args, **kwargs):
        if state["sent"] and state["hide_receipt"] and name == "claude:get-session-meta":
            raise InvokeTimeout("Fixture read-back unavailable")
        result = await invoke(name, *args, **kwargs)
        if name == "claude:start-session" and not state["sent"]:
            state["sent"] = True
            raise InvokeTimeout("Fixture lost start ACK")
        return result

    monkeypatch.setattr(client, "invoke", lose_ack)
    parent, _ = ops.create(OWNER, **selected["compound_request"])
    unknown = await finish(ops, parent["operation_id"])
    assert unknown["status"] == "needs_attention", unknown
    assert unknown["result"]["stage"] == "start_unknown"
    assert unknown["result"]["adopted"]
    row = unknown["result"]["works"][0]
    assert mock.channels().count("claude:start-session") == 1
    state["hide_receipt"] = False
    # Existing child recovery reads its original sent fence and resource. The
    # parent cannot allocate another Attempt or substitute another start key.
    await ops._execute(row["handoff_operation_id"])
    await ops._execute(row["start_operation_id"])
    ops.resume(OWNER, parent["operation_id"])
    done = await finish(ops, parent["operation_id"])
    assert done["status"] == "succeeded", done
    assert done["result"]["works"][0]["attempt_id"] == row["attempt_id"]
    assert mock.channels().count("claude:start-session") == 1


async def test_root_authority_is_checked_again_before_grandchild_bat_frame(selected, mock):
    from bat_agent_connector import api_auth

    ops = selected["d"].ops
    parent, _ = ops.create(OWNER, **selected["compound_request"])

    def revoke_before_bat(_):
        ops.db.execute("UPDATE api_principals SET revoked_at=? WHERE actor=?", (time.time(), OWNER.actor))

    selected["runner"].after["fetch"] = revoke_before_bat
    done = await finish(ops, parent["operation_id"])
    assert done["status"] in {"failed", "needs_attention"}, done
    assert done["result"]["adopted"]
    assert mock.channels().count("claude:start-session") == 0
    assert mock.channels().count("claude:send-message") == 0
    api_auth.issue(ops.db, OWNER.actor, list(OWNER.scopes))


async def test_live_root_blocks_direct_middle_and_leaf_cancellation_and_hides_withdrawn_source(selected):
    from bat_agent_connector import pm_access

    ops = selected["d"].ops
    parent, _ = ops.create(OWNER, **selected["compound_request"])
    await ops._execute(parent["operation_id"])
    mid = compound._child(ops, ops._row(parent["operation_id"]), "adopt")
    await ops._execute(mid["operation_id"])
    leaf = compound.adoption._children(ops.db, mid)[0]
    for child in (mid, leaf):
        with pytest.raises(OperationError) as error:
            ops.cancel(OWNER, child["operation_id"])
        assert error.value.code == "FORBIDDEN"
    outsider = Principal("another-project-owner", frozenset(OWNER.scopes))
    assert not pm_access.visible(ops.db, outsider, parent)
    assert not pm_access.event_visible(ops.db, outsider, {"resource_type": "operation",
        "resource_id": "missing", "kind": "operation.accepted", "body": {"action": compound.ACTION}})
    assert pm_access.visible(ops.db, OWNER, parent)
    ops.db.execute("DELETE FROM pm_workspace_members WHERE workspace_id=? AND actor=?", (selected["workspace_id"], OWNER.actor))
    assert not pm_access.visible(ops.db, OWNER, parent)


async def test_preview_and_durable_operation_match_http_rpc_mcp(selected, monkeypatch):
    w, ops = selected, selected["d"].ops
    env = copy.deepcopy(w["compound_request"])
    tok = token(w["d"], OWNER.actor, *OWNER.scopes)
    wid, tid = env["target"]["workspace_id"], env["target"]["thread_id"]
    path = f"/api/v1/pm/workspaces/{wid}/threads/{tid}/adopt-and-start-preview"
    before = ops.db.total_changes
    status, via_http = await http(w["port"], "POST", path, tok=tok, body=env["params"])
    assert status == 200, via_http
    via_rpc = await w["d"].call_api(compound.PREVIEW, {**env["target"], "body": env["params"]}, OWNER)
    via_mcp = await _call_mcp_tool(w, monkeypatch, tok, compound.PREVIEW, {**env["target"], **env["params"]})
    for value in (via_http, via_rpc, via_mcp):
        value.pop("observed_at")
    assert via_http == via_rpc == via_mcp
    assert ops.db.total_changes == before
    status, cap = await http(w["port"], "GET", "/api/v1/capabilities", tok=tok)
    assert status == 200
    assert cap["features"]["pm_workspaces"]["work_adopt_and_start"]["available"]
    env["preconditions"] = via_http["preconditions"]
    status, admitted = await http(w["port"], "POST", "/api/v1/operations", tok=tok, body=env)
    assert status == 202, admitted
    identity = admitted["operation"]["operation_id"]
    await finish(ops, identity)
    status, via_http = await http(w["port"], "GET", f"/api/v1/operations/{identity}", tok=tok)
    assert status == 200
    via_rpc = await w["d"].call_api("op_get", {"operation_id": identity}, OWNER)
    via_mcp = await _call_mcp_tool(w, monkeypatch, tok, "operation_get", {"operation_id": identity})
    assert via_http["operation"]["result"] == via_rpc["operation"]["result"] == via_mcp["operation"]["result"]
    outsider = token(w["d"], "unrelated", "observe", "manage", "start")
    status, _ = await http(w["port"], "POST", path, tok=outsider, body=env["params"])
    assert status == 404
    status, _ = await http(w["port"], "GET", f"/api/v1/operations/{identity}", tok=outsider)
    assert status == 404


async def test_child_insert_failure_rolls_back_the_attempt_before_refusal_receipt(selected, mock, monkeypatch):
    ops = selected["d"].ops
    identity = await hold_after_adoption(selected)
    original = starting.pm_work_start.admit_operation

    def raced_admission(*args, **kwargs):
        original(*args, **kwargs)
        raise OperationError("WORK_ITEM_CHANGED", "Fixture change after attempt insertion", 409)

    monkeypatch.setattr(starting.pm_work_start, "admit_operation", raced_admission)
    await ops._execute(identity)
    done = ops.get(identity)
    assert done["status"] == "failed", done
    assert done["result"]["stage"] == "adopted_not_started"
    assert done["result"]["works"][0]["status"] == "refused"
    assert not ops.db.execute("SELECT 1 FROM work_attempts").fetchone()
    assert not ops.db.execute("SELECT 1 FROM operations WHERE action='work.start'").fetchone()
    assert mock.channels().count("claude:start-session") == 0


async def test_new_worker_recovers_adopted_parent_without_repeating_adoption(selected, mock):
    from bat_agent_connector.operations import OperationService

    ops = selected["d"].ops
    identity = await hold_after_adoption(selected)
    adoptions = ops.db.execute("SELECT COUNT(*) FROM pm_plan_adoptions").fetchone()[0]
    new_worker = OperationService(ops.journal, actions=list(ops.actions.values()))
    new_worker.context.update(ops.context)
    done = await finish(new_worker, identity)
    assert done["status"] == "succeeded", done
    assert ops.db.execute("SELECT COUNT(*) FROM pm_plan_adoptions").fetchone()[0] == adoptions
    assert mock.channels().count("claude:start-session") == 1


async def test_changed_owned_git_binding_cannot_be_adopted_under_original_parent(selected, monkeypatch):
    from bat_agent_connector import pm_revision_git

    ops = selected["d"].ops
    parent, _ = ops.create(OWNER, **selected["compound_request"])
    monkeypatch.setattr(pm_revision_git, "binding_id", lambda plan: "f" * 64)
    await ops._execute(parent["operation_id"])
    done = ops.get(parent["operation_id"])
    assert done["status"] == "failed", done
    assert done["error_code"] == "PM_CONVERSATION_CHANGED"
    assert done["result"]["stage"] == "adoption_failed"
    assert not ops.db.execute("SELECT 1 FROM pm_work_adopt_and_start_children WHERE parent_id=?", (parent["operation_id"],)).fetchone()


async def test_definite_adoption_leaf_failure_does_not_offer_an_endless_resume(selected, mock, monkeypatch):
    ops = selected["d"].ops

    async def fail_input(ctx):
        raise OperationError("VERSION_CONFLICT", "The original reviewed input changed", 409)

    monkeypatch.setitem(ops.actions, "pm.input.record", replace(ops.actions["pm.input.record"], run=fail_input))
    parent, _ = ops.create(OWNER, **selected["compound_request"])
    done = await finish(ops, parent["operation_id"])
    assert done["status"] == "failed", done
    assert done["result"]["stage"] == "adoption_failed"
    assert not done["result"]["adopted"]
    assert not ops.db.execute("SELECT 1 FROM work_attempts").fetchone()
    assert mock.channels().count("claude:start-session") == 0
    with pytest.raises(OperationError) as error:
        ops.resume(OWNER, parent["operation_id"])
    assert error.value.code == "NOT_RESUMABLE"


async def test_adoption_receipt_survives_middle_failure_without_starting_or_waiting_forever(selected, mock, monkeypatch):
    ops = selected["d"].ops
    original = ops.actions[compound.proposals.ADOPT]
    before = ops.db.execute("SELECT COUNT(*) FROM pm_plan_adoptions").fetchone()[0]

    async def fail_after_receipt(ctx):
        await original.run(ctx)
        raise OperationError("PM_CONVERSATION_CHILD_STOPPED", "Fixture failure after adoption receipt", 409)

    monkeypatch.setitem(ops.actions, compound.proposals.ADOPT, replace(original, run=fail_after_receipt))
    parent, _ = ops.create(OWNER, **selected["compound_request"])
    done = await finish(ops, parent["operation_id"])
    assert done["status"] == "failed", done
    assert done["result"]["adopted"]
    assert done["result"]["stage"] == "adopted_not_started"
    assert ops.db.execute("SELECT COUNT(*) FROM pm_plan_adoptions").fetchone()[0] == before + 1
    assert not ops.db.execute("SELECT 1 FROM work_attempts").fetchone()
    assert mock.channels().count("claude:start-session") == 0
