"""Real PM domain fixtures for fixed views, unchanged history and source privacy."""
from __future__ import annotations

import json

import pytest

from bat_agent_connector import pm_governance, pm_maintenance_review_sources, pm_roadmap, pm_snapshots
from bat_agent_connector import pm_snapshot_views as views
from bat_agent_connector.operations import OpContext, OperationError
from tests import test_pm_governance as governance
from tests import test_pm_meetings as meetings
from tests import test_pm_roadmap as roadmap
from tests import test_pm_sprints as sprints
from tests.test_pm_discussions import CONTRIBUTOR, OWNER, READER, settle, workspace
from tests.test_pm_evidence import capture, link, task
from tests.test_pm_snapshots import request, snapshot

sprint_ops = meetings.sprint_ops
meeting_ops = meetings.ops


@pytest.fixture
def ops(meeting_ops):
    pm_roadmap.install(meeting_ops)
    pm_governance.install(meeting_ops)
    return meeting_ops


def fixed(ops, wid, sid, principal=READER):
    return pm_snapshots.get_snapshot(ops, principal, wid, sid)['snapshot']


def records(snap, kind):
    return snap['management_views']['views'][kind]['records']


async def test_empty_and_legacy_snapshots_never_invent_domains_or_modify_old_bytes(ops):
    ws = await workspace(ops)
    old = await snapshot(ops, ws['workspace_id'], who=OWNER)
    stored = ops.db.execute('SELECT document FROM pm_document_snapshots WHERE snapshot_id=?', (old['snapshot_id'],)).fetchone()[0]
    new = await snapshot(ops, ws['workspace_id'], who=OWNER, include_management_views=True)
    assert 'management_views' not in old
    assert set(new['management_views']['views']) == set(views.VIEWS)
    assert all(not records(new, kind) for kind in views.VIEWS)
    assert new['brief']['delivery']['coverage']['qualified_deliverables'] is None
    assert fixed(ops, ws['workspace_id'], old['snapshot_id'], OWNER) == old
    assert ops.db.execute('SELECT document FROM pm_document_snapshots WHERE snapshot_id=?', (old['snapshot_id'],)).fetchone()[0] == stored


@pytest.mark.parametrize('value', ['true', 1, None, {}, []])
async def test_view_selection_is_an_exact_boolean_not_caller_supplied_facts(ops, value):
    ws = await workspace(ops)
    with pytest.raises(OperationError, match='INVALID_PARAMS'):
        ops.create(OWNER, **request(ops, ws['workspace_id'], include_management_views=value))


async def test_sprint_meeting_and_cards_keep_original_revisions_after_later_edits(ops):
    data, sprint, _, meeting = await meetings.meeting(ops)
    await meetings.note(ops, meeting)
    await meetings.draft(ops, meeting)
    original = await snapshot(ops, data['workspace_id'], include_management_views=True)
    selected = records(original, 'sprint')
    assert [item['kind'] for item in selected] == ['sprint', 'meeting']
    assert selected[0]['data']['artifacts']['dod']['revision_id'] == data['dod']
    assert selected[1]['data']['draft']['document']['output']['why']
    assert len(records(original, 'kanban')) == 2
    assert selected[0]['data']['sprint']['status'] == 'planned'
    await sprints.change(ops, sprint, 'goal.revise', goal='Later goal')
    await meetings.note(ops, meeting, content='Later private discussion')
    ops.db.execute("UPDATE work_items SET title='Later work title',version=version+1 WHERE work_item_id=?", (data['work'][0],))
    again = fixed(ops, data['workspace_id'], original['snapshot_id'])
    assert again == original
    assert 'Later goal' not in json.dumps(again) and 'Later work title' not in json.dumps(again)
    current = await snapshot(ops, data['workspace_id'], include_management_views=True)
    assert current['management_views'] != original['management_views']


async def test_roadmap_dates_and_governance_narrative_are_fixed_at_original_revisions(ops):
    data = await governance.prepared(ops)
    baseline = await roadmap.revision(ops, data['wid'], milestones=[roadmap.milestone(data['plan'])])
    forecast = await roadmap.revision(ops, data['wid'], 'forecast', updates=[{'milestone_id': 'release', 'forecast_date': '2026-11-03'}])
    finding = await governance.record(ops, data)
    original = await snapshot(ops, data['wid'], include_management_views=True)
    assert [row['id'] for row in records(original, 'roadmap')] == [baseline['revision_id'], forecast['revision_id']]
    assert records(original, 'governance')[0]['id'] == finding['record_id']
    await roadmap.revision(ops, data['wid'], 'forecast', updates=[{'milestone_id': 'release', 'forecast_date': None}])
    await governance.change(ops, finding, 'revise', **governance.fields(data, description='Later interpretation'))
    assert fixed(ops, data['wid'], original['snapshot_id']) == original


async def test_original_task_withdrawal_removes_whole_card_finding_and_quoted_ancestor(ops):
    data = await governance.prepared(ops, evidence=True)
    finding = await governance.record(ops, data)
    original = await snapshot(ops, data['wid'], include_management_views=True)
    assert records(original, 'kanban') and records(original, 'governance')
    # A new narrative quoting the extended snapshot carries all flattened roots.
    quote = {**data, 'snapshot': original}
    await governance.record(ops, quote, root_cause_key='second-finding',
                            disambiguation={'record_ids': [finding['record_id']], 'rationale': 'A different consequence'})
    later = await snapshot(ops, data['wid'], include_management_views=True)
    assert len(records(later, 'governance')) == 2
    refs = views.snapshot_refs(ops, data['wid'], later['snapshot_id'])
    assert all('document' not in value for value in refs)
    ops.db.execute("UPDATE tasks SET controller_authority='{}' WHERE task_id=?", (data['task']['task_id'],))
    for original_view in (original, later):
        redacted = fixed(ops, data['wid'], original_view['snapshot_id'])
        assert not records(redacted, 'kanban') and not records(redacted, 'governance')
        assert redacted['visibility']['filtered']
        assert redacted['snapshot_digest'] == original_view['snapshot_digest']
        assert redacted['view_digest'] != original_view['view_digest']
        assert data['task']['task_id'] not in json.dumps(redacted)
        assert finding['record_id'] not in json.dumps(redacted)
    with pytest.raises(OperationError):
        views.access(ops, READER, data['wid'], refs)
    # Weekly review flattening must inherit these new authority kinds as well.
    refs = pm_maintenance_review_sources.snapshot_refs(later, later['snapshot_digest'])
    with pytest.raises(OperationError):
        pm_maintenance_review_sources.access(ops, READER, data['wid'], refs)


async def test_withdrawn_execution_without_saved_capture_still_removes_frozen_card(ops):
    data = await sprints.prepared(ops)
    source = task(ops)
    target = {'workspace_id': data['workspace_id'], 'work_item_id': data['work'][0]}
    await link(ops, target, 'task', source['task_id'])
    original = await snapshot(ops, data['workspace_id'], include_management_views=True)
    assert len(records(original, 'kanban')) == 2
    assert records(original, 'kanban')[0]['data']['latest_capture'] is None
    ops.db.execute("UPDATE tasks SET controller_authority='{}' WHERE task_id=?", (source['task_id'],))
    redacted = fixed(ops, data['workspace_id'], original['snapshot_id'])
    assert len(records(redacted, 'kanban')) == 1
    assert records(redacted, 'kanban')[0]['id'] == data['work'][1]
    assert source['task_id'] not in json.dumps(redacted)


async def test_original_operation_receipt_never_recaptures_later_management_heads(ops):
    data = await sprints.prepared(ops)
    intent = request(ops, data['workspace_id'], include_management_views=True)
    op, _ = ops.create(CONTRIBUTOR, **intent)
    result = await pm_snapshots._run(OpContext(ops, op))
    before = fixed(ops, data['workspace_id'], result['snapshot_id'])
    ops.db.execute("UPDATE work_items SET title='Different after receipt' WHERE work_item_id=?", (data['work'][0],))
    replay, created = ops.create(CONTRIBUTOR, **intent)
    assert not created and (await settle(ops, replay))['result'] == result
    assert fixed(ops, data['workspace_id'], result['snapshot_id']) == before


async def test_whole_wire_byte_bound_keeps_complete_records_and_honest_coverage(ops, monkeypatch):
    data = await sprints.prepared(ops)
    source = task(ops)
    await link(ops, {'workspace_id': data['workspace_id'], 'work_item_id': data['work'][0]}, 'task', source['task_id'])
    await capture(ops, {'workspace_id': data['workspace_id'], 'work_item_id': data['work'][0]})
    original = await snapshot(ops, data['workspace_id'], include_management_views=True)
    bound = len(json.dumps({'version': 1, 'workspace_id': data['workspace_id'], 'snapshot': original}, ensure_ascii=False).encode('utf-8')) - 1000
    monkeypatch.setattr(pm_snapshots, 'MAX_SNAPSHOT_BYTES', bound)
    limited = await snapshot(ops, data['workspace_id'], include_management_views=True)
    wire = json.dumps({'version': 1, 'workspace_id': data['workspace_id'], 'snapshot': limited}, ensure_ascii=False).encode('utf-8')
    assert len(wire) <= bound
    assert len(records(limited, 'kanban')) < len(records(original, 'kanban'))
    assert limited['management_views']['views']['kanban']['coverage']['byte_limited']
    for row in records(limited, 'kanban'):
        prior = next(item for item in records(original, 'kanban') if item['id'] == row['id'])
        assert row['data']['work'] == prior['data']['work']
