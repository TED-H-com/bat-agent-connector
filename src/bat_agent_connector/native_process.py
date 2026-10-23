"""Bounded native process IO, durable local spool, and read-only Linux death proofs.

The adapter owns launch policy and protocol interpretation. These primitives keep
PM receipt fields and error codes unchanged; they grant no execution authority.
"""

from __future__ import annotations

import asyncio
import copy
import hashlib
import inspect
import json
import os
import re
import shutil
import signal
import sys
import time
from pathlib import Path

MAX_LINE_BYTES = 1024 * 1024
MAX_STREAM_BYTES = 8 * 1024 * 1024


class NativeRuntimeError(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _require(condition, code="PM_NATIVE_BOUNDARY_UNVERIFIED"):
    if not condition:
        raise NativeRuntimeError(code)


def _json(value) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False,
                      separators=(",", ":")).encode("utf-8")


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


async def _call(callback, value):
    result = callback(copy.deepcopy(value))
    if inspect.isawaitable(result):
        await result


async def _spawn(*args, **kwargs):
    return await asyncio.create_subprocess_exec(*args, **kwargs)


def _process_identity(pid: int) -> dict:
    try:
        fields = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
        boot = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
        return {"pid": pid, "start_ticks": int(fields[19]), "boot_id_digest": _digest(boot)}
    except (OSError, ValueError, IndexError):
        raise NativeRuntimeError("PM_NATIVE_PROCESS_IDENTITY_UNAVAILABLE") from None


def inspect_process(identity: dict) -> dict:
    """Read-only proof about the original process. Never signal a possibly reused PID."""
    result = {"state": "unknown", "process": copy.deepcopy(identity), "observed_at": time.time(),
              "proof": "identity_unavailable"}
    if (sys.platform != "linux" or not isinstance(identity, dict)
            or type(identity.get("pid")) is not int or identity["pid"] <= 0
            or type(identity.get("start_ticks")) is not int or identity["start_ticks"] < 0
            or not isinstance(identity.get("boot_id_digest"), str)
            or not re.fullmatch(r"[0-9a-f]{64}", identity["boot_id_digest"])):
        return result
    try:
        boot = _digest(Path("/proc/sys/kernel/random/boot_id").read_text().strip())
        if boot != identity["boot_id_digest"]:
            return {**result, "state": "dead", "proof": "host_rebooted", "current_boot_id_digest": boot}
        try:
            fields = Path(f"/proc/{identity['pid']}/stat").read_text().rsplit(")", 1)[1].split()
        except FileNotFoundError:
            return {**result, "state": "dead", "proof": "original_pid_absent"}
        ticks = int(fields[19])
        if ticks != identity["start_ticks"]:
            return {**result, "state": "dead", "proof": "pid_reused", "current_start_ticks": ticks}
        if fields[0] in {"Z", "X"}:
            return {**result, "state": "dead", "proof": "original_process_exited", "process_state": fields[0]}
        return {**result, "state": "alive", "proof": "original_identity_matches"}
    except (OSError, ValueError, IndexError):
        return result


class BoundedNativeProcess:
    """Lifecycle for an owned subprocess object, never one reconstructed from a PID.

    Historical identities are inspected read-only with inspect_process. The
    launching adapter supplies the process, directory and open spool; callbacks
    must persist before returning. Only confirmed exit permits normal cleanup.
    """

    def __init__(self, run_id: str, *, record_lifecycle=None):
        self.run_id, self.record_lifecycle = run_id, record_lifecycle
        self.process = None
        self.process_launch_attempted = False
        self.directory = None
        self.exit_receipt = None
        self.retained_directory = None
        self.identity = None
        self._spool = None
        self._stderr_task = None
        self._bytes = 0
        self._sent = False
        self._persisted = True
        self._events = []
        self._stderr_bytes = 0
        self.preflight_diagnostic = None

    def _spool_record(self, record):
        self._spool.write(_json(record) + b"\n")
        self._spool.flush()
        os.fsync(self._spool.fileno())

    async def _receipt(self, record):
        self._spool_record({"source": "adapter", **record})
        if self.record_lifecycle is not None:
            try:
                await _call(self.record_lifecycle, record)
            except BaseException:
                self._persisted = False
                raise

    async def _drain_stderr(self):
        while data := await self.process.stderr.read(4096):
            self._stderr_bytes += len(data)
            if self._stderr_bytes > 65_536:
                try:
                    self.process.terminate()
                except ProcessLookupError:
                    pass
                raise NativeRuntimeError("PM_NATIVE_STDERR_LIMIT")

    async def _send(self, data):
        self.process.stdin.write(_json(data) + b"\n")
        await self.process.stdin.drain()

    async def _read_line(self):
        line = await self.process.stdout.readline()
        _require(bool(line) and line.endswith(b"\n"), "PM_NATIVE_STREAM_ENDED")
        self._bytes += len(line)
        _require(len(line) <= MAX_LINE_BYTES and self._bytes <= MAX_STREAM_BYTES, "PM_NATIVE_OUTPUT_LIMIT")
        return line

    async def close(self):
        if self.process is not None and self.exit_receipt is None:
            action = "stdin_closed"
            self.process.stdin.close()
            try:
                await asyncio.wait_for(self.process.wait(), 2)
            except asyncio.TimeoutError:
                action = "sigterm"
                if self.process.returncode is None:
                    try:
                        os.killpg(self.process.pid, signal.SIGTERM)
                    except ProcessLookupError:
                        pass
                try:
                    await asyncio.wait_for(self.process.wait(), 2)
                except asyncio.TimeoutError:
                    action = "sigkill"
                    if self.process.returncode is None:
                        try:
                            os.killpg(self.process.pid, signal.SIGKILL)
                        except ProcessLookupError:
                            pass
                    try:
                        await asyncio.wait_for(self.process.wait(), 5)
                    except asyncio.TimeoutError:
                        action = "sigkill_unconfirmed"
                        self._persisted = False
            if self._stderr_task is not None:
                if self.process.returncode is None:
                    self._stderr_task.cancel()
                try:
                    await asyncio.wait_for(asyncio.gather(self._stderr_task, return_exceptions=True), 2)
                except asyncio.TimeoutError:
                    self._stderr_task.cancel()
            dead = self.process.returncode is not None
            self.exit_receipt = {"kind": "process_exited" if dead else "process_exit_unknown",
                                 "run_id": self.run_id, "process": self.identity,
                                 "exit_code": self.process.returncode, "termination": action,
                                 "signal": -self.process.returncode if dead and self.process.returncode < 0 else None,
                                 "sent": self._sent, "native_event_count": len(self._events),
                                 "stderr_bytes": self._stderr_bytes,
                                 "process_death_confirmed": dead}
            if self.preflight_diagnostic is not None:
                self.exit_receipt["preflight_diagnostic"] = copy.deepcopy(self.preflight_diagnostic)
            try:
                await self._receipt(self.exit_receipt)
            except BaseException:
                self._persisted = False
        if self._spool is not None and not self._spool.closed:
            self._spool.close()
        if self.directory is not None and self.directory.exists():
            if self._persisted:
                shutil.rmtree(self.directory)
            else:
                self.retained_directory = self.directory
