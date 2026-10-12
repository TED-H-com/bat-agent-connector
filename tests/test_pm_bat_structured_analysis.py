"""Structured sinks through real owned Git and the original BAT wire executor."""
from __future__ import annotations

import copy
import json
import sqlite3
import uuid

import pytest

from bat_agent_connector import (
    api_auth,
    pm_access,
    pm_follow_up,
    pm_lifecycle_gates,
    pm_native_journal,
    pm_plans,
    pm_reads,
    pm_runs,
    registry,
)
from bat_agent_connector import pm_bat_operations as bat
from bat_agent_connector import pm_bat_runtime as runtime
from bat_agent_connector import pm_conversation_proposals as proposals
from bat_agent_connector import pm_follow_up_analysis as follow_up
from bat_agent_connector.errors import InvokeTimeout
from bat_agent_connector.operations import OpContext, OperationError, Uncertain
from tests import test_pm_conversation_proposals as conversation
from tests import test_pm_plan_operations as plans
from tests.operation_helpers import settle_operations
from tests.test_checkpoints import git, snapshot
from tests.test_pm_bat_operations import bat_world  # noqa: F401
from tests.test_pm_follow_up import prepare as prepare_intake
from tests.test_pm_follow_up_analysis import envelope as follow_up_envelope
from tests.test_pm_snapshots import snapshot as capture_snapshot

world, pm, ready = plans.world, plans.pm, plans.ready
OWNER = plans.OWNER
ORIGINAL_BINDING, ORIGINAL_CARRIER = bat._binding, bat.start_carrier
ACTIONS = (proposals.ACTION, follow_up.ACTION)


@pytest.fixture
def daemon(ready):
    return ready['d']


@pytest.fixture
async def structured(ready, bat_world, mock, monkeypatch):  # noqa: F811
    w, ops = ready, ready['d'].ops
    api_auth.issue(ops.db, OWNER.actor, list(OWNER.scopes))
    monkeypatch.setattr(bat, '_binding', ORIGINAL_BINDING)
    monkeypatch.setattr(bat, 'start_carrier', ORIGINAL_CARRIER)
    mock.handlers.pop('git:log', None)
    adopted = await plans.adopt(w)
    assert adopted['status'] == 'succeeded', json.dumps(adopted, indent=2)
    message = await plans.act(w, 'pm.message.append', {'content': '整理登入與忘記密碼計畫；推薦碼排除。'})
    captured = await capture_snapshot(ops, w['workspace_id'], who=OWNER)
    data = {'wid': w['workspace_id'], 'target': plans.target(w), 'snapshot': captured}
    intake = await prepare_intake(ops, data, who=OWNER, thread_id=plans.target(w)['thread_id'])
    w.update(settings=bat_world[1], proofs=bat_world[2], events=bat_world[3], message=message,
        intake=pm_follow_up.get_intake(ops, OWNER, w['workspace_id'], intake['intake_id'])['intake'])
    return w


def request(w, action, **params):
    if action == proposals.ACTION:
        value = conversation.propose_request(w)
        raw = conversation.envelope('message.' + w['message']['message_id'])
    else:
        value = {'action': action, 'target': plans.target(w),
            'params': {'intake_id': w['intake']['intake_id'], 'purpose': 'Compare fixed evidence'},
            'preconditions': {'expected_version': plans.version(w), 'expected_intake_digest': w['intake']['document_digest']},
            'idempotency_key': uuid.uuid4().hex}
        raw = follow_up_envelope()
    w['settings']['raw_reply'] = json.dumps(raw, ensure_ascii=False)
    value['params'].update(adapter=runtime.ADAPTER, **params)
    return value


async def execute(w, value):
    ops = w['d'].ops
    op = ops.create(OWNER, **value)[0]
    await settle_operations(ops)
    return ops.get(op['operation_id'])


def result(w, op):
    outcome = op['result']['outcome']
    if op['action'] == proposals.ACTION:
        return proposals.get_proposal(w['d'].ops, OWNER, w['workspace_id'], outcome['proposal_id'])['proposal']
    return follow_up.get_assessment(w['d'].ops, OWNER, w['workspace_id'], outcome['assessment_id'])['assessment']


async def recover(w, op):
    ops = w['d'].ops
    ops.db.execute('UPDATE operations SET next_run_at=0 WHERE operation_id=?', (op['operation_id'],))
    await settle_operations(ops)
    return ops.get(op['operation_id'])


@pytest.mark.parametrize('action', ACTIONS)
async def test_bat_structured_complete_original_proof_and_one_review(structured, mock, action):
    w, ops = structured, structured['d'].ops
    human, head, messages = snapshot(w['human']), pm_plans.head(ops.db, w['workspace_id']), plans.version(w)
    out = await execute(w, request(w, action))
    assert out['status'] == 'succeeded', out
    saved = result(w, out)
    run = pm_runs.get_by_operation(ops.db, out['operation_id'])
    assert run['adapter'] == runtime.ADAPTER and run['state'] == 'completed'
    assert saved['validation']['status'] == 'format_valid'
    assert saved['raw_reply'] == w['settings']['raw_reply']
    assert saved['provenance']['adapter'] == runtime.ADAPTER
    assert saved['provenance']['native_message_id'] == 'answer-1'
    proof = saved['provenance']['bat_runtime']
    assert proof['native_message_id'] == 'native-answer'
    assert proof['process_completion'] == runtime.death_proof(ops.db, run)
    assert 'runtimeCwd' not in json.dumps(saved['provenance'])
    assert not ops.db.execute('SELECT 1 FROM pm_native_events WHERE run_id=?', (run['run_id'],)).fetchone()
    assert plans.version(w) == messages and pm_plans.head(ops.db, w['workspace_id']) == head
    assert registry.get('h1', run['session_binding']['session_id'])['status'] == 'stopped'
    assert mock.channels().count('claude:start-session') == mock.channels().count('claude:send-message') == 1
    assert 'worktree:create' not in mock.channels()
    if action == proposals.ACTION:
        before = git(w['plan']['worktree_path'], 'rev-parse', 'HEAD')
        adopted = await conversation.complete(w, ops.create(OWNER, **conversation.adoption_request(w, saved))[0])
        assert adopted['status'] == 'succeeded', json.dumps(adopted, indent=2)
        assert git(w['plan']['worktree_path'], 'rev-parse', 'HEAD') != before
        accepted = pm_plans.accepted_plan(ops, OWNER, w['workspace_id'])['plan']['document']
        assert [row['id'] for row in accepted['requirements']] == ['member.login', 'member.recovery']
    assert snapshot(w['human']) == human


@pytest.mark.parametrize('action', ACTIONS)
async def test_invalid_bat_reply_remains_raw_unadopted(structured, action):
    w = structured
    value = request(w, action)
    w['settings']['raw_reply'] = 'Not structured JSON; do not adopt anything.'
    out = await execute(w, value)
    assert out['status'] == 'succeeded', out
    saved = result(w, out)
    assert saved['validation']['status'] == 'needs_review' and saved['document'] is None
    assert saved['document_digest'] is None and saved['raw_reply'] == w['settings']['raw_reply']


@pytest.mark.parametrize('action', ACTIONS)
@pytest.mark.parametrize('phase', ['claude:start-session', 'claude:send-message'])
async def test_original_bat_ack_loss_reads_once(structured, mock, monkeypatch, action, phase):
    w, ops = structured, structured['d'].ops
    client = ops.context['fleet'].client('h1')
    invoke, lost = client.invoke, False
    async def lose(channel, *args, **kwargs):
        nonlocal lost
        response = await invoke(channel, *args, **kwargs)
        if channel == phase and not lost:
            lost = True
            raise InvokeTimeout('Lost exact original response')
        return response
    monkeypatch.setattr(client, 'invoke', lose)
    out = await execute(w, request(w, action))
    assert out['status'] == 'uncertain', out
    assert (await recover(w, out))['status'] == 'succeeded'
    assert mock.channels().count('claude:start-session') == mock.channels().count('claude:send-message') == 1


@pytest.mark.parametrize('action', ACTIONS)
@pytest.mark.parametrize('failure', ['ack_only', 'gap', 'unattested', 'missing_exit'])
async def test_unproven_bat_never_enters_structured_sink(structured, mock, action, failure):
    w, ops = structured, structured['d'].ops
    w['settings'][failure] = True
    out = await execute(w, request(w, action))
    assert out['status'] != 'succeeded', out
    table = 'pm_conversation_proposals' if action == proposals.ACTION else 'pm_follow_up_assessments'
    assert not ops.db.execute(f'SELECT 1 FROM {table}').fetchone()
    if failure == 'missing_exit':
        run = pm_runs.get_by_operation(ops.db, out['operation_id'])
        assert ops.db.execute('SELECT run_id FROM pm_writer_slots WHERE workspace_id=?', (w['workspace_id'],)).fetchone()[0] == run['run_id']
        second = await execute(w, request(w, action))
        assert second['status'] != 'succeeded'
        proof = w['proofs'][run['session_binding']['session_id']]['pmRuntime']
        proof['exit'] = {'confirmedDead': True, 'exitCode': 0, 'signal': None, 'action': 'native_terminal', 'process': proof['process'].copy()}
        assert (await recover(w, out))['status'] == 'succeeded'
        assert mock.channels().count('claude:start-session') == mock.channels().count('claude:send-message') == 1


@pytest.mark.parametrize('action', ACTIONS)
async def test_committed_import_recovers_before_changed_thread_and_cancellation(structured, mock, monkeypatch, action):
    w, ops = structured, structured['d'].ops
    original, crashed = bat._retire_completed, False
    def crash(ctx, run):
        nonlocal crashed
        if not crashed:
            crashed = True
            raise Uncertain('pm.bat.retire', 'Lost only local retirement')
        return original(ctx, run)
    monkeypatch.setattr(bat, '_retire_completed', crash)
    out = await execute(w, request(w, action))
    assert out['status'] == 'uncertain', out
    run = pm_runs.get_by_operation(ops.db, out['operation_id'])
    assert run['state'] == 'completed'
    await plans.act(w, 'pm.message.append', {'content': 'Later discussion must not replace the saved result.'})
    ops.cancel(OWNER, out['operation_id'])
    done = await recover(w, out)
    assert done['status'] == 'succeeded', done
    assert pm_runs.get_by_operation(ops.db, out['operation_id']) == run
    assert mock.channels().count('claude:start-session') == mock.channels().count('claude:send-message') == 1


@pytest.mark.parametrize('action', ACTIONS)
async def test_import_transaction_rolls_back_and_recovers_same_native_receipt(structured, mock, monkeypatch, action):
    w, ops = structured, structured['d'].ops
    api_event, lost = ops.journal.api_event, False
    def lose(kind, *args, **kwargs):
        nonlocal lost
        if kind in {'pm_conversation_proposal', 'pm_follow_up_assessment'} and not lost:
            lost = True
            raise sqlite3.OperationalError('fixture failed atomic sink commit')
        return api_event(kind, *args, **kwargs)
    monkeypatch.setattr(ops.journal, 'api_event', lose)
    out = await execute(w, request(w, action))
    assert out['status'] == 'uncertain', out
    assert (await recover(w, out))['status'] == 'succeeded'
    assert mock.channels().count('claude:start-session') == mock.channels().count('claude:send-message') == 1


@pytest.mark.parametrize('adapter', [None, '', 'auto', 'bat', {}, ['bat-pm-linux-v1']])
def test_adapter_selection_rejects_unregistered_values(structured, adapter):
    w = structured
    value = request(w, proposals.ACTION)
    value['params']['adapter'] = adapter
    with pytest.raises(OperationError) as error:
        w['d'].ops.create(OWNER, **value)
    assert error.value.code == 'INVALID_PARAMS'


async def test_capabilities_are_additive_and_selection_is_original_intent(structured, mock):
    w, ops = structured, structured['d'].ops
    before = len(mock.invokes)
    caps = pm_reads.read(ops, OWNER, 'pm_capabilities_get', {'workspace_id': w['workspace_id']})
    assert len(mock.invokes) == before
    advertised = caps['structured_analysis']
    assert advertised['version'] == 1 and advertised['default_adapter'] == pm_native_journal.ADAPTER
    assert caps['conversation_proposals']['analysis'] == caps['native_analysis']
    for action in ACTIONS:
        descriptors = advertised['actions'][action]['adapters']
        assert [row['adapter'] for row in descriptors] == [pm_native_journal.ADAPTER, runtime.ADAPTER]
        assert descriptors[1]['authorized'] and descriptors[1]['eligible_for_preflight']
        assert descriptors[1]['model_execution_verified'] is False
    value = request(w, proposals.ACTION)
    out = await execute(w, value)
    assert out['status'] == 'succeeded', out
    changed = copy.deepcopy(value)
    changed['params']['adapter'] = pm_native_journal.ADAPTER
    with pytest.raises(OperationError) as error:
        ops.create(OWNER, **changed)
    assert error.value.code == 'IDEMPOTENCY_CONFLICT'


@pytest.mark.parametrize('action', ACTIONS)
@pytest.mark.parametrize('completion', ['completed', 'abort', 'preflight_abort'])
async def test_public_bat_proof_has_explicit_nested_schema(structured, mock, monkeypatch, action, completion):
    w, ops = structured, structured['d'].ops
    sentinel = 'SYNTHETIC_PRIVATE_DIAGNOSTIC_do_not_publish'
    if completion == 'abort':
        w['settings']['ack_only'] = True
    elif completion == 'preflight_abort':
        w['settings']['preflight_failure'] = 'launched'
    original = mock.dispatch
    def inject(channel, params):
        try:
            return original(channel, params)
        finally:
            value = w['proofs'].get(params.get('sessionId'), {}).get('pmRuntime')
            if channel in {'claude:start-session', 'claude:send-message'} and value:
                if isinstance(value.get('process'), dict):
                    value['process']['privateEnv'] = sentinel
                if value.get('exit'):
                    value['exit']['privatePath'] = sentinel
                    value['exit']['process']['privateEnv'] = sentinel
                    value['exit']['signal'] = {'privateEnv': sentinel}
                    value['processObservation'] = {'state': 'dead', 'proof': 'original_process_exited',
                        'observedAt': {'privateEnv': sentinel}, 'trace': sentinel}
                value['diagnostics'] = {'configuration': sentinel}
    monkeypatch.setattr(mock, 'dispatch', inject)
    out = await execute(w, request(w, action))
    run = pm_runs.get_by_operation(ops.db, out['operation_id'])
    if completion == 'completed':
        assert out['status'] == 'succeeded', out
        assert sentinel not in json.dumps(result(w, out))
        assert result(w, out)['provenance']['bat_runtime']['process_completion']['exit']['confirmedDead'] is True
    else:
        assert run['state'] == 'aborted', out
    public = pm_runs.read(ops, OWNER, w['workspace_id'], run['run_id'])
    assert sentinel not in json.dumps(public)
    private = runtime.observations(ops.db, run['run_id'])
    aborts = [row[0] for row in ops.db.execute('SELECT document FROM pm_bat_abort_proofs WHERE run_id=?', (run['run_id'],))]
    assert sentinel in json.dumps([private, aborts])


@pytest.mark.parametrize('action', ACTIONS)
@pytest.mark.parametrize('change', ['membership', 'cancel', 'pause', 'carrier'])
async def test_final_bat_input_frame_rechecks_original_authority(structured, mock, monkeypatch, action, change):
    w, ops = structured, structured['d'].ops
    client = ops.context['fleet'].client('h1')
    original = client._invoke_checked
    async def change_before_frame(channel, params, *args, **kwargs):
        if channel == 'claude:send-message':
            if change == 'membership':
                ops.db.execute("UPDATE pm_workspace_members SET role='reader' WHERE workspace_id=? AND actor=?", (w['workspace_id'], OWNER.actor))
            elif change == 'cancel':
                op = ops.db.execute('SELECT operation_id FROM pm_runs ORDER BY created_at DESC LIMIT 1').fetchone()[0]
                ops.cancel(OWNER, op)
            elif change == 'pause':
                head = pm_lifecycle_gates.head(ops.db, w['workspace_id'])
                paused = ops.create(OWNER, action='pm.lifecycle.pause', target={'workspace_id': w['workspace_id']},
                    params={'reason': 'Stop before the original frame'}, preconditions={'expected_lifecycle_revision': head['revision']},
                    idempotency_key=uuid.uuid4().hex)[0]
                await ops._execute(paused['operation_id'])
                assert ops.get(paused['operation_id'])['status'] == 'succeeded'
            else:
                git(w['plan']['worktree_path'], 'commit', '--allow-empty', '-m', 'Changed original carrier')
        return await original(channel, params, *args, **kwargs)
    monkeypatch.setattr(client, '_invoke_checked', change_before_frame)
    out = await execute(w, request(w, action))
    assert out['status'] != 'succeeded', out
    assert 'claude:send-message' not in mock.channels()
    run = pm_runs.get_by_operation(ops.db, out['operation_id'])
    assert run['state'] not in {'completed', 'aborted'}  # No fabricated process-death proof.
    assert ops.db.execute('SELECT run_id FROM pm_writer_slots WHERE workspace_id=?', (w['workspace_id'],)).fetchone()[0] == run['run_id']


@pytest.mark.parametrize('action', ACTIONS)
async def test_cancel_before_import_keeps_proven_dead_original_without_sink(structured, mock, monkeypatch, action):
    w, ops = structured, structured['d'].ops
    original = bat._observe
    async def cancel_after_observation(ctx, run, client):
        value = await original(ctx, run, client)
        ops.cancel(OWNER, ctx.operation_id)
        return value
    monkeypatch.setattr(bat, '_observe', cancel_after_observation)
    out = await execute(w, request(w, action))
    assert out['status'] == 'cancelled', out
    run = pm_runs.get_by_operation(ops.db, out['operation_id'])
    assert run['state'] == 'aborted'
    assert ops.db.execute('SELECT run_id FROM pm_writer_slots WHERE workspace_id=?', (w['workspace_id'],)).fetchone()[0] is None
    table = 'pm_conversation_proposals' if action == proposals.ACTION else 'pm_follow_up_assessments'
    assert not ops.db.execute(f'SELECT 1 FROM {table}').fetchone()
    assert mock.channels().count('claude:start-session') == mock.channels().count('claude:send-message') == 1


@pytest.mark.parametrize('action', ACTIONS)
async def test_bat_sink_cannot_be_routed_to_native_or_chat(structured, action):
    from bat_agent_connector import pm_analysis_adapters
    w, ops = structured, structured['d'].ops
    value = request(w, action)
    out = await execute(w, value)
    assert out['status'] == 'succeeded', out
    run = pm_runs.get_by_operation(ops.db, out['operation_id'])
    assert pm_analysis_adapters.fixed_context(out, runtime.ADAPTER, run['context'])
    assert not pm_analysis_adapters.fixed_context(out, pm_native_journal.ADAPTER, run['context'])
    changed = {**run['context'], 'output_destination': 'discussion'}
    assert not pm_analysis_adapters.fixed_context(out, runtime.ADAPTER, changed)
    with pytest.raises(OperationError, match='PM_OUTPUT_DESTINATION_REFUSED'):
        pm_runs.import_reply(ops.journal, run['run_id'], run['generation'], content='replacement',
            native_turn_id='original-turn', sdk_session_id=run['session_binding']['sdk_session_id'])
    with pytest.raises(OperationError, match='PM_OUTPUT_DESTINATION_REFUSED'):
        pm_native_journal.import_reply(ops.journal, run['run_id'], run['generation'])


@pytest.mark.parametrize('action', ACTIONS)
async def test_source_withdrawal_hides_bat_run_sink_operation_and_owned_session(structured, action):
    from bat_agent_connector import pm_source_lineage
    w, ops = structured, structured['d'].ops
    out = await execute(w, request(w, action))
    assert out['status'] == 'succeeded', out
    saved = pm_runs.get_by_operation(ops.db, out['operation_id'])
    session = 'h1/' + saved['session_binding']['session_id']
    assert pm_access.visible(ops.db, OWNER, out)
    assert pm_source_lineage.visible(ops.db, OWNER, 'session', session)
    ops.db.execute('DELETE FROM pm_workspace_members WHERE workspace_id=? AND actor=?', (w['workspace_id'], OWNER.actor))
    assert not pm_access.visible(ops.db, OWNER, out)
    assert not pm_source_lineage.visible(ops.db, OWNER, 'session', session)
    with pytest.raises(OperationError):
        result(w, out)
    with pytest.raises(OperationError):
        pm_runs.read(ops, OWNER, w['workspace_id'], saved['run_id'])
    for event in ops.journal.api_events(0, 500)['events']:
        if event['body'].get('operation_id') == out['operation_id']:
            assert not pm_access.event_visible(ops.db, OWNER, event)
    sink = proposals if action == proposals.ACTION else follow_up
    assert sink._import(ops.journal, saved['run_id'], saved['generation'])['outcome'] == saved['outcome']
    assert await sink._run(OpContext(ops, ops.get(out['operation_id']))) == out['result']


async def test_bat_follow_up_keeps_original_private_task_source_gate(structured):
    from bat_agent_connector import (
        controller_authority,
        pm_evidence,
        pm_source_lineage,
        pm_workspace,
        work_items,
    )
    from tests.test_pm_evidence import capture_request, work_request
    w, ops = structured, structured['d'].ops
    async def act(action, target=None, params=None, pre=None):
        done = await execute(w, {'action': action, 'target': target or {}, 'params': params or {},
            'preconditions': pre or {}, 'idempotency_key': uuid.uuid4().hex})
        assert done['status'] == 'succeeded', done
        return done['result']
    project = (await act('project.create', params={'name': 'Private original evidence', 'repositories': ['o/r']}))['project_id']
    work = (await act('work_item.create', {'project_id': project}, {'title': 'Original task evidence'}))['work_item_id']
    await act('pm.project.bind', {'workspace_id': w['workspace_id'], 'project_id': project},
        {'rationale': 'Bind the actual original task'},
        {'expected_workspace_version': pm_workspace.get_workspace(ops.db, OWNER, w['workspace_id'])['version'],
         'expected_project_version': work_items._get_project(ops.db, project)['version']})
    target = {'workspace_id': w['workspace_id'], 'work_item_id': work}
    bound = await execute(w, work_request(ops, target))
    assert bound['status'] == 'succeeded', bound
    with controller_authority.use(controller_authority.binding(ops.db, OWNER), ops.journal):
        task = ops.journal.submit(project='example', host='h1', workspace='w1',
            original_words='Original private task', idempotency_key=uuid.uuid4().hex)
    ops.db.execute("UPDATE tasks SET state='needs_ted' WHERE task_id=?", (task['task_id'],))
    await act('work_item.link', {'work_item_id': work}, {'kind': 'task', 'ref': task['task_id']})
    captured = await execute(w, capture_request(ops, target))
    assert captured['status'] == 'succeeded', captured
    assert pm_evidence.latest_capture(ops, OWNER, **target)['capture']['evidence_ids']
    data = {'wid': w['workspace_id'], 'target': plans.target(w),
        'snapshot': await capture_snapshot(ops, w['workspace_id'], who=OWNER)}
    intake = await prepare_intake(ops, data, who=OWNER, thread_id=plans.target(w)['thread_id'])
    w['intake'] = pm_follow_up.get_intake(ops, OWNER, w['workspace_id'], intake['intake_id'])['intake']
    out = await execute(w, request(w, follow_up.ACTION))
    assert out['status'] == 'succeeded', out
    saved = pm_runs.get_by_operation(ops.db, out['operation_id'])
    pending = ops.create(OWNER, **request(w, follow_up.ACTION))[0]
    session = 'h1/' + saved['session_binding']['session_id']
    assert pm_access.visible(ops.db, OWNER, out) and pm_source_lineage.visible(ops.db, OWNER, 'session', session)
    ops.db.execute("UPDATE tasks SET controller_authority='{}' WHERE task_id=?", (task['task_id'],))
    assert pm_workspace.get_workspace(ops.db, OWNER, w['workspace_id'])  # Membership remains valid.
    assert not pm_access.visible(ops.db, OWNER, out)
    assert not pm_access.visible(ops.db, OWNER, pending)
    assert not pm_source_lineage.visible(ops.db, OWNER, 'session', session)
    with pytest.raises(OperationError):
        result(w, out)
    with pytest.raises(OperationError):
        pm_runs.read(ops, OWNER, w['workspace_id'], saved['run_id'])
    await settle_operations(ops)
    assert ops.get(pending['operation_id'])['status'] != 'succeeded'
    assert ops.db.execute('SELECT COUNT(*) FROM pm_follow_up_assessments').fetchone()[0] == 1
    assert follow_up._import(ops.journal, saved['run_id'], saved['generation'])['outcome'] == saved['outcome']


@pytest.mark.parametrize('action', ACTIONS)
async def test_structured_original_receipt_survives_restart_and_new_generation(structured, mock, action):
    from tests.test_pm_bat_continuation import restart
    w = structured
    first = await execute(w, request(w, proposals.ACTION))
    assert first['status'] == 'succeeded', first
    original = pm_runs.get_by_operation(w['d'].ops.db, first['operation_id'])
    original_result, human = result(w, first), snapshot(w['human'])
    await restart(w)
    assert result(w, first) == original_result
    second = await execute(w, request(w, action))
    assert second['status'] == 'succeeded', second
    successor = pm_runs.get_by_operation(w['d'].ops.db, second['operation_id'])
    assert successor['generation'] == original['generation'] + 1
    assert successor['session_binding']['session_id'] != original['session_binding']['session_id']
    assert pm_runs.get_by_operation(w['d'].ops.db, first['operation_id']) == original
    assert result(w, first) == original_result and snapshot(w['human']) == human
    assert mock.channels().count('claude:start-session') == mock.channels().count('claude:send-message') == 2


@pytest.mark.parametrize('action', ACTIONS)
async def test_bat_structured_http_rpc_mcp_share_original_result(structured, monkeypatch, action):
    from bat_agent_connector import mcp_server
    from tests.test_api_v1 import http, token
    from tests.test_pm_governance_api import send
    from tests.test_pm_run_api import unpack
    w, d = structured, structured['d']
    out = await send(d, w['port'], request(w, action), OWNER)
    assert out['status'] == 'succeeded', out
    saved = result(w, out)
    if action == proposals.ACTION:
        method, params, path = 'pm_conversation_proposal_get', {'proposal_id': saved['proposal_id']}, '/conversation-proposals/' + saved['proposal_id']
    else:
        method, params, path = 'pm_follow_up_assessment_get', {'assessment_id': saved['assessment_id']}, '/follow-up-assessments/' + saved['assessment_id']
    params['workspace_id'] = w['workspace_id']
    auth = token(d, OWNER.actor, 'observe')
    monkeypatch.setenv('BATC_TASK_URL', f"http://127.0.0.1:{w['port']}/rpc")
    monkeypatch.setenv('BATC_API_TOKEN', auth)
    server, fleet = mcp_server.build_server(d.fleet.config, read_only=True, principal_only=True)
    try:
        status, expected = await http(w['port'], 'GET', '/api/v1/pm/workspaces/' + w['workspace_id'] + path, tok=auth)
        assert status == 200, expected
        assert await d.call_api(method, params, OWNER) == expected
        assert unpack(await server.call_tool(method, params)) == expected
        assert saved['provenance']['adapter'] == runtime.ADAPTER
    finally:
        await fleet.close()
