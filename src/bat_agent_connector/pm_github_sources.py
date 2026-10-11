"""Precise bounded GitHub GET profile; no write, scheduler or third-party CI inventory."""
from __future__ import annotations

import json
import re
import time
from urllib.parse import parse_qs, urlsplit

from .errors import TokenUnavailable
from .github import GitHubAmbiguous, GitHubReadLimit
from .pm_workload_sources import digest

PROFILE = 'github_open_prs_and_actions_v1'
MAX_PAGES = 128
MAX_ROWS = 4096
MAX_BYTES = 8_000_000
PAGE_BYTES = 512_000
MAX_SECONDS = 60
NONTERMINAL = ('in_progress', 'queued', 'requested', 'waiting', 'pending')
OUTSIDE_SCOPE = ['issues', 'third_party_ci_outside_open_pr_heads', 'unpublished_or_local_work',
                 'completed_actions_outside_open_pr_heads', 'provider_changes_between_reads']
SHA = re.compile('[0-9a-f]{40}')


class Incomplete(Exception):
    def __init__(self, code):
        self.code = code


def _id(value):
    if type(value) is not int or not 0 < value < 2**63:
        raise Incomplete('provider_identity_unproven')
    return value


def _sha(value):
    if not isinstance(value, str) or not SHA.fullmatch(value):
        raise Incomplete('provider_head_unproven')
    return value


def _state(value, allowed):
    if not isinstance(value, str) or value not in allowed:
        raise Incomplete('provider_status_unproven')
    return value


def project(row, kind, scope, sha=None):
    """Only identities and state, never provider prose, URL, identity email or logs."""
    if not isinstance(row, dict):
        raise Incomplete('provider_shape_unproven')
    result = {'id': _id(row.get('id'))}
    if kind == 'repository':
        if result['id'] != scope['repository_id'] or str(row.get('full_name', '')).lower() != scope['repository'].lower():
            raise Incomplete('provider_repository_changed')
        return result
    if kind == 'pulls':
        if row.get('state') != 'open' or (row.get('base') or {}).get('repo', {}).get('id') != scope['repository_id']:
            raise Incomplete('provider_pull_scope_changed')
        return {**result, 'number': _id(row.get('number')), 'state': 'open',
            'head_sha': _sha((row.get('head') or {}).get('sha')),
            'base_sha': _sha((row.get('base') or {}).get('sha'))}
    if kind == 'statuses':
        # The exact SHA comes from the fixed request; commit statuses lack a SHA field.
        if not isinstance(row.get('context'), str) or not row['context'] or len(row['context']) > 1024:
            raise Incomplete('provider_status_context_unproven')
        return {**result, 'head_sha': sha, 'state': _state(row.get('state'), ('error', 'failure', 'pending', 'success')),
            'context_digest': digest(row.get('context'))}
    if _sha(row.get('head_sha')) != sha and kind != 'actions':
        raise Incomplete('provider_head_changed')
    status = _state(row.get('status'), (*NONTERMINAL, 'completed'))
    conclusion = row.get('conclusion')
    if conclusion is not None and conclusion not in ('success', 'failure', 'neutral', 'cancelled', 'timed_out',
                                                    'action_required', 'stale', 'skipped', 'startup_failure'):
        raise Incomplete('provider_conclusion_unproven')
    result.update(head_sha=_sha(row.get('head_sha')), status=status, conclusion=conclusion)
    if kind == 'actions':
        if (row.get('repository') or {}).get('id') != scope['repository_id'] or status not in NONTERMINAL or conclusion is not None:
            raise Incomplete('provider_action_scope_changed')
        result['run_attempt'] = _id(row.get('run_attempt'))
        result['workflow_id'] = _id(row.get('workflow_id'))
    if kind == 'checks':
        result['suite_id'] = _id((row.get('check_suite') or {}).get('id'))
    return result


def next_page(link, api_url, path, query):
    """Never follow an arbitrary Link URL; prove the expected same-endpoint page."""
    if not link:
        return None
    if not isinstance(link, str) or len(link.encode()) > 16_384:
        raise Incomplete('pagination_unproven')
    expected = urlsplit(api_url + path)
    found, relations = None, set()
    for part in link.split(','):
        match = re.fullmatch(r'\s*<([^<>]+)>;\s*rel="(next|last|first|prev)"\s*', part)
        if not match or match[2] in relations:
            raise Incomplete('pagination_unproven')
        relations.add(match[2])
        url = urlsplit(match[1])
        if (url.scheme, url.netloc, url.path) != (expected.scheme, expected.netloc, expected.path) or url.fragment:
            raise Incomplete('pagination_scope_changed')
        params = parse_qs(url.query, keep_blank_values=True)
        fixed = {key: [str(value)] for key, value in query.items() if key != 'page'}
        if {key: value for key, value in params.items() if key != 'page'} != fixed:
            raise Incomplete('pagination_scope_changed')
        pages = params.get('page', [])
        if len(pages) != 1 or not pages[0].isdigit() or not 1 <= int(pages[0]) <= MAX_PAGES:
            raise Incomplete('pagination_unproven')
        if match[2] == 'next':
            found = int(pages[0])
            if found != query['page'] + 1:
                raise Incomplete('pagination_unproven')
    if 'last' in relations and found is None:
        last = next(re.fullmatch(r'\s*<([^<>]+)>;\s*rel="(next|last|first|prev)"\s*', p)
                    for p in link.split(',') if 'rel="last"' in p)
        if int(parse_qs(urlsplit(last[1]).query)['page'][0]) > query['page']:
            raise Incomplete('pagination_unproven')
    return found


class Traversal:
    def __init__(self, ctx, intent, started, check):
        self.ctx, self.intent, self.started, self.check = ctx, intent, started, check
        self.scope = intent['scope']
        self.gh = ctx.service.context['github']
        self.root = '/repos/' + self.scope['repository']
        self.pages, self.rows, self.bytes, self.receipts = 0, 0, 0, []
        self.open_pulls, self.nonterminal_actions = False, False
        self.last_received = started
        self.deadline = time.monotonic() + max(0, MAX_SECONDS - (time.time() - started))

    def limits(self):
        if time.time() < self.started or time.time() - self.started >= MAX_SECONDS or time.monotonic() >= self.deadline:
            raise Incomplete('observation_time_limit')
        if self.pages >= MAX_PAGES:
            raise Incomplete('observation_page_limit')
        if self.bytes >= MAX_BYTES:
            raise Incomplete('observation_byte_limit')

    async def read(self, traversal, path, query, kind, sha=None):
        self.limits()
        key = f'pm.github.page.{traversal}.{self.pages:03d}'
        request = {'path': path, 'query': query, 'kind': kind, 'head_sha': sha, 'profile': PROFILE}

        async def read_once():
            self.check()
            try:
                answer = await self.gh.bounded_get(path, query=query,
                    max_bytes=min(PAGE_BYTES, MAX_BYTES - self.bytes),
                    timeout_s=max(0.001, min(30, self.deadline - time.monotonic())))
                self.check()
                meta, status = answer['metadata'], answer['status']
                if (meta.get('authenticated') is not True or type(meta.get('bytes')) is not int
                        or not 0 <= meta['bytes'] <= min(PAGE_BYTES, MAX_BYTES - self.bytes)
                        or not isinstance(meta.get('body_sha256'), str)
                        or not re.fullmatch('[0-9a-f]{64}', meta['body_sha256'])):
                    raise Incomplete('provider_metadata_unproven')
                if status != 200:
                    raise Incomplete('provider_read_unavailable')
                payload = answer['payload']
                total = None
                if kind == 'repository':
                    items = [payload]
                elif kind in ('pulls', 'statuses'):
                    items = payload
                else:
                    field = {'suites': 'check_suites', 'checks': 'check_runs', 'actions': 'workflow_runs'}[kind]
                    if not isinstance(payload, dict):
                        raise Incomplete('provider_shape_unproven')
                    items, total = payload.get(field), payload.get('total_count')
                    if type(total) is not int or not 0 <= total <= MAX_ROWS:
                        raise Incomplete('provider_row_limit')
                    if kind == 'actions' and total >= 1000:
                        raise Incomplete('provider_search_limit')
                if not isinstance(items, list) or len(items) > (1 if kind == 'repository' else 100):
                    raise Incomplete('provider_page_shape_unproven')
                if self.rows + len(items) > MAX_ROWS:
                    raise Incomplete('observation_row_limit')
                following = next_page(meta.get('link', ''), self.gh.cfg.api_url, path, query) if query else None
                if kind in ('pulls', 'statuses') and len(items) == 100 and following is None:
                    raise Incomplete('pagination_full_page_without_continuation')
                projected = [project(item, kind, self.scope, sha) for item in items]
                return {'request_digest': digest(request), 'status': 200, 'received_at': time.time(),
                    'body_sha256': meta['body_sha256'], 'bytes': meta['bytes'], 'rows': projected,
                    'total_count': total, 'next_page': following}
            except GitHubReadLimit:
                return {'request_digest': digest(request), 'error': 'provider_read_limit'}
            except (GitHubAmbiguous, TokenUnavailable, TimeoutError, OSError):
                return {'request_digest': digest(request), 'error': 'provider_read_unavailable'}
            except (AttributeError, KeyError, TypeError, ValueError):
                return {'request_digest': digest(request), 'error': 'provider_shape_unproven'}
            except Incomplete as exc:
                return {'request_digest': digest(request), 'error': exc.code}

        async def missing_receipt(_):
            # A read without its original response cannot be recreated at its
            # original observation time. Preserve the gap, do not observe again.
            return {'request_digest': digest(request), 'error': 'original_page_receipt_unavailable'}

        receipt = await self.ctx.step(key, read_once, request=request, reconcile=missing_receipt)
        self.check()
        def preserve():
            self.check()
            encoded = json.dumps(receipt, sort_keys=True, ensure_ascii=False, separators=(',', ':'))
            self.ctx.service.db.execute('''INSERT INTO pm_github_observation_pages
                (operation_id,step,receipt,receipt_digest) VALUES(?,?,?,?)''',
                (self.ctx.operation_id, key, encoded, digest(receipt)))
            return {'receipt_digest': digest(receipt)}
        self.ctx.effect(key + '.preserve', preserve)
        if receipt.get('request_digest') != digest(request):
            raise Incomplete('original_page_request_changed')
        self.receipts.append({'step': key, 'request_digest': digest(request), 'receipt_digest': digest(receipt)})
        self.pages += 1
        if 'error' in receipt:
            raise Incomplete(receipt['error'])
        if not self.last_received <= receipt['received_at'] <= time.time():
            raise Incomplete('observation_clock_changed')
        self.last_received = receipt['received_at']
        self.bytes += receipt['bytes']
        self.rows += len(receipt['rows'])
        self.open_pulls |= kind == 'pulls' and bool(receipt['rows'])
        self.nonterminal_actions |= kind == 'actions' and bool(receipt['rows'])
        if self.bytes > MAX_BYTES or self.rows > MAX_ROWS:
            raise Incomplete('observation_limit')
        return receipt

    async def pages_for(self, traversal, path, kind, sha=None, **filters):
        page, rows, seen, total = 1, [], set(), None
        while True:
            receipt = await self.read(traversal, path, {'per_page': 100, 'page': page, **filters}, kind, sha)
            if total is not None and total != receipt['total_count']:
                raise Incomplete('provider_count_changed')
            total = receipt['total_count']
            for item in receipt['rows']:
                if item['id'] in seen:
                    raise Incomplete('provider_duplicate_row')
                seen.add(item['id'])
                rows.append(item)
            following = receipt['next_page']
            if not following:
                if total is not None and total != len(rows):
                    raise Incomplete('provider_count_incomplete')
                return sorted(rows, key=lambda row: row['id'])
            if not receipt['rows']:
                raise Incomplete('pagination_empty_page')
            page = following

    async def traverse(self, number):
        await self.read(number, self.root, {}, 'repository')
        pulls = await self.pages_for(number, self.root + '/pulls', 'pulls', state='open', sort='created', direction='asc')
        heads = {}
        for sha in sorted({pull['head_sha'] for pull in pulls}):
            suites = await self.pages_for(number, self.root + '/commits/' + sha + '/check-suites', 'suites', sha)
            checks = []
            for suite in suites:
                values = await self.pages_for(number, self.root + f'/check-suites/{suite["id"]}/check-runs', 'checks', sha, filter='all')
                if any(check['suite_id'] != suite['id'] for check in values):
                    raise Incomplete('provider_suite_changed')
                checks.extend(values)
            statuses = await self.pages_for(number, self.root + '/commits/' + sha + '/statuses', 'statuses', sha)
            heads[sha] = {'suites': suites, 'checks': checks, 'statuses': statuses}
        actions = []
        for state in NONTERMINAL:
            values = await self.pages_for(number, self.root + '/actions/runs', 'actions', status=state)
            if any(run['status'] != state for run in values):
                raise Incomplete('provider_action_status_changed')
            actions.extend(values)
        if len({run['id'] for run in actions}) != len(actions):
            raise Incomplete('provider_action_changed_during_traversal')
        await self.read(number, self.root, {}, 'repository')
        return {'pulls': pulls, 'heads': heads, 'actions': sorted(actions, key=lambda row: row['id'])}

    async def collect(self):
        first, second, reason = None, None, None
        try:
            first = await self.traverse(1)
            second = await self.traverse(2)
            if first != second:
                raise Incomplete('provider_changed_between_traversals')
            if time.time() < self.started or time.time() - self.started >= MAX_SECONDS:
                raise Incomplete('observation_time_limit')
        except Incomplete as exc:
            reason = exc.code
        return {'status': 'incomplete' if reason else 'complete', 'reason': reason,
            'traversals_completed': int(first is not None) + int(second is not None),
            'facts': second if second is not None else first, 'receipt_refs': self.receipts,
            'pages': self.pages, 'rows': self.rows, 'bytes': self.bytes, 'outside_scope': OUTSIDE_SCOPE,
            'observed_open_pulls': self.open_pulls, 'observed_nonterminal_actions': self.nonterminal_actions,
            'read_only': True, 'atomic_provider_snapshot': False}
