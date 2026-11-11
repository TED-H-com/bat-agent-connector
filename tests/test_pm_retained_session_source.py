"""Pinned PM sources use disk SQLite and mocked BAT; reads emit no BAT frames."""
from __future__ import annotations

import asyncio
import copy
import json
import sqlite3
from dataclasses import replace

import pytest

from bat_agent_connector import api_auth
from bat_agent_connector import pm_retained_session_source as source
from bat_agent_connector.operations import OperationError
from tests import test_session_archive as archive

daemon, ready = archive.daemon, archive.ready
SID, READER = archive.SID, archive.READER


async def saved(d, mock, messages=None):
    mock.archives[SID] = []
    mock.states[SID]['messages'] = messages or [archive.msg(1, '原始要求', 'user'), archive.msg(2, 'Proposed; not adopted')]
    await d.session_archive.capture('h1', SID)
    return archive.read(d)['retained_session_ref']


async def test_exact_reference_preserves_old_revisions_and_full_saved_text_offline(ready, mock):
    d = ready
    ref = await saved(d, mock, [archive.msg(1, 'x' * 5000, 'user'), archive.msg(2, 'old reply')])
    before = source.resolve(d.journal.db, READER, ref)
    assert before['messages'][0]['text'] == 'x' * 5000
    assert len(archive.read(d)['messages'][0]['text']) == 2000  # Display clipping is not model source truth.
    assert before['saved_at'] == archive.read(d)['saved_at']
    assert before['execution_status'] == 'unknown' and before['coverage']['partial']
    mock.states[SID]['messages'][1]['content'] = 'changed reply'
    await d.session_archive.capture('h1', SID)
    newer = archive.read(d)['retained_session_ref']
    assert newer['snapshot_id'] != ref['snapshot_id']
    mock.states.clear()
    mock.archives.clear()
    mock.invokes.clear()
    assert source.resolve(d.journal.db, READER, ref) == before
    assert source.visible(d.journal.db, READER, ref, selection=before['selection'])
    assert source.resolve(d.journal.db, READER, newer)['messages'][-1]['text'] == 'changed reply'
    assert not mock.invokes and not archive.count(d, 'operations')


@pytest.mark.parametrize('change', ['removed', 'url', 'fingerprint', 'profile_id'])
async def test_current_configuration_revokes_without_inventory_refresh(ready, mock, change):
    d = ready
    ref = await saved(d, mock)
    if change == 'removed':
        del d.inventory.config.hosts['h1']
    else:
        value = {'url': 'wss://other.invalid', 'fingerprint': 'f' * 64, 'profile_id': 'other'}[change]
        d.inventory.config.hosts['h1'] = replace(d.inventory.config.host('h1'), **{change: value})
    mock.invokes.clear()
    assert not source.visible(d.journal.db, READER, ref)
    with pytest.raises(OperationError) as caught:
        source.resolve(d.journal.db, READER, ref)
    assert caught.value.status == 404 and not mock.invokes


async def test_current_native_generation_fences_fixed_source_but_missing_native_keeps_identity(ready, mock):
    d = ready
    ref = await saved(d, mock)
    original = json.loads(d.journal.db.execute('SELECT body FROM sessions_observed WHERE host=? AND session_id=?',
        ('h1', SID)).fetchone()[0])
    missing = {**original, 'provider_native_id': None}
    with d.journal.tx():
        d.journal.db.execute('UPDATE sessions_observed SET body=? WHERE host=? AND session_id=?',
            (json.dumps(missing), 'h1', SID))
    assert source.visible(d.journal.db, READER, ref)
    with d.journal.tx():
        d.journal.db.execute('UPDATE sessions_observed SET body=? WHERE host=? AND session_id=?',
            (json.dumps({**original, 'provider_native_id': 'another-native-session'}), 'h1', SID))
    assert not source.visible(d.journal.db, READER, ref)


async def test_unactivated_database_connection_cannot_reuse_durable_observation_as_authority(ready, mock):
    d = ready
    ref = await saved(d, mock)
    connection = sqlite3.connect(d.journal.path)
    connection.row_factory = sqlite3.Row
    try:
        assert connection.execute('SELECT COUNT(*) FROM session_archive_snapshots').fetchone()[0]
        assert not source.visible(connection, READER, ref)
        with pytest.raises(OperationError) as caught:
            source.resolve(connection, READER, ref)
        assert caught.value.status == 404
    finally:
        connection.close()


@pytest.mark.parametrize('change', ['server', 'binding', 'snapshot', 'prefix', 'version', 'latest', 'extra'])
async def test_forged_or_unpinned_reference_is_refused(ready, mock, change):
    d = ready
    ref = await saved(d, mock)
    if change == 'server':
        ref['server_id'] = 'another-server'
    elif change in {'binding', 'snapshot'}:
        ref['snapshot_id' if change == 'snapshot' else 'binding'] = '0' * 64
    elif change == 'prefix':
        ref['session_id'] = SID[:8]
    elif change == 'version':
        ref['version'] = True
    elif change == 'latest':
        ref['snapshot_id'] = 'latest'
    else:
        ref['authority'] = 'observe'
    mock.invokes.clear()
    assert not source.visible(d.journal.db, READER, ref)
    with pytest.raises(OperationError):
        source.resolve(d.journal.db, READER, ref)
    assert not mock.invokes


async def test_selection_is_bounded_whole_messages_and_keeps_exact_order(ready, mock):
    d = ready
    ref = await saved(d, mock, [archive.msg(i, f'{i:02}' + 'x' * 998) for i in range(25)])
    page = source.resolve(d.journal.db, READER, ref, max_messages=20, max_chars=3500)
    assert [m['id'] for m in page['messages']] == ['msg-22', 'msg-23', 'msg-24']
    assert all(len(m['text']) == 1000 and not m['clipped'] for m in page['messages'])
    assert page['coverage']['omitted_message_count'] == 22
    assert page['selection_digest'] == page['selection']['digest']
    assert source.visible(d.journal.db, READER, ref, selection=page['selection'])
    subset = source.selection(ref, page['selection']['members'][1:])
    assert source.visible(d.journal.db, READER, ref, selection=subset)
    reverse = source.selection(ref, list(reversed(page['selection']['members'])))
    assert not source.visible(d.journal.db, READER, ref, selection=reverse)
    forged = copy.deepcopy(page['selection'])
    forged['digest'] = '0' * 64
    assert not source.visible(d.journal.db, READER, ref, selection=forged)
    wrong = source.selection(ref, [['msg-24', '0' * 64]])
    assert not source.visible(d.journal.db, READER, ref, selection=wrong)


async def test_selection_cannot_borrow_revision_from_newer_snapshot(ready, mock):
    d = ready
    old = await saved(d, mock)
    mock.states[SID]['messages'][-1]['content'] = 'new revision'
    await d.session_archive.capture('h1', SID)
    new = source.resolve(d.journal.db, READER, archive.read(d)['retained_session_ref'])
    forged = source.selection(old, [[new['messages'][-1]['id'], new['messages'][-1]['revision']]])
    assert not source.visible(d.journal.db, READER, old, selection=forged)


async def test_source_requires_observe_and_current_original_pm_lineage(ready, mock):
    from bat_agent_connector import pm_workspace_adapter
    from tests.test_work_items import PERSON, act

    d = ready
    ref = await saved(d, mock)
    no_observe = api_auth.Principal('reader', frozenset({'manage'}))
    assert not source.visible(d.journal.db, no_observe, ref)
    with pytest.raises(OperationError) as caught:
        source.resolve(d.journal.db, no_observe, ref)
    assert caught.value.status == 403
    owner = api_auth.Principal(PERSON.actor, PERSON.scopes | {'start', 'operate'})
    workspace = (await act(d, owner, 'pm.workspace.create', params={'name': 'Source authority'}))['result']['workspace']
    wid = workspace['workspace_id']
    original = await act(d, owner, pm_workspace_adapter.ACTION, {'workspace_id': wid},
        {'adapter': pm_workspace_adapter.ADAPTERS[0], 'rationale': 'Original source policy'},
        pm_workspace_adapter.read(d.ops, owner, wid)['preconditions'])
    with d.journal.tx():
        d.journal.db.execute('INSERT INTO pm_source_lineage VALUES(?,?,?,?)',
            ('session', 'h1/' + SID, original['operation_id'], original['operation_id']))
    assert source.visible(d.journal.db, owner, ref)
    assert not source.visible(d.journal.db, READER, ref)
    with d.journal.tx():
        d.journal.db.execute('DELETE FROM pm_workspace_members WHERE workspace_id=? AND actor=?', (wid, owner.actor))
    assert not source.visible(d.journal.db, owner, ref)
    assert archive.count(d, 'session_archive_snapshots') == 1


@pytest.mark.parametrize('change', ['removed', 'rebound'])
async def test_live_binding_cannot_enter_sqlite_only_history_through_either_parent(ready, mock, change):
    from bat_agent_connector import pm_source_history as history
    from bat_agent_connector import pm_source_reads as reads

    d, calls = ready, []
    db = d.journal.db
    ref = await saved(d, mock)
    def current_source():
        calls.append(True)
        return source.visible(db, READER, ref)
    def original_continuation():
        return reads.check(db, READER, 'continuation', 'saved-turn', current_source)
    def parent(kind):
        return reads.check(db, READER, kind, 'derived-parent', original_continuation)
    with history.operation(db, 'fixture-github-observer'):
        # Two enclosing historical decisions share the original source read.
        # Both must inherit its dynamic-authority taint, including memo reuse.
        with reads.scope(db, READER):
            assert parent('package_sources')
            assert parent('work_batch_sources')
        assert len(calls) == 1
        writes = db.total_changes
        if change == 'removed':
            del d.inventory.config.hosts['h1']
        else:
            d.inventory.config.hosts['h1'] = replace(d.inventory.config.host('h1'), profile_id='another-profile')
        assert db.total_changes == writes
        await asyncio.sleep(0)
        assert not parent('package_sources')
        assert not parent('work_batch_sources')
        assert len(calls) == 3
