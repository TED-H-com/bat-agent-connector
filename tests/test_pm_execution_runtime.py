"""Runtime evidence is independent of passing fixed verification/quality receipts."""
import copy
import json

import pytest

from bat_agent_connector import api_auth, pm_access, registry
from bat_agent_connector import pm_execution_runtime as runtime
from bat_agent_connector.operations import OperationError
from tests import pm_joined_lifecycle_helpers as story
from tests.operation_helpers import settle_operations
from tests.test_pm_joined_lifecycle import bat_world, daemon, joined, world  # noqa: F401


async def drive(w, child):
    ops = w['d'].ops
    reader = runtime.observer(ops, child)
    assert reader
    await ops._execute(reader['operation_id'])
    return ops.get(reader['operation_id'])


@pytest.fixture
async def uncompleted(joined, mock, monkeypatch):  # noqa: F811
    # Real independent verifiers and responsible reviews are already passing,
    # but no native terminal can be saved before the negative is injected.
    with monkeypatch.context() as held:
        held.setattr(story, 'complete_native_turn', lambda *_: None)
        w = await story.develop_original_requirements(joined, mock)
    ops = w['d'].ops
    w['original_children'] = [ops.get(oid) for oid in w['ledger'].references['execution_ids']]
    for child in w['original_children']:
        reader = await drive(w, child)
        assert reader['status'] == 'waiting_external', reader
        assert runtime.proof(ops, story.OWNER, child) is None
        parent, entry = runtime.relation(ops, child)
        assert runtime.batches._verified_proof(ops, story.OWNER, parent, entry)['status'] == 'passed'
    return w


def finish(w, mock):
    for child in w['original_children']:
        story.complete_native_turn(w, mock, child['result']['session_id'])


@pytest.mark.parametrize('failure', ['idle', 'abort', 'gap', 'later_input', 'pending_input', 'input_epoch', 'sdk', 'confinement', 'incarnation'])
async def test_passing_quality_cannot_replace_original_native_turn(uncompleted, mock, monkeypatch, failure):
    w, ops = uncompleted, uncompleted['d'].ops
    child = w['original_children'][-1]
    sid = child['result']['session_id']
    finish(w, mock)
    if failure == 'idle':
        w['coding'][sid]['reason'] = None
    elif failure == 'abort':
        w['coding'][sid]['reason'] = 'aborted'
    elif failure == 'later_input':
        mock.states[sid]['messages'].append({'id': 'later', 'role': 'user', 'content': 'An unrelated later instruction'})
    elif failure == 'pending_input':
        w['coding'][sid]['pending'] = 1
    elif failure == 'input_epoch':
        w['coding'][sid]['input_epoch'] = 'd' * 32
    elif failure == 'confinement':
        mock.metas[sid]['codexSandboxMode'] = 'danger-full-access'
    elif failure == 'incarnation':
        original_get = registry.get
        def changed(host, selected):
            value = original_get(host, selected)
            return {**value, 'controller_incarnation': 'e' * 32} if host == 'h1' and selected == sid else value
        monkeypatch.setattr(registry, 'get', changed)
    else:
        dispatch = mock.dispatch
        def changed(channel, params):
            value = dispatch(channel, params)
            if params.get('sessionId') == sid:
                if failure == 'gap' and channel == 'claude:sync-session' and params.get('cursor'):
                    return dispatch(channel, {'sessionId': sid})
                if failure == 'sdk' and channel == 'claude:get-session-meta':
                    return {**value, 'sdkSessionId': 'different-original-sdk'}
            return value
        monkeypatch.setattr(mock, 'dispatch', changed)
    writes = mock.channels().count('claude:send-message')
    reader = await drive(w, child)
    assert reader['status'] != 'succeeded', reader
    assert reader['error_code'] != 'INTERNAL', reader
    assert runtime.proof(ops, story.OWNER, child) is None
    assert mock.channels().count('claude:send-message') == writes
    story.assert_manual_preserved(w, mock)
    if failure == 'gap':
        monkeypatch.setattr(mock, 'dispatch', dispatch)
        await drive(w, child)
        assert runtime.proof(ops, story.OWNER, child) is None


async def test_current_credential_required_then_completed_receipt_replays(uncompleted, mock):
    w, ops = uncompleted, uncompleted['d'].ops
    child = w['original_children'][-1]
    finish(w, mock)
    sends = mock.channels().count('claude:send-message')
    api_auth.revoke(ops.db, story.OWNER.actor)
    denied = await drive(w, child)
    assert denied['status'] == 'waiting_external'
    assert runtime.proof(ops, story.OWNER, child) is None
    api_auth.issue(ops.db, story.OWNER.actor, ['observe'])
    restored = await drive(w, child)
    assert restored['status'] == 'succeeded', restored
    original = runtime.proof(ops, story.OWNER, child)
    api_auth.revoke(ops.db, story.OWNER.actor)
    await ops._execute(restored['operation_id'])
    assert runtime.proof(ops, story.OWNER, child) == original
    assert mock.channels().count('claude:send-message') == sends


async def test_private_replay_diagnostics_stay_private_and_withdrawal_hides_whole_observer(uncompleted, mock, monkeypatch):
    w, ops = uncompleted, uncompleted['d'].ops
    child = w['original_children'][-1]
    sid = child['result']['session_id']
    finish(w, mock)
    sentinel = 'PRIVATE-RUNTIME-DIAGNOSTIC-not-a-public-proof'
    dispatch = mock.dispatch
    def decorated(channel, params):
        value = dispatch(channel, params)
        if channel == 'claude:sync-session' and params.get('sessionId') == sid:
            value = copy.deepcopy(value)
            for event in value.get('events', []):
                event['params']['privateDiagnostic'] = {'path': sentinel, 'environment': sentinel}
        return value
    monkeypatch.setattr(mock, 'dispatch', decorated)
    reader = await drive(w, child)
    assert reader['status'] == 'succeeded', reader
    assert sentinel in ''.join(row[0] for row in ops.db.execute('SELECT response FROM operation_steps WHERE operation_id=? AND response IS NOT NULL', (reader['operation_id'],)))
    assert sentinel not in json.dumps(ops.get(reader['operation_id'], principal=story.OWNER))
    events = [dict(row) for row in ops.db.execute('SELECT * FROM api_events WHERE resource_type=\'operation\' AND resource_id=?', (reader['operation_id'],))]
    assert events and sentinel not in json.dumps(events)
    assert all(pm_access.event_visible(ops.db, story.OWNER, {**row, 'body': json.loads(row['body'])}) for row in events)
    ops.db.execute('DELETE FROM pm_workspace_members WHERE workspace_id=? AND actor=?', (w['workspace_id'], story.OWNER.actor))
    assert not runtime.operation_visible(ops.db, story.OWNER, reader)
    with pytest.raises(OperationError, match='NOT_FOUND'):
        ops.get(reader['operation_id'], principal=story.OWNER)
    assert all(not pm_access.event_visible(ops.db, story.OWNER, {**row, 'body': json.loads(row['body'])}) for row in events)
    assert ops.db.execute('SELECT COUNT(*) FROM tasks').fetchone()[0] == 0


async def test_private_observer_cannot_be_forged_or_replaced(uncompleted):
    w, ops = uncompleted, uncompleted['d'].ops
    child = w['original_children'][-1]
    original = runtime.observer(ops, child)
    envelope = runtime._request(child, runtime.origin(ops, child))
    same, fresh = ops.create(story.OWNER, **envelope)
    assert not fresh and same['operation_id'] == original['operation_id']
    with pytest.raises(OperationError, match='service-created'):
        ops.create(story.OWNER, **{**envelope, 'idempotency_key': 'forged-reader'})
    await settle_operations(ops)
    assert runtime.observer(ops, child)['operation_id'] == original['operation_id']


async def test_changed_clean_candidate_cannot_complete_original_selected_result(uncompleted, mock):
    from pathlib import Path

    from tests.test_integration import commit
    w, ops = uncompleted, uncompleted['d'].ops
    child = w['original_children'][-1]
    finish(w, mock)
    commit(Path(child['result']['worktree_path']), 'later.txt', 'Not the selected verified candidate', 'Later candidate')
    reader = await drive(w, child)
    assert reader['status'] == 'needs_attention' and reader['error_code'] == 'PM_EXECUTION_RUNTIME_CANDIDATE_CHANGED', reader
    assert runtime.proof(ops, story.OWNER, child) is None


@pytest.mark.parametrize('change', ['credential', 'pending_input'])
async def test_final_candidate_frame_does_not_skip_later_native_or_authority_change(uncompleted, mock, monkeypatch, change):
    from bat_agent_connector import pm_execution_verification
    w, ops = uncompleted, uncompleted['d'].ops
    child = w['original_children'][-1]
    sid = child['result']['session_id']
    finish(w, mock)
    original = pm_execution_verification.configuration
    seen = []
    async def change_after_read(*args, **kwargs):
        result = await original(*args, **kwargs)
        seen.append(change)
        if change == 'credential':
            api_auth.revoke(ops.db, story.OWNER.actor)
        else:
            w['coding'][sid]['pending'] = 1
        return result
    monkeypatch.setattr(pm_execution_verification, 'configuration', change_after_read)
    reader = await drive(w, child)
    assert seen == [change]
    assert reader['status'] != 'succeeded' and reader['error_code'] != 'INTERNAL', reader
    assert runtime.proof(ops, story.OWNER, child) is None
