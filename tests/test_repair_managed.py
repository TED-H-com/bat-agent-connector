import inspect
import json
from pathlib import Path

import pytest

from bat_agent_connector import platform_files as files
from bat_agent_connector import repair_managed
from scripts import install_connector as bootstrap
from tests.test_connector_installer import options, transport, tree_snapshot
from tests.test_connector_updates import make_deployment, make_package, release


@pytest.fixture
def managed(tmp_path):
    engine, receipt, _ = make_deployment(tmp_path)
    engine.enroll(receipt, 'preview')
    record = engine.read()['versions']['a' * 40]
    package = Path(record['proof']['package'])
    metadata = package.parent / 'bat_agent_connector-0.2.7.dist-info/direct_url.json'
    files.atomic_write(metadata, json.dumps({'url': bootstrap.REPOSITORY + '.git',
        'vcs_info': {'vcs': 'git', 'commit_id': 'a' * 40}}).encode())
    files.atomic_write(Path(record['root']) / 'tools/bat-agent-connector/uv-receipt.toml', b'[tool]\n')
    return engine, metadata


def test_validation_counterparts_match():
    assert inspect.getsource(bootstrap.verify_repair).replace('InstallError', 'UpdateError') == inspect.getsource(
        repair_managed.verify_repair)


def test_repair_dry_run_is_read_only(managed):
    engine, _ = managed
    _, _, download = transport()
    args = options(repair_managed=True)
    args.dry_run = True
    before = tree_snapshot(engine.home)
    result = bootstrap.install(args, home=engine.home, download=download,
                               run=lambda *a, **kw: pytest.fail('command during dry run'))
    assert result['status'] == 'verified_repair' and result['writes'] is False
    assert tree_snapshot(engine.home) == before


@pytest.mark.parametrize('change,code', [
    ('json', 'repair_state_invalid'), ('symlink', 'repair_state_invalid'),
    ('mode', 'repair_state_invalid'), ('large', 'repair_state_invalid'),
    ('schema', 'repair_state_invalid'), ('non-git', 'repair_install_not_managed'),
    ('foreign', 'repair_install_not_managed'),
])
def test_repair_refusals_before_network(managed, change, code):
    engine, metadata = managed
    state = engine.root / 'state.json'
    if change == 'json':
        state.write_text('{')
    elif change == 'schema':
        state.write_text('{}')
    elif change == 'symlink':
        backup = state.with_suffix('.backup')
        state.rename(backup)
        state.symlink_to(backup)
    elif change == 'mode':
        state.chmod(0o644)
    elif change == 'large':
        state.write_bytes(b' ' * (1024 * 1024 + 1))
    else:
        doc = json.loads(metadata.read_bytes())
        if change == 'non-git':
            doc['vcs_info']['vcs'] = 'hg'
        else:
            doc['url'] = 'https://example.invalid/other'
        metadata.write_text(json.dumps(doc))
    before = tree_snapshot(engine.home)
    with pytest.raises(bootstrap.InstallError, match=code):
        bootstrap.install(options(repair_managed=True), home=engine.home,
                          download=lambda *a: pytest.fail('network'))
    assert tree_snapshot(engine.home) == before


def test_repair_uses_updater_activation_and_rollback(managed):
    engine, _ = managed
    candidate = release('b' * 40, '0.2.16')
    staged = []
    def stage(root, release, *, home):
        staged.append((root, release))
        return make_package(root, release)
    engine.install = stage
    result = repair_managed.repair(candidate, home=engine.home, updater=engine)
    assert staged == [(engine.root / 'versions' / candidate['commit'], candidate)]
    assert result['status'] == 'repaired'
    state = engine.read()
    assert state['current'] == 'b' * 40 and state['previous'] == 'a' * 40
    assert 'pending' not in state
    assert engine.rollback()['installed']['commit'] == 'a' * 40


def test_repair_failed_stage_preserves_current(managed):
    engine, _ = managed
    def fail(*a, **kw):
        raise ValueError('installed_identity_mismatch')
    engine.install = fail
    before = (engine.root / 'state.json').read_bytes()
    with pytest.raises(ValueError, match='installed_identity_mismatch'):
        repair_managed.repair(release('b' * 40, '0.2.16'), home=engine.home, updater=engine)
    assert (engine.root / 'state.json').read_bytes() == before
    assert engine.status()['installed']['commit'] == 'a' * 40


def test_bootstrap_runs_new_installer_repair_path(managed, monkeypatch):
    engine, _ = managed
    _, _, download = transport(candidate=release('b' * 40, '0.2.16'))
    monkeypatch.setattr(bootstrap.shutil, 'which', lambda name: '/usr/bin/' + name)
    calls = []
    def command(args, **kwargs):
        calls.append(args)
        if 'rev-parse' in args:
            return 'b' * 40
        if 'bat_agent_connector.installer' in args:
            assert '--repair-managed' in args
            return '{"status":"repaired"}'
        return ''
    args = options(repair_managed=True)
    args.tag = release('b' * 40, '0.2.16')['tag']
    assert bootstrap.install(args, home=engine.home, download=download, run=command)['status'] == 'repaired'
    assert any('--constraints' in call for call in calls)


@pytest.mark.parametrize('flag', ['--adopt-existing', '--restore-adopted'])
def test_repair_modes_mutually_exclusive(flag):
    with pytest.raises(SystemExit):
        bootstrap.main(['--repair-managed', flag, *(['fixture'] if flag == '--restore-adopted' else [])])


def test_repair_accepts_updater_scratch_clone_url(managed):
    """v0.2.12 stage() installs from its private scratch clone, so uv records a file: URL."""
    engine, metadata = managed
    root = Path(engine.read()["versions"]["a" * 40]["root"])
    url = (root / "scratch").as_uri() + "/source-abc_123/repository"
    files.atomic_write(metadata, json.dumps({"url": url, "vcs_info": {"vcs": "git", "commit_id": "a" * 40}}).encode())
    assert repair_managed.verify_repair(engine.home)["current"] == "a" * 40
    files.atomic_write(metadata, json.dumps({"url": "file:///tmp/source-x/repository",
                                             "vcs_info": {"vcs": "git", "commit_id": "a" * 40}}).encode())
    with pytest.raises(repair_managed.UpdateError, match="repair_install_not_managed"):
        repair_managed.verify_repair(engine.home)
