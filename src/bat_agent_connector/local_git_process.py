"""Bounded Linux process transport for central-generated Git helpers.

This private prerequisite does not enable HostGitRunner's local dispatch. Script
ownership, repository guards and helper readiness must pass before that wiring.
"""
from __future__ import annotations

import asyncio
import contextlib
import math
import os
import signal
import sys

from .checkpoints import _LOCKED_CHECK, GIT_TIMEOUT_S
from .operations import AmbiguousOutcome, StepFailed
from .task_verifier import _group_alive

LIMIT = 1024 * 1024
MAX_TIMEOUT_S = 7200  # existing repository fetch configuration maximum
_ENV = {"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8",
        "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_OPTIONAL_LOCKS": "0", "GIT_TERMINAL_PROMPT": "0",
        "GIT_NO_REPLACE_OBJECTS": "1", "GIT_ATTR_NOSYSTEM": "1",
        "GIT_LFS_SKIP_SMUDGE": "1", "SSH_ASKPASS_REQUIRE": "never"}
_LAUNCH = b'batc-local-launch\n'
_PREFIX = 'IFS= read -r batc_local_launch && test "$batc_local_launch" = batc-local-launch || exit 125\n'


def _refuse(reason):
    return StepFailed('GIT_RUNNER_UNAVAILABLE', reason)


def _budget(value):
    value = GIT_TIMEOUT_S if value is None else value
    if type(value) not in (int, float) or not 0 < value <= MAX_TIMEOUT_S or not math.isfinite(value):
        raise _refuse('local_git_deadline_invalid')
    return float(value)


async def _stop(proc, readers):
    """Join only our new process group, including pipe holders after shell exit."""
    if proc.stdin is not None:
        proc.stdin.close()
    for reader in readers:
        reader.cancel()
    await asyncio.gather(*readers, return_exceptions=True)
    # SIGKILL cannot undo an earlier write. Its purpose is to stop further
    # helper effects while the durable operation retains its uncertain receipt.
    for _ in range(50):
        if not await asyncio.to_thread(_group_alive, proc.pid):
            break
        with contextlib.suppress(ProcessLookupError):
            os.killpg(proc.pid, signal.SIGKILL)
        await asyncio.sleep(.02)
    else:
        raise AmbiguousOutcome('local Git helper process exit is unconfirmed')

    async def drain(stream):
        while await stream.read(65536):
            pass

    try:
        await asyncio.wait_for(asyncio.gather(drain(proc.stdout), drain(proc.stderr), proc.wait()), 2)
    except asyncio.TimeoutError:
        raise AmbiguousOutcome('local Git helper pipe closure is unconfirmed') from None


async def run(script: str, timeout_s: float | None = None) -> str:
    """Run a fixed internal script under one caller budget and concurrent limits.

    No SSH fallback, profile environment, cwd selection or model launch is
    provided here. The initial stdin handshake prevents effects during a
    cancelled spawn before the caller obtains the owned process handle.
    """
    budget = _budget(timeout_s)
    if sys.platform != 'linux':
        raise _refuse('local_git_platform_unsupported')
    if not isinstance(script, str) or not script or '\0' in script:
        raise _refuse('local_git_script_invalid')
    proc = None
    readers = []
    check = _LOCKED_CHECK.get()
    token = _LOCKED_CHECK.set(None)

    async def execute():
        nonlocal proc, readers
        try:
            proc = await asyncio.create_subprocess_exec('/bin/sh', '-c', _PREFIX + script,
                cwd='/', env=dict(_ENV), start_new_session=True, stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        except OSError:
            raise _refuse('local_git_spawn_failed') from None

        async def read(stream, initial=b''):
            output = bytearray(initial)
            while chunk := await stream.read(min(65536, LIMIT + 1 - len(output))):
                output.extend(chunk)
                if len(output) > LIMIT:
                    raise ValueError('capture limit')
            return bytes(output)

        async def stdout():
            if check is None:
                return await read(proc.stdout)
            first = await proc.stdout.readline()
            if first.strip() != b'{"locked": true}':
                proc.stdin.close()
                return await read(proc.stdout, first)
            await check()
            proc.stdin.write(b'{"proceed": true}\n')
            await proc.stdin.drain()
            proc.stdin.close()
            # The gate line is also charged against the per-pipe budget.
            result = await read(proc.stdout, first)
            return result[len(first):]

        readers = [asyncio.create_task(stdout()), asyncio.create_task(read(proc.stderr))]
        proc.stdin.write(_LAUNCH)
        await proc.stdin.drain()
        if check is None:
            proc.stdin.close()
        out, _ = await asyncio.gather(*readers)
        await proc.wait()
        if proc.returncode != 0:
            raise AmbiguousOutcome('local Git helper exited without a successful receipt')
        try:
            result = out.decode('utf-8', errors='strict').strip()
        except UnicodeDecodeError:
            raise AmbiguousOutcome('local Git helper returned invalid receipt bytes') from None
        if await asyncio.to_thread(_group_alive, proc.pid):
            raise AmbiguousOutcome('local Git helper left a running descendant')
        return result

    try:
        return await asyncio.wait_for(execute(), budget)
    except asyncio.TimeoutError:
        raise AmbiguousOutcome('local Git helper timed out') from None
    except (ValueError, BrokenPipeError, ConnectionResetError):
        raise AmbiguousOutcome('local Git helper output or transport was incomplete') from None
    finally:
        _LOCKED_CHECK.reset(token)
        if proc is not None:
            # Repeated shutdown requests cannot cancel the bounded owned join.
            cleanup = asyncio.create_task(_stop(proc, readers))
            cancelled = False
            while not cleanup.done():
                try:
                    await asyncio.shield(cleanup)
                except asyncio.CancelledError:
                    cancelled = True
            cleanup.result()
            if cancelled:
                raise asyncio.CancelledError
