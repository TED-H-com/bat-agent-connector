"""Coding policy and mandatory-capable OS canary; no model or credentials."""
import asyncio
import errno
import hashlib
import json
import os
import runpy
import shutil
import socket
import sys
import zlib
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from bat_agent_connector import native_isolation as isolation
from bat_agent_connector import native_process
from bat_agent_connector.task_journal import Journal
from tests.test_native_carrier import prepare
from tests.test_native_carrier import world as carrier_world

world = carrier_world

FIXTURES = Path(__file__).parent / 'fixtures'
LIMIT = 64 * 1024 * 1024


def runtime_mounts():
    return (
        isolation.Mount(str(Path(sys.base_prefix).resolve()), '/runtime'),
        isolation.Mount(str(Path('/lib/x86_64-linux-gnu').resolve()), '/lib/x86_64-linux-gnu'),
        isolation.Mount(str(Path('/lib64/ld-linux-x86-64.so.2').resolve()), '/lib64/ld-linux-x86-64.so.2'),
        isolation.Mount('/etc/ssl/certs/ca-certificates.crt', '/etc/ssl/certs/ca-certificates.crt'),
    )


async def inputs(world, tmp_path, area_root=None):
    root = area_root or tmp_path
    if area_root is not None:
        world = SimpleNamespace(**{**vars(world), 'managed': root / 'managed'})
        world.managed.mkdir()
    receipt = await prepare(world)
    scratch, state, spool = root / 'scratch', root / 'state', tmp_path / 'spool'
    for path in (scratch, state, spool):
        path.mkdir(mode=0o700)
    forbidden = [tmp_path / name for name in ('sibling', 'manual', 'credentials', 'database')]
    for path in forbidden:
        path.mkdir()
        (path / 'secret').write_bytes(b'private fixture bytes')
    (spool / 'secret').write_bytes(b'private spool bytes')
    async def check():
        pass
    return dict(ctx=world.ctx, receipt=receipt, readonly=runtime_mounts(),
                areas=isolation.Areas(scratch, state, spool, LIMIT), protected=tuple(forbidden),
                command=('/runtime/bin/python3', '-I', '-c', 'pass'), check=check)


@pytest.fixture
async def case(world, tmp_path, monkeypatch):
    args = await inputs(world, tmp_path)
    # Policy tests supply availability/capacity observations; the canary probes the OS.
    which = shutil.which
    monkeypatch.setattr(isolation.shutil, 'which', lambda name: '/usr/bin/bwrap' if name == 'bwrap' else which(name))
    monkeypatch.setattr(isolation, '_disk_info', lambda _: ('ext4', LIMIT))
    monkeypatch.setattr(isolation, '_device', lambda _: 7, raising=False)
    journal = Journal(tmp_path / 'journal.sqlite')
    args['ctx'].service.journal = journal
    try:
        yield args
    finally:
        journal.close()


def canonical(plan, case):
    value = plan.manifest()
    replacements = [(str(case['areas'].scratch.parent), '<case>'),
                    (case['receipt'].reference()['clone']['path'], '<carrier>')]
    def path(value):
        for before, after in sorted(replacements, key=lambda p: -len(p[0])):
            if value == before or value.startswith(before + '/'):
                return after + value[len(before):]
        return value
    runtime = next(m.source for m in plan.mounts if m.target == '/runtime')
    for i in range(len(value['argv']) - 2):
        if value['argv'][i:i + 3] == ['--ro-bind', runtime, '/runtime']:
            value['argv'][i + 1] = '<runtime>'
    value['argv'] = [path(v) for v in value['argv']]
    value['mounts'] = [{**m, 'source': '<runtime>' if m['target'] == '/runtime' else path(m['source']),
                       'target': path(m['target'])} for m in value['mounts']]
    value['writable_storage'] = [{**s, 'target': path(s['target'])} for s in value['writable_storage']]
    return value


@pytest.mark.parametrize('runtime', ['/usr', '/opt/hostedtoolcache/Python/3.12/x64', '/home/runner/.local/share/uv/python'])
async def test_plan_matches_reviewable_golden_and_fixed_rules(case, runtime):
    plan = await isolation.build_plan(**case)
    original = next(m.source for m in plan.mounts if m.target == '/runtime')
    plan = replace(plan, argv=tuple(runtime if v == original else v for v in plan.argv),
                   mounts=tuple(replace(m, source=runtime) if m.target == '/runtime' else m for m in plan.mounts))
    expected = json.loads((FIXTURES / 'native-isolation-plan.json').read_text())
    assert canonical(plan, case) == expected
    assert plan.manifest()['spawn'] == {'close_fds': True, 'pass_fds': [], 'start_new_session': True, 'env': {}}
    assert '--unshare-pid' in plan.argv and ('--proc', '/proc') == plan.argv[plan.argv.index('--proc'):][:2]
    assert '--unshare-net' in plan.argv and '--disable-userns' in plan.argv
    assert not any(m.target == '/' for m in plan.mounts)
    assert {m.source for m in plan.mounts if m.writable} == {
        case['receipt'].reference()['clone']['path'], str(case['areas'].scratch), str(case['areas'].state)}


@pytest.mark.parametrize('kind', ['outside', 'spool', 'credential', 'symlink'])
async def test_unlisted_mounts_and_aliases_refused_before_spawn(case, monkeypatch, kind):
    spawned = []
    monkeypatch.setattr(isolation, '_spawn', lambda *a, **k: spawned.append(a))
    path = case['protected'][0]
    if kind == 'spool':
        path = case['areas'].spool
    elif kind == 'credential':
        path = case['protected'][2]
    elif kind == 'symlink':
        path = case['areas'].scratch.parent / 'alias'
        path.symlink_to(Path(sys.base_prefix), target_is_directory=True)
    mount = isolation.Mount(str(path), '/toolchain', writable=kind == 'outside')
    with pytest.raises(native_process.NativeRuntimeError, match='NATIVE_ISOLATION_(MOUNT|PATH)_UNPROVEN'):
        await isolation.build_plan(**{**case, 'readonly': (*case['readonly'], mount)})
    assert spawned == []


@pytest.mark.parametrize('kind', ['hardlink', 'symlink', 'socket'])
async def test_carrier_aliases_and_sockets_refused(case, kind):
    path = Path(case['receipt'].reference()['clone']['path']) / 'escape'
    sock = None
    try:
        if kind == 'hardlink':
            os.link(case['protected'][0] / 'secret', path)
        elif kind == 'symlink':
            path.symlink_to(case['protected'][0] / 'secret')
        else:
            sock = socket.socket(socket.AF_UNIX)
            fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                sock.bind(f'/proc/self/fd/{fd}/escape')
            finally:
                os.close(fd)
        with pytest.raises(native_process.NativeRuntimeError, match='NATIVE_ISOLATION_PATH_UNPROVEN'):
            await isolation.build_plan(**case)
    finally:
        if sock:
            sock.close()


async def test_carrier_readback_failure_never_spawns(case, monkeypatch):
    from bat_agent_connector.operations import NeedsAttention
    async def changed(*args, **kwargs):
        raise NeedsAttention('NATIVE_CARRIER_CHANGED', 'fixture')
    monkeypatch.setattr(isolation.native_carrier, 'readback', changed)
    with pytest.raises(NeedsAttention, match='fixture'):
        await isolation.build_plan(**case)


@pytest.mark.parametrize('disk', [('tmpfs', LIMIT), ('ext4', LIMIT + 1)])
async def test_disk_backing_and_aggregate_capacity_are_required(case, monkeypatch, disk):
    monkeypatch.setattr(isolation, '_disk_info', lambda _: disk)
    with pytest.raises(native_process.NativeRuntimeError, match='NATIVE_ISOLATION_QUOTA_UNPROVEN'):
        await isolation.build_plan(**case)


@pytest.mark.parametrize('other', ['carrier', 'worktree', 'git_dir', 'state'])
async def test_every_carrier_identity_shares_the_bounded_device(case, monkeypatch, other):
    saved = case['receipt'].reference()
    paths = {Path(saved[name]['path']) for name in ('clone', 'worktree', 'git_dir')}
    if other != 'carrier':
        paths = {case['areas'].state if other == 'state' else Path(saved[other]['path'])}
    monkeypatch.setattr(isolation, '_device', lambda p: 8 if Path(p) in paths else 7)
    with pytest.raises(native_process.NativeRuntimeError, match=isolation.QUOTA_UNPROVEN):
        await isolation.build_plan(**case)


@pytest.mark.parametrize('where', ['clone', 'worktree', 'git_dir', 'scratch', 'state'])
async def test_capacity_unavailable_never_means_unbounded(case, monkeypatch, where):
    saved = case['receipt'].reference()
    missing = Path(saved[where]['path']) if where in saved else getattr(case['areas'], where)
    def unavailable(path):
        if Path(path) == missing:
            raise OSError('capacity observation unavailable')
        return 'ext4', LIMIT
    monkeypatch.setattr(isolation, '_disk_info', unavailable)
    with pytest.raises(native_process.NativeRuntimeError, match=isolation.QUOTA_UNPROVEN):
        await isolation.build_plan(**case)


async def test_nested_carrier_mount_is_refused_even_on_same_device(case, monkeypatch):
    nested = Path(case['receipt'].reference()['clone']['path'], 'nested')
    nested.mkdir()
    observed = list(isolation._mounts())
    monkeypatch.setattr(isolation, '_mounts', lambda: iter([*observed, (nested, 'ext4')]))
    with pytest.raises(native_process.NativeRuntimeError, match=isolation.PATH_UNPROVEN):
        await isolation.build_plan(**case)


def test_disk_capacity_counts_total_blocks_not_available_space(tmp_path, monkeypatch):
    usage = SimpleNamespace(f_blocks=32768, f_frsize=4096, f_bfree=32, f_bavail=16)
    monkeypatch.setattr(isolation, 'os', SimpleNamespace(statvfs=lambda _: usage))
    monkeypatch.setattr(isolation, '_mounts', lambda: iter([(tmp_path, 'ext4')]))
    assert isolation._disk_info(tmp_path) == ('ext4', 134217728)


async def test_manifest_records_each_writable_mount_capacity_once(case):
    plan = await isolation.build_plan(**case)
    expected = [{'target': m.target, 'device': 7, 'capacity_bytes': LIMIT} for m in plan.mounts if m.writable]
    assert plan.manifest()['writable_storage'] == expected


def test_canary_fill_target_is_the_carrier_worktree(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    quota = runpy.run_path(str(FIXTURES / 'native-isolation-canary.py'))
    assert quota['carrier_fill_target']() == tmp_path / 'carrier-fill'


async def test_carrier_is_prepared_directly_under_the_provisioned_volume(world, tmp_path):
    volume = tmp_path / 'volume'
    volume.mkdir()
    args = await inputs(world, tmp_path, volume)
    saved = args['receipt'].reference()
    assert Path(saved['plan']['managed_root']) == volume / 'managed'
    assert all(Path(saved[name]['path']).is_relative_to(volume) for name in ('clone', 'worktree', 'git_dir'))
    assert Path(saved['cwd'], 'hello').read_text() == 'committed\n'


async def test_missing_bwrap_is_explicit_before_spawn(case, monkeypatch):
    monkeypatch.setattr(isolation.shutil, 'which', lambda _: None)
    with pytest.raises(native_process.NativeRuntimeError, match='NATIVE_ISOLATION_UNAVAILABLE'):
        await isolation.build_plan(**case)


async def test_owned_process_uses_private_spool_and_shared_lifecycle(case, monkeypatch):
    from tests.test_native_process import Process
    process, calls = Process(), []
    async def spawn(*argv, **kwargs):
        calls.append((argv, kwargs))
        return process
    async def probe(_):
        pass
    monkeypatch.setattr(isolation, '_spawn', spawn)
    monkeypatch.setattr(isolation, 'probe', probe)
    monkeypatch.setattr(isolation, '_process_identity', lambda _: {'pid': process.pid, 'start_ticks': 25, 'boot_id_digest': 'a' * 64})
    process.stderr.feed_eof()
    process.stdout.feed_data(b'{"ok":true}\n')
    lifecycle = []
    async def consume(runtime):
        assert type(runtime) is native_process.BoundedNativeProcess
        assert runtime.directory.is_relative_to(case['areas'].spool)
        assert os.stat(runtime._spool.name).st_mode & 0o777 == 0o600
        return await runtime._read_line()
    assert await isolation.run(**case, consume=consume, timeout=5, record_lifecycle=lifecycle.append) == b'{"ok":true}\n'
    assert calls[0][1]['close_fds'] is True and calls[0][1]['pass_fds'] == ()
    assert calls[0][1]['env'] == {} and calls[0][1]['start_new_session'] is True
    assert lifecycle[-1]['process_death_confirmed'] is True


async def test_namespace_probe_refuses_without_running_payload(case, monkeypatch):
    calls = []
    async def spawn(*argv, **kwargs):
        calls.append(argv)
        async def communicate():
            return b'', b'namespace denied'
        return SimpleNamespace(communicate=communicate, returncode=1)
    monkeypatch.setattr(isolation, '_spawn', spawn)
    with pytest.raises(native_process.NativeRuntimeError, match='NATIVE_ISOLATION_NAMESPACE_UNAVAILABLE'):
        await isolation.run(**case, consume=None, timeout=5)
    assert len(calls) == 1 and calls[0][-1] == 'pass'


def test_pm_policy_still_uses_original_readonly_root():
    from bat_agent_connector import pm_native_runtime
    source = Path(pm_native_runtime.__file__).read_text()
    assert 'command = [bwrap, "--ro-bind", "/", "/"' in source
    assert pm_native_runtime.NativePmRuntime.__bases__ == (native_process.BoundedNativeProcess,)


async def test_real_bwrap_canary(world, tmp_path):
    required = os.environ.get('BAC_REQUIRE_BWRAP') == '1'
    bwrap = shutil.which('bwrap')
    if not bwrap:
        if required:
            pytest.fail('Required bubblewrap is missing')
        pytest.skip('bubblewrap is unavailable')
    # Read-only prerequisite probe: no test payload or writable mounts yet.
    argv = [bwrap, *isolation.NAMESPACES, '--clearenv', '--proc', '/proc']
    for mount in runtime_mounts():
        argv.extend(('--ro-bind', mount.source, mount.target))
    argv.extend(('--remount-ro', '/', '--', '/runtime/bin/python3', '-I', '-c', 'pass'))
    try:
        await isolation.probe(isolation.IsolationPlan(tuple(argv), runtime_mounts(), '/', 0))
    except native_process.NativeRuntimeError as exc:
        if required:
            pytest.fail(f'Required OS isolation unavailable: {exc.code}')
        pytest.skip(f'OS isolation unavailable: {exc.code}')
    quota = os.environ.get('BAC_ISOLATION_QUOTA_ROOT')
    if not quota:
        if required:
            pytest.fail('BAC_ISOLATION_QUOTA_ROOT must name a bounded disk filesystem')
        pytest.skip('Real canary needs a bounded disk filesystem; see native-isolation.md')
    import tempfile
    with tempfile.TemporaryDirectory(prefix='canary-', dir=quota) as owned:
        case = await inputs(world, tmp_path, Path(owned))
        control_paths(case)[-1].write_text('')
        program = (FIXTURES / 'native-isolation-canary.py').read_text()
        forbidden = [str(p / 'secret') for p in (*case['protected'], case['areas'].spool)]
        assert Path(forbidden[0]).stat().st_dev != Path(owned).stat().st_dev
        sentinel_before = Path(forbidden[0]).read_bytes()
        before = {p: hashlib.sha256(Path(p).read_bytes()).hexdigest() for p in forbidden}
        fd = os.open(forbidden[0], os.O_RDWR)
        os.set_inheritable(fd, True)
        listener = socket.socket()
        listener.bind(('127.0.0.1', 0))
        listener.listen()
        saved = case['receipt'].reference()
        capacity = isolation._disk_info(Path(saved['cwd']))[1]
        common, gitdir = Path(saved['clone']['path'], '.git'), Path(saved['git_dir']['path'])
        blob = b'blob 6\0canary'
        oid = hashlib.sha1(blob).hexdigest()
        writable = {str(common / 'objects' / oid[:2] / oid[2:]): zlib.compress(blob).hex(),
                    str(common / 'refs/heads/canary'): (saved['source_sha'] + '\n').encode().hex(),
                    str(gitdir / 'index'): (gitdir / 'index').read_bytes().hex()}
        case['command'] = ('/runtime/bin/python3', '-I', '-c', program, json.dumps(forbidden),
                           str(fd), str(os.getpid()), str(listener.getsockname()[1]),
                           json.dumps([str(p) for p in control_paths(case)]), json.dumps(writable), str(capacity))
        try:
            try:
                plan = await isolation.build_plan(**case)
                await isolation.probe(plan)
            except native_process.NativeRuntimeError as exc:
                if required:
                    pytest.fail(f'Required OS isolation unavailable: {exc.code}')
                pytest.skip(f'OS isolation unavailable: {exc.code}')
            # A pre-existing hardlink cannot be hidden by a mount namespace: deny it before spawn.
            link = Path(case['receipt'].reference()['clone']['path']) / 'hardlink'
            hardlink_source = Path(owned, 'hardlink-source')
            hardlink_source.write_bytes(b'private volume bytes')
            os.link(hardlink_source, link)
            with pytest.raises(native_process.NativeRuntimeError, match='NATIVE_ISOLATION_PATH_UNPROVEN'):
                await isolation.build_plan(**case)
            link.unlink()
            async def consume(runtime):
                line = await runtime._read_line()
                await runtime.process.wait()
                assert runtime.process.returncode == 0
                return json.loads(line)
            result = await isolation.run(**case, consume=consume, timeout=15)
            quota_result = result.pop('carrier_quota')
            assert quota_result['target'] == str(Path(saved['cwd'], 'carrier-fill'))
            assert quota_result['errno'] == errno.ENOSPC, quota_result
            assert 0 < quota_result['written_bytes'] <= capacity <= LIMIT, quota_result
            assert quota_result['file_bytes'] == quota_result['written_bytes'], quota_result
            assert quota_result['capacity_bytes'] == capacity, quota_result
            assert quota_result['device'] == saved['worktree']['device'], quota_result
            assert not Path(quota_result['target']).exists()
            print('carrier quota:', json.dumps(quota_result, sort_keys=True))
            assert result and all(value is False for value in result.values()), result
            assert Path(case['receipt'].reference()['cwd'], 'canary-edit').read_text() == 'owned'
            assert {p: hashlib.sha256(Path(p).read_bytes()).hexdigest() for p in forbidden} == before
            assert Path(forbidden[0]).read_bytes() == sentinel_before
            Path(saved['cwd'], 'canary-edit').unlink()
            await isolation.native_carrier.readback(world.ctx, case['receipt'], check=case['check'])
        finally:
            os.close(fd)
            listener.close()


async def test_requested_mount_must_match_the_fixed_allowlist(case):
    requested = isolation.Mount('/usr/share', '/toolchain')
    with pytest.raises(native_process.NativeRuntimeError, match='NATIVE_ISOLATION_MOUNT_UNPROVEN'):
        await isolation.build_plan(**case, requested_mounts=(requested,))


async def test_directory_replacement_after_probe_refuses_payload(case, monkeypatch):
    calls = []
    async def changed(_):
        path = case['areas'].scratch
        path.rename(path.with_name('original-scratch'))
        path.mkdir()
    monkeypatch.setattr(isolation, 'probe', changed)
    monkeypatch.setattr(isolation, '_spawn', lambda *a, **k: calls.append(a))
    with pytest.raises(native_process.NativeRuntimeError, match='NATIVE_ISOLATION_PATH_UNPROVEN'):
        await isolation.run(**case, consume=None, timeout=5)
    assert calls == []


async def test_remote_readback_cannot_substitute_for_local_physical_identity(case, monkeypatch):
    async def remote_readback(*args, **kwargs):
        return case['receipt']
    monkeypatch.setattr(isolation.native_carrier, 'readback', remote_readback)
    clone = Path(case['receipt'].reference()['clone']['path'])
    clone.rename(clone.with_name('original-clone'))
    clone.mkdir()
    with pytest.raises(native_process.NativeRuntimeError, match='NATIVE_ISOLATION_PATH_UNPROVEN'):
        await isolation.build_plan(**case)


async def test_consumer_deadline_still_closes_the_owned_process(case, monkeypatch):
    from tests.test_native_process import Process
    process = Process()
    process.stderr.feed_eof()
    async def spawn(*args, **kwargs):
        return process
    async def probe(_):
        pass
    async def consume(_):
        await asyncio.Future()
    monkeypatch.setattr(isolation, '_spawn', spawn)
    monkeypatch.setattr(isolation, 'probe', probe)
    monkeypatch.setattr(isolation, '_process_identity', lambda _: {'pid': process.pid})
    with pytest.raises(asyncio.TimeoutError):
        await isolation.run(**case, consume=consume, timeout=0.01)
    assert process.closed == 1 and process.returncode == 0


def control_paths(case):
    saved = case['receipt'].reference()
    return [Path(saved['clone']['path'], '.git', p) for p in ('config', 'hooks', 'info', 'objects/info')] + [
        Path(saved['cwd'], '.git'), *(Path(saved['git_dir']['path'], p) for p in ('gitdir', 'commondir', 'config.worktree'))]


async def test_control_overlays_follow_carrier_and_are_required(case, monkeypatch):
    paths = control_paths(case)
    paths[-1].write_text('')
    plan = await isolation.build_plan(**case)
    for path in paths:
        assert path.exists()
        assert ('--ro-bind', str(path), str(path)) in tuple(zip(plan.argv, plan.argv[1:], plan.argv[2:]))
        assert plan.argv.index(str(path)) > plan.argv.index('--bind')
    readback = isolation.native_carrier.readback
    for path in paths[:-1]:
        backup = path.with_name(path.name + '.saved')
        async def removed_after_readback(*args, **kwargs):
            await readback(*args, **kwargs)
            path.rename(backup)
        monkeypatch.setattr(isolation.native_carrier, 'readback', removed_after_readback)
        try:
            with pytest.raises(native_process.NativeRuntimeError, match='NATIVE_ISOLATION_PATH_UNPROVEN'):
                await isolation.build_plan(**case)
        finally:
            backup.rename(path)
