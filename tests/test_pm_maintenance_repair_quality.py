"""Exact original accountable quality is necessary before owned publication."""
import copy
import sys
import uuid

import pytest

from bat_agent_connector import (
    api_auth,
    pm_execution_verification,
    pm_lifecycle_gates,
    pm_sprints,
    pr_delivery,
)
from bat_agent_connector import pm_maintenance_repair_evidence as evidence
from bat_agent_connector import pm_maintenance_repairs as repairs
from bat_agent_connector import pm_quality_reviews as quality
from bat_agent_connector.api_auth import Principal
from bat_agent_connector.operations import OperationError
from bat_agent_connector.task_verifier import ObservedVerifier, VerificationSettings
from tests.test_checkpoints import git
from tests.test_pm_maintenance_repair_receipts import record
from tests.test_pm_maintenance_repairs import (  # noqa: F401
    advance,
    execute,
    fixed,
    pm,
    ready,
    refresh,
    request,
    view,
    world,
)
from tests.test_pm_maintenance_repairs import repair_ready as repair_fixture
from tests.test_pm_quality_reviews import REVIEWER, assessment, selection
from tests.test_pm_quality_reviews import execute as quality_execute
from tests.test_pm_work_packages import action, member

repair_ready = repair_fixture
COMMAND = (sys.executable, '-c', 'print("verified original fixed candidate")')

OWNER = Principal('pm-owner', repairs.REQUIRED | {'integrate', 'merge', 'deploy', 'operate'})


@pytest.fixture(autouse=True)
def original_publication_authority(monkeypatch):
    from tests import test_pm_maintenance_repairs
    monkeypatch.setattr(test_pm_maintenance_repairs, 'OWNER', OWNER)
    monkeypatch.setattr(test_pm_maintenance_repairs, 'TEST_COMMAND_ID', pm_execution_verification.command_identity(COMMAND))


@pytest.fixture
async def quality_repair(repair_ready, monkeypatch):
    w, ops = repair_ready, repair_ready['d'].ops
    api_auth.issue(ops.db, OWNER.actor, sorted(OWNER.scopes))
    pm_sprints.install(ops)
    quality.install(ops)
    monkeypatch.setattr(pm_lifecycle_gates, 'RECORD_ACTIONS', pm_lifecycle_gates.RECORD_ACTIONS | set(quality.ACTIONS))
    await member(w, REVIEWER.actor, 'contributor')
    w['dod'] = (await action(w, 'pm.dod.revise', {'workspace_id': w['workspace_id']},
        {'plan_revision_id': w['package']['document']['context']['plan_ref']['revision_id'],
         'criteria': [{'id': 'regression', 'text': 'Fixed revision regression succeeds'}], 'rationale': 'Original criteria'},
        {'expected_revision_id': None}))['revision_id']
    w['parent'] = await advance(w, ops.create(OWNER, **request(w))[0])
    w['child'] = repairs._child(ops, w['parent'])
    wt = w['child']['result']['worktree_path']
    from tests.test_integration import commit
    git(wt, 'config', 'user.name', 'fixture')
    git(wt, 'config', 'user.email', 'fixture@example.invalid')
    w['candidate'] = commit(wt, 'implementation.txt', 'Exact maintenance change\n', 'Repair original Issue')
    w['tree'] = git(wt, 'rev-parse', 'HEAD^{tree}')
    pm_execution_verification.install(ops)
    w['d'].adapter.verifier = ObservedVerifier(VerificationSettings(
        commands={w['child']['params']['project_id']: COMMAND}, artifact_dir=str(w['human'].parent / 'verifier-logs')))
    configured = await pm_execution_verification.configuration(ops, OWNER, w['workspace_id'], w['child']['operation_id'])
    verified = await execute(ops, {'action': pm_execution_verification.ACTION,
        'target': {'workspace_id': w['workspace_id'], 'execution_operation_id': w['child']['operation_id']},
        'params': {key: configured[key] for key in ('candidate_commit', 'tree_hash', 'command_digest')},
        'preconditions': {'handoff_contract_digest': configured['handoff_contract_digest']}, 'idempotency_key': uuid.uuid4().hex}, who=OWNER)
    assert verified['result']['status'] == 'passed', verified
    w['proof'] = verified['result']['reference']
    env = selection(w)
    for item in env['params']['criteria']:
        item['condition'] = repairs._intent(w['parent'])['rule']['verification']
    created = await quality_execute(w, env)
    review = quality.get_review(ops, OWNER, w['workspace_id'], created['result']['review_id'])['review']
    await quality_execute(w, assessment(w, review), actor=REVIEWER)
    w['review'] = quality.get_review(ops, OWNER, w['workspace_id'], review['review_id'])['review']
    saved = w['review']['assessment']
    w['quality_ref'] = {'review_id': review['review_id'], **{key: saved[key] for key in ('operation_id', 'version', 'document_digest')}}
    await execute(ops, record(w, w['parent'], {'kind': 'quality', 'reference': w['quality_ref']}), who=OWNER)
    return w


def publish_gate(w, **changes):
    rule = repairs._intent(w['parent'])['rule']
    args = {'child_operation_id': w['child']['operation_id'], 'candidate_sha': w['candidate'],
        'candidate_tree': w['tree'], 'base_sha': rule['release']['commit_sha'], 'base_ref': 'main',
        'files': ['README.md'], 'files_complete': True, 'quality_ref': w['quality_ref']}
    args.update(changes)
    return evidence.require_publication(w['d'].ops, OWNER, w['parent'], **args)


async def test_exact_quality_original_and_candidate_gate(quality_repair):
    w = quality_repair
    assert publish_gate(w)['candidate_tree'] == w['tree']
    value = view(w, w['parent'])
    assert value['progress']['quality'][0]['reference'] == w['quality_ref']
    assert value['progress']['acceptance'] == []
    assert value['capacity_held'] and not value['repair_fixed']
    assert repairs.operation_visible(w['d'].ops.db, OWNER, w['parent'])


@pytest.mark.parametrize('change', ['child', 'candidate', 'tree', 'base', 'branch', 'incomplete', 'paths', 'selector'])
async def test_publication_requires_exact_original_revision_and_coverage(quality_repair, change):
    w = quality_repair
    delta = {'child': {'child_operation_id': 'op_' + 'a' * 32}, 'candidate': {'candidate_sha': 'a' * 40},
        'tree': {'candidate_tree': 'a' * 40}, 'base': {'base_sha': 'a' * 40}, 'branch': {'base_ref': 'other'},
        'incomplete': {'files_complete': False}, 'paths': {'files': []},
        'selector': {'quality_ref': {**w['quality_ref'], 'document_digest': 'f' * 64}}}[change]
    with pytest.raises(OperationError):
        publish_gate(w, **delta)


async def test_new_assessment_is_never_substituted_for_selected_original(quality_repair):
    w, ops = quality_repair, quality_repair['d'].ops
    await quality_execute(w, assessment(w, w['review'], version=w['review']['version']), actor=REVIEWER)
    with pytest.raises(OperationError) as caught:
        publish_gate(w)
    assert caught.value.code == 'PM_REPAIR_QUALITY_UNPROVEN'
    assert view(w, w['parent'])['progress']['quality'][0]['reference'] == w['quality_ref']
    original = quality.get_assessment(ops, OWNER, w['workspace_id'], **w['quality_ref'])
    assert original['assessment']['document']['qualified']


async def test_revocation_keeps_history_and_blocks_new_publication(quality_repair):
    w = quality_repair
    await quality_execute(w, {'action': quality.REVOKE, 'target': {'workspace_id': w['workspace_id'], 'review_id': w['review']['review_id']},
        'params': {'reason': 'Original criteria need further review'}, 'preconditions': {'expected_version': w['review']['version']},
        'idempotency_key': uuid.uuid4().hex}, actor=OWNER)
    with pytest.raises(OperationError):
        publish_gate(w)
    assert view(w, w['parent'])['progress']['quality'][0]['original_outcome']['qualified']


async def test_rule_and_policy_paths_both_gate_fixed_rename_sides(quality_repair, monkeypatch):
    w = quality_repair
    original = evidence.policies_for
    def narrowed(ops, parent):
        policy = copy.deepcopy(original(ops, parent))
        policy['allowed_paths'] = ['src/']
        return policy
    monkeypatch.setattr(evidence, 'policies_for', narrowed)
    with pytest.raises(OperationError) as caught:
        publish_gate(w, files=['src/new.py', 'private/old.py'])
    assert caught.value.code == 'PM_REPAIR_PATH_OUTSIDE_POLICY'
    assert publish_gate(w, files=['src/new.py'])


async def test_review_source_access_gates_saved_parent_reads(quality_repair, monkeypatch):
    from bat_agent_connector import pm_quality_sources
    w, ops = quality_repair, quality_repair['d'].ops
    original = pm_quality_sources.proof_access
    def hidden(*args, **kwargs):
        raise OperationError('NOT_FOUND', 'Original verifier source unavailable', 404)
    monkeypatch.setattr(pm_quality_sources, 'proof_access', hidden)
    assert not repairs.operation_visible(ops.db, OWNER, w['parent'])
    monkeypatch.setattr(pm_quality_sources, 'proof_access', original)
    assert repairs.operation_visible(ops.db, OWNER, w['parent'])


def test_fixed_native_pr_identity_is_additive_and_changed_ids_change_scope():
    old = {'number': 7, 'head': {'sha': 'a' * 40}, 'base': {'sha': 'b' * 40}}
    assert 'native_pr_id' not in pr_delivery.identity(old)
    one = pr_delivery.identity({**old, 'id': 101, 'node_id': 'PR_original'})
    two = pr_delivery.identity({**old, 'id': 102, 'node_id': 'PR_other'})
    assert one['native_pr_id'] == 101 and one['native_pr_node_id'] == 'PR_original'
    assert one != two


@pytest.fixture
async def publishing(quality_repair, monkeypatch):
    from bat_agent_connector.config import parse_config
    from tests.test_integration import RaceRunner
    from tests.test_pr_publication import pub
    w, ops = quality_repair, quality_repair['d'].ops
    raw = copy.deepcopy(w['raw'])
    raw['github']['repos'][0]['integrate'] = {'hosts': ['h1'], 'remote_url': str(w['remote']), 'workspace': 'ws-1'}
    ops.context['github_config'] = parse_config(raw).github
    w['runner'] = RaceRunner()
    ops.context['git_runner'] = w['runner']
    w['original'], w['result_sha'] = w['child'], w['candidate']
    return await pub.__wrapped__(w, monkeypatch)


async def test_parent_composes_only_one_original_publication_and_native_201(publishing):
    from bat_agent_connector import pm_maintenance_repair_publication as composed
    from bat_agent_connector import pr_publication
    from tests.test_checkpoints import snapshot
    w, ops = publishing, publishing['d'].ops
    human = snapshot(w['human'])
    w['parent'] = await advance(w, w['parent'], rounds=5)
    child = composed.child(ops, w['parent'])
    assert child is not None, {key: w['parent'].get(key) for key in ('status', 'error_code', 'error_message', 'result', 'reason')}
    assert child['status'] == 'succeeded', child
    receipt = pr_publication.require_creation_receipt(ops, OWNER, child['operation_id'],
        child_operation_id=w['child']['operation_id'], provider_origin=ops.context['github'].cfg.api_url,
        repository_id=w['gh'].repository_id, repair_parent_id=w['parent']['operation_id'])
    assert receipt['quality_ref'] == w['quality_ref'] and receipt['candidate_tree'] == w['tree']
    assert receipt['native_pr_id'] == 801
    assert child['params']['public_body'] == 'Fixes #50'
    assert child['params']['public_title'] == 'Maintenance repair for #50'
    assert child['params']['draft'] is True
    assert w['parent']['status'] == 'succeeded'
    assert w['parent']['result']['outcome'] == 'published_for_review'
    assert w['parent']['result']['capacity_held'] and not w['parent']['result']['repair_fixed']
    assert w['parent']['result']['release_publication'] == 'not_inferred'
    saved = view(w, w['parent'])
    assert saved['outcome'] == saved['progress']['outcome'] == 'published_for_review'
    assert saved['capacity_held'] and saved['finished_at'] is None
    assert saved['progress']['blocked_reason'] == 'PM_REPAIR_RUNTIME_TERMINATION_UNPROVEN'
    await advance(w, w['parent'], rounds=4)
    assert len(w['posts']) == 1 and ops.db.execute('SELECT COUNT(*) FROM pm_maintenance_repair_publications').fetchone()[0] == 1
    assert view(w, w['parent'])['publication_child']['operation_id'] == child['operation_id']
    assert snapshot(w['human']) == human


async def test_completed_publication_survives_stop_without_inventing_runtime_death(publishing):
    from bat_agent_connector import pm_maintenance_repair_publication as composed
    from tests.test_pm_lifecycle import decide
    w, ops = publishing, publishing['d'].ops
    # Admit and finish only the original publication, then stop the workspace
    # before the parent records that already achieved policy outcome.
    await ops._execute(w['parent']['operation_id'])
    child = composed.child(ops, w['parent'])
    await ops._execute(child['operation_id'])
    assert ops.get(child['operation_id'])['status'] == 'succeeded'
    await decide(ops, w['workspace_id'], who=OWNER)
    ops.db.execute('UPDATE api_principals SET revoked_at=0 WHERE actor=?', (OWNER.actor,))
    w['parent'] = await advance(w, w['parent'])
    assert w['parent']['status'] == 'succeeded', w['parent']
    assert w['parent']['result']['outcome'] == 'published_for_review'
    repairs.reconcile(ops)
    assert view(w, w['parent'])['capacity_held']
    assert len(w['posts']) == 1


async def test_unknown_original_publication_holds_capacity_and_never_reposts(publishing):
    from bat_agent_connector import pm_maintenance_repair_publication as composed
    w, ops = publishing, publishing['d'].ops
    w['lost_post'] = True
    await advance(w, w['parent'], rounds=5)
    child = composed.child(ops, w['parent'])
    assert child is not None and child['status'] in {'uncertain', 'needs_attention'}, child
    await advance(w, w['parent'], rounds=5)
    assert len(w['posts']) == 1
    saved = view(w, w['parent'])
    assert saved['capacity_held'] and saved['outcome'] is None
    assert ops.db.execute('SELECT COUNT(*) FROM pr_creation_receipts').fetchone()[0] == 0


async def test_public_copied_publication_envelope_cannot_claim_private_parent(publishing):
    from bat_agent_connector import pm_maintenance_repair_publication as composed
    w, ops = publishing, publishing['d'].ops
    env = composed._request(ops, w['parent'], w['quality_ref'])
    env['idempotency_key'] = uuid.uuid4().hex
    original = ops.create(OWNER, **env)[0]
    await ops._execute(original['operation_id'])
    done = ops.get(original['operation_id'])
    assert done['status'] == 'failed' and done['error_code'] == 'PM_REPAIR_PUBLICATION_CHANGED', done
    assert not w['posts'] and not w['runner'].scripts


async def test_pause_after_publication_admission_blocks_new_push_and_preserves_child(publishing):
    from bat_agent_connector import pm_maintenance_repair_publication as composed
    from tests.test_pm_lifecycle import decide
    w, ops = publishing, publishing['d'].ops
    ops.db.execute('UPDATE operations SET next_run_at=0 WHERE operation_id=?', (w['parent']['operation_id'],))
    await ops._execute(w['parent']['operation_id'])
    child = composed.child(ops, w['parent'])
    assert child is not None
    await decide(ops, w['workspace_id'], who=OWNER)
    await ops._execute(child['operation_id'])
    assert not w['posts'] and not w['runner'].scripts
    assert view(w, w['parent'])['publication_child']['operation_id'] == child['operation_id']
