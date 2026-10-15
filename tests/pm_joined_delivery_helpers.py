"""Delivery phases on the same original fixture ledger and real bare Git remote."""
from __future__ import annotations

import copy
import json
import re
from urllib.parse import parse_qs, unquote, urlsplit

from bat_agent_connector import delivery, deployment, integration, pr_delivery, pr_publication
from bat_agent_connector import pm_github_observation as github
from bat_agent_connector import pm_lifecycle_gates as lifecycle
from bat_agent_connector import pm_maintenance_policy as policies
from bat_agent_connector import pm_release_baselines as baselines
from bat_agent_connector import pm_workload as workload
from bat_agent_connector.config import parse_config
from tests.fakeverifier import FakeVerifier
from tests.operation_helpers import settle_operations
from tests.pm_joined_lifecycle_helpers import OWNER, assert_manual_preserved, execute
from tests.test_checkpoints import git
from tests.test_integration import RaceRunner
from tests.test_pm_delivery_lineage import sync_provider_graph
from tests.test_pm_lifecycle import decide
from tests.test_pm_maintenance_repairs import shared_request
from tests.test_pm_workload import settings


def configure_delivery(w):
    """Provider fixtures answer actual bounded HTTP, including original POST 201."""
    raw = copy.deepcopy(w['raw'])
    raw['github']['repos'][0]['integrate'] = {'hosts': ['h1'], 'remote_url': str(w['remote']), 'workspace': 'ws-1'}
    raw['deploy'] = {'recipes': [{'name': 'prod', 'repository': 'o/r', 'environment': 'production',
        'mode': 'workflow_dispatch', 'workflow': 'deploy.yml', 'deploy_job': 'deploy', 'ref': 'main',
        'inputs': {'source_sha': 'source_sha', 'operation_id': 'operation_id'}, 'run_name_contains': 'operation_id',
        'verification': {'kind': 'http_json', 'url': 'https://deployment.example/status',
            'version_required': True, 'health_required': True}}]}
    ops, gh = w['d'].ops, w['gh']
    w['raw'] = raw
    ops.context['github_config'] = parse_config(raw).github
    ops.context['deployment_verifier'] = FakeVerifier(gh)
    w['runner'] = RaceRunner()
    ops.context['git_runner'] = w['runner']
    gh.branches['main'] = w['sha']
    sync_provider_graph(w)
    original_provider, original_merge = gh.before_request, gh.merge
    original_comparison = gh.comparison
    w['public_posts'] = []

    def comparison(base, head, page):
        value = original_comparison(base, head, page)
        if value is not None:
            value['base_commit'] = {'sha': gh.branches.get(base, base)}
        return value

    def provider(method, path, body):
        # FakeGitHub calls this hook with the decoded path after recording the
        # exact incoming URL. Preserve the real requested status/page filters.
        parsed = urlsplit(gh.requests[-1][1])
        assert parsed.path == path
        if method == 'GET' and parsed.path == '/repos/o/r/actions/runs':
            query = parse_qs(parsed.query)
            status = query.get('status', [None])[0]
            page, size = int(query.get('page', [1])[0]), int(query.get('per_page', [100])[0])
            runs = [{**copy.deepcopy(row), 'repository': {'id': gh.repository_id}}
                for row in gh.runs.values() if status is None or row['status'] == status]
            gh.script.append(('GET', re.escape(path) + '$', 200, {},
                {'total_count': len(runs), 'workflow_runs': runs[(page - 1) * size:page * size]}))
        elif method == 'POST' and path == '/repos/o/r/pulls':
            w['public_posts'].append(copy.deepcopy(body))
            number = len(w['public_posts'])
            pr = gh.add_pr(number, git(w['remote'], 'rev-parse', 'refs/heads/' + body['head']),
                head_ref=body['head'], id=900 + number, node_id='PR_joined_' + str(number),
                draft=body['draft'], title=body['title'], body=body['body'])
            pr['base'].update(ref=body['base'], sha=git(w['remote'], 'rev-parse', 'refs/heads/' + body['base']))
            gh.track_remote(number, str(w['remote']))
            gh.script.append(('POST', re.escape(path) + '$', 201, {}, copy.deepcopy(pr)))
        elif method == 'GET' and '/git/ref/heads/' in path:
            ref = unquote(path.split('/git/ref/heads/', 1)[1])
            sha = git(w['remote'], 'rev-parse', 'refs/heads/' + ref)
            gh.script.append(('GET', re.escape(path) + '$', 200, {},
                {'ref': 'refs/heads/' + ref, 'object': {'type': 'commit', 'sha': sha}}))
        elif original_provider:
            original_provider(method, path, body)

    def merge(number, sha=None):
        # The synthetic provider creates a real published Git result. It never
        # modifies the human checkout or invents an object absent from the remote.
        pr = gh.pulls[number]
        gh._live_head(pr)
        base, head = gh.branches['main'], pr['head']['sha']
        tree = git(w['remote'], 'rev-parse', head + '^{tree}')
        merged = git(w['remote'], '-c', 'user.name=Fixture provider', '-c', 'user.email=provider@example.invalid',
            'commit-tree', tree, '-p', base, '-p', head, '-m', 'Merge the exact original reviewed head')
        git(w['remote'], 'update-ref', 'refs/heads/main', merged, base)
        original_merge(number, merged)
        w['state']['sha'] = merged
        sync_provider_graph(w)

    gh.before_request, gh.merge, gh.comparison = provider, merge, comparison


async def settle(w, envelope, *, rounds=8):
    ops = w['d'].ops
    original = ops.create(OWNER, **envelope)[0]
    for _ in range(rounds):
        ops.db.execute('UPDATE operations SET next_run_at=0 WHERE operation_id=?', (original['operation_id'],))
        await settle_operations(ops)
        done = ops.get(original['operation_id'])
        assert done['error_code'] != 'INTERNAL', done
        if done['status'] in {'succeeded', 'failed', 'cancelled', 'needs_attention'}:
            return done
    return done


async def publish_release(w, mock):
    configure_delivery(w)
    ops = w['d'].ops
    login, recovery = w['implementations']
    pub = await settle(w, {'action': pr_publication.ACTION,
        'target': {'workspace_id': w['workspace_id'], 'repository': 'o/r', 'host': 'h1',
            'source_operation_id': login['child']['operation_id']},
        'params': {'candidate_sha': login['candidate'], 'base_ref': 'main', 'public_title': 'Original member login and recovery',
            'public_body': 'The explicitly reviewed original membership changes.', 'draft': False},
        'preconditions': {'repository_id': w['gh'].repository_id, 'expected_base_sha': w['sha']},
        'idempotency_key': 'joined-original-publication'})
    assert pub['status'] == 'succeeded', pub
    receipt = pr_publication.require_creation_receipt(ops, OWNER, pub['operation_id'],
        child_operation_id=login['child']['operation_id'], provider_origin=w['gh'].url,
        repository_id=w['gh'].repository_id)
    number = receipt['pull_number']
    sync_provider_graph(w)
    preview = await settle(w, {'action': 'integration.preview',
        'target': {'repository': 'o/r', 'host': 'h1', 'pull_number': number},
        'params': {'sources': [{'kind': 'execution', 'id': item['child']['operation_id']}
            for item in (login, recovery)]}, 'preconditions': {}, 'idempotency_key': 'joined-original-integration-preview'})
    assert preview['status'] == 'succeeded' and not preview['result']['blocking'], preview
    request = {**integration.apply_request(preview['result']), 'idempotency_key': 'joined-original-integration'}
    integrated = await settle(w, request)
    assert integrated['status'] == 'succeeded' and integrated['result']['pushed'], integrated
    sync_provider_graph(w)
    card = await delivery.pr_preview(ops, 'o/r', number)
    merged = await settle(w, {**pr_delivery.merge_envelope(card['merge_preview']),
        'idempotency_key': 'joined-original-merge'})
    assert merged['status'] == 'succeeded' and merged['result']['verified'], merged
    released_sha = merged['result']['merged_sha']
    assert git(w['remote'], 'rev-parse', 'refs/heads/main') == released_sha
    pv = await deployment.preview(ops, 'prod')
    request = {'action': 'deployment.start', 'target': {'recipe': 'prod'}, 'params': {'source_sha': released_sha},
        'preconditions': pv['preconditions'], 'idempotency_key': 'joined-original-deployment'}
    pending = await settle(w, request, rounds=2)
    assert pending['status'] == 'waiting_external', pending
    run_id = pending['external_refs']['deploy_run_id']
    w['gh'].runs[run_id].update(status='completed', conclusion='success')
    w['gh'].jobs[run_id][1]['conclusion'] = 'success'
    deployed = await settle(w, request)
    assert deployed['status'] == 'succeeded', deployed
    selected = await execute(w, baselines.ACTION, {'workspace_id': w['workspace_id'], 'repository': 'o/r'},
        {'project_binding_id': w['project_binding_id'],
            'source': {'kind': 'deployment', 'deployment_id': deployed['result']['deployment_id']},
            'channel': 'production', 'maintenance_branch': 'main',
            'known_limitations': ['Offline provider fixture; installed/live acceptance remains separate.'],
            'rationale': 'Maintain this exact verified release of the original member plan'},
        {'expected_baseline_version': 0, 'expected_previous_baseline_id': None,
            'expected_repository_id': w['gh'].repository_id, 'expected_commit_sha': released_sha})
    w['baseline'] = baselines.get_baseline(ops, OWNER, w['workspace_id'], selected['result']['baseline_id'])['baseline']
    w['ledger'].record('original_release', publication_operation_id=pub['operation_id'],
        integration_operation_id=integrated['operation_id'], merge_operation_id=merged['operation_id'],
        deployment_operation_id=deployed['operation_id'], baseline_id=w['baseline']['baseline_id'], released_sha=released_sha)
    assert_manual_preserved(w, mock)
    return w


async def observe_release_workload(w):
    """Real inventory capture retains every original human and owned session."""
    ops, wid = w['d'].ops, w['workspace_id']
    document = settings()
    document['observation'].update(stable_for_seconds=0, github_profile=policies.GITHUB_PROFILE)
    document['observation']['max_age_seconds'] = 1800
    await execute(w, policies.ACTION, {'workspace_id': wid},
        {'policy': document, 'rationale': 'Observe all work still committed to the original released project'},
        {'expected_policy_revision': 0, 'expected_previous_policy_id': None,
            'expected_baseline_id': w['baseline']['baseline_id'],
            'expected_lifecycle_revision': lifecycle.head(ops.db, wid)['revision']})
    await decide(ops, wid, 'set_stage', params={'stage': 'development'}, who=OWNER)
    await w['d'].inventory.refresh_host('h1')
    assert ops.db.execute('SELECT COUNT(*) FROM sessions_observed').fetchone()[0] >= 3
    observation = await settle(w, shared_request(w, github.ACTION))
    assert observation['status'] == 'succeeded', observation
    evidence = github.get_observation(ops, OWNER, wid, observation['result']['observation_id'])['observation']
    assert evidence['document']['evidence']['status'] == 'complete', json.dumps(evidence['document']['evidence'])
    captured = await settle(w, shared_request(w, workload.ACTION))
    assert captured['status'] == 'succeeded', captured
    return workload.get_snapshot(ops, OWNER, wid, captured['result']['snapshot_id'])['snapshot']['document']
