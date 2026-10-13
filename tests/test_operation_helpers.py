"""A failed test must finish its workers before the fixture closes their journal."""

import asyncio

import pytest

from bat_agent_connector.api_auth import Principal
from bat_agent_connector.operations import ActionDef, OperationService
from bat_agent_connector.task_journal import Journal
from tests.operation_helpers import settle_operations

CALLER = Principal("fixture-operator", frozenset({"observe", "operate"}))


@pytest.fixture
async def ops(tmp_path):
    journal = Journal(tmp_path / "journal.db")
    service = OperationService(journal)
    try:
        yield service
    finally:
        # Also clean up when exercising the old, leaking helper in the red run.
        tasks = list(service._active.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        journal.close()


@pytest.mark.parametrize("count", [1, 2])
async def test_settle_timeout_joins_workers_and_preserves_original_recovery(ops, count):
    release = asyncio.Event()
    sent, finalized, reconciled = [], [], []

    async def run(ctx):
        async def awaiting_receipt():
            sent.append(ctx.operation_id)
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
    with pytest.raises(AssertionError, match="did not settle within") as caught:
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

    for envelope, oid in zip(envelopes, ids):
        replay, fresh = ops.create(CALLER, **envelope)
        assert not fresh and replay["operation_id"] == oid
    await settle_operations(ops)
    assert sent == ids and reconciled == ids  # No second send on the original key.
    assert all(ops.get(oid)["status"] == "succeeded" for oid in ids)


async def test_cancelled_settle_joins_workers_before_returning(ops):
    entered, release = asyncio.Event(), asyncio.Event()
    finalized = []

    async def run(ctx):
        entered.set()
        try:
            await release.wait()
        finally:
            finalized.append(ops.get(ctx.operation_id)["status"])

    ops.register(ActionDef("fixture.wait", "operate", "Synthetic blocked read", run))
    ops.create(CALLER, action="fixture.wait", target={}, params={}, idempotency_key="original")
    settling = asyncio.create_task(settle_operations(ops))
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
