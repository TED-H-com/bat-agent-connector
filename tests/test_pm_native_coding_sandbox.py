"""Offline real-kernel spike. No auth, provider, model, Fleet or product adapter."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from bat_agent_connector import pm_native_coding_sandbox as boundary

try:
    import tomllib
except ImportError:
    import tomli as tomllib


def test_profile_has_no_broad_read_or_ambient_authority():
    config = tomllib.loads(boundary.permission_config())
    policy = config['permissions'][boundary.PROFILE]
    assert 'extends' not in policy
    assert not any(key in policy['filesystem'] for key in ('/', ':minimal', ':workspace'))
    assert policy['filesystem']['/workspace'] == 'write'
    assert policy['filesystem']['/workspace/.git'] == 'read'
    for path in ('/controller', '/foreign', '/home', '/root'):
        assert policy['filesystem'][path] == 'none'
    assert policy['network'] == {'enabled': False}
    assert config['approval_policy'] == 'never' and config['mcp_servers'] == {}
    assert config['shell_environment_policy']['inherit'] == 'none'
    assert config['allow_login_shell'] is False and config['features']['use_legacy_landlock'] is False
    assert boundary.eligibility()['supported'] is False


def test_child_environment_does_not_inherit_credentials_or_change_parent(monkeypatch):
    monkeypatch.setenv('OPENAI_API_KEY', 'synthetic-api-canary')
    monkeypatch.setenv('BASH_ENV', '/foreign/inject.sh')
    before = dict(os.environ)
    value = boundary.environment('/home/probe', '/home/probe/.codex')
    assert 'OPENAI_API_KEY' not in value and 'BASH_ENV' not in value
    assert dict(os.environ) == before


def _git(directory, *args):
    return subprocess.run(['git', '-C', str(directory), *args], check=True, capture_output=True).stdout


def _runtime_files(python_root, shell):
    """Explicit ELF closure of the trusted fixture runtime, never a host root bind."""
    system_bwrap = Path(shutil.which('bwrap')).resolve()
    programs = [python_root / 'bin/python3', shell, system_bwrap]
    for pattern in ('_socket*', '_ctypes*', 'select*', 'math*', '_posixsubprocess*'):
        programs.extend(python_root.glob(f'lib/python*/lib-dynload/{pattern}.so'))
    libraries = set()
    for program in programs:
        text = subprocess.run(['ldd', str(program)], check=True, capture_output=True, text=True).stdout
        for name in re.findall(r'(/[^\s()]+)', text):
            path = Path(name)
            if path.exists() and not path.is_relative_to(python_root):
                libraries.add(path)
    return (boundary.Mount(python_root, '/runtime/python'), boundary.Mount(shell, '/runtime/bin/sh'),
            boundary.Mount(system_bwrap, '/usr/bin/bwrap'),
            *(boundary.Mount(path, str(path)) for path in sorted(libraries)))


@pytest.fixture
def kernel_probe(tmp_path):
    if sys.platform != 'linux' or not shutil.which('bwrap') or not shutil.which('codex'):
        pytest.skip('Optional Linux, bubblewrap and pinned Codex installation are required')
    cli = Path(shutil.which('codex')).resolve()
    if cli.suffix == '.js':
        package = cli.parent.parent
        candidates = list(package.glob('node_modules/@openai/codex-linux-*/vendor/*/bin/codex'))
        if len(candidates) != 1:
            pytest.skip('Explicit native Codex executable is unavailable')
        cli = candidates[0]
    bundled = cli.parent.parent / 'codex-resources/bwrap'
    if not bundled.is_file():
        pytest.skip('Pinned distribution does not expose its bundled bwrap')
    paths = {name: tmp_path / name for name in ('workspace', 'controller', 'scratch', 'empty', 'foreign')}
    for path in paths.values():
        path.mkdir()
    workspace = paths['workspace']
    _git(workspace, 'init', '-q')
    _git(workspace, 'config', 'user.name', 'offline fixture')
    _git(workspace, 'config', 'user.email', 'fixture@example.invalid')
    (workspace / 'answer.py').write_text('answer = 2\n')
    _git(workspace, 'add', 'answer.py')
    _git(workspace, 'commit', '-qm', 'fixed synthetic source')
    # This in-project override must never join the synthetic CLI configuration.
    (workspace / '.codex').mkdir()
    (workspace / '.codex/config.toml').write_text('[mcp_servers.inherited]\ncommand="/foreign/mcp.sh"\n')
    (workspace / '.agents').mkdir()
    (paths['foreign'] / 'private.txt').write_text('FOREIGN_CHECKOUT_CANARY')
    (paths['foreign'] / 'mcp.sh').write_text('echo inherited > /workspace/mcp-was-run\n')
    (paths['controller'] / 'auth-canary.json').write_text('SYNTHETIC_AUTH_CANARY')
    (paths['controller'] / 'config.toml').write_text(boundary.permission_config())
    home = str(Path.home())
    native_home = os.environ.get('CODEX_HOME', home + '/.codex')
    kwargs = {'bwrap': Path(shutil.which('bwrap')).resolve(), 'codex': cli, 'bundled_bwrap': bundled,
              **paths, 'toolchain': _runtime_files(Path(sys.base_prefix), Path('/bin/sh').resolve()),
              'home': home, 'native_home': native_home}
    return kwargs, boundary.environment(home, native_home)


def _program(source):
    return ('/runtime/python/bin/python3', '-I', '-c', source)


CANARIES = r'''
import ctypes, errno, json, os, pathlib, socket, stat, subprocess
P = pathlib.Path
checks = {}
def denied(name, action, expected_errno=None):
    try:
        action()
    except OSError as error:
        checks[name] = expected_errno is None or error.errno == expected_errno
    else:
        checks[name] = False
checks['cwd'] = str(P.cwd()) == '/workspace'
checks['original_source'] = P('answer.py').read_text() == 'answer = 2\n'
P('answer.py').write_text('answer = 3\n')
checks['coding_edit'] = P('answer.py').read_text() == 'answer = 3\n'
checks['subprocess'] = subprocess.check_output(['/runtime/bin/sh', '-c', 'printf controlled']).decode() == 'controlled'
P('/tmp/owned-scratch').write_text('bounded scratch')
checks['scratch'] = P('/tmp/owned-scratch').read_text() == 'bounded scratch'
for label, path in [('auth', '/controller/auth-canary.json'), ('foreign', '/foreign/private.txt'),
                    ('config', '/workspace/.codex/config.toml')]:
    denied(label + '_read', lambda p=path: P(p).read_bytes())
    denied(label + '_write', lambda p=path: P(p).write_text('changed'))
denied('git_metadata_write', lambda: P('/workspace/.git/config').write_text('changed'))
denied('toolchain_write', lambda: P('/runtime/bin/sh').write_bytes(b'changed'))
denied('proc_root_auth', lambda: P('/proc/1/root/controller/auth-canary.json').read_bytes())
denied('proc_root_foreign', lambda: P('/proc/1/root/foreign/private.txt').read_bytes())
denied('proc_root_write', lambda: P('/proc/1/root/controller/auth-canary.json').write_text('changed'))
P('/workspace/escape').symlink_to('/controller/auth-canary.json')
denied('symlink_escape_read', lambda: P('/workspace/escape').read_bytes())
denied('symlink_escape_write', lambda: P('/workspace/escape').write_text('changed'))
checks['ambient_env'] = not any(k in os.environ for k in ('OPENAI_API_KEY','ANTHROPIC_API_KEY','BASH_ENV','ENV','LD_PRELOAD','CODEX_HOME','HOME'))
checks['no_mcp_effect'] = not P('/workspace/mcp-was-run').exists()
denied('inet_socket', lambda: socket.socket(socket.AF_INET, socket.SOCK_STREAM), errno.EPERM)
denied('inet6_socket', lambda: socket.socket(socket.AF_INET6, socket.SOCK_STREAM), errno.EPERM)
denied('unix_connect', lambda: socket.socket(socket.AF_UNIX).connect('/tmp/absent.sock'), errno.EPERM)
libc = ctypes.CDLL(None, use_errno=True)
checks['ptrace_denied'] = libc.ptrace(0, 0, 0, 0) == -1 and ctypes.get_errno() == errno.EPERM
fds = list(P('/proc/self/fd').iterdir())
checks['no_auth_fd'] = all('auth-canary' not in os.readlink(p) for p in fds if p.exists())
regular = [int(p.name) for p in fds if p.exists() and stat.S_ISREG(p.stat().st_mode)]
checks['no_canary_fd_bytes'] = all(b'SYNTHETIC_AUTH_CANARY' not in os.pread(fd, 4096, 0) and
                                 b'FOREIGN_CHECKOUT_CANARY' not in os.pread(fd, 4096, 0) for fd in regular)
print(json.dumps(checks, sort_keys=True))
assert all(checks.values()), checks
'''


async def _version(kwargs, env):
    args = boundary.boundary_argv(**kwargs, command=_program('pass'))
    cut = args.index('/runtime/bin/codex', args.index('--') + 1)
    result = await boundary.offline_probe([*args[:cut + 1], '--version'], env)
    assert result['process_death_confirmed'], result
    if result['returncode'] != 0 and ('Operation not permitted' in result['stderr'] or 'Permission denied' in result['stderr']):
        pytest.skip('Host kernel rejects the isolated namespace; boundary remains unsupported')
    assert result['returncode'] == 0, result
    if result['stdout'].strip() != f'codex-cli {boundary.VERSION}':
        pytest.skip('Installed Codex is outside the fixed source contract')


async def test_actual_codex_restricted_profile_can_edit_only_owned_worktree(kernel_probe, monkeypatch):
    kwargs, env = kernel_probe
    await _version(kwargs, env)
    monkeypatch.setenv('OPENAI_API_KEY', 'synthetic-api-canary')
    monkeypatch.setenv('BASH_ENV', str(kwargs['foreign'] / 'mcp.sh'))
    old_config = (kwargs['workspace'] / '.git/config').read_bytes()
    # A caller-owned inheritable descriptor still must not reach Codex/tools.
    with (kwargs['controller'] / 'auth-canary.json').open('rb') as auth:
        os.set_inheritable(auth.fileno(), True)
        result = await boundary.offline_probe(boundary.boundary_argv(**kwargs, command=_program(CANARIES)), env)
    if result['reason'] == 'namespace_unavailable':
        assert result['process_death_confirmed'] and not result['supported'], result
        assert (kwargs['workspace'] / 'answer.py').read_text() == 'answer = 2\n'
        pytest.skip('Pinned CLI started, but its nested tool namespace was refused; no coding boundary verified')
    assert result['reason'] is None and result['returncode'] == 0, (result['reason'], result['stderr'], result['stdout'])
    assert result['process_death_confirmed'] and result['supported'] is False
    checks = json.loads(result['stdout'])
    assert len(checks) >= 22 and all(checks.values()), checks
    assert (kwargs['workspace'] / 'answer.py').read_text() == 'answer = 3\n'
    assert (kwargs['workspace'] / '.git/config').read_bytes() == old_config
    assert (kwargs['controller'] / 'auth-canary.json').read_text() == 'SYNTHETIC_AUTH_CANARY'
    assert (kwargs['foreign'] / 'private.txt').read_text() == 'FOREIGN_CHECKOUT_CANARY'
    assert not (kwargs['workspace'] / 'mcp-was-run').exists()


def _outer_command(kwargs, command):
    # Runner bounds are independently testable when the nested Codex sandbox is
    # unsupported. This helper never represents named-profile verification.
    args = boundary.boundary_argv(**kwargs, command=command)
    return [*args[:args.index('--') + 1], *command]


async def test_actual_outer_probe_timeout_kills_its_original_process(kernel_probe):
    kwargs, env = kernel_probe
    await _version(kwargs, env)
    result = await boundary.offline_probe(_outer_command(kwargs,
        _program('import time; time.sleep(30)')), env, timeout=1)
    assert result['reason'] == 'timeout', result['stderr']
    assert result['process_death_confirmed'] and not result['product_execution_authorized']


async def test_actual_outer_probe_bounds_output(kernel_probe):
    kwargs, env = kernel_probe
    await _version(kwargs, env)
    result = await boundary.offline_probe(_outer_command(kwargs,
        _program('print("x" * 100000)')), env)
    assert result['reason'] == 'output_limit', result['stderr']
    assert result['process_death_confirmed'] and len(result['stdout']) <= boundary.MAX_BYTES


def test_builder_rejects_broad_roots_and_aliased_owned_directories(kernel_probe):
    kwargs, _ = kernel_probe
    with pytest.raises(ValueError, match='narrow'):
        boundary.boundary_argv(**{**kwargs, 'toolchain': (boundary.Mount(Path('/'), '/runtime/all'),)}, command=_program('pass'))
    with pytest.raises(ValueError, match='disjoint'):
        boundary.boundary_argv(**{**kwargs, 'scratch': kwargs['workspace']}, command=_program('pass'))


async def test_timeout_budget_rejects_unbounded_values():
    for timeout in (0, -1, 31):
        with pytest.raises(ValueError, match='timeout'):
            await boundary.offline_probe([], {}, timeout=timeout)
