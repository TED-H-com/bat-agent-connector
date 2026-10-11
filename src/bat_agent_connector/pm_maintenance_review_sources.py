"""Fixed maintenance report facts and flat original-source access references."""
from __future__ import annotations

import json
import math

from . import pm_evidence, pm_snapshots
from . import pm_github_observation as github
from . import pm_maintenance_checks as checks
from . import pm_maintenance_policy as policies
from . import pm_release_baselines as baselines
from . import pm_workload as workload
from .operations import OperationError

MAX_ROWS = 100
MAX_REFS = 512
MAX_SOURCE_BYTES = 300_000
MAX_COMPARISON_ROWS = 512


def bad(message, code='PM_MAINTENANCE_REVIEW_SOURCE_UNAVAILABLE', status=404):
    return OperationError(code, message, status)


def ref(kind, ident, digest, **extra):
    return {'kind': kind, 'id': ident, 'digest': digest, **extra}


def unique(refs):
    values = {baselines._json(item): item for item in refs}
    if len(values) > MAX_REFS:
        raise bad('Select a shorter review comparison chain', 'PM_MAINTENANCE_REVIEW_SOURCE_LIMIT', 413)
    return [values[key] for key in sorted(values)]


def key(reference):
    return 'pmrs_' + baselines.sources.digest(reference)[:24]


def snapshot_refs(document, snapshot_digest):
    """Flatten snapshot authority; comparisons never recurse through live heads."""
    return unique([ref('snapshot_identity', document['snapshot_id'], snapshot_digest),
        *[ref('evidence', item['evidence_id'], item['fact_digest']) for item in document['evidence']],
        *((document.get('maintenance') or {}).get('access_refs') or [])])


def access(ops, principal, wid, references):
    """Authorize exact original identities without substituting current source data."""
    for source in references:
        kind, ident, expected = source['kind'], source['id'], source['digest']
        if kind == 'policy':
            actual = policies.fixed_record(ops, principal, wid, ident)['document_digest']
        elif kind == 'baseline':
            row = baselines._record(ops.db, wid, ident)
            baselines._access(ops, principal, wid, row)
            actual = row['document_digest']
        elif kind in {'github', 'workload'}:
            module = github if kind == 'github' else workload
            row = module._record(ops.db, wid, ident)
            module._access(ops, principal, wid, row)
            actual = row['document_digest']
        elif kind == 'maintenance_check':
            row = checks._row(ops, 'op_' + ident[5:])
            if row is None or row['workspace_id'] != wid:
                raise bad('Original check is unavailable')
            checks._access(ops, principal, ops.get(row['operation_id'], steps=False))
            actual = row['context_digest'] if source.get('basis') == 'admission_context' else row['document_digest']
        elif kind == 'maintenance_repair':
            from . import pm_maintenance_repairs as repairs
            row = repairs._row(ops, wid, ident)
            repairs._full_access(ops, principal, ops.get(row['operation_id'], steps=False))
            actual = row['context_digest']
        elif kind == 'evidence':
            actual = pm_evidence.get_evidence(ops, principal, wid, ident)['evidence']['fact_digest']
        elif kind == 'snapshot_identity':
            row = ops.db.execute('SELECT snapshot_digest,document FROM pm_document_snapshots WHERE workspace_id=? AND snapshot_id=?', (wid, ident)).fetchone()
            if row is None or pm_snapshots._digest(json.loads(row['document'])) != row['snapshot_digest']:
                raise bad('Original snapshot identity is unavailable')
            actual = row['snapshot_digest']
        else:
            raise bad('Unknown original review source')
        if actual != expected:
            raise bad('Original review source identity changed')


def _period_rows(ops, principal, wid, table, ident, start, end, reader, projection):
    if not ops.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone():
        return [], [], {'available': False, 'truncated': False, 'visibility_filtered': False}
    clock = 'COALESCE(finished_at,created_at)' if table == 'pm_maintenance_check_runs' else 'created_at'
    # Table/column choices are fixed internal constants, never user input.
    if table == 'pm_maintenance_repairs':
        # Carry unfinished original work into the review even when it began in
        # an earlier week. This is a fixed point observation, not a new attempt.
        query = ('SELECT repair_id,sequence FROM pm_maintenance_repairs WHERE workspace_id=? '
            'AND created_at<? AND (finished_at IS NULL OR finished_at>=?) ORDER BY sequence LIMIT ?')
        rows = ops.db.execute(query, (wid, end, start, MAX_ROWS + 1)).fetchall()
    else:
        query = (f'SELECT {ident},sequence FROM {table} WHERE workspace_id=? AND {clock}>=? AND {clock}<? '  # noqa: S608
            'ORDER BY sequence LIMIT ?')
        rows = ops.db.execute(query, (wid, start, end, MAX_ROWS + 1)).fetchall()
    values, refs = [], []
    coverage = {'available': True, 'truncated': len(rows) > MAX_ROWS, 'visibility_filtered': False}
    used = 0
    for row in rows[:MAX_ROWS]:
        try:
            value, reference = projection(reader(ops, principal, wid, row[ident]))
        except OperationError as exc:
            if exc.status not in (403, 404):
                raise
            coverage['visibility_filtered'] = True
            continue
        used += baselines._size(value) + baselines._size(reference)
        if used > MAX_SOURCE_BYTES:
            coverage['truncated'] = True
            break
        values.append(value)
        refs.append(reference)
    return values, refs, coverage


def _github_projection(response):
    row = response['observation']
    doc, facts = row['document'], row['document']['evidence']['facts']
    source = ref('github', row['observation_id'], row['document_digest'])
    projected = {'source_id': key(source), 'observation_id': row['observation_id'], 'observed_at': doc['as_of'],
        'expires_at': doc['expires_at'], 'scope_digest': baselines.sources.digest(doc['scope']),
        'coverage_status': doc['evidence']['status'], 'reason': doc['evidence']['reason'],
        'facts_digest': baselines.sources.digest(facts),
        'observed_open_pulls': doc['evidence']['observed_open_pulls'],
        'observed_nonterminal_actions': doc['evidence']['observed_nonterminal_actions'],
        'outside_scope': doc['evidence']['outside_scope']}
    if 'maintenance_sources' in doc['evidence']:
        projected.update(maintenance_sources=doc['evidence']['maintenance_sources'],
            source_binding_digest=doc['source_binding_digest'], document_digest=row['document_digest'])
    return projected, source


def _workload_projection(response):
    row = response['snapshot']
    doc = row['document']
    source = ref('workload', row['snapshot_id'], row['document_digest'])
    return {'source_id': key(source), 'snapshot_id': row['snapshot_id'], 'observed_at': doc['as_of'],
        'expires_at': doc['expires_at'], 'scope_digest': baselines.sources.digest(doc['scope']),
        'classification': doc['classification'], 'blocking_reasons': doc['blocking_reasons'][:50],
        'blockers_truncated': len(doc['blocking_reasons']) > 50,
        'source_coverage': [{'kind': item['kind'], 'status': item['status']} for item in doc['source_coverage'][:50]],
        'coverage_truncated': len(doc['source_coverage']) > 50, 'stability': doc['stability'],
        'quality': doc['quality'], 'automatic_maintenance_authorized': False}, source


def _check_projection(response, ops):
    row = response['run']
    saved = checks._row(ops, row['operation_id'])
    final = row['document']
    source = ref('maintenance_check', row['run_id'], row['document_digest'] if final else saved['context_digest'],
        basis='final_document' if final else 'admission_context')
    return {'source_id': key(source), 'run_id': row['run_id'], 'started_at': row['created_at'],
        'finished_at': row['finished_at'], 'scope_digest': baselines.sources.digest(saved['context']['workload']['scope']),
        'outcome': final['outcome'] if final else 'pending', 'reason': final['reason'] if final else None,
        'children': final['children'] if final else [], 'cost_coverage': final['cost_coverage'] if final else {
            'provider_billing': 'unmeasured', 'charged_microusd': None},
        'repair_started': False}, source


def _repair_projection(response):
    row = response['repair']
    source = ref('maintenance_repair', row['repair_id'], row['context_digest'], basis='admission_context')
    children = []
    for slot in ('child', 'publication_child', 'delivery_child', 'baseline_child', 'runtime_observer'):
        child = row.get(slot)
        if isinstance(child, dict):
            children.append({'slot': slot, **{key: child.get(key) for key in
                ('operation_id', 'action', 'status', 'error_code', 'result')}})
    return {'source_id': key(source), 'repair_id': row['repair_id'], 'operation_id': row['operation_id'],
        'work_item_id': row['work_item_id'], 'started_at': row['created_at'], 'finished_at': row['finished_at'],
        'scope_digest': baselines.sources.digest(row['context']['check']['workload']['scope']),
        'finding_id': row['finding_id'], 'outcome': row.get('outcome'), 'operation_status': row['operation_status'],
        'capacity_held': row['capacity_held'], 'repair_fixed': row['repair_fixed'],
        'release_status': row['release_status'], 'cost_coverage': row['cost_coverage'],
        'retries_used': row['retries_used'], 'children': children,
        'progress': row['progress'], 'runtime_completion': row.get('runtime_completion'),
        'required_ci_selection': row.get('required_ci_selection'),
        'composed_delivery': row.get('composed_delivery') or (row.get('delivery_child') or {}).get('result'),
        'baseline_update': row.get('baseline_update'),
        'observation_basis': 'saved_repair_point', 'later_changes_included': False}, source


def _state(values, coverage, now, *, enabled, supported=True):
    if not enabled:
        return 'disabled'
    if not supported or not coverage['available']:
        return 'not_configured'
    if coverage['visibility_filtered'] or coverage['truncated'] or coverage.get('other_scope_records'):
        return 'incomplete'
    if not values:
        return 'unchecked'
    last = values[-1]
    if (last.get('coverage_status') == 'incomplete' or last.get('outcome') in {'incomplete', 'stopped', 'pending'}
            or last.get('classification') == 'unknown' or last.get('coverage_truncated')
            or any(item['status'] != 'complete' for item in last.get('source_coverage', []))):
        return 'incomplete'
    if 'expires_at' in last and not last['observed_at'] <= now < last['expires_at']:
        return 'stale'
    return 'observed'


def _inventory(facts):
    result = {}
    if not facts:
        return {'digest': baselines.sources.digest(facts), 'items': {}, 'truncated': False}
    for pr in facts['pulls']:
        result['pull/' + str(pr['id'])] = pr
    for run in facts['actions']:
        result['action/' + str(run['id'])] = run
    for sha, head in facts['heads'].items():
        for kind in ('suites', 'checks', 'statuses'):
            for item in head[kind]:
                result[kind + '/' + sha + '/' + str(item['id'])] = item
    return {'digest': baselines.sources.digest(facts),
        'items': {name: baselines.sources.digest(value) for name, value in sorted(result.items())[:MAX_COMPARISON_ROWS]},
        'truncated': len(result) > MAX_COMPARISON_ROWS}


def _changes(previous, current):
    if previous is None or current is None:
        return {'status': 'comparison_unavailable', 'added': [], 'changed': [], 'no_longer_observed': [],
            'resolution_assessed': False, 'whole_period_no_events_proven': False}
    before, after = previous['items'], current['items']
    added, missing = sorted(after.keys() - before.keys()), sorted(before.keys() - after.keys())
    changed = sorted(ident for ident in before.keys() & after.keys() if before[ident] != after[ident])
    different = previous['digest'] != current['digest']
    limited = previous['truncated'] or current['truncated']
    return {'status': ('observed_changes_partial' if limited else 'observed_changes') if different else 'no_observed_changes',
        'comparison_truncated': limited,
        'added': added, 'changed': changed, 'no_longer_observed': missing,
        'resolution_assessed': False, 'whole_period_no_events_proven': False}


def _finding_inventory(sample, kind):
    source = sample['maintenance_sources'][kind]
    if source['status'] != 'complete':
        return None
    items = {item['finding_id']: baselines.sources.digest(item) for item in source['findings']}
    return {'digest': baselines.sources.digest(source),
        'items': dict(sorted(items.items())[:MAX_COMPARISON_ROWS]), 'truncated': len(items) > MAX_COMPARISON_ROWS}


def _finding_report(intent, observations, coverage, now, enabled, previous):
    """Compare only the exact configured source context; retain original point facts."""
    config = intent['observation'].get('maintenance_sources')
    if config is None:
        return None
    context = baselines.sources.digest(intent)
    samples = [row for row in observations if row.get('source_binding_digest') == context]
    deps = config['dependencies'] or {}
    kinds = {'issues': config['issues'] is not None, 'dependabot_pull_requests': deps.get('pull_requests', False),
        'dependabot_alerts': deps.get('alerts', False)}
    states, comparisons, inventories = {}, {}, {}
    prior = previous['report'].get('finding_comparison_facts') if previous else None
    for kind, selected in kinds.items():
        values = [{**row, 'coverage_status': row['maintenance_sources'][kind]['status']} for row in samples]
        state = _state(values, coverage, now, enabled=enabled and selected)
        # Old labels/manifests, policy or lifecycle cannot prove today's configured coverage.
        if selected and enabled and not samples and observations:
            state = 'incomplete'
        states[kind] = {'status': state, 'samples': len(samples), 'configured': selected,
            'other_context_records': len(samples) != len(observations),
            'observation_mode': 'point_samples', 'period_continuity': 'unproven',
            'whole_period_no_events_proven': False, 'release_applicability': 'unknown'}
        current = _finding_inventory(samples[-1], kind) if samples and selected else None
        earlier = None
        if prior and prior['context_digest'] == context:
            earlier = prior['sources'].get(kind)
        elif previous is None and len(samples) >= 2 and selected:
            earlier = _finding_inventory(samples[0], kind)
        comparisons[kind] = _changes(earlier, current)
        inventories[kind] = current
    selected_deps = [states[k] for k in kinds if k != 'issues' and kinds[k]]
    priority = ('incomplete', 'not_configured', 'unchecked', 'stale', 'disabled', 'observed')
    dependency_state = next((state for state in priority if any(v['status'] == state for v in selected_deps)), 'disabled')
    return {'coverage': {'issues': states['issues'], 'dependencies': {
        'status': dependency_state, 'samples': len(samples), 'sources': {k: states[k] for k in kinds if k != 'issues'},
        'release_applicability': 'unknown', 'manifest_scan_completeness': 'unknown'}},
        'finding_changes': comparisons, 'finding_comparison_facts': {'context_digest': context, 'sources': inventories}}


def build(ops, principal, wid, intent, period, now, previous=None):
    baseline = baselines._record(ops.db, wid, intent['baseline_id'])
    policy = policies._record(ops.db, wid, intent['policy_id'])
    refs = [ref('policy', intent['policy_id'], policy['document_digest']),
        ref('baseline', intent['baseline_id'], baseline['document_digest'])]
    start, end = period['start'], period['end']
    observations, gh_refs, gh_coverage = _period_rows(ops, principal, wid, 'pm_github_observations', 'observation_id',
        start, end, github.get_observation, _github_projection)
    workloads, wl_refs, wl_coverage = _period_rows(ops, principal, wid, 'pm_workload_snapshots', 'snapshot_id',
        start, end, workload.get_snapshot, _workload_projection)
    runs, run_refs, run_coverage = _period_rows(ops, principal, wid, 'pm_maintenance_check_runs', 'run_id',
        start, end, checks.get_run, lambda value: _check_projection(value, ops))
    from . import pm_maintenance_repairs as repairs
    repair_rows, repair_refs, repair_coverage = _period_rows(ops, principal, wid, 'pm_maintenance_repairs', 'repair_id',
        start, end, repairs.get_repair, _repair_projection)
    # Other scopes are visible historical context but cannot answer this policy's report.
    scope_digest = baselines.sources.digest(intent['scope'])
    for values, references, coverage in ((observations, gh_refs, gh_coverage), (workloads, wl_refs, wl_coverage),
                                         (runs, run_refs, run_coverage), (repair_rows, repair_refs, repair_coverage)):
        selected = [i for i, value in enumerate(values) if value['scope_digest'] == scope_digest]
        coverage['other_scope_records'] = len(selected) != len(values)
        refs.extend(references[i] for i in selected)
        values[:] = [values[i] for i in selected]
    adopted = policy['document']['policy']
    enabled = adopted['enabled']
    gh_coverage.update(status=_state(observations, gh_coverage, now, enabled=enabled,
        supported=intent['scope']['github_scope'] == github.provider.PROFILE), samples=len(observations),
        observation_mode='point_samples', period_continuity='unproven', whole_period_no_events_proven=False)
    wl_coverage.update(status=_state(workloads, wl_coverage, now, enabled=enabled), samples=len(workloads))
    run_coverage.update(status=_state(runs, run_coverage, now, enabled=enabled), samples=len(runs))
    repair_coverage.update(status=_state(repair_rows, repair_coverage, now, enabled=enabled), samples=len(repair_rows),
        observation_mode='saved_repair_points', includes_unfinished_previous_periods=True)
    current = next((item for item in reversed(observations) if item['coverage_status'] == 'complete'), None)
    # Do not let an older complete point hide the newest failed observation.
    if observations and observations[-1]['coverage_status'] != 'complete':
        current = None
    earlier = None
    if previous is not None:
        if previous['report']['policy']['scope'] == intent['scope']:
            earlier = previous['report'].get('comparison_facts')
        refs.extend(previous['access_refs'])
    elif len(observations) >= 2 and observations[0]['coverage_status'] == 'complete':
        earlier = _inventory(github._record(ops.db, wid, observations[0]['observation_id'])['document']['evidence']['facts'])
    current_facts = _inventory(github._record(ops.db, wid, current['observation_id'])['document']['evidence']['facts']) if current else None
    last_workload = workloads[-1] if workloads else None
    latest_baseline = baseline['document']
    report = {'schema_version': 1, 'period': period, 'as_of': now,
        'release': {'baseline_id': intent['baseline_id'], 'repository': latest_baseline['repository'],
            'repository_id': latest_baseline['repository_id'], 'commit_sha': latest_baseline['commit_sha'],
            'release_version': latest_baseline['release_version'], 'channel': latest_baseline['channel'],
            'maintenance_branch': latest_baseline['proof']['maintenance_branch'], 'quality': latest_baseline['quality']},
        'policy': {'policy_id': intent['policy_id'], 'version': intent['version'], 'enabled': enabled,
            'scope': intent['scope'], 'allowed_paths': adopted['allowed_paths'], 'delivery': adopted['delivery'],
            'schedule': adopted['schedule'], 'limits': adopted['limits']},
        'lifecycle': {'revision': intent['lifecycle_revision'], 'execution_epoch': intent['execution_epoch']},
        'coverage': {'github': gh_coverage, 'workload': wl_coverage, 'checks': run_coverage, 'repairs': repair_coverage,
            'issues': 'not_observed', 'dependencies': 'not_observed', 'research': 'not_performed',
            'quality': 'unassessed', 'model_analysis': 'not_requested', 'network_refresh': False},
        'observations': observations, 'workloads': workloads, 'checks': runs, 'repairs': repair_rows,
        'changes': {**_changes(earlier, current_facts), 'scope_changed': bool(previous
            and previous['report']['policy']['scope'] != intent['scope'])},
        'comparison_facts': current_facts,
        'current_blockers': last_workload['blocking_reasons'] if last_workload else [],
        'blocker_coverage': 'saved_workload_point_only' if last_workload else 'unchecked',
        'effort': {'basis': 'selected_maintenance_checks', 'completed_checks': sum(run['finished_at'] is not None for run in runs),
            'pending_checks': sum(run['finished_at'] is None for run in runs), 'charged_microusd': None,
            'cost_status': 'unmeasured', 'report_model_requests': 0, 'engineering_effort': 'unknown'},
        'previous_review': previous['context'] if previous else None,
        'next_observation_focus': ['review_source_coverage', 'verify_reported_results'],
        'next_check': {'status': 'schedule_intent_only', 'execution_time': None},
        'decisions': [], 'improvement_candidates': [], 'meeting_completed': False,
        'automatic_maintenance_authorized': False, 'scope_adopted': False}
    report['effort']['repair_counts'] = {'selected': len(repair_rows),
        'capacity_held': sum(row['capacity_held'] for row in repair_rows),
        'runtime_completed': sum(row['runtime_completion'] is not None for row in repair_rows),
        'delivery_outcomes': {outcome: sum(row['outcome'] == outcome for row in repair_rows)
            for outcome in ('published_for_review', 'merged', 'deployed')}}
    finding_report = _finding_report(intent, observations, gh_coverage, now, enabled, previous)
    required_coverage = [gh_coverage, wl_coverage]
    if finding_report:
        report['coverage'].update(finding_report.pop('coverage'))
        report.update(finding_report)
        required_coverage.extend(report['coverage'][kind] for kind in ('issues', 'dependencies')
            if report['coverage'][kind]['status'] != 'disabled')
    if all(coverage['status'] == 'observed' for coverage in required_coverage):
        report['report_status'] = report['changes']['status']
    else:
        report['report_status'] = 'data_gaps'
    refs = unique(refs)
    access(ops, principal, wid, refs)
    report['sources'] = [{'source_id': key(source), **source} for source in refs]
    return report, refs


def timestamp(value, label):
    if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value < 2**53:
        raise bad(label + ' must be a finite nonnegative epoch timestamp', 'INVALID_PARAMS', 422)
    return value
