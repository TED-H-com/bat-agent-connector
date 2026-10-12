"""Configured, immutable findings inside the existing bounded GitHub observation."""
from __future__ import annotations

import copy
import json
import re
from urllib.parse import parse_qs, quote, urlsplit

from . import pm_github_sources as github
from .operations import OperationError
from .pm_workload_sources import digest, has_table

KINDS = ('issues', 'dependabot_pull_requests', 'dependabot_alerts')
PERMISSIONS = {'issues': 'issues:read', 'dependabot_pull_requests': 'pull_requests:read',
               'dependabot_alerts': 'dependabot_alerts:read'}
LIST_KINDS = ('finding_issues', 'finding_pulls', 'finding_files', 'finding_alerts')
OBJECT_KINDS = ('finding_repository', 'finding_branch', 'finding_pull')


def _text(value, maximum=512):
    if (not isinstance(value, str) or not value or len(value) > maximum
            or any(ord(c) < 32 for c in value)):
        raise github.Incomplete('finding_field_unproven')
    return value


def _path(value):
    value = _text(value)
    if '\\' in value or any(p in ('', '.', '..') for p in value.split('/')):
        raise github.Incomplete('finding_path_unproven')
    return value


def validate(config, policy, baseline):
    """Strict optional configuration; never alter an old policy's canonical bytes."""
    def exact(value, keys):
        if not isinstance(value, dict) or set(value) != set(keys):
            raise ValueError
    try:
        exact(config, ('issues', 'dependencies'))
        issues, deps = config['issues'], config['dependencies']
        if issues is not None:
            exact(issues, ('labels_all',))
            labels = issues['labels_all']
            if ('issues' not in policy['sources'] or not isinstance(labels, list) or len(labels) > 20
                    or any(_text(label, 100) != label for label in labels) or len(set(labels)) != len(labels)):
                raise ValueError
        if deps is not None:
            exact(deps, ('manifests', 'base_branch', 'dependabot_actor_id', 'alerts', 'pull_requests'))
            paths = deps['manifests']
            branch = baseline['document']['proof']['maintenance_branch']
            if ('dependencies' not in policy['sources'] or not isinstance(paths, list) or not 1 <= len(paths) <= 100
                    or any(_path(path) != path for path in paths) or len(set(paths)) != len(paths)
                    or branch.get('status') != 'verified_at_observation'
                    or 'refs/heads/' + _text(deps['base_branch']) != branch.get('ref')
                    or type(deps['alerts']) is not bool or type(deps['pull_requests']) is not bool
                    or not (deps['alerts'] or deps['pull_requests'])):
                raise ValueError
            github._id(deps['dependabot_actor_id'])
    except (ValueError, TypeError, KeyError, github.Incomplete):
        raise OperationError('INVALID_PARAMS', 'Select exact configured Issue labels and dependency manifests, branch and native Bot identity', 422) from None


def capabilities():
    return {'version': 1, 'policy_field': 'observation.maintenance_sources', 'sources': list(KINDS),
        'permissions': PERMISSIONS, 'read_only': True, 'automatic_repair': False,
        'release_applicability': 'unknown', 'manifest_scan_completeness': 'unknown',
        'states': ['disabled', 'missing', 'complete', 'incomplete', 'stale', 'context_changed', 'stopped']}


def project(row, kind, scope):
    if not isinstance(row, dict):
        raise github.Incomplete('provider_shape_unproven')
    if kind == 'finding_repository':
        return {**github.project(row, 'repository', scope), 'default_branch': _text(row.get('default_branch'))}
    if kind == 'finding_branch':
        return {'id': _text(row.get('ref')), 'sha': github._sha((row.get('object') or {}).get('sha')),
                'type': github._state((row.get('object') or {}).get('type'), ('commit',))}
    if kind == 'finding_files':
        path = _path(row.get('filename'))
        previous = row.get('previous_filename')
        return {'id': path, 'path': path, 'previous_path': _path(previous) if previous is not None else None}
    if kind == 'finding_alerts':
        number = github._id(row.get('number'))
        dep, vuln, advisory = row.get('dependency') or {}, row.get('security_vulnerability') or {}, row.get('security_advisory') or {}
        package = dep.get('package') or {}
        if package != vuln.get('package'):
            raise github.Incomplete('finding_package_changed')
        patched = vuln.get('first_patched_version')
        if patched is not None and not isinstance(patched, dict):
            raise github.Incomplete('finding_patch_unproven')
        return {'id': number, 'number': number, 'state': github._state(row.get('state'), ('open',)),
            'manifest_path': _path(dep.get('manifest_path')), 'package': _text(package.get('name')),
            'ecosystem': _text(package.get('ecosystem'), 100), 'ghsa_id': _text(advisory.get('ghsa_id'), 100),
            'severity': github._state(vuln.get('severity'), ('low', 'medium', 'high', 'critical')),
            'vulnerable_version_range': _text(vuln.get('vulnerable_version_range'), 1024),
            'first_patched_version': _text(patched.get('identifier')) if isinstance(patched, dict) else None}
    if kind == 'finding_issues':
        result = {'id': github._id(row.get('id')), 'number': github._id(row.get('number')),
            'state': github._state(row.get('state'), ('open',)), 'is_pull_request': 'pull_request' in row}
        labels = row.get('labels')
        if not isinstance(labels, list) or len(labels) > 100:
            raise github.Incomplete('finding_labels_unproven')
        names = [_text(label.get('name'), 100) for label in labels if isinstance(label, dict)]
        if len(names) != len(labels) or len(set(names)) != len(names):
            raise github.Incomplete('finding_labels_unproven')
        return {**result, 'labels': sorted(names)}
    if kind in ('finding_pulls', 'finding_pull'):
        basic = github.project(row, 'pulls', scope)
        actor = row.get('user') or {}
        basic.update(base_branch=_text((row.get('base') or {}).get('ref')),
                     actor_id=github._id(actor.get('id')), actor_type=_text(actor.get('type'), 100))
        if kind == 'finding_pull':
            count = row.get('changed_files')
            if type(count) is not int or not 0 <= count <= 3000:
                raise github.Incomplete('provider_file_coverage_limit')
            basic['changed_files'] = count
        return basic
    raise github.Incomplete('finding_kind_unsupported')


def cursor_next(link, api_url, path, query):
    """Accept only an opaque cursor on the original endpoint with exact filters."""
    if not link:
        return None
    if not isinstance(link, str) or len(link.encode()) > 16384:
        raise github.Incomplete('pagination_unproven')
    expected = urlsplit(api_url + path)
    following, relations = None, set()
    for part in link.split(','):
        match = re.fullmatch(r'\s*<([^<>]+)>;\s*rel="(next|prev|first|last)"\s*', part)
        if not match or match[2] in relations:
            raise github.Incomplete('pagination_unproven')
        relations.add(match[2])
        url = urlsplit(match[1])
        if (url.scheme, url.netloc, url.path) != (expected.scheme, expected.netloc, expected.path) or url.fragment:
            raise github.Incomplete('pagination_scope_changed')
        params = parse_qs(url.query, keep_blank_values=True)
        fixed = {key: [str(value)] for key, value in query.items() if key not in ('after', 'before')}
        if {key: val for key, val in params.items() if key not in ('after', 'before')} != fixed:
            raise github.Incomplete('pagination_scope_changed')
        cursors = {key: val for key, val in params.items() if key in ('after', 'before')}
        if len(cursors) != 1:
            raise github.Incomplete('pagination_unproven')
        key, values = next(iter(cursors.items()))
        if len(values) != 1 or not 1 <= len(values[0]) <= 1024 or any(ord(c) < 32 for c in values[0]):
            raise github.Incomplete('pagination_unproven')
        if match[2] == 'next':
            following = {key: values[0]}
    if 'last' in relations and following is None:
        raise github.Incomplete('pagination_unproven')
    return following


def configuration(intent):
    return intent['observation'].get('maintenance_sources')


def initial(config):
    deps = (config or {}).get('dependencies') or {}
    enabled = {'issues': (config or {}).get('issues') is not None,
        'dependabot_pull_requests': deps.get('pull_requests', False), 'dependabot_alerts': deps.get('alerts', False)}
    return {kind: {'status': 'incomplete' if enabled[kind] else 'disabled',
        'reason': 'not_observed' if enabled[kind] else 'unconfigured', 'result': None,
        'permission': PERMISSIONS[kind], 'findings': []} for kind in KINDS}


def finding(traversal, kind, row, **facts):
    scope = traversal.scope
    native = {'provider_digest': scope['provider_digest'], 'repository_id': scope['repository_id'],
              'kind': kind, 'id': row['id'], 'number': row['number']}
    return {'finding_id': 'pmfn_' + digest([traversal.ctx.target['workspace_id'], native])[:32],
        'native': native, 'policy_id': traversal.intent['policy_id'], 'baseline_id': traversal.intent['baseline_id'],
        'baseline_digest': traversal.intent['baseline_digest'], 'state': 'observed_open',
        'release_applicability': 'unknown', 'resolved': False, 'facts': facts,
        'existing_pull_request': ({'id': row['id'], 'number': row['number'], 'head_sha': row['head_sha']}
                                  if kind == 'dependabot_pull_requests' else None),
        'repair_association': 'existing_pull_request' if kind == 'dependabot_pull_requests' else 'unproven'}


async def traverse(traversal, number):
    config = configuration(traversal.intent)
    state = initial(config)
    traversal.finding_partial = state
    deps = config['dependencies']
    for kind in KINDS:
        source = state[kind]
        if source['status'] == 'disabled':
            continue
        try:
            if kind == 'issues':
                wanted = set(config['issues']['labels_all'])
                rows = await traversal.pages_for(number, traversal.root + '/issues', 'finding_issues',
                    state='open', sort='created', direction='asc')
                source['criteria'] = {'labels_all': sorted(wanted), 'state': 'open', 'exclude_pull_requests': True}
                source['findings'] = [finding(traversal, kind, row, labels=row['labels'],
                    reproduction='unknown', priority='untriaged') for row in rows
                    if not row['is_pull_request'] and wanted <= set(row['labels'])]
            elif kind == 'dependabot_pull_requests':
                rows = await traversal.pages_for(number, traversal.root + '/pulls', 'finding_pulls',
                    state='open', base=deps['base_branch'], sort='created', direction='asc')
                source['coverage'] = {'branch': deps['base_branch'], 'manifests': deps['manifests'],
                    'manifest_scan_completeness': 'not_a_dependency_scan'}
                for row in rows:
                    if row['base_branch'] != deps['base_branch']:
                        raise github.Incomplete('finding_branch_changed')
                    if row['actor_id'] != deps['dependabot_actor_id'] or row['actor_type'] != 'Bot':
                        continue
                    path = traversal.root + '/pulls/' + str(row['number'])
                    detail = (await traversal.read(number, path, {}, 'finding_pull'))['rows'][0]
                    if any(detail[key] != value for key, value in row.items()):
                        raise github.Incomplete('finding_pull_changed')
                    files = await traversal.pages_for(number, path + '/files', 'finding_files')
                    if len(files) != detail['changed_files']:
                        raise github.Incomplete('finding_files_incomplete')
                    touched = sorted({p for item in files for p in (item['path'], item['previous_path'])
                                      if p in deps['manifests']})
                    if touched:
                        source['findings'].append(finding(traversal, kind, row, base_branch=row['base_branch'],
                            base_sha=row['base_sha'], actor_id=row['actor_id'], manifests=touched,
                            changed_files=files, adopted_dependency_version='unknown', available_version='unknown',
                            merged=False, release_fixed=False))
            else:
                repo = (await traversal.read(number, traversal.root, {}, 'finding_repository'))['rows'][0]
                path = traversal.root + '/git/ref/heads/' + quote(repo['default_branch'], safe='')
                branch = (await traversal.read(number, path, {}, 'finding_branch'))['rows'][0]
                if branch['id'] != 'refs/heads/' + repo['default_branch']:
                    raise github.Incomplete('finding_branch_changed')
                source['coverage'] = {'branch': repo['default_branch'], 'observed_branch_head': branch['sha'],
                    'scanner_head': 'unknown', 'manifests': deps['manifests'], 'manifest_scan_completeness': 'unknown',
                    'release_applicability': 'unknown'}
                rows = await traversal.pages_for(number, traversal.root + '/dependabot/alerts', 'finding_alerts',
                    state='open', sort='created', direction='asc')
                source['findings'] = [finding(traversal, kind, row, **{k: v for k, v in row.items() if k != 'id'},
                    adopted_dependency_version='unknown', repair_pull_request='unknown',
                    patch_available=row['first_patched_version'] is not None, release_fixed=False)
                    for row in rows if row['manifest_path'] in deps['manifests']]
                final = (await traversal.read(number, path, {}, 'finding_branch'))['rows'][0]
                if final != branch:
                    raise github.Incomplete('finding_branch_changed')
            source.update(status='complete', reason=None, result='matched' if source['findings'] else 'empty')
        except github.Incomplete as exc:
            source.update(status='incomplete', reason=exc.code, result=None)
    return state


def finalize(traversal, first, second, reason):
    """Keep partial positive evidence, but never promote an unfinished pass to clean."""
    selected = copy.deepcopy((second or first or {}).get('maintenance_sources') or traversal.finding_partial
                             or initial(configuration(traversal.intent)))
    if reason:
        for source in selected.values():
            if source['status'] != 'disabled':
                source.update(status='incomplete', reason=reason, result=None)
    return selected


def attach_work(ops, principal, wid, sources):
    """Dedup only exact native Issue mappings, with bounded original source refs."""
    from . import pm_issue_mappings as mappings
    refs = {}
    issue_source = sources['issues']
    for source in sources.values():
        for item in source['findings']:
            item['existing_work'] = []
    if not issue_source['findings'] or not has_table(ops.db, 'pm_issue_revisions'):
        return []
    wanted = {(item['native']['id'], item['native']['number'], item['native']['repository_id']): item
              for item in issue_source['findings']}
    rows = ops.db.execute("""SELECT r.* FROM pm_issue_revisions r JOIN (
        SELECT mapping_id,MAX(sequence) AS last FROM pm_issue_revisions
        WHERE workspace_id=? GROUP BY mapping_id) s ON r.sequence=s.last ORDER BY r.sequence LIMIT 1001""", (wid,))
    size = 0
    for count, row in enumerate(rows):
        size += len(row['document'].encode('utf-8'))
        if count >= 1000 or size > 2_000_000 or len(refs) >= 64:
            issue_source.update(status='incomplete', reason='work_mapping_limit', result=None)
            break
        doc = json.loads(row['document'])
        issue = doc.get('issue') or {}
        item = wanted.get((issue.get('id'), issue.get('number'), issue.get('repository_id')))
        if item is None or doc.get('github_api_url') != ops.context['github'].cfg.api_url:
            continue
        history = mappings._rows(ops.db, wid, row['mapping_id'])
        latest = history[-1]
        mappings._access(ops, principal, wid, history)
        ref = {key: latest[key] for key in ('mapping_id', 'revision_id', 'document_digest', 'operation_id', 'work_item_id')}
        refs[ref['revision_id']] = ref
        item['existing_work'].append({**ref, 'tracking_state': latest['document']['state']})
    return list(refs.values())


def require_sources(ops, principal, wid, refs):
    from . import pm_issue_mappings as mappings
    for ref in refs:
        rows = mappings._rows(ops.db, wid, ref['mapping_id'])
        selected = next((i for i, row in enumerate(rows) if row['revision_id'] == ref['revision_id']), None)
        if selected is None or any(rows[selected][key] != value for key, value in ref.items()):
            raise OperationError('NOT_FOUND', 'Original finding work source unavailable', 404)
        mappings._access(ops, principal, wid, rows[:selected + 1])


def current_findings(ops, principal, workspace_id):
    from . import pm_github_observation as observations
    from . import pm_lifecycle_gates, pm_maintenance_policy, pm_workload
    with observations.baselines._snapshot(ops.db):
        pm_workload._member(ops, principal, workspace_id)
        policy = pm_maintenance_policy.active_record(ops, principal, workspace_id)
        configured = policy['observation'].get('maintenance_sources')
        result = {'workspace_id': workspace_id, 'capabilities': capabilities(), 'observation_ref': None,
                  'sources': initial(configured), 'automatic_repair_authorized': False}
        row = observations.current_observation(ops, principal, workspace_id)
        saved = row['observation']
        result['visibility_filtered'] = row['visibility_filtered']
        lifecycle = pm_lifecycle_gates.head(ops.db, workspace_id)
        expected = pm_workload._intent(policy, lifecycle)
        status = None
        if not saved:
            status = 'missing'
        else:
            original = observations._record(ops.db, workspace_id, saved['observation_id'])
            if original['source_binding'] != expected:
                status = 'context_changed'
            elif observations.time.time() < saved['document']['as_of'] or observations.time.time() >= saved['document']['expires_at']:
                status = 'stale'
            if status != 'context_changed':
                result['sources'] = copy.deepcopy(saved['document']['evidence'].get('maintenance_sources') or initial(configured))
            result['observation_ref'] = {key: saved[key] for key in ('observation_id', 'document_digest')}
        if not policy['policy']['enabled'] or lifecycle.get('stage') not in ('development', 'maintain'):
            status = 'stopped'
        if status:
            for source in result['sources'].values():
                if source['status'] != 'disabled':
                    source.update(status=status, reason=status, result=None)
        return observations._bounded(result)


def get_finding(ops, principal, workspace_id, observation_id, document_digest, finding_id):
    """Exact historical occurrence, full source access; never execution authority."""
    from . import pm_github_observation as observations
    record = observations.get_observation(ops, principal, workspace_id, observation_id)['observation']
    if record['document_digest'] != document_digest:
        raise OperationError('PM_FINDING_CHANGED', 'Select the exact original observation digest', 409)
    for kind, source in record['document']['evidence'].get('maintenance_sources', {}).items():
        for item in source['findings']:
            if item['finding_id'] == finding_id:
                return {'workspace_id': workspace_id, 'observation_ref': {
                    'observation_id': observation_id, 'document_digest': document_digest},
                    'source': kind, 'source_status': source['status'], 'finding': item,
                    'automatic_repair_authorized': False}
    raise OperationError('NOT_FOUND', 'Original finding unavailable', 404)
