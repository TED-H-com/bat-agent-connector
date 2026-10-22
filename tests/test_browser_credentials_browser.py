"""Real auth journeys run in the Python-equipped browser integration job."""
import asyncio
import os
import shutil
import signal
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.skipif(os.environ.get("BATC_WORK_LOOP_BROWSER") != "1",
    reason="opt-in real-service browser integration")


async def test_browser_credentials_keep_refusal_and_work_across_explicit_recovery(tmp_path):
    assert shutil.which("node"), "Credential browser integration requires Node"
    assert (ROOT / "desktop/node_modules/@playwright/test/cli.js").is_file(), "Install the browser integration dependencies"
    scratch = tmp_path / "browser"
    scratch.mkdir()
    output = ROOT / "desktop/test-results/credential-service.log"
    output.parent.mkdir(exist_ok=True)
    with output.open("w") as log:
        child = await asyncio.create_subprocess_exec("node", "node_modules/@playwright/test/cli.js", "test",
            "--config=playwright.auth.config.ts", "--workers=2", cwd=ROOT / "desktop",
            stdout=log, stderr=asyncio.subprocess.STDOUT, start_new_session=True,
            env={**os.environ, "BATC_AUTH_PYTHON": sys.executable, "TMPDIR": str(scratch)})
        try:
            code = await asyncio.wait_for(child.wait(), 150)
        finally:
            if child.returncode is None:
                os.killpg(child.pid, signal.SIGTERM)
                await asyncio.wait_for(child.wait(), 10)
    assert code == 0, output.read_text()
