"""Serial manual reads use complete producer-shaped evidence, never last reply.

Pinned BAT c397fd7 codex_app_server.rs: metadata637/653; send echo5912 then
status5915; native completion metadata8314/status8320/result8325/end8344.
This is a source-shaped offline fixture, not a captured live model transcript.
No invented input ordinal or client-ID-to-turn binding is added to the wire.
"""
from __future__ import annotations

import copy
import datetime as dt
import json
import time
import uuid
from dataclasses import replace
from types import SimpleNamespace

import pytest

from bat_agent_connector import api_auth, pm_access
from bat_agent_connector import pm_session_quiescence as manual
from bat_agent_connector import pm_workload_sources as workload
from bat_agent_connector.config import HostConfig
from bat_agent_connector.operations import OpContext, OperationError, OperationService
from bat_agent_connector.task_journal import Journal
from tests.test_pm_evidence import OWNER, link, prepared, session
from tests.test_pm_evidence import ops as ops  # noqa: F401

SID, SDK, EPOCH, REPLAY = 'manual-source', 'native-thread', 'a' * 32, 'bat-replay-generation'


def wire(n=2):
    def meta(received, pending=0, active=False):
        return {'sdkSessionId': SDK, 'isStreaming': active, 'numTurns': received,
            'model': 'fixture-model', 'codexSandboxMode': 'workspace-write', 'codexApprovalPolicy': 'on-request',
            'inputState': {'schema': 'bat_input_state_v1', 'epoch': EPOCH, 'received': received, 'pending': pending}}
    records = []
    def event(channel, **params):
        records.append({'seq': len(records) + 1, 'channel': channel, 'params': {'sessionId': SID, **params}})
    event('claude:message', message={'id': 'sys-1000', 'sessionId': SID, 'role': 'system',
        'content': 'Codex session started (sandbox: workspace-write, approval: on-request)', 'timestamp': 1000})
    event('claude:status', meta=meta(0))
    event('claude:worktree-info', payload={'path': '/private/human-worktree'})
    for ordinal in range(1, n + 1):
        event('claude:message', message={'id': f'user-{1000 + ordinal}', 'sessionId': SID,
            'role': 'user', 'content': f'PRIVATE original instruction {ordinal}', 'timestamp': 1000 + ordinal})
        event('claude:status', meta=meta(ordinal, pending=1, active=True))
        event('claude:message', message={'id': f'answer-{ordinal}', 'role': 'assistant', 'content': 'Original result.'})
        event('claude:status', meta=meta(ordinal))
        event('claude:result', result={'subtype': 'success', 'result': 'Original result.', 'totalCost': 0})
        event('claude:turn-end', payload={'reason': 'completed', 'result': 'Original result.',
            'turnId': f'native-turn-{ordinal}', 'sdkSessionId': SDK})
    final = meta(n)
    origin = {'schema': manual.SERIAL_ORIGIN, 'session_id': SID, 'sdk_session_id': SDK,
        'input_state': final['inputState'], 'send_cursor': {'epoch': REPLAY, 'seq': 0}}
    return origin, records, final


def assess(origin, events, **kwargs):
    return manual.assess_serial_inputs(origin, events,
        kwargs.pop('cursor', {'epoch': REPLAY, 'seq': events[-1]['seq'] if events else 0}), **kwargs)


@pytest.mark.parametrize('count', [2, 3, manual.MAX_SERIAL_INPUTS])
def test_actual_codex_status_and_terminal_shape_support_serial_turns(count):
    origin, events, _ = wire(count)
    outcome = assess(origin, events)
    assert outcome['complete'], outcome
    assert len(outcome['turns']) == count
    assert [turn['native_turn_id'] for turn in outcome['turns']] == [f'native-turn-{i}' for i in range(1, count + 1)]
    assert all(turn['input_event_seq'] < turn['terminal_event_seq'] for turn in outcome['turns'])
    assert not assess(origin, events, has_more=True)['complete']


@pytest.mark.parametrize('failure', ['overlap', 'missing_terminal', 'duplicate_terminal', 'duplicate_turn',
    'duplicate_user', 'missing_status', 'missing_counter', 'wrong_ordinal', 'queued_counter', 'input_epoch',
    'sdk', 'abort', 'error', 'resting', 'claude_missing_turn', 'local_fallback_turn', 'no_sdk_terminal',
    'truncated_output', 'late_assistant', 'orphan_terminal', 'history_checkpoint', 'gap', 'replay_epoch',
    'cursor_gap', 'extra_received', 'pending', 'above_bound', 'initial_nonzero', 'late_error'])
def test_ambiguous_or_incomplete_history_never_becomes_quiescence(failure):
    origin, events, _ = wire()
    statuses = [row for row in events if row['channel'] == 'claude:status']
    users = [row for row in events if row['params'].get('message', {}).get('role') == 'user']
    ends = [row for row in events if row['channel'] == 'claude:turn-end']
    kwargs = {}
    if failure == 'overlap':
        events.remove(users[1])
        events.insert(3, users[1])
    elif failure == 'missing_terminal':
        events.remove(ends[0])
    elif failure == 'duplicate_terminal':
        events.insert(events.index(ends[0]), copy.deepcopy(ends[0]))
    elif failure == 'duplicate_turn':
        ends[1]['params']['payload']['turnId'] = ends[0]['params']['payload']['turnId']
    elif failure == 'duplicate_user':
        users[1]['params']['message']['id'] = users[0]['params']['message']['id']
    elif failure == 'missing_status':
        events = [row for row in events if row not in statuses[1:3]]
    elif failure == 'missing_counter':
        statuses[1]['params']['meta'].pop('inputState')
    elif failure == 'wrong_ordinal':
        statuses[3]['params']['meta']['inputState']['received'] = 1
    elif failure == 'queued_counter':
        statuses[1]['params']['meta']['inputState'].update(received=2, pending=2)
    elif failure == 'input_epoch':
        statuses[3]['params']['meta']['inputState']['epoch'] = 'b' * 32
    elif failure == 'sdk':
        statuses[3]['params']['meta']['sdkSessionId'] = 'other-sdk'
    elif failure in {'abort', 'error', 'resting'}:
        ends[0]['params']['payload']['reason'] = {'abort': 'aborted'}.get(failure, failure)
    elif failure == 'claude_missing_turn':
        ends[0]['params']['payload'].pop('turnId')
    elif failure == 'local_fallback_turn':
        ends[0]['params']['payload']['turnId'] = 'local-' + SID + '-1'
    elif failure == 'no_sdk_terminal':
        ends[0]['params']['payload'].pop('sdkSessionId')
    elif failure == 'truncated_output':
        ends[0]['params']['payload']['truncated'] = True
    elif failure == 'late_assistant':
        events.append(copy.deepcopy(next(row for row in events if row['params'].get('message', {}).get('role') == 'assistant')))
    elif failure == 'orphan_terminal':
        events.insert(0, copy.deepcopy(ends[0]))
    elif failure == 'history_checkpoint':
        events[3] = {'channel': 'claude:history-checkpoint', 'params': {'sessionId': SID}}
    elif failure == 'late_error':
        events.append({'channel': 'claude:error', 'params': {'sessionId': SID, 'error': 'Unsettled failure'}})
    elif failure == 'extra_received':
        origin['input_state']['received'] += 1
    elif failure == 'pending':
        origin['input_state']['pending'] = 1
    elif failure == 'above_bound':
        origin['input_state']['received'] = manual.MAX_SERIAL_INPUTS + 1
    elif failure == 'initial_nonzero':
        origin['send_cursor']['seq'] = 1
    for seq, event in enumerate(events, 1):
        event['seq'] = seq
    if failure == 'gap':
        events[3]['seq'] += 1
    elif failure == 'replay_epoch':
        kwargs['cursor'] = {'epoch': 'restarted', 'seq': len(events)}
    elif failure == 'cursor_gap':
        kwargs['cursor'] = {'epoch': REPLAY, 'seq': len(events) + 1}
    assert not assess(origin, events, **kwargs)['complete']


class ReadOnlyProvider:
    def __init__(self, host):
        self.host, self.auth_info, self.calls = host, {'authenticated': True}, []
        self.origin, self.events, self.meta = wire()
        self.page_size, self.after_empty = 100, None
        self.tab = {'id': SID, 'workspaceId': 'manual-workspace', 'agentPreset': 'codex-agent'}

    async def invoke(self, channel, params):
        self.calls.append((channel, copy.deepcopy(params)))
        assert channel in {'workspace:load', 'claude:sync-session', 'claude:get-session-meta'}, 'manual write forbidden'
        if channel == 'workspace:load':
            return {'terminals': [copy.deepcopy(self.tab)]}
        assert params['sessionId'] == SID
        if channel == 'claude:get-session-meta':
            return copy.deepcopy(self.meta)
        if 'cursor' not in params:
            return {'mode': 'snapshot', 'sessionId': SID, 'cursor': {'epoch': REPLAY, 'seq': len(self.events)},
                'state': {'sessionId': SID, 'meta': copy.deepcopy(self.meta), 'isStreaming': False,
                    'pendingPermission': None, 'pendingAskUser': None}}
        events = copy.deepcopy(self.events[params['cursor']['seq']:][:self.page_size])
        seq = events[-1]['seq'] if events else params['cursor']['seq']
        result = {'mode': 'delta', 'sessionId': SID, 'cursor': {'epoch': REPLAY, 'seq': seq},
            'events': events, 'hasMore': seq < len(self.events)}
        if not events and self.after_empty:
            self.after_empty()
        return result


def inventory(ops, provider, *, at=None):
    at = time.time() if at is None else at
    data = {'session_id': SID, 'workspace_id': 'manual-workspace', 'profile_id': provider.host.profile_id,
        'provenance': 'manual', 'loaded': True, 'streaming': False, 'pending': False,
        'sdk_session_id': SDK, 'native_input_state': copy.deepcopy(provider.meta['inputState']),
        'field_evidence': {'native_input': 'session_meta'}}
    ops.db.execute('UPDATE sessions_observed SET body=?,digest=?,last_seen_at=? WHERE host=? AND session_id=?',
        (json.dumps(data), manual.evidence.digest(data), at, provider.host.name, SID))
    return data


@pytest.fixture
async def linked(ops, tmp_path):  # noqa: F811
    target = await prepared(ops)
    hc = HostConfig('mock-host', 'wss://bat.example.invalid', 'a' * 64, 'env:FIXTURE')
    provider = ReadOnlyProvider(hc)
    cfg = SimpleNamespace(host=lambda _: hc, hosts={hc.name: hc})
    ops.context['fleet'] = SimpleNamespace(config=cfg, client=lambda _: provider)
    api_auth.issue(ops.db, OWNER.actor, sorted(OWNER.scopes))
    manual.install(ops)
    ref = session(ops, SID)
    inventory(ops, provider)
    await link(ops, target, 'session', ref)
    human = tmp_path / 'manual-checkout'
    human.mkdir()
    (human / 'original.txt').write_bytes(b'untouched human work\n')
    yield ops, target, provider
    assert (human / 'original.txt').read_bytes() == b'untouched human work\n'
    assert sorted(path.name for path in human.iterdir()) == ['original.txt']
    assert provider.tab['id'] == SID
    assert ops.db.execute('SELECT COUNT(*) FROM tasks').fetchone()[0] == 0


def request(ops, target):
    selected = manual.sources(ops, OWNER, **target)[0]
    assert selected['available']
    return {'action': manual.ACTION, 'target': target, 'params': {'link_id': selected['link_id']},
        'preconditions': {key: selected[key] for key in ('expected_link_operation_id',
            'expected_project_binding_id', 'expected_session_identity_digest')}, 'idempotency_key': uuid.uuid4().hex}


async def advance(ops, op):
    await ops._execute(op['operation_id'])
    result = ops.get(op['operation_id'])
    assert result['error_code'] != 'INTERNAL', result
    return result


async def test_serial_pages_survive_journal_reopen_with_original_key_and_private_proof(linked, monkeypatch):
    ops, target, provider = linked
    provider.page_size = 2
    envelope = request(ops, target)
    op, fresh = ops.create(OWNER, **envelope)
    assert fresh
    op = await advance(ops, op)
    assert op['status'] == 'waiting_external'
    origin_before = manual.saved._saved(ops, op['operation_id'], manual.ORIGIN)
    steps_before = [tuple(row) for row in ops.db.execute('SELECT name,response FROM operation_steps WHERE operation_id=?', (op['operation_id'],))]
    journal = Journal(ops.journal.path)
    recovered = OperationService(journal, actions=list(ops.actions.values()))
    recovered.context.update(ops.context)
    try:
        same, fresh = recovered.create(OWNER, **envelope)
        assert not fresh and same['operation_id'] == op['operation_id']
        for _ in range(len(provider.events)):
            same = await advance(recovered, same)
            if same['status'] == 'succeeded':
                break
        assert same['status'] == 'succeeded', same
        receipt = manual.proof(recovered, OWNER, same)
        assert receipt['schema'] == manual.SERIAL_RECEIPT and receipt['completed_turn_count'] == 2
        assert receipt['runtime_status'] == 'current_serial_inputs_quiescent'
        assert receipt['process_death_proven'] is False and receipt['work_acceptance'] == 'not_evaluated'
        assert 'turns' not in receipt and 'native_turn_id' not in receipt and 'PRIVATE' not in json.dumps(receipt)
        assert 'PRIVATE' not in json.dumps(recovered.get(same['operation_id'], principal=OWNER))
        private = manual.saved._saved(recovered, same['operation_id'], manual.COMPLETE)
        assert receipt['turns_digest'] == manual.evidence.digest(private['turns'])
        assert receipt['document_digest'] == manual.evidence.digest(private)
        assert manual.saved._saved(recovered, same['operation_id'], manual.ORIGIN) == origin_before
        for name, response in steps_before:
            assert recovered.db.execute('SELECT response FROM operation_steps WHERE operation_id=? AND name=?',
                (same['operation_id'], name)).fetchone()[0] == response
        decorated = copy.deepcopy(private)
        decorated.update(privateDiagnostic={'token': 'SENTINEL'}, native_turn_id='SENTINEL')
        decorated['input_state']['privateDiagnostic'] = 'SENTINEL'
        decorated['completion_cursor']['privateDiagnostic'] = 'SENTINEL'
        assert 'SENTINEL' not in json.dumps(manual.saved.public_receipt(decorated))
        calls = list(provider.calls)
        provider.meta['inputState']['received'] = 3
        api_auth.revoke(recovered.db, OWNER.actor)
        replayed = await manual._observe(OpContext(recovered, same))
        assert replayed == receipt and provider.calls == calls
        assert manual.saved._saved(recovered, same['operation_id'], manual.COMPLETE) == private
    finally:
        journal.close()


@pytest.mark.parametrize('change', ['pending', 'received', 'epoch', 'sdk', 'scope', 'credential', 'source'])
async def test_final_read_changes_cannot_record_a_serial_receipt(linked, change):
    ops, target, provider = linked
    op, _ = ops.create(OWNER, **request(ops, target))
    def changed():
        if change in {'pending', 'received', 'epoch'}:
            provider.meta['inputState'][change] = {'pending': 1, 'received': 3, 'epoch': 'b' * 32}[change]
        elif change == 'sdk':
            provider.meta['sdkSessionId'] = 'replaced-sdk'
        elif change == 'scope':
            provider.tab['workspaceId'] = 'another-workspace'
        elif change == 'credential':
            api_auth.revoke(ops.db, OWNER.actor)
        else:
            ops.db.execute('UPDATE work_item_links SET removed_at=1 WHERE work_item_id=?', (target['work_item_id'],))
    provider.after_empty = changed
    result = await advance(ops, op)
    assert result['status'] != 'succeeded'
    assert manual.saved._saved(ops, op['operation_id'], manual.COMPLETE) is None


async def test_current_workload_needs_same_original_counter_and_withdrawal_hides_whole_receipt(linked):
    ops, target, provider = linked
    op, _ = ops.create(OWNER, **request(ops, target))
    op = await advance(ops, op)
    assert op['status'] == 'succeeded', op
    proof = manual.proof(ops, OWNER, op)
    original = manual.saved._saved(ops, op['operation_id'], manual.ORIGIN)
    at = proof['recorded_at'] + .25
    inventory(ops, provider, at=at)
    digest = manual.native._host_binding(ops, 'mock-host')
    def iso(value):
        return dt.datetime.fromtimestamp(value, dt.timezone.utc).isoformat()
    scan = {'scan_id': f'scan_{int(at * 1000000)}', 'started_at': iso(int(at)), 'finished_at': iso(at),
        'status': 'succeeded', 'complete_enumeration': True, 'authority': {'verified': True},
        'binding_version': digest, 'coverage': {'workspace_ids': ['manual-workspace']}}
    ops.db.execute('INSERT INTO hosts_observed(host,reachable,last_attempt_at,last_success_at) VALUES(?,?,?,?)',
        ('mock-host', True, at, at))
    ops.db.execute('INSERT INTO discovery_latest VALUES(?,?,?,?)',
        ('mock-host', provider.host.profile_id, digest, json.dumps(scan)))
    policy = {'scope': {'bat_workspaces': [{'host': 'mock-host', 'workspace_id': 'manual-workspace',
        'profile_id': provider.host.profile_id, 'binding_digest': digest}], 'project_binding': {'project_id': 'unused'}},
        'observation': {'max_age_seconds': 300}}
    collector = workload.Collector(ops, OWNER, target['workspace_id'], policy, at + .1)
    def current():
        return ops.db.execute('SELECT * FROM sessions_observed WHERE host=? AND session_id=?', ('mock-host', SID)).fetchone()
    assert collector.current_repair_session(current(), proof, original, manual=True)
    provider.meta['inputState']['received'] += 1
    inventory(ops, provider, at=at)
    assert not collector.current_repair_session(current(), proof, original, manual=True)
    assert manual.proof(ops, OWNER, op) == proof
    ops.db.execute('UPDATE work_item_links SET removed_at=1 WHERE link_id=?', (proof['link_id'],))
    assert not manual.operation_visible(ops.db, OWNER, op)
    with pytest.raises(OperationError, match='NOT_FOUND'):
        manual.proof(ops, OWNER, op)
    events = [dict(row) for row in ops.db.execute('SELECT * FROM api_events WHERE resource_id=?', (op['operation_id'],))]
    assert events
    assert all(not pm_access.event_visible(ops.db, OWNER, {**row, 'body': json.loads(row['body'])}) for row in events)


async def test_single_input_original_receipt_bytes_and_digest_are_unchanged(linked):
    ops, target, provider = linked
    provider.origin, provider.events, provider.meta = wire(1)
    inventory(ops, provider)
    envelope = request(ops, target)
    op, _ = ops.create(OWNER, **envelope)
    assert 'replay_profile' not in op['external_refs']['admission_binding']['pm_session_quiescence']
    result = await advance(ops, op)
    assert result['status'] == 'succeeded', result
    proof = manual.proof(ops, OWNER, result)
    assert proof['schema'] == 'pm_manual_single_input_quiescence_v1'
    assert 'completed_turn_count' not in proof and proof['native_turn_id'] == 'native-turn-1'
    private = manual.saved._saved(ops, op['operation_id'], manual.COMPLETE)
    before = ops.db.execute('SELECT response FROM operation_steps WHERE operation_id=? AND name=?',
        (op['operation_id'], manual.COMPLETE)).fetchone()[0]
    expected = {key: private[key] for key in ('schema', 'workspace_id', 'observation_operation_id', 'host', 'session_id',
        'origin_digest', 'sdk_session_id', 'native_turn_id', 'event_digest', 'event_count', 'runtime_status',
        'process_death_proven', 'recorded_at', 'work_item_id', 'link_id', 'ownership', 'work_acceptance',
        'input_state', 'completion_cursor')}
    expected['document_digest'] = manual.evidence.digest(private)
    assert json.dumps(proof) == json.dumps(expected)
    provider.origin, provider.events, provider.meta = wire(2)
    inventory(ops, provider)
    same, fresh = ops.create(OWNER, **envelope)
    assert not fresh and same['operation_id'] == op['operation_id']
    assert await manual._observe(OpContext(ops, same)) == proof
    assert ops.db.execute('SELECT response FROM operation_steps WHERE operation_id=? AND name=?',
        (op['operation_id'], manual.COMPLETE)).fetchone()[0] == before


async def test_preupgrade_pending_key_never_acquires_the_serial_profile(linked):
    ops, target, provider = linked
    definition = ops.actions[manual.ACTION]
    def old_admit(*args):
        result = manual._admit(*args)
        result['pm_session_quiescence'].pop('replay_profile', None)
        return result
    ops.actions[manual.ACTION] = replace(definition, admit=old_admit)
    envelope = request(ops, target)
    op, _ = ops.create(OWNER, **envelope)
    ops.actions[manual.ACTION] = definition
    same, fresh = ops.create(OWNER, **envelope)
    assert not fresh and same['operation_id'] == op['operation_id']
    result = await advance(ops, same)
    assert result['status'] == 'needs_attention'
    assert manual.saved._saved(ops, op['operation_id'], manual.ORIGIN) is None


@pytest.mark.parametrize('bound', ['MAX_BYTES', 'MAX_EVENTS', 'MAX_PAGES'])
async def test_serial_observation_cannot_publish_partial_bounded_history(linked, monkeypatch, bound):
    ops, target, _ = linked
    op, _ = ops.create(OWNER, **request(ops, target))
    monkeypatch.setattr(manual.native, bound, 0)
    result = await advance(ops, op)
    assert result['status'] == 'needs_attention' and result['error_code'] == 'PM_SESSION_QUIESCENCE_LIMIT'
    assert manual.proof(ops, OWNER, result) is None


async def test_retention_gap_remains_sticky_after_original_snapshot_returns(linked, monkeypatch):
    ops, target, provider = linked
    op, _ = ops.create(OWNER, **request(ops, target))
    invoke = provider.invoke
    async def expired(channel, params):
        if channel == 'claude:sync-session' and params.get('cursor'):
            return await invoke(channel, {'sessionId': SID})
        return await invoke(channel, params)
    monkeypatch.setattr(provider, 'invoke', expired)
    result = await advance(ops, op)
    assert result['error_code'] == 'PM_SESSION_QUIESCENCE_GAP'
    monkeypatch.setattr(provider, 'invoke', invoke)
    with pytest.raises(manual.Wait):
        await manual._run(OpContext(ops, ops.get(op['operation_id'])))
    assert manual.saved._saved(ops, op['operation_id'], manual.COMPLETE) is None
