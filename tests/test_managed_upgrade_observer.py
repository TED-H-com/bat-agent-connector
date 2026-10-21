"""The seed observer reports real process starts without changing their inputs."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

OBSERVER = Path(__file__).resolve().parents[1] / 'desktop/tests'
pytestmark = pytest.mark.skipif(os.name == 'nt', reason='The publication producer uses POSIX host scripts')

PROGRAM = '''
import os, subprocess, sys
from types import SimpleNamespace
import managed_upgrade_observer as observer
plugins = []
observer.pytest_configure(SimpleNamespace(pluginmanager=SimpleNamespace(register=lambda plugin: plugins.append(plugin))))
fixture = SimpleNamespace(argname='ready')
setup = plugins[0].pytest_fixture_setup(fixture, None)
next(setup)
code = "import os,sys; assert sys.argv[1] == os.environ['SEED_OBSERVER_SECRET']; print('CHILD_DONE')"
argv = [sys.executable, '-c', code, os.environ['SEED_OBSERVER_SECRET']]
if sys.argv[1] == 'popen':
    subprocess.run(argv, cwd=os.getcwd(), env=dict(os.environ), check=True)
elif sys.argv[1] == 'posix_spawn':
    pid = os.posix_spawn(sys.executable, argv, dict(os.environ))
    assert os.waitpid(pid, 0)[1] == 0
else:
    os.execve(sys.executable, argv, dict(os.environ))
try:
    setup.send(SimpleNamespace(excinfo=None))
except StopIteration:
    pass
'''


@pytest.mark.parametrize('mode,event', [('popen', 'subprocess.Popen'),
                                        ('posix_spawn', 'os.posix_spawn'), ('execve', 'os.exec')])
def test_real_child_starts_are_observed_without_replacing_stdlib(tmp_path, mode, event):
    secret = 'batc_fixture_timeline_secret'
    env = {**os.environ, 'PYTHONPATH': str(OBSERVER), 'BATC_UPGRADE_SEED_MODE': '1',
           'BATC_UPGRADE_SEED_DATA': str(tmp_path / 'installation'),
           'SEED_OBSERVER_SECRET': secret, 'TMPDIR': str(tmp_path)}
    child = subprocess.run([sys.executable, '-c', PROGRAM, mode], cwd=tmp_path, env=env,
                           capture_output=True, text=True, timeout=20)
    assert child.returncode == 0, child.stderr
    assert 'CHILD_DONE' in child.stdout  # Original argv/env reached the actual child.
    assert secret not in child.stdout
    records = [json.loads(line.split(' ', 1)[1]) for line in child.stdout.splitlines()
               if line.startswith('[seed-timeline] ')]
    starts = [item for item in records if item['event'] == event]
    assert starts and starts[-1]['fixture'] == 'ready'
    assert starts[-1]['argv'][1] == '-c' and starts[-1]['cwd'] == '<fixture>'
    assert starts[-1]['env']['values'] == 'omitted'
    assert records[0]['event'] == 'observer.start'
    assert any(item['event'] == 'fixture.setup.start' for item in records)
    if mode != 'execve':
        ended = next(item for item in records if item['event'] == 'fixture.setup.end')
        assert ended['ok'] and ended['duration_s'] > 0
    assert [item['monotonic_s'] for item in records] == sorted(item['monotonic_s'] for item in records)


def test_large_host_argument_is_never_tokenized_or_decoded(tmp_path):
    program = '''
import base64, json, shlex, sys, zlib
import managed_upgrade_observer as observer
calls = []
forbidden = {shlex.split.__code__, base64.b64decode.__code__}
def profile(frame, event, arg):
    if event == 'call' and frame.f_code in forbidden:
        calls.append(frame.f_code.co_name)
    if event == 'c_call' and arg is zlib.decompress:
        calls.append('decompress')
sys.setprofile(profile)
observer._phase(['sh', '-c', "python3 -c '" + 'x' * 42480 + "' e30="])
sys.setprofile(None)
print(json.dumps(calls))
'''
    child = subprocess.run([sys.executable, '-c', program], cwd=tmp_path,
                           env={**os.environ, 'PYTHONPATH': str(OBSERVER)},
                           capture_output=True, text=True, timeout=20)
    assert child.returncode == 0, child.stderr
    assert json.loads(child.stdout) == [], 'Large host arguments must remain opaque to the observer'


def test_setup_sampler_records_bounded_main_thread_frames_and_stops(tmp_path):
    program = '''
import threading
from types import SimpleNamespace
import managed_upgrade_observer as observer
plugins = []
observer.pytest_configure(SimpleNamespace(pluginmanager=SimpleNamespace(register=plugins.append)))
setup = plugins[0].pytest_fixture_setup(SimpleNamespace(argname='world'), None)
next(setup)
threading.Event().wait(2.2)
try:
    setup.send(SimpleNamespace(excinfo=None))
except StopIteration:
    pass
plugins[0].pytest_unconfigure(None)
assert not observer._sampler.is_alive()
'''
    child = subprocess.run([sys.executable, '-c', program], cwd=tmp_path,
                           env={**os.environ, 'PYTHONPATH': str(OBSERVER), 'BATC_UPGRADE_SEED_MODE': '1',
                                'BATC_UPGRADE_SEED_DATA': str(tmp_path / 'installation')},
                           capture_output=True, text=True, timeout=20)
    assert child.returncode == 0, child.stderr
    records = [json.loads(line.split(' ', 1)[1]) for line in child.stdout.splitlines()
               if line.startswith('[seed-timeline] ')]
    samples = [item for item in records if item['event'] == 'fixture.setup.sample']
    assert samples and len(samples) <= 2
    assert all(item['fixture'] == 'world' and 0 < len(item['stack']) <= 12 for item in samples)
    assert any(frame['function'] == 'wait' for item in samples for frame in item['stack'])
    assert all(set(frame) == {'file', 'function', 'line'} for item in samples for frame in item['stack'])
    assert all(second['monotonic_s'] - first['monotonic_s'] >= 1
               for first, second in zip(samples, samples[1:]))


def test_collection_timeline_bounds_the_import_phase_before_fixture_setup(tmp_path):
    seed = tmp_path / 'test_seed.py'
    seed.write_text('def test_seed(tmp_path):\n    assert tmp_path.is_dir()\n')
    child = subprocess.run([sys.executable, '-m', 'pytest', '-q', '-s', '-p', 'managed_upgrade_observer',
                            '--noconftest', '--rootdir=' + str(tmp_path),
                            '--basetemp=' + str(tmp_path / 'child-pytest'), str(seed)],
                           cwd=tmp_path, env={**os.environ, 'PYTHONPATH': str(OBSERVER),
                                'BATC_UPGRADE_SEED_MODE': '1',
                                'BATC_UPGRADE_SEED_DATA': str(tmp_path / 'installation')},
                           capture_output=True, text=True, timeout=20)
    assert child.returncode == 0, child.stdout + child.stderr
    records = [json.loads(line.split('[seed-timeline] ', 1)[1]) for line in child.stdout.splitlines()
               if '[seed-timeline] ' in line]
    names = [item['event'] for item in records]
    assert names.index('observer.start') < names.index('seed.collection.start')
    assert names.index('seed.collection.start') < names.index('seed.collection.end')
    assert names.index('seed.collection.end') < names.index('fixture.setup.start')
    collected = next(item for item in records if item['event'] == 'seed.collection.end')
    assert collected['ok'] and collected['duration_s'] > 0
