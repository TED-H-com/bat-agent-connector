"""Offline coding isolation policy. Internal callers only; no route registration."""
from __future__ import annotations

import asyncio
import os
import re
import shutil
import stat
import sys
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path

from . import native_carrier
from .native_process import MAX_LINE_BYTES, BoundedNativeProcess, _process_identity, _require, _spawn

MOUNT_UNPROVEN = 'NATIVE_ISOLATION_MOUNT_UNPROVEN'
PATH_UNPROVEN = 'NATIVE_ISOLATION_PATH_UNPROVEN'
QUOTA_UNPROVEN = 'NATIVE_ISOLATION_QUOTA_UNPROVEN'
UNAVAILABLE = 'NATIVE_ISOLATION_UNAVAILABLE'
NAMESPACE_UNAVAILABLE = 'NATIVE_ISOLATION_NAMESPACE_UNAVAILABLE'
READONLY_TARGETS = frozenset({'/runtime', '/toolchain', '/lib/x86_64-linux-gnu',
    '/lib64/ld-linux-x86-64.so.2', '/etc/ssl/certs/ca-certificates.crt'})
NAMESPACES = ('--unshare-user', '--unshare-pid', '--unshare-net', '--unshare-ipc', '--unshare-uts',
              '--disable-userns', '--assert-userns-disabled', '--die-with-parent', '--new-session', '--cap-drop', 'ALL')
SPAWN = {'close_fds': True, 'pass_fds': (), 'start_new_session': True, 'env': {}}


@dataclass(frozen=True)
class Mount:
    source: str
    target: str
    writable: bool = False


@dataclass(frozen=True)
class Areas:
    scratch: Path
    state: Path
    spool: Path
    quota_bytes: int


@dataclass(frozen=True)
class IsolationPlan:
    argv: tuple[str, ...]
    mounts: tuple[Mount, ...]
    cwd: str
    quota_bytes: int
    bindings: tuple = ()

    def manifest(self):
        return {'argv': list(self.argv), 'mounts': [asdict(m) for m in self.mounts],
                'spawn': {**SPAWN, 'pass_fds': []}, 'quota_bytes': self.quota_bytes}


def _overlap(a, b):
    a, b = Path(a), Path(b)
    return a == b or a in b.parents or b in a.parents


def _real(path):
    path = Path(path)
    _require(path.is_absolute() and path.resolve() == path and path.exists(), PATH_UNPROVEN)
    mode = path.stat()
    _require(stat.S_ISDIR(mode.st_mode) or stat.S_ISREG(mode.st_mode) and mode.st_nlink == 1, PATH_UNPROVEN)
    return path


def _mounts():
    for line in Path('/proc/self/mountinfo').read_text().splitlines():
        fields, info = line.split(' - ', 1)
        path = re.sub(r'\\([0-7]{3})', lambda m: chr(int(m[1], 8)), fields.split()[4])
        yield Path(path), info.split()[0]


def _disk_info(path):
    mount, kind = max(((p, k) for p, k in _mounts() if p == path or p in path.parents), key=lambda v: len(v[0].parts))
    usage = os.statvfs(path)
    return kind, usage.f_blocks * usage.f_frsize


def _tree(root):
    root = _real(root)
    _require(not any(root in p.parents for p, _ in _mounts()), PATH_UNPROVEN)
    for parent, dirs, files in os.walk(root, followlinks=False):
        for name in dirs + files:
            path = Path(parent, name)
            item = path.lstat()
            if stat.S_ISLNK(item.st_mode):
                _require(path.resolve().is_relative_to(root), PATH_UNPROVEN)
            else:
                _require(stat.S_ISDIR(item.st_mode) or stat.S_ISREG(item.st_mode) and item.st_nlink == 1, PATH_UNPROVEN)
                _require(item.st_dev == root.stat().st_dev, PATH_UNPROVEN)


async def build_plan(ctx, receipt, *, readonly, areas, protected, command, check, requested_mounts=()):
    """Compile a trusted toolchain allowlist and fresh carrier readback into data.

    Mounts and disk volumes are operator-provisioned inputs, never request data.
    Volume capacity is a kernel-enforced aggregate ceiling, not a polling limit.
    """
    _require(sys.platform == 'linux' and os.uname().machine == 'x86_64', UNAVAILABLE)
    bwrap = shutil.which('bwrap')
    _require(bwrap is not None, UNAVAILABLE)
    await native_carrier.readback(ctx, receipt, check=check)
    saved = receipt.reference()
    for name in ('clone', 'worktree', 'git_dir'):
        physical = saved[name]
        local = _real(physical['path']).stat()
        _require((local.st_dev, local.st_ino) == (physical['device'], physical['inode']), PATH_UNPROVEN)
    carrier = _real(saved['clone']['path'])
    roots = [carrier, *(_real(p) for p in (areas.scratch, areas.state, areas.spool))]
    _require(all(not _overlap(a, b) for i, a in enumerate(roots) for b in roots[i + 1:]), MOUNT_UNPROVEN)
    forbidden = (*protected, areas.spool, Path.home() / '.ssh', Path.home() / '.config',
                 Path.home() / '.codex', Path.home() / '.claude')
    journal = getattr(ctx.service, 'journal', None)
    if journal is not None:
        forbidden += (journal.path,)
    _require(readonly and all(type(m) is Mount and not m.writable and m.target in READONLY_TARGETS for m in readonly), MOUNT_UNPROVEN)
    _require(len({m.target for m in readonly}) == len(readonly), MOUNT_UNPROVEN)
    mounts = (*readonly, Mount(str(carrier), str(carrier), True),
              Mount(str(areas.scratch), '/scratch', True), Mount(str(areas.state), '/state', True))
    _require(all(m in mounts for m in requested_mounts), MOUNT_UNPROVEN)
    for mount in mounts:
        source = _real(mount.source)
        _require(not any(_overlap(source, p) for p in forbidden), MOUNT_UNPROVEN)
        if mount.writable:
            _tree(source)
            _require(not any(_overlap(source, ro.source) for ro in readonly), MOUNT_UNPROVEN)
    kind, capacity = _disk_info(areas.scratch)
    _require(kind in {'ext2', 'ext3', 'ext4', 'xfs', 'btrfs'} and 0 < capacity <= areas.quota_bytes <= 1024**3, QUOTA_UNPROVEN)
    _require(areas.scratch.stat().st_dev == areas.state.stat().st_dev, QUOTA_UNPROVEN)
    _require(all(_disk_info(p)[0] in {'ext2', 'ext3', 'ext4', 'xfs', 'btrfs'} for p in roots), QUOTA_UNPROVEN)
    _require(command and any(Path(command[0]).is_relative_to(m.target) for m in readonly if m.target in {'/runtime', '/toolchain'}), MOUNT_UNPROVEN)
    argv = [bwrap, *NAMESPACES, '--clearenv', '--proc', '/proc']
    for mount in mounts:
        argv.extend(('--bind' if mount.writable else '--ro-bind', mount.source, mount.target))
    for key, value in (('PATH', '/toolchain/bin:/runtime/bin'), ('HOME', '/scratch'), ('TMPDIR', '/scratch'),
                       ('XDG_CONFIG_HOME', '/state'), ('LANG', 'C.UTF-8'), ('PYTHONDONTWRITEBYTECODE', '1')):
        argv.extend(('--setenv', key, value))
    argv.extend(('--remount-ro', '/', '--chdir', saved['cwd'], '--', *command))
    bindings = tuple((m.source, os.stat(m.source).st_dev, os.stat(m.source).st_ino, os.stat(m.source).st_ctime_ns) for m in mounts)
    return IsolationPlan(tuple(argv), mounts, saved['cwd'], areas.quota_bytes, bindings)


async def probe(plan):
    """Only a zero-workload namespace probe runs before coding can be admitted."""
    command = (*plan.argv[:plan.argv.index('--') + 1], '/runtime/bin/python3', '-I', '-c', 'pass')
    try:
        process = await _spawn(*command, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL, **SPAWN)
    except OSError:
        _require(False, UNAVAILABLE)
    try:
        await asyncio.wait_for(process.communicate(), 5)
        _require(process.returncode == 0, NAMESPACE_UNAVAILABLE)
    except asyncio.TimeoutError:
        _require(False, NAMESPACE_UNAVAILABLE)
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()


async def run(ctx, receipt, *, readonly, areas, protected, command, check, consume, timeout, record_lifecycle=None):
    """Bounded internal harness; future adapters supply their protocol consumer."""
    _require(0 < timeout <= 900, UNAVAILABLE)
    args = dict(readonly=readonly, areas=areas, protected=protected, command=command, check=check)
    plan = await build_plan(ctx, receipt, **args)
    await probe(plan)
    # Repeat carrier, mount and quota validation after the probe, before the effect.
    _require(await build_plan(ctx, receipt, **args) == plan, PATH_UNPROVEN)
    runtime = BoundedNativeProcess(receipt.reference()['attempt_id'], record_lifecycle=record_lifecycle)
    runtime.directory = Path(tempfile.mkdtemp(prefix='native-spool-', dir=areas.spool))
    fd = os.open(runtime.directory / 'events.ndjson', os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    runtime._spool = os.fdopen(fd, 'wb')
    try:
        await runtime._receipt({'kind': 'process_launch_attempted', 'run_id': runtime.run_id})
        runtime.process_launch_attempted = True
        try:
            runtime.process = await _spawn(*plan.argv, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                                          stderr=asyncio.subprocess.PIPE, limit=MAX_LINE_BYTES, **SPAWN)
        except BaseException:
            runtime._persisted = False
            raise
        runtime.identity = _process_identity(runtime.process.pid)
        runtime._stderr_task = asyncio.create_task(runtime._drain_stderr())
        await runtime._receipt({'kind': 'process_started', 'run_id': runtime.run_id, 'process': runtime.identity})
        return await asyncio.wait_for(consume(runtime), timeout)
    finally:
        await runtime.close()
