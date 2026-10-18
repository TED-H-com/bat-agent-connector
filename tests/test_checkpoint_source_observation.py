"""Failed real Git observations stay unknown instead of proving a clean source."""
import shlex
from pathlib import Path
from types import SimpleNamespace

import pytest

from bat_agent_connector import checkpoints
from tests.test_checkpoints import LocalRunner, git


@pytest.fixture
def source(tmp_path, checkpoint_repo_seed):
    import shutil
    root = tmp_path / 'source with spaces'
    shutil.copytree(checkpoint_repo_seed, root)
    return root


async def observe(source):
    ops = SimpleNamespace(context={'git_runner': LocalRunner()})
    return await checkpoints.source_state(ops, 'fixture', str(source))


async def test_corrupt_index_is_unknown_and_source_bytes_are_preserved(source):
    head = git(source, 'rev-parse', 'HEAD')
    refs = git(source, 'for-each-ref')
    index = source / '.git/index'
    broken = b'not a valid Git index\n'
    index.write_bytes(broken)
    before = index.stat().st_mtime_ns
    assert await observe(source) is None
    assert index.read_bytes() == broken and index.stat().st_mtime_ns == before
    assert git(source, 'rev-parse', 'HEAD') == head and git(source, 'for-each-ref') == refs


async def test_successful_empty_and_nonempty_status_keep_exact_counts(source):
    head = git(source, 'rev-parse', 'HEAD')
    assert await observe(source) == (head, 0)
    (source / '__BATC_GIT_STATUS_OK__').write_text('A filename is a status row, not a completion marker.\n')
    (source / 'untracked\nname').write_text('Newline in a filename remains one porcelain row.\n')
    assert await observe(source) == (head, 2)


@pytest.mark.parametrize('program', ['fsmonitor', 'clean-filter'])
async def test_source_program_is_refused_without_execution_or_index_change(source, tmp_path, program):
    marker = tmp_path / 'source-program-executed'
    command = 'touch ' + shlex.quote(str(marker)) + '; cat'
    if program == 'fsmonitor':
        git(source, 'config', 'core.fsmonitor', command)
    else:
        git(source, 'config', 'filter.fixture.clean', command)
        (source / '.gitattributes').write_text('* filter=fixture\n')
        tracked = source / git(source, 'ls-files').splitlines()[0]
        data = tracked.read_bytes()
        tracked.write_bytes((b'Z' if data[:1] != b'Z' else b'Y') + data[1:])
    index = source / '.git/index'
    before = index.read_bytes(), index.stat().st_mtime_ns
    result = await observe(source)
    assert not marker.exists(), 'Source observation must not execute a configured program'
    assert result is None
    assert (index.read_bytes(), index.stat().st_mtime_ns) == before


async def test_source_with_lazy_fetch_configuration_remains_unknown(source):
    git(source, 'config', 'remote.origin.promisor', 'true')
    assert await observe(source) is None


async def test_inherited_git_program_and_index_overrides_are_not_used(source, tmp_path, monkeypatch):
    head = git(source, 'rev-parse', 'HEAD')
    marker = tmp_path / 'inherited-program-executed'
    fake_index = tmp_path / 'index'
    fake_index.write_bytes(b'not the selected repository index')
    monkeypatch.setenv('GIT_INDEX_FILE', str(fake_index))
    monkeypatch.setenv('GIT_CONFIG_COUNT', '1')
    monkeypatch.setenv('GIT_CONFIG_KEY_0', 'core.fsmonitor')
    monkeypatch.setenv('GIT_CONFIG_VALUE_0', 'touch ' + shlex.quote(str(marker)))
    assert await observe(source) == (head, 0)
    assert fake_index.read_bytes() == b'not the selected repository index'
    assert not marker.exists()


async def test_source_submodule_is_not_silently_ignored(source):
    head = git(source, 'rev-parse', 'HEAD')
    git(source, 'update-index', '--add', '--cacheinfo', '160000,' + head + ',nested')
    index = source / '.git/index'
    before = index.read_bytes(), index.stat().st_mtime_ns
    assert await observe(source) is None
    assert (index.read_bytes(), index.stat().st_mtime_ns) == before


async def test_config_include_is_refused_before_following_it(source, tmp_path):
    include = tmp_path / 'unreadable-or-executable-context'
    # Even a missing include must not silently turn into a complete clean proof.
    git(source, 'config', 'include.path', str(include))
    assert await observe(source) is None


async def test_linked_worktree_keeps_its_own_head_and_index(source, tmp_path):
    linked = tmp_path / 'linked source'
    git(source, 'worktree', 'add', '-b', 'fixture-linked', str(linked))
    head = git(linked, 'rev-parse', 'HEAD')
    # Git's internal basename is an implementation detail; query its fixed path.
    index = Path(git(linked, 'rev-parse', '--git-path', 'index'))
    before = index.read_bytes(), index.stat().st_mtime_ns
    assert await observe(linked) == (head, 0)
    assert (index.read_bytes(), index.stat().st_mtime_ns) == before
