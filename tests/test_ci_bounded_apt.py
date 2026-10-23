"""Every workflow apt invocation uses the shared setup before package downloads."""
import os
import re
import subprocess
from pathlib import Path

import pytest
import yaml

from scripts.desktop_fixture_inputs import path_matches

ROOT = Path(__file__).resolve().parents[1]
ACTION = './.github/actions/bounded-apt'
CACHE = 'actions/cache@caa296126883cff596d87d8935842f9db880ef25'
APT = re.compile(r'\bapt(?:-get)?\s+(?:update|install)\b|playwright\s+install-deps\b|playwright\s+install\s+--with-deps\b')


def test_all_workflow_apt_steps_use_shared_setup():
    sites = []
    native_inputs = []
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
                inputs = steps[index - 1]['with']
                expected_set = {'native-isolation': 'native-isolation', 'browser-ui': 'playwright-chromium-fonts', 'build': 'native-linux'}.get(
                    job_name, 'playwright-chromium')
                assert inputs['package-set'] == expected_set
                # The lock pins Playwright's list; native libraries and extra fonts live in the workflow.
                if job_name == 'native-isolation':
                    assert inputs['package-set-hash'] == "${{ hashFiles('.github/workflows/ci.yml') }}"
                    assert re.findall(r'apt-get install -y ([^\n]+)', command) == ['bubblewrap e2fsprogs']
                else:
                    assert "hashFiles('desktop/package-lock.json'" in inputs['package-set-hash']
                if job_name in ('browser-ui', 'build'):
                    assert "'.github/workflows/desktop.yml'" in inputs['package-set-hash']
                if job_name == 'build':
                    native_inputs.append(inputs)
                assert steps[index - 1].get('if') == step.get('if')
                assert any(s.get('uses', '').startswith('actions/checkout@') for s in steps[:index - 1])
    assert set(sites) == {
        ('ci.yml', 'native-isolation'), ('desktop.yml', 'browser-ui'), ('desktop.yml', 'central-ui'),
        ('desktop.yml', 'work-loop'), ('desktop.yml', 'build'), ('pages.yml', 'validate'),
    }
    assert len(sites) == 7
    assert len(native_inputs) == 2 and native_inputs[0] == native_inputs[1]


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
    step, = [step for step in action['runs']['steps'] if 'Acquire::' in step.get('run', '')]
    assert step['shell'] == 'bash'
    assert '/etc/apt/apt.conf.d/99-playwright-ci' in step['run']
    settings = dict(re.findall(r'(Acquire::[\w:]+) "(\d+)";', step['run']))
    assert settings == {'Acquire::Retries': '3', 'Acquire::http::Timeout': '15', 'Acquire::https::Timeout': '15'}


def test_archives_cache_matches_apt_directory_and_is_best_effort():
    action = yaml.safe_load((ROOT / '.github/actions/bounded-apt/action.yml').read_text())
    for name in ('package-set', 'package-set-hash'):
        assert action['inputs'][name]['required'] is True
    prepare, cache, configure = action['runs']['steps']
    assert prepare['id'] == 'archives'
    # The combined action restores in main and saves in post, unlike /restore or /save.
    assert cache['uses'].split('@')[0] == 'actions/cache'
    assert cache['uses'] == CACHE
    assert cache['continue-on-error'] is True
    assert cache['if'] == "steps.archives.outputs.cache-needed == 'true'"
    assert cache['with']['path'] == '${{ steps.archives.outputs.directory }}/*.deb'
    assert cache['with']['key'] == '${{ steps.archives.outputs.key }}'
    assert cache['with']['restore-keys'].splitlines() == [
        '${{ steps.archives.outputs.exact-prefix }}', '${{ steps.archives.outputs.set-prefix }}',
    ]
    assert configure['env']['APT_ARCHIVES'] == '${{ steps.archives.outputs.directory }}'
    assert 'Dir::Cache::Archives "$APT_ARCHIVES";' in configure['run']
    assert 'APT::Keep-Downloaded-Packages "true";' in configure['run']
    assert 'Binary::apt::APT::Keep-Downloaded-Packages "true";' in configure['run']
    assert 'sudo install -d -o _apt -g root -m 0700 "$APT_ARCHIVES/partial"' in configure['run']
    assert 'sudo -u _apt test -w "$APT_ARCHIVES/partial"' in configure['run']
    assert configure.get('continue-on-error', False) is False
    assert configure['shell'] == 'bash'
    assert 'test -w "$APT_ARCHIVES"' in configure['run']
    assert 'APT::Sandbox::User' not in configure['run']
    for workflow in ('desktop.yml', 'pages.yml'):
        jobs = yaml.safe_load((ROOT / '.github/workflows' / workflow).read_text())['jobs']
        pins = {s['uses'] for job in jobs.values() for s in job.get('steps', [])
                if s.get('uses', '').startswith('actions/cache@')}
        assert pins == {CACHE}


def test_cache_identity_fallback_and_repeated_native_setup(tmp_path):
    """Run key preparation with only privileged directory creation replaced by a recorder."""
    action = yaml.safe_load((ROOT / '.github/actions/bounded-apt/action.yml').read_text())
    prepare = action['runs']['steps'][0]
    assert prepare.get('id') == 'archives'
    assert prepare['env'] == {
        'PACKAGE_SET': '${{ inputs.package-set }}',
        'PACKAGE_SET_HASH': '${{ inputs.package-set-hash }}',
    }
    runner_temp = tmp_path / 'work' / '_temp'
    runner_temp.mkdir(parents=True)
    commands = tmp_path / 'commands'
    commands.mkdir()
    sudo_log = tmp_path / 'sudo.log'
    sudo = commands / 'sudo'
    sudo.write_text('#!/bin/bash\nprintf "%s\\n" "$*" >> "$SUDO_LOG"\n')
    sudo.chmod(0o755)
    output = tmp_path / 'output'
    env = {
        **os.environ, 'RUNNER_TEMP': str(runner_temp), 'GITHUB_OUTPUT': str(output),
        'PATH': f'{commands}{os.pathsep}{os.environ["PATH"]}', 'SUDO_LOG': str(sudo_log),
        'GITHUB_WORKSPACE': str(tmp_path / 'work' / 'repo' / 'repo'),
        'RUNNER_OS': 'Linux', 'RUNNER_ARCH': 'X64', 'ImageOS': 'ubuntu24', 'ImageVersion': '20261019.1',
        'PACKAGE_SET': 'native-linux', 'PACKAGE_SET_HASH': 'a' * 64,
    }

    def run(**changes):
        output.write_text('')
        subprocess.run(['bash', '-euo', 'pipefail', '-c', prepare['run']], env={**env, **changes},
                       check=True, capture_output=True, text=True)
        return dict(line.split('=', 1) for line in output.read_text().splitlines())

    cold = run()
    directory = Path(cold['directory'])
    assert directory == Path('/var/cache/bat-ci-apt/native-linux')
    for private_root in (runner_temp, Path.home(), Path(env['GITHUB_WORKSPACE'])):
        assert not directory.is_relative_to(private_root)
    assignment, = [line for line in prepare['run'].splitlines() if line.startswith('archives=')]
    assert not any(name in assignment for name in ('RUNNER_TEMP', 'HOME', 'GITHUB_WORKSPACE'))
    assert sudo_log.read_text().splitlines() == [
        f'install -d -o {os.getuid()} -g {os.getgid()} -m 0755 {directory}',
    ]
    assert cold['cache-needed'] == 'true'
    assert cold['key'] == f"apt-v1-Linux-X64-ubuntu24-native-linux-{'a' * 64}-20261019.1"
    assert cold['exact-prefix'] == cold['key'].removesuffix('20261019.1')
    assert cold['set-prefix'] == 'apt-v1-Linux-X64-ubuntu24-native-linux-'

    # The first invocation's cache post saves packages from both native install steps.
    assert run()['cache-needed'] == 'false'
    newer = run(ImageVersion='20261026.1')
    assert newer['cache-needed'] == 'true'
    assert newer['key'] != cold['key']
    assert newer['exact-prefix'] == cold['exact-prefix']
    changed_set = run(PACKAGE_SET_HASH='b' * 64)
    assert changed_set['exact-prefix'] != cold['exact-prefix']
    assert changed_set['set-prefix'] == cold['set-prefix']
    for changes in ({'RUNNER_ARCH': 'ARM64'}, {'RUNNER_OS': 'Other'}, {'ImageOS': 'ubuntu26'},
                    {'PACKAGE_SET': 'playwright-chromium'}):
        assert not run(**changes)['key'].startswith(cold['set-prefix'])


def test_apt_sandbox_access_failure_stops_configuration(tmp_path):
    """A denied sandbox check must stop the real shell before it writes apt settings."""
    action = yaml.safe_load((ROOT / '.github/actions/bounded-apt/action.yml').read_text())
    configure = action['runs']['steps'][-1]
    commands = tmp_path / 'commands'
    commands.mkdir()
    log = tmp_path / 'sudo.log'
    sudo = commands / 'sudo'
    sudo.write_text('#!/bin/bash\nprintf "%s\\n" "$*" >> "$SUDO_LOG"\n'
                    'if [[ "$1" == "-u" && "$2" == "_apt" ]]; then exit 1; fi\n')
    sudo.chmod(0o755)
    env = {**os.environ, 'PATH': f'{commands}{os.pathsep}{os.environ["PATH"]}',
           'SUDO_LOG': str(log), 'APT_ARCHIVES': str(tmp_path)}
    result = subprocess.run(['bash', '-eo', 'pipefail', '-c', configure['run']], env=env,
                            capture_output=True, text=True)
    assert result.returncode == 1
    assert log.read_text().splitlines() == [
        f'install -d -o _apt -g root -m 0700 {tmp_path}/partial',
        f'-u _apt test -w {tmp_path}/partial',
    ]
