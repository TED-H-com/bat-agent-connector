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
