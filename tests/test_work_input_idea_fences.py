"""Exercise deferral against live mock-BAT send fences, not fabricated rows."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

from bat_agent_connector import operations as operations_mod
from bat_agent_connector.errors import ConnectionLost
from tests import test_pm_work_follow_up as follow
from tests.test_work_input_idea import defer

world, pm, ready, start_ready = follow.world, follow.pm, follow.ready, follow.start_ready


async def test_cancel_while_final_state_read_is_held_waits_for_original_worker(start_ready, mock, monkeypatch):
    w, ops = start_ready, start_ready['d'].ops
    att = await follow._start_managed_attempt(w, mock)
    await follow._refresh(w, mock, att, att['turn_marker'], streaming=False)
    original, started, release, _ = await follow._hold_second_state_read(monkeypatch, att['session_id'])
    source = None
    try:
        status, body = await follow._post(w, att, 'Retain before the last send check', key='defer-final-frame')
        assert status == 202, body
        source = body['operation']['operation_id']
        await ops.run_due()
        await asyncio.wait_for(started.wait(), 10)
        status, body = await defer(w, att, source)
        assert status == 202, body
        parent = body['operation']['operation_id']
        await ops._execute(parent)
        waiting = ops.get(parent)
        assert waiting['status'] == 'waiting_checks', waiting
        assert waiting['steps'][0]['status'] == 'succeeded'
        assert ops.db.execute('SELECT COUNT(*) FROM pm_threads WHERE operation_id=?', (parent,)).fetchone()[0] == 0
        release.set()
        await follow._settle(ops, source)
        assert (await follow._get_op(w, source))['input_receipt']['state'] == 'cancelled'
        # wake() only advances waiting_external. Drive this waiting_checks
        # retry at its recorded deadline, without changing durable row state.
        timer = operations_mod.time
        due = max(timer.time(), ops.get(parent)['next_run_at']) + 1
        with monkeypatch.context() as clock:
            clock.setattr(operations_mod, 'time', SimpleNamespace(time=lambda: due, monotonic=timer.monotonic))
            await follow.settle_operations(ops)
        completed = ops.get(parent)
        assert completed['status'] == 'succeeded', completed
        assert (await follow._get_op(w, source))['input_receipt']['state'] == 'cancelled'
        assert follow._frames(mock, f'batc-{source}') == []
    finally:
        release.set()
        follow._restore_roundtrip(original)
        if source:
            await follow._settle(ops, source)


async def test_unknown_delivery_keeps_its_fence_and_refuses_idea(start_ready, mock, monkeypatch):
    w, ops = start_ready, start_ready['d'].ops
    att = await follow._start_managed_attempt(w, mock)
    mock.echo_sends = False
    await follow._refresh(w, mock, att, att['turn_marker'], streaming=False)

    async def drop_after(client, frame, timeout, on_transport, original):
        if frame.get('channel') == 'claude:send-message':
            await original(client, frame, timeout, on_transport=on_transport)
            raise ConnectionLost('reply lost after transport fence')
        return await original(client, frame, timeout, on_transport=on_transport)

    original = follow._patch_roundtrip(monkeypatch, drop_after)
    try:
        status, body = await follow._post(w, att, 'Uncertain original', key='defer-unknown-source')
        assert status == 202, body
        source = body['operation']['operation_id']
        await follow._settle(ops, source, rounds=1)
        assert (await follow._get_op(w, source))['input_receipt']['state'] == 'unknown'
        status, body = await defer(w, att, source)
        assert status == 409 and body['error']['code'] == 'PM_WORK_INPUT_NOT_QUEUED', body
        assert ops.get(source)['external_refs']['send_sent'] is True
        assert not ops.get(source)['cancel_requested']
        assert len(follow._frames(mock, f'batc-{source}')) == 1
        assert ops.db.execute("SELECT COUNT(*) FROM operations WHERE action='pm.work_input.defer'").fetchone()[0] == 0
    finally:
        follow._restore_roundtrip(original)
