"""Updater fixtures never contact a release feed or operate a live Connector."""
from __future__ import annotations

import hashlib
import json
import os
import signal
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path

import pytest

from bat_agent_connector import platform_files as files
from bat_agent_connector import updater
from bat_agent_connector.update_install import (
    clean_env,
    digest_files,
    package_files,
    prepare_tool_paths,
    run,
    stage,
)
from bat_agent_connector.update_release import API, REPOSITORY, UpdateError, discover, manifest, version

pytestmark = pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux per-user updater")
SKILL = b"# Fixture canonical workflow\n"
SKILL_SHA = hashlib.sha256(SKILL).hexdigest()


def release(sha="a" * 40, ver="0.2.7"):
    return {"commit": sha, "version": ver, "tag": "v" + ver + "-preview.fixture", "epoch": 1,
            "skill_sha256": SKILL_SHA, "prerelease": True}


def publication(candidate=None, *, preview=True):
    candidate = candidate or release()
    doc = {"format": 1, "repository": REPOSITORY, "release": candidate["tag"],
           "source_commit": candidate["commit"], "versions": {"connector": candidate["version"]},
           "connector_update": {"protocol": 1, "compatibility_epoch": 1, "python": "3.13",
                                "canonical_skill_sha256": SKILL_SHA}, "validation": {"status": "passed"}}
    raw = json.dumps(doc).encode()
    item = {"tag_name": candidate["tag"], "draft": False, "prerelease": preview,
            "published_at": "2026-10-17T00:00:00Z", "assets": [
                {"name": "release-manifest.json", "size": len(raw), "digest": "sha256:" + hashlib.sha256(raw).hexdigest(),
                 "browser_download_url": "https://attacker.invalid/never-follow"}]}
    return item, raw


def test_release_downloads_only_constructed_publisher_urls_and_checks_integrity():
    item, raw = publication()
    calls = []

    def download(url):
        calls.append(url)
        return json.dumps([item]).encode() if url == API else raw

    assert discover("stable", download=download) is None
    observation = {}
    result = discover("preview", download=download, observation=observation)
    assert result["commit"] == "a" * 40
    assert "published_at" not in result
    assert observation["published_at"] == item["published_at"]
    assert calls[-1] == REPOSITORY + "/releases/download/" + item["tag_name"] + "/release-manifest.json"
    item["assets"][0]["digest"] = "sha256:" + "0" * 64
    with pytest.raises(UpdateError, match="digest_mismatch"):
        discover("preview", download=download)


@pytest.mark.parametrize("edit,code", [
    (lambda d: d.update(repository="https://attacker.invalid"), "manifest_mismatch"),
    (lambda d: d.update(source_commit="main"), "source_commit"),
    (lambda d: d["connector_update"].update(protocol=2), "incompatible"),
    (lambda d: d["connector_update"].update(compatibility_epoch=2), "incompatible"),
    (lambda d: d["connector_update"].update(python="3.10"), "python_unsupported"),
    (lambda d: d["versions"].update(connector="0.2.8"), "version_mismatch"),
    (lambda d: d["validation"].update(status="pending"), "not_validated"),
])
def test_manifest_requires_matching_validated_release(edit, code):
    item, raw = publication()
    doc = json.loads(raw)
    edit(doc)
    with pytest.raises(UpdateError, match=code):
        manifest(item, json.dumps(doc).encode())


@pytest.mark.parametrize("value", ["main", "0.2", "0.02.7", "0.2.7+other", "0.2.7-preview", None])
def test_package_version_is_strict(value):
    with pytest.raises(UpdateError, match="version"):
        version(value)


def test_same_version_cannot_name_two_commits():
    first, raw1 = publication()
    second, raw2 = publication({**release("b" * 40), "tag": "v0.2.7-preview.second"})
    def download(url):
        if url == API:
            return json.dumps([first, second]).encode()
        return raw2 if "second" in url else raw1
    with pytest.raises(UpdateError, match="republished"):
        discover("preview", download=download)


def make_package(root, candidate):
    files.ensure_private_directory(root)
    package = root / "tools/bat-agent-connector/lib/python3.13/site-packages/bat_agent_connector"
    files.ensure_private_directory(package)
    (package / "__init__.py").write_text("__version__ = " + repr(candidate["version"]) + "\n")
    files.atomic_write(package / "skill/SKILL.md", SKILL)
    files.atomic_write(root / "skill/SKILL.md", SKILL)
    files.ensure_private_directory(root / "bin")
    for name in updater.COMMANDS:
        (root / "bin" / name).write_text("fixture entrypoint\n")
    return {"package": str(package), "files": digest_files(package_files(package)),
            "python_files": digest_files(package_files(package, python_only=True)), "skill_sha256": SKILL_SHA}


def make_deployment(tmp_path):
    home = tmp_path / "home"
    files.ensure_private_directory(home)
    root = home / ".local/share/bat-agent-connector/releases" / ("a" * 40)
    candidate = release()
    proof = make_package(root, candidate)
    files.ensure_private_directory(home / ".local/bin")
    for name in updater.COMMANDS[:2]:
        (home / ".local/bin" / name).symlink_to(root / "bin" / name)
    files.atomic_write(home / ".agents/skills/bat-agent-connector/SKILL.md", SKILL)
    legacy = hashlib.sha256()
    for name, value in sorted(package_files(Path(proof["package"]), python_only=True).items()):
        legacy.update(name.encode() + b"\0" + bytes.fromhex(value))
    receipt = root / "rollout-receipt.json"
    doc = {"release": {"repository": REPOSITORY + ".git", "commit": candidate["commit"],
                       "package_version": candidate["version"], "canonical_sha256": SKILL_SHA},
           "build_identity": {"version": candidate["version"], "commit": candidate["commit"], "source": "vcs"},
           "installed_proof": {"package_path": proof["package"], "python_source_sha256": legacy.hexdigest(),
                               "packaged_workflow_sha256": SKILL_SHA},
           "entrypoints": {n: str((root / "bin" / n).resolve()) for n in updater.COMMANDS[:2]}}
    files.atomic_write(receipt, json.dumps(doc).encode())
    inspections = []
    def inspect(path, candidate, *, home):
        inspections.append(str(path))
        package = path / "tools/bat-agent-connector/lib/python3.13/site-packages/bat_agent_connector"
        return {"package": str(package), "files": digest_files(package_files(package)),
                "python_files": digest_files(package_files(package, python_only=True)), "skill_sha256": SKILL_SHA}
    u = updater.Updater(home, inspect=inspect, find_release=lambda channel, observation=None: candidate)
    return u, receipt, inspections


@pytest.fixture
def deployment(tmp_path):
    return make_deployment(tmp_path)


def test_enrollment_switches_future_launches_and_rollback_disables_updates(deployment):
    u, receipt, _ = deployment
    u.enroll(receipt, "preview")
    candidate = release("b" * 40, "0.2.8")
    installs = []
    def install(root, candidate, *, home):
        installs.append(candidate["commit"])
        return make_package(root, candidate)
    u.find_release = lambda channel, observation=None: candidate
    u.install = install
    result = u.apply()
    assert result["installed"]["commit"] == candidate["commit"]
    assert result["activation"] == "next_launch" and not result["processes_restarted"]
    for name in updater.COMMANDS:
        assert (u.home / ".local/bin" / name).resolve() == u.root / "versions" / candidate["commit"] / "bin" / name
    assert (u.home / ".agents/skills/bat-agent-connector/SKILL.md").read_bytes() == SKILL
    u.apply()
    assert installs == [candidate["commit"]]
    result = u.rollback()
    assert result["installed"]["commit"] == "a" * 40 and not result["enabled"]
    paused = u.apply()
    assert paused["installed"]["commit"] == "a" * 40 and not paused["enabled"]
    assert installs == [candidate["commit"]]


def update_tree(root):
    """Names, modes and bytes (including link targets), without following links."""
    if not root.exists():
        return None
    paths = [root, *root.rglob("*")]
    return {str(path.relative_to(root)): (
        path.lstat().st_mode,
        os.readlink(path) if path.is_symlink() else path.read_bytes() if path.is_file() else None,
    ) for path in paths}


@pytest.mark.parametrize("phase", ["not_enrolled", "enrolled", "installed", "failed", "pending"])
def test_status_is_byte_identical_and_does_not_create_a_lease(deployment, monkeypatch, phase):
    u, receipt, inspections = deployment
    if phase != "not_enrolled":
        u.enroll(receipt, "preview")
        u.find_release = lambda channel, observation=None: release("b" * 40, "0.2.8")
        u.install = lambda root, candidate, **_: make_package(root, candidate)
        if phase == "pending":
            reconcile = u.reconcile
            def interrupt(state):
                if state.get("pending"):
                    raise KeyboardInterrupt()
                return reconcile(state)
            monkeypatch.setattr(u, "reconcile", interrupt)
            with pytest.raises(KeyboardInterrupt):
                u.apply()
        elif phase == "failed":
            def fail(*args, **kwargs):
                raise UpdateError("fixture_install_failed")
            u.install = fail
            with pytest.raises(UpdateError):
                u.apply()
        elif phase == "installed":
            u.apply()
        (u.root / "lease").unlink()
    before = update_tree(u.root)
    inspected = len(inspections)
    def forbidden(*args, **kwargs):
        pytest.fail("status must not lock, reconcile, inspect, save, install or use the network")
    for name in ("locked", "reconcile", "save", "outcome", "install", "inspect", "find_release", "command"):
        monkeypatch.setattr(u, name, forbidden)
    if phase == "not_enrolled":
        with pytest.raises(FileNotFoundError):
            u.status()
    else:
        assert u.status()["pending"] == (phase == "pending")
    assert update_tree(u.root) == before
    assert len(inspections) == inspected


@pytest.mark.parametrize("change,code", [
    ("skill", "canonical_skill_changed"), ("source", "deployment_source_changed"),
    ("entrypoint", "deployment_entrypoint_changed"), ("receipt", "private"),
])
def test_enrollment_rejects_modified_or_missing_ownership_before_execution(deployment, change, code):
    u, receipt, inspections = deployment
    if change == "skill":
        (u.home / ".agents/skills/bat-agent-connector/SKILL.md").write_text("custom workflow")
    elif change == "source":
        package = receipt.parent / "tools/bat-agent-connector/lib/python3.13/site-packages/bat_agent_connector"
        (package / "__init__.py").write_text("raise RuntimeError('must not execute')")
    elif change == "entrypoint":
        entry = u.home / ".local/bin/batc"
        entry.unlink()
        entry.symlink_to("/unmanaged/command")
    else:
        receipt.chmod(0o644)
    before = {p: updater.snapshot(p) for p in u.links()}
    with pytest.raises(ValueError, match=code):
        u.enroll(receipt, "preview")
    assert {p: updater.snapshot(p) for p in u.links()} == before
    assert not (u.root / "current").exists()
    if change == "source":
        assert inspections == []


def test_failed_stage_preserves_active_version_and_failure_receipt(deployment):
    u, receipt, _ = deployment
    u.enroll(receipt, "preview")
    u.find_release = lambda channel, observation=None: release("b" * 40, "0.2.8")
    def fail(*args, **kwargs):
        raise UpdateError("fixture_download_failed")
    u.install = fail
    before = os.readlink(u.root / "current")
    with pytest.raises(UpdateError, match="fixture_download_failed"):
        u.apply()
    assert os.readlink(u.root / "current") == before
    assert u.read()["last"]["code"] == "fixture_download_failed"
    failure_path = u.root / "receipts" / (u.read()["last"]["receipt"] + ".json")
    original_failure = failure_path.read_bytes()
    u.install = lambda root, candidate, **kwargs: make_package(root, candidate)
    u.apply()
    assert failure_path.read_bytes() == original_failure


@pytest.mark.parametrize("position", ["before_pointer", "after_pointer"])
def test_interrupted_activation_reconciles_existing_install_without_reinstall(deployment, monkeypatch, position):
    u, receipt, _ = deployment
    u.enroll(receipt, "preview")
    candidate = release("b" * 40, "0.2.8")
    u.find_release = lambda channel, observation=None: candidate
    installs = []
    def install(root, candidate, *, home):
        installs.append(candidate["commit"])
        return make_package(root, candidate)
    u.install = install
    original = updater.replace_link
    def crash(path, target, expected):
        if position == "after_pointer":
            original(path, target, expected)
        raise KeyboardInterrupt("fixture process death")
    with monkeypatch.context() as patch:
        patch.setattr(updater, "replace_link", crash)
        with pytest.raises(KeyboardInterrupt):
            u.apply()
    assert u.read()["pending"]["to"] == candidate["commit"]
    assert u.apply()["installed"]["commit"] == candidate["commit"]
    assert installs == [candidate["commit"]]
    assert not u.read().get("pending")
    receipts = list((u.root / "receipts").glob("*.json"))
    assert len(receipts) == 1
    assert json.loads(receipts[0].read_bytes())["to"] == candidate["commit"]


def test_partial_enrollment_is_resumed_and_does_not_take_over_an_edited_link(deployment, monkeypatch):
    u, receipt, _ = deployment
    original = updater.replace_link
    calls = []
    def crash(path, target, expected):
        original(path, target, expected)
        calls.append(path)
        if len(calls) == 2:
            raise KeyboardInterrupt()
    with monkeypatch.context() as patch:
        patch.setattr(updater, "replace_link", crash)
        with pytest.raises(KeyboardInterrupt):
            u.enroll(receipt, "preview")
    assert u.read()["enrollment"]
    foreign = u.home / ".local/bin/bat-agent-connector-mcp"
    previous = os.readlink(foreign)
    foreign.unlink()
    foreign.symlink_to("/foreign/entrypoint")
    with pytest.raises(UpdateError, match="enrollment_target_changed"):
        u.policy(True)
    assert os.readlink(foreign) == "/foreign/entrypoint"
    foreign.unlink()
    foreign.symlink_to(previous)
    u.policy(True)
    assert not u.read().get("enrollment")


def test_active_or_rollback_source_edit_is_refused_before_interpreter(deployment):
    u, receipt, inspections = deployment
    u.enroll(receipt, "preview")
    inspections.clear()
    package = Path(u.read()["versions"]["a" * 40]["proof"]["package"])
    (package / "__init__.py").write_text("modified")
    with pytest.raises(UpdateError, match="verified_package_changed"):
        u.apply()
    assert inspections == []


def test_private_state_symlink_and_concurrent_lease_are_refused(deployment):
    u, receipt, _ = deployment
    u.enroll(receipt, "preview")
    with u.locked(), pytest.raises(UpdateError, match="already_running"):
        u.apply()
    state = u.root / "state.json"
    backup = u.root / "backup.json"
    state.rename(backup)
    state.symlink_to(backup)
    with pytest.raises(OSError):
        u.read()


def test_disabling_after_interruption_does_not_activate_a_pending_release(deployment, monkeypatch):
    u, receipt, _ = deployment
    u.enroll(receipt, "preview")
    u.find_release = lambda channel, observation=None: release("b" * 40, "0.2.8")
    u.install = lambda root, candidate, **kwargs: make_package(root, candidate)
    def crash(*args, **kwargs):
        raise KeyboardInterrupt()
    with monkeypatch.context() as patch:
        patch.setattr(updater, "replace_link", crash)
        with pytest.raises(KeyboardInterrupt):
            u.apply()
    before = os.readlink(u.root / "current")
    u.policy(False)
    assert u.apply()["pending"]
    assert os.readlink(u.root / "current") == before
    assert u.policy(True)["installed"]["commit"] == "b" * 40


def policy_file_snapshot(path):
    info = path.lstat()
    content = os.readlink(path) if path.is_symlink() else path.read_bytes() if path.is_file() else None
    return content, info.st_ino, info.st_mtime_ns


@contextmanager
def state_replacements(root):
    directory = root.stat()
    replacements = []
    observing = True
    def observe(event, args):
        if observing and event == "os.rename" and args[1] == "state.json" and args[3] >= 0:
            parent = os.fstat(args[3])
            if (parent.st_dev, parent.st_ino) == (directory.st_dev, directory.st_ino):
                with os.fdopen(os.open(args[0], os.O_RDONLY, dir_fd=args[2]), "rb") as source:
                    info = os.fstat(source.fileno())
                    saved = (source.read(), info.st_ino, info.st_mtime_ns)
                frame, reconciled = sys._getframe(1), False
                while frame:
                    reconciled |= frame.f_code.co_name == "reconcile"
                    frame = frame.f_back
                replacements.append((saved, reconciled))
    sys.addaudithook(observe)
    try:
        yield replacements
    finally:
        observing = False


def test_install_persists_pending_then_reconciled_state_without_rewriting(deployment):
    u, receipt, _ = deployment
    u.enroll(receipt, "preview")
    candidate = release("b" * 40, "0.2.8")
    u.find_release = lambda channel, observation=None: candidate
    u.install = lambda root, candidate, **kwargs: make_package(root, candidate)
    path = u.root / "state.json"
    with state_replacements(u.root) as replacements:
        result = u.apply()
    assert result["installed"]["commit"] == candidate["commit"] and not result["pending"]
    # Completed release discovery is durable independently of installation.
    assert len(replacements) == 3
    assert [reconciled for _, reconciled in replacements] == [False, False, True]
    checked = json.loads(replacements[0][0][0])
    assert checked["release_check"]["candidate"] == candidate
    assert checked["current"] == "a" * 40 and not checked.get("pending")
    published = json.loads(replacements[1][0][0])
    assert published["pending"]["to"] == candidate["commit"] and published["current"] == "a" * 40
    assert published["release_check"] == checked["release_check"]
    assert json.loads(replacements[2][0][0]) == u.read()
    assert policy_file_snapshot(path) == replacements[2][0]
    assert u.status() == result


def test_enable_after_pointer_refusal_only_saves_reconciled_state(deployment):
    u, receipt, _ = deployment
    u.enroll(receipt, "preview")
    candidate = release("b" * 40, "0.2.8")
    u.find_release = lambda channel, observation=None: candidate
    pointer = u.root / "current"
    def install(root, candidate, **kwargs):
        proof = make_package(root, candidate)
        pointer.unlink()
        pointer.symlink_to(u.home / "foreign-pointer-target")
        return proof
    u.install = install
    with pytest.raises(UpdateError, match="update_pointer_changed"):
        u.apply()
    interrupted = u.read()
    assert interrupted["enabled"] and interrupted["pending"]["to"] == candidate["commit"]
    assert interrupted["last"]["code"] == "update_pointer_changed"
    updater.replace_link(pointer, receipt.parent, updater.snapshot(pointer))
    with state_replacements(u.root) as replacements:
        result = u.policy(True, "preview")
    assert result["installed"]["commit"] == candidate["commit"] and not result["pending"]
    assert len(replacements) == 1
    assert replacements[0][1] is True
    assert policy_file_snapshot(u.root / "state.json") == replacements[0][0]
    assert json.loads(replacements[0][0][0]) == u.read()
    assert u.status() == result


@pytest.mark.parametrize("candidate", [None, release(), release("c" * 40, "0.2.6")])
def test_repeated_release_check_records_a_new_outcome(deployment, candidate):
    u, receipt, _ = deployment
    u.enroll(receipt, "preview")
    u.find_release = lambda channel, observation=None: candidate
    first = u.apply()
    path = u.root / "state.json"
    before = policy_file_snapshot(path)
    with path.open("rb") as original:
        second = u.apply()
        assert path.stat().st_ino != os.fstat(original.fileno()).st_ino
    assert second["last"]["status"] == first["last"]["status"]
    assert second["last"]["at"] != first["last"]["at"]
    assert path.read_bytes() != before[0] and u.status() == second


def test_repeated_schedule_preserves_completed_state_and_units(deployment, monkeypatch):
    u, receipt, _ = deployment
    u.enroll(receipt, "preview")
    calls = []
    original = updater.write_unit
    def write_unit(path, text):
        assert u.read()["schedule_intent"]
        assert not u.read().get("schedule_complete")
        original(path, text)
    monkeypatch.setattr(updater, "write_unit", write_unit)
    u.command = lambda args, **kwargs: calls.append(args)
    first = u.schedule()
    path = u.root / "state.json"
    before = policy_file_snapshot(path)
    units = {p: policy_file_snapshot(p) for p in (u.home / ".config/systemd/user").iterdir()}
    assert u.read()["schedule_intent"] and u.read()["schedule_complete"]
    assert u.schedule() == first
    assert len(calls) == 4 and calls[:2] == calls[2:]
    assert {p: policy_file_snapshot(p) for p in units} == units
    assert path.read_bytes() == before[0]
    assert path.stat().st_ino == before[1]
    assert path.stat().st_mtime_ns == before[2]


@pytest.mark.parametrize("channel", [None, "preview"])
def test_repeated_disable_preserves_state_links_and_receipts(deployment, monkeypatch, channel):
    u, receipt, _ = deployment
    u.enroll(receipt, "preview")
    # Retain a real update outcome as well as the original deployment receipt.
    u.find_release = lambda channel, observation=None: release("b" * 40, "0.2.8")
    u.install = lambda root, candidate, **kwargs: make_package(root, candidate)
    u.apply()
    calls = []
    def unexpected(*args, **kwargs):
        calls.append((args, kwargs))
        pytest.fail("Changing policy must not install a package or invoke the scheduler")
    monkeypatch.setattr(u, "install", unexpected)
    monkeypatch.setattr(u, "schedule", unexpected)
    monkeypatch.setattr(u, "command", unexpected)
    first = u.policy(False, channel)
    state = u.root / "state.json"
    assert not first["enabled"] and not first["pending"]
    before = policy_file_snapshot(state)
    links = {p: policy_file_snapshot(p) for p in [u.root / "current", *u.links()]}
    receipt_root = u.root / "receipts"
    assert len(list(receipt_root.glob("*.json"))) == 1
    def receipts():
        return {p: policy_file_snapshot(p) for p in [receipt, receipt_root, *receipt_root.rglob("*")]}
    saved_receipts = receipts()

    assert u.policy(False, channel) == first
    assert calls == []
    assert {p: policy_file_snapshot(p) for p in links} == links
    assert receipts() == saved_receipts
    assert state.read_bytes() == before[0]
    assert state.stat().st_ino == before[1]
    assert state.stat().st_mtime_ns == before[2]


@pytest.mark.parametrize("change", ["enable", "channel", "pending"])
def test_policy_changes_are_persisted_including_reconciliation(deployment, monkeypatch, change):
    u, receipt, _ = deployment
    u.enroll(receipt, "preview")
    if change == "pending":
        u.find_release = lambda channel, observation=None: release("b" * 40, "0.2.8")
        u.install = lambda root, candidate, **kwargs: make_package(root, candidate)
        def crash(*args, **kwargs):
            raise KeyboardInterrupt("fixture stops before pointer activation")
        with monkeypatch.context() as patch:
            patch.setattr(updater, "replace_link", crash)
            with pytest.raises(KeyboardInterrupt):
                u.apply()
        assert u.read()["enabled"] and u.read()["pending"]
    else:
        u.policy(False, "preview")
    path = u.root / "state.json"
    before = policy_file_snapshot(path)
    # Reconciliation can replace twice; keep the original inode allocated.
    with path.open("rb") as original:
        result = u.policy(change != "channel", "stable" if change == "channel" else "preview")
        assert path.stat().st_ino != os.fstat(original.fileno()).st_ino
        assert original.read() == before[0]
    saved = u.read()
    assert saved["enabled"] is result["enabled"] is (change != "channel")
    assert saved["channel"] == result["channel"] == ("stable" if change == "channel" else "preview")
    assert u.status() == result
    assert path.read_bytes() != before[0]
    if change == "pending":
        assert not saved.get("pending") and not result["pending"]
        assert saved["current"] == result["installed"]["commit"] == "b" * 40
        assert os.readlink(u.root / "current") == saved["versions"][saved["current"]]["root"]
        outcome = json.loads((u.root / "receipts" / (saved["last"]["receipt"] + ".json")).read_bytes())
        assert outcome["to"] == "b" * 40 and outcome["status"] == "installed_for_next_launch"


@pytest.mark.parametrize("guard,code", [("lease", "update_already_running"), ("channel", "invalid_channel"),
    ("state", "update_state_incompatible"), ("entrypoint", "managed_entrypoint_changed")])
def test_unchanged_policy_keeps_validation_and_lease(deployment, guard, code):
    u, receipt, _ = deployment
    u.enroll(receipt, "preview")
    enabled = guard == "entrypoint"
    if not enabled:
        u.policy(False, "preview")
    if guard == "state":
        state = u.read()
        u.save({**state, "format": 2})
    elif guard == "entrypoint":
        path = next(iter(u.links()))
        path.unlink()
        path.symlink_to(receipt.parent / "bin" / path.name)
    path = u.root / "state.json"
    before = policy_file_snapshot(path)
    if guard == "lease":
        with u.locked(), pytest.raises(UpdateError, match=code):
            u.policy(False, "preview")
    else:
        with pytest.raises(UpdateError, match=code):
            u.policy(enabled, "invalid" if guard == "channel" else "preview")
    assert policy_file_snapshot(path) == before


def test_scheduler_uses_owned_units_and_reconciles_systemctl_failure(deployment):
    u, receipt, _ = deployment
    u.enroll(receipt, "preview")
    calls = []
    def command(args, **kwargs):
        calls.append(args)
        if len(calls) == 2:
            raise UpdateError("fixture_systemd_unavailable")
    u.command = command
    with pytest.raises(UpdateError, match="systemd_unavailable"):
        u.schedule()
    assert not u.read().get("schedule_complete")
    assert u.schedule()["scheduler_configured"]
    assert calls[-1][-3:] == ["enable", "--now", "batc-update.timer"]
    unit = u.home / ".config/systemd/user/batc-update.service"
    assert 'ExecStart="%h/.local/bin/batc-update" run' in unit.read_text()
    unit.write_text("foreign scheduler command")
    with pytest.raises(UpdateError, match="scheduler_unit_changed"):
        u.schedule()


def test_existing_scheduler_unit_has_no_creation_evidence(deployment):
    u, receipt, _ = deployment
    u.enroll(receipt, "preview")
    directory = u.home / ".config/systemd/user"
    directory.mkdir(parents=True, mode=0o700)
    files.atomic_write(directory / "batc-update.timer", updater.TIMER.encode())
    with pytest.raises(UpdateError, match="scheduler_unit_not_owned"):
        u.schedule()


def test_scheduler_does_not_create_directories_through_a_foreign_link(deployment):
    u, receipt, _ = deployment
    u.enroll(receipt, "preview")
    foreign = u.home / "foreign"
    foreign.mkdir(mode=0o700)
    (u.home / ".config").symlink_to(foreign)
    with pytest.raises(OSError):
        u.schedule()
    assert list(foreign.iterdir()) == []


def test_enrollment_preserves_a_foreign_staged_skill(deployment):
    u, receipt, _ = deployment
    staged = receipt.parent / "skill/SKILL.md"
    staged.write_bytes(b"custom private skill")
    with pytest.raises(UpdateError, match="deployment_staged_skill_changed"):
        u.enroll(receipt, "preview")
    assert staged.read_bytes() == b"custom private skill"
    assert not (u.root / "current").exists()


def test_stage_refuses_unknown_or_changed_candidate_before_commands(tmp_path):
    root = tmp_path / "candidate"
    files.ensure_private_directory(root)
    (root / "foreign").write_text("preserve me")
    with pytest.raises(UpdateError, match="candidate_not_owned"):
        stage(root, release(), home=tmp_path)
    assert (root / "foreign").read_text() == "preserve me"
    (root / "foreign").unlink()
    files.atomic_write(root / "candidate.json", b"different release")
    with pytest.raises(UpdateError, match="candidate_identity_changed"):
        stage(root, release(), home=tmp_path)


@pytest.mark.parametrize("entries", [
    '[{name = "batc", install-path = "/foreign/batc", from = "bat-agent-connector"}]',
    '["malformed"]', '{}',
])
def test_partial_tool_install_cannot_follow_a_foreign_directory_or_receipt(tmp_path, entries):
    root = tmp_path / "candidate"
    foreign = tmp_path / "foreign"
    files.ensure_private_directory(root)
    files.ensure_private_directory(foreign)
    sentinel = foreign / "keep"
    sentinel.write_text("existing installation")
    (root / "tools").symlink_to(foreign, target_is_directory=True)
    with pytest.raises(OSError):
        prepare_tool_paths(root)
    assert sentinel.read_text() == "existing installation"
    (root / "tools").unlink()
    prepare_tool_paths(root)
    environment = root / "tools/bat-agent-connector"
    environment.mkdir(mode=0o700)
    (environment / "uv-receipt.toml").write_text('[tool]\nentrypoints = ' + entries + '\n')
    with pytest.raises(UpdateError, match="candidate_tool_receipt_changed"):
        prepare_tool_paths(root)
    assert sentinel.read_text() == "existing installation"


def test_command_timeout_output_limit_and_clean_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("BATC_API_TOKEN", "fixture-secret")
    monkeypatch.setenv("GIT_CONFIG_COUNT", "9")
    env = clean_env(tmp_path)
    assert "BATC_API_TOKEN" not in env
    assert env["GIT_CONFIG_COUNT"] == "2"
    assert (env["GIT_CONFIG_KEY_0"], env["GIT_CONFIG_VALUE_0"]) == ("maintenance.auto", "false")
    assert (env["GIT_CONFIG_KEY_1"], env["GIT_CONFIG_VALUE_1"]) == ("gc.auto", "0")
    with pytest.raises(UpdateError, match="command_timeout"):
        run([sys.executable, "-c", "import time; time.sleep(10)"], env=env, timeout=0.05)
    with pytest.raises(UpdateError, match="output_limit"):
        run([sys.executable, "-c", "print('x' * (5 * 1024 * 1024))"], env=env)
    with pytest.raises(UpdateError, match="command_failed"):
        run([sys.executable, "-c", "raise SystemExit(4)"], env=env)


@pytest.mark.parametrize("ignored_sigint", [False, True])
def test_interrupted_updater_reaps_its_installer_and_descendant(tmp_path, ignored_sigint):
    # Signal a separate updater process, never pytest or a live installation.
    ready = tmp_path / "ready.json"
    child_code = """
import json, os, signal, subprocess, sys, time
from pathlib import Path
child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])
def finish(*_):
    child.wait(timeout=5)
    raise SystemExit(0)
signal.signal(signal.SIGTERM, finish)
ready = Path(sys.argv[1])
ready.with_suffix(".tmp").write_text(json.dumps([os.getpid(), child.pid]))
ready.with_suffix(".tmp").replace(ready)
time.sleep(60)
"""
    runner_code = """
import signal, sys
from pathlib import Path
from bat_agent_connector.update_install import clean_env, run
signal.signal(signal.SIGINT, signal.default_int_handler)
try:
    run([sys.executable, '-c', sys.argv[2], sys.argv[3]], env=clean_env(Path(sys.argv[1])))
except KeyboardInterrupt:
    raise SystemExit(130)
"""
    # Bash background jobs (including local parallel shards) inherit SIGINT
    # ignored. Reproduce that state without changing pytest's signal handlers.
    launcher_code = """
import os, signal, sys
signal.signal(signal.SIGINT, signal.SIG_IGN if sys.argv[1] == 'True' else signal.default_int_handler)
os.execv(sys.executable, [sys.executable, '-c', *sys.argv[2:]])
"""
    runner = subprocess.Popen([sys.executable, "-c", launcher_code, str(ignored_sigint), runner_code,
                               str(tmp_path), child_code, str(ready)],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    owned = []
    try:
        deadline = time.monotonic() + 10
        while not ready.exists():
            assert runner.poll() is None and time.monotonic() < deadline
            time.sleep(0.02)
        owned = json.loads(ready.read_text())
        runner.send_signal(signal.SIGINT)
        # Readiness proves the interrupt handler is installed. This bound only
        # protects the suite from a hung runner; production keeps its 5s grace.
        assert runner.wait(timeout=10) == 130
        for pid in owned:
            with pytest.raises(ProcessLookupError):
                os.kill(pid, 0)
    finally:
        # A regression must not leave the test's own children behind.
        if ready.exists() and not owned:
            owned = json.loads(ready.read_text())
        if owned:
            try:
                os.killpg(owned[0], signal.SIGTERM)
            except ProcessLookupError:
                pass
        if runner.poll() is None:
            runner.kill()
        runner.wait(timeout=10)
