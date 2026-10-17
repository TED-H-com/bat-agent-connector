#!/usr/bin/env python3
"""Real isolated updater install from a local fixture tag, with no live BAT.

Use a temporary directory on disk; all fixture repositories and environments are
deleted on exit. Only the release transport is substituted. uv installs the real
locked package and the production source/entrypoint/metadata verifier reads it.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from bat_agent_connector import __version__
from bat_agent_connector import platform_files as files
from bat_agent_connector.update_install import clean_env, installed, run, stage
from bat_agent_connector.update_release import REPOSITORY

ROOT = Path(__file__).resolve().parents[1]


def validate(parent):
    files.ensure_private_directory(parent)
    with tempfile.TemporaryDirectory(prefix="connector-update-fixture-", dir=parent) as temporary:
        scratch = Path(temporary)
        home = scratch / "home"
        files.ensure_private_directory(home / ".local/bin")
        uv = shutil.which("uv")
        assert uv and sys.version_info[:2] == (3, 13), "Use the Python 3.13 fixture environment with uv"
        (home / ".local/bin/uv").symlink_to(uv)
        (home / ".local/bin/python3.13").symlink_to(Path(sys.executable).resolve())
        source = scratch / "source"
        source.mkdir(mode=0o700)
        for name in ("pyproject.toml", "uv.lock", "README.md", "LICENSE", "THIRD_PARTY_NOTICES.md"):
            shutil.copy2(ROOT / name, source / name)
        shutil.copytree(ROOT / "src/bat_agent_connector", source / "src/bat_agent_connector",
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        files.atomic_write(source / "skills/bat-agent-connector/SKILL.md",
                           (ROOT / "skills/bat-agent-connector/SKILL.md").read_bytes())
        env = clean_env(home)
        git = shutil.which("git", path=env["PATH"])
        run([git, "init", "--quiet", str(source)], env=env)
        run([git, "-C", str(source), "add", "."], env=env)
        run([git, "-C", str(source), "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
             "-c", "core.hooksPath=/dev/null", "commit", "--quiet", "-m", "isolated updater fixture"], env=env)
        sha = run([git, "-C", str(source), "rev-parse", "HEAD"], env=env)
        tag = "v" + __version__ + "-preview.fixture"
        run([git, "-C", str(source), "tag", tag], env=env)
        candidate = {"commit": sha, "version": __version__, "tag": tag, "epoch": 1, "prerelease": True,
                     "skill_sha256": hashlib.sha256((source / "skills/bat-agent-connector/SKILL.md").read_bytes()).hexdigest()}
        root = scratch / "installed"
        fetches = []
        def command(args, **kwargs):
            if "fetch" in args:
                assert REPOSITORY + ".git" in args
                args = [value if value != REPOSITORY + ".git" else source.as_uri() for value in args]
                args = [args[0], "-c", "protocol.file.allow=always", *args[1:]]
                fetches.append(sha)
            return run(args, **kwargs)
        proof = stage(root, candidate, home=home, command=command)
        assert installed(root, candidate, home=home) == proof
        assert stage(root, candidate, home=home, command=command) == proof
        assert fetches == [sha]
        version_text = run([str(root / "bin/batc"), "--version"], env=env)
        assert __version__ in version_text
        help_text = run([str(root / "bin/batc-update"), "--help"], env=env)
        assert "rollback" in help_text and "schedule" in help_text
        return {"status": "passed", "fixture_commit": sha, "package_version": __version__,
                "source_files_sha256": proof["files"], "canonical_skill_sha256": proof["skill_sha256"],
                "repeat_stage_reused_verified_environment": True, "fixture_resources_removed": True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--temp-parent", type=Path, default=Path.home() / "agent-work/tmp/connector-update")
    parser.add_argument("--receipt", type=Path, required=True)
    args = parser.parse_args()
    files.ensure_private_directory(args.receipt.parent)
    try:
        result = validate(args.temp_parent)
    except (OSError, ValueError, AssertionError, subprocess.SubprocessError):
        result = {"status": "failed", "fixture_resources_removed": True}
        files.atomic_write(args.receipt, (json.dumps(result, indent=2) + "\n").encode())
        raise
    files.atomic_write(args.receipt, (json.dumps(result, indent=2) + "\n").encode())
    print(json.dumps(result))


if __name__ == "__main__":
    main()
