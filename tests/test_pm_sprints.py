"""Versioned commitments, bounded Sprint management and execution isolation."""
from __future__ import annotations

import copy
import datetime as dt
import sqlite3
import uuid
from types import SimpleNamespace

import pytest

from bat_agent_connector import artifacts, pm_discussions, pm_evidence, pm_plans, pm_workspace, work_items
from bat_agent_connector import pm_sprints as pm
from bat_agent_connector.api_auth import Principal
from bat_agent_connector.operations import OpContext, OperationError, OperationService
from bat_agent_connector.task_journal import Journal
from tests.test_pm_discussions import CONTRIBUTOR, OUTSIDER, OWNER, READER, member, settle, workspace
from tests.test_pm_evidence import act, bind_project, bind_work, item, project
from tests.test_pm_plans import adopt, adoption, draft, planning

START = '2026-10-11T00:00:00Z'
END = '2026-10-18T00:00:00Z'
NOW = dt.datetime.fromisoformat(START.replace('Z', '+00:00')).timestamp() + 60
SECOND_OWNER = Principal('other-owner', frozenset({'observe', 'manage'}))
ADMIN = Principal('administrator', frozenset({'observe', 'manage'}), admin=True)


@pytest.fixture
def ops(tmp_path, monkeypatch):
    journal = Journal(tmp_path / 'sprints.db')
    service = OperationService(journal, actions=work_items.ACTIONS)
    service.context['artifact_store'] = artifacts.ArtifactStore(service)
    pm_workspace.install(service)
    pm_discussions.install(service)
    pm_plans.install(service)
    pm_evidence.install(service)
    version = journal.db.execute('PRAGMA user_version').fetchone()[0]
    pm.install(service)
    pm.install(service)
    assert journal.db.execute('PRAGMA user_version').fetchone()[0] == version
    monkeypatch.setattr(pm, 'time', SimpleNamespace(time=lambda: NOW))
    yield service
    journal.close()


def revision_request(ops, wid, kind, **params):
    return dict(action=f'pm.{kind}.revise', target={'workspace_id': wid},
                params={'rationale': 'Explicitly version this commitment', **params},
                preconditions={'expected_revision_id': pm._head(ops.db, wid, kind)}, idempotency_key=uuid.uuid4().hex)


async def revise(ops, wid, kind, **params):
    op, _ = ops.create(OWNER, **revision_request(ops, wid, kind, **params))
    return (await settle(ops, op))['result']['revision_id']


async def prepared(ops):
    discussion, confirmed, _, acceptance = await planning(ops)
    wid = discussion['workspace_id']
    plan = await draft(ops, discussion, requirements=[{'id': 'membership.login', 'source_input_id': confirmed,
                                                       'acceptance_input_ids': [acceptance]}])
    adopt(adoption(ops, discussion, plan['revision_id']))
    pid = await project(ops)
    await bind_project(ops, wid, pid)
    work = [await item(ops, pid, f'Deliverable {i}') for i in range(2)]
    bindings = []
    for work_id in work:
        result = await bind_work(ops, {'workspace_id': wid, 'work_item_id': work_id},
                                 plan_revision_id=plan['revision_id'], requirement_ids=['membership.login'])
        bindings.append(result['binding_id'])
    goal = await revise(ops, wid, 'product_goal', plan_revision_id=plan['revision_id'], text='Members can regain access')
    dod = await revise(ops, wid, 'dod', plan_revision_id=plan['revision_id'], criteria=[{'id': 'tests', 'text': 'Relevant tests verify the fixed change'}])
    backlog = await revise(ops, wid, 'backlog', product_goal_revision_id=goal, binding_ids=bindings)
    return dict(workspace_id=wid, discussion=discussion, plan=plan['revision_id'], goal=goal, dod=dod, backlog=backlog, work=work, bindings=bindings)


def create_request(ops, data, **params):
    wid = data['workspace_id']
    return dict(action='pm.sprint.create', target={'workspace_id': wid}, params={
        'title': 'Restore membership access', 'mode': 'personal_iteration', 'owner_actor': OWNER.actor,
        'developers': [OWNER.actor, CONTRIBUTOR.actor], 'backlog_revision_id': data['backlog'], 'dod_revision_id': data['dod'],
        'goal': 'A returning member can log in', 'starts_at': START, 'ends_at': END,
        'capacity_note': 'Two people with limited capacity', 'rationale': 'Select a bounded iteration', **params},
        preconditions={'expected_workspace_version': pm_workspace.get_workspace(ops.db, OWNER, wid)['version']},
        idempotency_key=uuid.uuid4().hex)


async def create(ops, data, **params):
    op, _ = ops.create(OWNER, **create_request(ops, data, **params))
    result = (await settle(ops, op))['result']
    return {'workspace_id': data['workspace_id'], 'sprint_id': result['sprint_id']}


def request(ops, target, action, **params):
    return dict(action=f'pm.sprint.{action}', target=target,
                params={'rationale': 'Explicit management decision', **params},
                preconditions={'expected_version': pm.get_sprint(ops, OWNER, **target)['sprint']['version']},
                idempotency_key=uuid.uuid4().hex)


async def change(ops, target, action, *, who=OWNER, **params):
    op, _ = ops.create(who, **request(ops, target, action, **params))
    return (await settle(ops, op))['result']


def selections(data, actors=(OWNER.actor,), how='Implement in my own environment'):
    return [{'binding_id': bid, 'assignee': actor, 'how': how}
            for bid, actor in zip(data['bindings'], actors)]


async def active(ops, data):
    target = await create(ops, data)
    await change(ops, target, 'select', items=selections(data))
    await change(ops, target, 'start')
    return target


def end_clock(monkeypatch):
    timestamp = dt.datetime.fromisoformat(END.replace('Z', '+00:00')).timestamp()
    monkeypatch.setattr(pm, 'time', SimpleNamespace(time=lambda: timestamp))


def execution_state(ops):
    names = [row[0] for row in ops.db.execute("SELECT name FROM sqlite_master WHERE type='table'")
             if row[0].startswith(('work_item', 'task', 'resource', 'attempt', 'session', 'pm_message', 'pm_run', 'pm_plan'))]
    return {name: [tuple(row) for row in ops.db.execute(f'SELECT * FROM {name} ORDER BY rowid')] for name in names}


async def test_explicit_distinct_commitments_fixed_source_revisions_and_truthful_mode(ops):
    data = await prepared(ops)
    target = await create(ops, data, mode='scrum', scrum_master=CONTRIBUTOR.actor)
    read = pm.get_sprint(ops, OWNER, **target)
    definition = read['sprint']['definition']
    artifacts_doc = pm.get_artifacts(ops, READER, data['workspace_id'])
    assert definition['product_goal_revision_id'] == data['goal'] != read['goal']['revision_id']
    assert read['goal']['text'] == 'A returning member can log in'
    assert artifacts_doc['product_goal']['document']['text'] == 'Members can regain access'
    assert artifacts_doc['dod']['document']['commitment_for'] == 'increment'
    assert artifacts_doc['dod']['document']['acceptance_criteria'] == 'separate_requirement_data'
    assert artifacts_doc['product_goal']['document']['plan_ref']['revision_id'] == data['plan']
    assert definition['scrum_conformance'] == 'not_established'
    assert 'events not yet implemented' in definition['mode_label']
    assert read['selected_items'] == [] and read['quality'] == 'unassessed'
    assert not read['execution_authorized'] and not read['review_available'] and not read['retro_available']
    assert not read['owner_reassignment_available']


async def test_future_goal_dod_and_backlog_changes_never_rewrite_existing_sprint(ops):
    data = await prepared(ops)
    target = await active(ops, data)
    before = copy.deepcopy(pm.get_sprint(ops, OWNER, **target))
    new_goal = await revise(ops, data['workspace_id'], 'product_goal', plan_revision_id=data['plan'], text='An explicitly changed long term goal')
    new_dod = await revise(ops, data['workspace_id'], 'dod', plan_revision_id=data['plan'], criteria=[{'id': 'a11y', 'text': 'Accessibility verified'}])
    await revise(ops, data['workspace_id'], 'backlog', product_goal_revision_id=new_goal, binding_ids=list(reversed(data['bindings'])))
    assert pm.get_sprint(ops, OWNER, **target) == before
    assert pm.get_artifacts(ops, OWNER, data['workspace_id'])['dod']['revision_id'] == new_dod
    with pytest.raises(OperationError, match='PM_SPRINT_GOAL_FIXED'):
        ops.create(OWNER, **request(ops, target, 'goal.revise', goal='Silently change the active Goal'))


async def test_planned_goal_revision_preserves_original_and_start_freezes_selected_revision(ops):
    data = await prepared(ops)
    target = await create(ops, data)
    original = pm.get_sprint(ops, OWNER, **target)['goal']
    await change(ops, target, 'goal.revise', goal='Explicitly refined login Goal')
    await change(ops, target, 'select', items=selections(data))
    await change(ops, target, 'start')
    read = pm.get_sprint(ops, OWNER, **target)
    assert read['goal_revisions'][0] == original
    assert read['goal_revisions'][1]['previous_revision_id'] == original['revision_id']
    assert read['sprint']['started_goal_revision_id'] == read['goal']['revision_id']
    assert read['sprint']['started_selection_revision_id'] == read['sprint']['selection_revision_id']


@pytest.mark.parametrize(('start', 'end'), [
    ('2026-01-01T00:00:00Z', '2026-02-01T00:00:00Z'),
    ('2026-01-31T12:00:00Z', '2026-02-28T12:00:00Z'),
    ('2028-01-31T12:00:00Z', '2028-02-29T12:00:00Z'),
    ('2026-12-15T12:00:00+00:00', '2027-01-15T12:00:00Z'),
])
def test_fixed_period_is_at_most_one_calendar_month(start, end):
    normalized = pm._interval(start, end)
    assert normalized[0].endswith('Z') and normalized[1] == end


@pytest.mark.parametrize(('start', 'end'), [
    (START, START), (END, START), (START, '2026-11-11T00:00:01Z'),
    ('2026-01-31T12:00:00Z', '2026-03-01T00:00:00Z'),
    ('2026-10-11T00:00:00', END), ('2026-10-11T00:00:00-04:00', END),
    (None, END), ([], END), ('garbage', END), ('9999-12-01T00:00:00Z', '9999-12-02T00:00:00Z'),
])
def test_invalid_or_expanded_intervals_rejected(start, end):
    with pytest.raises(OperationError):
        pm._interval(start, end)


async def test_timebox_start_and_close_boundaries_no_early_close(ops, monkeypatch):
    data = await prepared(ops)
    target = await create(ops, data)
    await change(ops, target, 'select', items=selections(data))
    with monkeypatch.context() as clock:
        clock.setattr(pm, 'time', SimpleNamespace(time=lambda: NOW - 61))
        with pytest.raises(OperationError, match='PM_SPRINT_TIMEBOX'):
            ops.create(OWNER, **request(ops, target, 'start'))
    await change(ops, target, 'start')
    with pytest.raises(OperationError, match='PM_SPRINT_TIMEBOX'):
        ops.create(OWNER, **request(ops, target, 'close'))
    end_clock(monkeypatch)
    await change(ops, target, 'close', rationale='Fixed period ended')
    assert pm.get_sprint(ops, OWNER, **target)['sprint']['status'] == 'closed'
    with pytest.raises(OperationError, match='PM_SPRINT_TERMINAL'):
        ops.create(OWNER, **request(ops, target, 'cancel'))


async def test_expired_planned_sprint_cannot_start_or_close_but_owner_can_cancel(ops, monkeypatch):
    data = await prepared(ops)
    target = await create(ops, data)
    await change(ops, target, 'select', items=selections(data))
    end_clock(monkeypatch)
    for action in ('start', 'close'):
        with pytest.raises(OperationError, match='PM_SPRINT_TIMEBOX'):
            ops.create(OWNER, **request(ops, target, action))
    await change(ops, target, 'cancel', rationale='The planned period was never started')


async def test_only_one_active_sprint_and_concurrent_start_is_checked_at_effect(ops):
    data = await prepared(ops)
    targets = [await create(ops, data), await create(ops, data)]
    for target in targets:
        await change(ops, target, 'select', items=selections(data))
    pending = [ops.create(OWNER, **request(ops, target, 'start'))[0] for target in targets]
    first = await settle(ops, pending[0])
    other = ops.get(pending[1]['operation_id'])
    assert first['result']['status'] == 'active'
    assert other['status'] == 'failed' and other['error_code'] == 'PM_SPRINT_ACTIVE'
    assert ops.db.execute("SELECT COUNT(*) FROM pm_sprint_state WHERE status='active'").fetchone()[0] == 1


@pytest.mark.parametrize('who', [CONTRIBUTOR, READER, OUTSIDER, SECOND_OWNER, ADMIN])
async def test_cancel_requires_exact_designated_owner_without_admin_override(ops, who):
    data = await prepared(ops)
    await member(ops, data, SECOND_OWNER.actor, 'owner')
    target = await active(ops, data)
    with pytest.raises(OperationError, match='NOT_FOUND' if who == OUTSIDER else 'FORBIDDEN'):
        ops.create(who, **request(ops, target, 'cancel', rationale='Goal obsolete'))
    original = pm.get_sprint(ops, OWNER, **target)['goal']
    await change(ops, target, 'cancel', rationale='Goal no longer has value')
    read = pm.get_sprint(ops, OWNER, **target)
    assert read['goal'] == original
    assert read['history'][-1]['document']['rationale'] == 'Goal no longer has value'
    assert read['history'][-1]['document']['goal_revision_id'] == original['revision_id']


async def test_queued_owner_decision_refuses_role_loss_before_effect(ops):
    data = await prepared(ops)
    await member(ops, data, SECOND_OWNER.actor, 'owner')
    target = await active(ops, data)
    pending, _ = ops.create(OWNER, **request(ops, target, 'cancel'))
    # Change the actual membership before running the already admitted effect.
    with ops.journal.tx():
        ops.db.execute("UPDATE pm_workspace_members SET role='contributor' WHERE workspace_id=? AND actor=?", (data['workspace_id'], OWNER.actor))
    failed = await settle(ops, pending, success=False)
    assert failed['status'] == 'failed' and failed['error_code'] == 'FORBIDDEN'
    assert pm.get_sprint(ops, OWNER, **target)['sprint']['status'] == 'active'


async def test_developer_owns_how_and_cannot_remove_reassign_or_reorder_others_work(ops):
    data = await prepared(ops)
    target = await create(ops, data)
    owner_item, other_item = selections(data, (OWNER.actor, CONTRIBUTOR.actor), how='')
    with pytest.raises(OperationError, match='FORBIDDEN'):
        ops.create(OWNER, **request(ops, target, 'select', items=[owner_item, {**other_item, 'how': 'Write it my way'}]))
    await change(ops, target, 'select', items=[owner_item, other_item])
    other_item['how'] = 'I will develop in my own CLI worktree'
    await change(ops, target, 'select', who=CONTRIBUTOR, items=[owner_item, other_item])
    for changed in ([other_item], [{**owner_item, 'assignee': CONTRIBUTOR.actor}, other_item],
                    [{**owner_item, 'how': 'Override'}, other_item]):
        with pytest.raises(OperationError, match='FORBIDDEN'):
            ops.create(CONTRIBUTOR, **request(ops, target, 'select', items=changed))
    with pytest.raises(OperationError, match='FORBIDDEN'):
        ops.create(OWNER, **request(ops, target, 'select', items=[owner_item, {**other_item, 'how': 'Override'}]))
    assert pm.get_sprint(ops, OWNER, **target)['selected_items'][1]['how'] == other_item['how']


async def test_unlisted_contributor_cannot_select_and_reader_cannot_manage(ops):
    data = await prepared(ops)
    target = await create(ops, data, developers=[OWNER.actor])
    for actor in (CONTRIBUTOR, READER, OUTSIDER):
        with pytest.raises(OperationError, match='NOT_FOUND' if actor == OUTSIDER else 'FORBIDDEN'):
            ops.create(actor, **request(ops, target, 'select', items=selections(data)))
    assert pm.get_sprint(ops, READER, **target)['selected_items'] == []


async def test_explicit_backlog_bounds_new_selections_and_stale_binding_refused(ops):
    data = await prepared(ops)
    one_backlog = await revise(ops, data['workspace_id'], 'backlog', product_goal_revision_id=data['goal'], binding_ids=data['bindings'][:1])
    target = await create(ops, data, backlog_revision_id=one_backlog)
    with pytest.raises(OperationError, match='INVALID_PARAMS'):
        ops.create(OWNER, **request(ops, target, 'select', items=selections(data, (OWNER.actor, OWNER.actor))))
    work_target = {'workspace_id': data['workspace_id'], 'work_item_id': data['work'][0]}
    await bind_work(ops, work_target, plan_revision_id=data['plan'], requirement_ids=['membership.login'])
    with pytest.raises(OperationError, match='PM_WORK_BINDING_CHANGED'):
        ops.create(OWNER, **request(ops, target, 'select', items=selections(data)))


async def test_start_rechecks_binding_but_active_history_does_not_follow_rebinding(ops):
    data = await prepared(ops)
    target = await create(ops, data)
    await change(ops, target, 'select', items=selections(data))
    source = {'workspace_id': data['workspace_id'], 'work_item_id': data['work'][0]}
    await bind_work(ops, source, plan_revision_id=data['plan'], requirement_ids=['membership.login'])
    with pytest.raises(OperationError, match='PM_WORK_BINDING_CHANGED'):
        ops.create(OWNER, **request(ops, target, 'start'))
    assert pm.get_sprint(ops, OWNER, **target)['selected_items'][0]['binding_id'] == data['bindings'][0]


async def test_start_requires_explicit_selection_and_active_selection_cannot_empty(ops):
    data = await prepared(ops)
    target = await create(ops, data)
    with pytest.raises(OperationError, match='PM_SPRINT_SELECTION_REQUIRED'):
        ops.create(OWNER, **request(ops, target, 'start'))
    await change(ops, target, 'select', items=selections(data))
    await change(ops, target, 'start')
    with pytest.raises(OperationError, match='INVALID_PARAMS'):
        ops.create(OWNER, **request(ops, target, 'select', items=[]))


async def test_close_is_not_done_review_or_retro_and_never_carries_or_controls_work(ops, monkeypatch):
    data = await prepared(ops)
    work = work_items._get_item(ops.db, data['work'][0])
    await act(ops, 'work_item.update', {'work_item_id': work['work_item_id']}, {'state': 'done'}, {'expected_version': work['version']})
    work = work_items._get_item(ops.db, work['work_item_id'])
    await act(ops, 'work_item.approve', {'work_item_id': work['work_item_id']}, {}, {'expected_fingerprint': work_items.fingerprint(work)},
              who=Principal(OWNER.actor, frozenset({'observe', 'manage', 'approve'})))
    before = execution_state(ops)
    target = await active(ops, data)
    await change(ops, target, 'select', items=selections(data, (OWNER.actor, OWNER.actor)))
    end_clock(monkeypatch)
    await change(ops, target, 'close', work_notes={data['work'][0]: 'Implementation approved; quality not yet independently assessed'})
    read = pm.get_sprint(ops, OWNER, **target)
    closed = read['history'][-1]['document']
    assert len(closed['items']) == 2
    assert {i['quality'] for i in closed['items']} == {'unassessed'}
    assert all(not i['counts_as_done'] and i['disposition'] == 'requires_product_backlog_consideration' for i in closed['items'])
    assert not closed['automatic_carryover'] and not closed['execution_changed']
    assert not closed['review_completed'] and not closed['retro_completed']
    second = await create(ops, data, starts_at=END, ends_at='2026-10-25T00:00:00Z')
    assert pm.get_sprint(ops, OWNER, **second)['selected_items'] == []
    assert pm.get_sprint(ops, OWNER, **target) == read
    assert execution_state(ops) == before


async def test_cancel_preserves_removed_selection_history_without_moving_attempts(ops):
    data = await prepared(ops)
    target = await create(ops, data)
    await change(ops, target, 'select', items=selections(data, (OWNER.actor, OWNER.actor)))
    await change(ops, target, 'select', items=selections(data))
    before = execution_state(ops)
    await change(ops, target, 'cancel', work_notes={data['work'][0]: 'Return for explicit prioritization'})
    read = pm.get_sprint(ops, OWNER, **target)
    assert len(read['selection_revisions'][0]['items']) == 2
    assert len(read['selection_revisions'][1]['items']) == 1
    assert read['history'][-1]['document']['items'][0]['reason'] == 'Return for explicit prioritization'
    assert execution_state(ops) == before


async def test_only_adopted_plans_and_workspace_local_artifacts_bind(ops):
    data = await prepared(ops)
    proposed = await draft(ops, data['discussion'])
    with pytest.raises(OperationError, match='PM_ADOPTED_PLAN_REQUIRED'):
        ops.create(OWNER, **revision_request(ops, data['workspace_id'], 'product_goal', plan_revision_id=proposed['revision_id'], text='Unadopted'))
    other = await workspace(ops)
    with pytest.raises(OperationError, match='PM_SPRINT_ARTIFACT_NOT_FOUND'):
        ops.create(OWNER, **revision_request(ops, other['workspace_id'], 'backlog', product_goal_revision_id=data['goal'], binding_ids=[]))
    target = await create(ops, data)
    with pytest.raises(OperationError, match='PM_SPRINT_NOT_FOUND'):
        pm.get_sprint(ops, OWNER, other['workspace_id'], target['sprint_id'])


async def test_artifact_and_sprint_cas_reject_concurrent_stale_mutations(ops):
    data = await prepared(ops)
    params = dict(plan_revision_id=data['plan'], text='A changed Product Goal')
    pending = [ops.create(OWNER, **revision_request(ops, data['workspace_id'], 'product_goal', **params))[0] for _ in range(2)]
    await settle(ops, pending[0])
    assert ops.get(pending[1]['operation_id'])['error_code'] == 'VERSION_CONFLICT'
    target = await create(ops, data)
    pending = [ops.create(OWNER, **request(ops, target, 'goal.revise', goal='New Sprint Goal'))[0] for _ in range(2)]
    await settle(ops, pending[0])
    assert ops.get(pending[1]['operation_id'])['error_code'] == 'VERSION_CONFLICT'


async def test_lost_ack_replay_and_restart_keep_original_local_fact(ops, tmp_path):
    data = await prepared(ops)
    req = create_request(ops, data)
    op, _ = ops.create(OWNER, **req)
    result = await pm._run(OpContext(ops, op))
    replay, created = ops.create(OWNER, **req)
    assert not created and (await settle(ops, replay))['result'] == result
    assert ops.db.execute('SELECT COUNT(*) FROM pm_sprints').fetchone()[0] == 1
    restarted = Journal(tmp_path / 'sprints.db')
    try:
        recovered = OperationService(restarted)
        pm.install(recovered)
        replay, created = recovered.create(OWNER, **req)
        assert not created and replay['result'] == result
        assert len(pm.list_sprints(recovered, OWNER, data['workspace_id'])['sprints']) == 1
    finally:
        restarted.close()


async def test_receipt_recovers_after_authority_loss_but_public_access_still_checks_role(ops):
    data = await prepared(ops)
    target = await active(ops, data)
    pending, _ = ops.create(OWNER, **request(ops, target, 'cancel'))
    result = await pm._run(OpContext(ops, pending))
    with ops.journal.tx():
        ops.db.execute('DELETE FROM pm_workspace_members WHERE workspace_id=? AND actor=?', (data['workspace_id'], OWNER.actor))
    assert await pm._run(OpContext(ops, pending)) == result
    with pytest.raises(OperationError, match='NOT_FOUND'):
        pm.get_sprint(ops, OWNER, **target)
    assert len(pm.get_sprint(ops, READER, **target)['history']) == 4


async def test_local_transaction_failure_rolls_back_all_sprint_state_and_retries_once(ops, monkeypatch):
    data = await prepared(ops)
    pending, _ = ops.create(OWNER, **create_request(ops, data))
    saved = ops.journal.api_event
    with monkeypatch.context() as broken:
        broken.setattr(ops.journal, 'api_event', lambda *a, **k: (_ for _ in ()).throw(RuntimeError('lost local write')))
        with pytest.raises(RuntimeError, match='lost local write'):
            await pm._run(OpContext(ops, pending))
    assert ops.db.execute('SELECT COUNT(*) FROM pm_sprints').fetchone()[0] == 0
    assert ops.db.execute('SELECT COUNT(*) FROM pm_sprint_goal_revisions').fetchone()[0] == 0
    assert ops.journal.api_event == saved
    result = await pm._run(OpContext(ops, pending))
    assert (await settle(ops, pending))['result'] == result
    assert ops.db.execute('SELECT COUNT(*) FROM pm_sprints').fetchone()[0] == 1


async def test_histories_immutable_bounded_and_ordered_by_sequence_after_clock_rollback(ops, monkeypatch):
    data = await prepared(ops)
    target = await create(ops, data)
    monkeypatch.setattr(pm, 'time', SimpleNamespace(time=lambda: NOW - 500))
    await change(ops, target, 'goal.revise', goal='Later insertion with older timestamp')
    await change(ops, target, 'select', items=selections(data))
    brief = pm.get_sprint(ops, OWNER, **target, history_limit=1)
    assert brief['goal']['text'] == 'Later insertion with older timestamp'
    assert len(brief['goal_revisions']) == 1 and brief['history_next_cursors']['goals']
    second = pm.list_history(ops, OWNER, **target, kind='goals', after=brief['history_next_cursors']['goals'], limit=1)
    assert second['records'][0]['created_at'] < brief['goal_revisions'][0]['created_at']
    assert second['next_cursor'] is None
    for table in ('pm_sprints', 'pm_sprint_artifact_revisions', 'pm_sprint_goal_revisions', 'pm_sprint_selections', 'pm_sprint_events'):
        with pytest.raises(sqlite3.IntegrityError, match='immutable'):
            ops.db.execute(f'DELETE FROM {table}')


async def test_reads_have_no_effects_and_pagination_is_workspace_scoped(ops):
    data = await prepared(ops)
    targets = [await create(ops, data), await create(ops, data)]
    await revise(ops, data['workspace_id'], 'product_goal', plan_revision_id=data['plan'], text='Second Product Goal')
    before = ops.db.total_changes
    first = pm.list_sprints(ops, READER, data['workspace_id'], limit=1)
    second = pm.list_sprints(ops, READER, data['workspace_id'], after=first['next_cursor'])
    assert first['sprints'][0]['sprint_id'] == targets[0]['sprint_id']
    assert second['sprints'][0]['sprint_id'] == targets[1]['sprint_id']
    revisions = pm.list_revisions(ops, READER, data['workspace_id'], 'product_goal', limit=1)
    assert revisions['revisions'][0]['revision_id'] == data['goal']
    assert pm.list_revisions(ops, READER, data['workspace_id'], 'product_goal', after=revisions['next_cursor'])['revisions'][0]['previous_revision_id'] == data['goal']
    assert pm.get_artifact(ops, READER, data['workspace_id'], 'dod', data['dod'])['artifact']['revision_id'] == data['dod']
    pm.get_sprint(ops, READER, **targets[0])
    assert ops.db.total_changes == before
    with pytest.raises(OperationError, match='NOT_FOUND'):
        pm.list_sprints(ops, OUTSIDER, data['workspace_id'])


@pytest.mark.parametrize(('field', 'value'), [
    ('owner_actor', []), ('mode', []), ('developers', [None]), ('developers', [OWNER.actor, OWNER.actor]),
    ('backlog_revision_id', {}), ('dod_revision_id', []), ('capacity_note', ''), ('scrum_master', {}),
    ('goal', ''), ('goal', '\ud800'), ('unexpected', True),
])
async def test_malformed_create_is_rejected_at_admission_without_state(ops, field, value):
    data = await prepared(ops)
    with pytest.raises(OperationError):
        ops.create(OWNER, **create_request(ops, data, **{field: value}))
    assert ops.db.execute('SELECT COUNT(*) FROM pm_sprints').fetchone()[0] == 0


@pytest.mark.parametrize('who', [CONTRIBUTOR, READER, OUTSIDER, Principal(OWNER.actor, frozenset({'manage'})), Principal(OWNER.actor, frozenset({'observe'}))])
async def test_product_commitments_require_direction_role_and_both_scopes(ops, who):
    data = await prepared(ops)
    with pytest.raises(OperationError, match='NOT_FOUND' if who == OUTSIDER else 'FORBIDDEN'):
        ops.create(who, **revision_request(ops, data['workspace_id'], 'product_goal', plan_revision_id=data['plan'], text='Change goal'))


async def test_active_developer_adaptation_retains_started_baseline_and_refuses_late_edits(ops, monkeypatch):
    data = await prepared(ops)
    target = await active(ops, data)
    baseline = pm.get_sprint(ops, OWNER, **target)['sprint']['started_selection_revision_id']
    # A new explicit binding elsewhere must not rewrite this active association.
    await bind_work(ops, {'workspace_id': data['workspace_id'], 'work_item_id': data['work'][0]},
                    plan_revision_id=data['plan'], requirement_ids=['membership.login'])
    await change(ops, target, 'select', items=selections(data, how='Refine my own implementation approach'))
    read = pm.get_sprint(ops, OWNER, **target)
    assert read['sprint']['started_selection_revision_id'] == baseline
    assert read['sprint']['selection_revision_id'] != baseline
    assert read['selected_items'][0]['binding_id'] == data['bindings'][0]
    end_clock(monkeypatch)
    with pytest.raises(OperationError, match='PM_SPRINT_TIMEBOX'):
        ops.create(OWNER, **request(ops, target, 'select', items=selections(data)))


async def test_cancel_does_not_stop_existing_task_or_modify_its_link(ops):
    data = await prepared(ops)
    task = ops.journal.submit(project='synthetic', host='mock-host', workspace='mock-workspace',
                              original_words='Continue independent work', idempotency_key=uuid.uuid4().hex)
    await act(ops, 'work_item.link', {'work_item_id': data['work'][0]}, {'kind': 'task', 'ref': task['task_id']})
    target = await active(ops, data)
    before = execution_state(ops)
    await change(ops, target, 'cancel', rationale='Goal obsolete; preserve independent execution')
    assert execution_state(ops) == before
    assert ops.db.execute('SELECT COUNT(*) FROM tasks').fetchone()[0] == 1


async def test_owner_change_is_unavailable_and_admin_with_demoted_owner_identity_cannot_cancel(ops):
    data = await prepared(ops)
    target = await active(ops, data)
    with pytest.raises(OperationError, match='INVALID_PARAMS'):
        ops.create(OWNER, **request(ops, target, 'cancel', owner_actor=CONTRIBUTOR.actor))
    with ops.journal.tx():
        ops.db.execute("UPDATE pm_workspace_members SET role='contributor' WHERE workspace_id=? AND actor=?", (data['workspace_id'], OWNER.actor))
    with pytest.raises(OperationError, match='FORBIDDEN'):
        ops.create(Principal(OWNER.actor, frozenset(), admin=True), **request(ops, target, 'cancel'))


async def test_creation_can_name_other_owner_without_granting_creator_later_direction_power(ops):
    data = await prepared(ops)
    await member(ops, data, SECOND_OWNER.actor, 'owner')
    target = await create(ops, data, owner_actor=SECOND_OWNER.actor)
    with pytest.raises(OperationError, match='FORBIDDEN'):
        ops.create(OWNER, **request(ops, target, 'goal.revise', goal='Creator tries to change Goal'))
    await change(ops, target, 'goal.revise', who=SECOND_OWNER, goal='Designated owner decides')
    await change(ops, target, 'cancel', who=SECOND_OWNER)


async def test_start_rechecks_selected_developer_and_optional_scrum_master_membership(ops):
    data = await prepared(ops)
    target = await create(ops, data, mode='scrum', scrum_master=CONTRIBUTOR.actor)
    await change(ops, target, 'select', items=selections(data))
    await member(ops, data, CONTRIBUTOR.actor, 'reader')
    with pytest.raises(OperationError, match='FORBIDDEN'):
        ops.create(OWNER, **request(ops, target, 'start'))
    await member(ops, data, CONTRIBUTOR.actor, 'contributor')
    await change(ops, target, 'select', items=selections(data, (CONTRIBUTOR.actor,), how=''))
    await member(ops, data, CONTRIBUTOR.actor, 'reader')
    with pytest.raises(OperationError, match='FORBIDDEN'):
        ops.create(OWNER, **request(ops, target, 'start'))


async def test_start_refuses_archived_selected_work(ops):
    data = await prepared(ops)
    target = await create(ops, data)
    await change(ops, target, 'select', items=selections(data))
    work = work_items._get_item(ops.db, data['work'][0])
    await act(ops, 'work_item.update', {'work_item_id': data['work'][0]}, {'archived': True}, {'expected_version': work['version']})
    with pytest.raises(OperationError):
        ops.create(OWNER, **request(ops, target, 'start'))
    assert pm.get_sprint(ops, OWNER, **target)['selected_items'][0]['work_item_id'] == data['work'][0]


async def test_unadopted_work_binding_cannot_enter_product_backlog(ops):
    data = await prepared(ops)
    result = await bind_work(ops, {'workspace_id': data['workspace_id'], 'work_item_id': data['work'][0]})
    with pytest.raises(OperationError, match='PM_ADOPTED_PLAN_REQUIRED'):
        ops.create(OWNER, **revision_request(ops, data['workspace_id'], 'backlog',
                                           product_goal_revision_id=data['goal'], binding_ids=[result['binding_id']]))


async def test_cancelled_pending_operation_makes_no_management_effect(ops):
    data = await prepared(ops)
    pending, _ = ops.create(OWNER, **create_request(ops, data))
    ops.cancel(OWNER, pending['operation_id'])
    assert (await settle(ops, pending, success=False))['status'] == 'cancelled'
    assert ops.db.execute('SELECT COUNT(*) FROM pm_sprints').fetchone()[0] == 0


async def test_bounded_unknown_and_wrong_type_fields_fail_before_new_revisions(ops):
    data = await prepared(ops)
    target = await create(ops, data)
    for values in ([{'id': 'tests', 'text': 'one'}, {'id': 'tests', 'text': 'two'}], [], [{'id': 'bad id', 'text': 'one'}]):
        with pytest.raises(OperationError, match='INVALID_PARAMS'):
            ops.create(OWNER, **revision_request(ops, data['workspace_id'], 'dod', plan_revision_id=data['plan'], criteria=values))
    for items in (selections(data) * 2, [{**selections(data)[0], 'assignee': []}], [{**selections(data)[0], 'how': '\ud800'}]):
        with pytest.raises(OperationError, match='INVALID_PARAMS|INVALID_REQUEST'):
            ops.create(OWNER, **request(ops, target, 'select', items=items))
    for fields in ({'work_notes': {'unselected': 'Fake reason'}}, {'rationale': ''}, {'work_notes': []}):
        with pytest.raises(OperationError, match='INVALID_PARAMS'):
            ops.create(OWNER, **request(ops, target, 'cancel', **fields))
    for kwargs in ({'after': -1}, {'after': True}, {'limit': 0}, {'limit': 101}, {'limit': '2'}):
        with pytest.raises(OperationError, match='INVALID_PARAMS'):
            pm.list_sprints(ops, OWNER, data['workspace_id'], **kwargs)
    with pytest.raises(OperationError, match='INVALID_PARAMS'):
        pm.list_history(ops, OWNER, **target, kind=[])
    with pytest.raises(OperationError, match='INVALID_PARAMS'):
        pm.get_sprint(ops, OWNER, **target, history_limit=0)
    assert len(pm.list_revisions(ops, OWNER, data['workspace_id'], 'dod')['revisions']) == 1
    assert pm.get_sprint(ops, OWNER, **target)['sprint']['version'] == 1
