"""Real build helpers and the shared candidate wheel for package tests."""
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from scripts.validate_connector_update import copy_fixture_source


def _build_env(directory, commit=None):
    env = dict(os.environ)
    for key in ("PYTHONPATH", "PYTHONHOME", "BATC_BUILD_COMMIT", "GIT_DIR", "GIT_WORK_TREE",
                "GIT_INDEX_FILE", "GIT_COMMON_DIR"):
        env.pop(key, None)
    scratch = directory / "scratch"
    scratch.mkdir(exist_ok=True)
    env["TMPDIR"] = str(scratch)
    if commit is not None:
        env["BATC_BUILD_COMMIT"] = commit
    return env


def _command(args, *, cwd, env):
    result = subprocess.run(args, cwd=cwd, env=env, text=True, capture_output=True)
    assert result.returncode == 0, f"{args!r}\n{result.stdout}\n{result.stderr}"
    return result.stdout.strip()


def _source_repo(directory, label):
    """Copy the real build inputs, including the real hook when present."""
    root = Path(__file__).resolve().parents[1]
    repo = directory / "source"
    repo.mkdir()
    copy_fixture_source(root, repo)
    env = _build_env(directory)
    for args in (["git", "init", "-q"], ["git", "add", "."],
                 ["git", "-c", "user.name=Build Fixture", "-c", "user.email=build@example.invalid",
                  "commit", "-qm", label]):
        _command(args, cwd=repo, env=env)
    head = _command(["git", "rev-parse", "HEAD"], cwd=repo, env=env)
    return repo, head


def _build(source, directory, mode="wheel", commit=None):
    out = directory / f"dist-{mode}"
    started = time.perf_counter()
    _command(["uv", "build", f"--{mode}", "--python", sys.executable, "--no-python-downloads",
              "--out-dir", str(out), str(source)], cwd=directory, env=_build_env(directory, commit))
    print(f"REAL BUILD {directory.name}/{mode}: {time.perf_counter() - started:.3f}s", flush=True)
    return next(out.glob("*.whl" if mode == "wheel" else "*.tar.gz"))


@pytest.fixture(scope="session")
def clean_wheel(tmp_path_factory):
    """One real candidate wheel per session, shared by packaging consumers."""
    directory = tmp_path_factory.mktemp("clean-wheel")
    repo, head = _source_repo(directory, "clean candidate")
    wheel = _build(repo, directory)
    return directory, repo, head, wheel
