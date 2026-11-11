"""Exercise the local disk wrapper without starting a nested pytest session."""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

WRAPPER = Path(__file__).resolve().parents[1] / "scripts" / "pytest-disktmp"
pytestmark = pytest.mark.skipif(sys.platform != "linux", reason="Linux local fixture wrapper")


@pytest.mark.parametrize("private", [False, True])
@pytest.mark.parametrize("status", [0, 7])
def test_disk_identity_cleanup_exit_status_and_host_permissions(tmp_path, private, status):
    if private:
        if not shutil.which("bwrap"):
            pytest.skip("bubblewrap is not installed")
        probe = subprocess.run(["bwrap", "--ro-bind", "/", "/", "--", "/usr/bin/true"],
                               capture_output=True, text=True)
        if probe.returncode:
            pytest.skip("bubblewrap user namespaces are unavailable")
    root = tmp_path / "shared-fixtures"
    root.mkdir(mode=0o775)
    root.chmod(0o775)
    sibling = root / "another-run"
    sibling.mkdir()
    (sibling / "keep").write_text("unrelated fixture")
    parents = [root, *root.parents, Path("/tmp")]
    modes = {path: path.stat().st_mode for path in parents}
    source = '''import json, os, pathlib, sys
root = pathlib.Path(os.environ['TMPDIR'])
(root / 'probe').write_text('disk fixture')
print(json.dumps({'dev': root.stat().st_dev, 'args': sys.argv[1:],
                  'uid': os.getuid(), 'root_uid': pathlib.Path('/').stat().st_uid,
                  'temp_uid': root.stat().st_uid, 'temp_mode': root.stat().st_mode & 0o777}))
sys.exit(int(sys.argv[1]))
'''
    args = [str(WRAPPER), *(["--private-tmp"] if private else []),
            sys.executable, "-c", source, str(status), "literal spaced value"]
    result = subprocess.run(args, env={**os.environ, "PYTEST_DISK_ROOT": str(root)},
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == status, result.stderr
    body = json.loads(result.stdout)
    assert body["dev"] == root.stat().st_dev
    assert body["args"][:2] == [str(status), "literal spaced value"]
    assert body["args"][2].startswith("--basetemp=")
    if private:
        assert body["root_uid"] == body["temp_uid"] == body["uid"]
        assert body["temp_mode"] == 0o700
        assert body["args"][2] == "--basetemp=/tmp/b"
    assert list(root.iterdir()) == [sibling]
    assert (sibling / "keep").read_text() == "unrelated fixture"
    assert {path: path.stat().st_mode for path in parents} == modes


@pytest.mark.parametrize("arguments", [[], ["--basetemp", "elsewhere"], ["--basetemp=elsewhere"]])
def test_invalid_arguments_do_not_allocate_or_run(tmp_path, arguments):
    root = tmp_path / "must-not-exist"
    command = [sys.executable, "-c", "raise AssertionError('must not run')"] if arguments else []
    result = subprocess.run([str(WRAPPER), *command, *arguments],
                            env={**os.environ, "PYTEST_DISK_ROOT": str(root)},
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 2
    assert not root.exists()
