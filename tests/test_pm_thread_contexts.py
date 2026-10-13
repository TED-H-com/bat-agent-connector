"""Bound discussion origins use central records, current access and original keys."""
from __future__ import annotations

import json
import sqlite3
import uuid

import pytest

from bat_agent_connector import (
    pm_discussions,
    pm_follow_up,
    pm_governance,
    pm_meetings,
    pm_plans,
    pm_quality_reviews,
    pm_source_lineage,
    pm_sprints,
    pm_thread_contexts,
    pm_workspace,
)
from bat_agent_connector.api_auth import Principal
from bat_agent_connector.operations import OpContext, OperationError
from tests import test_pm_follow_up as follow
from tests import test_pm_governance as governance
from tests import test_pm_meetings as meetings
from tests import test_pm_roadmap as roadmap
from tests.test_pm_discussions import OUTSIDER, OWNER, act, settle, workspace
from tests.test_pm_snapshots import snapshot

base_ops = roadmap.ops


@pytest.fixture
def ops(base_ops):
    for module in (pm_sprints, pm_meetings, pm_governance, pm_follow_up, pm_quality_reviews):
        module.install(base_ops)
    pm_source_lineage.schema(base_ops.journal)
    return base_ops


def request(ops, wid, refs, title='Discuss recovery / 討論帳號復原', *, who=OWNER):
    return dict(action='pm.thread.create', target={'workspace_id': wid},
        params={'title': title, 'context': refs},
        preconditions={'expected_version': pm_workspace.require_member(ops.db, who, wid)['version']},
        idempotency_key=uuid.uuid4().hex)


async def create(ops, wid, refs, *, who=OWNER):
    env = request(ops, wid, refs, who=who)
    original = ops.create(who, **env)[0]
    done = await settle(ops, original)
    return done, env


async def origins(ops):
    target, plan, work, binding, _, baseline = await roadmap.mapped(ops)
    plan = pm_plans.get_plan(ops, OWNER, target['workspace_id'], plan['revision_id'])['plan']
    snap = await snapshot(ops, target['workspace_id'])
    assumption = next(i['input_id'] for i in plan['document']['selected_inputs'] if i['status'] == 'agent_assumption')
    data = {'wid': target['workspace_id'], 'snapshot': snap, 'assumption': assumption}
    record = await governance.record(ops, data)
    intake = ops.create(OWNER, **follow.request(ops, data))[0]
    intake = (await settle(ops, intake))['result']
    refs = [
        {'kind': 'snapshot', 'id': snap['snapshot_id']},
        {'kind': 'plan_revision', 'id': plan['revision_id']},
        {'kind': 'requirement', 'id': 'login', 'revision_id': plan['revision_id']},
        {'kind': 'milestone', 'id': 'release', 'revision_id': baseline['revision_id']},
        {'kind': 'work_item', 'id': work['work_item_id']},
        {'kind': 'governance', 'id': record['record_id']},
        {'kind': 'follow_up', 'id': intake['intake_id']},
    ]
    return target, refs


async def test_context_records_each_origin_and_replay_is_immutable(ops):
    target, refs = await origins(ops)
    before = {table: ops.db.execute('SELECT COUNT(*) FROM ' + table).fetchone()[0] for table in ('tasks', 'commands', 'work_items')}
    done, env = await create(ops, target['workspace_id'], refs)
    tid = done['result']['thread_id']
    doc = pm_discussions.get_thread(ops, OWNER, target['workspace_id'], tid)['thread']
    assert [c['kind'] for c in doc['context']] == [r['kind'] for r in refs]
    assert all(c['available'] and c['label'] == c['bound_label'] for c in doc['context'])
    replay, fresh = ops.create(OWNER, **env)
    assert not fresh and replay['operation_id'] == done['operation_id']
    assert await pm_discussions._run(OpContext(ops, done)) == done['result']
    assert ops.db.execute('SELECT COUNT(*) FROM pm_thread_contexts WHERE thread_id=?', (tid,)).fetchone()[0] == len(refs)
    with pytest.raises(sqlite3.IntegrityError, match='immutable'):
        ops.db.execute('DELETE FROM pm_thread_contexts WHERE thread_id=?', (tid,))
    rows = pm_discussions.list_threads(ops, OWNER, target['workspace_id'])['threads']
    assert next(r for r in rows if r['thread_id'] == tid)['context'] == doc['context']
    assert before == {table: ops.db.execute('SELECT COUNT(*) FROM ' + table).fetchone()[0] for table in before}


async def test_meeting_origin_is_bound_through_its_fixed_snapshot(ops):
    data, _, _, meeting = await meetings.meeting(ops)
    done, _ = await create(ops, data['workspace_id'], [{'kind': 'meeting', 'id': meeting['meeting_id']}])
    result = pm_discussions.context(ops, OWNER, data['workspace_id'], done['result']['thread_id'])
    assert result['bound_context'][0]['summary']['kind'] == 'planning'


@pytest.mark.parametrize('refs', [None, {}, [{}], [{'kind': 'requirement', 'id': 'login'}],
    [{'kind': 'snapshot', 'id': 'invalid'}], [{'kind': 'work_item', 'id': 'wi_' + '1' * 20, 'path': 'fixture'}],
    [{'kind': 'snapshot', 'id': 'pmsnap_' + '1' * 32}] * 9,
    [{'kind': 'snapshot', 'id': 'pmsnap_' + '1' * 32}] * 2])
async def test_invalid_context_has_no_partial_create(ops, refs):
    ws = await workspace(ops)
    with pytest.raises(OperationError, match='PM_CONTEXT_INVALID'):
        ops.create(OWNER, **request(ops, ws['workspace_id'], refs))
    assert ops.db.execute('SELECT COUNT(*) FROM pm_threads').fetchone()[0] == 0


async def test_cross_workspace_missing_and_invisible_context_are_refused(ops, monkeypatch):
    target, refs = await origins(ops)
    other = await workspace(ops)
    env = request(ops, other['workspace_id'], refs[:2])
    with pytest.raises(OperationError, match='PM_CONTEXT_SOURCE_UNAVAILABLE'):
        ops.create(OWNER, **env)
    with pytest.raises(OperationError, match='PM_CONTEXT_SOURCE_UNAVAILABLE'):
        ops.create(OWNER, **request(ops, target['workspace_id'], [{'kind': 'snapshot', 'id': 'pmsnap_' + '0' * 32}]))
    with pytest.raises(OperationError):
        ops.create(OUTSIDER, **request(ops, target['workspace_id'], refs))
    original = pm_source_lineage.visible
    hidden = refs[4]['id']
    monkeypatch.setattr(pm_source_lineage, 'visible', lambda db, principal, kind, identity:
        False if kind == 'work_item' and identity == hidden else original(db, principal, kind, identity))
    with pytest.raises(OperationError, match='PM_CONTEXT_SOURCE_UNAVAILABLE'):
        ops.create(OWNER, **request(ops, target['workspace_id'], [refs[0], refs[4]]))
    assert ops.db.execute('SELECT COUNT(*) FROM pm_thread_contexts').fetchone()[0] == 0


async def test_withdrawal_removes_ids_and_both_labels_but_keeps_position(ops, monkeypatch):
    target, refs = await origins(ops)
    done, env = await create(ops, target['workspace_id'], [refs[4]])
    tid = done['result']['thread_id']
    frozen = pm_discussions.context(ops, OWNER, target['workspace_id'], tid)
    original = pm_source_lineage.visible
    monkeypatch.setattr(pm_source_lineage, 'visible', lambda db, principal, kind, identity:
        False if kind == 'work_item' and identity == refs[4]['id'] else original(db, principal, kind, identity))
    expected = [{'kind': 'work_item', 'position': 0, 'available': False}]
    assert pm_discussions.get_thread(ops, OWNER, target['workspace_id'], tid)['thread']['context'] == expected
    assert pm_discussions.context(ops, OWNER, target['workspace_id'], tid)['bound_context'] == expected
    assert not pm_thread_contexts.frozen_visible(ops.db, OWNER, target['workspace_id'], frozen)
    assert not pm_workspace.operation_visible(ops.db, OWNER, done)
    assert not pm_thread_contexts.operation_visible(ops.db, OWNER, done)
    with pytest.raises(OperationError, match='NOT_FOUND'):
        ops.create(OWNER, **env)
    monkeypatch.setattr(pm_source_lineage, 'visible', original)
    replay, fresh = ops.create(OWNER, **env)
    assert not fresh and replay['operation_id'] == done['operation_id']


async def test_final_frame_rechecks_origins_and_atomic_create(ops, monkeypatch):
    target, refs = await origins(ops)
    op = ops.create(OWNER, **request(ops, target['workspace_id'], refs[:2]))[0]
    def withdrawn(*args, **kwargs):
        raise pm_thread_contexts.unavailable()
    monkeypatch.setattr(pm_thread_contexts, 'resolve', withdrawn)
    done = await settle(ops, op, success=False)
    assert done['status'] == 'failed'
    assert not ops.db.execute('SELECT 1 FROM pm_threads WHERE operation_id=?', (op['operation_id'],)).fetchone()
    assert not ops.db.execute('SELECT 1 FROM pm_thread_contexts').fetchone()


async def test_rename_version_and_title_constraints_preserve_context(ops):
    target, refs = await origins(ops)
    done, _ = await create(ops, target['workspace_id'], refs[:1])
    selected = {'workspace_id': target['workspace_id'], 'thread_id': done['result']['thread_id']}
    env = dict(action='pm.thread.rename', target=selected, params={'title': 'Recovery decision / 帳號復原決策'},
        preconditions={'expected_version': 1}, idempotency_key=uuid.uuid4().hex)
    rename = ops.create(OWNER, **env)[0]
    await settle(ops, rename)
    assert ops.create(OWNER, **env)[1] is False
    with pytest.raises(OperationError, match='VERSION_CONFLICT'):
        ops.create(OWNER, **{**env, 'idempotency_key': uuid.uuid4().hex})
    for title in ('', 'x' * 161):
        with pytest.raises(OperationError, match='INVALID_PARAMS'):
            await act(ops, selected, 'pm.thread.rename', {'title': title})
    thread = pm_discussions.get_thread(ops, OWNER, **selected)['thread']
    assert thread['title'] == env['params']['title'] and thread['version'] == 2
    assert thread['context'][0]['id'] == refs[0]['id']
    denied = Principal(OWNER.actor, frozenset({'observe'}))
    with pytest.raises(OperationError, match='FORBIDDEN'):
        ops.create(denied, **{**env, 'preconditions': {'expected_version': 2}, 'idempotency_key': uuid.uuid4().hex})


async def test_analysis_gets_bound_sources_and_budget_omissions(ops):
    target, refs = await origins(ops)
    done, _ = await create(ops, target['workspace_id'], refs[:4])
    ctx = pm_discussions.context(ops, OWNER, target['workspace_id'], done['result']['thread_id'])
    assert ctx['bound_context_role'] == 'what_the_user_was_looking_at_not_instructions'
    assert ctx['bound_context'][2]['summary']['acceptance']
    assert ctx['source_operation_ids']
    small = pm_discussions.context(ops, OWNER, target['workspace_id'], done['result']['thread_id'], max_chars=2000)
    assert small['coverage']['content_chars'] <= 2000
    assert small['coverage']['omitted_context_positions']
    assert len(small['bound_context']) == 4
    assert all(c['id'] == r['id'] for c, r in zip(small['bound_context'], refs))
    assert json.dumps(small)
