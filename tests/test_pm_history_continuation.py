"""Every preview/descriptor case fails on 84c68e1c: the module and read are absent.

Compatibility assertions about existing receipts/capabilities are regression checks,
not claimed baseline failures. All providers and transcripts below are synthetic.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
from dataclasses import replace
from types import SimpleNamespace

import pytest

from bat_agent_connector import mcp_server
from bat_agent_connector import pm_external_history as refs
from bat_agent_connector import pm_history_continuation as continuation
from bat_agent_connector.api_auth import Principal
from bat_agent_connector.errors import InvokeTimeout
from bat_agent_connector.github import GitHubAmbiguous
from bat_agent_connector.operations import OperationError
from bat_agent_connector.session_history_config import HistoryConfig, HistorySource
from tests.test_api_v1 import daemon, http, served, token  # noqa: F401
from tests.test_external_history import AT, SESSION, append, message
from tests.test_pm_run_api import unpack
from tests.test_pm_work_start import OWNER, _seed_plan_and_work, _setup_full_pm_world

TEXT = ' \tA🧪e\u0301\r\nZ SHA ' + 'f' * 40 + ' checkpoint:cp_' + 'f' * 32 + ' /foreign/checkout '
SHA = 'c' * 40


@pytest.fixture(params=['codex', 'claude'])
async def world(daemon, tmp_path, monkeypatch, request):  # noqa: F811
    d = daemon
    ops, wid = _setup_full_pm_world(tmp_path, ops=d.ops)
    ctx = _seed_plan_and_work(ops, wid, bind_work=False)
    frames, git, provider = [], [], []
    state = {'sha': SHA, 'repository_id': 42, 'folder': str(tmp_path / 'foreign'), 'hook': None}
    (tmp_path / 'foreign').mkdir()
    subprocess.run(['git', 'init', '-q', str(tmp_path / 'foreign')], check=True)
    (tmp_path / 'foreign' / 'manual.txt').write_text('Manual and foreign resources are read only.')

    async def invoke(channel, params):
        frames.append((channel, params))
        assert channel == 'workspace:load', 'Preview sent a non-allowlisted BAT frame'
        if state.get('failure') == 'bat':
            raise InvokeTimeout('Synthetic read timeout')
        return {'workspaces': [{'id': 'w1', 'folderPath': state['folder']}],
            'sessions': [{'id': 'manual-session', 'cwd': state['folder']}]}

    async def repository(repository):
        provider.append(('repository', repository))
        if state['hook']:
            state['hook']()
        if state.get('failure') == 'github':
            raise GitHubAmbiguous('Synthetic read failure')
        return 200, {'id': state['repository_id'], 'full_name': 'o/r'}

    async def branch_ref(repository, branch):
        provider.append(('branch_ref', repository, branch))
        return 200, {'ref': 'refs/heads/main', 'object': {'type': 'commit', 'sha': state['sha']}}

    async def git_run(*args, **kwargs):
        git.append((args, kwargs))
        pytest.fail('Preview must never invoke Git')

    client = SimpleNamespace(invoke=invoke, host=SimpleNamespace(profile_id='prof-1'))
    ops.context['inventory'] = SimpleNamespace(fleet=SimpleNamespace(client=lambda host: client))
    ops.context['github'] = SimpleNamespace(repository=repository, branch_ref=branch_ref)
    ops.context['git_runner'].run = git_run
    source = HistorySource('synthetic-history', wid, request.param + '-history-jsonl-v1',
        tmp_path / 'history.jsonl', SESSION, (OWNER.actor,))
    if request.param == 'codex':
        records = [{'type': 'session_meta', 'payload': {'id': SESSION}}, message(TEXT)]
    else:
        records = [{'type': 'assistant', 'uuid': 'message-1', 'timestamp': AT, 'sessionId': SESSION,
            'message': {'role': 'assistant', 'content': [{'type': 'text', 'text': TEXT}]}}]
    append(source.path, *records)
    d.history_collector.config = HistoryConfig(10, (source,))
    monkeypatch.setattr(d.journal, 'owner_valid', lambda: True)
    await d.history_collector.collect_once()
    entry = ops.db.execute('SELECT * FROM session_history_entries WHERE source_id=?', (source.source_id,)).fetchone()
    selected = refs.preview(ops.db, OWNER, wid, source.source_id, entry['entry_id'], entry['content_digest'], {'kind': 'entry'})
    body = {'context_ref': selected['ref'], 'code_target': ctx['params']['code_target'], 'expected_source_sha': SHA,
        'project_id': ctx['project_id'], 'requirement_ids': ['req-login.1'],
        'new_work': {'title': 'Continue selected context', 'goal': 'Implement login', 'acceptance': 'Tests pass'},
        'instruction': 'Implement the selected requirements.', 'agent': 'claude', 'delivery': 'reviewable_changes'}
    return SimpleNamespace(d=d, ops=ops, wid=wid, source=source, entry=entry, body=body, state=state,
        frames=frames, git=git, provider=provider, selected=selected, records=records, root=tmp_path, ctx=ctx)


def snapshot(w):
    # Main and TEMP row counts, all journal content, and scratch files. No SQL mutation
    # is permitted even when it happens to leave the final row count unchanged.
    counts = {}
    for schema in ('main', 'temp'):
        for row in w.ops.db.execute(f"SELECT name FROM {schema}.sqlite_master WHERE type='table'"):
            name = row[0].replace('"', '""')
            counts[(schema, name)] = w.ops.db.execute(f'SELECT COUNT(*) FROM {schema}."{name}"').fetchone()[0]
    files = {str(p.relative_to(w.root)): p.read_bytes() for p in (w.root / 'foreign').rglob('*') if p.is_file()}
    return counts, w.ops.db.total_changes, files, list(w.ops.db.iterdump()), w.ops.db.execute('PRAGMA user_version').fetchone()[0]


async def call(w, *, body=None, principal=OWNER, code=None):
    before = snapshot(w)
    start = len(w.frames)
    try:
        if code:
            with pytest.raises(OperationError) as exc:
                await continuation.preview(w.ops, principal, w.wid, body if body is not None else w.body)
            assert exc.value.code == code, exc.value
            assert ' / ' in exc.value.message
            return exc.value
        return await continuation.preview(w.ops, principal, w.wid, body if body is not None else w.body)
    finally:
        assert snapshot(w) == before
        assert not w.git
        assert all(frame[0] == 'workspace:load' for frame in w.frames[start:])
        assert len(w.frames) - start <= 1


async def test_fixed_descriptor_and_exact_readonly_basis(world):
    w = world
    out = await call(w)
    assert out['excerpt']['parts'][0]['text'] == TEXT
    assert out['descriptor']['basis']['source_sha'] == SHA
    assert out['descriptor']['association'] == 'requester_selected_context_and_basis'
    assert out['producer_attribution'] == 'unproven'
    assert out['descriptor_digest'] == hashlib.sha256(json.dumps(out['descriptor'], ensure_ascii=False,
        sort_keys=True, allow_nan=False, separators=(',', ':')).encode('utf-8')).hexdigest()
    assert out['association'] == 'requester_selected_context_and_basis'
    assert set(out['meaning']['message']) == {'en', 'zh-TW'}
    assert out['notice']['code'] == 'PM_EXTERNAL_CONTINUATION_NOT_AVAILABLE'
    assert out['readiness']['status'] == 'ready'
    assert w.frames == [('workspace:load', {'profileId': 'prof-1'})]
    before = snapshot(w)
    assert (await continuation.resolve(w.ops, OWNER, w.wid, out['descriptor']))['descriptor'] == out['descriptor']
    assert snapshot(w) == before
    assert w.frames[-1] == w.frames[0]
    from bat_agent_connector import pm_external_history_links
    assert pm_external_history_links.capabilities()['derive_ready'] is False


@pytest.mark.parametrize('selection', [
    {'kind': 'entry'}, {'kind': 'text', 'part_index': 0, 'start_codepoint': 3, 'end_codepoint': 9}])
async def test_unicode_selection_and_append_do_not_retarget(world, selection):
    w = world
    fixed = refs.preview(w.ops.db, OWNER, w.wid, w.source.source_id, w.entry['entry_id'],
        w.entry['content_digest'], selection)
    w.body['context_ref'] = fixed['ref']
    out = await call(w)
    assert out['excerpt'] == fixed['content']
    append(w.source.path, *w.records)
    await w.d.history_collector.collect_once()
    assert await call(w) == out


@pytest.mark.parametrize('field,value', [('schema', 'checkpoint/v1'), ('server_id', 'other'),
    ('workspace_id', 'ws_' + 'b' * 32), ('source_id', 'other'), ('entry_id', 'shi_' + 'a' * 32),
    ('source_digest', 'a' * 64), ('content_digest', 'a' * 64), ('excerpt_digest', 'a' * 64),
    ('selection', {'kind': 'entry', 'path': '/foreign/checkout'})])
async def test_bad_reference_never_reaches_basis(world, field, value):
    w = world
    w.body['context_ref'][field] = value
    before = snapshot(w)
    with pytest.raises(OperationError):
        await continuation.preview(w.ops, OWNER, w.wid, w.body)
    assert snapshot(w) == before
    assert not w.frames and not w.provider and not w.git


@pytest.mark.parametrize('field', ['cwd', 'path', 'url', 'native_session_id', 'credentials', 'checkpoint_id', 'producer_attribution'])
@pytest.mark.parametrize('location', ['body', 'target', 'work'])
async def test_extra_input_fields_are_refused(world, field, location):
    target = {'body': world.body, 'target': world.body['code_target'], 'work': world.body['new_work']}[location]
    target[field] = 'synthetic'
    await call(world, code='PM_EXTERNAL_CONTINUATION_UNSUPPORTED')
    assert not world.provider


@pytest.mark.parametrize('case', ['observe', 'membership', 'grant', 'admin-without-grant', 'removed', 'rebound', 'inactive', 'unreadable'])
async def test_authority_and_hidden_sources_fail_before_basis(world, case):
    w = world
    principal = OWNER
    if case == 'observe':
        principal = replace(OWNER, scopes=frozenset({'manage'}))
    elif case == 'membership':
        w.ops.db.execute('DELETE FROM pm_workspace_members WHERE workspace_id=? AND actor=?', (w.wid, OWNER.actor))
    elif case == 'admin-without-grant':
        principal = Principal('local-admin', frozenset(), admin=True)
    elif case == 'grant':
        w.d.history_collector.config = HistoryConfig(10, (replace(w.source, readers=('other',)),))
    elif case == 'removed':
        w.d.history_collector.config = HistoryConfig(10, ())
    elif case == 'rebound':
        w.d.history_collector.config = HistoryConfig(10, (replace(w.source, path=w.source.path.with_suffix('.other')),))
    elif case == 'inactive':
        w.ops.db.execute('DELETE FROM temp.session_history_grant_state')
    elif case == 'unreadable':
        w.source.path.unlink()
        await w.d.history_collector.collect_once()
    w.body['code_target']['repository'] = 'secret/not-bound'
    await call(w, principal=principal, code='FORBIDDEN' if case == 'observe' else 'PM_HISTORY_SOURCE_UNAVAILABLE')
    assert not w.frames and not w.provider


@pytest.mark.parametrize('regrant', [False, True])
@pytest.mark.parametrize('provider_failure', [False, True])
async def test_revocation_during_provider_read_fences_reply(world, regrant, provider_failure):
    w = world
    def revoke():
        w.d.history_collector.config = HistoryConfig(10, ())
        if regrant:
            w.d.history_collector.config = HistoryConfig(10, (w.source,))
    w.state['hook'] = revoke
    w.state['failure'] = 'github' if provider_failure else None
    before = snapshot(w)
    # Revocation itself changes TEMP grant rows; isolate those authorized fixture
    # changes from preview's SQL using an authorizer around the preview-only spans.
    mutations = []
    def trace(sql):
        if sql.lstrip().upper().startswith(('INSERT', 'UPDATE', 'DELETE')):
            mutations.append(sql)
    def traced_revoke():
        w.ops.db.set_trace_callback(None)
        revoke()
        w.ops.db.set_trace_callback(trace)
    w.state['hook'] = traced_revoke
    w.ops.db.set_trace_callback(trace)
    try:
        with pytest.raises(OperationError, match='PM_HISTORY_SOURCE_UNAVAILABLE'):
            await continuation.preview(w.ops, OWNER, w.wid, w.body)
    finally:
        w.ops.db.set_trace_callback(None)
    assert not mutations and not w.git
    after = snapshot(w)
    assert before[2:] == after[2:]
    assert {k: v for k, v in before[0].items() if k[0] == 'main'} == {k: v for k, v in after[0].items() if k[0] == 'main'}
    assert w.frames == [('workspace:load', {'profileId': 'prof-1'})]


@pytest.mark.parametrize('case,code', [('head', 'REPOSITORY_REF_CHANGED'), ('unbound', 'REPOSITORY_NOT_BOUND'),
    ('missing-target', 'PM_EXTERNAL_CONTINUATION_UNSUPPORTED'), ('missing-project-binding', 'PM_PROJECT_BINDING_REQUIRED')])
async def test_published_basis_and_project_refusals(world, case, code):
    w = world
    if case == 'head':
        w.state['sha'] = 'd' * 40
    elif case == 'unbound':
        w.body['code_target']['host'] = 'other'
    elif case == 'missing-target':
        del w.body['code_target']['host']
    else:
        w.body['project_id'] = 'prj_' + 'f' * 20
    await call(w, code=code)


@pytest.mark.parametrize('kind', ['tool', 'empty', 'oversized'])
async def test_unusable_retained_context(world, kind):
    w = world
    entry = json.loads(w.entry['content'])
    entry['parts'] = [{'kind': 'text', 'text': '' if kind == 'empty' else 'x' * 16001}] if kind != 'tool' else [{'kind': 'tool_request', 'name': 'Read'}]
    entry['entry_id'] = 'shi_' + 'f' * 32
    digest = refs._digest({k: v for k, v in entry.items() if k != 'source_offset'})
    w.ops.db.execute('INSERT INTO session_history_entries VALUES(?,?,?,?,?)',
        (2, w.source.source_id, entry['entry_id'], json.dumps(entry), digest))
    w.body['context_ref']['content_digest'] = digest
    w.body['context_ref']['entry_id'] = entry['entry_id']
    if kind != 'oversized':
        w.body['context_ref'] = refs.preview(w.ops.db, OWNER, w.wid, w.source.source_id, entry['entry_id'], digest, {'kind': 'entry'})['ref']
    await call(w, code='PM_HISTORY_REFERENCE_INVALID' if kind == 'oversized' else 'PM_EXTERNAL_CONTEXT_UNUSABLE')


@pytest.mark.parametrize('case', ['schema', 'extra', 'association', 'repository_id', 'sha', 'binding', 'context-extra'])
async def test_strict_descriptor_validation_and_reproof(world, case):
    w = world
    descriptor = (await call(w))['descriptor']
    if case == 'schema':
        descriptor['schema'] = 'external_history_published/v2'
    elif case == 'extra':
        descriptor['producer_attribution'] = 'proven'
    elif case == 'association':
        descriptor['association'] = 'verified_result'
    elif case == 'repository_id':
        descriptor['basis']['repository_id'] = True
    elif case == 'sha':
        descriptor['basis']['source_sha'] = 'D' * 40
    elif case == 'binding':
        descriptor['basis']['binding_digest'] = 'a' * 64
    else:
        descriptor['context_ref']['native_session_id'] = SESSION
    before = snapshot(w)
    with pytest.raises(OperationError):
        await continuation.resolve(w.ops, OWNER, w.wid, descriptor)
    assert snapshot(w) == before and not w.git


async def test_readiness_does_not_grant_authority_or_change_existing_receipt_bytes(world):
    w = world
    before = [tuple(row) for row in w.ops.db.execute('SELECT * FROM operations ORDER BY operation_id')]
    principal = replace(OWNER, scopes=frozenset({'observe'}))
    out = await call(w, principal=principal)
    assert out['readiness']['reasons'][0]['code'] == 'PM_EXTERNAL_CONTINUATION_OWNER_REQUIRED'
    assert json.dumps(before, ensure_ascii=False).encode() == json.dumps(
        [tuple(row) for row in w.ops.db.execute('SELECT * FROM operations ORDER BY operation_id')], ensure_ascii=False).encode()


async def test_http_rpc_mcp_parity_and_refusal(world, served, monkeypatch):  # noqa: F811
    w = world
    _, port = served
    auth = token(w.d, OWNER.actor, *OWNER.scopes)
    monkeypatch.setenv('BATC_TASK_URL', f'http://127.0.0.1:{port}/rpc')
    monkeypatch.setenv('BATC_API_TOKEN', auth)
    server, fleet = mcp_server.build_server(w.d.fleet.config, read_only=False, principal_only=True)
    path = f'/api/v1/pm/workspaces/{w.wid}/history-continuation-preview'
    try:
        before = snapshot(w)
        expected = await call(w)
        status, result = await http(port, 'POST', path, tok=auth, body=w.body)
        assert status == 200, result
        assert result == expected
        assert await w.d.call_api('pm_history_continuation_preview', {'workspace_id': w.wid, 'body': w.body}, OWNER) == expected
        assert unpack(await server.call_tool('pm_history_continuation_preview', {'workspace_id': w.wid, 'body': w.body})) == expected
        assert snapshot(w) == before
        assert all(frame[0] == 'workspace:load' for frame in w.frames)
        assert not w.git
        w.body['expected_source_sha'] = 'a' * 40
        status, result = await http(port, 'POST', path, tok=auth, body=w.body)
        assert status == 409 and result['error']['code'] == 'REPOSITORY_REF_CHANGED', result
        for invoke in (w.d.call_api, server.call_tool):
            with pytest.raises(Exception, match='REPOSITORY_REF_CHANGED'):
                if invoke == w.d.call_api:
                    await invoke('pm_history_continuation_preview', {'workspace_id': w.wid, 'body': w.body}, OWNER)
                else:
                    await invoke('pm_history_continuation_preview', {'workspace_id': w.wid, 'body': w.body})
        assert (await http(port, 'POST', path + '?actor=other', tok=auth, body=w.body))[0] == 422
        assert (await http(port, 'GET', path, tok=auth))[0] in (404, 405)
    finally:
        await fleet.close()


@pytest.mark.parametrize('change,code', [('sha', 'REPOSITORY_REF_CHANGED'), ('folder', 'REPOSITORY_BINDING_CHANGED'),
    ('repository_id', 'REPOSITORY_BINDING_CHANGED')])
async def test_fixed_descriptor_never_follows_moved_head_or_workspace(world, change, code):
    w = world
    fixed = (await call(w))['descriptor']
    original = refs.canonical(fixed)
    w.state[change] = {'sha': 'e' * 40, 'folder': str(w.root / 'different'), 'repository_id': 43}[change]
    before = snapshot(w)
    with pytest.raises(OperationError, match=code):
        await continuation.resolve(w.ops, OWNER, w.wid, fixed)
    assert refs.canonical(fixed) == original
    assert snapshot(w) == before and not w.git
    assert all(c == 'workspace:load' for c, _ in w.frames)


@pytest.mark.parametrize('kind', ['fork', 'subagent'])
async def test_unproven_producer_identity_never_becomes_context(world, kind):
    w = world
    source = replace(w.source, source_id='unproven', path=w.root / 'unproven.jsonl')
    if source.adapter.startswith('codex'):
        extra = {'forked_from_id': SESSION} if kind == 'fork' else {'source': {'subagent': 'review'}}
        records = [{'type': 'session_meta', 'payload': {'id': SESSION, **extra}}, message(TEXT)]
    else:
        records = [{**w.records[0], 'isSidechain': True}]
    append(source.path, *records)
    w.d.history_collector.config = HistoryConfig(10, (source,))
    await w.d.history_collector.collect_once()
    from bat_agent_connector import session_history
    assert session_history.read(w.ops, OWNER, w.wid, source.source_id)['source']['readable'] is False
    w.body['context_ref']['source_id'] = source.source_id
    await call(w, code='PM_HISTORY_SOURCE_UNAVAILABLE')
    assert not w.provider


async def test_administrator_membership_is_not_a_reader_grant(world):
    principal = replace(OWNER, admin=True)
    world.d.history_collector.config = HistoryConfig(10, (replace(world.source, readers=('other',)),))
    await call(world, principal=principal, code='PM_HISTORY_SOURCE_UNAVAILABLE')


async def test_lost_preview_reply_repeats_identical_proof_without_receipt(world):
    first = await call(world)
    assert await call(world) == first
    assert len(world.frames) == 2


def test_shared_native_wire_validation_corpus():
    # All acceptance/rejection cases fail on 84c68e1c (validator absent).
    from pathlib import Path
    corpus = json.loads((Path(__file__).parent / 'fixtures/history-continuation-preview.json').read_text())
    for case in corpus['cases']:
        body = json.loads(json.dumps(corpus['body']))
        target = body
        for key in case['path'][:-1]:
            target = target[key]
        value = json.loads(case['encoded_value']) if 'encoded_value' in case else case['value']
        if 'repeat' in case:
            value *= case['repeat']
        if case['path']:
            target[case['path'][-1]] = value
        else:
            body = value
        if case['valid']:
            continuation.validate_preview_request(body)
        else:
            with pytest.raises(OperationError, match=case.get('python_code', 'PM_EXTERNAL_CONTINUATION_UNSUPPORTED')):
                continuation.validate_preview_request(body)


@pytest.mark.parametrize('transport', ['bat', 'github'])
async def test_unavailable_read_transport_is_bilingual_and_has_no_effect(world, transport):
    world.state['failure'] = transport
    await call(world, code='REPOSITORY_SOURCE_UNAVAILABLE')
    if transport == 'bat':
        assert not world.provider


async def test_ambiguous_bindings_require_an_explicit_target(world):
    from bat_agent_connector import repository_sync
    w = world
    fleet = w.ops.context['fleet']
    fleet.config.hosts['h2'] = SimpleNamespace(**{**vars(fleet.config.hosts['h1']), 'name': 'h2'})
    fleet.config.github.repos['o/r'].sync.bindings = (('h1', 'w1'), ('h2', 'w1'))
    fleet.orchestrate_enabled = lambda host: host in fleet.config.hosts
    w.ops.context['git_runner'].available = fleet.orchestrate_enabled
    assert len(repository_sync.capabilities(w.ops)) == 2
    del w.body['code_target']['host']
    await call(w, code='PM_EXTERNAL_CONTINUATION_UNSUPPORTED')
    assert not w.frames and not w.provider


@pytest.mark.parametrize('missing', ['observe', 'membership', 'reader_grant'])
async def test_access_refusal_parity_precedes_basis_details(world, served, monkeypatch, missing):  # noqa: F811
    w = world
    _, port = served
    principal = replace(OWNER, scopes=frozenset({'manage', 'start'})) if missing == 'observe' else OWNER
    auth = token(w.d, principal.actor, *principal.scopes)
    if missing == 'membership':
        w.ops.db.execute('DELETE FROM pm_workspace_members WHERE workspace_id=? AND actor=?', (w.wid, OWNER.actor))
    elif missing == 'reader_grant':
        w.d.history_collector.config = HistoryConfig(10, (replace(w.source, readers=('other',)),))
    w.body['code_target']['repository'] = 'hidden/not-bound'
    monkeypatch.setenv('BATC_TASK_URL', f'http://127.0.0.1:{port}/rpc')
    monkeypatch.setenv('BATC_API_TOKEN', auth)
    server, fleet = mcp_server.build_server(w.d.fleet.config, read_only=False, principal_only=True)
    try:
        before = snapshot(w)
        code = 'FORBIDDEN' if missing == 'observe' else 'PM_HISTORY_SOURCE_UNAVAILABLE'
        path = f'/api/v1/pm/workspaces/{w.wid}/history-continuation-preview'
        status, result = await http(port, 'POST', path, tok=auth, body=w.body)
        assert status == (403 if missing == 'observe' else 404), result
        assert result['error']['code'] == code
        with pytest.raises(OperationError) as error:
            await w.d.call_api('pm_history_continuation_preview', {'workspace_id': w.wid, 'body': w.body}, principal)
        assert result['error'] == {'code': error.value.code, 'message': error.value.message}
        with pytest.raises(Exception) as mcp_error:
            await server.call_tool('pm_history_continuation_preview', {'workspace_id': w.wid, 'body': w.body})
        assert code in str(mcp_error.value)
        for message in continuation.reason(code)['message'].values():
            assert message in str(mcp_error.value)
        assert snapshot(w) == before
        assert not w.frames and not w.provider and not w.git
    finally:
        await fleet.close()
