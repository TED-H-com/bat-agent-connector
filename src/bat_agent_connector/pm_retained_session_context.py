"""Fit exact saved conversation evidence into an existing PM continuation turn."""
from __future__ import annotations

import copy

from . import pm_retained_session_source as source
from . import pm_runs
from .operations import OperationError

CONTEXT_VERSION = 1


def attach(ops, principal, context, ref):
    """Attach a bounded chronological suffix, preserving caller dictionary aliases.

    Selection and source bodies are frozen together before model execution. Whole
    saved bodies either fit or are omitted; this layer never silently clips text.
    """
    saved = source.resolve(ops.db, principal, ref)
    candidate = copy.deepcopy(context)
    refs = candidate['source_references']
    candidate['coverage']['partial'] = True
    included = []

    def metadata():
        omitted = len(saved['messages']) - len(included)
        return {'version': CONTEXT_VERSION, 'ref': saved['ref'], 'saved_at': saved['saved_at'],
            'selection': source.selection(saved['ref'], [[row['id'], row['revision']] for row in included]),
            'coverage': {**saved['coverage'], 'included_message_count': len(included),
                'omitted_message_count': saved['coverage']['omitted_message_count'] + omitted,
                'clipped_message_count': sum(row['clipped'] for row in included), 'partial': True},
            'omitted_for_context_count': omitted, 'execution_status': 'unknown'}

    candidate['retained_session'] = metadata()
    for row in reversed(saved['messages']):
        key = 'retained.' + pm_runs._digest({'ref': saved['ref'], 'id': row['id'], 'revision': row['revision']})
        prior = refs.get(key)
        refs[key] = {'kind': 'retained_session_message', 'id': row['id'], 'revision': row['revision'],
            'host': saved['ref']['host'], 'session_id': saved['ref']['session_id'],
            'binding': saved['ref']['binding'], 'snapshot_id': saved['ref']['snapshot_id'],
            'retained_session_ref': saved['ref'], 'role': row['role'], 'quote': row['text'],
            'digest': pm_runs._digest(row), 'source_digest': row['source_digest'],
            'clipped': row['clipped'], 'disposition': 'context'}
        included.insert(0, row)
        candidate['retained_session'] = metadata()
        if len(pm_runs._json(candidate)) > context['coverage']['max_chars']:
            if prior is None:
                del refs[key]
            else:
                refs[key] = prior
            included.pop(0)
            candidate['retained_session'] = metadata()
            break
    if not included or len(pm_runs._json(candidate)) > context['coverage']['max_chars']:
        raise OperationError('PM_CONTEXT_TOO_LARGE',
            'Saved conversation and current decision facts exceed the context budget', 413)
    # build() retains aliases to these dictionaries while adding PM messages.
    context['source_references'].clear()
    context['source_references'].update(refs)
    context['coverage'].update(candidate['coverage'])
    context['retained_session'] = candidate['retained_session']
