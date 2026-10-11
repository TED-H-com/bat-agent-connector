"""Central execution provenance and real Git integration, all on disk-backed fixtures."""
import json
from dataclasses import replace

import pytest

from bat_agent_connector import execution_sources, integration, registry, work_items
from bat_agent_connector.operations import OperationError
from tests import test_integration as existing
from tests import test_session_start_operations as starts
from tests.conftest import controller_scope
from tests.test_checkpoints import git
from tests.test_work_items import PERSON, act, project
from tests.test_work_items import item as work_item

world, gh, daemon = existing.world, existing.gh, starts.daemon
hermetic_git = existing.hermetic_git


@pytest.mark.parametrize('observation', [None, {'streaming': None}, {'streaming': False},
    {'streaming': True, 'stale': True, 'observed_at': '2026-10-09T00:00:00Z'},
    {'streaming': False, 'pending': {'kind': 'permission'}}])
async def test_candidate_activity_is_not_completion(world, monkeypatch, observation):
    cp = await world.checkpoint()
    oid, _, _ = await world.agent_result(cp)
    monkeypatch.setattr(world.d.inventory, 'get_session', lambda *args: observation)
    item = next(r for r in integration.candidates(world.d.ops, 'h1')['agent_results'] if r['id'] == oid)
    assert item['streaming'] is (observation or {}).get('streaming')
    assert item['session'] == integration.candidate_session(observation)
    assert item['result'] == {'status': 'unverified', 'commit_sha': None}


async def test_standalone_start_is_discoverable_without_task_or_checkpoint(daemon, mock):
    mock.handlers['git:log'] = lambda p: [{'hash': starts.SHA}]
    op = await starts.create(daemon, params={'prompt': 'Keep this independent work'})
    assert op['status'] == 'succeeded', op
    rows = execution_sources.candidates(daemon.ops, 'h1')
    assert len(rows) == 1 and rows[0]['id'] == op['operation_id'] and rows[0]['eligible'], rows
    assert rows[0]['session_id'] == op['result']['session_id']
    assert execution_sources.resolve(daemon.ops, 'execution', op['operation_id'])['start'] == starts.SHA
    assert not daemon.journal.db.execute('SELECT 1 FROM tasks').fetchone()
    assert not daemon.journal.db.execute('SELECT 1 FROM checkpoints').fetchone()
    registry.update('h1', rows[0]['session_id'], created_at=op['created_at'] + 999)
    rows = execution_sources.candidates(daemon.ops, 'h1')
    assert not rows[0]['eligible'] and rows[0]['unavailable']['code'] == 'SOURCE_LINEAGE_UNPROVEN'


async def test_exact_execution_source_uses_existing_preview_apply_and_receipts(world):
    cp = await world.checkpoint()
    oid, sha, path = await world.agent_result(cp)
    before = existing.tree_digest(path)
    doc = await world.preview([{'kind': 'execution', 'id': oid}])
    assert doc['ready'], doc['blocking']
    assert doc['sources'][0]['pinned_sha'] == sha
    assert doc['sources'][0]['lineage']['execution_operation_id'] == oid
    assert any(warning['code'] == 'BRINGS_FOREIGN_COMMITS' for warning in doc['sources'][0]['warnings'])
    op = await world.apply(doc)
    assert op['status'] == 'succeeded', op
    assert git(world.remote, 'merge-base', '--is-ancestor', sha, world.remote_head()) == ''
    assert existing.tree_digest(path) == before
    receipt = world.receipts(op['operation_id'])[0]
    assert receipt['source_kind'] == 'execution' and receipt['source_id'] == oid and receipt['pinned_sha'] == sha


@pytest.mark.parametrize('change', ['incarnation', 'branch', 'acceptance', 'other_host'])
async def test_changed_execution_cannot_import_or_push(world, change, monkeypatch):
    cp = await world.checkpoint()
    oid, _, _ = await world.agent_result(cp)
    doc = await world.preview([{'kind': 'execution', 'id': oid}])
    sid = world.d.ops.get(oid)['result']['session_id']
    if change == 'incarnation':
        registry.update('h1', sid, created_at=0)
    elif change == 'branch':
        registry.update('h1', sid, branch='other')
    elif change == 'acceptance':
        world.d.journal.db.execute("UPDATE operation_steps SET response='{}' WHERE operation_id=? AND name='send'", (oid,))
    else:
        monkeypatch.setitem(world.d.fleet.config.hosts, 'h2', replace(world.d.fleet.config.host('h1'), name='h2'))
        with pytest.raises(OperationError, match='SOURCE_ON_OTHER_HOST'):
            integration.resolve_sources(world.d.ops, 'h2', [{'kind': 'execution', 'id': oid}])
        return
    op = await world.apply(doc)
    assert op['status'] == 'failed' and op['error_code'] in {'SOURCE_LINEAGE_UNPROVEN', 'SOURCE_CHANGED'}, op
    assert not world.runner.of('apply-prepare') and not world.runner.of('push')


async def test_task_command_result_and_project_binding(world):
    pid = await project(world.d, 'Task mapping', task_project='project-task')
    other = await project(world.d, 'Explicit links')
    cp = await world.checkpoint()
    oid, sha, _ = await world.agent_result(cp)
    sid = world.d.ops.get(oid)['result']['session_id']
    with controller_scope(world.d.journal, existing.OPERATOR):
        task = world.d.journal.submit(project='project-task', host='h1', workspace='demo-project', original_words='Task result',
                                      engine='goose', idempotency_key='delivery-task')
    assert task['controller_authority'] == registry.get('h1', sid)['controller_authority']
    tid = task['task_id']
    world.d.journal.change(tid, 'dispatching', fields={'session_id': sid})
    world.d.journal.change(tid, 'accepted')
    registry.update('h1', sid, task_id=tid, role='lead')
    sent = await world.run('session.send', {'host': 'h1', 'session_id': sid}, {'text': 'Review the result'})
    assert sent['status'] == 'succeeded', sent
    cid = sent['external_refs']['command_id']
    rows = execution_sources.candidates(world.d.ops, 'h1')
    item = next(row for row in rows if row['kind'] == 'task_command')
    assert item['id'] == cid and item['task_id'] == tid and item['eligible'], item
    assert item['project_ids'] == [pid]
    work = work_items.project_get(world.d.journal.db, pid, ops=world.d.ops)['work']
    assert len(work) == 1 and work[0]['id'] == cid and work[0]['eligible']
    assert not work_items.project_get(world.d.journal.db, other, ops=world.d.ops)['work']
    wid = await work_item(world.d, other, 'Linked task')
    await act(world.d, PERSON, 'work_item.link', {'work_item_id': wid}, {'kind': 'task', 'ref': tid})
    assert work_items.project_get(world.d.journal.db, other, ops=world.d.ops)['work'][0]['id'] == cid
    doc = await world.preview([{'kind': 'task_command', 'id': cid}])
    assert doc['ready'] and doc['sources'][0]['pinned_sha'] == sha
    op = await world.apply(doc)
    assert op['status'] == 'succeeded', op
    assert work_items.project_get(world.d.journal.db, pid, ops=world.d.ops)['work'][0]['delivered_to'][0]['pinned_sha'] == sha
    world.d.journal.db.execute("DELETE FROM events WHERE task_id=? AND kind IN ('command_accepted','command_settled')", (tid,))
    with pytest.raises(OperationError, match='SOURCE_LINEAGE_UNPROVEN'):
        execution_sources.resolve(world.d.ops, 'task_command', cid)


async def test_lost_push_reconciles_original_commit_even_after_registry_changes(world):
    cp = await world.checkpoint()
    oid, sha, _ = await world.agent_result(cp)
    doc = await world.preview([{'kind': 'execution', 'id': oid}])
    world.runner.lose_after.add('push')
    def changed():
        registry.update('h1', world.d.ops.get(oid)['result']['session_id'], created_at=0)
    world.runner.after['push'] = changed
    op = await world.apply(doc)
    assert op['status'] == 'succeeded', op
    assert world.runner.ran['push'] == 1
    assert world.receipts(op['operation_id'])[0]['pinned_sha'] == sha
    stored = json.loads(world.d.journal.db.execute('SELECT sources FROM integration_previews WHERE preview_id=?',
                                                  (doc['preview_id'],)).fetchone()[0])
    assert stored[0]['registry_identity']['created_at'] != 0
