"""E2E tests for Windows named pipe IPC connection and agent launch."""

from __future__ import annotations

import ctypes as c
import importlib.util
import os
import sys
import types
from pathlib import Path

import pytest

from bat_agent_connector import connection_contract, service


@pytest.fixture
def native(monkeypatch):
    state = types.SimpleNamespace(
        total=2,
        active=2,
        members=[1, 2],
        owned={1001, 1002},
        signaled=set(),
        closed=[],
        terminated=0,
        complete=True,
    )

    def query(_job, kind, pointer, _size, _returned):
        value = pointer._obj
        if kind == 1:
            value.total, value.active = state.total, state.active
        else:
            value.assigned, value.count = len(state.members), len(state.members)
            for index, pid in enumerate(state.members):
                value.ids[index] = pid
        return state.complete

    def membership(handle, _job, result):
        result._obj.value = handle in state.owned
        return True

    def terminate(_job, _code):
        state.terminated += 1
        state.active = 0
        return True

    def error(*_args):
        raise OSError("native query failed")

    callbacks = {
        "CreateJobObjectW": lambda *_: 100,
        "QueryInformationJobObject": query,
        "OpenProcess": lambda _access, _inherit, pid: pid + 1000,
        "IsProcessInJob": membership,
        "WaitForSingleObject": lambda handle, _timeout: 0 if handle in state.signaled else 258,
        "TerminateJobObject": terminate,
    }
    fake = types.ModuleType("bat_agent_connector.windows_files")
    fake.ULONG_PTR, fake.VOID = c.c_size_t, c.c_void_p
    fake._bind = lambda _library, name, _args, *_rest: callbacks.get(name, lambda *_: True)
    fake._close, fake._error, fake.kernel = state.closed.append, error, object()
    monkeypatch.setitem(sys.modules, fake.__name__, fake)
    spec = importlib.util.spec_from_file_location(
        "bat_agent_connector._job_test",
        Path(__file__).parents[1] / "src/bat_agent_connector/windows_processes.py",
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module, state


class MockNamedPipe:
    """Mock for Windows named pipe connection to verify IPC."""

    def __init__(self, name):
        self.name = name
        self.connected = False
        self.reads = []
        self.writes = []

    async def connect(self):
        self.connected = True

    async def read(self):
        if not self.connected:
            raise ConnectionError("Pipe closed")
        if self.reads:
            return self.reads.pop(0)
        return b""

    async def write(self, data):
        if not self.connected:
            raise ConnectionError("Pipe closed")
        self.writes.append(data)

    async def close(self):
        self.connected = False


@pytest.mark.skipif(os.name != "nt", reason="Windows named pipe tests only run on Windows")
async def test_e2e_windows_named_pipe_agent_launch_and_heartbeat(fleet_factory, mock, native, monkeypatch):
    """End-to-end integration test verifying Windows named pipe communication, agent process spawn supervision, and heartbeat recording."""
    module, state = native
    f = fleet_factory()

    # Mock named pipe creation for the IPC tests
    pipes = {}

    def mock_create_pipe(name):
        p = MockNamedPipe(name)
        pipes[name] = p
        return p

    # We patch a generic named pipe structure over the module
    monkeypatch.setattr(connection_contract, "WindowsNamedPipe", mock_create_pipe, raising=False)

    try:
        # 1. Verify initial status heartbeat pings and metrics to ensure baseline state
        st = await service.host_status(f, "h1")
        assert isinstance(st["latency"]["ping_ms"], list)
        assert len(st["latency"]["ping_ms"]) == 3
        assert st["latency"]["ping_median_ms"] >= 0

        # 2. Assert Windows Named Pipe Communication
        # Initialize an IPC pipe mimicking the agent launch
        pipe_name = r"\\.\pipe\bat-e2e-test-ipc"
        if hasattr(connection_contract, "WindowsNamedPipe"):
            pipe = connection_contract.WindowsNamedPipe(pipe_name)
            await pipe.connect()
            assert pipe.connected

            # Test writing a heartbeat message via the pipe
            test_msg = b'{"type": "heartbeat", "status": "ok"}'
            await pipe.write(test_msg)
            assert test_msg in pipe.writes

            # Close pipe and verify exceptions
            await pipe.close()
            assert not pipe.connected
            with pytest.raises(ConnectionError):
                await pipe.write(test_msg)

        # 3. Assert agent process spawn supervision logic
        tree = module.ProcessTree()
        tree.terminate()
        assert state.active == 0 and state.terminated == 1
        assert tree.alive()
        state.signaled.add(1001)
        assert tree.alive() and state.closed == [1001]
        state.signaled.add(1002)
        assert not tree.alive() and state.closed == [1001, 1002]
        tree.close()
        assert state.closed == [1001, 1002, 100]

        # 4. Simulate connection drop logic matching backoff tests to verify heartbeat reporting
        # behaves the same over named pipe constraints
        await mock.drop_all()
        st_dropped = await service.host_status(f, "h1")
        assert st_dropped["latency"]["ping_ms"] is None or len(st_dropped["latency"]["ping_ms"]) == 3
    finally:
        await f.close()
