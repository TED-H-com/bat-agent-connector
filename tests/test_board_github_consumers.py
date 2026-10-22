"""Every PM reader separates source closure, local claims and delivery evidence."""
import pytest

from bat_agent_connector import (
    pm_brief_exports,
    pm_evidence,
    pm_kanban,
    pm_overview,
    pm_schedule,
    pm_snapshots,
    work_items,
)
from tests.test_board_github_sync import Provider, issue, sync
from tests.test_work_items import PERSON, act
from tests.test_work_items import daemon as daemon


@pytest.fixture(params=[(True, None, False, 'github_closed'), (True, None, True, 'github_done'),
                        (False, 'completed', False, 'github_completed')])
async def source_card(daemon, request):
    d = daemon
    pr, reason, merged, lane = request.param
    ws = (await act(d, PERSON, 'pm.workspace.create', params={'name': 'Source readers'}))['result']['workspace']['workspace_id']
    source = Provider([issue(1, pr=pr)])
    d.ops.context['board_github_provider'] = source
    result = (await act(d, PERSON, 'pm.github.import', {'workspace_id': ws}, {'repository': 'example/board'},
        {'expected_revision': 0}))['result']
    pid = result['project_id']
    iid = d.ops.db.execute('SELECT work_item_id FROM work_items WHERE project_id=?', (pid,)).fetchone()[0]
    if not pr:
        current = work_items._get_item(d.ops.db, iid)
        await act(d, PERSON, 'work_item.update', {'work_item_id': iid}, {'state': 'doing'}, {'expected_version': current['version']})
    source.values = [issue(1, pr=pr, state='closed', merged=merged, state_reason=reason)]
    await sync(d, pid)
    binding = pm_evidence.list_work(d.ops, PERSON, ws)['bindings'][0]
    return d, ws, iid, lane, binding


async def test_transitioned_source_closure_is_not_wip(source_card):
    d, ws, iid, lane, _ = source_card
    card = pm_kanban.get_board(d.ops, PERSON, ws)['cards'][0]
    assert card['column'] == lane
    assert card['flow']['wip'] is False
    assert card['flow']['age_seconds'] is None and card['flow']['cycle_seconds'] is None
    assert pm_kanban.get_board(d.ops, PERSON, ws)['wip']['count'] == 0
    assert work_items._get_item(d.ops.db, iid)['state'] == 'doing'


async def test_mcp_work_list_filters_display_lane_without_changing_claim(source_card):
    d, _, iid, lane, _ = source_card
    public = work_items.work_item_get(d.ops.db, iid)['work_item']
    assert public['display_state'] == lane
    assert public['completion']['display_state'] == 'doing' and not public['completion']['pending']
    assert work_items.work_items_list(d.ops.db, state='doing')['work_items'] == []
    assert work_items.work_items_list(d.ops.db, state=lane)['work_items'][0]['work_item_id'] == iid
    assert work_items.work_items_list(d.ops.db, pending=True)['work_items'] == []
    nav = work_items.project_get(d.ops.db, public['project_id'], projection='navigation')
    assert nav['work_items'][0]['display_state'] == lane
    assert nav['project']['counts']['doing'] == nav['project']['counts']['pending'] == 0


async def test_overview_does_not_count_closed_source_as_active_requirement_work(source_card):
    d, ws, iid, _, binding = source_card
    projection = pm_overview.Projection(d.ops, PERSON, ws)
    projection.coverage = {k: {'complete': True} for k in ('work', 'quality', 'governance')}
    plan = {'revision_id': 'fixed-plan', 'document': {'requirements': [{'id': 'req'}]}}
    work = {iid: {'binding': {**binding, 'plan_revision_id': plan['revision_id'], 'requirement_ids': ['req']},
        'work': work_items._get_item(d.ops.db, iid)}}
    progress, states = projection.progress(plan, work, set(), set(), set())
    assert progress['in_progress'] == progress['in_review'] == progress['accepted'] == 0
    assert states['req'] == 'not_started'


async def test_schedule_exposes_source_lane_without_delivery_or_active_work_claim(source_card):
    d, _, iid, lane, binding = source_card
    notes = set()
    row = pm_schedule._work_node(d.ops.db, binding, work_items._get_item(d.ops.db, iid), None, [], False,
        {'operations': {}, 'merges': {}, 'deployments': {}}, notes)
    assert row['status'] == 'unknown'
    assert row['refs']['source_lane'] == lane
    assert row['refs']['github_source']['source'] == 'github'
    assert row['actual'] is None


async def test_fixed_brief_records_and_localizes_source_status(source_card):
    d, ws, iid, lane, binding = source_card
    await act(d, PERSON, 'pm.evidence.capture', {'workspace_id': ws, 'work_item_id': iid},
        pre={'expected_binding_id': binding['binding_id']})
    result = (await act(d, PERSON, 'pm.snapshot.create', {'workspace_id': ws},
        pre={'expected_plan_version': 0}))['result']
    saved = pm_snapshots.get_snapshot(d.ops, PERSON, ws, result['snapshot_id'])['snapshot']
    claim = next(row for row in saved['brief']['delivery']['items'] if row['kind'] == 'work_claim')
    assert claim['status'] == lane
    assert claim['details']['state'] == 'doing' and claim['details']['done_by'] is None
    for locale, label in [('en', 'GitHub source'), ('zh-TW', 'GitHub 來源')]:
        rendered = pm_brief_exports.render_brief(d.ops, PERSON, ws, result['snapshot_id'], format='markdown', locale=locale)['content']
        assert label in rendered
        assert '回報完成' not in rendered
