"""Updater fixtures never contact a release feed or operate a live Connector."""
from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path

import pytest

from bat_agent_connector import platform_files as files
from bat_agent_connector import updater
from bat_agent_connector.update_install import clean_env, digest_files, package_files, run, stage
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
    result = discover("preview", download=download)
    assert result["commit"] == "a" * 40
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


@pytest.fixture
def deployment(tmp_path):
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
    u = updater.Updater(home, inspect=inspect, find_release=lambda channel: candidate)
    return u, receipt, inspections


def test_enrollment_and_update_keep_work_and_switch_all_future_launches(deployment):
    u, receipt, _ = deployment
    sentinel = u.home / "existing-operation-journal"
    sentinel.write_bytes(b"original operation keys and unknown effects")
    u.enroll(receipt, "preview")
    candidate = release("b" * 40, "0.2.8")
    installs = []
    def install(root, candidate, *, home):
        installs.append(candidate["commit"])
        return make_package(root, candidate)
    u.find_release = lambda channel: candidate
    u.install = install
    result = u.apply()
    assert result["installed"]["commit"] == candidate["commit"]
    assert result["activation"] == "next_launch" and not result["processes_restarted"]
    for name in updater.COMMANDS:
        assert (u.home / ".local/bin" / name).resolve() == u.root / "versions" / candidate["commit"] / "bin" / name
    assert (u.home / ".agents/skills/bat-agent-connector/SKILL.md").read_bytes() == SKILL
    assert sentinel.read_bytes() == b"original operation keys and unknown effects"
    u.apply()
    assert installs == [candidate["commit"]]
    result = u.rollback()
    assert result["installed"]["commit"] == "a" * 40 and not result["enabled"]
    u.apply()
    assert installs == [candidate["commit"]]
    assert sentinel.read_bytes() == b"original operation keys and unknown effects"


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
    u.find_release = lambda channel: release("b" * 40, "0.2.8")
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
    u.find_release = lambda channel: candidate
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
    u.find_release = lambda channel: release("b" * 40, "0.2.8")
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


def test_command_timeout_output_limit_and_clean_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("BATC_API_TOKEN", "fixture-secret")
    monkeypatch.setenv("GIT_CONFIG_COUNT", "9")
    env = clean_env(tmp_path)
    assert "BATC_API_TOKEN" not in env and "GIT_CONFIG_COUNT" not in env
    with pytest.raises(UpdateError, match="command_timeout"):
        run([sys.executable, "-c", "import time; time.sleep(10)"], env=env, timeout=0.05)
    with pytest.raises(UpdateError, match="output_limit"):
        run([sys.executable, "-c", "print('x' * (5 * 1024 * 1024))"], env=env)
    with pytest.raises(UpdateError, match="command_failed"):
        run([sys.executable, "-c", "raise SystemExit(4)"], env=env)
