"""Console detachment does not escape Windows jobs; request permitted breakaway.

A nested/restrictive job can still refuse creation. Retry only ERROR_ACCESS_DENIED,
which proves no child was created, and emit one fixed diagnostic without private data.
"""
from __future__ import annotations

import ctypes as c
import subprocess

BREAKAWAY = 0x01000000
WARNING = b"MANAGED_SESSION_LIFETIME: connector may stop when the launching session ends\n"


class _Limits(c.Structure):
    _fields_ = [("process_time", c.c_int64), ("job_time", c.c_int64),
                ("flags", c.c_uint32), ("min_working_set", c.c_size_t),
                ("max_working_set", c.c_size_t), ("active_processes", c.c_uint32),
                ("affinity", c.c_size_t), ("priority", c.c_uint32),
                ("scheduling", c.c_uint32)]


def job_allows_breakaway(kernel=None):
    try:
        if kernel is None:
            kernel = c.WinDLL("kernel32", use_last_error=True)
        kernel.GetCurrentProcess.restype = c.c_void_p
        kernel.IsProcessInJob.argtypes = [c.c_void_p, c.c_void_p, c.POINTER(c.c_int)]
        kernel.QueryInformationJobObject.argtypes = [c.c_void_p, c.c_int, c.c_void_p, c.c_uint32, c.c_void_p]
        inside = c.c_int()
        if not kernel.IsProcessInJob(kernel.GetCurrentProcess(), None, c.byref(inside)):
            return False
        if not inside.value:
            return True  # the flag is harmless outside a job
        limits = _Limits()
        if not kernel.QueryInformationJobObject(None, 2, c.byref(limits), c.sizeof(limits), None):
            return False
        return bool(limits.flags & (0x800 | 0x1000))  # BREAKAWAY_OK / SILENT_BREAKAWAY_OK
    except (AttributeError, OSError):
        return False


def spawn(command, *, windows, **options):
    allowed = windows and job_allows_breakaway()
    if allowed:
        options["creationflags"] |= BREAKAWAY
    elif windows:
        options["stderr"].write(WARNING)
        options["stderr"].flush()
    try:
        return subprocess.Popen(command, **options)  # noqa: S603 - caller supplies fixed bundled executable
    except OSError as error:
        if not allowed or getattr(error, "winerror", None) != 5:
            raise
        options["creationflags"] &= ~BREAKAWAY
        options["stderr"].write(WARNING)
        options["stderr"].flush()
        return subprocess.Popen(command, **options)  # noqa: S603
