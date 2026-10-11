"""Real disk Git adoption through scheduling and fixed evidence, with mock BAT."""
from bat_agent_connector import api_auth, pm_plans, pm_workspace, work_items
from tests.test_checkpoints import bat_writes, snapshot
from tests.test_pm_plan_api import WORKTREE_OWNER, draft_body, select_planning
from tests.test_pm_worktree import pm as pm_fixture
from tests.test_pm_worktree import world as repository_world
from tests.test_work_items import act

pm, world = pm_fixture, repository_world


async def test_adopted_git_plan_schedule_forecast_and_fixed_results_share_exact_sources(pm, mock):
    d, wid = pm['d'], pm['workspace_id']
    owner = api_auth.Principal(WORKTREE_OWNER.actor, frozenset({'observe', 'manage', 'start'}))
    tid = d.ops.db.execute('SELECT thread_id FROM pm_threads WHERE workspace_id=?', (wid,)).fetchone()[0]
    target = {'workspace_id': wid, 'thread_id': tid}
    human = snapshot(pm['human'])
    source, _ = await select_planning(d, target, owner)
    await act(d, owner, 'pm.run.prepare', target,
              {'purpose': 'Prepare this fixed plan', 'repository_binding': pm['selection']}, {'expected_version': 4})
    body = draft_body(d, target)
    body['params']['requirements'] = [{'id': 'membership', 'source_input_id': source}]
    proposal = await act(d, owner, body['action'], target, body['params'], body['preconditions'])
    rid = proposal['result']['revision_id']
    adoption = await act(d, owner, 'pm.plan.adopt', {'workspace_id': wid, 'revision_id': rid},
                         {'rationale': 'Adopt this exact direction'}, {'expected_accepted_revision_id': None})
    accepted = await d.call_api('pm_plan_accepted_get', {'workspace_id': wid}, owner)
    assert accepted['plan']['adoption']['commit_sha'] == adoption['result']['commit_sha']
    pid = (await act(d, owner, 'project.create', params={'name': 'Membership'}))['result']['project_id']
    work = (await act(d, owner, 'work_item.create', {'project_id': pid}, {'title': 'Deliver membership'}))['result']['work_item_id']
    await act(d, owner, 'pm.project.bind', {'workspace_id': wid, 'project_id': pid}, {'rationale': 'Explicit project'},
              {'expected_workspace_version': pm_workspace.get_workspace(d.ops.db, owner, wid)['version'],
               'expected_project_version': work_items._get_project(d.ops.db, pid)['version']})
    binding = (await act(d, owner, 'pm.work.bind', {'workspace_id': wid, 'work_item_id': work},
        {'rationale': 'Fixed requirement', 'plan_revision_id': rid, 'requirement_ids': ['membership']},
        {'expected_binding_id': None, 'expected_work_version': work_items._get_item(d.ops.db, work)['version']}))['result']['binding_id']
    plan_version = pm_plans.head(d.ops.db, wid)['version']
    baseline = await act(d, owner, 'pm.roadmap.baseline.set', {'workspace_id': wid},
        {'rationale': 'First usable outcome', 'milestones': [{'milestone_id': 'release', 'title': 'Usable membership',
            'plan_revision_id': rid, 'requirement_ids': ['membership'], 'work_binding_ids': [binding],
            'baseline_date': '2026-11-01', 'depends_on': []}]},
        {'expected_plan_version': plan_version, 'expected_roadmap_version': 0})
    baseline_id = baseline['result']['revision_id']
    forecast = await act(d, owner, 'pm.roadmap.forecast.set', {'workspace_id': wid},
        {'baseline_revision_id': baseline_id, 'rationale': 'A dependency needs more time',
         'updates': [{'milestone_id': 'release', 'forecast_date': '2026-11-08'}]}, {'expected_roadmap_version': 1})
    capture = await act(d, owner, 'pm.evidence.capture', {'workspace_id': wid, 'work_item_id': work}, {}, {'expected_binding_id': binding})
    brief = await act(d, owner, 'pm.snapshot.create', {'workspace_id': wid}, {}, {'expected_plan_version': plan_version})
    observation = await act(d, owner, 'pm.roadmap.observe', {'workspace_id': wid},
        {'baseline_revision_id': baseline_id, 'snapshot_id': brief['result']['snapshot_id']}, {'expected_roadmap_version': 2})
    current = await d.call_api('pm_roadmap_get', {'workspace_id': wid}, owner)
    milestone = current['baseline']['document']['milestones'][0]
    assert milestone['baseline_date'] == milestone['original_baseline_date'] == '2026-11-01'
    assert milestone['plan_revision_id'] == rid and milestone['work_binding_ids'] == [binding]
    assert current['forecast']['document']['milestones'][0]['forecast_date'] == '2026-11-08'
    observed = current['observation']['document']
    assert observed['forecast_revision_id'] == forecast['result']['revision_id']
    result = observed['milestones'][0]
    assert result['work'][0]['match_status'] == 'exact'
    assert result['work'][0]['capture_id'] == capture['result']['capture_id']
    assert result['actual']['completion'] == result['actual']['dod'] == 'unassessed'
    assert result['actual']['actual_finish_date'] is None
    assert len(result['actual']['facts']) == 1
    replay = await act(d, owner, 'pm.roadmap.observe', observation['target'], observation['params'],
                       observation['preconditions'], key=observation['idempotency_key'])
    assert replay['operation_id'] == observation['operation_id']
    assert (await d.call_api('pm_roadmap_get', {'workspace_id': wid}, owner)) == current
    assert (await d.call_api('pm_plan_accepted_get', {'workspace_id': wid}, owner)) == accepted
    assert snapshot(pm['human']) == human and not bat_writes(mock)
    assert all(method == 'GET' for method, _, _ in pm['gh'].requests)
