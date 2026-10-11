"""Original deploy, baseline selection and runtime proof in one fixed report chain."""
import copy
from types import SimpleNamespace

import pytest

from bat_agent_connector import pm_maintenance_repair_runtime as runtime
from bat_agent_connector import pm_maintenance_review_sources as reports
from bat_agent_connector.operations import OperationError
from tests.test_pm_maintenance_repair_baseline import (  # noqa: F401
    OWNER,
    adopted_delivery,
    advance,
    base_ready_chain,
    ceiling,
    execute,
    finish_deployment,
    fixed,
    original_deployment,
    original_publication_authority,
    pm,
    publishing,
    quality_repair,
    ready,
    repair_ready,
    view,
    world,
)
from tests.test_pm_maintenance_repair_baseline import (
    ready_chain as ready_chain_fixture,
)
from tests.test_pm_maintenance_repair_runtime import (  # noqa: F401
    native_replay as native_replay_fixture,
)
from tests.test_pm_maintenance_repair_runtime import (
    observe,
)
from tests.test_pm_maintenance_repair_runtime import (
    original_native_agent as original_native_agent_fixture,
)
from tests.test_pm_release_baselines import remove_repo

ready_chain = ready_chain_fixture
native_replay = native_replay_fixture
original_native_agent = original_native_agent_fixture


async def test_deployment_baseline_completion_and_fixed_review_share_original_receipts(ready_chain, native_replay, monkeypatch, mock):
    w, ops = ready_chain, ready_chain['d'].ops
    assert runtime.origin(ops, w['parent'])['confinement']['evidence']['agent'] == 'codex'
    monkeypatch.setattr(runtime, 'time', SimpleNamespace(time=lambda: w['clock'][0]))
    native_replay['reason'] = 'completed'
    parent = await finish_deployment(w)
    repair = view(w, parent)
    period = {'start': repair['created_at'] - 1, 'end': repair['created_at'] + 1}
    intent = repair['context']['check']['workload']
    before, old_refs = reports.build(ops, OWNER, w['workspace_id'], intent, period, period['end'])
    assert before['repairs'][0]['capacity_held'] is True
    assert before['repairs'][0]['runtime_completion'] is None
    saved = copy.deepcopy(before)

    w['parent'] = await advance(w, parent, rounds=10)
    assert w['parent']['status'] == 'succeeded', w['parent']
    assert w['parent']['result']['baseline_update']['status'] == 'updated'
    reader = await observe(w)
    assert reader['status'] == 'succeeded', reader
    repair = view(w, w['parent'])
    after, new_refs = reports.build(ops, OWNER, w['workspace_id'], intent, period, period['end'])
    sample = after['repairs'][0]
    assert sample['capacity_held'] is False and sample['outcome'] == 'deployed'
    assert sample['baseline_update'] == repair['baseline_update']
    assert sample['runtime_completion'] == repair['runtime_completion']
    assert sample['composed_delivery'] == repair['delivery_child']['result']
    assert {child['slot'] for child in sample['children']} == {
        'child', 'publication_child', 'delivery_child', 'baseline_child', 'runtime_observer'}
    assert after['effort']['repair_counts']['runtime_completed'] == 1
    assert after['effort']['repair_counts']['capacity_held'] == 0
    assert before == saved
    assert len(w['posts']) == 1 and w['gh'].count('POST', 'dispatches') == 2
    assert mock.channels().count('claude:send-message') == 1
    reports.access(ops, OWNER, w['workspace_id'], old_refs)
    reports.access(ops, OWNER, w['workspace_id'], new_refs)
    await remove_repo(ops, w['child']['params']['project_id'])
    for references in (old_refs, new_refs):
        with pytest.raises(OperationError):
            reports.access(ops, OWNER, w['workspace_id'], references)
