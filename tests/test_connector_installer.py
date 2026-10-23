"""Offline bootstrap and enrollment contract tests."""
import argparse
import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from bat_agent_connector import installer, updater
from bat_agent_connector import platform_files as files
from bat_agent_connector.update_install import package_files
from bat_agent_connector.update_release import EPOCH, EntrypointDirectoryError, UpdateError, manifest
from scripts import install_connector as bootstrap
from tests.test_connector_updates import SKILL, make_package, publication, release


def tree_snapshot(home):
    """Ignore access times, which traversal and ordinary reads change."""
    snapshot = {}
    for path in home.rglob('*'):
        info = path.lstat()
        snapshot[path] = (
            info.st_mode, info.st_ino, info.st_uid, info.st_gid, info.st_nlink,
            info.st_size, info.st_mtime_ns, info.st_ctime_ns,
        )
    return snapshot


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
    u = updater.Updater(home, inspect=inspect, find_release=lambda channel, observation=None: candidate)
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


def transport(edit=None, *, candidate=None):
    item, raw = publication(candidate)
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


@pytest.mark.parametrize("epoch", [EPOCH - 1, EPOCH, EPOCH + 1])
def test_bootstrap_accepted_epoch_matches_updater_constant(epoch):
    item, _raw, download = transport(lambda doc: doc["connector_update"].update(compatibility_epoch=epoch))
    if epoch == EPOCH:
        assert bootstrap.resolve(item["tag_name"], "preview", download)["epoch"] == EPOCH
    else:
        with pytest.raises(bootstrap.InstallError, match="manifest_update_incompatible"):
            bootstrap.resolve(item["tag_name"], "preview", download)


@pytest.mark.parametrize("edit,accepted", [
    pytest.param(None, True, id="valid"),
    pytest.param(lambda d: d.update(format=2), False, id="format"),
    pytest.param(lambda d: d.update(repository="https://example.invalid"), False, id="repository"),
    pytest.param(lambda d: d.update(release="v0.2.8"), False, id="release"),
    pytest.param(lambda d: d.update(source_commit="main"), False, id="source_commit"),
    pytest.param(lambda d: d["versions"].update(connector="0.02.7"), False, id="version_format"),
    pytest.param(lambda d: d["versions"].update(connector="0.2.8"), False, id="version_tag"),
    pytest.param(lambda d: d["connector_update"].update(protocol=2), False, id="protocol"),
    pytest.param(lambda d: d["connector_update"].update(compatibility_epoch=EPOCH + 1), False, id="epoch"),
    pytest.param(lambda d: d["connector_update"].update(python="3.10"), False, id="python"),
    pytest.param(lambda d: d["connector_update"].update(canonical_skill_sha256="invalid"), False, id="skill_digest"),
    pytest.param(lambda d: d["validation"].update(status="pending"), False, id="validation"),
])
def test_bootstrap_and_updater_agree_on_shared_manifest_fields(edit, accepted):
    # Existing publication producer and transport keep both parser inputs identical.
    # Bootstrap's minimum package version and asset/checksum transport policy are separate.
    item, raw, download = transport(edit)
    if accepted:
        assert bootstrap.resolve(item["tag_name"], "preview", download) == manifest(item, raw)
    else:
        with pytest.raises(bootstrap.InstallError):
            bootstrap.resolve(item["tag_name"], "preview", download)
        with pytest.raises(UpdateError):
            manifest(item, raw)


def test_bootstrap_minimum_version_floor_is_an_intentional_difference():
    item, raw, download = transport(candidate=release(ver="0.2.6"))
    assert manifest(item, raw)["version"] == "0.2.6"
    with pytest.raises(bootstrap.InstallError, match="updater_release_required"):
        bootstrap.resolve(item["tag_name"], "preview", download)


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
    uv_environments = []
    monkeypatch.setattr(bootstrap.shutil, "which", lambda name: "/usr/bin/" + name)
    def run(args, **kwargs):
        calls.append(args)
        if args[0] == "/usr/bin/uv":
            uv_environments.append(kwargs["env"])
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
    assert uv_environments
    for env in uv_environments:
        assert env["GIT_CONFIG_COUNT"] == "2"
        assert (env["GIT_CONFIG_KEY_0"], env["GIT_CONFIG_VALUE_0"]) == ("maintenance.auto", "false")
        assert (env["GIT_CONFIG_KEY_1"], env["GIT_CONFIG_VALUE_1"]) == ("gc.auto", "0")


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


@pytest.fixture
def legacy(installation):
    home, root, source, candidate, inspect, finalize = installation
    tool = home / '.local/share/uv/tools/bat-agent-connector'
    package = tool / 'lib/python3.13/site-packages/bat_agent_connector'
    shutil.copytree(source / 'src/bat_agent_connector', package)
    metadata = package.parent / 'bat_agent_connector-0.2.7.dist-info'
    metadata.mkdir()
    (metadata / 'direct_url.json').write_text(json.dumps({'vcs_info': {'vcs': 'git', 'commit_id': candidate['commit']}}))
    (metadata / 'METADATA').write_text('\nVersion: 0.2.7\n')
    files.ensure_private_directory(home / '.local/bin')
    for name in installer.COMMANDS:
        files.atomic_write(tool / 'bin' / name, b'fixture')
        (home / '.local/bin' / name).symlink_to(tool / 'bin' / name)
    skill = home / '.agents/skills/bat-agent-connector/SKILL.md'
    files.atomic_write(skill, SKILL)
    receipt = home / '.config/bat-agent-connector/install-receipt-connector.json'
    doc = {'format': 1, 'repository': bootstrap.REPOSITORY, 'commit': candidate['commit'],
           'package_version': '0.2.7', 'canonical_sha256': hashlib.sha256(SKILL).hexdigest(),
           'executable': str(tool / 'bin/batc'), 'bundles': [{'adapter': 'generic', 'path': str(skill)}],
           'installed_proof': {'package_path': str(package), 'python_source_sha256': installer.source_digest(package),
                               'packaged_workflow_sha256': hashlib.sha256(SKILL).hexdigest()}}
    files.atomic_write(receipt, json.dumps(doc).encode())
    return installation, tool, package, skill, receipt


def test_adoption_enrollment_and_restore(legacy):
    installation, tool, package, skill, receipt = legacy
    home, root, source, candidate, inspect, finalize = installation
    before = receipt.read_bytes()
    adoption = bootstrap.verify_adoption(home, candidate)
    summary = finalize(adoption=adoption)
    assert receipt.read_bytes() == before and package.exists()
    assert installer.restore_adopted(root, home=home)['status'] == 'restored_adopted'
    assert (home / '.local/bin/batc').resolve() == tool / 'bin/batc'
    installer.adoption_links(home, root, adoption)
    u = updater.Updater(home, inspect=inspect, find_release=lambda channel, observation=None: candidate)
    assert u.enroll(Path(summary['receipt']), 'preview')['installation_matches_state']
    with pytest.raises(ValueError, match='adoption_'):
        installer.restore_adopted(root, home=home)


@pytest.mark.parametrize('change,code', [
    ('package', 'source_changed'), ('foreign', 'entrypoint_changed'), ('regular', 'entrypoint_changed'),
    ('skill', 'skill_changed'), ('outside', 'location_mismatch'), ('downgrade', 'downgrade_refused'),
    ('identity', 'identity_mismatch'), ('types', 'receipt_invalid'),
])
def test_adoption_refusals(legacy, change, code):
    installation, tool, package, skill, receipt = legacy
    home, root, source, candidate, inspect, finalize = installation
    link = home / '.local/bin/batc'
    if change == 'package':
        (package / '__init__.py').write_text('modified')
    elif change in ('foreign', 'regular'):
        link.unlink()
        if change == 'foreign':
            link.symlink_to(root / 'bin/batc')
        else:
            link.write_text('custom')
    elif change == 'skill':
        skill.write_text('custom')
    elif change in ('outside', 'types'):
        doc = json.loads(receipt.read_bytes())
        doc['installed_proof']['package_path'] = str(root) if change == 'outside' else []
        receipt.write_text(json.dumps(doc))
    elif change == 'identity':
        next(package.parent.glob('*.dist-info/direct_url.json')).write_text('{}')
    elif change == 'downgrade':
        candidate = {**candidate, 'version': '0.2.6'}
    with pytest.raises(ValueError, match=code):
        bootstrap.verify_adoption(home, candidate)
    assert not (root / 'rollout-receipt.json').exists()


def test_adoption_swap_failure_restores_links(legacy, monkeypatch):
    installation, tool, package, skill, receipt = legacy
    home, root, source, candidate, inspect, finalize = installation
    adoption = bootstrap.verify_adoption(home, candidate)
    original = installer.swap_link
    calls = []
    def swap(path, target):
        calls.append(path)
        if len(calls) == 2:
            raise OSError('fixture failure')
        original(path, target)
    monkeypatch.setattr(installer, 'swap_link', swap)
    with pytest.raises(ValueError, match='adoption_swap_failed'):
        finalize(adoption=adoption)
    for name in installer.COMMANDS:
        assert (home / '.local/bin' / name).resolve() == tool / 'bin' / name
    assert skill.read_bytes() == SKILL
    assert not (root / 'rollout-receipt.json').exists()


def test_restore_refuses_changed_link(legacy):
    installation, tool, package, skill, receipt = legacy
    home, root, source, candidate, inspect, finalize = installation
    finalize(adoption=bootstrap.verify_adoption(home, candidate))
    link = home / '.local/bin/batc'
    link.unlink()
    link.symlink_to(tool / 'bin/batc')
    with pytest.raises(ValueError, match='entrypoint_changed'):
        installer.restore_adopted(root, home=home)
    assert (home / '.local/bin/batc-update').readlink() == root / 'bin/batc-update'


def test_bootstrap_adoption_uses_verified_install_and_can_upgrade(legacy, monkeypatch):
    installation, tool, package, skill, receipt = legacy
    home, root, source, candidate, inspect, finalize = installation
    skill.chmod(0o644)
    skill.parent.chmod(0o755)
    args = options(adopt_existing=True)
    args.root = home / '.local/share/bat-agent-connector/releases/new'
    item, raw, download = transport()
    monkeypatch.setattr(bootstrap.shutil, 'which', lambda name: '/usr/bin/' + name)
    def run(arguments, **kwargs):
        if 'rev-parse' in arguments:
            return candidate['commit']
        if 'bat_agent_connector.installer' in arguments:
            adoption = json.loads(arguments[arguments.index('--adoption') + 1])
            new_root = Path(arguments[arguments.index('bat_agent_connector.installer') + 1])
            proof = make_package(new_root, candidate)
            source_path = Path(arguments[arguments.index('--source') + 1])
            shutil.copytree(source / 'src/bat_agent_connector', source_path / 'src/bat_agent_connector')
            files.atomic_write(source_path / 'skills/bat-agent-connector/SKILL.md', SKILL)
            return json.dumps(installer.finalize(new_root, candidate, source=source_path, home=home,
                inspect=lambda *a, **kw: proof, command=lambda *a, **kw: candidate['version'],
                identity=lambda: {'version': candidate['version'], 'commit': candidate['commit'], 'source': 'vcs'},
                adoption=adoption))
        return ''
    result = bootstrap.install(args, home=home, download=download, run=run)
    proof = {"package": str(args.root / "tools/bat-agent-connector/lib/python3.13/site-packages/bat_agent_connector"),
             "skill_sha256": candidate["skill_sha256"]}
    from bat_agent_connector.update_install import digest_files
    proof.update(files=digest_files(package_files(Path(proof["package"]))),
                 python_files=digest_files(package_files(Path(proof["package"]), python_only=True)))
    u = updater.Updater(home, inspect=lambda *a, **kw: proof,
                        find_release=lambda channel, observation=None: candidate)
    assert u.enroll(Path(result['receipt']), 'preview')['installation_matches_state']


def test_adoption_version_policy(legacy):
    installation, tool, package, skill, receipt = legacy
    home, root, source, candidate, inspect, finalize = installation
    assert bootstrap.verify_adoption(home, {**candidate, 'version': '0.2.8'})
    assert bootstrap.verify_adoption(home, {**candidate, 'version': '0.2.6'}, allow_downgrade=True)
    files.atomic_write(home / '.local/share/bat-agent-connector/update/state.json', b'{}')
    with pytest.raises(ValueError, match='already_update_managed'):
        bootstrap.verify_adoption(home, candidate)


def test_entrypoint_directory_error_names_directory_without_chmod(installation):
    home, root, source, candidate, inspect, finalize = installation
    directory = home / '.local/bin'
    directory.mkdir(parents=True, exist_ok=True)
    directory.chmod(0o775)
    with pytest.raises(UpdateError) as caught:
        finalize()
    assert str(caught.value) == 'entrypoint_directory_not_owned'
    assert caught.value.diagnostic()['directory'] == str(directory)
    assert caught.value.diagnostic()['hint'] == 'chmod g-w,o-w ' + str(directory)
    assert directory.stat().st_mode & 0o777 == 0o775


@pytest.mark.parametrize('verify_only', [False, True])
@pytest.mark.parametrize('mode', [0o775, 0o757])
def test_config_directory_error_names_directory_without_chmod(installation, verify_only, mode):
    home, root, source, candidate, inspect, finalize = installation
    if verify_only:
        finalize()
    directory = home / '.config'
    directory.mkdir()
    directory.chmod(mode)
    # Force traversal to update directory access times on relatime mounts.
    for path in list(home.rglob('*')):
        if path.is_dir():
            info = path.stat()
            os.utime(path, ns=(0, info.st_mtime_ns))
    before = tree_snapshot(home)
    with pytest.raises(EntrypointDirectoryError) as caught:
        finalize(verify_only=verify_only)
    assert caught.value.diagnostic() == {
        'code': 'entrypoint_directory_not_owned', 'directory': str(directory),
        'hint': 'chmod g-w,o-w ' + str(directory),
    }
    assert tree_snapshot(home) == before


@pytest.mark.parametrize('mode', [None, 0o700, 0o755])
def test_healthy_or_missing_config_directory_passes_without_chmod(installation, mode):
    home, root, source, candidate, inspect, finalize = installation
    directory = home / '.config'
    if mode is not None:
        directory.mkdir()
        directory.chmod(mode)
    assert finalize()['status'] == 'installed'
    assert finalize(verify_only=True)['writes'] is False
    if mode is None:
        assert not directory.exists()
    else:
        assert directory.stat().st_mode & 0o777 == mode


@pytest.mark.parametrize('reason', ['directory', 'other'])
def test_verify_only_surfaces_known_directory_diagnostic(tmp_path, monkeypatch, capsys, reason):
    home = tmp_path / 'home'
    root = home / 'release'
    python = root / 'tools/bat-agent-connector/bin/python'
    files.atomic_write(python, b'fixture')
    monkeypatch.setattr(bootstrap.Path, 'home', lambda: home)
    monkeypatch.setattr(bootstrap, 'resolve', lambda *a: release())
    monkeypatch.setattr(bootstrap.shutil, 'which', lambda name: '/usr/bin/' + name)
    directory = home / '.local/bin'
    diagnostic = json.dumps({'code': 'entrypoint_directory_not_owned', 'directory': str(directory)}).encode()
    def run(args, **kwargs):
        assert '--verify-only' in args
        assert kwargs['capture_output'] is True
        return subprocess.CompletedProcess(args, 1, b'', diagnostic if reason == 'directory' else b'other_failure')
    monkeypatch.setattr(bootstrap.subprocess, 'run', run)
    assert bootstrap.main(['--tag', 'v0.2.7', '--root', str(root), '--verify-only', '--allow-root']) == 1
    error = capsys.readouterr().err
    if reason == 'directory':
        assert 'entrypoint_directory_not_owned' in error
        assert str(directory) in error
        assert 'chmod g-w,o-w ' + str(directory) in error
        assert 'verification_command_failed' not in error
    else:
        assert 'verification_command_failed' in error


def test_verifier_cli_serializes_directory_detail(tmp_path, monkeypatch, capsys):
    from bat_agent_connector.update_release import EntrypointDirectoryError

    directory = tmp_path / '.config'
    def fail(*args, **kwargs):
        raise EntrypointDirectoryError(directory)
    monkeypatch.setattr(installer, 'finalize', fail)
    assert installer.main([str(tmp_path), '{}', '--verify-only']) == 1
    detail = json.loads(capsys.readouterr().err)
    assert detail == {'code': 'entrypoint_directory_not_owned', 'directory': str(directory),
                      'hint': 'chmod g-w,o-w ' + str(directory)}
