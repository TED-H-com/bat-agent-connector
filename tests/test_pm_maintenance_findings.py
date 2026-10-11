"""Configured sources use the original bounded reader, never real GitHub or models."""
import copy
import hashlib
import json
from types import SimpleNamespace
from urllib.parse import urlencode

import pytest

from bat_agent_connector import pm_github_observation as observation
from bat_agent_connector import pm_github_sources as provider
from bat_agent_connector import pm_maintenance_findings as findings
from bat_agent_connector import pm_maintenance_policy as policy
from bat_agent_connector.operations import OpContext, OperationError
from tests import test_pm_github_observation as githubtests
from tests import test_pm_workload as worktests
from tests.test_pm_evidence import OWNER
from tests.test_pm_release_baselines import remove_repo

base_ops = githubtests.base_ops
release_ops = githubtests.release_ops
workload_ops = githubtests.workload_ops
github_ops = githubtests.ops
NOW = githubtests.NOW


def configuration():
    return {'issues': {'labels_all': ['bug']}, 'dependencies': {'manifests': ['package.json'],
        'base_branch': 'support/1.x', 'dependabot_actor_id': 900, 'alerts': True, 'pull_requests': True}}


class Provider(githubtests.ReadProvider):
    def __init__(self, cfg):
        super().__init__(cfg)
        self.repo['default_branch'] = 'main'
        self.issues, self.alerts, self.files = [], [], []
        self.failure = None
        self.changed_files = 0
        self.cursor_link = None
        self.branch_sha = 'f' * 40

    async def bounded_get(self, path, *, query, max_bytes, timeout_s):
        custom = (path.endswith(('/issues', '/dependabot/alerts', '/files')) or '/git/ref/' in path
                  or '/pulls/' in path or path.endswith('/pulls') and 'base' in query)
        if not custom:
            return await super().bounded_get(path, query=query, max_bytes=max_bytes, timeout_s=timeout_s)
        self.reads.append((path, copy.deepcopy(query)))
        if self.mutate:
            await self.mutate(path, query)
        link, items = '', None
        if '/git/ref/' in path:
            data = {'ref': 'refs/heads/main', 'object': {'type': 'commit', 'sha': self.branch_sha}}
        elif path.endswith('/issues'):
            items = self.issues
        elif path.endswith('/dependabot/alerts'):
            items = self.alerts
        elif path.endswith('/files'):
            items = self.files
        elif path.endswith('/pulls'):
            items = self.pulls
        else:
            data = {**self.pulls[0], 'changed_files': self.changed_files}
        if items is not None:
            page = int(query.get('after', query.get('page', 1)))
            start = (page - 1) * self.page_size
            data = items[start:start + self.page_size]
            if start + self.page_size < len(items):
                next_query = {**query, ('after' if path.endswith('/dependabot/alerts') else 'page'): page + 1}
                link = '<' + self.cfg.api_url + path + '?' + urlencode(next_query) + '>; rel="next"'
            if path.endswith('/dependabot/alerts') and self.cursor_link:
                link = self.cursor_link
        raw = json.dumps(data).encode()
        return {'status': self.failure if self.failure and path.endswith('/dependabot/alerts') else 200,
            'payload': copy.deepcopy(data), 'metadata': {'link': link, 'bytes': len(raw),
            'body_sha256': hashlib.sha256(raw).hexdigest(), 'authenticated': True}}


@pytest.fixture
def ops(github_ops):
    github_ops.context['github'] = Provider(github_ops.context['github_config'])
    return github_ops


async def prepared(ops, monkeypatch, config=None):
    original = worktests.settings()
    original['sources'].append('dependencies')
    original['observation'].update(github_profile=provider.PROFILE,
        maintenance_sources=configuration() if config is None else config)
    monkeypatch.setattr(worktests, 'settings', lambda: copy.deepcopy(original))
    return await worktests.prepared(ops)


def populate(gh):
    gh.issues = [{'id': 501, 'number': 50, 'state': 'open', 'labels': [{'name': 'bug'}], 'body': 'PRIVATE INSTRUCTION'}]
    githubtests.populate(gh)
    gh.pulls[0].update(user={'id': 900, 'type': 'Bot'})
    gh.pulls[0]['base']['ref'] = 'support/1.x'
    gh.files = [{'filename': 'package.json', 'patch': 'PRIVATE PATCH'}]
    gh.changed_files = 1
    gh.alerts = [{'number': 9, 'state': 'open',
        'dependency': {'manifest_path': 'package.json', 'package': {'ecosystem': 'npm', 'name': 'example'}},
        'security_advisory': {'ghsa_id': 'GHSA-aaaa-bbbb-cccc', 'summary': 'PRIVATE ADVISORY'},
        'security_vulnerability': {'package': {'ecosystem': 'npm', 'name': 'example'}, 'severity': 'high',
            'vulnerable_version_range': '< 2.0.0', 'first_patched_version': {'identifier': '2.0.0'}}}]


async def test_configured_empty_means_endpoint_empty_not_scanned_or_released_healthy(ops, monkeypatch):
    data = await prepared(ops, monkeypatch)
    _, doc = await githubtests.observe(ops, data)
    assert doc['evidence']['status'] == 'complete'
    sources = doc['evidence']['maintenance_sources']
    assert all(s['status'] == 'complete' and s['result'] == 'empty' for s in sources.values())
    alerts = sources['dependabot_alerts']['coverage']
    assert alerts['branch'] == 'main' and alerts['scanner_head'] == 'unknown'
    assert alerts['manifest_scan_completeness'] == alerts['release_applicability'] == 'unknown'
    assert not any(op[0] != 'pm.github.observe' for op in ops.db.execute(
        "SELECT action FROM operations WHERE action LIKE 'pm.github.%'").fetchall())


async def test_fixed_findings_match_exact_sources_retain_native_ids_and_no_prose(ops, monkeypatch):
    data = await prepared(ops, monkeypatch)
    gh = ops.context['github']
    populate(gh)
    gh.issues.extend([{**gh.issues[0], 'id': 502, 'number': 51, 'pull_request': {}},
                      {**gh.issues[0], 'id': 503, 'number': 52, 'labels': [{'name': 'feature'}]}])
    _, doc = await githubtests.observe(ops, data)
    assert doc['evidence']['status'] == 'complete'
    sources = doc['evidence']['maintenance_sources']
    issue, pull, alert = [sources[k]['findings'][0] for k in findings.KINDS]
    assert len(sources['issues']['findings']) == 1 and issue['native']['id'] == 501
    assert pull['existing_pull_request'] == {'id': 71, 'number': 7, 'head_sha': githubtests.SHA}
    assert alert['facts']['patch_available'] and alert['existing_pull_request'] is None
    assert alert['release_applicability'] == 'unknown' and not alert['facts']['release_fixed']
    assert 'PRIVATE' not in json.dumps(doc)
    _, next_doc = await githubtests.observe(ops, data)
    assert [sources[k]['findings'][0]['finding_id'] for k in findings.KINDS] == [
        next_doc['evidence']['maintenance_sources'][k]['findings'][0]['finding_id'] for k in findings.KINDS]
    assert all(v['baseline_id'] == issue['baseline_id'] for v in (pull, alert))


@pytest.mark.parametrize('variant', ['actor', 'type', 'manifest'])
async def test_nonmatching_pull_is_not_own_repair(ops, monkeypatch, variant):
    data = await prepared(ops, monkeypatch)
    gh = ops.context['github']
    populate(gh)
    if variant == 'actor':
        gh.pulls[0]['user']['id'] = 901
    elif variant == 'type':
        gh.pulls[0]['user']['type'] = 'User'
    else:
        gh.files[0]['filename'] = 'other.json'
    _, doc = await githubtests.observe(ops, data)
    assert doc['evidence']['maintenance_sources']['dependabot_pull_requests']['result'] == 'empty'
    assert len(doc['evidence']['maintenance_sources']['dependabot_alerts']['findings']) == 1


@pytest.mark.parametrize('status', [403, 404, 429, 500])
async def test_unavailable_alerts_are_incomplete_but_keep_issue_and_pull_findings(ops, monkeypatch, status):
    data = await prepared(ops, monkeypatch)
    gh = ops.context['github']
    populate(gh)
    gh.failure = status
    _, doc = await githubtests.observe(ops, data)
    assert doc['evidence']['status'] == 'incomplete'
    sources = doc['evidence']['maintenance_sources']
    assert sources['dependabot_alerts']['status'] == 'incomplete'
    assert sources['dependabot_alerts']['result'] is None
    assert sources['issues']['result'] == sources['dependabot_pull_requests']['result'] == 'matched'


async def test_cursor_pagination_uses_same_bounds_and_deduplicates_alert_numbers(ops, monkeypatch):
    data = await prepared(ops, monkeypatch)
    gh = ops.context['github']
    populate(gh)
    gh.alerts.append({**gh.alerts[0], 'number': 10})
    gh.page_size = 1
    _, doc = await githubtests.observe(ops, data)
    assert doc['evidence']['status'] == 'complete'
    assert len(doc['evidence']['maintenance_sources']['dependabot_alerts']['findings']) == 2
    assert any(query.get('after') == '2' for path, query in gh.reads if path.endswith('/alerts'))
    gh.alerts[1]['number'] = 9
    _, doc = await githubtests.observe(ops, data)
    assert doc['evidence']['maintenance_sources']['dependabot_alerts']['reason'] == 'provider_duplicate_row'


@pytest.mark.parametrize('variant', ['origin', 'filter', 'cycle', 'full', 'file_count', 'branch'])
async def test_unproven_or_changing_coverage_cannot_report_empty(ops, monkeypatch, variant):
    data = await prepared(ops, monkeypatch)
    gh = ops.context['github']
    populate(gh)
    if variant in ('origin', 'filter', 'cycle'):
        url = gh.cfg.api_url + '/repos/example/product/dependabot/alerts'
        params = {'per_page': 100, 'state': 'open', 'sort': 'created', 'direction': 'asc', 'after': 2}
        if variant == 'origin':
            url = 'https://evil.invalid/stolen'
        elif variant == 'filter':
            params['state'] = 'fixed'
        gh.cursor_link = '<' + url + '?' + urlencode(params) + '>; rel="next"'
    elif variant == 'full':
        gh.alerts = [{**gh.alerts[0], 'number': n + 1} for n in range(100)]
    elif variant == 'file_count':
        gh.changed_files = 3001
    else:
        async def change(path, query):
            if '/git/ref/' in path and sum('/git/ref/' in p for p, _ in gh.reads) > 1:
                gh.branch_sha = 'e' * 40
        gh.mutate = change
    _, doc = await githubtests.observe(ops, data)
    assert doc['evidence']['status'] == 'incomplete'
    assert any(source['status'] == 'incomplete' and source['result'] is None
               for source in doc['evidence']['maintenance_sources'].values())


async def test_changed_findings_between_passes_are_not_complete_and_disappearance_not_resolved(ops, monkeypatch):
    data = await prepared(ops, monkeypatch)
    gh = ops.context['github']
    populate(gh)
    _, original = await githubtests.observe(ops, data)
    reads = len(gh.reads)
    async def change(path, query):
        if path.endswith('/issues') and sum(p.endswith('/issues') for p, _ in gh.reads[reads:]) == 2:
            gh.issues.clear()
    gh.mutate = change
    _, doc = await githubtests.observe(ops, data)
    assert doc['evidence']['reason'] == 'provider_changed_between_traversals'
    assert not original['evidence']['maintenance_sources']['issues']['findings'][0]['resolved']


async def test_original_receipt_replay_after_source_withdrawal_has_no_new_get_and_hides_reads(ops, monkeypatch):
    data = await prepared(ops, monkeypatch)
    populate(ops.context['github'])
    op, _ = await githubtests.observe(ops, data)
    reads = len(ops.context['github'].reads)
    await remove_repo(ops, data['project_id'])
    assert await observation._run(OpContext(ops, op)) == op['result']
    assert len(ops.context['github'].reads) == reads
    assert not observation.operation_visible(ops.db, OWNER, op)
    with pytest.raises(OperationError):
        findings.current_findings(ops, OWNER, data['workspace_id'])


@pytest.mark.parametrize('variant', ['missing_profile', 'paths', 'branch', 'actor', 'extra', 'source'])
async def test_policy_rejects_unbound_configuration(ops, monkeypatch, variant):
    original = worktests.settings()
    original['sources'].append('dependencies')
    original['observation'].update(github_profile=provider.PROFILE, maintenance_sources=configuration())
    config = original['observation']['maintenance_sources']
    if variant == 'missing_profile':
        del original['observation']['github_profile']
    elif variant == 'paths':
        config['dependencies']['manifests'] = ['../package.json']
    elif variant == 'branch':
        config['dependencies']['base_branch'] = 'other'
    elif variant == 'actor':
        config['dependencies']['dependabot_actor_id'] = True
    elif variant == 'extra':
        config['issues']['url'] = 'https://evil.invalid'
    else:
        original['sources'].remove('dependencies')
    monkeypatch.setattr(worktests, 'settings', lambda: copy.deepcopy(original))
    with pytest.raises(OperationError):
        await worktests.prepared(ops)
    assert ops.context['github'].reads == []


async def test_optional_omission_and_explicit_disabled_sources_remain_distinct(ops, monkeypatch):
    data = await prepared(ops, monkeypatch, {'issues': None, 'dependencies': None})
    _, doc = await githubtests.observe(ops, data)
    assert all(s['status'] == 'disabled' for s in doc['evidence']['maintenance_sources'].values())
    assert doc['evidence']['pages'] == 16
    assert policy.active_record(ops, OWNER, data['workspace_id'])['observation']['maintenance_sources'] == {
        'issues': None, 'dependencies': None}


async def test_findings_read_status_is_current_but_record_is_immutable(ops, monkeypatch):
    data = await prepared(ops, monkeypatch)
    _, doc = await githubtests.observe(ops, data)
    # An unadopted lifecycle is intentionally not automatic scheduling authority.
    view = findings.current_findings(ops, OWNER, data['workspace_id'])
    assert all(s['status'] == 'stopped' for s in view['sources'].values())
    monkeypatch.setattr(observation, 'time', SimpleNamespace(time=lambda: NOW + 9999))
    assert all(s['status'] == 'complete' for s in doc['evidence']['maintenance_sources'].values())
    assert view['automatic_repair_authorized'] is False


async def test_active_fresh_stale_and_changed_policy_views_do_not_fall_back(ops, monkeypatch):
    from tests.test_pm_lifecycle import decide
    data = await prepared(ops, monkeypatch)
    await decide(ops, data['workspace_id'], 'set_stage', params={'stage': 'development'})
    op, doc = await githubtests.observe(ops, data)
    assert all(s['status'] == 'complete' for s in findings.current_findings(ops, OWNER, data['workspace_id'])['sources'].values())
    monkeypatch.setattr(observation, 'time', SimpleNamespace(time=lambda: NOW + 121))
    assert all(s['status'] == 'stale' for s in findings.current_findings(ops, OWNER, data['workspace_id'])['sources'].values())
    saved = observation.get_observation(ops, OWNER, data['workspace_id'], op['result']['observation_id'])['observation']['document']
    assert saved == doc
    await decide(ops, data['workspace_id'], 'pause')
    assert all(s['status'] == 'stopped' for s in findings.current_findings(ops, OWNER, data['workspace_id'])['sources'].values())
    await decide(ops, data['workspace_id'], 'reopen', params={'stage': 'development'})
    assert all(s['status'] == 'context_changed' for s in findings.current_findings(ops, OWNER, data['workspace_id'])['sources'].values())


async def test_pause_during_new_source_read_stops_publication_and_all_later_requests(ops, monkeypatch):
    from tests.test_pm_lifecycle import decide
    data = await prepared(ops, monkeypatch)
    gh = ops.context['github']
    populate(gh)
    seen = []
    async def pause(path, query):
        if path.endswith('/issues'):
            seen.append(len(gh.reads))
            await decide(ops, data['workspace_id'], 'pause')
    gh.mutate = pause
    op = await githubtests.execute(ops, githubtests.request(ops, data), success=False)
    assert op['status'] in ('failed', 'cancelled'), op
    assert len(gh.reads) == seen[0]
    assert ops.db.execute('SELECT COUNT(*) FROM pm_github_observations').fetchone()[0] == 0


async def test_exact_finding_lookup_rejects_digest_or_id_substitution(ops, monkeypatch):
    data = await prepared(ops, monkeypatch)
    populate(ops.context['github'])
    op, doc = await githubtests.observe(ops, data)
    item = doc['evidence']['maintenance_sources']['issues']['findings'][0]
    args = (ops, OWNER, data['workspace_id'], op['result']['observation_id'])
    selected = findings.get_finding(*args, op['result']['document_digest'], item['finding_id'])
    assert selected['finding'] == item and not selected['automatic_repair_authorized']
    with pytest.raises(OperationError, match='exact original observation digest'):
        findings.get_finding(*args, 'a' * 64, item['finding_id'])
    with pytest.raises(OperationError, match='Original finding unavailable'):
        findings.get_finding(*args, op['result']['document_digest'], 'pmfn_' + 'a' * 32)
