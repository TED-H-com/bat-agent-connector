"""Import order is part of the service contract; each module gets a fresh interpreter."""
from __future__ import annotations

import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

import bat_agent_connector

WINDOWS_ONLY = {"windows_files", "windows_processes"}
MODULES = sorted(
    path.stem for path in Path(bat_agent_connector.__file__).parent.glob("*.py")
    if path.stem != "__init__" and (sys.platform == "win32" or path.stem not in WINDOWS_ONLY)
)


@pytest.mark.parametrize("group", range(4))
def test_modules_import_in_fresh_interpreters(group):
    def check(name):
        result = subprocess.run([sys.executable, "-c", f"import bat_agent_connector.{name}"],
                                capture_output=True, text=True, timeout=60)
        return name, result

    # Four batches, with at most four isolated interpreters alive at once.
    with ThreadPoolExecutor(max_workers=4) as pool:
        failures = [(name, result.stderr) for name, result in pool.map(check, MODULES[group::4])
                    if result.returncode]
    assert not failures, failures


def test_task_service_constructs_in_fresh_interpreter(tmp_path):
    program = """
import asyncio
import sys
from pathlib import Path
from bat_agent_connector.task_daemon import TaskDaemon
from tests.conftest import make_config
from tests.mockbat import MockBat

async def main():
    mock = MockBat()
    await mock.start()
    daemon = None
    try:
        daemon = TaskDaemon(make_config(mock, writes=True, orchestrate=True,
            managed_roots=['/srv'], safety={'write_min_interval_s': 0}), Path(sys.argv[1]) / 'tasks.db')
        assert 'pm.thread.rename' in daemon.ops.actions
    finally:
        if daemon:
            await daemon.fleet.close()
            await daemon.inventory.close()
            daemon.journal.close()
        await mock.stop()

asyncio.run(main())
"""
    result = subprocess.run([sys.executable, "-c", program, str(tmp_path)],
                            capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
