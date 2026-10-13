"""Wait for operation outcomes rather than a best-effort worker drain deadline."""

import asyncio
from pathlib import Path


def worker_details(ops):
    """Capture suspended frames before cancellation, without request data or locals."""
    details = []
    for op_id, task in tuple(ops._active.items()):
        op = ops.get(op_id)
        steps = [(step["name"], step["status"]) for step in op["steps"]]
        frames, awaiting = [], task.get_coro()
        for _ in range(32):
            frame = getattr(awaiting, "cr_frame", None) or getattr(awaiting, "gi_frame", None)
            if frame is None:
                break
            code = frame.f_code
            frames.append(f"{Path(code.co_filename).name}:{frame.f_lineno}:{code.co_name}")
            awaiting = getattr(awaiting, "cr_await", None) or getattr(awaiting, "gi_yieldfrom", None)
        details.append(f"{op_id}: action={op['action']} status={op['status']} attempts={op['attempts']} "
                       f"steps={steps!r} worker_done={task.done()} await={' -> '.join(frames)}")
    return "\n".join(details)


# Retain compatibility for fixtures that imported the original private helper.
_worker_details = worker_details


async def stop_workers(ops):
    """Cancel and join this service's pending workers before its journal closes.

    This stops in-memory attempts only; durable operations and their original
    receipts remain available for normal reconciliation.
    """
    pending = [task for task in ops._active.values() if not task.done()]
    for task in pending:
        task.cancel()
    await asyncio.gather(*pending, return_exceptions=True)


async def settle_operations(ops, *, timeout=60):
    """Run due operations and wait for each attempt's persisted status and worker completion.

    Uncertain/waiting attempts return without forcing their future retries. Yield after scheduling so a retry
    enters running before wait() observes the previous attempt's uncertain status.
    On failure, stop and join this service's workers while its fixture journal is
    still open. This does not cancel durable operations or change their receipts.
    """
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    try:
        while True:
            await ops.run_due()
            active = [(op_id, task) for op_id, task in ops._active.items() if not task.done()]
            if not active:
                return
            await asyncio.sleep(0)
            for op_id, task in active:
                outcome = await ops.wait(op_id, timeout=max(0, deadline - loop.time()))
                assert outcome["status"] not in {"accepted", "running"}, (
                    f"operation {op_id} did not settle within {timeout}s: {outcome['status']}\n"
                    + worker_details(ops)
                )
                try:
                    await asyncio.wait_for(asyncio.shield(task), timeout=max(0, deadline - loop.time()))
                except asyncio.TimeoutError:
                    raise AssertionError(f"operation {op_id} worker did not settle within {timeout}s\n"
                                         + worker_details(ops)) from None
    except BaseException:
        await stop_workers(ops)
        raise
