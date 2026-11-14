"""Durable report saves and source-authorized original Git receipts."""
from __future__ import annotations

import json
import re
import sqlite3
import time
from types import SimpleNamespace

from . import (
    controller_authority,
    pm_access,
    pm_discussions,
    pm_lifecycle_gates,
    pm_plans,
    pm_read_cache,
    pm_report_read_work,
    pm_revision_git,
    pm_revision_paths,
    pm_roadmap_report_files,
    pm_roadmap_report_sources,
    pm_roadmap_reports,
    pm_run_operations,
    pm_runs,
    pm_snapshots,
    pm_source_reads,
    pm_workspace,
    pm_worktree,
)
from .errors import ResourceReadOnly
from .operations import ActionDef, Cancelled, OperationError, StepFailed, Uncertain
from .pm_release_baselines import _snapshot_read

ACTION = 'pm.roadmap.report.materialize'
MATERIALIZATION = re.compile(r'pmrpt_[0-9a-f]{32}')
TABLE = 'pm_roadmap_report_materializations'
MAX_READ_BYTES = 2_000_000
MAX_CURSOR = 2**63 - 1
canonical = pm_roadmap_report_files.canonical
FIELDS = ('workspace_id', 'observation_revision_id', 'source_set_digest', 'view_digest',
          'renderer_version', 'format', 'locale', 'output_digest', 'byte_length')


def capabilities():
    return {'version': 1, 'action': ACTION, 'kind': 'roadmap_report_materialization',
        'reads': ['pm_roadmap_report_materializations_list', 'pm_roadmap_report_materialization_get'],
        'required_scopes': ['observe', 'manage', 'start'], 'immutable': True, 'owned_git_chain': True,
        'max_generated_bytes': 512_000, 'max_response_bytes': MAX_READ_BYTES,
        'default_limit': 20, 'max_limit': 50, 'execution_authorized': False}


def schema(journal):
    with journal.tx():
        journal.db.execute('''CREATE TABLE IF NOT EXISTS pm_roadmap_report_materializations (
            sequence INTEGER PRIMARY KEY AUTOINCREMENT, materialization_id TEXT NOT NULL UNIQUE,
            workspace_id TEXT NOT NULL, observation_revision_id TEXT NOT NULL,
            source_set_digest TEXT NOT NULL, view_digest TEXT NOT NULL, renderer_version INTEGER NOT NULL,
            format TEXT NOT NULL, locale TEXT NOT NULL, output_digest TEXT NOT NULL, byte_length INTEGER NOT NULL,
            git_receipt TEXT NOT NULL, operation_id TEXT NOT NULL UNIQUE, actor TEXT NOT NULL, created_at REAL NOT NULL)''')
        journal.db.execute('''CREATE INDEX IF NOT EXISTS pm_roadmap_report_materializations_workspace_sequence
            ON pm_roadmap_report_materializations(workspace_id, sequence)''')
        for verb in ('UPDATE', 'DELETE'):
            journal.db.execute(f'CREATE TRIGGER IF NOT EXISTS pm_roadmap_report_materializations_{verb.lower()}_immutable '
                f"BEFORE {verb} ON pm_roadmap_report_materializations BEGIN SELECT RAISE(ABORT, 'PM roadmap report materialization is immutable'); END")


def _scopes(principal):
    if any(not principal.allows(scope) for scope in ('observe', 'manage', 'start')):
        raise OperationError('FORBIDDEN', 'Report materialization requires observe, manage and start scopes', 403)


def _carrier(db, wid):
    plan = pm_runs.workspace_worktree(db, wid)
    row = db.execute('SELECT run_id FROM pm_workspace_worktrees WHERE workspace_id=?', (wid,)).fetchone()
    if not plan or not row:
        raise OperationError('PM_WORKTREE_REQUIRED', 'Prepare the original owned PM carrier first', 409)
    run = pm_runs.get(db, row['run_id'])
    if run['workspace_id'] != wid or run['worktree_binding'] != plan:
        raise OperationError('PM_WORKTREE_REQUIRED', 'The original owned PM carrier is unproven', 409)
    return plan, pm_discussions._thread(db, wid, run['thread_id'])


def _selected(ops, principal, target, params, pre):
    _scopes(principal)
    pm_workspace.require_member(ops.db, principal, target['workspace_id'], write=True)
    if (set(target) != {'workspace_id', 'observation_revision_id'}
            or set(params) != {'format', 'locale', 'renderer_version'}
            or not isinstance(params['format'], str) or params['format'] not in pm_roadmap_reports.FORMATS
            or not isinstance(params['locale'], str) or params['locale'] not in ('en', 'zh-TW')
            or type(params['renderer_version']) is not int or not 1 <= params['renderer_version'] <= 999_999_999
            or set(pre) != {'expected_source_set_digest', 'expected_view_digest', 'expected_output_digest'}
            or any(not isinstance(value, str) or not re.fullmatch(r'[0-9a-f]{64}', value) for value in pre.values())):
        raise OperationError('INVALID_PARAMS', 'Select one fixed report, format, locale, renderer and exact digests', 422)
    fixed = pm_roadmap_report_sources.load_fixed(ops, principal, **target)
    report = pm_roadmap_reports.render_fixed(fixed, **params)
    if any(report[key.removeprefix('expected_')] != value for key, value in pre.items()):
        raise OperationError('PM_REPORT_VIEW_CHANGED', 'Read the exact report before materializing it', 409)
    plan, thread = _carrier(ops.db, target['workspace_id'])
    return fixed, report, plan, thread


def _operation_ids(db, fixed):
    # Proven selected IDs only. Authorization remains exclusively load_fixed's
    # closure, including evidence and maintenance records not represented here.
    ids = {fixed[key]['operation_id'] for key in ('baseline', 'forecast', 'observation') if fixed[key]}
    row = db.execute('SELECT operation_id FROM pm_document_snapshots WHERE snapshot_id=?', (fixed['snapshot_id'],)).fetchone()
    ids.add(row[0])
    ids.update(pm_snapshots._plan_source_operations(db, fixed['snapshot']))
    for ref in fixed['source_manifest']:
        if ref['kind'] == 'plan':
            ids.update(pm_plans.source_operation_ids(db, fixed['workspace_id'], ref['id'], include_adoption=False))
        elif ref['kind'] == 'adoption':
            row = db.execute('SELECT operation_id,result FROM pm_plan_adoptions WHERE workspace_id=? AND decision_id=?',
                             (fixed['workspace_id'], ref['id'])).fetchone()
            ids.update((row['operation_id'], json.loads(row['result'])['commit_operation_id']))
    return sorted(ids)


def _admit(ops, principal, target, params, pre):
    fixed, report, plan, _ = _selected(ops, principal, target, params, pre)
    return {'principal': pm_workspace.principal_binding(principal), 'git_binding_id': pm_revision_git.binding_id(plan),
        'pm_roadmap_report_materialization': {**target, **params,
            **{key: report[key] for key in ('source_set_digest', 'view_digest', 'output_digest')},
            'operation_ids': _operation_ids(ops.db, fixed)},
        'pm_lineage': {'workspace_id': target['workspace_id']}}


@pm_read_cache.memoized
def _proof(db, principal, wid, observation):
    fixed = pm_roadmap_report_sources.load_fixed(SimpleNamespace(db=db), principal, wid, observation)
    return fixed['source_set_digest'], fixed['view_digest']


@pm_read_cache.memoized
def _frozen_report(db, identity):
    # Read only the metadata subtree, never project the private bytes. The
    # request cache also avoids re-decoding it for repeated events of one op.
    row = db.execute("SELECT json_extract(response, '$.report') FROM operation_steps "
                     "WHERE operation_id=? AND name='pm.roadmap.report.freeze' AND status='succeeded'", (identity,)).fetchone()
    return json.loads(row[0]) if row and row[0] else None


def source_visible(db, principal, operation):
    with pm_source_reads.scope(db, principal):
        try:
            original = pm_read_cache.operation(db, operation.get('operation_id')) or {}
            if original.get('action') != ACTION:
                return False
            fixed = original['external_refs']['admission_binding']['pm_roadmap_report_materialization']
            if (original['target'] != {key: fixed[key] for key in ('workspace_id', 'observation_revision_id')}
                    or original['params'] != {key: fixed[key] for key in ('format', 'locale', 'renderer_version')}
                    or original['preconditions'] != {'expected_' + key: fixed[key]
                        for key in ('source_set_digest', 'view_digest', 'output_digest')}):
                return False
            frozen = _frozen_report(db, original['operation_id'])
            if frozen is not None and (not isinstance(frozen, dict) or any(frozen.get(key) != fixed[key] for key in (
                    'workspace_id', 'observation_revision_id', 'source_set_digest', 'view_digest',
                    'output_digest', 'format', 'locale', 'renderer_version'))):
                return False
            return _proof(db, principal, fixed['workspace_id'], fixed['observation_revision_id']) == (
                fixed['source_set_digest'], fixed['view_digest'])
        except (OperationError, KeyError, TypeError, ValueError, RecursionError, sqlite3.DatabaseError):
            return False


operation_visible = source_visible


def _authorize_existing(ops, principal, operation, verb):
    _scopes(principal)
    pm_workspace.require_member(ops.db, principal, operation['target']['workspace_id'], write=True)
    if not source_visible(ops.db, principal, operation):
        raise OperationError('NOT_FOUND', 'Original report sources are unavailable', 404)
    if verb != 'replay' and principal.actor != operation['actor']:
        raise OperationError('FORBIDDEN', 'Only the original requester may control this materialization', 403)


def _record(db, wid, identity):
    if not isinstance(identity, str) or not MATERIALIZATION.fullmatch(identity):
        raise OperationError('NOT_FOUND', 'Report materialization not found', 404)
    row = db.execute('SELECT * FROM pm_roadmap_report_materializations WHERE workspace_id=? AND materialization_id=?', (wid, identity)).fetchone()
    if row is None:
        raise OperationError('NOT_FOUND', 'Report materialization not found', 404)
    value = dict(row)
    value['git_receipt'] = json.loads(value['git_receipt'])
    step = db.execute("SELECT request,response,status FROM operation_steps WHERE operation_id=? AND name='pm.revision.commit'",
                      (value['operation_id'],)).fetchone()
    if not step or step['status'] != 'succeeded':
        raise OperationError('PM_GIT_RECEIPT_INVALID', 'The original report receipt is unproven', 409)
    intent = json.loads(step['request'])
    receipt = pm_revision_git._spec_receipt(intent, value['git_receipt'])
    if (intent.get('artifact_kind') != pm_revision_paths.REPORT_KIND
            or receipt != pm_revision_git.step_receipt(json.loads(step['response']))
            or any(receipt.get(key) != value[key] for key in ('workspace_id', 'operation_id',
                'observation_revision_id', 'format', 'locale', 'renderer_version'))
            or receipt['revision_id'] != identity):
        raise OperationError('PM_GIT_RECEIPT_INVALID', 'The original report receipt is unproven', 409)
    return {'kind': 'roadmap_report_materialization', **value}


def _read(ops, principal, wid):
    if not principal.allows('observe'):
        raise OperationError('FORBIDDEN', 'Report receipt reads require observe scope', 403)
    pm_workspace.require_member(ops.db, principal, wid)


def _read_size(value):
    return len(json.dumps(value, ensure_ascii=False, allow_nan=False).encode('utf-8'))


def _bounded(value):
    if _read_size(value) > MAX_READ_BYTES:
        raise OperationError('PM_REPORT_MATERIALIZATION_READ_TOO_LARGE',
            'The complete report receipt response exceeds 2,000,000 UTF-8 bytes', 413)
    return value


@_snapshot_read
def get_materialization(ops, principal, workspace_id, materialization_id):
    _read(ops, principal, workspace_id)
    if not isinstance(materialization_id, str) or not MATERIALIZATION.fullmatch(materialization_id):
        raise OperationError('NOT_FOUND', 'Report materialization not found', 404)
    row = ops.db.execute('SELECT operation_id FROM pm_roadmap_report_materializations WHERE workspace_id=? AND materialization_id=?',
                         (workspace_id, materialization_id)).fetchone()
    if not row or not source_visible(ops.db, principal, dict(row)):
        raise OperationError('NOT_FOUND', 'Report materialization not found', 404)
    return _bounded({'version': 1, 'workspace_id': workspace_id,
        'materialization': _record(ops.db, workspace_id, materialization_id)})


@_snapshot_read
def list_materializations(ops, principal, workspace_id, *, after=0, limit=20):
    _read(ops, principal, workspace_id)
    if type(after) is not int or not 0 <= after <= MAX_CURSOR or type(limit) is not int or not 1 <= limit <= 50:
        raise OperationError('INVALID_PARAMS', 'Select a signed 64-bit cursor and a limit of 1–50', 422)
    with pm_source_reads.scope(ops.db, principal), pm_report_read_work.scope(ops.db) as work:
        rows = ops.db.execute('SELECT * FROM pm_roadmap_report_materializations WHERE workspace_id=? AND sequence>? ORDER BY sequence LIMIT ?',
            (workspace_id, after, limit + 1)).fetchall()
        result = {'version': 1, 'workspace_id': workspace_id, 'materializations': [],
            'next_after': None, 'source_visibility_filtered': False}
        scanned = after
        for row in rows[:limit]:
            if scanned != after and work.stopped:
                result['next_after'] = scanned
                return _bounded(result)
            with work.record(), pm_source_reads.record(ops.db, principal):
                visible = source_visible(ops.db, principal, dict(row))
            if not visible:
                scanned = row['sequence']
                result['source_visibility_filtered'] = True
                continue
            value = _record(ops.db, workspace_id, row['materialization_id'])
            candidate = {**result, 'materializations': [*result['materializations'], value],
                'next_after': MAX_CURSOR}
            if _read_size(candidate) > MAX_READ_BYTES:
                if scanned == after:
                    return _bounded(candidate)
                result['next_after'] = scanned
                return _bounded(result)
            result['materializations'].append(value)
            scanned = row['sequence']
        result['next_after'] = scanned if len(rows) > limit else None
        return _bounded(result)


def _result(record):
    return {'status': 'materialized', 'execution_authorized': False,
        **{key: record[key] for key in (*FIELDS, 'materialization_id', 'git_receipt', 'operation_id')}}


async def _run(ctx):
    try:
        return await _materialize(ctx)
    except (OperationError, StepFailed, Cancelled, ResourceReadOnly) as error:
        run = pm_runs.get_by_operation(ctx.service.db, ctx.operation_id)
        uncertain = ctx.service.db.execute("SELECT 1 FROM operation_steps WHERE operation_id=? AND status IN ('started','uncertain') "
            "AND name NOT IN ('pm.roadmap.report.freeze','pm.roadmap.report.record')", (ctx.operation_id,)).fetchone()
        if isinstance(error, Cancelled) and uncertain:
            raise Uncertain(ACTION, 'Cancellation is waiting for the original report receipt') from None
        if run and run['state'] not in pm_runs.TERMINAL and not uncertain:
            pm_runs.complete_service(ctx.service.journal, run['run_id'], run['generation'],
                {'status': 'not_materialized', 'reason': 'cancelled' if isinstance(error, Cancelled) else 'materialization_stopped'})
        raise


def _preflight(ctx, plan, revision, parent):
    request = pm_revision_git.request(ctx, plan, revision, parent)
    try:
        for phase in ('pm-revision', 'read.pm-revision'):
            pm_worktree.host_script(ctx.operation_id, plan, phase, revision=request)
    except OperationError as error:
        if error.code == 'PM_SNAPSHOT_TOO_LARGE':
            raise pm_roadmap_report_files.too_large() from None
        raise


async def _materialize(ctx):
    principal = pm_workspace.admitted_principal(ctx)
    wid, identity = ctx.target['workspace_id'], 'pmrpt_' + ctx.operation_id[3:]
    saved = ctx.service.db.execute('SELECT materialization_id FROM pm_roadmap_report_materializations WHERE operation_id=?', (ctx.operation_id,)).fetchone()
    if saved:
        return _result(_record(ctx.service.db, wid, identity))
    authority = pm_run_operations.execution_authority(ctx.service.db, principal, wid, ctx.operation_id)

    def freeze():
        fixed, report, plan, thread = _selected(ctx.service, principal, ctx.target, ctx.params, ctx.preconditions)
        if pm_revision_git.binding_id(plan) != ctx.admission_binding['git_binding_id']:
            raise OperationError('PM_REPORT_SOURCE_CHANGED', 'The original carrier changed', 409)
        run = pm_runs.create(ctx.service, principal, operation_id=ctx.operation_id, workspace_id=wid,
            thread_id=thread['thread_id'], expected_version=thread['version'], adapter='connector.git',
            purpose='Materialize roadmap report ' + identity, execution_authority=authority)
        pm_runs.bind_worktree(ctx.service.journal, run['run_id'], run['generation'], plan)
        previous = pm_plans.latest_git_receipt(ctx.service.db, wid)
        parent = previous['commit_sha'] if previous else plan['source_sha']
        revision = pm_roadmap_report_files.files(report, fixed['source_manifest'], ctx.operation_id)
        _preflight(ctx, plan, revision, parent)
        pm_access.inherit_operation_sources(ctx.service.db, ctx.operation_id, _operation_ids(ctx.service.db, fixed))
        return {'version': 1, 'run_id': run['run_id'], 'thread_id': thread['thread_id'], 'thread_version': thread['version'],
            'generation': run['generation'], 'plan': plan, 'expected_parent': parent,
            'report': {key: report[key] for key in pm_revision_paths.REPORT_METADATA},
            'source_manifest': fixed['source_manifest'], 'revision': revision}

    fixed = ctx.effect('pm.roadmap.report.freeze', freeze)
    ctx.set_refs(workspace_id=wid, run_id=fixed['run_id'], materialization_id=identity)
    # Each actual host call rechecks the transport bound. A succeeded Git step
    # can finalize without encoding a new command after a helper-version change.

    async def check():
        ctx.check_cancel()
        pm_run_operations.require_execution(ctx.service.journal, authority, wid)
        _authorize_existing(ctx.service, principal, ctx.op, 'replay')
        plan, _ = _carrier(ctx.service.db, wid)
        if plan != fixed['plan']:
            raise OperationError('PM_REPORT_SOURCE_CHANGED', 'The original carrier changed', 409)
        pm_lifecycle_gates.require_local(ctx.service.db, ctx.operation_id)

    with controller_authority.use(authority, ctx.service.journal):
        started = ctx.service.db.execute("SELECT 1 FROM operation_steps WHERE operation_id=? AND name='pm.revision.commit'", (ctx.operation_id,)).fetchone()
        if not started:
            await pm_worktree.ensure(ctx, fixed['plan'], check=check)
        receipt = await pm_revision_git.commit_revision(ctx, fixed['plan'], fixed['revision'], expected_parent=fixed['expected_parent'], check=check)

    def record():
        ctx.service.db.execute('''INSERT INTO pm_roadmap_report_materializations
            (materialization_id,workspace_id,observation_revision_id,source_set_digest,view_digest,renderer_version,
             format,locale,output_digest,byte_length,git_receipt,operation_id,actor,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
            (identity, *(fixed['report'][key] for key in FIELDS), canonical(receipt), ctx.operation_id, ctx.actor, time.time()))
        result = _result(_record(ctx.service.db, wid, identity))
        pm_runs.complete_service(ctx.service.journal, fixed['run_id'], fixed['generation'], result)
        ctx.service.journal.api_event('pm_workspace', wid, 'pm.roadmap.report.materialized', result, actor=ctx.actor)
        return result

    return ctx.effect('pm.roadmap.report.record', record, receipt_only=True)


def install(ops):
    schema(ops.journal)
    if ACTION not in ops.actions:
        ops.register(ActionDef(ACTION, 'manage', 'Save one fixed report in the owned PM Git carrier', _run, _admit,
            ('workspace_id', 'observation_revision_id'), authorize_existing=_authorize_existing))
