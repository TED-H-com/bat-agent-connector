"""Explicit, immutable accepted-document context for the existing PM runtimes."""
from __future__ import annotations

import copy
import json
import re

from . import pm_access, pm_discussions, pm_plans, pm_revision_git, pm_runs, pm_workspace, repository_sync
from .operations import OperationError, OperationService

SCHEMA = 'pm_context_bundle_v1'
ACTIONS = frozenset({'pm.run.analyze', 'pm.run.bat.analyze'})
MAX_BYTES = 256 * 1024
# 4,000 valid Unicode characters can each need six JSON bytes after escaping.
# Reserving this in preview keeps its whole-file choices stable for any valid purpose.
PURPOSE_BYTES = 4000 * 6 + 2
_FIELDS = frozenset({'schema', 'revision_id', 'document_digest', 'adoption_operation_id', 'git_receipt_digest'})


def _error(message, code='PM_CONTEXT_BUNDLE_CHANGED', status=409):
    return OperationError(code, message, status)


def _ref(value):
    if (not isinstance(value, dict) or set(value) != _FIELDS or value.get('schema') != SCHEMA
            or not isinstance(value.get('revision_id'), str) or not pm_plans.REVISION_ID.fullmatch(value['revision_id'])
            or not isinstance(value.get('adoption_operation_id'), str)
            or not re.fullmatch(r'op_[0-9a-f]{32}', value['adoption_operation_id'])
            or any(not isinstance(value.get(key), str) or not re.fullmatch(r'[0-9a-f]{64}', value[key])
                for key in ('document_digest', 'git_receipt_digest'))):
        raise _error('Select the exact original accepted-document bundle', 'INVALID_PARAMS', 422)
    return copy.deepcopy(value)


def _selected(db, principal, workspace_id, ref=None, *, current=False):
    if not principal.allows('observe'):
        raise _error('Accepted context requires observe scope', 'FORBIDDEN', 403)
    pm_workspace.require_member(db, principal, workspace_id)
    head = pm_plans.head(db, workspace_id)
    ref = _ref(ref) if ref is not None else None
    rid = ref['revision_id'] if ref else head['accepted_revision_id']
    if not rid:
        raise _error('No accepted plan is available', 'PM_ACCEPTED_PLAN_REQUIRED')
    if current and rid != head['accepted_revision_id']:
        raise _error('The accepted plan changed; review a new bundle preview')
    revision = pm_plans._revision(db, workspace_id, rid)
    files = pm_plans.revision_files(db, workspace_id, rid)
    query = 'SELECT * FROM pm_plan_adoptions WHERE workspace_id=? AND revision_id=?'
    args = [workspace_id, rid]
    if ref:
        query += ' AND operation_id=?'
        args.append(ref['adoption_operation_id'])
    adopted = db.execute(query + ' ORDER BY rowid DESC LIMIT 1', args).fetchone()
    if not adopted:
        raise _error('The exact plan has no adoption decision', 'PM_ACCEPTED_PLAN_REQUIRED')
    decision = json.loads(adopted['result'])
    receipt_row = db.execute('SELECT receipt FROM pm_plan_git_receipts WHERE operation_id=? AND workspace_id=? '
        'AND revision_id=?', (decision['commit_operation_id'], workspace_id, rid)).fetchone()
    receipt = json.loads(receipt_row[0]) if receipt_row else None
    carrier = pm_runs.workspace_worktree(db, workspace_id)
    if (not receipt or not carrier or 'source_operation_id' in receipt
            or receipt.get('operation_id') != decision['commit_operation_id']
            or receipt.get('document_digest') != files['document_digest']
            or receipt.get('file_digests') != files['file_digests']
            or receipt.get('commit_sha') != decision['commit_sha'] or receipt.get('tree_sha') != decision['tree_sha']
            or receipt.get('binding_id') != pm_revision_git.binding_id(carrier)
            or receipt.get('status') != 'verified'):
        raise _error('Original adopted files and Git creation evidence are unavailable')
    selected = {'schema': SCHEMA, 'revision_id': rid, 'document_digest': files['document_digest'],
        'adoption_operation_id': adopted['operation_id'], 'git_receipt_digest': pm_runs._digest(receipt)}
    if ref is not None and selected != ref:
        raise _error('The original adopted document or Git receipt differs')
    sources = sorted(set(pm_plans.source_operation_ids(db, workspace_id, rid)) | {
        revision['operation_id'], adopted['operation_id'], receipt['operation_id'], carrier['creation_operation_id']})
    pm_access.require_operation_sources(db, principal, sources)
    binding = {'ref': selected, 'source_operation_ids': sources,
        'carrier_digest': pm_runs._digest(carrier), 'carrier_creation_operation_id': carrier['creation_operation_id'],
        'git_receipt': receipt, 'decision': {key: decision[key] for key in (
            'revision_id', 'document_digest', 'decision_id', 'scope_revision', 'structured_scope_digest',
            'scope_impact', 'adopted_by', 'commit_operation_id')},
        'files': [{'path': path, 'digest': files['file_digests'][path], 'utf8_bytes': len(text.encode('utf-8'))}
            for path, text in sorted(files['files'].items())]}
    return binding, files['files'], carrier


def _current_configuration(ops, carrier):
    if repository_sync.digest(repository_sync.config_snapshot(ops, carrier['target'])) != carrier['config_digest']:
        raise _error('The original PM repository configuration changed', 'REPOSITORY_BINDING_CHANGED')


def admission(ops, principal, workspace_id, params):
    if 'context_bundle_ref' not in params:
        return {}
    binding, _, carrier = _selected(ops.db, principal, workspace_id, params['context_bundle_ref'], current=True)
    _current_configuration(ops, carrier)
    return {'pm_context_bundle': binding}


def _size(value):
    return len(pm_runs._json(value).encode('utf-8'))


def _seal(value):
    items = [*value['messages'], *(item for group in value['inputs_by_status'].values() for item in group)]
    value['coverage']['content_chars'] = sum(len(json.dumps(item, ensure_ascii=False)) for item in items)
    value['coverage']['discussion_utf8_bytes'] = sum(_size(item) for item in items)
    return _size(value) - _size(value['purpose']) + PURPOSE_BYTES


def _context(ops, principal, workspace_id, thread_id, binding, files, *, max_chars, purpose):
    original = pm_discussions.context(ops, principal, workspace_id, thread_id, max_chars=max_chars)
    context = copy.deepcopy(original)
    context.update(version=2, purpose=purpose, messages=[],
        inputs_by_status={key: [] for key in original['inputs_by_status']},
        source_operation_ids=sorted(set(original.get('source_operation_ids', [])) | set(binding['source_operation_ids'])))
    manifest = [{**entry, 'included': False} for entry in binding['files']]
    context['context_bundle'] = {'schema': SCHEMA, 'ref': binding['ref'], 'decision': binding['decision'],
        'git_receipt': binding['git_receipt'], 'source_operation_ids': binding['source_operation_ids'],
        'manifest': manifest, 'files': {}, 'coverage': {'accepted_document_complete': False,
            'acceptance_coverage': 'incomplete_original_document', 'execution_authorized': False,
            'adoption_authorized': False}}
    coverage = context['coverage']
    coverage.update(max_utf8_bytes=MAX_BYTES, purpose_budget_bytes=PURPOSE_BYTES, content_chars=0,
        omitted_message_ids=list(original['coverage']['omitted_message_ids']),
        omitted_input_ids=list(original['coverage']['omitted_input_ids']))
    for group in original['inputs_by_status'].values():
        coverage['omitted_input_ids'].extend(item['input_id'] for item in group)
    coverage['omitted_message_ids'].extend(item['message_id'] for item in original['messages'])
    if _seal(context) > MAX_BYTES:
        raise _error('Context source identities exceed the complete UTF-8 budget', 'PM_CONTEXT_TOO_LARGE', 413)
    # Metadata preserves exact acceptance/source identities; Markdown remains
    # part of the adopted document, so omitting either file is incomplete.
    for entry in sorted(manifest, key=lambda item: (not item['path'].endswith('/plan.json'), item['path'])):
        entry['included'] = True
        context['context_bundle']['files'][entry['path']] = files[entry['path']]
        if _seal(context) > MAX_BYTES:
            entry['included'] = False
            del context['context_bundle']['files'][entry['path']]
    for status, group in original['inputs_by_status'].items():
        for item in group:
            context['inputs_by_status'][status].append(item)
            coverage['omitted_input_ids'].remove(item['input_id'])
            if _seal(context) > MAX_BYTES:
                context['inputs_by_status'][status].pop()
                coverage['omitted_input_ids'].append(item['input_id'])
    for item in reversed(original['messages']):
        context['messages'].insert(0, item)
        coverage['omitted_message_ids'].remove(item['message_id'])
        if _seal(context) > MAX_BYTES:
            context['messages'].pop(0)
            coverage['omitted_message_ids'].append(item['message_id'])
    complete = all(entry['included'] for entry in manifest)
    context['context_bundle']['coverage'].update(accepted_document_complete=complete,
        acceptance_coverage='complete_original_document' if complete else 'incomplete_original_document')
    if _seal(context) > MAX_BYTES:
        raise _error('Context exceeds the complete UTF-8 budget', 'PM_CONTEXT_TOO_LARGE', 413)
    return context


def preview(ops, principal, workspace_id, thread_id, *, max_chars=32000):
    with ops.journal.tx():
        binding, files, carrier = _selected(ops.db, principal, workspace_id, current=True)
        _current_configuration(ops, carrier)
        context = _context(ops, principal, workspace_id, thread_id, binding, files,
            max_chars=max_chars, purpose='')
        return {'schema': 'pm_context_bundle_preview_v1', 'workspace_id': workspace_id, 'thread_id': thread_id,
            'thread_version': context['thread_version'], 'context_bundle_ref': binding['ref'],
            'decision': binding['decision'], 'git_receipt': binding['git_receipt'],
            'source_operation_ids': binding['source_operation_ids'], 'manifest': context['context_bundle']['manifest'],
            'coverage': {**context['context_bundle']['coverage'], 'max_utf8_bytes': MAX_BYTES,
                'preview_utf8_bytes': _size(context), 'purpose_budget_bytes': PURPOSE_BYTES},
            'authority': 'context_only', 'execution_authorized': False}


def for_operation(ops, principal, operation_id, *, max_chars, purpose):
    row = ops.db.execute('SELECT * FROM operations WHERE operation_id=?', (operation_id,)).fetchone()
    if row is None:
        return None
    op = OperationService._decode(row)
    if op['action'] not in ACTIONS or 'context_bundle_ref' not in op['params']:
        return None
    binding, files, carrier = _selected(ops.db, principal, op['target']['workspace_id'],
        op['params']['context_bundle_ref'], current=True)
    saved = (op['external_refs'].get('admission_binding') or {}).get('pm_context_bundle')
    if saved != binding:
        raise _error('The original operation does not bind this accepted-document context')
    _current_configuration(ops, carrier)
    return _context(ops, principal, **op['target'], binding=binding, files=files, max_chars=max_chars, purpose=purpose)


def operation_visible(db, principal, op):
    try:
        row = db.execute('SELECT * FROM operations WHERE operation_id=?', (op['operation_id'],)).fetchone()
        if row is None:
            return False
        original = OperationService._decode(row)
        if original['action'] not in ACTIONS or 'context_bundle_ref' not in original['params']:
            return True
        binding, _, _ = _selected(db, principal, original['target']['workspace_id'], original['params']['context_bundle_ref'])
        return binding == (original['external_refs'].get('admission_binding') or {}).get('pm_context_bundle')
    except (OperationError, KeyError, TypeError, ValueError, UnicodeError):
        return False


def require_current(ops, principal, run):
    if run['context'].get('version') != 2 or 'context_bundle' not in run['context']:
        return
    if not operation_visible(ops.db, principal, {'operation_id': run['operation_id']}):
        raise _error('Original accepted-document sources are unavailable', 'PM_CONTEXT_SOURCE_UNAVAILABLE', 404)
    carrier = pm_runs.workspace_worktree(ops.db, run['workspace_id'])
    _current_configuration(ops, carrier)
