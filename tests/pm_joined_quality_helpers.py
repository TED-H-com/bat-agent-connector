"""One original Sprint and snapshot carry the same two fixed quality decisions."""
from __future__ import annotations

import copy
import datetime as dt
import time
import xml.etree.ElementTree as ET
from types import SimpleNamespace

from bat_agent_connector import pm_brief_exports, pm_evidence, pm_kanban, pm_sprints, pm_workspace
from bat_agent_connector import pm_roadmap as roadmap
from tests import test_pm_quality_projections as projections
from tests.pm_joined_lifecycle_helpers import OWNER, execute
from tests.test_pm_roadmap import milestone
from tests.test_pm_roadmap import request as roadmap_request


async def plan_iteration(w, selections):
    ops, wid = w['d'].ops, w['workspace_id']
    bindings = [pm_evidence._binding(ops.db, wid, item['target']['work_item_id']) for item in selections]
    goal = await execute(w, 'pm.product_goal.revise', {'workspace_id': wid},
        {'plan_revision_id': w['revision']['revision_id'], 'text': 'Members regain access with original login and one-use recovery',
            'rationale': 'The original selected membership outcome'}, {'expected_revision_id': None})
    backlog = await execute(w, 'pm.backlog.revise', {'workspace_id': wid},
        {'product_goal_revision_id': goal['result']['revision_id'], 'binding_ids': [row['binding_id'] for row in bindings],
            'rationale': 'The two original requirements'}, {'expected_revision_id': None})
    start = dt.datetime.fromtimestamp(time.time() - 60, dt.timezone.utc)
    end = start + dt.timedelta(days=7)
    created = await execute(w, 'pm.sprint.create', {'workspace_id': wid},
        {'title': 'Original member access iteration', 'mode': 'personal_iteration', 'owner_actor': OWNER.actor,
            'developers': [OWNER.actor], 'backlog_revision_id': backlog['result']['revision_id'],
            'dod_revision_id': w['dod'], 'goal': 'Original login and recovery meet their fixed acceptance criteria',
            'starts_at': start.isoformat(), 'ends_at': end.isoformat(), 'capacity_note': 'Two independent original packages',
            'rationale': 'Preserve this explicit Goal independently of engineering attempts'},
        {'expected_workspace_version': pm_workspace.get_workspace(ops.db, OWNER, wid)['version']})
    target = {'workspace_id': wid, 'sprint_id': created['result']['sprint_id']}
    await execute(w, 'pm.sprint.select', target, {'items': [{'binding_id': row['binding_id'], 'assignee': OWNER.actor,
        'how': 'Independent owned implementation and fixed-candidate verification'} for row in bindings],
        'rationale': 'Commit only the selected original work'}, {'expected_version': 1})
    await execute(w, 'pm.sprint.start', target, {'rationale': 'Begin the original timebox'}, {'expected_version': 2})
    w['joined_sprint'] = {'target': target, 'ends_at': end.timestamp(),
        'original': copy.deepcopy(pm_sprints.get_sprint(ops, OWNER, **target))}
    return target


async def shared_outcomes(w, monkeypatch):
    ops, wid = w['d'].ops, w['workspace_id']
    references = w['ledger'].references['quality_refs']
    for item, reference in zip(w['implementations'], references):
        await projections.capture(item, reference)
        binding = pm_evidence._binding(ops.db, wid, item['target']['work_item_id'])
        card = pm_kanban._card(ops, OWNER, wid, binding, 0)
        assert card['quality']['qualified'] and not card['completion']['approved']
    saved = await projections.snapshot(w)
    quality = saved['brief']['delivery']['quality']
    assert quality['qualified_requirements'] == quality['total_requirements'] == 2, quality
    assert quality['contribution_coverage_complete']
    for locale in ('en', 'zh-TW'):
        for format in ('markdown', 'html', 'svg'):
            rendered = pm_brief_exports.render_brief(ops, OWNER, wid, saved['snapshot_id'], format=format, locale=locale)
            text = ''.join(ET.fromstring(rendered['content']).itertext()) if format == 'svg' else rendered['content']
            assert rendered['view_digest'] == saved['view_digest']
            assert '2 / 2' in text and all(ref['document_digest'] in ''.join(text.split()) for ref in references)
    bindings = [pm_evidence._binding(ops.db, wid, item['target']['work_item_id']) for item in w['implementations']]
    baseline = roadmap_request(ops, wid, milestones=[milestone(w['revision'],
        requirement_ids=[row['id'] for row in w['requirements']], work_binding_ids=[row['binding_id'] for row in bindings])])
    await projections.base.execute(w, baseline)
    observed = await projections.base.execute(w, roadmap_request(ops, wid, 'observation', snapshot_id=saved['snapshot_id']))
    actual = roadmap.get_revision(ops, OWNER, wid, observed['result']['revision_id'])['revision']['document']['milestones'][0]['actual']
    assert actual['completion'] == 'qualified' and actual['dod'] == 'accepted'
    assert actual['quality']['requirements'] == quality['requirements']
    sprint = w['joined_sprint']
    # Only the Sprint domain clock advances to the declared timebox boundary;
    # no stdlib or provider clocks, verifier outcomes or journal rows are changed.
    with monkeypatch.context() as clock:
        clock.setattr(pm_sprints, 'time', SimpleNamespace(time=lambda: sprint['ends_at']))
        await execute(w, 'pm.sprint.close', sprint['target'],
            {'rationale': 'Close the original timebox with its exact assessed results', 'quality_refs': {
                item['target']['work_item_id']: reference for item, reference in zip(w['implementations'], references)}},
            {'expected_version': 3})
    closed = pm_sprints.get_sprint(ops, OWNER, **sprint['target'])
    assert closed['quality_summary']['qualified_items'] == 2
    assert closed['goal'] == sprint['original']['goal']
    assert all(row['counts_as_done'] for row in closed['history'][-1]['document']['items'])
    assert not closed['history'][-1]['document']['review_completed']
    w['joined_snapshot'], w['joined_sprint']['closed'] = saved, copy.deepcopy(closed)
    w['ledger'].record('shared_quality_views', project_snapshot_id=saved['snapshot_id'],
        roadmap_observation_id=observed['result']['revision_id'], sprint_id=sprint['target']['sprint_id'])
