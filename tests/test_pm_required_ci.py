"""Native required checks reuse the bounded immutable two-pass observer."""
import copy
import hashlib
import json
from urllib.parse import unquote, urlencode

import pytest

from bat_agent_connector import pm_github_sources as source
from bat_agent_connector import pm_required_ci as required
from bat_agent_connector.operations import OperationError
from tests import test_pm_github_observation as base
from tests import test_pm_workload as workloads

base_ops = base.base_ops
release_ops = base.release_ops
workload_ops = base.workload_ops
github_ops = base.ops


class Provider(base.ReadProvider):
    def __init__(self, cfg):
        super().__init__(cfg)
        base.populate(self)
        self.pulls[0]['base']['ref'] = 'main'
        self.checks[0].update(name='required-test', app={'id': 10})
        self.statuses = []
        self.rules = [{'ruleset_id': 91, 'ruleset_source_type': 'Organization', 'type': 'required_status_checks',
            'parameters': {'strict_required_status_checks_policy': True,
                'required_status_checks': [{'context': 'required-test', 'integration_id': 10}]}}]
        self.protection = {'required_status_checks': {'strict': True, 'contexts': ['required-test'],
            'checks': [{'context': 'required-test', 'app_id': 10}]}}
        self.failure = None

    async def bounded_get(self, path, *, query, max_bytes, timeout_s):
        custom = '/branches/' in path or '/pulls/' in path
        if not custom:
            return await super().bounded_get(path, query=query, max_bytes=max_bytes, timeout_s=timeout_s)
        self.reads.append((path, copy.deepcopy(query)))
        if self.mutate:
            await self.mutate(path, query)
        link = ''
        if '/rules/branches/' in path:
            start = (query['page'] - 1) * self.page_size
            payload = self.rules[start:start + self.page_size]
            if start + self.page_size < len(self.rules):
                link = '<' + self.cfg.api_url + path + '?' + urlencode({**query, 'page': query['page'] + 1}) + '>; rel="next"'
        elif path.endswith('/protection'):
            payload = self.protection
        elif '/branches/' in path:
            payload = {'name': unquote(path.rsplit('/', 1)[1]), 'commit': {'sha': self.pulls[0]['base']['sha']}, 'protected': True}
        else:
            payload = self.pulls[0]
        raw = json.dumps(payload).encode()
        return {'status': self.failure if self.failure and path.endswith('/protection') else 200,
            'payload': copy.deepcopy(payload), 'metadata': {'link': link, 'authenticated': True,
                'bytes': len(raw), 'body_sha256': hashlib.sha256(raw).hexdigest()}}


@pytest.fixture
def ops(github_ops):
    github_ops.context['github'] = Provider(github_ops.context['github_config'])
    return github_ops


async def prepared(ops, monkeypatch, *, configured=True):
    policy = workloads.settings()
    policy['observation']['github_profile'] = source.PROFILE
    if configured:
        policy['observation']['required_ci_profile'] = required.PROFILE
    monkeypatch.setattr(workloads, 'settings', lambda: copy.deepcopy(policy))
    return await workloads.prepared(ops)


async def collect(ops, monkeypatch):
    data = await prepared(ops, monkeypatch)
    _, document = await base.observe(ops, data)
    return document


async def test_complete_native_app_context_two_pass_profile(ops, monkeypatch):
    doc = await collect(ops, monkeypatch)
    assert doc['evidence']['status'] == 'complete', doc['evidence']
    proof = doc['evidence']['facts']['required_ci']
    assert proof['profile'] == required.PROFILE and proof['status'] == 'complete'
    selected = proof['pulls'][0]
    assert selected['native_pr_id'] == 71 and selected['head_sha'] == base.SHA and selected['status'] == 'passed'
    assert selected['required'][0]['app_id'] == 10 and selected['required'][0]['check_ids'] == [73]
    assert 'required-test' not in json.dumps(doc)
    assert len([p for p, _ in ops.context['github'].reads if '/rules/branches/' in p]) == 2


@pytest.mark.parametrize('change', ['app', 'name', 'pending', 'failure', 'empty'])
async def test_arbitrary_passed_checks_do_not_prove_native_required_set(ops, monkeypatch, change):
    gh = ops.context['github']
    if change == 'app':
        gh.checks[0]['app']['id'] = 11
    elif change == 'name':
        gh.checks[0]['name'] = 'unrelated-test'
    elif change == 'pending':
        gh.checks[0].update(status='in_progress', conclusion=None)
    elif change == 'failure':
        gh.checks[0]['conclusion'] = 'failure'
    else:
        gh.checks = []
    doc = await collect(ops, monkeypatch)
    assert doc['evidence']['status'] == 'complete'
    assert doc['evidence']['facts']['required_ci']['pulls'][0]['status'] == 'blocked'


@pytest.mark.parametrize('failure', [403, 404, 500])
async def test_legacy_permission_or_missing_resource_never_means_no_required_checks(ops, monkeypatch, failure):
    ops.context['github'].failure = failure
    doc = await collect(ops, monkeypatch)
    assert doc['evidence']['status'] == 'incomplete'
    assert doc['evidence']['reason'].startswith('provider_')


@pytest.mark.parametrize('rule', ['merge_queue', 'workflows', 'new_unknown_rule'])
async def test_unsupported_native_rule_is_explicitly_incomplete(ops, monkeypatch, rule):
    ops.context['github'].rules.append({'ruleset_id': 92, 'ruleset_source_type': 'Repository', 'type': rule})
    doc = await collect(ops, monkeypatch)
    assert doc['evidence']['reason'] == 'required_ci_unsupported_branch_rule'


async def test_rules_and_head_changes_between_passes_are_not_a_proof(ops, monkeypatch):
    gh = ops.context['github']
    visits = []
    async def mutate(path, query):
        if '/rules/branches/' in path:
            visits.append(path)
            if len(visits) == 2:
                gh.rules[0]['parameters']['required_status_checks'][0]['integration_id'] = 11
    gh.mutate = mutate
    doc = await collect(ops, monkeypatch)
    assert doc['evidence']['reason'] == 'provider_changed_between_traversals'


async def test_unconfigured_policy_bytes_and_old_observation_shape_are_preserved(ops, monkeypatch):
    data = await prepared(ops, monkeypatch, configured=False)
    _, doc = await base.observe(ops, data)
    assert 'required_ci' not in doc['evidence']['facts']
    assert 'required_ci_profile' not in doc['intent']['observation'] if 'intent' in doc else True
    assert not any('/rules/branches/' in p or '/protection' in p for p, _ in ops.context['github'].reads)


async def test_legacy_context_without_precise_app_binding_is_incomplete(ops, monkeypatch):
    del ops.context['github'].protection['required_status_checks']['checks']
    doc = await collect(ops, monkeypatch)
    assert doc['evidence']['status'] == 'incomplete'


async def test_explicit_empty_required_set_is_not_a_passing_check(ops, monkeypatch):
    gh = ops.context['github']
    gh.rules = []
    gh.protection = {'required_status_checks': None}
    doc = await collect(ops, monkeypatch)
    selected = doc['evidence']['facts']['required_ci']['pulls'][0]
    assert selected['status'] == 'blocked' and selected['reason'] == 'required_checks_not_configured'


async def test_required_profile_requires_explicit_base_github_profile(ops, monkeypatch):
    policy = workloads.settings()
    policy['observation']['required_ci_profile'] = required.PROFILE
    monkeypatch.setattr(workloads, 'settings', lambda: copy.deepcopy(policy))
    with pytest.raises(OperationError):
        await workloads.prepared(ops)


def test_native_status_proof_uses_latest_fixed_context_and_rejects_missing_app():
    wanted = [{'context_digest': 'same', 'app_id': None}]
    statuses = [{'id': 1, 'context_digest': 'same', 'state': 'failure', 'created_at': 1},
        {'id': 2, 'context_digest': 'same', 'state': 'success', 'created_at': 2}]
    assert required.evaluate(wanted, [], statuses)['status'] == 'passed'
    assert required.evaluate([{**wanted[0], 'app_id': 10}], [], statuses)['status'] == 'blocked'
