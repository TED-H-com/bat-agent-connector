"""Verified original deployment advances its existing channel without republishing."""
import copy

import pytest

from bat_agent_connector import pm_lifecycle_gates
from bat_agent_connector import pm_maintenance_policy as policies
from bat_agent_connector import pm_maintenance_repair_baseline as baseline
from bat_agent_connector import pm_release_baselines as baselines
from bat_agent_connector.config import parse_config
from bat_agent_connector.operations import OperationError
from tests import test_deployments as deployments
from tests import test_pm_maintenance_repair_delivery as fixtures
from tests import test_pm_maintenance_repairs as setup
from tests.fakeverifier import FakeVerifier
from tests.test_pm_delivery_lineage import sync_provider_graph
from tests.test_pm_maintenance_repair_delivery import (  # noqa: F401
    OWNER,
    advance,
    chain,
    execute,
    original_publication_authority,
    pm,
    publishing,
    quality_repair,
    ready,
    repair_ready,
    view,
    world,
)
from tests.test_pm_release_baselines import remove_repo

adopted_delivery = fixtures.adopted_delivery
fixed = fixtures.fixed
ready_chain = fixtures.ready_chain


@pytest.fixture
def ceiling():
    return 'deploy'


@pytest.fixture(autouse=True)
def original_deployment(fixed, monkeypatch, adopted_delivery):
    w, ops = fixed, fixed['d'].ops
    raw = copy.deepcopy(w['raw'])
    raw['github']['repos'][0]['integrate'] = {'hosts': ['h1'], 'remote_url': str(w['remote']), 'workspace': 'ws-1'}
    raw['deploy'] = {'recipes': [{'name': 'prod', 'repository': 'o/r', 'environment': 'production',
        'mode': 'workflow_dispatch', 'workflow': 'deploy.yml', 'deploy_job': 'deploy', 'ref': 'main',
        'inputs': {'source_sha': 'source_sha', 'operation_id': 'operation_id'}, 'run_name_contains': 'operation_id',
        'verification': {'kind': 'http_json', 'url': 'https://deployment.example/status',
            'version_required': True, 'health_required': True}}]}
    w['raw'] = raw
    ops.context['github_config'] = parse_config(raw).github
    ops.context['deployment_verifier'] = FakeVerifier(w['gh'])
    before = w['gh'].before_request
    original = setup.execute
    async def selecting(ops, envelope, **kwargs):
        if envelope['action'] == baselines.ACTION and not baselines._head(ops.db, w['workspace_id'])['baseline_id']:
            sha = w['package']['document']['code_source']['source_sha']
            current = w['gh'].before_request
            w['gh'].before_request = before
            sync_provider_graph(w)
            w['gh'].branches['main'] = sha
            try:
                deployed = await deployments.deployed(w['d'], w['gh'], key='original-maintained-deployment', sha=sha)
            finally:
                w['gh'].before_request = current
            w['initial_deployment'] = deployed
            envelope = copy.deepcopy(envelope)
            envelope['params'].update(source={'kind': 'deployment', 'deployment_id': deployed['result']['deployment_id']}, channel='production')
        result = await original(ops, envelope, **kwargs)
        if envelope['action'] == baselines.ACTION:
            w['initial_baseline'] = copy.deepcopy(result['result'])
        return result
    monkeypatch.setattr(setup, 'execute', selecting)


async def finish_deployment(w):
    parent = await advance(w, w['parent'], rounds=10)
    completed = chain.child(w['d'].ops, parent)
    assert completed and completed['status'] == 'waiting_external', completed
    for run_id, run in w['gh'].runs.items():
        run.update(status='completed', conclusion='success')
        w['gh'].jobs[run_id][1]['conclusion'] = 'success'
    await w['d'].ops._execute(completed['operation_id'])
    return w['d'].ops.get(parent['operation_id'])


async def test_verified_same_channel_uses_original_registration_and_keeps_policy(ready_chain):
    w, ops = ready_chain, ready_chain['d'].ops
    before = policies.active_record(ops, OWNER, w['workspace_id'])
    lifecycle = pm_lifecycle_gates.head(ops.db, w['workspace_id'])
    parent = await finish_deployment(w)
    parent = await advance(w, parent, rounds=10)
    assert parent['status'] == 'succeeded', parent
    selected = parent['result']['baseline_update']
    assert selected['status'] == 'updated', selected
    record = baselines.get_baseline(ops, OWNER, w['workspace_id'], selected['baseline_id'])['baseline']
    assert record['previous_baseline_id'] == w['initial_baseline']['baseline_id']
    assert record['document']['intent']['source']['deployment_id'] == parent['result']['delivery']['deployment']['deployment_id']
    assert record['document']['delivery_status'] == {'github_release': 'not_assessed', 'package_registry': 'not_assessed', 'deployment': 'verified'}
    after = policies.active_record(ops, OWNER, w['workspace_id'])
    assert before['policy_id'] == after['policy_id'] and after['baseline_id'] == selected['baseline_id']
    assert pm_lifecycle_gates.head(ops.db, w['workspace_id']) == lifecycle
    assert parent['result']['repair_fixed'] is False and view(w, parent)['capacity_held']
    original = baseline.child(ops, parent)
    assert view(w, parent)['baseline_child']['operation_id'] == original['operation_id']
    # Recovery after selection uses its receipt, even though the old baseline CAS
    # and original repair's new-effect authority no longer match the new head.
    await ops._execute(original['operation_id'])
    await advance(w, parent)
    assert len(chain._rows(ops, parent, 'baseline')) == 1
    assert w['gh'].count('POST', 'dispatches') == 2 and w['gh'].count('PUT', 'merge-async') == 1
    await remove_repo(ops, w['child']['params']['project_id'])
    with pytest.raises(OperationError):
        baselines.get_baseline(ops, OWNER, w['workspace_id'], selected['baseline_id'])


@pytest.mark.parametrize('stop', ['revoke', 'pause'])
async def test_stop_before_selection_preserves_delivery_and_original_baseline(ready_chain, stop):
    from tests.test_pm_lifecycle import decide
    w, ops = ready_chain, ready_chain['d'].ops
    parent = await finish_deployment(w)
    if stop == 'revoke':
        ops.db.execute('UPDATE api_principals SET revoked_at=0 WHERE actor=?', (OWNER.actor,))
    else:
        await decide(ops, w['workspace_id'], who=OWNER)
    parent = await advance(w, parent, rounds=4)
    assert parent['status'] == 'succeeded', parent
    assert parent['result']['outcome'] == 'deployed' and parent['result']['baseline_update']['status'] == 'not_updated'
    assert baselines._head(ops.db, w['workspace_id'])['baseline_id'] == w['initial_baseline']['baseline_id']
    assert baseline.child(ops, parent) is None
    assert w['gh'].count('POST', 'dispatches') == 2
