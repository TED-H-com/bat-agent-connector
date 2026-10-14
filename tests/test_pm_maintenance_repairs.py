"""Fixed maintenance recipes through real disk Git and fake BAT/GitHub only."""
from __future__ import annotations

import copy
import datetime as dt
import json
import re
import sqlite3
import time
import uuid
from types import SimpleNamespace

import pytest

from bat_agent_connector import api_auth, pm_lifecycle_gates, pm_source_lineage
from bat_agent_connector import pm_github_observation as github
from bat_agent_connector import pm_github_sources as github_sources
from bat_agent_connector import pm_maintenance_checks as checks
from bat_agent_connector import pm_maintenance_policy as policy
from bat_agent_connector import pm_maintenance_repair_evidence as evidence
from bat_agent_connector import pm_maintenance_repair_rules as rules
from bat_agent_connector import pm_maintenance_repairs as repairs
from bat_agent_connector import pm_release_baselines as baselines
from bat_agent_connector import pm_work_batch_evidence as verifier
from bat_agent_connector import pm_workload as workload
from bat_agent_connector import pm_workload_sources as workload_sources
from bat_agent_connector.operations import OperationError
from tests.operation_helpers import settle_operations
from tests.test_checkpoints import snapshot
from tests.test_pm_lifecycle import decide
from tests.test_pm_package_handoff import fixed as fixed_fixture
from tests.test_pm_package_handoff import pm, ready, revoke, world  # noqa: F401
from tests.test_pm_package_handoff import request as handoff_request
from tests.test_pm_plan_operations import OWNER
from tests.test_pm_release_baselines import request as baseline_request
from tests.test_pm_workload import settings

fixed = fixed_fixture
TEST_COMMAND_ID = 'pytest'


async def execute(ops, envelope, *, who):
    op = ops.create(who, **envelope)[0]
    await ops._execute(op['operation_id'])
    op = ops.get(op['operation_id'])
    assert op['status'] == 'succeeded', (op['error_code'], op.get('reason'), [(step['name'], step.get('error')) for step in op['steps']])
    return op


def inventory(w):
    ops, now = w['d'].ops, w['clock'][0]
    hc = ops.context['fleet'].config.hosts['h1']
    digest = workload_sources.digest([hc.url, hc.fingerprint, hc.profile_id])
    discovery = {'binding_version': digest, 'finished_at': dt.datetime.fromtimestamp(now - 1, dt.timezone.utc).isoformat(),
        'status': 'succeeded', 'complete_enumeration': True, 'authority': {'verified': True},
        'coverage': {'workspace_ids': ['ws-1']}}
    with ops.journal.tx():
        ops.db.execute('INSERT OR REPLACE INTO hosts_observed(host,reachable,last_attempt_at,last_success_at) VALUES(?,?,?,?)', ('h1', True, now - 1, now - 1))
        ops.db.execute('INSERT OR REPLACE INTO discovery_latest VALUES(?,?,?,?)', ('h1', hc.profile_id, digest, json.dumps(discovery)))
        # The real temporary fixture has no human BAT session; Git remains dirty.
        ops.db.execute('DELETE FROM sessions_observed')


def shared_request(w, action):
    ops, wid = w['d'].ops, w['workspace_id']
    active = policy.active_record(ops, OWNER, wid)
    return {'action': action, 'target': {'workspace_id': wid}, 'params': {'policy_id': active['policy_id']},
        'preconditions': {'expected_policy_version': active['version'], 'expected_baseline_id': active['baseline_id'],
            'expected_lifecycle_revision': pm_lifecycle_gates.head(ops.db, wid)['revision']}, 'idempotency_key': uuid.uuid4().hex}


async def refresh(w):
    ops = w['d'].ops
    inventory(w)
    obs = await execute(ops, shared_request(w, github.ACTION), who=OWNER)
    w['observation'] = github.get_observation(ops, OWNER, w['workspace_id'], obs['result']['observation_id'])['observation']
    assert w['observation']['document']['evidence']['status'] == 'complete', json.dumps(w['observation']['document']['evidence'])
    capture = await execute(ops, shared_request(w, workload.ACTION), who=OWNER)
    document = workload.get_snapshot(ops, OWNER, w['workspace_id'], capture['result']['snapshot_id'])['snapshot']['document']
    assert document['classification'] == 'idle_candidate', document['blocking_reasons']


@pytest.fixture
async def repair_ready(fixed, monkeypatch):
    w, ops = fixed, fixed['d'].ops
    repairs.install(ops)
    clock = [time.time()]
    w['clock'] = clock
    for module in (api_auth, repairs, evidence, policy, workload, github, checks, github_sources):
        monkeypatch.setattr(module, 'time', SimpleNamespace(time=lambda: clock[0], monotonic=time.monotonic))
    sha = w['package']['document']['code_source']['source_sha']
    old_provider = w['gh'].before_request
    w['issues'] = [{'id': 501, 'number': 50, 'state': 'open', 'labels': [{'name': 'bug'}, {'name': 'release-1.4'}]}]
    def provider(method, path, body):
        old_provider(method, path, body)
        parsed = path.split('?', 1)[0]
        responses = {'/repos/o/r/releases/41': {'id': 41, 'tag_name': 'v1.4.0', 'draft': False, 'prerelease': False,
            'published_at': '2026-01-01T00:00:00Z', 'target_commitish': 'main'},
            '/repos/o/r/git/ref/tags/v1.4.0': {'ref': 'refs/tags/v1.4.0', 'object': {'type': 'commit', 'sha': sha}},
            '/repos/o/r/git/commits/' + sha: {'sha': sha},
            '/repos/o/r/compare/' + sha + '...' + sha: {'base_commit': {'sha': sha}, 'merge_base_commit': {'sha': sha}, 'status': 'identical'}, '/repos/o/r/issues': w['issues'],
            '/repos/o/r/pulls': [], '/repos/o/r/actions/runs': {'total_count': 0, 'workflow_runs': []}}
        if method == 'GET' and parsed in responses:
            w['gh'].script.append(('GET', re.escape(path) + '$', 200, {}, copy.deepcopy(responses[parsed])))
    w['gh'].before_request = provider
    wid = w['workspace_id']
    bound = ops.db.execute('SELECT binding_id FROM pm_project_bindings WHERE workspace_id=? ORDER BY rowid DESC LIMIT 1', (wid,)).fetchone()[0]
    env = baseline_request(ops, {'workspace_id': wid, 'repository': 'o/r'}, bound, branch='main')
    env['preconditions']['expected_commit_sha'] = sha
    adopted = await execute(ops, env, who=OWNER)
    baseline = baselines._record(ops.db, wid, adopted['result']['baseline_id'])
    handoff = await handoff_request(w)
    rule = {'key': 'release_bug', 'work_kind': 'bug_fix', 'labels_all': ['bug', 'release-1.4'],
        'release': rules.release_predicate(baseline), 'paths': ['.'],
        'handoff': {key: handoff[key] for key in ('target', 'params', 'preconditions')},
        'verification': {'kind': 'fixed_revision_verification', 'profile': 'observed_runner_v1', 'command_digest': verifier.digest(TEST_COMMAND_ID)}}
    document = settings()
    document['allowed_work_kinds'] = ['bug_fix']
    document['delivery']['maximum'] = 'pull_request'
    document['observation'].update(stable_for_seconds=0, github_profile=policy.GITHUB_PROFILE,
        maintenance_sources={'issues': {'labels_all': ['bug']}, 'dependencies': None})
    document['repair_rules'] = [rule]
    epoch = pm_lifecycle_gates.head(ops.db, wid)['revision']
    w['policy_document'] = document
    adopted = await execute(ops, {'action': policy.ACTION, 'target': {'workspace_id': wid},
        'params': {'policy': document, 'rationale': 'Owner explicitly adopts only this fixed release repair'},
        'preconditions': {'expected_policy_revision': 0, 'expected_previous_policy_id': None,
            'expected_baseline_id': baseline['baseline_id'], 'expected_lifecycle_revision': epoch}, 'idempotency_key': uuid.uuid4().hex}, who=OWNER)
    w['policy_id'] = adopted['result']['policy_id']
    await decide(ops, wid, 'set_stage', params={'stage': 'development'}, who=OWNER)
    api_auth.issue(ops.db, OWNER.actor, sorted(OWNER.scopes))
    # Credential issuance and policy eligibility use the same controlled clock.
    clock[0] += 1
    await refresh(w)
    return w


def request(w):
    env = shared_request(w, repairs.ACTION)
    obs = w['observation']
    finding = obs['document']['evidence']['maintenance_sources']['issues']['findings'][0]
    env['params'].update(rule_key='release_bug', finding_ref={'observation_id': obs['observation_id'],
        'document_digest': obs['document_digest'], 'finding_id': finding['finding_id']})
    return env


async def advance(w, parent, rounds=3):
    ops = w['d'].ops
    for _ in range(rounds):
        ops.db.execute('UPDATE operations SET next_run_at=0 WHERE operation_id=?', (parent['operation_id'],))
        await settle_operations(ops)
    parent = ops.get(parent['operation_id'])
    assert parent['error_code'] != 'INTERNAL', parent
    return parent


def view(w, parent):
    return repairs.get_repair(w['d'].ops, OWNER, w['workspace_id'], 'pmrp_' + parent['operation_id'][3:])['repair']


async def test_real_dispatch_is_one_owned_child_and_not_a_verified_release(repair_ready, mock):
    w, ops = repair_ready, repair_ready['d'].ops
    before = snapshot(w['human'])
    env = request(w)
    parent = await advance(w, ops.create(OWNER, **env)[0])
    current = view(w, parent)
    assert parent['status'] == 'waiting_external', parent
    assert current['child']['status'] == 'succeeded', current
    assert current['progress']['blocked_reason'] == 'PM_REPAIR_VERIFICATION_REQUIRED'
    assert current['capacity_held'] and not current['repair_fixed']
    assert current['release_status'] == 'not_inferred'
    assert mock.channels().count('claude:start-session') == mock.channels().count('claude:send-message') == 1
    assert snapshot(w['human']) == before
    replay, fresh = ops.create(OWNER, **env)
    assert not fresh and replay['operation_id'] == parent['operation_id']
    with pytest.raises(OperationError, match='existing repair'):
        ops.create(OWNER, **request(w))
    assert repairs.tick(ops) == []
    with pytest.raises(sqlite3.IntegrityError, match='immutable'):
        ops.db.execute('DELETE FROM pm_maintenance_repair_children')
    assert repairs.current_repair(ops, OWNER, w['workspace_id'])['repair']['repair_id'] == current['repair_id']


async def test_production_step_hooks_check_repair_once_and_keep_zero_retry_budget(repair_ready, monkeypatch):
    w, ops = repair_ready, repair_ready['d'].ops
    effect, restart = repairs.require_new_effect, repairs.admit_restart
    effects, restarts = [], []

    def checked(service, operation):
        effects.append(operation['operation_id'])
        return effect(service, operation)

    def retried(service, oid, name, selected):
        restarts.append((oid, name, selected))
        return restart(service, oid, name, selected)

    monkeypatch.setattr(repairs, 'require_new_effect', checked)
    monkeypatch.setattr(repairs, 'admit_restart', retried)
    env = request(w)
    parent = ops.create(OWNER, **env)[0]
    oid = parent['operation_id']
    assert oid in pm_source_lineage.roots(ops.db, 'operation', oid)
    ops._step_start(oid, 'fixture.unsent', {})
    assert effects == [oid]
    with pytest.raises(OperationError) as caught:
        ops._step_restart(oid, 'fixture.unsent', {})
    assert caught.value.code == 'PM_REPAIR_RETRIES_EXHAUSTED'
    assert restarts == [(oid, 'fixture.unsent', {})]
    assert ops.create(OWNER, **env)[0]['operation_id'] == oid
    assert ops.get(oid)['steps'][0]['status'] == 'started'
    assert ops.db.execute('SELECT COUNT(*) FROM pm_maintenance_repair_retries').fetchone()[0] == 0
