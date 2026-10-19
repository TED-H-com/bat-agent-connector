"""Verify a bootstrapped install and publish an updater-compatible receipt.

The standard-library scripts/install_connector.py installs via uv first, then
invokes this module in the installed isolated interpreter.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

from . import platform_files as files
from .build_identity import build_identity
from .update_install import clean_env, installed, package_files, run
from .update_release import REPOSITORY, UpdateError, require

COMMANDS = ("batc", "bat-agent-connector-mcp", "batc-update")


def source_digest(package):
    """Legacy rollout digest consumed by Updater.enroll."""
    digest = hashlib.sha256()
    for name, value in sorted(package_files(package, python_only=True).items()):
        digest.update(name.encode() + b"\0" + bytes.fromhex(value))
    return digest.hexdigest()


def finalize(root, release, *, source=None, home=None, verify_only=False,
             inspect=installed, command=run, identity=build_identity):
    home = Path.home() if home is None else Path(home)
    root = Path(root).absolute()
    files.check_private(root, directory=True)
    proof = inspect(root, release, home=home)
    package = Path(proof["package"])
    expected_identity = {"version": release["version"], "commit": release["commit"], "source": "vcs"}
    require(identity() == expected_identity, "installed_build_identity_mismatch")
    canonical = (package / "skill/SKILL.md").read_bytes()
    require(hashlib.sha256(canonical).hexdigest() == release["skill_sha256"], "installed_skill_mismatch")
    receipt = root / "rollout-receipt.json"
    doc = {"release": {"repository": REPOSITORY + ".git", "commit": release["commit"],
                       "package_version": release["version"], "canonical_sha256": release["skill_sha256"]},
           "build_identity": expected_identity,
           "installed_proof": {"package_path": str(package), "python_source_sha256": source_digest(package),
                               "packaged_workflow_sha256": proof["skill_sha256"]},
           "entrypoints": {name: str((root / "bin" / name).resolve()) for name in COMMANDS}}
    if verify_only:
        require(json.loads(files.read_private(receipt)) == doc, "install_receipt_mismatch")
        return {"status": "verified_installation", "receipt": str(receipt), "writes": False}
    require(source is not None, "source_checkout_required")
    require(package_files(Path(source) / "src/bat_agent_connector") == package_files(package),
            "installed_source_mismatch")
    require((Path(source) / "skills/bat-agent-connector/SKILL.md").read_bytes() == canonical,
            "source_skill_mismatch")
    version_text = command([str(root / "bin/batc"), "--version"], env=clean_env(home))
    require(version_text.strip() == release["version"], "installed_cli_version_mismatch")
    targets = {home / ".local/bin" / name: root / "bin" / name for name in COMMANDS}
    skill = home / ".agents/skills/bat-agent-connector/SKILL.md"
    require(not skill.exists() and not skill.is_symlink(), "canonical_skill_already_exists")
    for target in targets:
        require(not target.exists() and not target.is_symlink(), "entrypoint_already_exists")
        descriptor = files._posix_dir(target.parent, create=True)
        try:
            info = os.fstat(descriptor)
            require(info.st_uid == os.getuid() and not info.st_mode & 0o022, "entrypoint_directory_not_owned")
        finally:
            os.close(descriptor)
    files.ensure_private_directory(skill.parent)
    files.atomic_write(root / "skill/SKILL.md", canonical)
    for target, destination in targets.items():
        os.symlink(str(destination), target)
    descriptor = files.open_private_file(skill, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
    with os.fdopen(descriptor, "wb") as output:
        output.write(canonical)
        output.flush()
        os.fsync(output.fileno())
    summary = {"status": "installed", "receipt": str(receipt), "build_identity": expected_identity,
               "automatic_updates": "not_enrolled"}
    files.atomic_write(root / "install-receipt.json", json.dumps(summary, sort_keys=True).encode())
    files.atomic_write(receipt, json.dumps(doc, sort_keys=True).encode())
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path)
    parser.add_argument("release", help="Verified release JSON supplied by the bootstrap")
    parser.add_argument("--source", type=Path)
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args(argv)
    try:
        print(json.dumps(finalize(args.root, json.loads(args.release), source=args.source,
                                  verify_only=args.verify_only), sort_keys=True))
        return 0
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(str(exc) if isinstance(exc, UpdateError) else "installation_verification_failed", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
