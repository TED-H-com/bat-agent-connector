"""Existing multi-adoption history retains its original source visibility."""
from bat_agent_connector import pm_access, pm_plans
from tests import test_pm_conversation_source_privacy as privacy
from tests.test_pm_source_reads_joined import measured

world = privacy.world
pm = privacy.pm
ready = privacy.ready
proposal_world = privacy.proposal_world


async def test_repeated_real_plan_adoptions_preserve_prior_graph_visibility(proposal_world, monkeypatch):
    w, ops = proposal_world, proposal_world['d'].ops
    history = []
    for _ in range(3):
        proposal, adopted = await privacy.adopt_proposal(w)
        history.extend([ops.get(proposal['operation_id']), adopted])
    assert len({operation['operation_id'] for operation in history}) == 6
    assert pm_plans.head(ops.db, w['workspace_id'])['accepted_revision_id'] == history[-1]['result']['revision_id']
    for operation in history:
        old, _, _ = measured(ops.db, operation, monkeypatch, legacy=True, principal=privacy.OWNER)
        new, _, roots = measured(ops.db, operation, monkeypatch, legacy=False, principal=privacy.OWNER)
        assert old is new is True
        assert all(count == 1 for count in roots.values())
    access = privacy.withdrawal(w, monkeypatch)
    access['available'] = False
    assert all(not pm_access.visible(ops.db, privacy.OWNER, operation) for operation in history)
    access['available'] = True
    assert all(pm_access.visible(ops.db, privacy.OWNER, operation) for operation in history)
