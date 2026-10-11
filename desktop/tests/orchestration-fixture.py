"""Real central orchestration UI fixture; all effects stay inside MockBat and temp state."""
from __future__ import annotations

import asyncio
import contextlib
import copy
import json
import os
import sys
import tempfile
from pathlib import Path

from bat_agent_connector import api_auth, platform_files, registry
from bat_agent_connector.errors import InvokeTimeout
from bat_agent_connector.task_daemon import TaskDaemon
from tests.conftest import adopt, controller_scope, make_config
from tests.mockbat import TOKEN, MockBat
from tests.test_lifecycle import _add_wt_claude

RELAY = 'sess-codex-0002'
SOURCE = 'wt-claude-0007'
WRITES = {'worktree:create', 'claude:start-session', 'claude:send-message', 'claude:stop-session', 'worktree:remove', 'workspace:save'}


async def main():
    with tempfile.TemporaryDirectory(prefix='batc-orchestration-ui-') as temporary:
        root = Path(temporary)
        platform_files.ensure_private_directory(root / "state")
        platform_files.ensure_private_directory(root / "config")
        os.environ.update(BATC_CONFIG_DIR=str(root / 'config'), BATC_STATE_DIR=str(root / 'state'), BATC_TEST_TOKEN=TOKEN)
        os.environ.pop('BATC_DEVICE_ID', None)
        mock = MockBat()
        await mock.start()
        mock.handlers['git:log'] = lambda params: [{'hash': 'a' * 40}]
        mock.states[RELAY]['isStreaming'] = mock.metas[RELAY]['isStreaming'] = False
        config = make_config(mock, writes=True, orchestrate=True, managed_roots=['/srv'], orchestrate_max_sessions=16,
                             safety={'write_min_interval_s': 0})
        daemon = TaskDaemon(config, root / 'journal.db')
        token = api_auth.issue(daemon.journal.db, 'orchestration-browser', ['observe', 'start', 'operate'])
        principal = api_auth.authenticate(daemon.journal.db, token, daemon._admin_token)
        with controller_scope(daemon.journal, principal):
            adopt(RELAY, agent_preset='codex-agent', workspace_id='ws-1')
            _add_wt_claude(mock)
        manual_before = copy.deepcopy(mock.metas['sess-claude-0001'])
        tabs_before = copy.deepcopy(mock.ws_doc)
        await daemon.inventory.refresh_host('h1')
        server = await asyncio.start_server(daemon._handle, '127.0.0.1', 0)
        worker = asyncio.create_task(daemon.ops.loop(0.05))
        print(json.dumps({'port': server.sockets[0].getsockname()[1], 'token': token, 'relay': RELAY, 'source': SOURCE}), flush=True)
        try:
            while line := await asyncio.to_thread(sys.stdin.readline):
                command = json.loads(line)
                action = command['action']
                if action == 'stop':
                    break
                if action == 'diagnostic':
                    print(json.dumps({'operations': [{k: daemon.ops.get(op['operation_id']).get(k)
                                                      for k in ('operation_id', 'action', 'status', 'status_reason', 'steps')}
                                                     for op in daemon.ops.list()['operations']],
                                      'channels': mock.channels()}), flush=True)
                    continue
                if action == 'writes':
                    config.host('h1').writes = command['enabled']
                    print(json.dumps({'writes': command['enabled']}), flush=True)
                elif action == 'lose-next-send':
                    client = daemon.fleet.client('h1')
                    original = client.invoke
                    lost = False
                    async def lose(channel, *args, _original=original, **kwargs):
                        nonlocal lost
                        result = await _original(channel, *args, **kwargs)
                        if channel == 'claude:send-message' and not lost:
                            lost = True
                            raise InvokeTimeout('fixture sent frame with lost ACK')
                        return result
                    client.invoke = lose
                    print(json.dumps({'lose_next': True}), flush=True)
                elif action == 'verify':
                    op = daemon.ops.get(command['operation_id'])
                    assert op['actor'] == 'orchestration-browser'
                    assert op['action'] == command['operation_action'], op
                    assert op['status'] == command['status'], op
                    frames = [f for f in mock.invokes if f['channel'] in WRITES]
                    assert len(frames) == command['frames'], frames
                    assert mock.metas['sess-claude-0001'] == manual_before
                    assert mock.ws_doc == tabs_before
                    if op['action'] == 'session.relay':
                        assert op['target'] == {'host': 'h1', 'session_id': RELAY}
                        assert op['params']['start_if_missing'] is False and op['result']['sent'] is True
                        child = daemon.ops.get(op['result']['child_operation_id'])
                        assert child['actor'] == op['actor'] and child['status'] == 'succeeded'
                        assert op['params']['message'] in child['params']['text']
                    elif op['action'] == 'fanout.plan':
                        child = daemon.ops.get(op['external_refs']['fanout_result']['started'][0]['operation_id'])
                        sid = child['result']['session_id']
                        assert registry.get('h1', sid)['role'] == 'planner'
                        assert child['params']['agent'] == 'codex' and child['result']['prompt_sent'] is True
                    elif op['action'] == 'session.failover':
                        assert op['result']['old_session_id'] == SOURCE and op['result']['prompt_sent'] is True
                        assert registry.get('h1', SOURCE)['status'] == 'superseded'
                    else:
                        result = op['external_refs']['fanout_result']
                        assert len(result['started']) == 1, result
                        child = daemon.ops.get(result['started'][0]['operation_id'])
                        assert child['status'] == 'uncertain'
                        assert child['params']['prompt'] == 'Literal item one.'
                        assert all(s['status'] != 'succeeded' for s in child['steps'] if s['name'] == 'send')
                        assert len([s for s in op['steps'] if s['name'].startswith('fanout.child.')]) == 1
                    print(json.dumps({'verified': True}), flush=True)
                else:
                    raise ValueError('Unknown fixture command')
        finally:
            worker.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await worker
            server.close()
            await server.wait_closed()
            await daemon.artifact_store.close_reaper()
            await daemon.inventory.close()
            await daemon.fleet.close()
            daemon.journal.close()
            await mock.stop()


if __name__ == '__main__':
    asyncio.run(main())
