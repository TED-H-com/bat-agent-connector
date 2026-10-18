"""One explicit, immutable Overview assessment through existing PM runtimes."""
from __future__ import annotations

import base64
import hashlib
import json
import re
import time

from . import pm_analysis_adapters as adapters
from . import pm_native_operations, pm_read_cache, pm_runs, pm_workspace
from . import pm_overview_assessment_context as bundle
from . import pm_overview_assessment_format as fmt
from .operations import ActionDef, OperationError, OperationService

ACTION = 'pm.overview.assess'
RATE_WINDOWS = ((60, 1), (3600, 6), (86400, 24))
ID = re.compile(r'pmoa_[0-9a-f]{32}')


def schema(journal):
    from . import pm_bat_runtime, pm_native_journal
    pm_bat_runtime.schema(journal)
    pm_native_journal.schema(journal)
    with journal.tx():
        journal.db.execute('''CREATE TABLE IF NOT EXISTS pm_overview_assessments (
            sequence INTEGER PRIMARY KEY AUTOINCREMENT, assessment_id TEXT NOT NULL UNIQUE,
            workspace_id TEXT NOT NULL, thread_id TEXT NOT NULL, operation_id TEXT NOT NULL UNIQUE,
            run_id TEXT NOT NULL UNIQUE REFERENCES pm_runs(run_id), projection_digest TEXT NOT NULL,
            document TEXT NOT NULL)''')
        journal.db.execute('''CREATE INDEX IF NOT EXISTS pm_overview_assessments_workspace
            ON pm_overview_assessments(workspace_id,sequence DESC)''')
        journal.db.execute('''CREATE INDEX IF NOT EXISTS pm_overview_admissions
            ON operations(json_extract(target,'$.workspace_id'),created_at)
            WHERE action='pm.overview.assess' ''')
        for verb in ('UPDATE', 'DELETE'):
            journal.db.execute(f'CREATE TRIGGER IF NOT EXISTS pm_overview_assessments_{verb.lower()}_immutable '
                f"BEFORE {verb} ON pm_overview_assessments BEGIN SELECT RAISE(ABORT,'PM assessment is immutable'); END")


def install(ops):
    schema(ops.journal)
    if ACTION not in ops.actions:
        ops.register(ActionDef(ACTION, 'manage', 'Assess fixed Overview evidence without adopting or executing work',
            _run, _admit, ('workspace_id', 'thread_id'), authorize_existing=_authorize_existing))


def allowance(db, workspace_id, *, now=None):
    now = time.time() if now is None else now
    rows = [row[0] for row in db.execute('''SELECT created_at FROM operations WHERE action=?
        AND json_extract(target,'$.workspace_id')=? AND created_at>? ORDER BY created_at DESC LIMIT 24''',
        (ACTION, workspace_id, now - 86400))]
    retry = max((rows[count - 1] + window for window, count in RATE_WINDOWS
        if len(rows) >= count and rows[count - 1] > now - window), default=None)
    return {'allowed': retry is None, 'retry_at': retry}


def limits(db, workspace_id):
    return {'max_chars': bundle.MAX_CHARS, 'max_context_bytes': bundle.MAX_BYTES, 'timeout_s': 300,
        'max_output_bytes': fmt.MAX_OUTPUT_BYTES, 'turns': 1, 'automatic_retries': 0,
        'admissions': [{'window_s': w, 'count': n} for w, n in RATE_WINDOWS],
        'token_cap': None, 'monetary_cap': None, **allowance(db, workspace_id)}


def capabilities(ops, principal, workspace, *, native=None, bat=None):
    advertised = adapters.capabilities(ops, principal, workspace, native=native, bat=bat)['actions'][ACTION]
    return {'version': 1, 'action': ACTION, 'bundle_schema': bundle.SCHEMA, 'output_version': 1,
        'adapters': advertised['adapters'], 'action_registered': ACTION in ops.actions,
        'authorized': any(item['authorized'] for item in advertised['adapters']),
        'eligible_for_preflight': any(item['action_registered'] and item['eligible_for_preflight']
            for item in advertised['adapters']),
        'limits': limits(ops.db, workspace['workspace_id']), 'conclusions_verified': False,
        'execution_authorized': False, 'calls_provider_on_read': False}


def _ordinary(params):
    return {'purpose': bundle.PURPOSE, **{key: value for key, value in params.items() if key != 'context_bundle_ref'}}


def _values(ops, principal, target, params, pre):
    if (set(target) != {'workspace_id', 'thread_id'} or set(pre) != {'expected_version'}
            or set(params) - {'adapter', 'context_bundle_ref', 'model', 'max_chars', 'timeout_s'}
            or not {'adapter', 'context_bundle_ref'} <= set(params)):
        raise bundle.error('Select an explicit analysis adapter and original Overview selector')
    selector = params['context_bundle_ref']
    if (not isinstance(selector, dict) or set(selector) != {'schema', 'projection_digest'}
            or selector['schema'] != bundle.SCHEMA or not isinstance(selector['projection_digest'], str)
            or not re.fullmatch('[0-9a-f]{64}', selector['projection_digest'])):
        raise bundle.error('Use the original Overview preview selector')
    pm_native_operations._values(ops, principal, target, adapters.ordinary_params(_ordinary(params)), pre)
    if params.get('max_chars', bundle.MAX_CHARS) > bundle.MAX_CHARS or params.get('timeout_s', 300) > 300:
        raise bundle.error('Overview assessment allows at most 32000 context characters and 300 seconds')


def _admit(ops, principal, target, params, pre):
    _values(ops, principal, target, params, pre)
    preview = bundle.preview(ops, principal, **target, max_chars=params.get('max_chars', bundle.MAX_CHARS))
    if preview['selector'] != params['context_bundle_ref']:
        raise bundle.error('The Overview changed; preview it before assessing', 'VERSION_CONFLICT', 409)
    return {**adapters.admission(ops, principal, target, _ordinary(params), pre),
        'overview_assessment': {'context': preview['context'], 'context_digest': preview['context_digest']}}


def admit_operation(ops, principal, action, target, params, pre, binding):
    """Recheck/freeze inside the intent INSERT transaction across connections."""
    if action != ACTION:
        return
    _values(ops, principal, target, params, pre)
    preview = bundle.preview(ops, principal, **target, max_chars=params.get('max_chars', bundle.MAX_CHARS))
    if preview['selector'] != params['context_bundle_ref']:
        raise bundle.error('The Overview changed; preview it before assessing', 'VERSION_CONFLICT', 409)
    admitted_at = time.time()
    rate = allowance(ops.db, target['workspace_id'], now=admitted_at)
    if not rate['allowed']:
        raise bundle.error(f"Assessment limit reached; retry_at={rate['retry_at']:.3f}", 'PM_OVERVIEW_RATE_LIMIT', 429)
    binding['overview_assessment'] = {'context': preview['context'], 'context_digest': preview['context_digest']}
    return admitted_at


def operation_visible(db, principal, operation):
    from .pm_source_reads import check
    return check(db, principal, 'overview_assessment', operation['operation_id'],
        lambda: _visible(db, principal, operation['operation_id']))


def _visible(db, principal, identity):
    from . import pm_access, pm_source_lineage, pm_thread_contexts
    try:
        row = db.execute('SELECT * FROM operations WHERE operation_id=?', (identity,)).fetchone()
        op = OperationService._decode(row) if row else None
        if not op or op['action'] != ACTION or not principal.allows('observe'):
            return False
        pm_workspace.require_member(db, principal, op['target']['workspace_id'])
        frozen = op['external_refs']['admission_binding']['overview_assessment']
        context = frozen['context']
        if (context['schema'] != bundle.SCHEMA or context['output_destination'] != fmt.DESTINATION
                or context['projection_digest'] != op['params']['context_bundle_ref']['projection_digest']
                or context['workspace_id'] != op['target']['workspace_id']
                or context['thread_id'] != op['target']['thread_id']
                or pm_runs._digest(context) != frozen['context_digest']
                or not pm_thread_contexts.frozen_visible(db, principal, context['workspace_id'], context)
                or not pm_source_lineage.visible_roots(db, principal, context['source_roots'])):
            return False
        for source in context['source_operation_ids']:
            row = db.execute('SELECT * FROM operations WHERE operation_id=?', (source,)).fetchone()
            if not row or not pm_access.visible(db, principal, OperationService._decode(row)):
                return False
        return True
    except (OperationError, KeyError, TypeError, ValueError, RecursionError):
        return False


def _require_visible(db, principal, operation):
    if not operation_visible(db, principal, operation):
        raise bundle.error('Original assessment sources are unavailable', 'PM_OVERVIEW_SOURCE_UNAVAILABLE', 404)


def _authorize_existing(ops, principal, op, verb):
    pm_native_operations._authorize_existing(ops, principal, op, verb)
    _require_visible(ops.db, principal, op)


def _prepare(ctx, principal, authority):
    _require_visible(ctx.service.db, principal, ctx.op)
    _values(ctx.service, principal, ctx.target, ctx.params, ctx.preconditions)
    context = ctx.admission_binding['overview_assessment']['context']
    run = pm_runs.create(ctx.service, principal, operation_id=ctx.operation_id, **ctx.target,
        expected_version=ctx.preconditions['expected_version'], adapter=adapters.selection(ctx.params),
        purpose=bundle.PURPOSE, execution_authority=authority, max_chars=ctx.params.get('max_chars', bundle.MAX_CHARS),
        _context_factory=lambda: context)
    return adapters.prepare(ctx, run, fmt.prompt(run))


async def _run(ctx):
    return await adapters.run_analysis(ctx, prepare=_prepare, import_reply=_import)


def _import(journal, run_id, generation):
    from .pm_follow_up_analysis import _checked, _public_proof
    with journal.tx():
        run = pm_runs.get(journal.db, run_id)
        if run['generation'] != generation or run['context'].get('output_destination') != fmt.DESTINATION:
            raise bundle.error('The original assessment writer is required', 'PM_OUTPUT_DESTINATION_REFUSED', 409)
        if run['state'] == 'completed':
            if not journal.db.execute('SELECT 1 FROM pm_overview_assessments WHERE run_id=?', (run_id,)).fetchone():
                raise bundle.error('Original assessment receipt is missing', 'PM_OVERVIEW_ASSESSMENT_UNAVAILABLE', 409)
            return run
        run, observed, proof = adapters.verified_reply(journal, run_id, generation)
        context, raw = run['context'], observed['content']
        document, validation = fmt.validate(raw, context)
        retained = raw.encode('utf-8', errors='replace')[:fmt.MAX_OUTPUT_BYTES].decode('utf-8', errors='ignore')
        value = {'assessment_id': 'pmoa_' + run['operation_id'][3:], 'workspace_id': run['workspace_id'],
            'thread_id': run['thread_id'], 'operation_id': run['operation_id'], 'run_id': run_id,
            'context_digest': run['context_digest'], 'projection_digest': context['projection_digest'],
            'as_of': context['as_of'], 'journal_cursor': context['journal_cursor'], 'output_version': 1,
            'document': document, 'document_digest': pm_runs._digest(document) if document is not None else None,
            'raw_reply': retained, 'raw_reply_digest': hashlib.sha256(retained.encode()).hexdigest(),
            'raw_reply_truncated': retained != raw, 'validation': validation,
            'created_by': run['requester'], 'created_at': time.time(),
            'provenance': {**_public_proof(proof), 'actual_model': observed['actual_model'],
                'native_turn_id': observed['native_turn_id'], 'native_message_id': observed['native_message_id']},
            'conclusions_verified': False, 'accepted_plan_changed': False, 'execution_authorized': False}
        _checked(value)
        journal.db.execute('''INSERT INTO pm_overview_assessments
            (assessment_id,workspace_id,thread_id,operation_id,run_id,projection_digest,document)
            VALUES(?,?,?,?,?,?,?)''', (*[value[key] for key in
                ('assessment_id', 'workspace_id', 'thread_id', 'operation_id', 'run_id', 'projection_digest')], pm_runs._json(value)))
        outcome = {'assessment_id': value['assessment_id'], 'validation_status': validation['status'],
            'assistant_imported': False, 'accepted_plan_changed': False, 'execution_authorized': False,
            'conclusions_verified': False}
        pm_runs.receipt(journal, run_id, generation, 'import', 'confirmed', outcome)
        pm_runs._release(journal.db, run, 'completed', outcome)
        journal.api_event('pm_overview_assessment', value['assessment_id'], ACTION,
            {key: value[key] for key in ('workspace_id', 'thread_id', 'operation_id', 'run_id', 'assessment_id')}, actor=run['requester'])
        return pm_runs.get(journal.db, run_id)


def _view(row):
    value = json.loads(row['document'])
    if (any(value[key] != row[key] for key in ('assessment_id', 'workspace_id', 'thread_id', 'operation_id', 'run_id', 'projection_digest'))
            or hashlib.sha256(value['raw_reply'].encode()).hexdigest() != value['raw_reply_digest']
            or (pm_runs._digest(value['document']) if value['document'] is not None else None) != value['document_digest']):
        raise bundle.error('Original assessment cannot be verified', 'PM_OVERVIEW_ASSESSMENT_UNAVAILABLE', 409)
    return {**value, 'sequence': row['sequence']}


def _current(ops, principal, workspace_id):
    try:
        return bundle.basis(ops, principal, workspace_id)['projection_digest']
    except OperationError:
        return None


def _freshness(value, current):
    return 'unknown' if current is None else ('current' if value['projection_digest'] == current else 'out_of_date')


def get_assessment(ops, principal, workspace_id, assessment_id):
    from .pm_release_baselines import _snapshot
    with _snapshot(ops.db), pm_read_cache.scope(ops.db, principal):
        pm_workspace.require_member(ops.db, principal, workspace_id)
        row = ops.db.execute('SELECT * FROM pm_overview_assessments WHERE workspace_id=? AND assessment_id=?',
            (workspace_id, assessment_id)).fetchone() if isinstance(assessment_id, str) and ID.fullmatch(assessment_id) else None
        if not row:
            raise bundle.error('Assessment not found', 'PM_OVERVIEW_ASSESSMENT_NOT_FOUND', 404)
        _require_visible(ops.db, principal, row)
        value = _view(row)
        run = pm_runs.read(ops, principal, workspace_id, value['run_id'])
        if run['context_digest'] != value['context_digest'] or pm_runs._digest(run['context']) != value['context_digest']:
            raise bundle.error('Original assessment context changed', 'PM_OVERVIEW_ASSESSMENT_UNAVAILABLE', 409)
        return {'version': 1, 'workspace_id': workspace_id, 'assessment': value, 'context': run['context'],
            'freshness': _freshness(value, _current(ops, principal, workspace_id))}


def _cursor(workspace_id, sequence):
    return base64.urlsafe_b64encode(pm_runs._json([1, workspace_id, sequence]).encode()).decode().rstrip('=')


def _before(workspace_id, cursor):
    if cursor is None:
        return 9223372036854775807
    try:
        if not isinstance(cursor, str) or len(cursor) > 256 or not re.fullmatch('[A-Za-z0-9_-]+', cursor):
            raise ValueError
        version, wid, sequence = json.loads(base64.urlsafe_b64decode(cursor + '=' * (-len(cursor) % 4)))
        if type(version) is not int or version != 1 or wid != workspace_id or type(sequence) is not int or not 0 < sequence < 9223372036854775807:
            raise ValueError
        return sequence
    except (ValueError, TypeError, UnicodeError):
        raise bundle.error('Use the original Overview history cursor', 'INVALID_CURSOR') from None


def list_assessments(ops, principal, workspace_id, *, before=None, limit=10):
    from .pm_release_baselines import _snapshot
    if type(limit) is not int or not 1 <= limit <= 50:
        raise bundle.error('Use 1–50 history rows')
    with _snapshot(ops.db), pm_read_cache.scope(ops.db, principal):
        pm_workspace.require_member(ops.db, principal, workspace_id)
        if not principal.allows('observe'):
            raise bundle.error('Overview history requires observe scope', 'FORBIDDEN', 403)
        sequence = _before(workspace_id, before)
        rows = ops.db.execute('''SELECT * FROM pm_overview_assessments WHERE workspace_id=? AND sequence<?
            ORDER BY sequence DESC LIMIT 201''', (workspace_id, sequence)).fetchall()
        current, values, filtered, scanned = _current(ops, principal, workspace_id), [], False, 0
        for row in rows[:200]:
            scanned += 1
            if not operation_visible(ops.db, principal, row):
                filtered = True
            else:
                value = _view(row)
                values.append({**{key: value[key] for key in ('assessment_id', 'thread_id', 'operation_id', 'run_id',
                    'projection_digest', 'context_digest', 'as_of', 'created_at', 'output_version', 'validation')},
                    'freshness': _freshness(value, current)})
            if len(values) == limit:
                break
        more = len(rows) > scanned
        return {'version': 1, 'workspace_id': workspace_id, 'assessments': values, 'source_filtered': filtered,
            'partial': more, 'next_cursor': _cursor(workspace_id, rows[scanned - 1]['sequence']) if more and scanned else None,
            'returned': len(values), 'order': 'newest_first'}
