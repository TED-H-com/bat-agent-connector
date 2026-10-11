"""Actual private publish/observe/merge engines through temporary Git and providers."""
import copy

import pytest

from bat_agent_connector import pm_lifecycle_gates, pm_source_lineage
from bat_agent_connector import pm_maintenance_repair_delivery as chain
from bat_agent_connector import pm_maintenance_repair_evidence as evidence
from bat_agent_connector import pm_maintenance_repair_publication as publication
from bat_agent_connector import pm_required_ci as required_ci
from bat_agent_connector.config import parse_config
from tests import test_pm_maintenance_repairs as setup
from tests.test_checkpoints import git
from tests.test_pm_delivery_lineage import sync_provider_graph
from tests.test_pm_maintenance_repair_quality import (  # noqa: F401
    OWNER,
    advance,
    execute,
    fixed,
    original_publication_authority,
    pm,
    quality_repair,
    ready,
    repair_ready,
    view,
    world,
)
from tests.test_pm_maintenance_repair_quality import publishing as publishing_fixture
from tests.test_pm_repair_required_ci_binding import CIProvider

publishing = publishing_fixture

@pytest.fixture
def ceiling():
    return 'merge'


@pytest.fixture(autouse=True)
def adopted_delivery(monkeypatch, ceiling):
    original = setup.settings
    def settings():
        result = copy.deepcopy(original())
        result['observation']['required_ci_profile'] = required_ci.PROFILE
        return result
    monkeypatch.setattr(setup, 'settings', settings)
    original_execute = setup.execute
    async def adopting(ops, envelope, **kwargs):
        if envelope['action'] == setup.policy.ACTION:
            envelope = copy.deepcopy(envelope)
            envelope['params']['policy']['delivery'] = {'maximum': ceiling,
                'environment': 'production' if ceiling == 'deploy' else None}
        return await original_execute(ops, envelope, **kwargs)
    monkeypatch.setattr(setup, 'execute', adopting)


@pytest.fixture
async def ready_chain(publishing, monkeypatch, ceiling):
    w, ops = publishing, publishing['d'].ops
    comparison = w['gh'].comparison
    def complete_diff(base, head, page):
        result = comparison(base, head, page)
        if result is not None and head == w['candidate']:
            paths = git(w['remote'], 'diff', '--name-only', base, head).splitlines()
            result['files'] = [{'filename': path, 'status': 'added'} for path in paths]
        return result
    monkeypatch.setattr(w['gh'], 'comparison', complete_diff)
    def created(payload):
        # The provider keeps the ACTUAL POST draft flag. No later add_pr default
        # quietly turns a draft into a mergeable PR.
        body = w['posts'][-1]
        pr = w['gh'].add_pr(payload['number'], payload['head']['sha'], head_ref=body['head'],
            id=payload['id'], node_id=payload['node_id'], draft=body['draft'])
        pr['base']['sha'] = payload['base']['sha']
        w['gh'].branches['main'] = payload['base']['sha']
        w['gh'].before_request = None
        sync_provider_graph(w)
        receipt = {'repository_id': w['gh'].repository_id, 'repository': 'o/r',
            'pull_number': payload['number'], 'candidate_sha': payload['head']['sha']}
        w['ci_provider'] = CIProvider(ops.context['github'].cfg, w, receipt)
        if w.get('pending_ci'):
            w['ci_provider'].checks[0].update(status='in_progress', conclusion=None)
        monkeypatch.setattr(ops.context['github'], 'bounded_get', w['ci_provider'].bounded_get)
    w['after_post'] = created
    if ceiling == 'deploy':
        from tests.fakeverifier import FakeVerifier
        raw = copy.deepcopy(w['raw'])
        raw['github']['repos'][0]['integrate'] = {'hosts': ['h1'], 'remote_url': str(w['remote']), 'workspace': 'ws-1'}
        raw['deploy'] = {'recipes': [{'name': 'prod', 'repository': 'o/r', 'environment': 'production',
            'mode': 'workflow_dispatch', 'workflow': 'deploy.yml', 'deploy_job': 'deploy', 'ref': 'main',
            'inputs': {'source_sha': 'source_sha', 'operation_id': 'operation_id'}, 'run_name_contains': 'operation_id',
            'verification': {'kind': 'http_json', 'url': 'https://deployment.example/status',
                'version_required': True, 'health_required': True}}]}
        ops.context['github_config'] = parse_config(raw).github
        ops.context['deployment_verifier'] = FakeVerifier(w['gh'])
    return w


async def test_adopted_merge_composes_original_non_draft_ci_and_verified_merge(ready_chain):
    w, ops = ready_chain, ready_chain['d'].ops
    parent = await advance(w, w['parent'], rounds=10)
    assert parent['status'] == 'succeeded', parent
    assert parent['result']['outcome'] == 'merged' and parent['result']['capacity_held']
    assert not parent['result']['repair_fixed'] and parent['result']['release_publication'] == 'not_inferred'
    pub = publication.child(ops, parent)
    assert pub['params']['draft'] is False and w['posts'][0]['draft'] is False
    child = chain.child(ops, parent)
    assert child['status'] == 'succeeded' and child['result']['verified']
    reference = child['external_refs']['admission_binding']['pm_delivery']['pm_repair_required_ci'][parent['operation_id']]
    receipt, _ = evidence.owned_creation(ops, OWNER, parent)
    assert required_ci.historical(ops, OWNER, parent, receipt, reference)['proof']['status'] == 'passed'
    assert len(chain._rows(ops, parent, 'observation')) == 1
    assert ops.db.execute('SELECT COUNT(*) FROM pm_maintenance_check_runs').fetchone()[0] == 0
    assert w['gh'].count('PUT', 'merge-async') == 1
    await advance(w, parent)
    assert len(w['posts']) == w['gh'].count('PUT', 'merge-async') == 1
    assert view(w, parent)['delivery_child']['operation_id'] == child['operation_id']
    assert parent['operation_id'] in pm_source_lineage.roots(ops.db, 'operation', child['operation_id'])


async def test_private_unknown_merge_never_resends_and_exact_result_recovers(ready_chain):
    w, ops = ready_chain, ready_chain['d'].ops
    w['gh'].truncate.append(('PUT', r'/merge-async$'))
    parent = await advance(w, w['parent'], rounds=10)
    original = chain.child(ops, parent)
    assert original['status'] in {'uncertain', 'needs_attention'}, original
    for _ in range(3):
        await ops._execute(original['operation_id'])
    assert w['gh'].count('PUT', 'merge-async') == 1
    assert view(w, parent)['capacity_held']
    number = publication.published_receipt(ops, parent)['pull_number']
    w['gh'].merge(number)
    await ops._execute(original['operation_id'])
    parent = await advance(w, parent)
    assert parent['result']['outcome'] == 'merged'
    assert w['gh'].count('PUT', 'merge-async') == 1


@pytest.mark.parametrize('stop', ['pause', 'revoke'])
async def test_private_observation_checks_current_owner_and_epoch_before_each_read(ready_chain, stop):
    from tests.test_pm_lifecycle import decide
    w, ops = ready_chain, ready_chain['d'].ops
    await ops._execute(w['parent']['operation_id'])
    pub = publication.child(ops, w['parent'])
    await ops._execute(pub['operation_id'])
    await ops._execute(w['parent']['operation_id'])
    row = chain._rows(ops, w['parent'], 'observation')[0]
    if stop == 'pause':
        await decide(ops, w['workspace_id'], who=OWNER)
    else:
        ops.db.execute('UPDATE api_principals SET revoked_at=0 WHERE actor=?', (OWNER.actor,))
    before = len(w['ci_provider'].reads)
    await ops._execute(row['operation_id'])
    assert len(w['ci_provider'].reads) == before
    assert ops.get(row['operation_id'])['status'] == 'failed'
    assert chain.child(ops, w['parent']) is None and view(w, w['parent'])['capacity_held']


@pytest.mark.parametrize('ceiling', ['deploy'])
async def test_adopted_deploy_uses_one_existing_combined_engine_and_real_provider_receipts(ready_chain):
    w, ops = ready_chain, ready_chain['d'].ops
    parent = await advance(w, w['parent'], rounds=10)
    child = chain.child(ops, parent)
    assert child is not None and child['action'] == 'delivery.merge_and_deploy', parent
    assert child['status'] == 'waiting_external', child
    for run_id, run in w['gh'].runs.items():
        run.update(status='completed', conclusion='success')
        w['gh'].jobs[run_id][1]['conclusion'] = 'success'
    ops.db.execute('UPDATE operations SET next_run_at=0 WHERE operation_id=?', (child['operation_id'],))
    parent = await advance(w, parent, rounds=8)
    assert parent['status'] == 'succeeded', parent
    assert parent['result']['outcome'] == 'deployed'
    assert parent['result']['delivery']['deployment']['verified'] is True
    assert parent['result']['capacity_held'] and parent['result']['release_publication'] == 'not_inferred'
    assert w['gh'].count('PUT', 'merge-async') == w['gh'].count('POST', 'dispatches') == 1
    assert pm_lifecycle_gates.head(ops.db, w['workspace_id'])['stage'] == 'development'
