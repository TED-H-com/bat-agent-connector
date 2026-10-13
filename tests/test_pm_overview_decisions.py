"""Owner-reviewed adoption closes Overview's goal and recorded decision loop."""
from __future__ import annotations

import json

import pytest

from bat_agent_connector import (
    pm_discussions,
    pm_governance,
    pm_overview,
    pm_plans,
    pm_profiles,
    pm_register,
    pm_source_lineage,
)
from bat_agent_connector.operations import OperationError
from tests import test_pm_conversation_proposals as conversation
from tests import test_pm_governance as governance
from tests import test_pm_plan_operations as plans
from tests import test_pm_plans as local_plans
from tests.test_checkpoints import bat_writes, snapshot
from tests.test_pm_discussions import OWNER, act, prepared
from tests.test_pm_overview import base_ops, ops  # noqa: F401

world, pm, ready = conversation.world, conversation.pm, conversation.ready
proposal_world = conversation.proposal_world


@pytest.mark.parametrize('goal', ['Members can sign in and recover access', '會員可以登入並重設密碼'])
async def test_conversation_adoption_retains_confirmed_goal_and_register_decision(proposal_world, mock, goal):
    w, service = proposal_world, proposal_world['d'].ops
    recorded = await plans.act(w, 'pm.input.record', {'slot': 'value', 'value': goal,
        'status': 'user_confirmed', 'source_ref': {'kind': 'statement', 'ref': 'Owner confirms the project goal'}})
    draft = pm_discussions.get_thread(service, plans.OWNER, **plans.target(w))['draft']
    await plans.act(w, 'pm.thread.plan.revise', {'title': 'Member access', 'rationale': 'Retain the goal',
        'selected_input_ids': [*[i['input_id'] for i in draft['selected_inputs']], recorded['input_id']]},
        pre={'expected_version': plans.version(w), 'expected_draft_id': draft['draft_id']})
    baseline = await plans.draft(w)
    assert (await plans.adopt(w, baseline))['status'] == 'succeeded'
    assert pm_overview.read(service, plans.OWNER, w['workspace_id'])['plan']['goal'] == goal
    document = conversation.envelope('message.' + w['message']['message_id'])
    document['candidates'].insert(0, {'key': 'goal', 'domain': 'governance', 'text': goal,
        'kind': 'proposal', 'source_refs': ['accepted_plan']})
    w['settings']['raw_reply'] = json.dumps(document, ensure_ascii=False)
    proposal = await conversation.proposed(w)
    assert proposal['document'] is not None
    before = pm_overview.read(service, plans.OWNER, w['workspace_id'])
    assert before['attention_counts']['decision'] == 1
    human_before = snapshot(w['human'])
    request = conversation.adoption_request(w, proposal)
    original = service.create(plans.OWNER, **request)[0]
    done = await conversation.complete(w, original)
    assert done['status'] == 'succeeded', done
    accepted = pm_plans.accepted_plan(service, plans.OWNER, w['workspace_id'])['plan']
    source = next(i for i in accepted['document']['selected_inputs'] if i['slot'] == 'governance.goal')
    assert source['value'] == goal and source['status'] == 'user_confirmed'
    assert source['input_id'] in accepted['document']['sections']['governance']['source_input_ids']
    assert source in accepted['document']['sections']['governance']['statements']
    changes = service.db.total_changes
    out = pm_overview.read(service, plans.OWNER, w['workspace_id'])
    assert out['plan']['goal'] == goal
    assert not any(i['field'] == 'plan.goal' for i in out['unknown'])
    assert out['attention_counts']['decision'] == 0  # Still only pending decisions.
    assert out['recent_decisions_count'] == 2 and out['recent_decisions_complete']
    row = out['recent_decisions'][0]
    assert row == {'key': 'plan_revision.' + accepted['revision_id'], 'title': document['title'],
        'decided_at': accepted['adoption']['created_at'], 'actor': plans.OWNER.actor, 'status': 'adopted',
        'ref': {'kind': 'plan_revision', 'id': accepted['revision_id']},
        'operation_id': done['result']['adoption_operation_id']}
    inspector = pm_register.get_register(service, plans.OWNER, w['workspace_id'], row['key'])
    assert inspector['row']['source'] == row['ref']
    assert any(s['ref'] == {'kind': 'proposal', 'id': proposal['proposal_id']} for s in inspector['timeline'])
    assert pm_source_lineage.read_response(service.db, plans.OWNER, out) == out
    assert service.db.total_changes == changes
    assert service.create(plans.OWNER, **request)[0]['operation_id'] == original['operation_id']
    assert service.db.execute('SELECT count(*) FROM pm_plan_adoptions').fetchone()[0] == 2
    assert service.db.execute('SELECT count(*) FROM tasks').fetchone()[0] == 0
    assert snapshot(w['human']) == human_before and not bat_writes(mock)


async def test_goal_projection_reads_older_accepted_profile_without_rewriting(ops, monkeypatch):  # noqa: F811
    target = await prepared(ops)
    goal = await act(ops, target, 'pm.input.record', {'slot': 'value', 'value': 'Restore access\nwithout delays',
        'status': 'user_confirmed', 'source_ref': {'kind': 'statement', 'ref': 'Original owner goal'}})
    await act(ops, target, 'pm.thread.plan', {'selected_input_ids': [goal['input_id']], 'rationale': 'Select the goal'})
    with monkeypatch.context() as legacy:
        legacy.setattr(pm_profiles, 'VERSION', 2)
        revision = await local_plans.draft(ops, target)
    local_plans.adopt(local_plans.adoption(ops, target, revision['revision_id']))
    saved = pm_plans.get_plan(ops, OWNER, target['workspace_id'], revision['revision_id'])['plan']
    changes = ops.db.total_changes
    out = pm_overview.read(ops, OWNER, target['workspace_id'])
    assert saved['document']['profile_version'] == 2
    assert out['plan']['goal'] == 'Restore access without delays'
    assert out['recent_decisions'][0]['ref']['id'] == revision['revision_id']
    assert pm_plans.get_plan(ops, OWNER, target['workspace_id'], revision['revision_id'])['plan'] == saved
    assert ops.db.total_changes == changes


@pytest.mark.parametrize('slot', ['scope.goal', 'governance.value', 'governance.goal.extra', 'scope'])
async def test_goal_requires_selected_exact_slots_without_free_text(ops, slot):  # noqa: F811
    target = await prepared(ops)
    await act(ops, target, 'pm.input.record', {'slot': 'goal', 'value': 'Unselected goal',
        'status': 'user_confirmed', 'source_ref': {'kind': 'statement', 'ref': 'Not selected'}})
    included = await act(ops, target, 'pm.input.record', {'slot': slot, 'value': 'A goal mentioned elsewhere',
        'status': 'user_confirmed', 'source_ref': {'kind': 'statement', 'ref': 'Other selected input'}})
    await act(ops, target, 'pm.thread.plan', {'selected_input_ids': [included['input_id']], 'rationale': 'Select only this input'})
    revision = await local_plans.draft(ops, target, title='A title is not a goal', markdown='# Goal\nFree text is not confirmed goal evidence.\n')
    local_plans.adopt(local_plans.adoption(ops, target, revision['revision_id']))
    out = pm_overview.read(ops, OWNER, target['workspace_id'])
    assert out['plan']['goal'] is None
    assert {'field': 'plan.goal', 'reason': 'no_explicit_goal'} in out['unknown']


async def decided_changes(ops, count):  # noqa: F811
    conversation.proposals.install(ops)
    data = await governance.prepared(ops)
    rows = []
    for index in range(count):
        overlaps = {'disambiguation': {'record_ids': [r['record_id'] for r in rows],
            'rationale': 'Separate owner decisions on the same source'}} if rows else {}
        row = await governance.record(ops, data, kind='change', root_cause_key='direction-' + str(index),
            title='Direction ' + str(index), **overlaps)
        await governance.change(ops, row, 'decide', disposition='rejected', rationale='Keep the original scope')
        rows.append(row)
    return data, rows


async def test_decided_governance_retains_decision_time_after_revision(ops):  # noqa: F811
    data, records = await decided_changes(ops, 1)
    row = records[0]
    await governance.change(ops, row, 'decide', disposition='adopted_direction', rationale='Choose the direction after discussion')
    decided = governance.read(ops, row)['revision']
    await governance.change(ops, row, 'revise', **governance.fields(data, title='A new pending direction'))
    assert governance.read(ops, row)['revision']['document']['decision'] is None
    before = ops.db.total_changes
    out = pm_overview.read(ops, OWNER, data['wid'])
    assert out['attention_counts']['decision'] == 1
    assert out['recent_decisions_count'] == 2  # One adopted plan and this change, not two votes.
    decision = out['recent_decisions'][0]
    assert decision['title'] == decided['document']['title']
    assert decision['decided_at'] == decided['created_at'] and decision['actor'] == OWNER.actor
    assert decision['status'] == 'adopted_direction' and decision['operation_id'] == decided['operation_id']
    inspector = pm_register.get_register(ops, OWNER, data['wid'], decision['key'])
    assert inspector['row']['source'] == decision['ref']
    assert any(s['step'] == 'decision' and s['status'] == 'adopted_direction' for s in inspector['timeline'])
    assert ops.db.total_changes == before


@pytest.mark.parametrize('status', [403, 404])
async def test_decision_cap_follows_visibility_and_withdrawal_removes_inspector(ops, monkeypatch, status):  # noqa: F811
    data, records = await decided_changes(ops, pm_overview.DECISION_LIMIT + 2)
    full = pm_overview.read(ops, OWNER, data['wid'])
    assert len(full['recent_decisions']) == pm_overview.DECISION_LIMIT
    assert full['recent_decisions_count'] == len(records) + 1 and full['recent_decisions_truncated']
    hidden = {r['record_id'] for r in records[-2:]}
    actual = pm_governance._access
    def access(service, principal, record):
        if record['record_id'] in hidden:
            raise OperationError('PM_SOURCE_UNAVAILABLE', 'Source unavailable', status)
        return actual(service, principal, record)
    monkeypatch.setattr(pm_governance, '_access', access)
    before = ops.db.total_changes
    out = pm_overview.read(ops, OWNER, data['wid'])
    assert [r['ref']['id'] for r in out['recent_decisions']] == [r['record_id'] for r in reversed(records[:-2])]
    assert out['recent_decisions_count'] == len(records) - 1 and out['recent_decisions_complete']
    assert not any(rid in str(out) for rid in hidden)
    assert out['attention_counts'] == full['attention_counts']
    for rid in hidden:
        with pytest.raises(OperationError) as exc:
            pm_register.get_register(ops, OWNER, data['wid'], 'governance.' + rid)
        assert exc.value.status == 404
    assert pm_source_lineage.read_response(ops.db, OWNER, out) == out
    assert ops.db.total_changes == before


async def test_partial_decided_source_keeps_total_unknown_and_pending_count_unchanged(ops, monkeypatch):  # noqa: F811
    data, _ = await decided_changes(ops, 3)
    full = pm_overview.read(ops, OWNER, data['wid'])
    monkeypatch.setattr(pm_overview, 'DECIDED_SOURCE_LIMIT', 2)
    out = pm_overview.read(ops, OWNER, data['wid'])
    assert out['recent_decisions_count'] is None and not out['recent_decisions_complete']
    assert out['recent_decisions_truncated']
    assert out['coverage']['sources']['decided_governance'] == {'complete': False, 'next_cursor': None}
    assert {'field': 'decided_governance', 'reason': 'scan_limit'} in out['unknown']
    assert out['attention_counts']['decision'] == full['attention_counts']['decision'] == 0


@pytest.mark.parametrize('status', [409, 413])
async def test_unreadable_decided_source_is_partial_not_empty(ops, monkeypatch, status):  # noqa: F811
    data, _ = await decided_changes(ops, 1)
    def unavailable(*args):
        raise OperationError('PM_SOURCE_LIMIT', 'Source exceeds its read bound', status)
    monkeypatch.setattr(pm_governance, 'get_record', unavailable)
    out = pm_overview.read(ops, OWNER, data['wid'])
    assert out['recent_decisions_count'] is None and not out['recent_decisions_complete']
    assert len(out['recent_decisions']) == 1  # The visible adopted plan remains.
    assert {'field': 'decided_governance', 'reason': 'source_incomplete'} in out['unknown']


async def test_decided_governance_source_withdrawal_removes_row_and_count(ops):  # noqa: F811
    conversation.proposals.install(ops)
    data = await governance.prepared(ops, evidence=True)
    record = await governance.record(ops, data, kind='change')
    await governance.change(ops, record, 'decide', disposition='rejected', rationale='Keep the original scope')
    before = pm_overview.read(ops, OWNER, data['wid'])
    assert before['recent_decisions_count'] == 2
    assert before['recent_decisions'][0]['ref']['id'] == record['record_id']
    # The observed task's authority is withdrawn; none of its derived narrative
    # remains readable. No adoption or governance history is rewritten.
    ops.db.execute("UPDATE tasks SET controller_authority='{}' WHERE task_id=?", (data['task']['task_id'],))
    out = pm_overview.read(ops, OWNER, data['wid'])
    assert out['recent_decisions_count'] == 1 and out['recent_decisions_complete']
    assert record['record_id'] not in str(out)
    assert out['attention_counts']['decision'] == before['attention_counts']['decision'] == 0
    with pytest.raises(OperationError) as exc:
        pm_register.get_register(ops, OWNER, data['wid'], 'governance.' + record['record_id'])
    assert exc.value.status == 404
    assert pm_source_lineage.read_response(ops.db, OWNER, out) == out


async def test_missing_decided_source_cannot_claim_a_total(ops, monkeypatch):  # noqa: F811
    data, _ = await decided_changes(ops, 1)
    actual = pm_source_lineage._table
    monkeypatch.setattr(pm_source_lineage, '_table', lambda db, name: name != 'pm_plan_adoptions' and actual(db, name))
    out = pm_overview.read(ops, OWNER, data['wid'])
    assert out['recent_decisions_count'] is None and not out['recent_decisions_complete']
    assert len(out['recent_decisions']) == 1
    assert {'field': 'decided_plans', 'reason': 'source_unavailable'} in out['unknown']


async def test_decided_reference_budget_does_not_displace_pending_rows(ops, monkeypatch):  # noqa: F811
    data, records = await decided_changes(ops, 1)
    await governance.change(ops, records[0], 'revise', **governance.fields(data, title='A new pending direction'))
    before = pm_overview.read(ops, OWNER, data['wid'])
    actual = pm_source_lineage.document_roots
    def roots(db, value):
        # Model a union overflow at the final document barrier, after every
        # individual decision was checked. The pending-only document still fits.
        if isinstance(value, dict) and value.get('recent_decisions'):
            raise OperationError('PM_SOURCE_LINEAGE_LIMIT', 'Too many original sources for one document', 409)
        return actual(db, value)
    monkeypatch.setattr(pm_source_lineage, 'document_roots', roots)
    out = pm_overview.read(ops, OWNER, data['wid'])
    assert out['recent_decisions'] == [] and out['recent_decisions_count'] is None
    assert out['recent_decisions_truncated'] and not out['recent_decisions_complete']
    assert {'field': 'decided_references', 'reason': 'source_limit'} in out['unknown']
    assert out['attention'] == before['attention']
    assert out['attention_counts'] == before['attention_counts']
    assert out['attention_truncated'] == before['attention_truncated']
    assert out['recent_deliveries'] == before['recent_deliveries']
    assert out['recent_deliveries_complete'] == before['recent_deliveries_complete']
    assert pm_source_lineage.read_response(ops.db, OWNER, out) == out
