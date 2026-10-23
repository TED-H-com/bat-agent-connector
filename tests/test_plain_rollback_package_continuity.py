"""Plain rollback launches verified packages, rather than the test's modules.

Both releases copy current source and use explicit fixture build identities. The
fence parameter changes only LATEST_DATA_STEP in the previous copied source; it
does not claim that a supported published release has that combination. The raw
GitHub 201 is a labelled provider double, preserved by the real PR producer.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shutil
import signal
import socket
import sqlite3
import subprocess
import sys
import time
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlsplit

import pytest

from bat_agent_connector import __version__, api_auth, platform_files
from bat_agent_connector.pr_publication_host import branch
from bat_agent_connector.task_journal import LATEST_DATA_STEP
from bat_agent_connector.update_install import clean_env, installed, run, stage
from bat_agent_connector.update_release import EPOCH, REPOSITORY
from bat_agent_connector.updater import Updater
from scripts.validate_connector_update import (
    ROOT,
    copy_fixture_source,
    identity_line,
    installed_build_provenance,
)
from tests.package_build import _build_env, _command
from tests.test_api_v1 import http
from tests.test_pm_work_attempt_result import pm, ready, start_ready
from tests.test_repository_sync import world as repository_world
from tests.test_upgrade_rollback_continuity import close_daemon, differences, read_work, unknown_and_pending
from tests.upgrade_continuity_helpers import (
    assert_rows_preserved,
    checkout_files,
    seed_publication,
    snapshot_rows,
)

_ = pm, ready, start_ready
pytestmark = pytest.mark.skipif(not sys.platform.startswith('linux'), reason='The plain updater is Linux-only')


@pytest.fixture
def package_pair(tmp_path, request):
    home, source = tmp_path / 'home', tmp_path / 'source'
    platform_files.ensure_private_directory(home / '.local/bin')
    source.mkdir(mode=0o700)
    copy_fixture_source(ROOT, source)
    build_env = _build_env(tmp_path)
    # Production stage selects 3.13 even when the controlling pytest uses 3.10.
    # Interpreter provisioning belongs to CI, never to a test's scratch directory.
    build_env['UV_PYTHON_DOWNLOADS'] = 'never'
    if sys.version_info[:2] == (3, 13):
        python = Path(sys.executable)
        assert python.is_file() and os.access(python, os.X_OK), 'The controlling Python 3.13 interpreter is unavailable'
    else:
        found = subprocess.run(['uv', 'python', 'find', '3.13'], cwd=tmp_path,
                               env=build_env, capture_output=True, text=True)
        if found.returncode or not found.stdout.strip():
            pytest.skip('Python 3.13 interpreter is not discoverable; package fixtures never download interpreters')
        python = Path(found.stdout.strip())
        if not python.is_file() or not os.access(python, os.X_OK):
            pytest.skip('Discovered Python 3.13 interpreter is unavailable; package fixtures never download interpreters')
    python = python.resolve(strict=True)
    (home / '.local/bin/uv').symlink_to(shutil.which('uv'))
    (home / '.local/bin/python3.13').symlink_to(python)
    env = {**clean_env(home), 'TMPDIR': str(tmp_path)}
    _command(['git', 'init', '-q', str(source)], cwd=tmp_path, env=build_env)
    journal_source = source / 'src/bat_agent_connector/task_journal.py'
    original_journal = journal_source.read_text()
    if request.param:
        marker = f'LATEST_DATA_STEP = {LATEST_DATA_STEP}\n'
        assert original_journal.count(marker) == 1
        journal_source.write_text(original_journal.replace(marker, f'LATEST_DATA_STEP = {LATEST_DATA_STEP - 1}\n'))

    def release(version):
        _command(['git', 'add', '.'], cwd=source, env=build_env)
        _command(['git', '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid',
                  '-c', 'core.hooksPath=/dev/null', 'commit', '-qm', 'disposable package fixture'],
                 cwd=source, env=build_env)
        commit = _command(['git', 'rev-parse', 'HEAD'], cwd=source, env=build_env)
        tag = 'v' + version + '-preview.fixture'
        _command(['git', 'tag', tag], cwd=source, env=build_env)
        return {'version': version, 'commit': commit, 'tag': tag, 'epoch': EPOCH, 'prerelease': True,
                'skill_sha256': hashlib.sha256((source / 'skills/bat-agent-connector/SKILL.md').read_bytes()).hexdigest()}

    old = release(__version__)
    proofs, provenance = {}, {}

    def command(args, **kwargs):
        kwargs['env'] = {**kwargs['env'], 'TMPDIR': str(tmp_path), 'UV_LINK_MODE': 'copy'}
        if 'fetch' in args:
            assert REPOSITORY + '.git' in args
            args = [value if value != REPOSITORY + '.git' else source.as_uri() for value in args]
            args = [args[0], '-c', 'protocol.file.allow=always', *args[1:]]
        return run(args, **kwargs)

    def install(root, candidate, *, home):
        started = time.monotonic()
        proof = stage(root, candidate, home=home, command=command)
        assert installed(root, candidate, home=home) == proof
        proofs[candidate['commit']] = proof
        provenance[candidate['commit']] = installed_build_provenance(root, candidate, env=env)
        print(f'REAL PACKAGE {candidate["version"]}: {time.monotonic() - started:.3f}s', flush=True)
        return proof

    previous = home / '.local/share/bat-agent-connector/releases' / old['commit']
    install(previous, old, home=home)
    # The actual installed bootstrap verifier creates links and its enrollment receipt.
    bootstrap = json.loads(run([str(previous / 'tools/bat-agent-connector/bin/python'), '-I', '-B', '-m',
        'bat_agent_connector.installer', str(previous), json.dumps(old), '--source', str(source)], env=env))
    feed = [old]
    updater = Updater(home, find_release=lambda _, observation=None: feed[0], install=install)
    assert updater.enroll(Path(bootstrap['receipt']), 'preview')['installed'] == old
    # Version fields change build identity only. The fence variant restores the
    # original constant in the new release; all other handlers stay identical.
    journal_source.write_text(original_journal)
    parts = [int(part) for part in __version__.split('.')]
    parts[-1] += 1
    version = '.'.join(map(str, parts))
    for name, before, after in (
        ('pyproject.toml', f'version = "{__version__}"', f'version = "{version}"'),
        ('uv.lock', f'name = "bat-agent-connector"\nversion = "{__version__}"',
         f'name = "bat-agent-connector"\nversion = "{version}"'),
        ('src/bat_agent_connector/__init__.py', f'__version__ = "{__version__}"', f'__version__ = "{version}"'),
        ('skills/bat-agent-connector/SKILL.md', f'version: {__version__}\n', f'version: {version}\n'),
    ):
        path = source / name
        assert before in path.read_text()
        path.write_text(path.read_text().replace(before, after, 1))
    current = release(version)
    feed[0] = current
    yield SimpleNamespace(home=home, updater=updater, old=old, current=current,
                          proofs=proofs, provenance=provenance, env=env)


@pytest.fixture
async def world(package_pair, tmp_path, mock, monkeypatch):
    data = tmp_path / 'runtime'
    state = data / 'state'
    platform_files.ensure_private_directory(state)
    platform_files.ensure_private_directory(data / 'config')
    monkeypatch.setenv('BATC_STATE_DIR', str(state))
    monkeypatch.setenv('BATC_CONFIG_DIR', str(data / 'config'))
    trace = data / 'git-trace.jsonl'
    monkeypatch.setenv('GIT_TRACE2_EVENT', str(trace))
    async for value in repository_world.__wrapped__(mock, state, monkeypatch):
        value['data'] = data
        value['git_trace'] = trace
        yield value


def snapshot(path):
    with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)) as db:
        schema = db.execute('SELECT type,name,sql FROM sqlite_master ORDER BY type,name').fetchall()
        tables = {name: snapshot_rows(db, name)
                  for kind, name, _ in schema if kind == 'table'}
        return hashlib.sha256(path.read_bytes()).hexdigest(), schema, tables, db.execute('PRAGMA user_version').fetchone()[0]


CHILD = '''
import json, os, sys
from pathlib import Path
from bat_agent_connector import build_identity, cli, pr_publication, task_daemon, task_journal
package = Path(task_journal.__file__).parent
print(json.dumps({'identity': build_identity.build_identity(),
    'pid': os.getpid(), 'executable': sys.executable, 'prefix': sys.prefix,
    'embedded_commit': json.loads((package / 'build-info.json').read_text())['commit'],
    'modules': {module.__name__: module.__file__ for module in (cli, task_daemon, task_journal, pr_publication)},
    'maximum_data_step': task_journal.LATEST_DATA_STEP}), flush=True)
def audit(event, args):
    record = None
    if event == 'subprocess.Popen':
        record = {'event': event, 'executable': Path(os.fsdecode(args[0])).name,
                  'git_push': 'push' in args[1]}
    elif event == 'sqlite3.connect':
        record = {'event': event, 'database': str(args[0])}
    elif event == 'socket.bind':
        record = {'event': event}
    if record is not None:
        record['pid'] = os.getpid()
        with open(os.environ['BATC_PACKAGE_PROCESS_LOG'], 'a') as log:
            log.write(json.dumps(record) + '\\n')
sys.addaudithook(audit)
raise SystemExit(cli.main())
'''


def launch(pair, w, path, log, *, port=0):
    state = pair.updater.read()
    selected = state['versions'][state['current']]
    pair.updater.verify(selected)
    root = Path(selected['root'])
    assert (pair.updater.root / 'current').resolve() == root
    assert (pair.home / '.local/bin/batc').resolve() == root / 'tools/bat-agent-connector/bin/batc'
    child = subprocess.Popen([str(root / 'tools/bat-agent-connector/bin/python'), '-I', '-B', '-u', '-c', CHILD,
        '--config', str(w['data'] / 'config/hosts.toml'), 'serve', '--db', str(path), '--port', str(port)],
        cwd=pair.home, env={**pair.env, 'BATC_STATE_DIR': str(path.parent),
            'BATC_CONFIG_DIR': str(w['data'] / 'config'),
            'GIT_TRACE2_EVENT': str(w['git_trace']),
            'BATC_PACKAGE_PROCESS_LOG': str(w['data'] / 'package-processes.jsonl'),
            **{name: os.environ[name] for name in ('BATC_TEST_TOKEN', 'REPO_FAKE_TOKEN')}},
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=log, text=True)
    try:
        facts = identity_line(child)
        assert facts['pid'] == child.pid
        assert Path(facts['executable']) == root / 'tools/bat-agent-connector/bin/python'
        assert Path(facts['prefix']) == root / 'tools/bat-agent-connector'
        verified = pair.provenance[selected['release']['commit']]
        assert facts['identity'] == verified['identity']
        assert facts['embedded_commit'] == verified['embedded_commit'] == selected['release']['commit']
        assert selected['proof'] == pair.proofs[selected['release']['commit']]
        for module, filename in facts['modules'].items():
            assert Path(filename) == Path(selected['proof']['package']) / (module.rsplit('.', 1)[1] + '.py')
        print('SELECTED PACKAGE ' + json.dumps(facts, sort_keys=True), flush=True)
        return child, facts
    except BaseException:
        stop(child)
        raise


def stop(child):
    if child.poll() is None:
        child.send_signal(signal.SIGINT)
        try:
            child.wait(timeout=10)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait(timeout=10)
    child.stdout.close()


async def running_port(child, path):
    until = asyncio.get_running_loop().time() + 30
    while True:
        assert child.poll() is None, 'Selected package exited before listening'
        try:
            pointer = json.loads((path.parent / 'task-service.json').read_text())
        except (OSError, ValueError):
            pointer = {}
        if pointer.get('pid') == child.pid and pointer.get('endpoint'):
            assert pointer['db_path'] == str(path.resolve())
            endpoint = urlsplit(pointer['endpoint'])
            assert endpoint.scheme == 'http' and endpoint.hostname == '127.0.0.1' and endpoint.path == '/rpc'
            assert endpoint.port and endpoint.port != 18796, 'Disposable package must never contact the default port'
            return endpoint.port
        assert asyncio.get_running_loop().time() < until, 'Selected package did not publish its listener'
        await asyncio.sleep(0.05)


async def seeded(w, mock, monkeypatch):
    info = await seed_publication(w, mock, monkeypatch, w['data'])
    uncertain, pending = await unknown_and_pending(w, mock, info['attempt'], monkeypatch)
    # Match serve()'s real post-refresh collection before comparing live diagnostics.
    await w['d'].result_collector.collect_host('h1')
    before_api = await read_work(w, info['attempt'])
    principal = api_auth.authenticate(w['d'].ops.db, info['token'], w['d']._admin_token)
    assert principal is not None
    path = w['d']._db_path
    await close_daemon(w)
    # Remove only the stopped, test-owned bootstrap's stale discovery record.
    pointer = path.parent / 'task-service.json'
    if pointer.exists():
        assert json.loads(pointer.read_text())['pid'] == os.getpid()
        pointer.unlink()
    with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)) as db:
        stable = {table: snapshot_rows(db, table) for table in (
            'work_items', 'work_attempts', 'session_results', 'work_inputs', 'api_principals', 'pr_creation_receipts')}
        assert stable['work_inputs']['rows'] and stable['pr_creation_receipts']['rows']
        events = snapshot_rows(db, 'api_events')
        keys = {oid: db.execute('SELECT idem_key FROM operations WHERE operation_id=?', (oid,)).fetchone()[0]
                for oid in (uncertain, pending, info['operation_id'])}
    return path, info, stable, events, keys, before_api, principal.actor


def assert_one_push(w, info):
    ref = 'refs/heads/' + branch(info['operation_id'])
    starts = [json.loads(line) for line in w['git_trace'].read_text().splitlines()]
    pushes = [item for item in starts if item['event'] == 'start' and 'push' in item['argv']
              and any(ref in arg for arg in item['argv'])]
    assert len(pushes) == 1, pushes
    process_log = w['data'] / 'package-processes.jsonl'
    if process_log.exists():
        children = [json.loads(line) for line in process_log.read_text().splitlines()]
        assert not any(item['event'] == 'subprocess.Popen' and (item['executable'] == 'ssh' or item['git_push'])
                       for item in children), children


def journal_opens(w, pid):
    observed = [item for line in (w['data'] / 'package-processes.jsonl').read_text().splitlines()
                if (item := json.loads(line))['pid'] == pid]
    return observed, [item['database'] for item in observed if item['event'] == 'sqlite3.connect']


@pytest.mark.parametrize('package_pair', [False], indirect=True, ids=['compatible-builds'])
async def test_previous_package_recovers_publication_and_preserves_work_inputs_and_credentials(
        package_pair, start_ready, mock, monkeypatch, tmp_path):
    pair, w = package_pair, start_ready
    path, info, stable, events, keys, before_api, actor = await seeded(w, mock, monkeypatch)
    manual = checkout_files(w['human'])
    # Plain apply changes future launches; it does not restart the running producer.
    # Roll back before that first new launch, so the previous package must recover 201.
    before = snapshot(path)
    assert pair.updater.apply()['installed'] == pair.current
    assert snapshot(path) == before
    for action, candidate in [('rollback', pair.old), ('apply', pair.current)]:
        if action == 'apply':
            pair.updater.policy(True)
        before = snapshot(path)
        assert getattr(pair.updater, action)()['installed'] == candidate
        assert snapshot(path) == before
        with (tmp_path / (action + '.log')).open('w') as log:
            child, facts = await asyncio.to_thread(launch, pair, w, path, log)
            try:
                assert facts['maximum_data_step'] == LATEST_DATA_STEP
                port = await running_port(child, path)
                status, capabilities = await http(port, 'GET', '/api/v1/capabilities', tok=info['token'])
                assert status == 200 and capabilities['actor'] == actor
                assert capabilities['build'] == facts['identity']
                status, version = await http(port, 'GET', '/api/v1/version', tok=info['token'])
                assert status == 200 and version['connector'] == candidate['version']
                until = asyncio.get_running_loop().time() + 30
                while True:
                    status, body = await http(port, 'GET', '/api/v1/operations/' + info['operation_id'], tok=info['token'])
                    assert status == 200
                    operation = body['operation']
                    if operation['status'] == 'succeeded':
                        break
                    assert operation['status'] == 'running', operation
                    assert asyncio.get_running_loop().time() < until, operation
                    await asyncio.sleep(0.05)
                status, replay = await http(port, 'POST', '/api/v1/operations?wait=0', tok=info['token'],
                    body={key: value for key, value in info['request'].items() if key != 'idempotency_key'},
                    headers={'Idempotency-Key': info['request']['idempotency_key']})
                assert status == 200 and replay['created'] is False
                assert replay['operation']['operation_id'] == info['operation_id']
                # Compare after the new service's first ordinary collection,
                # rather than racing its not_checked_since_start diagnostics.
                result_path = ('/api/v1/pm/workspaces/' + info['attempt']['workspace_id'] +
                               '/work-attempts/' + info['attempt']['attempt_id'] + '/result')
                while True:
                    status, observed = await http(port, 'GET', result_path, tok=info['token'])
                    assert status == 200
                    if observed['collection']['status'] == 'checked':
                        break
                    assert asyncio.get_running_loop().time() < until, observed['collection']
                    await asyncio.sleep(0.05)
                after_api = await read_work({'port': port, 'owner_token': info['token']}, info['attempt'])
                # Result's cursor is the global event head, not a Result field.
                # Recovering outer completion appends events; retain that proof too.
                cursor = after_api['result'].pop('cursor')
                old_cursor = before_api['result']['cursor']
                assert cursor >= old_cursor
                old_events = before_api['result']['events_read']['http']
                assert after_api['result']['events_read']['http'] == old_events.replace(
                    f'?after={old_cursor}&', f'?after={cursor}&', 1)
                after_api['result']['events_read']['http'] = old_events
                expected_api = {**before_api, 'result': {key: value for key, value in before_api['result'].items()
                                                        if key != 'cursor'}}
                assert after_api == expected_api, differences(expected_api, after_api)
                with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)) as db:
                    for table, expected in stable.items():
                        assert_rows_preserved(db, table, expected)
                    assert_rows_preserved(db, 'api_events', events, allow_append=True)
                    for oid, key in keys.items():
                        assert db.execute('SELECT idem_key FROM operations WHERE operation_id=?', (oid,)).fetchone()[0] == key
                assert len(w['posts']) == w['runner'].ran['publication-push'] == 1
                assert w['gh'].count('POST', r'/repos/o/r/pulls$') == 1
                assert_one_push(w, info)
                _, databases = journal_opens(w, child.pid)
                assert path.as_uri() + '?mode=ro' in databases and str(path) in databases
                assert checkout_files(w['human']) == manual
                print('PACKAGE CONTINUITY ' + json.dumps({'commit': candidate['commit'],
                    'work_item_id': info['attempt']['work_item_id'], 'operation_id': info['operation_id'],
                    'original_keys': len(keys), 'credentials': 'authenticated',
                    'api_content_equal': True, 'create_pull': 1, 'push': 1}), flush=True)
            finally:
                await asyncio.to_thread(stop, child)


@pytest.mark.parametrize('package_pair', [True], indirect=True, ids=['lower-supported-step'])
async def test_previous_package_refuses_newer_journal_before_listening_or_writing(
        package_pair, start_ready, mock, monkeypatch, tmp_path):
    pair, w = package_pair, start_ready
    path, info, *_ = await seeded(w, mock, monkeypatch)
    before = snapshot(path)
    assert pair.updater.apply()['installed'] == pair.current
    assert pair.updater.rollback()['installed'] == pair.old
    assert snapshot(path) == before
    provider_before = (len(mock.frames), len(mock.invokes), len(mock.auth_frames), len(w['gh'].requests))
    with socket.socket() as reservation:
        reservation.bind(('127.0.0.1', 0))
        port = reservation.getsockname()[1]
    error_log = tmp_path / 'refusal.log'
    with error_log.open('w') as log:
        child, facts = await asyncio.to_thread(launch, pair, w, path, log, port=port)
        try:
            assert facts['maximum_data_step'] == LATEST_DATA_STEP - 1
            assert facts['identity']['commit'] == pair.old['commit']
            assert await asyncio.to_thread(child.wait, timeout=30) == 1
        finally:
            await asyncio.to_thread(stop, child)
    message = error_log.read_text()
    assert 'JOURNAL_NEWER_DATA_STEP' in message
    assert f'journal records data step {LATEST_DATA_STEP}' in message
    assert f'supports up to {LATEST_DATA_STEP - 1}' in message
    assert 'reopen with the newer Connector' in message and 'Traceback' not in message
    with socket.socket() as probe:
        with pytest.raises(ConnectionRefusedError):
            probe.connect(('127.0.0.1', port))
    assert not (path.parent / 'task-service.json').exists()
    observed, databases = journal_opens(w, child.pid)
    assert databases == [path.as_uri() + '?mode=ro']
    assert not any(item['event'] == 'socket.bind' for item in observed)
    assert snapshot(path) == before
    assert (len(mock.frames), len(mock.invokes), len(mock.auth_frames), len(w['gh'].requests)) == provider_before
    assert len(w['posts']) == w['runner'].ran['publication-push'] == 1
    print('PACKAGE REFUSAL ' + json.dumps({'commit': pair.old['commit'], 'exit': child.returncode,
        'diagnostic': message.strip(), 'journal_unchanged': True, 'no_listener': True,
        'bind_events': 0, 'journal_open': 'read-only', 'provider_calls': 0}), flush=True)
