"""Only complete original quality-source decisions may share a read scope."""
import asyncio
import copy
from dataclasses import replace

import pytest

from bat_agent_connector import pm_access, pm_plans, pm_source_reads
from bat_agent_connector import pm_quality_reviews as quality
from bat_agent_connector.operations import OperationError
from tests import test_pm_plan_operations as plans
from tests.test_pm_quality_reviews import (  # noqa: F401
    OWNER,
    REVIEWER,
    assessment,
    execute,
    fixed,
    new_review,
    pm,
    ready,
    reviewed,
    selection,
    withdraw_verifier,
    world,
)


def prior_quality_access(ops, principal, row):
    """Original complete source predicate, before scoped boolean reuse."""
    quality._member(ops, principal, row['workspace_id'])
    current, seen = row, set()
    while current:
        if current['review_id'] in seen or len(seen) >= quality.MAX_HISTORY:
            raise OperationError('PM_QUALITY_LIMIT', 'Original quality history exceeds its bound', 409)
        seen.add(current['review_id'])
        quality.sources.access(ops, principal, row['workspace_id'], current['context'])
        for record in quality._records(ops, current):
            if record['kind'] == 'assessment':
                quality.sources.proof_access(ops, principal, row['workspace_id'], record['document'])
        previous = current['context']['previous_review_id']
        current = quality._review(ops, row['workspace_id'], previous) if previous else None
    return len(seen)


async def test_canonical_quality_history_reuses_only_complete_same_credential_sources(reviewed, monkeypatch):  # noqa: F811
    w, ops = reviewed, reviewed['d'].ops
    first = await new_review(w)
    await execute(w, assessment(w, first), actor=REVIEWER)
    second = await new_review(w, previous=first['review_id'])
    row = quality._review(ops, w['workspace_id'], second['review_id'])
    assert prior_quality_access(ops, OWNER, row) == 2
    source, member, calls = quality.sources.access, quality._member, []
    denied = replace(OWNER, credential_id='other-original-credential')
    def counted(*args):
        calls.append(args[3]['execution_operation_id'])
        return source(*args)
    def credential_member(reader, principal, *args, **kwargs):
        if principal.credential_id == denied.credential_id:
            raise OperationError('NOT_FOUND', 'Original credential cannot read these sources', 404)
        return member(reader, principal, *args, **kwargs)
    monkeypatch.setattr(quality.sources, 'access', counted)
    monkeypatch.setattr(quality, '_member', credential_member)
    with pm_source_reads.scope(ops.db, OWNER):
        assert quality._access(ops, OWNER, row) == 2
        before = len(calls)
        assert before and quality._access(ops, OWNER, row) == 2 and len(calls) == before
        poisoned = copy.deepcopy(row)
        poisoned['context']['operation_ids'] = []
        with pytest.raises(OperationError) as changed:
            quality._access(ops, OWNER, poisoned)
        assert changed.value.status == 404
        with pytest.raises(OperationError) as other:
            quality._access(ops, denied, row)
        assert other.value.status == 404
        assert quality._access(ops, OWNER, row) == 2 and len(calls) == before

    # The integer result still enforces the existing bounded history admission.
    monkeypatch.setattr(quality, 'MAX_HISTORY', 2)
    with pytest.raises(OperationError) as limit:
        ops.create(OWNER, **selection(w, previous=second['review_id']))
    assert limit.value.code == 'PM_QUALITY_LIMIT'
    assert ops.db.execute('SELECT COUNT(*) FROM pm_quality_reviews').fetchone()[0] == 2


async def test_later_original_proof_withdrawal_hides_complete_quality_history(reviewed):  # noqa: F811
    w, ops = reviewed, reviewed['d'].ops
    first = await new_review(w)
    saved = await execute(w, assessment(w, first), actor=REVIEWER)
    second = await new_review(w, previous=first['review_id'])
    row = quality._review(ops, w['workspace_id'], second['review_id'])
    with pm_source_reads.scope(ops.db, OWNER):
        assert quality._access(ops, OWNER, row) == quality._access(ops, OWNER, row) == 2
    await asyncio.sleep(0)
    withdraw_verifier(w)
    with pytest.raises(OperationError) as hidden:
        quality._access(ops, OWNER, row)
    assert hidden.value.status == 404
    assert not quality.operation_visible(ops.db, OWNER, saved)
    current = quality.list_reviews(ops, OWNER, w['workspace_id'])
    assert current['visibility_filtered'] and current['reviews'] == []


async def test_original_quality_dod_does_not_traverse_a_later_plan_head(reviewed, monkeypatch):  # noqa: F811
    w, ops = reviewed, reviewed['d'].ops
    first = await new_review(w)
    saved = await execute(w, assessment(w, first), actor=REVIEWER)
    original = copy.deepcopy(quality.get_review(ops, OWNER, w['workspace_id'], first['review_id']))
    original_revision = first['context']['plan_ref']['revision_id']
    later = await plans.draft(w, markdown='# Later planning\nDiscuss the original assessment.\n')
    latest = pm_plans._revision(ops.db, w['workspace_id'], later['revision_id'])
    assert latest['revision_id'] != original_revision
    assert pm_plans.head(ops.db, w['workspace_id'])['latest_revision_id'] == latest['revision_id']

    # A real newer draft exists. Its display projection is optional and must
    # not enter the original DoD source graph (which can itself feed that draft).
    project, calls = pm_plans._public_head_projection, []
    def projected(*args):
        calls.append(args[2])
        return project(*args)
    monkeypatch.setattr(pm_plans, '_public_head_projection', projected)
    changes = ops.db.total_changes
    for ambient in (False, True):
        def read_original():
            assert quality.get_review(ops, OWNER, w['workspace_id'], first['review_id']) == original
            assert quality.operation_visible(ops.db, OWNER, saved)
        if ambient:
            with pm_source_reads.scope(ops.db, OWNER):
                read_original()
        else:
            read_original()
    assert calls == []
    assert ops.db.total_changes == changes

    # Current public UI reads still project the real head. Hiding only the new
    # draft preserves original quality; hiding original plan sources does not.
    visible = pm_access.operation_source_visible
    hidden = {latest['operation_id']}
    def available(db, principal, operation_id):
        return operation_id not in hidden and visible(db, principal, operation_id)
    monkeypatch.setattr(pm_access, 'operation_source_visible', available)
    historical = pm_plans.get_plan(ops, OWNER, w['workspace_id'], original_revision)
    assert historical['head'] is None and historical['head_unavailable']
    assert historical['plan']['is_current_accepted'] is None
    assert quality.get_review(ops, OWNER, w['workspace_id'], first['review_id']) == original
    original_sources = pm_plans.source_operation_ids(ops.db, w['workspace_id'], original_revision)
    hidden.add(original_sources[0])
    with pytest.raises(OperationError) as withdrawn:
        quality.get_review(ops, OWNER, w['workspace_id'], first['review_id'])
    assert withdrawn.value.status == 404
    assert not quality.operation_visible(ops.db, OWNER, saved)
    events = ops.journal.api_events(resource_id=saved['operation_id'])['events']
    assert events and not any(pm_access.event_visible(ops.db, OWNER, event) for event in events)
    assert ops.db.total_changes == changes
