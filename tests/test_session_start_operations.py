"""Standalone start through the real central operation service and MockBat."""
import pytest

from bat_agent_connector import api_auth, artifact_managed, registry
from bat_agent_connector import session_start_operations as starts
from tests import test_api_v1 as api
from tests.operation_helpers import settle_operations
from tests.test_api_v1 import http, token, write_frames

daemon = api.daemon
served = api.served

START = api_auth.Principal('starter', frozenset({'start'}))
SHA = 'a' * 40

@pytest.fixture(autouse=True)
def fixed_git(mock):
    mock.handlers['git:log'] = lambda p: [{'hash': SHA}]

async def create(d, *, params=None, key='start-1', target=None):
    op, _ = d.ops.create(START, action='session.start', target=target or {'host': 'h1', 'workspace': 'demo-project'},
                        params=params or {}, idempotency_key=key)
    await settle_operations(d.ops)
    return d.ops.get(op['operation_id'])

async def test_start_prompt_and_literal_replay(daemon, mock):
    prompt = '  Original words\nwith whitespace.  '
    out = await create(daemon, params={'prompt': prompt})
    assert out['status'] == 'succeeded', (out['error_code'], out['status_reason'], out['steps'])
    result = out['result']
    assert result['started'] is True and result['prompt_sent'] is True
    frame = next(x for x in mock.invokes if x['channel'] == 'claude:send-message')
    assert frame['params']['prompt'] == prompt
    before = len(write_frames(mock))
    mock.ws_doc['workspaces'] = []
    replay = await create(daemon, params={'prompt': prompt})
    assert replay['operation_id'] == out['operation_id'] and len(write_frames(mock)) == before
    row = registry.get('h1', result['session_id'])
    proof = artifact_managed.lineage(daemon.ops, 'h1', result['session_id'], row,
                                    {'execution_operation_id': out['operation_id']})
    assert proof['execution_operation_id'] == out['operation_id']

@pytest.mark.parametrize('params', [{'agent': []}, {'agent': 'goose'}, {'prompt': ''}, {'prompt': []},
    {'title': {}}, {'use_worktree': 1}, {'model': False}, {'task_id': 'client-task'}, {'session_id': 'reserved'},
    {'cwd_override': '/srv/manual'}, {'register_tab': True}, {'base_branch': 'main'}])
async def test_invalid_admission_has_no_effect(daemon, mock, params):
    from bat_agent_connector.operations import OperationError
    with pytest.raises(OperationError):
        await create(daemon, params=params)
    assert daemon.ops.list()['operations'] == [] and not write_frames(mock) and not registry.list_entries()

async def test_http_scope_key_discovery_and_start(served, mock):
    d, port = served
    reader, starter = token(d, 'reader', 'observe'), token(d, 'starter', 'start')
    status, found = await http(port, 'GET', '/api/v1/workspaces?host=h1&limit=1', tok=reader)
    assert status == 200 and len(found['workspaces']) == 1
    assert (await http(port, 'GET', '/api/v1/workspaces', tok=starter))[0] == 403
    body = {'action': 'session.start', 'target': {'host': 'h1', 'workspace': 'ws-1'}, 'params': {}}
    assert (await http(port, 'POST', '/api/v1/operations', tok=starter, body=body))[0] == 422
    body['idempotency_key'] = 'http-start'
    assert (await http(port, 'POST', '/api/v1/operations', tok=reader, body=body))[0] == 403
    status, out = await http(port, 'POST', '/api/v1/operations?wait=3', tok=starter, body=body)
    assert status in (200, 201, 202), out
    await settle_operations(d.ops)
    assert d.ops.list()['operations'][0]['status'] == 'succeeded'
    assert mock.channels().count('claude:start-session') == 1

async def test_legacy_no_key_is_independent_and_reserved_prefix_refused(daemon, mock):
    from bat_agent_connector.operations import NO_KEY_PREFIX, OperationError
    request = {'host': 'h1', 'workspace': 'ws-1', 'confirm': True}
    a = await starts.legacy(daemon.ops, START, request, entry='rpc')
    b = await starts.legacy(daemon.ops, START, request, entry='rpc')
    assert a['operation_status'] == b['operation_status'] == 'succeeded'
    assert a['session_id'] != b['session_id'] and a['idempotency_key'] is None and not a['idempotency_enabled']
    with pytest.raises(OperationError, match='reserved'):
        await starts.legacy(daemon.ops, START, {**request, 'idempotency_key': NO_KEY_PREFIX + 'x'}, entry='rpc')
    assert mock.channels().count('claude:start-session') == 2

async def test_cap_and_prompt_free_lineage_refused(daemon, mock):
    from bat_agent_connector.operations import OperationError
    first = await create(daemon)
    await create(daemon, key='second')
    third = await create(daemon, key='third')
    assert third['status'] == 'failed' and mock.channels().count('claude:start-session') == 2
    with pytest.raises(OperationError) as refused:
        artifact_managed.lineage(daemon.ops, 'h1', first['result']['session_id'],
            registry.get('h1', first['result']['session_id']), {'execution_operation_id': first['operation_id']})
    assert refused.value.code == 'ARTIFACT_LINEAGE_UNPROVEN'

@pytest.mark.parametrize('phase', ['worktree:create', 'claude:start-session', 'claude:send-message'])
@pytest.mark.parametrize('agent', ['claude', 'codex'])
async def test_lost_ack_readback_never_resends(daemon, mock, monkeypatch, phase, agent):
    from bat_agent_connector.errors import InvokeTimeout
    mock.echo_sends = True
    client = daemon.fleet.client('h1')
    original = client.invoke
    dropped = False
    async def lost(channel, *args, **kwargs):
        nonlocal dropped
        result = await original(channel, *args, **kwargs)
        if channel == phase and not dropped:
            dropped = True
            raise InvokeTimeout('fixture reply lost after actual frame')
        return result
    monkeypatch.setattr(client, 'invoke', lost)
    out = await create(daemon, params={'agent': agent, 'prompt': 'original'})
    assert out['status'] == 'uncertain', (out['status_reason'], out['steps'])
    if phase == 'claude:send-message':
        assert out['external_refs']['start_result']['started'] is True
    daemon.ops.db.execute('UPDATE operations SET next_run_at=0 WHERE operation_id=?', (out['operation_id'],))
    await settle_operations(daemon.ops)
    after = daemon.ops.get(out['operation_id'])
    assert after['status'] == ('uncertain' if phase == 'claude:send-message' and agent == 'codex' else 'succeeded'), after
    assert mock.channels().count(phase) == 1

@pytest.mark.parametrize('phase', ['worktree:create', 'claude:start-session', 'claude:send-message'])
async def test_intent_only_restart_does_not_resend(daemon, mock, monkeypatch, phase):
    from bat_agent_connector.operations import AmbiguousOutcome
    original = starts._invoke
    async def crash(ctx, plan, name, client, channel, *args, **kwargs):
        if channel == phase:
            raise AmbiguousOutcome('process stopped after intent before any transport evidence')
        return await original(ctx, plan, name, client, channel, *args, **kwargs)
    monkeypatch.setattr(starts, '_invoke', crash)
    out = await create(daemon, params={'prompt': 'once'})
    assert out['status'] == 'uncertain'
    monkeypatch.setattr(starts, '_invoke', original)
    daemon.ops.db.execute('UPDATE operations SET next_run_at=0 WHERE operation_id=?', (out['operation_id'],))
    await settle_operations(daemon.ops)
    assert daemon.ops.get(out['operation_id'])['status'] == 'uncertain'
    assert phase not in mock.channels()

@pytest.mark.parametrize('error', ['confinement', 'connection'])
async def test_positive_unsent_start_retains_carrier_and_releases_capacity(daemon, mock, monkeypatch, error):
    from bat_agent_connector import confinement
    from bat_agent_connector.errors import ConnectionLost
    async def refuse(*args, **kwargs):
        if error == 'connection':
            raise ConnectionLost('no start frame')
        raise confinement.ConfinementRefused('HOST_ACCOUNT_UNVERIFIED', 'pre-frame', sent=False)
    monkeypatch.setattr(confinement, 'guard_start_frame', refuse)
    out = await create(daemon)
    assert out['status'] == 'failed', out
    row = registry.list_entries('h1')[0]
    assert row['start_sent'] is False and row['status'] == 'failed'
    assert row['worktree_path'] == row['cwd'] == mock.worktrees[row['session_id']]['worktreePath']
    assert 'claude:start-session' not in mock.channels() and 'worktree:remove' not in mock.channels()
    before = len(write_frames(mock))
    assert (await create(daemon))['operation_id'] == out['operation_id']
    assert len(write_frames(mock)) == before

@pytest.mark.parametrize('managed', [False, True])
async def test_new_live_consumer_after_create_is_never_removed(daemon, mock, monkeypatch, managed):
    from bat_agent_connector import confinement
    async def refuse(*a, **kw):
        row = registry.list_entries('h1')[0]
        path = row['worktree_path']
        mock.ws_doc['terminals'].append({'id': 'new-consumer', 'cwd': path, 'workspaceId': 'ws-1'})
        mock.metas['new-consumer'] = {'cwd': path, 'isStreaming': True}
        if managed:
            registry.reserve('h1', {'session_id': 'new-consumer', 'cwd': path, 'worktree_path': path,
                'branch': row['branch'], 'shares_worktree_with': row['session_id'], 'agent_preset': 'claude'}, 5)
            registry.update('h1', 'new-consumer', status='active')
        raise confinement.ConfinementRefused('HOST_ACCOUNT_UNVERIFIED', 'pre-frame', sent=False)
    monkeypatch.setattr(confinement, 'guard_start_frame', refuse)
    out = await create(daemon)
    assert out['status'] == 'failed' and out['error_code'] == 'HOST_ACCOUNT_UNVERIFIED'
    row = registry.get('h1', out['external_refs']['session_id'])
    assert row['status'] == 'failed' and row['worktree_path'] == mock.metas['new-consumer']['cwd']
    assert mock.metas['new-consumer']['isStreaming'] is True
    assert row['session_id'] in mock.worktrees
    assert 'worktree:remove' not in mock.channels() and 'claude:start-session' not in mock.channels()

@pytest.mark.parametrize('boundary', ['carrier', 'start', 'prompt'])
@pytest.mark.parametrize('change', ['task', 'workspace', 'git', 'registry'])
async def test_final_frame_binding_guards(daemon, mock, monkeypatch, boundary, change):
    client, tripped = daemon.fleet.client('h1'), False
    original = client._invoke_checked
    selected = {'carrier': 'worktree:create', 'start': 'claude:start-session', 'prompt': 'claude:send-message'}[boundary]
    async def race(channel, params, *args, **kwargs):
        nonlocal tripped
        if channel == selected and not tripped:
            tripped = True
            sid = params['sessionId']
            if change == 'task':
                registry.update('h1', sid, task_id='later-task', role='lead')
            elif change == 'workspace':
                mock.ws_doc['workspaces'][0]['folderPath'] = '/srv/changed'
            elif change == 'git':
                mock.handlers['git:log'] = lambda p: [{'hash': 'b' * 40}]
            else:
                registry.update('h1', sid, start_operation_id='another-operation')
        return await original(channel, params, *args, **kwargs)
    monkeypatch.setattr(client, '_invoke_checked', race)
    out = await create(daemon, params={'prompt': 'do not reroute'})
    assert out['status'] in {'failed', 'needs_attention'}, out
    assert selected not in mock.channels()

async def test_max_in_flight_one_has_no_nested_read_deadlock(daemon, mock):
    import asyncio
    daemon.fleet.config.host('h1').orchestrate_register_tabs = True
    client = daemon.fleet.client('h1')
    client._sem = asyncio.Semaphore(1)
    out = await asyncio.wait_for(create(daemon, params={'prompt': 'original'}), 5)
    assert out['status'] == 'succeeded', out

async def test_created_commit_drift_prevents_start(daemon, mock):
    mock.handlers['git:log'] = lambda p: [{'hash': ('b' if '.bat-worktrees' in p['cwd'] else 'a') * 40}]
    out = await create(daemon)
    assert out['status'] == 'failed' and out['error_code'] == 'START_GIT_CHANGED'
    row = registry.list_entries('h1')[0]
    assert row['status'] == 'failed' and row['worktree_path'] == mock.worktrees[row['session_id']]['worktreePath']
    assert 'claude:start-session' not in mock.channels() and 'worktree:remove' not in mock.channels()

@pytest.mark.parametrize('changed', [{'cwd': '/srv/later', 'worktree_path': '/srv/later'}, {'branch': 'later-branch'}])
async def test_failure_retention_does_not_overwrite_rebound_carrier(daemon, mock, monkeypatch, changed):
    from bat_agent_connector import confinement
    async def refuse(*args, **kwargs):
        row = registry.list_entries('h1')[0]
        registry.update('h1', row['session_id'], **changed)
        raise confinement.ConfinementRefused('HOST_ACCOUNT_UNVERIFIED', 'pre-frame', sent=False)
    monkeypatch.setattr(confinement, 'guard_start_frame', refuse)
    out = await create(daemon)
    assert out['status'] == 'failed'
    row = registry.list_entries('h1')[0]
    assert row['status'] == 'starting' and all(row[k] == value for k, value in changed.items())
    assert 'claude:start-session' not in mock.channels() and 'worktree:remove' not in mock.channels()

async def test_manual_no_worktree_is_never_written(daemon, mock):
    mock.ws_doc['workspaces'][0]['folderPath'] = '/home/person/repo'
    from bat_agent_connector.operations import OperationError
    with pytest.raises(OperationError) as refused:
        await create(daemon, params={'use_worktree': False})
    assert refused.value.code == 'START_WORKTREE_REQUIRED' and not write_frames(mock)
    assert not daemon.ops.list()['operations']

async def test_mcp_principal_profile_start_uses_own_token(served, mock, monkeypatch):
    from bat_agent_connector.mcp_server import build_server
    from tests.test_mcp_principal import call
    d, port = served
    monkeypatch.setenv('BATC_TASK_URL', f'http://127.0.0.1:{port}/rpc')
    monkeypatch.setenv('BATC_API_TOKEN', token(d, 'start-only-agent', 'start'))
    server, fleet = build_server(d.fleet.config, principal_only=True)
    try:
        out = await call(server, 'session_start', {'host': 'h1', 'workspace': 'ws-1', 'confirm': True,
                                                'prompt': 'unchanged', 'idempotency_key': 'mcp-start'})
        assert 'succeeded' in out and d.ops.list()['operations'][0]['actor'] == 'start-only-agent'
        monkeypatch.delenv('BATC_API_TOKEN')
        assert 'BATC_API_TOKEN' in await call(server, 'session_start', {'host': 'h1', 'workspace': 'ws-1', 'confirm': True})
        assert mock.channels().count('claude:start-session') == 1
    finally:
        await fleet.close()

async def test_tab_registration_exact_receipt_and_no_repeated_save(daemon, mock):
    daemon.fleet.config.host('h1').orchestrate_register_tabs = True
    before = list(mock.ws_doc['terminals'])
    out = await create(daemon, params={'title': 'An exact title', 'prompt': 'words'})
    assert out['status'] == 'succeeded', out
    assert out['result']['tab']['appended'] is True
    assert mock.ws_doc['terminals'][:-1] == before
    assert mock.channels().count('workspace:save') == 1
    again = await create(daemon, params={'title': 'An exact title', 'prompt': 'words'})
    assert again['operation_id'] == out['operation_id'] and mock.channels().count('workspace:save') == 1

async def test_lost_tab_ack_reconciles_without_second_save(daemon, mock, monkeypatch):
    from bat_agent_connector.errors import InvokeTimeout
    daemon.fleet.config.host('h1').orchestrate_register_tabs = True
    client = daemon.fleet.client('h1')
    original = client._invoke_checked
    async def lost(channel, *args, **kwargs):
        out = await original(channel, *args, **kwargs)
        if channel == 'workspace:save':
            raise InvokeTimeout('saved, reply lost')
        return out
    monkeypatch.setattr(client, '_invoke_checked', lost)
    out = await create(daemon, params={'prompt': 'after registration'})
    assert out['status'] == 'uncertain' and 'claude:send-message' not in mock.channels()
    daemon.ops.db.execute('UPDATE operations SET next_run_at=0 WHERE operation_id=?', (out['operation_id'],))
    await settle_operations(daemon.ops)
    assert daemon.ops.get(out['operation_id'])['status'] == 'succeeded'
    assert mock.channels().count('workspace:save') == mock.channels().count('claude:send-message') == 1

async def test_default_rate_limit_does_not_block_own_initial_prompt(daemon, mock):
    daemon.fleet.config.safety.write_min_interval_s = 60
    assert (await create(daemon, params={'prompt': 'first'}))['status'] == 'succeeded'

async def test_confirmed_start_unknown_prompt_legacy_projection(daemon, mock, monkeypatch):
    from bat_agent_connector.errors import InvokeTimeout
    client = daemon.fleet.client('h1')
    original = client.invoke
    async def lost(channel, *args, **kwargs):
        out = await original(channel, *args, **kwargs)
        if channel == 'claude:send-message':
            raise InvokeTimeout('unknown prompt')
        return out
    monkeypatch.setattr(client, 'invoke', lost)
    out = await starts.legacy(daemon.ops, START, {'host': 'h1', 'workspace': 'ws-1', 'confirm': True,
        'agent': 'codex', 'prompt': 'original', 'idempotency_key': 'partial'}, entry='rpc')
    assert out['started'] is True and out['prompt_sent'] is None and out['operation_status'] == 'uncertain'
    assert out['session_id'] == registry.list_entries('h1')[0]['session_id']

@pytest.mark.parametrize('new_content', [None, 'committed', 'uncommitted'])
async def test_real_temporary_git_fixes_commit_and_preserves_source(daemon, mock, tmp_path, monkeypatch, new_content):
    import subprocess

    from tests.test_checkpoints import git, snapshot
    repo = tmp_path / 'source'
    repo.mkdir()
    subprocess.run(['git', 'init', '--template=', '-q', '-b', 'main', str(repo)], check=True)
    git(repo, 'config', 'user.email', 'fixture@example.invalid')
    git(repo, 'config', 'user.name', 'Fixture')
    (repo / 'file.txt').write_text('original bytes\n')
    git(repo, 'add', 'file.txt')
    git(repo, 'commit', '-qm', 'initial')
    head = git(repo, 'rev-parse', 'HEAD')
    from bat_agent_connector import managed_setup
    daemon.fleet.config.host('h1').managed_roots = [str(tmp_path)]
    managed_setup._activate(daemon, daemon.fleet.config, {})
    mock.ws_doc['workspaces'][0]['folderPath'] = str(repo)
    before = snapshot(repo)
    mock.handlers['git:log'] = lambda p: [{'hash': git(p['cwd'], 'rev-parse', 'HEAD')}]
    mock.handlers['git:branch'] = lambda p: git(p['cwd'], 'branch', '--show-current')
    mock.handlers['git:getRoot'] = lambda p: git(p['cwd'], 'rev-parse', '--show-toplevel')
    def worktree(p):
        path, branch = repo / '.bat-worktrees' / p['sessionId'], 'bat/' + p['sessionId']
        git(repo, 'worktree', 'add', '-b', branch, str(path), p['baseBranch'])
        result = {'success': True, 'worktreePath': str(path), 'branchName': branch, 'sourceBranch': p['baseBranch']}
        mock.worktrees[p['sessionId']] = result
        if new_content:
            (path / 'result.txt').write_text('new external result\n')
            if new_content == 'committed':
                git(path, 'add', 'result.txt')
                git(path, 'commit', '-qm', 'new result')
        return result
    mock.handlers['worktree:create'] = worktree
    if new_content == 'uncommitted':
        from bat_agent_connector import confinement
        async def refuse(*args, **kwargs):
            raise confinement.ConfinementRefused('HOST_ACCOUNT_UNVERIFIED', 'pre-frame', sent=False)
        monkeypatch.setattr(confinement, 'guard_start_frame', refuse)
    out = await create(daemon, params={'prompt': 'keep these exact words'})
    after = snapshot(repo)
    # An explicitly enabled shared Git worktree adds refs/admin data; source bytes/index/HEAD remain untouched.
    assert before['index'] == after['index'] and git(repo, 'rev-parse', 'HEAD') == head
    assert (repo / 'file.txt').read_text() == 'original bytes\n'
    if new_content:
        from pathlib import Path
        assert out['status'] == 'failed'
        assert out['error_code'] == ('START_GIT_CHANGED' if new_content == 'committed' else 'HOST_ACCOUNT_UNVERIFIED')
        row = registry.get('h1', out['external_refs']['session_id'])
        assert row['status'] == 'failed' and row['worktree_path'] == row['cwd']
        assert (Path(row['cwd']) / 'result.txt').read_text() == 'new external result\n'
        if new_content == 'committed':
            assert git(row['cwd'], 'rev-parse', 'HEAD') == git(repo, 'rev-parse', row['branch']) != head
        else:
            assert 'result.txt' in git(row['cwd'], 'status', '--porcelain')
        assert 'worktree:remove' not in mock.channels() and 'claude:start-session' not in mock.channels()
    else:
        assert out['status'] == 'succeeded', out
        assert out['result']['base_commit'] == git(out['result']['cwd'], 'rev-parse', 'HEAD') == head

async def test_cli_start_adapter_preserves_prompt_identity_and_no_raw_fallback(daemon, mock, monkeypatch):
    from types import SimpleNamespace

    from bat_agent_connector import cli, task_daemon
    called = []
    def request(method, **kwargs):
        called.append((method, kwargs))
        return {'operation_status': 'succeeded', 'started': True}
    monkeypatch.setattr(task_daemon, 'request', request)
    monkeypatch.setenv('BATC_API_TOKEN', 'own-principal-token')
    args = SimpleNamespace(read_only=False, config=None, cmd='start', host='h1', workspace='ws-1', agent='codex', confirm=True,
        prompt=' original\n', model='fixed-model', no_worktree=False, title='title', key='literal')
    # _run constructs its Fleet from configuration; replace only that constructor.
    monkeypatch.setattr(cli, 'Fleet', lambda *a, **kw: daemon.fleet)
    monkeypatch.setattr(cli, 'load_config', lambda *a, **kw: daemon.fleet.config)
    result, _ = await cli._run(args)
    assert result['started'] is True and called[0][0] == 'session_start'
    assert called[0][1]['prompt'] == args.prompt and called[0][1]['idempotency_key'] == 'literal'
    assert called[0][1]['_auth_token'] == 'own-principal-token' and not write_frames(mock)

@pytest.mark.parametrize('adapter', ['cli', 'mcp'])
async def test_start_adapters_recover_original_key_after_tier_disabled(served, mock, monkeypatch, adapter):
    from types import SimpleNamespace

    from bat_agent_connector import cli
    from bat_agent_connector.errors import WriteRefused
    from bat_agent_connector.mcp_server import build_server
    from tests.test_mcp_principal import call
    d, port = served
    monkeypatch.setenv('BATC_TASK_URL', f'http://127.0.0.1:{port}/rpc')
    monkeypatch.setenv('BATC_API_TOKEN', token(d, 'adapter-starter', 'start'))
    principal = api_auth.Principal('adapter-starter', frozenset({'start'}))
    request = {'host': 'h1', 'workspace': 'ws-1', 'confirm': True, 'idempotency_key': 'original-start',
               'agent': 'claude', 'prompt': None, 'model': None, 'use_worktree': True, 'title': None}
    original = await starts.legacy(d.ops, principal, request, entry=adapter)
    assert original['operation_status'] == 'succeeded'
    before = len(write_frames(mock))
    d.fleet.config.host('h1').orchestrate = False
    monkeypatch.setattr(cli, 'load_config', lambda *a, **kw: d.fleet.config)
    if adapter == 'cli':
        args = SimpleNamespace(read_only=False, config=None, cmd='start', host='h1', workspace='ws-1',
            agent='claude', confirm=True, prompt=None, model=None, no_worktree=False, title=None, key='original-start')
        replay, _ = await cli._run(args)
        assert replay['operation_id'] == original['operation_id']
        args.key = 'fresh-start'
        with pytest.raises(ValueError) as refusal:
            await cli._run(args)
        assert 'orchestrate' in str(refusal.value).lower()
        args.confirm = False
        with pytest.raises(WriteRefused, match='confirm'):
            await cli._run(args)
    else:
        # Rebuilding after a local policy change must retain the central recovery tool.
        server, fleet = build_server(d.fleet.config)
        try:
            assert 'session_start' in {t.name for t in await server.list_tools()}
            replay = await call(server, 'session_start', request)
            assert original['operation_id'] in replay and 'succeeded' in replay
            assert 'orchestrate' in (await call(server, 'session_start', {**request, 'idempotency_key':'fresh-start'})).lower()
            assert 'confirm' in await call(server, 'session_start', {**request, 'confirm':False})
        finally:
            await fleet.close()
    assert len(d.ops.list()['operations']) == 1 and len(write_frames(mock)) == before

def test_cli_read_only_start_refuses_before_stdin_config_rpc(monkeypatch, capsys):
    from types import SimpleNamespace

    from bat_agent_connector import cli, task_daemon
    def forbidden(*a, **kw):
        pytest.fail('read-only start reached config/stdin/RPC')
    monkeypatch.setattr(cli, 'sys', SimpleNamespace(stdin=SimpleNamespace(read=forbidden), stderr=cli.sys.stderr))
    monkeypatch.setattr(cli, 'load_config', forbidden)
    monkeypatch.setattr(task_daemon, 'request', forbidden)
    assert cli.main(['--read-only', 'start', 'h1', 'ws-1', '--prompt', '-', '--confirm']) == 1
    assert '--read-only' in capsys.readouterr().err

async def test_all_receipts_replay_after_policy_and_owner_change_without_writes(daemon, mock, monkeypatch):
    from bat_agent_connector.operations import Uncertain
    action = daemon.ops.actions['session.start']
    original = action.run
    async def crash_after_receipts(ctx):
        await original(ctx)
        raise Uncertain('completion', 'process stops after all receipts before operation result')
    from dataclasses import replace
    daemon.ops.actions['session.start'] = replace(action, run=crash_after_receipts)
    out = await create(daemon, params={'prompt': 'accepted once'})
    assert out['status'] == 'uncertain' and all(step['status'] == 'succeeded' for step in out['steps'])
    sid = out['external_refs']['session_id']
    registry.update('h1', sid, task_id='later-owner', role='lead')
    daemon.fleet.config.host('h1').writes = False
    before = len(write_frames(mock))
    daemon.ops.actions['session.start'] = action
    daemon.ops.db.execute('UPDATE operations SET next_run_at=0 WHERE operation_id=?', (out['operation_id'],))
    await settle_operations(daemon.ops)
    assert daemon.ops.get(out['operation_id'])['status'] == 'succeeded'
    assert len(write_frames(mock)) == before and registry.get('h1', sid)['task_id'] == 'later-owner'

async def test_registry_projection_cas_rejects_later_owner(daemon, mock, monkeypatch):
    original = registry.project_start
    tripped = False
    def race(host, sid, **kwargs):
        nonlocal tripped
        if kwargs['fields'].get('status') == 'active' and not tripped:
            tripped = True
            registry.update(host, sid, task_id='new-owner', role='lead', status='superseded')
        return original(host, sid, **kwargs)
    monkeypatch.setattr(registry, 'project_start', race)
    out = await create(daemon, params={'prompt': 'must not send'})
    assert out['status'] == 'needs_attention'
    row = registry.list_entries('h1')[0]
    assert row['task_id'] == 'new-owner' and row['status'] == 'superseded'
    assert 'claude:send-message' not in mock.channels()

async def test_start_identity_mismatch_stays_sticky_after_metadata_changes(daemon, mock, monkeypatch):
    original = mock.dispatch
    def wrong_start(channel, params):
        result = original(channel, params)
        if channel == 'claude:start-session':
            mock.metas[params['sessionId']]['cwd'] = '/srv/wrong'
        return result
    monkeypatch.setattr(mock, 'dispatch', wrong_start)
    out = await create(daemon, params={'prompt': 'never'})
    assert out['status'] == 'needs_attention' and out['external_refs']['start_mismatch'] == 'START_SESSION_MISMATCH'
    row = registry.list_entries('h1')[0]
    mock.metas[row['session_id']]['cwd'] = row['cwd']
    daemon.ops.resume(START, out['operation_id'])
    await settle_operations(daemon.ops)
    assert daemon.ops.get(out['operation_id'])['status'] == 'needs_attention'
    assert mock.channels().count('claude:start-session') == 1 and 'claude:send-message' not in mock.channels()

@pytest.mark.parametrize('agent', ['claude', 'codex'])
async def test_tabs_use_existing_bat_schema(daemon, mock, agent):
    hc = daemon.fleet.config.host('h1')
    hc.orchestrate_register_tabs, hc.default_permission_mode = True, 'allow_all'
    out = await create(daemon, params={'agent': agent})
    assert out['status'] == 'succeeded', out
    tab = mock.ws_doc['terminals'][-1]
    assert tab['type'] == 'terminal'
    if agent == 'codex':
        assert tab['agentParams'] == {'sandboxMode': 'danger-full-access', 'approvalPolicy': 'never'}
        assert 'codexSandboxMode' not in tab
    else:
        assert tab['permissionMode'] == 'bypassPermissions'

async def test_workspace_root_rebound_before_start_is_refused(daemon, mock, monkeypatch):
    client = daemon.fleet.client('h1')
    original = client.invoke
    async def race(channel, *args, **kwargs):
        if channel == 'claude:start-session':
            mock.handlers['git:getRoot'] = lambda p: None if p['cwd'] in mock.removed_paths else '/srv/other' if p['cwd'] == '/srv/demo' else p['cwd']
        return await original(channel, *args, **kwargs)
    monkeypatch.setattr(client, 'invoke', race)
    out = await create(daemon)
    assert out['status'] == 'failed' and out['error_code'] == 'START_BINDING_CHANGED'
    assert 'claude:start-session' not in mock.channels()

async def test_tab_previous_identity_loss_stays_visible_without_repair(daemon, mock, monkeypatch):
    daemon.fleet.config.host('h1').orchestrate_register_tabs = True
    original = mock.dispatch
    def loss(channel, params):
        result = original(channel, params)
        if channel == 'workspace:save':
            mock.ws_doc['terminals'].pop(0)
        return result
    monkeypatch.setattr(mock, 'dispatch', loss)
    out = await create(daemon, params={'prompt': 'never'})
    assert out['status'] == 'needs_attention' and out['error_code'] == 'START_TAB_CONFLICT'
    daemon.ops.resume(START, out['operation_id'])
    await settle_operations(daemon.ops)
    assert daemon.ops.get(out['operation_id'])['error_code'] == 'START_TAB_CONFLICT'
    assert mock.channels().count('workspace:save') == 1 and 'claude:send-message' not in mock.channels()

@pytest.mark.parametrize('status', ['failed', 'cancelled'])
def test_cli_failed_start_returns_nonzero(monkeypatch, status):
    from bat_agent_connector import cli
    async def result(args):
        return {'operation_status': status, 'started': None}, None
    monkeypatch.setattr(cli, '_run', result)
    assert cli.main(['start', 'h1', 'ws-1', '--confirm', '--key', 'saved']) == 1

async def test_cancel_after_created_carrier_retains_it_for_reviewed_cleanup(daemon, mock, monkeypatch):
    client, original = daemon.fleet.client('h1'), daemon.fleet.client('h1').invoke
    async def cancel(channel, *args, **kwargs):
        if channel == 'claude:start-session':
            op = daemon.ops.list()['operations'][0]
            daemon.ops.cancel(START, op['operation_id'])
        return await original(channel, *args, **kwargs)
    monkeypatch.setattr(client, 'invoke', cancel)
    out = await create(daemon)
    assert out['status'] == 'cancelled', out
    row = registry.list_entries('h1')[0]
    assert row['status'] == 'failed' and row['start_sent'] is False and row['worktree_path']
    assert 'claude:start-session' not in mock.channels() and 'worktree:remove' not in mock.channels()

async def test_cancel_unknown_carrier_waits_for_proof_before_releasing_capacity(daemon, mock, monkeypatch):
    from bat_agent_connector.errors import InvokeTimeout
    client, original = daemon.fleet.client('h1'), daemon.fleet.client('h1').invoke
    async def lost(channel, *args, **kwargs):
        out = await original(channel, *args, **kwargs)
        if channel == 'worktree:create':
            raise InvokeTimeout('created, ACK lost')
        return out
    monkeypatch.setattr(client, 'invoke', lost)
    out = await create(daemon)
    daemon.ops.cancel(START, out['operation_id'])
    assert registry.list_entries('h1')[0]['status'] == 'starting'
    await settle_operations(daemon.ops)
    after = daemon.ops.get(out['operation_id'])
    assert after['status'] == 'cancelled', after
    row = registry.list_entries('h1')[0]
    assert row['status'] == 'failed' and row['worktree_path'] in [w['worktreePath'] for w in mock.worktrees.values()]
    assert mock.channels().count('worktree:create') == 1 and 'claude:start-session' not in mock.channels()

async def test_unproven_created_carrier_retains_capacity_without_start(daemon, mock):
    mock.handlers['worktree:create'] = lambda p: {'success': True, 'worktreePath': '/home/person/wrong',
        'branchName': 'wrong', 'sourceBranch': 'main'}
    out = await create(daemon)
    assert out['status'] == 'needs_attention' and out['error_code'] == 'START_CARRIER_UNPROVEN'
    assert registry.list_entries('h1')[0]['status'] == 'starting'
    assert 'claude:start-session' not in mock.channels() and 'worktree:remove' not in mock.channels()

async def test_b2_rejects_wrong_initial_send_receipt(daemon, mock):
    import json

    from bat_agent_connector.operations import OperationError
    out = await create(daemon, params={'prompt': 'execution'})
    assert out['status'] == 'succeeded'
    oid, sid = out['operation_id'], out['result']['session_id']
    response = daemon.ops.db.execute("SELECT response FROM operation_steps WHERE operation_id=? AND name='send'", (oid,)).fetchone()[0]
    response = {**json.loads(response), 'session_id': 'wrong-session'}
    daemon.ops.db.execute("UPDATE operation_steps SET response=? WHERE operation_id=? AND name='send'", (json.dumps(response), oid))
    with pytest.raises(OperationError, match='initial prompt receipt'):
        artifact_managed.lineage(daemon.ops, 'h1', sid, registry.get('h1', sid), {'execution_operation_id': oid})

async def test_no_worktree_refused_even_for_managed_folder_before_admission(daemon, mock):
    from bat_agent_connector.operations import OperationError
    with pytest.raises(OperationError) as refused:
        await starts.legacy(daemon.ops, START, {'host': 'h1', 'workspace': 'ws-1', 'confirm': True,
            'use_worktree': False, 'idempotency_key': 'unsafe-shared'}, entry='rpc')
    assert refused.value.code == 'START_WORKTREE_REQUIRED'
    assert not daemon.ops.list()['operations'] and not registry.list_entries('h1') and not write_frames(mock)
