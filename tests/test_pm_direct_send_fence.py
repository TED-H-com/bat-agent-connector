import os

import pytest

from bat_agent_connector import safety
from tests import test_pm_attempt_results as results
from tests.operation_helpers import settle_operations
from tests.test_pm_work_follow_up import _patch_roundtrip
from tests.test_pm_work_start import STARTER_OWNER
from tests.test_pm_workspace_wip import read
from tests.test_pm_workspace_wip_lineage import closed_attempt

world = results.world
pm = results.pm
ready = results.ready
start_ready = results.start_ready


@pytest.mark.parametrize("when", ["before", "after"])
async def test_audit_open_failure_distinguishes_transport(start_ready, mock, monkeypatch, when):
    w = start_ready
    ops = w["d"].ops
    att, _ = await closed_attempt(w, mock)
    after = False
    old_open = safety.open_private

    def fail_open(path, flags, *args, **kw):
        if flags & os.O_APPEND and ((when == "before") or after):
            raise PermissionError("Synthetic audit open failure")
        return old_open(path, flags, *args, **kw)

    async def transported(client, frame, timeout, on_transport, original):
        nonlocal after
        value = await original(client, frame, timeout, on_transport=on_transport)
        if frame.get("channel") == "claude:send-message":
            after = True
        return value

    monkeypatch.setattr(safety, "open_private", fail_open)
    _patch_roundtrip(monkeypatch, transported)
    start = len(mock.invokes)
    op, _ = ops.create(
        STARTER_OWNER,
        action="session.send",
        target={"host": "h1", "session_id": att["session_id"]},
        params={"text": "Synthetic audit outcome"},
        preconditions={},
        idempotency_key="audit-" + when,
    )
    await settle_operations(ops)
    op = ops.get(op["operation_id"])
    frames = sum(f["channel"] == "claude:send-message" for f in mock.invokes[start:])
    value = read(ops, w["target"], STARTER_OWNER)
    facts = {
        "when": when,
        "status": op["status"],
        "error": op["error_code"],
        "frames": frames,
        "used": value["used"],
        "coverage": value["coverage"],
        "steps": [
            dict(r)
            for r in ops.db.execute(
                "SELECT name,status,request,response,error FROM operation_steps WHERE operation_id=?",
                (op["operation_id"],),
            )
        ],
    }
    print(facts)
    assert op["external_refs"].get("direct_send_dispatch") is (when == "after")
    assert frames == (0 if when == "before" else 1)
    assert value["used"] == (0 if when == "before" else None), (
        "REFUSED after an accepted frame is not unsent evidence"
    )


@pytest.mark.parametrize(
    "case", ["rate_hour", "rate_interval", "reused_id", "blank_text", "oversize", "write_disabled"]
)
async def test_other_predispatch_refusals(start_ready, mock, monkeypatch, case):
    from bat_agent_connector import service

    w = start_ready
    ops = w["d"].ops
    att, _ = await closed_attempt(w, mock)
    params = {"text": "Synthetic refusal"}
    if case == "rate_hour":
        w["d"].fleet.config.safety.max_writes_per_hour = 0
    if case == "rate_interval":
        w["d"].fleet.config.safety.write_min_interval_s = 999999
    if case == "reused_id":
        params["message_id"] = att["turn_marker"]
    if case == "blank_text":
        params["text"] = " "
    if case == "oversize":
        params["text"] = "x" * (service.MAX_PROMPT_CHARS + 1)
    start = len(mock.invokes)
    if case in {"blank_text", "oversize"}:
        from bat_agent_connector.operations import OperationError

        with pytest.raises(OperationError):
            ops.create(
                STARTER_OWNER,
                action="session.send",
                target={"host": "h1", "session_id": att["session_id"]},
                params=params,
                preconditions={},
                idempotency_key="pre-" + case,
            )
        assert not any(f["channel"] == "claude:send-message" for f in mock.invokes[start:])
        assert read(ops, w["target"], STARTER_OWNER)["used"] == 0
        return
    op, _ = ops.create(
        STARTER_OWNER,
        action="session.send",
        target={"host": "h1", "session_id": att["session_id"]},
        params=params,
        preconditions={},
        idempotency_key="pre-" + case,
    )
    if case == "write_disabled":
        w["d"].ops.context["fleet"].read_only = True
    await settle_operations(ops)
    done = ops.get(op["operation_id"])
    value = read(ops, w["target"], STARTER_OWNER)
    frames = sum(f["channel"] == "claude:send-message" for f in mock.invokes[start:])
    facts = {
        "case": case,
        "status": done["status"],
        "error": done["error_code"],
        "steps": done["steps"],
        "frames": frames,
        "value": value,
    }
    print(facts)
    assert frames == 0
    assert value["used"] == 0


@pytest.mark.parametrize("legacy_fence", [None, False, True])
async def test_legacy_refused_without_transport_fence_is_unknown(start_ready, mock, legacy_fence):
    import json
    w = start_ready
    ops = w["d"].ops
    att, _ = await closed_attempt(w, mock)
    sid = att["session_id"]
    mock.metas[sid]["isStreaming"] = True
    mock.states[sid]["isStreaming"] = True
    op, _ = ops.create(
        STARTER_OWNER,
        action="session.send",
        target={"host": "h1", "session_id": sid},
        params={"text": "Synthetic legacy refusal"},
        preconditions={},
        idempotency_key="legacy-refusal",
    )
    await settle_operations(ops)
    # Synthetic pre-fence durable history; REFUSED alone cannot prove dispatch phase.
    with ops.journal.tx():
        ops.db.execute(
            "UPDATE operations SET external_refs=json_set(json_remove(external_refs, '$.direct_send_dispatch'), "
            "'$.send_sent', json(?)) WHERE operation_id=?",
            (json.dumps(legacy_fence), op["operation_id"]),
        )
    value = read(ops, w["target"], STARTER_OWNER)
    assert value["used"] is None
    assert value["coverage"]["reasons"] == ["coverage_unavailable"]


async def test_direct_wait_after_dispatch_fence_keeps_generic_failure(start_ready, mock, monkeypatch):
    import json
    import sqlite3

    from bat_agent_connector.operations import Wait

    w = start_ready
    ops = w['d'].ops
    att, _ = await closed_attempt(w, mock)
    database = ops.db.execute('PRAGMA database_list').fetchone()[2]
    seen = []

    async def wait_after_fence(client, frame, timeout, on_transport, original):
        if frame.get('channel') != 'claude:send-message':
            return await original(client, frame, timeout, on_transport=on_transport)
        if on_transport:
            on_transport()
        # A separate disk connection proves the fence was committed before any
        # frame. A stop in this window must never mean the input was delivered.
        assert not ops.db.in_transaction
        with sqlite3.connect(database) as independent:
            refs = independent.execute('SELECT external_refs FROM operations WHERE operation_id=?',
                                      (op['operation_id'],)).fetchone()[0]
            seen.append(json.loads(refs).get('direct_send_dispatch'))
        raise Wait(status='waiting_external', reason='Synthetic wait after fence', delay_s=5)

    _patch_roundtrip(monkeypatch, wait_after_fence)
    start = len(mock.invokes)
    op, _ = ops.create(STARTER_OWNER, action='session.send',
        target={'host': 'h1', 'session_id': att['session_id']},
        params={'text': 'Synthetic fenced Wait'}, preconditions={}, idempotency_key='direct-fenced-wait')
    await settle_operations(ops)
    done = ops.get(op['operation_id'])
    assert done['status'] == 'failed', done
    assert done['error_code'] == 'INTERNAL'
    assert done['uncertain_tries'] == 0
    assert done['steps'][-1]['status'] == 'failed'
    assert 'send_sent' not in done['external_refs']
    assert seen == [True]
    assert not any(f['channel'] == 'claude:send-message' for f in mock.invokes[start:])
    value = read(ops, w['target'], STARTER_OWNER)
    assert value['used'] is None and value['coverage']['reasons'] == ['coverage_unavailable']


async def test_direct_fence_is_committed_before_websocket_write(start_ready, mock, monkeypatch):
    import json
    import sqlite3

    w = start_ready
    ops = w['d'].ops
    att, _ = await closed_attempt(w, mock)
    database = ops.db.execute('PRAGMA database_list').fetchone()[2]
    client = w['d'].fleet.client('h1')
    socket = client._ws
    original = socket.send
    seen = []

    async def inspect_frame(encoded, *args, **kwargs):
        frame = json.loads(encoded)
        if frame.get('channel') == 'claude:send-message':
            assert not ops.db.in_transaction
            with sqlite3.connect(database) as independent:
                refs = independent.execute('SELECT external_refs FROM operations WHERE operation_id=?',
                    (op['operation_id'],)).fetchone()[0]
                seen.append(json.loads(refs).get('direct_send_dispatch'))
            assert seen[-1] is True
        return await original(encoded, *args, **kwargs)

    monkeypatch.setattr(socket, 'send', inspect_frame)
    op, _ = ops.create(STARTER_OWNER, action='session.send',
        target={'host': 'h1', 'session_id': att['session_id']},
        params={'text': 'Synthetic fence ordering'}, preconditions={}, idempotency_key='direct-order')
    await settle_operations(ops)
    assert ops.get(op['operation_id'])['status'] == 'succeeded'
    assert seen == [True]
