"""Real updater and authenticated API peer for the native MockVault continuity test.

The caller supplies a disposable disk directory. No OS vault, user installation,
release feed or live provider is opened. The native test owns the credential-store
double; this peer owns only the real server-side credential and package pointers.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

from bat_agent_connector import api_auth, platform_files
from bat_agent_connector.config import parse_config
from bat_agent_connector.task_daemon import TaskDaemon
from tests.test_connector_updates import deployment, make_package, release


async def serve(directory):
    updater, receipt, _ = deployment.__wrapped__(directory)
    updater.enroll(receipt, 'preview')
    updater.find_release = lambda _, observation=None: release('b' * 40, '0.2.8')
    updater.install = lambda root, candidate, **_: make_package(root, candidate)
    state = updater.home / '.local/state/bat-agent-connector'
    platform_files.ensure_private_directory(state)
    os.environ['BATC_STATE_DIR'] = str(state)
    os.environ['BATC_CONFIG_DIR'] = str(updater.home / '.config/bat-agent-connector')
    for name in ('BATC_TASK_SETTINGS', 'BATC_TASK_ADMIN_TOKEN_FILE', 'BATC_TASK_CAPABILITY',
                 'BATC_TASK_URL', 'BATC_PM_PROVIDER_CONFIG', 'BATC_DEVICE_ID'):
        os.environ.pop(name, None)
    path = state / 'tasks.sqlite3'
    daemon = TaskDaemon(parse_config({'hosts': {}}), path)
    daemon.acquire_owner()
    token = api_auth.issue(daemon.journal.db, 'fixture-operator', ['observe'])
    credential = tuple(daemon.journal.db.execute('SELECT * FROM api_principals').fetchone())
    server = await asyncio.start_server(daemon._handle, '127.0.0.1', 0)
    port = server.sockets[0].getsockname()[1]

    def reply(value):
        print(json.dumps(value), flush=True)

    async def close():
        server.close()
        await server.wait_closed()
        await daemon.fleet.close()
        await daemon.inventory.close()
        daemon.release_owner()
        daemon.journal.close()

    try:
        # Private fixture pipe only; the token never enters the test receipt/log.
        reply({'endpoint': f'http://127.0.0.1:{port}/', 'token': token})
        while line := await asyncio.wait_for(asyncio.to_thread(sys.stdin.readline), 30):
            action = json.loads(line)['action']
            if action == 'stop':
                reply({'stopped': True})
                break
            assert action in {'apply', 'rollback'}
            await close()
            before = path.read_bytes()
            if action == 'apply' and not updater.status()['enabled']:
                updater.policy(True)
            switched = getattr(updater, action)()
            assert path.read_bytes() == before
            daemon = TaskDaemon(parse_config({'hosts': {}}), path)
            daemon.acquire_owner()
            assert tuple(daemon.journal.db.execute('SELECT * FROM api_principals').fetchone()) == credential
            assert api_auth.authenticate(daemon.journal.db, token, daemon._admin_token).actor == 'fixture-operator'
            server = await asyncio.start_server(daemon._handle, '127.0.0.1', port)
            reply({'commit': switched['installed']['commit'], 'credential_unchanged': True})
    finally:
        await close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir', type=Path, required=True)
    asyncio.run(serve(parser.parse_args().data_dir))
