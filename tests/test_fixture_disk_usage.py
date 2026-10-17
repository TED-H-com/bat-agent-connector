"""Exercise cleanup through real pytest teardown, including failed setup/tests."""

import os
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.parametrize("outcome", ["passed", "failed", "setup_error"])
def test_per_test_state_is_removed_after_fixture_teardown(tmp_path, outcome):
    suite = tmp_path / "suite"
    suite.mkdir()
    marker = tmp_path / "teardown.txt"
    external = tmp_path / "outside"
    external.mkdir()
    (external / "keep").write_text("outside the test directory")
    (suite / "conftest.py").write_text("from tests.conftest import isolated_dirs\n")
    (suite / "test_cleanup.py").write_text(
        "from pathlib import Path\n"
        "import pytest\n"
        "@pytest.fixture\n"
        "def state(tmp_path, request):\n"
        "    state = tmp_path / 'state'\n"
        "    state.mkdir()\n"
        "    (state / 'tasks.db').write_bytes(b'x' * 1024 * 1024)\n"
        f"    (tmp_path / 'external').symlink_to({str(external)!r}, target_is_directory=True)\n"
        "    def close():\n"
        "        assert (state / 'tasks.db').exists()\n"
        f"        Path({str(marker)!r}).write_text(str(tmp_path))\n"
        "    request.addfinalizer(close)\n"
        f"    if {outcome!r} == 'setup_error':\n"
        "        raise RuntimeError('intentional setup failure')\n"
        "    return state\n"
        "def test_state(state):\n"
        f"    assert {outcome!r} != 'failed', 'intentional assertion failure'\n"
    )
    env = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1]), "PYTEST_ADDOPTS": ""}
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-p",
            "no:cacheprovider",
            "--basetemp",
            str(tmp_path / "run"),
            "-o",
            "tmp_path_retention_policy=all",
            str(suite),
        ],
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == (0 if outcome == "passed" else 1), result.stdout + result.stderr
    assert marker.exists(), result.stdout + result.stderr
    assert not Path(marker.read_text()).exists()
    assert (external / "keep").read_text() == "outside the test directory"
