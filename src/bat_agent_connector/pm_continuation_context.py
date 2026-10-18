"""Bounded continuation context, preserving original access and decision facts."""
from __future__ import annotations

import time

from . import pm_discussions, pm_plans, pm_profiles, pm_runs, pm_thread_contexts
from .operations import OperationError


def error(message, code='PM_CONTINUATION_INVALID', status=422):
    return OperationError(code, message, status)


def build(ops, principal, target, params, pre, head):
    from . import pm_continuation
    page = pm_discussions.get_thread(ops, principal, **target, limit=100, _with_continuation=False)
    if page['coverage']['inputs_truncated']:
        raise error('Current decision inputs exceed the continuation bound', 'PM_CONTEXT_TOO_LARGE', 413)
    accepted = pm_plans.accepted_plan(ops, principal, target['workspace_id'])['plan']
    previous, after = None, 0
    refs, sources = {}, set()
    if selection := params.get('previous_brief_ref'):
        if not isinstance(selection, dict) or set(selection) != {'run_id', 'document_digest'}:
            raise error('Select the exact previous continuation run and document digest')
        run = pm_runs.read(ops, principal, target['workspace_id'], selection['run_id'])
        value = pm_continuation.brief(run)
        if (run['thread_id'] != target['thread_id'] or not value or value['document'] is None
                or value['document_digest'] != selection['document_digest']):
            raise error('The original continuation is unavailable', 'PM_CONTINUATION_SOURCE_UNAVAILABLE', 404)
        previous = {**selection, 'document': value['document'], 'as_of': run['context']['as_of'],
            'coverage': run['context']['coverage'], 'plan_head': run['context']['plan_head']}
        after = run['context']['coverage']['through_message']
        sources.add(run['operation_id'])
        # Retain only identities/digests cited by the brief, not old transcripts.
        cited = set()
        for key, rows in value['document'].items():
            if key == 'goal':
                rows = [rows] if rows else []
            if isinstance(rows, list):
                cited.update(ref for row in rows for ref in row.get('source_refs', []))
        for key in cited:
            ref = {name: field for name, field in run['context']['source_references'][key].items() if name != 'quote'}
            if ref['disposition'] == 'adopted':
                ref['disposition'] = 'historical_decision'
            if ref['kind'] == 'input' and ops.db.execute(
                    'SELECT 1 FROM pm_inputs WHERE supersedes_input_id=?', (ref['id'],)).fetchone():
                ref['disposition'] = 'superseded'
            refs[key] = ref
    bound = pm_thread_contexts.read(ops, principal, target['workspace_id'], target['thread_id'], summaries=True)
    if any(not row['available'] for row in bound):
        raise pm_thread_contexts.unavailable()
    decisions = {'adopted': [], 'rejected': []}
    def add(key, kind, identity, value, quote, operation_id, disposition='context'):
        refs[key] = {'kind': kind, 'id': identity, 'digest': pm_runs._digest(value),
            'quote': quote, 'operation_id': operation_id, 'disposition': disposition}
        sources.add(operation_id)
        if disposition in decisions:
            decisions[disposition].append({'text': quote, 'source_refs': [key]})
    inputs = {row['input_id']: row for row in [*page['inputs'], *((page['draft'] or {}).get('selected_inputs', []))]}
    selected = {row['input_id'] for row in accepted['document']['selected_inputs']} if accepted else set()
    if accepted:
        for row in accepted['document']['selected_inputs']:
            inputs.setdefault(row['input_id'], row)
        add('accepted_plan', 'plan', accepted['revision_id'], accepted,
            accepted['document']['markdown'], accepted['operation_id'])
        adoption = ops.db.execute('SELECT operation_id FROM pm_plan_adoptions WHERE workspace_id=? AND revision_id=? ORDER BY rowid DESC LIMIT 1',
            (target['workspace_id'], accepted['revision_id'])).fetchone()
        sources.add(adoption[0])
    for row in inputs.values():
        excluded = row['status'] == 'not_applicable' or row.get('review_disposition', {}).get('included') is False
        disposition = 'rejected' if excluded else 'adopted' if row['input_id'] in selected and row['status'] == 'user_confirmed' else 'candidate'
        add('input.' + row['input_id'], 'input', row['input_id'], row, row['value'], row['operation_id'], disposition)
    for row in bound:
        sources.update(row['source_operation_ids'])
        add('context.' + str(row['position']), row['kind'], row['id'], row,
            pm_runs._json(row['summary']), row['operation_id'], 'evidence' if row['kind'] == 'quality_assessment' else 'context')
    messages = [row for row in page['messages'] if row['sequence'] > after]
    through = max([after, *(row['sequence'] for row in messages)])
    coverage = {'max_chars': params.get('max_chars', 32000), 'scope': 'previous_brief_and_recent_changes',
        'after_message': after, 'through_message': through, 'included_message_ids': [],
        'omitted_message_ids': [row['message_id'] for row in messages], 'older_messages_omitted': bool(page['coverage']['messages_truncated']
            and page['messages'][0]['sequence'] > after + 1),
        'previous_partial': bool(previous and previous['coverage']['partial']),
        'semantic_verification': 'unassessed', 'network_checked': False, 'partial': False}
    # Sequences are global: query one older row rather than infer a gap from ID arithmetic.
    if messages:
        coverage['older_messages_omitted'] = bool(ops.db.execute(
            'SELECT 1 FROM pm_messages WHERE thread_id=? AND sequence>? AND sequence<? LIMIT 1',
            (target['thread_id'], after, messages[0]['sequence'])).fetchone())
    result = {'version': 1, **target, 'thread_version': pre['expected_version'], 'purpose': params['purpose'],
        'as_of': time.time(), 'plan_head': head, 'accepted_revision_id': head['accepted_revision_id'],
        'profile': pm_profiles.get(page['thread']['mode']), 'output_destination': 'continuation_brief',
        'output_contract_version': 1, 'previous_brief': previous, 'decisions': decisions,
        'bound_context': bound, 'source_references': refs, 'source_operation_ids': [], 'coverage': coverage,
        'authority': 'context_only', 'execution_authorized': False}
    def fits():
        result['source_operation_ids'] = sorted(sources)
        return len(pm_runs._json(result)) <= coverage['max_chars']
    # Freeze all coverage/source metadata before fitting recent text, so the
    # budget is exact even when an unusually long message cannot be included.
    sources.update(row['operation_id'] for row in messages)
    if not fits():
        raise error('Decision facts and previous brief exceed the context budget', 'PM_CONTEXT_TOO_LARGE', 413)
    for row in reversed(messages):
        key = 'message.' + row['message_id']
        add(key, 'message', row['message_id'], row, row['content'], row['operation_id'])
        coverage['included_message_ids'].insert(0, row['message_id'])
        coverage['omitted_message_ids'].remove(row['message_id'])
        if not fits():
            del refs[key]
            coverage['included_message_ids'].remove(row['message_id'])
            coverage['omitted_message_ids'].append(row['message_id'])
    coverage['partial'] = bool(coverage['previous_partial'] or coverage['older_messages_omitted'] or coverage['omitted_message_ids'])
    if not refs or not fits():
        raise error('Continuation context exceeds the selected budget', 'PM_CONTEXT_TOO_LARGE', 413)
    from .pm_access import require_operation_sources
    require_operation_sources(ops.db, principal, sources)
    return result
