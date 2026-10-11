"""Fixed view privacy and historical DoD use real independently verified results."""
from bat_agent_connector import pm_plans, pm_snapshots
from tests import test_pm_quality_projections as quality
from tests.test_pm_plan_operations import OWNER
from tests.test_pm_quality_reviews import execute

world, pm, ready, fixed, reviewed = quality.world, quality.pm, quality.ready, quality.fixed, quality.reviewed


async def test_qualified_sprint_card_and_snapshot_keep_original_conclusion_then_hide_withdrawn_proof(reviewed, monkeypatch):
    w, ops = reviewed, reviewed['d'].ops
    reference = await quality.qualified(w)
    await quality.capture(w, reference)
    target = await quality.sprint(w, monkeypatch)
    await quality.close(w, target, reference)
    op = await execute(w, quality.envelope('pm.snapshot.create', {'workspace_id': w['workspace_id']},
        {'include_management_views': True}, {'expected_plan_version': pm_plans.head(ops.db, w['workspace_id'])['version']}))
    before = pm_snapshots.get_snapshot(ops, OWNER, w['workspace_id'], op['result']['snapshot_id'])['snapshot']
    sections = before['management_views']['views']
    assert before['brief']['delivery']['quality']['qualified_requirements'] == 1
    assert sections['kanban']['records'][0]['data']['quality']['qualified']
    selected = sections['sprint']['records'][0]['data']['result']['items'][0]
    assert selected['counts_as_done'] and selected['quality_assessment']['reference'] == reference
    await quality.revoke(w, reference)
    assert pm_snapshots.get_snapshot(ops, OWNER, w['workspace_id'], before['snapshot_id'])['snapshot'] == before
    quality.withdraw_proof(w)
    redacted = pm_snapshots.get_snapshot(ops, OWNER, w['workspace_id'], before['snapshot_id'])['snapshot']
    assert redacted['visibility']['filtered']
    assert not redacted['management_views']['views']['kanban']['records']
    assert not redacted['management_views']['views']['sprint']['records']
    assert redacted['brief']['delivery'].get('quality') is None
