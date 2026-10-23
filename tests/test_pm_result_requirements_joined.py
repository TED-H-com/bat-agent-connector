"""One original repair assessment through Result and the existing PM projections."""
from bat_agent_connector import (
    pm_maintenance_review_sources,
    pm_overview,
    pm_quality_projections,
    pm_result_requirements,
    pm_sprints,
)
from tests import test_pm_maintenance_repair_quality as repair
from tests import test_pm_quality_projections as projections
from tests import test_pm_roadmap_quality as roadmap

world, pm, ready, fixed = repair.world, repair.pm, repair.ready, repair.fixed
repair_ready, quality_repair = repair.repair_ready, repair.quality_repair
original_publication_authority = repair.original_publication_authority


async def test_same_original_assessment_in_result_requirements_overview_roadmap_sprint_and_maintenance(quality_repair, monkeypatch):
    w, ops, owner = quality_repair, quality_repair['d'].ops, repair.OWNER
    ref = {**w['quality_ref'], 'candidate_commit': w['candidate']}
    section = pm_result_requirements.read(ops, owner, w['workspace_id'], w['child']['operation_id'], w['candidate'], 'verified')
    assert section['items'][0]['assessments'][0]['reference'] == ref
    assert pm_quality_projections.original(ops, owner, w['workspace_id'], ref)['assessment']['document'] == section['items'][0]['assessments'][0]['original']
    await projections.capture(w, ref)
    snapshot = await projections.snapshot(w)
    overview = pm_overview.read(ops, owner, w['workspace_id'])
    assert overview['recent_deliveries'][0]['assessment_ref'] == ref
    observation = await roadmap.observe(w, snapshot)
    assert observation['document']['milestones'][0]['actual']['quality']['assessments'][0]['reference'] == ref
    related = pm_result_requirements.read(ops, owner, w['workspace_id'], w['child']['operation_id'], w['candidate'], 'verified')['related']
    assert {row['kind'] for row in related} == {'milestone', 'maintenance_repair'}
    target = await projections.sprint(w, monkeypatch)
    await projections.close(w, target, ref)
    sprint = pm_sprints.get_sprint(ops, owner, **target)
    assert sprint['history'][-1]['document']['items'][0]['quality_assessment']['reference'] == ref
    saved = repair.view(w, w['parent'])
    period = {'start': saved['created_at'] - 1, 'end': saved['created_at'] + 1}
    report, _ = pm_maintenance_review_sources.build(ops, owner, w['workspace_id'],
        saved['context']['check']['workload'], period, period['end'])
    assert report['repairs'][0]['progress']['quality'][0]['reference'] == w['quality_ref']
    assert not report['repairs'][0]['repair_fixed']
