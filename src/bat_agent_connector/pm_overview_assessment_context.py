"""Freeze only the visible Overview and its minimal original evidence registry."""
from __future__ import annotations

from . import (
    pm_access,
    pm_overview,
    pm_read_cache,
    pm_runs,
    pm_source_lineage,
    pm_thread_contexts,
    pm_workspace,
)
from .operations import OperationError

SCHEMA = 'pm_overview_bundle_v1'
PURPOSE = 'Identify the main goal impact, recorded next steps and decisions from the fixed Overview evidence.'
MAX_CHARS = 32000
MAX_BYTES = 256 * 1024


def error(message, code='PM_OVERVIEW_ASSESSMENT_INVALID', status=422):
    return OperationError(code, message, status)


def _token(ref):
    return 'ref.' + pm_runs._digest(ref)[:24]


def basis(ops, principal, workspace_id):
    """The caller holds one central snapshot; reading never invokes a provider."""
    pm_workspace.require_member(ops.db, principal, workspace_id)
    if not principal.allows('observe'):
        raise error('Overview assessment requires observe scope', 'FORBIDDEN', 403)
    sources, roots = set(), set()

    def retain(value):
        roots.update(pm_source_lineage.document_roots(ops.db, value))
        sources.update(identity for kind, identity in pm_source_lineage.references(value) if kind == 'operation')
        if isinstance(value, dict):
            sources.update(value.get('source_operation_ids', []))

    projection = pm_overview.Projection(ops, principal, workspace_id, retain=retain).read()
    retain(projection)
    refs = {'projection': {'category': 'projection', 'label': 'Overview', 'eligible_templates': [],
        'digest': pm_runs._digest({k: v for k, v in projection.items() if k != 'as_of'})}}
    plan = projection['plan']
    goal = None
    if plan and plan['goal']:
        goal = 'goal'
        ref = {'kind': 'plan_revision', 'id': plan['revision_id']}
        refs[goal] = {'category': 'goal', 'label': plan['goal'], 'ref': ref,
            'digest': pm_runs._digest({'ref': ref, 'goal': plan['goal']}), 'eligible_templates': []}

    def add(ref, label, category, fact, templates=()):
        resolved = pm_thread_contexts.resolve(ops, principal, workspace_id, ref)
        sources.update(resolved['source_operation_ids'])
        retain(resolved['summary'])
        token = _token(ref)
        item = {'category': category, 'label': label, 'ref': ref,
            'digest': pm_runs._digest({'fact': fact, 'source_operation_ids': resolved['source_operation_ids']}),
            'eligible_templates': sorted(templates)}
        refs[token] = item
        return token, item, resolved

    for row in projection['attention']:
        kind = row['kind']
        templates = ['review_evidence']
        if kind == 'decision':
            templates += ['decision_first', 'prepare_options']
            decision_template = {'proposal': 'review_proposal', 'plan_revision': 'confirm_direction',
                'governance': 'choose_response'}.get(row['ref']['kind'])
            if decision_template:
                templates.append(decision_template)
        elif kind in {'risk', 'deviation'}:
            templates.append('watch_exposure')
            if kind == 'risk':
                templates.append('watch_recorded_risk')
            if row.get('reason') == 'forecast_later_than_baseline':
                templates.append('watch_recorded_forecast')
        token, item, resolved = add(row['ref'], row['title'], kind, row, templates)
        relation = row.get('context') or {}
        if kind == 'blocker' and goal and relation.get('current_revision_id') == plan['revision_id']:
            requirements = []
            for identity in relation.get('blocked_requirement_ids') or []:
                ref = {'kind': 'requirement', 'id': identity, 'revision_id': plan['revision_id']}
                value = pm_thread_contexts.resolve(ops, principal, workspace_id, ref)
                req, _, _ = add(ref, value['label'], 'requirement', value['summary'])
                requirements.append(req)
            if requirements:
                item.update(goal_ref=goal, requirement_refs=requirements)
                item['eligible_templates'].append('goal_blocker')
        if row['ref']['kind'] == 'governance':
            record = resolved['summary']
            source_kind = ops.db.execute('SELECT kind FROM pm_governance_records WHERE record_id=?',
                (row['ref']['id'],)).fetchone()[0]
            # A recorded response and accountable member are evidence of an
            # arrangement, never execution permission or a new assignment.
            if (record.get('response') and record.get('owner_actor')
                    and record.get('affected_requirement_ids') and relation.get('blocked_requirement_ids')):
                item['eligible_templates'].append('follow_recorded_action')
            if kind == 'blocker' and source_kind == 'dependency':
                item['eligible_templates'].append('clarify_dependency')

    for row in projection['milestones']:
        token = _token(row['ref'])
        if token not in refs:
            add(row['ref'], row['title'], 'milestone', row,
                ['review_evidence', *(['watch_recorded_forecast'] if row['forecast_date'] else [])])
    for row in projection['recent_deliveries']:
        ref = {'kind': 'work_item', 'id': row['work_item_id']}
        add(ref, row['title'], 'delivery', row, ['review_evidence'])

    limitations = []
    missing = list(projection['unknown'])
    for field, value in projection['coverage']['sources'].items():
        if not value['complete']:
            missing.append({'field': field, 'reason': 'incomplete'})
    for key in ('attention_truncated', 'milestones_truncated', 'recent_decisions_truncated'):
        if projection[key]:
            missing.append({'field': key, 'reason': 'truncated'})
    if not projection['recent_deliveries_complete']:
        missing.append({'field': 'recent_deliveries', 'reason': 'incomplete'})
    for missing_ref in missing:
        token = 'coverage.' + pm_runs._digest(missing_ref)[:24]
        refs[token] = {'category': 'coverage', **missing_ref, 'digest': pm_runs._digest(missing_ref), 'eligible_templates': []}
        if token not in limitations:
            limitations.append(token)
    if len(roots) > pm_source_lineage.MAX_ROOTS or len(sources) > 200:
        raise error('The complete Overview source set exceeds its bound', 'PM_CONTEXT_TOO_LARGE', 413)
    pm_access.require_operation_sources(ops.db, principal, sources)
    if not pm_source_lineage.visible_roots(ops.db, principal, roots):
        raise error('Original Overview sources are unavailable', 'PM_OVERVIEW_SOURCE_UNAVAILABLE', 404)
    value = {'projection': projection, 'source_references': refs, 'source_operation_ids': sorted(sources),
        'source_roots': sorted(roots), 'goal_ref': goal, 'required_limitations': sorted(limitations),
        'eligible_attention': bool(projection['attention'])}
    stable = {**value, 'projection': {key: field for key, field in projection.items() if key != 'as_of'}}
    value['projection_digest'] = pm_runs._digest(stable)
    value['journal_cursor'] = ops.journal.api_head()
    return value


def preview(ops, principal, workspace_id, thread_id, *, max_chars=MAX_CHARS):
    from .pm_overview_assessments import limits
    from .pm_release_baselines import _snapshot
    with _snapshot(ops.db), pm_read_cache.scope(ops.db, principal):
        # Ordinary discussion access remains separate from Overview selection;
        # its messages and bound context are never added to the bundle.
        from . import pm_discussions
        page = pm_discussions.get_thread(ops, principal, workspace_id, thread_id, limit=1, _with_continuation=False)
        value = basis(ops, principal, workspace_id)
        context = freeze(value, workspace_id, thread_id, page['thread']['version'], page['thread']['mode'], max_chars=max_chars)
        return {'schema': SCHEMA, 'workspace_id': workspace_id, 'thread_id': thread_id,
            'selector': {'schema': SCHEMA, 'projection_digest': value['projection_digest']},
            'context': context, 'context_digest': pm_runs._digest(context), 'calls_provider': False,
            'limits': limits(ops.db, workspace_id)}


def freeze(value, workspace_id, thread_id, version, mode, *, max_chars=MAX_CHARS):
    from . import pm_profiles
    if type(max_chars) is not int or not 2000 <= max_chars <= MAX_CHARS:
        raise error('Overview context is bounded to 2000–32000 characters')
    context = {'version': 1, 'schema': SCHEMA, 'workspace_id': workspace_id, 'thread_id': thread_id,
        'thread_version': version, 'profile': pm_profiles.get(mode), 'purpose': PURPOSE,
        'as_of': value['projection']['as_of'], **value, 'output_destination': 'overview_assessment',
        'output_contract_version': 1, 'authority': 'context_only', 'execution_authorized': False,
        'adoption_authorized': False, 'coverage': {**value['projection']['coverage'], 'max_chars': max_chars}}
    encoded = pm_runs._json(context)
    if len(encoded) > max_chars or len(encoded.encode('utf-8')) > MAX_BYTES:
        raise error('The complete Overview context exceeds the selected budget', 'PM_CONTEXT_TOO_LARGE', 413)
    return context
