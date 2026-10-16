"""Publication checks use synthetic identities and never echo offending content."""

import importlib.util
import shutil
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/check_public_content.py"
SPEC = importlib.util.spec_from_file_location("public_content_guard", SCRIPT)
GUARD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(GUARD)


def test_examples_attribution_and_prose_are_allowed():
    assert GUARD.check_text(
        "/home/operator/project\n/Users/fixture/project\n"
        "Synthetic passwd/home/executable layouts\nCopyright Ted Huang\n"
        "https://github.com/example/project\n"
    ) == []


def test_home_paths_and_conversations_report_categories_only():
    private_user = "sample" + "-owner"
    shared_url = "https://chatgpt.com/" + "share/sample-discussion"
    text = f"/home/{private_user}/project\nC:\\Users\\{private_user}\\project\n{shared_url}"
    assert GUARD.check_text(text) == [
        (1, "non-example home path"),
        (2, "non-example home path"),
        (3, "shared conversation link"),
    ]
    assert GUARD.check_text(f"c:\\users\\{private_user}\\project") == [(1, "non-example home path")]
    assert GUARD.check_text(f"c:/users/{private_user}/project") == [(1, "non-example home path")]


def test_cli_checks_tracked_config_without_disclosing_values(tmp_path):
    git = shutil.which("git")
    subprocess.run([git, "init", "--template=", "-q", str(tmp_path)], check=True)
    secret = "synthetic-value-that-must-not-be-printed"
    (tmp_path / "hosts.toml").write_text(f'token = "{secret}"\n')
    subprocess.run([git, "-C", str(tmp_path), "add", "hosts.toml"], check=True)
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--root", str(tmp_path)],
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 1
    assert "hosts.toml:1: private configuration filename" in result.stdout
    assert secret not in result.stdout + result.stderr
