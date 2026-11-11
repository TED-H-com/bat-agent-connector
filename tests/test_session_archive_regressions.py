"""Archive pagination regressions use synthetic BAT and disk-backed journals."""
from __future__ import annotations

import asyncio

import pytest

from bat_agent_connector import session_archive
from bat_agent_connector.client import BatClient
from bat_agent_connector.operations import OperationError
from tests import test_session_archive as archive

daemon = archive.daemon
ready = archive.ready
SID = archive.SID


def all_message_ids(d):
    page = archive.read(d, last_n=100)
    snapshot_id = page['snapshot_id']
    result = [m['id'] for m in page['messages']]
    while page['next_offset'] is not None:
        page = archive.read(d, snapshot_id=snapshot_id, offset=page['next_offset'], last_n=100)
        result = [m['id'] for m in page['messages']] + result
    return result


def source_cursor(d):
    key = ('h1', SID, d.session_archive.identity('h1', SID)[0])
    return d.session_archive.source(key)['cursor']


@pytest.mark.parametrize('older', [False, True])
@pytest.mark.parametrize(('previous', 'incoming', 'expected'), [
    (['A', 'B'], ['B', 'C'], ['A', 'B', 'C']),
    (['A', 'D'], ['B', 'C', 'D'], ['A', 'B', 'C', 'D']),
    (['A', 'D'], ['A', 'B', 'C'], ['A', 'B', 'C', 'D']),
])
def test_overlapping_windows_keep_native_chronology(older, previous, incoming, expected):
    members = [[mid, f'old-{mid}'] for mid in previous]
    rows = [{'id': mid, 'revision': f'new-{mid}'} for mid in incoming]
    result = session_archive.merge_members(members, rows, older=older)
    assert [mid for mid, _ in result] == expected
    assert len(result) == len(set(expected))
    assert all(dict(result)[mid] == f'new-{mid}' for mid in incoming)


async def test_large_append_during_backfill_eventually_retains_every_message(ready, mock):
    d = ready
    mock.states[SID]['messages'] = []
    mock.archives[SID] = [archive.msg(i) for i in range(1000)]
    await d.session_archive.capture('h1', SID)
    assert archive.read(d)['coverage']['reason'] == 'backfill_pending'
    initial = archive.read(d)

    # More messages arrive than fit in either the archive head or live window.
    mock.archives[SID].extend(archive.msg(i) for i in range(1000, 1500))
    for _ in range(9):
        await d.session_archive.capture('h1', SID)

    assert all_message_ids(d) == [f'msg-{i}' for i in range(1500)]
    assert archive.count(d, 'session_archive_messages') == 1500
    assert archive.read(d)['coverage']['source_end_observed']
    assert archive.read(d)['coverage']['reason'] is None
    assert archive.read(d, snapshot_id=initial['snapshot_id'])['messages'] == initial['messages']


async def test_completed_archive_poll_does_not_restart_backfill(ready, mock):
    d = ready
    mock.states[SID]['messages'] = []
    mock.archives[SID] = [archive.msg(i) for i in range(405)]
    for _ in range(3):
        await d.session_archive.capture('h1', SID)
    before = archive.read(d)
    assert before['coverage']['source_end_observed']
    snapshots = archive.count(d, 'session_archive_snapshots')

    for _ in range(5):
        mock.invokes.clear()
        await d.session_archive.capture('h1', SID)
        after = archive.read(d)
        assert after['snapshot_id'] == before['snapshot_id']
        assert after['coverage']['source_end_observed']
        assert after['coverage']['reason'] is None
        pages = [f['params'] for f in mock.invokes if f['channel'] == 'claude:load-archived']
        # Head and previous anchor checks are allowed; walking the middle is not.
        assert not any(p['offset'] == 200 for p in pages)
        assert len(pages) <= 2
        assert archive.count(d, 'session_archive_snapshots') == snapshots


async def test_live_capture_survives_archive_failure_without_losing_backfill_position(ready, mock):
    d = ready
    mock.states[SID]['messages'] = []
    mock.archives[SID] = [archive.msg(i) for i in range(405)]
    await d.session_archive.capture('h1', SID)
    cursor = source_cursor(d)
    before = archive.read(d)
    mock.states[SID]['messages'] = [archive.msg(5000, 'live text survives archive error')]

    def unavailable(_):
        raise RuntimeError('synthetic archive unavailable')

    mock.handlers['claude:load-archived'] = unavailable
    await d.session_archive.capture('h1', SID)
    after = archive.read(d)
    assert source_cursor(d) == cursor
    assert after['messages'][-1]['id'] == 'msg-5000'
    assert after['messages'][-1]['text'] == 'live text survives archive error'
    assert after['coverage']['reason'] == 'archive_unconfirmed'
    assert not after['coverage']['source_end_observed']
    assert archive.count(d, 'session_archive_messages') == 201
    assert archive.read(d, snapshot_id=before['snapshot_id'])['messages'] == before['messages']

    del mock.handlers['claude:load-archived']
    for _ in range(2):
        await d.session_archive.capture('h1', SID)
    assert all_message_ids(d) == [f'msg-{i}' for i in range(405)] + ['msg-5000']


@pytest.mark.parametrize('bad_page', [
    {'messages': [], 'total': 405, 'hasMore': False},
    {'messages': [archive.msg(i) for i in range(205, 405)], 'total': 405, 'hasMore': False},
    {'messages': [archive.msg(i) for i in range(388, 405)], 'total': 405, 'hasMore': True},
])
async def test_contradictory_page_does_not_advance_successful_cursor(ready, mock, bad_page):
    d = ready
    mock.states[SID]['messages'] = []
    mock.archives[SID] = [archive.msg(i) for i in range(405)]
    await d.session_archive.capture('h1', SID)
    cursor = source_cursor(d)
    count = archive.count(d, 'session_archive_messages')
    mock.handlers['claude:load-archived'] = lambda _: bad_page

    await d.session_archive.capture('h1', SID)

    assert source_cursor(d) == cursor
    assert archive.count(d, 'session_archive_messages') == count
    assert not archive.read(d)['coverage']['source_end_observed']


async def test_stalled_live_state_does_not_block_healthy_archive(ready, mock, monkeypatch):
    d = ready
    mock.archives[SID] = [archive.msg(1, 'healthy archive while live is stalled')]
    entered, cancelled = asyncio.Event(), asyncio.Event()
    invoke = BatClient.invoke

    async def stalled_live(client, channel, params=None, **kwargs):
        if channel == 'claude:get-session-state' and (params or {}).get('sessionId') == SID:
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()
        return await invoke(client, channel, params, **kwargs)

    # Stall only this request, allowing independent archive replies instead of
    # blocking every channel in MockBat's serial dispatch loop.
    monkeypatch.setattr(BatClient, 'invoke', stalled_live)
    monkeypatch.setattr(session_archive, 'READ_TIMEOUT', 1.2)
    mock.invokes.clear()
    await asyncio.wait_for(d.session_archive.capture('h1', SID), 3)

    assert entered.is_set() and cancelled.is_set()
    assert any(frame['channel'] == 'claude:load-archived' for frame in mock.invokes)
    assert [row['text'] for row in archive.read(d)['messages']] == ['healthy archive while live is stalled']
    assert not archive.api.write_frames(mock)


async def test_unloaded_runtime_native_identity_retains_snapshots_but_changed_identity_fences(ready, mock):
    d = ready
    terminal = next(t for t in mock.ws_doc['terminals'] if t['id'] == SID)
    terminal.pop('sdkSessionId', None)
    mock.metas[SID]['sdkSessionId'] = 'native-known-only-from-runtime'
    mock.states[SID]['messages'] = [archive.msg(1, 'first native conversation')]
    mock.archives[SID] = []
    await d.inventory.refresh_host('h1')
    await d.session_archive.capture('h1', SID)
    before = archive.read(d)

    mock.metas[SID] = None
    await d.inventory.refresh_host('h1')
    assert archive.read(d, snapshot_id=before['snapshot_id'])['messages'] == before['messages']
    await d.session_archive.capture('h1', SID)
    assert archive.read(d)['messages'] == before['messages']

    mock.metas[SID] = {'cwd': '/srv/demo', 'sdkSessionId': 'actually-different-native', 'isStreaming': False}
    mock.states[SID]['messages'] = [archive.msg(2, 'second native conversation')]
    await d.inventory.refresh_host('h1')
    with pytest.raises(OperationError):
        archive.read(d, snapshot_id=before['snapshot_id'])
    await d.session_archive.capture('h1', SID)
    assert [row['text'] for row in archive.read(d)['messages']] == ['second native conversation']
    with pytest.raises(OperationError):
        archive.read(d, snapshot_id=before['snapshot_id'])


async def test_successful_new_head_survives_older_page_failure_without_cursor_advance(ready, mock):
    d = ready
    mock.states[SID]['messages'] = []
    mock.archives[SID] = [archive.msg(i) for i in range(405)]
    await d.session_archive.capture('h1', SID)
    cursor = source_cursor(d)
    mock.archives[SID].append(archive.msg(405, 'new head survives older page failure'))

    def older_page_unavailable(params):
        offset, limit = params['offset'], params['limit']
        if offset >= 200:
            raise RuntimeError('synthetic older archive page failure')
        records = mock.archives[SID]
        end = max(0, len(records) - offset)
        start = max(0, end - limit)
        return {'messages': records[start:end], 'total': len(records), 'hasMore': start > 0}

    mock.handlers['claude:load-archived'] = older_page_unavailable
    await d.session_archive.capture('h1', SID)
    after = archive.read(d)
    assert source_cursor(d) == cursor
    assert after['messages'][-1]['id'] == 'msg-405'
    assert after['coverage']['reason'] == 'archive_unconfirmed'
    assert not after['coverage']['source_end_observed']
    assert all_message_ids(d) == [f'msg-{i}' for i in range(205, 406)]

    del mock.handlers['claude:load-archived']
    for _ in range(2):
        await d.session_archive.capture('h1', SID)
    assert all_message_ids(d) == [f'msg-{i}' for i in range(406)]
