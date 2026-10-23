"""Real merge refusal cannot turn external closure into requirement acceptance."""
from bat_agent_connector import pm_attempt_result_reads, pm_result_requirements
from tests import test_pm_external_closure as external
from tests import test_pm_quality_projections as projections

world, pm, ready = external.world, external.pm, external.ready
fixed, delivery_ready, source_world = external.fixed, external.delivery_ready, external.source_world
reviewed = projections.reviewed


async def test_closed_unmerged_pull_does_not_accept_or_deploy_original_requirements(source_world):
    w, ops = source_world, source_world['d'].ops
    def read():
        return pm_result_requirements.read(ops, external.OWNER, w['workspace_id'], w['original']['operation_id'], w['candidate'], 'verified')
    before = read()
    w['gh'].pulls[1].update(state='closed', merged=False, merged_at=None)
    await ops._execute(w['merge']['operation_id'])
    failed = ops.get(w['merge']['operation_id'])
    assert failed['status'] == 'failed' and failed['error_code'] == 'PR_CLOSED'
    assert read() == before
    assert not any(row['qualified'] for row in before['items'][0]['assessments'])
    delivery = pm_attempt_result_reads._delivery_section(ops, external.OWNER, w['workspace_id'],
        w['target']['work_item_id'], w['original']['operation_id'], 'pull_request', 'o/r', w['candidate'])[0]
    assert not delivery['deployments']


async def test_withdrawn_review_proof_is_unknown_not_no_responsible_conclusion(reviewed):
    w, ops = reviewed, reviewed['d'].ops
    ref = await projections.qualified(w)
    projections.withdraw_proof(w)
    section = pm_result_requirements.read(ops, external.OWNER, w['workspace_id'], w['child']['operation_id'], w['candidate'], 'verified')
    assert section['relation'] == 'explicit' and section['coverage']['reviews_partial']
    assert section['items'][0]['state'] == 'unavailable'
    assert section['items'][0]['assessments'] == []
    assert ref['review_id'] not in str(section)
