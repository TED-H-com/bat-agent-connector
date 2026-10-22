"""Every workflow apt invocation uses the shared setup before package downloads."""
import re
from pathlib import Path

import pytest
import yaml

from scripts.desktop_fixture_inputs import path_matches

ROOT = Path(__file__).resolve().parents[1]
ACTION = './.github/actions/bounded-apt'
APT = re.compile(r'\bapt(?:-get)?\s+(?:update|install)\b|playwright\s+install-deps\b|playwright\s+install\s+--with-deps\b')


def test_all_workflow_apt_steps_use_shared_setup():
    sites = []
    for path in sorted((ROOT / '.github/workflows').glob('*.yml')):
        document = yaml.safe_load(path.read_text())
        for job_name, job in document['jobs'].items():
            steps = job.get('steps', [])
            for index, step in enumerate(steps):
                command = step.get('run', '')
                assert 'Acquire::' not in command, f'Duplicate apt settings in {path.name}/{job_name}'
                if not APT.search(command):
                    continue
                sites.append((path.name, job_name))
                assert index and steps[index - 1].get('uses') == ACTION, (path.name, job_name, step)
                assert steps[index - 1].get('if') == step.get('if')
                assert any(s.get('uses', '').startswith('actions/checkout@') for s in steps[:index - 1])
    assert set(sites) == {
        ('desktop.yml', 'browser-ui'), ('desktop.yml', 'central-ui'),
        ('desktop.yml', 'work-loop'), ('desktop.yml', 'build'), ('pages.yml', 'validate'),
    }
    assert len(sites) == 6


@pytest.mark.parametrize('workflow', ['desktop.yml', 'pages.yml'])
def test_action_changes_trigger_each_consumer_workflow(workflow):
    document = yaml.safe_load((ROOT / '.github/workflows' / workflow).read_text())
    events = document.get('on', document.get(True))
    for event in ('pull_request', 'push'):
        for name in ('action.yml', 'setup.sh'):
            assert any(path_matches(f'.github/actions/bounded-apt/{name}', pattern)
                       for pattern in events[event]['paths'])


def test_shared_apt_settings_keep_retries_and_short_timeouts():
    action = yaml.safe_load((ROOT / '.github/actions/bounded-apt/action.yml').read_text())
    assert action['runs']['using'] == 'composite'
    step, = action['runs']['steps']
    assert step['shell'] == 'bash'
    assert '/etc/apt/apt.conf.d/99-playwright-ci' in step['run']
    settings = dict(re.findall(r'(Acquire::[\w:]+) "(\d+)";', step['run']))
    assert settings == {'Acquire::Retries': '3', 'Acquire::http::Timeout': '15', 'Acquire::https::Timeout': '15'}
