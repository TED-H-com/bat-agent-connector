"""Completed owned turn -> authenticated inventory -> new workload capture."""
import copy
import json
import time
from types import SimpleNamespace

import pytest

from bat_agent_connector import inventory, pm_workload, service
from bat_agent_connector import pm_github_observation as github
from bat_agent_connector import pm_maintenance_repair_runtime as runtime
from bat_agent_connector import pm_workload_sources as sources
from bat_agent_connector.operations import OperationError
from tests.test_pm_maintenance_repair_runtime import (  # noqa: F401
    OWNER,
    advance,
    execute,
    fixed,
    native_replay,
    observe,
    original_native_agent,
    original_publication_authority,
    pm,
    quality_repair,
    ready,
    repair_ready,
    view,
    world,
)
from tests.test_pm_maintenance_repair_runtime import observed as observed_fixture
from tests.test_pm_maintenance_repair_runtime import publishing as publishing_fixture
from tests.test_pm_maintenance_repairs import shared_request

observed = observed_fixture
publishing = publishing_fixture


@pytest.fixture
async def completed(observed, mock, monkeypatch):
    w = observed
    done = await observe(w)
    assert done['status'] == 'succeeded', json.dumps(done, indent=2)
    w['runtime_proof'] = runtime.proof(w['d'].ops, OWNER, w['parent'])
    w['clock'][0] += 1
    monkeypatch.setattr(inventory, 'time', SimpleNamespace(time=lambda: w['clock'][0],
        strftime=time.strftime, gmtime=time.gmtime))
    # The test's human checkout stays intact. These default mock tabs are not
    # part of this scenario; the real managed registry enumerates its own child.
    mock.ws_doc['terminals'] = []
    w['d'].inventory.settings.activity_every = 100
    w['d'].inventory._runs['h1'] = 1
    # The original publication stub returns a minimal POST receipt on /pulls.
    # Use the provider's real bounded GET for this later no-open-PR snapshot.
    client = w['d'].ops.context['github']
    monkeypatch.setattr(client, 'bounded_get', type(client).bounded_get.__get__(client))
    await refresh(w)
    return w


async def refresh(w):
    await w['d'].inventory.refresh_host('h1')
    observed = await execute(w['d'].ops, shared_request(w, github.ACTION), who=OWNER)
    assert observed['result']['coverage_status'] == 'complete', json.dumps(github.get_observation(w['d'].ops, OWNER, w['workspace_id'], observed['result']['observation_id']), indent=2)


async def capture(w):
    ops = w['d'].ops
    op = await execute(ops, shared_request(w, pm_workload.ACTION), who=OWNER)
    return op, pm_workload.get_snapshot(ops, OWNER, w['workspace_id'], op['result']['snapshot_id'])['snapshot']['document']


def changes(w, **delta):
    ops, proof = w['d'].ops, w['runtime_proof']
    row = ops.db.execute('SELECT * FROM sessions_observed WHERE host=? AND session_id=?',
        (proof['host'], proof['session_id'])).fetchone()
    body = {**json.loads(row['body']), **delta}
    ops.db.execute('UPDATE sessions_observed SET body=?,digest=? WHERE host=? AND session_id=?',
        (json.dumps(body), sources.digest(body), proof['host'], proof['session_id']))


async def test_original_completed_runtime_is_excluded_by_new_real_capture(completed, mock):
    w, ops = completed, completed['d'].ops
    original_tasks = ops.db.execute('SELECT COUNT(*) FROM tasks').fetchone()[0]
    sent = mock.channels().count('claude:send-message')
    op, doc = await capture(w)
    assert doc['classification'] == 'idle_candidate', doc['blocking_reasons']
    excluded = {v['kind']: v for v in doc['exclusions'] if v['reason'] == 'original_maintenance_attempt_completed'}
    assert set(excluded) == {'work', 'operation', 'session'}, doc
    assert excluded['operation']['id'] == w['child']['operation_id']
    assert excluded['session']['id'] == 'h1/' + w['child']['result']['session_id']
    assert all(v['runtime_observation_id'] == w['runtime_proof']['observation_operation_id'] for v in excluded.values())
    row = pm_workload._record(ops.db, w['workspace_id'], op['result']['snapshot_id'])
    assert any(r['kind'] == 'repair_runtime_completion' for r in row['source_binding']['source_refs'])
    assert ops.db.execute('SELECT COUNT(*) FROM tasks').fetchone()[0] == original_tasks
    assert mock.channels().count('claude:send-message') == sent
    assert not w['runtime_proof']['repair_fixed'] and not w['runtime_proof']['process_death_proven']


@pytest.mark.parametrize('change', ['new_sdk', 'second_input', 'pending_input', 'new_epoch', 'missing_identity',
    'streaming', 'pending', 'stale', 'incomplete', 'manual', 'foreign', 'old_inventory'])
async def test_current_later_unknown_foreign_or_incomplete_state_keeps_blocker(completed, change):
    w, proof = completed, completed['runtime_proof']
    identity = copy.deepcopy(proof['input_state'])
    if change == 'new_sdk':
        changes(w, sdk_session_id='another-native-session', num_turns=1)
    elif change == 'second_input':
        identity['received'] = 2
        changes(w, native_input_state=identity, num_turns=1)
    elif change == 'pending_input':
        identity.update(received=2, pending=1)
        changes(w, native_input_state=identity, num_turns=1)
    elif change == 'new_epoch':
        identity['epoch'] = 'b' * 32
        changes(w, native_input_state=identity)
    elif change == 'missing_identity':
        changes(w, native_input_state=None)
    elif change == 'streaming':
        changes(w, streaming=True)
    elif change == 'pending':
        changes(w, pending={'kind': 'ask_user'})
    elif change == 'stale':
        changes(w, fields_stale=True)
    elif change == 'incomplete':
        row = w['d'].ops.db.execute('SELECT body FROM discovery_latest WHERE host=?', ('h1',)).fetchone()
        data = json.loads(row[0])
        data['complete_enumeration'] = False
        w['d'].ops.db.execute('UPDATE discovery_latest SET body=? WHERE host=?', (json.dumps(data), 'h1'))
    elif change == 'manual':
        changes(w, provenance='manual', api_access='read_only')
    elif change == 'foreign':
        changes(w, registry_origin={'controller_incarnation': 'f' * 32})
    else:
        w['d'].ops.db.execute('UPDATE sessions_observed SET last_seen_at=?', (proof['recorded_at'] - 1,))
    _, doc = await capture(w)
    assert doc['classification'] != 'idle_candidate', doc
    assert 'started_attempt_result_unverified' in {v['code'] for v in doc['blocking_reasons']}
    assert not any(v['reason'] == 'original_maintenance_attempt_completed' for v in doc['exclusions'])


async def test_changed_work_content_remains_committed(completed):
    w, ops = completed, completed['d'].ops
    item = runtime.repairs._intent(w['parent'])['work_item_id']
    ops.db.execute("UPDATE work_items SET title='New request',version=version+1,state='in_progress' WHERE work_item_id=?", (item,))
    _, doc = await capture(w)
    assert any(v['source'] == {'kind': 'work', 'id': item} and v['code'] == 'work_committed' for v in doc['blocking_reasons'])
    assert not any(v['kind'] == 'work' and v['reason'] == 'original_maintenance_attempt_completed' for v in doc['exclusions'])


async def test_completion_source_withdrawal_hides_original_snapshot(completed, monkeypatch):
    w, ops = completed, completed['d'].ops
    op, _ = await capture(w)
    original = runtime._access
    def denied(*args):
        raise OperationError('NOT_FOUND', 'Original proof source withdrawn', 404)
    monkeypatch.setattr(runtime, '_access', denied)
    with pytest.raises(OperationError):
        pm_workload.get_snapshot(ops, OWNER, w['workspace_id'], op['result']['snapshot_id'])
    assert not pm_workload.operation_visible(ops.db, OWNER, op)
    monkeypatch.setattr(runtime, '_access', original)
    assert pm_workload.operation_visible(ops.db, OWNER, op)


async def test_inventory_projects_only_current_native_metadata(completed, mock):
    w, proof = completed, completed['runtime_proof']
    sid = proof['session_id']
    rows = await service._host_sessions(w['d'].inventory.fleet, 'h1', None, None, 'auto', False)
    row = next(r for r in rows if r['session_id'] == sid)
    assert row['sdk_session_id'] == proof['sdk_session_id']
    assert row['native_input_state'] == proof['input_state']
    assert row['registry_origin']['controller_incarnation'] == proof['registry_incarnation']
    mock.metas[sid].pop('sdkSessionId')
    mock.metas[sid].pop('inputState')
    rows = await service._host_sessions(w['d'].inventory.fleet, 'h1', None, None, 'auto', False)
    row = next(r for r in rows if r['session_id'] == sid)
    assert row['sdk_session_id'] is None and row['native_input_state'] is None


def test_input_identity_requires_original_zero_input_epoch():
    first = {'schema': runtime.INPUT_SCHEMA, 'epoch': 'a' * 32, 'received': 0, 'pending': 0}
    original = {'input_state': first}
    assert runtime.same_original_input(original, {**first, 'received': 1})
    assert not runtime.same_original_input({}, {**first, 'received': 1})
    assert not runtime.same_original_input(original, {**first, 'received': 1, 'pending': 1})
    assert runtime.input_state({'inputState': {**first, 'received': True}}) is None
    assert runtime.input_state({'inputState': {**first, 'pending': 1}}) is None


@pytest.fixture
async def identity_unavailable(native_replay):  # noqa: F811
    native_replay['input_identity'] = False


async def test_original_missing_input_counter_cannot_upgrade_later(identity_unavailable, publishing, native_replay, monkeypatch):  # noqa: F811
    w = publishing
    monkeypatch.setattr(runtime, 'time', SimpleNamespace(time=lambda: w['clock'][0]))
    native_replay.update(reason='completed', input_identity=True)
    w['parent'] = await advance(w, w['parent'], rounds=5)
    reader = await observe(w)
    assert reader['status'] != 'succeeded', reader
    assert runtime.origin(w['d'].ops, w['parent'])['input_state'] is None
    assert runtime.proof(w['d'].ops, OWNER, w['parent']) is None
    assert view(w, w['parent'])['capacity_held']


async def test_queued_input_counter_keeps_original_capacity(observed, native_replay):  # noqa: F811
    native_replay['input_pending'] = 1
    reader = await observe(observed)
    assert reader['status'] != 'succeeded', reader
    assert view(observed, observed['parent'])['capacity_held']
