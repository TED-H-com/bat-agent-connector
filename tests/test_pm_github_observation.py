"""Offline profile enumeration, fixed receipts and late authority checks."""
from __future__ import annotations

import copy
import hashlib
import json
import sqlite3
from types import SimpleNamespace
from urllib.parse import urlencode

import pytest

from bat_agent_connector import pm_github_observation as observation
from bat_agent_connector import pm_github_sources as provider
from bat_agent_connector import pm_workload as workload
from bat_agent_connector.operations import OpContext, OperationError
from tests import test_pm_workload as worktests
from tests.test_pm_evidence import OWNER, READER
from tests.test_pm_release_baselines import Provider, execute, remove_repo

base_ops = worktests.base_ops
release_ops = worktests.release_ops
workload_ops = worktests.ops
NOW = worktests.NOW
SHA = 'd' * 40


class ReadProvider(Provider):
    def __init__(self, cfg):
        super().__init__(cfg)
        self.reads, self.mutate = [], None
        self.pulls, self.suites, self.checks, self.statuses, self.runs = [], [], [], [], []
        self.total_override, self.page_size = None, 100

    async def bounded_get(self, path, *, query, max_bytes, timeout_s):
        assert max_bytes <= provider.PAGE_BYTES and 0 < timeout_s <= 30
        self.reads.append((path, copy.deepcopy(query)))
        if self.mutate:
            await self.mutate(path, query)
        if not query:
            data = self.repo
            items = None
        elif path.endswith('/pulls'):
            items, field = self.pulls, None
        elif path.endswith('/check-suites'):
            items, field = self.suites, 'check_suites'
        elif path.endswith('/check-runs'):
            items, field = self.checks, 'check_runs'
        elif path.endswith('/statuses'):
            items, field = self.statuses, None
        elif path.endswith('/actions/runs'):
            items, field = [row for row in self.runs if row['status'] == query['status']], 'workflow_runs'
        else:
            raise AssertionError(path)
        link = ''
        if items is not None:
            start = (query['page'] - 1) * self.page_size
            rows = items[start:start + self.page_size]
            data = {field: rows, 'total_count': len(items) if self.total_override is None else self.total_override} if field else rows
            if start + self.page_size < len(items):
                link = '<' + self.cfg.api_url + path + '?' + urlencode({**query, 'page': query['page'] + 1}) + '>; rel="next"'
        raw = json.dumps(data).encode()
        return {'status': 200, 'payload': copy.deepcopy(data), 'metadata': {'link': link,
            'bytes': len(raw), 'body_sha256': hashlib.sha256(raw).hexdigest(), 'authenticated': True}}


@pytest.fixture
def ops(workload_ops, monkeypatch):
    workload_ops.context['github'] = ReadProvider(workload_ops.context['github_config'])
    observation.install(workload_ops)
    for module in (observation, provider):
        monkeypatch.setattr(module, 'time', SimpleNamespace(time=lambda: NOW, monotonic=lambda: NOW))
    return workload_ops


async def prepared(ops, monkeypatch):
    original = worktests.settings()
    original['observation']['github_profile'] = provider.PROFILE
    monkeypatch.setattr(worktests, 'settings', lambda: copy.deepcopy(original))
    return await worktests.prepared(ops)


def request(ops, data):
    value = worktests.capture_request(ops, data)
    value['action'] = observation.ACTION
    return value


async def observe(ops, data, **kwargs):
    op = await execute(ops, request(ops, data), **kwargs)
    record = observation.get_observation(ops, OWNER, data['workspace_id'], op['result']['observation_id'])['observation']
    return op, record['document']


def populate(gh):
    gh.pulls = [{'id': 71, 'number': 7, 'state': 'open', 'head': {'sha': SHA},
        'base': {'sha': 'a' * 40, 'repo': {'id': 4242}}, 'title': 'PRIVATE TITLE'}]
    gh.suites = [{'id': 72, 'head_sha': SHA, 'status': 'completed', 'conclusion': 'success'}]
    gh.checks = [{'id': 73, 'head_sha': SHA, 'status': 'completed', 'conclusion': 'success', 'check_suite': {'id': 72}}]
    gh.statuses = [{'id': 74, 'state': 'pending', 'context': 'PRIVATE CONTEXT', 'description': 'PRIVATE DESCRIPTION'}]
    gh.runs = [{'id': 75, 'head_sha': SHA, 'status': 'in_progress', 'conclusion': None,
        'repository': {'id': 4242}, 'run_attempt': 2, 'workflow_id': 9}]


async def test_explicit_profile_empty_proves_scoped_observation_without_starting_work(ops, monkeypatch):
    data = await prepared(ops, monkeypatch)
    before = ops.db.execute('PRAGMA user_version').fetchone()[0]
    observation.install(ops)
    op, doc = await observe(ops, data)
    assert doc['evidence']['status'] == 'complete' and doc['evidence']['traversals_completed'] == 2
    assert doc['evidence']['pages'] == 16
    assert doc['evidence']['facts'] == {'pulls': [], 'heads': {}, 'actions': []}
    assert doc['scope']['github_scope'] == provider.PROFILE
    assert doc['automatic_maintenance_authorized'] is False
    assert doc['evidence']['atomic_provider_snapshot'] is False
    assert doc['evidence']['outside_scope'] == provider.OUTSIDE_SCOPE
    assert observation.operation_visible(ops.db, READER, op)
    assert ops.db.execute('PRAGMA user_version').fetchone()[0] == before
    for table in ('pm_github_observations', 'pm_github_observation_pages'):
        with pytest.raises(sqlite3.IntegrityError, match='immutable'):
            ops.db.execute(f'DELETE FROM {table}')
    worktests.inventory(ops)
    _, captured = await worktests.capture(ops, data)
    assert captured['classification'] == 'idle_candidate'
    assert captured['stability']['satisfied'] is False
    assert not captured['blocking_reasons']
    assert ops.db.execute('SELECT COUNT(*) FROM tasks').fetchone()[0] == 0


async def test_legacy_repository_policy_is_not_implicitly_narrowed(ops):
    data = await worktests.prepared(ops)
    with pytest.raises(OperationError, match='explicit GitHub observation profile'):
        await observe(ops, data)
    assert ops.context['github'].reads == []
    _, captured = await worktests.capture(ops, data)
    assert 'github_repository_coverage_unavailable' in worktests.codes(captured)


async def test_full_object_and_list_pagination_fixed_heads_and_nonterminal_actions(ops, monkeypatch):
    data = await prepared(ops, monkeypatch)
    gh = ops.context['github']
    populate(gh)
    gh.page_size = 1
    gh.checks.append({**gh.checks[0], 'id': 79})
    gh.statuses.append({**gh.statuses[0], 'id': 80, 'state': 'success'})
    gh.runs.extend([{**gh.runs[0], 'id': 100 + n, 'status': status} for n, status in enumerate(provider.NONTERMINAL)])
    _, doc = await observe(ops, data)
    assert doc['evidence']['status'] == 'complete'
    facts = doc['evidence']['facts']
    assert len(facts['heads'][SHA]['checks']) == 2 and len(facts['heads'][SHA]['statuses']) == 2
    assert len(facts['actions']) == 6
    assert any(query.get('page') == 2 and path.endswith('/check-runs') for path, query in gh.reads)
    assert all(query.get('filter') == 'all' for path, query in gh.reads if path.endswith('/check-runs'))
    public = json.dumps(doc)
    assert 'PRIVATE' not in public and 'api.github.com' not in public
    worktests.inventory(ops)
    _, captured = await worktests.capture(ops, data)
    assert captured['classification'] == 'development'
    assert {'github_open_pull_requests', 'github_nonterminal_actions'} <= worktests.codes(captured)


@pytest.mark.parametrize('variant,reason', [
    ('head', 'provider_changed_between_traversals'), ('actions', 'provider_changed_between_traversals'),
    ('repository', 'provider_repository_changed'), ('check', 'provider_head_changed'),
    ('count', 'provider_count_incomplete'), ('duplicate', 'provider_duplicate_row'),
    ('filter_limit', 'provider_search_limit'), ('unknown_state', 'provider_status_unproven'),
    ('context', 'provider_status_context_unproven'),
])
async def test_races_and_incomplete_provider_data_are_never_complete(ops, monkeypatch, variant, reason):
    data = await prepared(ops, monkeypatch)
    gh = ops.context['github']
    populate(gh)
    if variant == 'repository':
        gh.repo['id'] = 999
    elif variant == 'check':
        gh.checks[0]['head_sha'] = 'e' * 40
    elif variant == 'count':
        gh.total_override = 8
    elif variant == 'duplicate':
        gh.pulls.append(copy.deepcopy(gh.pulls[0]))
    elif variant == 'filter_limit':
        gh.pulls = []
        gh.total_override = 1000
    elif variant == 'unknown_state':
        gh.suites[0]['status'] = 'novel_status'
    elif variant == 'context':
        gh.statuses[0]['context'] = None
    else:
        async def change(path, query):
            count = sum(p.endswith('/pulls') for p, _ in gh.reads)
            if count == 2 and path.endswith('/pulls'):
                if variant == 'head':
                    gh.pulls[0]['base']['sha'] = 'e' * 40
                else:
                    gh.runs[0]['run_attempt'] = 3
        gh.mutate = change
    _, doc = await observe(ops, data)
    assert doc['evidence']['status'] == 'incomplete' and doc['evidence']['reason'] == reason


@pytest.mark.parametrize('bound,value,reason', [
    ('MAX_PAGES', 2, 'observation_page_limit'), ('MAX_ROWS', 1, 'observation_row_limit'),
    ('MAX_BYTES', 10, 'provider_metadata_unproven'),
])
async def test_provider_budget_limits(ops, monkeypatch, bound, value, reason):
    data = await prepared(ops, monkeypatch)
    populate(ops.context['github'])
    monkeypatch.setattr(provider, bound, value)
    _, doc = await observe(ops, data)
    assert doc['evidence']['status'] == 'incomplete' and doc['evidence']['reason'] == reason


async def test_time_bound_does_not_begin_another_read(ops, monkeypatch):
    data = await prepared(ops, monkeypatch)
    gh = ops.context['github']
    async def expire(path, query):
        monkeypatch.setattr(provider, 'time', SimpleNamespace(time=lambda: NOW + 61, monotonic=lambda: NOW + 61))
    gh.mutate = expire
    _, doc = await observe(ops, data)
    assert doc['evidence']['reason'] == 'observation_time_limit' and len(gh.reads) == 1


@pytest.mark.parametrize('stage', ['during_read', 'before_record'])
async def test_source_revocation_after_admission_prevents_publication(ops, monkeypatch, stage):
    data = await prepared(ops, monkeypatch)
    gh = ops.context['github']
    async def revoke(path, query):
        if stage == 'during_read' or len(gh.reads) == 16:
            await remove_repo(ops, data['project_id'])
    gh.mutate = revoke
    result = await execute(ops, request(ops, data), success=False)
    assert result['status'] == 'failed'
    assert ops.db.execute('SELECT COUNT(*) FROM pm_github_observations').fetchone()[0] == 0
    assert not observation.operation_visible(ops.db, OWNER, result)


async def test_original_key_receipt_recovery_reuses_no_provider_calls_and_withdrawal_hides_all(ops, monkeypatch):
    data = await prepared(ops, monkeypatch)
    body = request(ops, data)
    original = await execute(ops, body)
    gh = ops.context['github']
    count = len(gh.reads)
    replay, created = ops.create(OWNER, **body)
    assert not created and replay['operation_id'] == original['operation_id']
    ctx = OpContext(ops, original)
    assert await observation._run(ctx) == original['result']
    assert len(gh.reads) == count
    await remove_repo(ops, data['project_id'])
    assert not observation.operation_visible(ops.db, OWNER, original)
    current = observation.current_observation(ops, OWNER, data['workspace_id'])
    assert current['observation'] is None and current['visibility_filtered']
    assert observation.list_observations(ops, OWNER, data['workspace_id'])['observations'] == []
    with pytest.raises(OperationError):
        ops.create(OWNER, **body)


async def test_restart_from_original_pages_preserves_receipts_without_get_again(ops, monkeypatch):
    data = await prepared(ops, monkeypatch)
    body = request(ops, data)
    op = ops.create(OWNER, **body)[0]
    ctx = OpContext(ops, op)
    original_effect = ctx.effect
    def cut(name, fn, **kwargs):
        if name == 'pm.github.record':
            raise RuntimeError('synthetic process interruption before final local transaction')
        return original_effect(name, fn, **kwargs)
    ctx.effect = cut
    with pytest.raises(RuntimeError, match='synthetic'):
        await observation._run(ctx)
    count = len(ops.context['github'].reads)
    saved = ops.db.execute('SELECT receipt_digest FROM pm_github_observation_pages ORDER BY step').fetchall()
    result = await observation._run(OpContext(ops, ops.get(op['operation_id'])))
    assert result['coverage_status'] == 'complete'
    assert len(ops.context['github'].reads) == count
    assert saved == ops.db.execute('SELECT receipt_digest FROM pm_github_observation_pages ORDER BY step').fetchall()


async def test_unreceipted_original_read_is_unknown_and_not_refetched(ops, monkeypatch):
    data = await prepared(ops, monkeypatch)
    op = ops.create(OWNER, **request(ops, data))[0]
    ctx = OpContext(ops, op)
    ctx.effect('pm.github.started', lambda: {'started_at': NOW})
    ops._step_start(op['operation_id'], 'pm.github.page.1.000', {})
    result = await observation._run(ctx)
    assert result['coverage_status'] == 'incomplete'
    doc = observation.get_observation(ops, OWNER, data['workspace_id'], result['observation_id'])['observation']['document']
    assert doc['evidence']['reason'] == 'original_page_receipt_unavailable'
    assert ops.context['github'].reads == []


async def test_newest_incomplete_replaces_old_complete_for_workload(ops, monkeypatch):
    data = await prepared(ops, monkeypatch)
    await observe(ops, data)
    ops.context['github'].repo['id'] = 999
    await observe(ops, data)
    worktests.inventory(ops)
    _, doc = await worktests.capture(ops, data)
    assert doc['classification'] == 'unknown'
    assert 'github_observation_incomplete' in worktests.codes(doc)


async def test_stale_saved_receipt_and_provider_rebinding_cannot_prove_idle(ops, monkeypatch):
    data = await prepared(ops, monkeypatch)
    await observe(ops, data)
    worktests.inventory(ops)
    monkeypatch.setattr(workload, 'time', SimpleNamespace(time=lambda: NOW + 121))
    _, doc = await worktests.capture(ops, data)
    assert doc['classification'] == 'unknown' and 'source_expired' in worktests.codes(doc)
    ops.context['github_config'].api_url = 'https://other-provider.example.invalid'
    with pytest.raises(OperationError):
        await observe(ops, data)


@pytest.mark.parametrize('after,limit', [(True, 1), (-1, 1), (2**63, 1), (0, True), (0, 101)])
async def test_history_cursor_validation(ops, monkeypatch, after, limit):
    data = await prepared(ops, monkeypatch)
    with pytest.raises(OperationError):
        observation.list_observations(ops, OWNER, data['workspace_id'], after=after, limit=limit)


async def test_history_respects_whole_record_utf8_byte_bound(ops, monkeypatch):
    data = await prepared(ops, monkeypatch)
    await observe(ops, data)
    await observe(ops, data)
    one = observation.list_observations(ops, OWNER, data['workspace_id'], limit=1)
    monkeypatch.setattr(observation, 'MAX_READ_BYTES', observation._size(one) + 16)
    first = observation.list_observations(ops, OWNER, data['workspace_id'], limit=2)
    assert len(first['observations']) == 1 and first['next_cursor']
    final = observation.list_observations(ops, OWNER, data['workspace_id'], after=first['next_cursor'])
    assert len(final['observations']) == 1 and final['next_cursor'] is None


@pytest.mark.parametrize('link', [
    '<https://other.example.invalid/repos/example/product/pulls?page=2&per_page=100>; rel="next"',
    '<https://api.github.com/repos/example/product/actions/runs?page=2&per_page=100>; rel="next"',
    '<https://api.github.com/repos/example/product/pulls?page=3&per_page=100>; rel="next"',
    '<https://api.github.com/repos/example/product/pulls?page=2&per_page=100&state=all>; rel="next"',
    'malformed',
])
def test_untrusted_pagination_cannot_change_provider_or_endpoint(link):
    with pytest.raises(provider.Incomplete):
        provider.next_page(link, 'https://api.github.com', '/repos/example/product/pulls', {'page': 1, 'per_page': 100})


@pytest.mark.parametrize('action', ['pause', 'close'])
async def test_explicit_observation_allowed_while_paused_or_closed(ops, monkeypatch, action):
    data = await prepared(ops, monkeypatch)
    await worktests.decide(ops, data['workspace_id'], action)
    _, doc = await observe(ops, data)
    assert doc['evidence']['status'] == 'complete'


async def test_lifecycle_change_between_pages_stops_unsent_gets(ops, monkeypatch):
    data = await prepared(ops, monkeypatch)
    gh = ops.context['github']
    async def pause(path, query):
        await worktests.decide(ops, data['workspace_id'], 'pause')
    gh.mutate = pause
    result = await execute(ops, request(ops, data), success=False)
    assert result['status'] == 'failed'
    assert len(gh.reads) == 1
    assert ops.db.execute('SELECT COUNT(*) FROM pm_github_observations').fetchone()[0] == 0


async def test_paused_observer_cannot_use_arbitrary_external_step(ops, monkeypatch):
    data = await prepared(ops, monkeypatch)
    await worktests.decide(ops, data['workspace_id'], 'pause')
    op = ops.create(OWNER, **request(ops, data))[0]
    calls = []
    async def forbidden():
        calls.append(True)
        return {}
    with pytest.raises(OperationError, match='stopped new investment'):
        await OpContext(ops, op).step('pm.github.any_other_action', forbidden)
    assert calls == []


async def test_complete_empty_profile_can_pass_existing_stability_and_final_workload_gate(ops, monkeypatch):
    data = await prepared(ops, monkeypatch)
    await observe(ops, data)
    worktests.inventory(ops)
    await worktests.capture(ops, data)
    monkeypatch.setattr(workload, 'time', SimpleNamespace(time=lambda: NOW + 60))
    _, doc = await worktests.capture(ops, data)
    assert doc['stability']['satisfied'] is True
    current = workload._policy().active_record(ops, OWNER, data['workspace_id'])
    with ops.journal.tx():
        result = workload.evaluate_for_maintenance(ops, OWNER, data['workspace_id'], doc['snapshot_id'], current, now=NOW + 60)
    assert result['eligible'] is True and result['reasons'] == []
    assert result['automatic_maintenance_authorized'] is False


async def test_clock_reversal_between_original_page_receipts_is_incomplete(ops, monkeypatch):
    data = await prepared(ops, monkeypatch)
    gh = ops.context['github']
    async def shift(path, query):
        at = NOW + 2 if len(gh.reads) == 1 else NOW + 1
        monkeypatch.setattr(provider, 'time', SimpleNamespace(time=lambda: at, monotonic=lambda: NOW))
    gh.mutate = shift
    _, doc = await observe(ops, data)
    assert doc['evidence']['status'] == 'incomplete'
    assert doc['evidence']['reason'] == 'observation_clock_changed'
    assert len(gh.reads) == 2
