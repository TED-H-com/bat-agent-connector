"""A failed test must finish its workers before the fixture closes their journal."""

import asyncio
from types import SimpleNamespace

import pytest

from bat_agent_connector.api_auth import Principal
from bat_agent_connector.operations import ActionDef, OperationService
from bat_agent_connector.task_journal import Journal
from tests import operation_helpers as helpers
from tests import pm_joined_maintenance_helpers as maintenance
from tests.operation_helpers import _worker_details, settle_operations, stop_workers, worker_details

CALLER = Principal("fixture-operator", frozenset({"observe", "operate"}))


@pytest.fixture
async def ops(tmp_path):
    journal = Journal(tmp_path / "journal.db")
    service = OperationService(journal)
    try:
        yield service
    finally:
        # Also clean up when exercising the old, leaking helper in the red run.
        await stop_workers(service)
        journal.close()


@pytest.mark.parametrize("count", [1, 2])
@pytest.mark.parametrize("waiter", ["settle", "advance"])
async def test_wait_timeout_joins_workers_and_preserves_original_recovery(ops, monkeypatch, count, waiter):
    release, entered = asyncio.Event(), asyncio.Event()
    sent, finalized, reconciled = [], [], []

    async def run(ctx):
        async def awaiting_receipt():
            sent.append(ctx.operation_id)
            if len(sent) == count:
                entered.set()
            try:
                await release.wait()
            finally:
                # Cancellation cleanup must still have an open journal.
                finalized.append(ops.get(ctx.operation_id)["status"])

        async def read_back(request):
            reconciled.append(ctx.operation_id)
            return {"accepted": True}

        return await ctx.step("provider.receipt", awaiting_receipt, reconcile=read_back)

    ops.register(ActionDef("fixture.send", "operate", "Synthetic provider receipt", run))
    envelopes = [{"action": "fixture.send", "target": {}, "params": {},
                  "idempotency_key": f"original-{index}"} for index in range(count)]
    ids = [ops.create(CALLER, **envelope)[0]["operation_id"] for envelope in envelopes]
    with monkeypatch.context() as patch:
        if waiter == "advance":
            # Expire the existing 60-second wave after all synthetic workers
            # enter their receipt await; never replace a global stdlib clock.
            elapsed = [0]
            run_due = ops.run_due

            async def schedule_stuck_workers():
                await run_due()
                await asyncio.wait_for(entered.wait(), 5)
                elapsed[0] = 60

            patch.setattr(ops, "run_due", schedule_stuck_workers)
            patch.setattr(maintenance, "time", SimpleNamespace(monotonic=lambda: elapsed[0]))
        with pytest.raises(AssertionError, match="did not settle within") as caught:
            if waiter == "advance":
                await maintenance.advance({"d": SimpleNamespace(ops=ops)}, ops.get(ids[0]))
            else:
                await settle_operations(ops, timeout=0.01)

    assert finalized == ["running"] * count
    assert not ops._active
    message = str(caught.value)
    for oid in ids:
        assert oid in message
        saved = ops.get(oid)
        assert saved["status"] == "running"
        assert saved["steps"][0]["status"] == "started"
    assert "fixture.send" in message and "provider.receipt" in message
    assert "awaiting_receipt" in message
    assert "status=running" in message and "attempts=1" in message
    assert "worker_done=False" in message
    if waiter == "advance":
        assert "Repair attempt wave did not settle within 60s" in message
    assert _worker_details is worker_details

    for envelope, oid in zip(envelopes, ids):
        replay, fresh = ops.create(CALLER, **envelope)
        assert not fresh and replay["operation_id"] == oid
    await settle_operations(ops)
    assert sent == ids and reconciled == ids  # No second send on the original key.
    assert all(ops.get(oid)["status"] == "succeeded" for oid in ids)


@pytest.mark.parametrize("waiter", ["settle", "advance"])
async def test_cancelled_wait_joins_workers_before_returning(ops, waiter):
    entered, release = asyncio.Event(), asyncio.Event()
    finalized = []

    async def run(ctx):
        entered.set()
        try:
            await release.wait()
        finally:
            finalized.append(ops.get(ctx.operation_id)["status"])

    ops.register(ActionDef("fixture.wait", "operate", "Synthetic blocked read", run))
    op, _ = ops.create(CALLER, action="fixture.wait", target={}, params={}, idempotency_key="original")
    settling = asyncio.create_task(maintenance.advance({"d": SimpleNamespace(ops=ops)}, op)
                                   if waiter == "advance" else settle_operations(ops))
    try:
        await asyncio.wait_for(entered.wait(), 5)
    finally:
        settling.cancel()
        with pytest.raises(asyncio.CancelledError):
            await settling
    assert finalized == ["running"]
    assert not ops._active


async def test_settle_requires_worker_completion_after_persisted_outcome(ops, monkeypatch):
    release = asyncio.Event()
    finalized = []
    execute = ops._execute

    async def run(ctx):
        return {"accepted": True}

    async def waiting_after_receipt(oid):
        await execute(oid)
        try:
            await release.wait()
        finally:
            finalized.append(ops.get(oid)["status"])

    monkeypatch.setattr(ops, "_execute", waiting_after_receipt)
    ops.register(ActionDef("fixture.finish", "operate", "Synthetic worker cleanup", run))
    op, _ = ops.create(CALLER, action="fixture.finish", target={}, params={}, idempotency_key="original")
    with pytest.raises(AssertionError, match="worker did not settle within") as caught:
        await settle_operations(ops, timeout=0.01)
    assert "waiting_after_receipt" in str(caught.value)
    assert finalized == ["succeeded"]
    assert not ops._active
    assert ops.get(op["operation_id"])["result"] == {"accepted": True}


@pytest.mark.parametrize("stalled_sibling", [False, True])
@pytest.mark.parametrize("waiter", ["settle", "advance"])
async def test_advance_waits_for_persisted_progress_across_a_slow_wave(ops, monkeypatch, stalled_sibling, waiter):
    """A delayed scheduler can see new receipts after the wave's original bound."""
    elapsed = [0]
    release, recorded = asyncio.Event(), asyncio.Event()

    async def run(ctx):
        for index in range(2):
            await release.wait()
            release.clear()
            ctx.effect(f"receipt.{index}", lambda: {"accepted": True})
            recorded.set()
        return {"accepted": True}

    async def delayed_wait(tasks, *, timeout):
        # Simulate descheduling across a deadline while the provider records
        # a real receipt. No global clock or asyncio function is replaced.
        release.set()
        await recorded.wait()
        recorded.clear()
        elapsed[0] += 61
        return await asyncio.wait(tasks, timeout=0)

    ops.register(ActionDef("fixture.progress", "operate", "Synthetic receipts", run))
    op, _ = ops.create(CALLER, action="fixture.progress", target={}, params={}, idempotency_key="original")
    if stalled_sibling:
        async def stalled(ctx):
            await asyncio.Event().wait()
        ops.register(ActionDef("fixture.stalled", "operate", "Synthetic stalled worker", stalled))
        sibling, _ = ops.create(CALLER, action="fixture.stalled", target={}, params={}, idempotency_key="stalled")
    if waiter == "advance":
        monkeypatch.setattr(maintenance, "time", SimpleNamespace(monotonic=lambda: elapsed[0]))
        monkeypatch.setattr(maintenance, "asyncio", SimpleNamespace(wait=delayed_wait))
    else:
        # Replace this helper's dependency only, never a global stdlib clock.
        facade = SimpleNamespace(**{name: getattr(asyncio, name) for name in (
            "sleep", "gather", "wait_for", "shield", "TimeoutError")},
            get_running_loop=lambda: SimpleNamespace(time=lambda: elapsed[0]), wait=delayed_wait)
        monkeypatch.setattr(helpers, "asyncio", facade)
        original_wait = ops.wait

        async def progressed_wait(oid, *, timeout):
            await delayed_wait({ops._active[oid]}, timeout=timeout)
            return await original_wait(oid, timeout=0)

        monkeypatch.setattr(ops, "wait", progressed_wait)

    async def finish():
        if waiter == "advance":
            return await maintenance.advance({"d": SimpleNamespace(ops=ops)}, op, rounds=1)
        await settle_operations(ops)
        return ops.get(op["operation_id"])

    if stalled_sibling:
        with pytest.raises(AssertionError, match=sibling["operation_id"]):
            await finish()
        assert elapsed[0] == 61
        assert not ops._active
        assert ops.get(op["operation_id"])["steps"][0]["status"] == "succeeded"
        assert ops.get(sibling["operation_id"])["status"] == "running"
        return
    outcome = await finish()
    assert elapsed[0] == 122
    assert outcome["status"] == "succeeded" and outcome["result"] == {"accepted": True}
    assert [(step["name"], step["status"]) for step in outcome["steps"]] == [
        ("receipt.0", "succeeded"), ("receipt.1", "succeeded")]
    assert not ops._active
