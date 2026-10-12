"""Original provider identity remains stronger than an equal commit SHA."""
from __future__ import annotations

import pytest

from bat_agent_connector import pm_quality_sources as sources
from tests import test_pm_work_batch_evidence as batches
from tests.test_pm_evidence import OWNER

base_ops = batches.base_ops
release_ops = batches.release_ops
workload_ops = batches.workload_ops
ops = batches.ops


@pytest.mark.parametrize('changed', [None, 'provider_origin', 'repository_id'])
async def test_equal_head_github_proof_still_requires_the_review_original_repository(ops, monkeypatch, changed):
    wid, child, ref = await batches.fixed_result(ops, monkeypatch)
    native = {'provider_origin': ops.context['github'].cfg.api_url, 'repository_id': 4242,
        'candidate_commit': ref['candidate_commit']}
    condition = {'kind': 'fixed_revision_verification', 'profile': 'github_reported_checks_v1'}
    # Existing adapter establishes exact integration source -> observed checks.
    assert sources.verification.evaluate(ops, OWNER, wid, child, condition, ref)['status'] == 'passed'
    if changed:
        native[changed] = 9999 if changed == 'repository_id' else 'https://different.example.invalid'
    proof = sources._evaluate(ops, OWNER, wid, {'native_result': native}, child, condition, ref)
    assert proof['status'] == ('blocked' if changed else 'passed')
    assert proof['semantic_acceptance'] == 'not_evaluated'
    if changed:
        assert proof['reason'] == 'original_repository_identity_unproven'
