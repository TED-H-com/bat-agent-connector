"""Original pre-send callback and native replay through real managed Git fixtures."""
import copy
import sqlite3
from types import SimpleNamespace

import pytest

from bat_agent_connector import api_auth, confinement, pm_runtime, registry
from bat_agent_connector import pm_maintenance_repair_runtime as runtime
from bat_agent_connector import pm_maintenance_repairs as repairs
from bat_agent_connector.operations import OperationError
from tests.test_pm_lifecycle import decide
from tests.test_pm_maintenance_repair_quality import (  # noqa: F401
    OWNER,
    advance,
    execute,
    fixed,
    original_publication_authority,
    pm,
    quality_repair,
    ready,
    view,
    world,
)
from tests.test_pm_maintenance_repair_quality import publishing as publishing_fixture
from tests.test_pm_maintenance_repair_quality import repair_ready as repair_fixture

publishing = publishing_fixture
repair_ready = repair_fixture


@pytest.fixture(autouse=True)
def native_replay(mock):
    state = {'reason': None, 'after': None, 'gap': False, 'extra_input': False, 'page_size': 100,
        'before_input': False, 'drift': False}
    def sync(params):
        sid = params['sessionId']
        meta = mock.metas[sid]
        meta.update(sdkSessionId='native-' + sid, numTurns=int(bool(mock.states[sid]['messages'])))
        cursor = params.get('cursor')
        source = mock.states[sid]['messages']
        if cursor is None or state['gap']:
            return {'mode': 'snapshot', 'sessionId': sid, 'cursor': {'epoch': 'original-' + sid, 'seq': 0},
                'state': {'sessionId': sid, 'meta': copy.deepcopy(meta), 'messages': copy.deepcopy(source), 'isStreaming': False}}
        def event(seq, channel, **body):
            return {'seq': seq, 'channel': channel, 'params': {'sessionId': sid, **body}}
        events = []
        if source:
            events += [event(1, 'claude:message', message={'id': 'original-input', 'role': 'user', 'content': source[0]['content']}),
                event(2, 'claude:status', meta={**meta, 'numTurns': 1, 'isStreaming': False}),
                event(3, 'claude:message', message={'id': 'original-output', 'role': 'assistant', 'content': 'Fixed candidate recorded.'})]
            if state['drift']:
                events[1]['params']['meta']['permissionMode'] = 'bypassPermissions'
            if state['reason'] is not None:
                events.append(event(4, 'claude:turn-end', payload={'reason': state['reason'], 'turnId': 'original-native-turn',
                    'sdkSessionId': 'native-' + sid, 'result': 'Fixed candidate recorded.'}))
            if state['extra_input']:
                events.append(event(len(events) + 1, 'claude:message', message={'id': 'later-input', 'role': 'user', 'content': 'Another instruction'}))
        elif state['before_input']:
            events.append(event(1, 'claude:message', message={'id': 'earlier-input', 'role': 'user', 'content': 'Unexpected before send'}))
        available = [item for item in events if item['seq'] > cursor['seq']]
        selected = available[:state['page_size']]
        result = {'mode': 'delta', 'sessionId': sid, 'cursor': {'epoch': cursor['epoch'],
            'seq': selected[-1]['seq'] if selected else cursor['seq']}, 'events': selected, 'hasMore': len(selected) < len(available)}
        if state['after']:
            state.pop('after')()
            state['after'] = None
        return result
    mock.handlers['claude:sync-session'] = sync
    return state


@pytest.fixture
async def observed(publishing, native_replay, monkeypatch):
    w = publishing
    monkeypatch.setattr(runtime, 'time', SimpleNamespace(time=lambda: w['clock'][0]))
    native_replay['reason'] = 'completed'
    w['parent'] = await advance(w, w['parent'], rounds=5)
    assert w['parent']['result']['outcome'] == 'published_for_review'
    return w


async def observe(w):
    ops = w['d'].ops
    parent = ops.get(w['parent']['operation_id'])
    repairs.reconcile(ops)
    reader = runtime.observer(ops, parent)
    assert reader is not None
    await ops._execute(reader['operation_id'])
    return ops.get(reader['operation_id'])


async def test_actual_original_cursor_native_completion_and_quality_release(observed, mock):
    w, ops = observed, observed['d'].ops
    origin = runtime.origin(ops, w['parent'])
    assert origin['status'] == 'captured'
    assert origin['execution_operation_id'] == w['child']['operation_id']
    assert origin['sdk_session_id'] == 'native-' + w['child']['result']['session_id']
    calls = mock.channels()
    start, send = calls.index('claude:start-session'), calls.index('claude:send-message')
    assert 'claude:sync-session' in calls[start:send]
    reader = await observe(w)
    assert reader['status'] == 'succeeded', reader
    proof = runtime.proof(ops, OWNER, w['parent'])
    assert proof['native_turn_id'] == 'original-native-turn'
    assert proof['delivery']['candidate_commit'] == w['candidate']
    assert proof['delivery']['tree_hash'] == w['tree']
    assert proof['runtime_status'] == 'original_turn_completed'
    assert proof['process_death_proven'] is False and proof['repair_fixed'] is False
    assert not view(w, w['parent'])['capacity_held']
    assert view(w, w['parent'])['runtime_completion'] == proof
    with pytest.raises(sqlite3.IntegrityError):
        ops.db.execute("UPDATE pm_repair_runtime_proofs SET document='{}'")
    await observe(w)
    assert runtime.observer(ops, w['parent'])['operation_id'] == reader['operation_id']
    assert len(w['posts']) == 1 and mock.channels().count('claude:send-message') == 1


@pytest.mark.parametrize('failure', ['idle', 'abort', 'gap', 'input', 'drift', 'incarnation'])
async def test_idle_abort_gap_later_input_or_changed_identity_never_release(observed, native_replay, failure):
    w, ops = observed, observed['d'].ops
    if failure == 'idle':
        native_replay['reason'] = None
    elif failure == 'abort':
        native_replay['reason'] = 'aborted'
    elif failure == 'gap':
        native_replay['gap'] = True
    elif failure == 'input':
        native_replay['extra_input'] = True
    elif failure == 'drift':
        native_replay['drift'] = True
    else:
        registry.update('h1', w['child']['result']['session_id'], controller_incarnation='f' * 32)
    reader = await observe(w)
    assert reader['status'] != 'succeeded', reader
    assert view(w, w['parent'])['capacity_held']
    assert runtime.proof(ops, OWNER, w['parent']) is None
    if failure == 'gap':
        native_replay['gap'] = False
        await ops._execute(reader['operation_id'])
        assert runtime.proof(ops, OWNER, w['parent']) is None


async def test_contiguous_pages_survive_reexecution_and_late_terminal(observed, native_replay):
    w = observed
    native_replay['page_size'] = 1
    first = await observe(w)
    assert first['status'] == 'waiting_external' and view(w, w['parent'])['capacity_held']
    for _ in range(4):
        reader = await observe(w)
    assert reader['status'] == 'succeeded', reader
    assert w['d'].ops.db.execute('SELECT COUNT(*) FROM pm_repair_runtime_pages').fetchone()[0] == 4


async def test_pause_deadline_and_read_only_credential_keep_original_observation(observed):
    w, ops = observed, observed['d'].ops
    await decide(ops, w['workspace_id'], who=OWNER)
    w['clock'][0] += 10000
    api_auth.revoke(ops.db, OWNER.actor)
    api_auth.issue(ops.db, OWNER.actor, ['observe'])
    reader = await observe(w)
    assert reader['status'] == 'succeeded', reader
    assert not view(w, w['parent'])['capacity_held']


async def test_revoked_credential_then_rotation_reads_without_resend(observed, mock):
    w, ops = observed, observed['d'].ops
    api_auth.revoke(ops.db, OWNER.actor)
    first = await observe(w)
    assert first['status'] != 'succeeded'
    assert view(w, w['parent'])['capacity_held']
    api_auth.issue(ops.db, OWNER.actor, ['observe'])
    import time
    w['clock'][0] = max(w['clock'][0], time.time() + 1)
    await ops._execute(first['operation_id'])
    assert ops.get(first['operation_id'])['status'] == 'succeeded'
    assert mock.channels().count('claude:send-message') == 1


async def test_source_withdrawal_blocks_both_replay_and_historical_proof(observed, monkeypatch):
    from bat_agent_connector import pm_quality_sources
    w, ops = observed, observed['d'].ops
    await observe(w)
    original = pm_quality_sources.proof_access
    def hidden(*args, **kwargs):
        raise OperationError('NOT_FOUND', 'Original verifier unavailable', 404)
    monkeypatch.setattr(pm_quality_sources, 'proof_access', hidden)
    with pytest.raises(OperationError):
        runtime.proof(ops, OWNER, w['parent'])
    assert not runtime.operation_visible(ops.db, OWNER, runtime.observer(ops, w['parent']))
    monkeypatch.setattr(pm_quality_sources, 'proof_access', original)
    assert runtime.proof(ops, OWNER, w['parent']) is not None


async def test_public_request_cannot_create_second_observer(observed):
    w, ops = observed, observed['d'].ops
    request = runtime._request(w['parent'], runtime.origin(ops, w['parent']))
    request['idempotency_key'] = 'foreign-reader'
    with pytest.raises(OperationError):
        ops.create(OWNER, **request)


def test_coding_helper_cannot_weaken_read_only_default():
    from tests.test_pm_runtime import BASE, SDK, SID, assess, snapshot, transcript
    record = confinement.snapshot('codex', confinement.CONFINED_OPTIONS['codex'])
    raw = snapshot()
    raw['state']['meta'].update(record['options'])
    replay = pm_runtime.parse_replay(raw, SID)
    assert pm_runtime.fresh_owned_send_cursor(replay, SDK, record) == BASE
    with pytest.raises(confinement.ConfinementRefused):
        pm_runtime.fresh_send_cursor(replay, SDK)
    events = transcript()
    events[1]['params']['meta'].update(record['options'])
    assert assess(events)['reason'] == 'permission_options_changed'
    assert pm_runtime.assess_owned_turn(SID, BASE, events, {'epoch': BASE['epoch'], 'seq': 7}, SDK,
        original_confinement=record, expected_prompt_digest=pm_runtime.prompt_digest('Explore options'))['complete']
    with pytest.raises(pm_runtime.RuntimeEvidenceError):
        pm_runtime.assess_owned_turn(SID, BASE, events, BASE, SDK,
            original_confinement=confinement.snapshot('codex', {'codexSandboxMode': 'danger-full-access'}))


async def test_native_completion_does_not_free_unknown_original_publication(publishing, native_replay, monkeypatch):
    w, ops = publishing, publishing['d'].ops
    monkeypatch.setattr(runtime, 'time', SimpleNamespace(time=lambda: w['clock'][0]))
    native_replay['reason'] = 'completed'
    w['lost_post'] = True
    w['parent'] = await advance(w, w['parent'], rounds=5)
    for _ in range(2):
        reader = await observe(w)
        assert reader['status'] == 'waiting_external', reader
    assert view(w, w['parent'])['capacity_held']
    assert runtime.proof(ops, OWNER, w['parent']) is None
    assert len(w['posts']) == 1


async def test_changed_actual_candidate_after_verifier_and_publication_stays_held(observed):
    from tests.test_integration import commit
    w = observed
    commit(w['child']['result']['worktree_path'], 'later.txt', 'Later model change', 'Changed after publication')
    reader = await observe(w)
    assert reader['status'] == 'needs_attention' and reader['error_code'] == 'PM_REPAIR_RUNTIME_CANDIDATE_CHANGED'
    assert view(w, w['parent'])['capacity_held']


async def test_unavailable_origin_is_never_upgraded_from_later_idle_snapshot(repair_ready, mock, native_replay):
    from tests.test_pm_maintenance_repairs import request
    w, ops = repair_ready, repair_ready['d'].ops
    handler = mock.handlers.pop('claude:sync-session')
    w['parent'] = await advance(w, ops.create(OWNER, **request(w))[0])
    original = runtime.origin(ops, w['parent'])
    assert original['status'] == 'unavailable'
    mock.handlers['claude:sync-session'] = handler
    native_replay['reason'] = 'completed'
    repairs.reconcile(ops)
    assert runtime.origin(ops, w['parent']) == original
    assert runtime.observer(ops, w['parent']) is None
    assert view(w, w['parent'])['capacity_held']


async def test_earlier_input_between_cursor_and_first_send_blocks_transport(repair_ready, native_replay, mock):
    from tests.test_pm_maintenance_repairs import request
    w, ops = repair_ready, repair_ready['d'].ops
    native_replay['before_input'] = True
    parent = await advance(w, ops.create(OWNER, **request(w))[0])
    child = repairs._child(ops, parent)
    assert child['status'] in {'failed', 'needs_attention', 'uncertain'}, child
    assert 'claude:send-message' not in mock.channels()
    assert view(w, parent)['capacity_held']
