"""Real updater pointers, Work and publication journals; only providers are fake.

The package fixture changes release identity, not the data handlers or epoch.
Restart real TaskDaemon/Journal instances rather than pretending a sentinel is
business data. Distinct frozen runtime coverage lives in managed-upgrade-fixture.py.
"""
from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import sys
from contextlib import closing
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from bat_agent_connector import (
    api_auth,
    board_github,
    managed_runtime,
    platform_files,
)
from bat_agent_connector.errors import ConnectionLost
from bat_agent_connector.task_daemon import TaskDaemon
from desktop.tests.managed_runtime_assertions import assert_stopped_reply, service_stays_stopped
from tests import test_repository_sync
from tests.conftest import make_config
from tests.operation_helpers import settle_operations, stop_workers
from tests.test_api_v1 import http
from tests.test_checkpoints import snapshot
from tests.test_connector_updates import deployment, make_package, release
from tests.test_managed_runtime import installation, versioned_installation
from tests.test_pm_work_attempt_result import (
    _turn_ended,
    pm,
    ready,
    start_ready,
)
from tests.test_pm_work_follow_up import _get_op, _patch_roundtrip, _post, _settle
from tests.test_pr_publication import DELIVERER, delivery_ready, envelope, fixed, pub
from tests.test_repository_sync import world as repository_world
from tests.test_work_items import item, project
from tests.upgrade_continuity_helpers import (
    assert_rows_preserved,
    crash_after_201,
    seed_publication,
    snapshot_rows,
)

_ = deployment, pm, ready, start_ready, delivery_ready, fixed, pub, installation, versioned_installation


@pytest.fixture
def managed_seed(versioned_installation, monkeypatch):
    root, children = versioned_installation
    original = managed_runtime.ensure(root, timeout=15)
    assert managed_runtime.stop(root)['stopped'] is True
    children[-1].wait(timeout=10)
    return root, original


@pytest.fixture
async def world(request, tmp_path, mock, monkeypatch):
    target = os.environ.get('BATC_UPGRADE_SEED_DATA')
    if not target and request.node.name == 'test_managed_upgrade_recovers_original_201':
        target = str(request.getfixturevalue('managed_seed')[0])
    if target:
        data = Path(target)
        state = data / 'state'
        work = data.parent / 'publication-fixture'
        platform_files.ensure_private_directory(work)
        # Reuse the repository fixture's real Git/FakeGitHub setup, with the
        # existing owned journal instead of allocating a second business DB.
        monkeypatch.setattr(test_repository_sync, 'TaskDaemon',
            lambda config, _: TaskDaemon(config, state / 'tasks.sqlite3'))
        config_dir = data / 'config'
    else:
        home = request.getfixturevalue('deployment')[0].home if sys.platform == 'linux' else tmp_path / 'home'
        state = home / '.local/state/bat-agent-connector'
        work = state
        config_dir = home / '.config/bat-agent-connector'
    platform_files.ensure_private_directory(state)
    monkeypatch.setenv('BATC_STATE_DIR', str(state))
    monkeypatch.setenv('BATC_CONFIG_DIR', str(config_dir))
    async for value in repository_world.__wrapped__(mock, work, monkeypatch):
        yield value


def enroll(deployment):
    updater, receipt, _ = deployment
    updater.enroll(receipt, 'preview')
    updater.find_release = lambda _: release('b' * 40, '0.2.8')
    updater.install = lambda root, candidate, **_: make_package(root, candidate)
    return updater


def retained(db):
    tables = ('work_items', 'work_attempts', 'session_results', 'work_inputs',
              'operations', 'operation_steps', 'api_principals')
    result = {table: snapshot_rows(db, table) for table in tables}
    result['api_events'] = snapshot_rows(db, 'api_events')
    return result


def assert_retained(db, saved):
    for table, expected in saved.items():
        # Restart/observation may append new events, never rewrite original ones.
        assert_rows_preserved(db, table, expected, allow_append=table == 'api_events')


async def test_startup_preserves_old_work_rows_when_github_column_is_added(mock, tmp_path, monkeypatch):
    config = make_config(mock, writes=True)
    path = tmp_path / 'journal/tasks.sqlite3'
    # Seed through the real actions on the base schema, before the additive installer existed.
    with monkeypatch.context() as previous:
        previous.setattr(board_github, 'install', lambda ops: None)
        daemon = TaskDaemon(config, path)
        daemon.acquire_owner()
    try:
        pid = await project(daemon)
        for title in ('First preserved item', 'Second preserved item'):
            await item(daemon, pid, title, state='waiting', request='Keep the original request')
        assert 'github_external' not in {column[1] for column in daemon.ops.db.execute('PRAGMA table_info(work_items)')}
        saved = retained(daemon.ops.db)
    finally:
        await daemon.fleet.close()
        await daemon.inventory.close()
        daemon.journal.close()

    # TaskDaemon -> ApiV1 -> board_github.install is the actual upgraded startup path.
    daemon = TaskDaemon(config, path)
    try:
        assert [tuple(row) for row in daemon.ops.db.execute('SELECT github_external FROM work_items')] == [(None,), (None,)]
        assert_retained(daemon.ops.db, saved)
    finally:
        await daemon.fleet.close()
        await daemon.inventory.close()
        daemon.journal.close()


def differences(before, after, path=''):
    if isinstance(before, dict) and isinstance(after, dict):
        return [item for key in before.keys() | after.keys()
                for item in differences(before.get(key), after.get(key), path + '/' + key)]
    if before != after:
        return [(path, before, after)]
    return []


async def close_daemon(w):
    w['server'].close()
    await w['server'].wait_closed()
    daemon = w['d']
    await stop_workers(daemon.ops)
    await daemon.fleet.close()
    await daemon.inventory.close()
    daemon.release_owner()
    daemon.journal.close()


async def reopen_daemon(w):
    old = w['d']
    daemon = TaskDaemon(old._config, old._db_path)
    daemon.__dict__['_fixture_capabilities'] = dict(old.__dict__.get('_fixture_capabilities', {}))
    daemon.acquire_owner()
    for key in ('git_runner', 'github_config'):
        if key in old.ops.context:
            daemon.ops.context[key] = old.ops.context[key]
    w['d'] = daemon
    w['server'] = await asyncio.start_server(daemon._handle, '127.0.0.1', 0)
    w['port'] = w['server'].sockets[0].getsockname()[1]
    await daemon.inventory.refresh_host('h1')


async def read_work(w, attempt):
    prefix = '/api/v1/pm/workspaces/' + attempt['workspace_id']
    result = {}
    for name, path in [('work', '/api/v1/work-items/' + attempt['work_item_id']),
                       ('result', prefix + '/work-attempts/' + attempt['attempt_id'] + '/result')]:
        status, body = await http(w['port'], 'GET', path, tok=w['owner_token'])
        assert status == 200, body
        if name == 'result':
            # Collection diagnostics and live observation provenance belong to
            # this process, not the immutable Result. They truthfully restart
            # as not_checked_since_start; compare the complete durable view.
            body.pop('collection')
            for key in ('observed_at', 'state_source'):
                body['execution'].pop(key)
        result[name] = body
    return result


async def unknown_and_pending(w, mock, attempt, monkeypatch):
    mock.echo_sends = False

    async def lost(self, frame, timeout, on_transport, original):
        response = await original(self, frame, timeout, on_transport=on_transport)
        if frame.get('channel') == 'claude:send-message':
            raise ConnectionLost('Fixture lost the reply after the transport fence')
        return response

    with monkeypatch.context() as patch:
        _patch_roundtrip(patch, lost)
        status, body = await _post(w, attempt, 'Original uncertain instruction', key='continuity-uncertain')
        assert status == 202, body
        uncertain = body['operation']['operation_id']
        await _settle(w['d'].ops, uncertain, rounds=1)
    unknown = await _get_op(w, uncertain)
    assert unknown['status'] == 'uncertain', unknown
    assert unknown['input_receipt']['state'] == 'unknown'
    status, body = await _post(w, attempt, 'Original pending instruction', key='continuity-pending')
    assert status == 202, body
    pending = body['operation']['operation_id']
    queued = await _get_op(w, pending)
    assert queued['input_receipt']['state'] == 'queued', queued
    return uncertain, pending


async def continuity(w, mock, deployment, monkeypatch, switches):
    updater = enroll(deployment)
    attempt = await _turn_ended(w, mock, key='continuity-work-start')
    uncertain, pending = await unknown_and_pending(w, mock, attempt, monkeypatch)
    before_api = await read_work(w, attempt)
    before_checkout = snapshot(w['human'])
    original_token = w['owner_token']
    saved = retained(w['d'].ops.db)
    assert all(saved[table]['rows'] for table in ('work_attempts', 'session_results', 'operation_steps', 'api_events'))
    try:
        for switch, commit in switches:
            path = w['d']._db_path
            await close_daemon(w)
            before_bytes = path.read_bytes()
            if switch == 'apply' and not updater.status()['enabled']:
                # Rollback deliberately disables updates. Reapply is an
                # explicit operator decision through the real policy API.
                updater.policy(True)
            result = getattr(updater, switch)()
            assert result['installed']['commit'] == commit
            assert path.read_bytes() == before_bytes
            # An updater transition does not even open or checkpoint the journal.
            with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)) as db:
                assert_retained(db, saved)
            await reopen_daemon(w)
            assert_retained(w['d'].ops.db, saved)
            after_api = await read_work(w, attempt)
            assert after_api == before_api, differences(before_api, after_api)
            assert api_auth.authenticate(w['d'].ops.db, original_token, w['d']._admin_token) is not None
            assert snapshot(w['human']) == before_checkout
            for operation_id, key in [(uncertain, 'continuity-uncertain'), (pending, 'continuity-pending')]:
                operation = w['d'].ops.get(operation_id)
                assert operation['idempotency_key'] == key
    finally:
        await close_daemon(w)


@pytest.mark.skipif(sys.platform != 'linux', reason='Linux per-user updater peer')
async def test_native_mock_vault_peer_restarts_real_authentication_after_each_switch(tmp_path):
    script = Path(__file__).resolve().parents[1] / 'desktop/tests/updater-credentials-fixture.py'
    child = await asyncio.create_subprocess_exec(sys.executable, str(script), '--data-dir', str(tmp_path),
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
        env={**os.environ, 'PYTHONPATH': str(script.parents[2])})

    async def reply():
        line = await asyncio.wait_for(child.stdout.readline(), 20)
        assert line, 'Owned credential peer exited before replying'
        return json.loads(line)

    try:
        hello = await reply()
        port = int(hello['endpoint'].split(':')[2].rstrip('/'))
        identity = None
        for action, commit in [(None, 'a' * 40), ('apply', 'b' * 40), ('rollback', 'a' * 40), ('apply', 'b' * 40)]:
            if action:
                child.stdin.write((json.dumps({'action': action}) + '\n').encode())
                await child.stdin.drain()
                assert await reply() == {'commit': commit, 'credential_unchanged': True}
            status, caps = await http(port, 'GET', '/api/v1/capabilities', tok=hello['token'])
            assert status == 200 and caps['actor'] == 'fixture-operator'
            status, bootstrap = await http(port, 'GET', '/api/v1/bootstrap', tok=hello['token'])
            assert status == 200
            current = {key: bootstrap['sync'][key] for key in ('server_id', 'principal_id')}
            if identity is None:
                identity = current
            assert current == identity
        child.stdin.write(b'{"action":"stop"}\n')
        await child.stdin.drain()
        assert await reply() == {'stopped': True}
        await asyncio.wait_for(child.wait(), 10)
        assert child.returncode == 0
    finally:
        if child.returncode is None:
            child.terminate()
            await child.wait()


async def test_plain_upgrade_preserves_real_work_receipts_and_credentials(start_ready, mock, deployment, monkeypatch):
    await continuity(start_ready, mock, deployment, monkeypatch, [('apply', 'b' * 40)])


async def test_plain_rollback_and_reapply_preserve_real_work_receipts_and_credentials(start_ready, mock, deployment, monkeypatch):
    await continuity(start_ready, mock, deployment, monkeypatch,
                     [('apply', 'b' * 40), ('rollback', 'a' * 40), ('apply', 'b' * 40)])


async def lose_outer_completion(w, monkeypatch, request=None, principal=DELIVERER):
    return await crash_after_201(w, monkeypatch, request or envelope(w), principal)


async def test_managed_upgrade_recovers_original_201(start_ready, mock, monkeypatch, request, capsys):
    w = start_ready
    root, original = request.getfixturevalue('managed_seed')
    info = await seed_publication(w, mock, monkeypatch, root)
    operation_id, request_body = info['operation_id'], info['request']
    receipt, saved = info['receipt'], info['rows']
    before = snapshot(w['human'])
    await close_daemon(w)
    monkeypatch.setattr(managed_runtime, '__version__', '1.1.0')
    try:
        children = request.getfixturevalue('versioned_installation')[1]
        starts = len(children)
        held = managed_runtime.lifecycle(root)
        # managed_seed deliberately requested a user Stop. A package upgrade
        # cannot undo that hold; ordinary running upgrades use _stop_owned.
        capsys.readouterr()
        with service_stays_stopped(root), monkeypatch.context() as cli:
            cli.setattr(sys, 'argv', ['runtime', 'ensure', '--data-dir', str(root)])
            with pytest.raises(SystemExit) as refused:
                managed_runtime.main()
            assert_stopped_reply(refused.value.code, json.loads(capsys.readouterr().out), held)
            assert len(children) == starts
        upgraded = await asyncio.to_thread(managed_runtime.resume, root, timeout=15)
        assert len(children) == starts + 1
        assert {key: upgraded[key] for key in ('actor', 'server_id', 'principal_id', 'token')} == {
            key: original[key] for key in ('actor', 'server_id', 'principal_id', 'token')}
        assert upgraded['runtime_version'] == '1.1.0'
        done = await asyncio.to_thread(managed_runtime._request, upgraded['endpoint'],
            '/api/v1/operations/' + operation_id, token=w['owner_token'])
        # Managed startup's real scheduler reconciles only the saved receipt.
        until = asyncio.get_running_loop().time() + 10
        while done['operation']['status'] == 'running' and asyncio.get_running_loop().time() < until:
            await asyncio.sleep(0.05)
            done = await asyncio.to_thread(managed_runtime._request, upgraded['endpoint'],
                '/api/v1/operations/' + operation_id, token=w['owner_token'])
        state = done['operation']
        assert state['status'] == 'succeeded', (state['error_code'], state['status_reason'],
            [(step['name'], step['error']) for step in state['steps']])
        status, replay = await http(urlsplit(upgraded['endpoint']).port, 'POST', '/api/v1/operations?wait=0',
            tok=w['owner_token'], body={key: value for key, value in request_body.items() if key != 'idempotency_key'},
            headers={'Idempotency-Key': request_body['idempotency_key']})
        assert status == 200 and replay['created'] is False
        assert replay['operation']['operation_id'] == operation_id
        with closing(sqlite3.connect(root / 'state/tasks.sqlite3')) as db:
            assert_rows_preserved(db, 'pr_creation_receipts', receipt)
            for table, expected in saved.items():
                assert_rows_preserved(db, table, expected)
        assert len(w['posts']) == w['runner'].ran['publication-push'] == 1
        assert snapshot(w['human']) == before
    finally:
        # Fault fixture owns these exact Popen handles. Do not bypass the
        # product's busy guard or signal a PID recovered from a pointer.
        for child in request.getfixturevalue('versioned_installation')[1]:
            if child.poll() is None:
                child.terminate()
                await asyncio.to_thread(child.wait, timeout=10)


@pytest.mark.parametrize('switch', ['apply', 'rollback'])
async def test_plain_pointer_switch_recovers_original_201_without_repeating_effects(pub, deployment, monkeypatch, switch):
    w = pub
    updater = enroll(deployment)
    if switch == 'rollback':
        updater.apply()
    credential = api_auth.issue(w['d'].ops.db, DELIVERER.actor, sorted(DELIVERER.scopes))
    request, operation, receipt = await lose_outer_completion(w, monkeypatch)
    before_checkout = snapshot(w['human'])
    try:
        path = w['d']._db_path
        await close_daemon(w)
        original_bytes = path.read_bytes()
        getattr(updater, switch)()
        assert path.read_bytes() == original_bytes
        await reopen_daemon(w)
        status, replay = await http(w['port'], 'POST', '/api/v1/operations?wait=0', tok=credential,
            body={key: value for key, value in request.items() if key != 'idempotency_key'},
            headers={'Idempotency-Key': request['idempotency_key']})
        assert status == 200 and replay['created'] is False, replay
        assert replay['operation']['operation_id'] == operation['operation_id']
        await settle_operations(w['d'].ops)
        done = w['d'].ops.get(operation['operation_id'])
        assert done['status'] == 'succeeded', done
        assert_rows_preserved(w['d'].ops.db, 'pr_creation_receipts', receipt)
        assert len(w['posts']) == w['runner'].ran['publication-push'] == 1
        assert not any(method == 'POST' and path == '/repos/o/r/pulls' for method, path, _ in w['gh'].requests)
        assert snapshot(w['human']) == before_checkout
    finally:
        await close_daemon(w)
