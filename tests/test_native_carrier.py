"""Real disk Git behind the existing SSH runner's test transport; no native CLI."""
import base64
import json
import os
import re
import shlex
import shutil
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from bat_agent_connector import native_carrier as carrier
from bat_agent_connector import native_journal
from bat_agent_connector.operations import AmbiguousOutcome, NeedsAttention, OperationError
from bat_agent_connector.task_journal import Journal
from tests.test_checkpoints import LocalRunner, git, snapshot
from tests.test_native_journal import launcher


@contextmanager
def refused(code):
    with pytest.raises((OperationError, NeedsAttention)) as exc:
        yield
    assert re.fullmatch(code, exc.value.code), exc.value.code


def files(root):
    return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob('*') if p.is_file()}


class Runner(LocalRunner):
    def __init__(self):
        super().__init__()
        self.before, self.after, self.phases = {}, {}, []

    async def run(self, host, script, timeout_s=None):
        req = json.loads(base64.b64decode(shlex.split(script)[-1]))
        phase = req['phase']
        self.phases.append(phase)
        if phase in self.before:
            self.before.pop(phase)(req)
        result = await super().run(host, script, timeout_s)
        if phase in self.after:
            self.after.pop(phase)(req)
        return result


class Context:
    def __init__(self, runner, operation_id='op_' + '1' * 32):
        self.operation_id = operation_id
        self.service = SimpleNamespace(context={'git_runner': runner})
        self.saved = {}

    async def step(self, name, effect, *, request, reconcile):
        if name not in self.saved:
            self.saved[name] = await effect()
        return self.saved[name]


@pytest.fixture
def world(tmp_path):
    source, managed = tmp_path / 'human', tmp_path / 'managed'
    source.mkdir()
    managed.mkdir()
    git(source, 'init', '--template=', '-q', '-b', 'main')
    git(source, 'config', 'user.name', 'fixture')
    git(source, 'config', 'user.email', 'fixture@example.invalid')
    (source / 'hello').write_text('committed\n')
    git(source, 'add', '.')
    git(source, 'commit', '-qm', 'source')
    sha = git(source, 'rev-parse', 'HEAD')
    (source / 'hello').write_text('staged\n')
    git(source, 'add', 'hello')
    (source / 'hello').write_text('unstaged\n')
    (source / 'untracked').write_text('private\n')
    runner = Runner()
    return SimpleNamespace(source=source, managed=managed, runner=runner, ctx=Context(runner),
        fixed=carrier.FixedSource('h1', 'o/r', sha, str(source)), checks=[])


async def prepare(w, attempt='attempt-one'):
    async def check():
        w.checks.append('guard')
    return await carrier.prepare(w.ctx, w.fixed, attempt_id=attempt, managed_root=str(w.managed),
                                 artifact_manifest_digest=carrier.EMPTY_MANIFEST, check=check)


async def readback(w, receipt):
    async def check():
        w.checks.append('guard')
    return await carrier.readback(w.ctx, receipt, check=check)


async def test_fixed_checkpoint_receipt_and_launch_reference(world, tmp_path):
    w = world
    before = snapshot(w.source)
    receipt = await prepare(w)
    data = receipt.reference()
    assert data['creation_operation_id'] == w.ctx.operation_id
    assert data['host'] == 'h1' and data['repository'] == 'o/r'
    assert data['attempt_id'] == 'attempt-one' and data['source_sha'] == w.fixed.sha
    assert data['artifact_manifest_digest'] == carrier.EMPTY_MANIFEST
    assert git(data['cwd'], 'rev-parse', 'HEAD') == w.fixed.sha
    assert Path(data['cwd'], 'hello').read_text() == 'committed\n'
    assert Path(data['git_dir']['path']).is_relative_to(data['clone']['path'])
    for name in ['clone', 'worktree', 'git_dir']:
        actual = os.stat(data[name]['path'])
        assert data[name]['device'] == actual.st_dev and data[name]['inode'] == actual.st_ino
    assert await readback(w, receipt) is receipt
    assert snapshot(w.source) == before and len(w.checks) >= 4
    journal = Journal(tmp_path / 'native.db')
    try:
        native_journal.schema(journal)
        intent, _ = launcher(journal)
        intent = replace(intent, carrier_receipt=data, cwd=data['cwd'], source_sha=data['source_sha'])
        row = native_journal.record_intent(journal, intent)
        assert row['data']['carrier_receipt'] == data
    finally:
        journal.close()


async def test_attempts_have_private_objects_and_git_metadata(world):
    first = (await prepare(world)).reference()
    world.ctx = Context(world.runner, 'op_' + '2' * 32)
    second = (await prepare(world, 'attempt-two')).reference()
    assert first['clone'] != second['clone'] and first['branch'] != second['branch']
    objects = []
    for receipt in [first, second]:
        root = Path(receipt['clone']['path'], '.git', 'objects')
        assert not (root / 'info/alternates').exists()
        files = [p for p in root.rglob('*') if p.is_file()]
        assert files and all(p.stat().st_nlink == 1 for p in files)
        objects.append({(p.stat().st_dev, p.stat().st_ino) for p in files})
    assert objects[0].isdisjoint(objects[1])


async def test_published_source_reuses_fixed_fetch(world):
    world.fixed = replace(world.fixed, ref='refs/heads/main')
    receipt = await prepare(world)
    assert receipt.reference()['source_sha'] == world.fixed.sha
    assert world.runner.phases == ['fetch', 'prepare', 'read.carrier']


async def test_existing_path_without_original_receipt_is_never_adopted(world):
    receipt = await prepare(world)
    before = files(Path(receipt.reference()['cwd']))
    world.ctx.saved.clear()  # Even matching markers cannot substitute for the original receipt.
    with refused('NATIVE_CARRIER_CREATION_UNPROVEN'):
        await prepare(world)
    assert files(Path(receipt.reference()['cwd'])) == before


@pytest.mark.parametrize('sha', ['main', 'refs/heads/main', 'a' * 39, 'a' * 41])
async def test_branch_name_never_substitutes_for_fixed_sha(world, sha):
    world.fixed = replace(world.fixed, sha=sha)
    with refused('NATIVE_SOURCE_UNPROVEN'):
        await prepare(world)
    assert not world.runner.scripts and not list(world.managed.iterdir())


@pytest.mark.parametrize('kind', ['alternates', 'replace', 'promisor', 'partial'])
async def test_source_cannot_replace_objects_or_lazy_fetch(world, kind):
    source = world.source
    if kind == 'alternates':
        (source / '.git/objects/info/alternates').write_text(str(source / '.git/objects'))
    elif kind == 'replace':
        git(source, 'update-ref', 'refs/replace/' + world.fixed.sha, world.fixed.sha)
    elif kind == 'promisor':
        git(source, 'config', 'remote.origin.promisor', 'true')
    else:
        git(source, 'config', 'extensions.partialClone', 'origin')
    before = files(source)
    with refused('NATIVE_SOURCE_UNPROVEN'):
        await prepare(world)
    assert files(source) == before


async def test_receipt_cannot_be_minted_from_request_dict(world):
    with refused('NATIVE_CARRIER_CREATION_UNPROVEN'):
        carrier.CarrierReceipt({'cwd': str(world.source)})
    with refused('NATIVE_CARRIER_CREATION_UNPROVEN'):
        await readback(world, {'cwd': str(world.source)})
    assert not world.runner.scripts


@pytest.mark.parametrize('change', ['branch', 'base', 'inode', 'alternates', 'gitdir', 'hardlink', 'worktree_copy'])
async def test_readback_refuses_drift(world, change):
    receipt = await prepare(world)
    data = receipt.reference()
    path, clone = Path(data['cwd']), Path(data['clone']['path'])
    if change == 'branch':
        git(path, 'checkout', '-qb', 'foreign')
    elif change == 'base':
        git(path, '-c', 'user.name=fixture', '-c', 'user.email=fixture@example.invalid',
            'commit', '--allow-empty', '-qm', 'drift')
    elif change == 'inode':
        path.rename(path.with_name('old'))
        path.mkdir()
    elif change == 'worktree_copy':
        replacement = path.with_name('replacement')
        shutil.copytree(path, replacement)
        path.rename(path.with_name('old'))
        replacement.rename(path)
    elif change == 'alternates':
        (clone / '.git/objects/info/alternates').write_text(str(world.source / '.git/objects'))
    elif change == 'hardlink':
        os.link(clone / '.git/config', world.managed / 'shared-config')
    else:
        (path / '.git').write_text('gitdir: ' + str(world.source / '.git') + '\n')
    with refused('NATIVE_CARRIER_CHANGED|REPOSITORY_(CARRIER_CHANGED|CLONE_TAMPERED|GIT_FAILED)'):
        await readback(world, receipt)


async def test_lost_creation_reply_does_not_authorize_adoption(world):
    def lose(_):
        raise AmbiguousOutcome('lost reply')
    world.runner.after['fetch'] = lose
    with pytest.raises(AmbiguousOutcome):
        await prepare(world)
    with refused('NATIVE_CARRIER_CREATION_UNPROVEN'):
        await prepare(world)
    assert world.runner.phases == ['fetch', 'fetch']
    assert len(list(world.managed.iterdir())) == 1


async def test_local_runner_binding_is_unavailable(world):
    world.runner.execution_binding = lambda _: {'transport': 'local'}
    with refused('GIT_RUNNER_UNAVAILABLE'):
        await prepare(world)
    assert not world.runner.scripts


async def test_final_locked_check_can_refuse_creation(world):
    async def check():
        raise OperationError('FIXTURE_WITHDRAWN', 'withdrawn', 409)
    with refused('FIXTURE_WITHDRAWN'):
        await carrier.prepare(world.ctx, world.fixed, attempt_id='attempt', managed_root=str(world.managed),
                              artifact_manifest_digest=carrier.EMPTY_MANIFEST, check=check)
    assert not list(world.managed.iterdir())


async def test_unreceipted_worktree_inside_owned_clone_is_refused(world):
    def plant(req):
        path = Path(req['worktree_path'])
        path.parent.mkdir()
        git(req['clone_path'], 'worktree', 'add', '-b', req['branch'], str(path), req['source_sha'])
    world.runner.before['prepare'] = plant
    with refused('NATIVE_CARRIER_CREATION_UNPROVEN'):
        await prepare(world)
    assert len(list(world.managed.glob('*/.bat-worktrees/*/hello'))) == 1


async def test_original_receipt_is_immutable_and_replay_has_no_writes(world):
    receipt = await prepare(world)
    saved = receipt.reference()
    count = len(world.runner.phases)
    changed = receipt.reference()
    changed['source_sha'] = 'f' * 40
    assert receipt.reference() == saved
    replay = await prepare(world)
    assert replay.reference() == saved
    assert world.runner.phases[count:] == ['read.carrier']


@pytest.mark.parametrize('kind', ['directory', 'symlink'])
async def test_unknown_destination_keeps_its_contents(world, kind):
    before = files(world.source)
    def plant(req):
        path = Path(req['clone_path'])
        if kind == 'symlink':
            path.symlink_to(world.source, target_is_directory=True)
        else:
            path.mkdir()
            (path / 'keep').write_text('manual')
    world.runner.before['fetch'] = plant
    with refused('NATIVE_CARRIER_CREATION_UNPROVEN'):
        await prepare(world)
    assert files(world.source) == before
    path = next(world.managed.iterdir())
    assert path.is_symlink() if kind == 'symlink' else (path / 'keep').read_text() == 'manual'


async def test_published_ref_move_is_refused(world):
    world.fixed = replace(world.fixed, ref='refs/heads/absent')
    with refused('REPOSITORY_REF_CHANGED'):
        await prepare(world)
    assert not list(world.managed.glob('*/.bat-worktrees'))


async def test_guard_is_rechecked_after_host_lock(world):
    calls = []
    async def check():
        calls.append('checked')
        if len(calls) == 2:
            raise OperationError('FIXTURE_WITHDRAWN', 'withdrawn', 409)
    with refused('FIXTURE_WITHDRAWN'):
        await carrier.prepare(world.ctx, world.fixed, attempt_id='attempt', managed_root=str(world.managed),
                              artifact_manifest_digest=carrier.EMPTY_MANIFEST, check=check)
    assert len(calls) == 2 and not list(world.managed.iterdir())


async def test_readback_refuses_metadata_covering_another_worktree(world):
    receipt = await prepare(world)
    clone = receipt.reference()['clone']['path']
    git(clone, 'worktree', 'add', '-b', 'foreign', str(world.managed / 'foreign'), world.fixed.sha)
    with refused('NATIVE_CARRIER_CHANGED'):
        await readback(world, receipt)


async def test_source_config_cannot_hide_lazy_fetch_in_an_include(world):
    config = world.managed / 'extra-config'
    config.write_text('[remote "origin"]\n    promisor = true\n')
    git(world.source, 'config', 'include.path', str(config))
    with refused('NATIVE_SOURCE_UNPROVEN'):
        await prepare(world)
