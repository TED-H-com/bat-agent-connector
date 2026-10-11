"""Verified original deployment advances its existing channel without republishing."""
import copy
import re

import pytest

from bat_agent_connector import deployment_store, pm_lifecycle_gates
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
base_ready_chain = fixtures.ready_chain


@pytest.fixture
async def ready_chain(base_ready_chain):
    w = base_ready_chain
    comparison = w['gh'].comparison
    def compare(base, head, page):
        result = comparison(base, head, page)
        if result is not None:
            result['base_commit'] = {'sha': w['gh'].branches.get(base, base)}
        return result
    w['gh'].comparison = compare
    after_post = w['after_post']
    def published(payload):
        after_post(payload)
        previous = w['gh'].before_request
        def refs(method, path, body):
            if previous:
                previous(method, path, body)
            if method == 'GET' and path == '/repos/o/r/git/ref/heads/main':
                w['gh'].script.append(('GET', re.escape(path) + '$', 200, {}, {
                    'ref': 'refs/heads/main', 'object': {'type': 'commit', 'sha': w['gh'].branches['main']}}))
        w['gh'].before_request = refs
    w['after_post'] = published
    return w


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
    original = baseline.child(ops, parent)
    assert selected['status'] == 'updated', (selected, original and original.get('status_reason'))
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


@pytest.mark.parametrize('stop', ['revoke', 'pause', 'cancel'])
async def test_stop_before_selection_preserves_delivery_and_original_baseline(ready_chain, stop):
    from tests.test_pm_lifecycle import decide
    w, ops = ready_chain, ready_chain['d'].ops
    parent = await finish_deployment(w)
    if stop == 'revoke':
        ops.db.execute('UPDATE api_principals SET revoked_at=0 WHERE actor=?', (OWNER.actor,))
    elif stop == 'pause':
        await decide(ops, w['workspace_id'], who=OWNER)
    else:
        # Simulate restart of a running original parent whose cancellation was
        # committed after delivery completed but before its local result did.
        ops.db.execute("UPDATE operations SET status='running',cancel_requested=1 WHERE operation_id=?", (parent['operation_id'],))
    parent = await advance(w, parent, rounds=4)
    assert parent['status'] == 'succeeded', parent
    assert parent['result']['outcome'] == 'deployed' and parent['result']['baseline_update']['status'] == 'not_updated'
    assert baselines._head(ops.db, w['workspace_id'])['baseline_id'] == w['initial_baseline']['baseline_id']
    assert baseline.child(ops, parent) is None
    assert w['gh'].count('POST', 'dispatches') == 2


async def test_committed_baseline_recovers_parent_after_a_later_deployment(ready_chain):
    w, ops = ready_chain, ready_chain['d'].ops
    parent = await finish_deployment(w)
    await ops._execute(parent['operation_id'])
    selection = baseline.child(ops, parent)
    assert selection is not None
    await ops._execute(selection['operation_id'])
    assert ops.get(selection['operation_id'])['status'] == 'succeeded'
    selected = ops.get(selection['operation_id'])['result']
    assert not ops.db.execute("SELECT 1 FROM operation_steps WHERE operation_id=? AND name='pm.repair.delivered'",
                             (parent['operation_id'],)).fetchone()
    original = chain.child(ops, parent)
    previous = deployment_store.deployment(ops.db, operation_id=original['operation_id'])
    newer = 'd' * 40
    w['gh'].commits[newer] = {'sha': newer, 'parents': [{'sha': previous['identity']['source_sha']}]}
    w['gh'].branches['main'] = newer
    await deployments.deployed(w['d'], w['gh'], key='later-independent-deployment', sha=newer)
    assert deployment_store.deployment(ops.db, operation_id=original['operation_id'])['state'] == 'superseded'
    await ops._execute(parent['operation_id'])
    recovered = ops.get(parent['operation_id'])
    assert recovered['status'] == 'succeeded', (recovered.get('error_code'), recovered.get('reason'))
    assert recovered['result']['baseline_update']['baseline_id'] == selected['baseline_id']
    assert recovered['result']['delivery']['deployment']['state'] == 'superseded'
    assert view(w, recovered)['baseline_child']['operation_id'] == selection['operation_id']
    assert baselines.get_baseline(ops, OWNER, w['workspace_id'], selected['baseline_id'])['baseline']['previous_baseline_id'] == w['initial_baseline']['baseline_id']
    assert w['gh'].count('POST', 'dispatches') == 3 and len(chain._rows(ops, parent, 'baseline')) == 1


@pytest.mark.parametrize('token_missing', [False, True])
async def test_private_baseline_uses_bounded_http_and_current_token(ready_chain, monkeypatch, token_missing):
    from bat_agent_connector.errors import TokenUnavailable
    w, ops = ready_chain, ready_chain['d'].ops
    parent = await finish_deployment(w)
    await ops._execute(parent['operation_id'])
    selection = baseline.child(ops, parent)
    assert selection is not None
    client = ops.context['github']
    request, timeouts = client._request, []
    def capture(method, path, body=None, query=None, *, timeout_s=None):
        timeouts.append(timeout_s)
        return request(method, path, body, query, timeout_s=timeout_s)
    monkeypatch.setattr(client, '_request', capture)
    reads = len(w['gh'].requests)
    if token_missing:
        def unavailable():
            raise TokenUnavailable('fixture current token unavailable')
        monkeypatch.setattr(client, '_fixed_token', None)
        monkeypatch.setattr(client.cfg, 'token', unavailable)
        client._last_token = 'fixture-stale-token-never-reused'
    await ops._execute(selection['operation_id'])
    assert timeouts and all(value is not None and 0 < value <= 30 for value in timeouts)
    if token_missing:
        assert ops.get(selection['operation_id'])['status'] != 'succeeded'
        assert len(w['gh'].requests) == reads
        assert baselines._head(ops.db, w['workspace_id'])['baseline_id'] == w['initial_baseline']['baseline_id']
    else:
        assert ops.get(selection['operation_id'])['status'] == 'succeeded', ops.get(selection['operation_id'])
