"""Quality evidence remains readable and revocable across management discussions."""
from __future__ import annotations

import pytest

from bat_agent_connector import pm_governance, pm_meetings, pm_plans
from tests import test_pm_brief_quality_deviations as brief
from tests import test_pm_meetings as meetings
from tests import test_pm_quality_projections as shared
from tests import test_pm_quality_reviews as base
from tests.test_pm_plan_operations import OWNER

world, pm, ready, fixed, reviewed = shared.world, shared.pm, shared.ready, shared.fixed, shared.reviewed


@pytest.mark.parametrize('kind', ['governance', 'meeting'])
async def test_quality_discussion_original_sources_are_read_only_and_withdraw_together(reviewed, monkeypatch, kind):
    w, ops = reviewed, reviewed['d'].ops
    wid = w['workspace_id']
    await shared.capture(w, await brief.rejected(w))
    frozen = await shared.snapshot(w)
    if kind == 'governance':
        evidence = next(row for row in frozen['evidence'] if row['source_kind'] == 'quality_assessment')
        binding = frozen['work'][0]['binding']
        request = shared.envelope('pm.governance.record', {'workspace_id': wid}, {
            'kind': 'drift', 'root_cause_key': 'selected-quality-rejection', 'snapshot_id': frozen['snapshot_id'],
            'title': 'Selected quality was rejected', 'description': 'The reviewer rejected the original criteria.',
            'dimension': 'quality', 'owner_actor': OWNER.actor,
            'affected_requirement_ids': binding['requirement_ids'],
            'source_refs': [{'kind': 'requirement', 'id': binding['requirement_ids'][0]},
                            {'kind': 'evidence', 'id': evidence['evidence_id']}],
            'rationale': 'Discuss the original rejection without changing its scope.'},
            {'expected_plan_version': pm_plans.head(ops.db, wid)['version']})
        domain = pm_governance
    else:
        target = await shared.sprint(w, monkeypatch)
        request = meetings.create_request(ops, target, frozen, 'review')
        domain = pm_meetings
    operation = await base.execute(w, request)
    before = (ops.db.total_changes, ops.db.in_transaction, ops.db.execute('PRAGMA user_version').fetchone()[0])
    assert domain.operation_visible(ops.db, OWNER, operation)
    assert (ops.db.total_changes, ops.db.in_transaction, ops.db.execute('PRAGMA user_version').fetchone()[0]) == before
    shared.withdraw_proof(w)
    assert not domain.operation_visible(ops.db, OWNER, operation)
