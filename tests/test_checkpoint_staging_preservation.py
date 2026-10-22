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
        if key in {'clone_path', 'retained_staging_path', 'source_host', 'source_session_id'}}
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
@pytest.mark.parametrize('banner', [False, True])
async def test_continue_records_retained_copy_for_operation_page(
        daemon, human, mock, tmp_path, monkeypatch, refusal, code, banner):
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
    if banner:
        class ProfileRunner(LocalRunner):
            async def run(self, host, script, timeout_s=None):
                return await super().run(host, "printf '%s\\n' 'profile banner'\n" + script, timeout_s)
        daemon.ops.context['git_runner'] = ProfileRunner()
    try:
        await settle_operations(daemon.ops)
        failed = daemon.ops.get(op['operation_id'])
        document = operation_fixture(failed)
        golden = Path(__file__).parent / 'fixtures' / f'checkpoint-refusal-{refusal}.json'
        if os.environ.get('BATC_UPDATE_CHECKPOINT_GOLDEN') == '1':
            golden.write_text(json.dumps(document, ensure_ascii=False, indent=2) + '\n')
        assert failed['status'] == 'failed' and failed['error_code'] == code, failed
        assert 'new checkpoint.continue operation' in failed['status_reason']
        assert 'new idempotency key' in failed['status_reason']
        assert failed['external_refs']['retained_staging_path'] == str(staging)
        assert failed['external_refs']['source_host'] == cp['host']
        assert failed['external_refs']['source_session_id'] == cp['source_session_id']
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


async def test_prepare_reports_unknown_refusal_code_with_login_output(daemon, human, mock, tmp_path):
    cp = await producer.make_checkpoint(daemon)
    before = snapshot(human)
    code = 'CHECKPOINT_PREPARE_UNSUPPORTED'

    class NewerHostRefusal(LocalRunner):
        async def run(self, host, script, timeout_s=None):
            # A newer host step can add an error CODE detail result to the existing protocol.
            script = script.replace('error CHECKPOINT_STAGING_RETAINED', f'error {code} unsupported host step')
            return await super().run(host, "printf '%s\\n' 'profile banner'\n" + script, timeout_s)

    daemon.ops.context['git_runner'] = NewerHostRefusal()
    op, _ = daemon.ops.create(producer.OPERATOR, action='checkpoint.continue',
        target={'checkpoint_id': cp['checkpoint_id']}, params={'instructions': 'Continue from this version'},
        idempotency_key='unknown-refusal')
    dest = Path(checkpoints.clone_path(str(tmp_path / 'managed'), 'h1', str(human)))
    staging = Path(f'{dest}.batc-tmp-{op["operation_id"][3:15]}')
    staging.mkdir(parents=True)
    (staging / 'keep.txt').write_text('kept\n')
    try:
        await settle_operations(daemon.ops)
        failed = daemon.ops.get(op['operation_id'])
        assert failed['status'] == 'failed' and failed['error_code'] == code, failed
        assert failed['status_reason'] == f'{code} unsupported host step'
        assert failed['steps'][0]['error']['code'] == code
        assert 'retained_staging_path' not in failed['external_refs']
        assert (staging / 'keep.txt').read_text() == 'kept\n'
        assert snapshot(human) == before
        assert not producer.bat_writes(mock)
    finally:
        await daemon.fleet.close()
        await daemon.inventory.close()


async def test_prepare_rejects_broken_destination_symlink_before_cloning(tmp_path, checkpoint_repo_seed):
    human = make_human(tmp_path, checkpoint_repo_seed)
    before = snapshot(human)
    managed = tmp_path / 'managed'
    managed.mkdir()
    dest = Path(checkpoints.clone_path(str(managed), 'test-host', str(human)))
    dest.symlink_to(tmp_path / 'absent', target_is_directory=True)
    inode, target = dest.lstat().st_ino, dest.readlink()
    for suffix in ['0123456789ab', 'abcdef012345']:
        staging = Path(f'{dest}.batc-tmp-{suffix}')
        script = checkpoints.prepare_script(str(human), str(dest), str(dest / f'.bat-worktrees/batc-cp-{suffix}'),
            f'batc/cp-{suffix}', git(human, 'rev-parse', 'HEAD'), str(staging))
        with pytest.raises(checkpoints.GitCommandFailed, match='not a connector clone'):
            await LocalRunner().run('test-host', script)
        assert not staging.exists() and not staging.is_symlink()
    assert dest.is_symlink() and dest.lstat().st_ino == inode and dest.readlink() == target
    assert not list(managed.glob(dest.name + '.batc-tmp-*'))
    assert snapshot(human) == before


async def test_prepare_uses_origin_despite_host_clone_default(tmp_path, checkpoint_repo_seed, monkeypatch):
    human = make_human(tmp_path, checkpoint_repo_seed)
    git(human, 'remote', 'set-url', 'origin', producer.ORIGIN)
    before = snapshot(human)
    monkeypatch.setenv('GIT_CONFIG_COUNT', '1')
    monkeypatch.setenv('GIT_CONFIG_KEY_0', 'clone.defaultRemoteName')
    monkeypatch.setenv('GIT_CONFIG_VALUE_0', 'upstream')
    dest = Path(checkpoints.clone_path(str(tmp_path / 'managed'), 'test-host', str(human)))
    suffix = '0123456789ab'
    worktree = dest / f'.bat-worktrees/batc-cp-{suffix}'
    head = git(human, 'rev-parse', 'HEAD')
    script = checkpoints.prepare_script(str(human), str(dest), str(worktree), f'batc/cp-{suffix}',
        head, f'{dest}.batc-tmp-{suffix}')
    assert await LocalRunner().run('test-host', script) == f'{head}\n0'
    assert git(dest, 'remote') == 'origin'
    assert git(dest, 'remote', 'get-url', 'origin') == producer.ORIGIN
    assert snapshot(human) == before


async def test_prepare_quotes_managed_root_with_spaces_and_shell_characters(tmp_path, checkpoint_repo_seed):
    human = make_human(tmp_path, checkpoint_repo_seed)
    before = snapshot(human)
    managed = tmp_path / "managed spaces ' ; $(touch injected) `touch injected-too` &"
    dest = Path(checkpoints.clone_path(str(managed), 'test-host', str(human)))
    suffix = '0123456789ab'
    staging = Path(f'{dest}.batc-tmp-{suffix}')
    worktree = dest / f'.bat-worktrees/batc-cp-{suffix}'
    head = git(human, 'rev-parse', 'HEAD')
    script = checkpoints.prepare_script(str(human), str(dest), str(worktree), f'batc/cp-{suffix}', head, str(staging))
    runner = LocalRunner()
    for _ in range(2):
        assert await runner.run('test-host', f'cd {shlex.quote(str(tmp_path))}\n' + script) == f'{head}\n0'
    assert not staging.exists() and git(worktree, 'rev-parse', 'HEAD') == head
    assert not (tmp_path / 'injected').exists() and not (tmp_path / 'injected-too').exists()
    assert snapshot(human) == before


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
