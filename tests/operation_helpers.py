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


async def wait_operation_attempts(ops, active, *, timeout=60, started=None, clock=None, wait=None, description=None):
    """Wait for captured workers; each must make its own durable step progress.

    The stall bound protects the suite from a hung attempt, not from CPU time
    used by a progressing operation. Callers retain scheduling and teardown.
    """
    clock = clock or asyncio.get_running_loop().time
    wait = wait or asyncio.wait
    started = clock() if started is None else started

    def progress(oid):
        return tuple(tuple(row) for row in ops.db.execute(
            'SELECT seq,status FROM operation_steps WHERE operation_id=? ORDER BY seq', (oid,)))

    receipts = {oid: progress(oid) for oid, _ in active}
    changed = dict.fromkeys(receipts, started)
    pending = {task for _, task in active}
    while pending:
        remaining = min(timeout - (clock() - changed[oid]) for oid, task in active if task in pending)
        _, pending = await wait(pending, timeout=max(0, min(1, remaining)))
        now = clock()
        for oid, task in active:
            if task not in pending:
                continue
            current = progress(oid)
            if current != receipts[oid]:
                receipts[oid], changed[oid] = current, now
            if now - changed[oid] >= timeout:
                outcome = ops.get(oid)
                label = description or f"operation {oid}" + (
                    " worker" if outcome['status'] not in {'accepted', 'running'} else "")
                raise AssertionError(f"{label} did not settle within {timeout}s without persisted progress\n"
                                     + worker_details(ops))
    outcomes = []
    for oid, task in active:
        task.result()  # A persisted status does not prove worker cleanup finished.
        outcome = ops.get(oid)
        assert outcome['status'] not in {'accepted', 'running'}, outcome
        outcomes.append((outcome['action'], outcome['status']))
    return outcomes


async def settle_operations(ops, *, timeout=60):
    """Run due operations and wait for persisted results and worker completion.

    Uncertain/waiting attempts return without forcing their future retries. Yield after scheduling so a retry
    enters running before wait() observes the previous attempt's uncertain status.
    timeout is a per-worker guard against missing durable step progress, not
    an overall latency assertion. Product budgets remain independently checked.
    On failure, stop and join this service's workers while its fixture journal is
    still open. This does not cancel durable operations or change their receipts.
    """
    loop = asyncio.get_running_loop()
    try:
        while True:
            await ops.run_due()
            active = [(op_id, task) for op_id, task in ops._active.items() if not task.done()]
            if not active:
                return
            await asyncio.sleep(0)
            await wait_operation_attempts(ops, active, timeout=timeout, clock=loop.time, wait=asyncio.wait)
    except BaseException:
        await stop_workers(ops)
        raise
