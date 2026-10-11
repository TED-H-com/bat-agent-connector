"""Fixed quality conclusions flow into Sprint, evidence, Kanban and Brief."""
from __future__ import annotations

import copy
import datetime as dt
import json
import uuid
import xml.etree.ElementTree as ET
from types import SimpleNamespace

import pytest

from bat_agent_connector import (
    pm_access,
    pm_brief_exports,
    pm_evidence,
    pm_kanban,
    pm_plans,
    pm_snapshots,
    pm_sprints,
    pm_workspace,
)
from bat_agent_connector import pm_quality_reviews as quality
from bat_agent_connector.operations import OperationError
from tests import test_pm_quality_reviews as base
from tests.test_pm_plan_operations import OWNER

world, pm, ready, fixed, reviewed = base.world, base.pm, base.ready, base.fixed, base.reviewed


def envelope(action, target, params, pre):
    return dict(action=action, target=target, params=params, preconditions=pre, idempotency_key=uuid.uuid4().hex)


async def qualified(w):
    row = await base.new_review(w)
    op = await base.execute(w, base.assessment(w, row), actor=base.REVIEWER)
    return {key: op['result'][key] for key in ('review_id', 'version', 'document_digest')} | {
        'operation_id': op['operation_id'], 'candidate_commit': w['candidate']}


async def capture(w, reference=None):
    binding = pm_evidence._binding(w['d'].ops.db, w['workspace_id'], w['target']['work_item_id'])
    return await base.execute(w, envelope('pm.evidence.capture', w['target'],
        {'quality_ref': reference} if reference else {}, {'expected_binding_id': binding['binding_id']}))


async def snapshot(w):
    ops, wid = w['d'].ops, w['workspace_id']
    op = await base.execute(w, envelope('pm.snapshot.create', {'workspace_id': wid}, {},
        {'expected_plan_version': pm_plans.head(ops.db, wid)['version']}))
    return pm_snapshots.get_snapshot(ops, OWNER, wid, op['result']['snapshot_id'])['snapshot']


async def sprint(w, monkeypatch, *, dod=None):
    ops, wid = w['d'].ops, w['workspace_id']
    binding = pm_evidence._binding(ops.db, wid, w['target']['work_item_id'])
    goal = (await base.execute(w, envelope('pm.product_goal.revise', {'workspace_id': wid},
        {'plan_revision_id': binding['plan_revision_id'], 'text': 'Verify original login result', 'rationale': 'Fixed goal'},
        {'expected_revision_id': None})))['result']['revision_id']
    backlog = (await base.execute(w, envelope('pm.backlog.revise', {'workspace_id': wid},
        {'product_goal_revision_id': goal, 'binding_ids': [binding['binding_id']], 'rationale': 'Selected original work'},
        {'expected_revision_id': None})))['result']['revision_id']
    start = dt.datetime(2026, 10, 11, tzinfo=dt.timezone.utc)
    end = start + dt.timedelta(days=7)
    monkeypatch.setattr(pm_sprints, 'time', SimpleNamespace(time=lambda: start.timestamp() + 60))
    op = await base.execute(w, envelope('pm.sprint.create', {'workspace_id': wid}, {
        'title': 'Original login', 'mode': 'personal_iteration', 'owner_actor': OWNER.actor, 'developers': [OWNER.actor],
        'backlog_revision_id': backlog, 'dod_revision_id': dod or w['dod'], 'goal': 'Login meets original acceptance',
        'starts_at': start.isoformat(), 'ends_at': end.isoformat(), 'capacity_note': 'One result', 'rationale': 'Fixed iteration'},
        {'expected_workspace_version': pm_workspace.get_workspace(ops.db, OWNER, wid)['version']}))
    target = {'workspace_id': wid, 'sprint_id': op['result']['sprint_id']}
    await base.execute(w, envelope('pm.sprint.select', target,
        {'items': [{'binding_id': binding['binding_id'], 'assignee': OWNER.actor, 'how': 'Verify the fixed result'}], 'rationale': 'Original selection'},
        {'expected_version': 1}))
    await base.execute(w, envelope('pm.sprint.start', target, {'rationale': 'Begin fixed timebox'}, {'expected_version': 2}))
    monkeypatch.setattr(pm_sprints, 'time', SimpleNamespace(time=lambda: end.timestamp()))
    return target


async def close(w, target, reference):
    return await base.execute(w, envelope('pm.sprint.close', target,
        {'rationale': 'Close the ended timebox', 'quality_refs': {w['target']['work_item_id']: reference}}, {'expected_version': 3}))


async def revoke(w, reference):
    return await base.execute(w, envelope(quality.REVOKE, {'workspace_id': w['workspace_id'], 'review_id': reference['review_id']},
        {'reason': 'New counterexample changes future applicability'}, {'expected_version': reference['version']}))


def withdraw_proof(w):
    ops = w['d'].ops
    task = ops.db.execute('SELECT task_id FROM observed_verifications WHERE verification_id=?', (w['proof']['verification_id'],)).fetchone()[0]
    ops.db.execute("UPDATE tasks SET controller_authority=json_set(controller_authority,'$.pm_workspace_id',?) WHERE task_id=?", ('ws_' + 'f' * 32, task))


async def test_exact_quality_reaches_all_views_and_closed_sprint_stays_fixed_after_revocation(reviewed, monkeypatch):
    w, ops = reviewed, reviewed['d'].ops
    ref = await qualified(w)
    await capture(w, ref)
    first = await snapshot(w)
    assert first['brief']['delivery']['quality']['qualified_requirements'] == 1
    assert first['brief']['delivery']['quality']['total_requirements'] == 1
    for locale in ('en', 'zh-TW'):
        for format in ('markdown', 'html', 'svg'):
            exported = pm_brief_exports.render_brief(ops, OWNER, w['workspace_id'], first['snapshot_id'], format=format, locale=locale)
            assert exported['renderer_version'] == 3 and exported['view_digest'] == first['view_digest']
            content = ''.join(ET.fromstring(exported['content']).itertext()) if format == 'svg' else exported['content']
            assert '1 / 1' in content and ref['document_digest'] in ''.join(content.split())
    binding = pm_evidence._binding(ops.db, w['workspace_id'], w['target']['work_item_id'])
    card = pm_kanban._card(ops, OWNER, w['workspace_id'], binding, 0)
    assert card['quality']['qualified'] and not card['completion']['approved']
    target = await sprint(w, monkeypatch)
    await close(w, target, ref)
    before = pm_sprints.get_sprint(ops, OWNER, **target)
    assert before['quality_summary']['qualified_items'] == 1
    item = before['history'][-1]['document']['items'][0]
    assert item['counts_as_done'] and item['quality_assessment']['reference'] == ref
    assert not before['history'][-1]['document']['review_completed']
    await revoke(w, ref)
    assert pm_sprints.get_sprint(ops, OWNER, **target) == before
    assert pm_snapshots.get_snapshot(ops, OWNER, w['workspace_id'], first['snapshot_id'])['snapshot'] == first
    assert not pm_kanban._card(ops, OWNER, w['workspace_id'], binding, 0)['quality']['qualified']
    await capture(w, ref)
    later = await snapshot(w)
    assert later['brief']['delivery']['quality']['qualified_requirements'] == 0
    assert later['brief']['delivery']['quality']['requirements'][0]['acceptance'] == 'unassessed'


async def test_proof_withdrawal_hides_frozen_quality_and_original_operations_events(reviewed, monkeypatch):
    w, ops = reviewed, reviewed['d'].ops
    ref = await qualified(w)
    captured = await capture(w, ref)
    frozen = await snapshot(w)
    target = await sprint(w, monkeypatch)
    closed = await close(w, target, ref)
    withdraw_proof(w)
    for op in (captured, closed):
        assert not pm_access.visible(ops.db, OWNER, op)
        assert not pm_access.event_visible(ops.db, OWNER, {'resource_type': 'pm_sprint', 'kind': op['action'],
            'body': {'workspace_id': w['workspace_id'], 'operation_id': op['operation_id']}})
    with pytest.raises(OperationError) as hidden:
        pm_sprints.get_sprint(ops, OWNER, **target, history_limit=1)
    assert hidden.value.status == 404
    with pytest.raises(OperationError):
        pm_sprints.list_history(ops, OWNER, **target, kind='events')
    current = pm_evidence.latest_capture(ops, OWNER, w['workspace_id'], w['target']['work_item_id'])['capture']
    assert current['coverage']['inaccessible_sources']
    assert not any(value['source_kind'] == 'quality_assessment' for value in current['evidence'])
    read = pm_snapshots.get_snapshot(ops, OWNER, w['workspace_id'], frozen['snapshot_id'])['snapshot']
    assert read['snapshot_digest'] == frozen['snapshot_digest'] and read['view_digest'] != frozen['view_digest']
    assert ref['review_id'] not in json.dumps(read)


async def test_sprint_original_dod_and_explicit_latest_assessment_are_required(reviewed, monkeypatch):
    w, ops = reviewed, reviewed['d'].ops
    ref = await qualified(w)
    row = quality.get_review(ops, OWNER, w['workspace_id'], ref['review_id'])['review']
    await base.execute(w, base.assessment(w, row, verdict='rejected'), actor=base.REVIEWER)
    with pytest.raises(OperationError, match='latest conclusion'):
        await capture(w, ref)
    newer = quality.get_review(ops, OWNER, w['workspace_id'], ref['review_id'])['review']
    ref = {**ref, **{key: newer['assessment'][key] for key in ('operation_id', 'version', 'document_digest')}}
    target = await sprint(w, monkeypatch)
    await close(w, target, ref)
    closed = pm_sprints.get_sprint(ops, OWNER, **target)['history'][-1]['document']['items'][0]
    assert not closed['counts_as_done'] and closed['quality_assessment']['acceptance'] == 'rejected'


async def test_brief_deduplicates_requirements_and_unreviewed_contributions_remain_unknown(reviewed):
    w = reviewed
    await capture(w, await qualified(w))
    frozen = await snapshot(w)
    document = {key: copy.deepcopy(frozen[key]) for key in ('baseline', 'work', 'evidence', 'coverage')}
    # Two explicit work contributions for one requirement are one denominator.
    extra = copy.deepcopy(document['work'][0])
    extra['work_item_id'] = 'wi_' + 'e' * 20
    extra['binding']['work_item_id'] = extra['work_item_id']
    extra['binding']['binding_id'] = 'pmwb_' + 'e' * 32
    extra['evidence_ids'] = []
    document['work'].append(extra)
    summary = pm_snapshots._quality_summary(document)
    assert summary['total_requirements'] == 1 and summary['qualified_requirements'] == 0
    assert summary['requirements'][0]['acceptance'] == 'unassessed'
    document['work'].pop()
    document['coverage']['work_truncated'] = True
    assert pm_snapshots._quality_summary(document)['qualified_requirements'] == 0


async def test_changed_sprint_dod_cannot_count_an_otherwise_accepted_result(reviewed, monkeypatch):
    w, ops = reviewed, reviewed['d'].ops
    ref = await qualified(w)
    binding = pm_evidence._binding(ops.db, w['workspace_id'], w['target']['work_item_id'])
    revised = await base.execute(w, envelope('pm.dod.revise', {'workspace_id': w['workspace_id']}, {
        'plan_revision_id': binding['plan_revision_id'], 'criteria': [{'id': 'accessibility', 'text': 'Keyboard flow independently verified'}],
        'rationale': 'A separate explicit quality commitment'}, {'expected_revision_id': w['dod']}))
    target = await sprint(w, monkeypatch, dod=revised['result']['revision_id'])
    await close(w, target, ref)
    item = pm_sprints.get_sprint(ops, OWNER, **target)['history'][-1]['document']['items'][0]
    assert not item['counts_as_done']
    assert item['quality_assessment']['original_outcome']['qualified']
    assert 'dod_revision_id_changed' in item['quality_assessment']['applicability_reasons']


async def test_new_conclusion_between_close_admission_and_final_frame_is_not_substituted(reviewed, monkeypatch):
    w, ops = reviewed, reviewed['d'].ops
    ref = await qualified(w)
    target = await sprint(w, monkeypatch)
    env = envelope('pm.sprint.close', target, {'rationale': 'Close with reviewed originals',
        'quality_refs': {w['target']['work_item_id']: ref}}, {'expected_version': 3})
    original = ops.create(OWNER, **env)[0]
    review = quality.get_review(ops, OWNER, w['workspace_id'], ref['review_id'])['review']
    next_op = ops.create(base.REVIEWER, **base.assessment(w, review, verdict='rejected'))[0]
    await ops._execute(next_op['operation_id'])
    await ops._execute(original['operation_id'])
    assert ops.get(original['operation_id'])['error_code'] == 'PM_QUALITY_VERSION_CONFLICT'
    assert pm_sprints.get_sprint(ops, OWNER, **target)['sprint']['status'] == 'active'
