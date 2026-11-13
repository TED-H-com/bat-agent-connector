import errno
import inspect
import json
import os
import stat
from pathlib import Path
from types import SimpleNamespace

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


def test_private_read_policy_constants_match():
    """Fails on b910d6e9: repair readers did not have a retry policy."""
    for module in (repair_managed, bootstrap):
        assert module.PRIVATE_READ_DEADLINE_S == files.PRIVATE_READ_DEADLINE_S == 0.5
        assert module.PRIVATE_READ_RETRY_S == files.PRIVATE_READ_RETRY_S == 0.02


@pytest.fixture(params=[repair_managed, bootstrap], ids=['installed', 'bootstrap'])
def repair_reader(request, monkeypatch):
    module = request.param

    class Clock:
        now = 0.0

        def __init__(self):
            self.sleeps = []

        def monotonic(self):
            return self.now

        def sleep(self, seconds):
            assert 0 < seconds <= 0.5 - self.now + 1e-12
            self.sleeps.append(seconds)
            self.now = round(self.now + seconds, 9)

    clock = Clock()
    # Baseline has no time import. Replace only this module's reference.
    monkeypatch.setattr(module, 'time', clock, raising=False)
    return module, clock


@pytest.fixture
def repair_open_race(managed, repair_reader, monkeypatch):
    engine, _ = managed
    module, clock = repair_reader
    path = engine.root / 'state.json'
    opened = []

    def inject(replaced=0, *, open_elapsed=0, invalid=None, remove=False, open_error=None):
        content = path.read_bytes()
        replacements = []
        for index in range(replaced):
            replacement = path.with_name(f'replacement-{index}')
            replacement.write_bytes(content)
            replacement.chmod(0o600)
            replacements.append(replacement)
        attempts = []

        def opening(name, flags):
            assert name == path
            assert flags == os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
            attempts.append(clock.now)
            assert len(attempts) <= 40, 'unbounded repair retry'
            if open_error:
                raise OSError(open_error, 'synthetic open refusal')
            fd = os.open(name, flags)
            opened.append(fd)
            if len(attempts) <= len(replacements):
                os.replace(replacements[len(attempts) - 1], path)
                assert os.fstat(fd).st_nlink == 0
                if remove:
                    path.unlink()
            clock.now += open_elapsed
            return fd

        def fstat(fd):
            info = os.fstat(fd)
            values = {name: getattr(info, name) for name in ('st_mode', 'st_uid', 'st_nlink', 'st_size')}
            if invalid == 'owner':
                values['st_uid'] += 1
            elif invalid == 'group':
                values['st_mode'] |= 0o040
            elif invalid == 'other':
                values['st_mode'] |= 0o001
            elif invalid == 'large':
                values['st_size'] = 1024 * 1024 + 1
            elif invalid == 'growth':
                values['st_size'] = 1024 * 1024
            elif invalid == 'nonregular':
                values['st_mode'] = stat.S_IFIFO | 0o600
            return SimpleNamespace(**values)

        monkeypatch.setattr(module, 'os', SimpleNamespace(**{**vars(os), 'open': opening, 'fstat': fstat}))
        return attempts

    yield inject
    for fd in opened:
        try:
            os.fstat(fd)
        except OSError as error:
            assert error.errno == errno.EBADF
        else:
            os.close(fd)
            pytest.fail('repair reader leaked its descriptor')


def test_repair_retries_replaced_inode(managed, repair_reader, repair_open_race):
    """Fails on b910d6e9: the first replaced inode is refused."""
    module, clock = repair_reader
    attempts = repair_open_race(1)
    assert module.verify_repair(managed[0].home)['current'] == 'a' * 40
    assert attempts == [0, 0]
    assert clock.sleeps == []


def test_repair_replacement_retry_is_bounded(managed, repair_reader, repair_open_race):
    """Fails on b910d6e9: refusal is immediate, without the bounded retry."""
    module, clock = repair_reader
    attempts = repair_open_race(40)
    with pytest.raises(ValueError, match='^repair_state_invalid$'):
        module.verify_repair(managed[0].home)
    assert len(attempts) == 30
    assert attempts[:2] == [0, 0] and max(attempts) < 0.5
    assert clock.sleeps == pytest.approx([0.001, 0.002, 0.004, 0.008, 0.016] + [0.020] * 23 + [0.009])
    assert clock.now == 0.5


@pytest.mark.parametrize('replaced', [4, 10])
def test_repair_waits_until_replacement_settles(managed, repair_reader, repair_open_race, replaced):
    """Both cases fail on b910d6e9: the first replaced inode is refused."""
    module, clock = repair_reader
    attempts = repair_open_race(replaced)
    assert module.verify_repair(managed[0].home)['current'] == 'a' * 40
    assert len(attempts) == replaced + 1
    expected = [0.001, 0.002, 0.004] if replaced == 4 else [0.001, 0.002, 0.004, 0.008, 0.016] + [0.020] * 4
    assert clock.sleeps == pytest.approx(expected)


def test_repair_expired_first_open_does_not_retry(managed, repair_reader, repair_open_race):
    """Passes on b910d6e9: preserve immediate refusal after an expired first open."""
    module, clock = repair_reader
    attempts = repair_open_race(1, open_elapsed=0.501)
    with pytest.raises(ValueError, match='^repair_state_invalid$'):
        module.verify_repair(managed[0].home)
    assert len(attempts) == 1 and clock.sleeps == []


@pytest.mark.parametrize('replaced', [0, 1])
@pytest.mark.parametrize('invalid', ['owner', 'group', 'other', 'large', 'nonregular'])
def test_repair_invalid_stat_is_not_retried(managed, repair_reader, repair_open_race, replaced, invalid):
    """All cases pass on b910d6e9, including invalid files with zero links."""
    module, clock = repair_reader
    attempts = repair_open_race(replaced, invalid=invalid)
    with pytest.raises(ValueError, match='^repair_state_invalid$'):
        module.verify_repair(managed[0].home)
    assert len(attempts) == 1 and clock.sleeps == []


@pytest.mark.parametrize('invalid', ['hardlink', 'symlink', 'fifo', 'directory', 'json', 'schema', 'missing'])
def test_repair_other_refusals_are_immediate(managed, repair_reader, repair_open_race, invalid):
    """b910d6e9 refuses immediately; directory also exposes an existing fd leak."""
    module, clock = repair_reader
    attempts = repair_open_race()
    path = managed[0].root / 'state.json'
    if invalid == 'hardlink':
        os.link(path, path.with_suffix('.alias'))
    elif invalid in ('symlink', 'fifo', 'directory', 'missing'):
        path.rename(path.with_suffix('.original'))
        if invalid == 'symlink':
            path.symlink_to(path.with_suffix('.original'))
        elif invalid == 'fifo':
            os.mkfifo(path, 0o600)
        elif invalid == 'directory':
            path.mkdir(mode=0o700)
    else:
        path.write_text('{' if invalid == 'json' else '{}')
    with pytest.raises(ValueError, match='^repair_state_invalid$'):
        module.verify_repair(managed[0].home)
    assert len(attempts) == 1 and clock.sleeps == []


@pytest.mark.parametrize('code', [errno.EACCES, errno.EIO])
def test_repair_open_errors_are_immediate(managed, repair_reader, repair_open_race, code):
    """Both cases pass on b910d6e9: retain the original mapped error without retry."""
    module, clock = repair_reader
    attempts = repair_open_race(open_error=code)
    with pytest.raises(ValueError, match='^repair_state_invalid$'):
        module.verify_repair(managed[0].home)
    assert len(attempts) == 1 and clock.sleeps == []


def test_repair_read_growth_is_immediate(managed, repair_reader, repair_open_race):
    """Passes on b910d6e9: a file growing after fstat still exceeds the read bound."""
    module, clock = repair_reader
    attempts = repair_open_race(invalid='growth')
    (managed[0].root / 'state.json').write_bytes(b' ' * (1024 * 1024 + 1))
    with pytest.raises(ValueError, match='^repair_state_invalid$'):
        module.verify_repair(managed[0].home)
    assert len(attempts) == 1 and clock.sleeps == []


def test_repair_replaced_then_removed_keeps_error(managed, repair_reader, repair_open_race):
    """Fails on b910d6e9 on attempt count; the original error code is unchanged."""
    module, clock = repair_reader
    attempts = repair_open_race(1, remove=True)
    with pytest.raises(ValueError, match='^repair_state_invalid$'):
        module.verify_repair(managed[0].home)
    assert len(attempts) == 2 and clock.sleeps == []


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
