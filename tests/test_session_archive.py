"""Durable history uses synthetic BAT and disk SQLite; no CLI/model/live writes."""
from __future__ import annotations

import asyncio
from dataclasses import replace

import pytest

from bat_agent_connector import api_auth, session_archive
from bat_agent_connector.operations import OperationError
from tests import test_api_v1 as api
from tests.test_session_observation import rpc

daemon = api.daemon
served = api.served
SID = 'sess-claude-0001'
READER = api_auth.Principal('reader', frozenset({'observe'}))


@pytest.fixture
async def ready(daemon, monkeypatch):
    monkeypatch.setattr(daemon.journal, 'owner_valid', lambda: True)
    await daemon.inventory.refresh_host('h1')
    yield daemon
    await daemon.inventory.close()


def msg(n, text=None, role='assistant'):
    return {'id': f'msg-{n}', 'role': role, 'content': text or f'message {n}', 'timestamp': 1790000000000}


def read(d, **kw):
    return d.session_archive.read(READER, 'h1', SID, **kw)


def count(d, table):
    return d.journal.db.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]


async def test_background_capture_restart_and_offline_retained_read(ready, mock, monkeypatch):
    d = ready
    mock.states[SID]['messages'] = [msg(1, '```python\nprint(1)\n```', 'user'), msg(2, 'Proposed; not adopted')]
    monkeypatch.setattr(session_archive, 'INTERVAL', .01)
    task = asyncio.create_task(d.session_archive.loop())
    try:
        async def captured():
            while not count(d, 'session_archive_snapshots'):
                await asyncio.sleep(.005)
        await asyncio.wait_for(captured(), 5)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    before = read(d)
    assert '```python' in str(before['messages'])
    assert before['execution_status'] == 'unknown'
    assert not before['coverage']['history_complete']
    assert not api.write_frames(mock) and not count(d, 'operations')
    # Recreate collector against durable rows, with no source calls on retained reads.
    d.session_archive = session_archive.SessionArchive(d)
    mock.frames.clear()
    mock.archives.clear()
    mock.states.clear()
    mock.ws_doc['terminals'] = []
    assert read(d)['messages'] == before['messages']
    assert not mock.frames
    d.session_archive.failure(('h1', SID, d.session_archive.identity('h1', SID)[0]))
    assert read(d)['source_status'] == 'source_unavailable'
    assert read(d)['messages'] == before['messages']


async def test_replay_deduplicates_bodies_and_changed_revision_preserves_snapshot(ready, mock):
    d = ready
    mock.archives[SID] = []
    mock.states[SID]['messages'] = [msg(1, 'original', 'user'), msg(2, 'old reply')]
    await d.session_archive.capture('h1', SID)
    old = read(d)
    await d.session_archive.capture('h1', SID)
    assert count(d, 'session_archive_messages') == 2
    assert count(d, 'session_archive_snapshots') == 1
    mock.states[SID]['messages'][1]['content'] = 'revised reply'
    await d.session_archive.capture('h1', SID)
    assert count(d, 'session_archive_messages') == 3
    assert count(d, 'session_archive_snapshots') == 2
    assert read(d, snapshot_id=old['snapshot_id'])['messages'] == old['messages']
    latest = read(d)
    assert latest['messages'][-1]['text'] == 'revised reply'
    assert latest['brief']['source_snapshot'] == latest['snapshot_id']
    for item in latest['brief']['items']:
        m = next(m for m in latest['messages'] if m['id'] == item['message_id'])
        assert item['revision'] == m['revision']
        assert m['text'].startswith(item['text'])
        assert session_archive.digest({k:v for k,v in m.items() if k != 'revision'}) == item['revision']


async def test_backfill_beyond_3000_same_timestamp_and_append_preserves_order(ready, mock):
    d = ready
    mock.states[SID]['messages'] = []
    mock.archives[SID] = [msg(i) for i in range(3505)]
    await d.session_archive.capture('h1', SID)
    assert read(d)['coverage']['reason'] == 'backfill_pending'
    # Append shifts backwards offsets. The original anchor must still match.
    mock.archives[SID].append(msg(3505))
    for _ in range(18):
        await d.session_archive.capture('h1', SID)
    out = read(d)
    assert count(d, 'session_archive_messages') == 3506
    seen = []
    page = out
    while True:
        seen = [m['id'] for m in page['messages']] + seen
        if page['next_offset'] is None:
            break
        page = read(d, snapshot_id=out['snapshot_id'], offset=page['next_offset'], last_n=100)
    assert seen == [f'msg-{i}' for i in range(3506)]


async def test_gone_session_backfills_without_unsafe_state_or_resume(ready, mock):
    d = ready
    mock.states[SID]['messages'] = []
    mock.archives[SID] = [msg(1, 'historical')]
    mock.metas.pop(SID)
    d.journal.db.execute('UPDATE sessions_observed SET gone_at=1 WHERE host=? AND session_id=?', ('h1', SID))
    mock.frames.clear()
    await d.session_archive.collect_host('h1')
    assert read(d)['messages'][0]['text'] == 'historical'
    assert not any(f['channel'] == 'claude:get-session-state' and f.get('params', {}).get('sessionId') == SID for f in mock.invokes)
    assert not api.write_frames(mock)


async def test_transaction_failure_does_not_advance_cursor_or_save_partial_rows(ready, mock):
    d = ready
    mock.states[SID]['messages'] = []
    mock.archives[SID] = [msg(i) for i in range(405)]
    await d.session_archive.capture('h1', SID)
    key = ('h1', SID, d.session_archive.identity('h1', SID)[0])
    cursor = d.session_archive.source(key)['cursor']
    d.journal.db.execute("CREATE TRIGGER fail_archive BEFORE INSERT ON session_archive_snapshots BEGIN SELECT RAISE(ABORT,'fixture'); END")
    await d.session_archive.capture('h1', SID)
    assert d.session_archive.source(key)['cursor'] == cursor
    assert count(d, 'session_archive_messages') == 200
    d.journal.db.execute('DROP TRIGGER fail_archive')
    await d.session_archive.capture('h1', SID)
    assert count(d, 'session_archive_messages') == 400


async def test_replaced_archive_anchor_restarts_without_deleting_retained_history(ready, mock):
    d = ready
    mock.states[SID]['messages'] = []
    mock.archives[SID] = [msg(i) for i in range(405)]
    await d.session_archive.capture('h1', SID)
    old = read(d)
    mock.archives[SID] = [msg(900, 'replacement')]
    await d.session_archive.capture('h1', SID)
    assert count(d, 'session_archive_messages') == 201
    assert read(d, snapshot_id=old['snapshot_id'])['messages'] == old['messages']


async def test_host_retarget_refuses_before_frames_or_commit(ready, mock):
    d = ready
    await d.session_archive.capture('h1', SID)
    old = count(d, 'session_archive_messages')
    d.inventory.config.hosts['h1'] = replace(d.inventory.config.host('h1'), profile_id='another')
    mock.frames.clear()
    with pytest.raises(OperationError):
        await d.session_archive.capture('h1', SID)
    with pytest.raises(OperationError):
        read(d)
    assert not mock.frames and count(d, 'session_archive_messages') == old


async def test_native_generation_change_does_not_mix_conversations(ready, mock):
    d = ready
    await d.session_archive.capture('h1', SID)
    old = read(d)
    mock.metas[SID]['sdkSessionId'] = 'different-native-session'
    mock.states[SID]['messages'] = [msg(99, 'new generation')]
    mock.archives[SID] = []
    await d.session_archive.capture('h1', SID)
    assert read(d)['source_status'] == 'source_changed'
    await d.inventory.refresh_host('h1')
    await d.session_archive.capture('h1', SID)
    assert [m['text'] for m in read(d)['messages']] == ['new generation']
    with pytest.raises(OperationError):
        read(d, snapshot_id=old['snapshot_id'])


async def test_lease_loss_and_cancellation_leave_cursor_unchanged(ready, mock, monkeypatch):
    d = ready
    entered, release = asyncio.Event(), asyncio.Event()
    async def waiting(_):
        entered.set()
        await release.wait()
        return {'messages':[msg(1)], 'total':1, 'hasMore':False}
    mock.handlers['claude:load-archived'] = waiting
    task = asyncio.create_task(d.session_archive.capture('h1', SID))
    await asyncio.wait_for(entered.wait(), 5)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    assert not count(d, 'session_archive_messages')
    release.set()
    monkeypatch.setattr(d.journal, 'owner_valid', lambda: False)
    await d.session_archive.capture('h1', SID)
    assert not count(d, 'session_archive_sources')


async def test_storage_limit_keeps_old_data_and_cursor(ready, mock, monkeypatch):
    d = ready
    await d.session_archive.capture('h1', SID)
    old = read(d)
    key = ('h1', SID, d.session_archive.identity('h1', SID)[0])
    cursor = d.session_archive.source(key)['cursor']
    monkeypatch.setattr(session_archive, 'MAX_SESSION_BYTES', 1)
    mock.states[SID]['messages'].append(msg(999))
    await d.session_archive.capture('h1', SID)
    assert read(d)['messages'] == old['messages']
    assert read(d)['source_status'] == 'storage_limit'
    assert d.session_archive.source(key)['cursor'] == cursor


@pytest.mark.parametrize('surface', ['http', 'rpc'])
async def test_retained_transport_scope_and_pinned_pagination(ready, served, mock, surface):
    d, port = served
    await d.session_archive.capture('h1', SID)
    tok = api.token(d, 'reader', 'observe')
    mock.frames.clear()
    if surface == 'http':
        code, out = await api.http(port, 'GET', f'/api/v1/sessions/h1/{SID}/messages?source=retained&last_n=2', tok=tok)
    else:
        code, response = await rpc(port, tok, host='h1', session_id=SID, source='retained', last_n=2)
        out = response['result']
    assert code == 200 and out['source'] == 'retained' and len(out['messages']) == 2
    assert not mock.frames
    refused, _ = await api.http(port, 'GET', f'/api/v1/sessions/h1/{SID}/messages?source=retained', tok=api.token(d, 'no-observe', 'operate'))
    assert refused == 403
    bad, _ = await api.http(port, 'GET', f'/api/v1/sessions/h1/{SID}/messages?source=retained&after=123', tok=tok)
    assert bad == 422


async def test_clipping_and_missing_ids_are_explicit_and_not_fabricated(ready, mock):
    d = ready
    mock.archives[SID] = []
    mock.states[SID]['messages'] = [msg(1, 'x' * 20000), {'role':'user', 'content':'no identity'}]
    await d.session_archive.capture('h1', SID)
    out = read(d, max_chars=500, max_message_chars=60000)
    assert len(out['messages']) == 1 and len(out['messages'][0]['text']) == 500
    assert out['messages'][0]['clipped']
    assert out['coverage']['omitted_messages'] == 1
    assert out['coverage']['clipped_messages'] == 1


@pytest.mark.parametrize('change', ['owner', 'binding'])
async def test_inflight_source_or_owner_change_prevents_commit(ready, mock, monkeypatch, change):
    d = ready
    entered, release = asyncio.Event(), asyncio.Event()
    async def waiting(_):
        entered.set()
        await release.wait()
        return {'messages':[msg(1)], 'total':1, 'hasMore':False}
    mock.handlers['claude:load-archived'] = waiting
    task = asyncio.create_task(d.session_archive.capture('h1', SID))
    await asyncio.wait_for(entered.wait(), 5)
    if change == 'owner':
        monkeypatch.setattr(d.journal, 'owner_valid', lambda: False)
    else:
        d.inventory.config.hosts['h1'] = replace(d.inventory.config.host('h1'), profile_id='retargeted')
    release.set()
    await task
    assert not count(d, 'session_archive_messages')
    assert not count(d, 'session_archive_sources')


async def test_saved_brief_and_body_recheck_current_source_lineage(ready, mock):
    from bat_agent_connector import pm_source_lineage, pm_workspace_adapter
    from tests.test_work_items import PERSON, act
    d = ready
    await d.session_archive.capture('h1', SID)
    saved = read(d)
    owner = api_auth.Principal(PERSON.actor, PERSON.scopes | {'start', 'operate'})
    workspace = (await act(d, owner, 'pm.workspace.create', params={'name':'Saved source'}))['result']['workspace']
    wid = workspace['workspace_id']
    original = await act(d, owner, pm_workspace_adapter.ACTION, {'workspace_id':wid},
        {'adapter':pm_workspace_adapter.ADAPTERS[0], 'rationale':'Existing source policy'},
        pm_workspace_adapter.read(d.ops, owner, wid)['preconditions'])
    with d.journal.tx():
        d.journal.db.execute('INSERT INTO pm_source_lineage VALUES(?,?,?,?)',
            ('session', 'h1/'+SID, original['operation_id'], original['operation_id']))
    assert pm_source_lineage.visible(d.journal.db, owner, 'session', 'h1/'+SID)
    assert d.session_archive.read(owner, 'h1', SID)['brief'] == saved['brief']
    # A previously readable fixed snapshot is not a capability after source access changes.
    with pytest.raises(OperationError) as denied:
        read(d, snapshot_id=saved['snapshot_id'])
    assert denied.value.code == 'NOT_FOUND'
    assert count(d, 'session_archive_snapshots') >= 1


async def test_retained_observation_revalidates_credential_and_shutdown(ready):
    d = ready
    await d.session_archive.capture('h1', SID)
    calls = []
    def revoked_at_return():
        calls.append(True)
        return len(calls) == 1
    with pytest.raises(OperationError) as denied:
        await d.api.session_observation.request(READER, 'session_read', {'host':'h1', 'session_id':SID, 'source':'retained'},
                                    check_authorization=revoked_at_return)
    assert denied.value.code == 'FORBIDDEN' and len(calls) == 2
    await d.api.session_observation.close()
    with pytest.raises(OperationError) as closed:
        await d.api.session_observation.request(READER, 'session_read', {'host':'h1', 'session_id':SID, 'source':'retained'})
    assert closed.value.code == 'OBSERVATION_CLOSED'


def test_normalization_only_retains_public_text_and_skips_malformed_timestamps():
    rows, omitted = session_archive.normalize([
        {**msg(1), 'content':{'type':'thinking', 'text':'private thought'}},
        {**msg(2), 'content':[{'type':'reasoning', 'text':'private thought'},
                            {'type':'tool_result', 'text':'private tool data'},
                            {'type':'text', 'text':'public response'}]},
        {**msg(3), 'timestamp':float('inf')},
        msg(4, 'safe'),
    ])
    assert [m['text'] for m in rows] == ['public response', 'safe']
    assert omitted == 1
