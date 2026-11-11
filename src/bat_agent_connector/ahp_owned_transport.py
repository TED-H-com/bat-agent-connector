"""Unregistered AHP frame seam; production has no write adapter or caller.

The future central worker supplies synchronous current readback and a synchronous
socket enqueue. No create/turn RPC implementation, reconnect or retry lives here.
"""
from __future__ import annotations

import json

from . import ahp_owned_resources as ledger
from . import pm_source_lineage, resource_policy


async def guarded_frame(ctx, grant, frame, *, current, connect, semaphore, fresh_read):
    journal = ctx.service.journal
    if journal.db.in_transaction:
        ledger.refuse('AHP_FENCE_NOT_DURABLE')
    # Freeze the exact admitted bytes before any await; caller mutation cannot retarget them.
    payload = json.dumps(frame, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()
    frame = json.loads(payload)
    def check():
        resource_policy.check_ahp_grant(grant, ctx, frame, current=current)
    check()
    writer = await connect()
    check()
    async with semaphore:
        check()
        await fresh_read()
        check()
        creating = type(grant) is resource_policy.AhpCreateGrant
        if creating:
            await resource_policy.refresh_ahp_carrier(grant, ctx, frame, current=current)
        with journal.tx():
            check()
            with pm_source_lineage.execution(ctx.service, ctx.operation_id):
                pm_source_lineage.require_frame()
            if creating:
                ledger.create_sent(journal, grant.resource_id, frame, grant.connection)
            else:
                ledger.sent_fence(journal, grant.resource_id, grant.operation_id, grant.turn_id, ledger.digest(frame))
        # No await between the final check, durable commit and socket enqueue.
        # A crash, exception or cancellation from here cannot prove non-delivery.
        try:
            writer.write(payload)
            await writer.drain()
        except Exception:
            ledger.refuse('AHP_SENT_UNKNOWN')
