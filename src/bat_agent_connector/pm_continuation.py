"""Versioned continuation briefs in the existing immutable PM run receipts."""
from __future__ import annotations

import hashlib

from . import pm_analysis_adapters as adapters
from . import pm_continuation_format as fmt
from . import pm_native_operations, pm_plans, pm_runs, pm_workspace
from .operations import ActionDef, OperationError, OperationService
from .pm_continuation_context import build, error

ACTION = 'pm.continuation.prepare'


def install(ops):
    from . import pm_bat_runtime, pm_native_journal
    pm_bat_runtime.schema(ops.journal)
    pm_native_journal.schema(ops.journal)
    pm_plans.schema(ops.journal)
    with ops.journal.tx():
        ops.db.execute("""CREATE INDEX IF NOT EXISTS pm_continuation_thread
            ON pm_runs(workspace_id,thread_id,generation DESC)
            WHERE json_extract(context,'$.output_destination')='continuation_brief'""")
    if ACTION not in ops.actions:
        ops.register(ActionDef(ACTION, 'manage', 'Prepare a sourced continuation brief without adopting decisions',
            _run, _admit, ('workspace_id', 'thread_id'), authorize_existing=_authorize_existing))


def _base(ops, principal, target, params, pre):
    if (set(target) != {'workspace_id', 'thread_id'}
            or set(params) - {'purpose', 'model', 'max_chars', 'timeout_s', 'adapter', 'previous_brief_ref'}
            or set(pre) != {'expected_version', 'expected_plan_version', 'expected_accepted_revision_id'}):
        raise error('Supply one discussion and exact discussion/plan versions')
    pm_native_operations._values(ops, principal, target, adapters.ordinary_params(params),
        {'expected_version': pre['expected_version']})
    head = pm_plans.head(ops.db, target['workspace_id'])
    if (type(pre['expected_plan_version']) is not int or head['version'] != pre['expected_plan_version']
            or head['accepted_revision_id'] != pre['expected_accepted_revision_id']):
        raise error('The accepted plan changed; read it before summarizing', 'VERSION_CONFLICT', 409)
    return head


def _admit(ops, principal, target, params, pre):
    head = _base(ops, principal, target, params, pre)
    context = build(ops, principal, target, params, pre, head)
    return {**adapters.admission(ops, principal, target, params, pre),
        'continuation_brief': {'context': context, 'context_digest': pm_runs._digest(context)}}


def operation_visible(db, principal, operation):
    from .pm_source_reads import check
    return check(db, principal, 'continuation', operation['operation_id'],
        lambda: _visible(db, principal, operation['operation_id']))


def _visible(db, principal, operation_id):
    from . import pm_access, pm_thread_contexts
    try:
        row = db.execute('SELECT * FROM operations WHERE operation_id=?', (operation_id,)).fetchone()
        op = OperationService._decode(row) if row else None
        if not op or op['action'] != ACTION or not principal.allows('observe'):
            return False
        pm_workspace.require_member(db, principal, op['target']['workspace_id'])
        frozen = op['external_refs']['admission_binding']['continuation_brief']
        context = frozen['context']
        if pm_runs._digest(context) != frozen['context_digest']:
            return False
        if not pm_thread_contexts.frozen_visible(db, principal, op['target']['workspace_id'], context):
            return False
        for identity in context['source_operation_ids']:
            row = db.execute('SELECT * FROM operations WHERE operation_id=?', (identity,)).fetchone()
            if not row or not pm_access.visible(db, principal, OperationService._decode(row)):
                return False
        return True
    except (OperationError, KeyError, TypeError, ValueError, RecursionError):
        return False


def require_visible(db, principal, operation):
    if not operation_visible(db, principal, operation):
        raise error('Original continuation sources are unavailable', 'PM_CONTINUATION_SOURCE_UNAVAILABLE', 404)


def _authorize_existing(ops, principal, op, verb):
    pm_native_operations._authorize_existing(ops, principal, op, verb)
    require_visible(ops.db, principal, op)


def _prepare(ctx, principal, authority):
    require_visible(ctx.service.db, principal, ctx.op)
    _base(ctx.service, principal, ctx.target, ctx.params, ctx.preconditions)
    context = ctx.admission_binding['continuation_brief']['context']
    run = pm_runs.create(ctx.service, principal, operation_id=ctx.operation_id, **ctx.target,
        expected_version=ctx.preconditions['expected_version'], adapter=adapters.selection(ctx.params),
        purpose=ctx.params['purpose'], execution_authority=authority, max_chars=ctx.params.get('max_chars', 32000),
        _context_factory=lambda: context)
    return adapters.prepare(ctx, run, fmt.prompt(run))


async def _run(ctx):
    return await adapters.run_analysis(ctx, prepare=_prepare, import_reply=_import)


def brief(run):
    if run['context'].get('output_destination') != fmt.DESTINATION or run['state'] != 'completed':
        return None
    receipt = next((r for r in run['receipts'] if r['phase'] == 'import' and r['status'] == 'confirmed'), None)
    if not receipt or 'brief' not in receipt['data']:
        raise error('Original continuation receipt is missing', 'PM_CONTINUATION_CHANGED', 409)
    value = receipt['data']['brief']
    if (value['context_digest'] != run['context_digest'] or pm_runs._digest(run['context']) != run['context_digest']
            or hashlib.sha256(value['raw_reply'].encode()).hexdigest() != value['raw_reply_digest']
            or (pm_runs._digest(value['document']) if value['document'] is not None else None) != value['document_digest']):
        raise error('Original continuation receipt changed', 'PM_CONTINUATION_CHANGED', 409)
    return value


def _import(journal, run_id, generation):
    from .pm_follow_up_analysis import _checked, _public_proof
    with journal.tx():
        run = pm_runs.get(journal.db, run_id)
        if run['generation'] != generation or run['context'].get('output_destination') != fmt.DESTINATION:
            raise error('The original continuation writer is required', 'PM_OUTPUT_DESTINATION_REFUSED', 409)
        if run['state'] == 'completed':
            brief(run)
            return run
        run, observed, proof = adapters.verified_reply(journal, run_id, generation)
        raw = observed['content']
        document, validation = fmt.validate(raw, run['context'])
        # Preserve invalid bounded replies for review without publishing them as
        # a usable continuation. Oversized output remains in original run events.
        retained = raw[:fmt.MAX_REPLY_CHARS]
        value = {'context_digest': run['context_digest'], 'raw_reply': retained,
            'raw_reply_digest': hashlib.sha256(retained.encode()).hexdigest(), 'raw_reply_truncated': len(raw) > len(retained),
            'document': document, 'document_digest': pm_runs._digest(document) if document is not None else None,
            'validation': validation, 'provenance': {**_public_proof(proof), 'actual_model': observed['actual_model'],
                'native_turn_id': observed['native_turn_id'], 'native_message_id': observed['native_message_id']}}
        _checked(value)
        outcome = {'continuation_run_id': run_id, 'document_digest': value['document_digest'],
            'validation_status': validation['status'], 'assistant_imported': False,
            'accepted_plan_changed': False, 'conclusions_verified': False, 'execution_authorized': False}
        pm_runs.receipt(journal, run_id, generation, 'import', 'confirmed', {**outcome, 'brief': value})
        pm_runs._release(journal.db, run, 'completed', outcome)
        journal.api_event('pm_continuation', run_id, ACTION,
            {'workspace_id': run['workspace_id'], 'thread_id': run['thread_id'], 'operation_id': run['operation_id']},
            actor=run['requester'])
        return pm_runs.get(journal.db, run_id)


def latest(ops, principal, workspace_id, thread_id):
    # One newest immutable completion, never an older cached success when the
    # latest source is withdrawn. Raw output and full context stay detail-only.
    if not ops.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='pm_runs'").fetchone():
        return None
    row = ops.db.execute("""SELECT r.run_id FROM pm_runs r JOIN pm_run_state s USING(run_id)
        WHERE r.workspace_id=? AND r.thread_id=? AND s.status='completed'
        AND json_extract(r.context,'$.output_destination')='continuation_brief'
        ORDER BY r.generation DESC LIMIT 1""", (workspace_id, thread_id)).fetchone()
    if not row:
        return None
    run = pm_runs.get(ops.db, row[0])
    if not pm_runs.source_visible(ops.db, principal, run):
        return {'available': False}
    value = brief(run)
    return {'available': True, 'run_id': run['run_id'], 'operation_id': run['operation_id'],
        'document': value['document'], 'document_digest': value['document_digest'], 'validation': value['validation'],
        'as_of': run['context']['as_of'], 'coverage': run['context']['coverage'],
        'source_references': {key: {name: field for name, field in ref.items() if name != 'quote'}
            for key, ref in run['context']['source_references'].items()},
        'thread_version': run['thread_version'], 'plan_head': run['context']['plan_head'],
        'conclusions_verified': False, 'execution_authorized': False}
