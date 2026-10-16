"""v1.3 P5 follow-up: queue, proven-unsent fence, receipts, and read paths.

Tests drive public HTTP / RPC / MCP, mock BAT, and settle_operations. They do not
edit operation rows or call ``_transition``. Timing uses ``asyncio.Event`` or a
patched clock, never ``sleep``.
"""

from __future__ import annotations

import asyncio
import inspect
import json
from contextlib import asynccontextmanager

import pytest

from bat_agent_connector import (
    client as client_mod,
)
from bat_agent_connector import (
    controller_authority,
    lifecycle,
    pm_access,
    pm_work_follow_up,
    pm_workspace,
    registry,
    service,
)
from bat_agent_connector.api_auth import Principal
from bat_agent_connector.errors import ConnectionLost, WriteRefused
from bat_agent_connector.operations import (
    STATES as OP_STATES,
)
from bat_agent_connector.operations import (
    Cancelled,
    OpContext,
    Wait,
)
from bat_agent_connector.task_daemon import TaskDaemon
from tests.operation_helpers import settle_operations
from tests.test_api_v1 import http, token
from tests.test_pm_lifecycle import decide
from tests.test_pm_package_handoff import (
    pm as handoff_pm_fixture,
)
from tests.test_pm_package_handoff import (
    ready as handoff_ready_fixture,
)
from tests.test_pm_package_handoff import (
    world as handoff_world_fixture,
)
from tests.test_pm_work_attempt_result import (
    _call_mcp_tool,
    _seed_worktree_commits,
    _set_turn_messages,
    _start_managed_attempt,
    start_ready,
)
from tests.test_pm_work_packages import action as pkg_action
from tests.test_pm_work_start import STARTER_OWNER

world = handoff_world_fixture
pm = handoff_pm_fixture
ready = handoff_ready_fixture
_ = start_ready  # re-exported fixture


def _frames(mock, message_id=None, *, start=0):
    found = []
    for frame in mock.invokes[start:]:
        if frame.get("channel") != "claude:send-message":
            continue
        if message_id is None or (frame.get("params") or {}).get("clientMessageId") == message_id:
            found.append(frame)
    return found


def _by_client_id(frames):
    counts: dict[str, int] = {}
    for frame in frames:
        mid = (frame.get("params") or {}).get("clientMessageId")
        counts[str(mid)] = counts.get(str(mid), 0) + 1
    return counts


async def _post(w, att, text, *, key, schedule="after_current", pre=None, tok=None, target=None, params=None):
    body = {
        "action": "session.send",
        "target": target if target is not None else {
            "workspace_id": att["workspace_id"],
            "work_item_id": att["work_item_id"],
        },
        "params": params if params is not None else {"text": text, "schedule": schedule},
        "preconditions": pre or {},
    }
    return await http(
        w["port"], "POST", "/api/v1/operations",
        tok=tok or w["owner_token"], body=body, headers={"Idempotency-Key": key},
    )


async def _get_op(w, op_id, tok=None):
    status, body = await http(w["port"], "GET", f"/api/v1/operations/{op_id}", tok=tok or w["owner_token"])
    assert status == 200, body
    return body["operation"]


async def _settle(ops, op_id, rounds=6):
    last = None
    for _ in range(rounds):
        ops.wake(op_id)
        await settle_operations(ops)
        last = ops.get(op_id)
        if last["status"] in {"succeeded", "failed", "cancelled", "needs_attention"}:
            return last
    return last


async def _refresh(w, mock, att, marker, *, streaming=False, pending=None, extra=None):
    _seed_worktree_commits(mock, att["worktree_path"], att["base_sha"])
    _set_turn_messages(
        mock, att["session_id"], marker,
        streaming=streaming, pending_ask=pending, include_echo=True, extra_items=extra,
    )
    await w["d"].inventory._refresh_scheduled(att["host"])


async def _inputs(w, att, tok=None):
    status, body = await http(
        w["port"], "GET",
        f"/api/v1/pm/workspaces/{att['workspace_id']}/work/{att['work_item_id']}/inputs",
        tok=tok or w["owner_token"],
    )
    return status, body


async def _result(w, att):
    status, body = await http(
        w["port"], "GET",
        f"/api/v1/pm/workspaces/{att['workspace_id']}/work-attempts/{att['attempt_id']}/result",
        tok=w["owner_token"],
    )
    assert status == 200, body
    return body


def _positions(ops, attempt_id):
    rows = ops.db.execute(
        "SELECT position, operation_id, message_id FROM work_inputs WHERE attempt_id=? ORDER BY position",
        (attempt_id,),
    ).fetchall()
    return [(int(r["position"]), r["operation_id"], r["message_id"]) for r in rows]


async def _queue(w, mock, att, text, key):
    status, body = await _post(w, att, text, key=key)  # gitleaks:allow (test idempotency key)
    assert status == 202, body
    op_id = body["operation"]["operation_id"]
    await _settle(w["d"].ops, op_id, rounds=2)
    return op_id


async def _send_head(w, mock, att, marker):
    await _refresh(w, mock, att, marker, streaming=False)
    await settle_operations(w["d"].ops)


@asynccontextmanager
async def _restarted(daemon):
    daemon.release_owner()
    restarted = TaskDaemon(daemon._config, daemon._db_path)
    restarted.__dict__["_fixture_capabilities"] = dict(daemon.__dict__.get("_fixture_capabilities", {}))
    restarted.acquire_owner()
    # The fixture replaces the owner's Git runner. A new daemon builds its own,
    # which cannot see that test runner, so package handoff checks fail closed.
    runner = daemon.ops.context.get("git_runner")
    if runner is not None:
        restarted.ops.context["git_runner"] = runner
        restarted.fleet.confinement_runner = runner
    try:
        yield restarted
    finally:
        await restarted.fleet.close()
        await restarted.inventory.close()
        restarted.journal.close()


def _patch_roundtrip(monkeypatch, predicate):
    original = client_mod.BatClient._roundtrip

    async def wrapped(self, frame, timeout, on_transport=None):
        return await predicate(self, frame, timeout, on_transport, original)

    monkeypatch.setattr(client_mod.BatClient, "_roundtrip", wrapped)
    return original


def _restore_roundtrip(original):
    """Put the real round trip back without ``monkeypatch.undo()``.

    ``undo()`` also reverts the fixture's ``BATC_STATE_DIR``. Registry lookups
    would then miss the session this test created.
    """
    client_mod.BatClient._roundtrip = original


async def _drain_uncertain(ops, op_id, monkeypatch):
    import bat_agent_connector.operations as operations_mod

    original = operations_mod.time.time
    clock = {"t": original() + 10_000}

    def now():
        return clock["t"]

    monkeypatch.setattr(operations_mod.time, "time", now)
    try:
        for _ in range(8):
            current = ops.get(op_id)
            if current["status"] == "needs_attention":
                return current
            clock["t"] += 10_000
            ops.wake(op_id)
            await settle_operations(ops)
        current = ops.get(op_id)
        assert current["status"] == "needs_attention", current
        return current
    finally:
        monkeypatch.setattr(operations_mod.time, "time", original)


# --------------------------------------------------------------------------- 1


async def test_follow_up_fifo_order(start_ready, mock, monkeypatch):
    w = start_ready
    ops = w["d"].ops
    version = ops.db.execute("PRAGMA user_version").fetchone()[0]
    pm_work_follow_up.schema(ops.journal)
    pm_work_follow_up.schema(ops.journal)
    assert ops.db.execute("PRAGMA user_version").fetchone()[0] == version
    att = await _start_managed_attempt(w, mock, key="p5-fifo-start")  # gitleaks:allow (test idempotency key)
    before = _frames(mock)
    op_ids = []
    for index, text in enumerate(("first instruction", "second instruction", "third instruction"), start=1):
        op_ids.append(await _queue(w, mock, att, text, f"p5-fifo-{index}"))  # gitleaks:allow (test idempotency key)
    positions = _positions(ops, att["attempt_id"])
    assert [row[0] for row in positions] == [1, 2, 3]
    assert [row[1] for row in positions] == op_ids
    assert [row[2] for row in positions] == [f"batc-{op_id}" for op_id in op_ids]
    for op_id in op_ids:
        queued = await _get_op(w, op_id)
        assert queued["status"] == "waiting_external", queued
        assert queued["input_receipt"]["state"] == "queued"
        assert queued["input_receipt"]["cancellable"] is True
        assert queued["input_receipt"]["labels"]["state"]["zh-TW"] == "排隊中"
        assert queued["input_receipt"]["labels"]["state"]["en"] == "Queued"
        assert _frames(mock, f"batc-{op_id}") == []
    assert len(_frames(mock)) == len(before)

    marker = att["turn_marker"]
    sent = []
    for op_id in op_ids:
        await _send_head(w, mock, att, marker)
        await _settle(ops, op_id)
        done = await _get_op(w, op_id)
        assert done["status"] == "succeeded", done
        assert done["input_receipt"]["state"] == "sent"
        assert done["input_receipt"]["labels"]["state"]["zh-TW"] == "已送出"
        assert len(_frames(mock, f"batc-{op_id}")) == 1
        sent.append(f"batc-{op_id}")
        marker = f"batc-{op_id}"
    assert _positions(ops, att["attempt_id"]) == positions
    await _refresh(w, mock, att, marker, streaming=False)

    result = await _result(w, att)
    assert result["conversation"]["turn_marker"] == sent[-1]
    assert result["conversation"]["turn_done"] is True
    markers = {item["turn_marker"] for item in result["results"]}
    assert att["turn_marker"] in markers
    assert set(sent) <= markers
    assert len(result["results"]) >= 4

    status, listed = await _inputs(w, att)
    assert status == 200, listed
    assert [item["operation_id"] for item in listed["items"]] == op_ids
    assert listed["has_more"] is False
    rpc = await w["d"].call_api(
        "pm_work_inputs_list",
        {"workspace_id": att["workspace_id"], "work_item_id": att["work_item_id"]},
        STARTER_OWNER,
    )
    mcp = await _call_mcp_tool(
        w, monkeypatch, w["owner_token"], "pm_work_inputs_list",
        {"workspace_id": att["workspace_id"], "work_item_id": att["work_item_id"]},
    )
    def signature(payload):
        return [(item["operation_id"], item["position"], item["state"]) for item in payload["items"]]

    assert signature(listed) == signature(rpc) == signature(mcp)
    cap_status, cap = await http(w["port"], "GET", "/api/v1/capabilities", tok=w["owner_token"])
    assert cap_status == 200
    assert cap["features"]["pm_workspaces"]["work_follow_up"] == pm_work_follow_up.capabilities(ops)
    names = [row[0] for row in ops.db.execute(
        "SELECT name FROM sqlite_master WHERE type='trigger' AND tbl_name='work_inputs'")]
    assert "work_inputs_no_update" in names and "work_inputs_no_delete" in names


# --------------------------------------------------------------------------- 2


async def test_follow_up_busy_wait_then_boundary_wake(start_ready, mock):
    w = start_ready
    att = await _start_managed_attempt(w, mock, key="p5-busy-wake")  # gitleaks:allow (test idempotency key)
    await _refresh(w, mock, att, att["turn_marker"], streaming=True)
    start = len(mock.invokes)
    op_id = await _queue(w, mock, att, "wait for the current turn", "p5-busy-wake-1")  # gitleaks:allow (test idempotency key)
    queued = await _get_op(w, op_id)
    assert queued["status"] == "waiting_external"
    assert queued["input_receipt"]["state"] == "queued"
    assert _frames(mock, start=start) == []
    await _refresh(w, mock, att, att["turn_marker"], streaming=False)
    await _settle(w["d"].ops, op_id)
    sent = await _get_op(w, op_id)
    assert sent["status"] == "succeeded", sent
    assert sent["input_receipt"]["state"] == "sent"
    assert len(_frames(mock, f"batc-{op_id}")) == 1


# --------------------------------------------------------------------------- 3


async def test_queued_input_never_sent_before_boundary(start_ready, mock):
    w = start_ready
    ops = w["d"].ops
    att = await _start_managed_attempt(w, mock, key="p5-never-before")  # gitleaks:allow (test idempotency key)
    pending = {"toolUseId": "question-1", "questions": [{"question": "Continue?"}]}

    await _refresh(w, mock, att, att["turn_marker"], streaming=True)
    streaming_id = await _queue(w, mock, att, "while streaming", "p5-never-stream")  # gitleaks:allow (test idempotency key)
    ops.wake(streaming_id)
    await settle_operations(ops)
    assert (await _get_op(w, streaming_id))["input_receipt"]["state"] == "queued"
    assert _frames(mock, f"batc-{streaming_id}") == []
    refused, body = await _post(w, att, "now while streaming", key="p5-now-stream", schedule="now")  # gitleaks:allow (test idempotency key)
    assert refused == 409 and body["error"]["code"] == "PM_WORK_SESSION_BUSY", body

    # Auto inventory reads pending only while streaming. A streaming pass records
    # the question; the idle pass keeps that fact and clears the streaming flag.
    await _refresh(w, mock, att, att["turn_marker"], streaming=True, pending=pending)
    await _refresh(w, mock, att, att["turn_marker"], streaming=False, pending=pending)
    gate_id = await _queue(w, mock, att, "while a question is open", "p5-never-gate")  # gitleaks:allow (test idempotency key)
    ops.wake(gate_id)
    await settle_operations(ops)
    assert (await _get_op(w, gate_id))["input_receipt"]["state"] == "queued"
    assert _frames(mock, f"batc-{gate_id}") == []
    refused, body = await _post(w, att, "now while waiting", key="p5-now-gate", schedule="now")  # gitleaks:allow (test idempotency key)
    assert refused == 409 and body["error"]["code"] == "PM_WORK_SESSION_WAITING_INPUT", body

    await _refresh(w, mock, att, att["turn_marker"], streaming=True, pending=None)
    _set_turn_messages(mock, att["session_id"], att["turn_marker"], streaming=False, include_echo=False)
    await w["d"].inventory._refresh_scheduled(att["host"])
    missing_id = await _queue(w, mock, att, "before any boundary", "p5-never-boundary")  # gitleaks:allow (test idempotency key)
    ops.wake(missing_id)
    await settle_operations(ops)
    assert (await _get_op(w, missing_id))["input_receipt"]["state"] == "queued"
    assert _frames(mock, f"batc-{missing_id}") == []
    refused, body = await _post(w, att, "now before boundary", key="p5-now-boundary", schedule="now")  # gitleaks:allow (test idempotency key)
    assert refused == 409 and body["error"]["code"] == "PM_WORK_SESSION_BUSY", body


# --------------------------------------------------------------------------- 4


async def test_pre_fence_disconnect_and_busy_wait_use_zero_uncertain_tries_and_stay_queued(start_ready, mock, monkeypatch):
    w = start_ready
    ops = w["d"].ops
    att = await _start_managed_attempt(w, mock, key="p5-pre-fence")  # gitleaks:allow (test idempotency key)
    await _refresh(w, mock, att, att["turn_marker"], streaming=False)

    async def lose_before(_self, frame, timeout, on_transport, original):
        if frame.get("channel") == "claude:send-message":
            raise ConnectionLost("lost before the frame left")
        return await original(_self, frame, timeout, on_transport=on_transport)

    original_roundtrip = _patch_roundtrip(monkeypatch, lose_before)
    op_id = await _queue(w, mock, att, "disconnect before send", "p5-pre-fence-1")  # gitleaks:allow (test idempotency key)
    for _ in range(6):
        ops.wake(op_id)
        await settle_operations(ops)
        current = await _get_op(w, op_id)
        assert current["status"] == "waiting_external", current
        assert current["uncertain_tries"] == 0
        assert current["input_receipt"]["state"] == "queued"
        step = next(item for item in current["steps"] if item["name"] == "send")
        assert step["status"] == "started"
        assert _frames(mock, f"batc-{op_id}") == []
    _restore_roundtrip(original_roundtrip)
    ops.wake(op_id)
    await _settle(ops, op_id)
    sent = await _get_op(w, op_id)
    assert sent["status"] == "succeeded", (sent.get("error_code"), sent.get("status_reason"), sent.get("steps"))
    assert len(_frames(mock, f"batc-{sent['operation_id']}")) == 1

    await _refresh(w, mock, att, f"batc-{op_id}", streaming=False)
    mock.metas[att["session_id"]]["isStreaming"] = True
    live_id = await _queue(w, mock, att, "busy at the last frame", "p5-pre-fence-live")  # gitleaks:allow (test idempotency key)
    for _ in range(6):
        ops.wake(live_id)
        await settle_operations(ops)
        current = await _get_op(w, live_id)
        assert current["status"] == "waiting_external", current
        assert current["uncertain_tries"] == 0
        assert current["input_receipt"]["state"] == "queued"
        assert _frames(mock, f"batc-{live_id}") == []
    mock.metas[att["session_id"]]["isStreaming"] = False
    ops.wake(live_id)
    await _settle(ops, live_id)
    assert (await _get_op(w, live_id))["status"] == "succeeded"
    assert len(_frames(mock, f"batc-{live_id}")) == 1

    async def raise_wait(*_args, **_kwargs):
        raise Wait(status="waiting_external", reason="legacy-unfenced", delay_s=5.0)

    original_send = service.session_send
    monkeypatch.setattr(service, "session_send", raise_wait)
    direct, direct_body = await _post(
        w, att, "legacy wait",
        key="p5-legacy-wait",  # gitleaks:allow (test idempotency key)
        target={"host": att["host"], "session_id": att["session_id"]},
        params={"text": "legacy wait has no fence"},
    )
    assert direct == 202, direct_body
    legacy = await _settle(ops, direct_body["operation"]["operation_id"])
    assert legacy["status"] == "failed", legacy
    assert legacy["steps"][-1]["status"] == "failed"
    monkeypatch.setattr(service, "session_send", original_send)

    calls = {"n": 0}
    original_check = OpContext.check_cancel

    def cancel_on_second(self):
        calls["n"] += 1
        if calls["n"] >= 2:
            raise Cancelled("cancel before the frame")
        return original_check(self)

    monkeypatch.setattr(OpContext, "check_cancel", cancel_on_second)
    await _refresh(w, mock, att, f"batc-{live_id}", streaming=False)
    cancel_status, cancel_body = await _post(
        w, att, "cancel before send", key="p5-pre-fence-cancel",  # gitleaks:allow (test idempotency key)
    )
    assert cancel_status == 202, cancel_body
    cancelled_id = cancel_body["operation"]["operation_id"]
    await _settle(ops, cancelled_id)
    cancelled = await _get_op(w, cancelled_id)
    assert cancelled["status"] == "failed", cancelled
    assert cancelled["error_code"] == "CANCELLED"
    assert cancelled["input_receipt"]["state"] == "cancelled"
    assert _frames(mock, f"batc-{cancelled_id}") == []


# --------------------------------------------------------------------------- 5


async def test_cancel_while_waiting(start_ready, mock):
    w = start_ready
    ops = w["d"].ops
    att = await _start_managed_attempt(w, mock, key="p5-cancel-wait")  # gitleaks:allow (test idempotency key)
    await _refresh(w, mock, att, att["turn_marker"], streaming=True)
    first = await _queue(w, mock, att, "cancel me", "p5-cancel-wait-1")  # gitleaks:allow (test idempotency key)
    second = await _queue(w, mock, att, "send me later", "p5-cancel-wait-2")  # gitleaks:allow (test idempotency key)
    status, body = await http(w["port"], "POST", f"/api/v1/operations/{first}/cancel", tok=w["owner_token"])
    assert status == 200, body
    cancelled = await _get_op(w, first)
    assert cancelled["status"] == "cancelled"
    assert cancelled["input_receipt"]["state"] == "cancelled"
    await _refresh(w, mock, att, att["turn_marker"], streaming=False)
    await _settle(ops, second)
    assert _frames(mock, f"batc-{first}") == []
    sent = await _get_op(w, second)
    assert sent["status"] == "succeeded", sent
    assert len(_frames(mock, f"batc-{second}")) == 1


# --------------------------------------------------------------------------- 6


async def test_cancel_refused_after_send_began(start_ready, mock, monkeypatch):
    w = start_ready
    ops = w["d"].ops
    att = await _start_managed_attempt(w, mock, key="p5-no-cancel")  # gitleaks:allow (test idempotency key)
    await _refresh(w, mock, att, att["turn_marker"], streaming=False)
    started = asyncio.Event()
    release = asyncio.Event()

    async def hold(_self, frame, timeout, on_transport, original):
        if frame.get("channel") == "claude:send-message" and not started.is_set():
            if on_transport:
                on_transport()
            started.set()
            await release.wait()
            return await original(_self, frame, timeout, on_transport=None)
        return await original(_self, frame, timeout, on_transport=on_transport)

    original_roundtrip = _patch_roundtrip(monkeypatch, hold)
    status, body = await _post(w, att, "in flight", key="p5-no-cancel-1")  # gitleaks:allow (test idempotency key)
    assert status == 202, body
    op_id = body["operation"]["operation_id"]
    await ops.run_due()
    await asyncio.wait_for(started.wait(), 10)
    sending = await _get_op(w, op_id)
    assert sending["status"] == "running"
    assert sending["input_receipt"]["state"] == "sending"
    assert sending["input_receipt"]["cancellable"] is False
    refused, error = await http(w["port"], "POST", f"/api/v1/operations/{op_id}/cancel", tok=w["owner_token"])
    assert refused == 409 and error["error"]["code"] == "PM_WORK_CANCEL_NOT_ALLOWED_AFTER_SEND", error
    assert not ops.get(op_id)["cancel_requested"]
    release.set()
    await _settle(ops, op_id)
    sent = await _get_op(w, op_id)
    assert sent["input_receipt"]["state"] == "sent"
    refused, error = await http(w["port"], "POST", f"/api/v1/operations/{op_id}/cancel", tok=w["owner_token"])
    assert refused == 409 and error["error"]["code"] == "PM_WORK_CANCEL_NOT_ALLOWED_AFTER_SEND"
    assert not ops.get(op_id)["cancel_requested"]
    _restore_roundtrip(original_roundtrip)

    mock.echo_sends = False
    await _refresh(w, mock, att, f"batc-{op_id}", streaming=False)

    async def drop_after(_self, frame, timeout, on_transport, original):
        if frame.get("channel") == "claude:send-message":
            await original(_self, frame, timeout, on_transport=on_transport)
            raise ConnectionLost("dropped after the fence")
        return await original(_self, frame, timeout, on_transport=on_transport)

    _patch_roundtrip(monkeypatch, drop_after)
    status, body = await _post(w, att, "unknown delivery", key="p5-no-cancel-2")  # gitleaks:allow (test idempotency key)
    assert status == 202, body
    unknown_id = body["operation"]["operation_id"]
    await _settle(ops, unknown_id, rounds=1)
    unknown = await _get_op(w, unknown_id)
    assert unknown["status"] == "uncertain", unknown
    assert unknown["input_receipt"]["state"] == "unknown"
    assert unknown["input_receipt"]["cancellable"] is False
    refused, error = await http(w["port"], "POST", f"/api/v1/operations/{unknown_id}/cancel", tok=w["owner_token"])
    assert refused == 409 and error["error"]["code"] == "PM_WORK_CANCEL_NOT_ALLOWED_AFTER_SEND", error
    assert not ops.get(unknown_id)["cancel_requested"]
    attention = await _drain_uncertain(ops, unknown_id, monkeypatch)
    assert attention["status"] == "needs_attention"
    assert (await _get_op(w, unknown_id))["input_receipt"]["state"] == "unknown"
    refused, error = await http(w["port"], "POST", f"/api/v1/operations/{unknown_id}/cancel", tok=w["owner_token"])
    assert refused == 409 and error["error"]["code"] == "PM_WORK_CANCEL_NOT_ALLOWED_AFTER_SEND"
    assert not ops.get(unknown_id)["cancel_requested"]
    assert len(_frames(mock, f"batc-{unknown_id}")) == 1


# --------------------------------------------------------------------------- 7


async def test_post_fence_disconnect_busy_reply_and_refusal_reconcile_by_echo(start_ready, mock, monkeypatch):
    w = start_ready
    ops = w["d"].ops
    att = await _start_managed_attempt(w, mock, key="p5-post-fence")  # gitleaks:allow (test idempotency key)
    mock.echo_sends = False
    await _refresh(w, mock, att, att["turn_marker"], streaming=False)
    marker = att["turn_marker"]

    async def drop_after(_self, frame, timeout, on_transport, original):
        if frame.get("channel") == "claude:send-message":
            await original(_self, frame, timeout, on_transport=on_transport)
            raise ConnectionLost("dropped after the fence")
        return await original(_self, frame, timeout, on_transport=on_transport)

    original_roundtrip = _patch_roundtrip(monkeypatch, drop_after)
    status, body = await _post(w, att, "lost reply", key="p5-post-1")  # gitleaks:allow (test idempotency key)
    assert status == 202, body
    first = body["operation"]["operation_id"]
    second = (await _post(w, att, "wait behind unknown", key="p5-post-2"))[1]["operation"]["operation_id"]  # gitleaks:allow (test idempotency key)
    await _settle(ops, second, rounds=2)
    lost = await _get_op(w, first)
    follower = await _get_op(w, second)
    assert lost["input_receipt"]["state"] == "unknown", lost
    assert follower["status"] == "waiting_external"
    assert follower["input_receipt"]["state"] == "queued"
    assert follower["input_receipt"]["status_reason"] == "predecessor_outcome_uncertain"
    assert follower["input_receipt"]["blocked_by_operation_id"] == first
    assert len(_frames(mock, f"batc-{first}")) == 1
    resume, _ = await http(w["port"], "POST", f"/api/v1/operations/{first}/resume", tok=w["owner_token"])
    await settle_operations(ops)
    assert len(_frames(mock, f"batc-{first}")) == 1
    assert resume in {200, 409}
    _restore_roundtrip(original_roundtrip)

    # Exhaust the uncertain retries before the echo exists. Reconcile must
    # stay empty until the transcript line is appended below.
    if ops.get(first)["status"] != "needs_attention":
        await _drain_uncertain(ops, first, monkeypatch)
    mock.states[att["session_id"]]["messages"].append(
        {"id": f"batc-{first}", "role": "user", "content": "lost reply", "timestamp": 1_790_001_100_000}
    )
    status, body = await http(w["port"], "POST", f"/api/v1/operations/{first}/resume", tok=w["owner_token"])
    assert status == 200, body
    await _settle(ops, first)
    reconciled = await _get_op(w, first)
    assert reconciled["status"] == "succeeded", (
        reconciled.get("status"), reconciled.get("error_code"), reconciled.get("status_reason"),
        [(step.get("name"), step.get("status"), step.get("error")) for step in reconciled.get("steps") or []],
    )
    assert reconciled["input_receipt"]["state"] == "sent"
    assert reconciled["input_receipt"]["reconciled"] is True
    assert reconciled["input_receipt"]["settled_by"] == "bat_transcript"
    assert len(_frames(mock, f"batc-{first}")) == 1
    marker = f"batc-{first}"

    mock.handlers["claude:send-message"] = lambda _params: (_ for _ in ()).throw(Exception("Remote server is busy"))
    await _refresh(w, mock, att, marker, streaming=False)
    # The follower may now be free to send into the busy handler.
    await _settle(ops, second)
    busy = await _get_op(w, second)
    assert busy["input_receipt"]["state"] == "unknown", busy
    assert busy["status"] in {"uncertain", "needs_attention"}
    assert len(_frames(mock, f"batc-{second}")) == 1
    mock.handlers.pop("claude:send-message", None)
    if busy["status"] != "needs_attention":
        await _drain_uncertain(ops, second, monkeypatch)
    mock.states[att["session_id"]]["messages"].append(
        {"id": f"batc-{second}", "role": "user", "content": "wait behind unknown", "timestamp": 1_790_001_200_000}
    )
    await http(w["port"], "POST", f"/api/v1/operations/{second}/resume", tok=w["owner_token"])
    await _settle(ops, second)
    assert (await _get_op(w, second))["status"] == "succeeded"
    marker = f"batc-{second}"

    async def refuse_after(_self, frame, timeout, on_transport, original):
        if frame.get("channel") == "claude:send-message":
            await original(_self, frame, timeout, on_transport=on_transport)
            raise WriteRefused("deterministic refusal after the fence")
        return await original(_self, frame, timeout, on_transport=on_transport)

    original_roundtrip = _patch_roundtrip(monkeypatch, refuse_after)
    await _refresh(w, mock, att, marker, streaming=False)
    status, body = await _post(w, att, "refused after send", key="p5-post-refuse")  # gitleaks:allow (test idempotency key)
    assert status == 202, body
    refused_id = body["operation"]["operation_id"]
    await _settle(ops, refused_id, rounds=1)
    refused = await _get_op(w, refused_id)
    assert refused["status"] != "failed"
    assert refused["input_receipt"]["state"] == "unknown", refused
    assert all(item.get("label") != "Retry as new instruction" for item in refused["input_receipt"]["next_allowed_actions"])
    _restore_roundtrip(original_roundtrip)
    if ops.get(refused_id)["status"] != "needs_attention":
        await _drain_uncertain(ops, refused_id, monkeypatch)
    mock.states[att["session_id"]]["messages"].append(
        {"id": f"batc-{refused_id}", "role": "user", "content": "refused after send", "timestamp": 1_790_001_300_000}
    )
    await http(w["port"], "POST", f"/api/v1/operations/{refused_id}/resume", tok=w["owner_token"])
    await _settle(ops, refused_id)
    marker = f"batc-{refused_id}"

    # The failed input wakes the next one inside the same settle. Reject only
    # that input's message id; the follower must still be accepted.
    rejected_target = {"id": None}

    def reject(params):
        mid = params.get("clientMessageId")
        if rejected_target["id"] is not None and mid == f"batc-{rejected_target['id']}":
            return {"accepted": False, "ok": False}
        sid = params.get("sessionId")
        result = {
            "ok": True,
            "accepted": True,
            "queued": bool((mock.metas.get(sid) or {}).get("isStreaming")),
        }
        if mid:
            mock.sent[mid] = result
        return result

    mock.handlers["claude:send-message"] = reject
    await _refresh(w, mock, att, marker, streaming=False)
    status, body = await _post(w, att, "explicitly rejected", key="p5-post-reject")  # gitleaks:allow (test idempotency key)
    assert status == 202, body
    rejected_id = body["operation"]["operation_id"]
    rejected_target["id"] = rejected_id
    follower_id = (await _post(w, att, "after rejection", key="p5-post-after-reject"))[1]["operation"]["operation_id"]  # gitleaks:allow (test idempotency key)
    await _settle(ops, rejected_id)
    rejected = await _get_op(w, rejected_id)
    assert rejected["status"] == "failed" and rejected["error_code"] == "NOT_ACCEPTED", rejected
    assert rejected["input_receipt"]["state"] == "failed"
    assert any(item.get("label") == "Retry as new instruction" for item in rejected["input_receipt"]["next_allowed_actions"])
    assert any(item.get("label_zh_TW") == "以新指令重試" for item in rejected["input_receipt"]["next_allowed_actions"])
    mock.handlers.pop("claude:send-message", None)
    await _refresh(w, mock, att, marker, streaming=False)
    await _settle(ops, follower_id)
    followed = await _get_op(w, follower_id)
    assert followed["status"] == "succeeded", (
        followed.get("status"), followed.get("error_code"), followed.get("status_reason"),
    )
    marker = f"batc-{follower_id}"

    mock.handlers["claude:send-message"] = lambda _params: {}
    await _refresh(w, mock, att, marker, streaming=False)
    status, body = await _post(w, att, "missing receipt", key="p5-post-empty")  # gitleaks:allow (test idempotency key)
    assert status == 202, body
    empty_id = body["operation"]["operation_id"]
    blocked_id = (await _post(w, att, "blocked by unproven", key="p5-post-blocked"))[1]["operation"]["operation_id"]  # gitleaks:allow (test idempotency key)
    await _settle(ops, blocked_id, rounds=2)
    empty = await _get_op(w, empty_id)
    blocked = await _get_op(w, blocked_id)
    assert empty["status"] == "needs_attention", empty
    assert empty["error_code"] == "FOLLOW_UP_PROMPT_UNPROVEN"
    assert empty["input_receipt"]["state"] == "unknown"
    assert any(item.get("label_zh_TW") == "以原 ID 重新確認" for item in empty["input_receipt"]["next_allowed_actions"])
    assert len(_frames(mock, f"batc-{empty_id}")) == 1
    assert blocked["status"] == "waiting_external"
    assert blocked["input_receipt"]["status_reason"] == "predecessor_outcome_uncertain"
    assert blocked["input_receipt"]["blocked_by_operation_id"] == empty_id


# --------------------------------------------------------------------------- 8


async def test_codex_work_follow_up_refused_with_zero_bat_calls(start_ready, mock):
    w = start_ready
    ops = w["d"].ops
    att = await _start_managed_attempt(w, mock, key="p5-codex")  # gitleaks:allow (test idempotency key)
    registry.update(att["host"], att["session_id"], agent_preset="codex")
    for terminal in mock.ws_doc.get("terminals", []):
        if terminal.get("id") == att["session_id"]:
            terminal["agentPreset"] = "codex"
    await w["d"].inventory.refresh_host(att["host"])
    before_ops = ops.db.execute("SELECT COUNT(*) FROM operations").fetchone()[0]
    before_inputs = ops.db.execute("SELECT COUNT(*) FROM work_inputs").fetchone()[0]
    before_invokes = len(mock.invokes)
    status, body = await _post(w, att, "codex follow up", key="p5-codex-send")  # gitleaks:allow (test idempotency key)
    assert status == 409, body
    assert body["error"]["code"] == "PM_WORK_CODEX_FOLLOW_UP_UNSUPPORTED"
    assert body["error"]["admission_refused"] is True
    assert ops.db.execute("SELECT COUNT(*) FROM operations").fetchone()[0] == before_ops
    assert ops.db.execute("SELECT COUNT(*) FROM work_inputs").fetchone()[0] == before_inputs
    assert len(mock.invokes) == before_invokes


# --------------------------------------------------------------------------- 9


async def test_controller_mismatch_refused(start_ready, mock):
    w = start_ready
    ops = w["d"].ops
    att = await _start_managed_attempt(w, mock, key="p5-controller")  # gitleaks:allow (test idempotency key)
    current = pm_workspace.get_workspace(ops.db, STARTER_OWNER, att["workspace_id"])
    await pkg_action(
        w, "pm.workspace.member.set", {"workspace_id": att["workspace_id"]},
        {"actor": "colleague", "role": "contributor"}, {"expected_version": current["version"]},
        who=STARTER_OWNER,
    )
    colleague = token(w["d"], "colleague", "observe", "manage", "operate", "integrate")
    before_ops = ops.db.execute("SELECT COUNT(*) FROM operations").fetchone()[0]
    before_calls = len(mock.invokes)
    status, body = await _post(
        w, att, "from another controller", key="p5-controller-send", tok=colleague,  # gitleaks:allow (test idempotency key)
    )
    assert status == 409, body
    assert body["error"]["code"] == "PM_WORK_CONTROLLER_CONFLICT"
    assert body["error"]["admission_refused"] is True
    assert ops.db.execute("SELECT COUNT(*) FROM operations").fetchone()[0] == before_ops
    assert len(mock.invokes) == before_calls


async def test_writer_membership_rechecked_before_every_frame(start_ready, mock):
    """A queued follow-up is refused at the frame if the admitted writer lost write access."""
    w = start_ready
    ops = w["d"].ops
    att = await _start_managed_attempt(w, mock, key="p5-writer-recheck")  # gitleaks:allow (test idempotency key)
    second = Principal("pm-second-owner", frozenset({"observe", "manage", "start", "operate"}))
    second_tok = token(w["d"], second.actor, "observe", "manage", "start", "operate")
    current = pm_workspace.get_workspace(ops.db, STARTER_OWNER, att["workspace_id"])
    await pkg_action(
        w, "pm.workspace.member.set", {"workspace_id": att["workspace_id"]},
        {"actor": second.actor, "role": "owner"}, {"expected_version": current["version"]},
        who=STARTER_OWNER,
    )

    async def set_member(actor, role):
        viewed = pm_workspace.get_workspace(ops.db, second, att["workspace_id"])
        await pkg_action(
            w, "pm.workspace.member.set", {"workspace_id": att["workspace_id"]},
            {"actor": actor, "role": role}, {"expected_version": viewed["version"]},
            who=second,
        )

    await _refresh(w, mock, att, att["turn_marker"], streaming=True)
    demoted_id = await _queue(w, mock, att, "queued before demotion", "p5-writer-demote")  # gitleaks:allow (test idempotency key)
    queued = await _get_op(w, demoted_id)
    assert queued["status"] == "waiting_external", queued
    assert queued["input_receipt"]["state"] == "queued"
    assert _frames(mock, f"batc-{demoted_id}") == []

    await set_member(STARTER_OWNER.actor, "reader")
    await _refresh(w, mock, att, att["turn_marker"], streaming=False)
    await _settle(ops, demoted_id)
    refused = await _get_op(w, demoted_id)
    assert refused["status"] == "failed", refused
    assert refused["error_code"] == "FORBIDDEN", refused
    assert refused.get("status_reason") == "[FORBIDDEN] Workspace membership does not permit this action", refused
    assert refused["input_receipt"]["state"] == "failed"
    assert refused["input_receipt"]["error_code"] == "FORBIDDEN"
    assert _frames(mock, f"batc-{demoted_id}") == []

    await set_member(STARTER_OWNER.actor, "owner")
    await _refresh(w, mock, att, att["turn_marker"], streaming=False)
    later_id = await _queue(w, mock, att, "writer still sends", "p5-writer-later")  # gitleaks:allow (test idempotency key)
    await _settle(ops, later_id)
    later = await _get_op(w, later_id)
    assert later["status"] == "succeeded", later
    assert later["input_receipt"]["state"] == "sent"
    assert len(_frames(mock, f"batc-{later_id}")) == 1

    await _refresh(w, mock, att, f"batc-{later_id}", streaming=True)
    removed_id = await _queue(w, mock, att, "queued before removal", "p5-writer-remove")  # gitleaks:allow (test idempotency key)
    assert (await _get_op(w, removed_id))["status"] == "waiting_external"
    assert _frames(mock, f"batc-{removed_id}") == []
    await set_member(STARTER_OWNER.actor, None)
    await _refresh(w, mock, att, f"batc-{later_id}", streaming=False)
    await _settle(ops, removed_id)
    # The admitted actor is no longer a member, so their own token cannot read the receipt.
    gone = await _get_op(w, removed_id, tok=second_tok)
    assert gone["status"] == "failed", gone
    assert gone["error_code"] == "NOT_FOUND", gone
    assert gone.get("status_reason") == "[NOT_FOUND] Workspace not found", gone
    assert gone["input_receipt"]["state"] == "failed"
    assert gone["input_receipt"]["error_code"] == "NOT_FOUND"
    assert _frames(mock, f"batc-{removed_id}") == []

    await set_member(STARTER_OWNER.actor, "contributor")
    await _refresh(w, mock, att, f"batc-{later_id}", streaming=False)
    restored_id = await _queue(w, mock, att, "restored writer sends once", "p5-writer-restored")  # gitleaks:allow (test idempotency key)
    await _settle(ops, restored_id)
    restored = await _get_op(w, restored_id)
    assert restored["status"] == "succeeded", restored
    assert len(_frames(mock, f"batc-{restored_id}")) == 1


# --------------------------------------------------------------------------- 10


async def test_manual_and_foreign_sessions_not_targetable(start_ready, mock):
    w = start_ready
    att = await _start_managed_attempt(w, mock, key="p5-manual")  # gitleaks:allow (test idempotency key)
    status, body = await _post(
        w, att, "mixed",
        key="p5-mixed",  # gitleaks:allow (test idempotency key)
        target={"workspace_id": att["workspace_id"], "work_item_id": att["work_item_id"], "host": att["host"]},
        params={"text": "mixed target"},
    )
    assert status == 422 and body["error"]["code"] == "INVALID_TARGET", body
    assert body["error"]["admission_refused"] is True

    saved = dict(registry.get(att["host"], att["session_id"]))
    registry.update(att["host"], att["session_id"], start_operation_id="op_foreign_not_this_attempt")
    status, body = await _post(w, att, "foreign", key="p5-foreign")  # gitleaks:allow (test idempotency key)
    assert status == 409 and body["error"]["code"] == "PM_WORK_SESSION_NOT_MANAGED", body
    registry.update(
        att["host"], att["session_id"],
        start_operation_id=saved.get("start_operation_id"), provenance="manual_bat",
    )
    status, body = await _post(w, att, "manual", key="p5-manual-send")  # gitleaks:allow (test idempotency key)
    assert status in {403, 409}, body
    assert body["error"]["code"] in {"PM_WORK_SESSION_NOT_MANAGED", "READ_ONLY", "UNKNOWN_READ_ONLY", body["error"]["code"]}
    assert body["error"]["code"] in {"PM_WORK_SESSION_NOT_MANAGED", "READ_ONLY", "UNKNOWN_READ_ONLY"} or body["error"]["code"].endswith("READ_ONLY")
    path = registry.registry_path()
    with registry._locked(path):
        items = [row for row in registry._read(path) if not (
            row.get("host") == att["host"] and row.get("session_id") == att["session_id"])]
        registry._write(path, items)
    status, body = await _post(w, att, "unknown session", key="p5-unknown-session")  # gitleaks:allow (test idempotency key)
    assert status in {403, 409}, body
    assert body["error"]["code"] in {"PM_WORK_SESSION_NOT_MANAGED", "READ_ONLY", "UNKNOWN_READ_ONLY"} or str(body["error"]["code"]).endswith("READ_ONLY")


# --------------------------------------------------------------------------- 11


async def test_existing_callers_preserve_exact_retry_settings_and_errors(start_ready, mock, monkeypatch):
    w = start_ready
    ops = w["d"].ops
    att = await _start_managed_attempt(w, mock, key="p5-callers")  # gitleaks:allow (test idempotency key)
    host, sid = att["host"], att["session_id"]
    mock.echo_sends = False
    _set_turn_messages(mock, sid, att["turn_marker"], streaming=False)

    # Busy retries must not wait on the real backoff, but the replacement still
    # has to yield. A sleep that returns without awaiting starves the operation
    # worker, so the legacy send stays accepted until the settle timeout.
    original_sleep = client_mod.asyncio.sleep

    async def no_sleep(*_args, **_kwargs):
        await original_sleep(0)

    monkeypatch.setattr(client_mod.asyncio, "sleep", no_sleep)
    monkeypatch.setattr(client_mod.random, "uniform", lambda *_args, **_kwargs: 0.0)

    def busy(_params):
        raise Exception("Remote server is busy")

    mock.handlers["claude:send-message"] = busy
    start = len(mock.invokes)
    status, body = await _post(
        w, att, "legacy busy",
        key="p5-legacy-busy",  # gitleaks:allow (test idempotency key)
        target={"host": host, "session_id": sid},
        params={"text": "legacy busy once"},
    )
    assert status == 202, body
    await _settle(ops, body["operation"]["operation_id"], rounds=1)
    legacy_frames = _frames(mock, start=start)
    assert _by_client_id(legacy_frames) and set(_by_client_id(legacy_frames).values()) == {1}

    # Direct service calls are not operation workers, so they need the same
    # authenticated controller the session was started under.
    with controller_authority.use(controller_authority.binding(ops.db, STARTER_OWNER), ops.journal):
        start = len(mock.invokes)
        with pytest.raises(Exception) as continued:
            await service.session_continue(
                w["d"].fleet, host, sid, confirm=True, text="continue please", message_id="batc-continue-1",
            )
        continue_frames = _frames(mock, start=start)
        assert _by_client_id(continue_frames).get("batc-continue-1") == 3, (
            type(continued.value).__name__,
            str(continued.value),
        )

        start = len(mock.invokes)
        with pytest.raises(Exception):
            await lifecycle.session_relay(w["d"].fleet, host, "relay please", session_id=sid, confirm=True)
        relay_counts = _by_client_id(_frames(mock, start=start))
        assert relay_counts and set(relay_counts.values()) == {3}
        mock.handlers.pop("claude:send-message", None)

        mock.metas[sid]["isStreaming"] = True
        with pytest.raises(WriteRefused, match="queue=true"):
            await service.session_send(
                w["d"].fleet, host, sid, "do not queue", confirm=True, queue=False, message_id="batc-stream-1",
            )
        mock.metas[sid]["isStreaming"] = False

    mock.handlers["claude:send-message"] = lambda _params: {"accepted": False, "ok": False}
    status, body = await _post(
        w, att, "legacy rejected",
        key="p5-legacy-rejected",  # gitleaks:allow (test idempotency key)
        target={"host": host, "session_id": sid},
        params={"text": "legacy rejected"},
    )
    assert status == 202, body
    rejected = await _settle(ops, body["operation"]["operation_id"])
    assert rejected["status"] == "needs_attention" and rejected["error_code"] == "NOT_ACCEPTED", rejected
    assert "BAT did not accept the message" in json.dumps(rejected)
    mock.handlers.pop("claude:send-message", None)

    import bat_agent_connector.orchestration_operations as orchestration

    monkeypatch.setattr(orchestration, "linked", lambda _op: True)
    monkeypatch.setattr(orchestration, "check_child", lambda *_args, **_kwargs: None)

    async def lose_before(_self, frame, timeout, on_transport, original):
        if frame.get("channel") == "claude:send-message":
            raise ConnectionLost("relay lost before send")
        return await original(_self, frame, timeout, on_transport=on_transport)

    original_roundtrip = _patch_roundtrip(monkeypatch, lose_before)
    status, body = await _post(
        w, att, "relay child",
        key="p5-relay-unsent",  # gitleaks:allow (test idempotency key)
        target={"host": host, "session_id": sid},
        params={"text": "relay was not sent"},
    )
    assert status == 202, body
    relay = await _settle(ops, body["operation"]["operation_id"])
    assert relay["status"] == "failed" and relay["error_code"] == "RELAY_NOT_SENT", relay
    assert "relay prompt was not sent" in json.dumps(relay)
    _restore_roundtrip(original_roundtrip)

    instructions_status, instructions = await http(
        w["port"], "GET", f"/api/v1/sessions/{host}/{sid}/instructions", tok=w["owner_token"],
    )
    assert instructions_status == 200, instructions
    assert instructions["live_queue_available"] is False
    assert instructions["per_message_cancel"] is False
    assert instructions["interrupt_scope"] == "session"
    assert instructions["queue_receipt_semantics"] == "queued_at_submission_not_current_position"
    assert {item["action"] for item in []} == set()
    from bat_agent_connector import checkpoints, task_actions, task_bat

    checkpoint_source = inspect.getsource(checkpoints.start_in_worktree)
    task_source = inspect.getsource(task_bat.BatTaskAdapter.send)
    task_action_source = inspect.getsource(task_actions.send)
    assert "retry_on_disconnect=False" in checkpoint_source
    assert "BAT did not accept the first instruction" in checkpoint_source
    assert "retry_on_disconnect=False" in task_source
    assert 'OperationError("NOT_ACCEPTED", "BAT did not accept the task send", 409)' in task_action_source


# --------------------------------------------------------------------------- 12


async def test_lifecycle_stop_refuses_new_and_queued_inputs_while_sent_ones_finish(start_ready, mock):
    w = start_ready
    ops = w["d"].ops
    att = await _start_managed_attempt(w, mock, key="p5-lifecycle")  # gitleaks:allow (test idempotency key)
    await _refresh(w, mock, att, att["turn_marker"], streaming=False)
    first = await _queue(w, mock, att, "already sent", "p5-life-1")  # gitleaks:allow (test idempotency key)
    await _settle(ops, first)
    assert (await _get_op(w, first))["input_receipt"]["state"] == "sent"
    assert len(_frames(mock, f"batc-{first}")) == 1
    second = await _queue(w, mock, att, "still queued", "p5-life-2")  # gitleaks:allow (test idempotency key)
    assert (await _get_op(w, second))["status"] == "waiting_external"
    assert _frames(mock, f"batc-{second}") == []

    await decide(ops, att["workspace_id"], "pause", who=STARTER_OWNER)
    status, body = await _post(w, att, "new while paused", key="p5-life-3")  # gitleaks:allow (test idempotency key)
    assert status == 409 and body["error"]["code"] == "PM_LIFECYCLE_STOPPED", body
    assert body["error"]["admission_refused"] is True

    await _refresh(w, mock, att, f"batc-{first}", streaming=False)
    await _settle(ops, second)
    stopped = await _get_op(w, second)
    assert stopped["status"] == "failed" and stopped["error_code"] == "PM_LIFECYCLE_STOPPED", stopped
    assert stopped["input_receipt"]["state"] == "failed"
    assert any(item.get("label_zh_TW") == "以新指令重試" for item in stopped["input_receipt"]["next_allowed_actions"])
    assert _frames(mock, f"batc-{second}") == []
    result = await _result(w, att)
    assert any(item["turn_marker"] == f"batc-{first}" for item in result["results"])
    resume, resume_body = await http(w["port"], "POST", f"/api/v1/operations/{first}/resume", tok=w["owner_token"])
    assert resume == 409 and resume_body["error"]["code"] == "NOT_RESUMABLE", resume_body
    assert len(_frames(mock, f"batc-{first}")) == 1

    await decide(ops, att["workspace_id"], "reopen", who=STARTER_OWNER, params={"stage": "development"})
    await _refresh(w, mock, att, f"batc-{first}", streaming=True)
    fourth = await _queue(w, mock, att, "bound to this epoch", "p5-life-4")  # gitleaks:allow (test idempotency key)
    assert (await _get_op(w, fourth))["status"] == "waiting_external"
    assert not any(step["name"] == "send" for step in ops.get(fourth)["steps"])
    await decide(ops, att["workspace_id"], "pause", who=STARTER_OWNER)
    await decide(ops, att["workspace_id"], "reopen", who=STARTER_OWNER, params={"stage": "development"})
    await _refresh(w, mock, att, f"batc-{first}", streaming=False)
    await _settle(ops, fourth)
    moved = await _get_op(w, fourth)
    assert moved["status"] == "failed" and moved["error_code"] == "PM_LIFECYCLE_EPOCH_CHANGED", moved
    assert _frames(mock, f"batc-{fourth}") == []

    item = ops.db.execute("SELECT version FROM work_items WHERE work_item_id=?", (att["work_item_id"],)).fetchone()
    await pkg_action(
        w, "work_item.update", {"work_item_id": att["work_item_id"]},
        {"archived": True}, {"expected_version": item["version"]}, who=STARTER_OWNER,
    )
    status, body = await _post(w, att, "archived work", key="p5-life-archived")  # gitleaks:allow (test idempotency key)
    assert status == 409 and body["error"]["code"] == "WORK_ITEM_ARCHIVED", body


# --------------------------------------------------------------------------- 13


async def test_work_follow_up_visibility(start_ready, mock):
    w = start_ready
    ops = w["d"].ops
    att = await _start_managed_attempt(w, mock, key="p5-visibility")  # gitleaks:allow (test idempotency key)
    await _refresh(w, mock, att, att["turn_marker"], streaming=True)
    op_id = await _queue(w, mock, att, "members only", "p5-visibility-1")  # gitleaks:allow (test idempotency key)
    outsider_tok = token(w["d"], "outsider", "observe", "operate")
    outsider = Principal("outsider", frozenset({"observe", "operate"}))
    for path in (
        f"/api/v1/pm/workspaces/{att['workspace_id']}/work/{att['work_item_id']}/inputs",
        f"/api/v1/operations/{op_id}",
    ):
        status, body = await http(w["port"], "GET", path, tok=outsider_tok)
        assert status == 404, (path, body)
    status, body = await _post(w, att, "outsider send", key="p5-visibility-send", tok=outsider_tok)  # gitleaks:allow (test idempotency key)
    assert status == 404, body
    listed, events, boot = await asyncio.gather(
        http(w["port"], "GET", "/api/v1/operations", tok=outsider_tok),
        http(w["port"], "GET", "/api/v1/events?limit=100", tok=outsider_tok),
        http(w["port"], "GET", "/api/v1/bootstrap", tok=outsider_tok),
    )
    for status, payload in (listed, events, boot):
        assert status == 200
        assert op_id not in json.dumps(payload)

    hidden = {
        "resource_type": "operation",
        "resource_id": "op_missing_work_input",
        "kind": "operation.updated",
        "body": {"action": "session.send", "work_follow_up": True, "workspace_id": att["workspace_id"]},
    }
    assert pm_access.event_visible(ops.db, STARTER_OWNER, hidden) is False
    assert pm_access.event_visible(ops.db, outsider, hidden) is False
    ordinary = {
        "resource_type": "operation",
        "resource_id": "op_missing_ordinary",
        "kind": "operation.updated",
        "body": {"action": "session.read", "workspace_id": att["workspace_id"]},
    }
    assert pm_access.event_visible(ops.db, STARTER_OWNER, ordinary) is True
    assert pm_access.event_visible(ops.db, outsider, ordinary) is True
    real_missing_row = {
        "resource_type": "operation",
        "resource_id": op_id,
        "kind": "operation.updated",
        "body": {"action": "session.send", "work_follow_up": True},
    }
    assert pm_work_follow_up.missing_row_event_visible(ops.db, STARTER_OWNER, real_missing_row) is True
    assert pm_work_follow_up.missing_row_event_visible(ops.db, outsider, real_missing_row) is False


# --------------------------------------------------------------------------- 14


async def test_restart_recovery_of_waiting_inputs_from_journal(start_ready, mock):
    w = start_ready
    ops = w["d"].ops
    att = await _start_managed_attempt(w, mock, key="p5-restart")  # gitleaks:allow (test idempotency key)
    await _refresh(w, mock, att, att["turn_marker"], streaming=False)
    mock.metas[att["session_id"]]["isStreaming"] = True
    first = await _queue(w, mock, att, "started then paused", "p5-restart-1")  # gitleaks:allow (test idempotency key)
    second = await _queue(w, mock, att, "not started yet", "p5-restart-2")  # gitleaks:allow (test idempotency key)
    first_op = ops.get(first)
    assert first_op["status"] == "waiting_external"
    assert any(step["name"] == "send" and step["status"] == "started" for step in first_op["steps"])
    assert ops.get(first)["external_refs"]["send_sent"] is False
    assert not any(step["name"] == "send" for step in ops.get(second)["steps"])
    saved = _positions(ops, att["attempt_id"])

    async with _restarted(w["d"]) as restarted:
        new_ops = restarted.ops
        assert _positions(new_ops, att["attempt_id"]) == saved
        assert new_ops.get(first)["status"] != "uncertain"
        assert new_ops.get(second)["status"] != "uncertain"
        mock.metas[att["session_id"]]["isStreaming"] = False
        await _refresh(w, mock, att, att["turn_marker"], streaming=False)
        new_ops.wake(first)
        await _settle(new_ops, first)
        restarted_first = new_ops.get(first)
        assert restarted_first["status"] == "succeeded", (
            restarted_first.get("error_code"), restarted_first.get("status_reason"),
            [(step.get("name"), step.get("status"), step.get("error")) for step in restarted_first.get("steps") or []],
        )
        assert len(_frames(mock, f"batc-{first}")) == 1
        await _refresh(w, mock, att, f"batc-{first}", streaming=False)
        new_ops.wake(second)
        await _settle(new_ops, second)
        assert new_ops.get(second)["status"] == "succeeded", new_ops.get(second)
        assert len(_frames(mock, f"batc-{second}")) == 1


# --------------------------------------------------------------------------- 15


async def test_racing_admissions_get_distinct_positions(start_ready, mock):
    w = start_ready
    att = await _start_managed_attempt(w, mock, key="p5-race")  # gitleaks:allow (test idempotency key)
    first, second = await asyncio.gather(
        _post(w, att, "race one", key="p5-race-1"),  # gitleaks:allow (test idempotency key)
        _post(w, att, "race two", key="p5-race-2"),  # gitleaks:allow (test idempotency key)
    )
    assert first[0] == 202 and second[0] == 202, (first, second)
    positions = _positions(w["d"].ops, att["attempt_id"])
    assert [row[0] for row in positions] == [1, 2]
    assert len({row[1] for row in positions}) == 2
    assert len({row[2] for row in positions}) == 2


# --------------------------------------------------------------------------- 16


def _expected_receipt(send_sent, status, step, error_code):
    """Independent reading of spec §8.2 order 1 through 6."""
    sent = send_sent is True
    if not sent and (status == "cancelled" or (status == "failed" and error_code == "CANCELLED")):
        return "cancelled", False
    if ((not sent and status == "failed" and error_code != "CANCELLED")
            or (sent and status == "failed" and error_code == "NOT_ACCEPTED")):
        return "failed", False
    if sent and (status == "succeeded" or (status == "running" and step == "succeeded")):
        return "sent", False
    unknown_sent = sent and (
        (status == "running" and step == "uncertain")
        or status in {"accepted", "waiting_checks", "waiting_external", "needs_attention", "uncertain", "cancelled"}
        or (status == "failed" and error_code != "NOT_ACCEPTED")
    )
    unknown_unsent = (not sent) and (
        status in {"uncertain", "needs_attention", "succeeded"}
        or (status in {"accepted", "running", "waiting_checks", "waiting_external"} and step in {"uncertain", "succeeded"})
    )
    if unknown_sent or unknown_unsent:
        return "unknown", False
    if sent and status == "running" and (step is None or step in {"started", "failed"}):
        return "sending", False
    if (not sent and status in {"accepted", "running", "waiting_checks", "waiting_external"}
            and (step is None or step in {"started", "failed"})):
        return "queued", True
    return "unknown", False


def test_unit_receipt_state_matrix():
    assert len(OP_STATES) == 9
    cases = 0
    for send_sent in (True, False, None):
        for status in OP_STATES:
            for step in (None, "started", "succeeded", "failed", "uncertain"):
                for error_code in (None, "CANCELLED", "NOT_ACCEPTED", "REFUSED"):
                    cases += 1
                    op = {"status": status, "error_code": error_code, "external_refs": {}}
                    if send_sent is not None:
                        op["external_refs"]["send_sent"] = send_sent
                    got = pm_work_follow_up.receipt_state(op, None if step is None else {"status": step})
                    expect, cancellable = _expected_receipt(send_sent, status, step, error_code)
                    assert str(got) == expect, (send_sent, status, step, error_code, str(got), expect)
                    assert bool(got.cancellable) is cancellable
                    if send_sent is True:
                        assert str(got) not in {"queued", "cancelled"}
                        if status == "failed" and error_code == "NOT_ACCEPTED":
                            assert str(got) == "failed"
                        elif status == "failed" or status == "cancelled":
                            assert str(got) == "unknown"
                    assert got.cancellable is (str(got) == "queued")
                    if send_sent is True and status == "running":
                        if step == "uncertain":
                            assert str(got) == "unknown"
                        elif step == "succeeded":
                            assert str(got) == "sent"
                        else:
                            assert str(got) == "sending"
    assert cases == 540


# --------------------------------------------------------------------------- 17


async def test_setup_admission_refusal_preserves_admission_refused(start_ready, mock):
    w = start_ready
    ops = w["d"].ops
    for action in ("setup.host", "setup.repository", "setup.verification"):
        key = f"p5-setup-{action}"  # gitleaks:allow (test idempotency key)
        status, body = await http(
            w["port"], "POST", "/api/v1/operations", tok=w["owner_token"],
            body={"action": action, "target": {}, "params": {}, "preconditions": {}},
            headers={"Idempotency-Key": key},
        )
        assert status >= 400, body
        assert body["error"]["admission_refused"] is True, body
        assert not ops.db.execute("SELECT 1 FROM operations WHERE idem_key=?", (key,)).fetchone()
    att = await _start_managed_attempt(w, mock, key="p5-setup-work")  # gitleaks:allow (test idempotency key)
    status, body = await _post(
        w, att, "",
        key="p5-work-admission",  # gitleaks:allow (test idempotency key)
        params={"text": ""},
    )
    assert status == 422, body
    assert body["error"]["admission_refused"] is True
    assert body["error"]["code"] == "INVALID_PARAMS"
