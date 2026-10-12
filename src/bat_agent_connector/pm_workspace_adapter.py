"""Durable choice for existing bounded PM runtimes, never another executor."""
from __future__ import annotations

import json
import time

from . import pm_access, pm_context_bundles, pm_plans, pm_runs, pm_workspace
from .operations import ActionDef, OperationError, OperationService

ACTION = 'pm.workspace.adapter.select'
ADAPTERS = ('codex-native-linux', 'bat-pm-linux-v1')
ANALYSIS = {'pm.run.analyze', 'pm.run.bat.analyze', 'pm.conversation.propose', 'pm.follow_up.analyze'}
SCOPES = ('observe', 'manage', 'start')


def bad(message, code='PM_ADAPTER_CHANGED', status=409):
    return OperationError(code, message, status)


def installed(db):
    return db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='pm_adapter_choices'").fetchone() is not None


def install(ops):
    with ops.journal.tx():
        ops.db.execute('''CREATE TABLE IF NOT EXISTS pm_adapter_choices (
            workspace_id TEXT NOT NULL, version INTEGER NOT NULL, operation_id TEXT NOT NULL UNIQUE,
            document TEXT NOT NULL, document_digest TEXT NOT NULL, PRIMARY KEY(workspace_id,version))''')
        for verb in ('UPDATE', 'DELETE'):
            ops.db.execute(f'''CREATE TRIGGER IF NOT EXISTS pm_adapter_choices_{verb.lower()}_immutable
                BEFORE {verb} ON pm_adapter_choices BEGIN SELECT RAISE(ABORT,'PM adapter choice is immutable'); END''')
    if ACTION not in ops.actions:
        ops.register(ActionDef(ACTION, 'manage', 'Select this workspace bounded PM analysis adapter', _run,
            _admit, ('workspace_id',), authorize_existing=_authorize_existing))


def _row(db, wid, version=None):
    if not installed(db):
        return None
    row = db.execute('SELECT * FROM pm_adapter_choices WHERE workspace_id=? AND (? IS NULL OR version=?) '
        'ORDER BY version DESC LIMIT 1', (wid, version, version)).fetchone()
    if row is None:
        return None
    result = dict(row)
    result['document'] = json.loads(row['document'])
    if pm_runs._digest(result['document']) != row['document_digest']:
        raise bad('Original PM choice cannot be verified', 'PM_ADAPTER_CORRUPT')
    return result


def _ref(row):
    return {'version': row['version'], 'operation_id': row['operation_id'], 'document_digest': row['document_digest']} if row else None


def _writer(db, wid):
    row = db.execute('SELECT generation,run_id FROM pm_writer_slots WHERE workspace_id=?', (wid,)).fetchone()
    return dict(row) if row else {'generation': 0, 'run_id': None}


def _authority(db, principal, wid):
    if any(not principal.allows(scope) for scope in SCOPES):
        raise bad('PM adapter selection requires observe, manage and start', 'FORBIDDEN', 403)
    return pm_workspace.require_member(db, principal, wid, decide=True)


def _continuity(db, principal, wid):
    current = pm_plans.head(db, wid)
    carrier = pm_runs.workspace_worktree(db, wid)
    selected = pm_context_bundles._selected(db, principal, wid)[0] if current['accepted_revision_id'] else None
    sources = set(selected['source_operation_ids'] if selected else [])
    if carrier:
        sources.add(carrier['creation_operation_id'])
    pm_access.require_operation_sources(db, principal, sources)
    return {'plan_version': current['version'], 'accepted_revision_id': current['accepted_revision_id'],
        'accepted_context_ref': selected['ref'] if selected else None,
        'carrier_digest': pm_runs._digest(carrier) if carrier else None,
        'carrier_creation_operation_id': carrier['creation_operation_id'] if carrier else None,
        'source_operation_ids': sorted(sources)}


def _access(db, principal, wid, document):
    pm_workspace.require_member(db, principal, wid)
    if not principal.allows('observe'):
        raise bad('PM choice reads require observe', 'FORBIDDEN', 403)
    anchor = document['continuity']
    pm_access.require_operation_sources(db, principal, anchor['source_operation_ids'])
    if anchor['accepted_context_ref']:
        selected, _, _ = pm_context_bundles._selected(db, principal, wid, anchor['accepted_context_ref'])
        if (selected['carrier_digest'] != anchor['carrier_digest']
                or selected['carrier_creation_operation_id'] != anchor['carrier_creation_operation_id']):
            raise bad('Original PM document carrier unavailable', 'NOT_FOUND', 404)
    carrier = pm_runs.workspace_worktree(db, wid)
    if anchor['carrier_digest'] is not None and (pm_runs._digest(carrier) if carrier else None) != anchor['carrier_digest']:
        raise bad('Original PM carrier unavailable', 'NOT_FOUND', 404)


def _check(ops, principal, target, params, pre):
    if set(target) != {'workspace_id'} or set(params) != {'adapter', 'rationale'}:
        raise bad('Select one workspace, adapter and rationale', 'INVALID_PARAMS', 422)
    wid = target['workspace_id']
    _authority(ops.db, principal, wid)
    if params['adapter'] not in ADAPTERS:
        raise bad('Only advertised bounded PM analysis adapters can be selected', 'INVALID_PARAMS', 422)
    from .pm_discussions import _text
    _text(params['rationale'], 'rationale', 4000)
    expected_action = 'pm.run.analyze' if params['adapter'] == ADAPTERS[0] else 'pm.run.bat.analyze'
    if expected_action not in ops.actions:
        raise bad('The selected analysis action is unavailable', 'PM_ADAPTER_UNAVAILABLE')
    current, writer = _row(ops.db, wid), _writer(ops.db, wid)
    if current:
        _access(ops.db, principal, wid, current['document'])
    if writer['run_id'] is not None:
        raise bad('The original PM writer still requires completion or reconciliation', 'PM_WRITER_BUSY')
    continuity = _continuity(ops.db, principal, wid)
    expected = {'expected_selection_version': current['version'] if current else 0,
        'expected_writer_generation': writer['generation'], 'expected_plan_version': continuity['plan_version'],
        'expected_accepted_revision_id': continuity['accepted_revision_id'],
        'expected_continuity_digest': pm_runs._digest(continuity)}
    if pre != expected or any(type(pre.get(key)) is not int for key in
            ('expected_selection_version', 'expected_writer_generation', 'expected_plan_version')):
        raise bad('Read the current workspace choice, writer and document context before selecting')
    return {'workspace_id': wid, 'adapter': params['adapter'], 'rationale': params['rationale'],
        'version': expected['expected_selection_version'] + 1, 'previous_choice': _ref(current),
        'writer_generation': writer['generation'], 'continuity': continuity}


def _admit(ops, principal, target, params, pre):
    value = _check(ops, principal, target, params, pre)
    return {'principal': pm_workspace.principal_binding(principal), 'pm_lineage': {'workspace_id': target['workspace_id']},
        'pm_adapter_selection': value, 'pm_adapter_selection_digest': pm_runs._digest(value)}


def _authorize_existing(ops, principal, op, verb):
    if verb != 'replay':
        _authority(ops.db, principal, op['target']['workspace_id'])
    if not operation_visible(ops.db, principal, op):
        raise bad('Original PM choice unavailable', 'NOT_FOUND', 404)


async def _run(ctx):
    def select():
        with ctx.service.journal.tx():
            saved = ctx.service.db.execute('SELECT document FROM pm_adapter_choices WHERE operation_id=?',
                (ctx.operation_id,)).fetchone()
            if saved:
                return json.loads(saved[0])
            ctx.check_cancel()
            principal = pm_workspace.admitted_principal(ctx)
            value = _check(ctx.service, principal, ctx.target, ctx.params, ctx.preconditions)
            if (value != ctx.admission_binding['pm_adapter_selection']
                    or pm_runs._digest(value) != ctx.admission_binding['pm_adapter_selection_digest']):
                raise bad('The originally selected workspace context changed')
            value.update(operation_id=ctx.operation_id, selected_by=ctx.actor, recorded_at=time.time(),
                execution_authorized=False, successor_started=False, runtime_preflight='not_performed')
            ctx.service.db.execute('INSERT INTO pm_adapter_choices VALUES(?,?,?,?,?)',
                (ctx.target['workspace_id'], value['version'], ctx.operation_id, pm_runs._json(value), pm_runs._digest(value)))
            pm_access.inherit_operation_sources(ctx.service.db, ctx.operation_id, value['continuity']['source_operation_ids'])
            ctx.service.journal.api_event('pm_workspace_adapter', ctx.target['workspace_id'], ACTION,
                {'operation_id': ctx.operation_id, 'version': value['version']}, actor=ctx.actor)
            return value
    return ctx.effect('pm.adapter.select', select)


def operation_visible(db, principal, original):
    try:
        row = db.execute('SELECT * FROM operations WHERE operation_id=?', (original.get('operation_id'),)).fetchone()
        op = OperationService._decode(row) if row else None
        if not op or op['action'] != ACTION:
            return False
        binding = op['external_refs']['admission_binding']
        value = binding['pm_adapter_selection']
        if (pm_runs._digest(value) != binding['pm_adapter_selection_digest']
                or value['workspace_id'] != op['target']['workspace_id']):
            return False
        _access(db, principal, value['workspace_id'], value)
        return True
    except (OperationError, KeyError, TypeError, ValueError):
        return False


def admission(ops, principal, wid, adapter):
    row = _row(ops.db, wid)
    if row:
        _access(ops.db, principal, wid, row['document'])
        if row['document']['adapter'] != adapter:
            raise bad('Use the selected workspace PM adapter or explicitly change the workspace choice', 'PM_ADAPTER_MISMATCH')
    return {'pm_adapter_choice': _ref(row), **({'pm_source_roots': [row['operation_id']],
        'pm_lineage': {'workspace_id': wid}} if row else {})}


def require_new_run(ops, principal, operation_id, wid, adapter):
    row = ops.db.execute('SELECT * FROM operations WHERE operation_id=?', (operation_id,)).fetchone()
    if not row or row['action'] not in ANALYSIS:
        return
    op = OperationService._decode(row)
    original = op['external_refs']['admission_binding'].get('pm_adapter_choice')
    current = admission(ops, principal, wid, adapter)['pm_adapter_choice']
    if original != current:
        raise bad('The workspace PM choice changed before this original turn started')


def read(ops, principal, wid):
    pm_workspace.require_member(ops.db, principal, wid)
    row, writer = _row(ops.db, wid), _writer(ops.db, wid)
    try:
        if row:
            _access(ops.db, principal, wid, row['document'])
        continuity = _continuity(ops.db, principal, wid)
    except OperationError as exc:
        if exc.status not in (403, 404, 409):
            raise
        return {'version': 1, 'action': ACTION, 'choice': None, 'available': False,
            'reason': 'original_sources_unavailable', 'preconditions': None}
    authorized = all(principal.allows(scope) for scope in SCOPES)
    authorized = authorized and (principal.admin or pm_workspace.require_member(ops.db, principal, wid)['membership']['role'] == 'owner')
    return {'version': 1, 'action': ACTION, 'adapters': list(ADAPTERS), 'choice': row['document'] if row else None,
        'choice_ref': _ref(row), 'available': authorized and writer['run_id'] is None,
        'reason': 'owner_and_scopes_required' if not authorized else 'original_writer_unresolved' if writer['run_id'] else None,
        'preconditions': {'expected_selection_version': row['version'] if row else 0,
            'expected_writer_generation': writer['generation'], 'expected_plan_version': continuity['plan_version'],
            'expected_accepted_revision_id': continuity['accepted_revision_id'],
            'expected_continuity_digest': pm_runs._digest(continuity)},
        'writer': writer, 'runtime_preflight': 'not_performed', 'execution_authorized': False}
