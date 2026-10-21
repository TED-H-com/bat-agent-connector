"""Real fixture VCS installs with repository-locked runtime dependencies."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys


def install_fixture_candidate(source, commit, environment, *, env, lock_root):
    """Create or upgrade one venv; a wheel cache need not contain index metadata."""
    uv = shutil.which("uv")
    assert uv, "Real package fixture requires uv"
    # Installed provenance is removed/recreated by tests; never share cache inodes.
    install_env = {**env, "UV_NO_CONFIG": "1", "UV_LINK_MODE": "copy"}

    def run(args, *, cwd=None):
        result = subprocess.run([uv, *args], env=install_env, cwd=cwd, text=True,
                                capture_output=True, timeout=120)
        assert result.returncode == 0, result.stderr

    if not environment.exists():
        run(["venv", "--python", sys.executable, str(environment)])
    python = environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    constraints = environment.parent / (environment.name + "-constraints.txt")
    run(["export", "--locked", "--no-dev", "--no-emit-project", "--no-hashes",
         "--output-file", str(constraints)], cwd=lock_root)
    # Locked sync can cache wheel URLs without index pages. Allow resolution to
    # fetch missing metadata, while constraints retain the locked versions.
    run(["pip", "install", "--python", str(python), "--reinstall-package", "bat-agent-connector",
         "--constraints", str(constraints), "git+" + source.as_uri() + "@" + commit])
    return python
