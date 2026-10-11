"""Meeting reads retain complete facts under a UTF-8 wire budget."""
from __future__ import annotations

import copy

import pytest

from bat_agent_connector import pm_meetings as pm
from bat_agent_connector import pm_sprints
from bat_agent_connector.operations import OperationError
from tests.test_pm_meetings import (
    OWNER,
    READER,
    apply,
    capture,
    complete,
    create_request,
    draft,
    draft_params,
    link,
    meeting,
    note,
    read,
    request,
    settle,
    snapshot,
    task,
)
from tests.test_pm_meetings import ops as ops  # noqa: F401
from tests.test_pm_meetings import sprint_ops as sprint_ops  # noqa: F401
from tests.test_pm_sprint_read_budget import wire_size


def set_exact_budget(monkeypatch, document):
    budget = 0
    while True:
        document['coverage']['max_bytes'] = budget
        size = wire_size(document)
        if size == budget:
            monkeypatch.setattr(pm_sprints, 'MAX_READ_BYTES', budget)
            return budget
        budget = size


def assert_bounded(document):
    assert wire_size(document) <= pm_sprints.MAX_READ_BYTES


async def test_current_context_snapshot_draft_completion_remain_whole_with_zero_history_room(ops, monkeypatch):
    _, _, _, target = await meeting(ops, kind='retro')
    await draft(ops, target, 'retro')
    await note(ops, target)
    await complete(ops, target)
    await apply(ops, target, 'follow_up.record', follow_up_id='check_quality', status='open', result='繼續觀察')
    full = read(ops, target)
    minimal = copy.deepcopy(full)
    for kind, key in [('notes', 'notes'), ('follow_ups', 'follow_up_events')]:
        minimal[key] = []
        minimal['history_next_cursors'][kind] = 0
        minimal['coverage']['histories'][kind] = {'after': 0, 'returned': 0, 'has_more': True, 'byte_limited': True}
    budget = set_exact_budget(monkeypatch, minimal)
    result = read(ops, target)
    assert result == minimal
    for key in ('context', 'snapshot', 'draft', 'completion'):
        assert result[key] == full[key]
    assert result['history_next_cursors'] == {'notes': 0, 'follow_ups': 0}
    assert_bounded(result)
    for kind in ('notes', 'follow_ups'):
        page = pm.list_history(ops, READER, **target, kind=kind)
        assert page['records'] and page['next_cursor'] is None
        assert_bounded(page)
    monkeypatch.setattr(pm_sprints, 'MAX_READ_BYTES', budget - 1)
    with pytest.raises(OperationError, match='PM_MEETING_READ_TOO_LARGE') as error:
        read(ops, target)
    assert error.value.status == 413


async def test_twelve_valid_exact_200kb_drafts_page_by_bytes_without_loss(ops):
    _, _, _, target = await meeting(ops)
    params = draft_params()
    params['rationale'] = 'An explicit meeting record'
    params['output']['dependency_notes'] = ['驗' * 4000] * 16 + ['x']
    current = read(ops, target)
    document = pm._draft(ops, OWNER, current['meeting'], current['context']['document'], current['snapshot'], params)
    gap = pm.MAX_DOCUMENT_BYTES - len(pm._json(document).encode('utf-8'))
    params['output']['dependency_notes'][-1] += '驗' * (gap // 3) + 'x' * (gap % 3)
    expected = pm._draft(ops, OWNER, current['meeting'], current['context']['document'], current['snapshot'], params)
    assert len(pm._json(expected).encode('utf-8')) == 200_000
    ids = []
    for _ in range(12):
        ids.append((await apply(ops, target, 'draft', **params))['draft_revision_id'])
    first = pm.list_history(ops, READER, **target, kind='drafts')
    assert_bounded(first)
    assert first['coverage']['byte_limited'] and len(first['records']) < 12
    second = pm.list_history(ops, READER, **target, kind='drafts', after=first['next_cursor'])
    assert_bounded(second)
    assert second['next_cursor'] is None
    records = first['records'] + second['records']
    assert [record['revision_id'] for record in records] == ids
    assert all(record['document'] == expected for record in records)
    assert read(ops, target)['draft']['document'] == expected


@pytest.mark.parametrize('kind', ['contexts', 'notes', 'drafts', 'follow_ups'])
async def test_each_history_returns_complete_prefix_and_advances(ops, monkeypatch, kind):
    _, sprint, snap, target = await meeting(ops, kind='retro')
    for _ in range(2):
        req = request(ops, target, 'context.revise', snapshot_id=snap['snapshot_id'])
        req['preconditions']['expected_sprint_version'] = pm_sprints._sprint(ops.db, sprint['workspace_id'], sprint['sprint_id'])['version']
        op, _ = ops.create(OWNER, **req)
        await settle(ops, op)
    await apply(ops, target, 'note', content='中文\n"完整"\\文字' * 300)
    await apply(ops, target, 'note', content='後續\n"完整"\\文字' * 300)
    await draft(ops, target, 'retro')
    await draft(ops, target, 'retro')
    await complete(ops, target)
    for status in ('open', 'blocked'):
        await apply(ops, target, 'follow_up.record', follow_up_id='check_quality', status=status, result='持續核對' * 800)
    all_records = pm.list_history(ops, READER, **target, kind=kind)['records']
    sizes = [wire_size(pm.list_history(ops, READER, **target, kind=kind,
             after=0 if i == 0 else all_records[i - 1]['sequence'], limit=1)) for i in range(len(all_records))]
    monkeypatch.setattr(pm_sprints, 'MAX_READ_BYTES', max(sizes) + 64)
    received, after, byte_limited = [], 0, False
    while True:
        page = pm.list_history(ops, READER, **target, kind=kind, after=after)
        assert page['records']
        assert_bounded(page)
        received.extend(page['records'])
        byte_limited |= page['coverage']['byte_limited']
        if page['next_cursor'] is None:
            break
        assert page['next_cursor'] > after
        after = page['next_cursor']
    assert received == all_records and byte_limited


@pytest.mark.parametrize('surface', ['meeting', 'history', 'list'])
async def test_mandatory_or_single_record_overflow_errors_without_mutation(ops, monkeypatch, surface):
    data, _, _, target = await meeting(ops)
    await note(ops, target)
    before = ops.db.total_changes
    monkeypatch.setattr(pm_sprints, 'MAX_READ_BYTES', 450)
    lookups = {'meeting': lambda: read(ops, target), 'list': lambda: pm.list_meetings(ops, READER, data['workspace_id']),
               'history': lambda: pm.list_history(ops, READER, **target, kind='notes')}
    with pytest.raises(OperationError, match='PM_MEETING_READ_TOO_LARGE') as error:
        lookups[surface]()
    assert error.value.status == 413 and ops.db.total_changes == before


async def test_listing_byte_cap_keeps_first_omitted_visible_meeting_on_next_page(ops, monkeypatch):
    data, sprint, snap, first_target = await meeting(ops)
    ids = [first_target['meeting_id']]
    for _ in range(2):
        pending, _ = ops.create(OWNER, **create_request(ops, sprint, snap))
        ids.append((await settle(ops, pending))['result']['meeting_id'])
    one = pm.list_meetings(ops, READER, data['workspace_id'], limit=1)
    monkeypatch.setattr(pm_sprints, 'MAX_READ_BYTES', wire_size(one) + 64)
    received, after = [], 0
    while True:
        page = pm.list_meetings(ops, READER, data['workspace_id'], after=after)
        assert_bounded(page)
        assert len(page['meetings']) == 1
        received.extend(item['meeting_id'] for item in page['meetings'])
        if page['next_cursor'] is None:
            break
        assert page['coverage']['byte_limited'] and page['next_cursor'] > after
        after = page['next_cursor']
    assert received == ids


async def restricted_meeting(ops, data, sprint):
    work = {'workspace_id': data['workspace_id'], 'work_item_id': data['work'][0]}
    source = task(ops)
    await link(ops, work, 'task', source['task_id'])
    await capture(ops, work)
    snap = await snapshot(ops, data['workspace_id'])
    op, _ = ops.create(OWNER, **create_request(ops, sprint, snap))
    result = (await settle(ops, op))['result']
    return {'workspace_id': data['workspace_id'], 'meeting_id': result['meeting_id']}, source


async def test_filtered_meeting_list_advances_without_leaking_ids_or_stalling(ops):
    data, sprint, public_snap, public_target = await meeting(ops)
    private_target, source = await restricted_meeting(ops, data, sprint)
    op, _ = ops.create(OWNER, **create_request(ops, sprint, public_snap))
    later = (await settle(ops, op))['result']['meeting_id']
    ops.db.execute("UPDATE tasks SET controller_authority='{}' WHERE task_id=?", (source['task_id'],))
    first = pm.list_meetings(ops, READER, data['workspace_id'], limit=1)
    assert first['meetings'][0]['meeting_id'] == public_target['meeting_id']
    hidden = pm.list_meetings(ops, READER, data['workspace_id'], after=first['next_cursor'], limit=1)
    assert hidden['meetings'] == [] and hidden['next_cursor'] > first['next_cursor']
    assert hidden['coverage']['visibility_filtered'] and hidden['coverage']['scanned'] == 1
    assert private_target['meeting_id'] not in str(hidden)
    last = pm.list_meetings(ops, READER, data['workspace_id'], after=hidden['next_cursor'])
    assert last['meetings'][0]['meeting_id'] == later and last['next_cursor'] is None


async def test_old_context_draft_visibility_is_rechecked_on_each_page(ops, monkeypatch):
    data, sprint, public_snap, _ = await meeting(ops)
    target, source = await restricted_meeting(ops, data, sprint)
    await draft(ops, target)
    req = request(ops, target, 'context.revise', snapshot_id=public_snap['snapshot_id'])
    req['preconditions']['expected_sprint_version'] = pm_sprints._sprint(ops.db, sprint['workspace_id'], sprint['sprint_id'])['version']
    op, _ = ops.create(OWNER, **req)
    await settle(ops, op)
    await draft(ops, target)
    first = pm.list_history(ops, READER, **target, kind='drafts', limit=1)
    ops.db.execute("UPDATE tasks SET controller_authority='{}' WHERE task_id=?", (source['task_id'],))
    assert read(ops, target)['draft']['context_revision_id'] != first['records'][0]['context_revision_id']
    # Current membership/current context never grants access to an older source.
    monkeypatch.setattr(pm_sprints, 'MAX_READ_BYTES', wire_size(first) + 64)
    with pytest.raises(OperationError, match='PM_MEETING_SOURCE_UNAVAILABLE'):
        pm.list_history(ops, READER, **target, kind='drafts', limit=1)
    later = pm.list_history(ops, READER, **target, kind='drafts', after=first['next_cursor'])
    assert len(later['records']) == 1 and later['next_cursor'] is None


async def test_revoked_workspace_membership_is_checked_before_budget_failure(ops, monkeypatch):
    data, _, _, target = await meeting(ops)
    ops.db.execute('DELETE FROM pm_workspace_members WHERE workspace_id=? AND actor=?', (data['workspace_id'], READER.actor))
    monkeypatch.setattr(pm_sprints, 'MAX_READ_BYTES', 1)
    for lookup in (lambda: read(ops, target, READER), lambda: pm.list_meetings(ops, READER, data['workspace_id']),
                   lambda: pm.list_history(ops, READER, **target, kind='notes')):
        with pytest.raises(OperationError) as error:
            lookup()
        assert error.value.status == 404
