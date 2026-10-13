"""Explicit native required-check proof within the existing bounded GitHub reader."""
from __future__ import annotations

from urllib.parse import quote

from . import pm_github_sources as github
from .iso_time import parse_datetime
from .pm_workload_sources import digest

PROFILE = 'github_required_status_checks_v1'
OBJECT_KINDS = ('required_pull', 'required_branch', 'required_protection')
LIST_KINDS = ('required_rules', 'required_statuses')
KNOWN_RULES = frozenset({'creation', 'update', 'deletion', 'required_linear_history', 'required_signatures',
    'pull_request', 'non_fast_forward', 'commit_message_pattern', 'commit_author_email_pattern',
    'committer_email_pattern', 'branch_name_pattern', 'tag_name_pattern', 'file_path_restriction',
    'max_file_path_length', 'file_extension_restriction', 'max_file_size', 'required_deployments'})


def configuration(intent):
    return intent['observation'].get('required_ci_profile')


def capabilities():
    return {'version': 1, 'profiles': [PROFILE], 'policy_field': 'observation.required_ci_profile',
        'read_only': True, 'permissions': ['metadata:read', 'administration:read', 'checks:read', 'statuses:read', 'pull_requests:read'],
        'coverage': 'active_applied_branch_rules_and_legacy_required_status_checks',
        'unsupported': ['required_workflows', 'merge_queue', 'ambiguous_404', 'implicit_app_identity'],
        'fresh_current_exact_head_required': True, 'empty_required_set_passes': False}


def _context(value):
    if not isinstance(value, str) or not value or len(value) > 1024:
        raise github.Incomplete('required_ci_context_unproven')
    return digest(value)


def _requirement(value, field):
    if not isinstance(value, dict) or field not in value:
        raise github.Incomplete('required_ci_rules_unproven')
    app = value.get(field)
    if app is not None and app != -1:
        app = github._id(app)
    return {'context_digest': _context(value.get('context')), 'app_id': app if app != -1 else None}


def _checks(value, field):
    if not isinstance(value, list) or len(value) > 100:
        raise github.Incomplete('required_ci_rules_limit')
    rows = [_requirement(item, field) for item in value]
    if len({digest(row) for row in rows}) != len(rows):
        raise github.Incomplete('required_ci_duplicate_rule')
    return sorted(rows, key=digest)


def project(row, kind, scope, sha=None):
    if not isinstance(row, dict):
        raise github.Incomplete('required_ci_shape_unproven')
    if kind == 'required_pull':
        base, head = row.get('base') or {}, row.get('head') or {}
        if (row.get('state') != 'open' or (base.get('repo') or {}).get('id') != scope['repository_id']
                or not isinstance(base.get('ref'), str) or not base['ref'] or len(base['ref']) > 255):
            raise github.Incomplete('required_ci_target_changed')
        return {'id': github._id(row.get('id')), 'number': github._id(row.get('number')),
            'head_sha': github._sha(head.get('sha')), 'base_sha': github._sha(base.get('sha')), 'base_ref': base['ref']}
    if kind == 'required_branch':
        if type(row.get('protected')) is not bool or not isinstance(row.get('name'), str):
            raise github.Incomplete('required_ci_branch_unproven')
        return {'id': digest(row['name']), 'name': row['name'], 'sha': github._sha((row.get('commit') or {}).get('sha')),
            'protected': row['protected']}
    if kind == 'required_protection':
        if 'required_status_checks' not in row:
            raise github.Incomplete('required_ci_legacy_unproven')
        selected = row.get('required_status_checks')
        if selected is None:
            return {'id': 'legacy', 'required': [], 'strict': False}
        if not isinstance(selected, dict) or type(selected.get('strict')) is not bool:
            raise github.Incomplete('required_ci_legacy_unproven')
        checks = _checks(selected.get('checks'), 'app_id')
        contexts = selected.get('contexts')
        if not isinstance(contexts, list) or len(contexts) > 100 or {_context(value) for value in contexts} != {v['context_digest'] for v in checks}:
            raise github.Incomplete('required_ci_legacy_app_identity_unproven')
        return {'id': 'legacy', 'required': checks, 'strict': selected['strict']}
    if kind == 'required_rules':
        rid, typ = github._id(row.get('ruleset_id')), row.get('type')
        if row.get('ruleset_source_type') not in ('Repository', 'Organization', 'Enterprise') or not isinstance(typ, str):
            raise github.Incomplete('required_ci_rules_unproven')
        params = row.get('parameters') or {}
        if typ == 'required_status_checks':
            if type(params.get('strict_required_status_checks_policy')) is not bool:
                raise github.Incomplete('required_ci_rules_unproven')
            required = _checks(params.get('required_status_checks'), 'integration_id')
        elif typ in KNOWN_RULES:
            required = []
        else:
            raise github.Incomplete('required_ci_unsupported_branch_rule')
        return {'id': digest([rid, typ]), 'ruleset_id': rid, 'type': typ,
            'source_type': row['ruleset_source_type'], 'parameters_digest': digest(params), 'required': required,
            'strict': params.get('strict_required_status_checks_policy', False)}
    if kind == 'required_checks':
        value = github.project(row, 'checks', scope, sha)
        return {**value, 'context_digest': _context(row.get('name')), 'app_id': github._id((row.get('app') or {}).get('id'))}
    if kind == 'required_statuses':
        value = github.project(row, 'statuses', scope, sha)
        try:
            stamp = parse_datetime(row['created_at'])
            if stamp.tzinfo is None:
                raise ValueError
        except (KeyError, TypeError, AttributeError, ValueError):
            raise github.Incomplete('required_ci_status_order_unproven') from None
        return {**value, 'created_at': stamp.timestamp()}
    raise github.Incomplete('required_ci_profile_unproven')


def evaluate(required, checks, statuses):
    matrix = []
    for wanted in required:
        runs = [row for row in checks if row['context_digest'] == wanted['context_digest']
            and (wanted['app_id'] is None or wanted['app_id'] == row['app_id'])]
        states = [row for row in statuses if row['context_digest'] == wanted['context_digest']] if wanted['app_id'] is None else []
        if states:
            stamp = max(row['created_at'] for row in states)
            states = [row for row in states if row['created_at'] == stamp]
        passed = bool(runs or states) and all(row['status'] == 'completed' and row['conclusion'] == 'success' for row in runs) and all(row['state'] == 'success' for row in states)
        matrix.append({**wanted, 'status': 'passed' if passed else 'blocked',
            'check_ids': [row['id'] for row in runs], 'status_ids': [row['id'] for row in states]})
    return {'status': 'passed' if matrix and all(row['status'] == 'passed' for row in matrix) else 'blocked',
        'reason': None if matrix and all(row['status'] == 'passed' for row in matrix) else 'required_checks_not_passing' if matrix else 'required_checks_not_configured',
        'required': matrix}


async def traverse(reader, number, pulls):
    branches, results = {}, []
    for pull in pulls:
        detail = (await reader.read(number, reader.root + '/pulls/' + str(pull['number']), {}, 'required_pull'))['rows'][0]
        if any(detail[key] != pull[key] for key in ('id', 'number', 'head_sha', 'base_sha')):
            raise github.Incomplete('required_ci_target_changed')
        branch = detail['base_ref']
        path = reader.root + '/branches/' + quote(branch, safe='')
        if branch not in branches:
            current = (await reader.read(number, path, {}, 'required_branch'))['rows'][0]
            if current['name'] != branch or current['sha'] != detail['base_sha']:
                raise github.Incomplete('required_ci_branch_changed')
            rules = await reader.pages_for(number, reader.root + '/rules/branches/' + quote(branch, safe=''), 'required_rules')
            legacy = (await reader.read(number, path + '/protection', {}, 'required_protection'))['rows'][0]
            final = (await reader.read(number, path, {}, 'required_branch'))['rows'][0]
            if current != final:
                raise github.Incomplete('required_ci_branch_changed')
            branches[branch] = {'branch': current, 'rules': rules, 'legacy': legacy}
        selected = branches[branch]
        if selected['branch']['sha'] != detail['base_sha']:
            raise github.Incomplete('required_ci_branch_changed')
        required = {digest(value): value for value in selected['legacy']['required']}
        for rule in selected['rules']:
            required.update({digest(value): value for value in rule['required']})
        sha = detail['head_sha']
        checks = await reader.pages_for(number, reader.root + '/commits/' + sha + '/check-runs', 'required_checks', sha, filter='latest')
        statuses = await reader.pages_for(number, reader.root + '/commits/' + sha + '/statuses', 'required_statuses', sha)
        final = (await reader.read(number, reader.root + '/pulls/' + str(pull['number']), {}, 'required_pull'))['rows'][0]
        if detail != final:
            raise github.Incomplete('required_ci_target_changed')
        results.append({'native_pr_id': detail['id'], 'pull_number': detail['number'], 'head_sha': sha,
            'base_ref': branch, 'base_sha': detail['base_sha'], 'checks': checks, 'statuses': statuses,
            **evaluate([required[key] for key in sorted(required)], checks, statuses)})
    return {'profile': PROFILE, 'status': 'complete', 'branches': branches, 'pulls': results}



def historical(ops, principal, parent, receipt, reference):
    from . import pm_github_observation as observations
    from . import pm_maintenance_repairs as repairs
    from .operations import OperationError
    if (not isinstance(reference, dict) or set(reference) != {'observation_id', 'document_digest', 'native_pr_id'}
            or type(reference['native_pr_id']) is not int or reference['native_pr_id'] != receipt['native_pr_id']):
        raise OperationError('PM_REPAIR_REQUIRED_CI_UNPROVEN', 'Select the exact original observation and native PR', 409)
    row = observations._record(ops.db, parent['target']['workspace_id'], reference['observation_id'])
    observations._access(ops, principal, parent['target']['workspace_id'], row)
    document = row['document']
    proof = (document['evidence'].get('facts') or {}).get('required_ci') or {}
    expected = repairs._intent(parent)['check']['workload']
    if (row['document_digest'] != reference['document_digest'] or row['source_binding'] != expected
            or document['evidence']['status'] != 'complete' or proof.get('profile') != PROFILE
            or proof.get('status') != 'complete' or document['evidence']['traversals_completed'] != 2):
        raise OperationError('PM_REPAIR_REQUIRED_CI_UNPROVEN', 'The original exact policy required-CI coverage is unavailable', 409)
    matches = [value for value in proof['pulls'] if all(value.get(key) == receipt[key] for key in ('native_pr_id', 'pull_number', 'base_ref', 'base_sha'))
        and value['head_sha'] == receipt['candidate_sha']]
    if len(matches) != 1:
        raise OperationError('PM_REPAIR_REQUIRED_CI_UNPROVEN', 'The observation does not describe this exact original PR', 409)
    return {'reference': reference, 'operation_id': row['operation_id'], 'proof': matches[0],
        'as_of': document['as_of'], 'expires_at': document['expires_at']}


def require(ops, principal, parent, receipt, reference):
    from . import pm_maintenance_repairs as repairs
    from .operations import OperationError
    result = historical(ops, principal, parent, receipt, reference)
    latest = ops.db.execute('SELECT observation_id FROM pm_github_observations WHERE workspace_id=? ORDER BY sequence DESC LIMIT 1',
        (parent['target']['workspace_id'],)).fetchone()
    if (latest is None or latest[0] != reference['observation_id'] or result['proof']['status'] != 'passed'
            or not result['as_of'] <= repairs.time.time() < result['expires_at']):
        raise OperationError('PM_REPAIR_REQUIRED_CI_UNPROVEN', 'The selected original required-CI proof is no longer newest, fresh and passing', 409)
    return result
