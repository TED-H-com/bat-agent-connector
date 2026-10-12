"""One-ledger offline PM story. Helpers accept the existing world, never replace it.

Git and the configured verifier execute on disk. BAT and GitHub are synthetic
providers. None of these helpers install alternate product gates or write Done
claims, runtime origins, verifier receipts, or inventory rows into the journal.
"""
from __future__ import annotations

import copy
import json
import uuid
from dataclasses import dataclass, field

from bat_agent_connector import (
    api_auth,
    pm_plans,
    pm_runs,
    pm_workspace,
    repository_sync,
    work_items,
)
from bat_agent_connector import pm_bat_operations as bat
from bat_agent_connector import pm_bat_runtime as bat_runtime
from bat_agent_connector.api_auth import Principal
from tests import test_pm_conversation_proposals as conversation
from tests import test_pm_member_lifecycle_acceptance as member
from tests import test_pm_quality_reviews as quality
from tests import test_pm_work_batches as batch
from tests.operation_helpers import settle_operations
from tests.test_checkpoints import MANUAL, bat_writes, git, snapshot
from tests.test_pm_package_handoff import dispatch_runner
from tests.test_pm_plan_operations import act as thread_action
from tests.test_pm_spec_sources import FILES
from tests.test_pm_spec_sources import read as read_specs
from tests.test_repository_sync import REQUEST

OWNER = Principal('pm-owner', frozenset({'observe', 'manage', 'start', 'operate', 'integrate', 'merge', 'deploy'}))
ORIGINAL_BINDING, ORIGINAL_CARRIER = bat._binding, bat.start_carrier


@dataclass
class PhaseLedger:
    workspace_id: str
    phases: list = field(default_factory=list)
    references: dict = field(default_factory=dict)

    def record(self, phase, **references):
        assert phase not in self.phases, 'A joined phase is executed once in its original ledger'
        assert all(key not in self.references for key in references), 'Never overwrite original phase evidence'
        self.phases.append(phase)
        self.references.update(copy.deepcopy(references))

    def assert_original(self, w):
        assert w['workspace_id'] == self.workspace_id
        assert w['d'].ops.db.execute('SELECT COUNT(*) FROM pm_workspaces').fetchone()[0] == 1
        assert snapshot(w['human']) == w['human_before']


async def execute(w, action, target=None, params=None, pre=None, *, who=OWNER, key=None):
    ops = w['d'].ops
    op = ops.create(who, action=action, target=target or {}, params=params or {},
        preconditions=pre or {}, idempotency_key=key or uuid.uuid4().hex)[0]
    await settle_operations(ops)
    done = ops.get(op['operation_id'])
    assert done['status'] == 'succeeded', done
    return done


def provider_mux(w, mock, monkeypatch, bat_world):
    """Compose PM and coding wire fixtures; preserve real registered gates."""
    settings, proofs, events = bat_world[1:]
    pm_dispatch = mock.dispatch
    ordinary_dispatch = type(mock).dispatch.__get__(mock, type(mock))
    # Define this story's original provider inventory before its first discovery.
    # This human session remains in the same scoped workspace for every phase.
    manual = copy.deepcopy(next(row for row in mock.ws_doc['terminals'] if row['id'] == MANUAL))
    manual.update(cwd=str(w['human']), agentPreset='codex-agent', sdkSessionId='native-' + MANUAL)
    mock.ws_doc['terminals'] = [manual]
    mock.metas = {MANUAL: {'cwd': str(w['human']), 'isStreaming': True, 'sdkSessionId': 'native-' + MANUAL,
        'codexSandboxMode': 'workspace-write', 'codexApprovalPolicy': 'never'}}
    mock.states = {MANUAL: {'sessionId': MANUAL, 'isStreaming': True, 'pendingAskUser': None,
        'pendingPermission': None, 'messages': [{'id': 'human-original-input', 'role': 'user',
            'content': 'Read the original member behavior without changing this checkout.'}]}}
    mock.handlers['claude:list-sessions'] = lambda _: []
    coding = {MANUAL: {'epoch': 'coding-' + MANUAL, 'input_epoch': uuid.uuid4().hex,
        'reason': None, 'pending': 0}}

    def sync(sid, params):
        state, meta = coding[sid], mock.metas[sid]
        messages = mock.states[sid]['messages']
        inputs = [row for row in messages if row.get('role') == 'user']
        meta.update(sdkSessionId='native-' + sid, numTurns=len(inputs),
            inputState={'schema': 'bat_input_state_v1', 'epoch': state['input_epoch'],
                'received': len(inputs), 'pending': state['pending']})
        records = state.setdefault('events', [])
        def event(channel, **body):
            records.append({'seq': len(records) + 1, 'channel': channel, 'params': {'sessionId': sid, **body}})
        for index, source in enumerate(inputs[state.get('recorded_inputs', 0):], state.get('recorded_inputs', 0)):
            event('claude:message', message={'id': f'input-{index}', 'role': 'user', 'content': source['content'].strip()})
            event('claude:status', meta=copy.deepcopy(meta))
        state['recorded_inputs'] = len(inputs)
        if state['reason'] is not None:
            for index in range(state.get('completed_inputs', 0), len(inputs)):
                event('claude:message', message={'id': f'output-{index}', 'role': 'assistant', 'content': 'Fixed candidate recorded.'})
                event('claude:turn-end', payload={'reason': state['reason'], 'turnId': f'turn-{index}',
                    'sdkSessionId': meta['sdkSessionId'], 'result': 'Fixed candidate recorded.'})
            state['completed_inputs'] = len(inputs)
        cursor = params.get('cursor')
        if cursor is None:
            return {'mode': 'snapshot', 'sessionId': sid, 'cursor': {'epoch': state['epoch'], 'seq': len(records)},
                'state': {'sessionId': sid, 'meta': copy.deepcopy(meta), 'messages': copy.deepcopy(messages),
                    'isStreaming': meta['isStreaming']}}
        selected = [record for record in records if record['seq'] > cursor['seq']]
        return {'mode': 'delta', 'sessionId': sid, 'events': selected, 'hasMore': False,
            'cursor': {'epoch': state['epoch'], 'seq': selected[-1]['seq'] if selected else cursor['seq']}}

    def dispatch(channel, params):
        sid = params.get('sessionId')
        if sid in proofs or channel == 'claude:start-session' and params.get('options', {}).get('pmRuntimeProfile'):
            return pm_dispatch(channel, params)
        if channel == 'claude:sync-session' and sid in coding:
            return sync(sid, params)
        if channel == 'claude:get-session-meta' and sid in coding:
            sync(sid, {'sessionId': sid})
        response = ordinary_dispatch(channel, params)
        if channel == 'claude:start-session':
            coding[sid] = {'epoch': 'coding-' + sid, 'input_epoch': uuid.uuid4().hex, 'reason': None, 'pending': 0}
            sync(sid, {'sessionId': sid})
        return response

    monkeypatch.setattr(bat, '_binding', ORIGINAL_BINDING)
    monkeypatch.setattr(bat, 'start_carrier', ORIGINAL_CARRIER)
    monkeypatch.setattr(mock, 'dispatch', dispatch)
    mock.handlers.pop('git:log', None)
    w.update(pm_settings=settings, pm_proofs=proofs, pm_events=events, coding=coding,
        original_manual_ids=[MANUAL], original_manual_tab=copy.deepcopy(manual))


async def prepare_world(w):
    # Initial fixture repository creation precedes the human-preservation fence.
    files = {**FILES, 'login.py': member.repair.BROKEN, 'recovery.py': member.RECOVERY_BROKEN,
        'member_regression.py': member.MEMBER_REGRESSION}
    for path, contents in files.items():
        dest = w['human'] / path
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(contents)
    git(w['human'], 'add', *files)
    git(w['human'], 'commit', '-qm', 'Original membership implementation and fixed regression scenarios')
    git(w['human'], 'push', str(w['remote']), 'main')
    w['state']['sha'] = w['sha'] = git(w['human'], 'rev-parse', 'HEAD')
    w['human_before'] = snapshot(w['human'])
    dispatch_runner(w)
    ops = w['d'].ops
    api_auth.issue(ops.db, OWNER.actor, sorted(OWNER.scopes))
    created = await execute(w, 'pm.workspace.create', params={'name': 'One membership lifecycle', 'repositories': ['o/r']})
    w['workspace_id'] = created['result']['workspace']['workspace_id']
    w['ledger'] = PhaseLedger(w['workspace_id'])
    opened = await execute(w, 'pm.thread.create', {'workspace_id': w['workspace_id']},
        {'title': 'Member access and recovery'}, {'expected_version': 1})
    w['thread_id'] = opened['result']['thread_id']
    message = await thread_action(w, 'pm.message.append', {'content':
        '會員可以登入、忘記密碼後復原存取；推薦碼不在這一版。請整理成計畫。'})
    preview = await repository_sync.source_preview(ops, REQUEST)
    w['selection'] = {**preview['target'], **{key: preview[key] for key in
        ('source_ref', 'source_sha', 'repository_id', 'binding_digest')}}
    await thread_action(w, 'pm.run.prepare', {'purpose': 'Preserve the original membership discussion',
        'repository_binding': w['selection']})
    w['plan'] = pm_runs.workspace_worktree(ops.db, w['workspace_id'])
    w['pm_settings']['raw_reply'] = json.dumps(conversation.envelope('message.' + message['message_id']), ensure_ascii=False)
    proposal = await conversation.proposed(w, adapter=bat_runtime.ADAPTER)
    adopted = await conversation.complete(w, ops.create(OWNER, **conversation.adoption_request(w, proposal))[0])
    assert adopted['status'] == 'succeeded', adopted
    plan = pm_plans.accepted_plan(ops, OWNER, w['workspace_id'])['plan']
    w['revision'] = {'revision_id': plan['revision_id']}
    w['requirements'] = plan['document']['requirements']
    w['ledger'].record('idea_to_adopted_planning', proposal_id=proposal['proposal_id'],
        plan_revision_id=plan['revision_id'], adoption_operation_id=adopted['operation_id'])
    project = await execute(w, 'project.create', params={'name': 'Membership lifecycle', 'repositories': ['o/r']})
    w['project_id'] = project['result']['project_id']
    bound = await execute(w, 'pm.project.bind', {'workspace_id': w['workspace_id'], 'project_id': w['project_id']},
        {'rationale': 'Manage only this explicit membership project'},
        {'expected_workspace_version': pm_workspace.get_workspace(ops.db, OWNER, w['workspace_id'])['version'],
            'expected_project_version': work_items._get_project(ops.db, w['project_id'])['version']})
    w['project_binding_id'] = bound['result']['binding_id']
    current = pm_workspace.get_workspace(ops.db, OWNER, w['workspace_id'])
    await execute(w, 'pm.workspace.member.set', {'workspace_id': w['workspace_id']},
        {'actor': quality.REVIEWER.actor, 'role': 'contributor'}, {'expected_version': current['version']})
    api_auth.issue(ops.db, quality.REVIEWER.actor, sorted(quality.REVIEWER.scopes))
    dod = await execute(w, 'pm.dod.revise', {'workspace_id': w['workspace_id']},
        {'plan_revision_id': plan['revision_id'], 'criteria': [{'id': 'regression',
            'text': 'The fixed member acceptance and negative regression cases pass'}],
            'rationale': 'Original membership quality commitment'}, {'expected_revision_id': None})
    w['dod'] = dod['result']['revision_id']
    w['ledger'].assert_original(w)
    return w


async def engineering(w, requirement_id):
    from tests import test_pm_work_packages as packages
    req = next(row for row in w['requirements'] if row['id'] == requirement_id)
    item = await execute(w, 'work_item.create', {'project_id': w['project_id']},
        {'title': req['text'], 'goal': req['text'], 'acceptance': '; '.join(row['text'] for row in req['acceptance'])})
    if requirement_id == 'member.login':
        await w['d'].inventory.refresh_host('h1')
        await execute(w, 'work_item.link', {'work_item_id': item['result']['work_item_id']},
            {'kind': 'session', 'ref': 'h1/' + MANUAL, 'note': 'The original human inspects this adopted login work; no ownership transfer'})
    selected = {**w, 'target': {'workspace_id': w['workspace_id'], 'work_item_id': item['result']['work_item_id']}}
    await execute(w, 'pm.work.bind', selected['target'], {'rationale': 'Implement this adopted fixed requirement',
        'plan_revision_id': w['revision']['revision_id'], 'requirement_ids': [requirement_id]},
        {'expected_binding_id': None, 'expected_work_version': 1})
    specs = await read_specs(w, principal=OWNER, paths=['openspec/specs/identity/login/spec.md'])
    refs = [{'repository': 'o/r', **{key: row[key] for key in ('commit_sha', 'path', 'source_id', 'sha256')}}
        for row in specs['documents']]
    sid = refs[0]['source_id']
    selected['params'] = {'title': req['text'], 'requirements': [requirement_id], 'source_refs': refs,
        'code_source': w['selection'], 'rationale': 'Fixed accepted membership requirement',
        'design': {'status': 'described', 'text': 'Validate original credentials and one-use reset tokens.', 'source_ids': [sid]},
        'tasks': [{'key': requirement_id, 'description': req['text'], 'requirement_ids': [requirement_id],
            'depends_on': [], 'source_ids': [sid]}],
        'verification': [{'key': 'verify.' + requirement_id, 'requirement_id': requirement_id,
            'acceptance_input_ids': [row['source_input_id'] for row in req['acceptance']],
            'method': 'Run the fixed original member regression with its negative cases',
            'expected_evidence': 'Actual fixed candidate and tree from the configured verifier', 'source_ids': [sid]}],
        'delivery': {'repository': 'o/r', 'repository_id': w['selection']['repository_id'],
            'base_ref': 'refs/heads/main', 'goal': 'pull_request',
            **{key: {'status': 'not_applicable', 'text': 'Deployment is selected separately after verified publication.'}
                for key in ('migration', 'rollout', 'rollback', 'observation')}}}
    selected['package'] = packages.read(selected, await packages.create(selected))
    return selected


async def develop_original_requirements(w, mock):
    """Both original requirements remain in this one selected batch and ledger."""
    from pathlib import Path

    from bat_agent_connector import pm_requirement_acceptance as acceptance
    from tests import test_pm_execution_verification as producer
    from tests.test_integration import commit

    login, recovery = await engineering(w, 'member.login'), await engineering(w, 'member.recovery')
    conditions = [acceptance.freeze_condition(item['package'], item['params']['requirements'])
        for item in (login, recovery)]
    entries = [await batch.selected(login, 'login'), await batch.selected(recovery, 'recovery',
        [{'entry_key': 'login', 'condition': conditions[0]}])]
    for entry, condition in zip(entries, conditions):
        entry['result_condition'] = condition
        entry['handoff']['params']['agent'] = 'codex'
    ops = w['d'].ops
    request = batch.envelope(w, entries, max_parallel=1)
    parent = await batch.advance(w, ops.create(OWNER, **request)[0])
    selected = batch.view(w, parent)['entries']
    assert selected[0]['operation_id'] and selected[1]['operation_id'] is None
    child = ops.get(selected[0]['operation_id'])
    await member.configure_verifier(login, child, 'login')
    failed = await producer.execute(login)
    assert failed['result']['status'] == 'blocked'
    wt = Path(child['result']['worktree_path'])
    git(wt, 'config', 'user.name', 'fixture')
    git(wt, 'config', 'user.email', 'fixture@example.invalid')
    commit(wt, 'login.py', member.RESETTABLE_LOGIN, 'Implement the original login acceptance')
    complete_native_turn(w, mock, child['result']['session_id'])
    condition = await member.configure_verifier(login, child, 'login')
    verified = await producer.execute(login)
    assert verified['result']['status'] == 'passed'
    parent = await batch.advance(w, parent)
    assert batch.view(w, parent)['entries'][1]['operation_id'] is None  # Passing tests alone do not accept requirements.
    reference = await member.record_quality(login, verified, condition)
    await quality.execute(w, batch.proof_request(w, parent, reference, key='login', condition=conditions[0]))
    parent = await batch.advance(w, parent)
    selected = batch.view(w, parent)['entries']
    assert selected[0]['state'] == 'requirements_accepted' and selected[1]['operation_id']
    second = ops.get(selected[1]['operation_id'])
    assert second['params']['pm_requirement_quality_refs']['member.login']['reference'] == reference
    assert second['params']['source_sha'] == child['params']['source_sha'] == w['sha']
    await member.configure_verifier(recovery, second, 'recovery')
    failed_recovery = await producer.execute(recovery)
    assert failed_recovery['result']['status'] == 'blocked'
    wt = Path(second['result']['worktree_path'])
    git(wt, 'config', 'user.name', 'fixture')
    git(wt, 'config', 'user.email', 'fixture@example.invalid')
    commit(wt, 'login.py', member.RESETTABLE_LOGIN, 'Implement original login behavior at the fixed recovery baseline')
    commit(wt, 'recovery.py', member.RECOVERY_FIXED, 'Consume the original reset token once')
    complete_native_turn(w, mock, second['result']['session_id'])
    condition = await member.configure_verifier(recovery, second, 'recovery')
    verified_recovery = await producer.execute(recovery)
    assert verified_recovery['result']['status'] == 'passed'
    recovery_reference = await member.record_quality(recovery, verified_recovery, condition)
    await quality.execute(w, batch.proof_request(w, parent, recovery_reference, key='recovery',
        version=2, condition=conditions[1]))
    w['ledger'].record('development_and_quality', batch_operation_id=parent['operation_id'],
        execution_ids=[child['operation_id'], second['operation_id']],
        package_ids=[login['package']['package_id'], recovery['package']['package_id']],
        failed_verification_ids=[failed['operation_id'], failed_recovery['operation_id']],
        passing_verification_ids=[verified['operation_id'], verified_recovery['operation_id']],
        quality_refs=[reference, recovery_reference])
    w['implementations'] = [login, recovery]
    assert_manual_preserved(w, mock)
    return w


def complete_native_turn(w, mock, sid):
    """Synthetic provider observes independent completion, never a Connector stop."""
    w['coding'][sid]['reason'] = 'completed'
    mock.metas[sid]['isStreaming'] = mock.states[sid]['isStreaming'] = False


def assert_manual_preserved(w, mock):
    w['ledger'].assert_original(w)
    assert not [call for call in bat_writes(mock) if call['params'].get('sessionId') in w['original_manual_ids']]
    assert next(row for row in mock.ws_doc['terminals'] if row['id'] == MANUAL) == w['original_manual_tab']
    assert w['d'].ops.db.execute('SELECT COUNT(*) FROM tasks').fetchone()[0] == 0
