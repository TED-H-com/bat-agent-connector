"""Registered fixed-result adapters consume persisted central/provider receipts."""
from __future__ import annotations

import copy

import pytest

from bat_agent_connector import pm_work_batch_evidence as evidence
from bat_agent_connector.operations import OperationError
from tests import test_pm_github_observation as github
from tests.test_pm_evidence import OWNER, integration_receipt, source_operation

base_ops = github.base_ops
release_ops = github.release_ops
workload_ops = github.workload_ops
ops = github.ops


async def fixed_result(ops, monkeypatch):
    data = await github.prepared(ops, monkeypatch)
    provider = ops.context['github']
    github.populate(provider)
    provider.statuses[0]['state'] = 'success'
    observation, _ = await github.observe(ops, data)
    # Adapter fixture: only persisted original operation/receipt records are
    # supplied here. End-to-end handoff/Git delivery has its own real Git suite.
    child = await source_operation(ops, action='repository.continue')
    integration = await source_operation(ops, action='integration.apply')
    ops._merge_refs(integration['operation_id'], {'admission_binding': {'pm_delivery': {
        'provider_origin': provider.cfg.api_url, 'repository_id': 4242, 'pull_number': 7}}})
    integration_receipt(ops, integration['operation_id'], child['operation_id'])
    ops.db.execute('UPDATE integration_receipts SET pull_number=7,delivered_sha=? WHERE operation_id=?',
        (github.SHA, integration['operation_id']))
    record = github.observation._record(ops.db, data['workspace_id'], observation['result']['observation_id'])
    ref = {'observation_id': record['observation_id'], 'document_digest': record['document_digest'],
        'integration_operation_id': integration['operation_id'], 'source_seq': 1, 'candidate_commit': github.SHA}
    return data['workspace_id'], child, ref


async def test_exact_github_revision_requires_original_integration_and_reported_success(ops, monkeypatch):
    wid, child, ref = await fixed_result(ops, monkeypatch)
    condition = {'kind': 'fixed_revision_verification', 'profile': 'github_reported_checks_v1'}
    proof = evidence.evaluate(ops, OWNER, wid, child, condition, ref)
    assert proof['status'] == 'passed'
    assert proof['semantic_acceptance'] == 'not_evaluated'
    assert proof['coverage'] == 'all_reported_checks_at_exact_original_head'
    assert len(proof['operation_ids']) == 3
    assert 'PRIVATE' not in str(proof)
    for field, value in [('source_id', 'op_' + 'e' * 32), ('delivered_sha', 'f' * 40), ('status', 'uncertain')]:
        old = ops.db.execute('SELECT ' + field + ' FROM integration_receipts WHERE operation_id=?', (ref['integration_operation_id'],)).fetchone()[0]
        ops.db.execute('UPDATE integration_receipts SET ' + field + '=? WHERE operation_id=?', (value, ref['integration_operation_id']))
        assert evidence.evaluate(ops, OWNER, wid, child, condition, ref)['status'] == 'blocked'
        ops.db.execute('UPDATE integration_receipts SET ' + field + '=? WHERE operation_id=?', (old, ref['integration_operation_id']))
    changed = {**ref, 'document_digest': 'f' * 64}
    with pytest.raises(OperationError, match='digest does not match'):
        evidence.evaluate(ops, OWNER, wid, child, condition, changed)


@pytest.mark.parametrize('problem', ['pending', 'empty', 'wrong_provider', 'wrong_repository', 'wrong_pull', 'wrong_head'])
async def test_github_result_does_not_infer_native_identity_or_unreported_checks(ops, monkeypatch, problem):
    wid, child, ref = await fixed_result(ops, monkeypatch)
    provider = ops.context['github']
    if problem in {'pending', 'empty'}:
        if problem == 'pending':
            provider.statuses[0]['state'] = 'pending'
        else:
            provider.checks, provider.statuses = [], []
        observation, _ = await github.observe(ops, {'workspace_id': wid})
        record = github.observation._record(ops.db, wid, observation['result']['observation_id'])
        ref.update(observation_id=record['observation_id'], document_digest=record['document_digest'])
    elif problem == 'wrong_head':
        ref['candidate_commit'] = 'f' * 40
    else:
        binding = copy.deepcopy(ops.get(ref['integration_operation_id'])['external_refs']['admission_binding'])
        field, value = {'wrong_provider': ('provider_origin', 'https://other.example.invalid'),
            'wrong_repository': ('repository_id', 999), 'wrong_pull': ('pull_number', 999)}[problem]
        binding['pm_delivery'][field] = value
        ops._merge_refs(ref['integration_operation_id'], {'admission_binding': binding})
    condition = {'kind': 'fixed_revision_verification', 'profile': 'github_reported_checks_v1'}
    assert evidence.evaluate(ops, OWNER, wid, child, condition, ref)['status'] == 'blocked'


@pytest.mark.parametrize('profile,value', [
    ('observed_runner_v1', {'verification_id': True, 'candidate_commit': 'a' * 40, 'tree_hash': 'b' * 40}),
    ('observed_runner_v1', {'verification_id': 1, 'candidate_commit': int('1' * 40), 'tree_hash': 'b' * 40}),
    ('github_reported_checks_v1', {'observation_id': [], 'document_digest': 'd' * 64, 'integration_operation_id': [], 'source_seq': 2**63, 'candidate_commit': 'a' * 40}),
])
async def test_invalid_native_evidence_reference_is_refused_before_sql_lookup(ops, profile, value):
    child = await source_operation(ops, action='repository.continue')
    condition = {'kind': 'fixed_revision_verification', 'profile': profile}
    if profile == 'observed_runner_v1':
        condition['command_digest'] = evidence.digest('pytest')
    with pytest.raises(OperationError) as invalid:
        evidence.evaluate(ops, OWNER, 'ws_' + 'f' * 32, child, condition, value)
    assert invalid.value.code == 'PM_BATCH_EVIDENCE_INVALID' and invalid.value.status == 422
