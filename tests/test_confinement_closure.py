"""A10 / §06, §12: the shell gate must prove the entire closure before Python executes."""
from __future__ import annotations

import json
import os
import re
import shlex
import subprocess

import pytest

from bat_agent_connector import checkpoints, confinement, orchestrate
from tests.test_confinement import ACCOUNT, MANAGED, AccountRunner, account_observation


def closure_fixture(tmp_path, *, bad=None, failure=None):
    """Real shell/find over disposable files; virtual root ownership and trusted ancestors only."""
    layout = tmp_path / 'layout'
    tree = layout / 'usr/lib/python3.10'
    binary = layout / 'usr/bin/python3.10'
    binary.parent.mkdir(parents=True)
    tree.mkdir(parents=True)
    for name in ('encodings/__init__.py', 'json/__init__.py', '__pycache__/json.cpython-310.pyc',
                 'lib-dynload/_json.so', '_sysconfigdata_fixture.py'):
        path = tree / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('fixture')
        path.chmod(0o644)
    marker = tmp_path / 'interpreter-ran'
    verdict = json.dumps(account_observation('verified', 'read_only_account_check'))
    binary.write_text('#!/bin/sh\n/usr/bin/printf started > ' + shlex.quote(str(marker))
                      + "\n/usr/bin/printf '%s\\n' " + shlex.quote(verdict) + '\n')
    binary.chmod(0o555)
    (binary.parent / 'python3').symlink_to(binary.name)
    for directory in [layout, *[p for p in layout.rglob('*') if p.is_dir()]]:
        directory.chmod(0o755)
    if bad:
        (tree / bad).chmod(0o664)
    # This executable is a fixture scanner, never part of the production trust base.
    scanner = tmp_path / 'fixture-find'
    failure_word = shlex.quote(failure) if isinstance(failure, str) else "''"
    # Quoted "$layout" compares a literal prefix, never a glob.
    scanner.write_text('\n'.join((
        '#!/bin/sh',
        'target=$2',
        'layout=' + shlex.quote(str(layout)),
        'failure=' + failure_word,
        'uid=' + shlex.quote(str(os.getuid())),
        'has_maxdepth=0',
        'for arg in "$@"; do',
        '    if [ "$arg" = -maxdepth ]; then has_maxdepth=1; break; fi',
        'done',
        'if [ "$has_maxdepth" = 1 ] && [ "${target#"$layout"}" = "$target" ]; then exit 0; fi',
        "if [ \"$failure\" = link_owner ] && [ \"${target%'/intermediate'}\" != \"$target\" ]; then",
        "    printf '%s\\n' X; exit 0",
        'fi',
        'if [ "$has_maxdepth" = 0 ] && [ "$failure" = unreadable ]; then',
        "    printf '%s\\n' 'find: fixture unreadable entry' >&2; exit 1",
        'fi',
        'prev=',
        'reset=1',
        'for arg in "$@"; do',
        '    if [ "$reset" = 1 ]; then set --; reset=0; fi',
        '    if [ "$arg" = 0 ] && [ "$prev" = -uid ]; then set -- "$@" "$uid"; else set -- "$@" "$arg"; fi',
        '    prev=$arg',
        'done',
        'exec /usr/bin/find "$@"',
        '',
    )))
    scanner.chmod(0o755)
    command = confinement.account_script(ACCOUNT)
    command = command.replace('/usr/bin/python', str(layout / 'usr/bin/python'))
    mapped = ('/usr/bin/pyvenv.cfg', '/usr/bin/pybuilddir.txt',
              '/usr/bin/Modules/Setup.local', '/usr/pyvenv.cfg', '/usr/local/lib',
              '/usr/lib64', '/usr/lib', '/lib64', '/lib')
    command = re.sub('(?:' + '|'.join(re.escape(path) for path in mapped) + ')(?![A-Za-z0-9_])',
                     lambda match: str(layout) + match.group(), command)
    command = command.replace('/usr/bin/find', str(scanner))
    if failure == 'layout':
        (binary.parent / 'python3').unlink()
        (binary.parent / 'python3').symlink_to('python-unrecognised')
        (binary.parent / 'python-unrecognised').write_text('fixture')
    if failure == 'budget':
        command = command.replace('/bin/sh -s -- 50000 ', '/bin/sh -s -- 15 ')
    if failure == 'time':
        command = command.replace('/usr/bin/timeout 10 ', '/usr/bin/timeout 0.000001 ')
    return command, marker, tree


@pytest.mark.parametrize('boundary', ['auditor', 'bat'])
@pytest.mark.parametrize('redirect', ['python3._pth', 'python3.10._pth', 'pybuilddir.txt',
                                    'Modules/Setup.local', 'native', 'multiarch', 'native_target'])
def test_a10_both_closure_gates_reject_python_import_redirection(tmp_path, boundary, redirect):
    command, marker, tree = closure_fixture(tmp_path)
    layout = tree.parents[2]
    if redirect in {'native', 'multiarch', 'native_target'}:
        directory = tree.parent / ('fixture-linux-gnu' if redirect == 'multiarch' else '')
        directory.mkdir(mode=0o755, exist_ok=True)
        library = directory / 'libpython3.10.so.1.0'
        library.write_text('trusted fixture library')
        library.chmod(0o644)
        if redirect == 'native_target':
            target = layout / 'shared/libpython3.10.so.1.0'
            target.parent.mkdir(mode=0o755)
            library.rename(target)
            library.symlink_to(target)
            library = target
        config = library.with_name(library.name + '._pth')
    else:
        config = layout / 'usr/bin' / redirect
    config.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
    config.write_text('/unproven/imports\n')
    config.chmod(0o644)  # Even a root-owned redirect can select an unproven import tree.
    if boundary == 'bat':
        command = command[:command.index("proof=$(prove_closure)")] + 'prove_closure || exit 1\nBATC_CLOSURE\n'
    result = subprocess.run(['/bin/sh', '-c', command], capture_output=True, text=True, timeout=10)
    if boundary == 'auditor':
        assert json.loads(result.stdout) == {'status': 'unknown', 'reason': 'check_executable_untrusted'}
    else:
        assert result.returncode != 0 and result.stdout == ''
    assert not marker.exists()


@pytest.mark.parametrize('native_layout', ['direct', 'multiarch', 'alias', 'target'])
def test_a10_closure_gate_accepts_proven_native_library_without_overrides(tmp_path, native_layout):
    command, marker, tree = closure_fixture(tmp_path)
    layout = tree.parents[2]
    directory = tree.parent / ('fixture-linux-gnu' if native_layout == 'multiarch' else '')
    directory.mkdir(mode=0o755, exist_ok=True)
    library = directory / 'libpython3.10.so.1.0'
    library.write_text('trusted fixture library')
    library.chmod(0o644)
    if native_layout == 'alias':
        (layout / 'lib').symlink_to('usr/lib')
    if native_layout == 'target':
        target = layout / 'shared/libpython3.10.so.1.0'
        target.parent.mkdir(mode=0o755)
        library.rename(target)
        library.symlink_to(target)
    result = subprocess.run(['/bin/sh', '-c', command], capture_output=True, text=True, timeout=10)
    assert json.loads(result.stdout)['status'] == 'verified'
    assert marker.exists()


def test_a10_closure_gate_rejects_incomplete_native_directory_search(tmp_path):
    command, marker, tree = closure_fixture(tmp_path)
    directory = tree.parent / 'fixture-linux-gnu'
    directory.mkdir(mode=0o644)
    if directory.stat().st_uid == 0:
        pytest.skip('root bypasses the fixture directory search restriction')
    result = subprocess.run(['/bin/sh', '-c', command], capture_output=True, text=True, timeout=10)
    assert json.loads(result.stdout) == {'status': 'unknown', 'reason': 'check_executable_untrusted'}
    assert not marker.exists()


@pytest.mark.parametrize('boundary', ['auditor', 'bat'])
@pytest.mark.parametrize('bad', ['parent', 'link_owner'])
def test_a10_both_closure_gates_prove_intermediate_interpreter_symlink_hops(tmp_path, boundary, bad):
    command, marker, tree = closure_fixture(tmp_path, failure=bad)
    binary_dir = tree.parents[1] / 'bin'
    intermediate_dir = tree.parents[1] / 'intermediate-dir'
    intermediate_dir.mkdir(mode=0o755)
    (intermediate_dir / 'intermediate').symlink_to(binary_dir / 'python3.10')
    (binary_dir / 'python3').unlink()
    (binary_dir / 'python3').symlink_to(intermediate_dir / 'intermediate')
    if bad == 'parent':
        intermediate_dir.chmod(0o775)
    if boundary == 'bat':
        # Same generated definition run by the auditor immediately before sudo exec.
        command = command[:command.index("proof=$(prove_closure)")] + 'prove_closure || exit 1\nBATC_CLOSURE\n'
    result = subprocess.run(['/bin/sh', '-c', command], capture_output=True, text=True, timeout=10)
    if boundary == 'auditor':
        assert json.loads(result.stdout) == {'status': 'unknown', 'reason': 'check_executable_untrusted'}
    else:
        assert result.returncode != 0 and result.stdout == ''
    assert not marker.exists()


@pytest.mark.parametrize('bad', ['json/__init__.py', '__pycache__/json.cpython-310.pyc', 'lib-dynload/_json.so'])
def test_a10_closure_gate_blocks_writable_module_bytecode_and_extension_before_interpreter(tmp_path, bad):
    command, marker, _ = closure_fixture(tmp_path, bad=bad)
    result = subprocess.run(['/bin/sh', '-c', command], capture_output=True, text=True, timeout=10)
    assert json.loads(result.stdout) == {'status': 'unknown', 'reason': 'check_executable_untrusted'}
    assert not marker.exists()


@pytest.mark.parametrize('failure', ['layout', 'budget', 'time', 'unreadable'])
def test_a10_closure_gate_incomplete_proof_never_runs_interpreter(tmp_path, failure):
    command, marker, _ = closure_fixture(tmp_path, failure=failure)
    result = subprocess.run(['/bin/sh', '-c', command], capture_output=True, text=True, timeout=10)
    assert json.loads(result.stdout) == {'status': 'unknown', 'reason': 'check_executable_untrusted'}
    assert not marker.exists()


@pytest.mark.parametrize('bad_target', [False, True])
def test_a10_closure_gate_checks_symlink_targets_and_their_parents(tmp_path, bad_target):
    command, marker, tree = closure_fixture(tmp_path)
    target = tree.parent / 'shared-module.py'
    target.write_text('fixture')
    target.chmod(0o664 if bad_target else 0o644)
    (tree / 'linked.py').symlink_to('../shared-module.py')
    result = subprocess.run(['/bin/sh', '-c', command], capture_output=True, text=True, timeout=10)
    assert json.loads(result.stdout)['status'] == ('unknown' if bad_target else 'verified')
    assert marker.exists() is not bad_target


async def test_a10_clean_closure_gate_keeps_verified_claude_start(tmp_path, fleet_factory, mock):
    command, marker, _ = closure_fixture(tmp_path)

    class Gated(AccountRunner):
        async def run_account_check(self, host, script, timeout_s=None, *, ssh_alias):
            assert ssh_alias == ACCOUNT['check_ssh_alias']
            assert script.index('proof=$(prove_closure)') < script.index('exec /usr/bin/python3')
            self.scripts.append(script)
            result = subprocess.run(['/bin/sh', '-c', command], capture_output=True, text=True, timeout=10)
            return result.stdout

    fleet = fleet_factory(writes=True, orchestrate=True, confinement=ACCOUNT, **MANAGED)
    fleet.confinement_runner = Gated()
    try:
        result = await orchestrate.session_start(fleet, 'h1', 'demo-project', 'claude', confirm=True,
                                                write_scope='confined')
        assert marker.exists() and result['confinement']['options'] == {'permissionMode': 'acceptEdits'}
        assert len(fleet.confinement_runner.scripts) == 2
        assert mock.channels().count('claude:start-session') == 1
    finally:
        await fleet.close()


@pytest.mark.parametrize('case', ['single', 'multiple', 'trailing', 'nonzero'])
async def test_a10_account_verdict_requires_one_json_document_and_zero_exit(fleet_factory, monkeypatch, case):
    verdict = json.dumps(account_observation('verified', 'read_only_account_check'))
    output = verdict + (verdict if case == 'multiple' else 'trailing' if case == 'trailing' else '')
    command = "/usr/bin/printf '%s' " + shlex.quote(output) + ('; exit 1' if case == 'nonzero' else '')
    run = checkpoints._run

    async def fake_ssh(argv, timeout_s=None):
        assert argv[:4] == ('ssh', '-o', 'BatchMode=yes', ACCOUNT['check_ssh_alias'])
        return await run(('/bin/sh', '-c', command), timeout_s)

    monkeypatch.setattr(checkpoints, '_run', fake_ssh)
    fleet = fleet_factory(confinement=ACCOUNT)
    fleet.confinement_runner = checkpoints.SshGitRunner({})
    try:
        result = await confinement.check_account(fleet, 'h1')
        assert result['status'] == ('verified' if case == 'single' else 'unknown')
    finally:
        await fleet.close()
