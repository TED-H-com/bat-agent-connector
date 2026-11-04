"""Offline deployment fixtures: never connect to SSH or invoke installed engines."""
from __future__ import annotations

import asyncio
import copy
import json

import pytest

from bat_agent_connector import deploy_status as central
from bat_agent_connector import deploy_status_helper as helper
from bat_agent_connector.cli import build_parser, main
from bat_agent_connector.errors import ConfigError
from tests.test_api_v1 import daemon, http  # noqa: F401


@pytest.fixture
def binding(tmp_path):
    (tmp_path / '.git').mkdir()
    return dict(alias='fixture-alias', deploy_dirs=[str(tmp_path)], unit_patterns=['custom*'])


def fixture_run(argv):
    assert helper.allowed(argv), argv
    assert not set(argv) & {'exec', 'restart', 'start', 'stop', 'pull', 'rm', 'fetch', 'checkout'}
    if argv[0] == 'podman':
        raise helper.ReadError('permission_denied')
    if argv[0] == 'docker':
        return json.dumps(dict(Names='web', Image='example:v1', State='running', Status='Up (healthy)',
            Labels='private=SECRET,com.docker.compose.project=example,com.docker.compose.project.working_dir=/private/example,com.docker.compose.project.config_files=/private/compose.yml,com.docker.compose.service=web', Env=['SECRET']))
    if argv[0] == 'systemctl':
        return json.dumps([dict(unit='custom.service', load='loaded', active='active', sub='running', description='fixture'),
                           dict(unit='unrelated.service', description='PRIVATE')])
    if 'rev-parse' in argv:
        return 'abcdef123456\n' if 'HEAD' == argv[-1] and '--short=12' in argv else 'main\n'
    if 'log' in argv:
        return '2026-11-01T00:00:00+00:00\n' + 'x' * 1000 + '\nPRIVATE\n'
    return ' M tracked\n?? new\n'


def test_helper_projection(binding):
    reply = helper.collect(binding, fixture_run)
    assert central.validate(reply, binding) == reply
    assert reply['errors'] == [dict(source='podman', code='permission_denied')]
    assert reply['containers'][0]['health'] == 'healthy'
    assert reply['compose_projects'][0]['counts'] == {'running': 1}
    assert reply['containers'][0]['working_dir'] == 'example'
    assert reply['containers'][0]['config_files'] == 'compose.yml'
    assert len(reply['systemd']) == 2
    assert reply['deploys'][0]['dirty_count'] == 2
    assert len(reply['deploys'][0]['subject']) == 256
    assert not any(v in json.dumps(reply) for v in ('SECRET', 'PRIVATE', '/private', binding['deploy_dirs'][0]))


def test_bounds_and_text_fallback(binding):
    def execute(argv):
        if argv[0] in {'docker', 'podman'}:
            return '\n'.join(json.dumps({'Names': 'n' * 1000}) for _ in range(600))
        if argv[0] == 'systemctl':
            if argv[-1] == '--output=json':
                raise helper.ReadError('command_failed')
            return '\n'.join('bat.service loaded active running BAT' for _ in range(300))
        return fixture_run(argv)
    reply = helper.collect(binding, execute)
    assert len(reply['containers']) == 500 and len(reply['systemd']) == 200
    central.validate(reply, binding)


@pytest.mark.parametrize('field', ['containers', 'compose_projects', 'systemd', 'deploys', 'errors'])
def test_strict_reply(binding, field):
    reply = helper.collect(binding, fixture_run)
    reply[field][0]['env'] = 'SECRET'
    with pytest.raises(ValueError):
        central.validate(reply, binding)


def test_reply_types(binding):
    original = helper.collect(binding, fixture_run)
    for mutate in (lambda r: r.update(version=True), lambda r: r.update(containers=[{}] * 501),
                   lambda r: r['deploys'][0].update(dirty_count=True), lambda r: r.update(extra='secret'),
                   lambda r: r['errors'][0].update(code='PRIVATE')):
        reply = copy.deepcopy(original)
        mutate(reply)
        with pytest.raises(ValueError):
            central.validate(reply, binding)


def test_config(binding):
    raw = dict(managed_setup={'ssh_hosts': {'worker': 'fixture-alias'}}, deploy_status={'hosts': [dict(host='worker', deploy_dirs=binding['deploy_dirs'])]})
    assert central.parse_deploy_status(raw, {'worker'})['worker']['alias'] == 'fixture-alias'
    for patch in ({'host': 'unknown'}, {'deploy_dirs': ['relative']}, {'deploy_dirs': ['/srv/../private']},
                  {'unit_patterns': ['; bad']}, {'deploy_dirs': [None]}, {'unknown': True}):
        bad = copy.deepcopy(raw)
        bad['deploy_status']['hosts'][0].update(patch)
        with pytest.raises(ConfigError):
            central.parse_deploy_status(bad, {'worker'})
    for alias in ('-bad', 'two words', 'user@host', 'x;bad', ''):
        raw['managed_setup']['ssh_hosts']['worker'] = alias
        with pytest.raises(ConfigError):
            central.parse_deploy_status(raw, {'worker'})


def test_read_only_allowlist(binding):
    for argv in (['docker', 'exec', 'x'], ['systemctl', 'restart', 'bat'], ['git', '-C', '/tmp', 'pull'], ['sh', '-c', 'anything']):
        assert not helper.allowed(argv)
        with pytest.raises(ValueError):
            helper.run(argv)
    from pathlib import Path
    nested = Path(binding['deploy_dirs'][0]) / 'not-a-repository'
    nested.mkdir()
    binding['deploy_dirs'] = [str(nested)]

    def no_git(argv):
        assert argv[0] != 'git', 'Never search ancestors of a configured non-repository'
        return fixture_run(argv)
    row = helper.collect(binding, no_git)['deploys'][0]
    assert row['error'] == 'missing' and row['sha'] == ''


class Stream:
    def __init__(self, value):
        self.value = value

    async def read(self, n):
        chunk, self.value = self.value[:n], self.value[n:]
        return chunk


class Process:
    def __init__(self, out=b'', err=b'', blocked=False):
        self.stdout, self.stderr = Stream(out), Stream(err)
        self.returncode = None
        self.blocked, self.killed, self.joined = blocked, False, False

    def kill(self):
        self.killed = True
        self.blocked = False

    async def wait(self):
        while self.blocked:
            await asyncio.sleep(.001)
        self.returncode = 0
        self.joined = True


async def test_ssh_transport_and_failures(binding, monkeypatch):
    collector = central.DeployStatus({'worker': binding}, timeout_s=.01)
    argv = collector.argv(binding)
    assert argv[:6] == ('ssh', '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes', 'fixture-alias')
    assert argv[6].startswith('python3 -I -S -B -c ')
    cases = [(json.dumps(helper.collect(binding, fixture_run)).encode(), b'', False, None),
             (b'{}', b'', False, 'invalid_reply'), (b'x' * (central.LIMIT + 1), b'', False, 'invalid_reply'),
             (b'{}', b'SECRET' * 2000, False, 'invalid_reply'), (b'', b'', True, 'host_unreachable')]
    for out, err, blocked, error in cases:
        process = Process(out, err, blocked)

        async def spawn(*args, **kwargs):
            assert args == argv
            return process
        monkeypatch.setattr(central.hidden_process, 'create_subprocess_exec', spawn)
        result = await collector.document('worker')
        assert result['hosts'][0].get('error') == error
        assert process.joined
        if blocked or len(out) > central.LIMIT or len(err) > 8192:
            assert process.killed
        assert 'SECRET' not in json.dumps(result)


def test_helper_spawn_missing_and_permission(monkeypatch):
    for exc, code in ((FileNotFoundError(), 'missing'), (PermissionError(), 'permission_denied')):
        def spawn(*args, **kwargs):
            assert kwargs['shell'] is False and kwargs['env']['GIT_OPTIONAL_LOCKS'] == '0'
            raise exc
        monkeypatch.setattr(helper, 'popen', spawn)
        with pytest.raises(helper.ReadError, match=code):
            helper.run(['docker', 'ps', '-a', '--no-trunc', '--format', '{{json .}}'])


def test_cli_empty_and_renderer(tmp_path, capsys):
    config = tmp_path / 'hosts.toml'
    config.write_text('')
    assert main(['--config', str(config), 'hosts', 'deploy-status', '--json']) == 0
    assert json.loads(capsys.readouterr().out) == {'version': 1, 'hosts': []}
    assert build_parser().parse_args(['--json', 'hosts', 'deploy-status']).json
    from bat_agent_connector.cli import r_deploy_status
    r_deploy_status({'hosts': [{'host': 'worker', 'error': 'host_unreachable'}]})
    assert 'worker: host_unreachable' in capsys.readouterr().out


async def test_api(daemon, binding, monkeypatch):  # noqa: F811
    daemon.deploy_status = central.DeployStatus({'worker': binding})

    async def read(_binding):
        return helper.collect(binding, fixture_run)
    monkeypatch.setattr(daemon.deploy_status, 'read', read)
    server = await asyncio.start_server(daemon._handle, '127.0.0.1', 0)
    async with server:
        port = server.sockets[0].getsockname()[1]
        status, body = await http(port, 'GET', '/api/v1/hosts/deploy-status?host=worker', tok=daemon._admin_token)
        assert status == 200 and body['hosts'][0]['containers'][0]['name'] == 'web'
        status, _ = await http(port, 'GET', '/api/v1/hosts/deploy-status?host=unknown', tok=daemon._admin_token)
        assert status == 422
        status, _ = await http(port, 'GET', '/api/v1/hosts/deploy-status')
        assert status == 401


async def test_cancel_kills_and_joins(binding, monkeypatch):
    process = Process(blocked=True)
    started = asyncio.Event()

    async def spawn(*args, **kwargs):
        started.set()
        return process
    monkeypatch.setattr(central.hidden_process, 'create_subprocess_exec', spawn)
    task = asyncio.create_task(central.DeployStatus({'worker': binding}).document())
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert process.killed and process.joined


def test_shipped_helper_executes_in_isolation(binding):
    import sys

    from bat_agent_connector import hidden_process
    source = central.helper_source(binding)
    # Replace only the final invocation; execute the exact bundle in isolated Python,
    # with a fake command reader, never engines or SSH.
    source = source.rsplit('m.main(', 1)[0]
    source += "\ndef fake(argv):\n raise m.ReadError('missing')\n"
    source += f"print(m.json.dumps(m.collect({binding!r}, fake)))\n"
    output = hidden_process.run([sys.executable, '-I', '-S', '-B', '-c', source],
                                capture_output=True, timeout=10, check=True)
    reply = json.loads(output.stdout)
    assert len(reply['errors']) == 4
    central.validate(reply, binding)


def test_secret_metadata_redaction(binding):
    token = 'ghp_' + 'A' * 36
    reply = helper.collect(binding, lambda argv: fixture_run(argv).replace('example:v1', token))
    assert token not in json.dumps(reply)
    central.validate(reply, binding)


@pytest.mark.parametrize('mode,code', [('timeout', 'timeout'), ('stdout', 'output_limit'),
                                      ('stderr', 'output_limit'), ('permission', 'permission_denied'),
                                      ('failure', 'command_failed')])
def test_command_limits_kill_and_join(monkeypatch, mode, code):
    import io
    import subprocess

    class Child:
        def __init__(self):
            self.stdout = io.BytesIO(b'x' * (helper.LIMIT + 1) if mode == 'stdout' else b'')
            self.stderr = io.BytesIO(b'x' * 8193 if mode == 'stderr' else b'permission denied' if mode == 'permission' else b'')
            self.returncode = 1 if mode in {'permission', 'failure'} else 0
            self.killed, self.joined = False, False

        def kill(self):
            self.killed = True

        def wait(self, timeout=None):
            if mode == 'timeout' and not self.killed:
                raise subprocess.TimeoutExpired('fixture', timeout)
            self.joined = True

    child = Child()
    monkeypatch.setattr(helper, 'popen', lambda *args, **kwargs: child)
    with pytest.raises(helper.ReadError, match=code):
        helper.run(['docker', 'ps', '-a', '--no-trunc', '--format', '{{json .}}'])
    assert child.joined and child.stdout.closed and child.stderr.closed
    if mode in {'timeout', 'stdout', 'stderr'}:
        assert child.killed
