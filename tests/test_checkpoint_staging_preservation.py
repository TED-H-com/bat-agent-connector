"""Unproven checkpoint staging content must survive prepare and recovery."""
from __future__ import annotations

import json
import os
import shlex
import shutil
from pathlib import Path

import pytest

from bat_agent_connector import checkpoints
from bat_agent_connector.operations import AmbiguousOutcome
from tests import test_checkpoints as producer
from tests.operation_helpers import settle_operations
from tests.test_checkpoints import LocalRunner, git, make_human, snapshot

daemon = producer.daemon
human = producer.human


def destination_after_clone(tmp_path, dest, monkeypatch):
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


@pytest.mark.parametrize('kind', ['directory', 'file', 'symlink', 'dangling_symlink'])
async def test_prepare_preserves_preexisting_staging(kind, tmp_path, checkpoint_repo_seed):
    human = make_human(tmp_path, checkpoint_repo_seed)
    before = snapshot(human)
    managed = tmp_path / 'managed'
    managed.mkdir()
    dest = Path(checkpoints.clone_path(str(managed), 'test-host', str(human)))
    suffix = '0123456789ab'
    staging = Path(f'{dest}.batc-tmp-{suffix}')
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
    script = checkpoints.prepare_script(str(human), str(dest), str(dest / f'.bat-worktrees/batc-cp-{suffix}'),
        f'batc/cp-{suffix}', head, str(staging))
    runner = LocalRunner()
    for _ in range(2):
        assert await runner.run('test-host', script) == 'error CHECKPOINT_STAGING_RETAINED'
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
    dest = Path(checkpoints.clone_path(str(managed), 'test-host', str(human)))
    suffix = '0123456789ab'
    staging = Path(f'{dest}.batc-tmp-{suffix}')
    head = git(human, 'rev-parse', 'HEAD')
    destination_after_clone(tmp_path, dest, monkeypatch)
    script = checkpoints.prepare_script(str(human), str(dest), str(dest / f'.bat-worktrees/batc-cp-{suffix}'),
        f'batc/cp-{suffix}', head, str(staging))
    assert await LocalRunner().run('test-host', script) == 'error CHECKPOINT_DESTINATION_APPEARED'
    assert staging.is_dir() and (staging / '.git').is_dir()
    assert git(staging, 'rev-parse', 'HEAD') == head
    assert git(staging, 'config', '--get', 'batc.managed-clone') == 'true'
    assert git(staging, 'config', '--get', 'batc.source') == str(human)
    assert (dest / 'keep.txt').read_text() == 'preserved'
    assert not (dest / '.git').exists()
    assert snapshot(human) == before


def operation_fixture(operation):
    """Project the operation page's fields; normalize only IDs, paths and timestamps."""
    document = {key: operation[key] for key in ('operation_id', 'actor', 'entry', 'action', 'target',
        'params', 'preconditions', 'status', 'status_reason', 'error_code', 'result', 'created_at')}
    document['external_refs'] = {key: value for key, value in operation['external_refs'].items()
        if key in {'clone_path', 'retained_staging_path'}}
    document['steps'] = [{key: step[key] for key in ('seq', 'name', 'status', 'error')} for step in operation['steps']]
    document['created_at'] = 1_790_000_000
    return json.loads(json.dumps(document)
        .replace(operation['external_refs']['clone_path'], '/srv/managed/app-11111111')
        .replace(operation['operation_id'], 'op_' + 'c' * 32)
        .replace(operation['operation_id'][3:15], 'c' * 12)
        .replace(operation['target']['checkpoint_id'], 'cp_' + 'a' * 32))


@pytest.mark.parametrize('refusal,code', [
    ('staging', 'CHECKPOINT_STAGING_RETAINED'),
    ('destination', 'CHECKPOINT_DESTINATION_APPEARED'),
])
async def test_continue_records_retained_copy_for_operation_page(
        daemon, human, mock, tmp_path, monkeypatch, refusal, code):
    cp = await producer.make_checkpoint(daemon)
    before = snapshot(human)
    op, _ = daemon.ops.create(producer.OPERATOR, action='checkpoint.continue',
        target={'checkpoint_id': cp['checkpoint_id']}, params={'instructions': 'Continue from this version'},
        idempotency_key='retained-copy')
    dest = Path(checkpoints.clone_path(str(tmp_path / 'managed'), 'h1', str(human)))
    staging = Path(f'{dest}.batc-tmp-{op["operation_id"][3:15]}')
    if refusal == 'staging':
        staging.mkdir(parents=True)
        (staging / 'keep.txt').write_text('earlier partial copy\n')
    else:
        destination_after_clone(tmp_path, dest, monkeypatch)
    try:
        await settle_operations(daemon.ops)
        failed = daemon.ops.get(op['operation_id'])
        document = operation_fixture(failed)
        golden = Path(__file__).parent / 'fixtures' / f'checkpoint-refusal-{refusal}.json'
        if os.environ.get('BATC_UPDATE_CHECKPOINT_GOLDEN') == '1':
            golden.write_text(json.dumps(document, ensure_ascii=False, indent=2) + '\n')
        assert failed['status'] == 'failed' and failed['error_code'] == code, failed
        assert failed['external_refs']['retained_staging_path'] == str(staging)
        assert failed['steps'][0]['name'] == 'worktree.prepare'
        assert failed['steps'][0]['status'] == 'failed'
        assert failed['steps'][0]['error']['code'] == code
        assert staging.is_dir()
        assert snapshot(human) == before
        assert not producer.bat_writes(mock)
        assert document == json.loads(golden.read_text())
        if refusal == 'staging':
            again = await producer.run(daemon, 'checkpoint.continue', {'checkpoint_id': cp['checkpoint_id']},
                {'instructions': 'Continue from this version'}, key='new-attempt')
            assert again['status'] == 'succeeded', again
            assert again['operation_id'] != failed['operation_id']
            assert 'retained_staging_path' not in again['external_refs']
            assert (staging / 'keep.txt').read_text() == 'earlier partial copy\n'
            assert snapshot(human) == before
        else:
            assert (dest / 'keep.txt').read_text() == 'preserved'
            assert git(staging, 'rev-parse', 'HEAD') == cp['commit_sha']
    finally:
        await daemon.fleet.close()
        await daemon.inventory.close()


async def test_uncertain_prepare_records_kept_clone_on_rerun(daemon, human, mock, tmp_path, monkeypatch):
    cp = await producer.make_checkpoint(daemon)
    before = snapshot(human)
    executable = shutil.which('git')
    assert executable
    helpers = tmp_path / 'helpers'
    helpers.mkdir()
    wrapper = helpers / 'git'
    wrapper.write_text('#!/bin/sh\n' + shlex.quote(executable) + ' "$@" || exit $?\n'
        + 'if [ "$1" = clone ]; then echo "fixture lost reply" >&2; exit 255; fi\n')
    wrapper.chmod(0o700)
    monkeypatch.setenv('PATH', str(helpers) + os.pathsep + os.environ['PATH'])

    class LostCloneReply(LocalRunner):
        async def run(self, host, script, timeout_s=None):
            try:
                return await super().run(host, script, timeout_s)
            except checkpoints.GitCommandFailed as exc:
                if len(self.scripts) == 1:
                    assert exc.message == 'fixture lost reply'
                    # SSH exit 255 is ambiguous; the real local shell has already made the clone.
                    raise AmbiguousOutcome('fixture lost SSH reply after clone') from exc
                raise

    runner = LostCloneReply()
    daemon.ops.context['git_runner'] = runner
    try:
        uncertain = await producer.run(daemon, 'checkpoint.continue', {'checkpoint_id': cp['checkpoint_id']},
            {'instructions': 'Continue from this version'}, key='lost-clone-reply')
        assert uncertain['status'] == 'uncertain', uncertain
        staging = Path(f'{uncertain["external_refs"]["clone_path"]}.batc-tmp-{uncertain["operation_id"][3:15]}')
        kept = {str(p.relative_to(staging)): p.read_bytes() for p in staging.rglob('*') if p.is_file()}
        assert (staging / '.git').is_dir()
        daemon.journal.db.execute('UPDATE operations SET next_run_at=0 WHERE operation_id=?', (uncertain['operation_id'],))
        await settle_operations(daemon.ops)
        failed = daemon.ops.get(uncertain['operation_id'])
        assert failed['status'] == 'failed' and failed['error_code'] == 'CHECKPOINT_STAGING_RETAINED', failed
        assert failed['external_refs']['retained_staging_path'] == str(staging)
        assert failed['steps'][0]['error']['code'] == 'CHECKPOINT_STAGING_RETAINED'
        assert len(runner.scripts) == 2 and runner.scripts[0] == runner.scripts[1]
        assert kept == {str(p.relative_to(staging)): p.read_bytes() for p in staging.rglob('*') if p.is_file()}
        assert snapshot(human) == before
        assert not producer.bat_writes(mock)
    finally:
        await daemon.fleet.close()
        await daemon.inventory.close()
