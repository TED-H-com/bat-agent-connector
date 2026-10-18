"""Offline bootstrap and enrollment contract tests."""
import argparse
import hashlib
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from bat_agent_connector import installer, updater
from bat_agent_connector import platform_files as files
from bat_agent_connector.update_install import package_files
from scripts import install_connector as bootstrap
from tests.test_connector_updates import SKILL, make_package, publication, release


@pytest.fixture
def installation(tmp_path):
    home = tmp_path / "home"
    root = home / ".local/share/bat-agent-connector/releases" / ("a" * 40)
    candidate = release()
    proof = make_package(root, candidate)
    source = tmp_path / "source"
    shutil.copytree(Path(proof["package"]), source / "src/bat_agent_connector")
    files.atomic_write(source / "skills/bat-agent-connector/SKILL.md", SKILL)
    def inspect(root, candidate, *, home):
        return {**proof, "skill_sha256": hashlib.sha256((Path(proof["package"]) / "skill/SKILL.md").read_bytes()).hexdigest()}
    def finalize(**kwargs):
        return installer.finalize(root, candidate, home=home, source=source, inspect=inspect,
                                  command=lambda *a, **kw: candidate["version"],
                                  identity=lambda: {"version": candidate["version"], "commit": candidate["commit"],
                                                    "source": "vcs"}, **kwargs)
    return home, root, source, candidate, inspect, finalize


def test_receipt_is_accepted_by_real_enrollment_and_digest_matches(installation):
    home, root, source, candidate, inspect, finalize = installation
    summary = finalize()
    receipt = Path(summary["receipt"])
    assert receipt.name == "rollout-receipt.json"
    legacy = hashlib.sha256()
    for name, value in sorted(package_files(source / "src/bat_agent_connector", python_only=True).items()):
        legacy.update(name.encode() + b"\0" + bytes.fromhex(value))
    assert json.loads(receipt.read_text())["installed_proof"]["python_source_sha256"] == legacy.hexdigest()
    assert receipt.stat().st_mode & 0o777 == 0o600
    assert finalize(verify_only=True)["writes"] is False
    u = updater.Updater(home, inspect=inspect, find_release=lambda channel: candidate)
    assert u.enroll(receipt, "preview")["installation_matches_state"]


@pytest.mark.parametrize("tamper,code", [("source", "source_mismatch"), ("skill", "skill_mismatch")])
def test_tampering_never_produces_receipt(installation, tamper, code):
    home, root, source, candidate, inspect, finalize = installation
    package = root / "tools/bat-agent-connector/lib/python3.13/site-packages/bat_agent_connector"
    (package / ("__init__.py" if tamper == "source" else "skill/SKILL.md")).write_text("tampered")
    with pytest.raises(ValueError, match=code):
        finalize()
    assert not (root / "rollout-receipt.json").exists()
    assert not (home / ".local/bin/batc").exists()


def test_existing_entrypoint_preserved(installation):
    home, root, source, candidate, inspect, finalize = installation
    files.atomic_write(home / ".local/bin/batc", b"foreign command")
    with pytest.raises(ValueError, match="already_exists"):
        finalize()
    assert (home / ".local/bin/batc").read_bytes() == b"foreign command"
    assert not (root / "rollout-receipt.json").exists()


def transport(edit=None):
    item, raw = publication()
    if edit:
        doc = json.loads(raw)
        edit(doc)
        raw = json.dumps(doc).encode()
        item["assets"][0].update(size=len(raw), digest="sha256:" + hashlib.sha256(raw).hexdigest())
    checksum = hashlib.sha256(raw).hexdigest() + "  release-manifest.json\n"
    def download(url):
        if url.startswith(bootstrap.API):
            return json.dumps(item).encode()
        return checksum.encode() if url.endswith("SHA256SUMS") else raw
    return item, raw, download


@pytest.mark.parametrize("edit", [
    lambda d: d.update(repository="https://example.invalid"),
    lambda d: d.update(source_commit="main"),
    lambda d: d["validation"].update(status="pending"),
    lambda d: d["connector_update"].update(protocol=2),
    lambda d: d["versions"].update(connector="0.2.8"),
])
def test_bootstrap_rejects_wrong_manifest(edit):
    item, raw, download = transport(edit)
    with pytest.raises(bootstrap.InstallError):
        bootstrap.resolve(item["tag_name"], "preview", download)


def test_bootstrap_checks_asset_checksum_and_channel():
    item, raw, download = transport()
    assert bootstrap.resolve(item["tag_name"], "preview", download)["commit"] == "a" * 40
    with pytest.raises(bootstrap.InstallError, match="channel"):
        bootstrap.resolve(item["tag_name"], "stable", download)
    item["assets"][0]["digest"] = "sha256:" + "0" * 64
    def corrupt(url):
        return json.dumps(item).encode() if url.startswith(bootstrap.API) else download(url)
    with pytest.raises(bootstrap.InstallError, match="asset_digest"):
        bootstrap.resolve(item["tag_name"], "preview", corrupt)
    item["assets"][0]["digest"] = "sha256:" + hashlib.sha256(raw).hexdigest()
    with pytest.raises(bootstrap.InstallError, match="checksum"):
        bootstrap.resolve(item["tag_name"], "preview",
                          lambda url: b"bad" if url.endswith("SHA256SUMS") else download(url))


def options(**kwargs):
    return argparse.Namespace(tag=release()["tag"], channel_check="preview", root=None, dry_run=False,
                              verify_only=False, enable_updates=False, allow_root=True, **kwargs)


def test_dry_run_has_no_writes_or_commands(tmp_path):
    item, raw, download = transport()
    args = options()
    args.dry_run = True
    result = bootstrap.install(args, home=tmp_path / "home", download=download,
                               run=lambda *a, **kw: pytest.fail("unexpected command"))
    assert result["writes"] is False
    assert not (tmp_path / "home").exists()


def test_existing_updater_state_refused_before_network(tmp_path):
    home = tmp_path / "home"
    files.atomic_write(home / ".local/share/bat-agent-connector/update/state.json", b"{}")
    with pytest.raises(bootstrap.InstallError, match="already_update_managed"):
        bootstrap.install(options(), home=home, download=lambda u: pytest.fail("network"))


def test_bootstrap_uv_install_and_finalizer_are_pinned(tmp_path, monkeypatch):
    item, raw, download = transport()
    home = tmp_path / "home"
    calls = []
    monkeypatch.setattr(bootstrap.shutil, "which", lambda name: "/usr/bin/" + name)
    def run(args, **kwargs):
        calls.append(args)
        if "rev-parse" in args:
            return "a" * 40
        if "bat_agent_connector.installer" in args:
            assert args[:3][1:] == ["-I", "-m"]
            return json.dumps({"status": "installed", "receipt": "fixture"})
        return ""
    assert bootstrap.install(options(), home=home, download=download, run=run)["status"] == "installed"
    uv = next(c for c in calls if "install" in c)
    assert uv[-1] == "git+" + bootstrap.REPOSITORY + "@" + "a" * 40
    assert "--constraints" in uv and "--force" not in uv


def test_shell_syntax():
    subprocess.run(["bash", "-n", "scripts/install.sh"], check=True)
    subprocess.run(["sh", "-n", "scripts/install.sh"], check=True)


def test_verification_detects_changes_without_rewriting_receipt(installation):
    home, root, source, candidate, inspect, finalize = installation
    finalize()
    receipt = root / "rollout-receipt.json"
    before = receipt.read_bytes()
    package = root / "tools/bat-agent-connector/lib/python3.13/site-packages/bat_agent_connector"
    (package / "__init__.py").write_text("tampered")
    with pytest.raises(ValueError, match="receipt_mismatch"):
        finalize(verify_only=True)
    assert receipt.read_bytes() == before


def test_install_preserves_application_configuration(installation):
    home, root, source, candidate, inspect, finalize = installation
    config = home / ".config/bat-agent-connector"
    files.ensure_private_directory(config)
    for name in ("hosts.toml", "device-id", "credentials"):
        (config / name).write_text("fixture configuration")
    before = {p.name: p.read_bytes() for p in config.iterdir()}
    finalize()
    assert {p.name: p.read_bytes() for p in config.iterdir()} == before


def test_failed_uv_install_has_no_success_receipt(tmp_path, monkeypatch):
    item, raw, download = transport()
    home = tmp_path / "home"
    monkeypatch.setattr(bootstrap.shutil, "which", lambda name: "/usr/bin/" + name)
    def run(args, **kwargs):
        if "rev-parse" in args:
            return "a" * 40
        if "install" in args:
            raise bootstrap.InstallError("uv_install_command_failed")
        return ""
    with pytest.raises(bootstrap.InstallError, match="uv_install"):
        bootstrap.install(options(), home=home, download=download, run=run)
    assert not list(home.rglob("*receipt.json"))


def test_root_requires_explicit_allowance(tmp_path, monkeypatch):
    monkeypatch.setattr(bootstrap.os, "getuid", lambda: 0)
    args = options()
    args.allow_root = False
    with pytest.raises(bootstrap.InstallError, match="root_requires"):
        bootstrap.install(args, home=tmp_path, download=lambda url: pytest.fail("network"))


def test_verify_only_runs_no_uv_and_does_not_create_directories(tmp_path, monkeypatch):
    item, raw, download = transport()
    home = tmp_path / "home"
    root = home / ".local/share/bat-agent-connector/releases" / ("a" * 40)
    python = root / "tools/bat-agent-connector/bin/python"
    files.atomic_write(python, b"fixture interpreter")
    before = {str(p): (p.stat().st_mtime_ns, p.stat().st_size) for p in home.rglob("*")}
    monkeypatch.setattr(bootstrap.shutil, "which", lambda name: "/usr/bin/" + name)
    args = options()
    args.verify_only = True
    def run(args, **kwargs):
        assert args[0] == str(python) and "-B" in args and "--verify-only" in args
        return '{"status": "verified_installation", "writes": false}'
    assert bootstrap.install(args, home=home, download=download, run=run)["writes"] is False
    assert {str(p): (p.stat().st_mtime_ns, p.stat().st_size) for p in home.rglob("*")} == before
