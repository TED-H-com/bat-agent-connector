"""The original owned publication and explicit native required-CI selector travel together."""
import copy
import hashlib
import json
import uuid

import pytest

from bat_agent_connector import (
    delivery,
    pm_delivery_lineage,
    pm_github_observation,
    pm_source_lineage,
    pr_delivery,
)
from bat_agent_connector import pm_maintenance_repair_evidence as evidence
from bat_agent_connector import pm_maintenance_repairs as repairs
from bat_agent_connector import pm_required_ci as required
from bat_agent_connector.operations import OperationError
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
from tests.test_pm_maintenance_repair_receipts import record
from tests.test_pm_required_ci import Provider

publishing = publishing_fixture


@pytest.fixture(autouse=True)
def configured_required_ci(monkeypatch):
    original = setup.settings
    def settings():
        result = copy.deepcopy(original())
        result['observation']['required_ci_profile'] = required.PROFILE
        return result
    monkeypatch.setattr(setup, 'settings', settings)
    # The existing repair fixture sets its ceiling explicitly. Adopt a merge
    # ceiling in the real policy constructor, before that policy is stored.
    original_execute = setup.execute
    async def adopting(ops, envelope, **kwargs):
        if envelope['action'] == setup.policy.ACTION:
            envelope = copy.deepcopy(envelope)
            envelope['params']['policy']['delivery']['maximum'] = 'merge'
        return await original_execute(ops, envelope, **kwargs)
    monkeypatch.setattr(setup, 'execute', adopting)


class CIProvider(Provider):
    def __init__(self, cfg, w, receipt):
        super().__init__(cfg)
        self.repo.update(id=receipt['repository_id'], full_name=receipt['repository'])
        self.pulls = [copy.deepcopy(w['gh'].pulls[receipt['pull_number']])]
        self.suites[0]['head_sha'] = self.checks[0]['head_sha'] = receipt['candidate_sha']
        self.runs = []
        self.issues = w['issues']

    async def bounded_get(self, path, **kwargs):
        if path.endswith('/issues'):
            payload = copy.deepcopy(self.issues)
            raw = json.dumps(payload).encode()
            return {'status': 200, 'payload': payload, 'metadata': {'link': '', 'authenticated': True,
                'bytes': len(raw), 'body_sha256': hashlib.sha256(raw).hexdigest()}}
        return await super().bounded_get(path, **kwargs)


async def observe(w):
    ops = w['d'].ops
    env = setup.shared_request(w, pm_github_observation.ACTION)
    op = await execute(ops, env, who=OWNER)
    row = pm_github_observation.get_observation(ops, OWNER, w['workspace_id'], op['result']['observation_id'])['observation']
    assert row['document']['evidence']['status'] == 'complete', row['document']['evidence']
    return {'observation_id': row['observation_id'], 'document_digest': row['document_digest'], 'native_pr_id': w['creation']['native_pr_id']}


@pytest.fixture
async def published(publishing, monkeypatch):
    w, ops = publishing, publishing['d'].ops
    w['parent'] = await advance(w, w['parent'], rounds=5)
    w['creation'], _ = evidence.owned_creation(ops, OWNER, w['parent'])
    receipt = w['creation']
    w['gh'].before_request = None
    pr = w['gh'].add_pr(receipt['pull_number'], receipt['candidate_sha'], head_ref=receipt['branch_ref'],
        id=receipt['native_pr_id'], node_id=receipt['native_pr_node_id'])
    pr['base']['sha'] = receipt['base_sha']
    w['gh'].branches['main'] = receipt['base_sha']
    sync_provider_graph(w)
    comparison = w['gh'].comparison
    paths = git(w['remote'], 'diff', '--name-only', receipt['base_sha'], receipt['candidate_sha']).splitlines()
    def complete_diff(base, head, page):
        result = comparison(base, head, page)
        if result is not None and (base, head) == (receipt['base_sha'], receipt['candidate_sha']):
            result['files'] = [{'filename': path, 'status': 'added'} for path in paths]
        return result
    monkeypatch.setattr(w['gh'], 'comparison', complete_diff)
    w['ci_provider'] = CIProvider(ops.context['github'].cfg, w, receipt)
    monkeypatch.setattr(ops.context['github'], 'bounded_get', w['ci_provider'].bounded_get)
    w['ci_ref'] = await observe(w)
    w['merge_preview'] = (await delivery.pr_preview(ops, 'o/r', receipt['pull_number']))['merge_preview']
    assert not w['merge_preview']['blocking'], w['merge_preview']
    return w


async def select(w, ref):
    await execute(w['d'].ops, record(w, w['parent'], {'kind': 'required_ci', 'reference': ref},
        version=view(w, w['parent'])['version']), who=OWNER)


def merge_request(w):
    return {**pr_delivery.merge_envelope(w['merge_preview']), 'idempotency_key': uuid.uuid4().hex}


async def test_exact_selected_native_required_ci_frozen_at_merge_admission(published):
    w, ops = published, published['d'].ops
    with pytest.raises(OperationError, match='PM_REPAIR_REQUIRED_CI_UNPROVEN'):
        ops.create(OWNER, **merge_request(w))
    await select(w, w['ci_ref'])
    op = ops.create(OWNER, **merge_request(w))[0]
    selected = op['external_refs']['admission_binding']['pm_delivery']['pm_repair_required_ci']
    assert selected == {w['parent']['operation_id']: w['ci_ref']}
    assert w['parent']['operation_id'] in pm_source_lineage.roots(ops.db, 'operation', op['operation_id'])
    evidence.require_delivery(ops, OWNER, w['parent'], op)
    assert view(w, w['parent'])['progress']['required_ci_receipts'][0]['reference'] == w['ci_ref']


async def test_newer_passing_observation_never_replaces_frozen_merge_evidence(published):
    w, ops = published, published['d'].ops
    await select(w, w['ci_ref'])
    op = ops.create(OWNER, **merge_request(w))[0]
    newer = await observe(w)
    assert newer != w['ci_ref']
    await select(w, newer)
    with pytest.raises(OperationError, match='PM_REPAIR_REQUIRED_CI_UNPROVEN'):
        evidence.require_delivery(ops, OWNER, w['parent'], op)
    assert op['external_refs']['admission_binding']['pm_delivery']['pm_repair_required_ci'][w['parent']['operation_id']] == w['ci_ref']
    fresh = ops.create(OWNER, **merge_request(w))[0]
    assert fresh['external_refs']['admission_binding']['pm_delivery']['pm_repair_required_ci'][w['parent']['operation_id']] == newer


async def test_expired_original_proof_remains_readable_but_never_admits_new_effect(published):
    w, ops = published, published['d'].ops
    await select(w, w['ci_ref'])
    op = ops.create(OWNER, **merge_request(w))[0]
    saved = required.historical(ops, OWNER, w['parent'], w['creation'], w['ci_ref'])
    w['clock'][0] = saved['expires_at']
    assert required.historical(ops, OWNER, w['parent'], w['creation'], w['ci_ref']) == saved
    with pytest.raises(OperationError, match='PM_REPAIR_REQUIRED_CI_UNPROVEN'):
        evidence.require_delivery(ops, OWNER, w['parent'], op)
    assert view(w, w['parent'])['progress']['required_ci_receipts'][0]['reference'] == w['ci_ref']


@pytest.mark.parametrize('change', ['native', 'digest', 'failed', 'incomplete'])
async def test_wrong_or_incomplete_native_evidence_cannot_authorize_merge(published, change):
    w, ops = published, published['d'].ops
    ref = copy.deepcopy(w['ci_ref'])
    if change == 'native':
        ref['native_pr_id'] += 1
    elif change == 'digest':
        ref['document_digest'] = 'a' * 64
    elif change == 'failed':
        w['ci_provider'].checks[0]['conclusion'] = 'failure'
        ref = await observe(w)
    else:
        w['ci_provider'].failure = 404
        env = setup.shared_request(w, pm_github_observation.ACTION)
        done = await execute(ops, env, who=OWNER)
        row = pm_github_observation.get_observation(ops, OWNER, w['workspace_id'], done['result']['observation_id'])['observation']
        ref = {'observation_id': row['observation_id'], 'document_digest': row['document_digest'], 'native_pr_id': ref['native_pr_id']}
    if change == 'failed':
        await select(w, ref)  # An original blocked result remains useful history.
        with pytest.raises(OperationError, match='PM_REPAIR_REQUIRED_CI_UNPROVEN'):
            ops.create(OWNER, **merge_request(w))
    else:
        with pytest.raises(OperationError, match='PM_REPAIR_REQUIRED_CI_UNPROVEN'):
            await select(w, ref)


async def test_original_observation_source_access_gates_saved_delivery_proof(published, monkeypatch):
    w, ops = published, published['d'].ops
    await select(w, w['ci_ref'])
    original = pm_github_observation._access
    def withdrawn(ops, principal, wid, row):
        if row['observation_id'] == w['ci_ref']['observation_id']:
            raise OperationError('NOT_FOUND', 'Original required CI source withdrawn', 404)
        return original(ops, principal, wid, row)
    monkeypatch.setattr(pm_github_observation, '_access', withdrawn)
    assert not repairs.operation_visible(ops.db, OWNER, w['parent'])
    with pytest.raises(OperationError):
        required.historical(ops, OWNER, w['parent'], w['creation'], w['ci_ref'])
    assert pm_delivery_lineage.CONTRACT == 'pm-engineering-delivery/v1'
