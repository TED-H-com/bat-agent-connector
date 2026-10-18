"""Real disk fixtures and owned processes; local host dispatch stays disabled."""
from __future__ import annotations

import asyncio
import json
import os
import shlex
import signal
import sys
from types import SimpleNamespace

import pytest

from bat_agent_connector import local_git_process as process
from bat_agent_connector.checkpoints import _LOCKED_CHECK
from bat_agent_connector.operations import AmbiguousOutcome, NeedsAttention, StepFailed
from bat_agent_connector.task_verifier import _group_alive

pytestmark = pytest.mark.skipif(sys.platform != 'linux', reason='Linux process-group prerequisite')


def python(source):
    return shlex.join(('/usr/bin/python3', '-c', source))


async def wait_file(path):
    for _ in range(500):
        if path.exists():
            return path.read_text()
        await asyncio.sleep(.01)
    raise AssertionError('Owned helper did not reach its fixture barrier')


@pytest.mark.parametrize('budget', [0, -1, float('inf'), float('nan'), 7201, True, '30',
                                  pytest.param(10**1000, id='huge-integer')])
async def test_invalid_deadline_refuses_before_spawn(monkeypatch, budget):
    async def spawn(*args, **kwargs):
        raise AssertionError('Invalid deadline must not launch')
    monkeypatch.setattr(process.asyncio, 'create_subprocess_exec', spawn)
    with pytest.raises(StepFailed, match='local_git_deadline_invalid'):
        await process.run('exit 0', budget)


@pytest.mark.parametrize('platform', ['win32', 'darwin'])
async def test_other_platforms_refuse_before_spawn(monkeypatch, platform):
    monkeypatch.setattr(process, 'sys', SimpleNamespace(platform=platform))
    async def spawn(*args, **kwargs):
        raise AssertionError('Unsupported platform must not launch')
    monkeypatch.setattr(process.asyncio, 'create_subprocess_exec', spawn)
    with pytest.raises(StepFailed, match='local_git_platform_unsupported'):
        await process.run('exit 0', 1)


async def test_environment_cwd_and_nonlogin_shell_do_not_inherit_profiles(monkeypatch, tmp_path):
    startup = tmp_path / 'profile'
    marker = tmp_path / 'profile-executed'
    startup.write_text('touch ' + shlex.quote(str(marker)))
    for key in ('ENV', 'BASH_ENV', 'GIT_CONFIG', 'GIT_INDEX_FILE', 'GIT_SSH_COMMAND', 'PYTHONPATH'):
        monkeypatch.setenv(key, str(startup))
    monkeypatch.setenv('FIXTURE_PRIVATE_VALUE', 'fixture-only-secret')
    output = json.loads(await process.run(python('import os,json; print(json.dumps([os.getcwd(),dict(os.environ)]))'), 5))
    assert output[0] == '/'
    assert output[1]['PATH'] == '/usr/bin:/bin'
    assert not set(output[1]) & {'ENV', 'BASH_ENV', 'GIT_CONFIG', 'GIT_INDEX_FILE', 'GIT_SSH_COMMAND',
                                'PYTHONPATH', 'FIXTURE_PRIVATE_VALUE'}
    assert not marker.exists()


async def test_concurrent_pipes_return_exact_success_bytes():
    source = 'import os,threading; t=threading.Thread(target=lambda:os.write(2,b"e"*700000));t.start();os.write(1,b"a"*700000);t.join()'
    assert await process.run(python(source), 5) == 'a' * 700000


@pytest.mark.parametrize('pipe', [1, 2])
async def test_overflow_is_not_a_successful_receipt(pipe):
    with pytest.raises(AmbiguousOutcome, match='incomplete'):
        await process.run(python(f'import os;os.write({pipe},b"x"*{process.LIMIT + 1})'), 5)


async def test_nonzero_after_write_keeps_effect_uncertain(tmp_path):
    target = tmp_path / 'effect'
    script = 'printf effect > ' + shlex.quote(str(target)) + '; printf fixture-private >&2; exit 7'
    with pytest.raises(AmbiguousOutcome) as caught:
        await process.run(script, 5)
    assert target.read_text() == 'effect'
    assert 'fixture-private' not in str(caught.value)


async def test_spawn_failure_is_a_definitive_fixed_refusal(monkeypatch):
    async def spawn(*args, **kwargs):
        raise FileNotFoundError('fixture-private-path')
    monkeypatch.setattr(process.asyncio, 'create_subprocess_exec', spawn)
    with pytest.raises(StepFailed, match='local_git_spawn_failed') as caught:
        await process.run('exit 0', 5)
    assert 'fixture-private-path' not in str(caught.value)


def child_script(path, detached=False):
    return python(f'''import os,time
child=os.fork()
if child==0:
    {"[os.close(fd) for fd in (0,1,2)]" if detached else "pass"}
    time.sleep(30)
else:
    with open({str(path)!r},'w') as file: file.write(str(os.getpgrp()))
    {"pass" if detached else "time.sleep(30)"}
''')


@pytest.mark.parametrize('ending', ['timeout', 'cancel', 'detached'])
async def test_entire_owned_group_is_joined_without_touching_another_group(tmp_path, ending):
    marker = tmp_path / 'owned-group'
    unrelated = await asyncio.create_subprocess_exec('/usr/bin/sleep', '30', start_new_session=True)
    task = asyncio.create_task(process.run(child_script(marker, ending == 'detached'), 2 if ending == 'timeout' else 10))
    try:
        group = int(await wait_file(marker))
        if ending == 'cancel':
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            with pytest.raises(AmbiguousOutcome, match='timed out' if ending == 'timeout' else 'descendant'):
                await task
        assert not _group_alive(group)
        assert unrelated.returncode is None and _group_alive(unrelated.pid)
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        os.killpg(unrelated.pid, signal.SIGKILL)
        await unrelated.wait()


def locked_script(path):
    return python('import sys,pathlib;print(\'{"locked": true}\',flush=True); '
                  'answer=sys.stdin.readline(); '
                  f'pathlib.Path({str(path)!r}).write_text("effect") if answer==\'{{"proceed": true}}\\n\' else None; '
                  'print(\'{"saved": true}\')')


@pytest.mark.parametrize('allow', [False, True])
async def test_locked_proceed_preserves_gate_and_nested_read(tmp_path, allow):
    target = tmp_path / 'effect'
    checks = []
    async def check():
        assert not target.exists()
        checks.append(await process.run('printf nested', 5))
        if not allow:
            raise NeedsAttention('FIXTURE_WITHDRAWN', 'Original permission withdrawn')
    token = _LOCKED_CHECK.set(check)
    try:
        if allow:
            assert json.loads(await process.run(locked_script(target), 5)) == {'saved': True}
            assert target.read_text() == 'effect'
        else:
            with pytest.raises(NeedsAttention, match='Original permission withdrawn'):
                await process.run(locked_script(target), 5)
            assert not target.exists()
        assert checks == ['nested'] and _LOCKED_CHECK.get() is check
    finally:
        _LOCKED_CHECK.reset(token)


async def test_locked_stderr_overflow_does_not_deadlock_or_proceed(tmp_path):
    target = tmp_path / 'effect'
    entered = asyncio.Event()
    async def check():
        entered.set()
        await asyncio.Event().wait()
    script = python('import os,sys,pathlib;print(\'{"locked": true}\',flush=True); '
                    f'os.write(2,b"x"*{process.LIMIT + 1});answer=sys.stdin.readline(); '
                    f'pathlib.Path({str(target)!r}).write_text("effect") if answer==\'{{"proceed": true}}\\n\' else None')
    token = _LOCKED_CHECK.set(check)
    try:
        with pytest.raises(AmbiguousOutcome, match='incomplete'):
            await process.run(script, 5)
        assert entered.is_set() and not target.exists()
    finally:
        _LOCKED_CHECK.reset(token)


async def test_repeated_cancellation_still_joins_the_owned_cleanup(monkeypatch, tmp_path):
    entered, release = asyncio.Event(), asyncio.Event()
    owned = []
    stop = process._stop
    async def delayed_stop(proc, readers):
        owned.append((proc, readers))
        entered.set()
        await release.wait()
        await stop(proc, readers)
    monkeypatch.setattr(process, '_stop', delayed_stop)
    marker = tmp_path / 'owned-group'
    task = asyncio.create_task(process.run(child_script(marker), 10))
    try:
        group = int(await wait_file(marker))
        task.cancel()
        await asyncio.wait_for(entered.wait(), 5)
        for _ in range(3):
            task.cancel()
            await asyncio.sleep(.01)
        assert not task.done(), 'Cancellation must wait until the owned cleanup finishes'
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not _group_alive(group)
    finally:
        release.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        for proc, readers in owned:
            await stop(proc, readers)
