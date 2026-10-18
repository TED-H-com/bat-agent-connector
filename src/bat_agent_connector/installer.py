"""Verify a bootstrapped install and publish an updater-compatible receipt.

The standard-library scripts/install_connector.py installs via uv first, then
invokes this module in the installed isolated interpreter.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import secrets
import sys
from datetime import datetime, timezone
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


def swap_link(path, destination):
    temporary = path.with_name("." + path.name + "-" + secrets.token_hex(8))
    try:
        temporary.symlink_to(destination)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def swap_skill(path, data):
    descriptor = files._posix_dir(path.parent)
    try:
        info = os.fstat(descriptor)
        require(info.st_uid == os.getuid() and not info.st_mode & 0o022, "adoption_skill_directory_changed")
    finally:
        os.close(descriptor)
    info = path.lstat()
    require(path.is_file() and not path.is_symlink() and info.st_uid == os.getuid()
            and not info.st_mode & 0o022 and info.st_nlink == 1, "adoption_skill_changed")
    temporary = path.with_name(".SKILL-" + secrets.token_hex(8))
    try:
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as output:
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def adoption_links(home, root, adoption, *, restore=False):
    skill = home / ".agents/skills/bat-agent-connector/SKILL.md"
    expected_skill = (root / "skill/SKILL.md").read_bytes() if restore else (root / "adopted-SKILL.md").read_bytes()
    require(not skill.is_symlink() and skill.read_bytes() == expected_skill, "adoption_skill_changed")
    targets = {home / ".local/bin" / name: str(root / "bin" / name) for name in COMMANDS}
    for path, target in targets.items():
        expected = target if restore else adoption["links"][path.name]
        require(path.is_symlink() and path.lstat().st_uid == os.getuid()
                and os.readlink(path) == expected, "adoption_entrypoint_changed")
        descriptor = files._posix_dir(path.parent)
        try:
            info = os.fstat(descriptor)
            require(info.st_uid == os.getuid() and not info.st_mode & 0o022, "entrypoint_directory_not_owned")
        finally:
            os.close(descriptor)
    descriptor = files._posix_dir(skill.parent)
    try:
        info = os.fstat(descriptor)
        require(info.st_uid == os.getuid() and not info.st_mode & 0o022, "adoption_skill_directory_changed")
    finally:
        os.close(descriptor)
    info = skill.lstat()
    require(info.st_uid == os.getuid() and not info.st_mode & 0o022 and info.st_nlink == 1, "adoption_skill_changed")
    changed = []
    previous_skill = skill.read_bytes()
    try:
        for path, target in targets.items():
            previous = os.readlink(path)
            swap_link(path, adoption["links"][path.name] if restore else target)
            changed.append((path, previous))
        swap_skill(skill, (root / "adopted-SKILL.md").read_bytes() if restore else (root / "skill/SKILL.md").read_bytes())
    except (OSError, ValueError) as exc:
        try:
            for path, previous in reversed(changed):
                swap_link(path, previous)
            if skill.read_bytes() != previous_skill:
                swap_skill(skill, previous_skill)
        except (OSError, ValueError) as rollback:
            raise UpdateError("adoption_rollback_failed") from rollback
        raise UpdateError("adoption_swap_failed") from exc


def restore_adopted(root, *, home=None):
    home = Path.home() if home is None else Path(home)
    root = Path(root).absolute()
    files.check_private(root, directory=True)
    record = json.loads(files.read_private(root / "adoption-record.json"))
    previous = files.read_private(root / "adopted-SKILL.md")
    require(hashlib.sha256(previous).hexdigest() == record["skill_sha256"], "adoption_backup_changed")
    adoption_links(home, root, record, restore=True)
    record["restored_at"] = datetime.now(timezone.utc).isoformat()
    files.atomic_write(root / "adoption-record.json", json.dumps(record).encode())
    return {"status": "restored_adopted"}


def finalize(root, release, *, source=None, home=None, verify_only=False,
             inspect=installed, command=run, identity=build_identity, adoption=None):
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
    if adoption:
        state = home / ".local/share/bat-agent-connector/update/state.json"
        require(not state.exists() and not state.is_symlink(), "already_update_managed")
        require(source_digest(Path(adoption["package"])) == adoption["source_sha256"], "adoption_source_changed")
        previous = (Path(adoption["package"]) / "skill/SKILL.md").read_bytes()
        require(hashlib.sha256(previous).hexdigest() == adoption["skill_sha256"], "adoption_skill_changed")
        files.atomic_write(root / "adopted-SKILL.md", previous)
    else:
        require(not skill.exists() and not skill.is_symlink(), "canonical_skill_already_exists")
    for target in targets:
        if not adoption:
            require(not target.exists() and not target.is_symlink(), "entrypoint_already_exists")
        descriptor = files._posix_dir(target.parent, create=True)
        try:
            info = os.fstat(descriptor)
            require(info.st_uid == os.getuid() and not info.st_mode & 0o022, "entrypoint_directory_not_owned")
        finally:
            os.close(descriptor)
    if not adoption:
        files.ensure_private_directory(skill.parent)
    files.atomic_write(root / "skill/SKILL.md", canonical)
    if adoption:
        record = {**adoption, "adopted_at": datetime.now(timezone.utc).isoformat()}
        files.atomic_write(root / "adoption-record.json", json.dumps(record).encode())
        adoption_links(home, root, adoption)
    else:
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
    parser.add_argument("--adoption")
    parser.add_argument("--restore-adopted", action="store_true")
    args = parser.parse_args(argv)
    try:
        result = (restore_adopted(args.root) if args.restore_adopted else
                  finalize(args.root, json.loads(args.release), source=args.source,
                           verify_only=args.verify_only, adoption=json.loads(args.adoption) if args.adoption else None))
        print(json.dumps(result, sort_keys=True))
        return 0
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(str(exc) if isinstance(exc, UpdateError) else "installation_verification_failed", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
