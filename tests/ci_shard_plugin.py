"""Opt-in CI plugin: load with -p tests.ci_shard_plugin. Local defaults are unchanged."""

from __future__ import annotations

import faulthandler
import hashlib
import json
import math
import os
import platform
import sys
import time
from contextlib import contextmanager
from pathlib import Path

import pytest

from scripts.ci_sharding import assign_files, collection_digest, load_weights

_crash_state = pytest.StashKey[tuple[int, int | None]]()


def pytest_addoption(parser):
    group = parser.getgroup("ci-sharding")
    group.addoption("--ci-shard-index", type=int, help="One-based shard index")
    group.addoption("--ci-shard-count", type=int, help="Number of separate CI runners")
    group.addoption("--ci-shard-manifest", type=Path, help="Output JSON manifest")
    group.addoption("--ci-durations", type=Path, default=Path("tests/ci_durations.json"))
    group.addoption("--ci-watchdog-seconds", type=float, help="Diagnostic all-thread dumps; requires -p no:faulthandler")


def pytest_configure(config):
    index = config.getoption("ci_shard_index")
    count = config.getoption("ci_shard_count")
    manifest = config.getoption("ci_shard_manifest")
    timeout = config.getoption("ci_watchdog_seconds")
    if index is None and count is None and manifest is None and timeout is None:
        return
    if index is None or count is None or manifest is None or not 1 <= index <= count:
        raise pytest.UsageError("CI sharding requires index, count and manifest, with 1 <= index <= count")
    if config.getoption("numprocesses", default=None):
        raise pytest.UsageError("CI shards require separate runners, not pytest-xdist")
    if timeout is not None:
        if not math.isfinite(timeout) or timeout <= 0:
            raise pytest.UsageError("CI watchdog requires a finite positive number of seconds")
        if config.pluginmanager.hasplugin("faulthandler"):
            raise pytest.UsageError("CI watchdog requires -p no:faulthandler; only one timer may own dumps")
    manifest.parent.mkdir(parents=True, exist_ok=True)
    progress = manifest.with_name("progress.jsonl").open("a", encoding="ascii")
    config.add_cleanup(progress.close)
    # Duplicate stderr before per-test capture starts, so a blocked test cannot
    # trap the dump in pytest's capture file. The workflow tees it to an artifact.
    stderr_fd = None
    if timeout is not None:
        original_stderr = sys.stderr.fileno()
        stderr_fd = os.dup(original_stderr)
        # Like pytest's built-in plugin, retain the original stderr target:
        # faulthandler has no API to retrieve an already-enabled handler's fd.
        restore_fd = original_stderr if faulthandler.is_enabled() else None
        config.stash[_crash_state] = (stderr_fd, restore_fd)
        config.add_cleanup(lambda: _restore_crash_handler(config))  # Also covers configure failures.
        faulthandler.enable(file=stderr_fd, all_threads=True)
    config.pluginmanager.register(_ShardEvidence(progress, timeout, stderr_fd), "ci-shard-evidence")


def _restore_crash_handler(config):
    state = config.stash.get(_crash_state, None)
    if state is None:
        return
    stderr_fd, restore_fd = state
    faulthandler.cancel_dump_traceback_later()
    faulthandler.disable()
    os.close(stderr_fd)
    if restore_fd is not None:
        faulthandler.enable(file=restore_fd, all_threads=True)
    del config.stash[_crash_state]


def pytest_unconfigure(config):
    _restore_crash_handler(config)


def _read_proc(path):
    """Small injection seam for unavailable procfs; never patch global open."""
    return Path(path).read_text(encoding="ascii")


def _resource_snapshot():
    snapshot = {"mem_available_kib": None, "rss_kib": None, "threads": None}
    if sys.platform != "linux":
        return snapshot
    for path, fields in (
        ("/proc/meminfo", {"MemAvailable": "mem_available_kib"}),
        ("/proc/self/status", {"VmRSS": "rss_kib", "Threads": "threads"}),
    ):
        try:
            text = _read_proc(path)
        except (OSError, UnicodeError):
            continue  # Missing diagnostics must not change a test's outcome.
        for line in text.splitlines():
            key, _, raw = line.partition(":")
            if key not in fields:
                continue
            parts = raw.split()
            try:
                value = int(parts[0])
                units = [] if key == "Threads" else ["kB"]
                if 0 <= value < 2**63 and parts[1:] == units:
                    snapshot[fields[key]] = value
            except (ValueError, IndexError):
                pass
    return snapshot


class _ShardEvidence:
    """One native timer per test, including teardown after an assertion fails."""

    def __init__(self, stream, timeout, stderr_fd):
        self.stream = stream
        self.timeout = timeout
        self.stderr_fd = stderr_fd

    @pytest.hookimpl(wrapper=True, tryfirst=True)
    def pytest_runtest_protocol(self, item):
        if self.timeout is None:
            return (yield)
        # The built-in plugin cancels on pytest_exception_interact, even for a
        # normal failed assertion. Own the timer so stalled failure cleanup is
        # still observable. Repeated dumps use the SAME native timer (no GIL needed).
        faulthandler.dump_traceback_later(self.timeout, repeat=True, file=self.stderr_fd, exit=False)
        try:
            return (yield)
        finally:
            faulthandler.cancel_dump_traceback_later()

    def write(self, item, phase, event):
        # Escape newlines and bound even parametrized IDs to <8 KiB per line.
        # The full ID remains in manifest.json; its digest disambiguates truncation.
        self.stream.write(json.dumps({
            "time": time.time(), "nodeid": item.nodeid[:512],
            "nodeid_sha256": hashlib.sha256(item.nodeid.encode()).hexdigest(),
            "nodeid_truncated": len(item.nodeid) > 512, "phase": phase, "event": event,
            **_resource_snapshot(),
        }, ensure_ascii=True) + "\n")
        self.stream.flush()

    @contextmanager
    def phase(self, item, phase):
        self.write(item, phase, "start")
        try:
            yield
        finally:
            self.write(item, phase, "finish")

    @pytest.hookimpl(wrapper=True, tryfirst=True)
    def pytest_runtest_setup(self, item):
        with self.phase(item, "setup"):
            return (yield)

    @pytest.hookimpl(wrapper=True, tryfirst=True)
    def pytest_runtest_call(self, item):
        with self.phase(item, "call"):
            return (yield)

    @pytest.hookimpl(wrapper=True, tryfirst=True)
    def pytest_runtest_teardown(self, item):
        with self.phase(item, "teardown"):
            return (yield)


@pytest.hookimpl(trylast=True)
def pytest_collection_modifyitems(config, items):
    index = config.getoption("ci_shard_index")
    if index is None:
        return
    count = config.getoption("ci_shard_count")
    nodeids = sorted(item.nodeid for item in items)
    if len(nodeids) != len(set(nodeids)):
        raise pytest.UsageError("CI collection contains duplicate node IDs")
    table = config.getoption("ci_durations")
    if not table.is_absolute():
        table = config.rootpath / table
    try:
        assignment = assign_files([nodeid.split("::", 1)[0] for nodeid in nodeids], load_weights(table), count)
    except (OSError, ValueError) as exc:
        raise pytest.UsageError(f"Cannot load CI durations: {exc}") from exc
    selected, deselected = [], []
    for item in items:
        (selected if assignment[item.nodeid.split("::", 1)[0]] == index else deselected).append(item)
    manifest = config.getoption("ci_shard_manifest")
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(json.dumps({
        "schema_version": 1,
        "python_version": ".".join(platform.python_version_tuple()[:2]),
        "shard_index": index,
        "shard_count": count,
        "collection_count": len(nodeids),
        "collection_sha256": collection_digest(nodeids),
        "selected_nodeids": sorted(item.nodeid for item in selected),
    }, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
    items[:] = selected
    config.hook.pytest_deselected(items=deselected)
