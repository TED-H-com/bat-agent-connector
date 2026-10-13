"""Stable requirement impacts survive registered adoption without rewriting findings."""
from __future__ import annotations

import uuid

import pytest

from bat_agent_connector import (
    pm_discussions,
    pm_governance,
    pm_overview,
    pm_plans,
    pm_snapshots,
    pm_source_lineage,
)
from bat_agent_connector.operations import OperationError
from tests import test_pm_plan_operations as plans
from tests.operation_helpers import settle_operations
from tests.test_checkpoints import bat_writes, snapshot

world, pm, ready = plans.world, plans.pm, plans.ready


async def operate(w, action, target, params, preconditions):
    service = w['d'].ops
    request = {'action': action, 'target': target, 'params': params,
        'preconditions': preconditions, 'idempotency_key': uuid.uuid4().hex}
    original = service.create(plans.OWNER, **request)[0]
    await settle_operations(service)
    done = service.get(original['operation_id'])
    assert done['status'] == 'succeeded', done
    return done, request


async def blocked_plan(w, *, kind='issue'):
    service, wid = w['d'].ops, w['workspace_id']
    thread = pm_discussions.get_thread(service, plans.OWNER, **plans.target(w))
    login = thread['draft']['selected_inputs'][0]['input_id']
    recovery = await plans.act(w, 'pm.input.record', {'slot': 'scope', 'value': 'Members can recover access',
        'status': 'user_confirmed', 'source_ref': {'kind': 'statement', 'ref': 'Owner confirms recovery'}})
    await plans.act(w, 'pm.thread.plan.revise', {'title': 'Member access', 'rationale': 'Confirm the scope',
        'selected_input_ids': [login, recovery['input_id']]},
        pre={'expected_version': plans.version(w), 'expected_draft_id': thread['draft']['draft_id']})
    requirements = [{'id': rid, 'source_input_id': source, 'acceptance_input_ids': [source]}
        for rid, source in [('member.login', login), ('member.recovery', recovery['input_id'])]]
    revision = await plans.draft(w, requirements=requirements)
    assert (await plans.adopt(w, revision))['status'] == 'succeeded'
    saved, _ = await operate(w, 'pm.snapshot.create', {'workspace_id': wid}, {'title': 'Original issue context'},
        {'expected_plan_version': pm_plans.head(service.db, wid)['version']})
    snap = pm_snapshots.get_snapshot(service, plans.OWNER, wid, saved['result']['snapshot_id'])['snapshot']
    finding, _ = await operate(w, 'pm.governance.record', {'workspace_id': wid},
        {'kind': kind, 'root_cause_key': 'access-contract', 'snapshot_id': snap['snapshot_id'],
            'title': 'Access contract needs a decision', 'description': 'Login and recovery remain blocked.',
            'owner_actor': plans.OWNER.actor, 'affected_requirement_ids': [r['id'] for r in requirements],
            'status': 'monitoring' if kind == 'dependency' else 'open',
            'source_refs': [{'kind': 'requirement', 'id': r['id']} for r in requirements],
            'rationale': 'Record the unresolved access contract'},
        {'expected_plan_version': pm_plans.head(service.db, wid)['version']})
    return revision, requirements, snap, finding['result']


async def revise_plan(w, requirements, remaining):
    revision = await plans.draft(w, title='Updated member access',
        requirements=[r for r in requirements if r['id'] in remaining])
    assert (await plans.adopt(w, revision))['status'] == 'succeeded'
    return revision


@pytest.mark.parametrize('kind', ['issue', 'dependency'])
@pytest.mark.parametrize('remaining', [['member.login', 'member.recovery'], ['member.login'], []])
async def test_blockers_follow_stable_ids_and_report_absent_requirements_after_adoption(ready, mock, kind, remaining):
    w, service = ready, ready['d'].ops
    human_before = snapshot(w['human'])
    original, requirements, snap, finding = await blocked_plan(w, kind=kind)
    recorded = pm_governance.get_record(service, plans.OWNER, w['workspace_id'], finding['record_id'])
    before = pm_overview.read(service, plans.OWNER, w['workspace_id'])
    assert before['attention_counts']['blocker'] == 1 and before['progress']['blocked'] == 2
    revision = await revise_plan(w, requirements, remaining)
    changes = service.db.total_changes
    out = pm_overview.read(service, plans.OWNER, w['workspace_id'])
    blockers = [r for r in out['attention'] if r['kind'] == 'blocker']
    assert len(blockers) == out['attention_counts']['blocker'] == 1
    assert blockers[0]['ref'] == {'kind': 'governance', 'id': finding['record_id']}
    assert blockers[0]['context'] == {'requirement_ids': ['member.login', 'member.recovery'],
        'revision_id': original['revision_id'], 'current_revision_id': revision['revision_id'],
        'blocked_requirement_ids': remaining,
        'absent_requirement_ids': [r['id'] for r in requirements if r['id'] not in remaining]}
    progress = out['progress']
    assert progress['complete'] and progress['basis']['revision_id'] == revision['revision_id']
    assert progress['requirements'] == progress['blocked'] == len(remaining)
    assert sum(progress[k] for k in ('accepted', 'in_review', 'in_progress', 'blocked', 'not_started')) == len(remaining)
    assert pm_governance.get_record(service, plans.OWNER, w['workspace_id'], finding['record_id']) == recorded
    assert pm_snapshots.get_snapshot(service, plans.OWNER, w['workspace_id'], snap['snapshot_id'])['snapshot'] == snap
    assert pm_source_lineage.read_response(service.db, plans.OWNER, out) == out
    assert service.db.total_changes == changes
    assert service.db.execute('SELECT count(*) FROM pm_plan_adoptions').fetchone()[0] == 2
    assert service.db.execute('SELECT count(*) FROM tasks').fetchone()[0] == 0
    assert snapshot(w['human']) == human_before and not bat_writes(mock)


async def test_blocker_requirement_relation_is_unknown_when_current_plan_is_unavailable(ready, monkeypatch):
    w, service = ready, ready['d'].ops
    _, requirements, _, _ = await blocked_plan(w)
    revision = await revise_plan(w, requirements, ['member.login'])
    get = pm_plans.get_plan
    def unavailable(ops, principal, wid, rid):
        if rid == revision['revision_id']:
            raise OperationError('PM_SOURCE_UNAVAILABLE', 'Source unavailable', 404)
        return get(ops, principal, wid, rid)
    monkeypatch.setattr(pm_plans, 'get_plan', unavailable)
    out = pm_overview.read(service, plans.OWNER, w['workspace_id'])
    assert out['plan'] is out['progress'] is None
    assert out['attention_counts']['blocker'] == 1
    context = next(r for r in out['attention'] if r['kind'] == 'blocker')['context']
    assert context['requirement_ids'] == ['member.login', 'member.recovery']
    assert context['current_revision_id'] is context['blocked_requirement_ids'] is context['absent_requirement_ids'] is None
