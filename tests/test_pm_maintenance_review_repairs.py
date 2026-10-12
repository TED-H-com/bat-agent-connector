"""Saved reports retain actual repair receipts and current original-source gates."""
from __future__ import annotations

import copy

import pytest

from bat_agent_connector import pm_maintenance_review_exports as exports
from bat_agent_connector import pm_maintenance_review_sources as sources
from bat_agent_connector.operations import OperationError
from tests import test_pm_maintenance_acceptance as acceptance
from tests.test_pm_release_baselines import remove_repo

base_world = acceptance.base_world
world = acceptance.world
fixed = acceptance.fixed
pm = acceptance.pm
ready = acceptance.ready
repair_ready = acceptance.repair_ready
original_authority = acceptance.original_authority
original_repair = acceptance.original_repair


async def test_original_publication_is_reported_without_inventing_completion(original_repair):
    w, ops = original_repair, original_repair['d'].ops
    assert (await acceptance.publish(w))['status'] == 'succeeded'
    repair = acceptance.read(w)
    intent = repair['context']['check']['workload']
    period = {'start': repair['created_at'] - 1, 'end': repair['created_at'] + 1}
    report, references = sources.build(ops, acceptance.OWNER, w['workspace_id'], intent, period, period['end'])
    sample = report['repairs'][0]
    assert sample['repair_id'] == repair['repair_id']
    assert sample['outcome'] == 'published_for_review' and sample['capacity_held'] is True
    assert sample['runtime_completion'] is None and sample['repair_fixed'] is False
    assert sample['release_status'] == 'not_inferred'
    assert {child['slot'] for child in sample['children']} == {'child', 'publication_child'}
    assert next(child['result'] for child in sample['children'] if child['slot'] == 'publication_child') == repair['publication_child']['result']
    assert report['effort']['repair_counts'] == {'selected': 1, 'capacity_held': 1, 'runtime_completed': 0,
        'delivery_outcomes': {'published_for_review': 1, 'merged': 0, 'deployed': 0}}
    original = copy.deepcopy(report)
    # Old unfinished work remains visible in the next period, with its original
    # admission identity. Reading a later report never updates the saved one.
    later, _ = sources.build(ops, acceptance.OWNER, w['workspace_id'], intent,
        {'start': period['end'] + 1, 'end': period['end'] + 86400}, period['end'] + 86400)
    assert later['repairs'][0]['source_id'] == sample['source_id']
    assert report == original
    for locale, expected in (('en', 'Published for review'), ('zh-TW', '已發布供審查')):
        rendered = exports.blocks({'report': report}, locale)
        assert any(expected in text for _, text in rendered)
    sources.access(ops, acceptance.OWNER, w['workspace_id'], references)
    await remove_repo(ops, w['child']['params']['project_id'])
    with pytest.raises(OperationError):
        sources.access(ops, acceptance.OWNER, w['workspace_id'], references)
