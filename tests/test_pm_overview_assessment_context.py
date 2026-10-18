"""Freeze one central read and prove unrelated discussion text never enters it."""
from __future__ import annotations

import pytest

from bat_agent_connector import pm_overview_assessment_context as context
from bat_agent_connector import pm_runs
from bat_agent_connector.operations import OperationError
from tests import test_pm_governance as governance
from tests import test_pm_overview as overview
from tests import test_pm_plans as plans
from tests.test_pm_discussions import OWNER, act, prepared
from tests.test_pm_snapshots import snapshot

base_ops, ops, origins = overview.base_ops, overview.ops, overview.origins


async def test_basis_is_read_only_stable_across_unrelated_events_and_carrier_text(ops):
    target, _ = await origins(ops)
    before = ops.db.total_changes
    first = context.preview(ops, OWNER, **target)
    assert ops.db.total_changes == before
    assert first['context']['projection']['plan']['goal'] is None
    assert first['context']['goal_ref'] is None
    assert first['calls_provider'] is False
    assert context.preview(ops, OWNER, **target)['selector'] == first['selector']
    with ops.journal.tx():
        ops.journal.api_event('test', 'unrelated', 'test.unrelated', {})
    assert context.preview(ops, OWNER, **target)['selector'] == first['selector']
    await act(ops, target, 'pm.message.append', {'content': 'PRIVATE_CARRIER_SENTINEL'})
    later = context.preview(ops, OWNER, **target)
    assert later['selector'] == first['selector']
    assert 'PRIVATE_CARRIER_SENTINEL' not in pm_runs._json(later['context'])
    assert later['context']['thread_version'] > first['context']['thread_version']


async def test_blocker_has_original_current_requirement_goal_edges(ops):
    target = await prepared(ops)
    wid = target['workspace_id']
    selected = []
    for slot, value in [('goal', 'Restore access'), ('scope', 'Members can sign in')]:
        row = await act(ops, target, 'pm.input.record', {'slot': slot, 'value': value, 'status': 'user_confirmed',
            'source_ref': {'kind': 'statement', 'ref': 'Explicit owner direction'}})
        selected.append(row['input_id'])
    await act(ops, target, 'pm.thread.plan', {'selected_input_ids': selected, 'rationale': 'Plan account access'})
    plan = await plans.draft(ops, target, requirements=[{'id': 'login', 'source_input_id': selected[1]}])
    plans.adopt(plans.adoption(ops, target, plan['revision_id']))
    data = {'wid': wid, 'snapshot': await snapshot(ops, wid), 'assumption': selected[1]}
    before = context.preview(ops, OWNER, **target)
    await governance.record(ops, data, kind='dependency', root_cause_key='assessment-blocker', title='Restore dependency')
    after = context.preview(ops, OWNER, **target)
    assert after['selector'] != before['selector']
    sources = after['context']['source_references']
    blocker = next(row for row in sources.values() if row.get('label') == 'Restore dependency')
    assert blocker['goal_ref'] == 'goal' and blocker['requirement_refs']
    assert 'goal_blocker' in blocker['eligible_templates']
    assert all(sources[key]['category'] == 'requirement' for key in blocker['requirement_refs'])
    assert after['context']['source_operation_ids']


async def test_bound_rejects_essential_context_and_nonmember_reads(ops):
    target, _ = await origins(ops)
    with pytest.raises(OperationError) as error:
        context.preview(ops, OWNER, **target, max_chars=2000)
    assert error.value.status == 413
    with pytest.raises(OperationError) as error:
        context.preview(ops, overview.OUTSIDER, **target)
    assert error.value.status in (403, 404)
