import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def git_checkout():
    git = shutil.which("git")
    if git is None:
        pytest.skip("Git is unavailable")
    result = subprocess.run([git, "rev-parse", "--show-toplevel"], cwd=ROOT, capture_output=True, text=True)
    if result.returncode or Path(result.stdout.strip()).resolve() != ROOT:
        pytest.skip("The source is not a Git checkout")
    return git


def test_root_is_the_only_tracked_ignore_file(git_checkout):
    result = subprocess.run([git_checkout, "ls-files", "-z", "--", "*.gitignore", ".gitignore"], cwd=ROOT,
                            check=True, capture_output=True)
    assert result.stdout.split(b"\0") == [b".gitignore", b""]


def test_no_tracked_file_is_ignored(git_checkout):
    result = subprocess.run([git_checkout, "ls-files", "-ci", "--exclude-standard", "-z"], cwd=ROOT,
                            check=True, capture_output=True)
    assert result.stdout == b""
