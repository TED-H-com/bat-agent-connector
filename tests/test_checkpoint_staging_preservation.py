"""Unproven checkpoint staging content must survive prepare and recovery."""
from __future__ import annotations

import os
import shlex
import shutil

import pytest

from bat_agent_connector import checkpoints
from bat_agent_connector.operations import StepFailed
from tests.test_checkpoints import LocalRunner, git, make_human, snapshot


@pytest.mark.parametrize('kind', ['directory', 'file', 'symlink', 'dangling_symlink'])
async def test_prepare_preserves_preexisting_staging(kind, tmp_path, checkpoint_repo_seed):
    human = make_human(tmp_path, checkpoint_repo_seed)
    before = snapshot(human)
    managed = tmp_path / 'managed'
    managed.mkdir()
    dest = managed / 'checkpoint-clone'
    staging = managed / 'checkpoint-clone.batc-tmp-original'
    outside = tmp_path / 'unrelated-content'
    outside.mkdir()
    protected = outside / 'keep.txt'
    protected.write_text('unpublished work\n')
    if kind == 'directory':
        staging.mkdir()
        (staging / 'keep.txt').write_text('staging content without creation evidence\n')
    elif kind == 'file':
        staging.write_text('unrelated file\n')
    else:
        staging.symlink_to(outside if kind == 'symlink' else tmp_path / 'absent', target_is_directory=True)
    info = staging.lstat()
    payload = (staging / 'keep.txt').read_bytes() if kind == 'directory' else staging.read_bytes() if kind == 'file' else None
    link = staging.readlink() if staging.is_symlink() else None
    head = git(human, 'rev-parse', 'HEAD')
    script = checkpoints.prepare_script(str(human), str(dest), str(dest / '.bat-worktrees/new'),
        'batc/cp-new', head, str(staging))
    with pytest.raises(StepFailed):
        await LocalRunner().run('test-host', script)
    after = staging.lstat()
    assert (info.st_dev, info.st_ino, info.st_mode, info.st_size, info.st_mtime_ns) == (
        after.st_dev, after.st_ino, after.st_mode, after.st_size, after.st_mtime_ns)
    if link is not None:
        assert staging.readlink() == link
    else:
        assert ((staging / 'keep.txt').read_bytes() if kind == 'directory' else staging.read_bytes()) == payload
    assert protected.read_text() == 'unpublished work\n'
    assert not dest.exists()
    assert snapshot(human) == before


async def test_prepare_keeps_staging_if_destination_appears_during_clone(
        tmp_path, checkpoint_repo_seed, monkeypatch):
    human = make_human(tmp_path, checkpoint_repo_seed)
    before = snapshot(human)
    managed = tmp_path / 'managed'
    managed.mkdir()
    dest = managed / 'checkpoint-clone'
    staging = managed / 'checkpoint-clone.tmp'
    head = git(human, 'rev-parse', 'HEAD')
    executable = shutil.which('git')
    assert executable
    helpers = tmp_path / 'helpers'
    helpers.mkdir()
    wrapper = helpers / 'git'
    wrapper.write_text('#!/bin/sh\n' + shlex.quote(executable) + ' "$@" || exit $?\n'
        + 'if [ "$1" = clone ]; then mkdir ' + shlex.quote(str(dest))
        + '; printf %s preserved > ' + shlex.quote(str(dest / 'keep.txt')) + '; fi\n')
    wrapper.chmod(0o700)
    monkeypatch.setenv('PATH', str(helpers) + os.pathsep + os.environ['PATH'])
    script = checkpoints.prepare_script(str(human), str(dest), str(dest / '.bat-worktrees/new'),
        'batc/cp-new', head, str(staging))
    with pytest.raises(StepFailed):
        await LocalRunner().run('test-host', script)
    assert staging.is_dir() and (staging / '.git').is_dir()
    assert (dest / 'keep.txt').read_text() == 'preserved'
    assert not (dest / '.git').exists()
    assert snapshot(human) == before
