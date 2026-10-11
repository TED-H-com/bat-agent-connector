"""Fixed maintenance reports, explicit discussion and source-gated decisions."""
from __future__ import annotations

import sqlite3
import uuid
from types import SimpleNamespace

import pytest

from bat_agent_connector import pm_brief_exports, pm_lifecycle_gates, pm_plans, pm_snapshots
from bat_agent_connector import pm_maintenance_review as review
from bat_agent_connector import pm_maintenance_review_sources as sources
from bat_agent_connector.api_auth import Principal
from bat_agent_connector.operations import OpContext, OperationError
from tests import test_pm_github_observation as gh
from tests.test_pm_evidence import CONTRIBUTOR, OWNER, READER
from tests.test_pm_release_baselines import execute, remove_repo

base_ops = gh.base_ops
release_ops = gh.release_ops
workload_ops = gh.workload_ops
github_ops = gh.ops
NOW = gh.NOW


@pytest.fixture
def ops(github_ops, monkeypatch):
    review.install(github_ops)
    # Per-module clocks preserve stdlib and the offline provider's original clock.
    clock = SimpleNamespace(time=lambda: NOW + 10)
    monkeypatch.setattr(review, 'time', clock)
    monkeypatch.setattr(pm_snapshots, 'time', clock)
    return github_ops


def request(ops, data, **params):
    capture = gh.worktests.capture_request(ops, data)
    return {'action': review.PREFIX + 'prepare', 'target': {'workspace_id': data['workspace_id']},
        'params': {'title': 'Maintenance review 維護檢討', 'period_start': NOW - 7 * 86400,
            'period_end': NOW + 1, **params}, 'preconditions': {
            **capture['preconditions'], 'expected_policy_id': capture['params']['policy_id'],
            'expected_plan_version': pm_plans.head(ops.db, data['workspace_id'])['version'],
            'expected_review_revision': review.head(ops.db, data['workspace_id'])['revision']},
        'idempotency_key': uuid.uuid4().hex}


async def prepare(ops, data, **params):
    operation = await execute(ops, request(ops, data, **params))
    return operation, review.get_review(ops, OWNER, data['workspace_id'], operation['result']['review_id'])


def mutation(ops, data, rid, action, params):
    return {'action': review.PREFIX + action, 'target': {'workspace_id': data['workspace_id'], 'review_id': rid},
        'params': params, 'preconditions': {'expected_version': review._row(ops.db, data['workspace_id'], rid)['version']},
        'idempotency_key': uuid.uuid4().hex}


async def note(ops, data, rid, *, who=OWNER, content='Discuss missing weekly coverage before selecting work'):
    return await execute(ops, mutation(ops, data, rid, 'note', {'content': content, 'rationale': 'Owner discussion'}), who=who)


def completion(notes, **updates):
    return {'acknowledged_note_ids': notes, 'discussion_acknowledged': True,
        'discussion_summary': 'We reviewed the fixed report and its source gaps.', 'direction': 'continue_maintenance',
        'direction_rationale': 'Observe before proposing repair', 'follow_ups': [],
        'next_observation_focus': ['Check missing provider coverage'], 'rationale': 'Record this discussion', **updates}


async def test_one_immutable_snapshot_preparation_is_not_a_meeting_or_execution(ops, monkeypatch):
    data = await gh.prepared(ops, monkeypatch)
    body = request(ops, data)
    before = ops.db.execute('PRAGMA user_version').fetchone()[0]
    review.install(ops)
    calls = list(ops.context['github'].calls)
    op = await execute(ops, body)
    read = review.get_review(ops, READER, data['workspace_id'], op['result']['review_id'])
    report = read['snapshot']['maintenance']['report']
    assert read['review']['status'] == 'prepared' and read['review']['version'] == 1
    assert read['completion'] is None and read['events'] == []
    assert report['report_status'] == 'data_gaps'
    assert report['coverage']['github']['status'] == 'unchecked'
    assert report['coverage']['workload']['status'] == 'unchecked'
    assert report['coverage']['checks']['status'] == 'not_configured'
    assert report['changes']['status'] == 'comparison_unavailable'
    assert report['changes']['whole_period_no_events_proven'] is False
    assert report['meeting_completed'] is False and report['scope_adopted'] is False
    assert report['effort']['report_model_requests'] == 0
    assert read['snapshot']['snapshot_id'] == 'pmsnap_' + op['operation_id'][3:]
    assert len(pm_snapshots.list_snapshots(ops, READER, data['workspace_id'])['snapshots']) == 1
    assert ops.db.execute('SELECT COUNT(*) FROM pm_sprints').fetchone()[0] == 0
    assert ops.db.execute('SELECT COUNT(*) FROM tasks').fetchone()[0] == 0
    assert ops.db.execute('PRAGMA user_version').fetchone()[0] == before
    assert ops.context['github'].calls == calls and ops.context['github'].reads == []
    assert review.operation_visible(ops.db, READER, op)
    replay, created = ops.create(OWNER, **body)
    assert not created and replay['result'] == op['result']
    assert len(review.list_reviews(ops, READER, data['workspace_id'])['reviews']) == 1
    for table in ('pm_document_snapshots', 'pm_maintenance_reviews'):
        with pytest.raises(sqlite3.IntegrityError, match='immutable'):
            ops.db.execute(f'DELETE FROM {table}')


@pytest.mark.parametrize('params', [
    {'period_start': True}, {'period_end': float('nan')}, {'period_end': NOW + 11},
    {'period_start': NOW + 1}, {'period_start': NOW - 32 * 86400},
    {'period_start': 'yesterday'}, {'period_end': 2**60},
])
async def test_explicit_past_bounded_period(ops, monkeypatch, params):
    data = await gh.prepared(ops, monkeypatch)
    with pytest.raises(OperationError):
        ops.create(OWNER, **request(ops, data, **params))
    assert not review.list_reviews(ops, OWNER, data['workspace_id'])['reviews']


async def test_point_samples_do_not_prove_whole_period_no_events(ops, monkeypatch):
    data = await gh.prepared(ops, monkeypatch)
    gh.worktests.inventory(ops)
    await gh.observe(ops, data)
    await gh.worktests.capture(ops, data)
    _, first = await prepare(ops, data)
    assert first['snapshot']['maintenance']['report']['changes']['status'] == 'comparison_unavailable'
    await gh.observe(ops, data)
    _, second = await prepare(ops, data)
    report = second['snapshot']['maintenance']['report']
    assert report['changes']['status'] == 'no_observed_changes'
    assert report['report_status'] == 'no_observed_changes'
    assert report['coverage']['github']['samples'] == 2
    assert report['coverage']['github']['period_continuity'] == 'unproven'
    assert report['changes']['whole_period_no_events_proven'] is False
    assert report['coverage']['issues'] == 'not_observed'
    assert report['coverage']['research'] == 'not_performed'
    assert report['coverage']['quality'] == 'unassessed'


async def test_changed_missing_and_stale_samples_never_invent_resolved_work(ops, monkeypatch):
    data = await gh.prepared(ops, monkeypatch)
    gh.populate(ops.context['github'])
    await gh.observe(ops, data)
    _, first = await prepare(ops, data)
    ops.context['github'].pulls = []
    ops.context['github'].runs[0]['run_attempt'] += 1
    await gh.observe(ops, data)
    _, second = await prepare(ops, data, previous_review_id=first['review']['review_id'])
    report = second['snapshot']['maintenance']['report']
    assert report['changes']['status'] == 'observed_changes'
    assert 'pull/71' in report['changes']['no_longer_observed']
    assert 'action/75' in report['changes']['changed']
    assert report['changes']['resolution_assessed'] is False
    assert report['previous_review']['unattended_report_remains_pending'] is True
    assert first['review']['status'] == 'prepared'
    monkeypatch.setattr(review, 'time', SimpleNamespace(time=lambda: NOW + 1000))
    _, stale = await prepare(ops, data)
    assert stale['snapshot']['maintenance']['report']['coverage']['github']['status'] == 'stale'


async def test_discussion_candidate_completion_and_followup_are_attributed_unadopted_records(ops, monkeypatch):
    data = await gh.prepared(ops, monkeypatch)
    _, initial = await prepare(ops, data)
    rid = initial['review']['review_id']
    sid = initial['snapshot']['maintenance']['report']['sources'][0]['source_id']
    n = await note(ops, data, rid, who=CONTRIBUTOR)
    candidate = await execute(ops, mutation(ops, data, rid, 'candidate.record', {
        'title': 'Narrow a verification gap', 'problem': 'The saved workload lacks dependency coverage',
        'goal_relation': 'Preserve predictable maintenance', 'expected_effect': 'Make uncertainty visible',
        'options': ['Investigate a scoped read'], 'hypotheses': ['One bounded source may help'],
        'source_refs': [sid], 'rationale': 'Sourced proposal', 'public_sources': [{
            'url': 'https://example.invalid/research', 'title': 'Attributed claim', 'checked_at': NOW,
            'claim': 'A person reports that this source was inspected'}]}))
    before = dict(pm_lifecycle_gates.head(ops.db, data['workspace_id']))
    done = await execute(ops, mutation(ops, data, rid, 'complete', completion([n['result']['event_id']],
        candidate_ids=[candidate['result']['event_id']], follow_ups=[{'id': 'coverage', 'text': 'Inspect gap',
            'owner_actor': CONTRIBUTOR.actor, 'expected_effect': 'Explain missing source',
            'verification': 'Attach saved evidence', 'source_refs': [sid]}])))
    assert done['result']['version'] == 4 and done['result']['meeting_completed']
    update = await execute(ops, mutation(ops, data, rid, 'follow_up.record', {
        'follow_up_id': 'coverage', 'status': 'reported_done', 'result': 'Reported checked; result still needs assessment',
        'rationale': 'Attributed update'}), who=CONTRIBUTOR)
    view = review.get_review(ops, OWNER, data['workspace_id'], rid)
    assert view['review']['status'] == 'completed' and view['review']['version'] == 5
    assert view['snapshot']['snapshot_digest'] == initial['snapshot']['snapshot_digest']
    assert view['snapshot']['maintenance']['report']['meeting_completed'] is False
    assert view['completion']['document']['acknowledged_by'] == OWNER.actor
    assert view['completion']['document']['attendance_verified'] is False
    assert view['completion']['document']['scope_adopted'] is False
    assert view['events'][1]['document']['public_sources'][0]['verification'] == 'attributed_unverified_claim'
    assert view['events'][-1]['document']['verification_status'] == 'unassessed'
    assert view['events'][-1]['operation_id'] == update['operation_id']
    assert dict(pm_lifecycle_gates.head(ops.db, data['workspace_id'])) == before
    assert ops.db.execute('SELECT COUNT(*) FROM tasks').fetchone()[0] == 0
    with pytest.raises(sqlite3.IntegrityError, match='immutable'):
        ops.db.execute('DELETE FROM pm_maintenance_review_events')
    with pytest.raises(OperationError, match='cannot be rewritten'):
        ops.create(OWNER, **mutation(ops, data, rid, 'note', {'content': 'Rewrite', 'rationale': 'Late'}))


@pytest.mark.parametrize('who', [CONTRIBUTOR, READER, Principal('admin', frozenset(), admin=True)])
async def test_completion_needs_actual_current_owner_without_admin_escalation(ops, monkeypatch, who):
    data = await gh.prepared(ops, monkeypatch)
    _, view = await prepare(ops, data)
    rid = view['review']['review_id']
    n = await note(ops, data, rid)
    with pytest.raises(OperationError):
        ops.create(who, **mutation(ops, data, rid, 'complete', completion([n['result']['event_id']])))


@pytest.mark.parametrize('updates', [{'acknowledged_note_ids': []}, {'discussion_acknowledged': False}, {'discussion_summary': ''}])
async def test_generated_report_cannot_complete_without_saved_discussion_and_acknowledgment(ops, monkeypatch, updates):
    data = await gh.prepared(ops, monkeypatch)
    _, view = await prepare(ops, data)
    rid = view['review']['review_id']
    n = await note(ops, data, rid)
    with pytest.raises(OperationError):
        ops.create(OWNER, **mutation(ops, data, rid, 'complete', completion([n['result']['event_id']], **updates)))


async def test_original_source_revocation_hides_report_snapshot_metadata_exports_and_operations(ops, monkeypatch):
    data = await gh.prepared(ops, monkeypatch)
    op, view = await prepare(ops, data)
    rid, sid = view['review']['review_id'], view['snapshot']['snapshot_id']
    note_op = await note(ops, data, rid)
    for fmt in ('markdown', 'html', 'svg'):
        assert pm_brief_exports.render_brief(ops, READER, data['workspace_id'], sid, format=fmt)
    await remove_repo(ops, data['project_id'])
    for read in (
        lambda: review.get_review(ops, OWNER, data['workspace_id'], rid),
        lambda: review.list_events(ops, OWNER, data['workspace_id'], rid),
        lambda: pm_snapshots.get_snapshot(ops, OWNER, data['workspace_id'], sid),
        lambda: pm_snapshots.latest_snapshot(ops, OWNER, data['workspace_id']),
        *(lambda fmt=fmt: pm_brief_exports.render_brief(ops, OWNER, data['workspace_id'], sid, format=fmt)
          for fmt in ('markdown', 'html', 'svg')),
    ):
        with pytest.raises(OperationError) as error:
            read()
        assert error.value.status in (403, 404)
    assert review.current_review(ops, OWNER, data['workspace_id'])['visibility_filtered']
    assert review.list_reviews(ops, OWNER, data['workspace_id'])['reviews'] == []
    assert pm_snapshots.list_snapshots(ops, OWNER, data['workspace_id'])['snapshots'] == []
    assert not review.operation_visible(ops.db, OWNER, op)
    assert not review.operation_visible(ops.db, OWNER, note_op)
    # Durable original effect recovery never calls the source builder again.
    context = OpContext(ops, ops.get(op['operation_id']))
    assert await review._run(context) == op['result']
    assert context.replayed == ['pm.maintenance_review.record']


async def test_late_owner_loss_and_review_revision_races_fail_before_any_record(ops, monkeypatch):
    data = await gh.prepared(ops, monkeypatch)
    a, b = request(ops, data), request(ops, data)
    first, second = ops.create(OWNER, **a)[0], ops.create(OWNER, **b)[0]
    await ops._execute(first['operation_id'])
    await ops._execute(second['operation_id'])
    assert ops.get(first['operation_id'])['status'] == 'succeeded'
    assert ops.get(second['operation_id'])['error_code'] == 'VERSION_CONFLICT'
    rid = ops.get(first['operation_id'])['result']['review_id']
    n = await note(ops, data, rid)
    done = ops.create(OWNER, **mutation(ops, data, rid, 'complete', completion([n['result']['event_id']])))[0]
    with ops.journal.tx():
        ops.db.execute("UPDATE pm_workspace_members SET role='contributor' WHERE workspace_id=? AND actor=?", (data['workspace_id'], OWNER.actor))
    await ops._execute(done['operation_id'])
    assert ops.get(done['operation_id'])['error_code'] == 'FORBIDDEN'
    assert review.get_review(ops, OWNER, data['workspace_id'], rid)['review']['status'] == 'prepared'


async def test_whole_utf8_events_paginate_without_partial_text_or_cursor_loop(ops, monkeypatch):
    data = await gh.prepared(ops, monkeypatch)
    _, view = await prepare(ops, data)
    rid = view['review']['review_id']
    for i in range(3):
        await note(ops, data, rid, content=str(i) + '討論' * 100)
    full = review.list_events(ops, OWNER, data['workspace_id'], rid)
    one = {**full, 'events': full['events'][:1], 'next_cursor': 1}
    monkeypatch.setattr(review, 'MAX_READ_BYTES', sources.baselines._size(one) + 20)
    ids, cursor = [], 0
    for _ in range(5):
        page = review.list_events(ops, OWNER, data['workspace_id'], rid, after=cursor)
        assert sources.baselines._size(page) <= review.MAX_READ_BYTES
        ids.extend(row['event_id'] for row in page['events'])
        if page['next_cursor'] is None:
            break
        assert page['next_cursor'] > cursor
        cursor = page['next_cursor']
    assert ids == [row['event_id'] for row in full['events']]
    monkeypatch.setattr(review, 'MAX_READ_BYTES', 100)
    with pytest.raises(OperationError) as error:
        review.list_events(ops, OWNER, data['workspace_id'], rid)
    assert error.value.status == 413


@pytest.mark.parametrize('field,value', [('MAX_ROWS', 1), ('MAX_SOURCE_BYTES', 1)])
async def test_report_source_bounds_expose_coverage_gaps(ops, monkeypatch, field, value):
    data = await gh.prepared(ops, monkeypatch)
    await gh.observe(ops, data)
    await gh.observe(ops, data)
    monkeypatch.setattr(sources, field, value)
    _, view = await prepare(ops, data)
    coverage = view['snapshot']['maintenance']['report']['coverage']['github']
    assert coverage['truncated'] and coverage['status'] == 'incomplete'


async def test_completed_check_keeps_original_child_sources_and_cost_unknown(ops, monkeypatch):
    from bat_agent_connector import api_auth
    from bat_agent_connector import pm_maintenance_checks as checks
    from tests.test_pm_lifecycle import decide
    from tests.test_pm_maintenance_checks import check
    checks.install(ops)
    monkeypatch.setattr(checks, 'time', SimpleNamespace(time=lambda: NOW))
    data = await gh.prepared(ops, monkeypatch)
    await decide(ops, data['workspace_id'], 'set_stage', params={'stage': 'development'})
    api_auth.issue(ops.db, OWNER.actor, ['observe', 'manage'])
    gh.worktests.inventory(ops)
    parent, run = await check(ops, data)
    _, view = await prepare(ops, data)
    report = view['snapshot']['maintenance']['report']
    assert report['coverage']['checks']['status'] == 'observed'
    assert report['effort']['completed_checks'] == 1 and report['effort']['charged_microusd'] is None
    assert report['checks'][0]['run_id'] == run['run_id']
    assert report['checks'][0]['children'] == run['document']['children']
    source = next(ref for ref in report['sources'] if ref['kind'] == 'maintenance_check')
    assert source['digest'] == run['document_digest'] and source['basis'] == 'final_document'
    assert checks.operation_visible(ops.db, READER, parent)


async def test_different_scope_disabled_policy_preserves_gap_and_does_not_compare(ops, monkeypatch):
    from tests.test_pm_maintenance_policy import request as policy_request
    data = await gh.prepared(ops, monkeypatch)
    await gh.observe(ops, data)
    _, initial = await prepare(ops, data)
    changed = gh.worktests.settings()
    changed['enabled'] = False
    changed['observation'].pop('github_profile')
    await execute(ops, policy_request(ops, data['workspace_id'], document=changed))
    _, view = await prepare(ops, data, previous_review_id=initial['review']['review_id'])
    report = view['snapshot']['maintenance']['report']
    assert report['coverage']['github']['status'] == 'disabled'
    assert report['coverage']['github']['other_scope_records']
    assert report['changes']['scope_changed'] and report['changes']['status'] == 'comparison_unavailable'
    assert report['previous_review']['unattended_report_remains_pending']


async def test_snapshot_project_evidence_loss_withholds_whole_report_and_derived_comparison(ops, monkeypatch):
    import json

    from tests.test_pm_evidence import OUTSIDER, bind_work, capture, item, link, source_operation, workspace
    data = await gh.prepared(ops, monkeypatch)
    work = await item(ops, data['project_id'])
    target = {'workspace_id': data['workspace_id'], 'work_item_id': work}
    await bind_work(ops, target)
    original = await source_operation(ops)
    await link(ops, target, 'operation', original['operation_id'])
    await capture(ops, target)
    operation, initial = await prepare(ops, data)
    assert initial['snapshot']['evidence']
    _, derived = await prepare(ops, data, previous_review_id=initial['review']['review_id'])
    private = await workspace(ops, OUTSIDER)
    ops.db.execute("UPDATE operations SET action='pm.private',target=? WHERE operation_id=?",
        (json.dumps({'workspace_id': private['workspace_id']}), original['operation_id']))
    for value in (initial, derived):
        with pytest.raises(OperationError):
            review.get_review(ops, OWNER, data['workspace_id'], value['review']['review_id'])
        with pytest.raises(OperationError):
            pm_snapshots.get_snapshot(ops, OWNER, data['workspace_id'], value['snapshot']['snapshot_id'])
    assert not review.operation_visible(ops.db, OWNER, operation)
    assert pm_snapshots.list_snapshots(ops, OWNER, data['workspace_id'])['snapshots'] == []


@pytest.mark.parametrize('field,value', [('source_refs', []), ('source_refs', ['pmrs_unknown']),
    ('public_sources', [{'url': 'https://[broken', 'title': 'x', 'checked_at': NOW, 'claim': 'x'}])])
async def test_candidate_requires_original_sources_and_valid_attributed_reference(ops, monkeypatch, field, value):
    data = await gh.prepared(ops, monkeypatch)
    _, view = await prepare(ops, data)
    params = {'title': 'Idea', 'problem': 'Gap', 'goal_relation': 'Improve confidence',
        'expected_effect': 'More visibility', 'options': [], 'hypotheses': [],
        'source_refs': [view['snapshot']['maintenance']['report']['sources'][0]['source_id']],
        'rationale': 'Proposal', field: value}
    with pytest.raises(OperationError):
        ops.create(OWNER, **mutation(ops, data, view['review']['review_id'], 'candidate.record', params))


async def test_final_effect_source_loss_and_cancel_publish_nothing(ops, monkeypatch):
    data = await gh.prepared(ops, monkeypatch)
    admitted = ops.create(OWNER, **request(ops, data))[0]
    await remove_repo(ops, data['project_id'])
    await ops._execute(admitted['operation_id'])
    assert ops.get(admitted['operation_id'])['status'] == 'failed'
    assert ops.db.execute('SELECT COUNT(*) FROM pm_document_snapshots').fetchone()[0] == 0
    assert ops.db.execute('SELECT COUNT(*) FROM pm_maintenance_reviews').fetchone()[0] == 0
