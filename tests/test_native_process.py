"""Characterize the PM process boundary without launching a CLI or using credentials."""

import asyncio
import json
import signal
import sys
from types import SimpleNamespace

import pytest

from bat_agent_connector import pm_native_runtime as native


class Process:
    pid = 424242

    def __init__(self, exit_on_wait=1, exit_code=0):
        self.returncode = None
        self.exit_on_wait, self.exit_code = exit_on_wait, exit_code
        self.waits, self.closed, self.terminated = 0, 0, 0
        self.writes = []
        self.stdin = SimpleNamespace(close=self.close_stdin, write=self.writes.append, drain=self.drain)
        self.stdout = asyncio.StreamReader(limit=2 * 1024 * 1024)
        self.stderr = asyncio.StreamReader()

    def close_stdin(self):
        self.closed += 1

    async def drain(self):
        pass

    async def wait(self):
        self.waits += 1
        if self.waits != self.exit_on_wait:
            raise asyncio.TimeoutError
        self.returncode = self.exit_code
        return self.returncode

    def terminate(self):
        self.terminated += 1


@pytest.fixture
async def boundary(tmp_path):
    runtime = native.NativePmRuntime(tmp_path, "owned-run", use_auth=False)
    runtime.directory = tmp_path / "owned"
    runtime.directory.mkdir()
    runtime._spool = (runtime.directory / "events.ndjson").open("ab")
    runtime.process = Process()
    runtime.identity = {"pid": Process.pid, "start_ticks": 25, "boot_id_digest": "a" * 64}
    yield runtime
    runtime._spool.close()


async def test_spool_bytes_are_fsynced_before_callback_and_callback_gets_a_copy(boundary, monkeypatch):
    order = []
    fsync = native.os.fsync

    def sync(fd):
        fsync(fd)
        order.append("fsync")

    async def save(record):
        assert order == ["fsync"]
        assert (boundary.directory / "events.ndjson").read_bytes() == (
            b'{"kind":"fixture","nested":{"value":"\xe8\xad\x89\xe6\x93\x9a"},"source":"adapter"}\n')
        record["nested"]["value"] = "callback edit"
        order.append("callback")

    monkeypatch.setattr(native.os, "fsync", sync)
    boundary.record_lifecycle = save
    record = {"nested": {"value": "證據"}, "kind": "fixture"}
    await boundary._receipt(record)
    assert order == ["fsync", "callback"] and record["nested"]["value"] == "證據"


async def test_failed_callback_retains_original_spool_after_confirmed_exit(boundary):
    def fail(_):
        raise OSError("fixture persistence failure")

    boundary.record_lifecycle = fail
    with pytest.raises(OSError, match="fixture persistence failure"):
        await boundary._receipt({"kind": "fixture"})
    await boundary.close()
    assert boundary.retained_directory == boundary.directory
    assert boundary._spool.closed and boundary.exit_receipt["process_death_confirmed"]
    assert (boundary.directory / "events.ndjson").read_bytes().startswith(
        b'{"kind":"fixture","source":"adapter"}\n')


async def test_close_with_only_recorded_identity_never_signals_or_confirms_exit(boundary, monkeypatch):
    boundary.process, boundary._persisted = None, False
    signals = []
    monkeypatch.setattr(native.os, "kill", lambda *args: signals.append(args))
    monkeypatch.setattr(native.os, "killpg", lambda *args: signals.append(args))
    await boundary.close()
    assert signals == [] and boundary.exit_receipt is None
    assert boundary.retained_directory == boundary.directory and boundary._spool.closed


async def test_unknown_exit_cancels_and_joins_stderr_reader(boundary, monkeypatch):
    boundary.process = Process(None)
    monkeypatch.setattr(native.os, "killpg", lambda *_: None)
    ready, stopped = asyncio.Event(), asyncio.Event()

    async def reader():
        try:
            ready.set()
            await asyncio.Future()
        finally:
            stopped.set()

    boundary._stderr_task = asyncio.create_task(reader())
    await asyncio.wait_for(ready.wait(), 5)
    try:
        await boundary.close()
        assert stopped.is_set() and boundary._stderr_task.cancelled()
        assert boundary.exit_receipt["kind"] == "process_exit_unknown"
    finally:
        boundary._stderr_task.cancel()
        await asyncio.gather(boundary._stderr_task, return_exceptions=True)


@pytest.mark.skipif(sys.platform != "linux", reason="Linux process identity")
async def test_spawn_preserves_pipes_and_owned_child_identity():
    child = await native._spawn(
        sys.executable, "-c", "import sys; sys.stdout.buffer.write(sys.stdin.buffer.read())",
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE, start_new_session=True, limit=1024)
    try:
        identity = native._process_identity(child.pid)
        assert native.inspect_process(identity)["state"] == "alive"
        stdout, stderr = await asyncio.wait_for(child.communicate(b"fixture input\n"), 5)
        assert stdout == b"fixture input\n" and stderr == b"" and child.returncode == 0
        assert native.inspect_process(identity)["state"] == "dead"
    finally:
        if child.returncode is None:
            child.kill()
        await child.wait()


@pytest.mark.parametrize("count,overflow", [(1, 1024 * 1024 + 1), (8, 3)])
async def test_stdout_spool_bound_is_exact_and_rejected_line_is_not_recorded(boundary, count, overflow):
    def line(size):
        return b'{"text":"' + b"x" * (size - len(b'{"text":""}\n')) + b'"}\n'

    boundary._sent, boundary._event_sink = True, lambda _: None
    for _ in range(count):
        boundary.process.stdout.feed_data(line(1024 * 1024))
        await boundary._read()
    size = boundary._spool.tell()
    assert boundary._bytes == count * 1024 * 1024
    boundary.process.stdout.feed_data(b"{}\n" if overflow == 3 else line(overflow))
    with pytest.raises(native.NativeRuntimeError, match="^PM_NATIVE_OUTPUT_LIMIT$"):
        await boundary._read()
    assert boundary._spool.tell() == size and len(boundary._events) == count


@pytest.mark.parametrize("data,code", [(b"", "STREAM_ENDED"), (b"{}", "STREAM_ENDED"),
                                      (b"[]\n", "EVENT_INVALID"), (b"\xff\n", "EVENT_INVALID")])
async def test_stream_refusal_keeps_exact_error_and_invalid_raw_receipt(boundary, data, code):
    boundary.process.stdout.feed_data(data)
    boundary.process.stdout.feed_eof()
    with pytest.raises(native.NativeRuntimeError, match=f"^PM_NATIVE_{code}$"):
        await boundary._read()
    rows = (boundary.directory / "events.ndjson").read_bytes()
    if code == "EVENT_INVALID":
        assert json.loads(rows) == {"source": "adapter", "kind": "invalid_native_event",
                                   "run_id": "owned-run", "raw_utf8": data.decode(errors="replace")}
    else:
        assert rows == b""


async def test_send_keeps_canonical_utf8_and_stderr_limit_is_exact(boundary):
    await boundary._send({"z": "證據", "a": 1})
    assert boundary.process.writes == [b'{"a":1,"z":"\xe8\xad\x89\xe6\x93\x9a"}\n']
    boundary.process.stderr.feed_data(b"x" * 65_536)
    boundary.process.stderr.feed_eof()
    await boundary._drain_stderr()
    assert boundary._stderr_bytes == 65_536 and boundary.process.terminated == 0
    boundary.process.stderr = asyncio.StreamReader()
    boundary.process.stderr.feed_data(b"x")
    with pytest.raises(native.NativeRuntimeError, match="^PM_NATIVE_STDERR_LIMIT$"):
        await boundary._drain_stderr()
    assert boundary.process.terminated == 1


@pytest.mark.parametrize("exit_on_wait,exit_code,action,signals", [
    (1, 0, "stdin_closed", []), (2, -15, "sigterm", [signal.SIGTERM]),
    (3, -9, "sigkill", [signal.SIGTERM, signal.SIGKILL]),
    (None, None, "sigkill_unconfirmed", [signal.SIGTERM, signal.SIGKILL]),
])
async def test_close_pins_escalation_exit_receipt_and_unknown_retention(
        boundary, monkeypatch, exit_on_wait, exit_code, action, signals):
    boundary.process = Process(exit_on_wait, exit_code)
    sent = []
    monkeypatch.setattr(native.os, "killpg", lambda pid, sig: sent.append((pid, sig)))
    waits = []

    async def wait_for(awaitable, timeout):
        waits.append(timeout)
        return await asyncio.wait_for(awaitable, timeout)

    module = sys.modules[native.NativePmRuntime.close.__module__]
    monkeypatch.setattr(module, "asyncio", SimpleNamespace(
        wait_for=wait_for, TimeoutError=asyncio.TimeoutError, gather=asyncio.gather))
    receipts = []
    boundary.record_lifecycle = receipts.append
    boundary.preflight_diagnostic = {"stage": "initialize"}
    await boundary.close()
    dead = exit_on_wait is not None
    expected = {"kind": "process_exited" if dead else "process_exit_unknown", "run_id": "owned-run",
                "process": boundary.identity, "exit_code": exit_code, "termination": action,
                "signal": -exit_code if dead and exit_code < 0 else None, "sent": False,
                "native_event_count": 0, "stderr_bytes": 0, "process_death_confirmed": dead,
                "preflight_diagnostic": {"stage": "initialize"}}
    assert receipts == [expected] and boundary.exit_receipt == expected
    assert sent == [(Process.pid, sig) for sig in signals]
    assert boundary.process.waits == (exit_on_wait or 3) and boundary.process.closed == 1
    assert waits == [2, 2, 5][:exit_on_wait or 3]
    assert boundary.directory.exists() is not dead
    assert boundary.retained_directory == (None if dead else boundary.directory)
    if not dead:
        expected_bytes = json.dumps({"source": "adapter", **expected}, ensure_ascii=False,
                                    sort_keys=True, separators=(",", ":")).encode() + b"\n"
        assert (boundary.directory / "events.ndjson").read_bytes() == expected_bytes
    await boundary.close()
    assert len(receipts) == 1 and boundary.process.closed == 1


def proc_stat(state="S", ticks=25):
    return "424242 (fixture (worker)) " + " ".join([state, *(["0"] * 18), str(ticks)])


@pytest.mark.skipif(sys.platform != "linux", reason="Linux process identity")
@pytest.mark.parametrize("stat,boot,proof,state", [
    (proc_stat(), "fixture-boot", "original_identity_matches", "alive"),
    (proc_stat(ticks=26), "fixture-boot", "pid_reused", "dead"),
    (proc_stat("Z"), "fixture-boot", "original_process_exited", "dead"),
    (proc_stat("X"), "fixture-boot", "original_process_exited", "dead"),
    (FileNotFoundError(), "fixture-boot", "original_pid_absent", "dead"),
    (proc_stat(), "new-boot", "host_rebooted", "dead"),
    (PermissionError(), "fixture-boot", "identity_unavailable", "unknown"),
    ("malformed", "fixture-boot", "identity_unavailable", "unknown"),
    (proc_stat(), PermissionError(), "identity_unavailable", "unknown"),
])
def test_inspection_needs_positive_death_proof_and_never_signals(monkeypatch, stat, boot, proof, state):
    module = sys.modules[native.inspect_process.__module__]

    def read(path):
        result = boot if str(path).endswith("boot_id") else stat
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(module, "Path", lambda path: SimpleNamespace(read_text=lambda: read(path)))
    signals = []
    monkeypatch.setattr(native.os, "kill", lambda *args: signals.append(args))
    monkeypatch.setattr(native.os, "killpg", lambda *args: signals.append(args))
    identity = {"pid": 424242, "start_ticks": 25, "boot_id_digest": native._digest("fixture-boot")}
    result = native.inspect_process(identity)
    assert (result["state"], result["proof"]) == (state, proof)
    assert result["process"] == identity and result["process"] is not identity
    assert native.inspect_process({"pid": 424242})["state"] == "unknown"
    assert signals == []
    if isinstance(stat, Exception) or isinstance(boot, Exception) or stat == "malformed":
        with pytest.raises(native.NativeRuntimeError, match="^PM_NATIVE_PROCESS_IDENTITY_UNAVAILABLE$"):
            native._process_identity(424242)
    else:
        assert native._process_identity(424242) == {
            "pid": 424242, "start_ticks": int(stat.split()[-1]), "boot_id_digest": native._digest(boot)}
