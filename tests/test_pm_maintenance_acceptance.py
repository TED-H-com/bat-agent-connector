"""Offline policy-to-PR acceptance with a failing/passing real Git regression.

This proves a published-for-review outcome, not autonomous merge, release, or
runtime termination. BAT/provider responses are synthetic; Git and the configured
ObservedVerifier subprocess are real and restricted to pytest's disk directory.
"""
from __future__ import annotations

import copy
import re
import sys
import uuid
from pathlib import Path
from urllib.parse import unquote

import pytest

from bat_agent_connector import (
    api_auth,
    pm_access,
    pm_execution_verification,
    pm_lifecycle_gates,
    pr_publication,
)
from bat_agent_connector import pm_maintenance_repair_evidence as evidence
from bat_agent_connector import pm_maintenance_repair_publication as publication
from bat_agent_connector import pm_maintenance_repairs as repairs
from bat_agent_connector import pm_quality_reviews as quality
from bat_agent_connector.config import parse_config
from bat_agent_connector.operations import OperationError
from bat_agent_connector.task_verifier import ObservedVerifier, VerificationSettings
from tests import test_pm_maintenance_repairs as setup
from tests.test_checkpoints import MANUAL, bat_writes, git, snapshot
from tests.test_integration import RaceRunner, commit
from tests.test_pm_lifecycle import decide
from tests.test_pm_maintenance_repair_receipts import record
from tests.test_pm_maintenance_repairs import advance, execute, fixed, pm, ready  # noqa: F401
from tests.test_pm_maintenance_repairs import repair_ready as repair_fixture
from tests.test_pm_quality_reviews import REVIEWER, assessment, selection
from tests.test_pm_quality_reviews import execute as assess
from tests.test_pm_work_packages import action, member
from tests.test_repository_sync import world as repository_world

base_world = repository_world
repair_ready = repair_fixture

OWNER = api_auth.Principal('pm-owner', repairs.REQUIRED | {'integrate', 'merge', 'deploy', 'operate'})
COMMAND = (sys.executable, '-B', 'regression.py')
BROKEN = 'def login(password):\n    return bool(password)\n'
FIXED = 'def login(password):\n    return password == "correct"\n'
REGRESSION = '''import runpy
login = runpy.run_path('login.py')['login']
assert login('correct') is True, 'valid credentials must allow access'
assert login('incorrect') is False, 'an invalid password must be denied'
assert login('') is False, 'an empty password must be denied'
print('PASS: original fixed login acceptance and regression')
'''


@pytest.fixture
async def world(base_world):
    w = base_world
    # Build the released fixture before capturing the human preservation fence.
    # Stage only fixture source files; the existing human edit stays dirty.
    (w['human'] / 'regression.py').write_text(REGRESSION)
    (w['human'] / 'login.py').write_text(BROKEN)
    git(w['human'], 'add', 'login.py', 'regression.py')
    git(w['human'], 'commit', '-qm', 'Released login with reproducible invalid-password bug')
    git(w['human'], 'push', str(w['remote']), 'main')
    w['state']['sha'] = w['sha'] = git(w['human'], 'rev-parse', 'HEAD')
    return w


@pytest.fixture(autouse=True)
def original_authority(monkeypatch):
    monkeypatch.setattr(setup, 'OWNER', OWNER)
    monkeypatch.setattr(setup, 'TEST_COMMAND_ID', pm_execution_verification.command_identity(COMMAND))


async def verify(w):
    ops = w['d'].ops
    config = await pm_execution_verification.configuration(ops, OWNER, w['workspace_id'], w['child']['operation_id'])
    return await execute(ops, {'action': pm_execution_verification.ACTION,
        'target': {'workspace_id': w['workspace_id'], 'execution_operation_id': w['child']['operation_id']},
        'params': {key: config[key] for key in ('candidate_commit', 'tree_hash', 'command_digest')},
        'preconditions': {'handoff_contract_digest': config['handoff_contract_digest']},
        'idempotency_key': uuid.uuid4().hex}, who=OWNER)


@pytest.fixture
async def original_repair(repair_ready):
    w, ops = repair_ready, repair_ready['d'].ops
    w['human_before'] = snapshot(w['human'])
    w['task_count'] = ops.db.execute('SELECT COUNT(*) FROM tasks').fetchone()[0]
    api_auth.issue(ops.db, OWNER.actor, sorted(OWNER.scopes))
    await member(w, REVIEWER.actor, 'contributor')
    w['dod'] = (await action(w, 'pm.dod.revise', {'workspace_id': w['workspace_id']},
        {'plan_revision_id': w['package']['document']['context']['plan_ref']['revision_id'],
         'criteria': [{'id': 'regression', 'text': 'Valid credentials pass and invalid passwords are denied'}],
         'rationale': 'Original released login regression'}, {'expected_revision_id': None}))['revision_id']
    # This is the real bounded scheduler admission, not a hand-created parent row.
    ids = repairs.tick(ops)
    assert len(ids) == 1 and repairs.tick(ops) == []
    w['parent'] = await advance(w, ops.get(ids[0]))
    w['child'] = repairs._child(ops, w['parent'])
    assert w['child']['status'] == 'succeeded'
    w['d'].adapter.verifier = ObservedVerifier(VerificationSettings(
        commands={w['child']['params']['project_id']: COMMAND},
        artifact_dir=str(w['human'].parent / 'acceptance-verifier-logs')))
    failed = await verify(w)
    assert failed['result']['status'] == 'blocked', failed
    w['baseline_verification'] = failed
    wt = Path(w['child']['result']['worktree_path'])
    git(wt, 'config', 'user.name', 'fixture')
    git(wt, 'config', 'user.email', 'fixture@example.invalid')
    w['candidate'] = commit(wt, 'login.py', FIXED, 'Deny invalid passwords at the original fixed source')
    w['tree'] = git(wt, 'rev-parse', 'HEAD^{tree}')
    passed = await verify(w)
    assert passed['result']['status'] == 'passed', passed
    w['proof'] = passed['result']['reference']
    w['verification'] = passed
    env = selection(w)
    for criterion in env['params']['criteria']:
        criterion['condition'] = repairs._intent(w['parent'])['rule']['verification']
    created = await assess(w, env)
    review = quality.get_review(ops, OWNER, w['workspace_id'], created['result']['review_id'])['review']
    await assess(w, assessment(w, review), actor=REVIEWER)
    review = quality.get_review(ops, OWNER, w['workspace_id'], review['review_id'])['review']
    assert review['qualified']
    w['quality_ref'] = {'review_id': review['review_id'],
        **{key: review['assessment'][key] for key in ('operation_id', 'version', 'document_digest')}}
    await execute(ops, record(w, w['parent'], {'kind': 'quality', 'reference': w['quality_ref']}), who=OWNER)
    raw = copy.deepcopy(w['raw'])
    raw['github']['repos'][0]['integrate'] = {'hosts': ['h1'], 'remote_url': str(w['remote']), 'workspace': 'ws-1'}
    ops.context['github_config'] = parse_config(raw).github
    w['runner'] = RaceRunner()
    ops.context['git_runner'] = w['runner']
    w['posts'] = []
    old_provider = w['gh'].before_request

    def provider(method, path, body):
        # Preserve real HTTP client framing and bounded original 201 handling.
        # Never recreate this PR later with a different draft/native identity.
        if method == 'POST' and path == '/repos/o/r/pulls':
            w['posts'].append(copy.deepcopy(body))
            pr = w['gh'].add_pr(21, git(w['remote'], 'rev-parse', 'refs/heads/' + body['head']),
                head_ref=body['head'], id=901, node_id='PR_acceptance_original',
                draft=body['draft'], title=body['title'], body=body['body'])
            pr['base'].update(ref=body['base'], sha=git(w['remote'], 'rev-parse', 'refs/heads/' + body['base']))
            w['gh'].script.append(('POST', re.escape(path) + '$', 201, {}, copy.deepcopy(pr)))
        elif method == 'GET' and '/git/ref/heads/' in path:
            ref = unquote(path.split('/git/ref/heads/', 1)[1])
            sha = git(w['remote'], 'rev-parse', 'refs/heads/' + ref)
            w['gh'].script.append(('GET', re.escape(path) + '$', 200, {},
                {'ref': 'refs/heads/' + ref, 'object': {'type': 'commit', 'sha': sha}}))
        elif method == 'GET' and path == '/repos/o/r/pulls' and w['posts']:
            return  # FakeGitHub returns the same original stored native PR.
        else:
            old_provider(method, path, body)
    w['gh'].before_request = provider
    return w


def read(w):
    return repairs.get_repair(w['d'].ops, OWNER, w['workspace_id'], 'pmrp_' + w['parent']['operation_id'][3:])['repair']


def preserved(w, mock):
    assert snapshot(w['human']) == w['human_before']
    assert w['d'].ops.db.execute('SELECT COUNT(*) FROM tasks').fetchone()[0] == w['task_count']
    assert not [call for call in bat_writes(mock) if call['params'].get('sessionId') == MANUAL]


async def publish(w):
    w['parent'] = await advance(w, w['parent'], rounds=5)
    return publication.child(w['d'].ops, w['parent'])


async def test_policy_real_regression_to_original_native_pr_is_review_only(original_repair, mock):
    w, ops = original_repair, original_repair['d'].ops
    child = await publish(w)
    assert child['status'] == 'succeeded', child
    assert w['parent']['status'] == 'succeeded', w['parent']
    receipt, _ = evidence.owned_creation(ops, OWNER, w['parent'])
    assert receipt['native_pr_id'] == 901 and receipt['child_operation_id'] == w['child']['operation_id']
    assert receipt['repair_parent_id'] == w['parent']['operation_id'] and receipt['target_verified'] is True
    assert receipt['quality_ref'] == w['quality_ref'] and receipt['candidate_tree'] == w['tree']
    assert w['baseline_verification']['params']['candidate_commit'] == receipt['base_sha']
    assert w['verification']['params']['candidate_commit'] == receipt['candidate_sha'] == w['candidate']
    assert git(w['remote'], 'rev-parse', 'refs/heads/' + receipt['branch_ref']) == w['candidate']
    assert git(w['remote'], 'rev-parse', 'refs/heads/main') == receipt['base_sha']
    assert w['gh'].pulls[21]['draft'] is True and w['gh'].pulls[21]['merged'] is False
    row = read(w)
    assert row['outcome'] == 'published_for_review' and not row['repair_fixed']
    assert row['release_status'] == 'not_inferred' and row['capacity_held'] and row['finished_at'] is None
    assert row['progress']['blocked_reason'] == 'PM_REPAIR_RUNTIME_TERMINATION_UNPROVEN'
    for _ in range(3):
        repairs.reconcile(ops)
        assert repairs.tick(ops) == []
        await advance(w, w['parent'])
    assert len(w['posts']) == 1 and w['runner'].ran['publication-push'] == 1
    assert ops.db.execute('SELECT COUNT(*) FROM pm_maintenance_repair_children').fetchone()[0] == 1
    assert ops.db.execute('SELECT COUNT(*) FROM pm_maintenance_repair_publications').fetchone()[0] == 1
    preserved(w, mock)


async def test_pause_reopen_keeps_old_publication_key_without_new_frames(original_repair, mock):
    w, ops = original_repair, original_repair['d'].ops
    ops.db.execute('UPDATE operations SET next_run_at=0 WHERE operation_id=?', (w['parent']['operation_id'],))
    await ops._execute(w['parent']['operation_id'])
    child = publication.child(ops, w['parent'])
    assert child is not None
    original = {key: child[key] for key in ('action', 'target', 'params', 'preconditions', 'idempotency_key')}
    epoch = pm_lifecycle_gates.head(ops.db, w['workspace_id'])['execution_epoch']
    await decide(ops, w['workspace_id'], who=OWNER)
    await ops._execute(child['operation_id'])
    await decide(ops, w['workspace_id'], 'reopen', params={'stage': 'development'}, who=OWNER)
    assert pm_lifecycle_gates.head(ops.db, w['workspace_id'])['execution_epoch'] > epoch
    again, fresh = ops.create(OWNER, **original)
    assert not fresh and again['operation_id'] == child['operation_id']
    await ops._execute(child['operation_id'])
    assert not w['posts'] and not w['runner'].scripts
    assert read(w)['capacity_held'] and read(w)['publication_child']['operation_id'] == child['operation_id']
    preserved(w, mock)


async def test_lost_real_http_201_is_never_adopted_by_matching_native_pr(original_repair, mock):
    w, ops = original_repair, original_repair['d'].ops
    w['gh'].truncate.append(('POST', r'/repos/o/r/pulls$'))
    child = await publish(w)
    assert child['status'] in {'uncertain', 'needs_attention'}, child
    assert w['gh'].pulls[21]['head']['sha'] == w['candidate'] and w['gh'].pulls[21]['draft'] is True
    for _ in range(3):
        ops.db.execute('UPDATE operations SET next_run_at=0 WHERE operation_id=?', (child['operation_id'],))
        await ops._execute(child['operation_id'])
    assert len(w['posts']) == 1 and ops.db.execute('SELECT COUNT(*) FROM pr_creation_receipts').fetchone()[0] == 0
    assert pr_publication.get_publication(ops, OWNER, child['operation_id'])['recovery']['ownership_established'] is False
    assert read(w)['capacity_held'] and read(w)['outcome'] is None
    preserved(w, mock)


async def test_original_201_saved_after_pause_never_authorizes_next_delivery(original_repair, mock, monkeypatch):
    w, ops = original_repair, original_repair['d'].ops
    client = ops.context['github']
    original = client.create_pull
    async def pause_after_original_201(*args, **kwargs):
        response = await original(*args, **kwargs)
        assert response[0] == 201
        await decide(ops, w['workspace_id'], who=OWNER)
        return response
    monkeypatch.setattr(client, 'create_pull', pause_after_original_201)
    child = await publish(w)
    receipt = pr_publication.require_creation_receipt(ops, OWNER, child['operation_id'],
        child_operation_id=w['child']['operation_id'], repair_parent_id=w['parent']['operation_id'],
        provider_origin=client.cfg.api_url, repository_id=w['gh'].repository_id)
    assert receipt['native_pr_id'] == 901 and receipt['target_verified'] is True
    assert len(w['posts']) == 1 and read(w)['capacity_held']
    with pytest.raises(OperationError):
        repairs.require_new_effect(ops, child)
    preserved(w, mock)


async def test_full_caller_scopes_cannot_exceed_adopted_pr_ceiling(original_repair, mock):
    w, ops = original_repair, original_repair['d'].ops
    await publish(w)
    for action_name in ('github.pr.merge', 'delivery.merge_and_deploy', 'deployment.start'):
        with pytest.raises(OperationError) as caught:
            evidence.require_delivery(ops, OWNER, w['parent'], {'action': action_name})
        assert caught.value.code == 'PM_REPAIR_DELIVERY_CEILING'
    assert len(w['posts']) == 1 and w['gh'].pulls[21]['draft'] is True
    preserved(w, mock)


async def test_original_source_withdrawal_hides_published_proof_and_keeps_receipt(original_repair, mock):
    w, ops = original_repair, original_repair['d'].ops
    child = await publish(w)
    assert pm_access.visible(ops.db, OWNER, child)
    setup.revoke(w)
    assert not pm_access.visible(ops.db, OWNER, child)
    with pytest.raises(OperationError):
        read(w)
    with pytest.raises(OperationError):
        pr_publication.get_publication(ops, OWNER, child['operation_id'])
    assert ops.db.execute('SELECT COUNT(*) FROM pr_creation_receipts').fetchone()[0] == 1
    assert len(w['posts']) == 1
    preserved(w, mock)


async def test_idle_or_missing_session_metadata_never_releases_runtime_capacity(original_repair, mock):
    w, ops = original_repair, original_repair['d'].ops
    await publish(w)
    sid = w['child']['result']['session_id']
    for metadata in ({'isStreaming': False}, {'isStreaming': False, 'status': 'idle'}, None):
        if metadata is None:
            mock.metas.pop(sid, None)
        else:
            mock.metas[sid] = metadata
        repairs.reconcile(ops)
        assert read(w)['outcome'] == 'published_for_review'
        assert read(w)['capacity_held'] and read(w)['finished_at'] is None
    assert not [call for call in bat_writes(mock) if call['channel'] in {'claude:interrupt-turn', 'claude:abort-session', 'claude:stop-session'}]
    preserved(w, mock)
