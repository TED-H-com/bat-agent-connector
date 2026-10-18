"""Failed real Git observations stay unknown instead of proving a clean source."""
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
